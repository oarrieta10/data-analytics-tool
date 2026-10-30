"""backfill — estimates for the item sizes quality/ cannot trust, each with where it came from and how far to
trust it.

    run_backfill(client, params=None) -> BackfillResult   reads tables.load(client) only; runs quality/ on it
    assess(tables, quality_result, bp, ip)               the same on tables in hand (pure)
    load_items(client) / is_skipped(client) / set_skipped(client, skipped)   what later modules size from; a client
                                        whose data needs no estimates can skip backfill, and they use it as sent
    write_outputs(result, client) -> data/workspace/<client>/_backfill/
        values.parquet                 one row per (sku, field) acted on: original, estimate, value used,
                                        action, method, confidence, similarity, neighbours
        item_master_backfilled.parquet  one row per SKU, every item field with <field>_method / _confidence
        backfill_summary.json           donors, actions, methods, readiness for unit fit before and after

Fills only what is missing, zero, a placeholder or copied (targets.py); a value quality doubts for any other
reason (a suspected unit slip included) is kept, with the estimate beside it and a review flag when they disagree by more than
review_factor.  Never changes a trusted value; estimates only active SKUs.  Not here: case pack and case sizes
(a later stage), measuring accuracy (validate.py), feeding later modules (they will read these outputs).
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
from gtpfit.ingest import IngestParams
from gtpfit.quality import QualityResult, assess as quality_assess, saved_params as quality_saved_params

from .estimate import ACTIONS, METHODS, estimate
from .params import BackfillParams
from .targets import DIMS, UNIT_FIELDS, plan
from .text import BACKFILL_TEXT

__all__ = ["BackfillParams", "BackfillResult", "assess", "run_backfill", "write_outputs", "saved_params", "load_items",
           "is_skipped", "set_skipped", "BACKFILL_TEXT", "METHODS", "ACTIONS"]

CASE_FIELDS = {"case", "case_l", "case_w", "case_h", "case_weight", "case_qty"}
_ESTIMATED = ("neighbours", "neighbours_volume", "case_of_one")  # methods specific enough to send a value to review
VALUE_COLUMNS = ["sku", "field", "original", "estimate", "value", "action", "method", "confidence", "similarity",
                 "neighbours", "codes", "lines"]


@dataclass
class BackfillResult:
    values: pd.DataFrame
    items: pd.DataFrame
    summary: dict
    donors: list = field(default_factory=list)
    params: BackfillParams = field(default_factory=BackfillParams)


def _assign(orig: np.ndarray, pred: np.ndarray, known: np.ndarray) -> np.ndarray:
    """The estimated sides (longest first) in the l / w / h order that best matches the sides already known."""
    if not known.any():
        return pred
    best, cost = pred, np.inf
    for p in itertools.permutations(range(3)):
        c = np.abs(np.log(orig[known]) - np.log(pred[list(p)][known])).sum()
        if c < cost:
            best, cost = pred[list(p)], c
    return best


def _shares(status: pd.DataFrame, ready: set, has_inventory: bool) -> dict:
    act = status[status["bucket"] == "active"].drop_duplicates("sku")
    ok = act["sku"].isin(ready)
    out = {}
    for basis in ("lines", "units", "on_hand", "skus"):
        if basis == "on_hand" and not has_inventory:
            continue
        w = pd.Series(1.0, index=act.index) if basis == "skus" else act[basis].astype(float)
        total = float(w.sum())
        out[basis] = {"share": float(w[ok].sum() / total) if total else 0.0, "ready": float(w[ok].sum()),
                      "total": total}
    return out


def assess(tbls: dict, quality: QualityResult, bp: BackfillParams | None = None,
           ip: IngestParams | None = None, validate: bool = False) -> BackfillResult:
    """validate: also run the holdout accuracy test (validate.holdout) and put it in the summary."""
    bp, ip = bp or BackfillParams(), ip or IngestParams()
    first = tbls["item_master"].drop_duplicates("sku").reset_index(drop=True)
    flags, status = quality.flags, quality.status
    targets, donors = plan(status, flags, first, bp.max_donors)

    case_doubt = set(flags.loc[flags["field"].isin(CASE_FIELDS) & (flags["severity"] == "unusable"), "sku"])
    first = first.assign(_case_doubt=first["sku"].isin(case_doubt))
    by_sku = first.set_index("sku")
    peers = status.drop_duplicates("sku").set_index("sku")["peer_group"]

    q = by_sku.loc[targets["sku"]].reset_index()
    trusted = q[["sku"] + UNIT_FIELDS].copy()
    for i, t in enumerate(targets.itertuples(index=False)):
        for f in UNIT_FIELDS:
            if t.check or f in t.fill:
                trusted.at[i, f] = np.nan
    est = estimate(q, by_sku.loc[donors].reset_index(), peers, bp, trusted)

    rows = []
    for i, t in enumerate(targets.itertuples(index=False)):
        e = est.iloc[i]
        orig = np.array([pd.to_numeric(q.at[i, f], errors="coerce") for f in DIMS], float)
        known = (orig > 0) & np.array([f not in t.fill for f in DIMS])
        pred = np.array([e.est_long, e.est_mid, e.est_short], float)
        sides = _assign(orig, pred, known) if not np.isnan(pred).any() else pred
        est_of = dict(zip(DIMS, sides)) | {"unit_weight": e.est_weight}
        orig_of = dict(zip(DIMS, orig)) | {"unit_weight": pd.to_numeric(q.at[i, "unit_weight"], errors="coerce")}

        review = False
        if t.check and e.method in _ESTIMATED:
            ratios = []
            if known.all():
                ratios.append(abs(np.log(orig.prod() / pred.prod())))
            ow = orig_of["unit_weight"]
            if "unit_weight" not in t.fill and ow > 0 and e.est_weight > 0:
                ratios.append(abs(np.log(ow / e.est_weight)))
            review = bool(ratios) and float(np.exp(max(ratios))) > bp.review_factor

        for f in UNIT_FIELDS:
            base = dict(sku=t.sku, field=f, original=orig_of[f], estimate=est_of[f], similarity=e.similarity,
                        neighbours=e.neighbours, codes=t.codes)
            if f in t.fill:
                done = e.method != "none" and pd.notna(est_of[f])
                rows.append(base | dict(value=est_of[f] if done else np.nan,
                                        action="filled" if done else "not_estimated",
                                        method=e.method, confidence=e.confidence if done else ""))
            elif t.check:
                rows.append(base | dict(value=orig_of[f], action="kept_review" if review else "kept",
                                        method="client", confidence=""))
    values = pd.DataFrame(rows, columns=VALUE_COLUMNS)
    values["lines"] = values["sku"].map(status.drop_duplicates("sku").set_index("sku")["lines"]).fillna(0.0)

    items = first.drop(columns="_case_doubt").copy()
    for f in UNIT_FIELDS:
        items[f + "_method"] = "client"
        items[f + "_confidence"] = ""
    filled = values[values["action"] == "filled"]
    if len(filled):
        pos = items.reset_index().set_index("sku")["index"]
        for f, g in filled.groupby("field"):
            ix = pos.loc[g["sku"]].to_numpy()
            items.loc[ix, f] = g["value"].to_numpy()
            items.loc[ix, f + "_method"] = g["method"].to_numpy()
            items.loc[ix, f + "_confidence"] = g["confidence"].to_numpy()

    # -- readiness for unit fit: before, and with the estimates (a kept doubt still blocks)
    st = status.drop_duplicates("sku")
    before = set(st.loc[st["ready_unit_fit"], "sku"])
    unfilled = set(values.loc[values["action"] == "not_estimated", "sku"])
    resolved = set(targets.loc[~targets["check"], "sku"]) - unfilled
    after = before | resolved
    has_inv = tbls.get("inventory") is not None and len(tbls["inventory"]) > 0
    per_sku = values.groupby("sku")["action"].agg(set) if len(values) else pd.Series(dtype=object)
    est_by_sku = filled.drop_duplicates("sku")
    summary = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "donors": len(donors),
        "targets": int(len(targets)),
        "actions": {a: int(sum(1 for s in per_sku if a in s)) for a in ACTIONS},
        "methods": {k: int(v) for k, v in est_by_sku["method"].value_counts().items()},
        "confidence": {k: int(v) for k, v in est_by_sku["confidence"].value_counts().items()},
        "ready_before": _shares(status, before, has_inv),
        "ready_after": _shares(status, after, has_inv),
        # what still keeps lines from unit fit, as shares of in-scope lines
        "gap_after": {
            "orphans": _shares(status, set(st.loc[st["status"] == "orphan", "sku"]), False)["lines"]["share"],
            "kept_doubts": _shares(status, set(targets.loc[targets["check"], "sku"]) - before, False)["lines"]["share"],
            "not_estimated": _shares(status, unfilled - before, False)["lines"]["share"]},
        "params": {k: (list(v) if isinstance(v, tuple) else v) for k, v in dataclasses.asdict(bp).items()},
    }
    if validate:
        from .validate import holdout
        summary["holdout"] = holdout(tbls, quality, bp, ip).summary
    return BackfillResult(values, items, summary, donors, bp)


def saved_params(client: str) -> BackfillParams:
    """The client's backfill settings (backfill: in client.yaml), defaults for the rest."""
    saved = workspace.read_client_yaml(client).get("backfill") or {}
    names = {f.name for f in dataclasses.fields(BackfillParams)}
    return BackfillParams(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in saved.items() if k in names})


def is_skipped(client: str) -> bool:
    """Whether the consultant chose to skip backfill for this client (backfill_skipped: in client.yaml)."""
    return bool(workspace.read_client_yaml(client).get("backfill_skipped"))


def set_skipped(client: str, skipped: bool) -> None:
    """Skip backfill for this client, or stop skipping it.  Earlier results stay in _backfill/ and are used again
    when skipping stops."""
    workspace.write_client_yaml(client, backfill_skipped=bool(skipped))


def load_items(client: str) -> pd.DataFrame:
    """The item master later modules size from: backfill's output (estimated values filled, each with its method)
    when backfill has run for the client and is not skipped, else the item master as ingested (tables.load)."""
    f = workspace.client_dir(client) / "_backfill" / "item_master_backfilled.parquet"
    if f.exists() and not is_skipped(client):
        return pd.read_parquet(f)
    return tables.load(client)["item_master"]


def run_backfill(client: str, params: BackfillParams | None = None, validate: bool = True) -> BackfillResult:
    """Backfill for a client's in-scope data, after quality/ with the client's saved quality settings; with the
    holdout accuracy test unless validate=False."""
    tbls = tables.load(client)
    ip = workspace.client_params(client)
    q = quality_assess(tbls, quality_saved_params(client), ip, workspace.line_definition(client))
    return assess(tbls, q, params or saved_params(client), ip, validate)


def write_outputs(result: BackfillResult, client: str):
    out = workspace.client_dir(client) / "_backfill"
    out.mkdir(parents=True, exist_ok=True)
    result.values.to_parquet(out / "values.parquet", index=False)
    items = result.items.copy()
    if "description" in items:
        items["description"] = items["description"].astype("string")
    items.to_parquet(out / "item_master_backfilled.parquet", index=False)
    (out / "backfill_summary.json").write_text(json.dumps(result.summary, indent=2), encoding="utf-8")
    return out
