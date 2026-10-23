"""mapping.py — which client column is which canonical field; the client's saved mapping (profile).

    suggest_mapping(headers, table) -> Mapping   automatic, from schema synonyms
    load_wms_profile(path) / save_wms_profile(profile, path) / saved_mapping_for(profile, table, headers)

A profile records, for one client's exports: the column for each canonical
field (and every column name of the file it was confirmed on), file name
patterns, the length/weight units and the date format.  The workbench saves
one per client automatically and reuses it when a new file's columns overlap
(saved_mapping_for); extracts differ even on the same WMS, so profiles are
not shared across clients.

Does not read data values; unit and date detection live in units.py / coerce.py.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from .params import IngestParams
from .schema import TABLES, normalise_header, synonyms  # noqa: F401 (normalise_header re-exported)


@dataclass
class Mapping:
    table: str
    mapping: dict = field(default_factory=dict)        # field -> header
    method: dict = field(default_factory=dict)         # field -> exact | fuzzy | profile
    alternatives: dict = field(default_factory=dict)   # field -> other headers that also matched
    ambiguous: dict = field(default_factory=dict)      # field -> tied headers (not mapped)
    missing_required: list = field(default_factory=list)
    missing_optional: list = field(default_factory=list)
    unused_headers: list = field(default_factory=list)


def suggest_mapping(headers, table: str, params: IngestParams | None = None) -> Mapping:
    """Match headers to fields: exact synonym first (earlier synonyms preferred), then fuzzy."""
    params = params or IngestParams()
    norm = {h: normalise_header(h) for h in headers}
    flds = TABLES[table]
    # candidate scores: exact match = 2 - rank/100, fuzzy = ratio (< 1).  "Exact" ignores spaces, so
    # database-style names match too: partnumber = part number, customerorderno = customer order no.
    cand = []
    for f in flds:
        syn = list(synonyms(table, f.name))  # built-in + learned
        compact = [s.replace(" ", "") for s in syn]
        for h, n in norm.items():
            if n.replace(" ", "") in compact:
                cand.append((2 - compact.index(n.replace(" ", "")) / 100, f.name, h, "exact"))
            else:
                r = max(difflib.SequenceMatcher(None, n, s).ratio() for s in syn)
                if r >= params.fuzzy_header_cutoff:
                    cand.append((r, f.name, h, "fuzzy"))
    m = Mapping(table)
    by_field: dict[str, list] = {}
    for c in cand:
        by_field.setdefault(c[1], []).append(c)
    used: set = set()
    # most confident fields first, so a strong match is not stolen by a weak one
    for score, fname, h, how in sorted(cand, key=lambda c: (-c[0], c[1], str(c[2]))):
        if fname in m.mapping or fname in m.ambiguous or h in used:
            continue
        ties = [c[2] for c in by_field[fname] if c[0] == score and c[2] not in used]
        if len(ties) > 1:
            m.ambiguous[fname] = ties
            continue
        m.mapping[fname] = h
        m.method[fname] = how
        used.add(h)
    # a required field left without a column takes back a header an optional field took (e.g. a file whose
    # only date is "Ship Date": it is the order date, not the optional ship date)
    required = {f.name for f in flds if f.required}
    holder = {h: f for f, h in m.mapping.items()}
    for fname in [f.name for f in flds if f.required and f.name not in m.mapping and f.name not in m.ambiguous]:
        for score, _, h, how in sorted(by_field.get(fname, []), key=lambda c: -c[0]):
            if how != "exact":  # only an exact name is worth taking from another field
                continue
            other = holder.get(h)
            if other and other not in required:
                del m.mapping[other], m.method[other]
                m.mapping[fname], m.method[fname] = h, how
                holder[h] = fname
                break
    for fname, cs in by_field.items():
        if fname in m.mapping:
            others = [c[2] for c in cs if c[2] != m.mapping[fname] and c[2] not in used]
            if others:
                m.alternatives[fname] = others
    m.mapping = {f.name: m.mapping[f.name] for f in flds if f.name in m.mapping}  # schema order
    m.method = {k: m.method[k] for k in m.mapping}
    m.missing_required = [f.name for f in flds if f.required and f.name not in m.mapping]
    m.missing_optional = [f.name for f in flds if not f.required and f.name not in m.mapping]
    m.unused_headers = [h for h in headers if h not in used]
    return m


def mapping_from_profile(headers, table: str, columns: dict) -> Mapping:
    m = Mapping(table)
    present = set(headers)
    for f in TABLES[table]:
        h = columns.get(f.name)
        if h is not None and h in present:
            m.mapping[f.name] = h
            m.method[f.name] = "profile"
        elif f.required:
            m.missing_required.append(f.name)
        else:
            m.missing_optional.append(f.name)
    unknown = set(columns) - {f.name for f in TABLES[table]}
    if unknown:
        raise ValueError(f"Mapping file maps unknown {table} fields: {sorted(unknown)}")
    m.unused_headers = [h for h in headers if h not in set(m.mapping.values())]
    return m


# ---- saved mappings (profiles) -------------------------------------------------------------

@dataclass
class WmsProfile:
    name: str
    description: str = ""
    units: dict = field(default_factory=dict)        # {"length": "in", "weight": "lb"}
    date_format: str | None = None                   # strftime, e.g. "%m/%d/%Y"
    files: dict = field(default_factory=dict)        # table -> glob pattern
    sheets: dict = field(default_factory=dict)       # table -> sheet name (Excel)
    columns: dict = field(default_factory=dict)      # table -> {field: header}
    headers: dict = field(default_factory=dict)      # table -> every column name of the file it was confirmed on
    saved_at: str = ""                               # when the mapping was last confirmed


def saved_mapping_for(profile: WmsProfile | None, table: str, headers, min_overlap: float | None = None) -> dict | None:
    """The client's last confirmed mapping for this table, if the new file looks like the same extract.

    Same extract = every mapped column is still there, and the column names overlap the ones it was confirmed
    on by at least min_overlap (Jaccard; IngestParams.mapping_reuse_min_overlap).  Otherwise None: match afresh.
    """
    if profile is None or not profile.columns.get(table):
        return None
    min_overlap = IngestParams().mapping_reuse_min_overlap if min_overlap is None else min_overlap
    cols = profile.columns[table]
    now = {h for h in headers if h}
    if any(h not in now for h in cols.values()):
        return None
    seen = set(profile.headers.get(table) or cols.values())
    if len(now & seen) / len(now | seen) < min_overlap:
        return None
    return mapping_with_new_fields(cols, headers, table)


def mapping_with_new_fields(columns: dict, headers, table: str) -> dict:
    """A saved mapping plus fields it never mentions (added to the schema after it was saved, e.g. the package
    level) where a header the mapping does not use matches them exactly.  A field the saved mapping maps is
    never changed."""
    out = dict(columns)
    used = set(out.values())
    auto = suggest_mapping([h for h in headers if h and h not in used], table)
    for f, h in auto.mapping.items():
        if f not in out and auto.method.get(f) == "exact":
            out[f] = h
    return out


def load_wms_profile(path) -> WmsProfile:
    return parse_wms_profile(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def parse_wms_profile(data: dict) -> WmsProfile:
    known = set(WmsProfile.__dataclass_fields__)
    bad = set(data) - known
    if bad:
        raise ValueError(f"Unknown mapping file keys: {sorted(bad)}")
    unknown_tables = set(data.get("columns", {})) - set(TABLES)
    if unknown_tables:
        raise ValueError(f"Unknown tables in mapping file: {sorted(unknown_tables)}")
    return WmsProfile(**data)


def save_wms_profile(profile: WmsProfile, path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = {k: v for k, v in asdict(profile).items() if v not in ({}, None, "")}
    p.write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p
