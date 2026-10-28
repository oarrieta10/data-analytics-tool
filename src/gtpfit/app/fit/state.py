"""state.py — what the Fit page remembers: which client, its fit settings, and the cached fit run.

Session keys start with "fit::" (settings widgets "fit::p::<field>").  A fit run is cached on everything it
depends on (what profile depends on, the client's profile settings, the container catalogue file, and the fit
settings), and each fresh run writes data/workspace/<client>/_fit/.  Settings are saved under fit: in client.yaml
(only values that differ from defaults).
"""
from __future__ import annotations

import dataclasses
import json

import streamlit as st

from gtpfit import workspace
from gtpfit.app.profile import state as profile_state
from gtpfit.fit import FitParams, run_fit, saved_params as fit_saved_params, write_outputs
from gtpfit.fit.containers import catalog_path


def ss():
    return st.session_state


def client() -> str:
    return ss().get("fit::client", "") or ""


def saved_params(name: str) -> FitParams:
    return fit_saved_params(name)


def save_params(name: str, p: FitParams) -> None:
    base = dataclasses.asdict(FitParams())
    workspace.write_client_yaml(name, fit={k: v for k, v in dataclasses.asdict(p).items() if v != base[k]})


def _signature(name: str) -> str:
    cat = catalog_path()
    return json.dumps([profile_state._signature(name), workspace.read_client_yaml(name).get("profile"),
                       str(cat), cat.stat().st_mtime_ns if cat.exists() else None], default=str)


@st.cache_data(show_spinner=False, max_entries=6)
def _run(name: str, sig: str, params_json: str):
    r = run_fit(name, FitParams(**json.loads(params_json)))
    write_outputs(r, name)
    return r


def result(name: str, p: FitParams):
    """The fit run for this client and settings (cached; a fresh run also writes _fit/)."""
    return _run(name, _signature(name), json.dumps(dataclasses.asdict(p)))
