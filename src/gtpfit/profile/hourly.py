"""hourly.py — when in the day the work happens, from the order lines' time of day (orders.pick_secs).

    hourly(tables, line_definition, design_capacity=None) -> Hourly(available, by_hour, summary, note)

Share of lines per hour over the whole history, overall and per channel; the busiest hour's share; and, with a
design capacity per day, the design day's peak hour (capacity x that share), the hourly rate workstations must
reach.  Without a time of day in the data, available is False and note says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from gtpfit.lines import define_lines


@dataclass
class Hourly:
    available: bool
    by_hour: pd.DataFrame = field(default_factory=pd.DataFrame)   # hour, lines, share, share_<channel> ...
    summary: dict = field(default_factory=dict)
    note: str = ""


def hourly(tables: dict, line_definition: str = "row", design_capacity: float | None = None) -> Hourly:
    orders = tables["orders"]
    if "pick_secs" not in orders or pd.to_numeric(orders["pick_secs"], errors="coerce").notna().sum() == 0:
        return Hourly(False, note="No time of day in the order data, so there is no hourly profile. Map a pick "
                                  "time column on Ingest to get one.")
    lines = define_lines(orders, line_definition)
    secs = pd.to_numeric(lines["pick_secs"], errors="coerce")
    hour = (secs // 3600).dropna().astype(int)
    n = hour.value_counts().sort_index().reindex(range(24), fill_value=0)
    by = pd.DataFrame({"hour": n.index, "lines": n.to_numpy(), "share": (n / n.sum()).to_numpy()})
    if "channel" in lines:
        ch = lines.loc[hour.index, "channel"].astype("string").fillna("Unknown")
        for c, h in hour.groupby(ch):
            k = h.value_counts().reindex(range(24), fill_value=0)
            by[f"share_{c}"] = (k / k.sum()).to_numpy() if k.sum() else 0.0
    peak = int(by.loc[by["share"].idxmax(), "hour"])
    share = float(by["share"].max())
    summary = {"peak_hour": peak, "peak_hour_share": share, "lines_with_time": int(n.sum()),
               "share_with_time": float(n.sum() / len(lines)) if len(lines) else 0.0,
               "working_hours": [int(h) for h in by.loc[by["share"] >= 0.01, "hour"]],
               "design_peak_hour": float(design_capacity * share) if design_capacity else None}
    return Hourly(True, by, summary)
