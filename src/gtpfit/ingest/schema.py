"""schema.py — the canonical tables ingest produces and the header synonyms used to map them.

Field kinds:
    id      text key, trimmed (SKUs also upper-cased)
    text    free text, trimmed
    length  number, converted to inches       weight  number, converted to pounds
    count   number (case pack)                qty     transaction quantity, must be > 0
    stock   on-hand quantity, must be >= 0    date    calendar date
    time    time of day, stored as seconds after midnight
    cases   full cases on an order line (units = eaches + cases x case pack)

Synonyms are compared after normalise_header() (lower case, punctuation and a
trailing unit hint removed).  They are generic WMS/ERP vocabulary, plus the
client column names consultants confirmed with "Remember this synonym" in the
workbench (learned_synonyms.yaml next to this file; see synonyms()).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_UNIT_TAIL = r"(in|inch|inches|cm|mm|m|ft|lb|lbs|pound|pounds|kg|kgs|g|gr|grams|oz|ounces|ea|each)"


def normalise_header(h: str) -> str:
    s = str(h).lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)           # "(cm)", "[lbs]"
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(rf"\s+{_UNIT_TAIL}$", "", s)            # trailing "in", "kg", ...
    return s


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    required: bool = False
    synonyms: tuple = ()
    group: str | None = None  # unit-conversion group: "unit" or "case"


# Where the work happens: a site / warehouse / DC (not "location", which inventory uses for the bin).
_SITE = ("site", "warehouse", "warehouse id", "warehouse code", "whse", "dc", "distribution center",
         "distribution centre", "facility", "branch", "plant", "fulfillment center", "fulfilment centre", "fc",
         "site id", "site code")
_SKU = ("sku", "item", "item number", "item no", "item nbr", "item id", "item code", "product",
        "product id", "product code", "material", "material number", "article", "part number", "part no", "part")

TABLES: dict[str, list[Field]] = {
    "item_master": [
        Field("sku", "id", True, _SKU),
        Field("description", "text", False, ("description", "item description", "product description",
                                             "item desc", "desc", "item name", "product name", "sku desc",
                                             "sku description", "style description",
                                             "part description")),
        Field("category", "text", False, ("category", "product category", "department", "item category")),
        Field("subcategory", "text", False, ("sub category", "subcategory", "class", "item class", "sub class")),
        Field("unit_l", "length", True, ("unit length", "each length", "item length", "length", "ea length", "unit l",
                                          "std unit length", "unit len"), "unit"),
        Field("unit_w", "length", True, ("unit width", "each width", "item width", "width", "ea width", "unit w",
                                          "std unit width", "unit wid"), "unit"),
        Field("unit_h", "length", True, ("unit height", "each height", "item height", "height", "ea height", "unit h",
                                          "std unit height", "unit hgt"), "unit"),
        Field("unit_weight", "weight", True, ("unit weight", "each weight", "item weight", "weight",
                                               "ea weight", "net weight", "std unit weight", "unit wt"),
              "unit"),
        Field("case_qty", "count", True, ("case pack", "case qty", "case quantity", "units per case", "pack size",
                                           "master pack qty", "inner pack", "case pack qty", "upc per case",
                                           "std case quantity", "std case qty", "qpc", "qty per case",
                                           "quantity per case", "package qty", "package quantity", "qty per package",
                                           "units per package", "pack qty", "pack quantity"), None),
        Field("case_l", "length", False, ("case length", "carton length", "master length", "ctn length", "case l",
                                          "std case length"), "case"),
        Field("case_w", "length", False, ("case width", "carton width", "master width", "ctn width", "case w",
                                          "std case width"), "case"),
        Field("case_h", "length", False, ("case height", "carton height", "master height", "ctn height", "case h",
                                          "std case height"), "case"),
        Field("case_weight", "weight", False, ("case weight", "carton weight", "master weight", "ctn weight",
                                               "case gross weight", "std case weight"), "case"),
        # one row per package level (item / case / pallet): ingest combines them per SKU (packlevels.py)
        Field("pack_level", "text", False, ("package type", "pack type", "packaging type", "pack level",
                                            "package level", "packaging level", "uom", "unit of measure",
                                            "uom code", "pack uom")),
        Field("pallet_qty", "count", False, ("pallet qty", "pallet quantity", "units per pallet", "qty per pallet",
                                             "pallet pack", "ti hi qty")),
        Field("pallet_l", "length", False, ("pallet length",), "pallet"),
        Field("pallet_w", "length", False, ("pallet width",), "pallet"),
        Field("pallet_h", "length", False, ("pallet height",), "pallet"),
        Field("pallet_weight", "weight", False, ("pallet weight", "pallet gross weight"), "pallet"),
    ],
    "orders": [
        Field("order_id", "id", True, ("order", "order number", "order nbr", "order no", "order id", "sales order",
                                       "so number", "order num", "customer order no", "customer order number",
                                       "customer order")),
        Field("order_date", "date", True, ("order date", "ship date", "pick date", "shipped date", "date",
                                           "transaction date", "date shipped", "picked at", "shipped at",
                                           "closed at", "completed at")),
        Field("sku", "id", True, _SKU),
        # picked / shipped quantities first: a plain "quantity" is often what was ordered
        Field("qty", "qty", True, ("qty shipped", "qty picked", "picked quantity", "shipped quantity",
                                   "quantity shipped", "shipped qty", "pick qty", "units picked", "units shipped",
                                   "eaches picked", "eaches", "each qty", "qty", "quantity", "units")),
        Field("cases", "cases", False, ("cases", "cases picked", "full cases", "cases shipped", "case picks")),
        Field("pick_secs", "time", False, ("pick time", "time", "ship time", "pick timestamp", "time picked",
                                           "order start of hour", "start of hour", "pick hour")),
        Field("channel", "text", False, ("channel", "sales channel", "customer type", "channel type")),
        Field("site", "text", False, _SITE),
        Field("order_type", "text", False, ("order type", "order class", "order category", "order kind",
                                            "service level", "priority")),
        Field("order_status", "text", False, ("order status", "status", "line status", "fulfillment status",
                                              "fulfilment status", "order state")),
        # also an order_date synonym: when it is the only date it becomes the order date (required wins)
        Field("ship_date", "date", False, ("ship date", "shipped date", "date shipped", "shipping date")),
    ],
    "receipts": [
        Field("receipt_id", "id", False, ("receipt", "receipt number", "receipt nbr", "receipt no", "receipt id",
                                          "asn", "asn number", "asn id", "shipment", "shipment number",
                                          "inbound shipment", "container")),
        Field("po", "id", False, ("po", "po number", "po nbr", "purchase order", "po no")),
        Field("receipt_date", "date", True, ("receipt date", "received date", "date received", "receive date",
                                             "received at", "date")),
        Field("sku", "id", True, _SKU),
        Field("site", "text", False, _SITE),
        Field("qty", "qty", True, ("qty received", "units received", "received qty", "quantity received",
                                   "received quantity",
                                   "inventory units", "eaches received", "qty", "quantity", "units")),
    ],
    "inventory": [
        Field("snapshot_date", "date", False, ("snapshot date", "inventory date", "as of date", "inv date", "date",
                                               "count date")),
        Field("sku", "id", True, _SKU),
        Field("location", "text", False, ("location", "loc", "bin", "slot", "location code", "storage location")),
        Field("site", "text", False, _SITE),
        Field("on_hand", "stock", True, ("on hand qty", "on hand", "qty on hand", "oh qty", "oh units",
                                         "on hand units", "quantity on hand", "sum eaches", "qty", "quantity",
                                         "available qty")),
    ],
}

# Field names for people (data request template, receipt, workbench).
FIELD_LABEL = {
    "sku": "SKU / item number", "description": "Description", "category": "Category",
    "subcategory": "Sub-category", "unit_l": "Unit length", "unit_w": "Unit width", "unit_h": "Unit height",
    "unit_weight": "Unit weight", "case_qty": "Case quantity", "case_l": "Case length", "case_w": "Case width",
    "case_h": "Case height", "case_weight": "Case weight",
    "pack_level": "Package level", "pallet_qty": "Units per pallet", "pallet_l": "Pallet length",
    "pallet_w": "Pallet width", "pallet_h": "Pallet height", "pallet_weight": "Pallet weight", "order_id": "Order number", "order_date": "Pick / ship date",
    "qty": "Quantity (units)", "cases": "Full cases", "pick_secs": "Pick time", "channel": "Channel",
    "site": "Site / warehouse", "order_type": "Order type", "order_status": "Order status", "ship_date": "Ship date",
    "receipt_id": "Receipt / ASN / shipment number", "po": "PO number", "receipt_date": "Receipt date",
    "snapshot_date": "Snapshot date", "location": "Location", "on_hand": "Units on hand",
}
TABLE_LABEL = {"item_master": "Item master", "orders": "Order history", "receipts": "Receipts (inbound)",
               "inventory": "Inventory snapshots"}

# What each field means, for people choosing columns (workbench) and for the report.
FIELD_HELP = {
    "item_master": {
        "sku": "Item / part number: the key that links all tables.",
        "description": "Item name or description.",
        "category": "Product category.", "subcategory": "Product sub-category.",
        "unit_l": "Length of one unit (each).", "unit_w": "Width of one unit.", "unit_h": "Height of one unit.",
        "unit_weight": "Weight of one unit.",
        "case_qty": "Units per case (case pack / QPC).",
        "case_l": "Case length.", "case_w": "Case width.", "case_h": "Case height.", "case_weight": "Case weight.",
        "pack_level": "Package level when the file has one row per package (item / case / pallet); the rows are "
                      "combined into one row per SKU.",
        "pallet_qty": "Units on a full pallet.", "pallet_l": "Pallet length.", "pallet_w": "Pallet width.",
        "pallet_h": "Pallet height.", "pallet_weight": "Pallet weight.",
    },
    "orders": {
        "order_id": "Order number (customer or sales order).",
        "order_date": "Date the line was picked or shipped: drives daily volumes and the design day.",
        "sku": "Item / part number picked.",
        "qty": "Units picked or shipped on the line (loose units when there is a separate cases column).",
        "cases": "Full cases picked on the line, if counted apart from loose units.",
        "pick_secs": "Time of day the line was picked: drives the hourly profile.",
        "channel": "Sales channel, e.g. e-commerce vs store / retail / B2B.",
        "site": "Site, warehouse or DC that filled the order (used to scope the design).",
        "order_type": "Order type or service level, e.g. standard / expedited / replenishment.",
        "order_status": "Order or line status, e.g. shipped / cancelled / returned.",
        "ship_date": "Date the order shipped, if separate from the order date.",
    },
    "receipts": {
        "receipt_id": "Receipt, ASN or inbound shipment / container number.",
        "po": "Purchase order number.",
        "receipt_date": "Date the goods were received.",
        "sku": "Item / part number received.",
        "qty": "Units received.",
        "site": "Site, warehouse or DC that received the goods.",
    },
    "inventory": {
        "snapshot_date": "Date of the stock count / snapshot.",
        "sku": "Item / part number.",
        "location": "Storage location.",
        "on_hand": "Units on hand.",
        "site": "Site, warehouse or DC holding the stock.",
    },
}

# Tables a study cannot run without; the others only warn when absent.
REQUIRED_TABLES = ("item_master", "orders")

# Required fields whose column must exist but whose blank values are kept, not set aside: an item
# master with missing sizes or case packs is what quality/ and backfill/ work on.
KEEP_BLANK = {"item_master": ("unit_l", "unit_w", "unit_h", "unit_weight", "case_qty")}

# Guessing which table a file holds is looser than ingest's required fields: a SKU list with sizes but no
# case pack is still an item master (ingest then asks for the missing column).  An item master needs a
# SKU plus at least IDENTIFY_MIN of these; a bare SKU list is not one.
IDENTIFY_BY = {"item_master": ("description", "category", "unit_l", "unit_w", "unit_h", "unit_weight",
                               "case_qty", "case_l", "case_w", "case_h", "case_weight")}
IDENTIFY_MIN = 3


def could_be(table: str, mapped) -> bool:
    """Whether a file with these canonical fields mapped might hold this table (used to guess, not to ingest)."""
    mapped = set(mapped)
    if table in IDENTIFY_BY:
        return "sku" in mapped and len(mapped & set(IDENTIFY_BY[table])) >= IDENTIFY_MIN
    return all(f.name in mapped for f in TABLES[table] if f.required)


# ---- learned synonyms: client column names confirmed by consultants, shared by every client ----------

LEARNED_SYNONYMS = Path(__file__).with_name("learned_synonyms.yaml")
_learned_cache: dict = {}


def _learned_path() -> Path:
    return Path(os.environ.get("GTPFIT_SYNONYMS", LEARNED_SYNONYMS))  # env: tests use their own file


def learned_synonyms() -> dict:
    """{table: {field: [normalised header, ...]}} from the learned file (re-read when it changes)."""
    p = _learned_path()
    stamp = (str(p), p.stat().st_mtime_ns) if p.exists() else (str(p), None)
    if _learned_cache.get("stamp") != stamp:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
        _learned_cache.update(stamp=stamp, data=data or {})
    return _learned_cache["data"]


def synonyms(table: str, name: str) -> tuple:
    """Built-in synonyms for a field, then the learned ones (all normalised)."""
    f = field(table, name)
    built = [normalise_header(s) for s in f.synonyms]
    learned = [s for s in (learned_synonyms().get(table) or {}).get(name) or [] if s not in built]
    return tuple(built + learned)


def remember_synonym(table: str, name: str, header: str) -> bool:
    """Add a client column name to a field's synonyms for every future client.  False if already known."""
    if table not in TABLES or name not in {f.name for f in TABLES[table]}:
        raise ValueError(f"Unknown field {table}.{name}")
    n = normalise_header(header)
    if not n or n in synonyms(table, name) or n.replace(" ", "") in {s.replace(" ", "") for s in synonyms(table, name)}:
        return False
    data = {t: {k: list(v) for k, v in (fs or {}).items()} for t, fs in learned_synonyms().items()}
    data.setdefault(table, {}).setdefault(name, []).append(n)
    p = _learned_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# Client column names confirmed by consultants with \"Remember this synonym\" (workbench\n"
                 "# Confirm step).  Normalised (lower case, no punctuation).  Column names only, never data.\n"
                 + yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding="utf-8")
    return True


def fields(table: str) -> list[Field]:
    return TABLES[table]


def field(table: str, name: str) -> Field:
    return next(f for f in TABLES[table] if f.name == name)
