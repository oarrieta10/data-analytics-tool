"""read.py — read one client file into a DataFrame, plus facts about the file.

    read_headers(path, sheet=None) -> list of column names (cheap)
    read_raw(path, sheet=None, columns=None) -> (DataFrame, meta dict)

CSV/TXT/TSV: UTF-8 files go through DuckDB (fast on multi-million-row
exports, delimiter auto-detected); other encodings (Windows-1252, UTF-16) fall
back to pandas and are reported.  XLSX/XLSM: first sheet unless one is named.
Parquet: through DuckDB, keeping its own number and date types.
Text files stay text here; coerce.py decides types.  Blank cells become NA.
`columns` (names as read_headers returns them) reads only those columns, so a
wide export loads only what the mapping uses.

meta: format, encoding, sheet, data_rows, headers, duplicate_headers.
"""
from __future__ import annotations

import codecs
import csv
import datetime as dt
from pathlib import Path

import pandas as pd

CSV_EXT = {".csv", ".txt", ".tsv"}
EXCEL_EXT = {".xlsx", ".xlsm"}
PARQUET_EXT = {".parquet"}
_CHUNK = 1 << 20


def _sniff_encoding(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(4)
        if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            return "utf-16"
        f.seek(0)
        dec = codecs.getincrementaldecoder("utf-8")()
        try:
            while chunk := f.read(_CHUNK):
                dec.decode(chunk)
            dec.decode(b"", final=True)
        except UnicodeDecodeError:
            return "cp1252"
    return "utf-8"


def _header_line(path: Path, encoding: str) -> tuple[list, str]:
    with open(path, encoding="utf-8-sig" if encoding == "utf-8" else encoding, newline="") as f:
        sample = f.read(64_000)
    try:
        delim = csv.Sniffer().sniff(sample.splitlines()[0], delimiters=",;\t|").delimiter
    except (csv.Error, IndexError):
        delim = ","
    first = next(csv.reader(sample.splitlines()[:1], delimiter=delim), [])
    return [h.strip() for h in first], delim


def _dupes(headers) -> list:
    seen, d = set(), []
    for h in headers:
        if h in seen and h not in d:
            d.append(h)
        seen.add(h)
    return d


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def read_raw(path, sheet=None, columns=None) -> tuple[pd.DataFrame, dict]:
    path = Path(path)
    ext = path.suffix.lower()
    meta = dict(path=str(path), format=None, encoding=None, sheet=None)
    headers = read_headers(path, sheet)
    keep = [i for i, h in enumerate(headers) if columns is None or h in set(columns)]
    if ext in CSV_EXT:
        enc = _sniff_encoding(path)
        _, delim = _header_line(path, enc)
        meta.update(format="csv", encoding=enc)
        if enc == "utf-8":
            import duckdb
            rel = duckdb.read_csv(str(path), header=True, all_varchar=True, sep=delim)
            df = rel.select(*[_q(rel.columns[i]) for i in keep]).df()
        else:
            df = pd.read_csv(path, dtype=str, encoding=enc, sep=delim, keep_default_na=False, na_values=[""],
                             usecols=keep)
    elif ext in EXCEL_EXT:
        meta.update(format="excel", sheet=sheet or _first_sheet(path))
        df = pd.read_excel(path, sheet_name=meta["sheet"], dtype=object, usecols=keep)
        df = df.map(_cell_text)
    elif ext in PARQUET_EXT:
        import duckdb
        meta.update(format="parquet")
        rel = duckdb.read_parquet(str(path))
        df = rel.select(*[_q(rel.columns[i]) for i in keep]).df()
    elif ext == ".xls":
        raise ValueError(f"{path.name}: legacy .xls is not read; save it as .xlsx or .csv")
    else:
        raise ValueError(f"{path.name}: unsupported file type {ext}")
    df.columns = [headers[i] for i in keep]
    df = df.apply(_clean_text)
    df = df.replace("", pd.NA)
    meta.update(data_rows=len(df), headers=headers, duplicate_headers=_dupes(headers))
    return df, meta


def _clean_text(s: pd.Series) -> pd.Series:
    """Trim text columns; object columns holding times/dates (Parquet TIME) become text like Excel cells."""
    if s.dtype == object:
        first = s.dropna().head(1)
        if len(first) and not isinstance(first.iloc[0], str):
            s = s.map(_cell_text)
        return s.astype("string").str.strip()
    if pd.api.types.is_string_dtype(s):
        return s.str.strip()
    return s


def _first_sheet(path) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True)
    name = wb.worksheets[0].title
    wb.close()
    return name


def read_headers(path, sheet=None) -> list:
    """Header row only — cheap, for working out which table a file holds."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in CSV_EXT:
        return _header_line(path, _sniff_encoding(path))[0]
    if ext in EXCEL_EXT:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True)
        ws = wb[sheet] if sheet else wb.worksheets[0]
        h = [("" if c is None else str(c)).strip() for c in next(ws.iter_rows(max_row=1, values_only=True), ())]
        wb.close()
        return h
    if ext in PARQUET_EXT:
        import duckdb
        return [str(c).strip() for c in duckdb.read_parquet(str(path)).columns]
    return []


def _cell_text(v):
    """Excel cell -> text the way the client would see it (dates as ISO, 12.0 as 12)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return pd.NA
    if isinstance(v, dt.time):
        return v.strftime("%H:%M:%S")
    if isinstance(v, (dt.datetime, dt.date, pd.Timestamp)):
        ts = pd.Timestamp(v)
        return ts.strftime("%Y-%m-%d") if ts == ts.normalize() else ts.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
