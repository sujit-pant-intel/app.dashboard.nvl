"""_dash_frame.py — CSS + page-frame HTML for the SICC/UPM/CDYN dashboard.

These are plain string constants (no f-string escaping required).
"""

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Arial,sans-serif;background:#f0f2f5;color:#2c3e50;font-size:13px}
.page-hdr{background:#1f3a50;color:#fff;padding:10px 16px}
.page-hdr h1{font-size:14px;font-weight:bold}
.page-hdr .sub{font-size:11px;color:#aed6f1;margin-top:2px}
.filter-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:7px 14px;background:#fff;border-bottom:1px solid #dde}
.filter-row label{font-size:11px;color:#7f8c8d}
.ft-btn{padding:3px 10px;border:1px solid #bdc3c7;border-radius:4px;cursor:pointer;font-size:11px;background:#ecf0f1;color:#2c3e50}
.ft-btn.active{border-color:#2980b9;background:#d6eaff;color:#1a6491;font-weight:bold}
.tabs{display:flex;align-items:center;background:#2c3e50;padding:0 12px}
.tab-btn{padding:8px 22px;border:none;background:transparent;color:#95a5a6;cursor:pointer;font-size:12px;font-weight:bold;border-bottom:3px solid transparent}
.tab-btn.active{color:#3498db;border-bottom-color:#3498db}
.tab-panel{display:none}.tab-panel.active{display:block}
.wfr-btn{padding:3px 10px;font-size:11px;border:1px solid #bdc3c7;border-radius:3px;background:#f8f9fa;cursor:pointer;margin-left:4px}
.wfr-btn:hover{background:#d6eaff;border-color:#2980b9}
.main-layout{display:flex;gap:0;align-items:flex-start}
.tab-content{flex:1;min-width:0;overflow:hidden;padding:10px 14px}
.legend{display:flex;flex-wrap:wrap;gap:10px;align-items:center;font-size:11px;color:#7f8c8d;padding:4px 0 8px}
.ld{width:12px;height:12px;border-radius:2px;display:inline-block;margin-right:2px}
.hm-wrap{overflow-x:auto;margin-top:2px}
.hm-tbl{border-collapse:collapse;font-size:12px;white-space:nowrap}
.hm-tbl th{background:#2c3e50;color:#fff;padding:6px 12px;text-align:center;position:sticky;top:0;z-index:1}
.hm-tbl th.sticky-l{position:sticky;left:0;z-index:2;text-align:left;min-width:200px;background:#2c3e50}
.hm-tbl td{padding:5px 12px;border-bottom:1px solid #eee;text-align:right}
.hm-tbl td.tn{position:sticky;left:0;background:#f8f9fa;text-align:left;cursor:pointer;min-width:200px;border-right:2px solid #dde;z-index:1;font-size:12px}
.hm-tbl td.tn:hover{background:#eaf4ff}
.hm-tbl td.tn.sel{background:#d6eaff;border-left:3px solid #2980b9;font-weight:bold}
.hm-tbl tr.sel-row{background:#dbeafc !important}
.hm-tbl tr.sel-row td{font-weight:600}
.hm-tbl tr.sel-row td.tn{background:#d6eaff;border-left:3px solid #2980b9}
.hm-tbl tbody tr:not(.cat-hdr):hover{background:#eaf4ff !important;cursor:pointer}
.hm-tbl .tgt{color:#7f8c8d;font-style:italic;background:#f8f9fa}
.hm-tbl .ov{font-weight:bold;background:#eaf4ff}
.hm-tbl .ov.cell-r{background:#fdecea!important}
.cell-r{background:#fdecea!important;color:#c0392b;font-weight:bold}
.cell-y{background:#fef9e7!important;color:#7d6608}
.cell-g{background:#eafaf1!important;color:#1e8449}
.hm-tbl .ssep td{background:#eaf0fb;color:#1f618d;font-weight:bold;font-size:11px;padding:3px 12px;border-top:2px solid #aed6f1}
.dist-wrap{display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap}
/* Filter-by-Lot/Wafer table */
.wfr-panel{flex:0 0 auto;min-width:0;transition:width .15s}
.wfr-box{border:1px solid #dde;border-radius:4px;background:#fff;overflow:hidden}
.wfr-hdr{display:flex;justify-content:space-between;align-items:center;padding:6px 10px;background:#2c3e50;color:#fff;font-size:11px;font-weight:bold}
.wfr-hdr .cb{background:none;border:1px solid #7f8c8d;color:#bdc3c7;font-size:10px;padding:2px 8px;cursor:pointer;border-radius:3px;margin-left:3px}
.wfr-hdr .cb:hover{background:#3d5166;color:#fff}
.row-info{font-size:10px;color:#aed6f1;margin-left:8px}
.wfr-tbl-wrap{max-height:calc(100vh - 120px);overflow-y:auto}
.wfr-tbl{border-collapse:collapse;width:auto;font-size:12px;white-space:nowrap}
.wfr-tbl th{background:#34495e;color:#ecf0f1;padding:5px 10px;text-align:left;position:sticky;top:0;z-index:2;white-space:nowrap}
.wfr-tbl td{padding:4px 10px;border-bottom:1px solid #f0f0f0;cursor:pointer}
.wfr-tbl .num{text-align:right}
.wfr-tbl .fr:hover td{background:#eaf4ff}
.wfr-tbl .frs td{background:#d6eaff}
.wfr-tbl .frs:hover td{background:#bcd8f8}
.flt-btn{background:none;border:none;color:#aed6f1;cursor:pointer;font-size:11px;padding:0 0 0 3px;vertical-align:middle;opacity:.85}
.flt-btn:hover{opacity:1;color:#fff}
.flt-btn.active{color:#f1c40f!important;opacity:1}
.chart-panel{flex:1;min-width:300px}
.col-pills{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px}
.pill{padding:4px 10px;border:1px solid #bdc3c7;border-radius:12px;cursor:pointer;font-size:11px;background:#ecf0f1;color:#2c3e50}
.pill.active{background:#1f618d;color:#fff;border-color:#1f618d}
.pill.cdyn-pill{background:#fdf2f8;border-color:#d7bde2}
.pill.cdyn-pill.active{background:#7b241c;color:#fff}
.pill-sep{width:100%;border:none;border-top:1px dashed #ccc;margin:2px 0}
#hist-svg{width:100%;display:block;border:1px solid #eee;border-radius:4px;background:#fff}
.chart-note{font-size:10px;color:#7f8c8d;margin-top:4px}
.dd-panel{position:fixed;background:#fff;border:1px solid #aaa;border-radius:4px;box-shadow:0 4px 16px rgba(0,0,0,.18);z-index:9999;min-width:180px;max-width:260px;font-size:12px;color:#2c3e50}
.dd-panel input.dds{width:100%;padding:5px 8px;border:none;border-bottom:1px solid #ddd;font-size:12px;outline:none}
.dd-panel .dda{display:flex;gap:4px;padding:4px 6px;border-bottom:1px solid #eee}
.dd-panel .dda button{flex:1;padding:2px 6px;font-size:11px;cursor:pointer;border:1px solid #bdc3c7;background:#ecf0f1;border-radius:3px}
.dd-panel .ddl{max-height:200px;overflow-y:auto;padding:4px 0}
.dd-panel .ddi{display:flex;align-items:center;gap:6px;padding:3px 10px;cursor:pointer}
.dd-panel .ddi:hover{background:#eaf0fb}
.dd-panel .ddf{padding:4px 8px;border-top:1px solid #eee;text-align:right}
.dd-panel .ddf button{padding:3px 12px;font-size:11px;cursor:pointer;background:#2c3e50;color:#fff;border:none;border-radius:3px}
/* Category-coloured summary table */
.cat-tbl{border-collapse:collapse;font-size:12px;white-space:nowrap;width:100%}
.cat-tbl th{background:#2c3e50;color:#fff;padding:6px 12px;text-align:center;position:sticky;top:0;z-index:1}
.cat-tbl th:first-child{text-align:left;min-width:200px}
.cat-tbl td{padding:5px 12px;border-bottom:1px solid #eee;text-align:right}
.cat-tbl td:first-child{text-align:left;font-weight:500}
.cat-tbl .cat-hdr td{font-weight:bold;font-size:12px;padding:6px 12px;border-top:2px solid #ccc}
.cat-wrap{overflow-x:auto;margin-top:2px}
.cat-legend{display:flex;flex-wrap:wrap;gap:8px;padding:6px 0;font-size:11px;align-items:center}
.cat-swatch{width:14px;height:14px;border-radius:3px;display:inline-block;margin-right:3px;border:1px solid rgba(0,0,0,.15);cursor:pointer}
.cat-tog{display:inline-flex;align-items:center;gap:2px;cursor:pointer;padding:2px 6px;border-radius:4px;border:1px solid transparent;user-select:none}
.cat-tog:hover{border-color:#bbb}
.cat-tog.off{opacity:.35;text-decoration:line-through}
.side-layout{display:flex;gap:0;align-items:flex-start}
.side-layout .tbl-side{flex:0 1 auto;min-width:0;overflow-x:auto}
.side-layout .dist-side{flex:1 1 0;min-width:280px;overflow:hidden}
/* ── Resizable panel splitters ── */
.h-splitter{width:5px;flex-shrink:0;background:#d0d7de;cursor:col-resize;align-self:stretch;min-height:60px;border-radius:2px;transition:background .15s;user-select:none;position:relative}
.h-splitter:hover,.h-splitter.dragging{background:#2980b9}
.h-splitter::after{content:'⋮';position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#fff;font-size:14px;line-height:1;pointer-events:none}
.v-resize-handle{height:7px;background:#e8ecf0;cursor:ns-resize;border-radius:0 0 4px 4px;margin-top:1px;display:flex;align-items:center;justify-content:center;user-select:none;transition:background .15s}
.v-resize-handle:hover,.v-resize-handle.dragging{background:#2980b9}
.v-resize-handle::after{content:'—';color:#aaa;font-size:10px;line-height:1}
.v-resize-handle:hover::after,.v-resize-handle.dragging::after{color:#fff}
.collapse-btn{background:none;border:1px solid #7f8c8d;color:#bdc3c7;font-size:10px;padding:1px 7px;cursor:pointer;border-radius:3px;margin-left:4px;line-height:1.4;user-select:none}
.collapse-btn:hover{background:#3d5166;color:#fff}
.wfr-panel{flex-shrink:0;overflow:hidden;transition:width .15s}
.sidebar-toggle{padding:5px 8px;border:none;background:transparent;color:#95a5a6;cursor:pointer;font-size:16px;line-height:1;border-right:1px solid #3d5166;align-self:stretch;display:flex;align-items:center}
.sidebar-toggle:hover{background:#3d5166;color:#fff}
.wfr-panel.collapsed .wfr-tbl-wrap{display:none}
.dist-side.collapsed>*:not(.dist-hdr){display:none!important}
.dist-hdr{display:flex;align-items:center;gap:6px;padding:4px 6px;background:#f0f4fb;border-bottom:1px solid #dde;font-size:11px;font-weight:bold;color:#2c3e50;border-radius:4px 4px 0 0;cursor:default}
"""


def build_page_open(display_title: str, tabs_html: str) -> str:
    """Return the opening HTML up to (and including) the tabs bar + main-layout open."""
    return (
        f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>UPM/SICC/CDYN Dashboard -- {display_title}</title>
<style>
{CSS}
</style>
</head>
<body>
<div class="page-hdr">
  <h1>UPM / SICC / CDYN Dashboard</h1>
  <div class="sub">{display_title}</div>
</div>

<div class="tabs">
  <button class="sidebar-toggle" id="sidebar-toggle-btn" onclick="togglePanel('wfr-panel','wfr-splitter')" title="Show/hide filter sidebar">&#9776;</button>
{tabs_html}
</div>
<div class="main-layout">
<div class="wfr-panel" id="wfr-panel" style="width:280px">
  <div class="wfr-box">
    <div class="wfr-hdr">
      <span>Filter by Lot / Wafer</span>
      <span>
        <button class="cb" onclick="selAll()">Select All</button>
        <button class="cb" onclick="clrAll()">Clear</button>
        <button class="collapse-btn" onclick="togglePanel('wfr-panel','wfr-splitter')" title="Collapse/expand panel">&#9664;</button>
        <span class="row-info" id="row-info"></span>
      </span>
    </div>
    <div class="wfr-tbl-wrap">
      <table class="wfr-tbl">
        <thead id="wfr-thead"></thead>
        <tbody id="wfr-tbody"></tbody>
      </table>
    </div>
  </div>
</div>
<div class="h-splitter" id="wfr-splitter" title="Drag to resize sidebar" onmousedown="startSplit(event,'wfr-panel','tab-content','wfr-panel-w')"></div>
<div class="tab-content" id="tab-content">
'''
    )


def build_page_close() -> str:
    """Return the closing HTML after all tab panels (before script)."""
    return '''</div><!-- /tab-content -->
</div><!-- /main-layout -->
'''
