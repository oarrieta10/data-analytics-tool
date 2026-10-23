"""The look of the workbench: the HAI robot loading animation (served locally, installed once per page, shown in
the middle of the screen only for runs longer than a moment) and the Hai Blue accent colour.  JavaScript and
theming do not run under AppTest; the browser check is done by hand (see app/static/README.md)."""
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "src" / "gtpfit" / "app"
STATIC = APP / "static"


def test_loader_files_are_served_locally():
    js = (STATIC / "hai-loader" / "hai-loader.js").read_bytes().decode("utf-8")  # UTF-8, not cp1252
    imports = re.findall(r"""^import .* from ['"]([^'"]+)['"]""", js, flags=re.M)
    assert imports == ["../three/three.module.min.js"]  # no bare 'three': nothing to resolve online
    three = STATIC / "three" / "three.module.min.js"
    assert three.exists() and (STATIC / "three" / "LICENSE").exists()
    assert set(re.findall(r'from"([^"]+)"', three.read_text(encoding="utf-8"))) == {"./three.core.min.js"}
    assert (STATIC / "three" / "three.core.min.js").exists()
    assert "customElements.define('hai-loader'" in js


def test_static_serving_is_on():
    assert "--server.enableStaticServing" in (APP / "__main__.py").read_text(encoding="utf-8")


def test_loader_html():
    from gtpfit.app import loader
    h = loader.LOADER_HTML
    assert "/app/static/hai-loader/hai-loader.js" in h
    assert "__haiLoaderInstalled" in h                       # installed once per tab
    assert "data-test-script-state" in h and "rerunRequested" in h
    assert str(loader.SHOW_AFTER_MS) in h
    assert "stStatusWidgetRunningIcon" in h and "stStatusWidgetRunningManIcon" in h   # the running man goes
    assert "inset: 0" in h                                    # full screen: the robot sits in the middle


def test_workbench_installs_it(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    at = AppTest.from_string("from gtpfit.app import loader\nloader.install()\n")
    at.run()
    assert not at.exception, at.exception


# ---- accent colour: Hai Blue (Hai Robotics design system) ------------------------------------------------

def test_accent_is_hai_blue():
    from gtpfit.app import theme
    assert theme.HAI_BLUE == "#2E31FA" and theme.HAI_BLUE_4 == "#808AFF"
    flags = theme.streamlit_flags()
    pairs = dict(zip(flags[::2], flags[1::2]))
    assert pairs["--theme.light.primaryColor"] == pairs["--theme.light.linkColor"] == "#2E31FA"
    assert pairs["--theme.dark.primaryColor"] == pairs["--theme.dark.linkColor"] == "#808AFF"
    assert "theme.streamlit_flags()" in (APP / "__main__.py").read_text(encoding="utf-8")


def test_theme_flags_are_known_to_streamlit():
    pytest.importorskip("streamlit")
    from streamlit import config
    from gtpfit.app import theme
    config.get_config_options()
    for flag in theme.streamlit_flags()[::2]:
        assert flag[2:] in config._config_options, flag


# ---- the Hai Robotics logo, top left (Hai Robotics design system) ----------------------------------------

def test_logo_files_are_the_design_system_artwork():
    import hashlib
    brand = STATIC / "brand"
    sha = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in brand.glob("*.svg")}
    assert sha == {  # exactly as downloaded from the design system's Logos group: never redrawn or recoloured
        "hai-robotics-primary.svg": "356374f07e4a754e595fa39f026e9b2263a90c6030992c43d345d9ce7cfb77cf",
        "hai-robotics-primary-reverse.svg": "a6814bbe84d255b6ada2c67a64d19c7019ad449cac2630cd47012c35afcd3667"}
    for p in brand.glob("*.svg"):
        svg = p.read_text(encoding="utf-8").lower()
        assert "<script" not in svg and "href" not in svg and "url(" not in svg


def test_logo_follows_the_theme():
    from gtpfit.app import theme
    assert theme.logo_file("light").name == "hai-robotics-primary.svg"
    assert theme.logo_file("dark").name == "hai-robotics-primary-reverse.svg"   # white on dark grounds
    assert theme.logo_file(None).name == "hai-robotics-primary.svg"


def test_workbench_shows_the_logo(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    at = AppTest.from_string("from gtpfit.app import theme\ntheme.logo()\n")
    at.run()
    assert not at.exception, at.exception
    assert "theme.logo()" in (APP / "workbench.py").read_text(encoding="utf-8")
