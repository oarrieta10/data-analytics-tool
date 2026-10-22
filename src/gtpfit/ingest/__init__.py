"""ingest — client files in, canonical typed tables out, with a log of everything doubtful.

Reads any common client export (readers.py: delimited text, Excel, JSON,
Parquet, Arrow, ORC, SQLite/DuckDB files, gzip/zip), guesses which table each
file / sheet / database table holds (classify.py), maps columns to canonical
fields (the client's saved mapping or automatic matching), coerces types,
converts lengths/weights to inches/pounds, and refuses silent problems:
clipped exports, date gaps, unreadable dates, unclear units, too many bad rows.

Does not score or repair item data — that is quality/ and backfill/.
"""
from .checks import Issue
from .mapping import WmsProfile, load_wms_profile, parse_wms_profile, save_wms_profile, suggest_mapping
from .params import IngestParams
from .pipeline import (IngestError, IngestResult, TableResult, apply_acceptances, discover, ingest_folder, ingest_sources,
                       ingest_table, profile_from_result, table_inputs, write_outputs)
from .coverage import Coverage, coverage
from .detect import Detection, detect
from .readers import Source, UnsupportedFileError

__all__ = ["Issue", "WmsProfile", "load_wms_profile", "parse_wms_profile", "save_wms_profile", "suggest_mapping", "IngestParams",
           "IngestError", "IngestResult", "TableResult", "ingest_folder", "ingest_table",
           "profile_from_result", "write_outputs", "discover", "ingest_sources", "Source",
           "UnsupportedFileError", "Coverage", "coverage", "Detection", "detect", "apply_acceptances", "table_inputs"]
