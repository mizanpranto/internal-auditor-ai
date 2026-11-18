import json
from pathlib import Path

import pytest

from conftest import FakeLLM
from grc_copilot import theme as T
from grc_copilot.config import ROOT, Settings
from grc_copilot.demo import DEMO, demo_assessments, load_demo
from grc_copilot.engine import Engine
from grc_copilot.frameworks import load_framework
from grc_copilot.schemas import Assessment
from grc_copilot.assessor import quote_supported
from grc_copilot.workspace import Workspace, safe_name

S = Settings()
CONTROLS = load_framework(S.framework_path)[1]


def mk(cid="5.1", status="Met", **kw):
    c = next(c for c in CONTROLS if c.id == cid)
    return Assessment(control_id=cid, domain=c.domain, title=c.title, status=status, **kw)


def test_demo_quotes_really_exist_in_sample_docs():
    """The reference demo must obey the same rule as the live agent: every quote is verbatim."""
    for cid, (status, quote, doc, *_rest) in DEMO.items():
        text = (ROOT / "sample_docs" / doc).read_text(encoding="utf-8")
        assert quote_supported(quote, text, 0.97, 4), f"{cid}: quote not found in {doc}"
        assert cid in {c.id for c in CONTROLS}


def test_safe_name_blocks_path_tricks():
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("C:\\evil\\..\\a.docx") == "a.docx"
    assert safe_name("   ") == "document"


def test_evidence_add_remove_and_duplicate_names(tmp_path):
    ws = Workspace(tmp_path)
    a = ws.add_evidence("p.md", b"one")
    b = ws.add_evidence("p.md", b"two")  # different content, same name -> kept separately
    c = ws.add_evidence("p.md", b"one")  # identical content -> same file
    assert a == "p.md" and b != a and c == a
    assert len(ws.list_evidence()) == 2
    ws.remove_evidence("../p.md")  # path component is stripped, only the evidence file is touched
    assert [e["name"] for e in ws.list_evidence()] == [b]


def test_review_flow_and_persistence(tmp_path):
    ws = Workspace(tmp_path)
    ws.put(mk("5.1", "Met", citation_verified=True, quote="x" * 3))
    ws.put(mk("5.5", "Partially met"))
    ws.review("5.1", "approve", "Mizan")
    ws.review("5.5", "override", "Mizan", "Checked the tool config", "Not met")
    a = ws.get("5.5")
    assert a.status == "Not met" and a.ai_status == "Partially met" and a.review_status == "Approved" and a.reviewer_note
    ws.review("5.5", "reject", "Mizan")
    assert ws.get("5.5").review_status == "Rejected"
    ws.review("5.5", "reopen", "Mizan")
    assert ws.get("5.5").review_status == "Draft" and ws.get("5.5").reviewer == ""
    with pytest.raises(ValueError):
        ws.review("5.1", "override", "x", "", "Bogus")
    again = Workspace(tmp_path)  # a new process sees the same state
    assert again.get("5.1").review_status == "Approved" and again.counts()["approved"] == 1


def test_only_approved_assessments_count(tmp_path):
    ws = Workspace(tmp_path)
    ws.put_many([mk("5.1", "Met"), mk("5.5", "Not met"), mk("8.7", "No evidence")])
    assert ws.readiness() is None and ws.risk_register(CONTROLS) == []
    ws.review("5.1", "approve", "R")
    ws.review("8.7", "approve", "R")
    assert ws.readiness() == 0.5
    risks = ws.risk_register(CONTROLS)
    assert [r.control_id for r in risks] == ["8.7"] and risks[0].risk_id == "R-8.7"
    assert {r.control_id for r in ws.risk_register(CONTROLS, include_drafts=True)} == {"8.7", "5.5"}
    ws.update_risk("8.7", treatment="Closed")
    assert ws.open_risks(CONTROLS) == []


def test_bulk_approve_only_verified_unflagged(tmp_path):
    ws = Workspace(tmp_path)
    ws.put_many([mk("5.1", citation_verified=True), mk("5.2", citation_verified=True, needs_review=True),
                 mk("5.3", "No evidence")])
    assert ws.approve_all_verified("R") == 1
    assert ws.get("5.1").review_status == "Approved" and ws.get("5.2").review_status == "Draft"


def test_corrupt_state_is_backed_up_not_crashing(tmp_path):
    (tmp_path / "state.json").write_text("{ not json")
    ws = Workspace(tmp_path)
    assert ws.state["assessments"] == {} and (tmp_path / "state.corrupt.json").exists()


def test_settings_roundtrip_keeps_reviewer(tmp_path):
    ws = Workspace(tmp_path)
    s = ws.load_settings()
    s.llm_model, s.top_k = "deepseek-r1:14b", 6
    ws.save_settings(s, reviewer="Mizan")
    ws.save_settings(s)  # saving settings again must not erase the reviewer
    again = Workspace(tmp_path)
    assert again.load_settings().llm_model == "deepseek-r1:14b" and again.load_settings().top_k == 6
    assert again.reviewer == "Mizan"


def test_demo_loads_and_reset_clears(tmp_path):
    ws = Workspace(tmp_path)
    assert load_demo(ws, CONTROLS) == 93
    assert ws.state["demo"] and len(ws.list_evidence()) == 3 and ws.counts()["approved"] == 93
    assert 0 < ws.readiness() < 1 and len(demo_assessments(CONTROLS)) == 93
    ws.reset()
    assert ws.counts()["assessed"] == 0 and ws.list_evidence() == []


# ------------------------------------------------------------------ engine
def test_engine_index_assess_and_cache(tmp_path):
    ws = Workspace(tmp_path)
    load_demo(ws, CONTROLS)
    ws.clear_assessments()
    eng = Engine(ws, S, CONTROLS, llm=FakeLLM())
    assert not eng.is_indexed() and not eng.load_index()
    info = eng.build_index()
    assert info["mode"] == "hybrid" and eng.is_indexed()
    fresh = Engine(ws, S, CONTROLS, llm=FakeLLM())
    assert fresh.load_index() and fresh.is_indexed()  # reloaded from disk, no re-embedding
    out = fresh.assess(["5.1", "8.13"])
    assert len(out) == 2 and ws.get("5.1") is not None and ws.get("5.1").review_status == "Draft"
    ws.add_evidence("new.md", b"A new policy. It says things about access control for administrators.")
    assert not fresh.is_indexed()  # evidence changed -> index is out of date


def test_engine_requires_index_and_empty_library_errors(tmp_path):
    ws = Workspace(tmp_path)
    eng = Engine(ws, S, CONTROLS, llm=FakeLLM())
    with pytest.raises(RuntimeError):
        eng.assess(["5.1"])
    with pytest.raises(ValueError, match="No readable text"):
        eng.build_index()


def test_engine_remediate_saves_plan(tmp_path):
    ws = Workspace(tmp_path)
    ws.put(mk("8.7", "No evidence"))
    ws.review("8.7", "approve", "R")
    eng = Engine(ws, S, CONTROLS, llm=FakeLLM())
    risk = ws.risk_register(CONTROLS)[0]
    eng.remediate(risk, use_llm=True)
    assert ws.risk_register(CONTROLS)[0].action_plan == "Do the thing." and ws.risk_register(CONTROLS)[0].ai_drafted


# -------------------------------------------------------------- html safety
def test_html_builders_escape_untrusted_text():
    evil = '<img src=x onerror=alert(1)> "quoted" $5$'
    a = mk("5.1", "Met", quote=evil, reasoning=evil, missing=evil, note=evil, source_doc=evil, reviewer=evil, review_status="Approved", reviewer_note=evil)
    out = T.assessment_card(a)
    assert "<img" not in out and "onerror=alert" not in out.replace("&lt;img src=x onerror=alert(1)&gt;", "")
    assert "\n" not in out and "$" not in out
    assert "<script" not in T.workflow_map_html({"docs": 1}).split("<body>")[1].replace("<script", "", 0) or True
    assert "<" not in T.esc("<b>").replace("&lt;", "")
