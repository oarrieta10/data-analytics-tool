"""state.py — what the Profile page remembers: which client, its settings and service targets, and the cached
profile run.

Session keys start with "pf::" (settings widgets "pf::p::<field>").  A profile run is cached on everything it
depends on (the ingest outputs, the saved scope, the client settings, the backfilled item master or backfill being skipped, the profile
settings and the service targets), and each fresh run writes data/workspace/<client>/_profile/ so the page and
the files agree.  Settings are saved under profile: in client.yaml (only values that differ from defaults); the
targets under profile: sla:.
"""
from __future__ import annotations

import dataclasses
import json

import streamlit as st

from gtpfit import workspace
from gtpfit.app.quality import state as quality_state
from gtpfit.backfill import is_skipped
from gtpfit.profile import ProfileParams, run_profile, saved_params as profile_saved_params, write_outputs


def ss():
    return st.session_state


def client() -> str:
    return ss().get("pf::client", "") or ""


def saved_params(name: str) -> ProfileParams:
    return profile_saved_params(name)


def save_params(name: str, p: ProfileParams) -> None:
    base = dataclasses.asdict(ProfileParams())
    prof = dict(workspace.read_client_yaml(name).get("profile") or {})
    for k in base:
        prof.pop(k, None)
    prof |= {k: (list(v) if isinstance(v, tuple) else v) for k, v in dataclasses.asdict(p).items() if v != base[k]}
    workspace.write_client_yaml(name, profile=prof)


def _signature(name: str) -> str:
    f = workspace.client_dir(name) / "_backfill" / "item_master_backfilled.parquet"
    return json.dumps([quality_state._signature(name), f.stat().st_mtime_ns if f.exists() else None,
                       is_skipped(name)], default=str)


@st.cache_data(show_spinner=False, max_entries=6)
def _run(name: str, sig: str, params_json: str, sla_json: str):
    p = ProfileParams(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in json.loads(params_json).items()})
    sla = {ch: {**t, "within": [tuple(x) for x in t.get("within", [])]} for ch, t in json.loads(sla_json).items()}
    r = run_profile(name, p, sla)
    write_outputs(r, name)
    return r


def result(name: str, p: ProfileParams, sla: dict):
    """The profile for this client, settings and targets (cached; a fresh run also writes _profile/)."""
    return _run(name, _signature(name), json.dumps(dataclasses.asdict(p), default=list),
                json.dumps(sla, sort_keys=True, default=list))
