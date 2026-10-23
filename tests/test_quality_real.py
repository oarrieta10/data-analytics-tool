"""Quality on the real clients on this laptop (data/real, git-ignored).  Opt-in and local only.

Runs quality on the most recent workspace client for each real folder (its last ingest came from
data/real/), and prints what it found and how long it took, so the checks are seen on real extracts.
Nothing here is committed: no client values appear in this file, and the results stay in the test output.
Opt-in, because full-size clients take minutes:

    GTPFIT_REAL_TESTS=1 python -m pytest tests/test_quality_real.py -s
"""
import os
import time

import pytest
import yaml

from gtpfit import workspace

REAL = workspace.ROOT / "data" / "real"


def _real_clients() -> list[str]:
    base = workspace.DEFAULT_WORKSPACE
    if not REAL.exists() or not base.exists():
        return []
    newest = {}  # real folder -> the most recently updated workspace client ingested from it
    for d in sorted(base.iterdir(), key=lambda d: d.stat().st_mtime):
        y = d / "client.yaml"
        if (d / "_ingest" / "item_master.parquet").exists() and y.exists():
            last = str((yaml.safe_load(y.read_text(encoding="utf-8")) or {}).get("last_folder", ""))
            if os.path.normcase(str(REAL)) in os.path.normcase(last):
                newest[last] = d.name
    return sorted(newest.values())


CLIENTS = _real_clients() if os.environ.get("GTPFIT_REAL_TESTS") == "1" else []
pytestmark = pytest.mark.skipif(not CLIENTS, reason="real-client run is opt-in (GTPFIT_REAL_TESTS=1) and needs "
                                                    "data/real on this machine")


@pytest.fixture(autouse=True)
def _real_workspace(monkeypatch):
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(workspace.DEFAULT_WORKSPACE))


@pytest.mark.parametrize("client", CLIENTS or ["none"])
def test_quality_runs_on_real_client(client):
    from gtpfit.quality import run_quality
    t = time.time()
    r = run_quality(client)
    secs = time.time() - t
    s = r.summary
    print(f"\n== {client}: {s['counts']['skus_in_item_master']:,} SKUs in item master, "
          f"{s['counts']['skus_active']:,} active in scope; quality in {secs:.0f}s")
    for fit, d in s["score"].items():
        print(f"   {fit:9s} " + "  ".join(f"{k} {v['share']:.1%} ({v['band']})" for k, v in d.items()))
    for p in s["problems"]:
        print(f"   {p['code']:26s} {p['severity']:9s} {p['skus_active']:>8,} active / {p['skus']:>10,} SKUs  "
              f"{p['share_of_lines']:6.1%} of lines")
    assert len(r.status) >= s["counts"]["skus_active"]
    assert secs < 600, "quality must stay usable on full-size data"
