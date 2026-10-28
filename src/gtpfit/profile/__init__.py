"""profile — what the operation looks like, and the daily capacity it must be designed for.

    run_profile(client, params=None, sla=None, inbound_sla=None) -> ProfileResult   reads tables.load(client)
    write_outputs(result, client) -> data/workspace/<client>/_profile/
        daily.parquet          per working day: demand by channel and the backlog simulation at the design capacity
        abc.parquet            per SKU: lines, units, ABC class, cube moved, on-hand, days on hand
        profile_summary.json   design capacity and KPIs, volumes, order shape, hourly, ABC, stock, inbound
    load_sla / save_sla(client) the service targets per channel, saved under profile: sla: in client.yaml

Parts: the working calendar and daily demand (demand.py), the first-in-first-out backlog by age (backlog.py),
the design capacity from service targets or the percentile day (design.py), volumes and seasonality
(volumes.py), order shape (orders.py), hourly profile (hourly.py), ABC velocity (velocity.py), days on hand and
turns (stock.py), and receiving (inbound.py).  Item sizes come from the backfilled item master when backfill has
run (backfill.load_items).  Not here: container fit (fit/), workstations and footprint (sizing/).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
from dataclasses import dataclass

import pandas as pd

from gtpfit import tables, workspace

from .backlog import Simulation, percent_rank, simulate
from .demand import Calendar, calendar, daily_demand
from .design import Design, describe, solve, what_if
from .hourly import Hourly, hourly
from .inbound import Inbound, inbound
from .orders import OrderShape, order_shape
from .params import DEMAND_UNITS, ProfileParams
from .stock import Stock, stock
from .velocity import ABC, abc
from .volumes import Volumes, volumes

__all__ = ["ProfileParams", "DEMAND_UNITS", "Calendar", "calendar", "daily_demand", "Simulation", "simulate",
           "percent_rank", "Design", "solve", "what_if", "describe", "ProfileResult", "run_profile", "assess",
           "write_outputs", "load_sla", "save_sla", "saved_params"]


@dataclass
class ProfileResult:
    demand: pd.DataFrame
    open: pd.Series
    design: Design
    volumes: Volumes
    orders: OrderShape
    hourly: Hourly
    abc: ABC
    stock: Stock
    inbound: Inbound
    summary: dict
    params: ProfileParams


def _plain(v):
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if hasattr(v, "item"):
        return v.item()
    return v


def assess(tbls: dict, items: pd.DataFrame | None, sla: dict | None = None, p: ProfileParams | None = None,
           line_definition: str = "row", inbound_sla: dict | None = None) -> ProfileResult:
    """The whole profile on tables in hand (pure)."""
    p = p or ProfileParams()
    dem, open_ = daily_demand(tbls, line_definition, p)
    design = solve(dem, open_, sla or {}, p)
    vol = volumes(tbls, line_definition, p)
    shape = order_shape(tbls, line_definition)
    hrs = hourly(tbls, line_definition, design.capacity)
    vel = abc(tbls, items, line_definition, p)
    stk = stock(tbls, items, line_definition, p)
    inb = inbound(tbls, p, inbound_sla)
    k = design.kpis
    summary = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "line_definition": line_definition, "demand_unit": p.demand_unit,
        "design": {"capacity": design.capacity, "method": design.method, "binding": design.binding,
                   "sla": describe(sla or {}), "kpis": k},
        "calendar": {"working_days": int(len(dem)), "closed_days": int((~open_).sum()),
                     "channels": list(dem.columns)},
        "volumes": vol.summary,
        "orders": {"profile": shape.profile.to_dict("records"),
                   "lines_per_order": shape.lines_per_order.to_dict("records"),
                   "units_per_line": shape.units_per_line.to_dict("records")},
        "hourly": hrs.summary if hrs.available else {"available": False, "note": hrs.note},
        "abc": vel.summary.to_dict("records"),
        "stock": stk.summary,
        "inbound": inb.summary,
        "params": dataclasses.asdict(p),
    }
    return ProfileResult(dem, open_, design, vol, shape, hrs, vel, stk, inb, _plain(summary), p)


def saved_params(client: str) -> ProfileParams:
    """The client's profile settings (profile: in client.yaml, except sla), defaults for the rest."""
    saved = workspace.read_client_yaml(client).get("profile") or {}
    names = {f.name for f in dataclasses.fields(ProfileParams)}
    return ProfileParams(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in saved.items() if k in names})


def load_sla(client: str, key: str = "sla") -> dict:
    """The client's service targets: {channel: {"within": [(days, share)], "max_backlog_days": n}}."""
    raw = (workspace.read_client_yaml(client).get("profile") or {}).get(key) or {}
    out = {}
    for ch, t in raw.items():
        out[str(ch)] = {"within": [(int(d), float(s)) for d, s in t.get("within", [])]}
        if t.get("max_backlog_days") is not None:
            out[str(ch)]["max_backlog_days"] = float(t["max_backlog_days"])
    return out


def save_sla(client: str, sla: dict, key: str = "sla") -> None:
    prof = dict(workspace.read_client_yaml(client).get("profile") or {})
    prof[key] = {ch: ({"within": [[int(d), float(s)] for d, s in t.get("within", [])]} |
                      ({"max_backlog_days": float(t["max_backlog_days"])}
                       if t.get("max_backlog_days") is not None else {}))
                 for ch, t in (sla or {}).items()}
    workspace.write_client_yaml(client, profile=prof)


def run_profile(client: str, params: ProfileParams | None = None, sla: dict | None = None,
                inbound_sla: dict | None = None) -> ProfileResult:
    """The profile of a client's in-scope data, with its saved targets unless others are given."""
    from gtpfit.backfill import load_items
    tbls = tables.load(client)
    return assess(tbls, load_items(client), load_sla(client) if sla is None else sla,
                  params or saved_params(client), workspace.line_definition(client),
                  load_sla(client, "inbound_sla") if inbound_sla is None else inbound_sla)


def write_outputs(result: ProfileResult, client: str):
    out = workspace.client_dir(client) / "_profile"
    out.mkdir(parents=True, exist_ok=True)
    daily = result.design.simulation.daily.copy() if result.design.simulation is not None else pd.DataFrame()
    daily.rename_axis("date").reset_index().to_parquet(out / "daily.parquet", index=False)
    skus = result.abc.skus.merge(result.stock.skus[["sku", "days_on_hand", "turns"]], on="sku", how="left") \
        if len(result.stock.skus) else result.abc.skus
    skus.to_parquet(out / "abc.parquet", index=False)
    (out / "profile_summary.json").write_text(json.dumps(result.summary, indent=2, default=str), encoding="utf-8")
    return out
