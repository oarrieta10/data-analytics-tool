"""pages.py — the workbench pages, in module order, so one page can link or switch to another."""
import streamlit as st

from gtpfit.app import ingest, quality, scope

INGEST = st.Page(ingest.page, title="1 · Ingest", icon=":material/input:", url_path="ingest", default=True)
SCOPE = st.Page(scope.page, title="1b · Scope", icon=":material/filter_alt:", url_path="scope")
QUALITY = st.Page(quality.page, title="2 · Quality", icon=":material/fact_check:", url_path="quality")
ALL = [INGEST, SCOPE, QUALITY]
