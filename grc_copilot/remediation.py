"""Draft a fix for each gap. Uses the LLM, falls back to a deterministic template."""

from __future__ import annotations

from .schemas import Control, Remediation, RiskItem

SYSTEM_PROMPT = """You are an information security consultant writing remediation advice for an ISO 27001 gap.
Be concrete and practical. Do not invent facts about the company; use placeholders such as [Company] or [Owner]
where specifics are unknown. Keep every field short. Answer only with the requested JSON."""

OWNER_BY_DOMAIN = {
    "Organizational controls": "CISO / ISMS Manager",
    "People controls": "HR Manager",
    "Physical controls": "Facilities Manager",
    "Technological controls": "IT / Security Operations Lead",
}


def template_remediation(control: Control, risk: RiskItem) -> Remediation:
    return Remediation(
        action_plan=(
            f"Define and approve a documented approach for '{control.title}'. {control.summary} "
            f"Then collect operating evidence such as: {control.evidence_hint}."
        ),
        owner_role=OWNER_BY_DOMAIN.get(control.domain, "ISMS Manager"),
        policy_snippet=f"[Draft - review before use] {control.title}: {control.summary}",
        effort="Low" if risk.status == "Partially met" else "Medium",
    )


def draft_remediation(llm, control: Control, risk: RiskItem) -> tuple[Remediation, bool]:
    """Returns (remediation, ai_drafted). Never raises: falls back to the template."""
    user = (
        f"Control {control.id} - {control.title}\n"
        f"Requirement: {control.summary}\n"
        f"Assessment result: {risk.status}\n"
        f"Gap found: {risk.gap}\n\n"
        "Give: action_plan (2-4 sentences), owner_role (a job title), "
        "policy_snippet (a 2-3 sentence draft policy statement) and effort (Low, Medium or High)."
    )
    try:
        raw = llm.chat_json(SYSTEM_PROMPT, user, Remediation.model_json_schema(), task="remediate")
        return Remediation.model_validate(raw), True
    except Exception:  # noqa: BLE001
        return template_remediation(control, risk), False
