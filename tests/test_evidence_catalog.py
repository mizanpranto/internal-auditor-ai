import io
from pathlib import Path

import pytest
from openpyxl import load_workbook

from internal_auditor import evidence_catalog as EC
from internal_auditor.frameworks import list_frameworks, load_framework

CAT = EC.load_catalog()
FWS = list_frameworks()


def test_items_are_well_formed_and_unique():
    ids = [i["id"] for i in CAT["items"]]
    assert len(ids) == len(set(ids)) >= 40
    for i in CAT["items"]:
        assert i["name"] and i["proves"] and i["examples"] and i["type"] and i["refresh"] and i["maps_to"]


@pytest.mark.parametrize("fid", list(FWS))
def test_every_mapped_control_exists(fid):
    _, controls = load_framework(FWS[fid]["path"])
    valid = {c.id for c in controls}
    for it in CAT["items"]:
        assert set(it["maps_to"].get(fid, [])) <= valid, (it["id"], fid)


@pytest.mark.parametrize("fid", list(FWS))
def test_every_framework_has_catalog_items_and_high_coverage(fid):
    _, controls = load_framework(FWS[fid]["path"])
    hit, total, missing = EC.coverage(CAT, fid, [c.id for c in controls])
    assert total == len(controls) and hit >= 0.9 * total, missing
    assert EC.items_for(CAT, fid)


def test_unknown_framework_has_no_items_and_all_view_has_everything():
    assert EC.items_for(CAT, "nope") == []
    assert len(EC.items_for(CAT, None)) == len(CAT["items"])


def test_exports():
    md = EC.to_markdown(CAT, "gdpr", "GDPR")
    assert "E27" in md and "Art. 33" in md
    wb = load_workbook(io.BytesIO(EC.to_xlsx(CAT, "hipaa", "HIPAA")))
    ws = wb.active
    assert ws["G1"].value == "Supports controls" and ws.max_row > 20
