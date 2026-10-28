"""orders.py — what an order looks like: lines per order, units per line, single-line share, by channel.

    order_shape(tables, line_definition) -> OrderShape(profile, lines_per_order, units_per_line)

The profile per channel is lines.order_profile (under the client's line definition).  The distributions are
shares of orders with 1, 2, 3, 4 or 5+ lines, and of lines with 1, 2, 3-5, 6-10 or 11+ units.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from gtpfit.lines import define_lines, order_profile

LPO_BINS = [0, 1, 2, 3, 4, float("inf")]
LPO_LABELS = ["1", "2", "3", "4", "5+"]
UPL_BINS = [0, 1, 2, 5, 10, float("inf")]
UPL_LABELS = ["1", "2", "3-5", "6-10", "11+"]


@dataclass
class OrderShape:
    profile: pd.DataFrame
    lines_per_order: pd.DataFrame   # lines_per_order, orders, share
    units_per_line: pd.DataFrame    # units_per_line, lines, share


def _dist(values: pd.Series, bins, labels, name: str, count: str) -> pd.DataFrame:
    cut = pd.cut(values, bins=bins, labels=labels, right=True)
    n = cut.value_counts().reindex(labels, fill_value=0)
    return pd.DataFrame({name: labels, count: n.to_numpy(), "share": (n / n.sum()).to_numpy() if n.sum() else 0.0})


def order_shape(tables: dict, line_definition: str = "row") -> OrderShape:
    orders = tables["orders"]
    lines = define_lines(orders, line_definition)
    per_order = lines.groupby("order_id").size()
    qty = pd.to_numeric(lines["qty"], errors="coerce").dropna()
    return OrderShape(order_profile(orders, line_definition),
                      _dist(per_order, LPO_BINS, LPO_LABELS, "lines_per_order", "orders"),
                      _dist(qty[qty > 0], UPL_BINS, UPL_LABELS, "units_per_line", "lines"))
