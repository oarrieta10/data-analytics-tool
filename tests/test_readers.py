"""Reader registry: every supported client file type reads to the same canonical table as the CSV.

Synthetic data only.  .xls / .xlsb / .xlsm fixtures are made by tests/fixtures/make_excel_fixtures.py
(needs Excel on the machine) and committed; their tests skip if the files are missing.
"""
import gzip
import shutil
import sqlite3
import zipfile
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from gtpfit.ingest import ingest_folder, ingest_sources, ingest_table, load_wms_profile
from gtpfit.ingest.classify import classify, list_sources
from gtpfit.ingest.readers import Source, UnsupportedFileError, detect_format
from gtpfit.synth import generate, load_profile

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
WMS = ROOT / "wms_profiles" / "synthetic.yaml"
TINY = dict(n_skus=300, ecom_orders_per_day=60, b2b_orders_per_day=3, start_date="2025-01-06", days=40)
FILES = {"item_master": "item_master", "orders": "orders", "receipts": "receipts", "inventory": "inventory_snapshots"}


@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    out = tmp_path_factory.mktemp("readers_src")
    generate(load_profile(**TINY), out)
    return out


@pytest.fixture(scope="module")
def base(ds):
    """Canonical tables from the CSVs: what every other format must reproduce."""
    return {t: ingest_table(ds / f"{f}.csv", t).table for t, f in FILES.items()}


def _raw(ds, table):
    return pd.read_csv(ds / f"{FILES[table]}.csv", dtype=str, keep_default_na=False)


def _typed(ds, table):
    """The CSV with real column types (numbers, text), as databases and binary formats store it."""
    return duckdb.sql(f"select * from read_csv('{(ds / (FILES[table] + '.csv')).as_posix()}', header=true)").df()


def _same(source, table, base):
    got = ingest_table(source, table)
    blocks = [i for i in got.issues if i.severity == "block"]
    assert not blocks, blocks
    pd.testing.assert_frame_equal(got.table.reset_index(drop=True), base[table].reset_index(drop=True),
                                  check_dtype=False)


# ---- delimited text: any extension, any delimiter, sniffed encoding -------------------

@pytest.mark.parametrize("name,sep,enc", [
    ("items.txt", "\t", "utf-8"), ("items.tab", "\t", "utf-8"), ("items.psv", "|", "utf-8"),
    ("items.dat", "|", "utf-8"), ("items_semicolon.csv", ";", "utf-8"), ("items_utf16.txt", "\t", "utf-16"),
])
def test_delimited_text(ds, base, tmp_path, name, sep, enc):
    p = tmp_path / name
    _raw(ds, "item_master").to_csv(p, sep=sep, index=False, encoding=enc)
    _same(p, "item_master", base)


def test_cp1252_text_detected(ds, base, tmp_path):
    df = _raw(ds, "item_master")
    df.loc[0, "Item Description"] = "Café crème brûlée"
    p = tmp_path / "items.csv"
    df.to_csv(p, index=False, encoding="cp1252")
    t = ingest_table(p, "item_master").table
    assert t["description"].iat[0] == "Café crème brûlée"


# ---- compressed ------------------------------------------------------------------------

def test_gzip(ds, base, tmp_path):
    p = tmp_path / "orders.csv.gz"
    with gzip.open(p, "wt", encoding="utf-8", newline="") as f:
        _raw(ds, "orders").to_csv(f, index=False)
    _same(p, "orders", base)


def test_zip_single_and_multi_member(ds, base, tmp_path):
    one = tmp_path / "one.zip"
    with zipfile.ZipFile(one, "w") as z:
        z.write(ds / "receipts.csv", "receipts.csv")
    _same(one, "receipts", base)
    many = tmp_path / "export.zip"
    with zipfile.ZipFile(many, "w") as z:
        for f in FILES.values():
            z.write(ds / f"{f}.csv", f"exports/{f}.csv")
        z.writestr("readme.md", "not data")
    parts = {s.part for s in list_sources([many])}
    assert parts == {f"exports/{f}.csv" for f in FILES.values()}
    _same(Source(many, "exports/inventory_snapshots.csv"), "inventory", base)


# ---- Excel -------------------------------------------------------------------------------

def _workbook(ds, path):
    """Three sheets: notes, an item master under title rows, and orders."""
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame({"Notes": ["Exported from WMS", "Do not edit"]}).to_excel(w, sheet_name="Read me", index=False)
        _raw(ds, "item_master").to_excel(w, sheet_name="Items", index=False, startrow=3)
        ws = w.sheets["Items"]
        ws["A1"], ws["A2"] = "ACME Corp item master export", "Run date: 2025-02-14"
        _raw(ds, "orders").to_excel(w, sheet_name="Orders", index=False)


def test_xlsx_sheets_and_title_rows(ds, base, tmp_path):
    p = tmp_path / "export.xlsx"
    _workbook(ds, p)
    assert {s.part for s in list_sources([p])} == {"Read me", "Items", "Orders"}
    _same(Source(p, "Items"), "item_master", base)
    _same(Source(p, "Orders"), "orders", base)


@pytest.mark.parametrize("ext", [".xls", ".xlsb", ".xlsm"])
def test_excel_binary_and_macro_formats(ext):
    src, conv = FIX / "excel_sample.xlsx", FIX / f"excel_sample{ext}"
    if not conv.exists():
        pytest.skip(f"{conv.name} missing: run tests/fixtures/make_excel_fixtures.py on a machine with Excel")
    for sheet, table in [("Items", "item_master"), ("Orders", "orders")]:
        a = ingest_table(Source(src, sheet), table).table
        b = ingest_table(Source(conv, sheet), table).table
        pd.testing.assert_frame_equal(b, a, check_dtype=False)
    assert classify(Source(conv, "Items")).table == "item_master"


# ---- JSON ----------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["receipts.json", "receipts.jsonl", "receipts.json.gz"])
def test_json(ds, base, tmp_path, name):
    p = tmp_path / name
    df = _raw(ds, "receipts")
    text = df.to_json(orient="records", lines=name.endswith(".jsonl"))
    if name.endswith(".gz"):
        with gzip.open(p, "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        p.write_text(text, encoding="utf-8")
    _same(p, "receipts", base)


# ---- columnar: Feather / Arrow / ORC / Parquet ------------------------------------------

@pytest.mark.filterwarnings("ignore:Feather V1")  # old format, still read: that is the point
@pytest.mark.parametrize("name", ["inv.feather", "inv.arrow", "inv_v1.feather", "inv.orc", "inv.parquet"])
def test_columnar(ds, base, tmp_path, name):
    import pyarrow as pa
    import pyarrow.feather as feather
    import pyarrow.orc as orc
    import pyarrow.parquet as pq
    t = pa.Table.from_pandas(_typed(ds, "inventory"), preserve_index=False)
    p = tmp_path / name
    if name.endswith(".orc"):
        orc.write_table(t, p)
    elif name.endswith(".parquet"):
        pq.write_table(t, p)
    else:
        feather.write_feather(t, p, version=1 if "v1" in name else 2)
    _same(p, "inventory", base)


# ---- database files: SQLite and DuckDB -----------------------------------------------------

def test_sqlite_tables(ds, base, tmp_path):
    p = tmp_path / "wms.sqlite"
    with sqlite3.connect(p) as con:
        _typed(ds, "item_master").to_sql("ITEM", con, index=False)
        _typed(ds, "orders").to_sql("ORDER_LINES", con, index=False)
    assert {s.part for s in list_sources([p])} == {"ITEM", "ORDER_LINES"}
    _same(Source(p, "ITEM"), "item_master", base)
    _same(Source(p, "ORDER_LINES"), "orders", base)


def test_duckdb_tables(ds, base, tmp_path):
    p = tmp_path / "wms.duckdb"
    con = duckdb.connect(str(p))
    for t in ("receipts", "inventory"):
        con.execute(f"create table {t} as select * from read_csv('{(ds / (FILES[t] + '.csv')).as_posix()}', header=true)")
    con.close()
    assert {s.part for s in list_sources([p])} == {"receipts", "inventory"}
    _same(Source(p, "receipts"), "receipts", base)
    _same(Source(p, "inventory"), "inventory", base)


# ---- content beats extension; clear refusal -------------------------------------------------

def test_content_beats_extension(ds, base, tmp_path):
    pq_as_dat = tmp_path / "items.dat"
    duckdb.sql(f"copy (select * from read_csv('{(ds / 'item_master.csv').as_posix()}', header=true)) "
               f"to '{pq_as_dat.as_posix()}' (format parquet)")
    assert detect_format(pq_as_dat) == "parquet"
    _same(pq_as_dat, "item_master", base)
    csv_as_xlsx = tmp_path / "orders.xlsx"
    shutil.copy(ds / "orders.csv", csv_as_xlsx)
    assert detect_format(csv_as_xlsx) == "delimited"
    _same(csv_as_xlsx, "orders", base)


@pytest.mark.parametrize("name,content", [("report.pdf", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + bytes(range(256)) * 20),
                                          ("blob.bin", bytes(range(256)) * 40)])
def test_unsupported_file_lists_types(tmp_path, name, content):
    p = tmp_path / name
    p.write_bytes(content)
    with pytest.raises(UnsupportedFileError) as e:
        ingest_table(p, "orders")
    msg = str(e.value)
    assert name in msg and "Parquet" in msg and "Excel" in msg and "SQLite" in msg


# ---- guess, then confirm ----------------------------------------------------------------------

def test_classify_guesses_each_table(ds, tmp_path):
    for table, f in FILES.items():
        g = classify(Source(ds / f"{f}.csv"))
        assert g.table == table and g.confidence == "high", (table, g)
    p = tmp_path / "export.xlsx"
    _workbook(ds, p)
    assert classify(Source(p, "Items")).table == "item_master"
    assert classify(Source(p, "Orders")).table == "orders"
    assert classify(Source(p, "Read me")).table is None


def test_ingest_sources_matches_folder(ds):
    prof = load_wms_profile(WMS)
    a = ingest_folder(ds, profile=prof)
    assign = {t: [Source(ds / f"{f}.csv")] for t, f in FILES.items()}
    b = ingest_sources(assign, profile=prof)
    for t in FILES:
        pd.testing.assert_frame_equal(a.tables[t], b.tables[t])
    assert list(a.issues["code"]) == list(b.issues["code"])
