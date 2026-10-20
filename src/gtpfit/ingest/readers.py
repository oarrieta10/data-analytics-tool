"""readers.py — one registry of readers for client files; content decides the format, the extension only breaks ties.

    detect_format(path) -> format name            Source(path, part=None)
    parts(path) -> [sheet / table / zip member] or None
    headers(source) -> column names (stripped)
    read(source, columns=None, limit=None) -> (DataFrame, meta)

Every reader returns the same kind of DataFrame: text columns trimmed with
blanks as NA, numbers and dates kept native where the format stores them
(Parquet, Arrow, databases, Excel date cells), so mapping, units and checks do
not care where the data came from.  `columns` limits reading to the mapped
columns.  meta: format, encoding, part, data_rows, headers, duplicate_headers.

Files are read in place (DuckDB, calamine, pyarrow, sqlite3); nothing is
copied.  The one exception is compressed input: a gzip file or a zip member is
decompressed to a temp file (removed when the process exits), since none of the
readers can stream it.

Formats: delimited text with any extension (delimiter , tab | ; sniffed;
encoding sniffed), gzip and zip (read through), Excel .xlsx .xlsm .xls .xlsb
(python-calamine, falling back to openpyxl / xlrd / pyxlsb; title rows above
the header skipped), JSON and JSON Lines, Parquet, Feather/Arrow, ORC, SQLite
and DuckDB database files (parts = tables).
Not handled: Access, live database connections, SQL dumps, fixed-width reports.
"""
from __future__ import annotations

import atexit
import codecs
import os
import csv
import datetime as dt
import gzip
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

SUPPORTED = [
    ("Delimited text", "any extension (.csv .txt .tsv .tab .dat .psv ...); comma, tab, pipe or semicolon; "
                       "UTF-8, UTF-16, Windows-1252 and similar encodings"),
    ("Compressed", ".gz, .zip (members are read as above)"),
    ("Excel", ".xlsx .xlsm .xls .xlsb, any sheet; title rows above the header are skipped"),
    ("JSON", ".json (array of records) and JSON Lines .jsonl/.ndjson"),
    ("Parquet", ".parquet"),
    ("Feather / Arrow", ".feather .arrow .ipc"),
    ("ORC", ".orc"),
    ("SQLite", ".sqlite .sqlite3 .db (choose a table)"),
    ("DuckDB", ".duckdb .ddb (choose a table)"),
]
# extensions that mark a zip member (or an inconclusive file) as data
DATA_EXT = {".csv", ".txt", ".tsv", ".tab", ".dat", ".psv", ".gz", ".zip", ".xlsx", ".xlsm", ".xls", ".xlsb",
            ".json", ".jsonl", ".ndjson", ".parquet", ".feather", ".arrow", ".ipc", ".orc", ".sqlite", ".sqlite3",
            ".db", ".duckdb", ".ddb"}
LEGACY_ENCODING = "cp1252"  # first guess for non-UTF-8 text (Windows Western); others via charset-normalizer
HEADER_SCAN_ROWS = 30   # Excel: look this far down for the real header row
HEADER_FILL = 0.6       # a header row has text in >= 60% of the widest row's columns


class UnsupportedFileError(ValueError):
    def __init__(self, path, looked_like: str):
        kinds = "\n".join(f"  - {k}: {v}" for k, v in SUPPORTED)
        super().__init__(f"{Path(path).name}: cannot read this file ({looked_like}). Supported types:\n{kinds}")


@dataclass(frozen=True)
class Source:
    """What gets ingested: a file, or one sheet / table / zip member inside it."""
    path: Path
    part: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "path", Path(self.path))

    def __str__(self):
        return self.path.name + (f" > {self.part}" if self.part else "")


def as_source(x, part=None) -> Source:
    return x if isinstance(x, Source) else Source(Path(x), part)


# ---- detection -----------------------------------------------------------------------

def _detect_bytes(head: bytes, name: str, zip_names=None) -> str:
    if not head:
        raise UnsupportedFileError(name, "it is empty")
    if head.startswith(b"PAR1"):
        return "parquet"
    if head.startswith(b"PK\x03\x04"):
        names = set(zip_names or [])
        if "xl/workbook.xml" in names:
            return "excel"
        if "xl/workbook.bin" in names:
            return "excel"
        return "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "excel"  # OLE2: legacy .xls
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"SQLite format 3\x00"):
        return "sqlite"
    if head[8:12] == b"DUCK":
        return "duckdb"
    if head.startswith((b"ARROW1", b"FEA1")):
        return "arrow"
    if head.startswith(b"ORC"):
        return "orc"
    if head.startswith(b"%PDF"):
        raise UnsupportedFileError(name, "it is a PDF")
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "delimited"
    sample = head[:4096]
    if sample.count(b"\x00") > len(sample) // 100 or sum(b < 9 for b in sample) > len(sample) // 50:
        raise UnsupportedFileError(name, "it is binary, not a known data format")
    text = sample.lstrip(codecs.BOM_UTF8).lstrip()
    if text[:1] in (b"[", b"{"):
        return "json"
    return "delimited"


def detect_format(path) -> str:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    with open(path, "rb") as f:
        head = f.read(8192)
    names = None
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
        except zipfile.BadZipFile:
            raise UnsupportedFileError(path, "it is a damaged zip file")
    return _detect_bytes(head, path.name, names)


# ---- temp files for gzip / zip members (kept for the session, removed at exit) --------

_TMP = Path(tempfile.mkdtemp(prefix="gtpfit_ingest_"))
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
_EXTRACTED: dict = {}


def _extracted(path: Path, member: str | None) -> Path:
    """gzip -> its content; zip + member -> that member, as a temp file (cached by path, mtime, member)."""
    key = (str(path), path.stat().st_mtime_ns, member)
    if key not in _EXTRACTED:
        out = _TMP / f"{len(_EXTRACTED)}_{Path(member or path.stem).name}"
        if member is None:
            with gzip.open(path, "rb") as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
        else:
            with zipfile.ZipFile(path) as z, z.open(member) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
        _EXTRACTED[key] = out
    return _EXTRACTED[key]


def _zip_members(path: Path) -> list:
    out = []
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            n = info.filename
            if info.is_dir() or n.startswith("__MACOSX/") or Path(n).name.startswith("."):
                continue
            with z.open(n) as f:
                head = f.read(8192)
            try:
                fmt = _detect_bytes(head, n)
            except UnsupportedFileError:
                continue
            if fmt not in ("delimited", "json") or Path(n).suffix.lower() in DATA_EXT:
                out.append(n)
    return out


def _resolve(src: Source) -> Source:
    """Follow gzip / zip wrappers down to a directly readable file."""
    fmt = detect_format(src.path)
    if fmt == "gzip":
        return _resolve(Source(_extracted(src.path, None), src.part))
    if fmt == "zip":
        members = _zip_members(src.path)
        if not members:
            raise UnsupportedFileError(src.path, "a zip with no data files inside")
        member, rest = (src.part, None) if src.part else (members[0], None)
        if src.part is None and len(members) > 1:
            raise ValueError(f"{src.path.name} holds several data files {members}; choose one.")
        if src.part and "|" in src.part:  # zip member that itself has parts: "member|sheet"
            member, rest = src.part.split("|", 1)
        return _resolve(Source(_extracted(src.path, member), rest))
    return src


# ---- delimited text --------------------------------------------------------------------

def _encoding(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(4)
        if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            return "utf-16"
        f.seek(0)
        dec = codecs.getincrementaldecoder("utf-8")()
        try:
            while chunk := f.read(1 << 20):
                dec.decode(chunk)
            dec.decode(b"", final=True)
            return "utf-8"
        except UnicodeDecodeError:
            pass
        # Western WMS exports that are not UTF-8 are almost always Windows-1252; take it when it decodes
        # cleanly (charset-normalizer alone mistakes short accented text for Central European cp1250)
        f.seek(0)
        dec = codecs.getincrementaldecoder(LEGACY_ENCODING)()
        try:
            while chunk := f.read(1 << 20):
                dec.decode(chunk)
            return LEGACY_ENCODING
        except UnicodeDecodeError:
            pass
    from charset_normalizer import from_path
    best = from_path(path).best()
    enc = best.encoding if best else "cp1252"
    return {"ascii": "utf-8"}.get(enc, enc)


def _text_head(path: Path, enc: str) -> tuple[list, str]:
    with open(path, encoding="utf-8-sig" if enc == "utf-8" else enc, newline="") as f:
        sample = f.read(64_000)
    lines = sample.splitlines()
    first = lines[0] if lines else ""
    try:
        delim = csv.Sniffer().sniff("\n".join(lines[:20]), delimiters=",\t|;").delimiter
    except csv.Error:
        delim = max(",\t|;", key=first.count) if any(c in first for c in ",\t|;") else ","
    hdr = next(csv.reader([first], delimiter=delim), [])
    return [h.strip() for h in hdr], delim


class _Delimited:
    name = "delimited"

    def parts(self, path):
        return None

    def headers(self, path, part):
        return _text_head(path, _encoding(path))[0]

    def read(self, path, part, keep, limit=None):
        enc = _encoding(path)
        _, delim = _text_head(path, enc)
        if enc == "utf-8":
            import duckdb
            rel = duckdb.read_csv(str(path), header=True, all_varchar=True, sep=delim)
            rel = rel.select(*[_q(rel.columns[i]) for i in keep])
            return (rel.limit(limit) if limit else rel).df(), enc
        return pd.read_csv(path, dtype=str, encoding=enc, sep=delim, keep_default_na=False, na_values=[""],
                           usecols=keep, nrows=limit), enc


# ---- Excel -------------------------------------------------------------------------------

def _excel_engines(path: Path) -> list:
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return ["calamine", "xlrd"]
    with zipfile.ZipFile(path) as z:
        binary = "xl/workbook.bin" in z.namelist()
    return ["calamine", "pyxlsb"] if binary else ["calamine", "openpyxl"]


def _excel(fn, path):
    err = None
    for eng in _excel_engines(path):
        try:
            return fn(eng)
        except Exception as e:  # try the next engine; report the first failure if none works
            err = err or e
    raise UnsupportedFileError(path, f"Excel could not be opened: {err}")


def _header_row(rows: pd.DataFrame) -> int:
    """First row (of the top HEADER_SCAN_ROWS) that looks like a header: mostly text and as wide as the data."""
    filled = rows.notna() & rows.astype(str).apply(lambda s: s.str.strip() != "")
    width = filled.sum(axis=1)
    if width.max() == 0:
        return 0
    text = filled & rows.map(lambda v: isinstance(v, str))
    for i in range(len(rows)):
        nxt = width.iloc[i + 1] if i + 1 < len(rows) else width.iloc[i]
        if text.iloc[i].sum() >= HEADER_FILL * width.max() and nxt >= HEADER_FILL * width.max():
            return i
    return int(width.idxmax())


_SHEETS: dict = {}  # (path, mtime, sheet) -> parsed calamine sheet; parsing is the slow part, so header + read share it


def _calamine_sheet(path: Path, part):
    from python_calamine import CalamineWorkbook
    key = (str(path), path.stat().st_mtime_ns, part)
    if key not in _SHEETS:
        wb = CalamineWorkbook.from_path(str(path))
        sheet = wb.get_sheet_by_name(part if part is not None else wb.sheet_names[0])
        if len(_SHEETS) >= 2:
            _SHEETS.pop(next(iter(_SHEETS)))
        _SHEETS[key] = sheet
    return _SHEETS[key]


class _Excel:
    """python-calamine first (fast on big workbooks); pandas with openpyxl / xlrd / pyxlsb if it cannot open one."""
    name = "excel"

    def parts(self, path):
        try:
            from python_calamine import CalamineWorkbook
            return list(CalamineWorkbook.from_path(str(path)).sheet_names)
        except Exception:
            return _excel(lambda eng: list(pd.ExcelFile(path, engine=eng).sheet_names), path)

    def _top(self, path, part):
        try:
            top = pd.DataFrame(_calamine_sheet(path, part).to_python(nrows=HEADER_SCAN_ROWS), dtype=object)
        except Exception:
            sheet = part if part is not None else 0
            top = _excel(lambda eng: pd.read_excel(path, sheet_name=sheet, header=None, nrows=HEADER_SCAN_ROWS,
                                                   dtype=object, engine=eng), path)
        if not len(top):
            return 0, []
        h = _header_row(top.replace("", None))
        return h, ["" if pd.isna(v) else str(v).strip() for v in top.iloc[h]]

    def headers(self, path, part):
        return self._top(path, part)[1]

    def read(self, path, part, keep, limit=None):
        h, hdr = self._top(path, part)
        try:
            data = []
            for j, row in enumerate(_calamine_sheet(path, part).iter_rows()):
                if j > h:
                    data.append([row[i] if i < len(row) else None for i in keep])
                    if limit and len(data) >= limit:
                        break
            df = pd.DataFrame(data, columns=keep, dtype=object)
        except Exception:
            sheet = part if part is not None else 0
            df = _excel(lambda eng: pd.read_excel(path, sheet_name=sheet, header=None, skiprows=h + 1, usecols=keep,
                                                  dtype=object, engine=eng, nrows=limit), path)
        return df.apply(lambda s: s.map(_cell_text)), None


# ---- JSON, columnar, databases ---------------------------------------------------------------

def _q(name) -> str:
    return '"' + str(name).replace('"', '""') + '"'


class _Json:
    name = "json"

    def _rel(self, path):
        import duckdb
        src = f"read_json('{Path(path).as_posix()}')"
        rel = duckdb.sql(f"select * from {src}")
        sel = []
        for c, t in zip(rel.columns, rel.types):
            if str(t).upper().startswith("STRUCT"):  # flatten nested objects one level: a.b
                sel += [f'{_q(c)}.{_q(k)} as {_q(f"{c}.{k}")}' for k, _ in t.children]
            else:
                sel.append(_q(c))
        return duckdb.sql(f"select {', '.join(sel)} from {src}")

    def parts(self, path):
        return None

    def headers(self, path, part):
        return [str(c).strip() for c in self._rel(path).columns]

    def read(self, path, part, keep, limit=None):
        rel = self._rel(path)
        rel = rel.select(*[_q(rel.columns[i]) for i in keep])
        return (rel.limit(limit) if limit else rel).df(), None


class _Parquet:
    name = "parquet"

    def parts(self, path):
        return None

    def headers(self, path, part):
        import duckdb
        return [str(c).strip() for c in duckdb.read_parquet(str(path)).columns]

    def read(self, path, part, keep, limit=None):
        import duckdb
        rel = duckdb.read_parquet(str(path))
        rel = rel.select(*[_q(rel.columns[i]) for i in keep])
        return (rel.limit(limit) if limit else rel).df(), None


class _Arrow:
    name = "arrow"

    def _names(self, path):
        import pyarrow.ipc as ipc
        try:
            with ipc.open_file(path) as r:  # Feather v2 / Arrow IPC: schema without reading data
                return list(r.schema.names)
        except Exception:  # Feather v1 is not an IPC file
            import pyarrow.feather as feather
            return list(feather.read_table(path).schema.names)

    def parts(self, path):
        return None

    def headers(self, path, part):
        return [str(c).strip() for c in self._names(path)]

    def read(self, path, part, keep, limit=None):
        import pyarrow.feather as feather
        raw = self._names(path)
        t = feather.read_table(path, columns=[raw[i] for i in keep])
        return (t.slice(0, limit) if limit else t).to_pandas(), None


def _tz_database():
    """pyarrow's ORC reader needs an IANA time zone database; Windows has none, the tzdata package does."""
    if "TZDIR" not in os.environ:
        try:
            import tzdata
            os.environ["TZDIR"] = str(Path(tzdata.__file__).parent / "zoneinfo")
        except ImportError:
            pass


_tz_database()


class _Orc:
    name = "orc"

    def parts(self, path):
        return None

    def headers(self, path, part):
        import pyarrow.orc as orc
        return [str(c).strip() for c in orc.ORCFile(path).schema.names]

    def read(self, path, part, keep, limit=None):
        import pyarrow.orc as orc
        f = orc.ORCFile(path)
        raw = f.schema.names
        t = f.read(columns=[raw[i] for i in keep])
        return (t.slice(0, limit) if limit else t).to_pandas(), None


class _Sqlite:
    name = "sqlite"

    def _con(self, path):
        return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)

    def parts(self, path):
        with self._con(path) as con:
            return [r[0] for r in con.execute("select name from sqlite_master where type in ('table','view') "
                                              "and name not like 'sqlite_%' order by name")]

    def _table(self, path, part):
        if part is None:
            ts = self.parts(path)
            if len(ts) != 1:
                raise ValueError(f"{Path(path).name} holds tables {ts}; choose one.")
            part = ts[0]
        return part

    def headers(self, path, part):
        with self._con(path) as con:
            return [r[1].strip() for r in con.execute(f"pragma table_info({_q(self._table(path, part))})")]

    def read(self, path, part, keep, limit=None):
        t = self._table(path, part)
        with self._con(path) as con:
            raw = [r[1] for r in con.execute(f"pragma table_info({_q(t)})")]
            cols = ", ".join(_q(raw[i]) for i in keep)
            lim = f" limit {int(limit)}" if limit else ""
            return pd.read_sql_query(f"select {cols} from {_q(t)}{lim}", con), None


class _DuckDBFile:
    name = "duckdb"

    def _con(self, path):
        import duckdb
        return duckdb.connect(str(path), read_only=True)

    def parts(self, path):
        con = self._con(path)
        try:
            return [r[0] for r in con.execute("select table_name from information_schema.tables "
                                              "where table_schema = 'main' order by 1").fetchall()]
        finally:
            con.close()

    def _table(self, path, part):
        if part is None:
            ts = self.parts(path)
            if len(ts) != 1:
                raise ValueError(f"{Path(path).name} holds tables {ts}; choose one.")
            part = ts[0]
        return part

    def headers(self, path, part):
        con = self._con(path)
        try:
            return [str(c).strip() for c in con.table(self._table(path, part)).columns]
        finally:
            con.close()

    def read(self, path, part, keep, limit=None):
        con = self._con(path)
        try:
            rel = con.table(self._table(path, part))
            rel = rel.select(*[_q(rel.columns[i]) for i in keep])
            return (rel.limit(limit) if limit else rel).df(), None
        finally:
            con.close()


REGISTRY = {r.name: r for r in (_Delimited(), _Excel(), _Json(), _Parquet(), _Arrow(), _Orc(), _Sqlite(),
                                _DuckDBFile())}


# ---- public API -----------------------------------------------------------------------------

def parts(path) -> list | None:
    """Sheets / tables / zip members a file holds (None = a single table)."""
    path = Path(path)
    fmt = detect_format(path)
    if fmt == "gzip":
        return parts(_extracted(path, None))
    if fmt == "zip":
        out = []
        for m in _zip_members(path):
            inner = parts(_extracted(path, m))
            out += [m] if inner is None else [f"{m}|{p}" for p in inner]
        return out
    return REGISTRY[fmt].parts(path)


def headers(source) -> list:
    s = _resolve(as_source(source))
    return REGISTRY[detect_format(s.path)].headers(s.path, s.part)


def read(source, columns=None, limit: int | None = None) -> tuple[pd.DataFrame, dict]:
    """limit: read only the first rows (previews); data_rows then counts those rows only."""
    src = as_source(source)
    s = _resolve(src)
    fmt = detect_format(s.path)
    reader = REGISTRY[fmt]
    hdr = reader.headers(s.path, s.part)
    keep = [i for i, h in enumerate(hdr) if columns is None or h in set(columns)]
    df, enc = reader.read(s.path, s.part, keep, limit)
    df.columns = [hdr[i] for i in keep]
    df = df.apply(_clean_text).replace("", pd.NA)
    meta = dict(path=str(src.path), source=str(src), format=fmt, encoding=enc, part=src.part, sheet=src.part,
                data_rows=len(df), headers=hdr, duplicate_headers=_dupes(hdr))
    return df, meta


_DELIMS = {",": "comma", "\t": "tab", "|": "pipe", ";": "semicolon"}


def describe(source) -> str:
    """How a source is read, in plain words: "pipe-delimited, UTF-8 (gzip)", "Excel sheet 'Data'"."""
    src = as_source(source)
    outer = detect_format(src.path)
    s = _resolve(src)
    fmt = detect_format(s.path)
    if fmt == "delimited":
        enc = _encoding(s.path)
        text = f"{_DELIMS.get(_text_head(s.path, enc)[1], 'delimited')}-delimited, {enc.upper()}"
    elif fmt == "excel":
        h = REGISTRY["excel"]._top(s.path, s.part)[0]
        sheet = s.part if s.part is not None else REGISTRY["excel"].parts(s.path)[0]
        text = f"Excel sheet '{sheet}'" + (f", header on row {h + 1}" if h else "")
    elif fmt in ("sqlite", "duckdb"):
        text = f"{'SQLite' if fmt == 'sqlite' else 'DuckDB'} table '{REGISTRY[fmt]._table(s.path, s.part)}'"
    else:
        text = {"json": "JSON", "parquet": "Parquet", "arrow": "Arrow / Feather", "orc": "ORC"}[fmt]
    return text + {"gzip": " (gzip)", "zip": " (in zip)"}.get(outer, "")


def count_rows(source) -> int | None:
    """Data rows in a source (header excluded), as cheaply as the format allows; None if it cannot tell."""
    s = _resolve(as_source(source))
    fmt = detect_format(s.path)
    p = s.path.as_posix().replace("'", "''")
    try:
        import duckdb
        if fmt == "delimited":
            enc = _encoding(s.path)
            if enc == "utf-8":
                sep = _text_head(s.path, enc)[1].replace("'", "''")
                return int(duckdb.sql(f"select count(*) from read_csv('{p}', header=true, all_varchar=true, "
                                      f"sep='{sep}')").fetchone()[0])
            with open(s.path, encoding=enc, newline="") as f:
                return max(sum(1 for line in f if line.strip()) - 1, 0)
        if fmt == "excel":
            h = REGISTRY["excel"]._top(s.path, s.part)[0]
            return max(int(_calamine_sheet(s.path, s.part).height) - h - 1, 0)
        if fmt in ("json", "parquet"):
            fn = "read_json" if fmt == "json" else "read_parquet"
            return int(duckdb.sql(f"select count(*) from {fn}('{p}')").fetchone()[0])
        if fmt == "arrow":
            import pyarrow.ipc as ipc
            try:
                with ipc.open_file(s.path) as r:
                    return int(sum(r.get_batch(i).num_rows for i in range(r.num_record_batches)))
            except Exception:  # Feather v1
                import pyarrow.feather as feather
                return int(feather.read_table(s.path).num_rows)
        if fmt == "orc":
            import pyarrow.orc as orc
            return int(orc.ORCFile(s.path).nrows)
        if fmt == "sqlite":
            reader = REGISTRY["sqlite"]
            with reader._con(s.path) as con:
                return int(con.execute(f"select count(*) from {_q(reader._table(s.path, s.part))}").fetchone()[0])
        if fmt == "duckdb":
            reader = REGISTRY["duckdb"]
            con = reader._con(s.path)
            try:
                return int(con.execute(f"select count(*) from {_q(reader._table(s.path, s.part))}").fetchone()[0])
            finally:
                con.close()
    except Exception:  # a count is a convenience; reading still reports any real problem
        return None
    return None


def _dupes(hdr) -> list:
    seen, d = set(), []
    for h in hdr:
        if h in seen and h not in d:
            d.append(h)
        seen.add(h)
    return d


def _cell_text(v):
    """A typed cell as the text a client would see (dates ISO, 12.0 as 12); NA stays NA."""
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
        return pd.NA
    if isinstance(v, dt.time):
        return v.strftime("%H:%M:%S")
    if isinstance(v, (dt.datetime, dt.date, pd.Timestamp)):
        ts = pd.Timestamp(v)
        return ts.strftime("%Y-%m-%d") if ts == ts.normalize() else ts.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, bool):
        return str(v)
    return str(v)


def _clean_text(s: pd.Series) -> pd.Series:
    """Trim text columns; object columns holding numbers/times/dates (mixed cells) become text like Excel shows."""
    if s.dtype == object:
        if not s.dropna().map(lambda v: isinstance(v, str)).all():
            s = s.map(_cell_text)
        return s.astype("string").str.strip()
    if pd.api.types.is_string_dtype(s):
        return s.str.strip()
    return s
