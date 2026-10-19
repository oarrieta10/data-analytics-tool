"""lines.py — what counts as an order line: a per-client choice, agreed with the client.

    define_lines(orders, how)   -> one row per order line under that definition, with a `picks` count
    order_profile(orders, how)  -> lines/order, units/line, units/order, picks/line by channel

Definitions (LINE_DEFINITIONS):
    row        every row in the order file is a line (e.g. a SKU split across
               cartons is several lines, which can be real when the quantity
               needed multiple cartons)
    order_sku  rows with the same order + SKU are merged into one line and
               their quantities summed

Either way `picks` keeps the number of file rows behind each line, so carton-
or pick-level work is not lost.  Ingest keeps the file's rows untouched; this
is applied when the data is analysed (benchmark now, profile/ later).
Works on canonical order columns (order_id, sku, qty or another quantity column).
"""
from __future__ import annotations

import pandas as pd

LINE_DEFINITIONS = {
    "row": "Every row in the order file is a line (a SKU split across cartons counts once per carton).",
    "order_sku": "One line per order + SKU: repeated rows for a SKU on an order are merged, quantities summed.",
}
_SUMS = ("eaches", "cases")


def define_lines(orders: pd.DataFrame, how: str = "row", qty_col: str = "qty") -> pd.DataFrame:
    if how not in LINE_DEFINITIONS:
        raise ValueError(f"Unknown line definition {how!r}; choose from {sorted(LINE_DEFINITIONS)}")
    if how == "row":
        return orders.assign(picks=1)
    keys = ["order_id", "sku"]
    g = orders.groupby(keys, sort=False, dropna=False)
    agg = {c: "first" for c in orders.columns if c not in keys}
    for c in (qty_col, *_SUMS):
        if c in orders:
            agg[c] = "sum"
    if "full_case" in orders:
        agg["full_case"] = "any"
    out = g.agg(agg)
    for c in (qty_col, *_SUMS):  # a line whose rows are all unknown stays unknown, not 0
        if c in orders:
            out[c] = out[c].where(g[c].count() > 0)
            if pd.api.types.is_integer_dtype(orders[c].dtype):
                out[c] = out[c].astype(orders[c].dtype)
    out["picks"] = g.size()
    return out.reset_index()[list(orders.columns) + ["picks"]]


def order_profile(orders: pd.DataFrame, how: str = "row", qty_col: str = "qty") -> pd.DataFrame:
    """Order profile per channel plus 'All' under one line definition."""
    lines = define_lines(orders, how, qty_col)
    groups = [("All", lines)]
    if "channel" in lines:
        groups += [(str(ch), sub) for ch, sub in lines.groupby("channel", sort=True)]
    rows = []
    for ch, d in groups:
        per = d.groupby("order_id").size()
        q = d[qty_col].astype(float)
        rows.append(dict(channel=ch, definition=how, orders=len(per), lines=len(d), picks=int(d["picks"].sum()),
                         units=float(q.sum()), lines_per_order=len(d) / len(per),
                         units_per_line=float(q.sum() / q.notna().sum()) if q.notna().any() else float("nan"),
                         units_per_order=float(q.sum() / len(per)), picks_per_line=d["picks"].sum() / len(d),
                         single_line_share=float((per == 1).mean())))
    return pd.DataFrame(rows)
