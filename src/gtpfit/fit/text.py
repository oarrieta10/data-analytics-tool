"""text.py — plain words for what limits a SKU's fit in a container: a title, what it means, and what it does to
the design.  Shown on the Fit page; a test fails if a limit has no entry."""
from __future__ import annotations

from gtpfit.ingest.checks import IssueText as _T

FIT_TEXT: dict[str, _T] = {
    "size": _T("Fits; space is the limit", "Units fit, and the container's space (not its weight limit) decides "
               "how many.", "Storage per SKU follows the unit's size."),
    "weight": _T("Fits; weight is the limit", "Units fit, but the container's weight limit is reached before its "
                 "space is full.", "Fewer units per container than the space allows: more containers for heavy "
                 "SKUs."),
    "too_big": _T("Too big", "Not one unit fits the container (or cell) in any allowed orientation.",
                  "The SKU cannot be stored in this container: another container, system or zone."),
    "too_heavy": _T("Too heavy", "A single unit (or case) is heavier than the container's weight limit.",
                    "The SKU cannot be stored in this container: another container, system or zone."),
    "missing_size": _T("Missing size", "The unit's size or weight is unknown, so its fit cannot be counted.",
                       "Measure it or backfill it before it can be sized."),
    "case_fits": _T("Case fits the tray", "The SKU's case is within the tray's carton range and weight limit: one "
                    "case per tray.", "The SKU can be stored as full cases on trays."),
    "case_outside_tray": _T("Case outside the tray's range", "The SKU's case is too long, wide, short or tall for "
                            "the cartons the tray takes.", "The SKU cannot be stored as cases on this tray."),
    "missing_case": _T("Missing case size", "The case's size or weight, or the case pack, is unknown.",
                       "The case cannot be checked against the tray until it is measured."),
}
