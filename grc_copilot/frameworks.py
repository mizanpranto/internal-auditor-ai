"""Framework (control library) loader.

Every *.json file in the frameworks/ folder shows up in the app's framework
switcher. To add your own, copy frameworks/iso27001_2022.json and edit it
(or upload it from the Control Library page).
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT
from .schemas import Control

FRAMEWORK_DIR = ROOT / "frameworks"
DEFAULT_FRAMEWORK = "iso27001_2022"
SHORT_NAMES = {"iso27001_2022": "ISO 27001", "soc2_tsc": "SOC 2", "nist_csf_2": "NIST CSF 2.0", "custom": "Custom"}


def list_frameworks() -> dict[str, dict]:
    """{id: {name, short, count, path}} for each valid JSON file in frameworks/ (default framework first)."""
    found: dict[str, dict] = {}
    for p in sorted(FRAMEWORK_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            name = str(data.get("framework", p.stem))
            found[p.stem] = {"name": name, "short": SHORT_NAMES.get(p.stem, name.split(" (")[0]),
                             "count": len(data["controls"]), "path": p}
        except (json.JSONDecodeError, KeyError, OSError, TypeError):
            continue  # a broken file must not break the app
    return dict(sorted(found.items(), key=lambda kv: kv[0] != DEFAULT_FRAMEWORK))


def load_framework(path: str | Path) -> tuple[dict, list[Control]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    controls = [Control(**c) for c in data["controls"]]
    ids = [c.id for c in controls]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"Duplicate control ids in framework: {dupes}")
    meta = {k: v for k, v in data.items() if k != "controls"}
    return meta, controls


def natural_key(control_id: str) -> tuple:
    """Sort '5.2' before '5.10'."""
    return tuple(int(p) if p.isdigit() else p for p in control_id.split("."))
