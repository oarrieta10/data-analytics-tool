"""inbound.py — what arrives: receipt lines and units per day, the busy days, and the receiving capacity.

    inbound(tables, p, sla=None) -> Inbound(daily, summary, design)

Receipt lines are rows of the receipts file, on the receipts' own working calendar (demand.calendar on the
receipt dates).  The receiving capacity uses the same first-in-first-out backlog model as outbound
(design.solve): with a receiving target, e.g. {"All": {"within": [(1, 1.0)]}} (everything put away within one
working day), the smallest capacity meeting it; without one, the p.fallback_percentile day.  Without receipts,
available is False.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .demand import calendar
from .design import Design, solve
from .params import ProfileParams
from .volumes import _stats


@dataclass
class Inbound:
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    summary: dict = field(default_factory=dict)
    design: Design | None = None


def inbound(tables: dict, p: ProfileParams | None = None, sla: dict | None = None) -> Inbound:
    p = p or ProfileParams()
    rec = tables.get("receipts")
    if rec is None or not len(rec):
        return Inbound(summary={"available": False, "note": "No receipts, so no inbound profile."})
    date = pd.to_datetime(rec["receipt_date"]).dt.normalize()
    qty = pd.to_numeric(rec["qty"], errors="coerce").fillna(0.0)
    g = rec.assign(_d=date, _q=qty).groupby("_d")
    daily = pd.DataFrame({"lines": g.size(), "units": g["_q"].sum()})
    if "receipt_id" in rec and rec["receipt_id"].notna().any():
        daily["receipts"] = g["receipt_id"].nunique()
    daily.index.name = "date"

    cal = calendar(date, p)
    days = cal.days if len(cal.days) else pd.DatetimeIndex(daily.index)
    dem = daily[["lines"]].rename(columns={"lines": "All"}).reindex(days).fillna(0.0)
    if len(cal.days):  # receipts on a non-working day count on the next working day
        extra = daily["lines"][~daily.index.isin(days)]
        for d, v in extra.items():
            nxt = days[days.searchsorted(d)] if days.searchsorted(d) < len(days) else days[-1]
            dem.loc[nxt, "All"] += v
    open_ = pd.Series(True, index=days)
    if len(cal.days) and not p.closed_days_have_capacity:
        open_[cal.closed] = False
    design = solve(dem, open_, sla or {}, p)
    summary = {"available": True, "days_with_receipts": int(len(daily)),
               "lines": _stats(daily["lines"], p.fallback_percentile),
               "units": _stats(daily["units"], p.fallback_percentile),
               "units_per_line": float(daily["units"].sum() / daily["lines"].sum()) if daily["lines"].sum() else 0.0,
               "design": {"capacity": design.capacity, "method": design.method, "binding": design.binding}}
    return Inbound(daily.reset_index(), summary, design)
