"""quality — how much of the operation that matters is described by item data we can trust, and what exactly
is wrong with the rest.

    run_quality(client, params=None) -> QualityResult     reads tables.load(client) only (the in-scope data)
    assess(tables, qparams, iparams, line_definition)      the same on tables in hand (pure)
    write_outputs(result, client, run_key=None) -> data/workspace/<client>/_quality/
        sku_status.parquet, sku_flags.parquet, quality_summary.json, items_to_measure.csv / .parquet, run_key.txt
    load_outputs(client, run_key, params)                  those results again, when run_key still matches

Reads and classifies; never changes or fills a value (backfill/ does that), never writes into the client's
folder.  Not here: order-level checks (ingest), container fit (fit/).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
from dataclasses import dataclass, field

import pandas as pd

from gtpfit import tables, workspace
from gtpfit.ingest import IngestParams

from .checks import CODE_SEVERITY, CODES, member, run_checks
from .params import QualityParams
from .peers import peer_groups
from .status import activity, items_to_measure, score, sku_status
from .text import QUALITY_TEXT

__all__ = ["QualityParams", "QualityResult", "assess", "run_quality", "write_outputs", "load_outputs",
           "saved_params", "QUALITY_TEXT", "CODES"]


RUN_KEY_FILE = "run_key.txt"


@dataclass
class QualityResult:
    flags: pd.DataFrame
    status: pd.DataFrame
    summary: dict
    items: pd.DataFrame
    params: QualityParams = field(default_factory=QualityParams)


def _problems(flags: pd.DataFrame, status: pd.DataFrame) -> list[dict]:
    """One row per code: SKUs, share of in-scope lines they carry, severity."""
    total = float(status.loc[status["bucket"] == "active", "lines"].sum()) or 1.0
    lines = status.drop_duplicates("sku").set_index("sku")["lines"]
    active = status.loc[status["bucket"] == "active", "sku"]
    seen = flags[["code", "sku"]].drop_duplicates()
    seen = seen.assign(active=member(seen["sku"], active), lines=seen["sku"].map(lines).fillna(0.0))
    by_code = seen.groupby("code", sort=False).agg(skus=("sku", "size"), active=("active", "sum"), lines=("lines", "sum"))
    return [{"code": code, "title": QUALITY_TEXT[code].title, "severity": CODE_SEVERITY[code],
             "skus": int(by_code.at[code, "skus"]), "skus_active": int(by_code.at[code, "active"]),
             "share_of_lines": float(by_code.at[code, "lines"] / total)} for code in CODES if code in by_code.index]


def assess(tbls: dict, qp: QualityParams | None = None, ip: IngestParams | None = None,
           line_definition: str = "row") -> QualityResult:
    qp, ip = qp or QualityParams(), ip or IngestParams()
    im, orders = tbls["item_master"], tbls.get("orders")
    flags = run_checks(im, orders, qp, ip)
    peers = peer_groups(im, qp.peer_min_size)
    weights = activity(tbls, line_definition)
    status = sku_status(im, flags, weights, peers)
    has_inv = tbls.get("inventory") is not None and len(tbls["inventory"]) > 0
    act = status[status["bucket"] == "active"]
    summary = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "line_definition": line_definition,
        "has_inventory": has_inv,
        "score": score(status, qp, has_inv),
        "problems": _problems(flags, status),
        "counts": {"skus_in_item_master": int(im["sku"].nunique()), "skus_active": int(len(act)),
                   "skus_inactive": int((status["bucket"] != "active").sum()),
                   "status_active": {k: int(v) for k, v in act["status"].value_counts().items()},
                   "peer_levels": {k: int(v) for k, v in peers["peer_level"].value_counts().items()}},
        "params": {k: (list(v) if isinstance(v, tuple) else v) for k, v in dataclasses.asdict(qp).items()},
    }
    return QualityResult(flags, status, summary, items_to_measure(status, flags), qp)


def saved_params(client: str) -> QualityParams:
    """The client's quality settings (quality: in client.yaml), defaults for the rest."""
    saved = workspace.read_client_yaml(client).get("quality") or {}
    names = {f.name for f in dataclasses.fields(QualityParams)}
    return QualityParams(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in saved.items() if k in names})


def run_quality(client: str, params: QualityParams | None = None) -> QualityResult:
    """Quality for a client's in-scope data (tables.load), with its assortment bands and line definition."""
    return assess(tables.load(client), params, workspace.client_params(client), workspace.line_definition(client))


def write_outputs(result: QualityResult, client: str, run_key: str | None = None):
    """run_key says what the run depended on: load_outputs gives the results back for the same key."""
    out = workspace.client_dir(client) / "_quality"
    out.mkdir(parents=True, exist_ok=True)
    (out / RUN_KEY_FILE).unlink(missing_ok=True)   # half-written outputs must never be reused
    st = result.status.copy()
    st["description"] = st["description"].astype("string")
    st.to_parquet(out / "sku_status.parquet", index=False)
    result.flags.to_parquet(out / "sku_flags.parquet", index=False)
    (out / "quality_summary.json").write_text(json.dumps(result.summary, indent=2), encoding="utf-8")
    result.items.to_csv(out / "items_to_measure.csv", index=False, encoding="utf-8")
    result.items.to_parquet(out / "items_to_measure.parquet", index=False)
    if run_key:
        (out / RUN_KEY_FILE).write_text(run_key, encoding="utf-8")
    return out


def load_outputs(client: str, run_key: str, params: QualityParams) -> QualityResult | None:
    """The saved results when they came from a run with this key, else None (never run, or anything changed)."""
    out = workspace.client_dir(client) / "_quality"
    key = out / RUN_KEY_FILE
    if not key.exists() or key.read_text(encoding="utf-8") != run_key:
        return None
    try:
        return QualityResult(pd.read_parquet(out / "sku_flags.parquet"), pd.read_parquet(out / "sku_status.parquet"),
                             json.loads((out / "quality_summary.json").read_text(encoding="utf-8")),
                             pd.read_parquet(out / "items_to_measure.parquet"), params)
    except (OSError, ValueError):   # a file went missing or was damaged: run again
        return None
