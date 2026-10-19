"""Persistence boundary for a user's procurement follow-up desk.

The follow-up package deliberately sits beside the screening service.  It
accepts a sqlite connection and a project id, and does not import the service
or an AI provider.  ``planning.generate_plan`` is pure and only turns a saved
procurement explanation into a proposal; ``adopt_plan`` is the explicit point
where that proposal becomes user tasks.
"""

from .errors import FollowupConflictError, FollowupError, FollowupNotFoundError, FollowupValidationError
from .planning import generate_plan
from .repository import FollowupRepository
from .schema import ensure_schema
from .service import (
    adopt_plan,
    get_followup,
    merge_followup,
    note_material_change,
    save_followup,
    update_material,
    update_task,
)

__all__ = [
    "FollowupConflictError",
    "FollowupError",
    "FollowupNotFoundError",
    "FollowupRepository",
    "FollowupValidationError",
    "adopt_plan",
    "ensure_schema",
    "generate_plan",
    "get_followup",
    "merge_followup",
    "note_material_change",
    "save_followup",
    "update_material",
    "update_task",
]
