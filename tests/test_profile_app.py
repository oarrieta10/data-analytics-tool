"""The workbench Profile page (Streamlit AppTest, no browser): it waits for a saved (or skipped) scope; without
service targets it shows the percentile-day fallback; with targets saved for the client it shows the design
capacity and the target that sets it; the what-if capacity recomputes the KPIs; settings re-run and are saved;
the profile sections render and _profile/ is written."""
import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from gtpfit.ingest import ingest_folder, write_outputs  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
PAGE = """
from gtpfit.app.profile import page
page()
"""


@pytest.fixture(scope="module")
def scope_result():
    return ingest_folder(FIX / "scope_client", strict=False)


def _ws(tmp_path, monkeypatch, result, client="Acme", scoped=True):
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    write_outputs(result, tmp_path / "ws" / client / "_ingest", client=client)
    if scoped:
        from gtpfit.scope import ScopeSpec, save_scope
        save_scope(client, ScopeSpec(skipped=True, note="All rows: no scope set."))
    return client


def _page(client, **state):
    at = AppTest.from_string(PAGE, default_timeout=300)
    at.session_state["pf::client"] = client
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, at.exception
    return at


def _summary(tmp_path, client="Acme"):
    return json.loads((tmp_path / "ws" / client / "_profile" / "profile_summary.json").read_text(encoding="utf-8"))


def _text(at):
    return " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)


def _metric(at, label):
    return next(m for m in at.metric if m.label == label)


def test_waits_for_scope(tmp_path, monkeypatch, scope_result):
    client = _ws(tmp_path, monkeypatch, scope_result, scoped=False)
    at = _page(client)
    assert any("Set the scope" in i.value for i in at.info)
    assert at.button(key="pf::to_scope") and not at.metric


def test_without_targets_the_percentile_day_is_the_fallback(tmp_path, monkeypatch, scope_result):
    client = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    s = _summary(tmp_path)
    assert s["design"]["method"] == "fallback_percentile"
    assert _metric(at, "Design capacity").value == f"{s['design']['capacity']:,}"
    assert "95th-percentile day" in _text(at)
    assert not at.button(key="pf::to_fit").disabled
    for f in ("daily.parquet", "abc.parquet", "profile_summary.json"):
        assert (tmp_path / "ws" / client / "_profile" / f).exists()


def test_saved_targets_set_the_design_capacity(tmp_path, monkeypatch, scope_result):
    from gtpfit.profile import load_sla, save_sla
    client = _ws(tmp_path, monkeypatch, scope_result)
    chans = sorted(scope_result.tables["orders"]["channel"].dropna().unique())
    save_sla(client, {chans[0]: {"within": [(0, 0.99)]}})
    at = _page(client)
    s = _summary(tmp_path)
    assert s["design"]["method"] == "sla" and s["design"]["binding"]
    assert _metric(at, "Design capacity").value == f"{s['design']['capacity']:,}"
    assert f"Set by {s['design']['binding'][0]}" in _text(at)
    assert load_sla(client) == {chans[0]: {"within": [(0, 0.99)]}}  # the page keeps what was saved


def test_what_if_capacity(tmp_path, monkeypatch, scope_result):
    client = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    at.number_input(key="pf::whatif").set_value(1).run()
    assert not at.exception, at.exception
    assert "At 1 a day" in _text(at)
    assert int(_metric(at, "What-if days with backlog").value.replace(",", "")) > 0


def test_setting_recomputes_and_is_saved(tmp_path, monkeypatch, scope_result):
    import yaml
    client = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    lines = _summary(tmp_path)["design"]["capacity"]
    at.selectbox(key="pf::p::demand_unit").set_value("units").run()
    assert not at.exception, at.exception
    s = _summary(tmp_path)
    assert s["demand_unit"] == "units" and s["design"]["capacity"] != lines
    saved = yaml.safe_load((tmp_path / "ws" / client / "client.yaml").read_text(encoding="utf-8"))
    assert saved["profile"]["demand_unit"] == "units"


def test_profile_sections_render(tmp_path, monkeypatch, scope_result):
    client = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    heads = [h.value for h in at.subheader]
    for h in ("Design capacity", "Volumes and seasonality", "Order shape", "Hourly profile", "ABC velocity",
              "Stock", "Inbound"):
        assert h in heads, h
