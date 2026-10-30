"""profile/ stage 2: volumes and seasonality, order profile, hourly profile, ABC velocity, stock (days on hand,
turns) and inbound, then run_profile reading only tables.load and writing _profile/.  Small hand-built tables
whose answers can be worked out by hand."""
import json

import numpy as np
import pandas as pd
import pytest

from gtpfit.profile import ProfileParams
from gtpfit.profile.hourly import hourly
from gtpfit.profile.inbound import inbound
from gtpfit.profile.orders import order_shape
from gtpfit.profile.stock import stock
from gtpfit.profile.velocity import abc
from gtpfit.profile.volumes import volumes

P = ProfileParams()


def _orders(rows):
    """rows: (order_id, sku, qty, date, channel, hour)."""
    return pd.DataFrame([dict(order_id=o, sku=s, qty=q, order_date=pd.Timestamp(d), channel=c,
                              pick_secs=None if h is None else h * 3600) for o, s, q, d, c, h in rows])


# Mon 3 Mar .. Fri 7 Mar 2025 and Mon 7 Apr 2025: March light, April heavy
ORDERS = _orders(
    [("o1", "A", 1, "2025-03-03", "Ecomm", 9), ("o1", "B", 2, "2025-03-03", "Ecomm", 9),
     ("o2", "A", 1, "2025-03-04", "Retail", 10), ("o3", "A", 3, "2025-03-05", "Ecomm", 10),
     ("o4", "C", 1, "2025-03-06", "Retail", 14), ("o5", "A", 1, "2025-03-07", "Ecomm", 10)] +
    [(f"p{i}", "A" if i < 6 else "B", 1, "2025-04-07", "Ecomm", 10) for i in range(8)])


def test_volumes_months_peak_ratio_and_weekdays():
    v = volumes({"orders": ORDERS}, "row", P)
    s = v.summary
    assert s["lines"]["average_day"] == pytest.approx(14 / 6) and s["lines"]["peak_day"] == 8
    assert s["peak_day_date"] == "2025-04-07"
    m = v.monthly.set_index("month")
    assert m.loc["2025-03", "lines_per_day"] == pytest.approx(6 / 5) and m.loc["2025-04", "lines_per_day"] == 8
    assert s["peak_month"] == "2025-04" and s["peak_month_ratio"] == pytest.approx(8 / (14 / 6))
    assert v.weekday["share"].sum() == pytest.approx(1.0) and v.weekday.set_index("weekday").loc["Monday", "lines"] == 10
    assert set(v.daily.columns) >= {"lines", "units", "orders", "lines_Ecomm", "lines_Retail"}


def test_order_shape_distribution():
    o = order_shape({"orders": ORDERS}, "row")
    dist = o.lines_per_order.set_index("lines_per_order")
    assert dist.loc["1", "orders"] == 12 and dist.loc["2", "orders"] == 1 and dist["share"].sum() == pytest.approx(1)
    prof = o.profile.set_index("channel")
    assert prof.loc["All", "orders"] == 13 and prof.loc["Ecomm", "lines"] == 12


def test_hourly_profile_and_peak_hour():
    h = hourly({"orders": ORDERS}, "row", design_capacity=100)
    assert h.available and h.by_hour.set_index("hour").loc[10, "lines"] == 11
    assert h.summary["peak_hour"] == 10 and h.summary["peak_hour_share"] == pytest.approx(11 / 14)
    assert h.summary["design_peak_hour"] == pytest.approx(100 * 11 / 14)
    none = hourly({"orders": ORDERS.drop(columns="pick_secs")}, "row")
    assert not none.available and "time of day" in none.note


ITEMS = pd.DataFrame({"sku": ["A", "B", "C", "D"], "unit_l": [2.0, 4, 6, 12], "unit_w": [12.0, 12, 12, 12],
                      "unit_h": [12.0, 12, 12, 12], "unit_weight": 1.0})


def test_abc_by_lines_with_cube_and_non_movers():
    inv = pd.DataFrame({"sku": ["A", "B", "C", "D"], "on_hand": [10, 10, 10, 50],
                        "snapshot_date": pd.Timestamp("2025-03-31")})
    a = abc({"orders": ORDERS, "inventory": inv}, ITEMS, "row", ProfileParams(abc_cuts=(0.8, 0.95, 1.0)))
    cls = a.skus.set_index("sku")["abc"]
    assert cls["A"] == "A" and cls["B"] == "B" and cls["C"] == "C" and cls["D"] == "No movement"
    s = a.summary.set_index("abc")
    assert s.loc["A", "lines_share"] == pytest.approx(10 / 14)
    assert s.loc["A", "cube_moved_ft3"] == pytest.approx(12 * 2 * 12 * 12 / 1728)   # 12 units of 1/6 ft3
    assert s.loc["No movement", "skus"] == 1


def test_abc_classes_end_at_50_80_95_of_lines_and_d_is_the_rest():
    counts = {"S1": 50, "S2": 30, "S3": 15, "S4": 3, "S5": 2}
    orders = pd.DataFrame({"sku": [k for k, n in counts.items() for _ in range(n)], "qty": 1})
    inv = pd.DataFrame({"sku": ["S1", "S9"], "on_hand": [5, 5], "snapshot_date": pd.Timestamp("2025-03-31")})
    a = abc({"orders": orders, "inventory": inv}, None, "row", ProfileParams())
    assert a.skus.set_index("sku")["abc"].to_dict() == {"S1": "A", "S2": "B", "S3": "C", "S4": "D", "S5": "D",
                                                        "S9": "No movement"}
    assert list(a.summary["abc"]) == ["A", "B", "C", "D", "No movement"]
    assert a.summary.set_index("abc").loc["D", "lines_share"] == pytest.approx(0.05)


def test_abc_ties_rank_by_sku():
    orders = pd.DataFrame({"sku": ["B", "A", "C", "C"], "qty": [9, 1, 1, 1]})
    a = abc({"orders": orders}, None, "row", ProfileParams())
    assert list(a.skus["sku"]) == ["C", "A", "B"]


def test_stock_days_on_hand_and_turns():
    inv = pd.DataFrame({"sku": ["A", "A", "B", "D"], "on_hand": [20, 40, 6, 50],
                        "snapshot_date": pd.to_datetime(["2025-03-01", "2025-04-01", "2025-03-01", "2025-03-01"])})
    st = stock({"orders": ORDERS, "inventory": inv}, ITEMS, "row", P)
    sk = st.skus.set_index("sku")
    assert st.summary["working_days"] == 6                            # too sparse for a calendar: days with orders
    assert sk.loc["A", "avg_on_hand"] == pytest.approx(30)           # averaged over the two snapshots
    assert sk.loc["A", "units_per_day"] == pytest.approx(12 / 6)
    assert sk.loc["A", "days_on_hand"] == pytest.approx(30 / 2)
    assert np.isinf(sk.loc["D", "days_on_hand"]) and st.summary["no_movement_skus"] == 1
    assert st.summary["turns"] > 0 and st.summary["available"]
    assert not stock({"orders": ORDERS}, ITEMS, "row", P).summary["available"]


def test_stock_that_is_a_fixed_number_of_days_for_every_sku_is_flagged():
    rng = np.random.default_rng(2)
    skus = [f"S{i}" for i in range(40)]
    o = pd.DataFrame({"order_id": [f"o{i}" for i in range(400)], "sku": rng.choice(skus, 400),
                      "qty": rng.integers(1, 5, 400), "order_date": pd.Timestamp("2025-03-03")})
    units = o.groupby("sku").qty.sum()
    derived = pd.DataFrame({"sku": units.index, "on_hand": units.to_numpy() * 7.0, "snapshot_date": pd.Timestamp("2025-03-03")})
    assert stock({"orders": o, "inventory": derived}, ITEMS, "row", P).summary["uniform_days_on_hand"]
    real = derived.assign(on_hand=rng.integers(1, 500, len(derived)))
    assert not stock({"orders": o, "inventory": real}, ITEMS, "row", P).summary["uniform_days_on_hand"]


def test_inbound_daily_receipts_and_design():
    rec = pd.DataFrame({"receipt_id": ["r1", "r1", "r2", "r3"], "sku": ["A", "B", "A", "C"], "qty": [10, 5, 20, 1],
                        "receipt_date": pd.to_datetime(["2025-03-03", "2025-03-03", "2025-03-04", "2025-03-05"])})
    i = inbound({"receipts": rec}, P)
    assert i.summary["available"] and i.summary["lines"]["peak_day"] == 2 and i.summary["units"]["total"] == 36
    assert i.design.method == "fallback_percentile"
    assert not inbound({}, P).summary["available"]


def test_run_profile_reads_tables_load_and_writes_outputs(monkeypatch, tmp_path):
    import gtpfit.profile as prof
    seen = []

    dense = _orders([(f"d{i}-{j}", "ABC"[j % 3], 1, day, "Ecomm" if j % 2 else "Retail", 10)
                     for i, day in enumerate(pd.bdate_range("2025-03-03", periods=20)) for j in range(5 + i % 4)])

    def fake_load(client, scoped=True):
        seen.append((client, scoped))
        return {"item_master": ITEMS, "orders": dense}
    monkeypatch.setattr(prof.tables, "load", fake_load)
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    r = prof.run_profile("Acme", sla={"Ecomm": {"within": [(0, 0.9)]}})
    assert seen and all(s == ("Acme", True) for s in seen)
    assert r.design.method == "sla" and r.summary["design"]["capacity"] == r.design.capacity
    out = prof.write_outputs(r, "Acme")
    for name in ("daily.parquet", "abc.parquet", "profile_summary.json"):
        assert (out / name).exists(), name
    s = json.loads((out / "profile_summary.json").read_text(encoding="utf-8"))
    assert s["design"]["binding"] == r.design.binding and "volumes" in s and "hourly" in s


def test_sla_is_saved_per_client(monkeypatch, tmp_path):
    import gtpfit.profile as prof
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    sla = {"Ecomm": {"within": [(0, 0.95)]}, "Retail": {"within": [(3, 1.0)], "max_backlog_days": 2.0}}
    prof.save_sla("Acme", sla)
    assert prof.load_sla("Acme") == sla


def test_load_items_prefers_the_backfilled_item_master(monkeypatch, tmp_path):
    from gtpfit import backfill
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr(backfill.tables, "load", lambda c, scoped=True: {"item_master": ITEMS})
    assert backfill.load_items("Acme").equals(ITEMS)
    out = tmp_path / "ws" / "Acme" / "_backfill"
    out.mkdir(parents=True)
    filled = ITEMS.assign(unit_l=99.0)
    filled.to_parquet(out / "item_master_backfilled.parquet", index=False)
    assert (backfill.load_items("Acme")["unit_l"] == 99.0).all()
