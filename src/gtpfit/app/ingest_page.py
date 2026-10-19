"""ingest_page.py — run ingest on a folder with live parameters; inspect issues, mapping, units, dates, rejects."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from gtpfit.app import ROOT
from gtpfit.ingest import (IngestParams, ingest_folder, parse_wms_profile, profile_from_result,
                           save_wms_profile, write_outputs)
from gtpfit.ingest.schema import TABLES
from gtpfit.lines import LINE_DEFINITIONS, order_profile

SEV_LABEL = {"block": "⛔ block", "accepted": "✅ accepted", "warn": "⚠️ warn", "info": "ℹ️ info"}
AUTO = "(none: match columns automatically)"


_DATA_EXT = {".csv", ".txt", ".tsv", ".xlsx", ".xlsm", ".parquet"}


def _folders():
    """Folders under data/ that hold client-style files (skips _ingest, _truth, _benchmark...)."""
    base = ROOT / "data"
    if not base.exists():
        return []
    out = []
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base)
        if p.is_dir() and len(rel.parts) <= 3 and not any(x.startswith("_") for x in rel.parts) and                 any(c.suffix.lower() in _DATA_EXT for c in p.iterdir() if c.is_file()):
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


def _signature(folder: Path) -> str:
    """Changes when any file in the folder changes, so the cached run is refreshed."""
    return json.dumps(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size)
                             for p in folder.iterdir() if p.is_file()))


@st.cache_resource(max_entries=6, show_spinner=False)
def _run(folder: str, sig: str, profile_text: str | None, params_json: str, accept: tuple):
    profile = parse_wms_profile(yaml.safe_load(profile_text) or {}) if profile_text else None
    params = IngestParams(**_params_from_json(params_json))
    return ingest_folder(folder, profile=profile, params=params, accept=accept, strict=False)


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
        folders = _folders()
        choice = st.selectbox("Client folder", folders + ["Other folder..."],
                              index=folders.index("data/synthetic/default") if "data/synthetic/default" in folders else 0)
        folder = st.text_input("Folder path", value=str(ROOT / "data")) if choice == "Other folder..." else str(ROOT / choice)
        wms = _profiles(Path(folder))
        names = list(wms) + [AUTO]
        local = [n for n in names if n.startswith("local: ")]
        default = local[0] if local else ("synthetic" if "synthetic" in names and "synthetic" in folder else names[0])
        wchoice = st.selectbox("WMS profile", names, index=names.index(default), key=f"wmspick::{folder}",
                               help="'local:' profiles live next to the client's data and are never committed.")
        profile_text = None
        if wchoice != AUTO:
            with st.expander("Edit WMS profile for this run"):
                profile_text = st.text_area("YAML", wms[wchoice].read_text(encoding="utf-8"),
                                            height=320, key=f"wms::{wchoice}::{folder}",
                                            help="Try changing units to cm, or removing date_format.")
        with st.expander("Parameters (IngestParams)"):
            params = _param_controls()
        known_blocks = st.session_state.get("block_codes", [])
        kept = [c for c in st.session_state.get("accepted", []) if c in known_blocks]
        accept = st.multiselect("Accept blocks", known_blocks, default=kept, help="Codes you've checked and "
                                "accept. They stay in the log as 'accepted'.")
        st.session_state["accepted"] = accept  # survives the option list growing

    fpath = Path(folder)
    if not fpath.is_dir():
        st.warning(f"Not a folder: {folder}")
        return
    try:
        with st.spinner("Ingesting..."):
            run_key = (str(fpath), _signature(fpath), profile_text, json.dumps(params), tuple(sorted(accept)))
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
            default_out = str(Path(folder) / "_ingest")
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
