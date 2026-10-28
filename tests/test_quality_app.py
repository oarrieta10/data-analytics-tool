"""The workbench Quality page (Streamlit AppTest, no browser): it waits for a saved (or skipped) scope, shows
score tiles that match quality_summary.json, the problems with plain words, the items to measure, and
recomputes when the outlier strictness changes."""
import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from gtpfit.ingest import ingest_folder, write_outputs  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
PAGE = """
from gtpfit.app.quality import page
page()
"""


@pytest.fixture(scope="module")
def scope_result():
    return ingest_folder(FIX / "scope_client", strict=False)


def _ws(tmp_path, monkeypatch, result, client="Acme", scoped=True, inventory=True):
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    out = write_outputs(result, tmp_path / "ws" / client / "_ingest", client=client)
    if not inventory:
        (out / "inventory.parquet").unlink()
    if scoped:
        from gtpfit.scope import ScopeSpec, save_scope
        save_scope(client, ScopeSpec(skipped=True, note="All rows: no scope set."))
    return client


def _page(client, **state):
    at = AppTest.from_string(PAGE, default_timeout=180)
    at.session_state["qly::client"] = client
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, at.exception
    return at


def _summary(tmp_path, client="Acme"):
    return json.loads((tmp_path / "ws" / client / "_quality" / "quality_summary.json").read_text(encoding="utf-8"))


def test_waits_for_scope(tmp_path, monkeypatch, scope_result):
    client = _ws(tmp_path, monkeypatch, scope_result, scoped=False)
    at = _page(client)
    assert any("Set the scope" in i.value for i in at.info)
    assert at.button(key="qly::to_scope") and not at.metric


def test_tiles_match_the_summary_file(tmp_path, monkeypatch, scope_result):
    client = _ws(tmp_path, monkeypatch, scope_result)
    at = _page(client)
    s = _summary(tmp_path)
    tiles = {m.label: m.value for m in at.metric}
    assert list(tiles) == ["Lines", "Units", "On-hand", "SKUs"]
    for basis, label in (("lines", "Lines"), ("units", "Units"), ("on_hand", "On-hand"), ("skus", "SKUs")):
        assert tiles[label] == f"{s['score']['unit_fit'][basis]['share']:.0%}"
    text = " ".join(m.value for m in at.markdown)
    assert "of the picking work is described by usable dimensions and weight" in text
    assert not at.button(key="qly::to_backfill").disabled
    assert (tmp_path / "ws" / client / "_quality" / "items_to_measure.csv").exists()


def test_without_inventory(tmp_path, monkeypatch, scope_result):
    client = _ws(tmp_path, monkeypatch, scope_result, inventory=False)
    at = _page(client)
    assert [m.label for m in at.metric] == ["Lines", "Units", "SKUs"]
    assert not _summary(tmp_path)["has_inventory"]


def test_strictness_recomputes(tmp_path, monkeypatch, scope_result):
    client = _ws(tmp_path, monkeypatch, scope_result)

    def outliers():
        return sum(p["skus"] for p in _summary(tmp_path)["problems"] if p["code"].startswith("outlier"))
    at = _page(client, **{"qly::p::outlier_mad_k": 10.0})
    loose = outliers()
    at.number_input(key="qly::p::outlier_mad_k").set_value(2.0).run()
    assert not at.exception, at.exception
    assert outliers() > loose
    import yaml
    saved = yaml.safe_load((tmp_path / "ws" / client / "client.yaml").read_text(encoding="utf-8"))
    assert saved["quality"]["outlier_mad_k"] == 2.0  # saved for the client
