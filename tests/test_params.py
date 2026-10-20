"""IngestParams metadata: every threshold has a block, label, three-part tooltip, unit, source and confidence,
so the workbench can draw it and explain it; the assortment presets set exactly the three unit bands."""
import dataclasses

import pytest

from gtpfit.ingest import IngestParams
from gtpfit.ingest.params import (ASSORTMENT_HELP, ASSORTMENTS, BLOCKS, DEFAULT_ASSORTMENT, UNITS,
                                  assortment_bands, assortment_tooltip, format_value, meta, tooltip)
from gtpfit.lines import LINE_DEFINITION_HELP

FIELDS = dataclasses.fields(IngestParams)
BANDS = ("unit_longest_side_in", "case_longest_side_in", "density_lb_ft3")
REQUIRED = ("block", "label", "what", "why", "effect", "unit", "source", "confidence")


@pytest.mark.parametrize("f", FIELDS, ids=lambda f: f.name)
def test_every_field_has_metadata(f):
    m = meta(f.name)
    for k in REQUIRED:
        assert isinstance(m.get(k), str) and m[k].strip(), (f.name, k)
    assert m["block"] in BLOCKS and m["unit"] in UNITS
    assert m["confidence"] in ("low", "medium", "high")
    if m["unit"] in ("days", "share", "x", "ratio"):
        assert m["lo"] is not None and m["hi"] is not None and m["step"], f.name


@pytest.mark.parametrize("f", FIELDS, ids=lambda f: f.name)
def test_tooltip_states_the_actual_default(f):
    m = meta(f.name)
    shown = (not f.default) if m.get("invert") else f.default
    t = tooltip(f.name)
    assert f"Default {format_value(shown, m['unit'])}:" in t
    assert t.startswith(m["what"]) and t.endswith(m["effect"])


def test_texts_are_general():
    texts = [tooltip(f.name) + meta(f.name)["source"] for f in FIELDS] + [assortment_tooltip(), LINE_DEFINITION_HELP]
    for t in texts:
        low = t.lower()
        assert "client 1" not in low and "client 2" not in low and "real client" not in low and "2026" not in low
    assert all(ASSORTMENT_HELP.values())


def test_assortment_presets_set_the_three_bands():
    assert set(ASSORTMENTS) == {"consumer goods", "industrial", "grocery"}
    d = dataclasses.asdict(IngestParams())
    assert {k: d[k] for k in BANDS} == ASSORTMENTS[DEFAULT_ASSORTMENT]  # the defaults are the consumer preset
    assert {f.name for f in FIELDS if meta(f.name).get("via") == "assortment"} == set(BANDS)
    for name, preset in ASSORTMENTS.items():
        assert set(preset) == set(BANDS) and assortment_bands(name) == preset
        IngestParams(**{**d, **preset})
    assert ASSORTMENTS["industrial"]["density_lb_ft3"][1] > ASSORTMENTS["consumer goods"]["density_lb_ft3"][1]
    with pytest.raises(ValueError):
        assortment_bands("furniture")
