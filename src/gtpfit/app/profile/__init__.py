"""profile — the workbench Profile page: what the operation looks like and the capacity it must be designed for.

Design capacity first: service targets per channel (done within N working days, a backlog limit), the smallest
daily capacity meeting them (or the percentile day without targets), its KPIs, service by age, the backlog over
time, and a what-if capacity for client conversations.  Then volumes and seasonality, order shape, the hourly
profile, ABC velocity, stock and inbound.  Every number comes from the profile run (gtpfit.profile), which also
writes _profile/; this package only lays it out.  Opens once the client's scope is saved or skipped.
"""
from __future__ import annotations

import dataclasses

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from gtpfit import tables
from gtpfit.app.quality import state as quality_state
from gtpfit.app.theme import HAI_BLUE, HAI_BLUE_4
from gtpfit.ingest.params import block_fields
from gtpfit.profile import ProfileParams, load_sla, save_sla, what_if
from gtpfit.scope import load_scope

from . import state

ALL = "All"
GREY = "#5A5A5A"
ORANGE = "#FF9900"


def _header() -> str | None:
    st.title("4 · Profile")
    st.caption("What the operation looks like, and the daily capacity it must be designed for: the smallest that "
               "still meets the service targets, with the backlog it leaves.")
    ready = quality_state.ready_clients()
    if not ready:
        st.info("No client has finished Ingest yet.", icon=":material/info:")
        return None
    want = state.client() or st.session_state.get("bf::client") or st.session_state.get("qly::client", "")
    c1, c2 = st.columns([3, 1.2], vertical_alignment="bottom")
    name = c1.selectbox("Client", ready, index=ready.index(want) if want in ready else 0, key="pf::client_pick")
    st.session_state["pf::client"] = name
    c2.markdown("🔒 **Data stays on this computer**")
    if load_scope(name) is None:
        st.info(f"Set the scope for **{name}** first (or skip it to use every row): the profile describes only the "
                "work the design is for.", icon=":material/filter_alt:")
        if st.button("Go to Scope", key="pf::to_scope", icon=":material/filter_alt:"):
            st.session_state["scp::client"] = name
            from gtpfit.app import pages
            st.switch_page(pages.SCOPE)
        return None
    return name


@st.cache_data(show_spinner=False, max_entries=8)
def _channels(name: str, sig: str) -> list[str]:
    o = tables.load(name)["orders"]
    return sorted(o["channel"].dropna().astype(str).unique().tolist()) if "channel" in o else []


# ---- service targets ---------------------------------------------------------------------------------------

def _targets(name: str, channels: list[str], unit: str) -> dict:
    """The per-channel targets, edited in two tables and saved for the client when they change."""
    saved = load_sla(name)
    opts = [ALL] + channels
    key = f"pf::sla_init::{name}"
    if key not in st.session_state:  # the editors' starting rows stay fixed, so edits are not applied twice
        st.session_state[key] = (
            pd.DataFrame([{"Channel": ch, "Within days": d, "Share (%)": round(s * 100, 2)}
                          for ch, t in saved.items() for d, s in t.get("within", [])],
                         columns=["Channel", "Within days", "Share (%)"]),
            pd.DataFrame([{"Channel": ch, "Max backlog (days)": t["max_backlog_days"]}
                          for ch, t in saved.items() if t.get("max_backlog_days") is not None],
                         columns=["Channel", "Max backlog (days)"]))
    within0, limits0 = st.session_state[key]
    st.subheader("Design capacity")
    st.markdown("**Service targets** (per channel; *All* means all work together). "
                "Leave empty to size for the percentile day.")
    within = st.data_editor(within0, num_rows="dynamic", hide_index=True, width="stretch", key=f"pf::within::{name}",
                            column_config={
                                "Channel": st.column_config.SelectboxColumn(options=opts, required=True),
                                "Within days": st.column_config.NumberColumn(
                                    min_value=0, max_value=30, step=1, required=True,
                                    help="Working days after the work arrives; 0 = the same day."),
                                "Share (%)": st.column_config.NumberColumn(
                                    min_value=0.1, max_value=100.0, step=0.5, required=True,
                                    help="At least this share of the channel's work done within those days.")})
    st.caption("Optional: a limit on how much work may queue, in days of capacity.")
    limits = st.data_editor(limits0, num_rows="dynamic", hide_index=True, width="stretch", key=f"pf::limits::{name}",
                            column_config={
                                "Channel": st.column_config.SelectboxColumn(options=[ALL], required=True),
                                "Max backlog (days)": st.column_config.NumberColumn(
                                    min_value=0.1, max_value=30.0, step=0.5, required=True,
                                    help="A day's workload (new work plus backlog) never above this many days of "
                                         "capacity.")})
    sla: dict = {}
    for r in within.dropna().itertuples(index=False):
        sla.setdefault(str(r[0]), {"within": []})["within"].append((int(r[1]), float(r[2]) / 100))
    for r in limits.dropna().itertuples(index=False):
        sla.setdefault(str(r[0]), {"within": []})["max_backlog_days"] = float(r[1])
    if sla != saved:
        save_sla(name, sla)
    return sla


# ---- design capacity -----------------------------------------------------------------------------------------

def _kpi_tiles(k: dict) -> None:
    row1, row2 = st.columns(3), st.columns(3)
    row1[0].metric("Design ÷ average day", f"{k['design_to_average']:.2f}×")
    row1[1].metric("Peak-day coverage", f"{k['peak_day_coverage']:.0%}")
    row1[2].metric("Utilization", f"{k['utilization']:.0%}")
    row2[0].metric("Days with backlog", f"{k['days_with_backlog']:,}")
    row2[1].metric("Max days to clear", f"{k['max_days_to_clear']:.2f}")
    row2[2].metric("Avg fulfillment days", f"{k['avg_fulfillment_days']:.2f}")


def _service_table(k: dict, max_age: int) -> pd.DataFrame:
    rows = []
    for ch, s in k["service"].items():
        row = {"Channel": ch, "Demand": s["demand"]}
        for a, v in enumerate(s["cumulative"]):
            row["Same day" if a == 0 else (f"Within {a} days" if a < max_age else f"Within {a}+ days")] = 100 * v
        rows.append(row)
    return pd.DataFrame(rows)


def _backlog_chart(daily: pd.DataFrame, unit: str) -> None:
    d = daily.reset_index().rename(columns={"index": "date"})
    if "date" not in d:
        d = d.rename(columns={d.columns[0]: "date"})
    long = d.melt(id_vars="date", value_vars=["demand", "processed", "backlog"], var_name="series", value_name=unit)
    chart = alt.Chart(long).mark_line(strokeWidth=1.4).encode(
        x=alt.X("date:T", title=None), y=alt.Y(f"{unit}:Q", title=f"{unit} per day"),
        color=alt.Color("series:N", scale=alt.Scale(domain=["demand", "processed", "backlog"],
                                                    range=[GREY, HAI_BLUE, ORANGE]), title=None),
        tooltip=["date:T", "series:N", alt.Tooltip(f"{unit}:Q", format=",.0f")])
    st.altair_chart(chart.properties(height=260), width="stretch")


def _design(name: str, r, sla: dict, p: ProfileParams) -> None:
    d, k = r.design, r.design.kpis
    unit = p.demand_unit
    with st.container(border=True):
        c1, c2 = st.columns([1.3, 3], vertical_alignment="center")
        c1.metric("Design capacity", f"{d.capacity:,}")
        if d.method == "sla":
            c2.markdown(f"{unit} per working day: the smallest capacity that meets every target. "
                        f"**Set by {'; '.join(d.binding) or 'the targets'}** (one less misses it).")
        else:
            c2.markdown(f"{unit} per working day: the **{p.fallback_percentile * 100:g}th-percentile day**, a "
                        "fallback because no service target is set. Add targets above to size from service "
                        "levels instead.")
        _kpi_tiles(k)
        st.caption(f"{r.summary['calendar']['working_days']:,} working days, {r.summary['calendar']['closed_days']:,} "
                   f"closed (no orders{', worked off backlog' if p.closed_days_have_capacity else ''}). Average day "
                   f"{k['average_day']:,.0f}, busiest {k['peak_day']:,.0f} {unit}.")
    st.markdown("**Service by age**: share of each channel's work done within so many working days, at the design "
                "capacity (work still waiting when the history ends counts as not done).")
    tab = _service_table(k, p.max_age_days)
    st.dataframe(tab, hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%.1f%%") for c in tab.columns if c not in ("Channel", "Demand")} |
        {"Demand": st.column_config.NumberColumn(format="localized")})
    if d.simulation is not None:
        _backlog_chart(d.simulation.daily[["demand", "processed", "backlog"]], unit)
    with st.expander("How it's calculated"):
        st.markdown(
            "Each working day the new work joins a queue; work left from earlier days ages by one working day. The "
            "site then works oldest first up to its daily capacity; within one age, channels share it in "
            "proportion. A closed day does no work. **Within N days** for a target counts only work that arrived "
            "at least N working days before the history ends. The design capacity is found by trying capacities "
            "until the smallest that meets every target. **Max days to clear**: the most a day's workload (new "
            "plus waiting) reaches in days of capacity. **Avg fulfillment days**: the same day counts as 1.")

    st.markdown("**What-if**: try another daily capacity.")
    cap = st.number_input(f"Capacity ({unit} per working day)", min_value=1, value=int(d.capacity) or 1, step=100,
                          key="pf::whatif")
    w = what_if(r.demand, r.open, cap, p).kpis
    same = w["service"][ALL]["cumulative"][0]
    st.markdown(f"At {cap:,} a day, **{same:.1%}** of the work is done the same day, with "
                f"**{w['days_with_backlog']:,}** days starting with backlog and at most "
                f"**{w['max_days_to_clear']:.2f}** days of work queued.")
    cols = st.columns(4)
    cols[0].metric("What-if days with backlog", f"{w['days_with_backlog']:,}")
    cols[1].metric("What-if same day", f"{same:.1%}")
    cols[2].metric("What-if utilization", f"{w['utilization']:.0%}")
    cols[3].metric("What-if max days to clear", f"{w['max_days_to_clear']:.2f}")


# ---- the profile -------------------------------------------------------------------------------------------

def _volumes(r) -> None:
    st.subheader("Volumes and seasonality")
    s = r.volumes.summary
    cols = st.columns(4)
    cols[0].metric("Average day (lines)", f"{s['lines']['average_day']:,.0f}")
    cols[1].metric(f"{r.params.fallback_percentile * 100:g}th-percentile day", f"{s['lines']['percentile_day']:,.0f}")
    cols[2].metric("Busiest day", f"{s['lines']['peak_day']:,.0f}", help=s["peak_day_date"])
    cols[3].metric("Peak month ÷ average", f"{s['peak_month_ratio']:.2f}×", help=s["peak_month"])
    ch = " · ".join(f"{c} {v:.0%}" for c, v in s.get("channels", {}).items())
    st.caption(f"{s['days_with_orders']:,} days with orders, {s['first']} to {s['last']}; "
               f"{s['units_per_line']:.2f} units per line" + (f"; lines by channel: {ch}" if ch else "") + ".")
    c1, c2 = st.columns([3, 2])
    m = r.volumes.monthly
    c1.altair_chart(alt.Chart(m).mark_bar(color=HAI_BLUE).encode(
        x=alt.X("month:N", title=None), y=alt.Y("lines_per_day:Q", title="lines per day with orders"),
        tooltip=["month", alt.Tooltip("lines_per_day:Q", format=",.0f"), "days"]).properties(height=220),
        width="stretch")
    c2.altair_chart(alt.Chart(r.volumes.weekday).mark_bar(color=HAI_BLUE_4).encode(
        x=alt.X("weekday:N", sort=None, title=None), y=alt.Y("share:Q", axis=alt.Axis(format="%"), title="share of lines"),
        tooltip=["weekday", alt.Tooltip("share:Q", format=".1%")]).properties(height=220), width="stretch")
    with st.expander("How it's calculated"):
        st.markdown("Counted on the dates in the order file (days with orders). **Peak month ÷ average**: the "
                    "busiest month's lines per day with orders, over the average day.")


def _orders(r) -> None:
    st.subheader("Order shape")
    prof = r.orders.profile
    show = prof[["channel", "orders", "lines", "units", "lines_per_order", "units_per_line", "single_line_share"]]
    st.dataframe(show, hide_index=True, width="stretch", column_config={
        "single_line_share": st.column_config.NumberColumn("single-line orders", format="percent"),
        "lines_per_order": st.column_config.NumberColumn(format="%.2f"),
        "units_per_line": st.column_config.NumberColumn(format="%.2f")})
    c1, c2 = st.columns(2)
    c1.altair_chart(alt.Chart(r.orders.lines_per_order).mark_bar(color=HAI_BLUE).encode(
        x=alt.X("lines_per_order:N", sort=None, title="lines per order"),
        y=alt.Y("share:Q", axis=alt.Axis(format="%"), title="share of orders"),
        tooltip=["lines_per_order", alt.Tooltip("share:Q", format=".1%"), "orders"]).properties(height=200),
        width="stretch")
    c2.altair_chart(alt.Chart(r.orders.units_per_line).mark_bar(color=HAI_BLUE_4).encode(
        x=alt.X("units_per_line:N", sort=None, title="units per line"),
        y=alt.Y("share:Q", axis=alt.Axis(format="%"), title="share of lines"),
        tooltip=["units_per_line", alt.Tooltip("share:Q", format=".1%"), "lines"]).properties(height=200),
        width="stretch")
    st.caption(f"Lines counted as `{r.summary['line_definition']}`.")


def _hourly(r) -> None:
    st.subheader("Hourly profile")
    h = r.hourly
    if not h.available:
        st.info(h.note, icon=":material/schedule:")
        return
    s = h.summary
    cols = st.columns(3)
    cols[0].metric("Busiest hour", f"{s['peak_hour']:02d}:00")
    cols[1].metric("Its share of the day", f"{s['peak_hour_share']:.1%}")
    if s.get("design_peak_hour"):
        cols[2].metric("Design day's peak hour", f"{s['design_peak_hour']:,.0f}",
                       help="Design capacity × the busiest hour's share: the hourly rate workstations must reach.")
    st.altair_chart(alt.Chart(h.by_hour).mark_bar(color=HAI_BLUE).encode(
        x=alt.X("hour:O", title="hour of day"), y=alt.Y("share:Q", axis=alt.Axis(format="%"), title="share of lines"),
        tooltip=["hour", alt.Tooltip("share:Q", format=".1%"), "lines"]).properties(height=200), width="stretch")
    st.caption(f"{s['share_with_time']:.0%} of lines have a time of day. Shares are over the whole history.")


def _abc(name: str, r) -> None:
    st.subheader("ABC velocity")
    s = r.abc.summary
    st.dataframe(s[["abc", "skus", "skus_share", "lines_share", "units_share", "cube_moved_ft3", "cube_on_hand_ft3",
                    "sized_share"]], hide_index=True, width="stretch", column_config={
        "skus_share": st.column_config.NumberColumn("share of SKUs", format="percent"),
        "lines_share": st.column_config.NumberColumn("share of lines", format="percent"),
        "units_share": st.column_config.NumberColumn("share of units", format="percent"),
        "cube_moved_ft3": st.column_config.NumberColumn("cube moved (ft³)", format="localized"),
        "cube_on_hand_ft3": st.column_config.NumberColumn("cube on hand (ft³)", format="localized"),
        "sized_share": st.column_config.NumberColumn("SKUs with a size", format="percent")})
    a, b = r.params.abc_cuts
    st.caption(f"SKUs ranked by order lines: A up to {a:.0%} of lines, B up to {b:.0%}, C the rest; D are SKUs in "
               "stock that never moved. Cube uses the backfilled item master when backfill has run.")
    st.download_button("Download ABC per SKU", r.abc.skus.to_csv(index=False).encode("utf-8"), f"{name}_abc.csv",
                       "text/csv", key="pf::dl_abc", icon=":material/download:")


def _stock(r) -> None:
    st.subheader("Stock")
    s = r.stock.summary
    if not s.get("available"):
        st.info(s.get("note", "No inventory."), icon=":material/inventory_2:")
        return
    if s.get("uniform_days_on_hand"):
        st.warning("Every SKU holds the same number of days of its own demand. Real stock never does: this "
                   "inventory was probably derived from the orders, not taken from the warehouse system. Confirm with "
                   "the client before using days on hand or storage needs.", icon="⚠️")
    cols = st.columns(4)
    cols[0].metric("Days on hand", f"{s['days_on_hand']:.1f}" if s.get("days_on_hand") else "—")
    cols[1].metric("Turns per year", f"{s['turns']:.1f}" if s.get("turns") else "—")
    cols[2].metric("Cube on hand (ft³)", f"{s['cube_on_hand_ft3']:,.0f}" if s.get("cube_on_hand_ft3") else "—")
    cols[3].metric("SKUs that never moved", f"{s['no_movement_skus']:,}")
    st.caption(f"Average over {s['snapshots']} snapshot(s); units per day over {s['working_days']:,} working days; "
               f"turns scale the {s['history_days']:,}-day history to a year.")


def _inbound(r) -> None:
    st.subheader("Inbound")
    s = r.inbound.summary
    if not s.get("available"):
        st.info(s.get("note", "No receipts."), icon=":material/local_shipping:")
        return
    cols = st.columns(4)
    cols[0].metric("Average day (receipt lines)", f"{s['lines']['average_day']:,.0f}")
    cols[1].metric(f"{r.params.fallback_percentile * 100:g}th-percentile day", f"{s['lines']['percentile_day']:,.0f}")
    cols[2].metric("Busiest day", f"{s['lines']['peak_day']:,.0f}")
    cols[3].metric("Units per line", f"{s['units_per_line']:.1f}")
    st.caption(f"Receiving capacity: {s['design']['capacity']:,} lines per working day "
               f"({'from receiving targets' if s['design']['method'] == 'sla' else 'the percentile day, no receiving target set'}).")


# ---- settings and the page -----------------------------------------------------------------------------------

def _settings(name: str) -> ProfileParams:
    from gtpfit.app.ingest.widgets import control
    cur = state.saved_params(name)
    base = ProfileParams()
    new = {}
    st.subheader("Settings")
    for f in block_fields("client", ProfileParams):
        v = control(f, getattr(cur, f), f"pf::p::{f}", cls=ProfileParams)
        new[f] = tuple(v) if isinstance(v, list) else v
    det = block_fields("detection", ProfileParams)
    n = sum(getattr(cur, f) != getattr(base, f) for f in det)
    with st.expander("Detection settings" + (f" ({n} changed)" if n else "")):
        c1, c2 = st.columns([5, 1.3], vertical_alignment="center")
        c1.caption("Defaults suit most clients.")
        reset = c2.button("Reset to defaults", key="pf::reset", disabled=not n)
        for f in det:
            v = control(f, getattr(cur, f), f"pf::p::{f}", cls=ProfileParams)
            new[f] = tuple(v) if isinstance(v, list) else v
    p = ProfileParams(**new)
    if reset:
        for k in [k for k in st.session_state.keys() if str(k).startswith("pf::p::") and k[len("pf::p::"):] in det]:
            del st.session_state[k]
        p = dataclasses.replace(p, **{f: getattr(base, f) for f in det})
        state.save_params(name, p)
        st.rerun()
    if p != cur:
        state.save_params(name, p)
        if any(getattr(p, f) != getattr(cur, f) for f in det):
            st.rerun()
    return p


def page() -> None:
    name = _header()
    if not name:
        return
    top = st.container()
    body = st.container()
    settings_box = st.container()
    with settings_box:
        p = _settings(name)
        if st.button("Continue to Fit →", key="pf::to_fit", type="primary",
                     help="How many units of each SKU fit each container, and what limits the rest."):
            st.session_state["fit::client"] = name
            from gtpfit.app import pages
            st.switch_page(pages.FIT)
    with top:
        sla = _targets(name, _channels(name, state._signature(name)), p.demand_unit)
    with body:
        with st.spinner("Profiling the operation and sizing the design day…"):
            r = state.result(name, p, sla)
        _design(name, r, sla, p)
        _volumes(r)
        _orders(r)
        _hourly(r)
        _abc(name, r)
        _stock(r)
        _inbound(r)
