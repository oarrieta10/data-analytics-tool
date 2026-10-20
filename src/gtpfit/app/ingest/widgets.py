"""widgets.py — pieces shared by the Ingest steps: stepper, header, client settings, verdict, block cards,
coverage strip and the detail tabs.  Issue wording comes from gtpfit.ingest.checks.ISSUE_TEXT."""
from __future__ import annotations

import calendar
import dataclasses
import datetime as dt

import pandas as pd
import streamlit as st

from gtpfit.ingest import IngestParams
from gtpfit.ingest.checks import explain
from gtpfit.ingest.coverage import sparkline_svg
from gtpfit.ingest.params import ASSORTMENTS
from gtpfit.ingest.schema import FIELD_LABEL, TABLE_LABEL, TABLES
from gtpfit.lines import LINE_DEFINITIONS, order_profile

from . import state

SEV_LABEL = {"block": "⛔ block", "accepted": "✅ accepted", "warn": "⚠️ warn", "info": "ℹ️ info"}


def stepper(current: int) -> None:
    parts = []
    for i, name in enumerate(state.STEPS, start=1):
        mark = "✓" if i < current else str(i)
        parts.append(f"**({mark}) {name}**" if i == current else f"({mark}) {name}")
    st.markdown(" — ".join(parts))


def header(editable: bool) -> None:
    """Client · local-data badge.  Editable on Upload; a summary line afterwards."""
    if not editable:
        prof = state.client_profile()
        memory = f"mapping remembered from {prof.saved_at[:10]}" if prof and prof.saved_at else \
            "first ingest: columns matched automatically"
        st.caption(f"Client: **{state.client()}** · {memory} · 🔒 Data stays on this computer")
        return
    c1, c3 = st.columns([3, 1.2], vertical_alignment="bottom")
    with c1:
        recent = state.recent_clients()
        options = recent + [state.NEW_CLIENT]
        pick = st.selectbox("Client", options, key="ing::client_pick",
                            help="Required. Outputs and the client's confirmed column mapping and units are kept "
                                 "under data/workspace/<client>/ on this computer and reused next time.")
        if pick == state.NEW_CLIENT:
            name = st.text_input("New client name", key="ing::client_new", placeholder="e.g. Acme Retail")
        else:
            name = pick
        st.session_state["ing::client"] = state.safe_name(name)
    c3.markdown("🔒 **Data stays on this computer**")


def _bands_text(name: str) -> str:
    b = ASSORTMENTS[name]
    u, c, d = b["unit_longest_side_in"], b["case_longest_side_in"], b["density_lb_ft3"]
    return f"unit {u[0]:g}–{u[1]:g} in, case {c[0]:g}–{c[1]:g} in, {d[0]:g}–{d[1]:g} lb/ft³"


def client_settings() -> None:
    """'Client settings' on Confirm: the few choices that differ by client, saved to its client.yaml, plus a
    collapsed 'Engine settings' with every other IngestParams field (this session only).  Shows how many
    values differ from the defaults, with a Reset."""
    base, saved = state.default_settings(), state.client_settings()
    cur = st.session_state.get("ing::settings") or saved
    with st.container(border=True):
        top = st.container()
        new = {}
        a, b = st.columns(2)
        with a:
            new["peak_months"] = sorted(st.multiselect(
                "Peak months", list(range(1, 13)), default=list(cur["peak_months"]), key="cs::peak_months",
                format_func=lambda m: calendar.month_name[m]))
            st.caption("Orders must include these months, or the design day misses the busiest season.")
            new["max_gap_days_orders"] = int(st.number_input(
                "Longest tolerated gap in orders (days)", min_value=0, value=int(cur["max_gap_days_orders"]),
                step=1, key="cs::max_gap_days_orders"))
            st.caption("A longer run of days with no orders is a missing slice of the extract, not a closure.")
            pct = st.number_input("Share of unusable rows before a table is refused (%)", min_value=0.0,
                                  max_value=100.0, value=float(cur["max_bad_row_share"]) * 100, step=0.5,
                                  format="%.1f", key="cs::max_bad_row_share")
            new["max_bad_row_share"] = round(pct / 100, 6)
            st.caption("Above this, too many rows are set aside to trust the volumes.")
        with b:
            names = list(ASSORTMENTS)
            new["assortment"] = st.selectbox("Assortment type", names, index=names.index(cur["assortment"])
                                             if cur["assortment"] in names else 0, key="cs::assortment")
            st.caption(f"Sets the ranges used to check units: {_bands_text(new['assortment'])}.")
            new["upper_case_skus"] = not st.toggle("SKUs are case-sensitive", value=not cur["upper_case_skus"],
                                                   key="cs::case_sensitive")
            st.caption("Off: “ab-1” and “AB-1” are one SKU. On: they stay separate SKUs.")
            defs = list(LINE_DEFINITIONS)
            new["line_definition"] = st.radio("What counts as an order line", defs,
                                              index=defs.index(cur["line_definition"]), key="cs::line_definition",
                                              captions=[LINE_DEFINITIONS[d] for d in defs])
            st.caption("Agreed with the client; it changes lines per order and pick counts downstream.")
        engine = _engine_settings()
        if new != saved:
            state.save_client_settings(new)
        st.session_state["ing::settings"] = new
        n = sum(new[k] != base[k] for k in base) + len(engine)
        c1, c2 = top.columns([5, 1.3], vertical_alignment="center")
        c1.markdown("**Client settings**" + (f" · {n} differ from defaults" if n else " · all defaults"))
        if c2.button("Reset to defaults", key="cs::reset", disabled=not n):
            for k in [k for k in st.session_state.keys() if str(k).startswith(("cs::", "thr::"))]:
                del st.session_state[k]
            st.session_state["ing::settings"], st.session_state["ing::engine"] = base, {}
            state.save_client_settings(base)
            st.rerun()
        top.caption(f"Saved for {state.client()} and used every time.")


def _engine_settings() -> dict:
    """Every IngestParams field the client settings don't cover, collapsed; returns only the changed ones."""
    base = dataclasses.asdict(IngestParams())
    cur = {**base, **(st.session_state.get("ing::engine") or {})}
    new = {}
    with st.expander("Engine settings (rarely needed)"):
        st.caption("This session only. Each default and its source is in gtpfit/ingest/params.py.")
        for name, v in base.items():
            if name in state.CLIENT_FIELDS:
                continue
            label = name.replace("_", " ")
            key = f"thr::{name}"
            val = cur.get(name, v)
            if name == "max_gap_days":  # the orders gap is a client setting
                v = {k: x for k, x in v.items() if k != "orders"}
            if name == "excel_row_limits":
                txt = st.text_input(label, ", ".join(str(x) for x in val), key=key,
                                    help="Row counts that mean a clipped export.")
                new[name] = tuple(int(x) for x in txt.replace(" ", "").split(",") if x.strip().isdigit())
            elif isinstance(v, bool):
                new[name] = st.checkbox(label, val, key=key)
            elif isinstance(v, int):
                new[name] = int(st.number_input(label, value=int(val), step=1, key=key))
            elif isinstance(v, float):
                new[name] = float(st.number_input(label, value=float(val), format="%.4f", step=v / 10 or 0.01, key=key))
            elif isinstance(v, dict):
                new[name] = {k: int(st.number_input(f"{label}: {k}", value=int(val.get(k, x)), step=1,
                                                    key=f"{key}::{k}")) for k, x in v.items()}
            elif isinstance(v, tuple) and len(v) == 2:
                a, b = st.columns(2)
                new[name] = (float(a.number_input(f"{label}: low", value=float(val[0]), key=f"{key}::lo")),
                             float(b.number_input(f"{label}: high", value=float(val[1]), key=f"{key}::hi")))
            else:
                new[name] = val
    if "max_gap_days" in new:
        new["max_gap_days"] = {**base["max_gap_days"], **new["max_gap_days"]}
    changed = {k: x for k, x in new.items() if not json_eq(x, base[k])}
    st.session_state["ing::engine"] = changed
    return changed


def json_eq(a, b) -> bool:
    norm = lambda x: list(x) if isinstance(x, tuple) else x
    return norm(a) == norm(b)


# ---- review pieces ---------------------------------------------------------------------------

def verdict(result) -> None:
    iss = result.issues
    nb, nw, ni = (int((iss.severity == s).sum()) for s in ("block", "warn", "info"))
    na = int((iss.severity == "accepted").sum())
    tail = f"{nw} warning(s) · {ni} note(s)" + (f" · {na} accepted" if na else "")
    if nb:
        st.error(f"**BLOCKED** — {nb} decision(s) needed · {tail}", icon="⛔")
    elif nw or na:
        st.warning(f"**Proceed with cautions** — {tail}", icon="⚠️")
    else:
        st.success(f"**Clean** — {tail}", icon="✅")


def block_key(table, code) -> str:
    return f"{table or ''}:{code}"


def block_cards(result) -> None:
    """One card per open or accepted block: what happened, design impact, actions."""
    iss = result.issues[result.issues.severity.isin(["block", "accepted"])]
    accept = st.session_state.setdefault("ing::accept", {})
    requested = st.session_state.setdefault("ing::requested", [])
    for key, grp in iss.groupby(iss.apply(lambda r: block_key(r.table, r.code), axis=1), sort=False):
        first = grp.iloc[0]
        t = explain(first.code)
        is_open = (grp.severity == "block").any()
        with st.container(border=True):
            tag = "⛔ BLOCK" if is_open else "✅ ACCEPTED"
            where = TABLE_LABEL.get(first.table, "Files") if first.table else "Files"
            st.markdown(f"**{tag} · {t.title}** <span style='opacity:.7'>({where})</span>", unsafe_allow_html=True)
            for m in grp.message:
                st.write(m)
            if t.what:
                st.caption(t.what)
            st.markdown(f"**Design impact:** {t.impact}")
            if is_open and first.code in UNIT_CODES:
                _unit_picker(result, key)
            if is_open:
                c1, c2 = st.columns([1, 2])
                asked = key in requested
                if c1.button("Asked client to resend ✓" if asked else "Ask client to resend", key=f"req::{key}",
                             help="Records the request (it appears in the data receipt); the block stays open."):
                    requested.remove(key) if asked else requested.append(key)
                    st.rerun()
                with c2.expander("Accept anyway…"):
                    why = st.text_input("Reason (required)", key=f"accwhy::{key}",
                                        placeholder="e.g. client confirmed the extract is complete")
                    if st.button("Confirm", key=f"accok::{key}", disabled=not why.strip()):
                        accept[key] = {"reason": why.strip(), "at": dt.datetime.now().isoformat(timespec="seconds")}
                        st.rerun()
            else:
                a = accept.get(key) or accept.get(first.code) or {}
                st.markdown(f"Accepted: *{a.get('reason', '')}* ({a.get('at', '')})")
                if st.button("Undo", key=f"accundo::{key}"):
                    accept.pop(key, None)
                    st.rerun()


UNIT_CODES = ("unit_ambiguous", "unit_mismatch")


def unit_options(evidence) -> list[tuple]:
    """(length, weight, label) for the unit pairs that fit the data (all pairs if none fits), best first."""
    if evidence is None or not len(evidence):
        return []
    ev = evidence[evidence["group"] == "unit"] if (evidence["group"] == "unit").any() else evidence
    fit = ev[ev["plausible"] & ev["evidence"]]
    rows = fit if len(fit) else ev
    out = []
    for r in rows.itertuples():
        bits = []
        if r.median_longest_in == r.median_longest_in:  # not NaN
            bits.append(f"median longest side {r.median_longest_in:.1f} in")
        if r.median_density_lb_ft3 == r.median_density_lb_ft3:
            bits.append(f"{r.median_density_lb_ft3:.1f} lb/ft³")
        out.append((r.length, r.weight, f"{r.length} / {r.weight}" + (f" ({', '.join(bits)})" if bits else "")))
    return out


def _unit_picker(result, key: str) -> None:
    """Decide the units here; the choice is saved for the client and used for every later file."""
    opts = unit_options(result.unit_evidence)
    if not opts:
        return
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    pick = c1.selectbox("Units this client uses (pairs that fit the data)", [o[2] for o in opts],
                        key=f"unitpick::{key}")
    if c2.button("Use these units", key=f"unituse::{key}", type="primary"):
        length, weight, _ = next(o for o in opts if o[2] == pick)
        units = {"length": length, "weight": weight}
        st.session_state["ing::units"] = units
        state.save_client_profile(units=units)
        st.rerun()


def coverage_panel(result) -> None:
    c = result.coverage
    with st.container(border=True):
        st.markdown("**COVERAGE**")
        if c is None or c.first is None:
            st.info("No dated orders to show.")
            return
        st.markdown(f"{c.first:%b %d, %Y} → {c.last:%b %d, %Y} · {c.span_days:,} days, {c.days_with_rows:,} with orders")
        st.markdown(f"<div style='color:inherit'>{sparkline_svg(c, height=60)}</div>", unsafe_allow_html=True)
        st.caption("Order lines per day; shaded = days with no orders (darker = longer than a normal closure).")
        for w in c.warnings:
            st.warning(w, icon="⚠️")
        st.markdown(f"**{c.lines:,}** lines · **{c.orders:,}** orders · **{c.skus:,}** SKUs · **{c.days_with_rows:,}** "
                    f"days")


def issue_list(df: pd.DataFrame) -> None:
    for r in df.itertuples():
        t = explain(r.code)
        where = TABLE_LABEL.get(r.table, "Files") if r.table else "Files"
        st.markdown(f"**{t.title}** ({where}): {r.message}")
        if r.severity == "warn" and t.impact:
            st.caption(f"Design impact: {t.impact}")


@st.cache_resource(max_entries=4, show_spinner=False)
def _line_profiles(key: str, _orders: pd.DataFrame) -> pd.DataFrame:
    return pd.concat([order_profile(_orders, how) for how in LINE_DEFINITIONS], ignore_index=True)


def details(result, key: str) -> None:
    """The full detail views: tables, order lines, mapping, units, dates."""
    t_tab, t_lines, t_map, t_units, t_dates = st.tabs(["Tables", "Order lines", "Column mapping", "Units", "Dates"])
    with t_tab:
        if not result.tables:
            st.info("No tables were produced.")
        else:
            name = st.radio("Table", list(result.tables), horizontal=True, key="det::table",
                            format_func=lambda t: TABLE_LABEL.get(t, t))
            df = result.tables[name]
            st.caption(f"{len(df):,} rows. First 1,000 shown.")
            st.dataframe(df.head(1000), width="stretch", hide_index=True)
    with t_lines:
        st.markdown(f"What counts as an order line is **agreed with the client**: **{state.line_definition()}** "
                    "(set on Confirm). Both definitions are shown.")
        if "orders" in result.tables:
            prof = _line_profiles(key, result.tables["orders"])
            st.dataframe(prof[["channel", "definition", "orders", "lines", "lines_per_order", "units_per_line",
                               "units_per_order", "picks_per_line"]], hide_index=True, width="stretch",
                         column_config={c: st.column_config.NumberColumn(format="%.2f") for c in
                                        ("lines_per_order", "units_per_line", "units_per_order", "picks_per_line")})
    with t_map:
        for table, m in result.mappings.items():
            st.markdown(f"**{TABLE_LABEL.get(table, table)}** ← `{result.sources.get(table, '')}`")
            st.dataframe(pd.DataFrame([dict(field=FIELD_LABEL.get(f.name, f.name), required="yes" if f.required else "",
                                            column=m.mapping.get(f.name, "—"),
                                            matched_by=m.method.get(f.name, "not found")) for f in TABLES[table]]),
                         hide_index=True, width="stretch")
    with t_units:
        if len(result.units):
            st.dataframe(result.units, hide_index=True, width="stretch")
        else:
            st.info("No sizes to check.")
    with t_dates:
        for table, col in [("orders", "order_date"), ("receipts", "receipt_date"), ("inventory", "snapshot_date")]:
            if table in result.tables and col in result.tables[table] and result.tables[table][col].notna().any():
                d = result.tables[table][col].value_counts().sort_index()
                d = d.reindex(pd.date_range(d.index.min(), d.index.max()), fill_value=0)
                st.markdown(f"**{TABLE_LABEL[table]}**: rows per day")
                st.bar_chart(d.rename("rows"), height=180)
