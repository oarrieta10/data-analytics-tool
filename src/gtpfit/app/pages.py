"""pages.py — the workbench pages, in module order, so one page can link or switch to another."""
import streamlit as st

from gtpfit.app import ingest, scope

INGEST = st.Page(ingest.page, title="1 · Ingest", icon=":material/input:", url_path="ingest", default=True)
SCOPE = st.Page(scope.page, title="2 · Scope", icon=":material/filter_alt:", url_path="scope")
ALL = [INGEST, SCOPE]
