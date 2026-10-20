"""review.py — step 3: can I trust it, and what does it cover.

Verdict first, then one card per block (accept only with a reason, or ask the
client to resend), the coverage strip, and the details.  "Continue" stays
disabled while any block is open."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from gtpfit.ingest import Source
from gtpfit.ingest.exports import client_rejects
from gtpfit.ingest.receipt import data_receipt

from . import state, widgets


def current_result():
    """Run (or reuse) ingest for the confirmed files, profile, thresholds and accepted blocks."""
    assign = {t: [Source(Path(p), part) for p, part in srcs] for t, srcs in st.session_state["ing::assign"].items()}
    spec = state.run_spec(assign, state.run_profile_text(st.session_state.get("ing::overrides")))
    return state.run_ingest(spec, state.input_files(), st.session_state.get("ing::expected")), spec


def show() -> None:
    widgets.header(editable=False)
    if "ing::assign" not in st.session_state:
        state.go(2)
    r, spec = current_result()
    widgets.verdict(r)
    widgets.block_cards(r)
    widgets.coverage_panel(r)

    iss = r.issues
    warns, notes = iss[iss.severity == "warn"], iss[iss.severity == "info"]
    with st.expander(f"Warnings ({len(warns)})"):
        widgets.issue_list(warns) if len(warns) else st.write("None.")
    with st.expander(f"Notes ({len(notes)})"):
        widgets.issue_list(notes) if len(notes) else st.write("None.")
    with st.expander("Units detected"):
        if len(r.units):
            st.dataframe(r.units, hide_index=True, width="stretch")
        else:
            st.write("No item sizes to check.")
    n_rej = sum(len(v) for v in r.rejects.values() if v is not None)
    rej = client_rejects(r)
    with st.expander(f"Rejected rows ({n_rej:,})"):
        rows = rej[rej.record == "rejected row"]
        st.dataframe(rows.head(1000), hide_index=True, width="stretch")
        if len(rows) > 1000:
            st.caption(f"First 1,000 of {len(rows):,} shown; the download has them all.")
    st.download_button("Download rejects for the client", rej.to_csv(index=False).encode("utf-8"),
                       f"{state.client()}_rejects.csv", "text/csv", key="ing::dl_rej", icon=":material/download:")
    with st.expander("Details"):
        widgets.details(r, state.run_key(spec, state.input_files()))

    st.divider()
    c2, c3, c4 = st.columns([1.3, 1, 1.6])
    dets = state.detected(state.input_files())
    c2.download_button("Download data receipt",
                       data_receipt(r, state.client(), dets, st.session_state.get("ing::requested")).encode("utf-8"),
                       f"{state.client()}_data_receipt.html", "text/html", key="ing::dl_receipt")
    if c3.button("← Back", key="ing::back2"):
        state.go(2)
    if c4.button("Continue to Quality →", type="primary", key="ing::to4", disabled=r.blocked):
        st.session_state["ing::final"] = dict(result=r, detections=dets,
                                              requested=list(st.session_state.get("ing::requested", [])))
        state.go(4)
    if r.blocked:
        st.caption("Continue is available once every block is fixed or accepted with a reason.")
