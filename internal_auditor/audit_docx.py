"""Word (.docx) writer for the internal-audit report (python-docx)."""

from __future__ import annotations

import io
import re

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from . import audit_model as M

FONT = "Calibri"
RGB = lambda h: RGBColor.from_string(h)  # noqa: E731
_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean(s: object) -> str:
    return _BAD.sub("", re.sub(r"\s+", " ", str(s if s is not None else "")).strip())


def shade(cell, hex_fill: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    for old in tcPr.findall(qn("w:shd")):
        tcPr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcPr.append(shd)


def cell_margins(table, top=60, bottom=60, left=100, right=100) -> None:
    tblPr = table._tbl.tblPr
    m = OxmlElement("w:tblCellMar")
    for k, v in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        e = OxmlElement(f"w:{k}")
        e.set(qn("w:w"), str(v))
        e.set(qn("w:type"), "dxa")
        m.append(e)
    tblPr.append(m)


def borders(table, color="C9D3E3", inside=True, size=4) -> None:
    tblPr = table._tbl.tblPr
    b = OxmlElement("w:tblBorders")
    names = ["top", "left", "bottom", "right"] + (["insideH", "insideV"] if inside else [])
    for n in names:
        e = OxmlElement(f"w:{n}")
        e.set(qn("w:val"), "single")
        e.set(qn("w:sz"), str(size))
        e.set(qn("w:space"), "0")
        e.set(qn("w:color"), color)
        b.append(e)
    tblPr.append(b)


def set_widths(table, widths_cm: list[float]) -> None:
    table.autofit = False
    tblPr = table._tbl.tblPr
    lay = OxmlElement("w:tblLayout")
    lay.set(qn("w:type"), "fixed")
    tblPr.append(lay)
    grid = table._tbl.tblGrid
    for gc, w in zip(grid.findall(qn("w:gridCol")), widths_cm):
        gc.set(qn("w:w"), str(int(w * 567)))
    for row in table.rows:
        for c, w in zip(row.cells, widths_cm):
            c.width = Cm(w)


def no_split(row) -> None:
    trPr = row._tr.get_or_add_trPr()
    trPr.append(OxmlElement("w:cantSplit"))


def repeat_header(row) -> None:
    trPr = row._tr.get_or_add_trPr()
    e = OxmlElement("w:tblHeader")
    e.set(qn("w:val"), "true")
    trPr.append(e)


def field(par, instr: str, placeholder: str = "1") -> None:
    def fc(kind):
        r = par.add_run()
        e = OxmlElement("w:fldChar")
        e.set(qn("w:fldCharType"), kind)
        r._r.append(e)
        return r
    fc("begin")
    r = par.add_run()
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = f" {instr} "
    r._r.append(it)
    fc("separate")
    par.add_run(placeholder)
    fc("end")


def run(par, text, *, bold=False, italic=False, size=None, color=None, font=None, raw=False):
    r = par.add_run(text if raw else clean(text))
    r.bold, r.italic = bold, italic
    if size:
        r.font.size = Pt(size)
    if color:
        r.font.color.rgb = RGB(color)
    if font:
        r.font.name = font
    return r


def para(container, text="", *, bold=False, italic=False, size=None, color=None, align=None, after=None, before=None, style=None):
    p = container.add_paragraph(style=style) if style else container.add_paragraph()
    if text:
        run(p, text, bold=bold, italic=italic, size=size, color=color)
    if align:
        p.alignment = align
    pf = p.paragraph_format
    if after is not None:
        pf.space_after = Pt(after)
    if before is not None:
        pf.space_before = Pt(before)
    return p


def cell_text(cell, text, *, bold=False, italic=False, size=9, color=None, align=None):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    run(p, text, bold=bold, italic=italic, size=size, color=color)
    if align:
        p.alignment = align
    return p


def add_cell_par(cell, text, **kw):
    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    run(p, text, **kw)
    return p


def table(doc, headers, rows, widths, *, head_fill=M.NAVY, size=8.5, colour_col=None, bold_first=False):
    t = doc.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    borders(t)
    cell_margins(t)
    for c, h in zip(t.rows[0].cells, headers):
        shade(c, head_fill)
        cell_text(c, h, bold=True, size=size, color="FFFFFF")
    repeat_header(t.rows[0])
    for i, r in enumerate(rows):
        cells = t.add_row().cells
        no_split(t.rows[-1])
        for j, (c, v) in enumerate(zip(cells, r)):
            col = colour_col(j, v) if colour_col else None
            cell_text(c, v, size=size, bold=bool(col) or (bold_first and j == 0), color=col)
            if i % 2:
                shade(c, "EEF3FA")
    set_widths(t, widths)
    return t


def heading(doc, text, level=1):
    h = doc.add_heading(clean(text), level=level)
    return h


def style_doc(doc) -> None:
    st = doc.styles["Normal"]
    st.font.name, st.font.size = FONT, Pt(10.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    st.paragraph_format.space_after = Pt(6)
    st.paragraph_format.line_spacing = 1.12
    for name, size, color, before, after in (("Heading 1", 20, M.NAVY, 0, 10), ("Heading 2", 13, M.TEAL, 14, 4)):
        h = doc.styles[name]
        h.font.name, h.font.size, h.font.bold = FONT, Pt(size), True
        h.font.color.rgb = RGB(color)
        h.element.rPr.rFonts.set(qn("w:ascii"), FONT)
        h.element.rPr.rFonts.set(qn("w:hAnsi"), FONT)
        h.paragraph_format.space_before, h.paragraph_format.space_after = Pt(before), Pt(after)
        h.paragraph_format.keep_with_next = True


def page_break(doc):
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def write_docx(m: M.Model) -> bytes:
    info = m.info
    doc = Document()
    style_doc(doc)
    cp = doc.core_properties
    short = lambda s: clean(s)[:250]  # Word limits these properties to 255 characters
    cp.title, cp.author, cp.subject = short(m.cover_title), short(info.assessor_name or M.AGENT_NAME), short(f"{m.framework} internal assessment report")
    cp.comments = f"Generated by {M.AGENT_NAME}"

    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.0)
    sec.top_margin, sec.bottom_margin = Cm(1.4), Cm(2.0)
    BODY = 17.0

    # ------------------------------------------------------------------ cover
    band = doc.add_table(rows=1, cols=1)
    band.alignment = WD_TABLE_ALIGNMENT.CENTER
    c = band.rows[0].cells[0]
    shade(c, M.NAVY)
    set_widths(band, [BODY])
    cell_margins(band, top=300, bottom=300, left=300, right=300)
    cell_text(c, "INTERNAL ASSESSMENT REPORT", size=10, color="B8C7E3")
    p = c.paragraphs[0]
    p.paragraph_format.space_after = Pt(60)
    if m.logo:
        try:
            lp = c.add_paragraph()
            lp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            lp.add_run().add_picture(io.BytesIO(m.logo), height=Cm(1.5))
        except Exception:
            pass
    pa = c.add_paragraph()
    run(pa, M.AGENT_NAME, bold=True, size=27, color="FFFFFF")
    pa.paragraph_format.space_after = Pt(2)
    pb = c.add_paragraph()
    run(pb, f"Developed by {info.developed_by}" if info.developed_by else "AI-assisted GRC assessment", size=12, color="B8C7E3")
    stripe = doc.add_table(rows=1, cols=1)
    shade(stripe.rows[0].cells[0], M.TEAL)
    set_widths(stripe, [BODY])
    stripe.rows[0].height = Cm(0.25)
    stripe.rows[0].cells[0].paragraphs[0].paragraph_format.space_after = Pt(0)
    for r in stripe.rows[0].cells[0].paragraphs[0].runs:
        r.font.size = Pt(2)

    para(doc, "Compliance readiness assessment", bold=True, size=11, color=M.TEAL, before=34, after=2)
    para(doc, m.cover_title, bold=True, size=28, color=M.NAVY, after=8)
    if info.company:
        para(doc, info.company, size=17, color="333333", after=2)
    if m.scope_line:
        para(doc, m.scope_line, size=9.5, color=M.GREY, after=2)
    if info.scope:
        para(doc, "Scope: " + info.scope, size=9.5, color=M.GREY, after=14)
    rows = [("Prepared by (assessor)", f"{info.assessor_name}{', ' + info.assessor_title if info.assessor_title else ''}"),
            ("Approved by", f"{info.approver_name}{', ' + info.approver_title if info.approver_title else ''}"),
            ("Auditee contact", info.auditee_contact), ("Assessment period", info.assessment_period),
            ("Report date", info.report_date), ("Engagement reference", info.engagement_ref),
            ("Version", info.version), ("Classification", info.classification)]
    rows = [(a, b) for a, b in rows if b.strip()] + M.extra_cover_rows(info)
    dt = doc.add_table(rows=len(rows), cols=2)
    borders(dt, inside=False, size=0)
    cell_margins(dt, top=90, bottom=90, left=0, right=100)
    for r, (a, b) in zip(dt.rows, rows):
        cell_text(r.cells[0], a.upper(), bold=True, size=8, color=M.GREY)
        cell_text(r.cells[1], b, size=11)
        for cc in r.cells:
            tcPr = cc._tc.get_or_add_tcPr()
            bd = OxmlElement("w:tcBorders")
            e = OxmlElement("w:bottom")
            e.set(qn("w:val"), "single"); e.set(qn("w:sz"), "4"); e.set(qn("w:color"), "C9D3E3")
            bd.append(e)
            tcPr.append(bd)
    set_widths(dt, [4.8, BODY - 4.8])
    para(doc, f"Framework: {m.framework}. Generated {m.generated}. This report is {info.classification.lower()} and must not be shared "
              "outside the distribution list without the approval of the report owner.", size=8.5, color=M.GREY, before=18)

    # --------------------------------------------------- body section (header/footer)
    body = doc.add_section(WD_SECTION.NEW_PAGE)
    body.top_margin, body.bottom_margin = Cm(2.2), Cm(2.0)
    body.header.is_linked_to_previous = False
    body.footer.is_linked_to_previous = False
    hp = body.header.paragraphs[0]
    hp.text = ""
    run(hp, info.company or M.AGENT_NAME, bold=True, size=8, color=M.NAVY)
    run(hp, "\t" + clean(m.cover_title), size=8, color=M.GREY, raw=True)
    fp = body.footer.paragraphs[0]
    fp.text = ""
    run(fp, f"{info.classification}  |  v{info.version}", size=8, color=M.GREY)
    run(fp, "\tPage ", size=8, color=M.GREY, raw=True)
    field(fp, "PAGE")
    run(fp, " of ", size=8, color=M.GREY, raw=True)
    field(fp, "NUMPAGES")
    for r in fp.runs:
        r.font.size, r.font.color.rgb = Pt(8), RGB(M.GREY)
    for par in (hp, fp):
        par.style = doc.styles["Normal"]
        par.paragraph_format.space_after = Pt(0)
        par.paragraph_format.tab_stops.add_tab_stop(Cm(BODY), alignment=2)

    # ----------------------------------------------------------------- contents
    heading(doc, "Contents", 1)
    tp = doc.add_paragraph()
    field(tp, 'TOC \\o "1-1" \\h \\z \\u', "Right-click and choose Update Field to show page numbers.")
    for s in M.sections_for(m):
        para(doc, s, after=3, color=M.NAVY)
    page_break(doc)

    # --------------------------------------------------------- document control
    heading(doc, "Document control", 1)
    dc = [("Report title", m.cover_title), ("Organisation", info.company), ("Framework", m.framework),
          ("Engagement reference", info.engagement_ref),
          ("Assessor", f"{info.assessor_name} {info.assessor_title}".strip()),
          ("Approver", f"{info.approver_name} {info.approver_title}".strip()),
          ("Reviewers of AI drafts", ", ".join(m.reviewers) or "None recorded"), ("Version", info.version),
          ("Report date", info.report_date or m.generated), ("Classification", info.classification),
          ("Distribution", info.distribution), ("Assessment tool", f"{M.AGENT_NAME}, developed by {info.developed_by}"),
          ("Language model", f"{m.llm} (runs locally, no data sent to any cloud service)" if m.llm else ""),
          ("Model settings", m.run_settings)]
    table(doc, ["Item", "Detail"], [(a, b or "Not provided") for a, b in dc], [4.5, BODY - 4.5], bold_first=True, size=9)
    page_break(doc)

    # -------------------------------------------------------- executive summary
    heading(doc, "Executive summary", 1)
    top = doc.add_table(rows=1, cols=2)
    set_widths(top, [6.2, BODY - 6.2])
    box, fig = top.rows[0].cells
    shade(box, M.RATING_HEX.get(m.rating, "757575"))
    cell_text(box, m.rating.upper(), bold=True, size=17, color="FFFFFF", align=WD_ALIGN_PARAGRAPH.CENTER)
    add_cell_par(box, f"Overall readiness {m.score:.0%}", size=10, color="FFFFFF").alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_cell_par(box, m.rating_text, size=9, color="FFFFFF").alignment = WD_ALIGN_PARAGRAPH.CENTER
    figs = [("Controls in framework", m.total_controls), ("Assessed and included", len(m.results)),
            ("Approved by a reviewer", m.approved), ("Not covered", m.not_covered), ("Findings (gaps)", len(m.findings)),
            *([("Marked not applicable", len(m.na))] if m.na else []),
            ("Critical / High risks", f"{m.risk_counts['Critical']} / {m.risk_counts['High']}")]
    if m.is_hipaa:
        figs.append(("Required specs not Met", m.required_gaps))
    ft = fig.add_table(rows=len(figs), cols=2)
    borders(ft, inside=True, size=2)
    for r, (a, b) in zip(ft.rows, figs):
        cell_text(r.cells[0], a, size=9)
        cell_text(r.cells[1], b, size=9, bold=True, align=WD_ALIGN_PARAGRAPH.RIGHT)
    set_widths(ft, [BODY - 6.2 - 2.6, 2.0])
    fig.paragraphs[0].paragraph_format.space_after = Pt(0)
    heading(doc, "Key messages", 2)
    for msg in m.key_messages:
        para(doc, msg, style="List Bullet", after=3)
    if m.rating_note:
        para(doc, m.rating_note, size=8.5, color=M.GREY, before=4)
    if "donut" in m.charts:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(io.BytesIO(m.charts["donut"]), width=Cm(7.0))
    page_break(doc)

    # ------------------------------------------------------------ HIPAA overview
    if m.is_hipaa:
        heading(doc, "HIPAA compliance overview", 1)
        heading(doc, "Regulatory context", 2)
        for line in M.HIPAA_CONTEXT:
            para(doc, line, style="List Bullet", after=3)
        heading(doc, "Foundational programme elements", 2)
        para(doc, "These elements underpin the rest of the programme. The overall rating cannot be Satisfactory while any of them is not Met.", size=9, color=M.GREY)
        table(doc, ["Citation", "Element", "Status"], [(g["id"], g["title"], g["status"]) for g in m.foundational],
              [4.4, BODY - 8.0, 3.6], size=9, bold_first=True, colour_col=lambda j, v: M.STATUS_HEX.get(v) if j == 2 else None)
        heading(doc, "Required and Addressable specifications", 2)
        table(doc, ["Type", "In framework", "Assessed", "Met", "Partial", "Not met", "No evid.", "Readiness"],
              [({"Standard": "Privacy and Breach"}.get(k, k), tot, ass, a, b, c, dd, f"{sc:.0%}" if ass else "-")
               for k, tot, ass, a, b, c, dd, sc in m.req_table],
              [BODY - 13.6, 2.0, 1.8, 1.3, 1.5, 1.6, 1.7, 2.1], size=9)
        para(doc, M.ADDRESSABLE_NOTE, size=8.5, color=M.GREY, before=4)
        page_break(doc)

    # ------------------------------------------------------------------- scope
    heading(doc, "Scope, approach and rating scales", 1)
    heading(doc, "Objective", 2)
    para(doc, M.SCOPE_TEXT)
    heading(doc, "Scope", 2)
    para(doc, f"Framework: {m.framework}. " + (info.scope + ". " if info.scope else "") +
         f"{len(m.results)} of {m.total_controls} controls were assessed" +
         (f" during {info.assessment_period}." if info.assessment_period else "."))
    heading(doc, "Approach", 2)
    for step in M.METHOD_STEPS:
        para(doc, step, style="List Number", after=3)
    heading(doc, "Verdict definitions", 2)
    table(doc, ["Verdict", "Meaning"], M.STATUS_DEFS, [3.4, BODY - 3.4], size=9,
          colour_col=lambda j, v: M.STATUS_HEX.get(v) if j == 0 else None)
    heading(doc, "Overall rating scale", 2)
    table(doc, ["Rating", "Basis", "Implication"], M.RATING_DEFS, [3.4, 4.2, BODY - 7.6], size=9,
          colour_col=lambda j, v: M.RATING_HEX.get(v) if j == 0 else None)
    heading(doc, "Risk levels", 2)
    table(doc, ["Level", "Score", "Target"], M.RISK_DEFS, [3.4, 4.2, BODY - 7.6], size=9,
          colour_col=lambda j, v: M.LEVEL_HEX.get(v) if j == 0 else None)
    para(doc, M.SCORING_NOTE, size=8.5, color=M.GREY, before=4)
    heading(doc, "Limitations", 2)
    for t in M.limits_for(m):
        para(doc, t, style="List Bullet", after=3)
    page_break(doc)

    # ----------------------------------------------------------------- domains
    heading(doc, "Results by domain", 1)
    if "domains" in m.charts:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(io.BytesIO(m.charts["domains"]), width=Cm(BODY))
    o = m.summary["overall"]
    rows = [[d["domain"], d["total"], d["counts"]["Met"], d["counts"]["Partially met"], d["counts"]["Not met"], d["counts"]["No evidence"], f"{d['score']:.0%}"]
            for d in m.summary["domains"]]
    rows.append(["Total", o["total"], o["counts"]["Met"], o["counts"]["Partially met"], o["counts"]["Not met"], o["counts"]["No evidence"], f"{o['score']:.0%}"])
    t = table(doc, ["Domain", "Controls", "Met", "Partial", "Not met", "No evid.", "Readiness"], rows,
              [BODY - 11.3, 1.7, 1.3, 1.5, 1.7, 1.6, 2.0], size=9)
    for c in t.rows[-1].cells:
        shade(c, "DCE6F4")
        for r in c.paragraphs[0].runs:
            r.bold = True
    page_break(doc)

    # ---------------------------------------------------------------- findings
    heading(doc, "Detailed findings", 1)
    if not m.findings:
        para(doc, "No gaps were identified in the controls assessed.")
    else:
        para(doc, "Findings are ordered by risk score, highest first. Each shows the criteria (what the control requires), the "
                  "condition (what the documents show), the evidence, the gap, a recommendation and space for management's response.",
             size=9, color=M.GREY)
    for f in m.findings:
        _finding(doc, f, BODY)
        para(doc, "", after=6)
    page_break(doc)

    # ------------------------------------------------------------- action plan
    heading(doc, "Remediation action plan", 1)
    if m.findings:
        rows = [(f.ref, f"{f.control_id} {f.title}", f"{f.level} ({f.score})", f.recommendation, f.owner, f.due, f.treatment) for f in m.findings]
        table(doc, ["Ref", "Control", "Risk", "Recommended action", "Owner", "Due", "Status"], rows,
              [1.1, 3.0, 1.7, BODY - 12.2, 2.3, 1.9, 1.5], size=8,
              colour_col=lambda j, v: M.LEVEL_HEX.get(str(v).split(" ")[0]) if j == 2 else None)
    else:
        para(doc, "No actions required.")
    para(doc, "Owners, dates and status are proposals from the tool until management confirms them.", size=8.5, color=M.GREY, before=6)
    page_break(doc)

    # -------------------------------------------------------------- appendices
    heading(doc, "Appendices", 1)
    heading(doc, "Appendix A: documents reviewed", 2)
    if m.docs:
        table(doc, ["Document", "Sections", "Words", "SHA-256 (first 16)", "Notes"],
              [(d.get("name", ""), d.get("chunks", ""), d.get("words", ""), str(d.get("sha256", "-"))[:16], d.get("warning", "")) for d in m.docs],
              [BODY - 12.3, 1.6, 1.5, 3.7, 5.5], size=8.5)
        para(doc, "The SHA-256 value identifies the exact file reviewed. Recompute it to confirm a document has not changed since the assessment.", size=8.5, color=M.GREY, before=4)
    else:
        para(doc, "No documents recorded.")
    heading(doc, "Appendix B: control-by-control results", 2)
    table(doc, ["Control", "Title", "Verdict", "Quote verified", "Reviewer", "Review"],
          [(a.control_id, a.title, a.status, "Yes" if a.citation_verified else "No", a.reviewer or "-", a.review_status) for a in m.results],
          [1.6, BODY - 12.4, 2.4, 2.1, 3.4, 2.9], size=8.5, bold_first=True,
          colour_col=lambda j, v: M.STATUS_HEX.get(v) if j == 2 else None)
    if m.na:
        heading(doc, "Appendix C: controls marked not applicable", 2)
        para(doc, "A reviewer recorded why each control does not apply. Auditors should confirm these justifications; they are excluded from scoring.", size=9, color=M.GREY)
        table(doc, ["Control", "Title", "Justification", "Marked by", "Date"],
              [(a.control_id, a.title, a.reviewer_note, a.reviewer, a.reviewed_at) for a in m.na], [2.6, 3.4, BODY - 11.4, 2.8, 2.6], size=8.5, bold_first=True)
    heading(doc, f"Appendix {'D' if m.na else 'C'}: limitations and disclaimer", 2)
    para(doc, M.CAVEAT)
    heading(doc, "Sign-off", 2)
    sg = doc.add_table(rows=4, cols=2)
    for r, (a, b) in zip(sg.rows, [("Prepared by", "Approved by"),
                                   (f"{info.assessor_name or '____________________'}\n{info.assessor_title}", f"{info.approver_name or '____________________'}\n{info.approver_title}"),
                                   ("Signature: ______________________", "Signature: ______________________"),
                                   ("Date: ____________________________", "Date: ____________________________")]):
        cell_text(r.cells[0], a, bold=r is sg.rows[0], size=9.5, color=M.NAVY if r is sg.rows[0] else None)
        cell_text(r.cells[1], b, bold=r is sg.rows[0], size=9.5, color=M.NAVY if r is sg.rows[0] else None)
        no_split(r)
    cell_margins(sg, top=120, bottom=60, left=0, right=100)
    set_widths(sg, [BODY / 2, BODY / 2])

    # Ask Word to refresh the contents table (page numbers) when the file is opened.
    s = OxmlElement("w:updateFields")
    s.set(qn("w:val"), "true")
    doc.settings.element.append(s)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _finding(doc, f: M.Finding, width: float) -> None:
    t = doc.add_table(rows=1, cols=2)
    borders(t)
    cell_margins(t, top=70, bottom=70, left=120, right=120)
    top = t.rows[0]
    merged = top.cells[0].merge(top.cells[1])
    shade(merged, M.LEVEL_HEX[f.level])
    cell_text(merged, f"{f.ref}  |  {f.control_id} {f.title}      {f.level} ({f.score})", bold=True, size=10, color="FFFFFF")
    no_split(top)
    top.cells[0].paragraphs[0].paragraph_format.keep_with_next = True

    def row(label, value, *, italic=False, color=None, extra=None):
        r = t.add_row()
        no_split(r)
        shade(r.cells[0], "EEF3FA")
        cell_text(r.cells[0], label, bold=True, size=8.5, color=M.NAVY)
        cell_text(r.cells[1], value, italic=italic, size=8.5, color=color)
        if extra:
            add_cell_par(r.cells[1], extra, size=8, color=M.GREY)

    row("Domain / verdict", f"{f.domain}  |  {f.status}", color=None)
    if f.requirement:
        row("Specification", f.requirement + (". " + M.ADDRESSABLE_NOTE if f.requirement == "Addressable" else ""))
    row("Criteria", f.criteria or "See control description in the framework.")
    row("Condition", f.condition or "-")
    if f.quote:
        row("Evidence", f"“{f.quote}”", italic=True, extra=(("Verified in " if f.verified else "Unverified, cited from ") + f.source) if f.source else None)
    else:
        row("Evidence", "No supporting passage found in the documents provided.")
    row("Gap", f.gap or "-")
    row("Recommendation", f.recommendation + (" (AI-drafted, to be confirmed by the control owner)" if f.ai_drafted else ""))
    row("Owner / due date", f"{f.owner}  |  due {f.due or 'to be agreed'}  |  status {f.treatment}")
    if f.reviewer_note:
        row("Reviewer note", f"{f.reviewer_note} ({f.reviewer})")
    row("Management response", "To be completed by the control owner.", italic=True, color=M.GREY)
    set_widths(t, [3.2, width - 3.2])
