"""Command line: python -m gtpfit.synth [--profile FILE] [--out DIR] [--report-only]"""
from __future__ import annotations

import argparse
import json
import time

from .config import load_profile
from .generate import generate
from .qa import write_report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gtpfit.synth", description="Generate a synthetic warehouse dataset with an answer key.")
    ap.add_argument("--profile", help="YAML profile (defaults are used for anything missing)")
    ap.add_argument("--out", default="data/synthetic/default")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--report-only", action="store_true", help="re-run the realism report on an existing folder")
    a = ap.parse_args(argv)
    t = time.time()
    if not a.report_only:
        kw = {"seed": a.seed} if a.seed is not None else {}
        m = generate(load_profile(a.profile, **kw), a.out)
        print(json.dumps(m["counts"], indent=2))
    path = write_report(a.out)
    print(f"report: {path}  ({time.time() - t:.1f}s)")


if __name__ == "__main__":
    main()
