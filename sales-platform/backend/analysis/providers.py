from __future__ import annotations

from typing import Any, Protocol

import httpx

from .errors import AIError

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
