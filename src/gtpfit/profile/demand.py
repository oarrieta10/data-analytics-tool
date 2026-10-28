"""demand.py — the site's working calendar and the work that arrives on each working day, per channel.

    calendar(order_dates, p) -> Calendar(open_weekdays, days, closed)
    daily_demand(tables, line_definition, p) -> (demand: days x channels, open: days -> bool)

A weekday is a working day when it has orders in at least p.open_weekday_share of the weeks covered.  Every
working weekday between the first and last order is a day of the model; one with no orders is closed (no
capacity unless p.closed_days_have_capacity).  Orders on a non-working day are counted on the next open day
(work waiting for the site to open) or, with p.off_day_orders_to, the previous one (work done in overtime).  Demand is in p.demand_unit: order lines (under the
client's line definition), units, or orders.  Channels come from orders.channel ("All" when there is none).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from gtpfit.lines import define_lines

from .params import ProfileParams


@dataclass
class Calendar:
    open_weekdays: tuple       # 0 = Monday
    days: pd.DatetimeIndex     # every working weekday from the first to the last order
    closed: pd.DatetimeIndex   # working weekdays with no orders


def calendar(order_dates: pd.Series, p: ProfileParams | None = None) -> Calendar:
    p = p or ProfileParams()
    d = pd.to_datetime(order_dates).dt.normalize().dropna()
    if d.empty:
        return Calendar((), pd.DatetimeIndex([]), pd.DatetimeIndex([]))
    have = pd.DatetimeIndex(d.unique())
    weeks = max(1, pd.date_range(d.min() - pd.Timedelta(days=d.min().weekday()), d.max(), freq="W-MON").size)
    per_weekday = pd.Series(have.weekday).value_counts()
    open_wd = tuple(int(w) for w in sorted(per_weekday.index) if per_weekday[w] / weeks >= p.open_weekday_share)
    span = pd.date_range(d.min(), d.max(), freq="D")
    days = span[span.weekday.isin(open_wd)]
    return Calendar(open_wd, days, days.difference(have))


def daily_demand(tables: dict, line_definition: str = "row", p: ProfileParams | None = None):
    """demand (working days x channels, in p.demand_unit) and open (working day -> has capacity)."""
    p = p or ProfileParams()
    orders = tables["orders"]
    cal = calendar(orders["order_date"], p)
    open_ = pd.Series(True, index=cal.days)
    if not p.closed_days_have_capacity:
        open_[cal.closed] = False
    if cal.days.empty:
        return pd.DataFrame(index=cal.days), open_

    lines = define_lines(orders, line_definition)
    ch = lines["channel"].astype("string").fillna("Unknown") if "channel" in lines else \
        pd.Series("All", index=lines.index)
    date = pd.to_datetime(lines["order_date"]).dt.normalize()
    open_days = open_.index[open_.to_numpy()]
    if p.off_day_orders_to == "previous working day":
        pos = np.searchsorted(open_days.to_numpy(), date.to_numpy(), side="right") - 1  # the same or the previous
        pos = np.maximum(pos, 0)
    else:
        pos = np.searchsorted(open_days.to_numpy(), date.to_numpy())  # the same or the next open day
        pos = np.minimum(pos, len(open_days) - 1)
    day = pd.Series(open_days[pos], index=lines.index)

    if p.demand_unit == "units":
        val = pd.to_numeric(lines["qty"], errors="coerce").fillna(0.0)
        d = val.groupby([day, ch]).sum()
    elif p.demand_unit == "orders":
        d = lines.assign(_d=day, _c=ch).drop_duplicates(["order_id", "_c"]).groupby(["_d", "_c"]).size()
    else:
        d = pd.Series(1.0, index=lines.index).groupby([day, ch]).sum()
    demand = d.astype(float).unstack(fill_value=0.0).reindex(cal.days, fill_value=0.0)
    demand.columns = [str(c) for c in demand.columns]
    demand.index.name = "date"
    return demand, open_
