"""Readiness scoring: Met = 1, Partially met = 0.5, everything else = 0."""

from __future__ import annotations

from .schemas import Assessment

STATUS_ORDER = ["Met", "Partially met", "Not met", "No evidence"]
STATUS_WEIGHT = {"Met": 1.0, "Partially met": 0.5, "Not met": 0.0, "No evidence": 0.0}


def _tally(items: list[Assessment]) -> dict:
    counts = {s: sum(1 for a in items if a.status == s) for s in STATUS_ORDER}
    total = len(items)
    score = sum(STATUS_WEIGHT[a.status] for a in items) / total if total else 0.0
    return {"total": total, "counts": counts, "score": score}


def summarize(assessments: list[Assessment]) -> dict:
    """Overall and per-domain readiness (domains keep first-seen order)."""
    domains: dict[str, list[Assessment]] = {}
    for a in assessments:
        domains.setdefault(a.domain, []).append(a)
    return {
        "overall": _tally(assessments),
        "domains": [{"domain": d, **_tally(items)} for d, items in domains.items()],
        "needs_review": sum(1 for a in assessments if a.needs_review),
    }
