import asyncio
import json

import httpx
import pytest

from backend.analysis.errors import AIError
from backend.core.citations import citation_fragments
from backend.analysis.provider_finalizer import (
    _DEEPSEEK_DSML_INVOKE_CLOSE,
    _DEEPSEEK_DSML_PARAMETER_CLOSE,
    _YamlOutputError,
    _yaml_to_json,
    strict_format_report,
    StrictFinalizerResult,
)


VALID_YAML = r"""---
decision: |-
  consider
summary: |-
  number 12.50 and line one
  line two
report:
  version: 1
  sections:
    - id: |-
        section-1
      blocks:
        - type: |-
            paragraph
          text: |-
            keep this line
            and this line
          evidence:
            - record_id: 17
              quote: |-
                evidence line one
                evidence line two
options: []
...
"""


def _with_observed_dsml_tail(document: str) -> str:
    return document.rstrip("\n") + "\n" + _DEEPSEEK_DSML_PARAMETER_CLOSE + "\n" + _DEEPSEEK_DSML_INVOKE_CLOSE


def test_observed_deepseek_dsml_tail_is_unwrapped_without_changing_yaml_values():
    parsed = json.loads(_yaml_to_json(_with_observed_dsml_tail(VALID_YAML)))

    assert parsed["report"]["version"] == 1
    assert parsed["summary"] == "number 12.50 and line one\nline two"
    assert parsed["report"]["sections"][0]["blocks"][0]["evidence"][0]["quote"] == (
        "evidence line one\nevidence line two"
    )


@pytest.mark.parametrize(
    "tail",
    [
        "</unknown>",
        _DEEPSEEK_DSML_PARAMETER_CLOSE,
        _DEEPSEEK_DSML_INVOKE_CLOSE + "\n" + _DEEPSEEK_DSML_PARAMETER_CLOSE,
    ],
)
def test_unknown_or_incomplete_provider_tail_is_pollution(tail: str):
    with pytest.raises(_YamlOutputError) as caught:
        _yaml_to_json(VALID_YAML.rstrip("\n") + "\n" + tail)

    assert caught.value.polluted is True
    assert caught.value.eof is False


def test_missing_yaml_end_marker_is_distinguished_from_pollution():
    incomplete = VALID_YAML.replace("...\n", "")

    with pytest.raises(_YamlOutputError) as caught:
        _yaml_to_json(incomplete)

    assert caught.value.eof is True
    assert caught.value.polluted is False


def test_provider_pollution_classification_survives_the_ai_error_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("SCREENING_AI_TRACE_DIR", str(tmp_path))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{
            "message": {"content": VALID_YAML + "</unknown>"}, "finish_reason": "stop",
        }]})

    with pytest.raises(AIError) as caught:
        asyncio.run(strict_format_report(
            base_url="https://api.deepseek.com", model="mock", api_key="secret-value",
            messages=[], previous_output="bad JSON", available_evidence={},
            transport=httpx.MockTransport(handler),
        ))
    assert caught.value.metadata["polluted"] is True
    assert caught.value.metadata["eof"] is False


@pytest.mark.parametrize(
    "document",
    [
        _with_observed_dsml_tail(VALID_YAML.rstrip("\n") + "\n---\nsecond: |-\n  document\n...\n"),
        _with_observed_dsml_tail(VALID_YAML.replace("options: []\n", "options: []\noptions: []\n")),
        _with_observed_dsml_tail(VALID_YAML.replace("summary: |-\n", "summary: &value |-\n")),
    ],
)
def test_dsml_unwrap_does_not_relax_yaml_safety(document: str):
    with pytest.raises(_YamlOutputError):
        _yaml_to_json(document)


def test_business_prompt_and_previous_output_are_one_data_envelope():
    fragment = {**citation_fragments("actual read window")[0], "page": 1}
    business_messages = [
        {"role": "system", "content": "business JSON prompt"},
        {"role": "user", "content": "review fragment frag-1"},
    ]
    previous_output = '{"report":{"evidence":[{"fragment_id":"frag-1"}]}}'
    available_evidence = {
        "documents": [{
            "record_id": 17,
            "read_fragments": [{"page": 1, "text": "actual read window"}],
            "evidence_fragments": [fragment],
        }],
    }
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": VALID_YAML}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    result = asyncio.run(strict_format_report(
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        api_key="secret-value",
        messages=business_messages,
        previous_output=previous_output,
        available_evidence=available_evidence,
        transport=httpx.MockTransport(handler),
    ))

    assert isinstance(result, StrictFinalizerResult)
    payload_messages = calls[0]["messages"]
    assert [message["role"] for message in payload_messages] == ["system", "user"]
    data = json.loads(payload_messages[1]["content"])
    assert data["original_messages"] == business_messages
    assert data["previous_output"] == previous_output
    assert data["available_evidence"] == available_evidence
    assert data["available_evidence"]["documents"][0]["evidence_fragments"][0] == fragment
