"""demand.py — the calendar and the order lines.

Two channels share one catalogue:

* e-commerce: many small orders (mostly single-line, single-unit), picked
  all day in two waves, seven days a week;
* store / B2B replenishment: few large orders on weekdays, often full cases,
  spread wider across the catalogue (slow movers get ordered too).

Daily volume = base x weekday factor x annual season x promotions x noise.
Each SKU's share of a day's lines = popularity x lifecycle x its category's
seasonality, so toys peak in December and sporting goods in summer.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .catalog import TAXONOMY

# share of e-commerce picks by hour of day (two waves); store orders 6-14h
_ECOM_HOURS = np.array([0, 0, 0, 0, 0, 0, 2, 4, 6, 8, 9, 9, 8, 7, 7, 8, 8, 7, 6, 4, 3, 2, 1, 0], float)
_B2B_HOURS = np.array([0, 0, 0, 0, 0, 0, 6, 10, 12, 12, 11, 10, 9, 8, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0], float)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def build_calendar(p, rng: np.random.Generator) -> pd.DataFrame:
    dates = [p.start + dt.timedelta(days=i) for i in range(p.days)]
    doy = np.array([d.timetuple().tm_yday for d in dates])
    wd = np.array([d.weekday() for d in dates])
    factor = np.array(p.weekday_factors)[wd]
    # gentle annual shape: January dip, Q4 ramp into December
    season = 1.0 + p.q4_ramp * np.clip((doy - 274) / 60, 0, 1) * (doy < 359)
    season = season - 0.10 * (doy < 25)
    promo = np.ones(p.days)
    years = sorted({d.year for d in dates})
    special = {}
    for y in years:
        thanksgiving = _nth_weekday(y, 11, 3, 4)
        special[thanksgiving + dt.timedelta(days=1)] = p.promo_days.get("black_friday", 1)
        special[thanksgiving + dt.timedelta(days=4)] = p.promo_days.get("cyber_monday", 1)
        july = _nth_weekday(y, 7, 1, 2)
        special[july] = p.promo_days.get("mid_july_sale", 1)
        special[july + dt.timedelta(days=1)] = p.promo_days.get("mid_july_sale", 1) * 0.8
    for i, d in enumerate(dates):
        promo[i] = special.get(d, 1.0)
    noise = rng.lognormal(0, p.daily_noise_cv, p.days)
    closed = np.array([d.strftime("%m-%d") in p.closed_dates for d in dates])
    mult = factor * season * promo * noise
    mult[closed] = 0
    return pd.DataFrame(dict(date=dates, doy=doy, weekday=wd, volume_factor=mult, closed=closed))


def _sku_day_weights(im: pd.DataFrame, cal: pd.DataFrame) -> np.ndarray:
    """(n_days, n_skus) relative pick likelihood, before normalisation."""
    n_days = len(cal)
    amp = im["category"].map(lambda c: TAXONOMY[c]["amp"]).to_numpy()
    peak = im["category"].map(lambda c: TAXONOMY[c]["peak"]).to_numpy()
    doy = cal["doy"].to_numpy()[:, None]
    seasonal = 1 + amp[None, :] * np.cos(2 * np.pi * (doy - peak[None, :]) / 365)
    day = np.arange(n_days)[:, None]
    alive = (day >= im["active_from"].to_numpy()[None, :]) & (day < im["active_to"].to_numpy()[None, :])
    return im["popularity"].to_numpy()[None, :] * seasonal * alive


def _times(rng, hours_w, n):
    h = rng.choice(24, size=n, p=hours_w / hours_w.sum())
    m = rng.integers(0, 60, n)
    s = rng.integers(0, 60, n)
    return h * 3600 + m * 60 + s


def build_orders(p, im: pd.DataFrame, cal: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    weights = _sku_day_weights(im, cal)
    case_qty = im["case_qty"].to_numpy()
    skus = im["sku"].to_numpy()
    frames = []
    ec_seq, st_seq = 1_000_000, 500_000
    for d in range(len(cal)):
        vf = cal["volume_factor"].iat[d]
        if vf <= 0:
            continue
        w = weights[d]
        if w.sum() == 0:
            continue
        pe = w / w.sum()
        # --- e-commerce
        n_o = rng.poisson(p.ecom_orders_per_day * vf)
        k = np.where(rng.random(n_o) < p.ecom_single_line_share, 1,
                     1 + rng.geometric(p.ecom_multi_line_geom_p, n_o))
        n_l = int(k.sum())
        oid = np.repeat(np.arange(ec_seq, ec_seq + n_o), k)
        ec_seq += n_o
        idx = rng.choice(len(skus), size=n_l, p=pe)
        units = np.where(rng.random(n_l) < p.ecom_single_unit_share, 1, 1 + rng.geometric(0.6, n_l))
        t = np.repeat(_times(rng, _ECOM_HOURS, n_o), k)
        frames.append(pd.DataFrame(dict(day=d, order=np.char.add("EC", oid.astype(str)),
                                        channel="Ecommerce", sku_idx=idx, units=units, secs=t)))
        # --- store replenishment, weekdays only
        if cal["weekday"].iat[d] < 5:
            n_b = rng.poisson(p.b2b_orders_per_day * min(vf, 1.3))
            if n_b:
                kb = 3 + rng.negative_binomial(2, 2 / (2 + p.b2b_lines_mean - 3), n_b)
                pb = pe ** p.b2b_popularity_flatten
                pb /= pb.sum()
                nlb = int(kb.sum())
                oidb = np.repeat(np.arange(st_seq, st_seq + n_b), kb)
                st_seq += n_b
                idxb = rng.choice(len(skus), size=nlb, p=pb)
                cq = case_qty[idxb]
                full = rng.random(nlb) < p.b2b_full_case_share
                ub = np.where(full, cq * rng.geometric(0.6, nlb),
                              np.maximum(1, (cq * rng.uniform(0.1, 0.9, nlb)).astype(int)))
                tb = np.repeat(_times(rng, _B2B_HOURS, n_b), kb)
                frames.append(pd.DataFrame(dict(day=d, order=np.char.add("ST", oidb.astype(str)),
                                                channel="Store", sku_idx=idxb, units=ub, secs=tb)))
    o = pd.concat(frames, ignore_index=True)
    # one line per SKU per order: merge repeats (planted repeats come later)
    o = (o.groupby(["day", "order", "channel", "sku_idx"], as_index=False)
           .agg(units=("units", "sum"), secs=("secs", "first")))
    o["sku"] = skus[o["sku_idx"].to_numpy()]
    dates = pd.to_datetime(pd.Series(cal["date"].to_numpy()))
    o["order_date"] = dates.to_numpy()[o["day"].to_numpy()]
    return o
