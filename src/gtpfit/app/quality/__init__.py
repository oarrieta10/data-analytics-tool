"""quality — the workbench Quality page: can we trust the item data?

Score tiles (share of in-scope work carried by SKUs usable for unit fit, and case fit), what is wrong (one
row per problem, with plain words and examples), weight against cube, the items to measure, and the quality
settings.  Every number comes from the quality run (gtpfit.quality), which also writes _quality/; this
package only lays it out.  Opens once the client's scope is saved or skipped.
"""
from __future__ import annotations

import dataclasses

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from gtpfit import tables, workspace
from gtpfit.app.theme import HAI_BLUE
from gtpfit.ingest.params import block_fields
from gtpfit.quality import QUALITY_TEXT, QualityParams
from gtpfit.scope import load_scope

from . import state

BASIS_LABEL = {"lines": "Lines", "units": "Units", "on_hand": "On-hand", "skus": "SKUs"}
BAND_COLOUR = {"green": "green", "amber": "orange", "red": "red"}
STATUS_COLOUR = {"ok": HAI_BLUE, "suspect": "#FF9900", "unusable": "#D3271C"}  # Hai Blue, Safety Orange, red
SCATTER_SAMPLE = 4000   # usable SKUs drawn in the scatter (every problem SKU is drawn); keeps the page quick
SCATTER_SEED = 7


def _header() -> str | None:
    st.title("2 · Quality")
    st.caption("Can we trust the item data? How much of the work that matters is described by usable sizes and "
               "weights, and what exactly is wrong with the rest. Nothing is changed here: backfill fills gaps.")
    ready = state.ready_clients()
    if not ready:
        st.info("No client has finished Ingest yet.", icon=":material/info:")
        return None
    want = state.client() or st.session_state.get("scp::client") or st.session_state.get("ing::client", "")
    c1, c2 = st.columns([3, 1.2], vertical_alignment="bottom")
    name = c1.selectbox("Client", ready, index=ready.index(want) if want in ready else 0, key="qly::client_pick")
    st.session_state["qly::client"] = name
    c2.markdown("🔒 **Data stays on this computer**")
    if load_scope(name) is None:
        st.info(f"Set the scope for **{name}** first (or skip it to use every row): quality looks only at the work "
                "the design is for.", icon=":material/filter_alt:")
        if st.button("Go to Scope", key="qly::to_scope", icon=":material/filter_alt:"):
            st.session_state["scp::client"] = name
            from gtpfit.app import pages
            st.switch_page(pages.SCOPE)
        return None
    return name


def _settings(name: str) -> QualityParams:
    """Outlier strictness (client setting) and the collapsed detection settings, saved for the client."""
    from gtpfit.app.ingest.widgets import control
    cur = state.saved_params(name)
    base = QualityParams()
    new = {}
    st.subheader("Settings")
    for f in block_fields("client", QualityParams):
        v = control(f, getattr(cur, f), f"qly::p::{f}", cls=QualityParams)
        new[f] = tuple(v) if isinstance(v, list) else v
    det = block_fields("detection", QualityParams)
    n = sum(getattr(cur, f) != getattr(base, f) for f in det)
    with st.expander("Detection settings" + (f" ({n} changed)" if n else "")):
        c1, c2 = st.columns([5, 1.3], vertical_alignment="center")
        c1.caption("Defaults suit most clients.")
        reset = c2.button("Reset to defaults", key="qly::reset", disabled=not n)
        for f in det:
            v = control(f, getattr(cur, f), f"qly::p::{f}", cls=QualityParams)
            new[f] = tuple(v) if isinstance(v, list) else v
    p = QualityParams(**new)
    if reset:
        for k in [k for k in st.session_state.keys() if str(k).startswith("qly::p::")
                  and k[len("qly::p::"):] in det]:
            del st.session_state[k]
        p = dataclasses.replace(p, **{f: getattr(base, f) for f in det})
        state.save_params(name, p)
        st.rerun()
    if p != cur:
        state.save_params(name, p)
        if any(getattr(p, f) != getattr(cur, f) for f in det):
            st.rerun()  # the expander label counts changes: redraw it
    return p


def _tiles(summary: dict) -> None:
    sc = summary["score"]
    with st.container(border=True):
        st.markdown("**Usable for unit fit**, share of…")
        cols = st.columns(len(sc["unit_fit"]))
        for col, (basis, v) in zip(cols, sc["unit_fit"].items()):
            col.metric(BASIS_LABEL[basis], f"{v['share']:.0%}")
            col.markdown(f":{BAND_COLOUR[v['band']]}-badge[{v['band']}]")
        st.caption("Case fit: " + " · ".join(f"{BASIS_LABEL[b]} {v['share']:.0%} ({v['band']})"
                                             for b, v in sc["case_fit"].items()))
        lines = sc["unit_fit"]["lines"]["share"]
        st.markdown(f"**{lines:.0%} of the picking work is described by usable dimensions and weight.** "
                    f"Order lines counted as `{summary['line_definition']}`; SKUs with no in-scope activity "
                    f"({summary['counts']['skus_inactive']:,}) are left out of the score.")


def _problems(r) -> None:
    st.subheader("What is wrong")
    probs = [p for p in r.summary["problems"] if p["severity"] != "info"]
    if not probs:
        st.success("No problems found in the item data.", icon="✅")
        return
    view = pd.DataFrame([{"Problem": p["title"], "Active SKUs": p["skus_active"], "All SKUs": p["skus"],
                          "Share of lines": p["share_of_lines"], "Status": p["severity"]} for p in probs])
    st.dataframe(view, hide_index=True, width="stretch", column_config={
        "Share of lines": st.column_config.NumberColumn(format="percent")})
    lines = r.status.drop_duplicates("sku").set_index("sku")["lines"]
    for p in probs:
        t = QUALITY_TEXT[p["code"]]
        with st.expander(f"{p['title']} · {p['skus_active']:,} active SKUs · {p['share_of_lines']:.1%} of lines · "
                         f"{p['severity']}"):
            st.markdown(f"{t.what}  \n**Design impact:** {t.impact}")
            ex = r.flags[r.flags["code"] == p["code"]].copy()
            ex["lines"] = ex["sku"].map(lines).fillna(0)
            ex = ex.sort_values(["lines", "sku"], ascending=[False, True]).head(10)
            st.dataframe(ex[["sku", "field", "observed", "peer_ref", "suggested", "lines"]].rename(columns={
                "peer_ref": "peer median", "suggested": "suggested fix"}), hide_index=True, width="stretch")
            st.caption("Ten examples, busiest first. A suggested fix is shown, never applied.")


@st.cache_data(show_spinner=False, max_entries=4)
def _item_values(name: str, sig: str) -> pd.DataFrame:
    im = tables.load(name)["item_master"].drop_duplicates("sku")
    cols = [c for c in ("unit_l", "unit_w", "unit_h", "unit_weight") if c in im]
    return im[["sku"] + cols]


def _scatter(name: str, r) -> None:
    st.subheader("Weight against cube")
    vals = _item_values(name, state._signature(name))
    act = r.status[(r.status["bucket"] == "active") & r.status["status"].isin(STATUS_COLOUR)]
    d = act[["sku", "status"]].merge(vals, on="sku")
    d["cube_ft3"] = d["unit_l"] * d["unit_w"] * d["unit_h"] / 1728
    d = d[(d["cube_ft3"] > 0) & (d["unit_weight"] > 0)]
    if d.empty:
        st.info("No SKUs with both a size and a weight to plot.")
        return
    ok = d[d["status"] == "ok"]
    shown = pd.concat([d[d["status"] != "ok"], ok.sample(min(len(ok), SCATTER_SAMPLE), random_state=SCATTER_SEED)])
    lo, hi = workspace.client_params(name).density_lb_ft3
    x = np.geomspace(shown["cube_ft3"].min(), shown["cube_ft3"].max(), 2)
    band = pd.DataFrame({"cube_ft3": np.r_[x, x], "unit_weight": np.r_[x * lo, x * hi],
                         "line": [f"{lo:g} lb/ft³"] * 2 + [f"{hi:g} lb/ft³"] * 2})
    pts = alt.Chart(shown).mark_circle(size=18, opacity=0.6).encode(
        x=alt.X("cube_ft3:Q", scale=alt.Scale(type="log"), title="Unit cube (ft³)"),
        y=alt.Y("unit_weight:Q", scale=alt.Scale(type="log"), title="Unit weight (lb)"),
        color=alt.Color("status:N", scale=alt.Scale(domain=list(STATUS_COLOUR), range=list(STATUS_COLOUR.values())),
                        title="Status"),
        tooltip=["sku", "status", alt.Tooltip("cube_ft3:Q", format=".3f"), alt.Tooltip("unit_weight:Q", format=".2f")])
    lines = alt.Chart(band).mark_line(strokeDash=[4, 4], color="#5A5A5A").encode(
        x="cube_ft3:Q", y="unit_weight:Q", detail="line:N", tooltip=["line"])
    st.altair_chart((lines + pts).properties(height=380), width="stretch")
    st.caption(f"Each dot is an active SKU; dashed lines are the assortment's density range ({lo:g}–{hi:g} lb/ft³). "
               f"Every problem SKU is drawn, with {min(len(ok), SCATTER_SAMPLE):,} of {len(ok):,} usable ones.")


def _items(name: str, r) -> None:
    st.subheader("Items to measure")
    items = r.items
    if items.empty:
        st.success("Nothing to measure: every active SKU is usable.", icon="✅")
        return
    n80 = int((items["closes_gap"] < 0.8).sum()) + 1
    st.markdown(f"**Top {min(n80, len(items)):,} SKUs close 80% of the lines gap** ({len(items):,} SKUs to fix in "
                "all, ranked by share of lines).")
    chart = alt.Chart(items).mark_line(color=HAI_BLUE).encode(
        x=alt.X("rank:Q", title="SKUs fixed (busiest first)"),
        y=alt.Y("closes_gap:Q", axis=alt.Axis(format="%"), title="Share of the gap closed"),
        tooltip=["rank", "sku", alt.Tooltip("closes_gap:Q", format=".1%")])
    st.altair_chart(chart.properties(height=220), width="stretch")
    client_view = items[["rank", "sku", "description", "what_is_wrong", "fields_to_measure", "lines",
                         "share_of_lines"]]
    st.download_button("Download items to measure for the client", client_view.to_csv(index=False).encode("utf-8"),
                       f"{name}_items_to_measure.csv", "text/csv", key="qly::dl_items", icon=":material/download:")


def page() -> None:
    name = _header()
    if not name:
        return
    body = st.container()
    settings_box = st.container()
    with settings_box:
        p = _settings(name)
        if st.button("Continue to Backfill →", key="qly::to_backfill", type="primary",
                     help="Estimate the sizes quality cannot trust, and see how good the estimates are."):
            st.session_state["bf::client"] = name
            from gtpfit.app import pages
            st.switch_page(pages.BACKFILL)
    with body:
        with st.spinner("Checking the item data…"):
            r = state.result(name, p)
        _tiles(r.summary)
        _problems(r)
        _scatter(name, r)
        _items(name, r)
