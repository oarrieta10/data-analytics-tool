"""pipeline.py — read, map, type, convert and check client files into canonical tables.

    ingest_table(sources, table, profile=None, params=None, accept=()) -> TableResult
    ingest_sources({table: [Source]}, profile=None, params=None, accept=None, strict=True,
                   on_progress=None) -> IngestResult
    ingest_folder(folder, profile=None, params=None, accept=None, strict=True) -> IngestResult
    write_outputs(result, out_dir, client=None, detections=None, requested=None, folder=None) -> Path

accept: {"table:code" or "code": reason} (or {key: {"reason", "at"}}); a block is
only accepted with a reason, and the reason travels to accepted_blocks.csv and
the data receipt.

Canonical columns and units are in schema.py (inches, pounds, dates, seconds).
Rows that cannot be used (zero/negative quantity, blank or unreadable key or
date) go to `rejects` with a reason; nothing is dropped silently.  Problems
that make the data untrustworthy are "block" issues: ingest_folder raises
IngestError unless the consultant accepts them, each with a reason.

Does not judge item-level data quality (placeholders, outliers, single rows in
the wrong unit, duplicate SKUs, orphans) — that is quality/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import coerce
from .checks import (Issue, accepted_for, fmt_day, issues_frame, missing_day_runs, normalise_accept,
                     snapshot_gaps)
from .coverage import Coverage, coverage as order_coverage
from .mapping import Mapping, WmsProfile, mapping_from_profile, save_wms_profile, suggest_mapping
from .params import IngestParams
from .classify import classify, list_sources
from .read import read_headers, read_raw
from .readers import Source, as_source
from .schema import KEEP_BLANK, REQUIRED_TABLES, TABLES
from .units import LENGTH_TO_IN, WEIGHT_TO_LB, assess, describe, header_unit

_DATE_FIELD = {"orders": "order_date", "receipts": "receipt_date", "inventory": "snapshot_date"}


class IngestError(Exception):
    def __init__(self, result):
        self.result = result
        b = result.issues[result.issues.severity == "block"]
        lines = [f"  [{r.table}] {r.code}: {r.message}" for r in b.itertuples()]
        super().__init__("Ingest refused the data:\n" + "\n".join(lines) +
                         "\nFix the files, or accept a block you have checked with a reason: "
                         "accept={'table:code': 'reason'}.")


@dataclass
class TableResult:
    name: str
    paths: list
    table: pd.DataFrame | None = None
    rejects: pd.DataFrame | None = None
    issues: list = field(default_factory=list)
    units: pd.DataFrame | None = None
    unit_evidence: pd.DataFrame | None = None   # every unit pair tried, with implied medians
    mapping: Mapping | None = None
    date_format: str | None = None

    @property
    def blocked(self) -> bool:
        return any(i.severity == "block" for i in self.issues)


@dataclass
class IngestResult:
    tables: dict
    rejects: dict
    issues: pd.DataFrame
    units: pd.DataFrame
    mappings: dict
    sources: dict
    date_formats: dict
    unit_evidence: pd.DataFrame | None = None
    inputs: dict = field(default_factory=dict)   # table -> [Source] actually ingested
    coverage: Coverage | None = None             # period the orders cover (coverage.py)
    acceptances: dict = field(default_factory=dict)  # normalised accept: key -> {"reason", "at"}

    @property
    def blocked(self) -> bool:
        return bool((self.issues["severity"] == "block").any())

    @property
    def accepted(self) -> list:
        """Blocks the consultant accepted: table, code, message, reason, accepted_at."""
        out = []
        for r in self.issues[self.issues["severity"] == "accepted"].itertuples():
            a = accepted_for(self.acceptances, r.table, r.code) or {}
            out.append(dict(table=r.table, code=r.code, message=r.message, reason=a.get("reason", ""),
                            accepted_at=a.get("at", "")))
        return out


# ---- one table ------------------------------------------------------------------

def ingest_table(sources, table: str, profile: WmsProfile | None = None, params: IngestParams | None = None,
                 accept=None) -> TableResult:
    params = params or IngestParams()
    sheet = profile.sheets.get(table) if profile else None
    paths = [as_source(p, sheet) for p in (sources if isinstance(sources, (list, tuple)) else [sources])]
    res = TableResult(table, [str(p) for p in paths])
    acc = normalise_accept(accept)

    def add(code, severity, message, count=None, share=None, fld=None):
        if severity == "block" and accepted_for(acc, table, code):
            severity = "accepted"
        res.issues.append(Issue(table, code, severity, message,
                                None if count is None else int(count),
                                None if share is None else float(share), fld))

    # -- headers first: the same layout is required to stack files
    headers, dupes = None, set()
    for p in paths:
        h = read_headers(p)
        dupes |= {x for i, x in enumerate(h) if x in h[:i]}
        if headers is not None and h != headers:
            add("header_mismatch", "block", f"{p} has different columns from {paths[0]}; "
                "files for one table must share a layout.")
            return res
        headers = h

    # -- columns
    if profile and table in profile.columns:
        m = mapping_from_profile(headers, table, profile.columns[table])
    else:
        m = suggest_mapping(headers, table, params)
        add("auto_mapped", "info", "Columns matched automatically: " +
            "; ".join(f"{f} <- '{h}'" + (" (fuzzy)" if m.method[f] == "fuzzy" else "") for f, h in m.mapping.items()) +
            ". Save them as a WMS profile once confirmed.")
        fuzzy = {f: h for f, h in m.mapping.items() if m.method[f] == "fuzzy"}
        if fuzzy:
            add("fuzzy_header", "warn", "Matched on similar names only: " +
                "; ".join(f"{f} <- '{h}'" for f, h in fuzzy.items()) + ". Check them.")
        for f, hs in m.alternatives.items():
            add("alternative_columns", "info", f"{f} uses '{m.mapping[f]}'; also possible: {hs}.", fld=f)
    res.mapping = m
    used_dupes = sorted(dupes & set(m.mapping.values()))
    if used_dupes:
        add("duplicate_header", "block", f"{paths[0]} repeats column names {used_dupes} that the mapping uses; "
            "it is unclear which one holds the data. Rename one in the file or the WMS profile.")
        return res
    if dupes:
        add("duplicate_header", "info", f"{paths[0]} repeats column names {sorted(dupes)} (e.g. joined database "
            "tables); not used, so ignored.")
    req = {f.name for f in TABLES[table] if f.required}
    for f, hs in m.ambiguous.items():
        add("ambiguous_column", "block" if f in req else "warn",
            f"{f} could be any of {hs}; name it in the WMS profile.", fld=f)
    missing = [f for f in m.missing_required if f not in m.ambiguous]
    if missing:
        add("missing_required_column", "block", f"No column found for required field(s) {missing}. "
            f"Columns in the file: {headers}.")
    if m.missing_required:
        return res
    if m.missing_optional:
        add("optional_columns_absent", "info", f"Not in the file: {m.missing_optional}.")

    # -- read only the mapped columns; drop blank rows; spreadsheet row limits
    used = list(dict.fromkeys(m.mapping.values()))
    frames = []
    for p in paths:
        df, meta = read_raw(p, columns=used)
        if meta["encoding"] not in (None, "utf-8"):
            add("encoding_fallback", "info", f"{p} is not UTF-8; read as {meta['encoding']}.")
        n = meta["data_rows"]
        df["_file"], df["_row"] = str(p), range(2, n + 2)  # spreadsheet row number (header is row 1)
        blank = df[used].isna().all(axis=1)
        k = int(blank.sum())
        if n - k in params.excel_row_limits:
            add("excel_row_limit", "block", f"{p} has exactly {n - k:,} data rows, a spreadsheet row limit, so "
                "the export was probably cut off. Re-export from the WMS as CSV, or accept if the file truly ends "
                "there.", count=n - k)
        elif n in params.excel_row_limits and k:
            add("excel_padded", "warn", f"{p} runs to the spreadsheet row limit ({n:,} rows) but {k:,} of them "
                f"are blank; only {n - k:,} hold data. Check it is the full extract.", count=k)
        if k:
            add("blank_rows", "info", f"{k:,} blank rows in {p} ignored (e.g. a leftover spreadsheet range).",
                count=k)
        frames.append(df.loc[~blank])
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0].reset_index(drop=True)
    if len(df) == 0:
        add("empty_table", "block", f"{paths[0]} has a header but no rows.")
        return res
    n_rows = len(df)

    # -- types
    out = pd.DataFrame(index=df.index)
    reason = pd.Series(pd.NA, index=df.index, dtype="string")
    reason_field = {}

    def reject(mask, why, fname):
        new = mask & reason.isna()
        reason[new] = why
        if new.any():
            reason_field.setdefault(why, set()).add(fname)

    date_ok = True
    has_cases = None
    if "cases" in m.mapping:
        has_cases = (coerce.to_number(df[m.mapping["cases"]])[0] > 0).fillna(False)
    for f in TABLES[table]:
        if f.name not in m.mapping:
            continue
        col = df[m.mapping[f.name]]
        blank_ok = f.name in KEEP_BLANK.get(table, ())  # required column, but blank values are kept
        if f.kind == "id":
            vals, changed = coerce.normalise_ids(col, upper=params.upper_case_skus and f.name == "sku")
            if f.name == "sku" and changed:
                add("sku_normalised", "info", f"{changed:,} SKU spellings trimmed or upper-cased to match "
                    "the rest (e.g. ' ab123 ' -> 'AB123').", count=changed, fld=f.name)
            out[f.name] = vals
        elif f.kind == "text":
            out[f.name] = col.astype("string")
        elif f.kind in ("length", "weight", "count", "qty", "stock", "cases"):
            num, failed = coerce.to_number(col)
            if failed.any():
                ex = col[failed].drop_duplicates().head(3).tolist()
                if f.required and not blank_ok:
                    reject(failed, "unparseable", f.name)
                else:
                    add("unparseable_values", "warn", f"{int(failed.sum()):,} {f.name} values are not numbers "
                        f"(e.g. {ex}); left blank.", count=failed.sum(), share=failed.mean(), fld=f.name)
            if f.kind in ("qty", "stock", "cases"):
                if f.kind != "cases":
                    bad = ((num <= 0) if f.kind == "qty" else (num < 0)).fillna(False)
                    if f.kind == "qty" and has_cases is not None:
                        bad &= ~has_cases  # full-case line: loose units may be 0 or blank
                    reject(bad, "bad_qty", f.name)
                whole = num.dropna()
                if len(whole) and (whole % 1 == 0).all():
                    num = num.astype("Int64")
            out[f.name] = num
        elif f.kind == "date":
            declared = profile.date_format if profile else None
            fmt, status, fits = coerce.choose_date_format(col, declared, params.min_date_parse_share)
            best = sorted(fits.items(), key=lambda kv: -kv[1])[:2]
            best_txt = ", ".join(f"{k} ({v:.0%})" for k, v in best)
            ex = col.dropna().drop_duplicates().head(3).tolist()
            if status == "mismatch":
                add("date_format_mismatch", "block", f"Declared date format {declared} parses only "
                    f"{fits.get(declared, 0):.0%} of {f.name} values (e.g. {ex}); best fits: {best_txt}.", fld=f.name)
            elif status == "ambiguous":
                good = [k for k, v in fits.items() if v >= params.min_date_parse_share]
                add("date_format_ambiguous", "block", f"{f.name} values (e.g. {ex}) fit more than one format: "
                    f"{good}. Declare date_format in the WMS profile.", fld=f.name)
            elif status == "none":
                add("date_unparseable", "block", f"No known date format fits {f.name} (e.g. {ex}); "
                    f"best: {best_txt}.", fld=f.name)
            if fmt is None:
                date_ok = False
                out[f.name] = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
                continue
            if status == "detected":
                add("date_format_detected", "info", f"{f.name} read as {fmt}.", fld=f.name)
            elif status == "native":
                add("date_format_detected", "info", f"{f.name} is stored as dates in the file.", fld=f.name)
            res.date_format = fmt
            vals, failed = coerce.to_date(col, fmt)
            if f.required:
                reject(failed, "unparseable", f.name)
            out[f.name] = vals
        elif f.kind == "time":
            secs, failed = coerce.to_seconds(col)
            if failed.any():
                add("unparseable_values", "warn", f"{int(failed.sum()):,} {f.name} values are not times; left blank.",
                    count=failed.sum(), share=failed.mean(), fld=f.name)
            out[f.name] = secs
        if f.required and blank_ok:
            n_blank = int(col.isna().sum())
            if n_blank:
                add("blank_values", "info", f"{n_blank:,} rows ({n_blank / n_rows:.1%}) have no {f.name}; kept, "
                    "not set aside.", count=n_blank, share=n_blank / n_rows, fld=f.name)
        elif f.required:
            blank = col.isna() & ~has_cases if (f.kind == "qty" and has_cases is not None) else col.isna()
            reject(blank, "missing_value", f.name)

    # -- rows set aside
    for why, label in [("bad_qty", "zero or negative quantity"), ("missing_value", "a blank required field"),
                       ("unparseable", "an unreadable required value")]:
        k = int((reason == why).sum())
        if k:
            share = k / n_rows
            sev = "block" if share > params.max_bad_row_share else "warn"
            add(why, sev, f"{k:,} rows ({share:.2%}) have {label} in {sorted(reason_field[why])} and were set "
                f"aside{'' if sev == 'warn' else f'; more than {params.max_bad_row_share:.0%} means the extract is suspect'}.",
                count=k, share=share)
    keep = reason.isna()
    rej = df.loc[~keep].copy()
    rej.insert(0, "reason", reason[~keep])
    res.rejects = rej.reset_index(drop=True)
    out = out.loc[keep].reset_index(drop=True)
    if table == "orders" and "cases" in out:
        out["eaches"] = out["qty"]
        out["full_case"] = (out["cases"].fillna(0) > 0)
        add("cases_need_item_master", "info", "Full-case lines found ('cases' column): qty here is loose units "
            "only until case packs from the item master are applied (ingest_folder does this).", fld="cases")

    # -- units (item master)
    if table == "item_master":
        out, res.units, res.unit_evidence = _units(out, m, profile, params, add)

    # -- dates: range, gaps, closures
    dfield = _DATE_FIELD.get(table)
    if dfield in out and date_ok and out[dfield].notna().any():
        d = out[dfield]
        add("date_range", "info", f"{fmt_day(d.min())} to {fmt_day(d.max())} "
            f"({(d.max() - d.min()).days + 1} days, {d.nunique()} with rows).", fld=dfield)
        if table in params.max_gap_days:
            runs = missing_day_runs(d)
            lim = params.max_gap_days[table]
            long = [r for r in runs if r[2] > lim]
            short = [r for r in runs if r[2] <= lim]
            if long:
                add("date_gap", "block", f"No rows for {len(long)} stretch(es) longer than {lim} days: " +
                    "; ".join(f"{fmt_day(a)} to {fmt_day(b)} ({k} days)" for a, b, k in long[:5]) +
                    ". The extract may be missing data.", count=sum(r[2] for r in long), fld=dfield)
            if short:
                add("closed_days", "info", f"{sum(r[2] for r in short)} day(s) with no rows in short runs "
                    f"(closures, weekends), e.g. " + ", ".join(fmt_day(a) for a, _, _ in short[:5]) + ".",
                    count=sum(r[2] for r in short), fld=dfield)
        if table == "inventory":
            med, wide = snapshot_gaps(d, params.snapshot_gap_factor)
            if wide:
                add("snapshot_gap", "block", f"Snapshots are usually {med:.0f} days apart, but: " +
                    "; ".join(f"{fmt_day(a)} to {fmt_day(b)} ({k} days)" for a, b, k in wide[:5]) + ".",
                    count=len(wide), fld=dfield)
    res.table = out
    return res


def _units(out, m, profile, params, add):
    rows, evidence = [], []
    for g in ("unit", "case"):
        gf = [f for f in TABLES["item_master"] if f.group == g and f.name in out]
        if not gf:
            continue
        dims = [f.name for f in gf if f.kind == "length"]
        wt = next((f.name for f in gf if f.kind == "weight"), None)
        decl = {}
        for kind in ("length", "weight"):
            hints = {header_unit(m.mapping[f.name], kind) for f in gf if f.kind == kind} - {None}
            if len(hints) > 1:
                add("unit_conflict", "block", f"{g} {kind} headers name different units {sorted(hints)}.")
                return out, pd.DataFrame(rows), pd.DataFrame()
            if hints:
                decl[kind] = (hints.pop(), "header")
            elif profile and profile.units.get(kind):
                decl[kind] = (profile.units[kind], "profile")
        a = assess(out, dims if len(dims) == 3 else [], wt, g, params)
        evidence.append(a.assign(group=g, declared_length=decl.get("length", (None,))[0],
                                 declared_weight=decl.get("weight", (None,))[0]))
        cand = a
        for kind in ("length", "weight"):
            if kind in decl:
                cand = cand[cand[kind] == decl[kind][0]]
        fits = a[a.plausible & a.evidence]
        fits_txt = "; ".join(describe(r) for r in fits.itertuples()) or "none"
        chosen = None
        if len(decl) == 2:
            row = cand.iloc[0]
            chosen = row
            if row.evidence and not row.plausible:
                if len(fits):
                    add("unit_mismatch", "block", f"{g}: declared {describe(row)} looks wrong for consumer goods; "
                        f"the data fits {fits_txt}.")
                    chosen = None
                else:
                    add("unit_implausible", "warn", f"{g}: declared {describe(row)} is outside the usual range, "
                        "and no other unit pair fits better; kept as declared.")
        else:
            plaus = cand[cand.plausible & cand.evidence]
            undeclared = [k for k in ("length", "weight") if k not in decl]
            if len(plaus) == 1:
                chosen = plaus.iloc[0]
                add("units_statistical", "warn", f"{g}: {', '.join(undeclared)} unit not stated; the data fits only "
                    f"{describe(chosen)}. Declare units in the WMS profile to confirm.")
            elif len(plaus) > 1:
                add("unit_ambiguous", "block", f"{g}: {', '.join(undeclared)} unit not stated and the data fits "
                    f"several: " + "; ".join(describe(r) for r in plaus.itertuples()) +
                    ". Declare units in the WMS profile or the headers.")
            else:
                add("unit_ambiguous", "block", f"{g}: {', '.join(undeclared)} unit not stated and no unit pair "
                    f"looks like consumer goods (fits overall: {fits_txt}). Declare units.")
        if chosen is None:
            continue
        lu, wu = chosen.length, chosen.weight
        methods = {decl.get(k, (None, "statistical"))[1] for k in ("length", "weight")}
        for f in dims:
            out[f] = out[f] * LENGTH_TO_IN[lu]
        if wt:
            out[wt] = out[wt] * WEIGHT_TO_LB[wu]
        add("units", "info", f"{g}: {lu}/{wu} from {' + '.join(sorted(methods))}; {describe(chosen)}.")
        if (lu, wu) != ("in", "lb"):
            add("unit_converted", "info", f"{g} dimensions converted {lu} -> in and weight {wu} -> lb.")
        rows.append(dict(group=g, length=lu, weight=wu, method="+".join(sorted(methods)),
                         plausible=bool(chosen.plausible), median_longest_in=chosen.median_longest_in,
                         median_density_lb_ft3=chosen.median_density_lb_ft3))
    return out, pd.DataFrame(rows, columns=["group", "length", "weight", "method", "plausible",
                                            "median_longest_in", "median_density_lb_ft3"]), \
        (pd.concat(evidence, ignore_index=True) if evidence else pd.DataFrame())


# ---- several tables: confirmed sources, or a folder ----------------------------------

def discover(paths, profile=None, params=None, add=None) -> dict:
    """{table: [Source]} for files (and their sheets / tables), by WMS profile patterns or by classify()."""
    add = add or (lambda *a: None)
    files = sorted(Path(p) for p in paths)
    found: dict[str, list] = {}
    if profile and profile.files:
        for table, pattern in profile.files.items():
            hits = sorted(p for p in files if p.match(pattern))
            if hits:
                found[table] = [Source(p, profile.sheets.get(table)) for p in hits]
        return found
    for src in list_sources(files):
        g = classify(src, params)
        if g.table is None:
            add("", "unrecognised_file", "warn", f"{src} does not look like any table; ignored.")
        elif g.confidence == "low":
            add("", "ambiguous_file", "block", f"{src} could be {g.ranking[0][0]} or {g.ranking[1][0]}; "
                "assign it yourself (workbench) or list it under files: in a WMS profile.")
        else:
            found.setdefault(g.table, []).append(src)
    return found


def ingest_sources(assign: dict, profile: WmsProfile | None = None, params: IngestParams | None = None,
                   accept=None, strict: bool = True, _issues=None, where: str = "the inputs",
                   on_progress=None) -> IngestResult:
    """Ingest confirmed sources: {table: [Source or path]}.  Missing tables are reported like a folder run.

    on_progress(event) is called before and after each table with
    {"table", "index", "total", "stage": "start" | "done", "rows"}.
    """
    params = params or IngestParams()
    acc = normalise_accept(accept)
    issues: list[Issue] = list(_issues or [])
    progress = on_progress or (lambda e: None)

    def add(table, code, severity, message, count=None, share=None):
        if severity == "block" and accepted_for(acc, table, code):
            severity = "accepted"
        issues.append(Issue(table, code, severity, message, count, share))

    tables, rejects, units, mappings, sources, dfmt, evid, inputs = {}, {}, [], {}, {}, {}, None, {}
    for i, table in enumerate(TABLES):
        if not assign.get(table):
            add(table, "table_missing", "block" if table in REQUIRED_TABLES else "warn",
                f"No {table} file found in {where}.")
            continue
        progress(dict(table=table, index=i, total=len(TABLES), stage="start", rows=None))
        r = ingest_table(assign[table], table, profile, params, acc)
        progress(dict(table=table, index=i, total=len(TABLES), stage="done",
                      rows=None if r.table is None else len(r.table)))
        issues += r.issues
        sources[table] = ", ".join(r.paths)
        inputs[table] = [as_source(x) for x in assign[table]]
        if r.mapping is not None:
            mappings[table] = r.mapping
        if r.table is not None:
            tables[table], rejects[table] = r.table, r.rejects
            if r.date_format:
                dfmt[table] = r.date_format
        if r.units is not None and len(r.units):
            units.append(r.units.assign(table=table))
        if r.unit_evidence is not None and len(r.unit_evidence):
            evid = r.unit_evidence
    if "orders" in tables and "cases" in tables["orders"]:
        tables["orders"] = _apply_case_packs(tables["orders"], tables.get("item_master"), issues)
    cov = None
    if "orders" in tables and len(tables["orders"]):
        cov = order_coverage(tables["orders"], params)
        if cov.short:
            add("orders", "coverage_short", "warn", cov.warnings[0], count=cov.span_days)
        if not cov.peak_covered:
            add("orders", "peak_not_covered", "warn", cov.warnings[-1])
    res = IngestResult(tables, rejects, issues_frame(issues),
                       pd.concat(units, ignore_index=True) if units else pd.DataFrame(), mappings, sources, dfmt,
                       evid, inputs, cov, acc)
    if strict and res.blocked:
        raise IngestError(res)
    return res


def ingest_folder(folder, profile: WmsProfile | None = None, params: IngestParams | None = None,
                  accept=None, strict: bool = True, on_progress=None) -> IngestResult:
    """Every readable file in the folder (not subfolders), assigned by WMS profile or by classify()."""
    folder = Path(folder)
    params = params or IngestParams()
    acc = normalise_accept(accept)
    pre: list[Issue] = []

    def add(table, code, severity, message):
        if severity == "block" and accepted_for(acc, table, code):
            severity = "accepted"
        pre.append(Issue(table, code, severity, message))

    files = [p for p in folder.iterdir() if p.is_file()]
    found = discover(files, profile, params, add)
    return ingest_sources(found, profile, params, acc, strict, _issues=pre, where=str(folder),
                          on_progress=on_progress)


def _apply_case_packs(o: pd.DataFrame, im: pd.DataFrame | None, issues: list) -> pd.DataFrame:
    """qty = loose units + cases x case pack.  Case lines with no case pack keep a blank qty, flagged."""
    issues[:] = [i for i in issues if not (i.table == "orders" and i.code == "cases_need_item_master")]
    o = o.copy()
    cases = o["cases"].fillna(0).astype(float)
    full = cases > 0
    if im is None or "case_qty" not in im:
        issues.append(Issue("orders", "case_pack_missing", "warn", f"{int(full.sum()):,} full-case lines but no "
                            "item-master case packs; their unit quantity is left blank.", int(full.sum())))
        cq = pd.Series(float("nan"), index=o.index)
    else:
        packs = im.drop_duplicates("sku").set_index("sku")["case_qty"]
        cq = o["sku"].map(packs).astype(float)
    unknown = full & ~(cq > 0)
    qty = o["eaches"].fillna(0).astype(float) + cases * cq.where(cq > 0)
    qty[~full] = o.loc[~full, "eaches"].astype(float)
    whole = qty.dropna()
    o["qty"] = qty.astype("Int64") if len(whole) and (whole % 1 == 0).all() else qty
    issues.append(Issue("orders", "cases_converted", "info", f"{int(full.sum()):,} lines were picked as full cases; "
                        "units = loose units + cases x case pack.", int(full.sum()), float(full.mean()), "cases"))
    if unknown.any() and im is not None:
        issues.append(Issue("orders", "case_pack_missing", "warn", f"{int(unknown.sum()):,} full-case lines "
                            f"({o.loc[unknown, 'sku'].nunique():,} SKUs) have no case pack in the item master; their "
                            "unit quantity is left blank for backfill to estimate.", int(unknown.sum()),
                            float(unknown.mean()), "cases"))
    lead = [c for c in o.columns if c not in ("eaches", "cases", "full_case")]
    return o[lead + ["eaches", "cases", "full_case"]]


def profile_from_result(result: IngestResult, name: str) -> WmsProfile:
    """A WMS profile draft from what this run used — review it, then save it."""
    u = result.units
    units = {}
    if len(u):
        r = u.iloc[0]
        units = {"length": r["length"], "weight": r["weight"]}
    fmts = set(result.date_formats.values()) - {"native"}
    first = {t: srcs[0] for t, srcs in result.inputs.items() if srcs}
    return WmsProfile(name=name, units=units, date_format=fmts.pop() if len(fmts) == 1 else None,
                      files={t: s.path.name for t, s in first.items()},
                      sheets={t: s.part for t, s in first.items() if s.part},
                      columns={t: dict(m.mapping) for t, m in result.mappings.items()})


def write_outputs(result: IngestResult, out_dir, client: str | None = None, detections=None,
                  requested=None, folder=None) -> Path:
    """Canonical tables as Parquet, plus issues, units, rejects, a WMS profile draft, and the client-facing
    files: accepted_blocks.csv, rejects_for_client.csv and data_receipt.html.

    requested: issue keys ("table:code") the consultant has asked the client to resend.
    folder: the folder the files were read from, recorded in the receipt.
    """
    from .exports import accepted_blocks, client_rejects
    from .receipt import data_receipt
    import duckdb
    out = Path(out_dir)
    (out / "rejects").mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    for name, df in result.tables.items():
        con.register("t", df)
        target = (out / f"{name}.parquet").as_posix().replace("'", "''")
        con.execute(f"COPY t TO '{target}' (FORMAT parquet)")
        con.unregister("t")
    for name, df in result.rejects.items():
        if len(df):
            df.to_csv(out / "rejects" / f"{name}.csv", index=False, encoding="utf-8")
    result.issues.to_csv(out / "issues.csv", index=False, encoding="utf-8")
    result.units.to_csv(out / "units.csv", index=False, encoding="utf-8")
    save_wms_profile(profile_from_result(result, "draft"), out / "wms_profile_draft.yaml")
    accepted_blocks(result).to_csv(out / "accepted_blocks.csv", index=False, encoding="utf-8")
    client_rejects(result).to_csv(out / "rejects_for_client.csv", index=False, encoding="utf-8")
    (out / "data_receipt.html").write_text(data_receipt(result, client, detections, requested, folder), encoding="utf-8")
    con.close()
    return out
