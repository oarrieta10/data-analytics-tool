"""Command line: python -m gtpfit.ingest FOLDER [--wms PROFILE.yaml] [--accept CODE ...] [--out DIR]"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .mapping import load_wms_profile
from .pipeline import ingest_folder, write_outputs

_MARK = {"block": "BLOCK", "accepted": "accepted", "warn": "warn", "info": "info"}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gtpfit.ingest", description="Read a client's warehouse files into canonical tables.")
    ap.add_argument("folder")
    ap.add_argument("--wms", help="WMS profile YAML (column names, units, date format)")
    ap.add_argument("--accept", nargs="*", default=[], help="block codes you have checked and accept")
    ap.add_argument("--out", help="output folder (default FOLDER/_ingest)")
    ap.add_argument("--quiet", action="store_true", help="hide info lines")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # Windows consoles: never crash on a client's characters
    t = time.time()
    profile = load_wms_profile(a.wms) if a.wms else None
    r = ingest_folder(a.folder, profile=profile, accept=a.accept, strict=False)
    for row in r.issues.itertuples():
        if a.quiet and row.severity == "info":
            continue
        print(f"{_MARK[row.severity]:>8}  {row.table or '-':<12} {row.code:<24} {row.message}")
    for name, df in r.tables.items():
        print(f"{'':>8}  {name:<12} {len(df):>10,} rows kept, {len(r.rejects[name]):,} set aside")
    if r.blocked:
        print("\nRefused: fix the files or re-run with --accept CODE for blocks you have checked.")
        sys.exit(2)
    out = write_outputs(r, a.out or Path(a.folder) / "_ingest")
    print(f"\nwrote {out}  ({time.time() - t:.1f}s)")


if __name__ == "__main__":
    main()
