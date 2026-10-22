"""workbench.py — Streamlit entry point; one page per module."""
import streamlit as st

from gtpfit.app import pages

st.set_page_config(page_title="gtp-fit workbench", page_icon=":material/warehouse:", layout="wide")

nav = st.navigation(pages.ALL)
nav.run()
