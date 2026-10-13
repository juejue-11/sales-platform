import asyncio
import json
from pathlib import Path

import pytest

from backend.service import ScreeningService
from backend.tests.test_backend import row


@pytest.mark.parametrize("empty", ["", " \t\n", None])
def test_failed_attachment_does_not_block_later_daily_records(tmp_path: Path, empty):
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(row(1)), encoding="utf-8")
    service = ScreeningService(db_path=tmp_path / "screening.sqlite3",
                               source_path=source, archive_root=tmp_path, provider=object())
    try:
        asyncio.run(service.import_file())
        existing = row(1)
        existing["attachments"] = [{
            "name": "materials.pdf", "url": "https://example.test/materials.pdf",
            "status": "failed", "sha256": empty, "mime_type": empty,
            "archive_path": empty, "error": "attachment_too_large",
        }]
        source.write_text("\n".join(json.dumps(record) for record in
                                     [existing, row(2), row(3)]), encoding="utf-8")
        result = asyncio.run(service.import_file())
        assert result["inserted"] == 2
        assert result["total"] == 3
        payload = json.loads(service.db.execute(
            "SELECT source_payload_json FROM records WHERE id=1").fetchone()[0])
        attachment = payload["attachments"][0]
        assert attachment["status"] == "failed"
        assert attachment["error"] == "attachment_too_large"
        assert not attachment["sha256"]
        assert not service.status()["import_pending"]
        assert service.status()["import_error"] is None
        assert asyncio.run(service.import_file())["unchanged"] == 3
    finally:
        service.close()
