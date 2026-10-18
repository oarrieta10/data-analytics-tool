"""schema.py — the canonical tables ingest produces and the header synonyms used to map them.

Field kinds:
    id      text key, trimmed (SKUs also upper-cased)
    text    free text, trimmed
    length  number, converted to inches       weight  number, converted to pounds
    count   number (case pack)                qty     transaction quantity, must be > 0
    stock   on-hand quantity, must be >= 0    date    calendar date
    time    time of day, stored as seconds after midnight

Synonyms are compared after normalise_header() (lower case, punctuation and a
trailing unit hint removed).  They are generic WMS/ERP vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    required: bool = False
    synonyms: tuple = ()
    group: str | None = None  # unit-conversion group: "unit" or "case"


_SKU = ("sku", "item", "item number", "item no", "item nbr", "item id", "item code", "product",
        "product id", "product code", "material", "material number", "article", "part number")

TABLES: dict[str, list[Field]] = {
    "item_master": [
        Field("sku", "id", True, _SKU),
        Field("description", "text", False, ("description", "item description", "product description",
                                             "item desc", "desc", "item name", "product name")),
        Field("category", "text", False, ("category", "product category", "department", "item category")),
        Field("subcategory", "text", False, ("sub category", "subcategory", "class", "item class", "sub class")),
        Field("unit_l", "length", False, ("unit length", "each length", "item length", "length", "ea length"), "unit"),
        Field("unit_w", "length", False, ("unit width", "each width", "item width", "width", "ea width"), "unit"),
        Field("unit_h", "length", False, ("unit height", "each height", "item height", "height", "ea height"), "unit"),
        Field("unit_weight", "weight", False, ("unit weight", "each weight", "item weight", "weight",
                                               "ea weight", "net weight"), "unit"),
        Field("case_qty", "count", False, ("case pack", "case qty", "case quantity", "units per case", "pack size",
                                           "master pack qty", "inner pack", "case pack qty", "upc per case"), None),
        Field("case_l", "length", False, ("case length", "carton length", "master length", "ctn length"), "case"),
        Field("case_w", "length", False, ("case width", "carton width", "master width", "ctn width"), "case"),
        Field("case_h", "length", False, ("case height", "carton height", "master height", "ctn height"), "case"),
        Field("case_weight", "weight", False, ("case weight", "carton weight", "master weight", "ctn weight",
                                               "case gross weight"), "case"),
    ],
    "orders": [
        Field("order_id", "id", True, ("order", "order number", "order nbr", "order no", "order id", "sales order",
                                       "so number", "order num")),
        Field("order_date", "date", True, ("order date", "ship date", "pick date", "shipped date", "date",
                                           "transaction date", "date shipped")),
        Field("sku", "id", True, _SKU),
        Field("qty", "qty", True, ("qty", "quantity", "qty shipped", "units", "qty picked", "quantity shipped",
                                   "shipped qty", "pick qty", "units shipped", "eaches")),
        Field("pick_secs", "time", False, ("pick time", "time", "ship time", "pick timestamp", "time picked")),
        Field("channel", "text", False, ("channel", "order type", "sales channel", "customer type")),
    ],
    "receipts": [
        Field("receipt_id", "id", False, ("receipt", "receipt number", "receipt nbr", "receipt no", "receipt id",
                                          "asn", "asn number")),
        Field("po", "id", False, ("po", "po number", "po nbr", "purchase order", "po no")),
        Field("receipt_date", "date", True, ("receipt date", "received date", "date received", "receive date", "date")),
        Field("sku", "id", True, _SKU),
        Field("qty", "qty", True, ("qty received", "received qty", "quantity received", "qty", "quantity", "units")),
    ],
    "inventory": [
        Field("snapshot_date", "date", False, ("snapshot date", "inventory date", "as of date", "date", "count date")),
        Field("sku", "id", True, _SKU),
        Field("location", "text", False, ("location", "loc", "bin", "slot", "location code")),
        Field("on_hand", "stock", True, ("on hand qty", "on hand", "qty on hand", "oh qty", "quantity on hand",
                                         "qty", "quantity", "available qty")),
    ],
}

# Tables a study cannot run without; the others only warn when absent.
REQUIRED_TABLES = ("item_master", "orders")


def fields(table: str) -> list[Field]:
    return TABLES[table]


def field(table: str, name: str) -> Field:
    return next(f for f in TABLES[table] if f.name == name)
