"""scope.py — which part of the client's operation the analysis designs for: site, channel, order type,
order status, any extra low-cardinality order column (x_...), and a date window.

    detect_scope_columns(orders, params) -> [ScopeColumn]   what can be filtered, with lines per value
    default_spec(columns, params) -> ScopeSpec               everything in, except auto-excluded values
    apply_scope(tables, spec, params) -> ScopeResult         scoped tables, waterfall, warnings, summary
    impact(orders_all, orders_in, line_definition) -> DataFrame   before / after numbers (cheap aggregates)
    save_scope(client, spec) / load_scope(client)            the spec under scope: in the client's client.yaml

A scope is a definition, not a copy of the data: it is saved in client.yaml
and applied whenever the data is loaded (gtpfit.tables.load), so re-running
ingest never leaves stale scoped files behind.  Nothing disappears silently:
every row a filter removes is counted in the waterfall, and saving writes
scope_summary.csv (filter, value, rows removed, reason) to the client's
workspace.  The spec lists the values included per column, so a value that
first appears after a re-ingest (a new site) stays out, with a warning, until
the consultant reviews the scope.

Does not: change or drop anything in the ingest outputs, judge data quality,
or profile the operation (profile/ does that on the scoped tables).
Pure functions (DataFrames in, DataFrames out) apart from save_scope / load_scope.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from gtpfit.ingest.params import setting
from gtpfit.ingest.schema import FIELD_LABEL
from gtpfit.lines import define_lines

KNOWN = ("site", "channel", "order_status", "order_type")  # canonical scope fields, offered first
BLANK = "(blank)"                                         # how a missing value is shown and stored
RELATED = {"inventory": "stock", "receipts": "receipts"}  # tables the site filter also applies to
_CONSULTANT = "Consultant judgement"


@dataclass
class ScopeParams:
    scope_max_distinct: int = setting(
        30, block="detection", unit="count", lo=1, hi=500, step=1, label="Filter columns with up to",
        what="An extra order column is offered as a scope filter when it holds at most this many different "
             "values.",
        why="enough for sites, channels, statuses, pick areas and carriers, while long lists such as "
            "customers are left out",
        effect="Raise it to offer columns with more values (longer lists to tick); lower it to offer fewer.",
        source=_CONSULTANT, confidence="medium")
    scope_exclude_keywords: tuple = setting(
        ("cancel", "return", "void", "test"), block="detection", unit="words",
        label="Leave out values containing",
        what="Values containing any of these words (any case) start unticked, marked auto-excluded; they can "
             "be ticked back in.",
        why="cancelled, returned, voided and test orders are not picked work to design for",
        effect="Add a word to leave out more by default (e.g. “sample”); remove one to start with "
               "those values included.",
        source=_CONSULTANT, confidence="medium")


def params_from(diff: dict | None) -> ScopeParams:
    d = {k: (tuple(v) if isinstance(v, list) else v) for k, v in (diff or {}).items()
         if k in {f.name for f in dataclasses.fields(ScopeParams)}}
    return ScopeParams(**d)


def params_diff(params: ScopeParams) -> dict:
    base = dataclasses.asdict(ScopeParams())
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in dataclasses.asdict(params).items()
            if v != base[k]}


# ---- what can be filtered ----------------------------------------------------------------------------

@dataclass
class ScopeValue:
    value: str
    lines: int
    orders: int
    share: float                   # of all order lines
    auto_excluded: str | None = None   # the keyword that matched, if any


@dataclass
class ScopeColumn:
    name: str
    label: str
    values: list
    known: bool                    # a canonical field (site, channel, ...) rather than an x_ extra


def column_label(name: str) -> str:
    if name.startswith("x_"):
        return name[2:].replace("_", " ").capitalize() + " (extra column)"
    return FIELD_LABEL.get(name, name)


def _keys(s: pd.Series) -> pd.Series:
    """Values as scope sees them: text, blank shown as BLANK."""
    return s.astype("string").fillna(BLANK)


def keyword_hit(value: str, keywords) -> str | None:
    v = str(value).lower()
    return next((k for k in keywords if k and k.lower() in v), None)


def detect_scope_columns(orders: pd.DataFrame, params: ScopeParams | None = None) -> list[ScopeColumn]:
    """Known scope fields first (site, channel, order status, order type), then x_ extras with at most
    params.scope_max_distinct values.  Each value: lines, orders, share of lines, auto-exclusion keyword."""
    params = params or ScopeParams()
    n = len(orders)
    names = [c for c in KNOWN if c in orders and orders[c].notna().any()]
    names += [c for c in orders if str(c).startswith("x_") and orders[c].nunique() <= params.scope_max_distinct]
    out = []
    for c in names:
        k = _keys(orders[c])
        g = pd.DataFrame({"v": k, "o": orders["order_id"]}).groupby("v", sort=False)
        stats = g.agg(lines=("o", "size"), orders=("o", "nunique")).sort_values("lines", ascending=False)
        vals = [ScopeValue(str(v), int(r.lines), int(r.orders), r.lines / n if n else 0.0,
                           keyword_hit(v, params.scope_exclude_keywords)) for v, r in stats.iterrows()]
        out.append(ScopeColumn(c, column_label(c), vals, c in KNOWN))
    return out


# ---- the definition -------------------------------------------------------------------------------------

@dataclass
class ScopeSpec:
    include: dict = field(default_factory=dict)   # column -> [values kept]; columns not listed are not filtered
    seen: dict = field(default_factory=dict)      # column -> every value present when the scope was set
    date_from: str | None = None                  # ISO date, inclusive
    date_to: str | None = None
    date_basis: str = "order_date"                # or "ship_date" when mapped
    note: str = ""
    saved_at: str = ""
    params: dict = field(default_factory=dict)    # ScopeParams values that differ from the defaults

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ScopeSpec":
        names = {f.name for f in dataclasses.fields(cls)}
        d = {k: v for k, v in (d or {}).items() if k in names}
        for k in ("include", "seen"):
            d[k] = {c: [str(v) for v in vs] for c, vs in (d.get(k) or {}).items()}
        for k in ("date_from", "date_to"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        return cls(**d)


def default_spec(columns: list, params: ScopeParams | None = None) -> ScopeSpec:
    """Every value in, except those matching an exclusion keyword."""
    params = params or ScopeParams()
    inc = {}
    for c in columns:
        if any(v.auto_excluded for v in c.values):
            inc[c.name] = [v.value for v in c.values if not v.auto_excluded]
    return ScopeSpec(include=inc, seen={c.name: [v.value for v in c.values] for c in columns},
                     params=params_diff(params))


# ---- applying it -------------------------------------------------------------------------------------------

@dataclass
class ScopeResult:
    tables: dict
    waterfall: pd.DataFrame            # filter, removed, remaining (removed + final remaining = all lines)
    summary: pd.DataFrame              # filter, value, rows_removed, reason
    warnings: list = field(default_factory=list)
    related: dict = field(default_factory=dict)   # table -> one line on what the site filter did to it

    @property
    def lines_all(self) -> int:
        return int(self.waterfall["remaining"].iat[0])

    @property
    def lines_in(self) -> int:
        return int(self.waterfall["remaining"].iat[-1])


def _fmt_values(vals, most=3) -> str:
    vals = list(vals)
    return ", ".join(vals[:most]) + (f" and {len(vals) - most} more" if len(vals) > most else "")


def apply_scope(tables: dict, spec: ScopeSpec | None, params: ScopeParams | None = None) -> ScopeResult:
    """Orders filtered by each column in spec.include (in order), then the date window; the site filter also
    applies to inventory and receipts when they have a site column.  The item master is never filtered."""
    spec = spec or ScopeSpec()
    params = params or params_from(spec.params)
    out = dict(tables)
    o = tables["orders"]
    n0 = len(o)
    steps = [dict(filter="All lines", removed=0, remaining=n0)]
    summary, warnings, related = [], [], {}
    for col, vals in spec.include.items():
        label = column_label(col)
        if col not in o:
            warnings.append(f"The saved scope filters on {label}, which the orders no longer have; that filter is "
                            "skipped.")
            continue
        k = _keys(o[col])
        present = list(dict.fromkeys(k))
        gone = [v for v in vals if v not in present]
        if gone:
            warnings.append(f"The saved scope includes {label} value(s) no longer in the data: {_fmt_values(gone)}. "
                            "Review the scope.")
        seen = spec.seen.get(col)
        new = [v for v in present if seen is not None and v not in seen and v not in vals]
        if new:
            warnings.append(f"New {label} value(s) since the scope was saved, left out until you review it: "
                            f"{_fmt_values(new)}.")
        keep = k.isin(vals)
        for v, cnt in k[~keep].value_counts(sort=False).items():
            hit = keyword_hit(v, params.scope_exclude_keywords)
            why = ("not in the saved scope (new value)" if v in new else
                   f"auto-excluded (contains “{hit}”)" if hit else "excluded by the consultant")
            summary.append(dict(filter=label, value=str(v), rows_removed=int(cnt), reason=why))
        dropped = [v for v in present if v not in vals]
        name = (f"{label} = {_fmt_values(vals)}" if len(vals) <= 2 and len(dropped) > len(vals)
                else f"{label} excludes {_fmt_values(dropped)}")
        o = o[keep.to_numpy()]
        steps.append(dict(filter=name, removed=int((~keep).sum()), remaining=len(o)))
    if spec.date_from or spec.date_to:
        basis = spec.date_basis if spec.date_basis in o else "order_date"
        if basis != spec.date_basis:
            warnings.append(f"The saved scope dates by {FIELD_LABEL.get(spec.date_basis, spec.date_basis)}, which "
                            "the orders no longer have; the order date is used.")
        d = pd.to_datetime(o[basis])
        lo = pd.Timestamp(spec.date_from) if spec.date_from else d.min()
        hi = pd.Timestamp(spec.date_to) if spec.date_to else d.max()
        inside = (d >= lo) & (d <= hi)
        blank, before, after = d.isna(), d < lo, d > hi
        dl = FIELD_LABEL.get(basis, basis).lower()
        for mask, value, why in ((before, f"before {lo:%Y-%m-%d}", "outside the date window"),
                                 (after, f"after {hi:%Y-%m-%d}", "outside the date window"),
                                 (blank, f"no {dl}", f"no {dl} to place it in the window")):
            if mask.any():
                summary.append(dict(filter="Date window", value=value, rows_removed=int(mask.sum()), reason=why))
        o = o[inside.fillna(False).to_numpy()]
        steps.append(dict(filter=f"Dates {lo:%b %d, %Y} to {hi:%b %d, %Y} ({dl})",
                          removed=int((~inside.fillna(False)).sum()), remaining=len(o)))
    steps.append(dict(filter="In scope", removed=0, remaining=len(o)))
    out["orders"] = o.reset_index(drop=True)
    sites = spec.include.get("site")
    if sites is not None:
        for t, what in RELATED.items():
            if t not in tables:
                continue
            df = tables[t]
            if "site" in df:
                keep = _keys(df["site"]).isin(sites)
                out[t] = df[keep.to_numpy()].reset_index(drop=True)
                related[t] = f"site column found: same site filter applied ({int(keep.sum()):,} of {len(df):,} rows)"
                if (~keep).any():
                    summary.append(dict(filter=f"Site ({t})", value=_fmt_values(sorted(set(_keys(df['site'])[~keep]))),
                                        rows_removed=int((~keep).sum()), reason="other site"))
            else:
                msg = f"No site column in {t}; sizing will use all sites' {what}."
                related[t] = "no site column: sizing will use all sites' " + what
                warnings.append(msg)
    return ScopeResult(out, pd.DataFrame(steps), pd.DataFrame(summary, columns=["filter", "value", "rows_removed",
                                                                                   "reason"]),
                       warnings, related)


# ---- before / after ------------------------------------------------------------------------------------

IMPACT_ROWS = ("Orders", "Lines per day (average)", "Peak day (lines)", "Units per line")


def impact(orders_all: pd.DataFrame, orders_in: pd.DataFrame, line_definition: str = "row") -> pd.DataFrame:
    """Four cheap before / after numbers, under the client's order-line definition (gtpfit.lines).
    Lines per day is averaged over days with orders; the peak day is the busiest calendar day."""
    def nums(o):
        if o.empty:
            return [0, 0.0, 0, float("nan")]
        lines = define_lines(o, line_definition)
        per_day = lines.groupby(pd.to_datetime(lines["order_date"]).dt.normalize()).size()
        qty = pd.to_numeric(lines["qty"], errors="coerce") if "qty" in lines else pd.Series(dtype=float)
        return [int(o["order_id"].nunique()), float(per_day.mean()), int(per_day.max()),
                float(qty.sum() / len(lines)) if len(lines) else float("nan")]
    return pd.DataFrame({"measure": IMPACT_ROWS, "all_rows": nums(orders_all), "in_scope": nums(orders_in)})


# ---- saved with the client ---------------------------------------------------------------------------------

def load_scope(client: str) -> ScopeSpec | None:
    from gtpfit.workspace import read_client_yaml
    d = read_client_yaml(client).get("scope")
    return ScopeSpec.from_dict(d) if d else None


def save_scope(client: str, spec: ScopeSpec, summary: pd.DataFrame | None = None):
    """Save the spec under scope: in client.yaml and write _scope/scope_summary.csv (what it removes) to the
    client's workspace.  Without a summary it is worked out from the client's ingest outputs."""
    from gtpfit.workspace import client_dir, write_client_yaml
    spec = dataclasses.replace(spec, saved_at=dt.datetime.now().isoformat(timespec="seconds"))
    write_client_yaml(client, scope=spec.to_dict())
    if summary is None:
        from gtpfit.tables import load_unscoped
        try:
            summary = apply_scope(load_unscoped(client), spec).summary
        except FileNotFoundError:
            summary = None
    if summary is not None:
        d = client_dir(client) / "_scope"
        d.mkdir(parents=True, exist_ok=True)
        summary.to_csv(d / "scope_summary.csv", index=False, encoding="utf-8")
    return spec
