"""preview.py — what a profile will produce, in milliseconds, without generating it.

    preview(profile)  -> the calendar: per-day volume factor, event lift, basket shape
    estimate(profile) -> expected orders, order lines and generation time

Expected values only: the generator's draws (and its merging of repeat SKUs
within an order) make the real counts land within a few percent.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .demand import build_calendar
from .generate import EXCEL_ROW_LIMIT

SECONDS_PER_MILLION_LINES = 10.0  # measured on the default profile (laptop, 2026-10)


def preview(p) -> pd.DataFrame:
    return build_calendar(p, np.random.default_rng(p.seed))


def estimate(p, cal: pd.DataFrame | None = None) -> dict:
    cal = preview(p) if cal is None else cal
    vf = cal["volume_factor"].to_numpy()
    sl = cal["ecom_single_line_share"].to_numpy()
    gp = cal["ecom_multi_line_geom_p"].to_numpy()
    ecom_orders = p.ecom_orders_per_day * vf
    ecom_lines = ecom_orders * (sl + (1 - sl) * (1 + 1 / gp))
    weekday = (cal["weekday"].to_numpy() < 5) & (vf > 0)
    store_orders = p.b2b_orders_per_day * np.minimum(vf, p.b2b_volume_cap) * weekday
    store_lines = store_orders * p.b2b_lines_mean
    lines = (ecom_lines.sum() + store_lines.sum()) * (1 + p.defects.duplicate_order_rows)
    if p.truncate_orders_at_excel_limit:
        lines = min(lines, EXCEL_ROW_LIMIT)
    return dict(orders=int(ecom_orders.sum() + store_orders.sum()), order_lines=int(lines),
                ecom_orders=int(ecom_orders.sum()), store_orders=int(store_orders.sum()),
                seconds=max(1.0, lines / 1e6 * SECONDS_PER_MILLION_LINES))
