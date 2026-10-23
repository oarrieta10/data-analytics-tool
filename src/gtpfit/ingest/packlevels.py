"""packlevels.py — item masters with one row per packaging level, turned into one row per SKU.

Some systems list each SKU once per package: the item itself, a case (or inner pack), a pallet, each row
with "units in this package" and that package's own size and weight.  When the item master's pack_level
column is mapped, ingest combines them:

    unit size   <- the unit-level row (a level word in pack_unit_levels, holding 1 unit), in the order of
                   pack_unit_levels when there are several (e.g. the bare part before its shipping box)
    case pack   <- the case-level row with the fewest units above 1 (any level holding more than 1 unit that
                   is not a pallet); its quantity is the case pack, its size and weight the case's
    pallet      <- the pallet-level row (pack_pallet_levels) with the fewest units

A SKU with no unit-level row keeps a blank unit size: nobody measured the item itself, and quality / backfill
say so.  Nothing is dropped: every input row is kept, with its role and whether it was used, in the
item_master_levels table.  Does not judge whether the sizes are right (quality/ does).
"""
from __future__ import annotations

import re

import pandas as pd

from .params import IngestParams

UNIT_FIELDS = ("unit_l", "unit_w", "unit_h", "unit_weight")
CASE_FROM = {"unit_l": "case_l", "unit_w": "case_w", "unit_h": "case_h", "unit_weight": "case_weight"}
PALLET_FROM = {"unit_l": "pallet_l", "unit_w": "pallet_w", "unit_h": "pallet_h", "unit_weight": "pallet_weight"}


def _words(level) -> set:
    return set(re.findall(r"[a-z]+", str(level).lower())) if level is not None and level == level else set()


def level_role(level, qty, params: IngestParams | None = None) -> str:
    """unit / case / pallet / other for one package row (level name, units in the package)."""
    params = params or IngestParams()
    w = _words(level)
    one = qty is None or qty != qty or qty <= 1
    if w & set(params.pack_pallet_levels):
        return "pallet"
    if one:
        return "unit" if w & set(params.pack_unit_levels) else "other"
    return "case"


def _unit_rank(level, params) -> int:
    w = _words(level)
    hits = [i for i, word in enumerate(params.pack_unit_levels) if word in w]
    return min(hits) if hits else len(params.pack_unit_levels)


def combine_pack_levels(df: pd.DataFrame, params: IngestParams | None = None) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """(one row per SKU, every input row with role and used, a plain note on what was combined).

    df: canonical item-master rows with pack_level, case_qty (units in this package) and the size / weight
    columns of that package in unit_l / unit_w / unit_h / unit_weight.
    """
    params = params or IngestParams()
    d = df.reset_index(drop=True).copy()
    qty = pd.to_numeric(d["case_qty"], errors="coerce")
    d["role"] = [level_role(lv, q, params) for lv, q in zip(d["pack_level"], qty)]
    d["used"] = False
    d["_q"], d["_rank"] = qty, [_unit_rank(lv, params) for lv in d["pack_level"]]
    skus = list(dict.fromkeys(d["sku"]))
    out = pd.DataFrame({"sku": skus})
    keep_cols = [c for c in d.columns if c not in UNIT_FIELDS + ("case_qty", "pack_level", "role", "used", "_q",
                                                                   "_rank", "sku")
                 and not c.startswith("_")]
    if keep_cols:  # descriptive columns: the first non-blank per SKU
        first = d.groupby("sku", sort=False)[keep_cols].first()
        out = out.join(first, on="sku")

    def pick(role, order):
        sub = d[d["role"] == role].sort_values(order, kind="stable")
        chosen = sub.drop_duplicates("sku")
        d.loc[chosen.index, "used"] = True
        return chosen.set_index("sku")

    unit = pick("unit", ["_rank"])
    case = pick("case", ["_q"])
    pallet = pick("pallet", ["_q"])
    for f in UNIT_FIELDS:
        if f in d:
            out[f] = out["sku"].map(unit[f])
            out[CASE_FROM[f]] = out["sku"].map(case[f])
            out[PALLET_FROM[f]] = out["sku"].map(pallet[f])
    out["case_qty"] = out["sku"].map(case["_q"])
    out["pallet_qty"] = out["sku"].map(pallet["_q"])
    levels = d.drop(columns=["_q", "_rank"])

    by_level = levels.groupby(levels["pack_level"].fillna("(blank)").astype(str))
    level_txt = ", ".join(f"{k} {len(g):,}" for k, g in sorted(by_level, key=lambda kv: -len(kv[1])))
    unused = levels[~levels["used"]]
    unused_txt = ", ".join(f"{k} {n:,}" for k, n in unused["pack_level"].fillna("(blank)").astype(str)
                           .value_counts().items())
    no_unit = int(out["unit_l"].isna().sum()) if "unit_l" in out else len(out)
    note = (f"{len(levels):,} rows at {by_level.ngroups} package levels ({level_txt}) combined into "
            f"{len(out):,} SKUs: item size from the unit level, case pack from the smallest case-level row, pallet "
            f"quantity from the smallest pallet row. {no_unit:,} SKUs have no unit-level row, so their item size "
            f"is left blank." + (f" Rows not used (kept in item_master_levels): {unused_txt}." if len(unused) else ""))
    return out, levels, note
