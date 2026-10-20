"""upload.py — step 1: who the client is, which WMS profile, and the files (dropped or a folder).

Files are sorted into four slots from their columns as soon as they arrive."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from gtpfit.app import ROOT
from gtpfit.ingest.readers import SUPPORTED
from gtpfit.ingest.request_template import request_html, request_xlsx
from gtpfit.ingest.schema import REQUIRED_TABLES, TABLE_LABEL

from . import state, widgets


def _folders() -> list[str]:
    """Folders under data/ holding client-style files (skips outputs and the workspace)."""
    base = ROOT / "data"
    out = []
    if base.exists():
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(base)
            if p.is_dir() and len(rel.parts) <= 3 and not any(x.startswith("_") for x in rel.parts) and \
                    rel.parts[0] != "workspace" and \
                    any(c.suffix.lower() in state.DATA_EXT for c in p.iterdir() if c.is_file()):
                out.append(p.relative_to(ROOT).as_posix())
    return out


def _slots(dets) -> None:
    by_table: dict = {}
    for d in dets:
        if d.table:
            by_table.setdefault(d.table, []).append(d)
    cols = st.columns(4)
    for col, table in zip(cols, TABLE_LABEL):
        req = table in REQUIRED_TABLES
        with col.container(border=True):
            st.markdown(f"**{TABLE_LABEL[table]}** {'(required)' if req else '(optional)'}")
            st.caption(f"Enables {state.UNLOCKS[table]}.")
            got = by_table.get(table, [])
            if got:
                for d in got:
                    rows = f" · {d.rows:,} rows" if d.rows is not None else ""
                    st.markdown(f"✅ {d.label}{rows}")
            elif req:
                st.markdown("◻️ *waiting for a file*")
            else:
                st.markdown("◻️ *not provided*")
    other = [d for d in dets if not d.table]
    if other:
        st.caption("Not recognised as data (you can still assign them on the next step): " +
                   ", ".join(d.label for d in other))


def show() -> None:
    widgets.header(editable=True)
    m = st.radio("Files from", ["Drop files", "A folder"], horizontal=True, key="ing::mode",
                 help="Drop files: copied to a temporary folder, removed after ingest. A folder: read in place.")
    if m == "Drop files":
        gen = st.session_state.get("ing::up_gen", 0)
        ups = st.file_uploader("Drop everything at once: item master, orders, receipts, inventory", key=f"up::{gen}",
                               accept_multiple_files=True,
                               help="Any of: " + "; ".join(f"{k} ({v})" for k, v in SUPPORTED))
        state.save_uploads(ups, state.upload_dir())
        st.caption("Up to 5 GB per file. For very large exports, **A folder** is faster: files are read where they "
                   "are, nothing is copied.")
    else:
        folders = _folders()
        choice = st.selectbox("Folder", folders + ["Other folder…"], key="ing::folder_pick")
        path = st.text_input("Folder path", value=str(ROOT / "data"), key="ing::folder_other") \
            if choice == "Other folder…" else str(ROOT / choice) if choice else ""
        st.session_state["ing::folder_path"] = path
    files = state.input_files()
    dets = state.detected(files)
    _slots(dets)
    c1, c2, _ = st.columns([1.3, 1.3, 3])
    c1.download_button("Data request template (HTML)", request_html(), "data_request.html", "text/html",
                       icon=":material/description:", key="ing::tpl_html")
    c2.download_button("Data request template (Excel)", request_xlsx(), "data_request.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       icon=":material/table:", key="ing::tpl_xlsx")
    widgets.thresholds()
    missing_client = not state.client()
    if missing_client:
        st.caption("Name the client to continue.")
    if st.button("Next: confirm files →", type="primary", key="ing::to2", disabled=missing_client or not files):
        state.go(2)
