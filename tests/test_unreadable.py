"""Unreadable files never stop ingest: password-protected, locked, damaged or oddly zipped files (and zip
members) are listed as "can't read" with a plain reason, and every other file is read as normal.

All bad files are made by hand in tmp_path: a zip member's encryption flag or compression method is patched
in its headers (zipfile then refuses it exactly as it refuses a real one), and an encrypted Office file is
an OLE2 header holding an EncryptedPackage stream name.
"""
import gzip
import shutil
import struct
import zipfile
from pathlib import Path

import pytest

from gtpfit.ingest import ingest_table
from gtpfit.ingest.checks import normalise_accept
from gtpfit.ingest.detect import detect, summarize
from gtpfit.ingest.readers import Source, UnreadableFileError, headers, parts, read, skipped_parts
from gtpfit.ingest import readers

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "sample_client"
ITEMS = SAMPLE / "item_master.csv"
ORDERS = SAMPLE / "orders.csv"


def _patch_member(zpath: Path, member: str, *, flag: int | None = None, method: int | None = None) -> None:
    """Set a member's general-purpose flag bits and/or compression method in its local and central headers."""
    b = bytearray(zpath.read_bytes())
    name = member.encode()
    i = b.find(b"PK\x01\x02")
    while i != -1:
        n = struct.unpack_from("<H", b, i + 28)[0]
        if bytes(b[i + 46:i + 46 + n]) == name:
            local = struct.unpack_from("<I", b, i + 42)[0]
            if flag is not None:
                struct.pack_into("<H", b, i + 8, struct.unpack_from("<H", b, i + 8)[0] | flag)
                struct.pack_into("<H", b, local + 6, struct.unpack_from("<H", b, local + 6)[0] | flag)
            if method is not None:
                struct.pack_into("<H", b, i + 10, method)
                struct.pack_into("<H", b, local + 8, method)
        i = b.find(b"PK\x01\x02", i + 4)
    zpath.write_bytes(bytes(b))


def _zip(path: Path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, src in members.items():
            z.write(src, name)
    return path


def _encrypted_office(path: Path) -> Path:
    """What Excel writes for a workbook saved with a password: an OLE2 file with an EncryptedPackage stream."""
    head = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
    path.write_bytes(head + b"\x00" * 1024 + "EncryptedPackage".encode("utf-16-le") + b"\x00" * 2048)
    return path


# ---- readers -------------------------------------------------------------------------------------------

def test_encrypted_zip_member_is_skipped_and_named(tmp_path):
    z = _zip(tmp_path / "extract.zip", {"orders.csv": ORDERS, "Inbound data.xlsx": ITEMS})
    _patch_member(z, "Inbound data.xlsx", flag=0x1)
    with zipfile.ZipFile(z) as zf, pytest.raises(RuntimeError, match="encrypted"):
        zf.open("Inbound data.xlsx")  # the patched member is refused exactly like a real one
    assert parts(z) == ["orders.csv"]
    skipped = skipped_parts(z)
    assert [m for m, _ in skipped] == ["Inbound data.xlsx"]
    assert "password protected" in skipped[0][1]


def test_unsupported_zip_compression_is_skipped(tmp_path):
    z = _zip(tmp_path / "extract.zip", {"orders.csv": ORDERS, "items.csv": ITEMS})
    _patch_member(z, "items.csv", method=9)  # Deflate64: zipfile cannot decompress it
    assert parts(z) == ["orders.csv"]
    assert "re-zip" in dict(skipped_parts(z))["items.csv"]


def test_password_protected_office_file(tmp_path):
    p = _encrypted_office(tmp_path / "items.xlsx")
    with pytest.raises(UnreadableFileError, match="password protected"):
        parts(p)
    with pytest.raises(UnreadableFileError, match="password protected"):
        headers(Source(p))


def test_truncated_gzip_is_damaged(tmp_path):
    p = tmp_path / "orders.csv.gz"
    p.write_bytes(gzip.compress(ORDERS.read_bytes())[:-200])
    with pytest.raises(UnreadableFileError, match="damaged"):
        read(Source(p))


def test_corrupt_sqlite(tmp_path):
    p = tmp_path / "wms.sqlite"
    p.write_bytes(b"SQLite format 3\x00" + b"\x07" * 4000)
    with pytest.raises(UnreadableFileError, match="database"):
        parts(p)


def test_locked_file(tmp_path, monkeypatch):
    p = tmp_path / "orders.csv"
    shutil.copy(ORDERS, p)

    def locked(*a, **k):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")
    monkeypatch.setattr(readers, "open", locked, raising=False)
    with pytest.raises(UnreadableFileError, match="open in another program"):
        headers(Source(p))


def test_choose_one_errors_pass_through(tmp_path):
    """ValueErrors that ask the consultant to choose stay as they are; only crashes are reworded."""
    z = _zip(tmp_path / "two.zip", {"a.csv": ORDERS, "b.csv": ITEMS})
    with pytest.raises(ValueError, match="choose one"):
        headers(Source(z))


# ---- detect: listed, never crashing --------------------------------------------------------------------

def test_detect_lists_unreadable_and_keeps_the_rest(tmp_path):
    folder = tmp_path / "client"
    folder.mkdir()
    shutil.copy(ITEMS, folder / "item_master.csv")
    z = _zip(folder / "extract.zip", {"orders.csv": ORDERS, "Inbound data.xlsx": ITEMS})
    _patch_member(z, "Inbound data.xlsx", flag=0x1)
    _encrypted_office(folder / "receipts.xlsx")
    (folder / "inventory.csv.gz").write_bytes(gzip.compress(ITEMS.read_bytes())[:-200])
    (folder / "half.zip").write_bytes(z.read_bytes()[: len(z.read_bytes()) // 2])
    files = sorted(folder.iterdir())
    dets = detect(files)
    by = {(d.file.name, d.part): d for d in dets}
    assert by[("item_master.csv", None)].table == "item_master"
    assert by[("extract.zip", "orders.csv")].table == "orders"
    bad = {k: d.unreadable for k, d in by.items() if d.unreadable}
    assert set(bad) == {("extract.zip", "Inbound data.xlsx"), ("receipts.xlsx", None), ("inventory.csv.gz", None),
                        ("half.zip", None)}
    assert "password protected" in bad[("extract.zip", "Inbound data.xlsx")]
    assert "password protected" in bad[("receipts.xlsx", None)]
    assert "damaged" in bad[("inventory.csv.gz", None)] and "damaged" in bad[("half.zip", None)]
    assert all(d.table is None for d in dets if d.unreadable)
    s = summarize(files, dets)
    assert s.unreadable == 3  # receipts.xlsx, inventory.csv.gz, half.zip (the zip with a bad member has data)
    assert "3 can't be read" in s.text


def test_summary_text_unchanged_without_unreadable(tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    shutil.copy(ITEMS, folder / "item_master.csv")
    files = sorted(folder.iterdir())
    assert summarize(files, detect(files)).text == "1 files found: 1 data, 0 ignored, 0 zips"


# ---- the Confirm step: listed, warned, the rest still mapped ------------------------------------------

def test_confirm_step_lists_unreadable_and_maps_the_rest(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    folder = tmp_path / "client"
    folder.mkdir()
    for f in ("item_master.csv", "receipts.csv", "inventory_snapshots.csv"):
        shutil.copy(SAMPLE / f, folder / f)
    z = _zip(folder / "extract.zip", {"orders.csv": ORDERS, "USDC Inbound data.xlsx": ITEMS})
    _patch_member(z, "USDC Inbound data.xlsx", flag=0x1)
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    at = AppTest.from_string("from gtpfit.app.ingest import page\npage()\n", default_timeout=300)
    at.session_state["ing::client_pick"] = "Acme"
    at.run()
    at.text_input(key="ing::folder_paste").input(str(folder)).run()
    at.button(key="ing::folder_open").click().run()
    assert not at.exception, at.exception
    assert at.session_state["ing::step"] == 2
    warn = " ".join(w.value for w in at.warning)
    assert "1 file can't be read" in warn and "USDC Inbound data.xlsx" in warn and "password protected" in warn
    assert not at.error  # every required table still found
    assert not at.button(key="ing::run").disabled


def test_unexpected_error_on_a_step_is_shown_not_fatal(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    at = AppTest.from_string(
        "from gtpfit.app import ingest\n"
        "def boom():\n    raise RuntimeError('kaboom')\n"
        "ingest._SCREENS[2] = boom\n"
        "ingest.page()\n", default_timeout=120)
    at.session_state["ing::step"] = 2
    at.run()  # the script itself finishes; the traceback is an element inside "Details"
    assert [x.message for x in at.exception] == ["kaboom"]
    assert any("Something went wrong on Confirm files" in e.value and "kaboom" in e.value for e in at.error)
    at.button(key="ing::error_back").click().run()
    assert at.session_state["ing::step"] == 1


# ---- pipeline: a block, never a crash ------------------------------------------------------------------

def test_ingest_table_blocks_on_unreadable_source(tmp_path):
    res = ingest_table(Source(_encrypted_office(tmp_path / "items.xlsx")), "item_master")
    codes = {(i.code, i.severity) for i in res.issues}
    assert ("unreadable_file", "block") in codes
    assert "password protected" in next(i.message for i in res.issues if i.code == "unreadable_file")
    with pytest.raises(ValueError, match="cannot be accepted"):
        normalise_accept({"item_master:unreadable_file": "client says it is fine"})
