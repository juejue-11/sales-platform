import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from collector.__main__ import main
from collector import export as export_module
from collector.db import connect, sync_sources
from collector.export import export_jsonl
from collector.pipeline import ingest, run


SOURCE = {
    'id': 'stream', 'name': 'stream fixture', 'category': 'procurement',
    'entry_url': 'https://example.invalid/list', 'adapter': 'html_list',
    'enabled': True, 'access_status': 'public', 'item_selector': 'li',
}
TODAY = datetime.now(timezone.utc).date().isoformat()
VALID_TITLE = '破碎机备件采购公告 fixture notice'


class Response:
    def __init__(self, url, text='<article>buyer: fixture</article>'):
        self.url = url
        self.content = text.encode('utf-8')
        self.text = text
        self.headers = {'Content-Type': 'text/html; charset=utf-8'}
        self.encoding = 'utf-8'
        self.apparent_encoding = 'utf-8'


def snapshot(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


class StreamPublicationTests(unittest.TestCase):
    def test_first_record_is_visible_before_second_fetch_finishes(self):
        entries = [
            {'url': 'https://example.invalid/one', 'title': VALID_TITLE, 'published_at': TODAY},
            {'url': 'https://example.invalid/two', 'title': VALID_TITLE, 'published_at': TODAY},
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database_path = root / 'collector.sqlite3'
            target = root / 'export' / 'records.jsonl'
            second_started = threading.Event()
            release_second = threading.Event()
            first_published = threading.Event()
            result = []
            errors = []

            class Fetcher:
                def get(self, url):
                    if url == entries[1]['url']:
                        second_started.set()
                        release_second.wait(5)
                        raise TimeoutError('second item failed')
                    return Response(url)

            def worker():
                db = connect(database_path)
                try:
                    sync_sources(db, [SOURCE])

                    def publish():
                        export_jsonl(db, target)
                        first_published.set()

                    with patch('collector.pipeline.listing', return_value=entries):
                        result.append(run(db, {'sources': [SOURCE]}, root,
                                         fetcher=Fetcher(), on_record=publish))
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    db.close()

            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(first_published.wait(5))
            self.assertTrue(second_started.wait(5))

            # This is an independent reader while the collector is still in
            # the second fetch, so it exercises the published-file contract.
            rows = snapshot(target)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['canonical_url'], entries[0]['url'])
            reader = sqlite3.connect(
                f'file:{database_path.resolve().as_posix()}?mode=ro', uri=True)
            try:
                source_run = reader.execute(
                    'SELECT found_count,fetched_count FROM source_runs').fetchone()
                run_stats = json.loads(reader.execute(
                    'SELECT stats_json FROM runs').fetchone()[0])
            finally:
                reader.close()
            self.assertEqual(tuple(source_run), (2, 1))
            self.assertEqual(run_stats['new'], 1)
            release_second.set()
            thread.join(5)

            self.assertFalse(errors)
            self.assertEqual(result[0][1]['item_failed'], 1)
            self.assertEqual(result[0][1]['source_failed'], 1)

    def test_every_successful_ingest_including_unchanged_is_published(self):
        entry = {'url': 'https://example.invalid/one', 'title': VALID_TITLE, 'published_at': TODAY}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = connect(root / 'collector.sqlite3')
            try:
                sync_sources(db, [SOURCE])
                calls = []
                with patch('collector.pipeline.listing', return_value=[entry, entry]):
                    run(db, {'sources': [SOURCE]}, root,
                        fetcher=type('Fetcher', (), {'get': lambda self, url: Response(url)})(),
                        on_record=lambda: calls.append(
                            export_jsonl(db, root / 'records.jsonl')))
                self.assertEqual(calls, [1, 1])
                self.assertEqual(len(snapshot(root / 'records.jsonl')), 1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM versions').fetchone()[0], 1)
            finally:
                db.close()

    def test_replace_failure_keeps_previous_snapshot_and_run_unfinished(self):
        entry = {'url': 'https://example.invalid/one', 'title': VALID_TITLE, 'published_at': TODAY}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = connect(root / 'collector.sqlite3')
            target = root / 'records.jsonl'
            target.write_text('previous\n', encoding='utf-8')
            try:
                sync_sources(db, [SOURCE])
                with patch('collector.pipeline.listing', return_value=[entry]), \
                        patch.object(Path, 'replace', side_effect=OSError('replace failed')):
                    with self.assertRaisesRegex(OSError, 'replace failed'):
                        run(db, {'sources': [SOURCE]}, root,
                            fetcher=type('Fetcher', (), {'get': lambda self, url: Response(url)})(),
                            on_record=lambda: export_jsonl(db, target))
                self.assertEqual(target.read_text(encoding='utf-8'), 'previous\n')
                self.assertEqual(db.execute('SELECT status FROM runs').fetchone()[0], 'running')
                self.assertEqual(db.execute('SELECT status FROM source_runs').fetchone()[0], 'running')
                stats = json.loads(db.execute('SELECT stats_json FROM runs').fetchone()[0])
                self.assertEqual(stats['new'], 1)
                self.assertEqual(stats['item_failed'], 0)
                self.assertEqual(list(root.glob('.records-*.tmp')), [])
            finally:
                db.close()

    def test_replace_retries_transient_sharing_violation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = connect(root / 'collector.sqlite3')
            target = root / 'records.jsonl'
            target.write_text('previous\n', encoding='utf-8')
            original_replace = Path.replace
            calls = []

            def replace_once(destination):
                calls.append(destination)
                if len(calls) == 1:
                    raise PermissionError('consumer still has the snapshot open')
                pending = next(root.glob('.records-*.tmp'))
                return original_replace(pending, destination)

            try:
                with patch.object(export_module.Path, 'replace', side_effect=replace_once), \
                        patch.object(export_module.time, 'sleep') as sleep:
                    self.assertEqual(export_jsonl(db, target), 0)
                self.assertEqual(len(calls), 2)
                sleep.assert_called_once_with(0.1)
                self.assertEqual(target.read_text(encoding='utf-8'), '')
                self.assertEqual(list(root.glob('.records-*.tmp')), [])
            finally:
                db.close()

    def test_persistent_sharing_violation_keeps_snapshot_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = connect(root / 'collector.sqlite3')
            target = root / 'records.jsonl'
            target.write_text('previous\n', encoding='utf-8')
            try:
                with patch.object(export_module.Path, 'replace',
                                  side_effect=PermissionError('consumer keeps snapshot open')) as replace, \
                        patch.object(export_module.time, 'sleep') as sleep:
                    with self.assertRaisesRegex(PermissionError, 'consumer keeps snapshot open'):
                        export_jsonl(db, target)
                self.assertEqual(replace.call_count, 20)
                self.assertEqual(sleep.call_count, 19)
                self.assertEqual(target.read_text(encoding='utf-8'), 'previous\n')
                self.assertEqual(list(root.glob('.records-*.tmp')), [])
            finally:
                db.close()

    def test_cli_passes_real_publisher_callback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / 'sources.json'
            data = root / 'data'
            config.write_text(json.dumps({'sources': [SOURCE]}), encoding='utf-8')

            def fake_run(db, config_payload, data_root, **kwargs):
                run_id = db.execute(
                    "INSERT INTO runs(started_at,status) VALUES (?, 'running')",
                    (datetime.now(timezone.utc).isoformat(),)).lastrowid
                db.commit()
                ingest(db, data_root, run_id, config_payload['sources'][0],
                       {'url': 'https://example.invalid/one', 'title': 'Notice one'},
                       Response('https://example.invalid/one'))
                self.assertTrue(callable(kwargs['on_record']))
                kwargs['on_record']()
                return run_id, {'new': 1, 'changed': 0, 'markup_changed': 0,
                                'unchanged': 0, 'source_failed': 0, 'item_failed': 0}

            with patch('collector.__main__.run', side_effect=fake_run):
                main(['--config', str(config), '--data', str(data), 'run'])
            output = data / 'export' / 'records.jsonl'
            self.assertEqual(len(snapshot(output)), 1)


if __name__ == '__main__':
    unittest.main()
