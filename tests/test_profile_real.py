"""profile/ against the consultant's capacity workbook, on the real client it was built from.  Opt-in and local.

The workbook (data/capacity model/*.xlsm, git-ignored) simulates daily demand through a fixed capacity; its
saved values are the answer key.  The client is the workspace client whose total order lines equal the
workbook's total demand, and the expected figures are read from the workbook when the test runs: no client name
or value is written here.  The workbook works every weekday, holidays included, so this test runs the model the
same way (closed days with capacity), and counts an occasional Saturday's work on the Friday before.

    GTPFIT_REAL_TESTS=1 python -m pytest tests/test_profile_real.py -s
"""
import dataclasses
import os
from pathlib import Path

import pytest

from gtpfit import tables, workspace

BOOK_DIR = workspace.ROOT / "data" / "capacity model"


def _book():
    books = sorted(BOOK_DIR.glob("*.xls*")) if BOOK_DIR.exists() else []
    if os.environ.get("GTPFIT_REAL_TESTS") != "1" or not books:
        return None
    import openpyxl
    ws = openpyxl.load_workbook(books[-1], data_only=True).worksheets[-1]
    val = {c: ws[c].value for c in ("D1", "D3", "D4", "AH3", "AI9", "AI10", "AI11", "AM10", "AM11", "AM12", "AM13",
                                    "AM15", "AM16")}
    return {"capacity": val["D3"], "total": val["AH3"], "rank": val["D4"], "same_day": val["AI9"],
            "day2": val["AI10"], "day3": val["AI11"], "days_with_backlog": val["AM13"], "max_to_clear": val["D1"],
            "avg_fulfillment": val["AM15"], "utilization": val["AM16"], "design_avg": val["AM11"],
            "peak_cov": val["AM12"], "days": val["AM10"]}


BOOK = _book()
pytestmark = pytest.mark.skipif(BOOK is None, reason="opt-in (GTPFIT_REAL_TESTS=1) and needs the capacity workbook")


@pytest.fixture(autouse=True)
def _real_workspace(monkeypatch):
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(workspace.DEFAULT_WORKSPACE))


def _client():
    base = workspace.DEFAULT_WORKSPACE
    for d in sorted(base.iterdir()) if base.exists() else []:
        f = d / "_ingest" / "orders.parquet"
        if f.exists():
            import pyarrow.parquet as pq
            if pq.ParquetFile(f).metadata.num_rows == BOOK["total"]:
                return d.name
    pytest.skip("no workspace client matches the workbook's total demand")


def test_matches_the_capacity_workbook():
    from gtpfit.profile import ProfileParams, daily_demand, what_if
    client = _client()
    p = dataclasses.replace(ProfileParams(), closed_days_have_capacity=True, off_day_orders_to="previous working day")
    dem, open_ = daily_demand(tables.load(client), "row", p)
    s = what_if(dem, open_, BOOK["capacity"], p)
    k = s.kpis
    svc = k["service"]["All"]["by_age"]
    print(f"\n== {client} at {BOOK['capacity']:,}/day: {k['days_in_model']} days")
    rows = [("days in model", k["days_in_model"], BOOK["days"]),
            ("same day", svc[0], BOOK["same_day"]), ("+1 day", svc[1], BOOK["day2"]), ("+2 days", svc[2], BOOK["day3"]),
            ("days with backlog", k["days_with_backlog"], BOOK["days_with_backlog"]),
            ("max days to clear", k["max_days_to_clear"], BOOK["max_to_clear"]),
            ("avg fulfillment days", k["avg_fulfillment_days"], BOOK["avg_fulfillment"]),
            ("utilization", k["utilization"], BOOK["utilization"]),
            ("design / average", k["design_to_average"], BOOK["design_avg"]),
            ("peak-day coverage", k["peak_day_coverage"], BOOK["peak_cov"]),
            ("percentile rank", k["percentile_rank"], BOOK["rank"])]
    for name, ours, theirs in rows:
        print(f"   {name:22s} ours {ours:10.4f}   workbook {theirs:10.4f}")
    assert k["days_in_model"] == BOOK["days"]
    assert svc[0] == pytest.approx(BOOK["same_day"], abs=0.005)
    assert k["utilization"] == pytest.approx(BOOK["utilization"], rel=0.005)
    assert k["peak_day_coverage"] == pytest.approx(BOOK["peak_cov"], rel=0.01)
    assert k["avg_fulfillment_days"] == pytest.approx(BOOK["avg_fulfillment"], abs=0.01)
    assert abs(k["days_with_backlog"] - BOOK["days_with_backlog"]) <= 3
    assert k["balanced"]


def test_default_calendar_and_an_sla_solve():
    from gtpfit.profile import ProfileParams, daily_demand, describe, solve
    client = _client()
    p = ProfileParams()
    dem, open_ = daily_demand(tables.load(client), workspace.line_definition(client), p)
    print(f"\n== {client}: {len(dem)} working days, {int((~open_).sum())} closed; channels {list(dem.columns)}")
    fb = solve(dem, open_, {}, p)
    print(f"   no SLA: {fb.capacity:,}/day (fallback {p.fallback_percentile:.0%} day)")
    chans = list(dem.columns)
    sla = {chans[0]: {"within": [(0, 0.95)]}, chans[-1]: {"within": [(2, 1.0)]}}
    d = solve(dem, open_, sla, p)
    print(f"   SLA {describe(sla)} -> {d.capacity:,}/day, binding {d.binding}; "
          f"design/avg {d.kpis['design_to_average']:.2f}, days with backlog {d.kpis['days_with_backlog']}")
    assert d.capacity > 0 and d.binding
