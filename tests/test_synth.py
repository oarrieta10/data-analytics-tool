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


# ---- peak-day order shape -------------------------------------------------

SHAPE_COLS = ["ecom_single_line_share", "ecom_multi_line_geom_p",
              "ecom_single_unit_share", "ecom_extra_units_geom_p"]


def _lift_cal(lifts):
    return pd.DataFrame({"event_lift": np.asarray(lifts, float)})


# the "bigger baskets on peak days" shape (the default is neutral)
BIGGER = dict(ecom_single_line_share=0.45, ecom_multi_line_geom_p=0.42,
              ecom_single_unit_share=0.75, ecom_extra_units_geom_p=0.50)


def test_default_peak_shape_is_neutral():
    from gtpfit.synth.demand import day_shape
    p = load_profile()
    s = day_shape(p, _lift_cal([1.0, 2.0, 3.5]))
    for c in SHAPE_COLS:
        assert getattr(p.peak_shape, c) is None
        assert (s[c] == getattr(p, c)).all(), c


def test_unset_peak_value_follows_changed_base():
    from gtpfit.synth.demand import day_shape
    p = load_profile(ecom_single_line_share=0.9, peak_shape=dict(ecom_single_unit_share=0.7))
    s = day_shape(p, _lift_cal([1.0, 2.0]))
    assert (s["ecom_single_line_share"] == 0.9).all()
    assert s["ecom_single_unit_share"].iloc[1] == pytest.approx(0.7)


def _neutral_profile():
    p = load_profile()
    return load_profile(peak_shape={c: getattr(p, c) for c in SHAPE_COLS})


def test_day_shape_neutral_is_constant_base():
    from gtpfit.synth.demand import day_shape
    p = _neutral_profile()
    s = day_shape(p, _lift_cal([0.9, 1.0, 1.35, 2.6, 10]))
    for c in SHAPE_COLS:
        assert (s[c] == getattr(p, c)).all(), c


def test_day_shape_bigger_baskets_on_peak():
    from gtpfit.synth.demand import day_shape
    p = load_profile(peak_shape=BIGGER)
    lifts = [0.8, 1.0, 1.2, 1.6, 2.0, 2.6, 50]
    s = day_shape(p, _lift_cal(lifts))
    # quiet days use the base values exactly
    for c in SHAPE_COLS:
        assert s[c].iloc[0] == s[c].iloc[1] == getattr(p, c)
    # at the reference lift the "at reference" values apply
    for c in SHAPE_COLS:
        assert s[c].iloc[4] == pytest.approx(getattr(p.peak_shape, c))
    # every probability falls as lift rises (fewer single lines/units, longer tails)
    for c in SHAPE_COLS:
        assert s[c].is_monotonic_decreasing, c
    # extreme lift is capped at max_t
    t_max = p.peak_shape.max_t
    exp = p.ecom_single_line_share + (p.peak_shape.ecom_single_line_share - p.ecom_single_line_share) * t_max
    assert s["ecom_single_line_share"].iloc[-1] == pytest.approx(max(exp, 0.01))
    assert s[SHAPE_COLS].ge(0.01).all().all() and s[SHAPE_COLS].le(0.99).all().all()


def test_day_shape_direction_can_flip():
    from gtpfit.synth.demand import day_shape
    p = load_profile(peak_shape=dict(ecom_single_line_share=0.65, ecom_single_unit_share=0.90))
    s = day_shape(p, _lift_cal([1.0, 2.0]))
    assert s["ecom_single_line_share"].is_monotonic_increasing
    assert s["ecom_single_unit_share"].iloc[1] == pytest.approx(0.90)


def test_unknown_peak_shape_key_rejected():
    with pytest.raises(ValueError, match="peak_shape"):
        load_profile(peak_shape=dict(ecom_single_lines=0.4))


def test_peak_days_have_bigger_ecom_baskets(tmp_path):
    generate(load_profile(n_skus=300, ecom_orders_per_day=400, b2b_orders_per_day=2, peak_shape=BIGGER,
                          start_date="2025-10-01", days=90), tmp_path)
    cal = pd.read_csv(tmp_path / "_truth/calendar.csv")
    assert set(SHAPE_COLS + ["event_lift"]) <= set(cal.columns)
    cal["date"] = pd.to_datetime(cal["date"])
    open_ = cal[~cal["closed"]]
    peak = set(open_.nlargest(2, "event_lift")["date"])           # Black Friday, Cyber Monday
    quiet = set(open_.nsmallest(20, "event_lift")["date"])        # early October
    o = pd.read_csv(tmp_path / "orders.csv", dtype={"Item Number": str})
    o = o[o["Channel"] == "Ecommerce"]
    o["date"] = pd.to_datetime(o["Order Date"], format="%m/%d/%Y")
    per = o.groupby(["date", "Order Nbr"]).agg(l=("Item Number", "size"), u=("Qty Shipped", "sum")).reset_index()

    def shape(days):
        x = per[per["date"].isin(days)]
        return (x["l"] == 1).mean(), x["u"].sum() / x["l"].sum()

    (sl_peak, upl_peak), (sl_quiet, upl_quiet) = shape(peak), shape(quiet)
    assert sl_quiet - sl_peak > 0.08, (sl_quiet, sl_peak)
    assert upl_peak - upl_quiet > 0.15, (upl_quiet, upl_peak)


# ---- config metadata, diff, estimate (drive the workbench form) ------------

def _all_fields():
    import dataclasses
    from gtpfit.synth.config import DefectRates, PeakShape, Profile
    for cls in (Profile, DefectRates, PeakShape):
        for f in dataclasses.fields(cls):
            if f.name in ("defects", "peak_shape"):
                continue
            yield cls, f


def test_every_setting_has_ui_metadata():
    from gtpfit.synth.config import SECTIONS
    p = load_profile()
    for cls, f in _all_fields():
        md = f.metadata
        assert md.get("label") and md.get("help") and md.get("section") in SECTIONS, (cls.__name__, f.name)
        lo, hi = md.get("lo"), md.get("hi")
        if lo is not None and hi is not None:
            obj = {"Profile": p, "DefectRates": p.defects, "PeakShape": p.peak_shape}[cls.__name__]
            v = getattr(obj, f.name)
            vals = v.values() if isinstance(v, dict) else (v if isinstance(v, list) else [v])
            assert all(lo <= x <= hi for x in vals if x is not None), (f.name, v, lo, hi)


@pytest.mark.parametrize("name", [None, "default", "messy_large", "playground"])
def test_profile_diff_roundtrip(name):
    from gtpfit.synth.config import profile_diff, profile_yaml
    import yaml
    root = Path(__file__).resolve().parents[1]
    p = load_profile(root / "profiles" / f"{name}.yaml") if name else load_profile()
    d = profile_diff(p)
    assert load_profile(**d) == p
    assert load_profile(**yaml.safe_load(profile_yaml(p))) == p
    if name is None:
        assert d == {}


def test_estimate_close_to_generated(tmp_path):
    from gtpfit.synth.preview import estimate
    p = load_profile(n_skus=800, ecom_orders_per_day=300, b2b_orders_per_day=10, days=200)
    est = estimate(p)
    m = generate(p, tmp_path)
    for k in ("order_lines", "orders"):
        assert abs(est[k] / m["counts"][k] - 1) < 0.10, (k, est[k], m["counts"][k])


def test_preview_has_daily_shape():
    from gtpfit.synth.preview import preview
    cal = preview(load_profile(start_date="2025-10-01", days=90, peak_shape=BIGGER))
    assert len(cal) == 90 and {"volume_factor", "event_lift", "ecom_single_line_share"} <= set(cal.columns)
    assert cal["ecom_single_line_share"].min() < load_profile().ecom_single_line_share
