"""app — local workbench for inspecting and playing with each module before the next is built.

    python -m gtpfit.app        (opens http://localhost:8501)

One page per module, each with live controls for that module's parameters.
A development and review tool; the client deliverable is the HTML report.
Needs the optional extra: pip install -e .[app]
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # repo root: profiles/, wms_profiles/, data/
