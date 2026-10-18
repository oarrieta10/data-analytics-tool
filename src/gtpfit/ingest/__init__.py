"""ingest — client files in, canonical typed tables out, with a log of everything doubtful.

Reads CSV/Excel exports (DuckDB for large CSVs), maps columns to canonical
fields (saved per-WMS profiles or automatic matching), coerces types,
converts lengths/weights to inches/pounds, and refuses silent problems:
clipped exports, date gaps, unreadable dates, unclear units, too many bad rows.

Does not score or repair item data — that is quality/ and backfill/.
"""
from .checks import Issue
from .mapping import WmsProfile, load_wms_profile, save_wms_profile, suggest_mapping
from .params import IngestParams
from .pipeline import (IngestError, IngestResult, TableResult, ingest_folder, ingest_table,
                       profile_from_result, write_outputs)

__all__ = ["Issue", "WmsProfile", "load_wms_profile", "save_wms_profile", "suggest_mapping", "IngestParams",
           "IngestError", "IngestResult", "TableResult", "ingest_folder", "ingest_table",
           "profile_from_result", "write_outputs"]
