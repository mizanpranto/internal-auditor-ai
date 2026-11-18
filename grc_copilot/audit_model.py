"""Shared content for the internal-audit report. The PDF and DOCX writers both render this one model,
so the two formats always say the same thing."""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from typing import Optional

from .schemas import Assessment, Control, RiskItem
from .scoring import STATUS_ORDER, summarize

AGENT_NAME = "Pranto's GRC Proto-type"
CLASSIFICATIONS = ["Confidential", "Internal use only", "Restricted", "Public"]

NAVY, TEAL, GREY = "1F3864", "2E75B6", "595959"
STATUS_HEX = {"Met": "2E7D32", "Partially met": "E9A100", "Not met": "C62828", "No evidence": "757575"}
LEVEL_HEX = {"Critical": "B71C1C", "High": "E65100", "Medium": "F9A825", "Low": "2E7D32"}
RATING_HEX = {"Satisfactory": "2E7D32", "Needs improvement": "E9A100", "Unsatisfactory": "C62828"}

CAVEAT = (
    "This report is based on a review of the documents supplied. It tests whether those documents describe each "
    "control and does not test whether controls operate effectively. Draft verdicts were produced by a local "
    "language model and count only after a named reviewer approved them. A verdict of Met, Partially met or "
    "Not met requires a quotation that was verified to exist in the supplied documents; otherwise the control is "
    "reported as No evidence. Remediation text drafted by the model is a starting point for the control owner."
)


@dataclass
class ReportInfo:
    """Everything the cover page and document-control table need. All fields are optional text."""

    company: str = ""
    scope: str = ""
    report_title: str = ""
    engagement_ref: str = ""
    assessor_name: str = ""
    assessor_title: str = ""
    approver_name: str = ""
    approver_title: str = ""
    auditee_contact: str = ""
    assessment_period: str = ""
    report_date: str = ""
    version: str = "1.0"
    classification: str = "Confidential"
    distribution: str = ""
    developed_by: str = "Pranto"

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "ReportInfo":
        names = {f.name for f in fields(cls)}
        return cls(**{k: str(v) for k, v in (d or {}).items() if k in names and v is not None})

    def to_dict(self) -> dict:
        return asdict(self)


def rating_for(score: float) -> tuple[str, str]:
    """Overall rating and the sentence that explains it. Thresholds are printed in the report."""
    if score >= 0.85:
        return "Satisfactory", "Most controls are documented with verified evidence. Remaining gaps are minor."
    if score >= 0.60:
        return "Needs improvement", "Many controls are documented, but gaps need management attention."
    return "Unsatisfactory", "Significant gaps: many controls are not documented or are missing evidence."


@dataclass
class Finding:
    ref: str
    control_id: str
    title: str
    domain: str
    status: str
    level: str
    score: int
    criteria: str
    condition: str
    quote: str
    source: str
    verified: bool
    gap: str
    recommendation: str
    owner: str
    due: str
    treatment: str
    reviewer: str
    reviewer_note: str
    ai_drafted: bool


@dataclass
class Model:
    info: ReportInfo
    framework: str
    framework_note: str
    generated: str
    llm: str
    retrieval: str
    summary: dict
    total_controls: int
    approved: int
    drafts_included: int
    not_covered: int
    rating: str
    rating_text: str
    score: float
    findings: list[Finding]
    met: list[Assessment]
    risk_counts: dict
    docs: list[dict]
    results: list[Assessment]
    reviewers: list[str]
    key_messages: list[str]
    cover_title: str
    logo: Optional[bytes] = None
    charts: dict = field(default_factory=dict)


def _first_sentence(text: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def build_model(info: ReportInfo, meta: dict, pool: list[Assessment], risks: list[RiskItem], docs: list[dict],
                controls: list[Control], *, llm: str = "", retrieval: str = "", logo: Optional[bytes] = None,
                with_charts: bool = True) -> Model:
    by_id = {c.id: c for c in controls}
    summary = summarize(pool)
    overall = summary["overall"]
    rating, rating_text = rating_for(overall["score"]) if pool else ("Not rated", "No approved assessments yet.")
    risk_by_ctrl = {r.control_id: r for r in risks}
    asm = {a.control_id: a for a in pool}

    findings: list[Finding] = []
    for i, r in enumerate(sorted(risks, key=lambda r: (-r.score, r.control_id)), 1):
        a = asm.get(r.control_id)
        if a is None:
            continue
        c = by_id.get(r.control_id)
        gap = a.missing or r.gap
        findings.append(Finding(
            ref=f"F-{i:02d}", control_id=r.control_id, title=r.title, domain=r.domain, status=a.status, level=r.level,
            score=r.score, criteria=c.summary if c else "", condition=a.reasoning or r.gap, quote=a.quote,
            source=(a.source_doc + (f", page {a.page}" if a.page else "")) if a.source_doc else "",
            verified=a.citation_verified, gap=gap,
            recommendation=r.action_plan or f"Not yet drafted. Define actions that close this gap: {_first_sentence(gap)}",
            owner=r.owner_role or "To be assigned", due=r.due_date, treatment=r.treatment,
            reviewer=a.reviewer, reviewer_note=a.reviewer_note, ai_drafted=r.ai_drafted))

    met = [a for a in pool if a.status == "Met"]
    risk_counts = {lvl: sum(1 for r in risks if r.level == lvl) for lvl in ("Critical", "High", "Medium", "Low")}
    drafts = sum(1 for a in pool if a.review_status == "Draft")
    total = len(controls)
    not_covered = max(total - len(pool), 0)
    reviewers = sorted({a.reviewer for a in pool if a.reviewer and a.reviewer != "Reference demo"})
    framework = meta.get("framework", "")

    msgs: list[str] = []
    if pool:
        msgs.append(f"{len(pool)} of {total} controls in {framework} were assessed and are included "
                    f"({len(pool) / total:.0%} coverage). Overall readiness is {overall['score']:.0%}, rated {rating}.")
        c = overall["counts"]
        msgs.append(f"{c['Met']} controls are Met, {c['Partially met']} Partially met, {c['Not met']} Not met and "
                    f"{c['No evidence']} have no supporting evidence in the documents provided.")
        weakest = sorted(summary["domains"], key=lambda d: d["score"])[:2]
        if len(summary["domains"]) > 1 and weakest[0]["score"] < 1:
            msgs.append("Lowest readiness: " + "; ".join(f"{d['domain']} ({d['score']:.0%})" for d in weakest) + ".")
        if findings:
            top = findings[:3]
            msgs.append("Highest-scoring risks: " + "; ".join(f"{f.control_id} {f.title} ({f.level}, {f.score})" for f in top) + ".")
        else:
            msgs.append("No gaps were found in the controls assessed.")
        if summary["needs_review"]:
            msgs.append(f"{summary['needs_review']} findings were flagged because the model's quotation could not be "
                        "verified; they are reported as No evidence until a reviewer confirms the source.")
    if not_covered:
        msgs.append(f"{not_covered} controls were not assessed or not yet approved. They are excluded from the rating "
                    "and from this report's findings.")
    if drafts:
        msgs.append(f"{drafts} of the assessments included here are unreviewed AI drafts.")

    m = Model(
        info=info, framework=framework, framework_note=meta.get("note", ""), generated=meta.get("generated", ""),
        llm=llm, retrieval=retrieval, summary=summary, total_controls=total, approved=len(pool) - drafts,
        drafts_included=drafts, not_covered=not_covered, rating=rating, rating_text=rating_text,
        score=overall["score"], findings=findings, met=met, risk_counts=risk_counts, docs=docs, results=pool,
        reviewers=reviewers, key_messages=msgs,
        cover_title=info.report_title.strip() or f"{meta.get('short', framework)} Compliance Readiness Assessment",
        logo=logo)
    if with_charts and pool:
        m.charts = make_charts(m)
    return m


# ------------------------------------------------------------------ charts
def make_charts(m: Model) -> dict[str, bytes]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.spines.top": False,
                         "axes.spines.right": False})
    out: dict[str, bytes] = {}
    counts = m.summary["overall"]["counts"]

    fig, ax = plt.subplots(figsize=(3.4, 3.0))
    vals = [counts[s] for s in STATUS_ORDER]
    wedges, _ = ax.pie([v or 0 for v in vals], colors=["#" + STATUS_HEX[s] for s in STATUS_ORDER], startangle=90,
                       counterclock=False, wedgeprops={"width": 0.38, "edgecolor": "white", "linewidth": 1.5})
    ax.text(0, 0.07, f"{m.score:.0%}", ha="center", va="center", fontsize=20, fontweight="bold", color="#" + NAVY)
    ax.text(0, -0.22, "readiness", ha="center", va="center", fontsize=8.5, color="#" + GREY)
    ax.legend(wedges, [f"{s}: {v}" for s, v in zip(STATUS_ORDER, vals)], loc="upper center",
              bbox_to_anchor=(0.5, 0.02), ncol=2, frameon=False, fontsize=8, handlelength=1, columnspacing=1.2)
    ax.set_aspect("equal")
    out["donut"] = _png(fig)

    doms = m.summary["domains"]
    h = max(1.8, 0.5 * len(doms) + 0.9)
    fig, ax = plt.subplots(figsize=(6.4, h))
    names = [_wrap(d["domain"], 34) for d in doms][::-1]
    left = [0] * len(doms)
    for s in STATUS_ORDER:
        vals = [d["counts"][s] for d in doms][::-1]
        ax.barh(names, vals, left=left, color="#" + STATUS_HEX[s], label=s, edgecolor="white", height=0.62)
        left = [a + b for a, b in zip(left, vals)]
    for y, d in enumerate(doms[::-1]):
        ax.text(left[y] + 0.15, y, f"{d['score']:.0%}", va="center", fontsize=8.5, color="#" + NAVY, fontweight="bold")
    ax.set_xlabel("Controls assessed")
    ax.xaxis.get_major_locator().set_params(integer=True)
    ax.legend(ncol=4, frameon=False, fontsize=8, loc="lower center", bbox_to_anchor=(0.4, 1.0), handlelength=1)
    ax.set_xlim(0, max(left) * 1.12 if left else 1)
    out["domains"] = _png(fig)
    return out


def _wrap(text: str, width: int) -> str:
    import textwrap
    text = re.sub(r"[\x00-\x1f]", "", text)
    return "\n".join(textwrap.wrap(text, width)) or text


def _png(fig) -> bytes:
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


# ------------------------------------------------------------ static text
SCOPE_TEXT = (
    "Assess whether the organisation's policies, procedures and supporting records describe each control in the "
    "selected framework, identify gaps, and rank them by risk so management can prioritise remediation."
)

METHOD_STEPS = [
    "Evidence collection: the documents listed in Appendix A were supplied by the organisation and indexed locally. "
    "No data left the assessor's computer.",
    "Retrieval: for each control the most relevant passages were found using a hybrid of semantic and keyword search.",
    "AI drafting: a local language model proposed a verdict, quoting the passage it relied on.",
    "Citation lock: a verdict of Met, Partially met or Not met was kept only if its quotation was found in the "
    "source document. Otherwise it was downgraded to No evidence and flagged.",
    "Human review: a named reviewer approved, rejected or changed each verdict. Only approved assessments are "
    "scored. Upgrading an unverified verdict required a written note.",
    "Risk scoring: each gap was scored as likelihood x impact (1 to 25) and given a target remediation date.",
]

STATUS_DEFS = [
    ("Met", "The documents clearly describe the control, supported by a verified quotation."),
    ("Partially met", "The control is described but incompletely, or it lacks required elements."),
    ("Not met", "The documents contradict the control or show it is not in place."),
    ("No evidence", "No supporting passage was found, or the quotation could not be verified."),
]
RATING_DEFS = [
    ("Satisfactory", "Readiness of 85% or more", "Controls are generally documented. Fix remaining gaps in the normal cycle."),
    ("Needs improvement", "Readiness of 60% to 84%", "Notable gaps. Management should agree and track an action plan."),
    ("Unsatisfactory", "Readiness below 60%", "Significant gaps. Prompt senior management attention is recommended."),
]
RISK_DEFS = [
    ("Critical", "Score 20 to 25", "Remediate within 30 days"),
    ("High", "Score 12 to 19", "Remediate within 60 days"),
    ("Medium", "Score 6 to 11", "Remediate within 90 days"),
    ("Low", "Score 1 to 5", "Remediate within 180 days"),
]
SCORING_NOTE = ("Readiness = (Met x 1 + Partially met x 0.5) / controls assessed. Risk score = likelihood x control "
                "impact weight, each on a 1 to 5 scale.")
LIMITS = [
    "Document review only. Interviews, sampling, configuration checks and testing of operating effectiveness were not performed.",
    "A verified quotation proves that the text exists in the documents, not that the control works in practice.",
    "Scanned documents without a text layer were not readable by the tool.",
    "Framework summaries in the tool are paraphrases, not the text of the standard. Refer to the licensed standard.",
    "This report is not a certification audit and does not replace the opinion of an accredited auditor.",
]

SECTIONS = ["Document control", "Executive summary", "Scope, approach and rating scales", "Results by domain",
            "Detailed findings", "Remediation action plan", "Appendices"]


def build_reports(info: ReportInfo, meta: dict, pool: list[Assessment], risks: list[RiskItem], docs: list[dict],
                  controls: list[Control], **kw) -> tuple[bytes, bytes]:
    """Return (pdf_bytes, docx_bytes) rendered from one shared model."""
    from .audit_docx import write_docx
    from .audit_pdf import write_pdf
    if not info.report_date:
        info = ReportInfo.from_dict({**info.to_dict(), "report_date": date.today().strftime("%d %B %Y")})
    m = build_model(info, meta, pool, risks, docs, controls, **kw)
    return write_pdf(m), write_docx(m)
