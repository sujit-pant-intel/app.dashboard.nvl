# CLASS Dashboard

> **Status: Under Development** — excluded from deploy until further notice.

Generates an interactive HTML dashboard for NVL816 **package / class-test** analysis.  
Input is an AQUA class CSV (plain `.csv`, gzip-compressed `.csv.gz` / `.gz`, or `.zip` containing a single CSV). Output is a set of linked HTML files covering bin distribution, wafer patterns, and a full-featured interactive CLASS analysis page.

---

## Table of Contents

1. [Overview](#overview)
2. [Usage](#usage)
3. [Input Data](#input-data)
4. [Column Reference](#column-reference)
5. [Pipeline Steps](#pipeline-steps)
6. [Product Config](#product-config)
7. [Dashboard Layout](#dashboard-layout)
8. [Plot Outputs](#plot-outputs)
9. [CLASS Analysis HTML — Interactive Features](#class-analysis-html--interactive-features)
10. [Architecture](#architecture)

---

## Overview

Class / package testing is performed at **100 °C** on assembled packages.  
Sort / SDS testing is done at **20 °C** on singulated die.  
This dashboard correlates class measurements back to sort identifiers (lot, wafer, X/Y, DevRevStep) to enable die-level tracking through the supply chain.

Key data in every class CSV:

| Category | Description |
|----------|-------------|
| Die ID | `VISUAL_ID` — package visual identifier |
| Sort ID | `SORT_LOT_U1.U5`, `SORT_WAFER_U1.U5`, `SORT_X_U1.U5`, `SORT_Y_U1.U5` |
| Product | `DevRevStep_119325_U1.U5` (sort), `DevRevStep_6248_CLASSHOT` (class) |
| Sort SICC | SICC power measured at sort (20 °C, singulated die) |
| Class SICC | SICC power measured at package test (100 °C) |
| Sort UPM | UPM ring-oscillator values from sort |
| Vmin | PASSFLOW Vmin at each frequency for Core, Atom, CCF |

> The dashboard focuses on `U1.U5` (the NVL816 chiplet under test) for all sort-side columns.

---

## Usage

```
# GUI — opens the CLASS Dashboard window
python dashboard.py

# GUI with one pre-filled file (csv, gz, or zip)
python dashboard.py path\to\NVL_Class_forReport.csv

# GUI with multiple pre-filled files (paths joined by "; " in the input field)
python dashboard.py file1.csv file2.csv file3.csv

# Headless — single file
python dashboard.py file.csv --headless --out output\

# Headless — multiple files (concatenated before processing)
python dashboard.py file1.csv file2.csv --headless --out output\ --tag combined
```

**GUI steps:**

1. Launch `dashboard.py`
2. **Pipeline** tab → click **...** next to *Input files* to select one or more CSV / GZ / ZIP files  
   (the file dialog supports multi-select; selected paths are joined by `; ` in the field)
3. Set output folder and run tag, then click **Run Pipeline** — progress streams to the log panel
4. When complete, click **Open dashboard in browser** or use **Open Output**

**Multi-file notes:**

- Multiple input files are row-concatenated before processing (column schemas must match)
- Auto-tag and auto-output-path are derived from the **first** selected file
- In headless mode, pass additional paths as extra positional arguments before any `--` flags

---

## Input Data


Test data file: `NVL_Class_forReport_2cab5_1561405.CSV 2.csv` (6,196 rows, ~294 columns)

### Important columns in every class CSV

| Column | Description |
|--------|-------------|
| `VISUAL_ID` | Package visual identifier |
| `SORT_LOT_U1.U5` | Sort lot number (NVL816 chiplet) |
| `SORT_WAFER_U1.U5` | Sort wafer number |
| `SORT_X_U1.U5` | Sort die X coordinate |
| `SORT_Y_U1.U5` | Sort die Y coordinate |
| `DevRevStep_119325_U1.U5` | DevRevStep at sort (used for collateral lookups) |
| `DevRevStep_6248_CLASSHOT` | DevRevStep at class/package test |

---

## Column Reference

### Sort UPM

Ring-oscillator UPM values measured at sort — used as the X-axis for correlation plots.

```
UPM_0107_DPMH156P48ULVTINVD4_FULLDIE_0650_MED_119325_U1.U5   → short key: u107_650
UPM_0107_DPMH156P48ULVTINVD4_FULLDIE_0950_MED_119325_U1.U5   → short key: u107_950
UPM_0107_DPMH156P48ULVTINVD4_FULLDIE_1150_MED_119325_U1.U5   → short key: u107_1150
UPM_0107_DPMH156P48ULVTINVD4_FULLDIE_1200_MED_119325_U1.U5   → short key: u107_1200
UPM_0704_TPMH156P48ULVTNPPND6_FULLDIE_0950_MED_119325_U1.U5  → short key: u704_950
```

### SICC — Sort (20 °C, singulated die)

Measured at die level during sort. Rail abbreviations: `IA` = VCCCORE, `AT` = VCCATOM, `CCF` = VCCR.

```
PTH_POWER_CJ816P::...SICC_ALL_24A_V2_VCCATOM0|..._119325_U1.U5  → ss_a0
PTH_POWER_CJ816P::...SICC_ALL_24A_V2_VCCATOM1|..._119325_U1.U5  → ss_a1
PTH_POWER_CJ816P::...SICC_ALL_24A_V2_VCCATOM2|..._119325_U1.U5  → ss_a2
PTH_POWER_CJ816P::...SICC_ALL_24A_V2_VCCATOM3|..._119325_U1.U5  → ss_a3
PTH_POWER_CJ816P::...SICC_ALL_24A_V2_VCCCORE0|..._119325_U1.U5  → ss_c0
PTH_POWER_CJ816P::...SICC_ALL_24A_V2_VCCCORE1|..._119325_U1.U5  → ss_c1
PTH_POWER_CJ816P::...SICC_ALL_24A_V2_VCCCORE2|..._119325_U1.U5  → ss_c2
PTH_POWER_CJ816P::...SICC_ALL_24A_V2_VCCCORE3|..._119325_U1.U5  → ss_c3
PTH_POWER_CJ816P::...SICC_ALL_24A_V2_VCCR|..._119325_U1.U5      → ss_r
```

Derived: `ss_fc` = sum of all sort SICC rails (fullchip)

### SICC — Class / Package (100 °C)

Same rails measured at package test. Correlated against sort SICC to assess temperature delta and assembly shift.

```
VA-IN-NA-GSDS_D_S::PP_SICC_U1PU5_6248_CLASSHOT_IA00-V2_Value  → sc_c0  (VCCCORE0)
VA-IN-NA-GSDS_D_S::PP_SICC_U1PU5_6248_CLASSHOT_IA01-V2_Value  → sc_c1  (VCCCORE1)
VA-IN-NA-GSDS_D_S::PP_SICC_U1PU5_6248_CLASSHOT_IA02-V2_Value  → sc_c2  (VCCCORE2)
VA-IN-NA-GSDS_D_S::PP_SICC_U1PU5_6248_CLASSHOT_IA03-V2_Value  → sc_c3  (VCCCORE3)
VA-IN-NA-GSDS_D_S::PP_SICC_U1PU5_6248_CLASSHOT_AT00-V2_Value  → sc_a0  (VCCATOM0)
VA-IN-NA-GSDS_D_S::PP_SICC_U1PU5_6248_CLASSHOT_AT01-V2_Value  → sc_a1  (VCCATOM1)
VA-IN-NA-GSDS_D_S::PP_SICC_U1PU5_6248_CLASSHOT_AT02-V2_Value  → sc_a2  (VCCATOM2)
VA-IN-NA-GSDS_D_S::PP_SICC_U1PU5_6248_CLASSHOT_AT03-V2_Value  → sc_a3  (VCCATOM3)
VA-IN-NA-GSDS_D_S::PP_SICC_U1PU5_6248_CLASSHOT_CCF-V2_Value   → sc_r   (VCCR/Ring)
```

Derived: `sc_fc` = sum of all class SICC rails (fullchip)

### Vmin — PASSFLOW tokens

PASSFLOW Vmin columns give the minimum voltage at which each module passes at a given frequency.  
The normalizer auto-discovers these by searching for `UPSVFPASSFLOW_U1PU5_6248_CLASSHOT_<MODULE>_` in column names.

**NVL816 topology: 4 Core clusters, 4 Atom clusters, 1 CCF**

| Module | Search prefix | Index suffix | Short key pattern |
|--------|---------------|--------------|-------------------|
| Core | `...CLASSHOT_CR_<freq>_` | `_1` … `_4` | `vc_<freq>_<idx>` |
| Atom | `...CLASSHOT_AT_<freq>_` | `_1` … `_4` | `va_<freq>_<idx>` |
| CCF  | `...CLASSHOT_CCF_<freq>_` | `_1` only  | `vf_<freq>_1`     |

Example column names:
```
VA-IN-NA-GSDS_D_S::UPSVFPASSFLOW_U1PU5_6248_CLASSHOT_CR_4.900_1   → vc_4900_1
VA-IN-NA-GSDS_D_S::UPSVFPASSFLOW_U1PU5_6248_CLASSHOT_AT_3.800_2   → va_3800_2
VA-IN-NA-GSDS_D_S::UPSVFPASSFLOW_U1PU5_6248_CLASSHOT_CCF_4.400_1  → vf_4400_1
```

---

## Pipeline Steps

The dashboard runs a 6-step pipeline. Modules marked **(copy)** are exact, unmodified copies from the referenced dashboard.

| Step | Module | Source | Description |
|------|--------|--------|-------------|
| 1 | `add_material_type.py` | yield-dashboard (copy) | Merges material type, skew, BEOL skew via `LOT7`/`WAFER2` join |
| 2 | `apply_reticle_mapping.py` | yield-dashboard (copy) | Merges Layout, Device, Reticle coordinates via `SORT_X`/`SORT_Y` |
| 3 | `bin_distribution_html.py` | yield-dashboard (copy) | Generates `*BinDistribution.html` from `INTERFACE_BIN_*` column |
| 4 | `generate_heatmap_from_csv.py` | yield-dashboard (copy) | Wafer pattern heatmaps (`render_ibin_wafermap=False`) |
| 5 | `class_normalize.py` → `generate_pcm_html.py` | class-dashboard / etest-dashboard (copy) | Renames long AQUA columns to short keys, generates CLASS analysis HTML |
| 6 | master index | dashboard.py | `_Dashboard.html` linking all output files |

> **No IBIN wafer map** is generated (class data does not carry IBIN in the same format as yield).

---

## Product Config

Product config file: [`shared/setup/class-dashboard/NCXSDJ-CLASS-ProductConfig-L0.json`](../../shared/setup/class-dashboard/NCXSDJ-CLASS-ProductConfig-L0.json)

Key sections:

```jsonc
{
  "devrevstep_prefix": "NCXSDJ",   // used to find collateral files

  "sort_upm":   { "UPM 107_950": "<full col name>", ... },   // short key → original column
  "sort_upm_ref": { "UPM 107_950": 9154, ... },               // reference values for UPM %
  "sort_sicc":  { "SORT SICC CORE0": "<full col name>", ... },
  "class_sicc": { "CLASS SICC CORE0": "<full col name>", ... },

  "vmin_freq_search": {             // prefixes used to auto-discover Vmin columns
    "core": "UPSVFPASSFLOW_U1PU5_6248_CLASSHOT_CR_",
    "atom": "UPSVFPASSFLOW_U1PU5_6248_CLASSHOT_AT_",
    "ccf":  "UPSVFPASSFLOW_U1PU5_6248_CLASSHOT_CCF_"
  },

  // Bin Matrix / BinSplitAnalysis input columns
  "bin_matrix": {
    "DLCP": {
      "devRevStepPattern": "DevRevStep_*_CLASSHOT",  // CLASS-side DevRevStep column (wildcard)
      "dlcpExtractStart":  4,                        // substring start index in DevRevStep value
      "dlcpExtractLength": 2,                        // substring length
      "dlcpMap": { "2V": "UL", "4V": "UH", "5V": "UV" }  // raw key → decoded DLCP label
    },
    "passingQdfPattern": "VA-NA-UNIT-PASSING_QDFS_*_CLASSHOT",
    "wwPattern":         "LOTS End WW_*_CLASSHOT",
    "ProgramName": {
      "programNamePattern": "Program Name_*_CLASSHOT",
      "tpRevStart":  7,   // substring start within program name for TP Rev
      "tpRevLength": 8
    }
  },

  "groups": [                       // plot groups for generate_pcm_html (fnmatch patterns)
    { "name": "UPM (Sort)",  "patterns": ["UPM 107_950", "UPM 107_1200", "UPM 704_950"] },
    { "name": "SICC Sort",   "patterns": ["SORT SICC ATOM*", "SORT SICC CORE*", "SORT SICC RING"] },
    { "name": "SICC Class",  "patterns": ["CLASS SICC CORE*", "CLASS SICC ATOM*", "CLASS SICC RING"] },
    { "name": "Vmin Core",   "patterns": ["vc_*"] },
    { "name": "Vmin Atom",   "patterns": ["va_*"] },
    { "name": "Vmin Ring",   "patterns": ["vf_*"] }
  ]
}
```

### DLCP Decoding

DLCP (die-level classification parameter) is decoded from the CLASS-side `DevRevStep` column:

| DevRevStep `[4:6]` | DLCP label |
|--------------------|-----------|
| `2V` | `UL` (Ultra-Low) |
| `4V` | `UH` (Ultra-High) |
| `5V` | `UV` (Ultra-Voltage) |

Example: `DevRevStep = NCXSDJ4V...` → extract `[4:6]` = `"4V"` → DLCP = **`UH`**

The `DLCP` column is added to the normalized DataFrame by `class_normalize.py` and is available to all downstream HTML generators.

---

## BinSplitAnalysis

Located at `code/dashboard/BinSplitAnalysis/`. Generates a standalone `BinSplitAnalysis.html` and `BinSplitAnalysis.xlsx` from the AQUA class CSV.

**Config**: `config/config.json` — contains only a `productConfigFile` pointer to the shared ProductConfig. All tokens (`bin_matrix`, speed, lot/wafer) are resolved from the ProductConfig automatically.

**Inputs** (relative to `BinSplitAnalysis/`):

| Path | Description |
|------|-------------|
| `input/data/*.csv` | Raw AQUA class CSV(s) |
| `input/materialgroup/*.csv` | Material type lookup |
| `input/QDFconfig/NVL_BLLC_PO_BM.xlsx` | QDF spec table |

**Run**: `python generate_html.py`

---
  "sort_sicc":  { "ss_a0": "<full col name>", ... },
  "class_sicc": { "sc_c0": "<full col name>", ... },

  "vmin_freq_search": {             // prefixes used to auto-discover Vmin columns
    "core": "UPSVFPASSFLOW_U1PU5_6248_CLASSHOT_CR_",
    "atom": "UPSVFPASSFLOW_U1PU5_6248_CLASSHOT_AT_",
    "ccf":  "UPSVFPASSFLOW_U1PU5_6248_CLASSHOT_CCF_"
  },

  "groups": [                       // plot groups for generate_pcm_html (fnmatch patterns)
    { "name": "UPM (Sort)",  "patterns": ["u107_650", "u107_950", ...] },
    { "name": "SICC Sort",   "patterns": ["ss_a0", ..., "ss_fc"] },
    { "name": "SICC Class",  "patterns": ["sc_c0", ..., "sc_fc"] },
    { "name": "Vmin Core",   "patterns": ["vc_*"] },
    { "name": "Vmin Atom",   "patterns": ["va_*"] },
    { "name": "Vmin CCF",    "patterns": ["vf_*"] }
  ]
}
```

---

## Dashboard Layout

The CLASS Analysis HTML uses a **3-panel architecture** (all panels vertically resizable):

### Panel 1 — Lot / Wafer Filter

Filter controls scoped to:

| Filter | Column |
|--------|--------|
| Program Name (class) | `Program_Name_6248` |
| Program Name (sort) | `Program_Name_..U1.U5` |
| Sort Lot | `SORT_LOT_U1.U5` |
| Sort Wafer | `SORT_WAFER_U1.U5` |
| Sort X / Y | `SORT_X_U1.U5` / `SORT_Y_U1.U5` |

### Panel 2 — Parameter Table

Tabular view of key parameters per unit:

- SICC Sort, SICC Class
- Vmin Core (all freq / clusters)
- Vmin Atom (all freq / clusters)
- Vmin CCF

### Panel 3 — Charts (configurable)

| Chart | Type | Detail |
|-------|------|--------|
| SICC Sort / SICC Class | Distribution histogram | One chart per rail |
| Vmin Core | XY scatter | Tab per frequency |
| Vmin Atom | XY scatter | Tab per frequency |
| Vmin CCF  | XY scatter | Tab per frequency |
| Variability | Box/violin | All parameters |

---

## Plot Outputs

### BinDistribution HTML
Exact copy from yield-dashboard. Shows bin counts and yield summary by bin.

### Wafer Pattern Heatmaps
One heatmap per lot/wafer. Shows spatial die distribution.  
IBIN wafer map is **disabled** (`render_ibin_wafermap=False`).

### CLASS Analysis HTML (class_analysis_html.py)

Generated by `class_analysis_html.py` (class-specific, not a copy).  
One interactive scatter chart per group:

| Group | X-axis | Y-axis |
|-------|--------|--------|
| UPM (Sort) | Lot / Wafer / Material | UPM value |
| SICC Sort | Lot / Wafer / Material | Sort SICC rail |
| SICC Class | Lot / Wafer / Material | Class SICC rail |
| Vmin Core | Lot / Wafer / Material | `vc_<freq>_<idx>` |
| Vmin Atom | Lot / Wafer / Material | `va_<freq>_<idx>` |
| Vmin CCF  | Lot / Wafer / Material | `vf_<freq>_1` |

---

## CLASS Analysis HTML — Interactive Features

### Flow-Detail Popup

- Opens when clicking any row in the flow table
- **Floating fixed panel** — positioned at top-right (`top:60px; right:20px`)
- **Sticky** — does not close on backdrop click; only the × button closes it
- **Draggable** — drag by the header bar to reposition anywhere on screen
- Resizable via CSS `resize:both` on the card

### Speed Flow Panels

- Speed Flow renders as three module panels on one page (Core, Atom, Ring)
- Panels are separated by draggable splitters; users can resize adjacent panels directly
- Panel widths persist while the tab remains active so filter changes do not reset layout

### Numeric Precision Policy

- VMIN values are displayed at **3 decimal places**
  - Includes Speed Flow table medians and Speed Flow Chart card medians
  - Includes popup statistics (Mean/Median/sigma/Min/Max)
  - Includes XY crosshair readouts and delta values for VMIN axis
- UPM values are displayed at **1 decimal place**
  - Includes UPM reference values shown in XY titles
  - Includes XY axis labels, median/crosshair readouts, and delta values for UPM axis

### Pass Summary Semantics

- DCM/ATOM pass summary buckets are computed **per frequency** and are **not cumulative** across frequencies
- Priority bucket logic is applied per frequency only:
  - 4-domain pass bucket (all domains valid)
  - 2-domain bucket (2 or 3 valid domains, excluding 4-domain)
  - 1-domain bucket (exactly one valid domain, excluding higher buckets)
- Practical example from recent validation: at ATOM 3.7G, 4-ATOM count can be zero while 2-ATOM count is non-zero when no unit appears in all four ATOM instance sets at that frequency

### Below Threshold Column (Pass Summary Table)

The rightmost column of the DCM Pass Summary table is **Below Threshold** — units that were tested at the given frequency but passed with only **1 DCM** (bucket = 1), which is below the minimum required for a passing grade.

- **Count** = number of units in bucket-1 at that frequency (after IBIN1 filter)
- **%** = that count divided by `_passDenom` (total units tested at that frequency)
- Units not tested at a frequency (e.g. 5.1 G / 5.4 G parts at 5.5 G) do **not** appear in bucket-1 for that frequency, so their count is correctly 0
- Color: grey (`#7f8c8d`) to distinguish from the green pass columns

### UPM% Normalization

Speed Flow tab XY scatter plots can optionally normalize the UPM X-axis to a **percentage relative to a reference value**:

- A **Normalize UPM%** checkbox and reference-value input appear in the Speed Flow summary header (next to the "By material" label)
- When enabled, every UPM value is displayed as `(upm / ref) × 100`, so the reference lot sits at 100 %
- The reference value defaults to the **median UPM** of the currently visible data and updates automatically when the filter changes
- The user can override the reference by typing a value directly in the input box
- The UPM axis label changes to `UPM 107_950 (% ref=<value>)` to make the mode visible at a glance
- State variables: `_FLOW_SUMMARY_NORMALIZE_UPM` (bool), `_FLOW_SUMMARY_NORMALIZE_UPM_PCT` (float)
- Interpolation helper `_vfInterpUpm(fghz, upmPct, mat)` performs bilinear interpolation for the normalised display

### Material Filter on XY Scatter Plots (Speed Flow tab)

All Vmin vs UPM XY scatter plots in the Speed Flow tab support **interactive material filtering**:

- When a chart is initialised with more than one material, **"by Material" groupBy is auto-enabled** and the checkbox is pre-checked
- A row of **material toggle buttons** appears below each chart — one coloured pill per material, matching the dot/line colours
- Clicking a material pill **hides** that material's dots, regression line, and legend entry; clicking again shows it
- A **Show all** button clears all hidden materials in one click
- The filter state is stored per chart in `_XY_STATE[cid].matHidden` (object) and `_XY_STATE[cid].matList` (sorted array)
- Key functions: `_xyToggleMat(cid, mat)`, `_xyToggleMatIdx(cid, idx)`, `_xyClearMatFilter(cid)`, `_xyUpdateMatBtns(cid)`
- All button `onclick` handlers use `data-cid` / `data-mi` data attributes (no inline quote escaping) to avoid JS runtime errors

### XY Scatter Plot (`_xyBuildSVG`)

**Grid & Axes**

- Nice-tick algorithm (`_ntk`) produces ~5 major ticks per axis on round numbers
- 5 minor subdivisions between each major tick (lighter grid lines)
- Y-axis (Vmin) labels: **3 decimal places**
- X-axis (UPM %) labels: **1 decimal place**

**Grouped mode** (when `groupBy` is set, e.g. by material)

- Each group rendered in a distinct colour (10-colour palette)
- Per-group regression line
- Per-group median diamond (filled, white stroke, non-interactive overlay)
- Legend: bottom-left, full material name + `(X=95.0; Y=0.812)` median annotation in grey
- Hidden materials (via material filter) are skipped in dots, regression, and legend

**Non-grouped mode**

- Single global median diamond with `Med (X=...; Y=...)` text annotation
- R² of overall regression shown

---

## Architecture

```
class-dashboard/
├── dashboard.py               # Tkinter GUI — sidebar nav + content frames
├── readme.md                  # This file
├── requirements.txt
├── setup/
│   └── NCXSDJ-CLASS-ProductConfig-L0.json
└── src/
    ├── class_normalize.py     # Renames AQUA cols → short keys, discovers Vmin columns
    ├── class_analysis_html.py # CLASS-specific interactive HTML (popup, XY scatter, flow table)
    ├── class_merge.py         # Merges normalised data with reticle / material collateral
    ├── class_bindist.py       # Bin distribution summary for class bins
    ├── generate_class_html.py # Orchestrator: calls normalize → merge → analysis HTML
    ├── _constants.py          # Repo-root paths (_RETICLE_DIR, _MATERIAL_DIR, _wm_inject)
    │
    │   # ── Exact copies from yield-dashboard ──────────────────────────────
    ├── add_material_type.py
    ├── apply_reticle_mapping.py
    ├── bin_distribution_html.py
    ├── generate_heatmap_from_csv.py
    ├── csv_utils.py
    │
    │   # ── Exact copy from etest-dashboard ─────────────────────────────────
    └── generate_pcm_html.py
```

**Rule:** modules sourced from other dashboards must **not** be modified in this repo.  
All class-specific logic lives in `class_normalize.py`, `class_analysis_html.py`, `class_merge.py`, `class_bindist.py`, and `generate_class_html.py`.

### Sidecar Data File

To keep the HTML file loadable in browsers regardless of dataset size, all JS data variables are written to a **sidecar file** alongside the HTML:

| File | Contents |
|------|----------|
| `<tag>_class_analysis.html` | Dashboard UI, logic, and CSS |
| `<tag>_class_analysis.data.js` | All data variables (`WFR_DATA`, `PCM_ROWS`, `FLOW_DATA`, etc.) |

The HTML loads the sidecar via `<script src="<stem>.data.js"></script>` before the logic block.  
When sharing, zip both files together — the HTML will not render correctly without the sidecar.

**Data policy:** row data is **never capped or sampled** in either the flow builder (`_build_vmin_flow_data`) or the pass table builder (`_build_vmin_pass_table`). All rows from the input CSV are passed through to the sidecar JS.

