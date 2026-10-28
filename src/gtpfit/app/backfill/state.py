"""state.py — what the Backfill page remembers: which client, its settings, and the cached backfill run.

Session keys start with "bf::" (settings widgets "bf::p::<field>").  A backfill run is cached on everything it
depends on (what the quality run depends on, plus the client's quality settings and the backfill settings), and
each fresh run writes data/workspace/<client>/_backfill/ so the page and the files agree.  Backfill settings are
saved per client under backfill: in client.yaml (only values that differ from defaults).
"""
from __future__ import annotations

import dataclasses
import json

import streamlit as st

from gtpfit import workspace
from gtpfit.app.quality import state as quality_state
from gtpfit.backfill import BackfillParams, run_backfill, saved_params as backfill_saved_params, write_outputs


def ss():
    return st.session_state


def client() -> str:
    return ss().get("bf::client", "") or ""


def saved_params(name: str) -> BackfillParams:
    return backfill_saved_params(name)


def save_params(name: str, p: BackfillParams) -> None:
    base = dataclasses.asdict(BackfillParams())
    diff = {k: (list(v) if isinstance(v, tuple) else v) for k, v in dataclasses.asdict(p).items() if v != base[k]}
    workspace.write_client_yaml(name, backfill=diff)


def _signature(name: str) -> str:
    return json.dumps([quality_state._signature(name), workspace.read_client_yaml(name).get("quality")], default=str)


@st.cache_data(show_spinner=False, max_entries=6)
def _run(name: str, sig: str, params_json: str):
    p = BackfillParams(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in json.loads(params_json).items()})
    r = run_backfill(name, p)
    write_outputs(r, name)
    return r


def result(name: str, p: BackfillParams):
    """The backfill run (with its holdout) for this client and settings; a fresh run also writes _backfill/."""
    return _run(name, _signature(name), json.dumps(dataclasses.asdict(p), default=list))
