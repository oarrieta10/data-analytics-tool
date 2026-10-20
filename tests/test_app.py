"""The guided Ingest page (Streamlit AppTest, no browser): every step renders, a client folder is read in
place (nothing copied) and goes from Choose folder to Done, a blocked one needs a reasoned acceptance,
mappings / units / recent folders are remembered per client.

The native folder dialog cannot be driven by AppTest, so tests open a folder the other way a consultant
can: paste its path and click Open.
"""
import shutil
import tempfile
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "sample_client"  # frozen sample client
FILES = ("item_master.csv", "orders.csv", "receipts.csv", "inventory_snapshots.csv")
INGEST = """
from gtpfit.app.ingest import page
page()
"""


def _copy(dst: Path, **frames) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        shutil.copy(SAMPLE / f, dst / f)
    for name, df in frames.items():
        df.to_csv(dst / f"{name}.csv", index=False)
    return dst


def _next(at, key):
    at.button(key=key).click().run()
    assert not at.exception, at.exception
    return at


def _app(tmp_path, monkeypatch, folder=None, client="Acme", **state):
    """Start the page; with a folder, open it (paste + Open), which lands on step 2."""
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    at = AppTest.from_string(INGEST, default_timeout=300)
    if client:
        at.session_state["ing::client_pick"] = "New client…"  # the list defaults to the most recent client
        at.session_state["ing::client_new"] = client
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, at.exception
    if folder:
        at.text_input(key="ing::folder_paste").input(str(folder)).run()
        at = _next(at, "ing::folder_open")
    return at


def _again(tmp_path, monkeypatch, folder=None):
    """A later session for the same (now recent) client."""
    return _app(tmp_path, monkeypatch, folder=folder, client=None, **{"ing::client_pick": "Acme"})


def _step(at) -> int:
    return at.session_state["ing::step"]


def _ws(tmp_path) -> Path:
    return tmp_path / "ws" / "Acme"


# ---- step 1: choose a folder ----------------------------------------------------------------------

def test_choose_folder_screen(tmp_path, monkeypatch):
    at = _app(tmp_path, monkeypatch, client=None)
    assert _step(at) == 1
    text = " ".join(m.value for m in at.markdown)
    assert "Pick the folder holding the client's files. Nothing is copied." in text
    assert "drop" not in text.lower()
    assert at.button(key="ing::choose").label.startswith("Choose folder")
    assert at.button(key="ing::choose").disabled  # name the client first
    assert not at.get("file_uploader") and not [r for r in at.radio if "Files from" in r.label]
    for card in ("Choose folder", "Confirm files", "Review"):
        assert card in text
    for table in ("Item master", "Order history", "Receipts (inbound)", "Inventory snapshots"):
        assert table in text
    assert "Data stays on this computer" in text
    assert any(e.label.startswith("Advanced thresholds") for e in at.expander)


def test_opening_a_folder_shows_summary_and_goes_to_confirm(tmp_path, monkeypatch):
    folder = _copy(tmp_path / "client")
    (folder / "notes.md").write_text("# notes", encoding="utf-8")
    at = _app(tmp_path, monkeypatch, folder=folder)
    assert _step(at) == 2
    text = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "5 files found: 4 data, 1 ignored, 0 zips" in text and str(folder) in text


def test_bad_path_stays_on_step_one(tmp_path, monkeypatch):
    at = _app(tmp_path, monkeypatch)
    at.text_input(key="ing::folder_paste").input(str(tmp_path / "nope")).run()
    at = _next(at, "ing::folder_open")
    assert _step(at) == 1 and any("not a folder" in e.value.lower() for e in at.error)


def test_dialog_helper():
    from types import SimpleNamespace
    from gtpfit.app.ingest.state import choose_folder_dialog
    ok = lambda *a, **k: SimpleNamespace(returncode=0, stdout="C:/data/acme\n")
    cancel = lambda *a, **k: SimpleNamespace(returncode=0, stdout="\n")

    def broken(*a, **k):
        raise OSError("no display")
    assert choose_folder_dialog(run=ok) == "C:/data/acme"
    assert choose_folder_dialog(run=cancel) is None
    assert choose_folder_dialog(run=broken) is None


def test_recent_folders_remembered_per_client(tmp_path, monkeypatch):
    import yaml
    folder = _copy(tmp_path / "client")
    _app(tmp_path, monkeypatch, folder=folder)
    info = yaml.safe_load((_ws(tmp_path) / "client.yaml").read_text(encoding="utf-8"))
    assert info["recent_folders"][0] == str(folder)
    at = _again(tmp_path, monkeypatch)  # Acme again, no folder yet
    picks = [b for b in at.button if b.key and b.key.startswith("ing::recent::")]
    assert [b.label for b in picks] == [str(folder)]
    at = _next(at, picks[0].key)
    assert _step(at) == 2
    beta = _app(tmp_path, monkeypatch, client="Beta")
    assert not [b for b in beta.button if b.key and b.key.startswith("ing::recent::")]


# ---- the clean path, read in place ------------------------------------------------------------------

def _snapshot(folder: Path) -> dict:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.rglob("*")}


def test_clean_client_read_in_place_through_all_steps(tmp_path, monkeypatch):
    from gtpfit.ingest import readers
    folder = _copy(tmp_path / "client")
    before = _snapshot(folder)
    tmp_before = {p.name for p in Path(tempfile.gettempdir()).glob("gtpfit_upload_*")}
    extracted_before = len(list(readers._TMP.iterdir()))
    at = _app(tmp_path, monkeypatch, folder=folder)
    assert _step(at) == 2
    files = next(d.value for d in at.dataframe if "Use as" in d.value.columns).set_index("File")
    assert files.loc["orders.csv", "Use as"] == "Orders" and files.loc["item_master.csv", "Rows"] != "—"
    assert any(lbl.startswith("Column mapping: Item master (") for lbl in (e.label for e in at.expander))
    at = _next(at, "ing::run")
    assert _step(at) == 3
    assert any("Proceed with cautions" in w.value for w in at.warning)
    assert any("COVERAGE" in m.value for m in at.markdown)
    at = _next(at, "ing::to4")
    assert _step(at) == 4 and any("Ingest complete" in s.value for s in at.success)
    out = _ws(tmp_path) / "_ingest"
    for f in ("orders.parquet", "data_receipt.html", "rejects_for_client.csv", "accepted_blocks.csv"):
        assert (out / f).exists(), f
    assert str(folder) in (out / "data_receipt.html").read_text(encoding="utf-8")
    # nothing copied, nothing written beside the client's files
    assert _snapshot(folder) == before
    assert {p.name for p in Path(tempfile.gettempdir()).glob("gtpfit_upload_*")} == tmp_before
    assert len(list(readers._TMP.iterdir())) == extracted_before


def test_missing_required_column_disables_run(tmp_path, monkeypatch):
    im = pd.read_csv(SAMPLE / "item_master.csv", dtype=str).drop(columns=["Case Pack"])
    at = _app(tmp_path, monkeypatch, folder=_copy(tmp_path / "client", item_master=im))
    assert "Case quantity is required" in " ".join(m.value for m in at.markdown)
    assert at.button(key="ing::run").disabled


# ---- the blocked path: a reason is required, then Continue opens --------------------------------------

def test_block_needs_reasoned_acceptance(tmp_path, monkeypatch):
    o = pd.read_csv(SAMPLE / "orders.csv", dtype=str)
    o["_d"] = pd.to_datetime(o["Order Date"], format="%m/%d/%Y")
    clipped = o.sort_values("_d", kind="stable").head(6000).drop(columns="_d")
    folder = _copy(tmp_path / "client", orders=clipped)
    at = _app(tmp_path, monkeypatch, folder=folder, **{"thr::excel_row_limits": "6000"})
    at = _next(at, "ing::run")
    assert _step(at) == 3
    assert any("BLOCKED" in e.value for e in at.error)
    assert at.button(key="ing::to4").disabled
    key = "orders:excel_row_limit"
    text = " ".join(m.value for m in at.markdown)
    assert "Excel's row limit" in text and "Design impact" in text
    assert at.button(key=f"accok::{key}").disabled  # no reason yet
    at = _next(at, f"req::{key}")  # asked the client to resend: still blocked
    assert at.button(key="ing::to4").disabled
    at.text_input(key=f"accwhy::{key}").input("Client confirmed the export ends there").run()
    at = _next(at, f"accok::{key}")
    assert not [e for e in at.error if "BLOCKED" in e.value]
    at = _next(at, "ing::to4")
    out = _ws(tmp_path) / "_ingest"
    ab = pd.read_csv(out / "accepted_blocks.csv")
    assert list(ab["reason"]) == ["Client confirmed the export ends there"] and ab["accepted_at"].notna().all()
    receipt = (out / "data_receipt.html").read_text(encoding="utf-8")
    assert "Client confirmed the export ends there" in receipt and "Asked of the client" in receipt


def test_start_another_client(tmp_path, monkeypatch):
    at = _app(tmp_path, monkeypatch, folder=_copy(tmp_path / "client"))
    at = _next(_next(_next(at, "ing::run"), "ing::to4"), "ing::restart")
    assert _step(at) == 1
    assert "Acme" in at.selectbox(key="ing::client_pick").options  # now a recent client


# ---- pure helpers used by the Confirm step ------------------------------------------------------------

def test_default_mapping_prefers_profile_columns():
    from gtpfit.app.ingest import default_mapping
    hdr = ["Item Number", "Qty Received", "createdat", "updatedat"]
    auto = default_mapping("receipts", hdr, None)
    assert auto["sku"] == "Item Number" and "receipt_date" not in auto
    prof = default_mapping("receipts", hdr, {"sku": "Item Number", "receipt_date": "updatedat", "qty": "gone"})
    assert prof == {"sku": "Item Number", "receipt_date": "updatedat"}  # columns not in the file are dropped


def test_effective_profile_merges_choices():
    import yaml
    from gtpfit.app.ingest import effective_profile_text
    assert effective_profile_text("name: x\nunits: {length: in}\n", {}) == "name: x\nunits: {length: in}\n"
    out = yaml.safe_load(effective_profile_text("name: x\ncolumns:\n  orders: {sku: SKU}\n",
                                                {"receipts": {"receipt_date": "createdat"}}))
    assert out["columns"] == {"orders": {"sku": "SKU"}, "receipts": {"receipt_date": "createdat"}}


def test_mapping_problems_flag_missing_and_duplicates():
    from gtpfit.app.ingest.confirm import mapping_problems
    probs = mapping_problems("item_master", {"sku": ["A"], "unit_l": ["L", "Len"]})
    assert any("claimed by 2 columns" in p for p in probs)
    assert any("Unit weight is required" in p for p in probs)
    full = {f: [f] for f in ("sku", "unit_l", "unit_w", "unit_h", "unit_weight", "case_qty")}
    assert mapping_problems("item_master", full) == []


def test_chosen_column_lets_unrecognised_date_through(tmp_path):
    """A receipts export whose date column is 'createdat': refused until the consultant picks it."""
    import yaml
    from gtpfit.app.ingest import effective_profile_text
    from gtpfit.ingest import ingest_sources, parse_wms_profile, Source
    rec = pd.read_csv(SAMPLE / "receipts.csv", dtype=str)
    rec = rec.rename(columns={"Receipt Date": "createdat"}).assign(updatedat="2030-01-01")
    p = tmp_path / "inbound.csv"
    rec.to_csv(p, index=False)
    assign = {"receipts": [Source(p)]}
    before = ingest_sources(assign, strict=False)
    assert "missing_required_column" in set(before.issues.loc[before.issues.table == "receipts", "code"])
    cols = {"receipt_id": "Receipt Nbr", "po": "PO Nbr", "receipt_date": "createdat", "sku": "Item Number",
            "qty": "Qty Received"}
    prof = parse_wms_profile(yaml.safe_load(effective_profile_text(None, {"receipts": cols})))
    after = ingest_sources(assign, profile=prof, strict=False)
    assert len(after.tables["receipts"]) == len(rec)
    assert not set(after.issues.loc[after.issues.table == "receipts", "severity"]) & {"block"}


# ---- per-client memory: mapping and units saved automatically, synonyms learned --------------------------

@pytest.fixture
def learned(tmp_path, monkeypatch):
    monkeypatch.setenv("GTPFIT_SYNONYMS", str(tmp_path / "learned.yaml"))  # never the repo's file
    return tmp_path / "learned.yaml"


def test_no_profile_picker_and_no_save_button(tmp_path, monkeypatch, learned):
    at = _app(tmp_path, monkeypatch)
    assert not [s for s in at.selectbox if "WMS" in s.label]
    at.text_input(key="ing::folder_paste").input(str(_copy(tmp_path / "client"))).run()
    at = _next(_next(at, "ing::folder_open"), "ing::run")
    assert not [b for b in at.button if "Save WMS profile" in b.label]


def test_mapping_saved_automatically_and_reloaded(tmp_path, monkeypatch, learned):
    import yaml
    folder = _copy(tmp_path / "client")
    _next(_app(tmp_path, monkeypatch, folder=folder), "ing::run")
    prof = yaml.safe_load((_ws(tmp_path) / "wms_acme.yaml").read_text(encoding="utf-8"))
    assert prof["columns"]["item_master"]["sku"] == "Item Number" and "Item Number" in prof["headers"]["item_master"]
    # same extract resent: reused, and said so
    at = _again(tmp_path, monkeypatch, folder)
    labels = [e.label for e in at.expander if e.label.startswith("Column mapping")]
    assert labels and all(lbl.endswith("last confirmed mapping") for lbl in labels)
    assert any("Using last confirmed mapping" in i.value for i in at.info)
    # a different item-master export: matched afresh (the other tables still reuse theirs)
    im = pd.read_csv(SAMPLE / "item_master.csv", dtype=str).rename(columns={
        "Item Description": "Desc", "Category": "Dept", "Sub Category": "Class", "Case Length": "Ctn L",
        "Case Width": "Ctn W", "Case Height": "Ctn H"})
    at = _again(tmp_path, monkeypatch, _copy(tmp_path / "client2", item_master=im))
    by = {lbl.split(":")[1].split("(")[0].strip(): lbl for lbl in (e.label for e in at.expander)
          if lbl.startswith("Column mapping")}
    assert not by["Item master"].endswith("last confirmed mapping")
    assert by["Order history"].endswith("last confirmed mapping")


def test_unit_decision_persists_per_client(tmp_path, monkeypatch, learned):
    import yaml
    im = pd.read_csv(SAMPLE / "item_master.csv", dtype={"Item Number": str})
    dims = ["Unit Length", "Unit Width", "Unit Height", "Case Length", "Case Width", "Case Height"]
    im[dims] = (im[dims] * 2.54).round(2)                                   # centimetres,
    im[["Unit Weight", "Case Weight"]] = (im[["Unit Weight", "Case Weight"]] / 2.20462262185).round(3)  # kilograms
    folder = _copy(tmp_path / "client", item_master=im)                     # no unit in any column name
    at = _next(_app(tmp_path, monkeypatch, folder=folder), "ing::run")
    key = "item_master:unit_ambiguous"
    assert any("BLOCKED" in e.value for e in at.error)
    pick = at.selectbox(key=f"unitpick::{key}")
    assert any(o.startswith("cm / kg") for o in pick.options)
    pick.select(next(o for o in pick.options if o.startswith("cm / kg"))).run()
    at = _next(at, f"unituse::{key}")
    assert not [e for e in at.error if "BLOCKED" in e.value]
    prof = yaml.safe_load((_ws(tmp_path) / "wms_acme.yaml").read_text(encoding="utf-8"))
    assert prof["units"] == {"length": "cm", "weight": "kg"}
    # next session, same client: the decision is already made
    at = _next(_again(tmp_path, monkeypatch, folder), "ing::run")
    assert not [e for e in at.error if "BLOCKED" in e.value]
    assert not at.button(key="ing::to4").disabled


def test_remember_synonym_teaches_automatic_matching(tmp_path, monkeypatch, learned):
    import yaml
    im = pd.read_csv(SAMPLE / "item_master.csv", dtype=str).rename(columns={"Item Number": "STOCKCODE"})
    folder = _copy(tmp_path / "client", item_master=im)
    # a hand-made match confirmed earlier for this client: STOCKCODE is the SKU
    cols = {"sku": "STOCKCODE", "description": "Item Description", "unit_l": "Unit Length", "unit_w": "Unit Width",
            "unit_h": "Unit Height", "unit_weight": "Unit Weight", "case_qty": "Case Pack"}
    _ws(tmp_path).mkdir(parents=True)
    (_ws(tmp_path) / "wms_acme.yaml").write_text(yaml.safe_dump(
        {"name": "Acme", "columns": {"item_master": cols}, "headers": {"item_master": list(im.columns)},
         "saved_at": "2026-10-20T10:00:00"}), encoding="utf-8")
    at = _again(tmp_path, monkeypatch, folder)
    btn = "syn::item_master::sku::STOCKCODE"
    assert at.button(key=btn).label.startswith("Remember this synonym")
    at = _next(at, btn)
    assert "stockcode" in learned.read_text(encoding="utf-8")
    # a brand-new client with the same kind of export: matched automatically now
    at = _app(tmp_path, monkeypatch, folder=folder, client="Beta")
    assert not [m for m in at.markdown if "SKU / item number is required" in m.value]
    assert not at.button(key="ing::run").disabled
