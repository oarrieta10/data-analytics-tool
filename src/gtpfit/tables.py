"""tables.py — the one way analysis modules read a client's data.

    load(client, scoped=True) -> {table: DataFrame}      ingest outputs with the client's saved scope applied
    load_report(client) -> ScopeResult                   the same, plus the waterfall, warnings and summary
    load_unscoped(client) -> {table: DataFrame}          every row ingest kept (the Scope page starts here)

Every module from quality/ onward reads through load(), never from _ingest/
directly, so it only ever sees the in-scope data.  With no saved scope, load()
returns all rows.  Does not run ingest or change its outputs.
"""
from __future__ import annotations

import pandas as pd

from gtpfit.ingest.schema import TABLES
from gtpfit.scope import ScopeResult, apply_scope, load_scope, params_from
from gtpfit.workspace import ingest_dir


def load_unscoped(client: str) -> dict:
    d = ingest_dir(client)
    if not (d / "orders.parquet").exists():
        raise FileNotFoundError(f"No Ingest output for {client!r} in {d}: run Ingest for this client first.")
    return {t: pd.read_parquet(d / f"{t}.parquet") for t in TABLES if (d / f"{t}.parquet").exists()}


def load_report(client: str) -> ScopeResult:
    spec = load_scope(client)
    return apply_scope(load_unscoped(client), spec, params_from(spec.params if spec else None))


def load(client: str, scoped: bool = True) -> dict:
    return load_report(client).tables if scoped else load_unscoped(client)
