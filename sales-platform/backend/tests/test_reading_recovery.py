from __future__ import annotations

import asyncio
import copy
import json

import pytest

from backend.analysis.errors import AIError
from backend.application.reading import READING_BATCH_CHARS, _batched_material, invoke_reading
from backend.procurement.reading import build_material, build_reading_messages, material_version


def _material() -> dict:
    text = "本次采购内容，提交日期为 2026-10-23。"
    return {
        "material_version": "m-recovery",
        "sources": [{
            "source_id": "announcement-1", "source_kind": "announcement", "record_id": 1,
            "text": text,
        }],
        "segments": [{
            "id": "segment-1", "source_id": "announcement-1", "source_kind": "announcement",
            "record_id": 1, "page": None, "text": text,
        }],
        "coverage": [{"source_kind": "announcement", "record_id": 1,
                      "segments": 1, "characters": len(text), "status": "provided"}],
        "corrections": [],
    }


def _valid_reading() -> dict:
    return {
        "version": 1,
        "summary": "corrected",
        "demand_type": "confirmed",
        "sections": [],
        "milestones": [],
    }


def _three_segment_material() -> tuple[dict, str]:
    body = "x" * 18_000
    record = {"id": 1, "kind": "tender", "title": "long notice", "body_text": body}
    return build_material([record], [], version=material_version([record], []), segment_chars=6_000), body


def test_fixed_batches_preserve_order_and_bound_default_segments():
    material, body = _three_segment_material()
    batches = [batch for batch in _batched_material(material) if not batch.get("_unread_tail")]

    assert len(batches) == 2
    assert [batch["batch"] for batch in batches] == [
        {"index": 1, "total": 2}, {"index": 2, "total": 2},
    ]
    assert all(sum(len(str(segment.get("text") or "")) for segment in batch["segments"])
               <= READING_BATCH_CHARS for batch in batches)
    flattened = "".join(segment["text"] for batch in batches for segment in batch["segments"])
    assert flattened == body
    assert [segment["id"] for batch in batches for segment in batch["segments"]] == [
        "segment-1", "segment-2", "segment-3",
    ]


def test_oversized_presegmented_input_remains_intact_at_batch_boundary():
    material, _ = _three_segment_material()
    oversized = "y" * (READING_BATCH_CHARS + 1)
    material["segments"] = [{"id": "oversized", "source_id": "announcement-1",
                              "source_kind": "announcement", "record_id": 1,
                              "page": None, "text": oversized}]

    batches = [batch for batch in _batched_material(material) if not batch.get("_unread_tail")]

    assert len(batches) == 1
    assert batches[0]["segments"][0]["text"] == oversized
    assert len(batches[0]["segments"][0]["text"]) > READING_BATCH_CHARS


def test_reading_prompt_does_not_duplicate_segment_text_in_source_metadata():
    payload = json.loads(build_reading_messages(_material())[1]["content"])

    assert "text" not in payload["sources"][0]
    assert payload["segments"][0]["text"] == _material()["segments"][0]["text"]


def test_reading_prompt_keeps_source_text_when_no_segments_are_available():
    material = _material()
    material["segments"] = []

    payload = json.loads(build_reading_messages(material)[1]["content"])

    assert payload["sources"][0]["text"] == material["sources"][0]["text"]


def test_reading_prompt_keeps_unidentified_source_text():
    material = _material()
    material["sources"][0].pop("source_id")
    material["segments"][0].pop("source_id")

    payload = json.loads(build_reading_messages(material)[1]["content"])

    assert payload["sources"][0]["text"] == material["sources"][0]["text"]


@pytest.mark.parametrize(
    "invalid_output",
    [
        "not json",
        {"version": 1, "demand_type": "confirmed", "milestones": []},
    ],
    ids=["invalid-json", "missing-sections"],
)
def test_recoverable_reading_format_error_retries_only_the_current_batch(invalid_output):
    material = _material()
    calls: list[tuple[dict, list[dict[str, str]]]] = []

    class Reader:
        async def read_procurement(self, *, material, messages, **kwargs):
            calls.append((copy.deepcopy(material), copy.deepcopy(messages)))
            return invalid_output if len(calls) == 1 else _valid_reading()

    result = asyncio.run(invoke_reading(Reader(), material=material, config={"model": "test"}))

    assert result["status"] == "complete"
    assert result["summary"] == "corrected"
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    correction = calls[1][1][-1]["content"]
    assert "validation_error" in correction
    assert "previous_output" in correction
    assert "上一轮采购阅读输出未通过程序校验" in correction
    assert "not json" in correction or "milestones" in correction


def test_successful_first_batch_is_not_repeated_when_second_batch_is_corrected():
    material, _ = _three_segment_material()
    calls: list[dict] = []

    class Reader:
        async def read_procurement(self, *, material, **kwargs):
            batch_number = material["batch"]["index"]
            calls.append({"batch": batch_number, "segments": copy.deepcopy(material["segments"])})
            if batch_number == 1:
                return {**_valid_reading(), "summary": "first batch"}
            if sum(call["batch"] == 2 for call in calls) == 1:
                return "not json"
            return {**_valid_reading(), "summary": "second batch"}

    result = asyncio.run(invoke_reading(Reader(), material=material, config={"model": "test"}))

    assert [call["batch"] for call in calls] == [1, 2, 2]
    assert calls[0]["segments"] != calls[1]["segments"]
    assert calls[1]["segments"] == calls[2]["segments"]
    assert result["status"] == "complete"
    assert result["summary"] == "first batch；second batch"


def test_second_reading_format_error_is_explicit_and_bounded_to_one_retry():
    calls = 0

    class Reader:
        async def read_procurement(self, **kwargs):
            nonlocal calls
            calls += 1
            return "not json"

    with pytest.raises(AIError) as caught:
        asyncio.run(invoke_reading(Reader(), material=_material(), config={"model": "test"}))

    assert calls == 2
    assert caught.value.code == "MODEL_FORMAT_ERROR"
    assert "JSON" in str(caught.value)


@pytest.mark.parametrize("code", ["MODEL_ERROR", "MODEL_CONFIG", "OUTPUT_TOKEN_LIMIT"])
def test_provider_errors_are_not_retried(code):
    calls = 0

    class Reader:
        async def read_procurement(self, **kwargs):
            nonlocal calls
            calls += 1
            raise AIError("provider failure", code=code)

    with pytest.raises(AIError) as caught:
        asyncio.run(invoke_reading(Reader(), material=_material(), config={"model": "test"}))

    assert calls == 1
    assert caught.value.code == code


def test_openai_length_finish_reason_is_not_retried():
    calls = 0

    class Provider:
        def complete(self, **kwargs):
            nonlocal calls
            calls += 1
            return {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}

    with pytest.raises(AIError) as caught:
        asyncio.run(invoke_reading(Provider(), material=_material(), config={"model": "test"}))

    assert calls == 1
    assert caught.value.code == "OUTPUT_TOKEN_LIMIT"


def test_complete_provider_format_error_can_be_corrected_once():
    calls = 0

    class Provider:
        def complete(self, **kwargs):
            nonlocal calls
            calls += 1
            content = "not json" if calls == 1 else json.dumps(_valid_reading(), ensure_ascii=False)
            return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}

    result = asyncio.run(invoke_reading(Provider(), material=_material(), config={"model": "test"}))

    assert calls == 2
    assert result["status"] == "complete"


def test_cancellation_is_not_retried():
    calls = 0

    class Reader:
        async def read_procurement(self, **kwargs):
            nonlocal calls
            calls += 1
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(invoke_reading(Reader(), material=_material(), config={"model": "test"}))

    assert calls == 1


def test_secret_response_is_rejected_without_correction():
    secret = "sk-reading-secret"
    calls = 0

    class Reader:
        async def read_procurement(self, **kwargs):
            nonlocal calls
            calls += 1
            return {**_valid_reading(), "summary": secret}

    with pytest.raises(AIError) as caught:
        asyncio.run(invoke_reading(Reader(), material=_material(),
                                   config={"model": "test", "api_key": secret}))

    assert calls == 1
    assert secret not in str(caught.value)
