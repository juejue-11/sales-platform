"""Shared, conservative deadline selection from a saved reading."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ..core.utils import parse_timestamp


def submission_deadline(reading: Mapping[str, Any] | None) -> datetime | None:
    """Return a sourced, unambiguous submission time; never a purchase window.

    Multiple lots may have different dates. Retain those dates in the reading,
    but do not choose a project-wide deadline until their scope is resolved.
    """
    if not reading or reading.get('stale') or reading.get('status') not in {'complete', 'partial'}:
        return None
    if reading.get('conflicts'):
        return None
    milestones = reading.get('milestones')
    if not isinstance(milestones, list):
        return None
    dates = set()
    for item in milestones:
        if not isinstance(item, Mapping) or item.get('kind') != 'submission' or not item.get('sources'):
            continue
        value = parse_timestamp(item.get('at'), deadline=True)
        if value is not None:
            dates.add(value)
    return next(iter(dates)) if len(dates) == 1 else None
