"""Evidence intake form: share this app (not the main one) with the people who own the controls.

    streamlit run intake_app.py --server.port 8502 --server.address 0.0.0.0

It shows only a form. It cannot see assessments, risks or other people's answers.
"""

from __future__ import annotations

import streamlit as st

from internal_auditor import theme as T
from internal_auditor.frameworks import FRAMEWORK_DIR, load_framework
from internal_auditor.intake import ALLOWED, MAX_FILES, STATUSES, Intake, IntakeError
from internal_auditor.workspace import Workspace

st.set_page_config(page_title=f"{T.APP_NAME}: evidence form", page_icon="🛡️", layout="centered", initial_sidebar_state="collapsed")
st.markdown(T.css_block() + "<style>[data-testid='stSidebar'],[data-testid='stSidebarCollapsedControl']{display:none !important}"
            ".stApp .block-container{max-width:760px !important}</style>", unsafe_allow_html=True)
html = lambda s: st.markdown(s, unsafe_allow_html=True)  # noqa: E731

ws = Workspace()
intake = Intake(ws.root)
info = ws.report_info()
fw_path = ws.root / "framework.json" if ws.framework_id == "custom" else FRAMEWORK_DIR / f"{ws.framework_id}.json"
if not fw_path.exists():
    fw_path = FRAMEWORK_DIR / "iso27001_2022.json"
meta, controls_list = load_framework(fw_path)
controls = {c.id: c for c in controls_list}

company = info.get("company", "").strip()
html(T.page_header("Share your evidence", f"{company or 'Your organisation'} is preparing a compliance assessment. Answer the question below and upload any supporting documents.",
                   eyebrow=f"{company or T.APP_NAME} / Evidence request"))
html(T.note("Do not upload or type patient information. Send policies, procedures, configuration screenshots and reports with all "
            "names, record numbers and dates of birth removed. Describe how a process works instead of giving real patient examples.", "warn"))

if "unlocked" not in st.session_state:
    st.session_state.unlocked = not intake.code_required()
if not st.session_state.unlocked:
    code = st.text_input("Access code", type="password", help="Ask the person who sent you this link.")
    if st.button("Continue", type="primary"):
        if intake.check_code(code):
            st.session_state.unlocked = True
            st.rerun()
        else:
            st.error("That code is not correct.")
    st.stop()

done = st.session_state.get("done")
if done:
    st.success(f"Thank you. Your response was received. Your reference is **{done}**. Keep it in case the assessor has questions.")
    if st.button("Submit another response"):
        st.session_state.pop("done")
        st.session_state.n = st.session_state.get("n", 0) + 1
        st.rerun()
    st.stop()

n = st.session_state.get("n", 0)
open_reqs = [r for r in intake.requests() if intake.request_status(r) != "Closed" and r["control_id"] in controls]
label = lambda cid: f"{cid}  {controls[cid].title}"  # noqa: E731
req = None
if open_reqs:
    html(T.section("1. What are you answering?", "Pick one of the requests sent to you, or choose any requirement."))
    choice = st.selectbox("Request", ["I want to choose a requirement myself"] + [r["id"] for r in open_reqs], key=f"req_{n}",
                          format_func=lambda i: i if i.startswith("I want") else
                          f"{label(next(r for r in open_reqs if r['id'] == i)['control_id'])}  (asked of {next(r for r in open_reqs if r['id'] == i)['owner'] or 'anyone'}, due {next(r for r in open_reqs if r['id'] == i)['due']})")
    req = next((r for r in open_reqs if r["id"] == choice), None)
else:
    html(T.section("1. What are you answering?", "Choose the requirement your answer is about."))
cid = req["control_id"] if req else st.selectbox("Requirement", list(controls), key=f"ctl_{n}", format_func=label, index=None,
                                                  placeholder="Choose a requirement")
if cid is None:
    st.info("Choose the requirement your answer is about to continue.")
    st.stop()
c = controls[cid]
if req and req["note"]:
    st.info(req["note"])
html(T.panel(f"{cid} {c.title}" + (f" ({c.requirement})" if c.requirement else ""), c.summary, T.esc("Helpful evidence: " + c.evidence_hint)))
extra = st.multiselect("This answer also covers (optional)", [x for x in controls if x != cid], key=f"extra_{n}", format_func=label)

with st.form(f"intake_{n}"):
    html(T.section("2. About you"))
    a, b = st.columns(2)
    name = a.text_input("Your name *")
    email = b.text_input("Work email")
    a, b = st.columns(2)
    role = a.text_input("Your role *")
    dept = b.text_input("Department *")
    html(T.section("3. Your answer"))
    status = st.radio("How far is this in place? *", STATUSES, index=None)
    desc = st.text_area("How is this done in practice? *", height=140,
                        help="Describe the process, who does it and with which tools. Do not include patient details.")
    a, b = st.columns(2)
    owner = a.text_input("Who owns this?", placeholder="Name or role")
    reviewed = b.text_input("Last reviewed or tested", placeholder="e.g. March 2026")
    a, b = st.columns(2)
    freq = a.text_input("How often is it done or reviewed?", placeholder="e.g. quarterly")
    exc = b.text_input("Exceptions or limitations")
    html(T.section("4. Supporting evidence", f"Up to {MAX_FILES} files. PDF, DOCX, TXT and MD are read by the assessor's tool. PNG and JPG screenshots are kept for a person to view."))
    files = st.file_uploader("Upload files", type=[e.strip(".") for e in sorted(ALLOWED)], accept_multiple_files=True)
    attest = st.checkbox("I confirm this answer is accurate to the best of my knowledge and contains no patient information. *")
    if st.form_submit_button("Submit response", type="primary"):
        try:
            sub = intake.submit(name=name, email=email, role=role, department=dept, control_ids=[cid, *extra],
                                request_id=req["id"] if req else "", status_claimed=status or "", description=desc,
                                owner=owner, last_reviewed=reviewed, frequency=freq, exceptions=exc, attested=attest,
                                files=[(f.name, f.getvalue()) for f in files or []])
            st.session_state.done = sub.id
            st.rerun()
        except IntakeError as e:
            st.error(str(e))
st.caption("Your answer is stored on the assessor's computer and read by a person before it is used. It is not sent to any cloud service.")
