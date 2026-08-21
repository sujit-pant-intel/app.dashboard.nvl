# Yield Dashboard — Architecture Migration Plan

## Problem

The current design embeds all data directly inside the generated HTML file:
- Per-die arrays (UPM, SORT_X/Y, FB, SDT, HW fields) → `_ic_rows`
- Recovery/bin-description analysis → `_recov_data_json`, `_bindesc_data_json`
- Reticle mapping, pattern scores, HW combo tables

For large lots (500+ wafers, millions of dies) this produces HTML files of 10–50MB+.
The browser must parse and hold **all** of it in JS heap on page load, even for features
the user never opens (heatmap, DLCP, Bin Analysis).

---

## Suggested Target Architecture

```
output_folder/
  index.html              ← ~20KB shell, no embedded data
  dashboard.js            ← all JS, shared across all datasets
  data_summary.json       ← pre-computed per-wafer aggregates (~50–200KB)
  data_dies.parquet       ← raw per-die rows, compressed columnar format
  serve.bat               ← double-click to open dashboard locally
```

### serve.bat (for recipients with Python installed)
```bat
@echo off
python -m http.server 8765 --directory "%~dp0"
start http://localhost:8765/index.html
```

---

## Migration Phases

### Phase 1 — Extract JS to `dashboard.js` ✅ Start here
**Risk: zero. Functional change: none.**

- The large JS string in `yield_analysis.py` is written to `dashboard.js` in `out_dir`
- HTML emits `<script src="dashboard.js"></script>` instead of inline JS
- HTML immediately shrinks from 10–50MB to ~20KB
- Bug fixes and feature updates to JS apply to all dashboards by replacing one file
- Still works via `file://` (no server needed yet)

### Phase 2 — Replace JSON blobs with `data_summary.json`
**Risk: low. Requires serve.bat.**

- `_ic_data_json`, `_recov_data_json`, `_bindesc_data_json`, `_recov_die_grps_json`
  are written to a single `data_summary.json` file
- HTML fetches it asynchronously on load: `fetch('data_summary.json')`
- Browser memory improves because data is parsed after render, not blocking it
- Recipient must run `serve.bat` before opening `index.html`

### Phase 3 — Replace per-die arrays with `data_dies.parquet` + DuckDB-WASM
**Risk: medium. Largest memory benefit.**

- Raw per-die rows (SORT_X, SORT_Y, UPM columns, FB, SDT, HW fields) are written
  to `data_dies.parquet` using `df.to_parquet()` — lossless, 5–10× smaller than JSON
- `data_summary.json` retains only the pre-aggregated per-wafer data (bin counts,
  median UPM, material type, lot/wafer labels) — always small
- Heatmap, DLCP, Bin Analysis query `data_dies.parquet` via DuckDB-WASM on demand;
  raw die rows never fully enter JS heap
- SICC/UPM aggregations run as SQL inside WASM: e.g.
  ```sql
  SELECT SORT_LOT, SORT_WAFER,
         MEDIAN(UPM_950MV / 9154.0 * 100) AS med_upm,
         COUNT(*) FILTER (WHERE INTERFACE_BIN IN (1,2)) AS ff_count
  FROM dies
  GROUP BY SORT_LOT, SORT_WAFER
  ```
  Only the aggregated result (one row per wafer) enters JS memory

---

## Memory Profile Comparison

| Feature | Current | After Phase 3 |
|---|---|---|
| Page load | All data parsed at once | Only summary (~200KB) |
| Bin histogram / yield table | From JS heap | From summary JSON |
| DLCP CDF / UPM median | From JS heap | SQL aggregation in WASM |
| UPM heatmap (one wafer) | All wafers pre-loaded | Queried on click, discarded after |
| Bin Analysis / Recovery | All pre-loaded | Queried on click |
| Raw die data in JS heap | Full dataset | Never |

---

## Data Safety

No data is lost. The source CSV is never modified. Parquet is lossless for all
numeric and string types present in sort data. Verify with:

```python
import pandas as pd
df_orig = pd.read_csv('data.csv')
df_back = pd.read_parquet('data_dies.parquet')
assert df_orig[target_cols].equals(df_back[target_cols])
```

---

## What to Keep Embedded (always small, no benefit to externalizing)

- CSS styles
- Metadata: test program name, lot list, wafer count, total units

---

## Dependencies Introduced

| Phase | New dependency |
|---|---|
| Phase 1 | None |
| Phase 2 | Python `http.server` (stdlib, always available) |
| Phase 3 | DuckDB-WASM (~6MB JS bundle, loaded from CDN or bundled in folder) |

---

## Files Modified in `yield_analysis.py`

| Phase | Change |
|---|---|
| 1 | Extract JS string → write to `out_dir/dashboard.js`; HTML references it |
| 2 | Write JSON blobs to `out_dir/data_summary.json`; HTML fetches on load |
| 3 | Write `df[die_cols].to_parquet(out_dir/'data_dies.parquet')`; remove die arrays from summary |
