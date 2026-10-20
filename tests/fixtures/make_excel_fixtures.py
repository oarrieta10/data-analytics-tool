"""Make the Excel test fixtures from the sample client (run once; needs Excel on Windows).

    python tests/fixtures/make_excel_fixtures.py

Writes excel_sample.xlsx (sheets: 'Read me', 'Items' under two title rows,
'Orders') from tests/fixtures/sample_client, then has Excel save it as .xls,
.xlsb and .xlsm.  Python can read those formats but not write them, which is
why they are committed fixtures.  No real client data.
"""
import subprocess
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_client"
FORMATS = {".xls": 56, ".xlsb": 50, ".xlsm": 52}  # Excel XlFileFormat codes
ITEMS, ORDERS = 150, 400  # rows kept: small fixtures, still several pages of data


def main():
    items = pd.read_csv(SAMPLE / "item_master.csv", dtype=str, keep_default_na=False).head(ITEMS)
    orders = pd.read_csv(SAMPLE / "orders.csv", dtype=str, keep_default_na=False).head(ORDERS)
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
