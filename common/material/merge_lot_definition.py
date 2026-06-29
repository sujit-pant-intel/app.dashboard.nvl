"""
Merge lot definition CSVs:
- Old file = master (keep all rows)
- Add rows from new file that don't exist in old file
- Populate Material Type and Stepping for new rows based on AIO/BB column
"""

import csv
import os

OLD_FILE = r"c:\scripts\app.yield.nvl\shared\material\8PF5CV-NVL816-BLLC_L0_lot_definition_l1 - 06262026.csv"
NEW_FILE = r"c:\scripts\app.yield.nvl\shared\material\8PF5CV-NVL816-BLLC_L0_lot_definition_l1 - new.csv"
OUT_FILE = r"c:\scripts\app.yield.nvl\shared\material\8PF5CV-NVL816-BLLC_L0_lot_definition_l1.csv"

# Column mapping: new file col -> old file col
COL_MAP = {
    "TSMC Lot6": "TSMC_LOT",
    "Intel Lot7": "INTEL_LOT7",
    "WaferNo": "WaferID",
}

def material_for(aio_bb: str) -> tuple[str, str]:
    """Return (Material Type, Stepping) based on AIO/BB value."""
    if "BB" in aio_bb.upper():
        return "NVL816-BLLC-L5 AIO+BB,VyCD+", "L5"
    else:
        return "NVL816-BLLC-L0 AIO", "L0"

# Load old file
with open(OLD_FILE, newline="", encoding="utf-8-sig") as f:
    old_reader = csv.DictReader(f)
    old_cols = old_reader.fieldnames
    old_rows = list(old_reader)

# Build key set from old file (INTEL_LOT7 + WaferID)
old_keys = set()
for r in old_rows:
    old_keys.add((r["INTEL_LOT7"].strip(), r["WaferID"].strip()))

# Load new file
with open(NEW_FILE, newline="", encoding="utf-8-sig") as f:
    new_reader = csv.DictReader(f)
    new_rows = list(new_reader)

# Find rows in new that are not in old
added = []
for r in new_rows:
    lot7 = r["Intel Lot7"].strip()
    wafer = r["WaferNo"].strip()
    if (lot7, wafer) not in old_keys:
        # Map columns to old schema
        new_row = {col: "" for col in old_cols}
        for new_col, old_col in COL_MAP.items():
            new_row[old_col] = r.get(new_col, "")
        # Copy common columns
        for col in ["Lot#", "TSMC WaferID", "Intel WaferID", "AIO/BB", "MG4 split",
                    "Device Skew", "Vy CD+", "Remark", "inline scrap"]:
            if col in r:
                new_row[col] = r[col]
        # Set Material Type and Stepping
        mat, step = material_for(new_row["AIO/BB"])
        new_row["Material Type, Skew, BEOL Skew"] = mat
        new_row["Stepping"] = step
        added.append(new_row)

print(f"Old rows: {len(old_rows)}")
print(f"New rows to add: {len(added)}")

# Preview added rows
print("\nRows being added:")
for r in added:
    print(f"  Lot#={r['Lot#']!r:10s}  INTEL_LOT7={r['INTEL_LOT7']!r:12s}  WaferID={r['WaferID']!r:4s}  AIO/BB={r['AIO/BB']!r:10s}  Material={r['Material Type, Skew, BEOL Skew']!r}  Stepping={r['Stepping']!r}  Remark={r['Remark']!r}")

# Build key set from new file
new_keys = set()
for r in new_rows:
    new_keys.add((r["Intel Lot7"].strip(), r["WaferNo"].strip()))

# Remove old rows that no longer exist in new file
kept_old = [r for r in old_rows if (r["INTEL_LOT7"].strip(), r["WaferID"].strip()) in new_keys]
removed_count = len(old_rows) - len(kept_old)
print(f"Old rows removed (not in new): {removed_count}")

# Merge: kept old rows + added rows
merged = kept_old + added
print(f"\nTotal merged rows: {len(merged)}")

# Write merged file
with open(OUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=old_cols)
    writer.writeheader()
    writer.writerows(merged)

print(f"\nSaved to: {OUT_FILE}")
