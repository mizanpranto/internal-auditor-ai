"""PDF writer for the internal-audit report (reportlab)."""

from __future__ import annotations

import io
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

from . import audit_model as M

W, H = A4
MARGIN = 2.0 * cm
BODY_W = W - 2 * MARGIN
C = lambda h: colors.HexColor("#" + h)  # noqa: E731
NAVY, TEAL, GREY = C(M.NAVY), C(M.TEAL), C(M.GREY)
LIGHT, LINE = colors.HexColor("#EEF3FA"), colors.HexColor("#C9D3E3")

_MAP = {"–": "-", "—": "-", "−": "-", "→": "->", "≥": ">=", "≤": "<=", "✓": "yes",
        "•": "-", " ": " ", " ": " ", "​": ""}


def clean(s: object) -> str:
    """Text safe for the built-in PDF fonts (WinAnsi): unmappable characters become '?'."""
    s = re.sub(r"\s+", " ", str(s if s is not None else "")).strip()
    for k, v in _MAP.items():
        s = s.replace(k, v)
    return s.encode("cp1252", "replace").decode("cp1252")


def P(text: object, style: ParagraphStyle, cap: int = 1500) -> Paragraph:
    """Paragraph from plain text. Very long text is shortened: a single table row can never be taller than a page."""
    s = clean(text)
    return Paragraph(escape(s if len(s) <= cap else s[: cap - 3].rstrip() + "..."), style)


def styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=colors.HexColor("#222222"))
    s = {"body": base}

    def add(name, parent=base, **kw):
        s[name] = ParagraphStyle(name, parent=parent, **kw)

    add("small", fontSize=8, leading=11, textColor=GREY)
    add("cell", fontSize=8.5, leading=11.5)
    add("cellb", fontName="Helvetica-Bold", fontSize=8.5, leading=11.5)
    add("head", fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=colors.white)
    add("lab", fontName="Helvetica-Bold", fontSize=8.5, leading=11.5, textColor=NAVY)
    add("quote", fontName="Helvetica-Oblique", fontSize=8.5, leading=11.5, textColor=colors.HexColor("#333333"))
    add("h1", fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=NAVY, spaceBefore=0, spaceAfter=10)
    add("h1x", parent=s["h1"])
    add("h2", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=TEAL, spaceBefore=12, spaceAfter=5)
    add("bullet", leftIndent=12, bulletIndent=2, spaceAfter=3)
    add("toc1", fontName="Helvetica", fontSize=10.5, leading=19, textColor=NAVY)
    add("c_agent", fontName="Helvetica-Bold", fontSize=23, leading=27, textColor=colors.white)
    add("c_kick", fontSize=9.5, leading=13, textColor=colors.HexColor("#B8C7E3"))
    add("c_type", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=TEAL)
    add("c_title", fontName="Helvetica-Bold", fontSize=27, leading=32, textColor=NAVY)
    add("c_co", fontSize=15, leading=19, textColor=colors.HexColor("#333333"))
    add("c_lab", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=GREY)
    add("c_val", fontSize=10.5, leading=14, textColor=colors.HexColor("#111111"))
    add("foot", fontSize=7.5, leading=10, textColor=GREY, alignment=TA_CENTER)
    add("rating", fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=colors.white, alignment=TA_CENTER)
    add("ratingsub", fontSize=8.5, leading=11, textColor=colors.white, alignment=TA_CENTER)
    return s


class NumberedCanvas(rl_canvas.Canvas):
    """Two-pass canvas so the footer can say 'Page X of Y'."""

    footer_left = ""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._pages: list[dict] = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            if self._pageNumber > 1:
                self.setFont("Helvetica", 7.5)
                self.setFillColor(GREY)
                self.drawRightString(W - MARGIN, 1.15 * cm, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


class Doc(BaseDocTemplate):
    def afterFlowable(self, f):
        if isinstance(f, Paragraph) and f.style.name == "h1":
            text = f.getPlainText()
            key = f"s{self.seq.nextf('sec')}"
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (0, text, self.page, key))


def _table(rows, widths, head=True, zebra=True, extra=None):
    t = Table(rows, colWidths=widths, repeatRows=1 if head else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
          ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
          ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE)]
    if head:
        st += [("BACKGROUND", (0, 0), (-1, 0), NAVY)]
    if zebra:
        st += [("ROWBACKGROUNDS", (0, 1 if head else 0), (-1, -1), [colors.white, LIGHT])]
    t.setStyle(TableStyle(st + (extra or [])))
    return t


def _chip(text, hexcolor, style):
    t = Table([[Paragraph(f"<font color='white'><b>{escape(clean(text))}</b></font>", style)]], colWidths=[None])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), C(hexcolor)), ("TOPPADDING", (0, 0), (-1, -1), 2),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 4),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 4)]))
    return t


def write_pdf(m: M.Model) -> bytes:
    S = styles()
    info = m.info
    buf = io.BytesIO()
    footer_txt = clean(f"{info.classification}  |  {info.company or 'Company'}  |  {m.cover_title}")

    def cover_page(c, doc):
        c.saveState()
        c.setFillColor(NAVY)
        c.rect(0, H - 9.2 * cm, W, 9.2 * cm, stroke=0, fill=1)
        c.setFillColor(TEAL)
        c.rect(0, H - 9.2 * cm - 4 * mm, W, 4 * mm, stroke=0, fill=1)
        if m.logo:
            try:
                img = ImageReader(io.BytesIO(m.logo))
                iw, ih = img.getSize()
                bh = 1.7 * cm
                bw = min(bh * iw / ih, 5.5 * cm)
                bh = bw * ih / iw
                c.setFillColor(colors.white)
                c.roundRect(W - MARGIN - bw - 0.6 * cm, H - 2.0 * cm - bh - 0.3 * cm, bw + 0.6 * cm, bh + 0.6 * cm, 4, stroke=0, fill=1)
                c.drawImage(img, W - MARGIN - bw - 0.3 * cm, H - 2.0 * cm - bh, bw, bh, mask="auto")
            except Exception:
                pass
        c.setFillColor(colors.HexColor("#B8C7E3"))
        c.setFont("Helvetica", 10)
        c.drawString(MARGIN, H - 2.1 * cm, "INTERNAL ASSESSMENT REPORT")
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 25)
        c.drawString(MARGIN, H - 6.6 * cm, clean(M.AGENT_NAME))
        c.setFont("Helvetica", 11)
        c.setFillColor(colors.HexColor("#B8C7E3"))
        c.drawString(MARGIN, H - 7.5 * cm, clean(f"Developed by {info.developed_by}" if info.developed_by else "AI-assisted GRC assessment"))
        c.setFillColor(GREY)
        c.setFont("Helvetica", 7.5)
        c.drawCentredString(W / 2, 1.15 * cm, footer_txt)
        c.restoreState()

    def body_page(c, doc):
        c.saveState()
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(MARGIN, H - 1.6 * cm, W - MARGIN, H - 1.6 * cm)
        c.line(MARGIN, 1.6 * cm, W - MARGIN, 1.6 * cm)
        c.setFont("Helvetica-Bold", 7.5)
        c.setFillColor(NAVY)
        c.drawString(MARGIN, H - 1.4 * cm, clean(info.company or M.AGENT_NAME))
        c.setFont("Helvetica", 7.5)
        c.setFillColor(GREY)
        c.drawRightString(W - MARGIN, H - 1.4 * cm, clean(m.cover_title))
        c.drawString(MARGIN, 1.15 * cm, clean(f"{info.classification}  |  v{info.version}"))
        c.restoreState()

    doc = Doc(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=2.1 * cm, bottomMargin=2.1 * cm,
              title=clean(m.cover_title)[:250], author=clean(info.assessor_name or M.AGENT_NAME)[:250],
              subject=clean(f"{m.framework} internal assessment report"), creator=M.AGENT_NAME)
    doc.addPageTemplates([
        PageTemplate("cover", [Frame(MARGIN, 2.0 * cm, BODY_W, H - 9.2 * cm - 2.0 * cm - 1.4 * cm, id="c", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=cover_page),
        PageTemplate("body", [Frame(MARGIN, 2.1 * cm, BODY_W, H - 4.2 * cm, id="b", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=body_page),
    ])

    from reportlab.platypus import NextPageTemplate
    story: list = []

    # ------------------------------------------------------------ cover
    rows = [("Prepared by (assessor)", info.assessor_name, info.assessor_title),
            ("Approved by", info.approver_name, info.approver_title),
            ("Auditee contact", info.auditee_contact, ""),
            ("Assessment period", info.assessment_period, ""),
            ("Report date", info.report_date, ""),
            ("Engagement reference", info.engagement_ref, ""),
            ("Version", info.version, ""),
            ("Classification", info.classification, "")]
    rows += [(a, b, "") for a, b in M.extra_cover_rows(info)]
    cells = []
    for lab, val, sub in rows:
        if not val and not sub:
            continue
        cells.append([P(lab.upper(), S["c_lab"]), P(val + (f", {sub}" if sub else ""), S["c_val"])])
    details = Table(cells, colWidths=[4.6 * cm, BODY_W - 4.6 * cm])
    details.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                                 ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [Spacer(1, 0.2 * cm), P("Compliance readiness assessment", S["c_type"]), Spacer(1, 4),
              P(m.cover_title, S["c_title"]), Spacer(1, 10)]
    if info.company:
        story += [P(info.company, S["c_co"])]
    if m.scope_line:
        story += [Spacer(1, 2), P(m.scope_line, S["small"])]
    if info.scope:
        story += [Spacer(1, 2), P("Scope: " + info.scope, S["small"])]
    story += [Spacer(1, 0.6 * cm), details, Spacer(1, 0.5 * cm),
              P(f"Framework: {m.framework}. Generated {m.generated}. This report is {info.classification.lower()}. "
                "It must not be copied or shared outside the distribution list without the approval of the report owner.",
                S["small"]),
              NextPageTemplate("body"), PageBreak()]

    # ------------------------------------------------------------ contents
    toc = TableOfContents()
    toc.levelStyles = [S["toc1"]]
    toc.dotsMinLevel = 0
    story += [P("Contents", S["h1x"]), toc, PageBreak()]

    # ------------------------------------------------------------ document control
    story += [P("Document control", S["h1"])]
    dc = [[P("Item", S["head"]), P("Detail", S["head"])],
          *[[P(a, S["cellb"]), P(b or "Not provided", S["cell"])] for a, b in [
              ("Report title", m.cover_title), ("Organisation", info.company), ("Framework", m.framework),
              ("Engagement reference", info.engagement_ref), ("Assessor", f"{info.assessor_name} {info.assessor_title}".strip()),
              ("Approver", f"{info.approver_name} {info.approver_title}".strip()),
              ("Reviewers of AI drafts", ", ".join(m.reviewers) or "None recorded"),
              *M.extra_cover_rows(info),
              ("Version", info.version), ("Report date", info.report_date or m.generated),
              ("Classification", info.classification), ("Distribution", info.distribution),
              ("Assessment tool", f"{M.AGENT_NAME}, developed by {info.developed_by}"),
              ("Language model", f"{m.llm} (runs locally, no data sent to any cloud service)" if m.llm else ""),
              ("Model settings", m.run_settings)]]]
    story += [_table(dc, [4.5 * cm, BODY_W - 4.5 * cm]), PageBreak()]

    # ------------------------------------------------------------ executive summary
    story += [P("Executive summary", S["h1"])]
    box = Table([[P(m.rating.upper(), S["rating"])], [P(f"Overall readiness {m.score:.0%}", S["ratingsub"])],
                 [P(m.rating_text, S["ratingsub"])]], colWidths=[6.2 * cm])
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), C(M.RATING_HEX.get(m.rating, "757575"))),
                             ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    figs = [["Controls in framework", m.total_controls], ["Assessed and included", len(m.results)],
            ["Approved by a reviewer", m.approved], ["Not covered", m.not_covered],
            ["Findings (gaps)", len(m.findings)], *([["Marked not applicable", len(m.na)]] if m.na else []),
            ["Critical / High risks", f"{m.risk_counts['Critical']} / {m.risk_counts['High']}"]]
    if m.is_hipaa:
        figs.append(["Required specs not Met", m.required_gaps])
    ft = Table([[P(a, S["cell"]), P(b, S["cellb"])] for a, b in figs], colWidths=[4.6 * cm, 1.7 * cm])
    ft.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    top = Table([[box, ft]], colWidths=[6.8 * cm, BODY_W - 6.8 * cm])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [top, Spacer(1, 10), P("Key messages", S["h2"])]
    for msg in m.key_messages:
        story.append(Paragraph(escape(clean(msg)), S["bullet"], bulletText="•"))
    if m.rating_note:
        story += [Spacer(1, 4), P(m.rating_note, S["small"])]
    if "donut" in m.charts:
        story += [Spacer(1, 6), Image(io.BytesIO(m.charts["donut"]), width=7.0 * cm, height=6.1 * cm, hAlign="CENTER")]
    story.append(PageBreak())

    # ------------------------------------------------------------ HIPAA overview
    if m.is_hipaa:
        story += [P("HIPAA compliance overview", S["h1"]), P("Regulatory context", S["h2"])]
        for line in M.HIPAA_CONTEXT:
            story.append(Paragraph(escape(clean(line)), S["bullet"], bulletText="\u2022"))
        story.append(P("Foundational programme elements", S["h2"]))
        story.append(P("These elements underpin the rest of the programme. The overall rating cannot be Satisfactory while any of them is not Met.", S["small"]))
        story.append(Spacer(1, 4))
        rows = [[P(x, S["head"]) for x in ("Citation", "Element", "Status")]]
        for g in m.foundational:
            colour = M.STATUS_HEX.get(g["status"], "757575")
            rows.append([P(g["id"], S["cellb"]), P(g["title"], S["cell"]), Paragraph(f"<font color='#{colour}'><b>{g['status']}</b></font>", S["cell"])])
        story.append(_table(rows, [4.4 * cm, BODY_W - 8.0 * cm, 3.6 * cm]))
        story.append(P("Required and Addressable specifications", S["h2"]))
        rows = [[P(x, S["head"]) for x in ("Type", "In framework", "Assessed", "Met", "Partial", "Not met", "No evid.", "Readiness")]]
        for k, tot, ass, a, b, c, d, sc in m.req_table:
            rows.append([P({"Standard": "Privacy and Breach"}.get(k, k), S["cell"]), *[P(v, S["cell"]) for v in (tot, ass, a, b, c, d)],
                         P(f"{sc:.0%}" if ass else "-", S["cellb"])])
        story += [_table(rows, [BODY_W - 13.6 * cm, 2.0 * cm, 1.8 * cm, 1.3 * cm, 1.5 * cm, 1.6 * cm, 1.7 * cm, 2.1 * cm]),
                  Spacer(1, 4), P(M.ADDRESSABLE_NOTE, S["small"]), PageBreak()]

    # ------------------------------------------------------------ scope and approach
    story += [P("Scope, approach and rating scales", S["h1"]), P("Objective", S["h2"]), P(M.SCOPE_TEXT, S["body"]),
              P("Scope", S["h2"]),
              P(f"Framework: {m.framework}. " + (info.scope + ". " if info.scope else "") +
                f"{len(m.results)} of {m.total_controls} controls were assessed" +
                (f" during {info.assessment_period}." if info.assessment_period else "."), S["body"]),
              P("Approach", S["h2"])]
    for i, step in enumerate(M.METHOD_STEPS, 1):
        story.append(Paragraph(escape(clean(step)), S["bullet"], bulletText=f"{i}."))
    story += [P("Verdict definitions", S["h2"]),
              _table([[P("Verdict", S["head"]), P("Meaning", S["head"])],
                      *[[Paragraph(f"<font color='#{M.STATUS_HEX[a]}'><b>{a}</b></font>", S["cell"]), P(b, S["cell"])] for a, b in M.STATUS_DEFS]],
                     [3.4 * cm, BODY_W - 3.4 * cm]),
              P("Overall rating scale", S["h2"]),
              _table([[P("Rating", S["head"]), P("Basis", S["head"]), P("Implication", S["head"])],
                      *[[Paragraph(f"<font color='#{M.RATING_HEX[a]}'><b>{a}</b></font>", S["cell"]), P(b, S["cell"]), P(c, S["cell"])] for a, b, c in M.RATING_DEFS]],
                     [3.4 * cm, 4.2 * cm, BODY_W - 7.6 * cm]),
              P("Risk levels", S["h2"]),
              _table([[P("Level", S["head"]), P("Score", S["head"]), P("Target", S["head"])],
                      *[[Paragraph(f"<font color='#{M.LEVEL_HEX[a]}'><b>{a}</b></font>", S["cell"]), P(b, S["cell"]), P(c, S["cell"])] for a, b, c in M.RISK_DEFS]],
                     [3.4 * cm, 4.2 * cm, BODY_W - 7.6 * cm]),
              Spacer(1, 4), P(M.SCORING_NOTE, S["small"]), P("Limitations", S["h2"])]
    for t in M.limits_for(m):
        story.append(Paragraph(escape(clean(t)), S["bullet"], bulletText="•"))
    story.append(PageBreak())

    # ------------------------------------------------------------ results by domain
    story += [P("Results by domain", S["h1"])]
    if "domains" in m.charts:
        story.append(_img(m.charts["domains"], BODY_W))
    rows = [[P(x, S["head"]) for x in ("Domain", "Controls", "Met", "Partial", "Not met", "No evid.", "Readiness")]]
    for d in m.summary["domains"]:
        c = d["counts"]
        rows.append([P(d["domain"], S["cell"]), *[P(v, S["cell"]) for v in (d["total"], c["Met"], c["Partially met"], c["Not met"], c["No evidence"])],
                     P(f"{d['score']:.0%}", S["cellb"])])
    o = m.summary["overall"]
    rows.append([P("Total", S["cellb"]), *[P(v, S["cellb"]) for v in (o["total"], o["counts"]["Met"], o["counts"]["Partially met"], o["counts"]["Not met"], o["counts"]["No evidence"], f"{o['score']:.0%}")]])
    story += [Spacer(1, 8), _table(rows, [BODY_W - 11.3 * cm, 1.7 * cm, 1.3 * cm, 1.5 * cm, 1.7 * cm, 1.6 * cm, 2.0 * cm],
                                   extra=[("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#DCE6F4"))]), PageBreak()]

    # ------------------------------------------------------------ findings
    story += [P("Detailed findings", S["h1"])]
    if not m.findings:
        story.append(P("No gaps were identified in the controls assessed.", S["body"]))
    else:
        story.append(P("Findings are ordered by risk score, highest first. Each shows the criteria (what the control requires), "
                       "the condition (what the documents show), the evidence, the gap, a recommendation and space for "
                       "management's response.", S["small"]))
        story.append(Spacer(1, 6))
    for f in m.findings:
        story.append(_finding_block(f, S))
        story.append(Spacer(1, 10))
    story.append(PageBreak())

    # ------------------------------------------------------------ action plan
    story += [P("Remediation action plan", S["h1"])]
    if m.findings:
        rows = [[P(x, S["head"]) for x in ("Ref", "Control", "Risk", "Recommended action", "Owner", "Due", "Status")]]
        for f in m.findings:
            rows.append([P(f.ref, S["cellb"]), P(f"{f.control_id} {f.title}", S["cell"], 120),
                         Paragraph(f"<font color='#{M.LEVEL_HEX[f.level]}'><b>{f.level}</b></font><br/>{f.score}", S["cell"]),
                         P(f.recommendation, S["cell"], 500), P(f.owner, S["cell"], 80), P(f.due, S["cell"], 20), P(f.treatment, S["cell"], 20)])
        story.append(_table(rows, [1.1 * cm, 3.3 * cm, 1.5 * cm, BODY_W - 12.5 * cm, 2.3 * cm, 1.9 * cm, 1.4 * cm]))
    else:
        story.append(P("No actions required.", S["body"]))
    story.append(Spacer(1, 6))
    story.append(P("Owners, dates and status are proposals from the tool until management confirms them.", S["small"]))
    story.append(PageBreak())

    # ------------------------------------------------------------ appendices
    story += [P("Appendices", S["h1"]), P("Appendix A: documents reviewed", S["h2"])]
    if m.docs:
        rows = [[P(x, S["head"]) for x in ("Document", "Sections", "Words", "SHA-256 (first 16)", "Notes")]]
        for d in m.docs:
            rows.append([P(d.get("name", ""), S["cell"], 200), P(d.get("chunks", ""), S["cell"]), P(d.get("words", ""), S["cell"]),
                         P(str(d.get("sha256", "-"))[:16], S["cell"]), P(d.get("warning", ""), S["cell"], 200)])
        story.append(_table(rows, [BODY_W - 12.3 * cm, 1.6 * cm, 1.5 * cm, 3.7 * cm, 5.5 * cm]))
        story.append(P("The SHA-256 value identifies the exact file reviewed. Recompute it to confirm a document has not changed since the assessment.", S["small"]))
    else:
        story.append(P("No documents recorded.", S["body"]))
    story += [P("Appendix B: control-by-control results", S["h2"])]
    rows = [[P(x, S["head"]) for x in ("Control", "Title", "Verdict", "Quote verified", "Reviewer", "Review")]]
    for a in m.results:
        rows.append([P(a.control_id, S["cellb"], 20), P(a.title, S["cell"], 120),
                     Paragraph(f"<font color='#{M.STATUS_HEX[a.status]}'><b>{a.status}</b></font>", S["cell"]),
                     P("Yes" if a.citation_verified else "No", S["cell"]), P(a.reviewer or "-", S["cell"], 60), P(a.review_status, S["cell"])])
    story.append(_table(rows, [1.6 * cm, BODY_W - 12.4 * cm, 2.4 * cm, 2.1 * cm, 3.4 * cm, 2.9 * cm]))
    if m.na:
        story += [P("Appendix C: controls marked not applicable", S["h2"]),
                  P("A reviewer recorded why each control does not apply. Auditors should confirm these justifications; they are excluded from scoring.", S["small"]), Spacer(1, 4)]
        rows = [[P(x, S["head"]) for x in ("Control", "Title", "Justification", "Marked by", "Date")]]
        for a in m.na:
            rows.append([P(a.control_id, S["cellb"], 30), P(a.title, S["cell"], 120), P(a.reviewer_note, S["cell"], 500), P(a.reviewer, S["cell"], 60), P(a.reviewed_at, S["cell"], 20)])
        story.append(_table(rows, [2.6 * cm, 3.4 * cm, BODY_W - 11.4 * cm, 2.8 * cm, 2.6 * cm]))
    story += [P(f"Appendix {'D' if m.na else 'C'}: limitations and disclaimer", S["h2"]), P(M.CAVEAT, S["body"]), Spacer(1, 14)]

    sig = Table([[P("Prepared by", S["lab"]), P("Approved by", S["lab"])],
                 [P(f"{info.assessor_name or '____________________'}<br/>{info.assessor_title}", S["cell"]),
                  P(f"{info.approver_name or '____________________'}<br/>{info.approver_title}", S["cell"])],
                 [P("Signature: ______________________", S["cell"]), P("Signature: ______________________", S["cell"])],
                 [P("Date: ____________________________", S["cell"]), P("Date: ____________________________", S["cell"])]],
                colWidths=[BODY_W / 2] * 2)
    sig.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 7), ("LINEABOVE", (0, 0), (-1, 0), 0.8, NAVY), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(KeepTogether([P("Sign-off", S["h2"]), sig]))

    doc.multiBuild(story, canvasmaker=NumberedCanvas)
    return buf.getvalue()


def _img(data: bytes, width: float) -> Image:
    iw, ih = ImageReader(io.BytesIO(data)).getSize()
    return Image(io.BytesIO(data), width=width, height=width * ih / iw)


def _finding_block(f: M.Finding, S) -> Table:
    lab = lambda t: P(t, S["lab"])  # noqa: E731
    evid: list = [P(f"“{f.quote}”" if f.quote else "No supporting passage found in the documents provided.",
                    S["quote"] if f.quote else S["cell"])]
    if f.source:
        evid.append(P(("Verified in " if f.verified else "Unverified, cited from ") + f.source, S["small"]))
    status = Paragraph(f"<font color='#{M.STATUS_HEX[f.status]}'><b>{f.status}</b></font>", S["cell"])
    rows = [
        [Paragraph(f"<font color='white'><b>{f.ref}  |  {escape(clean(f.control_id))} {escape(clean(f.title))}   -   {f.level} ({f.score})</b></font>", S["head"]), ""],
        [lab("Domain / verdict"), [P(f.domain, S["cell"]), status]],
        *([[lab("Specification"), P(f.requirement + (". " + M.ADDRESSABLE_NOTE if f.requirement == "Addressable" else ""), S["cell"])]] if f.requirement else []),
        [lab("Criteria"), P(f.criteria or "See control description in the framework.", S["cell"], 900)],
        [lab("Condition"), P(f.condition or "-", S["cell"], 900)],
        [lab("Evidence"), evid],
        [lab("Gap"), P(f.gap or "-", S["cell"], 900)],
        [lab("Recommendation"), P(f.recommendation + (" (AI-drafted, to be confirmed by the control owner)" if f.ai_drafted else ""), S["cell"], 1200)],
        [lab("Owner / due date"), P(f"{f.owner}  |  due {f.due or 'to be agreed'}  |  status {f.treatment}", S["cell"])],
    ]
    if f.reviewer_note:
        rows.append([lab("Reviewer note"), P(f"{f.reviewer_note} ({f.reviewer})", S["cell"])])
    rows.append([lab("Management response"), P("To be completed by the control owner.", S["small"])])
    t = Table(rows, colWidths=[3.2 * cm, BODY_W - 3.2 * cm], splitByRow=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C(M.LEVEL_HEX[f.level])), ("SPAN", (0, 0), (1, 0)),
        ("BACKGROUND", (0, 1), (0, -1), LIGHT), ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    return t
