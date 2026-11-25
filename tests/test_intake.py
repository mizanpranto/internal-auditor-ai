import pytest

from internal_auditor.config import ROOT
from internal_auditor.frameworks import load_framework
from internal_auditor.intake import Intake, IntakeError, phi_flags
from internal_auditor.workspace import Workspace

_, CTRL = load_framework(ROOT / "frameworks" / "hipaa.json")
BY = {c.id: c for c in CTRL}
GOOD = dict(name="Asha Rahman", email="asha@clinic.example", role="IT Manager", department="IT", control_ids=["164.312(b)"],
            status_claimed="Fully in place", description="The EHR records every view, change and export of a patient record and logs are kept for six years.",
            attested=True, files=[])


def make(tmp_path, **kw):
    it = Intake(tmp_path)
    return it, it.submit(**{**GOOD, **kw})


def test_submit_creates_a_pending_submission_without_touching_the_workspace_state(tmp_path):
    ws = Workspace(tmp_path)
    before = (tmp_path / "state.json").read_text() if (tmp_path / "state.json").exists() else None
    it, sub = make(tmp_path)
    assert sub.review_status == "Pending" and sub.attested and it.get(sub.id).name == "Asha Rahman"
    assert not list(ws.evidence_dir.glob("*"))  # nothing is indexed or counted before the assessor accepts
    assert ((tmp_path / "state.json").read_text() if (tmp_path / "state.json").exists() else None) == before


@pytest.mark.parametrize("override,msg", [
    ({"name": ""}, "name, role and department"), ({"control_ids": []}, "requirement"), ({"status_claimed": ""}, "how far"),
    ({"description": "too short"}, "describe"), ({"attested": False}, "confirm"),
    ({"description": "Patient SSN 123-45-6789 was reset by the help desk last week for audit."}, "Social Security"),
    ({"exceptions": "MRN: 00123456 is an example"}, "medical record"),
    ({"files": [("evil.exe", b"MZ")]}, "not accepted"), ({"files": [("fake.pdf", b"MZ\x90\x00program")]}, "does not look like a real PDF"),
    ({"files": [("fake.png", b"<script>alert(1)</script>")]}, "real PNG"), ({"files": [("bin.txt", b"abc\x00def")]}, "real TXT"), ({"files": [("a.pdf", b"")]}, "empty"),
    ({"files": [("big.pdf", b"%PDF-" + b"x" * (10 * 1024 * 1024))]}, "larger than"),
    ({"files": [("a.txt", b"x")] * 11}, "at most 10"), ({"description": "x" * 4001}, "4000"),
])
def test_bad_submissions_are_rejected_with_a_clear_message(tmp_path, override, msg):
    with pytest.raises(IntakeError, match=msg):
        make(tmp_path, **override)
    assert not (tmp_path / "intake" / "submissions").exists() or not list((tmp_path / "intake" / "submissions").iterdir())


def test_file_names_cannot_escape_the_submission_folder(tmp_path):
    it, sub = make(tmp_path, files=[("../../../etc/passwd.txt", b"hello"), ("../x.md", b"# hi"), ("../x.md", b"# hi2")])
    folder = tmp_path / "intake" / "submissions" / sub.id / "files"
    assert sorted(p.name for p in folder.iterdir()) == sorted(sub.files) and len(sub.files) == 3
    assert not (tmp_path / "etc").exists()
    with pytest.raises(KeyError):
        it.file_bytes(sub.id, "../submission.json")
    with pytest.raises(KeyError):
        it.get("../../state")


def test_phi_inside_an_uploaded_document_is_flagged_not_blocked(tmp_path):
    _, sub = make(tmp_path, files=[("log.txt", b"Reset for patient DOB: 04/12/1980 and SSN 111-22-3333")])
    assert any("Social Security" in w and "log.txt" in w for w in sub.phi_warnings)
    assert phi_flags("Our policy says nothing personal.") == []


def test_access_code(tmp_path):
    it = Intake(tmp_path)
    assert not it.code_required() and it.check_code("")
    it.set_access_code("  s3cret ")
    assert it.code_required() and it.check_code("s3cret") and not it.check_code("nope") and not it.check_code("")


def test_requests_status_follows_submissions(tmp_path):
    it = Intake(tmp_path)
    req = it.add_request("164.312(b)", "Asha", "2026-12-01", "Send logging settings")
    assert it.request_status(req) == "Open"
    sub = it.submit(**{**GOOD, "request_id": req["id"]})
    assert it.request_status(req) == "Received"
    it.reject(sub.id, "Mizan", "Please attach the configuration screenshot")
    assert it.request_status(req) == "Open" and it.get(sub.id).review_status == "Rejected"
    sub2 = it.submit(**{**GOOD, "request_id": req["id"]})
    ws = Workspace(tmp_path)
    it.accept(sub2.id, ws, BY, "Mizan")
    assert it.request_status(req) == "Closed"
    it.remove_request(req["id"])
    assert it.requests() == []


def test_accept_adds_a_labelled_evidence_document_and_readable_files(tmp_path):
    ws = Workspace(tmp_path)
    it, sub = make(tmp_path, control_ids=["164.312(b)", "164.308(a)(1)(ii)(D)"], owner="IT Manager", last_reviewed="Nov 2026",
                   files=[("settings.txt", b"Audit logging is enabled on the EHR."), ("shot.png", b"\x89PNG\r\n\x1a\n....")])
    with pytest.raises(IntakeError, match="reviewer"):
        it.accept(sub.id, ws, BY, " ")
    done = it.accept(sub.id, ws, BY, "Mizan", "Looks right")
    assert done.review_status == "Accepted" and len(done.evidence_docs) == 2   # the answer + the readable file, not the image
    names = sorted(p.name for p in ws.evidence_dir.iterdir())
    assert names == sorted(done.evidence_docs) and f"form-{sub.id}.md" in names
    text = (ws.evidence_dir / f"form-{sub.id}.md").read_text()
    assert "self-attested" in text and "164.312(b) Audit controls (Required)" in text and "Asha Rahman, IT Manager, IT" in text
    assert "Accepted into the evidence library by Mizan" in text
    assert it.accept(sub.id, ws, BY, "Mizan").evidence_docs == done.evidence_docs   # accepting twice changes nothing
    assert len(list(ws.evidence_dir.iterdir())) == 2


def test_an_accepted_answer_can_be_quoted_by_the_citation_lock(tmp_path):
    from internal_auditor.assessor import quote_supported
    ws = Workspace(tmp_path)
    _, sub = make(tmp_path)
    Intake(tmp_path).accept(sub.id, ws, BY, "Mizan")
    text = (ws.evidence_dir / f"form-{sub.id}.md").read_text()
    assert quote_supported("The EHR records every view, change and export of a patient record", text, 0.97, 4)
