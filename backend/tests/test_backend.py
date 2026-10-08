from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.ai import AIError
from backend.main import create_app
from backend.service import ConflictError, ScreeningService, ServiceError
from backend.utils import SHANGHAI


def row(record_id: int = 1, *, body: str = "采购破碎机，截止时间：2099-12-31 09:00。", deadline: str | None = "2099-12-31 09:00"):
    return {
        "id": record_id, "source_id": "test", "kind": "tender", "title": "破碎机采购公告",
        "project_name": "破碎机采购公告", "buyer": "某矿业公司", "body_text": body,
        "equipment": ["破碎机"], "facts": [{"text": "采购破碎机", "evidence": "采购破碎机"}],
        "inferences": [], "published_at": "2026-10-08", "deadline_at": deadline,
        "archive_path": f"archive/{record_id}/v1.html", "current_version": 1,
    }


class Provider:
    def __init__(self, payload=None, fail=False):
        self.payload = payload or {
            "decision": "consider", "summary": "有条件跟进", "demand_summary": "公告提到设备采购",
            "reasons": [{"text": "正文有采购需求", "evidence": [{"record_id": 1, "quote": "采购破碎机"}]}],
            "unknowns": ["需要确认型号"], "next_actions": ["取得技术清单"],
            "options": [{"id": "a", "title": "按需求核对", "description": "核对需求后再推进",
                         "suitable_for": "型号资料齐全", "tradeoffs": [], "unknowns": [], "next_action": "核对型号"}],
        }
        self.fail = fail

    async def complete(self, **kwargs):
        if self.fail:
            raise AIError("simulated provider failure")
        return {"choices": [{"message": {"content": json.dumps(self.payload, ensure_ascii=False)}}]}


def make_service(tmp_path: Path, records: list[dict], provider=None) -> ScreeningService:
    source = tmp_path / "records.jsonl"
    source.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in records), encoding="utf-8")
    service = ScreeningService(db_path=tmp_path / "screening.sqlite3", source_path=source,
                               archive_root=tmp_path, provider=provider or Provider())
    asyncio.run(service.import_file())
    return service


def configure_ai(service: ScreeningService):
    service.update_settings({"ai": {"base_url": "https://mock.local/v1", "model": "mock", "enabled": True,
                                     "api_key": "secret-value"},
                             "business": {"capabilities": "测试能力"}})


def test_duplicate_import_is_atomic_and_idempotent(tmp_path: Path):
    service = make_service(tmp_path, [row()])
    assert service.dashboard()["total_records"] == 1
    assert asyncio.run(service.import_file())["unchanged"] == 1
    service.source_path.write_text('{"id": 1}\nnot-json\n', encoding="utf-8")
    with pytest.raises(ServiceError):
        asyncio.run(service.import_file())
    assert service.dashboard()["total_records"] == 1
    assert service.status()["import_error"]
    service.close()


def test_source_version_or_template_hash_updates_history_without_ai_stale(tmp_path: Path):
    service = make_service(tmp_path, [row()])
    configure_ai(service)
    queued = service.queue_project(1)
    asyncio.run(service.run_job(queued["job_id"]))
    changed = row()
    changed["current_version"] = 2
    changed["content_sha256"] = "layout-only-hash"
    service.source_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    result = asyncio.run(service.import_file())
    assert result["updated"] == 1
    assert service.summary(1)["ai_status"] == "succeeded"
    assert service.db.execute("SELECT current_version FROM records WHERE id=1").fetchone()[0] == 2
    assert service.db.execute("SELECT count(*) FROM record_versions WHERE record_id=1").fetchone()[0] == 2
    service.close()


def test_auto_import_toggle_and_signature_detection(tmp_path: Path):
    service = make_service(tmp_path, [row()])
    service.update_settings({"import": {"auto_import": False, "poll_seconds": 1}})
    changed = row(body="采购破碎机，截止时间：2099-12-31 09:00。新正文。")
    changed["current_version"] = 2
    service.source_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")

    async def scenario():
        service.start_background()
        await asyncio.sleep(1.2)
        assert service.db.execute("SELECT current_version FROM records WHERE id=1").fetchone()[0] == 1
        service.update_settings({"import": {"auto_import": True, "poll_seconds": 1}})
        await asyncio.sleep(1.5)

    asyncio.run(scenario())
    assert service.db.execute("SELECT current_version FROM records WHERE id=1").fetchone()[0] == 2
    service.close()


def test_missing_fields_are_review_not_excluded_and_possible_duplicate_stays_separate(tmp_path: Path):
    a = row(1, body="采购项目，未给出设备和截止时间。", deadline=None)
    a["equipment"] = []
    b = row(2, body="采购项目，未给出设备和截止时间。", deadline=None)
    b["equipment"] = []
    b["possible_duplicate_of"] = 1
    service = make_service(tmp_path, [a, b])
    assert service.dashboard()["total_projects"] == 2
    assert service.summary(2)["gate_status"] == "review"
    assert 1 in [x["id"] for x in service.project_detail(2)["related_projects"]]
    service.close()


def test_project_key_and_unsafe_related_hint_do_not_auto_merge(tmp_path: Path):
    first = row(1)
    first.update({"project_key": "same-key", "notice_number": "A-1", "title": "同一设备采购公告"})
    second = row(2, body="另一批次采购破碎机。", deadline=None)
    second.update({"project_key": "same-key", "notice_number": "A-2", "title": "同一设备采购公告",
                   "related_record_id": 1})
    service = make_service(tmp_path, [first, second])
    assert service.dashboard()["total_projects"] == 2
    assert service.summary(2)["record_ids"] == [2]
    assert 1 in [x["id"] for x in service.project_detail(2)["related_projects"]]
    service.close()


def test_verified_related_notice_can_share_project(tmp_path: Path):
    first = row(1)
    first.update({"project_key": "k1", "notice_number": "A-1", "title": "同一设备采购公告"})
    second = row(2, body="成交同一设备采购。", deadline=None)
    second.update({"project_key": "k2", "notice_number": "A-1", "title": "同一设备采购成交结果公告",
                   "kind": "award", "related_record_id": 1})
    service = make_service(tmp_path, [first, second])
    assert service.dashboard()["total_projects"] == 1
    assert service.summary(1)["record_ids"] == [2, 1]
    service.close()


def test_ai_judgment_evidence_and_human_choice_survive_new_version(tmp_path: Path):
    service = make_service(tmp_path, [row()])
    configure_ai(service)
    queued = service.queue_project(1)
    asyncio.run(service.run_job(queued["job_id"]))
    assert service.summary(1)["ai_status"] == "succeeded"
    judgment_id = service.project_detail(1)["judgment"]["id"]
    service.save_decision(1, {"status": "following", "owner": "销售", "next_action": "联系采购", "note": "人工确认",
                              "selected_option_id": "a", "judgment_id": judgment_id})
    changed = row(body="采购破碎机，截止时间：2099-12-31 09:00。新增防爆资质要求。")
    changed["current_version"] = 2
    service.source_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    assert asyncio.run(service.import_file())["updated"] == 1
    detail = service.project_detail(1)
    assert detail["ai_status"] == "queued"
    assert detail["judgment"]["stale"] is True
    assert detail["decision"]["selected_option_id"] == "a"
    with pytest.raises(ConflictError):
        service.save_decision(1, {"status": "following", "selected_option_id": "a"})
    service.close()


def test_invalid_evidence_fails_without_fake_judgment_and_retry_can_recover(tmp_path: Path):
    bad = Provider({"decision": "consider", "summary": "x", "demand_summary": "x",
                    "reasons": [{"text": "x", "evidence": [{"record_id": 1, "quote": "不存在的原文"}]}],
                    "unknowns": [], "next_actions": [], "options": []})
    service = make_service(tmp_path, [row()], provider=bad)
    configure_ai(service)
    queued = service.queue_project(1)
    asyncio.run(service.run_job(queued["job_id"]))
    assert service.summary(1)["ai_status"] == "failed"
    assert service.project_detail(1)["judgment"] is None
    service.provider = Provider()
    assert service.retry_failed() == 1
    job_id = service.db.execute("SELECT id FROM jobs ORDER BY id DESC LIMIT 1").fetchone()[0]
    asyncio.run(service.run_job(int(job_id)))
    assert service.summary(1)["ai_status"] == "succeeded"
    service.close()


def test_generation_that_races_an_import_is_saved_stale_and_requeued(tmp_path: Path):
    class BlockingProvider(Provider):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def complete(self, **kwargs):
            self.started.set()
            await self.release.wait()
            return await super().complete(**kwargs)

    provider = BlockingProvider()
    service = make_service(tmp_path, [row()], provider=provider)
    configure_ai(service)

    async def scenario():
        queued = service.queue_project(1)
        running = asyncio.create_task(service.run_job(queued["job_id"]))
        await provider.started.wait()
        changed = row(body="采购破碎机，截止时间：2099-12-31 09:00。新增技术条件。")
        changed["current_version"] = 2
        service.source_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        await service.import_file()
        provider.release.set()
        await running

    asyncio.run(scenario())
    assert service.project_detail(1)["ai_status"] == "queued"
    assert service.project_detail(1)["judgment"]["stale"] is True
    assert service.db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0] == 1
    service.close()


def test_positive_judgment_without_source_evidence_is_rejected(tmp_path: Path):
    provider = Provider({"decision": "recommend", "summary": "推荐", "demand_summary": "需求明确",
                         "reasons": [{"text": "看起来适合", "evidence": []}], "unknowns": [],
                         "next_actions": ["联系采购"], "options": []})
    service = make_service(tmp_path, [row()], provider=provider)
    configure_ai(service)
    queued = service.queue_project(1)
    asyncio.run(service.run_job(queued["job_id"]))
    assert service.summary(1)["ai_status"] == "failed"
    assert service.project_detail(1)["judgment"] is None
    service.close()


def test_empty_capability_card_cannot_store_positive_fit_or_options(tmp_path: Path):
    service = make_service(tmp_path, [row()])
    service.update_settings({"ai": {"base_url": "https://mock.local/v1", "model": "mock", "enabled": True,
                                     "api_key": "secret-value"}})
    queued = service.queue_project(1)
    asyncio.run(service.run_job(queued["job_id"]))
    judgment = service.project_detail(1)["judgment"]
    assert judgment["decision"] == "verify"
    assert judgment["options"] == []
    service.close()


def test_api_key_is_write_only_and_archive_path_is_constrained(tmp_path: Path):
    archive = tmp_path / "archive" / "1"
    archive.mkdir(parents=True)
    (archive / "v1.html").write_text("source", encoding="utf-8")
    service = make_service(tmp_path, [row()])
    configure_ai(service)
    assert "api_key" not in service.get_settings()["ai"]
    assert service.get_settings()["ai"]["has_api_key"] is True
    assert service.archive_path(1).read_text(encoding="utf-8") == "source"
    service.db.execute("UPDATE records SET archive_path='../screening.sqlite3' WHERE id=1")
    with pytest.raises(Exception):
        service.archive_path(1)
    service.close()


def test_deadline_date_uses_shanghai_timezone(tmp_path: Path):
    service = make_service(tmp_path, [row(deadline="2026-10-08")])
    # The date-only deadline expires at the end of the Asia/Shanghai business day.
    from backend.rules import evaluate_record
    record = service._records_for_project(1)[0]
    from datetime import datetime
    gate, _ = evaluate_record(record, service.get_settings(), now=datetime(2026, 10, 8, 12, tzinfo=SHANGHAI))
    assert gate == "candidate"
    gate, _ = evaluate_record(record, service.get_settings(), now=datetime(2026, 10, 9, 0, tzinfo=SHANGHAI))
    assert gate == "expired"
    service.close()
