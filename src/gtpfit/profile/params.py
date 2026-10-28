"""params.py — every setting profile/ uses, with its label, three-part tooltip, source and confidence (declared
with gtpfit.ingest.params.setting, so the workbench draws and explains them the same way).

The service targets (SLAs) are not here: they are structured, per channel, and saved under profile: sla: in the
client's client.yaml (see design.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from gtpfit.ingest.params import setting

_CONSULTANT = "Consultant judgement"
DEMAND_UNITS = ("lines", "units", "orders")
OFF_DAY_CHOICES = ("next working day", "previous working day")


@dataclass
class ProfileParams:
    # ---- client settings -----------------------------------------------------------------------------
    demand_unit: str = setting(
        "lines", block="client", unit="choice", options=DEMAND_UNITS, label="Demand measured in",
        what="What a day's work is counted in when sizing capacity and backlog: order lines (picks), units, or "
             "orders.",
        why="order lines drive picking work at a goods-to-person workstation",
        effect="Choose units when handling effort follows quantity (e.g. each-picking of many units per line); "
               "orders when packing or shipping is the constraint.",
        source="Industry practice for goods-to-person sizing", confidence="high")
    fallback_percentile: float = setting(
        0.95, block="client", unit="share", lo=0.5, hi=1.0, step=0.01, label="Without SLAs, size for the day at",
        what="When no service target is set, the design capacity is this percentile of daily demand (open days "
             "with demand).",
        why="the 95th-percentile day covers routine peaks without sizing for the single busiest outlier",
        effect="Raise it to absorb more peak days without backlog (a bigger system); lower it to accept more "
               "backlog on busy days.",
        source="Common sizing convention when service targets are unknown", confidence="medium")
    closed_days_have_capacity: bool = setting(
        False, block="client", unit="bool", label="Holidays work off backlog",
        what="An operating weekday with no orders (a holiday or shutdown) is closed: no work is processed and "
             "backlog waits for the next open day. Turn this on if the site works off backlog on such days.",
        why="a day without orders is usually a day the site is closed",
        effect="On: closed days process backlog at full capacity (less backlog, smaller system). Off: the "
               "conservative view.",
        source=_CONSULTANT, confidence="medium")

    off_day_orders_to: str = setting(
        "next working day", block="client", unit="choice", options=OFF_DAY_CHOICES,
        label="Orders on a non-working day count on",
        what="Where work dated on a non-working day (e.g. an occasional Saturday) is counted: on the next working "
             "day (work that waits for the site to open) or the previous one (work done in overtime alongside "
             "it).",
        why="an order date is usually when the work arrived, which waits for the next working day",
        effect="Choose the previous working day when the date is a pick or ship date and the site worked that "
               "day; it avoids a false Monday peak.",
        source=_CONSULTANT, confidence="medium")

    # ---- detection settings ------------------------------------------------------------------------------
    open_weekday_share: float = setting(
        0.5, block="detection", unit="share", lo=0.05, hi=1.0, step=0.05, label="Working weekday from",
        what="A weekday is a working day when it has orders in at least this share of the weeks covered; orders "
             "on other days (e.g. an occasional Saturday) are counted on the next working day.",
        why="a weekday worked in most weeks is part of the schedule; one worked now and then is overtime",
        effect="Raise it to treat only regular days as working days; lower it to include occasional ones.",
        source=_CONSULTANT, confidence="medium")
    max_age_days: int = setting(
        6, block="detection", unit="days", lo=1, hi=30, step=1, label="Track backlog age up to",
        what="Work is tracked by how many working days it has waited, up to this many; older work is kept "
             "together in a final 'or more' group.",
        why="service targets rarely go beyond a working week",
        effect="Raise it to see how old the oldest backlog gets; lower it for a shorter table.",
        source="Backlog-ageing practice", confidence="high")
