"""theme.py — the workbench's accent colour, from the Hai Robotics design system.

The accent (primary buttons, ticked boxes, sliders, toggles, progress bars, focus rings, links) is Hai Blue in
light mode and its lighter tint in dark mode, where Hai Blue on black is too dark to read; this is the design
system's `accent` token.  Status colours (red blocks, orange warnings, green success) keep Streamlit's
defaults: they carry meaning.  Passed to Streamlit as command-line options by app/__main__.py.
"""
from __future__ import annotations

# Hai Robotics design system (https://claude.ai/artifact/KBgzRxGv1ydpfyh9YGQevd), tokens.json
HAI_BLUE = "#2E31FA"    # `hai-blue`: primary brand colour; `accent` in light style
HAI_BLUE_4 = "#808AFF"  # `hai-blue-4`: `accent` in dark style (7:1 on black)


def streamlit_flags() -> list[str]:
    return ["--theme.light.primaryColor", HAI_BLUE, "--theme.light.linkColor", HAI_BLUE,
            "--theme.dark.primaryColor", HAI_BLUE_4, "--theme.dark.linkColor", HAI_BLUE_4]
