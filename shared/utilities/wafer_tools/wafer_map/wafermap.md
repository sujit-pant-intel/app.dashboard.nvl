# wafer_map — SVG Wafer Map Renderer

## Purpose

Lightweight module that draws a **pretty SVG wafer map** inside any HTML
`<div>` — die squares, reticle shot outlines, and die-loc number overlays.
No Plotly, no pattern analysis, no popup.  The caller supplies the color and
tooltip logic; this module handles only the geometry.

---

## Location

```
utilities/wafer_tools/wafer_map/
    __init__.py       ← exports WAFERMAP_JS
    _renderer_js.py   ← JS string: wmRender(containerId, cfg)
    wafermap.md       ← this file
```

---

## Why SVG (not Plotly)

Plotly scatter uses dots sized by marker-size — they don't fill the die grid
precisely.  SVG renders exact `<rect>` elements per die, so:

- Die squares fill the grid edge-to-edge
- Reticle shot outlines are real drawn rectangles
- Die-loc numbers sit inside each square
- No external library dependency at render time

---

## Import (Python)

**Option A — via repo root (mimcap style):**

```python
import sys, os
sys.path.insert(0, _REPO_ROOT)          # path to repo root (parent of utilities/)
from utilities.wafer_tools.wafer_map import WAFERMAP_JS
```

**Option B — direct wafer_tools path (vcccont_bin8 style):**

```python
import sys, os
_WT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'utilities', 'wafer_tools'))
if _WT not in sys.path: sys.path.insert(0, _WT)
from wafer_map import WAFERMAP_JS
```

**Option C — walk-up discovery (scan-dashboard / vcc_cont style, preferred):**

Walks up from the script's own directory checking `shared/utilities/wafer_tools`
and `utilities/wafer_tools` at each ancestor level.  Robust to any working
directory and to future repo restructuring.  Use `Path(__file__).resolve().parent`
— not `Path(__file__).parent` — so walk-up works when invoked with a relative path.

```python
from pathlib import Path
import sys

def _find_wafer_tools(start: Path, max_levels: int = 6) -> str:
    cur = start.resolve()
    for _ in range(max_levels):
        for rel in ("shared/utilities/wafer_tools", "utilities/wafer_tools"):
            cand = cur / rel
            if (cand / "wafer_map").is_dir():
                return str(cand)
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return ""

_WAFER_TOOLS = _find_wafer_tools(Path(__file__).resolve().parent)
if _WAFER_TOOLS and _WAFER_TOOLS not in sys.path:
    sys.path.insert(0, _WAFER_TOOLS)
from wafer_map import WAFERMAP_JS
```

Then inject **once** into the template HTML.  Inject **before** the main dashboard
`<script>` block (preferred) so `wmRender` is defined before any page-load JS runs.
Fall back to `</body>` only when the main-script marker is absent:

```python
_marker = '\n<script>\n"use strict";'
if _marker in html:
    html = html.replace(_marker, '\n' + WAFERMAP_JS + _marker, 1)
else:
    html = html.replace('</body>', WAFERMAP_JS + '\n</body>', 1)
```

`WAFERMAP_JS` is a self-contained `<script>` block.  It defines one global:
`wmRender(containerId, cfg)`.  No other globals are created.

> **Critical ordering rule**: `WAFERMAP_JS` must be injected **before** any
> script block that calls `wmRender()`.  Prefer injecting before the
> `<script>\n"use strict";` main block; fall back to before `</body>` when
> that marker is absent.  Calling `wmRender` from a script that appears
> earlier in the HTML will throw `wmRender is not defined`.

---

## JavaScript API — `wmRender(containerId, cfg)`

Call from your dashboard JS whenever you want to draw or redraw a map:

```js
wmRender('my-plot-div', {
    dies:          dieCfgArray,   // REQUIRED — see below
    colorFn:       myColorFn,    // REQUIRED — returns '#rrggbb' per die
    tooltipFn:     myTipFn,      // optional — returns HTML string per die
    retShots:      retShotsArray,// optional — shot bounding boxes
    retMap:        retMapObj,    // optional — die→site lookup
    retSiteNum:    retSiteNumObj,// optional — numeric die-loc labels
    retShotLabels: labelArray,   // optional — label text per shot (default: 1-based index)
    width:         320,          // optional, default 280
    bgColor:       '#0f1117',    // optional, default 'none'
    borderColor:   '#bdc3c7',    // optional, wafer circle
    shotColor:     '#2471a3',    // optional, shot outline colour
});
```

The container `<div>` is cleared and replaced with an `<svg>` element.
Call again with new data to redraw in-place.

---

## `cfg` Fields Reference

### `dies` (required)

Array of die objects.  Each **must** have `x` and `y` (integer die
coordinates, same scale as `retMap` keys).  Any other fields are ignored by
the renderer but are passed to `colorFn` and `tooltipFn`.

```js
// Minimal
dies = [{x: 0, y: 1}, {x: 1, y: 1}, ...]

// With app-specific data
dies = [{x: 0, y: 1, collapse: 42.5, phase: 1.23}, ...]
```

### `colorFn(die)` (required)

Function that receives one die object and returns a CSS color string.

### `tooltipFn(die)` (optional)

Function that receives one die object and returns an HTML string.
Shown in a floating tooltip on mouseover.  Omit if no tooltip needed.

### `retShots` (optional)

Array of shot bounding boxes.  Each entry is `[xMin, yMin, xMax, yMax]` in
die coordinates (same coordinate system as `dies[].x / y`).

```js
retShots = [
    [-5, -5, -2, -2],   // shot 0
    [-1, -5,  2, -2],   // shot 1
    ...
]
```

### `retMap` (optional)

Object mapping `"x,y"` die coordinate strings to `[rdx, rdy, shotIdx]`.

- `rdx`, `rdy` — intra-shot die position (reticle-field coordinates, same
  scale used by `retSiteNum` keys)
- `shotIdx` — 0-based index into `retShots`

```js
retMap = {
    "0,1":  [0, 1, 0],   // die at sort (0,1) → reticle-field pos (0,1), shot 0
    "-3,2": [0, 1, 1],   // die at sort (-3,2) → reticle-field pos (0,1), shot 1
    ...
}
```

This is the same dict produced by `load_reticle_map()` in vcccont_bin8 /
vcccont_mimcap — just convert `(sx,sy)` tuple keys to `"sx,sy"` strings.

### `retSiteNum` (optional)

Object mapping `"lx,ly"` site coordinate strings to a **die-location number
within one reticle field** (1, 2, 3 … N, where N = number of distinct die
positions per shot).

The die-loc number convention (which position gets "1") is determined by your
reticle CSV.  For NVL816, `Reticle` integers number left-to-right,
bottom-row-first (ascending Y, ascending X):

```
Reticle field (one shot):
  ┌─────┬─────┬─────┐
  │  4  │  5  │  6  │   ← high Y (top of wafer)
  ├─────┼─────┼─────┤
  │  1  │  2  │  3  │   ← low Y (bottom of wafer)
  └─────┴─────┴─────┘
```

The **same number** appears on every die across all shots that occupies that
position in the reticle field.  So on the full wafer you see "3" repeated
once per shot — always at the same relative position inside each shot box.

Die-loc text color is chosen automatically for contrast against the die fill
(black on light colors, white on dark colors).

```js
retSiteNum = { "0,0": 1, "1,0": 2, "2,0": 3, "0,1": 4, "1,1": 5, "2,1": 6 }
```

If omitted, no numbers are drawn.

### `retShotLabels` (optional)

Array of label strings, one per entry in `retShots`, shown at the geometric
centre of each shot outline.
If omitted, shots are labelled `1, 2, 3 …` by default.
Pass **`false`** (not an empty array) to suppress all shot labels entirely:

```js
retShotLabels: ['A', 'B', ...]   // custom labels per shot
retShotLabels: false              // suppress all shot labels (reticle outlines still drawn)
```

Individual wafer tiles typically use `retShotLabels: false` to avoid clutter;
the composite large-map shows labels (omit the field or pass an array).

---

## `colorFn` Recipes

### 1. Pass / fail (two colours)

```js
colorFn: function(d){ return d.isFail ? '#ff4444' : '#27ae60'; }
```

### 2. Collapse % gradient (mimcap-style)

```js
// Smooth red→green based on a percentage value vs a threshold
function collapseColor(pct, thr){
    if(pct == null) return '#444';
    var t = Math.min(1, Math.max(0, pct / 100));
    // above threshold → red family; below → green family
    if(pct >= thr){
        // thr..100 → orange → red
        var r = Math.round(255);
        var g = Math.round(Math.max(0, 180 * (1 - (pct-thr)/(100-thr))));
        return 'rgb('+r+','+g+',60)';
    } else {
        // 0..thr → green → yellow
        var r2 = Math.round(Math.min(255, 255 * pct / thr));
        return 'rgb('+r2+',200,80)';
    }
}

colorFn: function(d){ return collapseColor(d.collapse, _wmThr); }
```

### 3. IB integer → fixed palette (bin8-style)

```js
var _IB_PAL = {'1':'#00ff44','2':'#7ddb8a','3':'#3d3d3d','4':'#b0b0b0'};
colorFn: function(d){
    return _IB_PAL[String(d.ib)] || '#ff6644';  // default = fail colour
}
```

### 4. Continuous heat (e.g. leakage µA)

```js
// Map a value in [lo, hi] to a blue→red gradient
function heatColor(v, lo, hi){
    if(v == null) return '#333';
    var t = Math.min(1, Math.max(0, (v - lo) / (hi - lo)));
    var r = Math.round(t * 220);
    var b = Math.round((1-t) * 220);
    return 'rgb('+r+',80,'+b+')';
}

colorFn: function(d){ return heatColor(d.leakage, 0, 5); }
```

---

## Building `retShots` from a reticle map

Group die coordinates by shot position `(LayoutX, LayoutY)` — all dies that
share the same shot position form one bounding box.  **Do not add ±0.5
padding** — the renderer already adds +1 to the span so the outline extends
to the outer edge of the last die in each direction.

```python
# _reticle_lkp = {(sort_x, sort_y): {'lx':LayoutX,'ly':LayoutY,'rdx':...,'rdy':...,'r':Reticle}, ...}

_shot_xs = {}; _shot_ys = {}  # keyed by (lx, ly) shot position
for (sx, sy), info in _reticle_lkp.items():
    key = (info['lx'], info['ly'])
    _shot_xs.setdefault(key, []).append(sx)
    _shot_ys.setdefault(key, []).append(sy)

_shot_order = sorted(_shot_xs)     # sorted (lx,ly) tuples → stable shot indices
_shot_idx   = {k: i for i, k in enumerate(_shot_order)}

ret_shots = [
    [min(_shot_xs[k]), min(_shot_ys[k]),
     max(_shot_xs[k]), max(_shot_ys[k])]
    for k in _shot_order
]

# retMap: each die → [rdx, rdy, shotIdx]
ret_map = {
    f"{sx},{sy}": [info['rdx'], info['rdy'], _shot_idx[(info['lx'], info['ly'])]]
    for (sx, sy), info in _reticle_lkp.items()
}
```

---

## Building `retSiteNum` from a reticle map

Map each unique intra-shot die position `(rdx, rdy)` to a die-loc number.

**Option A — use the `Reticle` integer from the CSV directly (preferred):**

```python
# _reticle_lkp = {(sort_x, sort_y): {'rdx':ReticleDieX,'rdy':ReticleDieY,'r':Reticle}, ...}

ret_site_num = {}
for info in _reticle_lkp.values():
    ret_site_num[f"{info['rdx']},{info['rdy']}"] = info['r']
# e.g. {"0,0": 1, "1,0": 2, "2,0": 3, "0,1": 4, "1,1": 5, "2,1": 6}
```

**Option B — compute 1…N from sorted unique positions (generic):**

Sort by `(+rdy, +rdx)` = ascending Y (bottom-row first), ascending X,
so die-loc 1 starts at the bottom-left of the reticle field:

```python
_unique_sites = sorted(
    {(info['rdx'], info['rdy']) for info in _reticle_lkp.values()},
    key=lambda s: (s[1], s[0])   # ascending Y (bottom first), ascending X
)
ret_site_num = {f'{rdx},{rdy}': i+1 for i, (rdx, rdy) in enumerate(_unique_sites)}
```

In Python inject as JSON:
```python
ret_site_num_json = json.dumps(ret_site_num, separators=(',', ':'))
# inject as JS variable in the template:
# var WM_RET_SITE_NUM = <ret_site_num_json>;
```

---

## Full Migration Example — vcccont_mimcap wafermap tab

The mimcap dashboard currently uses `Plotly.newPlot` in `drawWmPlot()`.
To replace it with `wmRender`:

### Python (generate_dashboard.py)

```python
from utilities.wafer_map import WAFERMAP_JS

# Inject before the main script block (preferred); fall back to </body>
_marker = '\n<script>\n"use strict";'
if _marker in html:
    html = html.replace(_marker, '\n' + WAFERMAP_JS + _marker, 1)
else:
    html = html.replace('</body>', WAFERMAP_JS + '\n</body>', 1)
```

### JS (inside the dashboard template)

Replace the `drawWmPlot()` function body with:

```js
function drawWmPlot(){
  var wk = _wmSelWk;
  if(!wk || !WM_DATA[wk]){
    wmRender('wm-plot', {dies:[]});
    return;
  }
  var d = WM_DATA[wk];

  wmRender('wm-plot', {
    dies:      d.dies,
    colorFn:   function(die){
      // collapse mode: colour by average collapse % vs threshold
      if(_wmMode === 'collapse'){
        if(!die.c) return '#444';
        var vals = WM_CS.map(function(k){ return die.c[k]; }).filter(function(v){ return v!=null; });
        if(!vals.length) return '#444';
        var avg = vals.reduce(function(a,b){return a+b;}) / vals.length;
        return avg >= _wmThr ? _collapseRedish(avg, _wmThr) : _collapseGreenish(avg, _wmThr);
      }
      // phase mode: colour by phase µA value
      var v = die.v && die.v[_wmPhase];
      if(v == null) return '#333';
      return _phaseColor(v);
    },
    tooltipFn: function(die){
      var tip = '<b>'+d.lot+' W'+d.wafer+'</b><br>Die ('+die.x+','+die.y+')';
      if(_wmMode === 'collapse' && die.c){
        WM_CS.forEach(function(k){ if(die.c[k]!=null) tip += '<br>'+k+': '+die.c[k].toFixed(1)+'%'; });
      }
      if(_wmMode === 'phase' && die.v){
        WM_PHASES.forEach(function(ph,i){ if(die.v[i]!=null) tip += '<br>'+ph+': '+die.v[i].toFixed(2)+' µA'; });

      }
      return tip;
    },
    retShots:      WM_RET_SHOTS,       // inject from Python
    retMap:        WM_RET_MAP,         // inject from Python
    retSiteNum:    WM_RET_SITES,       // inject from Python {"rdx,rdy": Reticle}
    retShotLabels: WM_SHOT_LABELS,     // inject from Python [1,2,3,...] sequential shot numbers
    width:      document.getElementById('wm-plot').clientWidth || 320,
    bgColor:    '#0f1117',
  });
}
```

---

## Opening the composite view in a separate browser window

Dashboards built from a single `file://` HTML have no server, so cross-window
JS calls (`w.someFunction()`) are blocked by Chrome's security model.
The reliable pattern is to encode state in the **URL hash** and read it back
on the new window's load.

### Caller (main page JS)

```js
function openCompositeWindow(){
  var state = { /* filters, colorMode, etc. */ };
  // Encode state in hash — no cross-window JS needed.
  var url = location.href.split('#')[0] + '#cpstate=' + encodeURIComponent(JSON.stringify(state));
  // window.open reuses named window if still open, navigating it to new URL.
  var w = window.open(url, 'my_comp_window',
    'width=1600,height=950,resizable=yes,scrollbars=yes,toolbar=no,menubar=no,location=no,status=no');
  if(!w){ alert('Pop-up blocked — please allow pop-ups for this page.'); return; }
  w.focus();
}
```

### Popup receiver — inject as a SEPARATE script after WAFERMAP_JS

This script **must** be injected by Python *after* the main WAFERMAP_JS block
so that `wmRender` is defined and all DOM elements are parsed when it runs:

```python
_POPUP_INIT_JS = (
    '<script>\n'
    '(function(){\n'
    '  function _cpAutoOpen(){\n'
    '    var _h = location.hash;\n'
    '    if(_h.indexOf(\'#cpstate=\') !== 0) return;\n'
    '    try{\n'
    '      var _st = JSON.parse(decodeURIComponent(_h.slice(9)));\n'
    '      _cpShowOverlay(_st);   // your overlay-init function\n'
    '    } catch(e){ console.error(\'composite auto-open failed:\', e); }\n'
    '  }\n'
    '  _cpAutoOpen();\n'
    '  window.addEventListener(\'hashchange\', _cpAutoOpen);\n'
    '})();\n'
    '</script>'   # NOTE: plain </script>, NOT <\/script>
)

# Inject both scripts together (WAFERMAP_JS first, then popup init)
html = html.replace('</body>', WAFERMAP_JS + '\n' + _POPUP_INIT_JS + '\n</body>', 1)
```

> **Common pitfall**: writing `'<\/script>'` in a Python string produces the
> literal backslash in the HTML.  Browsers only close a script block on
> `</script>` — the backslash version is silently ignored and everything after
> it (including any overlay HTML) becomes script text.  Always use
> `'</script>'` (no backslash).

> **Why not `setTimeout(fn, 0)`?**  Chrome can yield mid-parse on large
> (4+ MB) files.  A `setTimeout` queued from the main script block may fire
> before the overlay `<div>` has been parsed, making
> `document.getElementById(...)` return null.  A separate `<script>` block
> injected at the very end of `<body>` runs only after all preceding HTML is
> parsed, so it is safe.

---

## Computing `RETICLE_SITE_NUM` in JS (no Python helper needed)

If you have `RETICLE_MAP` already injected as a JS constant, you can compute
`RETICLE_SITE_NUM` directly in JS — no extra Python variable needed:

```js
// RETICLE_MAP format: {"x,y": [rdx, rdy, shotIdx], ...}
// RETICLE_SITE_NUM: ascending Y (bottom-row first), ascending X — 1-based
var RETICLE_SITE_NUM = (function(){
  var sites = {};
  Object.values(RETICLE_MAP).forEach(function(v){ sites[v[0]+','+v[1]] = true; });
  var sorted = Object.keys(sites).sort(function(a, b){
    var ap = a.split(','), bp = b.split(',');
    var dy = (+ap[1]) - (+bp[1]);   // ascending Y (bottom-row first)
    return dy !== 0 ? dy : (+ap[0]) - (+bp[0]);
  });
  var out = {};
  sorted.forEach(function(k, i){ out[k] = i + 1; });
  return out;
})();
```

---

## Relationship to `wafer_pattern`

| Module | Purpose |
|---|---|
| `utilities/wafer_map/` | Draw a wafer map (SVG, any color function) |
| `utilities/wafer_pattern/` | Score spatial patterns + full WPA popup |

`wafer_pattern` imports nothing from `wafer_map` — it has its own SVG
rendering embedded in `WPA_FULL_JS`.  The two modules are independent;
use `wafer_map` when you only need the drawing primitive.

---

## Hover Tooltip Pattern — Scan-dashboard style (no `tooltipFn`)

The `wmRender` `tooltipFn` callback is convenient for simple tooltips, but for
dashboards that need **rich content** (per-core tables, conditional formatting,
cross-lookups to a separate data dict) the scan-dashboard pattern bypasses
`tooltipFn` entirely and wires hover directly on the SVG element.

### How it works

1. **A single shared `<div id="die-tip">` is placed once in the HTML** (not
   inside any map container).  It is `position:fixed`, `pointer-events:none`,
   `display:none` by default.

2. Each `<rect>` die element carries a `data-k` attribute — a pipe-delimited
   key `"lot|wafer|x|y"` — set by `wmRender` automatically when you pass a
   `keyFn` in `cfg`, or set manually on the SVG rects.

3. After `wmRender` returns, wire three listeners on the `<svg>` element
   (not on individual rects):
   - `mousemove` — look up `e.target.getAttribute('data-k')`, call
     `showTip(e, k)` if the key changed, always call `moveTip(e)`.
   - `mouseleave` — call `hideTip()` and reset the cached key.
   - `mouseover` / `mouseout` — stroke the hovered rect white for visual
     feedback, clear on mouseout.

4. `showTip(e, dk)` builds the full HTML from the lookup dict, writes it to
   `#die-tip`, sets `display:block`, and calls `moveTip(e)`.

5. `moveTip(e)` positions the tooltip at `(clientX+16, clientY+16)`, flipping
   left/up if it would overflow the viewport.

### CSS for the tooltip div

```css
#die-tip {
  position: fixed;
  z-index: 9999;
  pointer-events: none;
  display: none;
  background: #0c1624;
  border: 1px solid #2a4878;
  border-radius: 7px;
  padding: 9px 12px;
  font-size: 0.74rem;
  color: #c0ccd8;
  box-shadow: 0 4px 24px rgba(0,0,0,0.8);
  max-width: 310px;
  min-width: 170px;
  line-height: 1.5;
}
```

### JS skeleton

```js
// ── shared tooltip helpers ──────────────────────────────────────────────────

function showTip(e, dk) {
  var parts = dk.split('|');
  var lot = parts[0], wfr = parts[1], x = parts[2], y = parts[3];
  var r = _dieLookup[dk];   // your per-die data dict, keyed "lot|wafer|x|y"

  var html = '<div class="tip-hdr">' + lot + ' W' + wfr
           + ' &nbsp;&middot;&nbsp; (' + x + ', ' + y + ')</div>';

  // --- add whatever rich content you need ---
  if (r) {
    html += '<div>UPM: ' + r.upm + '%</div>';
    html += '<div>Fail: ' + r.fail_sig + '</div>';
    // per-core PRE / POST Vmin table, etc.
  }

  var tip = document.getElementById('die-tip');
  tip.innerHTML = html;
  tip.style.display = 'block';
  moveTip(e);
}

function hideTip() {
  document.getElementById('die-tip').style.display = 'none';
}

function moveTip(e) {
  var tip = document.getElementById('die-tip');
  var tx = e.clientX + 16, ty = e.clientY + 16;
  if (tx + tip.offsetWidth  + 10 > window.innerWidth)  tx = e.clientX - tip.offsetWidth  - 10;
  if (ty + tip.offsetHeight + 10 > window.innerHeight) ty = e.clientY - tip.offsetHeight - 10;
  tip.style.left = tx + 'px';
  tip.style.top  = ty + 'px';
}

// ── wire hover after wmRender ───────────────────────────────────────────────

function wireHover(containerId) {
  var svgEl = document.querySelector('#' + containerId + ' svg');
  if (!svgEl) return;
  var _tipKey = null;

  svgEl.addEventListener('mousemove', function(e) {
    if (e.target.tagName === 'rect') {
      moveTip(e);
      var k = e.target.getAttribute('data-k');
      if (k && k !== _tipKey) { _tipKey = k; showTip(e, k); }
    } else { _tipKey = null; hideTip(); }
  });

  svgEl.addEventListener('mouseover', function(e) {
    if (e.target.tagName === 'rect' && e.target.getAttribute('data-k')) {
      e.target.style.stroke = '#fff';
      e.target.style.strokeWidth = '0.8';
    }
  });

  svgEl.addEventListener('mouseout', function(e) {
    if (e.target.tagName === 'rect') {
      e.target.style.stroke = '';
      e.target.style.strokeWidth = '';
    }
  });

  svgEl.addEventListener('mouseleave', function() { _tipKey = null; hideTip(); });
}
```

### Key-change guard (`k !== _tipKey`)

The `_tipKey` variable per SVG element prevents `showTip` from firing on
every `mousemove` pixel.  The tooltip content is rebuilt only when the cursor
moves to a **different** die (or leaves a rect entirely).  This is important
for large SVGs with many rects — DOM writes on every mousemove are costly.

### Multiple maps sharing one tooltip div

All map instances share the single `#die-tip` div.  Each SVG gets its own
closure variable `_tipKey` inside `wireHover`, so they don't interfere.
`moveTip` just repositions the already-visible tooltip, which is correct
behaviour when the user moves between maps.

### Composite map variant (`showCompTip`)

When multiple wafers can share the same die position (composite overlay), the
key is `"x,y"` rather than `"lot|wafer|x|y"`, and the lookup returns an
**array** of entries (one per wafer).  `showCompTip` iterates the array and
renders a stacked per-wafer section inside the single tooltip div.

```js
function showCompTip(e, ck) {
  var entries = _compLookup[ck];   // array: [{lot, wafer, upm, ...}, ...]
  if (!entries || !entries.length) return;
  var pts = ck.split(','), cx = pts[0], cy = pts[1];
  var html = '<div class="tip-hdr">Position (' + cx + ', ' + cy + ')'
           + ' &nbsp;&middot;&nbsp; ' + entries.length + ' wafer(s)</div>';
  entries.forEach(function(en) {
    html += '<div style="border-top:1px solid #1e3050;margin-top:5px;padding-top:5px">';
    html += '<b>' + en.lot + '</b> W' + en.wafer + ' &nbsp; UPM: ' + en.upm + '%';
    // per-entry per-core table same as showTip ...
    html += '</div>';
  });
  var tip = document.getElementById('die-tip');
  tip.innerHTML = html;
  tip.style.display = 'block';
  moveTip(e);
}
```

### When to use `tooltipFn` vs this pattern

| Use `tooltipFn` | Use manual SVG hover |
|---|---|
| Simple text/value tooltip | Rich HTML tables, conditional row colours |
| Single data source per die | Cross-lookup to multiple dicts |
| No need for click handler sharing the same key | Click + hover both use `data-k` key |
| Rapid prototyping | Production dashboards with fine-grained control |

