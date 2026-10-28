"""text.py — plain words for every backfill method and action: a title, what it means, and what it does to the
design.  Shown on the Backfill page and in the outputs; a test fails if a method or action has no entry."""
from __future__ import annotations

from gtpfit.ingest.checks import IssueText as _T

BACKFILL_TEXT: dict[str, _T] = {
    # ---- methods: where a value came from
    "client": _T("Client's value", "The value in the client's item master, used as given.",
                 "Sizing uses the item as recorded."),
    "case_of_one": _T("Taken from a case of one", "The case holds one unit, so the unit's size and weight are the "
                      "case's.", "Slightly large: the case includes its packaging."),
    "neighbours": _T("From similar items", "Estimated from the trusted items most like it, by description, "
                     "category and any sizes it has.", "An estimate: good for sizing the system, not for "
                     "slotting a single item."),
    "neighbours_volume": _T("From similar items, scaled to its volume", "The shape comes from the most similar "
                            "trusted items and the size from the item's own recorded volume.",
                            "As good as the recorded volume; the proportions are estimated."),
    "peer_median": _T("Typical item of its group", "No trusted item is similar enough, so the median of its peer "
                      "group (or of all trusted items) is used.", "A placeholder for sizing totals; low "
                      "confidence, measure it if it matters."),
    "none": _T("No estimate", "There are no trusted items to borrow from.",
               "The item stays without a size until it is measured."),
    # ---- actions: what happened to the value
    "filled": _T("Filled", "The value was missing, a placeholder or copied, so the estimate is used.",
                 "The item can now be sized, on an estimate."),
    "kept": _T("Kept", "Quality doubted the value, but it is close to the estimate, so the client's value stays.",
               "Sizing uses the client's value."),
    "kept_review": _T("Kept, to review", "Quality doubted the value and it is far from the estimate; the client's "
                      "value stays until someone checks it.", "If the value is wrong, fit and storage for the "
                      "item are wrong: confirm it with the client."),
    "not_estimated": _T("Not estimated", "The value is missing and nothing could be estimated.",
                        "The item cannot be sized until it is measured."),
}
