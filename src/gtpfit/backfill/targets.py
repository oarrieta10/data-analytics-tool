"""targets.py — what backfill does with each item, read from quality/'s flags: fill, keep, or borrow from.

    plan(status, flags, item_master, max_donors) -> (targets, donors)

Targets are active SKUs in the item master with an unusable or suspect flag on an item field:
    fill     a missing, zero, placeholder or copied value: estimated and used
    check    every other doubt (outside the assortment's ranges, a case that contradicts the item, an outlier,
             a suspected unit slip): the client's value is kept and the estimate shown beside it.  A unit slip's
             suggested correction is not applied: it is judged against peers, which in a catalogue without
             categories span the whole assortment, and on real parts catalogues it turned good sizes into
             impossible ones.
Donors are trusted SKUs (no unusable or suspect flag on an item field, every item size and weight positive),
active ones first, at most max_donors.  Orphans (ordered, not in the item master) have nothing to match on
and are left to quality's items-to-measure list.
"""
from __future__ import annotations

import pandas as pd

UNIT_FIELDS = ["unit_l", "unit_w", "unit_h", "unit_weight"]
DIMS = ["unit_l", "unit_w", "unit_h"]
FILL_CODES = {"missing_value", "nonpositive_value", "placeholder_dims", "repeated_dims"}
_ON_ITEM = set(UNIT_FIELDS) | {"unit", "*"}
_DOUBT = ("unusable", "suspect")


def _fields(field: str) -> list:
    return DIMS if field == "unit" else UNIT_FIELDS if field == "*" else [field]


def plan(status: pd.DataFrame, flags: pd.DataFrame, im: pd.DataFrame, max_donors: int):
    """targets: sku, fill (fields), check (bool), codes; donors: list of SKUs."""
    item = flags[flags["field"].isin(_ON_ITEM) & flags["severity"].isin(_DOUBT) & (flags["code"] != "orphan_sku")]
    in_master = set(im["sku"])
    st = status.drop_duplicates("sku").set_index("sku")
    active = set(st.index[st["bucket"] == "active"])

    rows = []
    for sku, g in item[item["sku"].isin(active & in_master)].groupby("sku", sort=True):
        fill, check = [], False
        for r in g.itertuples(index=False):
            if r.code in FILL_CODES:
                fill += _fields(r.field)
            else:
                check = True
        fill = [f for f in UNIT_FIELDS if f in fill]
        rows.append(dict(sku=sku, fill=fill, check=check, codes=", ".join(sorted(set(g["code"])))))
    targets = pd.DataFrame(rows, columns=["sku", "fill", "check", "codes"])

    first = im.drop_duplicates("sku")
    doubted = set(item["sku"])
    ok = first[~first["sku"].isin(doubted)]
    ok = ok[pd.concat([pd.to_numeric(ok[c], errors="coerce") > 0 for c in UNIT_FIELDS], axis=1).all(axis=1)]
    order = ok.assign(_inactive=~ok["sku"].isin(active)).sort_values(["_inactive", "sku"], kind="stable")
    donors = order["sku"].head(max_donors).tolist()
    return targets, donors
