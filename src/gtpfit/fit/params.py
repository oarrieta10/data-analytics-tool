"""params.py — every setting fit/ uses, with its label, three-part tooltip, source and confidence (declared with
gtpfit.ingest.params.setting, so the workbench draws and explains them the same way).

Container sizes, payloads and divider layouts are not here: they are in the container catalogue
(src/gtpfit/catalog/containers.yaml), each with its own source.
"""
from __future__ import annotations

from dataclasses import dataclass

from gtpfit.ingest.params import setting

CUBING_METHODS = ("tessellation", "liquid cube")


@dataclass
class FitParams:
    # ---- client settings -----------------------------------------------------------------------------
    cubing_method: str = setting(
        "tessellation", block="client", unit="choice", options=CUBING_METHODS, label="Count units by",
        what="Tessellation packs whole boxes of the unit's size (6 orientations, then the leftover space); liquid "
             "cube treats the units as filling the space like a fluid, up to the utilisation below. The weight "
             "limit applies either way.",
        why="boxed goods stack as boxes; tessellation is the conservative count",
        effect="Choose liquid cube for soft, bagged or nesting goods that fill gaps (apparel, polybags).",
        source="Consultant's earlier storage-sizing tool (cubing method 1 / 2)", confidence="high")
    keep_upright: bool = setting(
        False, block="client", unit="bool", label="Keep units upright",
        what="Only turn units on their base (the recorded height stays vertical): for liquids, 'this side up' "
             "goods, or anything that must not be laid on its side.",
        why="most boxed goods can be stored on any side",
        effect="On: fewer orientations, so fewer units fit, and long items stood on end no longer fit.",
        source="Consultant judgement", confidence="medium")
    cell_utilisation: float = setting(
        0.90, block="client", unit="share", lo=0.3, hi=1.0, step=0.01, label="Liquid cube: usable share",
        what="With liquid cube, the share of a container's (or cell's) volume units can actually fill; a "
             "container's own value in the catalogue takes precedence.",
        why="gaps, curved walls and handling leave about a tenth of a container unusable",
        effect="Raise it for goods that pack tightly; lower it for irregular goods.",
        source="Consultant's earlier storage-sizing tool (global cell utilisation 0.90)", confidence="medium")

    # ---- detection settings ------------------------------------------------------------------------------
    min_side_in: float = setting(
        0.05, block="detection", unit="ratio", lo=0.0, hi=1.0, step=0.01, label="Smallest believable side (in)",
        what="A unit or case side smaller than this is treated as missing, so a placeholder like 0.001 in is not "
             "packed by the thousand.",
        why="real stored goods are rarely thinner than a few hundredths of an inch",
        effect="Raise it to ignore more tiny sizes as errors; lower it for very thin goods (labels, cards).",
        source="Consultant judgement", confidence="medium")
