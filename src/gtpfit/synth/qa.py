"""qa.py — does the synthetic dataset look like a real warehouse?

    realism_checks(out_dir) -> (checks DataFrame, chart data dict)
    write_report(out_dir)   -> path of synthetic_data_report.html

Reads only the client-style files (plus _truth for the answer key), the same
way the analysis pipeline will.  The "typical range" for each check is a
consultant's rule of thumb for mid-size e-commerce / omnichannel sites, not a
published standard; it says whether the shape is believable, nothing more.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import pandas as pd

CHECKS = [
    # key, label, low, high, format
    ("top20_lines", "Lines picked from the top 20% of SKUs", 0.70, 0.85, "pct"),
    ("single_line", "E-commerce orders with a single line", 0.40, 0.65, "pct"),
    ("lines_per_order", "E-commerce lines per order", 1.5, 2.5, "num"),
    ("design_ratio", "Design day (95th percentile) / average day", 1.3, 1.8, "x"),
    ("peak_ratio", "Peak day / average day", 2.0, 5.0, "x"),  # lines; bigger peak baskets (if set) push past 4x
    ("peak_basket", "E-commerce lines per order, top-5% days / other days", 0.9, 1.3, "x"),
    ("active_share", "Item-master SKUs picked during the year", 0.75, 0.95, "pct"),
    ("median_doh", "Median days on hand, SKUs that sell", 15, 60, "num"),
    ("turns", "Inventory turns per year", 6, 15, "num"),
    ("clean_dims", "Item-master rows with usable unit dims and weight", 0.60, 0.90, "pct"),
]


def _load(d: Path):
    o = pd.read_csv(d / "orders.csv", dtype={"Item Number": str})
    im = pd.read_csv(d / "item_master.csv", dtype={"Item Number": str})
    inv = pd.read_csv(d / "inventory_snapshots.csv", dtype={"Item Number": str})
    rec = pd.read_csv(d / "receipts.csv", dtype={"Item Number": str})
    truth = pd.read_csv(d / "_truth" / "item_master_true.csv", dtype={"sku": str})
    defects = pd.read_csv(d / "_truth" / "defects.csv")
    return o, im, inv, rec, truth, defects


def realism_checks(out_dir):
    d = Path(out_dir)
    o, im, inv, rec, truth, defects = _load(d)
    raw_rows = len(o)
    o = o.drop_duplicates()
    o = o[o["Qty Shipped"] > 0].copy()
    o["sku"] = o["Item Number"].str.strip().str.upper()
    o["date"] = pd.to_datetime(o["Order Date"], format="%m/%d/%Y")
    v = {}
    lines = o.groupby("sku").size().sort_values(ascending=False)
    cum = lines.cumsum().to_numpy() / lines.sum()
    v["top20_lines"] = cum[int(0.2 * len(lines)) - 1]
    ec = o[o["Channel"] == "Ecommerce"].groupby("Order Nbr").agg(l=("sku", "size"), u=("Qty Shipped", "sum"))
    v["single_line"] = (ec["l"] == 1).mean()
    v["lines_per_order"] = ec["l"].mean()
    daily = o.groupby("date").size()
    v["design_ratio"] = daily.quantile(0.95) / daily.mean()
    v["peak_ratio"] = daily.max() / daily.mean()
    # basket size on design-day-or-busier days vs the rest (same day split as design_ratio)
    busy = daily.index[daily >= daily.quantile(0.95)]
    ec_day = o[o["Channel"] == "Ecommerce"].groupby("Order Nbr").agg(l=("sku", "size"), date=("date", "first"))
    on_busy = ec_day["date"].isin(busy)
    v["peak_basket"] = ec_day.loc[on_busy, "l"].mean() / ec_day.loc[~on_busy, "l"].mean()
    v["active_share"] = im["Item Number"].isin(lines.index).sum() / im["Item Number"].nunique()
    units_day = o.groupby("sku")["Qty Shipped"].sum() / o["date"].nunique()
    avg_oh = inv.groupby("Item Number")["On Hand Qty"].mean()
    doh = (avg_oh / units_day.reindex(avg_oh.index)).replace([np.inf], np.nan).dropna()
    v["median_doh"] = doh.median()
    v["turns"] = o["Qty Shipped"].sum() / inv.groupby("Snapshot Date")["On Hand Qty"].sum().mean()
    dims = im[["Unit Length", "Unit Width", "Unit Height", "Unit Weight"]]
    usable = dims.notna().all(axis=1) & (dims[["Unit Length", "Unit Width", "Unit Height"]] > 0.25).all(axis=1) \
        & ~((dims["Unit Length"] == dims["Unit Width"]) & (dims["Unit Width"] == dims["Unit Height"]))
    v["clean_dims"] = usable.mean()

    rows = []
    for key, label, lo, hi, fmt in CHECKS:
        val = float(v[key])
        rows.append(dict(key=key, label=label, value=val, low=lo, high=hi, fmt=fmt,
                         status="in range" if lo <= val <= hi else ("below" if val < lo else "above")))
    checks = pd.DataFrame(rows)

    # ---- chart data
    n = len(cum)
    pick = np.unique(np.r_[np.linspace(0, n - 1, 200).astype(int), int(0.2 * n) - 1])
    pareto = [(float((i + 1) / n), float(cum[i])) for i in pick]
    ch = o["Channel"].unique()
    daily_ch = o.groupby(["date", "Channel"]).size().unstack(fill_value=0)
    all_days = pd.date_range(o["date"].min(), o["date"].max())
    daily_ch = daily_ch.reindex(all_days, fill_value=0)
    t = pd.to_datetime(o["Pick Time"], format="%H:%M:%S", errors="coerce")
    hours = o.assign(h=t.dt.hour).dropna(subset=["h"]).groupby(["h", "Channel"]).size().unstack(fill_value=0)
    hours = hours.reindex(range(24), fill_value=0)
    lpo = ec["l"].clip(upper=9).value_counts().sort_index()
    t2 = truth.copy()
    t2["cube_ft3"] = t2["unit_l"] * t2["unit_w"] * t2["unit_h"] / 1728
    cube = t2.groupby("category")["cube_ft3"].quantile([0.1, 0.5, 0.9]).unstack()
    cube = cube.sort_values(0.5)
    # defects: SKUs affected and share of lines they carry
    sku_lines = truth.set_index("sku")["lines"]
    dd = defects[defects["table"] == "item_master"].drop_duplicates(["key", "defect"])
    dsum = dd.groupby("defect").agg(skus=("key", "nunique"), keys=("key", list))
    dsum["line_share"] = [sku_lines.reindex(k).sum() / sku_lines.sum() for k in dsum["keys"]]
    dsum = dsum.drop(columns="keys").sort_values("skus", ascending=False)
    tx = defects[defects["table"] == "orders"]
    tx_n = np.where(tx["key"] == "*", pd.to_numeric(tx["true_value"], errors="coerce"), 1)
    tx_sum = pd.Series(tx_n, index=tx["defect"].to_numpy()).groupby(level=0).sum()
    vel = truth.loc[truth["lines"] > 0, "velocity"].value_counts().reindex(list("ABCD")).fillna(0)

    charts = dict(
        pareto=pareto,
        daily=dict(dates=[x.strftime("%Y-%m-%d") for x in daily_ch.index],
                   series={c: daily_ch[c].astype(int).tolist() for c in daily_ch.columns}),
        hours=dict(series={c: hours[c].astype(int).tolist() for c in hours.columns}),
        lpo=dict(k=[str(int(i)) if i < 9 else "9+" for i in lpo.index], n=lpo.astype(int).tolist()),
        cube=[dict(cat=c, p10=float(r[0.1]), p50=float(r[0.5]), p90=float(r[0.9])) for c, r in cube.iterrows()],
        defects=[dict(defect=k, skus=int(r.skus), line_share=float(r.line_share)) for k, r in dsum.iterrows()],
        tx_defects={k: int(c) for k, c in tx_sum.items()},
        velocity={k: int(c) for k, c in vel.items()},
        totals=dict(lines=int(len(o)), orders=int(o["Order Nbr"].nunique()), skus=int(im["Item Number"].nunique()),
                    days=int(o["date"].nunique()), receipts=int(len(rec)), first=str(o["date"].min().date()),
                    last=str(o["date"].max().date()), store_share=float((o["Channel"] == "Store").mean()),
                    raw_rows=int(raw_rows), excel_clipped=bool(raw_rows == 1_048_575)),
    )
    return checks, charts


def write_report(out_dir) -> Path:
    d = Path(out_dir)
    checks, charts = realism_checks(d)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    tpl = (Path(__file__).parent / "qa_template.html").read_text(encoding="utf-8")
    payload = dict(checks=checks.to_dict("records"), charts=charts,
                   profile=manifest["profile"], counts=manifest["counts"])
    out = d / "synthetic_data_report.html"
    out.write_text(tpl.replace("/*__DATA__*/null", json.dumps(payload)), encoding="utf-8")
    return out
