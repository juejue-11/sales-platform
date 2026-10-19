"""Trusted agent tool names and execution budgets."""
from ..research.project_research import RESEARCH_TOOLS

DEFAULT_AGENT_LIMITS = {
    "max_turns": 6,
    "max_tool_calls": 12,
    "timeout_ms": 300000,
    "max_output_tokens": 16384,
}

TRUSTED_AGENT_TOOLS = {"read_project_records", "read_company_capability", "read_related_records",
                       "fetch_project_documents", *RESEARCH_TOOLS}
