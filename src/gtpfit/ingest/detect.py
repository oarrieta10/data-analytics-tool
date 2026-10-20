"""detect.py — what each received file is: table guess, how it is read, how many rows, which sheets.

    detect(paths, params=None) -> [Detection]   one per file / sheet / database table / zip member

The Confirm step of the workbench shows these and lets the consultant change
the guess.  Files that are not data (a Word document, notes, a sheet of
pivots) are listed with table None, never silently dropped.  Reading the full
data is ingest's job; this only reads headers, a row count and the first rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .classify import classify
from .params import IngestParams
from .readers import DATA_EXT, Source, UnsupportedFileError, count_rows, describe, detect_format, parts


@dataclass
class Detection:
    file: Path
    part: str | None                 # sheet / table / zip member, or None for a single-table file
    table: str | None                # guessed canonical table, None = not data
    confidence: str                  # high | medium | low | none
    reason: str
    read_as: str = ""                # "pipe-delimited, UTF-8", "Excel sheet 'Data'", ...
    rows: int | None = None
    at_excel_limit: bool = False
    ranking: list = field(default_factory=list)
    parts: list = field(default_factory=list)   # every sheet / table in the file

    @property
    def source(self) -> Source:
        return Source(self.file, self.part)

    @property
    def label(self) -> str:
        return str(self.source)


def _not_data(p: Path, why: str) -> Detection:
    return Detection(p, None, None, "none", why)


def detect(paths, params: IngestParams | None = None) -> list[Detection]:
    params = params or IngestParams()
    out = []
    for p in sorted(Path(x) for x in paths):
        if not p.is_file() or p.name.startswith(("~$", ".")):
            continue
        try:
            fmt = detect_format(p)
            if fmt in ("delimited", "json") and p.suffix.lower() not in DATA_EXT:
                out.append(_not_data(p, f"A {p.suffix or 'text'} file, not a data export."))
                continue
            ps = parts(p)
        except (UnsupportedFileError, ValueError) as e:
            out.append(_not_data(p, str(e).splitlines()[0]))
            continue
        if ps == []:
            out.append(_not_data(p, "No data inside."))
            continue
        for part in ps if ps is not None else [None]:
            src = Source(p, part)
            g = classify(src, params)
            try:
                read_as = describe(src)
            except Exception as e:  # describing is cosmetic; keep the row
                read_as = f"unreadable: {e}"
            n = count_rows(src)
            out.append(Detection(p, part, g.table, g.confidence, g.reason, read_as, n,
                                 n is not None and n in params.excel_row_limits, g.ranking, list(ps or [])))
    return out
