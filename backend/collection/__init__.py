"""Process boundary for the independent sales-platform collector."""

from .manager import (
    CollectionBusy,
    CollectionError,
    CollectionInvalid,
    CollectionManager,
    CollectionUnavailable,
)

__all__ = [
    "CollectionBusy",
    "CollectionError",
    "CollectionInvalid",
    "CollectionManager",
    "CollectionUnavailable",
]
