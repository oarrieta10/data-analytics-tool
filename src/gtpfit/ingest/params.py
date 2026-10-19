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

    # Share of rows with zero / negative quantity (or unparseable keys) that
    # are set aside with a warning; above this the table is refused.
    # Source: consultant judgement.  Confidence: medium.
    max_bad_row_share: float = 0.01

    # A date format must parse at least this share of a column's values.
    min_date_parse_share: float = 0.99

    # Header matching: minimum similarity (difflib ratio) for a fuzzy match.
    fuzzy_header_cutoff: float = 0.86

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
