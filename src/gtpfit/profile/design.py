"""design.py — the design capacity: the smallest daily capacity whose backlog still meets the service targets.

    solve(demand, open, sla, p) -> Design(capacity, method, binding, kpis, simulation)
    what_if(demand, open, capacity, p) -> Simulation      the same model for a capacity typed by hand
    describe(sla) -> ["Ecomm: 95% same day", ...]

sla, per channel ("All" = all demand together):
    {"Ecomm": {"within": [(0, 0.95)]},                   95% done the same day
     "Retail": {"within": [(3, 1.0)], "max_backlog_days": 2}}
"within (n, share)": at least share of the channel's demand done within n working days of arriving (0 = same
day); "max_backlog_days": a day's workload never exceeds that many days of capacity.  Service only improves as
capacity grows, so the smallest capacity meeting every target is found by bisection on whole units, and the
targets that fail one unit below it are named as binding.  With no targets the design capacity is the
p.fallback_percentile day (days with demand), marked as a fallback.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .backlog import Simulation, simulate
from .params import ProfileParams


@dataclass
class Design:
    capacity: int
    method: str                       # "sla" | "fallback_percentile"
    binding: list = field(default_factory=list)
    kpis: dict = field(default_factory=dict)
    simulation: Simulation | None = None


def _within_text(days: int, share: float) -> str:
    when = "same day" if days == 0 else f"within {days} day" + ("s" if days != 1 else "")
    return f"{share * 100:g}% {when}"


def describe(sla: dict) -> list[str]:
    out = []
    for ch, t in (sla or {}).items():
        out += [f"{ch}: {_within_text(int(d), float(s))}" for d, s in t.get("within", [])]
        if t.get("max_backlog_days") is not None:
            out.append(f"{ch}: backlog at most {float(t['max_backlog_days']):g} days")
    return out


def _check(sla: dict) -> None:
    for ch, t in (sla or {}).items():
        for d, s in t.get("within", []):
            if not 0 < float(s) <= 1:
                raise ValueError(f"{ch}: a target share must be above 0 and at most 100% (got {s}).")
            if int(d) < 0:
                raise ValueError(f"{ch}: 'within' days cannot be negative (got {d}).")
        if t.get("max_backlog_days") is not None and float(t["max_backlog_days"]) <= 0:
            raise ValueError(f"{ch}: the backlog limit must be above 0 days.")


def failures(sim: Simulation, sla: dict) -> list[str]:
    """The targets this simulation misses, in describe()'s words."""
    k = sim.kpis
    out = []
    for ch, t in (sla or {}).items():
        svc = k["service"].get(ch)
        for d, s in t.get("within", []):
            if svc is None:
                continue
            cum = svc["within"][min(int(d), len(svc["within"]) - 1)]
            if cum + 1e-9 < float(s):
                out.append(f"{ch}: {_within_text(int(d), float(s))}")
        if t.get("max_backlog_days") is not None and k["max_days_to_clear"] > float(t["max_backlog_days"]) + 1e-9:
            out.append(f"{ch}: backlog at most {float(t['max_backlog_days']):g} days")
    return out


def what_if(demand: pd.DataFrame, open_: pd.Series, capacity: float, p: ProfileParams | None = None) -> Simulation:
    p = p or ProfileParams()
    return simulate(demand, capacity, open_, p.max_age_days)


def solve(demand: pd.DataFrame, open_: pd.Series, sla: dict, p: ProfileParams | None = None) -> Design:
    p = p or ProfileParams()
    _check(sla)
    total = demand.sum(axis=1)
    with_demand = total[total > 0]
    if not describe(sla):
        cap = math.ceil(float(np.quantile(with_demand, p.fallback_percentile))) if len(with_demand) else 0
        sim = what_if(demand, open_, cap, p)
        return Design(cap, "fallback_percentile", [], sim.kpis, sim)
    lo, hi = 0, max(1, math.ceil(float(total.max())))  # at the busiest day's demand nothing ever waits
    while lo < hi:
        mid = (lo + hi) // 2
        if mid > 0 and not failures(what_if(demand, open_, mid, p), sla):
            hi = mid
        else:
            lo = mid + 1
    sim = what_if(demand, open_, lo, p)
    binding = failures(what_if(demand, open_, lo - 1, p), sla) if lo > 1 else []
    return Design(lo, "sla", binding, sim.kpis, sim)
