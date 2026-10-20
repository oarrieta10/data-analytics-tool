"""Smoke tests for the workbench Ingest page (Streamlit AppTest, no browser)."""
import shutil
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "sample_client"  # frozen sample client
FILES = ("item_master.csv", "orders.csv", "receipts.csv", "inventory_snapshots.csv")


INGEST = """
from gtpfit.app import ingest_page
ingest_page.page()
"""


def test_ingest_page_guesses_and_ingests(tmp_path, monkeypatch):
    """Files already in the client workspace (the uploader can't be driven by AppTest): guess, confirm, ingest."""
    ws = tmp_path / "ws"
    client = ws / "untitled"
    client.mkdir(parents=True)
    for f in FILES:
        shutil.copy(SAMPLE / f, client / f)
    (client / "notes.md").write_text("not data", encoding="utf-8")
    # readable data that matches no table: its guess is None (this crashed the page once)
    (client / "contacts.csv").write_text("Name,Phone\nAna,555\n", encoding="utf-8")
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(ws))
    at = AppTest.from_string(INGEST, default_timeout=180).run()
    assert not at.exception, at.exception
    guesses = next(d.value for d in at.dataframe if "Use as" in d.value.columns).set_index(
        "Source (file > sheet / table)")
    assert guesses.loc["item_master.csv", "Use as"] == "Item master"
    assert guesses.loc["orders.csv", "Use as"] == "Orders"
    assert guesses.loc["receipts.csv", "Use as"] == "Receipts (inbound)"
    assert guesses.loc["inventory_snapshots.csv", "Use as"] == "Inventory"
    assert "notes.md" not in guesses.index  # not a data file
    assert guesses.loc["contacts.csv", "Use as"] == "Ignore"
    assert (guesses.drop(index="contacts.csv")["Confidence"] == "high").all()
    at.button(key="ing::go").click().run()
    assert not at.exception, at.exception
    assert any("Ready" in s.value for s in at.success), [e.value for e in at.error]


# ---- column mapping editor --------------------------------------------------------------

def test_default_mapping_prefers_profile_columns():
    from gtpfit.app.ingest_page import default_mapping
    hdr = ["Item Number", "Qty Received", "createdat", "updatedat"]
    auto = default_mapping("receipts", hdr, None)
    assert auto["sku"] == "Item Number" and "receipt_date" not in auto
    prof = default_mapping("receipts", hdr, {"sku": "Item Number", "receipt_date": "updatedat", "qty": "gone"})
    assert prof == {"sku": "Item Number", "receipt_date": "updatedat"}  # columns not in the file are dropped


def test_effective_profile_merges_choices():
    import yaml
    from gtpfit.app.ingest_page import effective_profile_text
    assert effective_profile_text("name: x\nunits: {length: in}\n", {}) == "name: x\nunits: {length: in}\n"
    out = yaml.safe_load(effective_profile_text("name: x\ncolumns:\n  orders: {sku: SKU}\n",
                                                {"receipts": {"receipt_date": "createdat"}}))
    assert out["columns"] == {"orders": {"sku": "SKU"}, "receipts": {"receipt_date": "createdat"}}
    assert yaml.safe_load(effective_profile_text(None, {"orders": {"sku": "A"}}))["columns"]["orders"] == {"sku": "A"}


def test_chosen_column_lets_unrecognised_date_through(tmp_path):
    """A receipts export whose date column is 'createdat': refused until the consultant picks it."""
    import pandas as pd
    import yaml
    from gtpfit.app.ingest_page import effective_profile_text
    from gtpfit.ingest import ingest_sources, parse_wms_profile, Source
    rec = pd.read_csv(SAMPLE / "receipts.csv", dtype=str)
    rec = rec.rename(columns={"Receipt Date": "createdat"}).assign(updatedat="2030-01-01")
    p = tmp_path / "inbound.csv"
    rec.to_csv(p, index=False)
    assign = {"receipts": [Source(p)]}
    before = ingest_sources(assign, strict=False)
    assert "missing_required_column" in set(before.issues.loc[before.issues.table == "receipts", "code"])
    cols = {"receipt_id": "Receipt Nbr", "po": "PO Nbr", "receipt_date": "createdat", "sku": "Item Number",
            "qty": "Qty Received"}
    prof = parse_wms_profile(yaml.safe_load(effective_profile_text(None, {"receipts": cols})))
    after = ingest_sources(assign, profile=prof, strict=False)
    assert len(after.tables["receipts"]) == len(rec)
    assert not set(after.issues.loc[after.issues.table == "receipts", "severity"]) & {"block"}


def test_ingest_page_shows_column_tabs(tmp_path, monkeypatch):
    client = tmp_path / "ws" / "untitled"
    client.mkdir(parents=True)
    for f in FILES:
        shutil.copy(SAMPLE / f, client / f)
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    at = AppTest.from_string(INGEST, default_timeout=180).run()
    assert not at.exception, at.exception
    assert any("Which column is which" in h.value for h in at.subheader)
    col_tables = [d.value for d in at.dataframe if "Field" in d.value.columns]
    assert len(col_tables) == 4
    orders = next(t for t in col_tables if "order_date" in set(t["Field"])).set_index("Field")
    assert orders.loc["order_date", "Column"].startswith("Order Date · ")  # with example values
    assert not [w for w in at.warning if "Required" in w.value]
