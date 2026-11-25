"""Universal evidence checklist: common evidence items mapped to the controls they can support.

The catalogue lives in data/evidence_catalog.json. Mapping shows where to look. It does not
prove a control is met: a reviewer must still check content, scope and dates.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from .frameworks import natural_key

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "evidence_catalog.json"


def load_catalog(path: Path = CATALOG_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def items_for(catalog: dict, framework_id: str | None) -> list[dict]:
    """Items relevant to one framework (with a 'controls' list), or all items when framework_id is None."""
    out = []
    for it in catalog["items"]:
        ids = sorted(it["maps_to"].get(framework_id, []), key=natural_key) if framework_id else []
        if framework_id is None or ids:
            out.append({**it, "controls": ids})
    return out


def coverage(catalog: dict, framework_id: str, control_ids: list[str]) -> tuple[int, int, list[str]]:
    """(controls with at least one evidence item, total controls, ids with none)."""
    hit = {c for it in catalog["items"] for c in it["maps_to"].get(framework_id, [])}
    missing = [c for c in control_ids if c not in hit]
    return len(control_ids) - len(missing), len(control_ids), missing


def to_markdown(catalog: dict, framework_id: str | None, label: str = "") -> str:
    lines = [f"# Evidence checklist{(' - ' + label) if label else ''}", "", catalog["note"], ""]
    for it in items_for(catalog, framework_id):
        lines += [f"## {it['id']} {it['name']}", f"- **Shows:** {it['proves']}",
                  f"- **Typical evidence:** {it['examples']}",
                  f"- **Type / refresh:** {it['type']} / {it['refresh']}"]
        if framework_id:
            lines.append(f"- **Supports controls:** {', '.join(it['controls'])}")
        lines += ["- **Status:** [ ] collected  [ ] reviewed  Owner: ________  Date: ________", ""]
    return "\n".join(lines)


def to_xlsx(catalog: dict, framework_id: str | None, label: str = "") -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Evidence checklist"
    head = ["Ref", "Evidence item", "What it shows", "Typical evidence", "Type", "Refresh"]
    if framework_id:
        head.append("Supports controls")
    head += ["Collected (Y/N)", "Owner", "Date", "Reviewer notes"]
    ws.append(head)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3864")
    for it in items_for(catalog, framework_id):
        row = [it["id"], it["name"], it["proves"], it["examples"], it["type"], it["refresh"]]
        if framework_id:
            row.append(", ".join(it["controls"]))
        ws.append(row + ["", "", "", ""])
    for col, w in zip("ABCDEFGHIJK", (7, 38, 48, 52, 11, 12, 34, 14, 16, 12, 30)):
        ws.column_dimensions[col].width = w
    for r in ws.iter_rows(min_row=2):
        for c in r:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "C2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
