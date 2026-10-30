"""The workbench Backfill page (Streamlit AppTest, no browser): it waits for a saved (or skipped) scope, shows
readiness before -> after that matches backfill_summary.json, says what gap is left, shows the holdout accuracy
table, lists filled and to-review items, looks up a SKU, and recomputes when a setting changes."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from gtpfit.ingest import ingest_folder, write_outputs  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
PAGE = """
from gtpfit.app.backfill import page
page()
"""
BLANKED = 3


@pytest.fixture(scope="module")
def scope_result():
    return ingest_folder(FIX / "scope_client", strict=False)


def _ws(tmp_path, monkeypatch, result, client="Acme", scoped=True):
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    out = write_outputs(result, tmp_path / "ws" / client / "_ingest", client=client)
    im = pd.read_parquet(out / "item_master.parquet")
    # gaps to fill: two items lose everything (generic descriptions: typical-item fallback), one only its weight
    # (its sizes find similar items)
    im.loc[im.index[:BLANKED - 1], ["unit_l", "unit_w", "unit_h", "unit_weight"]] = np.nan
    im.loc[im.index[BLANKED - 1], "unit_weight"] = np.nan
    im.to_parquet(out / "item_master.parquet", index=False)
    if scoped:
        from gtpfit.scope import ScopeSpec, save_scope
        save_scope(client, ScopeSpec(skipped=True, note="All rows: no scope set."))
    return client


def _page(client, **state):
    at = AppTest.from_string(PAGE, default_timeout=300)
    at.session_state["bf::client"] = client
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, at.exception
    return at


def _summary(tmp_path, client="Acme"):
    return json.loads((tmp_path / "ws" / client / "_backfill" / "backfill_summary.json").read_text(encoding="utf-8"))


def _text(at):
    return " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)


def test_waits_for_scope(tmp_path, monkeypatch, scope_result):
    client = _ws(tmp_path, monkeypatch, scope_result, scoped=False)
    at = _page(client)
    assert any("Set the scope" in i.value for i in at.info)
    assert at.button(key="bf::to_scope") and not at.metric


def test_readiness_tiles_match_the_summary_file(tmp_path, monkeypatch, scope_result):
    client = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    s = _summary(tmp_path)
    tiles = {m.label: m for m in at.metric}
    assert list(tiles)[:4] == ["Lines", "Units", "On-hand", "SKUs"]
    for basis, label in (("lines", "Lines"), ("units", "Units"), ("on_hand", "On-hand"), ("skus", "SKUs")):
        assert tiles[label].value == f"{s['ready_after'][basis]['share']:.0%}"
    assert s["actions"]["filled"] == BLANKED
    text = _text(at)
    assert f"{BLANKED} filled" in text and "still not ready" in text
    assert not at.button(key="bf::to_profile").disabled
    for f in ("values.parquet", "item_master_backfilled.parquet", "backfill_summary.json"):
        assert (tmp_path / "ws" / client / "_backfill" / f).exists()


def test_holdout_table_and_verdict(tmp_path, monkeypatch, scope_result):
    client = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    h = _summary(tmp_path)["holdout"]
    assert h["n"] >= 5
    text = _text(at)
    assert f"{h['n']:,} trusted items were hidden" in text
    assert f"×{h['fields']['volume']['factor']:.2f}" in text
    tables = [t.value for t in at.dataframe]
    assert any("Typical error" in df.columns for df in tables)


def test_filled_list_and_lookup(tmp_path, monkeypatch, scope_result):
    client = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    filled = pd.read_parquet(tmp_path / "ws" / client / "_backfill" / "values.parquet")
    borrowed = filled[(filled.action == "filled") & (filled.neighbours != "")]
    assert len(borrowed), "the fixture should give at least one estimate from similar items"
    sku = borrowed["sku"].iat[0]
    at.selectbox(key="bf::lookup").set_value(sku).run()
    assert not at.exception, at.exception
    assert any(sku in m.value for m in at.markdown)
    assert any("Borrowed from" in m.value for m in at.markdown)


def test_skipping_runs_nothing_and_later_modules_use_the_item_master_as_sent(tmp_path, monkeypatch, scope_result):
    from gtpfit.backfill import is_skipped, load_items
    client = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    assert load_items(client)["unit_weight"].notna().all()          # the estimates are in use
    at.button(key="bf::skip").click().run()
    assert not at.exception, at.exception
    assert is_skipped(client)
    assert any("skipped" in i.value for i in at.info) and not at.metric
    assert load_items(client)["unit_weight"].isna().sum() == BLANKED   # as sent
    assert (tmp_path / "ws" / client / "_backfill" / "values.parquet").exists()   # earlier results are kept

    fresh = _page(client)                                            # still skipped when the page opens again
    assert not fresh.metric and "of lines have usable" in _text(fresh)
    assert not fresh.button(key="bf::to_profile").disabled

    fresh.button(key="bf::unskip").click().run()
    assert not fresh.exception, fresh.exception
    assert not is_skipped(client) and fresh.metric
    assert load_items(client)["unit_weight"].notna().all()


def test_a_client_skipped_from_the_start_never_writes_backfill_outputs(tmp_path, monkeypatch, scope_result):
    from gtpfit.backfill import set_skipped
    client = _ws(tmp_path, monkeypatch, scope_result)
    set_skipped(client, True)
    _page(client)
    assert not (tmp_path / "ws" / client / "_backfill").exists()


def test_setting_recomputes_and_is_saved(tmp_path, monkeypatch, scope_result):
    import yaml
    client = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    at.number_input(key="bf::p::neighbours").set_value(1).run()
    assert not at.exception, at.exception
    assert _summary(tmp_path)["params"]["neighbours"] == 1
    saved = yaml.safe_load((tmp_path / "ws" / client / "client.yaml").read_text(encoding="utf-8"))
    assert saved["backfill"]["neighbours"] == 1
