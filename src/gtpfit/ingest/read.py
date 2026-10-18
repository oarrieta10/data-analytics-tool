"""read.py — read one client file into a DataFrame of strings, plus facts about the file.

    read_raw(path, sheet=None) -> (DataFrame[str], meta dict)

CSV/TXT/TSV: UTF-8 files go through DuckDB (fast on multi-million-row
exports, delimiter auto-detected); other encodings (Windows-1252, UTF-16) fall
back to pandas and are reported.  XLSX/XLSM: first sheet unless one is named.
Every value stays text here; coerce.py decides types.  Blank cells become NA.

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


def read_raw(path, sheet=None) -> tuple[pd.DataFrame, dict]:
    path = Path(path)
    ext = path.suffix.lower()
    meta = dict(path=str(path), format=None, encoding=None, sheet=None)
    if ext in CSV_EXT:
        enc = _sniff_encoding(path)
        headers, delim = _header_line(path, enc)
        meta.update(format="csv", encoding=enc)
        if enc == "utf-8":
            import duckdb
            rel = duckdb.read_csv(str(path), header=True, all_varchar=True, sep=delim)
            df = rel.df()
        else:
            df = pd.read_csv(path, dtype=str, encoding=enc, sep=delim, keep_default_na=False, na_values=[""])
    elif ext in EXCEL_EXT:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True)
        ws = wb[sheet] if sheet else wb.worksheets[0]
        headers = [("" if c is None else str(c)).strip() for c in next(ws.iter_rows(max_row=1, values_only=True), ())]
        meta.update(format="excel", sheet=ws.title)
        wb.close()
        df = pd.read_excel(path, sheet_name=meta["sheet"], dtype=object)
        df = df.map(_cell_text)
    elif ext == ".xls":
        raise ValueError(f"{path.name}: legacy .xls is not read; save it as .xlsx or .csv")
    else:
        raise ValueError(f"{path.name}: unsupported file type {ext}")
    df.columns = [str(c).strip() for c in headers][: len(df.columns)] if len(headers) == len(df.columns) else \
        [str(c).strip() for c in df.columns]
    df = df.apply(lambda s: s.str.strip() if (s.dtype == object or pd.api.types.is_string_dtype(s)) else s)
    df = df.replace("", pd.NA)
    meta.update(data_rows=len(df), headers=list(df.columns), duplicate_headers=_dupes(headers))
    return df, meta


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
