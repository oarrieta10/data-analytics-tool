"""fit — the workbench Fit page: which SKUs fit each container (and each divider cell), and what limits the rest.

The containers in the catalogue with their sources (and which values are placeholders), the share of active
SKUs and of order lines that fit each container and cell layout, a closer look at one container (limits, units
per container, fit by ABC class, how much rests on estimated sizes), a SKU look-up, adding a container to the
catalogue, and the fit settings.  Every number comes from the fit run (gtpfit.fit), which also writes _fit/;
this package only lays it out.  Opens once the client's scope is saved or skipped.
"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from gtpfit.app.quality import state as quality_state
from gtpfit.app.theme import HAI_BLUE
from gtpfit.fit import FIT_TEXT, FitParams, load_catalog, save_container
from gtpfit.ingest.params import block_fields
from gtpfit.scope import load_scope

from . import state

MM_PER_IN = 25.4


def _header() -> str | None:
    st.title("5 · Fit")
    st.caption("How many units of each SKU fit each container, and each divider cell, and what stops the rest: "
               "the container's space, its weight limit, or a size that is missing.")
    ready = quality_state.ready_clients()
    if not ready:
        st.info("No client has finished Ingest yet.", icon=":material/info:")
        return None
    want = state.client() or st.session_state.get("pf::client") or st.session_state.get("bf::client", "")
    c1, c2 = st.columns([3, 1.2], vertical_alignment="bottom")
    name = c1.selectbox("Client", ready, index=ready.index(want) if want in ready else 0, key="fit::client_pick")
    st.session_state["fit::client"] = name
    c2.markdown("🔒 **Data stays on this computer**")
    if load_scope(name) is None:
        st.info(f"Set the scope for **{name}** first (or skip it to use every row): fit looks only at the SKUs the "
                "design is for.", icon=":material/filter_alt:")
        if st.button("Go to Scope", key="fit::to_scope", icon=":material/filter_alt:"):
            st.session_state["scp::client"] = name
            from gtpfit.app import pages
            st.switch_page(pages.SCOPE)
        return None
    return name


def _containers(containers) -> None:
    st.subheader("Containers")
    rows = []
    for c in containers:
        l, w, h = c.inside_in
        rows.append({"Container": c.name, "System": c.system, "Holds": "loose units" if c.mode == "units" else
                     "one carton", "Inside (mm)": f"{l * MM_PER_IN:.0f} × {w * MM_PER_IN:.0f} × {h * MM_PER_IN:.0f}",
                     "Payload (lb)": round(c.payload_lb, 1),
                     "Dividers": ", ".join(lay.name for lay in c.layouts[1:]) or "none",
                     "Placeholder values": ", ".join(c.placeholders) or "", "Source": c.source,
                     "Confidence": c.confidence})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    ph = [c.name for c in containers if c.placeholders]
    if ph:
        st.warning(f"Placeholder values, not confirmed specs: {', '.join(ph)} (payload). Results that depend on "
                   "weight are provisional until they are confirmed.", icon="⚠️")


def _shares(r, containers) -> None:
    st.subheader("What fits")
    rows = []
    for c in containers:
        s = r.summary["containers"].get(c.id, {})
        for lay, v in s.get("layouts", {}).items():
            rows.append({"Container": c.name, "Cell": "whole container" if lay == "whole" else f"{lay} cell",
                         "SKUs that fit": 100 * v["skus_share"], "Lines that fit": 100 * v["lines_share"],
                         "SKUs": v["fits_skus"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
        "SKUs that fit": st.column_config.NumberColumn(format="%.0f%%"),
        "Lines that fit": st.column_config.NumberColumn(format="%.0f%%")})
    st.caption(f"{r.summary['skus']:,} SKUs active in scope (ordered or on hand). A SKU fits when at least one unit "
               "(or, on a tray, its case) goes in. Lines are in-scope order lines.")


def _detail(r, containers) -> None:
    st.subheader("One container in detail")
    names = {c.name: c for c in containers}
    pick = st.selectbox("Container", list(names), key="fit::detail")
    c = names[pick]
    s = r.summary["containers"][c.id]
    whole = s["layouts"]["whole"]
    st.markdown(f"**{whole['skus_share']:.0%} of SKUs and {whole['lines_share']:.0%} of lines fit a whole "
                f"{c.name}.** {s['estimated_share_of_fits']:.0%} of the SKUs that fit rest on estimated sizes "
                "(backfill)." + (" Its payload is a placeholder." if c.placeholders else ""))
    lim = pd.DataFrame([{"limit": FIT_TEXT[k].title, "SKUs": v, "what it means": FIT_TEXT[k].what}
                        for k, v in sorted(s["limits"].items(), key=lambda kv: -kv[1])])
    c1, c2 = st.columns(2)
    c1.altair_chart(alt.Chart(lim).mark_bar(color=HAI_BLUE).encode(
        x=alt.X("SKUs:Q"), y=alt.Y("limit:N", sort="-x", title=None),
        tooltip=["limit", "SKUs", "what it means"]).properties(height=200, title="What sets the count"),
        width="stretch")
    upc = pd.DataFrame({"units": list(s["units_per_container"]), "SKUs": list(s["units_per_container"].values())})
    c2.altair_chart(alt.Chart(upc).mark_bar(color=HAI_BLUE).encode(
        x=alt.X("units:N", sort=None, title="units per container"), y=alt.Y("SKUs:Q"),
        tooltip=["units", "SKUs"]).properties(height=200, title="Units per container (SKUs that fit)"),
        width="stretch")
    if s.get("by_abc"):
        st.caption("Share of SKUs that fit, by ABC class: " +
                   " · ".join(f"{k} {v:.0%}" for k, v in s["by_abc"].items()))


def _lookup(r) -> None:
    st.subheader("Look up a SKU")
    f = r.fit
    skus = f.drop_duplicates("sku").sort_values("lines", ascending=False)["sku"].tolist()
    sku = st.selectbox("SKU", skus, key="fit::lookup", help="Active SKUs, busiest first.")
    g = f[f["sku"] == sku]
    first = g.iloc[0]
    st.markdown(f"**{sku}** · {first['lines']:,.0f} lines · class {first['abc'] or '—'} · sizes from "
                f"{first['size_method']}" + (f" ({first['size_confidence']} confidence)" if first["size_confidence"]
                                             else ""))
    show = g[["container", "layout", "units", "limit", "units_tessellation", "units_liquid", "units_weight"]].copy()
    show["limit"] = show["limit"].map(lambda k: FIT_TEXT[k].title)
    st.dataframe(show, hide_index=True, width="stretch")


def _add_container() -> None:
    with st.expander("Add or replace a container in the catalogue"):
        st.caption("Saved to the catalogue for every client. Divider layouts and carton ranges for trays are edited "
                   "in the catalogue file. A source is required.")
        c1, c2, c3 = st.columns(3)
        cid = c1.text_input("Id (unique)", key="fit::new::id")
        name = c2.text_input("Name", key="fit::new::name")
        system = c3.text_input("System", key="fit::new::system")
        d1, d2, d3, d4 = st.columns(4)
        l = d1.number_input("Inside length (mm)", min_value=1.0, value=600.0, key="fit::new::l")
        w = d2.number_input("Inside width (mm)", min_value=1.0, value=400.0, key="fit::new::w")
        h = d3.number_input("Inside height (mm)", min_value=1.0, value=300.0, key="fit::new::h")
        pay = d4.number_input("Payload (kg)", min_value=0.1, value=30.0, key="fit::new::payload")
        e1, e2, e3 = st.columns([3, 1, 1])
        source = e1.text_input("Source", key="fit::new::source", placeholder="e.g. HAI spec sheet, 2026-10")
        conf = e2.selectbox("Confidence", ["high", "medium", "low"], key="fit::new::conf")
        pay_conf = e3.selectbox("Payload confidence", ["high", "medium", "low"], index=2, key="fit::new::pconf")
        if st.button("Save container", key="fit::new::save", icon=":material/save:"):
            spec = {"id": cid.strip(), "name": name.strip(), "system": system.strip(), "mode": "units",
                    "inside": {"l": l, "w": w, "h": h, "unit": "mm"},
                    "payload": {"value": pay, "unit": "kg", "confidence": pay_conf},
                    "dividers": [], "source": source.strip(), "confidence": conf}
            try:
                save_container(spec)
            except ValueError as e:
                st.error(str(e), icon="⛔")
            else:
                st.cache_data.clear()
                st.toast(f"Saved {name}.")
                st.rerun()


def _settings(name: str) -> FitParams:
    from gtpfit.app.ingest.widgets import control
    cur = state.saved_params(name)
    new = {}
    st.subheader("Settings")
    for f in block_fields("client", FitParams):
        new[f] = control(f, getattr(cur, f), f"fit::p::{f}", cls=FitParams)
    with st.expander("Detection settings"):
        for f in block_fields("detection", FitParams):
            new[f] = control(f, getattr(cur, f), f"fit::p::{f}", cls=FitParams)
    p = FitParams(**new)
    if p != cur:
        state.save_params(name, p)
    return p


def page() -> None:
    name = _header()
    if not name:
        return
    body = st.container()
    settings_box = st.container()
    with settings_box:
        p = _settings(name)
        _add_container()
        st.button("Continue to Slotting →", key="fit::to_slotting", disabled=True,
                  help="Slotting (which SKUs go to which system) is the next module; not built yet.")
    with body:
        try:
            containers = load_catalog()
        except ValueError as e:
            st.error(f"The container catalogue cannot be read: {e}", icon="⛔")
            return
        with st.spinner("Fitting every SKU into every container…"):
            r = state.result(name, p)
        _containers(containers)
        _shares(r, containers)
        _detail(r, containers)
        _lookup(r)
