from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from backend.analysis.ai import AIError
import backend.application.service as service_module
from backend.application.reading import READING_BATCH_CHARS, _merge_batch_readings, invoke_reading
from backend.application.service import ScreeningService
from backend.procurement.reading import build_material, material_version, normalize_reading, segment_text


def _notice_224() -> dict:
    candidates = [
        Path(__file__).parent / "fixtures" / "notice_224.json",
    ]
    # The owning repository keeps the single public fixture.  Do not fall
    # back to a user's collector export: tests must stay deterministic and
    # must never pull private source data into the reading contract.
    candidates.append(Path("C:/Users/cgr/Desktop/new/销售平台筛选系统/backend/tests/fixtures/notice_224.json"))
    for path in candidates:
        if not path.is_file():
            continue
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(value, dict) and int(value.get("id") or 0) == 224:
            return value
    pytest.skip("the collector #224 fixture is not available")


def _material_224() -> tuple[dict, dict]:
    record = _notice_224()
    version = material_version([record], [])
    return record, build_material([record], [], version=version)


def _notice_quotes(body: str) -> tuple[str, str, str]:
    positions = [match.start() for match in re.finditer("2026", body)]
    assert len(positions) >= 4
    access = body[max(0, positions[1] - 60):positions[3] + 80]
    submission = body[max(0, positions[3] - 30):positions[3] + 85]
    amount = body[max(0, body.find("90") - 55):body.find("90") + 100]
    return access, submission, amount


def _reading_payload(record: dict, *, bad: bool = False) -> dict:
    access, submission, qualification = _notice_quotes(record["body_text"])
    return {
        "version": 1,
        "summary": "source grounded",
        "demand_type": "confirmed",
        "stage": "submission",
        "buyer": "",
        "region": "",
        "category": ["crusher"],
        "sections": [{
            "id": "qualification",
            "title": "qualification",
            "requirements": [{
                "id": "02-qualification",
                "text": qualification,
                "explanation": "the cited qualification text is retained",
                "sources": [{"source_kind": "announcement", "record_id": 224, "quote": qualification}],
            }],
        }],
        "milestones": [
            {"id": "access", "kind": "document_access", "label": "document access cutoff",
             "at": "2026-10-19T16:00:00+08:00",
             "sources": [{"source_kind": "announcement", "record_id": 224, "quote": access}]},
            {"id": "submission", "kind": "submission", "label": "submission cutoff",
             "at": "2099-10-23T13:30:00+08:00" if bad else "2026-10-23T13:30:00+08:00",
             "sources": [{"source_kind": "announcement", "record_id": 224, "quote": submission}]},
        ],
    }


def test_notice_224_keeps_access_and_submission_dates_and_02_lot():
    record, material = _material_224()
    result = normalize_reading(_reading_payload(record), material)

    assert result["status"] == "complete"
    assert result["stage"] == "submission"
    assert {milestone["kind"] for milestone in result["milestones"]} == {"document_access", "submission"}
    assert {milestone["at"] for milestone in result["milestones"]} == {
        "2026-10-19T16:00:00+08:00", "2026-10-23T13:30:00+08:00"
    }
    requirement = result["sections"][0]["requirements"][0]
    assert requirement["sources"]
    assert "02" in re.sub(r"\s+", "", requirement["sources"][0]["quote"])
    assert "90" in re.sub(r"\s+", "", requirement["sources"][0]["quote"])
    assert "2026-10-19" not in requirement["sources"][0]["quote"]


def test_untrusted_date_amount_lot_and_restriction_cannot_become_facts():
    record, material = _material_224()
    access, submission, qualification = _notice_quotes(record["body_text"])
    restriction_start = record["body_text"].find("3.11")
    restriction = record["body_text"][restriction_start:restriction_start + 80]
    joint_claim = chr(32852) + chr(21512) + chr(20307) + " can participate"
    fake_amount = "contract amount 91" + chr(19975) + chr(20803)
    payload = {
        "demand_type": "confirmed",
        "stage": "free-form-model-stage",
        "sections": [{"id": "facts", "title": "facts", "requirements": [
                {"id": "fake-amount", "text": fake_amount, "explanation": "", "sources": [
                {"source_kind": "announcement", "record_id": 224, "quote": qualification}]},
            {"id": "fake-lot", "text": "01 package requirement", "explanation": "", "sources": [
                {"source_kind": "announcement", "record_id": 224, "quote": qualification}]},
                {"id": "fake-restriction", "text": joint_claim, "explanation": "", "sources": [
                {"source_kind": "announcement", "record_id": 224, "quote": restriction}]},
        ]}],
        "milestones": [{"id": "fake-date", "kind": "submission", "label": "submission", 
                        "at": "2099-01-01T09:00:00+08:00", "sources": [
                            {"source_kind": "announcement", "record_id": 224, "quote": submission}]}],
    }
    result = normalize_reading(payload, material)

    assert result["stage"] == ""
    assert result["milestones"] == []
    requirements = result["sections"][0]["requirements"]
    assert all(item["sources"] == [] and item["status"] == "uncertain" for item in requirements)
    assert result["unresolved"]


def test_explicit_current_lot_scope_rejects_background_lot_citation():
    record = {"id": 224, "kind": "tender", "title": "multi-lot notice", "body_text": (
        "2.7标段划分：01包背景设备，02包本次招标内容。"
        "本次招标内容为02包。02包合同额不低于90万元。"
    )}
    material = build_material([record], [], version=material_version([record], []))
    quote = record["body_text"]
    result = normalize_reading({"sections": [{"id": "qualification", "requirements": [{
        "id": "wrong-scope", "lot": "01包", "text": "01包合同额不低于90万元",
        "explanation": "01包要求", "sources": [{"source_kind": "announcement", "record_id": 224, "quote": quote}],
    }]}]}, material)

    requirement = result["sections"][0]["requirements"][0]
    assert requirement["status"] == "uncertain"
    assert any("适用包范围" in value for value in result["unresolved"])


def test_news_only_material_is_lead_and_unverified_milestone_is_not_kept():
    record = {"id": 7, "kind": "news", "title": "signal", "body_text": "2026-10-20 news"}
    material = build_material([record], [], version=material_version([record], []))
    payload = {"demand_type": "confirmed", "stage": "confirmed", "summary": "signal"}

    result = normalize_reading(payload, material)

    assert result["demand_type"] == "lead"
    assert result["milestones"] == []
    assert result["unresolved"]


def test_correction_must_be_explicitly_adopted():
    record = {"id": 1, "kind": "tender", "title": "notice", "body_text": "corrected requirement"}
    correction = {"id": 8, "project_id": 1, "requirement_id": "r1", "text": "corrected requirement",
                  "reason": "human review", "material_version": "old"}
    material = build_material([record], [], version=material_version([record], [], [correction]),
                              corrections=[correction])
    quote = record["body_text"]
    base = {"sections": [{"id": "facts", "title": "facts", "requirements": [{
        "id": "r1", "text": "old requirement", "explanation": "", 
        "sources": [{"source_kind": "announcement", "record_id": 1, "quote": quote}],
    }]}]}

    rejected = normalize_reading(base, material)
    assert any("人工纠正" in value for value in rejected["unresolved"])
    base["sections"][0]["requirements"][0]["text"] = "corrected requirement"
    adopted = normalize_reading(base, material)
    assert not any("correction" in value for value in adopted["unresolved"])


def test_scan_or_unavailable_attachment_forces_partial():
    record = {"id": 1, "kind": "tender", "title": "notice", "body_text": "announcement"}
    document = {"id": "doc-scan", "record_id": 1, "name": "scan.pdf", "status": "scan_required",
                "sha256": "scan-hash", "text": "", "page_texts": []}
    material = build_material([record], [document], version=material_version([record], [document]))

    result = normalize_reading({"summary": "announcement"}, material)

    assert result["status"] == "partial"
    assert any("附件资料" in value for value in result["unresolved"])


def test_long_material_is_sent_in_bounded_batches_and_coverage_is_host_counted():
    body = " ".join(f"fact-{index}" for index in range(5_000))
    record = {"id": 11, "kind": "tender", "title": "long", "body_text": body}
    material = build_material([record], [], version=material_version([record], []), segment_chars=4_000)
    calls: list[dict] = []

    class Reader:
        async def read_procurement(self, *, material, **kwargs):
            calls.append(material)
            segment = material["segments"][0]
            return {"version": 1, "summary": str(material["batch"]["index"]), "demand_type": "confirmed",
                    "category": ["cat" + str(material["batch"]["index"])], "milestones": [],
                    "sections": [{"id": "facts", "title": "facts", "requirements": [{
                        "id": "r" + str(material["batch"]["index"]), "text": segment["text"], "explanation": "",
                        "sources": [{"source_kind": "announcement", "record_id": 11, "quote": segment["text"]}],
                    }]}]}

    result = asyncio.run(invoke_reading(Reader(), material=material, config={"model": "test"}))

    assert len(calls) > 1
    assert all(sum(len(str(segment.get("text") or "")) for segment in call["segments"]) <= READING_BATCH_CHARS
               for call in calls)
    assert sum(item["segments"] for item in result["coverage"]) == len(material["segments"])
    assert result["status"] == "complete"
    assert len(result["category"]) == len(calls)


def test_attachment_page_text_is_limited_to_the_sent_batch():
    record = {"id": 1, "kind": "tender", "title": "notice", "body_text": "announcement"}
    page_text = " ".join(f"page-fact-{index}" for index in range(8_000))
    document = {"id": "doc-page", "record_id": 1, "name": "detail.pdf", "status": "parsed",
                "sha256": "page-hash", "text": page_text, "page_texts": [{"page": 1, "text": page_text}]}
    material = build_material([record], [document], version=material_version([record], [document]), segment_chars=4_000)
    from backend.application.reading import _batched_material

    batches = _batched_material(material)
    model_batches = [batch for batch in batches if not batch.get("_unread_tail")]
    assert len(model_batches) > 1
    assert all(len(source["page_texts"][0]["text"]) <= READING_BATCH_CHARS
               for batch in model_batches for source in batch["sources"] if source.get("page_texts"))


def test_service_fetches_attachment_before_explicit_reading_and_does_not_refetch_same_version(tmp_path, monkeypatch):
    record = {"id": 1, "source_id": "test", "kind": "tender", "title": "notice", "project_name": "notice",
              "buyer": "buyer", "body_text": "announcement", "attachments": [{"name": "detail.pdf", "url": "https://example.test/detail.pdf"}],
              "current_version": 1, "published_at": "2026-10-01", "deadline_at": None}
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    calls: list[int] = []

    async def fetch(value):
        calls.append(int(value["id"]))
        return [{"id": "doc-detail", "record_id": value["id"], "name": "detail.pdf", "url": value["source_url"],
                 "status": "parsed", "sha256": "detail-hash", "text": "parsed attachment", "page_texts": []}]

    class Reader:
        async def read_procurement(self, *, material, **kwargs):
            return {"version": 1, "summary": "read", "demand_type": "confirmed", "stage": "confirmed",
                    "sections": [], "milestones": []}

    monkeypatch.setattr(service_module, "fetch_record_documents", fetch)
    service = ScreeningService(db_path=tmp_path / "db.sqlite3", source_path=source, archive_root=tmp_path,
                               provider=Reader())
    try:
        asyncio.run(service.import_file())
        service.update_settings({"ai": {"base_url": "https://mock.local/v1", "model": "mock",
                                         "enabled": True, "api_key": "test-key"}})
        first = asyncio.run(service.run_reading(1, force=True))
        second = asyncio.run(service.run_reading(1, force=True))
        assert first["status"] == second["status"] == "complete"
        assert calls == [1]
        assert service.db.execute("SELECT status FROM procurement_documents WHERE id='doc-detail'").fetchone()[0] == "parsed"
        assert service.db.execute("SELECT count(*) FROM procurement_readings WHERE project_id=1").fetchone()[0] >= 2
    finally:
        service.close()


def test_market_report_shape_is_rejected_by_formal_reading_protocol():
    record, material = _material_224()

    class Reader:
        async def read_procurement(self, *, material, **kwargs):
            return {"decision": "consider", "summary": "market report", "report": {"sections": []}}

    with pytest.raises(AIError, match="version"):
        asyncio.run(invoke_reading(Reader(), material=material, config={"model": "test"}))


def test_batch_merge_uses_global_ids_and_keeps_conflicting_sources():
    material = {"material_version": "m-test"}
    values = [
        {"version": 1, "status": "complete", "summary": "first", "demand_type": "confirmed",
         "stage": "submission", "sections": [{"id": "facts", "title": "facts", "requirements": [{
             "id": "same", "text": "first fact", "explanation": "first explanation",
             "sources": [{"source_kind": "announcement", "record_id": 1, "quote": "first"}],
         }]}], "milestones": [{"id": "same", "kind": "submission", "label": "submit",
                                  "at": "2026-10-01T09:00:00+08:00",
                                  "sources": [{"source_kind": "announcement", "record_id": 1, "quote": "first date"}]}]},
        {"version": 1, "status": "complete", "summary": "second", "demand_type": "confirmed",
         "stage": "submission", "sections": [{"id": "facts", "title": "facts", "requirements": [{
             "id": "same", "text": "second fact", "explanation": "second explanation",
             "sources": [{"source_kind": "announcement", "record_id": 2, "quote": "second"}],
         }]}], "milestones": [{"id": "same", "kind": "submission", "label": "submit",
                                  "at": "2026-10-02T09:00:00+08:00",
                                  "sources": [{"source_kind": "announcement", "record_id": 2, "quote": "second date"}]}]},
    ]

    result = _merge_batch_readings(values, material, truncated=False)
    ids = [section["id"] for section in result["sections"]]
    ids.extend(requirement["id"] for section in result["sections"] for requirement in section["requirements"])
    ids.extend(milestone["id"] for milestone in result["milestones"])
    assert len(ids) == len(set(ids))
    requirements = result["sections"][0]["requirements"]
    assert len(requirements) == 2
    assert {source["record_id"] for item in requirements for source in item["sources"]} == {1, 2}
    assert len(result["milestones"]) == 2
    assert {source["record_id"] for item in result["milestones"] for source in item["sources"]} == {1, 2}
    assert result["conflicts"]


def _reading_restart_service(tmp_path: Path) -> ScreeningService:
    record = {"id": 1, "source_id": "restart", "kind": "tender", "title": "notice",
              "project_name": "notice", "buyer": "buyer", "body_text": "announcement",
              "attachments": [], "current_version": 1, "published_at": "2026-10-01", "deadline_at": None}
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    service = ScreeningService(db_path=tmp_path / "db.sqlite3", source_path=source,
                               archive_root=tmp_path, provider=object())
    asyncio.run(service.import_file())
    return service


def _save_reading_state(service: ScreeningService, status: str) -> None:
    version = str(service._reading_material(1)["material_version"])
    service._save_procurement_reading(
        1, {"version": 1, "status": status, "summary": status, "demand_type": "unknown",
            "stage": "", "buyer": "", "region": "", "category": [], "sections": [],
            "milestones": [], "coverage": [], "conflicts": [], "unresolved": []},
        material_version=version, config={},
    )


def test_restart_does_not_requeue_history_pending_after_new_complete(tmp_path):
    service = _reading_restart_service(tmp_path)
    _save_reading_state(service, "pending")
    _save_reading_state(service, "complete")
    db_path, source_path = service.db_path, service.source_path
    service.close()

    reopened = ScreeningService(db_path=db_path, source_path=source_path,
                                archive_root=tmp_path, provider=object())
    try:
        reopened.start_background()
        assert reopened.db.execute("SELECT count(*) FROM procurement_reading_jobs").fetchone()[0] == 0
    finally:
        reopened.close()


def test_restart_recovers_only_the_latest_pending_reading(tmp_path):
    service = _reading_restart_service(tmp_path)
    _save_reading_state(service, "complete")
    _save_reading_state(service, "pending")
    db_path, source_path = service.db_path, service.source_path
    service.close()

    reopened = ScreeningService(db_path=db_path, source_path=source_path,
                                archive_root=tmp_path, provider=object())
    try:
        reopened.start_background()
        reopened.start_background()
        rows = reopened.db.execute(
            "SELECT project_id,status FROM procurement_reading_jobs ORDER BY id"
        ).fetchall()
        assert [(int(row["project_id"]), row["status"]) for row in rows] == [(1, "queued")]
    finally:
        reopened.close()
