import json
import tempfile
import unittest
import io
from datetime import date
from pathlib import Path

from collector.db import connect, sync_sources
from collector.ai_extract import validate
from collector.extract import canonical_url, date_text, extract
from collector.fetch import feed_entries, html_entries, search_api_entries
from collector.fetch import detail
from pypdf import PdfWriter
from collector.pipeline import ingest, run
from collector.report import render, export_jsonl, trial_status


SOURCE = {'id': 'test', 'name': '测试公告源', 'category': 'procurement',
          'entry_url': 'https://example.org/list', 'adapter': 'html_list',
          'enabled': True, 'access_status': 'public', 'item_selector': 'li'}
NEWS = {'id': 'news', 'name': '测试新闻源', 'category': 'news',
        'entry_url': 'https://news.example.org/rss', 'adapter': 'rss',
        'enabled': True, 'access_status': 'public'}


class Response:
    def __init__(self, url, html):
        self.url = url
        self.content = html.encode('utf-8')
        self.text = html
        self.headers = {'Content-Type': 'text/html; charset=utf-8'}
        self.encoding = 'utf-8'
        self.apparent_encoding = 'utf-8'


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = connect(self.root / 'db.sqlite3')
        sync_sources(self.db, [SOURCE, NEWS])
        self.run_id = self.db.execute("INSERT INTO runs(started_at,status) VALUES (datetime('now'),'running')").lastrowid
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_listing_parsers_and_dates(self):
        feed = b'<rss><channel><item><title>Example</title><link>https://example.org/a</link><pubDate>Sat, 03 Oct 2026 00:00:00 GMT</pubDate></item></channel></rss>'
        self.assertEqual(feed_entries(feed, 'https://example.org/rss')[0]['title'], 'Example')
        self.assertEqual(date_text('Sat, 03 Oct 2026 00:00:00 GMT'), '2026-10-03')
        html = '<ul><li><a href="/a">公告 A</a></li></ul>'
        self.assertEqual(html_entries(html, 'https://example.org/list', SOURCE)[0]['url'], 'https://example.org/a')
        self.assertEqual(canonical_url('https://example.org/a?utm_source=x&id=1'),
                         'https://example.org/a?id=1')
        fields = extract('矿山输送机采购',
                         '项目编号：GCG2026-072）\n投标文件应于\n2026\n年\n10\n月\n21\n日\n9\n时\n00\n分\n之前递交',
                         'procurement')
        self.assertEqual(fields['notice_number'], 'GCG2026-072')
        self.assertEqual(fields['deadline_at'], '2026-10-21 09:00')
        self.assertEqual(extract('设备采购GHY-26680项目公告', '项目编号为：GHY-',
                                 'procurement')['notice_number'], 'GHY-26680')

    def test_public_search_api_list_and_news_title(self):
        class ApiResponse:
            def json(self):
                return {'content': json.dumps({'result': {'records': [
                    {'title': '破碎机公告', 'linkurl': '/item.html', 'webdate': '2026-10-03',
                     'projectnumber': 'BG-1234', 'endtime': '2026-10-10 09:00'}]}})}

        rows = search_api_entries(ApiResponse(), {'entry_url': 'https://example.org/list'})
        self.assertEqual(rows[0]['url'], 'https://example.org/item.html')
        self.assertEqual(rows[0]['notice_number'], 'BG-1234')
        self.assertEqual(rows[0]['deadline_at'], '2026-10-10 09:00')
        html = '<li><a href="/n"><h3>矿山新闻</h3><h4>2026-10-03</h4><p>摘要</p></a></li>'
        source = dict(SOURCE, list_title_selector='h3', date_selector='h4')
        self.assertEqual(html_entries(html, 'https://example.org', source)[0]['title'], '矿山新闻')

    def test_trial_restart_after_missed_days(self):
        self.db.execute("UPDATE runs SET started_at='2026-10-03T00:00:00+08:00' WHERE id=?",
                        (self.run_id,))
        for day in ('2026-10-03', '2026-10-08'):
            for source_id in ('test', 'news'):
                self.db.execute('''INSERT INTO source_runs(run_id,source_id,started_at,status)
                                   VALUES (?,?,?,'success')''',
                                (self.run_id, source_id, f'{day}T08:00:00+08:00'))
        self.db.commit()
        status = trial_status(self.db, self.root, today=date(2026, 10, 8)).read_text(encoding='utf-8')
        self.assertIn('连续成功 1/14 天', status)
        self.assertIn('2026-10-21', status)
        self.assertIn('2026-10-04', status)

    def test_versions_dedup_and_evidence(self):
        url = 'https://example.org/a'
        entry = {'url': url, 'title': '某矿旋回破碎机备件询价公告', 'published_at': '2026-10-03'}
        original = '<article>采购人：某矿业公司\n项目编号：ABC-1234\n投标截止时间：2026年10月05日 15:00\n采购旋回破碎机衬板。</article>'
        self.assertEqual(ingest(self.db, self.root, self.run_id, SOURCE, entry, Response(url, original)), 'new')
        self.assertEqual(ingest(self.db, self.root, self.run_id, SOURCE, entry, Response(url, original)), 'unchanged')
        changed = original.replace('衬板', '主轴')
        self.assertEqual(ingest(self.db, self.root, self.run_id, SOURCE, entry, Response(url, changed)), 'changed')
        changed_again = original.replace('衬板', '轴承')
        self.assertEqual(ingest(self.db, self.root, self.run_id, SOURCE, entry,
                                Response(url, changed_again)), 'changed')
        row = self.db.execute('SELECT * FROM records').fetchone()
        self.assertEqual(row['current_version'], 3)
        self.assertEqual(row['deadline_at'], '2026-10-05 15:00')
        self.assertEqual(self.db.execute('SELECT count(*) FROM versions').fetchone()[0], 3)
        self.assertTrue((self.root / 'archive' / str(row['id']) / 'v1.html').exists())
        self.assertTrue((self.root / 'archive' / str(row['id']) / 'v2.html').exists())
        self.assertIn('版本 2', render(self.db))
        self.assertIn('版本 3', render(self.db))

    def test_page_markup_change_keeps_version_without_content_alert(self):
        url = 'https://example.org/markup'
        entry = {'url': url, 'title': '矿山设备公告', 'published_at': '2026-10-03'}
        original = '<article>采购人：某矿业公司。设备清单保持不变。</article>'
        ingest(self.db, self.root, self.run_id, SOURCE, entry, Response(url, original))
        changed_markup = '<article>采购人：某矿业公司。设备清单保持不变。</article><!-- layout -->'
        self.assertEqual(ingest(self.db, self.root, self.run_id, SOURCE, entry,
                                Response(url, changed_markup)), 'markup_changed')
        self.assertEqual(self.db.execute('SELECT count(*) FROM versions').fetchone()[0], 2)

    def test_news_inference_remains_unverified(self):
        url = 'https://news.example.org/a'
        entry = {'url': url, 'title': '某矿山发布扩产消息', 'published_at': '2026-10-03'}
        html = '<article>某矿山今天公布扩产计划，新增矿石处理能力。</article>'
        ingest(self.db, self.root, self.run_id, NEWS, entry, Response(url, html))
        row = self.db.execute("SELECT * FROM records WHERE kind='news'").fetchone()
        inference = json.loads(row['inferences_json'])[0]
        self.assertEqual(inference['status'], '待核实')
        self.assertIn('扩产', inference['evidence'])
        self.assertIn('待核实', render(self.db))
        out = self.root / 'export.jsonl'
        self.assertEqual(export_jsonl(self.db, out), 1)
        self.assertEqual(json.loads(out.read_text(encoding='utf-8'))['inferences'][0]['status'], '待核实')

    def test_source_failure_is_visible(self):
        class BrokenFetcher:
            def get(self, url):
                raise TimeoutError('simulated outage')

        run_id, stats = run(self.db, {'sources': [SOURCE]}, self.root, fetcher=BrokenFetcher())
        self.assertEqual(stats['source_failed'], 1)
        self.assertEqual(self.db.execute('SELECT status FROM source_runs WHERE run_id=?',
                                         (run_id,)).fetchone()[0], 'failed')
        self.assertIn('simulated outage', render(self.db))

    def test_cross_source_same_number_is_marked_duplicate(self):
        other = dict(SOURCE, id='other', name='另一平台')
        sync_sources(self.db, [other])
        entry = {'url': 'https://example.org/one', 'title': '某矿破碎机询价公告', 'published_at': '2026-10-03'}
        body = '<article>采购人：某矿业公司\n项目编号：ABC-1234\n采购破碎机备件。</article>'
        ingest(self.db, self.root, self.run_id, SOURCE, entry, Response(entry['url'], body))
        entry2 = dict(entry, url='https://example.org/two')
        ingest(self.db, self.root, self.run_id, other, entry2, Response(entry2['url'], body))
        rows = self.db.execute('SELECT * FROM records ORDER BY id').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['duplicate_of'], rows[0]['id'])

    def test_correction_with_new_url_links_to_original_project(self):
        first = {'url': 'https://example.org/first', 'title': '某矿山破碎机采购公告',
                 'published_at': '2026-10-03'}
        changed = {'url': 'https://example.org/correction', 'title': '某矿山破碎机采购更正公告',
                   'published_at': '2026-10-04'}
        body = '<article>采购人：某矿业公司\n项目编号：ABC-1234\n采购破碎机备件。</article>'
        ingest(self.db, self.root, self.run_id, SOURCE, first, Response(first['url'], body))
        ingest(self.db, self.root, self.run_id, SOURCE, changed, Response(changed['url'], body))
        rows = self.db.execute('SELECT id,kind,related_record_id FROM records ORDER BY id').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['kind'], 'correction')
        self.assertEqual(rows[1]['related_record_id'], rows[0]['id'])

    def test_cross_source_without_number_is_possible_duplicate(self):
        other = dict(SOURCE, id='other', name='另一平台')
        sync_sources(self.db, [other])
        first = {'url': 'https://example.org/one', 'title': '某矿山破碎机备件公开询价',
                 'published_at': '2026-10-03'}
        second = {'url': 'https://example.org/two', 'title': '某矿山破碎机备件公开询价公告',
                  'published_at': '2026-10-03'}
        body = '<article>采购人：某矿业公司\n某矿山计划采购破碎机备件，报价文件应在规定时间上传。</article>'
        ingest(self.db, self.root, self.run_id, SOURCE, first, Response(first['url'], body))
        ingest(self.db, self.root, self.run_id, other, second, Response(second['url'], body))
        rows = self.db.execute('SELECT id,possible_duplicate_of,duplicate_of FROM records ORDER BY id').fetchall()
        self.assertEqual(rows[1]['possible_duplicate_of'], rows[0]['id'])
        self.assertIsNone(rows[1]['duplicate_of'])

    def test_ai_evidence_validation(self):
        body = '某矿山宣布扩产计划。新建选煤厂。'
        payload = {'equipment': [{'text': '破碎机', 'evidence': '新建选煤厂'}],
                                  'facts': [{'text': '已采购破碎机', 'evidence': '已采购破碎机'}],
                                  'inferences': [{'text': '可能需要备件', 'evidence': '宣布扩产计划'}]}
        result = validate(payload, body, 'news')
        self.assertEqual(result['equipment'], [])
        self.assertEqual(result['facts'], [])
        self.assertEqual(result['inferences'][0]['status'], '待核实')

    def test_scanned_pdf_is_archived_as_needing_ocr(self):
        writer = PdfWriter()
        writer.add_blank_page(width=400, height=400)
        stream = io.BytesIO()
        writer.write(stream)

        class PdfResponse:
            content = stream.getvalue()
            headers = {'Content-Type': 'application/pdf'}

        text, status, error, mime = detail(PdfResponse(), SOURCE)
        self.assertEqual(status, 'needs_ocr')
        self.assertEqual(mime, 'application/pdf')


if __name__ == '__main__':
    unittest.main()
