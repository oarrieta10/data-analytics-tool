# gtp-fit

Goods-to-person technology fit tool. Module 1, the synthetic data generator, is built.

```
pip install -e .[dev]
python -m gtpfit.synth --out data/synthetic/default
```

This writes four client-style CSVs (item master, orders, receipts, inventory snapshots), an answer key
under `_truth/`, a `manifest.json`, and `synthetic_data_report.html`, which checks the
dataset against typical warehouse ranges. A one-year, 8,000-SKU, ~1M-line dataset takes about 25 seconds.
E-commerce baskets get bigger on promotion and Q4 days by default (`peak_shape:` in a profile; the true
per-day shape is in `_truth/calendar.csv`). See `CLAUDE.md` for the plan and ground rules.
