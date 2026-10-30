# gtp-fit — goods-to-person technology fit tool

A local Python tool for HAI Robotics solution consulting. It takes a client's warehouse data
(item master, orders, receipts, inventory), cleans and backfills it, profiles the operation, and
sizes goods-to-person (GTP) options: HAI ACR systems against shuttles, AutoStore, Exotec and
similar. It must say plainly when HAI is not the best fit. Output is a single self-contained
interactive HTML report.

## Ground rules
- **Clean-room build.** Do not copy code, DAX, M, reference tables or data from any previous
  employer's tools. Methods are general industry practice; implementations are written fresh here.
  **Exception:** the consultant's own Power BI tool in `pbi/` (General Data Analysis, built at a previous
  employer, who gave permission) may be read in full and its rules and values reused as a starting point
  (slotting, storage sizing, tessellation); gtp-fit's code is still written fresh in Python, each reused value
  recorded as a parameter with its source. `pbi/` is git-ignored and never committed.
- **Develop and validate on real client data, locally.** From quality/ on, modules are built, tuned and checked on
  the real clients in `data/real/` (git-ignored; ingested and scoped into `data/workspace/`). Committed tests use
  small hand-built frames and the synthetic fixtures; real-data tests skip when `data/real/` is absent.
- **No real client data in the repo.** Tests use the frozen sample client in `tests/fixtures/`
  (made once by the separate synthetic-data-tool; this repo has no generator and does not depend on
  it). `data/` is git-ignored; real client files stay on the consultant's laptop.
- **Numbers come from code, never from prose.** Narrative text is filled from computed values.
- **Every assumption is a parameter** with a default, a unit, and a source/confidence note.
  No magic numbers inside functions.
- **Units:** inches, pounds, days internally. Convert at ingest; record the original unit.

## Layout
- `src/gtpfit/ingest/` — client files → canonical tables (done)
  - `schema.py` canonical fields + header synonyms · `params.py` thresholds with sources, labels and tooltips (`setting()` metadata)
  - `readers.py` one registry of file readers (content first, extension second): delimited text (any
    extension/delimiter/encoding), .gz/.zip, Excel .xlsx/.xlsm/.xls/.xlsb (python-calamine, fallbacks
    openpyxl/xlrd/pyxlsb; title rows skipped), JSON/JSONL, Parquet, Feather/Arrow, ORC, SQLite, DuckDB.
    A `Source(path, part)` is a file or one sheet / table / zip member. Not handled: Access, live DB
    connections, SQL dumps, fixed-width reports. A file or zip member that can't be opened (password, open in
    another program, damaged) raises `UnreadableFileError` with a plain reason; detect lists it as "Can't read",
    the rest of the folder goes on, and ingest blocks with `unreadable_file` (not acceptable).
  - `classify.py` guess the table each source holds (consultant confirms) · `mapping.py` auto-match, profile storage,
    `saved_mapping_for` (reuse a client's last confirmed mapping when the headers overlap)
  - `coerce.py` types, date-format detection · `units.py` header/profile/data unit decision
  - `checks.py` issue record, `ISSUE_TEXT` (title / plain meaning / design impact per code), accepting blocks, gaps
  - `detect.py` per file / sheet: table guess, read-as, row count · `coverage.py` period covered, gaps, peak flag
  - `pipeline.py` ingest_table / ingest_sources / ingest_folder / write_outputs; `apply_acceptances` (accepting
    only relabels blocks, so the workbench applies it to a cached run) and `table_inputs` (what one table depends
    on: the workbench re-reads a table only when its own inputs change, e.g. units re-read just the item master)
  - `exports.py` rejects for the client, accepted blocks · `receipt.py` data receipt (HTML) ·
    `request_template.py` data request (HTML / Excel) from the schema
- `src/gtpfit/lines.py` — order-line definitions (a per-client choice, see Conventions)
- `src/gtpfit/scope.py` — what the design is for: filters on site / channel / order status / order type / x_ extras
  and a date window, as a saved definition (`ScopeSpec`) applied at load time; waterfall, summary, before/after
- `src/gtpfit/quality/` — can we trust the item data: `checks.py` (flags per SKU: missing, placeholder, copied size,
  physics against the assortment's bands, case vs units, unit slips, peer outliers, duplicates, orphans),
  `peers.py` (sub-category → category → all, robust z in log space), `status.py` (status, readiness for unit / case
  fit, weighted score, items to measure), `text.py` (`QUALITY_TEXT`), `params.py` (`QualityParams`). Reads
  `tables.load` only; writes `data/workspace/<client>/_quality/` (with `run_key.txt`: the page reads those files back
  after a restart when inputs, settings and quality code are unchanged); never changes a value
- `src/gtpfit/backfill/` — estimates for item sizes quality cannot trust: `targets.py` (fill missing / zero /
  placeholder / copied values; keep every other doubt, unit slips included, with the estimate beside it and a review
  flag beyond `review_factor`; donors = trusted SKUs), `estimate.py` (case of one → nearest trusted neighbours by
  char n-gram TF-IDF of the description + case / known sizes + category → peer median; scaled to a given unit
  volume), `validate.py` (holdout: hide a seeded share of trusted active items, estimate them from the rest; error
  factor, ±25 / ±50% shares, by method and confidence, against a median-item baseline; confidence bands were set
  from it), `params.py` (`BackfillParams`), `text.py` (`BACKFILL_TEXT`). Runs quality first; estimates active SKUs
  only; writes `data/workspace/<client>/_backfill/` (values, item_master_backfilled, summary with the gap left:
  orphans / kept doubts / not estimated). A client can skip backfill (`backfill_skipped:` in client.yaml; buttons on
  the Quality and Backfill pages): nothing runs, `load_items` returns the item master as sent, earlier results are
  kept, and Profile and Fit say so and record it in their summaries
- `src/gtpfit/profile/` — the operation and its design capacity (stage 1 so far): `demand.py` (working calendar:
  weekdays worked in ≥ `open_weekday_share` of weeks; a working weekday without orders is closed; off-day orders
  count on the next or previous working day; demand per channel in lines / units / orders), `backlog.py` (daily
  FIFO simulation by age with a final "or more" group; service by age, days with backlog, max days to clear,
  utilization, design/average, peak-day coverage, percentile rank; "within N days" is judged only on work with N
  days left in the history), `design.py` (smallest capacity meeting per-channel SLAs by bisection, binding target
  named; `fallback_percentile` day without SLAs; `what_if`). Reproduces the consultant's capacity workbook
  (`data/capacity model/`, local) exactly on the client it was built from: `tests/test_profile_real.py`.
  Also `volumes.py` (daily / monthly / weekday, peak month ratio), `orders.py` (order profile, lines-per-order and
  units-per-line distributions), `hourly.py` (from `pick_secs`; design-day peak hour), `velocity.py` (ABC by lines,
  50 / 80 / 95% of lines, D the rest, as in `pbi/`; stock never ordered = No movement, cube moved and on hand), `stock.py` (days on hand, turns), `inbound.py` (receipts per
  day, receiving capacity with the same engine). `run_profile` reads `tables.load` and sizes from
  `backfill.load_items` (the backfilled item master when backfill has run); SLAs per channel are saved under
  `profile: sla:` (`inbound_sla:` for receiving); writes `_profile/` (daily, abc, profile_summary.json)
- `src/gtpfit/fit/` — units per SKU × container × divider cell (layer 1 of the design stage): `tessellate.py`
  (vectorised: 6 orientations, then the leftover length / width / height spaces, as in the `pbi/` tool; liquid
  cube; weight cap), `containers.py` (the catalogue `src/gtpfit/catalog/containers.yaml`, or `GTPFIT_CATALOG`:
  inside sizes in any unit, payload or `gross_limit` − tare, divider layouts, `units` or `case_on_tray` mode with a
  carton range, source and confidence required; a low-confidence payload is a placeholder, named in every result;
  tapered nestable totes are fitted to their bottom inside size), `params.py`
  (`FitParams`: cubing method, keep upright, liquid-cube utilisation). Fits active SKUs only; carries each SKU's
  size provenance from `backfill.load_items`; writes `_fit/` (fit.parquet long, fit_summary.json). Catalogue today:
  **HaiPick Climb only, containers HAI lists as available for sale** (650×450×300 folding and nestable totes,
  650×450×70 carton tray, 600×400×300 nestable tote; 30 kg gross per tote, no dividers). HPS3, mould-pending totes
  and competitors (AutoStore) are added later
- `src/gtpfit/tables.py` — **the one loader**: `tables.load(client)` = ingest outputs with the saved scope applied.
  Every module from quality/ onward reads through it, never from `_ingest/` directly
- `src/gtpfit/workspace.py` — workspace paths, client.yaml, client settings (no Streamlit; pages and modules share it)
- `src/gtpfit/app/` — local Streamlit workbench, one page per module (`python -m gtpfit.app`)
  - `app/ingest/` guided flow 1 Choose folder → 2 Confirm files (+ client settings) → 3 Review → 4 Done (`state.py` session,
    folder dialog, per-client memory; `widgets.py` shared pieces). Input is always one folder (native dialog in
    a subprocess, a pasted path, or a recent folder), read in place: nothing copied, nothing written into it
  - `app/profile/` the Profile page: service targets per channel in two editable tables (saved per client), the
    design capacity and the target that sets it (or the percentile-day fallback), KPIs, service by age, backlog
    chart, a what-if capacity, then volumes, order shape, hourly, ABC, stock (warns when inventory is a fixed number
    of days of demand), inbound
  - `app/fit/` the Fit page: catalogue containers with sources and placeholders, share of SKUs / lines that fit each
    container and cell, one container in detail (limits, units per container, by ABC, estimated sizes), SKU
    look-up, add a container to the catalogue (source required)
  - `app/quality/`, `app/backfill/` one page each (settings drawn from the module's params with
    `widgets.control`, saved under `quality:` / `backfill:` in client.yaml; a cached run that also writes the
    module's outputs). Backfill shows readiness before → after, the gap left, the holdout table, filled and
    to-review lists, a SKU look-up
  - `app/loader.py` the HAI robot loading animation in the middle of the screen while a page works (replaces
    Streamlit's running icon); `app/theme.py` Hai Blue accent and the Hai Robotics logo (top left), from the
    Hai Robotics design system, plus its chart tokens (darker-blue, hai-blue tints, safety-orange as the accent,
    ink-muted axes, line-grey gridlines, Open Sans) and `style_chart()`; red is used only for backlog (work
    waiting), at the consultant's request; `app/static/` serves the loader, three.js and the logo files locally (see its README)
- `vendor/` — third-party / HAI-supplied files kept as received (e.g. the loader zip); see its README
- `data/workspace/<client>/` — kept per client: `_ingest/` outputs, `wms_<client>.yaml` (confirmed columns, units,
  date format; saved automatically), `client.yaml` (recent folders, client settings, `scope:`), `_scope/scope_summary.csv` — git-ignored; client files are never copied
- `src/gtpfit/ingest/learned_synonyms.yaml` — client column names taught with "Remember this synonym" (global;
  committed; column names only, never values)
- `tests/fixtures/` — frozen sample client (`sample_client/`, its saved column mapping, expected findings) and
  Excel fixtures (.xls/.xlsb/.xlsm need Excel to make: `make_excel_fixtures.py`); see its README
- `tests/` — pytest; run `python -m pytest -q`

## Planned modules (build in this order; tests first for each)
After each module: add its page to the workbench with live parameter controls, launch it, and let
the user inspect and play with it before starting the next module.
1. ~~`ingest/`~~ (done) — column mapping (saved per client), unit detection, type coercion, refusal of
   silent problems (Excel row limit, date gaps, negative qty). DuckDB for large files.
1b. `scope` — what are we designing for: site, channel, order type / status, date window. Saved as a definition in
   client.yaml and applied by `tables.load`; nothing is copied. Stage 3 (exclude days, per-site runs) later.
2. `quality/` — data score: share of in-scope lines / units / on-hand (and SKUs) carried by SKUs whose data is usable
   for unit fit and for case fit. **Never weighted by cube**: an item with bad sizes has no trustworthy cube.
   Validated on real clients without an answer key: self-test (plant known defects into the client's own clean SKUs
   → recall) and consultant review of sampled flags (→ precision).
3. `backfill/` — tiered: derive from other fields → KNN (category, sub-category, TF-IDF of
   description, case dims/weight; log space) → category median. Every value tagged with method and
   confidence. Validation **to be agreed**; masked holdout on real item masters (hide known values,
   predict, measure error) works without an answer key.
4. ~~`profile/`~~ (done) — volumes, design day from SLAs (percentile fallback), peak ratios, order profile, ABC
   velocity, days on hand, turns, inbound. Still to add: growth factors (volume, SKU; shared by every scenario),
   pick frequency (days picked, days between picks), size / weight histograms per SKU and cube / weight per
   order, working hours per day.
The design stage (5–7) is built as **two layers**, so design parameters can be explored live:
   - **computed once per data change, cached:** a per-SKU table (velocity class, units per day, pick frequency,
     sizes and their provenance) and the fit table (units per container and per divider cell, for every
     catalogue container), plus daily demand per channel and the lines of each order;
   - **recomputed on every parameter change (target under 1 s):** slotting, storage, replenishment and
     workstation sizing, as vectorised arithmetic over those tables.
   **Scenarios** are named, saved parameter sets per client (typically 3–5), compared side by side on a page and
   in the report. Shared by every scenario (they describe the client): data, scope, backfilled sizes, service
   targets (SLAs) and growth. Per scenario: system / container, storage rules, slotting rules, operations.
   The goal is to **recommend the best HAI system** for the client (today HaiPick System 2, HaiPick System 3 and
   HaiPick Climb, some of which take larger products), set against competing technologies, and to say plainly
   when HAI is not the best fit.
5. `fit/` — per SKU × container: units that fit by tessellation (6 orientations, then the leftover space along
   each side), by liquid cube at a cell utilisation, and by weight cap; for the whole container and for divider
   cells (1/2, 1/4, 1/8). Starting point: the `pbi/` tool's tessellation.
5b. `slotting/` — which SKUs go where: per system, eligibility rules (fits a cell, weight cap, minimum units per
   bin, suspicious sizes, daily cube against the container), and where the rest go (another HAI system, carton
   flow, pallet flow, bulk / out of scope). Rules first, set per scenario; the order-level split (lines per order
   in each zone, orders completed in one zone) feeds workstation load. Starting point: the `pbi/` tool's slotting.
6. `catalog/` + `sizing/` — technology catalogue (YAML, each parameter with source + confidence: containers with
   inside sizes, weight cap and divider options, storage density, rates, cost); storage per SKU (days of stock by
   velocity class at the design year, totes and partial cells, max totes per SKU), replenishment (units and totes
   per day into the system, replenish point), containers, footprint at clear height, workstations at the
   design-day peak (daily rate first, the hourly peak as a check), rough cost. Growth (volume and SKU) is applied
   here, shared by every scenario.
7. `report/` — comparison scorecard and story across the scenarios; one self-contained HTML file (inline SVG
   charts, light/dark, hover tooltips, "how it's calculated" per chart).

## Commands
```
pip install -e .[dev,app]
python -m gtpfit.app                                                    # workbench, localhost:8501
python -m gtpfit.ingest tests/fixtures/sample_client --wms tests/fixtures/sample_wms_profile.yaml   # -> FOLDER/_ingest
python -m gtpfit.ingest FOLDER --accept "orders:excel_row_limit=client confirmed the extract"   # with a reason
python -m pytest -q
```

## Conventions
- pandas/numpy now; keep functions pure (DataFrame in, DataFrame out) so they port to DuckDB later.
- Deterministic: any random draw takes an explicit seed parameter.
- Docstring at the top of each module saying what it does and what it does not.
- Always pass `encoding="utf-8"` when reading/writing text; Windows defaults to cp1252.
- **What counts as an order line is a per-client choice** (`gtpfit/lines.py`: `row` = every file row,
  `order_sku` = repeated order + SKU rows merged; `picks` keeps the row count). Ingest never merges rows;
  analysis modules take `line_definition` as a parameter and the report states which one was used.
- **Item master required columns:** SKU, unit length / width / height, unit weight and case quantity
  (`schema.py`, `required=True`). A file without one is refused, but blank values inside those columns
  are kept and counted (`blank_values`), not set aside: filling them is `quality/` and `backfill/`'s job.
  Guessing which table a file holds is looser (`schema.could_be`), so a size list lacking a case pack is
  still offered as an item master and ingest then names the missing column.
- Ingest severities: `block` (refuse unless accepted **with a reason**: `accept={"table:code": reason}`;
  the reason goes to `accepted_blocks.csv` and the data receipt) · `warn` · `info`. Rows are never dropped
  silently — they go to `rejects` with a reason. Every issue code needs an `ISSUE_TEXT` entry (a test checks).
  Unit blocks (`unit_ambiguous`, `unit_mismatch`) cannot be accepted (`checks.NOT_ACCEPTABLE`): accepting would
  pass sizes through unconverted, read as inches and pounds. They are resolved by choosing or declaring units.
- **Mappings are per client and automatic** (no WMS-profile picker, no save button). The confirmed column
  mapping, units and date format are saved to the client's `wms_<client>.yaml`; a new file whose headers
  overlap the saved ones (`mapping_reuse_min_overlap`) reuses it ("using last confirmed mapping"). A hand
  correction can be taught globally with "Remember this synonym" (`schema.remember_synonym`). No shared
  cross-client profiles: revisit after about ten clients. `--wms FILE` on the CLI still takes any profile YAML.
- Coverage: orders should span 12 months (`min_coverage_days`) and the peak months (`peak_months`); short or
  peak-less history is a warning, not a block.
- **Scope.** Analysis modules read `tables.load(client)`, never `_ingest/` directly. The scope is a definition
  (`scope:` in client.yaml: included values per column, values seen when it was set, date window and basis,
  a required note when rows are removed), applied by the pure `scope.apply_scope`; no scoped copies are written.
  - **Columns ingest keeps for scope:** the scope fields (`orders.site / channel / order_status / order_type /
    ship_date`, `inventory.site`, `receipts.site`), plus unmapped order text columns with at most
    `extra_max_distinct` values, kept as `x_<name>` with values as sent.
  - **Exclusions:** values containing `scope_exclude_keywords` start excluded ("auto-excluded"). A value that
    first appears after a re-ingest stays out, with a warning, until the scope is reviewed.
  - **Related tables:** the site filter also applies to inventory and receipts when they have a site column,
    and warns when they don't. The waterfall's removed counts plus the in-scope rows always equal all rows,
    and saving writes `_scope/scope_summary.csv`.
- **Unit volume** (`unit_volume`, optional, cubic inches): for masters that give each unit's volume, often instead
  of its sizes. Its unit is only ever read from the header (m3, cm3, ft3, in3, litres) or the client's saved units,
  never guessed from the numbers; without one the values are left blank with `volume_unit_unknown`.
- **Package levels** (`ingest/packlevels.py`): an item master with one row per package (item / case / pallet,
  mapped `pack_level`) becomes one row per SKU: item size from the unit-level row (`pack_unit_levels`, holding 1),
  case pack and case size from the smallest case-level row, pallet quantity and size from the smallest pallet row.
  Every input row is kept in `item_master_levels` with its role and whether it was used; SKUs with no unit-level row
  keep a blank item size. A reused saved mapping also takes exact matches for fields it never mentioned
  (`mapping_with_new_fields`); fields it maps are never changed.
- **A required field wins a contested header** (`suggest_mapping`): a file whose only date is "Ship Date" uses it
  as the order date. Optional date fields (ship date) never block; bad values are left blank with a warning.
- **Thresholds carry their own wording.** Each `IngestParams` field is declared with `params.setting()`:
  block (`client` / `detection`), label, unit, range, and a three-part tooltip (what it does · why the default ·
  raise / lower), plus source and confidence. The page draws its controls and tooltips from this metadata
  (`widgets.control`); a test fails if a field lacks it. Write tooltips in general terms, never naming a client
  or its data. A new field needs a block, or it appears nowhere.
- **Confirm step settings:**
  - **Client settings** (always shown): peak months, longest order gap, unusable-row share, assortment type,
    SKU case sensitivity, order-line definition. Assortment type is a preset (`params.ASSORTMENTS`) that sets
    the three unit sanity bands together; the bands are never edited one by one.
  - **Detection settings** (collapsed): Excel row limits, receipts gap, snapshot gap factor, minimum history,
    date-parse share, header-match strictness, mapping-reuse overlap, peak-hint threshold.
  - Both are saved under `settings:` in the client's `client.yaml`, only the values that differ from the
    defaults.
- **Busiest-months hint** (`coverage.busiest_months`): months at `peak_hint_ratio` × the median month or more,
  from order lines per covered day, averaged across years; it needs all 12 months. It is a hint only: it never
  sets `peak_months`, and the coverage check does not use it.
