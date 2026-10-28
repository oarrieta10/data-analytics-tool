"""backfill/: estimates for item sizes quality cannot trust, each with its method, confidence and the donors it
borrowed from.  Small hand-built catalogues of parts families; the real-client holdout is in
tests/test_backfill_real.py (local only)."""
import dataclasses

import numpy as np
import pandas as pd
import pytest

from gtpfit.backfill import BackfillParams, assess
from gtpfit.backfill.text import BACKFILL_TEXT
from gtpfit.ingest import IngestParams
from gtpfit.ingest.params import assortment_bands
from gtpfit.quality import QualityParams, assess as quality_assess

IP = IngestParams(**assortment_bands("automotive / spare parts"))
QP = QualityParams(peer_min_size=5)
BP = BackfillParams(neighbours=3)

# (description stem, length, width, height in inches, density lb/ft3)
FAMILIES = [("HEX BOLT M8 X 20 ZINC", 1.2, 0.5, 0.5, 300.0),
            ("BRAKE CALIPER FRONT IRON", 9.0, 6.0, 4.0, 120.0),
            ("BODY SIDE MOLDING CHROME", 70.0, 3.0, 1.5, 6.0),
            ("SEAT HEADREST ASSEMBLY BLACK", 11.0, 9.0, 6.0, 4.0)]
VARIANTS = ["LEFT", "RIGHT", "UPPER", "LOWER", "REAR", "FRONT", "INNER", "OUTER"]


def _im(seed=3):
    """Four parts families of eight variants each: sizes within a few percent inside a family, far apart between."""
    rng = np.random.default_rng(seed)
    rows = []
    for f, (stem, l, w, h, dens) in enumerate(FAMILIES):
        for i, v in enumerate(VARIANTS):
            j = rng.uniform(0.95, 1.05, 3)
            ul, uw, uh = l * j[0], w * j[1], h * j[2]
            wt = ul * uw * uh / 1728 * dens
            rows.append(dict(sku=f"F{f}-{i}", description=f"{stem} {v}", category=f"C{f}",
                             unit_l=round(ul, 3), unit_w=round(uw, 3),
                             unit_h=round(uh, 3), unit_weight=round(wt, 4), case_qty=10.0,
                             case_l=round(ul * 2.2, 2), case_w=round(uw * 2.2, 2), case_h=round(uh * 2.2, 2),
                             case_weight=round(wt * 10.5, 3)))
    return pd.DataFrame(rows)


def _orders(skus):
    return pd.DataFrame([dict(order_id=f"o{i}", sku=s, qty=1, order_date=pd.Timestamp("2025-03-01"))
                         for i, s in enumerate(skus)])


def _run(im, bp=BP, active=None):
    t = {"item_master": im, "orders": _orders(active if active is not None else list(im.sku))}
    q = quality_assess(t, QP, IP, "row")
    return assess(t, q, bp, IP), q


def _v(res, sku, field):
    return res.values.set_index(["sku", "field"]).loc[(sku, field)]


def _sorted_dims(row):
    return sorted([row["unit_l"], row["unit_w"], row["unit_h"]], reverse=True)


# ---- what gets filled, what is kept ---------------------------------------------------------------------

def test_missing_item_takes_its_familys_size():
    im = _im()
    truth = im.loc[im.sku == "F1-3"].iloc[0]
    im.loc[im.sku == "F1-3", ["unit_l", "unit_w", "unit_h", "unit_weight"]] = np.nan
    res, _ = _run(im)
    for f in ("unit_l", "unit_w", "unit_h", "unit_weight"):
        r = _v(res, "F1-3", f)
        assert r.action == "filled" and r.method == "neighbours" and r.confidence in ("high", "medium")
        assert r.value == pytest.approx(truth[f], rel=0.15), f
    assert all(n.startswith("F1-") for n in _v(res, "F1-3", "unit_l").neighbours.split(", "))


def test_confidence_bands_follow_similarity():
    im = _im()
    im.loc[im.sku == "F1-3", ["unit_l", "unit_w", "unit_h", "unit_weight"]] = np.nan
    strict = dataclasses.replace(BP, high_similarity=0.999, medium_similarity=0.998)
    r = _v(_run(im, strict)[0], "F1-3", "unit_l")
    assert r.method == "neighbours" and r.confidence == "low"
    mid = dataclasses.replace(BP, high_similarity=0.999, medium_similarity=0.3)
    assert _v(_run(im, mid)[0], "F1-3", "unit_l").confidence == "medium"


def test_glued_description_still_matches():
    im = _im()
    im.loc[im.sku == "F0-2", "description"] = "HEXBOLTM8X20ZINCUPPER"
    im.loc[im.sku == "F0-2", ["unit_l", "unit_w", "unit_h", "unit_weight"]] = np.nan
    res, _ = _run(im)
    assert _v(res, "F0-2", "unit_l").neighbours.startswith("F0-")
    assert _v(res, "F0-2", "unit_l").value < 2


def test_only_the_missing_side_is_filled_and_known_sides_kept():
    im = _im()
    known = im.loc[im.sku == "F2-1", ["unit_l", "unit_w"]].iloc[0]
    im.loc[im.sku == "F2-1", "unit_h"] = np.nan
    res, _ = _run(im)
    assert _v(res, "F2-1", "unit_h").action == "filled" and _v(res, "F2-1", "unit_h").value < 3
    filled = res.items.set_index("sku").loc["F2-1"]
    assert filled.unit_l == known.unit_l and filled.unit_w == known.unit_w
    assert filled.unit_l_method == "client" and filled.unit_h_method == "neighbours"


def test_unit_slip_is_kept_for_review_not_corrected():
    """A suspected unit slip is judged against peers, which in a mixed catalogue span bolts to body panels: its
    suggested correction is not applied, the value is kept and the estimate shown for review."""
    im = _im()
    true_w = im.loc[im.sku == "F3-5", "unit_weight"].iat[0]
    im.loc[im.sku == "F3-5", "unit_weight"] = true_w * 16  # typed in ounces
    res, q = _run(im)
    assert "unit_slip" in set(q.flags.loc[q.flags.sku == "F3-5", "code"])
    r = _v(res, "F3-5", "unit_weight")
    assert r.action == "kept_review" and r.value == pytest.approx(true_w * 16)
    assert r.estimate == pytest.approx(true_w, rel=0.15)


def test_case_of_one_gives_the_unit():
    im = _im()
    i = im.index[im.sku == "F1-0"][0]
    im.loc[i, "case_qty"] = 1.0
    im.loc[i, ["case_l", "case_w", "case_h", "case_weight"]] = [9.1, 6.2, 4.1, 7.5]
    im.loc[i, ["unit_l", "unit_w", "unit_h", "unit_weight"]] = np.nan
    res, _ = _run(im)
    r = _v(res, "F1-0", "unit_l")
    assert r.method == "case_of_one" and r.value == pytest.approx(9.1)


def test_peer_median_when_nothing_is_similar():
    im = _im()
    im.loc[im.sku == "F3-7", "description"] = "QQQQ ZZZZ"
    im.loc[im.sku == "F3-7", ["unit_l", "unit_w", "unit_h", "unit_weight", "case_l", "case_w", "case_h",
                              "case_weight"]] = np.nan
    res, _ = _run(im)
    r = _v(res, "F3-7", "unit_l")
    assert r.method == "peer_median" and r.confidence == "low" and r.action == "filled"


def test_out_of_band_value_is_kept_and_reviewed_only_when_far_off():
    im = _im()
    im.loc[im.sku == "F1-6", ["unit_l", "unit_w", "unit_h"]] = [300.0, 200.0, 150.0]   # far off its family
    im.loc[im.sku == "F1-7", "unit_l"] = im.loc[im.sku == "F1-7", "unit_l"] * 1.3        # close: not reviewed
    ip = dataclasses.replace(IP, unit_longest_side_in=(0.2, 120.0))
    t = {"item_master": im, "orders": _orders(list(im.sku))}
    q = quality_assess(t, QP, ip, "row")
    res = assess(t, q, BP, ip)
    r = _v(res, "F1-6", "unit_l")
    assert r.action == "kept_review" and r.value == 300.0 and r.estimate < 20
    assert res.items.set_index("sku").loc["F1-6", "unit_l"] == 300.0
    assert "F1-7" not in set(res.values.loc[res.values.action == "kept_review", "sku"])


def test_trusted_values_are_never_changed():
    im = _im()
    im.loc[im.sku == "F0-0", "unit_weight"] = np.nan
    res, _ = _run(im)
    trusted = set(res.donors)
    assert len(trusted) >= 30
    out = res.items.set_index("sku")
    src = im.set_index("sku")
    for s in trusted:
        for f in ("unit_l", "unit_w", "unit_h", "unit_weight"):
            assert out.loc[s, f] == src.loc[s, f] and out.loc[s, f + "_method"] == "client"
    assert not set(res.values.sku) & trusted


def test_inactive_skus_are_not_estimated():
    im = _im()
    im.loc[im.sku.isin(["F0-1", "F0-2"]), "unit_l"] = np.nan
    res, _ = _run(im, active=[s for s in im.sku if s != "F0-2"])
    assert "F0-1" in set(res.values.sku) and "F0-2" not in set(res.values.sku)


def test_no_donors_means_not_estimated():
    im = _im()
    im[["unit_l", "unit_w", "unit_h", "unit_weight"]] = np.nan
    res, _ = _run(im)
    assert set(res.values.action) == {"not_estimated"} and res.values.value.isna().all()
    assert res.summary["donors"] == 0


def test_deterministic():
    im = _im()
    im.loc[im.sku.isin(["F0-3", "F2-4"]), ["unit_l", "unit_weight"]] = np.nan
    a, _ = _run(im)
    b, _ = _run(im)
    pd.testing.assert_frame_equal(a.values, b.values)


def test_summary_before_and_after():
    im = _im()
    im.loc[im.sku.isin(["F0-3", "F2-4"]), "unit_l"] = np.nan
    res, _ = _run(im)
    s = res.summary
    assert s["ready_before"]["skus"]["share"] == pytest.approx(30 / 32)
    assert s["ready_after"]["skus"]["share"] == pytest.approx(1.0)
    assert s["actions"]["filled"] == 2 and s["donors"] + s["targets"] == 32  # a mild outlier is a check target
    assert s["gap_after"] == {"orphans": 0.0, "kept_doubts": 0.0, "not_estimated": 0.0}


def test_gap_after_names_orphans_and_kept_doubts():
    im = _im()
    im.loc[im.sku == "F1-6", ["unit_l", "unit_w", "unit_h"]] = [300.0, 200.0, 150.0]   # out of range: kept
    res, _ = _run(im, active=list(im.sku) + ["NOT-IN-MASTER"])
    gap = res.summary["gap_after"]
    assert gap["orphans"] == pytest.approx(1 / 33) and gap["kept_doubts"] == pytest.approx(1 / 33)
    assert 1 - res.summary["ready_after"]["lines"]["share"] == pytest.approx(sum(gap.values()))


# ---- texts, params, loader ------------------------------------------------------------------------------

def test_every_method_and_action_has_text():
    from gtpfit.backfill.estimate import ACTIONS, METHODS
    assert set(METHODS) | set(ACTIONS) <= set(BACKFILL_TEXT)
    for k, t in BACKFILL_TEXT.items():
        assert t.title and t.what and t.impact, k


def test_every_backfill_param_has_metadata():
    from gtpfit.ingest.params import BLOCKS, UNITS, meta, tooltip
    for f in dataclasses.fields(BackfillParams):
        m = meta(f.name, BackfillParams)
        for k in ("block", "label", "what", "why", "effect", "unit", "source", "confidence"):
            assert isinstance(m.get(k), str) and m[k].strip(), (f.name, k)
        assert m["block"] in BLOCKS and m["unit"] in UNITS
        assert tooltip(f.name, BackfillParams).startswith(m["what"])
    client = [f.name for f in dataclasses.fields(BackfillParams) if f.metadata["block"] == "client"]
    assert client == ["neighbours", "review_factor"]


def test_reads_only_through_tables_load(monkeypatch, tmp_path):
    import gtpfit.backfill as b
    import gtpfit.quality as q
    seen = []
    im = _im()
    im.loc[im.sku == "F0-0", "unit_l"] = np.nan

    def fake_load(client, scoped=True):
        seen.append((client, scoped))
        return {"item_master": im, "orders": _orders(list(im.sku))}
    monkeypatch.setattr(q.tables, "load", fake_load)
    monkeypatch.setattr(b.tables, "load", fake_load)
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    r = b.run_backfill("Acme")
    assert seen and all(s == ("Acme", True) for s in seen)
    out = b.write_outputs(r, "Acme")
    for name in ("values.parquet", "item_master_backfilled.parquet", "backfill_summary.json"):
        assert (out / name).exists(), name
    assert out == tmp_path / "ws" / "Acme" / "_backfill"
