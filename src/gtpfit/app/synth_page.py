"""synth_page.py — build a synthetic client with a form and a live preview; inspect generated datasets."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from gtpfit.app import ROOT
from gtpfit.app.synth_form import form_to_profile, load_into_form, render_section
from gtpfit.synth import generate, load_profile
from gtpfit.synth.config import SECTIONS, profile_diff, profile_yaml
from gtpfit.synth.preview import estimate, preview
from gtpfit.synth.qa import write_report

DATA = ROOT / "data" / "synthetic"
PROFILES = ROOT / "profiles"


def _datasets():
    return sorted(p for p in DATA.iterdir() if (p / "manifest.json").exists()) if DATA.exists() else []


def _load_preset():
    name = st.session_state["syn::preset"]
    load_into_form(load_profile(PROFILES / f"{name}.yaml"))
    st.session_state["syn::out"] = name
    st.session_state.pop("syn::yaml_error", None)


def _apply_yaml():
    """Paste YAML on top of the current settings."""
    try:
        data = yaml.safe_load(st.session_state.get("syn::yaml") or "") or {}
        if not isinstance(data, dict):
            raise ValueError("YAML must be a mapping of setting: value")
        cur = profile_diff(form_to_profile())
        for sec in ("defects", "peak_shape"):
            if sec in data:
                cur[sec] = {**cur.get(sec, {}), **(data.pop(sec) or {})}
        cur.update(data)
        load_into_form(load_profile(None, **cur))
        st.session_state.pop("syn::yaml_error", None)
    except Exception as e:  # show it next to the box, keep the form as it was
        st.session_state["syn::yaml_error"] = str(e)


def _generate_tab():
    names = sorted(p.stem for p in PROFILES.glob("*.yaml"))
    if "syn::preset" not in st.session_state:  # first visit: start from playground
        st.session_state["syn::preset"] = "playground" if "playground" in names else names[0]
        _load_preset()

    top = st.columns([1.2, 1.2, 2.2, 0.9], vertical_alignment="bottom")
    top[0].selectbox("Start from preset", names, key="syn::preset", on_change=_load_preset,
                     help="Loads a saved profile into the form. Unsaved changes are replaced.")
    top[1].text_input("Output folder", key="syn::out", help="Written to data/synthetic/<name> (or a full path).")

    try:
        p = form_to_profile()
    except Exception as e:
        st.error(f"Settings problem: {e}")
        return
    cal = preview(p)
    est = estimate(p, cal)
    top[2].markdown(f"**~{est['order_lines']:,} order lines** · ~{est['orders']:,} orders · "
                    f"{p.n_skus:,} SKUs · ~{est['seconds']:.0f} s to generate")
    go = top[3].button("Generate", type="primary", icon=":material/play_arrow:", key="syn::generate",
                       width="stretch")

    if go:
        out = Path(st.session_state["syn::out"])
        out = out if out.is_absolute() else DATA / out
        with st.spinner(f"Generating {p.n_skus:,} SKUs x {p.days} days..."):
            m = generate(p, out)
            write_report(out)
        st.session_state["synth_view"] = out.name
        st.success(f"Wrote {out}: {m['counts']['order_lines']:,} order lines, {m['counts']['orders']:,} orders. "
                   "See 'Inspect a dataset'.")

    left, right = st.columns([3, 2], gap="large")
    with left:
        for tab, sec in zip(st.tabs(SECTIONS), SECTIONS):
            with tab:
                render_section(sec)
    with right:
        _preview(p, cal)

    st.divider()
    s1, s2 = st.columns(2, gap="large")
    with s1:
        st.markdown("**Save as profile**")
        diff = profile_diff(p)
        name = st.text_input("Profile name", value=st.session_state["syn::preset"], key="syn::save_name")
        target = PROFILES / f"{name}.yaml"
        overwrite = st.checkbox(f"Overwrite profiles/{name}.yaml", key="syn::overwrite") if target.exists() else True
        if st.button("Save", icon=":material/save:", key="syn::save", disabled=not (name and overwrite)):
            target.write_text(f"# Saved from the workbench. Only settings that differ from the defaults.\n"
                              f"{profile_yaml(p)}", encoding="utf-8")
            st.toast(f"Saved profiles/{name}.yaml")
        st.caption(f"{len(diff)} setting group(s) differ from the defaults; only those are written.")
    with s2:
        with st.expander("Advanced: YAML"):
            st.markdown("Current settings (only what differs from the defaults):")
            st.code(profile_yaml(p) or "# all defaults", language="yaml")
            st.text_area("Paste YAML to apply on top of the current settings", key="syn::yaml", height=140,
                         placeholder="peak_shape:\n  ecom_single_line_share: 0.65")
            st.button("Apply YAML", key="syn::apply_yaml", on_click=_apply_yaml)
            if st.session_state.get("syn::yaml_error"):
                st.error(st.session_state["syn::yaml_error"])


def _preview(p, cal: pd.DataFrame):
    st.markdown("**Live preview**")
    st.caption("Recomputed from the calendar as you change settings; no data is generated.")
    c = cal.set_index(pd.to_datetime(cal["date"]))
    st.markdown("Daily order volume (index, 1 = average weekday)")
    st.line_chart(c["volume_factor"], height=200, x_label="date", y_label="index")
    st.markdown("E-commerce single-line share (%)")
    st.line_chart(c["ecom_single_line_share"] * 100, height=200, x_label="date", y_label="% single-line")
    top = c[c["volume_factor"] > 0].nlargest(3, "event_lift")
    st.caption("Biggest event days: " + ", ".join(
        f"{d:%b %d} (lift {r.event_lift:.2f}, {r.ecom_single_line_share:.0%} single-line)" for d, r in top.iterrows()))


def _inspect_tab():
    ds = _datasets()
    if not ds:
        st.info("No datasets yet; generate one first.")
        return
    names = [d.name for d in ds]
    pick = st.session_state.get("synth_view", names[0])
    name = st.selectbox("Dataset", names, index=names.index(pick) if pick in names else 0)
    d = DATA / name
    cal_path = d / "_truth" / "calendar.csv"
    if cal_path.exists():
        cal = pd.read_csv(cal_path, parse_dates=["date"])
        if "ecom_single_line_share" in cal:
            st.subheader("Answer key: e-commerce basket shape by day")
            st.caption("Share of e-commerce orders with a single line, as generated. The profile module should "
                       "find this.")
            st.line_chart(cal.set_index("date")["ecom_single_line_share"], height=220,
                          x_label="date", y_label="single-line share")
    rpt = d / "synthetic_data_report.html"
    if not rpt.exists():
        if st.button("Build realism report"):
            write_report(d)
            st.rerun()
        return
    st.subheader("Realism report")
    st.iframe(rpt, height=1400)  # our own generated report, not untrusted HTML


def page():
    st.title("Synthetic data")
    st.caption("A fake client with a known answer key (`_truth/`). Every later module is scored against these "
               "datasets. Hover any control for what it does.")
    gen, view = st.tabs(["Generate", "Inspect a dataset"])
    with gen:
        _generate_tab()
    with view:
        _inspect_tab()
