"""_tab_cdyn.py — CDYN tab: HTML panel + JavaScript functions."""
from _tab_registry import Tab
from _dash_js_shared import build_dist_body_html

TAB_ID     = 'tab-cdyn'
TAB_LABEL  = 'CDYN'
TAB_ACTIVE = False


def tab_html() -> str:
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


def tab_js() -> str:
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
      body+='<td class="'+ccls(actual,tgt,true)+'">'+(actual!=null?actual.toFixed(4):'&#8212;')+'</td>';
      body+='<td class="tgt">'+(tgt!=null?tgt.toFixed(4):'&#8212;')+'</td>';
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
      body+='<td class="'+ccls(actual,tgt,true)+'">'+(actual!=null?actual.toFixed(4):'&#8212;')+'</td>';
      body+='<td class="tgt">'+(tgt!=null?tgt.toFixed(4):'&#8212;')+'</td>';
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


build_tab = Tab(
    tab_id=TAB_ID,
    label=TAB_LABEL,
    active=TAB_ACTIVE,
    html_fn=tab_html,
    js_fn=tab_js,
)
