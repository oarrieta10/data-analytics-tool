"""Database-style export conventions: units as snake_case suffixes (pce_lgth_mm, grss_ctn_wght_kg) and
timestamps with a zone suffix or a 12-hour clock."""
import pandas as pd
import pytest

from gtpfit.ingest.coerce import choose_date_format, to_date
from gtpfit.ingest.units import header_unit


@pytest.mark.parametrize("header,kind,unit", [
    ("pce_lgth_mm", "length", "mm"), ("ctn_hght_cm", "length", "cm"), ("pce_wght_g", "weight", "g"),
    ("grss_ctn_wght_kg", "weight", "kg"), ("unit-weight-lb", "weight", "lb"), ("Length (cm)", "length", "cm"),
])
def test_unit_suffix_in_snake_case(header, kind, unit):
    assert header_unit(header, kind) == unit


@pytest.mark.parametrize("header,kind", [("itm_lvl", "weight"), ("sz_cd", "length"), ("lgth_cd", "length"),
                                         ("msrmnt_dtime", "length"), ("cl_cd", "weight")])
def test_no_unit_from_ordinary_codes(header, kind):
    assert header_unit(header, kind) is None


def test_utc_timestamp_with_microseconds():
    s = pd.Series(["2026-06-01 22:14:22.550672 UTC", "2025-12-13 03:01:02.100000 UTC", None])
    fmt, status, _ = choose_date_format(s, None, 0.95)
    assert status == "detected"
    d, failed = to_date(s, fmt)
    assert list(d.dt.strftime("%Y-%m-%d")[:2]) == ["2026-06-01", "2025-12-13"] and not failed.any()


def test_whole_seconds_without_fraction():
    s = pd.Series(["2026-06-01 22:14:22.550672 UTC", "2025-08-13 18:33:26 UTC", "not a date"])
    d, failed = to_date(s, "%Y-%m-%d %H:%M:%S.%f UTC")
    assert list(d.dt.strftime("%Y-%m-%d")[:2]) == ["2026-06-01", "2025-08-13"]
    assert list(failed) == [False, False, True]


def test_date_format_judged_on_an_even_sample(monkeypatch):
    """Millions of distinct timestamps: the format is judged on a capped, evenly spaced sample, and day-first
    is still told from month-first when only some days are above 12."""
    from gtpfit.ingest import coerce
    monkeypatch.setattr(coerce, "DATE_DETECT_SAMPLE", 100)
    days = pd.date_range("2025-01-01", periods=5000, freq="37min")
    s = pd.Series(days.strftime("%d/%m/%Y %H:%M:%S"))
    assert len(coerce._detect_sample(s)) <= 100
    fmt, status, _ = choose_date_format(s, None, 0.95)
    assert (fmt, status) == ("%d/%m/%Y %H:%M:%S", "detected")
    assert not to_date(s, fmt)[1].any()


def test_twelve_hour_clock():
    s = pd.Series(["5/22/2026 5:35:18 AM", "12/13/2025 11:02:00 PM"])
    fmt, status, _ = choose_date_format(s, None, 0.95)
    assert status == "detected" and fmt == "%m/%d/%Y %I:%M:%S %p"
    d, _ = to_date(s, fmt)
    assert list(d.dt.strftime("%Y-%m-%d")) == ["2026-05-22", "2025-12-13"]
