"""volumes.py — how much work there is, and when: per day, per month, per weekday, per channel.

    volumes(tables, line_definition, p) -> Volumes(daily, monthly, weekday, summary)

Counted on the dates in the order file (days with orders), before any working-calendar adjustment: the backlog
model (demand.py) decides on which working day off-day orders are worked.  Lines follow the client's line
definition; units are the summed quantity; orders are distinct order numbers.
    average day        mean over days with orders
    peak day           the busiest day, and the day at p.fallback_percentile
    peak month ratio   the busiest month's lines per day with orders, over the year's average day
"""
from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass

import numpy as np
import pandas as pd

from gtpfit.lines import define_lines

from .params import ProfileParams


@dataclass
class Volumes:
    daily: pd.DataFrame     # date, lines, units, orders, lines_<channel> ...
    monthly: pd.DataFrame   # month, days, lines, units, orders, lines_per_day, units_per_day, orders_per_day
    weekday: pd.DataFrame   # weekday, lines, share
    summary: dict


def _stats(s: pd.Series, pct: float) -> dict:
    return {"total": float(s.sum()), "average_day": float(s.mean()) if len(s) else 0.0,
            "percentile_day": float(np.quantile(s, pct)) if len(s) else 0.0, "peak_day": float(s.max()) if len(s) else 0.0}


def volumes(tables: dict, line_definition: str = "row", p: ProfileParams | None = None) -> Volumes:
    p = p or ProfileParams()
    lines = define_lines(tables["orders"], line_definition)
    date = pd.to_datetime(lines["order_date"]).dt.normalize()
    qty = pd.to_numeric(lines["qty"], errors="coerce").fillna(0.0)
    g = lines.assign(_date=date, _qty=qty).groupby("_date")
    daily = pd.DataFrame({"lines": g.size(), "units": g["_qty"].sum(), "orders": g["order_id"].nunique()})
    if "channel" in lines:
        ch = lines["channel"].astype("string").fillna("Unknown")
        by = pd.Series(1, index=lines.index).groupby([date, ch]).sum().unstack(fill_value=0)
        for c in by.columns:
            daily[f"lines_{c}"] = by[c]
    daily = daily.fillna(0)
    daily.index.name = "date"

    m = daily.groupby(daily.index.to_period("M"))
    monthly = pd.DataFrame({"days": m.size(), "lines": m["lines"].sum(), "units": m["units"].sum(),
                            "orders": m["orders"].sum()})
    for k in ("lines", "units", "orders"):
        monthly[f"{k}_per_day"] = monthly[k] / monthly["days"]
    monthly.index = monthly.index.astype(str)
    monthly = monthly.rename_axis("month").reset_index()

    wd = daily.groupby(daily.index.weekday)["lines"].sum()
    weekday = pd.DataFrame({"weekday": [_cal.day_name[i] for i in wd.index], "lines": wd.to_numpy()})
    weekday["share"] = weekday["lines"] / weekday["lines"].sum() if weekday["lines"].sum() else 0.0

    avg = float(daily["lines"].mean()) if len(daily) else 0.0
    busiest = monthly.loc[monthly["lines_per_day"].idxmax()] if len(monthly) else None
    summary = {k: _stats(daily[k], p.fallback_percentile) for k in ("lines", "units", "orders")}
    summary |= {"days_with_orders": int(len(daily)),
                "first": str(daily.index.min().date()) if len(daily) else None,
                "last": str(daily.index.max().date()) if len(daily) else None,
                "peak_day_date": str(daily["lines"].idxmax().date()) if len(daily) else None,
                "peak_month": str(busiest["month"]) if busiest is not None else None,
                "peak_month_ratio": float(busiest["lines_per_day"] / avg) if busiest is not None and avg else 0.0,
                "units_per_line": float(daily["units"].sum() / daily["lines"].sum()) if daily["lines"].sum() else 0.0,
                "channels": {c[6:]: float(daily[c].sum() / daily["lines"].sum()) for c in daily.columns
                             if c.startswith("lines_")} if daily["lines"].sum() else {}}
    return Volumes(daily.reset_index(), monthly, weekday, summary)
