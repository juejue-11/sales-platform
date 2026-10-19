from __future__ import annotations

from backend.procurement import procurement_fingerprint
from backend.procurement.summary import build_procurement_summary


def _row(body: str, record_id: int = 7) -> dict:
    return {"id": record_id, "kind": "tender", "body_text": body, "source_payload_json": "{}"}


def _document(*, text: str = "", items: list[dict] | None = None, pages: list[dict] | None = None,
              document_id: str = "doc-1", record_id: int = 7) -> dict:
    return {
        "id": document_id, "record_id": record_id, "name": "list.pdf",
        "url": "https://example.test/list.pdf", "status": "parsed", "pages": len(pages or []),
        "sha256": "hash-1", "mime_type": "application/pdf", "byte_size": 10,
        "text": text, "page_texts": pages or [], "items": items or [],
        "error": "", "is_current": True, "updated_at": "",
    }


def _body() -> str:
    return ("采购范围：破碎机2套。\n交货期：合同签订后30天。\n交货地点：甲方指定地点。\n"
            "投标人资格要求：须具备有效防爆合格证。\n联系方式：联系人张工，电话010-12345678。")


def test_equivalent_body_and_attachment_merge_sources_and_keep_facts():
    body = _body()
    page = body.replace("30天", "30天")
    result = build_procurement_summary(
        [_row(body)],
        [_document(
            text=page,
            pages=[{"page": 2, "text": page}],
            items=[{"name": "破碎机", "quantity": "2.000", "unit": "套", "specification": "TK240A",
                     "page": 2, "quote": "采购范围：破碎机2套。", "delivery_period": "合同签订后30天",
                     "delivery_location": "甲方指定地点"}],
        )],
    )
    assert len(result["items"]) == 1
    assert {source["source_kind"] for source in result["items"][0]["sources"]} == {"announcement", "attachment"}
    assert {fact["field"] for fact in result["facts"]} >= {"scope", "delivery_period", "delivery_location", "qualification", "contact"}
    for fact in result["facts"]:
        assert isinstance(fact["id"], str) and fact["id"]
        for source in fact["sources"]:
            assert "".join(source["quote"].split())


def test_distinct_attachment_specifications_are_not_merged():
    result = build_procurement_summary(
        [_row("")],
        [_document(items=[
            {"name": "钢板", "quantity": "2", "unit": "吨", "specification": "A", "quote": "钢板 A 2吨"},
            {"name": "钢板", "quantity": "2", "unit": "吨", "specification": "B", "quote": "钢板 B 2吨"},
        ])],
    )
    assert len(result["items"]) == 2
    assert {item["specification"] for item in result["items"]} == {"A", "B"}


def test_missing_item_quote_is_uncertain_without_fabricating_evidence():
    result = build_procurement_summary(
        [_row("")],
        [_document(items=[{"name": "钢板", "quantity": "2", "unit": "吨", "specification": "A"}])],
    )
    item = result["items"][0]
    assert item["status"] == "uncertain"
    assert item["sources"][0]["quote"] == ""
    assert "来源摘录缺失" in item["note"]


def test_conflicting_quantity_and_delivery_values_are_preserved_and_flagged():
    body = _body()
    page = body.replace("2套", "1套").replace("30天", "40天")
    result = build_procurement_summary(
        [_row(body)],
        [_document(
            text=page, pages=[{"page": 3, "text": page}],
            items=[{"name": "破碎机", "quantity": "1", "unit": "套", "specification": "TK240A",
                     "page": 3, "quote": "采购范围：破碎机1套。", "delivery_period": "合同签订后40天",
                     "delivery_location": "甲方指定地点"}],
        )],
    )
    assert {item["quantity"] for item in result["items"]} == {"1", "2"}
    assert all(item["status"] == "conflict" for item in result["items"])
    assert "不一致" in result["attachment_note"]
    period_facts = [fact for fact in result["facts"] if fact["field"] == "delivery_period"]
    assert {fact["value"] for fact in period_facts} == {"合同签订后30天", "合同签订后40天"}
    assert all(fact["status"] == "conflict" for fact in period_facts)


def test_docx_text_only_sources_omit_fabricated_page():
    body = _body()
    result = build_procurement_summary(
        [_row("")],
        [_document(text=body, items=[{"name": "破碎机", "quantity": "2", "unit": "套", "quote": "采购范围：破碎机2套。", "page": 1}])],
    )
    assert "page" not in result["items"][0]["sources"][0]
    assert all("page" not in source for fact in result["facts"] for source in fact["sources"])


def test_polluted_delivery_value_is_preserved_as_uncertain():
    text = ("采购范围：破碎机2套。交货地点：货物质量标准或主要技术性能指标："
            "本项目的主要技术质量要求详见：合同模板20260701、附件3：图纸。")
    result = build_procurement_summary(
        [_row("")],
        [_document(text=text, pages=[{"page": 4, "text": text}], items=[
            {"name": "破碎机", "quantity": "2", "unit": "套", "page": 4,
             "delivery_location": "货物质量标准或主要技术性能指标：本项目的主要技术质量要求详见：合同模板20260701、附件3：图纸",
             "quote": "交货地点：货物质量标准或主要技术性能指标：本项目的主要技术质量要求详见：合同模板20260701、附件3：图纸。"},
        ])],
    )
    assert result["items"][0]["delivery_location"].startswith("货物质量标准")
    assert result["items"][0]["status"] == "uncertain"
    assert any(fact["field"] == "delivery_location" and fact["status"] == "uncertain" for fact in result["facts"])


def test_same_item_from_two_announcement_records_keeps_both_sources_with_attachment():
    first = _body()
    second = first.replace("张工", "李工")
    attachment = _document(
        text=first, pages=[{"page": 2, "text": first}],
        items=[{"name": "破碎机", "quantity": "2", "unit": "套", "specification": "TK240A",
                 "page": 2, "quote": "采购范围：破碎机2套。", "delivery_period": "合同签订后30天",
                 "delivery_location": "甲方指定地点"}],
    )
    result = build_procurement_summary([_row(first, 7), _row(second, 8)], [attachment])
    assert len(result["items"]) == 1
    announcement_sources = [source for source in result["items"][0]["sources"]
                            if source["source_kind"] == "announcement"]
    assert {source["record_id"] for source in announcement_sources} == {7, 8}
    assert {source["source_kind"] for source in result["items"][0]["sources"]} == {"announcement", "attachment"}


def test_news_body_does_not_become_procurement_fact():
    result = build_procurement_summary(
        [{"id": 9, "kind": "news", "body_text": _body(), "source_payload_json": "{}"}], [],
    )
    assert result["items"] == []
    assert result["facts"] == []


def test_different_attachment_material_delivery_values_are_uncertain_not_global_conflict():
    first = "采购范围：钢板A2吨。交货期：30天。"
    second = "采购范围：钢板B2吨。交货期：40天。"
    result = build_procurement_summary(
        [_row("")],
        [_document(
            text=f"{first}\n{second}",
            pages=[{"page": 1, "text": f"{first}\n{second}"}],
            items=[
                {"name": "钢板", "quantity": "2", "unit": "吨", "specification": "A", "page": 1,
                 "delivery_period": "30天", "quote": first},
                {"name": "钢板", "quantity": "2", "unit": "吨", "specification": "B", "page": 1,
                 "delivery_period": "40天", "quote": second},
            ],
        )],
    )
    periods = [fact for fact in result["facts"] if fact["field"] == "delivery_period"]
    assert {fact["value"] for fact in periods} == {"30天", "40天"}
    assert all(fact["status"] == "uncertain" for fact in periods)
    assert all("_scope_keys" not in fact for fact in result["facts"])
    assert result["status"] == "partial"


def test_provenance_and_uncertain_quote_note_do_not_change_fingerprint_but_conflict_does():
    first = {"status": "partial", "scope_summary": "x", "items": [{
        "name": "钢板", "quantity": "2", "unit": "吨", "note": "来源摘录缺失，无法定位原文片段。",
        "status": "uncertain", "sources": [{"source_kind": "attachment", "record_id": 7,
                                              "document_id": "doc-1", "page": 1, "quote": "", "sha256": "a"}],
    }], "delivery_period": "", "delivery_location": "", "missing_fields": [],
              "scope_quote": "", "attachment_status": "available", "attachment_note": "", "documents": []}
    second = {**first, "items": [{**first["items"][0], "sources": [{
        "source_kind": "attachment", "record_id": 7, "document_id": "doc-1", "page": 3,
        "quote": "different parser metadata", "sha256": "b",
    }]}]}
    assert procurement_fingerprint(first) == procurement_fingerprint(second)
    conflict = {**second, "items": [{**second["items"][0], "note": "交付字段存在多个来源值，已保留全部来源并标记冲突。"}]}
    assert procurement_fingerprint(first) != procurement_fingerprint(conflict)


def test_delivery_facts_require_heading_and_credible_value_and_keep_dirty_location_uncertain():
    text = (
        "采购范围：破碎机2套。交货期：2027-02-26。"
        "合同模板中未按交货期交货，买方有权要求赔偿或终止合同。"
        "交货地点：攀钢某公司。"
        "交货地点：攀钢某公司2.4货物质量标准或主要技术性能指标："
        "本项目主要技术要求详见：合同模板20260701、附件3：图纸。"
    )
    result = build_procurement_summary(
        [_row("")],
        [_document(text=text, pages=[{"page": 3, "text": text}])],
    )
    periods = [fact for fact in result["facts"] if fact["field"] == "delivery_period"]
    assert [fact["value"] for fact in periods] == ["2027-02-26"]
    locations = [fact for fact in result["facts"] if fact["field"] == "delivery_location"]
    assert {fact["value"] for fact in locations} == {
        "攀钢某公司",
        "攀钢某公司2.4货物质量标准或主要技术性能指标：本项目主要技术要求详见：合同模板20260701、附件3：图纸",
    }
    assert next(fact for fact in locations if fact["value"] == "攀钢某公司")["status"] == "extracted"
    assert next(fact for fact in locations if "2.4" in fact["value"])["status"] == "uncertain"
    assert not any("买方有权" in fact["value"] for fact in periods)
    assert not any("合同模板" in fact["value"] for fact in periods)


def test_attachment_scope_ignores_unlabelled_warranty_and_table_header_text():
    text = (
        "2、设备防锈保证期不少于自交付货物之日起6个月。"
        "备注码称描述描述号牌准量位。采购范围：破碎机2套。"
    )
    result = build_procurement_summary(
        [_row("")],
        [_document(text=text, pages=[{"page": 4, "text": text}])],
    )
    scopes = [fact for fact in result["facts"] if fact["field"] == "scope"]
    assert [fact["value"] for fact in scopes] == ["采购范围：破碎机2套"]
    assert all("防锈" not in fact["value"] and "备注码称" not in fact["value"] for fact in scopes)


def test_delivery_pointer_is_not_a_second_delivery_period_fact():
    text = "采购范围：破碎机2套。交货期：详见需求清单。"
    result = build_procurement_summary(
        [_row("")],
        [_document(text=text, pages=[{"page": 5, "text": text}])],
    )
    assert not any(fact["field"] == "delivery_period" for fact in result["facts"])


def test_conflicting_body_and_attachment_delivery_values_conflict_even_when_row_quote_has_delivery():
    body = "采购范围：破碎机2套。交货期：30天。"
    page = "采购范围：破碎机2套。交货期：40天。"
    result = build_procurement_summary(
        [_row(body)],
        [_document(
            text=page, pages=[{"page": 6, "text": page}],
            items=[{"name": "破碎机", "quantity": "2", "unit": "套", "specification": "TK240A",
                     "page": 6, "quote": page, "delivery_period": "40天"}],
        )],
    )
    periods = [fact for fact in result["facts"] if fact["field"] == "delivery_period"]
    assert {fact["value"] for fact in periods} == {"30天", "40天"}
    assert all(fact["status"] == "conflict" for fact in periods)


def test_independent_unpunctuated_heading_lines_are_still_source_backed():
    text = "采购范围：破碎机2套\n交货期：2027-02-26\n交货地点：甲方指定地点"
    result = build_procurement_summary(
        [_row("")],
        [_document(text=text, pages=[{"page": 7, "text": text}])],
    )
    assert {fact["field"] for fact in result["facts"]} >= {
        "scope", "delivery_period", "delivery_location",
    }
    assert all(source["quote"] for fact in result["facts"] for source in fact["sources"])


def test_dirty_attachment_location_does_not_turn_clean_body_location_into_item_conflict():
    body = _body()
    dirty = "攀钢某公司2.4货物质量标准或主要技术性能指标：合同模板20260701"
    result = build_procurement_summary(
        [_row(body)],
        [_document(items=[{
            "name": "破碎机", "quantity": "2", "unit": "套", "specification": "TK240A",
            "quote": "采购范围：破碎机2套。", "delivery_location": dirty,
        }])],
    )
    item = result["items"][0]
    assert item["status"] == "uncertain"
    assert "不一致" not in item.get("note", "")
    assert dirty in item["delivery_location"]
    assert "甲方指定地点" in item["delivery_location"]
    assert result["status"] == "partial"


def test_attachment_only_delivery_conflict_keeps_partial_status():
    first = "采购范围：破碎机2套。交货期：30天。"
    second = "采购范围：破碎机2套。交货期：40天。"
    result = build_procurement_summary(
        [_row("")],
        [_document(
            text=f"{first}\n{second}",
            pages=[{"page": 8, "text": f"{first}\n{second}"}],
            items=[
                {"name": "破碎机", "quantity": "2", "unit": "套", "specification": "TK240A",
                 "page": 8, "quote": first, "delivery_period": "30天"},
                {"name": "破碎机", "quantity": "2", "unit": "套", "specification": "TK240A",
                 "page": 8, "quote": second, "delivery_period": "40天"},
            ],
        )],
    )
    periods = [fact for fact in result["facts"] if fact["field"] == "delivery_period"]
    assert {fact["value"] for fact in periods} == {"30天", "40天"}
    assert all(fact["status"] == "conflict" for fact in periods)
    assert result["status"] == "partial"
