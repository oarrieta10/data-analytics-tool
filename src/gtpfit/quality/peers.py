"""peers.py — which SKUs each SKU is compared with, and how far it sits from them.

A SKU's peer group is its sub-category, else its category, else the whole assortment: whichever first has at
least min_size SKUs.  Distances are robust z-scores in log space (sizes and weights spread multiplicatively):
(log x - median) / (1.4826 x MAD).  A group with no spread (every value equal) puts nobody outside it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MAD_TO_SD = 1.4826  # scales a median absolute deviation to a standard deviation for normal data


def peer_groups(im: pd.DataFrame, min_size: int) -> pd.DataFrame:
    """sku, peer_group, peer_level for one row per SKU in im (first row of a repeated SKU)."""
    d = im.drop_duplicates("sku")[["sku"] + [c for c in ("category", "subcategory") if c in im]].copy()
    group = pd.Series("all", index=d.index, dtype=object)
    level = pd.Series("all", index=d.index, dtype=object)
    for col in ("category", "subcategory"):  # finer last, so it wins where big enough
        if col not in d:
            continue
        key = d[col].astype("string")
        if col == "subcategory" and "category" in d:
            key = d["category"].astype("string").fillna("") + " / " + key
        size = key.map(key.value_counts())
        ok = key.notna() & d[col].notna() & (size >= min_size)
        group[ok], level[ok] = key[ok], col
    return pd.DataFrame({"sku": d["sku"].to_numpy(), "peer_group": group.to_numpy(), "peer_level": level.to_numpy()})


def robust_stats(values: pd.Series, groups: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Per-row median and scaled MAD of log(values) within its group (positive values only)."""
    lv = np.log(values.where(values > 0))
    med = lv.groupby(groups).transform("median")
    mad = (lv - med).abs().groupby(groups).transform("median") * MAD_TO_SD
    return med, mad


def robust_z(values: pd.Series, groups: pd.Series) -> pd.Series:
    """Robust z-score in log space within each group; 0 where the group has no spread or the value is missing."""
    med, mad = robust_stats(values, groups)
    z = (np.log(values.where(values > 0)) - med) / mad.where(mad > 0)
    return z.fillna(0.0)
