"""done.py — step 4: write the outputs, hand over the receipt and rejects, remove the dropped files."""
from __future__ import annotations

import streamlit as st

from gtpfit.ingest import write_outputs

from . import state, widgets


def show() -> None:
    widgets.header(editable=False)
    final = st.session_state.get("ing::final")
    if not final:
        state.go(3)
    if "ing::out" not in st.session_state:
        out = write_outputs(final["result"], state.output_dir(), client=state.client(),
                            detections=final["detections"], requested=final["requested"])
        state.write_client_yaml(state.client(), wms_profile=st.session_state.get("ing::wms"),
                                mode=state.mode(), outputs=str(out))
        if state.mode() == "Drop files":
            state.remove_uploads()
        st.session_state["ing::out"] = str(out)
    from pathlib import Path
    out = Path(st.session_state["ing::out"])
    st.success(f"Ingest complete for **{state.client()}**. Outputs written to `{out}`.", icon="✅")
    files = sorted(p for p in out.rglob("*") if p.is_file())
    st.dataframe({"file": [str(p.relative_to(out)) for p in files],
                  "KB": [round(p.stat().st_size / 1024, 1) for p in files]}, hide_index=True, width="stretch")
    if state.mode() == "Drop files":
        st.caption("The dropped files have been removed from this computer's temporary folder; only these outputs "
                   "are kept.")
    c1, c2, c3 = st.columns([1.3, 1.3, 2])
    c1.download_button("Download data receipt", (out / "data_receipt.html").read_bytes(),
                       f"{state.client()}_data_receipt.html", "text/html", key="ing::dl_receipt4")
    c2.download_button("Download rejects", (out / "rejects_for_client.csv").read_bytes(),
                       f"{state.client()}_rejects.csv", "text/csv", key="ing::dl_rej4")
    if c3.button("Start another client", key="ing::restart"):
        state.reset()
        state.go(1)
