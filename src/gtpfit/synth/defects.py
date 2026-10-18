"""defects.py — plant realistic data problems and record every one.

Item-master defects are applied to disjoint SKU samples, one defect per SKU,
so the manifest is an exact answer key: for every planted problem it holds
the table, SKU, field, defect type, true value and the value the client
"sent".  Transaction defects are logged per row the same way.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DIMS = ["unit_l", "unit_w", "unit_h"]
CASE_DIMS = ["case_l", "case_w", "case_h"]


def _log(rows, table, key, field, defect, true, observed):
    rows.append(dict(table=table, key=key, field=field, defect=defect,
                     true_value=true, observed_value=observed))


def plant_item_master(p, im_true: pd.DataFrame, rng: np.random.Generator):
    r = p.defects
    im = im_true.copy()
    log: list[dict] = []
    n = len(im)
    pool = rng.permutation(n)
    cursor = 0

    def take(rate, eligible=None):
        nonlocal cursor
        k = int(round(rate * n))
        chosen = []
        while len(chosen) < k and cursor < n:
            i = pool[cursor]; cursor += 1
            if eligible is None or eligible[i]:
                chosen.append(i)
        return np.array(chosen, int)

    sku = im["sku"].to_numpy()
    for i in take(r.missing_dims):
        for f in DIMS:
            _log(log, "item_master", sku[i], f, "missing_dims", im.at[i, f], None)
            im.at[i, f] = np.nan
    for i in take(r.missing_weight):
        _log(log, "item_master", sku[i], "unit_weight", "missing_weight", im.at[i, "unit_weight"], None)
        im.at[i, "unit_weight"] = np.nan
    for i in take(r.missing_case_qty):
        _log(log, "item_master", sku[i], "case_qty", "missing_case_qty", im.at[i, "case_qty"], None)
        im.at[i, "case_qty"] = np.nan
    for rate, val, tag in ((r.placeholder_001, 0.01, "placeholder_0.01"), (r.placeholder_111, 1.0, "placeholder_1")):
        for i in take(rate):
            for f in DIMS:
                _log(log, "item_master", sku[i], f, tag, im.at[i, f], val)
                im.at[i, f] = val
    for i in take(r.metric_dims):
        for f in DIMS:
            v = round(im.at[i, f] * 2.54, 2)
            _log(log, "item_master", sku[i], f, "metric_units", im.at[i, f], v)
            im.at[i, f] = v
    for i in take(r.weight_in_oz):
        v = round(im.at[i, "unit_weight"] * 16, 3)
        _log(log, "item_master", sku[i], "unit_weight", "ounces_as_pounds", im.at[i, "unit_weight"], v)
        im.at[i, "unit_weight"] = v
    for i in take(r.decimal_shift):
        f = DIMS[int(rng.integers(3))]
        v = round(im.at[i, f] * 10, 2)
        _log(log, "item_master", sku[i], f, "decimal_shift_x10", im.at[i, f], v)
        im.at[i, f] = v
    for i in take(r.case_qty_default_1, eligible=(im["case_qty"] > 1).to_numpy()):
        _log(log, "item_master", sku[i], "case_qty", "default_1", im.at[i, "case_qty"], 1)
        im.at[i, "case_qty"] = 1
    for i in take(r.case_smaller_than_unit):
        for f, u in zip(CASE_DIMS, DIMS):
            v = round(im.at[i, u] / 2, 2)
            _log(log, "item_master", sku[i], f, "case_smaller_than_unit", im.at[i, f], v)
            im.at[i, f] = v

    # orphans: ordered SKUs dropped from the item master entirely
    sellable = (~im_true["inventory_only"]).to_numpy()
    orphan_idx = take(r.orphan_skus, eligible=sellable)
    for i in orphan_idx:
        _log(log, "item_master", sku[i], "*", "orphan_not_in_item_master", "present", None)

    # duplicates: a second row for the same SKU with conflicting dims
    dup_idx = take(r.duplicate_sku_rows)
    dups = im.loc[dup_idx].copy()
    for f in DIMS:
        dups[f] = (dups[f] * rng.uniform(0.6, 1.4, len(dups))).round(2)
    for i in dup_idx:
        _log(log, "item_master", sku[i], "*", "duplicate_conflicting_row", "single row", "two rows")

    observed = pd.concat([im.drop(index=orphan_idx), dups], ignore_index=True)
    observed = observed.sample(frac=1, random_state=int(rng.integers(1 << 31))).reset_index(drop=True)
    return observed, log, set(sku[orphan_idx])


def plant_transactions(p, orders: pd.DataFrame, rng: np.random.Generator, log: list):
    r = p.defects
    o = orders.copy()
    n = len(o)
    # SKU formatting variants for a handful of SKUs (every row of that SKU)
    uniq = o["sku"].unique()
    variants = rng.choice(uniq, size=int(round(r.sku_format_variants * len(uniq))), replace=False)
    for s in variants:
        bad = s.lower() + "  " if rng.random() < 0.5 else " " + s.lower()
        o.loc[o["sku"] == s, "sku"] = bad
        _log(log, "orders", s, "sku", "sku_format_variant", s, bad)
    # zero / negative quantities
    idx = rng.choice(n, int(round(r.zero_or_negative_qty * n)), replace=False)
    newq = np.where(rng.random(len(idx)) < 0.5, 0, -o["units"].to_numpy()[idx])
    for i, q in zip(idx, newq):
        _log(log, "orders", f"row:{o['order'].iat[i]}|{o['sku'].iat[i]}", "units", "zero_or_negative_qty", int(o["units"].iat[i]), int(q))
    o.loc[o.index[idx], "units"] = newq
    # missing pick time
    idx = rng.choice(n, int(round(r.missing_pick_time * n)), replace=False)
    o["secs"] = o["secs"].astype(float)
    o.loc[o.index[idx], "secs"] = np.nan
    _log(log, "orders", "*", "pick_time", "missing_pick_time", len(idx), "rows")
    # exact duplicate rows
    idx = rng.choice(n, int(round(r.duplicate_order_rows * n)), replace=False)
    o = pd.concat([o, o.iloc[idx]], ignore_index=True)
    _log(log, "orders", "*", "*", "duplicate_rows", len(idx), "rows")
    return o
