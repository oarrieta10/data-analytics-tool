"""fit — how many units of each SKU fit each catalogue container (and each divider cell), and what limits it.

    run_fit(client, params=None, catalog=None) -> FitResult     reads tables.load and backfill.load_items
    assess(items, containers, fp, lines=None, abc=None)          the same on items in hand (pure)
    write_outputs(result, client) -> data/workspace/<client>/_fit/
        fit.parquet        one row per SKU x container x cell layout: units by tessellation, liquid cube and
                           weight, units used, the limit, and where the SKU's sizes came from
        fit_summary.json   per container and layout: share of SKUs and of lines that fit, limits, units per
                           container, fit by ABC class, how much rests on estimated sizes

Two modes, set per container in the catalogue: "units" packs loose units into the container and its divider
cells (cells share the container's weight limit equally), counted by tessellation or liquid cube
(FitParams.cubing_method) and capped by weight; "case_on_tray" checks that the SKU's case is within the tray's
carton range and weight limit, one case per tray.  Only SKUs active in scope (ordered or on hand) are fitted.
This is layer 1 of the design stage: computed once per data change; slotting and sizing read it.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import itertools
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from gtpfit import tables, workspace

from .containers import Container, Layout, load_catalog, save_container
from .params import CUBING_METHODS, FitParams
from .tessellate import units_by_liquid, units_by_tessellation, units_by_weight
from .text import FIT_TEXT

__all__ = ["FitParams", "CUBING_METHODS", "FitResult", "assess", "run_fit", "write_outputs", "saved_params",
           "load_catalog", "save_container", "Container", "Layout", "LIMITS", "FIT_TEXT"]

LIMITS = tuple(FIT_TEXT)
UNIT_DIMS = ("unit_l", "unit_w", "unit_h")
CASE_DIMS = ("case_l", "case_w", "case_h")
UNIT_FIELDS = UNIT_DIMS + ("unit_weight",)
_CONF_RANK = {"high": 0, "medium": 1, "low": 2}
UNIT_BINS = [0, 1, 5, 10, 50, 200, np.inf]
UNIT_BIN_LABELS = ["1", "2-5", "6-10", "11-50", "51-200", "200+"]


@dataclass
class FitResult:
    fit: pd.DataFrame
    summary: dict
    containers: list = field(default_factory=list)
    params: FitParams = field(default_factory=FitParams)


def _num(df: pd.DataFrame, c: str) -> np.ndarray:
    return pd.to_numeric(df[c], errors="coerce").to_numpy(float) if c in df else np.full(len(df), np.nan)


def _sizes(df: pd.DataFrame, cols, min_side: float) -> np.ndarray:
    a = np.column_stack([_num(df, c) for c in cols])
    return np.where(a >= max(min_side, 1e-12), a, np.nan)


def _provenance(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Per SKU: where its item sizes came from (the first estimated field, else "client") and the lowest
    confidence among them."""
    meth = np.array(["client"] * len(df), dtype=object)
    conf = np.array([""] * len(df), dtype=object)
    for f in UNIT_FIELDS:
        m = df.get(f + "_method")
        c = df.get(f + "_confidence")
        if m is None:
            continue
        m = m.fillna("client").astype(str).to_numpy()
        est = (meth == "client") & (m != "client")
        meth = np.where(est, m, meth)
        if c is not None:
            c = c.fillna("").astype(str).to_numpy()
            worse = np.array([_CONF_RANK.get(b, -1) > _CONF_RANK.get(a, -1) for a, b in zip(conf, c)])
            conf = np.where(worse, c, conf)
    return meth, conf


def _case_fits(case: np.ndarray, rng: tuple, upright: bool) -> np.ndarray:
    orients = ((0, 1, 2), (1, 0, 2)) if upright else tuple(itertools.permutations(range(3)))
    lo = np.array([r[0] for r in rng])
    hi = np.array([r[1] for r in rng])
    ok = np.zeros(len(case), bool)
    for o in orients:
        d = case[:, o]
        ok |= ((d >= lo - 1e-9) & (d <= hi + 1e-9)).all(axis=1)
    return ok


def _units_block(it, c: Container, lay: Layout, unit, weight, fp: FitParams) -> dict:
    l, w, h = c.inside_in
    cell = np.array([l / lay.l_split, w / lay.w_split, h])
    cap = c.payload_lb / lay.cells
    util = c.cell_utilisation if c.cell_utilisation else fp.cell_utilisation
    tess = units_by_tessellation(unit, cell, fp.keep_upright)
    liq = units_by_liquid(np.prod(unit, axis=1), float(cell.prod()), util)
    wt = units_by_weight(weight, cap)
    geom = tess if fp.cubing_method == "tessellation" else liq
    missing = ~np.isfinite(unit).all(axis=1) | ~(weight > 0)
    units = np.minimum(geom, wt).astype(float)
    units[missing] = np.nan
    limit = np.select([missing, geom == 0, wt == 0, wt < geom], ["missing_size", "too_big", "too_heavy", "weight"],
                      "size")
    return {"units_tessellation": np.where(missing, np.nan, tess), "units_liquid": np.where(missing, np.nan, liq),
            "units_weight": np.where(missing, np.nan, wt), "units": units, "limit": limit}


def _tray_block(it, c: Container, case, case_wt, qty, fp: FitParams) -> dict:
    missing = ~np.isfinite(case).all(axis=1) | ~(qty > 0) | ~(case_wt > 0)
    inside = _case_fits(np.nan_to_num(case), c.carton_range_in, fp.keep_upright)
    heavy = case_wt > c.payload_lb
    limit = np.select([missing, ~inside, heavy], ["missing_case", "case_outside_tray", "too_heavy"], "case_fits")
    units = np.where(limit == "case_fits", qty, 0.0).astype(float)
    units[missing] = np.nan
    nan = np.full(len(it), np.nan)
    return {"units_tessellation": nan, "units_liquid": nan, "units_weight": nan, "units": units, "limit": limit}


def _share(mask: pd.Series, weights: pd.Series) -> float:
    total = float(weights.sum())
    return float(weights[mask].sum() / total) if total else 0.0


def _summary(fit: pd.DataFrame, containers: list, lines: pd.Series | None, abc: pd.Series | None) -> dict:
    out = {}
    for c in containers:
        f = fit[fit["container"] == c.id]
        lay = {}
        for name, g in f.groupby("layout", sort=False):
            g = g.set_index("sku")
            w = lines.reindex(g.index).fillna(0.0) if lines is not None else pd.Series(1.0, index=g.index)
            ok = g["units"].fillna(0) >= 1
            lay[name] = {"skus": int(len(g)), "fits_skus": int(ok.sum()), "skus_share": float(ok.mean()) if len(g) else 0.0,
                         "lines_share": _share(ok, w)}
        whole = f[f["layout"] == "whole"].set_index("sku")
        fits = whole["units"].fillna(0) >= 1
        units = whole.loc[fits, "units"]
        dist = pd.cut(units, UNIT_BINS, labels=UNIT_BIN_LABELS).value_counts().reindex(UNIT_BIN_LABELS, fill_value=0)
        s = {"name": c.name, "system": c.system, "mode": c.mode, "source": c.source, "confidence": c.confidence,
             "placeholders": c.placeholders, "inside_in": list(c.inside_in), "payload_lb": c.payload_lb,
             "layouts": lay, "limits": {k: int(v) for k, v in whole["limit"].value_counts().items()},
             "units_per_container": {k: int(v) for k, v in dist.items()},
             "estimated_share_of_fits": float((whole.loc[fits, "size_method"] != "client").mean()) if fits.any() else 0.0}
        if abc is not None:
            cls = abc.reindex(whole.index).fillna("not ranked")
            s["by_abc"] = {str(k): float(v) for k, v in fits.groupby(cls).mean().items()}
        out[c.id] = s
    return out


def assess(items: pd.DataFrame, containers: list, fp: FitParams | None = None, lines: pd.Series | None = None,
           abc: pd.Series | None = None) -> FitResult:
    """items: one row per SKU to fit (sizes in inches / pounds, optional <field>_method / _confidence columns);
    lines: SKU -> in-scope order lines (weights for the shares); abc: SKU -> velocity class."""
    fp = fp or FitParams()
    it = items.drop_duplicates("sku").reset_index(drop=True)
    unit = _sizes(it, UNIT_DIMS, fp.min_side_in)
    weight = _num(it, "unit_weight")
    case = _sizes(it, CASE_DIMS, fp.min_side_in)
    case_wt, qty = _num(it, "case_weight"), _num(it, "case_qty")
    meth, conf = _provenance(it)
    blocks = []
    for c in containers:
        layouts = c.layouts if c.mode == "units" else [Layout("whole", 1, 1, 1)]
        for lay in layouts:
            cols = _units_block(it, c, lay, unit, weight, fp) if c.mode == "units" else \
                _tray_block(it, c, case, case_wt, qty, fp)
            blocks.append(pd.DataFrame({"sku": it["sku"].to_numpy(), "container": c.id, "system": c.system,
                                        "mode": c.mode, "layout": lay.name, "cells": lay.cells, **cols,
                                        "size_method": meth, "size_confidence": conf}))
    fit = pd.concat(blocks, ignore_index=True) if blocks else pd.DataFrame()
    if len(fit):
        fit["lines"] = fit["sku"].map(lines).fillna(0.0) if lines is not None else np.nan
        fit["abc"] = fit["sku"].map(abc) if abc is not None else None
    summary = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"), "skus": int(len(it)),
               "containers": _summary(fit, containers, lines, abc) if len(fit) else {},
               "params": dataclasses.asdict(fp)}
    return FitResult(fit, summary, list(containers), fp)


def saved_params(client: str) -> FitParams:
    saved = workspace.read_client_yaml(client).get("fit") or {}
    names = {f.name for f in dataclasses.fields(FitParams)}
    return FitParams(**{k: v for k, v in saved.items() if k in names})


def run_fit(client: str, params: FitParams | None = None, catalog=None) -> FitResult:
    """Fit every SKU active in scope (ordered or on hand) into every catalogue container."""
    from gtpfit.backfill import load_items
    from gtpfit.profile import saved_params as profile_params
    from gtpfit.profile.velocity import abc as abc_classes
    from gtpfit.quality.status import activity
    tbls = tables.load(client)
    line_def = workspace.line_definition(client)
    act = activity(tbls, line_def)
    on_hand = act["on_hand"] if "on_hand" in act else 0
    active = act[(act["lines"] > 0) | (on_hand > 0)]
    items = load_items(client)
    items = items[items["sku"].isin(set(active["sku"]))]
    lines = active.set_index("sku")["lines"]
    abc = abc_classes(tbls, items, line_def, profile_params(client)).skus.set_index("sku")["abc"]
    containers = catalog if catalog is not None else load_catalog()
    return assess(items, containers, params or saved_params(client), lines, abc)


def write_outputs(result: FitResult, client: str):
    out = workspace.client_dir(client) / "_fit"
    out.mkdir(parents=True, exist_ok=True)
    result.fit.to_parquet(out / "fit.parquet", index=False)
    (out / "fit_summary.json").write_text(json.dumps(result.summary, indent=2, default=str), encoding="utf-8")
    return out
