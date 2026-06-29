"""_tab_sicc.py — SICC / UPM tab: HTML panel + JavaScript functions."""
from _tab_registry import Tab
from _dash_js_shared import build_dist_body_html

TAB_ID     = 'tab-sicc'
TAB_LABEL  = 'SICC'
TAB_ACTIVE = True   # This tab is shown first


def tab_html() -> str:
    chart_body = build_dist_body_html(
        scatter_svg='sicc-scatter-svg',
        scatter_title='sicc-scatter-title', scatter_note='sicc-scatter-note',
        dist_title='sicc-dist-title', hist_svg='upm-hist-svg',
        chart_note='upm-chart-note', stats_tbl='upm-stats-tbl',
        mini_upm_panel='sicc-mini-upm-panel', mini_upm_title='sicc-mini-upm-title',
        mini_upm_svg='sicc-mini-upm-svg',
        mini_upm_note='sicc-mini-upm-note',
        scatter_max_width='60%',
        hist_height='371',
    )
    return f'''
<div id="tab-sicc" class="tab-panel active">
  <div class="legend">
    <span class="ld" style="background:#fdecea;border:1px solid #e74c3c"></span>Over target
    <span class="ld" style="background:#fef9e7;border:1px solid #f39c12"></span>Within 5% of target
    <span class="ld" style="background:#eafaf1;border:1px solid #27ae60"></span>Under target
    &mdash; Click row to view distribution
    &nbsp;<button class="wfr-btn" onclick="selAll()">Select All Wafers</button>
    <button class="wfr-btn" onclick="clrAll()">Clear Selection</button>
    &nbsp;<button class="wfr-btn" onclick="showAllCats('sicc')">Show All Rows</button>
    <button class="wfr-btn" onclick="hideAllCats('sicc')">Hide All Rows</button>
    &nbsp;<button class="wfr-btn" onclick="exportTblCsv('sicc-head','sicc-body','sicc_table')" title="Export table to CSV">&#8681; Export CSV</button>
  </div>
  <div class="cat-legend" id="sicc-tab-legend"></div>
  <div class="side-layout">
    <div class="tbl-side" id="sicc-tbl-side">
      <div class="hm-wrap">
        <table class="hm-tbl"><thead id="sicc-head"></thead><tbody id="sicc-body"></tbody></table>
      </div>
    </div>
    <div class="h-splitter" id="sicc-dist-splitter" onmousedown="startSplit(event,'sicc-tbl-side',null,'sicc-tbl-w')"></div>
    <div class="dist-side" id="upm-dist-panel">
      <div class="dist-hdr">&#9998; Charts<button class="collapse-btn" onclick="toggleDistPanel('upm-dist-panel','sicc-dist-splitter')" title="Collapse/expand charts">&#9664;</button></div>
      <div id="upm-dist-body">
{chart_body}
      </div>
    </div>
  </div>
</div>
'''


def tab_js() -> str:
    return '''
function render_sicc(){
  var ai=SEL_WFR.size>0?Array.from(SEL_WFR):getFiltered();
  if(!ai.length){
    document.getElementById('sicc-head').innerHTML='';
    document.getElementById('sicc-body').innerHTML='<tr><td colspan="8" style="padding:14px;color:#7f8c8d">No data after filter.</td></tr>';
    return;
  }
  if(SICC_TBL_CFG&&SICC_TBL_CFG.length){
    var cats=_getCats(SICC_TBL_CFG);
    _buildCatLegend(cats,SICC_CAT_OFF,'sicc-tab-legend',render_sicc);
    var hdr='<tr><th class="sticky-l">Test</th><th>Actual Median (A)</th><th>Target (A)</th><th>Ratio</th><th>UPM Median (%)</th><th>UPM Target (%)</th></tr>';
    var body='',lastCat='';
    SICC_TBL_CFG.forEach(function(row){
      var cat=row[0],dispName=row[1],testName=row[2],upmCol=row[3]||'';
      if(SICC_CAT_OFF.has(cat))return;
      if(cat!==lastCat){
        body+='<tr class="cat-hdr"><td colspan="6" style="background:'+_catColor(cat)+';color:'+_catBorder(cat)+';border-left:4px solid '+_catBorder(cat)+'">'+esc(cat)+'</td></tr>';
        lastCat=cat;
      }
      var vals=ai.map(function(i){return ROWS[i].medians[testName];}).filter(function(v){return v!=null&&!isNaN(v);});
      var actual=medArr(vals);
      var tgt=TARGETS[testName.toUpperCase()]||null;
      var ratio=(actual!=null&&tgt!=null&&tgt!==0)?actual/tgt:null;
      var upmMed=null,upmTgt=null;
      if(upmCol){
        var uv=ai.map(function(i){return ROWS[i].medians[upmCol];}).filter(function(v){return v!=null&&!isNaN(v);});
        upmMed=medArr(uv);
        upmTgt=TARGETS[upmCol.toUpperCase()]||null;
      }
      var bg=_catColor(cat);
      var isSel=(testName===SEL_COL&&!IS_CDYN);
      body+='<tr class="'+(isSel?'sel-row':'')+'" style="background:'+bg+'" onclick="selCol(&quot;'+testName+'&quot;)">'; 
      body+='<td class="tn'+(isSel?' sel':'')+'" style="text-align:left;border-left:4px solid '+_catBorder(cat)+'">'+esc(dispName)+'</td>';
      body+='<td class="'+ccls(actual,tgt,false)+'">'+(actual!=null?actual.toFixed(4):'&#8212;')+'</td>';
      body+='<td class="tgt">'+(tgt!=null?tgt.toFixed(4):'&#8212;')+'</td>';
      body+='<td class="'+ratioCls(ratio)+'">'+(ratio!=null?ratio.toFixed(2):'&#8212;')+'</td>';
      body+='<td class="'+upmCls(upmMed,upmTgt)+'">'+(upmMed!=null?upmMed.toFixed(2):'&#8212;')+'</td>';
      body+='<td class="tgt">'+(upmTgt!=null?upmTgt.toFixed(2):'&#8212;')+'</td>';
      body+='</tr>';
    });
    document.getElementById('sicc-head').innerHTML=hdr;
    document.getElementById('sicc-body').innerHTML=body;
  }else{
    var totals=ai.map(function(i){return ROWS[i].total;}).filter(function(v){return v!=null&&!isNaN(v);});
    var nRows=totals.length?totals.reduce(function(a,b){return a+b;},0):null;
    var hdr='<tr><th class="sticky-l">Test</th><th>N&nbsp;Rows</th><th>Actual Median (A)</th><th>Target (A)</th><th>Multiple</th></tr>';
    var body='';
    SICC_COLS.forEach(function(col){
      var vals=ai.map(function(i){return ROWS[i].medians[col];}).filter(function(v){return v!=null&&!isNaN(v);});
      var actual=medArr(vals);
      var tgt=TARGETS[col.toUpperCase()];
      var ratio=(actual!=null&&tgt!=null&&tgt!==0)?actual/tgt:null;
      var isSel=(col===SEL_COL&&!IS_CDYN);
      body+='<tr class="'+(isSel?'sel-row':'')+'" onclick="selCol(&quot;'+col+'&quot;)" style="cursor:pointer">';
      body+='<td class="tn'+(isSel?' sel':'')+'">'+esc(col)+'</td>';
      body+='<td>'+(nRows!=null?Math.round(nRows).toLocaleString():'&#8212;')+'</td>';
      body+='<td class="'+ccls(actual,tgt,false)+'">'+(actual!=null?actual.toFixed(4):'&#8212;')+'</td>';
      body+='<td class="tgt">'+(tgt!=null?tgt.toFixed(4):'&#8212;')+'</td>';
      body+='<td class="'+ratioCls(ratio)+'">'+(ratio!=null?ratio.toFixed(2):'&#8212;')+'</td>';
      body+='</tr>';
    });
    document.getElementById('sicc-head').innerHTML=hdr;
    document.getElementById('sicc-body').innerHTML=body;
  }
  render_upm_dist();
}
// ── UPM Distribution (shown in SICC tab) ───────────────────────────────
function render_upm_dist(){
  var panel=document.getElementById('upm-dist-panel');
  var col=null;
  if(SEL_COL&&!IS_CDYN)col=SEL_COL;
  else if(SEL_COL&&(SICC_COLS.indexOf(SEL_COL)>=0||SICC_TBL_CFG.some(function(r){return r[2]===SEL_COL;})))col=SEL_COL;
  if(!col&&SICC_TBL_CFG&&SICC_TBL_CFG.length)col=SICC_TBL_CFG[0][2];
  if(!col&&SICC_COLS.length)col=SICC_COLS[0];
  if(!col&&UPM_DIST_COLS.length)col=UPM_DIST_COLS[0];
  if(!col){if(panel)panel.style.display='none';drawTabScatter([],null,'sicc-scatter-svg','sicc-scatter-title','sicc-scatter-note');drawMiniUpm([],null,false,'sicc-mini-upm-svg','sicc-mini-upm-title','sicc-mini-upm-note');return;}
  if(panel)panel.style.display='';
  var ai=getFiltered();var active=ai.filter(function(i){return SEL_WFR.has(i);});
  if(!active.length)active=ai;
  _renderDistBody(active,col,{isCdyn:false,histSvg:'upm-hist-svg',statsTbl:'upm-stats-tbl',noteEl:'upm-chart-note',distTitle:'sicc-dist-title',scatterSvg:'sicc-scatter-svg',scatterTitle:'sicc-scatter-title',scatterNote:'sicc-scatter-note',miniSvg:'sicc-mini-upm-svg',miniTitle:'sicc-mini-upm-title',miniNote:'sicc-mini-upm-note'});
}
registerTab('tab-sicc', render_sicc);
'''


build_tab = Tab(
    tab_id=TAB_ID,
    label=TAB_LABEL,
    active=TAB_ACTIVE,
    html_fn=tab_html,
    js_fn=tab_js,
)
