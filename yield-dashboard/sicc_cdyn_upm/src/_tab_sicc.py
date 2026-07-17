"""_tab_sicc.py — SICC / UPM tab: HTML panel + JavaScript functions."""
from _tab_registry import Tab
from _dash_js_shared import build_dist_body_html
from _tab_summ import tab_html as _summ_html, tab_js as _summ_js

TAB_ID     = 'tab-sicc'
TAB_LABEL  = 'Parametric Analysis'
TAB_ACTIVE = True   # This tab is shown first


def tab_html() -> str:
    from _dash_js_shared import _GROUP_BY_HTML_INLINE  # noqa
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
    </div>
  </div>
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
    <button onclick="_siccExportXyCsv()" title="Download XY chart data as CSV" style="font-size:12px;padding:3px 7px;border:1px solid #bbb;border-radius:3px;background:#fff;cursor:pointer;white-space:nowrap">&#11015; XY CSV</button>
    <span style="color:#aaa">|</span>
    <span style="font-weight:bold;color:#555">X:</span>
    <input id="sicc-xmin" type="number" placeholder="min" title="X-axis minimum (leave blank for auto)" oninput="render_upm_dist()" style="width:62px;font-size:11px;padding:2px 4px;border:1px solid #bbb;border-radius:3px">
    <input id="sicc-xmax" type="number" placeholder="max" title="X-axis maximum (leave blank for auto)" oninput="render_upm_dist()" style="width:62px;font-size:11px;padding:2px 4px;border:1px solid #bbb;border-radius:3px">
    <span style="font-weight:bold;color:#555">Y:</span>
    <input id="sicc-ymin" type="number" placeholder="min" title="Y-axis minimum (leave blank for auto)" oninput="render_upm_dist()" style="width:62px;font-size:11px;padding:2px 4px;border:1px solid #bbb;border-radius:3px">
    <input id="sicc-ymax" type="number" placeholder="max" title="Y-axis maximum (leave blank for auto)" oninput="render_upm_dist()" style="width:62px;font-size:11px;padding:2px 4px;border:1px solid #bbb;border-radius:3px">
    <button onclick="_siccResetAxisRange()" title="Reset axis ranges to auto" style="font-size:11px;padding:2px 6px;border:1px solid #bbb;border-radius:3px;background:#fff;cursor:pointer">&#8635; Reset</button>
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


def tab_js() -> str:
    return '''
var _siccScatterMode='sicc';
var _siccSelCols=[];          /* kept for backward compat */
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
var SICC_TREND='ols';              /* 'ols' | 'ts' | 'none' */
var SICC_Y_LOG=false;
var SICC_CROSSHAIR=false;
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
  render_upm_dist();
}
window._siccToggleAll=_siccToggleAll;
function _toggleSiccRow(key){
  if(SICC_CHECKED_ROWS.has(key))SICC_CHECKED_ROWS.delete(key);else SICC_CHECKED_ROWS.add(key);
  var col=key.split('||')[0];
  var isCdyn=_siccScatterMode==='cdyn';
  var s=isCdyn?CDYN_SEL_COLS:SICC_SEL_COLS;
  var anyChecked=_siccAllRowKeys.some(function(k){return k.indexOf(col+'||')===0&&SICC_CHECKED_ROWS.has(k);});
  if(!anyChecked)s.delete(col);else s.add(col);
  /* Update col panel checkbox list and button visually */
  var panelId=isCdyn?'cdyn-col-panel':'sicc-col-panel';
  _colDdRenderList(panelId,s,isCdyn,'');
  _colDdUpdateBtn(panelId,s);
  /* Sync → parameter table */
  if(typeof _ptSyncFromSicc!=='undefined'){_ptSyncFromSicc();if(typeof _ptRender!=='undefined')_ptRender();}
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
  var b1=document.getElementById('sicc-xy-sicc-btn'),b2=document.getElementById('sicc-xy-cdyn-btn');
  var sp=document.getElementById('sicc-col-panel'),cp=document.getElementById('cdyn-col-panel');
  if(b1){if(mode==='sicc'){b1.style.background='#2980b9';b1.style.color='#fff';}else{b1.style.background='#ecf0f1';b1.style.color='#2980b9';}}
  if(b2){if(mode==='cdyn'){b2.style.background='#27ae60';b2.style.color='#fff';}else{b2.style.background='#ecf0f1';b2.style.color='#27ae60';}}
  if(sp)sp.style.display=mode==='sicc'?'inline-block':'none';
  if(cp)cp.style.display=mode==='cdyn'?'inline-block':'none';
  if(mode==='cdyn'&&cp&&!cp._built){
    var dcols=CDYN_COLS.map(function(c){return{v:c,l:c};});
    _buildColPanel('cdyn-col-panel',dcols,CDYN_SEL_COLS,true);
  }
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
  var COLORS=['#1f77b4','#ff7f0e','#d62728','#9467bd','#8c564b','#e377c2','#00c853','#f39c12','#2e4057','#a93226'];
  var traces=[];var ti=0;var pts_have_upm=false;
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
      if(!_siccAllRowKeys.includes(rowKey)){_siccAllRowKeys.push(rowKey);SICC_CHECKED_ROWS.add(rowKey);}
      if(!SICC_CHECKED_ROWS.has(rowKey))return;
      var g=groups[gn];
      var col2=COLORS[ti%COLORS.length];
      traces.push({type:'scatter',mode:'markers',name:cols.length>1?col+(groupOrder.length>1?' ('+gn+')':''):gn,
        x:g.x,y:g.y,text:g.t,hoverinfo:'text',
        marker:{size:4,color:col2,opacity:0.75,line:{width:0.5,color:'#fff'}}});
      /* Trend line */
      if(SICC_TREND!=='none'&&g.x.length>=2){
        var numXs=g.x.filter(function(v){return typeof v==='number';});
        var numYs=[];g.x.forEach(function(v,k){if(typeof v==='number')numYs.push(g.y[k]);});
        if(numXs.length>=2){
          var reg=SICC_TREND==='ols'?_siccOLS(numXs,numYs):_siccTS(numXs,numYs);
          if(reg){
            var xmin2=Math.min.apply(null,numXs),xmax2=Math.max.apply(null,numXs);
            var tx=[xmin2,xmax2],ty=[reg.slope*xmin2+reg.intercept,reg.slope*xmax2+reg.intercept];
            traces.push({type:'scatter',mode:'lines',name:'Trend ('+gn+')',x:tx,y:ty,
              line:{color:col2,dash:'dot',width:1.5},hoverinfo:'skip',showlegend:false});
          }
        }
      }
      ti++;
    });
    var tgt=isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
    if(tgt&&active.length){
      var upmColT=_getUpmCol(col);
      var xs;
      if(pts_have_upm){
        /* Use actual UPM X range from plotted scatter points for the target line */
        var allXu=[];
        traces.forEach(function(tr){if(tr.mode==='markers'&&tr.x)tr.x.forEach(function(v){if(typeof v==='number')allXu.push(v);});});
        if(allXu.length){var xlo=Math.min.apply(null,allXu),xhi=Math.max.apply(null,allXu);xs=[xlo,xhi];}
      }
      if(!xs||!xs.length){
        xs=active.map(function(i){var r=ROWS[i];return(!isCdyn&&upmColT)?r.medians[upmColT]:r.wafer||('W'+i);});
        xs=xs.filter(function(x){return x!=null;});
      }
      if(xs.length)traces.push({type:'scatter',mode:'lines',name:'target',x:xs,y:xs.map(function(){return tgt;}),
        line:{color:'#e74c3c',dash:'dash',width:1.5},hoverinfo:'skip',showlegend:false});
    }
  });
  /* Check if any data was plotted with UPM% on x-axis */
  var hasUpmX=pts_have_upm||(!isCdyn&&_getUpmCol(cols[0]||''));
  var xTitle=hasUpmX?'UPM (%)':'Wafer';
  var yTitle=isCdyn?'CDYN (nF)':'SICC (A)';
  /* Per-trace: show only x/y in hover */
  traces.forEach(function(tr){if(tr.type==='scatter'&&tr.mode==='markers')tr.hovertemplate='<b>X:</b> %{x}<br><b>Y:</b> %{y}<extra></extra>';});
  /* Compute Y range from the actual plotted marker points (die-level data) + targets */
  var _yAll=[];
  traces.forEach(function(tr){
    if(tr.mode==='markers'&&tr.y){tr.y.forEach(function(v){if(v!=null&&isFinite(v)&&v>0)_yAll.push(v);});}
  });
  cols.forEach(function(col){
    var tgtV=isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
    if(tgtV!=null&&isFinite(tgtV)&&tgtV>0)_yAll.push(tgtV);
  });
  var _yAxisRange=null;
  if(_yAll.length){
    var _yMin=Math.min.apply(null,_yAll),_yMax=Math.max.apply(null,_yAll);
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
    var _yCurLo=_yAxisRange?_yAxisRange[0]:(_yAll.length?_toYAxis(Math.min.apply(null,_yAll)):0);
    var _yCurHi=_yAxisRange?_yAxisRange[1]:(_yAll.length?_toYAxis(Math.max.apply(null,_yAll)):1);
    _yAxisRange=[_yMinOvr!=null?_toYAxis(_yMinOvr):_yCurLo,_yMaxOvr!=null?_toYAxis(_yMaxOvr):_yCurHi];
  }
  var spikeOpts=SICC_CROSSHAIR?{showspikes:true,spikemode:'across',spikedash:'solid',spikecolor:'#111',spikethickness:1.5,spikeSnap:'cursor'}:{showspikes:false};
  var _yAxisCfg={title:{text:yTitle,font:{size:12}},tickfont:{size:10},type:SICC_Y_LOG?'log':'linear'};
  if(_yAxisRange)_yAxisCfg.range=_yAxisRange;else _yAxisCfg.autorange=true;
  var _xAxisCfg={title:{text:xTitle,font:{size:12}},tickfont:{size:10}};
  if(_xAxisRange&&_xAxisRange[0]!=null&&_xAxisRange[1]!=null){_xAxisCfg.range=_xAxisRange;}else{_xAxisCfg.autorange=true;}
  /* Build annotations: one label per target line, placed at the right edge */
  var _annotations=[];
  cols.forEach(function(col){
    var tgtV=isCdyn?(CDYN_TARGETS[col]||null):(TARGETS[col.toUpperCase()]||null);
    if(tgtV==null||!isFinite(tgtV))return;
    _annotations.push({xref:'paper',yref:'y',x:1.01,y:tgtV,xanchor:'left',yanchor:'middle',
      text:'<b>'+Number(tgtV).toFixed(4)+'</b>',showarrow:false,
      font:{size:10,color:'#e74c3c'}});
  });
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
  /* Always use newPlot to guarantee Y-axis rescales to current data */
  if(el._spl)Plotly.purge(el);
  Plotly.newPlot(el,traces,layout,cfg);el._spl=true;
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
      var mn=vals.length?Math.min.apply(null,vals):null,mx=vals.length?Math.max.apply(null,vals):null;
      var mean=vals.length?vals.reduce(function(a,b){return a+b;},0)/vals.length:null,std=null;
      if(mean!=null&&vals.length>1){var sq=vals.reduce(function(s,v){return s+(v-mean)*(v-mean);},0);std=Math.sqrt(sq/(vals.length-1));}
      var over=ratio!=null&&ratio>1,warn=ratio!=null&&ratio>0.95&&ratio<=1;
      var borderTop=gi===0?';border-top:2px solid #bcd':'';
      var rowKey=_siccRowKey(col,gk);
      /* Ensure key is registered & checked by default */
      if(!_siccAllRowKeys.includes(rowKey)){_siccAllRowKeys.push(rowKey);SICC_CHECKED_ROWS.add(rowKey);}
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
        +'<td style="'+td+borderTop+(over?';background:#fdecea':warn?';background:#fef9e7':'')+'">'+(med!=null?med.toFixed(4):'--')+'</td>'
        +'<td style="'+td+borderTop+'">'+(tgt!=null?tgt.toFixed(4):'--')+'</td>'
        +'<td style="'+td+borderTop+(over?';background:#fdecea;color:#c0392b;font-weight:bold':warn?';background:#fef9e7':'')+'">'+(ratio!=null?ratio.toFixed(3):'--')+'</td>'
        +'<td style="'+td+borderTop+'">'+(upmMed!=null?upmMed.toFixed(2)+'%':'--')+'</td>'
        +'<td style="'+td+borderTop+'">'+(mn!=null?mn.toFixed(4):'--')+'</td>'
        +'<td style="'+td+borderTop+'">'+(mx!=null?mx.toFixed(4):'--')+'</td>'
        +'<td style="'+td+borderTop+'">'+(mean!=null?mean.toFixed(4):'--')+'</td>'
        +'<td style="'+td+borderTop+'">'+(std!=null?std.toFixed(4):'--')+'</td>'
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
  var isCdyn=_siccScatterMode==='cdyn',cols=[];
  if(isCdyn){
    if(!document.getElementById('cdyn-col-panel')||!document.getElementById('cdyn-col-panel')._built){
      var dcols2=CDYN_COLS.map(function(c){return{v:c,l:c};});
      _buildColPanel('cdyn-col-panel',dcols2,CDYN_SEL_COLS,true);
    }
    cols=Array.from(CDYN_SEL_COLS);
    if(!cols.length&&CDYN_COLS.length){CDYN_SEL_COLS.add(CDYN_COLS[0]);cols=[CDYN_COLS[0]];}
  }else{
    cols=Array.from(SICC_SEL_COLS);
    if(!cols.length&&(SICC_TBL_CFG&&SICC_TBL_CFG.length||SICC_COLS.length)){
      var fc=SICC_TBL_CFG&&SICC_TBL_CFG.length?SICC_TBL_CFG[0][2]:SICC_COLS[0];
      SICC_SEL_COLS.add(fc);cols=[fc];
    }
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
''' + _summ_js()
build_tab = Tab(
    tab_id=TAB_ID,
    label=TAB_LABEL,
    active=TAB_ACTIVE,
    html_fn=tab_html,
    js_fn=tab_js,
)
