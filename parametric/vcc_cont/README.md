# VccCont BIN8 Dashboard

**Version:** v2  
**Author:** Pant, Sujit N — GEMS FTE  
**Location:** `app.dashboard.nvl/parametric/vcc_cont/src/`

---

## Overview

The VccCont BIN8 Dashboard is a self-contained Python tool that ingests yield CSV/ZIP data from SORT testing and generates an interactive HTML dashboard focused on **BIN8 (VCC Continuity) failures**. It parses the `SETBIN` column to identify the kill test per die, correlates measured pin currents against limits loaded from test program JSON files, and renders wafer maps, Pareto charts, histograms, and pattern-analysis overlays.

---

## Files

| File | Purpose |
|---|---|
| `src/generate_dashboard.py` | Core analysis and HTML generation engine (CLI entry point) |
| `src/gui.py` | Tkinter GUI launcher — wraps `generate_dashboard.py` as a subprocess |

---

## Architecture

```
gui.py  (Tkinter GUI)
   │  subprocess call
   ▼
generate_dashboard.py  (CLI)
   ├── Load CSV / extract from ZIP or GZ
   ├── _merge_material()          — join material/skew metadata from shared/material/
   ├── Split by PROG_COL          — one analysis per program name in the CSV
   │
   └── analyze_program()          — per-program pipeline
         ├── load_limits()              — parse VCC JSON limits from program InputFiles
         ├── resolve_limits_from_trace()— fallback: XEUS trace auto-detection
         ├── Per-die BIN8 loop          — parse SETBIN, collect failing pin readings
         ├── _compute_pin_distrib()     — histograms, Cp/Cpk, per-wafer stats
         ├── _build_raw_pin_data()      — Live Mode: full raw data for all limit pins
         ├── build_flow_data()          — VCC flow diagram data
         ├── WpaHtmlBuilder             — wafer pattern analysis overlay
         └── build_html()               — assemble final HTML
```

---

## Entry Points

### GUI (recommended)

```powershell
python gui.py
```

Opens a dark-themed Tkinter window where you can select inputs, configure options, generate, and open the dashboard.

### CLI

```powershell
python generate_dashboard.py \
    --csv  "C:\path\to\Yield.CSV" \
    --out  "C:\path\to\output\" \
    --prog-root "I:\program\1001\prod\hdmtprogs\nvl_ncx_sds" \
    --no-gui
```

| Argument | Description |
|---|---|
| `--csv` | Input yield CSV, ZIP archive, or `.csv.gz` file |
| `--out` | Output HTML file path or output folder |
| `--prog` | Single test program directory (for single-program CSVs) |
| `--prog-root` | Root folder containing one subfolder per program name — **preferred for multi-program CSVs** |
| `--json` | Explicit path to a `VCC_SDS_VSIM_START.json` limits file (fallback) |
| `--no-gui` | Skip the interactive run-options dialog; use auto-threshold only |
| `--live-mode` | Force Live Mode (embed raw pin data for interactive pin inspect) |
| `--focus-wafers N` | Auto-enable Live Mode when wafer count ≤ N (default: 50) |
| `--setup PATH` | JSON preset file path (default: `src/setup.json`) |

---

## Setup JSON Preset

A JSON file (loaded/saved via the GUI **Load / Save** buttons or the `--setup` argument) persists all path settings:

```json
{
  "csv":        "C:\\path\\to\\Yield.CSV",
  "prog":       "I:\\program\\...\\NCXSDJXL0H61C002620",
  "prog_root":  "I:\\program\\1001\\prod\\hdmtprogs\\nvl_ncx_sds",
  "out":        "C:\\path\\to\\output",
  "live_mode":  false,
  "focus_wafers": 50
}
```

---

## Input Data

### CSV Columns Used

| Column constant | Meaning |
|---|---|
| `INTERFACE_BIN_119325` | Interface bin — BIN8 (and 80, 89) are VCC failures |
| `FUNCTIONAL_BIN_119325` | Functional bin — used for Pareto grouping |
| `DATA_BIN_119325` | Data bin |
| `TPI_BIN::CTRL_UB_X_K_BIN_X_X_X_X_SETBIN_119325` | Kill-test string: `DATA_BIN\|KILL_TEST_FULL_NAME\|FLAGS` |
| `Lot_119325` | Lot ID |
| `Program Name_119325` | Test program name — CSV is split by this column |
| `SORT_WAFER` | Wafer number within lot |
| `SORT_X`, `SORT_Y` | Die coordinates |
| `DevRevStep_119325` | Device/revision/step — used to select the reticle map |

### Continuity Measurement Columns

Columns matching the regex pattern:

```
TPI_VCC::CONT_<rail>DPS_DC_<K|E>_<flow_kw>_X_X_X_X_<cond>_<pin>_<runid>
```

- **K-mode** columns: kill-test current readings (used for limit exceedance analysis)
- **E-mode / ISVM_EDC** columns: EDC current readings

---

## Limits Loading

Limits are loaded from the test program `Modules/TPI_VCC/InputFiles/` directory. The following JSON files are merged in priority order (START takes precedence for common pins):

| JSON file | Phase |
|---|---|
| `VCC_SDS_VSIM_START.json` | Pre-Surge / SDS Start |
| `VCC_SDS_VSIM_STRESS.json` | Stress |
| `VCC_SDS_VSIM_FINAL.json` | SDS Final |
| `VCC_SDTSTART_VSIM.json` | SDT Start |
| `VCC_SDTFINAL_VSIM.json` | SDT Final |
| `VCC_SDS_ISVM.json` | ISVM EDC limits |

**Fallback chain:** program directory → XEUS trace auto-detection → `--json` argument.

When using a multi-program CSV, supply `--prog-root` so each program receives its own limits. Without it, limits will be empty for programs that don't match the `--prog` directory name.

---

## Force Voltages

Force voltages per rail type (VLC, LC, HV, HC) are parsed from `LevelsSequences.lvl` in the program directory. The parser walks `Levels` blocks looking for `dc_spex` entries in VSIM or ISVM mode and prefers `dc_spex_VSIM_SDS_` > `dc_spex_VSIM_` > `dc_spex_ISVM_`. Falls back to XEUS trace auto-detection.

---

## Analysis Pipeline (`analyze_program`)

1. **Limits loading** — phase-specific + union dict for pin name discovery.
2. **BIN8 filtering** — keeps interface bins 8, 80, 89.
3. **Column discovery** — regex-parses all K-mode and E-mode continuity columns.
4. **Per-die loop** — for each BIN8 die:
   - Parses `SETBIN` to get the kill test name.
   - Identifies which K-mode columns correspond to that kill test.
   - Compares measured current vs. USL/LSL; records the worst violating pin per (pin, phase) pair.
   - Collects ISVM EDC summary per rail type.
5. **Summaries built:**
   - `fb_list` — failures by Functional Bin
   - `wfr_list` — failures by wafer
   - `kill_list` — failures by kill test (short label)
   - `pin_list` — failures by pin name
   - `rail_list` — per-pin statistics by phase
6. **Surge delta** — computes Pre-Surge vs Post-Surge median/P99 for passing dies per rail.
7. **ISVM EDC analysis** — similar Pre/Post statistics for EDC columns.
8. **Rail × Condition matrix** — die counts at intersection of (Pre-Surge, Post-Surge, ISVM-EDC) × (VLC, LC, HV, HC).
9. **Pin distributions (`_compute_pin_distrib`)** — 30-bin histograms with Cp/Cpk, per-wafer stats (normal mode), or deferred to JS (Live Mode).
10. **Flow data** — VCC test flow diagram.
11. **Wafer map** — all dies per wafer keyed by `prog|lot|wafer`.
12. **Reticle overlay** — loaded from `shared/reticle/` via `load_reticle_map`.
13. **Wafer Pattern Analysis (WPA)** — `WpaHtmlBuilder` computes systematic-pattern scores per wafer.

---

## Output

### Single program

```
output/
  vcccont-bin8-analysis.html       ← self-contained HTML (~5–30 MB)
```

### Multi-program CSV

```
output/
  vcccont-bin8-analysis-<prog1>.html
  vcccont-bin8-analysis-<prog2>.html
  index.html                        ← master page with sidebar nav + iframe
```

The output folder is cleaned of stale `.html` files before each run.

---

## Live Mode (Focus Mode)

When the wafer count is at or below the threshold (default: 50), **Live Mode** is automatically enabled. In this mode:

- All raw per-die, per-pin measurements for every limit pin are embedded directly in the HTML as `RAW_PIN_DATA`.
- The HTML JavaScript uses this data to render interactive scatter plots by wafer, lot, or die location.
- `wfr_stats` in `pin_distrib` is omitted (JS computes it on the fly from `RAW_PIN_DATA`).
- `detail_data` is omitted (all pins are covered by `RAW_PIN_DATA` instead of only the top 5).

Live Mode can be forced from the GUI checkbox or with `--live-mode` on the CLI, regardless of wafer count.

---

## Material Metadata (`_merge_material`)

Merges material type / skew columns from CSV files in `shared/material/`. Join key:

```
LOT7  = first 7 characters of Lot column
WAFER = integer of last 2 characters of SORT_WAFER
```

Matched against `INTEL_LOT7` + `WaferID` columns in the material lookup files. Adds columns:
- `Material Type, Skew, BEOL Skew`
- `Material Type`

---

## Phase Labels

| Label | Matched keyword(s) in test name |
|---|---|
| `Pre-Surge` | `PRESURGE` |
| `Post-Surge` | `POSTSURGE`, `START` (non-SDT) |
| `Post-Surge-HT` | `ETEMP` |
| `Stress` | `STRESS` |
| `SDS-Final` | `FINAL` (non-SDT) |
| `SDT-Start` | `SDTSTART` |
| `SDT-Final` | `SDTFINAL` |
| `ISVM-EDC` | `ISVM` |

---

## Rail Types

Parsed from the K-mode column name prefix:

| Label | DPS prefix in column |
|---|---|
| `VLC` | `VLCDPS` |
| `LC` | `LCDPS` |
| `HV` | `HVDPS` |
| `HC` | `HCDPS` |

---

## GUI (`gui.py`)

The Tkinter GUI provides:

| Control | Purpose |
|---|---|
| **CSV / ZIP file** | Select a yield CSV, ZIP containing a CSV, or `.csv.gz` |
| **Program root** | Root folder containing one subfolder per test program |
| **Output folder** | Destination for generated HTML files |
| **Setup file** | Load/Save all paths and settings as a JSON preset |
| **⚡ Live Mode** | Toggle raw-data embedding for interactive pin scatter plots |
| **⚙ Generate Dashboard** | Runs `generate_dashboard.py` as a subprocess with a live log |
| **▶ Open Dashboard** | Opens the generated HTML in the default browser |
| **✕ Cancel** | Terminates the running subprocess |

Log lines are color-coded: errors in red, warnings in gold, success in green, verbose output in dim.  
ZIP and GZ inputs are extracted to a temporary folder that is deleted after generation.

---

## External Dependencies

### Python packages

- `pandas` — CSV loading, data manipulation
- `numpy` — histogram binning, vectorized statistics
- `tkinter` (stdlib) — GUI and run-options dialog

### Shared libraries (workspace-relative)

| Path | Used for |
|---|---|
| `app.yield.nvl/code/utilities/wafer_tools/` | `wafer_pattern_analysis`, `wafer_map`, `wafer_analysis_parametric.reticle` |
| `app.yield.nvl/code/utilities/trace/` | `trace_bridge` — XEUS lot queries and auto-limit detection |
| `app.dashboard.nvl/shared/library/plotly-2.32.0.min.js` | Embedded Plotly for all charts |
| `app.dashboard.nvl/shared/reticle/` | Reticle map CSV files |
| `app.dashboard.nvl/shared/material/` | Material/skew lookup CSV files |

---

## Key Constants

| Constant | Default value | Description |
|---|---|---|
| `TARGET_IBIN` | `8` | Primary interface bin for BIN8 analysis |
| `TARGET_IBINS` | `[8, 80, 89]` | All VCC failure interface bins collected |
| `_DEFAULT_CSV` | `...vcc_cont_bin8/data/61A-61B-Yield.CSV` | Built-in fallback CSV path |
| `_DEFAULT_PROG` | `I:\program\1001\...\NCXSDJXL0H61C002620` | Built-in fallback program directory |
| `_DEFAULT_OUT` | `...vcc_cont_bin8/output/vcccont-bin8-analysis.html` | Built-in fallback output path |
