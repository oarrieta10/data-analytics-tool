"""workspace.py — where each client's work is kept on this computer, and the client's saved settings.

    data/workspace/<client>/
        _ingest/          ingest outputs (canonical tables as Parquet, issues, receipt)
        _scope/           scope_summary.csv (what the saved scope removes)
        client.yaml       recent folders, client settings (settings:), the saved scope (scope:)
        wms_<client>.yaml the client's confirmed column mapping, units and date format

No Streamlit here: the workbench pages and the analysis modules (through
gtpfit.tables) both use it.  Client files are never copied here and nothing is
written into the client's own folder.  The location can be moved with the
GTPFIT_WORKSPACE environment variable (tests use it).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import os
import re
from pathlib import Path

import yaml

from gtpfit.ingest import IngestParams
from gtpfit.ingest.params import DEFAULT_ASSORTMENT, assortment_bands, block_fields, meta

ROOT = Path(__file__).resolve().parents[2]  # repo root: data/ lives here
DEFAULT_WORKSPACE = ROOT / "data" / "workspace"


def workspace() -> Path:
    return Path(os.environ.get("GTPFIT_WORKSPACE", DEFAULT_WORKSPACE))


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _.-]", "", name or "").strip()


def client_dir(name: str) -> Path:
    return workspace() / safe_name(name)


def ingest_dir(name: str) -> Path:
    return client_dir(name) / "_ingest"


def read_client_yaml(name: str) -> dict:
    p = client_dir(name) / "client.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if name and p.exists() else {}


def write_client_yaml(name: str, **info) -> None:
    """Update the client's client.yaml (keys not given are kept)."""
    d = client_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    data = {**read_client_yaml(name), "name": name, "updated": dt.datetime.now().isoformat(timespec="seconds"),
            **info}
    (d / "client.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


# ---- client settings (set on Ingest's Confirm step), saved per client ----------------------------------

# Which IngestParams field shows where comes from its metadata (gtpfit.ingest.params.setting).
CLIENT_FIELDS = tuple(f for f in block_fields("client") if not meta(f).get("via"))  # drawn one by one
BAND_FIELDS = tuple(f for f in block_fields("client") if meta(f).get("via") == "assortment")
DETECTION_FIELDS = tuple(block_fields("detection"))
EXTRA_SETTINGS = {"assortment": DEFAULT_ASSORTMENT, "line_definition": "row"}  # client settings, not params


def _plain(v):
    return list(v) if isinstance(v, tuple) else v


def default_settings() -> dict:
    d = dataclasses.asdict(IngestParams())
    return {**{k: _plain(d[k]) for k in CLIENT_FIELDS + DETECTION_FIELDS}, **EXTRA_SETTINGS}


def client_settings(name: str) -> dict:
    """The client's settings: defaults, then what its client.yaml holds (unknown keys ignored)."""
    saved = read_client_yaml(name).get("settings") or {}
    base = default_settings()
    return {**base, **{k: v for k, v in saved.items() if k in base}}


def save_client_settings(settings: dict, name: str) -> None:
    """Save only the values that differ from the defaults, so a later change of default still reaches the client."""
    base = default_settings()
    write_client_yaml(name, settings={k: _plain(v) for k, v in settings.items()
                                      if k in base and _plain(v) != base[k]})


def changed(settings: dict, keys) -> list[str]:
    base = default_settings()
    return [k for k in keys if _plain(settings.get(k, base[k])) != base[k]]


def settings_params(settings: dict) -> dict:
    """IngestParams (as a dict) from the settings: each field as set, the bands from the assortment."""
    d = dataclasses.asdict(IngestParams())
    for k in CLIENT_FIELDS + DETECTION_FIELDS:
        if k in settings:
            d[k] = tuple(settings[k]) if isinstance(settings[k], list) else settings[k]
    d["peak_months"] = tuple(sorted(int(m) for m in d["peak_months"]))
    d.update(assortment_bands(settings.get("assortment", DEFAULT_ASSORTMENT)))
    return d


def client_params(name: str) -> IngestParams:
    """The client's IngestParams (peak months, minimum history, ...) from its saved settings."""
    return IngestParams(**settings_params(client_settings(name)))


def line_definition(name: str) -> str:
    return client_settings(name).get("line_definition", "row")
