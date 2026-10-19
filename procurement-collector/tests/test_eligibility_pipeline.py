import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from collector.db import connect
from collector.pipeline import run
from collector.report import render


LOCAL = ZoneInfo('Asia/Shanghai')

SOURCE = {
    'id': 'eligibility', 'name': 'eligibility fixture', 'category': 'procurement',
    'entry_url': 'https://example.invalid/list', 'adapter': 'html_list',
    'enabled': True, 'access_status': 'public', 'item_selector': 'li',
}
VALID_TITLE = '破碎机备件采购公告'


class Response:
    def __init__(self, url, text='<article>buyer: fixture</article>'):
        self.url = url
        self.content = text.encode('utf-8')
        self.text = text
        self.headers = {'Content-Type': 'text/html; charset=utf-8'}
        self.encoding = 'utf-8'
        self.apparent_encoding = 'utf-8'


class Fetcher:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        return self.responses[url]


class EligibilityPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = connect(self.root / 'collector.sqlite3')
        self.ai_env = patch.dict(os.environ, {'OPENAI_API_KEY': '', 'OPENAI_MODEL': ''})
        self.ai_env.start()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
        self.ai_env.stop()

    @staticmethod
    def title(suffix='fixture'):
        return f'{VALID_TITLE} {suffix}'

    @staticmethod
    def today():
        return datetime.now(LOCAL).date()

    def run_entries(self, entries, responses, *, at=None):
        fetcher = Fetcher(responses)
        with patch('collector.pipeline.listing', return_value=entries):
            result = run(self.db, {'sources': [SOURCE], 'max_age_days': 30},
                         self.root, fetcher=fetcher, at=at)
        return result, fetcher

    def test_old_listing_is_filtered_before_detail_fetch_and_expensive_work(self):
        url = 'https://example.invalid/old'
        old = (self.today() - timedelta(days=31)).isoformat()
        entry = {'url': url, 'title': self.title(), 'published_at': old}
        with patch('collector.pipeline.attachments.collect', side_effect=AssertionError), \
                patch('collector.pipeline.archive', side_effect=AssertionError):
            (run_id, stats), fetcher = self.run_entries([entry], {})
        self.assertEqual(fetcher.calls, [])
        self.assertEqual(stats['filtered'], 1)
        self.assertEqual(stats['item_failed'], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM records').fetchone()[0], 0)
        row = self.db.execute('SELECT run_id,source_id,title,url,published_at,reason FROM filtered_entries').fetchone()
        self.assertEqual(row['run_id'], run_id)
        self.assertEqual(row['reason'], 'published_too_old')

    def test_missing_listing_date_uses_detail_publication_metadata(self):
        url = 'https://example.invalid/detail-date'
        today = self.today().isoformat()
        entry = {'url': url, 'title': self.title(), 'published_at': None}
        response = Response(url, f'<article>发布日期：{today}</article>')
        (run_id, stats), fetcher = self.run_entries([entry], {url: response})
        self.assertEqual(fetcher.calls, [url])
        self.assertEqual(stats['new'], 1)
        self.assertEqual(stats['filtered'], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM records').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT published_at FROM records').fetchone()[0], today)

    def test_listing_timestamp_keeps_timezone_at_window_boundary(self):
        url = 'https://example.invalid/boundary'
        fixed_now = datetime(2026, 10, 16, 12, 0, tzinfo=LOCAL)
        entry = {'url': url, 'title': self.title(),
                 'published_at': '2026-09-15T20:00:00Z'}
        (run_id, stats), fetcher = self.run_entries(
            [entry], {url: Response(url)}, at=fixed_now)
        self.assertEqual(fetcher.calls, [url])
        self.assertEqual(stats['new'], 1)
        self.assertEqual(self.db.execute('SELECT run_id FROM events').fetchone()[0], run_id)

    def test_non_equipment_is_filtered_without_attachments_ai_or_business_record(self):
        url = 'https://example.invalid/service'
        today = self.today().isoformat()
        entry = {'url': url, 'title': 'generic service notice', 'published_at': today}
        response = Response(url)
        with patch('collector.pipeline.attachments.collect', side_effect=AssertionError), \
                patch('collector.pipeline.ai_extract.configured', side_effect=AssertionError), \
                patch('collector.pipeline.archive', side_effect=AssertionError):
            (_, stats), _ = self.run_entries([entry], {url: response})
        self.assertEqual(stats['filtered'], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM records').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT reason FROM filtered_entries').fetchone()[0], 'non_equipment')

    def test_source_keyword_filters_are_audited(self):
        today = self.today().isoformat()
        source = dict(SOURCE, include_keywords=['keep'], include_detail_keywords=['required'])
        title_miss = {'url': 'https://example.invalid/title-miss', 'title': 'other',
                      'published_at': today}
        detail_miss = {'url': 'https://example.invalid/detail-miss', 'title': 'keep ' + self.title(),
                       'published_at': today}
        fetcher = Fetcher({detail_miss['url']: Response(detail_miss['url'])})
        with patch('collector.pipeline.listing', return_value=[title_miss, detail_miss]):
            _, stats = run(self.db, {'sources': [source]}, self.root, fetcher=fetcher)
        self.assertEqual(stats['filtered'], 2)
        self.assertEqual(fetcher.calls, [detail_miss['url']])
        reasons = [row[0] for row in self.db.execute(
            'SELECT reason FROM filtered_entries ORDER BY id').fetchall()]
        self.assertEqual(reasons, ['title_keyword_miss', 'detail_keyword_miss'])

    def test_all_filtered_source_is_success_and_report_contains_audit(self):
        today = self.today().isoformat()
        old = (self.today() - timedelta(days=31)).isoformat()
        entries = [
            {'url': 'https://example.invalid/old', 'title': self.title('old'), 'published_at': old},
            {'url': 'https://example.invalid/generic', 'title': 'generic notice', 'published_at': today},
        ]
        generic = Response(entries[1]['url'])
        run_id, stats = self.run_entries(entries, {entries[1]['url']: generic})[0]
        self.assertEqual(stats['filtered'], 2)
        self.assertEqual(stats['new'], 0)
        source_run = self.db.execute('SELECT status,found_count,fetched_count FROM source_runs WHERE run_id=?',
                                      (run_id,)).fetchone()
        self.assertEqual(tuple(source_run), ('success', 2, 0))
        report = render(self.db, self.today())
        self.assertIn('## ⑤ 过滤记录', report)
        self.assertIn('过滤数量：2', report)
        self.assertIn('发布日期超出保留时间窗口', report)
        self.assertIn('非设备或备件货物采购', report)
        self.assertNotIn('published_too_old', report)
        self.assertNotIn('non_equipment', report)
        self.assertIn(entries[0]['url'], report)

    def test_eligible_notice_publishes_and_preserves_event_contract(self):
        url = 'https://example.invalid/valid'
        entry = {'url': url, 'title': self.title(), 'published_at': self.today().isoformat()}
        events = []
        fetcher = Fetcher({url: Response(url)})
        with patch('collector.pipeline.listing', return_value=[entry]):
            run_id, stats = run(self.db, {'sources': [SOURCE]}, self.root,
                                fetcher=fetcher, on_record=lambda: events.append('published'))
        self.assertEqual(stats['new'], 1)
        self.assertEqual(events, ['published'])
        event = self.db.execute('SELECT event_type,run_id FROM events').fetchone()
        self.assertEqual((event['event_type'], event['run_id']), ('new', run_id))

    def test_max_age_days_validation_happens_before_run_row(self):
        with self.assertRaises(ValueError):
            run(self.db, {'sources': [SOURCE], 'max_age_days': True}, self.root,
                fetcher=Fetcher({}))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
