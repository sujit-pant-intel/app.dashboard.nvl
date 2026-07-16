"""pcmprog_html.py — PCM-Program correlation dashboard (PCM-analysis style).

Two sections (tabs):
  1. UPM vs Freq (Delay)  — Sort UPM_* vs PCM Td_* converted to GHz
  2. SICC vs Poff (Ioff)  — Sort SICC_* vs PCM Ioff_* params

Each tab: 3-panel layout
  Left  : Shared wafer filter (lot/wafer accordion with Program column)
  Middle: Parameter table — updates on selection change
  Right : XY scatter — autocomplete axis, OLS trend + Pearson R

Td→Freq: freq_GHz = 500 / Td_ps
Program column: auto-detected (looks for 'program' in col name)
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# Column classification
# ─────────────────────────────────────────────────────────────────────────────

def _classify_columns(df):
    cols = list(df.columns)
    td_cols   = [c for c in cols if re.match(r'^Td_',   c, re.I)]
    ioff_cols = [c for c in cols if re.match(r'^Ioff_', c, re.I)]
    upm_cols  = [c for c in cols if c.startswith('UPM_')]
    sicc_cols = [c for c in cols if 'SICC' in c and not c.startswith('Ioff')]
    return td_cols, ioff_cols, upm_cols, sicc_cols


def _find_program_col(df):
    """Return first column that looks like a test-program column."""
    for c in df.columns:
        if re.match(r'(?i)^program\s', c):      # e.g. 'Program Name_119325'
            return c
    for c in df.columns:
        if re.match(r'(?i)^(sort_)?program(_name|_type|_id)?$', c):
            return c
        if re.match(r'(?i)^test_program$|^flow_name$|^lot_type$', c):
            return c
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Td -> Frequency conversion
# ─────────────────────────────────────────────────────────────────────────────

_FREQ_PFX = '__freq__'


def _freq_col(td_col):
    return _FREQ_PFX + td_col


def _td_to_freq_ghz(val):
    """Convert Td (ps) to equivalent frequency (GHz). val ~7-10 -> ~50-70 GHz."""
    if val is None or not isinstance(val, (int, float)) or math.isnan(val) or val <= 0:
        return None
    return round(500.0 / val, 6)


# ─────────────────────────────────────────────────────────────────────────────
# Friendly labels
# ─────────────────────────────────────────────────────────────────────────────

def _friendly_upm(col):
    m = re.search(r'_(\d{3,4})_MED', col, re.I) or re.search(r'_(\d{3,4})_', col)
    mv = int(m.group(1)) if m else 0
    return "UPM {}mV".format(mv) if mv else col[-30:]


def _friendly_sicc(col):
    cond = re.search(r'SICC_([A-Z0-9]+)_', col)
    rail = re.search(r'_(VCC\w+|VNN\w+)\|', col)
    c = cond.group(1) if cond else ''
    r = rail.group(1) if rail else ''
    return "{} {}".format(c, r).strip() if (c or r) else col.replace('_119325', '')[-35:]


_TD_MAP = {
    'RA4u': 'ULVTN', 'RA4U': 'ULVTN', 'RPA4u': 'ULVTP',
    'RJ4u': 'ULVTN-J', 'RA4A': 'ULLN', 'RPA4A': 'ULLP',
    'RA4B': 'LLLN',  'RPA4B': 'LLLP',
    'RA4L': 'LVTN',  'RPA4L': 'LVTP',
    'RA4S': 'SVTN',  'RPA4S': 'SVTP',
    'RK4u': 'ULVTN-K',
}


def _friendly_td(col):
    key = re.sub(r'^Td_', '', col, flags=re.I)
    return "Td {}".format(_TD_MAP.get(key, key))


def _friendly_freq(col):
    td_col = col[len(_FREQ_PFX):]
    key = re.sub(r'^Td_', '', td_col, flags=re.I)
    return "Freq {} (GHz)".format(_TD_MAP.get(key, key))


def _friendly_ioff(col):
    _MAP = {
        'RNA4u': 'Ioff ULVTN', 'RPA4u': 'Ioff ULVTP',
        'RNJ4u': 'Ioff ULVTN-J', 'RPJ4u': 'Ioff ULVTP-J',
        'RNA4A': 'Ioff ULLN',  'RPA4A': 'Ioff ULLP',
        'RNA4B': 'Ioff LLLN',  'RPA4B': 'Ioff LLLP',
        'RNA4L': 'Ioff LVTN',  'RPA4L': 'Ioff LVTP',
        'RNA4S': 'Ioff SVTN',  'RPA4S': 'Ioff SVTP',
        'RNA4e': 'Ioff ELVTN', 'RPA4e': 'Ioff ELVTP',
        'RNK4u': 'Ioff ULVTN-K',
    }
    key = re.sub(r'^Ioff_', '', col, flags=re.I)
    return _MAP.get(key, "Ioff {}".format(key))


# ─────────────────────────────────────────────────────────────────────────────
# SICC spec loader
# ─────────────────────────────────────────────────────────────────────────────

def _try_float(s):
    try:
        return float(re.sub(r'[^\d.\-]', '', str(s).strip()))
    except Exception:
        return None


def _load_sort_spec(repo_root, stepping="L0"):
    """Parse UPM and SICC spec from shared/spec/.

    Returns:
        {
          "_upm":  {"target": 9154},
          "_sicc": {
            "atom": {"sds_sh": 0.233, "sdt_sh": 1.524},
            "core": {"sds_sh": 0.446, "sdt_sh": 2.997},
            "ccf":  {"sds_sh": 0.750, "sdt_sh": 4.100},
          }
        }
    """
    spec = {"_upm": {"target": 9154}, "_sicc": {}}

    # ── UPM ───────────────────────────────────────────────────────────────
    upm_md = Path(repo_root) / "shared" / "spec" / "upm" / "upm.md"
    if upm_md.is_file():
        txt = upm_md.read_text(encoding="utf-8")
        m = re.search(r'(?i)upm target[^\d]+(\d{4,5})', txt)
        if m:
            spec["_upm"]["target"] = float(m.group(1))

    # ── SICC ──────────────────────────────────────────────────────────────
    sicc_md = Path(repo_root) / "shared" / "spec" / "sicc" / f"SICC_{stepping}_AIO_Table.md"
    if not sicc_md.is_file():
        sicc_md = Path(repo_root) / "shared" / "spec" / "sicc" / "SICC_L0_AIO_Table.md"
    if sicc_md.is_file():
        txt = sicc_md.read_text(encoding="utf-8")
        domain = None
        for line in txt.splitlines():
            dm = re.match(r'^##\s+(Core|Atom|CCF)\s+Domain', line, re.I)
            if dm:
                domain = dm.group(1).lower()
                spec["_sicc"].setdefault(domain, {})
                continue
            if domain is None:
                continue
            # Row: | **AIO (0.95V)** | 0.446 A | 2.997 A | ...
            # Use 0.95V AIO (single die, higher voltage → conservative USL)
            m = re.match(
                r'\|\s*\*?\*?AIO\s*\(0\.95V\)\*?\*?\s*\|([^|]+)\|([^|]+)', line)
            if m:
                sds = _try_float(m.group(1))
                sdt = _try_float(m.group(2))
                if sds is not None:
                    spec["_sicc"][domain]["sds_sh"] = sds
                if sdt is not None:
                    spec["_sicc"][domain]["sdt_sh"] = sdt

    return spec


def _apply_sort_spec(sort_spec, upm_cols, sicc_cols):
    """Map sort spec limits onto actual column names.

    Returns a dict {col: {sl, sh, tgt, unit}} ready to merge into spec_js.
    """
    result = {}
    upm_tgt = sort_spec.get("_upm", {}).get("target", 9154)
    sicc_dom = sort_spec.get("_sicc", {})

    # UPM: target=upm_tgt, sl=94% of target (L0 AIO expected), no USL
    for c in upm_cols:
        result[c] = {
            "sl":  round(upm_tgt * 0.94, 1),
            "sh":  None,
            "tgt": upm_tgt,
            "unit": "",
        }

    # SICC: map by rail domain and condition
    _DOM_MAP = {
        "vccatom": "atom",
        "vcccore": "core",
        "vccccf":  "ccf",
    }
    for c in sicc_cols:
        # Extract rail name from column pattern: |PP_SICC_xxx_RAILNAME|
        rail_m = re.search(r'\|PP_SICC_[^|]+_(VCC\w+|VNN\w+)\|', c, re.I)
        if not rail_m:
            continue
        rail = rail_m.group(1).lower()

        domain = None
        for prefix, dom in _DOM_MAP.items():
            if rail.startswith(prefix):
                domain = dom
                break
        if domain is None or domain not in sicc_dom:
            continue

        # Condition: 0P5A → SDS; 24A or PMUX → SDT
        is_sds = bool(re.search(r'_0P5A_|_500MA_', c, re.I))
        sh_key = "sds_sh" if is_sds else "sdt_sh"
        sh_val = sicc_dom[domain].get(sh_key)
        if sh_val is None:
            continue

        result[c] = {"sl": None, "sh": sh_val, "tgt": None, "unit": "A"}

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Data aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _count_per_wafer(df):
    id_cols = ["Lot", "Wafer"]
    if "Material" in df.columns:
        id_cols.append("Material")
    counts = {}
    for key, grp in df.groupby(id_cols):
        k = tuple(str(x) for x in (key if isinstance(key, tuple) else (key,)))
        while len(k) < 3:
            k = k + ('',)
        counts[k] = len(grp)
    return counts


def _build_wafer_rows(df, num_cols, program_col=None, freq_td_cols=None):
    """Return wafer-level dicts with median values + freq-converted Td cols."""
    id_cols = ["Lot", "Wafer"]
    if "Material" in df.columns:
        id_cols.append("Material")

    avail_num = [c for c in num_cols if c in df.columns]
    if not avail_num:
        return []

    sub = df[id_cols + avail_num].copy()
    for c in avail_num:
        sub[c] = pd.to_numeric(sub[c], errors="coerce")

    agg = sub.groupby(id_cols, as_index=False)[avail_num].median()
    counts = _count_per_wafer(df)

    prog_by_lot = {}
    if program_col and program_col in df.columns:
        for lot, grp in df.groupby("Lot"):
            vals = grp[program_col].dropna().unique()
            prog_by_lot[str(lot)] = str(vals[0]) if len(vals) > 0 else ""

    rows = []
    for _, r in agg.iterrows():
        lot_s = str(r.get("Lot",   ""))
        wfr_s = str(r.get("Wafer", ""))
        mat_s = str(r.get("Material", "")) if "Material" in id_cols else ""
        row = {
            "lot":     lot_s,
            "wafer":   wfr_s,
            "mat":     mat_s,
            "program": prog_by_lot.get(lot_s, ""),
            "n":       counts.get((lot_s, wfr_s, mat_s), 1),
        }
        for c in avail_num:
            v = r[c]
            row[c] = None if (v is None or (isinstance(v, float) and math.isnan(v))) \
                     else round(float(v), 8)
        if freq_td_cols:
            for tc in freq_td_cols:
                tv = row.get(tc)
                row[_freq_col(tc)] = _td_to_freq_ghz(tv)
        rows.append(row)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# CSS  (PCM-analysis light theme)
# ─────────────────────────────────────────────────────────────────────────────

_CSS = """*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;font-family:Arial,sans-serif;background:#f0f2f5;color:#2c3e50;font-size:13px;overflow:hidden}
#shell{display:flex;flex-direction:column;height:100vh;overflow:hidden}
.page-hdr{background:#1f3a50;color:#fff;padding:8px 16px;flex-shrink:0}
.page-hdr h1{font-size:14px;font-weight:bold}.page-hdr .sub{font-size:11px;color:#aed6f1;margin-top:2px}
.tabs{display:flex;align-items:center;background:#1a252f;padding:5px 12px;gap:6px;flex-shrink:0;border-bottom:3px solid #27ae60}
.tab-btn{padding:7px 22px;border:2px solid transparent;border-radius:5px;background:rgba(255,255,255,0.07);color:#95a5a6;cursor:pointer;font-size:13px;font-weight:bold;transition:background .15s,color .15s}
.tab-btn:hover{background:rgba(39,174,96,0.20);color:#a9dfbf}
.tab-btn.active{background:#27ae60;color:#fff;border-color:#1e8449}
.tab-panel{display:none;flex:1;min-height:0;overflow:hidden}
.tab-panel.active{display:flex;flex-direction:row}
#body-row{display:flex;flex-direction:row;flex:1;min-height:0;overflow:hidden}
#panel1{width:300px;min-width:160px;flex-shrink:0;background:#fff;display:flex;flex-direction:column;border-right:2px solid #d0d7de;overflow:hidden}
.p1-hdr{background:#2c3e50;color:#fff;padding:6px 10px;font-size:11px;font-weight:bold;display:flex;justify-content:space-between;align-items:center;flex-shrink:0;gap:4px}
.p1-srch{display:flex;gap:2px;padding:4px 6px;background:#f0f2f5;border-bottom:1px solid #dde;flex-shrink:0;flex-wrap:wrap}
.p1-srch input{padding:2px 5px;font-size:10px;border:1px solid #ccc;border-radius:3px;background:#fff;min-width:0}
.p1-body{flex:1;overflow-y:auto;overflow-x:auto}
.wfr-tbl{border-collapse:collapse;width:100%;font-size:12px;white-space:nowrap}
.wfr-tbl th{background:#34495e;color:#ecf0f1;padding:4px 8px;text-align:left;position:sticky;top:0;z-index:2}
.wfr-tbl th.num{text-align:right}
.wfr-tbl td{padding:3px 8px;border-bottom:1px solid #f0f0f0;cursor:pointer}
.wfr-tbl .num{text-align:right}
.lot-hdr td{background:#34495e!important;color:#ecf0f1!important;cursor:pointer}
.fp{padding:3px 8px;white-space:nowrap;border-bottom:1px solid #eee}
.fr:hover td{background:#eaf4ff!important}.frs td{background:#d6eaff!important;font-weight:bold}.frs:hover td{background:#bcd8f8!important}
.row-info{font-size:10px;color:#aed6f1;margin-left:6px;font-weight:normal}
.wfr-btn{padding:2px 9px;font-size:11px;border:1px solid #7f8c8d;border-radius:3px;background:none;color:#bdc3c7;cursor:pointer}.wfr-btn:hover{background:#3d5166;color:#fff}
.sp12{width:5px;flex-shrink:0;background:#d0d7de;cursor:col-resize;align-self:stretch;user-select:none}.sp12:hover{background:#2980b9}
#main-area{flex:1;min-width:0;display:flex;flex-direction:column;overflow:hidden}
.p2-wrap{width:380px;min-width:160px;flex-shrink:0;background:#fff;display:flex;flex-direction:column;border-right:2px solid #d0d7de;overflow:hidden;transition:width .12s}
.p2-wrap.p2-hidden{width:0!important;min-width:0!important;overflow:hidden;border:none}
.p2-hdr{background:#34495e;color:#fff;padding:5px 10px;font-size:11px;font-weight:bold;flex-shrink:0;display:flex;justify-content:space-between;align-items:center}
.p2-body{flex:1;overflow:auto}
.sp23{width:5px;flex-shrink:0;background:#d0d7de;cursor:col-resize;align-self:stretch;user-select:none}.sp23:hover{background:#2980b9}
.p3-wrap{flex:1;min-width:320px;display:flex;flex-direction:column;overflow:hidden;background:#f0f2f5;padding:6px}
.p3-ctrl{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:6px 8px;background:#fff;border-radius:5px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:6px;flex-shrink:0}
.p3-ctrl label{font-size:11px;color:#7f8c8d;font-weight:bold;margin-right:2px}
.p3-ctrl-sep{color:#bdc3c7;margin:0 4px}
.xy-ac-wrap{position:relative;display:inline-block}
.xy-ac-inp{border:1px solid #ccc;border-radius:4px;padding:3px 8px;font-size:12px;width:220px;background:#fff;color:#2c3e50;cursor:pointer}
.xy-ac-inp:focus{outline:none;border-color:#2980b9;box-shadow:0 0 0 2px rgba(41,128,185,.15)}
.xy-ac-pop{position:absolute;z-index:9999;background:#fff;border:1px solid #bdc3c7;border-radius:4px;box-shadow:0 4px 14px rgba(0,0,0,.18);max-height:280px;overflow-y:auto;min-width:240px;width:max-content;display:none;top:100%;left:0;margin-top:2px}
.xy-ac-item{padding:5px 10px;cursor:pointer;font-size:12px;white-space:nowrap}.xy-ac-item:hover{background:#d6eaff}
.sum-tbl{border-collapse:collapse;width:100%;font-size:11px;white-space:nowrap}
.sum-tbl th{background:#34495e;color:#ecf0f1;padding:4px 8px;text-align:left;position:sticky;top:0;z-index:1}
.sum-tbl th.num{text-align:right}
.sum-tbl td{padding:3px 8px;border-bottom:1px solid #eee}.sum-tbl td.num{text-align:right}
.sum-tbl tbody tr:nth-child(even){background:#f4f8ff}.sum-tbl tbody tr:hover{background:#eaf4ff}
.tag-sort{background:#fdcb6e;color:#7f4f00;font-size:9px;font-weight:bold;padding:1px 4px;border-radius:3px}
.tag-pcm{background:#a29bfe;color:#1a0050;font-size:9px;font-weight:bold;padding:1px 4px;border-radius:3px}
.val-hi{color:#c0392b;font-weight:bold}.val-lo{color:#2980b9;font-weight:bold}.val-ok{color:#27ae60}
#tip-box{position:fixed;background:rgba(20,28,40,0.93);color:#ecf0f1;font-size:12px;padding:5px 11px;border-radius:5px;pointer-events:none;z-index:9999;display:none;white-space:pre-line;box-shadow:0 2px 8px rgba(0,0,0,.4);border:1px solid #4a6278}
"""

# ─────────────────────────────────────────────────────────────────────────────
# JavaScript
# ─────────────────────────────────────────────────────────────────────────────

_JS = r"""
/* Utilities */
function esc(s){
  return String(s==null?'':s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function _fmt(v){
  if(v==null||!isFinite(v))return '\u2014';
  if(Math.abs(v)>0&&(Math.abs(v)<1e-4||Math.abs(v)>=1e7))return v.toExponential(3);
  return parseFloat(v.toPrecision(4)).toString();
}
function _fmtTk(v){
  if(Math.abs(v)>0&&(Math.abs(v)<0.001||Math.abs(v)>=10000))return v.toExponential(2);
  return parseFloat(v.toPrecision(4)).toString();
}
function _safeMin(a){var m=Infinity;for(var i=0;i<a.length;i++)if(a[i]<m)m=a[i];return m===Infinity?0:m;}
function _safeMax(a){var m=-Infinity;for(var i=0;i<a.length;i++)if(a[i]>m)m=a[i];return m===-Infinity?1:m;}

var _CPALS=['#2980b9','#27ae60','#e67e22','#8e44ad','#c0392b',
            '#16a085','#f39c12','#1abc9c','#d35400','#7f8c8d',
            '#3498db','#2ecc71','#e74c3c','#9b59b6','#f0a500'];
function _cPal(i){return _CPALS[i%_CPALS.length];}

/* Global filter state */
var _SEL=new Set();
var _LCOL={};
var _LAST_WFR=-1;
var _CUR_SID='upm_td';
var _FSRCH={lot:'',wafer:'',program:'',mat:''};
_WFR.forEach(function(_,i){_SEL.add(i);});

function _visIdx(){
  var vis=[];
  _WFR.forEach(function(w,i){
    var lo=_FSRCH.lot.toLowerCase(),wr=_FSRCH.wafer.toLowerCase(),
        pg=_FSRCH.program.toLowerCase(),mt=_FSRCH.mat.toLowerCase();
    if((!lo||(w.lot||'').toLowerCase().indexOf(lo)>=0)&&
       (!wr||(w.wafer||'').toLowerCase().indexOf(wr)>=0)&&
       (!pg||(w.program||'').toLowerCase().indexOf(pg)>=0)&&
       (!mt||(w.mat||'').toLowerCase().indexOf(mt)>=0))vis.push(i);
  });
  return vis;
}
function _cMap(){
  var lots=[],map={};
  _WFR.forEach(function(w,i){
    if(_SEL.has(i)&&lots.indexOf(w.lot)<0){map[w.lot]=_cPal(lots.length);lots.push(w.lot);}
  });
  return {map:map,lots:lots};
}

/* Filter build */
function _buildFilter(){
  var vis=_visIdx();
  var byLot={},lotOrder=[];
  vis.forEach(function(wi){
    var lot=_WFR[wi].lot;
    if(!byLot[lot]){byLot[lot]=[];lotOrder.push(lot);}
    byLot[lot].push(wi);
  });
  lotOrder.forEach(function(lot){if(_LCOL[lot]===undefined)_LCOL[lot]=true;});
  var html='',indet=[];
  lotOrder.forEach(function(lot){
    var rows=byLot[lot];
    var selCnt=rows.filter(function(wi){return _SEL.has(wi);}).length;
    var allSel=selCnt===rows.length,anySel=selCnt>0;
    var isCol=(_LCOL[lot]!==false);
    if(anySel&&!allSel)indet.push(lot);
    html+='<tr class="lot-hdr" onclick="_toggleLot(\''+esc(lot)+'\')">'
      +'<td colspan="4" style="padding:4px 8px;cursor:pointer;user-select:none">'
      +'<span style="margin-right:4px;font-size:10px">'+(isCol?'&#9658;':'&#9660;')+'</span>'
      +'<input id="lcb-'+esc(lot)+'" type="checkbox" style="vertical-align:middle;margin-right:4px"'
      +(allSel?' checked':'')+' onclick="_selLot(event,\''+esc(lot)+'\')">'
      +esc(lot)+'<span style="font-size:10px;color:#95a5a6;font-weight:normal;margin-left:5px">'
      +'('+selCnt+'/'+rows.length+')</span></td></tr>';
    rows.forEach(function(wi){
      var w=_WFR[wi],isSel=_SEL.has(wi);
      html+='<tr class="fr'+(isSel?' frs':'')+'"'+(isCol?' style="display:none"':'')
        +' onclick="_toggleWfr('+wi+',event)">'
        +'<td class="fp">'+esc(w.wafer)+'</td>'
        +'<td class="fp" style="color:#7f8c8d;font-size:10px;max-width:110px;overflow:hidden;text-overflow:ellipsis" title="'+esc(w.program||'')+'">'+esc(w.program||'')+'</td>'
        +'<td class="fp" style="color:#7f8c8d;font-size:10px">'+esc(w.mat||'')+'</td>'
        +'<td class="num fp" style="color:#7f8c8d">'+w.n+'</td>'
        +'</tr>';
    });
  });
  var tbody=document.getElementById('wfr-tbody');
  if(tbody)tbody.innerHTML=html;
  indet.forEach(function(lot){
    var cb=document.getElementById('lcb-'+lot);
    if(cb)cb.indeterminate=true;
  });
  var ri=document.getElementById('row-info');
  if(ri){var s=_SEL.size,t=_WFR.length;ri.textContent=(s>0&&s<t)?'('+s+'/'+t+')':'';}
}

function _toggleLot(lot){
  var wasOpen=(_LCOL[lot]===false);
  Object.keys(_LCOL).forEach(function(l){_LCOL[l]=true;});
  if(!wasOpen)_LCOL[lot]=false;
  _buildFilter();
}
function _selLot(ev,lot){
  ev.stopPropagation();
  var lotWfrs=[];
  _WFR.forEach(function(w,wi){if(w.lot===lot)lotWfrs.push(wi);});
  var allSel=lotWfrs.every(function(wi){return _SEL.has(wi);});
  lotWfrs.forEach(function(wi){if(allSel)_SEL.delete(wi);else _SEL.add(wi);});
  _buildFilter();_rerender();
}
function _toggleWfr(wi,ev){
  var vis=_visIdx();
  if(ev&&ev.shiftKey&&_LAST_WFR>=0){
    var lo=Math.min(wi,_LAST_WFR),hi=Math.max(wi,_LAST_WFR);
    for(var i=lo;i<=hi;i++)if(vis.indexOf(i)>=0)_SEL.add(i);
  }else{if(_SEL.has(wi))_SEL.delete(wi);else _SEL.add(wi);}
  _LAST_WFR=wi;_buildFilter();_rerender();
}
function _selAll(){_visIdx().forEach(function(i){_SEL.add(i);});_buildFilter();_rerender();}
function _clrAll(){_visIdx().forEach(function(i){_SEL.delete(i);});_buildFilter();_rerender();}
function _onSearch(f,v){_FSRCH[f]=v;_buildFilter();}

/* Tab switch */
function _showTab(sid){
  _CUR_SID=sid;
  document.querySelectorAll('.tab-btn').forEach(function(b){b.classList.toggle('active',b.dataset.sid===sid);});
  document.querySelectorAll('.tab-panel').forEach(function(p){p.classList.toggle('active',p.id==='tp-'+sid);});
  _rerender();
}
function _rerender(){_buildTable(_CUR_SID);buildXYTab(_CUR_SID);}

/* Parameter table */
function _buildTable(sid){
  var tbody=document.getElementById('tbl-tbody-'+sid);
  if(!tbody)return;
  var rows=_DATA[sid];
  if(!rows||!rows.length){
    tbody.innerHTML='<tr><td colspan="7" style="padding:16px;color:#aaa;text-align:center">No data</td></tr>';
    return;
  }
  var labels=_LABELS[sid]||{};
  var cols=_COL_ORDER[sid]||Object.keys(labels);
  var sortKeys=_SORT_KEYS[sid]||[];
  var html='',lastParam='';
  cols.forEach(function(c){
    var isSort=sortKeys.indexOf(c)>=0;
    var tag=isSort?'<span class="tag-sort">SORT</span>':'<span class="tag-pcm">PCM</span>';
    var paramLabel=labels[c]||c;
    var borderStyle=(paramLabel!==lastParam)?'border-top:2px solid #dde':'';
    lastParam=paramLabel;
    rows.forEach(function(r){
      if(!_SEL.has(r._idx))return;
      var v=r[c]; if(v===null||v===undefined)return;
      var sp=_SPEC[c]||{};
      var cls=(sp.sl!=null&&v<sp.sl)?'val-lo':(sp.sh!=null&&v>sp.sh)?'val-hi':'val-ok';
      html+='<tr style="'+borderStyle+'">'
        +'<td>'+esc(r.lot)+'</td>'
        +'<td>'+esc(r.wafer)+'</td>'
        +'<td style="color:#7f8c8d;font-size:10px">'+esc(r.mat||'')+'</td>'
        +'<td>'+tag+'</td>'
        +'<td>'+esc(paramLabel)+'</td>'
        +'<td class="num"><span class="'+cls+'">'+_fmt(v)+'</span></td>'
        +'<td style="color:#7f8c8d;font-size:10px">'+esc(sp.unit||'')+'</td>'
        +'</tr>';
      borderStyle='';
    });
  });
  tbody.innerHTML=html||'<tr><td colspan="7" style="padding:16px;color:#aaa;text-align:center">No wafers selected</td></tr>';
}

/* OLS + Pearson */
function _ols(xs,ys){
  var n=xs.length;if(n<2)return null;
  var mx=0,my=0,i;
  for(i=0;i<n;i++){mx+=xs[i];my+=ys[i];}mx/=n;my/=n;
  var num=0,den=0;
  for(i=0;i<n;i++){var dx=xs[i]-mx;num+=dx*(ys[i]-my);den+=dx*dx;}
  if(!den)return null;
  var sl=num/den;
  return{slope:sl,intercept:my-sl*mx};
}
function _pearson(xs,ys){
  var n=xs.length;if(n<2)return null;
  var mx=0,my=0,i;
  for(i=0;i<n;i++){mx+=xs[i];my+=ys[i];}mx/=n;my/=n;
  var cov=0,sx=0,sy=0;
  for(i=0;i<n;i++){var dx=xs[i]-mx,dy=ys[i]-my;cov+=dx*dy;sx+=dx*dx;sy+=dy*dy;}
  var d=Math.sqrt(sx*sy);return d?cov/d:null;
}

/* Tooltip */
var _tipEl=null;
var _TIPS=[];
function _showTipI(ev,i){
  if(!_tipEl){_tipEl=document.getElementById('tip-box');}
  if(!_tipEl)return;
  _tipEl.innerHTML=_TIPS[i]||'';
  _tipEl.style.display='block';
  _tipEl.style.left=(ev.clientX+14)+'px';
  _tipEl.style.top=(ev.clientY-28)+'px';
}
function _hideTip(){if(_tipEl)_tipEl.style.display='none';}

/* ── Per-tab XY state ── */
var _XY_ST={};
function _xyState(sid){
  if(!_XY_ST[sid]){
    _XY_ST[sid]={
      x:(_XCOL[sid]||''), xgrp:null,
      ys:(_YCOL[sid]?[_YCOL[sid]]:[]), ygrp:null, ysSeeded:false,
      logX:false, logY:false, die:false, trend:'ols',
      xmin:null, xmax:null, ymin:null, ymax:null, h:500, ysrch:'', gby:[],
      hiddenGrps:{}
    };
  }
  return _XY_ST[sid];
}
function _xyItems(sid,grp){
  var labels=_LABELS[sid]||{};
  var gcols=(_GROUP_COLS[sid]||{});
  var cols=grp&&gcols[grp]?gcols[grp]:(_COL_ORDER[sid]||Object.keys(labels));
  return cols.filter(function(k){return k in labels;}).map(function(k){
    return{key:k,label:labels[k]||k,lc:(k+' '+(labels[k]||'')).toLowerCase()};
  });
}
function _xyBuildYChecklist(sid){
  var st=_xyState(sid);
  var el=document.getElementById('xy-y-list-'+sid);if(!el)return;
  var items=_xyItems(sid,st.ygrp);
  var q=st.ysrch.toLowerCase();
  var vis=q?items.filter(function(it){return it.lc.indexOf(q)>=0;}):items;
  var html='';
  vis.forEach(function(it){
    var chk=st.ys.indexOf(it.key)>=0;
    html+='<label style="display:flex;align-items:center;gap:5px;padding:2px 6px;cursor:pointer;border-radius:3px;white-space:nowrap"'
      +' onmouseover="this.style.background=\'#e8f0fe\'" onmouseout="this.style.background=\'\'">'
      +'<input type="checkbox"'+(chk?' checked':'')
      +' onchange="_xyToggleY(\''+sid+'\',\''+it.key+'\')" style="cursor:pointer">'
      +'<b style="font-size:11px">'+esc(it.label)+'</b></label>';
  });
  el.innerHTML=html||'<div style="padding:6px;color:#aaa;font-size:11px">No params</div>';
  var btn=document.getElementById('xy-y-btn-'+sid);
  if(btn){
    var cnt=st.ys.length;
    var lbl=cnt===0?'(none)':cnt>1?(cnt+' Y params'):((_LABELS[sid]||{})[st.ys[0]]||st.ys[0]);
    btn.textContent=lbl;
    btn.style.color=cnt===0?'#c0392b':cnt>1?'#1a6bb5':'';
    btn.style.fontWeight=cnt>1?'bold':'';
  }
}
function _xyYDropToggle(sid){
  var pop=document.getElementById('xy-y-drop-'+sid);if(!pop)return;
  if(pop.style.display==='block'){pop.style.display='none';return;}
  pop.style.display='block';
  var srch=document.getElementById('xy-y-srch-'+sid);
  if(srch){srch.value=_xyState(sid).ysrch;srch.focus();}
  _xyBuildYChecklist(sid);
}
function _xyToggleY(sid,p){
  var st=_xyState(sid);
  var i=st.ys.indexOf(p);
  if(i>=0)st.ys.splice(i,1);else st.ys.push(p);
  st.ysSeeded=true;
  _xyBuildYChecklist(sid);buildXYTab(sid);
}
function _xyYClrAll(sid){
  var st=_xyState(sid);st.ys=[];st.ysSeeded=true;
  _xyBuildYChecklist(sid);buildXYTab(sid);
}
function _xyYSelAll(sid){
  var st=_xyState(sid);
  var items=_xyItems(sid,st.ygrp);
  var q=st.ysrch.toLowerCase();
  var vis=q?items.filter(function(it){return it.lc.indexOf(q)>=0;}):items;
  vis.forEach(function(it){if(st.ys.indexOf(it.key)<0)st.ys.push(it.key);});
  _xyBuildYChecklist(sid);buildXYTab(sid);
}
function _xySetGrp(sid,ax,grp){
  var st=_xyState(sid);
  if(ax==='x')st.xgrp=grp||null;
  else{st.ygrp=grp||null;_xyBuildYChecklist(sid);}
  buildXYTab(sid);
}
/* Close Y dropdown on outside click */
document.addEventListener('click',function(e){
  ['upm_td','sicc_ioff'].forEach(function(sid){
    var pop=document.getElementById('xy-y-drop-'+sid);
    var btn=document.getElementById('xy-y-btn-'+sid);
    if(!pop||pop.style.display!=='block')return;
    if(pop.contains(e.target)||e.target===btn)return;
    pop.style.display='none';
  });
},true);

/* ── Group-by ── */
function toggleGby(sid,field){
  var st=_xyState(sid);
  if(field==='none'){st.gby=[];}
  else{var i=st.gby.indexOf(field);if(i>=0)st.gby.splice(i,1);else st.gby.push(field);}
  document.querySelectorAll('.vgb-cb-'+sid).forEach(function(cb){
    if(cb.value==='none')cb.checked=st.gby.length===0;
    else cb.checked=st.gby.indexOf(cb.value)>=0;
  });
  buildXYTab(sid);
}
function _grpKey(sid,r){
  var st=_xyState(sid);
  if(!st.gby||!st.gby.length)return'All';
  var parts=[];
  if(st.gby.indexOf('lot')>=0)parts.push(r.lot||'');
  if(st.gby.indexOf('wafer')>=0)parts.push(String(r.wafer||''));
  if(st.gby.indexOf('material')>=0)parts.push(r.mat||'');
  if(st.gby.indexOf('program')>=0)parts.push(r.program||'');
  return parts.join('/')||'All';
}
function _cMapForSid(sid){
  var map={},keys=[];
  (_DATA[sid]||[]).forEach(function(r){
    if(!_SEL.has(r._idx))return;
    var k=_grpKey(sid,r);
    if(!map[k]){map[k]=_cPal(keys.length);keys.push(k);}
  });
  return{map:map,keys:keys};
}

function toggleGrpVis(sid,gk){
  var st=_xyState(sid);
  if(st.hiddenGrps[gk])delete st.hiddenGrps[gk];else st.hiddenGrps[gk]=1;
  buildXYTab(sid);
}
function toggleAllGrps(sid,show){
  var st=_xyState(sid);
  if(show){st.hiddenGrps={};}else{
    var cm=_cMapForSid(sid);
    cm.keys.forEach(function(k){st.hiddenGrps[k]=1;});
  }
  buildXYTab(sid);
}
/* ── Theil-Sen ── */
function _med(arr){
  var a=arr.slice().sort(function(a,b){return a-b;});
  var m=a.length;return m%2?a[(m-1)/2]:(a[m/2-1]+a[m/2])/2;
}
function _theilSen(xs,ys){
  var n=xs.length;if(n<3)return null;
  var slopes=[],i,j;
  for(i=0;i<n-1;i++)for(j=i+1;j<n;j++){
    var dx=xs[j]-xs[i];
    if(Math.abs(dx)>1e-12)slopes.push((ys[j]-ys[i])/dx);
  }
  if(!slopes.length)return null;
  var slope=_med(slopes);
  return{slope:slope,intercept:_med(ys)-slope*_med(xs)};
}

/* ── Main XY scatter build ── */
function buildXYTab(sid){
  var st=_xyState(sid);
  var cont=document.getElementById('xy-cont-'+sid);if(!cont)return;
  /* Seed Y on first call */
  if(!st.ysSeeded){
    var defY=_YCOL[sid];if(defY&&!st.ys.length)st.ys=[defY];
    st.ysSeeded=true;
  }
  /* Populate X select */
  var xsel=document.getElementById('xy-sel-x-'+sid);
  if(xsel){
    var xitems=_xyItems(sid,st.xgrp);
    xsel.innerHTML=xitems.map(function(it){
      return'<option value="'+esc(it.key)+'"'+(it.key===st.x?' selected':'')+'>'+esc(it.label)+'</option>';
    }).join('');
    if(xitems.length&&!xitems.some(function(it){return it.key===st.x;})){
      st.x=xitems[0].key;xsel.value=st.x;
    }
  }
  _xyBuildYChecklist(sid);
  var multiY=st.ys.length>1;
  var gbyWrap=document.getElementById('xy-gby-wrap-'+sid);
  if(gbyWrap){
    gbyWrap.style.opacity=multiY?'0.35':'';
    gbyWrap.style.pointerEvents=multiY?'none':'';
  }
  var xc=st.x;
  var labels=_LABELS[sid]||{};
  var validYs=st.ys.filter(function(y){return labels[y]!==undefined;});
  if(!xc||!validYs.length){
    cont.innerHTML='<div style="padding:24px;color:#888">Select valid X and Y parameters.</div>';return;
  }
  /* Color map */
  var cm;
  if(multiY){
    var cm2={},ck2=validYs.slice();
    ck2.forEach(function(k,i){cm2[k]=_cPal(i);});
    cm={map:cm2,keys:ck2};
  }else{
    cm=_cMapForSid(sid);
  }
  /* Collect points */
  _TIPS=[];var pts=[];
  var rows=_DATA[sid]||[];
  validYs.forEach(function(yc){
    rows.forEach(function(r){
      if(!_SEL.has(r._idx))return;
      var xv=r[xc],yv=r[yc];
      if(xv==null||yv==null||!isFinite(xv)||!isFinite(yv))return;
      var gk=multiY?yc:_grpKey(sid,r);
      if(st.hiddenGrps[gk])return;
      var ti=_TIPS.length;
      _TIPS.push('<b>'+esc(r.lot)+' W'+esc(r.wafer)+'</b>\n'
        +'X ('+esc(labels[xc]||xc)+'): '+_fmtTk(xv)+'\n'
        +'Y ('+esc(labels[yc]||yc)+'): '+_fmtTk(yv));
      pts.push({x:xv,y:yv,lot:r.lot,wafer:r.wafer,gk:gk,yc:yc,ti:ti});
    });
  });
  if(!pts.length){
    cont.innerHTML='<div style="padding:24px;color:#888;font-style:italic">No matching data.</div>';return;
  }
  /* Log helpers */
  function _lx(v){return st.logX?Math.log10(Math.max(v,1e-300)):v;}
  function _ly(v){return st.logY?Math.log10(Math.max(v,1e-300)):v;}
  function _fmtV(v,isLog){
    if(!isLog)return _fmtTk(v);
    var pw=Math.round(v);return(Math.abs(v-pw)<0.05)?'10^'+pw:_fmtTk(Math.pow(10,v));
  }
  var lxs=pts.map(function(p){return _lx(p.x);}),lys=pts.map(function(p){return _ly(p.y);});
  var xmn=st.xmin!=null?_lx(st.xmin):_safeMin(lxs);
  var xmx=st.xmax!=null?_lx(st.xmax):_safeMax(lxs);
  var ymn=st.ymin!=null?_ly(st.ymin):_safeMin(lys);
  var ymx=st.ymax!=null?_ly(st.ymax):_safeMax(lys);
  var xrng=xmx-xmn||1,yrng=ymx-ymn||1;
  var xpad=xrng*0.09,ypad=yrng*0.10;
  var xlo=xmn-xpad,xhi=xmx+xpad,ylo=ymn-ypad,yhi=ymx+ypad;
  var svgH=st.h,ML=90,MR=30,MT=40,MB=80;
  var plotW=820-ML-MR,plotH=svgH-MT-MB;
  var svgW=820;
  function xp(v){return ML+(v-xlo)/(xhi-xlo)*plotW;}
  function yp(v){return MT+(1-(v-ylo)/(yhi-ylo))*plotH;}
  var p=['<svg width="100%" viewBox="0 0 '+svgW+' '+svgH+'" style="display:block">'];
  p.push('<rect width="'+svgW+'" height="'+svgH+'" fill="#f8f9fa"/>');
  p.push('<rect x="'+ML+'" y="'+MT+'" width="'+plotW+'" height="'+plotH+'" fill="#fff" stroke="#ccc" stroke-width="1"/>');
  p.push('<defs><clipPath id="pc-'+sid+'"><rect x="'+ML+'" y="'+MT+'" width="'+plotW+'" height="'+plotH+'"/></clipPath></defs>');
  for(var xi2=0;xi2<=6;xi2++){
    var xv2=xlo+(xhi-xlo)*xi2/6,xpv2=(ML+xi2/6*plotW).toFixed(1);
    p.push('<line x1="'+xpv2+'" y1="'+MT+'" x2="'+xpv2+'" y2="'+(MT+plotH)+'" stroke="rgba(0,0,0,0.08)" stroke-width="0.8"/>');
    p.push('<text x="'+xpv2+'" y="'+(MT+plotH+18)+'" text-anchor="middle" font-size="13" fill="#333">'+_fmtV(xv2,st.logX)+'</text>');
  }
  for(var yi2=0;yi2<=5;yi2++){
    var yv2=ylo+(yhi-ylo)*yi2/5,ypv2=(MT+plotH*(1-yi2/5)).toFixed(1);
    p.push('<line x1="'+ML+'" y1="'+ypv2+'" x2="'+(ML+plotW)+'" y2="'+ypv2+'" stroke="rgba(0,0,0,0.08)" stroke-width="0.8"/>');
    p.push('<text x="'+(ML-6)+'" y="'+ypv2+'" text-anchor="end" dominant-baseline="middle" font-size="13" fill="#333">'+_fmtV(yv2,st.logY)+'</text>');
  }
  /* Trend line */
  if(st.trend!=='none'){
    var grpTd={};
    pts.forEach(function(pt){
      var lxv=_lx(pt.x),lyv=_ly(pt.y);
      if(!isFinite(lxv)||!isFinite(lyv))return;
      if(!grpTd[pt.gk])grpTd[pt.gk]={xs:[],ys:[]};
      grpTd[pt.gk].xs.push(lxv);grpTd[pt.gk].ys.push(lyv);
    });
    Object.keys(grpTd).forEach(function(gk){
      var td=grpTd[gk];var reg=null;
      if(st.trend==='ols')reg=_ols(td.xs,td.ys);
      else if(st.trend==='theilsen')reg=_theilSen(td.xs,td.ys);
      if(!reg)return;
      var col=cm.map[gk]||'#e74c3c';
      var tl_x1=xp(xlo).toFixed(1),tl_y1=yp(reg.slope*xlo+reg.intercept).toFixed(1);
      var tl_x2=xp(xhi).toFixed(1),tl_y2=yp(reg.slope*xhi+reg.intercept).toFixed(1);
      p.push('<line x1="'+tl_x1+'" y1="'+tl_y1+'" x2="'+tl_x2+'" y2="'+tl_y2+'"'
        +' stroke="'+col+'" stroke-width="2" stroke-dasharray="5,3"'
        +' clip-path="url(#pc-'+sid+')" opacity="0.85"/>');
    });
  }
  /* Dots */
  pts.forEach(function(pt){
    var col=cm.map[pt.gk]||'#2980b9';
    var cx=xp(pt.x).toFixed(1),cy=yp(pt.y).toFixed(1);
    p.push('<circle cx="'+cx+'" cy="'+cy+'" r="7" fill="'+col+'"'
      +' fill-opacity="0.72" stroke="'+col+'" stroke-width="1.2"'
      +' onmouseover="_showTipI(event,'+pt.ti+')" onmouseout="_hideTip()"/>');
  });
  /* R/R² badge */
  if(!multiY){
    var lxsA=pts.map(function(p2){return _lx(p2.x);}),lysA=pts.map(function(p2){return _ly(p2.y);});
    var r2=_pearson(lxsA,lysA);
    if(r2!=null){
      var r2sq=(r2*r2).toFixed(3);
      var rTxt='R='+r2.toFixed(3)+'   R\u00b2='+r2sq+'  (n='+pts.length+')';
      p.push('<rect x="'+(ML+plotW-232)+'" y="'+(MT+5)+'" width="228" height="22" rx="4" fill="rgba(255,255,255,0.9)" stroke="#bdc3c7" stroke-width="1"/>');
      p.push('<text x="'+(ML+plotW-118)+'" y="'+(MT+20)+'" text-anchor="middle" font-size="13" font-weight="bold" fill="#1a6bb5">'+esc(rTxt)+'</text>');
    }
  }
  /* Axis labels */
  var xLbl=esc(labels[xc]||xc||'X');
  p.push('<text x="'+(ML+plotW/2)+'" y="'+(svgH-8)+'" text-anchor="middle" font-size="14" font-weight="bold" fill="#333">'+xLbl+'</text>');
  var yLbl=esc(validYs.length===1?(labels[validYs[0]]||validYs[0]):'Y');
  p.push('<text transform="translate(14,'+(MT+plotH/2)+') rotate(-90)" text-anchor="middle" font-size="14" font-weight="bold" fill="#333">'+yLbl+'</text>');
  p.push('</svg>');
  /* HTML legend with checkboxes */
  var legendHtml='<div style="display:flex;flex-wrap:wrap;gap:4px 12px;padding:6px 10px 4px;border-top:1px solid #dde;align-items:center">';
  legendHtml+='<span style="font-size:11px;font-weight:bold;color:#555;margin-right:4px">Groups:</span>';
  legendHtml+='<button onclick="toggleAllGrps(\''+sid+'\',true)" style="font-size:10px;padding:1px 6px;border-radius:3px;border:1px solid #bbb;background:#e8f0fe;cursor:pointer">All</button>';
  legendHtml+='<button onclick="toggleAllGrps(\''+sid+'\',false)" style="font-size:10px;padding:1px 6px;border-radius:3px;border:1px solid #bbb;background:#fef0e8;cursor:pointer">None</button>';
  cm.keys.forEach(function(gk){
    var col=cm.map[gk];
    var lLabel=multiY?(labels[gk]||gk):gk;
    var hidden=!!st.hiddenGrps[gk];
    var opacity=hidden?'0.35':'1';
    legendHtml+='<label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:12px;opacity:'+opacity+'">';
    legendHtml+='<input type="checkbox"'+(hidden?'':' checked')+' onchange="toggleGrpVis(\''+sid+'\',\''+gk.replace(/'/g,"\\\\'")+'\')" style="cursor:pointer;accent-color:'+col+'">';
    legendHtml+='<span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:'+col+';flex-shrink:0"></span>';
    legendHtml+=esc(lLabel)+'</label>';
  });
  legendHtml+='</div>';
  cont.innerHTML='<div style="overflow-x:auto">'+p.join('')+'</div>'+legendHtml;
}

/* CSV download */
function downloadXYCSV(sid){
  var st=_xyState(sid);
  var xc=st.x,ys=st.ys,labels=_LABELS[sid]||{};
  var rows=_DATA[sid]||[];
  var header=['Lot','Wafer','Material','Program',labels[xc]||xc].concat(
    ys.map(function(y){return labels[y]||y;}));
  var lines=[header.join(',')];
  rows.forEach(function(r){
    if(!_SEL.has(r._idx))return;
    var vals=[r.lot,r.wafer,r.mat,r.program,r[xc]].concat(
      ys.map(function(y){return r[y];}));
    lines.push(vals.map(function(v){return v==null?'':String(v);}).join(','));
  });
  var blob=new Blob([lines.join('\n')],{type:'text/csv'});
  var a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='PCMProgram_'+sid+'_scatter.csv';
  document.body.appendChild(a);a.click();
  setTimeout(function(){document.body.removeChild(a);URL.revokeObjectURL(a.href);},1000);
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# HTML section tab builder
# ─────────────────────────────────────────────────────────────────────────────

def _tab_panel(sid, is_first, groups=None):
    """Return HTML for one tab panel (P2 table + P3 scatter with full XY controls)."""
    active = (' active' if is_first else '')
    groups = groups or []

    # Group option HTML for X/Y group selects
    grp_opts = '<option value="">All</option>'
    for g in groups:
        grp_opts += '<option value="{g}">{g}</option>'.format(g=g)

    tmpl = (
        '<div id="tp-SID" class="tab-panel ACT">\n'
        '  <div class="p2-wrap" id="p2-SID">\n'
        '    <div class="p2-hdr">Parameter Summary\n'
        '      <button class="wfr-btn" title="Hide table"'
        ' onclick="document.getElementById(\'p2-SID\').classList.toggle(\'p2-hidden\')">&#9664;</button>\n'
        '    </div>\n'
        '    <div class="p2-body">\n'
        '      <table class="sum-tbl">\n'
        '        <thead><tr><th>Lot</th><th>Wafer</th><th>Mat</th><th>Tag</th>'
        '<th>Param</th><th class="num">Value</th><th>Unit</th></tr></thead>\n'
        '        <tbody id="tbl-tbody-SID"></tbody>\n'
        '      </table>\n'
        '    </div>\n'
        '  </div>\n'
        '  <div class="sp23"></div>\n'
        '  <div class="p3-wrap" id="p3-SID">\n'
        # ── Controls header (two rows, PCM-analysis style) ──
        '    <div style="display:flex;flex-direction:column;flex-shrink:0;'
        'background:#f8f9fa;border-bottom:1px solid #dde;padding:6px 14px;gap:5px">\n'
        # Row 1: title · X group · X select · log X | Y group · Y multi-select · log Y · Per die
        '      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">\n'
        '        <b style="font-size:13px">&#10799; XY Scatter Plot</b>\n'
        '        <label style="font-size:12px;display:flex;align-items:center;gap:3px">X group:\n'
        '          <select onchange="_xySetGrp(\'SID\',\'x\',this.value)"'
        ' style="font-size:12px;padding:2px 4px;border-radius:3px;border:1px solid #ccc">'
        + grp_opts +
        '          </select></label>\n'
        '        <label style="font-size:12px;display:flex;align-items:center;gap:3px">X:\n'
        '          <select id="xy-sel-x-SID" style="font-size:12px;padding:2px 4px;border-radius:3px;'
        'border:1px solid #ccc;max-width:220px"'
        ' onchange="_xyState(\'SID\').x=this.value;buildXYTab(\'SID\')"></select>\n'
        '        </label>\n'
        '        <label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:3px">'
        '<input type="checkbox" onchange="_xyState(\'SID\').logX=this.checked;buildXYTab(\'SID\')"> log X</label>\n'
        '        <span style="width:1px;background:#ccc;align-self:stretch;margin:0 2px"></span>\n'
        '        <label style="font-size:12px;display:flex;align-items:center;gap:3px">Y group:\n'
        '          <select onchange="_xySetGrp(\'SID\',\'y\',this.value);_xyBuildYChecklist(\'SID\')"'
        ' style="font-size:12px;padding:2px 4px;border-radius:3px;border:1px solid #ccc">'
        + grp_opts +
        '          </select></label>\n'
        # Y multi-select dropdown
        '        <span style="font-size:12px;position:relative;display:inline-block">\n'
        '          Y: <button id="xy-y-btn-SID" onclick="_xyYDropToggle(\'SID\')"'
        ' style="font-size:12px;padding:2px 8px;border-radius:3px;border:1px solid #ccc;'
        'background:#fff;cursor:pointer;min-width:110px;max-width:200px;text-align:left;'
        'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">Y\u2026</button>\n'
        '          <div id="xy-y-drop-SID" style="display:none;position:absolute;top:100%;left:0;'
        'z-index:9999;background:#fff;border:1px solid #ccc;border-radius:4px;'
        'box-shadow:0 4px 12px rgba(0,0,0,0.15);min-width:260px;max-width:400px">\n'
        '            <div style="display:flex;align-items:center;gap:4px;padding:5px 6px;'
        'border-bottom:1px solid #e8e8e8;background:#f5f5f5">\n'
        '              <input id="xy-y-srch-SID" placeholder="Search params\u2026"'
        ' oninput="_xyState(\'SID\').ysrch=this.value;_xyBuildYChecklist(\'SID\')"'
        ' style="flex:1;font-size:11px;padding:3px 6px;border:1px solid #ccc;border-radius:3px">\n'
        '              <button onclick="_xyYSelAll(\'SID\')"'
        ' style="font-size:11px;padding:2px 7px;border-radius:3px;border:1px solid #bbb;'
        'background:#e8f0fe;cursor:pointer" title="Select all visible">All</button>\n'
        '              <button onclick="_xyYClrAll(\'SID\')"'
        ' style="font-size:11px;padding:2px 7px;border-radius:3px;border:1px solid #bbb;'
        'background:#fef0e8;cursor:pointer" title="Clear selection">Clr</button>\n'
        '            </div>\n'
        '            <div id="xy-y-list-SID" style="max-height:240px;overflow-y:auto;padding:3px 0"></div>\n'
        '          </div>\n'
        '        </span>\n'
        '        <label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:3px">'
        '<input type="checkbox" onchange="_xyState(\'SID\').logY=this.checked;buildXYTab(\'SID\')"> log Y</label>\n'
        '        <label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:3px">'
        '<input type="checkbox" onchange="_xyState(\'SID\').die=this.checked;buildXYTab(\'SID\')"'
        ' title="Wafer-level medians only"> Per die</label>\n'
        '      </div>\n'
        # Row 2: X range | Y range | Trend | Group by | Size | CSV
        '      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">\n'
        '        <input id="xy-xmin-SID" type="number" placeholder="auto" title="X min"'
        ' onchange="_xyState(\'SID\').xmin=this.value?+this.value:null;buildXYTab(\'SID\')"'
        ' style="width:72px;font-size:12px;padding:2px 4px;border:1px solid #ccc;border-radius:3px">\n'
        '        <span style="font-size:11px;color:#aaa">&#8211;</span>\n'
        '        <input id="xy-xmax-SID" type="number" placeholder="auto" title="X max"'
        ' onchange="_xyState(\'SID\').xmax=this.value?+this.value:null;buildXYTab(\'SID\')"'
        ' style="width:72px;font-size:12px;padding:2px 4px;border:1px solid #ccc;border-radius:3px">\n'
        '        <span style="width:1px;background:#ccc;align-self:stretch;margin:0 4px"></span>\n'
        '        <span style="font-size:12px;color:#555">Y range:</span>\n'
        '        <input id="xy-ymin-SID" type="number" placeholder="auto" title="Y min"'
        ' onchange="_xyState(\'SID\').ymin=this.value?+this.value:null;buildXYTab(\'SID\')"'
        ' style="width:72px;font-size:12px;padding:2px 4px;border:1px solid #ccc;border-radius:3px">\n'
        '        <span style="font-size:11px;color:#aaa">&#8211;</span>\n'
        '        <input id="xy-ymax-SID" type="number" placeholder="auto" title="Y max"'
        ' onchange="_xyState(\'SID\').ymax=this.value?+this.value:null;buildXYTab(\'SID\')"'
        ' style="width:72px;font-size:12px;padding:2px 4px;border:1px solid #ccc;border-radius:3px">\n'
        '        <span style="width:1px;background:#ccc;align-self:stretch;margin:0 4px"></span>\n'
        '        <span style="font-size:12px;color:#555">Trend:</span>\n'
        '        <label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:3px">'
        '<input type="radio" name="xy-trend-SID" value="none"'
        ' onchange="_xyState(\'SID\').trend=this.value;buildXYTab(\'SID\')"> None</label>\n'
        '        <label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:3px">'
        '<input type="radio" name="xy-trend-SID" value="ols" checked'
        ' onchange="_xyState(\'SID\').trend=this.value;buildXYTab(\'SID\')"> OLS</label>\n'
        '        <label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:3px">'
        '<input type="radio" name="xy-trend-SID" value="theilsen"'
        ' onchange="_xyState(\'SID\').trend=this.value;buildXYTab(\'SID\')"> Theil-Sen</label>\n'
        '        <span style="width:1px;background:#ccc;align-self:stretch;margin:0 4px"></span>\n'
        '        <span id="xy-gby-wrap-SID" style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">\n'
        '          <b style="font-size:12px;color:#555">Group by:</b>\n'
        '          <label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:3px">'
        '<input type="checkbox" class="vgb-cb-SID" value="none" checked'
        ' onchange="toggleGby(\'SID\',\'none\')"> None</label>\n'
        '          <label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:3px">'
        '<input type="checkbox" class="vgb-cb-SID" value="lot"'
        ' onchange="toggleGby(\'SID\',\'lot\')"> Lot</label>\n'
        '          <label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:3px">'
        '<input type="checkbox" class="vgb-cb-SID" value="wafer"'
        ' onchange="toggleGby(\'SID\',\'wafer\')"> Wafer</label>\n'
        '          <label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:3px">'
        '<input type="checkbox" class="vgb-cb-SID" value="material"'
        ' onchange="toggleGby(\'SID\',\'material\')"> Material</label>\n'
        '          <label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:3px">'
        '<input type="checkbox" class="vgb-cb-SID" value="program"'
        ' onchange="toggleGby(\'SID\',\'program\')"> Program</label>\n'
        '        </span>\n'
        '        <span style="width:1px;background:#ccc;align-self:stretch;margin:0 4px"></span>\n'
        '        <label style="font-size:12px;display:flex;align-items:center;gap:4px;cursor:default">'
        '&#11041; Size\n'
        '          <input id="xy-h-slider-SID" type="range" min="200" max="900" step="25" value="500"'
        ' oninput="_xyState(\'SID\').h=+this.value;document.getElementById(\'xy-h-val-SID\').textContent=this.value+\'px\';buildXYTab(\'SID\')"'
        ' style="width:90px;accent-color:#3498db">\n'
        '          <span id="xy-h-val-SID" style="min-width:34px;font-size:10px;color:#555">500px</span>\n'
        '        </label>\n'
        '        <span style="width:1px;background:#ccc;align-self:stretch;margin:0 4px"></span>\n'
        '        <button onclick="downloadXYCSV(\'SID\')" title="Download scatter data as CSV"'
        ' style="padding:3px 10px;font-size:11px;font-weight:bold;border:none;border-radius:4px;'
        'background:#27ae60;color:#fff;cursor:pointer"'
        ' onmouseover="this.style.background=\'#1e8449\'"'
        ' onmouseout="this.style.background=\'#27ae60\'">&#11015; CSV</button>\n'
        '      </div>\n'
        '    </div>\n'
        '    <div id="xy-cont-SID" style="flex:1;overflow-y:auto;padding:0 14px 10px"></div>\n'
        '  </div>\n'
        '</div>'
    ).replace('SID', sid).replace(' ACT', active)
    return tmpl
# ─────────────────────────────────────────────────────────────────────────────

def generate_pcmprog_html(
    df,
    out_folder,
    identifier,
    spec_lookup,
    lots,
    repo_root,
):
    """Generate PCMProgram.html and return the file path."""
    out_folder = Path(out_folder)
    out_folder.mkdir(parents=True, exist_ok=True)
    out_path = out_folder / "PCMProgram.html"

    if df is None or df.empty:
        out_path.write_text(
            "<html><body><p style='padding:24px;font-family:Arial'>No data available.</p></body></html>",
            encoding="utf-8",
        )
        return str(out_path)

    spec_lookup = spec_lookup or {}

    # Detect stepping from identifier (e.g. "NCXSDJXL0H61A002618-L0-param" → "L0")
    _step_m = re.search(r'[-_](L\d|P\d|R\d)[-_]', str(identifier), re.I)
    _stepping = _step_m.group(1).upper() if _step_m else "L0"

    td_cols, ioff_cols, upm_cols, sicc_cols = _classify_columns(df)
    prog_col = _find_program_col(df)

    td_cols   = td_cols[:12]
    ioff_cols = ioff_cols[:20]
    upm_cols  = upm_cols[:6]
    sicc_cols = sicc_cols[:8]

    freq_cols = [_freq_col(c) for c in td_cols]

    # Build shared wafer list
    id_cols = ["Lot", "Wafer"]
    if "Material" in df.columns:
        id_cols.append("Material")
    uniq_df = df[id_cols].drop_duplicates()

    prog_by_lot = {}
    if prog_col and prog_col in df.columns:
        for lot, grp in df.groupby("Lot"):
            vals = grp[prog_col].dropna().unique()
            prog_by_lot[str(lot)] = str(vals[0]) if len(vals) > 0 else ""

    counts = _count_per_wafer(df)
    wafer_list = []
    key_to_idx = {}
    for _, row in uniq_df.iterrows():
        lot_s = str(row.get("Lot",   ""))
        wfr_s = str(row.get("Wafer", ""))
        mat_s = str(row.get("Material", "")) if "Material" in id_cols else ""
        k = "{}||{}||{}".format(lot_s, wfr_s, mat_s)
        if k in key_to_idx:
            continue
        idx = len(wafer_list)
        key_to_idx[k] = idx
        wafer_list.append({
            "lot":     lot_s,
            "wafer":   wfr_s,
            "mat":     mat_s,
            "program": prog_by_lot.get(lot_s, ""),
            "n":       counts.get((lot_s, wfr_s, mat_s), 1),
        })

    def _make_rows(num_cols, freq_td_cols=None):
        rows = _build_wafer_rows(df, num_cols,
                                 program_col=prog_col,
                                 freq_td_cols=freq_td_cols)
        for r in rows:
            k = "{}||{}||{}".format(r["lot"], r["wafer"], r["mat"])
            r["_idx"] = key_to_idx.get(k, -1)
        return rows

    upm_rows  = _make_rows(upm_cols + td_cols, freq_td_cols=td_cols)
    sicc_rows = _make_rows(sicc_cols + ioff_cols, freq_td_cols=None)

    # Labels
    upm_labels = {}
    for c in upm_cols:
        upm_labels[c] = _friendly_upm(c)
    for c in freq_cols:
        upm_labels[c] = _friendly_freq(c)
    for c in td_cols:
        upm_labels[c] = _friendly_td(c)

    sicc_labels = {}
    for c in sicc_cols:
        sicc_labels[c] = _friendly_sicc(c)
    for c in ioff_cols:
        sicc_labels[c] = _friendly_ioff(c)

    upm_col_order  = freq_cols + td_cols + upm_cols
    sicc_col_order = ioff_cols + sicc_cols

    # Spec
    spec_js = {}
    for c, entry in spec_lookup.items():
        if isinstance(entry, (list, tuple)) and len(entry) >= 4:
            sl, sh, tgt, unit = entry[0], entry[1], entry[2], entry[3]
        elif isinstance(entry, dict):
            sl   = entry.get("sl") or entry.get("lsl")
            sh   = entry.get("sh") or entry.get("usl")
            tgt  = entry.get("tgt") or entry.get("target")
            unit = entry.get("unit", "")
        else:
            continue
        def _clean(v):
            return None if (v is None or (isinstance(v, float) and math.isnan(v))) else v
        spec_js[c] = {"sl": _clean(sl), "sh": _clean(sh),
                      "tgt": _clean(tgt), "unit": str(unit) if unit else ""}
    for fc in freq_cols:
        spec_js[fc] = {"sl": None, "sh": None, "tgt": None, "unit": "GHz"}

    # Overlay sort-specific specs from shared/spec/ (UPM target + SICC per-domain limits)
    try:
        _sort_spec = _load_sort_spec(repo_root, _stepping)
        _sort_overrides = _apply_sort_spec(_sort_spec, upm_cols, sicc_cols)
        for c, entry in _sort_overrides.items():
            # Only overlay if not already set by spec_lookup
            if c not in spec_js:
                spec_js[c] = entry
        print(f"[pcmprog] Sort spec overlaid: {len(_sort_overrides)} cols "
              f"(UPM tgt={_sort_spec['_upm']['target']}, "
              f"stepping={_stepping})")
    except Exception as _se:
        print(f"[pcmprog] WARNING: sort spec overlay failed: {_se}")


    def _pick_upm():
        for c in upm_cols:
            if "0950" in c or "950" in c:
                return c
        return upm_cols[0] if upm_cols else ""

    def _pick_freq():
        for fc, tc in zip(freq_cols, td_cols):
            key = re.sub(r"^Td_", "", tc, flags=re.I)
            if key in ("RA4u", "RJ4u"):
                return fc
        return freq_cols[0] if freq_cols else ""

    def _pick_sicc():
        for c in sicc_cols:
            if "VCCATOM" in c.upper():
                return c
        return sicc_cols[0] if sicc_cols else ""

    def _pick_ioff():
        for c in ioff_cols:
            if "RNA4u" in c or "RNA4U" in c:
                return c
        return ioff_cols[0] if ioff_cols else ""

    upm_default_x  = _pick_upm()
    upm_default_y  = _pick_freq()
    sicc_default_x = _pick_sicc()
    sicc_default_y = _pick_ioff()

    lots_str = ", ".join(lots) if lots else "—"

    # Groups per tab (for X/Y group selects)
    upm_groups   = ["Freq (GHz)", "Delay (Td)", "UPM"]
    sicc_groups  = ["Ioff", "SICC"]
    js_groups = json.dumps({"upm_td": upm_groups, "sicc_ioff": sicc_groups},
                            ensure_ascii=False)
    js_group_cols = json.dumps({
        "upm_td": {
            "Freq (GHz)": freq_cols,
            "Delay (Td)": td_cols,
            "UPM":        upm_cols,
        },
        "sicc_ioff": {
            "Ioff": ioff_cols,
            "SICC": sicc_cols,
        },
    }, ensure_ascii=False)

    # Serialise data
    js_wfr     = json.dumps(wafer_list, ensure_ascii=False)
    js_data    = json.dumps({"upm_td": upm_rows, "sicc_ioff": sicc_rows},      ensure_ascii=False)
    js_labels  = json.dumps({"upm_td": upm_labels, "sicc_ioff": sicc_labels},  ensure_ascii=False)
    js_col_ord = json.dumps({"upm_td": upm_col_order, "sicc_ioff": sicc_col_order}, ensure_ascii=False)
    js_sort_k  = json.dumps({"upm_td": upm_cols, "sicc_ioff": sicc_cols},       ensure_ascii=False)
    js_spec    = json.dumps(spec_js,  ensure_ascii=False)
    js_xcol    = json.dumps({"upm_td": upm_default_x,  "sicc_ioff": sicc_default_x}, ensure_ascii=False)
    js_ycol    = json.dumps({"upm_td": upm_default_y,  "sicc_ioff": sicc_default_y}, ensure_ascii=False)

    html_parts = [
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n",
        "<meta charset=\"utf-8\">\n",
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n",
        "<title>PCM-Program Correlation \u2014 {}</title>\n".format(identifier),
        "<style>", _CSS, "</style>\n",
        "</head>\n<body>\n<div id=\"shell\">\n",
        "<div class=\"page-hdr\">\n",
        "  <h1>PCM-Program Correlation Dashboard</h1>\n",
        "  <div class=\"sub\">{} &nbsp;&#124;&nbsp; Lots: {}</div>\n".format(identifier, lots_str),
        "</div>\n",
        "<div id=\"body-row\">\n",
        "  <div id=\"panel1\">\n",
        "    <div class=\"p1-hdr\">Wafer Filter",
        "<span id=\"row-info\" class=\"row-info\"></span>\n",
        "      <div style=\"display:flex;gap:3px\">\n",
        "        <button class=\"wfr-btn\" onclick=\"_selAll()\">All</button>\n",
        "        <button class=\"wfr-btn\" onclick=\"_clrAll()\">None</button>\n",
        "      </div>\n    </div>\n",
        "    <div class=\"p1-srch\">\n",
        "      <input placeholder=\"Wafer\" style=\"width:48px\" oninput=\"_onSearch('wafer',this.value)\">\n",
        "      <input placeholder=\"Program\" style=\"width:90px\" oninput=\"_onSearch('program',this.value)\">\n",
        "      <input placeholder=\"Mat\" style=\"width:40px\" oninput=\"_onSearch('mat',this.value)\">\n",
        "    </div>\n",
        "    <div class=\"p1-body\">\n",
        "      <table class=\"wfr-tbl\"><thead>\n",
        "        <tr><th>Wafer</th><th>Program</th><th>Material</th><th class=\"num\">N</th></tr>\n",
        "      </thead><tbody id=\"wfr-tbody\"></tbody></table>\n",
        "    </div>\n  </div>\n",
        "  <div class=\"sp12\"></div>\n",
        "  <div id=\"main-area\">\n",
        "    <div class=\"tabs\">\n",
        "      <button class=\"tab-btn active\" data-sid=\"upm_td\" onclick=\"_showTab('upm_td')\">UPM vs Freq (Delay)</button>\n",
        "      <button class=\"tab-btn\" data-sid=\"sicc_ioff\" onclick=\"_showTab('sicc_ioff')\">SICC vs Poff (Ioff)</button>\n",
        "    </div>\n",
        _tab_panel("upm_td",    is_first=True,  groups=upm_groups),  "\n",
        _tab_panel("sicc_ioff", is_first=False, groups=sicc_groups), "\n",
        "  </div>\n",
        "</div>\n",
        "<div id=\"tip-box\"></div>\n",
        "</div>\n",
        "<script>\n",
        "var _WFR       = {};\n".format(js_wfr),
        "var _DATA      = {};\n".format(js_data),
        "var _LABELS    = {};\n".format(js_labels),
        "var _COL_ORDER = {};\n".format(js_col_ord),
        "var _SORT_KEYS = {};\n".format(js_sort_k),
        "var _SPEC      = {};\n".format(js_spec),
        "var _XCOL      = {};\n".format(js_xcol),
        "var _YCOL      = {};\n".format(js_ycol),
        "var _GROUPS    = {};\n".format(js_groups),
        "var _GROUP_COLS = {};\n".format(js_group_cols),
        _JS, "\n",
        "_buildFilter();\n",
        "_showTab('upm_td');\n",
        "_rerender();\n",
        "</script>\n</body>\n</html>",
    ]

    out_path.write_text("".join(html_parts), encoding="utf-8")
    return str(out_path)
