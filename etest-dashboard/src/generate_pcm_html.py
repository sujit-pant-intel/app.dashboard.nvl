"""generate_pcm_html.py — PCM/ETest HTML Dashboard (vmin-matched layout).

Layout mirrors vmin_dashboard.html exactly:
  * Top filter panel  (wfr-panel) — table with Lot / Wafer / Material / Count
  * Tab strip         — Variability | Summary
  * Variability tab   — side-layout:
      left  = hm-tbl  (Group headers + per-param stats)
      right = SVG strip chart (JS-rendered, colour by Lot/Wafer/Material)
  * Summary tab       — full stats table, CSV download

Public API
----------
    generate_html(df, product_setup, output_path, spec_lookup=None) -> str
"""

from __future__ import annotations

import fnmatch
import json
import math
import os
import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from _constants import _wm_inject


# ---------------------------------------------------------------------------
# CSS  (same class names as vmin dashboard)
# ---------------------------------------------------------------------------
_CSS = """\
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Arial,sans-serif;background:#f0f2f5;color:#2c3e50;font-size:13px}
.page-hdr{background:#1f3a50;color:#fff;padding:10px 16px}
.page-hdr h1{font-size:14px;font-weight:bold}
.page-hdr .sub{font-size:11px;color:#aed6f1;margin-top:2px}
.info-bar{display:flex;flex-wrap:wrap;gap:8px;padding:8px 14px;background:#2c3e50;color:#ecf0f1;font-size:12px;border-bottom:2px solid #1a252f}
.info-bar b{color:#f1c40f}
.info-bar .ib-sep{color:#4a6a8a;margin:0 2px}
.tabs{display:flex;align-items:center;background:#1a252f;padding:6px 14px;gap:6px;border-bottom:3px solid #27ae60;flex-shrink:0}
.tab-btn{padding:8px 24px;border:2px solid transparent;border-radius:6px;background:rgba(255,255,255,0.07);color:#95a5a6;cursor:pointer;font-size:14px;font-weight:bold;letter-spacing:0.03em;transition:background .15s,color .15s,border-color .15s}
.tab-btn:hover{background:rgba(39,174,96,0.20);color:#a9dfbf;border-color:rgba(39,174,96,0.40)}
.tab-btn.active{background:#27ae60;color:#fff;border-color:#1e8449;box-shadow:0 2px 8px rgba(39,174,96,0.35)}
.tab-panel{display:none}.tab-panel.active{display:flex;flex-direction:column;flex:1;min-height:0;overflow:hidden}.tab-panel.active.tab-panel-row{flex-direction:row}#main-content{display:flex;flex-direction:row;flex:1;min-height:0;overflow:hidden}#tab-content{display:flex;flex-direction:column;flex:1;min-height:0;overflow:hidden}
.wfr-btn{padding:3px 10px;font-size:11px;border:1px solid #bdc3c7;border-radius:3px;background:#f8f9fa;cursor:pointer;margin-left:4px}
.wfr-btn:hover{background:#d6eaff;border-color:#2980b9}
.cb{padding:4px 12px;font-size:12px;cursor:pointer;border:1px solid #bdc3c7;background:#ecf0f1;border-radius:3px;color:#2c3e50}
.cb:hover{background:#d5dbde}
.dl-btn{padding:4px 14px;font-size:11px;border:none;border-radius:4px;background:#27ae60;color:#fff;cursor:pointer;font-weight:bold}
.dl-btn:hover{background:#1e8449}
.main-layout{display:flex;flex-direction:column;gap:0}
.tab-content{flex:1;min-width:0;overflow:hidden;padding:8px 14px}
.side-layout{display:flex;gap:0;align-items:flex-start}
.side-layout .tbl-side{flex:0 1 auto;min-width:0;overflow-x:auto;background:#fff;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.10);overflow:hidden}
.side-layout .dist-side{flex:1 1 0;min-width:320px;overflow:hidden;background:#fff;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.10)}
.dist-side{position:relative;overflow:hidden}
.legend{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:11px;color:#7f8c8d;padding:6px 10px 8px;background:#fff;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.07);margin-bottom:8px}
.hm-wrap{overflow:auto;max-height:calc(100vh - 320px);margin-top:0}
.hm-tbl{border-collapse:collapse;font-size:12px;white-space:nowrap;table-layout:auto}
.hm-tbl th{background:#2c3e50;color:#fff;padding:5px 10px;text-align:right;position:sticky;top:0;z-index:1}
.hm-tbl th:first-child{text-align:left;position:sticky;left:0;z-index:2;background:#2c3e50}
.hm-tbl td{padding:4px 10px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap}
.hm-tbl tbody tr:nth-child(even):not(.cat-hdr){background:#f4f8ff}
.hm-tbl td.tn{position:sticky;left:0;background:#f8f9fa;text-align:left;cursor:pointer;border-right:2px solid #dde;z-index:1;max-width:220px;overflow:hidden;text-overflow:ellipsis}
.hm-tbl td.tn:hover{background:#eaf4ff}
.hm-tbl tr.sel-row td{background:#eaf4ff!important}
.hm-tbl tr.sel-row td.tn{background:#d6eaff!important;border-left:3px solid #2980b9;font-weight:bold}
.hm-tbl tbody tr:not(.cat-hdr):hover{background:#eaf4ff!important;cursor:pointer}
.hm-tbl tr.cat-hdr td{background:#2c3e50;color:#ecf0f1;font-weight:bold;font-size:11px;cursor:pointer;padding:4px 10px}
.hm-tbl tr.cat-hdr:hover td{background:#34495e}
.hm-tbl tr.grp-hidden{display:none}
.cell-r{background:#fdecea!important;color:#c0392b;font-weight:bold}
.cell-g{background:#eafaf1!important;color:#1e8449}
.row-info{font-size:10px;color:#aed6f1;margin-left:8px;font-weight:normal}
.wfr-tbl{border-collapse:collapse;width:100%;table-layout:auto;font-size:12px;white-space:nowrap}
.wfr-tbl th{background:#34495e;color:#ecf0f1;padding:5px 10px;text-align:left;position:sticky;top:0;z-index:2}
.wfr-tbl td{padding:4px 10px;border-bottom:1px solid #f0f0f0;cursor:pointer}
.wfr-tbl .num{text-align:right}
.wfr-tbl tbody tr:nth-child(even) td{background:#f7faff}
.wfr-tbl .fr:hover td{background:#eaf4ff!important}
.wfr-tbl .frs td{background:#d6eaff!important;font-weight:bold}
.wfr-tbl .frs:hover td{background:#bcd8f8!important}
.wfr-panel.collapsed .wfr-box>*:not(.wfr-hdr){display:none!important}
.wfr-box{border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.12);background:#fff;margin-bottom:8px}
.wfr-hdr{display:flex;justify-content:space-between;align-items:center;padding:6px 12px;background:#2c3e50;color:#fff;font-size:11px;font-weight:bold;user-select:none;gap:4px}
.wfr-hdr .cb{background:none;border:1px solid #7f8c8d;color:#bdc3c7;font-size:10px;padding:2px 8px;cursor:pointer;border-radius:3px}
.wfr-hdr .cb:hover{background:#3d5166;color:#fff}
.wfr-hdr .collapse-btn{background:none;border:1px solid #7f8c8d;color:#bdc3c7;font-size:10px;padding:1px 7px;cursor:pointer;border-radius:3px}
.wfr-hdr .collapse-btn:hover{background:#3d5166;color:#fff}
.wfr-tbl-wrap{height:150px;overflow-y:auto;overflow-x:auto}
.wfr-resize{height:7px;background:#cbd5e1;cursor:ns-resize;display:flex;align-items:center;justify-content:center;user-select:none;touch-action:none}
.wfr-resize:hover,.wfr-resize.dragging{background:#2980b9}
.wfr-resize::after{content:"\\2261";color:#fff;font-size:10px}
.h-splitter{width:5px;flex-shrink:0;background:#d0d7de;cursor:col-resize;align-self:stretch;min-height:60px;border-radius:2px;transition:background .15s;user-select:none;position:relative}
.h-splitter:hover,.h-splitter.dragging{background:#2980b9}
.h-splitter::after{content:'\\22EE';position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#fff;font-size:14px;line-height:1;pointer-events:none}
.collapse-btn{background:none;border:1px solid #7f8c8d;color:#bdc3c7;font-size:10px;padding:1px 7px;cursor:pointer;border-radius:3px;margin-left:4px;line-height:1.4;user-select:none}
.collapse-btn:hover{background:#3d5166;color:#fff}
.dist-side.collapsed>*:not(.dist-hdr){display:none!important}
.dist-hdr{display:flex;align-items:center;gap:6px;padding:6px 10px;background:#f0f4fb;border-bottom:1px solid #dde;font-size:12px;font-weight:bold;color:#2c3e50;flex-wrap:wrap}
.sum-tbl{border-collapse:collapse;width:100%;font-size:12px;white-space:nowrap}
.sum-tbl th{background:#34495e;color:#ecf0f1;padding:5px 10px;text-align:left;position:sticky;top:0;z-index:1}
.sum-tbl th.num{text-align:right}
.sum-tbl td{padding:4px 10px;border-bottom:1px solid #eee}
.sum-tbl td.num{text-align:right}
.sum-tbl tbody tr:nth-child(even){background:#f4f8ff}
.sum-tbl tbody tr:hover{background:#eaf4ff}
.sum-tbl tr.grp-divider td{border-top:2px solid #bdc3c7}
.tbl-wrap{overflow:auto;flex:1;min-height:0}
/* ── 3-panel layout ── */
.three-panel{display:flex;flex-direction:row;flex:1;min-height:0;overflow:hidden;gap:0}
#panel1{width:280px;min-width:140px;flex-shrink:0;background:#fff;display:flex;flex-direction:column;border-right:2px solid #d0d7de;overflow:hidden;position:relative}
.p1-hdr{background:#2c3e50;color:#fff;padding:6px 10px;font-size:11px;font-weight:bold;display:flex;justify-content:space-between;align-items:center;flex-shrink:0}
.p1-search-row{display:flex;gap:2px;padding:4px 6px;background:#f0f2f5;border-bottom:1px solid #dde;flex-shrink:0}
.p1-search-row input{flex:1;min-width:0;padding:2px 5px;font-size:10px;border:1px solid #ccc;border-radius:3px;background:#fff}
.p1-body{flex:1;overflow-y:auto;overflow-x:auto}
.p1-resize{width:5px;flex-shrink:0;background:#d0d7de;cursor:col-resize;align-self:stretch;transition:background .15s;user-select:none}
.p1-resize:hover,.p1-resize.dragging{background:#2980b9}
/* splitter between P1 and P2: also acts as P2 toggle */
.sp12{width:22px;flex-shrink:0;background:#ecf0f1;cursor:col-resize;display:flex;align-items:center;justify-content:center;border-left:1px solid #d0d7de;border-right:1px solid #d0d7de;user-select:none;position:relative;z-index:2}
.sp12:hover{background:#d6eaff}
.sp12-btn{background:none;border:none;font-size:14px;cursor:pointer;color:#2c3e50;line-height:1;padding:0;display:block}
#panel2,.panel2-side{width:400px;min-width:180px;flex-shrink:0;background:#fff;display:flex;flex-direction:column;overflow:hidden;border-right:2px solid #d0d7de;transition:width 0.15s}
#panel2.p2-hidden,.panel2-side.p2-hidden{width:0!important;min-width:0!important;overflow:hidden;border:none}
.p2-hdr{background:#34495e;color:#fff;padding:5px 10px;font-size:11px;font-weight:bold;flex-shrink:0}
.p2-body{flex:1;overflow:auto}
.sp23{width:5px;flex-shrink:0;background:#d0d7de;cursor:col-resize;align-self:stretch;transition:background .15s;user-select:none}
.sp23:hover,.sp23.dragging{background:#2980b9}
#panel3{flex:1;min-width:0;overflow-y:auto;overflow-x:hidden;background:#f0f2f5;padding:6px}
.grp-card{background:#fff;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.10);margin-bottom:10px;overflow:hidden;content-visibility:auto;contain-intrinsic-size:0 560px}
.grp-card-hdr{display:flex;align-items:center;gap:6px;padding:4px 10px;font-size:11px;font-weight:bold;color:#ecf0f1;background:#34495e;cursor:pointer;user-select:none}
.grp-card-body{padding:0}
.grp-card.gc-collapsed .grp-card-body{display:none}
/* filter table rows */
.lot-hdr td{background:#34495e!important;color:#ecf0f1!important}
.fp{padding:3px 8px;white-space:nowrap;border-bottom:1px solid #eee}
.fr:hover td{background:#eaf4ff!important;cursor:pointer}
.frs td{background:#d6eaff!important;font-weight:bold}
.frs:hover td{background:#bcd8f8!important}
/* XY custom autocomplete */
.xy-ac-wrap{position:relative;display:inline-block}
.xy-ac-pop{position:absolute;z-index:9999;background:#fff;border:1px solid #bdc3c7;border-radius:4px;box-shadow:0 4px 14px rgba(0,0,0,.18);max-height:280px;overflow-y:auto;min-width:260px;width:max-content;display:none;top:100%;left:0}
.xy-ac-item{padding:5px 10px;cursor:pointer;font-size:12px;white-space:nowrap;line-height:1.4}
.xy-ac-item:hover,.xy-ac-item.ac-hi{background:#d6eaff}
/* ── Param detail modal ── */
.pm-overlay{position:fixed;inset:0;background:rgba(10,14,26,0.72);z-index:10000;display:none;align-items:center;justify-content:center}
.pm-card{background:#fff;border-radius:8px;box-shadow:0 8px 40px rgba(0,0,0,.45);width:min(96vw,860px);max-height:92vh;display:flex;flex-direction:column;overflow:hidden}
.pm-hdr{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;background:#2c3e50;color:#fff;flex-shrink:0}
.pm-hdr-title{font-size:13px;font-weight:bold;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;margin-right:8px}
.pm-close{background:none;border:1px solid #7f8c8d;color:#bdc3c7;font-size:16px;line-height:1;padding:2px 8px;border-radius:4px;cursor:pointer}
.pm-close:hover{background:#e74c3c;border-color:#e74c3c;color:#fff}
.pm-body{flex:1;overflow-y:auto;padding:12px 16px;background:#f0f2f5}
.pm-stat-row{display:flex;flex-wrap:wrap;background:#fff;border:1px solid #e0e0e0;border-radius:5px;margin-bottom:10px;overflow:hidden}
.pm-stat{display:inline-flex;flex-direction:column;align-items:center;gap:1px;padding:5px 14px;border-right:1px solid #eee}
.pm-stat-lbl{font-size:9px;color:#888;font-weight:bold;text-transform:uppercase;white-space:nowrap}
.pm-stat-val{font-size:14px;font-weight:bold}
.pm-grp-leg{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:11px;margin-top:6px;padding:4px 2px}
"""


# ---------------------------------------------------------------------------
# JavaScript  — 3-panel design
# ---------------------------------------------------------------------------
_JS = r"""
/* ── Utilities ──────────────────────────────────────────────────────────── */
function esc(s){
  return String(s==null?'':s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
/* ── Global tooltip element ─────────────────────────────────────────────── */
var _TT=null;
function _getTT(){
  if(!_TT){
    _TT=document.createElement('div');
    _TT.style.cssText='position:fixed;background:rgba(20,28,40,0.93);color:#ecf0f1;font-size:12px;'
      +'padding:5px 11px;border-radius:5px;pointer-events:none;z-index:9999;display:none;'
      +'white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,.4);border:1px solid #4a6278';
    document.body.appendChild(_TT);
  }
  return _TT;
}
function _med(arr){
  if(!arr||!arr.length)return null;
  var s=arr.slice().sort(function(a,b){return a-b;});
  var m=Math.floor(s.length/2);
  return s.length%2?s[m]:(s[m-1]+s[m])/2;
}
function _std(arr){
  if(!arr||arr.length<2)return 0;
  var mn=arr.reduce(function(a,b){return a+b;},0)/arr.length;
  return Math.sqrt(arr.reduce(function(a,b){return a+(b-mn)*(b-mn);},0)/(arr.length-1));
}
function _niceStep(r){
  if(r<=0||!isFinite(r))return 0.1;
  var m=Math.pow(10,Math.floor(Math.log10(r)));
  var s=r/m;
  return s<1.5?m:s<3?2*m:s<7?5*m:10*m;
}
function _fmt(v){
  if(v==null||isNaN(v)||!isFinite(v))return '';
  if(Math.abs(v)>0&&(Math.abs(v)<1e-4||Math.abs(v)>=1e7))return v.toExponential(3);
  return parseFloat(v.toPrecision(4)).toString();
}
function _sRand(s){var x=Math.sin(s+1)*10000;return x-Math.floor(x);}
function _safeMin(a){var m=Infinity;for(var _i=0;_i<a.length;_i++)if(a[_i]<m)m=a[_i];return m;}
function _safeMax(a){var m=-Infinity;for(var _i=0;_i<a.length;_i++)if(a[_i]>m)m=a[_i];return m;}

var _CPALS=['#2980b9','#27ae60','#e67e22','#8e44ad','#c0392b',
            '#16a085','#f39c12','#1abc9c','#d35400','#7f8c8d',
            '#3498db','#2ecc71','#e74c3c','#9b59b6','#f0a500'];
function _cPal(i){return _CPALS[i%_CPALS.length];}
/* Row identity key — includes program so the same wafer under two programs
   produces two distinct UNIQ_WFR entries and two distinct chart series. */
function _rKey(r){return r.lot+'|'+(r.layout||'')+'|'+(r.program||'')+'|'+r.wafer+'|'+(r.material||'');}

/* ── Unique wafer rows ──────────────────────────────────────────────────── */
/* Each entry: {lot, wafer, layout, material, program, key, n} */
var UNIQ_WFR=(function(){
  var seen={},out=[];
  PCM_ROWS.forEach(function(r){
    var k=_rKey(r);
    if(!seen[k]){
      seen[k]=out.length;
      out.push({lot:r.lot,wafer:r.wafer,sort_wafer:r.sort_wafer||r.wafer,layout:r.layout||'',material:r.material||'',program:r.program||'',key:k,n:r.n||0});
    }else{
      var idx=seen[k];
      if((r.n||0)>out[idx].n)out[idx].n=r.n;
    }
  });
  return out;
})();

/* ── Filter search state ────────────────────────────────────────────────── */
var _FSRCH={program:'',lot:'',wafer:'',layout:'',material:''};
var _SHOW_SEL=false;
function _matchSearch(w,wi){
  if(_SHOW_SEL&&!SEL_WFR.has(wi))return false;
  var pg=_FSRCH.program.toLowerCase(),lo=_FSRCH.lot.toLowerCase(),wr=_FSRCH.wafer.toLowerCase(),ly=_FSRCH.layout.toLowerCase(),mt=_FSRCH.material.toLowerCase();
  return(!pg||(w.program||'').toLowerCase().indexOf(pg)>=0)&&
         (!lo||w.lot.toLowerCase().indexOf(lo)>=0)&&
         (!wr||(w.sort_wafer||w.wafer).toLowerCase().indexOf(wr)>=0)&&
         (!ly||(w.layout||'').toLowerCase().indexOf(ly)>=0)&&
         (!mt||(w.material||'').toLowerCase().indexOf(mt)>=0);
}
function onSearch(field,val){_FSRCH[field]=val;buildWfrList();}
function toggleShowSel(){
  _SHOW_SEL=!_SHOW_SEL;
  var btn=document.getElementById('show-sel-btn');
  if(btn){btn.style.background=_SHOW_SEL?'#2980b9':'none';btn.style.color=_SHOW_SEL?'#fff':'#bdc3c7';}
  buildWfrList();
}

/* ── Selection ──────────────────────────────────────────────────────────── */
/* Pre-select all wafers by default */
var SEL_WFR=new Set();
UNIQ_WFR.forEach(function(_,i){SEL_WFR.add(i);});
var _tblLastWfr=-1;

function _visIndices(){
  var vis=[];
  UNIQ_WFR.forEach(function(w,i){if(_matchSearch(w,i))vis.push(i);});
  return vis;
}

function activeKeys(){
  var s=new Set();SEL_WFR.forEach(function(wi){s.add(UNIQ_WFR[wi].key);});return s;
}

/* _lotCollapsed keyed by lot STRING (stable across filter changes).
   Default: collapsed (true). _curLotOrder maps li→lot for current render. */
var _lotCollapsed={};
var _curLotOrder=[];

function buildWfrList(){
  var vis=_visIndices();
  var byProg={},progOrder=[];
  vis.forEach(function(wi){
    var prog=UNIQ_WFR[wi].program||UNIQ_WFR[wi].lot;
    if(!byProg[prog]){byProg[prog]=[];progOrder.push(prog);}
    byProg[prog].push(wi);
  });

  /* Save for toggleLot reference; default new programs to expanded */
  _curLotOrder=progOrder;
  progOrder.forEach(function(prog){
    if(_lotCollapsed[prog]===undefined)_lotCollapsed[prog]=false;
  });

  var html='';
  var indetermLots=[];  /* lots needing indeterminate checkbox after render */
  progOrder.forEach(function(prog,li){
    var rowsInLot=byProg[prog];
    var selCnt=rowsInLot.filter(function(wi){return SEL_WFR.has(wi);}).length;
    var allSel=selCnt===rowsInLot.length;
    var anySel=selCnt>0;
    var isCol=(_lotCollapsed[prog]===true);   /* default expanded */
    if(anySel&&!allSel)indetermLots.push(li); /* track partial */
    /* Aggregate layout/material/lot labels + total N across all wafers in this program group */
    var _lotLays=[],_lotMats=[],_lotLotArr=[];
    rowsInLot.forEach(function(wi){var ly=UNIQ_WFR[wi].layout||'';if(_lotLays.indexOf(ly)<0)_lotLays.push(ly);});
    rowsInLot.forEach(function(wi){var m=UNIQ_WFR[wi].material||'';if(_lotMats.indexOf(m)<0)_lotMats.push(m);});
    rowsInLot.forEach(function(wi){var l=UNIQ_WFR[wi].lot||'';if(l&&_lotLotArr.indexOf(l)<0)_lotLotArr.push(l);});
    var _layLbl=_lotLays.length===1?_lotLays[0]:(_lotLays[0]||'')+(_lotLays.length>1?' \u2026':'');
    var _matLbl=_lotMats.length===1?_lotMats[0]:(_lotMats[0]||'')+(_lotMats.length>1?' \u2026':'');
    var _lotLbl=_lotLotArr.join(', ');
    var _totN=rowsInLot.reduce(function(s,wi){return s+(UNIQ_WFR[wi].n||0);},0);
    html+='<tr class="lot-hdr" onclick="toggleLot('+li+')">'
      +'<td style="padding:4px 8px;background:#34495e;color:#ecf0f1;font-weight:bold;cursor:pointer;user-select:none;word-break:break-all">'
      +'<span id="lot-arr-'+li+'" style="margin-right:4px">'+(isCol?'&#9658;':'&#9660;')+'</span>'
      +'<input type="checkbox" id="lot-cb-'+li+'" style="vertical-align:middle;margin-right:4px" '
      +(allSel?'checked':'')+' onclick="selLot(event,\''+esc(prog)+'\')">'
      +esc(prog)+' <span style="font-size:10px;color:#95a5a6;font-weight:normal">'
      +'('+selCnt+'/'+rowsInLot.length+')</span>'
      +'</td>'
      +'<td style="background:#34495e;color:#aed6f1;font-size:10px;padding:4px 8px;cursor:pointer;word-break:break-all" title="'+esc(_lotLbl)+'">'+esc(_lotLbl)+'</td>'
      +'<td style="background:#34495e;font-size:10px;padding:4px 8px"></td>'
      +'<td style="background:#34495e;color:#bdc3c7;font-size:10px;padding:4px 8px;cursor:pointer;word-break:break-all" title="'+esc(_layLbl)+'">'+esc(_layLbl)+'</td>'
      +'<td style="background:#34495e;color:#bdc3c7;font-size:10px;padding:4px 8px;cursor:pointer;word-break:break-all" title="'+esc(_matLbl)+'">'+esc(_matLbl)+'</td>'
      +'<td class="num" style="background:#34495e;color:#bdc3c7;font-size:10px;padding:4px 8px;cursor:pointer">'+_totN+'</td>'
      +'</tr>';
    rowsInLot.forEach(function(wi){
      var w=UNIQ_WFR[wi];
      var isSel=SEL_WFR.has(wi);
      html+='<tr class="fr'+(isSel?' frs':'')+'" data-li="'+li+'"'
        +(isCol?' style="display:none"':'')+' onclick="toggleWfr('+wi+',event)">'
        +'<td class="fp" style="color:#7f8c8d;font-size:10px;word-break:break-all" title="'+esc(w.program||'')+'">'+esc(w.program||'')+'</td>'
        +'<td class="fp">'+esc(w.lot)+'</td>'
        +'<td class="fp">'+esc(w.sort_wafer||w.wafer)+'</td>'
        +'<td class="fp" style="color:#7f8c8d;font-size:10px;word-break:break-all" title="'+esc(w.layout||'')+'">'+esc(w.layout||'')+'</td>'
        +'<td class="fp" style="color:#7f8c8d;font-size:10px;word-break:break-all" title="'+esc(w.material||'')+'">'+esc(w.material||'')+'</td>'
        +'<td class="num fp">'+w.n+'</td>'
        +'</tr>';
    });
  });

  var tbody=document.getElementById('wfr-tbody');
  if(tbody)tbody.innerHTML=html;

  /* Set indeterminate on partial-selection lot checkboxes */
  indetermLots.forEach(function(li){
    var cb=document.getElementById('lot-cb-'+li);
    if(cb)cb.indeterminate=true;
  });

  /* Update selection badge */
  var ri=document.getElementById('row-info');
  if(ri){
    var s=SEL_WFR.size,t=UNIQ_WFR.length;
    ri.textContent=(s>0&&s<t)?'('+s+'/'+t+' selected)':'';
  }
}

/* Accordion toggle: expand clicked lot, collapse all others.
   If lot is already open, clicking again collapses it. */
function toggleLot(li){
  var lot=_curLotOrder[li];if(!lot)return;
  var wasOpen=(_lotCollapsed[lot]===false);
  /* Collapse all */
  _curLotOrder.forEach(function(l){_lotCollapsed[l]=true;});
  /* If it was closed, open it now */
  if(!wasOpen)_lotCollapsed[lot]=false;
  buildWfrList();
}

/* Select/deselect all wafers in a program group (operates on ALL wafers for that
   program, not just visible ones, so it works without opening the group) */
function selLot(ev,lot){
  ev.stopPropagation();
  var lotWfrs=[];
  UNIQ_WFR.forEach(function(w,wi){if((w.program||w.lot)===lot)lotWfrs.push(wi);});
  var allSel=lotWfrs.every(function(wi){return SEL_WFR.has(wi);});
  lotWfrs.forEach(function(wi){if(allSel)SEL_WFR.delete(wi);else SEL_WFR.add(wi);});
  buildWfrList();rerender();
}
function selLotAll(ev,lot){
  ev.stopPropagation();
  UNIQ_WFR.forEach(function(w,wi){if((w.program||w.lot)===lot)SEL_WFR.add(wi);});
  buildWfrList();rerender();
}
function selLotClr(ev,lot){
  ev.stopPropagation();
  UNIQ_WFR.forEach(function(w,wi){if((w.program||w.lot)===lot)SEL_WFR.delete(wi);});
  buildWfrList();rerender();
}

function toggleWfr(wi,ev){
  var vis=_visIndices();
  if(ev&&ev.shiftKey&&_tblLastWfr>=0){
    var lo2=Math.min(wi,_tblLastWfr),hi2=Math.max(wi,_tblLastWfr);
    for(var i=lo2;i<=hi2;i++)if(vis.indexOf(i)>=0)SEL_WFR.add(i);
  }else{if(SEL_WFR.has(wi))SEL_WFR.delete(wi);else SEL_WFR.add(wi);}
  _tblLastWfr=wi;buildWfrList();rerender();
}
function selAll(){_visIndices().forEach(function(i){SEL_WFR.add(i);});buildWfrList();rerender();}
function clrAll(){_visIndices().forEach(function(i){SEL_WFR.delete(i);});buildWfrList();rerender();}

/* ── Group-by (colour) ──────────────────────────────────────────────────── */
var VAR_GBY=[];
function toggleGby(field){
  if(field==='none'){VAR_GBY=[];}
  else{var i=VAR_GBY.indexOf(field);if(i>=0)VAR_GBY.splice(i,1);else VAR_GBY.push(field);}
  /* Sync all group-by checkbox sets (global bar + XY tab) */
  document.querySelectorAll('.vgb-cb').forEach(function(cb){
    if(cb.value==='none')cb.checked=VAR_GBY.length===0;
    else cb.checked=VAR_GBY.indexOf(cb.value)>=0;
  });
  rerender();
}
function _grpKey(r){
  if(!VAR_GBY.length)return 'All';
  var parts=[];
  if(VAR_GBY.indexOf('lot')>=0)parts.push(r.lot||'');
  if(VAR_GBY.indexOf('wafer')>=0)parts.push(String(r.wafer||''));
  if(VAR_GBY.indexOf('layout')>=0)parts.push(r.layout||'');
  if(VAR_GBY.indexOf('material')>=0)parts.push(r.material||'');
  return parts.join('/')||'All';
}
function _cMap(){
  var map={},keys=[];
  var ak=activeKeys();
  PCM_ROWS.forEach(function(r){
    if(!ak.has(_rKey(r)))return;
    var k=_grpKey(r);
    if(!map[k]){map[k]=_cPal(keys.length);keys.push(k);}
  });
  return {map:map,keys:keys};
}
/* ── Per-panel independent group-by helpers ─────────────────────────────── */
function _grpKeyWith(r,gby){
  if(!gby||!gby.length)return 'All';
  var parts=[];
  if(gby.indexOf('lot')>=0)parts.push(r.lot||'');
  if(gby.indexOf('wafer')>=0)parts.push(String(r.wafer||''));
  if(gby.indexOf('layout')>=0)parts.push(r.layout||'');
  if(gby.indexOf('material')>=0)parts.push(r.material||'');
  return parts.join('/')||'All';
}
function _cMapWith(gby){
  var map={},keys=[];
  var ak=activeKeys();
  PCM_ROWS.forEach(function(r){
    if(!ak.has(_rKey(r)))return;
    var k=_grpKeyWith(r,gby);
    if(!map[k]){map[k]=_cPal(keys.length);keys.push(k);}
  });
  return {map:map,keys:keys};
}

/* ── Group visibility ───────────────────────────────────────────────────── */
var _GRP_VIS={};
PCM_GROUPS.forEach(function(g){
  _GRP_VIS[g]=(PCM_DEFAULT_GROUPS.length===0||PCM_DEFAULT_GROUPS.indexOf(g)>=0);
});
// Safety: if no default matched any actual group, show all groups
if(PCM_DEFAULT_GROUPS.length>0&&!PCM_GROUPS.some(function(g){return _GRP_VIS[g];})){
  PCM_GROUPS.forEach(function(g){_GRP_VIS[g]=true;});
}
function toggleGroup(btn,grp){
  _GRP_VIS[grp]=!_GRP_VIS[grp];
  btn.classList.toggle('grp-off',!_GRP_VIS[grp]);
  rerender();
}
function setAllGroups(visible){
  PCM_GROUPS.forEach(function(grp){_GRP_VIS[grp]=visible;});
  document.querySelectorAll('.wfr-btn[onclick*="toggleGroup"]').forEach(function(btn){
    btn.classList.toggle('grp-off',!visible);
  });
  rerender();
}
function activeParamsForGroup(grp){return PCM_GROUP_PARAMS[grp]||[];}

/* ── Selected param ─────────────────────────────────────────────────────── */
var SEL_PARAM=null;
function selParam(param){
  SEL_PARAM=(SEL_PARAM===param)?null:param;
  rerender();
  if(param)_showParamModal(param);
}

/* ── Param detail modal ─────────────────────────────────────────────────── */
function _showParamModal(param){
  var overlay=document.getElementById('pm-overlay');
  if(!overlay)return;
  var titleEl=document.getElementById('pm-title');
  var meta=PCM_PARAM_META[param]||{};
  if(titleEl)titleEl.textContent=param+(meta.name?' — '+meta.name:'');
  _buildParamModalChart(param);
  overlay.style.display='flex';
}
function _closeParamModal(){
  var overlay=document.getElementById('pm-overlay');
  if(overlay)overlay.style.display='none';
}
document.addEventListener('keydown',function(e){if(e.key==='Escape')_closeParamModal();});

function _buildParamModalChart(param){
  var cont=document.getElementById('pm-body');
  if(!cont)return;
  var meta=PCM_PARAM_META[param]||{};
  var ak=activeKeys();
  var cm=_cMap();
  var isTd=!!param.match(/^Td_/i);
  var tgt=isTd?(meta.target!=null?meta.target:((meta.lsl!=null&&meta.usl!=null)?(meta.lsl+meta.usl)/2:null)):null;
  /* Collect values per group-by key */
  var grpVals={},grpOrder=[];
  PCM_ROWS.forEach(function(r){
    if(r.param!==param)return;
    if(!ak.has(_rKey(r)))return;
    var gk=_grpKey(r);
    if(!grpVals[gk]){grpVals[gk]=[];grpOrder.push(gk);}
    (r.die_values||[]).forEach(function(v){if(v!=null&&isFinite(v))grpVals[gk].push(v);});
  });
  /* Convert Td_ to % of target */
  function _norm(arr){
    if(isTd&&tgt&&tgt!==0)return arr.map(function(v){return v!==0?(tgt/v*100):null;}).filter(function(v){return v!=null&&isFinite(v);});
    return arr;
  }
  var allRaw=[];
  grpOrder.forEach(function(gk){allRaw=allRaw.concat(grpVals[gk]);});
  var normAll=_norm(allRaw);
  if(!normAll.length){
    cont.innerHTML='<div style="padding:24px;color:#888;text-align:center">No data for active selection</div>';
    return;
  }
  /* Clip to P1/P99 for range and dispersion stats */
  var srt=normAll.slice().sort(function(a,b){return a-b;});
  var p01=srt[Math.floor(srt.length*0.01)];
  var p99=srt[Math.min(srt.length-1,Math.ceil(srt.length*0.99))];
  var clipped=(srt.length>=10&&p99>p01)?normAll.filter(function(v){return v>=p01&&v<=p99;}):normAll;
  var med=_med(normAll),sd=_std(clipped),mn=_safeMin(clipped),mx=_safeMax(clipped);
  var cv=(med&&med!==0)?Math.abs(sd/med*100):null;
  var lsl=meta.lsl,usl=meta.usl;
  var unit=isTd?'% of tgt':(_isLeakage(param)?(_leakageScale([(meta.target||meta.usl||1e-6)]).unit):(meta.unit||''));
  /* Histogram parameters */
  var rng=mx-mn||Math.abs(mn)*0.02||0.1;
  var nBins=Math.max(12,Math.min(50,Math.ceil(Math.sqrt(normAll.length)*2.5)));
  var binW=rng/nBins;
  var xPad=Math.max(rng*0.06,binW*0.5);
  var xLo=mn-xPad,xHi=mx+xPad,xRng=xHi-xLo;
  /* Include spec lines in axis range if nearby */
  if(lsl!=null&&lsl>=xLo-5*rng)xLo=Math.min(xLo,lsl-xPad);
  if(usl!=null&&usl<=xHi+5*rng)xHi=Math.max(xHi,usl+xPad);
  xRng=xHi-xLo;
  /* Per-group histograms */
  var grpNorm={};
  grpOrder.forEach(function(gk){grpNorm[gk]=_norm(grpVals[gk]);});
  var grpCounts={},maxCnt=1;
  grpOrder.forEach(function(gk){
    var nv=grpNorm[gk].filter(function(v){return v>=mn&&v<=mx;});
    var cnts=new Array(nBins).fill(0);
    nv.forEach(function(v){
      var bi=Math.min(Math.floor((v-mn)/binW),nBins-1);
      if(bi>=0&&bi<nBins)cnts[bi]++;
    });
    grpCounts[gk]=cnts;
    var gc=_safeMax(cnts)||0;
    if(gc>maxCnt)maxCnt=gc;
  });
  var maxY=Math.ceil(maxCnt*1.15);
  /* SVG */
  var svgW=820,svgH=300,ML=64,MR=20,MT=36,MB=68;
  var plotW=svgW-ML-MR,plotH=svgH-MT-MB;
  function xp(v){return ML+(v-xLo)/xRng*plotW;}
  function yp(c){return MT+plotH-(c/maxY)*plotH;}
  var p=['<svg width="100%" viewBox="0 0 '+svgW+' '+svgH+'" style="display:block;background:#f8f9fa">' ];
  p.push('<rect x="'+ML+'" y="'+MT+'" width="'+plotW+'" height="'+plotH+'" fill="#fff" stroke="#ccc" stroke-width="1"/>');
  /* Y grid */
  for(var yi=0;yi<=5;yi++){
    var yv=Math.round(maxY*yi/5);
    var ypv=(MT+plotH-(yv/maxY)*plotH).toFixed(1);
    p.push('<line x1="'+ML+'" y1="'+ypv+'" x2="'+(ML+plotW)+'" y2="'+ypv+'" stroke="rgba(0,0,0,0.09)" stroke-width="0.8"/>');
    p.push('<text x="'+(ML-4)+'" y="'+ypv+'" text-anchor="end" dominant-baseline="middle" font-size="14" fill="#555">'+yv+'</text>');
  }
  p.push('<text transform="translate(13,'+(MT+plotH/2)+') rotate(-90)" text-anchor="middle" font-size="14" fill="#555">Count</text>');
  /* Bars */
  var nGrps=grpOrder.length;
  var bpxW=binW/xRng*plotW;
  var barW=Math.max(0.5,(bpxW-1)/Math.max(1,nGrps));
  grpOrder.forEach(function(gk,gi){
    var gcol=cm.map[gk]||_cPal(gi);
    var cnts=grpCounts[gk];
    var offsetX=(nGrps>1)?((gi-(nGrps-1)/2)*barW):0;
    for(var b=0;b<nBins;b++){
      if(!cnts[b])continue;
      var bx=(xp(mn+b*binW)+offsetX).toFixed(1);
      var bh=(cnts[b]/maxY*plotH).toFixed(1);
      var by=(MT+plotH-cnts[b]/maxY*plotH).toFixed(1);
      p.push('<rect x="'+bx+'" y="'+by+'" width="'+(Math.max(0.5,barW-0.5)).toFixed(1)+'" height="'+bh+'" fill="'+gcol+'" opacity="0.72" rx="1"/>');
    }
  });
  /* Spec / median vertical lines */
  function _vline(val,col,lbl,lblSide){
    var xv=xp(val).toFixed(1);
    if(parseFloat(xv)<ML-2||parseFloat(xv)>ML+plotW+2)return;
    p.push('<line x1="'+xv+'" y1="'+MT+'" x2="'+xv+'" y2="'+(MT+plotH)+'" stroke="'+col+'" stroke-width="2" stroke-dasharray="5,4"/>');
    var anchor=(lblSide==='right')?'start':'end';
    var tx=(lblSide==='right')?(parseFloat(xv)+4):(parseFloat(xv)-4);
    p.push('<text x="'+tx+'" y="'+(MT-7)+'" text-anchor="'+anchor+'" font-size="13" font-weight="bold" fill="'+col+'">'+esc(lbl)+'</text>');
  }
  if(lsl!=null)_vline(lsl,'#c0392b','LSL','right');
  if(usl!=null)_vline(usl,'#2980b9','USL','left');
  if(med!=null)_vline(med,'#27ae60','Median','right');
  /* X ticks */
  for(var xi=0;xi<=7;xi++){
    var xv=xLo+xRng*xi/7;
    var xpv=(ML+xi/7*plotW).toFixed(1);
    p.push('<text x="'+xpv+'" y="'+(MT+plotH+18)+'" text-anchor="middle" font-size="13" fill="#555">'+_fmt(xv)+'</text>');
  }
  /* X axis label */
  var unitLbl=unit?' ('+esc(unit)+')':'';
  p.push('<text x="'+(ML+plotW/2)+'" y="'+(svgH-4)+'" text-anchor="middle" font-size="14" font-weight="bold" fill="#333">'+esc(param)+esc(unitLbl)+'</text>');
  p.push('</svg>');
  /* Strip chart: one dot per (lot, wafer) median, colored by group-by */
  var sW=svgW,sH=70,sML=ML,sMR=MR,sMT=18,sMB=14;
  var sPlotW=sW-sML-sMR,sPlotH=sH-sMT-sMB;
  var ps=['<svg width="100%" viewBox="0 0 '+sW+' '+sH+'" style="display:block;background:#fff;border-top:1px solid #e8e8e8">' ];
  ps.push('<rect x="'+sML+'" y="'+sMT+'" width="'+sPlotW+'" height="'+sPlotH+'" fill="#f8f9fa" rx="2"/>');
  if(lsl!=null){var lx=xp(lsl).toFixed(1);ps.push('<line x1="'+lx+'" y1="'+sMT+'" x2="'+lx+'" y2="'+(sMT+sPlotH)+'" stroke="#c0392b" stroke-width="1.5" stroke-dasharray="4,3"/>');}  
  if(usl!=null){var ux=xp(usl).toFixed(1);ps.push('<line x1="'+ux+'" y1="'+sMT+'" x2="'+ux+'" y2="'+(sMT+sPlotH)+'" stroke="#2980b9" stroke-width="1.5" stroke-dasharray="4,3"/>');}  
  /* Median line */
  if(med!=null){var mx2=xp(med).toFixed(1);ps.push('<line x1="'+mx2+'" y1="'+sMT+'" x2="'+mx2+'" y2="'+(sMT+sPlotH)+'" stroke="#27ae60" stroke-width="2" stroke-dasharray="5,3"/>');}  
  /* IQR box */
  if(srt.length>=4){
    var q1=srt[Math.floor(srt.length*0.25)],q3=srt[Math.min(srt.length-1,Math.ceil(srt.length*0.75))];
    var qx1=Math.max(sML,xp(q1)),qx2=Math.min(sML+sPlotW,xp(q3));
    if(qx2>qx1)ps.push('<rect x="'+qx1.toFixed(1)+'" y="'+sMT+'" width="'+(qx2-qx1).toFixed(1)+'" height="'+sPlotH+'" fill="rgba(39,174,96,0.12)" stroke="#27ae60" stroke-width="1"/>');
  }
  /* Dots per group */
  var stripDots={};
  grpOrder.forEach(function(gk,gi){
    var gcol=cm.map[gk]||_cPal(gi);
    var vals=grpNorm[gk];
    vals.forEach(function(v,vi){
      if(v<xLo||v>xHi)return;
      var cx=xp(v).toFixed(1);
      var jitter=((_sRand(gi*997+vi)-0.5)*sPlotH*0.7);
      var cy=(sMT+sPlotH/2+jitter).toFixed(1);
      if(!stripDots[gcol])stripDots[gcol]='';
      stripDots[gcol]+='M'+cx+','+cy+'m-3,0a3,3,0,1,0,6,0a3,3,0,1,0,-6,0';
    });
  });
  Object.keys(stripDots).forEach(function(col){ps.push('<path d="'+stripDots[col]+'" fill="'+col+'" opacity="0.60"/>');}); 
  ps.push('<text x="'+sML+'" y="12" font-size="11" fill="#888">Strip (each dot = one measurement)</text>');
  ps.push('</svg>');
  /* Stats row */
  function _sb(lbl,val,col){
    return '<div class="pm-stat"><span class="pm-stat-lbl">'+lbl+'</span>'
      +'<span class="pm-stat-val" style="color:'+(col||'#2c3e50')+'">'+val+'</span></div>';
  }
  var statsHtml='<div class="pm-stat-row">'
    +_sb('N',normAll.length)
    +_sb('Median',_fmt(med),'#27ae60')
    +_sb('\u03c3',_fmt(sd))
    +_sb('Spread (%)',cv!=null?cv.toFixed(1)+'%':'\u2014')
    +_sb('P1',p01!=null?_fmt(p01):'\u2014','#7f8c8d')
    +_sb('P99',p99!=null?_fmt(p99):'\u2014','#7f8c8d')
    +(lsl!=null?_sb('LSL',_fmt(lsl),'#c0392b'):'')
    +(usl!=null?_sb('USL',_fmt(usl),'#2980b9'):'')
    +(unit?_sb('Unit',esc(unit),'#555'):'')
    +'</div>';
  /* Group legend */
  var legHtml='';
  if(grpOrder.length>1){
    legHtml='<div class="pm-grp-leg">';
    grpOrder.forEach(function(gk,gi){
      var gcol=cm.map[gk]||_cPal(gi);
      legHtml+='<span style="display:flex;align-items:center;gap:3px">'
        +'<span style="width:10px;height:10px;background:'+gcol+';display:inline-block;border-radius:2px"></span>'
        +esc(gk)+'</span>';
    });
    legHtml+='</div>';
  }
  cont.innerHTML=statsHtml+p.join('')+ps.join('')+legHtml;
}

/* ── Aggregate stats ────────────────────────────────────────────────────── */
/* Auto-scale Poff_/Ioff_ leakage params: returns {scale, unit} */
function _leakageScale(vals){
  var mx=_safeMax(vals.map(Math.abs));
  if(mx<1e-6)return{scale:1e9,unit:'nA'};
  if(mx<1e-3)return{scale:1e6,unit:'\u00b5A'};
  return{scale:1e3,unit:'mA'};
}
function _isLeakage(param){return /^(Poff_|Ioff_)/i.test(param);}
function _isSiccCdyn(p){var lo=p.toLowerCase();return lo.indexOf('sicc')>=0||lo.indexOf('cdyn')>=0;}
/* Convert raw Td_ values → frequency % of target (tgt/v*100).
   Poff_/Ioff_ → nA or µA for better axis spread.
   Returns the converted array, or the original array if not a special param. */
function _toDisplayVals(param,vals){
  if(param.match(/^Td_/i)){
    var _m=PCM_PARAM_META[param]||{};
    var tgt=_m.target!=null?_m.target:((_m.lsl!=null&&_m.usl!=null)?(_m.lsl+_m.usl)/2:null);
    if(!tgt||tgt===0)return vals;
    return vals.map(function(v){return v!==0?(tgt/v*100):null;}).filter(function(v){return v!=null&&isFinite(v);});
  }
  if(_isLeakage(param)&&vals.length){
    var sc=_leakageScale(vals).scale;
    return vals.map(function(v){return v*sc;});
  }
  return vals;
}
function _paramStats(param){
  var ak=activeKeys(),vals=[];
  PCM_ROWS.forEach(function(r){
    if(r.param!==param)return;
    if(!ak.has(_rKey(r)))return;
    (r.die_values||[]).forEach(function(v){if(v!=null&&isFinite(v))vals.push(v);});
  });
  if(!vals.length)return null;
  var dv=_toDisplayVals(param,vals);
  if(!dv.length)return null;
  var med=_med(dv);
  /* Clip to P1/P99 for σ, Spread (%), min, max — same as the strip chart —
     so extreme outliers don't inflate dispersion stats.
     N and Median are reported from the full dataset (median is robust). */
  var clipped=dv;
  if(dv.length>=10){
    var _srt=dv.slice().sort(function(a,b){return a-b;});
    var _p01=_srt[Math.floor(_srt.length*0.01)];
    var _p99=_srt[Math.min(_srt.length-1,Math.ceil(_srt.length*0.99))];
    if(_p99>_p01)clipped=dv.filter(function(v){return v>=_p01&&v<=_p99;});
  }
  var sd=_std(clipped);
  var cv=(med&&med!==0)?Math.abs(sd/med*100):null;
  return{n:dv.length,median:med,std:sd,cv:cv,min:_safeMin(clipped),max:_safeMax(clipped)};
}

/* ── Panel 2: parameter table ───────────────────────────────────────────── */
var _GRP_STATE={};
function _buildParamTableInto(headId,bodyId){
  var thead=document.getElementById(headId);
  var tbody=document.getElementById(bodyId);
  if(!thead||!tbody)return;
  thead.innerHTML='<tr>'
    +'<th style="text-align:left;min-width:160px;position:sticky;left:0;z-index:2;background:#2c3e50">Parameter</th>'
    +'<th style="min-width:44px">N</th>'
    +'<th style="min-width:70px">Median</th>'
    +'<th style="min-width:50px">&sigma;</th>'
    +'<th style="min-width:60px">Spread (%)</th>'
    +'<th style="min-width:58px">Min</th>'
    +'<th style="min-width:58px">Max</th>'
    +'<th style="min-width:50px">LSL</th>'
    +'<th style="min-width:50px">USL</th>'
    +'<th style="min-width:34px">Unit</th>'
    +'</tr>';
  var html='';
  PCM_GROUPS.forEach(function(grp){
    if(!_GRP_VIS[grp])return;
    var params=PCM_GROUP_PARAMS[grp]||[];
    var collapsed=_GRP_STATE[grp]===false;
    html+='<tr class="cat-hdr" onclick="toggleGrpRow(\''+esc(grp)+'\')"><td colspan="10">'
      +(collapsed?'&#9658;':'&#9660;')+' '+esc(grp)
      +' <span style="font-weight:normal;font-size:10px;color:#aed6f1">('+params.length+')</span>'
      +'</td></tr>';
    params.forEach(function(param){
      var meta=PCM_PARAM_META[param]||{},st=_paramStats(param);
      var isSel=SEL_PARAM===param;
      var cls=collapsed?'grp-hidden':'';
      var lsl=meta.lsl,usl=meta.usl,unit=meta.unit||'';
      var medCls='';
      if(st){if(lsl!=null&&st.median<lsl)medCls=' cell-r';else if(usl!=null&&st.median>usl)medCls=' cell-r';}
      html+='<tr class="'+(cls+(isSel?' sel-row':'')).trim()+'" onclick="selParam(\''+esc(param)+'\')">';
      var _pname=meta.name?meta.name:'';
      var _pdisp=_pname
        ?esc(param)+'<span style="color:#7f8c8d;font-size:10px;font-weight:normal;margin-left:4px">('+esc(_pname)+')</span>'
        :esc(param);
      html+='<td class="tn'+(isSel?' sel':'')+'" title="'+esc(param)+(_pname?' \u2014 '+esc(_pname):'')+'">'
        +_pdisp+'</td>';
      if(!st){html+='<td colspan="9" style="color:#aaa;font-style:italic">no data</td>';}
      else{
        html+='<td>'+st.n+'</td>'
          +'<td class="'+medCls.trim()+'">'+_fmt(st.median)+'</td>'
          +'<td>'+_fmt(st.std)+'</td>'
          +'<td>'+(st.cv!=null?st.cv.toFixed(1)+'%':'')+'</td>'
          +'<td>'+_fmt(st.min)+'</td>'
          +'<td>'+_fmt(st.max)+'</td>'
          +'<td style="color:#c0392b">'+(lsl!=null?_fmt(lsl):'')+'</td>'
          +'<td style="color:#2980b9">'+(usl!=null?_fmt(usl):'')+'</td>'
          +'<td style="color:#7f8c8d;font-size:10px">'+esc(unit)+'</td>';
      }
      html+='</tr>';
    });
  });
  tbody.innerHTML=html;
}
function buildParamTable(){
  _buildParamTableInto('var-head','var-body');
  _buildParamTableInto('dist-pt-head','dist-pt-body');
  _buildParamTableInto('xy-pt-head','xy-pt-body');
}
function toggleGrpRow(grp){_GRP_STATE[grp]=(_GRP_STATE[grp]===false)?true:false;buildParamTable();}

/* ── Shared CSV helpers ──────────────────────────────────────────────────── */
function _csvQ(v){v=String(v==null?'':v);return(v.indexOf(',')>=0||v.indexOf('"')>=0||v.indexOf('\n')>=0)?'"'+v.replace(/"/g,'""')+'"':v;}
function _csvBlob(lines,fname){var blob=new Blob([lines.join('\n')],{type:'text/csv'});var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=fname;a.click();}
function _csvTs(){return new Date().toISOString().slice(0,16).replace(/[T:]/g,'-');}

/* ── Variability tab: download parameter table as CSV ───────────────── */
function downloadVarCSV(){
  var cols=['Group','Parameter','N','Median','Std','Spread (%)','Min','Max','LSL','USL','Unit'];
  var lines=[cols.join(',')];
  function _q(v){
    v=String(v==null?'':v);
    return(v.indexOf(',')>=0||v.indexOf('"')>=0||v.indexOf('\n')>=0)?'"'+v.replace(/"/g,'""')+'"':v;
  }
  PCM_GROUPS.forEach(function(grp){
    if(!_GRP_VIS[grp])return;
    (PCM_GROUP_PARAMS[grp]||[]).forEach(function(param){
      var st=_paramStats(param);
      var meta=PCM_PARAM_META[param]||{};
      var row=[
        grp,param,
        st?st.n:'',
        st?_fmt(st.median):'',
        st?_fmt(st.std):'',
        st&&st.cv!=null?st.cv.toFixed(2):'',
        st?_fmt(st.min):'',
        st?_fmt(st.max):'',
        meta.lsl!=null?_fmt(meta.lsl):'',
        meta.usl!=null?_fmt(meta.usl):'',
        meta.unit||''
      ];
      lines.push(row.map(_q).join(','));
    });
  });
  var ts=new Date().toISOString().slice(0,16).replace(/[T:]/g,'-');
  var blob=new Blob([lines.join('\n')],{type:'text/csv'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='pcm_variability_'+ts+'.csv';a.click();
}

/* ── Group strip chart CSV — long format: one row per (lot, wafer, param) ── */
function downloadGrpCSV(grp){
  var ak=activeKeys();
  var params=PCM_GROUP_PARAMS[grp]||[];
  var cols=['Lot','Wafer','Program','Material','GroupBy','Param','N','Median','Std','Spread (%)','Min','Max','LSL','USL','Unit'];
  var lines=[cols.join(',')];
  PCM_ROWS.forEach(function(r){
    if(params.indexOf(r.param)<0)return;
    var k=_rKey(r);
    if(!ak.has(k))return;
    var meta=PCM_PARAM_META[r.param]||{};
    var vals=(r.die_values||[]).filter(function(v){return v!=null&&isFinite(v);});
    if(!vals.length&&r.median!=null)vals=[r.median];
    var med=_med(vals),sd=_std(vals);
    var cv=(med&&med!==0)?Math.abs(sd/med*100):null;
    var mn2=vals.length?_fmt(_safeMin(vals)):'',mx2=vals.length?_fmt(_safeMax(vals)):'';
    var row=[r.lot,r.wafer,r.program||'',r.material||'',_grpKey(r),r.param,
      vals.length,med!=null?_fmt(med):'',sd!=null?_fmt(sd):'',cv!=null?cv.toFixed(2):'',
      mn2,mx2,meta.lsl!=null?_fmt(meta.lsl):'',meta.usl!=null?_fmt(meta.usl):'',meta.unit||''];
    lines.push(row.map(_csvQ).join(','));
  });
  _csvBlob(lines,'pcm_grp_'+grp.replace(/[^a-zA-Z0-9]/g,'_')+'_'+_csvTs()+'.csv');
}

/* ── Per-site wide CSV: one row per (lot, wafer, program, material, site) ─ */
function downloadSiteCSV(){
  var ak=activeKeys();
  /* Collect all visible params in group order */
  var params=[];
  PCM_GROUPS.forEach(function(grp){
    if(!_GRP_VIS[grp])return;
    (PCM_GROUP_PARAMS[grp]||[]).forEach(function(p){if(params.indexOf(p)<0)params.push(p);});
  });
  if(!params.length){alert('No visible groups selected.');return;}
  /* Build key → {lot,wafer,program,material,vals:{param:[...]}} */
  var keyMap={};
  PCM_ROWS.forEach(function(r){
    if(params.indexOf(r.param)<0)return;
    var ak_k=_rKey(r);
    if(!ak.has(ak_k))return;
    var k=r.lot+'\x01'+r.wafer+'\x01'+(r.program||'')+'\x01'+(r.material||'');
    if(!keyMap[k])keyMap[k]={lot:r.lot,wafer:r.wafer,program:r.program||'',material:r.material||'',vals:{}};
    keyMap[k].vals[r.param]=(r.die_values||[]).filter(function(v){return v!=null&&isFinite(v);});
  });
  var cols=['Lot','Wafer','Program','Material','Site'].concat(params);
  var lines=[cols.join(',')];
  Object.keys(keyMap).forEach(function(k){
    var e=keyMap[k];
    var nSites=0;
    params.forEach(function(p){var n=(e.vals[p]||[]).length;if(n>nSites)nSites=n;});
    for(var i=0;i<nSites;i++){
      var row=[e.lot,e.wafer,e.program,e.material,i+1];
      params.forEach(function(p){
        var v=(e.vals[p]||[])[i];
        row.push(v!=null&&isFinite(v)?v:'');
      });
      lines.push(row.map(_csvQ).join(','));
    }
  });
  _csvBlob(lines,'pcm_sites_'+_csvTs()+'.csv');
}

/* ── Distribution histogram CSV — per-die values for one param ───────────── */
function downloadPdlyCSV(param){
  var ak=activeKeys();
  var meta=PCM_PARAM_META[param]||{};
  var isTd=!!param.match(/^Td_/i);
  var tgt=meta.target!=null?meta.target:((meta.lsl!=null&&meta.usl!=null)?(meta.lsl+meta.usl)/2:null);
  var hasTgt=isTd&&tgt&&tgt!==0;
  var cols=['Lot','Wafer','Material','GroupBy','Param','Value'];
  if(hasTgt)cols.push('Freq%OfTarget');
  cols.push('LSL','USL','Unit');
  var lines=[cols.join(',')];
  PCM_ROWS.forEach(function(r){
    if(r.param!==param)return;
    var k=_rKey(r);
    if(!ak.has(k))return;
    var vals=(r.die_values||[]).filter(function(v){return v!=null&&isFinite(v);});
    if(!vals.length&&r.median!=null)vals=[r.median];
    vals.forEach(function(v){
      var row=[r.lot,r.wafer,r.material||'',_grpKey(r),param,_fmt(v)];
      if(hasTgt)row.push(v!==0?(tgt/v*100).toFixed(4):'');
      row.push(meta.lsl!=null?_fmt(meta.lsl):'',meta.usl!=null?_fmt(meta.usl):'',meta.unit||'');
      lines.push(row.map(_csvQ).join(','));
    });
  });
  _csvBlob(lines,'pcm_dist_'+param.replace(/[^a-zA-Z0-9]/g,'_')+'_'+_csvTs()+'.csv');
}

/* ── XY scatter CSV ──────────────────────────────────────────────────────── */
function downloadXYCSV(){
  var ak=activeKeys();
  var xParam=_XY_X;
  _xyYsEnsure();
  var validYs=_XY_YS.filter(function(y){return y in PCM_PARAM_META;});
  if(!xParam||!validYs.length)return;
  var multiY=_XY_YS.length>1;
  var cols=['Lot','Wafer','Material','GroupBy','X_Param','X','Y_Param','Y'];
  var lines=[cols.join(',')];
  var xRows={};
  PCM_ROWS.forEach(function(r){
    if(!ak.has(_rKey(r)))return;
    if(r.param===xParam)xRows[_rKey(r)]=r;
  });
  validYs.forEach(function(yParam){
    var yRows={};
    PCM_ROWS.forEach(function(r){
      if(!ak.has(_rKey(r)))return;
      if(r.param===yParam)yRows[_rKey(r)]=r;
    });
    Object.keys(xRows).forEach(function(k){
      var xr=xRows[k],yr=yRows[k];if(!yr)return;
      var gk=multiY?yParam:_grpKey(xr);
      if(_XY_DIE){
        var xraw=xr.die_values||[],yraw=yr.die_values||[];
        var nd=Math.min(xraw.length,yraw.length);
        for(var di=0;di<nd;di++){
          var xrv=xraw[di],yrv=yraw[di];
          if(xrv==null||!isFinite(xrv)||yrv==null||!isFinite(yrv))continue;
          var xd=_toDisplayVals(xParam,[xrv]),yd=_toDisplayVals(yParam,[yrv]);
          if(!xd.length||!yd.length)continue;
          lines.push([xr.lot,xr.wafer,xr.material||'',gk,xParam,_fmt(xd[0]),yParam,_fmt(yd[0])].map(_csvQ).join(','));
        }
      }else{
        var xdv=_toDisplayVals(xParam,(xr.die_values||[]).filter(function(v){return v!=null&&isFinite(v);}));
        var ydv=_toDisplayVals(yParam,(yr.die_values||[]).filter(function(v){return v!=null&&isFinite(v);}));
        var xm=_med(xdv),ym=_med(ydv);
        if(xm!=null&&isFinite(xm)&&ym!=null&&isFinite(ym))
          lines.push([xr.lot,xr.wafer,xr.material||'',gk,xParam,_fmt(xm),yParam,_fmt(ym)].map(_csvQ).join(','));
      }
    });
  });
  var fname='pcm_xy_'+xParam.replace(/[^a-zA-Z0-9]/g,'_')+'_'+_csvTs()+'.csv';
  _csvBlob(lines,fname);
}

/* ── Panel 3: one SVG strip chart per group ─────────────────────────────── */
var _BANNER_COLS=['#1a5276','#117a65','#6e2f8a','#7d4e00','#922b21','#1a6e2b','#1a3a72','#7d4500'];

function _drawGroupChart(svgEl,grp,gi,params,ak,cm){
  if(!params||!params.length){svgEl.style.display='none';return;}
  svgEl.style.display='block';

  var W=Math.max(svgEl.parentElement?svgEl.parentElement.clientWidth-8:700,300);
  var ML=90,MR=80,MT=32,MB=8;
  var xStep=Math.max(32,(W-ML-MR)/params.length);
  var CW=xStep*params.length;
  var xLblH=Math.max(140,Math.min(300,params.reduce(function(mx,p){return Math.max(mx,p.length);},0)*10+20));
  var CH=_CHART_H;  /* user-adjustable via slider */
  var H=MT+CH+xLblH+MB;

  /* Y range */
  var allVals=[];
  PCM_ROWS.forEach(function(r){
    if(params.indexOf(r.param)<0)return;
    if(!ak.has(_rKey(r)))return;
    (r.die_values||[]).forEach(function(v){
      if(v==null||!isFinite(v))return;
      var cv2=_toDisplayVals(r.param,[v]);
      if(cv2.length)allVals.push(cv2[0]);
    });
  });
  var ylo,yhi;
  if(allVals.length>=2){
    var dMin=_safeMin(allVals),dMax=_safeMax(allVals);
    /* Always clip to 1st/99th percentile — catches Ioff-style params where
       outliers inflate range 100× while bulk of data sits near 0 */
    if(allVals.length>=10){
      var _srt=allVals.slice().sort(function(a,b){return a-b;});
      var _p01=_srt[Math.floor(_srt.length*0.01)];
      var _p99=_srt[Math.min(_srt.length-1,Math.ceil(_srt.length*0.99))];
      if(_p99>_p01){dMin=_p01;dMax=_p99;}
    }
    /* Extend to include spec limits only if within 5× data range */
    var _dr=dMax-dMin||Math.abs(dMin)*0.1||0.1;
    params.forEach(function(pm){
      var m=PCM_PARAM_META[pm]||{};
      if(m.lsl!=null&&m.lsl>=dMin-5*_dr)dMin=Math.min(dMin,m.lsl);
      if(m.usl!=null&&m.usl<=dMax+5*_dr)dMax=Math.max(dMax,m.usl);
    });
    var rng=dMax-dMin||_dr,pad=rng*0.15,ns=_niceStep(rng);
    ylo=Math.floor((dMin-pad)/ns)*ns;yhi=Math.ceil((dMax+pad)/ns)*ns;
  }else{ylo=0;yhi=1;}

  function xPos(i){return ML+(i+0.5)*xStep;}
  function yPos(v){return MT+(1-(v-ylo)/(yhi-ylo))*CH;}

  var p=[];
  var col=_BANNER_COLS[gi%_BANNER_COLS.length];
  p.push('<rect width="'+(ML+CW+MR)+'" height="'+H+'" fill="#f8f9fa"/>');
  p.push('<rect x="'+ML+'" y="'+MT+'" width="'+CW+'" height="'+CH+'" fill="white" stroke="#ccc" stroke-width="1"/>');

  /* Y grid — use step-count loop to avoid float accumulation drift on tiny scales (e.g. ps) */
  var yStep=_niceStep((yhi-ylo)/5);
  var yStart=Math.ceil(ylo/yStep)*yStep;
  var yGridN=Math.min(60,Math.ceil((yhi-yStart)/yStep)+2);
  for(var _yi=0;_yi<yGridN;_yi++){
    var yv=yStart+_yi*yStep;
    if(yv>yhi+yStep*0.01)break;
    var yp=yPos(yv);
    if(yp<MT-1||yp>MT+CH+1)continue;
    p.push('<line x1="'+ML+'" y1="'+yp.toFixed(1)+'" x2="'+(ML+CW)+'" y2="'+yp.toFixed(1)+'" stroke="rgba(0,0,0,0.07)" stroke-width="0.7"/>');
    p.push('<text x="'+(ML-3)+'" y="'+yp.toFixed(1)+'" text-anchor="end" dominant-baseline="middle" font-size="16" font-weight="bold" fill="#111">'+_fmt(yv)+'</text>');
  }

  /* Param columns */
  params.forEach(function(param,i){
    var meta=PCM_PARAM_META[param]||{};
    var x1=(xPos(i)-xStep*0.45).toFixed(1),x2=(xPos(i)+xStep*0.45).toFixed(1);
    /* Selected highlight */
    if(SEL_PARAM===param)
      p.push('<rect x="'+(xPos(i)-xStep/2).toFixed(1)+'" y="'+MT+'" width="'+xStep.toFixed(1)+'" height="'+CH+'" fill="rgba(52,152,219,0.10)" stroke="#3498db" stroke-width="1.2"/>');
    /* Alternating column bg */
    else if(i%2===1)
      p.push('<rect x="'+(xPos(i)-xStep/2).toFixed(1)+'" y="'+MT+'" width="'+xStep.toFixed(1)+'" height="'+CH+'" fill="rgba(0,0,0,0.02)"/>');
    if(meta.lsl!=null){var yL=yPos(meta.lsl);if(yL>=MT&&yL<=MT+CH)p.push('<line x1="'+x1+'" y1="'+yL.toFixed(1)+'" x2="'+x2+'" y2="'+yL.toFixed(1)+'" stroke="#c0392b" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.85"/>');}
    if(meta.usl!=null){var yU=yPos(meta.usl);if(yU>=MT&&yU<=MT+CH)p.push('<line x1="'+x1+'" y1="'+yU.toFixed(1)+'" x2="'+x2+'" y2="'+yU.toFixed(1)+'" stroke="#2980b9" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.85"/>');}
  });

  /* Dots — batched per colour into one <path> per colour for performance.
     Individual <circle> elements lock the browser at 20+ lots; paths do not. */
  var _MAX_COL_DOTS=500;  /* max displayed dots per parameter column */
  var _dotPaths={};       /* colour → accumulated SVG arc path string */
  params.forEach(function(param,i){
    /* Collect all visible dots for this column */
    var col_dots=[];
    var isTdParam=param.match(/^Td_/i);
    PCM_ROWS.forEach(function(r,ri){
      if(r.param!==param)return;
      if(!ak.has(_rKey(r)))return;
      var dotCol=cm.map[_grpKey(r)]||_cPal(0);
      /* Per-site: use all die_values; per-wafer: use stored median (single dot) */
      var src=_VAR_PER_SITE?(r.die_values||[]):[r.median];
      src.forEach(function(v,vi){
        if(v==null||!isFinite(v))return;
        var dv2=_toDisplayVals(param,[v]);
        if(!dv2.length)return;
        var yp2=yPos(dv2[0]);if(yp2<MT||yp2>MT+CH)return;
        col_dots.push({col:dotCol,ri:ri,vi:vi,yp:yp2});
      });
    });
    /* Deterministic subsample when column is dense */
    if(col_dots.length>_MAX_COL_DOTS){
      var step=col_dots.length/_MAX_COL_DOTS,sampled=[];
      for(var _s=0;_s<_MAX_COL_DOTS;_s++)sampled.push(col_dots[Math.floor(_s*step)]);
      col_dots=sampled;
    }
    col_dots.forEach(function(d){
      var jitter=(_sRand(d.ri*997+d.vi)-0.5)*xStep*0.52;
      var cx=+(xPos(i)+jitter).toFixed(1),cy=+d.yp.toFixed(1);
      /* SVG arc trick: one path segment = one circle, no extra DOM node */
      if(!_dotPaths[d.col])_dotPaths[d.col]='';
      _dotPaths[d.col]+='M'+cx+','+cy+'m-2.5,0a2.5,2.5,0,1,0,5,0a2.5,2.5,0,1,0,-5,0';
    });
  });
  /* Emit one <path> per colour — O(colours) nodes instead of O(dots) */
  Object.keys(_dotPaths).forEach(function(col){
    p.push('<path d="'+_dotPaths[col]+'" fill="'+col+'" opacity="0.70"/>');
  });

  /* Median diamond + target cross per param */
  params.forEach(function(param,i){
    var meta=PCM_PARAM_META[param]||{};
    var vals=[];
    PCM_ROWS.forEach(function(r){
      if(r.param!==param||!ak.has(_rKey(r)))return;
      (r.die_values||[]).forEach(function(v){if(v!=null&&isFinite(v)){var dv2=_toDisplayVals(param,[v]);if(dv2.length)vals.push(dv2[0]);}});
    });
    var med=_med(vals);if(med==null)return;
    var yp=yPos(med);if(yp<MT||yp>MT+CH)return;
    var cx=xPos(i),ds=7;  /* diamond half-size */
    /* Diamond shape: top, right, bottom, left */
    p.push('<polygon points="'+cx+','+(yp-ds)+' '+(cx+ds)+','+yp+' '+cx+','+(yp+ds)+' '+(cx-ds)+','+yp+'"'
      +' fill="#27ae60" stroke="#1a6e2b" stroke-width="1.2" opacity="0.92"/>');
    /* Target cross: LSL+USL midpoint */
    if(meta.lsl!=null&&meta.usl!=null){
      var tgt=(meta.lsl+meta.usl)/2;
      var yT=yPos(tgt);if(yT>=MT&&yT<=MT+CH){
        var ts=6;
        p.push('<line x1="'+(cx-ts)+'" y1="'+yT.toFixed(1)+'" x2="'+(cx+ts)+'" y2="'+yT.toFixed(1)+'" stroke="#f39c12" stroke-width="2.5"/>');
        p.push('<line x1="'+cx+'" y1="'+(yT-ts)+'" x2="'+cx+'" y2="'+(yT+ts)+'" stroke="#f39c12" stroke-width="2.5"/>');
      }
    }
  });

  /* X labels — 0.9× font, with device name if available */
  params.forEach(function(param,i){
    var xmeta=PCM_PARAM_META[param]||{};
    var isSortP=!!(xmeta.is_sort);
    /* Sort params (UPM/SICC/CDYN): use friendly name as main label, no sub-label */
    /* PCM params: raw column name as main label, friendly name in brackets below  */
    var lbl=isSortP
      ?(xmeta.name||param).length>26?(xmeta.name||param).slice(0,25)+'\u2026':(xmeta.name||param)
      :param.length>22?param.slice(0,21)+'\u2026':param;
    p.push('<text transform="translate('+xPos(i).toFixed(1)+','+(MT+CH+4)+') rotate(-48)" text-anchor="end" font-size="20" font-weight="bold" fill="#111">'+esc(lbl)+'</text>');
    if(xmeta.name&&!isSortP){
      var nlbl=xmeta.name.length>26?xmeta.name.slice(0,25)+'\u2026':xmeta.name;
      p.push('<text transform="translate('+xPos(i).toFixed(1)+','+(MT+CH+28)+') rotate(-48)" text-anchor="end" font-size="13" fill="#5d6d7e">'+esc('('+nlbl+')')+'</text>');
    }
  });

  /* Legend — right margin: spec lines and median only */
  var legX=ML+CW+6,legY=MT+4;
  var sly=legY;
  if(true){
    p.push('<line x1="'+legX+'" y1="'+(sly+5)+'" x2="'+(legX+18)+'" y2="'+(sly+5)+'" stroke="#e74c3c" stroke-width="2" stroke-dasharray="5,3"/>');
    p.push('<text x="'+(legX+21)+'" y="'+(sly+5)+'" dominant-baseline="middle" font-size="10" font-weight="bold" fill="#c0392b">LSL</text>');
    p.push('<line x1="'+legX+'" y1="'+(sly+20)+'" x2="'+(legX+18)+'" y2="'+(sly+20)+'" stroke="#2980b9" stroke-width="2" stroke-dasharray="5,3"/>');
    p.push('<text x="'+(legX+21)+'" y="'+(sly+20)+'" dominant-baseline="middle" font-size="10" font-weight="bold" fill="#2980b9">USL</text>');
    p.push('<line x1="'+legX+'" y1="'+(sly+35)+'" x2="'+(legX+18)+'" y2="'+(sly+35)+'" stroke="#f39c12" stroke-width="1.5" stroke-dasharray="2,2"/>');
    p.push('<text x="'+(legX+21)+'" y="'+(sly+35)+'" dominant-baseline="middle" font-size="10" fill="#d68910">Target</text>');
    /* Median diamond in legend */
    var dy=sly+52,dx=legX+5;
    p.push('<polygon points="'+dx+','+(dy-5)+' '+(dx+5)+','+dy+' '+dx+','+(dy+5)+' '+(dx-5)+','+dy+'" fill="#27ae60" stroke="#1a6e2b" stroke-width="1"/>');
    p.push('<text x="'+(legX+13)+'" y="'+dy+'" dominant-baseline="middle" font-size="10" font-weight="bold" fill="#1a6e2b">Median</text>');
  }

  p.push('<text transform="translate(18,'+(MT+CH/2)+') rotate(-90)" text-anchor="middle" dominant-baseline="middle" font-size="20" font-weight="bold" fill="#111">Value</text>');

  svgEl.setAttribute('viewBox','0 0 '+(ML+CW+MR)+' '+H);
  svgEl.setAttribute('width','100%');
  svgEl.setAttribute('height',H);
  svgEl.innerHTML=p.join('');
  /* Group-by colour legend — HTML row below chart */
  var legDiv=svgEl.parentElement?svgEl.parentElement.querySelector('.grp-legend'):null;
  if(legDiv){
    if(cm.keys.length<=1&&cm.keys[0]==='All'){legDiv.innerHTML='';}
    else{
      var lh='<div style="display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;padding:4px 8px">';
      cm.keys.forEach(function(k,i){
        lh+='<span style="display:flex;align-items:center;gap:4px;font-size:11px;color:#2c3e50">'
          +'<span style="display:inline-block;width:12px;height:12px;background:'+cm.map[k]+';border-radius:2px"></span>'
          +esc(k)+'</span>';
      });
      lh+='</div>';
      legDiv.innerHTML=lh;
    }
  }
  /* Tooltip — show param name + Y-value on mouse hover */
  svgEl.addEventListener('mousemove',function(e){
    var tt=_getTT();
    var rect=svgEl.getBoundingClientRect();
    var vbH=H,vbW=ML+CW+MR;
    var scaleX=vbW/rect.width,scaleY=vbH/rect.height;
    var mx=(e.clientX-rect.left)*scaleX,my=(e.clientY-rect.top)*scaleY;
    if(mx<ML||mx>ML+CW||my<MT||my>MT+CH){tt.style.display='none';return;}
    var pi=Math.floor((mx-ML)/xStep);
    if(pi<0||pi>=params.length){tt.style.display='none';return;}
    var yVal=yhi-(my-MT)/CH*(yhi-ylo);
    tt.innerHTML='<b>'+esc(params[pi])+'</b>&nbsp;&nbsp;Y = '+_fmt(yVal);
    tt.style.left=(e.clientX+14)+'px';
    tt.style.top=(e.clientY-36)+'px';
    tt.style.display='block';
  });
  svgEl.addEventListener('mouseleave',function(){_getTT().style.display='none';});
}

var _CHART_H=480;  /* default chart height, adjusted by slider */
var _VAR_PER_SITE=true; /* true = plot individual site values; false = one dot per wafer median */
var _PDLY_H=350;   /* propagation delay chart height, adjusted by slider */
var _XY_H=500;     /* XY scatter plot height, adjusted by slider */
var _XY2_H=500;    /* XY2 scatter plot height */
var _drawPending=null;
function drawAllCharts(){
  if(_drawPending){cancelAnimationFrame(_drawPending);_drawPending=null;}
  var ak=activeKeys(),cm=_cMap(),gi=0;
  var queue=PCM_GROUPS.slice();
  function _next(){
    if(!queue.length){_drawPending=null;return;}
    var grp=queue.shift();
    var gid=grp.replace(/[^a-zA-Z0-9]/g,'_');
    var svgEl=document.getElementById('svg-grp-'+gid);
    var card=document.getElementById('card-grp-'+gid);
    if(!svgEl){gi++;_drawPending=requestAnimationFrame(_next);return;}
    if(!_GRP_VIS[grp]){
      if(card)card.style.display='none';
      gi++;_drawPending=requestAnimationFrame(_next);return;
    }
    if(card)card.style.display='';
    var params=activeParamsForGroup(grp).filter(function(p){return p in PCM_PARAM_META;});
    _drawGroupChart(svgEl,grp,gi,params,ak,cm);
    gi++;
    _drawPending=requestAnimationFrame(_next);
  }
  _drawPending=requestAnimationFrame(_next);
}

/* ── Summary table ──────────────────────────────────────────────────────── */
function buildSummaryTable(){
  var tbody=document.getElementById('sum-tbody');if(!tbody)return;
  var ak=activeKeys(),html='',prevGrp=null;
  PCM_GROUPS.forEach(function(grp){
    if(!_GRP_VIS[grp])return;
    (PCM_GROUP_PARAMS[grp]||[]).forEach(function(param){
      var meta=PCM_PARAM_META[param]||{};
      var isTd=param.match(/^Td_/i);
      var tgt=isTd?(meta.target!=null?meta.target:((meta.lsl!=null&&meta.usl!=null)?(meta.lsl+meta.usl)/2:null)):null;
      PCM_ROWS.forEach(function(r){
        if(r.param!==param)return;
        if(!ak.has(_rKey(r)))return;
        var lsl=meta.lsl,usl=meta.usl,unit=isTd?'% of tgt':(_isLeakage(param)?_leakageScale((r.die_values||[])).unit:(meta.unit||''));
        /* For Td_ rows, convert per-row stats to % of target */
        var med=r.median,std=r.std,cv=r.cv,mn=r.min_val,mx=r.max_val;
        if(isTd&&tgt&&tgt!==0){
          med=(r.median&&r.median!==0)?(tgt/r.median*100):null;
          /* approximate std in % space using CV preservation */
          std=(med!=null&&r.cv!=null)?(Math.abs(r.cv/100*med)):null;
          cv=r.cv;  /* CV is unit-less, same */
          mn=(r.max_val&&r.max_val!==0)?(tgt/r.max_val*100):null;  /* larger t → smaller % */
          mx=(r.min_val&&r.min_val!==0)?(tgt/r.min_val*100):null;
          lsl=null;usl=null;
        }
        var mStyle=(lsl!=null&&med<lsl)||(usl!=null&&med>usl)?' style="color:#c0392b;font-weight:bold"':'';
        html+='<tr'+(grp!==prevGrp?' class="grp-divider"':'')+'>'+
          '<td>'+esc(grp)+'</td><td>'+esc(param)+'</td>'+
          '<td>'+esc(r.lot)+'</td><td>'+esc(r.wafer)+'</td>'+
          '<td>'+esc(r.material||'')+'</td>'+
          '<td class="num">'+r.n+'</td>'+
          '<td class="num"'+mStyle+'>'+(med!=null?_fmt(med):'')+'</td>'+
          '<td class="num">'+(std!=null?_fmt(std):'')+'</td>'+
          '<td class="num">'+(cv!=null?cv.toFixed(1)+'%':'')+'</td>'+
          '<td class="num">'+(mn!=null?_fmt(mn):'')+'</td>'+
          '<td class="num">'+(mx!=null?_fmt(mx):'')+'</td>'+
          '<td class="num" style="color:#c0392b">'+(lsl!=null?_fmt(lsl):'')+'</td>'+
          '<td class="num" style="color:#2980b9">'+(usl!=null?_fmt(usl):'')+'</td>'+
          '<td style="color:#7f8c8d;font-size:10px">'+esc(unit)+'</td>'+
          '</tr>';
        prevGrp=grp;
      });
    });
  });
  tbody.innerHTML=html;
}

/* ── CSV download ───────────────────────────────────────────────────────── */
function downloadCSV(){
  var ak=activeKeys();
  var cols=['Group','Parameter','Lot','Wafer','MaterialType','Skew','BEOLSkew',
            'N','Median','Std','Spread (%)','Min','Max','LSL','USL','Unit'];
  var lines=[cols.join(',')];
  PCM_GROUPS.forEach(function(grp){
    (PCM_GROUP_PARAMS[grp]||[]).forEach(function(param){
      var meta=PCM_PARAM_META[param]||{};
      PCM_ROWS.forEach(function(r){
        if(r.param!==param||!ak.has(_rKey(r)))return;
        var row=[grp,param,r.lot,r.wafer,r.material||'',
          r.n,r.median!=null?r.median:'',r.std!=null?r.std:'',r.cv!=null?r.cv.toFixed(2):'',
          r.min_val!=null?r.min_val:'',r.max_val!=null?r.max_val:'',
          meta.lsl!=null?meta.lsl:'',meta.usl!=null?meta.usl:'',meta.unit||''];
        lines.push(row.map(function(v){v=String(v==null?'':v);return(v.indexOf(',')>=0||v.indexOf('"')>=0)?'"'+v.replace(/"/g,'""')+'"':v;}).join(','));
      });
    });
  });
  var blob=new Blob([lines.join('\n')],{type:'text/csv'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='pcm_summary.csv';a.click();
}

/* ── Propagation Delay: frequency distribution tab ─────────────────────── */
var _PDLY_GRP=null;  /* null = show params from ALL groups; set to group name to filter */
var _PDLY_SEL=null;  /* Set of selected param names; null = use default */
/* Per-panel (1-4) independent state */
var _PDLY_GRP_P=(function(){var o={};for(var i=1;i<=PCM_DIST_PANELS.length+1;i++){o[i]=null;}return o;}());
var _PDLY_H_P=(function(){var o={};for(var i=1;i<=PCM_DIST_PANELS.length;i++){o[i]=350;}return o;}());
var _PDLY_SEL_P=(function(){var o={};for(var i=1;i<=PCM_DIST_PANELS.length;i++){o[i]=null;}return o;}());
var _PDLY_SRCH_P=(function(){var o={};for(var i=1;i<=PCM_DIST_PANELS.length;i++){o[i]='';}return o;}());
/* Per-panel independent group-by (dist panels 1..N+1) */
var _PDLY_GBY_P=(function(){var o={};for(var i=1;i<=PCM_DIST_PANELS.length+1;i++){o[i]=[];}return o;}());
function toggleGbyP(pn,field){
  var arr=_PDLY_GBY_P[pn]||(_PDLY_GBY_P[pn]=[]);
  if(field==='none'){arr.splice(0);}
  else{var i=arr.indexOf(field);if(i>=0)arr.splice(i,1);else arr.push(field);}
  buildPropDelayTab();
}
function _pdlyAllParamsForP(pn){
  var grp=_PDLY_GRP_P[pn];
  var pool=grp?(PCM_GROUP_PARAMS[grp]||[]):Object.keys(PCM_PARAM_META);
  return pool.filter(function(p){return p in PCM_PARAM_META;}).sort();
}
function _pdlyPDefault(pn){
  /* Use configured distribution panels if available */
  if(PCM_DIST_PANELS.length>=pn){
    var _dcfg=PCM_DIST_PANELS[pn-1];
    if(_dcfg&&_dcfg.params&&_dcfg.params.length){
      var _ds=new Set(_dcfg.params.filter(function(p){return p in PCM_PARAM_META;}));
      if(_ds.size)return _ds;
    }
  }
  var ap=_pdlyAllParamsForP(pn);
  var s=new Set();
  if(pn===1){
    if('Td_RJ4u' in PCM_PARAM_META)s.add('Td_RJ4u');
    if('Poff_RJ4u' in PCM_PARAM_META)s.add('Poff_RJ4u');
    var u1=_findParamLike('upm*0107*950*sds');if(u1)s.add(u1);
    return s.size?s:_pdlyDefault(ap);
  }
  if(pn===2){
    if('Td_RK4u' in PCM_PARAM_META)s.add('Td_RK4u');
    if('Poff_RK4u' in PCM_PARAM_META)s.add('Poff_RK4u');
    var u2=_findParamLike('upm*0704*950*sds');if(u2)s.add(u2);
    return s.size?s:_pdlyDefault(ap);
  }
  if(pn===3){
    /* SICC: RING/CORE/ATOM/FULLCHIP 0.95 SDS — no per-cluster entries (atom0, core1 etc.) */
    Object.keys(PCM_PARAM_META).forEach(function(k){
      var lo=k.toLowerCase();
      if(lo.indexOf('sicc')<0||lo.indexOf('sds')<0)return;
      if(/atom\d/.test(lo)||/core\d/.test(lo)||/ccf\d/.test(lo))return;
      var isRing=lo.indexOf('ring')>=0;
      var isCore=lo.indexOf('core')>=0;
      var isAtom=lo.indexOf('atom')>=0;
      var isFull=lo.indexOf('fullchip')>=0;
      if(isRing||isCore||isAtom||isFull)s.add(k);
    });
    return s.size?s:(ap.length?new Set([ap[0]]):new Set());
  }
  if(pn===4){
    /* CDYN: OG_128B_CDYN_ATOM0..3 SDS */
    Object.keys(PCM_PARAM_META).forEach(function(k){
      var lo=k.toLowerCase();
      if(lo.indexOf('cdyn')<0||lo.indexOf('sds')<0)return;
      if(lo.indexOf('og_128b')<0&&lo.indexOf('og128b')<0)return;
      if(/atom[0-3]/.test(lo))s.add(k);
    });
    /* fallback: any CDYN ATOM SDS */
    if(!s.size){Object.keys(PCM_PARAM_META).forEach(function(k){
      var lo=k.toLowerCase();
      if(lo.indexOf('cdyn')>=0&&lo.indexOf('atom')>=0&&lo.indexOf('sds')>=0)s.add(k);
    });}
    return s.size?s:(ap.length?new Set([ap[0]]):new Set());
  }
  return _pdlyDefault(ap);
}
function setPdlyGrpP(pn,grp){
  _PDLY_GRP_P[pn]=grp||null;
  _PDLY_SEL_P[pn]=null;
  buildPropDelayTab();
}
function togglePdlyParamP(pn,p){
  if(!_PDLY_SEL_P[pn])_PDLY_SEL_P[pn]=_pdlyPDefault(pn);
  var sel=_PDLY_SEL_P[pn];
  if(sel.has(p))sel.delete(p);else sel.add(p);
  if(!sel.size)_PDLY_SEL_P[pn]=_pdlyPDefault(pn);
  buildPropDelayTab();
}
function _pdlyPDropToggle(pn){
  var drop=document.getElementById('pdlyp'+pn+'-drop');if(!drop)return;
  if(drop.style.display==='flex'){drop.style.display='none';buildPropDelayTab();return;}
  drop.style.display='flex';
  var srch=document.getElementById('pdlyp'+pn+'-drop-srch');
  if(srch){srch.value=_PDLY_SRCH_P[pn]||'';srch.focus();}
  _pdlyPBuildDropList(pn);
}
function _pdlyPDropSearch(pn,val){_PDLY_SRCH_P[pn]=val;_pdlyPBuildDropList(pn);}
function _pdlyPBuildDropList(pn){
  var el=document.getElementById('pdlyp'+pn+'-drop-list');if(!el)return;
  var all=_pdlyAllParamsForP(pn).filter(function(p){return !p.match(/^Td_/i);});
  var q=(_PDLY_SRCH_P[pn]||'').toLowerCase();
  var vis=q?all.filter(function(p){var nm=(PCM_PARAM_META[p]||{}).name||'';return(p+' '+nm).toLowerCase().indexOf(q)>=0;}):all;
  var sel=_PDLY_SEL_P[pn];
  var html='';
  vis.forEach(function(p){
    var chk=sel&&sel.has(p);
    var nm=(PCM_PARAM_META[p]||{}).name||'';
    html+='<label style="display:flex;align-items:center;gap:5px;padding:4px 8px;cursor:pointer;font-size:11px;border-bottom:1px solid #f5f5f5"'
      +' onmouseover="this.style.background=\'#eaf4ff\'" onmouseout="this.style.background=\'\'">'
      +'<input type="checkbox"'+(chk?' checked':'')+' onchange="_pdlyPDropCheck('+pn+',\''+p.replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\');event.stopPropagation()" style="cursor:pointer">'
      +'<span><b>'+esc(p)+'</b>'+(nm?' <span style="color:#888;font-size:10px">('+esc(nm)+')</span>':'')+'</span></label>';
  });
  el.innerHTML=html||'<div style="padding:8px;color:#aaa;font-size:11px">No matches</div>';
}
function _pdlyPDropCheck(pn,p){
  if(!_PDLY_SEL_P[pn])_PDLY_SEL_P[pn]=_pdlyPDefault(pn);
  var sel=_PDLY_SEL_P[pn];
  if(sel.has(p))sel.delete(p);else sel.add(p);
  _pdlyPBuildDropList(pn);
  var all=_pdlyAllParamsForP(pn).filter(function(p2){return !p2.match(/^Td_/i);});
  var cnt=all.filter(function(p2){return sel.has(p2);}).length;
  var btn=document.getElementById('pdlyp'+pn+'-drop-btn');
  if(btn){btn.innerHTML=(cnt?cnt+' selected':'Select params')+' &#9660;';btn.style.fontWeight=cnt?'bold':'normal';btn.style.borderColor=cnt?'#2980b9':'#bdc3c7';btn.style.background=cnt?'#eaf4ff':'#f8f9fa';}
}
function _pdlyPDropSelAll(pn){
  var all=_pdlyAllParamsForP(pn).filter(function(p){return !p.match(/^Td_/i);});
  var q=(_PDLY_SRCH_P[pn]||'').toLowerCase();
  var vis=q?all.filter(function(p){var nm=(PCM_PARAM_META[p]||{}).name||'';return(p+' '+nm).toLowerCase().indexOf(q)>=0;}):all;
  if(!_PDLY_SEL_P[pn])_PDLY_SEL_P[pn]=_pdlyPDefault(pn);
  vis.forEach(function(p){_PDLY_SEL_P[pn].add(p);});
  _pdlyPBuildDropList(pn);
  var cnt=all.filter(function(p){return _PDLY_SEL_P[pn].has(p);}).length;
  var btn=document.getElementById('pdlyp'+pn+'-drop-btn');
  if(btn){btn.innerHTML=cnt+' selected &#9660;';btn.style.fontWeight='bold';btn.style.borderColor='#2980b9';btn.style.background='#eaf4ff';}
}
function _pdlyPDropClrAll(pn){
  var all=_pdlyAllParamsForP(pn).filter(function(p){return !p.match(/^Td_/i);});
  var q=(_PDLY_SRCH_P[pn]||'').toLowerCase();
  var vis=q?all.filter(function(p){var nm=(PCM_PARAM_META[p]||{}).name||'';return(p+' '+nm).toLowerCase().indexOf(q)>=0;}):all;
  if(!_PDLY_SEL_P[pn])_PDLY_SEL_P[pn]=_pdlyPDefault(pn);
  vis.forEach(function(p){_PDLY_SEL_P[pn].delete(p);});
  _pdlyPBuildDropList(pn);
  var btn=document.getElementById('pdlyp'+pn+'-drop-btn');
  if(btn){btn.innerHTML='Select params &#9660;';btn.style.fontWeight='normal';btn.style.borderColor='#bdc3c7';btn.style.background='#f8f9fa';}
}
/* Build the Group-filter + Group-by + Height + Prop-delay pills + Other dropdown control bar for a panel */
function _buildPdlyPanelBar(pn,allParams,sel,grp,h){
  var _pgby=_PDLY_GBY_P[pn]||[];
  /* Group filter buttons */
  var gBtns='<button onclick="setPdlyGrpP('+pn+',\'\')" style="font-size:11px;padding:2px 9px;border-radius:4px;border:none;cursor:pointer;color:#fff;background:'+((!grp)?'#2980b9':'rgba(0,0,0,0.25)')+';font-weight:'+((!grp)?'bold':'normal')+'">All</button>';
  PCM_GROUPS.forEach(function(g){
    var active=(grp===g);
    gBtns+=' <button onclick="setPdlyGrpP('+pn+',\''+g.replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\') " style="font-size:11px;padding:2px 9px;border-radius:4px;border:none;cursor:pointer;color:#fff;background:'+(active?'#2980b9':'rgba(0,0,0,0.25)')+';font-weight:'+(active?'bold':'normal')+'">'+esc(g)+'</button>';
  });
  /* Group-by checkboxes (per-panel independent state) */
  var gby='<span style="width:1px;background:#4a6278;align-self:stretch;margin:0 4px"></span>'
    +'<b style="color:#f1c40f;margin-right:4px;font-size:12px">Group by:</b>'
    +'<label style="cursor:pointer;font-size:12px;color:#ecf0f1"><input type="checkbox" value="none" onchange="toggleGbyP('+pn+',\'none\')"'+(_pgby.length===0?' checked':'')+'>  None</label>'
    +'<label style="cursor:pointer;font-size:12px;color:#ecf0f1"><input type="checkbox" value="lot" onchange="toggleGbyP('+pn+',\'lot\')"'+(_pgby.indexOf('lot')>=0?' checked':'')+'>  Lot</label>'
    +'<label style="cursor:pointer;font-size:12px;color:#ecf0f1"><input type="checkbox" value="wafer" onchange="toggleGbyP('+pn+',\'wafer\')"'+(_pgby.indexOf('wafer')>=0?' checked':'')+'>  Wafer</label>'
    +'<label style="cursor:pointer;font-size:12px;color:#ecf0f1"><input type="checkbox" value="layout" onchange="toggleGbyP('+pn+',\'layout\')"'+(_pgby.indexOf('layout')>=0?' checked':'')+'>  Layout</label>'
    +'<label style="cursor:pointer;font-size:12px;color:#ecf0f1"><input type="checkbox" value="material" onchange="toggleGbyP('+pn+',\'material\')"'+(_pgby.indexOf('material')>=0?' checked':'')+'>  Material</label>';
  /* Height slider */
  var hId='pdlyp'+pn+'-h-val';
  var hSlider='<span style="width:1px;background:#4a6278;align-self:stretch;margin:0 4px"></span>'
    +'<label style="display:flex;align-items:center;gap:4px;cursor:default;color:#ecf0f1;font-size:12px">&#11041; Height'
    +'<input type="range" min="150" max="900" step="25" value="'+h+'"'
    +' oninput="_PDLY_H_P['+pn+']=+this.value;document.getElementById(\''+hId+'\').textContent=this.value+\'px\';buildPropDelayTab()"'
    +' style="width:90px;accent-color:#3498db">'
    +'<span id="'+hId+'" style="min-width:34px;color:#aed6f1;font-size:10px">'+h+'px</span></label>';
  var bar='<div style="display:flex;flex-wrap:wrap;align-items:center;padding:6px 14px;gap:6px;flex-shrink:0;background:#1f3a50;border-bottom:1px solid #1a252f">'
    +'<span style="color:#aed6f1;font-size:11px">X: Freq% of target &nbsp;|&nbsp; Y: Samples</span>'
    +'<span style="width:1px;background:#4a6278;align-self:stretch;margin:0 4px"></span>'
    +'<span style="color:#f1c40f;font-size:12px;font-weight:bold">Group:</span>'+gBtns
    +gby+hSlider+'</div>';
  /* Prop. Delay pills + Other dropdown */
  var tdParams=allParams.filter(function(p){return p.match(/^Td_/i);});
  var otherParams=allParams.filter(function(p){return !p.match(/^Td_/i);});
  var selOtherCnt=otherParams.filter(function(p){return sel.has(p);}).length;
  var pills='<div style="display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin:8px 14px 6px;padding:6px 8px;background:#f0f4fb;border-radius:6px;border:1px solid #dde">';
  if(tdParams.length){
    pills+='<span style="font-size:10px;color:#7f8c8d;font-weight:bold;margin-right:2px;flex-shrink:0">Prop. Delay:</span>';
    tdParams.forEach(function(p){
      var isSel=sel.has(p);
      var meta=PCM_PARAM_META[p]||{};
      var nm=(meta.name||'').trim();
      var tip=(meta.lsl!=null?'LSL='+_fmt(meta.lsl)+' ':'')+(meta.usl!=null?'USL='+_fmt(meta.usl)+' ':'')+(meta.unit||'');
      pills+='<button onclick="togglePdlyParamP('+pn+',\''+p.replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\') " title="'+esc(tip.trim())+'"'
        +' style="padding:3px 12px;font-size:11px;border-radius:6px;border:1px solid '+(isSel?'#2980b9':'#bdc3c7')+';background:'+(isSel?'#2980b9':'#f8f9fa')+';color:'+(isSel?'#fff':'#2c3e50')+';cursor:pointer;font-weight:'+(isSel?'bold':'normal')+'">'
        +'&lt;'+esc(p)+'&gt;'+(nm?'<span style="font-size:9px;font-weight:normal;opacity:0.8;margin-left:3px">('+esc(nm)+')</span>':'')+'</button>';
    });
  }
  if(otherParams.length){
    if(tdParams.length)pills+='<span style="display:inline-block;width:1px;background:#bdc3c7;align-self:stretch;margin:0 6px"></span>';
    pills+='<span style="font-size:10px;color:#7f8c8d;font-weight:bold;margin-right:2px;flex-shrink:0">Other:</span>'
      +'<div style="position:relative;display:inline-block">'
      +'<button id="pdlyp'+pn+'-drop-btn" onclick="_pdlyPDropToggle('+pn+')" '
      +'style="padding:3px 10px 3px 12px;font-size:11px;border-radius:6px;border:1px solid '+(selOtherCnt?'#2980b9':'#bdc3c7')+';background:'+(selOtherCnt?'#eaf4ff':'#f8f9fa')+';color:#2c3e50;cursor:pointer;font-weight:'+(selOtherCnt?'bold':'normal')+'">'
      +(selOtherCnt?selOtherCnt+' selected':'Select params')+' &#9660;</button>'
      +'<div id="pdlyp'+pn+'-drop" style="display:none;position:absolute;top:calc(100% + 3px);left:0;z-index:9999;background:#fff;border:1px solid #bdc3c7;border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.18);width:310px;max-height:320px;flex-direction:column">'
      +'<div style="padding:5px 6px;border-bottom:1px solid #eee;display:flex;gap:4px;align-items:center">'
      +'<input id="pdlyp'+pn+'-drop-srch" type="text" placeholder="Search\u2026" oninput="_pdlyPDropSearch('+pn+',this.value)" style="flex:1;padding:3px 6px;font-size:11px;border:1px solid #ccc;border-radius:4px">'
      +'<button onclick="_pdlyPDropSelAll('+pn+')" style="font-size:10px;padding:2px 6px;border:1px solid #ccc;border-radius:3px;background:#f8f9fa;cursor:pointer;flex-shrink:0">All</button>'
      +'<button onclick="_pdlyPDropClrAll('+pn+')" style="font-size:10px;padding:2px 6px;border:1px solid #ccc;border-radius:3px;background:#f8f9fa;cursor:pointer;flex-shrink:0">None</button>'
      +'</div>'
      +'<div id="pdlyp'+pn+'-drop-list" style="overflow-y:auto;max-height:260px;padding:2px 0"></div>'
      +'</div></div>';
  }
  pills+='</div>';
  return bar+pills;
}
function _pdlyDefault(params){
  /* Default selection: Td_RJ4u, Td_RK4u, UPM ULVT 0107 950mV SDS, UPM ULVT 0704 950mV SDS */
  var sel=new Set();
  var _defaults=['td_rj4u','td_rk4u'];
  for(var i=0;i<params.length;i++){
    var lo=params[i].toLowerCase();
    if(_defaults.indexOf(lo)>=0) sel.add(params[i]);
    if(lo.indexOf('upm')>=0&&lo.indexOf('ulvt')>=0&&lo.indexOf('0107')>=0&&lo.indexOf('950')>=0&&lo.indexOf('sds')>=0) sel.add(params[i]);
    if(lo.indexOf('upm')>=0&&lo.indexOf('ulvt')>=0&&lo.indexOf('0704')>=0&&lo.indexOf('950')>=0&&lo.indexOf('sds')>=0) sel.add(params[i]);
  }
  if(sel.size>0) return sel;
  return params.length?new Set([params[0]]):new Set();
}
function _pdlyAllParams(){
  /* Return all params available given current _PDLY_GRP filter */
  var pool=_PDLY_GRP?(PCM_GROUP_PARAMS[_PDLY_GRP]||[]):Object.keys(PCM_PARAM_META);
  return pool.filter(function(p){return p in PCM_PARAM_META;}).sort();
}
function setPdlyGrp(grp){
  _PDLY_GRP=grp||null;
  _PDLY_SEL=null;  /* reset param selection when group changes */
  buildPropDelayTab();
}
var _PDLY_SRCH='';
function togglePdlyParam(p){
  var all=_pdlyAllParams();
  if(_PDLY_SEL===null)_PDLY_SEL=_pdlyDefault(all);
  if(_PDLY_SEL.has(p))_PDLY_SEL.delete(p);else _PDLY_SEL.add(p);
  if(_PDLY_SEL.size===0)_PDLY_SEL=_pdlyDefault(all);
  buildPropDelayTab();
}
/* ── Other-params dropdown helpers ─────────────────────────────────────── */
function _pdlyDropToggle(){
  var drop=document.getElementById('pdly-drop');
  if(!drop)return;
  if(drop.style.display==='flex'){drop.style.display='none';buildPropDelayTab();return;}
  drop.style.display='flex';
  var srch=document.getElementById('pdly-drop-srch');
  if(srch){srch.value=_PDLY_SRCH;srch.focus();}
  _pdlyBuildDropList();
}
function _pdlyDropSearch(val){_PDLY_SRCH=val;_pdlyBuildDropList();}
function _pdlyDropCheck(p){
  var all=_pdlyAllParams();
  if(_PDLY_SEL===null)_PDLY_SEL=_pdlyDefault(all);
  if(_PDLY_SEL.has(p))_PDLY_SEL.delete(p);else _PDLY_SEL.add(p);
  var other=all.filter(function(q){return !q.match(/^Td_/i);});
  var selCnt=other.filter(function(q){return _PDLY_SEL.has(q);}).length;
  var btn=document.getElementById('pdly-drop-btn');
  if(btn){
    btn.innerHTML=(selCnt?selCnt+' selected':'Select params')+' &#9660;';
    btn.style.fontWeight=selCnt?'bold':'normal';
    btn.style.borderColor=selCnt?'#2980b9':'#bdc3c7';
    btn.style.background=selCnt?'#eaf4ff':'#f8f9fa';
  }
  _pdlyBuildDropList();
}
function _pdlyBuildDropList(){
  var el=document.getElementById('pdly-drop-list');if(!el)return;
  var all=_pdlyAllParams().filter(function(p){return !p.match(/^Td_/i);});
  var q=(_PDLY_SRCH||'').toLowerCase();
  var vis=q?all.filter(function(p){var nm=(PCM_PARAM_META[p]||{}).name||'';return(p+' '+nm).toLowerCase().indexOf(q)>=0;}):all;
  var html='';
  vis.forEach(function(p){
    var chk=_PDLY_SEL&&_PDLY_SEL.has(p);
    var nm=(PCM_PARAM_META[p]||{}).name||'';
    html+='<label style="display:flex;align-items:center;gap:5px;padding:4px 8px;cursor:pointer;font-size:11px;border-bottom:1px solid #f5f5f5"'
      +' onmouseover="this.style.background=\'#eaf4ff\'" onmouseout="this.style.background=\'\'">' 
      +'<input type="checkbox"'+(chk?' checked':'')+' onchange="_pdlyDropCheck(\''+p+'\');event.stopPropagation()" style="cursor:pointer">'
      +'<span><b>'+esc(p)+'</b>'+(nm?' <span style="color:#888;font-size:10px">('+esc(nm)+')</span>':'')+'</span></label>';
  });
  el.innerHTML=html||'<div style="padding:8px;color:#aaa;font-size:11px">No matches</div>';
}
function _pdlyDropSelAll(){
  var all=_pdlyAllParams().filter(function(p){return !p.match(/^Td_/i);});
  var q=(_PDLY_SRCH||'').toLowerCase();
  var vis=q?all.filter(function(p){var nm=(PCM_PARAM_META[p]||{}).name||'';return(p+' '+nm).toLowerCase().indexOf(q)>=0;}):all;
  if(!_PDLY_SEL)_PDLY_SEL=new Set();
  vis.forEach(function(p){_PDLY_SEL.add(p);});
  _pdlyBuildDropList();
  var selCnt=all.filter(function(p){return _PDLY_SEL.has(p);}).length;
  var btn=document.getElementById('pdly-drop-btn');
  if(btn){btn.innerHTML=selCnt+' selected &#9660;';btn.style.fontWeight='bold';btn.style.borderColor='#2980b9';btn.style.background='#eaf4ff';}
}
function _pdlyDropClrAll(){
  var all=_pdlyAllParams().filter(function(p){return !p.match(/^Td_/i);});
  var q=(_PDLY_SRCH||'').toLowerCase();
  var vis=q?all.filter(function(p){var nm=(PCM_PARAM_META[p]||{}).name||'';return(p+' '+nm).toLowerCase().indexOf(q)>=0;}):all;
  if(!_PDLY_SEL)_PDLY_SEL=new Set();
  vis.forEach(function(p){_PDLY_SEL.delete(p);});
  _pdlyBuildDropList();
  var btn=document.getElementById('pdly-drop-btn');
  if(btn){btn.innerHTML='Select params &#9660;';btn.style.fontWeight='normal';btn.style.borderColor='#bdc3c7';btn.style.background='#f8f9fa';}
}
document.addEventListener('click',function(e){
  /* Close panel 3 dropdown */
  var drop=document.getElementById('pdly-drop');
  if(drop&&drop.style.display==='flex'){
    var btn=document.getElementById('pdly-drop-btn');
    if(!drop.contains(e.target)&&!(btn&&e.target===btn)){drop.style.display='none';buildPropDelayTab();}
  }
  /* Close per-panel dropdowns (panels 1-4) */
  [1,2,3,4].forEach(function(pn){
    var pd=document.getElementById('pdlyp'+pn+'-drop');
    if(pd&&pd.style.display==='flex'){
      var pb=document.getElementById('pdlyp'+pn+'-drop-btn');
      if(!pd.contains(e.target)&&!(pb&&e.target===pb)){pd.style.display='none';buildPropDelayTab();}
    }
  });
},true);
/* ── Shared chart-grid renderer for RO Distribution panels ─────────────── */
function _buildPdlyCards(params,ak,gby){
  var html='<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:12px">';
  var cm=_cMapWith(gby||[]);
  params.forEach(function(param){
    var meta=PCM_PARAM_META[param]||{};
    var tgt=meta.target!=null?meta.target:((meta.lsl!=null&&meta.usl!=null)?(meta.lsl+meta.usl)/2:null);
    var grpVals={},grpOrder=[];
    PCM_ROWS.forEach(function(r){
      if(r.param!==param)return;
      if(!ak.has(_rKey(r)))return;
      var gk=_grpKeyWith(r,gby||[]);
      if(!grpVals[gk]){grpVals[gk]=[];grpOrder.push(gk);}
      (r.die_values||[]).forEach(function(v){if(v!=null&&isFinite(v))grpVals[gk].push(v);});
    });
    var vals=[];
    grpOrder.forEach(function(gk){vals=vals.concat(grpVals[gk]);});
    if(!vals.length){
      html+='<div style="background:#fff;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.10);padding:10px">'
           +'<div style="font-weight:bold;font-size:12px;color:#2c3e50">'+esc(param)+'</div>'
           +'<div style="color:#aaa;font-style:italic;padding:8px 0">no data for active selection</div></div>';
      return;
    }
    var isTd=!!param.match(/^Td_/i);
    /* Only Td_ params are normalised to % of target; Poff_/Ioff_ use leakage scale */
    var tgt=isTd?(meta.target!=null?meta.target:((meta.lsl!=null&&meta.usl!=null)?(meta.lsl+meta.usl)/2:null)):null;
    var _lkSc=(!isTd&&_isLeakage(param)&&vals.length)?_leakageScale(vals):null;
    function _normArr(arr){
      if(isTd&&tgt&&tgt!==0)return arr.map(function(v){return v!==0?(tgt/v*100):null;}).filter(function(v){return v!=null&&isFinite(v);});
      if(_lkSc)return arr.map(function(v){return v*_lkSc.scale;});
      return arr;
    }
    var xSuffix=isTd&&tgt?'Frequency (% of target)':(_lkSc?_lkSc.unit:(meta.unit||''));
    var tgtStr=isTd&&tgt?' | target='+_fmt(tgt)+(meta.unit?' '+meta.unit:''):'';
    var normVals=_normArr(vals);
    if(!normVals.length)return;
    if(isTd){
      var _m0=_med(normVals),_s0=_std(normVals);
      normVals=normVals.filter(function(v){return Math.abs(v-_m0)<=3*_s0;});
      grpOrder.forEach(function(gk){
        var nv=_normArr(grpVals[gk]);
        grpVals[gk+'__clipped__']=nv.filter(function(v){return Math.abs(v-_m0)<=3*_s0;});
      });
    }
    if(!normVals.length)return;
    var med=_med(normVals),sd=_std(normVals);
    /* ── Pre-compute OOS counts (raw vals vs spec limits) for SVG + stats ── */
    var nTotal=vals.length;
    var nLo=0,nHi=0;
    if(meta.lsl!=null)vals.forEach(function(v){if(v<meta.lsl)nLo++;});
    if(meta.usl!=null)vals.forEach(function(v){if(v>meta.usl)nHi++;});
    var pctLo=nTotal?nLo/nTotal*100:0;
    var pctHi=nTotal?nHi/nTotal*100:0;
    /* σ-based counts (fallback when no spec limits) */
    var hasSpecLimits=(meta.lsl!=null||meta.usl!=null);
    var _s3lo=med-3*sd,_s3hi=med+3*sd,_s6lo=med-6*sd,_s6hi=med+6*sd;
    var n3lo=0,n3hi=0,n6lo=0,n6hi=0;
    normVals.forEach(function(v){
      if(v<_s3lo)n3lo++;else if(v>_s3hi)n3hi++;
      if(v<_s6lo)n6lo++;else if(v>_s6hi)n6hi++;
    });
    var nNorm=normVals.length;
    var nOut3=n3lo+n3hi,nOut6=n6lo+n6hi;
    var pctOut3=nNorm?nOut3/nNorm*100:0,pctOut6=nNorm?nOut6/nNorm*100:0;
    var mn=_safeMin(normVals),mx=_safeMax(normVals);
    var rng=mx-mn||Math.abs(mn)*0.02||0.1;
    var nBins=Math.max(10,Math.min(40,Math.ceil(Math.sqrt(normVals.length)*2.2)));
    var binW=rng/nBins;
    var grpCounts={};
    var maxCnt=1;
    grpOrder.forEach(function(gk){
      var nv=isTd?(grpVals[gk+'__clipped__']||[]):_normArr(grpVals[gk]);
      var cnts=new Array(nBins).fill(0);
      nv.forEach(function(v){
        var bi=Math.min(Math.floor((v-mn)/binW),nBins-1);
        if(bi>=0&&bi<nBins)cnts[bi]++;
      });
      grpCounts[gk]=cnts;
      var gc=_safeMax(cnts)||0;
      if(gc>maxCnt)maxCnt=gc;
    });
    var maxYDisp=Math.ceil(maxCnt*1.15);
    var xPad=Math.max(rng*0.05,binW*0.5);
    var xLo=mn-xPad,xRng=rng+xPad*2;
    var svgW=700,svgH=_PDLY_H,ML=72,MR=Math.max(100,grpOrder.length*0+100),MT=40,MB=72;
    var plotW=svgW-ML-MR,plotH=Math.max(40,svgH-MT-MB);
    function xp(v){return ML+(v-xLo)/xRng*plotW;}
    var p=['<svg width="100%" height="'+svgH+'" viewBox="0 0 '+svgW+' '+svgH+'" style="display:block">'];
    p.push('<rect width="'+svgW+'" height="'+svgH+'" fill="#f8f9fa"/>');
    p.push('<rect x="'+ML+'" y="'+MT+'" width="'+plotW+'" height="'+plotH+'" fill="#fff" stroke="#ccc" stroke-width="1"/>');
    for(var yi=0;yi<=5;yi++){
      var yv=Math.round(maxYDisp*yi/5);
      var ypv=(MT+plotH-(yv/maxYDisp)*plotH).toFixed(1);
      p.push('<line x1="'+ML+'" y1="'+ypv+'" x2="'+(ML+plotW)+'" y2="'+ypv+'" stroke="rgba(0,0,0,0.10)" stroke-width="0.8"/>');
      p.push('<text x="'+(ML-4)+'" y="'+ypv+'" text-anchor="end" dominant-baseline="middle" font-size="18" font-weight="bold" fill="#111">'+yv+'</text>');
    }
    p.push('<text transform="translate(18,'+(MT+plotH/2)+') rotate(-90)" text-anchor="middle" font-size="18" font-weight="bold" fill="#111">Samples</text>');
    var nGrps=grpOrder.length;
    var bpxD=binW/xRng*plotW;
    var barW=Math.max(0.5,(bpxD-1)/Math.max(1,nGrps));
    grpOrder.forEach(function(gk,gi2){
      var gcol=cm.map[gk]||_cPal(gi2);
      var cnts=grpCounts[gk];
      var offsetX=(nGrps>1)?((gi2-(nGrps-1)/2)*barW):0;
      for(var b=0;b<nBins;b++){
        if(!cnts[b])continue;
        var bx=(ML+(mn+b*binW-xLo)/xRng*plotW+offsetX).toFixed(1);
        var bh=(cnts[b]/maxYDisp*plotH).toFixed(1);
        var by=(MT+plotH-cnts[b]/maxYDisp*plotH).toFixed(1);
        p.push('<rect x="'+bx+'" y="'+by+'" width="'+(Math.max(0.5,barW-0.5)).toFixed(1)+'" height="'+bh+'" fill="'+gcol+'" opacity="0.65" rx="1"/>');
      }
    });
    var s3lo=med-3*sd,s3hi=med+3*sd;
    var s3loX=Math.max(ML,xp(s3lo)),s3hiX=Math.min(ML+plotW,xp(s3hi));
    if(s3hiX>s3loX)
      p.push('<rect x="'+s3loX.toFixed(1)+'" y="'+MT+'" width="'+(s3hiX-s3loX).toFixed(1)+'" height="'+plotH+'" fill="rgba(230,126,34,0.08)" stroke="none"/>');
    [[s3lo,'-3\u03c3','#e67e22'],[s3hi,'+3\u03c3','#e67e22']].forEach(function(t){
      var xv=xp(t[0]).toFixed(1);
      if(parseFloat(xv)<ML||parseFloat(xv)>ML+plotW)return;
      p.push('<line x1="'+xv+'" y1="'+MT+'" x2="'+xv+'" y2="'+(MT+plotH)+'" stroke="'+t[2]+'" stroke-width="2" stroke-dasharray="4,3"/>');
      p.push('<text x="'+xv+'" y="'+(MT-6)+'" text-anchor="middle" font-size="16" font-weight="bold" fill="'+t[2]+'">'+t[1]+'</text>');
    });
    var s6lo=med-6*sd,s6hi=med+6*sd;
    [[s6lo,'-6\u03c3','#c0392b'],[s6hi,'+6\u03c3','#c0392b']].forEach(function(t){
      var xv=xp(t[0]).toFixed(1);
      if(parseFloat(xv)<ML||parseFloat(xv)>ML+plotW)return;
      p.push('<line x1="'+xv+'" y1="'+MT+'" x2="'+xv+'" y2="'+(MT+plotH)+'" stroke="'+t[2]+'" stroke-width="1.5" stroke-dasharray="2,4"/>');
      p.push('<text x="'+xv+'" y="'+(MT-6)+'" text-anchor="middle" font-size="13" font-weight="bold" fill="'+t[2]+'">'+t[1]+'</text>');
    });
    var medX=xp(med).toFixed(1);
    if(parseFloat(medX)>=ML&&parseFloat(medX)<=ML+plotW){
      p.push('<line x1="'+medX+'" y1="'+MT+'" x2="'+medX+'" y2="'+(MT+plotH)+'" stroke="#27ae60" stroke-width="2.5" stroke-dasharray="6,3"/>');
      p.push('<text x="'+medX+'" y="'+(MT-6)+'" text-anchor="middle" font-size="16" font-weight="bold" fill="#1a6e2b">Median</text>');
    }
    if(tgt){
      var tgtX=xp(100).toFixed(1);
      if(parseFloat(tgtX)>=ML&&parseFloat(tgtX)<=ML+plotW){
        p.push('<line x1="'+tgtX+'" y1="'+MT+'" x2="'+tgtX+'" y2="'+(MT+plotH)+'" stroke="#8e44ad" stroke-width="2" stroke-dasharray="3,3"/>');
        p.push('<text x="'+tgtX+'" y="'+(MT+plotH+44)+'" text-anchor="middle" font-size="16" font-weight="bold" fill="#8e44ad">'+((100).toFixed(1))+'%</text>');
        p.push('<text x="'+tgtX+'" y="'+(MT-6)+'" text-anchor="middle" font-size="14" font-weight="bold" fill="#8e44ad">Target</text>');
      }
    } else {
      /* Raw-value chart: shade OOS regions, then draw spec lines */
      if(meta.lsl!=null&&nLo>0){
        var lslXf=xp(meta.lsl);
        var oosR=Math.min(lslXf,ML+plotW),oosL=ML;
        if(oosR>oosL){
          p.push('<rect x="'+oosL+'" y="'+MT+'" width="'+(oosR-oosL).toFixed(1)+'" height="'+plotH+'" fill="rgba(231,76,60,0.13)" stroke="none"/>');
          var lx=((oosL+oosR)/2).toFixed(1);
          p.push('<text x="'+lx+'" y="'+(MT+20)+'" text-anchor="middle" font-size="17" font-weight="bold" fill="#c0392b">'+pctLo.toFixed(1)+'%</text>');
          p.push('<text x="'+lx+'" y="'+(MT+36)+'" text-anchor="middle" font-size="13" fill="#c0392b">below LSL</text>');
        }
      }
      if(meta.usl!=null&&nHi>0){
        var uslXf=xp(meta.usl);
        var oosL2=Math.max(uslXf,ML),oosR2=ML+plotW;
        if(oosR2>oosL2){
          p.push('<rect x="'+oosL2.toFixed(1)+'" y="'+MT+'" width="'+(oosR2-oosL2).toFixed(1)+'" height="'+plotH+'" fill="rgba(231,76,60,0.13)" stroke="none"/>');
          var rx=((oosL2+oosR2)/2).toFixed(1);
          p.push('<text x="'+rx+'" y="'+(MT+20)+'" text-anchor="middle" font-size="17" font-weight="bold" fill="#c0392b">'+pctHi.toFixed(1)+'%</text>');
          p.push('<text x="'+rx+'" y="'+(MT+36)+'" text-anchor="middle" font-size="13" fill="#c0392b">above USL</text>');
        }
      }
      var rawSpecLines=[];
      if(meta.target!=null)rawSpecLines.push([meta.target,'Target','#8e44ad',3,3]);
      if(meta.lsl!=null)rawSpecLines.push([meta.lsl,'LSL','#e74c3c',4,3]);
      if(meta.usl!=null)rawSpecLines.push([meta.usl,'USL','#e74c3c',4,3]);
      rawSpecLines.forEach(function(sl){
        var rv=sl[0],rlbl=sl[1],rcol=sl[2],rd1=sl[3],rd2=sl[4];
        var rxp=xp(rv).toFixed(1);
        if(parseFloat(rxp)<ML||parseFloat(rxp)>ML+plotW)return;
        p.push('<line x1="'+rxp+'" y1="'+MT+'" x2="'+rxp+'" y2="'+(MT+plotH)+'" stroke="'+rcol+'" stroke-width="2" stroke-dasharray="'+rd1+','+rd2+'"/>');
        p.push('<text x="'+rxp+'" y="'+(MT+plotH+44)+'" text-anchor="middle" font-size="16" font-weight="bold" fill="'+rcol+'">'+_fmt(rv)+'</text>');
        p.push('<text x="'+rxp+'" y="'+(MT-6)+'" text-anchor="middle" font-size="14" font-weight="bold" fill="'+rcol+'">'+rlbl+'</text>');
      });
    }
    for(var xi=0;xi<=6;xi++){
      var xv2=xLo+xRng*xi/6;
      var xpv=(ML+xi/6*plotW).toFixed(1);
      var xlbl=tgt?xv2.toFixed(1)+'%':_fmt(xv2);
      p.push('<text x="'+xpv+'" y="'+(MT+plotH+24)+'" text-anchor="middle" font-size="18" font-weight="bold" fill="#111">'+xlbl+'</text>');
    }
    p.push('<text x="'+(ML+plotW/2)+'" y="'+(svgH-4)+'" text-anchor="middle" font-size="18" font-weight="bold" fill="#111">'
      +'&lt;'+esc(param)+'&gt;'+(meta.name?'('+esc(meta.name)+')':'')+' \u2014 '+esc(xSuffix)+esc(tgtStr)+'</text>');
    var cv=(med&&med!==0)?Math.abs(sd/med*100).toFixed(1):'\u2014';
    var medLbl=tgt?med.toFixed(1)+'%':_fmt(med);
    var sdLbl=tgt?sd.toFixed(1)+'%':_fmt(sd);
    p.push('<text x="'+(ML+plotW-2)+'" y="'+(MT+22)+'" text-anchor="end" font-size="16" fill="#222">N='+normVals.length+' | Med='+medLbl+' | \u03c3='+sdLbl+' | Spread='+cv+'%</text>');
    var ly=MT+12;
    p.push('<rect x="'+(ML+plotW+6)+'" y="'+ly+'" width="14" height="14" fill="#27ae60" rx="2"/>');
    p.push('<text x="'+(ML+plotW+24)+'" y="'+(ly+7)+'" dominant-baseline="middle" font-size="16" font-weight="bold" fill="#111">Median</text>');
    p.push('<line x1="'+(ML+plotW+6)+'" y1="'+(ly+30)+'" x2="'+(ML+plotW+20)+'" y2="'+(ly+30)+'" stroke="#e67e22" stroke-width="2" stroke-dasharray="4,3"/>');
    p.push('<text x="'+(ML+plotW+24)+'" y="'+(ly+30)+'" dominant-baseline="middle" font-size="16" font-weight="bold" fill="#d35400">\u00b13\u03c3</text>');
    p.push('<line x1="'+(ML+plotW+6)+'" y1="'+(ly+50)+'" x2="'+(ML+plotW+20)+'" y2="'+(ly+50)+'" stroke="#c0392b" stroke-width="1.5" stroke-dasharray="2,4"/>');
    p.push('<text x="'+(ML+plotW+24)+'" y="'+(ly+50)+'" dominant-baseline="middle" font-size="14" font-weight="bold" fill="#c0392b">\u00b16\u03c3</text>');
    if(tgt){
      p.push('<line x1="'+(ML+plotW+6)+'" y1="'+(ly+70)+'" x2="'+(ML+plotW+20)+'" y2="'+(ly+70)+'" stroke="#8e44ad" stroke-width="2" stroke-dasharray="3,3"/>');
      p.push('<text x="'+(ML+plotW+24)+'" y="'+(ly+70)+'" dominant-baseline="middle" font-size="16" font-weight="bold" fill="#8e44ad">Target</text>');
    } else {
      var legOff=70;
      if(meta.target!=null){
        p.push('<line x1="'+(ML+plotW+6)+'" y1="'+(ly+legOff)+'" x2="'+(ML+plotW+20)+'" y2="'+(ly+legOff)+'" stroke="#8e44ad" stroke-width="2" stroke-dasharray="3,3"/>');
        p.push('<text x="'+(ML+plotW+24)+'" y="'+(ly+legOff)+'" dominant-baseline="middle" font-size="16" font-weight="bold" fill="#8e44ad">Target</text>');
        legOff+=20;
      }
      if(meta.lsl!=null){
        p.push('<line x1="'+(ML+plotW+6)+'" y1="'+(ly+legOff)+'" x2="'+(ML+plotW+20)+'" y2="'+(ly+legOff)+'" stroke="#e74c3c" stroke-width="2" stroke-dasharray="4,3"/>');
        p.push('<text x="'+(ML+plotW+24)+'" y="'+(ly+legOff)+'" dominant-baseline="middle" font-size="16" font-weight="bold" fill="#e74c3c">LSL</text>');
        legOff+=20;
      }
      if(meta.usl!=null){
        p.push('<line x1="'+(ML+plotW+6)+'" y1="'+(ly+legOff)+'" x2="'+(ML+plotW+20)+'" y2="'+(ly+legOff)+'" stroke="#e74c3c" stroke-width="2" stroke-dasharray="4,3"/>');
        p.push('<text x="'+(ML+plotW+24)+'" y="'+(ly+legOff)+'" dominant-baseline="middle" font-size="16" font-weight="bold" fill="#e74c3c">USL</text>');
      }
    }
    p.push('</svg>');
    /* ── Stats table above chart ─────────────────────────────── */
    var sortedV=normVals.slice().sort(function(a,b){return a-b;});
    function _pctV(arr,p){
      if(!arr.length)return null;
      var idx=(p/100)*(arr.length-1);
      var lo=Math.floor(idx),hi=Math.ceil(idx);
      return arr[lo]+(arr[hi]-arr[lo])*(idx-lo);
    }
    var p1v=_pctV(sortedV,1),p99v=_pctV(sortedV,99);
    var statUnit=tgt?'%':(meta.unit||'');
    function _sfmt(v){
      if(v==null)return '\u2014';
      if(tgt)return v.toFixed(2)+'%';
      var raw=_fmt(v)+(statUnit?' '+statUnit:'');
      if(meta.target!=null&&meta.target!==0&&isFinite(v)&&isFinite(meta.target)){
        var pct=(v/meta.target*100).toFixed(1);
        raw+=' <span style="color:#888;font-size:10px;font-weight:normal">('+pct+'%)</span>';
      }
      return raw;
    }
    /* ── OOS already computed above for SVG annotation ── */
    var hasOOS=(nLo+nHi)>0;
    var statCells=[
      ['N',nTotal.toLocaleString(),'#555'],
      ['Median',_sfmt(med),'#1a6e2b'],
      ['\u03c3',_sfmt(sd),'#2471a3'],
      ['Spread (%)',med&&med!==0?(Math.abs(sd/med*100).toFixed(1)+'%'):'\u2014','#555'],
      ['P1',_sfmt(p1v),'#555'],
      ['P99',_sfmt(p99v),'#555']
    ];
    if(!tgt){
      if(meta.target!=null)statCells.push(['Target',_sfmt(meta.target),'#8e44ad']);
      if(meta.lsl!=null)statCells.push(['LSL',_sfmt(meta.lsl),'#e74c3c']);
      if(meta.usl!=null)statCells.push(['USL',_sfmt(meta.usl),'#e74c3c']);
    } else {
      if(meta.lsl!=null||meta.usl!=null){
        if(meta.lsl!=null)statCells.push(['LSL',_fmt(meta.lsl)+(meta.unit?' '+meta.unit:''),'#e74c3c']);
        if(meta.usl!=null)statCells.push(['USL',_fmt(meta.usl)+(meta.unit?' '+meta.unit:''),'#e74c3c']);
      }
    }
    /* Append fail % cells — always last, bright red if non-zero */
    if(meta.lsl!=null&&nLo>0)
      statCells.push(['% < LSL',pctLo.toFixed(1)+'% ('+nLo+')','#c0392b']);
    if(meta.usl!=null&&nHi>0)
      statCells.push(['% > USL',pctHi.toFixed(1)+'% ('+nHi+')','#c0392b']);
    /* σ-based fail cells — shown only when no spec limits defined */
    if(!hasSpecLimits){
      if(nOut6>0)statCells.push(['\u00b16\u03c3 out',pctOut6.toFixed(1)+'% ('+nOut6+')','#c0392b']);
    }
    var _isSpecOOS=(nLo+nHi)>0;
    var cardBorder=hasOOS?('2px solid '+(_isSpecOOS?'#e74c3c':'#e67e22')):'1px solid rgba(0,0,0,0)';
    var cardBg=hasOOS?(_isSpecOOS?'#fff8f8':'#fffbf5'):'#fff';
    var oosTag=hasOOS
      ?'<span style="float:right;margin-left:8px;padding:1px 7px;border-radius:3px;'
        +'background:'+(_isSpecOOS?'#e74c3c':'#d35400')+';color:#fff;font-size:10px;font-weight:bold;letter-spacing:.3px">'
        +'\u26a0 '+(_isSpecOOS?'OUT OF SPEC':'OUTSIDE \u00b13\u03c3')+'</span>'
      :'';
    var statBorderCol=hasOOS?(_isSpecOOS?'#fad7d7':'#fde8d0'):'#e8ecf0';
    var statHtml='<div style="display:flex;flex-wrap:wrap;gap:2px 0;margin-bottom:4px;border:1px solid '+statBorderCol+';border-radius:5px;overflow:hidden;font-size:11px;'+(hasOOS?('background:'+(_isSpecOOS?'#fff0f0':'#fff9f2')+';'):'') + '">';
    statCells.forEach(function(sc){
      var isFailCell=(sc[0]==='% < LSL'||sc[0]==='% > USL');
      var isSigCell=(sc[0]==='\u00b13\u03c3 out'||sc[0]==='\u00b16\u03c3 out');
      var cellBg=isFailCell?'background:#fdecea;':isSigCell?'background:#fef3e0;':'';
      statHtml+='<div style="display:flex;flex-direction:column;align-items:center;padding:3px 10px;border-right:1px solid '+statBorderCol+';flex:1;min-width:60px;'+cellBg+'">'
        +'<span style="color:'+((isFailCell||isSigCell)?sc[2]:'#888')+';font-size:9px;font-weight:bold;text-transform:uppercase;letter-spacing:.5px">'+sc[0]+'</span>'
        +'<span style="color:'+sc[2]+';font-weight:bold;font-size:12px;white-space:nowrap">'+sc[1]+'</span>'
        +'</div>';
    });
    statHtml+='</div>';
    var grpLegHtml='';
    if(nGrps>1){
      grpLegHtml='<div style="display:flex;flex-wrap:wrap;gap:4px 12px;padding:4px 6px 2px;font-size:11px;border-top:1px solid #eee;margin-top:2px">';
      grpOrder.forEach(function(gk,gi2){
        var gcol=cm.map[gk]||_cPal(gi2);
        grpLegHtml+='<span style="display:flex;align-items:center;gap:3px">'
          +'<span style="width:11px;height:11px;background:'+gcol+';display:inline-block;border-radius:2px;flex-shrink:0;opacity:0.85"></span>'
          +'<span style="color:#2c3e50;word-break:break-all">'+esc(gk)+'</span></span>';
      });
      grpLegHtml+='</div>';
    }
    var specParts=[];
    if(meta.lsl!=null)specParts.push('LSL='+_fmt(meta.lsl));
    if(meta.usl!=null)specParts.push('USL='+_fmt(meta.usl));
    if(tgt!=null)specParts.push('Target='+_fmt(tgt)+(meta.unit?' '+meta.unit:''));
    else if(meta.unit)specParts.push(meta.unit);
    var nameLbl=meta.name?' <span style="font-weight:normal;color:#5d6d7e;font-size:20px">('+esc(meta.name)+')</span>':'';
    var specLbl=specParts.length?' <span style="font-weight:normal;color:#7f8c8d;font-size:20px">['+specParts.map(function(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;');}).join(', ')+']</span>':'';
    var paramDisp='&lt;'+esc(param)+'&gt;';
    var csvBtn='<button onclick="downloadPdlyCSV(\''+param.replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\''
      +') " title="Download histogram data as CSV"'
      +' style="float:right;margin-left:8px;padding:2px 9px;font-size:10px;font-weight:bold;'
      +'border:none;border-radius:3px;background:#27ae60;color:#fff;cursor:pointer"'
      +' onmouseover="this.style.background=\'#1e8449\'" onmouseout="this.style.background=\'#27ae60\'">&#11015; CSV</button>';
    html+='<div style="background:'+cardBg+';border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.10);padding:8px 10px;border:'+cardBorder+'">'
         +'<div style="font-weight:bold;font-size:24px;color:#1a252f;margin-bottom:4px">'+csvBtn+oosTag+paramDisp+nameLbl+specLbl+'</div>'
         +statHtml+p.join('')+grpLegHtml+'</div>';
  });
  html+='</div>';
  return html;
}

/* ── RO Distribution panel collapse state ───────────────────────────────── */
var _PDLY_P_COLLAPSED=(function(){var o={};for(var i=1;i<=PCM_DIST_PANELS.length+1;i++){o[i]=false;}return o;}());
function togglePdlyP(n){
  _PDLY_P_COLLAPSED[n]=!_PDLY_P_COLLAPSED[n];
  var body=document.getElementById('pdlyp'+n+'-body');
  var btn=document.getElementById('pdlyp'+n+'-toggle');
  if(body)body.style.display=_PDLY_P_COLLAPSED[n]?'none':'';
  if(btn)btn.innerHTML=_PDLY_P_COLLAPSED[n]?'&#9654;':'&#9660;';
}

function buildPropDelayTab(){
  var ak=activeKeys();

  /* ── Configured panels (JSON-driven, N = PCM_DIST_PANELS.length) ─── */
  for(var _pn=1;_pn<=PCM_DIST_PANELS.length;_pn++){
    var _pb=document.getElementById('pdlyp'+_pn+'-body');
    if(_pb&&!_PDLY_P_COLLAPSED[_pn]){
      if(!_PDLY_SEL_P[_pn])_PDLY_SEL_P[_pn]=_pdlyPDefault(_pn);
      var _all=_pdlyAllParamsForP(_pn);
      var _params=_all.filter(function(p){return _PDLY_SEL_P[_pn].has(p);});
      if(!_params.length)_params=Array.from(_pdlyPDefault(_pn));
      var _bar=_buildPdlyPanelBar(_pn,_all,_PDLY_SEL_P[_pn],_PDLY_GRP_P[_pn],_PDLY_H_P[_pn]);
      var _oldH=_PDLY_H;_PDLY_H=_PDLY_H_P[_pn];
      var _cards=_params.length?_buildPdlyCards(_params,ak,_PDLY_GBY_P[_pn]||[]):'<div style="padding:16px;color:#888;font-style:italic;font-size:12px">Parameters not found in dataset.</div>';
      _PDLY_H=_oldH;
      _pb.innerHTML=_bar+'<div style="padding:0 14px 10px">'+_cards+'</div>';
    }
  }

  /* ── Custom panel (always N+1) ─────────────────────────────────────── */
  var _NC=PCM_DIST_PANELS.length+1;
  var b5=document.getElementById('pdlyp'+_NC+'-body');
  if(!b5||_PDLY_P_COLLAPSED[_NC])return;
  var allParams=_pdlyAllParams();
  if(!allParams.length){
    b5.innerHTML='<div style="padding:24px;color:#888;font-size:13px">No Propagation Delay parameters found in this dataset.</div>';
    return;
  }
  if(_PDLY_SEL===null)_PDLY_SEL=_pdlyDefault(allParams);
  var params3=allParams.filter(function(p){return _PDLY_SEL.has(p);});
  if(!params3.length){_PDLY_SEL=_pdlyDefault(allParams);params3=allParams.filter(function(p){return _PDLY_SEL.has(p);});}
  params3.sort(function(a,b){
    function _pairPri(p){var lo=p.toLowerCase();
      if(lo==='td_rj4u')return 0;
      if(lo.indexOf('upm')>=0&&lo.indexOf('0107')>=0&&lo.indexOf('950')>=0&&lo.indexOf('sds')>=0)return 1;
      if(lo==='td_rk4u')return 2;
      if(lo.indexOf('upm')>=0&&lo.indexOf('0704')>=0&&lo.indexOf('950')>=0&&lo.indexOf('sds')>=0)return 3;
      return 100;}
    var pa=_pairPri(a),pb=_pairPri(b);
    if(pa!==pb)return pa-pb;
    return a<b?-1:a>b?1:0;
  });
  var tdParams3=allParams.filter(function(p){return p.match(/^Td_/i);});
  var otherParams3=allParams.filter(function(p){return !p.match(/^Td_/i);});
  var selOtherCnt=otherParams3.filter(function(p){return _PDLY_SEL.has(p);}).length;
  /* Group filter buttons for Panel 3 (uses existing setPdlyGrp / pdly-grp-btn system) */
  var gBtns3='<button class="pdly-grp-btn" data-grp="" onclick="setPdlyGrp(\'\')" style="font-size:11px;padding:2px 9px;border-radius:4px;border:none;cursor:pointer;color:#fff;background:rgba(0,0,0,0.25);font-weight:normal">All</button>';
  PCM_GROUPS.forEach(function(g){
    gBtns3+=' <button class="pdly-grp-btn" data-grp="'+esc(g)+'" onclick="setPdlyGrp(\''+g.replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\') " style="font-size:11px;padding:2px 9px;border-radius:4px;border:none;cursor:pointer;color:#fff;background:rgba(0,0,0,0.25);font-weight:normal">'+esc(g)+'</button>';
  });
  var _nc_gby=_PDLY_GBY_P[_NC]||[];
  var gby3='<span style="width:1px;background:#4a6278;align-self:stretch;margin:0 4px"></span>'
    +'<b style="color:#f1c40f;margin-right:4px;font-size:12px">Group by:</b>'
    +'<label style="cursor:pointer;font-size:12px;color:#ecf0f1"><input type="checkbox" value="none" onchange="toggleGbyP('+_NC+',\'none\')"'+(_nc_gby.length===0?' checked':'')+'>  None</label>'
    +'<label style="cursor:pointer;font-size:12px;color:#ecf0f1"><input type="checkbox" value="lot" onchange="toggleGbyP('+_NC+',\'lot\')"'+(_nc_gby.indexOf('lot')>=0?' checked':'')+'>  Lot</label>'
    +'<label style="cursor:pointer;font-size:12px;color:#ecf0f1"><input type="checkbox" value="wafer" onchange="toggleGbyP('+_NC+',\'wafer\')"'+(_nc_gby.indexOf('wafer')>=0?' checked':'')+'>  Wafer</label>'
    +'<label style="cursor:pointer;font-size:12px;color:#ecf0f1"><input type="checkbox" value="layout" onchange="toggleGbyP('+_NC+',\'layout\')"'+(_nc_gby.indexOf('layout')>=0?' checked':'')+'>  Layout</label>'
    +'<label style="cursor:pointer;font-size:12px;color:#ecf0f1"><input type="checkbox" value="material" onchange="toggleGbyP('+_NC+',\'material\')"'+(_nc_gby.indexOf('material')>=0?' checked':'')+'>  Material</label>';
  var bar3='<div style="display:flex;flex-wrap:wrap;align-items:center;padding:6px 14px;gap:6px;flex-shrink:0;background:#1f3a50;border-bottom:1px solid #1a252f">'
    +'<span style="color:#aed6f1;font-size:11px">X: Freq% of target &nbsp;|&nbsp; Y: Samples</span>'
    +'<span style="width:1px;background:#4a6278;align-self:stretch;margin:0 4px"></span>'
    +'<span style="color:#f1c40f;font-size:12px;font-weight:bold">Group:</span>'+gBtns3
    +gby3
    +'<span style="width:1px;background:#4a6278;align-self:stretch;margin:0 4px"></span>'
    +'<label style="display:flex;align-items:center;gap:4px;cursor:default;color:#ecf0f1;font-size:12px">&#11041; Height'
    +'<input type="range" min="150" max="900" step="25" value="'+_PDLY_H+'"'
    +' oninput="_PDLY_H=+this.value;document.getElementById(\'pdly\'+_NC+\'-h-val\').textContent=this.value+\'px\';buildPropDelayTab()"'
    +' style="width:90px;accent-color:#3498db">'
    +'<span id="pdly'+_NC+'-h-val" style="min-width:34px;color:#aed6f1;font-size:10px">'+_PDLY_H+'px</span></label></div>';
  /* Prop. delay pills + Other dropdown */
  var pillHtml='<div style="display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin:8px 14px 6px;padding:6px 8px;background:#f0f4fb;border-radius:6px;border:1px solid #dde">';
  if(tdParams3.length){
    pillHtml+='<span style="font-size:10px;color:#7f8c8d;font-weight:bold;margin-right:2px;flex-shrink:0">Prop. Delay:</span>';
    tdParams3.forEach(function(p){
      var isSel=_PDLY_SEL.has(p);
      var meta=PCM_PARAM_META[p]||{};
      var nm=(meta.name||'').trim();
      var tip=(meta.lsl!=null?'LSL='+_fmt(meta.lsl)+' ':'')+(meta.usl!=null?'USL='+_fmt(meta.usl)+' ':'')+(meta.unit||'');
      pillHtml+='<button onclick="togglePdlyParam(\''+p.replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\') " title="'+esc(tip.trim())+'"'
        +' style="padding:3px 12px;font-size:11px;border-radius:6px;border:1px solid '+(isSel?'#2980b9':'#bdc3c7')+';background:'+(isSel?'#2980b9':'#f8f9fa')+';color:'+(isSel?'#fff':'#2c3e50')+';cursor:pointer;font-weight:'+(isSel?'bold':'normal')+'">'
        +'&lt;'+esc(p)+'&gt;'+(nm?'<span style="font-size:9px;font-weight:normal;opacity:0.8;margin-left:3px">('+esc(nm)+')</span>':'')+'</button>';
    });
  }
  if(otherParams3.length){
    if(tdParams3.length)pillHtml+='<span style="display:inline-block;width:1px;background:#bdc3c7;align-self:stretch;margin:0 6px"></span>';
    pillHtml+='<span style="font-size:10px;color:#7f8c8d;font-weight:bold;margin-right:2px;flex-shrink:0">Other:</span>'
      +'<div style="position:relative;display:inline-block">'
      +'<button id="pdly-drop-btn" onclick="_pdlyDropToggle()" style="padding:3px 10px 3px 12px;font-size:11px;border-radius:6px;border:1px solid '+(selOtherCnt?'#2980b9':'#bdc3c7')+';background:'+(selOtherCnt?'#eaf4ff':'#f8f9fa')+';color:#2c3e50;cursor:pointer;font-weight:'+(selOtherCnt?'bold':'normal')+'">'
      +(selOtherCnt?selOtherCnt+' selected':'Select params')+' &#9660;</button>'
      +'<div id="pdly-drop" style="display:none;position:absolute;top:calc(100% + 3px);left:0;z-index:9999;background:#fff;border:1px solid #bdc3c7;border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.18);width:310px;max-height:320px;flex-direction:column">'
      +'<div style="padding:5px 6px;border-bottom:1px solid #eee;display:flex;gap:4px;align-items:center">'
      +'<input id="pdly-drop-srch" type="text" placeholder="Search\u2026" oninput="_pdlyDropSearch(this.value)" style="flex:1;padding:3px 6px;font-size:11px;border:1px solid #ccc;border-radius:4px">'
      +'<button onclick="_pdlyDropSelAll()" style="font-size:10px;padding:2px 6px;border:1px solid #ccc;border-radius:3px;background:#f8f9fa;cursor:pointer;flex-shrink:0">All</button>'
      +'<button onclick="_pdlyDropClrAll()" style="font-size:10px;padding:2px 6px;border:1px solid #ccc;border-radius:3px;background:#f8f9fa;cursor:pointer;flex-shrink:0">None</button>'
      +'</div>'
      +'<div id="pdly-drop-list" style="overflow-y:auto;max-height:260px;padding:2px 0"></div>'
      +'</div></div>';
  }
  pillHtml+='</div>';
  /* Update Panel 3 group button highlights */
  document.querySelectorAll('.pdly-grp-btn').forEach(function(b){
    var bgrp=b.dataset.grp||null;
    var active=(bgrp===_PDLY_GRP)||(bgrp===''&&_PDLY_GRP===null);
    b.style.background=active?'#2980b9':'rgba(0,0,0,0.25)';
    b.style.fontWeight=active?'bold':'normal';
  });
  b5.innerHTML=bar3+pillHtml+'<div style="padding:0 14px 10px">'+_buildPdlyCards(params3,ak,_PDLY_GBY_P[_NC]||[])+'</div>';
  /* Re-apply group button highlight after innerHTML update */
  document.querySelectorAll('.pdly-grp-btn').forEach(function(b){
    var bgrp=b.dataset.grp||null;
    var active=(bgrp===_PDLY_GRP)||(bgrp===''&&_PDLY_GRP===null);
    b.style.background=active?'#2980b9':'rgba(0,0,0,0.25)';
    b.style.fontWeight=active?'bold':'normal';
  });
}

/* ── XY Scatter Plot tab ─────────────────────────────────────────────────── */
var _XY_XGRP=null;  /* null = all groups for X axis */
var _XY_YGRP=null;  /* null = all groups for Y axis */
/* XY tab (Panel 2): X = Td_* (propagation delay), Y = UPM */
var _XY_X=(function(){
  /* Configured default: xy panel index 1, left (a) X */
  if(PCM_XY_PANELS.length>1){var _c=PCM_XY_PANELS[1];if(_c&&_c.a&&_c.a.x&&_c.a.x in PCM_PARAM_META)return _c.a.x;}
  /* Hardcoded fallback */
  if('Td_RJ4u' in PCM_PARAM_META)return 'Td_RJ4u';
  var keys=Object.keys(PCM_PARAM_META);
  var tds=keys.filter(function(p){return p.match(/^Td_RJ/i);});
  if(tds.length)return tds[0];
  tds=keys.filter(function(p){return p.match(/^Td_/i);});
  if(tds.length)return tds[0];
  return keys[0]||'';
}());
var _XY_Y=null;var _XY_DIE=true;
var _XY_YS=[];     /* multi-Y selected params; when length>1 they act as the group-by */
var _XY_YSRCH=''; /* live search text inside Y checklist dropdown */
var _XY_LOG_X=false;var _XY_LOG_Y=false;
var _XY_TREND='ols';  /* 'none'|'ols'|'theilsen' */
var _XY_XMIN=null;var _XY_XMAX=null;var _XY_YMIN=null;var _XY_YMAX=null;
var _DRAG_CUR_A={x:null,y:null};  /* XY drag cursor A — persists across redraws */
var _DRAG_CUR_B={};               /* XY drag cursor B — keyed by plot prefix */

function _xyDefaultY(){
  /* Configured default: xy panel index 1, left (a) first Y */
  if(PCM_XY_PANELS.length>1){var _c=PCM_XY_PANELS[1];if(_c&&_c.a&&_c.a.ys&&_c.a.ys.length){var _fy=_c.a.ys[0];if(_fy in PCM_PARAM_META)return _fy;}}
  /* Hardcoded fallback */
  var keys=Object.keys(PCM_PARAM_META);
  for(var i=0;i<keys.length;i++){var lo=keys[i].toLowerCase();if(lo.indexOf('upm')>=0&&lo.indexOf('ulvt')>=0&&lo.indexOf('0107')>=0&&lo.indexOf('950')>=0&&lo.indexOf('sds')>=0)return keys[i];}
  var upms=keys.filter(function(p){return p.toLowerCase().indexOf('upm')>=0;});
  if(upms.length)return upms[0];
  return keys[0]||null;
}

function _populateXYDl(dlId){} /* no-op: replaced by custom AC */

function _xyItemsForGrp(grp){
  var params=grp?(PCM_GROUP_PARAMS[grp]||[]):Object.keys(PCM_PARAM_META);
  return params.filter(function(p){return p in PCM_PARAM_META;}).sort().map(function(p){
    var meta=PCM_PARAM_META[p]||{};
    return{key:p,nm:meta.name||'',isSrt:!!(meta.is_sort)};
  });
}
function _xyBuildSelects(){
  /* X axis only — Y axis uses a checkbox checklist instead */
  var sel=document.getElementById('xy-sel-x');if(!sel)return;
  var params=_xyItemsForGrp(_XY_XGRP);
  var html='';
  params.forEach(function(it){
    var nm=it.nm?(it.isSrt?' – '+it.nm:' ('+it.nm+')'):'';
    html+='<option value="'+esc(it.key)+'"'+(it.key===_XY_X?' selected':'')+'>'+esc(it.key)+esc(nm)+'</option>';
  });
  sel.innerHTML=html;
  if(params.length&&!params.some(function(it){return it.key===_XY_X;})){
    _XY_X=params[0].key;sel.value=_XY_X;
  }
}
var _XY_YS_SEEDED=false; /* true once auto-seed ran; stops re-seed after an explicit Clr */
/* Ensure _XY_YS is seeded from _XY_Y on first use only */
function _xyYsEnsure(){
  if(_XY_YS_SEEDED)return;
  if(!_XY_YS.length){
    var y=_XY_Y||_xyDefaultY();
    if(y){_XY_YS=[y];_XY_Y=y;}
  }
  _XY_YS_SEEDED=true;
}
/* Toggle a Y param in _XY_YS */
function _xyToggleY(p){
  var i=_XY_YS.indexOf(p);
  if(i>=0){_XY_YS.splice(i,1);}else{_XY_YS.push(p);}
  _XY_Y=_XY_YS[0]||null;
  _XY_YS_SEEDED=true;  /* explicit user action — no auto-re-seed */
  buildXYTab();buildFixedPanels();
}
/* Clear all Y selections — checklist goes fully unchecked */
function _xyYClrAll(){
  _XY_YS=[];
  _XY_Y=null;
  _XY_YS_SEEDED=true;  /* prevent _xyYsEnsure from immediately re-seeding */
  _xyBuildYChecklist();
  buildXYTab();buildFixedPanels();
}
/* Select all currently visible (filtered) Y params */
function _xyYSelAll(){
  _xyYsEnsure();
  var items=_xyItemsForGrp(_XY_YGRP);
  var q=_XY_YSRCH.toLowerCase();
  var vis=q?items.filter(function(it){return(it.key+' '+it.nm).toLowerCase().indexOf(q)>=0;}):items;
  vis.forEach(function(it){if(_XY_YS.indexOf(it.key)<0)_XY_YS.push(it.key);});
  buildXYTab();buildFixedPanels();
}
/* Build the checkbox list inside the Y dropdown */
function _xyBuildYChecklist(){
  var el=document.getElementById('xy-y-list');if(!el)return;
  _xyYsEnsure();
  var items=_xyItemsForGrp(_XY_YGRP);
  var q=_XY_YSRCH.toLowerCase();
  var vis=q?items.filter(function(it){return(it.key+' '+it.nm).toLowerCase().indexOf(q)>=0;}):items;
  var html='';
  vis.forEach(function(it){
    var chk=_XY_YS.indexOf(it.key)>=0;
    var nm=it.nm?'<span style="color:#888;font-size:10px"> ('+esc(it.nm)+')</span>':'';
    html+='<label style="display:flex;align-items:center;gap:5px;padding:2px 6px;cursor:pointer;border-radius:3px;white-space:nowrap"'
      +' onmouseover="this.style.background=\'#e8f0fe\'" onmouseout="this.style.background=\'\'">'
      +'<input type="checkbox"'+(chk?' checked':'')
      +' onchange="_xyToggleY(\''+esc(it.key)+'\')" style="cursor:pointer">'
      +'<b style="font-size:11px">'+esc(it.key)+'</b>'+nm+'</label>';
  });
  el.innerHTML=html||'<div style="padding:6px;color:#aaa;font-size:11px">No params</div>';
  /* Update button label */
  var btn=document.getElementById('xy-y-btn');
  if(btn){
    var cnt=_XY_YS.length;
    btn.textContent=cnt===0?'(none)':cnt>1?cnt+' Y params':_XY_YS[0];
    btn.style.color=cnt===0?'#c0392b':cnt>1?'#1a6bb5':'';
    btn.style.fontWeight=cnt>1?'bold':'';
  }
}
/* Toggle the Y checklist dropdown open/closed */
function _xyYDropToggle(){
  var pop=document.getElementById('xy-y-drop');if(!pop)return;
  var vis=pop.style.display==='block';
  if(vis){pop.style.display='none';return;}
  pop.style.display='block';
  var srch=document.getElementById('xy-y-srch');
  if(srch){srch.value=_XY_YSRCH;srch.focus();}
  _xyBuildYChecklist();
}
/* Close XY Plot 1 & 2 Y dropdowns, and fp*-y-drop dropdowns if click lands outside */
document.addEventListener('click',function(e){
  /* Panel 1 XY plots */
  ['xy','xy2'].forEach(function(pfx){
    var drop=document.getElementById(pfx+'-y-drop');
    var btn=document.getElementById(pfx+'-y-btn');
    if(drop&&drop.style.display==='block'&&!drop.contains(e.target)&&e.target!==btn)
      drop.style.display='none';
  });
  /* Fixed panel plots (fp0a, fp0b, fp2a, fp2b, fp3a, fp3b) */
  ['fp0a','fp0b','fp2a','fp2b','fp3a','fp3b'].forEach(function(pid){
    var drop=document.getElementById(pid+'-y-drop');
    var btn=document.getElementById(pid+'-y-btn');
    if(drop&&drop.style.display==='block'&&!drop.contains(e.target)&&e.target!==btn)
      drop.style.display='none';
  });
},true);
function _xySetGrp(ax,grp){
  if(ax==='x')_XY_XGRP=grp||null;
  else _XY_YGRP=grp||null;
  buildXYTab();
}
function _xyAcInit(wrpId){
  if(_xyAcSt[wrpId])return;
  var wrp=document.getElementById(wrpId);if(!wrp)return;
  var pop=wrp.querySelector('.xy-ac-pop');if(!pop)return;
  _xyAcSt[wrpId]={items:_xyAcItems(),pop:pop};
}
function _xyAcRender(wrpId,q){
  var s=_xyAcSt[wrpId];if(!s)return;
  var ql=q.toLowerCase();
  var matched=ql?s.items.filter(function(it){return it.lc.indexOf(ql)>=0;}):s.items;
  var html='';
  matched.slice(0,300).forEach(function(it){
    var nm=it.nm?'<span style="color:#7f8c8d;font-size:11px"> ('+it.nm+')</span>':'';
    html+='<div class="xy-ac-item" onmousedown="_xyAcPick(\''+wrpId+'\',\''+it.key+'\')"><b>'+esc(it.key)+'</b>'+nm+'</div>';
  });
  s.pop.innerHTML=html||'<div style="padding:6px 10px;color:#aaa;font-size:11px">No match</div>';
}
function _xyAcShow(wrpId){
  _xyAcInit(wrpId);
  var s=_xyAcSt[wrpId];if(!s)return;
  var inp=document.getElementById(wrpId).querySelector('input');
  /* Save current display value so we can restore it on blur without a pick */
  s.prevVal=inp?inp.value:'';
  s.picked=false;
  if(inp)inp.value='';  /* clear so all params are shown */
  _xyAcRender(wrpId,'');
  Object.keys(_xyAcSt).forEach(function(id){if(id!==wrpId&&_xyAcSt[id])_xyAcSt[id].pop.style.display='none';});
  s.pop.style.display='block';
}
function _xyAcHide(wrpId){
  var s=_xyAcSt[wrpId];if(!s)return;
  setTimeout(function(){
    s.pop.style.display='none';
    /* Restore previous value if user didn't pick anything */
    if(!s.picked){
      var inp=document.getElementById(wrpId);
      inp=inp?inp.querySelector('input'):null;
      if(inp)inp.value=s.prevVal||'';
    }
  },220);
}
function _xyAcFilter(wrpId,val,setter){
  _xyAcInit(wrpId);
  _xyAcRender(wrpId,val);
  var s=_xyAcSt[wrpId];if(s)s.pop.style.display='block';
  if(val in PCM_PARAM_META){window[setter]=val;buildXYTab();}
}
function _xyAcPick(wrpId,key){
  var wrp=document.getElementById(wrpId);if(!wrp)return;
  var inp=wrp.querySelector('input');if(inp)inp.value=key;
  var setter=wrp.dataset.setter;
  window[setter]=key;
  var s=_xyAcSt[wrpId];
  if(s){s.picked=true;s.pop.style.display='none';}
  buildXYTab();
}

/* ── XY Plot 2 (side panel) ──────────────────────────────────────────────── */
var _XY2_XGRP=null;var _XY2_YGRP=null;
/* XY2 tab (Panel 3): X = Poff_* (power-off), Y = UPM */
var _XY2_X=(function(){
  /* Configured default: xy panel index 1, right (b) X */
  if(PCM_XY_PANELS.length>1){var _c=PCM_XY_PANELS[1];if(_c&&_c.b&&_c.b.x&&_c.b.x in PCM_PARAM_META)return _c.b.x;}
  /* Hardcoded fallback */
  if('Poff_RJ4u' in PCM_PARAM_META)return 'Poff_RJ4u';
  var keys=Object.keys(PCM_PARAM_META);
  var poffs=keys.filter(function(p){return p.match(/^Poff_RJ/i);});
  if(poffs.length)return poffs[0];
  poffs=keys.filter(function(p){return p.match(/^Poff_/i);});
  if(poffs.length)return poffs[0];
  return keys[0]||'';
}());
var _XY2_Y=null;var _XY2_DIE=true;
var _XY2_YS=[];var _XY2_YSRCH='';
var _XY2_LOG_X=false;var _XY2_LOG_Y=false;
var _XY2_TREND='ols';
var _XY2_XMIN=null;var _XY2_XMAX=null;var _XY2_YMIN=null;var _XY2_YMAX=null;
var _XY2_YS_SEEDED=false;

function _xy2DefaultY(){
  /* Configured default: xy panel index 1, right (b) Y list */
  if(PCM_XY_PANELS.length>1){var _c=PCM_XY_PANELS[1];if(_c&&_c.b&&_c.b.ys&&_c.b.ys.length){var _ys=_c.b.ys.filter(function(p){return p in PCM_PARAM_META;});if(_ys.length)return _ys;}}
  /* Hardcoded fallback */
  var keys=Object.keys(PCM_PARAM_META);
  for(var i=0;i<keys.length;i++){var lo=keys[i].toLowerCase();if(lo.indexOf('upm')>=0&&lo.indexOf('ulvt')>=0&&lo.indexOf('0107')>=0&&lo.indexOf('950')>=0&&lo.indexOf('sds')>=0)return[keys[i]];}
  var upms=keys.filter(function(p){return p.toLowerCase().indexOf('upm')>=0;});
  if(upms.length)return[upms[0]];
  return keys.length?[keys[0]]:[];
}

function _xy2ItemsForGrp(grp){
  var params=grp?(PCM_GROUP_PARAMS[grp]||[]):Object.keys(PCM_PARAM_META);
  return params.filter(function(p){return p in PCM_PARAM_META;}).sort().map(function(p){
    var meta=PCM_PARAM_META[p]||{};
    return{key:p,nm:meta.name||'',isSrt:!!(meta.is_sort)};
  });
}
function _xy2BuildSelects(){
  var sel=document.getElementById('xy2-sel-x');if(!sel)return;
  var params=_xy2ItemsForGrp(_XY2_XGRP);
  var html='';
  params.forEach(function(it){
    var nm=it.nm?(it.isSrt?' \u2013 '+it.nm:' ('+it.nm+')'):'';
    html+='<option value="'+esc(it.key)+'"'+(it.key===_XY2_X?' selected':'')+'>'+esc(it.key)+esc(nm)+'</option>';
  });
  sel.innerHTML=html;
  if(params.length&&!params.some(function(it){return it.key===_XY2_X;})){
    _XY2_X=params[0].key;sel.value=_XY2_X;
  }
}
function _xy2YsEnsure(){
  if(_XY2_YS_SEEDED)return;
  if(!_XY2_YS.length){
    var dflt=_xy2DefaultY();
    if(dflt.length){_XY2_YS=dflt;_XY2_Y=dflt[0];}
  }
  _XY2_YS_SEEDED=true;
}
function _xy2ToggleY(p){
  var i=_XY2_YS.indexOf(p);
  if(i>=0){_XY2_YS.splice(i,1);}else{_XY2_YS.push(p);}
  _XY2_Y=_XY2_YS[0]||null;
  _XY2_YS_SEEDED=true;
  buildXY2Tab();
}
function _xy2YClrAll(){
  _XY2_YS=[];_XY2_Y=null;_XY2_YS_SEEDED=true;
  _xy2BuildYChecklist();buildXY2Tab();
}
function _xy2YSelAll(){
  _xy2YsEnsure();
  var items=_xy2ItemsForGrp(_XY2_YGRP);
  var q=_XY2_YSRCH.toLowerCase();
  var vis=q?items.filter(function(it){return(it.key+' '+it.nm).toLowerCase().indexOf(q)>=0;}):items;
  vis.forEach(function(it){if(_XY2_YS.indexOf(it.key)<0)_XY2_YS.push(it.key);});
  buildXY2Tab();
}
function _xy2BuildYChecklist(){
  var el=document.getElementById('xy2-y-list');if(!el)return;
  _xy2YsEnsure();
  var items=_xy2ItemsForGrp(_XY2_YGRP);
  var q=_XY2_YSRCH.toLowerCase();
  var vis=q?items.filter(function(it){return(it.key+' '+it.nm).toLowerCase().indexOf(q)>=0;}):items;
  var html='';
  vis.forEach(function(it){
    var chk=_XY2_YS.indexOf(it.key)>=0;
    var nm=it.nm?'<span style="color:#888;font-size:10px"> ('+esc(it.nm)+')</span>':'';
    html+='<label style="display:flex;align-items:center;gap:5px;padding:2px 6px;cursor:pointer;border-radius:3px;white-space:nowrap"'
      +' onmouseover="this.style.background=\'#e8f0fe\'" onmouseout="this.style.background=\'\'">'
      +'<input type="checkbox"'+(chk?' checked':'')
      +' onchange="_xy2ToggleY(\''+esc(it.key)+'\')" style="cursor:pointer">'
      +'<b style="font-size:11px">'+esc(it.key)+'</b>'+nm+'</label>';
  });
  el.innerHTML=html||'<div style="padding:6px;color:#aaa;font-size:11px">No params</div>';
  var btn=document.getElementById('xy2-y-btn');
  if(btn){
    var cnt=_XY2_YS.length;
    btn.textContent=cnt===0?'(none)':cnt>1?cnt+' Y params':_XY2_YS[0];
    btn.style.color=cnt===0?'#c0392b':cnt>1?'#1a6bb5':'';
    btn.style.fontWeight=cnt>1?'bold':'';
  }
}
function _xy2YDropToggle(){
  var pop=document.getElementById('xy2-y-drop');if(!pop)return;
  var vis=pop.style.display==='block';
  if(vis){pop.style.display='none';return;}
  pop.style.display='block';
  var srch=document.getElementById('xy2-y-srch');
  if(srch){srch.value=_XY2_YSRCH;srch.focus();}
  _xy2BuildYChecklist();
}
document.addEventListener('click',function(e){
  var drop=document.getElementById('xy2-y-drop');
  var btn=document.getElementById('xy2-y-btn');
  if(!drop||drop.style.display!=='block')return;
  if(drop.contains(e.target)||e.target===btn)return;
  drop.style.display='none';
},true);
function _xy2SetGrp(ax,grp){
  if(ax==='x')_XY2_XGRP=grp||null;
  else _XY2_YGRP=grp||null;
  buildXY2Tab();
}

/* ── XY Plot 2 CSV download ─────────────────────────────────────────────── */
function downloadXY2CSV(){
  var ak=activeKeys();
  var xParam=_XY2_X;
  _xy2YsEnsure();
  var validYs=_XY2_YS.filter(function(y){return y in PCM_PARAM_META;});
  if(!xParam||!validYs.length)return;
  var multiY=_XY2_YS.length>1;
  var cols=['Lot','Wafer','Material','GroupBy','X_Param','X','Y_Param','Y'];
  var lines=[cols.join(',')];
  var xRows={};
  PCM_ROWS.forEach(function(r){
    if(!ak.has(_rKey(r)))return;
    if(r.param===xParam)xRows[_rKey(r)]=r;
  });
  validYs.forEach(function(yParam){
    var yRows={};
    PCM_ROWS.forEach(function(r){
      if(!ak.has(_rKey(r)))return;
      if(r.param===yParam)yRows[_rKey(r)]=r;
    });
    Object.keys(xRows).forEach(function(k){
      var xr=xRows[k],yr=yRows[k];if(!yr)return;
      var gk=multiY?yParam:_grpKey(xr);
      if(_XY2_DIE){
        var xraw=xr.die_values||[],yraw=yr.die_values||[];
        var nd=Math.min(xraw.length,yraw.length);
        for(var di=0;di<nd;di++){
          var xrv=xraw[di],yrv=yraw[di];
          if(xrv==null||!isFinite(xrv)||yrv==null||!isFinite(yrv))continue;
          var xd=_toDisplayVals(xParam,[xrv]),yd=_toDisplayVals(yParam,[yrv]);
          if(!xd.length||!yd.length)continue;
          lines.push([xr.lot,xr.wafer,xr.material||'',gk,xParam,_fmt(xd[0]),yParam,_fmt(yd[0])].map(_csvQ).join(','));
        }
      }else{
        var xdv=_toDisplayVals(xParam,(xr.die_values||[]).filter(function(v){return v!=null&&isFinite(v);}));
        var ydv=_toDisplayVals(yParam,(yr.die_values||[]).filter(function(v){return v!=null&&isFinite(v);}));
        var xv=_med(xdv),yv=_med(ydv);
        if(xv!=null&&isFinite(xv)&&yv!=null&&isFinite(yv))
          lines.push([xr.lot,xr.wafer,xr.material||'',gk,xParam,_fmt(xv),yParam,_fmt(yv)].map(_csvQ).join(','));
      }
    });
  });
  _csvBlob(lines,'pcm_xy2_'+_csvTs()+'.csv');
}

/* ── Vmin-style always-on drag cursors for XY tab ─────────────────────── */
/* Two draggable cursors (A=orange, B=teal) with delta panel.
   Click anywhere → picks nearest cursor by distance and drags it.
   Positions persist across redraws via _DRAG_CUR_A / _DRAG_CUR_B[pfx].     */
function _initDragCursorsXY(svgEl,pfx,ML,MT,plotW,plotH,xLo,xHi,yLo,yHi,fmtX,fmtY){
  var NS='http://www.w3.org/2000/svg';
  var xRange=xHi-xLo||1,yRange=yHi-yLo||1;
  /* Restore or initialise cursor positions */
  var curX=(_DRAG_CUR_A.x!=null&&_DRAG_CUR_A.x>=xLo&&_DRAG_CUR_A.x<=xHi)?_DRAG_CUR_A.x:xLo+xRange*0.30;
  var curY=(_DRAG_CUR_A.y!=null&&_DRAG_CUR_A.y>=yLo&&_DRAG_CUR_A.y<=yHi)?_DRAG_CUR_A.y:yLo+yRange*0.50;
  if(!_DRAG_CUR_B[pfx])_DRAG_CUR_B[pfx]={x:null,y:null};
  var _b2=_DRAG_CUR_B[pfx];
  var curX2=(_b2.x!=null&&_b2.x>=xLo&&_b2.x<=xHi)?_b2.x:Math.min(xHi,curX+xRange*0.20);
  var curY2=(_b2.y!=null&&_b2.y>=yLo&&_b2.y<=yHi)?_b2.y:curY;
  function v2px(v){return ML+(v-xLo)/xRange*plotW;}
  function v2py(v){return MT+plotH-(v-yLo)/yRange*plotH;}
  function px2v(px){return xLo+(px-ML)/plotW*xRange;}
  function py2v(py){return yLo+(MT+plotH-py)/plotH*yRange;}
  function clamp(v,lo,hi){return v<lo?lo:v>hi?hi:v;}
  function getSvgPt(e){
    var pt=svgEl.createSVGPoint();pt.x=e.clientX;pt.y=e.clientY;
    var ctm=svgEl.getScreenCTM();if(!ctm)return null;
    var sp=pt.matrixTransform(ctm.inverse());return{x:sp.x,y:sp.y};
  }
  function _mk(tag,attrs){var el=document.createElementNS(NS,tag);Object.keys(attrs).forEach(function(k){el.setAttribute(k,attrs[k]);});el.style.pointerEvents='none';return el;}
  function _mkTxt(sz,col){return _mk('text',{'font-size':sz,'fill':col,'font-weight':'bold','stroke':'white','stroke-width':'1.5','paint-order':'stroke'});}
  /* Cursor A (bright red-orange) */
  var vLA=_mk('line',{x1:v2px(curX),x2:v2px(curX),y1:MT,y2:MT+plotH,stroke:'#ff3300','stroke-width':'2.5','stroke-dasharray':'5,2',opacity:'1'});
  var hLA=_mk('line',{x1:ML,x2:ML+plotW,y1:v2py(curY),y2:v2py(curY),stroke:'#ff3300','stroke-width':'2.5','stroke-dasharray':'5,2',opacity:'1'});
  var txAx=_mkTxt('14','#ff3300');
  var txAy=_mkTxt('14','#ff3300');
  /* Cursor B (bright blue) */
  var vLB=_mk('line',{x1:v2px(curX2),x2:v2px(curX2),y1:MT,y2:MT+plotH,stroke:'#0099ff','stroke-width':'2.5','stroke-dasharray':'5,2',opacity:'1'});
  var hLB=_mk('line',{x1:ML,x2:ML+plotW,y1:v2py(curY2),y2:v2py(curY2),stroke:'#0099ff','stroke-width':'2.5','stroke-dasharray':'5,2',opacity:'1'});
  var txBtag=_mkTxt('11','#0077cc');
  var txBx=_mkTxt('13','#0077cc');
  var txBy=_mkTxt('13','#0077cc');
  /* Delta panel background + labels */
  var dBg=_mk('rect',{rx:'4',ry:'4',fill:'rgba(255,255,255,0.92)',stroke:'#0099ff','stroke-width':'1.5'});dBg.style.pointerEvents='none';
  var dTxtX=_mkTxt('12','#cc2200');
  var dTxtY=_mkTxt('12','#0077cc');
  function _fX(v){return fmtX?fmtX(v):_fmt(v);}
  function _fY(v){return fmtY?fmtY(v):_fmt(v);}
  function _updA(){
    var px=v2px(curX),py=v2py(curY);
    vLA.setAttribute('x1',px.toFixed(1));vLA.setAttribute('x2',px.toFixed(1));
    hLA.setAttribute('y1',py.toFixed(1));hLA.setAttribute('y2',py.toFixed(1));
    var anc=px+plotW*0.55>ML+plotW?'end':'start';
    txAx.setAttribute('x',(anc==='end'?px-4:px+4).toFixed(1));txAx.setAttribute('y',(MT+14).toFixed(1));
    txAx.setAttribute('text-anchor',anc);txAx.textContent=_fX(curX);
    var ly=py-3<MT+14?py+15:py-4;
    txAy.setAttribute('x',(ML+5).toFixed(1));txAy.setAttribute('y',ly.toFixed(1));
    txAy.setAttribute('text-anchor','start');txAy.textContent=_fY(curY);
  }
  function _updB(){
    var px=v2px(curX2),py=v2py(curY2);
    vLB.setAttribute('x1',px.toFixed(1));vLB.setAttribute('x2',px.toFixed(1));
    hLB.setAttribute('y1',py.toFixed(1));hLB.setAttribute('y2',py.toFixed(1));
    var la=px+4>ML+plotW-50?'end':'start';
    txBtag.setAttribute('x',(la==='end'?px-4:px+4).toFixed(1));txBtag.setAttribute('y',(MT+12).toFixed(1));
    txBtag.setAttribute('text-anchor',la);txBtag.textContent='Cursor';
    var xba=px+4>ML+plotW-60?'end':'start';
    txBx.setAttribute('x',(xba==='end'?px-4:px+4).toFixed(1));txBx.setAttribute('y',(MT+28).toFixed(1));
    txBx.setAttribute('text-anchor',xba);txBx.textContent='X:'+_fX(curX2);
    var yly=py-3<MT+28?py+27:py-4;
    txBy.setAttribute('x',(ML+5).toFixed(1));txBy.setAttribute('y',yly.toFixed(1));
    txBy.setAttribute('text-anchor','start');txBy.textContent='Y:'+_fY(curY2);
  }
  function _updDelta(){
    var dx=Math.abs(curX2-curX),dy=Math.abs(curY2-curY);
    dTxtX.textContent='\u0394X: '+_fX(dx);dTxtY.textContent='\u0394Y: '+_fY(dy);
    var bW=118,bH=38,bX=ML+plotW-bW-4,bY=MT+4;
    dBg.setAttribute('x',bX);dBg.setAttribute('y',bY);dBg.setAttribute('width',bW);dBg.setAttribute('height',bH);
    dTxtX.setAttribute('x',(bX+bW/2).toFixed(1));dTxtX.setAttribute('y',(bY+14).toFixed(1));dTxtX.setAttribute('text-anchor','middle');
    dTxtY.setAttribute('x',(bX+bW/2).toFixed(1));dTxtY.setAttribute('y',(bY+30).toFixed(1));dTxtY.setAttribute('text-anchor','middle');
  }
  _updA();_updB();_updDelta();
  _DRAG_CUR_A.x=curX;_DRAG_CUR_A.y=curY;_b2.x=curX2;_b2.y=curY2;
  [vLA,hLA,txAx,txAy,vLB,hLB,txBtag,txBx,txBy,dBg,dTxtX,dTxtY].forEach(function(el){svgEl.appendChild(el);});
  /* Transparent drag handle — picks nearest cursor on mousedown, then drags */
  var uH=_mk('rect',{x:ML,y:MT,width:plotW,height:plotH,fill:'transparent'});
  uH.style.pointerEvents='all';uH.style.cursor='crosshair';
  svgEl.appendChild(uH);
  var _uDrag=false,_uTgt='A';
  function _uMove(sp){
    var px=clamp(sp.x,ML,ML+plotW),py=clamp(sp.y,MT,MT+plotH);
    if(_uTgt==='A'){curX=px2v(px);curY=py2v(py);_DRAG_CUR_A.x=curX;_DRAG_CUR_A.y=curY;_updA();}
    else{curX2=px2v(px);curY2=py2v(py);_b2.x=curX2;_b2.y=curY2;_updB();}
    _updDelta();
  }
  function _onUM(e){if(_uDrag){var sp=getSvgPt(e);if(sp)_uMove(sp);}}
  function _onUU(){_uDrag=false;document.removeEventListener('mousemove',_onUM);document.removeEventListener('mouseup',_onUU);}
  uH.addEventListener('mousedown',function(e){
    e.preventDefault();e.stopPropagation();
    var sp=getSvgPt(e);if(!sp)return;
    var px=clamp(sp.x,ML,ML+plotW),py=clamp(sp.y,MT,MT+plotH);
    var dA=Math.sqrt(Math.pow(px-v2px(curX),2)+Math.pow(py-v2py(curY),2));
    var dB=Math.sqrt(Math.pow(px-v2px(curX2),2)+Math.pow(py-v2py(curY2),2));
    _uTgt=(dA<=dB)?'A':'B';_uDrag=true;_uMove(sp);
    document.addEventListener('mousemove',_onUM);document.addEventListener('mouseup',_onUU);
  });
  function _onUTM(e){if(!_uDrag||!e.touches.length)return;e.preventDefault();var t=e.touches[0];var sp=getSvgPt({clientX:t.clientX,clientY:t.clientY});if(sp)_uMove(sp);}
  function _onUTE(){_uDrag=false;svgEl.removeEventListener('touchmove',_onUTM);svgEl.removeEventListener('touchend',_onUTE);}
  uH.addEventListener('touchstart',function(e){
    e.preventDefault();
    if(e.touches.length){var t=e.touches[0];var sp=getSvgPt({clientX:t.clientX,clientY:t.clientY});if(sp){
      var px=clamp(sp.x,ML,ML+plotW),py=clamp(sp.y,MT,MT+plotH);
      var dA=Math.sqrt(Math.pow(px-v2px(curX),2)+Math.pow(py-v2py(curY),2));
      var dB=Math.sqrt(Math.pow(px-v2px(curX2),2)+Math.pow(py-v2py(curY2),2));
      _uTgt=(dA<=dB)?'A':'B';_uDrag=true;_uMove(sp);}}
    svgEl.addEventListener('touchmove',_onUTM,{passive:false});svgEl.addEventListener('touchend',_onUTE);
  },{passive:false});
}

/* ── Dual-cursor (C1=red lock + C2=cyan measure) shared attach ──────────── */
/* msCbId: id of the "Measure" checkbox element; null => C2 always enabled */
function _attachDualCrosshair(svgEl,pfx,ML,MT,plotW,plotH,xlo,xhi,ylo,yhi,logX,logY,xUnit,_fmtTkFn,msCbId){
  var _c1Locked=false,_c2On=false;
  var _c1sx=null,_c1sy=null,_c1dx=null,_c1dy=null;
  var _c2sx=null,_c2sy=null,_c2dx=null,_c2dy=null;
  function _isMeasure(){var cb=msCbId?document.getElementById(msCbId):null;return cb?cb.checked:false;}
  function _svgPt(e){
    var pt=svgEl.createSVGPoint();pt.x=e.clientX;pt.y=e.clientY;
    var ctm=svgEl.getScreenCTM();if(!ctm)return null;
    var sp=pt.matrixTransform(ctm.inverse());return{sx:sp.x,sy:sp.y};
  }
  function _inPlot(sx,sy){return sx>=ML&&sx<=ML+plotW&&sy>=MT&&sy<=MT+plotH;}
  function _toDV(sx,sy){
    var dx=xlo+(sx-ML)/plotW*(xhi-xlo);
    var dy=ylo+(1-(sy-MT)/plotH)*(yhi-ylo);
    return{dx:dx,dy:dy};
  }
  function _showEl(id,val){
    var el=document.getElementById(id);
    if(!el)return;
    if(val===null){el.setAttribute('display','none');return;}
    el.removeAttribute('display');
    if(typeof val==='object'){
      if(val.x1!==undefined)el.setAttribute('x1',val.x1);
      if(val.x2!==undefined)el.setAttribute('x2',val.x2);
      if(val.y1!==undefined)el.setAttribute('y1',val.y1);
      if(val.y2!==undefined)el.setAttribute('y2',val.y2);
      if(val.x!==undefined)el.setAttribute('x',val.x);
      if(val.y!==undefined)el.setAttribute('y',val.y);
      if(val.text!==undefined)el.textContent=val.text;
      if(val.fill!==undefined)el.setAttribute('fill',val.fill);
      if(val.stroke!==undefined)el.setAttribute('stroke',val.stroke);
    }
  }
  function _bgBox(labelId,bgId){
    var lbl=document.getElementById(labelId);if(!lbl||!lbl.getBBox)return;
    var bb=lbl.getBBox();if(!bb||!bb.width)return;
    _showEl(bgId,{});var bg=document.getElementById(bgId);
    if(bg){bg.setAttribute('x',bb.x-2);bg.setAttribute('y',bb.y-1);bg.setAttribute('width',bb.width+4);bg.setAttribute('height',bb.height+2);}
  }
  function _hideAll(prefix){
    [prefix+'-ch-v',prefix+'-ch-h',prefix+'-ch-xl',prefix+'-ch-yl',prefix+'-ch-xlbg',prefix+'-ch-ylbg'].forEach(function(id){
      var el=document.getElementById(id);if(el)el.setAttribute('display','none');
    });
  }
  function _hideC2All(){
    [pfx+'-c2v',pfx+'-c2h',pfx+'-c2xl',pfx+'-c2xlbg',pfx+'-c2yl',pfx+'-c2ylbg',pfx+'-c2dx',pfx+'-c2dxbg',pfx+'-c2dy',pfx+'-c2dybg'].forEach(function(id){
      var el=document.getElementById(id);if(el)el.setAttribute('display','none');
    });
  }
  function _drawC1(sx,sy,locked){
    var d=_toDV(sx,sy);
    var col=locked?'#e67e22':'#e74c3c';
    var xLbl=_fmtTkFn(d.dx,logX)+((!logX&&xUnit==='% of tgt')?'%':'');
    _showEl(pfx+'-ch-v',{x1:sx.toFixed(1),x2:sx.toFixed(1),stroke:col});
    _showEl(pfx+'-ch-h',{y1:sy.toFixed(1),y2:sy.toFixed(1),stroke:col});
    _showEl(pfx+'-ch-xl',{x:sx.toFixed(1),text:xLbl,fill:col});
    _bgBox(pfx+'-ch-xl',pfx+'-ch-xlbg');
    _showEl(pfx+'-ch-yl',{y:sy.toFixed(1),text:_fmtTkFn(d.dy,logY),fill:col});
    _bgBox(pfx+'-ch-yl',pfx+'-ch-ylbg');
    _c1sx=sx;_c1sy=sy;_c1dx=d.dx;_c1dy=d.dy;
  }
  function _drawC2(sx,sy){
    var col='#0097a7';
    var d=_toDV(sx,sy);
    var xLbl=_fmtTkFn(d.dx,logX)+((!logX&&xUnit==='% of tgt')?'%':'');
    _showEl(pfx+'-c2v',{x1:sx.toFixed(1),x2:sx.toFixed(1),stroke:col});
    _showEl(pfx+'-c2h',{y1:sy.toFixed(1),y2:sy.toFixed(1),stroke:col});
    _showEl(pfx+'-c2xl',{x:sx.toFixed(1),text:xLbl,fill:col});
    _bgBox(pfx+'-c2xl',pfx+'-c2xlbg');
    _showEl(pfx+'-c2yl',{y:sy.toFixed(1),text:_fmtTkFn(d.dy,logY),fill:col});
    _bgBox(pfx+'-c2yl',pfx+'-c2ylbg');
    _c2sx=sx;_c2sy=sy;_c2dx=d.dx;_c2dy=d.dy;
    /* Delta labels midway between cursors */
    if(_c1sx!=null&&_c1sy!=null){
      var mx=(_c1sx+sx)/2,my=(_c1sy+sy)/2;
      mx=Math.max(ML+10,Math.min(ML+plotW-10,mx));
      my=Math.max(MT+16,Math.min(MT+plotH-8,my));
      var dxVal=d.dx-_c1dx,dyVal=d.dy-_c1dy;
      var dxStr='ΔX='+_fmtTkFn(dxVal,logX)+((!logX&&xUnit==='% of tgt')?'%':'');
      var dyStr='ΔY='+_fmtTkFn(dyVal,logY);
      _showEl(pfx+'-c2dx',{x:mx.toFixed(1),y:(my-8).toFixed(1),text:dxStr,fill:'#004d57'});
      _bgBox(pfx+'-c2dx',pfx+'-c2dxbg');
      _showEl(pfx+'-c2dy',{x:mx.toFixed(1),y:(my+8).toFixed(1),text:dyStr,fill:'#004d57'});
      _bgBox(pfx+'-c2dy',pfx+'-c2dybg');
    }
  }
  svgEl.addEventListener('mousemove',function(e){
    var s=_svgPt(e);if(!s)return;
    if(!_inPlot(s.sx,s.sy))return;
    if(!_c1Locked){_drawC1(s.sx,s.sy,false);_hideC2All();return;}
    if(_isMeasure()&&!_c2On){_drawC2(s.sx,s.sy);}
  });
  svgEl.addEventListener('click',function(e){
    var s=_svgPt(e);if(!s||!_inPlot(s.sx,s.sy))return;
    if(!_c1Locked){
      _c1Locked=true;_drawC1(s.sx,s.sy,true);
      _c2On=false;_hideC2All();
    }else if(_isMeasure()&&!_c2On){
      _c2On=true;_drawC2(s.sx,s.sy);
    }else{
      _c1Locked=false;_c2On=false;
      _hideAll(pfx);_hideC2All();
      _c1sx=_c1sy=_c2sx=_c2sy=null;
      _getTT().style.display='none';
    }
  });
  svgEl.addEventListener('mouseleave',function(){
    if(!_c1Locked){_hideAll(pfx);_getTT().style.display='none';}
    if(!_c2On){_hideC2All();}
  });
}
/* ── Build XY Plot 2 ─────────────────────────────────────────────────────── */
function buildXY2Tab(){
  if(_XY2_Y===null){var dflt=_xy2DefaultY();if(dflt.length){_XY2_Y=dflt[0];}}
  _xy2YsEnsure();
  var cont=document.getElementById('xy2-cont');if(!cont)return;
  var ak=activeKeys();
  var xParam=_XY2_X;
  _xy2BuildSelects();
  _xy2BuildYChecklist();

  var multiY=_XY2_YS.length>1;
  var validYs=_XY2_YS.filter(function(y){return y in PCM_PARAM_META;});
  if(!validYs.length||!xParam||!(xParam in PCM_PARAM_META)){
    cont.innerHTML='<div style="padding:24px;color:#888">Select valid X and Y parameters.</div>';return;}

  var gbyWrap=document.getElementById('xy2-gby-wrap');
  if(gbyWrap){
    gbyWrap.style.opacity=multiY?'0.35':'';
    gbyWrap.style.pointerEvents=multiY?'none':'';
    gbyWrap.title=multiY?'Group-by disabled \u2014 Y params are the groups':'';
  }

  var cm;
  if(multiY){
    var cKeys2=validYs.slice();
    var cMap2={};cKeys2.forEach(function(k,i){cMap2[k]=_cPal(i);});
    cm={keys:cKeys2,map:cMap2};
  }else{cm=_cMap();}

  var xmeta=PCM_PARAM_META[xParam]||{};
  var xUnit=xParam.match(/^Td_/i)?'% of tgt':(_isLeakage(xParam)?(_leakageScale([(xmeta.target||xmeta.usl||1e-6)]).unit):(xmeta.unit||''));
  var yParam1=validYs[0];
  var ymeta1=PCM_PARAM_META[yParam1]||{};
  var yUnit1=yParam1.match(/^Td_/i)?'% of tgt':(_isLeakage(yParam1)?(_leakageScale([(ymeta1.target||ymeta1.usl||1e-6)]).unit):(ymeta1.unit||''));

  var xRows={};
  PCM_ROWS.forEach(function(r){if(!ak.has(_rKey(r)))return;if(r.param===xParam)xRows[_rKey(r)]=r;});

  var pts=[];
  validYs.forEach(function(yParam){
    var yRows={};
    PCM_ROWS.forEach(function(r){if(!ak.has(_rKey(r)))return;if(r.param===yParam)yRows[_rKey(r)]=r;});
    Object.keys(xRows).forEach(function(k){
      var xr=xRows[k],yr=yRows[k];if(!yr)return;
      var gk=multiY?yParam:_grpKey(xr);
      var yUnitPt=(PCM_PARAM_META[yParam]||{}).unit||'';
      var _xy2SiccX=_isSiccCdyn(xParam),_xy2SiccY=_isSiccCdyn(yParam);
      if(_XY2_DIE){
        var xraw=xr.die_values||[],yraw=yr.die_values||[];
        var ndRaw=Math.min(xraw.length,yraw.length);
        for(var di=0;di<ndRaw;di++){
          var xrv=xraw[di],yrv=yraw[di];
          if(xrv==null||!isFinite(xrv)||yrv==null||!isFinite(yrv))continue;
          if((_xy2SiccX&&xrv<=0)||(_xy2SiccY&&yrv<=0))continue;
          var xd=_toDisplayVals(xParam,[xrv]),yd=_toDisplayVals(yParam,[yrv]);
          if(!xd.length||!yd.length||!isFinite(xd[0])||!isFinite(yd[0]))continue;
          pts.push({x:xd[0],y:yd[0],lot:xr.lot,wafer:xr.wafer,gk:gk,yParam:yParam,yUnit:yUnitPt});
        }
      }else{
        var xdv2=_toDisplayVals(xParam,(xr.die_values||[]).filter(function(v){return v!=null&&isFinite(v)&&(!_xy2SiccX||v>0);}));
        var ydv2=_toDisplayVals(yParam,(yr.die_values||[]).filter(function(v){return v!=null&&isFinite(v)&&(!_xy2SiccY||v>0);}));
        var xv=_med(xdv2),yv=_med(ydv2);
        if(xv!=null&&isFinite(xv)&&yv!=null&&isFinite(yv))pts.push({x:xv,y:yv,lot:xr.lot,wafer:xr.wafer,gk:gk,yParam:yParam,yUnit:yUnitPt});
      }
    });
  });
  if(!pts.length){cont.innerHTML='<div style="padding:24px;color:#888;font-style:italic">No matching data for selected X / Y.</div>';return;}

  function _lx2(v){return _XY2_LOG_X?Math.log10(Math.max(v,1e-300)):v;}
  function _ly2(v){return _XY2_LOG_Y?Math.log10(Math.max(v,1e-300)):v;}
  function _fmtTk2(v,isLog){
    if(!isLog)return _fmt(v);
    var pw=Math.round(v);return(Math.abs(v-pw)<0.05)?'10^'+pw:_fmt(Math.pow(10,v));
  }

  var lxs=pts.map(function(p){return _lx2(p.x);}),lys=pts.map(function(p){return _ly2(p.y);});
  var xmn=_XY2_XMIN!=null?_lx2(_XY2_XMIN):_safeMin(lxs);
  var xmx=_XY2_XMAX!=null?_lx2(_XY2_XMAX):_safeMax(lxs);
  var ymn=_XY2_YMIN!=null?_ly2(_XY2_YMIN):_safeMin(lys);
  var ymx=_XY2_YMAX!=null?_ly2(_XY2_YMAX):_safeMax(lys);
  var xrng=xmx-xmn||1,yrng=ymx-ymn||1;
  var xpad=xrng*0.08,ypad=yrng*0.08;
  var xlo=xmn-xpad,xhi=xmx+xpad,ylo=ymn-ypad,yhi=ymx+ypad;

  var svgH=_XY2_H,ML=80,MR=20,MT=36,MB=75;
  var plotW=700-ML-MR,plotH=svgH-MT-MB;
  var svgW=700;
  function xp(v){return ML+(v-xlo)/(xhi-xlo)*plotW;}
  function yp(v){return MT+(1-(v-ylo)/(yhi-ylo))*plotH;}

  var p=['<svg id="xy2-svg" width="100%" height="'+svgH+'" viewBox="0 0 '+svgW+' '+svgH+'" style="display:block">'];
  p.push('<rect width="'+svgW+'" height="'+svgH+'" fill="#f8f9fa"/>');
  p.push('<rect x="'+ML+'" y="'+MT+'" width="'+plotW+'" height="'+plotH+'" fill="#fff" stroke="#ccc" stroke-width="1"/>');
  for(var xi=0;xi<=6;xi++){
    var xv=xlo+(xhi-xlo)*xi/6,xpv=(ML+xi/6*plotW).toFixed(1);
    p.push('<line x1="'+xpv+'" y1="'+MT+'" x2="'+xpv+'" y2="'+(MT+plotH)+'" stroke="rgba(0,0,0,0.08)" stroke-width="0.8"/>');
    var xlbl=_fmtTk2(xv,_XY2_LOG_X)+((!_XY2_LOG_X&&xUnit==='% of tgt')?'%':'');
    p.push('<text x="'+xpv+'" y="'+(MT+plotH+20)+'" text-anchor="middle" font-size="12" fill="#333">'+xlbl+'</text>');
  }
  for(var yi=0;yi<=5;yi++){
    var yv=ylo+(yhi-ylo)*yi/5,ypv=(MT+plotH*(1-yi/5)).toFixed(1);
    p.push('<line x1="'+ML+'" y1="'+ypv+'" x2="'+(ML+plotW)+'" y2="'+ypv+'" stroke="rgba(0,0,0,0.08)" stroke-width="0.8"/>');
    p.push('<text x="'+(ML-5)+'" y="'+ypv+'" text-anchor="end" dominant-baseline="middle" font-size="12" fill="#333">'+_fmtTk2(yv,_XY2_LOG_Y)+'</text>');
  }

  /* Trend lines */
  if(_XY2_TREND!=='none'){
    var grpTd={};
    pts.forEach(function(pt){
      var lxv=_lx2(pt.x),lyv=_ly2(pt.y);
      if(!isFinite(lxv)||!isFinite(lyv))return;
      if(!grpTd[pt.gk])grpTd[pt.gk]={xs:[],ys:[]};
      grpTd[pt.gk].xs.push(lxv);grpTd[pt.gk].ys.push(lyv);
    });
    Object.keys(grpTd).forEach(function(gk){
      var reg=(_XY2_TREND==='theilsen')?_theilSen(grpTd[gk].xs,grpTd[gk].ys):_ols(grpTd[gk].xs,grpTd[gk].ys);
      if(!reg)return;
      var col=cm.map[gk]||_cPal(0);
      var ty1=reg.slope*xlo+reg.intercept,ty2=reg.slope*xhi+reg.intercept;
      p.push('<line x1="'+xp(xlo).toFixed(1)+'" y1="'+Math.max(MT,Math.min(MT+plotH,yp(ty1))).toFixed(1)+'"'
        +' x2="'+xp(xhi).toFixed(1)+'" y2="'+Math.max(MT,Math.min(MT+plotH,yp(ty2))).toFixed(1)+'"'
        +' stroke="'+col+'" stroke-width="2.5" stroke-dasharray="7,3" opacity="1.0"/>');
    });
  }

  /* Dots */
  var _dp2={};
  pts.forEach(function(pt){
    var col=cm.map[pt.gk]||_cPal(0);
    var lxv=_lx2(pt.x),lyv=_ly2(pt.y);
    if(!isFinite(lxv)||!isFinite(lyv))return;
    var cx=xp(lxv),cy=yp(lyv);
    if(cx<ML-5||cx>ML+plotW+5||cy<MT-5||cy>MT+plotH+5)return;
    if(!_dp2[col])_dp2[col]='';
    _dp2[col]+='M'+cx.toFixed(1)+','+cy.toFixed(1)+'m-0.875,0a0.875,0.875,0,1,0,1.75,0a0.875,0.875,0,1,0,-1.75,0';
  });
  Object.keys(_dp2).forEach(function(col){p.push('<path d="'+_dp2[col]+'" fill="'+col+'" opacity="0.95"/>');});

  /* Median diamonds */
  var grpXY={};
  pts.forEach(function(pt){
    if(!grpXY[pt.gk])grpXY[pt.gk]={xs:[],ys:[]};
    var lxv=_lx2(pt.x),lyv=_ly2(pt.y);
    if(isFinite(lxv)&&isFinite(lyv)){grpXY[pt.gk].xs.push(lxv);grpXY[pt.gk].ys.push(lyv);}
  });
  Object.keys(grpXY).forEach(function(gk){
    var gd=grpXY[gk],mx=_med(gd.xs),my=_med(gd.ys);
    if(mx==null||my==null)return;
    var col=cm.map[gk]||_cPal(0);
    var cx=xp(mx),cy=yp(my),ds=8;
    if(cx<ML||cx>ML+plotW||cy<MT||cy>MT+plotH)return;
    p.push('<polygon points="'+cx+','+(cy-ds)+' '+(cx+ds)+','+cy+' '+cx+','+(cy+ds)+' '+(cx-ds)+','+cy+'"'
      +' fill="'+col+'" stroke="#fff" stroke-width="1.6" opacity="0.95"/>');
  });

  /* Axis labels */
  var xLbl=esc(xParam+(xmeta.name?' ('+xmeta.name+')':''))+(xUnit?' \u2014 '+esc(xUnit):'');
  var yLbl=multiY?('Multiple Y params ('+validYs.length+')'):esc(yParam1+(ymeta1.name?' ('+ymeta1.name+')':''))+(yUnit1?' \u2014 '+esc(yUnit1):'');
  if(_XY2_LOG_X)xLbl+=' [log\u2081\u2080]';
  if(_XY2_LOG_Y)yLbl+=' [log\u2081\u2080]';
  p.push('<text x="'+(ML+plotW/2)+'" y="'+(svgH-4)+'" text-anchor="middle" font-size="14" font-weight="bold" fill="#222">'+xLbl+'</text>');
  p.push('<text transform="translate(14,'+(MT+plotH/2)+') rotate(-90)" text-anchor="middle" font-size="14" font-weight="bold" fill="#222">'+yLbl+'</text>');
  var rawXs=pts.map(function(p){return p.x;}),rawYs=pts.map(function(p){return p.y;});
  var statsStr='N='+pts.length+(multiY?'':' \u00a0 r='+_corrXY(rawXs,rawYs));
  p.push('<text x="'+(ML+plotW-4)+'" y="'+(MT+14)+'" text-anchor="end" font-size="12" fill="#555">'+statsStr+'</text>');

  p.push('</svg>');

  /* Legend */
  var lgKs=cm.keys.filter(function(k){return !(cm.keys.length===1&&k==='All');});
  var legParts=[];
  if(!multiY&&VAR_GBY.length>0){
    var _gbyLabels2={'lot':'Lot','wafer':'Wafer','layout':'Layout','material':'Material'};
    var _gbyStr2=VAR_GBY.map(function(f){return _gbyLabels2[f]||f;}).join(' + ');
    legParts.push('<span style="display:inline-flex;align-items:center;gap:3px;background:#e8f4fd;border:1px solid #aed6f1;border-radius:10px;padding:1px 8px;font-size:11px;color:#1a6bb5;font-weight:700">&#9650; '+esc(_gbyStr2)+'</span>');
  }
  lgKs.forEach(function(k){
    var col=cm.map[k];
    legParts.push('<span style="display:flex;align-items:center;gap:4px">'
      +'<svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4.5" fill="'+col+'" opacity="0.85"/></svg>'
      +'<span style="color:#2c3e50">'+esc(k)+'</span></span>');
  });
  if(_XY2_TREND!=='none'){
    legParts.push('<span style="display:flex;align-items:center;gap:4px">'
      +'<svg width="22" height="10" viewBox="0 0 22 10"><line x1="0" y1="5" x2="22" y2="5" stroke="#555" stroke-width="2" stroke-dasharray="7,3"/></svg>'
      +'<span style="color:#555">'+(_XY2_TREND==='theilsen'?'Theil-Sen':'OLS')+'</span></span>');
  }
  legParts.push('<span style="display:flex;align-items:center;gap:4px">'
    +'<svg width="14" height="14" viewBox="0 0 14 14"><polygon points="7,0 14,7 7,14 0,7" fill="#27ae60" stroke="#fff" stroke-width="1.2"/></svg>'
    +'<span style="color:#2c3e50">Group median</span></span>');
  var legHtml=(legParts.length
    ?'<div style="display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;padding:4px 6px;font-size:11px;border-top:1px solid #e8e8e8;background:#fafafa">'+legParts.join('')+'</div>'
    :'');
  var gbyBadge2=(!multiY&&VAR_GBY.length>0)
    ?('<div style="padding:3px 10px;background:#1f3a50;font-size:11px;color:#aed6f1;font-weight:600;text-align:left">'
      +'&#9650; Grouped by: <b style="color:#5dade2">'+esc(VAR_GBY.map(function(f){return({'lot':'Lot','wafer':'Wafer','layout':'Layout','material':'Material'})[f]||f;}).join(' + '))+'</b></div>')
    :'';
  cont.innerHTML='<div style="display:flex;flex-direction:column">'+p.join('')+legHtml+gbyBadge2+'</div>';

  /* Always-on drag cursors */
  var svgEl2=document.getElementById('xy2-svg');
  if(svgEl2){
    function _fmtTkXY2(v,isLog){if(!isLog)return _fmt(v);var pw=Math.round(v);return(Math.abs(v-pw)<0.05)?'10^'+pw:_fmt(Math.pow(10,v));}
    var fmtXY2x=function(v){return _fmtTkXY2(v,_XY2_LOG_X)+((!_XY2_LOG_X&&xUnit==='% of tgt')?'%':'');};
    var fmtXY2y=function(v){return _fmtTkXY2(v,_XY2_LOG_Y);};
    _initDragCursorsXY(svgEl2,'xy2',ML,MT,plotW,plotH,xlo,xhi,ylo,yhi,fmtXY2x,fmtXY2y);
  }
  /* Point hover tooltip */
  var svgEl=document.getElementById('xy2-svg');
  if(svgEl){
    svgEl.addEventListener('mousemove',function(e){
      var pt=svgEl.createSVGPoint();pt.x=e.clientX;pt.y=e.clientY;
      var ctm=svgEl.getScreenCTM();if(!ctm)return;
      var sp=pt.matrixTransform(ctm.inverse());
      var inPlot=(sp.x>=ML&&sp.x<=ML+plotW&&sp.y>=MT&&sp.y<=MT+plotH);
      if(!inPlot){_getTT().style.display='none';return;}
      var ctm2=svgEl.getScreenCTM();
      var best=null,bestD=9999;
      pts.forEach(function(dp){
        var dotSvgX=xp(_lx2(dp.x)),dotSvgY=yp(_ly2(dp.y));
        var scr=svgEl.createSVGPoint();scr.x=dotSvgX;scr.y=dotSvgY;
        var scrPx=ctm2?scr.matrixTransform(ctm2):{x:dotSvgX,y:dotSvgY};
        var d=Math.sqrt((e.clientX-scrPx.x)*(e.clientX-scrPx.x)+(e.clientY-scrPx.y)*(e.clientY-scrPx.y));
        if(d<bestD){bestD=d;best=dp;}
      });
      var tt=_getTT();
      if(best&&bestD<=22){
        var yValStr=_fmt(best.y)+(best.yUnit?' '+best.yUnit:'');
        var yLblLine=multiY?('<b>'+esc(best.yParam)+'</b>: '+yValStr):('Y: '+yValStr+(yUnit1?' '+yUnit1:''));
        tt.innerHTML='<b>'+esc(best.lot)+' / '+esc(best.wafer)+'</b><br>X: '+_fmt(best.x)+(xUnit?' '+xUnit:'')+'<br>'+yLblLine;
        tt.style.left=(e.clientX+14)+'px';tt.style.top=(e.clientY-48)+'px';tt.style.display='block';
      }else{tt.style.display='none';}
    });
    svgEl.addEventListener('mouseleave',function(){_getTT().style.display='none';});
  }
}

/* Pearson r */
function _corrXY(xs,ys){
  var n=xs.length;if(n<2)return '\u2014';
  var mx=0,my=0,i;
  for(i=0;i<n;i++){mx+=xs[i];my+=ys[i];}mx/=n;my/=n;
  var cov=0,sx=0,sy=0;
  for(i=0;i<n;i++){var dx=xs[i]-mx,dy=ys[i]-my;cov+=dx*dy;sx+=dx*dx;sy+=dy*dy;}
  var denom=Math.sqrt(sx*sy);if(!denom)return '\u2014';
  return (cov/denom).toFixed(3);
}

/* OLS slope, intercept forced through (median_x, median_y) — same anchor as diamond and Theil-Sen.
   Standard OLS intercept passes through the MEAN which diverges from the median diamond when
   per-die data is skewed or has outliers, making the line appear to miss the diamond. */
function _ols(xs,ys){
  var n=xs.length;if(n<2)return null;
  var mx=0,my=0,i;
  for(i=0;i<n;i++){mx+=xs[i];my+=ys[i];}mx/=n;my/=n;
  var num=0,den=0;
  for(i=0;i<n;i++){var dx=xs[i]-mx;num+=dx*(ys[i]-my);den+=dx*dx;}
  if(!den)return null;
  var sl=num/den;
  /* Force through median (same point as diamond) so line always passes through it */
  var medX=_med(xs),medY=_med(ys);
  return{slope:sl,intercept:medY-sl*medX};
}

/* Theil-Sen estimator — adapted from sicc_cdyn_upm reference implementation */
function _theilSen(xs,ys){
  var n=xs.length;if(n<3)return null;
  /* Fisher-Yates shuffle on index array, then cap at 300 for large n */
  var idx=[];for(var ii=0;ii<n;ii++)idx.push(ii);
  if(n>300){
    for(var si=n-1;si>0;si--){var ri=Math.floor(Math.random()*(si+1));var tmp=idx[si];idx[si]=idx[ri];idx[ri]=tmp;}
    idx=idx.slice(0,300);
  }
  var sn=idx.length,slopes=[],i,j;
  for(i=0;i<sn-1;i++)for(j=i+1;j<sn;j++){
    var dx=xs[idx[j]]-xs[idx[i]];
    if(Math.abs(dx)>1e-12)slopes.push((ys[idx[j]]-ys[idx[i]])/dx);
  }
  if(!slopes.length)return null;
  slopes.sort(function(a,b){return a-b;});
  var m2=slopes.length,slope=m2%2?slopes[(m2-1)/2]:(slopes[m2/2-1]+slopes[m2/2])/2;
  var medX=_med(xs),medY=_med(ys);
  return{slope:slope,intercept:medY-slope*medX};
}

/* ── Fixed scatter helper (Panels 2 & 3) ───────────────────────────────── */
/* Renders a per-die scatter of xParam vs one or more yParams into `cid`.   */
/* yParams may be a single string or an array.                               */
/* Ordinary Least Squares fit — returns {slope,intercept} or null */
function _olsFit(xs,ys){
  var n=xs.length;if(n<2)return null;
  var sx=0,sy=0,sxy=0,sx2=0;
  for(var i=0;i<n;i++){sx+=xs[i];sy+=ys[i];sxy+=xs[i]*ys[i];sx2+=xs[i]*xs[i];}
  var denom=n*sx2-sx*sx;if(Math.abs(denom)<1e-60)return null;
  var slope=(n*sxy-sx*sy)/denom;
  var intercept=(sy-slope*sx)/n;
  return{slope:slope,intercept:intercept};
}

/* Panel collapse state — dynamically sized from PCM_XY_PANELS */
var _FP_COLLAPSED=(function(){var o={};for(var i=0;i<PCM_XY_PANELS.length;i++){o[i]=false;}return o;}());
function toggleFp(n){
  _FP_COLLAPSED[n]=!_FP_COLLAPSED[n];
  var body=document.getElementById('fp'+n+'-body');
  var btn=document.getElementById('fp'+n+'-toggle');
  if(body)body.style.display=_FP_COLLAPSED[n]?'none':'flex';
  if(btn)btn.innerHTML=_FP_COLLAPSED[n]?'&#9654;':'&#9660;';
}

/* ── Fixed Panel per-plot state — dynamically sized from PCM_XY_PANELS ──────────────── */
var _FP_ST=(function(){
  var st={};
  for(var i=0;i<PCM_XY_PANELS.length;i++){
    ['a','b'].forEach(function(h){
      st['fp'+i+h]={xgrp:'',ygrp:'',x:null,ys:null,logX:false,logY:false,die:true,trend:'ols',xmin:null,xmax:null,ymin:null,ymax:null,h:500,ysrch:'',gby:[]};
    });
  }
  return st;
}());
function toggleGbyFP(pid,field){
  var st=_FP_ST[pid];if(!st)return;
  if(!st.gby)st.gby=[];
  if(field==='none'){st.gby.splice(0);}
  else{var i=st.gby.indexOf(field);if(i>=0)st.gby.splice(i,1);else st.gby.push(field);}
  fpBuild(pid);
}
function _fpDefaultY(pid){
  /* Use configured XY panels if available */
  var _m=pid.match(/^fp(\d+)([ab])$/);
  if(_m){
    var _pi=parseInt(_m[1]),_ph=_m[2];
    if(PCM_XY_PANELS.length>_pi){
      var _hc=(PCM_XY_PANELS[_pi]||{})[_ph];
      if(_hc&&_hc.ys&&_hc.ys.length){
        var _ys=new Set(_hc.ys.filter(function(p){return p in PCM_PARAM_META;}));
        if(_ys.size)return _ys;
      }
    }
  }
  return new Set(Object.keys(PCM_PARAM_META).slice(0,1));
}
function _fpAllX(pid){
  var st=_FP_ST[pid];
  var pool=st.xgrp?(PCM_GROUP_PARAMS[st.xgrp]||[]):Object.keys(PCM_PARAM_META);
  return pool.filter(function(p){return p in PCM_PARAM_META;}).sort();
}
function _fpAllY(pid){
  var st=_FP_ST[pid];
  var pool=st.ygrp?(PCM_GROUP_PARAMS[st.ygrp]||[]):Object.keys(PCM_PARAM_META);
  return pool.filter(function(p){return p in PCM_PARAM_META;}).sort();
}
function _fpEnsureY(pid){
  var st=_FP_ST[pid];if(!st)return;
  if(!st.ys||!st.ys.size)st.ys=_fpDefaultY(pid);
  if(!st.x){
    /* Use configured XY panels */
    var _m=pid.match(/^fp(\d+)([ab])$/);
    if(_m){
      var _pi=parseInt(_m[1]),_ph=_m[2];
      if(PCM_XY_PANELS.length>_pi){
        var _hc=(PCM_XY_PANELS[_pi]||{})[_ph];
        if(_hc&&_hc.x&&_hc.x in PCM_PARAM_META)st.x=_hc.x;
      }
    }
    /* No fallback to first param — leave null so chart shows hint */
  }
}
/* Render scatter chart HTML for a fixed panel plot using _FP_ST[pid] state */
function _fpRenderChart(pid){
  var st=_FP_ST[pid];_fpEnsureY(pid);
  /* SICC/CDYN: filter out zero and negative values (invalid readings) */
  var _siccCdynX=_isSiccCdyn(st.x||'');
  var allY=_fpAllY(pid);
  var selYs=allY.filter(function(p){return st.ys.has(p);});
  if(!selYs.length){st.ys=_fpDefaultY(pid);selYs=allY.filter(function(p){return st.ys.has(p);});}
  var xParam=st.x;
  if(!xParam||!(xParam in PCM_PARAM_META))
    return '<div style="padding:16px;color:#888;font-style:italic;font-size:12px">Select a valid X parameter.</div>';
  var ak=activeKeys();
  var multiY=selYs.length>1;
  var _effGby=(st.gby&&st.gby.length>0)?st.gby:VAR_GBY;  /* fall back to global group-by */
  var cm2;
  if(multiY){var cMap2={};selYs.forEach(function(k,i){cMap2[k]=_cPal(i);});cm2={map:cMap2};}
  else{cm2=_cMapWith(_effGby);}
  var xRows={};
  PCM_ROWS.forEach(function(r){if(ak.has(_rKey(r))&&r.param===xParam)xRows[_rKey(r)]=r;});
  var pts=[];
  selYs.forEach(function(yParam){
    var yRows={};
    PCM_ROWS.forEach(function(r){if(ak.has(_rKey(r))&&r.param===yParam)yRows[_rKey(r)]=r;});
    Object.keys(xRows).forEach(function(k){
      var xr=xRows[k],yr=yRows[k];if(!yr)return;
      var gk=multiY?yParam:_grpKeyWith(xr,_effGby);
      if(st.die){
        var xraw=xr.die_values||[],yraw=yr.die_values||[];
        var nd=Math.min(xraw.length,yraw.length);
        for(var di=0;di<nd;di++){
          var xrv=xraw[di],yrv=yraw[di];
          if(xrv==null||!isFinite(xrv)||yrv==null||!isFinite(yrv))continue;
          if((_siccCdynX||_isSiccCdyn(yParam))&&(xrv<=0||yrv<=0))continue;
          var xd=_toDisplayVals(xParam,[xrv]),yd=_toDisplayVals(yParam,[yrv]);
          if(xd.length&&yd.length&&isFinite(xd[0])&&isFinite(yd[0]))
            pts.push({x:xd[0],y:yd[0],lot:xr.lot,wafer:xr.wafer,gk:gk,yParam:yParam});
        }
      }else{
        var _xFlt=_siccCdynX;
        var xdv=_toDisplayVals(xParam,(xr.die_values||[]).filter(function(v){return v!=null&&isFinite(v)&&(!_xFlt||v>0);}));
        var _yFlt=_isSiccCdyn(yParam);
        var ydv=_toDisplayVals(yParam,(yr.die_values||[]).filter(function(v){return v!=null&&isFinite(v)&&(!_yFlt||v>0);}));
        var xv=_med(xdv),yv=_med(ydv);
        if(xv!=null&&isFinite(xv)&&yv!=null&&isFinite(yv))
          pts.push({x:xv,y:yv,lot:xr.lot,wafer:xr.wafer,gk:gk,yParam:yParam});
      }
    });
  });
  if(!pts.length)return '<div style="padding:16px;color:#888;font-style:italic;font-size:12px">No matching data for active selection.</div>';
  function _lxf(v){return st.logX?Math.log10(Math.max(v,1e-300)):v;}
  function _lyf(v){return st.logY?Math.log10(Math.max(v,1e-300)):v;}
  function _fmtTkF(v,isLog){if(!isLog)return _fmt(v);var pw=Math.round(v);return(Math.abs(v-pw)<0.05)?'10^'+pw:_fmt(Math.pow(10,v));}
  var lxs=pts.map(function(p){return _lxf(p.x);}),lys=pts.map(function(p){return _lyf(p.y);});
  var xmn=st.xmin!=null?_lxf(st.xmin):_safeMin(lxs),xmx=st.xmax!=null?_lxf(st.xmax):_safeMax(lxs);
  var ymn=st.ymin!=null?_lyf(st.ymin):_safeMin(lys),ymx=st.ymax!=null?_lyf(st.ymax):_safeMax(lys);
  var xrng=xmx-xmn||1,yrng=ymx-ymn||1;
  var xlo=xmn-xrng*0.08,xhi=xmx+xrng*0.08,ylo=ymn-yrng*0.08,yhi=ymx+yrng*0.08;
  var svgH=st.h,ML=90,MR=30,MT=40,MB=(multiY?88:65),svgW=820;
  var plotW=svgW-ML-MR,plotH=svgH-MT-MB;
  function xpf(v){return ML+(v-xlo)/(xhi-xlo)*plotW;}
  function ypf(v){return MT+(1-(v-ylo)/(yhi-ylo))*plotH;}
  var xmeta=PCM_PARAM_META[xParam]||{};
  var xUnit=xParam.match(/^Td_/i)?'% of tgt':(_isLeakage(xParam)?(_leakageScale([(xmeta.target||xmeta.usl||1e-6)]).unit):(xmeta.unit||''));
  var ymeta1=PCM_PARAM_META[selYs[0]]||{};
  var yUnit=selYs[0].match(/^Td_/i)?'% of tgt':(_isLeakage(selYs[0])?(_leakageScale([(ymeta1.target||ymeta1.usl||1e-6)]).unit):(ymeta1.unit||''));
  /* Store pts + bounds for crosshair attachment after innerHTML is set */
  st._lastPts=pts;
  st._lastBounds={xlo:xlo,xhi:xhi,ylo:ylo,yhi:yhi,ML:ML,MT:MT,plotW:plotW,plotH:plotH,xUnit:xUnit,multiY:multiY,selYs:selYs};
  var p=['<svg id="'+pid+'-svg" width="100%" height="'+svgH+'" viewBox="0 0 '+svgW+' '+svgH+'" style="display:block;cursor:crosshair">'];
  p.push('<rect width="'+svgW+'" height="'+svgH+'" fill="#f8f9fa"/>');
  p.push('<rect x="'+ML+'" y="'+MT+'" width="'+plotW+'" height="'+plotH+'" fill="#fff" stroke="#ccc" stroke-width="1"/>');
  for(var xi=0;xi<=6;xi++){
    var xv2=xlo+(xhi-xlo)*xi/6,xpv2=(ML+xi/6*plotW).toFixed(1);
    p.push('<line x1="'+xpv2+'" y1="'+MT+'" x2="'+xpv2+'" y2="'+(MT+plotH)+'" stroke="rgba(0,0,0,0.08)" stroke-width="0.8"/>');
    var xlbl2=_fmtTkF(xv2,st.logX)+((!st.logX&&xUnit==='% of tgt')?'%':'');
    p.push('<text x="'+xpv2+'" y="'+(MT+plotH+20)+'" text-anchor="middle" font-size="13" fill="#333">'+xlbl2+'</text>');
  }
  for(var yi=0;yi<=5;yi++){
    var yv2=ylo+(yhi-ylo)*yi/5,ypv2=(MT+plotH*(1-yi/5)).toFixed(1);
    p.push('<line x1="'+ML+'" y1="'+ypv2+'" x2="'+(ML+plotW)+'" y2="'+ypv2+'" stroke="rgba(0,0,0,0.08)" stroke-width="0.8"/>');
    p.push('<text x="'+(ML-6)+'" y="'+ypv2+'" text-anchor="end" dominant-baseline="middle" font-size="13" fill="#333">'+_fmtTkF(yv2,st.logY)+'</text>');
  }
  if(st.trend!=='none'){
    var grpTd2={},grpWBkts2={};
    pts.forEach(function(pt){
      var lxv=_lxf(pt.x),lyv=_lyf(pt.y);if(!isFinite(lxv)||!isFinite(lyv))return;
      if(!grpTd2[pt.gk])grpTd2[pt.gk]={xs:[],ys:[]};
      grpTd2[pt.gk].xs.push(lxv);grpTd2[pt.gk].ys.push(lyv);
      var wk=pt.lot+'||'+pt.wafer;
      if(!grpWBkts2[pt.gk])grpWBkts2[pt.gk]={};
      if(!grpWBkts2[pt.gk][wk])grpWBkts2[pt.gk][wk]=[];
      grpWBkts2[pt.gk][wk].push({x:lxv,y:lyv});
    });
    Object.keys(grpTd2).forEach(function(gk){
      var reg;
      if(st.trend==='theilsen'){
        var bkts=grpWBkts2[gk]||{};var wkeys=Object.keys(bkts);
        if(wkeys.length<2){reg=_ols(grpTd2[gk].xs,grpTd2[gk].ys);}
        else{
          var cslopes=[],tries=0,maxTries=4000;
          while(cslopes.length<300&&tries<maxTries){
            tries++;
            var aii=Math.floor(Math.random()*wkeys.length),bii=Math.floor(Math.random()*wkeys.length);
            if(aii===bii)continue;
            var ba2=bkts[wkeys[aii]],bb2=bkts[wkeys[bii]];
            var pa2=ba2[Math.floor(Math.random()*ba2.length)],pb2=bb2[Math.floor(Math.random()*bb2.length)];
            var dx=pb2.x-pa2.x;if(Math.abs(dx)<1e-12)continue;
            cslopes.push((pb2.y-pa2.y)/dx);
          }
          if(!cslopes.length){reg=_ols(grpTd2[gk].xs,grpTd2[gk].ys);}
          else{
            cslopes.sort(function(a,b){return a-b;});
            var m2=cslopes.length,sl=m2%2?cslopes[(m2-1)/2]:(cslopes[m2/2-1]+cslopes[m2/2])/2;
            var medX2=_med(grpTd2[gk].xs),medY2=_med(grpTd2[gk].ys);
            reg={slope:sl,intercept:medY2-sl*medX2};
          }
        }
      }else{reg=_ols(grpTd2[gk].xs,grpTd2[gk].ys);}
      if(!reg)return;
      var col2=cm2.map[gk]||_cPal(0);
      var ty1=reg.slope*xlo+reg.intercept,ty2=reg.slope*xhi+reg.intercept;
      p.push('<line x1="'+xpf(xlo).toFixed(1)+'" y1="'+Math.max(MT,Math.min(MT+plotH,ypf(ty1))).toFixed(1)+'"'
        +' x2="'+xpf(xhi).toFixed(1)+'" y2="'+Math.max(MT,Math.min(MT+plotH,ypf(ty2))).toFixed(1)+'"'
        +' stroke="'+col2+'" stroke-width="2.5" stroke-dasharray="7,3" opacity="1.0"/>');  // fpBuild fitline
    });
  }
  var _dp3={};
  pts.forEach(function(pt){
    var col2=cm2.map[pt.gk]||_cPal(0);
    var cx=xpf(_lxf(pt.x)),cy=ypf(_lyf(pt.y));
    if(!isFinite(cx)||!isFinite(cy)||cx<ML-5||cx>ML+plotW+5||cy<MT-5||cy>MT+plotH+5)return;
    if(!_dp3[col2])_dp3[col2]='';
    _dp3[col2]+='M'+cx.toFixed(1)+','+cy.toFixed(1)+'m-0.875,0a0.875,0.875,0,1,0,1.75,0a0.875,0.875,0,1,0,-1.75,0';
  });
  Object.keys(_dp3).forEach(function(col2){p.push('<path d="'+_dp3[col2]+'" fill="'+col2+'" fill-opacity="0.95" stroke="none"/>');});
  p.push('<text x="'+(ML+plotW/2)+'" y="'+(MT+plotH+20+16)+'" text-anchor="middle" font-size="13" fill="#333">'+xParam+(xUnit?' ('+xUnit+')':'')+'</text>');
  if(!multiY&&_effGby.length>0){
    var _fpGbyLbls={'lot':'Lot','wafer':'Wafer','layout':'Layout','material':'Material'};
    var _fpGbyFieldStr=_effGby.map(function(f){return _fpGbyLbls[f]||f;}).join('+');
    var _fpGbyVals=[];
    pts.forEach(function(pt){if(pt.gk!=null&&_fpGbyVals.indexOf(pt.gk)<0)_fpGbyVals.push(pt.gk);});
    _fpGbyVals.sort();
    var _shown=_fpGbyVals.slice(0,12);
    var _tspans=_shown.map(function(gk){
      var c=cm2.map[gk]||_cPal(0);
      return '<tspan fill="'+c+'" font-weight="bold">'+esc(gk)+'</tspan>';
    }).join('<tspan fill="#555">, </tspan>');
    if(_fpGbyVals.length>12)_tspans+='<tspan fill="#888"> …</tspan>';
    p.push('<text x="'+(ML+plotW/2)+'" y="'+(MT+plotH+20+16+14)+'" text-anchor="middle" font-size="10" fill="#555">&#9650; '+esc(_fpGbyFieldStr)+': '+_tspans+'</text>');
  }
  if(multiY){
    /* Y-axis group label (empty for dynamic panels) */
    var _fpYTitle='';
    if(_fpYTitle)p.push('<text transform="rotate(-90)" x="'+(-MT-plotH/2)+'" y="16" text-anchor="middle" font-size="13" font-weight="bold" fill="#333">'+_fpYTitle+'</text>');
    var _legItemW=Math.min(180,Math.floor((svgW-ML)/selYs.length));
    var _legY=MT+plotH+20+34;
    selYs.forEach(function(yp2,i){
      var col2=cm2.map[yp2]||_cPal(i);
      var _lx=ML+i*_legItemW;
      p.push('<rect x="'+_lx+'" y="'+(_legY-9)+'" width="10" height="10" fill="'+col2+'" rx="2"/>');
      p.push('<text x="'+(_lx+13)+'" y="'+_legY+'" font-size="11" fill="#333">'+esc(yp2)+'</text>');
    });
  } else {
    p.push('<text transform="rotate(-90)" x="'+(-MT-plotH/2)+'" y="16" text-anchor="middle" font-size="13" fill="#333">'+selYs[0]+(yUnit?' ('+yUnit+')':'')+'</text>');
  }
  p.push('<text x="'+(ML+4)+'" y="'+(MT-6)+'" font-size="10" fill="#999">n='+pts.length+'</text>');
  p.push('</svg>');
  return p.join('');
}
/* Attach drag-cursor interactivity to a fixed-panel SVG — call after setting innerHTML */
function _fpAttachCrosshair(pid){
  var svgEl=document.getElementById(pid+'-svg');if(!svgEl)return;
  var st=_FP_ST[pid];
  var b=st._lastBounds||{};var pts=st._lastPts||[];
  var ML=b.ML||90,MT=b.MT||40,plotW=b.plotW||700,plotH=b.plotH||400;
  var xlo=b.xlo||0,xhi=b.xhi||1,ylo=b.ylo||0,yhi=b.yhi||1;
  var xUnit=b.xUnit||'',multiY=b.multiY||false;
  /* Always-on drag cursors */
  function _fmtTkFp(v,isLog){if(!isLog)return _fmt(v);var pw=Math.round(v);return(Math.abs(v-pw)<0.05)?'10^'+pw:_fmt(Math.pow(10,v));}
  var fmtFpx=function(v){return _fmtTkFp(v,st.logX)+((!st.logX&&xUnit==='% of tgt')?'%':'');};
  var fmtFpy=function(v){return _fmtTkFp(v,st.logY);};
  _initDragCursorsXY(svgEl,pid,ML,MT,plotW,plotH,xlo,xhi,ylo,yhi,fmtFpx,fmtFpy);
  function _lxfL(v){return st.logX?Math.log10(Math.max(v,1e-300)):v;}
  function _lyfL(v){return st.logY?Math.log10(Math.max(v,1e-300)):v;}
  /* Point hover tooltip */
  svgEl.addEventListener('mousemove',function(e){
    var pt2=svgEl.createSVGPoint();pt2.x=e.clientX;pt2.y=e.clientY;
    var ctm=svgEl.getScreenCTM();if(!ctm)return;
    var sp=pt2.matrixTransform(ctm.inverse());
    var inPlot=(sp.x>=ML&&sp.x<=ML+plotW&&sp.y>=MT&&sp.y<=MT+plotH);
    if(!inPlot){_getTT().style.display='none';return;}
    function _xpFp(v){return ML+(v-xlo)/(xhi-xlo)*plotW;}
    function _ypFp(v){return MT+(1-(v-ylo)/(yhi-ylo))*plotH;}
    var ctm2=svgEl.getScreenCTM();
    var best=null,bestD=9999;
    pts.forEach(function(pt){
      var dotSvgX=_xpFp(_lxfL(pt.x)),dotSvgY=_ypFp(_lyfL(pt.y));
      var scr=svgEl.createSVGPoint();scr.x=dotSvgX;scr.y=dotSvgY;
      var scrPx=ctm2?scr.matrixTransform(ctm2):{x:dotSvgX,y:dotSvgY};
      var d=Math.sqrt((e.clientX-scrPx.x)*(e.clientX-scrPx.x)+(e.clientY-scrPx.y)*(e.clientY-scrPx.y));
      if(d<bestD){bestD=d;best=pt;}
    });
    var tt=_getTT();
    if(best&&bestD<=22){
      var yValStr=_fmt(best.y);
      var yLblLine=multiY?('<b>'+esc(best.yParam)+'</b>: '+yValStr):('Y: '+yValStr);
      tt.innerHTML='<b>'+esc(best.lot||'')+' / '+esc(best.wafer||'')+'</b><br>X: '+_fmt(best.x)+(xUnit?' '+xUnit:'')+'<br>'+yLblLine;
      tt.style.left=(e.clientX+14)+'px';tt.style.top=(e.clientY-48)+'px';tt.style.display='block';
    }else{tt.style.display='none';}
  });
  svgEl.addEventListener('mouseleave',function(){_getTT().style.display='none';});
}
function _fpBuildYList(pid){
  var el=document.getElementById(pid+'-y-list');if(!el)return;
  var st=_FP_ST[pid];
  var all=_fpAllY(pid);
  var q=(st.ysrch||'').toLowerCase();
  var vis=q?all.filter(function(p){var nm=(PCM_PARAM_META[p]||{}).name||'';return(p+' '+nm).toLowerCase().indexOf(q)>=0;}):all;
  var html='';
  vis.forEach(function(p){
    var chk=st.ys&&st.ys.has(p);
    var nm=(PCM_PARAM_META[p]||{}).name||'';
    html+='<label style="display:flex;align-items:center;gap:5px;padding:2px 6px;cursor:pointer;border-radius:3px;font-size:11px;white-space:nowrap"'
      +' onmouseover="this.style.background=\'#e8f0fe\'" onmouseout="this.style.background=\'\'">'
      +'<input type="checkbox"'+(chk?' checked':'')+' onchange="_fpToggleY(\''+pid+'\',\''+p.replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\',this.checked)" style="cursor:pointer">'
      +'<b style="font-size:11px">'+esc(p)+'</b>'+(nm?'<span style="color:#888;font-size:10px"> ('+esc(nm)+')</span>':'')+'</label>';
  });
  el.innerHTML=html||'<div style="padding:6px;color:#aaa;font-size:11px">No matches</div>';
}
function _fpYDropToggle(pid){
  var drop=document.getElementById(pid+'-y-drop');if(!drop)return;
  if(drop.style.display==='block'){drop.style.display='none';return;}
  drop.style.display='block';
  var srch=document.getElementById(pid+'-y-srch');if(srch){srch.value=_FP_ST[pid].ysrch||'';srch.focus();}
  _fpBuildYList(pid);
}
function _fpToggleY(pid,p,chk){
  var st=_FP_ST[pid];if(!st.ys)st.ys=_fpDefaultY(pid);
  if(chk)st.ys.add(p);else st.ys.delete(p);
  if(!st.ys.size)st.ys=_fpDefaultY(pid);
  var btn=document.getElementById(pid+'-y-btn');
  if(btn){
    var all=_fpAllY(pid),selCnt=all.filter(function(p2){return st.ys.has(p2);}).length;
    btn.textContent=selCnt===1?Array.from(st.ys.values()).filter(function(v){return all.indexOf(v)>=0;})[0]||selCnt+' Y':(selCnt+' Y params');
  }
  var cont=document.getElementById(pid+'-cont');if(cont){cont.innerHTML=_fpRenderChart(pid);_fpAttachCrosshair(pid);}
}
function _fpYSelAll(pid){
  var st=_FP_ST[pid];var all=_fpAllY(pid);
  var q=(st.ysrch||'').toLowerCase();
  var vis=q?all.filter(function(p){var nm=(PCM_PARAM_META[p]||{}).name||'';return(p+' '+nm).toLowerCase().indexOf(q)>=0;}):all;
  if(!st.ys)st.ys=new Set();
  vis.forEach(function(p){st.ys.add(p);});
  _fpBuildYList(pid);var cont=document.getElementById(pid+'-cont');if(cont){cont.innerHTML=_fpRenderChart(pid);_fpAttachCrosshair(pid);}
}
function _fpYClrAll(pid){
  var st=_FP_ST[pid];var all=_fpAllY(pid);
  var q=(st.ysrch||'').toLowerCase();
  var vis=q?all.filter(function(p){var nm=(PCM_PARAM_META[p]||{}).name||'';return(p+' '+nm).toLowerCase().indexOf(q)>=0;}):all;
  if(!st.ys)st.ys=_fpDefaultY(pid);
  vis.forEach(function(p){st.ys.delete(p);});
  if(!st.ys.size)st.ys=_fpDefaultY(pid);
  _fpBuildYList(pid);var cont=document.getElementById(pid+'-cont');if(cont){cont.innerHTML=_fpRenderChart(pid);_fpAttachCrosshair(pid);}
}
function _fpDownloadCSV(pid){
  var st=_FP_ST[pid];_fpEnsureY(pid);
  var all=_fpAllY(pid),selYs=all.filter(function(p){return st.ys.has(p);});
  var ak=activeKeys(),xParam=st.x;
  var xRows={};
  PCM_ROWS.forEach(function(r){if(ak.has(_rKey(r))&&r.param===xParam)xRows[_rKey(r)]=r;});
  var rows=[['lot','wafer','gk','x_param','x_val','y_param','y_val']];
  selYs.forEach(function(yParam){
    var yRows={};
    PCM_ROWS.forEach(function(r){if(ak.has(_rKey(r))&&r.param===yParam)yRows[_rKey(r)]=r;});
    Object.keys(xRows).forEach(function(k){
      var xr=xRows[k],yr=yRows[k];if(!yr)return;
      if(st.die){
        var xraw=xr.die_values||[],yraw=yr.die_values||[];
        var nd=Math.min(xraw.length,yraw.length);
        for(var di=0;di<nd;di++){
          if(xraw[di]==null||yraw[di]==null)continue;
          var xd=_toDisplayVals(xParam,[xraw[di]]),yd=_toDisplayVals(yParam,[yraw[di]]);
          if(xd.length&&yd.length)rows.push([xr.lot,xr.wafer,_grpKey(xr),xParam,xd[0],yParam,yd[0]]);
        }
      }else{
        var xdv=_toDisplayVals(xParam,(xr.die_values||[]).filter(function(v){return v!=null&&isFinite(v);}));
        var ydv=_toDisplayVals(yParam,(yr.die_values||[]).filter(function(v){return v!=null&&isFinite(v);}));
        var xv=_med(xdv),yv=_med(ydv);
        if(xv!=null&&yv!=null)rows.push([xr.lot,xr.wafer,_grpKey(xr),xParam,xv,yParam,yv]);
      }
    });
  });
  var csv=rows.map(function(r){return r.join(',');}).join('\n');
  var a=document.createElement('a');a.href='data:text/csv;charset=utf-8,'+encodeURIComponent(csv);
  a.download=pid+'_scatter.csv';a.click();
}
function fpBuild(pid){
  var wrap=document.getElementById(pid+'-wrap');if(!wrap)return;
  var st=_FP_ST[pid];_fpEnsureY(pid);
  var allX=_fpAllX(pid);
  /* Do NOT auto-pick allX[0] — only use configured x or leave blank */
  var allY=_fpAllY(pid);
  var selYs=allY.filter(function(p){return st.ys.has(p);});
  if(!selYs.length){st.ys=_fpDefaultY(pid);selYs=allY.filter(function(p){return st.ys.has(p);});}
  var selYCnt=selYs.length;
  var yBtnLbl=selYCnt===1?selYs[0]:(selYCnt?selYCnt+' Y params':'Y\u2026');
  var xOpts=st.x?'':'<option value="">-- select X --</option>';
  allX.forEach(function(p){xOpts+='<option value="'+esc(p)+'"'+(p===st.x?' selected':'')+'>'+esc(p)+'</option>';});
  var xGrpOpts='<option value="">All</option>';
  PCM_GROUPS.forEach(function(g){xGrpOpts+='<option value="'+esc(g)+'"'+(g===st.xgrp?' selected':'')+'>'+esc(g)+'</option>';});
  var yGrpOpts='<option value="">All</option>';
  PCM_GROUPS.forEach(function(g){yGrpOpts+='<option value="'+esc(g)+'"'+(g===st.ygrp?' selected':'')+'>'+esc(g)+'</option>';});
  var jsPid='\''+pid+'\'';
  var gbyHtml='<span style="width:1px;background:#ccc;align-self:stretch;margin:0 2px"></span>'
    +'<b style="font-size:11px;color:#555">Gby:</b>'
    +'<label style="font-size:11px;cursor:pointer;display:flex;align-items:center;gap:2px"><input type="checkbox" '+(st.gby.length===0?'checked':'')+' onchange="toggleGbyFP('+jsPid+',\'none\')"> None</label>'
    +'<label style="font-size:11px;cursor:pointer;display:flex;align-items:center;gap:2px"><input type="checkbox" '+(st.gby.indexOf('lot')>=0?'checked':'')+' onchange="toggleGbyFP('+jsPid+',\'lot\')"> Lot</label>'
    +'<label style="font-size:11px;cursor:pointer;display:flex;align-items:center;gap:2px"><input type="checkbox" '+(st.gby.indexOf('wafer')>=0?'checked':'')+' onchange="toggleGbyFP('+jsPid+',\'wafer\')"> Wfr</label>'
    +'<label style="font-size:11px;cursor:pointer;display:flex;align-items:center;gap:2px"><input type="checkbox" '+(st.gby.indexOf('layout')>=0?'checked':'')+' onchange="toggleGbyFP('+jsPid+',\'layout\')"> Lyt</label>'
    +'<label style="font-size:11px;cursor:pointer;display:flex;align-items:center;gap:2px"><input type="checkbox" '+(st.gby.indexOf('material')>=0?'checked':'')+' onchange="toggleGbyFP('+jsPid+',\'material\')"> Mat</label>';
  var _xmFp=st.x?(PCM_PARAM_META[st.x]||{}):{};
  var _xUFp=st.x?(st.x.match(/^Td_/i)?'% of tgt':(_isLeakage(st.x)?_leakageScale([(_xmFp.target||_xmFp.usl||1e-6)]).unit:(_xmFp.unit||''))):'';
  var _xLblFp=st.x?(st.x+(_xmFp.name?' ('+_xmFp.name+')':'')+' \u2014 '+_xUFp):'Select X parameter\u2026';
  var barHtml='<div style="display:flex;flex-direction:column;flex-shrink:0;background:#f8f9fa;border-bottom:1px solid #dde;padding:5px 10px;gap:4px">'
    +'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">'
    +'<b style="font-size:12px;color:#2c3e50">&#10799; '+esc(_xLblFp)+'</b>'
    +'<label style="font-size:11px;display:flex;align-items:center;gap:2px">X grp:'
    +'<select onchange="_FP_ST['+jsPid+'].xgrp=this.value;_FP_ST['+jsPid+'].x=_fpAllX('+jsPid+')[0]||\'\';fpBuild('+jsPid+')" style="font-size:11px;padding:1px 3px;border-radius:3px;border:1px solid #ccc">'+xGrpOpts+'</select></label>'
    +'<label style="font-size:11px;display:flex;align-items:center;gap:2px">X:'
    +'<select onchange="_FP_ST['+jsPid+'].x=this.value||null;fpBuild('+jsPid+')" style="font-size:11px;padding:1px 3px;border-radius:3px;border:1px solid #ccc;max-width:180px">'+xOpts+'</select></label>'
    +'<span style="width:1px;background:#ccc;align-self:stretch;margin:0 1px"></span>'
    +'<label style="font-size:11px;display:flex;align-items:center;gap:2px">Y grp:'
    +'<select onchange="_FP_ST['+jsPid+'].ygrp=this.value;_FP_ST['+jsPid+'].ys=null;fpBuild('+jsPid+')" style="font-size:11px;padding:1px 3px;border-radius:3px;border:1px solid #ccc">'+yGrpOpts+'</select></label>'
    +'<span style="font-size:11px;position:relative;display:inline-block">'
    +'Y: <button id="'+pid+'-y-btn" onclick="_fpYDropToggle('+jsPid+')" style="font-size:11px;padding:1px 6px;border-radius:3px;border:1px solid #ccc;background:#fff;cursor:pointer;min-width:90px;max-width:200px;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(yBtnLbl)+'</button>'
    +'<div id="'+pid+'-y-drop" style="display:none;position:absolute;top:100%;left:0;z-index:9999;background:#fff;border:1px solid #ccc;border-radius:4px;box-shadow:0 4px 12px rgba(0,0,0,0.15);min-width:260px;max-width:400px">'
    +'<div style="display:flex;align-items:center;gap:4px;padding:4px 5px;border-bottom:1px solid #e8e8e8;background:#f5f5f5">'
    +'<input id="'+pid+'-y-srch" placeholder="Search\u2026" oninput="_FP_ST['+jsPid+'].ysrch=this.value;_fpBuildYList('+jsPid+')" style="flex:1;font-size:11px;padding:2px 5px;border:1px solid #ccc;border-radius:3px">'
    +'<button onclick="_fpYSelAll('+jsPid+')" style="font-size:10px;padding:1px 5px;border-radius:3px;border:1px solid #bbb;background:#e8f0fe;cursor:pointer">All</button>'
    +'<button onclick="_fpYClrAll('+jsPid+')" style="font-size:10px;padding:1px 5px;border-radius:3px;border:1px solid #bbb;background:#fef0e8;cursor:pointer">Clr</button>'
    +'</div>'
    +'<div id="'+pid+'-y-list" style="max-height:240px;overflow-y:auto;padding:3px 0"></div>'
    +'</div></span>'
    +'</div>'
    +'<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">'
    +'<label style="font-size:11px;cursor:pointer;display:flex;align-items:center;gap:2px"><input type="checkbox"'+(st.logX?' checked':'')+' onchange="_FP_ST['+jsPid+'].logX=this.checked;fpBuild('+jsPid+')"> logX</label>'
    +'<label style="font-size:11px;cursor:pointer;display:flex;align-items:center;gap:2px"><input type="checkbox"'+(st.logY?' checked':'')+' onchange="_FP_ST['+jsPid+'].logY=this.checked;fpBuild('+jsPid+')"> logY</label>'
    +'<label style="font-size:11px;cursor:pointer;display:flex;align-items:center;gap:2px"><input type="checkbox"'+(st.die?' checked':'')+' onchange="_FP_ST['+jsPid+'].die=this.checked;fpBuild('+jsPid+')"> Per die</label>'
    +'<span style="width:1px;background:#ccc;align-self:stretch;margin:0 2px"></span>'
    +'<span style="font-size:11px;color:#555">Trend:</span>'
    +'<label style="font-size:11px;cursor:pointer;display:flex;align-items:center;gap:2px"><input type="radio" name="'+pid+'-trend" value="none"'+(st.trend==='none'?' checked':'')+' onchange="_FP_ST['+jsPid+'].trend=this.value;fpBuild('+jsPid+')"> None</label>'
    +'<label style="font-size:11px;cursor:pointer;display:flex;align-items:center;gap:2px"><input type="radio" name="'+pid+'-trend" value="ols"'+(st.trend==='ols'?' checked':'')+' onchange="_FP_ST['+jsPid+'].trend=this.value;fpBuild('+jsPid+')"> OLS</label>'
    +'<label style="font-size:11px;cursor:pointer;display:flex;align-items:center;gap:2px"><input type="radio" name="'+pid+'-trend" value="theilsen"'+(st.trend==='theilsen'?' checked':'')+' onchange="_FP_ST['+jsPid+'].trend=this.value;fpBuild('+jsPid+')"> T-S</label>'
    +gbyHtml
    +'</div>'
    +'<div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap">'
    +'<span style="font-size:11px;color:#555">X:</span>'
    +'<input type="number" placeholder="auto" title="X min" value="'+(st.xmin!=null?st.xmin:'')+'" onchange="_FP_ST['+jsPid+'].xmin=this.value?+this.value:null;fpBuild('+jsPid+')" style="width:60px;font-size:11px;padding:1px 3px">'
    +'<span style="font-size:10px;color:#aaa">\u2013</span>'
    +'<input type="number" placeholder="auto" title="X max" value="'+(st.xmax!=null?st.xmax:'')+'" onchange="_FP_ST['+jsPid+'].xmax=this.value?+this.value:null;fpBuild('+jsPid+')" style="width:60px;font-size:11px;padding:1px 3px">'
    +'<span style="width:1px;background:#ccc;align-self:stretch;margin:0 3px"></span>'
    +'<span style="font-size:11px;color:#555">Y:</span>'
    +'<input type="number" placeholder="auto" title="Y min" value="'+(st.ymin!=null?st.ymin:'')+'" onchange="_FP_ST['+jsPid+'].ymin=this.value?+this.value:null;fpBuild('+jsPid+')" style="width:60px;font-size:11px;padding:1px 3px">'
    +'<span style="font-size:10px;color:#aaa">\u2013</span>'
    +'<input type="number" placeholder="auto" title="Y max" value="'+(st.ymax!=null?st.ymax:'')+'" onchange="_FP_ST['+jsPid+'].ymax=this.value?+this.value:null;fpBuild('+jsPid+')" style="width:60px;font-size:11px;padding:1px 3px">'
    +'<span style="width:1px;background:#ccc;align-self:stretch;margin:0 3px"></span>'
    +'<label style="font-size:11px;display:flex;align-items:center;gap:3px">H '
    +'<input type="range" min="200" max="1000" step="25" value="'+st.h+'" oninput="_FP_ST['+jsPid+'].h=+this.value;document.getElementById(\''+pid+'-h-val\').textContent=this.value+\'px\';fpBuild('+jsPid+')" style="width:70px;accent-color:#3498db">'
    +'<span id="'+pid+'-h-val" style="min-width:30px;font-size:10px;color:#555">'+st.h+'px</span></label>'
    +'<span style="width:1px;background:#ccc;align-self:stretch;margin:0 2px"></span>'
    +'<button onclick="_fpDownloadCSV('+jsPid+')" title="Download CSV" style="padding:2px 8px;font-size:10px;font-weight:bold;border:none;border-radius:3px;background:#27ae60;color:#fff;cursor:pointer" onmouseover="this.style.background=\'#1e8449\'" onmouseout="this.style.background=\'#27ae60\'">&#11015; CSV</button>'
    +'</div>'
    +'</div>';
  wrap.innerHTML=barHtml+'<div id="'+pid+'-cont" style="flex:1;overflow-y:auto;padding:0 8px 8px">'+_fpRenderChart(pid)+'</div>';
  _fpBuildYList(pid);
  _fpAttachCrosshair(pid);
}

function _buildFixedScatter(cid,xParam,yParams,accentColor){
  var cont=document.getElementById(cid);if(!cont)return;
  if(!xParam)xParam='';
  if(typeof yParams==='string')yParams=[yParams];
  /* Filter to params that exist */
  yParams=yParams.filter(function(y){return y&&(y in PCM_PARAM_META);});
  if(!xParam||(!(xParam in PCM_PARAM_META))||!yParams.length){
    cont.innerHTML='<div style="padding:16px;color:#888;font-style:italic;font-size:12px">No data — parameter not available.</div>';return;}
  var ak=activeKeys();
  var xRows={};
  PCM_ROWS.forEach(function(r){if(ak.has(_rKey(r))&&r.param===xParam)xRows[_rKey(r)]=r;});
  var multiY=yParams.length>1;
  var cPal=['#2980b9','#27ae60','#8e44ad','#e67e22','#c0392b','#16a085'];
  var cm={};yParams.forEach(function(y,i){cm[y]=cPal[i%cPal.length];});
  var pts=[];
  yParams.forEach(function(yParam){
    var yRows={};
    PCM_ROWS.forEach(function(r){if(ak.has(_rKey(r))&&r.param===yParam)yRows[_rKey(r)]=r;});
    Object.keys(xRows).forEach(function(k){
      var xr=xRows[k],yr=yRows[k];if(!yr)return;
      var xraw=xr.die_values||[],yraw=yr.die_values||[];
      var nd=Math.min(xraw.length,yraw.length);
      for(var di=0;di<nd;di++){
        var xrv=xraw[di],yrv=yraw[di];
        if(xrv==null||!isFinite(xrv)||yrv==null||!isFinite(yrv))continue;
        var xd=_toDisplayVals(xParam,[xrv]),yd=_toDisplayVals(yParam,[yrv]);
        if(xd.length&&yd.length&&isFinite(xd[0])&&isFinite(yd[0]))
          pts.push({x:xd[0],y:yd[0],gk:multiY?yParam:_grpKey(xr),yParam:yParam});
      }
    });
  });
  if(!pts.length){
    cont.innerHTML='<div style="padding:16px;color:#888;font-style:italic;font-size:12px">No matching data for active selection.</div>';return;}
  var lxs=pts.map(function(p){return p.x;}),lys=pts.map(function(p){return p.y;});
  var xmn=_safeMin(lxs),xmx=_safeMax(lxs),ymn=_safeMin(lys),ymx=_safeMax(lys);
  var xrng=xmx-xmn||1,yrng=ymx-ymn||1;
  var xlo=xmn-xrng*0.08,xhi=xmx+xrng*0.08,ylo=ymn-yrng*0.08,yhi=ymx+yrng*0.08;
  var svgH=440,ML=80,MR=20,MT=28,MB=60,svgW=700;
  var plotW=svgW-ML-MR,plotH=svgH-MT-MB;
  function xp(v){return ML+(v-xlo)/(xhi-xlo)*plotW;}
  function yp(v){return MT+(1-(v-ylo)/(yhi-ylo))*plotH;}
  var xmeta=PCM_PARAM_META[xParam]||{};
  var xUnit=xParam.match(/^Td_/i)?'% of tgt':(_isLeakage(xParam)?(_leakageScale([(xmeta.target||xmeta.usl||1e-6)]).unit):(xmeta.unit||''));
  var ymeta0=PCM_PARAM_META[yParams[0]]||{};
  var yUnit=yParams[0].match(/^Td_/i)?'% of tgt':(_isLeakage(yParams[0])?(_leakageScale([(ymeta0.target||ymeta0.usl||1e-6)]).unit):(ymeta0.unit||''));
  var p=['<svg width="100%" viewBox="0 0 '+svgW+' '+svgH+'" style="display:block;max-height:440px">'];
  p.push('<rect width="'+svgW+'" height="'+svgH+'" fill="#f8f9fa"/>');
  p.push('<rect x="'+ML+'" y="'+MT+'" width="'+plotW+'" height="'+plotH+'" fill="#fff" stroke="#ccc" stroke-width="1"/>');
  for(var xi=0;xi<=6;xi++){
    var xv=xlo+(xhi-xlo)*xi/6,xpv=(ML+xi/6*plotW).toFixed(1);
    p.push('<line x1="'+xpv+'" y1="'+MT+'" x2="'+xpv+'" y2="'+(MT+plotH)+'" stroke="rgba(0,0,0,0.07)" stroke-width="0.8"/>');
    p.push('<text x="'+xpv+'" y="'+(MT+plotH+18)+'" text-anchor="middle" font-size="11" fill="#555">'+_fmt(xv)+'</text>');
  }
  for(var yi=0;yi<=5;yi++){
    var yv=ylo+(yhi-ylo)*yi/5,ypv=(MT+plotH*(1-yi/5)).toFixed(1);
    p.push('<line x1="'+ML+'" y1="'+ypv+'" x2="'+(ML+plotW)+'" y2="'+ypv+'" stroke="rgba(0,0,0,0.07)" stroke-width="0.8"/>');
    p.push('<text x="'+(ML-5)+'" y="'+ypv+'" text-anchor="end" dominant-baseline="middle" font-size="11" fill="#555">'+_fmt(yv)+'</text>');
  }
  /* OLS trend per group */
  var grpPts={};
  pts.forEach(function(pt){(grpPts[pt.gk]=grpPts[pt.gk]||[]).push(pt);});
  Object.keys(grpPts).forEach(function(gk){
    var gpts=grpPts[gk],n=gpts.length;if(n<2)return;
    var gcolor=multiY?cm[gk]:(accentColor||'#2980b9');
    var xs=gpts.map(function(p2){return p2.x;}),ys=gpts.map(function(p2){return p2.y;});
    var t=_olsFit(xs,ys);if(!t)return;
    var x0=xlo,x1=xhi,y0=t.slope*x0+t.intercept,y1=t.slope*x1+t.intercept;
    p.push('<line x1="'+xp(x0).toFixed(1)+'" y1="'+yp(y0).toFixed(1)+'" x2="'+xp(x1).toFixed(1)+'" y2="'+yp(y1).toFixed(1)+'" stroke="'+gcolor+'" stroke-width="1.5" stroke-dasharray="5,3" opacity="0.7"/>');
  });
  /* Dots */
  pts.forEach(function(pt){
    var gcolor=multiY?cm[pt.gk]:(accentColor||'#2980b9');
    var cx=xp(pt.x).toFixed(1),cy=yp(pt.y).toFixed(1);
    if(+cx<ML-4||+cx>ML+plotW+4||+cy<MT-4||+cy>MT+plotH+4)return;
    p.push('<circle cx="'+cx+'" cy="'+cy+'" r="2.2" fill="'+gcolor+'" opacity="0.55"/>');
  });
  /* Axis labels */
  p.push('<text x="'+(ML+plotW/2)+'" y="'+(svgH-8)+'" text-anchor="middle" font-size="12" fill="#333">'+xParam+(xUnit?' ('+xUnit+')':'')+'</text>');
  p.push('<text transform="rotate(-90)" x="'+(-MT-plotH/2)+'" y="14" text-anchor="middle" font-size="12" fill="#333">'+(yParams.length===1?yParams[0]:yUnit?(yUnit):yParams[0])+(yUnit?' ('+yUnit+')':'')+'</text>');
  p.push('<text x="'+(ML+4)+'" y="'+(MT-6)+'" font-size="10" fill="#999">n='+pts.length+'</text>');
  p.push('</svg>');
  cont.innerHTML=p.join('');
}

/* Find first PCM param key matching ordered tokens in pattern (e.g. 'upm*0704*950*sds') */
function _findParamLike(pattern){
  var tokens=pattern.toLowerCase().split('*').filter(Boolean);
  var keys=Object.keys(PCM_PARAM_META);
  for(var i=0;i<keys.length;i++){
    var k=keys[i].toLowerCase(),ok=true,pos=0;
    for(var t=0;t<tokens.length;t++){var idx=k.indexOf(tokens[t],pos);if(idx<0){ok=false;break;}pos=idx+tokens[t].length;}
    if(ok)return keys[i];
  }
  return null;
}

function buildFixedPanels(){
  for(var i=0;i<PCM_XY_PANELS.length;i++){
    fpBuild('fp'+i+'a');
    fpBuild('fp'+i+'b');
  }
}

function buildXYTab(){
  if(_XY_Y===null)_XY_Y=_xyDefaultY();
  _xyYsEnsure();   /* ensure _XY_YS seeded from _XY_Y */
  _populateXYDl('xy-dl-x');
  var ix=document.getElementById('xy-inp-x');if(ix&&document.activeElement!==ix)ix.value=_XY_X||'';
  var cont=document.getElementById('xy-cont');if(!cont)return;
  var ak=activeKeys();
  var xParam=_XY_X;
  _xyBuildSelects();    /* rebuild X <select> */
  _xyBuildYChecklist(); /* rebuild Y button label + checklist */

  /* Multi-Y mode: selected Y params act as the group-by dimension */
  var multiY=_XY_YS.length>1;
  var validYs=_XY_YS.filter(function(y){return y in PCM_PARAM_META;});
  if(!validYs.length||!xParam||!(xParam in PCM_PARAM_META)){
    cont.innerHTML='<div style="padding:24px;color:#888">Select valid X and Y parameters.</div>';return;}

  /* Dim group-by controls in multi-Y mode */
  var gbyWrap=document.getElementById('xy-gby-wrap');
  if(gbyWrap){
    gbyWrap.style.opacity=multiY?'0.35':'';
    gbyWrap.style.pointerEvents=multiY?'none':'';
    gbyWrap.title=multiY?'Group-by disabled \u2014 Y params are the groups':'';
  }

  /* Color map: in multi-Y, Y param names are the group keys */
  var cm;
  if(multiY){
    var cKeys2=validYs.slice();
    var cMap2={};cKeys2.forEach(function(k,i){cMap2[k]=_cPal(i);});
    cm={keys:cKeys2,map:cMap2};
  }else{
    cm=_cMap();
  }

  var xmeta=PCM_PARAM_META[xParam]||{};
  var xUnit=xParam.match(/^Td_/i)?'% of tgt':(_isLeakage(xParam)?(_leakageScale([(xmeta.target||xmeta.usl||1e-6)]).unit):(xmeta.unit||''));
  /* Primary Y meta (for axis label when single-Y) */
  var yParam1=validYs[0];
  var ymeta1=PCM_PARAM_META[yParam1]||{};
  var yUnit1=yParam1.match(/^Td_/i)?'% of tgt':(_isLeakage(yParam1)?(_leakageScale([(ymeta1.target||ymeta1.usl||1e-6)]).unit):(ymeta1.unit||''));

  /* Collect X rows */
  var xRows={};
  PCM_ROWS.forEach(function(r){
    if(!ak.has(_rKey(r)))return;
    var k=_rKey(r);
    if(r.param===xParam)xRows[k]=r;
  });

  /* Collect points for each Y param */
  var pts=[];
  validYs.forEach(function(yParam){
    var yRows={};
    PCM_ROWS.forEach(function(r){
      if(!ak.has(_rKey(r)))return;
      var k=_rKey(r);
      if(r.param===yParam)yRows[k]=r;
    });
    Object.keys(xRows).forEach(function(k){
      var xr=xRows[k],yr=yRows[k];if(!yr)return;
      var gk=multiY?yParam:_grpKey(xr);
      var yUnitPt=(PCM_PARAM_META[yParam]||{}).unit||'';
      var _xySiccX=_isSiccCdyn(xParam),_xySiccY=_isSiccCdyn(yParam);
      if(_XY_DIE){
        var xraw=xr.die_values||[],yraw=yr.die_values||[];
        var ndRaw=Math.min(xraw.length,yraw.length);
        for(var di=0;di<ndRaw;di++){
          var xrv=xraw[di],yrv=yraw[di];
          if(xrv==null||!isFinite(xrv)||yrv==null||!isFinite(yrv))continue;
          if((_xySiccX&&xrv<=0)||(_xySiccY&&yrv<=0))continue;
          var xd=_toDisplayVals(xParam,[xrv]),yd=_toDisplayVals(yParam,[yrv]);
          if(!xd.length||!yd.length||!isFinite(xd[0])||!isFinite(yd[0]))continue;
          pts.push({x:xd[0],y:yd[0],lot:xr.lot,wafer:xr.wafer,gk:gk,yParam:yParam,yUnit:yUnitPt});
        }
      }else{
        var xdv2=_toDisplayVals(xParam,(xr.die_values||[]).filter(function(v){return v!=null&&isFinite(v)&&(!_xySiccX||v>0);}));
        var ydv2=_toDisplayVals(yParam,(yr.die_values||[]).filter(function(v){return v!=null&&isFinite(v)&&(!_xySiccY||v>0);}));
        var xv=_med(xdv2),yv=_med(ydv2);
        if(xv!=null&&isFinite(xv)&&yv!=null&&isFinite(yv))pts.push({x:xv,y:yv,lot:xr.lot,wafer:xr.wafer,gk:gk,yParam:yParam,yUnit:yUnitPt});
      }
    });
  });
  if(!pts.length){cont.innerHTML='<div style="padding:24px;color:#888;font-style:italic">No matching data for selected X / Y.</div>';return;}

  /* Log helpers */
  function _lx(v){return _XY_LOG_X?Math.log10(Math.max(v,1e-300)):v;}
  function _ly(v){return _XY_LOG_Y?Math.log10(Math.max(v,1e-300)):v;}
  function _fmtTk(v,isLog){
    if(!isLog)return _fmt(v);
    var pw=Math.round(v);return(Math.abs(v-pw)<0.05)?'10^'+pw:_fmt(Math.pow(10,v));
  }

  var lxs=pts.map(function(p){return _lx(p.x);}),lys=pts.map(function(p){return _ly(p.y);});
  var xmn=_XY_XMIN!=null?_lx(_XY_XMIN):_safeMin(lxs);
  var xmx=_XY_XMAX!=null?_lx(_XY_XMAX):_safeMax(lxs);
  var ymn=_XY_YMIN!=null?_ly(_XY_YMIN):_safeMin(lys);
  var ymx=_XY_YMAX!=null?_ly(_XY_YMAX):_safeMax(lys);
  var xrng=xmx-xmn||1,yrng=ymx-ymn||1;
  var xpad=xrng*0.08,ypad=yrng*0.08;
  var xlo=xmn-xpad,xhi=xmx+xpad,ylo=ymn-ypad,yhi=ymx+ypad;

  var svgH=_XY_H,ML=90,MR=30,MT=40,MB=85;
  var plotW=820-ML-MR,plotH=svgH-MT-MB;
  var svgW=820;
  function xp(v){return ML+(v-xlo)/(xhi-xlo)*plotW;}
  function yp(v){return MT+(1-(v-ylo)/(yhi-ylo))*plotH;}

  var p=['<svg id="xy-svg" width="100%" height="'+svgH+'" viewBox="0 0 '+svgW+' '+svgH+'" style="display:block">'];
  p.push('<rect width="'+svgW+'" height="'+svgH+'" fill="#f8f9fa"/>');
  p.push('<rect x="'+ML+'" y="'+MT+'" width="'+plotW+'" height="'+plotH+'" fill="#fff" stroke="#ccc" stroke-width="1"/>');
  /* X ticks */
  for(var xi=0;xi<=6;xi++){
    var xv=xlo+(xhi-xlo)*xi/6,xpv=(ML+xi/6*plotW).toFixed(1);
    p.push('<line x1="'+xpv+'" y1="'+MT+'" x2="'+xpv+'" y2="'+(MT+plotH)+'" stroke="rgba(0,0,0,0.08)" stroke-width="0.8"/>');
    var xlbl=_fmtTk(xv,_XY_LOG_X)+((!_XY_LOG_X&&xUnit==='% of tgt')?'%':'');
    p.push('<text x="'+xpv+'" y="'+(MT+plotH+20)+'" text-anchor="middle" font-size="13" fill="#333">'+xlbl+'</text>');
  }
  /* Y ticks */
  for(var yi=0;yi<=5;yi++){
    var yv=ylo+(yhi-ylo)*yi/5,ypv=(MT+plotH*(1-yi/5)).toFixed(1);
    p.push('<line x1="'+ML+'" y1="'+ypv+'" x2="'+(ML+plotW)+'" y2="'+ypv+'" stroke="rgba(0,0,0,0.08)" stroke-width="0.8"/>');
    p.push('<text x="'+(ML-6)+'" y="'+ypv+'" text-anchor="end" dominant-baseline="middle" font-size="13" fill="#333">'+_fmtTk(yv,_XY_LOG_Y)+'</text>');
  }

  /* Trend lines per group */
  if(_XY_TREND!=='none'){
    var grpTd={};     /* all per-die (lx, ly) points — used by OLS */
    var grpWBkts={};  /* gk -> waferKey -> [{x,y}] — used by Theil-Sen cross-wafer sampling */
    pts.forEach(function(pt){
      var lxv=_lx(pt.x),lyv=_ly(pt.y);
      if(!isFinite(lxv)||!isFinite(lyv))return;
      if(!grpTd[pt.gk])grpTd[pt.gk]={xs:[],ys:[]};
      grpTd[pt.gk].xs.push(lxv);grpTd[pt.gk].ys.push(lyv);
      /* Bucket each die under its (group, lot, wafer) key */
      var wk=pt.lot+'||'+pt.wafer;
      if(!grpWBkts[pt.gk])grpWBkts[pt.gk]={};
      if(!grpWBkts[pt.gk][wk])grpWBkts[pt.gk][wk]=[];
      grpWBkts[pt.gk][wk].push({x:lxv,y:lyv});
    });
    Object.keys(grpTd).forEach(function(gk){
      var reg;
      if(_XY_TREND==='theilsen'){
        /* Cross-wafer die-pair sampling:
           Only pair dies from DIFFERENT wafers, then take the median pairwise slope.
           This isolates between-wafer variation (the same signal OLS sees, but robust
           to outliers via median).  All-pairs Theil-Sen gave slope≈0 because
           within-wafer pairs (randomly distributed) dominate and their slopes cancel.
           Wafer-median Theil-Sen also gave slope≈0 when wafer X-medians are tight
           (process control) — too few distinct X positions to drive the estimate. */
        var bkts=grpWBkts[gk]||{};
        var wkeys=Object.keys(bkts);
        if(wkeys.length<2){
          reg=_ols(grpTd[gk].xs,grpTd[gk].ys);
        }else{
          var cslopes=[],tries=0,maxTries=4000;
          while(cslopes.length<300&&tries<maxTries){
            tries++;
            var ai=Math.floor(Math.random()*wkeys.length);
            var bi=Math.floor(Math.random()*wkeys.length);
            if(ai===bi)continue;
            var ba=bkts[wkeys[ai]],bb=bkts[wkeys[bi]];
            var pa=ba[Math.floor(Math.random()*ba.length)];
            var pb=bb[Math.floor(Math.random()*bb.length)];
            var dx=pb.x-pa.x;
            if(Math.abs(dx)<1e-12)continue;
            cslopes.push((pb.y-pa.y)/dx);
          }
          if(!cslopes.length){
            reg=_ols(grpTd[gk].xs,grpTd[gk].ys);
          }else{
            cslopes.sort(function(a,b){return a-b;});
            var m2=cslopes.length,slope=m2%2?cslopes[(m2-1)/2]:(cslopes[m2/2-1]+cslopes[m2/2])/2;
            var medX=_med(grpTd[gk].xs),medY=_med(grpTd[gk].ys);
            reg={slope:slope,intercept:medY-slope*medX};
          }
        }
      }else{
        reg=_ols(grpTd[gk].xs,grpTd[gk].ys);
      }
      if(!reg)return;
      var col=cm.map[gk]||_cPal(0);
      var ty1=reg.slope*xlo+reg.intercept,ty2=reg.slope*xhi+reg.intercept;
      p.push('<line x1="'+xp(xlo).toFixed(1)+'" y1="'+Math.max(MT,Math.min(MT+plotH,yp(ty1))).toFixed(1)+'"'
        +' x2="'+xp(xhi).toFixed(1)+'" y2="'+Math.max(MT,Math.min(MT+plotH,yp(ty2))).toFixed(1)+'"'
        +' stroke="'+col+'" stroke-width="2.5" stroke-dasharray="7,3" opacity="1.0"/>');
    });
  }

  /* Dots */
  var _dp2={};
  pts.forEach(function(pt){
    var col=cm.map[pt.gk]||_cPal(0);
    var lxv=_lx(pt.x),lyv=_ly(pt.y);
    if(!isFinite(lxv)||!isFinite(lyv))return;
    var cx=xp(lxv),cy=yp(lyv);
    if(cx<ML-5||cx>ML+plotW+5||cy<MT-5||cy>MT+plotH+5)return;
    if(!_dp2[col])_dp2[col]='';
    _dp2[col]+='M'+cx.toFixed(1)+','+cy.toFixed(1)+'m-0.875,0a0.875,0.875,0,1,0,1.75,0a0.875,0.875,0,1,0,-1.75,0';
  });
  Object.keys(_dp2).forEach(function(col){p.push('<path d="'+_dp2[col]+'" fill="'+col+'" opacity="0.95"/>');});

  /* Median diamond per group */
  var grpXY={};
  pts.forEach(function(pt){
    if(!grpXY[pt.gk]){grpXY[pt.gk]={xs:[],ys:[]};}
    var lxv=_lx(pt.x),lyv=_ly(pt.y);
    if(isFinite(lxv)&&isFinite(lyv)){grpXY[pt.gk].xs.push(lxv);grpXY[pt.gk].ys.push(lyv);}
  });
  Object.keys(grpXY).forEach(function(gk){
    var gd=grpXY[gk],mx=_med(gd.xs),my=_med(gd.ys);
    if(mx==null||my==null)return;
    var col=cm.map[gk]||_cPal(0);
    var cx=xp(mx),cy=yp(my),ds=9;
    if(cx<ML||cx>ML+plotW||cy<MT||cy>MT+plotH)return;
    p.push('<polygon points="'+cx+','+(cy-ds)+' '+(cx+ds)+','+cy+' '+cx+','+(cy+ds)+' '+(cx-ds)+','+cy+'"'
      +' fill="'+col+'" stroke="#fff" stroke-width="1.8" opacity="0.95"/>');
  });

  /* Axis labels */
  var xLbl=esc(xParam+(xmeta.name?' ('+xmeta.name+')':''))+(xUnit?' \u2014 '+esc(xUnit):'');
  var yLbl=multiY
    ?('Multiple Y params ('+validYs.length+')')
    :esc(yParam1+(ymeta1.name?' ('+ymeta1.name+')':''))+(yUnit1?' \u2014 '+esc(yUnit1):'');
  if(_XY_LOG_X)xLbl+=' [log\u2081\u2080]';
  if(_XY_LOG_Y)yLbl+=' [log\u2081\u2080]';
  p.push('<text x="'+(ML+plotW/2)+'" y="'+(svgH-4)+'" text-anchor="middle" font-size="15" font-weight="bold" fill="#222">'+xLbl+'</text>');
  p.push('<text transform="translate(14,'+(MT+plotH/2)+') rotate(-90)" text-anchor="middle" font-size="15" font-weight="bold" fill="#222">'+yLbl+'</text>');
  /* Stats — overall Pearson r (shown only in single-Y mode; multiY shows N only) */
  var rawXs=pts.map(function(p){return p.x;}),rawYs=pts.map(function(p){return p.y;});
  var statsStr='N='+pts.length+(multiY?'':' \u00a0 r='+_corrXY(rawXs,rawYs));
  p.push('<text x="'+(ML+plotW-4)+'" y="'+(MT+16)+'" text-anchor="end" font-size="13" fill="#555">'+statsStr+'</text>');
  /* Crosshair C1 elements (initially hidden) */
  p.push('<line id="xy-ch-v" x1="0" y1="'+MT+'" x2="0" y2="'+(MT+plotH)+'" stroke="#e74c3c" stroke-width="1" stroke-dasharray="5,3" opacity="0.8" display="none" pointer-events="none"/>');
  p.push('<line id="xy-ch-h" x1="'+ML+'" y1="0" x2="'+(ML+plotW)+'" y2="0" stroke="#e74c3c" stroke-width="1" stroke-dasharray="5,3" opacity="0.8" display="none" pointer-events="none"/>');
  p.push('<rect id="xy-ch-xlbg" rx="2" ry="2" fill="rgba(255,255,255,0.82)" display="none" pointer-events="none"/>');
  p.push('<text id="xy-ch-xl" y="'+(MT+plotH+36)+'" text-anchor="middle" font-size="17" font-weight="bold" fill="#c0392b" display="none" pointer-events="none"></text>');
  p.push('<rect id="xy-ch-ylbg" rx="2" ry="2" fill="rgba(255,255,255,0.82)" display="none" pointer-events="none"/>');
  p.push('<text id="xy-ch-yl" x="'+(ML-4)+'" text-anchor="end" dominant-baseline="middle" font-size="17" font-weight="bold" fill="#c0392b" display="none" pointer-events="none"></text>');
  /* Crosshair C2 (measure) elements */
  p.push('<line id="xy-c2v" x1="0" y1="'+MT+'" x2="0" y2="'+(MT+plotH)+'" stroke="#0097a7" stroke-width="1" stroke-dasharray="4,4" opacity="0.9" display="none" pointer-events="none"/>');
  p.push('<line id="xy-c2h" x1="'+ML+'" y1="0" x2="'+(ML+plotW)+'" y2="0" stroke="#0097a7" stroke-width="1" stroke-dasharray="4,4" opacity="0.9" display="none" pointer-events="none"/>');
  p.push('<rect id="xy-c2xlbg" rx="2" ry="2" fill="rgba(224,247,250,0.88)" display="none" pointer-events="none"/>');
  p.push('<text id="xy-c2xl" y="'+(MT+plotH+52)+'" text-anchor="middle" font-size="14" font-weight="bold" fill="#006064" display="none" pointer-events="none"></text>');
  p.push('<rect id="xy-c2ylbg" rx="2" ry="2" fill="rgba(224,247,250,0.88)" display="none" pointer-events="none"/>');
  p.push('<text id="xy-c2yl" x="'+(ML-4)+'" text-anchor="end" dominant-baseline="middle" font-size="14" font-weight="bold" fill="#006064" display="none" pointer-events="none"></text>');
  p.push('<rect id="xy-c2dxbg" rx="2" ry="2" fill="rgba(255,255,224,0.92)" display="none" pointer-events="none"/>');
  p.push('<text id="xy-c2dx" text-anchor="middle" font-size="13" font-weight="bold" fill="#004d57" display="none" pointer-events="none"></text>');
  p.push('<rect id="xy-c2dybg" rx="2" ry="2" fill="rgba(255,255,224,0.92)" display="none" pointer-events="none"/>');
  p.push('<text id="xy-c2dy" text-anchor="middle" font-size="13" font-weight="bold" fill="#004d57" display="none" pointer-events="none"></text>');
  p.push('</svg>');

  /* Bottom HTML legend row */
  var lgKs=cm.keys.filter(function(k){return !(cm.keys.length===1&&k==='All');});
  var legParts=[];
  if(!multiY&&VAR_GBY.length>0){
    var _gbyLabels={'lot':'Lot','wafer':'Wafer','layout':'Layout','material':'Material'};
    var _gbyStr=VAR_GBY.map(function(f){return _gbyLabels[f]||f;}).join(' + ');
    legParts.push('<span style="display:inline-flex;align-items:center;gap:3px;background:#e8f4fd;border:1px solid #aed6f1;border-radius:10px;padding:1px 8px;font-size:11px;color:#1a6bb5;font-weight:700">&#9650; '+esc(_gbyStr)+'</span>');
  }
  lgKs.forEach(function(k){
    var col=cm.map[k];
    legParts.push('<span style="display:flex;align-items:center;gap:4px">'
      +'<svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4.5" fill="'+col+'" opacity="0.85"/></svg>'
      +'<span style="color:#2c3e50">'+esc(k)+'</span></span>');
  });
  if(_XY_TREND!=='none'){
    legParts.push('<span style="display:flex;align-items:center;gap:4px">'
      +'<svg width="22" height="10" viewBox="0 0 22 10"><line x1="0" y1="5" x2="22" y2="5" stroke="#555" stroke-width="2" stroke-dasharray="7,3"/></svg>'
      +'<span style="color:#555">'+(_XY_TREND==='theilsen'?'Theil-Sen':'OLS')+'</span></span>');
  }
  legParts.push('<span style="display:flex;align-items:center;gap:4px">'
    +'<svg width="14" height="14" viewBox="0 0 14 14"><polygon points="7,0 14,7 7,14 0,7" fill="#27ae60" stroke="#fff" stroke-width="1.2"/></svg>'
    +'<span style="color:#2c3e50">Group median</span></span>');
  var legHtml=(legParts.length
    ?'<div style="display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center;padding:5px 8px;font-size:12px;border-top:1px solid #e8e8e8;background:#fafafa">'+legParts.join('')+'</div>'
    :'');
  var gbyBadge=(!multiY&&VAR_GBY.length>0)
    ?('<div style="padding:3px 10px;background:#1f3a50;font-size:11px;color:#aed6f1;font-weight:600;text-align:left">'
      +'&#9650; Grouped by: <b style="color:#5dade2">'+esc(VAR_GBY.map(function(f){return({'lot':'Lot','wafer':'Wafer','layout':'Layout','material':'Material'})[f]||f;}).join(' + '))+'</b></div>')
    :'';
  cont.innerHTML='<div style="display:flex;flex-direction:column">'+p.join('')+legHtml+gbyBadge+'</div>';

  /* Attach vmin-style drag cursors (A=orange, B=teal, always visible) */
  var svgEl1=document.getElementById('xy-svg');
  if(svgEl1){
    var fmtXYx=function(v){return _fmt(v)+((!_XY_LOG_X&&xUnit==='% of tgt')?'%':'');};
    var fmtXYy=function(v){return _fmt(v);};
    _initDragCursorsXY(svgEl1,'xy',ML,MT,plotW,plotH,xlo,xhi,ylo,yhi,fmtXYx,fmtXYy);
  }
  /* Point hover tooltip — show nearest point Lot/Wafer/X/Y */
  var svgEl=document.getElementById('xy-svg');
  if(svgEl){
    function _evToSvg(e){
      var pt=svgEl.createSVGPoint();pt.x=e.clientX;pt.y=e.clientY;
      var ctm=svgEl.getScreenCTM();if(!ctm)return null;
      var sp=pt.matrixTransform(ctm.inverse());return{sx:sp.x,sy:sp.y};
    }
    svgEl.addEventListener('mousemove',function(e){
      var s=_evToSvg(e);if(!s)return;
      var inPlot=(s.sx>=ML&&s.sx<=ML+plotW&&s.sy>=MT&&s.sy<=MT+plotH);
      if(!inPlot){_getTT().style.display='none';return;}
      var ctm2=svgEl.getScreenCTM();
      var best=null,bestD=9999;
      pts.forEach(function(pt){
        var dotSvgX=xp(_lx(pt.x)),dotSvgY=yp(_ly(pt.y));
        var scr=svgEl.createSVGPoint();scr.x=dotSvgX;scr.y=dotSvgY;
        var scrPx=ctm2?scr.matrixTransform(ctm2):{x:dotSvgX,y:dotSvgY};
        var d=Math.sqrt((e.clientX-scrPx.x)*(e.clientX-scrPx.x)+(e.clientY-scrPx.y)*(e.clientY-scrPx.y));
        if(d<bestD){bestD=d;best=pt;}
      });
      var tt=_getTT();
      if(best&&bestD<=22){
        var yValStr=_fmt(best.y)+(best.yUnit?' '+best.yUnit:'');
        var yLblLine=multiY?('<b>'+esc(best.yParam)+'</b>: '+yValStr):('Y: '+yValStr+(yUnit1?' '+yUnit1:''));
        tt.innerHTML='<b>'+esc(best.lot)+' / '+esc(best.wafer)+'</b><br>X: '+_fmt(best.x)+(xUnit?' '+xUnit:'')+'<br>'+yLblLine;
        tt.style.left=(e.clientX+14)+'px';tt.style.top=(e.clientY-48)+'px';tt.style.display='block';
      }else{tt.style.display='none';}
    });
    svgEl.addEventListener('mouseleave',function(){_getTT().style.display='none';});
  }
}

/* ── Tab switch ─────────────────────────────────────────────────────────── */
function showTab(btn,id){
  document.querySelectorAll('.tab-panel').forEach(function(p){p.classList.remove('active');p.style.display='none';});
  document.querySelectorAll('.tab-btn').forEach(function(b){b.classList.remove('active');});
  var panel=document.getElementById(id);
  panel.style.display='';
  panel.classList.add('active');btn.classList.add('active');
  /* Render only if dirty (filter changed while tab was hidden) */
  if(id==='tab-var'  &&_DIRTY['tab-var'])  {drawAllCharts();_DIRTY['tab-var']=false;}
  if(id==='tab-pdly' &&_DIRTY['tab-pdly']) {buildPropDelayTab();_DIRTY['tab-pdly']=false;}
  if(id==='tab-xy'   &&_DIRTY['tab-xy'])   {buildXYTab();buildXY2Tab();buildFixedPanels();_DIRTY['tab-xy']=false;}
  if(id==='tab-pa'   &&_DIRTY['tab-pa'])   {buildParamAnalysisTab();_DIRTY['tab-pa']=false;}
}

/* ── Generic side-panel toggle/drag for Distribution and XY tabs ─────── */
function toggleSideP(p2Id,spId,btnId){
  var p2=document.getElementById(p2Id);if(!p2)return;
  var sp=document.getElementById(spId);
  var btn=document.getElementById(btnId);
  var hidden=p2.classList.toggle('p2-hidden');
  if(btn)btn.innerHTML=hidden?'&#9654;':'&#9664;';
  if(sp)sp.style.width=hidden?'14px':'5px';
}
function startSideP(ev,p2Id){
  ev.preventDefault();
  var p2=document.getElementById(p2Id);if(!p2)return;
  var startX=ev.clientX,startW=p2.offsetWidth;
  var sp=ev.currentTarget;sp.classList.add('dragging');
  function onMove(e){p2.style.width=Math.max(180,startW+e.clientX-startX)+'px';}
  function onUp(){sp.classList.remove('dragging');document.removeEventListener('mousemove',onMove);document.removeEventListener('mouseup',onUp);}
  document.addEventListener('mousemove',onMove);document.addEventListener('mouseup',onUp);
}

/* ── Panel 2 toggle (arrow button between P1 and P2) ───────────────────── */
function toggleP2(){
  var p2=document.getElementById('panel2');
  var sp=document.getElementById('sp12');
  var btn=document.getElementById('p2-toggle-btn');
  var hidden=p2.classList.toggle('p2-hidden');
  if(btn)btn.innerHTML=hidden?'&#9654;':'&#9664;';
  if(sp)sp.style.width=hidden?'14px':'5px';
}

/* ── Splitter drag between P2 and P3 ───────────────────────────────────── */
function startSplit23(ev){
  ev.preventDefault();
  var p2=document.getElementById('panel2');if(!p2)return;
  var startX=ev.clientX,startW=p2.offsetWidth;
  var sp=ev.currentTarget;sp.classList.add('dragging');
  function onMove(e){p2.style.width=Math.max(180,startW+e.clientX-startX)+'px';}
  function onUp(){sp.classList.remove('dragging');document.removeEventListener('mousemove',onMove);document.removeEventListener('mouseup',onUp);}
  document.addEventListener('mousemove',onMove);document.addEventListener('mouseup',onUp);
}

/* ── Panel 1 resize handle ──────────────────────────────────────────────── */
(function(){
  var p1=document.getElementById('panel1');if(!p1)return;
  var rz=document.getElementById('p1-resize');if(!rz)return;
  rz.addEventListener('mousedown',function(ev){
    ev.preventDefault();var startX=ev.clientX,startW=p1.offsetWidth;
    rz.classList.add('dragging');
    function onMove(e){p1.style.width=Math.max(180,startW+e.clientX-startX)+'px';}
    function onUp(){rz.classList.remove('dragging');document.removeEventListener('mousemove',onMove);document.removeEventListener('mouseup',onUp);}
    document.addEventListener('mousemove',onMove);document.addEventListener('mouseup',onUp);
  });
})();

/* ── Main rerender ──────────────────────────────────────────────────────── */
/* Dirty flags: set on every filter change; cleared when tab becomes visible */
var _DIRTY={'tab-var':true,'tab-pdly':true,'tab-xy':true,'tab-pa':true};
function rerender(){
  buildWfrList();buildParamTable();
  var ap=document.querySelector('.tab-panel.active');
  var aid=ap?ap.id:'tab-var';
  /* Mark all tabs dirty */
  Object.keys(_DIRTY).forEach(function(k){_DIRTY[k]=true;});
  /* Immediately render only the visible tab */
  if(aid==='tab-var')  {drawAllCharts();_DIRTY['tab-var']=false;}
  if(aid==='tab-pdly') {buildPropDelayTab();_DIRTY['tab-pdly']=false;}
  if(aid==='tab-xy')   {buildXYTab();buildXY2Tab();buildFixedPanels();_DIRTY['tab-xy']=false;}
  if(aid==='tab-pa')   {buildParamAnalysisTab();_DIRTY['tab-pa']=false;}
}
window.addEventListener('resize',function(){var ap=document.querySelector('.tab-panel.active');if(ap&&ap.id==='tab-var')drawAllCharts();});
"""

# Inline init — must run AFTER both _JS and _PA_JS are fully defined
_INIT_JS = """
/* Script is at end of body — DOM is ready; init directly */
buildWfrList();rerender();setTimeout(function(){if(!_DIRTY['tab-var'])return;drawAllCharts();_DIRTY['tab-var']=false;},80);
"""


# ---------------------------------------------------------------------------
# Parameter Analysis tab — JavaScript
# ---------------------------------------------------------------------------
_PA_JS = r"""
/* ── Parameter Analysis tab ─────────────────────────────────────────────── */
var _PA_ROWS=[];
var _PA_SORT_COL='pct_fail';
var _PA_SORT_ASC=false;
var _PA_SRCH='';
var _PA_STATUS_FILTER=new Set(['FAIL','MARGINAL','PASS','NO SPEC']);
var _PA_FAIL_THR=5.0;

function _paCompute(){
  var ak=activeKeys();
  var paramVals={};
  PCM_ROWS.forEach(function(r){
    if(!ak.has(_rKey(r)))return;
    var p=r.param;
    if(!paramVals[p])paramVals[p]=[];
    (r.die_values||[]).forEach(function(v){if(v!=null&&isFinite(v))paramVals[p].push(v);});
  });
  var rows=[];
  Object.keys(paramVals).forEach(function(param){
    var vals=paramVals[param];
    if(!vals.length)return;
    var meta=PCM_PARAM_META[param]||{};
    /* Respect group visibility toggles from the tab bar */
    if(meta.group && _GRP_VIS[meta.group]===false)return;
    var lsl=meta.lsl,usl=meta.usl;
    var n=vals.length;
    var sorted=vals.slice().sort(function(a,b){return a-b;});
    var med=_med(vals);
    var p1=sorted[Math.floor(sorted.length*0.01)];
    var p99=sorted[Math.min(sorted.length-1,Math.ceil(sorted.length*0.99))];
    var hasSpec=(lsl!=null||usl!=null);
    var n_lo=lsl!=null?vals.filter(function(v){return v<lsl;}).length:0;
    var n_hi=usl!=null?vals.filter(function(v){return v>usl;}).length:0;
    var pct_lo=lsl!=null?(n_lo/n*100):null;
    var pct_hi=usl!=null?(n_hi/n*100):null;
    var pct_fail=hasSpec?((n_lo+n_hi)/n*100):null;
    /* σ-based outliers (always computed from converted display values) */
    var dvals=_toDisplayVals(param,vals);
    var dmed=_med(dvals),dsd=_std(dvals);
    var pct_out3=null,pct_out6=null;
    if(dvals.length&&dsd>0){
      var nd=dvals.length;
      var s3lo=dmed-3*dsd,s3hi=dmed+3*dsd,s6lo=dmed-6*dsd,s6hi=dmed+6*dsd;
      var n3=dvals.filter(function(v){return v<s3lo||v>s3hi;}).length;
      var n6=dvals.filter(function(v){return v<s6lo||v>s6hi;}).length;
      pct_out3=n3/nd*100;
      pct_out6=n6/nd*100;
    }
    var status;
    if(!hasSpec)status='NO SPEC';
    else if(pct_fail>=_PA_FAIL_THR)status='FAIL';
    else if(pct_fail>0)status='MARGINAL';
    else status='PASS';
    rows.push({param:param,group:meta.group||'',name:meta.name||'',unit:meta.unit||'',
      lsl:lsl,target:meta.target,usl:usl,n:n,median:med,p1:p1,p99:p99,
      pct_lo:pct_lo,pct_hi:pct_hi,pct_fail:pct_fail,pct_out3:pct_out3,pct_out6:pct_out6,status:status});
  });
  return rows;
}

var _paStatusOrd={FAIL:0,MARGINAL:1,PASS:2,'NO SPEC':3};
function _paSortRows(rows){
  rows=rows.slice();
  var col=_PA_SORT_COL,asc=_PA_SORT_ASC;
  rows.sort(function(a,b){
    var av=a[col],bv=b[col];
    if(col==='status'){av=_paStatusOrd[av]||9;bv=_paStatusOrd[bv]||9;}
    if(av==null&&bv==null)return 0;
    if(av==null)return 1;if(bv==null)return -1;
    var cmp=av<bv?-1:av>bv?1:0;
    return asc?cmp:-cmp;
  });
  return rows;
}
function paSort(col){
  if(_PA_SORT_COL===col)_PA_SORT_ASC=!_PA_SORT_ASC;
  else{_PA_SORT_COL=col;_PA_SORT_ASC=(col==='param'||col==='group'||col==='unit'||col==='name');}
  buildParamAnalysisTab();
}
function _paBuildTable(rows){
  var cols=['group','param','name','unit','lsl','target','usl','n','median','p1','p99','pct_lo','pct_hi','pct_fail','pct_out3','pct_out6','status'];
  var hdrs=['Group','Parameter','Device Name','Unit','Spec Lo','Target','Spec Hi','N','Median','P1','P99','%Fail Lo','%Fail Hi','%Fail','%Out 3σ','%Out 6σ','Status'];
  var thead='<tr>';
  cols.forEach(function(c,i){
    var align=(i>3&&c!=='status')?'right':'left';
    var arr=(_PA_SORT_COL===c)?(_PA_SORT_ASC?' &#9650;':' &#9660;'):' <span style="opacity:.4">&#8597;</span>';
    thead+='<th onclick="paSort(\''+c+'\')" style="background:#2c3e50;color:#ecf0f1;padding:5px 8px;text-align:'+align+';white-space:nowrap;cursor:pointer;position:sticky;top:0;z-index:1;user-select:none">'+hdrs[i]+arr+'</th>';
  });
  thead+='</tr>';
  var tbody='';
  var statusBg={FAIL:'#3a1a1a',MARGINAL:'#383820',PASS:'#1e3a2f','NO SPEC':'#232340'};
  var statusFg={FAIL:'#f38ba8',MARGINAL:'#f9e2af',PASS:'#a6e3a1','NO SPEC':'#a6adc8'};
  rows.forEach(function(r){
    var bg=statusBg[r.status]||'#1e1e2e';
    var fg=statusFg[r.status]||'#cdd6f4';
    tbody+='<tr style="background:'+bg+'">';
    function _td(v,align,color){return '<td style="padding:4px 8px;text-align:'+(align||'left')+';color:'+(color||fg)+';white-space:nowrap">'+v+'</td>';}
    tbody+=_td(esc(r.group));
    tbody+=_td('<b>'+esc(r.param)+'</b>');
    tbody+=_td(esc(r.name.length>32?r.name.slice(0,31)+'\u2026':r.name));
    tbody+=_td(esc(r.unit));
    tbody+=_td(r.lsl!=null?_fmt(r.lsl):'\u2014','right','#f38ba8');
    tbody+=_td(r.target!=null?_fmt(r.target):'\u2014','right',fg);
    tbody+=_td(r.usl!=null?_fmt(r.usl):'\u2014','right','#89b4fa');
    tbody+=_td(r.n,'right');
    tbody+=_td(_fmt(r.median),'right');
    tbody+=_td(_fmt(r.p1),'right');
    tbody+=_td(_fmt(r.p99),'right');
    tbody+=_td(r.pct_lo!=null?r.pct_lo.toFixed(1)+'%':'\u2014','right','#f38ba8');
    tbody+=_td(r.pct_hi!=null?r.pct_hi.toFixed(1)+'%':'\u2014','right','#89b4fa');
    var pfv=r.pct_fail!=null?r.pct_fail.toFixed(1)+'%':'\u2014';
    tbody+='<td style="padding:4px 8px;text-align:right;font-weight:bold;color:'+fg+'">'+pfv+'</td>';
    var o3fg=r.pct_out3>5?'#f38ba8':r.pct_out3>0?'#f9e2af':fg;
    tbody+=_td(r.pct_out3!=null?r.pct_out3.toFixed(1)+'%':'\u2014','right',o3fg);
    var o6fg=r.pct_out6>0?'#89b4fa':fg;
    tbody+=_td(r.pct_out6!=null?r.pct_out6.toFixed(1)+'%':'\u2014','right',o6fg);
    tbody+='<td style="padding:4px 8px"><span style="display:inline-block;padding:1px 8px;border-radius:3px;font-size:10px;font-weight:bold;background:'+fg+';color:'+bg+'">'+esc(r.status)+'</span></td>';
    tbody+='</tr>';
  });
  return '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:11px"><thead>'+thead+'</thead><tbody>'+tbody+'</tbody></table></div>';
}
function _paBuildGroupCharts(allRows){
  var html='';
  var statusFg={FAIL:'#f38ba8',MARGINAL:'#f9e2af',PASS:'#a6e3a1','NO SPEC':'#5d6d7e'};
  PCM_GROUPS.forEach(function(grp){
    if(_GRP_VIS[grp]===false)return;
    var grpRows=allRows.filter(function(r){return r.group===grp;});
    if(!grpRows.length)return;
    var sorted=grpRows.slice().sort(function(a,b){return(b.pct_fail||0)-(a.pct_fail||0);});
    var n=sorted.length;
    var W=Math.max(400,n*30+130),H=230,ML=46,MR=14,MT=30,MB=90;
    var plotW=W-ML-MR,plotH=H-MT-MB;
    var rawMax=sorted.reduce(function(m,r){return Math.max(m,r.pct_fail||0);},0);
    var maxPct=Math.max(10,Math.ceil(rawMax*1.35/5)*5);
    if(maxPct<_PA_FAIL_THR*2)maxPct=Math.ceil(_PA_FAIL_THR*2/5)*5;
    var barW=Math.max(3,(plotW/n)-2);
    function xp(i){return ML+(i+0.5)*(plotW/n);}
    function yp(v){return MT+(1-Math.min(v,maxPct)/maxPct)*plotH;}
    var p=[];
    p.push('<svg width="100%" height="'+H+'" viewBox="0 0 '+W+' '+H+'" style="display:block">');
    p.push('<rect width="'+W+'" height="'+H+'" fill="#16213e"/>');
    p.push('<rect x="'+ML+'" y="'+MT+'" width="'+plotW+'" height="'+plotH+'" fill="#0d1b2e"/>');
    for(var yi=0;yi<=4;yi++){
      var yv=maxPct*yi/4;
      var ypv=yp(yv).toFixed(1);
      p.push('<line x1="'+ML+'" y1="'+ypv+'" x2="'+(ML+plotW)+'" y2="'+ypv+'" stroke="rgba(255,255,255,0.07)" stroke-width="0.7"/>');
      p.push('<text x="'+(ML-3)+'" y="'+ypv+'" text-anchor="end" dominant-baseline="middle" font-size="10" fill="#7f8c8d">'+yv.toFixed(0)+'%</text>');
    }
    var ftY=yp(_PA_FAIL_THR).toFixed(1);
    p.push('<line x1="'+ML+'" y1="'+ftY+'" x2="'+(ML+plotW)+'" y2="'+ftY+'" stroke="#e74c3c" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.85"/>');
    p.push('<text x="'+(ML+3)+'" y="'+(parseFloat(ftY)-4)+'" font-size="9" fill="#e74c3c">FAIL \u2265'+_PA_FAIL_THR+'%</text>');
    sorted.forEach(function(r,i){
      var col=statusFg[r.status]||'#7f8c8d';
      var pct=r.pct_fail!=null?r.pct_fail:0;
      if((r.status==='PASS'||r.status==='NO SPEC')&&pct<=0)pct=0;
      var bh=Math.max(pct>0?2:0,(pct/maxPct)*plotH).toFixed(1);
      var by=(MT+plotH-parseFloat(bh)).toFixed(1);
      var bx=(xp(i)-barW/2).toFixed(1);
      if(parseFloat(bh)>0)p.push('<rect x="'+bx+'" y="'+by+'" width="'+barW.toFixed(1)+'" height="'+bh+'" fill="'+col+'" opacity="0.82" rx="1"/>');
      if(r.pct_fail>0&&(r.status==='FAIL'||r.status==='MARGINAL')){
        p.push('<text x="'+xp(i).toFixed(1)+'" y="'+(parseFloat(by)-2)+'" text-anchor="middle" font-size="8" fill="'+col+'">'+r.pct_fail.toFixed(1)+'%</text>');
      }
    });
    sorted.forEach(function(r,i){
      var col=statusFg[r.status]||'#7f8c8d';
      var lbl=r.param.length>14?r.param.slice(0,13)+'\u2026':r.param;
      p.push('<text transform="translate('+xp(i).toFixed(1)+','+(MT+plotH+5)+') rotate(-45)" text-anchor="end" font-size="9" fill="'+col+'">'+esc(lbl)+'</text>');
    });
    p.push('</svg>');
    html+='<div style="background:#1e1e2e;border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,.4);margin-bottom:10px;overflow:hidden">'
      +'<div style="padding:4px 10px;background:#2a2a3e;color:#cdd6f4;font-size:11px;font-weight:bold;display:flex;align-items:center;gap:8px">'
      +esc(grp)
      +'<span style="font-weight:normal;color:#7f8c8d;font-size:10px">('+sorted.length+' params \u2014 sorted by %fail)</span>'
      +'</div>'
      +p.join('')+'</div>';
  });
  return html||'<div style="color:#7f8c8d;padding:16px;font-style:italic">No parameter groups found.</div>';
}
function buildParamAnalysisTab(){
  var cont=document.getElementById('pa-cont');if(!cont)return;
  _PA_ROWS=_paCompute();
  var rows=_paSortRows(_PA_ROWS);
  if(_PA_SRCH){
    var q=_PA_SRCH.toLowerCase();
    rows=rows.filter(function(r){return r.param.toLowerCase().indexOf(q)>=0||r.name.toLowerCase().indexOf(q)>=0||r.group.toLowerCase().indexOf(q)>=0;});
  }
  rows=rows.filter(function(r){return _PA_STATUS_FILTER.has(r.status);});
  var allRows=_PA_ROWS;
  var counts={FAIL:0,MARGINAL:0,PASS:0,'NO SPEC':0};
  allRows.forEach(function(r){if(counts[r.status]!=null)counts[r.status]++;});
  var badges={'FAIL':'pa-cnt-fail','MARGINAL':'pa-cnt-marg','PASS':'pa-cnt-pass','NO SPEC':'pa-cnt-nospec'};
  Object.keys(badges).forEach(function(s){var el=document.getElementById(badges[s]);if(el)el.textContent=counts[s];});
  var tblHtml=rows.length?_paBuildTable(rows):'<div style="padding:20px;color:#7f8c8d;font-style:italic">No parameters match the current filter.</div>';
  var chartsHtml=_paBuildGroupCharts(allRows);
  cont.innerHTML=
    '<div style="margin-bottom:14px">'+tblHtml+'</div>'
    +'<div style="font-size:12px;font-weight:bold;color:#89b4fa;margin:12px 0 6px;border-bottom:1px solid #313244;padding-bottom:4px">&#9632; Failure Rate by Group (sorted worst\u2192best)</div>'
    +chartsHtml;
}
function togglePaStatus(status,el){
  if(_PA_STATUS_FILTER.has(status))_PA_STATUS_FILTER.delete(status);
  else _PA_STATUS_FILTER.add(status);
  if(el){el.style.opacity=_PA_STATUS_FILTER.has(status)?'1':'0.30';}
  buildParamAnalysisTab();
}
function downloadParamAnalysisCSV(){
  if(!_PA_ROWS.length)return;
  var cols=['Group','Parameter','Device Name','Unit','Spec Lo','Target','Spec Hi','N','Median','P1','P99','%Fail Lo','%Fail Hi','%Fail','%Out 3σ','%Out 6σ','Status'];
  var lines=[cols.join(',')];
  _PA_ROWS.forEach(function(r){
    var row=[r.group,r.param,r.name,r.unit,
      r.lsl!=null?r.lsl:'',r.target!=null?r.target:'',r.usl!=null?r.usl:'',
      r.n,_fmt(r.median),_fmt(r.p1),_fmt(r.p99),
      r.pct_lo!=null?r.pct_lo.toFixed(2):'',r.pct_hi!=null?r.pct_hi.toFixed(2):'',
      r.pct_fail!=null?r.pct_fail.toFixed(2):'',
      r.pct_out3!=null?r.pct_out3.toFixed(2):'',r.pct_out6!=null?r.pct_out6.toFixed(2):'',
      r.status];
    lines.push(row.map(_csvQ).join(','));
  });
  _csvBlob(lines,'pcm_param_analysis_'+_csvTs()+'.csv');
}
"""


# ---------------------------------------------------------------------------
# Python helpers
# ---------------------------------------------------------------------------

def _match_params(cols: List[str], patterns: List[str]) -> List[str]:
    matched, seen = [], set()
    for pat in patterns:
        for col in cols:
            if col not in seen and fnmatch.fnmatch(col, pat):
                matched.append(col)
                seen.add(col)
    return matched


# ---------------------------------------------------------------------------
# Sort-column helpers
# ---------------------------------------------------------------------------

def _sort_col_friendly(col: str, gname: str) -> str:
    """Return a short human-readable name for a UPM / SICC / CDYN column."""
    import re as _re
    if gname == "UPM":
        m = _re.search(r'_(\d{4})_MED', col)
        if m:
            mv = int(m.group(1))
            return f"UPM {mv}mV"
    if gname == "SICC":
        _dom = {"VCCATOM": "Atom", "VCCCORE": "Core", "VCCCCF": "CCF"}
        for k, v in _dom.items():
            if k.upper() in col.upper():
                cm = _re.search(r'(\d+)P(\d+)A', col, _re.I)
                if cm:
                    return f"SICC {v} {cm.group(1)}.{cm.group(2)}A"
                return f"SICC {v}"
    if gname == "CDYN":
        _dom = {"VCCATOM": "Atom", "VCCCORE": "Core", "VCCCCF": "CCF"}
        for k, v in _dom.items():
            if k.upper() in col.upper():
                cm = _re.search(r'_(\d{3,4})MV', col, _re.I)
                if cm:
                    return f"CDYN {v} {cm.group(1)}mV"
                return f"CDYN {v}"
    # Fallback: last 30 chars of column name
    return col[-30:] if len(col) > 30 else col


def _safe_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _fmt_val(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, float):
        return float(f'{v:.10g}')
    return v


_PCM_ID_COLS = {
    "Technology", "Layout", "Lot", "Wafer", "TestProgram", "TestProgramVersion",
    "Fab", "Step", "Equipment", "EquipmentType", "TestDateTime", "TestDate",
    "TimeLoaded", "WaferResultID", "Site", "LayoutX", "LayoutY", "Map", "MapID",
    "ReticleShotRadius", "Material",
}

# Columns that carry material identity info (shown in filter panel)
_MAT_COLS = ["Material"]

_MAX_DIE_VALUES = 500  # store up to 500 per-site values (covers 393-reticle IDW maps)


# ---------------------------------------------------------------------------
# Data computation
# ---------------------------------------------------------------------------

def _compute_rows(
    df: pd.DataFrame,
    groups: List[dict],
    pcm_cols: List[str],
    spec_lookup,
    wfr_program: dict | None = None,
) -> Tuple[List[dict], dict, dict, List[str]]:
    pcm_rows: List[dict] = []
    pcm_param_meta: dict = {}
    pcm_group_params: dict = {}
    pcm_groups: List[str] = []

    rng = np.random.default_rng(42)

    for g in groups:
        gname = g.get("name", "")
        patterns = g.get("patterns", [])
        params = _match_params(pcm_cols, patterns)
        if not params:
            continue
        pcm_groups.append(gname)
        pcm_group_params[gname] = params

        for param in params:
            if param not in df.columns:
                continue
            lsl = usl = target = None
            unit = name = ""
            if spec_lookup and param in spec_lookup:
                _row = spec_lookup[param]
                _sl, _sh, _tgt = _row[0], _row[1], _row[2]
                _unit = _row[3] if len(_row) > 3 else ""
                _name = _row[4] if len(_row) > 4 else ""
                lsl    = None if (isinstance(_sl,  float) and math.isnan(_sl))  else float(_sl)
                usl    = None if (isinstance(_sh,  float) and math.isnan(_sh))  else float(_sh)
                target = None if (isinstance(_tgt, float) and math.isnan(_tgt)) else float(_tgt)
                unit   = _unit or ""
                name   = _name or ""
            pcm_param_meta[param] = {"group": gname, "lsl": lsl, "usl": usl,
                                     "unit": unit, "target": target, "name": name}

            grp_cols = ["Lot", "Wafer"]
            if "Program" in df.columns:
                grp_cols.append("Program")
            if "Layout" in df.columns:
                grp_cols.append("Layout")
            for mc in _MAT_COLS:
                if mc in df.columns:
                    grp_cols.append(mc)

            for keys, sub in df.groupby(grp_cols):
                if isinstance(keys, str):
                    keys = (keys,)
                lot      = str(keys[0]) if len(keys) > 0 else ""
                wafer    = str(keys[1]) if len(keys) > 1 else ""
                sort_wafer = (
                    str(sub["sort_wafer"].dropna().iloc[0])
                    if "sort_wafer" in sub.columns and sub["sort_wafer"].notna().any()
                    else wafer
                )
                _off = 2
                program  = str(keys[_off]) if ("Program" in df.columns and len(keys) > _off) else ""
                if "Program" in df.columns: _off += 1
                layout   = str(keys[_off]) if ("Layout" in df.columns and len(keys) > _off) else ""
                if "Layout" in df.columns: _off += 1
                material = str(keys[_off]) if len(keys) > _off else ""

                vals = pd.to_numeric(sub[param], errors="coerce").dropna().values
                if not len(vals):
                    continue

                med = float(np.nanmedian(vals))
                std = float(np.nanstd(vals, ddof=1)) if len(vals) > 1 else 0.0
                cv  = abs(std / med * 100) if med != 0 else None

                if len(vals) > _MAX_DIE_VALUES:
                    # Truncate preserving order rather than random-sampling so
                    # that die_values[i] aligns across params (same row order).
                    sample = vals[:_MAX_DIE_VALUES]
                else:
                    sample = vals
                die_values = [float(f'{float(v):.10g}') for v in sample]

                pcm_rows.append({
                    "lot":        lot,
                    "wafer":      wafer,
                    "sort_wafer": sort_wafer,
                    "layout":     layout,
                    "material":   material,
                    "program":    program or (wfr_program or {}).get((lot, wafer), ""),
                    "group":      gname,
                    "param":      param,
                    "n":          int(len(vals)),
                    "median":     _fmt_val(med),
                    "std":        _fmt_val(std),
                    "cv":         round(cv, 2) if cv is not None else None,
                    "min_val":    _fmt_val(float(vals.min())),
                    "max_val":    _fmt_val(float(vals.max())),
                    "die_values": die_values,
                })

    return pcm_rows, pcm_param_meta, pcm_group_params, pcm_groups


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_html(
    df: pd.DataFrame,
    product_setup: dict,
    output_path: str,
    spec_lookup=None,
    sort_groups: "dict | None" = None,
    default_groups: "list | None" = None,
    pcm_panels: "dict | None" = None,
) -> str:
    """Generate the PCM dashboard HTML. Returns output_path.

    sort_groups (optional) — pre-computed dict from product_config_json:
        {
            "UPM":  {"cols": [...], "labels": {col: label, ...}},
            "SICC": {"cols": [...], "labels": {...}},
            "CDYN": {"cols": [...], "labels": {...}},
        }
    When provided, these replace the regex-based auto-detection of sort columns.
    """
    title    = product_setup.get("title", "PCM / ETest Dashboard")
    subtitle = product_setup.get("subtitle", "") or ""
    groups   = product_setup.get("groups", [])

    for col in ("Lot", "Wafer"):
        if col not in df.columns:
            df = df.copy(); df[col] = ""
    for mc in _MAT_COLS:
        if mc not in df.columns:
            df = df.copy(); df[mc] = ""
        df[mc] = df[mc].fillna("").astype(str)
    df["Lot"]   = df["Lot"].fillna("").astype(str)
    df["Wafer"] = df["Wafer"].fillna("").astype(str)

    pcm_cols = [
        c for c in df.columns
        if c not in _PCM_ID_COLS and pd.api.types.is_numeric_dtype(df[c])
    ]

    # Build lot+wafer → program mapping from "Program" or "TestProgram" column
    _prog_src = next((c for c in ["Program", "TestProgram"] if c in df.columns), None)
    wfr_program: dict = {}
    if _prog_src:
        for (lot, wafer), _g in df.groupby(["Lot", "Wafer"]):
            _mode = _g[_prog_src].dropna().mode()
            wfr_program[(str(lot), str(wafer))] = str(_mode.iloc[0]) if len(_mode) else ""

    pcm_rows, pcm_param_meta, pcm_group_params, pcm_groups = _compute_rows(
        df, groups, pcm_cols, spec_lookup, wfr_program
    )

    # ── Sort group definitions (UPM / SICC / CDYN) ──────────────────────────
    # Only show sort params when explicitly defined in product_config_json.
    # No fallback regex — avoids dumping raw column names for every column
    # that happens to contain "SICC" or "CDYN".
    _sort_prepend: list = []  # list of (gname, [cols], {col: label})
    if sort_groups:
        for _sname in ("UPM", "SICC", "CDYN"):
            _sg = sort_groups.get(_sname)
            if not _sg:
                continue
            _cols_in_df = [c for c in _sg.get("cols", [])
                           if c in pcm_cols and c not in pcm_param_meta]
            if _cols_in_df:
                _sort_prepend.append((_sname, _cols_in_df, _sg.get("labels", {})))

    if _sort_prepend:
        _sort_groups_dicts = [{"name": g, "patterns": cs} for g, cs, _ in _sort_prepend]
        _s_rows, _s_meta, _s_grp_params, _s_groups = _compute_rows(
            df, _sort_groups_dicts, pcm_cols, None, wfr_program
        )
        # Apply display names + USL targets for SICC/CDYN/UPM columns
        for _col, _m in _s_meta.items():
            _gname = _m.get("group", "")
            _labels = next((lbl for g, cs, lbl in _sort_prepend if g == _gname), {})
            _m["name"] = _labels.get(_col) or _sort_col_friendly(_col, _gname)
            _m["is_sort"] = True  # suppress bracket wrapping in JS for UPM/SICC/CDYN
            # Inject USL from product-config targets (sicc_targets / cdyn_targets)
            _sg_targets = (sort_groups or {}).get(_gname, {}).get("targets", {})
            if _col in _sg_targets and _m.get("usl") is None:
                _m["usl"] = float(_sg_targets[_col])
        # Prepend sort rows + groups (UPM first)
        pcm_rows = _s_rows + pcm_rows
        pcm_param_meta.update(_s_meta)
        for _sname in reversed(_s_groups):
            pcm_groups.insert(0, _sname)
            pcm_group_params[_sname] = _s_grp_params.get(_sname, [])

    # ── Filter to default_groups only (keeps data small) ────────────────────
    # Sort groups (UPM/SICC/CDYN) are always kept; filter only applies to PCM groups.
    if default_groups:
        _keep = set(default_groups)
        _sort_group_names = {g for g, _, _ in _sort_prepend} if _sort_prepend else set()
        pcm_groups = [g for g in pcm_groups if g in _keep or g in _sort_group_names]
        pcm_group_params = {g: v for g, v in pcm_group_params.items() if g in _keep or g in _sort_group_names}
        _kept_params = {p for params in pcm_group_params.values() for p in params}
        pcm_param_meta = {p: v for p, v in pcm_param_meta.items() if p in _kept_params}
        pcm_rows = [r for r in pcm_rows if r.get("param") in _kept_params]

    # The RO/Propagation-Delay group is shown exclusively in the RO Distribution tab
    pcm_pdly_grp = next((g for g in pcm_groups if 'propagation' in g.lower() or g.lower().startswith('ro')), 'Propagation Delay')

    # ── Resolve pcm_panels wildcards into concrete param names ───────────────
    # Priority already determined by caller; here we just expand wildcards.
    import fnmatch as _fnm_r
    _all_param_keys = list(pcm_param_meta.keys())

    def _resolve_first(pattern: str) -> "str | None":
        """Return first param key matching pattern (fnmatch, case-insensitive).
        If no wildcard characters, prefer exact match then case-insensitive match."""
        if '*' not in pattern and '?' not in pattern:
            if pattern in pcm_param_meta:
                return pattern
            for _k in _all_param_keys:
                if _k.lower() == pattern.lower():
                    return _k
            return None
        for _k in _all_param_keys:
            if _fnm_r.fnmatch(_k.lower(), pattern.lower()):
                return _k
        return None

    def _resolve_all(pattern: str) -> list:
        """Return all param keys matching pattern (fnmatch, case-insensitive).
        If no wildcard characters, treat as single exact match."""
        if '*' not in pattern and '?' not in pattern:
            r = _resolve_first(pattern)
            return [r] if r else []
        return [_k for _k in _all_param_keys if _fnm_r.fnmatch(_k.lower(), pattern.lower())]

    _dist_panels_resolved: list = []
    _xy_panels_resolved: list = []

    if pcm_panels:
        for _dp in pcm_panels.get("distribution", []):
            _rp = [r for p in _dp.get("params", []) for r in [_resolve_first(p)] if r]
            if _rp:
                _dist_panels_resolved.append({"label": _dp.get("label", ""), "params": _rp})

        for _xp in pcm_panels.get("xy", []):
            def _half(h: dict) -> dict:
                _rx = _resolve_first(h.get("x", "")) or ""
                _ry: list = []
                _seen: set = set()
                for _ypat in h.get("ys", []):
                    for _m in _resolve_all(_ypat):
                        if _m not in _seen:
                            _seen.add(_m); _ry.append(_m)
                return {"x": _rx, "ys": _ry}
            _xy_panels_resolved.append({
                "label": _xp.get("label", ""),
                "a": _half(_xp.get("a", {})),
                "b": _half(_xp.get("b", {})),
            })

    # ── Pre-build Distribution tab panels HTML (dynamic N-panel layout) ─────
    _custom_dn = len(_dist_panels_resolved) + 1
    _dist_panels_html = ''
    for _i, _dp in enumerate(_dist_panels_resolved):
        _pn = _i + 1
        _lbl = (_dp.get('label') or f'Panel {_pn}').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        _dist_panels_html += (
            f'<div style="flex-shrink:0;border-bottom:3px solid #bcd">'
            f'<div style="background:#1a6e2b;border-bottom:1px solid #bcd;padding:4px 10px;'
            f'display:flex;align-items:center;gap:8px;cursor:pointer" onclick="togglePdlyP({_pn})">'
            f'<button id="pdlyp{_pn}-toggle" style="border:none;background:none;cursor:pointer;'
            f'font-size:15px;color:#fff;padding:0 4px;line-height:1" title="Collapse/Expand">&#9660;</button>'
            f'<span style="font-size:15px;font-weight:bold;color:#fff">&#9673; Panel {_pn} &mdash; {_lbl}</span>'
            f'</div>'
            f'<div id="pdlyp{_pn}-body"></div>'
            f'</div>\n'
        )
    # Always add the "Custom" panel at the end
    _dist_panels_html += (
        f'<div style="flex-shrink:0">'
        f'<div style="background:#1a6e2b;border-bottom:1px solid #bcd;padding:4px 10px;'
        f'display:flex;align-items:center;gap:8px;cursor:pointer" onclick="togglePdlyP({_custom_dn})">'
        f'<button id="pdlyp{_custom_dn}-toggle" style="border:none;background:none;cursor:pointer;'
        f'font-size:15px;color:#fff;padding:0 4px;line-height:1" title="Collapse/Expand">&#9660;</button>'
        f'<span style="font-size:15px;font-weight:bold;color:#fff">&#9673; Panel {_custom_dn} &mdash; Custom</span>'
        f'</div>'
        f'<div id="pdlyp{_custom_dn}-body"></div>'
        f'</div>\n'
    )

    # ── Pre-build XY tab panels HTML (dynamic N-panel layout) ───────────────
    _xy_panels_html = ''
    for _i, _p in enumerate(_xy_panels_resolved):
        _lbl = (_p.get('label') or f'Panel {_i + 1}').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        _border = ';border-bottom:3px solid #bcd' if _i < len(_xy_panels_resolved) - 1 else ''
        _xy_panels_html += (
            f'<div style="flex-shrink:0{_border}">'
            f'<div style="background:#1a6e2b;border-bottom:1px solid #bcd;padding:4px 10px;'
            f'display:flex;align-items:center;gap:8px;cursor:pointer" onclick="toggleFp({_i})">'
            f'<button id="fp{_i}-toggle" style="border:none;background:none;cursor:pointer;'
            f'font-size:15px;color:#fff;padding:0 4px;line-height:1" title="Collapse/Expand">&#9660;</button>'
            f'<span style="font-size:15px;font-weight:bold;color:#fff">'
            f'&#9673; Panel {_i + 1} &mdash; {_lbl}</span>'
            f'</div>'
            f'<div id="fp{_i}-body" style="display:flex;flex-direction:row;min-height:0">'
            f'<div id="fp{_i}a-wrap" style="flex:1;border-right:2px solid #dde;min-width:0;'
            f'display:flex;flex-direction:column"></div>'
            f'<div id="fp{_i}b-wrap" style="flex:1;min-width:0;display:flex;flex-direction:column"></div>'
            f'</div>'
            f'</div>\n'
        )
    if not _xy_panels_html:
        _xy_panels_html = (
            '<div style="padding:24px;color:#888;text-align:center;font-style:italic">'
            'No XY panels configured &mdash; add an <b>xy</b> section to your panel setup JSON.'
            '</div>\n'
        )

    lots    = sorted(df["Lot"].unique())
    wafers  = sorted(df["Wafer"].unique())
    n_sites = len(df)
    ib_parts = [
        f"<b>Lots ({len(lots)}):</b> {', '.join(lots[:5])}{'  +'+str(len(lots)-5)+' more' if len(lots)>5 else ''}",
        f"<b>Wafers ({len(wafers)}):</b> {', '.join(wafers[:6])}{'  +'+str(len(wafers)-6)+' more' if len(wafers)>6 else ''}",
        f"<b>Sites:</b> {n_sites:,}",
    ]
    for mc in _MAT_COLS:
        if mc in df.columns:
            uniq = sorted(v for v in df[mc].unique() if v and v != "nan")
            if uniq:
                ib_parts.append(f"<b>{mc} ({len(uniq)}):</b> {', '.join(uniq[:3])}")
    info_bar = (
        '<div class="info-bar">'
        + '<span class="ib-sep"> | </span>'.join(ib_parts)
        + '</div>'
    )

    # Group toggle buttons (shown in tab bar)
    # Sort groups (UPM/SICC/CDYN) are always visible by default
    _sort_group_names_for_vis = [g for g, _, _ in _sort_prepend] if _sort_prepend else []
    _default_grp_set = set(default_groups) | set(_sort_group_names_for_vis) if default_groups else None
    # Only apply defaults if at least one actual group matches; otherwise show all
    if _default_grp_set is not None and not any(g in _default_grp_set for g in pcm_groups):
        _default_grp_set = None
    grp_btns = ""
    for g in pcm_groups:
        ge = g.replace("'", "\\'")
        n  = len(pcm_group_params.get(g, []))
        _off = (_default_grp_set is not None and g not in _default_grp_set)
        _cls = 'wfr-btn grp-off' if _off else 'wfr-btn'
        grp_btns += (
            f'<button class="{_cls}" onclick="toggleGroup(this,\'{ge}\')" '
            f'style="display:inline-flex;flex-direction:column;align-items:center;'
            f'padding:2px 10px;line-height:1.2">'
            f'<span style="font-size:9px;color:#95a5a6;font-weight:normal">[{n} params]</span>'
            f'<span>{g}</span></button> '
        )

    # Inline group-by snippet (reused in each tab toolbar)
    _GBY = (
        '<span style="width:1px;background:#4a6278;align-self:stretch;margin:0 4px"></span>'
        '<b style="color:#f1c40f;margin-right:4px;font-size:12px">Group by:</b>'
        "<label style='cursor:pointer;font-size:12px;color:#ecf0f1'><input type='checkbox' class='vgb-cb' value='none' onchange=\"toggleGby('none')\" checked> None</label>"
        "<label style='cursor:pointer;font-size:12px;color:#ecf0f1'><input type='checkbox' class='vgb-cb' value='lot' onchange=\"toggleGby('lot')\"> Lot</label>"
        "<label style='cursor:pointer;font-size:12px;color:#ecf0f1'><input type='checkbox' class='vgb-cb' value='wafer' onchange=\"toggleGby('wafer')\"> Wafer</label>"
        "<label style='cursor:pointer;font-size:12px;color:#ecf0f1'><input type='checkbox' class='vgb-cb' value='layout' onchange=\"toggleGby('layout')\"> Layout</label>"
        "<label style='cursor:pointer;font-size:12px;color:#ecf0f1'><input type='checkbox' class='vgb-cb' value='material' onchange=\"toggleGby('material')\"> Material</label>"
    )

    # Group-by now embedded in each tab; bottom bar removed
    gby_html = ''

    # Per-group SVG chart cards (panel 3 content)
    grp_cards = ""
    banner_cols = ['#1a5276','#117a65','#6e2f8a','#7d4e00','#922b21','#1a6e2b','#1a3a72','#7d4500']
    for gi, g in enumerate(pcm_groups):
        gid = re.sub(r'[^a-zA-Z0-9]', '_', g)
        ge  = g.replace("'", "\\'")
        col = banner_cols[gi % len(banner_cols)]
        n   = len(pcm_group_params.get(g, []))
        _card_off = False  # data is already filtered; all rendered cards are visible
        grp_cards += (
            f'<div class="grp-card" id="card-grp-{gid}">'
            f'<div class="grp-card-hdr" onclick="var c=this.parentElement;c.classList.toggle(\'gc-collapsed\');this.querySelector(\'.gc-tog\').textContent=c.classList.contains(\'gc-collapsed\')?\'+\':\'-\'">'
            f'<span class="gc-tog" style="font-size:28px;line-height:1;width:24px;display:inline-block;text-align:center">-</span>'
            f'{g} <span style="font-weight:normal;font-size:10px;opacity:0.7">({n} params)</span>'
            f'<button onclick="event.stopPropagation();downloadGrpCSV(\'{ge}\')" '
            f'title="Download strip chart data as CSV" '
            f'style="margin-left:auto;padding:2px 9px;font-size:10px;font-weight:bold;'
            f'border:none;border-radius:3px;background:#27ae60;color:#fff;cursor:pointer;'
            f'flex-shrink:0" '
            f'onmouseover="this.style.background=\'#1e8449\'" '
            f'onmouseout="this.style.background=\'#27ae60\'">&#11015; CSV</button>'
            f'</div>'
            f'<div class="grp-card-body"><svg id="svg-grp-{gid}" style="display:block;width:100%"></svg>'
            f'<div class="grp-legend" style="padding:2px 8px 6px"></div></div>'
            f'</div>\n'
        )

    # Summary table header removed

    html = (
        "<!doctype html>\n<html lang='en'>\n<head>\n<meta charset='utf-8'>\n"
        f"<title>{title}</title>\n<style>\n{_CSS}\n"
        "button.grp-off{background:rgba(0,0,0,0.3)!important;opacity:0.7;text-decoration:line-through;color:#fff!important}\n"
        "</style>\n</head>\n<body style='display:flex;flex-direction:column;height:100vh;overflow:hidden'>\n"
        f'<div class="page-hdr"><h1>{title}</h1>'
        + (f'<div class="sub">{subtitle}</div>' if subtitle else "")
        + "</div>\n"
        + info_bar + "\n"
        # tabs row
        + '<div class="tabs" style="flex-shrink:0">'
        + '<button class="tab-btn active" onclick="showTab(this,\'tab-var\')">&#9741; Variability</button>'
        + '<button class="tab-btn" onclick="showTab(this,\'tab-pdly\')">&#9107; Distribution</button>'
        + '<button class="tab-btn" onclick="showTab(this,\'tab-xy\')">&#10799; XY Plot</button>'
        + '<button class="tab-btn" onclick="showTab(this,\'tab-pa\')">&#9660; Parameter Analysis</button>'
        + '<span style="flex:1"></span>'
        + '<span style="display:flex;align-items:center;padding:0 6px;gap:4px">'
        + '<button class="wfr-btn" onclick="setAllGroups(true)" title="Show all groups" style="padding:2px 8px;font-size:10px">All</button>'
        + '<button class="wfr-btn" onclick="setAllGroups(false)" title="Hide all groups" style="padding:2px 8px;font-size:10px">None</button>'
        + '<span style="width:1px;background:#4a6278;align-self:stretch;margin:4px 2px"></span>'
        + grp_btns
        + '</span>'
        + '</div>\n'
        # Main content: persistent filter sidebar + tab content area
        + '<div id="main-content">\n'
        # Panel 1: persistent filter (visible on ALL tabs)
        + '<div id="panel1">\n'
        + '<div class="p1-hdr">'
        + '&#128269; Filter'
        + '<span style="font-weight:normal;font-size:10px" id="row-info"></span>'
        + '<span><button style="background:none;border:1px solid #7f8c8d;color:#bdc3c7;font-size:10px;padding:1px 6px;cursor:pointer;border-radius:3px;margin-left:2px" onclick="selAll()">All</button>'
        + '<button style="background:none;border:1px solid #7f8c8d;color:#bdc3c7;font-size:10px;padding:1px 6px;cursor:pointer;border-radius:3px;margin-left:2px" onclick="clrAll()">Clr</button>'
        + '<button id="show-sel-btn" style="background:none;border:1px solid #7f8c8d;color:#bdc3c7;font-size:10px;padding:1px 6px;cursor:pointer;border-radius:3px;margin-left:2px" onclick="toggleShowSel()" title="Show only selected wafers">Sel</button></span>'
        + '</div>'
        # Search row
        + '<div class="p1-search-row">'
        + '<input placeholder="Program..." oninput="onSearch(\'program\',this.value)" title="Filter by Program" style="flex:2">'
        + '<input placeholder="Lot..." oninput="onSearch(\'lot\',this.value)" title="Filter by Lot" style="flex:2">'
        + '<input placeholder="Wafer..." oninput="onSearch(\'wafer\',this.value)" title="Filter by Wafer" style="flex:1">'
        + '<input placeholder="Layout..." oninput="onSearch(\'layout\',this.value)" title="Filter by Layout" style="flex:2">'
        + '<input placeholder="Material..." oninput="onSearch(\'material\',this.value)" title="Filter by Material" style="flex:2">'
        + '</div>'
        + '<div class="p1-body">'
        + '<table style="border-collapse:collapse;width:100%;font-size:11px">'
        + '<thead style="position:sticky;top:0;z-index:2"><tr>'
        + '<th style="background:#34495e;color:#ecf0f1;padding:4px 8px;text-align:left">Program</th>'
        + '<th style="background:#34495e;color:#ecf0f1;padding:4px 8px;text-align:left">Lot</th>'
        + '<th style="background:#34495e;color:#ecf0f1;padding:4px 8px;text-align:left">Wafer</th>'
        + '<th style="background:#34495e;color:#ecf0f1;padding:4px 8px;text-align:left">Layout</th>'
        + '<th style="background:#34495e;color:#ecf0f1;padding:4px 8px;text-align:left">Material</th>'
        + '<th style="background:#34495e;color:#ecf0f1;padding:4px 8px;text-align:right">N</th>'
        + '</tr></thead>'
        + '<tbody id="wfr-tbody"></tbody>'
        + '</table></div></div>\n'
        # Panel 1 resize handle
        + '<div id="p1-resize" class="p1-resize"></div>\n'
        # Tab content area — all 3 tabs share the persistent filter
        + '<div id="tab-content">\n'
        # --- variability tab (three-panel: sp12 / p2 / p3 — panel1 is now persistent above)
        + '<div id="tab-var" class="tab-panel active">\n'
        + ('<div style="display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center;padding:5px 12px;background:#1f3a50;color:#fff;font-size:12px;border-bottom:1px solid #1a252f;flex-shrink:0">'
           + _GBY
           + '<span style="width:1px;background:#4a6278;align-self:stretch;margin:0 4px"></span>'
           "<label style='cursor:pointer;font-size:12px;color:#ecf0f1;display:flex;align-items:center;gap:4px'>"
           "<input type='checkbox' id='var-persite-cb' checked onchange='_VAR_PER_SITE=this.checked;drawAllCharts()'> Per site</label>"
           + '<span style="width:1px;background:#4a6278;align-self:stretch;margin:0 4px"></span>'
           '<label style="cursor:default;color:#ecf0f1;display:flex;align-items:center;gap:5px">'
           '&#11041; Height'
           '<input id="chart-h-slider" type="range" min="150" max="1200" step="50" value="480" '
           'oninput="_CHART_H=+this.value;document.getElementById(\'chart-h-val\').textContent=this.value+\'px\';drawAllCharts()" '
           'style="width:100px;vertical-align:middle;accent-color:#3498db">'
           '<span id="chart-h-val" style="min-width:34px;font-size:10px;color:#aed6f1">480px</span>'
           '</label></div>\n')
        + '<div class="three-panel">\n'
        + '<div class="sp12" id="sp12" onmousedown="startSplit23(event)" title="Drag to resize | click arrow to hide/show table">'
        + '<button class="sp12-btn" id="p2-toggle-btn" onclick="event.stopPropagation();toggleP2()" title="Toggle parameter table">&#9664;</button>'
        + '</div>\n'
        # Panel 2: param table (hidden by default)
        + '<div id="panel2" class="p2-hidden">\n'
        + ('<div class="p2-hdr">&#128202; Parameter Table'
           '<button onclick="downloadVarCSV()" title="Download parameter summary table as CSV" '
           'style="margin-left:8px;padding:2px 9px;font-size:10px;font-weight:bold;'
           'border:none;border-radius:3px;background:#27ae60;color:#fff;cursor:pointer;'
           'vertical-align:middle" '
           'onmouseover="this.style.background=\'#1e8449\'" '
           'onmouseout="this.style.background=\'#27ae60\'">&#11015; Summary CSV</button>'
           '<button onclick="downloadSiteCSV()" title="Download per-site wide CSV (one row per reticle)" '
           'style="margin-left:4px;padding:2px 9px;font-size:10px;font-weight:bold;'
           'border:none;border-radius:3px;background:#2980b9;color:#fff;cursor:pointer;'
           'vertical-align:middle" '
           'onmouseover="this.style.background=\'#1a6496\'" '
           'onmouseout="this.style.background=\'#2980b9\'">&#11015; Per-site CSV</button>'
           '</div>')
        + '<div class="p2-body">'
        + '<table class="hm-tbl"><thead id="var-head"></thead><tbody id="var-body"></tbody></table>'
        + '</div></div>\n'
        # Splitter P2-P3
        + '<div class="sp23" id="sp23" onmousedown="startSplit23(event)"></div>\n'
        # Panel 3: per-group charts
        + '<div id="panel3">\n'
        + grp_cards
        + '</div>\n'
        + '</div></div>\n'
        # --- RO Distribution tab — side-panel + charts ---
        + '<div id="tab-pdly" class="tab-panel tab-panel-row">'
        + '<div class="sp12" id="sp12-dist" onmousedown="startSideP(event,\'panel2-dist\')" title="Drag to resize | click arrow to hide/show table">'
        + '<button class="sp12-btn" id="p2-dist-btn" onclick="event.stopPropagation();toggleSideP(\'panel2-dist\',\'sp12-dist\',\'p2-dist-btn\')" title="Toggle parameter table">&#9664;</button>'
        + '</div>\n'
        + '<div id="panel2-dist" class="panel2-side p2-hidden">\n'
        + ('<div class="p2-hdr">&#128202; Parameter Table</div>')
        + '<div class="p2-body">'
        + '<table class="hm-tbl"><thead id="dist-pt-head"></thead><tbody id="dist-pt-body"></tbody></table>'
        + '</div></div>\n'
        + '<div class="sp23" id="sp23-dist" onmousedown="startSideP(event,\'panel2-dist\')"></div>\n'
        + '<div style="flex:1;min-width:0;overflow-y:auto;overflow-x:hidden;background:#f0f2f5;padding:6px">'
        + _dist_panels_html
        + '</div>\n'
        + '</div>\n'
        # --- XY Plot tab — side-panel + charts ---
        + '<div id="tab-xy" class="tab-panel tab-panel-row">'
        + '<div class="sp12" id="sp12-xy" onmousedown="startSideP(event,\'panel2-xy\')" title="Drag to resize | click arrow to hide/show table">'
        + '<button class="sp12-btn" id="p2-xy-btn" onclick="event.stopPropagation();toggleSideP(\'panel2-xy\',\'sp12-xy\',\'p2-xy-btn\')" title="Toggle parameter table">&#9664;</button>'
        + '</div>\n'
        + '<div id="panel2-xy" class="panel2-side p2-hidden">\n'
        + ('<div class="p2-hdr">&#128202; Parameter Table</div>')
        + '<div class="p2-body">'
        + '<table class="hm-tbl"><thead id="xy-pt-head"></thead><tbody id="xy-pt-body"></tbody></table>'
        + '</div></div>\n'
        + '<div class="sp23" id="sp23-xy" onmousedown="startSideP(event,\'panel2-xy\')"></div>\n'
        + '<div style="flex:1;min-width:0;overflow-y:auto;overflow-x:hidden;background:#f0f2f5;padding:6px">'
        + _xy_panels_html
        + '</div>\n'
        + '</div>\n'         # close tab-xy
        # ── Tab: Parameter Analysis ──────────────────────────────────────
        + '<div id="tab-pa" class="tab-panel">\n'
        + ('<div style="display:flex;flex-wrap:wrap;align-items:center;padding:6px 14px;gap:6px;'
           'flex-shrink:0;background:#1f3a50;border-bottom:1px solid #1a252f">'
           '<b style="font-size:13px;color:#fff;margin-right:6px">&#9660; Parameter Analysis</b>'
           '<span style="color:#aed6f1;font-size:11px">'
           'Fail thresholds: &nbsp;<b style="color:#f38ba8">&ge;5%</b> = FAIL &nbsp;'
           '<b style="color:#f9e2af">&gt;0%</b> = MARGINAL &nbsp;'
           '<b style="color:#a6e3a1">0%</b> = PASS'
           '</span>'
           '<span style="width:1px;background:#4a6278;align-self:stretch;margin:0 6px"></span>'
           '<button id="pa-flt-fail" onclick="togglePaStatus(\'FAIL\',this)" '
           'style="padding:2px 10px;font-size:11px;font-weight:bold;border:none;border-radius:4px;'
           'background:#f38ba8;color:#1e1e2e;cursor:pointer">'
           'FAIL <span id="pa-cnt-fail">0</span></button>'
           '<button id="pa-flt-marg" onclick="togglePaStatus(\'MARGINAL\',this)" '
           'style="padding:2px 10px;font-size:11px;font-weight:bold;border:none;border-radius:4px;'
           'background:#f9e2af;color:#1e1e2e;cursor:pointer">'
           'MARGINAL <span id="pa-cnt-marg">0</span></button>'
           '<button id="pa-flt-pass" onclick="togglePaStatus(\'PASS\',this)" '
           'style="padding:2px 10px;font-size:11px;font-weight:bold;border:none;border-radius:4px;'
           'background:#a6e3a1;color:#1e1e2e;cursor:pointer">'
           'PASS <span id="pa-cnt-pass">0</span></button>'
           '<button id="pa-flt-nospec" onclick="togglePaStatus(\'NO SPEC\',this)" '
           'style="padding:2px 10px;font-size:11px;font-weight:bold;border:none;border-radius:4px;'
           'background:#5d6d7e;color:#fff;cursor:pointer">'
           'NO SPEC <span id="pa-cnt-nospec">0</span></button>'
           '<span style="width:1px;background:#4a6278;align-self:stretch;margin:0 6px"></span>'
           '<input type="text" placeholder="Search parameter\u2026" '
           'oninput="_PA_SRCH=this.value;buildParamAnalysisTab()" '
           'style="padding:3px 8px;font-size:12px;border:1px solid #4a6278;border-radius:4px;'
           'background:#2c3e50;color:#ecf0f1;width:200px">'
           '<button onclick="downloadParamAnalysisCSV()" '
           'title="Export parameter analysis as CSV" '
           'style="margin-left:auto;padding:3px 12px;font-size:11px;font-weight:bold;border:none;'
           'border-radius:4px;background:#27ae60;color:#fff;cursor:pointer" '
           'onmouseover="this.style.background=\'#1e8449\'" '
           'onmouseout="this.style.background=\'#27ae60\'">&#11015; Export CSV</button>'
           '</div>')
        + '<div id="pa-cont" style="flex:1;overflow-y:auto;padding:10px 14px;background:#1e1e2e"></div>'
        + '</div>\n'         # close tab-pa
        + '</div>\n'         # close tab-content
        + '</div>\n'         # close main-content
        # ── Param detail modal (hidden by default, opens on row click) ──
        + '<div id="pm-overlay" class="pm-overlay" onclick="if(event.target===this)_closeParamModal()">\n'
        + '<div class="pm-card">\n'
        + '<div class="pm-hdr"><span class="pm-hdr-title" id="pm-title"></span>'
        + '<button class="pm-close" onclick="_closeParamModal()" title="Close (Esc)">&times;</button></div>\n'
        + '<div class="pm-body" id="pm-body"></div>\n'
        + '</div></div>\n'
        # ── Footer / scripts ──
        + '<footer style="text-align:center;font-size:9px;color:#aed6f1;padding:4px;'
        + 'background:#2c3e50;border-top:1px solid #1a252f;flex-shrink:0">'
        + '</footer>\n'
        + '<script>\n'
        + 'var PCM_ROWS=__ROWS__;\n'
        + 'var PCM_GROUPS=__GROUPS__;\n'
        + 'var PCM_GROUP_PARAMS=__GROUP_PARAMS__;\n'
        + 'var PCM_PARAM_META=__PARAM_META__;\n'
        + 'var PCM_DEFAULT_GROUPS=__DEFAULT_GROUPS__;\n'
        + 'var PCM_DIST_PANELS=__DIST_PANELS__;\n'
        + 'var PCM_XY_PANELS=__XY_PANELS__;\n'
        + _JS + _PA_JS + _INIT_JS
        + '</script>\n</body>\n</html>\n'
    )

    html = html.replace("__ROWS__",         _safe_json(pcm_rows))
    html = html.replace("__GROUPS__",       _safe_json(pcm_groups))
    html = html.replace("__GROUP_PARAMS__", _safe_json(pcm_group_params))
    html = html.replace("__PARAM_META__",   _safe_json(pcm_param_meta))
    _js_default_groups = (list(default_groups) + _sort_group_names_for_vis) if default_groups else []
    html = html.replace("__DEFAULT_GROUPS__", _safe_json(_js_default_groups))
    html = html.replace("__DIST_PANELS__",  _safe_json(_dist_panels_resolved))
    html = html.replace("__XY_PANELS__",    _safe_json(_xy_panels_resolved))

    html = _wm_inject(html)  # fixed-position author watermark

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return output_path
