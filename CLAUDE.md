# gtp-fit — goods-to-person technology fit tool

A local Python tool for HAI Robotics solution consulting. It takes a client's warehouse data
(item master, orders, receipts, inventory), cleans and backfills it, profiles the operation, and
sizes goods-to-person (GTP) options: HAI ACR systems against shuttles, AutoStore, Exotec and
similar. It must say plainly when HAI is not the best fit. Output is a single self-contained
interactive HTML report.

## Ground rules
- **Clean-room build.** Do not copy code, DAX, M, reference tables or data from any previous
  employer's tools. Methods are general industry practice; implementations are written fresh here.
- **No real client data in the repo.** Development and tests use `gtpfit.synth` datasets only.
  `data/` is git-ignored. Real client files stay on the consultant's laptop.
- **Numbers come from code, never from prose.** Narrative text is filled from computed values.
- **Every assumption is a parameter** with a default, a unit, and a source/confidence note.
  No magic numbers inside functions.
- **Units:** inches, pounds, days internally. Convert at ingest; record the original unit.

## Layout
- `src/gtpfit/synth/` — synthetic dataset generator with an answer key (done)
  - `config.py` profile + defect rates · `catalog.py` taxonomy and true item master
  - `demand.py` calendar + orders · `inventory.py` (s,Q) simulation, receipts, snapshots
  - `defects.py` planted problems + manifest · `generate.py` writer · `qa.py` realism report
- `src/gtpfit/ingest/` — client files → canonical tables (done)
  - `schema.py` canonical fields + header synonyms · `params.py` thresholds with sources
  - `readers.py` one registry of file readers (content first, extension second): delimited text (any
    extension/delimiter/encoding), .gz/.zip, Excel .xlsx/.xlsm/.xls/.xlsb (python-calamine, fallbacks
    openpyxl/xlrd/pyxlsb; title rows skipped), JSON/JSONL, Parquet, Feather/Arrow, ORC, SQLite, DuckDB.
    A `Source(path, part)` is a file or one sheet / table / zip member. Not handled: Access, live DB
    connections, SQL dumps, fixed-width reports.
  - `classify.py` guess the table each source holds (consultant confirms) · `mapping.py` auto-match + WMS profiles
  - `coerce.py` types, date-format detection · `units.py` header/profile/data unit decision
  - `checks.py` issue record, gaps · `pipeline.py` ingest_table / ingest_sources / ingest_folder / write_outputs
- `src/gtpfit/app/` — local Streamlit workbench, one page per module (`python -m gtpfit.app`)
- `profiles/` — YAML profiles for synthetic clients (`default`, `messy_large`, `playground`)
- `data/workspace/<client>/` — files dropped into the workbench (git-ignored, stays on the laptop)
- `tests/fixtures/` — synthetic Excel fixtures (.xls/.xlsb/.xlsm need Excel to make: `make_excel_fixtures.py`)
- `wms_profiles/` — saved column mappings per client WMS (`synthetic.yaml` for synth exports)
- `tests/` — pytest; run `python -m pytest -q`

## Planned modules (build in this order; tests first for each)
After each module: add its page to the workbench with live parameter controls, launch it, and let
the user inspect and play with it before starting the next module.
1. ~~`ingest/`~~ (done) — column mapping (saved per-WMS profiles), unit detection, type coercion, refusal of
   silent problems (Excel row limit, date gaps, negative qty). DuckDB for large files.
2. `quality/` — data score weighted by lines/units/cube; placeholder, physics and outlier checks.
   **Scored against `_truth/defects.csv`**: report recall and precision per defect type.
3. `backfill/` — tiered: derive from other fields → KNN (category, sub-category, TF-IDF of
   description, case dims/weight; log space) → category median. Every value tagged with method and
   confidence. **Scored against `_truth/item_master_true.csv`** with masked-holdout error (MAPE).
4. `profile/` — volumes, design day at a percentile, peak ratios, order profile, ABC velocity,
   days on hand, turns, inbound.
5. `fit/` — per-SKU container fit (6-orientation tessellation + weight cap) for every container
   in the catalogue.
6. `catalog/` + `sizing/` — technology catalogue (YAML, each parameter with source + confidence);
   system sizing: containers, footprint at clear height, workstations at design-day peak, rough cost.
7. `report/` — comparison scorecard and story; one self-contained HTML file (inline SVG charts,
   light/dark, hover tooltips, "how it's calculated" per chart).

## Commands
```
pip install -e .[dev,app]
python -m gtpfit.app                                                    # workbench, localhost:8501
python -m gtpfit.synth --out data/synthetic/default                     # generate + report
python -m gtpfit.synth --profile profiles/messy_large.yaml --out data/synthetic/messy_large
python -m gtpfit.synth --out data/synthetic/default --report-only
python -m gtpfit.ingest data/synthetic/default --wms wms_profiles/synthetic.yaml   # -> FOLDER/_ingest
python -m gtpfit.ingest FOLDER --accept excel_row_limit                         # accept a checked block
python -m pytest -q
```

## Conventions
- pandas/numpy now; keep functions pure (DataFrame in, DataFrame out) so they port to DuckDB later.
- Deterministic: every random draw goes through the profile's seed.
- Docstring at the top of each module saying what it does and what it does not.
- Always pass `encoding="utf-8"` when reading/writing text; Windows defaults to cp1252.
- **What counts as an order line is a per-client choice** (`gtpfit/lines.py`: `row` = every file row,
  `order_sku` = repeated order + SKU rows merged; `picks` keeps the row count). Ingest never merges rows;
  analysis modules take `line_definition` as a parameter and the report states which one was used.
- Ingest severities: `block` (refuse unless `accept=[code]`) · `warn` · `info`. Rows are never dropped
  silently — they go to `rejects` with a reason.
