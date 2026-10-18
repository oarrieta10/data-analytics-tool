"""config.py — every knob the synthetic generator exposes.

A profile is a plain YAML file (see profiles/). Anything left out keeps the
default below, so a profile only needs to state what makes that client
different.  Units: inches, pounds, days.
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

    # store / B2B replenishment channel
    b2b_orders_per_day: float = 30.0     # weekdays only
    b2b_lines_mean: float = 22.0
    b2b_full_case_share: float = 0.5
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
    truncate_orders_at_excel_limit: bool = False  # simulate a clipped export

    @property
    def start(self) -> dt.date:
        return dt.date.fromisoformat(self.start_date)

    def to_dict(self) -> dict:
        return asdict(self)


def load_profile(path: str | Path | None = None, **overrides) -> Profile:
    data: dict = {}
    if path:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    data.update(overrides)
    defect_data = data.pop("defects", {}) or {}
    known = {f.name for f in fields(Profile)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown profile keys: {sorted(unknown)}")
    known_def = {f.name for f in fields(DefectRates)}
    bad = set(defect_data) - known_def
    if bad:
        raise ValueError(f"Unknown defect keys: {sorted(bad)}")
    return Profile(**data, defects=DefectRates(**defect_data))
