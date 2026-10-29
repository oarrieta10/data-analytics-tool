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
# Charts: "lead with hai-blue, then safety-orange, black-3c and the greys; tint ramps for more series"
DARKER_BLUE = "#04068B"    # `darker-blue`: second primary colour
HAI_BLUE_2 = "#D1D8FF"     # `hai-blue-2`: Hai Blue tint, "fills in charts and diagrams"
SAFETY_ORANGE = "#FF9900"  # `safety-orange`: the one accent colour; reference lines and key values
INK_MUTED = "#5A5A5A"      # `ink-muted` (cool-grey-11): axis labels and captions on white
BLACK_3C = "#373737"       # `black-3c`: steel grey, the third chart colour after hai-blue and safety-orange
COOL_GREY_8 = "#AAAAAA"    # `cool-grey-8`: a lighter grey for minor or "unknown" series
LINE = "#E6E6E6"           # `line` (cool-grey-4): gridlines and hairlines
FONT = '"Open Sans", Arial, sans-serif'   # `sans`: the design and marketing typeface
# Not a brand colour: backlog is drawn in red because red means work waiting (kept at the consultant's request)
BACKLOG_RED = "#E03C31"


def style_chart(chart):
    """An Altair chart in the Hai Robotics style: Open Sans, ink-muted axis text, line-grey gridlines, no frame."""
    return (chart.configure(font=FONT)
            .configure_axis(labelColor=INK_MUTED, titleColor=INK_MUTED, gridColor=LINE, domainColor=LINE,
                            tickColor=LINE, labelFont=FONT, titleFont=FONT)
            .configure_legend(labelColor=INK_MUTED, labelFont=FONT, titleFont=FONT)
            .configure_view(strokeWidth=0))


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
