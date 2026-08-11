# XY Panel — Full Implementation Reference

**Source file:** `code/dashboard/etest-dashboard/src/generate_pcm_html.py`  
**JavaScript render functions:** `fpBuild(pid)`, `_fpRenderChart(pid)`  
**Python HTML generator:** `generate_html()` / `_buildFpPanels()`

---

## 1. Overview

The XY Plot tab contains N side-by-side paired panels. Each panel has two halves (A and B), each independently configurable. `PCM_XY_PANELS` drives the initial layout.

Each half is called a "sub-panel" and identified by `pid = "fp{panelIndex}{side}"` (e.g. `"fp0a"`, `"fp0b"`, `"fp1a"`, …).

---

## 2. Data Schema

Same `PCM_ROWS` as the Distribution panel. Additional per-row fields used by XY:

| Field | Used as |
|---|---|
| `lot` | Lot ID for group-by and tooltip |
| `wafer` | Wafer for group-by and tooltip |
| `layout` | Layout for group-by |
| `material` | Material for group-by |
| `param` | Y-axis parameter name |
| `median` | Per-wafer median (plotted as one dot) |
| `die_values` | Per-die values (plotted when "Per die" checked) |

`PCM_PARAM_META` provides unit strings for axis labels.

---

## 3. Python Configuration

### `pcm_panels["xy"]` JSON

```json
{
  "xy": [
    {
      "label": "Prop Delay vs Vts",
      "x": "Vts_RN_0107",
      "ys": ["Td_RJ4u", "Td_RK4u"],
      "height": 400
    },
    {
      "label": "SICC vs Temperature",
      "x": "Temp",
      "ys": ["SICC_RING_0.95_SDS"],
      "height": 350
    }
  ]
}
```

| Key | Type | Default | Description |
|---|---|---|---|
| `label` | string | `"Panel {i+1}"` | Title displayed in panel header |
| `x` | string \| null | `null` | Initial X parameter |
| `ys` | string[] \| null | `null` | Initial Y parameter(s) |
| `height` | int | `400` | SVG height in pixels |

---

## 4. HTML DOM Structure (Python-generated)

One block per configured XY panel (i = 0..N−1):

```html
<!-- Panel wrapper — border-bottom:3px solid #bcd on all but the last -->
<div style="flex-shrink:0; border-bottom:3px solid #bcd">

  <!-- Green collapse/expand header -->
  <div style="background:#1a6e2b;
              border-bottom:1px solid #bcd;
              padding:4px 10px;
              display:flex; align-items:center; gap:8px;
              cursor:pointer"
       onclick="toggleFp({i})">

    <!-- Toggle button -->
    <button id="fptog{i}"
            style="border:none; background:none; cursor:pointer;
                   font-size:15px; color:#fff; padding:0 4px; line-height:1"
            title="Collapse/Expand">
      ▼    <!-- ▼ = &#9660; expanded | ▶ = &#9654; collapsed -->
    </button>

    <!-- Panel title -->
    <span style="font-size:15px; font-weight:bold; color:#fff">
      ⊙ Panel {i+1} — {label}    <!-- ⊙ = &#9673; -->
    </span>
  </div>

  <!-- Body: flex row containing left and right halves -->
  <div id="fp{i}-body"
       style="display:flex; flex-direction:row; min-height:0">

    <!-- Left half (sub-panel A) -->
    <div id="fp{i}a-wrap"
         style="flex:1; border-right:2px solid #dde;
                min-width:0; display:flex; flex-direction:column">
      <!-- fpBuild("fp{i}a") renders here -->
    </div>

    <!-- Right half (sub-panel B) -->
    <div id="fp{i}b-wrap"
         style="flex:1; min-width:0; display:flex; flex-direction:column">
      <!-- fpBuild("fp{i}b") renders here -->
    </div>
  </div>

</div>
```

---

## 5. JavaScript State Variables

```js
// Per-sub-panel state object
// _FP_ST[pid].x     — selected X param name (string | null)
// _FP_ST[pid].xgrp  — X param group filter ('' = all)
// _FP_ST[pid].ys    — selected Y params (string[] | null = none)
// _FP_ST[pid].ygrp  — Y param group filter ('' = all)
// _FP_ST[pid].logx  — bool  (X axis log scale)
// _FP_ST[pid].logy  — bool  (Y axis log scale)
// _FP_ST[pid].perdie — bool (plot per-die values, not wafer medians)
// _FP_ST[pid].trend — 'none' | 'ols' | 'theilsen'
// _FP_ST[pid].gby   — string[] (group-by fields, same as distribution)
// _FP_ST[pid].h     — number (SVG height in pixels, default 400)
// _FP_ST[pid].xmin  — number | '' (manual X min)
// _FP_ST[pid].xmax  — number | '' (manual X max)
// _FP_ST[pid].ymin  — number | '' (manual Y min)
// _FP_ST[pid].ymax  — number | '' (manual Y max)
var _FP_ST = {};
```

Initialised from `PCM_XY_PANELS` config at page load:

```js
PCM_XY_PANELS.forEach(function(cfg, i) {
  ['a','b'].forEach(function(side) {
    var pid = 'fp' + i + side;
    _FP_ST[pid] = {
      x: cfg.x || null, xgrp: '', ys: cfg.ys || null, ygrp: '',
      logx: false, logy: false, perdie: false, trend: 'none',
      gby: [], h: cfg.height || 400,
      xmin:'', xmax:'', ymin:'', ymax:''
    };
  });
});
```

---

## 6. Control Bar (`fpBuild`)

Built entirely in JavaScript, injected into `fp{i}{s}-wrap`.

### Bar container (light-grey strip)

```html
<div style="display:flex; flex-direction:column; flex-shrink:0;
            background:#f8f9fa; border-bottom:1px solid #dde;
            padding:5px 10px; gap:4px">
```

### Row 1 — X/Y parameter selectors

```html
<div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap">
```

#### X-param display label (leftmost)

```html
<!-- When X is selected: -->
<span style="font-size:12px; font-weight:bold; color:#2c3e50">
  ✗ {xParam} ({name}) — {unit}    <!-- ✗ = &#10799; -->
</span>

<!-- When X is null: -->
<span style="font-size:12px; font-weight:bold; color:#2c3e50">
  Select X parameter…
</span>
```

#### X group selector

```html
<span style="font-size:11px; display:flex; align-items:center; gap:2px">X grp:</span>
<select style="font-size:11px; padding:1px 3px; border-radius:3px; border:1px solid #ccc"
        onchange="_FP_ST['{pid}'].xgrp = this.value;
                  _FP_ST['{pid}'].x = _fpAllX('{pid}')[0] || '';
                  fpBuild('{pid}')">
  <option value="">All</option>
  <!-- One <option> per PCM_GROUPS entry -->
  <option value="{group}">{group}</option>
</select>
```

#### X param selector

```html
<span style="font-size:11px; display:flex; align-items:center; gap:2px">X:</span>
<select style="font-size:11px; padding:1px 3px; border-radius:3px;
               border:1px solid #ccc; max-width:180px"
        onchange="_FP_ST['{pid}'].x = this.value || null; fpBuild('{pid}')">
  <!-- Optional placeholder when no X selected: -->
  <option value="">-- select X --</option>
  <!-- One option per param in current X group -->
  <option value="{p}" {selected}>{p}</option>
</select>
```

`max-width:180px` prevents long param names from expanding the bar.

#### Vertical divider between X and Y sections

```html
<span style="width:1px; background:#ccc; align-self:stretch; margin:0 1px"></span>
```

#### Y group selector

```html
<span style="font-size:11px; display:flex; align-items:center; gap:2px">Y grp:</span>
<select style="font-size:11px; padding:1px 3px; border-radius:3px; border:1px solid #ccc"
        onchange="_FP_ST['{pid}'].ygrp = this.value;
                  _FP_ST['{pid}'].ys = null;
                  fpBuild('{pid}')">
  <option value="">All</option>
  <option value="{group}">{group}</option>
</select>
```

#### Y dropdown trigger button

```html
<span style="font-size:11px; position:relative; display:inline-block">
  Y:&nbsp;
  <button id="{pid}-y-btn"
          onclick="_fpYDropToggle('{pid}')"
          style="font-size:11px; padding:1px 6px; border-radius:3px;
                 border:1px solid #ccc; background:#fff; cursor:pointer;
                 min-width:90px; max-width:200px;
                 text-align:left; overflow:hidden;
                 text-overflow:ellipsis; white-space:nowrap">
    {param}           <!-- 1 param selected → param name -->
    {n} Y params      <!-- n>1 params → "{n} Y params" -->
    Select Y…         <!-- nothing selected -->
  </button>
```

#### Y dropdown panel

```html
  <div id="{pid}-y-drop"
       style="display:none; position:absolute; top:100%; left:0;
              z-index:9999; background:#fff; border:1px solid #ccc;
              border-radius:4px; box-shadow:0 4px 12px rgba(0,0,0,0.15);
              min-width:260px; max-width:400px">

    <!-- Header bar -->
    <div style="display:flex; align-items:center; gap:4px;
                padding:4px 5px; border-bottom:1px solid #e8e8e8;
                background:#f5f5f5">
      <input id="{pid}-y-srch" placeholder="Search…"
             oninput="_fpYSearch('{pid}', this.value)"
             style="flex:1; font-size:11px; padding:2px 5px;
                    border:1px solid #ccc; border-radius:3px">
      <button onclick="_fpYSelAll('{pid}')"
              style="font-size:10px; padding:1px 5px; border-radius:3px;
                     border:1px solid #bbb; background:#e8f0fe;
                     cursor:pointer">All</button>
      <button onclick="_fpYClrAll('{pid}')"
              style="font-size:10px; padding:1px 5px; border-radius:3px;
                     border:1px solid #bbb; background:#fef0e8;
                     cursor:pointer">Clr</button>
    </div>

    <!-- Param list -->
    <div id="{pid}-y-list"
         style="max-height:240px; overflow-y:auto; padding:3px 0">
      <label style="display:flex; align-items:center; gap:5px;
                    padding:2px 6px; cursor:pointer; border-radius:3px;
                    font-size:11px; white-space:nowrap"
             onmouseover="this.style.background='#e8f0fe'"
             onmouseout="this.style.background=''">
        <input type="checkbox" style="cursor:pointer"
               onchange="_fpToggleY('{pid}', '{p}', this.checked)"
               {checked}>
        <b style="font-size:11px">{param}</b>
        <span style="color:#888; font-size:10px"> ({name})</span>
      </label>

      <!-- When search has no results: -->
      <div style="padding:6px; color:#aaa; font-size:11px">No matches</div>
    </div>
  </div>
</span>
```

Y dropdown header button styles:

| Button | `background` | Action |
|---|---|---|
| All | `#e8f0fe` | `_fpYSelAll(pid)` — select all visible |
| Clr | `#fef0e8` | `_fpYClrAll(pid)` — deselect all visible |

---

### Row 2 — Display options

```html
<div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap">
```

#### Log / Per-die checkboxes

```html
<!-- logX -->
<label style="font-size:11px; cursor:pointer;
              display:flex; align-items:center; gap:2px">
  <input type="checkbox"
         onchange="_FP_ST['{pid}'].logx = this.checked; fpBuild('{pid}')"
         {checked}>
  logX
</label>

<!-- logY — identical structure -->
<label ...><input ... onchange="_FP_ST['{pid}'].logy = ...">logY</label>

<!-- Per die -->
<label ...><input ... onchange="_FP_ST['{pid}'].perdie = ...">Per die</label>
```

#### Trend radio group

```html
<span style="width:1px; background:#ccc; align-self:stretch; margin:0 2px"></span>
<span style="font-size:11px; color:#555">Trend:</span>

<!-- None radio -->
<label style="font-size:11px; cursor:pointer;
              display:flex; align-items:center; gap:2px">
  <input type="radio" name="{pid}-trend" value="none"
         onchange="_FP_ST['{pid}'].trend = this.value; fpBuild('{pid}')"
         {checked}>
  None
</label>

<!-- OLS radio — value="ols" label="OLS" -->
<!-- T-S radio  — value="theilsen" label="T-S" -->
```

All three radios share `name="{pid}-trend"` to form a single radio group per sub-panel.

#### Group-by checkboxes

```html
<span style="width:1px; background:#ccc; align-self:stretch; margin:0 2px"></span>
<b style="font-size:11px; color:#555">Gby:</b>

<label style="font-size:11px; cursor:pointer;
              display:flex; align-items:center; gap:2px">
  <input type="checkbox" value="none"
         onchange="toggleGbyFP('{pid}', 'none')"
         {checked}>None</label>

<!-- Same pattern for: Lot (value="lot") | Wfr (value="wafer") |
                       Lyt (value="layout") | Mat (value="material") -->
```

All 5 group-by checkboxes:

| Label | `value` | `onchange` |
|---|---|---|
| `None` | `"none"` | `toggleGbyFP(pid,'none')` |
| `Lot` | `"lot"` | `toggleGbyFP(pid,'lot')` |
| `Wfr` | `"wafer"` | `toggleGbyFP(pid,'wafer')` |
| `Lyt` | `"layout"` | `toggleGbyFP(pid,'layout')` |
| `Mat` | `"material"` | `toggleGbyFP(pid,'material')` |

---

### Row 3 — Axis ranges, height, CSV

```html
<div style="display:flex; align-items:center; gap:5px; flex-wrap:wrap">
```

#### X range inputs

```html
<span style="font-size:11px; color:#555">X:</span>
<input type="number" placeholder="auto" title="X min"
       value="{xmin || ''}"
       oninput="_FP_ST['{pid}'].xmin = this.value; fpBuild('{pid}')"
       style="width:60px; font-size:11px; padding:1px 3px">
<span style="font-size:10px; color:#aaa">–</span>    <!-- – = &#8211; -->
<input type="number" placeholder="auto" title="X max"
       value="{xmax || ''}"
       oninput="_FP_ST['{pid}'].xmax = this.value; fpBuild('{pid}')"
       style="width:60px; font-size:11px; padding:1px 3px">
```

#### Divider + Y range inputs (same structure as X)

#### Height slider

```html
<span style="width:1px; background:#ccc; align-self:stretch; margin:0 3px"></span>
<span style="font-size:11px; display:flex; align-items:center; gap:3px">
  H
  <input type="range" min="200" max="1000" step="25" value="{h}"
         oninput="_FP_ST['{pid}'].h = +this.value;
                  document.getElementById('{pid}-h-val').textContent = this.value + 'px';
                  fpBuild('{pid}')"
         style="width:70px; accent-color:#3498db">
  <span id="{pid}-h-val"
        style="min-width:30px; font-size:10px; color:#555">
    {h}px
  </span>
</span>
```

Height slider bounds: `min=200`, `max=1000`, `step=25`.

#### CSV download button

```html
<span style="width:1px; background:#ccc; align-self:stretch; margin:0 2px"></span>
<button onclick="_fpDownloadCSV('{pid}')"
        title="Download CSV"
        style="padding:2px 8px; font-size:10px; font-weight:bold;
               border:none; border-radius:3px;
               background:#27ae60; color:#fff; cursor:pointer"
        onmouseover="this.style.background='#1e8449'"
        onmouseout="this.style.background='#27ae60'">
  ⬇ CSV    <!-- ⬇ = &#11015; -->
</button>
```

### Chart container (below bar)

```html
<div id="{pid}-cont"
     style="flex:1; overflow-y:auto; padding:0 8px 8px">
  <!-- _fpRenderChart(pid) renders SVG here -->
</div>
```

---

## 7. SVG Scatter Chart (`_fpRenderChart`)

### Canvas dimensions

| Variable | Value | Notes |
|---|---|---|
| `svgW` | `820` | Fixed viewBox width |
| `svgH` | `_FP_ST[pid].h` | Default 400, range 200–1000 |
| `ML` | `90` | Left margin |
| `MR` | `30` | Right margin |
| `MT` | `40` | Top margin |
| `MB` | `65` | Bottom margin (single Y-axis) |
| `MB` | `88` | Bottom margin (multi-Y legend) |
| `plotW` | `700` | `820 − 90 − 30` |
| `plotH` | `svgH − MT − MB` | |

### SVG element

```html
<svg id="{pid}-svg"
     width="100%" height="{svgH}" viewBox="0 0 820 {svgH}"
     style="display:block; cursor:crosshair">
```

`cursor:crosshair` — always; the crosshair drag cursor is overlaid by `_initDragCursorsXY`.

### Background rects

```html
<rect width="820" height="{svgH}" fill="#f8f9fa"/>
<rect x="90" y="40" width="700" height="{plotH}"
      fill="#fff" stroke="#ccc" stroke-width="1"/>
```

### X-axis grid and tick labels — 7 ticks (xi = 0..6)

```
xv  = xlo + (xhi − xlo) × xi / 6
xpv = (ML + xi/6 × plotW).toFixed(1)
```

```html
<!-- Grid line -->
<line x1="{xpv}" y1="40" x2="{xpv}" y2="{MT+plotH}"
      stroke="rgba(0,0,0,0.08)" stroke-width="0.8"/>

<!-- Tick label — y = MT + plotH + 20 -->
<text x="{xpv}" y="{MT+plotH+20}"
      text-anchor="middle" font-size="13" fill="#333">
  {label}
  <!-- Linear: _fmt(xv)  |  Log: "10^{n}" if integer power, else _fmt(10^xv) -->
  <!-- Td_ params append "%" when not log scale -->
</text>
```

### Y-axis grid and tick labels — 6 lines (yi = 0..5)

```
yv  = ylo + (yhi − ylo) × yi / 5
ypv = (MT + plotH × (1 − yi/5)).toFixed(1)
```

```html
<!-- Grid line -->
<line x1="90" y1="{ypv}" x2="{ML+plotW}" y2="{ypv}"
      stroke="rgba(0,0,0,0.08)" stroke-width="0.8"/>

<!-- Tick label — x = ML - 6 = 84 -->
<text x="84" y="{ypv}"
      text-anchor="end" dominant-baseline="middle"
      font-size="13" fill="#333">{_fmt(yv)}</text>
```

### Axis padding

```js
var xpad = xrng * 0.08;   // 8% padding on each side
var ypad = yrng * 0.08;
xlo -= xpad; xhi += xpad;
ylo -= ypad; yhi += ypad;
// Manual overrides from _FP_ST[pid].xmin/xmax/ymin/ymax take precedence
```

### Scatter dots

Each point: a circle approximated as an SVG path for batch rendering:

```
path "M{cx},{cy}m-3,0a3,3,0,1,0,6,0a3,3,0,1,0,-6,0"
  → radius 3 circle centred at (cx, cy)
```

Batched by colour into one `<path>` per colour group:

```html
<path d="{all paths for this colour}"
      fill="{colour}" fill-opacity="0.55" stroke="none"/>
```

### Trend lines

Calculated via `_olsFit(pts)` or `_theilsenFit(pts)`:

```html
<line x1="{x1px}" y1="{y1px}" x2="{x2px}" y2="{y2px}"
      stroke="{colour}" stroke-width="2"
      stroke-dasharray="7,3" opacity="0.75"/>
```

Line is clipped to the plot bounds (does not extend into margins).

### X-axis label

```html
<text x="{ML + plotW/2}" y="{MT + plotH + 36}"
      text-anchor="middle" font-size="13" fill="#333">
  {xParam} ({unit})    <!-- unit omitted if empty string -->
</text>
```

Position: `y = MT + plotH + 20 + 16`.

### Y-axis label (single Y param)

```html
<text transform="rotate(-90)"
      x="{-(MT + plotH/2)}" y="16"
      text-anchor="middle" font-size="13" fill="#333">
  {yParam} ({unit})
</text>
```

### Multi-Y legend (multiple Y params selected)

Drawn below the chart when `ys.length > 1`:

```
legY   = MT + plotH + 20 + 34
itemW  = min(180, floor(plotW / n))
```

```html
<!-- For each Y param i: -->
<rect x="{ML + i×itemW}" y="{legY}"
      width="10" height="10" rx="2" fill="{colour}"/>
<text x="{ML + i×itemW + 14}" y="{legY + 5}"
      dominant-baseline="middle"
      font-size="11" fill="#333">
  {yParam}
</text>
```

### N label (top-left inside plot area)

```html
<text x="{ML + 4}" y="{MT − 6}"
      font-size="10" fill="#999">
  n={pts.length}
</text>
```

Position: `x=94`, `y=34`.

---

## 8. Tooltip

### CSS (applied via JS to a single shared `<div>` appended to `<body>`)

```css
position: fixed;
background: rgba(20,28,40,0.93);
color: #ecf0f1;
font-size: 12px;
padding: 5px 11px;
border-radius: 5px;
pointer-events: none;
z-index: 9999;
display: none;
white-space: nowrap;
box-shadow: 0 2px 8px rgba(0,0,0,.4);
border: 1px solid #4a6278;
```

Created lazily by `_getTT()`.

### Positioning

```js
tt.style.left = (e.clientX + 14) + 'px';
tt.style.top  = (e.clientY - 48) + 'px';
```

### Activation radius

Tooltip appears when the mouse is within **22 pixels** of the nearest data point (Euclidean distance in SVG coordinates).

### Content

Single-Y mode:
```html
<b>{lot} / {wafer}</b><br>
X: {x} {xUnit}<br>
Y: {y}
```

Multi-Y mode (one `<br>` line per Y param):
```html
<b>{lot} / {wafer}</b><br>
X: {x} {xUnit}<br>
<b>{yParam1}</b>: {y1}<br>
<b>{yParam2}</b>: {y2}
```

---

## 9. Crosshair / Hover Cursors

Initialised by `_initDragCursorsXY(svgEl, pid, ML, MT, plotW, plotH, xlo, xhi, ylo, yhi, fmtX, fmtY)`.

- **Two SVG `<line>` elements** (horizontal + vertical), styled:
  `stroke:#e74c3c; stroke-width:1; stroke-dasharray:3,3; pointer-events:none`
- Two `<text>` elements showing the X and Y values at the cursor position.
- Both lines are **always visible while the mouse hovers inside the plot area** (between `ML/MT` and `ML+plotW/MT+plotH` in viewBox coordinates).
- Hidden when the mouse leaves the SVG (`mouseleave`).

```js
// Always show on hover — no mousedown required
svgEl.addEventListener('mousemove', function(e) {
  var xy = getSVGCoords(e);
  showAt(xy[0], xy[1]);
});
svgEl.addEventListener('mouseleave', function() {
  [hLine, vLine, xText, yText].forEach(function(el) {
    el.setAttribute('visibility', 'hidden');
  });
});
```

`showAt(cx, cy)` hides all four elements when `cx/cy` is outside the plot bounds, so the crosshair disappears when the cursor slides into the margin area.

**Note:** each sub-panel (A and B) has its own independent crosshair, so two XY sub-panels = two independent crosshairs.

---

## 10. Trend Line Algorithms

### OLS (`_olsFit`)

```js
// Ordinary least squares: y = m*x + b
var sx=0,sy=0,sxx=0,sxy=0,n=pts.length;
pts.forEach(p => { sx+=p[0]; sy+=p[1]; sxx+=p[0]*p[0]; sxy+=p[0]*p[1]; });
var m = (n*sxy - sx*sy) / (n*sxx - sx*sx);
var b = (sy - m*sx) / n;
```

### Theil-Sen (`_theilsenFit`)

```js
// Median of all pairwise slopes
var slopes = [];
for (var i=0; i<pts.length; i++)
  for (var j=i+1; j<pts.length; j++)
    if (pts[j][0] !== pts[i][0])
      slopes.push((pts[j][1]-pts[i][1]) / (pts[j][0]-pts[i][0]));
var m = _med(slopes);
// intercept = median(yi - m*xi)
var intercepts = pts.map(p => p[1] - m*p[0]);
var b = _med(intercepts);
```

Theil-Sen is robust to outliers. Limited to `min(pts.length, 300)` points for performance.

---

## 11. Group-By System

Same mechanism as the Distribution panel. `toggleGbyFP(pid, field)`:

```js
function toggleGbyFP(pid, field) {
  var arr = _FP_ST[pid].gby || (_FP_ST[pid].gby = []);
  if (field === 'none') {
    arr.splice(0);
  } else {
    var i = arr.indexOf(field);
    if (i >= 0) arr.splice(i, 1);
    else arr.push(field);
  }
  fpBuild(pid);
}
```

Group keys and colour palette: identical to distribution panel (`_grpKeyWith`, `_cPal`).

---

## 12. Collapse / Expand

```js
function toggleFp(n) {
  var body = document.getElementById('fp' + n + '-body');
  var btn  = document.getElementById('fptog' + n);
  var collapsed = (body.style.display === 'none');
  body.style.display = collapsed ? 'flex' : 'none';
  btn.innerHTML     = collapsed ? '&#9660;' : '&#9654;';
  //  ▼ = &#9660; (expanded, flex)  |  ▶ = &#9654; (collapsed, none)
}
```

---

## 13. CSV Download

`_fpDownloadCSV(pid)`:

Generates a CSV with columns: `lot, wafer, layout, material, {xParam}, {y1}, {y2}, ...`  
One row per wafer (median values), or one row per die when "Per die" is checked.  
Filename: `xy_{xParam}_vs_{y1}[_y2...].csv`

---

## 14. Helper Functions Reference

| Function | Purpose |
|---|---|
| `fpBuild(pid)` | Rebuild entire control bar + chart for one sub-panel |
| `_fpRenderChart(pid)` | Build scatter SVG; inject into `{pid}-cont` |
| `_fpAllX(pid)` | All available X params given current X group filter |
| `_fpAllY(pid)` | All available Y params given current Y group filter |
| `_fpYDropToggle(pid)` | Open / close the Y dropdown |
| `_fpYSearch(pid, val)` | Filter Y dropdown list |
| `_fpYSelAll(pid)` | Select all visible Y params |
| `_fpYClrAll(pid)` | Deselect all visible Y params |
| `_fpToggleY(pid, p, checked)` | Toggle one Y param + update button label |
| `_fpDownloadCSV(pid)` | Download scatter data as CSV |
| `_olsFit(pts)` | Ordinary least squares → `{m, b}` |
| `_theilsenFit(pts)` | Theil-Sen robust regression → `{m, b}` |
| `_initDragCursorsXY(...)` | Install SVG crosshair drag handler |
| `toggleGbyFP(pid, field)` | Toggle group-by field for sub-panel pid |
| `_getTT()` | Return (or create) shared tooltip div |
| `_fmt(v)` | Format a number for axis labels and tooltips |

---

## 15. Complete Python Example

```python
pcm_panels = {
    "distribution": [...],
    "xy": [
        {
            "label": "Prop Delay vs Vts N-FET",
            "x": "Vts_RN_0107",
            "ys": ["Td_RJ4u", "Td_RK4u"],
            "height": 420
        },
        {
            "label": "SICC vs Temperature",
            "x": "Temp",
            "ys": ["SICC_RING_0.95_SDS"],
            "height": 350
        }
    ]
}

generate_html(df, product_setup, "output.html",
              spec_lookup=spec_lookup, pcm_panels=pcm_panels)
```

---

## 16. Global CSS Relevant to XY Panels

From `_CSS` (lines ~35–185 in the source file):

```css
/* Tab panel layout */
.tab-panel.active { display:flex; flex-direction:column; flex:1; min-height:0; overflow:hidden }

/* Three-panel page layout */
.three-panel { display:flex; flex-direction:row; flex:1; min-height:0; overflow:hidden; gap:0 }

/* Panel 3 (right content area that contains XY panels) */
#panel3 { flex:1; min-width:0; overflow-y:auto; overflow-x:hidden;
          background:#f0f2f5; padding:6px }

/* Autocomplete popup (used in XY param search fields) */
.xy-ac-wrap { position:relative; display:inline-block }
.xy-ac-pop  { position:absolute; z-index:9999; background:#fff;
              border:1px solid #bdc3c7; border-radius:4px;
              box-shadow:0 4px 14px rgba(0,0,0,.18);
              max-height:280px; overflow-y:auto;
              min-width:260px; width:max-content;
              display:none; top:100%; left:0 }
.xy-ac-item { padding:5px 10px; cursor:pointer; font-size:12px;
              white-space:nowrap; line-height:1.4 }
.xy-ac-item:hover,
.xy-ac-item.ac-hi { background:#d6eaff }
```
