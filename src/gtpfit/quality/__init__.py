"""quality — how much of the operation that matters is described by item data we can trust, and what exactly
is wrong with the rest.

    run_quality(client, params=None) -> QualityResult     reads tables.load(client) only (the in-scope data)
    assess(tables, qparams, iparams, line_definition)      the same on tables in hand (pure)
    write_outputs(result, client) -> data/workspace/<client>/_quality/
        sku_status.parquet, sku_flags.parquet, quality_summary.json, items_to_measure.csv

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

from .checks import CODE_SEVERITY, CODES, run_checks
from .params import QualityParams
from .peers import peer_groups
from .status import activity, items_to_measure, score, sku_status
from .text import QUALITY_TEXT

__all__ = ["QualityParams", "QualityResult", "assess", "run_quality", "write_outputs", "QUALITY_TEXT", "CODES"]


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
    active = set(status.loc[status["bucket"] == "active", "sku"])
    out = []
    for code in CODES:
        skus = flags.loc[flags["code"] == code, "sku"].unique()
        if not len(skus):
            continue
        out.append({"code": code, "title": QUALITY_TEXT[code].title, "severity": CODE_SEVERITY[code],
                    "skus": int(len(skus)), "skus_active": int(sum(1 for s in skus if s in active)),
                    "share_of_lines": float(lines.reindex(skus).fillna(0).sum() / total)})
    return out


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


def run_quality(client: str, params: QualityParams | None = None) -> QualityResult:
    """Quality for a client's in-scope data (tables.load), with its assortment bands and line definition."""
    return assess(tables.load(client), params, workspace.client_params(client), workspace.line_definition(client))


def write_outputs(result: QualityResult, client: str):
    out = workspace.client_dir(client) / "_quality"
    out.mkdir(parents=True, exist_ok=True)
    st = result.status.copy()
    st["description"] = st["description"].astype("string")
    st.to_parquet(out / "sku_status.parquet", index=False)
    result.flags.to_parquet(out / "sku_flags.parquet", index=False)
    (out / "quality_summary.json").write_text(json.dumps(result.summary, indent=2), encoding="utf-8")
    result.items.to_csv(out / "items_to_measure.csv", index=False, encoding="utf-8")
    return out
