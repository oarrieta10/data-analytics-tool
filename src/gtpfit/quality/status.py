"""status.py — from flags to a per-SKU status, readiness for fit, the weighted score and the items to measure.

    activity(tables, line_definition) -> per-SKU lines, units, on_hand from the in-scope tables
    sku_status(item_master, flags, weights, peers) -> one row per SKU (and per orphan)
    score(status, params, has_inventory) -> shares of activity carried by SKUs ready for unit / case fit
    items_to_measure(status, flags) -> problem SKUs ranked by share of lines, with the cumulative gap closed

Status: unusable (a flag that makes the item impossible to fit as recorded) > suspect (probably wrong) > ok;
orphans (ordered, not in the item master) are their own status.  Readiness:
    ready_unit_fit  item size and weight present and not unusable
    ready_case_fit  case pack usable, and case size usable or derivable (case size blank, item size usable)
The score is a share of in-scope activity (lines, units, on-hand) and of SKUs; SKUs with no in-scope activity
are left out.  It is never weighted by cube: an item with bad sizes has no trustworthy cube.
"""
from __future__ import annotations

import pandas as pd

from gtpfit.ingest.schema import FIELD_LABEL
from gtpfit.lines import define_lines

from .checks import CODE_SEVERITY, member
from .params import QualityParams
from .text import QUALITY_TEXT

UNIT_FIELDS = {"unit_l", "unit_w", "unit_h", "unit_weight", "unit"}
CASE_FIELDS = {"case_qty", "case_l", "case_w", "case_h", "case"}
SEVERITY_RANK = {"unusable": 0, "orphan": 0, "suspect": 1, "info": 2}
BASES = ("lines", "units", "on_hand", "skus")
INACTIVE = "inactive in scope"

_DIMS = "unit length, width, height"
_FIELDS_TO_MEASURE = {
    "placeholder_dims": _DIMS, "repeated_dims": _DIMS, "longest_side_out_of_band": _DIMS, "outlier_cube": _DIMS,
    "density_out_of_band": _DIMS + ", unit weight", "outlier_density": _DIMS + ", unit weight",
    "outlier_weight": "unit weight", "case_smaller_than_unit": "case length, width, height, case pack",
    "case_cannot_hold_units": "case length, width, height, case pack",
    "case_mostly_air": "case length, width, height, case pack", "case_lighter_than_units": "case weight, case pack",
    "case_qty_not_whole": "case pack", "duplicate_conflict": "all sizes (its rows disagree)",
    "orphan_sku": "add to the item master with all sizes",
}


def activity(tables: dict, line_definition: str = "row") -> pd.DataFrame:
    """sku, lines (under the client's line definition), units, on_hand (average across snapshots)."""
    o = tables.get("orders")
    out = pd.DataFrame(columns=["lines", "units"], dtype=float)
    if o is not None and len(o):
        lines = define_lines(o, line_definition)
        qty = pd.to_numeric(lines["qty"], errors="coerce").fillna(0) if "qty" in lines else 0
        out = pd.DataFrame({"lines": lines.groupby("sku").size(),
                            "units": lines.assign(_q=qty).groupby("sku")["_q"].sum()})
    inv = tables.get("inventory")
    if inv is not None and len(inv) and "on_hand" in inv:
        oh = pd.to_numeric(inv["on_hand"], errors="coerce").fillna(0)
        if "snapshot_date" in inv and inv["snapshot_date"].notna().any():
            n_dates = inv["snapshot_date"].nunique()
            avg = inv.assign(_q=oh).groupby("sku")["_q"].sum() / n_dates
        else:
            avg = inv.assign(_q=oh).groupby("sku")["_q"].sum()
        out = out.join(avg.rename("on_hand"), how="outer")
    out = out.fillna(0.0)
    out.index.name = "sku"
    return out.reset_index()


def sku_status(im: pd.DataFrame, flags: pd.DataFrame, weights: pd.DataFrame, peers: pd.DataFrame) -> pd.DataFrame:
    d = im.drop_duplicates("sku", keep="first")
    base = pd.DataFrame({"sku": d["sku"].to_numpy(),
                         "description": d["description"].to_numpy() if "description" in d else None})
    orphans = flags.loc[flags["code"] == "orphan_sku", "sku"]
    if len(orphans):
        base = pd.concat([base, pd.DataFrame({"sku": orphans.to_numpy(), "description": None})], ignore_index=True)
    base = base.merge(peers, on="sku", how="left")
    problems = flags[member(flags["severity"], ["unusable", "suspect", "orphan"])]
    worst = problems.assign(r=problems["severity"].map(SEVERITY_RANK)).sort_values("r").drop_duplicates("sku")
    base["status"] = base["sku"].map(worst.set_index("sku")["severity"]).fillna("ok")
    unus = flags[member(flags["severity"], ["unusable", "orphan"])]
    unit_bad = member(base["sku"], unus.loc[member(unus["field"], UNIT_FIELDS | {"*"}), "sku"])
    case_bad = member(base["sku"], unus.loc[member(unus["field"], CASE_FIELDS | {"*"}), "sku"])
    derivable = member(base["sku"], flags.loc[flags["code"] == "case_dims_derivable", "sku"])
    has_case = d.set_index("sku")[[c for c in ("case_l", "case_w", "case_h") if c in d]]
    has_case = has_case.apply(pd.to_numeric, errors="coerce").gt(0).all(axis=1) if has_case.shape[1] == 3 else \
        pd.Series(False, index=d["sku"])
    base["ready_unit_fit"] = ~unit_bad
    case_dims_ok = base["sku"].map(has_case).fillna(False).astype(bool) & ~case_bad
    base["ready_case_fit"] = ~case_bad & (case_dims_ok | (derivable & base["ready_unit_fit"]))
    base = base.merge(weights, on="sku", how="left")
    for c in ("lines", "units", "on_hand"):
        base[c] = base[c].fillna(0.0) if c in base else 0.0
    base["bucket"] = ((base["lines"] > 0) | (base["on_hand"] > 0)).map({True: "active", False: INACTIVE})
    # the problem codes, listed for active SKUs (the flags table has every SKU's)
    act = problems[member(problems["sku"], base.loc[base["bucket"] == "active", "sku"])]
    base["problems"] = base["sku"].map(act.drop_duplicates(["sku", "code"]).groupby("sku")["code"].agg(", ".join))
    return base


def _band(share: float, p: QualityParams) -> str:
    return "green" if share >= p.score_green else "amber" if share >= p.score_amber else "red"


def score(status: pd.DataFrame, p: QualityParams, has_inventory: bool) -> dict:
    act = status[status["bucket"] == "active"]
    out = {}
    for fit in ("unit_fit", "case_fit"):
        ready = act[f"ready_{fit}"]
        res = {}
        for basis in BASES:
            if basis == "on_hand" and not has_inventory:
                continue
            w = pd.Series(1.0, index=act.index) if basis == "skus" else act[basis]
            total = float(w.sum())
            share = float(w[ready].sum() / total) if total else 0.0
            res[basis] = {"share": share, "band": _band(share, p), "ready": float(w[ready].sum()), "total": total}
        out[fit] = res
    return out


def _plain(codes: list) -> tuple[str, str]:
    codes = sorted(dict.fromkeys(codes), key=lambda c: SEVERITY_RANK.get(CODE_SEVERITY.get(c, "info"), 3))
    wrong = "; ".join(QUALITY_TEXT[c].title for c in codes)
    fields = []
    for c in codes:
        fields += [x.strip() for x in _FIELDS_TO_MEASURE.get(c, "").split(",") if x.strip()]
    return wrong, ", ".join(dict.fromkeys(fields))


def items_to_measure(status: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    """Unusable, suspect and orphan SKUs with in-scope activity, ranked by lines, with the gap closed so far."""
    bad = status[status["status"].isin(["unusable", "suspect", "orphan"]) & (status["bucket"] == "active")].copy()
    total_lines = float(status.loc[status["bucket"] == "active", "lines"].sum()) or 1.0
    probs = flags[member(flags["severity"], ["unusable", "suspect", "orphan"]) & member(flags["sku"], bad["sku"])]
    by_sku = {}
    for s_, c, fld in zip(probs["sku"], probs["code"], probs["field"]):
        by_sku.setdefault(s_, []).append((c, fld))
    texts = {}
    for s, pairs in by_sku.items():
        codes = [c for c, _ in pairs]
        wrong, fields = _plain(codes)
        for c, fld in pairs:  # a missing / zero / slipped value names its own field
            if c in ("missing_value", "nonpositive_value", "unit_slip") and fld in FIELD_LABEL:
                fields = ", ".join(dict.fromkeys([FIELD_LABEL[fld].lower()] + [x for x in fields.split(", ") if x]))
        texts[s] = (wrong, fields)
    bad["what_is_wrong"] = bad["sku"].map(lambda s: texts.get(s, ("", ""))[0])
    bad["fields_to_measure"] = bad["sku"].map(lambda s: texts.get(s, ("", ""))[1])
    bad = bad.sort_values(["lines", "units", "sku"], ascending=[False, False, True]).reset_index(drop=True)
    bad["share_of_lines"] = bad["lines"] / total_lines
    gap = float(bad["lines"].sum()) or 1.0
    bad["closes_gap"] = bad["lines"].cumsum() / gap
    bad.insert(0, "rank", range(1, len(bad) + 1))
    return bad[["rank", "sku", "description", "status", "what_is_wrong", "fields_to_measure", "lines", "units",
                "share_of_lines", "closes_gap"]]
