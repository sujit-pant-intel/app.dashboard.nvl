"""_filter_lot_wafer.py — Shared Filter-by-Lot/Wafer CSS and JS.

Single source of truth for the filter-table styles and the column-filter
dropdown panel, used by both:

  • yld/src/bin_distribution_html.py          (master — defines the visual style)
  • sicc_cdyn_upm/src/_dash_frame.py          (CSS, table class)
  • sicc_cdyn_upm/src/_dash_js_shared.py      (JS, complete rFilter/toggleRow)

HOW TO USE
----------
Both consumers import:

    from _filter_lot_wafer import FILTER_TABLE_CSS, FILTER_DD_JS, make_filter_js

``FILTER_TABLE_CSS``  — append to your page CSS string.
``FILTER_DD_JS``      — inject into the page <script> providing ``_ftDdCreate(opts)``.
``make_filter_js(on_change_calls, sel_var, toggle_fn, metric_cols=None, extra_text_cols=None)``
                      — returns the COMPLETE filter JS (rFilter, sortFilter,
                        toggleRow, selectAllRows, clearRows, ftDdOpen).
                        Parameterized so all dashboards share the exact same logic.
                        ``metric_cols`` / ``extra_text_cols`` let a consumer swap in
                        its own sortable numeric columns / extra text columns instead
                        of the default FF% / FF+DF% / Total (see docstring below).

DATA CONTRACT
-------------
All dashboards must provide a ``DATA`` JS object with:
  { rows: [...],        // array of row objects
    hasMaterial: bool,  // true if rows have non-empty material field
    hasDate:     bool,  // true if rows have non-empty date field
    hasUpmMed:   bool,  // true if rows have upmMed array
  }

Each row must have: program, lot, wafer, material, date, upmMed (list), plus
whatever fields the metric_cols/extra_text_cols reference. The default
metric_cols needs: binCounts (dict), total.
"""

# ── Shared CSS (bin_distribution_html.py is the master definition) ───────────
FILTER_TABLE_CSS = (
    '.ftw{overflow-x:auto;max-height:calc(100vh - 320px);overflow-y:auto}\n'
    '.ftbl{border-collapse:collapse;font-size:13px;white-space:nowrap;width:100%}\n'
    '.ftbl th{background:#2c3e50;color:#ecf0f1;padding:5px 9px;text-align:left;'
    'position:sticky;top:0;z-index:1}\n'
    '.ftbl td{padding:4px 9px;border-bottom:1px solid #eee;text-align:left}\n'
    '.flt-btn{background:none;border:none;color:#aed6f1;cursor:pointer;font-size:11px;'
    'padding:0 0 0 4px;vertical-align:middle;opacity:.85}\n'
    '.flt-btn:hover{opacity:1;color:#fff}\n'
    '.flt-btn.active{color:#f1c40f!important;opacity:1}\n'
    '.dd-panel{position:fixed;background:#fff;border:1px solid #aaa;border-radius:4px;'
    'box-shadow:0 4px 16px rgba(0,0,0,.18);z-index:25000;min-width:180px;max-width:280px;'
    'font-family:Arial,sans-serif;font-size:12px;color:#2c3e50}\n'
    '.dd-panel .dd-search{width:100%;box-sizing:border-box;padding:5px 8px;border:none;'
    'border-bottom:1px solid #ddd;font-size:12px;outline:none}\n'
    '.dd-panel .dd-acts{display:flex;gap:4px;padding:4px 6px;border-bottom:1px solid #eee}\n'
    '.dd-panel .dd-acts button{flex:1;padding:2px 6px;font-size:11px;cursor:pointer;'
    'border:1px solid #bdc3c7;background:#ecf0f1;border-radius:3px}\n'
    '.dd-panel .dd-list{max-height:200px;overflow-y:auto;padding:4px 0}\n'
    '.dd-panel .dd-item{display:flex;align-items:center;gap:6px;padding:3px 10px;cursor:pointer}\n'
    '.dd-panel .dd-item:hover{background:#eaf0fb}\n'
    '.dd-panel .dd-item input{margin:0;cursor:pointer}\n'
    '.dd-panel .dd-footer{padding:4px 8px;border-top:1px solid #eee;text-align:right}\n'
    '.dd-panel .dd-footer button{padding:3px 12px;font-size:11px;cursor:pointer;'
    'background:#2c3e50;color:#fff;border:none;border-radius:3px}\n'
    '.fr{cursor:pointer;transition:background .1s}\n'
    '.fr:hover td{background:#f0f4ff}\n'
    '.frs td{background:#d6eaff!important;font-weight:bold}\n'
)

# ── Shared dropdown filter JS (bin_distribution_html.py is the master) ───────
# Provides _ftDdCreate(opts) — a self-contained column-filter dropdown.
#
# opts = {
#   btn      : the button element that triggered the open (used for positioning),
#   allVals  : Array of all distinct string values for this column,
#   checked  : Set of currently checked values,
#   onApply  : function(checkedSet) — called when the user clicks OK or clicks outside.
# }
#
# Both bin_distribution_html.py (IC.ftDdOpen) and sicc_cdyn_upm (tblFtOpen) call
# this function — they only differ in how they compute allVals / checked and in
# what onApply does.
FILTER_DD_JS = r"""
// ── Shared filter dropdown (_filter_lot_wafer.py) ────────────────────────────
var _ftDdInst_=null;
function _ftDdCreate(opts){
  if(_ftDdInst_){_ftDdClose_();}
  var allVals=opts.allVals,checked=opts.checked,onApply=opts.onApply,btn=opts.btn;
  var panel=document.createElement('div');
  panel.className='dd-panel';
  panel.innerHTML='<input class="dd-search" placeholder="Search\u2026">'
    +'<div class="dd-acts"><button>Select All</button><button>Clear</button></div>'
    +'<div class="dd-list" id="_flw_ddl"></div>'
    +'<div class="dd-footer"><button>OK</button></div>';
  document.body.appendChild(panel);
  var r=btn.getBoundingClientRect();
  panel.style.top=(r.bottom+2+window.scrollY)+'px';
  panel.style.left=Math.min(r.left,window.innerWidth-210)+'px';
  function renderList(vals){
    var list=document.getElementById('_flw_ddl');if(!list)return;
    list.innerHTML=vals.map(function(v){
      var c=checked.has(v)?' checked':'';
      var sv=String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
      return '<label class="dd-item"><input type="checkbox"'+c+' data-val="'+sv+'">'+sv+'</label>';
    }).join('');
    list.querySelectorAll('input').forEach(function(inp){
      inp.onchange=function(){if(inp.checked)checked.add(inp.dataset.val);else checked.delete(inp.dataset.val);};
    });
  }
  _ftDdInst_={panel:panel,apply:function(){onApply(checked);_ftDdClose_();}};
  renderList(allVals);
  panel.querySelector('.dd-search').oninput=function(){
    var q=(this.value||'').toLowerCase();
    renderList(q?allVals.filter(function(v){return String(v).toLowerCase().indexOf(q)>=0;}):allVals);
  };
  var acts=panel.querySelectorAll('.dd-acts button');
  acts[0].onclick=function(){allVals.forEach(function(v){checked.add(v);});renderList(allVals);};
  acts[1].onclick=function(){checked.clear();renderList(allVals);};
  panel.querySelector('.dd-footer button').onclick=function(){_ftDdInst_.apply();};
  setTimeout(function(){document.addEventListener('mousedown',_ftDdOutside_);},0);
}
function _ftDdClose_(){
  document.removeEventListener('mousedown',_ftDdOutside_);
  if(_ftDdInst_&&_ftDdInst_.panel&&_ftDdInst_.panel.parentNode)
    _ftDdInst_.panel.parentNode.removeChild(_ftDdInst_.panel);
  _ftDdInst_=null;
}
function _ftDdOutside_(e){if(_ftDdInst_&&!_ftDdInst_.panel.contains(e.target)){_ftDdInst_.apply();}}
"""

import json as _json

# ── Default metric columns (bin_distribution_html.py's FF% / FF+DF% / Total) ─
# get(row)    → number used both for display and for sorting (0-safe fallback)
# fmt(v, row) → display string (may render '—' for the zero-total case)
_DEFAULT_METRIC_COLS = [
    {
        'key': 'ff', 'label': 'FF%',
        'get': "var bc=row.binCounts||{};return row.total>0?"
               "(((bc['1']||0)+(bc['2']||0))/row.total*100):0;",
        'fmt': "return row.total>0?v.toFixed(1)+'%':'\\u2014';",
    },
    {
        'key': 'ffdf', 'label': 'FF+DF%',
        'get': "var bc=row.binCounts||{};var ff=(bc['1']||0)+(bc['2']||0);"
               "return row.total>0?((ff+(bc['3']||0)+(bc['4']||0))/row.total*100):0;",
        'fmt': "return row.total>0?v.toFixed(1)+'%':'\\u2014';",
    },
    {
        'key': 'total', 'label': 'Total',
        'get': "return row.total||0;",
        'fmt': "return v.toLocaleString();",
    },
]


def _metric_entry_js(m):
    return (
        '{k:' + _json.dumps(m['key']) + ',l:' + _json.dumps(m['label'])
        + ',get:function(row){' + m['get'] + '}'
        + ',fmt:function(v,row){' + m['fmt'] + '}}'
    )


# ── Complete filter JS template ───────────────────────────────────────────────
# Placeholders substituted by make_filter_js():
#   __SEL__            → JS Set variable name  (e.g. 'sR' or 'SEL_WFR')
#   __FN_NS__          → namespace prefix for onclick helpers (e.g. 'IC.' or '')
#   __TOGGLE_FN__      → full toggleRow reference (e.g. 'IC.toggleRow' or 'toggleRow')
#   __ON_CHANGE__      → JS calls after any filter/selection change
#   __EXTRA_TEXT_DEFS__→ JS array literal [{k:...,l:...}, ...] of extra dropdown-filterable
#                        text columns rendered after MaterialType (e.g. Stepping)
#   __METRICS__        → JS array literal of {k,l,get,fmt} sortable numeric columns
#                        rendered after Date Tested (default: FF% / FF+DF% / Total)
_FILTER_COMPLETE_JS_TEMPLATE = r"""
// ── Filter-by-Lot/Wafer complete JS (master: bin_distribution_html.py) ──────
var _ftDdState={};
var _ftSortCol=null,_ftSortDir=-1;
var _ftIdxs=DATA.rows.map(function(_,i){return i;});
var _ftLR=-1;
var _FT_EXTRA_TXT=__EXTRA_TEXT_DEFS__;
var _FT_METRICS=__METRICS__;
function _ftTextDefs(){
  var d=[{k:'program',l:'TestProgram'},{k:'lot',l:'Lot'},{k:'wafer',l:'Wafer'}];
  if(DATA.hasMaterial)d.push({k:'material',l:'MaterialType'});
  _FT_EXTRA_TXT.forEach(function(e){d.push(e);});
  return d;
}
function _ftRowTextVals(row){
  return _ftTextDefs().map(function(d){return row[d.k]!=null?row[d.k]:'';});
}
function _visibleFtIdxs(){
  var vis=[];
  _ftIdxs.forEach(function(i){
    var row=DATA.rows[i];
    var cols=_ftRowTextVals(row);
    var ok=Object.keys(_ftDdState).every(function(ci){var s=_ftDdState[ci];return !s||s.has(String(cols[parseInt(ci)]||''));});
    if(ok)vis.push(i);
  });
  return vis;
}
function _buildFilterThead(){
  var th=document.getElementById('filter-thead');if(!th||th.innerHTML)return;
  var h='<tr>';
  _ftTextDefs().forEach(function(d,i){
    h+='<th>'+d.l+' <button class="flt-btn" id="ft-fb-'+i+'" onclick="event.stopPropagation();__FN_NS__ftDdOpen('+i+',this)" title="Filter">&#9660;</button></th>';
  });
  if(DATA.hasUpmMed)h+='<th class="num" onclick="event.stopPropagation();__FN_NS__sortFilter(\'upmmed\')" style="cursor:pointer">UPM (Med) <span id="ft-sh-upmmed"></span></th>';
  if(DATA.hasDate)h+='<th onclick="event.stopPropagation();__FN_NS__sortFilter(\'date\')" style="cursor:pointer">Date Tested <span id="ft-sh-date"></span></th>';
  _FT_METRICS.forEach(function(m){
    h+='<th class="num" onclick="event.stopPropagation();__FN_NS__sortFilter(\''+m.k+'\')" style="cursor:pointer">'+m.l+' <span id="ft-sh-'+m.k+'"></span></th>';
  });
  h+='</tr>';
  th.innerHTML=h;
}
function ftDdOpen(col,btn){
  var allVals=[];var seen=new Set();
  DATA.rows.forEach(function(row){
    var cols=_ftRowTextVals(row);
    var v=String(cols[col]||'');
    if(!seen.has(v)){seen.add(v);allVals.push(v);}
  });
  allVals.sort(function(a,b){return a.localeCompare(b);});
  var allowed=_ftDdState[col];
  var checked=allowed?new Set(allowed):new Set(allVals);
  _ftDdCreate({btn:btn,allVals:allVals,checked:checked,onApply:function(chk){
    _ftDdState[col]=(chk.size===allVals.length)?null:new Set(chk);
    var b=document.getElementById('ft-fb-'+col);if(b)b.classList.toggle('active',!!_ftDdState[col]);
    rFilter();
  }});
}
function sortFilter(col){
  if(_ftSortCol===col){_ftSortDir=-_ftSortDir;}else{_ftSortCol=col;_ftSortDir=-1;}
  rFilter();
}
function rFilter(){
  _buildFilterThead();
  var tbody=document.getElementById('filter-tbody');if(!tbody)return;
  _ftIdxs=DATA.rows.map(function(_,i){return i;});
  if(_ftSortCol){
    _ftIdxs.sort(function(a,b){
      var ra=DATA.rows[a],rb=DATA.rows[b];
      if(_ftSortCol==='date'){var av=ra.date||'',bv=rb.date||'';return _ftSortDir*(av<bv?-1:av>bv?1:0);}
      if(_ftSortCol==='upmmed'){
        var au=(ra.upmMed&&ra.upmMed[0]!=null)?ra.upmMed[0]:-Infinity,bu=(rb.upmMed&&rb.upmMed[0]!=null)?rb.upmMed[0]:-Infinity;
        return _ftSortDir*(au-bu);
      }
      var mm=null;
      for(var mi=0;mi<_FT_METRICS.length;mi++){if(_FT_METRICS[mi].k===_ftSortCol){mm=_FT_METRICS[mi];break;}}
      if(mm)return _ftSortDir*(mm.get(ra)-mm.get(rb));
      return 0;
    });
  }
  var html='';
  _ftIdxs.forEach(function(i){
    var row=DATA.rows[i];
    var cols=_ftRowTextVals(row);
    var show=Object.keys(_ftDdState).every(function(ci){
      var s=_ftDdState[ci];return !s||s.has(String(cols[parseInt(ci)]||''));
    });
    if(!show)return;
    var sel=__SEL__.has(i);
    html+='<tr class="fr'+(sel?' frs':'')+'" onclick="__TOGGLE_FN__('+i+',event)">';
    cols.forEach(function(c){html+='<td>'+esc(String(c||''))+'</td>';});
    if(DATA.hasUpmMed&&row.upmMed)(row.upmMed||[]).forEach(function(v){html+='<td class="num">'+(v!=null?v.toFixed(2):'\u2014')+'</td>';});
    if(DATA.hasDate)html+='<td>'+esc(row.date||'')+'</td>';
    _FT_METRICS.forEach(function(m){var v=m.get(row);html+='<td class="num">'+m.fmt(v,row)+'</td>';});
    html+='</tr>';
  });
  var sortKeys=['date','upmmed'].concat(_FT_METRICS.map(function(m){return m.k;}));
  sortKeys.forEach(function(k){
    var sh=document.getElementById('ft-sh-'+k);
    if(sh)sh.innerHTML=(_ftSortCol===k)?(_ftSortDir>0?'&#9650;':'&#9660;'):'';
  });
  tbody.innerHTML=html;
  var ri=document.getElementById('row-sel-info');
  if(ri)ri.textContent=__SEL__.size<DATA.rows.length?'('+__SEL__.size+'/'+DATA.rows.length+' selected)':'';
}
function toggleRow(idx,event){
  var vis=_visibleFtIdxs();
  if(event&&event.shiftKey&&_ftLR>=0){
    var a=vis.indexOf(idx),b=vis.indexOf(_ftLR);
    if(a<0||b<0){__SEL__.add(idx);}
    else{var lo=Math.min(a,b),hi=Math.max(a,b);for(var k=lo;k<=hi;k++)__SEL__.add(vis[k]);}
  }else if(event&&(event.ctrlKey||event.metaKey)){
    if(__SEL__.has(idx)){if(__SEL__.size>1)__SEL__.delete(idx);}else __SEL__.add(idx);
  }else{
    var allVis=vis.every(function(i){return __SEL__.has(i);});
    if(allVis&&vis.length>0){__SEL__.clear();__SEL__.add(idx);}
    else if(__SEL__.size===1&&__SEL__.has(idx)){vis.forEach(function(i){__SEL__.add(i);});}
    else if(__SEL__.has(idx)){__SEL__.delete(idx);}
    else{__SEL__.add(idx);}
  }
  _ftLR=idx;rFilter();__ON_CHANGE__
}
function selectAllRows(){
  _visibleFtIdxs().forEach(function(i){__SEL__.add(i);});_ftLR=-1;rFilter();__ON_CHANGE__
}
function clearRows(){__SEL__.clear();_ftLR=-1;rFilter();__ON_CHANGE__}
"""


def make_filter_js(on_change_calls, sel_var='sR', toggle_fn='IC.toggleRow',
                    metric_cols=None, extra_text_cols=None):
    """Return complete filter-by-Lot/Wafer JS parameterized for the calling dashboard.

    on_change_calls : JS executed after any filter or selection change.
                      bin_dist example : 'upd();'
                      SICC example     : 'render_sicc();render_cdyn();render_summ();'
    sel_var         : JS Set variable holding selected row indices.
                      bin_dist: 'sR'   |  SICC: 'SEL_WFR'
    toggle_fn       : Full JS function reference for row onclick.
                      bin_dist: 'IC.toggleRow'  |  SICC: 'toggleRow'
    metric_cols     : Optional list of sortable numeric columns rendered after
                      Date Tested, each a dict {'key','label','get','fmt'} where
                      'get' is a JS function BODY (must `return` a number, given
                      `row`) and 'fmt' is a JS function body (must `return` a
                      display string, given `v` = get's result and `row`).
                      Defaults to the original FF% / FF+DF% / Total columns.
                      Example (scan-dashboard Total/Fail/%Fail):
                        [{'key': 'total', 'label': 'Total',
                          'get': 'return row.total||0;',
                          'fmt': "return v?v.toLocaleString():'\\u2014';"},
                         {'key': 'fail', 'label': 'Fail',
                          'get': 'return row.fail||0;',
                          'fmt': 'return String(v);'},
                         {'key': 'pfail', 'label': '% Fail',
                          'get': 'return row.total?(row.fail/row.total*100):0;',
                          'fmt': "return row.total?v.toFixed(2)+'%':'\\u2014';"}]
    extra_text_cols : Optional list of extra dropdown-filterable text columns
                      rendered after MaterialType, each a dict {'key','label'}
                      (e.g. [{'key': 'stepping', 'label': 'Step'}]).

    The returned JS references DATA.rows, DATA.hasMaterial, DATA.hasDate,
    DATA.hasUpmMed — the caller must define a DATA object before this JS runs.
    Each row must additionally provide whatever fields metric_cols/extra_text_cols
    reference (default metric_cols needs `binCounts` + `total`).
    """
    fn_ns = (toggle_fn.rsplit('.', 1)[0] + '.') if '.' in toggle_fn else ''
    metric_cols = metric_cols if metric_cols is not None else _DEFAULT_METRIC_COLS
    extra_text_cols = extra_text_cols or []
    metrics_js = '[' + ','.join(_metric_entry_js(m) for m in metric_cols) + ']'
    extra_text_js = _json.dumps(
        [{'k': c['key'], 'l': c['label']} for c in extra_text_cols]
    )
    js = _FILTER_COMPLETE_JS_TEMPLATE
    js = js.replace('__SEL__', sel_var)
    js = js.replace('__FN_NS__', fn_ns)
    js = js.replace('__TOGGLE_FN__', toggle_fn)
    js = js.replace('__ON_CHANGE__', on_change_calls)
    js = js.replace('__EXTRA_TEXT_DEFS__', extra_text_js)
    js = js.replace('__METRICS__', metrics_js)
    return js
