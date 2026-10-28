"""fit/ on the real clients on this laptop (data/real, git-ignored).  Opt-in and local only.

Fits every active SKU of each real workspace client into every catalogue container and prints, per container and
cell layout, the share of SKUs and of lines that fit, what limits the rest, and how long it took.  Nothing here is
committed: no client values appear in this file.

    GTPFIT_REAL_TESTS=1 python -m pytest tests/test_fit_real.py -s
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
def test_fit_runs_on_real_client(client):
    from gtpfit.fit import run_fit
    t = time.time()
    r = run_fit(client)
    secs = time.time() - t
    print(f"\n== {client}: {r.summary['skus']:,} active SKUs x {len(r.containers)} containers in {secs:.1f}s")
    for cid, s in r.summary["containers"].items():
        lays = "  ".join(f"{k}: {v['skus_share']:.0%} SKUs / {v['lines_share']:.0%} lines" for k, v in s["layouts"].items())
        print(f"   {s['name'][:38]:38s} {lays}")
        print(f"   {'':38s} limits {s['limits']}  estimated sizes {s['estimated_share_of_fits']:.0%}"
              + (f"  placeholders {s['placeholders']}" if s["placeholders"] else ""))
    assert len(r.fit) == r.summary["skus"] * sum(len(c.layouts) if c.mode == "units" else 1 for c in r.containers)
    assert secs < 120, "fit must stay quick on full-size data"
