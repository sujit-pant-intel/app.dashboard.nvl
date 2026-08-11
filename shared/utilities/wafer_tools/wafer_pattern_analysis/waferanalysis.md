# wafer_pattern_analysis — Shared Wafer Pattern Analysis Module

## Purpose

Shared Python module that scores wafer spatial fail patterns (CENTER / EDGE / DONUT /
SYSTEMATIC / RETICLE / RANDOM) and optionally renders the same interactive WPA popup
used by the yield-dashboard.  Any debug tool or analysis script can call this module
and get:

- **Python scores** (for bullet text, table rows, auto-generated summaries)
- **Embeddable HTML+JS** (same look-and-feel as yield-dashboard WPA)

---

## Location

```
utilities/wafer_tools/wafer_pattern_analysis/
    __init__.py          ← re-exports score_wafer, WpaHtmlBuilder
    scorer.py            ← pure-Python scoring (no dependencies beyond stdlib)
    html_builder.py      ← builds WPA HTML block; imports JS from _wpa_js.py
    _wpa_js.py           ← single-source JS/CSS (wm-pat-* IDs, matches yield-dashboard)
    waferanalysis.md     ← this file
```

---

## Level 1 — Python Scoring API

### `score_wafer(xs, ys, ib_mask=None, edge_exclude_rows=1)`

Score the spatial pattern of a set of fail (or selected) dies.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `xs` | `list[int]` | SORT_X coordinates of the dies to score |
| `ys` | `list[int]` | SORT_Y coordinates of the dies to score |
| `ib_mask` | `list[bool] \| None` | If given, only `True` entries contribute to scoring (pass all=True to score all) |
| `edge_exclude_rows` | `int` | Number of outermost rows/cols to exclude from scoring (default 1, same as WPA default) |

**Returns** — `WaferPattern` dataclass:

```python
@dataclass
class WaferPattern:
    center:     float   # 0–1
    edge:       float   # 0–1
    donut:      float   # 0–1
    systematic: float   # 0–1
    reticle:    float   # 0–1  (0 if no reticle map provided)
    random:     float   # 0–1  = max(0, 1 - dominated)
    primary:    str     # 'CENTER'|'EDGE'|'DONUT'|'SYSTEMATIC'|'RETICLE'|'RANDOM'
    confidence: str     # 'LOW' (N<20) | 'MEDIUM' (N<50) | 'HIGH' (N>=50)
    n_fail:     int     # number of dies actually scored
    edge_pct:   float   # % of fail dies in outer ~40% of X radius (vcccont compat)
    summary:    str     # human sentence, e.g. "Edge-biased (62% in outer cols)"
```

**Minimal usage (vcccont_bin8 pattern):**

```python
import sys, os
_WT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'utilities', 'wafer_tools'))
if _WT not in sys.path: sys.path.insert(0, _WT)
from wafer_pattern_analysis import score_wafer

# Replace the existing _edge_thr block:
pat = score_wafer(
    xs=[d['x'] for d in dies],
    ys=[d['y'] for d in dies],
)
if pat.primary != 'RANDOM':
    _top_bullets.append(
        f'<b>{pat.n_fail} BIN8 dies</b> show '
        f'<b>{pat.primary}</b> pattern: {pat.summary}.'
    )
```

---

### `score_wafer_reticle(xs, ys, reticle_map, site_totals)`

Same as `score_wafer` but also computes the RETICLE score.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `xs` / `ys` | `list[int]` | Fail die coordinates |
| `reticle_map` | `dict[(int,int), tuple]` | `{(sort_x, sort_y): (site_x, site_y, shot_idx)}` — same format as `load_reticle_map()` returns |
| `site_totals` | `dict[str, int]` | `{"site_x,site_y": total_shots}` |

Returns the same `WaferPattern` dataclass with `.reticle` populated.

---

## Level 2 — HTML Builder API (same look as yield-dashboard WPA)

### `WpaHtmlBuilder`

Builds a self-contained WPA popup block that looks and behaves identically to the
yield-dashboard WPA.  Drop the output into any HTML page.

```python
from wafer_pattern_analysis import WpaHtmlBuilder

b = WpaHtmlBuilder()

# Add one wafer
b.add_wafer(
    key='LOT123A::W05',          # "lot::wafer" or any unique string
    dies=[(x, y, ib), ...],      # list of (sort_x, sort_y, ib_value)
    lot='LOT123A',
    wafer='05',
    material='FF',               # optional
    reticle_map=ret_map,         # optional dict from load_reticle_map()
    reticle_shots=shots,         # optional list of shot bounding-box tuples
)

# Repeat for all wafers / lots
# ...

html_block = b.build(
    btn_label='📊 Wafer Pattern Analysis',   # text on the trigger button
    trigger_id='my-wpa-btn',                 # HTML id of an existing button (optional)
    standalone=False,                        # True = wrap in full <!DOCTYPE html> page
    watermark='',                            # one-line text in drag bar (e.g. author)
)
```

`html_block` contains:
- Trigger button (or hook on `trigger_id` element)
- Overlay + full WPA modal popup — identical look to yield-dashboard
- All embedded JS (`_wmScorePattern`, `_wmScoreReticle`, `_wmPatRender`, …)
- All embedded CSS (`wm-pat-*` classes, `wm-t`, `wm-bar-*`)

**WPA popup tabs and features:**

| Feature | Description |
|---|---|
| Wafer Maps (left pane) | Per-wafer SVG grid, die loc numbers overlaid, reticle shot outlines |
| Composite Map (left pane) | Mode-IB or bin-density heat map across all selected wafers |
| Bin Impact (right tab) | Per-IB average pattern score bars, best-pattern annotation |
| Reticle tab | Table A (by die loc: fail%, hit%, wafer hits) + Table B (by shot: correlated locs) |
| Guide tab | Static reference table mapping each pattern to typical process suspects |
| Lot trend | Mini table above score table — per-lot average scores + top die loc |
| IB filter | Colour-swatch checkboxes to show/hide any IB on all maps |
| Die loc filter | Checkboxes to highlight/dim individual reticle die locations |
| Shot filter | Dropdown to show/hide individual shot indices |
| Edge-excl. / ≥IB | Controls for edge exclusion rows and fail-IB threshold |
| Vsplit / resize | Drag handles for left/right panel widths and score panel height |

Inject before `</body>` in your HTML output file.

---

## Data Format — `WM_PAT` JS Object

The JS block is driven by a single `WM_PAT` constant injected as inline JSON.
The Python builder produces this automatically, but if you construct it manually:

```js
var WM_PAT = {
  wafers: {
    "LOT123A::W05": {
      lot:      "LOT123A",
      wafer:    "05",
      material: "FF",           // optional
      program:  "NVL_SDS",      // optional
      pfx:      "8PF5CV",       // DevRevStep prefix; used to pick per-product reticle map
      dies: [
        [sort_x, sort_y, ib],   // ib = null for pass dies
        ...
      ]
    },
    ...
  },

  // Reticle data (optional; set hasReticle=false to omit)
  hasReticle: true,
  retMap: { "sort_x,sort_y": [site_x, site_y, shot_idx], ... },
  retShots: [[xMin,yMin,xMax,yMax], ...],       // per-shot bounding boxes
  retSiteTotals: { "site_x,site_y": N, ... },   // total shots per site
  retSiteLabels: { "site_x,site_y": "Loc1", ... },
  _retSiteNum:   { "site_x,site_y": 1, ... },   // numeric label for overlay

  // Per-DevRevStep override maps (multi-product CSVs)
  retMaps: {
    "8PF5CV": { retMap: {...}, retShots: [...], retSiteTotals: {...} },
    "8PF6CV": { retMap: {...}, retShots: [...], retSiteTotals: {...} },
  }
};
```

---

## Scoring Algorithm (single source of truth)

The algorithm is defined in `_wpa_js.py` as a JS string constant **and** mirrored
exactly in `scorer.py`.  Do not change one without the other.

### `_wmScorePattern(failXn, failYn)` — spatial scoring

Inputs: normalized coordinates `xn = (x - xCtr) / xRad`, `yn = (y - yCtr) / yRad`
(so the wafer disk fits in the unit circle).

**Radial band boundaries:**

| Band | r range | Meaning |
|---|---|---|
| B1 | 0 – 0.15 | Core center |
| B2 | 0.15 – 0.40 | Inner |
| B3 | 0.40 – 0.60 | Mid-inner |
| B4 | 0.60 – 0.75 | Mid-outer |
| B5 | 0.75 – 0.90 | Outer |
| B6 | 0.90 – ∞ | Edge |

**Expected fractions** (uniform circular distribution, `P(r < R) = R²`):

| Zone | Bands | Expected fraction |
|---|---|---|
| Center zone | B1+B2 | 0.16 |
| Mid zone | B3+B4 | 0.4025 |
| Edge zone | B5+B6 | 0.4375 |

**Score formulas:**

```
fC = (B1+B2) / N          # actual center fraction
fE = (B5+B6) / N          # actual edge fraction
fM = (B3+B4) / N          # actual mid fraction

centerScore     = clamp((fC - 0.16) / (1 - 0.16), 0, 1)
edgeScore       = clamp((fE - 0.4375) / (1 - 0.4375), 0, 1)
midEnrich       = max(0, (fM - 0.4025) / (1 - 0.4025))
donutScore      = min(1, midEnrich × 2 × (1 - max(centerScore, edgeScore) × 0.7))
sampleConf      = min(1, N / 20)
qImbal          = (max(quadrant_counts) - min(quadrant_counts)) / N
systematicScore = min(1, qImbal × 2.5) × sampleConf
dominated       = max(centerScore, edgeScore, donutScore, systematicScore)
randomScore     = max(0, min(1, 1 - dominated))
confidence      = 'LOW' if N<20 else 'MEDIUM' if N<50 else 'HIGH'
```

### `_wmScoreReticle(actX, actY, retMap, siteTotals)` — reticle scoring

```
For each fail die → look up (site_x, site_y, shot_idx) in retMap
Group by site → count unique failing shots per site
  score_site = failing_shots / total_shots_at_site
  weighted   = Σ(score_site × die_count) / total_mapped_dies
  maxSite    = max(score_site across all sites)
raw = weighted × 0.4 + maxSite × 0.6
sampleConf = min(1, N / 15)
reticleScore = min(1, raw × sampleConf)
```

---

## Pattern Colors (consistent across yield-dashboard and any tool using this module)

| Pattern | Hex |
|---|---|
| CENTER | `#c0392b` (red) |
| EDGE | `#e67e22` (orange) |
| DONUT | `#8e44ad` (purple) |
| SYSTEMATIC | `#2471a3` (blue) |
| RETICLE | `#1f618d` (dark blue) |
| RANDOM | `#27ae60` (green) |

---

## IB Color Mapping

The WPA popup colors dies by IB using `_wmIbColor(ib)`.  The yield-dashboard
defines this palette in `_pipeline_html.py`.  The shared module uses the same
mapping, defined once in `_wpa_js.py` and imported via `WpaHtmlBuilder`.

Pass bins 1–4 use fixed colours (green/grey); fail bins get palette slots assigned
deterministically by MD5 hash with deduplication (same algorithm as `_pipeline_html.py`).
`_wmIsFail(ib)` returns true when `ib >= _wmFailThr` (default 3); set `fail_thr` on
`WpaHtmlBuilder()` to change.

---

## vcccont_bin8 Migration Guide

### Before (existing code, lines ~983–999):

```python
_all_x_abs  = [abs(d['x']) for d in dies] or [1]
_max_x      = max(_all_x_abs)
_edge_thr   = int(_max_x * 0.6)
_wfr_stats  = []
for w in wfr_list:
    ...
    en   = sum(1 for x in xs if abs(x) >= _edge_thr)
    ep   = en / n * 100 if n else 0
    _wfr_stats.append({..., 'edge_pct': ep, ...})
_edge_wafers = [w for w in _wfr_stats if w['edge_pct'] >= 40]
```

### After (using this module):

```python
from wafer_pattern import score_wafer

_wfr_stats = []
for w in wfr_list:
    wnum = w['wfr']
    dies_w = [d for d in dies if d['wfr'] == wnum]
    pat = score_wafer([d['x'] for d in dies_w], [d['y'] for d in dies_w])
    _wfr_stats.append({
        ...,
        'edge_pct':  pat.edge_pct,
        'pat':       pat,          # full scores available if needed
    })
_edge_wafers = [w for w in _wfr_stats if w['edge_pct'] >= 40]

# Bullet now uses pat.summary instead of hand-crafted string:
for w in _wfr_stats:
    if w['pat'].primary not in ('RANDOM', 'EDGE'):
        _top_bullets.append(f'W{w["wfr"]}: {w["pat"].summary}')
```

### Optional — add the full WPA popup to vcccont_bin8 output HTML:

```python
from wafer_pattern_analysis import WpaHtmlBuilder

b = WpaHtmlBuilder()
for w in wfr_list:
    b.add_wafer(
        key=f'{w["lot"]}::{w["wfr"]}',
        dies=[(d['x'], d['y'], TARGET_IBIN) for d in dies if d['wfr'] == w['wfr']],
        lot=w['lot'], wafer=str(w['wfr']),
        reticle_map=reticle_lookup,   # from load_reticle_map()
    )
wpa_block = b.build()

# Inject before </body> in the generated HTML:
html = html.replace('</body>', wpa_block + '</body>')
```

The popup will look and behave exactly like the yield-dashboard WPA — same colors,
same scores, same per-wafer map grid, lot picker, score table.

---

## Relationship to yield-dashboard

`_wpa_js.py` contains the **complete JS/CSS** extracted from `_pipeline_html.py`
(yield-dashboard), using the same `wm-pat-*` DOM IDs and all JS functions.  The
yield-dashboard still inlines its own copy; this module provides a standalone
version for use in debug tools and analysis scripts.

**Sync rule:** when `_pipeline_html.py` WPA JS is updated, sync the changes into
`_wpa_js.py` (`WPA_FULL_JS`) and update the "Last verified" comment at the top.
Similarly, keep `scorer.py` thresholds in sync with `_wmScorePattern` in
`_pipeline_html.py`.

---

## Adding a New Debug Tool

1. Add a `sys.path.insert` pointing at the repo root (parent of `utilities/`)
2. `from utilities.wafer_pattern import score_wafer, WpaHtmlBuilder`
3. Call `score_wafer(xs, ys)` — get scores, primary pattern, summary sentence
4. Optionally `WpaHtmlBuilder().add_wafer(...).build()` — full WPA popup HTML
5. No third-party dependencies; `scorer.py` is stdlib-only

The user sees the same WPA popup they already know from the yield-dashboard —
same colours, same tabs, reticle die-loc numbers, composite map, shot filter.

---

## Scan-Dashboard Client-Side Pattern Scoring (`_rvBuildPatternScore`)

The scan-dashboard (`dashboard/scan-dashboard/dashboard/index.html`) implements
its own **simplified** pattern scoring entirely in JavaScript inside
`_rvBuildPatternScore()`.  It does **not** reuse `_wmScorePattern` from
`_wpa_js.py`.

### Why separate

The scan-dashboard is a static HTML file — no server-side Python at render time.
Copying the full `_wmScorePattern` was judged unnecessary for the qualitative
summary it needs; a simpler radial-zone approach is sufficient.

### Algorithm (as implemented)

**Radius normalisation** — computed from the layout bounding box (not actual wafer
geometry): `r = dist(die, center) / maxR`, where `center = (midX, midY)` of all
background die positions and `maxR = max(dist)` over all background positions.

**Scores are fail-count-weighted** (each die contributes its `fc` value, not 1):

| Score | Formula |
|---|---|
| EDGE | `Σ(fc where r > 0.60) / totFc` |
| CENTER | `Σ(fc where r < 0.30) / totFc` |
| DONUT | `wMid` if `wMid>0.45 && wEdge<0.35 && wCenter<0.25`, else `max(0, wMid-0.35)` |
| RETICLE | `max(0, (maxShotFc/totFc − 1/N) / (1 − 1/N))` — how much the top shot exceeds uniform share; `N` = total shots in layout |
| SYSTEMATIC | `max(0, (top20pct − 0.5) / 0.5)` where `top20pct` = fraction of `totFc` held by top 20% of die locations |
| RANDOM | `max(0, 1 − dominated × 1.5)` |

`dominated = max(EDGE, CENTER, DONUT, RETICLE, SYSTEMATIC)`

Confidence: LOW (< 20 fail locs), MED (< 50), HIGH (≥ 50).

### Differences from `_wmScorePattern` (reference algorithm)

| Aspect | `_wmScorePattern` (yield-dashboard / `scorer.py`) | `_rvBuildPatternScore` (scan-dashboard) |
|---|---|---|
| Coordinate normalisation | Separate x/y radii (elliptical unit disk) | Single `maxR` (circular approx) |
| Weighting | Die count (1 per fail die) | Fail-partition count (`fc`) |
| Band boundaries | 6 bands (0, 0.15, 0.40, 0.60, 0.75, 0.90, ∞) | 3 zones (< 0.30, 0.30–0.65, > 0.60) |
| SYSTEMATIC score | Quadrant imbalance × sample confidence | Top-20%-locs fail concentration |
| RETICLE score | `weighted × 0.4 + maxSite × 0.6` using per-site shot hits | Shot-level dominance over uniform share |
| Pattern colors | EDGE=orange `#e67e22`, CENTER=red `#c0392b` | EDGE=red `#e74c3c`, CENTER=purple `#9b59b6` |

### Sync guidance

If the reference thresholds in `scorer.py` / `_wmScorePattern` are tuned, review
whether `_rvBuildPatternScore` should also be updated.  The two are intentionally
independent but should agree directionally (edge-biased data → EDGE wins in both).
