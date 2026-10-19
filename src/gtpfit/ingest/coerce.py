"""coerce.py — text columns to typed columns, counting every value that does not parse.

Each function returns the converted Series plus a boolean mask of values that
were present but could not be converted, so the caller can report or reject
them.  Nothing is dropped here.  Work is done on unique values, which keeps
million-row date and SKU columns fast.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

# candidate date formats; a format "fits" when it parses nearly every value
DATE_FORMATS = ["%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%y", "%d/%m/%y", "%d.%m.%Y", "%Y/%m/%d",
                "%m-%d-%Y", "%d-%m-%Y", "%Y%m%d", "%d-%b-%Y", "%d-%b-%y", "%b %d %Y",
                "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"]
TIME_FORMATS = ["%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p", "%Y-%m-%d %H:%M:%S"]
_THOUSANDS = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")


def _no_fail(s):
    return pd.Series(False, index=s.index)


def to_number(s: pd.Series) -> tuple[pd.Series, pd.Series]:
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        return s.astype(float), _no_fail(s)
    txt = s.astype("string")
    clean = txt.where(~txt.str.match(_THOUSANDS, na=False), txt.str.replace(",", "", regex=False))
    num = pd.to_numeric(clean, errors="coerce").astype(float)
    failed = s.notna() & num.isna()
    return num, failed


def _parse_unique(s: pd.Series, fmt: str) -> tuple[pd.Series, pd.Series]:
    u = pd.Series(s.dropna().unique())
    parsed = pd.to_datetime(u, format=fmt, errors="coerce")
    lookup = dict(zip(u, parsed))
    out = pd.to_datetime(s.map(lookup))
    return out, s.notna() & out.isna()


def date_fits(s: pd.Series) -> dict:
    """Share of non-blank unique values each candidate format parses."""
    u = pd.Series(s.dropna().unique())
    if u.empty:
        return {}
    return {f: float(pd.to_datetime(u, format=f, errors="coerce").notna().mean()) for f in DATE_FORMATS}


def choose_date_format(s: pd.Series, declared: str | None, min_share: float):
    """-> (format or None, status, fits).  status: native | declared | detected | mismatch | ambiguous | none."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return "native", "native", {}
    fits = date_fits(s)
    good = [f for f, v in fits.items() if v >= min_share]
    if declared:
        if fits.get(declared, date_fits_one(s, declared)) >= min_share:
            return declared, "declared", fits
        return None, "mismatch", fits
    if len(good) == 1:
        return good[0], "detected", fits
    # day-first vs month-first both parse when every day is <= 12
    return None, ("ambiguous" if good else "none"), fits


def date_fits_one(s: pd.Series, fmt: str) -> float:
    u = pd.Series(s.dropna().unique())
    return float(pd.to_datetime(u, format=fmt, errors="coerce").notna().mean()) if len(u) else 0.0


def to_date(s: pd.Series, fmt: str) -> tuple[pd.Series, pd.Series]:
    if fmt == "native":
        return s.dt.normalize().astype("datetime64[ns]"), _no_fail(s)
    out, failed = _parse_unique(s, fmt)
    return out.dt.normalize().astype("datetime64[ns]"), failed


def to_seconds(s: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Time of day -> seconds after midnight (Int64), trying each TIME_FORMAT in turn."""
    if pd.api.types.is_datetime64_any_dtype(s):  # Excel-style times arrive as 1899-12-30 HH:MM:SS
        secs = (s.dt.hour * 3600 + s.dt.minute * 60 + s.dt.second).astype("Int64")
        return secs, _no_fail(s)
    u = pd.Series(s.dropna().unique())
    best = pd.Series(pd.NaT, index=u.index, dtype="datetime64[ns]")
    for f in TIME_FORMATS:
        miss = best.isna()
        if not miss.any():
            break
        best[miss] = pd.to_datetime(u[miss], format=f, errors="coerce")
    secs = (best.dt.hour * 3600 + best.dt.minute * 60 + best.dt.second).astype("Int64")
    out = s.map(dict(zip(u, secs))).astype("Int64")
    return out, s.notna() & out.isna()


def normalise_ids(s: pd.Series, upper: bool) -> tuple[pd.Series, int]:
    """Trim (and upper-case) keys.  Returns the series and how many distinct raw values changed."""
    if not pd.api.types.is_string_dtype(s):
        s = s.astype("string")  # numeric order / receipt numbers
    u = pd.Series(s.dropna().unique())
    fixed = u.str.strip()
    if upper:
        fixed = fixed.str.upper()
    changed = int((fixed != u).sum())
    out = s.map(dict(zip(u, fixed))).astype("string")
    return out, changed
