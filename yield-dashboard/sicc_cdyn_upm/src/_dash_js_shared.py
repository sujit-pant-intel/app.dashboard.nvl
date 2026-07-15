"""_dash_js_shared.py — Shared JavaScript for the dashboard.

Plain Python string constants — normal JS braces {{ }} do NOT need escaping here.
"""
import sys as _sys, os as _os
_YLD_SRC = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '../../yld/src')
if _YLD_SRC not in _sys.path:
    _sys.path.insert(0, _YLD_SRC)
from _filter_lot_wafer import FILTER_DD_JS as _FILTER_DD_JS, make_filter_js as _make_filter_js

_SICC_ON_CHANGE = (
    'render_sicc();render_cdyn();render_summ();'
    'var _ap=document.querySelector(\'.tab-panel.active\');'
    'if(_ap&&_ap.id===\'tab-dist\')renderHist();'
)
_SICC_FILTER_JS = _make_filter_js(
    on_change_calls=_SICC_ON_CHANGE,
    sel_var='SEL_WFR',
    toggle_fn='toggleRow',
)

# ── Shared state, utils, sidebar, and chart helpers ─────────────────────────
SHARED_JS = (
    _FILTER_DD_JS
    + 'var DATA={rows:ROWS,hasMaterial:ROWS.some(function(r){return r.material&&r.material!==\'\';}),'  # noqa
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
    var row=[r.program||'',r.lot||'',r.wafer||''].concat(DATA.hasMaterial?[r.material||']':[])
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
function medArr(a){
  if(!a||!a.length)return null;
  var s=a.slice().sort(function(x,y){return x-y;});
  var m=Math.floor(s.length/2);
  return s.length%2?s[m]:(s[m-1]+s[m])/2;
}
/* Flatten die-level arrays from multiple wafer rows into a single flat array of valid numbers */
function flatVals(indices,field,isCdyn){
  var out=[];
  for(var k=0;k<indices.length;k++){
    var r=ROWS[indices[k]];if(!r)continue;
    var v=isCdyn?r.cdyn[field]:r.medians[field];
    if(v==null)continue;
    if(!isNaN(v))out.push(v);
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
  var lo=Math.min.apply(null,allU),hi=Math.max.apply(null,allU);
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
  var maxC=Math.max.apply(null,counts)||1;
  var bw=cW/nb;
  var p=['<rect width="'+W+'" height="'+H+'" fill="#fffaf4"/>'];
  for(var i=0;i<nb;i++){
    var bh=(counts[i]/maxC)*cH;
    var bx=pl+i*bw,by=pt+cH-bh;
    p.push('<rect x="'+bx.toFixed(1)+'" y="'+by.toFixed(1)+'" width="'+(bw*0.85).toFixed(1)+'" height="'+Math.max(1,bh).toFixed(1)+'" fill="#e67e22" opacity="0.75"/>');
    if(counts[i]>0)p.push('<text x="'+(bx+bw*0.425).toFixed(1)+'" y="'+(by-2).toFixed(1)+'" text-anchor="middle" font-size="12" fill="#c0650a">'+counts[i]+'</text>');
  }
  // Median line
  if(med!=null&&hi>lo){
    var mx=pl+(med-lo)/(hi-lo)*cW;
    if(mx>=pl-2&&mx<=pl+cW+2){
      p.push('<line x1="'+mx.toFixed(1)+'" x2="'+mx.toFixed(1)+'" y1="'+pt+'" y2="'+(pt+cH)+'" stroke="#d35400" stroke-width="2" stroke-dasharray="4,3"/>');
      p.push('<text x="'+(mx+3).toFixed(1)+'" y="'+(pt+15)+'" font-size="15" fill="#d35400" font-weight="bold">Med:'+med.toFixed(2)+'%</text>');
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
      for(var di=0;di<dp.s.length;di++) sVals.push(dp.s[di]);
    }else{
      var h=r.hists[SEL_COL];
      if(h&&h.edges.length>1){
        for(var bi=0;bi<h.counts.length;bi++){
          var mid=(h.edges[bi]+h.edges[bi+1])/2;
          for(var ci=0;ci<h.counts[bi];ci++)sVals.push(mid);
        }
      }else{
        var fb=isCdyn?r.cdyn[SEL_COL]:r.medians[SEL_COL];
        if(fb!=null&&!isNaN(fb))sVals.push(fb);
      }
    }
  });
  if(!sVals.length)return null;
  var lo=Math.min.apply(null,sVals),hi=Math.max.apply(null,sVals);
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
function computeStats(vals){if(!vals||!vals.length)return null;var s=vals.slice().sort(function(a,b){return a-b;});var n=s.length;var sum=s.reduce(function(a,b){return a+b;},0);var mean=sum/n;var med=n%2?s[Math.floor(n/2)]:(s[n/2-1]+s[n/2])/2;var vari=s.reduce(function(ac,v){var d=v-mean;return ac+d*d;},0)/n;return{min:s[0],max:s[n-1],median:med,stddev:Math.sqrt(vari),count:n};}
function renderStatsTable(stats,containerId,dec){var el=document.getElementById(containerId);if(!el)return;if(!stats){el.innerHTML='';return;}var d=dec||4;el.innerHTML='<table class="cat-tbl" style="width:auto;max-width:420px;margin-top:6px;font-size:12px"><thead><tr><th>Stat</th><th>Value</th></tr></thead><tbody><tr><td style="text-align:left">Count</td><td>'+stats.count+'</td></tr><tr><td style="text-align:left">Min</td><td>'+stats.min.toFixed(d)+'</td></tr><tr><td style="text-align:left">Max</td><td>'+stats.max.toFixed(d)+'</td></tr><tr><td style="text-align:left">Median</td><td>'+stats.median.toFixed(d)+'</td></tr><tr><td style="text-align:left">Std Dev</td><td>'+stats.stddev.toFixed(d)+'</td></tr></tbody></table>';}
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
  var maxC=Math.max.apply(null,counts)||1;
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
      var uMin=Math.min.apply(null,validMeds),uMax=Math.max.apply(null,validMeds);
      var uPad=(uMax-uMin)*0.1||1;uMin-=uPad;uMax+=uPad;
      var uRange=uMax-uMin;if(uRange===0)uRange=1;
      // Draw one dot per bin at bin center
      for(var bi=0;bi<n;bi++){
        if(binMeds[bi]!=null){
          var cx=pl+(bi+0.5)*bw;
          var cy=pt+cH-((binMeds[bi]-uMin)/uRange)*cH;
          var nw=binU[bi].length;
          p.push('<circle cx="'+cx.toFixed(1)+'" cy="'+cy.toFixed(1)+'" r="5" fill="#e67e22" stroke="#fff" stroke-width="1" opacity="0.85"><title>Bin '+bi+': '+nw+' die(s)\\nUPM Med: '+binMeds[bi].toFixed(2)+'%</title></circle>');
          p.push('<text x="'+cx.toFixed(1)+'" y="'+(cy-7).toFixed(1)+'" text-anchor="middle" font-size="12" fill="#d35400">'+binMeds[bi].toFixed(1)+'%</text>');
        }
      }
      // Overall UPM median horizontal dashed line
      if(ov.uMed!=null){
        var umy=pt+cH-((ov.uMed-uMin)/uRange)*cH;
        p.push('<line x1="'+pl+'" x2="'+(pl+cW)+'" y1="'+umy.toFixed(1)+'" y2="'+umy.toFixed(1)+'" stroke="#d35400" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.6"/>');
        p.push('<text x="'+(pl+cW+6)+'" y="'+(umy+4).toFixed(1)+'" text-anchor="start" font-size="13" fill="#d35400" font-weight="bold">Med:'+ov.uMed.toFixed(2)+'%</text>');
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
      p.push('<text x="'+(tx+4).toFixed(1)+'" y="'+(pt+20)+'" font-size="15" fill="#27ae60">Tgt:'+Number(tgt).toFixed(4)+'</text>');
    }
  }
  if(medVal!=null&&hi>lo){
    var mx=pl+(medVal-lo)/(hi-lo)*cW;
    if(mx>=pl-2&&mx<=pl+cW+2){
      p.push('<line x1="'+mx.toFixed(1)+'" x2="'+mx.toFixed(1)+'" y1="'+pt+'" y2="'+(pt+cH)+'" stroke="#8B4513" stroke-width="2.5" stroke-dasharray="5,3"/>');
      p.push('<text x="'+(mx+4).toFixed(1)+'" y="'+(pt+38)+'" font-size="17" fill="#8B4513" font-weight="bold">Med:'+medVal.toFixed(4)+'</text>');
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
    p.push('<text x="'+(pl+cW/2).toFixed(1)+'" y="'+(H-18)+'" text-anchor="middle" font-size="17" fill="#444">'+lo.toFixed(4)+'</text>');
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
    }else{
      // Fallback: wafer-level medians (CDYN has no die_pairs)
      var yv=r.cdyn&&r.cdyn[col]!=null?r.cdyn[col]:(r.medians&&r.medians[col]!=null?r.medians[col]:null);
      var xv=r.medians&&r.medians[uCol]!=null?r.medians[uCol]:null;
      if(yv!=null&&xv!=null&&yv>0)pts.push({x:xv,y:yv,m:grp});
    }
  });
  if(!pts.length){svg.innerHTML='';if(noteEl)noteEl.textContent='No paired die data.';return;}
  var xArr=pts.map(function(p){return p.x;});
  var yArr=pts.map(function(p){return p.y;});
  var xFilt=filterOutliers(xArr,5);
  var yFilt=filterOutliers(yArr,5);
  var xMin2=Math.min.apply(null,xFilt),xMax2=Math.max.apply(null,xFilt);
  var yMin2=Math.min.apply(null,yFilt),yMax2=Math.max.apply(null,yFilt);
  pts=pts.filter(function(p){return p.x>=xMin2&&p.x<=xMax2&&p.y>=yMin2&&p.y<=yMax2;});
  if(!pts.length){svg.innerHTML='';if(noteEl)noteEl.textContent='No data after filtering.';return;}
  var useLog=_SCATTER_Y_LOG&&pts.every(function(p){return p.y>0;});
  var xVals=pts.map(function(p){return p.x;});
  var yVals=pts.map(function(p){return p.y;});
  var xLo=Math.min.apply(null,xVals),xHi=Math.max.apply(null,xVals);
  var yLo=Math.min.apply(null,yVals),yHi=Math.max.apply(null,yVals);
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
      for(var di=0;di<dp.s.length;di++)allVals.push(dp.s[di]);
    }else{
      var h=r.hists[col];
      if(h&&h.edges.length>1){
        for(var bi=0;bi<h.counts.length;bi++){var mid=(h.edges[bi]+h.edges[bi+1])/2;for(var ci=0;ci<h.counts[bi];ci++)allVals.push(mid);}
      }else{var v=cfg.isCdyn?r.cdyn[col]:r.medians[col];if(v!=null)allVals.push(v);}
    }
  });
  allVals=filterOutliers(allVals.filter(function(v){return v>0;}),5);
  if(!allVals.length){drawSVG([],[],null,tgt,col,cfg.histSvg,false);renderStatsTable(null,cfg.statsTbl);if(ne)ne.textContent='No data.';return;}
  var lo=Math.min.apply(null,allVals),hi=Math.max.apply(null,allVals);
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
