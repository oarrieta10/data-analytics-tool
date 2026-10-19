"""Smoke tests for the workbench pages (Streamlit AppTest, no browser)."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

SYNTH = """
from gtpfit.app import synth_page
synth_page.page()
"""


def _app():
    return AppTest.from_string(SYNTH, default_timeout=120).run()


def _estimate_text(at):
    return next(m.value for m in at.markdown if "order lines" in m.value)


def test_synth_page_renders():
    at = _app()
    assert not at.exception, at.exception


def test_slider_changes_estimate():
    at = _app()
    before = _estimate_text(at)
    at.number_input(key="syn::E-commerce orders::ecom_orders_per_day").set_value(4000).run()
    assert not at.exception
    assert _estimate_text(at) != before


def test_preset_loads():
    at = _app()
    at.selectbox(key="syn::preset").select("messy_large").run()
    assert not at.exception
    assert at.number_input(key="syn::Catalogue::n_skus").value == 20000


def test_apply_bad_yaml_shows_error():
    at = _app()
    at.text_area(key="syn::yaml").input("not_a_setting: 3").run()
    at.button(key="syn::apply_yaml").click().run()
    assert not at.exception
    assert any("not_a_setting" in e.value for e in at.error)


def test_generate_tiny_dataset(tmp_path):
    at = _app()
    at.number_input(key="syn::Catalogue::n_skus").set_value(200).run()
    at.number_input(key="syn::E-commerce orders::ecom_orders_per_day").set_value(40).run()
    at.slider(key="syn::Basics::days").set_value(30).run()
    at.text_input(key="syn::out").input(str(tmp_path / "tiny")).run()
    at.button(key="syn::generate").click().run()
    assert not at.exception, at.exception
    assert (tmp_path / "tiny" / "orders.csv").exists()


def test_peak_toggle_reveals_slider():
    at = _app()
    base = "syn::Peak basket shape::ecom_single_line_share"
    assert not [s for s in at.slider if s.key == base]
    at.toggle(key=base + "::on").set_value(True).run()
    assert not at.exception
    assert at.slider(key=base).value == at.slider(key="syn::E-commerce orders::ecom_single_line_share").value
