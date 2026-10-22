# Test fixtures

No real client data here. Everything is synthetic and frozen; tests never generate data.

| File | What it is |
|---|---|
| `sample_client/` | A small sample client: `item_master.csv`, `orders.csv` (~22k lines, Dec 15 → 120 days, so Dec 25 and Jan 1 closures are included, plus e-com and store orders with full-case lines), `receipts.csv`, `inventory_snapshots.csv`. Only these four files: folder-discovery tests read the whole folder. |
| `sample_wms_profile.yaml` | Column names, units and date format of the sample client's export layout. |
| `sample_expected.csv` | Problems planted in the sample that tests must find: zero/negative-quantity order rows, SKU spelling variants. |
| `scope_client/` | A small client for the Scope step (made once by `make_scope_fixture.py`, seed 20261022). `orders.csv`: 2,778 lines at two sites. DC-East covers Jan 2025 → Jan 2026; DC-West covers Mar → Sep 2025 only, so DC-West alone has under 12 months and no Nov–Dec. It has two channels (Ecommerce, Store) and the statuses Shipped, `CANCELLED` and `Returned`, in mixed case on purpose. Also an Order Type, a Ship Date (blank on cancelled orders), a low-cardinality Carrier (UPS / FedEx / LTL) and a high-cardinality Customer ID. `inventory_snapshots.csv` has a Site column; `receipts.csv` has none, on purpose. Units are named in the item master headers. |
| `excel_sample.xlsx` + `.xls` `.xlsb` `.xlsm` | Three-sheet workbook (notes, an item master under title rows, orders) for the Excel readers. The other formats were saved from the .xlsx by Excel (`make_excel_fixtures.py`). |

`sample_client/` and `sample_expected.csv` were made on 2026-10-20 by **synthetic-data-tool**
(`C:\Tool Dev\synthetic-data-tool`, commit `2bfb6b8`, `profiles/test_fixture.yaml`, seed 20261020):

```
python -m synthtool --profile profiles/test_fixture.yaml --out <tmp>
# copy the four CSVs into sample_client/; keep the zero_or_negative_qty and
# sku_format_variant rows of <tmp>/_truth/defects.csv as sample_expected.csv
```

Replacing them changes what the tests see, so regenerate deliberately and re-run the full suite.
