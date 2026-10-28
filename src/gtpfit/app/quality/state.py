"""state.py — what the Quality page remembers: which client, its settings, and the cached quality run.

Session keys start with "qly::" (settings widgets "qly::p::<field>").  A quality run is cached on everything
it depends on (the ingest outputs, the saved scope, the settings, the client's assortment and line
definition), and each fresh run writes data/workspace/<client>/_quality/ so the page and the files agree.
Quality settings are saved per client under quality: in client.yaml (only values that differ from defaults).
"""
from __future__ import annotations

import dataclasses
import json

import streamlit as st

from gtpfit import workspace
from gtpfit.quality import QualityParams, run_quality, saved_params as quality_saved_params, write_outputs
from gtpfit.scope import load_scope


def ss():
    return st.session_state


def ready_clients() -> list[str]:
    """Clients whose ingest has finished, newest first."""
    base = workspace.workspace()
    if not base.exists():
        return []
    done = [d for d in base.iterdir() if (d / "_ingest" / "item_master.parquet").exists()
            and (d / "_ingest" / "orders.parquet").exists()]
    return [d.name for d in sorted(done, key=lambda d: d.stat().st_mtime, reverse=True)]


def client() -> str:
    return ss().get("qly::client", "") or ""


def saved_params(name: str) -> QualityParams:
    return quality_saved_params(name)


def save_params(name: str, p: QualityParams) -> None:
    base = dataclasses.asdict(QualityParams())
    diff = {k: (list(v) if isinstance(v, tuple) else v) for k, v in dataclasses.asdict(p).items() if v != base[k]}
    workspace.write_client_yaml(name, quality=diff)


def _signature(name: str) -> str:
    d = workspace.ingest_dir(name)
    files = sorted((p.name, p.stat().st_mtime_ns) for p in d.glob("*.parquet")) if d.exists() else []
    spec = load_scope(name)
    return json.dumps([files, spec.to_dict() if spec else None, workspace.client_settings(name)], default=str)


@st.cache_data(show_spinner=False, max_entries=6)
def _run(name: str, sig: str, params_json: str):
    p = QualityParams(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in json.loads(params_json).items()})
    r = run_quality(name, p)
    write_outputs(r, name)
    return r


def result(name: str, p: QualityParams):
    """The quality run for this client and settings (cached; a fresh run also writes _quality/)."""
    return _run(name, _signature(name), json.dumps(dataclasses.asdict(p), default=list))
