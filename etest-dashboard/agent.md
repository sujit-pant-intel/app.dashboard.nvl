# PCM Merge GUI — etest-dashboard

## Overview

The etest-dashboard is a dark-theme Tkinter application with two tabs:

1. **PCM Merge** (`src/pcm_merge_gui.py`) — Merges PCM etest data with die-level yield
   CSVs, adds reticle-map attributes, and optionally merges material metadata.
   Result: enriched CSV per input file.

2. **PCM Dashboard** (`src/pcm_dashboard_frame.py` + `src/generate_pcm_html.py`) —
   Generates a self-contained HTML variability dashboard for selected lots, grouped
   by parameter groups defined in a Product Setup JSON.

Launch via the top-level launcher (recommended):

```
python dashboard.py            # simple mode (input + PCM filter + hybrid + output)
python dashboard.py -d         # advanced mode (all options visible)
python dashboard.py input.json # load a saved config on startup
```

Or run the module directly:

```
python src/pcm_merge_gui.py
```

### Simple vs Advanced mode (PCM Merge tab)

| Mode | How to invoke | What is shown |
|---|---|---|
| Simple | `dashboard.py` (default) | Input list, Parameter filter, Hybrid toggle, Output folder, buttons, log |
| Advanced | `dashboard.py -d` | All of the above **+** etest CSV, wafer filter, full-site α, reticle map, material CSV |

All fields are always initialised; only the visibility differs.

---

## Folder Structure

```
etest-dashboard/
├── dashboard.py                       ← launcher (simple / advanced mode)
├── requirements.txt                   ← third-party deps: numpy, pandas
├── src/
│   ├── pcm_merge_gui.py               ← Tab 1: PCM Merge GUI + pipeline
│   ├── pcm_dashboard_frame.py         ← Tab 2: PCM Dashboard GUI frame
│   └── generate_pcm_html.py           ← HTML generator (matplotlib charts + table)
└── agent.md                           ← this file
```

Collateral (shared across tools):

```
shared/spec/collateral/etest/
└── pcm_product_setup.json             ← default parameter-group config for Dashboard tab
```

Shared data roots (relative to repo root `../../../` from `src/`):

| Constant | Path | Contents |
|---|---|---|
| `_NINE_SITE_DIR` | `shared/etest/9-sites/` | 9-site PCM CSVs (recursive subfolders) |
| `_FULL_SITE_DIR` | `shared/etest/full-sites/` | Full-site PCM CSVs (all lots, same tech) |
| `_RETICLE_DIR`   | `shared/reticle/` | Reticle-mapping CSVs per technology |
| `_MATERIAL_DIR`  | `shared/material/` | Lot-definition CSVs (INTEL_LOT7 keyed) |
| `_SPEC_CSV`      | `shared/spec/wat/N2P_NVL816_WAT_PDK1.0_target.csv` | PCM spec limits |
| `_DEFAULT_SETUP` | `shared/spec/collateral/etest/pcm_product_setup.json` | Default parameter-group config |

Reticle mapping CSV is auto-detected from the input file's DevRevStep (prefix6 match in `_RETICLE_DIR`).

---

## PCM Dashboard Tab

### Purpose

Generates a **self-contained HTML variability dashboard** for one or more PCM lots
without requiring a yield CSV.  The HTML is opened automatically in the browser.

### Inputs

| Field | Default | Purpose |
|---|---|---|
| Lot list | — | Multi-select from lots found in `shared/etest/9-sites/` |
| Include full-site CSVs | off | Also search `shared/etest/full-sites/` |
| Product Setup JSON | `shared/spec/collateral/etest/pcm_product_setup.json` | Defines groups, title, patterns |
| Output folder | — | Directory where `pcm_dashboard.html` is written |

### Product Setup JSON format

```json
{
  "title": "My Dashboard Title",
  "subtitle": "Optional subtitle",
  "groups": [
    { "name": "Conductance", "patterns": ["Con_*"] },
    { "name": "Vts N-FET",   "patterns": ["Vts_RN*", "Vtl_N*"] }
  ]
}
```

`patterns` use standard `fnmatch` wildcards (`*` = any characters, `?` = one character).
Multiple patterns per group are OR-combined.

### HTML Dashboard contents (4 tabs)

| Tab | Description |
|---|---|
| **Variability** | One strip chart per group; dots = per-die values; median diamond; LSL/USL/Target lines |
| **RO Distribution** | Frequency histogram per Propagation Delay / Oscillator param; X = % of target |
| **XY Plot** | Scatter of any X vs Y (or multiple Y) parameter across all selected wafers |
| **Summary Table** | Group, Parameter, Lot, Wafer, Material, N, Median, σ, CV%, Min, Max, LSL, USL, Unit |

Common features across all tabs:
- **Persistent filter sidebar** (left panel): searchable lot/wafer/layout/material table; All / Clr / Sel buttons
- **Group toggle buttons** (top bar): All / None + per-group buttons to show/hide groups in Variability tab
- **Group-by toolbar** on each tab: None / Lot / Wafer / Layout / Material (colour-codes dots/bars by the selection)
- CSV download: exports summary table as `pcm_summary.csv`

### Variability tab

- Strip chart per group; per-die dots coloured by group-by selection
- Median diamond (green) per parameter
- LSL (red dashed) / USL (blue dashed) / Target (orange cross) lines when spec is available
- **Group-by colour legend** rendered as HTML row *below* each chart (not in SVG right margin)
- Height slider (150 – 1200 px)

### RO Distribution tab

- **Group selector** (toolbar pill buttons): `All` shows params from every group; individual buttons filter to one group
  - Default = `All`; selecting a group resets the parameter pill selection
- Parameter pills below toolbar select which params to display as histogram cards
- ±3σ shaded region; median (green) and target (purple, at 100 %) vertical lines
- `Os_*` (oscillator speed) parameters now included alongside `Td_*` in the "Propagation Delay (OSC)" group

**Normalization rules (per-parameter in `_buildPdlyCards`):**

| Param pattern | X-axis label | Scale applied |
|---|---|---|
| `Td_*` (propagation delay) | Frequency (% of target) | `tgt / v × 100` — only when `meta.target` is non-null |
| `Poff_*`, `Ioff_*` (leakage) | `nA` / `µA` / `mA` (auto) | `_leakageScale(vals)` — picks unit so median ≈ 1–100 |
| All other params | `meta.unit` or raw | No transform |

> **Rule**: Only `Td_*` params use % normalization. `Poff_*` and `Ioff_*` are
> leakage currents — they use `_leakageScale` for unit auto-scaling, NOT `tgt/v*100`.
> The `isTd` flag gates the `%` path; anything else that has a target will NOT be normalized.

**Default params for the two main panels (`_pdlyPDefault`):**

| Panel | Default params |
|---|---|
| Panel 1 | `Td_RJ4u`, `Poff_RJ4u`, UPM_0107_950_sds (via `_findParamLike`) |
| Panel 2 | `Td_RK4u`, `Poff_RK4u`, UPM_0704_950_sds (via `_findParamLike`) |

Each is added only when it exists in `PCM_PARAM_META`.

### XY Plot tab

- X-axis: single parameter `<select>` (filtered by X group)
- Y-axis: **multi-select checklist dropdown** with live search; supports 1 or many Y params
  - When >1 Y param selected: group-by is dimmed and Y param names act as the colour key
  - Button label shows count, turns red/bold to indicate selection state
  - **Clr** button fully clears Y selection (does not re-seed)
- Trend lines: OLS or cross-wafer Theil-Sen (robust; samples 300 random cross-wafer die pairs, median slope anchored at medX/medY)
- Log X / Log Y toggles; X/Y range inputs; per-die / per-wafer-median toggle
- Height slider

### "Color by" options (Group-by)

| Option | Description |
|---|---|
| None | All dots same colour (default) |
| Lot | One colour per lot |
| Wafer | One colour per unique wafer ID |
| Layout | One colour per Layout value |
| Material | One colour per Material Type / Device Skew combination |

### Key source files

| File | Role |
|---|---|
| `src/pcm_dashboard_frame.py` | Tkinter frame class; lot scanning with Layout column; data loading (`_load_and_merge`) |
| `src/generate_pcm_html.py` | `generate_html(df, setup, path, spec_lookup)` — 4-tab HTML dashboard generation |
| `shared/spec/collateral/etest/pcm_product_setup.json` | Default group config; `Os_*` included in Propagation Delay group |

### Common Pitfalls (PCM Dashboard)

| Symptom | Cause | Fix |
|---|---|---|
| `Poff_*` / `Ioff_*` params show as % of target in distribution chart | Old `_buildPdlyCards` applied `tgt/v*100` to any param with a target value | Fixed: `isTd` flag gates the % path — only `Td_*` params normalize to % of target |
| Leakage currents shown in wrong unit | `_leakageScale` not applied when `isTd=false` | Fixed: non-`Td_*` leakage params use `_leakageScale(vals)` to auto-select nA / µA / mA |
| Distribution panel defaults missing `Poff_*` | Old `_pdlyPDefault` only seeded `Td_*` and UPM params | Fixed: Panel 1 adds `Poff_RJ4u`, Panel 2 adds `Poff_RK4u` (when present in spec) |

---

## GUI Fields (PCM Merge tab)

| Field | Default | Purpose |
|---|---|---|
| Input file(s) | — | Yield CSV or ZIP of yield CSVs |
| Parameter filter | `*Con*` | Wildcard(s) for PCM params, e.g. `*Vts*,*Isat*`; blank = all |
| Use full-site Hybrid | auto | Enabled + checked when full-site files are found; unchecked otherwise |
| Output folder | — | Destination for merged CSVs, spec-violation reports, and log |

### Advanced mode only (`-d`)

| Field | Purpose |
|---|---|
| 9-site etest CSV | Optional — leave blank for reticle+material-only mode |
| Wafer filter | Single wafer or "all"; populated from the input file |
| Alpha (α) | Blend weight: 1.0 = pure IDW, 0.0 = pure shape (default 0.5) |
| Reticle map CSV | Required — maps SORT_X/Y → LayoutX/Y and reticle attributes |
| Material CSV | Optional — lot-definition file for material metadata |

Config can be saved/loaded as JSON (💾 / 📂 buttons).

### Watermark

A purple `Pant, Sujit N — GEMS FTE` badge is placed in the top-right corner of the
window (matches yield-dashboard style).

---

## Pipeline Steps (`run_pipeline`)

### Step 1 — Load yield CSV
- Reads input CSV, normalises column names (`Sort_X` → `SORT_X`, etc.).
- Logs all wafer IDs found in `SORT_WAFER` / `WAFER` column.

### Step 2 — Load reticle mapping
- Maps `SORT_X` / `SORT_Y` → `LayoutX`, `LayoutY`, `Reticle`, `ReticleShot`,
  `Radius`, zone labels (`Concentric`, `Grid`, `Radial`, `Rows`, `Columns`, `Sectors`).
- If the mapping CSV has only `DieX`/`DieY`, `SORT_X`/`SORT_Y` are computed by
  centering around the midpoint.

### Steps 3–8 — PCM etest merge *(skipped if no etest CSV)*

**Step 3 — Load 9-site etest CSV**
- Warns if the etest filename doesn't contain the input `SORT_LOT`.
- Filters rows to the selected wafer; raises a descriptive error if filter matches 0 rows
  (lists available wafers in the error message).

**Step 4 — PCM parameter detection**
- All numeric non-ID columns are candidate PCM parameters.
- Optional wildcard filter (`pcm_filter`) narrows selection (case-insensitive, comma-separated).
- Default filter is `*Con*`; blank = all parameters.

**Step 5 — Average 9-site values**
- Groups by `(LayoutX, LayoutY)` and takes the mean of each PCM column.

**Step 6 — IDW to all reticles (Mode A)**
- Inverse Distance Weighting (`p=2`) from the 9 measured site positions to every
  reticle position in the mapping CSV.
- Exact-match sites are copied directly (no distance blending).

**Step 7 — Hybrid reconstruction (Mode B, if full-site CSVs provided)**
```
SampleShape  = full_site_mean − Median(full_site_mean)        [spatial pattern]
V_IDW        = IDW from 9 real site values
WaferScale   = Median( (V_9site_i − RealMedian) / (SampleShape_i + ε) )
Hybrid       = α·V_IDW + (1−α)·(RealMedian + WaferScale·SampleShape)
FinalMap     = Hybrid + (RealMedian − Median(Hybrid))         [median enforcement]
```
- Full-site CSVs are all lots matching the same DevRevStep (e.g. all `8PF5CV-L-*-PCM.csv`).
- Parameters missing from full-site CSVs fall back to pure IDW.
- The Hybrid checkbox is **only enabled** when full-site files are detected for the
  loaded input's technology; it is unchecked and non-functional when no files are found.

**Step 8 — Build reticle → PCM lookup dict**
- Keyed by `(LayoutX, LayoutY)` float tuples.

If no etest CSV is provided:
- Steps 3–8 are entirely skipped.
- `mode_str = "Reticle+Material only"`, `reticle_pcm = {}`, `pcm_cols = []`.

### Step 9 — Merge reticle attributes
- Left-join yield on `(SORT_X, SORT_Y)` to add `LayoutX`, `LayoutY`, and all reticle
  attribute columns.
- Drops `LayoutX`/`LayoutY` from output if they were not in the original yield CSV.
- Fills each PCM column from the `reticle_pcm` lookup (no-op if etest was skipped).

### Step 10 — Material merge
- Joins `shared/material/` CSV on `INTEL_LOT7` (first 7 chars of `SORT_LOT`).
- Tries per-wafer join (`INTEL_LOT7` + `WaferID`) first; falls back to lot-level if
  no rows match.
- Before merging, drops any conflicting columns already present in the yield data to
  prevent pandas `_x`/`_y` suffix collisions.
- Material columns kept: `Material Type`, `Device Skew`, `MG4 split`, `AIO/BB`,
  `Vy CD+`, `Remark`, `inline scrap`, `Material Type, Skew, BEOL Skew`, `Purpose`
  (whichever exist in the file).

### Step 11 — Save
- Output: `<out_folder>/<stem>-merged.csv`.

---

## DevRevStep Parsing (`_parse_devrevstep`)

Always returns `(prefix6, step)`:
- `prefix6` = first 6 characters (technology node, e.g. `8PF5CV`, `8PF6CV`).
- `step` = **last character** (manufacturing step: `R`, `P`, `L`, …).
- Characters between position 6 and the last char are variant codes (e.g. `E` in
  `8PF6CVER`) and are ignored for etest filename matching.

| Input | prefix6 | step |
|---|---|---|
| `8PF5CVL` | `8PF5CV` | `L` |
| `8PF6CVR` | `8PF6CV` | `R` |
| `8PF6CVER` | `8PF6CV` | `R` |

---

## Wafer Filtering & SORT_WAFER Decoding

The wafer dropdown is populated from the **input file** (not the etest CSV):
- For ZIP inputs: all CSVs inside the ZIP are scanned in a background thread; wafer
  values are unioned across all files.
- Raw values are shown in the dropdown (e.g. `803`, `814`).
- Wafer filter is only shown in **advanced mode**.

**`_decode_sort_wafer(val)`** converts a `SORT_WAFER` value before passing it to the
etest filter:
- 3-digit all-numeric → take last 2 digits as int (`803` → `3`, `814` → `14`).
- Otherwise → `str(int(val))` to strip leading zeros.

When the input wafer column is `SORT_WAFER` or `Sort_Wafer`, the flag
`_input_wafer_is_sort_wafer` is set and decoding is applied automatically.

---

## Auto-Detection

When an input file is selected, the GUI auto-detects:
- **DevRevStep** and **SORT_LOT** from the CSV.
- **9-site etest CSV** (`_guess_etest_path`): recursive `os.walk` of `_NINE_SITE_DIR`, priority:
  1. Exact filename `<prefix6>-<step>-<sort_lot>-PCM.csv`
  2. Exact filename with trailing-zero suffix `<prefix6>-<step>-<sort_lot>0-PCM.csv`
     *(handles lots like Q601S0G where the file is Q601S0G0-PCM.csv)*
  3. Fuzzy: any file containing both `<prefix6>` and `<sort_lot>`
  4. Any file starting with `<prefix6>-<step>-`
  5. Any file starting with `<prefix6>`
- **Reticle map CSV** (`_guess_reticle_map`): searches `_RETICLE_DIR` (`shared/reticle/`)
  for a file containing `<prefix6>` and `"Reticle"` in the name.
- **Material CSV** (`_guess_material_file`): looks in `_MATERIAL_DIR` for all files
  containing `<prefix6>`, then reads `INTEL_LOT7` column from each to find the file
  that contains the current `SORT_LOT[:7]`.  Falls back to prefix-match if not found.
- **Full-site CSVs**: recursive `os.walk` of `_FULL_SITE_DIR` for
  `<prefix6>-<step>-*-PCM.csv`.  If found, the Hybrid checkbox is enabled and checked
  automatically.

### Per-File Auto-Switch (mixed-technology batches)

When a batch contains files from different technology nodes (e.g. an 8PF5CV `L0` file
and an 8PF6CV `R0` file in the same ZIP), the pipeline **switches all three** (etest,
reticle map, material) per-file during `_pipeline_thread`:

- If the GUI-selected etest CSV has a different `prefix6` than the current file's
  DevRevStep, `_guess_etest_path` is called and the result is used for that file.
- Same logic for the reticle map — `_guess_reticle_map` is called on mismatch.
- Material is **always** re-resolved per file using `_guess_material_file(drs, sort_lot)`
  to ensure the correct lot-definition CSV (e.g. P0 vs R0) is used.

Switch events are logged as:
```
[Auto ] Etest switched to 8PF6CV-R-Q601S0G0-PCM.csv (tech 8PF6CV ≠ GUI 8PF5CV)
[Auto ] Reticle map switched to 8PF6CV-NVL816-Reticle_Mapping.csv (tech 8PF6CV ≠ GUI 8PF5CV)
[Auto ] Material switched to 8PF6CV-NVL816_R0_lot_definitions_r1.csv (lot 'Q601S0G')
```

---

## Outputs

| File | Location |
|---|---|
| Merged CSV | `<out_folder>/<stem>-merged.csv` |
| Spec violations | `<out_folder>/spec-violation/<stem>-violations.csv` |
| Run log | `<out_folder>/merge-log-YYYYMMDD-HHMMSS.txt` |

**Spec violations** (`write_spec_violations`): checks every PCM column against
`Spec Low` / `Spec High` in `_SPEC_CSV`; records parameter, value, limits, violation
type (`above_USL` / `below_LSL`), and deviation per die.

**Log file**: written after every batch run (or on failure), capturing every pipeline
message including config header, row counts, join statistics, and any warnings.

**Output folder cleanup**: at the start of each run, any existing `*-merged.csv`,
`spec-violation/` subfolder, and `merge-log-*.txt` files are removed so outputs are
always from the latest run only.

---

## Dependencies

```
pandas  numpy  tkinter (stdlib)
```

Install via:
```
python -m pip install pandas numpy --proxy http://proxy-us.intel.com:911
```


---

## Pipeline Steps (`run_pipeline`)

### Step 1 — Load yield CSV
- Reads input CSV, normalises column names (`Sort_X` → `SORT_X`, etc.).
- Logs all wafer IDs found in `SORT_WAFER` / `WAFER` column.

### Step 2 — Load reticle mapping
- Maps `SORT_X` / `SORT_Y` → `LayoutX`, `LayoutY`, `Reticle`, `ReticleShot`,
  `Radius`, zone labels (`Concentric`, `Grid`, `Radial`, `Rows`, `Columns`, `Sectors`).
- If the mapping CSV has only `DieX`/`DieY`, `SORT_X`/`SORT_Y` are computed by
  centering around the midpoint.

### Steps 3–8 — PCM etest merge *(skipped if no etest CSV)*

**Step 3 — Load 9-site etest CSV**
- Warns if the etest filename doesn't contain the input `SORT_LOT`.
- Filters rows to the selected wafer; raises a descriptive error if filter matches 0 rows
  (lists available wafers in the error message).

**Step 4 — PCM parameter detection**
- All numeric non-ID columns are candidate PCM parameters.
- Optional wildcard filter (`pcm_filter`) narrows selection (case-insensitive, comma-separated).

**Step 5 — Average 9-site values**
- Groups by `(LayoutX, LayoutY)` and takes the mean of each PCM column.

**Step 6 — IDW to all reticles (Mode A)**
- Inverse Distance Weighting (`p=2`) from the 9 measured site positions to every
  reticle position in the mapping CSV.
- Exact-match sites are copied directly (no distance blending).

**Step 7 — Hybrid reconstruction (Mode B, if full-site CSVs provided)**
```
SampleShape  = full_site_mean − Median(full_site_mean)        [spatial pattern]
V_IDW        = IDW from 9 real site values
WaferScale   = Median( (V_9site_i − RealMedian) / (SampleShape_i + ε) )
Hybrid       = α·V_IDW + (1−α)·(RealMedian + WaferScale·SampleShape)
FinalMap     = Hybrid + (RealMedian − Median(Hybrid))         [median enforcement]
```
- Full-site CSVs are all lots matching the same DevRevStep (e.g. all `8PF5CV-L-*-PCM.csv`).
- Parameters missing from full-site CSVs fall back to pure IDW.

**Step 8 — Build reticle → PCM lookup dict**
- Keyed by `(LayoutX, LayoutY)` float tuples.

If no etest CSV is provided:
- Steps 3–8 are entirely skipped.
- `mode_str = "Reticle+Material only"`, `reticle_pcm = {}`, `pcm_cols = []`.

### Step 9 — Merge reticle attributes
- Left-join yield on `(SORT_X, SORT_Y)` to add `LayoutX`, `LayoutY`, and all reticle
  attribute columns.
- Drops `LayoutX`/`LayoutY` from output if they were not in the original yield CSV.
- Fills each PCM column from the `reticle_pcm` lookup (no-op if etest was skipped).

### Step 10 — Material merge
- Joins `shared/material/` CSV on `INTEL_LOT7` (first 7 chars of `SORT_LOT`).
- Tries per-wafer join (`INTEL_LOT7` + `WaferID`) first; falls back to lot-level if
  no rows match.
- Before merging, drops any conflicting columns already present in the yield data to
  prevent pandas `_x`/`_y` suffix collisions.
- Material columns kept: `Material Type`, `Device Skew`, `MG4 split`, `AIO/BB`,
  `Vy CD+`, `Remark`, `inline scrap`, `Material Type, Skew, BEOL Skew`, `Purpose`
  (whichever exist in the file).

### Step 11 — Save
- Output: `<out_folder>/<stem>-merged.csv`.

---

## DevRevStep Parsing (`_parse_devrevstep`)

Always returns `(prefix6, step)`:
- `prefix6` = first 6 characters (technology node, e.g. `8PF5CV`, `8PF6CV`).
- `step` = **last character** (manufacturing step: `R`, `P`, `L`, …).
- Characters between position 6 and the last char are variant codes (e.g. `E` in
  `8PF6CVER`) and are ignored for etest filename matching.

| Input | prefix6 | step |
|---|---|---|
| `8PF5CVL` | `8PF5CV` | `L` |
| `8PF6CVR` | `8PF6CV` | `R` |
| `8PF6CVER` | `8PF6CV` | `R` |

---

## Wafer Filtering & SORT_WAFER Decoding

The wafer dropdown is populated from the **input file** (not the etest CSV):
- For ZIP inputs: all CSVs inside the ZIP are scanned in a background thread; wafer
  values are unioned across all files.
- Raw values are shown in the dropdown (e.g. `803`, `814`).

**`_decode_sort_wafer(val)`** converts a `SORT_WAFER` value before passing it to the
etest filter:
- 3-digit all-numeric → take last 2 digits as int (`803` → `3`, `814` → `14`).
- Otherwise → `str(int(val))` to strip leading zeros.

When the input wafer column is `SORT_WAFER` or `Sort_Wafer`, the flag
`_input_wafer_is_sort_wafer` is set and decoding is applied automatically.

---

## Auto-Detection

When an input file is selected, the GUI auto-detects:
- **DevRevStep** and **SORT_LOT** from the CSV.
- **9-site etest CSV** (`_guess_etest_path`): recursive `os.walk` of `_NINE_SITE_DIR`, priority:
  1. Exact filename `<prefix6>-<step>-<sort_lot>-PCM.csv`
  2. Exact filename with trailing-zero suffix `<prefix6>-<step>-<sort_lot>0-PCM.csv`
     *(handles lots like Q601S0G where the file is Q601S0G0-PCM.csv)*
  3. Fuzzy: any file containing both `<prefix6>` and `<sort_lot>`
  4. Any file starting with `<prefix6>-<step>-`
  5. Any file starting with `<prefix6>`
- **Reticle map CSV** (`_guess_reticle_map`): searches `_RETICLE_DIR` (`shared/reticle/`)
  for a file containing `<prefix6>` and `"Reticle"` in the name.
- **Material CSV** (`_guess_material_file`): looks in `_MATERIAL_DIR` for all files
  containing `<prefix6>`, then reads `INTEL_LOT7` column from each to find the file
  that contains the current `SORT_LOT[:7]`.  Falls back to prefix-match if not found.
- **Full-site CSVs**: recursive `os.walk` of `_FULL_SITE_DIR` for
  `<prefix6>-<step>-*-PCM.csv`.

### Per-File Auto-Switch (mixed-technology batches)

When a batch contains files from different technology nodes (e.g. an 8PF5CV `L0` file
and an 8PF6CV `R0` file in the same ZIP), the pipeline **switches all three** (etest,
reticle map, material) per-file during `_pipeline_thread`:

- If the GUI-selected etest CSV has a different `prefix6` than the current file's
  DevRevStep, `_guess_etest_path` is called and the result is used for that file.
- Same logic for the reticle map — `_guess_reticle_map` is called on mismatch.
- Material is **always** re-resolved per file using `_guess_material_file(drs, sort_lot)`
  to ensure the correct lot-definition CSV (e.g. P0 vs R0) is used.

Switch events are logged as:
```
[Auto ] Etest switched to 8PF6CV-R-Q601S0G0-PCM.csv (tech 8PF6CV ≠ GUI 8PF5CV)
[Auto ] Reticle map switched to 8PF6CV-NVL816-Reticle_Mapping.csv (tech 8PF6CV ≠ GUI 8PF5CV)
[Auto ] Material switched to 8PF6CV-NVL816_R0_lot_definitions_r1.csv (lot 'Q601S0G')
```

---

## Outputs

| File | Location |
|---|---|
| Merged CSV | `<out_folder>/<stem>-merged.csv` |
| Spec violations | `<out_folder>/spec-violation/<stem>-violations.csv` |
| Run log | `<out_folder>/merge-log-YYYYMMDD-HHMMSS.txt` |

**Spec violations** (`write_spec_violations`): checks every PCM column against
`Spec Low` / `Spec High` in `_SPEC_CSV`; records parameter, value, limits, violation
type (`above_USL` / `below_LSL`), and deviation per die.

**Log file**: written after every batch run (or on failure), capturing every pipeline
message including config header, row counts, join statistics, and any warnings.

---

## Dependencies

```
pandas  numpy  matplotlib  tkinter (stdlib)
```

Install via:
```
python -m pip install pandas numpy matplotlib --proxy http://proxy-us.intel.com:911
```
