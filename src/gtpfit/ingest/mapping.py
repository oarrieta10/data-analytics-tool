"""mapping.py — which client column is which canonical field; saved WMS profiles.

    suggest_mapping(headers, table) -> Mapping   automatic, from schema synonyms
    load_wms_profile(path) / save_wms_profile(profile, path)

A WMS profile records, for one client system's exports: the column for each
canonical field, file name patterns, the declared length/weight units and the
date format.  Once confirmed for a WMS it is reused for every client on it.

Does not read data values; unit and date detection live in units.py / coerce.py.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from .params import IngestParams
from .schema import TABLES

_UNIT_TAIL = r"(in|inch|inches|cm|mm|m|ft|lb|lbs|pound|pounds|kg|kgs|g|gr|grams|oz|ounces|ea|each)"


def normalise_header(h: str) -> str:
    s = str(h).lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)           # "(cm)", "[lbs]"
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(rf"\s+{_UNIT_TAIL}$", "", s)            # trailing "in", "kg", ...
    return s


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
    # candidate scores: exact match = 2 - rank/100, fuzzy = ratio (< 1)
    cand = []
    for f in flds:
        syn = [normalise_header(s) for s in f.synonyms]
        for h, n in norm.items():
            if n in syn:
                cand.append((2 - syn.index(n) / 100, f.name, h, "exact"))
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
        raise ValueError(f"WMS profile maps unknown {table} fields: {sorted(unknown)}")
    m.unused_headers = [h for h in headers if h not in set(m.mapping.values())]
    return m


# ---- WMS profiles -------------------------------------------------------------

@dataclass
class WmsProfile:
    name: str
    description: str = ""
    units: dict = field(default_factory=dict)        # {"length": "in", "weight": "lb"}
    date_format: str | None = None                   # strftime, e.g. "%m/%d/%Y"
    files: dict = field(default_factory=dict)        # table -> glob pattern
    sheets: dict = field(default_factory=dict)       # table -> sheet name (Excel)
    columns: dict = field(default_factory=dict)      # table -> {field: header}


def load_wms_profile(path) -> WmsProfile:
    return parse_wms_profile(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def parse_wms_profile(data: dict) -> WmsProfile:
    known = set(WmsProfile.__dataclass_fields__)
    bad = set(data) - known
    if bad:
        raise ValueError(f"Unknown WMS profile keys: {sorted(bad)}")
    unknown_tables = set(data.get("columns", {})) - set(TABLES)
    if unknown_tables:
        raise ValueError(f"Unknown tables in WMS profile: {sorted(unknown_tables)}")
    return WmsProfile(**data)


def save_wms_profile(profile: WmsProfile, path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = {k: v for k, v in asdict(profile).items() if v not in ({}, None, "")}
    p.write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p
