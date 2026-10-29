"""fit/: how many units of a SKU fit a container (and each divider cell), and what limits it; a carton on a tray.
Hand-built boxes whose answers can be counted by hand, the catalogue file's rules, and the per-SKU assessment."""
import dataclasses

import numpy as np
import pandas as pd
import pytest

from gtpfit.fit import FitParams, assess
from gtpfit.fit.containers import Container, Layout, load_catalog
from gtpfit.fit.tessellate import units_by_liquid, units_by_tessellation, units_by_weight
from gtpfit.fit.text import FIT_TEXT


def _t(items, box, upright=False):
    return units_by_tessellation(np.asarray(items, float), np.asarray(box, float), upright).tolist()


# ---- tessellation -----------------------------------------------------------------------------------------

def test_cubes_fill_the_box():
    assert _t([[1, 1, 1]], [4, 4, 4]) == [64]
    assert _t([[2, 2, 2]], [4, 4, 4]) == [8]


def test_an_item_that_only_fits_on_its_side():
    assert _t([[10, 2, 2]], [4, 4, 12]) == [4]          # stood on end: 2 x 2 across the 4 x 4 floor
    assert _t([[10, 2, 2]], [4, 4, 12], upright=True) == [0]   # kept as it lies, 10 in long on a 4 x 4 floor


def test_leftover_space_is_filled():
    # 2 x 1 x 1 items in a 5 x 3 x 1 box: a block of 2 x 3 = 6 leaves a 1 x 3 strip that takes one more, turned
    assert _t([[2, 1, 1]], [5, 3, 1]) == [7]


def test_upright_keeps_height_vertical_but_turns_horizontally():
    assert _t([[3, 1, 2]], [1, 3, 2], upright=True) == [1]  # turned 90 degrees on the floor


def test_missing_or_zero_sizes_fit_nothing():
    assert _t([[0, 1, 1], [np.nan, 1, 1]], [4, 4, 4]) == [0, 0]


def test_never_more_than_the_volume_and_bigger_boxes_never_fit_fewer():
    rng = np.random.default_rng(0)
    items = rng.uniform(0.5, 9, (300, 3))
    small, big = np.array([12.0, 10, 8]), np.array([13.0, 10, 9])
    a, b = units_by_tessellation(items, small), units_by_tessellation(items, big)
    assert (a * items.prod(axis=1) <= small.prod() + 1e-9).all()
    assert (b >= a).all()


def test_liquid_cube_and_weight():
    assert units_by_liquid(np.array([10.0]), 100.0, 0.9).tolist() == [9]
    assert units_by_weight(np.array([7.0, 0.0, np.nan]), 60.0).tolist()[0] == 8


# ---- the catalogue ----------------------------------------------------------------------------------------

def _write(tmp_path, body):
    p = tmp_path / "containers.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_catalogue_converts_units_and_flags_placeholders(tmp_path):
    p = _write(tmp_path, """
containers:
  - id: t1
    name: Tote
    system: HaiPick Climb
    mode: units
    inside: {l: 600, w: 400, h: 300, unit: mm}
    payload: {value: 30, unit: kg, confidence: low, note: placeholder}
    source: spec sheet
    confidence: high
""")
    c = load_catalog(p)[0]
    assert c.inside_in == pytest.approx((600 / 25.4, 400 / 25.4, 300 / 25.4))
    assert c.payload_lb == pytest.approx(30 / 0.45359237) and c.placeholders == ["payload"]
    assert [lay.name for lay in c.layouts] == ["whole"]


def test_catalogue_refuses_a_container_without_a_source(tmp_path):
    p = _write(tmp_path, """
containers:
  - id: t1
    name: Tote
    system: X
    mode: units
    inside: {l: 10, w: 10, h: 10, unit: in}
    payload: {value: 50, unit: lb}
    confidence: high
""")
    with pytest.raises(ValueError, match="source"):
        load_catalog(p)


def test_payload_is_the_gross_limit_less_the_tare(tmp_path):
    p = _write(tmp_path, """
containers:
  - id: t1
    name: Tote
    system: HaiPick Climb
    mode: units
    inside: {l: 572, w: 377, h: 289, unit: mm}
    inside_top: {l: 606, w: 412, unit: mm}
    tare: {value: 2.22, unit: kg}
    gross_limit: {value: 30, unit: kg}
    source: spec sheet
    confidence: high
""")
    c = load_catalog(p)[0]
    assert c.payload_lb == pytest.approx((30 - 2.22) / 0.45359237) and c.placeholders == []
    assert c.inside_in[0] == pytest.approx(572 / 25.4)          # a tapered tote is fitted to its bottom
    assert c.inside_top_in == pytest.approx((606 / 25.4, 412 / 25.4))


def test_a_gross_limit_needs_a_tare(tmp_path):
    p = _write(tmp_path, """
containers:
  - id: t1
    name: Tote
    system: X
    mode: units
    inside: {l: 10, w: 10, h: 10, unit: in}
    gross_limit: {value: 30, unit: kg}
    source: s
    confidence: high
""")
    with pytest.raises(ValueError, match="tare"):
        load_catalog(p)


def test_the_shipped_catalogue_is_haipick_climb_containers_for_sale():
    cats = load_catalog()
    assert len(cats) == 4 and all(c.source and c.confidence for c in cats)
    assert {c.system for c in cats} == {"HaiPick Climb"}
    assert all("available for sale" in c.status for c in cats)
    assert all(not c.placeholders for c in cats)
    assert all(c.tare_lb + c.payload_lb == pytest.approx(30 / 0.45359237) for c in cats)   # 30 kg gross
    tray = next(c for c in cats if c.mode == "case_on_tray")
    assert tray.carton_range_in is not None
    assert all(lay.name == "whole" for c in cats for lay in c.layouts)                    # no dividers


# ---- the assessment ---------------------------------------------------------------------------------------

BOX = Container(id="box", name="Box", system="Test", supplier="", mode="units", inside_in=(12.0, 8.0, 6.0),
                payload_lb=40.0, layouts=[Layout("whole", 1, 1, 1), Layout("1/2", 2, 2, 1), Layout("1/4", 4, 2, 2)],
                source="test", confidence="high")
TRAY = Container(id="tray", name="Tray", system="Test", supplier="", mode="case_on_tray", inside_in=(24.0, 16.0, 1.2),
                 payload_lb=66.0, layouts=[Layout("whole", 1, 1, 1)], source="test", confidence="high",
                 carton_range_in=((8, 24), (8, 16), (2, 18)))


def _items():
    return pd.DataFrame({
        "sku": ["small", "heavy", "big", "nosize", "flat"],
        "unit_l": [2.0, 2.0, 20.0, np.nan, 11.0], "unit_w": [2.0, 2.0, 2.0, 1.0, 7.0],
        "unit_h": [2.0, 2.0, 2.0, 1.0, 1.0], "unit_weight": [0.5, 10.0, 1.0, 1.0, 1.0],
        "case_qty": [24, 4, 6, 10, 5], "case_l": [10.0, 8.0, 22.0, 12.0, 30.0], "case_w": [10.0, 8.0, 6.0, 10.0, 10.0],
        "case_h": [6.0, 4.0, 6.0, 8.0, 5.0], "case_weight": [13.0, 70.0, 7.0, 11.0, 6.0],
        "unit_l_method": ["client", "client", "client", "client", "neighbours"],
        "unit_l_confidence": ["", "", "", "", "medium"]})


def test_units_and_what_limits_them():
    r = assess(_items(), [BOX], FitParams())
    f = r.fit.set_index(["sku", "layout"])
    assert f.loc[("small", "whole"), "units"] == 6 * 4 * 3 and f.loc[("small", "whole"), "limit"] == "size"
    assert f.loc[("heavy", "whole"), "units"] == 4 and f.loc[("heavy", "whole"), "limit"] == "weight"
    assert f.loc[("big", "whole"), "units"] == 0 and f.loc[("big", "whole"), "limit"] == "too_big"
    assert f.loc[("nosize", "whole"), "limit"] == "missing_size" and np.isnan(f.loc[("nosize", "whole"), "units"])


def test_divider_cells_shrink_the_space_and_split_the_weight():
    f = assess(_items(), [BOX], FitParams()).fit.set_index(["sku", "layout"])
    assert f.loc[("small", "1/2"), "units"] == 3 * 4 * 3                 # a 6 x 8 x 6 cell
    assert f.loc[("heavy", "1/2"), "units"] == 2                         # 20 lb per half
    assert f.loc[("small", "1/4"), "units"] == 3 * 2 * 3                 # a 6 x 4 x 6 cell
    assert f.loc[("flat", "1/2"), "units"] == 0 and f.loc[("flat", "1/2"), "limit"] == "too_big"


def test_liquid_cube_method():
    f = assess(_items(), [BOX], FitParams(cubing_method="liquid cube", cell_utilisation=0.5)).fit.set_index(["sku", "layout"])
    assert f.loc[("small", "whole"), "units"] == int(12 * 8 * 6 * 0.5 // 8)


def test_a_case_on_a_tray():
    f = assess(_items(), [TRAY], FitParams()).fit.set_index("sku")
    assert f.loc["small", "units"] == 24 and f.loc["small", "limit"] == "case_fits"   # one case of 24
    assert f.loc["heavy", "limit"] == "too_heavy" and f.loc["heavy", "units"] == 0     # a 70 lb case, 66 lb tray
    assert f.loc["big", "limit"] == "case_outside_tray"    # 22 x 6 x 6: 6 in is narrower than the 8 in minimum
    assert f.loc["flat", "limit"] == "case_outside_tray"   # 30 in long
    assert f.loc["nosize", "units"] == 10                  # the case is known even when the unit is not


def test_estimated_sizes_are_carried_along():
    f = assess(_items(), [BOX], FitParams()).fit.set_index(["sku", "layout"])
    assert f.loc[("flat", "whole"), "size_method"] == "neighbours" and f.loc[("small", "whole"), "size_method"] == "client"


def test_summary_shares_weighted_by_lines():
    lines = pd.Series({"small": 6, "heavy": 2, "big": 1, "nosize": 1, "flat": 0})
    r = assess(_items(), [BOX], FitParams(), lines=lines)
    s = r.summary["containers"]["box"]
    assert s["layouts"]["whole"]["skus_share"] == pytest.approx(3 / 5)
    assert s["layouts"]["whole"]["lines_share"] == pytest.approx(8 / 10)
    assert s["limits"]["too_big"] == 1


def test_run_fit_reads_tables_load_and_needs_no_inventory(monkeypatch, tmp_path):
    import gtpfit.fit as fit
    from gtpfit import backfill
    seen = []
    orders = pd.DataFrame({"order_id": ["o1", "o2"], "sku": ["small", "heavy"], "qty": [1, 2],
                           "order_date": pd.Timestamp("2025-03-03")})

    def fake_load(client, scoped=True):
        seen.append((client, scoped))
        return {"item_master": _items(), "orders": orders}
    monkeypatch.setattr(fit.tables, "load", fake_load)
    monkeypatch.setattr(backfill.tables, "load", fake_load)
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    r = fit.run_fit("Acme", catalog=[BOX])
    assert seen and all(s == ("Acme", True) for s in seen)
    assert set(r.fit["sku"]) == {"small", "heavy"}                    # only SKUs active in scope
    out = fit.write_outputs(r, "Acme")
    assert (out / "fit.parquet").exists() and (out / "fit_summary.json").exists()


def test_every_limit_has_text():
    from gtpfit.fit import LIMITS
    assert set(LIMITS) <= set(FIT_TEXT)
    for k, t in FIT_TEXT.items():
        assert t.title and t.what and t.impact, k
