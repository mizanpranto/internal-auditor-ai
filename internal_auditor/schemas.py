"""Pydantic models shared by every stage of the pipeline."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

ModelStatus = Literal["Met", "Partially met", "Not met", "No evidence"]  # what the language model may answer
# A reviewer may also mark a control "Not applicable" (with a written justification). The model never decides this.
Status = Literal["Met", "Partially met", "Not met", "No evidence", "Not applicable"]


class Control(BaseModel):
    id: str
    domain: str
    title: str
    summary: str
    evidence_hint: str = ""
    impact: int = 3  # 1-5, generic default used by the risk scorer
    requirement: str = ""  # HIPAA only: "Required", "Addressable" or "Standard"


class Chunk(BaseModel):
    chunk_id: int
    doc: str
    page: Optional[int] = None
    text: str


class LLMAssessment(BaseModel):
    """What the model must answer with.

    Field order is deliberate: the model has to pick a source and copy a quote
    BEFORE it is allowed to choose a verdict, which keeps an 8B model grounded.
    """

    chunk_id: int = Field(description="chunk_id of the excerpt holding the evidence, or -1 if there is none")
    quote: str = Field(description="1-2 sentences copied word for word from that excerpt, or empty string")
    status: ModelStatus
    reasoning: str = Field(description="At most two sentences explaining the verdict")
    missing: str = Field(description="What is still missing to fully meet the control, or empty string")


class Assessment(BaseModel):
    """Final, citation-verified result for one control."""

    control_id: str
    domain: str
    title: str
    status: Status
    quote: str = ""
    source_doc: str = ""
    page: Optional[int] = None
    chunk_id: Optional[int] = None
    reasoning: str = ""
    missing: str = ""
    citation_verified: bool = False
    needs_review: bool = False
    note: str = ""
    # human-in-the-loop: an AI verdict is only a draft until a reviewer approves it
    review_status: Literal["Draft", "Approved", "Rejected"] = "Draft"
    ai_status: Optional[Status] = None  # the model's original verdict when a reviewer changed it
    reviewer: str = ""
    reviewer_note: str = ""
    reviewed_at: str = ""
    evidence_sha: str = ""  # SHA-256 of the cited document when a reviewer approved this verdict


class Remediation(BaseModel):
    action_plan: str = Field(description="2-4 sentences: the concrete steps to close the gap")
    owner_role: str = Field(description="Job role that should own the fix, e.g. 'IT Operations Manager'")
    policy_snippet: str = Field(description="A short draft policy statement (2-3 sentences)")
    effort: Literal["Low", "Medium", "High"]


class RiskItem(BaseModel):
    risk_id: str
    control_id: str
    domain: str
    title: str
    status: Status
    gap: str
    likelihood: int
    impact: int
    score: int
    level: Literal["Critical", "High", "Medium", "Low"]
    action_plan: str = ""
    owner_role: str = ""
    effort: str = ""
    due_date: str = ""
    policy_snippet: str = ""
    ai_drafted: bool = False
    treatment: str = "Open"
