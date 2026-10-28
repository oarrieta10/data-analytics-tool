"""detect.py — what each received file is: table guess, how it is read, how many rows, which sheets.

    detect(paths, params=None) -> [Detection]   one per file / sheet / database table / zip member

The Confirm step of the workbench shows these and lets the consultant change
the guess.  Files that are not data (a Word document, notes, a sheet of
pivots) are listed with table None, never silently dropped.  So are files and
zip members that cannot be opened (password protected, open in another
program, damaged): `unreadable` holds the plain reason, and the rest of the
folder is read as normal; nothing here raises on a bad file.  Reading the full
data is ingest's job; this only reads headers, a row count and the first rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .classify import classify
from .params import IngestParams
from .readers import (DATA_EXT, Source, UnreadableFileError, UnsupportedFileError, count_rows, describe,
                      detect_format, parts, skipped_parts, unreadable_reason)


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
    unreadable: str = ""             # why it could not be opened ("password protected: ..."), "" if it could

    @property
    def source(self) -> Source:
        return Source(self.file, self.part)

    @property
    def label(self) -> str:
        return str(self.source)


@dataclass
class FolderSummary:
    files: int
    data: int      # files with at least one sheet / table guessed as a canonical table
    ignored: int   # files that are not data (notes, reports, unrecognised tables)
    zips: int      # zip archives (their members are listed separately as sources)
    unreadable: int = 0   # files that could not be opened at all (password, lock, damage)

    @property
    def text(self) -> str:
        bad = f", {self.unreadable} can't be read" if self.unreadable else ""
        return (f"{self.files} files found: {self.data} data, {self.ignored} ignored{bad}, "
                f"{self.zips} {'zip' if self.zips == 1 else 'zips'}")


def summarize(files, detections) -> FolderSummary:
    """One line about a folder: how many files, how many hold data, how many were ignored or can't be read,
    how many zips."""
    files = [Path(f) for f in files if Path(f).is_file()]
    zips = [f for f in files if _is_zip(f)]
    with_data = {d.file for d in detections if d.table}
    bad = {d.file for d in detections if d.unreadable and d.part is None}
    data = sum(1 for f in files if f not in zips and f in with_data)
    unreadable = sum(1 for f in files if f not in zips and f not in with_data and f in bad)
    return FolderSummary(len(files), data, len(files) - len(zips) - data - unreadable, len(zips), unreadable)


def _is_zip(p: Path) -> bool:
    try:
        return detect_format(p) == "zip"
    except (UnsupportedFileError, ValueError, OSError):
        return False


def _not_data(p: Path, why: str) -> Detection:
    return Detection(p, None, None, "none", why)


def _cant_read(p: Path, part: str | None, why: str) -> Detection:
    return Detection(p, part, None, "none", f"Can't read: {why}", unreadable=why)


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
        except UnreadableFileError as e:
            out.append(_cant_read(p, None, e.reason))
            continue
        except (UnsupportedFileError, ValueError) as e:
            out.append(_not_data(p, str(e).splitlines()[0]))
            continue
        except Exception as e:  # anything else: listed as unreadable, never a crash
            out.append(_cant_read(p, None, unreadable_reason(e)))
            continue
        skipped = skipped_parts(p)
        if ps == [] and not skipped:
            out.append(_not_data(p, "No data inside."))
            continue
        for part in ps if ps is not None else [None]:
            src = Source(p, part)
            try:
                g = classify(src, params)
            except Exception as e:
                out.append(_cant_read(p, part, unreadable_reason(e)))
                continue
            if g.unreadable:
                out.append(_cant_read(p, part, g.unreadable))
                continue
            try:
                read_as = describe(src)
            except Exception as e:  # describing is cosmetic; keep the row
                read_as = f"unreadable: {e}"
            n = count_rows(src)
            out.append(Detection(p, part, g.table, g.confidence, g.reason, read_as, n,
                                 n is not None and n in params.excel_row_limits, g.ranking, list(ps or [])))
        out += [_cant_read(p, member, why) for member, why in skipped]
    return out
