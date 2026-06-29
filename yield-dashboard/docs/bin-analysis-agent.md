# Recovery & Hard-Fail Bin Analysis — NVL816 AQUA Sort Data

## Overview

This guide covers how to analyze **any AQUA interface bin** using the AP/CR LOGTRACKER columns in the NVL816 sort CSV (`NCXSDJXL0H61`). The goal is to identify which test instances are driving a given bin outcome, which AP/CR groups are affected, and how the failure distribution compares across lots and wafers.

The same DEFLATE32 LOGTRACKER decode method applies to recovery bins, hard-fail bins, or any other iBin that has LOGTRACKER data. Key differences between recovery and hard-fail analysis are noted in §3.4.

---

## 1. Data Source

### File location

| Path | Description |
|------|-------------|
| `C:\work\yield\data\61C\NCXSDJXL0H61_<date>.csv` | Daily AQUA extract (delivered by automation) |
| `C:\temp\aqua_tmp\NCXSDJXL0H61_<date>.csv` | Alternate working copy |

The file is a flat CSV with one row per die. It contains 197 columns including bin results, LOGTRACKER strings, and COREBINRESULT fields for op `119325`.

### Key identifying columns

| Column | Description |
|--------|-------------|
| `SORT_LOT` | Lot ID (e.g. `Q603SF9`) |
| `SORT_WAFER` | Wafer number (integer, e.g. `206`) |
| `SORT_X`, `SORT_Y` | Die coordinates |
| `INTERFACE_BIN_119325` | Final interface (2-digit) bin |
| `FUNCTIONAL_BIN_119325` | Final functional bin (3xx for recovery) |
| `DATA_BIN_119325` | Data bin (encodes recovery sub-type) |
| `Bin Description_119325` | Human-readable bin label (not always populated for recovery bins) |

---

## 2. Interface Bin Definitions (iBin)

| iBin | Name | Description |
|------|------|-------------|
| 1 | Full Function (FF) | All cores + APs active, all tests pass |
| 2 | Defeatured Function (DF) | Some cores/APs disabled by design, still passes |
| **3** | **ATOM Recovery** | One or more AP pairs fused out due to failure |
| **4** | **Core Recovery** | One or more core pairs fused out due to failure |
| 8 | DC Fail | DC parametric failure |
| 19 | Reset Fail | Reset/power-on failure |
| 26 | HVQK Stress | Fails HVQK stress screen |
| **41** | **ATOM Hard Fail — Functional** | ATOM functional hard fail (fBin 4134); scan trackers may be empty |
| **42** | **ATOM Hard Fail — Scan Stuckat** | Unrecoverable scan stuckat; too many APs affected for recovery |

Recovery yield target for NVL816: iBin 1+2 ≥ 77.9% (iBin 1+2+3+4 counts toward total sort yield).

---

## 3. Recovery-Specific Columns

### Fuse-out selection (which AP/CR groups were disabled)

| Column | Description |
|--------|-------------|
| `TPI_BIN::CTRL_UB_X_K_FINAL_X_X_X_X_COREBINRESULT_ATOMSELECT_119325` | Bitmask: which AP pairs were disabled (ATOM recovery or hard-fail dies) |
| `TPI_BIN::CTRL_UB_X_K_FINAL_X_X_X_X_COREBINRESULT_CORESELECT_119325` | Bitmask: which core pairs were disabled (Core recovery or hard-fail dies) |

**Bit encoding (0-indexed):**
- Bit 0 = AP0 / CR0 (value 1)
- Bit 1 = AP1 / CR1 (value 2)
- Bit 2 = AP2 / CR2 (value 4)
- Bit 3 = AP3 / CR3 (value 8)

Example: `ATOMSELECT = 12` = `0b1100` = AP2 + AP3 both disabled.
Example: `CORESELECT = 6` = `0b0110` = CR1 + CR2 both disabled.

> **Note:** AP/CR pairs are disabled as units. A failure in one member of a pair causes both to be fused out.

### Per-AP/CR LOGTRACKER strings

| Column pattern | Description |
|----------------|-------------|
| `...LOGTRACKER_AP0_119325` through `AP3` | Per-AP test failure tracker (ATOM bin analysis) |
| `...LOGTRACKER_CR0_119325` through `CR3` | Per-CR test failure tracker (Core bin analysis) |
| `...LOGTRACKER_TRACKER_ATOM_BIN_119325` | Compact integer encoding of which APs had failures |
| `...LOGTRACKER_TRACKER_CORE_BIN_119325` | Compact integer encoding of which CRs had failures |

The `AP`/`CR` LOGTRACKER columns contain **DEFLATE32-encoded** strings. Each decoded string contains a newline-delimited list of test events in the form:

```
APx|x|0|TPI_RECOVERY::CTRL_SCREEN_X_K_START_X_X_X_X_TRACKERCLEAR
APx|0|1|SCN_ATOM::STUCKAT_ATOM_SB_K_BEGIN_N_VATOM_NOM_LFM_OCC
```

**Token field meanings:** `AP_ID | step_index | flag | test_instance_name`

| Field | Value | Meaning |
|-------|-------|---------|
| `AP_ID` | `AP0`–`AP3` or `CR0`–`CR3` | Which AP/CR group this entry belongs to |
| `step_index` | `x` (TRACKERCLEAR) or `0` (detection) | `x` = tracker reset; `0` = first detection entry |
| `flag` | `0` (TRACKERCLEAR) or `1` (detection) | `1` = this test detected a non-zero condition |
| `test_instance_name` | PRIME test instance path | The test that was active when the condition was first detected |

**Important:** The test listed in the LOGTRACKER entry is the **first failing test** — the test that triggers the rm1/rm2 exit in PRIME and causes the recovery bin to be assigned.

- For **stuckat scan tests** (`STUCKAT_ATOM_SB_K_BEGIN`, `STUCKAT_ATOM_SB_K_END`): the test **formally fails** in PRIME (`PassFail=Fail`, rm1=-1 or rm2=-2 exit codes). The recovery SetBin (e.g. `b98421253`) is assigned at the rm1/rm2 exit point of this test itself.
- For **Vmin detection tests** (`ATSPEED_VMIN`, `LSA_VMIN`, `SBFT_VMIN` ending in `_OCC` or `_PMOVI`): the test completes its OCC/PMOVI pass normally, but sets the detection flag. The downstream `CTRL_SCREEN` test evaluates the flag and assigns the recovery bin. The LOGTRACKER records the detection test, not the downstream CTRL.

Empirically (from 478 entries across recovery dies in this dataset):
- `step_index` is **always `0`** — only the first detection is ever logged
- `flag` is **always `1`** — the LOGTRACKER never records a second test entry per AP
- There are **never multiple failure lines** per AP per die — the LOGTRACKER records one detection point and stops

This means the pareto analysis identifies the **first detection point per AP** (the earliest point in the recovery flow where a defect was seen), not the test that formally assigns the bin.

### Detection checkpoint classification

The most common detection checkpoints observed in this dataset and what each indicates:

| Detection checkpoint (LOGTRACKER) | Defect category | How it detects | Formally fails in PRIME? |
|-----------------------------------|-----------------|----------------|--------------------------|
| `SCN_ATOM::STUCKAT_ATOM_SB_K_BEGIN_..._OCC` | Structural stuckat | Scan OCC pattern: counts stuck-at cells at begin-of-flow voltage | **Yes** — rm1/rm2 exit; `PassFail=Fail` in PRIME |
| `SCN_ATOM::STUCKAT_ATOM_SB_K_END_..._OCC` | Structural stuckat (end-of-flow screen) | Same as K_BEGIN but run at end keypoint, catches stragglers | **Yes** — rm1/rm2 exit |
| `SCN_ATOM::ATSPEED_ATOM_VMIN_K_PREHVQK_..._OCC` | Scan at-speed Vmin margin | At-speed OCC scan at nominal Vcc, pre-HVQK | **No** — OCC pattern |
| `ARR_ATOM::LSA_ATOM_VMIN_K_PREHVQK_...PMOVI` | Array Vmin margin (pre-stress) | PMOVI array Vmin sweep at 1200mV ceiling, pre-HVQK | **No** — detection step logged before CTRL evaluates |
| `ARR_ATOM::SSA/XSA_ATOM_VMIN_K_..._PMOVI` | Array Vmin (SSA/XSA bitcell) | PMOVI sweep, same principle | **No** |
| `FUN_ATOM::SBFT_ATOM_VMIN_K_END_...` | Functional Vmin margin (end-of-flow) | SBFT functional Vmin sweep at end keypoint | **No** |
| `SCN_CORE::ATSPEED_CORE_VMIN_K_PREHVQK_..._OCC` | Core scan at-speed Vmin (pre-stress) | At-speed OCC scan pre-HVQK on core mesh | **No** |
| `ARR_CORE::LSA/SSA_CORE_VMIN_K_PREHVQK_...PMOVI` | Core array Vmin (pre-stress) | PMOVI sweep on core array cells pre-HVQK | **No** |
| `FUN_CORE::SBFT_CORE_VMIN_K_END_...` | Core functional Vmin (end-of-flow) | SBFT functional Vmin sweep on core at end keypoint | **No** |

**Rule:** `STUCKAT_ATOM_SB_K_BEGIN` and `STUCKAT_ATOM_SB_K_END` end in `_OCC` but **formally fail** in PRIME (rm1/rm2 exit). All other `_OCC` tests (ATSPEED_VMIN, etc.) and `_PMOVI` sweep tests complete normally — for those, the recovery bin is assigned by a downstream `CTRL_SCREEN` test that reads the detection flag.

### 3.4 — LOGTRACKER coverage caveats (hard-fail bins)

Not all dies in hard-fail bins have LOGTRACKER entries. The AP/CR trackers only record tests in the **recovery screening flow**. A die can reach a hard-fail bin via a completely different path:

| Scenario | ATOMSELECT | LOGTRACKER state | Interpretation |
|----------|-----------|-----------------|----------------|
| Recovery attempted, still failed hard | 4, 8, or 12 | Populated — one or more APs have entries | Too many APs affected; even after fusing out a pair the die still failed |
| Standard config, stuckat detected | 16 | May be populated | Failed via scan path but wasn't eligible for or didn't reach recovery |
| Standard config, non-scan fail | 16 | **Empty / TRACKERCLEAR only** | Failed via functional/DC path; AP scan tracker was never written |
| Unknown / APSELECT=0 | 0 | Unclear | Rare; fuse state not finalized or test-program edge case |

**Practical rules:**
- `ATOMSELECT = 16` (`0b10000`) in a hard-fail bin = AP4 disabled by design only → no recovery fusing. These dies are most likely to have **empty trackers**.
- `ATOMSELECT ≠ 16` in a hard-fail bin = recovery was attempted but the die still failed (overflow). These dies **will** have tracker entries.
- `first_failing_test()` returns `None` for any AP whose tracker decodes to only `TRACKERCLEAR` tokens. Dies where all 4 APs return `None` are **silently excluded** from the pareto table — they are not a bug, just non-scan fails.
- For hard-fail bins, the `Count` column shows **per-die** counts (not per-AP-tracker). A single die can appear in multiple rows if different APs have different first failing tests — so **the column totals may sum to more than 100%**.

---

## 4. Decoding DEFLATE32 Strings

The LOGTRACKER values are stored as `DEFLATE32_<base32-encoded-zlib-data>` strings. They can be decoded without any Intel-internal dependencies using only Python's `zlib`:

```python
import zlib

_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_C2I = {c: i for i, c in enumerate(_CHARS)}

def deflate32_decode(s):
    if not isinstance(s, str) or not s.startswith('DEFLATE32_'):
        return str(s)
    enc = s[10:].strip('=')
    bits = ''.join(bin(_C2I[c])[2:].zfill(5) for c in enc)
    bits += '0' * (8 - len(bits) % 8)
    raw = bytes(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))
    return zlib.decompress(raw, -8).decode('utf-8')
```

To extract the first detection test from a decoded string:

```python
def first_failing_test(decoded_str):
    """Return the first non-TRACKERCLEAR test instance logged for this AP/CR.
    For stuckat K_BEGIN/K_END: this IS the formally failing test (rm1/rm2 exit, PassFail=Fail).
    For Vmin OCC/PMOVI detection tests: this is the detection checkpoint; the downstream
    CTRL_SCREEN test assigns the bin but is not recorded in the LOGTRACKER.
    """
    for token in decoded_str.split('|'):
        token = token.strip()
        if token and '::' in token and 'TRACKERCLEAR' not in token:
            return token
    return None
```

---

## 5. Step-by-Step Analysis

### Step 1 — Load the CSV

```python
import pandas as pd

CSV = r'C:\work\yield\data\61C\NCXSDJXL0H61_20260520_060002.csv'

# Minimal columns for recovery analysis
base_cols = ['SORT_LOT', 'SORT_WAFER', 'SORT_X', 'SORT_Y', 'INTERFACE_BIN_119325']
cr_cols  = [f'TPI_BIN::CTRL_UB_X_K_FINAL_X_X_X_X_LOGTRACKER_CR{i}_119325' for i in range(4)]
ap_cols  = [f'TPI_BIN::CTRL_UB_X_K_FINAL_X_X_X_X_LOGTRACKER_AP{i}_119325' for i in range(4)]
sel_cols = [
    'TPI_BIN::CTRL_UB_X_K_FINAL_X_X_X_X_COREBINRESULT_CORESELECT_119325',
    'TPI_BIN::CTRL_UB_X_K_FINAL_X_X_X_X_COREBINRESULT_ATOMSELECT_119325',
]

df = pd.read_csv(CSV, low_memory=False, usecols=base_cols + cr_cols + ap_cols + sel_cols)
```

### Step 2 — Filter to lot / wafer and bin of interest

```python
LOT      = 'Q603SF9'
WAFER    = 206        # integer
IBIN_COL = 'INTERFACE_BIN_119325'

lot_wafer = df[(df['SORT_LOT'] == LOT) & (df['SORT_WAFER'] == WAFER)]

# Filter to any iBin of interest:
# - Use ap_cols for ATOM-side bins (scan/array failures in AP0-AP3 trackers)
# - Use cr_cols for Core-side bins (scan/array failures in CR0-CR3 trackers)
# - Pass hard_fail=False for recovery bins (per-AP-tracker denominator)
# - Pass hard_fail=True  for hard-fail bins (per-die denominator; % may sum >100%)
sub = lot_wafer[lot_wafer[IBIN_COL] == IBIN]   # replace IBIN with the integer bin of interest
```

For hard-fail bins pass `hard_fail=True` to `recovery_table()` (see §5 code). This switches the denominator from per-AP-tracker to per-die so `%` stays ≤ 100% for rows where only one AP fires per die. Rows may still exceed 100% if a single die has different first-failing tests on different APs — this is expected.

### Step 3 — ATOMSELECT / CORESELECT distribution

```python
# For any ATOM-side bin:
print(sub['TPI_BIN::...ATOMSELECT_119325'].value_counts())

# For any Core-side bin:
print(sub['TPI_BIN::...CORESELECT_119325'].value_counts())
```

Interpret results using the bitmask table in §3. For hard-fail bins, ATOMSELECT=16 (only AP4 disabled by design) indicates no recovery was attempted — these dies likely have empty LOGTRACKER entries.

### Step 4 — First-failing-test pareto (per AP or CR)

```python
from collections import defaultdict

def recovery_pareto(df_recovery, tracker_cols):
    """Build a pareto of first failing test across all tracker columns."""
    test_group_counts = defaultdict(lambda: defaultdict(int))
    total = len(df_recovery)

    for _, row in df_recovery.iterrows():
        for col in tracker_cols:
            decoded = deflate32_decode(row[col])
            fail = first_failing_test(decoded)
            if fail:
                group = col.split('LOGTRACKER_')[1].split('_119325')[0]  # e.g. 'AP2' or 'CR3'
                test_group_counts[fail][group] += 1

    test_totals = {t: sum(v.values()) for t, v in test_group_counts.items()}
    for test, tot in sorted(test_totals.items(), key=lambda x: -x[1]):
        groups = '  '.join(f'{g}={test_group_counts[test].get(g, 0)}'
                           for g in sorted(test_group_counts[test].keys()))
        print(f'  {tot:3d} ({100*tot/total:.1f}%)  {groups}  {test}')
```

Call it for a bin subset, choosing tracker columns to match the failure side:

```python
# ATOM-side bin (ap_cols = AP0–AP3 trackers)
print(f'=== iBin {IBIN} ATOM ({len(sub)} dies) ===')
recovery_pareto(sub, ap_cols)

# Core-side bin (cr_cols = CR0–CR3 trackers)
print(f'=== iBin {IBIN} Core ({len(sub)} dies) ===')
recovery_pareto(sub, cr_cols)
```

---

## 6. Test Name Conventions

LOGTRACKER test instance names follow this pattern:

```
<FLOW>::<TEST_INSTANCE_K_KEYPOINT_N_NET_RAIL_CORNER_FREQ_SUFFIX>
```

| Prefix | Flow |
|--------|------|
| `SCN_ATOM::` | Scan tests on Atom cores |
| `SCN_CORE::` | Scan tests on Performance (P) cores |
| `ARR_ATOM::` | Array (memory/cache) tests on Atom |
| `ARR_CORE::` | Array tests on P-cores |
| `FUN_ATOM::` | Functional tests on Atom |
| `FUN_CORE::` | Functional tests on P-cores |
| `TPI_RECOVERY::` | Recovery flow control (tracker clear, etc.) |

### Common test suffixes

| Suffix fragment | Meaning |
|-----------------|---------|
| `K_BEGIN` / `K_END` | Keypoint: beginning / end of recovery flow |
| `K_PREHVQK` | Keypoint: pre-HVQK stress screen |
| `N_VATOM_NOM` / `N_VCCIA_NOM` | Net: Atom / IA supply rail at nominal voltage |
| `LFM` | Low Frequency Mode |
| `OCC` | Occurrence-based (one-pass scan) |
| `PMOVI` | PMOVI (array Vmin test mode) |
| `MLCLS` | MLC lockstep functional test |
| `ALLCORE` | All-core configuration |
| `STUCKAT_SB` | Stuckat scan-based |
| `ATSPEED_VMIN` | At-speed Vmin sweep |
| `LSA_VMIN` | LSA (array) Vmin sweep |
| `SBFT_VMIN` | SBFT functional Vmin sweep |

---

## 7. Interpreting Results — Example Reference Data (Q603SF9 W206)

> These tables are specific to lot **Q603SF9**, wafer **206** from the `NCXSDJXL0H61_20260520_060002.csv` extract. They illustrate the output format for four bins. Run `_q603sf9_bins.py` with any iBin to generate equivalent tables for other bins or wafers.

### iBin 3 — ATOM Recovery (64 dies, ATOMSELECT=12 → AP2+AP3 disabled)

| Count | % | AP breakdown | First failing test (PRIME rm1/rm2 exit) |
|-------|---|--------------|----------------------------------------|
| 47 | 73.4% | AP3=30, AP0=9, AP2=5, AP1=3 | `SCN_ATOM::STUCKAT_ATOM_SB_K_BEGIN_N_VATOM_NOM_LFM_OCC` |
| 10 | 15.6% | AP2=4, AP0=3, AP1=2, AP3=1 | `ARR_ATOM::LSA_ATOM_VMIN_K_PREHVQK_NITO_VATOM_VNOM_LFM_1200_PMOVI` |
| 2 | 3.1% | AP2=1, AP3=1 | `SCN_ATOM::ATSPEED_ATOM_VMIN_K_PREHVQK_N_VATOM_NOM_LFM_OCC` |
| 2 | 3.1% | AP2=1, AP3=1 | `FUN_ATOM::SBFT_ATOM_VMIN_K_END_X_VATOM_X_X_F1_ATOM_L2_LOCKSTEP` |
| 2 | 3.1% | AP2=1, AP0=1 | `ARR_ATOM::ALL_ATOM_SB_K_END_NITO_VATOM_NOM_LFM` |
| 1 | 1.6% | AP3=1 | `SCN_ATOM::STUCKAT_ATOM_SB_K_END_N_VATOM_NOM_LFM_OCC` |

**Interpretation:** 73% structural scan stuckat (defect-driven, not marginal Vmin). AP2 is fused out as the collateral pair partner of AP3 — for many dies only AP3 has an actual failure, AP2's tracker is clear.

### iBin 4 — Core Recovery (30 dies, CORESELECT=6 → CR1+CR2 disabled)

| Count | % | CR breakdown | First failing test (PRIME rm1/rm2 exit) |
|-------|---|--------------|----------------------------------------|
| 14 | 46.7% | CR0=4, CR3=4, CR1=3, CR2=3 | `SCN_CORE::ATSPEED_CORE_VMIN_K_PREHVQK_N_VCCIA_NOM_LFM_ALLCORE` |
| 7 | 23.3% | CR0=3, CR3=2, CR1=1, CR2=1 | `ARR_CORE::LSA_CORE_VMIN_K_PREHVQK_HPTP_VCCC_NOM_LFM_F1_PMOVI` |
| 4 | 13.3% | CR2=3, CR1=1 | `FUN_CORE::SBFT_CORE_VMIN_K_END_X_CR_NOM_LFM_CORE_MLCLS` |
| 2 | 6.7% | CR3=2 | `FUN_CORE::SBFT_CORE_VMIN_K_END_X_CR_NOM_LFM_CORE_MLC` |
| 1 | 3.3% | CR1=1 | `ARR_CORE::SSA_CORE_VMIN_K_PREHVQK_HPTP_VCCC_NOM_LFM_F1_PMOVI` |
| 1 | 3.3% | CR3=1 | `ARR_CORE::LSA_CORE_VMIN_K_PREHVQK_HPTP_VCCC_NOM_LFM_F1_INTM` |
| 1 | 3.3% | CR3=1 | `ARR_CORE::LSA_CORE_SCBD_K_END_HPTP_VCCC_NOM_LFM_F1` |

**Interpretation:** 100% Vmin-driven (no structural scan stuckat). The scan at-speed Vmin pre-HVQK test (`ATSPEED_CORE_VMIN_K_PREHVQK`) is the same test family as fbin 2647, but these dies passed recovery because their Vmin was marginal before HVQK stress — not after.

### Key contrast

| iBin | Driver | Implication |
|------|--------|-------------|
| 3 (ATOM) | 73% scan stuckat | Structural/localized defect — limited improvement from process margin |
| 4 (Core) | 100% Vmin tail | Process margin issue — improvements to Vcc or process can recover |

### iBin 41 — ATOM Hard Fail (12 dies, fBin 4134)

> **Coverage disclaimer:** 5 of 12 dies have ATOMSELECT=16 (standard config, no recovery attempted). These dies have empty LOGTRACKER entries — they likely failed via a functional path, not via the AP scan tracker. Only 7 dies appear in the table below.

| Count | % of 12 | AP breakdown | First failing test |
|------:|--------:|---|---|
| 6 | 50.0% | AP1=3, AP2=3, AP3=3, AP0=1 | `SCN_ATOM::STUCKAT_ATOM_SB_K_BEGIN_N_VATOM_NOM_LFM_OCC` |
| 1 | 8.3% | AP3=1 | `SCN_ATOM::ATSPEED_ATOM_VMIN_K_PREHVQK_N_VATOM_NOM_LFM_OCC` |

**Note:** ATOMSELECT distribution: 16→6 dies (50%, standard), 12→3 dies (25%, AP2+AP3 attempted), 4→2 dies (17%, AP2 attempted), 8→1 die (8%, AP3 attempted). The 3 dies with ATOMSELECT≠16 are recovery-overflow: recovery was attempted but the die still failed.

### iBin 42 — ATOM Hard Fail (37 dies, fBins 4216/4220/4229/4234)

> **Coverage disclaimer:** 5 of 37 dies have ATOMSELECT=16 (standard config). These may have empty trackers. The `%` values below may sum to more than 100% — a die can appear in multiple rows if different APs first fail at different tests.

| Count | % of 37 | AP breakdown | First failing test |
|------:|--------:|---|---|
| 31 | 83.8% | AP3=25, AP1=8, AP0=7, AP2=6 | `SCN_ATOM::STUCKAT_ATOM_SB_K_BEGIN_N_VATOM_NOM_LFM_OCC` |
| 8 | 21.6% | AP1=8 | `SCN_ATOM::STUCKAT_ATOM_SB_K_END_N_VATOM_NOM_LFM_OCC` |
| 2 | 5.4% | AP2=2 | `ARR_ATOM::LSA_ATOM_VMIN_K_PREHVQK_NITO_VATOM_VNOM_LFM_1200_PMOVI` |

**ATOMSELECT distribution:** 12→15 (40.5%, AP2+AP3 attempted), 8→12 (32.4%, AP3 attempted), 16→5 (13.5%, standard), 4→3 (8.1%, AP2 attempted), 0→2 (5.4%).

**Key insight:** Same `STUCKAT_K_BEGIN` test as iBin 3, but these dies couldn't be recovered because stuckat spread across ≥3 APs. AP3 dominates (25/31 stuckat-begin dies) — consistent with iBin 3 where AP3 is also the primary failing unit. The iBin 42 overflow dies are the tail of the same defect population that iBin 3 captures.

---

## 8. Functional Sub-Bin Deep Dive (fBin 101, 198, 201, 202, 301, 303)

These six functional bins account for the large majority of all dies in the NVL816 AQUA data. Understanding what each one means and what differentiates the sub-bins is critical for yield analysis.

### Summary across all lots (as of 20260520)

| fBin | iBin | Total dies | ATOMSELECT | CORESELECT | Category |
|------|------|-----------|------------|------------|----------|
| 101 | 1 | 2861 | 16 = `0b10000` | 8 = `0b01000` | Full Function — primary FF SKU |
| 198 | 1 |  279 | 16 = `0b10000` | 8 = `0b01000` | Full Function — alternate FF mark |
| 201 | 2 |  550 | 16 = `0b10000` | 8 = `0b01000` | Defeatured Function — primary DF SKU |
| 202 | 2 |   13 | 16 = `0b10000` | 8 = `0b01000` | Defeatured Function — rare DF variant |
| 301 | 3 |  349 | 12 = `0b01100` | 8 = `0b01000` | ATOM Recovery — AP2+AP3 fused out |
| 303 | 3 |   60 | 12 = `0b01100` | 8 = `0b01000` | ATOM Recovery — AP2+AP3 fused out |

### fBin 101 vs 198 — both iBin 1 (Full Function)

- **Identical fuse config**: ATOMSELECT=16 (`0b10000`) and CORESELECT=8 (`0b01000`) for both.
  - ATOMSELECT bit 4 (value 16) = AP4 disabled by design (always the case for all non-recovery dies)
  - CORESELECT bit 3 (value 8) = CR3 disabled by design (always the case)
- The difference between 101 and 198 is **not** in the fuse settings — it is a product SKU / bin assignment distinction made by the test program (e.g., speed sort result, thermal mark, or specific functional capability variant). Both bins fully pass all recovery screens.

### fBin 201 vs 202 — both iBin 2 (Defeatured Function)

- Same ATOMSELECT=16, CORESELECT=8 as the iBin 1 bins above.
- 201 is the primary DF product. 202 is a rare alternate DF mark (13 dies in this dataset).
- Neither has any failed test causing recovery — these dies are defeatured **by product design**, not due to defect.

> **Key insight:** For fBins 101/198/201/202, there are no test failures driving the bin. The sub-bin number encodes the product configuration or speed mark. To determine what differentiates 101 from 198 (or 201 from 202), examine the test program's bin assignment logic or the `DATA_TOTAL_BIN_119325` / `FUNCTIONAL_TOTAL_BIN_119325` columns.

### fBin 301 vs 303 — both iBin 3 (ATOM Recovery, ATOMSELECT=12)

Both sub-bins have identical fuse settings:
- ATOMSELECT = 12 = `0b01100` → AP2 (bit 2) + AP3 (bit 3) fused out
- CORESELECT = 8 = `0b01000` → CR3 disabled by design

The sub-bin difference (301 vs 303) encodes a different **recovered product configuration** (e.g., 301 = 2-AP recovery config type A, 303 = 2-AP recovery config type B). The underlying failure that triggered recovery is the same test population for both.

#### fBin 301 root cause (349 dies, all lots)

| Count | % | AP breakdown | First failing test (PRIME rm1/rm2 exit) |
|-------|---|--------------|----------------------------------------|
| 275 | 79% | AP3=179, AP2=39, AP1=37, AP0=20 | `SCN_ATOM::STUCKAT_ATOM_SB_K_BEGIN_N_VATOM_NOM_LFM_OCC` |
| 38 | 11% | AP2=20, AP3=7, AP1=6, AP0=5 | `ARR_ATOM::LSA_ATOM_VMIN_K_PREHVQK_NITO_VATOM_VNOM_LFM_1200_PMOVI` |
| 8 | 2% | AP2=6, AP1=1, AP3=1 | `SCN_ATOM::STUCKAT_ATOM_SB_K_END_N_VATOM_NOM_LFM_OCC` |
| 6 | 2% | AP2=3, AP0=2, AP1=1 | `SCN_ATOM::ATSPEED_ATOM_VMIN_K_PREHVQK_N_VATOM_NOM_LFM_OCC` |
| 6 | 2% | AP3=2, AP2=3, AP0=2, AP1=1 | `ARR_ATOM::ALL_ATOM_SB_K_END_NITO_VATOM_NOM_LFM` |
| 4 | 1% | AP0=4 | `ARR_ATOM::XSA_ATOM_VMIN_K_END_NITO_VATOM_NOM_LFM` |
| 4 | 1% | AP2=3, AP1=1 | `ARR_ATOM::SSA_ATOM_VMIN_K_PREHVQK_NITO_VATOM_VNOM_LFM_1200_PMOVI` |
| 3 | 1% | AP2=2, AP3=1 | `FUN_ATOM::SBFT_ATOM_VMIN_K_END_X_VATOM_X_X_F1_ATOM_L2_LOCKSTEP` |

**79% scan stuckat at begin**, dominated by AP3 (179/349 = 51% of all fBin 301 dies).

#### fBin 303 root cause (60 dies, all lots)

| Count | % | AP breakdown | First failing test (PRIME rm1/rm2 exit) |
|-------|---|--------------|----------------------------------------|
| 41 | 68% | AP3=28, AP2=6, AP0=5, AP1=2 | `SCN_ATOM::STUCKAT_ATOM_SB_K_BEGIN_N_VATOM_NOM_LFM_OCC` |
| 13 | 22% | AP0=4, AP1=3, AP2=3, AP3=3 | `ARR_ATOM::LSA_ATOM_VMIN_K_PREHVQK_NITO_VATOM_VNOM_LFM_1200_PMOVI` |
| 2 | 3% | AP2=2 | `SCN_ATOM::STUCKAT_ATOM_SB_K_END_N_VATOM_NOM_LFM_OCC` |
| 2 | 3% | AP0=2 | `ARR_ATOM::SSA_ATOM_VMIN_K_PREHVQK_NITO_VATOM_VNOM_LFM_1200_PMOVI` |
| 1 | 2% | AP3=1 | `ARR_ATOM::ROM_ATOM_VMIN_K_PREHVQK_NITO_VATOM_VNOM_LFM_1200` |

**Same driver as 301** — 68% scan stuckat, 22% LSA Vmin. fBin 303 tends to have slightly higher Vmin contribution vs 301.

### Combined summary — what is driving ATOM recovery bins

Across fBin 301+303 combined (409 dies, all lots):

| Cause category | Count | % |
|----------------|-------|---|
| **Scan stuckat at begin** (`SCN_ATOM::STUCKAT_ATOM_SB_K_BEGIN`) | ~316 | **77%** |
| **ARR LSA Vmin pre-HVQK** (`ARR_ATOM::LSA_ATOM_VMIN_K_PREHVQK`) | ~51 | **12%** |
| ARR SSA/XSA Vmin | ~10 | 2% |
| Scan stuckat at end | ~10 | 2% |
| Scan atspeed Vmin pre-HVQK | ~7 | 2% |
| Other (ARR end, SBFT Vmin, ROM Vmin) | ~15 | 4% |

**AP3 is the most commonly failing AP** across all ATOM recovery — AP3 accounts for ≈ 51% of all scan stuckat failures and ~18% of all Vmin failures. AP3 failures cause AP2 to be fused out as the pair partner.

---

## 9. Lot-Level Aggregation

To roll up across all wafers in a lot:

```python
lot_df = df[df['SORT_LOT'] == LOT].copy()
total  = len(lot_df)

for ibin, label in [(3, 'ATOM Recovery'), (4, 'Core Recovery')]:
    sub = lot_df[lot_df['INTERFACE_BIN_119325'] == ibin]
    print(f'iBin {ibin} ({label}): {len(sub)} dies ({100*len(sub)/total:.1f}%)')
    tracker_cols = ap_cols if ibin == 3 else cr_cols
    recovery_pareto(sub, tracker_cols)
    print()
```

---

## 9. Notes and Caveats

- **LOGTRACKER records the FIRST catching test** per AP/CR — if multiple tests fail within the same AP group, only the earliest one is captured.
- **Collateral fuse-out**: When ATOMSELECT or CORESELECT shows a pair disabled but only one member has a LOGTRACKER failure entry, the other member was fused as a pair partner, not due to an independent failure.
- **FUNCTIONAL_BIN for recovery dies**: The `FUNCTIONAL_BIN_119325` column for iBin 3/4 dies contains the recovery product bin (e.g. 301, 302, 303, 304) — not the underlying test failure. Use the `LOGTRACKER_AP*/CR*` columns for root cause.
- **DEFLATE32 decode is safe to inline** — the decode logic uses only Python stdlib (`zlib`). No Intel-internal packages required.
- **op 119325 column suffix**: All bin/tracker columns are suffixed with `_119325` (the sort operation number). This may change with new test program revisions.
