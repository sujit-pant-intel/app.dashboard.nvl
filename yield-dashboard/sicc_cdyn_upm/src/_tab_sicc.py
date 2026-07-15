"""_tab_sicc.py — SICC / UPM tab: HTML panel + JavaScript functions."""
from _tab_registry import Tab
from _dash_js_shared import build_dist_body_html
from _tab_summ import tab_html as _summ_html, tab_js as _summ_js

TAB_ID     = 'tab-sicc'
TAB_LABEL  = 'SICC'
TAB_ACTIVE = True   # This tab is shown first


def tab_html() -> str:
    from _dash_js_shared import _GROUP_BY_HTML_INLINE  # noqa
    return f'''
<div id="tab-sicc" class="tab-panel active">
  <div style="display:flex;align-items:center;gap:10px;padding:6px 10px;background:#f8f9fa;border-bottom:1px solid #dde;flex-wrap:wrap">
    <div style="display:flex;align-items:center;gap:6px">
      <button id="sicc-xy-sicc-btn" onclick="_setSiccScatterMode('sicc')" style="padding:7px 14px;font-size:13px;font-weight:bold;border:2px solid #2980b9;border-radius:5px;background:#2980b9;color:#fff;cursor:pointer;white-space:nowrap">&#128202; SICC</button>
      <select id="sicc-scatter-col-sel" style="font-size:11px;padding:4px 6px;border:1px solid #bdc3c7;border-radius:3px;background:#f8f9fa;color:#2c3e50" onchange="_onSiccSelChange()"></select>
    </div>
    <div style="display:flex;align-items:center;gap:6px">
      <button id="sicc-xy-cdyn-btn" onclick="_setSiccScatterMode('cdyn')" style="padding:7px 14px;font-size:13px;font-weight:bold;border:2px solid #27ae60;border-radius:5px;background:#ecf0f1;color:#27ae60;cursor:pointer;white-space:nowrap">&#128200; CDYN</button>
      <select id="cdyn-scatter-col-sel" style="display:none;font-size:11px;padding:4px 6px;border:1px solid #bdc3c7;border-radius:3px;background:#f8f9fa;color:#2c3e50" onchange="render_upm_dist()"></select>
    </div>
    <div style="display:flex;gap:4px;margin-left:auto">
      <button class="wfr-btn" onclick="selAll()">Select All Wafers</button>
      <button class="wfr-btn" onclick="clrAll()">Clear Selection</button>
    </div>
  </div>
  <div id="upm-dist-panel" style="flex:1;overflow-y:auto;padding:8px 10px">
    <div id="upm-dist-body">
      <div id="sicc-scatter-wrap" style="position:relative;resize:both;overflow:hidden;min-height:200px;min-width:300px;width:100%;height:420px;border:1px solid #eee;border-radius:4px;background:#fff">
        <div id="sicc-scatter-div" style="width:100%;height:100%"></div>
        <div style="position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#aaa 50%);border-radius:0 0 4px 0;opacity:0.5;pointer-events:none" title="Drag corner to resize"></div>
      </div>
      <div id="sicc-scatter-note" style="font-size:11px;color:#7f8c8d;margin:2px 0 4px"></div>
      <div style="overflow-x:auto;margin:4px 0 6px">
        <table style="border-collapse:collapse;font-size:11px;white-space:nowrap;min-width:600px">
          <thead id="sicc-stats-head"></thead><tbody id="sicc-stats-body"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>
'''


def tab_js() -> str:
    return '''
var _siccScatterMode='sicc';
var _siccSelCols=[];
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
  var b1=document.getElementById('sicc-xy-sicc-btn'),b2=document.getElementById('sicc-xy-cdyn-btn');
  var ssel=document.getElementById('sicc-scatter-col-sel');
  var csel=document.getElementById('cdyn-scatter-col-sel');
  if(b1){if(mode==='sicc'){b1.style.background='#2980b9';b1.style.color='#fff';}else{b1.style.background='#ecf0f1';b1.style.color='#2980b9';}}
  if(b2){if(mode==='cdyn'){b2.style.background='#27ae60';b2.style.color='#fff';}else{b2.style.background='#ecf0f1';b2.style.color='#27ae60';}}
  if(ssel)ssel.style.display=mode==='sicc'?'':'none';
  if(csel)csel.style.display=mode==='cdyn'?'':'none';
  if(mode==='cdyn'&&csel&&!csel._populated){csel._populated=true;CDYN_COLS.forEach(function(c){var o=document.createElement('option');o.value=c;o.textContent=c;csel.appendChild(o);});}
  render_upm_dist();
}
function _toggleSiccChart(sid){
  var el=document.getElementById(sid);if(!el)return;
  var show=el.style.display==='none';
  el.style.display=show?'':'none';
  if(show)render_upm_dist();
}
function _populateSiccChecks(){
  var ct=document.getElementById('sicc-col-checks');if(!ct||ct._populated)return;
  ct._populated=true;
  var cols=SICC_TBL_CFG&&SICC_TBL_CFG.length?SICC_TBL_CFG.map(function(r){return{v:r[2],l:r[1]||r[2]};})
    :SICC_COLS.map(function(c){return{v:c,l:c};});
  if(!cols.length)return;
  cols.forEach(function(col,idx){
    var lbl=document.createElement('label');
    lbl.style.cssText='display:inline-flex;align-items:center;gap:3px;font-size:11px;cursor:pointer;padding:2px 8px;border:1px solid #bdc3c7;border-radius:12px;background:#fff;white-space:nowrap;user-select:none';
    var cb=document.createElement('input');cb.type='checkbox';cb.value=col.v;
    if(idx===0){cb.checked=true;_siccSelCols=[col.v];}
    cb.onchange=function(){_onSiccColCheck();};
    lbl.appendChild(cb);lbl.appendChild(document.createTextNode(' '+col.l));
    ct.appendChild(lbl);
  });
}
function _onSiccColCheck(){
  var cks=document.querySelectorAll('#sicc-col-checks input[type=checkbox]:checked');
  _siccSelCols=Array.from(cks).map(function(cb){return cb.value;});
  if(!_siccSelCols.length){var f=document.querySelector('#sicc-col-checks input[type=checkbox]');if(f){f.checked=true;_siccSelCols=[f.value];}}
  render_upm_dist();
}
function _drawPlotlyScatterSicc(active,cols,isCdyn){
  var el=document.getElementById('sicc-scatter-div');
  if(!el||typeof Plotly==='undefined')return;
  if(!active.length||!cols.length){if(el._spl)Plotly.purge(el);el._spl=false;return;}
  var COLORS=['#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd','#8c564b','#e377c2','#17becf','#bcbd22','#aec7e8'];
  var traces=[];var ti=0;
  cols.forEach(function(col){
    /* Find UPM col for SICC mode */
    var upmCol=null;
    if(!isCdyn){
      var _cfg=(SICC_TBL_CFG||[]).find(function(r){return r[2]===col;});
      if(_cfg&&_cfg[3])upmCol=_cfg[3];
    }
    var groups={};
    active.forEach(function(i){
      var r=ROWS[i];
      var gk=XY_COLOR_BY.length?XY_COLOR_BY.map(function(f){return f==='lot'?(r.lot||'?'):f==='wafer'?(r.wafer||'?'):f==='material'?(r.material||'?'):f==='program'?(r.program||'?'):'?';}).join('/'):'All';
      if(!groups[gk])groups[gk]={x:[],y:[],t:[]};
      var yv=isCdyn?r.cdyn[col]:r.medians[col];
      var xv=(!isCdyn&&upmCol)?r.medians[upmCol]:null;
      if(isCdyn)xv=r.wafer||('W'+i);
      if(yv!=null&&!isNaN(yv)&&(isCdyn||xv!=null)){
        var wid=r.wafer||('W'+i);
        var tgt=isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
        var xLabel=isCdyn?wid:(upmCol?(xv!=null?xv.toFixed(2):'--'):'--');
        groups[gk].x.push(isCdyn?wid:(xv!=null?xv:0));groups[gk].y.push(yv);
        groups[gk].t.push('<b>'+col+'</b><br>Wafer: '+wid+'<br>'+(upmCol?'UPM%: '+xLabel+'<br>':'')+'SICC: '+yv.toFixed(4)+'<br>Target: '+(tgt?tgt.toFixed(4):'--')+'<br>Ratio: '+(tgt?(yv/tgt).toFixed(3):'--'));
      }
    });
    var gns=Object.keys(groups);
    gns.forEach(function(gn){
      var g=groups[gn];
      traces.push({type:'scatter',mode:'markers',name:cols.length>1?col+(gns.length>1?' ('+gn+')':''):gn,
        x:g.x,y:g.y,text:g.t,hoverinfo:'text',
        marker:{size:9,color:COLORS[ti%COLORS.length],opacity:0.85,line:{width:0.5,color:'#fff'}}});
      ti++;
    });
    var tgt=isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
    if(tgt&&active.length&&isCdyn){
      var xs=active.map(function(i){return ROWS[i].wafer||('W'+i);});
      traces.push({type:'scatter',mode:'lines',name:'Target ('+col+')',x:xs,y:xs.map(function(){return tgt;}),
        line:{color:'#e74c3c',dash:'dash',width:1.5},hoverinfo:'skip',showlegend:true});
    }
  });
  var xTitle=isCdyn?'Wafer':((SICC_TBL_CFG&&SICC_TBL_CFG.length&&SICC_TBL_CFG[0][3])?'UPM (%)':'Wafer');
  var layout={title:{text:cols.join(' | '),font:{size:12,color:'#2c3e50'}},
    xaxis:{title:xTitle,tickfont:{size:10}},
    yaxis:{title:isCdyn?'CDYN Value':'SICC (A)'},
    margin:{t:40,b:80,l:70,r:10},plot_bgcolor:'#fff',paper_bgcolor:'#fff',
    legend:{orientation:'h',x:0,y:1.1,font:{size:10}},hovermode:'closest'};
  var cfg={responsive:true,displayModeBar:true,modeBarButtonsToRemove:['lasso2d','select2d'],displaylogo:false};
  if(el._spl)Plotly.react(el,traces,layout,cfg);else{Plotly.newPlot(el,traces,layout,cfg);el._spl=true;}
}
function _renderSiccStats(active,cols,isCdyn){
  var hd=document.getElementById('sicc-stats-head'),bd=document.getElementById('sicc-stats-body');
  if(!hd||!bd)return;
  var th='padding:4px 10px;background:#2c3e50;color:#fff;font-size:11px;white-space:nowrap';
  var gbGroups=XY_COLOR_BY.length?XY_COLOR_BY.join('/'):'None';
  var gbCell='<td style="padding:3px 10px;border-bottom:1px solid #eee;font-size:11px;color:#555">'+esc(gbGroups)+'</td>';
  hd.innerHTML='<tr><th style="'+th+';text-align:left">Parameter</th><th style="'+th+';text-align:right">N</th><th style="'+th+';text-align:right">Median</th><th style="'+th+';text-align:right">Target</th><th style="'+th+';text-align:right">Ratio</th><th style="'+th+';text-align:right">Min</th><th style="'+th+';text-align:right">Max</th><th style="'+th+';text-align:right">Mean</th><th style="'+th+';text-align:right">Std</th><th style="'+th+';text-align:left">Group By</th></tr>';
  var body='';
  cols.forEach(function(col){
    var vals=active.map(function(i){return isCdyn?ROWS[i].cdyn[col]:ROWS[i].medians[col];}).filter(function(v){return v!=null&&!isNaN(v);});
    var med=medArr(vals),tgt=isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
    var ratio=(med!=null&&tgt&&tgt!==0)?med/tgt:null;
    var mn=vals.length?Math.min.apply(null,vals):null,mx=vals.length?Math.max.apply(null,vals):null;
    var mean=vals.length?vals.reduce(function(a,b){return a+b;},0)/vals.length:null,std=null;
    if(mean!=null&&vals.length>1){var sq=vals.reduce(function(s,v){return s+(v-mean)*(v-mean);},0);std=Math.sqrt(sq/(vals.length-1));}
    var over=ratio!=null&&ratio>1,warn=ratio!=null&&ratio>0.95&&ratio<=1;
    var td='padding:3px 10px;text-align:right;border-bottom:1px solid #eee;font-size:11px';
    body+='<tr><td style="'+td+';text-align:left;font-weight:bold">'+esc(col)+'</td>'
      +'<td style="'+td+'">'+vals.length+'</td>'
      +'<td style="'+td+(over?';background:#fdecea':warn?';background:#fef9e7':'')+'">'+(med!=null?med.toFixed(4):'--')+'</td>'
      +'<td style="'+td+'">'+(tgt!=null?tgt.toFixed(4):'--')+'</td>'
      +'<td style="'+td+(over?';background:#fdecea;color:#c0392b;font-weight:bold':warn?';background:#fef9e7':'')+'">'+(ratio!=null?ratio.toFixed(3):'--')+'</td>'
      +'<td style="'+td+'">'+(mn!=null?mn.toFixed(4):'--')+'</td>'
      +'<td style="'+td+'">'+(mx!=null?mx.toFixed(4):'--')+'</td>'
      +'<td style="'+td+'">'+(mean!=null?mean.toFixed(4):'--')+'</td>'
      +'<td style="'+td+'">'+(std!=null?std.toFixed(4):'--')+'</td>'
      +gbCell+'</tr>';
  });
  bd.innerHTML=body;
}
function _renderSiccHistOnly(active,col,isCdyn){
  if(!active.length||!col)return;
  var allVals=[];
  active.forEach(function(i){var r=ROWS[i],h=r.hists&&r.hists[col];
    if(h&&h.edges&&h.edges.length>1){for(var bi=0;bi<h.counts.length;bi++){var mid=(h.edges[bi]+h.edges[bi+1])/2;for(var ci=0;ci<h.counts[bi];ci++)allVals.push(mid);}}
    else{var v=isCdyn?r.cdyn[col]:r.medians[col];if(v!=null&&!isNaN(v))allVals.push(v);}});
  allVals=filterOutliers(allVals.filter(function(v){return v>0;}),5);
  var tgt=isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
  if(!allVals.length){drawSVG([],[],null,tgt,col,'upm-hist-svg',false);renderStatsTable(null,'upm-stats-tbl');return;}
  var lo=Math.min.apply(null,allVals),hi=Math.max.apply(null,allVals);
  if(lo===hi){var d=Math.abs(lo*0.05)||0.01;lo-=d;hi+=d;}
  var nb=Math.max(6,Math.min(30,Math.round(Math.sqrt(allVals.length))));
  var step=(hi-lo)/nb,edges=[],counts=[];
  for(var bi=0;bi<=nb;bi++)edges.push(lo+bi*step);
  for(var bi=0;bi<nb;bi++)counts.push(0);
  allVals.forEach(function(m){var idx=Math.min(nb-1,Math.floor((m-lo)/step));if(idx<0)idx=0;counts[idx]++;});
  var uov=(typeof _buildUpmOverlay!=='undefined')?_buildUpmOverlay(active,col,isCdyn):null;
  drawSVG(edges,counts,medArr(allVals),tgt,col,'upm-hist-svg',false,uov,isCdyn?'CDYN':'SICC');
  renderStatsTable(computeStats(allVals),'upm-stats-tbl',4);
  var te=document.getElementById('sicc-dist-title');if(te)te.textContent=col+(isCdyn?' CDYN':' SICC')+' Distribution';
  var ne=document.getElementById('upm-chart-note');if(ne)ne.textContent='Die distribution -- '+active.length+' wafer(s), '+allVals.length+' values';
}
function render_sicc(){
  _populateSiccChecks();
  var _sh=document.getElementById('sicc-head'),_sb=document.getElementById('sicc-body');
  if(_sh)_sh.innerHTML='';if(_sb)_sb.innerHTML='';
  render_upm_dist();
  render_summ_if_open();
}
function render_upm_dist(){
  _populateSiccChecks();
  var ai=getFiltered(),active=ai.filter(function(i){return SEL_WFR.has(i);});
  if(!active.length)active=ai;
  var isCdyn=_siccScatterMode==='cdyn',cols=[];
  if(isCdyn){
    var cs=document.getElementById('cdyn-scatter-col-sel'),cv=cs?cs.value:'';
    if(!cv&&CDYN_COLS.length)cv=CDYN_COLS[0];
    if(cv)cols=[cv];
  }else{
    cols=_siccSelCols.length?_siccSelCols:[];
    if(!cols.length){if(SICC_TBL_CFG&&SICC_TBL_CFG.length)cols=[SICC_TBL_CFG[0][2]];else if(SICC_COLS.length)cols=[SICC_COLS[0]];}
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
window._onSiccColCheck=_onSiccColCheck;
registerTab('tab-sicc', render_sicc);
''' + _summ_js()
build_tab = Tab(
    tab_id=TAB_ID,
    label=TAB_LABEL,
    active=TAB_ACTIVE,
    html_fn=tab_html,
    js_fn=tab_js,
)
