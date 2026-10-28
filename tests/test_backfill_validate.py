"""backfill/validate.py: the masked holdout.  Hide trusted items' sizes, estimate them from the rest, measure the
error against what was hidden; no answer key needed.  Uses the parts-family catalogue from test_backfill."""
import dataclasses

import numpy as np
import pandas as pd
import pytest

from gtpfit.backfill import BackfillParams, assess
from gtpfit.backfill.validate import holdout
from gtpfit.quality import assess as quality_assess

from test_backfill import IP, QP, _im, _orders

BP = BackfillParams(neighbours=3, holdout_share=0.25)


def _hold(im=None, bp=BP):
    im = _im() if im is None else im
    t = {"item_master": im, "orders": _orders(list(im.sku))}
    q = quality_assess(t, QP, IP, "row")
    return holdout(t, q, bp, IP)


def test_hides_a_seeded_share_of_trusted_active_items():
    a, b = _hold(), _hold()
    assert len(a.detail) == 8 and list(a.detail.sku) == list(b.detail.sku)  # 25% of 32, same seed same items
    c = _hold(bp=dataclasses.replace(BP, seed=7))
    assert list(c.detail.sku) != list(a.detail.sku)


def test_hidden_items_never_borrow_from_each_other():
    r = _hold()
    hidden = set(r.detail.sku)
    for nb in r.detail.neighbours.dropna():
        assert not set(filter(None, nb.split(", "))) & hidden


def test_errors_are_small_on_tight_families_and_beat_the_baseline():
    s = _hold().summary
    assert s["n"] == 8
    for f in ("long", "mid", "short", "weight", "volume"):
        assert s["fields"][f]["factor"] < 1.2, f
        assert s["fields"][f]["within_50"] >= 0.85, f
        assert s["fields"][f]["factor"] < s["baseline"][f]["factor"], f
    assert s["volume_lift"] > 1


def test_breakdown_by_method_and_confidence():
    s = _hold().summary
    assert sum(r["n"] for r in s["by_confidence"]) == 8
    assert {r["confidence"] for r in s["by_confidence"]} <= {"high", "medium", "low"}
    assert sum(r["n"] for r in s["by_method"]) == 8
    for r in s["by_confidence"]:
        assert {"volume_factor", "weight_factor", "volume_within_50"} <= set(r)


def test_too_few_trusted_items_says_so():
    im = _im()
    im.loc[im.index[2:], ["unit_l", "unit_w", "unit_h", "unit_weight"]] = np.nan
    s = _hold(im).summary
    assert s["n"] == 0 and "too few" in s["note"]


def test_holdout_goes_into_the_backfill_summary():
    im = _im()
    t = {"item_master": im, "orders": _orders(list(im.sku))}
    q = quality_assess(t, QP, IP, "row")
    res = assess(t, q, BP, IP, validate=True)
    assert res.summary["holdout"]["n"] == 8
    assert "holdout" not in assess(t, q, BP, IP).summary
