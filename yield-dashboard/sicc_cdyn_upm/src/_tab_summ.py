"""_tab_summ.py — All Medians (summary) tab: HTML panel + JavaScript functions."""
from _tab_registry import Tab

TAB_ID     = 'tab-summ'
TAB_LABEL  = 'All Medians'
TAB_ACTIVE = False


def tab_html() -> str:
    return '''
<div id="tab-summ" class="tab-panel">
  <div style="margin:4px 0 6px">
    <button class="wfr-btn" onclick="selAll()">Select All Wafers</button>
    <button class="wfr-btn" onclick="clrAll()">Clear Selection</button>
    &nbsp;<button class="wfr-btn" onclick="showAllCats('summ-sicc')">Show All SICC</button>
    <button class="wfr-btn" onclick="hideAllCats('summ-sicc')">Hide All SICC</button>
    &nbsp;<button class="wfr-btn" onclick="showAllCats('summ-cdyn')">Show All CDYN</button>
    <button class="wfr-btn" onclick="hideAllCats('summ-cdyn')">Hide All CDYN</button>
    &nbsp;<button class="wfr-btn" onclick="exportTblCsv('sicc-cat-head','sicc-cat-body','sicc_summary')" title="Export SICC summary to CSV">&#8681; Export SICC CSV</button>
    <button class="wfr-btn" onclick="exportTblCsv('cdyn-cat-head','cdyn-cat-body','cdyn_summary')" title="Export CDYN summary to CSV">&#8681; Export CDYN CSV</button>
    <span id="summ-row-info" style="font-size:11px;color:#7f8c8d;margin-left:8px"></span>
  </div>
  <h3 style="margin:4px 0 6px;font-size:13px;color:#2c3e50">SICC Summary (by Category)</h3>
  <div class="cat-legend" id="sicc-cat-legend"></div>
  <div class="cat-wrap"><table class="cat-tbl"><thead id="sicc-cat-head"></thead><tbody id="sicc-cat-body"></tbody></table></div>
  <h3 style="margin:18px 0 6px;font-size:13px;color:#2c3e50">CDYN Summary (by Category)</h3>
  <div class="cat-legend" id="cdyn-cat-legend"></div>
  <div class="cat-wrap"><table class="cat-tbl"><thead id="cdyn-cat-head"></thead><tbody id="cdyn-cat-body"></tbody></table></div>
</div>
'''


def tab_js() -> str:
    return '''
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
  // Collect unique categories in order
  var catOrder=[],catSet=new Set();
  cfg.forEach(function(row){
    if(!catSet.has(row[0])){catSet.add(row[0]);catOrder.push(row[0]);}
  });
  // Toggleable legend
  if(legEl){
    _buildCatLegend(catOrder,offSet,legendId,render_summ);
  }
  // Header
  var hdr='<tr><th style="text-align:left;min-width:200px">Test</th><th>Category</th><th>Actual Median</th><th>Target</th><th>Ratio</th><th>UPM Median (%)</th><th>UPM Target (%)</th></tr>';
  if(headEl)headEl.innerHTML=hdr;
  // Body
  var body='';
  var lastCat='';
  cfg.forEach(function(row){
    var cat=row[0], dispName=row[1], testName=row[2], upmCol=row[3];
    if(offSet&&offSet.has(cat))return;
    // Category separator
    if(cat!==lastCat){
      body+='<tr class="cat-hdr"><td colspan="7" style="background:'+_catColor(cat)+';color:'+_catBorder(cat)+';border-left:4px solid '+_catBorder(cat)+'">'+esc(cat)+'</td></tr>';
      lastCat=cat;
    }
    // Compute median of the matching test across selected wafers
    var actual=null, tgt=null, ratio=null, upmMed=null, upmTgt=null;
    if(isCdyn){
      var vals=ai.map(function(i){return ROWS[i].cdyn[testName];}).filter(function(v){return v!=null&&!isNaN(v);});
      actual=medArr(vals);
      tgt=CDYN_TARGETS[testName]||null;
    }else{
      var vals=ai.map(function(i){return ROWS[i].medians[testName];}).filter(function(v){return v!=null&&!isNaN(v);});
      actual=medArr(vals);
      tgt=TARGETS[testName.toUpperCase()]||null;
    }
    ratio=(actual!=null&&tgt!=null&&tgt!==0)?actual/tgt:null;
    // UPM
    if(upmCol){
      var uv=ai.map(function(i){return ROWS[i].medians[upmCol];}).filter(function(v){return v!=null&&!isNaN(v);});
      upmMed=medArr(uv);
      upmTgt=TARGETS[upmCol.toUpperCase()]||null;
    }
    var bg=_catColor(cat);
    var clickFn=isCdyn?'selCdyn':'selCol';
    body+='<tr style="background:'+bg+';cursor:pointer" onclick="'+clickFn+'(&quot;'+testName+'&quot;)">';
    body+='<td style="text-align:left;border-left:4px solid '+_catBorder(cat)+'">'+esc(dispName)+'</td>';
    body+='<td style="color:#7f8c8d;font-size:11px;text-align:center">'+esc(cat)+'</td>';
    body+='<td class="'+ccls(actual,tgt,isCdyn)+'">'+(actual!=null?actual.toFixed(4):'&#8212;')+'</td>';
    body+='<td class="tgt">'+(tgt!=null?tgt.toFixed(4):'&#8212;')+'</td>';
    body+='<td class="'+ratioCls(ratio)+'">'+(ratio!=null?ratio.toFixed(2):'&#8212;')+'</td>';
    body+='<td class="'+upmCls(upmMed,upmTgt)+'">'+(upmMed!=null?upmMed.toFixed(2):'&#8212;')+'</td>';
    body+='<td class="tgt">'+(upmTgt!=null?upmTgt.toFixed(2):'&#8212;')+'</td>';
    body+='</tr>';
  });
  if(bodyEl)bodyEl.innerHTML=body;
}
registerTab('tab-summ', render_summ);
'''


build_tab = Tab(
    tab_id=TAB_ID,
    label=TAB_LABEL,
    active=TAB_ACTIVE,
    html_fn=tab_html,
    js_fn=tab_js,
)
