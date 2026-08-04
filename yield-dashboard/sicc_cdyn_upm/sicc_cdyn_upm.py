"""sicc_cdyn_upm.py — Merged single-file SICC/UPM/CDYN pipeline.

Consolidates (10 files → 1):
  _tab_registry.py, _dash_frame.py, _dash_js_shared.py,
  _tab_summ.py, _tab_sicc.py, _tab_cdyn.py, _tab_charts.py,
  generate_dashboard_html_svg.py, sicc_processor.py, run_py_dashboard.py

Public API (unchanged):
  load_config(path)              -> dict
  process_csv(csv, cfg, ...)     -> dict
  generate_html_svg(data, path)  -> str
  run_python_pipeline(...)       -> None  (called by _pipeline_runner.py)

Run as GUI:
  python sicc_cdyn_upm.py

Headless (called by yield pipeline):
  python sicc_cdyn_upm.py --headless --csv-file <f> --output-dir <d> ...

External dependency (shared with yld/src/bin_distribution_html.py):
  _filter_lot_wafer.py  — lot/wafer filter CSS + JS
"""
import sys
sys.dont_write_bytecode = True

import json
import os
import re
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional
from tkinter import filedialog, messagebox
import tkinter as tk

import numpy as np
import pandas as pd

# ── Shared filter-by-Lot/Wafer dependency (lives in yld/src/) ────────────────
_THIS_DIR = Path(__file__).parent
_YLD_SRC = str((_THIS_DIR / "../yld").resolve())
if _YLD_SRC not in sys.path:
    sys.path.insert(0, _YLD_SRC)

from _filter_lot_wafer import (   # noqa: E402
    FILTER_TABLE_CSS as _FILTER_CSS,
    FILTER_DD_JS as _FILTER_DD_JS,
    make_filter_js as _make_filter_js,
)

# ════════════════════════════════════════════════════════════════
# Tab dataclass  (formerly _tab_registry.py)
# ════════════════════════════════════════════════════════════════
@dataclass
class Tab:
    tab_id: str            # HTML element id, e.g. "tab-sicc"
    label: str             # Button label shown in tabs bar
    active: bool           # True = initially active tab
    html_fn: Callable[[], str]   # Returns the <div id="tab-X"> panel HTML
    js_fn: Callable[[], str]     # Returns JS functions for this tab

# ════════════════════════════════════════════════════════════════
# CSS + page frame  (formerly _dash_frame.py)
# ════════════════════════════════════════════════════════════════

_CSS_BASE = """
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
/* ── All Medians right-expand panel (PCM-style) ── */
.all-med-toggle{width:22px;flex-shrink:0;background:#ecf0f1;cursor:pointer;display:flex;align-items:center;justify-content:center;align-self:stretch;border-left:1px solid #d0d7de;border-right:1px solid #d0d7de;user-select:none}
.all-med-toggle:hover{background:#d6eaff}
.all-med-toggle .amt-btn{background:none;border:none;font-size:13px;cursor:pointer;color:#2c3e50;line-height:1;padding:0}
#all-med-panel{width:0;min-width:0;overflow:hidden;transition:width 0.15s;display:flex;flex-direction:column;background:#fff;border-right:2px solid #d0d7de;flex-shrink:0;height:calc(100vh - 90px);align-self:flex-start}
#all-med-panel.open{width:420px;min-width:180px}
.all-med-hdr{background:#2c3e50;color:#fff;padding:6px 10px;font-size:11px;font-weight:bold;flex-shrink:0;display:flex;justify-content:space-between;align-items:center}
.all-med-body{flex:1;overflow:auto;padding:6px 8px 10px}
"""
# Append shared filter CSS (bin_distribution_html.py is the master)
CSS = _CSS_BASE + _FILTER_CSS
_CSS_BASE = None  # free the reference


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
        <button class="cb" onclick="selectAllRows()">Select All</button>
        <button class="cb" onclick="clearRows()">Clear</button>
        <button class="cb" onclick="exportCsv()" title="Export to CSV">&#8681; CSV</button>
        <button class="collapse-btn" onclick="togglePanel('wfr-panel','wfr-splitter')" title="Collapse/expand panel">&#9664;</button>
        <span class="row-info" id="row-sel-info"></span>
      </span>
    </div>
    <div class="wfr-tbl-wrap">
      <table class="ftbl">
        <thead id="filter-thead"></thead>
        <tbody id="filter-tbody"></tbody>
      </table>
    </div>
  </div>
</div>
<div class="h-splitter" id="wfr-splitter" title="Drag to resize sidebar" onmousedown="startSplit(event,'wfr-panel','tab-content','wfr-panel-w')"></div>
<div class="all-med-toggle" id="all-med-toggle" onclick="toggleSummPanel()" title="Parameter Table">
  <button class="amt-btn" id="all-med-toggle-btn">&#9654;</button>
</div>
<div id="all-med-panel">
  <div class="all-med-hdr">
    Parameter Table
    <span style="display:flex;align-items:center;gap:4px">
      <button class="collapse-btn" onclick="toggleSummPanel()" title="Close">&#9664;</button>
    </span>
  </div>
  <div class="all-med-body" id="sicc-summ-body">
    <div style="padding:0 0 5px;display:flex;gap:4px;align-items:center">
      <input id="param-tbl-search" type="text" placeholder="&#128269; Search parameters..." oninput="_ptFilter(this.value)"
        style="flex:1;font-size:11px;padding:4px 8px;border:1px solid #bdc3c7;border-radius:3px;background:#f8f9fa;color:#2c3e50">
      <button onclick="_ptExportCsv()" title="Download parameter table as CSV" style="font-size:12px;padding:3px 7px;border:1px solid #bbb;border-radius:3px;background:#fff;cursor:pointer;white-space:nowrap">&#11015; CSV</button>
    </div>
    <div style="overflow:auto;max-height:calc(100vh - 200px)">
      <table id="param-unified-tbl" style="border-collapse:collapse;font-size:11px;white-space:nowrap;width:100%">
        <thead id="param-tbl-head"></thead>
        <tbody id="param-tbl-body"></tbody>
      </table>
    </div>
  </div>
</div>
<div class="h-splitter" id="all-med-splitter" title="Drag to resize All Medians panel" onmousedown="startSplit(event,'all-med-panel',null,'all-med-w')"></div>
<div class="tab-content" id="tab-content">
'''
    )


def build_page_close() -> str:
    """Return the closing HTML after all tab panels (before script)."""
    return '''</div><!-- /tab-content -->
</div><!-- /main-layout -->
'''

# ════════════════════════════════════════════════════════════════
# Shared JavaScript + HTML helpers  (formerly _dash_js_shared.py)
# ════════════════════════════════════════════════════════════════

_SICC_ON_CHANGE = (
    'render_sicc();render_cdyn();render_summ();'
    'var _ap=document.querySelector(\'.tab-panel.active\');'
    'if(_ap&&_ap.id===\'tab-dist\')renderHist();'
    'if(typeof _ptRefreshModal!==\'undefined\')_ptRefreshModal();'
)
_SICC_FILTER_JS = _make_filter_js(
    on_change_calls=_SICC_ON_CHANGE,
    sel_var='SEL_WFR',
    toggle_fn='toggleRow',
)

# ── Shared state, utils, sidebar, and chart helpers ─────────────────────────
SHARED_JS = (
    _FILTER_DD_JS
    + 'var DATA={rows:ROWS,hasMaterial:true,'  # always show Material column in filter sidebar
    + 'hasDate:ROWS.some(function(r){return r.date&&r.date!==\'\';}),'                                   # noqa
    + 'hasUpmMed:ROWS.some(function(r){return r.upmMed!=null&&r.upmMed.length>0;})};\n'
    + _SICC_FILTER_JS
    + r'''
window.toggleRow=toggleRow;window.selectAllRows=selectAllRows;window.clearRows=clearRows;
window.selAll=selectAllRows;window.clrAll=clearRows;
window.ftDdOpen=ftDdOpen;window.sortFilter=sortFilter;window.rFilter=rFilter;
// exportCsv for the CSV button in the filter panel sidebar
function exportCsv(){
  var active=[];DATA.rows.forEach(function(r,i){if(SEL_WFR.has(i))active.push(i);});
  active.sort(function(a,b){return a-b;});
  var hdrs=['Program','Lot','Wafer'].concat(DATA.hasMaterial?['Material']:[])
    .concat(DATA.hasUpmMed?['UPM_Med']:[]).concat(DATA.hasDate?['DateTested']:[])
    .concat(['FF%','FFDF%','Total']);
  var lines=[hdrs.join(',')];
  active.forEach(function(i){
    var r=DATA.rows[i];var tot=r.total||0;
    var bc=r.binCounts||{};
    var ff=(bc['1']||0)+(bc['2']||0),ffdf=ff+(bc['3']||0)+(bc['4']||0);
    var row=[r.program||'',r.lot||'',r.wafer||''].concat(DATA.hasMaterial?[r.material||'']:[])
      .concat(DATA.hasUpmMed?(r.upmMed||[]).map(function(v){return v!=null?v:''}):[])
      .concat(DATA.hasDate?[r.date||'']:[])
      .concat([tot>0?(ff/tot*100).toFixed(1):0,tot>0?(ffdf/tot*100).toFixed(1):0,tot]);
    lines.push(row.map(function(v){var s=String(v);return s.indexOf(',')>=0?'"'+s+'"':s;}).join(','));
  });
  var blob=new Blob([lines.join('\r\n')],{type:'text/csv'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='filter_rows.csv';document.body.appendChild(a);a.click();
  setTimeout(function(){document.body.removeChild(a);URL.revokeObjectURL(a.href);},100);
}
window.exportCsv=exportCsv;
'''
    + r'''
var IS_CDYN=false;
var XY_COLOR_BY=['material'];
var _SCATTER_Y_LOG=true;
function _toggleScatterYLog(){
  _SCATTER_Y_LOG=!_SCATTER_Y_LOG;
  document.querySelectorAll('.scatter-ylog-btn').forEach(function(b){
    b.textContent=_SCATTER_Y_LOG?'Y: Log':'Y: Linear';
    b.style.background=_SCATTER_Y_LOG?'#2c3e50':'';
    b.style.color=_SCATTER_Y_LOG?'#fff':'';
  });
  render_sicc();render_cdyn();var ap=document.querySelector('.tab-panel.active');if(ap&&ap.id==='tab-dist')renderHist();
}
// Set initial button state to match default
document.addEventListener('DOMContentLoaded',function(){
  document.querySelectorAll('.scatter-ylog-btn').forEach(function(b){
    b.textContent='Y: Log';b.style.background='#2c3e50';b.style.color='#fff';
  });
});
window._toggleScatterYLog=_toggleScatterYLog;
var _scatterRanges={};
function _applyScatterRange(svgId){
  var g=function(sfx){var el=document.getElementById(svgId+sfx);return el&&el.value.trim()!==''?parseFloat(el.value):null;};
  _scatterRanges[svgId]={xMin:g('-xmin'),xMax:g('-xmax'),yMin:g('-ymin'),yMax:g('-ymax')};
  render_sicc();render_cdyn();var ap=document.querySelector('.tab-panel.active');if(ap&&ap.id==='tab-dist')renderHist();
}
function _resetScatterRange(svgId){
  _scatterRanges[svgId]={xMin:null,xMax:null,yMin:null,yMax:null};
  ['xmin','xmax','ymin','ymax'].forEach(function(s){var el=document.getElementById(svgId+'-'+s);if(el)el.value='';});
  render_sicc();render_cdyn();var ap=document.querySelector('.tab-panel.active');if(ap&&ap.id==='tab-dist')renderHist();
}
window._applyScatterRange=_applyScatterRange;window._resetScatterRange=_resetScatterRange;
var PARETO_GROUP=['lot','wafer'];
function _toggleParetoGroup(field){
  if(field==='none'){PARETO_GROUP=[];}
  else{var idx=PARETO_GROUP.indexOf(field);if(idx>=0)PARETO_GROUP.splice(idx,1);else PARETO_GROUP.push(field);}
  document.querySelectorAll('.pareto-gb').forEach(function(cb){
    if(cb.value==='none')cb.checked=PARETO_GROUP.length===0;
    else cb.checked=PARETO_GROUP.indexOf(cb.value)>=0;
  });
  var ap=document.querySelector('.tab-panel.active');if(ap&&ap.id==='tab-dist')renderHist();
}
window._toggleParetoGroup=_toggleParetoGroup;
function _toggleXYGroup(field){
  if(field==='none'){XY_COLOR_BY=[];}
  else{var idx=XY_COLOR_BY.indexOf(field);if(idx>=0)XY_COLOR_BY.splice(idx,1);else XY_COLOR_BY.push(field);}
  document.querySelectorAll('.xy-cb').forEach(function(cb){
    if(cb.value==='none')cb.checked=XY_COLOR_BY.length===0;
    else cb.checked=XY_COLOR_BY.indexOf(cb.value)>=0;
  });
  render_sicc();render_cdyn();var ap=document.querySelector('.tab-panel.active');if(ap&&ap.id==='tab-dist')renderHist();
}
window._toggleXYGroup=_toggleXYGroup;
var SEL_WFR=new Set();
var SICC_CAT_OFF=new Set();
var CDYN_CAT_OFF=new Set();
var SUMM_SICC_OFF=new Set();
var SUMM_CDYN_OFF=new Set();
function _getCats(cfg){var o=[],s=new Set();cfg.forEach(function(r){if(!s.has(r[0])){s.add(r[0]);o.push(r[0]);}});return o;}
function _buildCatLegend(cats,offSet,elId,renderFn){var el=document.getElementById(elId);if(!el)return;el.innerHTML=cats.map(function(cat){var off=offSet.has(cat);return '<span class="cat-tog'+(off?' off':'')+'" data-cat="'+esc(cat)+'" data-legend="'+esc(elId)+'"><span class="cat-swatch" style="background:'+_catColor(cat)+';border-color:'+_catBorder(cat)+'"></span>'+esc(cat)+'</span>';}).join('');el.onclick=function(e){var sp=e.target.closest('.cat-tog');if(!sp)return;_togCat(sp,sp.getAttribute('data-cat'),sp.getAttribute('data-legend'));}}
function _togCat(span,cat,legendId){
  var offSet;
  if(legendId==='sicc-tab-legend')offSet=SICC_CAT_OFF;
  else if(legendId==='cdyn-tab-legend')offSet=CDYN_CAT_OFF;
  else if(legendId==='sicc-cat-legend')offSet=SUMM_SICC_OFF;
  else offSet=SUMM_CDYN_OFF;
  if(offSet.has(cat))offSet.delete(cat);else offSet.add(cat);
  span.classList.toggle('off');
  if(legendId==='sicc-tab-legend')render_sicc();
  else if(legendId==='cdyn-tab-legend')render_cdyn();
  else render_summ();
}
window._togCat=_togCat;
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
/* Safe min/max: avoids Function.apply stack overflow on large arrays (>65k items) */
function _safeMin(a){var r=Infinity;for(var _i=0;_i<a.length;_i++){if(a[_i]<r)r=a[_i];}return r;}
function _safeMax(a){var r=-Infinity;for(var _i=0;_i<a.length;_i++){if(a[_i]>r)r=a[_i];}return r;}
function medArr(a){
  if(!a||!a.length)return null;
  var s=a.slice().sort(function(x,y){return x-y;});
  var m=Math.floor(s.length/2);
  return s.length%2?s[m]:(s[m-1]+s[m])/2;
}
/* Flatten die-level values from die_pairs across multiple wafer rows */
function flatVals(indices,field,isCdyn){
  var out=[];
  for(var k=0;k<indices.length;k++){
    var r=ROWS[indices[k]];if(!r)continue;
    var dp=r.die_pairs&&r.die_pairs[field];
    if(dp&&dp.s){for(var j=0;j<dp.s.length;j++){if(dp.s[j]!=null&&!isNaN(dp.s[j])&&dp.s[j]>0)out.push(dp.s[j]);}}
  }
  return out;
}
/* Get all die-level values for a column from die_pairs across multiple wafers */
function flatDieVals(indices,field){
  var out=[];
  for(var k=0;k<indices.length;k++){
    var r=ROWS[indices[k]];if(!r)continue;
    var dp=r.die_pairs&&r.die_pairs[field];
    if(dp&&dp.s){for(var j=0;j<dp.s.length;j++){if(dp.s[j]!=null&&!isNaN(dp.s[j]))out.push(dp.s[j]);}}
  }
  return out;
}
/* Get paired die-level x,y points for scatter (SICC/CDYN value vs UPM) */
function flatDiePairs(indices,field){
  var pts=[];
  for(var k=0;k<indices.length;k++){
    var r=ROWS[indices[k]];if(!r)continue;
    var dp=r.die_pairs&&r.die_pairs[field];
    if(dp&&dp.s&&dp.u){
      for(var j=0;j<dp.s.length;j++){
        if(dp.s[j]!=null&&!isNaN(dp.s[j])&&dp.u[j]!=null&&!isNaN(dp.u[j]))
          pts.push({s:dp.s[j],u:dp.u[j]});
      }
    }
  }
  return pts;
}
function filterOutliers(arr,nSigma){
  if(!arr||arr.length<3)return arr;
  var med=medArr(arr);
  var n=arr.length,sum=0;
  for(var i=0;i<n;i++){var d=arr[i]-med;sum+=d*d;}
  var sd=Math.sqrt(sum/n);
  if(sd===0)return arr;
  var lim=nSigma*sd;
  return arr.filter(function(v){return Math.abs(v-med)<=lim;});
}
function _isValidUpmPct(v){
  return v!=null&&!isNaN(v)&&v>=0&&v<=100;
}
function getFiltered(){
  return ROWS.map(function(_,i){return i;});
}
function getFieldVals(field){
  var seen=new Set(),out=[];
  ROWS.forEach(function(r){var v=r[field];if(!seen.has(v)){seen.add(v);out.push(v);}});
  out.sort(function(a,b){var na=parseFloat(a),nb=parseFloat(b);return(!isNaN(na)&&!isNaN(nb))?(na-nb):String(a).localeCompare(String(b));});
  return out;
}
function ccls(val,tgt,cdyn){
  if(tgt===undefined||tgt===null)return'';
  if(val>tgt)return'cell-r';
  if(cdyn?val>tgt*0.9:val>tgt*0.95)return'cell-y';
  return'cell-g';
}
// ratio cls: lower-is-better (SICC/CDYN). ratio>1 = over target
function ratioCls(r){
  if(r==null)return'';
  if(r>1.0)return'cell-r';
  if(r>=0.95)return'cell-y';
  return'cell-g';
}
// UPM cls: higher-is-better
function upmCls(v,tgt){
  if(v==null||tgt==null)return'';
  if(v>=tgt)return'cell-g';
  if(v>=tgt*0.95)return'cell-y';
  return'cell-r';
}
// CDYN Type derived from friendly name
function cdynType(col){
  var c=col.toLowerCase();
  if(c.indexOf('max')<0)return'Individual';
  if(c.indexOf('atom')>=0)return'ATOM Max(0-3)';
  if(c.indexOf('core')>=0)return'CORE Max(0-3)';
  return'Max';
}

// ── Tab registry (populated by each tab module via registerTab) ────────────
var _TAB_RENDERS = {};
var _TAB_LAZY    = {};
function registerTab(id, fn, lazy) {
  _TAB_RENDERS[id] = fn;
  if (lazy) _TAB_LAZY[id] = true;
}
function showTab(btn, id) {
  document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
  document.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); });
  btn.classList.add('active');
  document.getElementById(id).classList.add('active');
  if (_TAB_RENDERS[id]) _TAB_RENDERS[id]();
}
window.showTab = showTab;

// ── Category-colour palette ────────────────────────────────────────────
var CAT_COLORS={
  'CORE - SDS':'#d4e6f1','ATOM - SDS':'#d5f5e3','VCCIA - SDS':'#fdebd0',
  'VCCIO - SDS':'#fadbd8','VCCSRAM - SDS':'#e8daef','VNNAON - SDS':'#d6eaf8',
  'VCC1P8A - SDS':'#fcf3cf','CORE TOTAL - SDS':'#aed6f1','ATOM TOTAL - SDS':'#abebc6',
  'RING - SDS':'#f5cba7','FULLCHIP - SDS':'#f9e79f',
  'CORE - SDT':'#a9cce3','ATOM - SDT':'#a9dfbf','VCCIA - SDT':'#f5cba7',
  'VCCIO - SDT':'#f1948a','VCCSRAM - SDT':'#d2b4de','VNNAON - SDT':'#85c1e9',
  'VCC1P8A - SDT':'#f9e79f','CORE TOTAL - SDT':'#7fb3d8','ATOM TOTAL - SDT':'#82e0aa',
  'RING - SDT':'#eb984e','FULLCHIP - SDT':'#f4d03f'
};
var CAT_BORDER={
  'CORE - SDS':'#2980b9','ATOM - SDS':'#27ae60','VCCIA - SDS':'#e67e22',
  'VCCIO - SDS':'#e74c3c','VCCSRAM - SDS':'#8e44ad','VNNAON - SDS':'#3498db',
  'VCC1P8A - SDS':'#f1c40f','CORE TOTAL - SDS':'#2471a3','ATOM TOTAL - SDS':'#1e8449',
  'RING - SDS':'#ca6f1e','FULLCHIP - SDS':'#d4ac0d',
  'CORE - SDT':'#1f618d','ATOM - SDT':'#1d8348','VCCIA - SDT':'#ca6f1e',
  'VCCIO - SDT':'#c0392b','VCCSRAM - SDT':'#6c3483','VNNAON - SDT':'#2e86c1',
  'VCC1P8A - SDT':'#b7950b','CORE TOTAL - SDT':'#1a5276','ATOM TOTAL - SDT':'#196f3d',
  'RING - SDT':'#a04000','FULLCHIP - SDT':'#9a7d0a'
};
var _dynPal=[['#fce4ec','#c2185b'],['#fff3e0','#e65100'],['#e0f7fa','#00838f'],['#f3e5f5','#7b1fa2'],['#e8f5e9','#2e7d32'],['#fff8e1','#f9a825'],['#fbe9e7','#bf360c'],['#e1f5fe','#0277bd'],['#f9fbe7','#827717'],['#ede7f6','#4527a0']];
var _dynMap={},_dynI=0;
function _catColor(cat){if(CAT_COLORS[cat])return CAT_COLORS[cat];if(!_dynMap[cat]){var p=_dynPal[_dynI%_dynPal.length];_dynMap[cat]={bg:p[0],bd:p[1]};_dynI++;} return _dynMap[cat].bg;}
function _catBorder(cat){if(CAT_BORDER[cat])return CAT_BORDER[cat];if(!_dynMap[cat]){var p=_dynPal[_dynI%_dynPal.length];_dynMap[cat]={bg:p[0],bd:p[1]};_dynI++;} return _dynMap[cat].bd;}
// ── Filter-by-Lot/Wafer table (yield-dashboard style) ──────────────────────
var _tblFT={};
function _getUpmCol(col){
  // Mirror backend mapping behavior: last matching config row wins.
  // This prevents picking a stale/duplicate UPM mapping in Charts.
  var cfgs=[SICC_TBL_CFG,CDYN_TBL_CFG];
  var colLc=(col||'').toLowerCase();
  var hit=null;
  for(var c=0;c<cfgs.length;c++){
    var cfg=cfgs[c];
    if(!cfg||!cfg.length)continue;
    for(var i=0;i<cfg.length;i++){
      var t=(cfg[i][2]||'');
      var u=(cfg[i][3]||'');
      if(u&&t.toLowerCase()===colLc)hit=u;
    }
  }
  return hit;
}
// ── Mini UPM distribution chart (blown-up view of UPM for selected column) ──
function drawMiniUpm(active,primaryCol,isCdyn,svgId,titleId,noteId){
  var svg=document.getElementById(svgId);
  var titleEl=document.getElementById(titleId);
  var noteEl=document.getElementById(noteId);
  var panelId=svgId.replace('-svg','');
  var panel=document.getElementById(panelId+'-panel');
  if(!svg)return;
  var col=primaryCol;
  if(!col){svg.innerHTML='';if(panel)panel.style.display='none';return;}
  var uCol=_getUpmCol(col);
  if(!uCol){svg.innerHTML='';if(panel)panel.style.display='none';return;}
  if(panel)panel.style.display='';
  if(titleEl)titleEl.textContent=uCol+' (paired with '+col+')';
  // Collect all UPM die values from die_pairs
  var allU=[];
  active.forEach(function(i){
    var r=ROWS[i];
    var dp=r.die_pairs&&r.die_pairs[col];
    if(dp&&dp.u&&dp.u.length){
      for(var di=0;di<dp.u.length;di++){
        if(dp.s[di]>0) allU.push(dp.u[di]);
      }
    }
  });
  allU=filterOutliers(allU.filter(function(v){return v!=null&&!isNaN(v);}),5);
  if(!allU.length){svg.innerHTML='';if(noteEl)noteEl.textContent='No UPM data.';return;}
  // Build histogram
  var lo=_safeMin(allU),hi=_safeMax(allU);
  if(lo===hi){var d=Math.abs(lo*0.05)||0.5;lo-=d;hi+=d;}
  var nb=Math.max(6,Math.min(25,Math.round(Math.sqrt(allU.length))));
  var step=(hi-lo)/nb;
  var edges=[],counts=[];
  for(var bi=0;bi<=nb;bi++)edges.push(lo+bi*step);
  for(var bi=0;bi<nb;bi++)counts.push(0);
  allU.forEach(function(v){var idx=Math.min(nb-1,Math.floor((v-lo)/step));if(idx<0)idx=0;counts[idx]++;});
  var med=medArr(allU);
  // Draw compact SVG histogram
  var W=Math.max(svg.clientWidth||480,240),H=parseInt(svg.getAttribute('height'))||200;
  var pl=48,pr=12,pt=18,pb=38;
  var cW=W-pl-pr,cH=H-pt-pb;
  var maxC=_safeMax(counts)||1;
  var bw=cW/nb;
  var p=['<rect width="'+W+'" height="'+H+'" fill="#fffaf4"/>'];
  for(var i=0;i<nb;i++){
    var bh=(counts[i]/maxC)*cH;
    var bx=pl+i*bw,by=pt+cH-bh;
    p.push('<rect x="'+bx.toFixed(1)+'" y="'+by.toFixed(1)+'" width="'+(bw*0.85).toFixed(1)+'" height="'+Math.max(1,bh).toFixed(1)+'" fill="#e67e22" opacity="0.75"/>');
    if(counts[i]>0)p.push('<text x="'+(bx+bw*0.425).toFixed(1)+'" y="'+(by-2).toFixed(1)+'" text-anchor="middle" font-size="12" fill="#c0650a">'+counts[i]+'</text>');
  }
  // Median line — label as axis tick below X-axis
  if(med!=null&&hi>lo){
    var mx=pl+(med-lo)/(hi-lo)*cW;
    if(mx>=pl-2&&mx<=pl+cW+2){
      p.push('<line x1="'+mx.toFixed(1)+'" x2="'+mx.toFixed(1)+'" y1="'+pt+'" y2="'+(pt+cH)+'" stroke="#d35400" stroke-width="2" stroke-dasharray="4,3"/>');
      p.push('<line x1="'+mx.toFixed(1)+'" x2="'+mx.toFixed(1)+'" y1="'+(pt+cH)+'" y2="'+(pt+cH+6)+'" stroke="#d35400" stroke-width="2"/>');
      p.push('<text x="'+mx.toFixed(1)+'" y="'+(H-2)+'" text-anchor="middle" font-size="11" fill="#d35400" font-weight="bold">Med:'+med.toFixed(2)+'%</text>');
    }
  }
  // Y-axis (count)
  var yStep=Math.ceil(maxC/3);if(yStep<1)yStep=1;
  for(var yt=0;yt<=maxC;yt+=yStep){
    var ty=pt+cH-(yt/maxC)*cH;
    p.push('<line x1="'+(pl-3)+'" x2="'+pl+'" y1="'+ty.toFixed(1)+'" y2="'+ty.toFixed(1)+'" stroke="#c0650a" opacity="0.5"/>');
    p.push('<text x="'+(pl-5)+'" y="'+(ty+3).toFixed(1)+'" text-anchor="end" font-size="15" fill="#c0650a">'+yt+'</text>');
  }
  p.push('<line x1="'+pl+'" x2="'+(pl+cW)+'" y1="'+(pt+cH)+'" y2="'+(pt+cH)+'" stroke="#c0650a" opacity="0.5"/>');
  p.push('<line x1="'+pl+'" x2="'+pl+'" y1="'+pt+'" y2="'+(pt+cH)+'" stroke="#c0650a" opacity="0.5"/>');
  // X-axis ticks
  var xRange=hi-lo;
  if(xRange>0){
    var rawStep=xRange/6;
    var mag=Math.pow(10,Math.floor(Math.log10(rawStep)));
    var norm=rawStep/mag;
    var niceStep;
    if(norm<=1.5)niceStep=1*mag;else if(norm<=3.5)niceStep=2*mag;else if(norm<=7.5)niceStep=5*mag;else niceStep=10*mag;
    var xStart=Math.ceil(lo/niceStep)*niceStep;
    var xDec=Math.max(0,Math.ceil(-Math.log10(niceStep))+1);if(xDec>6)xDec=6;
    for(var xv=xStart;xv<=hi+niceStep*0.001;xv+=niceStep){
      var xx=pl+(xv-lo)/xRange*cW;
      if(xx>=pl-1&&xx<=pl+cW+1){
        p.push('<line x1="'+xx.toFixed(1)+'" x2="'+xx.toFixed(1)+'" y1="'+(pt+cH)+'" y2="'+(pt+cH+3)+'" stroke="#c0650a" opacity="0.5"/>');
        p.push('<text x="'+xx.toFixed(1)+'" y="'+(pt+cH+18)+'" text-anchor="middle" font-size="14" fill="#c0650a">'+xv.toFixed(xDec)+'%</text>');
      }
    }
  }
  // X-axis label
  p.push('<text x="'+(pl+cW/2).toFixed(1)+'" y="'+(H-2)+'" text-anchor="middle" font-size="15" fill="#c0650a" font-weight="bold">UPM (%)</text>');
  svg.setAttribute('viewBox','0 0 '+W+' '+H);
  svg.innerHTML=p.join('');
  if(noteEl)noteEl.textContent=allU.length+' die(s), median='+med.toFixed(2)+'%, range=['+lo.toFixed(2)+', '+hi.toFixed(2)+']';
  /* Stats table */
  var statsTblId=svgId.replace('-svg','-stats');
  var stEl=document.getElementById(statsTblId);
  if(stEl&&med!=null){
    var _sum=allU.reduce(function(a,b){return a+b;},0);var _mean=_sum/allU.length;
    var _sq=allU.reduce(function(a,v){return a+(v-_mean)*(v-_mean);},0);var _sd=Math.sqrt(_sq/allU.length);
    stEl.innerHTML='<table style="border-collapse:collapse;font-size:11px;margin-top:2px">'
      +'<thead><tr><th style="padding:2px 8px;background:#e67e22;color:#fff;text-align:left">Stat</th><th style="padding:2px 8px;background:#e67e22;color:#fff">Value</th></tr></thead>'
      +'<tbody>'
      +'<tr><td style="padding:2px 8px;border-bottom:1px solid #eee">Count (dies)</td><td style="padding:2px 8px;border-bottom:1px solid #eee;text-align:right">'+allU.length+'</td></tr>'
      +'<tr><td style="padding:2px 8px;border-bottom:1px solid #eee">Min</td><td style="padding:2px 8px;border-bottom:1px solid #eee;text-align:right">'+lo.toFixed(2)+'%</td></tr>'
      +'<tr><td style="padding:2px 8px;border-bottom:1px solid #eee;font-weight:bold;color:#d35400">Median</td><td style="padding:2px 8px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;color:#d35400">'+med.toFixed(2)+'%</td></tr>'
      +'<tr><td style="padding:2px 8px;border-bottom:1px solid #eee">Max</td><td style="padding:2px 8px;border-bottom:1px solid #eee;text-align:right">'+hi.toFixed(2)+'%</td></tr>'
      +'<tr><td style="padding:2px 8px">Std Dev</td><td style="padding:2px 8px;text-align:right">'+_sd.toFixed(2)+'%</td></tr>'
      +'</tbody></table>';
  }
}
function _buildUpmOverlay(active,primaryCol,isCdyn){
  var col=primaryCol||SEL_COL;
  if(!col)return null;
  var uCol=_getUpmCol(col);
  if(!uCol)return null;
  var pts=[];
  var allU=[];
  active.forEach(function(i){
    var r=ROWS[i];
    var dp=r.die_pairs&&r.die_pairs[col];
    if(dp&&dp.s&&dp.u&&dp.s.length){
      for(var di=0;di<dp.s.length;di++){
        if(dp.s[di]>0)pts.push({s:dp.s[di],u:dp.u[di]});
      }
      allU=allU.concat(dp.u);
    }
    // No fallback — only use actual die-level UPM data
  });
  if(!pts.length)return null;
  return {pts:pts,colName:uCol,uMed:medArr(allU)};
}
// Build SICC/CDYN overlay for UPM/CDYN dist panels (reverse of UPM overlay)
function _buildSiccCdynOverlay(active,isCdyn){
  if(!SEL_COL)return null;
  var sVals=[];
  active.forEach(function(i){
    var r=ROWS[i];
    var dp=r.die_pairs&&r.die_pairs[SEL_COL];
    if(dp&&dp.s&&dp.s.length){
      for(var di=0;di<dp.s.length;di++){if(dp.s[di]!=null&&!isNaN(dp.s[di]))sVals.push(dp.s[di]);}
    }
  });
  if(!sVals.length)return null;
  var lo=_safeMin(sVals),hi=_safeMax(sVals);
  if(lo===hi){var d=Math.abs(lo*0.05)||0.01;lo-=d;hi+=d;}
  var nb=Math.max(4,Math.min(25,Math.round(Math.sqrt(sVals.length))));
  var step=(hi-lo)/nb;
  var edges=[],counts=[];
  for(var i=0;i<=nb;i++)edges.push(lo+i*step);
  for(var i=0;i<nb;i++)counts.push(0);
  sVals.forEach(function(v){
    var idx=Math.min(nb-1,Math.floor((v-lo)/step));
    if(idx<0)idx=0;
    counts[idx]++;
  });
  return {edges:edges,counts:counts,med:medArr(sVals),colName:SEL_COL};
}
function computeStats(vals){if(!vals||!vals.length)return null;var s=vals.slice().sort(function(a,b){return a-b;});var n=s.length;var sum=s.reduce(function(a,b){return a+b;},0);var mean=sum/n;var med=n%2?s[Math.floor(n/2)]:(s[n/2-1]+s[n/2])/2;var vari=s.reduce(function(ac,v){var d=v-mean;return ac+d*d;},0)/n;return{min:s[0],max:s[n-1],median:med,mean:mean,stddev:Math.sqrt(vari),count:n};}
function renderStatsTable(stats,containerId,dec){
  var el=document.getElementById(containerId);if(!el)return;
  if(!stats){el.innerHTML='';return;}
  var d=dec||4;var fv=function(v){return v!=null?v.toFixed(d):'--';};
  var _th='padding:6px 14px;font-size:11px;font-weight:600;text-align:center;background:#2c3e50;color:#ecf0f1;letter-spacing:0.04em;white-space:nowrap;border-right:1px solid #3d5166';
  var _thHL='padding:6px 14px;font-size:11px;font-weight:700;text-align:center;background:#1a4a7a;color:#fff;letter-spacing:0.04em;white-space:nowrap;border-right:1px solid #3d5166';
  var _td='padding:6px 14px;font-size:12px;text-align:center;white-space:nowrap;border-right:1px solid #e8e8e8;color:#2c3e50';
  var _tdHL='padding:6px 14px;font-size:13px;font-weight:700;text-align:center;white-space:nowrap;color:#1a4a7a;border-right:1px solid #c8ddf5;background:#eef6ff';
  el.innerHTML=
    '<table style="border-collapse:collapse;width:100%;margin-top:10px;border-radius:6px;overflow:hidden;box-shadow:0 1px 5px rgba(0,0,0,.12)">'
    +'<thead><tr>'
    +'<th style="'+_th+'">N (dies)</th>'
    +'<th style="'+_th+'">Min</th>'
    +'<th style="'+_thHL+'">Median</th>'
    +'<th style="'+_th+'">Mean</th>'
    +'<th style="'+_th+'">Max</th>'
    +'<th style="'+_th+';border-right:none">Std Dev</th>'
    +'</tr></thead>'
    +'<tbody><tr style="background:#fff">'
    +'<td style="'+_td+'">'+stats.count.toLocaleString()+'</td>'
    +'<td style="'+_td+'">'+fv(stats.min)+'</td>'
    +'<td style="'+_tdHL+'">'+fv(stats.median)+'</td>'
    +'<td style="'+_td+'">'+fv(stats.mean)+'</td>'
    +'<td style="'+_td+'">'+fv(stats.max)+'</td>'
    +'<td style="'+_td+';border-right:none">'+fv(stats.stddev)+'</td>'
    +'</tr></tbody></table>';
}
function drawSVG(edges,counts,medVal,tgt,ylabel,svgId,showCounts,overlay,barLabel){
  var svg=document.getElementById(svgId||'hist-svg');
  if(!svg)return;
  var ov=overlay&&overlay.pts&&overlay.pts.length?overlay:null;
  var W=Math.max(svg.clientWidth||500,260),H=parseInt(svg.getAttribute('height'))||340;
  var pl=58,pr=ov?98:20,pt=32,pb=56;
  var cW=W-pl-pr,cH=H-pt-pb;
  var n=counts.length;
  var p=['<rect width="'+W+'" height="'+H+'" fill="#f8f9fa"/>'];
  if(!n){svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.innerHTML=p.join('');return;}
  var lo=edges[0],hi=edges[edges.length-1];
  var maxC=_safeMax(counts)||1;
  var bw=cW/n;
  // Primary bars (SICC/CDYN - blue)
  for(var i=0;i<n;i++){
    var bh=(counts[i]/maxC)*cH;
    var bx=pl+i*bw,by=pt+cH-bh;
    p.push('<rect x="'+bx.toFixed(1)+'" y="'+by.toFixed(1)+'" width="'+(bw*0.85).toFixed(1)+'" height="'+Math.max(1,bh).toFixed(1)+'" fill="#3498db" opacity="0.82"/>');
    if(showCounts!==false&&counts[i]>0)p.push('<text x="'+(bx+bw*0.425).toFixed(1)+'" y="'+(by-3).toFixed(1)+'" text-anchor="middle" font-size="6" fill="#555">'+counts[i]+'</text>');
  }
  // UPM overlay: median UPM per histogram bin (right Y-axis)
  if(ov){
    // Bin wafer points into same histogram edges, compute median UPM per bin
    var binU=[];
    for(var bi=0;bi<n;bi++)binU.push([]);
    var binSpan=(hi-lo)/n;
    ov.pts.forEach(function(pt){
      // Clamp to histogram range so extreme values still land in first/last bin
      var sv=pt.s;
      if(sv<lo)sv=lo;
      if(sv>hi)sv=hi-1e-12;
      var idx=Math.min(n-1,Math.floor((sv-lo)/binSpan));
      if(idx<0)idx=0;
      binU[idx].push(pt.u);
    });
    var binMeds=binU.map(function(arr){return medArr(arr);});
    var validMeds=binMeds.filter(function(v){return v!=null;});
    if(validMeds.length){
      // Dynamic UPM range so markers spread across chart height
      var uMin=_safeMin(validMeds),uMax=_safeMax(validMeds);
      var uPad=(uMax-uMin)*0.1||1;uMin-=uPad;uMax+=uPad;
      var uRange=uMax-uMin;if(uRange===0)uRange=1;
      // Draw one dot per bin at bin center
      for(var bi=0;bi<n;bi++){
        if(binMeds[bi]!=null){
          var cx=pl+(bi+0.5)*bw;
          var cy=pt+cH-((binMeds[bi]-uMin)/uRange)*cH;
          var nw=binU[bi].length;
          p.push('<circle cx="'+cx.toFixed(1)+'" cy="'+cy.toFixed(1)+'" r="5" fill="#e67e22" stroke="#fff" stroke-width="1" opacity="0.85"><title>Bin '+bi+': '+nw+' die(s)\\nUPM Med: '+binMeds[bi].toFixed(2)+'%</title></circle>');
        }
      }
      // Overall UPM median horizontal dashed line — label on right Y-axis only
      if(ov.uMed!=null){
        var umy=pt+cH-((ov.uMed-uMin)/uRange)*cH;
        p.push('<line x1="'+pl+'" x2="'+(pl+cW)+'" y1="'+umy.toFixed(1)+'" y2="'+umy.toFixed(1)+'" stroke="#d35400" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.6"/>');
        p.push('<text x="'+(pl+cW+6)+'" y="'+(umy+4).toFixed(1)+'" text-anchor="start" font-size="11" fill="#d35400" font-weight="bold">'+ov.uMed.toFixed(1)+'%</text>');
      }
      // Right Y-axis for UPM median
      var uTicks=5;
      var uStep=(uMax-uMin)/(uTicks-1);
      for(var ti=0;ti<uTicks;ti++){
        var tv=uMin+ti*uStep;
        var ty=pt+cH-((tv-uMin)/uRange)*cH;
        p.push('<line x1="'+(pl+cW)+'" x2="'+(pl+cW+4)+'" y1="'+ty.toFixed(1)+'" y2="'+ty.toFixed(1)+'" stroke="#e67e22"/>');
        p.push('<text x="'+(pl+cW+6)+'" y="'+(ty+4).toFixed(1)+'" text-anchor="start" font-size="17" fill="#c0650a">'+tv.toFixed(1)+'%</text>');
      }
      p.push('<line x1="'+(pl+cW)+'" x2="'+(pl+cW)+'" y1="'+pt+'" y2="'+(pt+cH)+'" stroke="#e67e22" opacity="0.5"/>');
      p.push('<text x="'+(pl+cW+82)+'" y="'+(pt+cH/2).toFixed(1)+'" text-anchor="middle" font-size="17" fill="#c0650a" font-weight="bold" transform="rotate(-90,'+(pl+cW+82)+','+(pt+cH/2)+')">'+(ov.colName||'UPM Median')+'</text>');
    }
    // Legend
    var _bl=barLabel||(IS_CDYN?'CDYN':'SICC');
    p.push('<rect x="'+(pl+4)+'" y="'+(pt-6)+'" width="10" height="10" fill="#3498db" opacity="0.82"/>');
    p.push('<text x="'+(pl+17)+'" y="'+(pt+3)+'" font-size="15" fill="#555">'+_bl+' (count)</text>');
    p.push('<circle cx="'+(pl+110)+'" cy="'+(pt-1)+'" r="4" fill="#e67e22" opacity="0.85"/>');
    p.push('<text x="'+(pl+117)+'" y="'+(pt+3)+'" font-size="15" fill="#e67e22">UPM Med (%)</text>');
  }
  if(tgt!==undefined&&tgt!==null&&hi>lo){
    var tx=pl+(tgt-lo)/(hi-lo)*cW;
    if(tx>=pl-2&&tx<=pl+cW+2){
      p.push('<line x1="'+tx.toFixed(1)+'" x2="'+tx.toFixed(1)+'" y1="'+pt+'" y2="'+(pt+cH)+'" stroke="#27ae60" stroke-width="2" stroke-dasharray="4,3"/>');
      p.push('<text x="'+(tx+4).toFixed(1)+'" y="'+(pt+20)+'" font-size="15" fill="#27ae60">Tgt:'+Number(tgt).toFixed(2)+'</text>');
    }
  }
  if(medVal!=null&&hi>lo){
    var mx=pl+(medVal-lo)/(hi-lo)*cW;
    if(mx>=pl-2&&mx<=pl+cW+2){
      p.push('<line x1="'+mx.toFixed(1)+'" x2="'+mx.toFixed(1)+'" y1="'+pt+'" y2="'+(pt+cH)+'" stroke="#8B4513" stroke-width="2.5" stroke-dasharray="5,3"/>');
      /* Median label as a tick below X-axis */
      p.push('<line x1="'+mx.toFixed(1)+'" x2="'+mx.toFixed(1)+'" y1="'+(pt+cH)+'" y2="'+(pt+cH+8)+'" stroke="#8B4513" stroke-width="2"/>');
      p.push('<text x="'+mx.toFixed(1)+'" y="'+(H-4)+'" text-anchor="middle" font-size="13" fill="#8B4513" font-weight="bold">Med:'+medVal.toFixed(2)+'</text>');
    }
  }
  var yStep=Math.ceil(maxC/4);if(yStep<1)yStep=1;
  for(var yt=0;yt<=maxC;yt+=yStep){
    var ty=pt+cH-(yt/maxC)*cH;
    p.push('<line x1="'+(pl-4)+'" x2="'+pl+'" y1="'+ty.toFixed(1)+'" y2="'+ty.toFixed(1)+'" stroke="#aaa"/>');
    p.push('<text x="'+(pl-6)+'" y="'+(ty+4).toFixed(1)+'" text-anchor="end" font-size="17" fill="#444">'+yt+'</text>');
  }
  p.push('<line x1="'+pl+'" x2="'+(pl+cW)+'" y1="'+(pt+cH)+'" y2="'+(pt+cH)+'" stroke="#aaa"/>');
  p.push('<line x1="'+pl+'" x2="'+pl+'" y1="'+pt+'" y2="'+(pt+cH)+'" stroke="#aaa"/>');
  // X-axis ticks with better resolution
  var xRange=hi-lo;
  if(xRange>0){
    // Choose ~6-10 nice ticks across range
    var rawStep=xRange/8;
    var mag=Math.pow(10,Math.floor(Math.log10(rawStep)));
    var norm=rawStep/mag;
    var niceStep;
    if(norm<=1.5)niceStep=1*mag;
    else if(norm<=3.5)niceStep=2*mag;
    else if(norm<=7.5)niceStep=5*mag;
    else niceStep=10*mag;
    var xStart=Math.ceil(lo/niceStep)*niceStep;
    var xDec=Math.max(0,Math.ceil(-Math.log10(niceStep))+1);
    if(xDec>8)xDec=8;
    for(var xv=xStart;xv<=hi+niceStep*0.001;xv+=niceStep){
      var xx=pl+(xv-lo)/xRange*cW;
      if(xx>=pl-1&&xx<=pl+cW+1){
        p.push('<line x1="'+xx.toFixed(1)+'" x2="'+xx.toFixed(1)+'" y1="'+(pt+cH)+'" y2="'+(pt+cH+5)+'" stroke="#aaa"/>');
        p.push('<text x="'+xx.toFixed(1)+'" y="'+(H-18)+'" text-anchor="middle" font-size="17" fill="#444">'+xv.toFixed(xDec)+'</text>');
      }
    }
  }else{
    p.push('<text x="'+(pl+cW/2).toFixed(1)+'" y="'+(H-18)+'" text-anchor="middle" font-size="17" fill="#444">'+lo.toFixed(2)+'</text>');
  }
  p.push('<text x="'+(pl+cW/2).toFixed(1)+'" y="'+(pt-10)+'" text-anchor="middle" font-size="17" fill="#333" font-weight="bold">'+esc(ylabel)+'</text>');
  svg.setAttribute('viewBox','0 0 '+W+' '+H);
  svg.innerHTML=p.join('');
}
// Nice tick helper for scatter axes
function _fmtSci(v){
  if(v===0)return '0';
  var e=Math.floor(Math.log10(Math.abs(v)));
  var m=v/Math.pow(10,e);
  var mStr=Math.abs(m-Math.round(m))<0.001?Math.round(m).toString():m.toFixed(1);
  var _sup=['\u2070','\u00b9','\u00b2','\u00b3','\u2074','\u2075','\u2076','\u2077','\u2078','\u2079'];
  var eAbs=Math.abs(e);var eSign=e<0?'\u207b':'';var eStr=String(eAbs).split('').map(function(d){return _sup[+d]||d;}).join('');
  return e===0?mStr:(e===1?(mStr==='1'?'10':mStr+'\u00d710'):mStr+'\u00d710'+eSign+eStr);
}
function _niceTicks(lo,hi,target){
  var range=hi-lo;if(range<=0)return [lo];
  var rawStep=range/target;
  var mag=Math.pow(10,Math.floor(Math.log10(rawStep)));
  var norm=rawStep/mag;
  var step;
  if(norm<=1.5)step=1*mag;else if(norm<=3.5)step=2*mag;else if(norm<=7.5)step=5*mag;else step=10*mag;
  var ticks=[];
  var start=Math.ceil(lo/step)*step;
  for(var v=start;v<=hi+step*0.001;v+=step)ticks.push(v);
  return ticks;
}
// ── Draggable cursor lines for XY scatter plots ─────────────────────────
// Creates vertical (X) and horizontal (Y) draggable crosshair lines
// over an SVG scatter plot, initialized at the median values.
// Fixed median values are shown as a badge in the top-right corner.
function _initDragCursors(svg,xMed,yMed,xLo,xHi,yLo,yHi,pl,pt,cW,cH,xLabel,yLabel,fmtX,fmtY){
  var NS='http://www.w3.org/2000/svg';
  var curX=xMed!=null?xMed:(xLo+xHi)/2;
  var curY=yMed!=null?yMed:(yLo+yHi)/2;
  var xRange=xHi-xLo,yRange=yHi-yLo;
  if(xRange===0)xRange=1;if(yRange===0)yRange=1;
  function val2px_x(v){return pl+((v-xLo)/xRange)*cW;}
  function val2py_y(v){return pt+cH-((v-yLo)/yRange)*cH;}
  function px2val_x(px){return xLo+((px-pl)/cW)*xRange;}
  function py2val_y(py){return yLo+((pt+cH-py)/cH)*yRange;}
  function clamp(v,lo,hi){return v<lo?lo:v>hi?hi:v;}
  // Vertical cursor line (X axis)
  var vLine=document.createElementNS(NS,'line');
  vLine.setAttribute('x1',val2px_x(curX));vLine.setAttribute('x2',val2px_x(curX));
  vLine.setAttribute('y1',pt);vLine.setAttribute('y2',pt+cH);
  vLine.setAttribute('stroke','#d35400');vLine.setAttribute('stroke-width','1.8');
  vLine.setAttribute('stroke-dasharray','6,3');vLine.setAttribute('opacity','0.85');
  vLine.style.pointerEvents='none';
  // Horizontal cursor line (Y axis)
  var hLine=document.createElementNS(NS,'line');
  hLine.setAttribute('x1',pl);hLine.setAttribute('x2',pl+cW);
  hLine.setAttribute('y1',val2py_y(curY));hLine.setAttribute('y2',val2py_y(curY));
  hLine.setAttribute('stroke','#8B4513');hLine.setAttribute('stroke-width','1.8');
  hLine.setAttribute('stroke-dasharray','6,3');hLine.setAttribute('opacity','0.85');
  hLine.style.pointerEvents='none';
  // Single full-chart drag handle — always moves both cursors together
  var xyHandle=document.createElementNS(NS,'rect');
  xyHandle.setAttribute('x',pl);xyHandle.setAttribute('y',pt);
  xyHandle.setAttribute('width',cW);xyHandle.setAttribute('height',cH);
  xyHandle.setAttribute('fill','transparent');xyHandle.style.cursor='crosshair';
  // Value readout labels (show current cursor values, updated on drag)
  var vLabel=document.createElementNS(NS,'text');
  vLabel.setAttribute('font-size','18');vLabel.setAttribute('fill','#d35400');vLabel.setAttribute('font-weight','bold');
  vLabel.style.pointerEvents='none';
  function _updateVLabel(){
    var px=parseFloat(vLine.getAttribute('x1'));
    vLabel.setAttribute('x',px+3);vLabel.setAttribute('y',pt+11);
    vLabel.textContent='X: '+(fmtX?fmtX(curX):curX.toFixed(2));
  }
  var hLabel=document.createElementNS(NS,'text');
  hLabel.setAttribute('font-size','18');hLabel.setAttribute('fill','#8B4513');hLabel.setAttribute('font-weight','bold');
  hLabel.style.pointerEvents='none';
  function _updateHLabel(){
    var py=parseFloat(hLine.getAttribute('y1'));
    hLabel.setAttribute('x',pl+4);hLabel.setAttribute('y',py-3);
    hLabel.textContent='Y: '+(fmtY?fmtY(curY):curY.toFixed(2));
  }
  _updateVLabel();_updateHLabel();
  // Append: lines, labels, then drag handle on top
  svg.appendChild(vLine);svg.appendChild(hLine);
  svg.appendChild(vLabel);svg.appendChild(hLabel);
  svg.appendChild(xyHandle);
  // Drag logic — always move both cursors together
  var dragging=false;
  function _moveBoth(sp){
    var px=clamp(sp.x,pl,pl+cW);
    var py=clamp(sp.y,pt,pt+cH);
    curX=px2val_x(px);curY=py2val_y(py);
    vLine.setAttribute('x1',px);vLine.setAttribute('x2',px);
    hLine.setAttribute('y1',py);hLine.setAttribute('y2',py);
    _updateVLabel();_updateHLabel();
  }
  function getSvgPt(e){
    var rect=svg.getBoundingClientRect();
    var vb=svg.viewBox.baseVal;
    var sx=vb.width/rect.width,sy=vb.height/rect.height;
    return{x:(e.clientX-rect.left)*sx,y:(e.clientY-rect.top)*sy};
  }
  function onMove(e){
    if(!dragging)return;
    _moveBoth(getSvgPt(e));
  }
  function onUp(){dragging=false;document.removeEventListener('mousemove',onMove);document.removeEventListener('mouseup',onUp);}
  xyHandle.addEventListener('mousedown',function(e){
    e.preventDefault();e.stopPropagation();dragging=true;
    _moveBoth(getSvgPt(e));
    document.addEventListener('mousemove',onMove);document.addEventListener('mouseup',onUp);
  });
  // Touch support
  function onTouchMove(e){if(!dragging||!e.touches.length)return;e.preventDefault();var t=e.touches[0];onMove({clientX:t.clientX,clientY:t.clientY});}
  function onTouchEnd(){dragging=false;svg.removeEventListener('touchmove',onTouchMove);svg.removeEventListener('touchend',onTouchEnd);}
  xyHandle.addEventListener('touchstart',function(e){
    e.preventDefault();dragging=true;
    if(e.touches.length){var t=e.touches[0];_moveBoth(getSvgPt({clientX:t.clientX,clientY:t.clientY}));}
    svg.addEventListener('touchmove',onTouchMove,{passive:false});svg.addEventListener('touchend',onTouchEnd);
  },{passive:false});
}
// ── Shared scatter palette + linear regression ─────────────────────────
var _MPAL=['#3498db','#e74c3c','#2ecc71','#9b59b6','#e67e22','#1abc9c','#e91e63','#00bcd4','#8bc34a','#795548','#f39c12','#607d8b'];
function _linReg(arr){
  var n=arr.length;if(n<3)return null;
  var sx=0,sy=0,sxy=0,sxx=0;
  for(var i=0;i<n;i++){sx+=arr[i].x;sy+=arr[i].y;sxy+=arr[i].x*arr[i].y;sxx+=arr[i].x*arr[i].x;}
  var denom=n*sxx-sx*sx;
  if(Math.abs(denom)<1e-12)return null;
  var slope=(n*sxy-sx*sy)/denom;
  var intercept=(sy-slope*sx)/n;
  var yMean=sy/n,ssTot=0,ssRes=0;
  for(var i=0;i<n;i++){var pred=slope*arr[i].x+intercept;ssRes+=(arr[i].y-pred)*(arr[i].y-pred);ssTot+=(arr[i].y-yMean)*(arr[i].y-yMean);}
  var r2=ssTot>0?1-ssRes/ssTot:0;
  return {slope:slope,intercept:intercept,r2:r2};
}
// Theil-Sen estimator: median of pairwise slopes, intercept through (medX,medY)
function _theilSen(arr){
  var n=arr.length;if(n<3)return null;
  // Cap sample to avoid O(n²) slowdown on large datasets
  var sample=arr;
  if(n>300){sample=arr.slice();for(var si=sample.length-1;si>0;si--){var ri=Math.floor(Math.random()*(si+1));var tmp=sample[si];sample[si]=sample[ri];sample[ri]=tmp;}sample=sample.slice(0,300);}
  var slopes=[];
  var n=sample.length;
  for(var i=0;i<n;i++)for(var j=i+1;j<n;j++){var dx=sample[j].x-sample[i].x;if(Math.abs(dx)>1e-12)slopes.push((sample[j].y-sample[i].y)/dx);}
  if(!slopes.length)return null;
  slopes.sort(function(a,b){return a-b;});
  var m2=slopes.length,slope=m2%2?slopes[(m2-1)/2]:(slopes[m2/2-1]+slopes[m2/2])/2;
  var xs=arr.map(function(p){return p.x;}).sort(function(a,b){return a-b;});
  var ys=arr.map(function(p){return p.y;}).sort(function(a,b){return a-b;});
  var medX=xs.length%2?xs[(xs.length-1)/2]:(xs[xs.length/2-1]+xs[xs.length/2])/2;
  var medY=ys.length%2?ys[(ys.length-1)/2]:(ys[ys.length/2-1]+ys[ys.length/2])/2;
  var intercept=medY-slope*medX;
  // Pseudo-R² vs OLS for display
  var yMean=arr.reduce(function(s,p){return s+p.y;},0)/n,ssTot=0,ssRes=0;
  for(var i=0;i<n;i++){var pred=slope*arr[i].x+intercept;ssRes+=(arr[i].y-pred)*(arr[i].y-pred);ssTot+=(arr[i].y-yMean)*(arr[i].y-yMean);}
  var r2=ssTot>0?1-ssRes/ssTot:0;
  return {slope:slope,intercept:intercept,r2:r2};
}
var _SCATTER_THEIL_SEN=false;
function _toggleTheilSen(cb){
  _SCATTER_THEIL_SEN=cb.checked;
  render_sicc();render_cdyn();var ap=document.querySelector('.tab-panel.active');if(ap&&ap.id==='tab-dist')renderHist();
}
window._toggleTheilSen=_toggleTheilSen;
function drawTabScatter(active,col,svgId,titleId,noteId){
  function _fmtV(v){return v.toFixed(3);}
  var svg=document.getElementById(svgId);
  var titleEl=document.getElementById(titleId);
  var noteEl=document.getElementById(noteId);
  if(!svg)return;
  if(!col||!active||!active.length){svg.innerHTML='';if(titleEl)titleEl.textContent='XY Scatter';if(noteEl)noteEl.textContent='';return;}
  var uCol=_getUpmCol(col);
  if(!uCol){svg.innerHTML='';if(titleEl)titleEl.textContent='No UPM mapping for '+col;if(noteEl)noteEl.textContent='';return;}
  if(titleEl)titleEl.textContent=col+' vs '+uCol;
  var pts=[];
  active.forEach(function(i){
    var r=ROWS[i];
    var parts=[];
    if(XY_COLOR_BY.indexOf('program')>=0)parts.push(r.program||'');
    if(XY_COLOR_BY.indexOf('lot')>=0)parts.push(r.lot||'');
    if(XY_COLOR_BY.indexOf('wafer')>=0)parts.push(r.wafer||'');
    if(XY_COLOR_BY.indexOf('material')>=0)parts.push(r.material||'');
    var grp=parts.length?parts.join(' | '):'';
    var dp=r.die_pairs&&r.die_pairs[col];
    if(dp&&dp.s&&dp.u&&dp.s.length){
      // Die-level: plot each die individually
      for(var di=0;di<dp.s.length;di++){
        if(dp.s[di]>0)pts.push({x:dp.u[di],y:dp.s[di],m:grp});
      }
    }
  });
  if(!pts.length){svg.innerHTML='';if(noteEl)noteEl.textContent='No paired die data.';return;}
  var xArr=pts.map(function(p){return p.x;});
  var yArr=pts.map(function(p){return p.y;});
  var xFilt=filterOutliers(xArr,5);
  var yFilt=filterOutliers(yArr,5);
  var xMin2=_safeMin(xFilt),xMax2=_safeMax(xFilt);
  var yMin2=_safeMin(yFilt),yMax2=_safeMax(yFilt);
  pts=pts.filter(function(p){return p.x>=xMin2&&p.x<=xMax2&&p.y>=yMin2&&p.y<=yMax2;});
  if(!pts.length){svg.innerHTML='';if(noteEl)noteEl.textContent='No data after filtering.';return;}
  var useLog=_SCATTER_Y_LOG&&pts.every(function(p){return p.y>0;});
  var xVals=pts.map(function(p){return p.x;});
  var yVals=pts.map(function(p){return p.y;});
  var xLo=_safeMin(xVals),xHi=_safeMax(xVals);
  var yLo=_safeMin(yVals),yHi=_safeMax(yVals);
  if(xLo===xHi){var d=Math.abs(xLo*0.05)||0.5;xLo-=d;xHi+=d;}
  if(yLo===yHi){var d=Math.abs(yLo*0.05)||0.01;yLo-=d;yHi+=d;}
  var xBuf=(xHi-xLo)*0.05;xLo-=xBuf;xHi+=xBuf;
  var yBuf=(yHi-yLo)*0.05;yLo-=yBuf;yHi+=yBuf;
  // Apply user-specified axis ranges if set
  var _sr=_scatterRanges[svgId]||{};
  if(_sr.xMin!=null)xLo=_sr.xMin;if(_sr.xMax!=null)xHi=_sr.xMax;
  if(_sr.yMin!=null)yLo=_sr.yMin;if(_sr.yMax!=null)yHi=_sr.yMax;
  if(xLo>=xHi){xLo-=0.01;xHi+=0.01;}if(yLo>=yHi){yLo-=0.01;yHi+=0.01;}
  var logYLo,logYHi,logYRange;
  if(useLog){logYLo=Math.log10(yLo>0?yLo:1e-9);logYHi=Math.log10(yHi);logYRange=logYHi-logYLo;if(logYRange<=0)logYRange=1;}
  var xMed=medArr(xVals),yMed=medArr(yVals);
  // Build ordered list of unique groups
  var matOrder=[],matSet2={};
  pts.forEach(function(pt){if(!matSet2[pt.m]){matSet2[pt.m]=true;matOrder.push(pt.m);}});
  var multiMat=matOrder.length>1;
  var matColor={};
  matOrder.forEach(function(m,i){matColor[m]=multiMat?_MPAL[i%_MPAL.length]:'#3498db';});
  var W=Math.max(svg.clientWidth||540,300),H=W;  // square: height always equals width
  var pl=110,pr=14,pt2=24,pb=48;
  var cW=W-pl-pr,cH=H-pt2-pb;
  var xRange=xHi-xLo,yRange=yHi-yLo;
  function _yPos(v){if(useLog&&v>0)return pt2+cH-((Math.log10(v)-logYLo)/logYRange)*cH;return pt2+cH-((v-yLo)/yRange)*cH;}
  var p=['<rect width="'+W+'" height="'+H+'" fill="#f8f9fa"/>'];
  for(var gi=0;gi<=4;gi++){
    var gy=pt2+gi*(cH/4);
    p.push('<line x1="'+pl+'" x2="'+(pl+cW)+'" y1="'+gy.toFixed(1)+'" y2="'+gy.toFixed(1)+'" stroke="#eee"/>');
    var gx=pl+gi*(cW/4);
    p.push('<line x1="'+gx.toFixed(1)+'" x2="'+gx.toFixed(1)+'" y1="'+pt2+'" y2="'+(pt2+cH)+'" stroke="#eee"/>');
  }
  // Scatter dots — packed <path> per color group for fast rendering with large N.
  // Pixel-dedup at 0.5px resolution merges overlapping dots before building path.
  var _dedupSeen={};
  var _matPaths={};
  matOrder.forEach(function(m){_matPaths[m]=[];});
  var _totalDots=pts.length,_shownDots=0;
  for(var i=0;i<pts.length;i++){
    if(useLog&&pts[i].y<=0)continue;
    var cx=pl+((pts[i].x-xLo)/xRange)*cW;
    var cy=_yPos(pts[i].y);
    var _pk=Math.round(cx*2)+'|'+Math.round(cy*2)+'|'+pts[i].m;
    if(!_dedupSeen[_pk]){_dedupSeen[_pk]=true;_matPaths[pts[i].m].push([(cx-0.81).toFixed(2),cy.toFixed(1)]);_shownDots++;}
  }
  matOrder.forEach(function(m){
    var _mp=_matPaths[m];
    if(!_mp.length)return;
    var _d=[];
    for(var _di=0;_di<_mp.length;_di++){_d.push('M '+_mp[_di][0]+' '+_mp[_di][1]+' a 0.81,0.81 0 1,0 1.62,0 a 0.81,0.81 0 1,0 -1.62,0');}
    p.push('<path d="'+_d.join(' ')+'" fill="'+matColor[m]+'" fill-opacity="0.7" stroke="none"/>');
  });
  // Per-material fit lines
  var fitLegend=[];
  matOrder.forEach(function(mat){
    var mPts=pts.filter(function(p){return p.m===mat;});
    // In log mode, regress log10(y) vs x so trend line is consistent between log/linear views
    var regPts=mPts;
    if(useLog){regPts=mPts.filter(function(p){return p.y>0;}).map(function(p){return {x:p.x,y:Math.log10(p.y),m:p.m};});}
    var fit=_SCATTER_THEIL_SEN?_theilSen(regPts):_linReg(regPts);
    if(!fit)return;
    var clr=matColor[mat];
    var fitY1,fitY2;
    if(useLog){fitY1=Math.pow(10,fit.slope*xLo+fit.intercept);fitY2=Math.pow(10,fit.slope*xHi+fit.intercept);}
    else{fitY1=fit.slope*xLo+fit.intercept;fitY2=fit.slope*xHi+fit.intercept;}
    var fx1=pl,fy1=_yPos(fitY1);
    var fx2=pl+cW,fy2=_yPos(fitY2);
    p.push('<line x1="'+fx1.toFixed(1)+'" x2="'+fx2.toFixed(1)+'" y1="'+fy1.toFixed(1)+'" y2="'+fy2.toFixed(1)+'" stroke="'+clr+'" stroke-width="2.5" stroke-dasharray="8,3" opacity="1.0"/>');
    var xs=mPts.map(function(p){return p.x;}).sort(function(a,b){return a-b;});
    var ys=mPts.map(function(p){return p.y;}).sort(function(a,b){return a-b;});
    var medX=xs.length%2?xs[(xs.length-1)/2]:(xs[xs.length/2-1]+xs[xs.length/2])/2;
    var medY=ys.length%2?ys[(ys.length-1)/2]:(ys[ys.length/2-1]+ys[ys.length/2])/2;
    // Median diamond marker for this group
    var _dmx=pl+((medX-xLo)/xRange)*cW;
    var _dmy=_yPos(medY);
    var _ds=7;
    p.push('<polygon points="'+_dmx.toFixed(1)+','+(_dmy-_ds).toFixed(1)+' '+(_dmx+_ds).toFixed(1)+','+_dmy.toFixed(1)+' '+_dmx.toFixed(1)+','+(_dmy+_ds).toFixed(1)+' '+(_dmx-_ds).toFixed(1)+','+_dmy.toFixed(1)+'" fill="'+clr+'" stroke="#222" stroke-width="1.2" "/>');
    var eqTxt='y='+_fmtV(fit.slope)+'x'+(fit.intercept>=0?'+':'')+_fmtV(fit.intercept)+' (R\u00B2='+fit.r2.toFixed(3)+', med x='+medX.toFixed(3)+', y='+medY.toFixed(3)+')';
    fitLegend.push({label:(mat||'All')+': '+eqTxt,color:clr});
  });
  // Legend (bottom-right, starting at 0.25 of chart width)
  if(fitLegend.length){
    var lx=pl+4,ly=pt2+cH-4;
    for(var li=fitLegend.length-1;li>=0;li--){
      p.push('<line x1="'+lx+'" x2="'+(lx+10)+'" y1="'+ly+'" y2="'+ly+'" stroke="'+fitLegend[li].color+'" stroke-width="2"/>');
      p.push('<text x="'+(lx+14)+'" y="'+(ly+3)+'" font-size="11" fill="'+fitLegend[li].color+'" font-weight="bold">'+esc(fitLegend[li].label)+'</text>');
      ly-=12;
    }
  }
  p.push('<line x1="'+pl+'" x2="'+(pl+cW)+'" y1="'+(pt2+cH)+'" y2="'+(pt2+cH)+'" stroke="#aaa"/>');
  p.push('<line x1="'+pl+'" x2="'+pl+'" y1="'+pt2+'" y2="'+(pt2+cH)+'" stroke="#aaa"/>');
  var xNice=_niceTicks(xLo,xHi,5);
  for(var ti=0;ti<xNice.length;ti++){
    var xv=xNice[ti];
    var xx=pl+((xv-xLo)/xRange)*cW;
    if(xx>=pl-1&&xx<=pl+cW+1){
      p.push('<line x1="'+xx.toFixed(1)+'" x2="'+xx.toFixed(1)+'" y1="'+(pt2+cH)+'" y2="'+(pt2+cH+4)+'" stroke="#aaa"/>');
      p.push('<text x="'+xx.toFixed(1)+'" y="'+(pt2+cH+20)+'" text-anchor="middle" font-size="18" fill="#444">'+xv.toFixed(1)+'%</text>');
    }
  }
  var yNice;
  if(useLog){
    // Log ticks: major at powers of 10, minor at 2,3,4,5,6,7,8,9 × 10^n
    yNice=[];
    var lo10=Math.floor(logYLo),hi10=Math.ceil(logYHi);
    for(var ei=lo10;ei<=hi10;ei++){
      var tv=Math.pow(10,ei);
      if(tv>=yLo*0.99&&tv<=yHi*1.01)yNice.push({v:tv,major:true});
      if(ei<hi10){for(var mi=2;mi<=9;mi++){var mv=mi*tv;if(mv>=yLo*0.99&&mv<=yHi*1.01)yNice.push({v:mv,major:false});}}
    }
    if(!yNice.length)yNice=[{v:yLo,major:true},{v:yHi,major:true}];
  }else{  // linear — wrap to same shape
    yNice=_niceTicks(yLo,yHi,4).map(function(v){return {v:v,major:true};});
  }
  for(var ti=0;ti<yNice.length;ti++){
    var _yt=yNice[ti];var yv=_yt.v;var isMajor=_yt.major;
    var yy=_yPos(yv);
    if(yy>=pt2-1&&yy<=pt2+cH+1){
      if(isMajor){
        p.push('<line x1="'+(pl-5)+'" x2="'+pl+'" y1="'+yy.toFixed(1)+'" y2="'+yy.toFixed(1)+'" stroke="#aaa" stroke-width="1"/>');
        p.push('<line x1="'+pl+'" x2="'+(pl+cW)+'" y1="'+yy.toFixed(1)+'" y2="'+yy.toFixed(1)+'" stroke="rgba(0,0,0,0.07)" stroke-width="0.7"/>');
        var _yvLabel=useLog?_fmtSci(yv):_fmtV(yv);
        p.push('<text x="'+(pl-8)+'" y="'+(yy+6).toFixed(1)+'" text-anchor="end" font-size="18" fill="#444">'+_yvLabel+'</text>');
      }else{
        p.push('<line x1="'+(pl-3)+'" x2="'+pl+'" y1="'+yy.toFixed(1)+'" y2="'+yy.toFixed(1)+'" stroke="#bbb" stroke-width="0.8"/>');
        p.push('<line x1="'+pl+'" x2="'+(pl+cW)+'" y1="'+yy.toFixed(1)+'" y2="'+yy.toFixed(1)+'" stroke="rgba(0,0,0,0.03)" stroke-width="0.5"/>');
        var _mnLabel=useLog?_fmtSci(yv):_fmtV(yv);
        p.push('<text x="'+(pl-8)+'" y="'+(yy+6).toFixed(1)+'" text-anchor="end" font-size="18" fill="#444">'+_mnLabel+'</text>');
      }
    }
  }
  p.push('<text x="'+(pl+cW/2).toFixed(1)+'" y="'+(H-4)+'" text-anchor="middle" font-size="18" fill="#333" font-weight="bold">'+esc(uCol)+' (%)</text>');
  p.push('<text x="20" y="'+(pt2+cH/2).toFixed(1)+'" text-anchor="middle" font-size="18" fill="#333" font-weight="bold" transform="rotate(-90,20,'+(pt2+cH/2)+')">'+esc(col)+'</text>');
  p.push('<text x="'+(pl+cW/2).toFixed(1)+'" y="'+(pt2-8)+'" text-anchor="middle" font-size="18" fill="#333" font-weight="bold">'+esc(col)+' vs '+esc(uCol)+'</text>');
  svg.setAttribute('viewBox','0 0 '+W+' '+H);
  svg.innerHTML=p.join('');
  // Add draggable cursor lines (initialized at median)
  _initDragCursors(svg,xMed,yMed,xLo,xHi,yLo,yHi,pl,pt2,cW,cH,
    uCol,col,function(v){return v.toFixed(2)+'%';},_fmtV);
  var _dotNote=_shownDots<_totalDots?' ('+_shownDots+' shown, '+(_totalDots-_shownDots)+' overlapping merged)':'';
  if(noteEl)noteEl.textContent='X med='+xMed.toFixed(2)+'%, Y med='+_fmtV(yMed);
}

function updateAll() {
  var activeId = (document.querySelector('.tab-panel.active') || {}).id;
  Object.keys(_TAB_RENDERS).forEach(function(id) {
    if (!_TAB_LAZY[id] || id === activeId) _TAB_RENDERS[id]();
  });
  rFilter();
}
function init() {
  if (SICC_COLS.length)       { SEL_COL = SICC_COLS[0]; IS_CDYN = false; }
  else if (UPM_COLS.length)   { SEL_COL = UPM_COLS[0];  IS_CDYN = false; }
  else if (CDYN_COLS.length)  { SEL_COL = CDYN_COLS[0]; IS_CDYN = true;  }
  // Pre-populate SEL_WFR with all rows on first load
  if(SEL_WFR.size===0)DATA.rows.forEach(function(_,i){SEL_WFR.add(i);});
  rFilter();
  // Render non-lazy tabs on load
  Object.keys(_TAB_RENDERS).forEach(function(id) {
    if (!_TAB_LAZY[id]) _TAB_RENDERS[id]();
  });
}
// ── Shared distribution-body renderer (used by SICC + CDYN tabs) ─────────────
function _renderDistBody(active,col,cfg){
  var titleEl=document.getElementById(cfg.distTitle);
  var ne=document.getElementById(cfg.noteEl);
  var tgt=cfg.isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
  if(titleEl)titleEl.textContent=col+(cfg.isCdyn?' CDYN':' SICC')+' Distribution';
  if(!active.length){
    drawTabScatter([],null,cfg.scatterSvg,cfg.scatterTitle,cfg.scatterNote);
    drawSVG([],[],null,null,'',cfg.histSvg,false);
    renderStatsTable(null,cfg.statsTbl);
    drawMiniUpm([],null,cfg.isCdyn,cfg.miniSvg,cfg.miniTitle,cfg.miniNote);
    return;
  }
  var allVals=[];
  active.forEach(function(i){
    var r=ROWS[i];
    var dp=r.die_pairs&&r.die_pairs[col];
    if(dp&&dp.s&&dp.s.length){
      for(var di=0;di<dp.s.length;di++){if(dp.s[di]!=null&&!isNaN(dp.s[di]))allVals.push(dp.s[di]);}
    }
  });
  allVals=filterOutliers(allVals.filter(function(v){return v>0;}),5);
  if(!allVals.length){drawSVG([],[],null,tgt,col,cfg.histSvg,false);renderStatsTable(null,cfg.statsTbl);if(ne)ne.textContent='No data.';return;}
  var lo=_safeMin(allVals),hi=_safeMax(allVals);
  if(lo===hi){var d=Math.abs(lo*0.05)||0.01;lo-=d;hi+=d;}
  var nb=Math.max(6,Math.min(30,Math.round(Math.sqrt(allVals.length))));
  var step=(hi-lo)/nb;var edges=[],counts=[];
  for(var bi=0;bi<=nb;bi++)edges.push(lo+bi*step);
  for(var bi=0;bi<nb;bi++)counts.push(0);
  allVals.forEach(function(m){var idx=Math.min(nb-1,Math.floor((m-lo)/step));if(idx<0)idx=0;counts[idx]++;});
  var isSiccCol=!cfg.isCdyn&&(SICC_COLS.indexOf(col)>=0||SICC_TBL_CFG.some(function(r){return r[2]===col;}));
  var upmOv=(cfg.isCdyn||isSiccCol)?_buildUpmOverlay(active,col,cfg.isCdyn):null;
  drawSVG(edges,counts,medArr(allVals),tgt,col,cfg.histSvg,false,upmOv,cfg.isCdyn?'CDYN':'SICC');
  renderStatsTable(computeStats(allVals),cfg.statsTbl,4);
  if(ne)ne.textContent='Die distribution \u2014 '+active.length+' wafer(s), '+allVals.length+' values';
  drawTabScatter(active,col,cfg.scatterSvg,cfg.scatterTitle,cfg.scatterNote);
  drawMiniUpm(active,col,cfg.isCdyn,cfg.miniSvg,cfg.miniTitle,cfg.miniNote);
}
/* ── Export a rendered <table> to CSV download ──────────────────────────────
   headId : id of the <thead> element
   bodyId : id of the <tbody> element
   fname  : suggested download filename (no extension; .csv is appended)        */
function exportTblCsv(headId,bodyId,fname){
  function cellText(td){return td.textContent.replace(/\s+/g,' ').trim();}
  function quoteCsv(s){return(s.indexOf(',')>=0||s.indexOf('"')>=0||s.indexOf('\n')>=0)?'"'+s.replace(/"/g,'""')+'"':s;}
  var head=document.getElementById(headId);
  var body=document.getElementById(bodyId);
  if(!head||!body)return;
  var lines=[];
  Array.from(head.querySelectorAll('tr')).forEach(function(tr){
    lines.push(Array.from(tr.querySelectorAll('th,td')).map(function(c){return quoteCsv(cellText(c));}).join(','));
  });
  Array.from(body.querySelectorAll('tr')).forEach(function(tr){
    // Skip category header rows (they contain a colspan and no useful data columns)
    if(tr.classList.contains('cat-hdr'))return;
    lines.push(Array.from(tr.querySelectorAll('th,td')).map(function(c){return quoteCsv(cellText(c));}).join(','));
  });
  if(!lines.length)return;
  var blob=new Blob([lines.join('\r\n')],{type:'text/csv'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download=(fname||'export')+'.csv';document.body.appendChild(a);a.click();
  setTimeout(function(){document.body.removeChild(a);URL.revokeObjectURL(a.href);},100);
}
window.exportTblCsv=exportTblCsv;
'''
)  # end SHARED_JS

# ── Resize panel IIFE ────────────────────────────────────────────────────────
RESIZE_JS = '''
// ── Resizable panels ────────────────────────────────────────────────────────
(function(){
  var LS='dsh_';
  function sv(k,v){try{localStorage.setItem(LS+k,String(v));}catch(e){}}
  function gv(k){try{return localStorage.getItem(LS+k);}catch(e){return null;}}
  // Restore saved sizes on load
  function restoreSizes(){
    // wfr-panel: restore width or collapsed state
    var wp=document.getElementById('wfr-panel');
    var wSpl=document.getElementById('wfr-splitter');
    var colState=gv('col_wfr-panel');
    if(colState==='1'){
      if(wp){wp.style.width='0';wp.style.flex='0 0 0';wp.style.minWidth='0';wp.dataset.collapsed='1';}
      if(wSpl)wSpl.style.display='none';
      var btn=document.getElementById('sidebar-toggle-btn');
      if(btn)btn.style.color='#3498db';
    }else{
      var w=gv('wfr-panel-w');
      if(wp&&w){wp.style.width=w+'px';wp.style.flex='0 0 '+w+'px';}
    }
    // tbl-side widths (table panel on left of charts splitter)
    [['sicc-tbl-side','sicc-tbl-w'],['cdyn-tbl-side','cdyn-tbl-w']].forEach(function(p){
      var el=document.getElementById(p[0]);var d=gv(p[1]);
      if(el&&d){el.style.flex='0 0 '+d+'px';el.style.width=d+'px';}
    });
    // Collapse states
    [['wfr-panel','wfr-splitter','wfr-tbl-wrap'],
     ['upm-dist-panel','sicc-dist-splitter','upm-dist-body'],
     ['cdyn-dist-panel','cdyn-dist-splitter','cdyn-dist-body']].forEach(function(p){
      var state=gv('col_'+p[0]);
      if(state==='1')_applyCollapse(p[0],p[1],p[2],true);
    });
  }
  function _applyCollapse(panelId,splitterId,bodyId,collapsed){
    var panel=document.getElementById(panelId);
    var spl=document.getElementById(splitterId);
    var body=document.getElementById(bodyId)||panel&&panel.querySelector('.wfr-tbl-wrap');
    if(body)body.style.display=collapsed?'none':'';
    if(spl)spl.style.display=collapsed?'none':'';
    var btn=panel&&panel.querySelector('.collapse-btn');
    if(btn)btn.innerHTML=collapsed?'&#9654;':'&#9664;';
  }
  // Re-render plots for the currently active tab
  function _rerender(){
    var active=document.querySelector('.tab-panel.active');
    if(!active)return;
    var id=active.id;
    if(id==='tab-sicc'){if(typeof render_sicc==='function')render_sicc();}
    else if(id==='tab-cdyn'){if(typeof render_cdyn==='function')render_cdyn();}
    else if(id==='tab-dist'){if(typeof renderHist==='function')renderHist();}
    else if(id==='tab-summ'){if(typeof render_summ==='function')render_summ();}
  }
  // Horizontal splitter drag: leftId is the panel being resized
  window.startSplit=function(e,leftId,rightId,storageKey){
    e.preventDefault();
    var left=document.getElementById(leftId);if(!left)return;
    var spl=e.currentTarget;spl.classList.add('dragging');
    var startX=e.clientX,startW=left.getBoundingClientRect().width;
    function mm(ev){
      var w=Math.max(120,startW+(ev.clientX-startX));
      left.style.flex='0 0 '+w+'px';left.style.width=w+'px';
    }
    function mu(){
      document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);
      spl.classList.remove('dragging');
      var finalW=left.getBoundingClientRect().width;
      if(storageKey)sv(storageKey,finalW);
      // Also save as the panel's preferred width (for collapse/expand)
      if(leftId==='wfr-panel')sv('wfr-panel-w',finalW);
      _rerender();
    }
    document.addEventListener('mousemove',mm);document.addEventListener('mouseup',mu);
  };
  // Toggle collapse for left wfr-panel (fully hides, saves/restores width)
  window.togglePanel=function(panelId,splitterId){
    var panel=document.getElementById(panelId);if(!panel)return;
    var spl=document.getElementById(splitterId);
    var isCollapsed=panel.dataset.collapsed==='1';
    if(isCollapsed){
      // Restore
      var savedW=gv(panelId+'-w')||'280';
      panel.style.width=savedW+'px';panel.style.flex='0 0 '+savedW+'px';panel.style.minWidth='';
      panel.dataset.collapsed='0';
      if(spl)spl.style.display='';
      var btn=document.getElementById('sidebar-toggle-btn');
      if(btn)btn.style.color='';
      sv('col_'+panelId,'0');
      setTimeout(_rerender,50);
    }else{
      // Save current width then collapse
      var curW=panel.getBoundingClientRect().width;
      if(curW>10)sv(panelId+'-w',curW);
      panel.style.width='0';panel.style.flex='0 0 0';panel.style.minWidth='0';
      panel.dataset.collapsed='1';
      if(spl)spl.style.display='none';
      var btn=document.getElementById('sidebar-toggle-btn');
      if(btn)btn.style.color='#3498db';
      sv('col_'+panelId,'1');
      setTimeout(_rerender,50);
    }
  };
  // Toggle collapse for dist-side panels
  window.toggleDistPanel=function(panelId,splitterId){
    var panel=document.getElementById(panelId);if(!panel)return;
    var bodyId=panelId==='upm-dist-panel'?'upm-dist-body':'cdyn-dist-body';
    var body=document.getElementById(bodyId);if(!body)return;
    var collapsed=body.style.display==='none';
    _applyCollapse(panelId,splitterId,bodyId,!collapsed);
    sv('col_'+panelId,collapsed?'0':'1');
  };
  // Init on DOM ready
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',restoreSizes);
  else restoreSizes();

  // ── XY Plot proportional resize (drag corner handle) ──
  document.addEventListener('mousedown',function(e){
    var handle=e.target.closest('.xy-resize-handle');
    if(!handle)return;
    e.preventDefault();
    var wrap=handle.closest('.xy-resize-wrap');
    if(!wrap)return;
    var svg=wrap.querySelector('svg');
    if(!svg)return;
    var startX=e.clientX,startY=e.clientY;
    var startW=wrap.offsetWidth,startH=svg.offsetHeight;
    var ratio=startW/startH;
    svg.style.aspectRatio='auto';
    svg.style.height=startH+'px';
    wrap.style.width=startW+'px';
    wrap.style.maxWidth='none';
    if(wrap.style.flex)wrap.style.flex='0 0 '+startW+'px';
    function onMove(ev){
      var dx=ev.clientX-startX,dy=ev.clientY-startY;
      // Use whichever delta is larger to drive proportional resize
      var delta=Math.abs(dx)>Math.abs(dy)?dx:dy;
      var newW=Math.max(200,startW+delta);
      var newH=Math.max(150,newW/ratio);
      wrap.style.width=newW+'px';
      if(wrap.style.flex)wrap.style.flex='0 0 '+newW+'px';
      svg.style.height=newH+'px';
    }
    function onUp(){
      document.removeEventListener('mousemove',onMove);
      document.removeEventListener('mouseup',onUp);
      _rerender();
    }
    document.addEventListener('mousemove',onMove);
    document.addEventListener('mouseup',onUp);
  });
})();
'''

# ── Python HTML helpers (shared layout builders) ────────────────────────────

_GROUP_BY_HTML = (
    '<div style="margin:4px 0;font-size:8px;color:#555">Group by: '
    "<label style=\"margin-left:6px;cursor:pointer\"><input type=\"checkbox\" class=\"xy-cb\" value=\"none\" onchange=\"_toggleXYGroup('none')\"> None</label>"
    "<label style=\"margin-left:6px;cursor:pointer\"><input type=\"checkbox\" class=\"xy-cb\" value=\"program\" onchange=\"_toggleXYGroup('program')\"> Program</label>"
    "<label style=\"margin-left:6px;cursor:pointer\"><input type=\"checkbox\" class=\"xy-cb\" value=\"lot\" onchange=\"_toggleXYGroup('lot')\"> Lot</label>"
    "<label style=\"margin-left:6px;cursor:pointer\"><input type=\"checkbox\" class=\"xy-cb\" value=\"wafer\" onchange=\"_toggleXYGroup('wafer')\"> Wafer</label>"
    "<label style=\"margin-left:6px;cursor:pointer\"><input type=\"checkbox\" class=\"xy-cb\" value=\"material\" onchange=\"_toggleXYGroup('material')\" checked> Material</label>"
    '</div>'
)
_GROUP_BY_HTML_INLINE = (
    "<label style=\"margin-left:4px;cursor:pointer\"><input type=\"checkbox\" class=\"xy-cb\" value=\"none\" onchange=\"_toggleXYGroup('none')\"> None</label>"
    "<label style=\"margin-left:4px;cursor:pointer\"><input type=\"checkbox\" class=\"xy-cb\" value=\"program\" onchange=\"_toggleXYGroup('program')\"> Program</label>"
    "<label style=\"margin-left:4px;cursor:pointer\"><input type=\"checkbox\" class=\"xy-cb\" value=\"lot\" onchange=\"_toggleXYGroup('lot')\"> Lot</label>"
    "<label style=\"margin-left:4px;cursor:pointer\"><input type=\"checkbox\" class=\"xy-cb\" value=\"wafer\" onchange=\"_toggleXYGroup('wafer')\"> Wafer</label>"
    "<label style=\"margin-left:4px;cursor:pointer\"><input type=\"checkbox\" class=\"xy-cb\" value=\"material\" onchange=\"_toggleXYGroup('material')\" checked> Material</label>"
)

def build_dist_body_html(
        scatter_svg, scatter_title, scatter_note,
        dist_title, hist_svg, chart_note, stats_tbl,
        mini_upm_panel, mini_upm_title, mini_upm_svg, mini_upm_note,
        scatter_max_width='100%', hist_height='297', mini_height='200',
        body_max_width=''):
    """Vertical 3-layer layout (XY scatter → Distribution → UPM).
    scatter_max_width controls XY panel width (e.g. '90%' for SICC 1.5x).
    body_max_width optionally caps the outer container (e.g. '480px').
    Used by SICC and CDYN tabs so one change fixes both."""
    wrap_open  = f'      <div style="max-width:{body_max_width}">\n' if body_max_width else ''
    wrap_close = '      </div>\n' if body_max_width else ''
    return (
        wrap_open
        # ── Layer 1: XY Scatter ──
        + f'      <div class="xy-resize-wrap" style="max-width:{scatter_max_width};margin-top:2px;position:relative">\n'
        + '        <div style="font-size:24px;color:#888;margin-bottom:4px;line-height:1.6">Group by: '
        + _GROUP_BY_HTML_INLINE
        + '</div>\n'
        + f'        <h3 id="{scatter_title}" style="margin:4px 0 2px;font-size:12px;color:#2c3e50">XY Scatter</h3>\n'
        + '        <div style="font-size:11px;color:#555;margin-bottom:3px">'
        + '<button class="scatter-ylog-btn" onclick="_toggleScatterYLog()" '
        + 'style="font-size:11px;padding:2px 8px;cursor:pointer;border:1px solid #7f8c8d;border-radius:4px;background:#2c3e50;color:#fff" '
        + 'title="Toggle Y-axis between linear and log scale">Y: Log</button>'
        + f'<span style="margin-left:10px;font-size:11px;color:#444">X:</span>'
        + f'<input id="{scatter_svg}-xmin" type="number" step="any" placeholder="auto" style="width:68px;font-size:11px;border:1px solid #bbb;border-radius:3px;padding:1px 4px;margin-left:3px">'
        + f'<span style="font-size:11px;color:#666;padding:0 3px">–</span>'
        + f'<input id="{scatter_svg}-xmax" type="number" step="any" placeholder="auto" style="width:68px;font-size:11px;border:1px solid #bbb;border-radius:3px;padding:1px 4px">'
        + f'<span style="margin-left:8px;font-size:11px;color:#444">Y:</span>'
        + f'<input id="{scatter_svg}-ymin" type="number" step="any" placeholder="auto" style="width:68px;font-size:11px;border:1px solid #bbb;border-radius:3px;padding:1px 4px;margin-left:3px">'
        + f'<span style="font-size:11px;color:#666;padding:0 3px">–</span>'
        + f'<input id="{scatter_svg}-ymax" type="number" step="any" placeholder="auto" style="width:68px;font-size:11px;border:1px solid #bbb;border-radius:3px;padding:1px 4px">'
        + f'<button onclick="_applyScatterRange(\'{scatter_svg}\')" style="margin-left:6px;font-size:11px;padding:2px 8px;cursor:pointer;border:1px solid #27ae60;border-radius:4px;background:#27ae60;color:#fff">Apply</button>'
        + f'<button onclick="_resetScatterRange(\'{scatter_svg}\')" style="margin-left:3px;font-size:11px;padding:2px 8px;cursor:pointer;border:1px solid #95a5a6;border-radius:4px;background:#ecf0f1;color:#333">Reset</button>'
        + '<label style="margin-left:14px;font-size:11px;color:#444;cursor:pointer" title="Theil-Sen uses median of pairwise slopes — robust to outliers. OLS uses mean.">'
        + '<input type="checkbox" class="scatter-theil-cb" onchange="_toggleTheilSen(this)" style="vertical-align:middle;margin-right:3px">Theil-Sen</label>'
        + '</div>\n'
        + f'        <svg id="{scatter_svg}" style="width:100%;aspect-ratio:1/1;display:block;border:1px solid #eee;border-radius:4px;background:#fff"></svg>\n'
        + '        <div class="xy-resize-handle" style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#aaa 50%);border-radius:0 0 4px 0;opacity:0.5" title="Drag to resize"></div>\n'
        + f'        <div class="chart-note" id="{scatter_note}" style="font-size:16px;color:#2c3e50;margin-top:4px"></div>\n'
        + '      </div>\n'
        # ── Layer 2: Distribution histogram (same width cap) ──
        + '      <div class="xy-resize-wrap" style="max-width:95%;margin-top:36px;position:relative">\n'
        + f'        <h3 id="{dist_title}" style="margin:0 0 2px;font-size:13px;color:#2c3e50">Distribution</h3>\n'
        + f'        <svg id="{hist_svg}" height="{hist_height}" style="width:100%;display:block;border:1px solid #eee;border-radius:4px;background:#fff"></svg>\n'
        + '        <div class="xy-resize-handle" style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#aaa 50%);border-radius:0 0 4px 0;opacity:0.5" title="Drag to resize"></div>\n'
        + f'        <div class="chart-note" id="{chart_note}" style="font-size:15px;color:#7f8c8d;margin-top:4px"></div>\n'
        + f'        <div id="{stats_tbl}" style="margin-top:8px"></div>\n'
        + '      </div>\n'
        # ── Layer 3: Mini-UPM (same width cap) ──
        + f'      <div id="{mini_upm_panel}" style="max-width:80%;margin-top:36px">\n'
        + f'        <h3 id="{mini_upm_title}" style="margin:0 0 2px;font-size:12px;color:#c0650a">UPM Distribution</h3>\n'
        + f'        <svg id="{mini_upm_svg}" height="{mini_height}" style="width:100%;display:block;border:1px solid #f5e0c3;border-radius:4px;background:#fffaf4"></svg>\n'
        + f'        <div id="{mini_upm_note}" style="font-size:9px;color:#c0650a;margin-top:2px"></div>\n'
        + '      </div>'
        + ('\n' + wrap_close if body_max_width else '')
    )

# ════════════════════════════════════════════════════════════════
# Parameter Table JS  (formerly _tab_summ.py)
# ════════════════════════════════════════════════════════════════



def _summ_tab_html() -> str:
    return ''


def _summ_tab_js() -> str:
    return '''
/* ── Parameter Table state ──────────────────────────────────────────── */
var _ptRows=[];        // [{type,cat,testName,dispName,upmCol,isCdyn,groupKey}, ...]
var _ptSortCol=null;
var _ptSortDir=1;
var _ptQuery='';
var _ptSelSet=new Set();
var _ptPopupGroupBy=['material']; // groupby state for popup plots

/* 9 distinct group colors: 4 cat slots × SICC/CDYN + UPM slot */
var _PT_COLORS=[
  ['#dbeafe','#1e40af'],['#dcfce7','#166534'],  /* cat0: SICC=light-blue, CDYN=light-green */
  ['#fef9c3','#854d0e'],['#fce7f3','#9d174d'],  /* cat1: SICC=light-yellow, CDYN=light-pink */
  ['#ede9fe','#5b21b6'],['#ffedd5','#9a3412'],  /* cat2: SICC=light-violet, CDYN=light-orange */
  ['#e0f2fe','#075985'],['#f0fdf4','#14532d'],  /* cat3: more blues/greens */
  ['#fff7ed','#c2410c']                          /* cat4: UPM=light-amber */
];

function _ptGroupColor(catIdx,isCdyn,isUpm){
  if(isUpm){return['#fff7ed','#c2410c'];}
  var pair=_PT_COLORS[catIdx%(_PT_COLORS.length/2|0)*2+(isCdyn?1:0)];
  return pair||['#f8f9fa','#374151'];
}

function _ptBuildRows(){
  _ptRows=[];
  var catMap={},catIdx=0;
  /* SICC rows */
  (SICC_TBL_CFG||[]).forEach(function(r){
    var cat=r[0];
    if(!(cat in catMap))catMap[cat]=catIdx++;
    _ptRows.push({type:'SICC',cat:cat,testName:r[2],dispName:r[1]||r[2],upmCol:r[3]||'',isCdyn:false,isUpm:false,catIdx:catMap[cat]});
  });
  /* CDYN rows */
  (CDYN_TBL_CFG||[]).forEach(function(r){
    var cat=r[0];
    if(!(cat in catMap))catMap[cat]=catIdx++;
    _ptRows.push({type:'CDYN',cat:cat,testName:r[2],dispName:r[1]||r[2],upmCol:r[3]||'',isCdyn:true,isUpm:false,catIdx:catMap[cat]});
  });
  /* UPM rows */
  (typeof UPM_TBL_CFG!=='undefined'?UPM_TBL_CFG:[]).forEach(function(r){
    var cat=r[0];
    if(!(cat in catMap))catMap[cat]=catIdx++;
    _ptRows.push({type:'UPM',cat:cat,testName:r[2],dispName:r[1]||r[2],upmCol:r[3]||r[2],isCdyn:false,isUpm:true,catIdx:catMap[cat]});
  });
  /* fallback: raw SICC cols */
  if(!_ptRows.length){
    SICC_COLS.forEach(function(c){_ptRows.push({type:'SICC',cat:'SICC',testName:c,dispName:c,upmCol:'',isCdyn:false,isUpm:false,catIdx:0});});
    CDYN_COLS.forEach(function(c){_ptRows.push({type:'CDYN',cat:'CDYN',testName:c,dispName:c,upmCol:'',isCdyn:true,isUpm:false,catIdx:1});});
  }
  _ptSelSet=new Set(); /* will be populated by _ptSyncFromSicc below */
}

function _ptComputeRow(row,ai){
  var actual=null,tgt=null,ratio=null,upmMed=null,upmTgt=null;
  if(row.isCdyn){
    var v=ai.map(function(i){return ROWS[i].cdyn[row.testName];}).filter(function(v){return v!=null&&!isNaN(v);});
    actual=medArr(v); tgt=CDYN_TARGETS[row.testName]||null;
  }else{
    var v=ai.map(function(i){return ROWS[i].medians[row.testName];}).filter(function(v){return v!=null&&!isNaN(v);});
    actual=medArr(v); tgt=TARGETS[row.testName.toUpperCase()]||null;
  }
  ratio=(actual!=null&&tgt!=null&&tgt!==0)?actual/tgt:null;
  if(row.upmCol&&!row.isUpm){
    var uv=ai.map(function(i){return ROWS[i].medians[row.upmCol];}).filter(function(v){return v!=null&&!isNaN(v);});
    upmMed=medArr(uv); upmTgt=TARGETS[row.upmCol.toUpperCase()]||null;
  }
  return{actual:actual,tgt:tgt,ratio:ratio,upmMed:upmMed,upmTgt:upmTgt};
}

function _ptRender(){
  var hd=document.getElementById('param-tbl-head'),bd=document.getElementById('param-tbl-body');
  if(!hd||!bd)return;
  var ai=SEL_WFR.size>0?Array.from(SEL_WFR):getFiltered();
  /* Compute values for all rows */
  var computed=_ptRows.map(function(r){return _ptComputeRow(r,ai);});
  /* Sort */
  var idxArr=_ptRows.map(function(_,i){return i;});
  if(_ptSortCol!==null){
    idxArr.sort(function(a,b){
      var va=_ptSortVal(a,computed[a]),vb=_ptSortVal(b,computed[b]);
      if(va===null&&vb===null)return 0;
      if(va===null)return 1;if(vb===null)return -1;
      return _ptSortDir*(va<vb?-1:va>vb?1:0);
    });
  }
  /* Header */
  var cols=[
    {k:'sel',l:'',w:'28px'},
    {k:'actions',l:'',w:'44px'},
    {k:'type',l:'Type',w:'46px'},
    {k:'cat',l:'Category',w:'80px'},
    {k:'test',l:'Parameter',w:'160px'},
    {k:'actual',l:'Median',w:'72px'},
    {k:'tgt',l:'Target',w:'72px'},
    {k:'ratio',l:'Ratio',w:'52px'},
    {k:'upm',l:'UPM%',w:'52px'},
    {k:'upmtgt',l:'UPM Tgt',w:'60px'}
  ];
  var th='background:#2c3e50;color:#fff;padding:4px 7px;font-size:11px;position:sticky;top:0;z-index:2;cursor:pointer;user-select:none;white-space:nowrap';
  var hdrHtml='<tr>';
  cols.forEach(function(c){
    if(c.k==='sel'){
      hdrHtml+='<th style="'+th+';cursor:default;width:'+c.w+'"><input type="checkbox" id="pt-sel-all" onmousedown="this._wasIndet=this.indeterminate" onclick="_ptHdrClick(this)" style="cursor:pointer"></th>';
    }else if(c.k==='actions'){
      hdrHtml+='<th style="'+th+';cursor:default;width:'+c.w+'">'+c.l+'</th>';
    }else{
      var arrow=_ptSortCol===c.k?(_ptSortDir>0?' &#9650;':' &#9660;'):'';
      hdrHtml+='<th style="'+th+';width:'+c.w+'" data-sk="'+c.k+'" onclick="_ptSort(this.dataset.sk)">'+c.l+arrow+'</th>';
    }
  });
  hdrHtml+='</tr>';
  hd.innerHTML=hdrHtml;
  /* Restore header checkbox state */
  var allCb=document.getElementById('pt-sel-all');
  if(allCb){
    var visCount=0,selCount=0;
    idxArr.forEach(function(i){
      var row=_ptRows[i];
      var q=_ptQuery.toLowerCase();
      if(q&&(row.dispName+row.cat+row.type+row.testName).toLowerCase().indexOf(q)<0)return;
      visCount++;if(_ptSelSet.has(i))selCount++;
    });
    allCb.checked=visCount>0&&selCount===visCount;
    allCb.indeterminate=selCount>0&&selCount<visCount;
  }
  /* Body */
  var q=_ptQuery.toLowerCase();
  var body='';
  idxArr.forEach(function(i){
    var row=_ptRows[i],cv=computed[i];
    /* search filter */
    if(q&&(row.dispName+row.cat+row.type+row.testName).toLowerCase().indexOf(q)<0)return;
    var clr=_ptGroupColor(row.catIdx,row.isCdyn,row.isUpm);
    var bg=clr[0],fg=clr[1];
    var sel=_ptSelSet.has(i);
    var over=cv.ratio!=null&&cv.ratio>1,warn=cv.ratio!=null&&cv.ratio>0.95&&cv.ratio<=1;
    var medBg=over?'background:#fdecea':warn?'background:#fef9e7':'background:'+bg;
    var ratioBg=over?'background:#fdecea;color:#c0392b;font-weight:bold':warn?'background:#fef9e7':'';
    var td='padding:3px 7px;border-bottom:1px solid rgba(0,0,0,.06);vertical-align:middle';
    body+='<tr data-idx="'+i+'" style="background:'+bg+'">'
      +'<td style="'+td+';text-align:center;width:28px"><input type="checkbox" '+(sel?'checked':'')+' onchange="_ptCheckRow('+i+',this.checked)" style="cursor:pointer"></td>'
      +'<td style="'+td+';text-align:center;width:44px;white-space:nowrap">'
        +(row.isUpm
          ? '<button onclick="_ptGoUpmDist('+i+')" title="Show distribution in main window" style="background:none;border:none;cursor:pointer;font-size:14px;padding:0 1px">&#128202;</button>'
          : '<button onclick="_ptShowDist('+i+',false)" title="Distribution" style="background:none;border:none;cursor:pointer;font-size:14px;padding:0 1px">&#128202;</button>'
            +'<button onclick="_ptShowDist('+i+',true)" title="UPM Distribution" style="background:none;border:none;cursor:pointer;font-size:14px;padding:0 1px">&#9889;</button>')
      +'</td>'
      +'<td style="'+td+';color:'+fg+';font-weight:bold;font-size:10px;text-align:center">'+esc(row.type)+'</td>'
      +'<td style="'+td+';color:'+fg+';font-size:10px">'+esc(row.cat)+'</td>'
      +'<td style="'+td+';font-weight:bold;border-left:3px solid '+fg+'">'+esc(row.dispName)+'</td>'
      +'<td style="'+td+';text-align:right;'+medBg+'">'+(cv.actual!=null?cv.actual.toFixed(2):'&#8212;')+'</td>'
      +'<td style="'+td+';text-align:right">'+(cv.tgt!=null?cv.tgt.toFixed(2):'&#8212;')+'</td>'
      +'<td style="'+td+';text-align:right;'+ratioBg+'">'+(cv.ratio!=null?cv.ratio.toFixed(2):'&#8212;')+'</td>'
      +'<td style="'+td+';text-align:right">'+(cv.upmMed!=null?cv.upmMed.toFixed(2):'&#8212;')+'</td>'
      +'<td style="'+td+';text-align:right">'+(cv.upmTgt!=null?cv.upmTgt.toFixed(2):'&#8212;')+'</td>'
      +'</tr>';
  });
  if(!body)body='<tr><td colspan="10" style="padding:14px;color:#7f8c8d;text-align:center">No data.</td></tr>';;
  bd.innerHTML=body;
}

function _ptSortVal(i,cv){
  var row=_ptRows[i];
  if(_ptSortCol==='type')return row.type;
  if(_ptSortCol==='cat')return row.cat;
  if(_ptSortCol==='test')return row.dispName;
  if(_ptSortCol==='actual')return cv.actual;
  if(_ptSortCol==='tgt')return cv.tgt;
  if(_ptSortCol==='ratio')return cv.ratio;
  if(_ptSortCol==='upm')return cv.upmMed;
  if(_ptSortCol==='upmtgt')return cv.upmTgt;
  return null;
}

/* Derive _ptSelSet entirely from SICC_CHECKED_ROWS — single source of truth */
function _ptSyncFromSicc(){
  if(typeof _siccAllRowKeys==='undefined'||typeof SICC_CHECKED_ROWS==='undefined')return;
  _ptRows.forEach(function(row,i){
    var anyChecked=_siccAllRowKeys.some(function(k){
      return k.indexOf(row.testName+'||')===0&&SICC_CHECKED_ROWS.has(k);
    });
    if(anyChecked)_ptSelSet.add(i);else _ptSelSet.delete(i);
  });
}
window._ptSyncFromSicc=_ptSyncFromSicc;
function _ptSort(col){
  if(_ptSortCol===col)_ptSortDir*=-1;
  else{_ptSortCol=col;_ptSortDir=1;}
  _ptRender();
}

function _ptFilter(q){
  _ptQuery=q;
  _ptRender();
}

function _ptToggleAll(checked){
  /* Update SEL_COLS and SICC_CHECKED_ROWS for all rows of active mode, then mirror back */
  var isCdyn=(typeof _siccScatterMode!=='undefined')&&_siccScatterMode==='cdyn';
  if(typeof _siccAllRowKeys!=='undefined'&&typeof SICC_CHECKED_ROWS!=='undefined'&&
     typeof SICC_SEL_COLS!=='undefined'&&typeof CDYN_SEL_COLS!=='undefined'){
    var sc=isCdyn?CDYN_SEL_COLS:SICC_SEL_COLS;
    _ptRows.forEach(function(row){
      if(row.isCdyn!==isCdyn)return;
      if(checked)sc.add(row.testName);else sc.delete(row.testName);
      var found=false;
      _siccAllRowKeys.forEach(function(k){
        if(k.indexOf(row.testName+'||')===0){
          found=true;
          if(checked)SICC_CHECKED_ROWS.add(k);else SICC_CHECKED_ROWS.delete(k);
        }
      });
      if(!found&&checked){
        var dk=row.testName+'||All';
        _siccAllRowKeys.push(dk);
        if(typeof _siccAllRowKeysSet!=='undefined')_siccAllRowKeysSet.add(dk);
        SICC_CHECKED_ROWS.add(dk);
      }
    });
  }
  _ptSyncFromSicc();
  if(!checked){
    /* Fast path for deselect-all: restyle all traces + target lines invisible.
       Do NOT call render_upm_dist (it would auto-repopulate SICC_SEL_COLS). */
    var _el=typeof _siccTraceIndexMap!=='undefined'&&document.getElementById('sicc-scatter-div');
    if(_el&&_el._spl&&typeof _siccTraceIndexMap!=='undefined'){
      var _allIdxs=[];
      Object.keys(_siccTraceIndexMap).forEach(function(k){_siccTraceIndexMap[k].forEach(function(i){_allIdxs.push(i);});});
      if(typeof _siccTargetTraceIndices!=='undefined')_siccTargetTraceIndices.forEach(function(i){_allIdxs.push(i);});
      if(_allIdxs.length)Plotly.restyle(_el,{visible:false},_allIdxs);
    }
    /* Clear the stats table — no rows should show when nothing is selected */
    var _bd=document.getElementById('sicc-stats-body');
    if(_bd){_bd.innerHTML='';}
    var _hcb=document.getElementById('sicc-sel-all');
    if(_hcb){_hcb.checked=false;_hcb.indeterminate=false;}
  }else{
    if(typeof render_upm_dist!=='undefined')render_upm_dist();
  }
  _ptRender();
}

function _ptCheckRow(i,checked){
  var row=_ptRows[i];if(!row)return;
  /* UPM rows: selecting switches to UPM distribution mode in main window */
  if(row.isUpm){
    if(checked&&typeof _setSiccScatterMode!=='undefined'&&typeof _setUpmMainCol!=='undefined'){
      _setUpmMainCol(row.testName);
      var sel=document.getElementById('upm-main-col-sel');
      if(sel){sel.value=row.testName;}
      _setSiccScatterMode('upm');
      var siccBtn=document.getElementById('btn-sicc');
      if(siccBtn)siccBtn.click();
    }
    if(checked)_ptSelSet.add(i);else _ptSelSet.delete(i);
    _ptRender();
    return;
  }
  var isCdyn=row.isCdyn;
  /* Add/remove from SEL_COLS so render_upm_dist picks it up */
  if(typeof SICC_SEL_COLS!=='undefined'&&typeof CDYN_SEL_COLS!=='undefined'){
    var sc=isCdyn?CDYN_SEL_COLS:SICC_SEL_COLS;
    if(checked)sc.add(row.testName);else sc.delete(row.testName);
  }
  /* Update SICC_CHECKED_ROWS for all known group keys */
  if(typeof _siccAllRowKeys!=='undefined'&&typeof SICC_CHECKED_ROWS!=='undefined'){
    var found=false;
    _siccAllRowKeys.forEach(function(k){
      if(k.indexOf(row.testName+'||')===0){
        found=true;
        if(checked)SICC_CHECKED_ROWS.add(k);else SICC_CHECKED_ROWS.delete(k);
      }
    });
    /* If no keys registered yet, pre-populate the default 'All' group key */
    if(!found&&checked){
      var dk=row.testName+'||All';
      _siccAllRowKeys.push(dk);
      if(typeof _siccAllRowKeysSet!=='undefined')_siccAllRowKeysSet.add(dk);
      SICC_CHECKED_ROWS.add(dk);
    }
  }
  _ptSyncFromSicc();
  if(typeof render_upm_dist!=='undefined')render_upm_dist();
}

/* ── Popup groupby controls ─────────────────────────────────────────── */
function _ptGroupByHTML(){
  var opts=['program','lot','wafer','material'];
  var none=_ptPopupGroupBy.length===0;
  var html='<div style="margin:0 0 8px;font-size:11px;color:#555;border-bottom:1px solid #eee;padding-bottom:6px;display:flex;flex-wrap:wrap;align-items:center;gap:6px">'
    +'<span style="font-weight:600;margin-right:2px">Group by:</span>'
    +'<label style="cursor:pointer"><input type="checkbox" class="pt-popup-gb" value="none"'+(none?' checked':'')+' onchange="_togglePopupGroup(&apos;none&apos;)"> None</label>';
  opts.forEach(function(o){
    var chk=_ptPopupGroupBy.indexOf(o)>=0?' checked':'';
    html+='<label style="cursor:pointer"><input type="checkbox" class="pt-popup-gb" value="'+o+'"'+chk+' onchange="_togglePopupGroup(&apos;'+o+'&apos;)"> '+o.charAt(0).toUpperCase()+o.slice(1)+'</label>';
  });
  html+='</div>';
  return html;
}
function _togglePopupGroup(field){
  if(field==='none'){_ptPopupGroupBy=[];}
  else{var idx=_ptPopupGroupBy.indexOf(field);if(idx>=0)_ptPopupGroupBy.splice(idx,1);else _ptPopupGroupBy.push(field);}
  document.querySelectorAll('.pt-popup-gb').forEach(function(cb){
    if(cb.value==='none')cb.checked=_ptPopupGroupBy.length===0;
    else cb.checked=_ptPopupGroupBy.indexOf(cb.value)>=0;
  });
  _ptRefreshModal();
}
window._togglePopupGroup=_togglePopupGroup;
/* ── Re-render dist histogram + aggregate stats for given active set ─────── */
function _ptRedrawHistModal(active,testName,isCdyn){
  if(typeof _renderSiccHistOnly==='undefined')return;
  var _o='upm-hist-svg',_on='upm-chart-note',_os='upm-stats-tbl',_ot='sicc-dist-title';
  var h=document.getElementById(_o),hn=document.getElementById(_on),hs=document.getElementById(_os),ht=document.getElementById(_ot);
  var mh=document.getElementById('pt-modal-hist-svg'),mhn=document.getElementById('pt-modal-chart-note'),mhs=document.getElementById('pt-modal-stats-tbl'),mht=document.getElementById('pt-modal-dist-title');
  if(h)h.id='_ph_hist';if(hn)hn.id='_ph_note';if(hs)hs.id='_ph_stbl';if(ht)ht.id='_ph_ttl';
  if(mh)mh.id=_o;if(mhn)mhn.id=_on;if(mhs)mhs.id=_os;if(mht)mht.id=_ot;
  _renderSiccHistOnly(active,testName,isCdyn);
  if(mh)mh.id='pt-modal-hist-svg';if(mhn)mhn.id='pt-modal-chart-note';if(mhs)mhs.id='pt-modal-stats-tbl';if(mht)mht.id='pt-modal-dist-title';
  if(h)h.id=_o;if(hn)hn.id=_on;if(hs)hs.id=_os;if(ht)ht.id=_ot;
  /* Inject full UPM stats columns */
  var _stEl=document.getElementById('pt-modal-stats-tbl');
  if(!_stEl)return;
  var _tbl=_stEl.querySelector('table');
  if(!_tbl)return;
  _tbl.style.width='auto';
  var _extra=_stEl.querySelector('div');if(_extra)_extra.remove();
  var _extra2=_stEl.querySelector('table:nth-of-type(2)');if(_extra2){var _p2=_extra2.parentNode;if(_p2)_p2.remove();}
  var _uCol=_getUpmCol(testName);if(!_uCol)return;
  var _uVals=[];
  active.forEach(function(i){var dp=ROWS[i].die_pairs&&ROWS[i].die_pairs[testName];if(dp&&dp.u)dp.u.forEach(function(v){if(v!=null&&!isNaN(v))_uVals.push(v);});});
  var _uSt=computeStats(_uVals);if(!_uSt)return;
  var _thBase='padding:6px 10px;font-size:11px;font-weight:600;text-align:center;letter-spacing:0.04em;white-space:nowrap;border-right:1px solid #a04000';
  var _thU=_thBase+';background:#c0650a;color:#fff;font-weight:700';
  var _thUM=_thBase+';background:#9a3412;color:#fff;font-weight:700';
  var _tdBase='padding:6px 10px;font-size:12px;text-align:center;white-space:nowrap;border-right:1px solid #f5d5b0;color:#7a3800';
  var _tdUM='padding:6px 10px;font-size:13px;font-weight:700;text-align:center;white-space:nowrap;color:#c0650a;background:#fff8f0;border-right:1px solid #f5d5b0';
  var uCols=[
    {l:'N (UPM)',v:_uSt.count.toLocaleString(),th:_thU,td:_tdBase},
    {l:'Min UPM%',v:_uSt.min.toFixed(2)+'%',th:_thU,td:_tdBase},
    {l:'Med UPM%',v:_uSt.median.toFixed(2)+'%',th:_thUM,td:_tdUM},
    {l:'Mean UPM%',v:_uSt.mean.toFixed(2)+'%',th:_thU,td:_tdBase},
    {l:'Max UPM%',v:_uSt.max.toFixed(2)+'%',th:_thU,td:_tdBase},
    {l:'SD UPM%',v:_uSt.stddev.toFixed(2)+'%',th:_thU,td:_tdBase}
  ];
  var _hRow=_tbl.querySelector('thead tr'),_dRow=_tbl.querySelector('tbody tr');
  uCols.forEach(function(c){
    if(_hRow){var _th=document.createElement('th');_th.setAttribute('style',c.th);_th.textContent=c.l;_hRow.appendChild(_th);}
    if(_dRow){var _td=document.createElement('td');_td.setAttribute('style',c.td);_td.textContent=c.v;_dRow.appendChild(_td);}
  });
}
window._ptRedrawHistModal=_ptRedrawHistModal;
/* Shared: filter active by checked groups and redraw */
function _ptApplyGroupFilter(){
  var modal=document.getElementById('pt-dist-modal');
  if(!modal)return;
  var rowIdx=modal._ptRowIdx;
  if(rowIdx==null||!_ptRows[rowIdx])return;
  var testName=_ptRows[rowIdx].testName,isCdyn=_ptRows[rowIdx].isCdyn;
  var allActive=modal._ptActive;
  if(!allActive||!allActive.length)return;
  var checkedGroups=new Set();
  var tbl=document.querySelector('#pt-modal-group-stats table');
  var totalRows=tbl?tbl.querySelectorAll('tbody tr').length:0;
  if(tbl){tbl.querySelectorAll('tbody tr').forEach(function(row){
    var cbEl=row.querySelector('input[type=checkbox]');
    var gkEl=row.querySelector('td:nth-child(2)');
    if(cbEl&&gkEl&&cbEl.checked)checkedGroups.add(gkEl.textContent.trim());
  });}
  /* Sync header checkbox state */
  var hCb=tbl&&tbl.querySelector('thead input[type=checkbox]');
  if(hCb){hCb.checked=checkedGroups.size===totalRows;hCb.indeterminate=checkedGroups.size>0&&checkedGroups.size<totalRows;}
  var filteredActive=allActive;
  if(checkedGroups.size<totalRows&&checkedGroups.size>0){
    filteredActive=allActive.filter(function(i){return checkedGroups.has(_ptPopupGroupKey(ROWS[i]));});
    if(!filteredActive.length)filteredActive=allActive;
  }
  if(modal._ptShowUpm){
    /* ⚡ UPM popup */
    if(checkedGroups.size===0){
      var _svg=document.getElementById('pt-modal-upm-svg');
      if(_svg)_svg.innerHTML='<text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle" font-size="14" fill="#aaa">No groups selected</text>';
      var _note=document.getElementById('pt-modal-upm-note');if(_note)_note.textContent='';
      var _st=document.getElementById('pt-modal-upm-stats');if(_st)_st.innerHTML='';
      return;
    }
    if(typeof drawMiniUpm!=='undefined')drawMiniUpm(filteredActive,testName,isCdyn,'pt-modal-upm-svg','pt-dist-modal-title','pt-modal-upm-note');
    var _uVals=[];
    filteredActive.forEach(function(i){var dp=ROWS[i].die_pairs&&ROWS[i].die_pairs[testName];if(dp&&dp.u)dp.u.forEach(function(v){if(v!=null&&!isNaN(v))_uVals.push(v);});});
    if(typeof renderStatsTable!=='undefined')renderStatsTable(computeStats(_uVals),'pt-modal-upm-stats',2);
  }else{
    /* 📊 Distribution popup */
    if(checkedGroups.size===0){
      var _svg=document.getElementById('pt-modal-hist-svg');
      if(_svg)_svg.innerHTML='<text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle" font-size="14" fill="#aaa">No groups selected</text>';
      var _note=document.getElementById('pt-modal-chart-note');if(_note)_note.textContent='';
      var _st=document.getElementById('pt-modal-stats-tbl');if(_st)_st.innerHTML='';
      return;
    }
    _ptRedrawHistModal(filteredActive,testName,isCdyn);
  }
}
window._ptApplyGroupFilter=_ptApplyGroupFilter;
function _ptToggleGroupRow(cb){
  var tr=cb.closest('tr');
  if(!tr)return;
  tr.style.opacity=cb.checked?'1':'0.25';
  tr.querySelectorAll('td:not(:first-child)').forEach(function(c){
    c.style.textDecoration=cb.checked?'none':'line-through';
  });
  _ptApplyGroupFilter();
}
window._ptToggleGroupRow=_ptToggleGroupRow;
function _ptToggleAllGroupRows(cb){
  var tbl=document.querySelector('#pt-modal-group-stats table');
  if(!tbl)return;
  tbl.querySelectorAll('tbody tr').forEach(function(row){
    var cbEl=row.querySelector('input[type=checkbox]');
    if(!cbEl)return;
    cbEl.checked=cb.checked;
    row.style.opacity=cb.checked?'1':'0.25';
    row.querySelectorAll('td:not(:first-child)').forEach(function(c){
      c.style.textDecoration=cb.checked?'none':'line-through';
    });
  });
  _ptApplyGroupFilter();
}
window._ptToggleAllGroupRows=_ptToggleAllGroupRows;
function _ptPopupGroupKey(r){
  var parts=[];
  if(_ptPopupGroupBy.indexOf('program')>=0)parts.push(r.program||'');
  if(_ptPopupGroupBy.indexOf('lot')>=0)parts.push(r.lot||'');
  if(_ptPopupGroupBy.indexOf('wafer')>=0)parts.push(r.wafer||'');
  if(_ptPopupGroupBy.indexOf('material')>=0)parts.push(r.material||'');
  return parts.length?parts.join(' | '):'All';
}
function _ptRenderGroupStats(active,testName,isCdyn,showUpm,containerId){
  var el=document.getElementById(containerId);
  if(!el){return;}
  if(!active.length){el.innerHTML='';return;}
  /* Collect per-group SICC/CDYN values and (for dist mode) per-group UPM values */
  var groupMap={},upmMap={},groupOrder=[];
  var uCol=(!showUpm)?_getUpmCol(testName):null;
  active.forEach(function(i){
    var r=ROWS[i];
    var gk=_ptPopupGroupKey(r);
    if(!groupMap[gk]){groupMap[gk]=[];upmMap[gk]=[];groupOrder.push(gk);}
    var dp=r.die_pairs&&r.die_pairs[testName];
    if(showUpm){
      if(dp&&dp.u)dp.u.forEach(function(v){if(v!=null&&!isNaN(v))groupMap[gk].push(v);});
    }else{
      if(dp&&dp.s)dp.s.forEach(function(v){if(v!=null&&!isNaN(v)&&v>0)groupMap[gk].push(v);});
      if(uCol&&dp&&dp.u)dp.u.forEach(function(v){if(v!=null&&!isNaN(v))upmMap[gk].push(v);});
    }
  });
  /* Skip if no data */
  var nonEmpty=groupOrder.filter(function(g){return groupMap[g].length>0;});
  if(!nonEmpty.length){el.innerHTML='';return;}
  var hasUpm=!showUpm&&uCol&&nonEmpty.some(function(g){return upmMap[g].length>0;});
  var _pal=['#3498db','#27ae60','#e67e22','#9b59b6','#e74c3c','#1abc9c','#f39c12','#2980b9','#c0392b','#16a085'];
  var _th='padding:5px 8px;font-size:10px;font-weight:600;background:#2c3e50;color:#ecf0f1;text-align:center;white-space:nowrap;border-right:1px solid #3d5166';
  var _thHL='padding:5px 8px;font-size:10px;font-weight:700;background:#1a4a7a;color:#fff;text-align:center;white-space:nowrap;border-right:1px solid #3d5166';
  var _thU='padding:5px 8px;font-size:10px;font-weight:600;background:#c0650a;color:#fff;text-align:center;white-space:nowrap;border-right:1px solid #a04000';
  var _thUM='padding:5px 8px;font-size:10px;font-weight:700;background:#9a3412;color:#fff;text-align:center;white-space:nowrap;border-right:1px solid #a04000';
  var dec=showUpm?2:4;
  var fv=function(v){return v!=null?v.toFixed(dec):'--';};
  var label=showUpm?'Per-Group Stats (UPM %)':'Per-Group Stats';
  var upmHdrs=hasUpm
    ?'<th style="'+_thU+'">N (UPM)</th>'
     +'<th style="'+_thU+'">Min UPM%</th>'
     +'<th style="'+_thUM+'">Med UPM%</th>'
     +'<th style="'+_thU+'">Mean UPM%</th>'
     +'<th style="'+_thU+'">Max UPM%</th>'
     +'<th style="'+_thU+';border-right:none">SD UPM%</th>'
    :'';
  var _thCb='padding:5px 4px;font-size:10px;font-weight:600;background:#2c3e50;color:#ecf0f1;text-align:center;white-space:nowrap;border-right:1px solid #3d5166;width:24px';
  var html='<div style="margin-top:10px;font-size:11px;font-weight:bold;color:#2c3e50">'+label+'</div>'
    +'<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;margin-top:4px;font-size:11px">'
    +'<thead><tr>'
    +'<th style="'+_thCb+'"><input type="checkbox" checked title="Select / deselect all" onchange="_ptToggleAllGroupRows(this)" style="cursor:pointer"></th>'
    +'<th style="'+_th+';text-align:left">Group</th>'
    +'<th style="'+_th+'">N (dies)</th>'
    +'<th style="'+_th+'">Min</th>'
    +'<th style="'+_thHL+'">Median</th>'
    +'<th style="'+_th+'">Mean</th>'
    +'<th style="'+_th+'">Max</th>'
    +'<th style="'+_th+(hasUpm?'':';border-right:none')+'">Std Dev</th>'
    +upmHdrs
    +'</tr></thead><tbody>';
  nonEmpty.forEach(function(gk,gi){
    var st=computeStats(groupMap[gk]);
    if(!st)return;
    var clr=_pal[gi%_pal.length];
    var suf=showUpm?'%':'';
    var _uSt=hasUpm?computeStats(upmMap[gk]):null;
    var _tdU='padding:4px 8px;border-bottom:1px solid #eee;text-align:right;color:#7a3800';
    var _tdUM='padding:4px 8px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;color:#c0650a;background:#fff8f0';
    html+='<tr>'
      +'<td style="padding:2px 4px;border-bottom:1px solid #eee;text-align:center"><input type="checkbox" checked title="Hide/show this row" onchange="_ptToggleGroupRow(this)" style="cursor:pointer"></td>'
      +'<td style="padding:4px 8px;border-left:3px solid '+clr+';border-bottom:1px solid #eee;font-weight:bold;white-space:nowrap">'+esc(gk)+'</td>'
      +'<td style="padding:4px 8px;border-bottom:1px solid #eee;text-align:right">'+st.count.toLocaleString()+'</td>'
      +'<td style="padding:4px 8px;border-bottom:1px solid #eee;text-align:right">'+fv(st.min)+suf+'</td>'
      +'<td style="padding:4px 8px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;color:#1a4a7a;background:#eef6ff">'+fv(st.median)+suf+'</td>'
      +'<td style="padding:4px 8px;border-bottom:1px solid #eee;text-align:right">'+fv(st.mean)+suf+'</td>'
      +'<td style="padding:4px 8px;border-bottom:1px solid #eee;text-align:right">'+fv(st.max)+suf+'</td>'
      +'<td style="padding:4px 8px;border-bottom:1px solid #eee;text-align:right'+(hasUpm?'':';border-right:none')+'">'+fv(st.stddev)+suf+'</td>'
      +(_uSt
        ?'<td style="'+_tdU+'">'+_uSt.count.toLocaleString()+'</td>'
         +'<td style="'+_tdU+'">'+_uSt.min.toFixed(2)+'%</td>'
         +'<td style="'+_tdUM+'">'+_uSt.median.toFixed(2)+'%</td>'
         +'<td style="'+_tdU+'">'+_uSt.mean.toFixed(2)+'%</td>'
         +'<td style="'+_tdU+'">'+_uSt.max.toFixed(2)+'%</td>'
         +'<td style="'+_tdU+';border-right:none">'+_uSt.stddev.toFixed(2)+'%</td>'
        :'')
      +'</tr>';
  });
  html+='</tbody></table></div>';
  el.innerHTML=html;
}
window._ptRenderGroupStats=_ptRenderGroupStats;

function _ptCloseModal(){var m=document.getElementById('pt-dist-modal');if(m)m.style.display='none';}
window._ptCloseModal=_ptCloseModal;
function _ptShowDist(rowIdx,showUpm){
  var row=_ptRows[rowIdx];
  if(!row)return;
  var testName=row.testName,isCdyn=row.isCdyn;
  var ai=getFiltered();var active=ai.filter(function(i){return SEL_WFR.has(i);});
  if(!active.length)active=ai;
  /* Build/show floating modal */
  var modal=document.getElementById('pt-dist-modal');
  if(!modal){
    modal=document.createElement('div');
    modal.id='pt-dist-modal';
    modal.style.cssText='position:fixed;top:60px;right:20px;width:620px;height:520px;min-width:320px;min-height:260px;max-width:95vw;max-height:92vh;background:#fff;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.35);z-index:9999;display:flex;flex-direction:column;overflow:hidden;resize:both';
    modal.innerHTML='<div id="pt-dist-modal-header" style="display:flex;justify-content:space-between;align-items:center;padding:8px 14px;background:#2c3e50;color:#fff;flex-shrink:0;cursor:grab;user-select:none">'
        +'<span id="pt-dist-modal-title" style="font-size:13px;font-weight:bold"></span>'
        +'<button onclick="_ptCloseModal()" style="background:none;border:none;color:#fff;font-size:18px;cursor:pointer;padding:0 4px">&#10005;</button>'
      +'</div>'
      +'<div style="padding:12px;overflow:auto;flex:1;display:flex;flex-direction:column;min-height:0">'
        +'<div id="pt-dist-modal-content" style="flex:1;display:flex;flex-direction:column;min-height:0"></div>'
      +'</div>';
    document.body.appendChild(modal);
    /* Drag logic */
    (function(){
      var hdr=document.getElementById('pt-dist-modal-header');
      var dx=0,dy=0,mx=0,my=0;
      hdr.addEventListener('mousedown',function(e){
        if(e.target.tagName==='BUTTON')return;
        e.preventDefault();
        mx=e.clientX;my=e.clientY;
        document.addEventListener('mousemove',onDrag);
        document.addEventListener('mouseup',onUp);
        hdr.style.cursor='grabbing';
      });
      function onDrag(e){
        dx=e.clientX-mx;dy=e.clientY-my;mx=e.clientX;my=e.clientY;
        modal.style.left=(modal.offsetLeft+dx)+'px';
        modal.style.top=(modal.offsetTop+dy)+'px';
        modal.style.right='auto';
      }
      function onUp(){document.removeEventListener('mousemove',onDrag);document.removeEventListener('mouseup',onUp);hdr.style.cursor='grab';}
    })();
  }
  window.pt_modal_id='pt-dist-modal';
  modal._ptRowIdx=rowIdx;
  modal._ptShowUpm=showUpm;
  var titleEl=document.getElementById('pt-dist-modal-title');
  if(titleEl)titleEl.textContent=(showUpm?'UPM Distribution':'Distribution')+': '+testName;
  /* Inject SVG container into modal */
  var content=document.getElementById('pt-dist-modal-content');
  if(showUpm){
    content.innerHTML=_ptGroupByHTML()
      +'<svg id="pt-modal-upm-svg" style="width:100%;flex:1;min-height:0;display:block;border:1px solid #f5e0c3;border-radius:4px;background:#fffaf4"></svg>'
      +'<div id="pt-modal-upm-note" style="font-size:10px;color:#c0650a;margin-top:3px"></div>'
      +'<div id="pt-modal-upm-stats" style="margin-top:6px"></div>'
      +'<div id="pt-modal-group-stats"></div>';
    modal.style.display='flex';
    modal._ptActive=active;
    if(typeof drawMiniUpm!=='undefined')drawMiniUpm(active,testName,isCdyn,'pt-modal-upm-svg','pt-dist-modal-title','pt-modal-upm-note');
    /* Overwrite basic stats with XY-style renderStatsTable */
    var _allU=[];
    active.forEach(function(i){var dp=ROWS[i].die_pairs&&ROWS[i].die_pairs[testName];if(dp&&dp.u)dp.u.forEach(function(v){if(v!=null&&!isNaN(v))_allU.push(v);});});
    if(typeof renderStatsTable!=='undefined')renderStatsTable(computeStats(_allU),'pt-modal-upm-stats',2);
    /* Per-group stats */
    _ptRenderGroupStats(active,testName,isCdyn,true,'pt-modal-group-stats');
  }else{
    content.innerHTML=_ptGroupByHTML()
      +'<h3 id="pt-modal-dist-title" style="font-size:12px;color:#2c3e50;margin:0 0 4px"></h3>'
      +'<svg id="pt-modal-hist-svg" style="width:100%;flex:1;min-height:0;display:block;border:1px solid #eee;border-radius:4px;background:#fff"></svg>'
      +'<div id="pt-modal-chart-note" style="font-size:11px;color:#7f8c8d;margin-top:4px"></div>'
      +'<div id="pt-modal-stats-tbl" style="margin-top:8px"></div>'
      +'<div id="pt-modal-group-stats"></div>';
    modal.style.display='flex';
    modal._ptActive=active;
    _ptRedrawHistModal(active,testName,isCdyn);
    /* Per-group stats */
    _ptRenderGroupStats(active,testName,isCdyn,false,'pt-modal-group-stats');
  }
}
window._ptShowDist=_ptShowDist;
/* Navigate to UPM distribution in main window (instead of popup) */
function _ptGoUpmDist(rowIdx){
  var row=_ptRows[rowIdx];
  if(!row||!row.isUpm)return;
  /* Switch to UPM mode and select the column */
  if(typeof _setSiccScatterMode!=='undefined'&&typeof _setUpmMainCol!=='undefined'){
    _setUpmMainCol(row.testName);
    /* Sync the <select> element */
    var sel=document.getElementById('upm-main-col-sel');
    if(sel){sel.value=row.testName;}
    _setSiccScatterMode('upm');
  }
  /* Switch to Parametric Analysis tab */
  var siccBtn=document.getElementById('btn-sicc');
  if(siccBtn)siccBtn.click();
}
window._ptGoUpmDist=_ptGoUpmDist;
/* Export parameter table as CSV */
function _ptExportCsv(){
  if(!_ptRows.length)_ptBuildRows();
  var ai=SEL_WFR.size>0?Array.from(SEL_WFR):getFiltered();
  var computed=_ptRows.map(function(r){return _ptComputeRow(r,ai);});
  var q=_ptQuery.toLowerCase();
  var hdrs=['Type','Category','Parameter','Median','Target','Ratio','UPM%','UPM_Target','Checked'];
  var lines=[hdrs.join(',')];
  _ptRows.forEach(function(row,i){
    if(q&&(row.dispName+row.cat+row.type+row.testName).toLowerCase().indexOf(q)<0)return;
    var cv=computed[i];
    var checked=_ptSelSet.has(i)?'1':'0';
    var vals=[row.type,row.cat,row.dispName,
      cv.actual!=null?cv.actual.toFixed(6):'',
      cv.tgt!=null?cv.tgt.toFixed(6):'',
      cv.ratio!=null?cv.ratio.toFixed(4):'',
      cv.upmMed!=null?cv.upmMed.toFixed(4):'',
      cv.upmTgt!=null?cv.upmTgt.toFixed(4):'',
      checked];
    lines.push(vals.map(function(v){var s=String(v);return s.indexOf(',')>=0||s.indexOf('"')>=0?'"'+s.replace(/"/g,'""')+'"':s;}).join(','));
  });
  var blob=new Blob([lines.join(String.fromCharCode(13,10))],{type:'text/csv'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='parameter_table.csv';document.body.appendChild(a);a.click();
  setTimeout(function(){document.body.removeChild(a);URL.revokeObjectURL(a.href);},100);
}
window._ptExportCsv=_ptExportCsv;
/* Refresh the open dist/upm popup when lot/wafer selection changes */
function _ptRefreshModal(){
  var modal=document.getElementById('pt-dist-modal');
  if(!modal||modal.style.display==='none')return;
  var rowIdx=modal._ptRowIdx;
  var showUpm=modal._ptShowUpm;
  if(rowIdx==null||!_ptRows[rowIdx])return;
  _ptShowDist(rowIdx,showUpm);
}
window._ptRefreshModal=_ptRefreshModal;
window._ptSort=_ptSort;
window._ptFilter=_ptFilter;
window._ptToggleAll=_ptToggleAll;
function _ptHdrClick(cb){
  /* onmousedown captured the pre-click indeterminate state;
     if it was indeterminate ('-'), treat click as deselect-all regardless of
     what the browser set checked to. */
  var deselect=cb._wasIndet===true||!cb.checked;
  cb.indeterminate=false;
  cb.checked=!deselect;
  delete cb._wasIndet;
  _ptToggleAll(!deselect);
}
window._ptHdrClick=_ptHdrClick;
window._ptCheckRow=_ptCheckRow;

function toggleSummPanel(){
  var panel=document.getElementById('all-med-panel');
  var btn=document.getElementById('all-med-toggle-btn');
  if(!panel)return;
  var open=panel.classList.contains('open');
  if(open){
    panel._savedW=panel.getBoundingClientRect().width;
    panel.style.flex='';panel.style.width='';
    panel.classList.remove('open');
    if(btn)btn.innerHTML='&#9654;';
  }else{
    panel.classList.add('open');
    var w=panel._savedW||480;
    panel.style.flex='0 0 '+w+'px';panel.style.width=w+'px';
    if(btn)btn.innerHTML='&#9664;';
    render_summ();
  }
}
function render_summ_if_open(){
  var panel=document.getElementById('all-med-panel');
  if(panel&&panel.classList.contains('open'))render_summ();
}
function render_summ(){
  if(!_ptRows.length)_ptBuildRows();
  _ptSyncFromSicc(); /* always mirror SICC_CHECKED_ROWS before rendering */
  _ptRender();
}
window.toggleSummPanel=toggleSummPanel;
'''

    return '''
function toggleSummPanel(){
  var panel=document.getElementById('all-med-panel');
  var btn=document.getElementById('all-med-toggle-btn');
  if(!panel)return;
  var open=panel.classList.contains('open');
  if(open){
    panel._savedW=panel.getBoundingClientRect().width;
    panel.style.flex='';panel.style.width='';
    panel.classList.remove('open');
    if(btn)btn.innerHTML='&#9654;';
  }else{
    panel.classList.add('open');
    var w=panel._savedW||420;
    panel.style.flex='0 0 '+w+'px';panel.style.width=w+'px';
    if(btn)btn.innerHTML='&#9664;';
    render_summ();
  }
}
function render_summ_if_open(){
  var panel=document.getElementById('all-med-panel');
  if(panel&&panel.classList.contains('open'))render_summ();
}
function render_summ(){
  var ai=SEL_WFR.size>0?Array.from(SEL_WFR):getFiltered();
  _renderCatTable(SICC_TBL_CFG,ai,false,'sicc-cat-head','sicc-cat-body','sicc-cat-legend',SUMM_SICC_OFF);
  _renderCatTable(CDYN_TBL_CFG,ai,true,'cdyn-cat-head','cdyn-cat-body','cdyn-cat-legend',SUMM_CDYN_OFF);
}

function _renderCatTable(cfg,ai,isCdyn,headId,bodyId,legendId,offSet){
  var headEl=document.getElementById(headId);
  var bodyEl=document.getElementById(bodyId);
  var legEl =document.getElementById(legendId);
  if(!cfg||!cfg.length){
    if(headEl)headEl.innerHTML='';
    if(bodyEl)bodyEl.innerHTML='<tr><td colspan="5" style="padding:14px;color:#7f8c8d">No table config defined.</td></tr>';
    if(legEl)legEl.innerHTML='';
    return;
  }
  var catOrder=[],catSet=new Set();
  cfg.forEach(function(row){
    if(!catSet.has(row[0])){catSet.add(row[0]);catOrder.push(row[0]);}
  });
  if(legEl) _buildCatLegend(catOrder,offSet,legendId,render_summ);
  var hdr='<tr><th style="text-align:left;min-width:160px">Test</th><th>Cat</th><th>Median</th><th>Target</th><th>Ratio</th><th>UPM%</th><th>UPM Tgt</th></tr>';
  if(headEl)headEl.innerHTML=hdr;
  var body='',lastCat='';
  cfg.forEach(function(row){
    var cat=row[0],dispName=row[1],testName=row[2],upmCol=row[3];
    if(offSet&&offSet.has(cat))return;
    if(cat!==lastCat){
      body+='<tr class="cat-hdr"><td colspan="7" style="background:'+_catColor(cat)+';color:'+_catBorder(cat)+';border-left:4px solid '+_catBorder(cat)+'">'+esc(cat)+'</td></tr>';
      lastCat=cat;
    }
    var actual=null,tgt=null,ratio=null,upmMed=null,upmTgt=null;
    if(isCdyn){
      var vals=ai.map(function(i){return ROWS[i].cdyn[testName];}).filter(function(v){return v!=null&&!isNaN(v);});
      actual=medArr(vals); tgt=CDYN_TARGETS[testName]||null;
    }else{
      var vals=ai.map(function(i){return ROWS[i].medians[testName];}).filter(function(v){return v!=null&&!isNaN(v);});
      actual=medArr(vals); tgt=TARGETS[testName.toUpperCase()]||null;
    }
    ratio=(actual!=null&&tgt!=null&&tgt!==0)?actual/tgt:null;
    if(upmCol){
      var uv=ai.map(function(i){return ROWS[i].medians[upmCol];}).filter(function(v){return v!=null&&!isNaN(v);});
      upmMed=medArr(uv); upmTgt=TARGETS[upmCol.toUpperCase()]||null;
    }
    var bg=_catColor(cat);
    body+='<tr style="background:'+bg+'">';
    body+='<td style="text-align:left;border-left:4px solid '+_catBorder(cat)+'">'+esc(dispName)+'</td>';
    body+='<td style="color:#7f8c8d;font-size:10px;text-align:center">'+esc(cat)+'</td>';
    body+='<td class="'+ccls(actual,tgt,isCdyn)+'">'+(actual!=null?actual.toFixed(2):'&#8212;')+'</td>';
    body+='<td class="tgt">'+(tgt!=null?tgt.toFixed(2):'&#8212;')+'</td>';
    body+='<td class="'+ratioCls(ratio)+'">'+(ratio!=null?ratio.toFixed(2):'&#8212;')+'</td>';
    body+='<td class="'+upmCls(upmMed,upmTgt)+'">'+(upmMed!=null?upmMed.toFixed(2):'&#8212;')+'</td>';
    body+='<td class="tgt">'+(upmTgt!=null?upmTgt.toFixed(2):'&#8212;')+'</td>';
    body+='</tr>';
  });
  if(bodyEl)bodyEl.innerHTML=body;
}
window.toggleSummPanel=toggleSummPanel;
'''

# ════════════════════════════════════════════════════════════════
# SICC / Parametric Analysis tab  (formerly _tab_sicc.py)
# ════════════════════════════════════════════════════════════════



def _sicc_tab_html() -> str:
    return f'''
<div id="tab-sicc" class="tab-panel active">
  <div style="display:flex;align-items:center;gap:10px;padding:6px 10px;background:#f8f9fa;border-bottom:1px solid #dde;flex-wrap:wrap">
    <div style="display:flex;align-items:center;gap:6px">
      <button id="sicc-xy-sicc-btn" onclick="_setSiccScatterMode(\'sicc\')" style="padding:7px 14px;font-size:13px;font-weight:bold;border:2px solid #2980b9;border-radius:5px;background:#2980b9;color:#fff;cursor:pointer;white-space:nowrap">&#128202; SICC</button>
      <div id="sicc-col-panel" style="position:relative;display:inline-block"></div>
    </div>
    <div style="display:flex;align-items:center;gap:6px">
      <button id="sicc-xy-cdyn-btn" onclick="_setSiccScatterMode(\'cdyn\')" style="padding:7px 14px;font-size:13px;font-weight:bold;border:2px solid #27ae60;border-radius:5px;background:#ecf0f1;color:#27ae60;cursor:pointer;white-space:nowrap">&#128200; CDYN</button>
      <div id="cdyn-col-panel" style="position:relative;display:none"></div>
      <a href="https://intel.sharepoint.com/:x:/r/sites/ftesdsexecution/_layouts/15/Doc.aspx?sourcedoc=%7BB2A0D111-751C-4EEE-9F65-A43F2AC6D12F%7D&file=NVL816_CDIE-N2P_PreSi_summary.xlsx&action=default&mobileredirect=true" target="_blank" rel="noopener noreferrer" style="font-size:12px;color:#2980b9;text-decoration:underline;white-space:nowrap;margin-left:4px">SICC/CDYN SPEC &#128196;</a>
    </div>    <div style="display:flex;align-items:center;gap:6px">
      <button id="sicc-xy-upm-btn" onclick="_setSiccScatterMode('upm')" style="padding:7px 14px;font-size:13px;font-weight:bold;border:2px solid #e67e22;border-radius:5px;background:#ecf0f1;color:#e67e22;cursor:pointer;white-space:nowrap">&#128300; UPM</button>
      <select id="upm-main-col-sel" onchange="_setUpmMainCol(this.value)" style="font-size:12px;padding:4px 8px;border:1px solid #bbb;border-radius:4px;display:none"><option value="">Select UPM…</option></select>
    </div>  </div>
  <!-- Plot controls bar -->
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:5px 10px;background:#f0f4f8;border-bottom:1px solid #dde;font-size:12px">
    <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
      <input type="checkbox" id="sicc-ylog-cb" onchange="_setSiccYLog(this.checked)"> Y: Log scale
    </label>
    <span style="color:#aaa">|</span>
    <span style="font-weight:bold;color:#555">Trend:</span>
    <label style="display:flex;align-items:center;gap:3px;cursor:pointer"><input type="radio" name="sicc-trend" value="ols" checked onchange="_setSiccTrend(\'ols\')"> OLS</label>
    <label style="display:flex;align-items:center;gap:3px;cursor:pointer"><input type="radio" name="sicc-trend" value="ts" onchange="_setSiccTrend(\'ts\')"> Theil-Sen</label>
    <label style="display:flex;align-items:center;gap:3px;cursor:pointer"><input type="radio" name="sicc-trend" value="none" onchange="_setSiccTrend(\'none\')"> None</label>
    <span style="color:#aaa">|</span>
    <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
      <input type="checkbox" id="sicc-crosshair-cb" onchange="_setSiccCrosshair(this.checked)"> Crosshair cursor
    </label>
    <span style="color:#aaa">|</span>
    <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
      <input type="checkbox" id="sicc-show-target-cb" onchange="_setSiccShowTarget(this.checked)"> Show target line
    </label>
    <span style="color:#aaa">|</span>
    <button onclick="_siccExportXyCsv()" title="Download XY chart data as CSV" style="font-size:12px;padding:3px 7px;border:1px solid #bbb;border-radius:3px;background:#fff;cursor:pointer;white-space:nowrap">&#11015; XY CSV</button>
    <span style="color:#aaa">|</span>
    <span style="font-weight:bold;color:#555">X:</span>
    <input id="sicc-xmin" type="number" placeholder="min" title="X-axis minimum (leave blank for auto)" oninput="render_upm_dist()" style="width:62px;font-size:11px;padding:2px 4px;border:1px solid #bbb;border-radius:3px">
    <input id="sicc-xmax" type="number" placeholder="max" title="X-axis maximum (leave blank for auto)" oninput="render_upm_dist()" style="width:62px;font-size:11px;padding:2px 4px;border:1px solid #bbb;border-radius:3px">
    <span style="font-weight:bold;color:#555">Y:</span>
    <input id="sicc-ymin" type="number" placeholder="min" title="Y-axis minimum (leave blank for auto)" oninput="render_upm_dist()" style="width:62px;font-size:11px;padding:2px 4px;border:1px solid #bbb;border-radius:3px">
    <input id="sicc-ymax" type="number" placeholder="max" title="Y-axis maximum (leave blank for auto)" oninput="render_upm_dist()" style="width:62px;font-size:11px;padding:2px 4px;border:1px solid #bbb;border-radius:3px">
    <button onclick="_siccResetAxisRange()" title="Reset axis ranges to auto" style="font-size:11px;padding:2px 6px;border:1px solid #bbb;border-radius:3px;background:#fff;cursor:pointer">&#8635; Reset</button>
    <span style="color:#aaa">|</span>
    <span style="font-weight:bold;color:#555">Group by:</span>
    {_GROUP_BY_HTML_INLINE}
  </div>
  <div id="upm-dist-panel" style="flex:1;overflow-y:auto;padding:8px 10px">
    <div id="upm-dist-body">
      <div id="sicc-scatter-wrap" style="position:relative;resize:both;overflow:hidden;min-height:200px;min-width:300px;width:100%;height:420px;border:1px solid #eee;border-radius:4px;background:#fff">
        <div id="sicc-scatter-div" style="width:100%;height:100%"></div>
        <div style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#aaa 50%);border-radius:0 0 4px 0;opacity:0.5;pointer-events:none" title="Drag corner to resize"></div>
      </div>
      <div id="sicc-xy-coords" style="display:none;font-size:12px;font-family:monospace;color:#111;background:#f0f0f0;border:1px solid #ccc;border-radius:3px;padding:2px 8px;margin:3px 0;letter-spacing:0.03em"></div>
      <div id="sicc-scatter-note" style="font-size:11px;color:#7f8c8d;margin:2px 0 4px"></div>
      <div style="margin:4px 0 2px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
        <div id="sicc-cat-panel" style="position:relative;display:inline-block"></div>
        <button onclick="_siccDownloadStatsCsv()" title="Download stats table as CSV" style="font-size:11px;padding:3px 8px;border:1px solid #bbb;border-radius:3px;background:#fff;cursor:pointer;margin-left:auto;white-space:nowrap">&#11015; Table CSV</button>
      </div>
      <div style="overflow-x:auto;margin:4px 0 6px">
        <table style="border-collapse:collapse;font-size:11px;white-space:nowrap;min-width:600px">
          <thead id="sicc-stats-head"></thead><tbody id="sicc-stats-body"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>
'''


def _sicc_tab_js() -> str:
    return '''
var _siccScatterMode='sicc';
var _siccSelCols=[];          /* kept for backward compat */
var SICC_SHOW_TARGET=false;   /* target line hidden by default */
var _upmMainSelCol='';        /* selected UPM column for main distribution view */
/* Stubs for removed tabs (TAB_CDYN, TAB_CHARTS) so shared JS calls don't throw */
function render_cdyn(){}
function renderHist(){}
function _paramDropToggle(){_colDdToggle(_siccScatterMode==='cdyn'?'cdyn-col-panel':'sicc-col-panel');}window._paramDropToggle=_paramDropToggle;
var SICC_SOCK_FILTER=new Set(); /* sockets to HIDE; empty = show all */
function toggleSockFilter(s){
  if(SICC_SOCK_FILTER.has(s))SICC_SOCK_FILTER.delete(s);else SICC_SOCK_FILTER.add(s);
  _catPanelUpdateBtn();
  render_upm_dist();
}
function _catPanelUpdateBtn(){
  var btn=document.getElementById('sicc-cat-panel-btn');if(!btn)return;
  var cnt=SICC_SOCK_FILTER.size;
  btn.textContent=cnt===0?'Category (All)':'Category ('+cnt+' hidden)';
  btn.style.color=cnt>0?'#c0392b':'';
}
function _buildCatPanel(sockets){
  var panel=document.getElementById('sicc-cat-panel');if(!panel||panel._built)return;
  panel._built=true;
  var btn=document.createElement('button');
  btn.id='sicc-cat-panel-btn';
  btn.style.cssText='font-size:12px;padding:4px 10px;border:1px solid #bbb;border-radius:4px;background:#fff;cursor:pointer;white-space:nowrap';
  btn.textContent='Category (All)';
  btn.addEventListener('click',function(e){e.stopPropagation();var drop=document.getElementById('sicc-cat-drop');if(!drop)return;drop.style.display=drop.style.display==='none'?'block':'none';});
  var drop=document.createElement('div');
  drop.id='sicc-cat-drop';
  drop.style.cssText='display:none;position:absolute;z-index:9999;background:#fff;border:1px solid #bbc;border-radius:4px;box-shadow:0 4px 14px rgba(0,0,0,.18);min-width:180px;top:100%;left:0;margin-top:2px;padding:4px 0';
  sockets.forEach(function(s){
    var lbl=document.createElement('label');
    lbl.style.cssText='display:flex;align-items:center;gap:5px;padding:4px 10px;cursor:pointer;font-size:12px;white-space:nowrap';
    lbl.addEventListener('mouseover',function(){lbl.style.background='#f0f6ff';});
    lbl.addEventListener('mouseout',function(){lbl.style.background='';});
    var cb=document.createElement('input');cb.type='checkbox';cb.checked=!SICC_SOCK_FILTER.has(s);cb.style.cursor='pointer';
    cb.addEventListener('change',function(){toggleSockFilter(s);});
    var span=document.createElement('span');span.textContent=s||'(none)';
    lbl.appendChild(cb);lbl.appendChild(span);drop.appendChild(lbl);
  });
  panel.appendChild(btn);panel.appendChild(drop);
  document.addEventListener('click',function(e){if(drop.style.display==='none')return;if(panel.contains(e.target))return;drop.style.display='none';},true);
}
window.toggleSockFilter=toggleSockFilter;
/* _catDdToggle: open/close the category filter panel from table header */
function _catDdToggle(){
  var drop=document.getElementById('sicc-cat-drop');
  if(!drop)return;
  drop.style.display=drop.style.display==='none'?'block':'none';
}
window._catDdToggle=_catDdToggle;
var SICC_SEL_COLS=new Set();  /* selected SICC column keys */
var CDYN_SEL_COLS=new Set();  /* selected CDYN column keys */
var _siccColsBuilt=false,_cdynColsBuilt=false;
var SICC_CHECKED_ROWS=new Set();
var _siccAllRowKeys=[];            /* ordered list of all row keys for All/None */
var _siccAllRowKeysSet=new Set();  /* O(1) lookup mirror of _siccAllRowKeys */
var _siccTraceIndexMap={};         /* rowKey → [scatter_idx, trend_idx?] for fast Plotly.restyle */
var _siccTargetTraceIndices=[];    /* target-line trace indices (one per col) */
var SICC_TREND='ols';              /* 'ols' | 'ts' | 'none' */
var SICC_Y_LOG=false;
var SICC_CROSSHAIR=false;
/* Debounce: coalesce rapid checkbox toggles into one render */
var _upmRenderTimer=null;
function _renderUpmDistDebounced(){clearTimeout(_upmRenderTimer);_upmRenderTimer=setTimeout(render_upm_dist,120);}
/* Subsample: reduce scatter points to maxN using Fisher-Yates on indices */
function _subsampleIdx(n,maxN){
  if(n<=maxN)return null; /* null = use all */
  var idx=new Array(n);for(var i=0;i<n;i++)idx[i]=i;
  for(var i=n-1;i>=n-maxN;i--){var j=Math.floor(Math.random()*(i+1));var t=idx[i];idx[i]=idx[j];idx[j]=t;}
  return idx.slice(n-maxN);
}
function _setSiccShowTarget(v){SICC_SHOW_TARGET=v;render_upm_dist();}
window._setSiccShowTarget=_setSiccShowTarget;
var _upmColTimer=null;
function _setUpmMainCol(c){
  if(!c)return;
  if(_upmMainSelCol===c)return; /* no-op if already selected */
  _upmMainSelCol=c;
  /* Sync select display value in case called programmatically */
  var _sel=document.getElementById('upm-main-col-sel');
  if(_sel&&_sel.value!==c)_sel.value=c;
  clearTimeout(_upmColTimer);
  render_upm_dist();
}
window._setUpmMainCol=_setUpmMainCol;
function _populateUpmMainDrop(){
  var sel=document.getElementById('upm-main-col-sel');if(!sel||sel._built)return;
  sel._built=true;
  var cols=(typeof UPM_TBL_CFG!=='undefined'&&UPM_TBL_CFG&&UPM_TBL_CFG.length)
    ?UPM_TBL_CFG.map(function(r){return{v:r[2],l:r[1]||r[2]};})
    :(typeof UPM_DIST_COLS!=='undefined'?UPM_DIST_COLS.map(function(c){return{v:c,l:c};}):[] );
  cols.forEach(function(c){
    var opt=document.createElement('option');opt.value=c.v;opt.textContent=c.l;sel.appendChild(opt);
  });
  if(cols.length&&!_upmMainSelCol){_upmMainSelCol=cols[0].v;sel.value=cols[0].v;}
}
function _drawUpmMainDist(active,col){
  var el=document.getElementById('sicc-scatter-div');
  if(!el||typeof Plotly==='undefined')return;
  if(!col||!active.length){if(el._spl){Plotly.purge(el);el._spl=false;}return;}
  // Collect die-level values (UPM dist cols store per-die values in die_pairs[col].s)
  var allVals=[];
  active.forEach(function(i){
    var r=ROWS[i];
    var dp=r.die_pairs&&r.die_pairs[col];
    if(dp&&dp.s&&dp.s.length){dp.s.forEach(function(v){if(v!=null&&!isNaN(v))allVals.push(v);});}
    else if(r.medians&&r.medians[col]!=null){allVals.push(r.medians[col]);}
  });
  allVals=filterOutliers(allVals.filter(function(v){return v!=null&&!isNaN(v);}),5);
  if(!allVals.length){if(el._spl){Plotly.purge(el);el._spl=false;}return;}
  var tgt=TARGETS[col.toUpperCase()]||null;
  var med=medArr(allVals);
  var nb=Math.max(10,Math.min(80,Math.round(Math.sqrt(allVals.length))));
  var traces=[{
    type:'histogram',x:allVals,name:col,nbinsx:nb,
    marker:{color:'rgba(230,126,34,0.78)',line:{width:0.5,color:'#bf6000'}},
    hovertemplate:'Range: %{x}<br>Count: %{y}<extra></extra>'
  }];
  var shapes=[],annotations=[];
  // Median line
  if(med!=null){
    shapes.push({type:'line',x0:med,x1:med,yref:'paper',y0:0,y1:1,
      line:{color:'#8B4513',dash:'dash',width:2}});
    annotations.push({xref:'x',yref:'paper',x:med,y:0.97,xanchor:'left',yanchor:'top',
      text:'<b>Med: '+med.toFixed(2)+'</b>',showarrow:false,font:{size:11,color:'#8B4513'}});
  }
  // Target line
  if(tgt!=null){
    shapes.push({type:'line',x0:tgt,x1:tgt,yref:'paper',y0:0,y1:1,
      line:{color:'#27ae60',dash:'dot',width:2}});
    annotations.push({xref:'x',yref:'paper',x:tgt,y:0.87,xanchor:'left',yanchor:'top',
      text:'<b>Tgt: '+Number(tgt).toFixed(2)+'</b>',showarrow:false,font:{size:11,color:'#27ae60'}});
  }
  var layout={
    title:{text:col+' \u2014 Distribution ('+allVals.length.toLocaleString()+' dies, '+active.length+' wafer'+(active.length>1?'s':'')+')',font:{size:12,color:'#2c3e50'}},
    xaxis:{title:{text:col,font:{size:12}},tickfont:{size:10},automargin:true},
    yaxis:{title:{text:'Count',font:{size:12}},tickfont:{size:10}},
    margin:{t:40,b:48,l:60,r:20},
    plot_bgcolor:'#fff',paper_bgcolor:'#fff',
    shapes:shapes,annotations:annotations,showlegend:false,bargap:0.04
  };
  var cfg={responsive:true,displayModeBar:true,modeBarButtonsToRemove:['lasso2d','select2d'],displaylogo:false};
  if(el._spl&&el._upmDistCol===col){Plotly.react(el,traces,layout,cfg);el._upmDistCol=col;}
  else{if(el._spl)Plotly.purge(el);Plotly.newPlot(el,traces,layout,cfg);el._spl=true;el._upmDistCol=col;}
}
function _renderUpmMainStats(active,col){
  var hd=document.getElementById('sicc-stats-head'),bd=document.getElementById('sicc-stats-body');
  if(!hd||!bd)return;
  var tgt=TARGETS[col.toUpperCase()]||null;
  var groupMap={},groupOrder=[],allVals=[];
  active.forEach(function(i){
    var r=ROWS[i];
    var gk=XY_COLOR_BY.length?XY_COLOR_BY.map(function(f){
      return f==='lot'?(r.lot||'?'):f==='wafer'?(r.wafer||'?'):f==='material'?(r.material||'?'):f==='program'?(r.program||'?'):'?';
    }).join('/'):'All';
    if(!groupMap[gk]){groupMap[gk]=[];groupOrder.push(gk);}
    var dp=r.die_pairs&&r.die_pairs[col];
    if(dp&&dp.s&&dp.s.length){dp.s.forEach(function(v){if(v!=null&&!isNaN(v)){groupMap[gk].push(v);allVals.push(v);}});}
    else if(r.medians&&r.medians[col]!=null){groupMap[gk].push(r.medians[col]);allVals.push(r.medians[col]);}
  });
  function _stCalc(vals){
    if(!vals.length)return null;
    var s=vals.slice().sort(function(a,b){return a-b;}),n=s.length;
    var sum=s.reduce(function(a,b){return a+b;},0),mean=sum/n;
    var med=n%2?s[(n-1)/2]:(s[n/2-1]+s[n/2])/2;
    var sq=s.reduce(function(a,v){return a+(v-mean)*(v-mean);},0),std=n>1?Math.sqrt(sq/(n-1)):0;
    return{n:n,min:s[0],max:s[n-1],mean:mean,median:med,std:std};
  }
  var totSt=_stCalc(allVals);
  var th='padding:4px 8px;background:#2c3e50;color:#fff;font-size:11px;white-space:nowrap';
  hd.innerHTML='<tr>'
    +'<th style="'+th+';cursor:default;width:22px"></th>'
    +'<th style="'+th+';text-align:left">Group</th>'
    +'<th style="'+th+'">N (dies)</th>'
    +'<th style="'+th+'">Min</th>'
    +'<th style="'+th+'">Median</th>'
    +'<th style="'+th+'">Mean</th>'
    +'<th style="'+th+'">Max</th>'
    +'<th style="'+th+'">Std Dev</th>'
    +'<th style="'+th+'">Target</th>'
    +'<th style="'+th+'">Ratio</th>'
    +'</tr>';
  var COLORS=['#1f77b4','#ff7f0e','#d62728','#9467bd','#8c564b','#e377c2','#00c853','#f39c12'];
  var td='padding:3px 8px;border-bottom:1px solid #eee;font-size:11px';
  function _mkRow(gk,st,isTotal,colIdx){
    if(!st)return'';
    var ratio=(tgt&&st.median)?st.median/tgt:null;
    var over=ratio!=null&&ratio>1,warn=ratio!=null&&ratio>0.95&&ratio<=1;
    var border=(!isTotal&&colIdx!=null)?'border-left:3px solid '+COLORS[colIdx%COLORS.length]+';':'';
    var cbCell=isTotal
      ?'<td style="'+td+';text-align:center"></td>'
      :'<td style="'+td+';text-align:center"><input type="checkbox" checked onchange="_upmGrpToggle(this)" style="cursor:pointer"></td>';
    var trStyle=isTotal?'background:#f0f4f8;font-weight:bold':'';
    return'<tr data-gk="'+esc(gk)+'" style="'+trStyle+border+'">'
      +cbCell
      +'<td style="'+td+';text-align:left'+(isTotal?';font-weight:bold':';color:#555')+'">'+esc(gk)+'</td>'
      +'<td style="'+td+';text-align:right">'+st.n+'</td>'
      +'<td style="'+td+';text-align:right">'+st.min.toFixed(2)+'</td>'
      +'<td style="'+td+';text-align:right'+(over?';background:#fdecea':warn?';background:#fef9e7':'')+'">'+st.median.toFixed(2)+'</td>'
      +'<td style="'+td+';text-align:right">'+st.mean.toFixed(2)+'</td>'
      +'<td style="'+td+';text-align:right">'+st.max.toFixed(2)+'</td>'
      +'<td style="'+td+';text-align:right">'+st.std.toFixed(2)+'</td>'
      +'<td style="'+td+';text-align:right">'+(tgt!=null?tgt.toFixed(2):'--')+'</td>'
      +'<td style="'+td+';text-align:right'+(over?';background:#fdecea;color:#c0392b;font-weight:bold':warn?';background:#fef9e7':'')+'">'+( ratio!=null?ratio.toFixed(2):'--')+'</td>'
      +'</tr>';
  }
  var body=totSt?_mkRow('All (Total)',totSt,true,null):'';
  groupOrder.forEach(function(gk,gi){body+=_mkRow(gk,_stCalc(groupMap[gk]),false,gi);});
  bd.innerHTML=body||'<tr><td colspan="10" style="padding:8px;color:#aaa;text-align:center">No data</td></tr>';
}
window._renderUpmMainStats=_renderUpmMainStats;
function _upmGrpToggle(cb){
  var tr=cb.closest('tr');if(!tr)return;
  tr.style.opacity=cb.checked?'1':'0.3';
  tr.querySelectorAll('td:not(:first-child)').forEach(function(td){td.style.textDecoration=cb.checked?'none':'line-through';});
  _upmGrpRedrawDist();
}
function _upmGrpRedrawDist(){
  var bd=document.getElementById('sicc-stats-body');if(!bd)return;
  var checkedGks=new Set();
  bd.querySelectorAll('tr[data-gk]').forEach(function(row){
    var rowCb=row.querySelector('input[type=checkbox]');
    if(rowCb&&rowCb.checked)checkedGks.add(row.getAttribute('data-gk'));
  });
  var ai=getFiltered(),active=ai.filter(function(i){return SEL_WFR.has(i);});
  if(!active.length)active=ai;
  if(!checkedGks.size){_drawUpmMainDist(active,_upmMainSelCol);return;}
  var filtered=active.filter(function(i){
    var r=ROWS[i];
    var gk=XY_COLOR_BY.length?XY_COLOR_BY.map(function(f){
      return f==='lot'?(r.lot||'?'):f==='wafer'?(r.wafer||'?'):f==='material'?(r.material||'?'):f==='program'?(r.program||'?'):'?';
    }).join('/'):'All';
    return checkedGks.has(gk);
  });
  _drawUpmMainDist(filtered.length?filtered:active,_upmMainSelCol);
}
window._upmGrpToggle=_upmGrpToggle;
function _setSiccTrend(m){SICC_TREND=m;render_upm_dist();}
function _setSiccYLog(v){SICC_Y_LOG=v;render_upm_dist();}
function _setSiccCrosshair(v){SICC_CROSSHAIR=v;render_upm_dist();}
function _toggleSiccCol(col,isCdyn){
  var s=isCdyn?CDYN_SEL_COLS:SICC_SEL_COLS;
  if(s.has(col)){
    s.delete(col);
    /* Sync: uncheck all rows for this col */
    _siccAllRowKeys.forEach(function(k){if(k.indexOf(col+'||')===0)SICC_CHECKED_ROWS.delete(k);});
  }else{
    s.add(col);
    /* Re-check all existing rows for this col (they'll auto-add on next render) */
    _siccAllRowKeys.forEach(function(k){if(k.indexOf(col+'||')===0)SICC_CHECKED_ROWS.add(k);});
  }
  render_upm_dist();
}
/* ── Column dropdown helpers (DOM-based, no quote escaping needed) ── */
function _colDdToggle(panelId){
  var drop=document.getElementById(panelId+'-drop');if(!drop)return;
  var opening=drop.style.display==='none';
  drop.style.display=opening?'block':'none';
  if(opening){
    var srch=document.getElementById(panelId+'-srch');
    if(srch){srch.value='';srch.focus();}
    var panel=document.getElementById(panelId);
    if(panel&&panel._cols)_colDdRenderList(panelId,panel._isCdyn?CDYN_SEL_COLS:SICC_SEL_COLS,panel._isCdyn,'');
  }
}
function _colDdSearch(panelId){
  var panel=document.getElementById(panelId);if(!panel||!panel._cols)return;
  var q=(document.getElementById(panelId+'-srch')||{}).value||'';
  _colDdRenderList(panelId,panel._isCdyn?CDYN_SEL_COLS:SICC_SEL_COLS,panel._isCdyn,q.toLowerCase());
}
function _colDdRenderList(panelId,selSet,isCdyn,q){
  var panel=document.getElementById(panelId);if(!panel||!panel._cols)return;
  var list=document.getElementById(panelId+'-list');if(!list)return;
  var vis=q?panel._cols.filter(function(c){return c.l.toLowerCase().indexOf(q)>=0||c.v.toLowerCase().indexOf(q)>=0;}):panel._cols;
  list.innerHTML='';
  if(!vis.length){var nd=document.createElement('div');nd.style.cssText='padding:8px;color:#aaa;font-size:11px';nd.textContent='No matches';list.appendChild(nd);return;}
  vis.forEach(function(c){
    var lbl=document.createElement('label');
    lbl.style.cssText='display:flex;align-items:center;gap:5px;padding:4px 10px;cursor:pointer;font-size:12px;white-space:nowrap';
    lbl.addEventListener('mouseover',function(){lbl.style.background='#f0f6ff';});
    lbl.addEventListener('mouseout',function(){lbl.style.background='';});
    var cb=document.createElement('input');cb.type='checkbox';cb.checked=selSet.has(c.v);cb.style.cursor='pointer';
    cb.addEventListener('change',function(){_toggleSiccCol(c.v,isCdyn);});
    var span=document.createElement('span');span.textContent=c.l;
    lbl.appendChild(cb);lbl.appendChild(span);list.appendChild(lbl);
  });
}
function _colDdUpdateBtn(panelId,selSet){
  var btn=document.getElementById(panelId+'-btn');if(!btn)return;
  var cnt=selSet.size;
  if(cnt===0){btn.textContent='(none)';btn.style.color='#c0392b';}
  else if(cnt===1){
    var panel=document.getElementById(panelId);
    var found=panel&&panel._cols?panel._cols.find(function(c){return selSet.has(c.v);}):null;
    btn.textContent=found?found.l:Array.from(selSet)[0];btn.style.color='';
  }else{btn.textContent=cnt+' selected';btn.style.color='#1a6bb5';}
}
function _colDdBulk(panelId,isCdyn,add){
  var panel=document.getElementById(panelId);if(!panel||!panel._cols)return;
  var q=(document.getElementById(panelId+'-srch')||{}).value||'';
  var vis=q?panel._cols.filter(function(c){return c.l.toLowerCase().indexOf(q.toLowerCase())>=0;}):panel._cols;
  var s=isCdyn?CDYN_SEL_COLS:SICC_SEL_COLS;
  vis.forEach(function(c){
    if(add&&!s.has(c.v)){s.add(c.v);_siccAllRowKeys.forEach(function(k){if(k.indexOf(c.v+'||')===0)SICC_CHECKED_ROWS.add(k);});}
    if(!add&&s.has(c.v)){s.delete(c.v);_siccAllRowKeys.forEach(function(k){if(k.indexOf(c.v+'||')===0)SICC_CHECKED_ROWS.delete(k);});}
  });
  _colDdRenderList(panelId,s,isCdyn,q.toLowerCase());
  _colDdUpdateBtn(panelId,s);
  render_upm_dist();
}
function _buildColPanel(panelId,cols,selSet,isCdyn){
  var panel=document.getElementById(panelId);if(!panel)return;
  if(panel._built)return;
  panel._built=true;
  if(selSet.size===0&&cols.length)selSet.add(cols[0].v);
  panel._cols=cols;panel._isCdyn=isCdyn;
  /* Trigger button */
  var btn=document.createElement('button');
  btn.id=panelId+'-btn';
  btn.style.cssText='font-size:12px;padding:4px 10px;border:1px solid #bbb;border-radius:4px;background:#fff;cursor:pointer;min-width:140px;text-align:left;white-space:nowrap';
  btn.addEventListener('click',function(e){e.stopPropagation();_colDdToggle(panelId);});
  /* Dropdown wrapper */
  var drop=document.createElement('div');
  drop.id=panelId+'-drop';
  drop.style.cssText='display:none;position:absolute;z-index:9999;background:#fff;border:1px solid #bbc;border-radius:4px;box-shadow:0 4px 14px rgba(0,0,0,.18);min-width:280px;max-width:400px;top:100%;left:0;margin-top:2px';
  /* Search bar */
  var bar=document.createElement('div');
  bar.style.cssText='display:flex;align-items:center;gap:4px;padding:5px 6px;border-bottom:1px solid #e8e8e8;background:#f5f5f5';
  var srch=document.createElement('input');srch.id=panelId+'-srch';srch.placeholder='Search\u2026';
  srch.style.cssText='flex:1;font-size:11px;padding:3px 6px;border:1px solid #ccc;border-radius:3px';
  srch.addEventListener('input',function(){_colDdSearch(panelId);});
  var btnAll=document.createElement('button');btnAll.textContent='All';
  btnAll.style.cssText='font-size:11px;padding:2px 7px;border-radius:3px;border:1px solid #bbb;background:#e8f0fe;cursor:pointer';
  btnAll.addEventListener('click',function(){_colDdBulk(panelId,isCdyn,true);});
  var btnClr=document.createElement('button');btnClr.textContent='Clr';
  btnClr.style.cssText='font-size:11px;padding:2px 7px;border-radius:3px;border:1px solid #bbb;background:#fef0e8;cursor:pointer';
  btnClr.addEventListener('click',function(){_colDdBulk(panelId,isCdyn,false);});
  bar.appendChild(srch);bar.appendChild(btnAll);bar.appendChild(btnClr);
  /* List */
  var list=document.createElement('div');list.id=panelId+'-list';
  list.style.cssText='max-height:260px;overflow-y:auto;padding:3px 0';
  drop.appendChild(bar);drop.appendChild(list);
  panel.appendChild(btn);panel.appendChild(drop);
  _colDdRenderList(panelId,selSet,isCdyn,'');
  _colDdUpdateBtn(panelId,selSet);
  /* Close on outside click */
  document.addEventListener('click',function(e){
    if(drop.style.display==='none')return;
    if(panel.contains(e.target))return;
    drop.style.display='none';
  },true);
}
window._colDdToggle=_colDdToggle;
function _siccToggleAll(checked){
  var bd=document.getElementById('sicc-stats-body');if(!bd)return;
  bd.querySelectorAll('input[type=checkbox][data-rk]').forEach(function(cb){
    var rk=cb.getAttribute('data-rk');
    if(checked)SICC_CHECKED_ROWS.add(rk);else SICC_CHECKED_ROWS.delete(rk);
  });
  /* Sync → parameter table */
  if(typeof _ptSyncFromSicc!=='undefined'){_ptSyncFromSicc();if(typeof _ptRender!=='undefined')_ptRender();}
  if(!checked){
    /* Fast path: hide all traces + target lines, clear table — same as _ptToggleAll(false) */
    var _el=document.getElementById('sicc-scatter-div');
    if(_el&&_el._spl&&typeof _siccTraceIndexMap!=='undefined'){
      var _allIdxs=[];
      Object.keys(_siccTraceIndexMap).forEach(function(k){_siccTraceIndexMap[k].forEach(function(i){_allIdxs.push(i);});});
      if(typeof _siccTargetTraceIndices!=='undefined')_siccTargetTraceIndices.forEach(function(i){_allIdxs.push(i);});
      if(_allIdxs.length)Plotly.restyle(_el,{visible:false},_allIdxs);
    }
    bd.innerHTML='';
    var _allCb=document.getElementById('sicc-sel-all');
    if(_allCb){_allCb.checked=false;_allCb.indeterminate=false;}
  }else{
    _renderUpmDistDebounced();
  }
}
window._siccToggleAll=_siccToggleAll;
function _toggleSiccRow(key){
  if(SICC_CHECKED_ROWS.has(key))SICC_CHECKED_ROWS.delete(key);else SICC_CHECKED_ROWS.add(key);
  var vis=SICC_CHECKED_ROWS.has(key);
  var col=key.split('||')[0];
  var isCdyn=_siccScatterMode==='cdyn';
  var s=isCdyn?CDYN_SEL_COLS:SICC_SEL_COLS;
  var anyChecked=_siccAllRowKeys.some(function(k){return k.indexOf(col+'||')===0&&SICC_CHECKED_ROWS.has(k);});
  if(!anyChecked)s.delete(col);else s.add(col);
  /* Update col panel dropdown if open */
  var panelId=isCdyn?'cdyn-col-panel':'sicc-col-panel';
  var _ddEl=document.getElementById(panelId+'-drop');
  if(_ddEl&&_ddEl.style.display!=='none'){_colDdRenderList(panelId,s,isCdyn,'');}
  _colDdUpdateBtn(panelId,s);
  /* Sync → parameter table */
  if(typeof _ptSyncFromSicc!=='undefined'){_ptSyncFromSicc();if(typeof _ptRender!=='undefined')_ptRender();}
  /* Fast path: surgical restyle instead of full re-render */
  var el=document.getElementById('sicc-scatter-div');
  var idxs=_siccTraceIndexMap[key];
  if(el&&el._spl&&idxs&&idxs.length){
    Plotly.restyle(el,{visible:vis},idxs);
    /* Update only this row's opacity in the table — no full DOM rebuild */
    var bd=document.getElementById('sicc-stats-body');
    if(bd){var _cbs=bd.querySelectorAll('input[type=checkbox][data-rk]');for(var _ci=0;_ci<_cbs.length;_ci++){if(_cbs[_ci].getAttribute('data-rk')===key){var _tr=_cbs[_ci].closest('tr');if(_tr)_tr.style.opacity=vis?'1':'0.45';break;}}}
    return;
  }
  /* Fallback: full render if trace map not ready */
  render_upm_dist();
}
function _siccRowKey(col,gk){return col+'||'+gk;}
/* OLS regression */
function _siccOLS(xs,ys){
  var n=xs.length;if(n<2)return null;
  var mx=0,my=0,i;
  for(i=0;i<n;i++){mx+=xs[i];my+=ys[i];}mx/=n;my/=n;
  var num=0,den=0;
  for(i=0;i<n;i++){var dx=xs[i]-mx;num+=dx*(ys[i]-my);den+=dx*dx;}
  if(!den)return null;
  var sl=num/den;return{slope:sl,intercept:my-sl*mx};
}
/* Theil-Sen estimator */
function _siccTS(xs,ys){
  var slopes=[],i,j;
  for(i=0;i<xs.length-1;i++)for(j=i+1;j<xs.length;j++){
    var dx=xs[j]-xs[i];if(Math.abs(dx)>1e-12)slopes.push((ys[j]-ys[i])/dx);
  }
  if(!slopes.length)return null;
  slopes.sort(function(a,b){return a-b;});
  var m=slopes.length,sl=m%2?slopes[(m-1)/2]:(slopes[m/2-1]+slopes[m/2])/2;
  var sx=xs.slice().sort(function(a,b){return a-b;}),sy=ys.slice().sort(function(a,b){return a-b;});
  var mx2=sx.length%2?sx[(sx.length-1)/2]:(sx[sx.length/2-1]+sx[sx.length/2])/2;
  var my2=sy.length%2?sy[(sy.length-1)/2]:(sy[sy.length/2-1]+sy[sy.length/2])/2;
  return{slope:sl,intercept:my2-sl*mx2};
}
window._setSiccTrend=_setSiccTrend;window._setSiccYLog=_setSiccYLog;window._setSiccCrosshair=_setSiccCrosshair;window._toggleSiccRow=_toggleSiccRow;window._toggleSiccCol=_toggleSiccCol;
function _siccResetAxisRange(){
  ['sicc-xmin','sicc-xmax','sicc-ymin','sicc-ymax'].forEach(function(id){var el=document.getElementById(id);if(el)el.value='';});
  render_upm_dist();
}
window._siccResetAxisRange=_siccResetAxisRange;
/* Download the stats table as CSV */
function _siccDownloadStatsCsv(){
  var hd=document.getElementById('sicc-stats-head');
  var bd=document.getElementById('sicc-stats-body');
  if(!bd)return;
  var rows=[];
  /* Header row from thead */
  if(hd){
    var ths=hd.querySelectorAll('th');
    var hdr=[];
    ths.forEach(function(th){
      /* skip the checkbox column */
      if(th.querySelector('input[type=checkbox]'))return;
      hdr.push(th.textContent.trim());
    });
    rows.push(hdr);
  }
  /* Data rows from tbody */
  bd.querySelectorAll('tr').forEach(function(tr){
    var tds=tr.querySelectorAll('td');
    if(!tds.length)return;
    var row=[];
    tds.forEach(function(td){
      /* skip checkbox cell */
      if(td.querySelector('input[type=checkbox]'))return;
      var v=td.textContent.trim();
      /* quote cells containing commas or quotes */
      if(v.indexOf(',')>=0||v.indexOf('"')>=0)v='"'+v.replace(/"/g,'""')+'"';
      row.push(v);
    });
    if(row.length)rows.push(row);
  });
  if(rows.length<=1){alert('No data in table.');return;}
  var csv=rows.map(function(r){return r.join(',');}).join('\\r\\n');
  var blob=new Blob([csv],{type:'text/csv'});
  var a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='sicc_stats.csv';
  document.body.appendChild(a);a.click();
  setTimeout(function(){document.body.removeChild(a);URL.revokeObjectURL(a.href);},100);
}
window._siccDownloadStatsCsv=_siccDownloadStatsCsv;
/* Export XY scatter data as CSV */
function _siccExportXyCsv(){
  var isCdyn=_siccScatterMode==='cdyn';
  var ai=getFiltered(),active=ai.filter(function(i){return SEL_WFR.has(i);});
  if(!active.length)active=ai;
  var cols=Array.from(isCdyn?CDYN_SEL_COLS:SICC_SEL_COLS);
  if(!cols.length){alert('No columns selected.');return;}
  /* Determine if UPM x-axis applies */
  var upmCol=(!isCdyn&&cols.length===1)?_getUpmCol(cols[0]):null;
  var xLabel=upmCol?'UPM (%)':'Wafer';
  var yLabel=isCdyn?'CDYN (nF)':'SICC (A)';
  var hdrs=['Parameter','Group',xLabel,yLabel,'Target','Ratio','Lot','Wafer'];
  var lines=[hdrs.join(',')];
  cols.forEach(function(col){
    if(typeof SICC_CHECKED_ROWS!=='undefined'){
      var hasAny=_siccAllRowKeys.some(function(k){return k.indexOf(col+'||')===0&&SICC_CHECKED_ROWS.has(k);});
      if(!hasAny)return;
    }
    var tgt=isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
    active.forEach(function(i){
      var r=ROWS[i];
      var gk=XY_COLOR_BY.length?XY_COLOR_BY.map(function(f){
        return f==='lot'?(r.lot||'?'):f==='wafer'?(r.wafer||'?'):f==='material'?(r.material||'?'):f==='program'?(r.program||'?'):'?';
      }).join('/'):'All';
      var rk=_siccRowKey(col,gk);
      if(typeof SICC_CHECKED_ROWS!=='undefined'&&!SICC_CHECKED_ROWS.has(rk))return;
      var dp=r.die_pairs&&r.die_pairs[col];
      if(dp&&dp.s&&dp.s.length){
        /* One row per die */
        for(var di=0;di<dp.s.length;di++){
          var sv=dp.s[di],uv=dp.u?dp.u[di]:null;
          if(sv==null||isNaN(sv))continue;
          var ratio=(sv!=null&&tgt)?sv/tgt:null;
          var vals=[col,gk,
            uv!=null?uv.toFixed(4):'',
            sv.toFixed(6),
            tgt!=null?tgt.toFixed(6):'',
            ratio!=null?ratio.toFixed(4):'',
            r.lot||'',r.wafer||''];
          lines.push(vals.map(function(v){return v.indexOf(',')>=0||v.indexOf('"')>=0?'"'+v.replace(/"/g,'""')+'"':v;}).join(','));
        }
      }
    });
  });
  var blob=new Blob([lines.join(String.fromCharCode(13,10))],{type:'text/csv'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download=(isCdyn?'cdyn':'sicc')+'_xy_data.csv';document.body.appendChild(a);a.click();
  setTimeout(function(){document.body.removeChild(a);URL.revokeObjectURL(a.href);},100);
}
window._siccExportXyCsv=_siccExportXyCsv;
// ResizeObserver: relay container resize to Plotly so chart fills the new size
(function(){
  if(!window.ResizeObserver)return;
  var _wrap=null;
  function _initRO(){
    _wrap=document.getElementById('sicc-scatter-wrap');
    if(!_wrap)return;
    new ResizeObserver(function(){
      var el=document.getElementById('sicc-scatter-div');
      if(el&&el._spl&&typeof Plotly!=='undefined')Plotly.Plots.resize(el);
    }).observe(_wrap);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',_initRO);
  else _initRO();
})();
function _setSiccScatterMode(mode){
  _siccScatterMode=mode;
  var b1=document.getElementById('sicc-xy-sicc-btn'),b2=document.getElementById('sicc-xy-cdyn-btn'),b3=document.getElementById('sicc-xy-upm-btn');
  var sp=document.getElementById('sicc-col-panel'),cp=document.getElementById('cdyn-col-panel'),up=document.getElementById('upm-main-col-sel');
  if(b1){if(mode==='sicc'){b1.style.background='#2980b9';b1.style.color='#fff';}else{b1.style.background='#ecf0f1';b1.style.color='#2980b9';}}
  if(b2){if(mode==='cdyn'){b2.style.background='#27ae60';b2.style.color='#fff';}else{b2.style.background='#ecf0f1';b2.style.color='#27ae60';}}
  if(b3){if(mode==='upm'){b3.style.background='#e67e22';b3.style.color='#fff';}else{b3.style.background='#ecf0f1';b3.style.color='#e67e22';}}
  if(sp)sp.style.display=mode==='sicc'?'inline-block':'none';
  if(cp)cp.style.display=mode==='cdyn'?'inline-block':'none';
  if(up)up.style.display=mode==='upm'?'inline-block':'none';
  if(mode==='cdyn'&&cp&&!cp._built){
    var dcols=CDYN_COLS.map(function(c){return{v:c,l:c};});
    _buildColPanel('cdyn-col-panel',dcols,CDYN_SEL_COLS,true);
  }
  if(mode==='upm')_populateUpmMainDrop();
  render_upm_dist();
  if(typeof render_summ_if_open!=='undefined')render_summ_if_open();
}
function _toggleSiccChart(sid){
  var el=document.getElementById(sid);if(!el)return;
  var show=el.style.display==='none';
  el.style.display=show?'':'none';
  if(show)render_upm_dist();
}
function _populateSiccDropdown(){
  var panel=document.getElementById('sicc-col-panel');if(!panel||panel._built)return;
  var cols=SICC_TBL_CFG&&SICC_TBL_CFG.length?SICC_TBL_CFG.map(function(r){return{v:r[2],l:r[1]||r[2]};}):
    SICC_COLS.map(function(c){return{v:c,l:c};});
  _buildColPanel('sicc-col-panel',cols,SICC_SEL_COLS,false);
}
function _onSiccSelChange(){}  /* no-op — kept for compat */
function _drawPlotlyScatterSicc(active,cols,isCdyn){
  var el=document.getElementById('sicc-scatter-div');
  if(!el||typeof Plotly==='undefined')return;
  if(!active.length||!cols.length){if(el._spl)Plotly.purge(el);el._spl=false;return;}
  _siccTraceIndexMap={};  /* reset map on full rebuild */
  _siccTargetTraceIndices=[];
  var COLORS=['#1f77b4','#ff7f0e','#d62728','#9467bd','#8c564b','#e377c2','#00c853','#f39c12','#2e4057','#a93226'];
  var traces=[];var ti=0;var pts_have_upm=false;
  var _xRangeLo=Infinity,_xRangeHi=-Infinity;
  var _yRangeLo=Infinity,_yRangeHi=-Infinity;
  cols.forEach(function(col){
    var upmCol=_getUpmCol(col);
    var groups={},groupOrder=[];
    active.forEach(function(i){
      var r=ROWS[i];
      var gk=XY_COLOR_BY.length?XY_COLOR_BY.map(function(f){return f==='lot'?(r.lot||'?'):f==='wafer'?(r.wafer||'?'):f==='material'?(r.material||'?'):f==='program'?(r.program||'?'):'?';}).join('/'):'All';
      if(!groups[gk]){groups[gk]={x:[],y:[],t:[]};groupOrder.push(gk);}
      var wid=r.wafer||('W'+i);
      var dp=r.die_pairs&&r.die_pairs[col];
      /* Check if die_pairs.u values are valid UPM% (0-100); CDYN partners may be raw frequency */
      /* dpUpmValid: u values are genuine UPM% when their median is 0–105
         (allow slight overclock). Raw-frequency partners have medians in 1000s */
      var _uMed=(function(){if(!dp||!dp.u||!dp.u.length)return null;var s=dp.u.slice().sort(function(a,b){return a-b;});var m=s.length;return m%2?s[(m-1)/2]:(s[m/2-1]+s[m/2])/2;})();
      var dpUpmValid=_uMed!=null&&_uMed>=0&&_uMed<=105;
      if(dp&&dp.s&&dp.s.length&&dpUpmValid){
        /* Per-die scatter: dp.u = UPM% per die (same UPM for SICC and CDYN on same die)
           Upper fence = median + 6 * MAD-based sigma (captures >99.9999% of valid data) */
        pts_have_upm=true;
        var _uv=dp.u.filter(function(v){return v!=null&&!isNaN(v)&&v>=0;}).sort(function(a,b){return a-b;});
        var _um=_uv.length%2?_uv[(_uv.length-1)/2]:(_uv[_uv.length/2-1]+_uv[_uv.length/2])/2;
        var _mads=_uv.map(function(v){return Math.abs(v-_um);}).sort(function(a,b){return a-b;});
        var _mad=_mads.length%2?_mads[(_mads.length-1)/2]:(_mads[_mads.length/2-1]+_mads[_mads.length/2])/2;
        var _uFence=_um+2*1.4826*_mad;  /* ~2-sigma: median+10% for typical UPM ~95% → fence ~105% */
        for(var di=0;di<dp.s.length;di++){
          if(dp.s[di]!=null&&dp.s[di]>0&&dp.u[di]!=null&&dp.u[di]>=0&&dp.u[di]<=_uFence){
            groups[gk].x.push(dp.u[di]);
            groups[gk].y.push(dp.s[di]);
            groups[gk].t.push('<b>'+col+'</b><br>Wafer: '+wid+'<br>UPM%: '+dp.u[di].toFixed(2)+'<br>'+(isCdyn?'CDYN (nF)':'SICC')+': '+dp.s[di].toFixed(4));
          }
        }
      }else if(dp&&dp.s&&dp.s.length){
        /* die_pairs.u exists but values are not valid UPM% — plot die values vs wafer id on X */
        for(var di=0;di<dp.s.length;di++){
          if(dp.s[di]!=null&&dp.s[di]>0){
            groups[gk].x.push(wid);
            groups[gk].y.push(dp.s[di]);
            groups[gk].t.push('<b>'+col+'</b><br>Wafer: '+wid+'<br>'+(isCdyn?'CDYN (nF)':'SICC')+': '+dp.s[di].toFixed(4));
          }
        }
      }
    });
    groupOrder.forEach(function(gn){
      var rowKey=_siccRowKey(col,gn);
      /* Default: add to checked set on first encounter */
      if(!_siccAllRowKeysSet.has(rowKey)){_siccAllRowKeys.push(rowKey);_siccAllRowKeysSet.add(rowKey);SICC_CHECKED_ROWS.add(rowKey);}
      var _isVisible=SICC_CHECKED_ROWS.has(rowKey);
      var g=groups[gn];
      var col2=COLORS[ti%COLORS.length];
      /* Subsample to max 3000 pts per trace — visually identical, 10x faster Plotly render */
      var _sx=g.x,_sy=g.y,_st=g.t;
      var _sidx=_subsampleIdx(_sx.length,3000);
      if(_sidx){_sx=_sidx.map(function(i){return g.x[i];});_sy=_sidx.map(function(i){return g.y[i];});_st=_sidx.map(function(i){return g.t[i];});}
      var _scatterIdx=traces.length;
      traces.push({type:'scattergl',mode:'markers',name:cols.length>1?col+(groupOrder.length>1?' ('+gn+')':''):gn,
        x:_sx,y:_sy,text:_st,hoverinfo:'text',visible:_isVisible,
        marker:{size:4,color:col2,opacity:0.75,line:{width:0.5,color:'#fff'}}});
      _siccTraceIndexMap[rowKey]=[_scatterIdx];
      /* Track x/y range for checked traces only */
      if(_isVisible){
        for(var _ri=0;_ri<g.x.length;_ri++){if(typeof g.x[_ri]==='number'){if(g.x[_ri]<_xRangeLo)_xRangeLo=g.x[_ri];if(g.x[_ri]>_xRangeHi)_xRangeHi=g.x[_ri];}}
        for(var _ri=0;_ri<g.y.length;_ri++){if(g.y[_ri]!=null&&isFinite(g.y[_ri])&&g.y[_ri]>0){if(g.y[_ri]<_yRangeLo)_yRangeLo=g.y[_ri];if(g.y[_ri]>_yRangeHi)_yRangeHi=g.y[_ri];}}
      }
      /* Trend line */
      if(SICC_TREND!=='none'&&g.x.length>=2){
        var numXs=g.x.filter(function(v){return typeof v==='number';});
        var numYs=[];g.x.forEach(function(v,k){if(typeof v==='number')numYs.push(g.y[k]);});
        if(numXs.length>=2){
          var reg=SICC_TREND==='ols'?_siccOLS(numXs,numYs):_siccTS(numXs,numYs);
          if(reg){
            var xmin2=_safeMin(numXs),xmax2=_safeMax(numXs);
            var tx=[xmin2,xmax2],ty=[reg.slope*xmin2+reg.intercept,reg.slope*xmax2+reg.intercept];
            traces.push({type:'scatter',mode:'lines',name:'Trend ('+gn+')',x:tx,y:ty,
              line:{color:col2,dash:'dot',width:1.5},hoverinfo:'skip',showlegend:false,visible:_isVisible});
            _siccTraceIndexMap[rowKey].push(traces.length-1);
          }
        }
      }
      ti++;
    });
    var tgt=isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
    if(SICC_SHOW_TARGET&&tgt&&active.length){
      var upmColT=_getUpmCol(col);
      var xs;
      if(pts_have_upm){
        /* Use pre-computed X range tracked during data-build — no second array pass */
        if(_xRangeLo<=_xRangeHi){xs=[_xRangeLo,_xRangeHi];}
      }
      if(!xs||!xs.length){
        xs=active.map(function(i){var r=ROWS[i];return(!isCdyn&&upmColT)?r.medians[upmColT]:r.wafer||('W'+i);});
        xs=xs.filter(function(x){return x!=null;});
      }
      if(xs.length){traces.push({type:'scatter',mode:'lines',name:'target',x:xs,y:xs.map(function(){return tgt;}),
        line:{color:'#e74c3c',dash:'dash',width:1.5},hoverinfo:'skip',showlegend:false});
        _siccTargetTraceIndices.push(traces.length-1);}
    }
  });
  /* Check if any data was plotted with UPM% on x-axis */
  var hasUpmX=pts_have_upm||(!isCdyn&&_getUpmCol(cols[0]||''));
  var xTitle=hasUpmX?'UPM (%)':'Wafer';
  var yTitle=isCdyn?'CDYN (nF)':'SICC (A)';
  /* Per-trace: show only x/y in hover */
  traces.forEach(function(tr){if(tr.type==='scatter'&&tr.mode==='markers')tr.hovertemplate='<b>X:</b> %{x}<br><b>Y:</b> %{y}<extra></extra>';});
  /* Y range: use inline-tracked values + targets — no second array pass needed */
  cols.forEach(function(col){
    var tgtV=isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
    if(tgtV!=null&&isFinite(tgtV)&&tgtV>0){if(tgtV<_yRangeLo)_yRangeLo=tgtV;if(tgtV>_yRangeHi)_yRangeHi=tgtV;}
  });
  var _yAxisRange=null;
  if(_yRangeLo<=_yRangeHi){
    var _yMin=_yRangeLo,_yMax=_yRangeHi;
    var _yPad=(_yMax-_yMin)*0.20||Math.abs(_yMax)*0.15||0.01;
    /* Proportional 20% padding below/above the data range on both sides */
    _yAxisRange=SICC_Y_LOG
      ?[Math.log10(Math.max(_yMin*(1-0.20),1e-12)),Math.log10(_yMax*(1+0.20))]
      :[_yMin-_yPad,_yMax+_yPad];
  }
  /* Apply manual axis range overrides from textboxes */
  var _xminEl=document.getElementById('sicc-xmin'),_xmaxEl=document.getElementById('sicc-xmax');
  var _yminEl=document.getElementById('sicc-ymin'),_ymaxEl=document.getElementById('sicc-ymax');
  var _xMinOvr=_xminEl&&_xminEl.value!==''?parseFloat(_xminEl.value):null;
  var _xMaxOvr=_xmaxEl&&_xmaxEl.value!==''?parseFloat(_xmaxEl.value):null;
  var _yMinOvr=_yminEl&&_yminEl.value!==''?parseFloat(_yminEl.value):null;
  var _yMaxOvr=_ymaxEl&&_ymaxEl.value!==''?parseFloat(_ymaxEl.value):null;
  var _xAxisRange=(_xMinOvr!=null&&_xMaxOvr!=null)?[_xMinOvr,_xMaxOvr]:(_xMinOvr!=null?[_xMinOvr,null]:(_xMaxOvr!=null?[null,_xMaxOvr]:null));
  if(_yMinOvr!=null||_yMaxOvr!=null){
    /* For log scale Plotly expects range in log10 units; convert user's linear input */
    var _toYAxis=function(v){return SICC_Y_LOG?Math.log10(Math.max(v,1e-12)):v;};
    var _yCurLo=_yAxisRange?_yAxisRange[0]:(_yRangeLo<=_yRangeHi?_toYAxis(_yRangeLo):0);
    var _yCurHi=_yAxisRange?_yAxisRange[1]:(_yRangeLo<=_yRangeHi?_toYAxis(_yRangeHi):1);
    _yAxisRange=[_yMinOvr!=null?_toYAxis(_yMinOvr):_yCurLo,_yMaxOvr!=null?_toYAxis(_yMaxOvr):_yCurHi];
  }
  var spikeOpts=SICC_CROSSHAIR?{showspikes:true,spikemode:'across',spikedash:'solid',spikecolor:'#111',spikethickness:1.5,spikeSnap:'cursor'}:{showspikes:false};
  var _yAxisCfg={title:{text:yTitle,font:{size:12}},tickfont:{size:10},type:SICC_Y_LOG?'log':'linear'};
  if(_yAxisRange)_yAxisCfg.range=_yAxisRange;else _yAxisCfg.autorange=true;
  var _xAxisCfg={title:{text:xTitle,font:{size:12}},tickfont:{size:10}};
  if(_xAxisRange&&_xAxisRange[0]!=null&&_xAxisRange[1]!=null){_xAxisCfg.range=_xAxisRange;}else{_xAxisCfg.autorange=true;}
  /* Build annotations: one label per target line, placed at the right edge */
  var _annotations=[];
  if(SICC_SHOW_TARGET){cols.forEach(function(col){
    var tgtV=isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
    if(tgtV==null||!isFinite(tgtV))return;
    _annotations.push({xref:'paper',yref:'y',x:1.01,y:tgtV,xanchor:'left',yanchor:'middle',
      text:'<b>'+Number(tgtV).toFixed(2)+'</b>',showarrow:false,
      font:{size:10,color:'#e74c3c'}});
  });}
  var layout={
    title:{text:''},
    xaxis:Object.assign(_xAxisCfg,spikeOpts),
    yaxis:Object.assign(_yAxisCfg,spikeOpts),
    margin:{t:10,b:40,l:70,r:70},plot_bgcolor:'#fff',paper_bgcolor:'#fff',
    showlegend:false,
    annotations:_annotations,
    hovermode:'closest'
  };
  var cfg={responsive:true,displayModeBar:true,modeBarButtonsToRemove:['lasso2d','select2d'],displaylogo:false};
  /* Plotly.react diffs existing plot (fast); newPlot only on first render */
  if(el._spl){Plotly.react(el,traces,layout,cfg);}else{Plotly.newPlot(el,traces,layout,cfg);el._spl=true;}
  /* Attach crosshair events (once per element) */
  if(SICC_CROSSHAIR&&!el._chEvt){
    el._chEvt=true;
    el._chLocked=false;
    var coordDiv=document.getElementById('sicc-xy-coords');
    el.on('plotly_hover',function(d){
      if(el._chLocked)return;
      var pt=d.points[0];if(!pt)return;
      if(coordDiv){coordDiv.style.display='inline-block';coordDiv.textContent='X: '+_fmtCoord(pt.x)+'   Y: '+_fmtCoord(pt.y);}
    });
    el.on('plotly_unhover',function(){
      if(el._chLocked)return;
      if(coordDiv)coordDiv.style.display='none';
    });
    el.on('plotly_click',function(d){
      var pt=d.points[0];if(!pt)return;
      if(!el._chLocked){
        /* Lock: draw crosshair shapes at clicked point */
        el._chLocked=true;
        if(coordDiv){coordDiv.style.display='inline-block';coordDiv.style.background='#dbeafe';coordDiv.style.borderColor='#2980b9';coordDiv.textContent='[locked] X: '+_fmtCoord(pt.x)+'   Y: '+_fmtCoord(pt.y)+' (click to release)';}
        Plotly.relayout(el,{shapes:[
          {type:'line',xref:'x',yref:'paper',x0:pt.x,x1:pt.x,y0:0,y1:1,line:{color:'#111',width:1.5,dash:'dot'}},
          {type:'line',xref:'paper',yref:'y',x0:0,x1:1,y0:pt.y,y1:pt.y,line:{color:'#111',width:1.5,dash:'dot'}}
        ]});
      }else{
        /* Unlock: remove shapes */
        el._chLocked=false;
        Plotly.relayout(el,{shapes:[]});
        if(coordDiv){coordDiv.style.display='none';coordDiv.style.background='#f0f0f0';coordDiv.style.borderColor='#ccc';}
      }
    });
  }else if(!SICC_CROSSHAIR&&el._chEvt){
    el._chEvt=false;el._chLocked=false;
    var cd=document.getElementById('sicc-xy-coords');if(cd)cd.style.display='none';
    if(el._spl)Plotly.relayout(el,{shapes:[]});
  }
}
function _fmtCoord(v){
  if(v==null)return '--';
  if(typeof v==='string')return v;
  return Math.abs(v)>=0.01?v.toPrecision(5):v.toExponential(3);
}
function _renderSiccStats(active,cols,isCdyn){
  var hd=document.getElementById('sicc-stats-head'),bd=document.getElementById('sicc-stats-body');
  if(!hd||!bd)return;
  var COLORS=['#1f77b4','#ff7f0e','#d62728','#9467bd','#8c564b','#e377c2','#00c853','#f39c12','#2e4057','#a93226'];
  var th='padding:4px 8px;background:#2c3e50;color:#fff;font-size:11px;white-space:nowrap';
  var typeLabel=isCdyn?'CDYN':'SICC';
  hd.innerHTML='<tr>'
    +'<th style="'+th+'"><input type="checkbox" id="sicc-sel-all" onchange="_siccToggleAll(this.checked)" style="cursor:pointer" title="Select / deselect all"></th>'
    +'<th style="'+th+';text-align:left">Type</th>'
    +'<th style="'+th+';text-align:left;cursor:pointer" onclick="_catDdToggle()" title="Filter by category">Category &#9660;</th>'
    +'<th style="'+th+';text-align:left;cursor:pointer" onclick="_paramDropToggle()" title="Filter parameters">Parameter &#9660;</th>'
    +'<th style="'+th+';text-align:left">Group By</th>'
    +'<th style="'+th+';text-align:right">N (dies)</th>'
    +'<th style="'+th+';text-align:right">Median</th>'
    +'<th style="'+th+';text-align:right">Target</th>'
    +'<th style="'+th+';text-align:right">Ratio</th>'
    +'<th style="'+th+';text-align:right">UPM Med %</th>'
    +'<th style="'+th+';text-align:right">Min</th>'
    +'<th style="'+th+';text-align:right">Max</th>'
    +'<th style="'+th+';text-align:right">Mean</th>'
    +'<th style="'+th+';text-align:right">Std</th>'
    +'</tr>';
  var body='';
  var td='padding:3px 8px;text-align:right;border-bottom:1px solid #eee;font-size:11px';
  var typeLabel=isCdyn?'CDYN':'SICC';
  /* Collect all sockets for building the category panel */
  var allSocks=[];
  cols.forEach(function(col){var s=col.indexOf(' - ')>=0?col.split(' - ').slice(1).join(' - ').trim():'';
    if(allSocks.indexOf(s)<0)allSocks.push(s);});
  _buildCatPanel(allSocks);
  var colorIdx=0;
  cols.forEach(function(col){
    var tgt=isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
    var groupVals={},groupDieN={},groupUpmVals={},groupOrder=[];
    active.forEach(function(i){
      var r=ROWS[i];
      var gk=XY_COLOR_BY.length?XY_COLOR_BY.map(function(f){return f==='lot'?(r.lot||'?'):f==='wafer'?(r.wafer||'?'):f==='material'?(r.material||'?'):f==='program'?(r.program||'?'):'?';}).join('/'):'All';
      if(!groupVals[gk]){groupVals[gk]=[];groupDieN[gk]=0;groupUpmVals[gk]=[];groupOrder.push(gk);}
      var dp=r.die_pairs&&r.die_pairs[col];
      /* Die-level values from die_pairs (always present for SICC and CDYN) */
      if(dp&&dp.s&&dp.s.length){
        dp.s.forEach(function(v){if(v!=null&&!isNaN(v)&&v>0){groupVals[gk].push(v);groupDieN[gk]++;}});
      }
      /* Collect die-level UPM% values for group median */
      if(dp&&dp.u){dp.u.forEach(function(u){if(u!=null&&!isNaN(u))groupUpmVals[gk].push(u);});}
    });
    groupOrder.forEach(function(gk,gi){
      var vals=groupVals[gk];
      var dieN=groupDieN[gk];
      var med=medArr(vals);
      var upmMed=medArr(groupUpmVals[gk]);
      var ratio=(med!=null&&tgt&&tgt!==0)?med/tgt:null;
      var mn=vals.length?_safeMin(vals):null,mx=vals.length?_safeMax(vals):null;
      var mean=vals.length?vals.reduce(function(a,b){return a+b;},0)/vals.length:null,std=null;
      if(mean!=null&&vals.length>1){var sq=vals.reduce(function(s,v){return s+(v-mean)*(v-mean);},0);std=Math.sqrt(sq/(vals.length-1));}
      var over=ratio!=null&&ratio>1,warn=ratio!=null&&ratio>0.95&&ratio<=1;
      var borderTop=gi===0?';border-top:2px solid #bcd':'';
      var rowKey=_siccRowKey(col,gk);
      /* Ensure key is registered & checked by default */
      if(!_siccAllRowKeysSet.has(rowKey)){_siccAllRowKeys.push(rowKey);_siccAllRowKeysSet.add(rowKey);SICC_CHECKED_ROWS.add(rowKey);}
      var chk=SICC_CHECKED_ROWS.has(rowKey);
      var dotCol=COLORS[colorIdx%COLORS.length];colorIdx++;
      /* Split col name on ' - ' → parameter name + socket/category */
      var _parts=col.split(' - ');var _pname=_parts[0].trim();var _sock=_parts.slice(1).join(' - ').trim();
      /* Apply category filter */
      if(SICC_SOCK_FILTER.has(_sock))return;
      body+='<tr style="opacity:'+(chk?'1':'0.45')+'">'
        +'<td style="'+td+';text-align:center'+borderTop+'">'
        +'<span style="display:inline-flex;align-items:center;gap:3px">'
        +'<input type="checkbox" data-rk="'+esc(rowKey)+'"'+(chk?' checked':'')
        +' onchange="_toggleSiccRow(this.getAttribute(\\'data-rk\\'))" style="cursor:pointer;accent-color:'+dotCol+'">'
        +'<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:'+dotCol+'"></span>'
        +'</span></td>'
        +'<td style="'+td+';text-align:left'+borderTop+';color:#888;font-size:10px">'+esc(typeLabel)+'</td>'
        +'<td style="'+td+';text-align:left'+borderTop+';color:#555">'+esc(_sock)+'</td>'
        +'<td style="'+td+';text-align:left;font-weight:bold'+borderTop+'">'+esc(_pname)+'</td>'
        +'<td style="'+td+borderTop+';text-align:left;color:#555">'+esc(gk)+'</td>'
        +'<td style="'+td+borderTop+'">'+dieN+'</td>'
        +'<td style="'+td+borderTop+(over?';background:#fdecea':warn?';background:#fef9e7':'')+'">'+(med!=null?med.toFixed(2):'--')+'</td>'
        +'<td style="'+td+borderTop+'">'+(tgt!=null?tgt.toFixed(2):'--')+'</td>'
        +'<td style="'+td+borderTop+(over?';background:#fdecea;color:#c0392b;font-weight:bold':warn?';background:#fef9e7':'')+'">'+(ratio!=null?ratio.toFixed(2):'--')+'</td>'
        +'<td style="'+td+borderTop+'">'+(upmMed!=null?upmMed.toFixed(2)+'%':'--')+'</td>'
        +'<td style="'+td+borderTop+'">'+(mn!=null?mn.toFixed(2):'--')+'</td>'
        +'<td style="'+td+borderTop+'">'+(mx!=null?mx.toFixed(2):'--')+'</td>'
        +'<td style="'+td+borderTop+'">'+(mean!=null?mean.toFixed(2):'--')+'</td>'
        +'<td style="'+td+borderTop+'">'+(std!=null?std.toFixed(2):'--')+'</td>'
        +'</tr>';
    });
  });
  bd.innerHTML=body;
  /* Sync header select-all checkbox */
  var _allCb=document.getElementById('sicc-sel-all');
  if(_allCb){
    var _visKeys=[],_chkKeys=[];
    bd.querySelectorAll('input[type=checkbox][data-rk]').forEach(function(cb){
      _visKeys.push(cb.getAttribute('data-rk'));
      if(cb.checked)_chkKeys.push(cb.getAttribute('data-rk'));
    });
    _allCb.checked=_visKeys.length>0&&_chkKeys.length===_visKeys.length;
    _allCb.indeterminate=_chkKeys.length>0&&_chkKeys.length<_visKeys.length;
  }
}
function _renderSiccHistOnly(active,col,isCdyn){
  if(!active.length||!col)return;
  var allVals=[];
  active.forEach(function(i){
    var r=ROWS[i];
    /* Die-level values from die_pairs (always present for both SICC and CDYN) */
    var dp=r.die_pairs&&r.die_pairs[col];
    if(dp&&dp.s&&dp.s.length){
      dp.s.forEach(function(v){if(v!=null&&!isNaN(v)&&v>0)allVals.push(v);});
    }
  });
  allVals=filterOutliers(allVals.filter(function(v){return v>0;}),5);
  var tgt=isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
  if(!allVals.length){drawSVG([],[],null,tgt,col,'upm-hist-svg',false);renderStatsTable(null,'upm-stats-tbl');return;}
  var lo=_safeMin(allVals),hi=_safeMax(allVals);
  if(lo===hi){var d=Math.abs(lo*0.05)||0.01;lo-=d;hi+=d;}
  var nb=Math.max(6,Math.min(30,Math.round(Math.sqrt(allVals.length))));
  var step=(hi-lo)/nb,edges=[],counts=[];
  for(var bi=0;bi<=nb;bi++)edges.push(lo+bi*step);
  for(var bi=0;bi<nb;bi++)counts.push(0);
  allVals.forEach(function(m){var idx=Math.min(nb-1,Math.floor((m-lo)/step));if(idx<0)idx=0;counts[idx]++;});
  var uov=(typeof _buildUpmOverlay!=='undefined')?_buildUpmOverlay(active,col,isCdyn):null;
  drawSVG(edges,counts,medArr(allVals),tgt,col,'upm-hist-svg',false,uov,isCdyn?'CDYN':'SICC');
  renderStatsTable(computeStats(allVals),'upm-stats-tbl',4);
  /* UPM stats table */
  var upmTblEl=document.getElementById('upm-stats-tbl');
  if(upmTblEl&&uov&&uov.uMed!=null){
    var allU=[];
    active.forEach(function(i){var r=ROWS[i];var dp=r.die_pairs&&r.die_pairs[col];if(dp&&dp.u)allU=allU.concat(dp.u.filter(function(v){return v!=null&&!isNaN(v);}));});
    var uStats=computeStats(allU);
    if(uStats){
      var uTbl='<div style="margin-top:8px;font-size:11px;font-weight:bold;color:#c0650a">UPM Stats (%)</div>'
        +'<table style="border-collapse:collapse;font-size:11px;margin-top:3px">'
        +'<thead><tr><th style="padding:2px 8px;background:#e67e22;color:#fff;text-align:left">Stat</th><th style="padding:2px 8px;background:#e67e22;color:#fff">Value</th></tr></thead>'
        +'<tbody>'
        +'<tr><td style="padding:2px 8px;border-bottom:1px solid #eee">Count (dies)</td><td style="padding:2px 8px;border-bottom:1px solid #eee;text-align:right">'+uStats.count+'</td></tr>'
        +'<tr><td style="padding:2px 8px;border-bottom:1px solid #eee">Median UPM</td><td style="padding:2px 8px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;color:#c0650a">'+uStats.median.toFixed(2)+'%</td></tr>'
        +'<tr><td style="padding:2px 8px;border-bottom:1px solid #eee">Min UPM</td><td style="padding:2px 8px;border-bottom:1px solid #eee;text-align:right">'+uStats.min.toFixed(2)+'%</td></tr>'
        +'<tr><td style="padding:2px 8px;border-bottom:1px solid #eee">Max UPM</td><td style="padding:2px 8px;border-bottom:1px solid #eee;text-align:right">'+uStats.max.toFixed(2)+'%</td></tr>'
        +'<tr><td style="padding:2px 8px">Std Dev</td><td style="padding:2px 8px;text-align:right">'+uStats.stddev.toFixed(2)+'%</td></tr>'
        +'</tbody></table>';
      upmTblEl.innerHTML=(upmTblEl.innerHTML||'')+uTbl;
    }
  }
  var te=document.getElementById('sicc-dist-title');if(te)te.textContent=col+(isCdyn?' CDYN':' SICC')+' Distribution';
  var ne=document.getElementById('upm-chart-note');if(ne)ne.textContent='Die distribution -- '+active.length+' wafer(s), '+allVals.length+' values';
}
function render_sicc(){
  _populateSiccDropdown();
  var _sh=document.getElementById('sicc-head'),_sb=document.getElementById('sicc-body');
  if(_sh)_sh.innerHTML='';if(_sb)_sb.innerHTML='';
  render_upm_dist();
  render_summ_if_open();
}
function render_upm_dist(){
  _populateSiccDropdown();
  var ai=getFiltered(),active=ai.filter(function(i){return SEL_WFR.has(i);});
  if(!active.length)active=ai;
  /* ── UPM distribution mode ───────────────────────────────────────── */
  if(_siccScatterMode==='upm'){
    _populateUpmMainDrop();
    var col=_upmMainSelCol;
    _drawUpmMainDist(active,col);
    _renderUpmMainStats(active,col);
    var ne2=document.getElementById('sicc-scatter-note');
    if(ne2)ne2.textContent=active.length+' wafer(s) | UPM: '+col;
    return;
  }
  /* ── SICC / CDYN scatter mode ────────────────────────────────────── */
  var isCdyn=_siccScatterMode==='cdyn',cols=[];
  if(isCdyn){
    if(!document.getElementById('cdyn-col-panel')||!document.getElementById('cdyn-col-panel')._built){
      var dcols2=CDYN_COLS.map(function(c){return{v:c,l:c};});
      _buildColPanel('cdyn-col-panel',dcols2,CDYN_SEL_COLS,true);
    }
    cols=Array.from(CDYN_SEL_COLS);
  }else{
    cols=Array.from(SICC_SEL_COLS);
  }
  _drawPlotlyScatterSicc(active,cols,isCdyn);
  _renderSiccStats(active,cols,isCdyn);
  var ne=document.getElementById('sicc-scatter-note');
  if(ne)ne.textContent=active.length+' wafer(s) | Parameter: '+cols.join(', ');
  var pc=cols[0]||null;
  if(pc){
    var hs=document.getElementById('sicc-hist-section');
    if(hs&&hs.style.display!=='none')_renderSiccHistOnly(active,pc,isCdyn);
    var us=document.getElementById('sicc-upm-section');
    if(us&&us.style.display!=='none')drawMiniUpm(active,pc,isCdyn,'sicc-mini-upm-svg','sicc-mini-upm-title','sicc-mini-upm-note');
  }
}
window._setSiccScatterMode=_setSiccScatterMode;
window._toggleSiccChart=_toggleSiccChart;
window._onSiccSelChange=_onSiccSelChange;
registerTab('tab-sicc', render_sicc);
''' + _summ_tab_js()


# ════════════════════════════════════════════════════════════════
# CDYN tab  (formerly _tab_cdyn.py) — not in active TABS list
# ════════════════════════════════════════════════════════════════



def _cdyn_tab_html() -> str:
    chart_body = build_dist_body_html(
        scatter_svg='cdyn-scatter-svg',
        scatter_title='cdyn-scatter-title', scatter_note='cdyn-scatter-note',
        dist_title='cdyn-dist-title', hist_svg='cdyn-hist-svg',
        chart_note='cdyn-chart-note', stats_tbl='cdyn-stats-tbl',
        mini_upm_panel='cdyn-mini-upm-panel', mini_upm_title='cdyn-mini-upm-title',
        mini_upm_svg='cdyn-mini-upm-svg',
        mini_upm_note='cdyn-mini-upm-note',
        scatter_max_width='60%',
        hist_height='371',
    )
    return f'''
<div id="tab-cdyn" class="tab-panel">
  <div class="legend">
    <span class="ld" style="background:#fdecea;border:1px solid #e74c3c"></span>Over target
    <span class="ld" style="background:#fef9e7;border:1px solid #f39c12"></span>Within 10% of target
    <span class="ld" style="background:#eafaf1;border:1px solid #27ae60"></span>Under target
    &mdash; Click row to view distribution
    &nbsp;<button class="wfr-btn" onclick="selAll()">Select All Wafers</button>
    <button class="wfr-btn" onclick="clrAll()">Clear Selection</button>
    &nbsp;<button class="wfr-btn" onclick="showAllCats('cdyn')">Show All Rows</button>
    <button class="wfr-btn" onclick="hideAllCats('cdyn')">Hide All Rows</button>
    &nbsp;<button class="wfr-btn" onclick="exportTblCsv('cdyn-head','cdyn-body','cdyn_table')" title="Export table to CSV">&#8681; Export CSV</button>
  </div>
  <div class="cat-legend" id="cdyn-tab-legend"></div>
  <div class="side-layout">
    <div class="tbl-side" id="cdyn-tbl-side">
      <div class="hm-wrap">
        <table class="hm-tbl"><thead id="cdyn-head"></thead><tbody id="cdyn-body"></tbody></table>
      </div>
    </div>
    <div class="h-splitter" id="cdyn-dist-splitter" onmousedown="startSplit(event,'cdyn-tbl-side',null,'cdyn-tbl-w')"></div>
    <div class="dist-side" id="cdyn-dist-panel">
      <div class="dist-hdr">&#9998; Charts<button class="collapse-btn" onclick="toggleDistPanel('cdyn-dist-panel','cdyn-dist-splitter')" title="Collapse/expand charts">&#9664;</button></div>
      <div id="cdyn-dist-body">
{chart_body}
      </div>
    </div>
  </div>
</div>
'''


def _cdyn_tab_js() -> str:
    return '''
function render_cdyn(){
  var ai=SEL_WFR.size>0?Array.from(SEL_WFR):getFiltered();
  if(CDYN_TBL_CFG&&CDYN_TBL_CFG.length){
    var cats=_getCats(CDYN_TBL_CFG);
    _buildCatLegend(cats,CDYN_CAT_OFF,'cdyn-tab-legend',render_cdyn);
    var hdr='<tr><th class="sticky-l">Test</th><th>Actual Median (nF)</th><th>Expected (nF)</th><th>Ratio</th><th>UPM Median (%)</th><th>UPM Target (%)</th></tr>';
    var body='',lastCat='';
    CDYN_TBL_CFG.forEach(function(row){
      var cat=row[0],dispName=row[1],testName=row[2],upmCol=row[3]||'';
      if(CDYN_CAT_OFF.has(cat))return;
      if(cat!==lastCat){
        body+='<tr class="cat-hdr"><td colspan="6" style="background:'+_catColor(cat)+';color:'+_catBorder(cat)+';border-left:4px solid '+_catBorder(cat)+'">'+esc(cat)+'</td></tr>';
        lastCat=cat;
      }
      var vals=ai.map(function(i){return ROWS[i].cdyn[testName];}).filter(function(v){return v!=null&&!isNaN(v);});
      var actual=medArr(vals);
      var tgt=CDYN_TARGETS[testName]||null;
      var ratio=(actual!=null&&tgt!=null&&tgt!==0)?actual/tgt:null;
      var upmMed=null,upmTgt=null;
      if(upmCol){
        var uv=ai.map(function(i){return ROWS[i].medians[upmCol];}).filter(function(v){return v!=null&&!isNaN(v);});
        upmMed=medArr(uv);
        upmTgt=TARGETS[upmCol.toUpperCase()]||null;
      }
      var bg=_catColor(cat);
      var isSel=(testName===SEL_COL&&IS_CDYN);
      body+='<tr class="'+(isSel?'sel-row':'')+'" style="background:'+bg+'" onclick="selCdyn(&quot;'+testName+'&quot;)">';
      body+='<td class="tn'+(isSel?' sel':'')+'" style="text-align:left;border-left:4px solid '+_catBorder(cat)+'">'+esc(dispName)+'</td>';
      body+='<td class="'+ccls(actual,tgt,true)+'">'+(actual!=null?actual.toFixed(2):'&#8212;')+'</td>';
      body+='<td class="tgt">'+(tgt!=null?tgt.toFixed(2):'&#8212;')+'</td>';
      body+='<td class="'+ratioCls(ratio)+'">'+(ratio!=null?ratio.toFixed(2):'&#8212;')+'</td>';
      body+='<td class="'+upmCls(upmMed,upmTgt)+'">'+(upmMed!=null?upmMed.toFixed(2):'&#8212;')+'</td>';
      body+='<td class="tgt">'+(upmTgt!=null?upmTgt.toFixed(2):'&#8212;')+'</td>';
      body+='</tr>';
    });
    document.getElementById('cdyn-head').innerHTML=hdr;
    document.getElementById('cdyn-body').innerHTML=body;
  }else if(!CDYN_COLS.length){
    document.getElementById('cdyn-head').innerHTML='';
    document.getElementById('cdyn-body').innerHTML='<tr><td colspan="7" style="padding:14px;color:#7f8c8d">No CDYN columns detected.</td></tr>';
  }else{
    var hdr='<tr><th class="sticky-l">Test</th><th>Type</th><th>Actual Median (nF)</th><th>Expected (nF)</th><th>Ratio</th></tr>';
    var body='';
    CDYN_COLS.forEach(function(col){
      var tgt=CDYN_TARGETS[col];
      var vals=ai.map(function(i){return ROWS[i].cdyn[col];}).filter(function(v){return v!=null&&!isNaN(v);});
      var actual=medArr(vals);
      var ratio=(actual!=null&&tgt!=null&&tgt!==0)?actual/tgt:null;
      var isSel=(col===SEL_COL&&IS_CDYN);
      body+='<tr class="'+(isSel?'sel-row':'')+'" onclick="selCdyn(&quot;'+col+'&quot;)" style="cursor:pointer">';
      body+='<td class="tn'+(isSel?' sel':'')+'">'+esc(col)+'</td>';
      body+='<td style="color:#7f8c8d;font-size:11px">'+esc(cdynType(col))+'</td>';
      body+='<td class="'+ccls(actual,tgt,true)+'">'+(actual!=null?actual.toFixed(2):'&#8212;')+'</td>';
      body+='<td class="tgt">'+(tgt!=null?tgt.toFixed(2):'&#8212;')+'</td>';
      body+='<td class="'+ratioCls(ratio)+'">'+(ratio!=null?ratio.toFixed(2):'&#8212;')+'</td>';
      body+='</tr>';
    });
    document.getElementById('cdyn-head').innerHTML=hdr;
    document.getElementById('cdyn-body').innerHTML=body;
  }
  render_cdyn_dist();
}
// ── CDYN Distribution (shown in CDYN tab) ───────────────────────────────
function render_cdyn_dist(){
  var panel=document.getElementById('cdyn-dist-panel');
  var col=null;
  if(SEL_COL&&IS_CDYN)col=SEL_COL;
  else if(SEL_COL&&(CDYN_COLS.indexOf(SEL_COL)>=0||CDYN_TBL_CFG.some(function(r){return r[2]===SEL_COL;})))col=SEL_COL;
  if(!col&&CDYN_TBL_CFG&&CDYN_TBL_CFG.length)col=CDYN_TBL_CFG[0][2];
  if(!col&&CDYN_COLS.length)col=CDYN_COLS[0];
  if(!col){if(panel)panel.style.display='none';drawTabScatter([],null,'cdyn-scatter-svg','cdyn-scatter-title','cdyn-scatter-note');drawMiniUpm([],null,true,'cdyn-mini-upm-svg','cdyn-mini-upm-title','cdyn-mini-upm-note');return;}
  if(panel)panel.style.display='';
  var ai=getFiltered();var active=ai.filter(function(i){return SEL_WFR.has(i);});
  if(!active.length)active=ai;
  _renderDistBody(active,col,{isCdyn:true,histSvg:'cdyn-hist-svg',statsTbl:'cdyn-stats-tbl',noteEl:'cdyn-chart-note',distTitle:'cdyn-dist-title',scatterSvg:'cdyn-scatter-svg',scatterTitle:'cdyn-scatter-title',scatterNote:'cdyn-scatter-note',miniSvg:'cdyn-mini-upm-svg',miniTitle:'cdyn-mini-upm-title',miniNote:'cdyn-mini-upm-note'});
}
function selCol(col){
  SEL_COL=col;IS_CDYN=false;
  render_sicc();
}
function selCdyn(col){
  SEL_COL=col;IS_CDYN=true;
  render_cdyn();
}
window.selCol=selCol;window.selCdyn=selCdyn;
function showAllCats(scope){
  if(scope==='sicc'){SICC_CAT_OFF.clear();render_sicc();}
  else if(scope==='cdyn'){CDYN_CAT_OFF.clear();render_cdyn();}
  else if(scope==='summ-sicc'){SUMM_SICC_OFF.clear();render_summ();}
  else if(scope==='summ-cdyn'){SUMM_CDYN_OFF.clear();render_summ();}
}
function hideAllCats(scope){
  if(scope==='sicc'){_getCats(SICC_TBL_CFG).forEach(function(c){SICC_CAT_OFF.add(c);});render_sicc();}
  else if(scope==='cdyn'){_getCats(CDYN_TBL_CFG).forEach(function(c){CDYN_CAT_OFF.add(c);});render_cdyn();}
  else if(scope==='summ-sicc'){_getCats(SICC_TBL_CFG).forEach(function(c){SUMM_SICC_OFF.add(c);});render_summ();}
  else if(scope==='summ-cdyn'){_getCats(CDYN_TBL_CFG).forEach(function(c){SUMM_CDYN_OFF.add(c);});render_summ();}
}
window.showAllCats=showAllCats;window.hideAllCats=hideAllCats;
registerTab('tab-cdyn', render_cdyn);
'''




# ════════════════════════════════════════════════════════════════
# Charts tab  (formerly _tab_charts.py) — not in active TABS list
# ════════════════════════════════════════════════════════════════



def _charts_tab_html() -> str:
    return '''
<div id="tab-dist" class="tab-panel">
  <div class="col-pills" id="col-pills"></div>
  <div style="display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap">
    <div class="xy-resize-wrap" style="flex:0 0 36%;min-width:300px;position:relative">
      <h3 id="scatter-title" style="margin:0 0 4px;font-size:12px;color:#2c3e50">UPM vs Selected Column</h3>
      <div style="font-size:11px;color:#888;margin-bottom:4px">
        <button class="scatter-ylog-btn" onclick="_toggleScatterYLog()" style="font-size:11px;padding:2px 8px;cursor:pointer;border:1px solid #7f8c8d;border-radius:4px;background:#2c3e50;color:#fff" title="Toggle Y-axis between linear and log scale">Y: Log</button>
      </div>
      <svg id="scatter-svg" style="width:100%;aspect-ratio:1/1;display:block;border:1px solid #eee;border-radius:4px;background:#fff"></svg>
      <div class="xy-resize-handle" style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#aaa 50%);border-radius:0 0 4px 0;opacity:0.5" title="Drag to resize"></div>
      <div class="chart-note" id="scatter-note" style="font-size:16px;color:#2c3e50;margin-top:4px"></div>
    </div>
    <div class="xy-resize-wrap" style="flex:0 0 48%;min-width:300px;position:relative">
      <h3 style="margin:0 0 4px;font-size:18px;color:#2c3e50">Pareto (per wafer)</h3>
      <div style="font-size:14px;color:#888;margin-bottom:4px;line-height:1.6">X-axis: <label style="margin-left:4px;cursor:pointer"><input type="checkbox" class="pareto-gb" value="none" onchange="_toggleParetoGroup('none')"> None</label><label style="margin-left:6px;cursor:pointer"><input type="checkbox" class="pareto-gb" value="program" onchange="_toggleParetoGroup('program')"> Program</label><label style="margin-left:6px;cursor:pointer"><input type="checkbox" class="pareto-gb" value="lot" onchange="_toggleParetoGroup('lot')" checked> Lot</label><label style="margin-left:6px;cursor:pointer"><input type="checkbox" class="pareto-gb" value="wafer" onchange="_toggleParetoGroup('wafer')" checked> Wafer</label><label style="margin-left:6px;cursor:pointer"><input type="checkbox" class="pareto-gb" value="material" onchange="_toggleParetoGroup('material')"> Material</label></div>
      <svg id="pareto-svg" style="width:100%;aspect-ratio:2/1.125;display:block;border:1px solid #eee;border-radius:4px;background:#fff"></svg>
      <div class="xy-resize-handle" style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#aaa 50%);border-radius:0 0 4px 0;opacity:0.5" title="Drag to resize"></div>
      <div class="chart-note" id="pareto-note" style="font-size:12px;color:#7f8c8d;margin-top:4px;text-align:left"></div>
    </div>
  </div>
  <div style="margin-top:48px">
    <div class="xy-resize-wrap" style="max-width:95%;position:relative">
      <h3 style="margin:0 0 4px;font-size:12px;color:#2c3e50">Distribution</h3>
      <svg id="hist-svg" style="width:100%;aspect-ratio:1/0.45;display:block;border:1px solid #eee;border-radius:4px;background:#fff"></svg>
      <div class="xy-resize-handle" style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#aaa 50%);border-radius:0 0 4px 0;opacity:0.5" title="Drag to resize"></div>
      <div class="chart-note" id="chart-note" style="font-size:15px;color:#7f8c8d;margin-top:4px"></div>
      <div id="dist-stats-tbl" style="margin-top:6px"></div>
    </div>
    <div class="xy-resize-wrap" style="max-width:95%;margin-top:36px;position:relative">
      <div id="dist-mini-upm-panel">
        <h3 id="dist-mini-upm-title" style="margin:0 0 4px;font-size:12px;color:#c0650a">UPM Distribution</h3>
        <svg id="dist-mini-upm-svg" style="width:100%;aspect-ratio:1/0.45;display:block;border:1px solid #f5e0c3;border-radius:4px;background:#fffaf4"></svg>
        <div class="xy-resize-handle" style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#aaa 50%);border-radius:0 0 4px 0;opacity:0.5" title="Drag to resize"></div>
        <div id="dist-mini-upm-note" style="font-size:9px;color:#c0650a;margin-top:2px"></div>
      </div>
    </div>
  </div>
</div>
'''


def _charts_tab_js() -> str:
    return '''
var FT = {};
var _ddOpen = null;
function buildPills(){
  var div=document.getElementById('col-pills');
  if(!div)return;
  var html='';var prevS=null;
  ALL_COLS.forEach(function(c){
    var s=UPM_COLS.indexOf(c)>=0?'upm':'sicc';
    if(s!==prevS&&prevS!==null)html+='<hr class="pill-sep">';
    prevS=s;
    var act=(c===SEL_COL&&!IS_CDYN)?' active':'';
    html+='<button class="pill'+act+'" data-c="'+esc(c)+'" onclick="pClick(this,false)">'+esc(c)+'</button>';
  });
  if(CDYN_COLS.length){
    html+='<hr class="pill-sep">';
    CDYN_COLS.forEach(function(c){
      var act=(c===SEL_COL&&IS_CDYN)?' active':'';
      html+='<button class="pill cdyn-pill'+act+'" data-c="'+esc(c)+'" onclick="pClick(this,true)">'+esc(c)+'</button>';
    });
  }
  div.innerHTML=html;
}
function pClick(btn,cdyn){
  var col=btn.dataset.c;
  SEL_COL=col;IS_CDYN=!!cdyn;
  var active=document.querySelector('.tab-panel.active');
  if(active&&active.id==='tab-dist'){renderHist();}
  else if(cdyn){render_cdyn();}
  else{render_sicc();}
}
window.pClick=pClick;
function renderHist(){
  buildPills();
  var _distCfg={isCdyn:IS_CDYN,histSvg:'hist-svg',statsTbl:'dist-stats-tbl',noteEl:'chart-note',distTitle:null,scatterSvg:'scatter-svg',scatterTitle:'scatter-title',scatterNote:'scatter-note',miniSvg:'dist-mini-upm-svg',miniTitle:'dist-mini-upm-title',miniNote:'dist-mini-upm-note'};
  if(!SEL_COL){_renderDistBody([],null,_distCfg);drawPareto([],null);return;}
  var ai=getFiltered();
  var active=ai.filter(function(i){return SEL_WFR.has(i);});
  if(!active.length)active=ai;
  var tgt=IS_CDYN?CDYN_TARGETS[SEL_COL]:TARGETS[SEL_COL.toUpperCase()];
  _renderDistBody(active,SEL_COL,_distCfg);
  drawPareto(active,tgt);
}
function drawPareto(active,tgt){
  var svg=document.getElementById('pareto-svg');
  var note=document.getElementById('pareto-note');
  if(!svg)return;
  if(!SEL_COL||!active||!active.length){svg.innerHTML='';if(note)note.textContent='';return;}
  var _groups={};
  active.forEach(function(i){
    var r=ROWS[i];
    var _dp=r.die_pairs&&r.die_pairs[SEL_COL];
    var _dvals=(_dp&&_dp.s&&_dp.s.length)?_dp.s.filter(function(v){return v!=null&&!isNaN(v)&&v>0;}):[];
    if(_dvals.length){
      var parts=[];
      if(PARETO_GROUP.indexOf('program')>=0)parts.push(r.program||'?');
      if(PARETO_GROUP.indexOf('lot')>=0)parts.push(r.lot);
      if(PARETO_GROUP.indexOf('wafer')>=0)parts.push(r.wafer);
      if(PARETO_GROUP.indexOf('material')>=0)parts.push(r.material||'?');
      var lbl=parts.length?parts.join('/'):r.lot+'/'+r.wafer;
      if(!_groups[lbl])_groups[lbl]={vals:[],indices:[]};
      _dvals.forEach(function(v){_groups[lbl].vals.push(v);});
      _groups[lbl].indices.push(i);
    }
  });
  var pts=[];
  Object.keys(_groups).forEach(function(lbl){
    var g=_groups[lbl];
    var med=medArr(g.vals);
    if(med!=null)pts.push({label:lbl,val:med,idx:g.indices[0],count:g.vals.length});
  });
  if(!pts.length){svg.innerHTML='';if(note)note.textContent='No data.';return;}
  pts.sort(function(a,b){return b.val-a.val;});
  var W=Math.max(svg.clientWidth||500,260),H=svg.clientHeight||383;
  var pl=78,pr=98,pt=32,pb=200;
  var cW=W-pl-pr,cH=H-pt-pb;
  var n=pts.length;
  var maxV=pts[0].val||1;
  if(tgt!=null&&tgt>maxV)maxV=tgt*1.05;
  var bw=Math.min(cW/n,80);
  var barArea=bw*n;
  var xOff=pl+(cW-barArea)/2;
  var p=['<rect width="'+W+'" height="'+H+'" fill="#f8f9fa"/>'];
  p.push('<text x="'+(pl+cW/2).toFixed(1)+'" y="'+(pt-20)+'" text-anchor="middle" font-size="15" fill="#333" font-weight="bold">'+esc(SEL_COL)+'</text>');
  for(var i=0;i<n;i++){
    var bh=(pts[i].val/maxV)*cH;
    var bx=xOff+i*bw;
    var by=pt+cH-bh;
    var col=(tgt!=null&&pts[i].val>tgt)?'#e74c3c':'#3498db';
    p.push('<rect x="'+(bx+1).toFixed(1)+'" y="'+by.toFixed(1)+'" width="'+Math.max(1,bw-2).toFixed(1)+'" height="'+Math.max(1,bh).toFixed(1)+'" fill="'+col+'" opacity="0.82"/>');
    var _tx=(bx+bw/2).toFixed(1);
    if(bh>40){p.push('<text x="'+_tx+'" y="'+(by+14).toFixed(1)+'" text-anchor="middle" font-size="12" fill="#fff" transform="rotate(-90,'+_tx+','+(by+14).toFixed(1)+')">'+ pts[i].val.toFixed(4)+'</text>');}
    else{p.push('<text x="'+_tx+'" y="'+(by-3).toFixed(1)+'" text-anchor="middle" font-size="12" fill="#333" transform="rotate(-90,'+_tx+','+(by-3).toFixed(1)+')">'+ pts[i].val.toFixed(4)+'</text>');}
  }
  if(tgt!=null){
    var ty=pt+cH-(tgt/maxV)*cH;
    if(ty>=pt-2&&ty<=pt+cH+2){
      p.push('<line x1="'+pl+'" x2="'+(pl+cW)+'" y1="'+ty.toFixed(1)+'" y2="'+ty.toFixed(1)+'" stroke="#27ae60" stroke-width="2"/>');  
      p.push('<text x="'+(pl+cW*0.75).toFixed(1)+'" y="'+(ty-4).toFixed(1)+'" font-size="14" fill="#27ae60" font-weight="bold">Exp:'+Number(tgt).toFixed(4)+'</text>');
      p.push('<line x1="'+(pl-6)+'" x2="'+pl+'" y1="'+ty.toFixed(1)+'" y2="'+ty.toFixed(1)+'" stroke="#27ae60" stroke-width="2"/>');
      p.push('<text x="'+(pl-8)+'" y="'+(ty+4).toFixed(1)+'" text-anchor="end" font-size="13" fill="#27ae60" font-weight="bold">Exp:'+Number(tgt).toFixed(4)+'</text>');
    }
  }
  var ySteps=5;var yStep=maxV/ySteps;
  for(var yi=0;yi<=ySteps;yi++){
    var yt=yi*yStep;
    var yy=pt+cH-(yt/maxV)*cH;
    p.push('<line x1="'+(pl-4)+'" x2="'+pl+'" y1="'+yy.toFixed(1)+'" y2="'+yy.toFixed(1)+'" stroke="#aaa"/>');
    p.push('<text x="'+(pl-6)+'" y="'+(yy+4).toFixed(1)+'" text-anchor="end" font-size="17" fill="#444">'+yt.toFixed(4)+'</text>');
  }

  for(var i=0;i<n;i++){
    var tx=xOff+i*bw+bw/2;
    p.push('<text x="'+tx.toFixed(1)+'" y="'+(pt+cH+8)+'" text-anchor="end" transform="rotate(-45,'+tx.toFixed(1)+','+(pt+cH+8)+')" font-size="11" fill="#333">'+esc(pts[i].label)+'</text>');
  }
  p.push('<line x1="'+pl+'" x2="'+(pl+cW)+'" y1="'+(pt+cH)+'" y2="'+(pt+cH)+'" stroke="#aaa"/>');
  p.push('<line x1="'+pl+'" x2="'+pl+'" y1="'+pt+'" y2="'+(pt+cH)+'" stroke="#aaa"/>');
  // UPM overlay on Pareto: per-wafer UPM median as orange diamond markers
  var uCol=_getUpmCol(SEL_COL);
  if(uCol){
    var uPts=[],uMax=-Infinity,uMin=Infinity;
    for(var i=0;i<n;i++){
      var r=ROWS[pts[i].idx];
      var _udp=r.die_pairs&&r.die_pairs[SEL_COL];
      var _uarr=(_udp&&_udp.u&&_udp.u.length)?_udp.u.filter(function(u){return u!=null&&!isNaN(u)&&u>=0;}):[];
      var uv=_uarr.length?(_uarr.slice().sort(function(a,b){return a-b;})[Math.floor((_uarr.length-1)/2)]):null;
      if(uv!=null){uPts.push({i:i,v:uv});if(uv>uMax)uMax=uv;if(uv<uMin)uMin=uv;}
    }
    if(uPts.length>1&&uMax>uMin){
      var uRange=uMax-uMin;if(uRange===0)uRange=1;
      // Right Y-axis for UPM %
      var uYSteps=4;
      for(var yi=0;yi<=uYSteps;yi++){
        var uv2=uMin+yi*(uRange/uYSteps);
        var uy2=pt+cH-((uv2-uMin)/uRange)*cH;
        p.push('<text x="'+(pl+cW+18)+'" y="'+(uy2+3).toFixed(1)+'" text-anchor="start" font-size="17" fill="#d35400">'+uv2.toFixed(1)+'</text>');
      }

      p.push('<text x="'+(pl+cW+80)+'" y="'+(pt+cH/2).toFixed(1)+'" text-anchor="middle" font-size="17" fill="#d35400" font-weight="bold" transform="rotate(-90,'+(pl+cW+80)+','+(pt+cH/2)+')">UPM%</text>');
      // Draw UPM line + markers
      var uLine='';
      uPts.forEach(function(up){
        var cx=xOff+up.i*bw+bw/2;
        var cy=pt+cH-((up.v-uMin)/uRange)*cH;
        uLine+=cx.toFixed(1)+','+cy.toFixed(1)+' ';
        p.push('<polygon points="'+cx+','+(cy-4)+' '+(cx+4)+','+cy+' '+cx+','+(cy+4)+' '+(cx-4)+','+cy+'" fill="#d35400" opacity="0.85"/>');
      });
    }
  }
  svg.setAttribute('viewBox','0 0 '+W+' '+H);
  svg.innerHTML=p.join('');
  if(note)note.textContent=n+' group(s), sorted descending (median per group).'+(uCol?' Diamonds = UPM %':'');
}
function ftOpen(field,btn){
  if(_ddOpen)_ddClose();
  var vals=getFieldVals(field);
  var panel=document.createElement('div');panel.className='dd-panel';
  panel.innerHTML='<input class="dds" placeholder="Search…">'
    +'<div class="dda"><button>All</button><button>Clear</button></div>'
    +'<div class="ddl" id="_ddl"></div>'
    +'<div class="ddf"><button>OK</button></div>';
  document.body.appendChild(panel);
  var r=btn.getBoundingClientRect();
  panel.style.top=(r.bottom+2+window.scrollY)+'px';
  panel.style.left=Math.min(r.left,window.innerWidth-220)+'px';
  _ddOpen={panel:panel,field:field,btn:btn,vals:vals,chk:FT[field]?new Set(FT[field]):new Set(vals)};
  _ddRender(vals);
  panel.querySelector('.dds').oninput=function(){
    var q=this.value.toLowerCase();
    _ddRender(q?vals.filter(function(v){return String(v).toLowerCase().indexOf(q)>=0;}):vals);
  };
  var acts=panel.querySelectorAll('.dda button');
  acts[0].onclick=function(){_ddOpen.vals.forEach(function(v){_ddOpen.chk.add(v);});_ddRender(_ddOpen.vals);};
  acts[1].onclick=function(){_ddOpen.chk.clear();_ddRender(_ddOpen.vals);};
  panel.querySelector('.ddf button').onclick=_ddApply;
  setTimeout(function(){document.addEventListener('mousedown',_ddOut);},0);
}
window.ftOpen=ftOpen;
function _ddRender(vals){
  var list=document.getElementById('_ddl');if(!list)return;
  list.innerHTML=vals.map(function(v){
    return '<label class="ddi"><input type="checkbox"'+ (_ddOpen.chk.has(v)?' checked':'')+' data-val="'+esc(String(v))+'">'+esc(String(v))+'</label>';
  }).join('');
  list.querySelectorAll('input').forEach(function(inp){
    inp.onchange=function(){
      if(inp.checked)_ddOpen.chk.add(inp.dataset.val);
      else _ddOpen.chk.delete(inp.dataset.val);
    };
  });
}
function _ddApply(){
  if(!_ddOpen)return;
  var field=_ddOpen.field,chk=_ddOpen.chk,vals=_ddOpen.vals;
  FT[field]=(chk.size===vals.length)?null:new Set(chk);
  var btn=document.getElementById('ft-'+field);
  if(btn){
    btn.classList.toggle('active',FT[field]!=null);
    btn.textContent=(FT[field]?field+' ('+FT[field].size+'/'+vals.length+')':' All')+' ▼';
  }
  _ddClose();
  SEL_WFR.clear();
  updateAll();
}
function _ddClose(){
  document.removeEventListener('mousedown',_ddOut);
  if(_ddOpen&&_ddOpen.panel.parentNode)_ddOpen.panel.parentNode.removeChild(_ddOpen.panel);
  _ddOpen=null;
}
function _ddOut(e){if(_ddOpen&&!_ddOpen.panel.contains(e.target))_ddApply();}
registerTab('tab-dist', renderHist, true);
'''




# ════════════════════════════════════════════════════════════════
# Active tabs registry  (formerly _tab_registry.TABS)
# ════════════════════════════════════════════════════════════════
TABS: List[Tab] = [
    Tab(tab_id='tab-sicc', label='Parametric Analysis', active=True,
        html_fn=_sicc_tab_html, js_fn=_sicc_tab_js),
]

# ════════════════════════════════════════════════════════════════
# HTML dashboard generator  (formerly generate_dashboard_html_svg.py)
# ════════════════════════════════════════════════════════════════





def _wm_inject(html: str) -> str:
    _wm = (
        '<div id="_wm_div" style="position:fixed;top:8px;right:12px;font-size:10px;'
        'font-weight:600;pointer-events:none;z-index:99999;'
        'font-family:Arial,sans-serif;user-select:none;letter-spacing:0.04em;'
        'padding:2px 6px;border-radius:3px;background:transparent;">'
        'Pant, Sujit N \u2014 GEMS FTE</div>'
        '<script>(function(){'
        'if(window!==window.top){var _d=document.getElementById("_wm_div");if(_d)_d.style.display="none";return;}'
        'var d=document.getElementById("_wm_div");'
        'if(d)d.style.color="rgba(255,255,255,0.9)";'
        '})();</script>'
    )
    import re as _re_wm
    if '</body>' not in html:
        return html
    html = _re_wm.sub(
        r'<div[^>]*id=["\']_wm_div["\'][^>]*>[\s\S]*?</div>\s*<script[^>]*>[\s\S]*?</script>',
        '', html)
    html = _re_wm.sub(r'<div[^>]*>[^<]*GEMS FTE[^<]*</div>', '', html)
    return html.replace('</body>', _wm + '\n</body>', 1)


def _esc_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


# JS injected INSIDE the main IIFE (so it has direct access to render_sicc etc.)
# Adds window resize + ResizeObserver so charts redraw whenever the panel changes size.
_INNER_RESIZE_OBS_JS = r"""
// ── ResizeObserver: auto-redraw SVG charts on panel/window resize ────────────
// Injected by generate_dashboard_html_svg.py inside the main IIFE so that
// render_sicc / render_cdyn / _TAB_RENDERS are all in scope.
(function () {
  function _svgRerender() {
    var active = document.querySelector('.tab-panel.active');
    if (!active) return;
    var id = active.id;
    if (_TAB_RENDERS[id]) _TAB_RENDERS[id]();
  }
  var _rt = null;
  function _debounced() { clearTimeout(_rt); _rt = setTimeout(_svgRerender, 80); }
  window.addEventListener('resize', _debounced);
  if (typeof ResizeObserver !== 'undefined') {
    var obs = new ResizeObserver(_debounced);
    function _attach() {
      document.querySelectorAll('.dist-side, .side-layout, .tab-content, .chart-panel')
        .forEach(function (el) { obs.observe(el); });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _attach);
    else _attach();
  }
}());
"""


def generate_html_svg(data: dict, output_path: str, title: str = '') -> str:
    """Generate the responsive-SVG variant of the SICC/UPM/CDYN dashboard.

    Identical to generate_html() in generate_dashboard_html.py except:
    • The page title carries the suffix ' — SVG'.
    • A ResizeObserver is injected so charts redraw on window/panel resize.
    • Font colours and all other styling are unchanged from the original.
    """
    csv_name      = data.get('csv_name', 'data.csv')
    display_title = title or csv_name

    sicc_cols     = data.get('sicc_columns', [])
    upm_cols      = data.get('upm_columns', [])
    cdyn_cols     = data.get('cdyn_columns', [])
    targets       = data.get('targets', {})
    cdyn_targets  = data.get('cdyn_targets', {})
    rows          = data.get('rows', [])
    sicc_tbl_cfg  = data.get('sicc_table_config', [])
    cdyn_tbl_cfg  = data.get('cdyn_table_config', [])
    upm_tbl_cfg   = data.get('upm_table_config', [])
    upm_dist      = data.get('upm_dist_cols', [])

    tgt_map = {k.upper(): v for k, v in targets.items()}
    for c in sicc_cols + upm_cols + upm_dist:
        if c.upper() not in tgt_map and c in targets:
            tgt_map[c.upper()] = targets[c]

    data_json     = _esc_json(rows)
    sicc_json     = _esc_json(sicc_cols)
    upm_json      = _esc_json(upm_cols)
    cdyn_json     = _esc_json(cdyn_cols)
    targets_json  = _esc_json(tgt_map)
    cdyn_tgt_json = _esc_json(cdyn_targets)
    sicc_tbl_json = _esc_json(sicc_tbl_cfg)
    cdyn_tbl_json = _esc_json(cdyn_tbl_cfg)
    upm_tbl_json  = _esc_json(upm_tbl_cfg)
    upm_dist_json = _esc_json(upm_dist)
    _def_col      = (sicc_tbl_cfg[0][2] if sicc_tbl_cfg
                     else (sicc_cols + cdyn_cols + [''])[0])
    default_col   = _esc_json(_def_col)

    # ── Tab bar ──────────────────────────────────────────────────────────────
    tabs_html = ''
    for tab in TABS:
        active_cls = ' active' if tab.active else ''
        btn_id = tab.tab_id.replace('tab-', '')
        tabs_html += (
            f'  <button class="tab-btn{active_cls}" id="btn-{btn_id}"'
            f' onclick="showTab(this,\'{tab.tab_id}\')">{tab.label}</button>\n'
        )
    tabs_html += '\n'

    # ── Tab panels ───────────────────────────────────────────────────────────
    tabs_panels_html = ''
    for tab in TABS:
        panel = tab.html_fn()
        if tab.active:
            panel = panel.replace('class="tab-panel"', 'class="tab-panel active"', 1)
        tabs_panels_html += panel + '\n'

    # ── Per-tab JS ───────────────────────────────────────────────────────────
    tabs_js = ''
    for tab in TABS:
        tabs_js += tab.js_fn() + '\n'

    # ── Inline data declarations ──────────────────────────────────────────────
    data_js = (
        f'var ROWS={data_json};\n'
        f'var SICC_COLS={sicc_json};\n'
        f'var UPM_COLS={upm_json};\n'
        f'var CDYN_COLS={cdyn_json};\n'
        f'var TARGETS={targets_json};\n'
        f'var CDYN_TARGETS={cdyn_tgt_json};\n'
        f'var SICC_TBL_CFG={sicc_tbl_json};\n'
        f'var CDYN_TBL_CFG={cdyn_tbl_json};\n'
        f'var UPM_TBL_CFG={upm_tbl_json};\n'
        f'var UPM_DIST_COLS={upm_dist_json};\n'
        f'var ALL_COLS=SICC_COLS.concat(UPM_COLS);\n'
        f'var SEL_COL={default_col};\n'
        f'var IS_CDYN=false;\n'
    )

    # ── Assemble HTML (identical structure to generate_dashboard_html.py) ────
    # Embed Plotly inline so the HTML is fully self-contained and can be shared
    # without requiring access to local file paths.
    _PLOTLY_ABS = os.path.normpath(os.path.join(
        str(_THIS_DIR),
        '..', '..', 'shared', 'library', 'plotly-3.5.0.min.js'
    ))
    with open(_PLOTLY_ABS, 'r', encoding='utf-8') as _pf:
        _plotly_src = _pf.read()
    _plotly_tag = f'<script charset="utf-8">{_plotly_src}</script>\n'

    html = (
        build_page_open(display_title, tabs_html).replace(
            '</head>', _plotly_tag + '</head>', 1)
        + tabs_panels_html
        + build_page_close()
        + '<script>\n(function(){\n'
        + data_js
        + SHARED_JS
        + tabs_js
        + _INNER_RESIZE_OBS_JS          # ← inside IIFE: has access to render_* fns
        + 'if(document.readyState===\'loading\')document.addEventListener(\'DOMContentLoaded\',init);\nelse init();\n'
        + '})();\n'
        + RESIZE_JS
        + '\n</script></body></html>'
    )

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(_wm_inject(html))
    return html

# ════════════════════════════════════════════════════════════════
# CSV processor  (formerly sicc_processor.py)
# ════════════════════════════════════════════════════════════════




# ---------------------------------------------------------------------------
# Wildcard / ordered-token matching (mirrors JSL's OrderedLike)
# ---------------------------------------------------------------------------
def _ordered_like(text: str, pattern: str) -> bool:
    """Return True if all tokens in *pattern* (split on ``*``) appear
    inside *text* in order (case-insensitive)."""
    tokens = [t for t in pattern.split('*') if t]
    if not tokens:
        return True
    pos = 0
    text_up = text.upper()
    for tok in tokens:
        idx = text_up.find(tok.upper(), pos)
        if idx < 0:
            return False
        pos = idx + len(tok)
    return True


def _find_col(df_cols, pattern: str) -> Optional[str]:
    """Return first column name matching *pattern* (wildcard), or None."""
    for c in df_cols:
        if _ordered_like(c, pattern):
            return c
    return None


# ---------------------------------------------------------------------------
# Config loading — supports .jsl and .json
# ---------------------------------------------------------------------------
def load_config(config_path: str) -> dict:
    """Load testlist config from a ``.jsl`` or ``.json`` file.

    JSON format::

        {
          "siccList":      [["pattern", "friendly name"], ...],
          "siccTotalList": [["SumColName", "Col1", "Col2", ...], ...],
          "columnConfigs": [["NewColName", "src_pattern", divisor], ...],
          "cdynList":      [["pattern", "friendly name"], ...]   // optional
        }
    """
    p = Path(config_path)
    if not p.exists():
        return {}
    text = p.read_text(encoding='utf-8')
    if p.suffix.lower() == '.json':
        return json.loads(text)
    # Assume JSL
    return _parse_jsl_config(text)


def _parse_jsl_config(text: str) -> dict:
    """Best-effort parser for testlist.jsl — extracts renameList, TotalList,
    columnConfigs (and optionally cdynList) from JMP Scripting Language source."""

    def _jsl_entries(block: str) -> list:
        """Extract inner ``{ ... }`` items from a JSL list block."""
        entries = []
        depth, start = 0, -1
        for i, ch in enumerate(block):
            if ch == '{':
                if depth == 0:
                    start = i + 1
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    inner = block[start:i]
                    # Extract quoted strings
                    strs = re.findall(r'"([^"]*)"', inner)
                    # Extract standalone numbers that are NOT inside quotes
                    # (needed for columnConfigs divisor e.g. 9154)
                    in_q = [False] * len(inner)
                    for m in re.finditer(r'"[^"]*"', inner):
                        for k in range(m.start(), m.end()):
                            in_q[k] = True
                    nums = [m.group(1) for m in re.finditer(r'\b(\d+(?:\.\d+)?)\b', inner)
                            if not in_q[m.start()]]
                    combined = strs + nums
                    if combined:
                        entries.append(combined)
                    start = -1
        return entries

    def _extract_block(name: str) -> str:
        m = re.search(r'\b' + re.escape(name) + r'\s*=\s*\{', text)
        if not m:
            return ''
        depth, end = 0, m.end() - 1
        for i in range(m.end() - 1, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        return text[m.end():end]

    rename_block  = _extract_block('renameList')
    total_block   = _extract_block('TotalList')
    col_block     = _extract_block('columnConfigs')

    rename_list = []
    for e in _jsl_entries(rename_block):
        if len(e) >= 2:
            rename_list.append([e[0], e[1]])

    total_list = []
    for e in _jsl_entries(total_block):
        if e:
            total_list.append(e)

    column_configs = []
    for e in _jsl_entries(col_block):
        if len(e) >= 3:
            try:
                column_configs.append([e[0], e[1], float(e[2])])
            except (ValueError, IndexError):
                pass

    return {
        'siccList':      rename_list,
        'siccTotalList': total_list,
        'columnConfigs': column_configs,
    }


# ---------------------------------------------------------------------------
# Histogram helper
# ---------------------------------------------------------------------------
def _make_hist(vals: np.ndarray, n_bins: int = 40) -> dict:
    vals = vals[np.isfinite(vals)]
    if len(vals) == 0:
        return {'edges': [], 'counts': []}
    n = min(n_bins, max(8, len(vals) // 5))
    counts, edges = np.histogram(vals, bins=n)
    return {
        'edges':  [round(float(e), 8) for e in edges],
        'counts': [int(c) for c in counts],
    }


# ---------------------------------------------------------------------------
# Main processing function
# ---------------------------------------------------------------------------
def process_csv(csv_path: str,
                config: dict,
                target_csv: str = '',
                cdyn_targets: Optional[dict] = None,
                override_targets: Optional[dict] = None,
                override_cdyn_targets: Optional[dict] = None,
                build_histograms: bool = False) -> dict:
    """Process a sort-data CSV using *config* and return a data dict
    suitable for ``generate_dashboard_html.generate_html``.

    Parameters
    ----------
    csv_path              : path to the raw sort CSV
    config                : dict from ``load_config``
    target_csv            : path to SICC target CSV (TestName, Target columns; legacy)
    cdyn_targets          : dict mapping CDYN friendly names → target values (optional)
    override_targets      : SICC/UPM targets from product config — take precedence over config/CSV
    override_cdyn_targets : CDYN targets from product config — take precedence over config
    """
    df = pd.read_csv(csv_path, dtype=object)
    df = df.copy()  # defragment on entry to suppress PerformanceWarning from column additions
    col_names = list(df.columns)

    rename_list    = config.get('siccList',      config.get('renameList', []))
    total_list     = config.get('siccTotalList', config.get('totalList', []))
    upm_info_list   = config.get('upmInfo', config.get('columnConfigs', []))
    cdyn_list      = config.get('cdynList', [])

    # ── Step 1: Rename SICC columns ────────────────────────────────────────
    rename_map   = {}   # original_col → new_name
    used_targets = {}   # new_name → count (for deduplication)

    for pat, new_name in rename_list:
        matched_cols = [col for col in col_names if col not in rename_map and _ordered_like(col, pat)]
        if not matched_cols:
            continue
        # Rename the first match to the friendly name
        count = used_targets.get(new_name, 0)
        final_name = new_name if count == 0 else f'{new_name} ({count + 1})'
        rename_map[matched_cols[0]] = final_name
        used_targets[new_name] = count + 1
        # Track any additional matches (e.g. V1 vs V2 variants) for coalescing after rename
        if len(matched_cols) > 1:
            rename_map.setdefault('_extra_coalesce_', [])
            rename_map['_extra_coalesce_'].append((final_name, matched_cols[1:]))

    _extra_coalesce = rename_map.pop('_extra_coalesce_', [])
    df = df.rename(columns=rename_map)

    # Fill NaN in renamed columns from additional variant columns (e.g. V2 when V1 was renamed)
    for final_name, extra_cols in _extra_coalesce:
        if final_name in df.columns:
            for extra_col in extra_cols:
                if extra_col in df.columns:
                    df[final_name] = df[final_name].combine_first(
                        pd.to_numeric(df[extra_col], errors='coerce')
                    )

    # ── Step 2: Compute sum columns ────────────────────────────────────────
    # Supports grouped/derived totals, e.g.:
    #   ["SICC FULLCHIP", "SICC CORE 0.95", "SICC ATOM 0.95", "SICC RING 0.95"]
    # by resolving entries iteratively until no new sum can be created.
    sum_col_names = []
    pending_totals = [entry for entry in total_list if isinstance(entry, list) and len(entry) >= 2]
    while pending_totals:
        progressed = False
        still_pending = []

        for entry in pending_totals:
            sum_name = entry[0]
            src_cols = entry[1:]
            avail = [c for c in src_cols if c in df.columns]

            # If none of the source columns exist yet, defer this entry.
            # This allows derived totals to resolve after their dependencies are computed.
            if not avail:
                still_pending.append(entry)
                continue

            numeric_cols = pd.DataFrame(
                {c: pd.to_numeric(df[c], errors='coerce') for c in avail}
            )
            # min_count=1: row must have at least 1 non-NaN src value to produce
            # a sum; rows where ALL src cols are NaN stay NaN (not 0) so that
            # _make_row's dropna() correctly excludes them from the median.
            df[sum_name] = numeric_cols.sum(axis=1, min_count=1)
            if sum_name not in sum_col_names:
                sum_col_names.append(sum_name)
            progressed = True

        if not progressed:
            break
        pending_totals = still_pending

    # ── Step 3: UPM columns (distribution only — not in SICC heatmap) ─────
    upm_col_names: list[str] = []       # empty: UPM excluded from SICC heatmap
    _upm_dist_cols: list[str] = []      # UPM columns for distribution chart
    _upm_targets: dict = {}             # display_name → target% (from upmInfo)
    for entry in upm_info_list:
        if len(entry) < 3:
            continue
        new_name, src_pat = entry[0], entry[1]
        try:
            divisor = float(entry[2])
        except (ValueError, TypeError):
            divisor = np.nan
        src_col = _find_col(col_names, src_pat)
        if src_col and new_name not in df.columns:
            src_vals = pd.to_numeric(df[src_col], errors='coerce')
            scaled_vals = src_vals / divisor * 100 if np.isfinite(divisor) and divisor != 0 else pd.Series(np.nan, index=src_vals.index)

            # Prefer true UPM percent values as-is when source already looks like percent.
            src_valid = src_vals.dropna()
            scaled_valid = scaled_vals.dropna()
            src_pct_like = (len(src_valid) > 0 and (src_valid.between(0, 100).mean() >= 0.9))
            scaled_pct_like = (len(scaled_valid) > 0 and (scaled_valid.between(0, 100).mean() >= 0.9))

            if src_pct_like and not scaled_pct_like:
                df[new_name] = src_vals
            elif scaled_pct_like:
                df[new_name] = scaled_vals
            else:
                # Fallback to scaled behavior to preserve legacy expectation when both are ambiguous.
                df[new_name] = scaled_vals if np.isfinite(divisor) and divisor != 0 else src_vals

            _upm_dist_cols.append(new_name)
        # Extract target from 4th element if present (e.g. "94%" → 94)
        if len(entry) >= 4:
            tgt_str = str(entry[3]).replace('%', '').strip()
            try:
                _upm_targets[new_name] = float(tgt_str)
            except (ValueError, TypeError):
                pass

    # ── Step 4: CDYN columns ───────────────────────────────────────────────
    cdyn_col_names: list[str] = []
    cdyn_rename: dict[str, str] = {}

    if cdyn_list:
        for pat, friendly in cdyn_list:
            for col in col_names:
                if col not in cdyn_rename and _ordered_like(col, pat):
                    cdyn_rename[col] = friendly
                    break
        if cdyn_rename:
            df = df.rename(columns=cdyn_rename)
            cdyn_col_names = list(cdyn_rename.values())
    else:
        # Auto-detect columns that look like CDYN tests
        cdyn_col_names = [
            c for c in df.columns
            if re.search(r'cdyn', c, re.I) or
               (re.search(r'_og_', c, re.I) and re.search(r'_v1_', c, re.I))
        ]

    # ── Step 5: Identify grouping / metadata columns ───────────────────────
    # Defragment DataFrame after repeated column insertions (SICC totals, UPM, CDYN)
    df = df.copy()

    def _col(patterns: list) -> Optional[str]:
        for p in patterns:
            c = next((c for c in df.columns if p.lower() in c.lower()), None)
            if c:
                return c
        return None

    # Prefer SORT_LOT (present in all CSVs after merge) so mixed-product
    # merges don't silently drop rows that are NaN in product-specific columns
    # (e.g. LOTFROMFS only exists in some CSVs and causes groupby to skip rows).
    lot_col = next((c for c in df.columns if c.lower() == 'sort_lot'), None)
    if not lot_col:
        lot_col = _col(['lot']) if not any('slot' in c.lower() for c in df.columns if 'lot' in c.lower()) else None
    if not lot_col:
        lot_col = next((c for c in df.columns if c.lower() == 'lot' or
                        ('lot' in c.lower() and 'slot' not in c.lower())), None)
    wfr_col = (next((c for c in df.columns if 'sort_wafer' in c.lower()), None) or
               next((c for c in df.columns if c.lower() == 'wafer' or 'wafer' in c.lower()), None))
    prg_col = next((c for c in df.columns
                    if 'testprogram' in c.lower() or 'program' in c.lower()), None)
    mat_col = next((c for c in df.columns if 'material' in c.lower()), None)
    x_col   = next((c for c in df.columns if 'sort_x' in c.lower() or c.lower() == 'x'), None)
    y_col   = next((c for c in df.columns if 'sort_y' in c.lower() or c.lower() == 'y'), None)

    group_cols = [c for c in [prg_col, lot_col, wfr_col] if c]

    # ── Extra columns for shared filter panel (same CSV as bin_distribution) ──
    _date_col = (next((c for c in df.columns if 'end_date'   in c.lower()), None) or
                 next((c for c in df.columns if 'start_date' in c.lower()), None) or
                 next((c for c in df.columns if 'date'       in c.lower()), None))
    _ib_col = next((c for c in df.columns
                    if 'interface_bin' in c.lower() and 'total' not in c.lower()), None)
    _upm_med_col_fp = _upm_dist_cols[0] if _upm_dist_cols else None

    # -- Step 6: Numeric conversion --
    sicc_col_names = list(used_targets.keys()) + sum_col_names
    # deduplicate while preserving order
    seen: set = set()
    sicc_col_names = [c for c in sicc_col_names
                      if c in df.columns and not (c in seen or seen.add(c))]
    all_analysis_cols = sicc_col_names + _upm_dist_cols

    # ── Auto-detect fallback: if renameList matched nothing, scan the CSV ─
    if not sicc_col_names and not _upm_dist_cols:
        _META = {prg_col, lot_col, wfr_col, mat_col, x_col, y_col}
        _meta_kw = {'lot', 'wafer', 'program', 'material', 'x', 'y',
                    'slot', 'site', 'bin', 'pass', 'fail', 'date', 'time',
                    'id', 'index', 'seq', 'part', 'tester', 'head'}
        auto_cols = []
        for c in df.columns:
            if c in _META or c is None:
                continue
            cl = c.lower()
            if any(kw in cl for kw in _meta_kw):
                continue
            # must be numeric-ish
            sample = pd.to_numeric(df[c], errors='coerce')
            if sample.notna().sum() > len(df) * 0.3:
                auto_cols.append(c)
        # Split into SICC-like and CDYN-like based on column name hints
        auto_sicc, auto_cdyn = [], []
        for c in auto_cols:
            cl = c.upper()
            if 'UPM' in cl:
                pass  # UPM handled via upmInfo, not auto-detect
            elif 'CDYN' in cl or ('_OG_' in cl and '_V1_' in cl):
                auto_cdyn.append(c)
            else:
                auto_sicc.append(c)
        sicc_col_names = auto_sicc
        if not cdyn_col_names:
            cdyn_col_names = auto_cdyn
        all_analysis_cols = sicc_col_names + _upm_dist_cols

    for c in all_analysis_cols + cdyn_col_names:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')

    # ── Step 7: Load SICC targets ──────────────────────────────────────────
    # Priority: targets embedded in config JSON > target_csv argument
    targets: dict = {}

    # 1) Read from config dict (sicc_targets + upm_targets)
    cfg_sicc = config.get('sicc_targets', {})
    cfg_upm  = config.get('upm_targets', {})
    for name, val in cfg_sicc.items():
        try:
            targets[str(name).strip().upper()] = float(val)
        except (ValueError, TypeError):
            pass
    for name, val in cfg_upm.items():
        try:
            targets[str(name).strip().upper()] = float(val)
        except (ValueError, TypeError):
            pass

    # 2) Fallback: read from separate target CSV (legacy)
    if not targets and target_csv and Path(target_csv).is_file():
        try:
            tdf = pd.read_csv(target_csv, dtype=object)
            tc = tdf.columns.tolist()
            tn_col = next((c for c in tc if 'testname' in c.lower()), tc[0])
            tg_col = next((c for c in tc if 'target' in c.lower()),
                          tc[1] if len(tc) > 1 else None)
            if tg_col:
                for _, row in tdf.iterrows():
                    key = str(row[tn_col]).strip().upper()
                    try:
                        targets[key] = float(str(row[tg_col]).replace(',', ''))
                    except ValueError:
                        pass
        except Exception:
            pass

    # 3) CDYN targets: from config dict (keyed by friendly name from cdynList)
    cfg_cdyn_tgt = config.get('cdyn_targets', {})
    resolved_cdyn_targets: dict = {}
    for name, val in cfg_cdyn_tgt.items():
        try:
            resolved_cdyn_targets[str(name).strip()] = float(val)
        except (ValueError, TypeError):
            pass
    # merge with any explicitly passed cdyn_targets argument
    if cdyn_targets:
        resolved_cdyn_targets.update(cdyn_targets)
    # 4) Apply overrides from product config JSON — highest priority
    if override_targets:
        targets.update(override_targets)
    if override_cdyn_targets:
        resolved_cdyn_targets.update(override_cdyn_targets)
    # Merge UPM targets from upmInfo into main targets dict
    for _n, _v in _upm_targets.items():
        targets[_n.upper()] = _v

    # ── Step 8: Build SICC/CDYN → UPM die-pair mapping ────────────────────
    _pair_map: dict[str, str] = {}  # sicc_or_cdyn_col → upm_col
    # Build set of actual UPM column names in DataFrame for fuzzy fallback
    _upm_cols_in_df = set(_upm_dist_cols)

    # Auto-detect UPM% columns in the DataFrame (0-100 range, 'UPM' in name)
    # These supplement any explicitly listed UPM columns from config.
    _auto_upm_candidates: list[str] = []
    for _c in df.columns:
        if 'upm' in _c.lower() and _c not in _upm_cols_in_df:
            _s = pd.to_numeric(df[_c], errors='coerce').dropna()
            if len(_s) > 0:
                _med = float(_s.median())
                if 0 <= _med <= 105:          # looks like a percentage
                    _auto_upm_candidates.append(_c)
                    _upm_cols_in_df.add(_c)

    def _resolve_upm_col(name: str) -> str | None:
        """Return actual UPM column name in df, or None."""
        if name in _upm_cols_in_df:
            return name
        # Try case-insensitive match
        nl = name.lower()
        for u in _upm_cols_in_df:
            if u.lower() == nl:
                return u
        return None

    def _auto_pair_upm(col: str) -> str | None:
        """Try to find a UPM partner for a SICC/CDYN column by name substitution."""
        if not _upm_cols_in_df:
            return None
        cu = col.upper()
        # Try replacing SICC/CDYN token with UPM and finding a match
        for token in ('SICC', 'CDYN'):
            if token not in cu:
                continue
            candidate = _re2.sub(token, 'UPM', cu, count=1)
            for u in _upm_cols_in_df:
                if u.upper() == candidate:
                    return u
        # Fuzzy: longest common suffix match among UPM candidates
        best, best_len = None, 0
        cl = col.lower()
        for u in _upm_cols_in_df:
            ul = u.lower()
            # common suffix length
            i = 0
            while i < min(len(cl), len(ul)) and cl[-(i+1)] == ul[-(i+1)]:
                i += 1
            if i > best_len and i >= 6:   # require at least 6 chars in common suffix
                best_len, best = i, u
        return best

    for cfg_entry in config.get('SiccTableConfig', []):
        if len(cfg_entry) >= 4 and cfg_entry[2] and cfg_entry[3]:
            resolved = _resolve_upm_col(cfg_entry[3])
            if resolved:
                _pair_map[cfg_entry[2]] = resolved
    for cfg_entry in config.get('cdynTableConfig', []):
        if len(cfg_entry) >= 4 and cfg_entry[2] and cfg_entry[3]:
            resolved = _resolve_upm_col(cfg_entry[3])
            if resolved:
                _pair_map[cfg_entry[2]] = resolved

    # Auto-pair any SICC/CDYN columns not yet in _pair_map
    # Since UPM is per-die (same value for SICC and CDYN on the same die),
    # prefer re-using the UPM column already paired with any SICC column.
    _any_sicc_upm = next((v for k, v in _pair_map.items() if k in sicc_col_names), None)
    for _col in list(sicc_col_names) + list(cdyn_col_names):
        if _col not in _pair_map:
            # For CDYN: first try the same UPM column already used by SICC
            if _col in cdyn_col_names and _any_sicc_upm:
                _pair_map[_col] = _any_sicc_upm
            else:
                _ap = _auto_pair_upm(_col)
                if _ap:
                    _pair_map[_col] = _ap

    # ── Step 9: Per-wafer medians + histograms ─────────────────────────────
    rows = []
    _analysis_cols_present = [c for c in all_analysis_cols if c in df.columns]
    _cdyn_cols_present = [c for c in cdyn_col_names if c in df.columns]

    def _make_row(grp: pd.DataFrame, program: str, lot: str,
                  wafer: str, material: str) -> dict:
        medians, hists, cdyn_meds = {}, {}, {}
        die_pairs: dict[str, dict] = {}
        for c in _analysis_cols_present:
            vals = grp[c].dropna().values
            if len(vals):
                medians[c] = round(float(np.median(vals)), 8)
                if build_histograms:
                    hists[c] = _make_hist(vals)
                # Populate die_pairs if this column has a paired UPM column
                upm_partner = _pair_map.get(c)
                if upm_partner and upm_partner in grp.columns:
                    # Paired: keep only rows where both SICC and UPM are valid
                    mask = grp[c].notna() & grp[upm_partner].notna()
                    s_vals = grp.loc[mask, c].values
                    u_vals = grp.loc[mask, upm_partner].values
                    if len(s_vals):
                        die_pairs[c] = {
                            's': [round(float(v), 8) for v in s_vals],
                            'u': [round(float(v), 8) for v in u_vals],
                        }
        for c in _cdyn_cols_present:
            vals = grp[c].dropna().values
            if len(vals):
                cdyn_meds[c] = round(float(np.median(vals)), 8)
                if build_histograms:
                    hists[c] = _make_hist(vals)
                # Populate die_pairs for CDYN columns too
                upm_partner = _pair_map.get(c)
                if upm_partner and upm_partner in grp.columns:
                    mask = grp[c].notna() & grp[upm_partner].notna()
                    s_vals = grp.loc[mask, c].values
                    u_vals = grp.loc[mask, upm_partner].values
                    if len(s_vals):
                        die_pairs[c] = {
                            's': [round(float(v), 8) for v in s_vals],
                            'u': [round(float(v), 8) for v in u_vals],
                        }
        # Die-level data for UPM columns so distribution can be rendered in browser
        for c in _upm_dist_cols:
            if c in grp.columns and c not in die_pairs:
                u_vals_raw = grp[c].dropna().values
                if len(u_vals_raw):
                    die_pairs[c] = {
                        's': [round(float(v), 8) for v in u_vals_raw],
                        'u': [],
                    }
        return {
            'program':    program,
            'lot':        lot,
            'wafer':      wafer,
            'material':   material,
            'total':      len(grp),
            'medians':    medians,
            'hists':      hists,
            'cdyn':       cdyn_meds,
            'die_pairs':  die_pairs,
            # ── Shared filter-panel fields (same CSV → same data contract) ──
            'date':       (str(grp[_date_col].dropna().iloc[0])
                           if _date_col and _date_col in grp.columns
                           and not grp[_date_col].dropna().empty else ''),
            'binCounts':  ({str(k): int(v)
                            for k, v in grp[_ib_col].astype(str)
                              .str.extract(r'(\d+)', expand=False)
                              .dropna().value_counts().items()}
                           if _ib_col and _ib_col in grp.columns else {}),
            'upmMed':     ([round(float(np.median(grp[_upm_med_col_fp].dropna().values)), 4)]
                           if _upm_med_col_fp and _upm_med_col_fp in grp.columns
                           and len(grp[_upm_med_col_fp].dropna()) > 0 else None),
        }

    if group_cols:
        for keys, grp in df.groupby(group_cols, sort=False):
            if not isinstance(keys, tuple):
                keys = (keys,)
            kd = dict(zip(group_cols, keys))
            mat_val = ''
            if mat_col and mat_col in grp.columns:
                nn = grp[mat_col].dropna()
                if not nn.empty:
                    mat_val = str(nn.iloc[0])
            rows.append(_make_row(
                grp,
                program  = str(kd.get(prg_col, '')),
                lot      = str(kd.get(lot_col, '')),
                wafer    = str(kd.get(wfr_col, '')),
                material = mat_val,
            ))
    else:
        rows.append(_make_row(df, '', '', 'ALL', ''))

    return {
        'rows':         rows,
        'sicc_columns': sicc_col_names,
        'upm_columns':  upm_col_names,
        'cdyn_columns': cdyn_col_names,
        'targets':      targets,
        'cdyn_targets': resolved_cdyn_targets,
        'csv_name':     Path(csv_path).name,
        'group_cols': {
            'program':  prg_col,
            'lot':      lot_col,
            'wafer':    wfr_col,
            'material': mat_col,
            'x':        x_col,
            'y':        y_col,
        },
        'sicc_table_config': config.get('SiccTableConfig', []),
        'cdyn_table_config': config.get('cdynTableConfig', []),
        'upm_table_config':  config.get('upmTableConfig', []),
        'upm_dist_cols':     _upm_dist_cols,
        'upm_info':          upm_info_list,
        # Die-level DataFrame with all column transforms (UPM %, SICC/CDYN renames)
        # applied.  Callers that need per-die data can use this directly.
        'df':           df,
    }

# ════════════════════════════════════════════════════════════════
# Standalone launcher + headless entry point  (formerly run_py_dashboard.py)
# ════════════════════════════════════════════════════════════════



# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_CONFIG_JSON = _THIS_DIR.parent.parent / 'collateral' / 'sicc_cdyn_testlist.json'
_DEFAULT_CONFIG = _DEFAULT_CONFIG_JSON


# ---------------------------------------------------------------------------
# Core pipeline function (thread-safe — no tkinter calls)
# ---------------------------------------------------------------------------
def run_python_pipeline(csv_path: str,
                         config_path: str,
                         target_csv: str,
                         output_dir: str,
                         dashboard_dir: str,
                         status_cb,
                         done_cb,
                         error_cb,
                         product_config_path: str = '') -> None:
    """Run the full Python pipeline in a background thread.

    Parameters
    ----------
    csv_path            : path to the sort data CSV
    config_path         : path to testlist.jsl or testlist.json (may be empty)
    target_csv          : path to SICC target CSV (may be empty; legacy)
    output_dir          : folder to write output files
    dashboard_dir       : folder to write the main dashboard HTML
    status_cb           : callable(str) — progress messages
    done_cb             : callable(str) — called with dashboard HTML path on success
    error_cb            : callable(str) — called with error message on failure
    product_config_path : path to Product Config JSON (sicc_targets / upm_target / cdyn_targets)
    """
    try:
        status_cb('Loading configuration…')
        if config_path and Path(config_path).is_file():
            cfg = load_config(config_path)
        elif _DEFAULT_CONFIG.is_file():
            cfg = load_config(str(_DEFAULT_CONFIG))
            status_cb(f'Using default config: {_DEFAULT_CONFIG.name}')
        else:
            cfg = {}
            status_cb('No config found — will auto-detect columns.')

        # Log the first 20 column names so user can see what's in the CSV
        try:
            import pandas as _pd
            _hdr = list(_pd.read_csv(csv_path, nrows=0, dtype=object).columns)
            status_cb(f'CSV has {len(_hdr)} columns. First 20:')
            for _c in _hdr[:20]:
                status_cb(f'  {_c}')
            if len(_hdr) > 20:
                status_cb(f'  … and {len(_hdr)-20} more')
        except Exception:
            pass

        # Extract targets from product config JSON (overrides anything in testlist)
        _override_targets: dict = {}
        _override_cdyn_targets: dict = {}
        if product_config_path and Path(product_config_path).is_file():
            try:
                import json as _jspc
                _pcfg = _jspc.loads(Path(product_config_path).read_text(encoding='utf-8'))
                for _e in _pcfg.get('sicc_targets', []):
                    _t = str(_e.get('test', '')).strip()
                    _v = _e.get('target_A')
                    if _t and _v is not None:
                        try: _override_targets[_t.upper()] = float(_v)
                        except (ValueError, TypeError): pass
                # upm_target not used from Product Config (UPM targets come from upmInfo)
                for _e in _pcfg.get('cdyn_targets', []):
                    _t = str(_e.get('test', '')).strip()
                    _v = _e.get('target_nF')
                    if _t and _v is not None:
                        try: _override_cdyn_targets[_t] = float(_v)
                        except (ValueError, TypeError): pass
                if _override_targets or _override_cdyn_targets:
                    status_cb(f'Loaded {len(_override_targets)} SICC + {len(_override_cdyn_targets)} CDYN targets from product config.')
                # Merge testlist configs from product config (takes precedence)
                for _key in ('siccList', 'siccTotalList', 'cdynList', 'upmInfo',
                             'SiccTableConfig', 'cdynTableConfig', 'upmTableConfig'):
                    if _key in _pcfg:
                        cfg[_key] = _pcfg[_key]
                        status_cb(f'Using {_key} from product config.')
            except Exception as _ep:
                status_cb(f'WARNING: Could not read product config targets: {_ep}')

        status_cb('Processing CSV…')
        data = process_csv(csv_path, cfg, target_csv=target_csv,
                           override_targets=_override_targets or None,
                           override_cdyn_targets=_override_cdyn_targets or None)

        n_rows   = len(data.get('rows', []))
        n_sicc   = len(data.get('sicc_columns', []))
        n_upm    = len(data.get('upm_columns', []))
        n_cdyn   = len(data.get('cdyn_columns', []))
        status_cb(
            f'Found {n_rows} wafers | {n_sicc} SICC | {n_upm} UPM | {n_cdyn} CDYN columns'
        )
        if n_sicc == 0 and n_upm == 0:
            status_cb('WARNING: No SICC/UPM columns matched the testlist patterns.')
            status_cb('Check the column names above vs your testlist.jsl renameList.')
            status_cb('Auto-detecting numeric test columns as fallback…')

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        Path(dashboard_dir).mkdir(parents=True, exist_ok=True)

        status_cb('Generating interactive HTML…')
        csv_stem   = Path(csv_path).stem
        html_name  = f'{csv_stem}_sicc_analysis.html'
        html_path  = Path(dashboard_dir) / html_name
        generate_html_svg(data, str(html_path))

        done_cb(str(html_path))

    except Exception as exc:
        error_cb(str(exc))


# ---------------------------------------------------------------------------
# tkinter GUI
# ---------------------------------------------------------------------------
class PyLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SICC/UPM/CDYN — Python Dashboard')
        self.resizable(False, False)
        self.configure(bg='#1a252f')

        tk.Label(
            self, text='SICC / UPM / CDYN  (Python Engine)',
            bg='#1a252f', fg='#3498db', font=('Arial', 13, 'bold')
        ).grid(row=0, column=0, columnspan=3, pady=(14, 8), padx=14)

        self.csv_var    = self._field(1, 'Input CSV file',         '',                  'csv')
        self.cfg_var    = self._field(2, 'Config (.json/.jsl)',      self._default_cfg(), 'cfg')
        self.out_var    = self._field(3, 'Output folder',            '',                  'dir')
        self.dash_var   = self._field(4, 'Dashboard folder',         '',                  'dir')

        # Info box
        info = tk.LabelFrame(self, text='What this does', bg='#1a252f', fg='#7f8c8d',
                             font=('Arial', 8), padx=8, pady=4)
        info.grid(row=6, column=0, columnspan=3, padx=14, pady=(4, 0), sticky='ew')
        for i, t in enumerate([
            '1. Read sort CSV  →  rename SICC/UPM columns via testlist config',
            '2. Compute sum columns (SICC CORE, ATOM, FULLCHIP) and UPM %',
            '3. Detect CDYN columns and compute medians',
            '4. Calculate per-wafer medians + distributions',
            '5. Write self-contained interactive HTML dashboard',
        ]):
            tk.Label(info, text=t, bg='#1a252f', fg='#95a5a6',
                     font=('Consolas', 8), anchor='w').grid(row=i, column=0, sticky='w')

        # Buttons
        btn_frame = tk.Frame(self, bg='#1a252f')
        btn_frame.grid(row=7, column=0, columnspan=3, pady=12)

        tk.Button(
            btn_frame, text='  Generate Dashboard  ',
            bg='#27ae60', fg='white', font=('Arial', 11, 'bold'),
            relief='flat', cursor='hand2', activebackground='#2ecc71',
            command=self._on_run
        ).pack(side='left', padx=6)

        # Status
        self._status = tk.StringVar(value='Ready')
        tk.Label(
            self, textvariable=self._status,
            bg='#1a252f', fg='#95a5a6', font=('Arial', 9)
        ).grid(row=8, column=0, columnspan=3, pady=(0, 10))

    # ── helpers ────────────────────────────────────────────────────────────

    def _default_cfg(self) -> str:
        return str(_DEFAULT_CONFIG) if _DEFAULT_CONFIG.is_file() else ''

    def _field(self, row: int, label: str, default: str, kind: str) -> tk.StringVar:
        tk.Label(
            self, text=label, bg='#1a252f', fg='#ecf0f1',
            font=('Arial', 9), width=18, anchor='e'
        ).grid(row=row, column=0, padx=(14, 4), pady=3)

        var = tk.StringVar(value=default)
        tk.Entry(
            self, textvariable=var, width=54,
            bg='#2c3e50', fg='white', insertbackground='white',
            relief='flat', font=('Consolas', 9)
        ).grid(row=row, column=1, padx=4, pady=3)

        def browse():
            if kind == 'dir':
                d = filedialog.askdirectory()
                if d:
                    var.set(d.replace('/', '\\'))
            else:
                ftypes = {
                    'csv': [('CSV files', '*.csv'), ('All', '*.*')],
                    'cfg': [('Config', '*.jsl *.json'), ('JSL', '*.jsl'), ('JSON', '*.json'), ('All', '*.*')],
                }.get(kind, [('All', '*.*')])
                f = filedialog.askopenfilename(filetypes=ftypes)
                if f:
                    var.set(f)

        tk.Button(
            self, text='...', bg='#2980b9', fg='white',
            relief='flat', cursor='hand2', width=3,
            activebackground='#3498db', command=browse
        ).grid(row=row, column=2, padx=(0, 14), pady=3)

        return var

    # ── run ────────────────────────────────────────────────────────────────

    def _on_run(self):
        csv   = self.csv_var.get().strip()
        cfg   = self.cfg_var.get().strip()
        out   = self.out_var.get().strip()
        dash  = self.dash_var.get().strip()

        if not csv or not Path(csv).is_file():
            messagebox.showerror('Error', 'Input CSV file not found.')
            return
        if not out:
            out = str(Path(csv).parent / 'sicc_upm_output')

        out  = str(Path(out).resolve())
        dash = str(Path(dash or out).resolve())

        self._status.set('Processing…')
        self.update()

        threading.Thread(
            target=run_python_pipeline,
            args=(csv, cfg, '', out, dash,
                  self._set_status, self._on_done, self._on_error),
            daemon=True
        ).start()

    def _set_status(self, msg: str):
        self.after(0, lambda: self._status.set(msg))

    def _on_done(self, html_path: str):
        self.after(0, lambda: self._status.set(f'Done → {html_path}'))
        webbrowser.open(Path(html_path).as_uri())

    def _on_error(self, msg: str):
        self.after(0, lambda: messagebox.showerror('Error', msg))


# ---------------------------------------------------------------------------
# Headless entry point — compatible with _loader.py dispatch
# ---------------------------------------------------------------------------
def _run_headless(args: list) -> None:
    """Run the Python pipeline without any GUI.

    Called when invoked via::

        python _loader.py run_py_dashboard --headless \\
            --csv-file   <data.csv>          \\
            --output-dir <folder>            \\
            [--config    <testlist.json>]    \\
            [--target-csv <targets.csv>]     \\
            [--dashboard-dir <folder>]       \\
            [--product-config <cfg.json>]

    Stdout lines emitted on completion (same protocol as run_dashboard.py)::

        SICC_DASHBOARD: <abs-path-to-html>
    """
    import argparse as _ap

    p = _ap.ArgumentParser(prog='run_py_dashboard.py --headless')
    p.add_argument('--csv-file',       required=True,  help='Input sort CSV')
    p.add_argument('--config',         default='',     help='Testlist .json/.jsl (optional)')
    p.add_argument('--target-csv',     default='',     help='SICC target CSV (optional)')
    p.add_argument('--output-dir',     required=True,  help='Output folder')
    p.add_argument('--dashboard-dir',  default='',     help='Dashboard folder (defaults to output-dir)')
    p.add_argument('--product-config', default='',     help='Product Config JSON (optional)')
    opts, _unknown = p.parse_known_args(args)

    result: dict = {}
    import threading as _thr
    done_evt = _thr.Event()

    def _done(html_path):
        result['html'] = html_path
        done_evt.set()

    def _error(msg):
        result['error'] = msg
        done_evt.set()

    t = _thr.Thread(
        target=run_python_pipeline,
        args=(opts.csv_file, opts.config, opts.target_csv,
              opts.output_dir, opts.dashboard_dir or opts.output_dir,
              lambda m: print(m, flush=True), _done, _error,
              opts.product_config),
        daemon=False,
    )
    t.start()
    done_evt.wait()
    t.join(timeout=5)

    if 'error' in result:
        sys.exit(1)

    html_path = result.get('html', '')
    if html_path:
        print(f'SICC_DASHBOARD: {html_path}', flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    if '--headless' in sys.argv:
        _run_headless([a for a in sys.argv[1:] if a != '--headless'])
        return

    app = PyLauncher()
    app.mainloop()


if __name__ == '__main__':
    main()
