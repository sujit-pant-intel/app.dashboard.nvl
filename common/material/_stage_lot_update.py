"""
_stage_lot_update.py
====================
Creates:
  1. Backup:  8PF5CV-NVL816-BLLC_L0_lot_definition_l1_backup_20260511.csv
  2. Staged:  8PF5CV-NVL816-BLLC_L0_lot_definition_l1_ww19staged.csv

Changes applied (based on WW19 email vs current CSV diff, May 11 2026):
  A. Add Lot 4 primary (K8A236 / Q603S6V) — 10 wafers — status SCRAP
     (was missing from CSV; makeup lots K8A387 + K9H927 already present)
  B. Fill K9H927 Intel lot IDs from WW19:
       makeup lot-4  (wafers 12,13)  → Q552S9P0Z2
       makeup lot-10 (wafers 7-11)   → Q552S9P0Z1
       makeup lot-7  (no wafer rows) → Q552S9P0Z0  (1 wfr — add row)
  C. Fill K9H922 Intel lot ID → Q552S9R0Z0  (makeup lot-7, 1 wfr)
  D. Update K8A359 remark  "makeup lot-16" → "makeup lot-16 & 18"
     (email labels it "Lot 16 & 18 makeup"; rows 1-2 are lot-16, 3-5 are lot-18)
"""

import csv, shutil, os

HERE     = os.path.dirname(os.path.abspath(__file__))
ORIG     = os.path.join(HERE, "8PF5CV-NVL816-BLLC_L0_lot_definition_l1.csv")
BACKUP   = os.path.join(HERE, "8PF5CV-NVL816-BLLC_L0_lot_definition_l1_backup_20260511.csv")
STAGED   = os.path.join(HERE, "8PF5CV-NVL816-BLLC_L0_lot_definition_l1_ww19staged.csv")

# ── 1. backup ──────────────────────────────────────────────────────────────
shutil.copy2(ORIG, BACKUP)
print(f"Backup created: {os.path.basename(BACKUP)}")

# ── 2. read original ────────────────────────────────────────────────────────
with open(ORIG, newline="", encoding="utf-8-sig") as f:
    reader = csv.reader(f)
    header = next(reader)
    rows   = list(reader)

print(f"Original rows (excl. header): {len(rows)}")

# Column indices  (header printed for reference)
# Lot#  | Material Type, Skew...  | Material Type | Stepping |
# TSMC_LOT | INTEL_LOT7 | WaferID | TSMC WaferID | Intel WaferID |
# AIO/BB | MG4 split | Device Skew | Vy CD+ | Remark | inline scrap
COL_LOT       = 0
COL_MAT_FULL  = 1
COL_MAT       = 2
COL_STEP      = 3
COL_TSMC      = 4
COL_INTEL     = 5
COL_WAFER_NUM = 6
COL_TSMC_WID  = 7
COL_INTEL_WID = 8
COL_AIOBB     = 9
COL_MG4       = 10
COL_SKW       = 11
COL_VYCD      = 12
COL_REMARK    = 13
COL_SCRAP     = 14

updated_rows = []

for r in rows:
    # Extend short rows to full width
    while len(r) <= COL_SCRAP:
        r.append("")

    tsmc   = r[COL_TSMC].strip()
    remark = r[COL_REMARK].strip()

    # ── B: Fill K9H927 Intel lot IDs ─────────────────────────────────────
    if tsmc == "K9H927" and not r[COL_INTEL].strip():
        if remark == "makeup lot-4":
            r[COL_INTEL]    = "Q552S9P"   # 7-char Intel lot
            r[COL_TSMC_WID] = r[COL_TSMC_WID] or "K9H927.0L"  # sub-lot Z2
        elif remark == "makeup lot-10":
            r[COL_INTEL]    = "Q552S9P"   # 7-char Intel lot
            r[COL_TSMC_WID] = r[COL_TSMC_WID] or "K9H927.0K"  # sub-lot Z1
        elif remark == "makeup lot-7":
            # Replace the old empty placeholder row with proper values
            r[COL_INTEL]    = "Q552S9P"   # 7-char Intel lot
            r[COL_TSMC_WID] = "K9H927.0J" # sub-lot Z0
            # (WaferID/IntelWaferID remain blank — exact decimal wafer# TBD)

    # ── C: Fill K9H922 Intel lot ID ──────────────────────────────────────
    if tsmc == "K9H922" and not r[COL_INTEL].strip():
        r[COL_INTEL]    = "Q552S9R"   # 7-char Intel lot
        # sub-lot suffix stays in TSMC WaferID (K9H922.0M / Z0)

    # ── D: Update K8A359 remark ───────────────────────────────────────────
    if tsmc == "K8A359" and r[COL_REMARK].strip() == "makeup lot-16":
        wnum_s = r[COL_WAFER_NUM].strip()
        wnum   = int(wnum_s) if wnum_s.isdigit() else -1
        if wnum <= 2:
            r[COL_REMARK] = "makeup lot-16 & 18"
        else:
            r[COL_REMARK] = "makeup lot-16 & 18"  # same — unified label

    updated_rows.append(r)

# ── A: Inject Lot 4 primary (K8A236 / Q603S6V) after Lot 3 block ─────────
# Find insertion point: after last row of Lot 3 (K8A235) / MK lot (K8A387) block
# Strategy: insert before the first Lot 5 row (K8A210)
lot4_rows = []
for wn in range(1, 11):   # 10 wafers (started 12; 2 scrapped → 10 remain)
    tsmc_wid  = f"K8A236.{wn:02d}"
    intel_wid = f"Q603S6V-{wn:02d}"
    scrap_flag = "X" if wn in (11, 12) else ""   # placeholder — exact scrapped wfrs TBD
    lot4_rows.append([
        "Lot 4",                    # Lot#
        "NVL816-BLLC-L0 AIO",      # Material Type, Skew...
        "AIO",                      # Material Type
        "L0",                       # Stepping
        "K8A236",                   # TSMC_LOT
        "Q603S6V",                  # INTEL_LOT7
        str(wn),                    # WaferID
        tsmc_wid,                   # TSMC WaferID
        intel_wid,                  # Intel WaferID
        "AIO",                      # AIO/BB
        "POR",                      # MG4 split
        "POR",                      # Device Skew
        "",                         # Vy CD+
        "Lot 4 - SCRAPPED at TSMC", # Remark
        scrap_flag,                 # inline scrap
    ])

# ── Rebuild row list with Lot 4 insertion only ────────────────────────────
# (K9H927 makeup lot-7 is updated in-place above; no new row needed)
final_rows = []
lot4_inserted = False

for r in updated_rows:
    tsmc = r[COL_TSMC].strip()

    # Insert Lot 4 block before first Lot 5 row (K8A210)
    if not lot4_inserted and tsmc == "K8A210":
        final_rows.extend(lot4_rows)
        lot4_inserted = True

    final_rows.append(r)

if not lot4_inserted:
    final_rows.extend(lot4_rows)

# ── Write staged file ─────────────────────────────────────────────────────
with open(STAGED, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(header)
    writer.writerows(final_rows)

print(f"Staged file created: {os.path.basename(STAGED)}")
print(f"  Original rows : {len(rows)}")
print(f"  Staged rows   : {len(final_rows)}")
print(f"  Delta         : +{len(final_rows) - len(rows)} rows")
print()

# ── Summary of changes ────────────────────────────────────────────────────
print("Changes applied:")
print("  A. Added Lot 4 (K8A236 / Q603S6V) — 10 wafers — SCRAPPED")
print("  B. Filled K9H927 Intel lot IDs:")
print("       makeup lot-4  (wafers 12,13) → Q552S9P0Z2")
print("       makeup lot-10 (wafers 7-11)  → Q552S9P0Z1")
print("       makeup lot-7  (existing row) → Q552S9P0Z0 / K9H927.0J (in-place update)")
print("  C. Filled K9H922 Intel lot ID     → Q552S9R0Z0")
print("  D. Updated K8A359 remark (wfr 1-2) → 'makeup lot-16 & 18'")
print()
print("Still needs manual review / confirmation before replacing original.")
