---
applyTo: "**"
---

# Yield Dashboard — Copilot Agent Instructions

## Project Purpose

Automates the full yield analysis pipeline for Intel silicon sort data. Produces:
- Per-bin wafer heatmaps (SVG/PNG)
- Flowchart of bin code paths
- Compare runs (diff two snapshot directories)
- Crystal Ball bin definitions from `.bindef` files
- Portable offline HTML bundle

Languages: **Python 3.9+**, HTML/CSS/JS. No JMP required.

---

## Entry Points

| Command | Purpose |
|---|---|
| `python run_dashboard.py` | Launch tkinter GUI dashboard |
| `python src/yield_pipeline.py --config config.json` | CLI pipeline run |
| `python src/_loader.py <module> [args]` | Subprocess dispatcher (supports `.py` and compiled `.pyd`) |

---

## Pipeline Flow

```
run_dashboard.py  (GUI)
    |
    +-- TabPipeline  --->  yield_pipeline.py
    |       |
    |       +-- parse_bindef_to_crystalball.py   (read .bindef -> bin map)
    |       +-- apply_reticle_mapping.py          (CSV reticle -> wafer coords)
    |       +-- bin_distribution_html.py          (BinDistribution HTML per lot)
    |       +-- generate_bin_wafer_heatmaps.py    (SVG heatmaps)
    |       +-- generate_heatmap_from_csv.py      (generic heatmap from CSV)
    |       +-- generate_flowchart.py             (bin path flowchart SVG)
    |       +-- generate_plots_from_csv.py        (summary bar/scatter plots)
    |       +-- get_dd_update.py                  (pull latest data from DD)
    |
    +-- TabCompare   --->  compare_runs.py / compareTP.py
    |
    +-- TabManage    --->  manage_dashboard.py
    |
    +-- TabWaferMap  --->  generate_bin_wafer_heatmaps.py (direct)
    |
    +-- TabVmin      --->  (calls vmin/src/run_vmin.py via subprocess)
    |
    +-- TabPortable  --->  make_portable_dashboard.py

NOTE: `_update_dashboard_html` in `_pipeline_html.py` creates run-blocks in
Dashboard.html with only a "Dashboard Yield Report" link. The SICC/UPM link
is NOT added to Dashboard.html run-blocks — that slot is reserved for the
SICC/UPM JSL pipeline (sicc_cdyn_upm). The yield report sidebar (index.html)
still shows the SICC / CDYN / UPM section with plot.html and analysis links.
```

---

## Configuration — `config.json`

```json
{
  "product": "NVL816",
  "lot_id": "LT123456",
  "input_dir": "C:/data/sort",
  "output_dir": "C:/data/output",
  "bindef_path": "C:/data/prod.bindef",
  "reticle_csv": "collateral/8PF6CV-NVL816-Reticle_Mapping.csv",
  "run_steps": ["parse_bindef", "apply_reticle", "heatmaps", "flowchart", "plots"],
  "dpw": 619
}
```

| Key | Required | Description |
|---|---|---|
| `product` | Yes | Product code — used to select reticle CSV and DPW |
| `lot_id` | Yes | Lot identifier for output folder naming |
| `input_dir` | Yes | Folder containing raw sort CSV files |
| `output_dir` | Yes | Destination for all generated outputs |
| `bindef_path` | No | Path to `.bindef` file for Crystal Ball bin map |
| `reticle_csv` | No | Override reticle mapping CSV path |
| `run_steps` | No | List of steps to run; omit to run all |
| `dpw` | No | Dies per wafer (default: auto from product) |

---

## Source File Responsibilities

| File | Role |
|---|---|
| `run_dashboard.py` | tkinter GUI; tab orchestration; background thread dispatch |
| `src/yield_pipeline.py` | CLI pipeline runner; calls each step module in order |
| `src/_loader.py` | Subprocess dispatcher — resolves `.py` vs compiled `.pyd` |
| `src/pipeline.py` | Thin orchestrator: `PipelineFrame` inherits from 4 mixins; `PipelineGUI`; `__main__` CLI |
| `src/_pipeline_constants.py` | Shared module-level constants (`_SRC_DIR`, `_LOADER`, `SICC_*_SCRIPT`, etc.) |
| `src/_pipeline_server.py` | `OpenerServerMixin`: HTTP file-opener server on port 56947 |
| `src/_pipeline_ui.py` | `PipelineUIMixin`: `_build_ui`, `load_json`, all `_browse_*` dialogs |
| `src/_pipeline_runner.py` | `PipelineRunnerMixin`: `run_pipeline`, `_run_sicc_upm_headless`, `open_report`, `save_json`, bin-image helpers |
| `src/_pipeline_html.py` | `PipelineHtmlMixin`: `_build_pareto_html`, `_build_master_html`, `_update_dashboard_html` |
| `src/parse_bindef_to_crystalball.py` | Parse `.bindef` -> Crystal Ball bin definition CSV |
| `src/apply_reticle_mapping.py` | Map reticle coordinates from CSV to wafer die positions |
| `src/bin_distribution_html.py` | BinDistribution HTML (lot/wafer/material filter) |
| `src/generate_bin_wafer_heatmaps.py` | Generate SVG wafer heatmaps per bin code |
| `src/generate_heatmap_from_csv.py` | Generic 2D heatmap from any XY CSV |
| `src/generate_flowchart.py` | Graphviz-style bin path flowchart -> SVG |
| `src/generate_plots_from_csv.py` | Summary bar and scatter plots via matplotlib |
| `src/get_dd_update.py` | Pull latest sort data from DD; product-specific configs |
| `src/compare_runs.py` | Diff two output snapshot directories; produce diff report |
| `src/compareTP.py` | Test program comparison utility |
| `src/manage_dashboard.py` | Archive, clean, and manage output directories |
| `src/make_portable_dashboard.py` | Bundle HTML + assets into a self-contained ZIP |
| `src/csv_utils.py` | Robust CSV reader: encoding detection + chunked iteration |

---

## pipeline.py — Mixin Architecture

`pipeline.py` was split from a 3817-line monolith into focused modules using Python mixin inheritance. `pipeline.py` is now a thin orchestrator (~142 lines).

### Class hierarchy

```python
class PipelineFrame(
    OpenerServerMixin,   # _pipeline_server.py
    PipelineUIMixin,     # _pipeline_ui.py
    PipelineRunnerMixin, # _pipeline_runner.py
    PipelineHtmlMixin,   # _pipeline_html.py
    tk.Frame,
):
    def __init__(self, parent, **kwargs): ...
```

### Mixin file responsibilities

| File | Class | Key methods |
|---|---|---|
| `_pipeline_constants.py` | (module) | `_SRC_DIR`, `_ROOT_DIR`, `_FROZEN`, `_LOADER`, `SICC_UPM_SCRIPT`, `SICC_CDYN_UPM_SCRIPT` |
| `_pipeline_server.py` | `OpenerServerMixin` | `_poll_open_queue`, `_start_opener_server` |
| `_pipeline_ui.py` | `PipelineUIMixin` | `_build_ui`, `_on_sicc_toggle`, `load_json`, `_populate_fields`, all `_browse_*` |
| `_pipeline_runner.py` | `PipelineRunnerMixin` | `run_pipeline`, `open_dashboard_folder`, `_resolve_dashboard_path`, `_find_bin_image`, `_ensure_placeholder_png`, `open_bin_image`, `_run_sicc_upm_headless`, `open_report`, `save_json` |
| `_pipeline_html.py` | `PipelineHtmlMixin` | `_build_pareto_html` (FBIN + IBIN horizontal bar charts), `_build_master_html` (sidebar+iframe layout; collapsible sidebar with `#sb-toggle` arrow button; collapses to 30px via `.sb-collapsed` class), `_update_dashboard_html` (run-block: exact `data-stem` matching by block_key; Yield Report link only; SICC/UPM link removed — reserved for JSL run) |

### Rules when editing

- **Never add new methods directly to `pipeline.py`** — add to the appropriate mixin.
- Each mixin imports constants via `from _pipeline_constants import ...` at the top.
- `self` inside a mixin refers to `PipelineFrame` at runtime — all mixin methods share the same `self` and can call each other's methods freely.
- MRO resolution: `OpenerServerMixin` → `PipelineUIMixin` → `PipelineRunnerMixin` → `PipelineHtmlMixin` → `tk.Frame`.
- `dashboard.py` imports `from pipeline import PipelineFrame` — this is unchanged.
- `pipeline.py.bak` is the original 3817-line backup — keep it until the split is fully validated.

---

## CSV Utilities — `csv_utils.py`

All CSV reading in this project must go through `csv_utils.py`. Never use `pd.read_csv()` directly.

| Function | Purpose |
|---|---|
| `detect_encoding(path)` | Tries `utf-8-sig` -> `utf-8` -> `utf-16` -> `latin-1` |
| `sniff_columns(path)` | Returns column names without reading full file |
| `read_csv_smart(path)` | Returns full DataFrame with correct encoding |
| `iter_chunks(path, chunksize)` | Yields chunks; default 100k rows; override with `CSV_CHUNK_SIZE` env var |

---

## Products Supported by `get_dd_update.py`

| Product | Lot Prefix | DPW |
|---|---|---|
| ARL68-N3B | 8PYJCVJ | 797 |
| ARLS816 | 8PYVCVB / 8PYVCVAB | 516 |
| NVL48 | 8PY6CVT | 1200 |
| NVL816 | 8PF6CVP / 8PF6CVR | 619 |
| NVL816-BLLC | 8PF5CVL | 393 |

Reticle mapping CSVs are in `../collateral/`.

---

## Subprocess Dispatch — `_loader.py`

`_loader.py` is the single dispatcher for all pipeline steps invoked via subprocess.

```bash
python _loader.py <module_name> [args...]
```

- Resolves `<module_name>.py` first; falls back to compiled `<module_name>.pyd`
- Used by `run_dashboard.py` to run each pipeline step in a separate process
- All output is captured and forwarded to the GUI progress queue
- Do not call pipeline modules directly with `subprocess`; always go through `_loader.py`

---

## GUI Architecture

- **Framework:** tkinter, dark theme (`#1a252f` background)
- **Threading:** Long-running pipeline steps run in a `threading.Thread`; progress updates posted to `queue.Queue` and polled with `root.after()`
- **Tabs:** Pipeline | Compare | Manage | Wafer Map | Vmin | Portable
- **Local HTTP server:** Random port; handles `/open?path=` and `/folder?path=` for HTML button actions

---

## HTML Dashboard Conventions

- Dark theme: `background: #1a252f`, panel: `#2c3e50`, accent: `#1f618d`
- Wafer map SVGs embedded inline or referenced via relative paths
- PNG images cache-busted with `?t=<unix_timestamp>`
- Open-file buttons use `fetch('http://127.0.0.1:PORT/open?path=...')` — never `href="..."` (avoids download dialog)
- Portable bundle: all assets inlined as base64 or data URIs; no external dependencies

---

## Common Pitfalls

| Issue | Root Cause | Fix |
|---|---|---|
| Dashboard.html block erased when using similar identifier | Old `_any_re` regex used substring match on `data-stem` | Fixed: now uses exact `block_key` match only — same identifier replaces, different identifier adds |
| Custom Dashboard.html filename ignored (always writes to Dashboard.html) | `_resolve_dashboard_path` only returned absolute paths when file already existed; fuzzy fallback found existing Dashboard.html | Fixed: absolute paths ending in `.html`/`.htm` are now returned directly even if file doesn't exist yet |
| Pareto chart bars too spread out with many bins | `figsize` height factor too large, explicit bar height missing | Fixed: `n * 0.32` height factor, `height=0.95` for tight bars, `labelpad=10` on x-axis |
| `_loader.py` can't find module | Wrong working directory | Always run from `src/` or pass absolute paths |
| Heatmap is blank / all one color | Missing reticle CSV or wrong DPW | Check `reticle_csv` in config and DPW matches product |
| Flowchart SVG not generated | `graphviz` not installed | `pip install graphviz` and install Graphviz binaries |
| `get_dd_update.py` times out | Intel proxy not configured | Set `http_proxy=http://proxy-us.intel.com:911` |
| Portable bundle missing images | PNG paths are absolute | Use `make_portable_dashboard.py` to re-bundle |
| Compare tab shows no diff | Output dirs not snapshots | Each run must be in its own subdirectory |
| Vmin tab not launching | `vmin/src/run_vmin.py` path wrong | Check relative path from `run_dashboard.py` to vmin module |

---

## Intel Proxy

```
http://proxy-us.intel.com:911
```

Set in environment or pass to `requests.get(..., proxies={"http": ..., "https": ...})`.

---

## Digital Dashboard — `get_dd_update.py`

See [`digitaltracker.md`](digitaltracker.md) for full logic, fallback chain, and known limitations.

### Key rule — column 3 (Recovery Bins) requires LOGTRACKER

Column 3 of the output Excel ("Recovery Bins (3-4) (%)") shows per-module-type (ARR_ATOM, FUN_ATOM, SCN_ATOM, ARR_CORE, FUN_CORE, SCN_CORE) counts of IB3/4 defeature die. This breakdown is **only possible when LOGTRACKER_AP/CR columns are present and non-null** in the data CSV.

For **NVL816-BLLC** (and any product where LOGTRACKER is absent), column 3 will be 0% for all module rows. This is expected and correct — the `SortBinCalculatorConfig` in the test program confirms that IB3/4 sub-bins (301–304, 401–403) encode only *repair type* (VminRepair, DefectRepair), **not** which ARR/FUN/SCN test caused the defeat. Without LOGTRACKER, the ARR/FUN/SCN attribution is unavailable.

**Do not attempt to infer ARR/FUN/SCN from bin numbers for IB3/4 bins.**

---

## Pass Pareto Panel — `_build_pareto_html` (`_pipeline_html.py`)

### Column layout (current)

| Col | Header | JS key | Sortable | Filterable |
|---|---|---|---|---|
| 0 | Functional Bin | `fb` | ✓ | ✓ (`pp-fb-0`) |
| 1 | Description | `desc` | ✓ | ✓ (`pp-fb-1`) |
| 2 | Total | `total` | ✓ | — |
| 3 | Count | `count` | ✓ | — |
| 4 | Pass % | `pct` | ✓ | — |
| 5 | Module | `mods` | ✓ | — |

"Pass Bucket" column was **removed** (2026-05-24). It was `bkt` at index 1. All subsequent indices shifted by −1.  
Default sort: **Functional Bin ascending** (`ppSort={col:0,dir:'asc'}`).

### Description source — `Pass-Bin-Map`

Add a `"Pass-Bin-Map"` key to the `yieldtarget_input*.json` config to provide human-readable descriptions for pass FBs:

```json
"Pass-Bin-Map": {
  "101": {"cat": "FF Yield", "desc": "FF - No Repair"},
  "198": {"cat": "FF Yield", "desc": "FF - Vmin Repair"},
  "301": {"cat": "DF Yield", "desc": "Atom Recovery Defeature(DF) - No Repair"},
  ...
}
```

Lookup order per FB: `Pass-Bin-Map[fb]["desc"]` → `_fb_bucket_desc(fb)[1]` (fallback if key absent or FB not in map).

### JS state (`rPP` / `ppGetFiltered`)

PP data rows contain only: `{fb, desc, total, count, pct, mods}`. `bkt` is **not** in the data. The `_ddVals` key array for `tbl==='pp'` is `['fb','desc','total','count']`.

---

## Bin Description Lookup — `_bin_map_cat` (`_pipeline_html.py`)

`_bin_map_cat(n_str)` returns `(cat, desc)` for any FB. Priority order (2026-05-24):

1. **Exact match in `bin_map`** — `bin_map[n_str]`
2. **IB-digit fallback in `bin_map`** — tries `bin_map[n_str[:2]]`, then `bin_map[n_str[:1]]`  
   e.g. FB `1742` → tries `"17"` → `{"cat":"STRESS","desc":"HVBI_STRESS"}` ✓
3. **`bindef_dict`** (from `Bin Description_` TRACE CSV column) — **only used if the value does not match `B\d{7,}_`** (leaf-bin description pattern)

### Why the leaf-bin filter matters

The `Bin Description_` column in TRACE CSV contains the *leaf bin* description (e.g. `B17420026_FAIL_SCN_ATOM_STUCKAT_ATOM_SB_K_VMAX_N_VATOM_NOM_LFM_OCC_2`), not an FB-level description. `bindef_dict` is keyed by FB (`drop_duplicates(subset=[fb_col])` takes first occurrence), so without filtering it would show the leaf bin name in the Description column of the Fail Pareto. The regex guard `B\d{7,}_` blocks any value whose first token is a 7+-digit data-bin-encoded number.

### Common Pitfalls

| Issue | Root Cause | Fix |
|---|---|---|
| Fail pareto Description shows `B17420026_FAIL_SCN_ATOM…` | `bindef_dict` keyed by FB but stores leaf bin desc | `_bin_map_cat` now filters values matching `B\d{7,}_` and uses IB-digit fallback |
| Pass pareto Description empty when `Pass-Bin-Map` absent | No fallback | `or _fb_bucket_desc(fb)[1]` provides fallback |
| Sort icon stuck after removing Pass Bucket column | `ppSort.col` pointed to old index 5 (Pass %) | Updated to `col:0` (Functional Bin); all filter/render indices shifted |

---



The HW Breakdown modal in the IBIN wafer map HTML mirrors the histogram (`bin_distribution_html.py`) in behaviour. Key design rules:

### State variables (JS, in `_IBIN_JS` block per lot)
| Variable | Purpose |
|---|---|
| `_ibinHwBin` | Currently open bin label; `null` when modal closed |
| `_ibinHwSel` | `Set` of selected `hwIdx` strings; **empty = show all**; `'__none__'` sentinel = show nothing |
| `_ibinHwColFilter` | Object mapping column name → filter text; cleared on modal close |
| `_ibinLot` | Lot label string injected at HTML gen time via `_json_fbdesc_ibin.dumps(str(lot_label))` |

### Column order
`["Lot","Wafer","Count","%"].concat(orderedCols)` — Lot and Wafer always first, matching histogram.

### `ibinHwChkChange(cb)` — checkbox toggle
Rebuilds `_ibinHwSel` from all checkboxes, adds `'__none__'` when all are unchecked, then calls `ibinHwRenderList()` + `ibinHwApply()`. **Do not** patch `tr.style.opacity` directly — always re-render.

### `ibinHwClrAll()` — "✗ None" button
Unchecks all, clears `_ibinHwSel`, adds `'__none__'` sentinel, re-renders.

### `ibinHwSelAll()` — "✓ All" button
Clears `_ibinHwSel` to empty (empty = show all), re-renders.

### `ibinHwApply()` — applies selection to wafer map SVG
`hwMatch = _ibinHwSel.size===0 || _ibinHwSel.has(hw)` — empty set highlights all; `'__none__'` never matches any real `hw` so nothing is highlighted.

### Duplicate-row guard
The fallback block counting dies into `_wHw[""]` (empty-wafer key) runs **only when `grandTotal===0`** after iterating wafer sections. This prevents duplicate rows when wafer-section data already exists.

### Wafer legend selection → HW breakdown sync
`wmSelectWafer` calls `_wmUpdateView()`. The HW refresh (`ibinHwRenderList(); ibinHwApply()`) is placed **before** the `if(_wmSel.size===0){…return;}` early exit so it fires on both select and deselect. `wmShowAll()` also calls the HW refresh.

### FB panel close → HW modal auto-close
`ibinFbClose()` calls `ibinHwClose()` after clearing the FB panel.

### Common Pitfalls — HW Breakdown
| Issue | Root Cause | Fix |
|---|---|---|
| "None" selects all / last uncheck selects all | `_ibinHwSel` empty = show all; no sentinel | Add `'__none__'` when all unchecked |
| Second+ toggle doesn't update wafer map | `ibinHwChkChange` patched DOM opacity instead of re-rendering | Always call `ibinHwRenderList()` before `ibinHwApply()` |
| Wafer legend click doesn't refresh HW breakdown | HW refresh after early return in `_wmUpdateView` | Move HW refresh before `if(_wmSel.size===0) return` |
| Duplicate blank-wafer row in table | Fallback `_wHw[""]` block ran unconditionally alongside wafer sections | Guard with `&&grandTotal===0` |
| Lot column missing | `entries.push` lacked `lot:_ibinLot` field | Add field; ensure `_ibinLot` injected in JS block |

---

## UPM Heatmap — Round Wafer Fix (`bin_distribution_html.py` → `_renderUpmMaps()`)

### Problem
With products that have fewer X or Y die columns (e.g., 4-reticle vs 6-reticle), the UPM heatmap per-wafer SVG rendered as an oval because cell height was always fixed at `cs=12px` regardless of coordinate span ratio.

### Fix (applied 2026-05-02)
`_renderUpmMaps()` now computes a **fixed canvas width** (`FIXED_W=200px`) and derives `cs` per-wafer from the X die count, then scales Y cell height (`csy`) by the X/Y span ratio:

```js
var FIXED_W = 200, pad = 2;
var xCnt = xMax - xMin + 1, yCnt = yMax - yMin + 1;
var cs = Math.max(1, (FIXED_W - pad*2) / xCnt);
var csy = (xSpan > 0 && ySpan > 0) ? (cs * xSpan / ySpan) : cs;
var W = FIXED_W, H = Math.round(yCnt * csy + pad*2);
// die rect: width=cs*0.92, height=csy*0.92 (proportional gap)
```

**Result**: All wafers render at the same ~200×200px canvas and appear round regardless of reticle/die-count difference between products.

### Key rules
- `FIXED_W` normalises all wafers to the same physical size — do **not** use `(xMax-xMin+1)*cs` for W
- `csy = cs * xSpan/ySpan` mirrors the Python ibin fix (`_die_dy = _xr/_yr`)
- Die gap uses `cs*0.92` / `csy*0.92` (proportional), not a fixed 1px

---

## IBIN Wafer Map Composite — Layout & Spacing (`generate_heatmap_from_csv.py`)

### Title / spacing controls
| Parameter | Location | Current value | Purpose |
|---|---|---|---|
| `title_in` | `generate_heatmap_from_csv.py` | `0.009"` | Inches reserved for suptitle; controls space above composite plot |
| `suptitle fontsize` | `fig_comp.suptitle(...)` | `5` | Title font size (pt) |
| `suptitle y` | `fig_comp.suptitle(...)` | `1.005` | Vertical anchor (slightly above axes top) |

### Wafer Summary header row fonts
All three elements use inline `font-size` (updated 2026-05-02):
- **"Wafer Summary" label**: `20px`
- **"◻ Show All" button**: `17px`
- **hint text** `(click to select…)`: `17px`

### Wafer Summary table font
CSS class `.wm-sum-tbl{font-size:17px}` applied to the summary `<table>` — all other tables use the base `11px`. Do **not** change the global `table{font-size}` rule.

### Margin between composite and Wafer Summary header
`margin:4px 0 2px` on the header `<div>` — keep minimal.