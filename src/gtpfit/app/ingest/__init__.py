"""ingest — the workbench Ingest page as a guided flow: 1 Choose folder → 2 Confirm files → 3 Review → 4 Done.

Answers, in order: what did I receive, is it what I think it is, can I trust
it, and what does it cover.  Step state lives in st.session_state ("ing::step").
All the analysis is in gtpfit.ingest; this package only lays it out.
"""
from __future__ import annotations

import streamlit as st

from . import confirm, done, review, state, upload, widgets
from .confirm import default_mapping, effective_profile_text  # noqa: F401  (used by tests and other pages)

_SCREENS = {1: upload.show, 2: confirm.show, 3: review.show, 4: done.show}


def page() -> None:
    state.progress_slot()  # first, every run: a fixed place for the top-bar progress bar
    st.title("1 · Ingest")
    step = st.session_state.setdefault("ing::step", 1)
    widgets.stepper(step)
    _SCREENS[step]()
