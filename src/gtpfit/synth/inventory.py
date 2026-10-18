"""inventory.py — receipts and on-hand snapshots from a reorder-point policy.

Every SKU runs a continuous-review (s, Q) policy against the demand the
orders actually generated, so inbound, inventory and outbound agree:

    Q  = daily rate x cover days (by velocity class), rounded up to cases
    s  = daily rate x (lead time + 3 days review) x safety factor
    order Q whenever on-hand + on-order <= s; it lands lead-time days later

Dead-stock SKUs (never ordered) hold a static few cases and get no receipts.
Snapshots are taken at each month end.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def velocity_class(lines_by_sku: np.ndarray) -> np.ndarray:
    """A/B/C/D by cumulative share of lines: 50 / 80 / 95 / rest."""
    order = np.argsort(-lines_by_sku, kind="stable")
    cum = np.cumsum(lines_by_sku[order]) / max(lines_by_sku.sum(), 1)
    cls = np.empty(len(order), dtype="<U1")
    cls[order] = np.select([cum <= 0.5, cum <= 0.8, cum <= 0.95], ["A", "B", "C"], "D")
    cls[lines_by_sku == 0] = "D"
    return cls


def simulate(p, im: pd.DataFrame, orders: pd.DataFrame, cal: pd.DataFrame, rng: np.random.Generator):
    n, T = len(im), len(cal)
    demand = np.zeros((n, T))
    np.add.at(demand, (orders["sku_idx"].to_numpy(), orders["day"].to_numpy()), orders["units"].to_numpy())
    lines = np.bincount(orders["sku_idx"].to_numpy(), minlength=n).astype(float)
    vclass = velocity_class(lines)

    active_days = np.maximum(im["active_to"].to_numpy() - im["active_from"].to_numpy(), 1)
    rate = demand.sum(1) / active_days
    case = im["case_qty"].to_numpy()
    cover = np.vectorize(p.cover_days.get)(vclass)
    lt_lo, lt_hi = p.lead_time_days
    sup_lt = rng.integers(lt_lo, lt_hi + 1, p.n_suppliers + 1)
    lt = sup_lt[im["supplier"].to_numpy()]
    Q = np.maximum(np.ceil(rate * cover / case), 1) * case
    s = np.ceil(rate * (lt + 3) * p.safety_factor)

    oh = np.where(im["active_from"].to_numpy() == 0, s + np.ceil(Q * rng.uniform(0.2, 0.9, n)), 0.0)
    dead = im["inventory_only"].to_numpy()
    oh[dead] = case[dead] * rng.integers(1, 6, dead.sum())
    pipeline = np.zeros((n, T + lt_hi + 2))
    on_order = np.zeros(n)
    rec_sku, rec_day, rec_qty = [], [], []
    month_end = {i for i in range(T) if i == T - 1 or cal["date"].iat[i].month != cal["date"].iat[i + 1].month}
    snaps = []
    for d in range(T):
        arriving = pipeline[:, d]
        got = arriving > 0
        if got.any():
            idx = np.flatnonzero(got)
            rec_sku.append(idx); rec_day.append(np.full(len(idx), d)); rec_qty.append(arriving[idx])
            oh += arriving
            on_order -= arriving
        oh = np.maximum(oh - demand[:, d], 0)
        live = (~dead) & (d >= im["active_from"].to_numpy() - lt) & (d < im["active_to"].to_numpy() - 7)
        need = live & (oh + on_order <= s) & (rate > 0)
        if need.any():
            idx = np.flatnonzero(need)
            # suppliers occasionally short-ship part of a case quantity
            q = Q[idx].copy()
            short = rng.random(len(idx)) < 0.03
            q[short] = np.maximum(q[short] - np.ceil(case[idx][short] * rng.uniform(0.1, 0.9, short.sum())), 1)
            pipeline[idx, d + lt[idx]] += q
            on_order[idx] += q
        if d in month_end:
            have = np.flatnonzero(oh > 0)
            snaps.append(pd.DataFrame(dict(day=d, sku_idx=have, on_hand=oh[have].astype(int))))

    receipts = pd.DataFrame(dict(sku_idx=np.concatenate(rec_sku), day=np.concatenate(rec_day),
                                 units=np.concatenate(rec_qty).astype(int)))
    receipts["supplier"] = im["supplier"].to_numpy()[receipts["sku_idx"]]
    # one PO per supplier per arrival day; receipt number per PO
    po = receipts.groupby(["supplier", "day"]).ngroup()
    receipts["po"] = np.char.add("PO", (700000 + po.to_numpy()).astype(str))
    receipts["receipt"] = np.char.add("RC", (300000 + po.to_numpy()).astype(str))
    snapshots = pd.concat(snaps, ignore_index=True)
    stats = pd.DataFrame(dict(velocity=vclass, lines=lines, daily_rate=rate, lead_time=lt,
                              reorder_point=s, order_qty=Q))
    return receipts, snapshots, stats
