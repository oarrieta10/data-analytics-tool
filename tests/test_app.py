"""The guided Ingest page (Streamlit AppTest, no browser): every step renders, a clean client goes from
Upload to Done with no configuration, a blocked one needs a reasoned acceptance, dropped files are removed.

The uploader itself cannot be driven by AppTest, so files are placed where the page reads them: a folder
("A folder" mode) or the session's temp upload folder ("Drop files" mode).
"""
import shutil
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


def _app(tmp_path, monkeypatch, folder=None, upload_dir=None, client="Acme", **state):
    monkeypatch.setenv("GTPFIT_WORKSPACE", str(tmp_path / "ws"))
    at = AppTest.from_string(INGEST, default_timeout=300)
    if folder:
        at.session_state["ing::mode"] = "A folder"
        at.session_state["ing::folder_pick"] = "Other folder…"
        at.session_state["ing::folder_other"] = str(folder)
    if upload_dir:
        at.session_state["ing::tmpdir"] = str(upload_dir)
    if client:
        at.session_state["ing::client_new"] = client
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def _step(at) -> int:
    return at.session_state["ing::step"]


def _next(at, key):
    at.button(key=key).click().run()
    assert not at.exception, at.exception
    return at


# ---- step 1 ---------------------------------------------------------------------------------------

def test_upload_step_renders_empty(tmp_path, monkeypatch):
    at = _app(tmp_path, monkeypatch, client=None)
    assert not at.exception, at.exception
    assert _step(at) == 1
    assert at.button(key="ing::to2").disabled  # no client, no files
    text = " ".join(m.value for m in at.markdown)
    for slot in ("Item master", "Order history", "Receipts (inbound)", "Inventory snapshots"):
        assert slot in text
    assert "Data stays on this computer" in text
    assert any(e.label.startswith("Advanced thresholds") for e in at.expander)


def test_upload_slots_fill_from_detection(tmp_path, monkeypatch):
    at = _app(tmp_path, monkeypatch, folder=_copy(tmp_path / "client"))
    text = " ".join(m.value for m in at.markdown)
    for f in FILES:
        assert f"✅ {f}" in text
    assert not at.button(key="ing::to2").disabled


# ---- the clean path: Upload -> Confirm -> Review -> Done --------------------------------------------

def test_clean_client_goes_through_all_steps(tmp_path, monkeypatch):
    folder = _copy(tmp_path / "client")
    at = _next(_app(tmp_path, monkeypatch, folder=folder), "ing::to2")
    assert _step(at) == 2
    files = next(d.value for d in at.dataframe if "Use as" in d.value.columns).set_index("File")
    assert files.loc["orders.csv", "Use as"] == "Orders" and files.loc["item_master.csv", "Rows"] != "—"
    labels = [e.label for e in at.expander]
    assert any(lbl.startswith("Column mapping: Item master (") for lbl in labels)
    assert not at.button(key="ing::run").disabled
    at = _next(at, "ing::run")
    assert _step(at) == 3
    assert any("Proceed with cautions" in w.value for w in at.warning)  # short history, no peak, guessed units
    assert not [e for e in at.error if "BLOCKED" in e.value]
    assert any("COVERAGE" in m.value for m in at.markdown)
    assert not at.button(key="ing::to4").disabled
    at = _next(at, "ing::to4")
    assert _step(at) == 4
    out = folder / "_ingest"
    for f in ("orders.parquet", "data_receipt.html", "rejects_for_client.csv", "accepted_blocks.csv"):
        assert (out / f).exists(), f
    assert any("Ingest complete" in s.value for s in at.success)


def test_missing_required_column_disables_run(tmp_path, monkeypatch):
    im = pd.read_csv(SAMPLE / "item_master.csv", dtype=str).drop(columns=["Case Pack"])
    at = _next(_app(tmp_path, monkeypatch, folder=_copy(tmp_path / "client", item_master=im)), "ing::to2")
    red = " ".join(m.value for m in at.markdown)
    assert "Case quantity is required" in red
    assert at.button(key="ing::run").disabled


# ---- the blocked path: a reason is required, then Continue opens --------------------------------------

def test_block_needs_reasoned_acceptance(tmp_path, monkeypatch):
    o = pd.read_csv(SAMPLE / "orders.csv", dtype=str)
    o["_d"] = pd.to_datetime(o["Order Date"], format="%m/%d/%Y")
    clipped = o.sort_values("_d", kind="stable").head(6000).drop(columns="_d")
    folder = _copy(tmp_path / "client", orders=clipped)
    at = _app(tmp_path, monkeypatch, folder=folder, **{"thr::excel_row_limits": "6000"})
    at = _next(_next(at, "ing::to2"), "ing::run")
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
    assert not at.button(key="ing::to4").disabled
    at = _next(at, "ing::to4")
    out = folder / "_ingest"
    ab = pd.read_csv(out / "accepted_blocks.csv")
    assert list(ab["reason"]) == ["Client confirmed the export ends there"] and ab["accepted_at"].notna().all()
    receipt = (out / "data_receipt.html").read_text(encoding="utf-8")
    assert "Client confirmed the export ends there" in receipt and "Asked of the client" in receipt


# ---- dropped files live in a temp folder until Done ----------------------------------------------------

def test_dropped_files_removed_after_done(tmp_path, monkeypatch):
    up = _copy(tmp_path / "gtpfit_upload_test")
    at = _app(tmp_path, monkeypatch, upload_dir=up)
    at = _next(_next(_next(at, "ing::to2"), "ing::run"), "ing::to4")
    assert _step(at) == 4
    assert not up.exists()  # temp uploads gone
    kept = tmp_path / "ws" / "Acme"
    assert (kept / "_ingest" / "data_receipt.html").exists() and (kept / "client.yaml").exists()
    at = _next(at, "ing::restart")
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
