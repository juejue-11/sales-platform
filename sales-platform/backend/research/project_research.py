"""Public compatibility surface for project-scoped research.

The implementations live in modules named for their responsibilities:
configuration, network access, parsing, SQLite evidence storage, source
operations, and session orchestration.  Keep this module small so callers can
use the stable research interface without depending on those internals.
"""
from __future__ import annotations

from .config import (
    RESEARCH_POLICY_VERSION,
    RESEARCH_RELATIONS,
    RESEARCH_TOOLS,
    ResearchError,
    configuration_fingerprint,
    validate_research_arguments,
)
from .session import ResearchSession
from .storage import (
    current_dependencies,
    list_sources,
    merge_project_research,
    read_saved_source,
)

__all__ = [
    "RESEARCH_POLICY_VERSION",
    "RESEARCH_RELATIONS",
    "RESEARCH_TOOLS",
    "ResearchError",
    "ResearchSession",
    "configuration_fingerprint",
    "current_dependencies",
    "list_sources",
    "merge_project_research",
    "read_saved_source",
    "validate_research_arguments",
]
