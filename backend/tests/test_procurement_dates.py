from __future__ import annotations

try:
    from backend.procurement.dates import dates_in_quote, normalize_timestamp, timestamp_supported
except ModuleNotFoundError:  # pragma: no cover - useful in a standalone checkout
    import importlib.util
    import sys
    from pathlib import Path

    _path = Path(__file__).parents[1] / "procurement" / "dates.py"
    _spec = importlib.util.spec_from_file_location("procurement_dates", _path)
    assert _spec is not None and _spec.loader is not None
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _module
    _spec.loader.exec_module(_module)
    dates_in_quote = _module.dates_in_quote
    normalize_timestamp = _module.normalize_timestamp
    timestamp_supported = _module.timestamp_supported


def test_notice_224_range_keeps_endpoint_pairing_and_layout_whitespace():
    quote = "发售时间：\n2026年\n10\n月\n13\n日\n9:00 - 2026年\n10\n月\n19\n日\n16:00"
    assert dates_in_quote(quote) == [
        "2026-10-13T09:00:00+08:00",
        "2026-10-19T16:00:00+08:00",
    ]
    assert timestamp_supported("2026-10-13T09:00:00+08:00", quote)
    assert timestamp_supported("2026-10-19T16:00:00+08:00", quote)
    assert timestamp_supported("2026年10月13日9:00", quote)
    assert not timestamp_supported("2026-10-13T16:00:00+08:00", quote)
    assert not timestamp_supported("2099-10-13T09:00:00+08:00", quote)
    assert not timestamp_supported("2026-10-13T09:00:30+08:00", quote)


def test_chinese_time_and_normalization_equivalences():
    quote = "文件时间：2026年10月20日08时00分"
    assert dates_in_quote(quote) == ["2026-10-20T08:00:00+08:00"]
    assert timestamp_supported("2026-10-20T08:00:00+08:00", quote)
    assert timestamp_supported("2026-10-20 8:00", quote)
    assert normalize_timestamp("2026年10月20日08时00分") == "2026-10-20T08:00:00+08:00"
    assert normalize_timestamp("2026-10-20 8:00") == "2026-10-20T08:00:00+08:00"


def test_date_only_stays_date_only_and_explicit_offsets_become_local():
    assert normalize_timestamp("2026-10-20") == "2026-10-20"
    assert normalize_timestamp("2026-10-20T01:00:00Z") == "2026-10-20T09:00:00+08:00"
    quote = "截止日期：2026-10-20T01:00:00Z"
    assert timestamp_supported("2026-10-20T09:00:00+08:00", quote)
    assert timestamp_supported("2026-10-20", quote)


def test_daily_hours_do_not_attach_to_date_endpoints():
    quote = "2026年10月8日至2026年10月14日每日9:00时至17:00时"
    assert dates_in_quote(quote) == ["2026-10-08", "2026-10-14"]
    assert timestamp_supported("2026-10-08", quote)
    assert timestamp_supported("2026-10-14", quote)
    assert not timestamp_supported("2026-10-08T09:00:00+08:00", quote)


def test_sentence_boundary_does_not_borrow_working_hour():
    quote = "日期：2026年10月13日。工作时间：9:00-17:00"
    assert dates_in_quote(quote) == ["2026-10-13"]
    assert not timestamp_supported("2026-10-13T09:00:00+08:00", quote)


def test_padded_chinese_month_and_time_range_are_complete_pairs():
    quote = "下载时间：2026年09月29日08时00分至2026年10月20日08时00分"
    assert dates_in_quote(quote) == [
        "2026-09-29T08:00:00+08:00",
        "2026-10-20T08:00:00+08:00",
    ]
    assert timestamp_supported("2026-09-29T08:00:00+08:00", quote)
    assert timestamp_supported("2026-10-20T08:00:00+08:00", quote)


def test_invalid_date_and_time_are_not_normalized_or_invented():
    assert normalize_timestamp("2026-02-30") is None
    assert normalize_timestamp("2026-10-20 24:00") is None
    assert dates_in_quote("截止日期：10月20日，时间另行通知") == []


def test_compact_offset_is_normalized_and_range_separator_is_not_an_offset():
    assert normalize_timestamp("2026-10-20T09:00:00+0800") == "2026-10-20T09:00:00+08:00"
    quote = "时间：2026-10-20T09:00-2024-10-21T10:00"
    assert dates_in_quote(quote) == [
        "2026-10-20T09:00:00+08:00",
        "2024-10-21T10:00:00+08:00",
    ]
