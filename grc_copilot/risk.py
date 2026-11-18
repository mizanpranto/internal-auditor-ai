"""Turn control gaps into a scored risk register (likelihood x impact, 1-25)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from .frameworks import natural_key
from .schemas import Assessment, Control, RiskItem

# How likely the gap is to hurt you, judged from the assessment outcome.
LIKELIHOOD = {"Not met": 5, "No evidence": 4, "Partially met": 3}
DUE_DAYS = {"Critical": 30, "High": 60, "Medium": 90, "Low": 180}


def level_for(score: int) -> str:
    if score >= 20:
        return "Critical"
    if score >= 12:
        return "High"
    if score >= 6:
        return "Medium"
    return "Low"


def gap_text(a: Assessment, c: Control) -> str:
    if a.missing:
        return a.missing
    if a.status == "No evidence":
        return f"No documented evidence found. Typical evidence: {c.evidence_hint}"
    return a.reasoning or c.summary


def build_risk_register(assessments: list[Assessment], controls: list[Control], today: Optional[date] = None) -> list[RiskItem]:
    today = today or date.today()
    by_id = {c.id: c for c in controls}
    items: list[RiskItem] = []
    for a in assessments:
        if a.status == "Met":
            continue
        c = by_id[a.control_id]
        likelihood = LIKELIHOOD[a.status]
        score = likelihood * c.impact
        level = level_for(score)
        items.append(
            RiskItem(
                risk_id="",
                control_id=a.control_id,
                domain=a.domain,
                title=a.title,
                status=a.status,
                gap=gap_text(a, c),
                likelihood=likelihood,
                impact=c.impact,
                score=score,
                level=level,
                due_date=(today + timedelta(days=DUE_DAYS[level])).isoformat(),
            )
        )
    items.sort(key=lambda r: (-r.score, natural_key(r.control_id)))
    for i, r in enumerate(items, start=1):
        r.risk_id = f"R-{i:03d}"
    return items
