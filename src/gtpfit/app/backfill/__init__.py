"""backfill — the workbench Backfill page: estimates for the item sizes quality cannot trust.

Readiness for unit fit before -> after (and what gap is left, and why), how good the estimates are (the holdout:
trusted items hidden and estimated from the rest), the filled items, the doubted values kept for review, a
look-up of one SKU and the items it borrowed from, and the backfill settings.  Every number comes from the
backfill run (gtpfit.backfill), which also writes _backfill/; this package only lays it out.  Opens once the
client's scope is saved or skipped.
"""
from __future__ import annotations

import dataclasses

import pandas as pd
import streamlit as st

from gtpfit.backfill import BACKFILL_TEXT, BackfillParams
from gtpfit.ingest.params import block_fields
from gtpfit.scope import load_scope

from gtpfit.app.quality import state as quality_state
from . import state

BASIS_LABEL = {"lines": "Lines", "units": "Units", "on_hand": "On-hand", "skus": "SKUs"}
FIELD_LABEL = {"long": "Longest side", "mid": "Middle side", "short": "Shortest side", "weight": "Weight",
               "volume": "Volume"}
SIZE_COLS = ["unit_l", "unit_w", "unit_h", "unit_weight"]
LIST_ROWS = 200  # rows shown in the filled / review tables (the downloads hold every row)
PCT = "%.0f%%"   # shares shown as whole percents (values passed as 0-100)


def _header() -> str | None:
    st.title("3 · Backfill")
    st.caption("Estimates for the item sizes quality cannot trust, from the most similar trusted items. Missing, "
               "placeholder and copied values are filled; any other doubted value is kept, with the estimate "
               "beside it. Every value says where it came from.")
    ready = quality_state.ready_clients()
    if not ready:
        st.info("No client has finished Ingest yet.", icon=":material/info:")
        return None
    want = state.client() or st.session_state.get("qly::client") or st.session_state.get("scp::client", "")
    c1, c2 = st.columns([3, 1.2], vertical_alignment="bottom")
    name = c1.selectbox("Client", ready, index=ready.index(want) if want in ready else 0, key="bf::client_pick")
    st.session_state["bf::client"] = name
    c2.markdown("🔒 **Data stays on this computer**")
    if load_scope(name) is None:
        st.info(f"Set the scope for **{name}** first (or skip it to use every row): backfill estimates only the "
                "items the design is for.", icon=":material/filter_alt:")
        if st.button("Go to Scope", key="bf::to_scope", icon=":material/filter_alt:"):
            st.session_state["scp::client"] = name
            from gtpfit.app import pages
            st.switch_page(pages.SCOPE)
        return None
    return name


def _settings(name: str) -> BackfillParams:
    """Similar items used and review factor (client settings), and the collapsed detection settings."""
    from gtpfit.app.ingest.widgets import control
    cur = state.saved_params(name)
    base = BackfillParams()
    new = {}
    st.subheader("Settings")
    for f in block_fields("client", BackfillParams):
        v = control(f, getattr(cur, f), f"bf::p::{f}", cls=BackfillParams)
        new[f] = tuple(v) if isinstance(v, list) else v
    det = block_fields("detection", BackfillParams)
    n = sum(getattr(cur, f) != getattr(base, f) for f in det)
    with st.expander("Detection settings" + (f" ({n} changed)" if n else "")):
        c1, c2 = st.columns([5, 1.3], vertical_alignment="center")
        c1.caption("Defaults suit most clients.")
        reset = c2.button("Reset to defaults", key="bf::reset", disabled=not n)
        for f in det:
            v = control(f, getattr(cur, f), f"bf::p::{f}", cls=BackfillParams)
            new[f] = tuple(v) if isinstance(v, list) else v
    p = BackfillParams(**new)
    if reset:
        for k in [k for k in st.session_state.keys() if str(k).startswith("bf::p::") and k[len("bf::p::"):] in det]:
            del st.session_state[k]
        p = dataclasses.replace(p, **{f: getattr(base, f) for f in det})
        state.save_params(name, p)
        st.rerun()
    if p != cur:
        state.save_params(name, p)
        if any(getattr(p, f) != getattr(cur, f) for f in det):
            st.rerun()  # the expander label counts changes: redraw it
    return p


def _tiles(s: dict) -> None:
    with st.container(border=True):
        st.markdown("**Ready for unit fit with the estimates**, share of… (change since Quality)")
        cols = st.columns(len(s["ready_after"]))
        for col, (basis, v) in zip(cols, s["ready_after"].items()):
            before = s["ready_before"][basis]["share"]
            col.metric(BASIS_LABEL[basis], f"{v['share']:.0%}", delta=f"{(v['share'] - before) * 100:+.1f} pts",
                       delta_color="normal")
        a = s["actions"]
        st.caption(f"{a['filled']:,} filled · {a['kept']:,} kept (close to the estimate) · {a['kept_review']:,} kept, "
                   f"to review · {a['not_estimated']:,} not estimated · {s['donors']:,} trusted items to borrow "
                   "from. Counts are SKUs.")
        gap = s["gap_after"]
        left = 1 - s["ready_after"]["lines"]["share"]
        parts = [(gap["orphans"], "ordered but not in the item master (nothing to match on: ask the client for "
                                  "those item rows)"),
                 (gap["kept_doubts"], "doubted values kept as recorded (see To review)"),
                 (gap["not_estimated"], "missing, with nothing to estimate from")]
        why = "; ".join(f"**{v:.1%}** {t}" for v, t in parts if v > 0)
        st.markdown(f"**{left:.1%} of lines are still not ready for unit fit**" + (f": {why}." if why else "."))


def _accuracy(s: dict) -> None:
    st.subheader("How good are the estimates")
    h = s.get("holdout") or {}
    if not h.get("n"):
        st.info(f"No accuracy test: {h.get('note') or 'not run'}. Estimates are shown, but their error is "
                "unknown.", icon=":material/info:")
        return
    v, b = h["fields"]["volume"], h["baseline"]["volume"]
    st.markdown(f"**{h['n']:,} trusted items were hidden and estimated from the other {h['donors']:,}.** Their "
                f"volume came out typically **×{v['factor']:.2f}** off ({v['within_50']:.0%} within ±50%), against "
                f"×{b['factor']:.2f} ({b['within_50']:.0%}) by guessing the median item for everything.")
    tab = pd.DataFrame([{"Measure": FIELD_LABEL[f], "Typical error": f"×{m['factor']:.2f}",
                         "Within ±25%": 100 * m["within_25"], "Within ±50%": 100 * m["within_50"],
                         "Median-item guess": f"×{h['baseline'][f]['factor']:.2f}"} for f, m in h["fields"].items()])
    st.dataframe(tab, hide_index=True, width="stretch", column_config={
        "Within ±25%": st.column_config.NumberColumn(format=PCT),
        "Within ±50%": st.column_config.NumberColumn(format=PCT)})
    conf = pd.DataFrame(h["by_confidence"])
    fills = s.get("confidence", {})
    conf["Filled here"] = conf["confidence"].map(fills).fillna(0).astype(int)
    conf[["volume_within_50", "weight_within_50"]] = 100 * conf[["volume_within_50", "weight_within_50"]]
    st.dataframe(conf.rename(columns={"confidence": "Confidence", "n": "Hidden items",
                                      "volume_factor": "Volume error", "volume_within_50": "Volume within ±50%",
                                      "weight_factor": "Weight error", "weight_within_50": "Weight within ±50%"}),
                 hide_index=True, width="stretch", column_config={
                     "Volume error": st.column_config.NumberColumn(format="×%.2f"),
                     "Weight error": st.column_config.NumberColumn(format="×%.2f"),
                     "Volume within ±50%": st.column_config.NumberColumn(format=PCT),
                     "Weight within ±50%": st.column_config.NumberColumn(format=PCT)})
    st.caption("Hidden items often have a near-twin (a colour or revision of the same part), so the real gaps, "
               "which have fewer, sit lower in this table: read each fill's error from its confidence row.")


def _wide(values: pd.DataFrame, items: pd.DataFrame, action: str) -> pd.DataFrame:
    """One row per SKU with an action: description, sizes used (or kept) and the estimates."""
    v = values[values["action"] == action]
    if v.empty:
        return pd.DataFrame()
    est = v.pivot_table(index="sku", columns="field", values="estimate", aggfunc="first")
    orig = v.pivot_table(index="sku", columns="field", values="original", aggfunc="first")
    meta = v.drop_duplicates("sku").set_index("sku")[["method", "confidence", "similarity", "codes", "lines",
                                                       "neighbours"]]
    desc = items.drop_duplicates("sku").set_index("sku").get("description")
    out = meta.copy()
    out.insert(0, "description", desc.reindex(out.index) if desc is not None else "")
    for f in SIZE_COLS:
        if f in orig:
            out[f"client {f[5:]}"] = orig[f]
        if f in est:
            out[f"estimate {f[5:]}"] = est[f]
    return out.sort_values(["lines"], ascending=False).reset_index()


def _lists(name: str, r) -> None:
    st.subheader("Filled")
    filled = _wide(r.values, r.items, "filled")
    if filled.empty:
        st.success("Nothing to fill: no missing, placeholder or copied sizes among active SKUs.", icon="✅")
    else:
        t = BACKFILL_TEXT["filled"]
        st.caption(f"{t.what} {t.impact} Busiest first.")
        st.dataframe(filled.drop(columns=["codes"]).head(LIST_ROWS), hide_index=True, width="stretch")
        st.download_button("Download filled items", filled.to_csv(index=False).encode("utf-8"),
                           f"{name}_backfill_filled.csv", "text/csv", key="bf::dl_filled", icon=":material/download:")
    st.subheader("To review")
    review = _wide(r.values, r.items, "kept_review")
    if review.empty:
        st.success("Nothing to review: every doubted value is close to its estimate.", icon="✅")
        return
    t = BACKFILL_TEXT["kept_review"]
    st.caption(f"{t.what} {t.impact} Busiest first.")
    st.dataframe(review.head(LIST_ROWS), hide_index=True, width="stretch")
    st.download_button("Download items to review for the client", review.to_csv(index=False).encode("utf-8"),
                       f"{name}_backfill_review.csv", "text/csv", key="bf::dl_review", icon=":material/download:")


def _lookup(r) -> None:
    st.subheader("Look up a SKU")
    skus = r.values.sort_values("lines", ascending=False)["sku"].drop_duplicates().tolist()
    if not skus:
        st.caption("No estimated SKUs to look up.")
        return
    sku = st.selectbox("SKU", skus, key="bf::lookup", help="Every SKU backfill estimated, busiest first.")
    v = r.values[r.values["sku"] == sku]
    items = r.items.drop_duplicates("sku").set_index("sku")
    first = v.iloc[0]
    desc = items.at[sku, "description"] if "description" in items else ""
    m = BACKFILL_TEXT.get(v.loc[v["action"] == "filled", "method"].iat[0] if (v["action"] == "filled").any()
                          else first["method"])
    st.markdown(f"**{sku}** · {desc if pd.notna(desc) else ''}  \nQuality's doubts: {first['codes']}  \n"
                f"{m.title if m else ''}: {m.what if m else ''}")
    st.dataframe(v[["field", "original", "estimate", "value", "action", "method", "confidence"]],
                 hide_index=True, width="stretch")
    nb = [s for s in str(first["neighbours"] or "").split(", ") if s]
    if nb:
        st.markdown(f"**Borrowed from** {len(nb)} trusted items (similarity of the closest: "
                    f"{first['similarity']:.2f}):")
        cols = [c for c in ["description"] + SIZE_COLS if c in items]
        st.dataframe(items.loc[[s for s in nb if s in items.index], cols].reset_index(), hide_index=True,
                     width="stretch")


def page() -> None:
    name = _header()
    if not name:
        return
    body = st.container()
    settings_box = st.container()
    with settings_box:
        p = _settings(name)
        st.caption("Not done here: case packs and case sizes, and SKUs ordered but missing from the item master.")
        if st.button("Continue to Profile →", key="bf::to_profile", type="primary",
                     help="Volumes, order shape, ABC, and the design capacity from service targets."):
            st.session_state["pf::client"] = name
            from gtpfit.app import pages
            st.switch_page(pages.PROFILE)
    with body:
        with st.spinner("Estimating sizes and testing the estimates…"):
            r = state.result(name, p)
        _tiles(r.summary)
        _accuracy(r.summary)
        _lists(name, r)
        _lookup(r)
