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
