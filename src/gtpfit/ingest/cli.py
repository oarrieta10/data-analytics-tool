"""Command line: python -m gtpfit.ingest FOLDER [--wms PROFILE.yaml] [--accept KEY=REASON ...] [--out DIR]

KEY is an issue code (excel_row_limit) or table:code (orders:date_gap).  A block
is only accepted with a reason; the reason is written to accepted_blocks.csv and
the data receipt.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .checks import NOT_ACCEPTABLE
from .mapping import load_wms_profile
from .pipeline import ingest_folder, write_outputs

_MARK = {"block": "BLOCK", "accepted": "accepted", "warn": "warn", "info": "info"}


def _accept_arg(items) -> dict:
    out = {}
    for item in items:
        key, sep, reason = item.partition("=")
        if not sep or not reason.strip():
            print(f"--accept {item}: give a reason, e.g. --accept \"{key}=client confirmed the extract is complete\"")
            sys.exit(2)
        code = key.strip().split(":")[-1]
        if code in NOT_ACCEPTABLE:
            print(f"--accept {key.strip()}: this block cannot be accepted; {NOT_ACCEPTABLE[code]}.")
            sys.exit(2)
        out[key.strip()] = reason.strip()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gtpfit.ingest", description="Read a client's warehouse files into canonical tables.")
    ap.add_argument("folder")
    ap.add_argument("--wms", help="WMS profile YAML (column names, units, date format)")
    ap.add_argument("--accept", nargs="*", default=[], metavar="KEY=REASON",
                    help="accept a block you have checked, with a reason: code=reason or table:code=reason")
    ap.add_argument("--out", help="output folder (default FOLDER/_ingest)")
    ap.add_argument("--client", help="client name for the data receipt")
    ap.add_argument("--quiet", action="store_true", help="hide info lines")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # Windows consoles: never crash on a client's characters
    accept = _accept_arg(a.accept)
    t = time.time()
    profile = load_wms_profile(a.wms) if a.wms else None
    r = ingest_folder(a.folder, profile=profile, accept=accept, strict=False)
    for row in r.issues.itertuples():
        if a.quiet and row.severity == "info":
            continue
        print(f"{_MARK[row.severity]:>8}  {row.table or '-':<12} {row.code:<24} {row.message}")
    for name, df in r.tables.items():
        print(f"{'':>8}  {name:<12} {len(df):>10,} rows kept, {len(r.rejects[name]):,} set aside")
    if r.blocked:
        print("\nRefused: fix the files, or re-run with --accept table:code=REASON for a block you have checked.")
        sys.exit(2)
    out = write_outputs(r, a.out or Path(a.folder) / "_ingest", client=a.client)
    print(f"\nwrote {out}  ({time.time() - t:.1f}s)")


if __name__ == "__main__":
    main()
