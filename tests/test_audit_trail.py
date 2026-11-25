import pytest
from internal_auditor.schemas import Assessment
from internal_auditor.workspace import Workspace


def mk(ws, cid="A.1", status="Partially met", doc="policy.md", verified=True):
    ws.put(Assessment(control_id=cid, domain="D", title="T", status=status, source_doc=doc, citation_verified=verified))


def test_log_chain_detects_edit(tmp_path):
    ws = Workspace(tmp_path)
    ws.log("one"); ws.log("two"); ws.log("three")
    assert ws.verify_log() == (True, 3, "3 entries, chain intact.")
    lines = ws.chain_path.read_text().splitlines()
    ws.chain_path.write_text("\n".join([lines[0], lines[1].replace("two", "TWO"), lines[2]]) + "\n")
    ok, n, msg = ws.verify_log()
    assert not ok and n == 1 and "entry 2" in msg


def test_log_chain_detects_deleted_entry(tmp_path):
    ws = Workspace(tmp_path)
    for t in ("a", "b", "c"):
        ws.log(t)
    lines = ws.chain_path.read_text().splitlines()
    ws.chain_path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert not ws.verify_log()[0]


def test_approval_records_hash_and_detects_change(tmp_path):
    ws = Workspace(tmp_path)
    ws.add_evidence("policy.md", b"v1")
    mk(ws)
    ws.review("A.1", "approve", "Rev")
    a = ws.get("A.1")
    assert a.evidence_sha and ws.evidence_state(a) == "current"
    (ws.evidence_dir / "policy.md").write_bytes(b"v2")
    assert ws.evidence_state(ws.get("A.1")) == "changed"
    (ws.evidence_dir / "policy.md").unlink()
    assert ws.evidence_state(ws.get("A.1")) == "missing"


def test_override_to_met_must_name_evidence(tmp_path):
    ws = Workspace(tmp_path)
    ws.add_evidence("policy.md", b"x")
    mk(ws, status="Not met", verified=True)
    with pytest.raises(ValueError, match="Name the evidence"):
        ws.review("A.1", "override", "Rev", "looks fine to me", "Met")
    a = ws.review("A.1", "override", "Rev", "Checked policy.md section 4", "Met")
    assert a.status == "Met" and a.ai_status == "Not met"
