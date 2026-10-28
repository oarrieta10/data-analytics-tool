"""params.py — every setting backfill/ uses, with its label, three-part tooltip, source and confidence
(declared with gtpfit.ingest.params.setting, so the workbench draws and explains them the same way).

Which values count as trustworthy, and which problems are fills rather than reviews, come from quality/'s
checks and the client's assortment bands; they are not repeated here.
"""
from __future__ import annotations

from dataclasses import dataclass

from gtpfit.ingest.params import setting

_CONSULTANT = "Consultant judgement"


@dataclass
class BackfillParams:
    # ---- client settings -----------------------------------------------------------------------------
    neighbours: int = setting(
        5, block="client", unit="count", lo=1, hi=25, step=1, label="Similar items used",
        what="An item's missing sizes are estimated from this many of the most similar trusted items (by "
             "description, category and any case or item sizes it has).",
        why="enough items to smooth one odd neighbour, few enough to stay specific",
        effect="Raise it for steadier, more average estimates; lower it to follow the closest items more "
               "closely (more specific, noisier).",
        source="Common choice for nearest-neighbour estimates", confidence="medium")
    review_factor: float = setting(
        3.0, block="client", unit="x", lo=1.5, hi=20.0, step=0.5, label="Review when off by",
        what="A client value quality doubts but backfill keeps (e.g. outside the assortment's ranges) is sent "
             "for review when it and the estimate differ by more than this factor in volume or weight.",
        why="similar items rarely differ threefold; a bigger gap usually means a typing error",
        effect="Raise it to review only gross disagreements; lower it to review more items.",
        source=_CONSULTANT, confidence="low")

    # ---- detection settings ------------------------------------------------------------------------------
    min_similarity: float = setting(
        0.3, block="detection", unit="share", lo=0.05, hi=0.9, step=0.05, label="Similar enough from",
        what="An item counts as similar when its similarity (0 to 1, from description, category and sizes) is "
             "at least this; with none that similar, the estimate falls back to the peer group's median.",
        why="below about a third, descriptions share only common words",
        effect="Raise it to borrow only from close matches (more peer medians); lower it to borrow from looser "
               "ones.",
        source=_CONSULTANT, confidence="low")
    high_similarity: float = setting(
        0.85, block="detection", unit="share", lo=0.1, hi=1.0, step=0.05, label="High confidence from",
        what="An estimate is high confidence when its closest item is at least this similar and the items it "
             "borrows from agree (see the spread below).",
        why="in a hold-out test on a parts catalogue, estimates this similar were typically 12% off in volume "
            "and 6% in weight",
        effect="Raise it to call fewer estimates high confidence; lower it to call more.",
        source="Hold-out test on one parts catalogue (2,226 hidden items)", confidence="medium")
    medium_similarity: float = setting(
        0.5, block="detection", unit="share", lo=0.05, hi=1.0, step=0.05, label="Medium confidence from",
        what="Below high confidence, an estimate is medium when its closest item is at least this similar; "
             "below it, low.",
        why="in the same hold-out test, estimates from 0.5 to 0.85 were typically 1.9x off in volume, and "
            "below 0.5 3.3x off",
        effect="Raise it to call fewer estimates medium; lower it to call more.",
        source="Hold-out test on one parts catalogue (567 and 87 hidden items)", confidence="medium")
    high_max_spread: float = setting(
        1.5, block="detection", unit="x", lo=1.05, hi=5.0, step=0.05, label="High confidence: spread at most",
        what="For high confidence, the volumes of the items borrowed from must agree within this factor (a "
             "robust spread around their median).",
        why="similar items within 50% of each other in volume give a usable estimate",
        effect="Raise it to accept more disagreement; lower it to demand closer agreement.",
        source=_CONSULTANT, confidence="low")
    size_weight: float = setting(
        1.0, block="detection", unit="ratio", lo=0.0, hi=5.0, step=0.1, label="Weight of sizes vs description",
        what="How much the case sizes and any item sizes already known count, against the description, when "
             "judging how similar two items are (0 = description only).",
        why="sizes and descriptions are equally informative when both are present",
        effect="Raise it to match items mostly by size; lower it to match them mostly by description.",
        source=_CONSULTANT, confidence="low")
    category_penalty: float = setting(
        0.5, block="detection", unit="share", lo=0.0, hi=1.0, step=0.05, label="Other-category penalty",
        what="When both items have a category and they differ, their similarity is reduced by this share.",
        why="an item in another category can still be the best match, but should rarely win",
        effect="Raise it toward 100% to borrow only within the category; lower it to ignore categories.",
        source=_CONSULTANT, confidence="low")
    description_ngrams: tuple = setting(
        (3, 5), block="detection", unit="numbers", label="Description letter groups",
        what="Descriptions are compared by the groups of this many consecutive letters they share (shortest, "
             "longest), which copes with abbreviations and words run together.",
        why="three to five letters capture word stems without matching by chance",
        effect="Longer groups demand closer wording; shorter ones match more loosely.",
        source="Character n-gram TF-IDF, a standard text-similarity method", confidence="medium")
    candidates: int = setting(
        30, block="detection", unit="count", lo=5, hi=500, step=5, label="Candidates per item",
        what="How many items are first shortlisted by description and by size before the most similar are "
             "chosen.",
        why="ample room above the items used, at little cost",
        effect="Raise it if good matches are being missed; lower it to run faster.",
        source=_CONSULTANT, confidence="medium")
    min_donors: int = setting(
        20, block="detection", unit="count", lo=1, hi=1000, step=1, label="Peer median needs at least",
        what="A peer group's median is used as a fallback only when the group has at least this many trusted "
             "items; otherwise the median of all trusted items is used.",
        why="enough items for a stable median",
        effect="Raise it to fall back to broader groups more often; lower it to use small groups.",
        source=_CONSULTANT, confidence="medium")
    max_donors: int = setting(
        200_000, block="detection", unit="count", lo=1_000, hi=5_000_000, step=1_000, label="Trusted items used at most",
        what="At most this many trusted items are searched (active ones first), to keep very large item "
             "masters fast.",
        why="ample for any assortment; only catalogues of millions reach it",
        effect="Raise it to search every trusted item (slower); lower it to run faster.",
        source=_CONSULTANT, confidence="medium")
    holdout_share: float = setting(
        0.2, block="detection", unit="share", lo=0.05, hi=0.5, step=0.05, label="Accuracy test: share hidden",
        what="To measure accuracy, this share of trusted active items has its sizes hidden and estimated from "
             "the rest.",
        why="enough items for a stable error, while most remain to borrow from",
        effect="Raise it for a steadier error figure (fewer items left to borrow from); lower it for the "
               "opposite.",
        source="Hold-out validation", confidence="high")
    seed: int = setting(
        0, block="detection", unit="count", lo=0, hi=10_000, step=1, label="Accuracy test: random seed",
        what="Which items are hidden in the accuracy test; the same seed always hides the same items.",
        why="any fixed number makes the test repeatable",
        effect="Change it to check that the error does not depend on which items were hidden.",
        source="Reproducibility", confidence="high")
