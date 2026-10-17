"""Bounded, local views over trusted procurement document snapshots.

The document fetcher owns network access and persistence.  This module only
accepts the already trusted document snapshot supplied by the host and builds
a small directory or read window for the agent.  Keeping that boundary
explicit prevents a model supplied URL, path, or document id from becoming a
second data source.

The public integration surface is :func:`validate_document_access_arguments`
and :func:`build_document_view`.  A caller may keep the full snapshot in its
job-local registry and pass the same snapshot for each page or query read; no
network operation happens here.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any


DEFAULT_DOCUMENT_LIMIT = 8_000
MAX_DOCUMENT_LIMIT = 16_000
MAX_DOCUMENT_OFFSET = 500_000
MAX_DOCUMENT_PAGE = 100_000
MAX_DOCUMENT_ID_LENGTH = 160
MAX_DOCUMENT_QUERY_LENGTH = 4_000
# Leave room for the host's procurement summary when the directory is wrapped
# in the normal fetch_project_documents response.
MAX_DIRECTORY_RESPONSE_CHARS = 15_000
_DOCUMENT_ID_RE = re.compile(r"^[A-Za-z0-9_.:/-]+$")


class DocumentAccessError(ValueError):
    """A safe, stable error that the host can map onto the RPC error enum."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class DocumentAccessArguments:
    """Validated arguments for the fetch_project_documents read window."""

    record_id: int | None = None
    document_id: str | None = None
    page: int | None = None
    query: str | None = None
    offset: int = 0
    limit: int = DEFAULT_DOCUMENT_LIMIT

    @property
    def is_directory(self) -> bool:
        return self.document_id is None and self.page is None and self.query is None and self.offset == 0


_ALLOWED_ARGUMENTS = frozenset({"record_id", "document_id", "page", "query", "offset", "limit"})


def _positive_integer(value: Any, *, field: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DocumentAccessError("invalid_request", f"invalid {field}")
    if maximum is not None and value > maximum:
        raise DocumentAccessError("invalid_request", f"invalid {field}")
    return value


def _non_negative_integer(value: Any, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise DocumentAccessError("invalid_request", f"invalid {field}")
    return value


def validate_document_access_arguments(arguments: Any) -> DocumentAccessArguments:
    """Validate the agent's document directory/read arguments.

    ``record_id`` and ``document_id`` are identifiers only.  URLs, local paths,
    headers, and credentials are intentionally not part of this contract.
    ``document_id`` is checked against the trusted snapshot by
    :func:`build_document_view`, which is what prevents cross-document reads.
    """

    if arguments is None:
        arguments = {}
    if not isinstance(arguments, Mapping) or any(key not in _ALLOWED_ARGUMENTS for key in arguments):
        raise DocumentAccessError("invalid_request", "invalid document arguments")

    record_id = arguments.get("record_id")
    if record_id is not None:
        record_id = _positive_integer(record_id, field="record_id")

    document_id = arguments.get("document_id")
    if document_id is not None:
        if not isinstance(document_id, str):
            raise DocumentAccessError("invalid_request", "invalid document_id")
        document_id = document_id.strip()
        if (not document_id or len(document_id) > MAX_DOCUMENT_ID_LENGTH
                or not _DOCUMENT_ID_RE.fullmatch(document_id)):
            raise DocumentAccessError("invalid_request", "invalid document_id")

    page = arguments.get("page")
    if page is not None:
        page = _positive_integer(page, field="page", maximum=MAX_DOCUMENT_PAGE)

    query = arguments.get("query")
    if query is not None:
        if not isinstance(query, str):
            raise DocumentAccessError("invalid_request", "invalid query")
        query = query.strip()
        if not query or len(query) > MAX_DOCUMENT_QUERY_LENGTH:
            raise DocumentAccessError("invalid_request", "invalid query")

    offset = arguments.get("offset", 0)
    offset = _non_negative_integer(offset, field="offset", maximum=MAX_DOCUMENT_OFFSET)

    limit = arguments.get("limit", DEFAULT_DOCUMENT_LIMIT)
    limit = _positive_integer(limit, field="limit", maximum=MAX_DOCUMENT_LIMIT)

    return DocumentAccessArguments(
        record_id=record_id,
        document_id=document_id,
        page=page,
        query=query,
        offset=offset,
        limit=limit,
    )


def _record_id(document: Mapping[str, Any]) -> int | None:
    value = document.get("record_id")
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _document_id(document: Mapping[str, Any]) -> str:
    return str(document.get("id") or "").strip()


def _text(document: Mapping[str, Any]) -> str:
    value = document.get("text")
    return value if isinstance(value, str) else str(value or "")


def _page_texts(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    values = document.get("page_texts")
    if not isinstance(values, list):
        return result
    seen: set[int] = set()
    for value in values:
        if not isinstance(value, Mapping):
            continue
        raw_page = value.get("page")
        if isinstance(raw_page, bool):
            continue
        try:
            page = int(raw_page)
        except (TypeError, ValueError, OverflowError):
            continue
        if page < 1 or page in seen:
            continue
        text = value.get("text")
        if not isinstance(text, str):
            text = str(text or "")
        seen.add(page)
        result.append({"page": page, "text": text})
    return result


def _metadata(document: Mapping[str, Any]) -> dict[str, Any]:
    """Copy server metadata while excluding all full-content fields."""

    result: dict[str, Any] = {}
    for key in (
        "id", "record_id", "name", "url", "status", "pages", "sha256", "fetched_at",
        "mime_type", "byte_size", "error", "updated_at",
    ):
        if key not in document:
            continue
        value = document.get(key)
        if isinstance(value, str):
            # Metadata is descriptive and is never a citation fragment.  A
            # future parser cannot make a directory unbounded via a title or
            # error string.
            value = value[:4_000]
        result[key] = value
    return result


def build_procurement_context(procurement: Mapping[str, Any]) -> dict[str, Any]:
    """Expose a bounded reading index, not the cached bill of materials.

    Full rows and their quotations belong to an explicit document read. This
    applies to both the initial prompt and the SDK's local project reader.
    """
    result: dict[str, Any] = {}
    for key in ("status", "scope_summary", "delivery_period", "delivery_location",
                "attachment_status", "attachment_note"):
        value = procurement.get(key)
        if isinstance(value, str):
            result[key] = value[:2000]
    for key in ("missing_fields", "conflicts"):
        values = procurement.get(key)
        if isinstance(values, list):
            result[key] = [str(value)[:300] for value in values[:16]]
    items = procurement.get("items")
    result["item_count"] = len(items) if isinstance(items, list) else procurement.get("item_count", 0)
    documents = procurement.get("documents")
    result["documents"] = []
    if isinstance(documents, list):
        for document in documents:
            if not isinstance(document, Mapping):
                continue
            metadata = _metadata(document)
            if _json_size(result) + _json_size(metadata) > MAX_DIRECTORY_RESPONSE_CHARS:
                break
            result["documents"].append(metadata)
        result["document_count"] = len(documents)
    return result


def _compact_items(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Retain the old small-document item shape without unbounded payloads."""

    raw_items = document.get("items")
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in raw_items[:100]:
        if not isinstance(raw, Mapping):
            continue
        item: dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, str):
                item[str(key)] = value[:2_000]
            elif isinstance(value, (int, float, bool)) or value is None:
                item[str(key)] = value
        items.append(item)
    return items


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _available_text(document: Mapping[str, Any]) -> str:
    text = _text(document)
    if text:
        return text
    pages = _page_texts(document)
    return "\n\n".join(page["text"] for page in pages)


def _window(
    *,
    mode: str,
    request: DocumentAccessArguments,
    available: int,
    start: int = 0,
    returned: int = 0,
    page: int | None = None,
    matched: bool | None = None,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "offset": request.offset,
        "limit": request.limit,
        "source_offset": start,
        "returned_chars": returned,
        "available_chars": available,
        "page": page,
        "query": request.query,
        "matched": matched,
    }


def _directory_document(document: Mapping[str, Any], request: DocumentAccessArguments) -> dict[str, Any]:
    value = _metadata(document)
    source = _available_text(document)
    pages = _page_texts(document)
    # The old fetch response exposed text for small attachments.  Keep that
    # useful compatibility path, while long documents remain metadata-only and
    # therefore do not get injected into every model turn.
    page_chars = sum(len(str(page.get("text") or "")) for page in pages)
    if source and len(source) <= DEFAULT_DOCUMENT_LIMIT and page_chars <= DEFAULT_DOCUMENT_LIMIT:
        value["text"] = source
        value["page_texts"] = pages
        value["read_window"] = _window(
            mode="directory", request=request, available=len(source), returned=len(source),
            page=None, matched=None,
        )
        items = _compact_items(document)
        if items and _json_size(items) <= 4_000:
            value["items"] = items
    else:
        value["read_window"] = _window(
            mode="directory", request=request, available=len(source), returned=0,
            page=None, matched=None,
        )
    return value


def _select_document(documents: list[Mapping[str, Any]], request: DocumentAccessArguments) -> Mapping[str, Any]:
    if request.document_id is not None:
        if request.record_id is None:
            record_ids = {_record_id(document) for document in documents if _record_id(document) is not None}
            if len(record_ids) > 1:
                raise DocumentAccessError("record_not_in_project", "record_id is required for a multi-record snapshot")
        matches = [document for document in documents if _document_id(document) == request.document_id]
        if not matches:
            raise DocumentAccessError("not_found", "document not found")
        if request.record_id is not None and any(_record_id(document) != request.record_id for document in matches):
            raise DocumentAccessError("record_not_in_project", "document does not belong to record")
        if len(matches) != 1:
            raise DocumentAccessError("not_found", "document id is ambiguous")
        return matches[0]

    selected = documents
    if request.record_id is not None:
        selected = [document for document in documents if _record_id(document) == request.record_id]
    if len(selected) == 1:
        return selected[0]
    raise DocumentAccessError("invalid_request", "document_id is required for a multi-document read")


def _content_document(document: Mapping[str, Any], request: DocumentAccessArguments) -> dict[str, Any]:
    value = _metadata(document)
    pages = _page_texts(document)
    full_text = _available_text(document)
    selected_text = full_text
    selected_page: int | None = None
    mode = "document"
    matched = None
    source_start = 0

    if request.page is not None:
        mode = "page"
        page_values = [page for page in pages if page["page"] == request.page]
        if not page_values:
            # A parser that supplied no page table may still have a one-page
            # text value.  Page one remains a compatible read in that case.
            if request.page != 1 or not full_text:
                raise DocumentAccessError("not_found", "page not found")
            selected_text = full_text
        else:
            selected_text = page_values[0]["text"]
        selected_page = request.page

    if request.query is not None:
        mode = "query"
        needle = request.query.casefold()
        candidates = [(selected_page, selected_text, 0)]
        if request.page is None and pages:
            candidates = [(int(page["page"]), str(page["text"]), 0) for page in pages]
        found: tuple[int | None, str, int] | None = None
        for page, text, _ in candidates:
            index = text.casefold().find(needle)
            if index >= 0:
                found = (page, text, index)
                break
        if found is None:
            value["text"] = ""
            value["page_texts"] = []
            value["read_window"] = _window(
                mode=mode, request=request, available=len(selected_text), returned=0,
                page=selected_page, matched=False,
            )
            return value
        selected_page, selected_text, source_start = found
        # The query itself is the beginning of the returned continuous slice;
        # this makes a model quote directly copyable from the response.
        matched = True

    start = source_start + request.offset
    if start > len(selected_text):
        fragment = ""
    else:
        fragment = selected_text[start:start + request.limit]
    value["text"] = fragment
    value["page_texts"] = ([{"page": selected_page, "text": fragment}]
                            if selected_page is not None and fragment else [])
    value["read_window"] = _window(
        mode=mode, request=request, available=len(selected_text), returned=len(fragment),
        start=start, page=selected_page, matched=matched,
    )

    # Item facts are useful to older callers but are independent of the text
    # read window.  Include them only while keeping the complete response
    # bounded; the host's procurement summary remains the authoritative place
    # for the full list.
    items = _compact_items(document)
    if items and _json_size(items) <= 4_000:
        value["items"] = items
    return value


def build_document_view(
    documents: Iterable[Mapping[str, Any]],
    arguments: Mapping[str, Any] | DocumentAccessArguments | None = None,
) -> dict[str, Any]:
    """Return a bounded directory or content window from trusted snapshots.

    A directory request (only ``record_id`` or no arguments) can include the
    complete text of a short attachment for compatibility.  Long attachments
    return metadata and a ``read_window`` with ``returned_chars=0``.  A content
    request must resolve to exactly one document; page and query reads never
    concatenate pages or documents.
    """

    request = (arguments if isinstance(arguments, DocumentAccessArguments)
               else validate_document_access_arguments(arguments))
    trusted: list[Mapping[str, Any]] = [document for document in documents if isinstance(document, Mapping)]

    if request.document_id is not None:
        # Resolve against the full snapshot first, then apply record ownership.
        document = _select_document(trusted, request)
        value = _content_document(document, request)
        return {"documents": [value], "read_window": value.get("read_window"), "mode": "read"}

    if request.is_directory:
        selected = ([document for document in trusted if _record_id(document) == request.record_id]
                    if request.record_id is not None else trusted)
        result: list[dict[str, Any]] = []
        omitted = 0
        for index, document in enumerate(selected):
            candidate = _directory_document(document, request)
            with_content = result + [candidate]
            # Keep the serialized directory bounded even when a project has
            # many short attachments.  Metadata is retained; only optional
            # compatibility text is dropped when the directory budget is hit.
            summary = {
                "returned_documents": len(with_content),
                "omitted_documents": len(selected) - index - 1,
                "truncated": False,
            }
            if _json_size({"documents": with_content, "mode": "directory", "directory": summary}) > MAX_DIRECTORY_RESPONSE_CHARS:
                candidate.pop("text", None)
                candidate.pop("page_texts", None)
                candidate.pop("items", None)
                candidate["read_window"] = _window(
                    mode="directory", request=request,
                    available=len(_available_text(document)), returned=0,
                )
            summary["returned_documents"] = len(result) + 1
            if _json_size({
                "documents": result + [candidate],
                "mode": "directory",
                "directory": summary,
            }) > MAX_DIRECTORY_RESPONSE_CHARS:
                # Do not silently discard metadata.  Stop at the bounded
                # directory and report how many trusted documents remain.
                omitted = len(selected) - index
                break
            result.append(candidate)
        return {
            "documents": result,
            "mode": "directory",
            "directory": {
                "returned_documents": len(result),
                "omitted_documents": omitted,
                "truncated": omitted > 0,
            },
        }

    document = _select_document(trusted, request)
    value = _content_document(document, request)
    return {"documents": [value], "read_window": value.get("read_window"), "mode": "read"}


__all__ = [
    "DEFAULT_DOCUMENT_LIMIT", "MAX_DOCUMENT_LIMIT", "MAX_DOCUMENT_OFFSET",
    "DocumentAccessArguments", "DocumentAccessError", "build_document_view",
    "validate_document_access_arguments",
]
