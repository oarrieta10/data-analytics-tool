"""config.py — every knob the synthetic generator exposes.

A profile is a plain YAML file (see profiles/). Anything left out keeps the
default below, so a profile only needs to state what makes that client
different.  Units: inches, pounds, days.

Nested sections (`defects:`, `peak_shape:`) work the same way: list only the
keys you want to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
import datetime as dt

import yaml


@dataclass
class DefectRates:
    """Share of SKUs (item master) or rows (transactions) given each defect.

    Rates are applied independently to disjoint SKU samples, so each planted
    SKU carries exactly one item-master defect and the ground truth stays
    unambiguous.
    """
    missing_dims: float = 0.06          # L, W, H all blank
    missing_weight: float = 0.04
    missing_case_qty: float = 0.03
    placeholder_001: float = 0.05       # L = W = H = 0.01 (system default)
    placeholder_111: float = 0.02       # L = W = H = 1
    metric_dims: float = 0.02           # centimetres typed into inch fields
    weight_in_oz: float = 0.02          # ounces typed into a pound field
    decimal_shift: float = 0.01         # one dimension x10
    case_qty_default_1: float = 0.03    # case pack left at 1
    case_smaller_than_unit: float = 0.01  # case dims overwritten with unit dims / 2
    duplicate_sku_rows: float = 0.005   # second, conflicting item-master row
    orphan_skus: float = 0.02           # ordered SKUs missing from the item master
    sku_format_variants: float = 0.005  # lower case / trailing spaces in orders
    # transaction rows
    zero_or_negative_qty: float = 0.002
    duplicate_order_rows: float = 0.001
    missing_pick_time: float = 0.005


@dataclass
class PeakShape:
    """E-commerce basket shape on event days (promotions, Q4 ramp).

    Each value is what the matching Profile setting becomes on a day whose
    event lift (season x promo, not weekday or noise) equals `reference_lift`.
    In between, the setting is interpolated linearly from its base value; on
    bigger events it is extrapolated up to `max_t` times the reference step
    (default 1: none, because Black Friday's lift stacks promo on the Q4 ramp).
    Days with lift <= 1 use the base values.  Set a value equal to its base to
    switch that effect off; move it the other way for smaller peak baskets.
    Defaults lean to bigger baskets on peak days (consultant judgement, low
    confidence; varies by client).
    """
    reference_lift: float = 2.0
    max_t: float = 1.0                     # 1 = events above reference_lift get the reference shape
    ecom_single_line_share: float = 0.45   # base 0.55: fewer single-line orders
    ecom_multi_line_geom_p: float = 0.42   # base 0.50: longer multi-line orders
    ecom_single_unit_share: float = 0.75   # base 0.82: more multi-unit lines
    ecom_extra_units_geom_p: float = 0.50  # base 0.60: more units when > 1


@dataclass
class Profile:
    name: str = "default"
    seed: int = 20261015
    start_date: str = "2025-01-01"
    days: int = 365

    # catalogue
    n_skus: int = 8000
    share_inventory_only: float = 0.06   # stocked but never ordered (dead stock)
    share_new_in_year: float = 0.12      # introduced part-way through the year
    share_discontinued: float = 0.08     # stops selling part-way through
    popularity_sigma: float = 1.75       # lognormal spread of SKU popularity

    # e-commerce channel
    ecom_orders_per_day: float = 1200.0
    ecom_single_line_share: float = 0.55
    ecom_multi_line_geom_p: float = 0.5  # extra lines beyond 1 ~ geometric
    ecom_single_unit_share: float = 0.82
    ecom_extra_units_geom_p: float = 0.6   # units beyond 1 ~ geometric

    # store / B2B replenishment channel
    b2b_orders_per_day: float = 30.0     # weekdays only
    b2b_lines_mean: float = 22.0
    b2b_full_case_share: float = 0.5
    b2b_full_case_multiple_geom_p: float = 0.6  # cases per full-case line ~ geometric
    b2b_volume_cap: float = 1.3          # store orders don't follow e-com promos past this
    b2b_popularity_flatten: float = 0.7  # p^x, <1 spreads demand to slow movers

    # calendar
    weekday_factors: list = field(default_factory=lambda: [1.15, 1.10, 1.05, 1.00, 0.95, 0.70, 0.50])
    closed_dates: list = field(default_factory=lambda: ["01-01", "12-25"])
    q4_ramp: float = 0.35                # December volume uplift vs base
    promo_days: dict = field(default_factory=lambda: {
        "black_friday": 2.6, "cyber_monday": 2.3, "mid_july_sale": 1.8})
    daily_noise_cv: float = 0.08

    # inventory policy
    cover_days: dict = field(default_factory=lambda: {"A": 14, "B": 30, "C": 45, "D": 60})
    lead_time_days: list = field(default_factory=lambda: [5, 21])
    safety_factor: float = 1.3
    n_suppliers: int = 60

    defects: DefectRates = field(default_factory=DefectRates)
    peak_shape: PeakShape = field(default_factory=PeakShape)
    truncate_orders_at_excel_limit: bool = False  # simulate a clipped export

    @property
    def start(self) -> dt.date:
        return dt.date.fromisoformat(self.start_date)

    def to_dict(self) -> dict:
        return asdict(self)


_SECTIONS = {"defects": DefectRates, "peak_shape": PeakShape}


def load_profile(path: str | Path | None = None, **overrides) -> Profile:
    data: dict = {}
    if path:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    data.update(overrides)
    sections = {}
    for key, cls in _SECTIONS.items():
        sub = data.pop(key, {}) or {}
        bad = set(sub) - {f.name for f in fields(cls)}
        if bad:
            raise ValueError(f"Unknown {key} keys: {sorted(bad)}")
        sections[key] = cls(**sub)
    known = {f.name for f in fields(Profile)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown profile keys: {sorted(unknown)}")
    return Profile(**data, **sections)
