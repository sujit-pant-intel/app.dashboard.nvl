# Variability Chart — Full Implementation Reference

**Source file:** `code/dashboard/etest-dashboard/src/generate_pcm_html.py`  
**Primary render function:** `_drawGroupChart(svgEl, grp, gi, params, ak, cm)`  
**Orchestrator:** `drawAllCharts()`  
**Location in page:** `#panel3` inside the Variability tab (`#tab-var`)

---

## 1. Three-Panel Layout Overview

The Variability tab uses a horizontal flex layout called `.three-panel`:

```
#tab-var
└── toolbar (flexbox bar — background:#1f3a50)
└── .three-panel  (flex:row, flex:1, min-height:0, overflow:hidden, gap:0)
    ├── #panel1       — persistent lot/wafer filter sidebar
    ├── .p1-resize    — 5px col-resize handle between panel1 and sp12
    ├── .sp12 #sp12   — 22px toggle/resize handle (◁▷ arrow button)
    ├── #panel2       — parameter stats table (hidden by default, width:400px)
    ├── .sp23 #sp23   — 5px col-resize handle between panel2 and panel3
    └── #panel3       — strip chart cards (flex:1, scrollable)
```

All panels share one horizontal flex row; `#panel3` is the right remainder (`flex:1`).

---

## 2. Three-Panel CSS

```css
/* Outer wrapper */
.three-panel { display:flex; flex-direction:row; flex:1; min-height:0;
               overflow:hidden; gap:0 }

/* Panel 1 — Lot/Wafer filter */
#panel1 { width:280px; min-width:140px; flex-shrink:0; background:#fff;
          display:flex; flex-direction:column;
          border-right:2px solid #d0d7de; overflow:hidden; position:relative }

/* P1 resize handle */
.p1-resize { width:5px; flex-shrink:0; background:#d0d7de;
             cursor:col-resize; align-self:stretch;
             transition:background .15s; user-select:none }
.p1-resize:hover, .p1-resize.dragging { background:#2980b9 }

/* sp12 — toggle + resize between p1 and p2 */
.sp12 { width:22px; flex-shrink:0; background:#ecf0f1; cursor:col-resize;
        display:flex; align-items:center; justify-content:center;
        border-left:1px solid #d0d7de; border-right:1px solid #d0d7de;
        user-select:none; position:relative; z-index:2 }
.sp12:hover { background:#d6eaff }
.sp12-btn { background:none; border:none; font-size:14px; cursor:pointer;
            color:#2c3e50; line-height:1; padding:0; display:block }

/* Panel 2 — parameter table */
#panel2 { width:400px; min-width:180px; flex-shrink:0; background:#fff;
          display:flex; flex-direction:column;
          overflow:hidden; border-right:2px solid #d0d7de; transition:width 0.15s }
#panel2.p2-hidden { width:0 !important; min-width:0 !important;
                    overflow:hidden; border:none }

/* sp23 — resize between p2 and p3 */
.sp23 { width:5px; flex-shrink:0; background:#d0d7de; cursor:col-resize;
        align-self:stretch; transition:background .15s; user-select:none }
.sp23:hover, .sp23.dragging { background:#2980b9 }

/* Panel 3 — strip chart cards */
#panel3 { flex:1; min-width:0; overflow-y:auto; overflow-x:hidden;
          background:#f0f2f5; padding:6px }
```

---

## 3. Panel 3 — Group Card Structure (Python-generated HTML)

One `.grp-card` per PCM group, rendered by Python into `grp_cards`:

```html
<div class="grp-card" id="card-grp-{gid}">

  <!-- Collapsible header bar — dark teal/navy, colour from _BANNER_COLS -->
  <div class="grp-card-hdr"
       onclick="var c=this.parentElement;
                c.classList.toggle('gc-collapsed');
                this.querySelector('.gc-tog').textContent =
                  c.classList.contains('gc-collapsed') ? '+' : '-'">

    <!-- Toggle icon: '-' open, '+' collapsed -->
    <span class="gc-tog"
          style="font-size:28px; line-height:1; width:24px;
                 display:inline-block; text-align:center">
      -
    </span>

    {group name}
    <span style="font-weight:normal; font-size:10px; opacity:0.7">
      ({n} params)
    </span>

    <!-- Per-group CSV download button (right-aligned via margin-left:auto) -->
    <button onclick="event.stopPropagation(); downloadGrpCSV('{ge}')"
            title="Download strip chart data as CSV"
            style="margin-left:auto; padding:2px 9px; font-size:10px;
                   font-weight:bold; border:none; border-radius:3px;
                   background:#27ae60; color:#fff; cursor:pointer; flex-shrink:0"
            onmouseover="this.style.background='#1e8449'"
            onmouseout="this.style.background='#27ae60'">
      ⬇ CSV    <!-- ⬇ = &#11015; -->
    </button>

  </div>

  <!-- Card body — visible when NOT gc-collapsed -->
  <div class="grp-card-body">
    <!-- SVG strip chart — drawn by _drawGroupChart() -->
    <svg id="svg-grp-{gid}" style="display:block; width:100%"></svg>

    <!-- Group-by colour legend — filled by _drawGroupChart() -->
    <div class="grp-legend" style="padding:2px 8px 6px"></div>
  </div>

</div>
```

`{gid}` = `re.sub(r'[^a-zA-Z0-9]', '_', g)` (group name with special chars → underscore).  
`{ge}`  = group name with `'` escaped to `\'` for inline JS.

### Group card CSS

```css
.grp-card {
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 1px 4px rgba(0,0,0,.10);
  margin-bottom: 10px;
  overflow: hidden;
  content-visibility: auto;               /* browser renders lazily */
  contain-intrinsic-size: 0 560px;        /* prevents CLS on lazy render */
}

.grp-card-hdr {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  font-size: 11px;
  font-weight: bold;
  color: #ecf0f1;
  background: #34495e;     /* uniform dark; Python uses _BANNER_COLS for SVG background */
  cursor: pointer;
  user-select: none;
}

.grp-card-body { padding: 0 }

/* Collapsed state: body hidden */
.grp-card.gc-collapsed .grp-card-body { display: none }
```

### Banner colour palette (`_BANNER_COLS`)

Eight colours cycled by group index (`gi % 8`). Used for the SVG background `fill` (not the card header — that is always `#34495e`):

```js
var _BANNER_COLS = [
  '#1a5276',   // 0 — deep navy blue
  '#117a65',   // 1 — dark teal
  '#6e2f8a',   // 2 — dark purple
  '#7d4e00',   // 3 — dark amber
  '#922b21',   // 4 — dark red
  '#1a6e2b',   // 5 — dark green
  '#1a3a72',   // 6 — navy
  '#7d4500',   // 7 — dark orange
];
```

> Note: `_BANNER_COLS` is defined but not actually used for the card background or SVG overall background in the current code. The SVG background rect uses `fill:"#f8f9fa"` (see Section 5). The `col` variable is read from `_BANNER_COLS[gi % ...]` but is assigned and not used further in the current source. The CSS header background is always `#34495e`.

---

## 4. `drawAllCharts()` — Rendering Queue

Charts are drawn one at a time via `requestAnimationFrame` to avoid blocking the UI thread:

```js
var _drawPending = null;

function drawAllCharts() {
  if (_drawPending) { cancelAnimationFrame(_drawPending); _drawPending = null; }

  var ak = activeKeys();    // Set of active row keys
  var cm = _cMap();         // { map: {groupKey→colour}, keys: [groupKey, ...] }
  var gi = 0;

  var queue = PCM_GROUPS.slice();   // copy of all group names

  function _next() {
    if (!queue.length) { _drawPending = null; return; }
    var grp = queue.shift();
    var gid = grp.replace(/[^a-zA-Z0-9]/g, '_');
    var svgEl = document.getElementById('svg-grp-' + gid);
    var card  = document.getElementById('card-grp-' + gid);

    if (!svgEl) { gi++; _drawPending = requestAnimationFrame(_next); return; }

    // Hidden group: collapse card, skip drawing
    if (!_GRP_VIS[grp]) {
      if (card) card.style.display = 'none';
      gi++;
      _drawPending = requestAnimationFrame(_next);
      return;
    }

    if (card) card.style.display = '';   // restore card if previously hidden

    var params = activeParamsForGroup(grp)
                   .filter(function(p) { return p in PCM_PARAM_META; });

    _drawGroupChart(svgEl, grp, gi, params, ak, cm);
    gi++;
    _drawPending = requestAnimationFrame(_next);
  }

  _drawPending = requestAnimationFrame(_next);
}
```

`drawAllCharts()` is called whenever anything changes (filter, group-by, group visibility, per-site toggle, height slider). It cancels any in-flight draw and restarts.

---

## 5. `_drawGroupChart()` — Complete SVG Construction

### Signature

```js
function _drawGroupChart(svgEl, grp, gi, params, ak, cm)
```

| Parameter | Type | Description |
|---|---|---|
| `svgEl` | DOM element | `<svg id="svg-grp-{gid}">` |
| `grp` | string | Group name |
| `gi` | number | Group index (0-based), used for `_BANNER_COLS` |
| `params` | string[] | Parameter names in display order |
| `ak` | Set\<string\> | Active row keys (from `activeKeys()`) |
| `cm` | `{map, keys}` | Colour map from `_cMap()` |

If `params` is empty or null: `svgEl.style.display = 'none'` and return early.

---

### 5.1 Dimensions

```js
var W    = Math.max(svgEl.parentElement ? svgEl.parentElement.clientWidth - 8 : 700, 300);
var ML   = 90;     // left margin (Y-axis labels + axis title)
var MR   = 80;     // right margin (spec line legend)
var MT   = 32;     // top margin
var MB   = 8;      // bottom margin

var xStep = Math.max(32, (W - ML - MR) / params.length);
var CW    = xStep * params.length;   // chart plot width
var CH    = _CHART_H;                // chart plot height (user-adjustable, default 480)

// X-label height: proportional to longest param name, clamped 140–300px
var xLblH = Math.max(140, Math.min(300,
  params.reduce(function(mx, p) { return Math.max(mx, p.length); }, 0) * 10 + 20
));

var H = MT + CH + xLblH + MB;       // total SVG height
```

SVG `viewBox`: `"0 0 {ML+CW+MR} {H}"`, `width="100%"`, `height={H}`.

| Variable | Fixed | Computed |
|---|---|---|
| `ML` | `90` | — |
| `MR` | `80` | — |
| `MT` | `32` | — |
| `MB` | `8` | — |
| `xStep` | — | `max(32, (W-170)/params.length)` |
| `CH` | — | `_CHART_H` (slider, default `480`) |
| `xLblH` | — | `clamp(140, maxParamLen×10+20, 300)` |
| `H` | — | `MT + CH + xLblH + MB` |

---

### 5.2 Coordinate Functions

```js
function xPos(i) { return ML + (i + 0.5) * xStep; }    // centre of column i
function yPos(v) { return MT + (1 - (v - ylo) / (yhi - ylo)) * CH; }
```

`yPos` maps value `ylo → MT+CH` (bottom), `yhi → MT` (top).

---

### 5.3 Y-Axis Range (`ylo`, `yhi`)

```js
// 1. Collect all displayed values (after unit conversion) for all params in group
var allVals = [];
PCM_ROWS.forEach(function(r) {
  if (params.indexOf(r.param) < 0) return;
  if (!ak.has(_rKey(r))) return;
  (r.die_values || []).forEach(function(v) {
    if (v == null || !isFinite(v)) return;
    var cv2 = _toDisplayVals(r.param, [v]);
    if (cv2.length) allVals.push(cv2[0]);
  });
});

// 2. Clip to P1–P99 (prevents leakage/outlier params from inflating range)
if (allVals.length >= 10) {
  var srt = allVals.slice().sort(function(a,b){ return a-b; });
  var p01 = srt[Math.floor(srt.length * 0.01)];
  var p99 = srt[Math.min(srt.length-1, Math.ceil(srt.length * 0.99))];
  if (p99 > p01) { dMin = p01; dMax = p99; }
}

// 3. Extend to include spec limits only if within 5× data range
var _dr = dMax - dMin || Math.abs(dMin) * 0.1 || 0.1;
params.forEach(function(pm) {
  var m = PCM_PARAM_META[pm] || {};
  if (m.lsl != null && m.lsl >= dMin - 5 * _dr) dMin = Math.min(dMin, m.lsl);
  if (m.usl != null && m.usl <= dMax + 5 * _dr) dMax = Math.max(dMax, m.usl);
});

// 4. Nice-step padding (15% headroom)
var rng = dMax - dMin || _dr, pad = rng * 0.15, ns = _niceStep(rng);
ylo = Math.floor((dMin - pad) / ns) * ns;
yhi = Math.ceil((dMax + pad) / ns) * ns;
```

Fallback when `allVals.length < 2`: `ylo = 0; yhi = 1`.

### `_niceStep(r)` — round-number grid step

```js
function _niceStep(r) {
  if (r <= 0 || !isFinite(r)) return 0.1;
  var m = Math.pow(10, Math.floor(Math.log10(r)));
  var s = r / m;
  return s < 1.5 ? m : s < 3 ? 2*m : s < 7 ? 5*m : 10*m;
}
```

---

### 5.4 Background Rects

```html
<!-- Full SVG background: light grey -->
<rect width="{ML+CW+MR}" height="{H}" fill="#f8f9fa"/>

<!-- Chart plot area: white with 1px grey border -->
<rect x="{ML}" y="{MT}" width="{CW}" height="{CH}"
      fill="white" stroke="#ccc" stroke-width="1"/>
```

---

### 5.5 Y-Axis Grid Lines and Labels

Grid lines use step-count loop to avoid float drift:

```js
var yStep  = _niceStep((yhi - ylo) / 5);      // target ~5 grid lines
var yStart = Math.ceil(ylo / yStep) * yStep;
var yGridN = Math.min(60, Math.ceil((yhi - yStart) / yStep) + 2);
```

For each grid line `yv = yStart + _yi × yStep` where `_yi ∈ [0, yGridN)` and `yv ≤ yhi`:

```html
<!-- Horizontal grid line -->
<line x1="{ML}" y1="{yp}" x2="{ML+CW}" y2="{yp}"
      stroke="rgba(0,0,0,0.07)" stroke-width="0.7"/>

<!-- Y-axis tick label -->
<text x="{ML-3}" y="{yp}"
      text-anchor="end" dominant-baseline="middle"
      font-size="16" font-weight="bold" fill="#111">
  {_fmt(yv)}
</text>
```

Grid line: `stroke:rgba(0,0,0,0.07)`, `stroke-width:0.7`.  
Tick label: `font-size:16`, `font-weight:bold`, `fill:#111`, positioned 3px left of plot edge.

### Y-Axis Title

```html
<text transform="translate(18, {MT + CH/2}) rotate(-90)"
      text-anchor="middle" dominant-baseline="middle"
      font-size="20" font-weight="bold" fill="#111">
  Value
</text>
```

Position: `x=18` (fixed), `y=MT+CH/2`, rotated 90° CCW. `font-size:20 bold fill:#111`.

---

### 5.6 Per-Parameter Column Elements

Rendered for every `param` at index `i`:

```js
var x1 = (xPos(i) - xStep * 0.45).toFixed(1);   // spec line left edge
var x2 = (xPos(i) + xStep * 0.45).toFixed(1);   // spec line right edge
```

**Selected parameter highlight** (when `SEL_PARAM === param`):

```html
<rect x="{xPos(i) - xStep/2}" y="{MT}"
      width="{xStep}" height="{CH}"
      fill="rgba(52,152,219,0.10)" stroke="#3498db" stroke-width="1.2"/>
```

`fill:rgba(52,152,219,0.10)` — translucent blue wash.  
`stroke:#3498db` — solid blue border, `stroke-width:1.2`.

**Alternating column tint** (odd-index columns only, when NOT selected):

```html
<rect x="{xPos(i) - xStep/2}" y="{MT}"
      width="{xStep}" height="{CH}"
      fill="rgba(0,0,0,0.02)"/>
```

Even-index columns: no background (transparent over white plot area).

**LSL line** (clipped to plot area — only drawn if `yL` is between `MT` and `MT+CH`):

```html
<line x1="{x1}" y1="{yL}" x2="{x2}" y2="{yL}"
      stroke="#c0392b" stroke-width="1.5"
      stroke-dasharray="4,3" opacity="0.85"/>
```

`stroke:#c0392b` (red), `stroke-width:1.5`, `stroke-dasharray:4,3`, `opacity:0.85`.  
Width spans `±45%` of `xStep` centred on the column.

**USL line** (same clipping rule):

```html
<line x1="{x1}" y1="{yU}" x2="{x2}" y2="{yU}"
      stroke="#2980b9" stroke-width="1.5"
      stroke-dasharray="4,3" opacity="0.85"/>
```

`stroke:#2980b9` (blue), same width/dash/opacity as LSL.

---

### 5.7 Data Dots — Batched SVG Paths

Dots are rendered using SVG arc path trick: **one `<path>` per colour** instead of one `<circle>` per dot. This keeps the DOM small when many lots are loaded.

**Dot radius: 2.5px** (`m-2.5,0a2.5,2.5,0,1,0,5,0a2.5,2.5,0,1,0,-5,0`).  
**Opacity:** `0.70`.

#### Per-column dot collection

```js
var _MAX_COL_DOTS = 500;   // subsample threshold per parameter column
```

For each `param` at index `i`:

1. Iterate `PCM_ROWS` — skip if `r.param !== param` or key not in `ak`.
2. Determine dot colour: `cm.map[_grpKey(r)] || _cPal(0)`.
3. Source values:
   - `_VAR_PER_SITE === true` → use `r.die_values` (all site measurements)
   - `_VAR_PER_SITE === false` → use `[r.median]` (single wafer median)
4. Convert via `_toDisplayVals(param, [v])`.
5. Compute `yp2 = yPos(dv[0])` — skip if outside `[MT, MT+CH]`.
6. Accumulate `col_dots` array: `{col, ri, vi, yp}`.

#### Deterministic subsampling (when `col_dots.length > 500`):

```js
var step = col_dots.length / _MAX_COL_DOTS;
var sampled = [];
for (var _s = 0; _s < _MAX_COL_DOTS; _s++)
  sampled.push(col_dots[Math.floor(_s * step)]);
col_dots = sampled;
```

#### Jitter

```js
var jitter = (_sRand(d.ri * 997 + d.vi) - 0.5) * xStep * 0.52;
var cx = +(xPos(i) + jitter).toFixed(1);
```

`_sRand(s) = frac(sin(s+1) × 10000)` — deterministic pseudo-random in [0,1).  
Jitter range: `±xStep × 0.26` (i.e. ±26% of column width).

#### Path emission

```js
// For each dot:
_dotPaths[d.col] += 'M' + cx + ',' + cy +
                    'm-2.5,0a2.5,2.5,0,1,0,5,0a2.5,2.5,0,1,0,-5,0';

// One <path> per colour:
Object.keys(_dotPaths).forEach(function(col) {
  p.push('<path d="' + _dotPaths[col] + '" fill="' + col + '" opacity="0.70"/>');
});
```

---

### 5.8 Median Diamond + Target Cross

Rendered **after** dots (on top layer) for each param:

**Median diamond:**

```html
<polygon points="{cx},{yp-7} {cx+7},{yp} {cx},{yp+7} {cx-7},{yp}"
         fill="#27ae60" stroke="#1a6e2b" stroke-width="1.2" opacity="0.92"/>
```

Half-size `ds = 7px`.  
`fill:#27ae60`, `stroke:#1a6e2b`, `stroke-width:1.2`, `opacity:0.92`.  
Skipped if median is null or outside `[MT, MT+CH]`.

The median is calculated fresh from all active die values for that param (not from `PCM_ROWS[i].median`):

```js
var vals = [];
PCM_ROWS.forEach(function(r) {
  if (r.param !== param || !ak.has(_rKey(r))) return;
  (r.die_values || []).forEach(function(v) {
    if (v != null && isFinite(v)) {
      var dv2 = _toDisplayVals(param, [v]);
      if (dv2.length) vals.push(dv2[0]);
    }
  });
});
var med = _med(vals);
```

**Target cross** (only when both `meta.lsl` and `meta.usl` are defined):

```js
var tgt = (meta.lsl + meta.usl) / 2;
var ts  = 6;   // arm half-length
```

```html
<!-- Horizontal arm -->
<line x1="{cx-6}" y1="{yT}" x2="{cx+6}" y2="{yT}"
      stroke="#f39c12" stroke-width="2.5"/>

<!-- Vertical arm -->
<line x1="{cx}" y1="{yT-6}" x2="{cx}" y2="{yT+6}"
      stroke="#f39c12" stroke-width="2.5"/>
```

`stroke:#f39c12` (amber), `stroke-width:2.5`. Only drawn if `yT ∈ [MT, MT+CH]`.

---

### 5.9 X-Axis Labels (rotated, below plot)

Labels start at `y = MT + CH + 4` (4px below plot bottom), rotated −48°.

```js
var lbl = isSortP
  ? /* sort param: use friendly name */
    (xmeta.name || param).length > 26
      ? (xmeta.name || param).slice(0, 25) + '…'
      : (xmeta.name || param)
  : /* PCM param: use raw column name */
    param.length > 22
      ? param.slice(0, 21) + '…'
      : param;
```

Truncation:
- Sort params: truncate at 26 chars → append `…` (U+2026)
- PCM params: truncate at 22 chars → append `…`

**Main label:**

```html
<text transform="translate({xPos(i)}, {MT+CH+4}) rotate(-48)"
      text-anchor="end" font-size="20" font-weight="bold" fill="#111">
  {lbl}
</text>
```

`font-size:20 bold fill:#111`, `text-anchor:end`, `rotate(-48deg)`.

**Sub-label** (friendly name in brackets, PCM params only, when `meta.name` exists and NOT a sort param):

```js
var nlbl = xmeta.name.length > 26 ? xmeta.name.slice(0, 25) + '…' : xmeta.name;
```

```html
<text transform="translate({xPos(i)}, {MT+CH+28}) rotate(-48)"
      text-anchor="end" font-size="13" fill="#5d6d7e">
  ({nlbl})
</text>
```

`font-size:13 fill:#5d6d7e`, 28px below main label origin, same rotation.

---

### 5.10 Right-Margin Legend

Positioned at `legX = ML + CW + 6`, `legY = MT + 4`.

```html
<!-- LSL dashed line sample -->
<line x1="{legX}" y1="{legY+5}" x2="{legX+18}" y2="{legY+5}"
      stroke="#e74c3c" stroke-width="2" stroke-dasharray="5,3"/>
<text x="{legX+21}" y="{legY+5}"
      dominant-baseline="middle" font-size="10" font-weight="bold" fill="#c0392b">
  LSL
</text>

<!-- USL dashed line sample -->
<line x1="{legX}" y1="{legY+20}" x2="{legX+18}" y2="{legY+20}"
      stroke="#2980b9" stroke-width="2" stroke-dasharray="5,3"/>
<text x="{legX+21}" y="{legY+20}"
      dominant-baseline="middle" font-size="10" font-weight="bold" fill="#2980b9">
  USL
</text>

<!-- Target dashed line sample -->
<line x1="{legX}" y1="{legY+35}" x2="{legX+18}" y2="{legY+35}"
      stroke="#f39c12" stroke-width="1.5" stroke-dasharray="2,2"/>
<text x="{legX+21}" y="{legY+35}"
      dominant-baseline="middle" font-size="10" fill="#d68910">
  Target
</text>

<!-- Median diamond sample -->
<polygon points="{legX+5},{legY+47} {legX+10},{legY+52} {legX+5},{legY+57} {legX},{legY+52}"
         fill="#27ae60" stroke="#1a6e2b" stroke-width="1"/>
<text x="{legX+13}" y="{legY+52}"
      dominant-baseline="middle" font-size="10" font-weight="bold" fill="#1a6e2b">
  Median
</text>
```

Legend item vertical spacing:

| Item | Y offset from `legY` |
|---|---|
| LSL line midpoint | `+5` |
| USL line midpoint | `+20` |
| Target line midpoint | `+35` |
| Median diamond centre | `+52` (`dy = sly+52`) |

Legend text starts at `x = legX + 21` (21px right of line end). All items right-aligned within the 80px `MR` margin.

---

### 5.11 Complete SVG Structure (render order)

All elements are pushed into array `p[]` then joined and set as `svgEl.innerHTML`:

```
[0]  Background rect (full SVG, #f8f9fa)
[1]  Plot area rect (white, stroke #ccc)
[2…] Y-grid lines
[3…] Y-tick labels
[4…] Per-param: selected highlight OR alternating tint rect
[5…] Per-param: LSL dashed line (if in range)
[6…] Per-param: USL dashed line (if in range)
[7…] Data dot paths (one <path> per group-by colour)
[8…] Per-param: median diamond
[9…] Per-param: target cross (if LSL+USL both defined)
[10…] X-axis labels (rotated text × params.length)
[11…] X-axis sub-labels (optional, PCM params with friendly name)
[12…] Right-margin legend (4 items: LSL, USL, Target, Median)
[13]  Y-axis title ("Value")
```

After setting `svgEl.innerHTML`, the function sets SVG attributes:

```js
svgEl.setAttribute('viewBox', '0 0 ' + (ML + CW + MR) + ' ' + H);
svgEl.setAttribute('width',   '100%');
svgEl.setAttribute('height',  H);
```

---

## 6. Group-By Colour Legend (HTML below SVG)

The `.grp-legend` `<div>` inside `.grp-card-body` is filled after the SVG:

```js
var legDiv = svgEl.parentElement
               ? svgEl.parentElement.querySelector('.grp-legend')
               : null;

if (cm.keys.length <= 1 && cm.keys[0] === 'All') {
  legDiv.innerHTML = '';   // no legend when not grouped
} else {
  var lh = '<div style="display:flex; flex-wrap:wrap; gap:6px 14px; ' +
           'align-items:center; padding:4px 8px">';
  cm.keys.forEach(function(k, i) {
    lh += '<span style="display:flex; align-items:center; gap:4px; ' +
          'font-size:11px; color:#2c3e50">'
        + '<span style="display:inline-block; width:12px; height:12px; ' +
          'background:' + cm.map[k] + '; border-radius:2px"></span>'
        + esc(k)
        + '</span>';
  });
  lh += '</div>';
  legDiv.innerHTML = lh;
}
```

Colour swatch: `12×12px`, `border-radius:2px`, `background:{groupColour}`.  
Label text: `font-size:11px; color:#2c3e50`.  
Container: `display:flex; flex-wrap:wrap; gap:6px 14px; padding:4px 8px`.

---

## 7. Tooltip

Added to each `<svg>` after rendering:

```js
svgEl.addEventListener('mousemove', function(e) {
  var tt = _getTT();
  var rect = svgEl.getBoundingClientRect();
  var vbH = H, vbW = ML + CW + MR;
  var scaleX = vbW / rect.width;
  var scaleY = vbH / rect.height;
  var mx = (e.clientX - rect.left) * scaleX;
  var my = (e.clientY - rect.top)  * scaleY;

  // Hide outside plot area
  if (mx < ML || mx > ML + CW || my < MT || my > MT + CH) {
    tt.style.display = 'none'; return;
  }

  var pi  = Math.floor((mx - ML) / xStep);     // column index
  if (pi < 0 || pi >= params.length) { tt.style.display = 'none'; return; }

  var yVal = yhi - (my - MT) / CH * (yhi - ylo);   // back-project Y value

  tt.innerHTML = '<b>' + esc(params[pi]) + '</b>&nbsp;&nbsp;Y = ' + _fmt(yVal);
  tt.style.left = (e.clientX + 14) + 'px';
  tt.style.top  = (e.clientY - 36) + 'px';
  tt.style.display = 'block';
});

svgEl.addEventListener('mouseleave', function() { _getTT().style.display = 'none'; });
```

**Tooltip element** (created once, appended to `document.body`):

```js
var _TT = null;
function _getTT() {
  if (!_TT) {
    _TT = document.createElement('div');
    _TT.style.cssText =
      'position:fixed;'
      + 'background:rgba(20,28,40,0.93);'
      + 'color:#ecf0f1;'
      + 'font-size:12px;'
      + 'padding:5px 11px;'
      + 'border-radius:5px;'
      + 'pointer-events:none;'
      + 'z-index:9999;'
      + 'display:none;'
      + 'white-space:nowrap;'
      + 'box-shadow:0 2px 8px rgba(0,0,0,.4);'
      + 'border:1px solid #4a6278';
    document.body.appendChild(_TT);
  }
  return _TT;
}
```

Tooltip position: `left = mouseX + 14px`, `top = mouseY - 36px`.  
Content: `<b>{paramName}</b>  Y = {formattedValue}`.

---

## 8. Group-By System

### Global `VAR_GBY` state

```js
var VAR_GBY = [];   // e.g. [] | ['lot'] | ['lot','wafer'] | ['material'] etc.
```

### `toggleGby(field)`

```js
function toggleGby(field) {
  if (field === 'none') { VAR_GBY = []; }
  else {
    var i = VAR_GBY.indexOf(field);
    if (i >= 0) VAR_GBY.splice(i, 1);    // remove
    else         VAR_GBY.push(field);      // add
  }
  // Sync ALL checkbox sets with class="vgb-cb"
  document.querySelectorAll('.vgb-cb').forEach(function(cb) {
    if (cb.value === 'none') cb.checked = VAR_GBY.length === 0;
    else cb.checked = VAR_GBY.indexOf(cb.value) >= 0;
  });
  rerender();
}
```

### `_grpKey(r)` — colour key for a row

```js
function _grpKey(r) {
  if (!VAR_GBY.length) return 'All';
  var parts = [];
  if (VAR_GBY.indexOf('lot')      >= 0) parts.push(r.lot     || '');
  if (VAR_GBY.indexOf('wafer')    >= 0) parts.push(String(r.wafer    || ''));
  if (VAR_GBY.indexOf('layout')   >= 0) parts.push(r.layout  || '');
  if (VAR_GBY.indexOf('material') >= 0) parts.push(r.material || '');
  return parts.join('/') || 'All';
}
```

### `_cMap()` — build colour assignment

```js
function _cMap() {
  var map = {}, keys = [];
  var ak = activeKeys();
  PCM_ROWS.forEach(function(r) {
    if (!ak.has(_rKey(r))) return;
    var k = _grpKey(r);
    if (!map[k]) { map[k] = _cPal(keys.length); keys.push(k); }
  });
  return { map: map, keys: keys };
}
```

Returns `{ map: {groupKey → hexColour}, keys: [groupKey, ...] }`.

### Colour palette `_CPALS`

15 colours, cycled by insertion order (`_cPal(i) = _CPALS[i % 15]`):

```js
var _CPALS = [
  '#2980b9',  // 0  — medium blue
  '#27ae60',  // 1  — medium green
  '#e67e22',  // 2  — orange
  '#8e44ad',  // 3  — purple
  '#c0392b',  // 4  — red
  '#16a085',  // 5  — dark teal
  '#f39c12',  // 6  — amber
  '#1abc9c',  // 7  — turquoise
  '#d35400',  // 8  — dark orange
  '#7f8c8d',  // 9  — grey
  '#3498db',  // 10 — light blue
  '#2ecc71',  // 11 — light green
  '#e74c3c',  // 12 — bright red
  '#9b59b6',  // 13 — medium purple
  '#f0a500',  // 14 — golden yellow
];
```

---

## 9. Per-Site vs Per-Wafer Toggle

```js
var _VAR_PER_SITE = true;   // default: show individual site measurements
```

```html
<input type="checkbox" id="var-persite-cb" checked
       onchange="_VAR_PER_SITE = this.checked; drawAllCharts()">
Per site
```

| Mode | `_VAR_PER_SITE` | Dot source | Dots per (lot, wafer) |
|---|---|---|---|
| Per site | `true` | `r.die_values` | One dot per PCM site |
| Per wafer | `false` | `[r.median]` | One dot (wafer median) |

---

## 10. Chart Height Slider

```html
<input id="chart-h-slider" type="range"
       min="150" max="1200" step="50" value="480"
       oninput="_CHART_H = +this.value;
                document.getElementById('chart-h-val').textContent = this.value + 'px';
                drawAllCharts()"
       style="width:100px; vertical-align:middle; accent-color:#3498db">
<span id="chart-h-val"
      style="min-width:34px; font-size:10px; color:#aed6f1">
  480px
</span>
```

```js
var _CHART_H = 480;   // current chart plot height in pixels
```

| Attribute | Value |
|---|---|
| `min` | 150 |
| `max` | 1200 |
| `step` | 50 |
| Default | 480 |
| `accent-color` | `#3498db` |
| Track width | `100px` |

---

## 11. `_fmt(v)` — Value Formatter

```js
function _fmt(v) {
  if (v == null || isNaN(v) || !isFinite(v)) return '';
  if (Math.abs(v) > 0 && (Math.abs(v) < 1e-4 || Math.abs(v) >= 1e7))
    return v.toExponential(3);       // e.g. "1.234e-7"
  return parseFloat(v.toPrecision(4)).toString();  // e.g. "1.234", "12.34", "0.001234"
}
```

---

## 12. `_toDisplayVals(param, vals)` — Unit Conversion

Applied before plotting all dot values and before computing chart median:

| Param pattern | Conversion |
|---|---|
| `Td_*` (case-insensitive) | `target / v × 100` → `% of target`; target = `meta.target ?? (lsl+usl)/2` |
| `Poff_*` or `Ioff_*` | multiply by `_leakageScale(vals).scale` → nA / µA / mA |
| All others | identity (return `vals` unchanged) |

`_leakageScale(vals)` auto-selects unit based on `max(|vals|)`:

| `max(|v|)` | Scale | Unit |
|---|---|---|
| `< 1e-6` | `1e9` | `nA` |
| `< 1e-3` | `1e6` | `µA` |
| `≥ 1e-3` | `1e3` | `mA` |

---

## 13. Data Source Structure

### `PCM_ROWS` — injected by Python

Each row is a per-(lot, wafer, param) record:

```js
{
  lot:        "LotID",
  wafer:      3,              // wafer number
  sort_wafer: "03",           // display wafer number (optional)
  layout:     "Layout Name",
  material:   "MaterialType",
  program:    "TestProgram",
  param:      "Td_RJ4u",
  n:          9,              // site count for this wafer
  median:     1.23e-10,       // per-wafer median
  std:        2.1e-12,
  cv:         1.7,            // Cv% as number
  min_val:    1.10e-10,
  max_val:    1.37e-10,
  die_values: [1.10e-10, 1.15e-10, ..., 1.37e-10]  // one per site
}
```

### `PCM_PARAM_META` — injected by Python

```js
{
  "Td_RJ4u": {
    name:   "Ring J 4µm",    // friendly name
    unit:   "s",
    lsl:    null,
    usl:    null,
    target: 1.2e-10,
    is_sort: false
  },
  ...
}
```

### `_rKey(r)` — row identity

```js
function _rKey(r) {
  return r.lot + '|' + (r.layout || '') + '|' + (r.program || '') + '|'
       + r.wafer + '|' + (r.material || '');
}
```

---

## 14. `downloadGrpCSV(grp)` — Per-Group Strip Chart CSV

```js
// Columns:
['Lot', 'Wafer', 'Program', 'Material', 'GroupBy',
 'Param', 'N', 'Median', 'Std', 'Spread (%)', 'Min', 'Max',
 'LSL', 'USL', 'Unit']
```

One row per active `(lot, wafer, param)` combination within the group.  
Filename: `pcm_grp_{group_name_sanitized}_{YYYY-MM-DDTHH-MM}.csv`.  
Sanitisation: `grp.replace(/[^a-zA-Z0-9]/g, '_')`.

---

## 15. Key Functions Reference

| Function | Signature | Purpose |
|---|---|---|
| `_drawGroupChart` | `(svgEl, grp, gi, params, ak, cm)` | Build and inject full SVG for one group |
| `drawAllCharts` | `()` | Orchestrate all groups via rAF queue |
| `_grpKey` | `(r)` → string | Map row to colour group key |
| `_cMap` | `()` → `{map, keys}` | Assign colours to group keys |
| `_cPal` | `(i)` → hex string | Cycle through `_CPALS[i % 15]` |
| `toggleGby` | `(field)` | Toggle group-by field; sync checkboxes; rerender |
| `_niceStep` | `(r)` → number | Round-number axis step for range `r` |
| `_fmt` | `(v)` → string | 4-sig-fig or exponential formatter |
| `_toDisplayVals` | `(param, vals)` → number[] | Unit conversion (Td_, leakage) |
| `_sRand` | `(s)` → float [0,1) | Deterministic jitter seed |
| `_getTT` | `()` → DOM element | Lazy-create global tooltip div |
| `_med` | `(arr)` → number | Median of numeric array |
| `_std` | `(arr)` → number | Sample std dev |
| `_safeMin` / `_safeMax` | `(arr)` → number | Min/max without spread operator |
| `activeKeys` | `()` → Set | Row keys for currently-selected wafers |
| `activeParamsForGroup` | `(grp)` → string[] | `PCM_GROUP_PARAMS[grp]` |
