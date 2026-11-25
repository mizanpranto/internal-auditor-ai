import pytest

from conftest import FakeLLM
from internal_auditor import audit_model as M
from internal_auditor.config import ROOT, Settings
from internal_auditor.frameworks import load_framework
from internal_auditor.report import write_markdown, write_xlsx
from internal_auditor.risk import build_risk_register
from internal_auditor.schemas import Assessment, LLMAssessment
from internal_auditor.scoring import summarize
from internal_auditor.workspace import Workspace
from test_audit_report import docx_text, pdf_text

META, CTRL = load_framework(ROOT / "frameworks" / "hipaa.json")
BY = {c.id: c for c in CTRL}
CLEARING = BY["164.308(a)(4)(ii)(A)"]


def met(c):
    return Assessment(control_id=c.id, domain=c.domain, title=c.title, status="Met", citation_verified=True, review_status="Approved")


def test_model_can_never_answer_not_applicable():
    with pytest.raises(Exception):
        LLMAssessment(chunk_id=-1, quote="", status="Not applicable", reasoning="", missing="")


def test_marking_not_applicable_needs_a_reviewer_and_a_justification(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError, match="reviewer"):
        ws.mark_not_applicable(CLEARING, "", "We do not operate a health care clearinghouse.")
    with pytest.raises(ValueError, match="justification"):
        ws.mark_not_applicable(CLEARING, "Mizan", "n/a")
    assert ws.get(CLEARING.id) is None or ws.get(CLEARING.id).status != "Not applicable"
    a = ws.mark_not_applicable(CLEARING, "Mizan", "We do not operate a health care clearinghouse.")  # never assessed before
    assert a.status == "Not applicable" and a.review_status == "Approved" and a.reviewer == "Mizan"
    assert a.reviewer_note.startswith("We do not") and a.ai_status == "No evidence"


def test_override_to_not_applicable_keeps_the_model_verdict_and_can_be_reversed(tmp_path):
    ws = Workspace(tmp_path)
    ws.put(Assessment(control_id="X1", domain="D", title="T", status="Not met"))
    with pytest.raises(ValueError, match="justification"):
        ws.review("X1", "override", "Mizan", "", "Not applicable")
    a = ws.review("X1", "override", "Mizan", "Out of scope: no ICS in this site.", "Not applicable")
    assert a.status == "Not applicable" and a.ai_status == "Not met"
    back = ws.review("X1", "override", "Mizan", "", "Not met")
    assert back.status == "Not met"


def test_not_applicable_is_left_out_of_scores_and_risks():
    items = [met(CTRL[0]), Assessment(control_id="a", domain=CTRL[0].domain, title="t", status="Not applicable", review_status="Approved"),
             Assessment(control_id=CTRL[1].id, domain=CTRL[1].domain, title="t", status="Not met", review_status="Approved")]
    s = summarize(items)
    assert s["overall"]["total"] == 2 and s["overall"]["na"] == 1 and s["overall"]["score"] == 0.5
    risks = build_risk_register(items, CTRL)
    assert [r.control_id for r in risks] == [CTRL[1].id]


def test_a_model_run_never_overwrites_not_applicable(tmp_path):
    from internal_auditor.engine import Engine
    ws = Workspace(tmp_path)
    ws.add_evidence("p.md", (ROOT / "sample_docs_hipaa" / "hipaa_security_policy.md").read_bytes())
    ws.mark_not_applicable(CLEARING, "Mizan", "We do not operate a health care clearinghouse.")
    eng = Engine(ws, Settings(), CTRL, llm=FakeLLM())
    eng.build_index(use_embeddings=True)
    res = eng.assess([CLEARING.id, "164.308(a)(2)"])
    assert [a.control_id for a in res] == ["164.308(a)(2)"]
    assert ws.get(CLEARING.id).status == "Not applicable"


def test_exports_do_not_break_with_not_applicable(tmp_path):
    pool = [met(CTRL[0]), Assessment(control_id=CLEARING.id, domain=CLEARING.domain, title=CLEARING.title, status="Not applicable",
                                     reviewer_note="No clearinghouse.", review_status="Approved")]
    s = summarize(pool)
    assert write_xlsx(tmp_path / "a.xlsx", {"framework": "x"}, pool, [], s, []).exists()
    assert "Not applicable" in write_markdown(tmp_path / "a.md", {"framework": "x"}, pool, [], s, []).read_text()


def test_audit_report_lists_not_applicable_with_justification_and_does_not_count_it():
    na = Assessment(control_id=CLEARING.id, domain=CLEARING.domain, title=CLEARING.title, status="Not applicable",
                    reviewer_note="We do not operate a health care clearinghouse.", reviewer="Mizan", reviewed_at="2026-11-22 10:00",
                    review_status="Approved")
    gate = BY["164.308(b)(1)"]
    pool = [met(c) for c in CTRL if c.id not in (CLEARING.id, gate.id)] + [na]
    gate_na = Assessment(control_id=gate.id, domain=gate.domain, title=gate.title, status="Not applicable",
                         reviewer_note="No business associates handle ePHI.", reviewer="Mizan", review_status="Approved")
    info = M.ReportInfo(company="Clinic", assessor_name="A")
    m = M.build_model(info, META, pool + [gate_na], [], [], CTRL, with_charts=False)
    assert len(m.na) == 2 and len(m.results) == 78 and m.not_covered == 0 and m.score == 1.0
    assert m.rating == "Satisfactory" and not m.rating_note       # a justified N/A foundational control does not cap the rating
    assert not any(f.control_id == CLEARING.id for f in m.findings)
    assert any("2 controls were marked not applicable" in k for k in m.key_messages) and "of 78 applicable controls" in m.key_messages[0]
    pdf, docx = M.build_reports(info, META, pool + [gate_na], [], [], CTRL)
    text = " ".join(pdf_text(pdf)).replace("\n", " ")
    assert "Appendix C: controls marked not applicable" in text and "We do not operate a health care clearinghouse." in text
    assert "Appendix D: limitations" in text
    assert "Appendix C: controls marked not applicable" in docx_text(docx) and "No business associates handle ePHI." in docx_text(docx)


def test_without_not_applicable_appendix_letters_are_unchanged():
    m = M.build_model(M.ReportInfo(), META, [met(CTRL[0])], [], [], CTRL, with_charts=False)
    assert m.na == []
    pdf, _ = M.build_reports(M.ReportInfo(), META, [met(CTRL[0])], [], [], CTRL)
    assert "Appendix C: limitations" in " ".join(pdf_text(pdf)).replace("\n", " ")
