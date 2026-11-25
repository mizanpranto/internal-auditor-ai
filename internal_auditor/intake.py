"""Evidence intake: lets people outside the assessment team answer a control question and upload evidence.

How it stays safe and simple:
* The public form is a separate small app (intake_app.py). It never shows assessments, risks or other people's
  submissions, and it can only *create* new submission folders.
* The assessor's app is the only place that reads, accepts or rejects submissions. Nothing a contributor writes is
  indexed or scored until the assessor accepts it.
* Form answers are self-attested. Accepted answers become a clearly labelled evidence document, so the citation
  lock can quote them and the report can say how much of the evidence is self-attested.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .workspace import safe_name

INDEXABLE = {".pdf", ".docx", ".txt", ".md"}
IMAGES = {".png", ".jpg", ".jpeg"}
ALLOWED = INDEXABLE | IMAGES
MAX_FILES = 10
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT = 4000
STATUSES = ["Fully in place", "Partly in place", "Planned", "Not in place", "Not applicable"]
SUB_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")

_PHI = [
    ("a Social Security number pattern", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("a medical record number", re.compile(r"\b(?:MRN|medical record (?:number|no\.?))\s*[:#]?\s*\d{4,}", re.I)),
    ("a date of birth", re.compile(r"\b(?:DOB|date of birth)\s*[:\-]?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", re.I)),
]


def phi_flags(text: str) -> list[str]:
    """Obvious patient-identifier patterns. A warning aid only: it cannot find every kind of PHI."""
    return [label for label, rx in _PHI if rx.search(text or "")]


def looks_like(ext: str, data: bytes) -> bool:
    """Check the first bytes match the file type, so a renamed program or script is not accepted as a document."""
    if ext == ".pdf":
        return data[:5] == b"%PDF-"
    if ext == ".docx":
        return data[:4] == b"PK\x03\x04"
    if ext == ".png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if ext in (".jpg", ".jpeg"):
        return data[:3] == b"\xff\xd8\xff"
    if ext in (".txt", ".md"):
        return b"\x00" not in data[:4096]
    return False


class IntakeError(ValueError):
    """A problem the contributor can fix (shown to them as-is)."""


@dataclass
class Submission:
    id: str
    created: str
    name: str
    email: str
    role: str
    department: str
    control_ids: list[str]
    request_id: str = ""
    status_claimed: str = ""
    description: str = ""
    owner: str = ""
    last_reviewed: str = ""
    frequency: str = ""
    exceptions: str = ""
    files: list[str] = field(default_factory=list)
    attested: bool = False
    phi_warnings: list[str] = field(default_factory=list)
    review_status: str = "Pending"  # Pending, Accepted, Rejected
    reviewer: str = ""
    reviewed_at: str = ""
    reviewer_comment: str = ""
    evidence_docs: list[str] = field(default_factory=list)


class Intake:
    def __init__(self, root: str | Path):
        self.dir = Path(root) / "intake"
        self.sub_dir = self.dir / "submissions"

    # ------------------------------------------------------------- settings
    def config(self) -> dict:
        try:
            return json.loads((self.dir / "config.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def set_access_code(self, code: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        cfg = self.config()
        cfg["access_code"] = code.strip()
        (self.dir / "config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")

    def code_required(self) -> bool:
        return bool(self.config().get("access_code"))

    def check_code(self, code: str) -> bool:
        want = self.config().get("access_code", "")
        return not want or hmac.compare_digest(want.encode(), (code or "").strip().encode())

    # ------------------------------------------------------------- requests
    def _req_path(self) -> Path:
        return self.dir / "requests.json"

    def requests(self) -> list[dict]:
        try:
            return json.loads(self._req_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

    def add_request(self, control_id: str, owner: str, due: str, note: str) -> dict:
        reqs = self.requests()
        req = {"id": "R" + secrets.token_hex(3), "control_id": control_id, "owner": owner.strip(), "due": due,
               "note": note.strip(), "created": datetime.now().strftime("%Y-%m-%d")}
        reqs.append(req)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._req_path().write_text(json.dumps(reqs, indent=1), encoding="utf-8")
        return req

    def remove_request(self, request_id: str) -> None:
        reqs = [r for r in self.requests() if r["id"] != request_id]
        self._req_path().write_text(json.dumps(reqs, indent=1), encoding="utf-8")

    def request_status(self, req: dict) -> str:
        """Open until a submission answers it; Received while pending; Closed once one is accepted."""
        mine = [s for s in self.submissions() if s.request_id == req["id"] or req["control_id"] in s.control_ids]
        if any(s.review_status == "Accepted" for s in mine):
            return "Closed"
        return "Received" if any(s.review_status == "Pending" for s in mine) else "Open"

    # ---------------------------------------------------------- submissions
    def submit(self, *, name: str, email: str, role: str, department: str, control_ids: list[str], request_id: str = "",
               status_claimed: str, description: str, owner: str = "", last_reviewed: str = "", frequency: str = "",
               exceptions: str = "", attested: bool, files: list[tuple[str, bytes]]) -> Submission:
        name, role, department = name.strip(), role.strip(), department.strip()
        if not (name and role and department):
            raise IntakeError("Please enter your name, role and department.")
        if not control_ids:
            raise IntakeError("Please choose the requirement your answer is about.")
        if status_claimed not in STATUSES:
            raise IntakeError("Please choose how far the requirement is in place.")
        if len(description.strip()) < 20:
            raise IntakeError("Please describe how this is done in at least a sentence or two.")
        if not attested:
            raise IntakeError("Please tick the box to confirm your answer is accurate.")
        texts = [description, owner, exceptions, frequency, last_reviewed, email, name]
        if any(len(t or "") > MAX_TEXT for t in texts):
            raise IntakeError(f"Text answers can be at most {MAX_TEXT} characters.")
        flags = sorted({f for t in texts for f in phi_flags(t)})
        if flags:
            raise IntakeError("Your answer seems to contain " + ", ".join(flags) + ". Please remove patient information "
                              "and describe the process instead, then submit again.")
        if len(files) > MAX_FILES:
            raise IntakeError(f"Please upload at most {MAX_FILES} files.")
        clean_files: list[tuple[str, bytes]] = []
        for fname, data in files:
            fname = safe_name(fname)
            ext = Path(fname).suffix.lower()
            if ext not in ALLOWED:
                raise IntakeError(f"{fname}: this file type is not accepted. Use PDF, DOCX, TXT, MD, PNG or JPG.")
            if len(data) > MAX_FILE_BYTES:
                raise IntakeError(f"{fname} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
            if not data:
                raise IntakeError(f"{fname} is empty.")
            if not looks_like(ext, data):
                raise IntakeError(f"{fname} does not look like a real {ext[1:].upper()} file.")
            clean_files.append((re.sub(r"[^A-Za-z0-9._ -]", "_", fname)[:120], data))

        self.sub_dir.mkdir(parents=True, exist_ok=True)
        while True:
            sid = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
            folder = self.sub_dir / sid
            try:
                folder.mkdir()
                break
            except FileExistsError:
                continue
        (folder / "files").mkdir()
        saved: list[str] = []
        for fname, data in clean_files:
            stem, suf = Path(fname).stem, Path(fname).suffix
            target, n = folder / "files" / fname, 1
            while target.exists():
                n += 1
                target = folder / "files" / f"{stem}-{n}{suf}"
            target.write_bytes(data)
            saved.append(target.name)
        sub = Submission(id=sid, created=datetime.now().strftime("%Y-%m-%d %H:%M"), name=name, email=email.strip(), role=role,
                         department=department, control_ids=list(dict.fromkeys(control_ids)), request_id=request_id,
                         status_claimed=status_claimed, description=description.strip(), owner=owner.strip(),
                         last_reviewed=last_reviewed.strip(), frequency=frequency.strip(), exceptions=exceptions.strip(),
                         files=saved, attested=True)
        sub.phi_warnings = self.scan_files(sub, folder)
        self._write(sub)
        return sub

    def scan_files(self, sub: Submission, folder: Optional[Path] = None) -> list[str]:
        """Look inside uploaded documents for obvious patient identifiers (images cannot be read)."""
        from .ingest import read_document
        folder = folder or self.sub_dir / sub.id
        found: set[str] = set()
        for fname in sub.files:
            path = folder / "files" / fname
            if path.suffix.lower() in INDEXABLE:
                try:
                    text = "\n".join(t for _, t in read_document(path))
                except Exception:
                    continue
                found |= {f"{f} in {fname}" for f in phi_flags(text)}
        return sorted(found)

    def _write(self, sub: Submission) -> None:
        (self.sub_dir / sub.id / "submission.json").write_text(json.dumps(asdict(sub), indent=1), encoding="utf-8")

    def submissions(self) -> list[Submission]:
        out: list[Submission] = []
        if not self.sub_dir.exists():
            return out
        for p in sorted(self.sub_dir.iterdir(), reverse=True):
            if SUB_ID.match(p.name) and (p / "submission.json").exists():
                try:
                    out.append(Submission(**json.loads((p / "submission.json").read_text(encoding="utf-8"))))
                except (json.JSONDecodeError, TypeError, OSError):
                    continue
        return out

    def get(self, sid: str) -> Submission:
        if not SUB_ID.match(sid):
            raise KeyError(sid)
        return Submission(**json.loads((self.sub_dir / sid / "submission.json").read_text(encoding="utf-8")))

    def file_bytes(self, sid: str, fname: str) -> bytes:
        if not SUB_ID.match(sid) or fname != safe_name(fname):
            raise KeyError(fname)
        return (self.sub_dir / sid / "files" / fname).read_bytes()

    # --------------------------------------------------------------- review
    def evidence_text(self, sub: Submission, controls: dict, reviewer: str) -> str:
        lines = [f"# Evidence submission {sub.id}", "",
                 "Evidence type: self-attested questionnaire response. The contributor's statements have not been independently verified.", "",
                 f"Submitted by: {sub.name}, {sub.role}, {sub.department}, on {sub.created}.", ""]
        for cid in sub.control_ids:
            c = controls.get(cid)
            lines.append(f"Requirement: {cid} {c.title if c else ''}".strip() + (f" ({c.requirement})" if c and c.requirement else ""))
        lines += ["", f"Implementation status stated by the contributor: {sub.status_claimed}.", "",
                  f"How it is done: {sub.description}", ""]
        if sub.owner:
            lines += [f"Responsible owner: {sub.owner}.", ""]
        if sub.last_reviewed:
            lines += [f"Last reviewed or tested: {sub.last_reviewed}.", ""]
        if sub.frequency:
            lines += [f"Review or operation frequency: {sub.frequency}.", ""]
        if sub.exceptions:
            lines += [f"Exceptions or limitations: {sub.exceptions}", ""]
        if sub.files:
            lines += ["Attached files: " + ", ".join(sub.files) + ".", ""]
        lines += [f"The contributor confirmed this answer is accurate. Accepted into the evidence library by {reviewer} on "
                  f"{datetime.now():%Y-%m-%d}."]
        return "\n".join(lines)

    def accept(self, sid: str, ws, controls: dict, reviewer: str, comment: str = "") -> Submission:
        """Copy the answer (as a text document) and any readable files into the workspace's evidence library."""
        sub = self.get(sid)
        if sub.review_status == "Accepted":
            return sub
        if not reviewer.strip():
            raise IntakeError("Enter your name as the reviewer first.")
        docs = [ws.add_evidence(f"form-{sub.id}.md", self.evidence_text(sub, controls, reviewer.strip()).encode("utf-8"))]
        for fname in sub.files:
            if Path(fname).suffix.lower() in INDEXABLE:
                docs.append(ws.add_evidence(f"form-{sub.id}-{fname}", self.file_bytes(sid, fname)))
        sub.review_status, sub.reviewer, sub.reviewer_comment = "Accepted", reviewer.strip(), comment.strip()
        sub.reviewed_at, sub.evidence_docs = datetime.now().strftime("%Y-%m-%d %H:%M"), docs
        self._write(sub)
        ws.log(f"Accepted evidence submission {sub.id} from {sub.name} ({', '.join(sub.control_ids)})")
        return sub

    def reject(self, sid: str, reviewer: str, reason: str) -> Submission:
        if not reviewer.strip():
            raise IntakeError("Enter your name as the reviewer first.")
        if not reason.strip():
            raise IntakeError("Give a short reason so the contributor knows what to fix.")
        sub = self.get(sid)
        sub.review_status, sub.reviewer, sub.reviewer_comment = "Rejected", reviewer.strip(), reason.strip()
        sub.reviewed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._write(sub)
        return sub
