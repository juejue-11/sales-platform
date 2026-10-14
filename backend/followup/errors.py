"""Errors exposed by the follow-up boundary."""

from __future__ import annotations


class FollowupError(RuntimeError):
    """Base error for invalid or conflicting follow-up operations."""


class FollowupValidationError(FollowupError, ValueError):
    """The caller supplied a value that cannot be persisted safely."""


class FollowupNotFoundError(FollowupError, LookupError):
    """A task, material, or project follow-up record does not exist."""


class FollowupConflictError(FollowupError):
    """A monotonic state or immutable-history invariant was violated."""
