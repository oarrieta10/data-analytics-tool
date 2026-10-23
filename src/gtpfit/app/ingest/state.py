"""state.py — what the guided Ingest page remembers between steps, and where client files live.

Session keys all start with "ing::".  Input is always one folder, chosen with
the operating system's folder dialog (a short-lived subprocess), a pasted path
or one of the client's recent folders, and read in place: nothing is copied and
nothing is written into it.  data/workspace/<client>/ keeps _ingest/ outputs,
the client's remembered mapping (wms_<client>.yaml) and client.yaml (recent
folders, client settings; also how recent clients are listed).  Client settings
(always shown) and detection settings (collapsed) are both saved per client,
only where they differ from the defaults; which field goes where comes from
IngestParams' metadata.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import streamlit as st
import yaml

from gtpfit import workspace as ws
from gtpfit.ingest import (IngestParams, Source, ingest_sources, ingest_table, load_wms_profile, parse_wms_profile,
                           save_wms_profile, table_inputs)
from gtpfit.ingest.mapping import WmsProfile
from gtpfit.ingest.detect import detect
from gtpfit.ingest.schema import TABLE_LABEL

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
    return ws.workspace()


# ---- clients ---------------------------------------------------------------------------------

safe_name, client_dir = ws.safe_name, ws.client_dir


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


write_client_yaml = ws.write_client_yaml


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
    for k in [k for k in ss().keys() if str(k).startswith(("srcs::", "map::", "cs::"))]:
        del ss()[k]  # a new folder: fresh guesses and mappings, the client's saved settings
    ss().pop("ing::settings", None)
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


# ---- settings: client and detection settings, saved per client ----------------------------------------

CLIENT_FIELDS, BAND_FIELDS, DETECTION_FIELDS = ws.CLIENT_FIELDS, ws.BAND_FIELDS, ws.DETECTION_FIELDS
EXTRA_SETTINGS = ws.EXTRA_SETTINGS
default_settings, changed, settings_params = ws.default_settings, ws.changed, ws.settings_params


def client_settings(name: str | None = None) -> dict:
    return ws.client_settings(client() if name is None else name)


def save_client_settings(settings: dict, name: str | None = None) -> None:
    ws.save_client_settings(settings, name or client())


def settings() -> dict:
    """This run's settings: edited on Confirm this session, else the client's saved ones."""
    return ss().get("ing::settings") or client_settings()


def line_definition() -> str:
    return settings().get("line_definition", "row")


def params_from_json(s: str) -> dict:
    d = json.loads(s) if s else {}
    for f in dataclasses.fields(IngestParams):
        if isinstance(d.get(f.name), list):
            d[f.name] = tuple(d[f.name])
    return d


def params() -> dict:
    return settings_params(settings())


def params_json() -> str:
    return json.dumps(params(), sort_keys=True)


# ---- running ingest ------------------------------------------------------------------------------

def run_spec(assign: dict, profile: str | None) -> dict:
    """What a run reads with.  Acceptances are not part of it: they only relabel blocks (apply_acceptances)."""
    return dict(assign=sorted((t, str(s.path), s.part) for t, srcs in assign.items() for s in srcs),
                profile=profile, params=params())


def run_key(spec: dict, files) -> str:
    return json.dumps(spec, sort_keys=True, default=str) + signature(files)


TABLE_RUNS = 12  # cached per-table results kept in the session (a few variants of each of four tables)


def table_key(sources, table: str, profile, params: IngestParams) -> str:
    """Changes only when something this table's ingest depends on changes (see pipeline.table_inputs)."""
    return json.dumps([table, [(str(s.path), s.part) for s in sources], signature(sorted({s.path for s in sources})),
                       table_inputs(table, profile, params)], sort_keys=True, default=str)


def _cached_table(sources, table, profile, params, accept):
    """ingest_table, reused while the table's own inputs are unchanged (e.g. a unit decision re-reads only the
    item master).  Run without acceptances: they are applied to the whole result afterwards."""
    cache = ss().setdefault("ing::table_runs", {})
    srcs = [s if isinstance(s, Source) else Source(Path(s), None) for s in sources]
    key = table_key(srcs, table, profile, params)
    if key not in cache:
        if len(cache) >= TABLE_RUNS:
            cache.pop(next(iter(cache)))
        cache[key] = ingest_table(srcs, table, profile, params)
    return cache[key]


# The progress bar sits in the page's top bar (level with Deploy and the ⋮ menu), fixed there so it stays
# in view wherever the page is scrolled.  The header is z-index 999990 in Streamlit; this goes above it.
_HEADER_PROGRESS_CSS = """<style>
.st-key-ing-progress {position: fixed; top: 0.6rem; left: 50%; transform: translateX(-50%);
  width: min(40vw, 520px); z-index: 1000000; gap: 0;}
.st-key-ing-progress p {font-size: 0.85rem; margin: 0;}
</style>"""


def progress_slot() -> None:
    """Reserve the top-bar progress box at the top of the page, on every run, empty until needed.

    Its place in the page must never change between runs: Streamlit keeps an element's previous contents on
    screen until a run ends, so a box created only on some runs takes over whatever sat in that place before
    (e.g. a block card) and shows it, fixed, over the top bar for the whole run."""
    st.markdown(_HEADER_PROGRESS_CSS, unsafe_allow_html=True)
    ss()["ing::progress_slot"] = st.container(key="ing-progress").empty()


def header_progress(text: str):
    """A progress bar in the top bar of the page, drawn in the slot progress_slot() reserved."""
    slot = ss().get("ing::progress_slot")
    if slot is None:  # not reserved (e.g. a test calling run_ingest directly): reserve it here
        progress_slot()
        slot = ss()["ing::progress_slot"]
    return slot.progress(0.0, text=text)


def run_ingest(spec: dict, files, expected_rows: dict | None = None):
    """Ingest with a progress bar, without acceptances (apply them with apply_acceptances).  Whole runs are
    kept per run key so going back and forth is instant; tables whose inputs did not change are not re-read."""
    key = run_key(spec, files)
    runs = ss().setdefault("ing::runs", {})
    if key in runs:
        return runs[key]
    assign: dict = {}
    for table, path, part in spec["assign"]:
        assign.setdefault(table, []).append(Source(Path(path), part))
    profile = parse_wms_profile(yaml.safe_load(spec["profile"]) or {}) if spec["profile"] else None
    p = IngestParams(**params_from_json(json.dumps(spec["params"])))  # tuples back from any lists
    cache = ss().setdefault("ing::table_runs", {})
    bar = header_progress("Starting ingest…")

    def tick(e):
        label = TABLE_LABEL.get(e["table"], e["table"])
        n = (expected_rows or {}).get(e["table"])
        if e["stage"] == "start":
            if table_key(assign[e["table"]], e["table"], profile, p) in cache:
                text = f"{label}: unchanged, reusing it…"
            else:
                text = f"Reading {label}" + (f": {n:,} rows…" if n else "…")
            bar.progress(e["index"] / e["total"], text=text)
        else:
            bar.progress((e["index"] + 1) / e["total"], text=f"{label}: {e['rows'] or 0:,} rows kept")

    r = ingest_sources(assign, profile=profile, params=p, strict=False, where="the confirmed files",
                       on_progress=tick, run_table=_cached_table)
    bar.empty()
    if len(runs) >= 3:
        runs.pop(next(iter(runs)))
    runs[key] = r
    return r


def reset() -> None:
    """Start another client: forget the run and the choices (the client's files were never copied)."""
    for k in [k for k in ss().keys() if str(k).startswith(("ing::", "srcs::", "map::", "thr::", "cs::", "ds::", "acc"))]:
        del ss()[k]
