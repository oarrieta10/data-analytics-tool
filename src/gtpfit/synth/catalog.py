"""catalog.py — the product taxonomy and the *true* item master.

Each product type has its own size, density and seasonality, so a SKU's
description, category and sub-category genuinely predict its dimensions.
That signal is what the KNN backfill will learn from later; the ground truth
written here is what it is scored against.

All dimensions are inches, weights pounds, sorted so L >= W >= H.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# category: (popularity weight, seasonal amplitude, seasonal peak day-of-year,
#            density lb/in^3, product types)
# product type: (name, median L, W, H, relative popularity, variants)
_APPAREL_SIZES = ["XS", "S", "M", "L", "XL", "XXL"]
_COLOURS = ["Black", "Navy", "Grey", "White", "Red", "Olive", "Blue", "Tan"]

TAXONOMY: dict[str, dict] = {
    "Apparel": dict(w=1.6, amp=0.15, peak=320, dens=0.0035, types=[
        ("T-Shirt", 11, 9, 1.0, 1.4, "size"), ("Jeans", 14, 11, 2.0, 0.8, "size"),
        ("Hoodie", 14, 12, 3.0, 0.8, "size"), ("Socks 3-Pack", 9, 5, 1.5, 1.0, "size"),
        ("Rain Jacket", 18, 14, 4.0, 0.4, "size")]),
    "Footwear": dict(w=0.8, amp=0.10, peak=240, dens=0.006, types=[
        ("Sneakers", 13, 8, 5.0, 1.2, "shoe"), ("Sandals", 12, 5, 3.0, 0.6, "shoe"),
        ("Winter Boots", 14, 10, 6.0, 0.4, "shoe")]),
    "Electronics": dict(w=1.2, amp=0.25, peak=345, dens=0.012, types=[
        ("Phone Case", 7, 4, 0.8, 1.5, "colour"), ("Wireless Earbuds", 5, 4, 2.0, 1.0, "colour"),
        ("USB-C Cable", 6, 4, 1.0, 1.4, "length"), ("Bluetooth Speaker", 8, 5, 4.0, 0.5, "colour"),
        ("Smart Watch", 5, 4, 3.0, 0.4, "colour"), ("Tablet", 11, 8, 2.0, 0.2, "storage")]),
    "Health & Beauty": dict(w=1.3, amp=0.08, peak=345, dens=0.030, types=[
        ("Shampoo", 8, 3, 2.0, 1.2, "volume"), ("Lipstick", 3, 1, 1.0, 1.0, "colour"),
        ("Vitamins", 5, 3, 3.0, 1.1, "count"), ("Face Cream", 4, 3, 3.0, 0.8, "volume"),
        ("Hair Dryer", 12, 9, 5.0, 0.2, "colour")]),
    "Household": dict(w=1.0, amp=0.12, peak=330, dens=0.012, types=[
        ("Kitchen Towels", 10, 8, 3.0, 0.8, "colour"), ("Food Storage Set", 12, 10, 7.0, 0.5, "count"),
        ("Scented Candle", 4, 4, 4.0, 0.9, "scent"), ("Picture Frame", 12, 10, 1.5, 0.5, "frame"),
        ("Bed Pillow", 20, 14, 6.0, 0.3, "size2")]),
    "Books & Games": dict(w=0.7, amp=0.30, peak=350, dens=0.024, types=[
        ("Paperback", 8, 5, 1.0, 1.4, "none"), ("Hardcover", 10, 7, 1.5, 0.7, "none"),
        ("Board Game", 11, 11, 3.0, 0.4, "none")]),
    "Tools & Hardware": dict(w=0.6, amp=0.20, peak=150, dens=0.055, types=[
        ("Screwdriver Set", 9, 4, 2.0, 0.6, "count"), ("Cordless Drill", 12, 10, 4.0, 0.3, "volt"),
        ("Fastener Box", 5, 4, 2.0, 1.0, "count"), ("Tape Measure", 3, 3, 2.0, 0.7, "length")]),
    "Toys": dict(w=0.8, amp=0.60, peak=350, dens=0.006, types=[
        ("Plush Toy", 10, 8, 6.0, 0.9, "colour"), ("Building Set", 15, 10, 3.0, 0.6, "count"),
        ("Action Figure", 10, 6, 2.0, 0.8, "none")]),
    "Grocery": dict(w=0.9, amp=0.10, peak=340, dens=0.032, types=[
        ("Coffee Beans", 8, 5, 3.0, 1.2, "weight"), ("Snack Bars 12-Pack", 9, 6, 3.0, 1.0, "flavour"),
        ("Canned Soup", 4.5, 3, 3.0, 0.8, "flavour"), ("Sparkling Water 24-Pack", 16, 11, 9.0, 0.3, "flavour")]),
    "Sporting Goods": dict(w=0.6, amp=0.35, peak=170, dens=0.010, types=[
        ("Yoga Mat", 26, 6, 6.0, 0.5, "colour"), ("Water Bottle", 10, 3, 3.0, 1.2, "colour"),
        ("Dumbbell", 12, 4, 4.0, 0.3, "weightlb"), ("Tennis Balls 3-Can", 8, 3, 3.0, 0.8, "none")]),
}

# product types whose density is far from the category average
_DENSITY_OVERRIDE = {"Dumbbell": 0.20, "Sparkling Water 24-Pack": 0.022, "Canned Soup": 0.040,
                     "Bed Pillow": 0.002, "Plush Toy": 0.003, "Yoga Mat": 0.006,
                     "Paperback": 0.020, "Cordless Drill": 0.030}

_BRANDS = ["Northpeak", "Bluefern", "Oakline", "Corvex", "Lumina", "Harbor & Pine",
           "Vantis", "Kestrel", "Marlowe", "Solstice", "Ironwood", "Tidewater",
           "Quill", "Brightside", "Atlas Home", "Fieldcraft"]
_ADJ = ["Classic", "Essential", "Pro", "Everyday", "Premium", "Compact", "Ultra", "Original"]
_VARIANTS = {
    "size": _APPAREL_SIZES, "shoe": [str(s) for s in range(6, 13)], "colour": _COLOURS,
    "length": ["3ft", "6ft", "10ft"], "storage": ["64GB", "128GB", "256GB"],
    "volume": ["100ml", "250ml", "500ml"], "count": ["30ct", "60ct", "120ct"],
    "scent": ["Vanilla", "Cedar", "Citrus", "Lavender"], "frame": ["5x7", "8x10", "11x14"],
    "size2": ["Standard", "Queen", "King"], "volt": ["12V", "18V", "20V"],
    "weight": ["12oz", "2lb", "5lb"], "flavour": ["Original", "Lemon", "Berry", "Chocolate"],
    "weightlb": ["5lb", "10lb", "15lb", "25lb"], "none": [""],
}
_STD_PACKS = np.array([1, 2, 3, 4, 6, 8, 10, 12, 16, 18, 20, 24, 30, 36, 48, 50, 60, 72, 96, 100, 144])


def _variant_scale(kind: str, idx: int, n: int) -> float:
    """Bigger variants are bigger: sizes, capacities and weights scale dims."""
    if kind in ("size", "shoe", "volume", "count", "frame", "size2", "weight", "weightlb", "length"):
        return 0.85 + 0.35 * idx / max(n - 1, 1)
    return 1.0


def _grid(n: int) -> tuple[int, int, int]:
    """Most cube-like a x b x c arrangement of n units (a >= b >= c)."""
    best, score = (n, 1, 1), 1e18
    for a in range(1, n + 1):
        if n % a:
            continue
        m = n // a
        for b in range(1, m + 1):
            if m % b:
                continue
            c = m // b
            s = a * b + b * c + a * c
            if s < score:
                best, score = tuple(sorted((a, b, c), reverse=True)), s
    return best


def build_true_item_master(p, rng: np.random.Generator) -> pd.DataFrame:
    """One row per SKU with true attributes, popularity and lifecycle."""
    rows = []
    cats = list(TAXONOMY)
    cat_w = np.array([TAXONOMY[c]["w"] for c in cats], float)
    cat_of = rng.choice(len(cats), size=p.n_skus, p=cat_w / cat_w.sum())
    sku_seen: set[str] = set()
    for i in range(p.n_skus):
        cat = cats[cat_of[i]]
        spec = TAXONOMY[cat]
        types = spec["types"]
        tw = np.array([t[4] for t in types])
        name, L0, W0, H0, tpop, vkind = types[rng.choice(len(types), p=tw / tw.sum())]
        variants = _VARIANTS[vkind]
        vi = int(rng.integers(len(variants)))
        scale = _variant_scale(vkind, vi, len(variants))
        dims = np.array([L0, W0, H0]) * scale * rng.lognormal(0, 0.18, 3)
        dims = np.sort(np.round(np.maximum(dims, 0.3), 2))[::-1]
        dens = _DENSITY_OVERRIDE.get(name, spec["dens"]) * rng.lognormal(0, 0.25)
        weight = round(float(max(dims.prod() * dens, 0.02)), 3)

        # case pack: aim for a ~1,000-2,600 in^3 case under ~40 lb
        unit_cube = dims.prod()
        target = rng.uniform(1000, 2600) / unit_cube
        target = min(target, 40 / max(weight, 0.01))
        fits = _STD_PACKS[_STD_PACKS <= max(target, 1)]
        qty = int(fits[-1])
        a, b, c = _grid(qty)  # a >= b >= c: stack the most units along the thinnest side
        case = np.sort(np.round(np.array([c * dims[0], b * dims[1], a * dims[2]]) + 0.5, 2))[::-1]
        case_w = round(qty * weight * 1.03 + 0.4 * (case.prod() / 1728) ** (2 / 3) + 0.3, 2)

        brand = _BRANDS[int(rng.integers(len(_BRANDS)))]
        adj = _ADJ[int(rng.integers(len(_ADJ)))]
        desc = " ".join(x for x in (brand, adj, name, variants[vi]) if x)
        prefix = "".join(w[0] for w in cat.replace("&", "").split())[:2].upper()
        while True:
            sku = f"{prefix}{int(rng.integers(10000, 99999))}-{int(rng.integers(10, 99))}"
            if sku not in sku_seen:
                sku_seen.add(sku)
                break
        rows.append(dict(
            sku=sku, description=desc, category=cat, subcategory=name,
            unit_l=dims[0], unit_w=dims[1], unit_h=dims[2], unit_weight=weight,
            case_qty=qty, case_l=case[0], case_w=case[1], case_h=case[2], case_weight=case_w,
            _type_pop=tpop))
    im = pd.DataFrame(rows)

    # popularity: lognormal x product-type weight; some SKUs never sell
    pop = rng.lognormal(0, p.popularity_sigma, p.n_skus) * im["_type_pop"].to_numpy()
    dead = rng.random(p.n_skus) < p.share_inventory_only
    pop[dead] = 0.0
    im["popularity"] = pop / pop.sum()
    im["inventory_only"] = dead

    # lifecycle (day indexes, inclusive start / exclusive end)
    start = np.zeros(p.n_skus, int)
    end = np.full(p.n_skus, p.days, int)
    live = ~dead
    new = live & (rng.random(p.n_skus) < p.share_new_in_year)
    lo, hi = min(30, p.days // 4), max(min(30, p.days // 4) + 1, p.days - 30)
    start[new] = rng.integers(lo, hi, new.sum())
    disc = live & ~new & (rng.random(p.n_skus) < p.share_discontinued)
    lo, hi = min(60, p.days // 3), max(min(60, p.days // 3) + 1, p.days - 20)
    end[disc] = rng.integers(lo, hi, disc.sum())
    im["active_from"], im["active_to"] = start, end
    im["supplier"] = rng.integers(1, p.n_suppliers + 1, p.n_skus)
    return im.drop(columns="_type_pop")
