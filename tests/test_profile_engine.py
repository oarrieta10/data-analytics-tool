"""profile/ stage 1: the operating calendar, daily demand per channel, the FIFO backlog simulation by age, and the
SLA-driven design capacity.  Small hand-built series whose answers can be worked out by hand; the check against
the capacity workbook on a real client is in tests/test_profile_real.py (local only)."""
import dataclasses
import math

import numpy as np
import pandas as pd
import pytest

from gtpfit.profile import ProfileParams
from gtpfit.profile.backlog import percent_rank, simulate
from gtpfit.profile.demand import calendar, daily_demand
from gtpfit.profile.design import solve, what_if

P = ProfileParams()


def _days(n, start="2025-03-03"):  # 2025-03-03 is a Monday
    return pd.bdate_range(start, periods=n)


def _demand(values, channel="All", start="2025-03-03"):
    return pd.DataFrame({channel: np.asarray(values, float)}, index=_days(len(values), start))


def _open(n, closed=()):
    o = pd.Series(True, index=_days(n))
    for i in closed:
        o.iloc[i] = False
    return o


# ---- the simulation ---------------------------------------------------------------------------------------

def test_backlog_ages_and_clears_oldest_first():
    s = simulate(_demand([10, 0, 0]), 4, _open(3), max_age=6)
    proc = s.processed_by_age("All")
    assert proc[0] == 4 and proc[1] == 4 and proc[2] == 2           # same day, a day late, two days late
    k = s.kpis
    assert k["service"]["All"]["by_age"][:3] == pytest.approx([0.4, 0.4, 0.2])
    assert k["service"]["All"]["cumulative"][2] == pytest.approx(1.0)
    assert k["days_with_backlog"] == 2 and k["max_days_to_clear"] == pytest.approx(10 / 4)
    assert k["avg_fulfillment_days"] == pytest.approx((1 * 4 + 2 * 4 + 3 * 2) / 10)  # same day counts as day 1


def test_within_n_days_judges_only_work_with_n_days_left_in_the_history():
    # the last day's 10 cannot be done "within 2 days" before the history ends: they are not held against it
    s = simulate(_demand([4, 4, 4, 10]), 6, _open(4), max_age=6)
    w = s.kpis["service"]["All"]["within"]
    assert w[2] == pytest.approx(1.0)                          # days 1-2 (the only ones with 2 days left): all done
    assert s.kpis["service"]["All"]["cumulative"][2] < 1.0     # the plain table still counts the end backlog


def test_capacity_is_never_exceeded_and_work_is_conserved():
    rng = np.random.default_rng(5)
    d = rng.integers(0, 50, 40)
    s = simulate(_demand(d), 25, _open(40), max_age=6)
    assert (s.daily["processed"] <= 25 + 1e-9).all()
    assert s.kpis["total_demand"] == pytest.approx(s.kpis["processed"] + s.kpis["ending_backlog"])
    assert s.kpis["balanced"]


def test_oldest_work_goes_first():
    s = simulate(_demand([8, 5]), 5, _open(2), max_age=6)
    day2 = s.daily.iloc[1]
    assert day2["processed_age1"] == 3 and day2["processed_age0"] == 2  # yesterday's 3 before today's 5


def test_work_older_than_max_age_is_kept_not_dropped():
    s = simulate(_demand([100] + [0] * 9), 1, _open(10), max_age=2)
    assert s.kpis["balanced"] and s.kpis["ending_backlog"] == pytest.approx(90)
    assert s.daily["start_age2"].iloc[-1] == pytest.approx(91)  # the "2+" bucket accumulates


def test_closed_day_neither_processes_nor_ages_work():
    s = simulate(_demand([10, 0, 0]), 6, _open(3, closed=[1]), max_age=6)
    assert s.daily["processed"].tolist() == [6, 0, 4]
    assert s.processed_by_age("All")[1] == 4 and s.processed_by_age("All")[2] == 0  # still one open day late
    assert s.kpis["open_days"] == 2


def test_channels_share_capacity_in_proportion_within_an_age():
    d = pd.DataFrame({"Ecomm": [6.0], "Retail": [2.0]}, index=_days(1))
    s = simulate(d, 4, _open(1), max_age=6)
    assert s.processed_by_age("Ecomm")[0] == 3 and s.processed_by_age("Retail")[0] == 1
    assert s.kpis["service"]["Ecomm"]["by_age"][0] == pytest.approx(0.5)


def test_capacity_kpis():
    s = simulate(_demand([10, 20, 30, 40]), 30, _open(4), max_age=6)
    k = s.kpis
    assert k["design_to_average"] == pytest.approx(30 / 25) and k["peak_day_coverage"] == pytest.approx(30 / 40)
    assert k["utilization"] == pytest.approx(100 / (30 * 4))
    assert k["percentile_rank"] == pytest.approx(percent_rank([10, 20, 30, 40], 30))


def test_percent_rank_matches_the_spreadsheet_definition():
    assert percent_rank([1, 2, 3, 4, 5], 3.5) == pytest.approx(0.625)  # PERCENTRANK.INC({1..5}, 3.5)
    assert percent_rank([1, 2, 3, 4, 5], 5) == 1.0 and percent_rank([1, 2, 3, 4, 5], 9) == 1.0
    assert percent_rank([1, 2, 3, 4, 5], 1) == 0.0


# ---- the calendar and daily demand ------------------------------------------------------------------------

def _orders(day_counts: dict, channel=None):
    rows = []
    for day, n in day_counts.items():
        for i in range(n):
            rows.append(dict(order_id=f"{day}-{i}", sku=f"S{i % 3}", qty=2, order_date=pd.Timestamp(day),
                             **({"channel": channel} if channel else {})))
    return pd.DataFrame(rows)


def test_calendar_finds_working_weekdays_and_closed_days():
    days = {d.strftime("%Y-%m-%d"): 3 for d in _days(20)}       # four weeks, Monday to Friday
    days.pop("2025-03-17")                                       # a Monday with no orders: closed
    days["2025-03-15"] = 2                                       # one Saturday: not a working day
    cal = calendar(_orders(days).order_date, P)
    assert cal.open_weekdays == (0, 1, 2, 3, 4)
    assert pd.Timestamp("2025-03-17") in cal.closed and len(cal.days) == 20


def test_saturday_demand_arrives_on_monday_and_closed_days_get_no_capacity():
    days = {d.strftime("%Y-%m-%d"): 3 for d in _days(20)}
    days.pop("2025-03-17")
    days["2025-03-15"] = 2                                       # Saturday before the closed Monday
    dem, open_ = daily_demand({"orders": _orders(days)}, "row", P)
    assert dem.loc["2025-03-18", "All"] == 3 + 2                 # Saturday's 2 roll past the closed Monday
    assert dem.loc["2025-03-17", "All"] == 0 and not open_.loc["2025-03-17"]
    on = daily_demand({"orders": _orders(days)}, "row", dataclasses.replace(P, closed_days_have_capacity=True))[1]
    assert on.loc["2025-03-17"]


def test_weekend_work_can_count_on_the_previous_working_day():
    days = {d.strftime("%Y-%m-%d"): 3 for d in _days(20)}      # four weeks: one Saturday is not a working day
    days["2025-03-08"] = 2                                       # a Saturday
    prev = dataclasses.replace(P, off_day_orders_to="previous working day")
    dem, _ = daily_demand({"orders": _orders(days)}, "row", prev)
    assert dem.loc["2025-03-07", "All"] == 3 + 2 and dem.loc["2025-03-10", "All"] == 3   # Friday, not Monday


def test_demand_units_and_channels():
    o = pd.concat([_orders({"2025-03-03": 4}, "Ecomm"), _orders({"2025-03-03": 2}, "Retail")])
    lines, _ = daily_demand({"orders": o}, "row", P)
    assert lines.loc["2025-03-03"].to_dict() == {"Ecomm": 4, "Retail": 2}
    units, _ = daily_demand({"orders": o}, "row", dataclasses.replace(P, demand_unit="units"))
    assert units.loc["2025-03-03"].to_dict() == {"Ecomm": 8, "Retail": 4}


# ---- the design capacity ----------------------------------------------------------------------------------

DEM = _demand([100, 300, 120, 80, 400, 90, 110, 250, 60, 130])


def test_smallest_capacity_that_meets_the_sla():
    sla = {"All": {"within": [(0, 0.9)]}}
    d = solve(DEM, _open(10), sla, P)
    assert d.method == "sla" and d.kpis["service"]["All"]["within"][0] >= 0.9
    worse = what_if(DEM, _open(10), d.capacity - 1, P)
    assert worse.kpis["service"]["All"]["within"][0] < 0.9
    assert d.binding == ["All: 90% same day"]


def test_strictest_channel_decides():
    dem = pd.DataFrame({"Ecomm": [50, 150, 60, 40, 200.0], "Retail": [50, 150, 60, 40, 200.0]}, index=_days(5))
    loose = solve(dem, _open(5), {"Retail": {"within": [(3, 1.0)]}}, P)
    strict = solve(dem, _open(5), {"Retail": {"within": [(3, 1.0)]}, "Ecomm": {"within": [(0, 1.0)]}}, P)
    assert strict.capacity > loose.capacity and strict.binding == ["Ecomm: 100% same day"]


def test_max_backlog_days_target():
    d = solve(DEM, _open(10), {"All": {"max_backlog_days": 1.5}}, P)
    assert d.kpis["max_days_to_clear"] <= 1.5
    assert what_if(DEM, _open(10), d.capacity - 1, P).kpis["max_days_to_clear"] > 1.5
    assert d.binding == ["All: backlog at most 1.5 days"]


def test_no_sla_falls_back_to_the_percentile_day():
    d = solve(DEM, _open(10), {}, P)
    assert d.method == "fallback_percentile"
    assert d.capacity == math.ceil(np.quantile(DEM["All"], 0.95))


def test_impossible_target_says_so():
    with pytest.raises(ValueError, match="share"):
        solve(DEM, _open(10), {"All": {"within": [(0, 1.2)]}}, P)
