"""read.py — the two calls the pipeline makes, kept for compatibility; the work is in readers.py.

    read_headers(path_or_source, sheet=None) -> list of column names (cheap)
    read_raw(path_or_source, sheet=None, columns=None) -> (DataFrame, meta dict)

`sheet` names a sheet, database table or zip member when the first argument is
a plain path.  See readers.py for the formats and the DataFrame contract.
"""
from __future__ import annotations

from .readers import as_source, headers, read


def read_headers(path, sheet=None) -> list:
    return headers(as_source(path, sheet))


def read_raw(path, sheet=None, columns=None):
    return read(as_source(path, sheet), columns)
