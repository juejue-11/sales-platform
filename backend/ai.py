from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

import httpx

from .utils import json_text, source_quote, sha256_text


logger = logging.getLogger(__name__)


class AIError(RuntimeError):
    pass


class AIProvider(Protocol):
    async def complete(self, *, base_url: str, model: str, api_key: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        ...


class OpenAICompatibleProvider:
    """Small HTTP client for OpenAI-compatible Chat Completions endpoints."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 90.0):
        self.transport = transport
        self.timeout = timeout

    async def complete(self, *, base_url: str, model: str, api_key: str,
                       messages: list[dict[str, str]]) -> dict[str, Any]:
        if not base_url or not model or not api_key:
            raise AIError("AI is not configured")
        url = base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
        except Exception as exc:  # do not expose API key or request headers
            raise AIError(f"AI request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise AIError(f"AI request returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise AIError("AI response was not JSON") from exc
        return data


def input_fingerprint(project: dict[str, Any], capability_card: dict[str, Any]) -> str:
    records = project.get("records", [])
    relevant = []
    for record in records:
        relevant.append({
            "id": record.get("id"), "title": record.get("title"), "body_text": record.get("body_text"),
            "kind": record.get("kind"), "buyer": record.get("buyer"), "equipment": record.get("equipment"),
            "facts": record.get("facts"), "inferences": record.get("inferences"),
            "deadline_at": record.get("deadline_at"), "related_record_id": record.get("related_record_id"),
        })
    return sha256_text(json_text({"records": relevant, "capability": capability_card}))


def build_messages(project: dict[str, Any], capability_card: dict[str, Any]) -> list[dict[str, str]]:
    kind = project.get("kind") or "tender"
    system = """你是销售机会判断助手。你只能依据输入的项目正文和公司能力卡回答，不得编造产品、型号、价格、交期、资质或采购事实。必须只返回一个 JSON 对象，不要 Markdown。字段固定为：
decision（recommend|consider|verify|not_recommended）、summary、demand_summary、reasons（数组，每项含 text 和 evidence 数组；每条 evidence 必须含存在的 record_id 和正文原文 quote）、unknowns（字符串数组）、next_actions（字符串数组）、options（0 到 3 项；每项含 id、title、description、suitable_for、tradeoffs 字符串数组、unknowns 字符串数组、next_action）。理由要用通俗语言。quote 必须从对应 record_id 的 body_text 连续逐字复制，保留原文用字、标点和空格；不能改写、拼接不相邻句子，不能引用标题、facts、公司能力卡或另一条记录代替正文。优先选简短且完整的原文片段；原文不存在的信息只能列为待核实，不得作为证据。若输入是新闻或矿业权动态，只能输出核实方向，不能把推测写成采购事实，decision 应为 verify，options 可以为空。若资料不足，使用 verify/consider 并明确待核实内容。"""
    records = []
    for record in project.get("records", []):
        # Keep source text available for evidence while bounding a pathological
        # collector record to a reasonable request size.
        body = str(record.get("body_text") or "")
        if len(body) > 30000:
            body = body[:30000] + "\n[正文已截断，不能引用截断部分以外的内容]"
        records.append({
            "record_id": record.get("id"), "kind": record.get("kind"), "title": record.get("title"),
            "buyer": record.get("buyer"), "published_at": record.get("published_at"),
            "deadline_at": record.get("deadline_at"), "equipment": record.get("equipment"),
            "facts": record.get("facts"), "inferences": record.get("inferences"), "body_text": body,
        })
    user = json_text({"project": {"id": project.get("id"), "title": project.get("title"), "kind": kind,
                                    "records": records}, "company_capability_card": capability_card})
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def correction_messages(messages: list[dict[str, str]], error: AIError) -> list[dict[str, str]]:
    """Ask once for a fresh, valid response while retaining all source inputs."""
    return [*messages, {"role": "user", "content": (
        f"上一次输出未通过程序校验：{error}。请根据前面的同一份资料重新生成完整 JSON，"
        "不要解释错误，不要使用 Markdown。检查所有字段类型与 JSON 引号、逗号。"
        "每条 evidence 的 record_id 必须存在；quote 必须连续逐字复制该记录的 body_text，"
        "不得概括、拼接或替换原文。删去无法找到原文支持的理由；"
        "依据不足时使用 verify 并写清待核实事项，不得编造证据。"
    )}]


def extract_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError("AI response did not contain message content") from exc
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        raise AIError("AI response content was empty")
    return content.strip()


def validate_judgment(payload: Any, project: dict[str, Any], model: str,
                      fingerprint: str) -> dict[str, Any]:
    """Validate model output before it can enter the judgments table."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise AIError("AI response content was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise AIError("AI response must be a JSON object")
    decision = payload.get("decision")
    if decision not in {"recommend", "consider", "verify", "not_recommended"}:
        raise AIError("invalid AI decision")
    if project.get("kind") == "news" and decision != "verify":
        raise AIError("news judgment must be verify")
    def required_text(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str):
            raise AIError(f"invalid AI field: {name}")
        value = value.strip()
        if not value:
            raise AIError(f"invalid AI field: {name}")
        return value
    summary = required_text("summary")
    demand_summary = required_text("demand_summary")
    unknowns = payload.get("unknowns", [])
    next_actions = payload.get("next_actions", [])
    if not isinstance(unknowns, list) or not all(isinstance(x, str) for x in unknowns):
        raise AIError("invalid unknowns")
    if not isinstance(next_actions, list) or not all(isinstance(x, str) for x in next_actions):
        raise AIError("invalid next_actions")
    record_map = {int(r["id"]): r for r in project.get("records", []) if r.get("id") is not None}
    reasons = payload.get("reasons", [])
    if not isinstance(reasons, list):
        raise AIError("invalid reasons")
    normalized_reasons = []
    for reason in reasons:
        if not isinstance(reason, dict) or not isinstance(reason.get("text"), str) or not reason.get("text", "").strip():
            raise AIError("invalid reason")
        evidence = reason.get("evidence", [])
        if not isinstance(evidence, list):
            raise AIError("invalid evidence")
        clean_evidence = []
        for item in evidence:
            if not isinstance(item, dict):
                raise AIError("invalid evidence item")
            try:
                record_id = int(item.get("record_id"))
            except (TypeError, ValueError) as exc:
                raise AIError("invalid evidence record_id") from exc
            record = record_map.get(record_id)
            quote = item.get("quote")
            matched_quote = source_quote(quote, str(record.get("body_text") or "")) if record and isinstance(quote, str) else None
            if matched_quote is None:
                raise AIError(f"evidence quote does not match source record (record_id={record_id})")
            clean_evidence.append({"record_id": record_id, "quote": matched_quote})
        normalized_reasons.append({"text": reason["text"].strip(), "evidence": clean_evidence})
    if decision in {"recommend", "consider"}:
        if not normalized_reasons or not any(item["evidence"] for item in normalized_reasons):
            raise AIError("positive AI judgment requires source evidence")
    options = payload.get("options", [])
    if not isinstance(options, list) or len(options) > 3:
        raise AIError("options must contain 0 to 3 items")
    normalized_options = []
    option_ids = set()
    for option in options:
        if not isinstance(option, dict):
            raise AIError("invalid option")
        option_id = option.get("id")
        if not isinstance(option_id, str) or not option_id.strip() or option_id in option_ids:
            raise AIError("invalid option id")
        option_ids.add(option_id)
        fields = {}
        for name in ("title", "description", "suitable_for", "next_action"):
            if not isinstance(option.get(name), str):
                raise AIError(f"invalid option field: {name}")
            fields[name] = option[name].strip()
        for name in ("tradeoffs", "unknowns"):
            value = option.get(name, [])
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise AIError(f"invalid option field: {name}")
            fields[name] = [x.strip() for x in value]
        fields["id"] = option_id.strip()
        normalized_options.append(fields)
    if project.get("kind") == "news" and options:
        # A news lead may have verification actions but no product delivery plan.
        for option in normalized_options:
            material = f"{option['title']} {option['description']}"
            if any(word in material for word in ("供货方案", "报价方案", "采购方案", "交付方案")):
                raise AIError("news judgment contains a procurement option")
    return {
        "created_at": None,
        "model": model,
        "input_fingerprint": fingerprint,
        "stale": False,
        "decision": decision,
        "summary": summary,
        "demand_summary": demand_summary,
        "reasons": normalized_reasons,
        "unknowns": [x.strip() for x in unknowns],
        "next_actions": [x.strip() for x in next_actions],
        "options": normalized_options,
    }
