"""velocity.py — ABC classes: which SKUs carry the picking work.

    abc(tables, items, line_definition, p) -> ABC(skus, summary)

SKUs are ranked by order lines.  Class A runs until the cumulative share of lines reaches p.abc_cuts[0] (the
busiest SKU is always A), B until p.abc_cuts[1], C is the rest; SKUs in stock that never moved are
"D (no movement)".  Per class: SKUs, lines, units, cube moved (units x unit cube) and cube on hand, from items
(the backfilled item master when there is one, so estimated sizes count too).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from gtpfit.lines import define_lines

from .params import ProfileParams

IN3_PER_FT3 = 1728
NO_MOVE = "D (no movement)"
CLASSES = ["A", "B", "C", NO_MOVE]


@dataclass
class ABC:
    skus: pd.DataFrame      # sku, lines, units, rank, cum_share, abc, unit_cube_ft3, cube_moved_ft3, on_hand
    summary: pd.DataFrame   # abc, skus, sku_share, lines, lines_share, units, units_share, cube_moved_ft3, ...


def unit_cube_ft3(items: pd.DataFrame) -> pd.Series:
    """sku -> one unit's cube in ft3 (NaN when a size is missing)."""
    if items is None or not len(items):
        return pd.Series(dtype=float)
    it = items.drop_duplicates("sku").set_index("sku")
    dims = [pd.to_numeric(it[c], errors="coerce") for c in ("unit_l", "unit_w", "unit_h") if c in it]
    if len(dims) < 3:
        return pd.Series(np.nan, index=it.index)
    return (dims[0] * dims[1] * dims[2] / IN3_PER_FT3).where(lambda v: v > 0)


def average_on_hand(inventory: pd.DataFrame | None) -> pd.Series:
    """sku -> on-hand averaged over snapshots (a SKU missing from a snapshot counts as 0 there)."""
    if inventory is None or not len(inventory):
        return pd.Series(dtype=float)
    oh = pd.to_numeric(inventory["on_hand"], errors="coerce").fillna(0.0)
    n = inventory["snapshot_date"].nunique() if "snapshot_date" in inventory and \
        inventory["snapshot_date"].notna().any() else 1
    return oh.groupby(inventory["sku"]).sum() / max(n, 1)


def abc(tables: dict, items: pd.DataFrame | None, line_definition: str = "row",
        p: ProfileParams | None = None) -> ABC:
    p = p or ProfileParams()
    a_cut, b_cut = p.abc_cuts
    lines = define_lines(tables["orders"], line_definition)
    qty = pd.to_numeric(lines["qty"], errors="coerce").fillna(0.0)
    g = lines.assign(_q=qty).groupby("sku")
    s = pd.DataFrame({"lines": g.size(), "units": g["_q"].sum()}).sort_values(["lines", "units"], ascending=False,
                                                                              kind="stable")
    total = float(s["lines"].sum()) or 1.0
    s["rank"] = np.arange(1, len(s) + 1)
    s["cum_share"] = s["lines"].cumsum() / total
    s["abc"] = np.where((s["cum_share"] <= a_cut + 1e-12) | (s["rank"] == 1), "A",
                        np.where(s["cum_share"] <= b_cut + 1e-12, "B", "C"))
    onh = average_on_hand(tables.get("inventory"))
    idle = onh[(onh > 0) & ~onh.index.isin(s.index)]
    if len(idle):
        s = pd.concat([s, pd.DataFrame({"lines": 0, "units": 0.0, "rank": np.nan, "cum_share": 1.0,
                                        "abc": NO_MOVE}, index=idle.index)])
    s.index.name = "sku"
    cube = unit_cube_ft3(items).reindex(s.index)
    s["unit_cube_ft3"] = cube
    s["cube_moved_ft3"] = s["units"] * cube
    s["on_hand"] = onh.reindex(s.index).fillna(0.0)
    s["cube_on_hand_ft3"] = s["on_hand"] * cube
    s = s.reset_index()

    g = s.groupby("abc")
    summ = pd.DataFrame({"skus": g.size(), "lines": g["lines"].sum(), "units": g["units"].sum(),
                         "cube_moved_ft3": g["cube_moved_ft3"].sum(min_count=1),
                         "on_hand": g["on_hand"].sum(), "cube_on_hand_ft3": g["cube_on_hand_ft3"].sum(min_count=1)})
    summ = summ.reindex([c for c in CLASSES if c in summ.index])
    for k in ("skus", "lines", "units"):
        summ[f"{k}_share"] = summ[k] / summ[k].sum() if summ[k].sum() else 0.0
    summ["sized_share"] = g["unit_cube_ft3"].apply(lambda c: c.notna().mean()).reindex(summ.index)
    return ABC(s, summ.rename_axis("abc").reset_index())
