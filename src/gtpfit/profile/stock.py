"""stock.py — how much is held, and for how long: days on hand and turns, per SKU and overall.

    stock(tables, items, line_definition, p) -> Stock(skus, summary)

    average on-hand   per SKU, averaged over the inventory snapshots
    units per day     units ordered / working days (the working calendar of demand.py; days with orders when
                      the history is too short to tell the working weekdays)
    days on hand      average on-hand / units per day (infinite for stock that never moved)
    turns             units ordered per year / average on-hand (the history scaled to 365 days)
Overall figures weight every SKU by its stock; cube on hand uses items (the backfilled item master when there
is one).  Without inventory, available is False.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from gtpfit.lines import define_lines

from .demand import calendar
from .params import ProfileParams
from .velocity import average_on_hand, unit_cube_ft3

DAYS_PER_YEAR = 365


@dataclass
class Stock:
    skus: pd.DataFrame = field(default_factory=pd.DataFrame)  # sku, avg_on_hand, units, units_per_day, days_on_hand, turns
    summary: dict = field(default_factory=dict)


def stock(tables: dict, items: pd.DataFrame | None, line_definition: str = "row",
          p: ProfileParams | None = None) -> Stock:
    p = p or ProfileParams()
    inv = tables.get("inventory")
    if inv is None or not len(inv):
        return Stock(summary={"available": False, "note": "No inventory snapshots, so no days on hand or turns."})
    orders = tables["orders"]
    dates = pd.to_datetime(orders["order_date"]).dt.normalize()
    cal = calendar(dates, p)
    working_days = len(cal.days) if len(cal.days) else int(dates.nunique())
    span = (dates.max() - dates.min()).days + 1 if len(dates) else 0
    lines = define_lines(orders, line_definition)
    units = pd.to_numeric(lines["qty"], errors="coerce").fillna(0.0).groupby(lines["sku"]).sum()

    onh = average_on_hand(inv)
    s = pd.DataFrame({"avg_on_hand": onh})
    s["units"] = units.reindex(s.index).fillna(0.0)
    s["units_per_day"] = s["units"] / working_days if working_days else 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        s["days_on_hand"] = np.where(s["units_per_day"] > 0, s["avg_on_hand"] / s["units_per_day"], np.inf)
        per_year = s["units"] * DAYS_PER_YEAR / span if span else 0.0
        s["turns"] = np.where(s["avg_on_hand"] > 0, per_year / s["avg_on_hand"], np.nan)
    cube = unit_cube_ft3(items).reindex(s.index)
    s["cube_on_hand_ft3"] = s["avg_on_hand"] * cube
    s.index.name = "sku"

    held = float(s["avg_on_hand"].sum())
    moving = s[s["units"] > 0]
    total_per_day = float(units.sum() / working_days) if working_days else 0.0
    summary = {"available": True, "working_days": int(working_days), "history_days": int(span),
               "snapshots": int(inv["snapshot_date"].nunique()) if "snapshot_date" in inv else 1,
               "skus_on_hand": int((s["avg_on_hand"] > 0).sum()), "avg_on_hand_units": held,
               "cube_on_hand_ft3": float(s["cube_on_hand_ft3"].sum(min_count=1)) if cube.notna().any() else None,
               "days_on_hand": held / total_per_day if total_per_day else None,
               "turns": float(units.sum() * DAYS_PER_YEAR / span / held) if span and held else None,
               "median_days_on_hand": float(moving["days_on_hand"].median()) if len(moving) else None,
               "no_movement_skus": int(((s["units"] == 0) & (s["avg_on_hand"] > 0)).sum()),
               "no_movement_share_of_stock": float(s.loc[s["units"] == 0, "avg_on_hand"].sum() / held) if held else 0.0}
    return Stock(s.reset_index(), summary)
