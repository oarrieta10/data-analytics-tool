"""Scope: what part of the operation the analysis designs for (site, channel, order type, status, dates).

Stage 0: scope columns survive ingest (new canonical fields, x_ pass-through extras) and gtpfit.tables.load
applies the client's saved scope.  Uses tests/fixtures/scope_client (see its README).
"""
import dataclasses
from pathlib import Path

import pandas as pd
import pytest

from gtpfit.ingest import IngestParams, ingest_folder, ingest_table, suggest_mapping, write_outputs

FIX = Path(__file__).resolve().parent / "fixtures"
SCOPE = FIX / "scope_client"


@pytest.fixture(scope="module")
def ingested():
    return ingest_folder(SCOPE, strict=False)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A workspace holding the scope client's ingest outputs, as the workbench writes them."""
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    return tmp_path / "ws"


def _ingest_into(ws, result, client="Acme"):
    write_outputs(result, ws / client / "_ingest", client=client)
    return client


# ---- stage 0: schema and mapping ----------------------------------------------------------------------

def test_scope_fields_map_from_typical_headers():
    m = suggest_mapping(["Order Nbr", "Order Date", "Ship Date", "Warehouse", "Sales Channel", "Order Type",
                         "Order Status", "Item Number", "Qty Shipped"], "orders").mapping
    assert m["site"] == "Warehouse" and m["channel"] == "Sales Channel" and m["order_type"] == "Order Type"
    assert m["order_status"] == "Order Status" and m["ship_date"] == "Ship Date"
    assert m["order_date"] == "Order Date"
    assert suggest_mapping(["Item", "On Hand", "DC"], "inventory").mapping["site"] == "DC"
    assert suggest_mapping(["Item", "Qty Received", "Receipt Date", "Facility"], "receipts").mapping["site"] == \
        "Facility"


def test_order_type_is_not_channel():
    m = suggest_mapping(["Order", "Date", "Item", "Qty", "Order Type"], "orders").mapping
    assert m.get("order_type") == "Order Type" and "channel" not in m


def test_required_field_keeps_a_contested_header():
    # only a ship date: it is the order date (required), not the optional ship_date
    m = suggest_mapping(["Order Nbr", "Ship Date", "Item Number", "Qty Shipped"], "orders").mapping
    assert m["order_date"] == "Ship Date" and "ship_date" not in m


def test_optional_ship_date_never_blocks(tmp_path):
    o = pd.read_csv(SCOPE / "orders.csv", dtype=str)
    o.loc[o.index[:50], "Ship Date"] = "not a date"
    p = tmp_path / "orders.csv"
    o.to_csv(p, index=False)
    r = ingest_table(p, "orders")
    assert not [i for i in r.issues if i.severity == "block"], [i for i in r.issues if i.severity == "block"]
    assert r.table["ship_date"].isna().sum() >= 50
    assert len(r.table) == len(o)  # nothing set aside for an optional date
    assert any(i.code == "unparseable_values" and i.field == "ship_date" for i in r.issues)


def test_low_cardinality_extras_survive_as_x_columns(ingested):
    o = ingested.tables["orders"]
    assert "x_carrier" in o and set(o["x_carrier"].dropna()) == {"UPS", "FedEx", "LTL"}  # values as sent
    assert not [c for c in o if "customer" in c]  # 1,000+ distinct values: not kept
    note = ingested.issues[ingested.issues.code == "extra_columns"].message.iat[0]
    assert "Carrier" in note and "Customer ID" in note
    for c in ("site", "channel", "order_type", "order_status", "ship_date"):
        assert c in o
    assert "site" in ingested.tables["inventory"] and "site" not in ingested.tables["receipts"]


def test_extras_threshold_is_a_param(tmp_path):
    r = ingest_table(SCOPE / "orders.csv", "orders", params=IngestParams(extra_max_distinct=2))
    assert "x_carrier" not in r.table  # three carriers > 2


# ---- stage 0: the loader ----------------------------------------------------------------------------

def test_tables_load_all_rows_without_a_saved_scope(ws, ingested):
    from gtpfit import tables
    client = _ingest_into(ws, ingested)
    t = tables.load(client)
    assert set(t) == {"item_master", "orders", "receipts", "inventory"}
    assert len(t["orders"]) == len(ingested.tables["orders"])


def test_tables_load_applies_the_saved_scope(ws, ingested):
    from gtpfit import tables
    from gtpfit.scope import ScopeSpec, save_scope
    client = _ingest_into(ws, ingested)
    save_scope(client, ScopeSpec(include={"site": ["DC-East"]}, note="DC-East only"))
    t = tables.load(client)
    assert set(t["orders"]["site"]) == {"DC-East"} and set(t["inventory"]["site"]) == {"DC-East"}
    assert len(tables.load(client, scoped=False)["orders"]) == len(ingested.tables["orders"])


def test_tables_load_needs_an_ingest(ws):
    from gtpfit import tables
    with pytest.raises(FileNotFoundError, match="Ingest"):
        tables.load("Nobody")


# ---- metadata ---------------------------------------------------------------------------------------

def test_every_scope_param_has_metadata():
    from gtpfit.ingest.params import BLOCKS, UNITS, meta, tooltip
    from gtpfit.scope import ScopeParams
    for f in dataclasses.fields(ScopeParams):
        m = meta(f.name, ScopeParams)
        for k in ("block", "label", "what", "why", "effect", "unit", "source", "confidence"):
            assert isinstance(m.get(k), str) and m[k].strip(), (f.name, k)
        assert m["block"] in BLOCKS and m["unit"] in UNITS
        assert tooltip(f.name, ScopeParams).startswith(m["what"])
