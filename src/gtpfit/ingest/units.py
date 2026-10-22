"""units.py — which length and weight units an item master uses, and conversion to inches / pounds.

Decided per group of columns ("unit" = each dims + weight, "case" = case dims +
weight), in this order:
    1. a unit written in the header, e.g. "Length (cm)"
    2. the units saved for the client (chosen on the Review step)
    3. the data: the one unit pair whose median size and density fall inside the
       ranges for the client's assortment type (params.*_longest_side_in,
       params.density_lb_ft3, set together by params.ASSORTMENTS)
The data check also runs on declared units and refuses a declaration the
numbers contradict.  It cannot separate every pair (in/lb vs cm/oz give nearly
the same density), so when more than one pair fits and nothing is declared,
ingest refuses instead of guessing.

Works on the whole column (one unit for the file).  Single rows typed in the
wrong unit are a data-quality problem for quality/, not handled here.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .params import IngestParams

# exact definitions (NIST): 1 in = 2.54 cm; 1 lb = 0.45359237 kg = 16 oz
LENGTH_TO_IN = {"in": 1.0, "cm": 1 / 2.54, "mm": 1 / 25.4, "ft": 12.0, "m": 100 / 2.54}
WEIGHT_TO_LB = {"lb": 1.0, "kg": 1 / 0.45359237, "oz": 1 / 16, "g": 1 / 453.59237}
CUBIC_IN_PER_FT3 = 1728

_HINTS = {
    "length": [("mm", r"\bmm\b|millimet"), ("cm", r"\bcm\b|centimet"), ("ft", r"\bft\b|\bfeet\b|\bfoot\b"),
               ("m", r"\(m\)|\bmetres?\b|\bmeters?\b"), ("in", r"\bin\b\)?$|\(in\)|\binch|\"")],
    "weight": [("kg", r"\bkgs?\b|kilo"), ("oz", r"\boz\b|ounce"), ("lb", r"\blbs?\b|pound"),
               ("g", r"\(g\)|\bgr\b|\bgrams?\b|\bg$")],
}


def header_unit(header: str, kind: str) -> str | None:
    h = str(header).lower().strip()
    for unit, pat in _HINTS[kind]:
        if re.search(pat, h):
            return unit
    return None


def _stats(df: pd.DataFrame, dims: list, weight: str | None, lu: str, wu: str):
    """Median longest side (in) and median density (lb/ft3) under one unit hypothesis."""
    longest = density = np.nan
    if len(dims) == 3:
        d = df[dims].to_numpy(float) * LENGTH_TO_IN[lu]
        ok = (d > 0).all(axis=1)
        if ok.any():
            longest = float(np.median(d[ok].max(axis=1)))
            if weight is not None:
                w = df[weight].to_numpy(float)[ok] * WEIGHT_TO_LB[wu]
                vol = d[ok].prod(axis=1) / CUBIC_IN_PER_FT3
                good = w > 0
                if good.any():
                    density = float(np.median(w[good] / vol[good]))
    return longest, density


def expected_ranges(group: str, params: IngestParams) -> str:
    """The ranges a unit pair is judged against, in words (they come from the assortment type)."""
    side = params.unit_longest_side_in if group == "unit" else params.case_longest_side_in
    d = params.density_lb_ft3
    return (f"the expected ranges for this assortment (median longest side {side[0]:g}–{side[1]:g} in, "
            f"median density {d[0]:g}–{d[1]:g} lb/ft3)")


def assess(df: pd.DataFrame, dims: list, weight: str | None, group: str, params: IngestParams) -> pd.DataFrame:
    """Every (length, weight) unit pair with its implied medians and whether it is plausible."""
    side_band = params.unit_longest_side_in if group == "unit" else params.case_longest_side_in
    rows = []
    for lu in LENGTH_TO_IN:
        for wu in WEIGHT_TO_LB:
            longest, density = _stats(df, dims, weight, lu, wu)
            ok_side = np.isnan(longest) or side_band[0] <= longest <= side_band[1]
            ok_dens = np.isnan(density) or params.density_lb_ft3[0] <= density <= params.density_lb_ft3[1]
            rows.append(dict(length=lu, weight=wu, median_longest_in=longest, median_density_lb_ft3=density,
                             plausible=bool(ok_side and ok_dens),
                             evidence=bool(not np.isnan(longest) or not np.isnan(density))))
    return pd.DataFrame(rows)


def describe(row) -> str:
    parts = [f"{row.length}/{row.weight}"]
    if not np.isnan(row.median_longest_in):
        parts.append(f"median longest side {row.median_longest_in:.1f} in")
    if not np.isnan(row.median_density_lb_ft3):
        parts.append(f"median density {row.median_density_lb_ft3:.1f} lb/ft3")
    return parts[0] + (" (" + ", ".join(parts[1:]) + ")" if len(parts) > 1 else "")
