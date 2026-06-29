---
applyTo: "**"
---

# SICC / CDYN / UPM — Python Dashboard (`sicc_cdyn_upm/`)

## Purpose
Pure-Python replacement for the JSL-based `sicc_cdyn_upm` pipeline.
**No JMP required.** Reads sort-data CSV directly, processes it, and
generates a self-contained interactive HTML dashboard.

The yield pipeline (`yield_dashboard`) calls this automatically via
`_run_sicc_py_headless()` in `_pipeline_runner.py`. The output
`{stem}_sicc_analysis.html` appears in the yield report sidebar under
"SICC / CDYN / UPM" → "SICC/CDYN Report".

---

## Entry Points

| Method | Command |
|---|---|
| Standalone GUI | `python src/run_py_dashboard.py` |
| From JSL launcher | `python ../sicc_cdyn_upm/src/run_dashboard.py` → select "Python (New)" engine |
| Programmatic | `from run_py_dashboard import run_python_pipeline` |
| Called by yield pipeline | `_pipeline_runner.py → _run_sicc_py_headless()` |

```powershell
python src/run_py_dashboard.py
```

---

## Source Files

| File | Role |
|---|---|
| `src/run_py_dashboard.py` | Tkinter GUI launcher; `run_python_pipeline()` for headless use; background thread dispatch |
| `src/sicc_processor.py` | CSV processor: column rename, sum columns, UPM%, per-group medians, CDYN detection |
| `src/generate_dashboard_html.py` | **Thin assembler** — public API `generate_html(data, output_path, title)`. Imports from modules below |
| `src/_dash_frame.py` | CSS + page skeleton HTML (sidebar, tabs bar); tab bar has a **SICC/CDYN SPEC** hyperlink appended after the tab buttons |
| `src/_dash_js_shared.py` | Shared JavaScript: tab registry, utils, category colors, sidebar, chart helpers, resize |
| `src/_tab_registry.py` | `Tab` dataclass + `TABS` list — **edit here to add/remove tabs** |
| `src/_tab_sicc.py` | SICC tab: HTML panel + `render_sicc`, `render_upm_dist` JavaScript |
| `src/_tab_cdyn.py` | CDYN tab: HTML panel + `render_cdyn`, `render_cdyn_dist`, `selCol`, `selCdyn` JavaScript |
| `src/_tab_summ.py` | All Medians tab: HTML panel + `render_summ`, `_renderCatTable` JavaScript |
| `src/_tab_charts.py` | Charts tab: HTML panel + `renderHist`, `buildPills`, `drawScatter`, `drawPareto` JavaScript |

---

## How to Add a New Tab (e.g. VMin)

**Step 1** — Create `src/_tab_vmin.py`:
```python
from _tab_registry import Tab

TAB_ID     = 'tab-vmin'
TAB_LABEL  = 'VMin'
TAB_ACTIVE = False

def tab_html() -> str:
    return '''
<div id="tab-vmin" class="tab-panel">
  <div id="vmin-content">No data yet.</div>
</div>'''

def tab_js() -> str:
    return '''
function render_vmin() {
  var el = document.getElementById('vmin-content');
  if (!el) return;
  // ... render logic using VMIN_ROWS, etc. ...
}
registerTab('tab-vmin', render_vmin);
'''

build_tab = Tab(
    tab_id=TAB_ID, label=TAB_LABEL, active=TAB_ACTIVE,
    html_fn=tab_html, js_fn=tab_js,
)
```

**Step 2** — Register in `src/_tab_registry.py`:
```python
from _tab_vmin import build_tab as TAB_VMIN
TABS.append(TAB_VMIN)
```

**Step 3** — Add data in `src/sicc_processor.py` → `process_csv()` return dict:
```python
return { ..., 'vmin_rows': vmin_data }
```

**Step 4** — Inject the data in `src/generate_dashboard_html.py` → `generate_html()`:
```python
vmin_data     = data.get('vmin_rows', [])
vmin_json     = _esc_json(vmin_data)
# In data_js block:
data_js += f'var VMIN_ROWS={vmin_json};\n'
```

**No other files need editing.** The tabs bar button, panel, and JS registration happen automatically.

---

## Processing Pipeline (`sicc_processor.process_csv`)

```
Step 1: Rename SICC columns    — match renameList patterns (ordered-token wildcard)
Step 2: Compute sum columns    — resolve TotalList / siccTotalList (iterative for derived sums)
Step 3: UPM% columns           — columnConfigs: (src_pattern ÷ divisor) × 100
Step 4: CDYN columns           — cdynList patterns or auto-detect (*cdyn* / *_og_*_v1_*)
Step 5: Identify group cols    — Program, Lot, Wafer, Material, X, Y
Step 6: Numeric conversion     — all analysis + CDYN columns → float
Step 7: Load SICC targets      — priority: override_targets > config JSON > target CSV
Step 8: Per-wafer medians      — group by (Program, Lot, Wafer); compute median + histogram per column
```

Auto-detect fallback: if renameList matches nothing, scans all numeric CSV columns
and splits them into SICC / UPM / CDYN based on column name keywords.

---

## Config File

Reads column-rename rules from `testlist.jsl` (JSL parsed) or `testlist.json`.

**Resolution order:**
1. User-specified path (GUI or `config_path` argument)
2. `collateral/sicc_cdyn_testlist.json` (preferred)
3. `sicc_cdyn_upm/src/testlist.jsl` (fallback)

### JSON format (preferred)

```json
{
  "siccList":      [["PTH_POWER*SICC_ALL*500*V2*VCCCORE0*", "SICC CORE0 0.95"], ...],
  "siccTotalList": [["SICC FULLCHIP", "SICC CORE0 0.95", "SICC ATOM 0.95", "SICC RING 0.95"], ...],
  "columnConfigs": [["UPM ULVT 0107 950mV (%)", "UPM_0107_*FULLDIE_0950_*", 9154], ...],
  "cdynList":      [["PTH_POWER*OG_128B*CDYN_ATOM0*", "OG_128B_CDYN_ATOM0"], ...]
}
```

If `cdynList` is omitted, CDYN columns are auto-detected (any column
containing `cdyn` or matching `*_OG_*_V1_*`).

### Target sources (priority order)

| Priority | Source | Keys |
|---|---|---|
| 1 (highest) | Product Config JSON (`product_config_path`) | `sicc_targets` → `target_A`, `upm_target` → `target_%`, `cdyn_targets` → `target_nF` |
| 2 | Config JSON (`sicc_targets`, `upm_targets`, `cdyn_targets` dicts) | name → value |
| 3 (lowest) | Separate target CSV (`target_csv` argument) | `TestName`, `Target` columns |

---

## Dashboard HTML Features

- **Four tabs:** SICC/UPM (heatmap matrix) · CDYN (heatmap matrix) · All Medians (category-grouped summary) · Charts (histogram + pareto + scatter)
- **Filter by Program / Lot / Wafer / Material** — Excel-style dropdown checkboxes
- **Filter by Lot/Wafer selection table** — checkbox rows; selecting updates charts
- **SICC tab dist-side panel** (right side when row clicked):
  - XY Scatter plot (top) — die-level UPM vs selected SICC column with median lines, linear fit + R²
  - Distribution histogram — raw die values with UPM overlay (orange dots)
  - Mini UPM distribution — compact orange histogram of paired UPM values
- **CDYN tab dist-side panel** (same layout as SICC):
  - XY Scatter plot (top) — die-level UPM vs selected CDYN column
  - Distribution histogram — raw die values with UPM overlay
  - Mini UPM distribution
- **Charts tab:**
  - Row 1: XY Scatter (55%) + Mini UPM distribution (40%)
  - Row 2: Combined distribution histogram + Pareto per-wafer bar chart
  - Distribution charts use compact `aspect-ratio:1/0.45` (less whitespace)
  - Column selector pills for all SICC/CDYN columns
  - Pareto UPM overlay: dynamic Y-axis with diamond markers per group
- **Distribution histogram:**
  - Prefers raw `die_pairs.s` values over histogram midpoint approximation (avoids outlier-induced bimodal artifacts)
  - *Single wafer* → raw die distribution for that wafer
  - *Multiple wafers* → combined die-level histogram from all selected wafers
  - UPM overlay uses **dynamic Y-axis** (auto-scaled to actual data range with 10% padding) so markers spread across chart height
  - UPM Med label aligned with right Y-axis (not floating on top)
- **Scatter plot features:**
  - 5σ outlier filtering on both axes
  - 5% axis buffer so points don't sit on edges
  - Median lines (orange vertical for UPM, brown horizontal for Y)
  - Linear regression fit line (red) with equation and R²
  - Conditional formatting: values > 0 → 1 decimal place, else 3 decimal places
  - Nice tick calculator for readable axis labels
- **Target line** on histogram (green dashed) if SICC target supplied
- **Median line** (brown dashed)
- **Heatmap colour coding:** red = median > target, yellow = within margin, green = well below
- **All Medians tab** — full sortable table with red highlight when median > target
- **CDYN tab** — median vs target table for all CDYN tests
- 100% self-contained HTML (no server, no external dependencies)

---

## Output

```
<dashboard_dir>/{csv_stem}_sicc_analysis.html
```

Single self-contained HTML file. All data embedded inline as JSON.

---

## Key Variables (`process_csv` output dict)

| Key | Type | Description |
|---|---|---|
| `rows` | list of dicts | One entry per program×lot×wafer group |
| `rows[i].program` | str | Test program name |
| `rows[i].lot` | str | Lot ID |
| `rows[i].wafer` | str | Wafer ID |
| `rows[i].material` | str | Material ID |
| `rows[i].total` | int | Die count in group |
| `rows[i].medians` | dict | `{col_name: median_float}` for SICC + UPM columns |
| `rows[i].hists` | dict | `{col_name: {edges: [...], counts: [...]}}` |
| `rows[i].cdyn` | dict | `{cdyn_col: median_float}` |
| `rows[i].die_pairs` | dict | `{col_name: {s: [floats], u: [floats]}}` — paired SICC/CDYN + UPM die values for scatter plots |
| `sicc_columns` | list | Renamed + sum SICC column names |
| `upm_columns` | list | UPM % column names |
| `cdyn_columns` | list | CDYN column names |
| `targets` | dict | SICC/UPM targets keyed by upper-case column name |
| `cdyn_targets` | dict | CDYN targets keyed by column name |
| `csv_name` | str | Original CSV filename |
| `group_cols` | dict | `{program, lot, wafer, material, x, y}` → resolved column names or None |

---

## `run_python_pipeline()` Parameters

| Parameter | Required | Description |
|---|---|---|
| `csv_path` | Yes | Path to the sort data CSV |
| `config_path` | No | Path to testlist.jsl or testlist.json (empty = use default) |
| `target_csv` | No | Path to SICC target CSV (legacy; overridden by product config) |
| `output_dir` | Yes | Folder to write intermediate output files |
| `dashboard_dir` | Yes | Folder to write the main dashboard HTML |
| `status_cb` | Yes | `callable(str)` — progress messages |
| `done_cb` | Yes | `callable(str)` — called with dashboard HTML path on success |
| `error_cb` | Yes | `callable(str)` — called with error message on failure |
| `product_config_path` | No | Path to Product Config JSON (sicc_targets / upm_target / cdyn_targets) |

---

## Wildcard Matching

`_ordered_like(text, pattern)` — all tokens in pattern (split on `*`) must appear
inside text in order (case-insensitive). Mirrors JMP's `OrderedLike()` function.

Example: `PTH_POWER*SICC_ALL*500*V2*VCCCORE0*` matches
`PTH_POWER_DFX_SICC_ALL_500MHZ_V2_VCCCORE0_PC_0950`.

---

## Loader Integration

The yield pipeline (`yield_dashboard/src/_pipeline_runner.py`) invokes this module in two ways:

1. **Direct import** (`_run_sicc_py_headless`): Adds `sicc_cdyn_upm/src` to `sys.path`, then imports `sicc_processor.process_csv` and `generate_dashboard_html.generate_html` directly. This is the primary path used during pipeline execution.

2. **Loader dispatch** (`yield_dashboard/src/_loader.py`): The loader adds all sibling `*/src` directories to `sys.path` via glob, making `sicc_processor` and `generate_dashboard_html` importable by name. The standalone GUI entry point `run_py_dashboard` has a `main()` function compatible with loader dispatch.

**Path resolution:** `_pipeline_runner.py` computes:
```python
_sicc_py_src = Path(_SRC_DIR).parent.parent / 'sicc_cdyn_upm' / 'src'
```
This resolves to `dashboard/sicc_cdyn_upm/src/` relative to the yield_dashboard source directory.

**Product config merging:** The pipeline runner reads `sicc_targets`, `cdyn_targets`, `SiccTableConfig`, `cdynTableConfig`, and `upmInfo` from the product config JSON (fail bucket path) and merges them into the config dict before calling `process_csv`.

---

## Common Pitfalls

- **`medians` must be scalar floats:** Each `rows[i].medians[col]` is a single `float` (the per-wafer median). Never store arrays here — use `die_pairs` for die-level values.
- **`die_pairs` structure:** `{col: {s: [float,...], u: [float,...]}}` where `s` = SICC/CDYN die values, `u` = paired UPM die values. Only populated when the column has a UPM partner in `SiccTableConfig` or `cdynTableConfig`.
- **Config not found:** if `testlist.jsl` is missing, columns are auto-detected.
  Provide a `testlist.json` or the original `testlist.jsl` for accurate renaming.
- **CDYN columns not detected:** add a `cdynList` to your JSON config with explicit
  patterns matching the raw column names.
- **Large CSV:** `process_csv` keeps raw die values in `die_pairs` and pre-binned histograms per wafer. Output HTML size scales with wafer×column×die count.
- **Target matching:** targets are looked up by upper-cased column name. The CSV
  columns must be `TestName` and `Target` (or similar).
- **Sum columns with dependencies:** `siccTotalList` entries that reference other
  sum columns are resolved iteratively. If a sum can't be resolved (missing
  source columns), it is silently dropped.
- **No SICC/UPM columns found:** the pipeline falls back to auto-detection —
  all numeric columns that don't look like metadata are classified as SICC/UPM/CDYN
  based on column name keywords. This may include unwanted columns.
- **UPM overlay Y-axis:** Distribution histograms use dynamic UPM range (auto-scaled from data min/max + padding). Pareto also uses dynamic range. Do NOT use fixed 0-100 range — it causes all dots to cluster at top when values are in a narrow range (e.g. 92-97%).
