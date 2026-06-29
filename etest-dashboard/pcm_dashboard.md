I want to make dashboard similar to vmin-dashboard. Few changes

In order to do that we need to create another tab in GUI called ETest/PCM Dashboard. Param will be

1. Lot Number   - multiple available 
2. Product Setup JSON
3. Output Folder 
Option for full site ortherwise use 9-site to generate full site

It will look for that lot and generate output . Output will have etest data as well as material

On the output , i will to create HTML Dashboard like Vmin . Here are few things

1. There will be lot / wafer / material and die count 

2. First we will do variabilty only . In variability plots will be groups. Grouping will be defined in product setup. This basically defined as . There will be variablity per group so it doesn't blow up . User can disable enable 
    Groups [
        "Cmin" , "Cmin*",
        "Con", "Con"
    ]
    Chart will show scatter plot as well as high low limit

    We will also have capbility to groupby lot/wafer/material  where group will get different chart as well as median values. 

3. Table will show all parameter as group
Group, Name, <rest is same as Vmin>
Table can be downloaded as CSV as well as Variablity data. 


HTML
I want layout as here file:///U:/nvl-bllc/vmin/NVL-BLLC/NVL816-BLLC-L0-61A/output/vmin_dashboard.html  .

1. Filter tab need be table with Layout,Wafer,Material (full name), Count, 
2. Grouping tab will be on top
3. Table with param name and details on left based on group in product json 
4. Group by is checkbox where i can check per layout,lot/wafer/material

---

## Changelog

### 2026-05-10

#### ZIP-transparent file support (`_constants.py`, `pcm_merge_gui.py`, `pcm_dashboard_frame.py`)
- Added `_ZIP_SEP`, `_zip_basename`, `_zip_isfile`, `_walk_dir_and_zips`, `_read_csv` in `_constants.py`
- All file discovery (etest, full-site, reticle, material) now walks inside `.zip` archives transparently using `archive.zip::member` references
- `_guess_etest_path`, `_list_available_etest_files`, `_guess_full_site_files`, `run_pipeline`, `_scan_lots`, `_find_csv_for_lot`, `_get_lot_layout`, `_load_and_merge` all updated

#### GUI validation fix (`pcm_merge_gui.py`)
- `_run` pre-flight check changed from `os.path.isfile` → `_zip_isfile` for etest CSV, so zip references pass validation
- `_apply_config` now clears stale etest paths (e.g. old OneDrive paths) on JSON load — auto-detect re-fills from shared folder

#### HTML dashboard — Tab styling (`generate_pcm_html.py`)
- Tab bar: darker background (`#1f3a50`), 14 px bold font, pill-style buttons
- Active tab: solid green fill (`#27ae60`) with white text and glow shadow
- Inactive tab hover: green-tinted highlight
- 3 px green bottom border ties bar to active tab colour

#### HTML dashboard — Outlier-robust stats (`generate_pcm_html.py`)
- `_paramStats()` now clips to P1/P99 before computing σ, CV%, Min, Max
- N and Median remain unclipped (robust estimators)
- Fixes extreme CV% values (e.g. 81,822,344%) caused by near-zero outliers

#### HTML dashboard — CSV download for every plot (`generate_pcm_html.py`)
- Shared helpers: `_csvQ`, `_csvBlob`, `_csvTs`
- **Variability parameter table**: existing `downloadVarCSV()` — Group, Param, N, Median, Std, CV%, Min, Max, LSL, USL, Unit
- **Group strip charts** (new): `downloadGrpCSV(grp)` — long format per (Lot, Wafer, Param); button in each group card header (right-aligned, green)
- **RO Distribution histograms** (new): `downloadPdlyCSV(param)` — per-die values; button floated top-right of each card; Freq%OfTarget column added for Td_ params
- **XY Scatter** (new): `downloadXYCSV()` — per-die or per-wafer pairs; button in second toolbar row

#### HTML dashboard — RO Distribution parameter selector (`generate_pcm_html.py`)
- `Td_` (Propagation Delay) parameters kept as visible toggle pills
- All other parameters moved to a compact searchable checkbox dropdown
- Dropdown shows live-filtered list, All/None buttons, selected count badge

#### HTML dashboard — Interactive parameter modal (`generate_pcm_html.py`)
- Clicking any row in the Parameter Table opens a modal popup
- Modal contains: stats bar (N, Median, σ, CV%, P1, P99, LSL, USL, Unit), histogram SVG, strip chart SVG, group-by colour legend
- Stats use P1/P99 clipping consistent with the table
- Close via × button, Escape key, or clicking the dark backdrop
- CSS: `.pm-overlay`, `.pm-card`, `.pm-hdr`, `.pm-body`, `.pm-stat-row` etc.
- JS: `_showParamModal`, `_closeParamModal`, `_buildParamModalChart`

HTML
I want layout as here file:///U:/nvl-bllc/vmin/NVL-BLLC/NVL816-BLLC-L0-61A/output/vmin_dashboard.html  .

1. Filter tab need be table with Layout,Wafer,Material (full name), Count, 
2. Grouping tab will be on top
3. Table with param name and details on left based on group in product json 
4. Group by is checkbox where i can check per layout,lot/wafer/material

