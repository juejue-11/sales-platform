import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collector.db import connect, sync_sources
from collector.pipeline import ingest
from collector.report import render
from test_attachments import SOURCE, Response


class AttachmentReportTests(unittest.TestCase):
    def test_current_attachment_gaps_remain_visible_when_notice_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = connect(root / 'test.db')
            sync_sources(db, [SOURCE])
            run = db.execute("INSERT INTO runs(started_at,status) VALUES ('2026-10-12','running')").lastrowid
            entry = {'url': Response.url, 'title': '钢材采购招标公告'}
            attachments = [
                {'name': '技术附件.pdf', 'url': 'https://example.org/technical.pdf',
                 'status': 'failed', 'sha256': '', 'error': 'attachment_too_large'},
                {'name': '扫描清单.pdf', 'url': 'https://example.org/scanned.pdf',
                 'status': 'unreadable', 'sha256': 'scan', 'error': 'needs_ocr'},
                {'name': '已解析清单.pdf', 'url': 'https://example.org/list.pdf',
                 'status': 'parsed', 'sha256': 'parsed'},
            ]
            with patch('collector.attachments.collect', return_value=attachments):
                self.assertEqual(ingest(db, root, run, SOURCE, entry, Response()), 'new')
                self.assertEqual(ingest(db, root, run, SOURCE, entry, Response()), 'unchanged')
            report = render(db)
            db.close()
            self.assertIn('附件异常：记录 #1', report)
            self.assertIn('技术附件.pdf', report)
            self.assertIn('attachment_too_large', report)
            self.assertIn('扫描清单.pdf', report)
            self.assertIn('needs_ocr', report)
            self.assertIn('https://example.org/technical.pdf', report)
            self.assertNotIn('已解析清单.pdf', report)
