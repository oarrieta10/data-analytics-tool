"""params.py — every threshold quality/ uses, with its label, three-part tooltip, source and confidence
(declared with gtpfit.ingest.params.setting, so the workbench draws and explains them the same way).

The assortment's size and density bands are not here: they come from the client's assortment type
(gtpfit.ingest.params.ASSORTMENTS), the same bands ingest checks units against.
"""
from __future__ import annotations

from dataclasses import dataclass

from gtpfit.ingest.params import setting

_CONSULTANT = "Consultant judgement"


@dataclass
class QualityParams:
    # ---- client setting ------------------------------------------------------------------------------
    outlier_mad_k: float = setting(
        3.5, block="client", unit="x", lo=2.0, hi=10.0, step=0.5, label="Outlier strictness",
        what="A SKU's cube, weight or density is an outlier when it sits more than this many robust standard "
             "deviations (median and MAD, in log space) from its peers.",
        why="a common robust cut-off: ordinary items rarely sit that far from their peers",
        effect="Raise it to flag only the most extreme items; lower it to flag more (more to check, more false "
               "alarms).",
        source="Iglewicz & Hoaglin's modified z-score (3.5), applied in log space", confidence="medium")

    # ---- detection settings ------------------------------------------------------------------------------
    peer_min_size: int = setting(
        20, block="detection", unit="count", lo=3, hi=500, step=1, label="Smallest peer group",
        what="SKUs are compared with their sub-category, else their category, else the whole assortment, "
             "whichever first has at least this many SKUs.",
        why="enough items for a stable median and spread",
        effect="Raise it to compare with broader groups (steadier, less specific); lower it to compare with "
               "narrower ones (more specific, noisier).",
        source=_CONSULTANT, confidence="medium")
    placeholder_values: tuple = setting(
        (0.01, 1.0, 99.0, 999.0, 9999.0), block="detection", unit="numbers", label="Placeholder sizes",
        what="An item whose length, width and height are all equal to one of these values was typed as a "
             "placeholder, not measured.",
        why="the fill-in values systems and people use when a size is unknown",
        effect="Add a value a client's system uses as a default; remove one if real items have that size.",
        source=_CONSULTANT, confidence="high")
    repeated_dims_min_share: float = setting(
        0.02, block="detection", unit="share", lo=0.001, hi=0.5, step=0.005, label="Copied size: share of peers",
        what="The same length x width x height on at least this share of a peer group (and at least the count "
             "below) is a copied default, not a measurement.",
        why="different items rarely measure exactly alike; a size shared by many is usually pasted",
        effect="Raise it to flag only very common copied sizes; lower it to flag smaller clusters (genuinely "
               "identical items may then be flagged).",
        source=_CONSULTANT, confidence="low")
    repeated_dims_min_count: int = setting(
        10, block="detection", unit="count", lo=2, hi=10000, step=1, label="Copied size: SKUs at least",
        what="A copied size must also be shared by at least this many SKUs.",
        why="a handful of identical items is normal (colour variants); many is not",
        effect="Raise it to ignore small clusters; lower it to catch them.",
        source=_CONSULTANT, confidence="low")
    case_fill_min: float = setting(
        0.9, block="detection", unit="x", lo=0.5, hi=1.0, step=0.01, label="Case must hold at least",
        what="A case whose volume is below its units' total volume times this cannot hold them; the same "
             "allowance applies to a case lighter than its units.",
        why="allows for rounded sizes and slightly compressible goods",
        effect="Raise it toward 1 to flag tighter cases; lower it to allow more rounding.",
        source=_CONSULTANT, confidence="medium")
    case_fill_max: float = setting(
        3.0, block="detection", unit="x", lo=1.2, hi=20.0, step=0.1, label="Case mostly air above",
        what="A case more than this many times its units' total volume is mostly air: the case or the case "
             "pack is suspect.",
        why="dunnage and loose packing rarely triple the volume",
        effect="Raise it to accept looser packing; lower it to flag more cases.",
        source=_CONSULTANT, confidence="low")
    unit_slip_ratios: tuple = setting(
        (2.54, 25.4, 10.0, 100.0, 16.0), block="detection", unit="numbers", label="Unit slip ratios",
        what="A value this many times (or this fraction of) its peers, that becomes normal when divided back, "
             "was probably typed in another unit: centimetres, millimetres, a decimal shift, ounces.",
        why="the usual mix-ups between inches, centimetres, millimetres, pounds and ounces",
        effect="Add a ratio for another mix-up; remove one to stop suggesting it.",
        source="Unit conversion factors", confidence="high")
    unit_slip_tolerance: float = setting(
        0.08, block="detection", unit="share", lo=0.01, hi=0.3, step=0.01, label="Unit slip tolerance",
        what="How close a value's ratio to its peers must be to a slip ratio to count as that slip.",
        why="close enough for rounding, far enough apart that 10 and 16 are not confused",
        effect="Raise it to catch rougher slips (more false alarms); lower it for exact ones only.",
        source=_CONSULTANT, confidence="medium")
    score_green: float = setting(
        0.90, block="detection", unit="share", lo=0.5, hi=1.0, step=0.01, label="Score: green from",
        what="A score at or above this share is shown green: the data is good enough to design from.",
        why="at 90% of the work described, the remaining gap rarely changes a system choice",
        effect="Raise it to demand cleaner data before showing green; lower it to be more lenient.",
        source=_CONSULTANT, confidence="low")
    score_amber: float = setting(
        0.75, block="detection", unit="share", lo=0.3, hi=1.0, step=0.01, label="Score: amber from",
        what="A score at or above this share (and below green) is amber: usable with care; below it, red.",
        why="below three quarters of the work described, sizing rests too much on estimates",
        effect="Raise it to show red sooner; lower it to show amber longer.",
        source=_CONSULTANT, confidence="low")
