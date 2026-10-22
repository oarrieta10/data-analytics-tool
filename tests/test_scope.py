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


# ---- stage 1: what can be filtered, the waterfall, saving -------------------------------------------------

def test_detect_known_fields_first_then_extras(ingested):
    from gtpfit.scope import detect_scope_columns
    cols = detect_scope_columns(ingested.tables["orders"])
    names = [c.name for c in cols]
    assert names[:4] == ["site", "channel", "order_status", "order_type"] and names[4:] == ["x_carrier"]
    site = cols[0]
    assert {v.value for v in site.values} == {"DC-East", "DC-West"}
    assert sum(v.lines for v in site.values) == len(ingested.tables["orders"])
    assert abs(sum(v.share for v in site.values) - 1) < 1e-9


def test_auto_exclusion_ignores_case(ingested):
    from gtpfit.scope import ScopeParams, default_spec, detect_scope_columns
    status = next(c for c in detect_scope_columns(ingested.tables["orders"]) if c.name == "order_status")
    auto = {v.value: v.auto_excluded for v in status.values}
    assert auto == {"Shipped": None, "CANCELLED": "cancel", "Returned": "return"}
    assert default_spec(detect_scope_columns(ingested.tables["orders"])).include == {"order_status": ["Shipped"]}
    none = detect_scope_columns(ingested.tables["orders"], ScopeParams(scope_exclude_keywords=()))
    assert not any(v.auto_excluded for c in none for v in c.values)


def _east_shipped():
    from gtpfit.scope import ScopeSpec
    return ScopeSpec(include={"site": ["DC-East"], "order_status": ["Shipped"]}, note="DC-East, shipped only")


def test_waterfall_adds_up(ingested):
    from gtpfit.scope import ScopeSpec, apply_scope
    spec = ScopeSpec(include={"site": ["DC-East"], "order_status": ["Shipped"]}, date_from="2025-02-01",
                     date_to="2025-12-31")
    r = apply_scope(ingested.tables, spec)
    w = r.waterfall
    n = len(ingested.tables["orders"])
    assert w["filter"].iat[0] == "All lines" and w["remaining"].iat[0] == n and w["filter"].iat[-1] == "In scope"
    assert w["removed"].sum() + w["remaining"].iat[-1] == n
    assert len(r.tables["orders"]) == w["remaining"].iat[-1]
    assert (w["remaining"].diff().fillna(0).iloc[1:-1] == -w["removed"].iloc[1:-1]).all()
    assert r.summary["rows_removed"].sum() >= w["removed"].sum()  # + the related tables' rows
    reasons = set(r.summary.reason)
    assert "excluded by the consultant" in reasons and "outside the date window" in reasons
    assert any(x.startswith("auto-excluded") for x in reasons)


def test_site_filter_reaches_related_tables(ingested):
    from gtpfit.scope import apply_scope
    r = apply_scope(ingested.tables, _east_shipped())
    assert set(r.tables["inventory"]["site"]) == {"DC-East"}
    assert len(r.tables["receipts"]) == len(ingested.tables["receipts"])  # no site column: all kept
    assert "No site column in receipts; sizing will use all sites' receipts." in r.warnings
    assert r.related["inventory"].startswith("site column found")
    assert len(r.tables["item_master"]) == len(ingested.tables["item_master"])


def test_saved_spec_reloads_and_gives_identical_tables(ws, ingested):
    from gtpfit import tables
    from gtpfit.scope import apply_scope, load_scope, save_scope
    client = _ingest_into(ws, ingested)
    save_scope(client, _east_shipped())
    assert load_scope(client).include == _east_shipped().include and load_scope(client).saved_at
    assert load_scope("Beta") is None
    a = tables.load(client)["orders"]
    b = apply_scope(tables.load_unscoped(client), _east_shipped()).tables["orders"]
    pd.testing.assert_frame_equal(a, b)
    summary = pd.read_csv(ws / client / "_scope" / "scope_summary.csv")
    assert list(summary.columns) == ["filter", "value", "rows_removed", "reason"]
    assert summary.loc[summary.value == "DC-West", "rows_removed"].iat[0] > 0


def test_missing_and_new_values_warn_instead_of_failing(ingested):
    from gtpfit.scope import ScopeSpec, apply_scope
    gone = apply_scope(ingested.tables, ScopeSpec(include={"site": ["DC-East", "DC-North"]}))
    assert any("no longer in the data: DC-North" in w for w in gone.warnings)
    new = apply_scope(ingested.tables, ScopeSpec(include={"site": ["DC-East"]}, seen={"site": ["DC-East"]}))
    assert any("New Site / warehouse value(s)" in w and "DC-West" in w for w in new.warnings)
    assert "not in the saved scope (new value)" in set(new.summary.reason)
    skipped = apply_scope(ingested.tables, ScopeSpec(include={"x_zone": ["A"]}))
    assert any("no longer have" in w for w in skipped.warnings) and skipped.lines_in == skipped.lines_all


def test_ship_date_basis_drops_unshipped_with_a_reason(ingested):
    from gtpfit.scope import ScopeSpec, apply_scope
    r = apply_scope(ingested.tables, ScopeSpec(date_from="2025-01-01", date_to="2026-12-31", date_basis="ship_date"))
    cancelled = int((ingested.tables["orders"]["order_status"] == "CANCELLED").sum())
    row = r.summary[r.summary.value == "no ship date"]
    assert int(row.rows_removed.iat[0]) == cancelled
