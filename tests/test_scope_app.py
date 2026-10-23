"""The workbench Scope page (Streamlit AppTest, no browser): it waits for Ingest, offers the scope columns,
recomputes the waterfall live, needs a note to save, and saves a definition that tables.load then applies."""
from pathlib import Path

import pandas as pd
import pytest
import yaml

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from gtpfit.ingest import ingest_folder, write_outputs  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
SCOPE_PAGE = """
from gtpfit.app.scope import page
page()
"""


@pytest.fixture(scope="module")
def scope_result():
    return ingest_folder(FIX / "scope_client", strict=False)


@pytest.fixture(scope="module")
def sample_result():
    return ingest_folder(FIX / "sample_client", strict=False)


def _page(tmp_path, monkeypatch, client=None, **state):
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    at = AppTest.from_string(SCOPE_PAGE, default_timeout=120)
    if client:
        at.session_state["scp::client"] = client
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, at.exception
    return at


def _ingested(tmp_path, result, client="Acme"):
    write_outputs(result, tmp_path / "ws" / client / "_ingest", client=client)
    return client


def _text(at) -> str:
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption] +
                    [i.value for i in at.info] + [w.value for w in at.warning])


def _waterfall(at) -> pd.DataFrame:
    return next(d.value for d in at.dataframe if "Lines left" in d.value.columns)


def test_waits_for_ingest(tmp_path, monkeypatch):
    at = _page(tmp_path, monkeypatch)
    assert "No client has finished Ingest yet" in _text(at)
    assert at.button(key="scp::to_ingest")
    assert not at.checkbox


def test_filters_offered_with_auto_exclusions(tmp_path, monkeypatch, scope_result):
    client = _ingested(tmp_path, scope_result)
    at = _page(tmp_path, monkeypatch, client)
    text = _text(at)
    for label in ("Site / warehouse", "Channel", "Order status", "Order type"):
        assert f"**{label}**" in text
    assert at.checkbox(key="scp::v::site::DC-East").value and at.checkbox(key="scp::v::site::DC-West").value
    assert not at.checkbox(key="scp::v::order_status::CANCELLED").value  # auto-excluded, any case
    assert not at.checkbox(key="scp::v::order_status::Returned").value
    assert "auto-excluded" in text
    assert "Filter on another column" in [s.label for s in at.selectbox]  # x_carrier is offered on request
    w = _waterfall(at)
    assert w["Step"].iat[0] == "All lines" and w["Step"].iat[-1] == "In scope"
    assert "**Receipts:** no site column" in text


def test_last_12_months_sets_the_window(tmp_path, monkeypatch, scope_result):
    client = _ingested(tmp_path, scope_result)
    at = _page(tmp_path, monkeypatch, client)
    at.button(key="scp::last12").click().run()
    frm, to = at.slider(key="scp::dates").value
    assert (to - frm).days == 364 and str(to) == "2026-01-31"
    w = _waterfall(at)
    assert any(s.startswith("Dates Feb 01, 2025 to Jan 31, 2026") for s in w["Step"])
    assert w["Lines removed"].str.len().gt(0).sum() >= 2  # status + dates


def test_live_filters_note_and_save(tmp_path, monkeypatch, scope_result):
    from gtpfit import tables
    client = _ingested(tmp_path, scope_result)
    at = _page(tmp_path, monkeypatch, client)
    before = _waterfall(at)["Lines left"].iat[-1]
    at.checkbox(key="scp::v::site::DC-East").uncheck().run()
    assert not at.exception, at.exception
    w = _waterfall(at)
    assert w["Lines left"].iat[-1] < before
    assert any(s.startswith("Site / warehouse") for s in w["Step"])
    # a note is required when rows are removed
    at.text_area(key="scp::note").input("").run()
    assert at.button(key="scp::save").disabled
    at.text_area(key="scp::note").input("DC-West only; excludes cancelled and returned").run()
    at.button(key="scp::save").click().run()
    assert not at.exception, at.exception
    assert any("Scope saved" in s.value for s in at.success)
    saved = yaml.safe_load((tmp_path / "ws" / client / "client.yaml").read_text(encoding="utf-8"))["scope"]
    assert saved["include"]["site"] == ["DC-West"] and saved["note"].startswith("DC-West only")
    assert (tmp_path / "ws" / client / "_scope" / "scope_summary.csv").exists()
    o = tables.load(client)["orders"]
    assert set(o["site"]) == {"DC-West"} and set(o["order_status"]) == {"Shipped"}
    # a new session opens on the saved scope
    again = _page(tmp_path, monkeypatch, client)
    assert not again.checkbox(key="scp::v::site::DC-East").value
    assert again.text_area(key="scp::note").value.startswith("DC-West only")


def test_reset_goes_back_to_defaults(tmp_path, monkeypatch, scope_result):
    client = _ingested(tmp_path, scope_result)
    at = _page(tmp_path, monkeypatch, client)
    at.checkbox(key="scp::v::site::DC-East").uncheck().run()
    at.button(key="scp::reset").click().run()
    assert at.checkbox(key="scp::v::site::DC-East").value
    assert not at.checkbox(key="scp::v::order_status::CANCELLED").value


def test_sample_client_shows_its_channel_column(tmp_path, monkeypatch, sample_result):
    client = _ingested(tmp_path, sample_result, "Sample")
    at = _page(tmp_path, monkeypatch, client)
    assert "**Channel**" in _text(at) and at.checkbox(key="scp::v::channel::Ecommerce").value
    assert at.button(key="scp::save")


def test_no_scope_columns(tmp_path, monkeypatch, sample_result):
    tables = {k: v for k, v in sample_result.tables.items()}
    tables["orders"] = tables["orders"].drop(columns=["channel"])
    out = tmp_path / "ws" / "Plain" / "_ingest"
    out.mkdir(parents=True)
    for t, df in tables.items():
        df.to_parquet(out / f"{t}.parquet")
    at = _page(tmp_path, monkeypatch, "Plain")
    assert "No scope columns found; using all rows." in _text(at)
    assert at.slider(key="scp::dates")
    assert not at.button(key="scp::save").disabled  # nothing removed: no note needed


def test_impact_tiles_and_coverage_recheck(tmp_path, monkeypatch, scope_result):
    client = _ingested(tmp_path, scope_result)
    at = _page(tmp_path, monkeypatch, client)
    labels = [m.label for m in at.metric]
    assert labels == ["Orders", "Lines per day (average)", "Peak day (lines)", "Units per line"]
    orders_before = at.metric[0].value
    assert not [w for w in at.warning if "peak season" in w.value or "Orders cover" in w.value]  # East: 13 months
    at.checkbox(key="scp::v::site::DC-East").uncheck().run()
    assert not at.exception, at.exception
    assert at.metric[0].value != orders_before and at.metric[0].delta.startswith("-")
    warns = " ".join(w.value for w in at.warning)
    assert "Orders cover" in warns and "No orders in Nov, Dec" in warns  # DC-West: Mar-Sep only
    assert "Busiest months in this data" in _text(at)


def test_skip_scope_saves_all_rows_on_purpose(tmp_path, monkeypatch, scope_result):
    from gtpfit import tables
    from gtpfit.scope import load_scope
    client = _ingested(tmp_path, scope_result)
    at = _page(tmp_path, monkeypatch, client)
    at.button(key="scp::skip").click().run()
    assert not at.exception, at.exception
    spec = load_scope(client)
    assert spec.skipped and spec.include == {} and spec.note
    assert len(tables.load(client)["orders"]) == len(scope_result.tables["orders"])


def test_use_suggested_note_after_editing(tmp_path, monkeypatch, scope_result):
    client = _ingested(tmp_path, scope_result)
    at = _page(tmp_path, monkeypatch, client)
    suggestion = at.text_area(key="scp::note").value
    at.text_area(key="scp::note").input("my own words").run()
    assert not at.exception, at.exception
    at.button(key="scp::suggest").click().run()          # used to fail: the box was already drawn
    assert not at.exception, at.exception
    assert at.text_area(key="scp::note").value == suggestion


def _keys(at):
    return {b.key for b in at.button}


def test_saved_scope_offers_quality_until_changed(tmp_path, monkeypatch, scope_result):
    client = _ingested(tmp_path, scope_result)
    at = _page(tmp_path, monkeypatch, client)
    assert {"scp::save", "scp::skip"} <= _keys(at) and "scp::to_quality" not in _keys(at)
    at.button(key="scp::save").click().run()
    assert not at.exception, at.exception
    assert any("Scope saved" in s.value for s in at.success)
    assert "scp::to_quality" in _keys(at) and not {"scp::save", "scp::skip"} & _keys(at)
    at.checkbox(key="scp::v::site::DC-West").uncheck().run()      # a change: save again
    assert "scp::save" in _keys(at) and "scp::to_quality" not in _keys(at)
    assert any("Unsaved changes" in c.value for c in at.caption)
    again = _page(tmp_path, monkeypatch, client)                   # a new session opens on the saved scope
    assert "scp::to_quality" in _keys(again)


def test_skip_ticks_every_value_and_offers_quality(tmp_path, monkeypatch, scope_result):
    client = _ingested(tmp_path, scope_result)
    at = _page(tmp_path, monkeypatch, client)
    assert not at.checkbox(key="scp::v::order_status::CANCELLED").value   # auto-excluded before the skip
    at.button(key="scp::skip").click().run()
    assert not at.exception, at.exception
    assert at.checkbox(key="scp::v::order_status::CANCELLED").value       # every row, as saved
    assert "scp::to_quality" in _keys(at) and "scp::save" not in _keys(at)
    assert _waterfall(at)["Lines left"].iat[-1] == _waterfall(at)["Lines left"].iat[0]
