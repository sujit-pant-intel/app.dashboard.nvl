# Variability Parameter Table — Full Implementation Reference

**Source file:** `code/dashboard/etest-dashboard/src/generate_pcm_html.py`  
**JavaScript function:** `buildParamTable()`  
**Location in page:** `#panel2` inside the Variability tab (`#tab-var`)

---

## 1. Overview

The parameter table lives in `#panel2`, which is **hidden by default** and toggled open by clicking the `◁` arrow on the `.sp12` splitter between panel2 and panel3.

When open it renders a full-width scrollable table (`class="hm-tbl"`) with:
- One **group header row** per PCM parameter group
- One **data row** per parameter within each visible, expanded group
- Statistics columns: N, Median, σ, Spread (%), Min, Max, LSL, USL, Unit

Clicking any data row selects that parameter (`SEL_PARAM`) and opens the **param detail modal** (`#pm-overlay`).

---

## 2. Panel 2 DOM Structure (Python-generated)

```html
<!-- Hidden by default via p2-hidden class — toggleP2() removes/restores it -->
<div id="panel2" class="p2-hidden">

  <!-- Header bar -->
  <div class="p2-hdr">
    📊 Parameter Table    <!-- 📊 = &#128202; -->

    <!-- Summary CSV button -->
    <button onclick="downloadVarCSV()"
            title="Download parameter summary table as CSV"
            style="margin-left:8px; padding:2px 9px; font-size:10px; font-weight:bold;
                   border:none; border-radius:3px; background:#27ae60; color:#fff; cursor:pointer;
                   vertical-align:middle"
            onmouseover="this.style.background='#1e8449'"
            onmouseout="this.style.background='#27ae60'">
      ⬇ Summary CSV    <!-- ⬇ = &#11015; -->
    </button>

    <!-- Per-site CSV button -->
    <button onclick="downloadSiteCSV()"
            title="Download per-site wide CSV (one row per reticle)"
            style="margin-left:4px; padding:2px 9px; font-size:10px; font-weight:bold;
                   border:none; border-radius:3px; background:#2980b9; color:#fff; cursor:pointer;
                   vertical-align:middle"
            onmouseover="this.style.background='#1a6496'"
            onmouseout="this.style.background='#2980b9'">
      ⬇ Per-site CSV
    </button>
  </div>

  <!-- Scrollable body containing the table -->
  <div class="p2-body">
    <table class="hm-tbl">
      <thead id="var-head"></thead>    <!-- rebuilt by buildParamTable() -->
      <tbody id="var-body"></tbody>    <!-- rebuilt by buildParamTable() -->
    </table>
  </div>

</div>
```

Panel 2 CSS:
```css
#panel2 { width:400px; min-width:180px; flex-shrink:0; background:#fff;
          display:flex; flex-direction:column;
          overflow:hidden; border-right:2px solid #d0d7de;
          transition:width 0.15s }

/* Hidden state — width collapses to 0 */
#panel2.p2-hidden { width:0 !important; min-width:0 !important;
                    overflow:hidden; border:none }

.p2-hdr { background:#34495e; color:#fff; padding:5px 10px;
          font-size:11px; font-weight:bold; flex-shrink:0 }

.p2-body { flex:1; overflow:auto }
```

---

## 3. sp12 Splitter (panel2 toggle / resize handle)

```html
<div class="sp12" id="sp12"
     onmousedown="startSplit23(event)"
     title="Drag to resize | click arrow to hide/show table">

  <button class="sp12-btn" id="p2-toggle-btn"
          onclick="event.stopPropagation(); toggleP2()"
          title="Toggle parameter table">
    ◁    <!-- ◁ = &#9664; when panel2 is open -->
    <!-- ▷ = &#9654; when panel2 is hidden -->
  </button>

</div>
```

```css
/* 22px wide arrow-button bar between panel2 and panel3 */
.sp12 { width:22px; flex-shrink:0; background:#ecf0f1; cursor:col-resize;
        display:flex; align-items:center; justify-content:center;
        border-left:1px solid #d0d7de; border-right:1px solid #d0d7de;
        user-select:none; position:relative; z-index:2 }
.sp12:hover { background:#d6eaff }
.sp12-btn { background:none; border:none; font-size:14px; cursor:pointer;
            color:#2c3e50; line-height:1; padding:0; display:block }
```

`toggleP2()` logic:
```js
function toggleP2() {
  var p2  = document.getElementById('panel2');
  var btn = document.getElementById('p2-toggle-btn');
  var hidden = p2.classList.toggle('p2-hidden');
  btn.innerHTML = hidden ? '&#9654;' : '&#9664;';
  //  ▷ = &#9654; (panel hidden)  |  ◁ = &#9664; (panel visible)
}
```

---

## 4. Table CSS (`.hm-tbl`)

```css
/* Table base */
.hm-tbl { border-collapse:collapse; font-size:12px;
          white-space:nowrap; table-layout:auto }

/* Column headers */
.hm-tbl th { background:#2c3e50; color:#fff; padding:5px 10px;
             text-align:right; position:sticky; top:0; z-index:1 }
/* First header cell (Parameter) — sticky left + right */
.hm-tbl th:first-child { text-align:left; position:sticky;
                          left:0; z-index:2; background:#2c3e50 }

/* Data cells */
.hm-tbl td { padding:4px 10px; border-bottom:1px solid #eee;
             text-align:right; white-space:nowrap }

/* Alternating row backgrounds (even rows, excluding group headers) */
.hm-tbl tbody tr:nth-child(even):not(.cat-hdr) { background:#f4f8ff }

/* Parameter name cell — sticky left column */
.hm-tbl td.tn { position:sticky; left:0; background:#f8f9fa;
                text-align:left; cursor:pointer;
                border-right:2px solid #dde; z-index:1;
                max-width:220px; overflow:hidden; text-overflow:ellipsis }
.hm-tbl td.tn:hover { background:#eaf4ff }

/* SELECTED row */
.hm-tbl tr.sel-row td { background:#eaf4ff !important }
.hm-tbl tr.sel-row td.tn { background:#d6eaff !important;
                             border-left:3px solid #2980b9;
                             font-weight:bold }

/* Row hover */
.hm-tbl tbody tr:not(.cat-hdr):hover { background:#eaf4ff !important; cursor:pointer }

/* GROUP HEADER row */
.hm-tbl tr.cat-hdr td { background:#2c3e50; color:#ecf0f1;
                         font-weight:bold; font-size:11px;
                         cursor:pointer; padding:4px 10px }
.hm-tbl tr.cat-hdr:hover td { background:#34495e }

/* Hidden rows (when group is collapsed) */
.hm-tbl tr.grp-hidden { display:none }

/* Spec violation cell */
.cell-r { background:#fdecea !important; color:#c0392b; font-weight:bold }

/* Normal + good cell (not currently used but in CSS) */
.cell-g { background:#eafaf1 !important; color:#1e8449 }
```

---

## 5. `buildParamTable()` — Full Generated HTML

### `<thead>` — 10 column headers

```html
<tr>
  <!-- sticky left + sticky top -->
  <th style="text-align:left; min-width:160px; position:sticky; left:0; z-index:2; background:#2c3e50">
    Parameter
  </th>
  <th style="min-width:44px">N</th>
  <th style="min-width:70px">Median</th>
  <th style="min-width:50px">σ</th>          <!-- σ = &sigma; -->
  <th style="min-width:60px">Spread (%)</th>
  <th style="min-width:58px">Min</th>
  <th style="min-width:58px">Max</th>
  <th style="min-width:50px">LSL</th>
  <th style="min-width:50px">USL</th>
  <th style="min-width:34px">Unit</th>
</tr>
```

Column widths: Parameter `min-width:160px`, N `44px`, Median `70px`, σ `50px`, Spread `60px`, Min/Max `58px`, LSL/USL `50px`, Unit `34px`.

---

### Group header row (`.cat-hdr`)

One per PCM group that has `_GRP_VIS[grp] === true`:

```html
<tr class="cat-hdr" onclick="toggleGrpRow('{grp}')">
  <td colspan="10">
    ▼    <!-- &#9660; expanded | ▶ &#9658; collapsed -->
    {group name}
    <span style="font-weight:normal; font-size:10px; color:#aed6f1">
      ({params.length})
    </span>
  </td>
</tr>
```

States:

| State | Arrow char | Code |
|---|---|---|
| Expanded | `▼` | `&#9660;` |
| Collapsed | `▶` | `&#9658;` |

The arrow is inserted as plain text at the start of the cell content (no wrapping element).

---

### Parameter data row

```html
<!-- Normal (unselected) row: class="" or class="grp-hidden" -->
<!-- Selected row: class="sel-row" or class="grp-hidden sel-row" -->
<tr class="{cls}" onclick="selParam('{param}')">

  <!-- Cell 1: sticky parameter name (class="tn") -->
  <td class="tn" title="{param} — {name}">
    {param}
    <span style="color:#7f8c8d; font-size:10px; font-weight:normal; margin-left:4px">
      ({name})    <!-- only shown if meta.name is non-empty -->
    </span>
  </td>

  <!-- Cells 2-10: stats (only when data exists) -->
  <td>{st.n}</td>

  <!-- Median: cell-r class applied when outside spec -->
  <td class="{medCls}">
    {_fmt(st.median)}
  </td>

  <td>{_fmt(st.std)}</td>

  <td>
    {st.cv.toFixed(1)}%    <!-- empty string when cv is null -->
  </td>

  <td>{_fmt(st.min)}</td>
  <td>{_fmt(st.max)}</td>

  <!-- LSL: red text -->
  <td style="color:#c0392b">{_fmt(meta.lsl)}</td>    <!-- empty if no lsl -->

  <!-- USL: blue text -->
  <td style="color:#2980b9">{_fmt(meta.usl)}</td>    <!-- empty if no usl -->

  <!-- Unit: grey, 10px -->
  <td style="color:#7f8c8d; font-size:10px">{meta.unit}</td>

</tr>
```

When **no data** for a parameter:
```html
<tr ...>
  <td class="tn">...</td>
  <td colspan="9" style="color:#aaa; font-style:italic">no data</td>
</tr>
```

---

### Median cell colouring (`medCls`)

```js
var medCls = '';
if (st) {
  if (lsl != null && st.median < lsl)  medCls = ' cell-r';
  else if (usl != null && st.median > usl) medCls = ' cell-r';
}
```

| Condition | `medCls` | Background | Text colour | Bold |
|---|---|---|---|---|
| Within spec | `""` | default | default | no |
| Median < LSL | `" cell-r"` | `#fdecea` | `#c0392b` | yes |
| Median > USL | `" cell-r"` | `#fdecea` | `#c0392b` | yes |

Only Median gets the colour treatment. No other stat cell is coloured by spec in the table.

---

### Selected row highlight (`sel-row`)

| Row element | Normal bg | Selected bg |
|---|---|---|
| `td` (all) | (default / alternating) | `#eaf4ff` |
| `td.tn` (name cell) | `#f8f9fa` | `#d6eaff` |
| `td.tn` border | `border-right:2px solid #dde` | + `border-left:3px solid #2980b9; font-weight:bold` |

Applied class: `sel-row` added to `<tr>` when `SEL_PARAM === param`.

---

## 6. State Variables

```js
// ── Global group visibility (toggleable via toolbar buttons) ──────────────
var _GRP_VIS = {};   // {groupName: bool}
// Initialised from PCM_DEFAULT_GROUPS (from product_setup["pcm_param_groups"]):
PCM_GROUPS.forEach(function(g) {
  _GRP_VIS[g] = (PCM_DEFAULT_GROUPS.length === 0
              || PCM_DEFAULT_GROUPS.indexOf(g) >= 0);
});
// Fallback: if no groups match default list, show all
if (PCM_DEFAULT_GROUPS.length > 0 && !PCM_GROUPS.some(function(g){ return _GRP_VIS[g]; })) {
  PCM_GROUPS.forEach(function(g){ _GRP_VIS[g] = true; });
}

// ── Group expand/collapse state ───────────────────────────────────────────
var _GRP_STATE = {};  // {groupName: bool | undefined}
// undefined or true  → expanded (default)
// false              → collapsed (rows hidden)

// ── Currently selected parameter (drives modal + strip chart highlight) ───
var SEL_PARAM = null;   // string | null

// ── PCM group definitions (injected by Python) ────────────────────────────
var PCM_GROUPS = ['Propagation Delay', 'Power (Off)', 'Vts N-FET', ...];
var PCM_GROUP_PARAMS = {
  'Propagation Delay': ['Td_RJ4u', 'Td_RK4u', ...],
  'Power (Off)':       ['Poff_RJ4u', ...],
  ...
};
```

---

## 7. `_paramStats(param)` — Statistics Computation

Called once per visible parameter on every `buildParamTable()` render.

```js
function _paramStats(param) {
  var ak = activeKeys(), vals = [];
  PCM_ROWS.forEach(function(r) {
    if (r.param !== param) return;
    if (!ak.has(_rKey(r))) return;
    (r.die_values || []).forEach(function(v) {
      if (v != null && isFinite(v)) vals.push(v);
    });
  });
  if (!vals.length) return null;          // → "no data" colspan

  var dv = _toDisplayVals(param, vals);   // Td_ → %, leakage → nA/µA/mA
  if (!dv.length) return null;

  var med = _med(dv);

  // Clip to P1/P99 for σ, Spread, Min, Max (full N and Median are un-clipped)
  var clipped = dv;
  if (dv.length >= 10) {
    var srt = dv.slice().sort(function(a, b) { return a - b; });
    var p01 = srt[Math.floor(srt.length * 0.01)];
    var p99 = srt[Math.min(srt.length - 1, Math.ceil(srt.length * 0.99))];
    if (p99 > p01) clipped = dv.filter(function(v) { return v >= p01 && v <= p99; });
  }

  var sd = _std(clipped);
  var cv = (med && med !== 0) ? Math.abs(sd / med * 100) : null;
  return {
    n:      dv.length,
    median: med,
    std:    sd,
    cv:     cv,           // null when median = 0
    min:    _safeMin(clipped),
    max:    _safeMax(clipped)
  };
}
```

**Clipping rule:** N and Median use the full dataset. σ, Spread (%), Min, Max use P1–P99 clipped data to suppress extreme outliers (e.g. leakage at invalid sites).

### `_toDisplayVals(param, vals)` — param-specific unit conversion

| Param pattern | Conversion |
|---|---|
| `Td_*` (case-insensitive) | `target / v × 100` → `%` of target; `target = meta.target ?? (lsl+usl)/2` |
| `Poff_*` or `Ioff_*` | multiply by `_leakageScale(vals).scale` → nA / µA / mA |
| All others | identity (return as-is) |

---

## 8. Interactions

### `selParam(param)`

```js
function selParam(param) {
  SEL_PARAM = (SEL_PARAM === param) ? null : param;  // toggle
  rerender();                                          // rebuilds table + strip chart
  if (param) _showParamModal(param);                  // open detail modal
}
```

- Clicking the same row twice deselects it (no modal opened on deselect).
- `rerender()` rebuilds the currently-visible tab (marks all others dirty).

### `toggleGrpRow(grp)`

```js
function toggleGrpRow(grp) {
  _GRP_STATE[grp] = (_GRP_STATE[grp] === false) ? true : false;
  buildParamTable();
}
```

- `_GRP_STATE[grp] === false` → collapsed (rows have class `grp-hidden`).
- Any other value (including `undefined`) → expanded.

### `toggleGrp(grp, btn)` — toolbar group filter

```js
function toggleGrp(grp, btn) {
  _GRP_VIS[grp] = !_GRP_VIS[grp];
  btn.classList.toggle('grp-off', !_GRP_VIS[grp]);
  rerender();
}
```

The toolbar group buttons have class `grp-off` appended when hidden:
```css
button.grp-off {
  background: rgba(0,0,0,0.3) !important;
  opacity: 0.7;
  text-decoration: line-through;
  color: #fff !important;
}
```

### `setAllGroups(visible)` — "All" / "None" toolbar buttons

```js
function setAllGroups(visible) {
  PCM_GROUPS.forEach(function(grp) { _GRP_VIS[grp] = visible; });
  document.querySelectorAll('.grp-btn').forEach(function(btn) {
    btn.classList.toggle('grp-off', !visible);
  });
  rerender();
}
```

---

## 9. Variability Tab Toolbar (above the three-panel area)

```html
<div style="display:flex; flex-wrap:wrap; gap:4px 10px; align-items:center;
            padding:5px 12px; background:#1f3a50; color:#fff; font-size:12px;
            border-bottom:1px solid #1a252f; flex-shrink:0">

  <!-- Group-by (same snippet as all other tabs) -->
  <span style="width:1px; background:#4a6278; align-self:stretch; margin:0 4px"></span>
  <b style="color:#f1c40f; margin-right:4px; font-size:12px">Group by:</b>
  <label style="cursor:pointer; font-size:12px; color:#ecf0f1">
    <input type="checkbox" class="vgb-cb" value="none"
           onchange="toggleGby('none')" checked> None
  </label>
  <!-- Lot | Wafer | Layout | Material — same structure (class="vgb-cb") -->

  <!-- Separator -->
  <span style="width:1px; background:#4a6278; align-self:stretch; margin:0 4px"></span>

  <!-- Per site checkbox -->
  <label style="cursor:pointer; font-size:12px; color:#ecf0f1;
                display:flex; align-items:center; gap:4px">
    <input type="checkbox" id="var-persite-cb" checked
           onchange="_VAR_PER_SITE = this.checked; drawAllCharts()">
    Per site
  </label>

  <!-- Separator -->
  <span style="width:1px; background:#4a6278; align-self:stretch; margin:0 4px"></span>

  <!-- Chart height slider -->
  <label style="cursor:default; color:#ecf0f1;
                display:flex; align-items:center; gap:5px">
    ↕ Height    <!-- ↕ = &#11041; -->
    <input id="chart-h-slider" type="range"
           min="150" max="1200" step="50" value="480"
           oninput="_CHART_H = +this.value;
                    document.getElementById('chart-h-val').textContent = this.value + 'px';
                    drawAllCharts()"
           style="width:100px; vertical-align:middle; accent-color:#3498db">
    <span id="chart-h-val" style="min-width:34px; font-size:10px; color:#aed6f1">480px</span>
  </label>

</div>
```

Height slider: `min=150`, `max=1200`, `step=50`, default `480`.  
`_VAR_PER_SITE` default: `true`.

---

## 10. Toolbar Group Filter Buttons (tab bar)

Appended to the tab bar, right-aligned via `flex:1` spacer:

```html
<span style="display:flex; align-items:center; padding:0 6px; gap:4px">
  <button class="wfr-btn" onclick="setAllGroups(true)"
          title="Show all groups"
          style="padding:2px 8px; font-size:10px">All</button>
  <button class="wfr-btn" onclick="setAllGroups(false)"
          title="Hide all groups"
          style="padding:2px 8px; font-size:10px">None</button>

  <!-- Divider -->
  <span style="width:1px; background:#4a6278; align-self:stretch; margin:4px 2px"></span>

  <!-- One per group — class "grp-btn" always; "grp-off" when hidden -->
  <button class="wfr-btn grp-btn"
          onclick="toggleGrp('{g}', this)"
          style="padding:2px 8px; font-size:10px">
    <span style="font-size:9px; color:#95a5a6; font-weight:normal">
      [{n} params]
    </span>
    {groupName}
  </button>
</span>
```

`.wfr-btn` CSS:
```css
.wfr-btn { padding:3px 10px; font-size:11px; border:1px solid #bdc3c7;
           border-radius:3px; background:#f8f9fa; cursor:pointer; margin-left:4px }
.wfr-btn:hover { background:#d6eaff; border-color:#2980b9 }
```

`.grp-off` (hidden group):
```css
button.grp-off { background:rgba(0,0,0,0.3) !important; opacity:0.7;
                 text-decoration:line-through; color:#fff !important }
```

---

## 11. CSV Downloads

### `downloadVarCSV()` — Summary CSV

Columns: `Group, Parameter, N, Median, Std, Spread (%), Min, Max, LSL, USL, Unit`  
One row per parameter across all visible groups.  
Filename: `pcm_variability_{YYYY-MM-DDTHH-MM}.csv`

### `downloadSiteCSV()` — Per-site wide CSV

Columns: `Lot, Wafer, Program, Material, Site, {param1}, {param2}, ...`  
One row per (lot × wafer × site index). Site index is 1-based.  
Filename: `pcm_sites_{YYYY-MM-DDTHH-MM}.csv`

Both use `_csvBlob(lines, fname)` to trigger a browser download.

---

## 12. Param Detail Modal (opened by clicking a row)

### Overlay structure

```html
<div id="pm-overlay" class="pm-overlay">    <!-- display:flex when open -->
  <div class="pm-card">

    <!-- Header -->
    <div class="pm-hdr">
      <span class="pm-hdr-title" id="pm-title">
        {param} — {name}
      </span>
      <button class="pm-close" onclick="_closeParamModal()">✕</button>
    </div>

    <!-- Body: stats + histogram + strip chart + legend -->
    <div class="pm-body" id="pm-body">
      <!-- built by _buildParamModalChart(param) -->
    </div>

  </div>
</div>
```

CSS:
```css
.pm-overlay { position:fixed; inset:0; background:rgba(10,14,26,0.72);
              z-index:10000; display:none; align-items:center;
              justify-content:center }
.pm-card { background:#fff; border-radius:8px;
           box-shadow:0 8px 40px rgba(0,0,0,.45);
           width:min(96vw, 860px); max-height:92vh;
           display:flex; flex-direction:column; overflow:hidden }
.pm-hdr { display:flex; align-items:center; justify-content:space-between;
          padding:10px 16px; background:#2c3e50; color:#fff; flex-shrink:0 }
.pm-hdr-title { font-size:13px; font-weight:bold; overflow:hidden;
                text-overflow:ellipsis; white-space:nowrap;
                flex:1; margin-right:8px }
.pm-close { background:none; border:1px solid #7f8c8d; color:#bdc3c7;
            font-size:16px; line-height:1; padding:2px 8px;
            border-radius:4px; cursor:pointer }
.pm-close:hover { background:#e74c3c; border-color:#e74c3c; color:#fff }
.pm-body { flex:1; overflow-y:auto; padding:12px 16px; background:#f0f2f5 }
```

Closed by: clicking `✕` or pressing `Escape`.

### Modal stats row (`.pm-stat-row`)

```html
<div class="pm-stat-row">
  <div class="pm-stat">
    <span class="pm-stat-lbl">N</span>
    <span class="pm-stat-val" style="color:#2c3e50">{n}</span>
  </div>
  <div class="pm-stat">
    <span class="pm-stat-lbl">Median</span>
    <span class="pm-stat-val" style="color:#27ae60">{median}</span>
  </div>
  <!-- σ | Spread (%) | P1 | P99 | LSL (if set) | USL (if set) | Unit -->
</div>
```

```css
.pm-stat-row { display:flex; flex-wrap:wrap; background:#fff;
               border:1px solid #e0e0e0; border-radius:5px;
               margin-bottom:10px; overflow:hidden }
.pm-stat { display:inline-flex; flex-direction:column; align-items:center;
           gap:1px; padding:5px 14px; border-right:1px solid #eee }
.pm-stat-lbl { font-size:9px; color:#888; font-weight:bold;
               text-transform:uppercase; white-space:nowrap }
.pm-stat-val { font-size:14px; font-weight:bold }
```

Stat cells in order:

| Label | Value colour | Notes |
|---|---|---|
| `N` | `#2c3e50` | Full un-clipped count |
| `Median` | `#27ae60` | Full un-clipped |
| `σ` | `#2c3e50` | From P1–P99 clipped |
| `Spread (%)` | `#2c3e50` | `cv.toFixed(1) + "%"` or `—` |
| `P1` | `#7f8c8d` | 1st percentile |
| `P99` | `#7f8c8d` | 99th percentile |
| `LSL` | `#c0392b` | Only if `meta.lsl != null` |
| `USL` | `#2980b9` | Only if `meta.usl != null` |
| `Unit` | `#555` | Only if `unit` is non-empty |

### Modal histogram SVG

| Variable | Value |
|---|---|
| `svgW` | `820` |
| `svgH` | `300` |
| `ML` | `64` |
| `MR` | `20` |
| `MT` | `36` |
| `MB` | `68` |
| `plotW` | `736` |
| `plotH` | `196` |

```html
<svg width="100%" viewBox="0 0 820 300" style="display:block; background:#f8f9fa">
```

Y-axis: 6 grid lines, `font-size:14`, `fill:#555`.  
Y-axis title: `"Count"` at `x=13`.  
Bins: `max(12, min(50, ceil(sqrt(N) × 2.5)))`.  
Bars: `opacity:0.72`, `rx:1`.

Vertical lines in the modal histogram:

| Line | Colour | `stroke-width` | `stroke-dasharray` | Label anchor |
|---|---|---|---|---|
| LSL | `#c0392b` | `2` | `5,4` | `start` (right side of line) |
| USL | `#2980b9` | `2` | `5,4` | `end` (left side of line) |
| Median | `#27ae60` | `2` | `5,4` | `start` |

Label position: `y = MT - 7` (7px above plot top).

X-axis ticks: 8 ticks (xi=0..7), `font-size:13`, `fill:#555`.  
X-axis label: `y = svgH - 4`, `font-size:14 bold`, `fill:#333`.

### Modal strip chart SVG (below histogram)

```html
<svg width="100%" viewBox="0 0 820 70"
     style="display:block; background:#fff; border-top:1px solid #e8e8e8">
```

Dimensions: W=820, H=70, ML=64 (same as histogram), MT=18, MB=14, `plotH=38`.

Elements rendered (same x-scale as histogram):
- Plot background rect: `fill:#f8f9fa; rx:2`
- LSL / USL dashed lines: `stroke-width:1.5; stroke-dasharray:4,3`
- Median line: `stroke:#27ae60; stroke-width:2; stroke-dasharray:5,3`
- IQR box (Q1–Q3): `fill:rgba(39,174,96,0.12); stroke:#27ae60; stroke-width:1`
- Scatter dots: `m-3,0a3,3,0,1,0,6,0…` path, `opacity:0.60`
- "Strip (each dot = one measurement)" label at `x=ML, y=12`, `font-size:11; fill:#888`

Dots use `_sRand(ri×997 + vi)` for deterministic jitter: `±sPlotH×0.35` vertically.

### Modal group legend (`.pm-grp-leg`)

Shown only when `grpOrder.length > 1`:

```html
<div class="pm-grp-leg">
  <span style="display:flex; align-items:center; gap:3px">
    <span style="width:10px; height:10px; background:{colour};
                 display:inline-block; border-radius:2px"></span>
    {groupKey}
  </span>
</div>
```

```css
.pm-grp-leg { display:flex; flex-wrap:wrap; gap:4px 14px;
              font-size:11px; margin-top:6px; padding:4px 2px }
```

---

## 13. Strip Chart Highlight for `SEL_PARAM`

When a row is selected (`SEL_PARAM !== null`), `_drawGroupChart()` renders a blue highlight column for that parameter in every group strip chart:

```html
<!-- Blue translucent column rect -->
<rect x="{xPos(i) - xStep/2}" y="{MT}" width="{xStep}" height="{CH}"
      fill="rgba(52,152,219,0.10)" stroke="#3498db" stroke-width="1.2"/>
```

Colours: `fill:rgba(52,152,219,0.10)`, `stroke:#3498db`.

Non-selected columns with odd index get a subtle alternating tint:
```html
<rect fill="rgba(0,0,0,0.02)"/>
```

---

## 14. Key Functions Reference

| Function | Purpose |
|---|---|
| `buildParamTable()` | Rebuild `var-head` + `var-body` from current selection + group state |
| `selParam(param)` | Toggle `SEL_PARAM`; call `rerender()`; open modal if selecting |
| `toggleGrpRow(grp)` | Toggle `_GRP_STATE[grp]` (expand/collapse within table); rebuild table |
| `toggleGrp(grp, btn)` | Toggle `_GRP_VIS[grp]` (show/hide group entirely); rerender |
| `setAllGroups(visible)` | Set all groups visible/hidden; rerender |
| `toggleP2()` | Add/remove `p2-hidden` from `#panel2`; flip arrow button |
| `downloadVarCSV()` | Download summary table as CSV |
| `downloadSiteCSV()` | Download per-site wide CSV |
| `_paramStats(param)` | Compute `{n, median, std, cv, min, max}` for a param |
| `_toDisplayVals(param, vals)` | Convert raw values to display units (Td_, leakage) |
| `_showParamModal(param)` | Build and show the param detail modal |
| `_buildParamModalChart(param)` | Render histogram + strip + stats inside `#pm-body` |
| `_closeParamModal()` | Hide `#pm-overlay` |
| `drawAllCharts()` | Re-draw all visible strip chart SVGs (triggered by gby/height/persite changes) |
