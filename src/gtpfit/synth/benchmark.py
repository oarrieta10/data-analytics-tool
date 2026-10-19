"""benchmark.py — compare the shape of two warehouse datasets (e.g. a real client vs a synthetic one).

    shape(orders, receipts=None, inventory=None, items=None) -> (metrics dict, series dict)
    compare(a_metrics, b_metrics, names) -> DataFrame

Inputs are canonical frames (ingest column names):
    orders     order_id, sku, order_date, units, channel ("Ecommerce" / "Store"), hour (opt), full_case (opt)
    receipts   receipt_id, sku, receipt_date, qty
    inventory  snapshot_date, sku, on_hand
    items      sku, unit_l, unit_w, unit_h, unit_weight, case_qty

Only shape metrics (shares, ratios, per-order and per-day figures) so a real
client and a synthetic one of a different size can be compared.  Used to
calibrate synthetic profiles; it does not size anything.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _per_channel(o: pd.DataFrame, m: dict, tag: str):
    per = o.groupby("order_id").agg(lines=("sku", "size"), units=("units", "sum"))
    m[f"{tag}single_line_share"] = float((per["lines"] == 1).mean())
    m[f"{tag}lines_per_order"] = float(per["lines"].mean())
    m[f"{tag}lines_per_order_p90"] = float(per["lines"].quantile(0.9))
    u = o["units"]
    m[f"{tag}units_per_line"] = float(u.mean())
    m[f"{tag}single_unit_share"] = float((u == 1).mean())
    if "full_case" in o:
        m[f"{tag}full_case_line_share"] = float(o["full_case"].mean())
    return per


def shape(orders: pd.DataFrame, receipts=None, inventory=None, items=None):
    o = orders.copy()
    o["order_date"] = pd.to_datetime(o["order_date"]).dt.normalize()
    m, s = {}, {}
    days = o["order_date"].nunique()
    span = (o["order_date"].max() - o["order_date"].min()).days + 1
    m["span_days"] = span
    m["picking_days"] = days
    m["picking_days_per_week"] = days / span * 7
    m["lines"], m["orders"], m["units"], m["skus_picked"] = len(o), o["order_id"].nunique(), float(o["units"].sum()), \
        o["sku"].nunique()

    # channels
    for ch in ("Ecommerce", "Store"):
        sub = o[o["channel"] == ch]
        m[f"{ch.lower()}_line_share"] = len(sub) / len(o)
        m[f"{ch.lower()}_order_share"] = sub["order_id"].nunique() / m["orders"]
        if len(sub):
            per = _per_channel(sub, m, f"{ch.lower()}_")
            lpo = per["lines"].clip(upper=20).value_counts(normalize=True).sort_index()
            s[f"lpo_{ch.lower()}"] = {int(k): round(float(v), 5) for k, v in lpo.items()}
    _per_channel(o, m, "all_")

    # daily volume
    daily = o.groupby("order_date").size()
    m["lines_per_picking_day"] = float(daily.mean())
    m["design_ratio_p95"] = float(daily.quantile(0.95) / daily.mean())
    m["peak_ratio"] = float(daily.max() / daily.mean())
    m["daily_cv"] = float(daily.std() / daily.mean())
    wd = o.groupby(o["order_date"].dt.weekday).size().reindex(range(7), fill_value=0)
    s["weekday_share"] = dict(zip(WEEKDAYS, (wd / wd.sum()).round(4).tolist()))
    mo = o.groupby(o["order_date"].dt.month).size()
    s["month_index"] = {int(k): round(float(v), 4) for k, v in (mo / mo.mean()).items()}
    m["month_index_max"], m["month_index_min"] = float((mo / mo.mean()).max()), float((mo / mo.mean()).min())
    s["daily_sorted"] = (daily.sort_values(ascending=False) / daily.mean()).round(4).tolist()  # load-duration

    # busy-day baskets (e-com), same split as the realism report
    busy = daily.index[daily >= daily.quantile(0.95)]
    ec = o[o["channel"] == "Ecommerce"]
    if len(ec):
        eo = ec.groupby("order_id").agg(l=("sku", "size"), d=("order_date", "first"))
        on = eo["d"].isin(busy)
        m["ecom_peak_basket_ratio"] = float(eo.loc[on, "l"].mean() / eo.loc[~on, "l"].mean()) if on.any() else np.nan

    # SKU concentration
    sl = o.groupby("sku").size().sort_values(ascending=False)
    cum = sl.cumsum().to_numpy() / sl.sum()
    m["top20_sku_line_share"] = float(cum[max(int(0.2 * len(sl)) - 1, 0)])
    m["top1_sku_line_share"] = float(cum[max(int(0.01 * len(sl)) - 1, 0)])
    m["skus_picked_per_day"] = float(o.groupby("order_date")["sku"].nunique().mean())
    pick = np.unique(np.r_[np.linspace(0, len(cum) - 1, 200).astype(int)])
    s["pareto"] = [(float((i + 1) / len(cum)), float(cum[i])) for i in pick]

    if "hour" in o and o["hour"].notna().any():
        h = o["hour"].dropna().astype(int).value_counts(normalize=True).reindex(range(24), fill_value=0)
        s["hour_share"] = h.round(4).tolist()
        m["active_hours"] = int((h > 0.005).sum())
        m["peak_hour_share"] = float(h.max())

    # receipts
    if receipts is not None and len(receipts):
        r = receipts.copy()
        r["receipt_date"] = pd.to_datetime(r["receipt_date"]).dt.normalize()
        m["receipt_lines_per_receiving_day"] = len(r) / r["receipt_date"].nunique()
        m["lines_per_receipt"] = len(r) / r["receipt_id"].nunique() if "receipt_id" in r else np.nan
        m["units_per_receipt_line"] = float(r["qty"].mean())
        m["units_received_vs_picked"] = float(r["qty"].sum() / max(m["units"], 1))

    # inventory
    if inventory is not None and len(inventory):
        inv = inventory.copy()
        per_snap = inv.groupby("snapshot_date")["on_hand"].sum()
        m["snapshots"] = len(per_snap)
        m["skus_on_hand_per_snapshot"] = float(inv.groupby("snapshot_date")["sku"].nunique().mean())
        units_per_day = m["units"] / span
        m["days_on_hand_total"] = float(per_snap.mean() / units_per_day)
        m["turns_per_year"] = float(m["units"] / span * 365 / per_snap.mean())
        rate = o.groupby("sku")["units"].sum() / span
        avg_oh = inv.groupby("sku")["on_hand"].mean()
        doh = (avg_oh / rate.reindex(avg_oh.index)).replace([np.inf], np.nan).dropna()
        m["median_sku_days_on_hand"] = float(doh.median())
        m["stocked_skus_not_picked_share"] = float(1 - avg_oh.index.isin(rate.index).mean())

    # item master
    if items is not None and len(items):
        it = items
        d = it[["unit_l", "unit_w", "unit_h"]]
        usable = (d > 0.25).all(axis=1) & (it["unit_weight"] > 0)
        m["item_master_rows"] = len(it)
        m["item_master_skus_picked_share"] = float(it["sku"].isin(sl.index).mean())
        m["usable_unit_dims_share"] = float(usable.mean())
        m["placeholder_001_share"] = float((d == 0.01).all(axis=1).mean())
        m["cube_dims_all_equal_share"] = float(((d["unit_l"] == d["unit_w"]) & (d["unit_w"] == d["unit_h"])).mean())
        u = it.loc[usable]
        picked = u[u["sku"].isin(sl.index)]
        if len(picked):
            longest = picked[["unit_l", "unit_w", "unit_h"]].max(axis=1)
            vol = picked["unit_l"] * picked["unit_w"] * picked["unit_h"] / 1728
            m["picked_median_longest_in"] = float(longest.median())
            m["picked_median_cube_ft3"] = float(vol.median())
            m["picked_median_density_lb_ft3"] = float((picked["unit_weight"] / vol).median())
            m["picked_median_weight_lb"] = float(picked["unit_weight"].median())
        if "case_qty" in it:
            cq = it.loc[it["sku"].isin(sl.index), "case_qty"]
            m["picked_median_case_qty"] = float(cq[cq > 0].median()) if (cq > 0).any() else np.nan
    return m, s


def compare(a: dict, b: dict, names=("real", "synthetic")) -> pd.DataFrame:
    keys = [k for k in a if k in b] + [k for k in a if k not in b] + [k for k in b if k not in a]
    rows = []
    for k in keys:
        x, y = a.get(k, np.nan), b.get(k, np.nan)
        ratio = y / x if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x not in (0,) and \
            not np.isnan(x) and not np.isnan(y) else np.nan
        rows.append({"metric": k, names[0]: x, names[1]: y, f"{names[1]} / {names[0]}": ratio})
    return pd.DataFrame(rows)
