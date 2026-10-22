"""params.py — every threshold the ingest step uses, with its source, label and tooltip.

Ingest refuses data it cannot trust instead of quietly fixing it; these
numbers decide where "warn" becomes "block".  Override any of them by passing
IngestParams(...) to ingest_folder / ingest_table (the workbench keeps a
client's overrides in its client.yaml).

Each field is declared with setting(): the default plus what the workbench
needs to draw it (block, label, unit, range) and to explain it (what it does,
why the default, what raising / lowering it does), with a source and a
confidence.  The page builds its controls and tooltips from this metadata, so
the wording lives next to the value.  It does not decide anything itself.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field, fields

BLOCKS = ("client", "detection")  # Client settings (always shown) · Detection settings (collapsed)
UNITS = ("days", "count", "share", "x", "ratio", "months", "bool", "rows", "words", "in", "lb/ft3")


def setting(default, *, block, label, what, why, effect, unit, source, confidence, lo=None, hi=None, step=None,
            **extra):
    """A threshold plus its workbench metadata.

    unit: one of UNITS ("share" is 0-1, shown as %; "rows" a list of row counts; "months" month numbers;
    "count" a whole number; "words" a list of words).
    extra: via="assortment" (set by the assortment preset, not drawn alone); widget="toggle" with
    invert=True (shown as the opposite statement, e.g. "SKUs are case-sensitive").
    """
    md = dict(block=block, label=label, what=what, why=why, effect=effect, unit=unit, source=source,
              confidence=confidence, lo=lo, hi=hi, step=step, **extra)
    return field(default=default, metadata=md)


_CONSULTANT = "Consultant judgement"


@dataclass
class IngestParams:
    # ---- client settings ------------------------------------------------------------------------
    peak_months: tuple = setting(
        (11, 12), block="client", unit="months", label="Peak months",
        what="Months holding the client's peak season; the order history must include each of them.",
        why="the November-December retail and e-commerce peak",
        effect="Change it for a client whose peak falls elsewhere (back-to-school, spring garden). "
               "Adding months makes the coverage check stricter; removing them relaxes it.",
        source="US retail / e-commerce peak (Black Friday to Christmas)", confidence="medium")
    max_gap_days_orders: int = setting(
        4, block="client", unit="days", lo=0, hi=60, step=1, label="Longest tolerated gap in orders (days)",
        what="Longest run of missing calendar days allowed inside the order history before it counts as a gap "
             "in the extract.",
        why="long weekends and holiday shutdowns are usually shorter",
        effect="Raise it for a site that closes for long periods; lower it to catch smaller holes.",
        source=_CONSULTANT + " (long weekends, holiday shutdowns)", confidence="medium")
    max_bad_row_share: float = setting(
        0.01, block="client", unit="share", lo=0.0, hi=0.5, step=0.005,
        label="Share of unusable rows before a table is refused",
        what="Rows with a zero or negative quantity or an unreadable key are set aside with a warning; above "
             "this share of the table, the table is refused.",
        why="a healthy extract has very few such rows, so more than one in a hundred points to a broken export",
        effect="Raise it to accept a messier extract (more rows set aside, volumes less certain); lower it to "
               "refuse sooner.",
        source=_CONSULTANT, confidence="medium")
    upper_case_skus: bool = setting(
        True, block="client", unit="bool", widget="toggle", invert=True, label="SKUs are case-sensitive",
        what="Whether SKUs that differ only in upper / lower case are the same item. Spaces are always trimmed.",
        why="most systems treat “ab-1” and “AB-1” as one item, and mixed case is "
            "usually a typing variant",
        effect="Turn it on when the client's system really has separate items that differ only in case; "
               "leave it off to merge the variants.",
        source=_CONSULTANT, confidence="high")
    # Unit sanity bands, in inches / lb / ft3: set together by the assortment preset (ASSORTMENTS).
    unit_longest_side_in: tuple = setting(
        (4.0, 36.0), block="client", unit="in", via="assortment", label="Unit longest side (in)",
        what="Range the median longest side of the units should fall in once converted to inches.",
        why="mixed consumer goods",
        effect="A wider range accepts more unit readings as plausible; a narrower one flags more.",
        source=_CONSULTANT + " for mixed consumer goods", confidence="low")
    case_longest_side_in: tuple = setting(
        (8.0, 48.0), block="client", unit="in", via="assortment", label="Case longest side (in)",
        what="Range the median longest side of the cases should fall in once converted to inches.",
        why="mixed consumer goods",
        effect="A wider range accepts more unit readings as plausible; a narrower one flags more.",
        source=_CONSULTANT + " for mixed consumer goods", confidence="low")
    density_lb_ft3: tuple = setting(
        (2.0, 40.0), block="client", unit="lb/ft3", via="assortment", label="Density (lb/ft³)",
        what="Range the median item density (weight over cube) should fall in.",
        why="mixed consumer goods; light goods such as apparel sit near the floor, dense ones above",
        effect="A wider range accepts more unit readings as plausible; a narrower one flags more.",
        source=_CONSULTANT + " for mixed consumer goods; apparel and footwear assortments run light",
        confidence="low")

    # ---- detection settings ------------------------------------------------------------------------
    excel_row_limits: tuple = setting(
        (1_048_575, 1_048_576, 65_535, 65_536), block="detection", unit="rows", label="Excel row limits",
        what="Row counts that mean an export was clipped by a spreadsheet's sheet limit.",
        why="Excel 2007+ sheets stop at 1,048,576 rows and .xls sheets at 65,536 (data rows under one header)",
        effect="Add a count if the client's system clips exports at another limit; removing one stops that "
               "limit being flagged.",
        source="Excel 2007+ and .xls sheet limits", confidence="high")
    max_gap_days_receipts: int = setting(
        6, block="detection", unit="days", lo=0, hi=60, step=1, label="Longest tolerated gap in receipts (days)",
        what="Longest run of missing calendar days allowed inside the receipt history before it counts as a gap "
             "in the extract.",
        why="inbound is often lumpier than orders, with no deliveries over weekends and shutdowns",
        effect="Raise it for a site with infrequent deliveries; lower it to catch smaller holes.",
        source=_CONSULTANT, confidence="medium")
    snapshot_gap_factor: float = setting(
        1.5, block="detection", unit="x", lo=1.0, hi=10.0, step=0.1, label="Snapshot gap factor",
        what="A spacing between inventory snapshots above this multiple of the usual spacing counts as a gap.",
        why="weekly snapshots may slip a day or two, but a missed week is a gap",
        effect="Raise it to tolerate irregular snapshots; lower it to flag smaller slips.",
        source=_CONSULTANT, confidence="medium")
    min_coverage_days: int = setting(
        358, block="detection", unit="days", lo=0, hi=1100, step=1, label="Minimum history (days)",
        what="Order history shorter than this gets a warning: a design year needs every season.",
        why="12 months less a week, allowing for a year-end closure or a 52-week extract",
        effect="Raise it to ask for more history; lowering it silences the warning for short extracts, which "
               "still miss seasons.",
        source=_CONSULTANT + " (a design year must include every season)", confidence="medium")
    min_date_parse_share: float = setting(
        0.99, block="detection", unit="share", lo=0.5, hi=1.0, step=0.005, label="Date format must read",
        what="A date format is chosen only if it reads at least this share of a column's values.",
        why="a correct format reads nearly every value; the rest are blanks or typos",
        effect="Raise it to insist on cleaner dates; lower it to accept a format that misreads more values.",
        source=_CONSULTANT, confidence="medium")
    fuzzy_header_cutoff: float = setting(
        0.86, block="detection", unit="ratio", lo=0.5, hi=1.0, step=0.01, label="Column-name match strictness",
        what="How alike a client column name must be to a known name for automatic matching to use it "
             "(1 = identical).",
        why="catches spelling variants such as “Qty Ordered” / “Qty_Ordrd” without "
            "matching unrelated columns",
        effect="Raise it for fewer, safer automatic matches; lower it to match looser names (more wrong guesses "
               "to correct).",
        source=_CONSULTANT, confidence="medium")
    mapping_reuse_min_overlap: float = setting(
        0.8, block="detection", unit="share", lo=0.0, hi=1.0, step=0.05, label="Reuse last mapping when columns overlap",
        what="A new file reuses the client's last confirmed column mapping when its column names overlap the "
             "earlier file's by at least this share.",
        why="a resent or refreshed extract keeps nearly all its columns; a different export does not",
        effect="Raise it to reuse only for near-identical files; lower it to reuse for files that changed more.",
        source=_CONSULTANT, confidence="medium")
    peak_hint_ratio: float = setting(
        1.3, block="detection", unit="x", lo=1.0, hi=5.0, step=0.05, label="Busiest-month hint threshold",
        what="A month is named in the busiest-months hint when its order lines per day are at least this "
             "multiple of the median month.",
        why="a peak worth designing for stands well clear of an ordinary month",
        effect="Raise it to name only the sharpest peaks; lower it to name more months.",
        source=_CONSULTANT, confidence="low")
    extra_max_distinct: int = setting(
        30, block="detection", unit="count", lo=0, hi=500, step=1, label="Extra order columns kept up to",
        what="Order columns that match no field are kept (named x_...) when they hold at most this many "
             "different values, so the Scope step can filter on them (e.g. a pick area or carrier).",
        why="enough for sites, channels, statuses and carriers, while order numbers, customers and free "
            "text are left out",
        effect="Raise it to keep columns with more values (larger outputs); lower it, or set 0, to keep fewer.",
        source=_CONSULTANT, confidence="medium")

    def gap_limit(self, table: str) -> int | None:
        """Longest tolerated run of missing days for a dated table (None: not checked)."""
        return {"orders": self.max_gap_days_orders, "receipts": self.max_gap_days_receipts}.get(table)


def meta(name: str, cls=None) -> dict:
    """A field's setting() metadata; cls is the parameter dataclass (IngestParams by default)."""
    return dict(next(f for f in fields(cls or IngestParams) if f.name == name).metadata)


def block_fields(block: str, cls=None) -> list[str]:
    return [f.name for f in fields(cls or IngestParams) if f.metadata.get("block") == block]


def format_value(value, unit: str) -> str:
    """A value as the page shows it: 1% not 0.01, Nov, Dec not (11, 12)."""
    if unit == "share":
        return f"{value * 100:g}%"
    if unit == "months":
        return ", ".join(calendar.month_abbr[m] for m in value) or "none"
    if unit == "bool":
        return "on" if value else "off"
    if unit == "rows":
        return ", ".join(f"{v:,}" for v in value)
    if unit == "words":
        return ", ".join(f"“{v}”" for v in value) or "none"
    if unit in ("in", "lb/ft3"):
        shown = "lb/ft³" if unit == "lb/ft3" else unit
        return f"{value[0]:g}–{value[1]:g} {shown}"
    if unit == "days":
        return f"{value:g} days"
    if unit == "x":
        return f"{value:g}×"
    return f"{value:g}"


def tooltip(name: str, cls=None) -> str:
    """What it does. Default (from the actual default): why. What raising / lowering it does."""
    m = meta(name, cls)
    d = next(f for f in fields(cls or IngestParams) if f.name == name).default
    if m.get("invert"):
        d = not d
    return f"{m['what']}\n\nDefault {format_value(d, m['unit'])}: {m['why']}.\n\n{m['effect']}"


# Assortment presets: the three unit sanity bands, set together from the kind
# of goods the client stores (the workbench offers these, not the bands).
# Source: consultant judgement.  Confidence: low.
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
ASSORTMENT_HELP = dict(
    what="The kind of goods the client stores. It sets the size and density ranges used to check that the "
         "declared or detected units are plausible.",
    why="mixed consumer goods, the most common assortment",
    effect="Industrial widens the ranges toward small dense parts and long items; grocery narrows sizes and "
           "allows liquids' weight. A wrong choice flags good units or lets wrong ones through.")


def assortment_tooltip() -> str:
    h = ASSORTMENT_HELP
    return f"{h['what']}\n\nDefault {DEFAULT_ASSORTMENT}: {h['why']}.\n\n{h['effect']}"


def assortment_bands(name: str) -> dict:
    """The three unit sanity bands for an assortment type (ValueError for an unknown one)."""
    if name not in ASSORTMENTS:
        raise ValueError(f"Unknown assortment {name!r}; choose from {sorted(ASSORTMENTS)}")
    return dict(ASSORTMENTS[name])
