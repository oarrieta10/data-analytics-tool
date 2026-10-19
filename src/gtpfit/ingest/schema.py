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
        Field("unit_l", "length", False, ("unit length", "each length", "item length", "length", "ea length", "unit l",
                                          "std unit length", "unit len"), "unit"),
        Field("unit_w", "length", False, ("unit width", "each width", "item width", "width", "ea width", "unit w",
                                          "std unit width", "unit wid"), "unit"),
        Field("unit_h", "length", False, ("unit height", "each height", "item height", "height", "ea height", "unit h",
                                          "std unit height", "unit hgt"), "unit"),
        Field("unit_weight", "weight", False, ("unit weight", "each weight", "item weight", "weight",
                                               "ea weight", "net weight", "std unit weight", "unit wt"),
              "unit"),
        Field("case_qty", "count", False, ("case pack", "case qty", "case quantity", "units per case", "pack size",
                                           "master pack qty", "inner pack", "case pack qty", "upc per case",
                                           "std case quantity", "std case qty", "qpc", "qty per case",
                                           "quantity per case"), None),
        Field("case_l", "length", False, ("case length", "carton length", "master length", "ctn length", "case l",
                                          "std case length"), "case"),
        Field("case_w", "length", False, ("case width", "carton width", "master width", "ctn width", "case w",
                                          "std case width"), "case"),
        Field("case_h", "length", False, ("case height", "carton height", "master height", "ctn height", "case h",
                                          "std case height"), "case"),
        Field("case_weight", "weight", False, ("case weight", "carton weight", "master weight", "ctn weight",
                                               "case gross weight", "std case weight"), "case"),
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
        Field("channel", "text", False, ("channel", "order type", "sales channel", "customer type")),
    ],
    "receipts": [
        Field("receipt_id", "id", False, ("receipt", "receipt number", "receipt nbr", "receipt no", "receipt id",
                                          "asn", "asn number", "asn id", "shipment", "shipment number",
                                          "inbound shipment", "container")),
        Field("po", "id", False, ("po", "po number", "po nbr", "purchase order", "po no")),
        Field("receipt_date", "date", True, ("receipt date", "received date", "date received", "receive date",
                                             "received at", "date")),
        Field("sku", "id", True, _SKU),
        Field("qty", "qty", True, ("qty received", "units received", "received qty", "quantity received",
                                   "received quantity",
                                   "inventory units", "eaches received", "qty", "quantity", "units")),
    ],
    "inventory": [
        Field("snapshot_date", "date", False, ("snapshot date", "inventory date", "as of date", "inv date", "date",
                                               "count date")),
        Field("sku", "id", True, _SKU),
        Field("location", "text", False, ("location", "loc", "bin", "slot", "location code", "storage location")),
        Field("on_hand", "stock", True, ("on hand qty", "on hand", "qty on hand", "oh qty", "oh units",
                                         "on hand units", "quantity on hand", "sum eaches", "qty", "quantity",
                                         "available qty")),
    ],
}

# Tables a study cannot run without; the others only warn when absent.
REQUIRED_TABLES = ("item_master", "orders")

# Besides its required fields, a file must map one of these to count as that
# table: a bare SKU list is not an item master.
IDENTIFY_BY = {"item_master": ("description", "category", "unit_l", "unit_w", "unit_h", "unit_weight",
                               "case_qty", "case_l", "case_w", "case_h", "case_weight")}


def fields(table: str) -> list[Field]:
    return TABLES[table]


def field(table: str, name: str) -> Field:
    return next(f for f in TABLES[table] if f.name == name)
