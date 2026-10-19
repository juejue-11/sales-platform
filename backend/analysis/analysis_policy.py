from __future__ import annotations

from typing import Any


ANALYSIS_POLICY_VERSION = "market_agent_v2"
DEFAULT_MARKET_GOAL = (
    "围绕矿山设备经销商和生产商的市场开发需求，调查项目事实，识别明确需求与有依据的潜在机会，"
    "说明参与条件、关键缺口和下一步行动，并提供可核对的来源。"
)
COMPANY_MATERIAL_FIELDS = ("scope_description", "supported_products", "capabilities", "solution_examples")
COMPANY_MATCH_STATES = {"not_evaluated", "partial", "assessed"}
CUSTOMER_PREFERENCE_FIELDS = (
    "customer_role", "equipment_categories", "region_preferences", "mineral_types", "demand_focus",
)


def analysis_context(business: dict[str, Any]) -> dict[str, Any]:
    """Separate customer interests from self-reported company material once."""
    profile = {key: str(business.get(key) or "").strip()
               for key in ("company_name", *COMPANY_MATERIAL_FIELDS)}
    keywords = business.get("target_keywords", [])
    preferences = {
        "target_keywords": [str(value).strip() for value in keywords if str(value).strip()]
        if isinstance(keywords, list) else [],
        **{key: str(business.get(key) or "").strip() for key in CUSTOMER_PREFERENCE_FIELDS},
    }
    return {
        "analysis_goal": DEFAULT_MARKET_GOAL,
        "analysis_semantic_version": ANALYSIS_POLICY_VERSION,
        "customer_preferences": preferences,
        "company_capability_card": profile,
        # Material availability is not proof of capability or qualification.
        "company_profile_present": any(profile[key] for key in COMPANY_MATERIAL_FIELDS),
    }


def current_analysis_meta(value: Any) -> bool:
    return (isinstance(value, dict) and value.get("semantic_version") == ANALYSIS_POLICY_VERSION
            and value.get("decision_scope") == "market"
            and isinstance(value.get("company_match"), str)
            and value["company_match"] in COMPANY_MATCH_STATES)


def result_analysis_meta(value: Any, business: dict[str, Any]) -> dict[str, str]:
    """Host owns the version/scope; the model may describe matching coverage."""
    if value is not None and not isinstance(value, dict):
        raise ValueError("invalid analysis_meta")
    state = (value or {}).get("company_match", "not_evaluated")
    if not isinstance(state, str) or state not in COMPANY_MATCH_STATES:
        raise ValueError("invalid company_match")
    if state != "not_evaluated" and not analysis_context(business)["company_profile_present"]:
        raise ValueError("company matching requires substantive company material")
    return {"semantic_version": ANALYSIS_POLICY_VERSION, "decision_scope": "market", "company_match": state}
