"""theme.py — the workbench's accent colour and logo, from the Hai Robotics design system.

The accent (primary buttons, ticked boxes, sliders, toggles, progress bars, focus rings, links) is Hai Blue in
light mode and its lighter tint in dark mode, where Hai Blue on black is too dark to read; this is the design
system's `accent` token.  Status colours (red blocks, orange warnings, green success) keep Streamlit's
defaults: they carry meaning.  Passed to Streamlit as command-line options by app/__main__.py.

The logo is the primary (horizontal) lockup, top left above the page list: full colour on light grounds,
the reverse (white) version in dark mode.  The files are the design system's own artwork, unchanged
(app/static/brand/; the brand book forbids redrawing or recolouring it).
"""
from __future__ import annotations

from pathlib import Path

# Hai Robotics design system (https://claude.ai/artifact/KBgzRxGv1ydpfyh9YGQevd), tokens.json
HAI_BLUE = "#2E31FA"    # `hai-blue`: primary brand colour; `accent` in light style
HAI_BLUE_4 = "#808AFF"  # `hai-blue-4`: `accent` in dark style (7:1 on black)


def streamlit_flags() -> list[str]:
    return ["--theme.light.primaryColor", HAI_BLUE, "--theme.light.linkColor", HAI_BLUE,
            "--theme.dark.primaryColor", HAI_BLUE_4, "--theme.dark.linkColor", HAI_BLUE_4]


BRAND = Path(__file__).with_name("static") / "brand"
# Logo height: Streamlit's "large" logo (32px) clears the brand book's 11px minimum for the primary lockup.
LOGO_SIZE = "large"


def logo_file(theme_type: str | None) -> Path:
    """The primary logo for this theme: reverse (white) on dark grounds, full colour otherwise."""
    name = "hai-robotics-primary-reverse.svg" if theme_type == "dark" else "hai-robotics-primary.svg"
    return BRAND / name


def logo() -> None:
    """The Hai Robotics logo, top left above the page list (Streamlit redraws it when the theme changes)."""
    import streamlit as st
    st.logo(str(logo_file(getattr(st.context.theme, "type", None))), size=LOGO_SIZE)
