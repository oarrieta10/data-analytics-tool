"""exports.py — tables for the client and the audit trail: accepted blocks, and issues + rejected rows.

    accepted_blocks(result) -> DataFrame   table, code, title, message, reason, accepted_at
    client_rejects(result)  -> DataFrame   one row per issue and per rejected row, with file and row number

The rejects table is meant for the client's data team: each rejected row
keeps its file, spreadsheet row number (header = row 1), reason and the
values it had, so they can find and fix it at source.
"""
from __future__ import annotations

import pandas as pd

from .checks import explain

COLUMNS = ["record", "table", "file", "row", "reason", "title", "detail", "values"]


def accepted_blocks(result) -> pd.DataFrame:
    rows = [dict(a, title=explain(a["code"]).title) for a in result.accepted]
    return pd.DataFrame(rows, columns=["table", "code", "title", "message", "reason", "accepted_at"])


def client_rejects(result) -> pd.DataFrame:
    out = []
    iss = result.issues[result.issues["severity"].isin(["block", "accepted", "warn"])]
    for r in iss.itertuples():
        out.append(dict(record="issue", table=r.table, file="", row="", reason=r.code,
                        title=explain(r.code).title, detail=r.message, values=""))
    for table, df in result.rejects.items():
        if df is None or not len(df):
            continue
        cols = [c for c in df.columns if c not in ("reason", "_file", "_row")]
        vals = df[cols].astype("string")
        for i in range(len(df)):
            v = vals.iloc[i]
            out.append(dict(record="rejected row", table=table, file=df["_file"].iat[i], row=int(df["_row"].iat[i]),
                            reason=df["reason"].iat[i], title=explain(df["reason"].iat[i]).title, detail="",
                            values="; ".join(f"{c}={v[c]}" for c in cols if pd.notna(v[c]))))
    return pd.DataFrame(out, columns=COLUMNS)
