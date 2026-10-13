from __future__ import annotations

import hashlib
import io

from docx import Document

from backend.public_documents import read_archived_documents


def test_archive_is_hash_checked_and_confined_to_record(tmp_path):
    document = Document()
    document.add_paragraph("采购破碎机两台")
    output = io.BytesIO()
    document.save(output)
    raw = output.getvalue()
    relative = "archive/1/v1-attachments/spec.docx"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    metadata = {"archive_path": relative, "sha256": hashlib.sha256(raw).hexdigest(),
                "name": "规格.docx", "url": "https://official.example/spec.docx",
                "text": "不可信缓存正文", "fetched_at": "2026-10-12T08:00:00+08:00"}
    values = read_archived_documents({"id": 1, "attachments": [metadata]}, tmp_path)
    assert values[0]["status"] == "parsed"
    assert "采购破碎机两台" in values[0]["text"]
    assert "不可信缓存正文" not in values[0]["text"]
    assert values[0]["fetched_at"] == metadata["fetched_at"]
    assert read_archived_documents({"id": 2, "attachments": [metadata]}, tmp_path) == []
    assert read_archived_documents({"id": 1, "attachments": [{**metadata, "sha256": "0" * 64}]}, tmp_path) == []
    assert read_archived_documents({"id": 1, "attachments": [{**metadata, "archive_path": "../spec.docx"}]}, tmp_path) == []
