"""Persistent workspace: evidence files, assessments, review decisions, risk treatment.

Everything lives in one folder (default ./workspace) so a session survives
restarts and nothing is stored outside the machine.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict, fields
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .config import ROOT, Settings
from .frameworks import DEFAULT_FRAMEWORK
from .schemas import Assessment, Control, RiskItem
from .risk import build_risk_register
from .scoring import STATUS_WEIGHT, summarize

DEFAULT_ROOT = ROOT / "workspace"
RISK_STATUSES = ["Open", "In progress", "Closed", "Accepted"]
PERSISTED_SETTINGS = ["ollama_url", "llm_model", "embed_model", "num_ctx", "temperature", "think", "top_k"]
PER_FRAMEWORK = ("assessments", "risks", "impact", "demo")  # state that belongs to one framework
FRESH = {"assessments": {}, "risks": {}, "impact": {}, "demo": False}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def safe_name(name: str) -> str:
    """Strip any path component from an uploaded file name."""
    base = Path(name.replace("\\", "/")).name.strip().lstrip(".")
    return base or "document"


class Workspace:
    def __init__(self, root: Optional[str | Path] = None):
        self.root = Path(root or os.getenv("COPILOT_WORKSPACE", DEFAULT_ROOT))
        self.evidence_dir = self.root / "evidence"
        self.index_dir = self.root / "index"
        self.state_path = self.root / "state.json"
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.state: dict = {"assessments": {}, "risks": {}, "activity": [], "settings": {}, "demo": False}
        self.load()

    # ------------------------------------------------------------ persistence
    def load(self) -> None:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.state.update(data)
            except (json.JSONDecodeError, OSError):
                backup = self.state_path.with_suffix(".corrupt.json")
                shutil.copy(self.state_path, backup)  # keep the broken file, start clean

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=1)
        os.replace(tmp, self.state_path)  # atomic: a crash never leaves half a file

    def log(self, text: str) -> None:
        self.state["activity"].insert(0, {"ts": _now(), "text": text})
        del self.state["activity"][60:]
        self.save()

    # --------------------------------------------------------------- settings
    def load_settings(self) -> Settings:
        s = Settings()
        for k, v in self.state.get("settings", {}).items():
            if k in PERSISTED_SETTINGS:
                setattr(s, k, v)
        return s

    def save_settings(self, s: Settings, reviewer: Optional[str] = None) -> None:
        keep = self.state.get("settings", {}).get("reviewer", "")
        self.state["settings"] = {k: getattr(s, k) for k in PERSISTED_SETTINGS}
        self.state["settings"]["reviewer"] = keep if reviewer is None else reviewer.strip()
        self.save()

    @property
    def reviewer(self) -> str:
        return self.state.get("settings", {}).get("reviewer", "")

    # --------------------------------------------------------------- evidence
    def list_evidence(self) -> list[dict]:
        out = []
        for p in sorted(self.evidence_dir.rglob("*")):
            if p.is_file():
                st = p.stat()
                out.append({"name": str(p.relative_to(self.evidence_dir)), "size": st.st_size,
                            "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
        return out

    def add_evidence(self, name: str, data: bytes) -> str:
        name = safe_name(name)
        target = self.evidence_dir / name
        stem, suffix, n = target.stem, target.suffix, 1
        while target.exists() and target.read_bytes() != data:
            n += 1
            target = self.evidence_dir / f"{stem}-{n}{suffix}"
        target.write_bytes(data)
        self.log(f"Added evidence document {target.name}")
        return target.name

    def remove_evidence(self, name: str) -> None:
        target = (self.evidence_dir / safe_name(name))
        if target.is_file():
            target.unlink()
            self.log(f"Removed evidence document {target.name}")

    # ------------------------------------------------------------ assessments
    def assessments(self) -> dict[str, Assessment]:
        return {cid: Assessment.model_validate(a) for cid, a in self.state["assessments"].items()}

    def get(self, control_id: str) -> Optional[Assessment]:
        a = self.state["assessments"].get(control_id)
        return Assessment.model_validate(a) if a else None

    def put(self, a: Assessment, *, log: bool = False) -> None:
        self.state["assessments"][a.control_id] = a.model_dump()
        if log:
            self.log(f"Assessed {a.control_id} {a.title}: {a.status}")
        else:
            self.save()

    def put_many(self, items: list[Assessment]) -> None:
        for a in items:
            self.state["assessments"][a.control_id] = a.model_dump()
        self.save()

    def review(self, control_id: str, action: str, reviewer: str, note: str = "", new_status: Optional[str] = None) -> Assessment:
        """action: approve | override | reject | reopen"""
        a = self.get(control_id)
        if a is None:
            raise KeyError(control_id)
        if action == "approve":
            a.review_status = "Approved"
        elif action == "override":
            if new_status not in STATUS_WEIGHT:
                raise ValueError(f"Unknown status {new_status!r}")
            if new_status in ("Met", "Partially met") and new_status != a.status and not a.citation_verified and not note.strip():
                raise ValueError("Add a reviewer note saying which evidence you checked. "
                                 "A control cannot be rated Met or Partially met without a verified quote or a note.")
            if new_status != a.status:
                a.ai_status = a.ai_status or a.status
                a.status = new_status  # type: ignore[assignment]
            a.review_status = "Approved"
        elif action == "reject":
            a.review_status = "Rejected"
        elif action == "reopen":
            a.review_status, a.reviewer, a.reviewer_note, a.reviewed_at = "Draft", "", "", ""
            self.put(a)
            return a
        else:
            raise ValueError(f"Unknown review action {action!r}")
        a.reviewer, a.reviewer_note, a.reviewed_at = reviewer or "Reviewer", note.strip(), _now()
        self.put(a)
        self.log(f"{a.reviewer} {'approved' if a.review_status == 'Approved' else 'rejected'} {a.control_id} {a.title} ({a.status})")
        return a

    def approve_all_verified(self, reviewer: str) -> int:
        """Bulk-approve drafts whose quote the citation lock verified. Unverified ones stay for a human."""
        n = 0
        for cid, raw in list(self.state["assessments"].items()):
            a = Assessment.model_validate(raw)
            if a.review_status == "Draft" and a.citation_verified and not a.needs_review:
                a.review_status, a.reviewer, a.reviewed_at = "Approved", reviewer or "Reviewer", _now()
                self.state["assessments"][cid] = a.model_dump()
                n += 1
        if n:
            self.log(f"{reviewer or 'Reviewer'} bulk-approved {n} citation-verified assessments")
        return n

    def clear_assessments(self) -> None:
        self.state["assessments"], self.state["risks"], self.state["demo"] = {}, {}, False
        self.log("Cleared all assessments")

    # ------------------------------------------------------------- frameworks
    @property
    def framework_id(self) -> str:
        return self.state.get("framework", DEFAULT_FRAMEWORK)

    def switch_framework(self, fid: str) -> None:
        """Make `fid` the active framework. Each framework keeps its own assessments,
        risk treatment and impact weights, so switching back never loses work."""
        cur = self.framework_id
        if fid == cur:
            return
        stash = self.state.setdefault("stash", {})
        stash[cur] = {k: self.state.get(k, FRESH[k]) for k in PER_FRAMEWORK}
        saved = stash.pop(fid, None) or {}
        for k in PER_FRAMEWORK:
            self.state[k] = saved.get(k, FRESH[k] if not isinstance(FRESH[k], dict) else {})
        self.state["framework"] = fid
        self.log(f"Switched framework to {fid}")

    def forget_framework(self, fid: str) -> None:
        """Drop the saved work of a framework that is not active (used when a custom file is replaced)."""
        self.state.get("stash", {}).pop(fid, None)
        self.save()

    # ------------------------------------------------------------------ risks
    def approved(self) -> list[Assessment]:
        return [a for a in self.assessments().values() if a.review_status == "Approved"]

    def risk_register(self, controls: list[Control], include_drafts: bool = False) -> list[RiskItem]:
        by_id = {c.id: c for c in controls}
        pool = [a for a in self.assessments().values()
                if a.control_id in by_id and (a.review_status == "Approved" or (include_drafts and a.review_status == "Draft"))]
        risks = build_risk_register(pool, controls)
        for r in risks:
            r.risk_id = f"R-{r.control_id}"  # stable id: does not shift when other risks change
            saved = self.state["risks"].get(r.control_id, {})
            for k in ("action_plan", "owner_role", "effort", "due_date", "policy_snippet", "ai_drafted"):
                if k in saved:
                    setattr(r, k, saved[k])
            r.treatment = saved.get("treatment", "Open")
        return risks

    def risk_status(self, control_id: str) -> str:
        return self.state["risks"].get(control_id, {}).get("treatment", "Open")

    def impact_overrides(self) -> dict:
        return self.state.setdefault("impact", {})

    def update_risk(self, control_id: str, **fields_: object) -> None:
        cur = self.state["risks"].setdefault(control_id, {})
        cur.update({k: v for k, v in fields_.items() if v is not None})
        self.save()

    def open_risks(self, controls: list[Control]) -> list[RiskItem]:
        return [r for r in self.risk_register(controls) if self.risk_status(r.control_id) in ("Open", "In progress")]

    # --------------------------------------------------------------- summaries
    def readiness(self) -> Optional[float]:
        """Average score of reviewer-approved assessments (None when nothing is approved yet)."""
        ap = self.approved()
        return summarize(ap)["overall"]["score"] if ap else None

    def counts(self) -> dict:
        a = list(self.assessments().values())
        return {
            "assessed": len(a),
            "approved": sum(1 for x in a if x.review_status == "Approved"),
            "draft": sum(1 for x in a if x.review_status == "Draft"),
            "rejected": sum(1 for x in a if x.review_status == "Rejected"),
            "needs_review": sum(1 for x in a if x.needs_review and x.review_status == "Draft"),
        }

    # -------------------------------------------------------- audit report details
    @property
    def logo_path(self) -> Path:
        return self.root / "report_logo.png"

    def report_info(self) -> dict:
        return dict(self.state.get("report_info", {}))

    def save_report_info(self, info: dict) -> None:
        self.state["report_info"] = {k: str(v) for k, v in info.items()}
        self.save()

    def save_logo(self, data: Optional[bytes]) -> None:
        """Store a company logo (any common image, converted to PNG). None removes it."""
        if not data:
            self.logo_path.unlink(missing_ok=True)
            return
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.thumbnail((900, 400))
        img.convert("RGBA").save(self.logo_path, "PNG")

    def logo(self) -> Optional[bytes]:
        return self.logo_path.read_bytes() if self.logo_path.exists() else None

    def reset(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.__init__(self.root)  # type: ignore[misc]
