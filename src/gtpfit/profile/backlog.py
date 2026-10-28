"""backlog.py — how a day's work flows through a site with a fixed daily capacity: what is done the same day,
what waits, and for how long.

    simulate(demand, capacity, open, max_age) -> Simulation(daily, kpis, ...)
    percent_rank(values, x)                     where a capacity sits among the daily demands (0 to 1)

Each working day, new work joins the queue at age 0 and yesterday's leftovers age by one working day; the site
then works oldest first (first in, first out) until its capacity for the day is used; within one age, channels
are served in proportion to what they have waiting.  Work older than max_age stays together in a final
"max_age or more" group, so demand always equals work done plus the backlog left at the end.  A closed day
(open = False) does no work and ages nothing.

KPIs, overall and per channel:
    service by age     share of demand done the same day, one working day later, ... and cumulative
    days with backlog  working days that start with work carried over
    max days to clear  the most a day's workload (new work plus carried backlog) / capacity reaches
    avg fulfillment    average age at completion, the same day counting as day 1
    utilization        demand / (capacity x open days); design / average day; peak-day coverage (capacity /
                       busiest day); percentile rank of the capacity among days with demand
Pure: DataFrame and numbers in, results out.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def percent_rank(values, x: float) -> float:
    """Rank of x among values from 0 to 1, interpolating between neighbours (spreadsheet PERCENTRANK.INC)."""
    v = np.sort(np.asarray(values, float))
    n = len(v)
    if n < 2 or x >= v[-1]:
        return 1.0 if n and x >= v[-1] else 0.0
    if x <= v[0]:
        return 0.0
    i = int(np.searchsorted(v, x, side="right")) - 1
    below = int(np.searchsorted(v, v[i], side="left"))
    frac = (x - v[i]) / (v[i + 1] - v[i]) if v[i + 1] > v[i] else 0.0
    return float((below + frac * (np.searchsorted(v, v[i + 1], side="left") - below)) / (n - 1))


@dataclass
class Simulation:
    daily: pd.DataFrame          # per day: demand, open, capacity, start / processed / unprocessed by age, backlog
    processed: np.ndarray        # days x ages x channels
    channels: list
    capacity: float
    max_age: int
    kpis: dict

    def processed_by_age(self, channel: str = "All") -> np.ndarray:
        p = self.processed.sum(axis=0)
        return p.sum(axis=1) if channel == "All" else p[:, self.channels.index(channel)]


def _service(processed: np.ndarray, demand: float, within: list) -> dict:
    by_age = (processed / demand).tolist() if demand else [0.0] * len(processed)
    return {"demand": float(demand), "by_age": by_age, "cumulative": np.cumsum(by_age).tolist(), "within": within}


def _within(done_c: np.ndarray, demand_c: np.ndarray, open_ix: np.ndarray, is_open: np.ndarray) -> list:
    """Share of demand done within n working days (n = 0 .. ages-1), counting only work that arrived at least n
    working days before the history ends, so the end of the data is not held against a target.
    done_c: days x ages for one channel (or all), demand_c: per day."""
    A = done_c.shape[1]
    last = open_ix[is_open].max() if is_open.any() else -1
    arrival = open_ix[:, None] - np.arange(A)[None, :]          # open-day index each processed amount arrived on
    out = []
    for n in range(A):
        cutoff = last - n
        den = demand_c[(open_ix <= cutoff) & is_open].sum() + demand_c[~is_open & (open_ix <= cutoff)].sum()
        num = done_c[:, : n + 1][(arrival[:, : n + 1] <= cutoff)].sum()
        out.append(float(num / den) if den > 0 else 1.0)
    return out


def simulate(demand: pd.DataFrame, capacity, open_: pd.Series, max_age: int = 6) -> Simulation:
    """demand: working days x channels; capacity: a number or a per-day Series; open_: working day -> bool."""
    days = demand.index
    chans = [str(c) for c in demand.columns]
    D = demand.to_numpy(float)
    n, k, A = len(days), len(chans), max_age + 1
    cap = (capacity.reindex(days).to_numpy(float) if isinstance(capacity, pd.Series)
           else np.full(n, float(capacity)))
    is_open = open_.reindex(days).fillna(True).to_numpy(bool)
    cap = np.where(is_open, cap, 0.0)

    queue = np.zeros((A, k))
    start = np.zeros((n, A, k))
    done = np.zeros((n, A, k))
    carried = np.zeros(n)
    for t in range(n):
        carried[t] = queue.sum()
        if is_open[t]:
            aged = np.zeros((A, k))
            aged[1:] = queue[:-1]
            aged[-1] += queue[-1]                  # the oldest group keeps everything older
            aged[0] += D[t]
            queue = aged
        else:
            queue = queue.copy()
            queue[0] += D[t]                       # (a closed day has no orders; kept for safety)
        start[t] = queue
        left = cap[t]
        for a in range(A - 1, -1, -1):             # oldest first
            avail = queue[a].sum()
            if avail <= 0 or left <= 0:
                continue
            take = min(avail, left)
            done[t, a] = queue[a] * (take / avail)
            left -= take
        queue = queue - done[t]

    tot_demand = D.sum(axis=1)
    processed_day = done.sum(axis=(1, 2))
    workload = tot_demand + carried
    with np.errstate(divide="ignore", invalid="ignore"):
        to_clear = np.where(cap > 0, workload / np.where(cap > 0, cap, 1), np.nan)
    daily = pd.DataFrame({"demand": tot_demand, "open": is_open, "capacity": cap, "carried_in": carried,
                          "processed": processed_day, "backlog": start.sum(axis=(1, 2)) - processed_day,
                          "days_to_clear": to_clear}, index=days)
    for c, name in enumerate(chans):
        daily[f"demand_{name}"] = D[:, c]
    for a in range(A):
        daily[f"start_age{a}"] = start[:, a].sum(axis=1)
        daily[f"processed_age{a}"] = done[:, a].sum(axis=1)

    total = float(tot_demand.sum())
    proc_total = float(done.sum())
    ending = float(queue.sum())
    with_demand = tot_demand[tot_demand > 0]
    open_days = int(is_open.sum())
    cap_open = cap[is_open]
    cap_ref = float(np.median(cap_open)) if len(cap_open) else 0.0
    by_age = done.sum(axis=0)                      # ages x channels
    open_ix = np.cumsum(is_open) - 1               # each day's open-day index (a closed day shares the previous)
    within_all = _within(done.sum(axis=2), tot_demand, open_ix, is_open)
    within_ch = [_within(done[:, :, c], D[:, c], open_ix, is_open) for c in range(k)]
    kpis = {
        "capacity": cap_ref, "days_in_model": int(n), "open_days": open_days,
        "total_demand": total, "processed": proc_total, "ending_backlog": ending,
        "balanced": bool(abs(total - proc_total - ending) <= 1e-6 * max(total, 1.0)),
        "service": {"All": _service(by_age.sum(axis=1), total, within_all)} |
                   {ch: _service(by_age[:, c], float(D[:, c].sum()), within_ch[c]) for c, ch in enumerate(chans)},
        "days_with_backlog": int(((carried > 1e-9) & is_open).sum()),
        "max_days_to_clear": float(np.nanmax(to_clear)) if np.isfinite(to_clear).any() else 0.0,
        "avg_fulfillment_days": float((by_age.sum(axis=1) * np.arange(1, A + 1)).sum() / proc_total)
        if proc_total else 0.0,
        "utilization": total / (cap_ref * open_days) if cap_ref and open_days else 0.0,
        "average_day": float(with_demand.mean()) if len(with_demand) else 0.0,
        "peak_day": float(with_demand.max()) if len(with_demand) else 0.0,
    }
    kpis["design_to_average"] = cap_ref / kpis["average_day"] if kpis["average_day"] else 0.0
    kpis["peak_day_coverage"] = cap_ref / kpis["peak_day"] if kpis["peak_day"] else 0.0
    kpis["percentile_rank"] = percent_rank(with_demand, cap_ref) if len(with_demand) else 0.0
    return Simulation(daily, done, chans, cap_ref, max_age, kpis)
