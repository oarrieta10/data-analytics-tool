"""done.py — step 4: write the outputs to the client's workspace, hand over the receipt and rejects."""
from __future__ import annotations

import streamlit as st

from gtpfit.ingest import profile_from_result, write_outputs

from . import state, widgets


def show() -> None:
    widgets.header(editable=False)
    final = st.session_state.get("ing::final")
    if not final:
        state.go(3)
    if "ing::out" not in st.session_state:
        out = write_outputs(final["result"], state.output_dir(), client=state.client(),
                            detections=final["detections"], requested=final["requested"],
                            folder=state.input_folder(), line_definition=state.line_definition())
        used = profile_from_result(final["result"], state.client())  # the units and date format ingest used
        state.save_client_profile(units=used.units, date_format=used.date_format)
        state.write_client_yaml(state.client(), last_ingest=str(out), last_folder=str(state.input_folder()))
        st.session_state["ing::out"] = str(out)
    from pathlib import Path
    out = Path(st.session_state["ing::out"])
    st.success(f"Ingest complete for **{state.client()}**. Outputs written to `{out}`.", icon="✅")
    files = sorted(p for p in out.rglob("*") if p.is_file())
    st.dataframe({"file": [str(p.relative_to(out)) for p in files],
                  "KB": [round(p.stat().st_size / 1024, 1) for p in files]}, hide_index=True, width="stretch")
    st.caption(f"The client's files were read in place from `{state.input_folder()}`; nothing was copied or "
               "written there.")
    c1, c2, c3 = st.columns([1.3, 1.3, 2])
    c1.download_button("Download data receipt", (out / "data_receipt.html").read_bytes(),
                       f"{state.client()}_data_receipt.html", "text/html", key="ing::dl_receipt4")
    c2.download_button("Download rejects", (out / "rejects_for_client.csv").read_bytes(),
                       f"{state.client()}_rejects.csv", "text/csv", key="ing::dl_rej4")
    if st.button("Continue to Scope →", type="primary", key="ing::to_scope",
                 help="Decide which sites, channels and order types the design is for."):
        st.session_state["scp::client"] = state.client()
        from gtpfit.app import pages
        st.switch_page(pages.SCOPE)
    if c3.button("Start another client", key="ing::restart"):
        state.reset()
        state.go(1)
