"""quality/: reads and classifies item data (never changes a value).  Small hand-built frames, one per check,
then status, readiness, the weighted score and the items-to-measure list.  Real-client runs are in
tests/test_quality_real.py (local only)."""
import dataclasses

import numpy as np
import pandas as pd
import pytest

from gtpfit.ingest import IngestParams
from gtpfit.quality import QualityParams, assess
from gtpfit.quality.checks import run_checks
from gtpfit.quality.peers import peer_groups, robust_z
from gtpfit.quality.text import QUALITY_TEXT

P = QualityParams(peer_min_size=5)
IP = IngestParams()


def _im(n=30, seed=1, **cols):
    """A clean item master: n SKUs of shoebox-like goods (density ~8 lb/ft3), cases of 6 that fit them."""
    rng = np.random.default_rng(seed)
    l, w, h = rng.uniform(8, 14, n), rng.uniform(5, 9, n), rng.uniform(3, 6, n)
    wt = l * w * h / 1728 * rng.uniform(6, 10, n)
    d = pd.DataFrame({"sku": [f"S{i:03d}" for i in range(n)], "description": [f"item {i}" for i in range(n)],
                      "unit_l": l.round(2), "unit_w": w.round(2), "unit_h": h.round(2), "unit_weight": wt.round(3),
                      "case_qty": 6.0, "case_l": (l * 3.1).round(2), "case_w": (w * 2.1).round(2),
                      "case_h": (h * 1.05).round(2), "case_weight": (wt * 6.2).round(3)})
    for k, v in cols.items():
        d[k] = v
    return d


def _flags(im, orders=None, params=P):
    return run_checks(im, orders, params, IP)


def _codes(flags, sku):
    return set(flags.loc[flags.sku == sku, "code"])


def test_clean_frame_has_no_problem_flags():
    f = _flags(_im())
    assert f[f.severity != "info"].empty, f[f.severity != "info"]


# ---- a. missing ---------------------------------------------------------------------------------------

def test_missing_and_nonpositive():
    im = _im()
    im.loc[0, "unit_l"] = np.nan
    im.loc[1, "unit_weight"] = 0
    im.loc[2, "case_qty"] = np.nan
    im.loc[3, "unit_h"] = -2
    f = _flags(im)
    assert "missing_value" in _codes(f, "S000") and "nonpositive_value" in _codes(f, "S001")
    assert "missing_value" in _codes(f, "S002") and "nonpositive_value" in _codes(f, "S003")
    row = f[(f.sku == "S000") & (f.code == "missing_value")].iloc[0]
    assert row.field == "unit_l" and row.severity == "unusable"


# ---- b. placeholder ----------------------------------------------------------------------------------

def test_placeholder_dims():
    im = _im()
    im.loc[0, ["unit_l", "unit_w", "unit_h"]] = 1.0
    im.loc[1, ["unit_l", "unit_w", "unit_h"]] = 0.01
    im.loc[2, ["unit_l", "unit_w", "unit_h"]] = [1.0, 1.0, 2.0]  # not all equal: not a placeholder
    f = _flags(im)
    assert "placeholder_dims" in _codes(f, "S000") and "placeholder_dims" in _codes(f, "S001")
    assert "placeholder_dims" not in _codes(f, "S002")


def test_repeated_dims_copied_default():
    im = _im(n=60)
    im.loc[:11, ["unit_l", "unit_w", "unit_h"]] = [12.5, 7.0, 4.5]  # 12 SKUs share one size: a copied default
    f = _flags(im, params=dataclasses.replace(P, repeated_dims_min_count=10, repeated_dims_min_share=0.02))
    hit = set(f.loc[f.code == "repeated_dims", "sku"])
    assert hit == {f"S{i:03d}" for i in range(12)}
    f2 = _flags(im, params=dataclasses.replace(P, repeated_dims_min_count=13))
    assert f2[f2.code == "repeated_dims"].empty


# ---- c. physics -----------------------------------------------------------------------------------------

def test_density_and_longest_side_bands():
    im = _im()
    im.loc[0, "unit_weight"] = 200.0            # a shoebox weighing 200 lb: ~ 500 lb/ft3
    im.loc[1, ["unit_l", "unit_w", "unit_h"]] = [80.0, 6.0, 4.0]  # longest side 80 in, beyond the 36 in band
    im.loc[1, "unit_weight"] = 80 * 6 * 4 / 1728 * 8
    im.loc[1, ["case_l", "case_w", "case_h"]] = [250.0, 13.0, 5.0]
    im.loc[1, "case_weight"] = im.loc[1, "unit_weight"] * 6.2
    f = _flags(im)
    assert "density_out_of_band" in _codes(f, "S000") and "longest_side_out_of_band" in _codes(f, "S001")


def test_case_physics():
    im = _im()
    im.loc[0, ["case_l", "case_w", "case_h"]] = im.loc[0, ["unit_l", "unit_w", "unit_h"]].to_numpy() / 2  # smaller
    im.loc[1, "case_qty"] = 600.0                  # 600 units cannot fit in a case sized for 6
    im.loc[2, ["case_l", "case_w", "case_h"]] = im.loc[2, ["case_l", "case_w", "case_h"]].to_numpy() * 3  # 27x air
    im.loc[3, "case_weight"] = im.loc[3, "unit_weight"] * 2  # lighter than its 6 units
    im.loc[4, "case_qty"] = 6.5
    f = _flags(im)
    assert "case_smaller_than_unit" in _codes(f, "S000")
    assert "case_cannot_hold_units" in _codes(f, "S001")
    assert "case_mostly_air" in _codes(f, "S002")
    assert "case_lighter_than_units" in _codes(f, "S003")
    assert "case_qty_not_whole" in _codes(f, "S004")
    sev = f.set_index(["sku", "code"])["severity"]
    assert sev[("S002", "case_mostly_air")] == "suspect" and sev[("S000", "case_smaller_than_unit")] == "unusable"


# ---- d. unit slip; e. outliers ----------------------------------------------------------------------------

def test_unit_slip_suggests_but_never_applies():
    im = _im()
    im.loc[0, "unit_l"] = round(im.loc[0, "unit_l"] * 2.54, 2)       # length typed in cm
    im.loc[1, "unit_weight"] = round(im.loc[1, "unit_weight"] * 16, 3)  # weight typed in ounces
    before = im.copy()
    f = _flags(im)
    s0 = f[(f.sku == "S000") & (f.code == "unit_slip")]
    assert len(s0) == 1 and s0.field.iat[0] == "unit_l"
    assert s0.suggested.iat[0] == pytest.approx(im.loc[0, "unit_l"] / 2.54, rel=1e-6)
    assert "unit_slip" in _codes(f, "S001")
    pd.testing.assert_frame_equal(im, before)  # nothing changed


def test_natural_spread_is_not_a_slip():
    im = _im(n=40, seed=3)
    f = _flags(im)
    assert f[f.code == "unit_slip"].empty


def test_outliers_in_log_space():
    im = _im(n=40)
    im.loc[0, "unit_weight"] = im.loc[0, "unit_weight"] * 7     # heavy for its peers, not a known slip ratio
    f = _flags(im)
    assert {"outlier_weight", "outlier_density"} & _codes(f, "S000")
    assert len(f[f.code.str.startswith("outlier")].sku.unique()) <= 2  # the rest of the frame is quiet


def test_peer_group_fallback():
    im = _im(n=12)
    im["category"] = ["A"] * 6 + ["B"] * 6
    im["subcategory"] = ["a1"] * 2 + ["a2"] * 4 + ["b1"] * 6
    g = peer_groups(im, min_size=5)
    lv = g.set_index("sku")["peer_level"]
    assert lv["S000"] == "category" and lv["S006"] == "subcategory"   # a1 too small -> category A; b1 has 6
    g2 = peer_groups(im, min_size=20)
    assert set(g2["peer_level"]) == {"all"}
    z = robust_z(pd.Series([1.0, 1.0, 1.0, 1.0]), pd.Series(["g"] * 4))
    assert (z == 0).all()  # no spread: nothing is an outlier


# ---- f. duplicates; g. orphans -------------------------------------------------------------------------

def test_duplicates_identical_and_conflicting():
    im = _im()
    same = im.iloc[[0]].copy()
    other = im.iloc[[1]].copy()
    other["unit_l"] = other["unit_l"] * 1.5
    f = _flags(pd.concat([im, same, other], ignore_index=True))
    assert "duplicate_sku" in _codes(f, "S000") and "duplicate_conflict" in _codes(f, "S001")


def test_orphans_from_orders():
    im = _im()
    orders = pd.DataFrame({"order_id": ["o1", "o2", "o3"], "sku": ["S000", "ZZZ", "ZZZ"], "qty": [1, 2, 3],
                           "order_date": pd.to_datetime(["2025-01-01"] * 3)})
    f = _flags(im, orders)
    o = f[f.code == "orphan_sku"]
    assert list(o.sku) == ["ZZZ"] and o.observed.iat[0] == pytest.approx(2 / 3)  # share of lines


# ---- status, readiness, score, items to measure -----------------------------------------------------------

def _tables(im, orders, inventory=None):
    t = {"item_master": im, "orders": orders}
    if inventory is not None:
        t["inventory"] = inventory
    return t


def _orders(rows):
    return pd.DataFrame([dict(order_id=o, sku=s, qty=q, order_date=pd.Timestamp("2025-03-01")) for o, s, q in rows])


def test_status_readiness_and_inactive():
    im = _im(n=10)
    im.loc[0, "unit_l"] = np.nan                             # unusable for unit fit
    im.loc[1, ["case_l", "case_w", "case_h"]] = np.nan       # case dims missing but derivable
    im.loc[2, "case_qty"] = np.nan                           # no case pack: not ready for case fit
    orders = _orders([("a", "S000", 1), ("b", "S001", 1), ("c", "S002", 1), ("d", "S003", 4)])
    r = assess(_tables(im, orders), P, IP, "row")
    st = r.status.set_index("sku")
    assert st.loc["S000", "status"] == "unusable" and not st.loc["S000", "ready_unit_fit"]
    assert st.loc["S001", "ready_case_fit"] and "case_dims_derivable" in set(r.flags.code)
    assert st.loc["S002", "ready_unit_fit"] and not st.loc["S002", "ready_case_fit"]
    assert st.loc["S009", "bucket"] == "inactive in scope" and st.loc["S003", "bucket"] == "active"


def test_score_shares_by_weighting_and_line_definition():
    im = _im(n=4)
    im.loc[0, "unit_weight"] = np.nan                      # S000 unusable
    orders = _orders([("a", "S000", 1), ("a", "S000", 1), ("b", "S001", 6), ("c", "S002", 1), ("d", "S003", 2)])
    inv = pd.DataFrame({"sku": ["S000", "S001"], "on_hand": [30, 10],
                        "snapshot_date": pd.to_datetime(["2025-03-01"] * 2)})
    by_row = assess(_tables(im, orders, inv), P, IP, "row").summary["score"]["unit_fit"]
    assert by_row["lines"]["share"] == pytest.approx(3 / 5)        # S000 has 2 of 5 rows
    assert by_row["units"]["share"] == pytest.approx(9 / 11)
    assert by_row["on_hand"]["share"] == pytest.approx(10 / 40)
    assert by_row["skus"]["share"] == pytest.approx(3 / 4)
    merged = assess(_tables(im, orders, inv), P, IP, "order_sku").summary["score"]["unit_fit"]
    assert merged["lines"]["share"] == pytest.approx(3 / 4)        # S000's two rows are one line
    no_inv = assess(_tables(im, orders), P, IP, "row").summary["score"]["unit_fit"]
    assert "on_hand" not in no_inv
    assert by_row["lines"]["band"] == "red"                         # 60% < amber 75%


def test_orphans_count_against_the_score():
    im = _im(n=2)
    orders = _orders([("a", "S000", 1), ("b", "ZZZ", 1), ("c", "S001", 1), ("d", "ZZZ", 1)])
    r = assess(_tables(im, orders), P, IP, "row")
    assert r.summary["score"]["unit_fit"]["lines"]["share"] == pytest.approx(0.5)
    assert r.status.set_index("sku").loc["ZZZ", "status"] == "orphan"


def test_items_to_measure_ranked_with_cumulative_gap():
    im = _im(n=6)
    im.loc[[0, 1, 2], "unit_l"] = np.nan
    orders = _orders([("a", "S000", 1)] + [(f"b{i}", "S001", 1) for i in range(3)] +
                     [(f"c{i}", "S002", 1) for i in range(6)] + [("d", "S003", 1)])
    items = assess(_tables(im, orders), P, IP, "row").items
    assert list(items.sku) == ["S002", "S001", "S000"]
    assert list(items.closes_gap.round(3)) == [0.6, 0.9, 1.0]
    assert "unit length" in items.fields_to_measure.iat[0] and items.what_is_wrong.iat[0]
    assert {"sku", "description", "what_is_wrong", "fields_to_measure", "lines", "share_of_lines"} <= set(items)


# ---- texts, params, the loader -----------------------------------------------------------------------------

def test_every_code_has_text():
    from gtpfit.quality.checks import CODES
    assert set(CODES) <= set(QUALITY_TEXT)
    for c, t in QUALITY_TEXT.items():
        assert t.title and t.what and t.impact, c


def test_every_quality_param_has_metadata():
    from gtpfit.ingest.params import BLOCKS, UNITS, meta, tooltip
    for f in dataclasses.fields(QualityParams):
        m = meta(f.name, QualityParams)
        for k in ("block", "label", "what", "why", "effect", "unit", "source", "confidence"):
            assert isinstance(m.get(k), str) and m[k].strip(), (f.name, k)
        assert m["block"] in BLOCKS and m["unit"] in UNITS
        assert tooltip(f.name, QualityParams).startswith(m["what"])
    client = [f.name for f in dataclasses.fields(QualityParams) if f.metadata["block"] == "client"]
    assert client == ["outlier_mad_k"]


def test_reads_only_through_tables_load(monkeypatch, tmp_path):
    import gtpfit.quality as q
    seen = []
    im = _im(n=5)
    orders = _orders([("a", "S000", 1)])

    def fake_load(client, scoped=True):
        seen.append((client, scoped))
        return {"item_master": im, "orders": orders}
    monkeypatch.setattr(q.tables, "load", fake_load)
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    r = q.run_quality("Acme")
    assert seen == [("Acme", True)] and len(r.status) == 5
    out = q.write_outputs(r, "Acme")
    for name in ("sku_status.parquet", "sku_flags.parquet", "quality_summary.json", "items_to_measure.csv"):
        assert (out / name).exists(), name
    assert out == tmp_path / "ws" / "Acme" / "_quality"
