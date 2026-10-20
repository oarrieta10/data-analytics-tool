"""Per-client mapping memory and learned synonyms (no shared cross-client profiles)."""
import pytest

from gtpfit.ingest import suggest_mapping
from gtpfit.ingest.mapping import WmsProfile, load_wms_profile, save_wms_profile, saved_mapping_for
from gtpfit.ingest.schema import remember_synonym, synonyms

HDR = ["Item Number", "Item Description", "Unit Length", "Unit Width", "Unit Height", "Unit Weight", "Case Pack",
       "Case Length", "Case Width", "Case Height", "Case Weight", "Category", "Sub Category"]
COLS = {"sku": "Item Number", "unit_l": "Unit Length", "unit_w": "Unit Width", "unit_h": "Unit Height",
        "unit_weight": "Unit Weight", "case_qty": "Case Pack"}


@pytest.fixture(autouse=True)
def learned_file(tmp_path, monkeypatch):
    """Every test gets its own learned-synonym file (never the repo's)."""
    monkeypatch.setenv("GTPFIT_SYNONYMS", str(tmp_path / "learned.yaml"))
    return tmp_path / "learned.yaml"


def _prof():
    return WmsProfile(name="Acme", columns={"item_master": COLS}, headers={"item_master": HDR})


def test_saved_mapping_reloads_for_matching_headers():
    assert saved_mapping_for(_prof(), "item_master", HDR) == COLS
    resent = HDR[:-1] + ["Brand"]  # a refreshed extract with one column swapped
    assert saved_mapping_for(_prof(), "item_master", resent) == COLS


def test_saved_mapping_not_used_for_a_different_file():
    other = ["SKU", "Desc", "L", "W", "H", "Wt", "Pack"]
    assert saved_mapping_for(_prof(), "item_master", other) is None
    mostly_same_but_key_missing = [h for h in HDR if h != "Item Number"] + ["Part"]
    assert saved_mapping_for(_prof(), "item_master", mostly_same_but_key_missing) is None
    assert saved_mapping_for(_prof(), "orders", HDR) is None
    assert saved_mapping_for(None, "item_master", HDR) is None


def test_profile_keeps_headers_and_units(tmp_path):
    p = WmsProfile(name="Acme", units={"length": "cm", "weight": "kg"}, columns={"item_master": COLS},
                   headers={"item_master": HDR}, saved_at="2026-10-21T10:00:00")
    save_wms_profile(p, tmp_path / "wms_acme.yaml")
    back = load_wms_profile(tmp_path / "wms_acme.yaml")
    assert back.units == {"length": "cm", "weight": "kg"} and back.headers == {"item_master": HDR}


def test_remember_synonym_changes_the_next_suggestion(learned_file):
    hdr = ["STOCKCODE", "Unit Length", "Unit Width", "Unit Height", "Unit Weight", "Case Pack"]
    assert "sku" not in suggest_mapping(hdr, "item_master").mapping
    assert remember_synonym("item_master", "sku", "STOCKCODE") is True
    assert suggest_mapping(hdr, "item_master").mapping["sku"] == "STOCKCODE"
    assert "stockcode" in synonyms("item_master", "sku")
    assert learned_file.exists()
    assert remember_synonym("item_master", "sku", "Stock-Code") is False  # same name once normalised
    assert remember_synonym("item_master", "sku", "Item Number") is False  # already built in


def test_remember_synonym_rejects_unknown_fields():
    with pytest.raises(ValueError):
        remember_synonym("item_master", "colour", "CLR")
