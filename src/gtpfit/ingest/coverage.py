"""coverage.py — what period the order history covers, and whether that is enough to design from.

    coverage(orders, params) -> Coverage
    sparkline_svg(cov, width, height) -> inline SVG of lines per day, gap days shaded

A design year needs about 12 months (params.min_coverage_days) and the
client's peak months (params.peak_months).  Lines here are file rows; the
order-line definition (gtpfit.lines) is applied later by the profile module.
Works on the canonical orders table (order_date, order_id, sku, qty).
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
