"""workbench.py — Streamlit entry point; one page per module."""
import streamlit as st

from gtpfit.app import ingest_page

st.set_page_config(page_title="gtp-fit workbench", page_icon=":material/warehouse:", layout="wide")

nav = st.navigation([
    st.Page(ingest_page.page, title="1 · Ingest", icon=":material/input:", url_path="ingest", default=True),
])
nav.run()
