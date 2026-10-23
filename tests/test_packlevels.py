"""Item masters with one row per packaging level (each / case / pallet ...) become one row per SKU at ingest:
the unit row gives the item's own size, the smallest case its case pack and size, the pallet its pallet
quantity.  Nothing is dropped silently: every level row is kept in item_master_levels and counted."""
import pandas as pd
import pytest

from gtpfit.ingest import IngestParams, ingest_table, suggest_mapping
from gtpfit.ingest.packlevels import combine_pack_levels, level_role

LONG = pd.DataFrame({
    "Part Number": ["A", "A", "A", "B", "B", "B", "C", "D", "D", "D"],
    "Package Type": ["Part", "Case", "Pallet", "Part", "Case", "Case", "Case", "Part", "SIP", "Pallet"],
    "Quantity": [1, 12, 480, 1, 6, 24, 10, 1, 1, 50],
    "Length": [4.0, 13.0, 48.0, 2.0, 7.0, 13.0, 20.0, 5.0, 9.0, 40.0],
    "Width": [3.0, 10.0, 40.0, 2.0, 5.0, 9.0, 12.0, 4.0, 8.0, 40.0],
    "Height": [2.0, 7.0, 40.0, 1.0, 3.0, 6.0, 8.0, 3.0, 5.0, 30.0],
    "Weight": [0.5, 6.5, 300.0, 0.2, 1.4, 5.1, 9.0, 1.0, 1.4, 60.0],
})


def test_level_roles():
    p = IngestParams()
    assert level_role("Part", 1, p) == "unit" and level_role("EACH", 1, p) == "unit"
    assert level_role("Each", 105, p) == "case"          # an "each" level holding 105 is an inner pack
    assert level_role("Corrugated Case", 12, p) == "case" and level_role("Wood Pallet", 480, p) == "pallet"
    assert level_role("SIP", 1, p) == "unit" and level_role("Mystery", 1, p) == "other"


def test_combine_one_row_per_sku():
    wide = pd.DataFrame({"sku": LONG["Part Number"], "pack_level": LONG["Package Type"],
                         "case_qty": LONG["Quantity"], "unit_l": LONG["Length"], "unit_w": LONG["Width"],
                         "unit_h": LONG["Height"], "unit_weight": LONG["Weight"]})
    out, levels, notes = combine_pack_levels(wide, IngestParams())
    out = out.set_index("sku")
    assert list(out.index) == ["A", "B", "C", "D"]
    a = out.loc["A"]
    assert (a.unit_l, a.unit_weight, a.case_qty, a.case_l, a.case_weight, a.pallet_qty, a.pallet_l) == \
        (4.0, 0.5, 12, 13.0, 6.5, 480, 48.0)
    assert out.loc["B", "case_qty"] == 6 and out.loc["B", "case_l"] == 7.0      # the smallest case pack
    assert pd.isna(out.loc["C", "unit_l"]) and out.loc["C", "case_qty"] == 10   # no unit row: size unknown
    assert out.loc["D", "unit_l"] == 5.0                                         # Part preferred over SIP
    assert pd.isna(out.loc["D", "case_qty"])
    assert len(levels) == len(LONG) and set(levels["role"]) == {"unit", "case", "pallet"}
    unused = levels[~levels["used"]]
    assert sorted(zip(unused["sku"], unused["pack_level"])) == [("B", "Case"), ("D", "SIP")]  # B's 24-pack, D's SIP
    assert "10 rows" in notes and "4 SKUs" in notes and "not used" in notes


def test_pack_level_maps_from_typical_headers():
    for h in ("Package Type", "package_type", "Pack Level", "UOM", "Unit of Measure"):
        m = suggest_mapping(["Part Number", h, "Quantity", "Length", "Width", "Height", "Weight"], "item_master")
        assert m.mapping.get("pack_level") == h, h


def test_ingest_combines_levels(tmp_path):
    p = tmp_path / "item_master.csv"
    LONG.rename(columns={"Length": "Length (in)", "Weight": "Weight (lb)", "Quantity": "Package Qty"}).to_csv(
        p, index=False)
    r = ingest_table(p, "item_master")
    assert not [i for i in r.issues if i.severity == "block"], [i for i in r.issues if i.severity == "block"]
    t = r.table.set_index("sku")
    assert len(t) == 4 and t.loc["A", "case_qty"] == 12 and t.loc["A", "pallet_qty"] == 480
    assert any(i.code == "pack_levels_combined" for i in r.issues)
    assert r.levels is not None and len(r.levels) == len(LONG)


def test_saved_mapping_gains_new_fields():
    from gtpfit.ingest.mapping import WmsProfile, mapping_with_new_fields
    prof = WmsProfile(name="x", columns={"item_master": {"sku": "Part Number", "case_qty": "Quantity",
                                                         "unit_l": "Length", "unit_w": "Width", "unit_h": "Height",
                                                         "unit_weight": "Weight"}})
    headers = list(LONG.columns)
    m = mapping_with_new_fields(prof.columns["item_master"], headers, "item_master")
    assert m["pack_level"] == "Package Type" and m["sku"] == "Part Number"
