"""config.py — every knob the synthetic generator exposes.

A profile is a plain YAML file (see profiles/). Anything left out keeps the
default below, so a profile only needs to state what makes that client
different.  Units: inches, pounds, days.

Nested sections (`defects:`, `peak_shape:`) work the same way: list only the
keys you want to change.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
import datetime as dt

import yaml

# Order of the sections in the workbench form.
SECTIONS = ["Basics", "Catalogue", "E-commerce orders", "Store orders", "Calendar & peaks",
            "Peak basket shape", "Inventory", "Data problems"]


def knob(default, *, section, label, help, unit=None, lo=None, hi=None, step=None, widget=None, **extra):
    """A profile setting plus what the workbench needs to draw it.

    unit: "share" (0-1, shown as %), "x" (multiplier), "days", or None.
    widget: force "number" or "slider"; otherwise chosen from the range.
    extra: group (sub-heading within a section), pairs_with (peak value's base setting).
    """
    md = dict(section=section, label=label, help=help, unit=unit, lo=lo, hi=hi, step=step, widget=widget, **extra)
    if isinstance(default, (list, dict)):
        return field(default_factory=lambda d=default: copy.deepcopy(d), metadata=md)
    return field(default=default, metadata=md)


_DP = "Data problems"
_IM = dict(section=_DP, group="Item master", unit="share", lo=0.0)
_TX = dict(section=_DP, group="Transactions", unit="share", lo=0.0)


@dataclass
class DefectRates:
    """Share of SKUs (item master) or rows (transactions) given each defect.

    Rates are applied independently to disjoint SKU samples, so each planted
    SKU carries exactly one item-master defect and the ground truth stays
    unambiguous.
    """
    missing_dims: float = knob(0.06, **_IM, hi=0.5, label="Missing dimensions", help="L, W and H all blank.")
    missing_weight: float = knob(0.04, **_IM, hi=0.5, label="Missing weight", help="Unit weight blank.")
    missing_case_qty: float = knob(0.03, **_IM, hi=0.5, label="Missing case pack", help="Case quantity blank.")
    placeholder_001: float = knob(0.05, **_IM, hi=0.5, label="Placeholder 0.01 dims",
                                  help="L = W = H = 0.01, a common system default.")
    placeholder_111: float = knob(0.02, **_IM, hi=0.5, label="Placeholder 1x1x1 dims", help="L = W = H = 1.")
    metric_dims: float = knob(0.02, **_IM, hi=0.5, label="Centimetres in inch fields",
                              help="Dimensions typed in cm on single rows.")
    weight_in_oz: float = knob(0.02, **_IM, hi=0.5, label="Ounces in pound field",
                               help="Weight typed in oz on single rows.")
    decimal_shift: float = knob(0.01, **_IM, hi=0.5, label="Decimal shift", help="One dimension x10.")
    case_qty_default_1: float = knob(0.03, **_IM, hi=0.5, label="Case pack left at 1",
                                     help="Case quantity defaulted to 1.")
    case_smaller_than_unit: float = knob(0.01, **_IM, hi=0.5, label="Case smaller than unit",
                                         help="Case dims overwritten with unit dims / 2.")
    duplicate_sku_rows: float = knob(0.005, **_IM, hi=0.2, label="Duplicate SKU rows",
                                     help="A second, conflicting item-master row.")
    orphan_skus: float = knob(0.02, **_IM, hi=0.3, label="Orphan SKUs",
                              help="Ordered SKUs missing from the item master.")
    sku_format_variants: float = knob(0.005, **_TX, hi=0.2, label="SKU spelling variants",
                                      help="Lower case or stray spaces in the orders file.")
    zero_or_negative_qty: float = knob(0.002, **_TX, hi=0.1, label="Zero / negative quantity",
                                       help="Order rows with qty <= 0.")
    duplicate_order_rows: float = knob(0.001, **_TX, hi=0.1, label="Duplicate order rows",
                                       help="Exact repeated rows.")
    missing_pick_time: float = knob(0.005, **_TX, hi=0.2, label="Missing pick time",
                                    help="Order rows with a blank time.")


_PK = "Peak basket shape"


@dataclass
class PeakShape:
    """E-commerce basket shape on event days (promotions, Q4 ramp).

    Each value, when set, is what the matching Profile setting becomes on a day
    whose event lift (season x promo, not weekday or noise) equals
    `reference_lift`.  In between, the setting is interpolated linearly from its
    normal value; on bigger events it is extrapolated up to `max_t` times the
    reference step (default 1: none, because Black Friday's lift stacks promo on
    the Q4 ramp).  Days with lift <= 1 use the normal values.
    Unset (None, the default) = same as normal: no basket change on peak days.
    Source: a real omnichannel client showed ~1.02x e-com lines per order on its
    busiest days (2026-10 benchmark); other clients go either way.
    """
    reference_lift: float = knob(2.0, section=_PK, unit="x", lo=1.1, hi=4.0, step=0.1, label="Reference event lift",
                                 help="Event lift (season x promo) at which the peak values fully apply.")
    max_t: float = knob(1.0, section=_PK, lo=0.0, hi=3.0, step=0.1, label="Extrapolation cap",
                        help="1 = events bigger than the reference get the reference shape; "
                             ">1 lets them go further (Black Friday is ~3.5x).")
    ecom_single_line_share: float | None = knob(None, section=_PK, unit="share", lo=0.05, hi=0.95,
                                         pairs_with="ecom_single_line_share", label="Single-line orders",
                                         help="Share of e-com orders with one line on a reference event day.")
    ecom_multi_line_geom_p: float | None = knob(None, section=_PK, lo=0.1, hi=0.95, step=0.01,
                                         pairs_with="ecom_multi_line_geom_p", label="Multi-line tail (p)",
                                         help="Lower = longer multi-line orders on peak days.")
    ecom_single_unit_share: float | None = knob(None, section=_PK, unit="share", lo=0.05, hi=0.95,
                                         pairs_with="ecom_single_unit_share", label="Single-unit lines",
                                         help="Share of e-com lines with one unit on a reference event day.")
    ecom_extra_units_geom_p: float | None = knob(None, section=_PK, lo=0.1, hi=0.95, step=0.01,
                                          pairs_with="ecom_extra_units_geom_p", label="Extra-units tail (p)",
                                          help="Lower = more units on multi-unit lines on peak days.")


_B, _C, _E, _S, _K, _I = "Basics", "Catalogue", "E-commerce orders", "Store orders", "Calendar & peaks", "Inventory"


@dataclass
class Profile:
    name: str = knob("default", section=_B, label="Name", help="Label stored in the manifest.")
    seed: int = knob(20261015, section=_B, widget="number", label="Random seed",
                     help="Same profile + seed = byte-identical files. Change it for a different client "
                          "with the same shape.")
    start_date: str = knob("2025-01-01", section=_B, label="Start date", help="First calendar day.")
    days: int = knob(365, section=_B, unit="days", lo=30, hi=730, step=1, label="Days",
                     help="Length of the history.")

    # catalogue
    n_skus: int = knob(8000, section=_C, lo=100, hi=50000, step=100, widget="number", label="SKUs",
                       help="Size of the true catalogue.")
    share_inventory_only: float = knob(0.06, section=_C, unit="share", lo=0.0, hi=0.5, label="Dead stock",
                                       help="Stocked but never ordered.")
    share_new_in_year: float = knob(0.12, section=_C, unit="share", lo=0.0, hi=0.5, label="New in year",
                                    help="Introduced part-way through the year.")
    share_discontinued: float = knob(0.08, section=_C, unit="share", lo=0.0, hi=0.5, label="Discontinued",
                                     help="Stops selling part-way through.")
    popularity_sigma: float = knob(1.75, section=_C, lo=0.5, hi=3.0, step=0.05, label="Popularity skew",
                                   help="Lognormal spread of SKU popularity. Higher = fewer, bigger fast movers "
                                        "(top-20% share of lines rises).")

    # e-commerce channel
    ecom_orders_per_day: float = knob(1200.0, section=_E, lo=0.0, hi=20000.0, step=50.0, widget="number",
                                      label="Orders per day", help="Base e-com orders on an average day.")
    ecom_single_line_share: float = knob(0.55, section=_E, unit="share", lo=0.05, hi=0.95,
                                         label="Single-line orders", help="Share of orders with one line.")
    ecom_multi_line_geom_p: float = knob(0.5, section=_E, lo=0.1, hi=0.95, step=0.01, label="Multi-line tail (p)",
                                         help="Extra lines beyond 1 ~ geometric(p); mean extra = 1/p. "
                                              "Lower = longer multi-line orders.")
    ecom_single_unit_share: float = knob(0.82, section=_E, unit="share", lo=0.05, hi=0.95,
                                         label="Single-unit lines", help="Share of lines with one unit.")
    ecom_extra_units_geom_p: float = knob(0.6, section=_E, lo=0.1, hi=0.95, step=0.01, label="Extra-units tail (p)",
                                          help="Units beyond 1 ~ geometric(p). Lower = more units.")

    # store / B2B replenishment channel
    b2b_orders_per_day: float = knob(30.0, section=_S, lo=0.0, hi=2000.0, step=1.0, widget="number",
                                     label="Orders per weekday", help="Store orders; weekdays only.")
    b2b_lines_mean: float = knob(22.0, section=_S, lo=3.0, hi=200.0, step=1.0, label="Lines per order (mean)",
                                 help="Store orders have at least 3 lines.")
    b2b_full_case_share: float = knob(0.5, section=_S, unit="share", lo=0.0, hi=1.0, label="Full-case lines",
                                      help="Share of store lines shipped in whole cases.")
    b2b_full_case_multiple_geom_p: float = knob(0.6, section=_S, lo=0.1, hi=0.95, step=0.01,
                                                label="Cases per full-case line (p)",
                                                help="Cases per full-case line ~ geometric(p). Lower = more cases.")
    b2b_volume_cap: float = knob(1.3, section=_S, unit="x", lo=1.0, hi=3.0, step=0.05, label="Peak cap",
                                 help="Store volume follows the calendar only up to this multiple; stores don't "
                                      "follow e-com promotions.")
    b2b_popularity_flatten: float = knob(0.7, section=_S, lo=0.2, hi=1.0, step=0.05, label="Spread to slow movers",
                                         help="Store demand uses popularity^x; below 1 spreads lines to slow movers.")

    # calendar
    weekday_factors: list = knob([1.15, 1.10, 1.05, 1.00, 0.95, 0.70, 0.50], section=_K, unit="x", lo=0.0, hi=2.0,
                                 step=0.05, label="Weekday factors", help="Volume multiplier Monday to Sunday.")
    closed_dates: list = knob(["01-01", "12-25"], section=_K, label="Closed dates",
                              help="MM-DD dates with no picking every year.")
    q4_ramp: float = knob(0.35, section=_K, unit="share", lo=0.0, hi=1.5, label="Q4 ramp",
                          help="December volume uplift over base, ramping from October.")
    promo_days: dict = knob({"black_friday": 2.6, "cyber_monday": 2.3, "mid_july_sale": 1.8}, section=_K,
                            unit="x", lo=1.0, hi=4.0, step=0.1, label="Promotion days",
                            help="Volume multiplier on each event; 1 = no event.")
    daily_noise_cv: float = knob(0.08, section=_K, lo=0.0, hi=0.5, step=0.01, label="Daily noise (CV)",
                                 help="Random day-to-day variation.")

    # inventory policy
    cover_days: dict = knob({"A": 14, "B": 30, "C": 45, "D": 60}, section=_I, unit="days", lo=1, hi=180, step=1,
                            label="Days of cover by velocity class", help="Order-up-to cover per ABC(D) class.")
    lead_time_days: list = knob([5, 21], section=_I, unit="days", lo=1, hi=90, step=1, label="Supplier lead time",
                                help="Range of supplier lead times.")
    safety_factor: float = knob(1.3, section=_I, lo=0.0, hi=4.0, step=0.1, label="Safety factor",
                                help="Safety stock multiplier on lead-time demand.")
    n_suppliers: int = knob(60, section=_I, lo=1, hi=1000, step=1, widget="number", label="Suppliers",
                            help="Number of suppliers (each has its own lead time).")

    defects: DefectRates = field(default_factory=DefectRates)
    peak_shape: PeakShape = field(default_factory=PeakShape)
    truncate_orders_at_excel_limit: bool = knob(False, section=_DP, group="Export",
                                                label="Clip orders at Excel's row limit",
                                                help="Simulate an export cut off at 1,048,575 rows.")

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


def profile_diff(p: Profile) -> dict:
    """Only the settings that differ from the defaults, sections nested (what a saved profile holds)."""
    base = Profile()
    out = {}
    for f in fields(Profile):
        v, b = getattr(p, f.name), getattr(base, f.name)
        if f.name in _SECTIONS:
            sub = {g.name: getattr(v, g.name) for g in fields(v) if getattr(v, g.name) != getattr(b, g.name)}
            if sub:
                out[f.name] = sub
        elif v != b:
            out[f.name] = v
    return out


def profile_yaml(p: Profile) -> str:
    return yaml.safe_dump(profile_diff(p), sort_keys=False, default_flow_style=None)
