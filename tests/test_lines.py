"""Tests for the order-line definition (a per-client choice)."""
import numpy as np
import pandas as pd
import pytest

from gtpfit.lines import LINE_DEFINITIONS, define_lines, order_profile


def _orders():
    # order A: SKU X split over two cartons (one full case + loose units); order B: two different SKUs
    return pd.DataFrame({
        "order_id": ["A", "A", "A", "B", "B"],
        "sku": ["X", "X", "Y", "X", "Z"],
        "order_date": pd.to_datetime(["2025-01-02"] * 5),
        "channel": ["Store", "Store", "Store", "Ecommerce", "Ecommerce"],
        "qty": pd.array([12, 3, 1, 1, pd.NA], dtype="Int64"),
        "cases": [1, 0, 0, 0, 0],
        "full_case": [True, False, False, False, False],
    })


def test_definitions_are_documented():
    assert set(LINE_DEFINITIONS) == {"row", "order_sku"}
    assert all(isinstance(v, str) and v for v in LINE_DEFINITIONS.values())


def test_row_keeps_every_row():
    o = define_lines(_orders(), "row")
    assert len(o) == 5 and (o["picks"] == 1).all()


def test_order_sku_merges_repeats():
    o = define_lines(_orders(), "order_sku").set_index(["order_id", "sku"])
    assert len(o) == 4
    ax = o.loc[("A", "X")]
    assert ax["qty"] == 15 and ax["picks"] == 2 and ax["cases"] == 1 and bool(ax["full_case"])
    assert ax["channel"] == "Store"
    assert pd.isna(o.loc[("B", "Z"), "qty"])  # unknown stays unknown, not 0


def test_unknown_definition_rejected():
    with pytest.raises(ValueError, match="row"):
        define_lines(_orders(), "carton")


def test_order_profile_by_definition():
    p = order_profile(_orders(), "row").set_index("channel")
    q = order_profile(_orders(), "order_sku").set_index("channel")
    assert p.loc["Store", "lines_per_order"] == 3 and q.loc["Store", "lines_per_order"] == 2
    assert p.loc["Store", "units_per_order"] == q.loc["Store", "units_per_order"] == 16
    assert q.loc["Store", "picks_per_line"] == 1.5
    assert p.loc["All", "orders"] == 2


def test_benchmark_shape_uses_definition():
    from gtpfit.synth.benchmark import shape
    o = _orders().rename(columns={"qty": "units"})
    a, _ = shape(o, line_definition="row")
    b, _ = shape(o, line_definition="order_sku")
    assert a["store_lines_per_order"] == 3 and b["store_lines_per_order"] == 2
    assert b["picks_per_line"] == pytest.approx(5 / 4)
