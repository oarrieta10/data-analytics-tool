"""state.py — what the guided Ingest page remembers between steps, and where client files live.

Session keys all start with "ing::".  Uploaded files go to a temp folder for
the session and are removed once step 4 has written the outputs (or on
"Start another client").  data/workspace/<client>/ keeps only what is worth
keeping: _ingest/ outputs, the client's WMS profile (wms_<client>.yaml) and
client.yaml, which is how recent clients are listed.  "A folder" mode reads
files in place and writes <folder>/_ingest/.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import streamlit as st
import yaml

from gtpfit.app import ROOT
from gtpfit.ingest import IngestParams, Source, ingest_sources, parse_wms_profile
from gtpfit.ingest.detect import detect
from gtpfit.ingest.schema import TABLE_LABEL

WORKSPACE = Path(os.environ.get("GTPFIT_WORKSPACE", ROOT / "data" / "workspace"))  # env: tests
DATA_EXT = {".csv", ".txt", ".tsv", ".tab", ".dat", ".psv", ".gz", ".zip", ".xlsx", ".xlsm", ".xls", ".xlsb",
            ".json", ".jsonl", ".parquet", ".feather", ".arrow", ".orc", ".sqlite", ".db", ".duckdb"}
STEPS = ["Upload", "Confirm files", "Review", "Done"]
UNLOCKS = {"item_master": "container fit, cube and storage", "orders": "volumes, the design day and workstations",
           "receipts": "inbound sizing", "inventory": "storage and days-on-hand sizing"}
USE_AS = {"item_master": "Item master", "orders": "Orders", "receipts": "Receipts", "inventory": "Inventory",
          None: "Ignore"}
LABEL_TO_TABLE = {v: k for k, v in USE_AS.items()}
NEW_CLIENT = "New client…"


def ss():
    return st.session_state


def go(step: int) -> None:
    ss()["ing::step"] = step
    st.rerun()


def workspace() -> Path:
    return Path(os.environ.get("GTPFIT_WORKSPACE", WORKSPACE))


# ---- clients ---------------------------------------------------------------------------------

def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _.-]", "", name or "").strip()


def client_dir(name: str) -> Path:
    return workspace() / safe_name(name)


def recent_clients() -> list[str]:
    """Clients with something kept in the workspace (outputs, profile or client.yaml), newest first."""
    base = workspace()
    if not base.exists():
        return []
    keep = [d for d in base.iterdir() if d.is_dir() and
            ((d / "client.yaml").exists() or (d / "_ingest").exists() or any(d.glob("wms*.yaml")))]
    return [d.name for d in sorted(keep, key=lambda d: d.stat().st_mtime, reverse=True)]


def client() -> str:
    return ss().get("ing::client", "") or ""


def write_client_yaml(name: str, **info) -> None:
    d = client_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "last_ingest": dt.datetime.now().isoformat(timespec="seconds"), **info}
    (d / "client.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


# ---- where the files are ---------------------------------------------------------------------

def mode() -> str:
    return ss().get("ing::mode", "Drop files")


def upload_dir() -> Path:
    """The session's temp folder for dropped files (created on first use)."""
    if not ss().get("ing::tmpdir") or not Path(ss()["ing::tmpdir"]).exists():
        ss()["ing::tmpdir"] = tempfile.mkdtemp(prefix="gtpfit_upload_")
    return Path(ss()["ing::tmpdir"])


def input_folder() -> Path | None:
    if mode() == "Drop files":
        return upload_dir()
    f = ss().get("ing::folder_path")
    return Path(f) if f else None


def input_files() -> list[Path]:
    f = input_folder()
    return sorted(p for p in f.iterdir() if p.is_file()) if f and f.is_dir() else []


def save_uploads(files, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for up in files or []:
        target = dest / Path(up.name).name
        if not target.exists() or target.stat().st_size != up.size:
            target.write_bytes(up.getbuffer())


def remove_uploads() -> None:
    """Delete the temp upload folder (never a folder the consultant pointed at)."""
    t = ss().pop("ing::tmpdir", None)
    if t and Path(t).exists() and Path(t).name.startswith("gtpfit_upload_"):
        shutil.rmtree(t, ignore_errors=True)
    ss()["ing::up_gen"] = ss().get("ing::up_gen", 0) + 1  # the uploader forgets its files


def output_dir() -> Path:
    if mode() == "A folder" and input_folder():
        return input_folder() / "_ingest"
    return client_dir(client()) / "_ingest"


def signature(paths) -> str:
    """Changes when any file changes, so cached detection and runs refresh."""
    return json.dumps([(str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths])


@st.cache_data(show_spinner=False, max_entries=8)
def detections(sig: str, paths: tuple, params_json: str):
    return detect([Path(p) for p in paths], IngestParams(**params_from_json(params_json)))


# ---- WMS profiles ----------------------------------------------------------------------------

AUTO = "Auto-detect"


def profiles() -> dict:
    """Shared profiles (wms_profiles/), plus this client's own and any wms*.yaml beside folder-mode files."""
    opts = {p.stem: p for p in sorted((ROOT / "wms_profiles").glob("*.yaml"))}
    for base in [client_dir(client()) if client() else None, input_folder() if mode() == "A folder" else None]:
        if base and base.is_dir():
            for p in sorted(base.rglob("wms*.yaml")):
                if "_ingest" not in p.relative_to(base).parts:
                    opts[f"{base.name}: {p.name}"] = p
    return opts


def profile_text() -> str | None:
    choice = ss().get("ing::wms", AUTO)
    p = profiles().get(choice)
    return p.read_text(encoding="utf-8") if p else None


def client_profile_path() -> Path:
    return client_dir(client()) / f"wms_{safe_name(client()).replace(' ', '_').lower()}.yaml"


# ---- thresholds --------------------------------------------------------------------------------

def params_from_json(s: str) -> dict:
    d = json.loads(s) if s else {}
    for f in dataclasses.fields(IngestParams):
        if isinstance(d.get(f.name), list):
            d[f.name] = tuple(d[f.name])
    return d


def params() -> dict:
    return ss().get("ing::params") or dataclasses.asdict(IngestParams())


def params_json() -> str:
    return json.dumps(params(), sort_keys=True)


# ---- running ingest ------------------------------------------------------------------------------

def run_spec(assign: dict, profile: str | None) -> dict:
    return dict(assign=sorted((t, str(s.path), s.part) for t, srcs in assign.items() for s in srcs),
                profile=profile, params=params(), accept=ss().get("ing::accept", {}))


def run_key(spec: dict, files) -> str:
    return json.dumps(spec, sort_keys=True, default=str) + signature(files)


def run_ingest(spec: dict, files, expected_rows: dict | None = None):
    """Ingest with a progress bar; results are kept per run key so going back and forth is instant."""
    key = run_key(spec, files)
    runs = ss().setdefault("ing::runs", {})
    if key in runs:
        return runs[key]
    assign: dict = {}
    for table, path, part in spec["assign"]:
        assign.setdefault(table, []).append(Source(Path(path), part))
    profile = parse_wms_profile(yaml.safe_load(spec["profile"]) or {}) if spec["profile"] else None
    bar = st.progress(0.0, text="Starting ingest…")

    def tick(e):
        label = TABLE_LABEL.get(e["table"], e["table"])
        n = (expected_rows or {}).get(e["table"])
        if e["stage"] == "start":
            bar.progress(e["index"] / e["total"], text=f"Reading {label}" + (f": {n:,} rows…" if n else "…"))
        else:
            bar.progress((e["index"] + 1) / e["total"], text=f"{label}: {e['rows'] or 0:,} rows kept")

    p = IngestParams(**params_from_json(json.dumps(spec["params"])))  # tuples back from any lists
    r = ingest_sources(assign, profile=profile, params=p, accept=spec["accept"],
                       strict=False, where="the confirmed files", on_progress=tick)
    bar.empty()
    if len(runs) >= 3:
        runs.pop(next(iter(runs)))
    runs[key] = r
    return r


def reset() -> None:
    """Start another client: forget the run, the choices and the dropped files."""
    remove_uploads()
    for k in [k for k in ss().keys() if str(k).startswith(("ing::", "srcs::", "map::", "thr::", "acc"))]:
        if k not in ("ing::up_gen",):
            del ss()[k]
