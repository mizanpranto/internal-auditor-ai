import json

import pytest

from conftest import FakeLLM
from internal_auditor.config import ROOT, Settings
from internal_auditor.frameworks import DEFAULT_FRAMEWORK, list_frameworks, load_framework
from internal_auditor.schemas import Assessment
from internal_auditor.workspace import Workspace

EXPECTED = {"iso27001_2022": 93, "soc2_tsc": 38, "nist_csf_2": 22, "hipaa": 80, "gdpr": 41, "iso42001": 45}


def test_bundled_frameworks_are_listed_default_first():
    fws = list_frameworks()
    assert {k: v["count"] for k, v in fws.items()} == EXPECTED
    assert next(iter(fws)) == DEFAULT_FRAMEWORK
    assert fws["soc2_tsc"]["short"] == "SOC 2" and fws["nist_csf_2"]["short"] == "NIST CSF 2.0"


@pytest.mark.parametrize("fid,count", EXPECTED.items())
def test_each_framework_is_well_formed(fid, count):
    meta, controls = load_framework(ROOT / "frameworks" / f"{fid}.json")
    assert len(controls) == count and len({c.id for c in controls}) == count
    assert all(1 <= c.impact <= 5 for c in controls)
    assert all(c.title and c.summary and c.evidence_hint and c.domain for c in controls)
    assert "paraphrase" in meta["note"]


def test_soc2_and_nist_shapes():
    _, soc2 = load_framework(ROOT / "frameworks" / "soc2_tsc.json")
    assert sum(c.id.startswith("CC") for c in soc2) == 33
    assert {c.id for c in soc2 if c.id[0] in "AC" and not c.id.startswith("CC")} == {"A1.1", "A1.2", "A1.3", "C1.1", "C1.2"}
    _, nist = load_framework(ROOT / "frameworks" / "nist_csf_2.json")
    assert {c.domain for c in nist} == {"Govern", "Identify", "Protect", "Detect", "Respond", "Recover"}
    assert [c.id for c in nist if c.domain == "Respond"] == ["RS.MA", "RS.AN", "RS.CO", "RS.MI"]


def test_a_broken_framework_file_is_skipped(tmp_path, monkeypatch):
    import internal_auditor.frameworks as fw
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "empty.json").write_text(json.dumps({"framework": "x"}))  # no controls key
    (tmp_path / "ok.json").write_text(json.dumps({"framework": "Fine (v1)", "controls": []}))
    monkeypatch.setattr(fw, "FRAMEWORK_DIR", tmp_path)
    found = fw.list_frameworks()
    assert list(found) == ["ok"] and found["ok"]["short"] == "Fine"


def _a(cid, status="Met"):
    return Assessment(control_id=cid, domain="D", title="t", status=status, review_status="Approved")


def test_switching_frameworks_keeps_each_ones_work(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.framework_id == DEFAULT_FRAMEWORK
    ws.put(_a("5.1"))
    ws.impact_overrides()["5.1"] = 5
    ws.update_risk("5.1", owner_role="CISO")

    ws.switch_framework("soc2_tsc")
    assert ws.framework_id == "soc2_tsc"
    assert ws.assessments() == {} and ws.impact_overrides() == {} and ws.state["risks"] == {}
    ws.put(_a("CC6.1", "Partially met"))

    ws.switch_framework(DEFAULT_FRAMEWORK)  # back: the ISO work is intact, SOC 2 work is stashed
    assert list(ws.assessments()) == ["5.1"] and ws.impact_overrides() == {"5.1": 5}
    assert ws.state["risks"]["5.1"]["owner_role"] == "CISO"

    ws.switch_framework("soc2_tsc")
    assert ws.get("CC6.1").status == "Partially met"


def test_framework_choice_and_stash_survive_restart(tmp_path):
    ws = Workspace(tmp_path)
    ws.put(_a("5.1"))
    ws.switch_framework("nist_csf_2")
    again = Workspace(tmp_path)
    assert again.framework_id == "nist_csf_2" and again.assessments() == {}
    again.switch_framework(DEFAULT_FRAMEWORK)
    assert list(again.assessments()) == ["5.1"]


def test_forget_framework_drops_only_the_stash(tmp_path):
    ws = Workspace(tmp_path)
    ws.put(_a("5.1"))
    ws.switch_framework("custom")
    ws.put(_a("X1"))
    ws.switch_framework(DEFAULT_FRAMEWORK)
    ws.forget_framework("custom")
    ws.switch_framework("custom")
    assert ws.assessments() == {}
    ws.switch_framework(DEFAULT_FRAMEWORK)
    assert list(ws.assessments()) == ["5.1"]


def test_switching_to_the_current_framework_is_a_noop(tmp_path):
    ws = Workspace(tmp_path)
    ws.put(_a("5.1"))
    ws.switch_framework(DEFAULT_FRAMEWORK)
    assert list(ws.assessments()) == ["5.1"] and "stash" not in ws.state


def test_reviewer_cannot_upgrade_an_unverified_verdict_without_a_note(tmp_path):
    ws = Workspace(tmp_path)
    ws.put(Assessment(control_id="8.7", domain="D", title="Malware", status="No evidence"))
    with pytest.raises(ValueError, match="reviewer note"):
        ws.review("8.7", "override", "Mizan", "", "Met")
    assert ws.get("8.7").status == "No evidence" and ws.get("8.7").review_status == "Draft"
    a = ws.review("8.7", "override", "Mizan", "Saw EDR console screenshot, ticket SEC-114", "Partially met")
    assert a.status == "Partially met" and a.ai_status == "No evidence" and a.review_status == "Approved"
    # lowering a verdict, or touching a verified one, never needs a note
    ws.put(Assessment(control_id="5.1", domain="D", title="Policies", status="Met", citation_verified=True))
    assert ws.review("5.1", "override", "Mizan", "", "Partially met").status == "Partially met"


@pytest.mark.parametrize("fid", ["soc2_tsc", "nist_csf_2", "hipaa"])
def test_engine_assesses_a_non_iso_framework(tmp_path, fid):
    """The whole agent loop (retrieval, citation lock, scoring) is framework-agnostic."""
    from internal_auditor.engine import Engine
    ws = Workspace(tmp_path)
    ws.add_evidence("policy.md", (ROOT / "sample_docs" / "access_control_procedure.md").read_bytes())
    _, controls = load_framework(ROOT / "frameworks" / f"{fid}.json")
    eng = Engine(ws, Settings(), controls, llm=FakeLLM())
    eng.build_index(use_embeddings=True)
    ids = [c.id for c in controls[:6]]
    res = eng.assess(ids)
    assert [a.control_id for a in res] == ids
    assert all(a.review_status == "Draft" for a in res)
    assert set(ws.assessments()) == set(ids)


def test_hipaa_framework_shape():
    meta, ctrl = load_framework(ROOT / "frameworks" / "hipaa.json")
    by = {c.id: c for c in ctrl}
    assert {c.requirement for c in ctrl} == {"Required", "Addressable", "Standard"}
    # Security Rule: 49 specifications; Privacy 24; Breach Notification 7 (80 in all)
    sec = [c for c in ctrl if c.id.startswith(("164.308", "164.310", "164.312", "164.314", "164.316"))]
    assert len(sec) == 49 and all(c.requirement in ("Required", "Addressable") for c in sec)
    assert by["164.308(a)(1)(ii)(A)"].requirement == "Required" and by["164.312(a)(2)(iv)"].requirement == "Addressable"
    assert {c.domain.split(" (")[0] for c in ctrl} >= {"Administrative safeguards", "Physical safeguards", "Technical safeguards",
                                                      "Organizational requirements", "Policies and documentation", "Privacy Rule", "Breach Notification Rule"}
    assert set(meta["gating_controls"]) <= set(by) and set(meta["foundational_controls"]) >= set(meta["gating_controls"])


def test_hipaa_demo_quotes_exist_and_load(tmp_path):
    from internal_auditor.assessor import quote_supported
    from internal_auditor.demo import demo_for, load_demo
    table, folder = demo_for("hipaa")
    _, ctrl = load_framework(ROOT / "frameworks" / "hipaa.json")
    ids = {c.id for c in ctrl}
    for cid, (status, quote, doc, *_rest) in table.items():
        assert cid in ids
        assert quote_supported(quote, (ROOT / folder / doc).read_text(encoding="utf-8"), 0.97, 4), cid
    ws = Workspace(tmp_path)
    ws.switch_framework("hipaa")
    assert load_demo(ws, ctrl) == 80 and len(ws.assessments()) == 80
    assert len(list(ws.evidence_dir.glob("*.md"))) == 3
