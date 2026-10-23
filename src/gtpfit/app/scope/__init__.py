"""scope — the workbench Scope page: what part of the client's operation the design is for.

Filters on the scope columns ingest kept (site, channel, order status, order
type, x_ extras) and a date window; shows what each filter removes, what it
does to the headline numbers and to coverage, and saves the scope as a
definition in the client's client.yaml (gtpfit.scope).  The analysis itself
is in gtpfit.scope; this package only lays it out.
"""
from __future__ import annotations

import dataclasses
import datetime as dt

import pandas as pd
import streamlit as st

from gtpfit import workspace
from gtpfit.ingest.coverage import OUTSIDE_NOTE, busiest_months, coverage
from gtpfit.scope import (ScopeParams, ScopeSpec, apply_scope, default_spec, detect_scope_columns, impact,
                          load_scope, params_diff, same_scope, save_scope)

from . import state

_DAYS_12M = 365  # "Last 12 months": the window ending on the last order date


def _header() -> str | None:
    ready = state.ready_clients()
    st.title("1b · Scope")
    st.caption("What are we designing for? Pick the sites, channels and order types the design must serve. "
               "Everything after this step (quality, profile, sizing) sees only the rows in scope.")
    if not ready:
        st.info("No client has finished Ingest yet. Run Ingest first; Scope then opens for that client.",
                icon=":material/info:")
        if st.button("Go to Ingest", key="scp::to_ingest", icon=":material/input:"):
            from gtpfit.app import pages
            st.switch_page(pages.INGEST)
        return None
    want = state.client() or st.session_state.get("ing::client", "")
    idx = ready.index(want) if want in ready else 0
    c1, c2 = st.columns([3, 1.2], vertical_alignment="bottom")
    name = c1.selectbox("Client", ready, index=idx, key="scp::client_pick",
                        help="Clients whose Ingest has finished. The scope is saved with the client.")
    if name != state.client():
        st.session_state["scp::client"] = name
        state.forget_widgets()
    c2.markdown("🔒 **Data stays on this computer**")
    return name


def _filters(cols, start: ScopeSpec, n_lines: int) -> dict:
    """One bordered block per scope column, a checkbox per value.  Returns {column: [included values]} for the
    columns where something is unticked."""
    include = {}
    shown = [c for c in cols if c.known or c.name in start.include or
             c.name in st.session_state.get("scp::extra", [])]
    others = [c for c in cols if c not in shown]
    if not cols:
        st.info("No scope columns found; using all rows. Only the date window below applies.",
                icon=":material/info:")
    for c in shown:
        with st.container(border=True):
            st.markdown(f"**{c.label}**")
            kept = []
            for v in c.values:
                # a column the spec does not filter keeps every value (a fresh default spec already lists the
                # auto-excluded values; a saved or skipped one means what it says)
                default = v.value in start.include[c.name] if c.name in start.include else True
                a, b, d, e = st.columns([3, 1.2, 0.8, 1.6], vertical_alignment="center")
                on = a.checkbox(v.value, value=default, key=f"scp::v::{c.name}::{v.value}")
                b.markdown(f"{v.lines:,} lines")
                d.markdown(f"{v.share:.0%}")
                if v.auto_excluded:
                    e.caption(f"auto-excluded (“{v.auto_excluded}”)")
                if on:
                    kept.append(v.value)
            if len(kept) < len(c.values):
                include[c.name] = kept
    if others:
        pick = st.selectbox("Filter on another column", ["—"] + [c.label for c in others], key="scp::add",
                            help="Extra order columns kept by Ingest (few different values each).")
        if pick != "—":
            st.session_state.setdefault("scp::extra", []).append(next(c.name for c in others if c.label == pick))
            del st.session_state["scp::add"]
            st.rerun()
    return include


def _dates(orders, start: ScopeSpec):
    """Date window slider, 'Last 12 months', and the date basis (only when a ship date is mapped)."""
    basis_opts = ["order_date"] + (["ship_date"] if "ship_date" in orders and orders["ship_date"].notna().any()
                                   else [])
    with st.container(border=True):
        st.markdown("**Date window** (orders only; inventory and receipts keep every date)")
        basis = "order_date"
        if len(basis_opts) > 1:
            basis = st.radio("Date used", basis_opts, horizontal=True, key="scp::basis",
                             index=basis_opts.index(start.date_basis) if start.date_basis in basis_opts else 0,
                             format_func=lambda b: {"order_date": "Order date", "ship_date": "Ship date"}[b])
        d = pd.to_datetime(orders[basis]).dropna()
        lo, hi = d.min().date(), d.max().date()
        if "scp::dates" not in st.session_state:
            st.session_state["scp::dates"] = (
                max(lo, dt.date.fromisoformat(start.date_from)) if start.date_from else lo,
                min(hi, dt.date.fromisoformat(start.date_to)) if start.date_to else hi)
        a, b = st.columns([5, 1.3], vertical_alignment="bottom")
        if b.button("Last 12 months", key="scp::last12"):
            st.session_state["scp::dates"] = (max(lo, hi - dt.timedelta(days=_DAYS_12M - 1)), hi)
            st.rerun()
        frm, to = a.slider("Window", min_value=lo, max_value=hi, key="scp::dates", format="MMM D, YYYY")
    full = frm == lo and to == hi
    return (None if full else frm.isoformat()), (None if full else to.isoformat()), basis


def _waterfall(res) -> None:
    w = res.waterfall
    n = int(w["remaining"].iat[0])
    view = pd.DataFrame({"Step": w["filter"],
                         "Lines removed": ["" if r == 0 else f"−{r:,}" for r in w["removed"]],
                         "Lines left": w["remaining"]})
    with st.container(border=True):
        st.markdown("**What each filter removes**")
        st.dataframe(view, hide_index=True, width="stretch", column_config={
            "Lines left": st.column_config.ProgressColumn("Lines left", min_value=0, max_value=max(n, 1),
                                                          format="%d")})


def _related(res, tables) -> None:
    lines = []
    for t, label in (("inventory", "Inventory"), ("receipts", "Receipts")):
        if t not in tables:
            continue
        if t in res.related:
            lines.append(f"**{label}:** {res.related[t]}")
        else:
            has = "site" in tables[t]
            lines.append(f"**{label}:** " + ("site column found; no site filter set, so all sites are used"
                                             if has else "no site column; sizing uses all sites' rows"))
    if lines:
        with st.container(border=True):
            st.markdown("  \n".join(lines))


def suggested_note(spec: ScopeSpec, cols) -> str:
    """A plain-language scope note from the spec (the consultant can edit it)."""
    parts = []
    for c in cols:
        if c.name in spec.include:
            kept = spec.include[c.name]
            dropped = [v.value for v in c.values if v.value not in kept]
            if c.name == "site" and kept:
                parts.append(f"{' and '.join(kept)} only")
            elif dropped:
                parts.append(f"{c.label.lower()} excludes {', '.join(dropped)}")
    if spec.date_from or spec.date_to:
        parts.append(f"orders from {spec.date_from or 'the start'} to {spec.date_to or 'the end'}")
    return "; ".join(parts) if parts else "All rows: no filter."


def _detection_settings() -> ScopeParams:
    from gtpfit.app.ingest.widgets import control
    cur = state.params()
    base = ScopeParams()
    names = [f.name for f in dataclasses.fields(ScopeParams)]
    n = sum(getattr(cur, k) != getattr(base, k) for k in names)
    new = {}
    with st.expander("Detection settings" + (f" ({n} changed)" if n else "")):
        c1, c2 = st.columns([5, 1.3], vertical_alignment="center")
        c1.caption("Defaults suit most clients. Changes apply when the scope is reset or saved.")
        reset = c2.button("Reset to defaults", key="scpd::reset", disabled=not n)
        for k in names:
            v = control(k, getattr(cur, k), f"scpd::{k}", cls=ScopeParams)
            new[k] = tuple(v) if isinstance(v, list) else v
    if reset:
        for k in [k for k in st.session_state.keys() if str(k).startswith("scpd::")]:
            del st.session_state[k]
        st.session_state["scp::params"] = base
        st.rerun()
    p = ScopeParams(**new)
    if p != cur:
        st.session_state["scp::params"] = p
        st.rerun()
    return p


def page() -> None:
    name = _header()
    if not name:
        return
    base = state.base_tables(name)
    orders = base["orders"]
    params = state.params()
    cols = detect_scope_columns(orders, params)
    start = state.start_spec(name, orders)

    st.subheader("Filters")
    include = _filters(cols, start, len(orders))
    frm, to, basis = _dates(orders, start)
    spec = ScopeSpec(include=include, seen={c.name: [v.value for v in c.values] for c in cols},
                     date_from=frm, date_to=to, date_basis=basis, params=params_diff(params))
    res = apply_scope(base, spec, params)
    for w in res.warnings:
        if not w.startswith("No site column"):
            st.warning(w, icon="⚠️")

    _waterfall(res)
    _impact(res, base, name)
    _related(res, base)

    removed = res.lines_in < res.lines_all
    with st.container(border=True):
        st.markdown("**Scope note** (goes into the report's assumptions)" + (" · required" if removed else ""))
        suggestion = suggested_note(spec, cols)
        if "scp::note" not in st.session_state:
            st.session_state["scp::note"] = start.note or suggestion
        note = st.text_area("Scope note", key="scp::note", label_visibility="collapsed", height=70)
        if note.strip() != suggestion:  # the callback runs before the next run draws the note box
            st.button("Use suggested note", key="scp::suggest", help=suggestion,
                      on_click=lambda: st.session_state.update({"scp::note": suggestion}))
    _actions(name, cols, params, dataclasses.replace(spec, note=note.strip()), res, removed)
    _detection_settings()


def _actions(name: str, cols, params, current: ScopeSpec, res, removed: bool) -> None:
    """Save / skip while the scope on screen differs from the saved one; once they match, go on to Quality."""
    saved = load_scope(name)
    done = st.session_state.pop("scp::just_saved", None)
    if done:
        st.success(done, icon="✅")
    c1, c2, c3, _ = st.columns([1, 1.8, 2.2, 1.3])
    if c1.button("Reset", key="scp::reset", help="Back to every value in, except the auto-excluded ones."):
        state.forget_widgets()
        st.session_state[f"scp::start::{name}"] = default_spec(cols, params)
        st.rerun()
    if same_scope(saved, current):
        if c2.button("Continue to Quality →", type="primary", key="scp::to_quality"):
            st.session_state["qly::client"] = name
            from gtpfit.app import pages
            st.switch_page(pages.QUALITY)
        what = "skipped: every row is used" if saved.skipped else \
            f"saved: {res.lines_in:,} of {res.lines_all:,} order lines in scope"
        c3.caption(f"Scope {what} ({saved.saved_at[:10]}). Change a filter to edit it.")
        return
    blocked = removed and not current.note
    if c2.button("Save scope", type="primary", key="scp::save", disabled=blocked):
        saved = save_scope(name, current, res.summary)
        st.session_state[f"scp::start::{name}"] = saved
        st.session_state["scp::just_saved"] = (
            f"Scope saved for **{name}** ({res.lines_in:,} of {res.lines_all:,} order lines in scope). Every module "
            f"from Quality on reads only these rows. What was left out is listed in "
            f"`{workspace.client_dir(name) / '_scope' / 'scope_summary.csv'}`.")
        st.rerun()
    if c3.button("Skip scope (use all rows)", key="scp::skip",
                 help="Design for every row ingest kept, on purpose. Quality opens either way."):
        save_scope(name, ScopeSpec(skipped=True, note="All rows: no scope set.",
                                   seen={c.name: [v.value for v in c.values] for c in cols}))
        state.forget_widgets()  # every box ticked, no date window, the skip note
        st.session_state[f"scp::start::{name}"] = load_scope(name)
        st.session_state["scp::just_saved"] = (f"No scope for **{name}**: every module from Quality on uses all "
                                               f"{res.lines_all:,} order lines.")
        st.rerun()
    if saved is not None:
        st.caption("Unsaved changes: the saved scope is still the one in use until you save.")
    if blocked:
        st.caption("Write a scope note to save: it explains to the report's readers what was left out.")


_IMPACT_FMT = {"Orders": "{:,.0f}", "Lines per day (average)": "{:,.0f}", "Peak day (lines)": "{:,.0f}",
               "Units per line": "{:,.2f}"}


def _impact(res, base, name) -> None:
    """Does it move the design?  Four cheap numbers, all rows -> in scope, under the client's line definition,
    then the coverage check and busiest-months hint re-run on the in-scope orders (same wording as Ingest)."""
    how = workspace.line_definition(name)
    tiles = impact(base["orders"], res.tables["orders"], how)
    with st.container(border=True):
        st.markdown(f"**Does it move the design?** all rows → in scope · order lines counted as `{how}`")
        for col, r in zip(st.columns(len(tiles)), tiles.itertuples()):
            f = _IMPACT_FMT[r.measure]
            delta = r.in_scope - r.all_rows if pd.notna(r.in_scope) and pd.notna(r.all_rows) else None
            shown = None if delta is None else f.format(abs(delta))
            if shown is not None and float(shown.replace(",", "")) == 0:
                shown = None  # a change too small to show at this precision
            col.metric(r.measure, "—" if pd.isna(r.in_scope) else f.format(r.in_scope),
                       None if shown is None else ("+" if delta > 0 else "-") + shown, delta_color="off")
            col.caption(f"all rows: {'—' if pd.isna(r.all_rows) else f.format(r.all_rows)}")
        params = workspace.client_params(name)
        orders_in = res.tables["orders"]
        cov = coverage(orders_in, params)
        for w in cov.warnings:
            st.warning(w, icon="⚠️")
        if len(orders_in):
            hint = busiest_months(orders_in["order_date"], params.peak_months, params.peak_hint_ratio)
            st.caption(hint.text + " (in-scope orders)")
            if hint.outside:
                st.markdown(f":orange[{OUTSIDE_NOTE}]")
