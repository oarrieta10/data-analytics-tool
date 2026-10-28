"""Unit volume: an optional item-master field for files that give each unit's volume (often instead of its
length, width and height).  The unit comes from the header (m3, cm3, ft3, in3, litres) or the client's saved
units, and is converted to cubic inches; with neither, the values are left blank with a warning."""
import pandas as pd
import pytest

from gtpfit.ingest import ingest_table
from gtpfit.ingest.checks import ISSUE_TEXT
from gtpfit.ingest.mapping import WmsProfile
from gtpfit.ingest.schema import FIELD_HELP, FIELD_LABEL, TABLES
from gtpfit.ingest.units import VOLUME_TO_IN3, header_unit

M3_IN3 = 61023.744094732


def _items(tmp_path, vol_header, vols, dims=("", "", "")):
    df = pd.DataFrame({"SKU": [f"A{i}" for i in range(len(vols))], "Length (in)": dims[0], "Width (in)": dims[1],
                       "Height (in)": dims[2], "Weight (lb)": 1.0, "Case Pack": 12, vol_header: vols})
    p = tmp_path / "items.csv"
    df.to_csv(p, index=False)
    return p


@pytest.mark.parametrize("header,unit", [("pce_volume_m3", "m3"), ("Unit Volume (cm3)", "cm3"),
                                         ("unit cube cu ft", "ft3"), ("Item Volume (in³)", "in3"),
                                         ("unit volume (L)", "l"), ("Unit Cube (ft3)", "ft3")])
def test_volume_unit_from_header(header, unit):
    assert header_unit(header, "volume") == unit


def test_conversions_are_exact():
    assert VOLUME_TO_IN3["ft3"] == 1728
    assert VOLUME_TO_IN3["m3"] == pytest.approx(M3_IN3)
    assert VOLUME_TO_IN3["l"] * 1000 == pytest.approx(M3_IN3)


def test_field_is_declared_and_described():
    f = next(f for f in TABLES["item_master"] if f.name == "unit_volume")
    assert (f.kind, f.required, f.group) == ("volume", False, "unit")
    assert FIELD_LABEL["unit_volume"] and FIELD_HELP["item_master"]["unit_volume"]
    assert "volume_unit_unknown" in ISSUE_TEXT


def test_m3_converted_to_cubic_inches(tmp_path):
    p = _items(tmp_path, "pce_volume_m3", [0.00182, 0.000990, None])
    res = ingest_table(p, "item_master")
    v = res.table.set_index("sku")["unit_volume"]
    assert v["A0"] == pytest.approx(0.00182 * M3_IN3) and v["A1"] == pytest.approx(0.00099 * M3_IN3)
    assert pd.isna(v["A2"])
    assert any(i.code == "unit_converted" and "volume" in i.message for i in res.issues)


def test_saved_units_used_when_header_is_silent(tmp_path):
    p = _items(tmp_path, "Unit Volume", [2.0])
    prof = WmsProfile(name="x", columns={"item_master": {
        "sku": "SKU", "unit_l": "Length (in)", "unit_w": "Width (in)", "unit_h": "Height (in)",
        "unit_weight": "Weight (lb)", "case_qty": "Case Pack", "unit_volume": "Unit Volume"}}, units={"volume": "ft3"})
    res = ingest_table(p, "item_master", profile=prof)
    assert res.table["unit_volume"].iat[0] == pytest.approx(2 * 1728)


def test_unknown_volume_unit_left_blank_with_warning(tmp_path):
    p = _items(tmp_path, "Unit Volume", [2.0, 3.0])
    res = ingest_table(p, "item_master")
    assert "unit_volume" in res.mapping.mapping
    assert res.table["unit_volume"].isna().all()
    assert any(i.code == "volume_unit_unknown" and i.severity == "warn" for i in res.issues)
