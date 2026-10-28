"""tessellate.py — how many identical boxes (units or cases) fit a space, three ways; vectorised over SKUs.

    units_by_tessellation(items, box, upright=False) -> units per SKU
    units_by_liquid(unit_cube, box_volume, utilisation) -> units per SKU
    units_by_weight(unit_weight, cap) -> units per SKU

Tessellation, as in the consultant's earlier storage-sizing tool: for each orientation of the item (all 6, or
the 2 that keep its height vertical when upright), pack a block of floor(box side / item side) along each side,
then fill three leftover spaces, each with its own best orientation (the leftover length x full width x full
height; the used length x leftover width x full height; the used length x used width x leftover height), and
keep the best total.  It is a fast lower bound, not an optimal packing.  Liquid cube treats the units as
fluid: floor(volume x utilisation / unit cube).  Weight: floor(cap / unit weight).  Sizes in inches, weights in
pounds; a missing or non-positive size or weight fits nothing.
"""
from __future__ import annotations

import numpy as np

ALL = ((0, 1, 2), (0, 2, 1), (1, 2, 0), (1, 0, 2), (2, 0, 1), (2, 1, 0))  # item side placed along box L, W, H
UPRIGHT = ((0, 1, 2), (1, 0, 2))                                        # the item's height stays vertical
_EPS = 1e-9  # a side that exactly fills the box still counts as fitting


def _fits(space: np.ndarray, d: np.ndarray) -> np.ndarray:
    """floor(space / d) per side, 0 where d is unusable; space and d are (n, 3)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.floor(space / d + _EPS)
    return np.where((d > 0) & np.isfinite(q) & (q > 0), q, 0.0)


def _best_block(items: np.ndarray, space: np.ndarray, orients) -> np.ndarray:
    best = np.zeros(len(items))
    for o in orients:
        best = np.maximum(best, _fits(space, items[:, o]).prod(axis=1))
    return best


def units_by_tessellation(items, box, upright: bool = False) -> np.ndarray:
    """items: (n, 3) length, width, height; box: (3,) inside length, width, height."""
    items = np.asarray(items, float).reshape(-1, 3)
    box = np.broadcast_to(np.asarray(box, float), items.shape)
    ok = np.isfinite(items).all(axis=1) & (items > 0).all(axis=1)
    items = np.where(ok[:, None], items, np.nan)
    orients = UPRIGHT if upright else ALL
    best = np.zeros(len(items))
    for o in orients:
        d = items[:, o]
        q = _fits(box, d)
        used = q * np.nan_to_num(d)
        a = q.prod(axis=1)
        b = _best_block(items, np.column_stack([box[:, 0] - used[:, 0], box[:, 1], box[:, 2]]), orients)
        c = _best_block(items, np.column_stack([used[:, 0], box[:, 1] - used[:, 1], box[:, 2]]), orients)
        h = _best_block(items, np.column_stack([used[:, 0], used[:, 1], box[:, 2] - used[:, 2]]), orients)
        best = np.maximum(best, a + b + c + h)
    return np.where(ok, best, 0.0).astype(int)


def units_by_liquid(unit_cube, box_volume: float, utilisation: float) -> np.ndarray:
    v = np.asarray(unit_cube, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        n = np.floor(box_volume * utilisation / v + _EPS)
    return np.where((v > 0) & np.isfinite(n), n, 0.0).astype(int)


def units_by_weight(unit_weight, cap: float) -> np.ndarray:
    w = np.asarray(unit_weight, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        n = np.floor(cap / w + _EPS)
    return np.where((w > 0) & np.isfinite(n), n, 0.0).astype(int)
