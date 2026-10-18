"""generate.py — run the whole generator and write a client-style dataset.

    generate(profile, out_dir) -> dict   (paths and headline counts)

Output folder:
    item_master.csv, orders.csv, receipts.csv, inventory_snapshots.csv
        what a client would send: their own column names, US units, defects
    _truth/item_master_true.csv   correct attributes for every SKU
    _truth/defects.csv            the answer key for every planted problem
    _truth/sku_stats.csv          velocity class, rate and policy per SKU
    manifest.json                 profile used, seed, counts, file list

Same profile + seed -> byte-identical files.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Profile
from .catalog import build_true_item_master
from .demand import build_calendar, build_orders
from .inventory import simulate
from .defects import plant_item_master, plant_transactions

EXCEL_ROW_LIMIT = 1_048_575  # data rows below one header row

# the client's own column names — the ingest step has to map these
IM_COLS = {"sku": "Item Number", "description": "Item Description", "category": "Category",
           "subcategory": "Sub Category", "unit_l": "Unit Length", "unit_w": "Unit Width",
           "unit_h": "Unit Height", "unit_weight": "Unit Weight", "case_qty": "Case Pack",
           "case_l": "Case Length", "case_w": "Case Width", "case_h": "Case Height",
           "case_weight": "Case Weight"}


def _fmt_time(secs: pd.Series) -> pd.Series:
    s = secs.fillna(-1).astype(int)
    out = (s // 3600).map("{:02d}".format) + ":" + (s % 3600 // 60).map("{:02d}".format) + ":" + (s % 60).map("{:02d}".format)
    return out.where(s >= 0, "")


def generate(p: Profile, out_dir: str | Path) -> dict:
    out = Path(out_dir)
    (out / "_truth").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(p.seed)

    im_true = build_true_item_master(p, rng)
    cal = build_calendar(p, rng)
    orders = build_orders(p, im_true, cal, rng)
    receipts, snaps, stats = simulate(p, im_true, orders, cal, rng)
    im_obs, log, orphans = plant_item_master(p, im_true, rng)
    orders_obs = plant_transactions(p, orders, rng, log)

    dates = pd.to_datetime(pd.Series(cal["date"].to_numpy()))
    # --- orders, sorted the way a WMS export usually is
    o = orders_obs.sort_values(["day", "secs", "order"], kind="stable", na_position="last")
    o_out = pd.DataFrame({
        "Order Nbr": o["order"], "Order Date": o["order_date"].dt.strftime("%m/%d/%Y"),
        "Pick Time": _fmt_time(o["secs"]), "Channel": o["channel"],
        "Item Number": o["sku"], "Qty Shipped": o["units"].astype(int)})
    truncated = False
    if p.truncate_orders_at_excel_limit and len(o_out) > EXCEL_ROW_LIMIT:
        o_out = o_out.iloc[:EXCEL_ROW_LIMIT]
        truncated = True

    skus = im_true["sku"].to_numpy()
    r_out = pd.DataFrame({
        "Receipt Nbr": receipts["receipt"], "PO Nbr": receipts["po"],
        "Receipt Date": dates.to_numpy()[receipts["day"]],
        "Item Number": skus[receipts["sku_idx"]], "Qty Received": receipts["units"]})
    r_out = r_out.sort_values(["Receipt Date", "Receipt Nbr", "Item Number"])
    r_out["Receipt Date"] = r_out["Receipt Date"].dt.strftime("%m/%d/%Y")

    loc_zone = np.where(im_true["case_l"].to_numpy() > 30, "BULK", np.where(stats["velocity"].to_numpy() == "A", "FWD", "RES"))
    loc_code = np.char.add(np.char.add(loc_zone, "-"), rng.integers(100, 999, len(im_true)).astype(str))
    s_out = pd.DataFrame({
        "Snapshot Date": dates.to_numpy()[snaps["day"]], "Item Number": skus[snaps["sku_idx"]],
        "Location": loc_code[snaps["sku_idx"]], "On Hand Qty": snaps["on_hand"]})
    s_out["Snapshot Date"] = s_out["Snapshot Date"].dt.strftime("%m/%d/%Y")

    im_out = im_obs[list(IM_COLS)].rename(columns=IM_COLS)
    im_out["Case Pack"] = im_out["Case Pack"].astype("Int64")

    files = {
        "item_master": out / "item_master.csv", "orders": out / "orders.csv",
        "receipts": out / "receipts.csv", "inventory_snapshots": out / "inventory_snapshots.csv"}
    im_out.to_csv(files["item_master"], index=False)
    o_out.to_csv(files["orders"], index=False)
    r_out.to_csv(files["receipts"], index=False)
    s_out.to_csv(files["inventory_snapshots"], index=False)

    truth = im_true.join(stats)
    truth["in_item_master_file"] = ~truth["sku"].isin(orphans)
    truth.to_csv(out / "_truth" / "item_master_true.csv", index=False)
    pd.DataFrame(log).to_csv(out / "_truth" / "defects.csv", index=False)
    cal.to_csv(out / "_truth" / "calendar.csv", index=False)

    manifest = dict(
        generator="gtpfit.synth", profile=p.to_dict(),
        counts=dict(skus_true=len(im_true), item_master_rows=len(im_out), order_lines=len(o_out),
                    orders=int(o_out["Order Nbr"].nunique()), receipt_lines=len(r_out),
                    snapshot_rows=len(s_out), planted_defect_records=len(log)),
        orders_truncated_at_excel_limit=truncated,
        files={k: v.name for k, v in files.items()})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest
