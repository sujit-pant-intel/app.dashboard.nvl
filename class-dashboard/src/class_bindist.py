"""class_bindist.py — CLASS bin distribution HTML generator.

Simple, self-contained HTML showing interface-bin distribution with an
interactive lot / wafer filter.  No yield-target table, no bindef parsing.

Public API
----------
    generate(csv_path, out_dir=None, output_path=None) -> str
        Returns the path to the generated HTML file.
"""

from __future__ import annotations
import json
import os
from pathlib import Path

try:
    from csv_utils import detect_encoding, sniff_columns, read_csv_smart
    _HAS_CSV_UTILS = True
except ImportError:
    _HAS_CSV_UTILS = False

try:
    from _constants import _wm_inject
except ImportError:
    def _wm_inject(html: str) -> str:  # type: ignore[misc]
        return html


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate(csv_path, out_dir=None, output_path=None) -> str:
    """Build ``*_BinDistribution.html`` for CLASS data.

    Parameters
    ----------
    csv_path:    Input CSV (path to merged CLASS data after material/reticle merge)
    out_dir:     Output directory.  Defaults to csv_path parent / 'output'.
    output_path: Override the full output file path (takes precedence over out_dir).

    Returns the path to the generated HTML.
    """
    import pandas as pd

    csvp = Path(csv_path)

    # ── Column detection ─────────────────────────────────────────────────────
    if _HAS_CSV_UTILS:
        enc      = detect_encoding(csvp)
        all_cols = sniff_columns(csvp, encoding=enc)
    else:
        enc = None
        for _e in ("utf-8-sig", "utf-16", "latin-1"):
            try:
                all_cols = list(pd.read_csv(csvp, nrows=0, encoding=_e).columns)
                enc = _e
                break
            except Exception:
                pass
        else:
            all_cols = []

    bin_col = next(
        (c for c in all_cols if "INTERFACE_BIN" in c.upper() and "TOTAL" not in c.upper()),
        None,
    )
    if not bin_col:
        raise RuntimeError(f"No INTERFACE_BIN column found in {csvp.name}")

    lot_col = next(
        (c for c in all_cols if c.lower() == "lot"),
        next((c for c in all_cols if "sort_lot" in c.lower()), None),
    )
    waf_col = next(
        (c for c in all_cols if c.lower() in ("wafer", "sort_wafer")),
        next((c for c in all_cols if "sort_wafer" in c.lower()), None),
    )

    # ── Load only needed columns ──────────────────────────────────────────────
    usecols = [bin_col]
    if lot_col:
        usecols.append(lot_col)
    if waf_col and waf_col not in usecols:
        usecols.append(waf_col)

    if _HAS_CSV_UTILS:
        df = read_csv_smart(csvp, usecols=usecols, encoding=enc)
    else:
        df = pd.read_csv(csvp, usecols=usecols, encoding=enc, low_memory=False)

    # Normalise bin column → integer string (strip letters like "B")
    import re as _re
    df["_bin"] = (
        df[bin_col].astype(str)
        .str.extract(r"(\d+)", expand=False)
        .fillna("?")
    )
    df["_lot"]   = df[lot_col].astype(str).str.strip()   if lot_col else "ALL"
    df["_wafer"] = df[waf_col].astype(str).str.strip()   if waf_col else "1"

    # ── Build per-(lot, wafer, bin) counts ───────────────────────────────────
    grp = (
        df.groupby(["_lot", "_wafer", "_bin"], sort=False)
        .size()
        .reset_index(name="n")
    )

    rows = []
    for _, r in grp.iterrows():
        rows.append({"lot": r["_lot"], "wafer": r["_wafer"],
                     "bin": r["_bin"], "n": int(r["n"])})

    # Sorted unique bins for chart axis
    def _bsort(b):
        try:
            return (0, int(b))
        except Exception:
            return (1, b)

    all_bins = sorted({r["bin"] for r in rows}, key=_bsort)

    # Lots in order of first appearance
    seen: list = []
    for r in rows:
        if r["lot"] not in seen:
            seen.append(r["lot"])
    all_lots = seen

    # ── Output path ──────────────────────────────────────────────────────────
    if output_path:
        html_out = Path(output_path)
    else:
        out_d = Path(out_dir) if out_dir else csvp.parent / "output"
        out_d.mkdir(parents=True, exist_ok=True)
        html_out = out_d / f"{csvp.stem}_BinDistribution.html"

    html_out.parent.mkdir(parents=True, exist_ok=True)

    # ── Generate HTML ─────────────────────────────────────────────────────────
    html = _render_html(
        rows     = rows,
        all_bins = all_bins,
        all_lots = all_lots,
        title    = csvp.stem,
        bin_col  = bin_col,
    )
    html_out.write_text(_wm_inject(html), encoding="utf-8")
    return str(html_out)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

def _render_html(rows, all_bins, all_lots, title, bin_col):
    data_json  = json.dumps(rows,     separators=(",", ":"))
    bins_json  = json.dumps(all_bins, separators=(",", ":"))
    lots_json  = json.dumps(all_lots, separators=(",", ":"))

    # Colour map: bin 1 = green, bin 2 = blue, 3/4 = teal/cyan,
    # all others cycle through a palette
    _PALETTE = [
        "#27ae60", "#2980b9", "#16a085", "#8e44ad", "#e67e22",
        "#e74c3c", "#f39c12", "#1abc9c", "#3498db", "#9b59b6",
        "#d35400", "#2ecc71", "#c0392b", "#7f8c8d", "#f1c40f",
        "#2c3e50", "#e8daef", "#a9cce3", "#a9dfbf", "#f9e79f",
    ]
    bin_colors_js = "{"
    for i, b in enumerate(all_bins):
        c = _PALETTE[i % len(_PALETTE)]
        bin_colors_js += f'"{b}":"{c}",'
    bin_colors_js = bin_colors_js.rstrip(",") + "}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title} — Bin Distribution</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Arial,sans-serif;background:#1a252f;color:#ecf0f1;display:flex;
     flex-direction:column;min-height:100vh}}
/* ── Header ── */
#hdr{{background:#0f1e2b;padding:10px 18px;display:flex;align-items:baseline;gap:14px;
      border-bottom:1px solid #0a1520}}
#hdr h1{{font-size:15px;color:#27ae60;font-weight:700}}
#hdr span{{font-size:11px;color:#95a5a6}}
/* ── Layout ── */
#layout{{display:flex;flex:1;overflow:hidden}}
/* ── Filter sidebar ── */
#sidebar{{width:220px;min-width:180px;background:#0f1e2b;padding:10px;
          overflow-y:auto;border-right:1px solid #0a1520;font-size:12px}}
#sidebar h2{{font-size:11px;color:#95a5a6;text-transform:uppercase;
             letter-spacing:.06em;margin-bottom:6px}}
.lot-block{{margin-bottom:10px}}
.lot-hdr{{display:flex;align-items:center;gap:6px;margin-bottom:3px}}
.lot-hdr label{{font-weight:700;color:#3498db;cursor:pointer;font-size:12px}}
.wafer-row{{padding-left:14px;display:flex;align-items:center;gap:5px;
            margin:2px 0;color:#b0c4de;cursor:pointer}}
.wafer-row input{{accent-color:#27ae60;cursor:pointer}}
.sb-btns{{display:flex;gap:5px;margin-bottom:8px}}
.sb-btns button{{flex:1;padding:3px 0;font-size:11px;background:#2c3e50;
                 color:#ecf0f1;border:none;border-radius:3px;cursor:pointer}}
.sb-btns button:hover{{background:#3d5166}}
/* ── Main ── */
#main{{flex:1;padding:14px 18px;overflow-y:auto;display:flex;
       flex-direction:column;gap:14px}}
#summary{{font-size:12px;color:#95a5a6}}
#summary b{{color:#ecf0f1}}
/* ── Chart ── */
#chart-wrap{{background:#0f1e2b;border-radius:4px;padding:14px;overflow-x:auto}}
svg#bar-chart{{display:block}}
/* ── Table ── */
#tbl-wrap{{background:#0f1e2b;border-radius:4px;padding:10px}}
#tbl-wrap h3{{font-size:12px;color:#95a5a6;margin-bottom:6px;
              text-transform:uppercase;letter-spacing:.05em}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
th{{background:#2c3e50;color:#95a5a6;padding:5px 10px;text-align:left;
    font-weight:600;position:sticky;top:0}}
td{{padding:4px 10px;border-bottom:1px solid #243447;color:#ecf0f1}}
tr:hover td{{background:#243447}}
.swatch{{display:inline-block;width:10px;height:10px;border-radius:2px;
          margin-right:5px;vertical-align:middle}}
</style>
</head>
<body>
<div id="hdr">
  <h1>Bin Distribution</h1>
  <span id="hdr-sub">{title}</span>
</div>
<div id="layout">
  <!-- Filter sidebar -->
  <div id="sidebar">
    <h2>Lot / Wafer Filter</h2>
    <div class="sb-btns">
      <button onclick="_selAll(true)">All</button>
      <button onclick="_selAll(false)">None</button>
    </div>
    <div id="filter-tree"></div>
  </div>
  <!-- Main content -->
  <div id="main">
    <div id="summary">Loading...</div>
    <div id="chart-wrap"><svg id="bar-chart"></svg></div>
    <div id="tbl-wrap">
      <h3>Bin Counts</h3>
      <table id="bin-table">
        <thead><tr><th>Bin</th><th>Count</th><th>%</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
</div>

<script>
const _DATA  = {data_json};
const _BINS  = {bins_json};
const _LOTS  = {lots_json};
const _CLRS  = {bin_colors_js};

/* ── Build filter tree ─────────────────────────────────────────────────── */
const _tree = document.getElementById('filter-tree');

// Group wafers by lot
const _lwMap = {{}};
_DATA.forEach(r => {{
  if (!_lwMap[r.lot]) _lwMap[r.lot] = new Set();
  _lwMap[r.lot].add(r.wafer);
}});

_LOTS.forEach(lot => {{
  const wafers = Array.from(_lwMap[lot] || []).sort((a,b) => {{
    const na = parseInt(a), nb = parseInt(b);
    return isNaN(na)||isNaN(nb) ? a.localeCompare(b) : na-nb;
  }});
  const blk = document.createElement('div');
  blk.className = 'lot-block';
  blk.id = 'lot_' + _esc(lot);

  const hdr = document.createElement('div');
  hdr.className = 'lot-hdr';

  const lbl = document.createElement('label');
  lbl.textContent = lot;
  lbl.style.cursor = 'pointer';
  lbl.onclick = () => _toggleLot(lot);
  hdr.appendChild(lbl);

  const btns = document.createElement('div');
  btns.style.display='flex'; btns.style.gap='4px'; btns.style.marginLeft='auto';
  ['All','None'].forEach((t,i) => {{
    const b = document.createElement('button');
    b.textContent=t; b.style.fontSize='10px';
    b.style.padding='1px 5px'; b.style.background='#2c3e50';
    b.style.color='#ecf0f1'; b.style.border='none';
    b.style.borderRadius='2px'; b.style.cursor='pointer';
    b.onclick = () => _selLot(lot, i===0);
    btns.appendChild(b);
  }});
  hdr.appendChild(btns);
  blk.appendChild(hdr);

  wafers.forEach(w => {{
    const row = document.createElement('div');
    row.className = 'wafer-row';
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.checked = true;
    cb.id = 'cb_' + _esc(lot) + '_' + _esc(w);
    cb.onchange = _refresh;
    const lw = document.createElement('label');
    lw.htmlFor = cb.id;
    lw.textContent = 'W' + w;
    row.appendChild(cb); row.appendChild(lw);
    blk.appendChild(row);
  }});
  _tree.appendChild(blk);
}});

function _esc(s){{ return s.replace(/[^a-zA-Z0-9]/g,'_'); }}

function _selAll(v){{
  document.querySelectorAll('#filter-tree input[type=checkbox]')
    .forEach(cb => cb.checked = v);
  _refresh();
}}
function _selLot(lot, v){{
  const blk = document.getElementById('lot_'+_esc(lot));
  if (blk) blk.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked=v);
  _refresh();
}}
function _toggleLot(lot){{
  const blk = document.getElementById('lot_'+_esc(lot));
  if (!blk) return;
  const cbs = blk.querySelectorAll('input[type=checkbox]');
  const any = Array.from(cbs).some(cb=>cb.checked);
  cbs.forEach(cb=>cb.checked=!any);
  _refresh();
}}

/* ── Compute filtered bin counts ───────────────────────────────────────── */
function _getSelected(){{
  const sel = new Set();
  document.querySelectorAll('#filter-tree input:checked').forEach(cb=>{{
    const parts = cb.id.replace('cb_','').split('_');
    // id = cb_<lot_esc>_<wafer_esc> — match back by checkbox position
    sel.add(cb.id);
  }});
  return sel;
}}

function _computeCounts(){{
  // Build set of checked (lot,wafer) pairs
  const checked = new Set();
  document.querySelectorAll('#filter-tree input:checked').forEach(cb=>{{
    checked.add(cb.id);
  }});
  const binCts = {{}};
  let total = 0;
  _DATA.forEach(r => {{
    const cbid = 'cb_'+_esc(r.lot)+'_'+_esc(r.wafer);
    if (!checked.has(cbid)) return;
    binCts[r.bin] = (binCts[r.bin]||0) + r.n;
    total += r.n;
  }});
  return {{binCts, total}};
}}

/* ── Render bar chart ──────────────────────────────────────────────────── */
function _drawChart(binCts, total){{
  const svg = document.getElementById('bar-chart');
  const W = Math.max(600, _BINS.length * 36 + 80);
  const H = 320;
  const PL=60, PR=20, PT=20, PB=60;
  const cw = (W-PL-PR) / Math.max(_BINS.length,1);
  const maxPct = Math.max(..._BINS.map(b=>(binCts[b]||0)/Math.max(total,1)*100), 1);
  const yScale = (H-PT-PB) / maxPct;

  let s = `<svg id="bar-chart" width="${{W}}" height="${{H}}" xmlns="http://www.w3.org/2000/svg"
    style="font-family:Arial,sans-serif">`;

  // Grid lines
  const ticks = 5;
  for (let i=0;i<=ticks;i++){{
    const yv = maxPct*i/ticks;
    const yp = H-PB-yv*yScale;
    s += `<line x1="${{PL}}" x2="${{W-PR}}" y1="${{yp}}" y2="${{yp}}"
           stroke="#243447" stroke-width="1"/>`;
    s += `<text x="${{PL-6}}" y="${{yp+4}}" text-anchor="end" fill="#95a5a6"
           font-size="9">${{yv.toFixed(1)}}%</text>`;
  }}

  // Bars
  _BINS.forEach((b,i)=>{{
    const pct = (binCts[b]||0)/Math.max(total,1)*100;
    const bh  = pct*yScale;
    const bx  = PL + i*cw + cw*0.12;
    const by  = H-PB-bh;
    const bw  = cw*0.76;
    const clr = _CLRS[b] || '#7f8c8d';
    s += `<rect x="${{bx}}" y="${{by}}" width="${{bw}}" height="${{bh}}"
           fill="${{clr}}" rx="2">
           <title>Bin ${{b}}: ${{pct.toFixed(2)}}% (${{binCts[b]||0}} dies)</title>
         </rect>`;
    if (pct>0.3){{
      s += `<text x="${{bx+bw/2}}" y="${{by-4}}" text-anchor="middle"
             fill="#ecf0f1" font-size="9">${{pct.toFixed(1)}}%</text>`;
    }}
    // X label
    s += `<text x="${{bx+bw/2}}" y="${{H-PB+14}}" text-anchor="middle"
           fill="#95a5a6" font-size="10">${{b}}</text>`;
  }});

  // Axes
  s += `<line x1="${{PL}}" x2="${{PL}}"    y1="${{PT}}" y2="${{H-PB}}" stroke="#95a5a6" stroke-width="1"/>`;
  s += `<line x1="${{PL}}" x2="${{W-PR}}"  y1="${{H-PB}}" y2="${{H-PB}}" stroke="#95a5a6" stroke-width="1"/>`;
  s += `<text x="${{W/2}}" y="${{H-4}}" text-anchor="middle" fill="#95a5a6" font-size="11">Interface Bin</text>`;
  s += `<text x="${{10}}" y="${{H/2}}" text-anchor="middle" fill="#95a5a6" font-size="11"
         transform="rotate(-90,${{10}},${{H/2}})">% Dies</text>`;
  s += '</svg>';
  svg.outerHTML = s;  // replace the SVG element
  // reattach (outerHTML replacement detaches)
  document.getElementById('chart-wrap').innerHTML =
    `<div style="overflow-x:auto">${{s}}</div>`;
}}

/* ── Render table ──────────────────────────────────────────────────────── */
function _drawTable(binCts, total){{
  const tbody = document.querySelector('#bin-table tbody');
  tbody.innerHTML = '';
  _BINS.forEach(b=>{{
    const cnt = binCts[b]||0;
    if (!cnt) return;
    const pct = (cnt/Math.max(total,1)*100).toFixed(2);
    const clr = _CLRS[b]||'#7f8c8d';
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><span class="swatch" style="background:${{clr}}"></span>Bin ${{b}}</td>
                    <td>${{cnt.toLocaleString()}}</td>
                    <td>${{pct}}%</td>`;
    tbody.appendChild(tr);
  }});
  // Totals row
  const tr2 = document.createElement('tr');
  tr2.innerHTML = `<td><b>Total</b></td><td><b>${{total.toLocaleString()}}</b></td><td><b>100%</b></td>`;
  tr2.style.fontWeight='bold';
  tbody.appendChild(tr2);
}}

/* ── Main refresh ──────────────────────────────────────────────────────── */
function _refresh(){{
  const {{binCts, total}} = _computeCounts();
  // Count selected wafers
  const selWafers = document.querySelectorAll('#filter-tree input:checked').length;
  const totWafers = document.querySelectorAll('#filter-tree input').length;
  document.getElementById('summary').innerHTML =
    `<b>${{total.toLocaleString()}}</b> dies &nbsp;|&nbsp; ` +
    `<b>${{selWafers}}</b> / ${{totWafers}} lot-wafers selected`;
  _drawChart(binCts, total);
  _drawTable(binCts, total);
}}

// Initial render
_refresh();
</script>
</body>
</html>"""
