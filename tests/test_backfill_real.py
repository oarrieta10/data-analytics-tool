"""Backfill and its holdout accuracy test on the real clients on this laptop (data/real, git-ignored).  Opt-in
and local only.

Runs backfill (with the holdout) on the most recent workspace client for each real folder and prints what it
filled, what it kept, and how far off the estimates of hidden items were, against guessing the median item.
Nothing here is committed: no client values appear in this file, and the results stay in the test output.

    GTPFIT_REAL_TESTS=1 python -m pytest tests/test_backfill_real.py -s
"""
import time

import pytest

from gtpfit import workspace
from test_quality_real import CLIENTS

pytestmark = pytest.mark.skipif(not CLIENTS, reason="real-client run is opt-in (GTPFIT_REAL_TESTS=1) and needs "
                                                    "data/real on this machine")


@pytest.fixture(autouse=True)
def _real_workspace(monkeypatch):
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(workspace.DEFAULT_WORKSPACE))


@pytest.mark.parametrize("client", CLIENTS or ["none"])
def test_backfill_runs_and_beats_guessing(client):
    from gtpfit.backfill import run_backfill
    t = time.time()
    r = run_backfill(client)
    secs = time.time() - t
    s, h = r.summary, r.summary["holdout"]
    print(f"\n== {client}: {s['donors']:,} trusted items, {s['targets']:,} doubted; backfill in {secs:.0f}s")
    print(f"   actions {s['actions']}  methods {s['methods']}  confidence {s['confidence']}")
    for b, d in s["ready_after"].items():
        print(f"   ready for unit fit, {b:8s} {s['ready_before'][b]['share']:6.1%} -> {d['share']:6.1%}")
    print(f"   gap left: " + ", ".join(f"{k} {v:.1%}" for k, v in s["gap_after"].items()))
    if not h["n"]:
        print(f"   holdout: {h['note']}")
        return
    print(f"   holdout: {h['n']:,} hidden of {h['pool']:,} trusted active items")
    for f, m in h["fields"].items():
        b = h["baseline"][f]
        print(f"   {f:7s} x{m['factor']:.2f} off, {m['within_50']:.0%} within ±50%   (median item: x{b['factor']:.2f}, "
              f"{b['within_50']:.0%})")
    for row in h["by_confidence"]:
        print(f"   {row['confidence']:7s} n={row['n']:>6,}  volume x{row['volume_factor']:.2f}, "
              f"weight x{row['weight_factor']:.2f}")
    assert h["fields"]["volume"]["factor"] <= h["baseline"]["volume"]["factor"]
    assert secs < 600, "backfill must stay usable on full-size data"
