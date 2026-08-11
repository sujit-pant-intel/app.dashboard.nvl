# Distribution Panel — Full Implementation Reference

**Source file:** `code/dashboard/etest-dashboard/src/generate_pcm_html.py`  
**Entry-point function:** `buildPropDelayTab()` (JavaScript)  
**Python HTML generator:** `generate_html()` in `generate_pcm_html.py`

---

## 1. Overview

The Distribution tab renders N + 1 collapsible panels.  
- **Panels 1 … N** — driven by `PCM_DIST_PANELS` (configured via `pcm_panels` JSON).  
- **Panel N+1** — "Custom" panel, always present, free-choice param selector.  
- Every panel is independent: own group filter, param selection, height slider, group-by, and SVG cards.

---

## 2. Data Schema

### `PCM_ROWS` (one object per parameter × per wafer)

| Field | Type | Description |
|---|---|---|
| `lot` | string | Lot ID |
| `wafer` | string | Wafer number |
| `sort_wafer` | string | Sort wafer ID |
| `layout` | string | Layout/reticle name |
| `material` | string | Material type |
| `program` | string | Test program name |
| `group` | string | Parameter group name |
| `param` | string | Parameter column name |
| `n` | number | Number of measured die sites |
| `median` | number | Median of raw values |
| `std` | number | Std deviation |
| `cv` | number\|null | CV (%) |
| `min_val` | number | Min raw value |
| `max_val` | number | Max raw value |
| `die_values` | number[] | Per-die raw values (max 500 entries) |

### `PCM_PARAM_META` (keyed by param name)

| Field | Type | Description |
|---|---|---|
| `group` | string | Group name |
| `lsl` | number\|null | Lower spec limit |
| `usl` | number\|null | Upper spec limit |
| `target` | number\|null | Target value |
| `unit` | string | Physical unit string |
| `name` | string | Human-readable display name |

### `PCM_DIST_PANELS` (JavaScript array, injected by Python)

```js
var PCM_DIST_PANELS = [
  { "label": "Prop Delay — RJ4u", "params": ["Td_RJ4u", "Poff_RJ4u"] },
  { "label": "SICC SDS",          "params": ["SICC_RING_0.95_SDS", ...] },
];
```

---

## 3. Python Configuration

### `generate_html()` signature (relevant excerpt)

```python
def generate_html(
    df: pd.DataFrame,
    product_setup: dict,
    output_path: str,
    spec_lookup=None,           # {param: (lsl, usl, target, unit, name)}
    pcm_panels: dict | None = None,
) -> str:
```

### `pcm_panels["distribution"]` JSON

```json
{
  "distribution": [
    {
      "label": "Key Propagation Delays",
      "params": ["Td_RJ4u", "Td_RK4u", "Poff_RJ4u", "UPM_ULVT_0107*"]
    },
    {
      "label": "SICC SDS",
      "params": ["SICC_RING_*_SDS", "SICC_CORE_*_SDS"]
    }
  ]
}
```

- `params` entries support fnmatch wildcards (`*`, `?`), case-insensitive.
- Each wildcard resolves to all matching param column names at generation time.

### `product_setup` JSON (group definitions)

```json
{
  "title": "NVL816 PCM Dashboard",
  "subtitle": "Optional subtitle",
  "groups": [
    { "name": "Propagation Delay", "patterns": ["Td_*"] },
    { "name": "Vts N-FET",         "patterns": ["Vts_RN*", "Vts_N*"] }
  ]
}
```

---

## 4. HTML DOM Structure (Python-generated)

One block per configured panel (pn = 1..N), then one Custom panel (pn = N+1):

```html
<!-- Panel wrapper — border-bottom:3px solid #bcd on all but the last panel -->
<div style="flex-shrink:0; border-bottom:3px solid #bcd">

  <!-- Green collapse/expand header -->
  <div style="background:#1a6e2b;
              border-bottom:1px solid #bcd;
              padding:4px 10px;
              display:flex; align-items:center; gap:8px;
              cursor:pointer"
       onclick="togglePdlyP({pn})">

    <!-- Toggle button -->
    <button id="pdlyp{pn}-toggle"
            style="border:none; background:none; cursor:pointer;
                   font-size:15px; color:#fff; padding:0 4px; line-height:1"
            title="Collapse/Expand">
      ▼    <!-- ▼ = &#9660; when expanded; ▶ = &#9654; when collapsed -->
    </button>

    <!-- Panel title -->
    <span style="font-size:15px; font-weight:bold; color:#fff">
      ⊙ Panel {pn} — {label}    <!-- ⊙ = &#9673; -->
    </span>
  </div>

  <!-- Body: empty initially; filled by buildPropDelayTab() -->
  <div id="pdlyp{pn}-body"></div>

</div>
```

`buildPropDelayTab()` injects into `pdlyp{pn}-body`:

```html
{bar HTML from _buildPdlyPanelBar}
<div style="padding:0 14px 10px">
  {cards HTML from _buildPdlyCards}
</div>
```

---

## 5. JavaScript State Variables

```js
// Collapse state (false = expanded by default)
var _PDLY_P_COLLAPSED = (function() {
  var o = {};
  for (var i = 1; i <= PCM_DIST_PANELS.length + 1; i++) o[i] = false;
  return o;
}());

// Per-panel group filter — null = all groups; else a PCM_GROUPS string
var _PDLY_GRP_P  = { 1: null, 2: null, ..., N+1: null };

// Per-panel chart height in pixels (range 150–900, step 25, default 350)
var _PDLY_H_P    = { 1: 350, 2: 350, ..., N: 350 };

// Per-panel selected param Set — null means use _pdlyPDefault(pn)
var _PDLY_SEL_P  = { 1: null, 2: null, ..., N: null };

// Per-panel search string in the "Other" dropdown
var _PDLY_SRCH_P = { 1: '', 2: '', ..., N: '' };

// Per-panel group-by fields (INDEPENDENT per panel)
// []               → None (single colour)
// ['lot']          → colour by lot
// ['wafer']        → colour by wafer
// ['lot','wafer']  → colour by lot+wafer combo
// ['layout']       → colour by layout
// ['material']     → colour by material
var _PDLY_GBY_P  = { 1: [], 2: [], ..., N+1: [] };

// Custom panel (N+1) extra state:
var _PDLY_GRP  = null;   // group filter
var _PDLY_SEL  = null;   // selected params Set
var _PDLY_H    = 350;    // chart height
var _PDLY_SRCH = '';     // search string
```

---

## 6. Control Bar (`_buildPdlyPanelBar`)

### Bar container (dark-blue strip)

```html
<div style="display:flex; flex-wrap:wrap; align-items:center;
            padding:6px 14px; gap:6px; flex-shrink:0;
            background:#1f3a50; border-bottom:1px solid #1a252f">
```

### Axis hint text (leftmost item)

```html
<span style="color:#aed6f1; font-size:11px">
  X: Freq% of target &nbsp;|&nbsp; Y: Samples
</span>
```

### Vertical divider (reused throughout the bar)

```html
<span style="width:1px; background:#4a6278; align-self:stretch; margin:0 4px"></span>
```

### "Group:" label

```html
<span style="color:#f1c40f; font-size:12px; font-weight:bold">Group:</span>
```

### Group filter buttons

Active button (`grp === g`):
```html
<button onclick="setPdlyGrpP({pn}, '{g}')"
        style="font-size:11px; padding:2px 9px; border-radius:4px;
               border:none; cursor:pointer; color:#fff;
               background:#2980b9; font-weight:bold">
  {g}
</button>
```

Inactive button:
```html
<button style="... background:rgba(0,0,0,0.25); font-weight:normal">{g}</button>
```

"All" button uses `setPdlyGrpP({pn}, '')`. Both states: `font-size:11px; padding:2px 9px; border-radius:4px; border:none; cursor:pointer; color:#fff`.

### "Group by:" label and checkboxes

```html
<b style="color:#f1c40f; margin-right:4px; font-size:12px">Group by:</b>

<label style="cursor:pointer; font-size:12px; color:#ecf0f1">
  <input type="checkbox" value="none"
         onchange="toggleGbyP({pn}, 'none')"
         checked>    <!-- checked when _PDLY_GBY_P[pn].length === 0 -->
  None
</label>

<label style="cursor:pointer; font-size:12px; color:#ecf0f1">
  <input type="checkbox" value="lot"
         onchange="toggleGbyP({pn}, 'lot')"
         checked>    <!-- checked when 'lot' in _PDLY_GBY_P[pn] -->
  Lot
</label>
<!-- identical pattern for: Wafer | Layout | Material -->
```

All 5 checkboxes — label text, `value`, and `onchange`:

| Label | `value` | `onchange` |
|---|---|---|
| `None` | `"none"` | `toggleGbyP(pn,'none')` |
| `Lot` | `"lot"` | `toggleGbyP(pn,'lot')` |
| `Wafer` | `"wafer"` | `toggleGbyP(pn,'wafer')` |
| `Layout` | `"layout"` | `toggleGbyP(pn,'layout')` |
| `Material` | `"material"` | `toggleGbyP(pn,'material')` |

### Height slider

```html
<span style="width:1px; background:#4a6278; align-self:stretch; margin:0 4px"></span>

<label style="display:flex; align-items:center; gap:4px;
              cursor:default; color:#ecf0f1; font-size:12px">
  ↕ Height    <!-- ↕ = &#11041; -->
  <input type="range" min="150" max="900" step="25" value="{h}"
         oninput="_PDLY_H_P[{pn}] = +this.value;
                  document.getElementById('pdlyp{pn}-h-val').textContent = this.value + 'px';
                  buildPropDelayTab()"
         style="width:90px; accent-color:#3498db">
  <span id="pdlyp{pn}-h-val"
        style="min-width:34px; color:#aed6f1; font-size:10px">
    {h}px
  </span>
</label>
```

---

## 7. Pills Bar (below control bar)

```html
<div style="display:flex; flex-wrap:wrap; gap:4px; align-items:center;
            margin:8px 14px 6px; padding:6px 8px;
            background:#f0f4fb; border-radius:6px; border:1px solid #dde">
```

### "Prop. Delay:" section (only when Td_* params exist)

```html
<span style="font-size:10px; color:#7f8c8d; font-weight:bold;
             margin-right:2px; flex-shrink:0">Prop. Delay:</span>
```

### Prop. Delay pill — SELECTED

```html
<button onclick="togglePdlyParamP({pn}, '{p}')"
        title="{LSL=... USL=... unit}"
        style="padding:3px 12px; font-size:11px; border-radius:6px;
               border:1px solid #2980b9; background:#2980b9;
               color:#fff; cursor:pointer; font-weight:bold">
  &lt;{param}&gt;
  <span style="font-size:9px; font-weight:normal; opacity:0.8; margin-left:3px">
    ({name})    <!-- only if meta.name is non-empty -->
  </span>
</button>
```

### Prop. Delay pill — UNSELECTED

```html
<button style="padding:3px 12px; font-size:11px; border-radius:6px;
               border:1px solid #bdc3c7; background:#f8f9fa;
               color:#2c3e50; cursor:pointer; font-weight:normal">
  &lt;{param}&gt;{optional name span}
</button>
```

Pill state summary:

| State | `border` | `background` | `color` | `font-weight` |
|---|---|---|---|---|
| Selected | `1px solid #2980b9` | `#2980b9` | `#fff` | `bold` |
| Unselected | `1px solid #bdc3c7` | `#f8f9fa` | `#2c3e50` | `normal` |

### Divider between Td_ pills and Other dropdown

```html
<span style="display:inline-block; width:1px; background:#bdc3c7;
             align-self:stretch; margin:0 6px"></span>
```

### "Other:" label

```html
<span style="font-size:10px; color:#7f8c8d; font-weight:bold;
             margin-right:2px; flex-shrink:0">Other:</span>
```

### "Other params" dropdown trigger button

With selections (`selOtherCnt > 0`):
```html
<button id="pdlyp{pn}-drop-btn" onclick="_pdlyPDropToggle({pn})"
        style="padding:3px 10px 3px 12px; font-size:11px; border-radius:6px;
               border:1px solid #2980b9; background:#eaf4ff;
               color:#2c3e50; cursor:pointer; font-weight:bold">
  {n} selected ▼    <!-- ▼ = &#9660; -->
</button>
```

Without selections:
```html
<button style="... border:1px solid #bdc3c7; background:#f8f9fa; font-weight:normal">
  Select params ▼
</button>
```

Trigger button state:

| State | `border` | `background` | `font-weight` | Text |
|---|---|---|---|---|
| Has selections | `1px solid #2980b9` | `#eaf4ff` | `bold` | `{n} selected ▼` |
| Empty | `1px solid #bdc3c7` | `#f8f9fa` | `normal` | `Select params ▼` |

### "Other" dropdown panel

```html
<div id="pdlyp{pn}-drop"
     style="display:none; position:absolute; top:calc(100% + 3px); left:0;
            z-index:9999; background:#fff; border:1px solid #bdc3c7;
            border-radius:6px; box-shadow:0 4px 16px rgba(0,0,0,.18);
            width:310px; max-height:320px; flex-direction:column">

  <!-- Search + All/None bar -->
  <div style="padding:5px 6px; border-bottom:1px solid #eee;
              display:flex; gap:4px; align-items:center">
    <input id="pdlyp{pn}-drop-srch" type="text" placeholder="Search…"
           oninput="_pdlyPDropSearch({pn}, this.value)"
           style="flex:1; padding:3px 6px; font-size:11px;
                  border:1px solid #ccc; border-radius:4px">
    <button onclick="_pdlyPDropSelAll({pn})"
            style="font-size:10px; padding:2px 6px; border:1px solid #ccc;
                   border-radius:3px; background:#f8f9fa;
                   cursor:pointer; flex-shrink:0">All</button>
    <button onclick="_pdlyPDropClrAll({pn})"
            style="font-size:10px; padding:2px 6px; border:1px solid #ccc;
                   border-radius:3px; background:#f8f9fa;
                   cursor:pointer; flex-shrink:0">None</button>
  </div>

  <!-- Param list -->
  <div id="pdlyp{pn}-drop-list"
       style="overflow-y:auto; max-height:260px; padding:2px 0">
    <!-- Each row built by _pdlyPBuildDropList(pn) -->
    <label style="display:flex; align-items:center; gap:5px;
                  padding:2px 6px; cursor:pointer; border-radius:3px;
                  font-size:11px; white-space:nowrap"
           onmouseover="this.style.background='#e8f0fe'"
           onmouseout="this.style.background=''">
      <input type="checkbox" checked
             onchange="_pdlyPDropCheck({pn}, '{p}', this.checked)"
             style="cursor:pointer">
      <b style="font-size:11px">{param}</b>
      <span style="color:#888; font-size:10px"> ({name})</span>
    </label>
  </div>

</div>
```

Key dimensions: `width:310px`, `max-height:320px` (panel), `max-height:260px` (list).

---

## 8. SVG Histogram

### Canvas dimensions

| Variable | Value | Notes |
|---|---|---|
| `svgW` | `700` | Fixed width in viewBox units |
| `svgH` | `_PDLY_H_P[pn]` | Default 350, range 150–900 |
| `ML` | `72` | Left margin |
| `MR` | `100` | Right margin (legend space) |
| `MT` | `40` | Top margin |
| `MB` | `72` | Bottom margin |
| `plotW` | `528` | `700 - 72 - 100` |
| `plotH` | `svgH - 112` | `svgH - 40 - 72` |

### SVG element

```html
<svg width="100%" height="{svgH}" viewBox="0 0 700 {svgH}" style="display:block">
```

No `cursor` style (unlike XY scatter which has `cursor:crosshair`).

### Background

```html
<rect width="700" height="{svgH}" fill="#f8f9fa"/>
<rect x="72" y="40" width="528" height="{plotH}"
      fill="#fff" stroke="#ccc" stroke-width="1"/>
```

### Y-axis — 6 grid lines and tick labels (yi = 0..5)

```
yv  = round(maxYDisp × yi / 5)
ypv = (40 + plotH − (yv / maxYDisp) × plotH).toFixed(1)
```

```html
<!-- Grid line -->
<line x1="72" y1="{ypv}" x2="600" y2="{ypv}"
      stroke="rgba(0,0,0,0.10)" stroke-width="0.8"/>

<!-- Tick label at x = ML - 4 = 68 -->
<text x="68" y="{ypv}"
      text-anchor="end" dominant-baseline="middle"
      font-size="18" font-weight="bold" fill="#111">{yv}</text>
```

### Y-axis title ("Samples")

```html
<text transform="translate(18, {MT + plotH/2}) rotate(-90)"
      text-anchor="middle"
      font-size="18" font-weight="bold" fill="#111">Samples</text>
```

Position: x=18 in the left margin (18px from left edge of SVG).

### Bin count formula

```
nBins = max(10, min(40, ceil(sqrt(N) × 2.2)))
binW  = range / nBins
maxYDisp = ceil(maxBarCount × 1.15)
xPad  = max(range × 0.05, binW × 0.5)
xLo   = mn − xPad
xRng  = range + xPad × 2
```

### Histogram bars

```
bpxD    = binW / xRng × plotW       (bin width in SVG pixels)
barW    = max(0.5, (bpxD − 1) / max(1, nGrps))
offsetX = (nGrps > 1) ? (gi − (nGrps−1)/2) × barW : 0

bx = ML + (mn + b×binW − xLo) / xRng × plotW + offsetX
bh = cnts[b] / maxYDisp × plotH
by = MT + plotH − bh
```

```html
<rect x="{bx}" y="{by}" width="{max(0.5, barW − 0.5)}" height="{bh}"
      fill="{groupColour}" opacity="0.65" rx="1"/>
```

Multiple groups: bars are side-by-side within each bin (offset by `gi × barW`).

### ±3σ region shading

```html
<!-- Orange fill rect behind the ±3σ lines -->
<rect x="{max(ML, xp(med−3σ))}" y="40"
      width="{min(ML+plotW, xp(med+3σ)) − max(ML, xp(med−3σ))}"
      height="{plotH}"
      fill="rgba(230,126,34,0.08)" stroke="none"/>
```

### Vertical reference lines and labels

| Line | Colour | `stroke-width` | `stroke-dasharray` | Top label font | Bottom label |
|---|---|---|---|---|---|
| −3σ | `#e67e22` | `2` | `4,3` | `font-size:16 bold` at `y=MT−6` | — |
| +3σ | `#e67e22` | `2` | `4,3` | `font-size:16 bold` at `y=MT−6` | — |
| −6σ | `#c0392b` | `1.5` | `2,4` | `font-size:13 bold` at `y=MT−6` | — |
| +6σ | `#c0392b` | `1.5` | `2,4` | `font-size:13 bold` at `y=MT−6` | — |
| Median | `#27ae60` | `2.5` | `6,3` | `font-size:16 bold fill:#1a6e2b` at `y=MT−6` | — |
| Target (Td_) | `#8e44ad` | `2` | `3,3` | `font-size:14 bold` at `y=MT−6` | `font-size:16 bold` at `y=MT+plotH+44` |
| LSL | `#e74c3c` | `2` | `4,3` | `font-size:14 bold` at `y=MT−6` | `font-size:16 bold` at `y=MT+plotH+44` |
| USL | `#e74c3c` | `2` | `4,3` | `font-size:14 bold` at `y=MT−6` | `font-size:16 bold` at `y=MT+plotH+44` |

All top labels: `text-anchor="middle"`.

### OOS shading text (raw-value params only)

When `nLo > 0` (below LSL) or `nHi > 0` (above USL):

```html
<!-- Fill -->
<rect x="{oosL}" y="40" width="{oosR−oosL}" height="{plotH}"
      fill="rgba(231,76,60,0.13)" stroke="none"/>

<!-- Percentage — y = MT + 20 -->
<text x="{midX}" y="{MT+20}"
      text-anchor="middle" font-size="17" font-weight="bold" fill="#c0392b">
  {pct}%
</text>

<!-- Label — y = MT + 36 -->
<text x="{midX}" y="{MT+36}"
      text-anchor="middle" font-size="13" fill="#c0392b">below LSL</text>
<!-- or: above USL -->
```

### X-axis tick labels — 7 ticks (xi = 0..6)

```
xv  = xLo + xRng × xi / 6
xpv = (ML + xi/6 × plotW).toFixed(1)
```

```html
<text x="{xpv}" y="{MT + plotH + 24}"
      text-anchor="middle"
      font-size="18" font-weight="bold" fill="#111">
  {xv.toFixed(1)}%    <!-- Td_ mode: percentage -->
  {_fmt(xv)}          <!-- raw mode: _fmt formatting -->
</text>
```

### X-axis label (bottom of SVG)

```html
<text x="{ML + plotW/2}" y="{svgH − 4}"
      text-anchor="middle"
      font-size="18" font-weight="bold" fill="#111">
  &lt;{param}&gt;({name}) — {xSuffix}{tgtStr}
</text>
```

`xSuffix` values:
- `Td_*` with target → `"Frequency (% of target)"`
- Leakage → `_leakageScale().unit` (e.g. `"nA"`, `"µA"`, `"mA"`)
- Others → `meta.unit`

`tgtStr` (Td_ only): `" | target=123.4 Hz"` (raw target + unit)

### Stats summary line (inside plot area, upper right)

```html
<text x="{ML + plotW − 2}" y="{MT + 22}"
      text-anchor="end" font-size="16" fill="#222">
  N={n} | Med={medLbl} | σ={sdLbl} | Spread={cv}%
</text>
```

### Right-side legend (x origin = ML + plotW + 6 = 606)

Legend Y positions (`ly = MT + 12 = 52`):

| Item | SVG element | Y | Colour | Style |
|---|---|---|---|---|
| Median swatch | `<rect width=14 height=14 rx=2>` | `ly` | `#27ae60` | — |
| Median label | `<text font-size=16 bold>` | `ly+7` | `#111` | `dominant-baseline:middle` |
| ±3σ swatch | `<line>` | `ly+30` | `#e67e22` | `stroke-width:2 dasharray:4,3` |
| ±3σ label | `<text font-size=16 bold>` | `ly+30` | `#d35400` | `dominant-baseline:middle` |
| ±6σ swatch | `<line>` | `ly+50` | `#c0392b` | `stroke-width:1.5 dasharray:2,4` |
| ±6σ label | `<text font-size=14 bold>` | `ly+50` | `#c0392b` | `dominant-baseline:middle` |
| Target swatch | `<line>` | `ly+70` | `#8e44ad` | `stroke-width:2 dasharray:3,3` |
| LSL swatch | `<line>` | `ly+70+20` | `#e74c3c` | `stroke-width:2 dasharray:4,3` |
| USL swatch | `<line>` | `ly+70+40` | `#e74c3c` | `stroke-width:2 dasharray:4,3` |

All swatch lines: `x1=ML+plotW+6`, `x2=ML+plotW+20`. Label text at `x=ML+plotW+24`.  
Target/LSL/USL only shown when present in `PCM_PARAM_META`.

---

## 9. Card Wrapper and Header

### Card container

| Condition | `border` | `background` |
|---|---|---|
| Normal | `1px solid rgba(0,0,0,0)` | `#fff` |
| Outside ±3σ (no spec) | `2px solid #e67e22` | `#fffbf5` |
| Spec violation | `2px solid #e74c3c` | `#fff8f8` |

```html
<div style="background:{cardBg}; border-radius:6px;
            box-shadow:0 1px 4px rgba(0,0,0,.10);
            padding:8px 10px; border:{cardBorder}">
```

### Card header line

```html
<div style="font-weight:bold; font-size:24px; color:#1a252f; margin-bottom:4px">

  <!-- CSV download button (float:right) -->
  <button onclick="downloadPdlyCSV('{param}')"
          title="Download histogram data as CSV"
          style="float:right; margin-left:8px; padding:2px 9px;
                 font-size:10px; font-weight:bold;
                 border:none; border-radius:3px;
                 background:#27ae60; color:#fff; cursor:pointer"
          onmouseover="this.style.background='#1e8449'"
          onmouseout="this.style.background='#27ae60'">
    ⬇ CSV    <!-- ⬇ = &#11015; -->
  </button>

  <!-- OOS badge (float:right, shown only when violations exist) -->
  <span style="float:right; margin-left:8px; padding:1px 7px;
               border-radius:3px; color:#fff;
               font-size:10px; font-weight:bold; letter-spacing:.3px;
               background:#e74c3c">   <!-- spec: #e74c3c | sigma: #d35400 -->
    ⚠ OUT OF SPEC     <!-- or: ⚠ OUTSIDE ±3σ -->
  </span>

  &lt;{param}&gt;
  <span style="font-weight:normal; color:#5d6d7e; font-size:20px">({name})</span>
  <span style="font-weight:normal; color:#7f8c8d; font-size:20px">
    [LSL=…, USL=…, Target=…]
  </span>
</div>
```

---

## 10. Stats Table

```html
<div style="display:flex; flex-wrap:wrap; gap:2px 0; margin-bottom:4px;
            border:1px solid {statBorderCol}; border-radius:5px;
            overflow:hidden; font-size:11px; {optionalBg}">
```

Border and background by violation state:

| Condition | `statBorderCol` | Container bg |
|---|---|---|
| Normal | `#e8ecf0` | (none) |
| Spec violation | `#fad7d7` | `background:#fff0f0` |
| Sigma violation | `#fde8d0` | `background:#fff9f2` |

```html
  <!-- Each stat cell -->
  <div style="display:flex; flex-direction:column; align-items:center;
              padding:3px 10px; border-right:1px solid {statBorderCol};
              flex:1; min-width:60px; {cellBg}">
    <!-- cellBg: fail cells = background:#fdecea | sigma = background:#fef3e0 -->

    <!-- Label: 9px, bold, uppercase, letter-spacing:.5px -->
    <span style="color:#888;     <!-- or red for fail/sigma -->
                 font-size:9px; font-weight:bold;
                 text-transform:uppercase; letter-spacing:.5px">
      N
    </span>

    <!-- Value: 12px, bold -->
    <span style="color:{valueColour}; font-weight:bold;
                 font-size:12px; white-space:nowrap">
      {value}
    </span>
  </div>
```

All stat cells in order:

| Label | `color` | Shown when |
|---|---|---|
| `N` | `#555` | Always |
| `Median` | `#1a6e2b` | Always |
| `σ` | `#2471a3` | Always |
| `Spread (%)` | `#555` | Always |
| `P1` | `#555` | Always |
| `P99` | `#555` | Always |
| `Target` | `#8e44ad` | Raw mode + `meta.target != null` |
| `LSL` | `#e74c3c` | Raw mode + `meta.lsl != null` |
| `USL` | `#e74c3c` | Raw mode + `meta.usl != null` |
| `% < LSL` | `#c0392b` | `nLo > 0` — cell bg `#fdecea` |
| `% > USL` | `#c0392b` | `nHi > 0` — cell bg `#fdecea` |
| `±6σ out` | `#c0392b` | No spec limits AND `nOut6 > 0` — cell bg `#fef3e0` |

---

## 11. Group Colour Legend (below SVG, only when nGrps > 1)

```html
<div style="display:flex; flex-wrap:wrap; gap:4px 12px;
            padding:4px 6px 2px; font-size:11px;
            border-top:1px solid #eee; margin-top:2px">
  <span style="display:flex; align-items:center; gap:3px">
    <!-- colour swatch: 11×11px square -->
    <span style="width:11px; height:11px; background:{colour};
                 display:inline-block; border-radius:2px;
                 flex-shrink:0; opacity:0.85"></span>
    <span style="color:#2c3e50; word-break:break-all">{groupKey}</span>
  </span>
</div>
```

---

## 12. Group-By System

### `_grpKeyWith(r, gby)` — per-row group key

```js
function _grpKeyWith(r, gby) {
  if (!gby || !gby.length) return 'All';
  var parts = [];
  if (gby.indexOf('lot')      >= 0) parts.push(r.lot || '');
  if (gby.indexOf('wafer')    >= 0) parts.push(String(r.wafer || ''));
  if (gby.indexOf('layout')   >= 0) parts.push(r.layout || '');
  if (gby.indexOf('material') >= 0) parts.push(r.material || '');
  return parts.join('/') || 'All';
}
```

### `_cMapWith(gby)` — colour map

```js
function _cMapWith(gby) {
  var map = {}, keys = [];
  var ak = activeKeys();
  PCM_ROWS.forEach(function(r) {
    if (!ak.has(_rKey(r))) return;
    var k = _grpKeyWith(r, gby);
    if (!map[k]) { map[k] = _cPal(keys.length); keys.push(k); }
  });
  return { map: map, keys: keys };
}
```

### `toggleGbyP(pn, field)` — toggle handler

```js
function toggleGbyP(pn, field) {
  var arr = _PDLY_GBY_P[pn] || (_PDLY_GBY_P[pn] = []);
  if (field === 'none') {
    arr.splice(0);           // clear all → None
  } else {
    var i = arr.indexOf(field);
    if (i >= 0) arr.splice(i, 1);  // remove
    else arr.push(field);           // add
  }
  buildPropDelayTab();
}
```

### Colour palette (15 colours, cycling)

```js
var _CPALS = [
  '#2980b9', '#27ae60', '#e67e22', '#8e44ad', '#c0392b',
  '#16a085', '#f39c12', '#1abc9c', '#d35400', '#7f8c8d',
  '#3498db', '#2ecc71', '#e74c3c', '#9b59b6', '#f0a500'
];
function _cPal(i) { return _CPALS[i % _CPALS.length]; }
```

---

## 13. Parameter Transforms

### `Td_*` — frequency % of target

```js
// target = meta.target ?? ((lsl + usl) / 2)
normVals = rawVals.map(v => v !== 0 ? (target / v * 100) : null).filter(isFinite);

// Clip to ±3σ (prevents extreme outliers distorting histogram)
var m0 = _med(normVals), s0 = _std(normVals);
normVals = normVals.filter(v => Math.abs(v - m0) <= 3 * s0);
```

X-axis unit: `"Frequency (% of target)"`. Target line drawn at x=100%.

### Leakage (`Poff_*` / `Ioff_*`) — auto-scale

`_leakageScale(vals)` → `{scale, unit}`:
- Median < 1e−6 → `scale=1e9`, `unit="nA"`
- Median < 1e−3 → `scale=1e6`, `unit="µA"`
- else → `scale=1e3`, `unit="mA"`

### SICC / CDYN — zero filtering

Values `≤ 0` are filtered out before stats and charting (invalid readings).

---

## 14. Collapse / Expand

```js
function togglePdlyP(n) {
  _PDLY_P_COLLAPSED[n] = !_PDLY_P_COLLAPSED[n];
  var body = document.getElementById('pdlyp' + n + '-body');
  var btn  = document.getElementById('pdlyp' + n + '-toggle');
  if (body) body.style.display = _PDLY_P_COLLAPSED[n] ? 'none' : '';
  if (btn)  btn.innerHTML = _PDLY_P_COLLAPSED[n] ? '&#9654;' : '&#9660;';
  //  ▶ = &#9654; (collapsed)  |  ▼ = &#9660; (expanded)
}
```

---

## 15. Complete Python Example

```python
from generate_pcm_html import generate_html

product_setup = {
    "title": "NVL816 PCM Dashboard",
    "subtitle": "Run 2026-05-19",
    "groups": [
        {"name": "Propagation Delay", "patterns": ["Td_*"]},
        {"name": "Vts N-FET",         "patterns": ["Vts_RN*", "Vts_N*"]},
        {"name": "SICC",              "patterns": ["SICC_*"]},
    ]
}

pcm_panels = {
    "distribution": [
        {
            "label": "Key Prop Delays",
            "params": ["Td_RJ4u", "Td_RK4u", "Poff_RJ4u"]
        },
        {
            "label": "SICC Ring",
            "params": ["SICC_RING_*_SDS", "SICC_CORE_*_SDS"]
        },
    ],
    "xy": [...]
}

generate_html(df, product_setup, "output.html",
              spec_lookup=spec_lookup, pcm_panels=pcm_panels)
```

---

## 16. Key Functions Reference

| Function | Purpose |
|---|---|
| `buildPropDelayTab()` | Master render; loops pn=1..N then custom panel |
| `_buildPdlyPanelBar(pn, allParams, sel, grp, h)` | Returns dark-blue bar + pills HTML |
| `_buildPdlyCards(params, ak, gby)` | Returns 2-col SVG card grid HTML |
| `togglePdlyP(n)` | Collapse / expand panel n |
| `toggleGbyP(pn, field)` | Toggle a group-by field for panel pn |
| `setPdlyGrpP(pn, grp)` | Set group filter for panel pn; resets `_PDLY_SEL_P[pn]` |
| `togglePdlyParamP(pn, p)` | Toggle param in/out of `_PDLY_SEL_P[pn]` |
| `_pdlyPDefault(pn)` | Returns default param `Set` for panel pn |
| `_pdlyAllParamsForP(pn)` | All params available given current group filter |
| `_pdlyPDropToggle(pn)` | Open / close the "Other params" dropdown |
| `_pdlyPDropSearch(pn, val)` | Filter dropdown list |
| `_pdlyPDropSelAll(pn)` | Select all visible items in dropdown |
| `_pdlyPDropClrAll(pn)` | Deselect all visible items in dropdown |
| `_pdlyPBuildDropList(pn)` | Rebuild the checkbox list inside dropdown |
| `_pdlyPDropCheck(pn, p)` | Toggle one param + update trigger button state |
| `_grpKeyWith(r, gby)` | Group-key for a PCM_ROWS entry with explicit gby |
| `_cMapWith(gby)` | Colour map `{map, keys}` for an explicit gby array |
| `downloadPdlyCSV(param)` | Download die-values as CSV for one param |
