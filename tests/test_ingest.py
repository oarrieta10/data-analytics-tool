"""Tests for ingest: mapping, reading, units, dates, refusals — scored on gtpfit.synth data."""
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gtpfit.synth import load_profile, generate
from gtpfit.ingest import (IngestError, IngestParams, ingest_folder, ingest_table, load_wms_profile,
                           save_wms_profile, suggest_mapping)

ROOT = Path(__file__).resolve().parents[1]
WMS = ROOT / "wms_profiles" / "synthetic.yaml"
# Dec -> Apr: covers the Dec 25 and Jan 1 closures, ~45k order lines
SMALL = dict(n_skus=600, ecom_orders_per_day=150, b2b_orders_per_day=4, start_date="2024-12-15", days=120)


@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    out = tmp_path_factory.mktemp("ingest_src")
    generate(load_profile(**SMALL), out)
    return out


@pytest.fixture(scope="module")
def res(ds):
    return ingest_folder(ds, profile=load_wms_profile(WMS))


def _clone(ds, dst, **frames):
    """Copy the client files to dst, replacing any given as name=DataFrame."""
    dst.mkdir(parents=True, exist_ok=True)
    for f in ("item_master.csv", "orders.csv", "receipts.csv", "inventory_snapshots.csv"):
        shutil.copy(ds / f, dst / f)
    for name, df in frames.items():
        df.to_csv(dst / f"{name}.csv", index=False)
    return dst


def _raw(ds, name):
    return pd.read_csv(ds / f"{name}.csv", dtype=str, keep_default_na=False)


def _codes(result, severity=None):
    i = result.issues
    return set(i.loc[i.severity == severity, "code"] if severity else i["code"])


# ---- happy path -------------------------------------------------------------

def test_synthetic_folder_ingests_without_blocks(res, ds):
    assert not res.blocked, res.issues[res.issues.severity == "block"]
    assert set(res.tables) == {"item_master", "orders", "receipts", "inventory"}
    o = res.tables["orders"]
    assert list(o.columns[:4]) == ["order_id", "order_date", "sku", "qty"]
    assert pd.api.types.is_datetime64_any_dtype(o["order_date"])
    assert (o["qty"] > 0).all()
    raw = len(_raw(ds, "orders"))
    assert len(o) + len(res.rejects["orders"]) == raw
    assert len(res.tables["item_master"]) == len(_raw(ds, "item_master"))


def test_closures_are_reported_not_blocked(res):
    i = res.issues
    closed = i[(i.table == "orders") & (i.code == "closed_days")]
    assert len(closed) == 1 and closed["count"].iat[0] == 2  # Dec 25, Jan 1


def test_declared_units_confirmed_by_data(res):
    u = res.units.set_index("group")
    assert u.loc["unit", "length"] == "in" and u.loc["unit", "weight"] == "lb"
    assert u.loc["unit", "method"] == "profile" and u.loc["unit", "plausible"]


# ---- mapping ----------------------------------------------------------------

def test_auto_mapping_matches_saved_profile(ds):
    prof = load_wms_profile(WMS)
    for table, fname in [("item_master", "item_master"), ("orders", "orders"),
                         ("receipts", "receipts"), ("inventory", "inventory_snapshots")]:
        headers = list(_raw(ds, fname).columns)
        m = suggest_mapping(headers, table)
        assert m.mapping == prof.columns[table], table
        assert not m.ambiguous


def test_auto_mapping_variant_headers():
    m = suggest_mapping(["SKU", "Order No.", "Ship Date", "Units", "Each Length (in)", "Carton Length"], "orders")
    assert m.mapping["sku"] == "SKU" and m.mapping["order_id"] == "Order No."
    assert m.mapping["order_date"] == "Ship Date" and m.mapping["qty"] == "Units"
    im = suggest_mapping(["Item", "Each Length (in)", "Carton Length", "Master Pack Qty"], "item_master")
    assert im.mapping["unit_l"] == "Each Length (in)" and im.mapping["case_l"] == "Carton Length"
    assert im.mapping["case_qty"] == "Master Pack Qty"


def test_folder_without_profile_classifies_files_and_detects(ds):
    r = ingest_folder(ds)
    assert not r.blocked, r.issues[r.issues.severity == "block"]
    assert r.sources["orders"].endswith("orders.csv")
    assert r.units.set_index("group").loc["unit", "method"] == "statistical"
    assert "units_statistical" in _codes(r, "warn")


def test_profile_roundtrip(tmp_path):
    p = load_wms_profile(WMS)
    save_wms_profile(p, tmp_path / "copy.yaml")
    assert load_wms_profile(tmp_path / "copy.yaml") == p


def test_missing_required_column_blocks(ds, tmp_path):
    o = _raw(ds, "orders").drop(columns=["Item Number"])
    r = ingest_folder(_clone(ds, tmp_path, orders=o), profile=load_wms_profile(WMS), strict=False)
    assert "missing_required_column" in _codes(r, "block")
    with pytest.raises(IngestError):
        ingest_folder(tmp_path, profile=load_wms_profile(WMS))


# ---- refusals ---------------------------------------------------------------

def test_bad_qty_rows_quarantined_match_answer_key(res, ds):
    rej = res.rejects["orders"]
    bad = rej[rej.reason == "bad_qty"]
    got = set(zip(bad["Order Nbr"], bad["Item Number"].str.strip().str.upper()))
    truth = pd.read_csv(ds / "_truth/defects.csv")
    keys = truth.loc[truth.defect == "zero_or_negative_qty", "key"].str.removeprefix("row:")
    want = {(k.split("|")[0], k.split("|")[1].strip().upper()) for k in keys}
    assert got == want
    assert "bad_qty" in _codes(res, "warn")


def test_bad_qty_over_threshold_blocks(ds, tmp_path):
    o = _raw(ds, "orders")
    o.loc[o.index[::20], "Qty Shipped"] = "-1"  # 5%
    r = ingest_folder(_clone(ds, tmp_path, orders=o), profile=load_wms_profile(WMS), strict=False)
    assert "bad_qty" in _codes(r, "block")


def test_excel_row_limit_blocks_and_can_be_accepted(ds, tmp_path):
    o = _raw(ds, "orders").head(3000)
    folder = _clone(ds, tmp_path, orders=o)
    params = IngestParams(excel_row_limits=(3000,))
    r = ingest_folder(folder, profile=load_wms_profile(WMS), params=params, strict=False)
    assert "excel_row_limit" in _codes(r, "block")
    r2 = ingest_folder(folder, profile=load_wms_profile(WMS), params=params, accept=["excel_row_limit"])
    assert not r2.blocked and "excel_row_limit" in _codes(r2, "accepted")


def test_date_gap_blocks(ds, tmp_path):
    o = _raw(ds, "orders")
    d = pd.to_datetime(o["Order Date"], format="%m/%d/%Y")
    o = o[~d.between("2025-02-03", "2025-02-12")]
    r = ingest_folder(_clone(ds, tmp_path, orders=o), profile=load_wms_profile(WMS), strict=False)
    gaps = r.issues[(r.issues.code == "date_gap") & (r.issues.table == "orders")]
    assert gaps.severity.iat[0] == "block" and "2025-02-03" in gaps.message.iat[0]


# ---- dates ------------------------------------------------------------------

def test_day_first_dates_detected_when_not_declared(ds, tmp_path):
    o = _raw(ds, "orders")
    o["Order Date"] = pd.to_datetime(o["Order Date"], format="%m/%d/%Y").dt.strftime("%d/%m/%Y")
    folder = _clone(ds, tmp_path, orders=o)
    declared = ingest_folder(folder, profile=load_wms_profile(WMS), strict=False)
    assert "date_format_mismatch" in _codes(declared, "block")
    p = load_wms_profile(WMS)
    p.date_format = None
    r = ingest_folder(folder, profile=p)
    base = ingest_folder(ds, profile=load_wms_profile(WMS))
    assert r.tables["orders"]["order_date"].equals(base.tables["orders"]["order_date"])


def test_ambiguous_dates_block(ds, tmp_path):
    o = _raw(ds, "orders")
    d = pd.to_datetime(o["Order Date"], format="%m/%d/%Y")
    o = o[(d.dt.month == 1) & (d.dt.day <= 12)]
    t = ingest_table(_clone(ds, tmp_path, orders=o) / "orders.csv", "orders")
    assert "date_format_ambiguous" in {i.code for i in t.issues if i.severity == "block"}


# ---- units ------------------------------------------------------------------

_CM = ["Unit Length", "Unit Width", "Unit Height", "Case Length", "Case Width", "Case Height"]
_KG = ["Unit Weight", "Case Weight"]


def _metric_item_master(ds, hints: bool):
    im = pd.read_csv(ds / "item_master.csv", dtype={"Item Number": str})
    im[_CM] = (im[_CM] * 2.54).round(2)
    im[_KG] = (im[_KG] / 2.20462262185).round(3)
    if hints:
        im = im.rename(columns={**{c: f"{c} (cm)" for c in _CM}, **{c: f"{c} (kg)" for c in _KG}})
    return im


def test_metric_with_header_hints_is_converted(ds, res, tmp_path):
    folder = _clone(ds, tmp_path, item_master=_metric_item_master(ds, hints=True))
    r = ingest_table(folder / "item_master.csv", "item_master")
    assert not [i for i in r.issues if i.severity == "block"]
    a = r.table.set_index("sku")
    b = res.tables["item_master"].drop_duplicates("sku").set_index("sku")
    a = a[~a.index.duplicated()]
    for c in ["unit_l", "case_h", "unit_weight", "case_weight"]:
        x, y = a[c].reindex(b.index), b[c]
        ok = x.notna() & y.notna() & (y > 0.05)
        np.testing.assert_allclose(x[ok], y[ok], rtol=5e-3, atol=2e-3, err_msg=c)  # cm 2dp, kg 3dp rounding
    u = r.units.set_index("group")
    assert (u["method"] == "header").all() and (u["length"] == "cm").all() and (u["weight"] == "kg").all()


def test_metric_without_hints_or_profile_is_ambiguous(ds, tmp_path):
    folder = _clone(ds, tmp_path, item_master=_metric_item_master(ds, hints=False))
    r = ingest_table(folder / "item_master.csv", "item_master")
    blocks = {i.code: i for i in r.issues if i.severity == "block"}
    assert "unit_ambiguous" in blocks
    assert "cm" in blocks["unit_ambiguous"].message  # tells the consultant what fits


def test_profile_says_inches_but_data_is_cm_blocks(ds, tmp_path):
    folder = _clone(ds, tmp_path, item_master=_metric_item_master(ds, hints=False))
    r = ingest_folder(folder, profile=load_wms_profile(WMS), strict=False)
    assert "unit_mismatch" in _codes(r, "block")


# ---- readers ----------------------------------------------------------------

def test_xlsx_reads_like_csv(ds, res, tmp_path):
    im = pd.read_csv(ds / "item_master.csv", dtype={"Item Number": str}).head(300)
    o = pd.read_csv(ds / "orders.csv", dtype={"Item Number": str, "Order Nbr": str}).head(2000)
    o["Order Date"] = pd.to_datetime(o["Order Date"], format="%m/%d/%Y")  # real Excel date cells
    im.to_excel(tmp_path / "im.xlsx", index=False)
    o.to_excel(tmp_path / "o.xlsx", index=False)
    a = ingest_table(tmp_path / "im.xlsx", "item_master").table
    pd.testing.assert_frame_equal(a.reset_index(drop=True), res.tables["item_master"].head(300).reset_index(drop=True))
    b = ingest_table(tmp_path / "o.xlsx", "orders").table
    want = res.tables["orders"]
    want = want[want["order_id"].isin(set(o["Order Nbr"]))]
    assert set(b["order_date"].dt.date) == set(want["order_date"].dt.date)


def test_cp1252_csv_is_decoded(ds, tmp_path):
    im = _raw(ds, "item_master").head(50)
    im.loc[0, "Item Description"] = "Café Crème 12 oz — Dark"
    p = tmp_path / "im.csv"
    im.to_csv(p, index=False, encoding="cp1252")
    r = ingest_table(p, "item_master")
    assert r.table["description"].iat[0] == "Café Crème 12 oz — Dark"
    assert "encoding_fallback" in {i.code for i in r.issues}


def test_sku_normalisation_counted(res, ds):
    truth = pd.read_csv(ds / "_truth/defects.csv")
    n = (truth.defect == "sku_format_variant").sum()
    i = res.issues
    row = i[(i.table == "orders") & (i.code == "sku_normalised")]
    assert n > 0 and row["count"].iat[0] == n
    assert not res.tables["orders"]["sku"].str.contains(r"^\s|\s$|[a-z]").any()


def test_write_outputs(res, tmp_path):
    from gtpfit.ingest import write_outputs
    out = write_outputs(res, tmp_path / "_ingest")
    import duckdb
    back = duckdb.read_parquet(str(out / "orders.parquet")).df()
    assert len(back) == len(res.tables["orders"])
    assert (out / "issues.csv").exists() and (out / "units.csv").exists()
