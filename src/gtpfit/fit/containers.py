"""containers.py — the container catalogue: what the design stage fits SKUs into, with where each spec came from.

    load_catalog(path=None) -> [Container]      read and check src/gtpfit/catalog/containers.yaml (or path)
    save_container(spec: dict, path=None)       add or replace one container (by id), source required

Sizes are converted to inches and weights to pounds on load; the file keeps the units it was written in.  A
container needs an id, name, system, mode, inside sizes, a payload, source and confidence; a payload marked
confidence: low is listed in Container.placeholders, so results can say which values are not confirmed.  Where
the spec limits container + goods instead (`gross_limit`), the payload is that limit less the tare.  A tapered
(nestable) tote is fitted to its bottom inside size, the narrowest; the opening is kept as `inside_top`.
Layouts are the whole container plus any divider cells (name, cells, splits along length and width).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CATALOG = Path(__file__).resolve().parents[1] / "catalog" / "containers.yaml"


def catalog_path() -> Path:
    """The catalogue file: GTPFIT_CATALOG when set (tests, a consultant's own copy), else the shipped one."""
    import os
    return Path(os.environ["GTPFIT_CATALOG"]) if os.environ.get("GTPFIT_CATALOG") else CATALOG
TO_IN = {"in": 1.0, "mm": 1 / 25.4, "cm": 1 / 2.54}
TO_LB = {"lb": 1.0, "kg": 1 / 0.45359237}
MODES = ("units", "case_on_tray")
REQUIRED = ("id", "name", "system", "mode", "inside", "source", "confidence")   # and payload, or gross_limit + tare


@dataclass(frozen=True)
class Layout:
    name: str        # "whole", "1/2", ...
    cells: int       # cells per container
    l_split: int     # the length is divided into this many
    w_split: int     # the width into this many


@dataclass
class Container:
    id: str
    name: str
    system: str
    supplier: str
    mode: str
    inside_in: tuple                      # (length, width, height) in inches
    payload_lb: float
    layouts: list = field(default_factory=lambda: [Layout("whole", 1, 1, 1)])
    source: str = ""
    confidence: str = ""
    carton_range_in: tuple | None = None  # ((lo, hi) length, width, height) for case_on_tray
    cell_utilisation: float | None = None
    tare_lb: float | None = None
    inside_top_in: tuple | None = None    # (length, width) at the opening of a tapered tote; recorded, not fitted
    status: str = ""
    placeholders: list = field(default_factory=list)

    @property
    def volume_in3(self) -> float:
        l, w, h = self.inside_in
        return l * w * h


def _length(v: dict, key: str, where: str) -> float:
    unit = v.get("unit", "in")
    if unit not in TO_IN:
        raise ValueError(f"{where}: unknown length unit {unit!r} (use mm, cm or in).")
    return float(v[key]) * TO_IN[unit]


def _weight(v: dict, where: str) -> float:
    unit = v.get("unit", "lb")
    if unit not in TO_LB:
        raise ValueError(f"{where}: unknown weight unit {unit!r} (use kg or lb).")
    return float(v["value"]) * TO_LB[unit]


def parse(spec: dict) -> Container:
    where = f"container {spec.get('id', '?')}"
    missing = [k for k in REQUIRED if not spec.get(k)]
    if missing:
        raise ValueError(f"{where}: missing {', '.join(missing)}.")
    if spec["mode"] not in MODES:
        raise ValueError(f"{where}: mode must be one of {MODES}.")
    ins = spec["inside"]
    inside = tuple(_length(ins, k, where) for k in ("l", "w", "h"))
    if min(inside) <= 0:
        raise ValueError(f"{where}: inside sizes must be positive.")
    tare = _weight(spec["tare"], where) if spec.get("tare") else None
    if spec.get("payload"):
        pay = spec["payload"]
        payload = _weight(pay, where)
    elif spec.get("gross_limit"):
        # the spec limits container + goods: what it carries is that limit less the container's own weight
        if tare is None:
            raise ValueError(f"{where}: a gross_limit needs the tare to give the payload.")
        pay = spec["gross_limit"]
        payload = _weight(pay, where) - tare
    else:
        raise ValueError(f"{where}: missing payload (or gross_limit with tare).")
    top = spec.get("inside_top")
    layouts = [Layout("whole", 1, 1, 1)] + [Layout(str(d["name"]), int(d["cells"]), int(d.get("l_split", 1)),
                                                   int(d.get("w_split", 1))) for d in spec.get("dividers") or []]
    rng = None
    if spec["mode"] == "case_on_tray":
        cr = spec.get("carton_range")
        if not cr:
            raise ValueError(f"{where}: a case_on_tray container needs carton_range.")
        f = TO_IN[cr.get("unit", "in")]
        rng = tuple((float(cr[k][0]) * f, float(cr[k][1]) * f) for k in ("l", "w", "h"))
    return Container(id=str(spec["id"]), name=str(spec["name"]), system=str(spec["system"]),
                     supplier=str(spec.get("supplier", "")), mode=spec["mode"], inside_in=inside,
                     payload_lb=payload, layouts=layouts, source=str(spec["source"]),
                     confidence=str(spec["confidence"]), carton_range_in=rng,
                     cell_utilisation=spec.get("cell_utilisation"), tare_lb=tare,
                     inside_top_in=tuple(_length(top, k, where) for k in ("l", "w")) if top else None,
                     status=str(spec.get("status", "")),
                     placeholders=["payload"] if str(pay.get("confidence", "")).lower() == "low" else [])


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_catalog(path=None) -> list[Container]:
    data = _read(Path(path) if path else catalog_path())
    cats = [parse(s) for s in data.get("containers") or []]
    ids = [c.id for c in cats]
    if len(ids) != len(set(ids)):
        raise ValueError("container ids must be unique.")
    return cats


def save_container(spec: dict, path=None) -> Container:
    """Add a container, or replace the one with the same id; checked before anything is written."""
    c = parse(spec)
    p = Path(path) if path else catalog_path()
    data = _read(p) if p.exists() else {}
    rows = [s for s in data.get("containers") or [] if str(s.get("id")) != c.id] + [spec]
    data["containers"] = rows
    header = "".join(line + "\n" for line in p.read_text(encoding="utf-8").splitlines()
                     if line.startswith("#")) if p.exists() else ""
    p.write_text(header + "\n" + yaml.safe_dump({"containers": rows}, sort_keys=False, allow_unicode=True),
                 encoding="utf-8")
    return c
