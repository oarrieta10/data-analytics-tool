"""ingest_page.py — drop client files (or pick a folder), confirm what each holds, ingest with live parameters.

Dropped files are saved to data/workspace/<client>/ (git-ignored, stays on this
laptop).  Every file, sheet, database table and zip member gets a guessed table
that the consultant confirms or changes before ingest runs.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from gtpfit.app import ROOT
from gtpfit.ingest import (IngestParams, Source, ingest_sources, parse_wms_profile, profile_from_result,
                           save_wms_profile, write_outputs)
from gtpfit.ingest.classify import classify, list_sources
from gtpfit.ingest.readers import SUPPORTED
from gtpfit.ingest.schema import TABLES
from gtpfit.lines import LINE_DEFINITIONS, order_profile

SEV_LABEL = {"block": "⛔ block", "accepted": "✅ accepted", "warn": "⚠️ warn", "info": "ℹ️ info"}
AUTO = "(none: match columns automatically)"


WORKSPACE = Path(os.environ.get("GTPFIT_WORKSPACE", ROOT / "data" / "workspace"))  # env: tests
USE_AS = {"item_master": "Item master", "orders": "Orders", "receipts": "Receipts (inbound)",
          "inventory": "Inventory", None: "Ignore"}
_LABEL_TO_TABLE = {v: k for k, v in USE_AS.items()}
CONF_LABEL = {"high": "high", "medium": "medium", "low": "low: check", "none": "—"}
_DATA_EXT = {".csv", ".txt", ".tsv", ".tab", ".dat", ".psv", ".gz", ".zip", ".xlsx", ".xlsm", ".xls", ".xlsb",
             ".json", ".jsonl", ".parquet", ".feather", ".arrow", ".orc", ".sqlite", ".db", ".duckdb"}


def _folders():
    """Folders under data/ that hold client-style files (skips _ingest, _truth, _benchmark...)."""
    base = ROOT / "data"
    if not base.exists():
        return []
    out = []
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base)
        if p.is_dir() and len(rel.parts) <= 3 and not any(x.startswith("_") for x in rel.parts) and \
                rel.parts[0] != "workspace" and any(c.suffix.lower() in _DATA_EXT for c in p.iterdir() if c.is_file()):
            out.append(p.relative_to(ROOT).as_posix())
    return out


def _profiles(folder: Path) -> dict:
    """Shared WMS profiles plus any wms*.yaml kept with the client's data (those stay local, never committed)."""
    opts = {p.stem: p for p in sorted((ROOT / "wms_profiles").glob("*.yaml"))}
    if folder.is_dir():
        for p in sorted(folder.rglob("wms*.yaml")):
            if "_ingest" in p.relative_to(folder).parts:  # outputs, not inputs
                continue
            opts[f"local: {p.relative_to(folder).as_posix()}"] = p
    return opts


def _files(folder: Path) -> list:
    return sorted(p for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []


def _signature(paths) -> str:
    """Changes when any file changes, so cached guesses and runs are refreshed."""
    return json.dumps([(str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths])


def _client_dir(name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9 _.-]", "", name).strip() or "untitled"
    return WORKSPACE / safe


def _save_uploads(files, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for up in files or []:
        target = dest / Path(up.name).name
        if not target.exists() or target.stat().st_size != up.size:
            target.write_bytes(up.getbuffer())


@st.cache_data(show_spinner=False, max_entries=8)
def _guesses(sig: str, paths: tuple) -> list:
    """One row per file / sheet / database table / zip member, with the guessed table."""
    rows = []
    for src in list_sources([Path(p) for p in paths]):
        g = classify(src)
        rows.append(dict(path=str(src.path), part=src.part, source=str(src), guess=g.table,
                         confidence=g.confidence, why=g.reason))
    return rows


@st.cache_resource(max_entries=6, show_spinner=False)
def _run(assign_key: tuple, profile_text: str | None, params_json: str, accept: tuple):
    profile = parse_wms_profile(yaml.safe_load(profile_text) or {}) if profile_text else None
    params = IngestParams(**_params_from_json(params_json))
    assign: dict = {}
    for table, path, part, _ in assign_key:
        assign.setdefault(table, []).append(Source(Path(path), part))
    return ingest_sources(assign, profile=profile, params=params, accept=accept, strict=False,
                          where="the confirmed files")


def _sources_table(rows: list, sig: str) -> dict:
    """The guess-then-confirm table; returns {table: [Source]} for the rows not set to Ignore."""
    # read from the row dicts, not a DataFrame: pandas turns a missing guess / part (None) into NaN
    view = pd.DataFrame({
        "Use as": [USE_AS[r["guess"]] for r in rows],
        "Source (file > sheet / table)": [r["source"] for r in rows],
        "Guess": [USE_AS[r["guess"]] if r["guess"] else "—" for r in rows],
        "Confidence": [CONF_LABEL[r["confidence"]] for r in rows],
        "Why": [r["why"] for r in rows],
    })
    edited = st.data_editor(
        view, hide_index=True, width="stretch", key=f"srcs::{sig}",
        disabled=["Source (file > sheet / table)", "Guess", "Confidence", "Why"],
        column_config={
            "Use as": st.column_config.SelectboxColumn(options=list(USE_AS.values()), required=True, width="small",
                                                       help="Confirm the guess or change it. Ignore = not used."),
            "Why": st.column_config.TextColumn(width="large")})
    assign: dict = {}
    for i, label in enumerate(edited["Use as"]):
        table = _LABEL_TO_TABLE[label]
        if table:
            assign.setdefault(table, []).append(Source(Path(rows[i]["path"]), rows[i]["part"]))
    return assign


def _params_from_json(s: str) -> dict:
    d = json.loads(s)
    for f in dataclasses.fields(IngestParams):
        if isinstance(d.get(f.name), list):
            d[f.name] = tuple(d[f.name])
    return d


def _param_controls() -> dict:
    """One widget per IngestParams field, built from the dataclass so new params appear automatically."""
    p = IngestParams()
    vals = {}
    for f in dataclasses.fields(p):
        v = getattr(p, f.name)
        label = f.name.replace("_", " ")
        key = f"ip::{f.name}"
        if isinstance(v, bool):
            vals[f.name] = st.checkbox(label, v, key=key)
        elif isinstance(v, float):
            vals[f.name] = st.number_input(label, value=v, format="%.4f", step=v / 10 or 0.01, key=key)
        elif isinstance(v, int):
            vals[f.name] = st.number_input(label, value=v, step=1, key=key)
        elif isinstance(v, dict):
            st.markdown(f"**{label}**")
            vals[f.name] = {k: st.number_input(f"{label}: {k}", value=x, step=1, key=f"{key}::{k}") for k, x in v.items()}
        elif isinstance(v, tuple) and f.name == "excel_row_limits":
            txt = st.text_input(label, ", ".join(str(x) for x in v), key=key,
                                help="Row counts that mean a clipped export. Add a small number to try the refusal.")
            vals[f.name] = tuple(int(x) for x in txt.replace(" ", "").split(",") if x)
        elif isinstance(v, tuple) and len(v) == 2:
            a, b = st.columns(2)
            vals[f.name] = (a.number_input(f"{label}: low", value=float(v[0]), key=f"{key}::lo"),
                            b.number_input(f"{label}: high", value=float(v[1]), key=f"{key}::hi"))
    return vals


@st.cache_resource(max_entries=6, show_spinner=False)
def _line_profiles(run_key: tuple, _orders: pd.DataFrame) -> pd.DataFrame:
    return pd.concat([order_profile(_orders, how) for how in LINE_DEFINITIONS], ignore_index=True)


def _order_lines_tab(r, run_key):
    st.markdown("What counts as an order line is **agreed with the client**. Ingest keeps the file's rows as they "
                "are; the definition is applied when the data is analysed. Both are shown so you can discuss them.")
    for how, text in LINE_DEFINITIONS.items():
        st.markdown(f"- `{how}`: {text}")
    if "orders" not in r.tables:
        st.info("No orders table.")
        return
    with st.spinner("Comparing line definitions..."):
        prof = _line_profiles(run_key, r.tables["orders"])
    merged = int(prof.loc[(prof.channel == "All") & (prof.definition == "row"), "lines"].iat[0] -
                 prof.loc[(prof.channel == "All") & (prof.definition == "order_sku"), "lines"].iat[0])
    if merged:
        st.warning(f"{merged:,} rows repeat an order + SKU already on that order: the two definitions differ.",
                   icon="⚠️")
    else:
        st.success("No order has the same SKU on more than one row: both definitions give the same answer.", icon="✅")
    show = prof[["channel", "definition", "orders", "lines", "lines_per_order", "units_per_line", "units_per_order",
                 "picks_per_line", "single_line_share"]]
    st.dataframe(show, hide_index=True, width="stretch", column_config={
        "orders": st.column_config.NumberColumn(format="localized"),
        "lines": st.column_config.NumberColumn(format="localized"),
        "lines_per_order": st.column_config.NumberColumn("lines / order", format="%.2f"),
        "units_per_line": st.column_config.NumberColumn("units / line", format="%.2f"),
        "units_per_order": st.column_config.NumberColumn("units / order", format="%.2f"),
        "picks_per_line": st.column_config.NumberColumn("picks (rows) / line", format="%.2f"),
        "single_line_share": st.column_config.NumberColumn("single-line orders", format="percent")})
    st.caption("Units include full cases (cases x case pack) when the file has a cases column. Units per order is "
               "the same under both definitions; only how units split into lines changes.")


def page():
    st.title("1 · Ingest")
    st.caption("Client files in, canonical tables out (inches, pounds, dates). Problems that make the data "
               "untrustworthy are **blocks**: ingest refuses until you fix the file or accept the block. "
               "Rows it can't use are set aside with a reason, never dropped silently.")

    with st.sidebar:
        st.header("Inputs")
        mode = st.radio("Files from", ["Drop files", "A folder"], horizontal=True, key="ing::mode",
                        help="Drop files: saved under data/workspace/<client> on this laptop (never committed). "
                             "A folder: files already on disk, e.g. very large exports.")
        if mode == "Drop files":
            client = st.text_input("Client", value="untitled", key="ing::client",
                                   help="Dropped files are kept in data/workspace/<client>.")
            folder = _client_dir(client)
        else:
            folders = _folders()
            choice = st.selectbox("Folder", folders + ["Other folder..."], key="ing::folder",
                                  index=folders.index("data/synthetic/default") if "data/synthetic/default" in folders
                                  else 0)
            folder = Path(st.text_input("Folder path", value=str(ROOT / "data"))) if choice == "Other folder..." \
                else ROOT / choice
        wms = _profiles(folder)
        names = list(wms) + [AUTO]
        local = [n for n in names if n.startswith("local: ")]
        default = local[0] if local else ("synthetic" if "synthetic" in names and "synthetic" in str(folder)
                                          else AUTO)
        wchoice = st.selectbox("WMS profile", names, index=names.index(default), key=f"wmspick::{folder}",
                               help="Column names, units and date format for a client system. 'local:' profiles "
                                    "live next to the client's data and are never committed.")
        profile_text = None
        if wchoice != AUTO:
            with st.expander("Edit WMS profile for this run"):
                profile_text = st.text_area("YAML", wms[wchoice].read_text(encoding="utf-8"),
                                            height=320, key=f"wms::{wchoice}::{folder}",
                                            help="e.g. declare units, a date_format, or columns: for a table "
                                                 "whose columns were not recognised.")
        with st.expander("Parameters (IngestParams)"):
            params = _param_controls()
        known_blocks = st.session_state.get("block_codes", [])
        kept = [c for c in st.session_state.get("accepted", []) if c in known_blocks]
        accept = st.multiselect("Accept blocks", known_blocks, default=kept, help="Codes you've checked and "
                                "accept. They stay in the log as 'accepted'.")
        st.session_state["accepted"] = accept  # survives the option list growing

    # -- 1. files
    if mode == "Drop files":
        gen = st.session_state.get("up_gen", 0)  # new key after "Remove" so the uploader forgets the files
        ups = st.file_uploader("Drop client files here", accept_multiple_files=True, key=f"up::{folder.name}::{gen}",
                               help="Any of: " + "; ".join(f"{k} ({v})" for k, v in SUPPORTED))
        _save_uploads(ups, folder)
    files = _files(folder)
    if not files:
        st.info("Drop the client's files above: item master, order history, inbound / receipts, inventory. "
                "Any common export works: CSV or other delimited text, Excel (any sheet), JSON, Parquet, "
                "Feather/Arrow, ORC, SQLite or DuckDB files, or a zip of them." if mode == "Drop files"
                else f"No files in {folder}.")
        return
    with st.expander(f"{len(files)} file(s) in {folder.relative_to(ROOT) if folder.is_relative_to(ROOT) else folder}",
                     expanded=False):
        st.dataframe(pd.DataFrame({"file": [p.name for p in files],
                                   "MB": [round(p.stat().st_size / 1e6, 1) for p in files]}),
                     hide_index=True, width="stretch")
        if mode == "Drop files" and st.button("Remove these files", icon=":material/delete:"):
            for p in files:
                p.unlink()
            st.session_state["up_gen"] = st.session_state.get("up_gen", 0) + 1
            st.rerun()

    # -- 2. guess, then confirm
    sig = _signature(files)
    st.subheader("What does each file hold?")
    st.caption("Guessed from the column names. Change **Use as** where the guess is wrong; set sheets and tables "
               "you don't need to Ignore. Files of one table must share a layout to be stacked.")
    with st.spinner("Reading headers and guessing..."):
        rows = _guesses(sig, tuple(str(p) for p in files))
    if not rows:
        st.warning("None of these files could be read as data.")
        return
    assign = _sources_table(rows, sig)
    assign_key = tuple(sorted((t, str(s.path), s.part, s.path.stat().st_mtime_ns)
                              for t, srcs in assign.items() for s in srcs))
    confirmed = st.session_state.setdefault("ingest_confirmed", set())
    if sig not in confirmed:
        if st.button("Ingest", type="primary", icon=":material/play_arrow:", key="ing::go",
                     disabled=not assign):
            confirmed.add(sig)
            st.rerun()
        st.caption("After the first run, changes to the table above or the sidebar re-run ingest automatically.")
        return

    # -- 3. results
    st.divider()
    try:
        with st.spinner("Ingesting..."):
            run_key = (assign_key, profile_text, json.dumps(params), tuple(sorted(accept)))
            r = _run(*run_key)
    except Exception as e:  # malformed profile YAML, unreadable file: show it
        st.error(f"Ingest failed: {e}")
        return
    codes = sorted(set(known_blocks) | set(r.issues.loc[r.issues.severity.isin(["block", "accepted"]), "code"]))
    if codes != known_blocks:  # new block codes: redraw so they can be accepted right away
        st.session_state["block_codes"] = codes
        st.rerun()

    iss = r.issues
    nb = int((iss.severity == "block").sum())
    if nb:
        st.error(f"**Refused**: {nb} block(s). Fix the files or accept the block codes in the sidebar.", icon="⛔")
    else:
        st.success(f"**Ready**: {len(r.tables)} tables ingested; "
                   f"{int((iss.severity == 'warn').sum())} warning(s) to look at.", icon="✅")

    cols = st.columns(max(len(r.tables), 1))
    for c, (name, df) in zip(cols, r.tables.items()):
        rej = len(r.rejects.get(name, []))
        c.metric(name, f"{len(df):,} rows", f"{rej:,} set aside" if rej else "none set aside",
                 delta_color="inverse" if rej else "off")

    t_iss, t_tab, t_lines, t_map, t_units, t_dates, t_rej, t_out = st.tabs(
        ["Issues", "Tables", "Order lines", "Column mapping", "Units", "Dates", "Set-aside rows", "Outputs"])

    with t_lines:
        _order_lines_tab(r, run_key)

    with t_iss:
        show = st.pills("Show", list(SEV_LABEL.values()), default=list(SEV_LABEL.values()), selection_mode="multi")
        v = iss.assign(severity=iss.severity.map(SEV_LABEL))
        v = v[v.severity.isin(show or [])]
        st.dataframe(v[["severity", "table", "code", "message", "count", "share"]], hide_index=True,
                     width="stretch", column_config={
                         "message": st.column_config.TextColumn(width="large"),
                         "share": st.column_config.NumberColumn(format="percent")})

    with t_tab:
        if not r.tables:
            st.info("No tables were produced.")
        else:
            name = st.radio("Table", list(r.tables), horizontal=True, key="tab_table")
            df = r.tables[name]
            st.caption(f"{len(df):,} rows. First 1,000 shown.")
            st.dataframe(df.head(1000), width="stretch", hide_index=True)
            st.markdown("**Column summary**")
            summ = pd.DataFrame({
                "type": df.dtypes.astype(str),
                "filled": df.notna().mean(),
                "distinct": df.nunique(),
                "min": [df[c].min() if df[c].notna().any() and df[c].dtype.kind in "iufM" else None for c in df],
                "median": [df[c].median() if df[c].dtype.kind in "iuf" else None for c in df],
                "max": [df[c].max() if df[c].notna().any() and df[c].dtype.kind in "iufM" else None for c in df],
            })
            st.dataframe(summ.astype({"min": str, "max": str}), width="stretch",
                         column_config={"filled": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})

    with t_map:
        for table, m in r.mappings.items():
            st.markdown(f"**{table}** ← `{r.sources.get(table, '')}`")
            rows = []
            for f in TABLES[table]:
                rows.append(dict(field=f.name, required="yes" if f.required else "", kind=f.kind,
                                 column=m.mapping.get(f.name, "—"), matched_by=m.method.get(f.name, "not found"),
                                 also_possible=", ".join(m.alternatives.get(f.name, [])) or ""))
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            if m.unused_headers:
                st.caption(f"Columns not used: {m.unused_headers}")
        if r.mappings:
            st.caption("Running without a WMS profile shows what automatic matching would do.")

    with t_units:
        st.markdown("Units are decided per group: **header** hint → **WMS profile** → **data**. The data check "
                    "runs every time: each length × weight pair below is tried, and a pair *fits* when the "
                    "assortment's median size and density look like consumer goods "
                    f"(longest side {params['unit_longest_side_in'][0]:g}–{params['unit_longest_side_in'][1]:g} in "
                    f"for units, {params['case_longest_side_in'][0]:g}–{params['case_longest_side_in'][1]:g} in for "
                    f"cases; density {params['density_lb_ft3'][0]:g}–{params['density_lb_ft3'][1]:g} lb/ft³).")
        if len(r.units):
            st.dataframe(r.units, hide_index=True, width="stretch")
        ev = r.unit_evidence
        if ev is not None and len(ev):
            g = st.radio("Group", sorted(ev.group.unique(), reverse=True), horizontal=True)
            e = ev[ev.group == g].copy()
            e["fits"] = e.plausible.map({True: "✓ fits", False: "✗"})
            e["declared"] = ((e.length == e.declared_length) | e.declared_length.isna()) & \
                            ((e.weight == e.declared_weight) | e.declared_weight.isna())
            e["declared"] = e["declared"].map({True: "●", False: ""}) if e.declared_length.notna().any() or \
                e.declared_weight.notna().any() else ""
            st.dataframe(e[["length", "weight", "median_longest_in", "median_density_lb_ft3", "fits", "declared"]]
                         .sort_values(["fits", "length"], ascending=[False, True]), hide_index=True,
                         width="stretch", column_config={
                             "median_longest_in": st.column_config.NumberColumn("median longest side (in)", format="%.1f"),
                             "median_density_lb_ft3": st.column_config.NumberColumn("median density (lb/ft³)", format="%.1f")})
            st.caption("● = consistent with what was declared (header or profile). Note in/lb and cm/oz give nearly "
                       "the same density (2.54³ ≈ 16), so size is what separates them.")
        elif "item_master" in r.tables:
            st.info("No dimension columns to check.")

    with t_dates:
        for table, col in [("orders", "order_date"), ("receipts", "receipt_date"), ("inventory", "snapshot_date")]:
            if table not in r.tables or col not in r.tables[table]:
                continue
            d = r.tables[table][col]
            if d.notna().sum() == 0:
                continue
            daily = d.value_counts().sort_index()
            daily = daily.reindex(pd.date_range(daily.index.min(), daily.index.max()), fill_value=0)
            st.markdown(f"**{table}**: rows per day ({daily.index.min():%Y-%m-%d} to {daily.index.max():%Y-%m-%d})")
            st.bar_chart(daily.rename("rows"), height=200, x_label="date", y_label="rows")
            notes = iss[(iss.table == table) & iss.code.isin(["date_gap", "closed_days", "snapshot_gap"])]
            for row in notes.itertuples():
                st.caption(f"{SEV_LABEL[row.severity]} {row.message}")
        if r.date_formats:
            st.caption("Date formats used: " + ", ".join(f"{t}: `{f}`" for t, f in r.date_formats.items()))

    with t_rej:
        any_rej = False
        for name, df in r.rejects.items():
            if not len(df):
                continue
            any_rej = True
            st.markdown(f"**{name}**: {len(df):,} rows set aside")
            st.dataframe(df["reason"].value_counts().rename_axis("reason").reset_index(name="rows"), hide_index=True)
            st.dataframe(df.head(500), hide_index=True, width="stretch")
        if not any_rej:
            st.info("No rows set aside.")

    with t_out:
        if nb:
            st.warning("Outputs are only written when nothing is blocked.")
        else:
            default_out = str(folder / "_ingest")
            out = st.text_input("Write to", default_out)
            if st.button("Write Parquet tables, issues, units, rejects", icon=":material/download:"):
                p = write_outputs(r, out)
                st.success(f"Wrote {p}")
        st.divider()
        st.markdown("**Save this run's mapping as a WMS profile**: useful after an automatic run you've checked.")
        draft = profile_from_result(r, "new_wms")
        name = st.text_input("Profile name", "new_wms")
        draft.name = name
        target = ROOT / "wms_profiles" / f"{name}.yaml"
        st.code(yaml.safe_dump(dataclasses.asdict(draft), sort_keys=False), language="yaml")
        if st.button("Save to wms_profiles/", icon=":material/save:"):
            if target.exists():
                st.error(f"{target.name} exists; pick another name.")
            else:
                save_wms_profile(draft, target)
                st.success(f"Saved {target.relative_to(ROOT)}")
