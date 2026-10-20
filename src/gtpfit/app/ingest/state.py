"""state.py — what the guided Ingest page remembers between steps, and where client files live.

Session keys all start with "ing::".  Input is always one folder, chosen with
the operating system's folder dialog (a short-lived subprocess), a pasted path
or one of the client's recent folders, and read in place: nothing is copied and
nothing is written into it.  data/workspace/<client>/ keeps _ingest/ outputs,
the client's remembered mapping (wms_<client>.yaml) and client.yaml (recent
folders; also how recent clients are listed).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import streamlit as st
import yaml

from gtpfit.app import ROOT
from gtpfit.ingest import IngestParams, Source, ingest_sources, load_wms_profile, parse_wms_profile, save_wms_profile
from gtpfit.ingest.mapping import WmsProfile
from gtpfit.ingest.detect import detect
from gtpfit.ingest.schema import TABLE_LABEL

WORKSPACE = Path(os.environ.get("GTPFIT_WORKSPACE", ROOT / "data" / "workspace"))  # env: tests
DATA_EXT = {".csv", ".txt", ".tsv", ".tab", ".dat", ".psv", ".gz", ".zip", ".xlsx", ".xlsm", ".xls", ".xlsb",
            ".json", ".jsonl", ".parquet", ".feather", ".arrow", ".orc", ".sqlite", ".db", ".duckdb"}
STEPS = ["Choose folder", "Confirm files", "Review", "Done"]
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
    """Update the client's client.yaml (keys not given are kept)."""
    d = client_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "client.yaml"
    old = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}
    data = {**old, "name": name, "updated": dt.datetime.now().isoformat(timespec="seconds"), **info}
    (d / "client.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


# ---- where the files are: one folder, read in place ---------------------------------------------

def input_folder() -> Path | None:
    f = ss().get("ing::folder_path")
    return Path(f) if f else None


def input_files() -> list[Path]:
    f = input_folder()
    return sorted(p for p in f.iterdir() if p.is_file()) if f and f.is_dir() else []


def output_dir() -> Path:
    """Outputs go to the workspace, never into the client's folder (it may be a shared drive)."""
    return client_dir(client()) / "_ingest"


_DIALOG = "; ".join([
    "import tkinter as tk",
    "from tkinter import filedialog",
    "root = tk.Tk()",
    "root.withdraw()",
    "root.attributes('-topmost', True)",
    "print(filedialog.askdirectory(title='Choose the folder holding the client files') or '')",
])
DIALOG_TIMEOUT_S = 600  # the consultant may browse a while


def choose_folder_dialog(run=subprocess.run) -> str | None:
    """The operating system's folder picker, in a short-lived subprocess (not the Streamlit thread).

    Returns the chosen path, or None if cancelled or no dialog could be shown (e.g. no display).
    """
    try:
        r = run([sys.executable, "-c", _DIALOG], capture_output=True, text=True, timeout=DIALOG_TIMEOUT_S)
    except Exception:  # no tkinter / no display / timed out: the paste field still works
        return None
    path = (getattr(r, "stdout", "") or "").strip()
    return path if getattr(r, "returncode", 1) == 0 and path else None


RECENT_FOLDERS = 5


def recent_folders(name: str) -> list[str]:
    p = client_dir(name) / "client.yaml"
    if not name or not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return list(data.get("recent_folders") or [])


def remember_folder(name: str, folder) -> None:
    folders = [str(folder)] + [f for f in recent_folders(name) if f != str(folder)]
    write_client_yaml(name, recent_folders=folders[:RECENT_FOLDERS])


def open_folder(path: str) -> str | None:
    """Use this folder for the client and go to Confirm.  Returns an error message if it can't be used."""
    p = Path(str(path).strip().strip('"')).expanduser()
    if not str(path).strip() or not p.is_dir():
        return f"Not a folder: {path}"
    ss()["ing::folder_path"] = str(p)
    remember_folder(client(), p)
    for k in [k for k in ss().keys() if str(k).startswith(("srcs::", "map::"))]:
        del ss()[k]  # a new folder: fresh guesses and mappings
    go(2)
    return None


def signature(paths) -> str:
    """Changes when any file changes, so cached detection and runs refresh."""
    return json.dumps([(str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths])


@st.cache_data(show_spinner=False, max_entries=8)
def detections(sig: str, paths: tuple, params_json: str):
    return detect([Path(p) for p in paths], IngestParams(**params_from_json(params_json)))


# ---- the client's remembered mapping (saved automatically; no profile picking) ------------------------

def client_profile_path() -> Path:
    return client_dir(client()) / f"wms_{safe_name(client()).replace(' ', '_').lower()}.yaml"


def client_profile() -> WmsProfile | None:
    """This client's last confirmed columns, units and date format, if any."""
    p = client_profile_path()
    if not client() or not p.exists():
        return None
    try:
        return load_wms_profile(p)
    except Exception:  # an unreadable profile must not stop ingest; matching starts afresh
        return None


def save_client_profile(columns=None, headers=None, sheets=None, files=None, units=None,
                        date_format=None) -> WmsProfile:
    """Merge confirmed decisions into the client's profile and save it (tables not given are kept)."""
    prof = client_profile() or WmsProfile(name=client(), description=f"Confirmed for {client()} in the workbench")
    for attr, new in (("columns", columns), ("headers", headers), ("sheets", sheets), ("files", files)):
        if new:
            getattr(prof, attr).update(new)
    if units:
        prof.units = dict(units)
    if date_format:
        prof.date_format = date_format
    prof.saved_at = dt.datetime.now().isoformat(timespec="seconds")
    save_wms_profile(prof, client_profile_path())
    return prof


def detected(files) -> list:
    """What each file is: cached detection, then the client's remembered mappings applied."""
    if not files:
        return []
    return with_memory(detections(signature(files), tuple(str(p) for p in files), params_json()))


def with_memory(dets: list) -> list:
    """Detections, re-guessed where a file matches a mapping confirmed earlier for this client."""
    from gtpfit.ingest.mapping import saved_mapping_for
    from gtpfit.ingest.readers import headers as source_headers
    prof = client_profile()
    if prof is None or not prof.columns:
        return dets
    out = []
    for d in dets:
        try:
            hdr = source_headers(d.source)
        except Exception:  # unreadable: keep the detection as it is
            out.append(d)
            continue
        hit = next((t for t in prof.columns if saved_mapping_for(prof, t, hdr)), None)
        out.append(dataclasses.replace(d, table=hit, confidence="high",
                                       reason="matches the columns confirmed last time for this client")
                   if hit else d)
    return out


def chosen_units() -> dict:
    """Units for this run: picked on the Review screen this session, else remembered for the client."""
    prof = client_profile()
    return ss().get("ing::units") or (dict(prof.units) if prof and prof.units else {})


def run_profile_text(overrides: dict | None = None) -> str | None:
    """The profile ingest runs with: remembered units / date format plus the columns confirmed this time
    (only tables whose mapping differs from automatic matching need to be spelled out)."""
    prof = client_profile()
    d: dict = {"name": client() or "workbench"}
    if chosen_units():
        d["units"] = chosen_units()
    if prof and prof.date_format:
        d["date_format"] = prof.date_format
    if overrides:
        d["columns"] = overrides
    return yaml.safe_dump(d, sort_keys=False, allow_unicode=True) if len(d) > 1 else None


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
    """Start another client: forget the run and the choices (the client's files were never copied)."""
    for k in [k for k in ss().keys() if str(k).startswith(("ing::", "srcs::", "map::", "thr::", "acc"))]:
        del ss()[k]
