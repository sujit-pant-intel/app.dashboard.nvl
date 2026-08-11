# Filter / Lot / Wafer Panel — Full Implementation Reference

**Source file:** `code/dashboard/etest-dashboard/src/generate_pcm_html.py`  
**JavaScript functions:** `buildWfrList()`, `toggleLot()`, `toggleWfr()`, `selAll()`, `clrAll()`, `rerender()`  
**Python HTML builder:** `generate_html()` — `#panel1` section

---

## 1. Overview

The filter panel (`#panel1`) is the left sidebar of the three-panel layout. It shows all lots and wafers in a collapsible accordion table. Clicking a row toggles its selection. Selected wafers drive all charts in tabs 2 (Variability), 3 (Distribution), 4 (XY), and 5 (Parameter Analysis).

The panel is resizable by dragging the `.p1-resize` handle on its right edge.

---

## 2. Data Schema

### `WFR_DATA` (JavaScript array, one object per wafer)

| Field | Type | Description |
|---|---|---|
| `lot` | string | Lot ID |
| `wafer` | string | Wafer number |
| `sort_wafer` | string \| null | Sort wafer ID |
| `layout` | string | Layout/reticle name |
| `material` | string | Material type |
| `program` | string | Test program name |
| `n` | number | Die count |

### `SEL_WFR` (JavaScript `Set`)

Holds indices (into `WFR_DATA`) of currently selected wafers. All charts filter to these rows.

```js
var SEL_WFR = new Set();  // populated with all indices by default (all selected)
```

### `activeKeys()`

```js
function activeKeys() {
  // Returns a Set of "lot/wafer" strings for currently selected wafers
  var s = new Set();
  SEL_WFR.forEach(function(wi) {
    var w = WFR_DATA[wi];
    s.add(w.lot + '/' + w.wafer);
  });
  return s;
}
```

---

## 3. Page Frame (Python-generated)

### Page header

```html
<div class="page-hdr">
  <h1>{title}</h1>
  <div class="sub">{subtitle}</div>
</div>
```

CSS:
```css
.page-hdr { background:#1f3a50; color:#fff; padding:10px 16px }
.page-hdr h1 { font-size:14px; font-weight:bold }
.page-hdr .sub { font-size:11px; color:#aed6f1; margin-top:2px }
```

### Info bar

```html
<div class="info-bar">
  <span><b>Product:</b> {product}</span>
  <span class="ib-sep">|</span>
  <span><b>Lots:</b> {n}</span>
  <span class="ib-sep">|</span>
  <span><b>Wafers:</b> {n}</span>
  <span class="ib-sep">|</span>
  <span><b>Generated:</b> {date}</span>
</div>
```

CSS:
```css
.info-bar { display:flex; flex-wrap:wrap; gap:8px; padding:8px 14px;
            background:#2c3e50; color:#ecf0f1; font-size:12px;
            border-bottom:2px solid #1a252f }
.info-bar b { color:#f1c40f }
.info-bar .ib-sep { color:#4a6a8a; margin:0 2px }
```

### Tab bar

```html
<div class="tabs">
  <button class="tab-btn active" onclick="showTab(this,'tab-var')">&#9741; Variability</button>
  <button class="tab-btn"        onclick="showTab(this,'tab-pdly')">&#9107; Distribution</button>
  <button class="tab-btn"        onclick="showTab(this,'tab-xy')">&#10799; XY Plot</button>
  <button class="tab-btn"        onclick="showTab(this,'tab-pa')">&#9660; Param Analysis</button>
</div>
```

Icons: Variability=`⚷` (&#9741;), Distribution=`⇣` (&#9107;), XY=`✗` (&#10799;), Param Analysis=`▼` (&#9660;).

CSS:
```css
.tabs { display:flex; align-items:center; background:#1a252f;
        padding:6px 14px; gap:6px;
        border-bottom:3px solid #27ae60; flex-shrink:0 }

.tab-btn { padding:8px 24px; border:2px solid transparent; border-radius:6px;
           background:rgba(255,255,255,0.07); color:#95a5a6; cursor:pointer;
           font-size:14px; font-weight:bold; letter-spacing:0.03em;
           transition:background .15s, color .15s, border-color .15s }

.tab-btn:hover { background:rgba(39,174,96,0.20); color:#a9dfbf;
                 border-color:rgba(39,174,96,0.40) }

.tab-btn.active { background:#27ae60; color:#fff; border-color:#1e8449;
                  box-shadow:0 2px 8px rgba(39,174,96,0.35) }
```

---

## 4. Three-Panel Layout

```html
<div class="three-panel">
  <div id="panel1">...</div>       <!-- filter sidebar -->
  <div class="p1-resize"></div>    <!-- drag-resize handle -->
  <div class="sp12">               <!-- P1↔P2 splitter -->
    <button class="sp12-btn" onclick="_sp12Toggle()">&#9664;</button>
  </div>
  <div id="panel2">...</div>       <!-- optional second panel -->
  <div class="sp23"></div>         <!-- P2↔P3 splitter -->
  <div id="panel3">                <!-- main content area -->
    <div id="tab-var" class="tab-panel active">...</div>
    <div id="tab-pdly" class="tab-panel">...</div>
    <div id="tab-xy" class="tab-panel">...</div>
    <div id="tab-pa" class="tab-panel">...</div>
  </div>
</div>
```

CSS:
```css
.three-panel { display:flex; flex-direction:row; flex:1;
               min-height:0; overflow:hidden; gap:0 }

/* Panel 1 — filter sidebar */
#panel1 { width:280px; min-width:140px; flex-shrink:0; background:#fff;
          display:flex; flex-direction:column;
          border-right:2px solid #d0d7de; overflow:hidden; position:relative }

/* P1 resize handle */
.p1-resize { width:5px; flex-shrink:0; background:#d0d7de; cursor:col-resize;
             align-self:stretch; transition:background .15s; user-select:none }
.p1-resize:hover, .p1-resize.dragging { background:#2980b9 }

/* P1↔P2 splitter (also toggles P2 visibility) */
.sp12 { width:22px; flex-shrink:0; background:#ecf0f1; cursor:col-resize;
        display:flex; align-items:center; justify-content:center;
        border-left:1px solid #d0d7de; border-right:1px solid #d0d7de;
        user-select:none; position:relative; z-index:2 }
.sp12:hover { background:#d6eaff }
.sp12-btn { background:none; border:none; font-size:14px; cursor:pointer;
            color:#2c3e50; line-height:1; padding:0; display:block }

/* Panel 2 */
#panel2 { width:400px; min-width:180px; flex-shrink:0; background:#fff;
          display:flex; flex-direction:column;
          overflow:hidden; border-right:2px solid #d0d7de;
          transition:width 0.15s }
#panel2.p2-hidden { width:0 !important; min-width:0 !important;
                    overflow:hidden; border:none }
.p2-hdr  { background:#34495e; color:#fff; padding:5px 10px;
           font-size:11px; font-weight:bold; flex-shrink:0 }
.p2-body { flex:1; overflow:auto }

/* P2↔P3 splitter */
.sp23 { width:5px; flex-shrink:0; background:#d0d7de; cursor:col-resize;
        align-self:stretch; transition:background .15s; user-select:none }
.sp23:hover, .sp23.dragging { background:#2980b9 }

/* Panel 3 — main content */
#panel3 { flex:1; min-width:0; overflow-y:auto; overflow-x:hidden;
          background:#f0f2f5; padding:6px }
```

---

## 5. Panel 1 Header (`.p1-hdr`)

```html
<div class="p1-hdr">
  <!-- Left: filter icon + title -->
  🔍 Filter    <!-- 🔍 = &#128269; -->

  <!-- Middle: selection count badge -->
  <span id="row-info"
        style="font-weight:normal; font-size:10px; color:inherit">
    ({sel}/{total} selected)    <!-- empty string when all or none selected -->
  </span>

  <!-- Right: action buttons -->
  <span>
    <button onclick="selAll()" class="cb">All</button>
    <button onclick="clrAll()" class="cb">Clr</button>
    <button id="show-sel-btn" onclick="toggleShowSel()"
            title="Show only selected wafers" class="cb">Sel</button>
  </span>
</div>
```

CSS:
```css
.p1-hdr { background:#2c3e50; color:#fff; padding:6px 10px;
          font-size:11px; font-weight:bold;
          display:flex; justify-content:space-between; align-items:center;
          flex-shrink:0 }

/* Shared button style in .p1-hdr */
.wfr-hdr .cb { background:none; border:1px solid #7f8c8d; color:#bdc3c7;
               font-size:10px; padding:1px 6px; cursor:pointer;
               border-radius:3px; margin-left:2px }
.wfr-hdr .cb:hover { background:#3d5166; color:#fff }
```

**"Sel" button active state** (when `_SHOW_SEL === true`):
```js
btn.style.background = '#2980b9';
btn.style.color      = '#fff';
```

**"Sel" button inactive state:**
```js
btn.style.background = '';
btn.style.color      = '';
```

---

## 6. Search Row (`.p1-search-row`)

Five search inputs in a flex row, one per column:

```html
<div class="p1-search-row">
  <input placeholder="Program..."  oninput="onSearch('program',  this.value)"
         title="Filter by Program"  style="flex:2">
  <input placeholder="Lot..."      oninput="onSearch('lot',      this.value)"
         title="Filter by Lot"      style="flex:2">
  <input placeholder="Wafer..."    oninput="onSearch('wafer',    this.value)"
         title="Filter by Wafer"    style="flex:1">
  <input placeholder="Layout..."   oninput="onSearch('layout',   this.value)"
         title="Filter by Layout"   style="flex:2">
  <input placeholder="Material..." oninput="onSearch('material', this.value)"
         title="Filter by Material" style="flex:2">
</div>
```

CSS:
```css
.p1-search-row { display:flex; gap:2px; padding:4px 6px;
                 background:#f0f2f5; border-bottom:1px solid #dde; flex-shrink:0 }
.p1-search-row input { flex:1; min-width:0; padding:2px 5px;
                        font-size:10px; border:1px solid #ccc;
                        border-radius:3px; background:#fff }
```

Flex ratios: Program `flex:2`, Lot `flex:2`, Wafer `flex:1`, Layout `flex:2`, Material `flex:2`.

The `oninput` handler updates `_SEARCH[field]` and calls `buildWfrList()`.

---

## 7. Filter Table

Inserted into `.p1-body`:

```html
<div class="p1-body">
  <table style="border-collapse:collapse; width:100%; font-size:11px">
    <thead style="position:sticky; top:0; z-index:2">
      <tr>
        <th style="background:#34495e; color:#ecf0f1; padding:4px 8px; text-align:left">Program</th>
        <th style="background:#34495e; color:#ecf0f1; padding:4px 8px; text-align:left">Lot</th>
        <th style="background:#34495e; color:#ecf0f1; padding:4px 8px; text-align:left">Wafer</th>
        <th style="background:#34495e; color:#ecf0f1; padding:4px 8px; text-align:left">Layout</th>
        <th style="background:#34495e; color:#ecf0f1; padding:4px 8px; text-align:left">Material</th>
        <th style="background:#34495e; color:#ecf0f1; padding:4px 8px; text-align:right">N</th>
      </tr>
    </thead>
    <tbody id="wfr-tbody">
      <!-- Built by buildWfrList() -->
    </tbody>
  </table>
</div>
```

CSS for `.p1-body`:
```css
.p1-body { flex:1; overflow-y:auto; overflow-x:auto }
```

---

## 8. Lot Header Row (`.lot-hdr`)

One row per unique program/lot group:

```html
<tr class="lot-hdr" onclick="toggleLot({li})">

  <!-- Cell 1: arrow + checkbox + program name + count -->
  <td style="padding:4px 8px; background:#34495e; color:#ecf0f1;
             font-weight:bold; cursor:pointer; user-select:none;
             word-break:break-all">
    <span id="lot-arr-{li}" style="margin-right:4px">▾</span>
    <!-- ▾ = &#9662; expanded | ▸ = &#9658; collapsed -->
    <input id="lot-cb-{li}" type="checkbox" style="vertical-align:middle; margin-right:4px"
           onclick="selLot(event, '{prog}')">
    {program}
    <span style="font-size:10px; color:#95a5a6; font-weight:normal">
      ({selCnt}/{total})
    </span>
  </td>

  <!-- Cell 2: lot number(s) -->
  <td style="background:#34495e; color:#aed6f1; font-size:10px;
             padding:4px 8px; cursor:pointer; word-break:break-all"
      title="{full lot list}">
    {firstLot}…    <!-- "…" appended when multiple lots -->
  </td>

  <!-- Cell 3: wafer — empty -->
  <td style="background:#34495e; font-size:10px; padding:4px 8px"></td>

  <!-- Cell 4: layout -->
  <td style="background:#34495e; color:#bdc3c7; font-size:10px;
             padding:4px 8px; cursor:pointer; word-break:break-all">
    {layoutList}
  </td>

  <!-- Cell 5: material -->
  <td style="background:#34495e; color:#bdc3c7; font-size:10px;
             padding:4px 8px; cursor:pointer; word-break:break-all">
    {materialList}
  </td>

  <!-- Cell 6: N total -->
  <td style="background:#34495e; color:#bdc3c7; font-size:10px;
             padding:4px 8px; cursor:pointer; text-align:right">
    {totalN}
  </td>

</tr>
```

CSS:
```css
.lot-hdr td { background:#34495e !important; color:#ecf0f1 !important }
```

Lot header checkbox state:

| Condition | `checked` | `indeterminate` |
|---|---|---|
| All wafers selected | `true` | `false` |
| Some wafers selected | `false` | `true` |
| No wafers selected | `false` | `false` |

---

## 9. Wafer Detail Row (`.fr`)

One row per wafer in `WFR_DATA`:

```html
<tr class="fr {frs if selected}"
    data-li="{li}"
    style="{display:none if lot collapsed}"
    onclick="toggleWfr({wi}, event)">

  <!-- Cell 1: program -->
  <td class="fp"
      style="color:#7f8c8d; font-size:10px; word-break:break-all"
      title="{program}">{program}</td>

  <!-- Cell 2: lot -->
  <td class="fp">{lot}</td>

  <!-- Cell 3: wafer (shows sort_wafer if present, else wafer) -->
  <td class="fp">{sort_wafer || wafer}</td>

  <!-- Cell 4: layout -->
  <td class="fp" style="color:#7f8c8d; font-size:10px; word-break:break-all">
    {layout}
  </td>

  <!-- Cell 5: material — same style as layout -->
  <td class="fp" style="color:#7f8c8d; font-size:10px; word-break:break-all">
    {material}
  </td>

  <!-- Cell 6: N (right-aligned) -->
  <td class="num fp">{n}</td>

</tr>
```

CSS:
```css
/* .fp — base cell style */
.fp { padding:3px 8px; white-space:nowrap; border-bottom:1px solid #eee }

/* Unselected row hover */
.fr:hover td { background:#eaf4ff !important; cursor:pointer }

/* Selected row */
.frs td { background:#d6eaff !important; font-weight:bold }
.frs:hover td { background:#bcd8f8 !important }

/* .num — right-aligned */
.wfr-tbl .num { text-align:right }
```

Row states:

| State | Class | `td` background | Font weight |
|---|---|---|---|
| Unselected | `fr` | (default) | normal |
| Unselected + hover | `fr` | `#eaf4ff` | normal |
| Selected | `fr frs` | `#d6eaff` | bold |
| Selected + hover | `fr frs` | `#bcd8f8` | bold |

---

## 10. Selection Mechanics

### `toggleWfr(wi, event)`

```js
function toggleWfr(wi, event) {
  if (event.shiftKey && _tblLastWfr !== null) {
    // Shift-click: select range between _tblLastWfr and wi
    var vis = _visIndices();   // visible (non-filtered) row indices
    var a = vis.indexOf(_tblLastWfr), b = vis.indexOf(wi);
    if (a > b) { var t = a; a = b; b = t; }
    for (var k = a; k <= b; k++) SEL_WFR.add(vis[k]);
    // Range-select only adds, never removes
  } else {
    // Regular click: toggle
    if (SEL_WFR.has(wi)) SEL_WFR.delete(wi);
    else                  SEL_WFR.add(wi);
    _tblLastWfr = wi;
  }
  buildWfrList();
  rerender();
}
```

`_tblLastWfr` — index of the last singly-clicked wafer (used as shift-click anchor).

### `_visIndices()`

Returns array of `WFR_DATA` indices that are currently visible (match all search filters and, if `_SHOW_SEL`, are selected).

### `selLot(event, prog)`

```js
function selLot(event, prog) {
  event.stopPropagation();  // prevent toggleLot from firing
  var all = WFR_DATA.map((w,i) => w.program === prog ? i : -1).filter(i => i >= 0);
  var cb  = document.getElementById('lot-cb-' + /* li */);
  if (cb.checked) all.forEach(i => SEL_WFR.add(i));
  else            all.forEach(i => SEL_WFR.delete(i));
  buildWfrList();
  rerender();
}
```

### `selAll()` / `clrAll()`

```js
function selAll() {
  WFR_DATA.forEach((_, i) => SEL_WFR.add(i));
  buildWfrList(); rerender();
}
function clrAll() {
  SEL_WFR.clear();
  buildWfrList(); rerender();
}
```

---

## 11. Accordion Behaviour (`toggleLot`)

```js
var _lotCollapsed = {};   // keyed by program name

function toggleLot(li) {
  var prog = WFR_DATA.find(w => /* first row of group li */);
  _lotCollapsed[prog] = !_lotCollapsed[prog];
  buildWfrList();   // re-renders with hidden/shown rows
}
```

Rules:
- All lots expanded by default (`_lotCollapsed[prog] = false` on first render).
- Clicking an expanded header closes it; clicking a collapsed header opens it.
- Only one lot header open at a time (opening a lot closes all others).
- Collapsed wafer rows: `style="display:none"`.
- Arrow spans:
  - `▾` (&#9662;) — expanded
  - `▸` (&#9658;) — collapsed

---

## 12. Show-Selected Toggle (`toggleShowSel`)

```js
var _SHOW_SEL = false;

function toggleShowSel() {
  _SHOW_SEL = !_SHOW_SEL;
  var btn = document.getElementById('show-sel-btn');
  btn.style.background = _SHOW_SEL ? '#2980b9' : '';
  btn.style.color      = _SHOW_SEL ? '#fff'    : '';
  buildWfrList();
}
```

When `_SHOW_SEL === true`, `_matchSearch()` adds an additional filter: rows where `!SEL_WFR.has(wi)` are hidden.

---

## 13. Search / Filter (`_matchSearch`)

```js
function _matchSearch(w, wi) {
  if (_SHOW_SEL && !SEL_WFR.has(wi)) return false;
  var q;
  q = _SEARCH.program;
  if (q && w.program.toLowerCase().indexOf(q) < 0) return false;
  q = _SEARCH.lot;
  if (q && w.lot.toLowerCase().indexOf(q) < 0) return false;
  q = _SEARCH.wafer;
  if (q && (w.sort_wafer || w.wafer).toLowerCase().indexOf(q) < 0) return false;
  q = _SEARCH.layout;
  if (q && w.layout.toLowerCase().indexOf(q) < 0) return false;
  q = _SEARCH.material;
  if (q && w.material.toLowerCase().indexOf(q) < 0) return false;
  return true;
}
```

All comparisons are substring, case-insensitive (`.toLowerCase().indexOf(q) >= 0`).  
The Wafer column searches `sort_wafer` first (falls back to `wafer`).

---

## 14. Selection Count Badge (`#row-info`)

```js
function _updateRowInfo() {
  var total = WFR_DATA.length;
  var sel   = SEL_WFR.size;
  var el = document.getElementById('row-info');
  if (!el) return;
  if (sel === 0 || sel === total) { el.textContent = ''; return; }
  el.textContent = '(' + sel + '/' + total + ' selected)';
}
```

CSS: `font-weight:normal; font-size:10px; color:inherit` (inherits white from `.p1-hdr`).

---

## 15. Lazy Tab Rendering (`rerender`)

When the wafer selection changes, `rerender()` is called:

```js
var _DIRTY = { var: true, pdly: true, xy: true, pa: true };

function rerender() {
  // Mark all tabs dirty
  Object.keys(_DIRTY).forEach(function(k) { _DIRTY[k] = true; });

  // Immediately rebuild the currently visible tab only
  var active = document.querySelector('.tab-panel.active');
  if (!active) return;

  switch (active.id) {
    case 'tab-var':  if (_DIRTY.var)  { buildVariabilityTab();    _DIRTY.var  = false; } break;
    case 'tab-pdly': if (_DIRTY.pdly) { buildPropDelayTab();      _DIRTY.pdly = false; } break;
    case 'tab-xy':   if (_DIRTY.xy)   { buildAllFpPanels();       _DIRTY.xy   = false; } break;
    case 'tab-pa':   if (_DIRTY.pa)   { buildParamAnalysisTab();  _DIRTY.pa   = false; } break;
  }
}
```

The other tabs rebuild when the user clicks their tab button:

```js
function showTab(btn, id) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(id).classList.add('active');

  var key = id.replace('tab-', '');
  if (_DIRTY[key]) {
    switch (key) {
      case 'var':  buildVariabilityTab();   break;
      case 'pdly': buildPropDelayTab();     break;
      case 'xy':   buildAllFpPanels();      break;
      case 'pa':   buildParamAnalysisTab(); break;
    }
    _DIRTY[key] = false;
  }
}
```

---

## 16. Resize Handle (`.p1-resize`)

Right edge of `#panel1`. Dragging resizes Panel 1 width.

```js
var _p1ResizeDragging = false;
p1Resize.addEventListener('mousedown', function(e) {
  _p1ResizeDragging = true;
  p1Resize.classList.add('dragging');
  document.addEventListener('mousemove', _onP1Resize);
  document.addEventListener('mouseup',   _onP1ResizeEnd);
});

function _onP1Resize(e) {
  var w = Math.max(140, Math.min(600, e.clientX));
  document.getElementById('panel1').style.width = w + 'px';
}
```

CSS:
```css
.p1-resize { width:5px; flex-shrink:0; background:#d0d7de; cursor:col-resize;
             align-self:stretch; transition:background .15s; user-select:none }
.p1-resize:hover, .p1-resize.dragging { background:#2980b9 }
```

Minimum width: `140px`. Maximum: `600px`.

---

## 17. Global Group-By (Variability Tab)

The Variability tab has a group-by widget (`_GBY_HTML` snippet) in its toolbar:

```html
<!-- Variability tab toolbar -->
<div style="background:#1f3a50; border-bottom:1px solid #1a252f;
            padding:5px 12px; gap:4px; display:flex; align-items:center">

  <!-- ... other toolbar items ... -->

  <!-- Separator -->
  <span style="width:1px; background:#4a6278; align-self:stretch; margin:0 4px"></span>

  <!-- "Group by:" label -->
  <b style="color:#f1c40f; margin-right:4px; font-size:12px">Group by:</b>

  <!-- All 5 checkboxes: class="vgb-cb" -->
  <label style="cursor:pointer; font-size:12px; color:#ecf0f1">
    <input type="checkbox" class="vgb-cb" value="none"
           onchange="toggleGby('none')" checked>
    None
  </label>
  <!-- Lot | Wafer | Layout | Material — same structure -->
</div>
```

`_GBY` is a global array (default `[]`). `toggleGby(field)` updates `_GBY` and calls `buildVariabilityTab()`.

This is separate from per-panel group-by (`_PDLY_GBY_P`, `_FP_ST[pid].gby`).

---

## 18. Global CSS Summary

Full stylesheet defined in `_CSS` variable in the source file. Key selectors relevant to the filter panel:

```css
/* Global reset */
* { box-sizing:border-box; margin:0; padding:0 }
body { font-family:Arial,sans-serif; background:#f0f2f5; color:#2c3e50; font-size:13px }

/* Filter table */
.wfr-tbl { border-collapse:collapse; width:100%; table-layout:auto;
           font-size:12px; white-space:nowrap }
.wfr-tbl th { background:#34495e; color:#ecf0f1; padding:5px 10px;
              text-align:left; position:sticky; top:0; z-index:2 }
.wfr-tbl td { padding:4px 10px; border-bottom:1px solid #f0f0f0; cursor:pointer }
.wfr-tbl .num { text-align:right }
.wfr-tbl tbody tr:nth-child(even) td { background:#f7faff }
.wfr-tbl .fr:hover td  { background:#eaf4ff !important }
.wfr-tbl .frs td       { background:#d6eaff !important; font-weight:bold }
.wfr-tbl .frs:hover td { background:#bcd8f8 !important }

/* Lot header rows */
.lot-hdr td { background:#34495e !important; color:#ecf0f1 !important }

/* Cell padding */
.fp { padding:3px 8px; white-space:nowrap; border-bottom:1px solid #eee }

/* Button styles shared across the header and other UI */
.cb { padding:4px 12px; font-size:12px; cursor:pointer;
      border:1px solid #bdc3c7; background:#ecf0f1;
      border-radius:3px; color:#2c3e50 }
.cb:hover { background:#d5dbde }

.dl-btn { padding:4px 14px; font-size:11px; border:none; border-radius:4px;
          background:#27ae60; color:#fff; cursor:pointer; font-weight:bold }
.dl-btn:hover { background:#1e8449 }

/* Row count badge */
.row-info { font-size:10px; color:#aed6f1; margin-left:8px; font-weight:normal }
```

---

## 19. Key Functions Reference

| Function | Purpose |
|---|---|
| `buildWfrList()` | Rebuild `#wfr-tbody` with current filter/selection state |
| `toggleLot(li)` | Expand/collapse lot group `li`; opens that lot, closes others |
| `selLot(event, prog)` | Select/deselect all wafers of program `prog` |
| `toggleWfr(wi, event)` | Toggle wafer `wi`; supports shift-click range |
| `selAll()` | Select all wafers |
| `clrAll()` | Deselect all wafers |
| `toggleShowSel()` | Toggle "show only selected" filter |
| `onSearch(field, val)` | Update search query for column `field`; rebuilds list |
| `_matchSearch(w, wi)` | Test if wafer `w` at index `wi` passes all filters |
| `_visIndices()` | Sorted array of currently visible row indices |
| `activeKeys()` | `Set<"lot/wafer">` for all selected wafers |
| `rerender()` | Mark all tabs dirty + rebuild currently visible tab |
| `showTab(btn, id)` | Switch active tab; rebuild if dirty |
| `toggleGby(field)` | Toggle global group-by field (Variability tab) |
| `_updateRowInfo()` | Update `#row-info` selection badge text |
