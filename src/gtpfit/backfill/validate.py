"""validate.py — how good backfill's estimates are on this client's own data, without an answer key.

    holdout(tables, quality_result, bp, ip) -> HoldoutResult(detail, summary)

A seeded share (bp.holdout_share) of the trusted active items has all its item sizes and weight hidden, the way
real gaps look; each is estimated from the remaining trusted items only, exactly as backfill would, and
compared with what was hidden.  The error of a field is a factor: the median of |log(estimate / true)|, turned
back into a ratio (1.18 = typically 18% off, either way).  Also reported: the share within ±25% and ±50%, the
same figures by method and by confidence (does "high" really beat "medium"?), and a baseline that guesses the
median trusted item for everything, so the gain over guessing is visible.

Measures; never tunes a setting.  Sides are compared longest / middle / shortest, as backfill predicts them.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from gtpfit.ingest import IngestParams
from gtpfit.quality import QualityResult

from .estimate import estimate
from .params import BackfillParams
from .targets import DIMS, UNIT_FIELDS, plan

MIN_HIDDEN = 5  # fewer hidden items than this gives no usable error figure
_FIELDS = ("long", "mid", "short", "weight", "volume")


@dataclass
class HoldoutResult:
    detail: pd.DataFrame   # one row per hidden SKU: true and estimated sides / weight, method, confidence
    summary: dict


def _stats(ratio: np.ndarray) -> dict:
    r = ratio[np.isfinite(ratio) & (ratio > 0)]
    if not len(r):
        return {"factor": None, "within_25": None, "within_50": None}
    return {"factor": float(np.exp(np.median(np.abs(np.log(r))))),
            "within_25": float(np.mean((r >= 0.75) & (r <= 1.25))),
            "within_50": float(np.mean((r >= 0.5) & (r <= 1.5)))}


def _ratios(d: pd.DataFrame, prefix: str) -> dict:
    out = {f: (d[f"{prefix}_{f}"] / d[f"true_{f}"]).to_numpy(float) for f in ("long", "mid", "short", "weight")}
    out["volume"] = ((d[f"{prefix}_long"] * d[f"{prefix}_mid"] * d[f"{prefix}_short"]) /
                     (d["true_long"] * d["true_mid"] * d["true_short"])).to_numpy(float)
    return out


def _group(d: pd.DataFrame, by: str) -> list:
    rows = []
    for key, g in d.groupby(by, sort=False):
        r = _ratios(g, "est")
        v, w = _stats(r["volume"]), _stats(r["weight"])
        rows.append({by: key, "n": int(len(g)), "volume_factor": v["factor"], "volume_within_50": v["within_50"],
                     "weight_factor": w["factor"], "weight_within_50": w["within_50"]})
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(rows, key=lambda x: (order.get(x[by], 9), -x["n"]))


def holdout(tbls: dict, quality: QualityResult, bp: BackfillParams | None = None,
            ip: IngestParams | None = None) -> HoldoutResult:
    bp = bp or BackfillParams()
    first = tbls["item_master"].drop_duplicates("sku").reset_index(drop=True)
    status = quality.status.drop_duplicates("sku")
    _, donors = plan(quality.status, quality.flags, first, bp.max_donors)
    active = set(status.loc[status["bucket"] == "active", "sku"])
    pool = [s for s in donors if s in active]
    n_hide = int(round(bp.holdout_share * len(pool)))
    empty = HoldoutResult(pd.DataFrame(), {"n": 0, "note": f"too few trusted active items to test accuracy "
                                                           f"({len(pool)})", "fields": {}, "baseline": {},
                                           "by_method": [], "by_confidence": []})
    if n_hide < MIN_HIDDEN or len(pool) - n_hide < bp.neighbours:
        return empty

    rng = np.random.default_rng(bp.seed)
    hidden = sorted(rng.choice(np.array(pool, dtype=object), size=n_hide, replace=False).tolist())
    hide = set(hidden)
    by_sku = first.set_index("sku")
    rest = [s for s in donors if s not in hide]
    peers = status.set_index("sku")["peer_group"]

    q = by_sku.loc[hidden].reset_index()
    truth = q[UNIT_FIELDS].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    q[UNIT_FIELDS] = np.nan
    if "unit_volume" in q:
        q["unit_volume"] = np.nan  # hidden with the sizes: it would give the answer away
    case_doubt = set(quality.flags.loc[quality.flags["field"].isin({"case", "case_l", "case_w", "case_h", "case_weight",
                                                                    "case_qty"}) &
                                       (quality.flags["severity"] == "unusable"), "sku"])
    q["_case_doubt"] = q["sku"].isin(case_doubt)
    donors_df = by_sku.loc[rest].reset_index()
    donors_df["_case_doubt"] = donors_df["sku"].isin(case_doubt)
    est = estimate(q, donors_df, peers, bp, q[["sku"] + UNIT_FIELDS])

    true_sides = -np.sort(-truth[:, :3], axis=1)
    detail = pd.DataFrame({"sku": hidden, "description": q.get("description", pd.Series("", index=q.index)).to_numpy(),
                           "true_long": true_sides[:, 0], "true_mid": true_sides[:, 1],
                           "true_short": true_sides[:, 2], "true_weight": truth[:, 3],
                           "est_long": est["est_long"].to_numpy(float), "est_mid": est["est_mid"].to_numpy(float),
                           "est_short": est["est_short"].to_numpy(float),
                           "est_weight": est["est_weight"].to_numpy(float), "method": est["method"].to_numpy(),
                           "confidence": est["confidence"].to_numpy(), "similarity": est["similarity"].to_numpy(),
                           "neighbours": est["neighbours"].to_numpy()})

    # the baseline: the median trusted item (of the items left to borrow from), for every hidden item
    rest_vals = donors_df[UNIT_FIELDS].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    med_sides = np.median(-np.sort(-rest_vals[:, :3], axis=1), axis=0)
    base = detail.assign(base_long=med_sides[0], base_mid=med_sides[1], base_short=med_sides[2],
                         base_weight=float(np.median(rest_vals[:, 3])))

    est_r, base_r = _ratios(detail, "est"), _ratios(base, "base")
    fields = {f: _stats(est_r[f]) for f in _FIELDS}
    baseline = {f: _stats(base_r[f]) for f in _FIELDS}
    summary = {"n": int(n_hide), "pool": int(len(pool)), "donors": int(len(rest)), "seed": int(bp.seed),
               "share": float(bp.holdout_share), "note": "", "fields": fields, "baseline": baseline,
               "volume_lift": float(baseline["volume"]["factor"] / fields["volume"]["factor"])
               if fields["volume"]["factor"] else None,
               "by_method": _group(detail, "method"), "by_confidence": _group(detail, "confidence")}
    return HoldoutResult(detail, summary)
