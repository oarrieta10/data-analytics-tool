"""checks.py — the issue record, what every issue code means in plain words, accepting blocks, and the
whole-table checks (row limits, date gaps).

Severity:
    block     ingest refuses the data; the consultant must fix it or accept that
              specific block with a reason (accept={"table:code": "reason"})
    accepted  a block the consultant has accepted; kept in the log with the reason
    warn      data used, but someone should look (rows set aside, guessed units)
    info      what was done (mapping, conversions, closures)

ISSUE_TEXT holds, per code, a short title, what it means and the design
impact, written for a consultant talking to a client.  The numbers come from
the issue's own message; this text never contains figures.
"""
from __future__ import annotations

import datetime as dt
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


@dataclass(frozen=True)
class IssueText:
    title: str    # a few words, for a card heading
    what: str     # what it means, in plain words
    impact: str   # one sentence: what it does to the design if left alone


_T = IssueText
ISSUE_TEXT: dict[str, IssueText] = {
    # ---- files and tables
    "table_missing": _T("A table was not provided",
                        "No file was given for this table.",
                        "Without an item master or order history the system cannot be sized; without receipts or "
                        "inventory, inbound and storage sizing are skipped."),
    "ambiguous_file": _T("Unclear which table a file holds",
                         "A file's columns fit more than one table equally well.",
                         "The wrong assignment would mix up item data and transactions."),
    "unrecognised_file": _T("A file was not recognised",
                            "No table's required columns were found in this file, so it was ignored.",
                            "If it holds data you need, assign it by hand or ask the client what it is."),
    "empty_table": _T("A file has no rows",
                      "The file has a header but no data under it.",
                      "The table it was meant to supply is effectively missing."),
    "header_mismatch": _T("Files for one table have different columns",
                          "Several files were given for the same table, but their columns differ.",
                          "They cannot be stacked safely; rows could land in the wrong fields."),
    "duplicate_header": _T("Repeated column names",
                           "The file repeats a column name (common in joined database exports).",
                           "If the repeated column is one we use, values could come from the wrong copy."),
    "encoding_fallback": _T("Older text encoding",
                            "The file is not UTF-8; it was read as a Windows/Western encoding.",
                            "None if accented text reads correctly in the preview."),
    "blank_rows": _T("Blank rows ignored",
                     "Rows with nothing in them (often a leftover spreadsheet range) were skipped.",
                     "None; they hold no data."),
    # ---- spreadsheet clipping and date coverage
    "excel_row_limit": _T("The export hit Excel's row limit",
                          "The file has exactly as many rows as a spreadsheet can hold, so the export was most "
                          "likely cut off.",
                          "The latest dates are probably missing, so peak season may be absent and the design day "
                          "understated."),
    "excel_padded": _T("File padded to the spreadsheet limit",
                       "The file runs to the spreadsheet row limit, but most of those rows are blank.",
                       "Probably harmless; check with the client that this is the full extract."),
    "date_gap": _T("Days missing in the history",
                   "There are stretches with no rows that are longer than a normal closure.",
                   "Volumes for those weeks are missing, which understates averages and can hide a peak."),
    "snapshot_gap": _T("Inventory snapshots missing",
                       "The spacing between inventory snapshots is uneven: some are missing.",
                       "On-hand levels and days-on-hand for those periods are unknown."),
    "closed_days": _T("Short closures",
                      "A few days have no rows (weekends, holidays).",
                      "None; this is normal operating calendar."),
    "date_range": _T("Dates covered",
                     "The first and last date in the table.",
                     "Sets the period every volume and peak figure is drawn from."),
    "coverage_short": _T("Less than a year of orders",
                         "The order history covers less than 12 months.",
                         "Seasonal peaks outside the period are not seen, so the design day may be understated."),
    "peak_not_covered": _T("Peak season not covered",
                           "The order history has no rows in the client's peak months.",
                           "The design day is set from off-peak volumes and will likely be too low."),
    # ---- columns and mapping
    "missing_required_column": _T("A required column is missing",
                                  "No column was found (or chosen) for a field the analysis needs.",
                                  "That table cannot be used until the column is supplied or mapped."),
    "ambiguous_column": _T("Unclear which column to use",
                           "Two or more columns fit the same field equally well.",
                           "Picking the wrong one would feed the wrong values into every calculation."),
    "fuzzy_header": _T("Columns matched on similar names",
                       "Some columns were matched because their names are close, not exact.",
                       "Worth a glance: a wrong match feeds wrong values in."),
    "extra_columns": _T("Extra order columns kept for scoping",
                        "Order columns that match no field but hold only a few different values (e.g. a pick area "
                        "or carrier) were kept, named x_..., so the Scope step can filter on them.",
                        "None; they are only used if the scope filters on them."),
    "auto_mapped": _T("Columns matched automatically",
                      "Columns were matched to fields by their names.",
                      "None once confirmed; the confirmed mapping is remembered for this client."),
    "alternative_columns": _T("Other columns could also fit",
                              "Another column could also supply this field.",
                              "None if the chosen column is the right one."),
    "optional_columns_absent": _T("Optional columns not provided",
                                  "Some optional fields have no column in the file.",
                                  "Analyses that use them are skipped or use defaults."),
    "sku_normalised": _T("SKU spellings tidied",
                         "Spaces were trimmed and SKUs upper-cased so the same item matches across tables.",
                         "None; it prevents one item counting as two."),
    # ---- values and rows
    "bad_qty": _T("Rows with zero or negative quantity",
                  "Some rows have a quantity of zero or below (returns, adjustments, typos); they were set aside.",
                  "A few are normal; many suggest the extract mixes in returns or adjustments."),
    "missing_value": _T("Rows missing a required value",
                        "Some rows are blank in a field every row needs; they were set aside.",
                        "Those rows cannot be counted; many of them means the extract is suspect."),
    "unparseable": _T("Rows with unreadable values",
                      "Some required values could not be read as numbers or dates; those rows were set aside.",
                      "Those rows cannot be counted; many of them means a format problem in the export."),
    "unparseable_values": _T("Some values could not be read",
                             "Some optional values were not numbers or times; they were left blank.",
                             "Minor unless a large share is affected."),
    "blank_values": _T("Blank item values kept",
                       "Some items have no value in a required size, weight or case-pack column; the rows are kept.",
                       "Those items need their values filled before container fit and cube."),
    "date_format_detected": _T("Date format recognised",
                               "The date format was worked out from the values.",
                               "None."),
    "date_format_mismatch": _T("Dates don't match the declared format",
                               "The date format saved for this client does not fit this file.",
                               "Dates would be misread (e.g. day and month swapped)."),
    "date_format_ambiguous": _T("Day and month could be either way round",
                                "Every date fits both day-first and month-first.",
                                "Reading them the wrong way round scrambles the calendar and the peak."),
    "date_unparseable": _T("Dates could not be read",
                           "No known date format fits the values.",
                           "The table cannot be placed in time until the format is known."),
    # ---- units
    "units": _T("Units of measure", "Which length and weight units the item data uses, and how that was decided.",
                "Sets every size, cube and weight figure."),
    "unit_converted": _T("Units converted", "Sizes and weights were converted to inches and pounds.", "None."),
    "unit_ambiguous": _T("Units of measure unclear",
                         "The sizes could be in more than one unit (e.g. inches or centimetres) and none is declared.",
                         "Every cube, container-fit and weight figure depends on getting this right."),
    "unit_mismatch": _T("Declared units look wrong",
                        "The units declared for this client do not fit the sizes in the file.",
                        "Cube and fit would be off by a large factor."),
    "unit_conflict": _T("Columns name different units",
                        "Size or weight columns of one group say different units in their names.",
                        "Some sizes would be converted wrongly."),
    "unit_implausible": _T("Sizes look unusual",
                           "The declared units give sizes or densities outside the usual range.",
                           "Worth checking with the client; could be a niche assortment."),
    "units_statistical": _T("Units worked out from the data",
                            "No units were declared; the only unit pair that fits was used.",
                            "Confirm the units with the client; a wrong guess scales every size and weight."),
    # ---- order lines and cases
    "cases_need_item_master": _T("Full cases need case packs",
                                 "Some order lines are counted in full cases; units need the item master's case pack.",
                                 "Units per line are understated until the item master is included."),
    "cases_converted": _T("Full cases converted to units",
                          "Full-case lines were turned into units with each item's case pack.",
                          "None; unit volumes are complete."),
    "case_pack_missing": _T("Case lines without a case pack",
                            "Some full-case order lines are for items with no case pack, so their units are unknown.",
                            "Those lines' units are blank until the case pack is filled."),
}


def explain(code: str) -> IssueText:
    """Plain-language text for a code (a neutral fallback for codes not yet described)."""
    return ISSUE_TEXT.get(code, IssueText(code.replace("_", " ").capitalize(), "", ""))


# ---- accepting blocks: only with a reason --------------------------------------------------------

# Blocks that cannot be accepted, with what to do instead.  Accepting a unit block would let the sizes and
# weights through unconverted, read as inches and pounds whatever they really are.
NOT_ACCEPTABLE = {
    "unit_ambiguous": "choose the units this client uses on the Review step, or name them in the column headers",
    "unit_mismatch": "choose the units that fit on the Review step",
}


def normalise_accept(accept) -> dict:
    """{key: {"reason", "at"}} from {key: reason} or {key: {"reason", "at"}}; key = "code" or "table:code".

    A list of bare codes, or a blank reason, is refused: a block is accepted only with a reason.  So is a
    block in NOT_ACCEPTABLE, which must be resolved instead.
    """
    if not accept:
        return {}
    if not isinstance(accept, dict):
        raise ValueError(f"Accepting blocks needs a reason for each: pass {{code: reason}}, not {list(accept)}.")
    out = {}
    for key, v in accept.items():
        code = str(key).split(":")[-1]
        if code in NOT_ACCEPTABLE:
            raise ValueError(f"'{key}' cannot be accepted: {NOT_ACCEPTABLE[code]}.")
        rec = dict(v) if isinstance(v, dict) else {"reason": v}
        reason = str(rec.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"Accepting '{key}' needs a reason.")
        out[str(key)] = {"reason": reason, "at": rec.get("at") or dt.datetime.now().isoformat(timespec="seconds")}
    return out


def accepted_for(acc: dict, table: str, code: str) -> dict | None:
    """The acceptance that covers this block, if any: table-specific first, then the bare code."""
    return acc.get(f"{table}:{code}") or acc.get(code)


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
