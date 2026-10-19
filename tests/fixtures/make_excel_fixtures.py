"""Make the Excel test fixtures from synthetic data (run once; needs Excel on Windows).

    python tests/fixtures/make_excel_fixtures.py

Writes excel_sample.xlsx (sheets: 'Read me', 'Items' under two title rows,
'Orders'), then has Excel save it as .xls, .xlsb and .xlsm.  Python can read
those formats but not write them, which is why they are committed fixtures.
Synthetic data only.
"""
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

from gtpfit.synth import generate, load_profile

HERE = Path(__file__).resolve().parent
FORMATS = {".xls": 56, ".xlsb": 50, ".xlsm": 52}  # Excel XlFileFormat codes


def main():
    with tempfile.TemporaryDirectory() as tmp:
        generate(load_profile(n_skus=150, ecom_orders_per_day=40, b2b_orders_per_day=2, start_date="2025-01-06",
                              days=10, seed=3), tmp)
        items = pd.read_csv(Path(tmp) / "item_master.csv", dtype=str, keep_default_na=False)
        orders = pd.read_csv(Path(tmp) / "orders.csv", dtype=str, keep_default_na=False).head(400)
    src = HERE / "excel_sample.xlsx"
    with pd.ExcelWriter(src, engine="openpyxl") as w:
        pd.DataFrame({"Notes": ["Exported from WMS", "Do not edit"]}).to_excel(w, sheet_name="Read me", index=False)
        items.to_excel(w, sheet_name="Items", index=False, startrow=3)
        w.sheets["Items"]["A1"], w.sheets["Items"]["A2"] = "ACME Corp item master export", "Run date: 2025-02-14"
        orders.to_excel(w, sheet_name="Orders", index=False)
    for ext, code in FORMATS.items():
        out = HERE / f"excel_sample{ext}"
        out.unlink(missing_ok=True)
        ps = (f"$x = New-Object -ComObject Excel.Application; $x.DisplayAlerts = $false; "
              f"$w = $x.Workbooks.Open('{src}'); $w.SaveAs('{out}', {code}); $w.Close($false); $x.Quit()")
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], check=True)
        print("wrote", out.name, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
