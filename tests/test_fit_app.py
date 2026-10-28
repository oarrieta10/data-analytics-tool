"""The workbench Fit page (Streamlit AppTest, no browser): it waits for a saved (or skipped) scope; lists the
catalogue's containers with their sources and placeholders; shows per container and cell layout the share of
SKUs and lines that fit (matching fit_summary.json); looks up a SKU; re-runs when the cubing method changes; and
adds a container to the catalogue (a source is required)."""
import json
from pathlib import Path

import pytest
import yaml

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from gtpfit.ingest import ingest_folder, write_outputs  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
PAGE = """
from gtpfit.app.fit import page
page()
"""
CATALOG = """
containers:
  - id: tote
    name: Test tote
    system: HaiPick Climb
    mode: units
    inside: {l: 600, w: 400, h: 300, unit: mm}
    payload: {value: 30, unit: kg, confidence: low}
    source: test sheet
    confidence: high
"""


@pytest.fixture(scope="module")
def scope_result():
    return ingest_folder(FIX / "scope_client", strict=False)


def _ws(tmp_path, monkeypatch, result, client="Acme", scoped=True):
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    cat = tmp_path / "containers.yaml"
    cat.write_text(CATALOG, encoding="utf-8")
    monkeypatch.setenv("GTPFIT_CATALOG", str(cat))
    write_outputs(result, tmp_path / "ws" / client / "_ingest", client=client)
    if scoped:
        from gtpfit.scope import ScopeSpec, save_scope
        save_scope(client, ScopeSpec(skipped=True, note="All rows: no scope set."))
    return client, cat


def _page(client, **state):
    at = AppTest.from_string(PAGE, default_timeout=300)
    at.session_state["fit::client"] = client
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, at.exception
    return at


def _summary(tmp_path, client="Acme"):
    return json.loads((tmp_path / "ws" / client / "_fit" / "fit_summary.json").read_text(encoding="utf-8"))


def _text(at):
    return " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption) + " " + \
        " ".join(w.value for w in at.warning)


def test_waits_for_scope(tmp_path, monkeypatch, scope_result):
    client, _ = _ws(tmp_path, monkeypatch, scope_result, scoped=False)
    at = _page(client)
    assert any("Set the scope" in i.value for i in at.info)
    assert at.button(key="fit::to_scope")


def test_shares_match_the_summary_and_placeholders_are_named(tmp_path, monkeypatch, scope_result):
    client, _ = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    s = _summary(tmp_path)["containers"]["tote"]["layouts"]["whole"]
    shares = at.dataframe[0].value if at.dataframe else None
    assert shares is not None and "Test tote" in shares.to_string()
    assert f"{s['lines_share']:.0%}" in _text(at)
    assert "placeholder" in _text(at).lower()
    assert at.button(key="fit::to_slotting").disabled


def test_cubing_method_recomputes_and_is_saved(tmp_path, monkeypatch, scope_result):
    client, _ = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    at.selectbox(key="fit::p::cubing_method").set_value("liquid cube").run()
    assert not at.exception, at.exception
    assert _summary(tmp_path)["params"]["cubing_method"] == "liquid cube"
    saved = yaml.safe_load((tmp_path / "ws" / client / "client.yaml").read_text(encoding="utf-8"))
    assert saved["fit"]["cubing_method"] == "liquid cube"


def test_sku_lookup(tmp_path, monkeypatch, scope_result):
    client, _ = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    sku = at.selectbox(key="fit::lookup").options[0]
    at.selectbox(key="fit::lookup").set_value(sku).run()
    assert not at.exception, at.exception
    assert any(sku in m.value for m in at.markdown)


def test_adding_a_container_needs_a_source(tmp_path, monkeypatch, scope_result):
    client, cat = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    at.text_input(key="fit::new::id").input("big_tote").run()
    at.text_input(key="fit::new::name").input("Big tote").run()
    at.text_input(key="fit::new::system").input("HaiPick System 3").run()
    at.button(key="fit::new::save").click().run()
    assert any("source" in e.value.lower() for e in at.error)
    at.text_input(key="fit::new::source").input("HAI spec sheet").run()
    at.button(key="fit::new::save").click().run()
    assert not at.exception, at.exception
    ids = [c["id"] for c in yaml.safe_load(cat.read_text(encoding="utf-8"))["containers"]]
    assert ids == ["tote", "big_tote"]
