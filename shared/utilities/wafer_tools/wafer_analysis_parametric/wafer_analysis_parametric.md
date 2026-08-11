# wafer_analysis_parametric — Parametric Fail Dashboard Method

## Purpose

Reference guide for building single-file HTML debug dashboards for **parametric / continuity
test failures** (e.g. VCC continuity BIN8, MIMC capacitance).  Captures the design
decisions, module wiring, and layout patterns established by `vcccont_bin8`.

This folder is a placeholder for shared parametric utilities to be extracted here in
future.  For now it documents the method so future dashboards can follow the same
pattern without rediscovery.

---

## Location

```
utilities/wafer_tools/wafer_analysis_parametric/
    wafer_analysis_parametric.md   ← this file
```

---

## Reference Implementation

```
code/debug/parametric/vcccont_bin8/src/generate_dashboard.py
```

Produces a self-contained ~4.7 MB HTML file.  No web server needed — open directly
in browser.

**Rebuild command:**
```powershell
cd "C:\scripts\app.yield.nvl\code\debug\parametric\vcccont_bin8\src"
C:\scripts\.venv\Scripts\python.exe -W ignore generate_dashboard.py --out "..\output-61AB\vcccont-bin8-analysis.html"
```

---

## Module Wiring

Both modules live in `utilities/wafer_tools/`.  Add the `wafer_tools` directory to
`sys.path` and import by package name:

```python
_WT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    '..', '..', '..', '..', '..', 'utilities', 'wafer_tools'))
if _WT not in sys.path:
    sys.path.insert(0, _WT)

from wafer_pattern_analysis import score_wafer, WaferPattern, WpaHtmlBuilder
from wafer_pattern_analysis._wpa_js import WPA_SCORE_JS
from wafer_map import WAFERMAP_JS
```

| Module | What it provides |
|---|---|
| `wafer_map` | `WAFERMAP_JS` — inject once before `</body>`;  exposes `wmRender(divId, cfg)` in JS |
| `wafer_pattern_analysis` | `score_wafer()` for Python-side pattern scoring; `WpaHtmlBuilder` for full WPA popup; `WPA_SCORE_JS` for inline JS scoring in the dashboard |

> `WAFERMAP_JS` **must** be the last `<script>` before `</body>`.  Any JS that calls
> `wmRender()` at page-load time must be injected *after* it.

---

## Test Context: Singulated (SORT) Dies

These dashboards deal with **singulated die test** data — not wafer-probe.

- Die coordinates come from SORT_X / SORT_Y columns
- "Wafer" still means a physical wafer run through sort
- No DPS / prober / chuck-site concepts apply
- Reticle sites are photolithography shots, not test sites
- "Systematic" pattern = same reticle-field position failing across shots/wafers
  → suspect reticle defect or mask step

---

## Key Data Dimensions (vcccont_bin8)

| Dimension | Column / Source | Description |
|---|---|---|
| Functional Bin | `SETBIN` prefix (before `\|`) | Coarse fail category (BIN8 = VCC continuity) |
| Kill Phase | `SETBIN` middle field | Which test phase first triggered BIN8 |
| Rail Type | Parsed from kill test name | VCC rail group (e.g. CORE, MEM, IO) |
| Failing Pin | `SETBIN` or separate pin column | Specific continuity pin that failed |
| Reticle Site | `RETICLE_MAP` lookup | Die's position within the reticle field (1-based loc number) |

---

## Composite Wafer View

The composite wafer overlays **all selected dies across all wafers** onto a single
SVG wafer map.  Dies that fail on multiple wafers are colored by frequency.

### Size constants

```python
_cpPAD    = 3      # padding (px) between die cells
_cpTW_LG  = 488    # composite (large) wafer width px  — currently 1.25× of 390
_cpTW_SM  = 240    # per-wafer tile width px
```

### Color modes (selectable in UI)

| Mode | `cp-color` value | What it shows |
|---|---|---|
| Functional Bin | `fbin` | Color by FBIN category |
| Kill Phase | `phase` | First kill phase |
| Rail Type | `rtype` | VCC rail group |
| Failing Pin (CS) | `pin` | Specific failing pin |
| Reticle Site # | `site` | Die-loc number within reticle field |
| Fail Freq % | `freq` | % of selected wafers where that die failed |

### JS globals required

| Global | Type | Description |
|---|---|---|
| `ALL_MAP` | object | `{"x,y": {fbin,phase,rtype,pin,site,wfrs:[...]}}` composite die map |
| `DIES` | array | Per-die records with x, y, wfr, fbin, phase, rtype, pin |
| `RETICLE_MAP` | object | `{"x,y": [rdx,rdy,shotIdx]}` — from reticle CSV |
| `RETICLE_SITE_NUM` | object | `{"rdx,rdy": loc_number}` |
| `TARGET_IBIN` | int | The IB being analyzed (e.g. 8) |
| `FB_LIST` | array | `[{fbin, label, color}]` |
| `PIN_LIST` | array | Unique failing pin names |
| `PROGS` | array | Program names present in data |
| `LOTS` | array | Lot IDs present in data |
| `WFR_RADIUS` | int | Max SORT_X / SORT_Y extent (used by wafer circle) |

---

## 3-Pane Layout

The Composite View tab uses a **3-pane horizontal layout** with drag-resize handles.

```
┌──────────────┬──────────────────────────┬──────────────────────────┐
│  Pane 1      │  Pane 2                  │  Pane 3                  │
│  Filters     │  Composite wafer (SVG)   │  Per-wafer tiles         │
│  (240px)     │  + Reticle Site table    │  + Pattern Score panel   │
│              │  (537px)                 │  (flex:1)                │
└──────────────┴──────────────────────────┴──────────────────────────┘
```

### CSS classes

| Class | Role |
|---|---|
| `.cp-pane-1` | Left: filters, fixed 240px, scrollable |
| `.cp-pane-2` | Middle: composite map + reticle site section, fixed 537px |
| `.cp-pane-3` | Right: per-wafer tiles + pattern score, flex:1 |
| `.cp-vresize-handle` | Vertical divider — drag to resize adjacent pane horizontally |
| `.cp-resize-handle` | Horizontal divider within a pane — drag to resize sub-section |

### Resize JS

```js
// Horizontal: resizes a named pane by class name
_cpStartResizeH(event, 'cp-pane-1')   // drag handle between pane 1 and 2
_cpStartResizeH(event, 'cp-pane-2')   // drag handle between pane 2 and 3

// Vertical: resizes a sub-section by element ID
_cpStartResize(event, 'cp-site-inner', 'up')   // reticle site panel height
_cpStartResize(event, 'cp-pat-inner',  'up')   // pattern score panel height
```

---

## Pattern Score Panel

Uses `WPA_SCORE_JS` (from `wafer_pattern_analysis._wpa_js`) inlined into the HTML to
score patterns entirely in JavaScript at view time.  No Python scoring at dashboard
generation time for the interactive panel.

Key JS functions:
- `_wmScorePattern(failXn, failYn)` — spatial radial scoring
- `_wmScoreReticle(xs, ys, retMap, siteTotals)` — reticle-site correlation
- `_wmPrimary(scores)` — picks dominant pattern
- `updatePatternPanel()` — called on every filter change; reads current die selection,
  scores it, writes bar chart + conclusion text to `#cp-pat-bars` / `#cp-pat-conclusion`

### Conclusion text (singulated test framing)

The SYSTEMATIC conclusion text specifically avoids DPS/prober language.  It references:
- Reticle site map (photolithography shots)
- Fail Freq % heatmap
- Handler swap experiment (singulated dies use handlers, not probers)
- Reticle defect or mask step as primary suspect

---

## Reticle Site Analysis Panel

Shows a table of reticle die-location numbers (`Loc 1 … Loc N`) ranked by fail frequency
across all selected wafers.  Each row shows:
- Loc number, % of wafers where that loc failed, total die hits, correlated locs

Populated by `_cpBuildSiteTable()` in dashboard JS, which reads from `ALL_MAP` and
`RETICLE_SITE_NUM`.

---

## Adding a New Parametric Dashboard

1. Copy `vcccont_bin8/src/generate_dashboard.py` as the template
2. Update `_WT` path depth to match new directory depth
3. Replace data parsing section (SETBIN columns → your test's columns)
4. Update `ALL_MAP` build logic with your dimensions
5. Update color mode options in `<select id="cp-color">` and `_cpColorFn()` in JS
6. Update conclusion text framing for your test type
7. Keep `_cpTW_LG`, `_cpTW_SM`, `WFR_RADIUS`, `RETICLE_MAP` wiring unchanged

---

## UI Specification

### Top Bar (`.cp-topbar`)

Spans the full width above all 3 panes.  Contains the view title and toggle buttons.

```css
.cp-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 12px;
    background: #0a1018;
    border-bottom: 2px solid #1e3050;
    flex-shrink: 0;
}
```

Height is content-driven (~32px).  The layout below it is `calc(100vh - 90px)` tall
(54px tab bar + ~36px top bar).

### Pane Sizes (initial / default)

| Pane | CSS class | Default width | Min width | Notes |
|---|---|---|---|---|
| Filters | `.cp-pane-1` | 240px | 120px | Fixed, scrollable |
| Composite + Reticle | `.cp-pane-2` | 537px | 200px | Fixed, vertically split |
| Tiles + Pattern | `.cp-pane-3` | flex:1 | 200px | Takes remaining space |

Vertical dividers (`.cp-vresize-handle`): `width:5px`, `cursor:ew-resize`, `background:#1e3050`.
Hover/active color: `#3a6090`.

Horizontal dividers (`.cp-resize-handle`): `height:5px`, `cursor:ns-resize`, `background:#1a2a40`.

### Sub-section Heights

| Section | Element ID | Default height | Min height |
|---|---|---|---|
| Reticle Site Analysis | `#cp-site-inner` | 28vh | 60px |
| Pattern Score | `#cp-pat-inner` | 32vh | 60px |

### Color Palette (dark theme)

| Role | Value |
|---|---|
| Page / pane background | `#0a1018` |
| Card / filter background | `#0d1520` |
| Panel divider / border | `#1e3050` |
| Pane divider handle | `#1a2a40` (H), `#1e3050` (V) |
| Handle hover | `#3a6090` |
| Heading text | `#8ab4d4` |
| Body text | `#c0ccd8` |
| Muted label | `#556677` |
| Reticle site button | `#c77dff` on `#1a1a2a` border `#6a3aaa` |
| Pattern score button | `#4ecdc4` on `#1a2a1a` border `#2a6a2a` |

### Font Sizes

| Element | Size |
|---|---|
| Section headings (`.cp-h2`) | `13px` (top bar: `0.9rem`) |
| Sub-section headings | `0.78–0.82rem` |
| Filter column labels (`.cp-flab`) | `0.68rem`, `font-weight:700`, `text-transform:uppercase` |
| Checkbox labels (`.cp-cb`) | `0.7rem` |
| Toggle buttons (`.cp-tog`) | `0.62rem` |
| Color-by select | `0.72rem` |
| Legend text | `10px` |
| Pattern conclusion text | `0.78rem`, `line-height:1.6` |

### Wafer Map Sizes

| Constant | Value | Notes |
|---|---|---|
| `_cpTW_LG` | 488px | Composite wafer (large) — 1.25× of 390 base |
| `_cpTW_SM` | 240px | Per-wafer tile |
| `_cpPAD` | 3px | Die cell padding |

Composite SVG is centered within pane 2 using `display:flex; align-items:center; justify-content:center`
on the `#cp-map-scroll` div.

### Toggle Button Styles (top bar)

```css
/* Pattern Score button */
font-size:0.78rem; background:#1a2a1a; border:1px solid #2a6a2a;
color:#4ecdc4; border-radius:4px; padding:3px 10px;

/* Reticle Sites button */
font-size:0.78rem; background:#1a1a2a; border:1px solid #6a3aaa;
color:#c77dff; border-radius:4px; padding:3px 10px;

/* Close (✕) buttons inside panels */
font-size:0.72rem; background:#1a2235; border:1px solid #445566;
color:#8ab4d4; border-radius:4px; padding:1px 8px;
```

---

## Reticle CSV Format

Located in `shared/reticle/`.  Two CSVs per product step:

```
8PF5CV-NVL-816-BLLC-Reticle_Mapping.csv
8PF6CV-NVL816-Reticle_Mapping.csv
```

Columns: `SORT_X, SORT_Y, RETICLE_X, RETICLE_Y, RETICLE` (1-based die-loc number).

Load with `load_reticle_map()` in the generator:
```python
reticle_lookup  = {}   # {(sx,sy): (rdx,rdy,shot_idx)}
reticle_shots   = []   # [[xmin,ymin,xmax,ymax], ...]
site_num        = {}   # {(rdx,rdy): loc_number}
```
