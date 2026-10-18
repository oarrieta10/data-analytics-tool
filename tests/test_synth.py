"""Tests for the synthetic generator: determinism, answer-key integrity, realism."""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gtpfit.synth import load_profile, generate
from gtpfit.synth.qa import realism_checks

SMALL = dict(n_skus=1500, ecom_orders_per_day=250, b2b_orders_per_day=8, days=180)


@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    out = tmp_path_factory.mktemp("small")
    generate(load_profile(**SMALL), out)
    return out


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def test_same_seed_same_files(ds, tmp_path):
    generate(load_profile(**SMALL), tmp_path)
    for f in ("item_master.csv", "orders.csv", "receipts.csv", "inventory_snapshots.csv", "_truth/defects.csv"):
        assert _md5(ds / f) == _md5(tmp_path / f), f


def test_different_seed_differs(ds, tmp_path):
    generate(load_profile(**SMALL, seed=7), tmp_path)
    assert _md5(ds / "orders.csv") != _md5(tmp_path / "orders.csv")


def test_answer_key_matches_observed_item_master(ds):
    im = pd.read_csv(ds / "item_master.csv", dtype={"Item Number": str})
    truth = pd.read_csv(ds / "_truth/item_master_true.csv", dtype={"sku": str}).set_index("sku")
    dfx = pd.read_csv(ds / "_truth/defects.csv")
    dfx = dfx[(dfx.table == "item_master") & (dfx.field != "*")]
    col = {"unit_l": "Unit Length", "unit_w": "Unit Width", "unit_h": "Unit Height", "unit_weight": "Unit Weight",
           "case_qty": "Case Pack", "case_l": "Case Length", "case_w": "Case Width", "case_h": "Case Height"}
    first = im.drop_duplicates("Item Number", keep=False).set_index("Item Number")
    checked = 0
    for r in dfx.itertuples():
        if r.key not in first.index:
            continue  # duplicate-row SKUs are ambiguous by design
        obs = first.at[r.key, col[r.field]]
        assert float(truth.at[r.key, r.field]) == pytest.approx(float(r.true_value))
        if pd.isna(r.observed_value):
            assert pd.isna(obs)
        else:
            assert float(obs) == pytest.approx(float(r.observed_value))
        checked += 1
    assert checked > 100


def test_clean_skus_equal_truth(ds):
    """SKUs with no planted defect must carry their true values exactly."""
    im = pd.read_csv(ds / "item_master.csv", dtype={"Item Number": str}).drop_duplicates("Item Number", keep=False)
    truth = pd.read_csv(ds / "_truth/item_master_true.csv", dtype={"sku": str}).set_index("sku")
    bad = set(pd.read_csv(ds / "_truth/defects.csv")["key"])
    clean = im[~im["Item Number"].isin(bad)].set_index("Item Number")
    assert len(clean) > 1000
    np.testing.assert_allclose(clean["Unit Length"], truth.loc[clean.index, "unit_l"])
    np.testing.assert_allclose(clean["Unit Weight"], truth.loc[clean.index, "unit_weight"])


def test_orphans_absent_and_ordered(ds):
    im = pd.read_csv(ds / "item_master.csv", dtype={"Item Number": str})
    o = pd.read_csv(ds / "orders.csv", dtype={"Item Number": str})
    dfx = pd.read_csv(ds / "_truth/defects.csv")
    orphans = set(dfx.loc[dfx.defect == "orphan_not_in_item_master", "key"])
    assert orphans and not orphans & set(im["Item Number"])
    assert orphans & set(o["Item Number"].str.strip().str.upper())


def test_physics_of_true_data(ds):
    t = pd.read_csv(ds / "_truth/item_master_true.csv")
    assert (t.unit_l >= t.unit_w).all() and (t.unit_w >= t.unit_h).all()
    assert (t.case_l * t.case_w * t.case_h >= t.unit_l * t.unit_w * t.unit_h * t.case_qty).all()
    assert (t.case_weight > t.unit_weight * t.case_qty).all()


def test_inventory_never_negative_and_receipts_in_cases(ds):
    s = pd.read_csv(ds / "inventory_snapshots.csv")
    assert (s["On Hand Qty"] > 0).all()
    r = pd.read_csv(ds / "receipts.csv", dtype={"Item Number": str})
    t = pd.read_csv(ds / "_truth/item_master_true.csv", dtype={"sku": str}).set_index("sku")
    full = (r["Qty Received"] % t.loc[r["Item Number"], "case_qty"].to_numpy() == 0).mean()
    assert full > 0.9  # most receipts are whole cases; a few are short-shipped


def test_realism_checks_in_range_for_default_shape(ds):
    checks, _ = realism_checks(ds)
    shape = checks[checks.key.isin(["top20_lines", "single_line", "lines_per_order", "clean_dims"])]
    assert (shape.status == "in range").all(), shape


def test_excel_truncation_clips_orders(tmp_path, monkeypatch):
    import importlib
    g = importlib.import_module("gtpfit.synth.generate")
    monkeypatch.setattr(g, "EXCEL_ROW_LIMIT", 5000)
    m = g.generate(load_profile(n_skus=300, ecom_orders_per_day=80, b2b_orders_per_day=2, days=60,
                                truncate_orders_at_excel_limit=True), tmp_path)
    assert m["orders_truncated_at_excel_limit"] is True
    assert len(pd.read_csv(tmp_path / "orders.csv")) == 5000


def test_report_html_has_no_mojibake(ds):
    from gtpfit.synth.qa import write_report
    html = Path(write_report(ds)).read_text(encoding="utf-8")
    # UTF-8 read as cp1252 turns "–", "✓", "×" into "â€“", "âœ“", "Ã—"
    assert not any(bad in html for bad in ("â€", "âœ", "Ã")), "report text is double-encoded"
