"""estimate.py — an estimate of an item's sizes and weight from the trusted items most like it.

    estimate(query, donors, peers, bp) -> one row per query SKU: est_dims (longest, middle, shortest),
                                          est_weight, method, confidence, similarity, neighbours

Tiers, first that applies:
    case_of_one   a case of one unit with its own sizes: the unit is the case
    neighbours    the bp.neighbours most similar trusted items, combined by a similarity-weighted median of
                  their log sizes (sides taken longest / middle / shortest, so orientation adds no noise);
                  scaled to the item's own volume when the item master gives one (neighbours_volume)
    peer_median   nothing similar enough: the median of the item's peer group (else of all trusted items)
    none          no trusted items at all
Similarity (0 to 1) blends the description (TF-IDF of character n-grams, which copes with abbreviations and
words run together) with sizes the item already has (case sizes and weight, case volume per unit, any
trusted item size), weighted by bp.size_weight, and is cut by bp.category_penalty across categories.

Pure and deterministic: no random draws, ties broken by donor order.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .params import BackfillParams

METHODS = ("client", "case_of_one", "neighbours", "neighbours_volume", "peer_median", "none")
ACTIONS = ("filled", "kept", "kept_review", "not_estimated")
DIMS = ["unit_l", "unit_w", "unit_h"]
CASE = ["case_l", "case_w", "case_h"]
_SPLIT = re.compile(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])")


def normalise_text(s: pd.Series) -> pd.Series:
    """Lower case, letters and digits apart ("PUMP800W" -> "pump 800 w"), punctuation as spaces."""
    t = s.fillna("").astype(str).str.lower()
    t = t.map(lambda x: _SPLIT.sub(" ", x))
    return t.str.replace(r"[^a-z0-9]+", " ", regex=True).str.strip()


def _num(df: pd.DataFrame, c: str) -> np.ndarray:
    return pd.to_numeric(df[c], errors="coerce").to_numpy(float) if c in df else np.full(len(df), np.nan)


def size_features(df: pd.DataFrame, trusted_unit: pd.DataFrame | None = None) -> np.ndarray:
    """Log size features per row (NaN where unknown): case sides longest-first, case weight, case volume per
    unit, then the item's own longest / middle / shortest side and weight where trusted."""
    n = len(df)
    cs = np.column_stack([_num(df, c) for c in CASE])
    case_ok = (cs > 0).all(axis=1) & ~df.get("_case_doubt", pd.Series(False, index=df.index)).to_numpy(bool)
    cs = -np.sort(-np.where(case_ok[:, None], cs, np.nan), axis=1)
    cw = _num(df, "case_weight")
    cw = np.where(case_ok & (cw > 0), cw, np.nan)
    qty = _num(df, "case_qty")
    per_unit = np.where(case_ok & (qty > 0), np.prod(cs, axis=1) / qty, np.nan)
    u = trusted_unit if trusted_unit is not None else df
    us = np.column_stack([_num(u, c) for c in DIMS])
    us = -np.sort(-np.where((us > 0).all(axis=1)[:, None], us, np.nan), axis=1)
    uw = _num(u, "unit_weight")
    uw = np.where(uw > 0, uw, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(np.column_stack([cs, cw, per_unit, us, uw]).reshape(n, 9))


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values, kind="stable")
    v, w = values[order], weights[order]
    c = np.cumsum(w)
    return float(v[np.searchsorted(c, c[-1] / 2.0)])


def _donor_targets(donors: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    d = -np.sort(-np.column_stack([_num(donors, c) for c in DIMS]), axis=1)
    return np.log(d), np.log(_num(donors, "unit_weight"))


def _empty(query: pd.DataFrame) -> pd.DataFrame:
    n = len(query)
    return pd.DataFrame({"sku": query["sku"].to_numpy(), "est_long": np.nan, "est_mid": np.nan, "est_short": np.nan,
                         "est_weight": np.nan, "method": ["none"] * n, "confidence": [""] * n,
                         "similarity": np.nan, "neighbours": [""] * n})


def estimate(query: pd.DataFrame, donors: pd.DataFrame, peers: pd.Series, bp: BackfillParams | None = None,
             query_unit: pd.DataFrame | None = None) -> pd.DataFrame:
    """query / donors: item rows (sku, description, category, case_*, unit_*); query_unit: the query's item
    sizes that can be trusted (NaN elsewhere); peers: sku -> peer group."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.neighbors import NearestNeighbors

    bp = bp or BackfillParams()
    out = _empty(query)
    if not len(query) or not len(donors):
        return out
    q = query.reset_index(drop=True)
    d = donors.reset_index(drop=True)
    d_dims, d_w = _donor_targets(d)

    # -- description
    tq, td = normalise_text(q.get("description", pd.Series("", index=q.index))), \
        normalise_text(d.get("description", pd.Series("", index=d.index)))
    lo, hi = bp.description_ngrams
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(int(lo), int(hi)), sublinear_tf=True, dtype=np.float32)
    vec.fit(pd.concat([td, tq], ignore_index=True))
    xq, xd = vec.transform(tq), vec.transform(td)
    has_text_q = np.asarray(xq.getnnz(axis=1) > 0)
    k_c = min(bp.candidates, len(d))
    text_cands = np.full((len(q), 0), -1)
    if has_text_q.any() and xd.nnz:
        nn = NearestNeighbors(n_neighbors=k_c, metric="cosine", algorithm="brute").fit(xd)
        idx = np.full((len(q), k_c), -1)
        idx[has_text_q] = nn.kneighbors(xq[has_text_q], return_distance=False)
        text_cands = idx

    # -- sizes
    fq = size_features(q, query_unit)
    fd = size_features(d)
    case_q, case_d = ~np.isnan(fq[:, :5]).any(axis=1), ~np.isnan(fd[:, :5]).any(axis=1)
    size_cands = np.full((len(q), 0), -1)
    if case_q.any() and case_d.sum() >= 1:
        rows_d = np.flatnonzero(case_d)
        nn = NearestNeighbors(n_neighbors=min(k_c, len(rows_d))).fit(fd[rows_d, :5])
        idx = np.full((len(q), min(k_c, len(rows_d))), -1)
        idx[case_q] = rows_d[nn.kneighbors(fq[case_q, :5], return_distance=False)]
        size_cands = idx

    cat_q = q["category"].astype("string").to_numpy() if "category" in q else None
    cat_d = d["category"].astype("string").to_numpy() if "category" in d else None
    d_sku = d["sku"].astype(str).to_numpy()
    d_peer = d["sku"].map(peers).astype("string").to_numpy()
    d_logvol = d_dims.sum(axis=1)

    rows = []
    for i in range(len(q)):
        cands = np.unique(np.concatenate([text_cands[i], size_cands[i]]))
        cands = cands[cands >= 0]
        best = None
        if len(cands):
            parts, weights = [], []
            if has_text_q[i]:
                parts.append(np.asarray((xd[cands] @ xq[i].T).todense()).ravel())
                weights.append(1.0)
            shared = ~np.isnan(fq[i]) & ~np.isnan(fd[cands])
            if bp.size_weight > 0 and shared.any():
                diff = np.where(shared, fd[cands] - fq[i], 0.0)
                n_sh = shared.sum(axis=1)
                dist = np.sqrt((diff ** 2).sum(axis=1) / np.maximum(n_sh, 1))
                ssim = np.where(n_sh > 0, np.exp(-dist), np.nan)
                if has_text_q[i]:
                    sim_t = parts[0]
                    blend = np.where(np.isnan(ssim), sim_t, (sim_t + bp.size_weight * np.nan_to_num(ssim)) /
                                     (1 + bp.size_weight))
                    parts, weights = [blend], [1.0]
                else:
                    parts, weights = [np.nan_to_num(ssim)], [1.0]
            sim = parts[0] if parts else np.zeros(len(cands))
            if cat_q is not None and cat_d is not None and not pd.isna(cat_q[i]):
                other = pd.notna(cat_d[cands]) & (cat_d[cands] != cat_q[i])
                sim = np.where(other, sim * (1 - bp.category_penalty), sim)
            order = np.lexsort((cands, -sim))[: bp.neighbours]
            keep = order[sim[order] >= bp.min_similarity]
            if len(keep):
                best = (cands[keep], sim[keep])
        if best is not None:
            nb, w = best
            w = np.maximum(w, 1e-9)
            dims = sorted([np.exp(_weighted_median(d_dims[nb, j], w)) for j in range(3)], reverse=True)
            wt = float(np.exp(_weighted_median(d_w[nb], w)))
            lv = d_logvol[nb]
            spread = float(np.exp(_weighted_median(np.abs(lv - _weighted_median(lv, w)), w)))
            conf = "high" if (w.max() >= bp.high_similarity and spread <= bp.high_max_spread) else "medium"
            rows.append((dims, wt, "neighbours", conf, float(w.max()), ", ".join(d_sku[nb])))
            continue
        peer = peers.get(q.at[i, "sku"]) if isinstance(peers, pd.Series) else None
        grp = np.flatnonzero(d_peer == peer) if peer is not None and not pd.isna(peer) else np.array([], int)
        grp = grp if len(grp) >= bp.min_donors else np.arange(len(d))
        dims = sorted(np.exp(np.median(d_dims[grp], axis=0)).tolist(), reverse=True)
        rows.append((dims, float(np.exp(np.median(d_w[grp]))), "peer_median", "low", np.nan, ""))

    out["est_long"] = [r[0][0] for r in rows]
    out["est_mid"] = [r[0][1] for r in rows]
    out["est_short"] = [r[0][2] for r in rows]
    out["est_weight"] = [r[1] for r in rows]
    out["method"] = [r[2] for r in rows]
    out["confidence"] = [r[3] for r in rows]
    out["similarity"] = [r[4] for r in rows]
    out["neighbours"] = [r[5] for r in rows]

    # -- a case of one unit is the unit itself (when its case sizes can be trusted)
    cs = np.column_stack([_num(q, c) for c in CASE])
    one = (_num(q, "case_qty") == 1) & (cs > 0).all(axis=1) & \
        ~q.get("_case_doubt", pd.Series(False, index=q.index)).to_numpy(bool)
    if one.any():
        srt = -np.sort(-cs[one], axis=1)
        out.loc[one, ["est_long", "est_mid", "est_short"]] = srt
        cw = _num(q, "case_weight")[one]
        out.loc[one, "est_weight"] = np.where(cw > 0, cw, out.loc[one, "est_weight"].to_numpy(float))
        out.loc[one, ["method", "confidence", "neighbours"]] = ["case_of_one", "medium", ""]
        out.loc[one, "similarity"] = np.nan

    # -- the item's own volume, when given, sets the scale (the neighbours set the shape)
    vol = _num(q, "unit_volume")
    scale = (out["method"] == "neighbours").to_numpy() & (vol > 0)
    if scale.any():
        cur = out.loc[scale, ["est_long", "est_mid", "est_short"]].to_numpy(float)
        f = (vol[scale] / cur.prod(axis=1)) ** (1 / 3)
        out.loc[scale, ["est_long", "est_mid", "est_short"]] = cur * f[:, None]
        out.loc[scale, "method"] = "neighbours_volume"
    return out
