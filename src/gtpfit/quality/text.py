"""text.py — plain words for every quality code: a title, what it means, and what it does to the design.
Shown on the Quality page and in the items-to-measure list; a test fails if a code has no entry."""
from __future__ import annotations

from gtpfit.ingest.checks import IssueText as _T

QUALITY_TEXT: dict[str, _T] = {
    # ---- missing
    "missing_value": _T("Missing size or weight", "A length, width, height, weight or case pack is blank.",
                        "The item cannot be fitted to a container or weighed against a limit until it is measured."),
    "nonpositive_value": _T("Zero or negative size or weight", "A size, weight or case pack is zero or below.",
                            "Treated like a missing value: the item cannot be fitted."),
    # ---- placeholder
    "placeholder_dims": _T("Placeholder size", "Length, width and height are all the same fill-in value (e.g. "
                           "1 x 1 x 1 or 0.01), typed because the real size was unknown.",
                           "The cube is fiction: fit and storage volume would be wrong for these items."),
    "repeated_dims": _T("Copied default size", "Many items in the same group share exactly the same length, width "
                        "and height, which usually means a size was pasted rather than measured.",
                        "Fit and cube for these items reflect the copied size, not the items."),
    # ---- physics
    "density_out_of_band": _T("Impossible density", "Weight divided by cube falls outside the range for this "
                              "assortment: the size or the weight is wrong.",
                              "Weight limits and cube for the item cannot both be right."),
    "longest_side_out_of_band": _T("Longest side out of range", "The item's longest side falls outside the range "
                                   "for this assortment.", "The item may not exist at that size; fit would be wrong."),
    "case_smaller_than_unit": _T("Case smaller than the item", "A side of the case is shorter than the item's "
                                 "matching side.", "Case-level storage and fit are impossible as recorded."),
    "case_cannot_hold_units": _T("Case cannot hold its units", "The case's volume is less than its units' total "
                                 "volume.", "The case size or the case pack is wrong; case-level sizing would be wrong."),
    "case_mostly_air": _T("Case mostly air", "The case is many times bigger than its units need.",
                          "The case size or case pack is probably wrong; check before sizing case storage."),
    "case_lighter_than_units": _T("Case lighter than its units", "The case weighs less than its units added up.",
                                  "The case weight or the case pack is wrong."),
    "case_qty_not_whole": _T("Case pack not a whole number", "The number of units in a case has a fraction.",
                             "Case-to-unit conversions cannot be trusted for the item."),
    # ---- unit slip
    "unit_slip": _T("Likely typed in another unit", "The value is about 2.54, 10, 16 or 25.4 times (or that "
                    "fraction of) its peers and becomes normal when divided back: probably centimetres, "
                    "millimetres, ounces or a decimal shift.", "Fit, cube or weight would be off by that factor; the "
                    "suggested value is shown, not applied."),
    # ---- outliers
    "outlier_cube": _T("Cube unlike its peers", "The item's cube is far from similar items'.",
                       "Possibly a typing error; check before trusting fit for it."),
    "outlier_weight": _T("Weight unlike its peers", "The item's weight is far from similar items'.",
                         "Possibly a typing error; check before trusting weight limits for it."),
    "outlier_density": _T("Density unlike its peers", "Weight for its size is far from similar items'.",
                          "The size or the weight is probably wrong."),
    # ---- duplicates
    "duplicate_sku": _T("Repeated SKU row", "The SKU appears on more than one item-master row with the same values.",
                        "None for sizing; the extract has redundant rows."),
    "duplicate_conflict": _T("SKU rows disagree", "The SKU appears on more than one item-master row with different "
                             "values; the first row was checked.", "It is unclear which size is right."),
    # ---- orphans
    "orphan_sku": _T("Ordered but not in item master", "The SKU appears in the in-scope orders but has no item-master "
                     "row, so it has no size or weight at all.",
                     "Its picking work cannot be fitted to any container until it is added."),
    # ---- information
    "case_dims_derivable": _T("Case size can be estimated", "The case has no size, but its case pack and the item's "
                              "size are known, so a case size can be estimated later.",
                              "Case fit uses an estimate for these items."),
}
