"""receipt.py — the data receipt: one self-contained HTML page saying what was received and what was found.

    data_receipt(result, client=None, detections=None, requested=None, folder=None, line_definition=None) -> str

Lists the files received (and what each was used as), rows kept and set aside,
the period covered, every block / warning, the blocks accepted with their
reasons, and anything asked of the client.  Every number comes from the ingest
result.  No scripts, no external files; light and dark follow the viewer.
It later feeds the report's data appendix.
"""
from __future__ import annotations

import datetime as dt
from html import escape

from .checks import explain, fmt_day
from .coverage import sparkline_svg
from .schema import TABLE_LABEL

_CSS = """
:root{--bg:#fff;--fg:#1d2433;--muted:#5b6475;--line:#d9dee7;--block:#b42318;--warn:#a15c07;--ok:#1b7f4b;--card:#f6f8fb}
@media (prefers-color-scheme:dark){:root{--bg:#12161d;--fg:#e6e9ef;--muted:#9aa3b2;--line:#2c3340;--block:#f97066;
--warn:#fdb022;--ok:#47cd89;--card:#1a1f28}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0}
main{max-width:960px;margin:0 auto;padding:32px 16px 64px}h1{font-size:26px;margin:0 0 4px}
h2{font-size:18px;margin:32px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.muted{color:var(--muted)}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;border:1px solid}
.block{color:var(--block)}.warn{color:var(--warn)}.ok{color:var(--ok)}.accepted{color:var(--ok)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:8px 0}
.spark{color:var(--fg);margin:8px 0}
"""


def _t(v) -> str:
    return escape("" if v is None else str(v))


def data_receipt(result, client: str | None = None, detections=None, requested=None, folder=None,
                 line_definition: str | None = None) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    iss = result.issues
    blocks, warns = iss[iss.severity == "block"], iss[iss.severity == "warn"]
    verdict = ("block", "Blocked: decisions needed") if len(blocks) else \
        ("warn", "Usable with cautions") if len(warns) else ("ok", "Clean")
    h = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
         f"content='width=device-width,initial-scale=1'><title>Data receipt{' - ' + _t(client) if client else ''}"
         f"</title><style>{_CSS}</style></head><body><main>",
         f"<h1>Data receipt{': ' + _t(client) if client else ''}</h1>",
         f"<p class='muted'>Generated {now} by gtp-fit ingest. Every figure below is computed from the files "
         f"received.</p>",
         f"<p><span class='tag {verdict[0]}'>{verdict[1]}</span> &nbsp; {len(blocks)} block(s) open, "
         f"{len(result.accepted)} accepted, {len(warns)} warning(s).</p>"]

    # files received
    if folder:
        h.append(f"<p><b>Folder read:</b> <code>{_t(folder)}</code> (read in place; nothing copied)</p>")
    if line_definition:
        from gtpfit.lines import LINE_DEFINITIONS
        h.append(f"<p><b>Order line definition:</b> {_t(line_definition)}: "
                 f"{_t(LINE_DEFINITIONS.get(line_definition, ''))}</p>")
    h.append("<h2>Files received</h2><table><tr><th>File</th><th>Used as</th><th>Read as</th>"
             "<th class='n'>Rows in file</th><th class='n'>Rows kept</th><th class='n'>Set aside</th></tr>")
    det = {d.label: d for d in (detections or [])}
    for table, srcs in result.inputs.items():
        kept = len(result.tables[table]) if table in result.tables else 0
        rej = len(result.rejects.get(table, [])) if table in result.rejects else 0
        for k, src in enumerate(srcs):
            d = det.get(str(src))
            h.append(f"<tr><td>{_t(src)}</td><td>{_t(TABLE_LABEL.get(table, table))}</td>"
                     f"<td>{_t(d.read_as if d else '')}</td>"
                     f"<td class='n'>{'' if not d or d.rows is None else f'{d.rows:,}'}</td>"
                     f"<td class='n'>{f'{kept:,}' if k == 0 else ''}</td><td class='n'>{f'{rej:,}' if k == 0 else ''}"
                     f"</td></tr>")
    unused = [d for d in (detections or []) if d.label not in {str(s) for v in result.inputs.values() for s in v}]
    for d in unused:
        h.append(f"<tr class='muted'><td>{_t(d.label)}</td><td>not used</td><td>{_t(d.read_as)}</td>"
                 f"<td class='n'>{'' if d.rows is None else f'{d.rows:,}'}</td><td></td><td></td></tr>")
    h.append("</table>")
    missing = [TABLE_LABEL[t] for t in TABLE_LABEL if t not in result.inputs]
    if missing:
        h.append(f"<p class='muted'>Not provided: {_t(', '.join(missing))}.</p>")

    # coverage
    c = result.coverage
    if c is not None and c.first is not None:
        h.append("<h2>Period covered (orders)</h2>")
        h.append(f"<p>{fmt_day(c.first)} to {fmt_day(c.last)}: {c.span_days:,} days, {c.days_with_rows:,} with "
                 f"orders &middot; {c.lines:,} lines &middot; {c.orders:,} orders &middot; {c.skus:,} SKUs</p>")
        h.append(f"<div class='spark'>{sparkline_svg(c)}</div>")
        h += [f"<p class='warn'>{_t(w)}</p>" for w in c.warnings]

    # issues
    def _rows(df, cls):
        for r in df.itertuples():
            t = explain(r.code)
            h.append(f"<div class='card'><span class='tag {cls}'>{_t(r.severity)}</span> <b>{_t(t.title)}</b> "
                     f"<span class='muted'>({_t(TABLE_LABEL.get(r.table, r.table or 'files'))})</span>"
                     f"<div>{_t(r.message)}</div><div class='muted'>{_t(t.impact)}</div></div>")
    if len(blocks):
        h.append("<h2>Open blocks</h2>")
        _rows(blocks, "block")
    if result.accepted:
        h.append("<h2>Blocks accepted, with reasons</h2><table><tr><th>Table</th><th>Issue</th><th>Reason</th>"
                 "<th>Accepted</th></tr>")
        for a in result.accepted:
            h.append(f"<tr><td>{_t(TABLE_LABEL.get(a['table'], a['table']))}</td><td>{_t(explain(a['code']).title)}"
                     f"<div class='muted'>{_t(a['message'])}</div></td><td>{_t(a['reason'])}</td>"
                     f"<td>{_t(a['accepted_at'])}</td></tr>")
        h.append("</table>")
    if requested:
        h.append("<h2>Asked of the client</h2><ul>")
        for key in requested:
            table, _, code = key.rpartition(":")
            h.append(f"<li>{_t(explain(code).title)} ({_t(TABLE_LABEL.get(table, table or 'files'))})</li>")
        h.append("</ul>")
    if len(warns):
        h.append("<h2>Warnings</h2>")
        _rows(warns, "warn")
    rejected = sum(len(v) for v in result.rejects.values() if v is not None)
    h.append(f"<h2>Rows set aside</h2><p>{rejected:,} row(s) were set aside with a reason; the list with file and "
             f"row numbers is in <code>rejects_for_client.csv</code>.</p>")
    h.append("</main></body></html>")
    return "\n".join(h)
