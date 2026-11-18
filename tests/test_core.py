from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

from conftest import FakeLLM
from grc_copilot.assessor import apply_citation_lock, assess_control, quote_supported
from grc_copilot.config import ROOT, Settings
from grc_copilot.frameworks import load_framework
from grc_copilot.ingest import chunk_text
from grc_copilot.pipeline import run
from grc_copilot.risk import build_risk_register, level_for
from grc_copilot.schemas import Assessment, Chunk, LLMAssessment
from grc_copilot.scoring import summarize
from grc_copilot.store import ChunkStore, Hit

S = Settings()


def _hit(text, cid=1, doc="policy.md", page=None):
    return Hit(Chunk(chunk_id=cid, doc=doc, page=page, text=text), 1.0, None, 5.0)


# ------------------------------------------------------------- framework
def test_framework_has_93_unique_controls():
    meta, controls = load_framework(S.framework_path)
    assert len(controls) == 93
    assert len({c.id for c in controls}) == 93
    assert {c.domain for c in controls} == {"Organizational controls", "People controls", "Physical controls", "Technological controls"}
    assert all(1 <= c.impact <= 5 for c in controls)


# -------------------------------------------------------------- chunking
def test_chunking_keeps_all_words_and_respects_size():
    text = "\n\n".join(" ".join(f"w{p}_{i}" for i in range(60)) + "." for p in range(12))
    chunks = chunk_text(text, size=100, overlap=20)
    assert len(chunks) > 1
    assert all(len(c.split()) <= 100 for c in chunks)
    joined = " ".join(chunks)
    assert all(f"w{p}_{i}" in joined for p in range(12) for i in range(60))


def test_chunking_splits_one_giant_paragraph_by_sentence():
    text = " ".join(f"Sentence number {i} says something useful here." for i in range(100))
    chunks = chunk_text(text, size=50, overlap=10)
    assert len(chunks) > 5 and all(len(c.split()) <= 50 for c in chunks)


# ---------------------------------------------------------- citation lock
def test_quote_supported_exact_fuzzy_and_fabricated():
    text = "All administrator accounts must use multi-factor authentication. Passwords must be 14 characters long."
    assert quote_supported("All administrator accounts must use multi-factor authentication.", text)
    assert quote_supported("all administrator accounts must use multifactor authentication", text)  # case / punctuation
    assert quote_supported("All admin accounts must use multi-factor authentication.", text, threshold=0.8)  # light fuzz
    assert not quote_supported("Encryption keys are rotated every month by the security team.", text)
    assert not quote_supported("Passwords must", text)  # too short to be evidence


def test_grounded_verdict_keeps_citation_and_page():
    ctrl = load_framework(S.framework_path)[1][0]
    h = _hit("Intro line. The policy is approved by the Board and reviewed once a year. Other text.", cid=7, page=3)
    parsed = LLMAssessment(chunk_id=7, quote="The policy is approved by the Board and reviewed once a year.",
                           status="Met", reasoning="ok", missing="")
    a = apply_citation_lock(ctrl, parsed, [h], S)
    assert a.status == "Met" and a.citation_verified and a.page == 3 and a.source_doc == "policy.md"


def test_fabricated_quote_is_downgraded_to_no_evidence():
    ctrl = load_framework(S.framework_path)[1][0]
    h = _hit("Employees must lock their screens when leaving a desk for any reason.")
    parsed = LLMAssessment(chunk_id=1, quote="The board approves the policy every single quarter without exception",
                           status="Met", reasoning="trust me", missing="")
    a = apply_citation_lock(ctrl, parsed, [h], S)
    assert a.status == "No evidence" and a.needs_review and not a.citation_verified
    assert "could not be found" in a.note


def test_wrong_chunk_id_is_recovered_if_quote_exists_elsewhere():
    ctrl = load_framework(S.framework_path)[1][0]
    hits = [_hit("Unrelated paragraph about parking permits and bicycle racks.", cid=1),
            _hit("Backups are encrypted and stored in a separate cloud region every night.", cid=2)]
    parsed = LLMAssessment(chunk_id=1, quote="Backups are encrypted and stored in a separate cloud region",
                           status="Partially met", reasoning="ok", missing="restore test")
    a = apply_citation_lock(ctrl, parsed, hits, S)
    assert a.status == "Partially met" and a.chunk_id == 2 and a.citation_verified


def test_no_hits_means_no_evidence_and_no_llm_call():
    llm = FakeLLM()
    ctrl = load_framework(S.framework_path)[1][0]
    a = assess_control(ctrl, [], llm, S)
    assert a.status == "No evidence" and llm.calls == []


def test_llm_failure_never_crashes_and_is_flagged():
    class Boom(FakeLLM):
        def chat_json(self, *a, **k):
            raise RuntimeError("model exploded")

    ctrl = load_framework(S.framework_path)[1][0]
    a = assess_control(ctrl, [_hit("some text here that is long enough to matter")], Boom(), S)
    assert a.status == "No evidence" and a.needs_review and "failed" in a.note


# ----------------------------------------------------------- risk & score
def test_risk_levels_and_register_order():
    assert [level_for(s) for s in (25, 20, 19, 12, 11, 6, 5, 1)] == ["Critical", "Critical", "High", "High", "Medium", "Medium", "Low", "Low"]
    _, controls = load_framework(S.framework_path)
    by = {c.id: c for c in controls}
    rows = [
        Assessment(control_id="8.7", domain=by["8.7"].domain, title=by["8.7"].title, status="No evidence"),    # 4*5=20
        Assessment(control_id="5.5", domain=by["5.5"].domain, title=by["5.5"].title, status="Partially met"),  # 3*2=6
        Assessment(control_id="5.1", domain=by["5.1"].domain, title=by["5.1"].title, status="Met"),            # no risk
        Assessment(control_id="8.2", domain=by["8.2"].domain, title=by["8.2"].title, status="Not met"),        # 5*5=25
    ]
    risks = build_risk_register(rows, controls, today=date(2026, 1, 1))
    assert [r.control_id for r in risks] == ["8.2", "8.7", "5.5"]
    assert [r.risk_id for r in risks] == ["R-001", "R-002", "R-003"]
    assert risks[0].level == "Critical" and risks[0].due_date == "2026-01-31"
    assert risks[2].level == "Medium" and risks[2].due_date == "2026-04-01"


def test_summary_scoring():
    mk = lambda i, s, d: Assessment(control_id=i, domain=d, title="t", status=s)
    s = summarize([mk("1", "Met", "A"), mk("2", "Partially met", "A"), mk("3", "No evidence", "B"), mk("4", "Not met", "B")])
    assert s["overall"]["score"] == pytest.approx(0.375)
    assert [d["score"] for d in s["domains"]] == [0.75, 0.0]


# ------------------------------------------------------------- retrieval
def test_store_finds_relevant_chunk_hybrid_and_keyword_only():
    chunks = [
        Chunk(chunk_id=0, doc="a.md", text="Backups are encrypted and stored in a separate cloud region and restore tested yearly."),
        Chunk(chunk_id=1, doc="a.md", text="Visitors must sign in at reception and wear a badge at all times in the office."),
        Chunk(chunk_id=2, doc="a.md", text="Passwords must be fourteen characters long and multi factor authentication is mandatory."),
    ]
    q = "Information backup. Backup copies of information are kept and regularly tested. backup job logs restore test"
    for llm in (FakeLLM(), None):
        store = ChunkStore(llm, S)
        store.add(chunks)
        if llm:
            store.build_vectors()
        top = store.search(q, k=2)
        assert top and top[0].chunk.chunk_id == 0


# ---------------------------------------------------------------- end to end
@pytest.mark.parametrize("behaviour,expect_verified", [("grounded", True), ("hallucinate", False)])
def test_pipeline_end_to_end(tmp_path, behaviour, expect_verified):
    res = run(ROOT / "sample_docs", S, FakeLLM(behaviour), out_dir=tmp_path, limit=12)
    assert len(res.assessments) == 12
    if expect_verified:
        assert any(a.citation_verified for a in res.assessments)
    else:  # fabricated quotes must never survive the lock
        assert not any(a.citation_verified for a in res.assessments)
        assert all(a.status == "No evidence" for a in res.assessments)
    for p in res.paths.values():
        assert Path(p).exists()
    wb = load_workbook(res.paths["xlsx"])
    assert wb.sheetnames == ["Summary", "Control Assessment", "Risk Register", "Policy Drafts", "Documents"]
    assert wb["Control Assessment"].max_row == 13
    assert "Compliance Readiness Report" in Path(res.paths["md"]).read_text()


def test_resume_skips_finished_controls(tmp_path):
    first = FakeLLM()
    run(ROOT / "sample_docs", S, first, out_dir=tmp_path, limit=5, remediate=False)
    second = FakeLLM()
    res = run(ROOT / "sample_docs", S, second, out_dir=tmp_path, limit=5, remediate=False, resume=True)
    assert len(res.assessments) == 5 and second.calls == []


def test_unknown_control_id_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        run(ROOT / "sample_docs", S, FakeLLM(), out_dir=tmp_path, control_ids=["99.9"])
