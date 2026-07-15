"""_filter_lot_wafer.py — Shared Filter-by-Lot/Wafer CSS and JS.

Single source of truth for the filter-table styles and the column-filter
dropdown panel, used by both:

  • yld/src/bin_distribution_html.py          (master — defines the visual style)
  • sicc_cdyn_upm/src/_dash_frame.py          (CSS, table class)
  • sicc_cdyn_upm/src/_dash_js_shared.py      (JS, thin tblFtOpen wrapper)

HOW TO USE
----------
Both consumers import:

    from _filter_lot_wafer import FILTER_TABLE_CSS, FILTER_DD_JS

``FILTER_TABLE_CSS`` — append to your page CSS string.
``FILTER_DD_JS``     — inject into the page <script> (global or IIFE scope).
                       Provides ``_ftDdCreate(opts)`` which both dashboards
                       call from their per-dashboard ``ftDdOpen``/``tblFtOpen``
                       thin-wrapper functions.
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
