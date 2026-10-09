from __future__ import annotations

import asyncio
import json
from pathlib import Path

from backend.briefing import build_briefing, normalize_source_text
from backend.service import ScreeningService


def procurement_body() -> str:
    return """一、项目概况
本项目采购煤矿井下压裂泵。
二、采购范围
煤矿井下压裂泵 2 套。
三、交货期：合同签订后 30 日内，交货地点：山西某矿井下。
四、投标人资格要求：具备矿安标志证书、防爆合格证，并具有类似业绩。
五、联系方式：采购联系人：张三，电话：13800000000；平台客服：李四，电话：4000000000。
"""


# Representative fragments copied from the read-only collector export records
# 165/166/93.  They preserve the section transitions that caught the original
# implementation: a scope table followed by file caveats, delivery labels in
# one numbered section, and a contact block followed by an objection channel.
REAL_166_FRAGMENT = """三、招标范围：
1.本项目共划分为1个标段（包）：煤矿井下压裂泵采购。
2.本项目物资采购范围：本项目为煤矿井下压裂泵采购项目招标采购，煤矿井下压裂泵2套。
3.交货期：合同签订之日起30个日历天。
4.交货地点：甲方指定地点。
四、投标人资格要求：1.2投标产品必须具有矿用产品安全标志证书和防爆合格证。
"""
REAL_165_FRAGMENT = """3.招标范围：3.1设备名称及数量见下表：设备或材料名称：采暖通风设备，单位：台，数量：125。
3.2设备交货期：预计2个月。
4.投标人资格要求：4.3业绩要求：有已完成同类型业绩3项，含合同首页、采购内容页及签字盖章页。
5.招标文件出售方式与时间：文件售价：500元/包段。
"""
REAL_93_FRAGMENT = """7.联系方式：招标人：攀钢集团物资贸易有限公司，联系人：张芳，电话：0812-3383899。
8.异议渠道：异议联系人：谢某，联系电话：028-87702258。
"""


def source_row(record_id: int, *, kind: str = "tender", body: str | None = None) -> dict:
    return {
        "id": record_id,
        "source_id": "test",
        "kind": kind,
        "title": "煤矿井下压裂泵采购公告",
        "project_name": "煤矿井下压裂泵采购公告",
        "buyer": "某矿业公司",
        "body_text": body if body is not None else procurement_body(),
        "equipment": ["压裂泵"],
        "facts": [],
        "inferences": [],
        "published_at": "2026-10-08",
        "deadline_at": "2099-12-31 09:00",
        "original_url": "https://example.test/notice/1",
        "current_version": 1,
    }


def make_service(tmp_path: Path, rows: list[dict]) -> ScreeningService:
    source = tmp_path / "records.jsonl"
    source.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    service = ScreeningService(db_path=tmp_path / "screening.sqlite3", source_path=source,
                               archive_root=tmp_path)
    asyncio.run(service.import_file())
    return service


def test_briefing_extracts_real_scope_delivery_qualification_and_buyer_contact():
    briefing = build_briefing(source_row(7))

    assert briefing["record_id"] == 7
    assert "采购范围" in briefing["scope_excerpt"]
    assert "煤矿井下压裂泵" in briefing["scope_excerpt"]
    assert "2套" in briefing["scope_excerpt"]
    assert "30日" in briefing["delivery_excerpt"]
    assert "山西某矿井下" in briefing["delivery_excerpt"]
    assert "矿安" in briefing["qualification_excerpt"]
    assert "防爆" in briefing["qualification_excerpt"]
    assert "业绩" in briefing["qualification_excerpt"]
    assert "采购联系人" in briefing["contact_excerpt"]
    assert "平台客服" not in briefing["contact_excerpt"]
    assert all(len(briefing[key]) <= limit for key, limit in {
        "scope_excerpt": 450,
        "delivery_excerpt": 240,
        "qualification_excerpt": 350,
        "contact_excerpt": 240,
    }.items())


def test_briefing_does_not_promote_file_sale_or_platform_opening():
    body = "公告：请登录平台报名并购买招标文件。采购范围：煤矿井下压裂泵 2 套。"
    briefing = build_briefing(source_row(8, body=body))

    assert "煤矿井下压裂泵" in briefing["scope_excerpt"]
    assert "2套" in briefing["scope_excerpt"]
    assert "登录平台" not in briefing["scope_excerpt"]
    assert "购买招标文件" not in briefing["scope_excerpt"]


def test_inline_qualification_evidence_is_not_a_scope_heading():
    body = "投标人须提供已完成同类型业绩，含合同首页、采购内容页及签字盖章页。"
    briefing = build_briefing(source_row(81, body=body))

    assert briefing["scope_excerpt"] == ""


def test_unknown_scope_heading_returns_empty_excerpt():
    body = "三、采购范围：详见招标文件第五章。四、资格要求：详见招标文件。"
    briefing = build_briefing(source_row(82, body=body))

    assert briefing["scope_excerpt"] == ""
    assert briefing["qualification_excerpt"] == ""


def test_briefing_normalizes_collector_line_breaks_without_losing_units():
    body = "采\n购\n范\n围\n：煤\n矿\n井\n下\n压\n裂\n泵\n2\n套。交\n货\n期\n：30\n日。"
    normalized = normalize_source_text(body)
    briefing = build_briefing(source_row(9, body=body))

    assert "采购范围" in normalized
    assert "煤矿井下压裂泵2套" in normalized
    assert "采购范围" in briefing["scope_excerpt"]
    assert "2套" in briefing["scope_excerpt"]
    assert "30日" in briefing["delivery_excerpt"]


def test_real_collector_fragments_keep_scope_delivery_and_first_contact_block():
    scope = build_briefing(source_row(166, body=REAL_166_FRAGMENT))
    table = build_briefing(source_row(165, body=REAL_165_FRAGMENT))
    contact = build_briefing(source_row(93, body=REAL_93_FRAGMENT))

    assert "煤矿井下压裂泵2套" in scope["scope_excerpt"]
    assert "30个日历天" in scope["delivery_excerpt"]
    assert "甲方指定地点" in scope["delivery_excerpt"]
    assert "采暖通风设备" in table["scope_excerpt"]
    assert "125" in table["scope_excerpt"]
    assert "采购内容页" not in table["scope_excerpt"]
    assert "文件售价" not in table["scope_excerpt"]
    assert "招标人" in contact["contact_excerpt"]
    assert "攀钢集团物资贸易有限公司" in contact["contact_excerpt"]
    assert "0812-3383899" in contact["contact_excerpt"]
    assert "异议" not in contact["contact_excerpt"]


def test_news_keeps_substantive_scope_but_has_no_procurement_qualification_or_delivery():
    row = source_row(10, kind="news", body="行业动态：某矿区计划建设新项目，后续采购信息以正式公告为准。")
    briefing = build_briefing(row)

    assert "某矿区计划建设新项目" in briefing["scope_excerpt"]
    assert briefing["delivery_excerpt"] == ""
    assert briefing["qualification_excerpt"] == ""


def test_summary_contains_briefing_and_q_searches_equipment_and_scope(tmp_path: Path):
    service = make_service(tmp_path, [source_row(11)])
    summary = service.summary(11)
    assert summary["briefing"]["record_id"] == 11
    assert "2套" in summary["briefing"]["scope_excerpt"]
    assert summary["demand_summary"] is None
    assert summary["ai_next_actions"] == []
    assert service.list_projects(q="压裂泵")["total"] == 1
    assert service.list_projects(q="2套")["total"] == 1
    service.close()


def test_stale_judgment_does_not_leak_summary_or_next_actions(tmp_path: Path):
    service = make_service(tmp_path, [source_row(14)])
    service.db.execute(
        """INSERT INTO judgments
        (project_id,created_at,model,input_fingerprint,stale,decision,summary,demand_summary,
         reasons_json,unknowns_json,next_actions_json,options_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (14, "2026-10-08T10:00:00+08:00", "fixture", "fixture", 1, "recommend",
         "旧判断", "旧需求", "[]", "[]", '["旧下一步"]', "[]"),
    )
    service.db.commit()

    summary = service.summary(14)
    assert summary["ai_decision"] is None
    assert summary["ai_summary"] is None
    assert summary["demand_summary"] is None
    assert summary["ai_next_actions"] == []
    service.close()


def test_active_only_filters_out_closed_and_expired_projects(tmp_path: Path):
    active = source_row(12)
    closed = source_row(13)
    closed["kind"] = "award"
    service = make_service(tmp_path, [active, closed])
    all_items = service.list_projects(page_size=20)
    active_items = service.list_projects(active_only=True, page_size=20)

    assert all_items["total"] == 2
    assert active_items["total"] == 1
    assert active_items["items"][0]["id"] == 12
    service.close()
