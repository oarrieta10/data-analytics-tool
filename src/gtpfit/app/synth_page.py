"""synth_page.py — generate synthetic clients from an editable profile and view the realism report."""
from __future__ import annotations

import pandas as pd
import streamlit as st
import yaml

from gtpfit.app import ROOT
from gtpfit.synth import generate, load_profile
from gtpfit.synth.qa import write_report

DATA = ROOT / "data" / "synthetic"


def _datasets():
    return sorted(p for p in DATA.iterdir() if (p / "manifest.json").exists()) if DATA.exists() else []


def page():
    st.title("Synthetic data")
    st.caption("A fake client with a known answer key (`_truth/`). Edit a profile, generate, and check the shape "
               "against typical warehouse ranges. Every later module is scored against these datasets.")

    gen, view = st.tabs(["Generate", "Inspect a dataset"])

    with gen:
        profiles = sorted((ROOT / "profiles").glob("*.yaml"))
        names = [p.stem for p in profiles]
        c1, c2 = st.columns([1, 3])
        base = c1.selectbox("Start from profile", names, index=names.index("playground") if "playground" in names else 0)
        src = ROOT / "profiles" / f"{base}.yaml"
        out_name = c1.text_input("Output folder", value=base, help=f"Written to data/synthetic/<name>")
        c1.caption("Anything you leave out keeps its default from `synth/config.py`. Big profiles "
                   "(e.g. messy_large) take ~40 s.")
        text = c2.text_area("Profile YAML: edit freely; only saved if you click Save profile",
                            src.read_text(encoding="utf-8"), height=460, key=f"yaml::{base}")
        b1, b2, _ = c1.columns([1, 1, 0.2])
        go = b1.button("Generate", type="primary", icon=":material/play_arrow:")
        save = b2.button("Save profile", icon=":material/save:")
        if save:
            (ROOT / "profiles" / f"{base}.yaml").write_text(text, encoding="utf-8")
            st.toast(f"Saved profiles/{base}.yaml")
        if go:
            try:
                p = load_profile(None, **(yaml.safe_load(text) or {}))
            except Exception as e:  # bad YAML or unknown key: show it, don't crash the page
                st.error(f"Profile problem: {e}")
            else:
                out = DATA / out_name
                with st.spinner(f"Generating {p.n_skus:,} SKUs x {p.days} days..."):
                    m = generate(p, out)
                    write_report(out)
                st.session_state["synth_view"] = out_name
                st.success(f"Wrote data/synthetic/{out_name}. Open the 'Inspect a dataset' tab.")
                cols = st.columns(4)
                for i, (k, v) in enumerate(m["counts"].items()):
                    cols[i % 4].metric(k.replace("_", " "), f"{v:,}")

    with view:
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
                st.caption("Share of e-commerce orders with a single line, as generated. It dips on event days "
                           "(promotions, Q4) when `peak_shape` makes baskets bigger. The profile module should find this.")
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
