"""ingest — the workbench Ingest page as a guided flow: 1 Choose folder → 2 Confirm files → 3 Review → 4 Done.

Answers, in order: what did I receive, is it what I think it is, can I trust
it, and what does it cover.  Step state lives in st.session_state ("ing::step").
All the analysis is in gtpfit.ingest; this package only lays it out.  An
unexpected error on a step is shown with its traceback and a way back to
step 1, instead of ending the session.
"""
from __future__ import annotations

import streamlit as st

from . import confirm, done, review, state, upload, widgets
from .confirm import default_mapping, effective_profile_text  # noqa: F401  (used by tests and other pages)

_SCREENS = {1: upload.show, 2: confirm.show, 3: review.show, 4: done.show}
_STEP_NAME = {1: "Choose folder", 2: "Confirm files", 3: "Review", 4: "Done"}


def page() -> None:
    state.progress_slot()  # first, every run: a fixed place for the top-bar progress bar
    st.title("1 · Ingest")
    step = st.session_state.setdefault("ing::step", 1)
    widgets.stepper(step)
    try:
        _SCREENS[step]()
    except Exception as e:  # last resort; Streamlit's rerun / stop signals are not Exceptions and pass through
        st.error(f"**Something went wrong on {_STEP_NAME[step]}:** {type(e).__name__}: {e}. The files were not "
                 "changed. Go back to choose the folder again; if it happens again, send the details below.",
                 icon="⚠️")
        with st.expander("Details"):
            st.exception(e)
        if step != 1 and st.button("← Back to choose folder", key="ing::error_back"):
            state.go(1)
