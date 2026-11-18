"""Excel + Markdown readiness reports."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

from .schemas import Assessment, RiskItem
from .scoring import STATUS_ORDER

STATUS_FILL = {
    "Met": "C6EFCE",
    "Partially met": "FFEB9C",
    "Not met": "FFC7CE",
    "No evidence": "D9D9D9",
}
LEVEL_FILL = {"Critical": "F4B6B6", "High": "F8CBAD", "Medium": "FFE699", "Low": "E2EFDA"}
HEADER_FILL = PatternFill("solid", start_color="1F3864", end_color="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")
_THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

CAVEAT = (
    "This is an automated document review by a local language model. It checks whether the supplied documents "
    "describe each control; it does not test whether controls operate effectively. A control is only rated "
    "Met / Partially met / Not met when the model quotes a passage that was verified to exist in your documents; "
    "otherwise it is reported as No evidence. All AI-drafted remediation text must be reviewed by a qualified person."
)


def _fill(hex_colour: str) -> PatternFill:
    return PatternFill("solid", start_color=hex_colour, end_color=hex_colour)


def _write_table(ws, top: int, headers: list[str], rows: list[list], widths: list[int] | None = None) -> int:
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=top, column=j, value=h)
        c.fill, c.font, c.border = HEADER_FILL, HEADER_FONT, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for i, row in enumerate(rows, start=top + 1):
        for j, v in enumerate(row, start=1):
            c = ws.cell(row=i, column=j, value=v)
            c.border = BORDER
            c.alignment = Alignment(vertical="top", wrap_text=True)
    if widths:
        for j, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(j)].width = w
    return top + len(rows)


def write_xlsx(path: Path, meta: dict, assessments: list[Assessment], risks: list[RiskItem], summary: dict, docs: list[dict]) -> Path:
    wb = Workbook()

    # ------------------------------------------------------------ Summary
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Compliance Readiness Report"
    ws["A1"].font = Font(bold=True, size=16)
    ws["A2"] = meta.get("framework", "")
    ws["A2"].font = Font(italic=True, color="595959")

    info = [
        ("Generated", meta.get("generated", "")),
        ("Language model", meta.get("model", "")),
        ("Retrieval mode", meta.get("retrieval", "")),
        ("Controls assessed", summary["overall"]["total"]),
        ("Documents reviewed", len(docs)),
        ("Overall readiness", summary["overall"]["score"]),
        ("Findings needing human review", summary["needs_review"]),
    ]
    for i, (k, v) in enumerate(info, start=4):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        c = ws.cell(row=i, column=2, value=v)
        c.alignment = Alignment(horizontal="left")
        if k == "Overall readiness":
            c.number_format = "0%"
            c.font = Font(bold=True, size=13)

    row = 4 + len(info) + 1
    ws.cell(row=row, column=1, value="Readiness by domain").font = Font(bold=True, size=12)
    header_row = row + 1
    headers = ["Domain", "Controls", *STATUS_ORDER, "Readiness"]
    rows = [
        [d["domain"], d["total"], *[d["counts"][s] for s in STATUS_ORDER], d["score"]]
        for d in summary["domains"]
    ]
    last = _write_table(ws, header_row, headers, rows)
    for r in range(header_row + 1, last + 1):
        ws.cell(row=r, column=7).number_format = "0%"
    for j, s in enumerate(STATUS_ORDER, start=3):
        ws.cell(row=header_row, column=j).fill = _fill(STATUS_FILL[s])
        ws.cell(row=header_row, column=j).font = Font(bold=True, color="000000")

    if rows:
        chart = BarChart()
        chart.type = "bar"
        chart.title = "Readiness by domain"
        chart.style = 10
        chart.add_data(Reference(ws, min_col=7, min_row=header_row, max_row=last), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=header_row + 1, max_row=last))
        chart.y_axis.scaling.min, chart.y_axis.scaling.max = 0, 1
        chart.y_axis.number_format = "0%"
        chart.legend = None
        chart.height, chart.width = 7, 14
        ws.add_chart(chart, "J4")

    row = last + 2
    ws.cell(row=row, column=1, value="Top risks").font = Font(bold=True, size=12)
    top = [[r.risk_id, f"{r.control_id} {r.title}", r.level, r.score, r.due_date] for r in risks[:10]]
    last = _write_table(ws, row + 1, ["Risk", "Control", "Level", "Score", "Due"], top)
    for r in range(row + 2, last + 1):
        ws.cell(row=r, column=3).fill = _fill(LEVEL_FILL[ws.cell(row=r, column=3).value])

    row = last + 2
    ws.cell(row=row, column=1, value="Important").font = Font(bold=True)
    ws.merge_cells(start_row=row + 1, start_column=1, end_row=row + 1, end_column=8)
    c = ws.cell(row=row + 1, column=1, value=CAVEAT)
    c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[row + 1].height = 75

    for col, w in zip("ABCDEFG", [34, 40, 14, 14, 14, 14, 12]):
        ws.column_dimensions[col].width = w

    # ----------------------------------------------------------- Controls
    wc = wb.create_sheet("Control Assessment")
    rows = [
        [
            a.control_id, a.domain, a.title, a.status, a.quote, a.source_doc, a.page,
            a.reasoning, a.missing, "Yes" if a.citation_verified else "No",
            "Yes" if a.needs_review else "", a.note, a.review_status, a.reviewer, a.reviewer_note,
        ]
        for a in assessments
    ]
    heads = ["ID", "Domain", "Control", "Status", "Evidence quote (verified)", "Source document", "Page",
             "Reasoning", "Gap / missing", "Citation verified", "Needs review", "Note",
             "Review status", "Reviewer", "Reviewer note"]
    last = _write_table(wc, 1, heads, rows, [7, 22, 34, 14, 55, 24, 7, 45, 40, 11, 11, 40, 13, 16, 30])
    for r in range(2, last + 1):
        wc.cell(row=r, column=4).fill = _fill(STATUS_FILL[wc.cell(row=r, column=4).value])
    wc.freeze_panes = "D2"
    wc.auto_filter.ref = f"A1:{get_column_letter(len(heads))}{max(last, 2)}"

    # ------------------------------------------------------- Risk register
    wr = wb.create_sheet("Risk Register")
    rows = [
        [r.risk_id, r.control_id, r.title, r.domain, r.status, r.gap, r.likelihood, r.impact, r.score, r.level,
         r.action_plan, r.owner_role, r.effort, r.due_date, r.treatment, "AI draft" if r.ai_drafted else "Template"]
        for r in risks
    ]
    heads = ["Risk ID", "Control", "Title", "Domain", "Status", "Gap", "Likelihood (1-5)", "Impact (1-5)", "Score",
             "Level", "Remediation plan", "Owner (role)", "Effort", "Target date", "Treatment status", "Plan source"]
    last = _write_table(wr, 1, heads, rows, [8, 8, 30, 20, 13, 40, 11, 10, 8, 10, 55, 22, 9, 12, 12, 11])
    for r in range(2, last + 1):
        wr.cell(row=r, column=10).fill = _fill(LEVEL_FILL[wr.cell(row=r, column=10).value])
        wr.cell(row=r, column=5).fill = _fill(STATUS_FILL[wr.cell(row=r, column=5).value])
    wr.freeze_panes = "D2"
    wr.auto_filter.ref = f"A1:{get_column_letter(len(heads))}{max(last, 2)}"

    # --------------------------------------------------------- Policy drafts
    wp = wb.create_sheet("Policy Drafts")
    rows = [[r.control_id, r.title, r.level, r.policy_snippet, "AI draft" if r.ai_drafted else "Template"] for r in risks]
    last = _write_table(wp, 1, ["Control", "Title", "Risk level", "Draft policy statement (review before use)", "Source"], rows,
                        [9, 34, 11, 100, 11])
    wp.freeze_panes = "A2"

    # ------------------------------------------------------------ Documents
    wd = wb.create_sheet("Documents")
    rows = [[d["name"], d["chunks"], d["words"], d.get("warning", "")] for d in docs]
    _write_table(wd, 1, ["Document", "Chunks", "Words", "Warning"], rows, [50, 10, 10, 60])

    for sheet in wb.worksheets:  # print-friendly: landscape, one page wide
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def write_markdown(path: Path, meta: dict, assessments: list[Assessment], risks: list[RiskItem], summary: dict, docs: list[dict]) -> Path:
    o = summary["overall"]
    lines = [
        "# Compliance Readiness Report",
        "",
        f"**Framework:** {meta.get('framework', '')}  ",
        f"**Generated:** {meta.get('generated', '')}  ",
        f"**Model:** {meta.get('model', '')} ({meta.get('retrieval', '')} retrieval)  ",
        f"**Documents reviewed:** {len(docs)}  ",
        f"**Controls assessed:** {o['total']}",
        "",
        f"## Overall readiness: {o['score']:.0%}",
        "",
        " | ".join(f"{s}: {o['counts'][s]}" for s in STATUS_ORDER),
        "",
        "## Readiness by domain",
        "",
        "| Domain | Controls | Met | Partially met | Not met | No evidence | Readiness |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in summary["domains"]:
        c = d["counts"]
        lines.append(f"| {d['domain']} | {d['total']} | {c['Met']} | {c['Partially met']} | {c['Not met']} | {c['No evidence']} | {d['score']:.0%} |")

    lines += ["", "## Top 10 risks", "", "| Risk | Control | Level | Score | Due | Owner |", "|---|---|---|---|---|---|"]
    for r in risks[:10]:
        lines.append(f"| {r.risk_id} | {r.control_id} {r.title} | {r.level} | {r.score} | {r.due_date} | {r.owner_role} |")

    lines += ["", "## Gaps by domain"]
    for d in summary["domains"]:
        gaps = [a for a in assessments if a.domain == d["domain"] and a.status != "Met"]
        if not gaps:
            continue
        lines += ["", f"### {d['domain']}", ""]
        for a in gaps:
            lines.append(f"- **{a.control_id} {a.title}** ({a.status}): {a.missing or a.reasoning}")

    if summary["needs_review"]:
        lines += ["", "## Findings needing human review", ""]
        for a in assessments:
            if a.needs_review:
                lines.append(f"- **{a.control_id} {a.title}**: {a.note}")

    lines += ["", "## Method and limits", "", CAVEAT, ""]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
