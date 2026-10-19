"""classify.py — guess which canonical table a file, sheet or database table holds, from its column names.

    list_sources(paths) -> [Source]     every readable file, expanded into sheets / tables / zip members
    classify(source) -> Guess           best table (or None), confidence, ranking, matched fields

A table qualifies when all its required fields match a column and (for the
item master) at least one identifying field does too.  Among qualifying
tables a dated transaction log (orders, receipts) beats an item list, then
more matched fields wins.  The guess is only a starting
point: the consultant confirms or changes it before ingest runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .mapping import suggest_mapping
from .params import IngestParams
from .readers import DATA_EXT, Source, UnsupportedFileError, as_source, detect_format, headers, parts
from .schema import IDENTIFY_BY, TABLES

TRANSACTIONS = ("orders", "receipts")  # their required date makes them a stronger signal than column counts
HIGH_MIN_FIELDS = 3  # "high" needs at least this many matched fields (sku + a generic "quantity" is not enough)
CLEAR_LEAD = 2       # ...and this many more than the runner-up (or the runner-up does not qualify)


@dataclass
class Guess:
    source: Source
    table: str | None
    confidence: str                      # high | medium | low | none
    ranking: list = field(default_factory=list)   # [(table, score, matched fields)] best first
    reason: str = ""

    @property
    def matched(self) -> list:
        return self.ranking[0][2] if self.ranking and self.table else []


def classify(source, params: IngestParams | None = None) -> Guess:
    src = as_source(source)
    params = params or IngestParams()
    try:
        hdr = headers(src)
    except (UnsupportedFileError, ValueError) as e:
        return Guess(src, None, "none", reason=str(e).splitlines()[0])
    ranking, ok = [], {}
    for table, flds in TABLES.items():
        m = suggest_mapping(hdr, table, params)
        ident = IDENTIFY_BY.get(table)
        ok[table] = not m.missing_required and (not ident or any(f in m.mapping for f in ident))
        ranking.append((table, round(len(m.mapping) / len(flds), 3) if ok[table] else 0.0, list(m.mapping)))
    # qualifying first; a dated transaction log beats a list of items; then more matched fields
    ranking.sort(key=lambda r: (ok[r[0]], ok[r[0]] and r[0] in TRANSACTIONS, len(r[2]) if ok[r[0]] else 0, r[1]),
                 reverse=True)
    best, second = ranking[0], ranking[1]
    if not ok[best[0]]:
        return Guess(src, None, "none", ranking, "no table's required columns found")
    n_best, n_second = len(best[2]), (len(second[2]) if ok[second[0]] else 0)
    decisive = not ok[second[0]] or (best[0] in TRANSACTIONS and second[0] not in TRANSACTIONS) or \
        n_best - n_second >= CLEAR_LEAD
    if n_best >= HIGH_MIN_FIELDS and decisive:
        conf = "high"
    elif n_best > n_second or decisive:
        conf = "medium"
    else:
        conf = "low"
    ranking = [(t, sc if ok[t] else 0.0, mf) for t, sc, mf in ranking]
    why = f"matched {len(best[2])} of {len(TABLES[best[0]])} fields: {', '.join(best[2])}"
    if ok[second[0]]:
        why += f"; could also be {second[0]}"
    return Guess(src, best[0], conf, ranking, why)


def list_sources(paths) -> list[Source]:
    """Each path as Sources: one per sheet / database table / zip member; unreadable files are skipped."""
    out = []
    for p in paths:
        p = Path(p)
        if not p.is_file() or p.name.startswith((".", "~$")):
            continue
        try:
            if detect_format(p) in ("delimited", "json") and p.suffix.lower() not in DATA_EXT:
                continue  # .html, .md, .yaml, .py ...: text, but not a data export
            ps = parts(p)
        except (UnsupportedFileError, ValueError):
            continue
        out += [Source(p)] if ps is None else [Source(p, x) for x in ps]
    return out
