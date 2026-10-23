"""widgets.py — pieces shared by the Ingest steps: stepper, header, client and detection settings, verdict, block cards,
coverage strip and the detail tabs.  Issue wording comes from gtpfit.ingest.checks.ISSUE_TEXT."""
from __future__ import annotations

import base64
import calendar
import datetime as dt
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from gtpfit.app.theme import HAI_BLUE, HAI_BLUE_4
from gtpfit.ingest.checks import NOT_ACCEPTABLE, explain
from gtpfit.ingest.coverage import sparkline_svg
from gtpfit.ingest.coverage import OUTSIDE_NOTE
from gtpfit.ingest.params import ASSORTMENTS, assortment_tooltip, format_value, meta, tooltip
from gtpfit.ingest.schema import FIELD_LABEL, TABLE_LABEL, TABLES
from gtpfit.lines import LINE_DEFINITION_HELP, LINE_DEFINITIONS, order_profile

from . import state

SEV_LABEL = {"block": "⛔ block", "accepted": "✅ accepted", "warn": "⚠️ warn", "info": "ℹ️ info"}


# Step icons from the Hai Robotics design system's Icons group (app/static/icons/, unchanged files): what comes
# in, is it what we think, can we trust it, packaged outputs.  The set has no check mark, and the brand book
# forbids mixing in other icon libraries, so a step's state shows by colour and the track between steps.
STEP_ICONS = ("inbound", "search", "safety", "package")
_ICONS = Path(__file__).resolve().parents[1] / "static" / "icons"
# Design-system colours per theme: accent (`hai-blue` / `hai-blue-4`), done ink (`black-3c` / `cool-grey-4`),
# upcoming icon and label (`cool-grey-8` / `cool-grey-11` and the reverse in dark), track (`cool-grey-4` / `black-3c`).
_STEP_COLOURS = {False: dict(now=HAI_BLUE, done="#373737", next_icon="#AAAAAA", next_text="#5A5A5A", track="#E6E6E6"),
                 True: dict(now=HAI_BLUE_4, done="#E6E6E6", next_icon="#5A5A5A", next_text="#AAAAAA", track="#373737")}


def _icon(name: str, colour: str) -> str:
    """The design-system icon in one of its allowed colours, as an <img> data URI (st.html drops inline <svg>)."""
    svg = (_ICONS / f"{name}.svg").read_text(encoding="utf-8").replace('stroke="#231815"', f'stroke="{colour}"')
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img src="data:image/svg+xml;base64,{data}" width="26" height="26" alt="">'


def stepper_html(current: int, steps, dark: bool) -> str:
    """The step row: icon and name per step, joined by a track that is accent-coloured up to the current step."""
    c = _STEP_COLOURS[dark]
    parts = []
    for i, name in enumerate(steps, start=1):
        cls = "done" if i < current else "now" if i == current else "next"
        cur = ' aria-current="step"' if cls == "now" else ""
        icon = _icon(STEP_ICONS[(i - 1) % len(STEP_ICONS)], c["now"] if cls == "now" else c["done"] if cls == "done"
                     else c["next_icon"])
        if i > 1:
            parts.append(f'<span class="track{" on" if i <= current else ""}"></span>')
        parts.append(f'<span class="step {cls}"{cur}>{icon}<span class="name">{i} · {escape(name)}</span></span>')
    css = (f".hai-steps{{display:flex;align-items:center;gap:10px;margin:2px 0 10px;flex-wrap:wrap}}"
           f".hai-steps .step{{display:inline-flex;align-items:center;gap:8px;font-size:15px;white-space:nowrap}}"
           f".hai-steps .done{{color:{c['done']}}}.hai-steps .now{{color:{c['now']};font-weight:600}}"
           f".hai-steps .next{{color:{c['next_icon']}}}.hai-steps .next .name{{color:{c['next_text']}}}"
           f".hai-steps .track{{flex:1;min-width:24px;height:2px;background:{c['track']}}}"
           f".hai-steps .track.on{{background:{c['now']}}}")
    return f'<style>{css}</style><nav class="hai-steps" aria-label="Ingest steps">{"".join(parts)}</nav>'


def stepper(current: int) -> None:
    st.html(stepper_html(current, state.STEPS, getattr(st.context.theme, "type", None) == "dark"))


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
        typed = st.session_state.get("ing::client_pick")
        if typed and typed not in recent:  # a new name typed earlier stays a choice on every rerun
            recent = [typed] + recent
        # one box: pick a recent client, or type a new name and press Enter (accept_new_options)
        pick = st.selectbox("Client", recent, index=0 if recent else None, key="ing::client_pick",
                            accept_new_options=True, placeholder="Type a client name and press Enter",
                            help="Pick a recent client, or type a new name and press Enter to add one. Outputs "
                                 "and the client's confirmed column mapping and units are kept under "
                                 "data/workspace/<client>/ on this computer and reused next time.")
        st.session_state["ing::client"] = state.safe_name(pick or "")
    c3.markdown("🔒 **Data stays on this computer**")


def _bands_text(name: str) -> str:
    b = ASSORTMENTS[name]
    return ", ".join(f"{meta(k)['label'].split(' (')[0].lower()} {format_value(b[k], meta(k)['unit'])}"
                     for k in state.BAND_FIELDS)


def control(name: str, value, key: str, cls=None):
    """One parameter field as a widget, drawn from its setting() metadata (label, unit, range) with its tooltip.
    cls: the parameter dataclass (IngestParams by default; the Scope page passes ScopeParams)."""
    m = meta(name, cls)
    label, unit, tip = m["label"], m["unit"], tooltip(name, cls)
    if unit == "months":
        return sorted(st.multiselect(label, list(range(1, 13)), default=list(value), key=key, help=tip,
                                     format_func=lambda x: calendar.month_name[x]))
    if unit == "bool":
        shown = st.toggle(label, value=(not value) if m.get("invert") else bool(value), key=key, help=tip)
        return (not shown) if m.get("invert") else shown
    if unit == "rows":
        txt = st.text_input(label + " (comma-separated)", ", ".join(str(x) for x in value), key=key, help=tip)
        return [int(x) for x in txt.replace(" ", "").split(",") if x.strip().isdigit()]
    if unit == "numbers":
        txt = st.text_input(label + " (comma-separated)", ", ".join(f"{v:g}" for v in value), key=key, help=tip)
        vals = []
        for x in txt.split(","):
            try:
                vals.append(float(x))
            except ValueError:
                pass
        return vals
    if unit == "words":
        txt = st.text_input(label + " (comma-separated)", ", ".join(value), key=key, help=tip)
        return [w.strip() for w in txt.split(",") if w.strip()]
    if unit == "share":
        pct = st.number_input(label + " (%)", min_value=m["lo"] * 100, max_value=m["hi"] * 100,
                              value=float(value) * 100, step=m["step"] * 100, format="%.1f", key=key, help=tip)
        return round(pct / 100, 6)
    if unit in ("days", "count"):
        return int(st.number_input(label, min_value=m["lo"], max_value=m["hi"], value=int(value), step=m["step"],
                                   key=key, help=tip))
    return float(st.number_input(label, min_value=float(m["lo"]), max_value=float(m["hi"]), value=float(value),
                                 step=float(m["step"]), format="%.2f", key=key, help=tip))


def _save(new: dict) -> None:
    if new != state.client_settings():
        state.save_client_settings(new)
    st.session_state["ing::settings"] = new


def _reset(prefix: str, keys) -> None:
    for k in [k for k in st.session_state.keys() if str(k).startswith(prefix)]:
        del st.session_state[k]
    base = state.default_settings()
    _save({**state.settings(), **{k: base[k] for k in keys}})
    st.rerun()


def client_settings(hint=None) -> None:
    """'Client settings' on Confirm, always shown and saved for the client: the fields whose metadata says
    block="client" (the unit bands through the assortment preset), plus the line definition.  hint: the
    busiest-months PeakHint for the confirmed orders, or a message when there is none."""
    cur = dict(state.settings())
    keys = list(state.CLIENT_FIELDS) + list(state.EXTRA_SETTINGS)
    new = dict(cur)
    with st.container(border=True):
        top = st.container()
        a, b = st.columns(2)
        with a:
            for name in state.CLIENT_FIELDS:
                if name == "upper_case_skus":
                    continue  # drawn beside the assortment
                new[name] = control(name, cur[name], f"cs::{name}")
                if name == "peak_months":
                    _peak_hint(hint, new[name])
        with b:
            names = list(ASSORTMENTS)
            new["assortment"] = st.selectbox("Assortment type", names, index=names.index(cur["assortment"])
                                             if cur["assortment"] in names else 0, key="cs::assortment",
                                             help=assortment_tooltip())
            st.caption(f"Checks units against: {_bands_text(new['assortment'])}.")
            new["upper_case_skus"] = control("upper_case_skus", cur["upper_case_skus"], "cs::upper_case_skus")
            defs = list(LINE_DEFINITIONS)
            new["line_definition"] = st.radio("What counts as an order line", defs,
                                              index=defs.index(cur["line_definition"]), key="cs::line_definition",
                                              captions=[LINE_DEFINITIONS[d] for d in defs],
                                              help=LINE_DEFINITION_HELP)
        _save(new)
        n = len(state.changed(new, keys))
        c1, c2 = top.columns([5, 1.3], vertical_alignment="center")
        c1.markdown("**Client settings**" + (f" · {n} differ from defaults" if n else " · all defaults"))
        if c2.button("Reset to defaults", key="cs::reset", disabled=not n):
            _reset("cs::", keys)
        top.caption(f"Saved for {state.client()} and used every time. Hover a setting for what it does.")


def _peak_hint(hint, peak) -> None:
    """The busiest-months hint under Peak months; the outside note is judged against the months set now."""
    if hint is None:
        return
    if isinstance(hint, str):
        st.caption(hint)
        return
    st.caption(hint.text + " A hint only: it does not change the peak months.")
    if hint.months and not set(hint.months) & set(peak):
        st.markdown(f":orange[{OUTSIDE_NOTE}]")


def detection_settings() -> None:
    """'Detection settings', collapsed: the fields whose metadata says block="detection", saved for the client,
    with how many differ from the defaults and a Reset."""
    cur = dict(state.settings())
    keys = list(state.DETECTION_FIELDS)
    n_before = len(state.changed(cur, keys))
    new = dict(cur)
    with st.expander("Detection settings" + (f" ({n_before} changed)" if n_before else "")):
        top = st.container()
        for name in keys:
            new[name] = control(name, cur[name], f"ds::{name}")
        _save(new)
        n = len(state.changed(new, keys))
        if n != n_before:
            st.rerun()  # an expander's label is drawn before its contents: redraw it with the new count
        c1, c2 = top.columns([5, 1.3], vertical_alignment="center")
        c1.caption("Defaults suit most clients." + (f" {n} differ from defaults." if n else ""))
        if c2.button("Reset to defaults", key="ds::reset", disabled=not n):
            _reset("ds::", keys)


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
                if first.code in NOT_ACCEPTABLE:  # accepting would pass the sizes through unconverted
                    c2.caption("This block can't be accepted as it is: the sizes would go through unconverted, "
                               "read as inches and pounds. Choose the units above, or ask the client.")
                else:
                    with c2.expander("Accept anyway…"):
                        why = st.text_input("Reason (required)", key=f"accwhy::{key}",
                                            placeholder="e.g. client confirmed the extract is complete")
                        if st.button("Confirm", key=f"accok::{key}", disabled=not why.strip()):
                            accept[key] = {"reason": why.strip(),
                                           "at": dt.datetime.now().isoformat(timespec="seconds")}
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
