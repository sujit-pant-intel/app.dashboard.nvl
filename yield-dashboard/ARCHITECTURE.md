# Yield Dashboard — Architecture Migration Plan

*Last updated: 2026-08-21*

## Problem

The current design embeds all data directly inside the generated HTML file:
- Per-die arrays (UPM, SORT_X/Y, FB, SDT, HW fields) → `_ic_rows`
- Recovery/bin-description analysis → `_recov_data_json`, `_bindesc_data_json`
- Reticle mapping, pattern scores, HW combo tables

For large lots (500+ wafers, millions of dies) this produces HTML files of 10–50MB+.
The browser must parse and hold **all** of it in JS heap on page load, even for features
the user never opens (heatmap, DLCP, Bin Analysis).

---

## Constraint

Users receive the output folder and open `index.html` directly via `file://`.
No local server is available. All data loading must work without `fetch()`.
`<script src="...">` tags work on `file://`; `fetch()` does not.

---

## Target Architecture

```
output_folder/
  wafermap.html           ← nav shell (iframe host); loads sub-pages
  bin_dist/
    *_BinDistribution.html  ← yield/bin dashboard HTML (~20KB)
    dashboard.js            ← all BinDist JS logic
    data_summary.js         ← window.__summary = {...}  (per-wafer aggregates)
    hw_<lot>.js             ← window.__hw_<lot> = {...} (HW combos, per-lot)
    *_bindef.csv            ← bin definition CSVs
    *_out.xlsx              ← output Excel files
  sicc/
    *_sicc_analysis.html    ← SICC/CDYN/UPM interactive dashboard
    plotly.min.js           ← shared Plotly library (externalized)
    sicc_data.js            ← SICC/CDYN per-die data
    sicc_dashboard.js       ← SICC dashboard JS logic
  wafermap/
    *_IBIN_WaferMap_*.html  ← per-lot wafermap pages
  data_heatmap.js         ← window.__heatmap = [...]  (per-die x/y/bin, lazy — Phase 3)
  data_dlcp.js            ← window.__dlcp = [...]     (UPM columns only, lazy — Phase 3)
  data_recovery.js        ← window.__recovery = {...} (bin analysis, lazy — Phase 3)
```

Lazy files are injected as `<script>` tags only when the user opens that tab:

```js
function loadScript(src, callback) {
    const s = document.createElement('script');
    s.src = src;
    s.onload = callback;
    document.head.appendChild(s);
}
// Only on first click of "Heatmap" tab:
loadScript('data_heatmap.js', renderHeatmap);
```

---

## Migration Phases

### Phase 1 — Extract JS to `dashboard.js` ✅ Done
**Risk: zero. Functional change: none.**

- The large JS string in `yield_analysis.py` is written to `dashboard.js` in `out_dir`
- HTML emits `<script src="dashboard.js"></script>` instead of inline JS
- HTML immediately shrinks from 10–50MB to ~20KB
- Works via `file://`

### Phase 2 — Extract aggregated data to `data_summary.js` ✅ Done
**Risk: low. No server required.**

- `_ic_data_json`, `_recov_data_json`, `_bindesc_data_json`, `_recov_die_grps_json`
  are written to `data_summary.js` as `window.__summary = {...}`
- HTML loads it via `<script src="data_summary.js"></script>` — works on `file://`
- Note: spec originally said `fetch('data_summary.json')` but that requires a server;
  `<script src>` achieves the same separation without one

### Phase 3 — Split per-die arrays into lazy-loaded JS files
**Risk: low. No server required. Largest load-time benefit.**

- Raw per-die rows are split by feature into separate `.js` files
  (`data_heatmap.js`, `data_dlcp.js`, `data_recovery.js`), each assigning a
  `window.__<feature>` global
- `data_summary.js` retains only pre-aggregated per-wafer data (bin counts,
  median UPM, material type, lot/wafer labels) — always small
- Feature JS files are injected via dynamic `<script>` tags only when the user
  opens that tab for the first time — never parsed on page load
- Raw die data never enters JS heap until explicitly requested

### Phase 4 — Output folder reorganization into subdirectories ✅ Done
**Risk: low. Backward-compatible (discovery helpers fall back to top folder).**

- `*_BinDistribution.html`, `*_bindef.csv`, `*_out.xlsx` → moved into `bin_dist/`
- `*_sicc_analysis.html` → moved into `sicc/`
- `wafermap.html` nav links prefixed: `bin_dist/<name>`, `sicc/<name>`
- Script src paths inside each HTML drop the subfolder prefix (HTML and JS are co-located)
- All glob/discovery calls in `yield_pipeline.py`, `yield_automation.py`, `yield_group_compare.py`
  check the new subdirectory first with a top-folder fallback for compatibility with old runs
- Files modified: `sicc_cdyn_upm/sicc_cdyn_upm.py`, `yld/yield_analysis.py`,
  `yld/yield_pipeline.py`, `yld/yield_automation.py`, `yld/yield_group_compare.py`

### Phase 5 — Plotly externalization for SICC ✅ Done
**Risk: zero. Functional change: none.**

- Plotly bundle (~3.5MB) written once to `sicc/plotly.min.js`
- All `*_sicc_analysis.html` files reference it via `<script src="plotly.min.js">`
- Eliminates per-lot 3.5MB inline blob; `sicc/` folder total stays ~3.5MB regardless of lot count

### Phase 6 — SVG `<title>` removal from SICC HTML ✅ Done
**Risk: zero. Titles are tooltip noise; removing them is safe.**

- SICC wafermap SVG rects previously embedded `<title>die info</title>` per rect
- Removed entirely; tooltip is handled by Plotly hover, not SVG title
- File size reduction: ~65% per `*_sicc_analysis.html`

### Phase 7 — HW combo data externalized to per-lot JS files ✅ Done
**Risk: low.**

- `window.__hw_<lot>` data written to `bin_dist/hw_<lot>.js` for each lot
- BinDistribution HTML loads only the relevant `hw_<lot>.js` files it needs
- Avoids embedding large HW combo tables inline in HTML

---

## Planned Future Improvements

### F-1 — `scattergl` for SICC scatter traces (crash prevention)
- In `sicc_cdyn_upm.py`, change every per-die scatter trace from `'type': 'scatter'`
  to `'type': 'scattergl'` (WebGL-backed)
- Prevents tab crash on large lots with 200K+ points
- One-word change per trace; zero visual difference for the user

### F-2 — CSS/JS deduplication across per-lot wafermap HTMLs
- Shared CSS (~50KB) and JS (hover, zoom, FB filter, HW modal, wafer selection) is
  duplicated in every `*_IBIN_WaferMap_*.html`
- Move to `wafermap/wafermap_ibin.js` + `wafermap/wafermap_ibin.css`
- Reference via `<script src="wafermap_ibin.js">` and `<link rel="stylesheet" href="wafermap_ibin.css">`
- Saves ~150KB × N lots; nav shell already uses the `wafermap/` subdirectory

### F-3 — FBDESC extraction to shared file
- `FBDESC` mapping (~5KB) is identical across all per-lot wafermap HTMLs
- Extract to `wafermap/fbdesc.js` as `window.__fbdesc = {...}`
- Load once via `<script src="fbdesc.js">` in each per-lot HTML

### F-4 — Phase 3: lazy-loaded per-die JS files (BinDist)
- See Phase 3 above (not yet implemented)
- Prerequisite: Phase 2 (`data_summary.js`) is already done ✅

---

## Memory Profile Comparison

| Feature | Current | After Phase 3 |
|---|---|---|
| Page load | All data parsed at once | Only `data_summary.js` (~200KB) |
| Bin histogram / yield table | From JS heap | From `data_summary.js` |
| DLCP CDF / UPM median | From JS heap | From `data_dlcp.js`, loaded on first tab click |
| UPM heatmap | All wafers pre-loaded | From `data_heatmap.js`, loaded on first tab click |
| Bin Analysis / Recovery | All pre-loaded | From `data_recovery.js`, loaded on first tab click |
| Raw die data in JS heap at page load | Full dataset | Never |

---

## Data Safety

No data is lost. The source CSV is never modified. Each `.js` data file is
generated directly from the same DataFrame used to build the dashboard.

---

## What to Keep Embedded (always small, no benefit to externalizing)

- CSS styles
- Metadata: test program name, lot list, wafer count, total units

---

## Dependencies Introduced

| Phase | New dependency |
|---|---|
| Phase 1 | None |
| Phase 2 | None |
| Phase 3 | None |

---

## Files Modified

| Phase | Files |
|---|---|
| 1 | `yld/yield_analysis.py` — extract JS → `dashboard.js`; HTML uses `<script src>` |
| 2 | `yld/yield_analysis.py` — write aggregated blobs to `data_summary.js` |
| 3 | (pending) `yld/yield_analysis.py` — split per-die arrays; `dashboard.js` lazy-injects |
| 4 | `sicc_cdyn_upm/sicc_cdyn_upm.py`, `yld/yield_analysis.py`, `yld/yield_pipeline.py`, `yld/yield_automation.py`, `yld/yield_group_compare.py` |
| 5 | `sicc_cdyn_upm/sicc_cdyn_upm.py` — Plotly externalized |
| 6 | `sicc_cdyn_upm/sicc_cdyn_upm.py` — SVG title removal |
| 7 | `yld/yield_analysis.py` — HW combo data to `hw_<lot>.js` |

---

## Future Direction — Server + Database + Real-Time Dashboard

*Status: planned / not yet implemented*

### Motivation

The current static-file approach (Phases 1–7) eliminates per-load bloat but still bakes all
data at generation time. As lot count grows across daily runs, a persistent database enables
on-demand queries, cross-lot analytics, and a live dashboard that updates automatically when
new data arrives — without regenerating any HTML.

### Constraint change

Users would launch a `.bat` file instead of opening `index.html` directly. The launcher starts
the local server, then opens the browser. The "double-click to view" UX is preserved.

```bat
@echo off
cd /d "%~dp0"
start /b python server.py
timeout /t 2 >nul
start http://localhost:5001
```

### Target Architecture

```
dashboard.db            ← DuckDB file; appended each daily run
server.py               ← Flask API + static file server
launch.bat              ← starts server.py, opens browser
wafermap.html           ← nav shell; fetch() replaces <script src> data loading
bin_dist/
  *_BinDistribution.html
  dashboard.js          ← uses fetch('/api/...') instead of window.__summary
sicc/ ...
wafermap/ ...
```

### Database schema (DuckDB)

| Table | Key | Contents |
|---|---|---|
| `ic_rows` | `(lot, wafer, x, y)` | Per-die ibin, UPM, FB, SDT, HW fields |
| `summary` | `(lot, wafer)` | Pre-aggregated bin counts, yield, material type |
| `recov` | `(lot, wafer, bin)` | Recovery / bin-description analysis |
| `hw_combos` | `(lot, hw_combo)` | HW combination tables |
| `ingest_log` | `lot` | One row per ingested lot; used to skip re-processing |

### Deduplication strategy

Each daily run does a **delete-then-insert** per lot before appending:

```python
con.execute("DELETE FROM ic_rows WHERE lot = ?", [lot_id])
con.execute("INSERT INTO ic_rows SELECT * FROM ic_df")
con.execute("INSERT OR REPLACE INTO ingest_log(lot, source_csv) VALUES (?,?)", [lot_id, csv_path])
```

If the lot has already been ingested and the source CSV is unchanged, the run skips it entirely
via `ingest_log`.

### Server API (`server.py` — Flask)

| Endpoint | Returns |
|---|---|
| `GET /` | `wafermap.html` |
| `GET /api/summary` | Per-wafer aggregates for all lots |
| `GET /api/heatmap?lot=X[&wafer=Y]` | Per-die x/y/ibin for lot (and optionally wafer) |
| `GET /api/dlcp?lot=X` | UPM columns + x/y |
| `GET /api/recovery?lot=X` | Bin analysis / recovery data |
| `GET /api/stream` | Server-Sent Events; fires when a new lot is ingested |

### Real-time update (Server-Sent Events)

The server pushes a notification the moment a new lot is written to `ingest_log`.
The browser reloads only the summary — no page refresh needed.

```js
// dashboard.js
const es = new EventSource('/api/stream');
es.onmessage = () => fetch('/api/summary').then(r => r.json()).then(renderYieldTable);
```

### Cross-lot analytics unlocked by the DB

| Feature | SQL pattern |
|---|---|
| Yield trend over time | `SELECT ingested_at, AVG(yield) FROM summary GROUP BY DATE(ingested_at)` |
| Lot-to-lot bin comparison | `WHERE lot IN (...)` |
| Yield drop alert at ingest | Server-side check before responding to `/api/summary` |
| Cross-lot UPM heatmap overlay | Aggregate `ic_rows` across lots |
| On-demand Excel export | `pandas.read_sql → DataFrame.to_excel` in `/api/export` |

### What changes in `yield_analysis.py`

- End of pipeline: write DataFrames to `dashboard.db` instead of `.js` files
- `data_summary.js` / `hw_<lot>.js` generation removed
- Static HTML shells (`wafermap.html`, `*_BinDistribution.html`) still generated as today

### What changes in `dashboard.js`

- `window.__summary` / `window.__hw_*` reads replaced with `fetch('/api/...')` calls
- `window.__CONFIG = { apiBase: 'http://localhost:5001' }` injected in HTML `<head>` for
  portability (can point to a shared server instead of localhost)
