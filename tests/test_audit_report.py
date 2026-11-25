import io

import pytest
from docx import Document
from pypdf import PdfReader

from internal_auditor import audit_model as M
from internal_auditor.config import ROOT
from internal_auditor.demo import load_demo
from internal_auditor.frameworks import load_framework
from internal_auditor.schemas import Assessment
from internal_auditor.workspace import Workspace

META, CONTROLS = load_framework(ROOT / "frameworks" / "iso27001_2022.json")
INFO = M.ReportInfo(company="Northwind Ltd", assessor_name="Mizan Pranto", assessor_title="GRC Analyst",
                    approver_name="Rahim C.", engagement_ref="IA-1", assessment_period="Nov 2026",
                    distribution="Audit Committee", scope="ISMS")


def demo_inputs(tmp_path):
    ws = Workspace(tmp_path)
    load_demo(ws, CONTROLS)
    return ws, sorted(ws.approved(), key=lambda a: a.control_id), ws.risk_register(CONTROLS, False)


def pdf_text(data: bytes) -> list[str]:
    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages]


def docx_text(data: bytes) -> str:
    d = Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts += [c.text for c in row.cells]
    return "\n".join(parts)


def test_both_formats_are_real_files_with_the_cover_details(tmp_path):
    _, pool, risks = demo_inputs(tmp_path)
    pdf, docx = M.build_reports(INFO, {**META, "generated": "2026-11-15"}, pool, risks, [], CONTROLS, llm="qwen3:8b")
    assert pdf[:5] == b"%PDF-" and docx[:2] == b"PK"
    cover = pdf_text(pdf)[0]
    for want in ("Internal Auditor", "Developed by Pranto", "Northwind Ltd", "Mizan Pranto", "Rahim C.", "IA-1", "Confidential"):
        assert want in cover
    word = docx_text(docx)
    for want in ("Internal Auditor", "Northwind Ltd", "Mizan Pranto", "IA-1", "Executive summary", "Detailed findings"):
        assert want in word


def test_pdf_has_all_sections_and_page_x_of_y(tmp_path):
    _, pool, risks = demo_inputs(tmp_path)
    pdf, _ = M.build_reports(INFO, META, pool, risks, [], CONTROLS)
    pages = pdf_text(pdf)
    total = len(pages)
    joined = "\n".join(pages)
    for h in ("Contents", "Document control", "Executive summary", "Scope, approach and rating scales", "Results by domain",
              "Detailed findings", "Remediation action plan", "Appendices", "Sign-off", "To be completed by the control owner"):
        assert h in joined
    assert f"Page 2 of {total}" in pages[1]
    assert "Page 1 of" not in pages[0]  # the cover has no page number


def test_rating_thresholds():
    assert M.rating_for(0.9)[0] == "Satisfactory" and M.rating_for(0.85)[0] == "Satisfactory"
    assert M.rating_for(0.6)[0] == "Needs improvement" and M.rating_for(0.59)[0] == "Unsatisfactory"


def test_findings_are_ordered_by_risk_and_exclude_met(tmp_path):
    _, pool, risks = demo_inputs(tmp_path)
    m = M.build_model(INFO, META, pool, risks, [], CONTROLS, with_charts=False)
    scores = [f.score for f in m.findings]
    assert scores == sorted(scores, reverse=True) and [f.ref for f in m.findings][:2] == ["F-01", "F-02"]
    assert all(f.status != "Met" for f in m.findings) and len(m.findings) == len(risks)
    assert m.rating == "Unsatisfactory" or m.rating in M.RATING_HEX


def test_unreviewed_and_uncovered_controls_are_disclosed():
    pool = [Assessment(control_id="5.1", domain="D", title="Policies", status="Met", citation_verified=True,
                       review_status="Draft", quote="q", source_doc="p.md")]
    m = M.build_model(M.ReportInfo(), META, pool, [], [], CONTROLS, with_charts=False)
    text = " ".join(m.key_messages)
    assert "92 controls were not assessed" in text and "unreviewed AI drafts" in text


def test_empty_and_all_met_reports_still_build(tmp_path):
    pdf, docx = M.build_reports(M.ReportInfo(), META, [], [], [], CONTROLS)
    assert pdf[:5] == b"%PDF-" and "NOT RATED" in docx_text(docx)
    pool = [Assessment(control_id="5.1", domain="D", title="P", status="Met", citation_verified=True, review_status="Approved")]
    pdf, docx = M.build_reports(M.ReportInfo(company="X"), META, pool, [], [], CONTROLS)
    assert "No gaps were identified" in "\n".join(pdf_text(pdf)) and "No gaps were identified" in docx_text(docx)


def test_awkward_text_does_not_break_either_writer():
    evil = '<b>bold</b> & <script>x</script> "q" — → مرحبا 你好 \x07 ' + "long " * 400
    info = M.ReportInfo(company=evil, assessor_name=evil, scope=evil, report_title=evil)
    pool = [Assessment(control_id="8.7", domain=evil, title=evil, status="Not met", quote=evil, source_doc=evil,
                       reasoning=evil, missing=evil, reviewer_note=evil, reviewer=evil, review_status="Approved")]
    from internal_auditor.risk import build_risk_register
    risks = build_risk_register(pool, CONTROLS)
    pdf, docx = M.build_reports(info, META, pool, risks, [{"name": evil, "chunks": 1, "words": 2, "warning": evil}], CONTROLS)
    assert pdf[:5] == b"%PDF-" and "<script>" in docx_text(docx)  # kept as literal text, never as markup


def test_logo_is_accepted(tmp_path):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (300, 90), (10, 120, 200)).save(buf, "PNG")
    _, pool, risks = demo_inputs(tmp_path)
    pdf, docx = M.build_reports(INFO, META, pool, risks, [], CONTROLS, logo=buf.getvalue())
    assert pdf[:5] == b"%PDF-" and len(Document(io.BytesIO(docx)).inline_shapes) >= 2


def test_report_details_and_logo_persist_in_the_workspace(tmp_path):
    from PIL import Image
    ws = Workspace(tmp_path)
    ws.save_report_info(INFO.to_dict())
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "red").save(buf, "JPEG")
    ws.save_logo(buf.getvalue())
    again = Workspace(tmp_path)
    assert M.ReportInfo.from_dict(again.report_info()).company == "Northwind Ltd"
    assert again.logo()[:4] == b"\x89PNG"
    again.save_logo(None)
    assert again.logo() is None
    assert M.ReportInfo.from_dict({"company": "A", "unknown": "x", "version": None}).version == "1.0"


# ------------------------------------------------------------------- HIPAA
HMETA, HCTRL = load_framework(ROOT / "frameworks" / "hipaa.json")


def hipaa_inputs(tmp_path):
    ws = Workspace(tmp_path)
    ws.switch_framework("hipaa")
    load_demo(ws, HCTRL)
    return ws, sorted(ws.approved(), key=lambda a: a.control_id), ws.risk_register(HCTRL, False)


def hipaa_info():
    return M.ReportInfo(company="Riverbend Family Clinic", assessor_name="Mizan Pranto", entity_type="Covered entity",
                        security_official="Asha Rahman", privacy_official="Lee Carter", systems_in_scope="EHR, billing, email")


def test_hipaa_report_has_the_hipaa_sections_and_cover_fields(tmp_path):
    _, pool, risks = hipaa_inputs(tmp_path)
    pdf, docx = M.build_reports(hipaa_info(), {**HMETA, "generated": "2026-11-18"}, pool, risks, [], HCTRL)
    pages = pdf_text(pdf)
    cover = pages[0]
    for want in ("HIPAA Compliance Readiness Assessment", "Riverbend Family Clinic", "Covered entity", "Asha Rahman", "Lee Carter",
                 "Security Rule, Privacy Rule and Breach Notification Rule"):
        assert want in cover
    joined = "\n".join(pages)
    for want in ("HIPAA compliance overview", "Foundational programme elements", "Required and Addressable specifications",
                 "Addressable does not mean optional", "Rating gate", "not a legal opinion", "Specification"):
        assert want in joined.replace("\n", " ")
    word = docx_text(docx)
    assert "HIPAA compliance overview" in word and "Foundational programme elements" in word and "Asha Rahman" in word


def test_hipaa_rating_is_capped_while_a_foundational_element_is_not_met():
    from internal_auditor.risk import build_risk_register
    good = [Assessment(control_id=c.id, domain=c.domain, title=c.title, status="Met", citation_verified=True, review_status="Approved") for c in HCTRL]
    m = M.build_model(hipaa_info(), HMETA, good, [], [], HCTRL, with_charts=False)
    assert m.rating == "Satisfactory" and not m.rating_note and all(g["status"] == "Met" for g in m.foundational)
    # same 100% readiness, but risk analysis not assessed -> cannot be Satisfactory
    partial = [a for a in good if a.control_id != "164.308(a)(1)(ii)(A)"]
    m2 = M.build_model(hipaa_info(), HMETA, partial, [], [], HCTRL, with_charts=False)
    assert m2.score >= 0.85 and m2.rating == "Needs improvement" and "164.308(a)(1)(ii)(A)" in m2.rating_note
    assert next(g for g in m2.foundational if g["id"] == "164.308(a)(1)(ii)(A)")["status"] == "Not assessed"


def test_required_gaps_and_requirement_table(tmp_path):
    _, pool, risks = hipaa_inputs(tmp_path)
    m = M.build_model(hipaa_info(), HMETA, pool, risks, [], HCTRL, with_charts=False)
    assert m.required_gaps == sum(1 for f in m.findings if f.requirement == "Required") > 0
    kinds = {row[0]: row for row in m.req_table}
    assert set(kinds) == {"Required", "Addressable", "Standard"} and sum(r[2] for r in m.req_table) == len(pool)
    assert kinds["Required"][1] + kinds["Addressable"][1] + kinds["Standard"][1] == 80


def test_self_attested_form_documents_are_counted_and_labelled():
    docs = [{"name": "form-20261118-101010-abc123.md", "chunks": 1, "words": 100},
            {"name": "form-20261118-101010-abc123-settings.txt", "chunks": 1, "words": 5}, {"name": "policy.md", "chunks": 2, "words": 9}]
    m = M.build_model(hipaa_info(), HMETA, [], [], docs, HCTRL, with_charts=False)
    assert m.self_attested == 1
    assert [d["warning"] for d in m.docs] == ["Self-attested form response", "Supplied through the evidence form", ""]


def test_report_lists_file_hashes_settings_and_the_risk_analysis_disclaimer(tmp_path):
    _, pool, risks = hipaa_inputs(tmp_path)
    docs = [{"name": "policy.md", "chunks": 2, "words": 90, "sha256": "ab" * 32}]
    pdf, docx = M.build_reports(hipaa_info(), HMETA, pool, risks, docs, HCTRL, run_settings="temperature 0.1; context 8192")
    text = " ".join(pdf_text(pdf)).replace("\n", " ")
    assert "abababababababab" in text and "temperature 0.1; context 8192" in text and "not the enterprise-wide risk analysis" in text
    assert "abababababababab" in docx_text(docx) and "design evidence" in docx_text(docx)


def test_non_hipaa_reports_are_unchanged(tmp_path):
    _, pool, risks = demo_inputs(tmp_path)
    m = M.build_model(INFO, META, pool, risks, [], CONTROLS, with_charts=False)
    assert not m.is_hipaa and not m.req_table and not m.rating_note and "HIPAA" not in m.cover_title
    assert M.sections_for(m) == M.SECTIONS and M.limits_for(m) == M.LIMITS
