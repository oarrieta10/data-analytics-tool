"""Make tests/fixtures/scope_client/ once (seeded). Tests never run this; they read the frozen CSVs.

    python tests/fixtures/make_scope_fixture.py

Two sites, two channels, mixed-case cancelled / returned statuses, an order type,
a ship date (blank on cancelled orders), a low-cardinality carrier column and a
high-cardinality customer column.  DC-East covers Jan 2025 - Jan 2026; DC-West
only Mar - Sep 2025, so DC-West alone has under 12 months and no Nov - Dec.
Inventory has a site column; receipts do not.
"""
import csv
import datetime as dt
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent / "scope_client"
rng = random.Random(20261022)
SKUS = [f"SC{1000 + i}" for i in range(40)]
SITES = {"DC-East": (dt.date(2025, 1, 1), dt.date(2026, 1, 31), 6),
         "DC-West": (dt.date(2025, 3, 1), dt.date(2025, 9, 30), 3)}


def days(a, b):
    return [a + dt.timedelta(n) for n in range((b - a).days + 1)]


def fmt(d):
    return d.strftime("%m/%d/%Y")


def write(name, header, rows):
    with open(OUT / name, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    OUT.mkdir(exist_ok=True)
    write("item_master.csv", ["Item Number", "Item Description", "Unit Length (in)", "Unit Width (in)",
                              "Unit Height (in)", "Unit Weight (lb)", "Case Pack"],
          [[s, f"Sample item {s}", round(rng.uniform(6, 18), 1), round(rng.uniform(4, 12), 1),
            round(rng.uniform(2, 8), 1), round(rng.uniform(0.5, 6), 2), rng.choice([6, 12, 24])] for s in SKUS])
    rows, n = [], 0
    for site, (a, b, per_day) in SITES.items():
        for d in days(a, b):
            lines_today = max(1, int(rng.gauss(per_day, 1.5)))
            while lines_today > 0:
                n += 1
                k = min(lines_today, rng.choice([1, 1, 2, 3]))
                lines_today -= k
                r = rng.random()
                status = "Shipped" if r < 0.94 else ("CANCELLED" if r < 0.98 else "Returned")
                ship = "" if status == "CANCELLED" else fmt(d + dt.timedelta(rng.choice([0, 1, 1, 2])))
                common = [f"SO{n:06d}", fmt(d), ship, site, rng.choice(["Ecommerce", "Ecommerce", "Store"]),
                          rng.choice(["Standard", "Standard", "Expedited"]), status,
                          rng.choice(["UPS", "FedEx", "LTL"]), f"C{rng.randint(10000, 12999)}"]
                for sku in rng.sample(SKUS, k):
                    rows.append(common + [sku, rng.choice([1, 1, 2, 3, 6])])
    write("orders.csv", ["Order Nbr", "Order Date", "Ship Date", "Site", "Channel", "Order Type", "Order Status",
                         "Carrier", "Customer ID", "Item Number", "Qty Shipped"], rows)
    receipts, m = [], 0
    for d in days(dt.date(2025, 1, 2), dt.date(2026, 1, 30)):
        if d.weekday() < 5 and rng.random() < 0.6:
            m += 1
            receipts += [[f"RC{m:05d}", fmt(d), s, rng.choice([24, 48, 96])] for s in rng.sample(SKUS, 3)]
    write("receipts.csv", ["Receipt Nbr", "Receipt Date", "Item Number", "Qty Received"], receipts)
    inv = []
    for snap in [dt.date(2025, mo, 28) for mo in range(1, 13)]:
        for site in SITES:
            inv += [[fmt(snap), site, s, f"{site[-4:].upper()}-{i:03d}", rng.randint(5, 300)]
                    for i, s in enumerate(SKUS)]
    write("inventory_snapshots.csv", ["Snapshot Date", "Site", "Item Number", "Location", "On Hand Qty"], inv)
    print(f"{len(rows):,} order lines, {len(receipts):,} receipt lines, {len(inv):,} inventory rows -> {OUT}")


if __name__ == "__main__":
    main()
