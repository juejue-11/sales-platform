"""Stable references to contiguous text supplied to the model."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .utils import sha256_text


def citation_fragments(text: str) -> list[dict[str, str]]:
    """Split a source into bounded, unchanged spans identified by content."""
    result: list[dict[str, str]] = []
    start = 0
    while start < len(text):
        end = min(start + 600, len(text))
        if end < len(text):
            boundary = max(text.rfind(mark, start + 300, end) for mark in ("\n", "。", "；"))
            if boundary >= 0:
                end = boundary + 1
        fragment = text[start:end]
        if fragment.strip():
            result.append({"fragment_id": "ev-" + sha256_text(f"{start}:{fragment}")[:24],
                           "text": fragment})
        start = end
    return result


def resolve_citation(fragment_id: Any, windows: Iterable[dict[str, Any]], *,
                     page: int | None = None) -> dict[str, Any]:
    """Resolve only within the caller's trusted, actually read windows."""
    if not isinstance(fragment_id, str) or not fragment_id:
        raise ValueError("invalid evidence fragment_id")
    for window in windows:
        if page is not None and window.get("page") != page:
            continue
        for fragment in citation_fragments(str(window.get("text") or "")):
            if fragment["fragment_id"] == fragment_id:
                return {"quote": fragment["text"], "page": window.get("page")}
    raise ValueError("evidence fragment was not read in this job")
