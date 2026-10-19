# gtp-fit

Goods-to-person technology fit tool. The synthetic data generator and ingest are built.

```
pip install -e .[dev]
python -m gtpfit.synth --out data/synthetic/default
```

This writes four client-style CSVs (item master, orders, receipts, inventory snapshots), an answer key
under `_truth/`, a `manifest.json`, and `synthetic_data_report.html`, which checks the
dataset against typical warehouse ranges. A one-year, 8,000-SKU, ~1M-line dataset takes about 25 seconds.
E-commerce baskets can change on promotion and Q4 days (`peak_shape:` in a profile, off by default; the true
per-day shape is in `_truth/calendar.csv`). Module 2, ingest, reads a client folder into canonical tables and refuses data it can't trust:
```
python -m gtpfit.ingest data/synthetic/default --wms wms_profiles/synthetic.yaml
```
See `CLAUDE.md` for the plan and ground rules.
