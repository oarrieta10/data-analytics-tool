"""state.py — what the Scope page remembers: which client, its unscoped tables (cached), the spec being edited.

Session keys start with "scp::" (value checkboxes "scp::v::<column>::<value>", detection settings
"scpd::<field>").  The spec on screen is rebuilt from the widgets on every run; it is written to the
client's client.yaml only by "Save scope" (gtpfit.scope.save_scope).
"""
from __future__ import annotations

import json

import streamlit as st

from gtpfit import tables, workspace
from gtpfit.scope import ScopeParams, ScopeSpec, default_spec, detect_scope_columns, load_scope, params_from


def ss():
    return st.session_state


def ready_clients() -> list[str]:
    """Clients whose ingest has finished (outputs in the workspace), newest first."""
    base = workspace.workspace()
    if not base.exists():
        return []
    done = [d for d in base.iterdir() if (d / "_ingest" / "orders.parquet").exists()]
    return [d.name for d in sorted(done, key=lambda d: d.stat().st_mtime, reverse=True)]


def client() -> str:
    return ss().get("scp::client", "") or ""


def _signature(name: str) -> str:
    d = workspace.ingest_dir(name)
    return json.dumps(sorted((p.name, p.stat().st_mtime_ns) for p in d.glob("*.parquet"))) if d.exists() else ""


@st.cache_data(show_spinner="Loading the ingested tables…", max_entries=4)
def _base(name: str, sig: str) -> dict:
    return tables.load_unscoped(name)


def base_tables(name: str) -> dict:
    """Every row ingest kept, cached until ingest writes new outputs (filtering on top is cheap)."""
    return _base(name, _signature(name))


def params() -> ScopeParams:
    return ss().get("scp::params") or params_from((load_scope(client()) or ScopeSpec()).params)


def start_spec(name: str, orders) -> ScopeSpec:
    """The spec the page opens with: the client's saved one, else everything in except auto-excluded values."""
    key = f"scp::start::{name}"
    if key not in ss():
        ss()[key] = load_scope(name) or default_spec(detect_scope_columns(orders, params()), params())
    return ss()[key]


def forget_widgets(prefixes=("scp::v::", "scp::dates", "scp::basis", "scp::note", "scp::extra")) -> None:
    for k in [k for k in ss().keys() if str(k).startswith(prefixes)]:
        del ss()[k]
