"""confirm.py — step 2: is each file what we think it is, and which client column feeds which field.

The consultant confirms or changes each file's table (one row per file / sheet
/ database table) and each column's field.  Unmapped required fields and a
field claimed twice disable "Run ingest".  Confirmed columns can be saved to
the client's own WMS profile."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from gtpfit.ingest import Source, save_wms_profile, suggest_mapping
from gtpfit.ingest.mapping import WmsProfile
from gtpfit.ingest.readers import headers as source_headers, read as read_source
from gtpfit.ingest.schema import FIELD_HELP, FIELD_LABEL, REQUIRED_TABLES, TABLE_LABEL, TABLES
from gtpfit.ingest.units import header_unit

from . import state, widgets

IGNORE = "(ignore)"
EXAMPLES, PREVIEW_ROWS = 3, 200
CONF = {"high": "", "medium": " (check)", "low": " (unsure)", "none": ""}
_KIND_UNIT = {"length": "length: from header, profile or data", "weight": "weight: from header, profile or data",
              "qty": "units (eaches)", "cases": "full cases", "count": "units per case", "stock": "units",
              "date": "date", "time": "time of day", "id": "", "text": ""}


# ---- pure helpers (tested) ----------------------------------------------------------------------

def default_mapping(table: str, headers: list, profile_columns: dict | None) -> dict:
    """The starting mapping: the WMS profile's columns for this table if it has them, else automatic matching."""
    if profile_columns:
        return {f: h for f, h in profile_columns.items() if h in headers}
    return dict(suggest_mapping(headers, table).mapping)


def effective_profile_text(profile_text: str | None, overrides: dict) -> str | None:
    """The WMS profile with the consultant's column choices for the tables they changed."""
    if not overrides:
        return profile_text
    base = (yaml.safe_load(profile_text) if profile_text else None) or {"name": "workbench"}
    cols = dict(base.get("columns") or {})
    cols.update(overrides)
    base["columns"] = cols
    return yaml.safe_dump(base, sort_keys=False, allow_unicode=True)


def mapping_problems(table: str, chosen: dict) -> list[str]:
    """chosen: field -> [client columns].  Required fields with no column, and fields claimed twice."""
    out = [f"{FIELD_LABEL[f.name]} is required: pick the client column that holds it."
           for f in TABLES[table] if f.required and not chosen.get(f.name)]
    out += [f"{FIELD_LABEL[f]} is claimed by {len(cs)} columns ({', '.join(cs)}): keep one."
            for f, cs in chosen.items() if len(cs) > 1]
    return out


def unit_guess(column: str, kind: str | None) -> str:
    hint = header_unit(column, "length") or header_unit(column, "weight")
    if hint:
        return f"{hint} (from the column name)"
    return _KIND_UNIT.get(kind, "") if kind else ""


@st.cache_data(show_spinner=False, max_entries=32)
def preview(sig: str, path: str, part) -> tuple:
    """Column names and a few distinct example values per column (first PREVIEW_ROWS rows)."""
    src = Source(Path(path), part)
    hdr = source_headers(src)
    df, _ = read_source(src, limit=PREVIEW_ROWS)
    ex = {}
    for c in dict.fromkeys(df.columns):
        col = df[c].iloc[:, 0] if isinstance(df[c], pd.DataFrame) else df[c]
        vals = [str(v) for v in col.dropna().drop_duplicates().head(EXAMPLES)]
        ex[c] = ", ".join(v if len(v) <= 24 else v[:23] + "…" for v in vals)
    return hdr, ex


# ---- screen pieces --------------------------------------------------------------------------------

def _files_table(dets, sig: str) -> dict:
    view = pd.DataFrame({
        "File": [d.file.name for d in dets],
        "Sheet / table": [d.part or "" for d in dets],
        "Looks like": [(state.USE_AS[d.table] + CONF[d.confidence]) if d.table else "Not data" for d in dets],
        "Rows": ["—" if d.rows is None else f"{d.rows:,}" + (" ⚠ Excel limit" if d.at_excel_limit else "")
                 for d in dets],
        "Read as": [d.read_as for d in dets],
        "Use as": [state.USE_AS[d.table] for d in dets],
    })
    edited = st.data_editor(view, hide_index=True, width="stretch", key=f"srcs::{sig}",
                            disabled=["File", "Sheet / table", "Looks like", "Rows", "Read as"],
                            column_config={"Use as": st.column_config.SelectboxColumn(
                                options=list(state.USE_AS.values()), required=True,
                                help="Confirm or change. Ignore = not used.")})
    assign: dict = {}
    for d, label in zip(dets, edited["Use as"]):
        t = state.LABEL_TO_TABLE[label]
        if t:
            assign.setdefault(t, []).append(d.source)
    return assign


def _mapping(table: str, src: Source, sig: str, prof_cols: dict) -> tuple[dict, list]:
    """Client column -> field editor for one table.  Returns (field -> column, problems)."""
    hdr, ex = preview(sig, str(src.path), src.part)
    default = default_mapping(table, hdr, prof_cols.get(table))
    col_field = {h: f for f, h in default.items()}
    opts = {IGNORE: None} | {FIELD_LABEL[f.name] + (" *" if f.required else ""): f.name for f in TABLES[table]}
    label_of = {v: k for k, v in opts.items()}
    kinds = {f.name: f.kind for f in TABLES[table]}
    cols = [h for h in dict.fromkeys(hdr) if h]
    prev = st.session_state.get(f"map::{table}::chosen", {f: [h] for f, h in default.items()})
    n_fields = len(TABLES[table])
    title = f"Column mapping: {TABLE_LABEL[table]} ({sum(1 for v in prev.values() if v)} of {n_fields} fields mapped)"
    bad_before = bool(mapping_problems(table, prev))
    chosen: dict = {}
    with st.expander(title, expanded=bad_before):
        st.caption(f"From {src}. * = required. Samples are the first distinct values in the file.")

        def editor(hs, part):
            df = pd.DataFrame({"Client column": hs, "Maps to": [label_of[col_field.get(h)] for h in hs],
                               "Samples": [ex.get(h, "") for h in hs],
                               "Unit guess": [unit_guess(h, kinds.get(col_field.get(h))) for h in hs]})
            return st.data_editor(df, hide_index=True, width="stretch", key=f"map::{table}::{sig}::{part}",
                                  disabled=["Client column", "Samples", "Unit guess"],
                                  column_config={"Maps to": st.column_config.SelectboxColumn(
                                      options=list(opts), required=True, width="medium")})
        mapped = [h for h in cols if h in col_field]
        unmapped = [h for h in cols if h not in col_field]
        frames = [editor(mapped, "mapped")] if mapped else []
        if unmapped and st.toggle(f"Show unmapped columns ({len(unmapped)})", key=f"map::{table}::{sig}::show"):
            frames.append(editor(unmapped, "unmapped"))
        elif unmapped:
            frames.append(pd.DataFrame({"Client column": unmapped, "Maps to": [IGNORE] * len(unmapped)}))
        for f in frames:
            for col, lab in zip(f["Client column"], f["Maps to"]):
                if opts[lab]:
                    chosen.setdefault(opts[lab], []).append(col)
        problems = mapping_problems(table, chosen)
        for p in problems:
            st.markdown(f":red[**{p}**]")
        missing_opt = [FIELD_LABEL[f.name] for f in TABLES[table] if not f.required and f.name not in chosen]
        if missing_opt:
            st.caption(f"Not mapped (optional): {', '.join(missing_opt)}.")
    st.session_state[f"map::{table}::chosen"] = chosen
    return {f: cs[0] for f, cs in chosen.items()}, problems


def show() -> None:
    widgets.header(editable=False)
    files = state.input_files()
    if not files:
        st.warning("No files: go back and add some.")
        if st.button("← Back", key="ing::back1"):
            state.go(1)
        return
    sig = state.signature(files)
    dets = state.detections(sig, tuple(str(p) for p in files), state.params_json())
    st.subheader("What did we receive?")
    assign = _files_table(dets, sig)

    blocking = []
    for t in TABLE_LABEL:
        if t in assign:
            continue
        if t in REQUIRED_TABLES:
            blocking.append(t)
            st.error(f"Missing: {TABLE_LABEL[t]} (required): {state.UNLOCKS[t]} cannot be sized. Assign a file "
                     "above or go back and add one.", icon="⛔")
        else:
            st.info(f"Missing: {TABLE_LABEL[t]} (optional): {state.UNLOCKS[t]} will be skipped.", icon="ℹ️")

    st.subheader("Which column is which?")
    prof_text = state.profile_text()
    prof_cols = ((yaml.safe_load(prof_text) or {}).get("columns") or {}) if prof_text else {}
    mappings, problems, overrides = {}, [], {}
    for t in [t for t in TABLES if t in assign]:
        mp, pr = _mapping(t, assign[t][0], sig, prof_cols)
        mappings[t], problems = mp, problems + pr
        hdr, _ = preview(sig, str(assign[t][0].path), assign[t][0].part)
        if mp != default_mapping(t, hdr, prof_cols.get(t)):
            overrides[t] = mp
    eff = effective_profile_text(prof_text, overrides)

    if st.button(f"Save mapping to {state.client()}'s WMS profile", key="ing::savemap", disabled=not mappings,
                 help=f"Writes {state.client_profile_path().name} under data/workspace/{state.client()}/ (local)."):
        base = (yaml.safe_load(prof_text) if prof_text else None) or {}
        first = {t: srcs[0] for t, srcs in assign.items()}
        prof = WmsProfile(name=state.client(), description=f"Columns confirmed for {state.client()}",
                          units=base.get("units") or {}, date_format=base.get("date_format"),
                          files={t: s.path.name for t, s in first.items()},
                          sheets={t: s.part for t, s in first.items() if s.part}, columns=mappings)
        save_wms_profile(prof, state.client_profile_path())
        st.session_state["ing::wms"] = f"{state.client_dir(state.client()).name}: {state.client_profile_path().name}"
        st.toast(f"Saved {state.client_profile_path().name}")

    c1, c2 = st.columns([1, 5])
    if c1.button("← Back", key="ing::back1"):
        state.go(1)
    blocked = bool(blocking or problems or not assign)
    if c2.button("Run ingest →", type="primary", key="ing::run", disabled=blocked):
        st.session_state["ing::assign"] = {t: [(str(s.path), s.part) for s in srcs] for t, srcs in assign.items()}
        st.session_state["ing::profile_eff"] = eff
        st.session_state["ing::expected"] = {d.table: (d.rows or 0) for d in dets if d.table}
        st.session_state["ing::accept"] = {}
        st.session_state["ing::requested"] = []
        state.go(3)
    if blocked:
        st.caption("Run ingest needs every required table and required column.")
