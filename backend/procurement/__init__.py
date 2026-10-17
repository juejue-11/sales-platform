"""Small public interface for pure procurement aggregation."""

from .model import document_from_row, document_public_metadata, normalize_document, procurement_fingerprint
from .summary import build_procurement_summary

__all__ = [
    "build_procurement_summary",
    "document_from_row",
    "document_public_metadata",
    "normalize_document",
    "procurement_fingerprint",
]
