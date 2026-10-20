"""upload.py — step 1: who the client is, and the folder holding their files (read in place, nothing copied).

The folder comes from the operating system's folder dialog, a pasted path, or
one of the client's recent folders.  Once chosen, the page moves to Confirm."""
from __future__ import annotations

import streamlit as st

from gtpfit.ingest.request_template import request_html, request_xlsx
from gtpfit.ingest.schema import REQUIRED_TABLES, TABLE_LABEL

from . import state, widgets

_CARD_CSS = """<style>
.st-key-ing-folder-card {border: 2px dashed rgba(128,128,128,.55); border-radius: 12px; padding: 28px 24px;
  text-align: center; background: rgba(128,128,128,.06);}
</style>"""


def _open(path) -> None:
    err = state.open_folder(path)  # moves to step 2 when the folder is usable
    if err:
        st.error(err, icon="⛔")


def _folder_card(named: bool) -> None:
    st.markdown(_CARD_CSS, unsafe_allow_html=True)
    with st.container(key="ing-folder-card"):
        st.markdown("### Client files")
        st.markdown("Pick the folder holding the client's files. Nothing is copied.")
        if st.button("Choose folder…", type="primary", key="ing::choose", disabled=not named,
                     icon=":material/folder_open:", help="Opens this computer's folder picker."):
            path = state.choose_folder_dialog()
            if path:
                _open(path)
            else:
                st.info("No folder chosen. You can also paste a path below.", icon="ℹ️")
        if not named:
            st.caption("Name the client first.")
    c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
    paste = c1.text_input("Or paste a folder path", key="ing::folder_paste", disabled=not named,
                          placeholder=r"e.g. C:\Clients\Acme\2026-10 extract")
    if c2.button("Open", key="ing::folder_open", disabled=not named):
        _open(paste)
    recent = state.recent_folders(state.client()) if named else []
    if recent:
        st.caption(f"Recent folders for {state.client()}:")
        for i, f in enumerate(recent):
            if st.button(f, key=f"ing::recent::{i}", icon=":material/history:"):
                _open(f)


def _step_cards() -> None:
    c1, c2, c3 = st.columns(3)
    with c1.container(border=True):
        st.markdown("**1 · Choose folder**")
        lines = []
        for t in TABLE_LABEL:
            need = "required" if t in REQUIRED_TABLES else "optional"
            lines.append(f"- **{TABLE_LABEL[t]}** ({need}): enables {state.UNLOCKS[t]}")
        st.markdown("\n".join(lines))
    with c2.container(border=True):
        st.markdown("**2 · Confirm files**")
        st.markdown("Check what each file holds and which client column feeds which field. Columns confirmed "
                    "before for this client are reused.")
    with c3.container(border=True):
        st.markdown("**3 · Review**")
        st.markdown("See what blocks a design, what the history covers, and the rows set aside. Accept a block "
                    "only with a reason.")


def show() -> None:
    widgets.header(editable=True)
    named = bool(state.client())
    _folder_card(named)
    _step_cards()
    c1, c2, _ = st.columns([1.3, 1.3, 3])
    c1.download_button("Data request template (HTML)", request_html(), "data_request.html", "text/html",
                       icon=":material/description:", key="ing::tpl_html")
    c2.download_button("Data request template (Excel)", request_xlsx(), "data_request.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       icon=":material/table:", key="ing::tpl_xlsx")
