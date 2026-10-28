"""profile — what the operation looks like, and the daily capacity it must be designed for.

Stage 1 (here): the working calendar and daily demand per channel (demand.py), the first-in-first-out backlog
simulation by age (backlog.py), and the design capacity from service targets per channel, or the percentile
day when none are set (design.py).  Later: volumes and seasonality, order profile, hourly profile, ABC velocity,
stock and inbound.

Reads tables.load(client) only.  Not here: container fit (fit/), workstations and footprint (sizing/).
"""
from __future__ import annotations

from .backlog import Simulation, percent_rank, simulate
from .demand import Calendar, calendar, daily_demand
from .design import Design, describe, solve, what_if
from .params import DEMAND_UNITS, ProfileParams

__all__ = ["ProfileParams", "DEMAND_UNITS", "Calendar", "calendar", "daily_demand", "Simulation", "simulate",
           "percent_rank", "Design", "solve", "what_if", "describe"]
