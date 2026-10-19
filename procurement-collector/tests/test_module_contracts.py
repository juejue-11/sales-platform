import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collector import export as export_module
from collector import identity, pipeline, report
from collector.db import connect, sync_sources


SOURCE = {'id': 'contract', 'name': '契约测试源', 'category': 'procurement',
          'entry_url': 'https://example.org/list', 'adapter': 'html_list',
          'enabled': True, 'access_status': 'public'}


class ModuleContractTests(unittest.TestCase):
    def _database(self, root, *, invalid=False):
        db = connect(root / 'collector.sqlite3')
        sync_sources(db, [SOURCE])
        equipment = 'invalid-json' if invalid else '["破碎机"]'
        record_id = db.execute('''INSERT INTO records
            (source_id,canonical_url,kind,title,published_at,buyer,project_name,
             notice_number,deadline_at,company,equipment_json,facts_json,inferences_json,
             project_key,possible_duplicate_of,duplicate_of,related_record_id,first_seen_at,
             last_seen_at,current_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('contract', 'https://example.org/item', 'tender', '测试公告', '2026-10-13',
             '测试采购方', '测试公告', 'CN-1234', None, None, equipment,
             '[{"text":"正文事实","evidence":"正文事实"}]', '[]', None, None, None,
             None, '2026-10-13T00:00:00+00:00', '2026-10-13T00:00:00+00:00', 2)).lastrowid
        db.execute('''INSERT INTO versions
            (record_id,version,fetched_at,original_url,archive_path,content_sha256,
             mime_type,body_text,parse_status,parse_error)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
                   (record_id, 2, '2026-10-13T00:00:00+00:00', 'https://example.org/item',
                    'archive/1/v2.html', 'hash-v2', 'text/html', '正文事实', 'ok', None))
        if not invalid:
            db.execute('''INSERT INTO version_attachments(record_id,version,attachments_json)
                          VALUES (?,?,?)''',
                       (record_id, 2, json.dumps([{'name': '附件.pdf', 'status': 'parsed',
                                                   'archive_path': 'archive/1/v2-attachments/a.pdf'}],
                                                  ensure_ascii=False)))
        db.commit()
        return db

    @patch.dict(os.environ, {'OPENAI_API_KEY': '', 'OPENAI_MODEL': ''})
    def test_old_identity_imports_are_aliases_for_new_module(self):
        for name in ('equivalent_record', 'related_project', 'relink_projects',
                     'normalized_match_text', 'duplicate_candidates'):
            self.assertIs(getattr(pipeline, name), getattr(identity, name))

    @patch.dict(os.environ, {'OPENAI_API_KEY': '', 'OPENAI_MODEL': ''})
    def test_old_and_new_export_entrypoints_preserve_jsonl_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = self._database(root)
            try:
                current = root / 'current.jsonl'
                compat = root / 'compat.jsonl'
                self.assertEqual(export_module.export_jsonl(db, current), 1)
                self.assertEqual(report.export_jsonl(db, compat), 1)
                self.assertEqual(current.read_bytes(), compat.read_bytes())
                record = json.loads(current.read_text(encoding='utf-8'))
                self.assertEqual(record['current_version'], 2)
                self.assertEqual(record['content_sha256'], 'hash-v2')
                self.assertEqual(record['attachments'][0]['name'], '附件.pdf')
                self.assertEqual(record['equipment'], ['破碎机'])
            finally:
                db.close()

    @patch.dict(os.environ, {'OPENAI_API_KEY': '', 'OPENAI_MODEL': ''})
    def test_export_failure_keeps_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = self._database(root, invalid=True)
            target = root / 'records.jsonl'
            target.write_text('previous\n', encoding='utf-8')
            try:
                with self.assertRaises(json.JSONDecodeError):
                    export_module.export_jsonl(db, target)
                self.assertEqual(target.read_text(encoding='utf-8'), 'previous\n')
                self.assertEqual(list(root.glob('.records-*.tmp')), [])
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()
