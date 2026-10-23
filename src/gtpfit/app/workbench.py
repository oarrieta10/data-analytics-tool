"""workbench.py — Streamlit entry point; one page per module."""
import streamlit as st

from gtpfit.app import loader, pages, theme

st.set_page_config(page_title="gtp-fit workbench", page_icon=":material/warehouse:", layout="wide")
loader.install()  # the HAI robot in place of Streamlit's running icon
theme.logo()      # Hai Robotics logo, top left

nav = st.navigation(pages.ALL)
nav.run()
