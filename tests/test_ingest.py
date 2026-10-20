"""Tests for ingest: mapping, reading, units, dates, refusals, on the frozen sample client in tests/fixtures."""
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gtpfit.ingest import (IngestError, IngestParams, ingest_folder, ingest_table, load_wms_profile,
                           save_wms_profile, suggest_mapping)

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
WMS = FIX / "sample_wms_profile.yaml"
# frozen sample client (see tests/fixtures/README.md): Dec 15 -> 120 days, so Dec 25 and Jan 1 closures
SAMPLE = FIX / "sample_client"
EXPECTED = FIX / "sample_expected.csv"  # planted problems the tests must find


@pytest.fixture(scope="module")
def ds():
    return SAMPLE  # read-only: tests that change files work on copies (_clone)


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

def test_sample_folder_ingests_without_blocks(res, ds):
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
    truth = pd.read_csv(EXPECTED)
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
    r2 = ingest_folder(folder, profile=load_wms_profile(WMS), params=params, accept={"excel_row_limit": "client confirmed the file ends there"})
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
    truth = pd.read_csv(EXPECTED)
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


# ---- real-client learnings: parquet, blank rows, classification, names, cases + eaches, density ----

def _to_parquet(src: Path, dst: Path, date_col=None, date_fmt="%m/%d/%Y"):
    import duckdb
    sel = "*" if not date_col else f"* replace (strptime(\"{date_col}\", '{date_fmt}') as \"{date_col}\")"
    types = f", types={{'{date_col}': 'VARCHAR'}}" if date_col else ""
    duckdb.sql(f"copy (select {sel} from read_csv('{src.as_posix()}', header=true{types})) "
               f"to '{dst.as_posix()}' (format parquet)")


def test_parquet_reads_like_csv(ds, res, tmp_path):
    _to_parquet(ds / "item_master.csv", tmp_path / "im.parquet")
    _to_parquet(ds / "orders.csv", tmp_path / "o.parquet", date_col="Order Date")  # real timestamp column
    a = ingest_table(tmp_path / "im.parquet", "item_master").table
    pd.testing.assert_frame_equal(a, res.tables["item_master"], check_dtype=False)
    b = ingest_table(tmp_path / "o.parquet", "orders")
    assert not [i for i in b.issues if i.severity == "block"]
    want = res.tables["orders"]
    assert len(b.table) == len(want)
    assert b.table["order_date"].equals(want["order_date"])
    assert (b.table["qty"].to_numpy() == want["qty"].to_numpy()).all()


def test_blank_rows_ignored_not_rejected(ds, res, tmp_path):
    folder = _clone(ds, tmp_path)
    with open(folder / "orders.csv", "a", encoding="utf-8") as f:
        f.write(",,,,,\n" * 500)
    r = ingest_folder(folder, profile=load_wms_profile(WMS))
    i = r.issues
    row = i[(i.table == "orders") & (i.code == "blank_rows")]
    assert row["count"].iat[0] == 500 and row.severity.iat[0] == "info"
    assert len(r.tables["orders"]) == len(res.tables["orders"])
    assert len(r.rejects["orders"]) == len(res.rejects["orders"])


def test_padded_to_row_limit_warns_only(ds, tmp_path):
    folder = _clone(ds, tmp_path)
    n = len(_raw(ds, "orders"))
    with open(folder / "orders.csv", "a", encoding="utf-8") as f:
        f.write(",,,,,\n" * 100)
    r = ingest_folder(folder, profile=load_wms_profile(WMS), params=IngestParams(excel_row_limits=(n + 100,)))
    assert not r.blocked
    assert "excel_padded" in _codes(r, "warn")


def test_classification_needs_identifying_fields(ds, tmp_path):
    rec = _raw(ds, "receipts").rename(columns={"Qty Received": "Inventory Units"})
    inv = _raw(ds, "inventory_snapshots").rename(columns={"On Hand Qty": "Sum_Eaches"})
    folder = _clone(ds, tmp_path, receipts=rec, inventory_snapshots=inv)
    pd.DataFrame({"SKU": ["A1", "B2"], "Note": ["x", "y"]}).to_csv(folder / "sku_list.csv", index=False)
    r = ingest_folder(folder, strict=False)
    assert r.sources["receipts"].endswith("receipts.csv")
    assert r.sources["inventory"].endswith("inventory_snapshots.csv")
    assert r.sources["item_master"].endswith("item_master.csv")
    assert "unrecognised_file" in _codes(r, "warn")  # a bare SKU list is not an item master


@pytest.mark.parametrize("table,headers,want", [
    ("orders", ["ORDER NUMBER", "SKU", "ORDER DATE", "UNITS PICKED", "Cases", "Channel", "ORDER START OF HOUR"],
     {"order_id": "ORDER NUMBER", "order_date": "ORDER DATE", "sku": "SKU", "qty": "UNITS PICKED",
      "cases": "Cases", "pick_secs": "ORDER START OF HOUR", "channel": "Channel"}),
    ("orders", ["Warehouse", "Pick Date", "Pick Time", "Order", "SKU", "Cases", "Eaches", "Channel"],
     {"order_id": "Order", "order_date": "Pick Date", "sku": "SKU", "qty": "Eaches", "cases": "Cases",
      "pick_secs": "Pick Time", "channel": "Channel"}),
    ("receipts", ["RECEIPT NUMBER", "SKU", "RECEIPT DATE", "UNITS RECEIVED", "Eaches Received", "PO Number"],
     {"receipt_id": "RECEIPT NUMBER", "po": "PO Number", "receipt_date": "RECEIPT DATE", "sku": "SKU",
      "qty": "UNITS RECEIVED"}),
    ("receipts", ["Warehouse", "Shipment", "PO Number", "Case Number", "SKU", "Inventory Units", "Receipt Date"],
     {"receipt_id": "Shipment", "po": "PO Number", "receipt_date": "Receipt Date", "sku": "SKU",
      "qty": "Inventory Units"}),
    ("inventory", ["INV DATE", "SKU", "STORAGE LOCATION", "OH UNITS"],
     {"snapshot_date": "INV DATE", "sku": "SKU", "location": "STORAGE LOCATION", "on_hand": "OH UNITS"}),
    ("inventory", ["Inventory Snapshot", "Sku", "Sum_Eaches", "Snapshot Date"],
     {"snapshot_date": "Snapshot Date", "sku": "Sku", "on_hand": "Sum_Eaches"}),
    ("item_master", ["SKU", "SKU DESC", "UNIT L", "UNIT W", "UNIT H", "UNIT WEIGHT", "CASE QTY", "CASE L"],
     {"sku": "SKU", "description": "SKU DESC", "unit_l": "UNIT L", "unit_w": "UNIT W", "unit_h": "UNIT H",
      "unit_weight": "UNIT WEIGHT", "case_qty": "CASE QTY", "case_l": "CASE L"}),
    ("item_master", ["Company ", "SKU", "Style Description", "Std Unit Length", "Std Unit Width", "Std Unit Height",
                     "Unit Weight", "Std Case Quantity"],
     {"sku": "SKU", "description": "Style Description", "unit_l": "Std Unit Length", "unit_w": "Std Unit Width",
      "unit_h": "Std Unit Height", "unit_weight": "Unit Weight", "case_qty": "Std Case Quantity"}),
])
def test_common_wms_headers_map(table, headers, want):
    m = suggest_mapping(headers, table)
    assert {k: v for k, v in m.mapping.items() if k in want} == want, m.mapping
    assert not m.ambiguous


def _cases_eaches(ds, res, tmp_path, drop_pack_for=None):
    """Sample orders rewritten the way some WMSs export: full cases in 'Cases', loose units in 'Eaches'."""
    o = _raw(ds, "orders")
    im = res.tables["item_master"].drop_duplicates("sku").set_index("sku")["case_qty"]
    sku = o["Item Number"].str.strip().str.upper()
    q = pd.to_numeric(o["Qty Shipped"])
    cq = sku.map(im)
    full = (o["Channel"] == "Store") & (cq > 1) & (q > 0) & (q % cq == 0)
    o["Cases"] = np.where(full, (q / cq).fillna(0), 0).astype(int)
    o["Eaches"] = np.where(full, 0, q).astype(int)
    o = o.drop(columns=["Qty Shipped"])
    imr = _raw(ds, "item_master")
    if drop_pack_for is not None:
        imr.loc[imr["Item Number"] == drop_pack_for, "Case Pack"] = ""
    folder = _clone(ds, tmp_path, orders=o, item_master=imr)
    prof = load_wms_profile(WMS)
    prof.columns["orders"] = {**prof.columns["orders"], "qty": "Eaches", "cases": "Cases"}
    return folder, prof, int(full.sum()), o, full


def test_cases_plus_eaches_recovers_units(ds, res, tmp_path):
    folder, prof, n_full, _, _ = _cases_eaches(ds, res, tmp_path)
    assert n_full > 100
    r = ingest_folder(folder, profile=prof)
    o, base = r.tables["orders"], res.tables["orders"]
    assert len(o) == len(base)  # full-case lines are not set aside as zero quantity
    assert (o["qty"].to_numpy() == base["qty"].to_numpy()).all()
    assert int(o["full_case"].sum()) == int((o["cases"] > 0).sum())
    assert {"eaches", "cases", "full_case"} <= set(o.columns)


def test_cases_without_case_pack_flagged(ds, res, tmp_path):
    _, _, _, o, full = _cases_eaches(ds, res, tmp_path / "a")
    victim = o.loc[full, "Item Number"].str.strip().str.upper().value_counts().index[0]
    folder, prof, _, o, full = _cases_eaches(ds, res, tmp_path / "b", drop_pack_for=victim)
    r = ingest_folder(folder, profile=prof)
    i = r.issues
    row = i[i.code == "case_pack_missing"]
    n = int((full & (o["Item Number"].str.strip().str.upper() == victim)).sum())
    assert row.severity.iat[0] == "warn" and row["count"].iat[0] == n
    oo = r.tables["orders"]
    assert oo.loc[(oo["sku"] == victim) & (oo["cases"] > 0), "qty"].isna().all()


def test_low_density_goods_fit_inches_pounds():
    from gtpfit.ingest.units import assess
    items = pd.DataFrame({"unit_l": [14.0] * 50, "unit_w": [10.0] * 50, "unit_h": [6.0] * 50,
                          "unit_weight": [1.46] * 50})  # ~3 lb/ft3: light apparel / footwear boxes
    a = assess(items, ["unit_l", "unit_w", "unit_h"], "unit_weight", "unit", IngestParams())
    assert a.set_index(["length", "weight"]).loc[("in", "lb"), "plausible"]


# ---- database-style exports: run-together names, joined tables ---------------------------

@pytest.mark.parametrize("table,headers,want", [
    ("orders", ["warehouse", "closedat", "releasedat", "id", "part_number", "part_description", "customerorderno",
                "quantity", "pickedquantity", "order_status"],
     {"order_id": "customerorderno", "order_date": "closedat", "sku": "part_number", "qty": "pickedquantity"}),
    ("receipts", ["id", "asnid", "partnumber", "po", "quantity", "receivedat"],
     {"receipt_id": "asnid", "sku": "partnumber", "po": "po", "qty": "quantity", "receipt_date": "receivedat"}),
    ("item_master", ["partnumber", "partdescription", "length", "width", "height", "weight", "qpc"],
     {"sku": "partnumber", "description": "partdescription", "unit_l": "length", "case_qty": "qpc"}),
])
def test_database_style_headers_map(table, headers, want):
    m = suggest_mapping(headers, table)
    assert {k: v for k, v in m.mapping.items() if k in want} == want, m.mapping


def test_unused_duplicate_columns_do_not_block(tmp_path):
    p = tmp_path / "joined.csv"
    p.write_text("id,Item Number,Item Description,Unit Length,Unit Width,Unit Height,Unit Weight,Case Pack,id\n"
                 "1,A1,Thing,5,4,3,1.5,12,9\n2,B2,Other,6,5,4,2.0,6,8\n", encoding="utf-8")
    r = ingest_table(p, "item_master")
    assert "duplicate_header" in {i.code for i in r.issues if i.severity == "info"}
    assert r.table is not None and list(r.table["sku"]) == ["A1", "B2"]
    p2 = tmp_path / "joined2.csv"
    p2.write_text("Item Number,Unit Weight,Item Number\nA1,1.5,B\n", encoding="utf-8")
    r2 = ingest_table(p2, "item_master")
    assert "duplicate_header" in {i.code for i in r2.issues if i.severity == "block"}


def test_two_generic_columns_are_not_high_confidence(tmp_path):
    from gtpfit.ingest.classify import classify
    p = tmp_path / "x.csv"
    p.write_text("partnumber,quantity,weight\nA1,3,1.2\n", encoding="utf-8")
    assert classify(p).confidence != "high"


# ---- item master: unit dims, unit weight and case pack are required columns ---------------------

ITEM_REQUIRED = ["sku", "unit_l", "unit_w", "unit_h", "unit_weight", "case_qty"]


def test_item_master_required_fields():
    from gtpfit.ingest.schema import TABLES
    assert [f.name for f in TABLES["item_master"] if f.required] == ITEM_REQUIRED


@pytest.mark.parametrize("drop,field", [("Unit Length", "unit_l"), ("Unit Width", "unit_w"),
                                        ("Unit Height", "unit_h"), ("Unit Weight", "unit_weight"),
                                        ("Case Pack", "case_qty")])
def test_item_master_refused_without_a_required_column(ds, tmp_path, drop, field):
    p = tmp_path / "im.csv"
    _raw(ds, "item_master").drop(columns=[drop]).to_csv(p, index=False)
    r = ingest_table(p, "item_master")
    blocks = [i for i in r.issues if i.severity == "block"]
    assert [i.code for i in blocks] == ["missing_required_column"]
    assert field in blocks[0].message
    assert r.table is None


def test_blank_required_values_are_kept_and_reported(ds, tmp_path):
    """The column is required; blank values in it are what quality/ and backfill/ are for, so rows stay."""
    im = _raw(ds, "item_master")
    im.loc[0:9, ["Unit Length", "Unit Width", "Unit Height"]] = ""
    im.loc[10:19, "Unit Weight"] = ""
    im.loc[20:24, "Case Pack"] = ""
    im.loc[30, "Unit Length"] = "N/A"
    p = tmp_path / "im.csv"
    im.to_csv(p, index=False)
    r = ingest_table(p, "item_master")
    assert not [i for i in r.issues if i.severity == "block"]
    assert len(r.table) == len(im) and len(r.rejects) == 0
    blank = {i.field: i for i in r.issues if i.code == "blank_values"}
    cols = {"unit_l": "Unit Length", "unit_w": "Unit Width", "unit_h": "Unit Height", "unit_weight": "Unit Weight",
            "case_qty": "Case Pack"}
    for fld, col in cols.items():
        assert blank[fld].count == (im[col] == "").sum() and blank[fld].severity == "info", fld
    bad = [i for i in r.issues if i.code == "unparseable_values" and i.field == "unit_l"]
    assert bad and bad[0].severity == "warn" and bad[0].count == 1
    assert pd.isna(r.table["unit_l"].iat[30])


def test_blank_sku_still_sets_the_row_aside(ds, tmp_path):
    im = _raw(ds, "item_master")
    im.loc[40, "Item Number"] = ""
    p = tmp_path / "im.csv"
    im.to_csv(p, index=False)
    r = ingest_table(p, "item_master")
    assert len(r.rejects) == 1 and r.rejects["reason"].iat[0] == "missing_value"
    assert len(r.table) == len(im) - 1


def test_item_list_without_case_pack_is_guessed_then_asked_for_it(tmp_path):
    """Guessing is looser than ingest: sizes and a SKU make it an item master; ingest then names what is missing."""
    from gtpfit.ingest.classify import classify
    p = tmp_path / "packaging.csv"
    p.write_text("part_number,part_description,quantity,length,width,height,weight\nA1,Thing,1,5,4,3,1.2\n",
                 encoding="utf-8")
    g = classify(p)
    assert g.table == "item_master" and g.confidence == "high"
    r = ingest_table(p, "item_master")
    blocks = [i for i in r.issues if i.severity == "block"]
    assert [i.code for i in blocks] == ["missing_required_column"] and "case_qty" in blocks[0].message
