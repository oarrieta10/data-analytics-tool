"""coverage.py — what period the order history covers, and whether that is enough to design from.

    coverage(orders, params) -> Coverage
    sparkline_svg(cov, width, height) -> inline SVG of lines per day, gap days shaded
    busiest_months(dates, peak_months, ratio) -> PeakHint: the months that stand out in the data

A design year needs about 12 months (params.min_coverage_days) and the
client's peak months (params.peak_months).  Lines here are file rows; the
order-line definition (gtpfit.lines) is applied later by the profile module.
Works on the canonical orders table (order_date, order_id, sku, qty).

The busiest-months hint is only a hint: it never sets peak_months and the
coverage check does not use it.  Each year-month's rows are divided by the
calendar days of that month the history covers (so a partial first or last
month, or February, does not look quiet), each calendar month is averaged
across years, and a month is named when it is at least `ratio` times the
median of the twelve.  It needs all twelve calendar months.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from html import escape

import pandas as pd

from .checks import fmt_day, missing_day_runs
from .params import IngestParams


@dataclass
class Coverage:
    first: pd.Timestamp | None
    last: pd.Timestamp | None
    span_days: int
    days_with_rows: int
    lines: int
    orders: int
    units: float
    skus: int
    daily: pd.DataFrame                              # date, lines, orders (every calendar day in the span)
    gap_runs: list = field(default_factory=list)     # (first day, last day, days) with no rows
    peak_months: tuple = ()
    peak_covered: bool = True
    short: bool = False
    warnings: list = field(default_factory=list)


def coverage(orders: pd.DataFrame, params: IngestParams | None = None) -> Coverage:
    params = params or IngestParams()
    d = pd.to_datetime(orders["order_date"]).dt.normalize()
    if d.notna().sum() == 0:
        empty = pd.DataFrame(columns=["date", "lines", "orders"])
        return Coverage(None, None, 0, 0, 0, 0, 0.0, 0, empty, short=True, peak_covered=False,
                        peak_months=tuple(params.peak_months), warnings=["No dated order lines."])
    first, last = d.min(), d.max()
    span = (last - first).days + 1
    per = pd.DataFrame({"date": d, "order_id": orders["order_id"]}).groupby("date").agg(
        lines=("order_id", "size"), orders=("order_id", "nunique"))
    daily = per.reindex(pd.date_range(first, last), fill_value=0).rename_axis("date").reset_index()
    months_present = set(d.dt.month.unique())
    peak = tuple(params.peak_months)
    peak_ok = all(m in months_present for m in peak)
    short = span < params.min_coverage_days
    warnings = []
    if short:
        warnings.append(f"Orders cover {span} days ({fmt_day(first)} to {fmt_day(last)}); a design year needs "
                        f"about 12 months ({params.min_coverage_days}+ days).")
    if not peak_ok:
        missing = [calendar.month_abbr[m] for m in peak if m not in months_present]
        warnings.append(f"No orders in {', '.join(missing)}: peak season "
                        f"({'-'.join(calendar.month_abbr[m] for m in peak)}) is not covered.")
    qty = pd.to_numeric(orders["qty"], errors="coerce") if "qty" in orders else pd.Series(dtype=float)
    return Coverage(first, last, span, int(d.nunique()), int(d.notna().sum()), int(orders["order_id"].nunique()),
                    float(qty.sum()), int(orders["sku"].nunique()) if "sku" in orders else 0, daily,
                    missing_day_runs(d), peak, peak_ok, short, warnings)


def sparkline_svg(cov: Coverage, width: int = 640, height: int = 64, long_gap_days: int = 4) -> str:
    """Lines per day as a filled sparkline; missing days shaded (darker when a run is longer than long_gap_days).

    Self-contained SVG (no scripts, no external styles); colours follow the page's text colour.
    """
    if cov.first is None or not len(cov.daily):
        return ""
    n = len(cov.daily)
    top = float(cov.daily["lines"].max()) or 1.0
    x = lambda i: i * width / max(n - 1, 1)
    y = lambda v: height - 2 - (height - 6) * v / top
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(cov.daily["lines"]))
    shades = []
    for a, b, k in cov.gap_runs:
        i0, i1 = (a - cov.first).days, (b - cov.first).days
        op = 0.35 if k > long_gap_days else 0.12
        shades.append(f'<rect x="{x(i0) - 0.5:.1f}" y="0" width="{max(x(i1) - x(i0) + 1, 1.5):.1f}" '
                      f'height="{height}" fill="currentColor" opacity="{op}"><title>{k} day(s) with no orders: '
                      f'{fmt_day(a)} to {fmt_day(b)}</title></rect>')
    label = escape(f"Order lines per day, {fmt_day(cov.first)} to {fmt_day(cov.last)}")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" '
            f'height="{height}" role="img" aria-label="{label}" preserveAspectRatio="none">'
            f'{"".join(shades)}'
            f'<polyline points="0,{height} {pts} {width},{height}" fill="currentColor" opacity="0.18" stroke="none"/>'
            f'<polyline points="{pts}" fill="none" stroke="currentColor" stroke-width="1.2" opacity="0.9"/></svg>')


OUTSIDE_NOTE = "Busiest month is outside the peak months you set. Worth asking the client."


@dataclass
class PeakHint:
    enough: bool                                  # all 12 calendar months have orders
    months: tuple = ()                            # hinted months, calendar order
    ratios: dict = field(default_factory=dict)    # month -> lines per day / median month
    monthly: pd.Series | None = None              # month 1-12 -> mean lines per covered day
    outside: bool = False                         # months hinted and none of them in peak_months

    @property
    def text(self) -> str:
        if not self.enough:
            return "Busiest months in this data: not enough history to suggest (needs all 12 months)."
        if not self.months:
            return "Busiest months in this data: no month stands out."
        return "Busiest months in this data: " + ", ".join(
            f"{calendar.month_abbr[m]} ({self.ratios[m]:.1f}×)" for m in self.months) + "."


def busiest_months(dates, peak_months=(), ratio: float | None = None) -> PeakHint:
    """Months whose order lines per day are at least `ratio` x the median month (see the module docstring)."""
    ratio = IngestParams().peak_hint_ratio if ratio is None else ratio
    d = pd.to_datetime(pd.Series(dates), errors="coerce").dropna().dt.normalize()
    if d.empty or d.dt.month.nunique() < 12:
        return PeakHint(False)
    first, last = d.min(), d.max()
    ym = d.dt.to_period("M")
    rows = ym.value_counts()
    days = pd.Series(pd.date_range(first, last)).dt.to_period("M").value_counts()  # covered calendar days
    rate = (rows / days.reindex(rows.index)).rename("rate")
    monthly = rate.groupby(rate.index.month).mean().sort_index()
    median = float(monthly.median())
    ratios = {int(m): float(v / median) for m, v in monthly.items()}
    months = tuple(m for m in sorted(ratios) if ratios[m] >= ratio)
    outside = bool(months) and not set(months) & set(peak_months)
    return PeakHint(True, months, ratios, monthly, outside)
