"""params.py — every threshold the ingest step uses, with its source.

Ingest refuses data it cannot trust instead of quietly fixing it; these
numbers decide where "warn" becomes "block".  Override any of them by passing
IngestParams(...) to ingest_folder / ingest_table.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IngestParams:
    # Row counts that mean an export was clipped by a spreadsheet: data rows
    # under one header row.  Source: Excel 2007+ (1,048,576) and .xls (65,536)
    # sheet limits.  Confidence: high.
    excel_row_limits: tuple = (1_048_575, 1_048_576, 65_535, 65_536)

    # Longest run of missing calendar days tolerated inside a table's date
    # range before it counts as a gap in the extract (closures are shorter).
    # Source: consultant judgement (long weekends, holiday shutdowns); a real
    # client's Christmas closure ran Fri-Mon (4 days), so orders allow 4.
    # Confidence: medium.
    max_gap_days: dict = field(default_factory=lambda: {"orders": 4, "receipts": 6})
    # Snapshots: a spacing above this multiple of the median spacing is a gap.
    snapshot_gap_factor: float = 1.5

    # Order history long enough to design from: 12 months, less a week for a
    # year-end closure or a 52-week extract (364 days).  Shorter -> warn.
    # Source: consultant judgement (a design year must include every season).
    # Confidence: medium.
    min_coverage_days: int = 358
    # Months holding the client's peak season; orders must have rows in each.
    # Source: US retail / e-commerce peak (Black Friday to Christmas); change
    # per client (e.g. back-to-school, spring garden).  Confidence: medium.
    peak_months: tuple = (11, 12)

    # Share of rows with zero / negative quantity (or unparseable keys) that
    # are set aside with a warning; above this the table is refused.
    # Source: consultant judgement.  Confidence: medium.
    max_bad_row_share: float = 0.01

    # A date format must parse at least this share of a column's values.
    min_date_parse_share: float = 0.99

    # Header matching: minimum similarity (difflib ratio) for a fuzzy match.
    fuzzy_header_cutoff: float = 0.86
    # Reuse a client's last confirmed column mapping when a new file's column
    # names overlap the old file's by at least this share (Jaccard).  A resent
    # or refreshed extract keeps nearly all columns; a different export does not.
    # Source: consultant judgement.  Confidence: medium.
    mapping_reuse_min_overlap: float = 0.8

    # Unit sanity bands for an assortment's medians, in inches / lb / ft3.
    # Source: consultant judgement for mixed consumer goods; dense categories
    # (hardware, liquids) sit higher, light ones lower: a real apparel/footwear
    # client's picked SKUs had a median of ~6 lb/ft3 (2026-10), so the floor is 2.
    # Confidence: low — used to check declared units and to choose only when
    # exactly one unit pair fits.
    unit_longest_side_in: tuple = (4.0, 36.0)
    case_longest_side_in: tuple = (8.0, 48.0)
    density_lb_ft3: tuple = (2.0, 40.0)

    # SKU clean-up: strip spaces always; upper-case unless the client's SKUs
    # are case-sensitive.
    upper_case_skus: bool = True


# Assortment presets: the three unit sanity bands above, set together from the
# kind of goods the client stores (the workbench offers these, not the bands).
# Source: consultant judgement.  Confidence: low; see each note.
ASSORTMENTS = {
    # Mixed consumer goods / e-commerce / apparel: the IngestParams defaults.
    "consumer goods": dict(unit_longest_side_in=(4.0, 36.0), case_longest_side_in=(8.0, 48.0),
                           density_lb_ft3=(2.0, 40.0)),
    # MRO / hardware / spare parts: small dense parts to long items; bagged
    # fasteners and machined parts run far denser than consumer goods.
    "industrial": dict(unit_longest_side_in=(1.0, 48.0), case_longest_side_in=(4.0, 60.0),
                       density_lb_ft3=(4.0, 150.0)),
    # Grocery / beverage: compact units; liquids near water (62 lb/ft3) in the
    # case, snacks and paper goods light.
    "grocery": dict(unit_longest_side_in=(2.0, 20.0), case_longest_side_in=(6.0, 30.0),
                    density_lb_ft3=(3.0, 70.0)),
}
DEFAULT_ASSORTMENT = "consumer goods"


def assortment_bands(name: str) -> dict:
    """The three unit sanity bands for an assortment type (ValueError for an unknown one)."""
    if name not in ASSORTMENTS:
        raise ValueError(f"Unknown assortment {name!r}; choose from {sorted(ASSORTMENTS)}")
    return dict(ASSORTMENTS[name])
