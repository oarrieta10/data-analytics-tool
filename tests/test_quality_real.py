"""Quality on the real clients on this laptop (data/real, git-ignored).  Skipped wherever there are none.

Runs quality on every workspace client whose last ingest came from data/real/, and prints what it found and
how long it took, so the checks are seen on real extracts.  Nothing here is committed: no client values
appear in this file, and the results stay in the test output.

    python -m pytest tests/test_quality_real.py -s
"""
import time

import pytest
import yaml

from gtpfit import workspace

REAL = workspace.ROOT / "data" / "real"


def _real_clients() -> list[str]:
    base = workspace.DEFAULT_WORKSPACE
    if not REAL.exists() or not base.exists():
        return []
    out = []
    for d in sorted(base.iterdir()):
        y = d / "client.yaml"
        if (d / "_ingest" / "item_master.parquet").exists() and y.exists():
            last = str((yaml.safe_load(y.read_text(encoding="utf-8")) or {}).get("last_folder", ""))
            if str(REAL) in last or "data\\real" in last or "data/real" in last:
                out.append(d.name)
    return out


CLIENTS = _real_clients()
pytestmark = pytest.mark.skipif(not CLIENTS, reason="no real client data on this machine")


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
        print(f"   {p['code']:26s} {p['severity']:9s} {p['skus']:>10,} SKUs  {p['share_of_lines']:6.1%} of lines")
    assert len(r.status) >= s["counts"]["skus_active"]
    assert secs < 600, "quality must stay usable on full-size data"
