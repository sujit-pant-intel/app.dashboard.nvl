---
applyTo: "**"
---

# Dashboard Workspace — Master Agent Instructions

## Workspace Overview

This workspace contains four integrated tools for Intel sort data analysis, all
unified under a single tabbed GUI (`dashboard.py`). Each sub-project also runs
standalone.

```
dashboard/
├── dashboard.py           ← Unified tabbed GUI (entry point for all tools)
├── deploy_dashboard.py    ← Protect & deploy Python sources (Cython/PyArmor/Nuitka)
├── collateral/            ← Reticle mapping CSVs (collateral/reticle/) + lot-definition CSVs (collateral/material/)
├── yield_dashboard/       ← Yield analysis pipeline  (see sub-section below)
├── sicc_cdyn_upm/         ← SICC/CDYN/UPM analysis: JSL pipeline + Pure-Python dashboard (no JMP required)
└── vmin/                  ← Vmin outlier analysis     (see sub-section below)
```

---

## Unified GUI (`dashboard.py`)

Entry point for the entire workspace.

```powershell
python dashboard.py
```

**Tabs:**

| Tab | Frame class | What it does |
|---|---|---|
| Pipeline | `PipelineFrame` (yield_dashboard/src/pipeline.py) | Full yield pipeline + HTML reports |
| Compare | `CompareFrame` (yield_dashboard/src/compareTP.py) | Multi-run comparison report |
| Manage | `ManageFrame` (yield_dashboard/src/manage_dashboard.py) | Delete/reorder Dashboard.html run blocks |
| Wafer Map | `WaferHeatmapFrame` (dashboard.py) | Per-IBIN wafer scatter heatmaps |
| Vmin | `VminFrame` (dashboard.py → vmin/src/run_vmin.py) | Vmin outlier analysis via JMP |
| Portable | `PortableFrame` (dashboard.py) | Embed all assets into portable single-file HTML |

**Shared opener server** — started once at launch on a random port.
HTML buttons call `fetch('http://127.0.0.1:<port>/open?path=...')` to open
JMP project files and Excel files via the OS shell without browser download prompts.

---

## Sub-Project 1 — Yield Analysis Pipeline (`yield_dashboard/`)

### Purpose
End-to-end yield data processing: AQUA fetch → BinDef parse → Digital Dashboard
update → BinDistribution chart → contour heatmaps → custom plots → SICC/UPM
analysis → master HTML report + persistent Dashboard.html.

### Entry Points

| Method | Command |
|---|---|
| GUI (via unified) | `python dashboard.py` → Pipeline tab |
| GUI (standalone) | `python yield_dashboard/src/pipeline.py` |
| CLI | `python yield_dashboard/src/yield_pipeline.py --input run_config.json` |

### Key Source Files

| File | Role |
|---|---|
| `src/pipeline.py` | Tkinter GUI, pipeline driver, HTML builder |
| `src/yield_pipeline.py` | CLI orchestrator |
| `src/bin_distribution_html.py` | BinDistribution HTML (lot/wafer/material filter) |
| `src/generate_heatmap_from_csv.py` | Per-bin contour heatmap HTMLs |
| `src/generate_plots_from_csv.py` | Custom analysis plots (one HTML per tag) |
| `src/get_dd_update.py` | Appends yield columns to DigitalDashBoard.xlsx |
| `src/parse_bindef_to_crystalball.py` | `BinDefinitions.bdefs` → bindef CSV |
| `src/compare_runs.py` | Multi-run comparison report |
| `src/compareTP.py` | Tkinter front-end for compare_runs |
| `src/manage_dashboard.py` | GUI to delete run blocks from Dashboard.html |
| `src/make_portable_dashboard.py` | Single-file portable Dashboard HTML |
| `src/apply_reticle_mapping.py` | Merges reticle layout + material type into yield CSV; output: `<csv>_reticle_material.csv` |
| `src/add_material_type.py` | Merges material type info (TSMC_LOT, Material Type, etc.) from lot-definition CSVs; runs BEFORE reticle merge |
| `src/csv_utils.py` | Encoding detection, chunked CSV reads |
| `src/_loader.py` | Subprocess dispatcher for `.py` / `.pyd` modules |
| `src/_pipeline_runner.py` | Pipeline orchestration (background thread) |
| `src/_pipeline_ui.py` | Tkinter widget layout for the pipeline tab |
| `src/_pipeline_html.py` | `_build_master_html` — builds `index.html` with collapsible sidebar + iframe layout |
| `src/_pipeline_server.py` | Opener HTTP server (file:// open buttons) |
| `src/_pipeline_constants.py` | Shared constants (paths, palette, etc.) |
| `src/fail_bucket_table.txt` | Default bin → fail bucket → expected yield |

### Pipeline Config JSON

```json
{
  "skip_aqua":         true,
  "outputFilename":    "C:\\data\\yield.CSV",
  "TestProgram":       "NVL_SDS",
  "TestProgram_folder":"C:\\tp\\TestPrograms\\NVL_SDS",
  "dashboard":         "C:\\data\\DigitalDashBoard.xlsx",
  "output_folder":     "C:\\data\\runs",
  "identifier":        "NVL_SDS_51M",
  "product_config_json": "C:\\configs\\product_config.json",
  "analysis_info":     "C:\\configs\\analysis_parameters.json",
  "sicc_run":          true,
  "sicc_csv_file":     "C:\\data\\sicc_target.csv",
  "sicc_output_dir":   "C:\\data\\sicc_out"
}
```

### Product Config JSON

Controls bin labeling, yield targets, and SICC target generation.

```json
{
  "bin_map": {
    "1": {"cat": "Pass", "desc": "SDS FF (No Repair)"},
    "2": {"cat": "Pass", "desc": "MBIST Repair"},
    "8": {"cat": "Fail", "desc": "TPI Foundry"}
  },
  "yield_targets": {
    "1/2":     {"fail_bucket": "SDS FF yield",    "expected_yield_percent": 67.8},
    "1/2/3/4": {"fail_bucket": "SDS FF+DF yield", "expected_yield_percent": 86.0}
  },
  "sicc_targets": [
    {"test": "VCCINT_SICC", "target_A": 1.23e-9},
    {"test": "VCCAUX_SICC", "target_A": 4.56e-10}
  ]
}
```

`sicc_targets` auto-generates `_sicc_targets_generated.csv` next to the JSON.
Use key `"target_A"` (not `"target"`).

### Pipeline Flow

```
Load JSON → Run
│
├─ 1. AQUA Fetch            (skip_aqua=false)
│       AquaCmdLine.exe → yield.CSV
│       Renames .temp file if needed
│
├─ 2. Parse BinDefinitions
│       BinDefinitions.bdefs → {TestProgram}_bindef.csv
│       Skipped if bindef CSV already contains DB\d+ entries
│       Fallback: glob {prefix7}*/BinDefinitions.bdefs
│
├─ 3. Append to Digital Dashboard
│       yield CSV + bindef CSV → new columns in DigitalDashBoard.xlsx
│       ⚠ Fails if Excel has the file open
│
├─ 4. Add Material Type         (before reticle merge)
│       collateral/material/*.csv → lot-definition lookup by 6-char LOT7 prefix
│       Merges: TSMC_LOT, Material Type, Skew, BEOL Skew, Production Lot, WaferID
│       Output: <csv>_material_merged.csv  (temp dir or output_dir, never in-place)
│       "Save reticle/material CSV" checkbox controls dest (output folder vs temp)
│
├─ 5. Apply Reticle Mapping     (optional)
│       DevRevStep_* prefix → collateral/reticle/*.csv lookup
│       Merges: Layout, Device, LayoutX/Y, ReticleDieX/Y, Reticle
│       Output: <csv>_reticle_material.csv  [zipped if save checked]
│
├─ 6. Generate BinDistribution
│       → output/{stem}_BinDistribution.png   (bar chart, % labels above each bar)
│       → output/{stem}_BinDistribution.html
│           • Interactive histogram + IB legend panel
│           • IB legend search box: type to filter legend entries by bin or description
│           • Click legend entry → isolates that IB in the histogram (hides all other bars)
│           • Ctrl+click legend → opens FB breakdown popup (bar chart + FB checkboxes)
│           • Click bar in histogram → opens FB breakdown popup
│           • FB popup is **floating/draggable** (non-modal): transparent overlay with
│             `pointer-events:none`; dark blue title drag bar; click X to close
│             Position resets on each open; Escape key closes it
│           • FB popup is resizable: drag bottom-right corner handle (min 420×260px)
│           • FB popup: "Show Wafer Distribution" → wafer tile grid colored by FB count
│           • Clicking a wafer tile closes popup and selects that wafer in filter table
│           • FB popup: "HW Breakdown" button opens floating HW Breakdown popup
│             (does NOT auto-open; must be clicked manually from within FB popup)
│           • FB popup: "UPM Heatmap 📊" button (shown when UPM columns present)
│             Opens a second floating draggable popup with per-wafer SVG heatmaps
│             colored by UPM @950mV as % of spec (9154 MHz), scaled to data range
│             Dies for the active IB + checked FBs shown at full opacity; others dimmed
│             Hover any die for tooltip: % of spec, raw MHz, IB, FB, x/y coordinates
│             Updates live when FB or HW checkboxes change
│           • Unchecking FB or HW checkboxes live-updates the histogram bar counts
│           • _fbFilterIb tracks the active IB; gFC() applies FB+HW filter when set
│           • Closing FB popup resets _fbFilterIb and HW selection; chart recalculates
│           • Per-die data embedded in DATA.rows[i].dies: [x, y, ib, fb, upm0, ...]
│             (fb = functional bin number per die; used for UPM heatmap FB-aware dimming)
│
├─ 7. Inject Bin Fail Summary
│       Replaces <!-- PARETO_INJECT_START/END --> sentinel in BinDistribution.html
│       Columns: Bin | Category | Description | Total Count | Fail Count | Yield/Fail%
│       Row order: bin_map order → yield_targets-only bins → unknown ⚠ bins
│
├─ 8. Generate Contour Heatmaps + IBIN Wafer Map
│       One HTML per bin group with >0 hits
│       → output/heatmap/{stem}_Heatmap_bin_{label}.html
│       Shared X/Y axis limits (global min/max ± 1); top 5 hotspots annotated
│       IBIN Wafer Map: → output/heatmap/{stem}_IBIN_WaferMap_{lot}.html
│           • Die rectangles colored by IBIN; composite + per-wafer views
│           • Composite view: SVG + legend side by side with no separator line
│             Corner resize handle (▗ bottom-right) scales SVG width+height together
│             (aspect ratio preserved via viewBox; legend re-aligns during drag)
│           • Click IB legend entry → sticky bottom FB filter panel
│           • FB panel: checkboxes per FB, count+%, Highlight button, Show All, Close
│           • Ctrl+click legend → classic dim-other-bins toggle
│           • Highlight: matching dies glow yellow, non-selected dim to 10% opacity
│           • FB panel: "HW Breakdown" button → floating draggable popup
│             (non-modal: transparent backdrop, pointer-events:none on overlay)
│             Popup counts only selected wafer's dies when a wafer is active;
│             falls back to composite SVG only when no wafer filter is set
│
│       Wafer Pattern Analysis (WPA) — floating modal overlay on wafermap.html
│           Opened via "📊 Wafer Pattern Analysis" button in the IBIN Wafer Map header bar.
│           In popup/embedded mode the IBIN header and WPA title bar are hidden.
│
│         Layout (two-column with vertical splitter):
│           ┌────────────────────────────────────┬──────────────────────────────┐
│           │  Left panel (Wafer Maps / Composite│ Right panel (Bin Impact / ... │
│           │  Map tabs)                         │ tabs)                        │
│           └────────────────────────────────────┴──────────────────────────────┘
│           • Vertical splitter (gray grip bar) between left and right panels.
│             Drag to resize horizontally.  Drag fully right → right panel hides,
│             left panel expands to full WPA window width automatically.
│           • WPA box resizable from bottom-right corner (browser native resize).
│
│         Lot / Wafer Picker (two rows at top of WPA box):
│           Row 1 — purple bar: "Lots: All | None | <lot checkboxes>"
│           Row 2 — dark bar:   "Wafers: All | None | [Lot] <wafer checkboxes>..."
│           • Lot labels parsed from key with `::` separator (lot::wafer format).
│           • Unchecking a lot cascades to uncheck all its wafers.
│           • `[LotName]` separators group wafers by lot in row 2.
│
│         Left Panel tabs:
│           • 🗏 Wafer Maps — individual per-wafer SVG maps for selected wafers
│           • 🔬 Composite Map — Mode-IB composite SVG across all selected wafers
│
│         Right Panel tabs:
│           • 🔍 Bin Impact — per-bin fail% table (no controls bar; content only)
│           • 🔬 Composite Map — exact copy of left panel composite; updates in sync.
│           • 🌐 Reticle — reticle-site analysis (shown only when reticle map loaded;
│               no controls bar at top — all controls are in retrow/shotrow)
│           • ℹ Guide — spatial pattern reference table (CENTER/EDGE/DONUT/SYST/RET/RANDOM)
│
│         Filter rows (below wafer picker, above left+right panels):
│           • retrow  — "Die Loc: [Loc1][Loc2]... All | None"  (reticle site checkboxes)
│               Shown only when reticle map loaded and wafers selected.
│           • shotrow — "Shot #: <dropdown> | Excl. edge rows: <0–10 select> | ≥IB: 1 2 3 4 5"
│               Shot # dropdown: multi-select with search; button shows "All (N)" or "N/M selected"
│               Edge row exclusion: configurable 0–10 rows (default 1)
│               IB threshold: radio 1–5 (default 3); controls both wafer maps and reticle view
│
│         Pattern Scores panel (below left+right panels, above resize handle):
│           • Horizontal gray resize bar — drag up/down to change scores panel height.
│           • Lot Trend table: Lot | Wfrs | Primary | Center | Edge | Donut | Syst. |
│                              [Ret.] | [Top Die Loc] | Rnd
│             Top Die Loc: aggregate raw site fail counts across all wafers in the lot,
│             then pick the site with the highest total — consistent with Table A sort.
│           • Per-wafer table: Lot | Wafer | Material | Primary | Fail% | Driver IB |
│                     Center | Edge | Donut | Syst. | [Ret.] | Rnd
│
│         Reticle filter on Composite Map:
│           • When reticle checkboxes are unchecked, matching dies show white in composite SVG.
│           • Applied in both left and right composite map panels.
│
│         Key JS functions in wafermap.html (_pipeline_html.py):
│           _wmPatBuildLotPicker()       — builds lot row with All/None + checkboxes
│           _wmPatBuildWaferPicker()     — builds wafer row filtered by active lots
│           _wmPatLotToggle(lt,on)       — cascades wafer unselection when lot unchecked
│           _wmBuildModeMap(keys)        — renders composite SVG into both #wm-pat-modemap-body
│                                         and #wm-pat-modemap-body2 (right panel copy)
│           wmPatCollapseRight() — removed; splitter drag to full-right replaces this
│           wmPatTab(t)                  — switches right panel tabs
│           wmPatLTab(t)                 — switches left panel tabs
│           _wmPatInitDrag()             — drag-to-move on green title bar; called on WPA open
│
├─ 8. Generate Custom Plots  (analysis_info JSON present)
│       One HTML per analysis tag → output/{stem}_{tag}.html
│
├─ 8b. Parametric Dashboard  (run_parametric=true)
│       Called via `sort-parametric/parametric_runner.py`
│       Steps:
│         1. Extract lot IDs from merged CSV using `usecols=[lot_col]` (ALL rows, no nrows cap)
│            Priority: SORT_LOT > lot (case-insensitive) > any col with 'lot' not 'slot'
│         2. Find PCM CSVs: BOTH shared/etest/9-sites/ and shared/etest/full-sites/ always searched
│            `--full-site` flag sets full-sites priority; plain CSVs and ZIPs both supported
│            Matching: exact 8-char lot ID first; 7-char prefix fallback
│         3. Load & merge PCM data via etest-dashboard's `_load_and_merge`
│         4. IDW spatial expansion (9-site PCM → all reticle positions)
│         5. Generate pcm_analysis.html via `generate_pcm_html.generate_html`
│            `default_groups` comes from product config's `pcm_param_groups` key
│         6. Generate ParametricDashboard.html (iframe wrapper: PCM + UPM + SICC + CDYN panes)
│       Key file: `sort-parametric/parametric_runner.py`
│
├─ 9. SICC/CDYN/UPM Analysis (sicc_run=true)
│       → ../sicc_cdyn_upm/src/run_dashboard.py --headless  (JMP-based)
│       → sicc_upm_dashboard.html + plot.html
│       Also runs Python SICC/CDYN pipeline (sicc_cdyn_upm)
│       → {stem}_sicc_analysis.html
│       "Open JMP Project" button uses opener server on port 56947
│
├─ 10. Build Master Report
│       output/index.html
│       Left sidebar (collapsible, toggles to 30px strip via #sb-toggle button)
│         + iframe content area; sidebar links open pages in the iframe.
│       Sidebar: Bin Distribution | SICC/CDYN/UPM | UPM Distribution |
│               Wafer Maps | Heatmaps | Fail Pareto | Custom Plots
│       After zip creation: Downloads section injected before <!-- SIDEBAR_END -->
│       with link to <csv>_reticle_material.zip (if save checkbox checked)
│
└─ 11. Update Dashboard.html
        Co-located with DigitalDashBoard.xlsx (or custom path)
        Adds/replaces collapsible run-block keyed by identifier
        Block matching: exact `data-stem` match only (no substring matching)
        Same identifier → replaces existing block; different identifier → adds new block
        Custom filename supported (e.g. Dashboard-test.html) — created if not exists
        File created fresh only if it doesn't exist; existing file always updated in-place (concat fix)
        `_resolve_dashboard_path` returns absolute .html/.htm paths directly
        Run-block contains: "Dashboard Yield Report" link only
        (SICC/UPM link is NOT added here — reserved for SICC/UPM JSL run)
        Sections: YIELD_START/END · COMPARE_START/END · VMIN_START/END
        Watermark: injected once by `_wm_inject()` before </body>; guard checks `'GEMS FTE' in html`
                   WPA popup has a separate absolute-positioned badge in the title drag bar (top-right)
```

### `index.html` Sidebar Architecture

`index.html` uses a **sidebar + iframe** flex layout. Key CSS classes:

| Selector | Purpose |
|---|---|
| `#sidebar` | Left panel; `width:270px`; collapses to `width:30px` via `.sb-collapsed` |
| `#sidebar-inner` | Scrollable link list inside sidebar; hidden when `.sb-collapsed` |
| `#sb-toggle` | Absolute-positioned button inside `#sidebar`; shows `‹`/`›` arrow |
| `#content` | `flex:1` iframe; fills remaining width |

Toggle works by adding/removing `.sb-collapsed` on `#sidebar`; JS sets `textContent` to `›` (collapsed) or `‹` (expanded). The button lives **inside** `#sidebar` so it stays visible at `left:0` when collapsed.

Output files are written to the **T: drive** (network share), not `C:\temp`. Actual example paths:
- `T:\example\nvl816\output\<identifier>\index.html`
- `T:\example\nvl816-bllc\output\<identifier>\index.html`

### Output Structure

```
<output_folder>/<identifier>/
  output/
    index.html
    {stem}_BinDistribution.png
    {stem}_BinDistribution.html       ← + Bin Fail Summary & Fail Pareto injected
    {stem}_{tag}.html                 ← one per analysis tag
    heatmap/
      {stem}_Heatmap_bin_{label}.html
      {stem}_IBIN_WaferMap_{lot}.html
  sicc_upm_dashboard.html
  plot.html
<output_folder>/<identifier>/  (if save checked)
  {stem}_reticle_material.zip         ← reticle+material merged CSV, zipped
<dashboard_xlsx_dir>/
  Dashboard.html                      ← persistent, never erased between runs
```

### GUI Sections (pipeline.py / _pipeline_ui.py)

| Section | Fields / Notes |
|---|---|
| **Left panel — Inputs** | Dashboard.html path; **Data CSVs listbox** (first item = primary CSV, additional items merged in; supports CSV and ZIP; Add/Remove Selected buttons); auto-populated: Output folder (derived from dashboard dir), Identifier, TP folder, TestProgram; "Save merged file" checkbox |
| **Right panel — Options & Product Config** | Generate Wafermap checkbox; Product Config dropdown (auto-scan `shared/spec/collateral/yield/`); ↺ refresh + resolved path label |
| **Right panel — Parametric Dashboard** | Run Parametric checkbox; Full-site PCM checkbox; Parameter Groups dropdown (All/None/↺ per-product-config default); Custom filter (wildcard) |

**Data CSVs listbox** replaces the old single "Data CSV" entry + "Extra Data CSVs" text box:
- First item → `aqua_out_var` (primary CSV passed to pipeline)
- Items 1..n → extra CSVs merged in (`get_extra_csv_paths()` returns `items[1:]`)
- ZIP files in extra list are extracted the same way as the primary ZIP (all CSVs inside concatenated)
- Load JSON populates listbox from `DataCSV` (index 0) + `extra_csv_files` (subsequent items)
- `output_folder` is auto-derived from the dashboard path — never saved/restored from JSON

**Top bar buttons:** Load JSON · Run · Save JSON · Open Dashboard · ↺ Reset

**↺ Reset** clears all fields back to startup defaults (confirms first), including the listbox.

Pipeline runs in a **background thread**; output streamed via `queue.Queue` to the log.

### Subprocess Dispatch via `_loader.py`

All subprocess calls go through `_loader.py`:
```
python _loader.py <module_name> [args...]
```
- Adds its own directory (`yield_dashboard/src/`) to `sys.path` at startup
- Also adds **all** sibling `*/src/` directories (e.g. `sicc_cdyn_upm/src/`) via `_run_root.glob('*/src')`
- Imports the named module (works for `.py` **and** compiled `.pyd` binaries)
- Calls `mod.main()` if it exists, otherwise falls back to `runpy.run_module`
- This lets the same command work before and after `deploy_dashboard.py` compiles sources
- `_LOADER` constant is defined in `_pipeline_constants.py`; `_PYTHON` is redefined as `[sys.executable, '-B']` in `_pipeline_runner.py`

**Example dispatch pattern used in `_pipeline_runner.py`:**
```python
subprocess.run([sys.executable, _LOADER, 'bin_distribution_html', csv_path, out_dir])
# or (frozen/REPL path):
import runpy; runpy.run_module('bin_distribution_html', run_name='__main__')
```

### get_dd_update.py — Supported Products

| Product | DEVREVSTEP prefix | DPW |
|---|---|---|
| ARL68-N3B | `8PYJCVJ` | 797 |
| ARLS816 | `8PYVCVB`, `8PYVCVAB` | 516 |
| NVL48 | `8PY6CVT` | 1200 |
| NVL816 | `8PF6CVP`, `8PF6CVR`, `8PF6CVER` | 619 |
| NVL816-BLLC | `8PF5CVL` | 393 |

### compare_runs.py — Multi-Run Comparison

```powershell
python yield_dashboard/src/compare_runs.py Dashboard.html [--ref "<stem>"]
```

Report section order:
1. **Run Summary table** — Program Name / Lot(s) / Material Type / Wafer(s) / # Dies (one column per run)
   - Data sourced from `var DATA` in each run's `*_reticle_material_BinDistribution.html`
   - `parse_index_meta(dash_dir, index_href)` resolves the run folder from `index_href`, finds the best `*_BinDistribution.html`, and extracts unique programs/lots/wafers/materials
2. Yield Information chart (`build_combined_rdnd_chart`)
   - Y-axis label "Yield (%)" positioned at `y=0.4`; legend at `bbox_to_anchor=(1.18, 1.0)`
   - Expected-yield dotted lines; Exp XX% labels below the dotted line (staggered)
3. Yield Table
4. Bin Fail Summary (Bin | Category | Description | per-run Yield/Fail%)
5. Top 10 Interface Bin Fail Pareto
6. SICC Median + UPM charts
7. SICC/UPM Table — `Actual > Target` → red+bold; `Multiple > 1` → red+bold
8. xlsx comparison table

**Compare GUI (`compareTP.py`)**
- Each run row shows `Col N` label + ↑ / ↓ buttons + checkbox + name
- ↑↓ reorder the column order in the report; hint label: "Use ↑↓ to set column order in report"
- `generate_report(runs_data, out_path, config_json=..., dash_dir=dash_path.parent)` — `dash_dir` is the folder containing `Dashboard.html`; passed through to `build_run_summary_table_html`

### make_portable_dashboard.py

Embeds all assets into a single `Dashboard_portable.html`.

| Asset type | Strategy |
|---|---|
| PNG / CSS / JS | base64 data-URI |
| Linked `.html` | Blob URL (recursive, max depth 3) |
| `iframe src` | converted to `srcdoc` |
| `.xlsx`, `.jmp`, `.jmpprj`, `.csv` | disabled (greyed, strikethrough) |

### csv_utils.py

| Function | Purpose |
|---|---|
| `detect_encoding(path)` | Tries utf-8-sig → utf-8 → utf-16 → latin-1 |
| `sniff_columns(path)` | Header row only — no data rows loaded |
| `read_csv_smart(path, usecols)` | Full read with optional column selection |
| `iter_chunks(path, usecols, chunksize)` | 100k-row generator; override with `CSV_CHUNK_SIZE` env var |

---

## Sub-Project 2 — SICC/CDYN/UPM Analysis (`sicc_cdyn_upm/`)

### Purpose
Automates the full Static ICC (SICC) and UPM analysis — raw JMP data → median
calculation → bivariate scatter plot → HTML dashboard.

### Entry Points

| Method | Command |
|---|---|
| GUI (via unified) | `python dashboard.py` → Pipeline tab → SICC/CDYN/UPM checkbox |
| Standalone GUI | `python sicc_cdyn_upm/src/run_dashboard.py` |
| Headless (called by yield pipeline) | `python sicc_cdyn_upm/src/run_dashboard.py --headless ...` |

### Required Inputs

| Input | Notes |
|---|---|
| Input `.jmp` file | Raw SICC/UPM data from AQUA/SDS |
| Target CSV | `sicc_target-*.csv` — columns: `TestName`, `SICC Target (A)` |
| Output folder | Where JMP saves tables + PNGs + CSVs |
| Dashboard folder | Where `sicc_upm_dashboard.html` is written |

Target CSV is auto-generated from `sicc_targets` in the Product Config JSON when not provided manually.

### Key Source Files

| File | Language | Role |
|---|---|---|
| `src/run_dashboard.py` | Python | GUI launcher, matplotlib chart builder, HTML writer, opener server |
| `src/run.jsl` | JSL | Master JMP pipeline: calls scripts 1 & 2, exports CSVs, writes manifest |
| `src/1. process_sicc_upm.jsl` | JSL | Renames tests, sums, computes UPM%, stacks to Stacked_SICC_Combined |
| `src/2. calculate_median_and_plot.jsl` | JSL | Medians, target merge, JMP Bivariate plot |
| `src/testlist.jsl` | JSL | User-editable config: rename patterns, sum defs, UPM configs, contour columns |
| `src/_manifest.json` | JSON | Pipeline completion metadata written by run.jsl |

### JSL Conventions

- Always start standalone scripts with `Names Default To Here(1)`.
- Use `::varname` (double-colon) for variables shared across `Include()` boundaries.
- Use `Char(34)` or `q = Char(34)` instead of escaped `\"` in strings.
- Use `Is Missing(x)` instead of `x == .` for null checks.
- `Col Median(col)` — median of a column object; `Column(dt, "name")` — get column object.
- JSL date: `Char(Month(Today())) || "/" || Char(Day(Today())) || "/" || Char(Year(Today()))`.

### Key JSL Globals

| Variable | Purpose |
|---|---|
| `::dashboard_output_dir` | Output folder path set by Python before `Include(run.jsl)` |
| `::dashboard_csv_path` | Target CSV path set by Python |
| `::dashboard_manifest` | Path where `_manifest.json` is written |
| `::custom_testlist_path` | Optional override for testlist.jsl; empty = use default |

### matplotlib Plot Priority (run_dashboard.py)

`build_scatter_plot()` tries in order:
1. `scatter_data.csv` exists → SICC vs UPM scatter (colored by TestName, red median line)
2. `Group_Medians.csv` exists → horizontal bar (medians + target diamond markers)
3. `sicc_target*.csv` exists → target-only dot chart (shown before JMP run)

Do not merge these paths or change the priority.

### Generated Outputs

```
<output_folder>/
  scatter_data.csv           ← written by run.jsl from dtStacked (after full run)
  Group_Medians.csv          ← written by run.jsl after script 2
  bivariate_plot.png         ← matplotlib chart
  _manifest.json             ← pipeline metadata
  SICC_UPM_Analysis_Project.jmpprj

<dashboard_folder>/
  sicc_upm_dashboard.html    ← dark-theme dashboard; Open JMP Project + Open Folder buttons
  plot.html                  ← tabs: SICC/UPM scatter, contour plots, table
```

---

## Sub-Project 2b — Pure-Python SICC/CDYN/UPM (`sicc_cdyn_upm/`)

### Purpose
Pure-Python replacement for the JSL-based pipeline.
**No JMP required.** Reads sort-data CSV directly, processes it, and
generates a self-contained interactive HTML dashboard.

The yield pipeline (`yield_dashboard`) calls this automatically via
`_run_sicc_py_headless()` in `_pipeline_runner.py`. The output
`{stem}_sicc_analysis.html` appears in the yield report sidebar under
"SICC / CDYN / UPM" → "SICC/CDYN Report".

### Entry Points

| Method | Command |
|---|---|
| Standalone GUI | `python sicc_cdyn_upm/src/run_py_dashboard.py` |
| From JSL launcher | `python sicc_cdyn_upm/src/run_dashboard.py` → select "Python (New)" engine |
| Called by yield pipeline | `_run_sicc_py_headless()` in `_pipeline_runner.py` |

### Key Source Files

| File | Role |
|---|---|
| `src/run_py_dashboard.py` | Tkinter GUI launcher + `run_python_pipeline()` for programmatic use |
| `src/sicc_processor.py` | CSV processor: column rename, sums, UPM%, per-group medians, CDYN detection |
| `src/generate_dashboard_html.py` | **Thin assembler** — public API `generate_html(data, output_path, title)` |
| `src/_dash_frame.py` | CSS + page skeleton HTML; tab bar includes **SICC/CDYN SPEC** hyperlink |
| `src/_dash_js_shared.py` | Shared JS: tab registry, utils, category colors, sidebar, chart helpers, resize |
| `src/_tab_registry.py` | `Tab` dataclass + `TABS` list — edit here to add/remove tabs |
| `src/_tab_sicc.py` | SICC tab: HTML panel + JS |
| `src/_tab_cdyn.py` | CDYN tab: HTML panel + JS |
| `src/_tab_summ.py` | All Medians tab: HTML panel + JS |
| `src/_tab_charts.py` | Charts tab: UPM scatter, Pareto bar, distribution histogram |

### Tab Bar
The tab bar (`_dash_frame.py`) contains buttons for: **SICC**, **CDYN**, **All Medians**, **Charts** plus a hyperlink "SICC/CDYN SPEC" linking to the NVL816 PreSi summary Excel on SharePoint.
The link is appended to `tabs_html` in `generate_dashboard_html.py` after the tab loop.

### Config

Reads column-rename rules from `testlist.jsl` (JSL parsed) or `testlist.json`.
Default config: `collateral/sicc_cdyn_testlist.json` → fallback `sicc_cdyn_upm/src/testlist.jsl`.

Targets are extracted from Product Config JSON: `sicc_targets` (→ `target_A`),
`upm_target` (→ `target_%`), `cdyn_targets` (→ `target_nF`).

### Generated Output

```
<dashboard_dir>/{csv_stem}_sicc_analysis.html   ← self-contained interactive HTML
```

The HTML has four tabs: **SICC** | **CDYN** | **All Medians** | **Charts**.
Filter by Program / Lot / Wafer / Material. 100% client-side — no server required.

Key JS functions in the generated HTML:
- `drawTabScatter(active, col, svgId, titleId, noteId)` — compact scatter for SICC/CDYN tabs
- `drawScatter(active)` — full scatter for Charts tab
- `drawMiniUpm(active, primaryCol, isCdyn, svgId, titleId, noteId)` — compact UPM histogram
- `drawSVG(...)` — SVG histogram with optional UPM overlay
- `drawPareto(active, tgt)` — wafer-median Pareto bar chart with UPM diamond overlay
- All histograms prefer raw `die_pairs.s` values over midpoint approximation

### Common Pitfalls

- `biv` (JMP Bivariate object) is only live immediately after `Include(script2Path)`. Save PNG in the same `Try()` block.
- `Group_Medians.csv` is written by `run.jsl`, not script 2. Do not expect it until after a full run.
- `testlist.jsl` is the single source of truth for rename patterns, sum definitions, UPM configs.
- `_OPENER_PORT` is 0 until `start_opener_server()` is called. Call once at startup.
- HTML buttons must use `fetch('http://127.0.0.1:PORT/...')` with `href="javascript:void(0)"` — never `href="http://..."`.
---

## Sub-Project 3 — Vmin Analysis (`vmin/`)

### Purpose
Vmin outlier detection and distribution analysis. Launches JMP with a config JSL,
runs the analysis pipeline, generates an outlier summary HTML dashboard.

### Entry Points

| Method | Command |
|---|---|
| GUI (via unified) | `python dashboard.py` → Vmin tab |
| Standalone | `python vmin/src/run_vmin.py --headless --data-file <path> --output-dir <dir> --config-jsl <path>` |

### GUI Fields (VminFrame)

| Field | Notes |
|---|---|
| Input CSV / JMP | Raw Vmin data file |
| Config JSL | Optional; defaults to `vmin/src/config.jsl` |
| Output folder | Where JMP outputs are written |
| Dashboard HTML | Optional; existing or new Dashboard.html to update |

### JMP Auto-Detection

`run_vmin.py` searches these paths in order:
```
C:\Program Files\SAS\JMPPRO\18\JMP.exe
C:\Program Files\SAS\JMPPRO\17\JMP.exe
...
C:\Program Files\SAS\JMP\18\JMP.exe
```

### Outlier Analysis — Severity Scoring

| Score range | Priority | Meaning |
|---|---|---|
| ≥ 100 | CRITICAL (red) | Trimodal distribution, >50% outliers + bimodal, R² < 0.3 + bimodal |
| 50–99 | HIGH (orange) | High outliers, poor correlation, strong bimodal |
| 25–49 | MEDIUM (yellow) | Moderate outliers, weak correlation |
| < 25 | LOW (green) | Normal distribution, few outliers |

Score contributors: Trimodal +100, Outlier% > 50% +50, R² < 0.3 +40, Bimodality > 0.7 +25, |Kurtosis| > 3 +15, Negative slope +30, |Skewness| > 2 +12.

### Summary Table Columns

| Column | Description |
|---|---|
| `Priority` | CRITICAL / HIGH / MEDIUM / LOW (color-coded) |
| `Severity_Score` | Weighted numeric score; sort descending to find worst tests |
| `Issue_Flags` | Comma-separated: TRIMODAL, HIGH_OUTLIERS, POOR_CORRELATION, WEAK_CORRELATION, STRONG_BIMODAL, HEAVY_TAILS, NEGATIVE_SLOPE, HIGH_SKEW |
| `testname` | Full test name: `BLOCK::TEST_PATTERN_CONDITIONS_MODULE` |
| `N_Valid` | Count of valid dies (both Vmin and UPM non-missing, Vmin in configured range) |
| `Intercept`, `Slope` | Linear fit parameters: `Vmin = Intercept + Slope × UPM` |

---

## Tool 4 — Deploy Dashboard (`deploy_dashboard.py`)

GUI to protect Python source files and deploy to a network share.

```powershell
python deploy_dashboard.py
```

**Protection options:**

| Method | Speed | Strength | Notes |
|---|---|---|---|
| Cython | Fast (~10-30s) | Native .pyd binary | Recommended |
| PyArmor | Medium | Encrypted .py + runtime | Requires license for large files |
| Nuitka | Very slow | Strongest native binary | Free |

Non-`.py` files (`.jsl`, `.json`, `.txt`, `.csv`) are always copied as-is.

Skipped automatically: `.log`, `.pyc`, `.pyd`, `.so`, `.c`, `.spec`, `__pycache__`, `.git`, `build`, `dist`, `.venv`.

---

## Collateral (`collateral/`)

Reticle mapping CSVs matched by the first 6 characters of the `DevRevStep_*` column value.

| File | Product prefix |
|---|---|
| `8PF5CV-NVL-816-BLLC-Reticle_Mappling.csv` | `8PF5CV` |
| `8PF6CV-NVL816-Reticle_Mapping.csv` | `8PF6CV` |
| `8PY6CV-NVL48-Reticle_Mapping.csv` | `8PY6CV` |

`apply_reticle_mapping.py` resolves the correct file automatically from `collateral/reticle/`.

### Lot-Definition CSVs (`collateral/material/`)

| File | Product |
|---|---|
| `8PF5CV-NVL816-BLLC_L0_lot_definition_r1.csv` | NVL816-BLLC |
| `8PF6CV-NVL816_P0_lot_definitions_r1.csv` | NVL816 P0 |
| `8PF6CV-NVL816_R0_lot_definitions_r1.csv` | NVL816 R0 |

`add_material_type.py` tries **all** candidate files that match the 6-char prefix and uses
the first one where `INTEL_LOT7` values intersect with the yield CSV's lot values.
Key columns merged: `INTEL_LOT7`, `WaferID`, `TSMC_LOT`, `Material Type`, `Material Type, Skew, BEOL Skew`, `Production Lot`.

---

## Multi-Product (Mixed-CSV) Merge Notes

When the pipeline merges a primary CSV (e.g. 8PF5CV, 4-reticle) with one or
more extra CSVs (e.g. 8PF6CV, 6-reticle), all merged files must share a common
lot column. **Always use `SORT_LOT`** — it is present in every CSV after merge.
`LOTFROMFS` is only present in the primary CSV and must never be used as the lot key.

### ZIP support in the Data CSVs listbox

Both the **primary** CSV (item 0) and any **extra** CSVs (items 1..n) in the listbox
may be `.zip` files. The pipeline extracts them identically:
- All `.csv` members inside the zip are extracted to a temp directory.
- Multiple CSVs in one zip are concatenated before use.
- Implemented via the `_load_extra_path(ep)` helper in `_pipeline_runner.py`.

ZIPs encoded as `archive.zip::member` (the `_ZIP_SEP = "::"` convention from the
etest CSV path system) are **not** used here — these are always plain
`path/to/file.zip` paths.

### Lot column priority

All lot-column detection in the pipeline follows this priority order:
1. Exact match: `SORT_LOT`
2. Exact match: `lot` (case-insensitive)
3. Any column containing `lot` but not `slot` (case-insensitive)

Files where this pattern is applied:
- `bin_distribution_html.py` — BinDistribution row grouping
- `generate_heatmap_from_csv.py` — IBIN wafer map file naming (2 locations)
- `sicc_processor.py` — SICC/CDYN/UPM per-wafer grouping
- `_pipeline_html.py` — `_lot_col_wmf` (WM_FILES link map), `_lot_wm` (wafermap.html filter rows), `_lot_col_fp` (fail pareto), `_lot_col` (scatter), `_lot_col_upm` (UPM section)
- `parametric_runner.py` — lot list extraction from merged CSV (full read, no nrows cap)

Reticle files are matched per-product via the `DevRevStep_*` column prefix (6 chars):
- `8PF5CV` → 4-reticle map (`8PF5CV-NVL-816-BLLC-Reticle_Mappling.csv`)
- `8PF6CV` → 6-reticle map (`8PF6CV-NVL816-Reticle_Mapping.csv`)

---

## Common Pitfalls (All Projects)

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard Excel update fails | File open in Excel | Close Excel, re-run |
| Heatmaps not generated | Missing `Sort_X` / `Sort_Y` in CSV | Check column names |
| SICC targets CSV is empty | `"target"` key used instead of `"target_A"` | Use `"target_A"` in Product Config JSON |
| Stale Bin Fail Summary | Old `PARETO_INJECT_START/END` sentinel in BinDistribution.html | Delete HTML file, re-run |
| Material type not merged | 6-char LOT7 prefix not in any `collateral/material/` file | Add correct lot-definition CSV |
| `_x`/`_y` duplicate columns after merge | Stale all-NaN material columns in existing CSV | `add_material_type.py` drops them automatically before merge |
| Wrong material file selected | Multiple candidate CSVs for same prefix | Module tries all candidates and uses first with matching INTEL_LOT7 values |
| Wrong TP detected (`Can't find TP, defaulting to '??'`) | `DevRevStep_*` column value not in any product's `DEVREVSTEP` list in `get_dd_update.py` | Add the exact value (e.g. `8PF6CVER`) to the correct product's list |
| Mixed-product merge shows only primary-CSV wafers (SICC, BinDistribution, wafermap filter) | `LOTFROMFS` picked as lot column (only present in primary CSV); secondary-product rows have NaN → dropped by groupby | Fixed: all lot-col detection now prefers `SORT_LOT` (present in all merged CSVs) |
| IBIN wafermap files named `UNKNOWN` for secondary-product lots | Same `LOTFROMFS` bug in `generate_heatmap_from_csv.py` lot detection | Fixed: `SORT_LOT`-first priority in both lot_col locations |
| FB popup blocks page interaction | Old modal had dark backdrop + blocking overlay | Now floating/draggable: overlay `background:transparent;pointer-events:none`; modal `pointer-events:auto` |
| UPM heatmap all one color | Values compressed into narrow spec% range | Color scales to actual data range (min→max); tooltip shows both data% and spec% |
| UPM tooltip not visible | `z-index` below overlay; or SVG inline `onmouseover` stripped by innerHTML | Tooltip `z-index:30000`; uses delegated `mousemove` on `upm-body` div via `_setupUpmBodyHover()` |
| UPM heatmap doesn't react to FB/HW selection | Render not triggered on filter change | `fbCbChange`, `selectAllFbs`, `clearFbs`, `bhHwChk`, `bhHwSelAll`, `bhHwClrAll` all call `_renderUpmMaps()` when `_upmOpen` |
| UPM dies all dimmed | IB type mismatch: bar click passes string `'26'`, die stores int `26` — strict `===` always fails | Fixed: use `String(ib)===String(_fbModalIb)` throughout |
| FB popup wafer tile jump broken | Column filter active shifts row indices | `fbTileClick` clears all dropdown filters before navigating |
| IB legend click does nothing useful | Old behavior: click opened FB modal directly | Now: click isolates IB in histogram; Ctrl+click opens FB modal |
| IB legend search not filtering | `lgSearch()` not exposed on IC object | Ensure `lgSearch:lgSearch` is in the IC return map |
| FB popup can't be resized | Old modal used `overflow-y:auto` on outer div | New: `.fb-modal` has `resize:both;overflow:hidden`; content in `.fb-modal-inner` scrolls |
| IBIN composite resize changes width only | Old `.h-resize-handle` bar only dragged width | Replaced with `.comp-corner-resize` corner handle; SVG `width:100%+viewBox` auto-scales height |
| IBIN HW Breakdown drag not working | Drag IIFE ran before modal HTML existed in DOM | Wrapped in `document.addEventListener('DOMContentLoaded',...)` |
| IBIN HW Breakdown shows wrong die counts when wafer selected | Composite overview SVG (all wafers) double-counted when `_wmSel` active | Added `wmSel.size===0` guard in `ibinHwRenderList` and `ibinHwApply` |
| IBIN HW Breakdown blocks wafer map interaction | Modal overlay had dark backdrop + `pointer-events` covering page | Overlay uses `background:transparent;pointer-events:none`; box has `pointer-events:auto` |
| JMP Project button does nothing | GUI not running (opener server offline) | Keep GUI open while using HTML buttons |
| Bindef parse skipped | Existing bindef CSV already has `DB\d+` entries | Delete bindef CSV to force re-parse |
| Vmin JMP not found | JMP not installed in default path | Set `--jmp-exe` flag or install JMP |
| Portable file too large | Many large PNGs | Normal — all images are base64-encoded inline |
| Parametric runner misses lots (R0 not found) | Old code used `nrows=5000` on merged CSV; R0 rows appear after row 5000 | Fixed: `parametric_runner.py` reads only lot column with `usecols=[lot_col]`, no row cap |
| Extra CSV ZIP not extracted | Extra CSVs used to call `read_csv_smart` directly on `.zip` path | Fixed: `_load_extra_path()` helper in `_pipeline_runner.py` extracts all CSVs from zip |

---

## Quick-Start Commands

```powershell
# Unified GUI — recommended entry point
python dashboard.py

# Yield pipeline CLI
python yield_dashboard/src/yield_pipeline.py --input run_config.json

# BinDistribution chart only
python yield_dashboard/src/bin_distribution_html.py "C:\data\yield.CSV" "" "C:\configs\product_config.json"

# Heatmaps only
python yield_dashboard/src/generate_heatmap_from_csv.py "C:\data\yield.CSV" "" "C:\configs\product_config.json" --gui --html-only

# Custom plots only
python yield_dashboard/src/generate_plots_from_csv.py "C:\data\yield.CSV" "C:\configs\analysis_parameters.json"

# Multi-run comparison
python yield_dashboard/src/compare_runs.py "M:\dashboard\Dashboard.html"

# Make portable
python yield_dashboard/src/make_portable_dashboard.py "M:\dashboard\Dashboard.html"

# SICC/UPM standalone (JMP-based)
python sicc_cdyn_upm/src/run_dashboard.py

# SICC/CDYN standalone GUI (pure Python, no JMP)
python sicc_cdyn_upm/src/run_py_dashboard.py

# Vmin standalone
python vmin/src/run_vmin.py --headless --data-file "C:\data\input.jmp" --output-dir "C:\data\out"

# Install dependencies (Intel proxy)
$env:HTTPS_PROXY = "http://proxy-us.intel.com:911"
python -m pip install --user -r yield_dashboard/requirements.txt
```
