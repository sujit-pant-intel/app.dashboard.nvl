"""generate_class_html.py — CLASS Dashboard HTML generator.

Produces a self-contained single-file HTML that renders XY scatter plots
for:
  • SICC Sort  (Sort UPM vs Sort SICC per domain)
  • SICC Class (Sort UPM vs Class SICC per domain)
  • Vmin Core  (Sort UPM vs Vmin at each discovered freq, all 4 cores)
  • Vmin Atom  (Sort UPM vs Vmin at each discovered freq, all 4 atoms)
  • Vmin CCF   (Sort UPM vs CCF Vmin at each discovered freq)

Layout mirrors the etest-dashboard PCM HTML:
  Left panel  — lot / wafer filter (accordion grouped by lot)
  Right area  — group-by bar + tab strip + plot cards

Public API
----------
    generate_html(df, product_config, vmin_meta, output_path) -> str
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from _constants import _wm_inject

# ── palette ──────────────────────────────────────────────────────────────────
_CPALS = ['#2980b9','#27ae60','#e67e22','#8e44ad','#c0392b',
          '#16a085','#f39c12','#1abc9c','#d35400','#7f8c8d']

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
_CSS = """\
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Arial,sans-serif;background:#f0f2f5;color:#2c3e50;font-size:13px;display:flex;flex-direction:column;height:100vh;overflow:hidden}
.page-hdr{background:#1f3a50;color:#fff;padding:8px 14px;flex-shrink:0}
.page-hdr h1{font-size:14px;font-weight:bold}
.page-hdr .sub{font-size:11px;color:#aed6f1;margin-top:2px}
.info-bar{display:flex;flex-wrap:wrap;gap:8px;padding:6px 14px;background:#2c3e50;color:#ecf0f1;font-size:12px;border-bottom:2px solid #1a252f;flex-shrink:0}
.info-bar b{color:#f1c40f}
#main-wrap{display:flex;flex-direction:row;flex:1;min-height:0;overflow:hidden}
/* ── Left filter panel ── */
#panel1{width:260px;min-width:130px;flex-shrink:0;background:#fff;display:flex;flex-direction:column;border-right:2px solid #d0d7de;overflow:hidden;position:relative}
.p1-hdr{background:#2c3e50;color:#fff;padding:6px 10px;font-size:11px;font-weight:bold;display:flex;justify-content:space-between;align-items:center;flex-shrink:0;gap:4px}
.p1-hdr button{background:none;border:1px solid #7f8c8d;color:#bdc3c7;font-size:10px;padding:1px 7px;cursor:pointer;border-radius:3px}
.p1-hdr button:hover{background:#3d5166;color:#fff}
.p1-search-row{display:flex;gap:2px;padding:4px 6px;background:#f0f2f5;border-bottom:1px solid #dde;flex-shrink:0;flex-wrap:wrap}
.p1-search-row input{flex:1;min-width:60px;padding:2px 5px;font-size:10px;border:1px solid #ccc;border-radius:3px}
.p1-body{flex:1;overflow-y:auto;overflow-x:auto}
.p1-resize{width:5px;flex-shrink:0;background:#d0d7de;cursor:col-resize;align-self:stretch;transition:background .15s;user-select:none}
.p1-resize:hover,.p1-resize.dragging{background:#2980b9}
/* ── Right content ── */
#right{display:flex;flex-direction:column;flex:1;min-width:0;overflow:hidden}
.gby-bar{padding:4px 10px;background:#f0f4fb;border-bottom:1px solid #dde;display:flex;gap:8px;align-items:center;font-size:11px;flex-shrink:0;flex-wrap:wrap}
.gby-bar span{font-weight:bold;color:#2c3e50}
.tabs{display:flex;align-items:center;background:#1a252f;padding:5px 12px;gap:5px;border-bottom:3px solid #27ae60;flex-shrink:0;flex-wrap:wrap}
.tab-btn{padding:6px 18px;border:2px solid transparent;border-radius:6px;background:rgba(255,255,255,0.07);color:#95a5a6;cursor:pointer;font-size:13px;font-weight:bold;transition:background .15s,color .15s}
.tab-btn:hover{background:rgba(39,174,96,0.20);color:#a9dfbf}
.tab-btn.active{background:#27ae60;color:#fff;border-color:#1e8449;box-shadow:0 2px 8px rgba(39,174,96,.35)}
.tab-panel{display:none;flex:1;min-height:0;overflow-y:auto;padding:8px 10px;background:#f0f2f5}
.tab-panel.active{display:block}
/* ── Plot cards ── */
.plot-grid{display:flex;flex-wrap:wrap;gap:10px}
.plot-card{background:#fff;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);overflow:hidden;flex:1 1 560px;min-width:420px;max-width:100%}
.plot-card-hdr{display:flex;align-items:center;gap:6px;padding:5px 10px;background:#34495e;color:#ecf0f1;font-size:11px;font-weight:bold;user-select:none;flex-wrap:wrap}
.plot-card-body{padding:4px 8px 8px}
.legend-row{display:flex;flex-wrap:wrap;gap:4px 12px;padding:4px 8px 0;font-size:11px}
.leg-item{display:flex;align-items:center;gap:4px;cursor:pointer}
.leg-swatch{width:10px;height:10px;border-radius:50%;flex-shrink:0}
/* ── Filter table ── */
.wfr-tbl{border-collapse:collapse;width:100%;font-size:11px;white-space:nowrap}
.wfr-tbl th{background:#34495e;color:#ecf0f1;padding:4px 8px;text-align:left;position:sticky;top:0;z-index:2}
.wfr-tbl th.num{text-align:right}
.wfr-tbl td{padding:3px 8px;border-bottom:1px solid #eee}
.wfr-tbl td.num{text-align:right}
.wfr-tbl .fr:hover td{background:#eaf4ff!important;cursor:pointer}
.wfr-tbl .frs td{background:#d6eaff!important;font-weight:bold}
.wfr-tbl .frs:hover td{background:#bcd8f8!important}
.lot-hdr-row td{background:#34495e!important;color:#ecf0f1!important;cursor:pointer}
.fp{padding:3px 8px;white-space:nowrap;border-bottom:1px solid #eee}
"""

# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------
_JS = r"""
/* ── Utils ── */
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function _fmt(v){if(v==null||isNaN(v)||!isFinite(v))return '';if(Math.abs(v)>0&&(Math.abs(v)<1e-4||Math.abs(v)>=1e7))return v.toExponential(3);return parseFloat(v.toPrecision(4)).toString();}
function _safeMin(a){var m=Infinity;for(var i=0;i<a.length;i++)if(a[i]<m)m=a[i];return m===Infinity?0:m;}
function _safeMax(a){var m=-Infinity;for(var i=0;i<a.length;i++)if(a[i]>m)m=a[i];return m===-Infinity?0:m;}
function _med(a){if(!a||!a.length)return null;var s=a.slice().sort(function(a,b){return a-b;});var m=Math.floor(s.length/2);return s.length%2?s[m]:(s[m-1]+s[m])/2;}
function _niceStep(r){if(r<=0||!isFinite(r))return 0.1;var m=Math.pow(10,Math.floor(Math.log10(r)));var s=r/m;return s<1.5?m:s<3?2*m:s<7?5*m:10*m;}
var _CPALS=['#2980b9','#27ae60','#e67e22','#8e44ad','#c0392b','#16a085','#f39c12','#1abc9c','#d35400','#7f8c8d','#3498db','#2ecc71','#e74c3c','#9b59b6','#f0a500'];
function _cPal(i){return _CPALS[i%_CPALS.length];}

/* ── Unique wafer index ── */
function _rKey(r){return r.lot+'|'+(r.layout||'')+'|'+r.wafer+'|'+(r.material||'');}
var UNIQ_WFR=(function(){
  var seen={},out=[];
  ROWS.forEach(function(r){
    var k=_rKey(r);
    if(!seen[k]){seen[k]=out.length;out.push({lot:r.lot,wafer:r.wafer,layout:r.layout||'',material:r.material||'',key:k,n:0});}
    out[seen[k]].n++;
  });
  return out;
})();

/* ── Selection state ── */
var SEL_WFR=new Set();
UNIQ_WFR.forEach(function(_,i){SEL_WFR.add(i);});
var _FSRCH={lot:'',wafer:'',layout:'',material:''};
var _lastWfr=-1;
var _lotCollapsed={};
var _curLotOrder=[];

function activeKeys(){var s=new Set();SEL_WFR.forEach(function(wi){s.add(UNIQ_WFR[wi].key);});return s;}

function _visIndices(){var vis=[];UNIQ_WFR.forEach(function(w,i){
  if(_FSRCH.lot&&w.lot.toLowerCase().indexOf(_FSRCH.lot.toLowerCase())<0)return;
  if(_FSRCH.wafer&&w.wafer.toLowerCase().indexOf(_FSRCH.wafer.toLowerCase())<0)return;
  if(_FSRCH.layout&&(w.layout||'').toLowerCase().indexOf(_FSRCH.layout.toLowerCase())<0)return;
  if(_FSRCH.material&&(w.material||'').toLowerCase().indexOf(_FSRCH.material.toLowerCase())<0)return;
  vis.push(i);
});return vis;}

function onSearch(field,val){_FSRCH[field]=val;buildWfrList();}

function buildWfrList(){
  var vis=_visIndices();
  var byLot={},lotOrder=[];
  vis.forEach(function(wi){
    var l=UNIQ_WFR[wi].lot;
    if(!byLot[l]){byLot[l]=[];lotOrder.push(l);}
    byLot[l].push(wi);
  });
  _curLotOrder=lotOrder;
  lotOrder.forEach(function(l){if(_lotCollapsed[l]===undefined)_lotCollapsed[l]=false;});

  var html='';
  var indeterms=[];
  lotOrder.forEach(function(lot,li){
    var wis=byLot[lot];
    var selCnt=wis.filter(function(wi){return SEL_WFR.has(wi);}).length;
    var allSel=selCnt===wis.length;
    var isCol=_lotCollapsed[lot]===true;
    if(selCnt>0&&!allSel)indeterms.push(li);
    var mats=[];wis.forEach(function(wi){var m=UNIQ_WFR[wi].material||'';if(mats.indexOf(m)<0)mats.push(m);});
    var lays=[];wis.forEach(function(wi){var ly=UNIQ_WFR[wi].layout||'';if(lays.indexOf(ly)<0)lays.push(ly);});
    var totN=wis.reduce(function(s,wi){return s+UNIQ_WFR[wi].n;},0);
    html+='<tr class="lot-hdr-row" onclick="toggleLot('+li+')">'
      +'<td colspan="4" style="padding:4px 8px;font-size:11px">'
      +'<span id="la-'+li+'" style="margin-right:3px">'+(isCol?'&#9658;':'&#9660;')+'</span>'
      +'<input type="checkbox" id="lcb-'+li+'" style="vertical-align:middle;margin-right:3px" '+(allSel?'checked':'')
      +' onclick="selLot(event,\''+esc(lot)+'\')">'
      +esc(lot)+' <span style="font-size:10px;color:#aed6f1">('+selCnt+'/'+wis.length+')</span>'
      +' <span style="font-size:10px;color:#bdc3c7;font-weight:normal">'+(mats[0]||'')+' '+( lays[0]||'')+'</span>'
      +'</td>'
      +'<td class="num" style="background:#34495e;color:#bdc3c7;font-size:10px;padding:4px 6px">'+totN+'</td>'
      +'</tr>';
    wis.forEach(function(wi){
      var w=UNIQ_WFR[wi];
      var isSel=SEL_WFR.has(wi);
      html+='<tr class="fr'+(isSel?' frs':'')+'" data-li="'+li+'"'+(isCol?' style="display:none"':'')
        +' onclick="toggleWfr('+wi+',event)">'
        +'<td class="fp">'+esc(w.lot)+'</td>'
        +'<td class="fp">'+esc(w.wafer)+'</td>'
        +'<td class="fp" style="font-size:10px;color:#7f8c8d">'+esc(w.layout||'')+'</td>'
        +'<td class="fp num">'+w.n+'</td>'
        +'</tr>';
    });
  });
  var tbody=document.getElementById('wfr-tbody');
  if(tbody)tbody.innerHTML=html;
  indeterms.forEach(function(li){var cb=document.getElementById('lcb-'+li);if(cb)cb.indeterminate=true;});
  var ri=document.getElementById('row-info');
  if(ri){var s=SEL_WFR.size,t=UNIQ_WFR.length;ri.textContent=(s>0&&s<t)?'('+s+'/'+t+' sel)':'';}
}

function toggleLot(li){
  var lot=_curLotOrder[li];if(!lot)return;
  var wasOpen=(_lotCollapsed[lot]===false);
  _curLotOrder.forEach(function(l){_lotCollapsed[l]=true;});
  if(!wasOpen)_lotCollapsed[lot]=false;
  buildWfrList();
}
function selLot(ev,lot){
  ev.stopPropagation();
  var wis=[];UNIQ_WFR.forEach(function(w,wi){if(w.lot===lot)wis.push(wi);});
  var allSel=wis.every(function(wi){return SEL_WFR.has(wi);});
  wis.forEach(function(wi){if(allSel)SEL_WFR.delete(wi);else SEL_WFR.add(wi);});
  buildWfrList();rerender();
}
function toggleWfr(wi,ev){
  var vis=_visIndices();
  if(ev&&ev.shiftKey&&_lastWfr>=0){
    var lo=Math.min(wi,_lastWfr),hi=Math.max(wi,_lastWfr);
    for(var i=lo;i<=hi;i++)if(vis.indexOf(i)>=0)SEL_WFR.add(i);
  }else{if(SEL_WFR.has(wi))SEL_WFR.delete(wi);else SEL_WFR.add(wi);}
  _lastWfr=wi;buildWfrList();rerender();
}
function selAll(){_visIndices().forEach(function(i){SEL_WFR.add(i);});buildWfrList();rerender();}
function clrAll(){_visIndices().forEach(function(i){SEL_WFR.delete(i);});buildWfrList();rerender();}

/* ── Group-by ── */
var VAR_GBY=[];
function toggleGby(field){
  if(field==='none'){VAR_GBY=[];}
  else{var i=VAR_GBY.indexOf(field);if(i>=0)VAR_GBY.splice(i,1);else VAR_GBY.push(field);}
  document.querySelectorAll('.vgb-cb').forEach(function(cb){
    if(cb.value==='none')cb.checked=VAR_GBY.length===0;
    else cb.checked=VAR_GBY.indexOf(cb.value)>=0;
  });
  rerender();
}
function _grpKey(r){
  if(!VAR_GBY.length)return 'All';
  var pts=[];
  if(VAR_GBY.indexOf('lot')>=0)pts.push(r.lot||'');
  if(VAR_GBY.indexOf('wafer')>=0)pts.push(r.wafer||'');
  if(VAR_GBY.indexOf('layout')>=0)pts.push(r.layout||'');
  if(VAR_GBY.indexOf('material')>=0)pts.push(r.material||'');
  return pts.join('/')||'All';
}
function _cMap(){
  var map={},keys=[];
  var ak=activeKeys();
  ROWS.forEach(function(r){
    if(!ak.has(_rKey(r)))return;
    var k=_grpKey(r);
    if(!map[k]){map[k]=_cPal(keys.length);keys.push(k);}
  });
  return {map:map,keys:keys};
}

/* ── Tabs ── */
function showTab(id,btn){
  document.querySelectorAll('.tab-panel').forEach(function(p){p.classList.remove('active');});
  document.querySelectorAll('.tab-btn').forEach(function(b){b.classList.remove('active');});
  var p=document.getElementById(id);if(p)p.classList.add('active');
  if(btn)btn.classList.add('active');
}

/* ── Panel1 resize ── */
(function(){
  var el=document.getElementById('p1-resize');
  var p1=document.getElementById('panel1');
  if(!el||!p1)return;
  var dragging=false,startX=0,startW=0;
  el.addEventListener('mousedown',function(e){dragging=true;startX=e.clientX;startW=p1.offsetWidth;el.classList.add('dragging');e.preventDefault();});
  document.addEventListener('mousemove',function(e){if(!dragging)return;var w=Math.max(100,Math.min(500,startW+(e.clientX-startX)));p1.style.width=w+'px';});
  document.addEventListener('mouseup',function(){if(dragging){dragging=false;el.classList.remove('dragging');}});
})();

/* ── XY Scatter SVG renderer ────────────────────────────────────────────── */
/*
  drawXY(containerId, xKey, yDefs, opts)
    xKey    — short key for X axis (e.g. 'u107_950')
    yDefs   — array of {key, label, color}  (for multi-Y)
    opts    — {fixedColors: bool}  if true, use yDef.color instead of group-by
*/
function drawXY(containerId, xKey, yDefs, opts){
  opts=opts||{};
  var cont=document.getElementById(containerId);
  if(!cont){return;}

  var ak=activeKeys();
  var cm=_cMap();

  /* Collect active rows */
  var pts=[];  /* {x, y, grpKey, yIdx, rKey} */
  ROWS.forEach(function(r){
    if(!ak.has(_rKey(r)))return;
    var xv=r[xKey];
    if(xv==null||isNaN(+xv)||!isFinite(+xv))return;
    yDefs.forEach(function(yd,yi){
      var yv=r[yd.key];
      if(yv==null||isNaN(+yv)||!isFinite(+yv))return;
      pts.push({x:+xv,y:+yv,gk:_grpKey(r),yi:yi,rk:_rKey(r),lot:r.lot,wafer:r.wafer,pkg:r.pkg||''});
    });
  });

  if(!pts.length){
    cont.innerHTML='<div style="padding:20px;color:#888;text-align:center">No data for current selection</div>';
    return;
  }

  /* Axis ranges with 5% padding */
  var xs=pts.map(function(p){return p.x;}),ys=pts.map(function(p){return p.y;});
  var xMin=_safeMin(xs),xMax=_safeMax(xs),yMin=_safeMin(ys),yMax=_safeMax(ys);
  var xPad=(xMax-xMin||Math.abs(xMin)*0.1||1)*0.05;
  var yPad=(yMax-yMin||Math.abs(yMin)*0.1||0.01)*0.08;
  var xLo=xMin-xPad,xHi=xMax+xPad,yLo=yMin-yPad,yHi=yMax+yPad;
  var xRng=xHi-xLo,yRng=yHi-yLo;

  var W=680,H=340,ML=60,MR=16,MT=18,MB=52;
  var pW=W-ML-MR,pH=H-MT-MB;

  function px(v){return ML+pW*(v-xLo)/xRng;}
  function py(v){return MT+pH*(1-(v-yLo)/yRng);}

  var parts=['<svg width="100%" viewBox="0 0 '+W+' '+H+'" style="display:block;background:#fff">'];

  /* Plot area border */
  parts.push('<rect x="'+ML+'" y="'+MT+'" width="'+pW+'" height="'+pH+'" fill="#fafbfc" stroke="#ddd" stroke-width="1"/>');

  /* Grid lines X */
  var xStep=_niceStep(xRng/5);
  var xStart=Math.ceil(xLo/xStep)*xStep;
  for(var xv=xStart;xv<=xHi+xStep*0.01;xv+=xStep){
    var xpx=px(xv).toFixed(1);
    if(parseFloat(xpx)<ML-1||parseFloat(xpx)>ML+pW+1)continue;
    parts.push('<line x1="'+xpx+'" y1="'+MT+'" x2="'+xpx+'" y2="'+(MT+pH)+'" stroke="rgba(0,0,0,0.07)" stroke-width="0.8"/>');
    parts.push('<text x="'+xpx+'" y="'+(MT+pH+14)+'" text-anchor="middle" font-size="10" fill="#666">'+_fmt(xv)+'</text>');
  }
  /* Grid lines Y */
  var yStep=_niceStep(yRng/5);
  var yStart=Math.ceil(yLo/yStep)*yStep;
  for(var yv=yStart;yv<=yHi+yStep*0.01;yv+=yStep){
    var ypy=py(yv).toFixed(1);
    if(parseFloat(ypy)<MT-1||parseFloat(ypy)>MT+pH+1)continue;
    parts.push('<line x1="'+ML+'" y1="'+ypy+'" x2="'+(ML+pW)+'" y2="'+ypy+'" stroke="rgba(0,0,0,0.07)" stroke-width="0.8"/>');
    parts.push('<text x="'+(ML-4)+'" y="'+ypy+'" text-anchor="end" dominant-baseline="middle" font-size="10" fill="#666">'+_fmt(yv)+'</text>');
  }

  /* Points */
  var TT_DATA=[];
  pts.forEach(function(p,pi){
    var col=opts.fixedColors?yDefs[p.yi].color:( cm.map[p.gk]||_cPal(p.yi));
    var cx=px(p.x).toFixed(1),cy=py(p.y).toFixed(1);
    parts.push('<circle cx="'+cx+'" cy="'+cy+'" r="4" fill="'+col+'" fill-opacity="0.72" stroke="'+col+'" stroke-width="0.5"'
      +' onmouseenter="showTT(event,'+pi+')" onmouseleave="hideTT()" style="cursor:default"/>');
    TT_DATA.push({x:p.x,y:p.y,lot:p.lot,wfr:p.wafer,pkg:p.pkg,ylabel:yDefs[p.yi].label});
  });

  /* Axis labels */
  var xLabel=LABELS[xKey]||xKey;
  var yLabels=yDefs.map(function(yd){return yd.label;}).filter(function(l,i,a){return a.indexOf(l)===i;}).join(' / ');
  parts.push('<text x="'+(ML+pW/2)+'" y="'+(H-6)+'" text-anchor="middle" font-size="11" font-weight="bold" fill="#333">'+esc(xLabel)+'</text>');
  parts.push('<text transform="translate(12,'+(MT+pH/2)+') rotate(-90)" text-anchor="middle" font-size="10" fill="#555">'+esc(yLabels.substring(0,60))+'</text>');

  parts.push('</svg>');

  /* Tooltip script data */
  var ttJson=JSON.stringify(TT_DATA);
  var ttScript='<script>window._tt_'+containerId+'='+ttJson+';<\/script>';

  cont.innerHTML=parts.join('')+ttScript;
}

/* ── Tooltip ── */
var _TT=null;
function _getTT(){
  if(!_TT){_TT=document.createElement('div');_TT.style.cssText='position:fixed;background:rgba(20,28,40,0.93);color:#ecf0f1;font-size:11px;padding:5px 10px;border-radius:5px;pointer-events:none;z-index:9999;display:none;white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,.4)';document.body.appendChild(_TT);}
  return _TT;
}
function showTT(ev,pi){
  var id=ev.currentTarget.closest('[id]');if(!id)return;
  var data=window['_tt_'+id.id];
  if(!data||!data[pi])return;
  var d=data[pi];
  var tt=_getTT();
  tt.innerHTML='<b>X:</b> '+_fmt(d.x)+'  <b>Y:</b> '+_fmt(d.y)+'<br>'
    +esc(d.ylabel)+'<br>'
    +'Lot: '+esc(d.lot||'')+'  Wfr: '+esc(d.wfr||'')
    +(d.pkg?'<br>Pkg: '+esc(d.pkg):'');
  tt.style.display='block';
  tt.style.left=(ev.clientX+12)+'px';
  tt.style.top=(ev.clientY-10)+'px';
}
function hideTT(){var tt=_getTT();tt.style.display='none';}

/* ── rerender ── */
function rerender(){
  /* Re-draw all visible XY plots */
  document.querySelectorAll('[data-xy]').forEach(function(el){
    var spec=null;try{spec=JSON.parse(el.getAttribute('data-xy'));}catch(e){return;}
    if(!spec)return;
    drawXY(el.id, spec.xKey, spec.yDefs, spec.opts||{});
  });
}

/* ── Init ── */
window.addEventListener('DOMContentLoaded',function(){
  buildWfrList();
  rerender();
});
"""

# ---------------------------------------------------------------------------
# Python helpers
# ---------------------------------------------------------------------------

def _safe_float(v):
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return None


def _build_rows_json(df: pd.DataFrame, keys: List[str]) -> str:
    """Emit the ROWS array with only the short keys actually present in df."""
    present = [k for k in keys if k in df.columns]
    records = []
    for _, row in df.iterrows():
        rec: dict = {}
        for k in ['lot', 'wafer', 'pkg', 'layout', 'material', 'sx', 'sy']:
            v = row.get(k)
            rec[k] = '' if pd.isna(v) else str(v) if k not in ('sx','sy') else _safe_float(v)
        for k in present:
            rec[k] = _safe_float(row[k])
        records.append(rec)
    return json.dumps(records, separators=(',', ':'))


def _vmin_tab_plots(vmin_meta: dict, module: str, upm_key: str, label_prefix: str, cfg: dict) -> List[dict]:
    """
    Build a list of plot specs for a Vmin tab.
    Each spec = {card_label, xKey, yDefs: [{key, label, color}], opts}
    One card per (freq) — all indices of that freq on the same plot.
    """
    entries = vmin_meta.get(module, [])
    # Group by freq
    by_freq: Dict[str, List] = {}
    for short_key, freq_mhz, idx, _ in entries:
        if freq_mhz not in by_freq:
            by_freq[freq_mhz] = []
        by_freq[freq_mhz].append((short_key, idx))

    plots = []
    upm_label = (cfg.get('sort_upm_labels') or {}).get(upm_key, upm_key)
    for freq_mhz in sorted(by_freq, key=lambda x: int(x)):
        freq_ghz = f'{int(freq_mhz)/1000:.3f}'.rstrip('0').rstrip('.')
        y_defs = []
        for short_key, idx in sorted(by_freq[freq_mhz], key=lambda t: int(t[1])):
            y_defs.append({
                'key': short_key,
                'label': f'{label_prefix} {idx} @ {freq_ghz} GHz',
                'color': _CPALS[int(idx) % len(_CPALS)],
            })
        plots.append({
            'label': f'{label_prefix} Vmin @ {freq_ghz} GHz vs {upm_label}',
            'xKey': upm_key,
            'yDefs': y_defs,
            'opts': {'fixedColors': True},
        })
    return plots


def _xy_plots_from_cfg(xy_plots: List[dict], all_labels: dict) -> List[dict]:
    """Convert product config xy_plot entries into card specs."""
    specs = []
    for p in xy_plots:
        x_key = p.get('x', '')
        ys = p.get('ys', [])
        y_defs = [{'key': yk, 'label': all_labels.get(yk, yk), 'color': _CPALS[i % len(_CPALS)]}
                  for i, yk in enumerate(ys)]
        specs.append({
            'label': p.get('label', x_key),
            'xKey': x_key,
            'yDefs': y_defs,
            'opts': {'fixedColors': True},
        })
    return specs


def _render_plot_cards(tab_id: str, plot_specs: List[dict]) -> str:
    """Emit HTML for a grid of plot cards within a tab."""
    parts = ['<div class="plot-grid">']
    for i, spec in enumerate(plot_specs):
        card_id = f'xy_{tab_id}_{i}'
        spec_json = json.dumps({'xKey': spec['xKey'], 'yDefs': spec['yDefs'], 'opts': spec.get('opts', {})},
                               separators=(',', ':'))
        # Legend row
        legend_html = ''.join(
            f'<span class="leg-item"><span class="leg-swatch" style="background:{yd["color"]}"></span>'
            f'{yd["label"]}</span>'
            for yd in spec['yDefs']
        )
        parts.append(
            f'<div class="plot-card">'
            f'<div class="plot-card-hdr">{spec["label"]}</div>'
            f'<div class="legend-row">{legend_html}</div>'
            f'<div class="plot-card-body">'
            f'<div id="{card_id}" data-xy=\'{spec_json}\'></div>'
            f'</div></div>'
        )
    parts.append('</div>')
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_html(
    df: pd.DataFrame,
    product_config: dict,
    vmin_meta: dict,
    output_path: str,
) -> str:
    """Generate the class analysis HTML and write it to *output_path*.

    Returns the absolute output path.
    """
    cfg = product_config
    title   = cfg.get('title', 'CLASS Dashboard')
    subtitle = cfg.get('subtitle', '')

    # ── Build all labels dict (short_key → human label) ──────────────────
    all_labels: dict = {}
    all_labels.update(cfg.get('sort_upm_labels', {}))
    all_labels.update(cfg.get('sort_sicc_labels', {}))
    all_labels.update(cfg.get('class_sicc_labels', {}))
    # Add vmin labels dynamically
    for module, entries in vmin_meta.items():
        prefix = {'core': 'Core', 'atom': 'Atom', 'ccf': 'CCF'}.get(module, module.title())
        for short_key, freq_mhz, idx, _ in entries:
            freq_ghz = f'{int(freq_mhz)/1000:.3f}'.rstrip('0').rstrip('.')
            all_labels[short_key] = f'{prefix} {idx} Vmin @ {freq_ghz} GHz'

    # ── Gather all short keys in df ────────────────────────────────────────
    meta_keys = (['lot','wafer','pkg','layout','material','sx','sy']
                 + list(cfg.get('sort_upm', {}).keys())
                 + list(cfg.get('sort_sicc', {}).keys()) + ['ss_fc']
                 + list(cfg.get('class_sicc', {}).keys()) + ['sc_fc']
                 + [e[0] for entries in vmin_meta.values() for e in entries])
    data_keys = [k for k in meta_keys if k in df.columns]

    rows_json = _build_rows_json(df, data_keys)
    labels_json = json.dumps(all_labels, separators=(',', ':'))

    # ── Summary info ──────────────────────────────────────────────────────
    n_dies   = len(df)
    n_lots   = df['lot'].nunique() if 'lot' in df.columns else 0
    n_wafers = df['wafer'].nunique() if 'wafer' in df.columns else 0
    n_pkgs   = df['pkg'].nunique() if 'pkg' in df.columns else 0

    # ── Build XY plot specs per tab ────────────────────────────────────────
    xy_cfg = cfg.get('xy_plots', {})
    sicc_sort_plots  = _xy_plots_from_cfg(xy_cfg.get('sicc_sort', []), all_labels)
    sicc_class_plots = _xy_plots_from_cfg(xy_cfg.get('sicc_class', []), all_labels)

    upm_x = 'u107_950'   # default UPM for Vmin tabs
    vmin_core_plots = _vmin_tab_plots(vmin_meta, 'core', upm_x, 'Core', cfg)
    vmin_atom_plots = _vmin_tab_plots(vmin_meta, 'atom', upm_x, 'Atom', cfg)
    vmin_ccf_plots  = _vmin_tab_plots(vmin_meta, 'ccf',  upm_x, 'CCF',  cfg)

    # HTML for each tab content
    sicc_sort_html  = _render_plot_cards('sicc_sort',  sicc_sort_plots)  if sicc_sort_plots  else '<div style="padding:20px;color:#888">No SICC Sort plot data.</div>'
    sicc_class_html = _render_plot_cards('sicc_class', sicc_class_plots) if sicc_class_plots else '<div style="padding:20px;color:#888">No SICC Class plot data.</div>'
    vmin_core_html  = _render_plot_cards('vmin_core',  vmin_core_plots)  if vmin_core_plots  else '<div style="padding:20px;color:#888">No Core Vmin columns found.</div>'
    vmin_atom_html  = _render_plot_cards('vmin_atom',  vmin_atom_plots)  if vmin_atom_plots  else '<div style="padding:20px;color:#888">No Atom Vmin columns found.</div>'
    vmin_ccf_html   = _render_plot_cards('vmin_ccf',   vmin_ccf_plots)   if vmin_ccf_plots   else '<div style="padding:20px;color:#888">No CCF Vmin columns found.</div>'

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{title}</title>
<style>{_CSS}</style>
</head><body>

<div class="page-hdr">
  <h1>{title}</h1>
  <div class="sub">{subtitle}</div>
</div>

<div class="info-bar">
  <span><b>Dies:</b> {n_dies:,}</span>
  <span><b>Packages:</b> {n_pkgs:,}</span>
  <span><b>Lots:</b> {n_lots}</span>
  <span><b>Wafers:</b> {n_wafers}</span>
</div>

<div id="main-wrap">

  <!-- ── Filter panel ── -->
  <div id="panel1">
    <div class="p1-hdr">
      <span>Lot / Wafer <span id="row-info" style="font-size:10px;color:#aed6f1;font-weight:normal"></span></span>
      <div style="display:flex;gap:3px">
        <button onclick="selAll()">All</button>
        <button onclick="clrAll()">None</button>
      </div>
    </div>
    <div class="p1-search-row">
      <input placeholder="Lot…" oninput="onSearch('lot',this.value)" title="Filter by lot">
      <input placeholder="Wfr…" oninput="onSearch('wafer',this.value)" title="Filter by wafer">
      <input placeholder="Mat…" oninput="onSearch('material',this.value)" title="Filter by material">
    </div>
    <div class="p1-body">
      <table class="wfr-tbl">
        <thead><tr>
          <th>Lot</th><th>Wfr</th><th>Layout</th><th class="num">N</th>
        </tr></thead>
        <tbody id="wfr-tbody"></tbody>
      </table>
    </div>
  </div>
  <div class="p1-resize" id="p1-resize"></div>

  <!-- ── Right: group-by + tabs ── -->
  <div id="right">
    <div class="gby-bar">
      <span>Color by:</span>
      <label><input type="checkbox" class="vgb-cb" value="lot" onclick="toggleGby('lot')"> Lot</label>
      <label><input type="checkbox" class="vgb-cb" value="wafer" onclick="toggleGby('wafer')"> Wafer</label>
      <label><input type="checkbox" class="vgb-cb" value="layout" onclick="toggleGby('layout')"> Layout</label>
      <label><input type="checkbox" class="vgb-cb" value="material" onclick="toggleGby('material')"> Material</label>
    </div>

    <div class="tabs">
      <button class="tab-btn active" onclick="showTab('sicc_sort',this)">SICC Sort</button>
      <button class="tab-btn" onclick="showTab('sicc_class',this)">SICC Class</button>
      <button class="tab-btn" onclick="showTab('vmin_core',this)">Vmin Core</button>
      <button class="tab-btn" onclick="showTab('vmin_atom',this)">Vmin Atom</button>
      <button class="tab-btn" onclick="showTab('vmin_ccf',this)">Vmin CCF</button>
    </div>

    <div id="sicc_sort"  class="tab-panel active">{sicc_sort_html}</div>
    <div id="sicc_class" class="tab-panel">{sicc_class_html}</div>
    <div id="vmin_core"  class="tab-panel">{vmin_core_html}</div>
    <div id="vmin_atom"  class="tab-panel">{vmin_atom_html}</div>
    <div id="vmin_ccf"   class="tab-panel">{vmin_ccf_html}</div>

  </div><!-- #right -->
</div><!-- #main-wrap -->

<script>
const ROWS={rows_json};
const LABELS={labels_json};
{_JS}
</script>
</body></html>"""

    html = _wm_inject(html)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(html, encoding='utf-8')
    return str(out_p)
