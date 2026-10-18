"""checks.py — the issue record and the whole-table checks (row limits, date gaps).

Severity:
    block     ingest refuses the data; the consultant must fix it or accept the
              specific code (ingest_folder(..., accept=[code]))
    accepted  a block the consultant has accepted; kept in the log
    warn      data used, but someone should look (rows set aside, guessed units)
    info      what was done (mapping, conversions, closures)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

SEVERITY_ORDER = {"block": 0, "accepted": 1, "warn": 2, "info": 3}


@dataclass
class Issue:
    table: str
    code: str
    severity: str
    message: str
    count: int | None = None
    share: float | None = None
    field: str | None = None

    def to_dict(self):
        return asdict(self)


def issues_frame(issues) -> pd.DataFrame:
    cols = list(Issue.__dataclass_fields__)
    df = pd.DataFrame([i.to_dict() for i in issues], columns=cols)
    df["count"] = df["count"].astype("Int64")
    return df.sort_values("severity", key=lambda s: s.map(SEVERITY_ORDER), kind="stable").reset_index(drop=True)


def missing_day_runs(dates: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    """Runs of calendar days with no rows, between the first and last date present."""
    d = pd.DatetimeIndex(dates.dropna().unique()).normalize().sort_values()
    if len(d) < 2:
        return []
    missing = pd.date_range(d[0], d[-1]).difference(d)
    runs, start, prev = [], None, None
    for day in missing:
        if start is None or (day - prev).days > 1:
            if start is not None:
                runs.append((start, prev, (prev - start).days + 1))
            start = day
        prev = day
    if start is not None:
        runs.append((start, prev, (prev - start).days + 1))
    return runs


def snapshot_gaps(dates: pd.Series, factor: float) -> tuple[float, list]:
    """Median spacing between snapshot dates and the spacings wider than factor x median."""
    d = pd.DatetimeIndex(dates.dropna().unique()).sort_values()
    if len(d) < 3:
        return float("nan"), []
    step = pd.Series(d[1:] - d[:-1]).dt.days
    med = float(step.median())
    wide = [(d[i], d[i + 1], int(s)) for i, s in enumerate(step) if s > factor * med]
    return med, wide


def fmt_day(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%d")
