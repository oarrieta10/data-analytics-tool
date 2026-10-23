"""checks.py — every item-data check, as flags: (sku, field, code, severity, observed, peer_ref, suggested).

Reads and classifies; never changes a value (a suggested fix is recorded, not applied; filling and correcting
is backfill/'s job).  Checks run on the first item-master row of each SKU; repeated SKUs are reported by the
duplicate checks.  Nothing is excluded by default: every rule reports.

    missing        missing_value, nonpositive_value              (unit L/W/H, unit weight, case pack)
    placeholder    placeholder_dims, repeated_dims               (fill-in values; one size copied across a group)
    physics        density_out_of_band, longest_side_out_of_band (the assortment's bands, ingest's params)
                   case_smaller_than_unit, case_cannot_hold_units, case_mostly_air,
                   case_lighter_than_units, case_qty_not_whole
    unit slip      unit_slip                                     (2.54 / 25.4 / 10 / 100 / 16 off its peers)
    outliers       outlier_cube, outlier_weight, outlier_density (robust z in log space within the peer group)
    duplicates     duplicate_sku, duplicate_conflict
    orphans        orphan_sku                                    (ordered in scope, not in the item master)
    information    case_dims_derivable                           (no case size, but case pack and item size known)

Peer statistics (medians, spreads) come only from SKUs whose item size and weight are present, positive and
not placeholders or copied sizes, so bad rows do not shape what "normal" looks like.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from gtpfit.ingest import IngestParams

from .params import QualityParams
from .peers import MAD_TO_SD, peer_groups

UNIT_DIMS = ["unit_l", "unit_w", "unit_h"]
CASE_DIMS = ["case_l", "case_w", "case_h"]
REQUIRED = UNIT_DIMS + ["unit_weight", "case_qty"]
IN3_PER_FT3 = 1728.0

# Severity per code: unusable (the item cannot be fitted as recorded), suspect (probably wrong; check), info.
CODE_SEVERITY = {
    "missing_value": "unusable", "nonpositive_value": "unusable",
    "placeholder_dims": "unusable", "repeated_dims": "unusable",
    "density_out_of_band": "unusable", "longest_side_out_of_band": "unusable",
    "case_smaller_than_unit": "unusable", "case_cannot_hold_units": "unusable",
    "case_lighter_than_units": "unusable", "case_qty_not_whole": "unusable",
    "case_mostly_air": "suspect", "unit_slip": "suspect",
    "outlier_cube": "suspect", "outlier_weight": "suspect", "outlier_density": "suspect",
    "duplicate_conflict": "suspect", "duplicate_sku": "info",
    "orphan_sku": "orphan", "case_dims_derivable": "info",
}
CODES = tuple(CODE_SEVERITY)
FLAG_COLUMNS = ["sku", "field", "code", "severity", "observed", "peer_ref", "suggested"]


def _flags(sku, field, code, observed=None, peer_ref=None, suggested=None) -> pd.DataFrame:
    sku = pd.Series(sku).reset_index(drop=True)
    n = len(sku)

    def col(v):
        return pd.Series(v).reset_index(drop=True).astype(float) if isinstance(v, (pd.Series, np.ndarray, list)) \
            else pd.Series([np.nan if v is None else v] * n, dtype=float)
    return pd.DataFrame({"sku": sku, "field": field, "code": code, "severity": CODE_SEVERITY[code],
                         "observed": col(observed), "peer_ref": col(peer_ref), "suggested": col(suggested)})


def _num(d: pd.DataFrame, c: str) -> pd.Series:
    return pd.to_numeric(d[c], errors="coerce") if c in d else pd.Series(np.nan, index=d.index)


def _group_stat(log_values: pd.Series, groups: pd.Series, clean: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Per-row median and scaled MAD of log values, computed from the clean rows of each group only."""
    lv, g = log_values[clean], groups[clean]
    med_g = lv.groupby(g).median()
    mad_g = (lv - g.map(med_g)).abs().groupby(g).median() * MAD_TO_SD
    return groups.map(med_g), groups.map(mad_g)


def run_checks(im: pd.DataFrame, orders: pd.DataFrame | None, qp: QualityParams | None = None,
               ip: IngestParams | None = None) -> pd.DataFrame:
    qp, ip = qp or QualityParams(), ip or IngestParams()
    out = [duplicates(im)]
    d = im.drop_duplicates("sku", keep="first").reset_index(drop=True)
    sku = d["sku"]
    v = {c: _num(d, c) for c in REQUIRED + CASE_DIMS + ["case_weight"]}
    good = {c: v[c].notna() & (v[c] > 0) for c in v}

    # -- missing
    for c in REQUIRED:
        out.append(_flags(sku[v[c].isna()], c, "missing_value"))
        bad = v[c].notna() & (v[c] <= 0)
        out.append(_flags(sku[bad], c, "nonpositive_value", v[c][bad]))
    dims_ok = good["unit_l"] & good["unit_w"] & good["unit_h"]

    # -- placeholders: all three sides equal to a fill-in value
    eq = dims_ok & np.isclose(v["unit_l"], v["unit_w"]) & np.isclose(v["unit_w"], v["unit_h"])
    fill_in = np.isclose(v["unit_l"].to_numpy()[:, None], np.asarray(qp.placeholder_values, float)[None, :]).any(axis=1)
    ph = eq & pd.Series(fill_in, index=d.index)
    out.append(_flags(sku[ph], "unit", "placeholder_dims", v["unit_l"][ph]))

    # -- copied default size: one L x W x H on a big share of a peer group
    peers = peer_groups(d, qp.peer_min_size)
    g = peers["peer_group"]
    triple = (v["unit_l"].round(2).astype(str) + "x" + v["unit_w"].round(2).astype(str) + "x" +
              v["unit_h"].round(2).astype(str)).where(dims_ok & ~ph)
    n_same = triple.groupby([g, triple]).transform("size") if triple.notna().any() else pd.Series(0, index=d.index)
    group_n = g.map(g.value_counts())
    rep = triple.notna() & (n_same >= qp.repeated_dims_min_count) & (n_same >= qp.repeated_dims_min_share * group_n)
    out.append(_flags(sku[rep], "unit", "repeated_dims", n_same[rep]))

    # -- physics: the assortment's bands
    vol = v["unit_l"] * v["unit_w"] * v["unit_h"]
    item_ok = dims_ok & good["unit_weight"] & ~ph
    dens = v["unit_weight"] / (vol / IN3_PER_FT3)
    lo, hi = ip.density_lb_ft3
    bad = item_ok & ((dens < lo) | (dens > hi))
    out.append(_flags(sku[bad], "unit", "density_out_of_band", dens[bad]))
    longest = pd.concat([v[c] for c in UNIT_DIMS], axis=1).max(axis=1)
    lo, hi = ip.unit_longest_side_in
    bad = dims_ok & ~ph & ((longest < lo) | (longest > hi))
    out.append(_flags(sku[bad], "unit", "longest_side_out_of_band", longest[bad]))

    # -- physics: the case against its units
    case_ok = good["case_l"] & good["case_w"] & good["case_h"]
    qty_ok = good["case_qty"]
    both = dims_ok & case_ok & qty_ok & ~ph
    us = np.sort(np.column_stack([v[c] for c in UNIT_DIMS]), axis=1)
    cs = np.sort(np.column_stack([v[c] for c in CASE_DIMS]), axis=1)
    smaller = both & pd.Series((cs < us).any(axis=1), index=d.index)
    out.append(_flags(sku[smaller], "case", "case_smaller_than_unit"))
    case_vol = v["case_l"] * v["case_w"] * v["case_h"]
    fill = case_vol / (v["case_qty"] * vol)
    bad = both & (fill < qp.case_fill_min)
    out.append(_flags(sku[bad], "case", "case_cannot_hold_units", fill[bad]))
    bad = both & (fill > qp.case_fill_max)
    out.append(_flags(sku[bad], "case", "case_mostly_air", fill[bad]))
    wfill = v["case_weight"] / (v["case_qty"] * v["unit_weight"])
    bad = good["case_weight"] & qty_ok & good["unit_weight"] & (wfill < qp.case_fill_min)
    out.append(_flags(sku[bad], "case_weight", "case_lighter_than_units", wfill[bad]))
    bad = qty_ok & (v["case_qty"] % 1 != 0)
    out.append(_flags(sku[bad], "case_qty", "case_qty_not_whole", v["case_qty"][bad]))

    # -- unit slip and outliers, against clean peers
    clean = item_ok & ~rep
    k = qp.outlier_mad_k
    direct = [_slips(sku, v[c], g, clean, good[c] & ~ph, c, qp) for c in UNIT_DIMS + ["unit_weight"]]
    out += direct
    caught = set(pd.concat(direct)["sku"]) if any(len(x) for x in direct) else set()
    out.append(_density_slips(sku, v, dens, g, clean, item_ok & ~sku.isin(caught), qp))
    for code, x in (("outlier_cube", vol), ("outlier_weight", v["unit_weight"]), ("outlier_density", dens)):
        lx = np.log(x.where(item_ok))
        med, mad = _group_stat(lx, g, clean)
        z = ((lx - med) / mad.where(mad > 0)).abs()
        bad = item_ok & (z > k)
        out.append(_flags(sku[bad], "unit", code, x[bad], np.exp(med[bad])))

    # -- information: case size missing but derivable
    derivable = ~case_ok & qty_ok & dims_ok & ~ph
    out.append(_flags(sku[derivable], "case", "case_dims_derivable"))

    # -- orphans: ordered in scope, not in the item master
    if orders is not None and len(orders) and "sku" in orders:
        lines = orders.groupby("sku").size()
        missing = lines[~lines.index.isin(set(d["sku"]))]
        out.append(_flags(pd.Series(missing.index), "*", "orphan_sku", (missing / lines.sum()).to_numpy()))

    f = pd.concat([x for x in out if len(x)], ignore_index=True) if any(len(x) for x in out) else \
        pd.DataFrame(columns=FLAG_COLUMNS)
    return f.drop_duplicates(["sku", "field", "code"])[FLAG_COLUMNS].reset_index(drop=True)


def _slips(sku, x, g, clean, usable, field, qp) -> pd.DataFrame:
    """A value whose ratio to its peer median is within unit_slip_tolerance of a slip ratio, that is an outlier
    as recorded and back among its peers when divided back (works for items near their peers' typical size)."""
    lx = np.log(x.where(usable))
    med, mad = _group_stat(np.log(x.where(clean)), g, clean)
    mad = mad.where(mad > 0)
    z = ((lx - med) / mad).abs()
    tol = np.log1p(qp.unit_slip_tolerance)
    hit = pd.Series(False, index=x.index)
    suggested = pd.Series(np.nan, index=x.index)
    for r in qp.unit_slip_ratios:
        for ratio in (r, 1 / r):
            lr = np.log(ratio)
            fixed_z = ((lx - lr - med) / mad).abs()
            ok = usable & ~hit & (z >= qp.outlier_mad_k) & ((lx - med - lr).abs() <= tol) & (fixed_z < qp.outlier_mad_k)
            suggested[ok] = x[ok] / ratio
            hit |= ok
    return _flags(sku[hit], field, "unit_slip", x[hit], np.exp(med[hit]), suggested[hit])


def _density_slips(sku, v, dens, g, clean, item_ok, qp) -> pd.DataFrame:
    """Unit slips seen through density, which does not depend on how big an item is: a weight in ounces makes it
    16x too dense, an item measured in centimetres 2.54^3 too light, one side in millimetres 25.4x too light.

    Density varies between items more than a rounding tolerance, so a slip here is judged against the peers'
    own spread: the item is an outlier as recorded, and is back among its peers (robust z below outlier_mad_k)
    once the best-fitting correction is undone.  The field blamed for a one-side slip is the side furthest from
    its peers, and only when it is further out than the weight."""
    k = qp.outlier_mad_k
    ld = np.log(dens.where(item_ok))
    med, mad = _group_stat(ld, g, clean)
    mad = mad.where(mad > 0)
    z = ((ld - med) / mad).abs()
    fz = {}
    for c in UNIT_DIMS + ["unit_weight"]:
        lc = np.log(v[c].where(item_ok))
        m, s_ = _group_stat(lc, g, clean)
        fz[c] = ((lc - m) / s_.where(s_ > 0)).abs().fillna(0)
    fz = pd.DataFrame(fz)
    side = fz[UNIT_DIMS].idxmax(axis=1)
    side_out = fz[UNIT_DIMS].max(axis=1) > fz["unit_weight"]
    # candidates: (density ratio, what is blamed, divide by)
    cands = []
    for r in qp.unit_slip_ratios:
        cands += [(r, "weight", r), (1 / r, "weight", 1 / r), (1 / r ** 3, "all_sides", r), (r ** 3, "all_sides", 1 / r),
                  (1 / r, "one_side", r), (r, "one_side", 1 / r)]
    cz = pd.DataFrame({i: ((ld - np.log(dr) - med) / mad).abs() for i, (dr, _, _) in enumerate(cands)})
    for i, (_, blame, _) in enumerate(cands):  # blame a side only if it stands out more than the weight, and v.v.
        if blame == "one_side":
            cz.loc[~side_out, i] = np.inf
        elif blame == "weight":
            cz.loc[side_out, i] = np.inf
    best = cz.idxmin(axis=1)
    best_z = cz.min(axis=1)
    hit = item_ok & (z >= k) & (best_z < k)
    rows = []
    for i, (_, blame, div) in enumerate(cands):
        pick = hit & (best == i)
        if not pick.any():
            continue
        if blame == "weight":
            rows.append(_flags(sku[pick], "unit_weight", "unit_slip", v["unit_weight"][pick], None,
                               v["unit_weight"][pick] / div))
        elif blame == "all_sides":
            for c in UNIT_DIMS:
                rows.append(_flags(sku[pick], c, "unit_slip", v[c][pick], None, v[c][pick] / div))
        else:
            for c in UNIT_DIMS:
                p2 = pick & (side == c)
                rows.append(_flags(sku[p2], c, "unit_slip", v[c][p2], None, v[c][p2] / div))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=FLAG_COLUMNS)


def duplicates(im: pd.DataFrame) -> pd.DataFrame:
    """SKUs on more than one row: identical values (duplicate_sku) or not (duplicate_conflict)."""
    rep = im[im["sku"].duplicated(keep=False)]
    if rep.empty:
        return pd.DataFrame(columns=FLAG_COLUMNS)
    cols = [c for c in REQUIRED + CASE_DIMS + ["case_weight"] if c in im]
    nunique = rep.groupby("sku")[cols].nunique(dropna=False).max(axis=1)
    rows = rep.groupby("sku").size()
    conflict = nunique > 1
    return pd.concat([_flags(pd.Series(rows.index[~conflict.to_numpy()]), "*", "duplicate_sku", rows[~conflict]),
                      _flags(pd.Series(rows.index[conflict.to_numpy()]), "*", "duplicate_conflict", rows[conflict])],
                     ignore_index=True)
