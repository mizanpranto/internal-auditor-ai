"""Pranto's GRC Proto-type: local compliance agent UI.   Run with:  streamlit run app.py"""

from __future__ import annotations

import json
import math
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from grc_copilot import theme as T
from grc_copilot.config import Settings
from grc_copilot.demo import load_demo
from grc_copilot.engine import Engine
from grc_copilot.frameworks import DEFAULT_FRAMEWORK, list_frameworks, load_framework, natural_key
from grc_copilot.ingest import ingest_folder
from grc_copilot.llm import OllamaClient
from grc_copilot.report import write_markdown, write_xlsx
from grc_copilot.audit_model import CLASSIFICATIONS, ReportInfo, build_reports
from grc_copilot.scoring import STATUS_ORDER, summarize
from grc_copilot.workspace import RISK_STATUSES, Workspace

PAGES = ["Overview", "Evidence Library", "Control Assessment", "Risk Register", "Control Library", "Settings"]

st.set_page_config(page_title=T.APP_NAME, page_icon="🛡️", layout="wide", initial_sidebar_state="auto")  # open on desktop, collapsed on phones
st.markdown(T.css_block(), unsafe_allow_html=True)


# ------------------------------------------------------------------ state
def html(s: str) -> None:
    st.markdown(s, unsafe_allow_html=True)


def show_df(data, **kw) -> None:
    """st.dataframe across Streamlit versions (width API changed in 2025)."""
    try:
        st.dataframe(data, width="stretch", **kw)
    except TypeError:
        st.dataframe(data, use_container_width=True, **kw)


def embed_html(doc: str, height: int) -> None:
    """Render a self-contained HTML document (st.iframe on new Streamlit, components.html on old)."""
    if hasattr(st, "iframe"):
        st.iframe(doc, height="content")  # sized to the content, so no dead space under the map
    else:  # Streamlit < 1.5x
        import streamlit.components.v1 as components
        components.html(doc, height=height)


def flash(kind: str, text: str) -> None:
    st.session_state.setdefault("flash", []).append((kind, text))


def show_flash() -> None:
    for kind, text in st.session_state.pop("flash", []):
        html(T.note(text, kind))


def go(page: str) -> None:
    st.session_state["nav"] = page


if "ws" not in st.session_state:
    st.session_state.ws = Workspace()
    st.session_state.settings = st.session_state.ws.load_settings()
ws: Workspace = st.session_state.ws
ws.load()  # always start from what is on disk
settings: Settings = st.session_state.settings


FRAMEWORKS = list_frameworks()
CUSTOM_FILE = ws.root / "framework.json"


def framework_path(fid: str) -> Path:
    if fid == "custom":
        return CUSTOM_FILE
    return FRAMEWORKS[fid]["path"] if fid in FRAMEWORKS else FRAMEWORKS[DEFAULT_FRAMEWORK]["path"]


def get_framework():
    fid = ws.framework_id
    if (fid == "custom" and not CUSTOM_FILE.exists()) or (fid != "custom" and fid not in FRAMEWORKS):
        ws.switch_framework(DEFAULT_FRAMEWORK)  # the saved framework file is gone: fall back
    meta, controls = load_framework(framework_path(ws.framework_id))
    overrides = ws.impact_overrides()
    for c in controls:
        c.impact = int(overrides.get(c.id, c.impact))
    return meta, controls


fw_meta, controls = get_framework()
by_id = {c.id: c for c in controls}
fw_short = FRAMEWORKS.get(ws.framework_id, {}).get("short", "Custom")


def start_demo() -> None:
    """The reference demo is ISO 27001 only, so switch to it first."""
    ws.switch_framework(DEFAULT_FRAMEWORK)
    load_demo(ws, load_framework(framework_path(DEFAULT_FRAMEWORK))[1])


@st.cache_data(ttl=8, show_spinner=False)
def _health(url: str, llm: str, embed: str) -> dict:
    return OllamaClient(Settings(ollama_url=url, llm_model=llm, embed_model=embed)).healthcheck()


@st.cache_data(show_spinner=False)
def _doc_stats(sig: tuple, folder: str, words: int, overlap: int) -> list[dict]:
    s = Settings()
    s.chunk_words, s.chunk_overlap = words, overlap
    return ingest_folder(folder, s)[1]


def get_engine() -> Engine:
    sig = (settings.ollama_url, settings.llm_model, settings.embed_model, settings.num_ctx,
           settings.temperature, settings.think, settings.top_k)
    eng = st.session_state.get("engine")
    if eng is None or st.session_state.get("engine_sig") != sig:
        eng = Engine(ws, settings, controls)
        eng.load_index()
        st.session_state.engine, st.session_state.engine_sig = eng, sig
    eng.controls = controls
    return eng


engine = get_engine()
health = _health(settings.ollama_url, settings.llm_model, settings.embed_model)
evidence = ws.list_evidence()
reviewer = ws.reviewer


def docs_info() -> list[dict]:
    sig = tuple((e["name"], e["size"], e["modified"]) for e in evidence)
    return _doc_stats(sig, str(ws.evidence_dir), settings.chunk_words, settings.chunk_overlap) if evidence else []


def run_assess(ids: list[str], label: str) -> None:
    """Run the model over `ids` with a progress bar, then refresh."""
    if not engine.is_indexed():
        st.error("Build the search index first (Evidence Library).")
        return
    try:
        engine.preflight()
    except RuntimeError as e:
        st.error(str(e))
        return
    bar = st.progress(0.0, text=f"Starting {label}")

    def prog(i: int, n: int, msg: str) -> None:
        bar.progress(min(1.0, i / max(n, 1)), text=f"{i}/{n}  {msg}")

    try:
        res = engine.assess(ids, prog)
    except Exception as e:  # noqa: BLE001
        bar.empty()
        st.error(f"Assessment stopped: {e}")
        return
    bar.empty()
    failed = sum(1 for a in res if a.note.startswith("Assessment failed"))
    flash("warn" if failed else "", f"Assessed {len(res)} control(s). They are drafts until you approve them."
          + (f" {failed} failed and were flagged for review." if failed else ""))
    st.rerun()


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    html(T.brand_html())
    html(T.sidebar_status_html(health, settings.llm_model, settings.embed_model))
    page = st.radio("Workspace", PAGES, key="nav", label_visibility="collapsed")
    fw_ids = [*FRAMEWORKS, *(["custom"] if CUSTOM_FILE.exists() else [])]
    fw_label = lambda i: "Custom (uploaded)" if i == "custom" else f"{FRAMEWORKS[i]['short']} · {FRAMEWORKS[i]['count']} controls"  # noqa: E731
    picked = st.selectbox("Framework", fw_ids, index=fw_ids.index(ws.framework_id), format_func=fw_label,
                          help="Each framework keeps its own assessments, so you can switch back and forth.")
    if picked != ws.framework_id:
        ws.switch_framework(picked)
        st.rerun()
    html('<div class="sbmodels" style="margin-top:1.6rem;font-size:.86rem">Runs on local models only. No document leaves this computer.</div>')


# ================================================================== OVERVIEW
def page_overview() -> None:
    counts = ws.counts()
    open_risks = ws.open_risks(controls)
    ready = ws.readiness()
    html(T.page_header("AI Governance & Risk Command Center", "Evidence-led compliance operations · Local-first AI · Human-approved decisions"))
    show_flash()

    if ws.state.get("demo"):
        html(T.note("Reference demo loaded: fictional sample documents with hand-written verdicts, not live model output. "
                    "Reset the workspace in Settings to start with your own documents.", "warn"))
    html(T.kpi_cards([
        ("Control library", str(len(controls)), "Available assessment objectives"),
        ("Reviewed controls", str(counts["approved"]), "Reviewer-approved assessments"),
        ("Control readiness", f"{round(ready * 100)}<small>%</small>" if ready is not None else "<small>n/a</small>", "Average of approved scores"),
        ("Open risk items", str(len(open_risks)), "Items requiring follow-up"),
    ]))

    if not evidence and not counts["assessed"]:
        html(T.empty_state("Start with evidence", "Upload your policies and procedures, or explore a populated workspace built from fictional sample documents."))
        b1, b2, _ = st.columns([0.9, 1.3, 4])
        b1.button("Add evidence", type="primary", on_click=go, args=("Evidence Library",))
        if b2.button("Load reference demo"):
            start_demo()
            st.rerun()

    left, right = st.columns([2.05, 1], gap="large")
    with left:
        html(T.panel("AI workflow routing map", "Evidence-to-decision pipeline · Select a workspace from the navigation to run each stage"))
        state = {"docs": len(evidence), "chunks": engine.index_info.get("chunks", 0) if engine.is_indexed() else 0,
                 "indexed": engine.is_indexed(), "controls": len(controls), "assessed": counts["assessed"],
                 "risks": len(open_risks), "approved": counts["approved"], "framework": fw_short}
        embed_html(T.workflow_map_html(state), 380)
    with right:
        html(T.section("Agent & model health", "Local runtime status"))
        chip = T.chip("Ollama reachable", "ok") if health["ok"] else T.chip("Ollama not running", "bad")
        model_line = f'<div class="model-line">Model: <code>{T.esc(settings.llm_model)}</code></div>'
        if not health["ok"]:
            msg = "Open the Ollama app or run: ollama serve"
        elif health["llm"] and health["embed"]:
            msg = "Selected model available"
        else:
            missing = [m for m, ok in ((settings.llm_model, health["llm"]), (settings.embed_model, health["embed"])) if not ok]
            msg = "Missing: " + ", ".join(missing) + ". Run: ollama pull " + missing[0]
        html(chip + model_line + f'<div class="kv">{T.esc(msg)}</div><div class="hr"></div>')

        html(T.section("Workspace activity"))
        html(
            f'<div class="stat-line"><span>📁 Evidence documents</span><b>{len(evidence)}</b></div>'
            f'<div class="stat-line"><span>🧩 Controls awaiting assessment</span><b>{len(controls) - counts["assessed"]}</b></div>'
            f'<div class="stat-line"><span>📝 Drafts awaiting review</span><b>{counts["draft"]}</b></div>'
            f'<div class="stat-line"><span>⚠️ Open risk items</span><b>{len(open_risks)}</b></div><div class="hr"></div>'
        )
        html(T.section("Human-in-the-loop"))
        html('<div class="kv">AI suggestions remain drafts until a reviewer validates the evidence and saves an approved assessment.</div>')

    html('<div style="height:1.1rem"></div>')
    # next step + detail row
    c1, c2 = st.columns(2, gap="large")
    with c1:
        approved = ws.approved()
        pool = approved or list(ws.assessments().values())
        html(T.panel("Readiness by domain", "Based on approved assessments" if approved else "Based on unreviewed drafts. Approve them to make this official."
                     if pool else "Appears once controls are assessed", T.domain_bars(pool) if pool else ""))
    with c2:
        if open_risks:
            rows = "".join(
                f'<div class="stat-line"><span>{T.chip(r.level, {"Critical": "no", "High": "no", "Medium": "half", "Low": "ok"}[r.level], True)} '
                f'&nbsp;{T.esc(r.control_id)} {T.esc(r.title)}</span><b>{r.score}</b></div>' for r in open_risks[:6])
            html(T.panel("Top open risks", "Highest scores first. Open the Risk Register to plan the fix.", rows))
        else:
            step = ("Upload evidence in the Evidence Library." if not evidence else
                    "Build the search index." if not engine.is_indexed() else
                    "Run an assessment in Control Assessment." if not counts["assessed"] else
                    "Review and approve the drafts in Control Assessment." if counts["draft"] else
                    "No open risks. Keep evidence up to date.")
            html(T.panel("Next step", step))


# ============================================================ EVIDENCE LIBRARY
def page_evidence() -> None:
    html(T.page_header("Evidence Library", "Upload policies, procedures and evidence. Files stay in the workspace folder on this computer."))
    show_flash()
    n = st.session_state.get("up_n", 0)
    files = st.file_uploader("Drop policies, procedures or evidence here", type=["pdf", "docx", "txt", "md"],
                             accept_multiple_files=True, key=f"up_{n}")
    if files:
        for f in files:
            ws.add_evidence(f.name, f.getvalue())
        st.session_state.up_n = n + 1
        flash("", f"Added {len(files)} document(s). Build the index so the agent can search them.")
        st.rerun()

    if not evidence:
        html(T.empty_state("No evidence yet", "Add a PDF, DOCX, TXT or Markdown file above. Scanned PDFs need OCR first."))
        if st.button("Load reference demo instead"):
            start_demo()
            st.rerun()
        return

    stats = {d["name"]: d for d in docs_info()}
    df = pd.DataFrame([{
        "Document": e["name"], "Size (KB)": round(e["size"] / 1024, 1), "Added": e["modified"],
        "Passages": stats.get(e["name"], {}).get("chunks", 0), "Words": stats.get(e["name"], {}).get("words", 0),
        "Warning": stats.get(e["name"], {}).get("warning", "") or ("unsupported or unreadable" if e["name"] not in stats else ""),
    } for e in evidence])
    html(T.section(f"{len(evidence)} document(s)"))
    show_df(df, hide_index=True)

    rm = st.multiselect("Remove documents", [e["name"] for e in evidence], placeholder="Select documents to remove")
    if rm and st.button(f"Remove {len(rm)} selected"):
        for name in rm:
            ws.remove_evidence(name)
        st.rerun()

    html('<div class="hr"></div>')
    html(T.section("Search index", "The agent only sees passages from this index. Rebuild it after adding or removing documents."))
    ok = engine.is_indexed()
    stale = bool(engine.index_info.get("fingerprint")) and not ok
    if ok:
        info = engine.index_info
        html(T.chip(f"Index ready · {info['chunks']} passages · {info['mode']} search", "ok"))
        if info.get("note"):
            html('<div style="height:.6rem"></div>' + T.note(info["note"], "warn"))
    else:
        html(T.chip("Out of date: evidence changed" if stale else "Not indexed", "half" if stale else "idle"))
    html('<div style="height:.75rem"></div>')
    use_emb = st.checkbox("Use embeddings (better matching, needs the embedding model)", value=True)
    if st.button("Rebuild search index" if ok else "Build search index", type="primary"):
        bar = st.progress(0.0, text="Starting")
        try:
            info = engine.build_index(use_emb, lambda i, n_, m: bar.progress(i / max(n_, 1), text=m))
        except Exception as e:  # noqa: BLE001
            bar.empty()
            st.error(str(e))
        else:
            bar.empty()
            flash("warn" if info["note"] else "", info["note"] or f"Indexed {info['chunks']} passages from {len(info['docs'])} document(s).")
            st.rerun()

    if ok and engine.store:
        with st.expander("Preview passages the agent will search"):
            doc = st.selectbox("Document", sorted({c.doc for c in engine.store.chunks}))
            for c in [c for c in engine.store.chunks if c.doc == doc][:15]:
                where = f"Passage {c.chunk_id}" + (f" · page {c.page}" if c.page else "")
                html(f'<div class="pass"><small>{T.esc(where)}</small>{T.esc(c.text)}</div>')


# ========================================================== CONTROL ASSESSMENT
def page_assessment() -> None:
    html(T.page_header("Control Assessment", "The agent drafts a verdict for each control. Nothing counts until a reviewer approves it."))
    show_flash()
    assessed = ws.assessments()

    if not evidence and not assessed:
        html(T.empty_state("No evidence to assess", "Add documents in the Evidence Library first."))
        st.button("Open Evidence Library", type="primary", on_click=go, args=("Evidence Library",))
        return

    html(T.section("Run the agent", "Each control is one model call. Results are saved as they arrive, so you can stop at any time."))
    if not engine.is_indexed():
        html(T.note("The search index is missing or out of date. Build it in the Evidence Library before running an assessment.", "warn"))
        st.button("Open Evidence Library", on_click=go, args=("Evidence Library",), key="to_ev")
    else:
        todo = [c.id for c in controls if c.id not in assessed]
        scope = st.radio("Scope", ["Next 10 unassessed", "All unassessed", "Selected controls"], horizontal=True, label_visibility="collapsed")
        ids: list[str] = []
        if scope == "Next 10 unassessed":
            ids = todo[:10]
        elif scope == "All unassessed":
            ids = todo
        else:
            ids = st.multiselect("Controls", [c.id for c in controls], format_func=lambda i: f"{i}  {by_id[i].title}",
                                 placeholder="Choose controls to assess or re-run")
            if any(i in assessed and assessed[i].review_status != "Draft" for i in ids):
                st.caption("Re-running a reviewed control replaces its verdict and returns it to Draft.")
        if st.button(f"Run assessment ({len(ids)} control{'s' if len(ids) != 1 else ''})", type="primary", disabled=not ids):
            run_assess(ids, "assessment")
        if not todo and scope != "Selected controls":
            st.caption("Every control already has a result.")

    if not assessed:
        return

    html('<div class="hr"></div>')
    counts = ws.counts()
    html(T.section("Review queue", f"{counts['assessed']} assessed · {counts['approved']} approved · {counts['draft']} draft · {counts['rejected']} rejected"
                   + (f" · {counts['needs_review']} flagged" if counts["needs_review"] else "")))

    name = st.text_input("Reviewer name", value=reviewer, placeholder="Your name. Recorded with every approval.")
    if name.strip() != reviewer:
        ws.save_settings(settings, reviewer=name)
        st.rerun()
    can_review = bool(reviewer)
    if not can_review:
        html(T.note("Enter your name to approve or reject. Every decision is recorded with it.", "warn"))

    cols = st.columns([1.1, 1.4, 1.5, 1.6])
    f_review = cols[0].selectbox("Review state", ["All", "Draft", "Approved", "Rejected"])
    f_status = cols[1].multiselect("Verdict", STATUS_ORDER, placeholder="Any verdict")
    domains = sorted({a.domain for a in assessed.values()})
    f_domain = cols[2].selectbox("Domain", ["All", *domains])
    f_query = cols[3].text_input("Search", placeholder="Control id or keyword")

    items = [a for _, a in sorted(assessed.items(), key=lambda kv: natural_key(kv[0]))]
    items = [a for a in items
             if (f_review == "All" or a.review_status == f_review)
             and (not f_status or a.status in f_status)
             and (f_domain == "All" or a.domain == f_domain)
             and (not f_query or f_query.lower() in f"{a.control_id} {a.title} {a.quote} {a.reasoning}".lower())]

    bulk = sum(1 for a in assessed.values() if a.review_status == "Draft" and a.citation_verified and not a.needs_review)
    if bulk and st.button(f"Approve {bulk} citation-verified draft(s)", disabled=not can_review,
                          help="Only drafts whose quote was found word for word in your documents. Flagged drafts stay for manual review."):
        n = ws.approve_all_verified(reviewer)
        flash("", f"Approved {n} citation-verified assessment(s).")
        st.rerun()

    per = 6
    pages = max(1, math.ceil(len(items) / per))
    pg = st.number_input("Page", min_value=1, max_value=pages, value=1, step=1) if pages > 1 else 1
    st.caption(f"Showing {min(len(items), (pg - 1) * per + 1)}–{min(len(items), pg * per)} of {len(items)}")
    if not items:
        html(T.empty_state("Nothing matches these filters", "Clear a filter to see more controls."))

    for a in items[(pg - 1) * per: pg * per]:
        cid = a.control_id
        html(T.assessment_card(a, by_id.get(cid)))
        b = st.columns([1, 1, 1, 3])
        if a.review_status == "Draft":
            if b[0].button("Approve", key=f"ap_{cid}", type="primary", disabled=not can_review):
                ws.review(cid, "approve", reviewer)
                st.rerun()
            if b[1].button("Reject", key=f"rj_{cid}", disabled=not can_review):
                ws.review(cid, "reject", reviewer)
                st.rerun()
        elif b[0].button("Reopen", key=f"ro_{cid}"):
            ws.review(cid, "reopen", reviewer)
            st.rerun()
        if b[2].button("Re-run", key=f"rr_{cid}", help="Ask the model again for this control"):
            run_assess([cid], cid)
        with st.expander("Change verdict or add a note"):
            new = st.selectbox("Verdict", STATUS_ORDER, index=STATUS_ORDER.index(a.status), key=f"ns_{cid}")
            note = st.text_input("Reviewer note", value=a.reviewer_note, key=f"nt_{cid}")
            if st.button("Save and approve", key=f"sv_{cid}", disabled=not can_review):
                try:
                    ws.review(cid, "override", reviewer, note, new)
                except ValueError as e:
                    st.error(str(e))
                else:
                    st.rerun()


# ============================================================== RISK REGISTER
def approved_pool(include_drafts: bool):
    pool = [a for a in ws.assessments().values()
            if a.review_status == "Approved" or (include_drafts and a.review_status == "Draft")]
    pool.sort(key=lambda a: natural_key(a.control_id))
    return pool, ws.risk_register(controls, include_drafts)


def build_exports(include_drafts: bool) -> tuple[bytes, bytes] | None:
    pool, risks = approved_pool(include_drafts)
    if not pool:
        return None
    meta = {**fw_meta, "generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "model": settings.llm_model,
            "retrieval": engine.index_info.get("mode", "n/a")}
    out = ws.root / "exports"
    summary = summarize(pool)
    x = write_xlsx(out / "compliance_report.xlsx", meta, pool, risks, summary, docs_info())
    m = write_markdown(out / "compliance_report.md", meta, pool, risks, summary, docs_info())
    return x.read_bytes(), m.read_bytes()


def audit_report_section() -> None:
    """Cover-page details, then a PDF and a Word internal-audit report built from the approved assessments."""
    html('<div class="hr"></div>')
    html(T.section("Internal audit report", "A formatted report with a cover page, executive summary, findings and action plan. PDF and Word."))
    saved = ReportInfo.from_dict(ws.report_info())
    if not saved.assessor_name:
        saved.assessor_name = reviewer
    with st.form("report_details"):
        c1, c2, c3 = st.columns(3)
        company = c1.text_input("Company name", saved.company, placeholder="Organisation being assessed")
        assessor = c2.text_input("Assessment done by", saved.assessor_name, placeholder="Name of the assessor")
        a_title = c3.text_input("Assessor role", saved.assessor_title, placeholder="e.g. GRC Analyst")
        c1, c2, c3 = st.columns(3)
        approver = c1.text_input("Approved by", saved.approver_name, placeholder="e.g. Head of Internal Audit")
        ap_title = c2.text_input("Approver role", saved.approver_title)
        contact = c3.text_input("Auditee contact", saved.auditee_contact, placeholder="Person at the company")
        c1, c2, c3 = st.columns(3)
        scope = c1.text_input("Scope", saved.scope, placeholder="e.g. ISMS, head office")
        period = c2.text_input("Assessment period", saved.assessment_period, placeholder="e.g. 1 Nov to 14 Nov 2026")
        ref = c3.text_input("Engagement reference", saved.engagement_ref, placeholder="e.g. IA-2026-014")
        c1, c2, c3 = st.columns(3)
        title = c1.text_input("Report title", saved.report_title, placeholder="Leave empty for the default title")
        rdate = c2.text_input("Report date", saved.report_date, placeholder="Leave empty for today")
        version = c3.text_input("Version", saved.version or "1.0")
        c1, c2, c3 = st.columns(3)
        cls = c1.selectbox("Classification", CLASSIFICATIONS, index=CLASSIFICATIONS.index(saved.classification) if saved.classification in CLASSIFICATIONS else 0)
        dist = c2.text_input("Distribution", saved.distribution, placeholder="e.g. Audit Committee, CISO")
        dev = c3.text_input("Agent developed by", saved.developed_by or "Pranto")
        logo_up = st.file_uploader("Company logo (optional, PNG or JPG)", type=["png", "jpg", "jpeg"])
        if st.form_submit_button("Save report details"):
            ws.save_report_info(ReportInfo(company, scope, title, ref, assessor, a_title, approver, ap_title, contact,
                                           period, rdate, version, cls, dist, dev).to_dict())
            if logo_up is not None:
                try:
                    ws.save_logo(logo_up.getvalue())
                except Exception:
                    st.error("That image could not be read. Use a PNG or JPG file.")
            st.success("Saved. These details appear on the cover page.")
    if ws.logo():
        c1, c2 = st.columns([5, 1])
        c1.caption("A company logo is saved and will appear on the cover page.")
        if c2.button("Remove logo"):
            ws.save_logo(None)
            st.rerun()

    inc = st.checkbox("Include unreviewed drafts in the report", value=False, key="audit_inc")
    if st.button("Generate audit report", type="primary"):
        pool, risks = approved_pool(inc)
        if not pool:
            st.warning("Nothing approved yet. Approve assessments first, or include drafts.")
        else:
            info = ReportInfo.from_dict(ws.report_info())
            if not info.assessor_name:
                info.assessor_name = reviewer
            meta = {**fw_meta, "generated": datetime.now().strftime("%Y-%m-%d %H:%M")}
            with st.spinner("Building the report..."):
                st.session_state["audit"] = build_reports(info, meta, pool, risks, docs_info(), controls, llm=settings.llm_model,
                                                          retrieval=engine.index_info.get("mode", "n/a"), logo=ws.logo())
    if "audit" in st.session_state:
        pdf, docx = st.session_state["audit"]
        stem = "internal_audit_report"
        d1, d2, _ = st.columns([1, 1, 3])
        d1.download_button("Download PDF", pdf, f"{stem}.pdf", "application/pdf")
        d2.download_button("Download Word", docx, f"{stem}.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def page_risks() -> None:
    html(T.page_header("Risk Register", "Gaps in approved assessments, scored by likelihood and impact."))
    show_flash()
    drafts = st.toggle("Include unreviewed drafts", value=False, help="Off by default: only reviewer-approved assessments create risks.")
    risks = ws.risk_register(controls, drafts)
    if not risks:
        msg = ("Approve some assessments in Control Assessment. Gaps (anything other than Met) become risks here."
               if not ws.approved() else "Every approved control is Met, so there are no risks to register.")
        html(T.empty_state("No risks yet", msg))
        st.button("Open Control Assessment", on_click=go, args=("Control Assessment",))
        if ws.approved():
            audit_report_section()
        return

    by_level = {lvl: sum(1 for r in risks if r.level == lvl) for lvl in ("Critical", "High", "Medium", "Low")}
    html(T.kpi_cards([(lvl, str(n), c) for (lvl, n), c in zip(by_level.items(), ["Fix within 30 days", "Fix within 60 days", "Fix within 90 days", "Fix within 180 days"])]))

    l, r_ = st.columns([1, 1.2], gap="large")
    with l:
        html(T.section("Heat map", "Number of risks at each likelihood and impact"))
        html(T.heatmap_html(risks))
    with r_:
        html(T.section("Remediation drafting", "The model drafts an owner, plan and policy text for each risk. Review before use."))
        todo = [x for x in risks if not ws.state["risks"].get(x.control_id, {}).get("action_plan")]
        use_ai = st.checkbox("Use the local model (otherwise a fast template)", value=health["ok"] and health["llm"])
        if st.button(f"Draft plans for {len(todo)} risk(s)", type="primary", disabled=not todo):
            if use_ai:
                try:
                    engine.preflight()
                except RuntimeError as e:
                    st.error(str(e))
                    st.stop()
            bar = st.progress(0.0, text="Drafting")
            for i, rk in enumerate(todo, 1):
                bar.progress((i - 1) / len(todo), text=f"{i}/{len(todo)}  {rk.control_id} {rk.title}")
                engine.remediate(rk, use_llm=use_ai)
            bar.empty()
            flash("", f"Drafted plans for {len(todo)} risk(s).")
            st.rerun()

    html('<div class="hr"></div>')
    df = pd.DataFrame([{
        "Risk": r.risk_id, "Control": f"{r.control_id} {r.title}", "Level": r.level, "Score": r.score,
        "Treatment": r.treatment, "Owner": r.owner_role, "Due": r.due_date, "Gap": r.gap,
    } for r in risks])
    show_df(df, hide_index=True)

    html(T.section("Edit a risk"))
    pick = st.selectbox("Risk", [r.risk_id for r in risks], format_func=lambda i: next(f"{x.risk_id}  {x.title}  ({x.level})" for x in risks if x.risk_id == i))
    rk = next(x for x in risks if x.risk_id == pick)
    html(T.note(f"{rk.control_id} {rk.title} · {rk.status} · likelihood {rk.likelihood} × impact {rk.impact} = {rk.score} ({rk.level}). Gap: {rk.gap}"))
    with st.form(f"risk_{rk.control_id}"):
        c1, c2, c3 = st.columns(3)
        treat = c1.selectbox("Treatment status", RISK_STATUSES, index=RISK_STATUSES.index(rk.treatment) if rk.treatment in RISK_STATUSES else 0)
        owner = c2.text_input("Owner (role)", value=rk.owner_role)
        try:
            due_val = date.fromisoformat(rk.due_date)
        except ValueError:
            due_val = date.today()
        due = c3.date_input("Target date", value=due_val)
        plan = st.text_area("Remediation plan", value=rk.action_plan, height=110)
        pol = st.text_area("Draft policy statement (review before use)", value=rk.policy_snippet, height=90)
        if st.form_submit_button("Save changes", type="primary"):
            ws.update_risk(rk.control_id, treatment=treat, owner_role=owner, due_date=due.isoformat(), action_plan=plan, policy_snippet=pol)
            ws.log(f"Updated risk {rk.risk_id}: {treat}")
            flash("", f"Saved {rk.risk_id}.")
            st.rerun()
    if st.button("Draft this plan with the model", key=f"ai_{rk.control_id}"):
        try:
            engine.preflight()
            engine.remediate(rk, use_llm=True)
        except RuntimeError as e:
            st.error(str(e))
        else:
            st.rerun()

    html('<div class="hr"></div>')
    html(T.section("Export", "Excel workbook and Markdown file built from reviewer-approved assessments."))
    inc = st.checkbox("Also include unreviewed drafts in the export", value=False)
    if st.button("Prepare reports"):
        res = build_exports(inc)
        if res is None:
            st.warning("Nothing approved yet. Approve assessments first, or include drafts.")
        else:
            st.session_state["exports"] = res
    if "exports" in st.session_state:
        x, m = st.session_state["exports"]
        d1, d2, _ = st.columns([1, 1, 3])
        d1.download_button("Download Excel", x, "compliance_report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        d2.download_button("Download Markdown", m, "compliance_report.md", "text/markdown")
    audit_report_section()


# ============================================================== CONTROL LIBRARY
def page_library() -> None:
    html(T.page_header("Control Library", f"{fw_meta.get('framework', 'Framework')} · {len(controls)} controls. Summaries are paraphrased, not the text of the standard."))
    show_flash()
    assessed = ws.assessments()
    c1, c2 = st.columns([1, 2])
    dom = c1.selectbox("Domain", ["All", *sorted({c.domain for c in controls})])
    q = c2.text_input("Search", placeholder="Control id or keyword")
    view = [c for c in controls if (dom == "All" or c.domain == dom) and
            (not q or q.lower() in f"{c.id} {c.title} {c.summary} {c.evidence_hint}".lower())]
    df = pd.DataFrame([{
        "ID": c.id, "Domain": c.domain.replace(" controls", ""), "Control": c.title, "What it requires": c.summary,
        "Typical evidence": c.evidence_hint, "Impact": c.impact,
        "Result": (assessed[c.id].status + (" ✓" if assessed[c.id].review_status == "Approved" else "")) if c.id in assessed else "Not assessed",
    } for c in view])
    st.caption("Impact (1 to 5) feeds the risk score. Double-click a value to tune it for your organisation, then save.")
    edited = st.data_editor(df, hide_index=True, key="lib_editor", disabled=[c for c in df.columns if c != "Impact"],
                            column_config={"Impact": st.column_config.NumberColumn(min_value=1, max_value=5, step=1)})
    changed = {r["ID"]: int(r["Impact"]) for _, r in edited.iterrows() if int(r["Impact"]) != by_id[r["ID"]].impact}
    if st.button(f"Save {len(changed)} impact change(s)", disabled=not changed, type="primary"):
        ws.impact_overrides().update(changed)
        ws.log(f"Changed impact weight for {len(changed)} control(s)")
        st.rerun()

    html('<div class="hr"></div>')
    html(T.section("Add your own framework", "Switch between the bundled frameworks in the sidebar. To add another, upload a JSON file shaped like frameworks/iso27001_2022.json. It is kept separately, so nothing else is lost."))
    up = st.file_uploader("Framework JSON", type=["json"], key="fw_up")
    if up is not None:
        try:
            with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as tmp:
                tmp.write(up.getvalue())
            meta, new = load_framework(tmp.name)
            if not new:
                raise ValueError("The file contains no controls.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Not a valid framework file: {e}")
        else:
            st.success(f"{meta.get('framework', up.name)}: {len(new)} controls")
            replacing = CUSTOM_FILE.exists()
            if st.button("Use this framework" + (" (replaces the earlier custom one)" if replacing else "")):
                if ws.framework_id == "custom":
                    ws.clear_assessments()
                    ws.state["impact"] = {}
                else:
                    ws.forget_framework("custom")
                CUSTOM_FILE.write_bytes(up.getvalue())
                ws.switch_framework("custom")
                st.rerun()


# ================================================================== SETTINGS
def page_settings() -> None:
    html(T.page_header("Settings", "Choose the local models and how the agent behaves. Everything is saved in the workspace folder."))
    show_flash()
    installed = health.get("models", [])
    with st.form("settings"):
        url = st.text_input("Ollama URL", settings.ollama_url)
        c1, c2 = st.columns(2)
        llm_opts = installed if settings.llm_model in installed else [settings.llm_model, *installed]
        llm = c1.selectbox("Chat model", llm_opts, index=llm_opts.index(settings.llm_model))
        emb_opts = installed if settings.embed_model in installed else [settings.embed_model, *installed]
        emb = c2.selectbox("Embedding model", emb_opts, index=emb_opts.index(settings.embed_model))
        c3, c4, c5 = st.columns(3)
        temp = c3.slider("Temperature", 0.0, 1.0, float(settings.temperature), 0.05, help="Low values give consistent audit-style answers.")
        ctx = c4.selectbox("Context window", [4096, 8192, 16384, 32768], index=[4096, 8192, 16384, 32768].index(settings.num_ctx) if settings.num_ctx in [4096, 8192, 16384, 32768] else 1)
        topk = c5.slider("Excerpts per control", 2, 8, int(settings.top_k))
        think = st.toggle("Thinking mode (slower, can help on hard judgements)", value=settings.think)
        if st.form_submit_button("Save settings", type="primary"):
            settings.ollama_url, settings.llm_model, settings.embed_model = url.strip(), llm, emb
            settings.temperature, settings.num_ctx, settings.top_k, settings.think = temp, int(ctx), int(topk), think
            ws.save_settings(settings)
            _health.clear()
            flash("", "Settings saved.")
            st.rerun()

    html('<div class="hr"></div>')
    html(T.section("Connection"))
    if health["ok"]:
        html(T.chip("Ollama reachable", "ok") + "&nbsp; " + T.chip(f"chat model {'found' if health['llm'] else 'missing'}", "ok" if health["llm"] else "bad", True)
             + "&nbsp; " + T.chip(f"embedding model {'found' if health['embed'] else 'missing'}", "ok" if health["embed"] else "bad", True))
        if not health["llm"]:
            html(T.note(f"Run in a terminal: ollama pull {settings.llm_model}", "warn"))
        if not health["embed"]:
            html(T.note(f"Run in a terminal: ollama pull {settings.embed_model}", "warn"))
    else:
        html(T.chip("Ollama not running", "bad") + T.note("Open the Ollama app or run: ollama serve", "warn"))
    if st.button("Check again"):
        _health.clear()
        st.rerun()

    html('<div class="hr"></div>')
    html(T.section("Workspace", f"Stored in {ws.root}"))
    b1, b2 = st.columns([1, 3])
    if b1.button("Load reference demo", help="ISO 27001 only. Switches to that framework."):
        start_demo()
        st.rerun()
    ok = st.checkbox("I understand this deletes all evidence, assessments and reviews")
    if st.button("Reset workspace", disabled=not ok):
        ws.reset()
        for k in ("engine", "engine_sig", "exports"):
            st.session_state.pop(k, None)
        st.session_state.settings = ws.load_settings()
        st.rerun()


{"Overview": page_overview, "Evidence Library": page_evidence, "Control Assessment": page_assessment,
 "Risk Register": page_risks, "Control Library": page_library, "Settings": page_settings}[page]()
