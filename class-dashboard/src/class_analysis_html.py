"""class_analysis_html.py — CLASS analysis page generator.

Three-panel architecture following chart_tools specs:
  Panel 1 : Lot/Wafer filter (Program_Name_6248, Program_Name_U1.U5,
            Sort_lot, sort_Wafer, sort_x, sort_y)
  Panel 2 : Parameter stats table  (SICC Sort, SICC Class, Vmin Core/Atom/CCF)
  Panel 3 : Chart tabs
              Variability   — strip chart cards for all groups
              Distribution  — histogram panels for SICC Sort + SICC Class
              VMIN-CORE     — XY scatter
              VMIN-ATOM     — XY scatter
              VMIN-CCF      — XY scatter

Public API
----------
    generate_html(df, product_config, vmin_meta, output_path,
                  spec_lookup=None) -> str
        Returns output_path.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

try:
    from _constants import _wm_inject
except ImportError:
    def _wm_inject(h: str) -> str:          # type: ignore[misc]
        return h


# ── Test-program .msg loader ──────────────────────────────────────────────────

def _load_tp_info(tp_dir: str = '') -> dict:
    """Parse Outlook .msg TP build-report files in *tp_dir*.

    Returns {tp_name: {field: value, ...}} keyed by 'Test Program Name'.
    Returns {} if extract-msg is not available or directory is empty/missing.
    """
    import glob as _glob
    import os as _os

    if not tp_dir:
        try:
            from _constants import _REPO_ROOT as _RR
            tp_dir = _os.path.join(_RR, 'shared', 'setup', 'automation',
                                    'class-dashboard', 'TestProgram')
        except ImportError:
            return {}

    if not _os.path.isdir(tp_dir):
        return {}

    try:
        import extract_msg as _em
    except ImportError:
        return {}

    # Map raw label substrings → snake_case keys used by the JS popup
    _KEY_MAP = [
        ('short name',    'nick_name'),
        ('nick name',     'nick_name'),
        ('test program name', 'tp_name'),
        ('products',      'products'),
        ('subfamily',     'products'),
        ('integrator',    'integrator'),
        ('tos profile',   'tos_profile'),
        ('tos_profile',   'tos_profile'),
        ('stepping',      'stepping'),
        ('classif',       'classification'),
        ('prime',         'prime_path'),
        ('skipped module', 'skipped_modules'),
    ]
    _SKIP = ('owner', 'objective', 'fuse path', 'issues', 'summary',
             'correlation', 'submission', 'recipients', 'ref_vpo', 'new_vpo')

    def _map_key(raw: str) -> str | None:
        rl = raw.lower()
        if any(s in rl for s in _SKIP):
            return None
        for pat, name in _KEY_MAP:
            if pat in rl:
                return name
        return None

    def _parse_body(text: str) -> dict:
        fields: dict = {}

        # Extract Built Date — may be "Built Date YYYY-MM-DD" (single space)
        m = re.search(r'\bBuilt Date\s+(\S+)', text)
        if m:
            fields['built_date'] = m.group(1).strip()

        # Locate "Test Program Summary Information" section
        sec_idx = text.find('Test Program Summary Information')
        section = text[sec_idx:] if sec_idx >= 0 else text

        # Format A: tab-separated  "Key\tValue"
        tab_hits = 0
        for raw_line in section.splitlines():
            if '\t' not in raw_line:
                continue
            key_raw, _, val = raw_line.partition('\t')
            key_raw = key_raw.strip()
            val = val.strip()
            if not key_raw or not val:
                continue
            mapped = _map_key(key_raw)
            if mapped and mapped not in fields:
                fields[mapped] = val
            tab_hits += 1

        # Format B: blank-line-separated  "Key\n\nValue"  (older RE: mails)
        if tab_hits == 0:
            lines = [ln.strip() for ln in section.splitlines()]
            i = 0
            while i < len(lines):
                if not lines[i]:
                    i += 1
                    continue
                mapped = _map_key(lines[i])
                if mapped and mapped not in fields:
                    j = i + 1
                    while j < len(lines) and not lines[j]:
                        j += 1
                    if j < len(lines) and lines[j]:
                        fields[mapped] = lines[j]
                        i = j + 1
                        continue
                i += 1

        # Split skipped_modules string into list
        if 'skipped_modules' in fields and isinstance(fields['skipped_modules'], str):
            fields['skipped_modules'] = [m.strip() for m in fields['skipped_modules'].split(',') if m.strip()]

        # Capture full body from report title line (use rfind to skip Subject: header in RE: mails)
        _fbd_idx = text.rfind('Full Daily Build Report')
        if _fbd_idx >= 0:
            _lb = text.rfind('\n', 0, _fbd_idx)
            _full = text[_lb + 1:].strip()
            _full = _full.replace('\r\n', '\n').replace('\r', '\n')
            _full = re.sub(r'\n{3,}', '\n\n', _full)
            fields['full_body'] = _full

        return fields

    result: dict = {}
    for fpath in sorted(_glob.glob(_os.path.join(tp_dir, '*.msg'))):
        try:
            msg = _em.openMsg(fpath)
            body = msg.body or ''
            fields = _parse_body(body)
            tp_name = fields.get('tp_name', '').strip()
            # Fallback: parse TP name from subject line
            if not tp_name:
                subj = getattr(msg, 'subject', '') or ''
                sm = re.match(r'(?:RE:\s*\[.*?\]\s*|RE:\s*|\[.*?\]\s*)?(\S+)\s+Full\s+Daily', subj, re.IGNORECASE)
                if sm:
                    tp_name = sm.group(1)
            if tp_name:
                result[tp_name] = fields
        except Exception:
            pass
    return result


# ── Constants ─────────────────────────────────────────────────────────────────
_MAX_DIE_VALUES = 500       # cap per (wafer × param) row
_BANNER_COLS = [
    '#1a5276', '#117a65', '#6e2f8a', '#7d4e00',
    '#922b21', '#1a6e2b', '#1a3a72', '#7d4500',
]
_COLOUR_PAL = [
    '#e74c3c','#3498db','#2ecc71','#f39c12','#9b59b6',
    '#1abc9c','#e67e22','#34495e','#16a085','#8e44ad',
    '#27ae60','#2980b9','#d35400','#7f8c8d','#c0392b',
]


# ── Helpers ───────────────────────────────────────────────────────────────────
def _esc(s: str) -> str:
    return re.sub(r'[^a-zA-Z0-9]', '_', s)


def _safe(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if (f != f) else round(f, 6)
    except Exception:
        return None


def _fmt_n(v, prec: int = 4) -> str:
    if v is None:
        return ''
    try:
        f = float(v)
        if f != f:
            return ''
        if abs(f) >= 1000:
            return f'{f:.0f}'
        if abs(f) >= 10:
            return f'{f:.1f}'
        return f'{f:.{prec}g}'
    except Exception:
        return ''


def _find_col(cols: List[str], *needles: str) -> Optional[str]:
    """Case-insensitive match; normalises spaces/underscores before comparing."""
    def _norm(s: str) -> str:
        return s.upper().replace('_', ' ')
    for c in cols:
        cn = _norm(c)
        if all(_norm(n) in cn for n in needles):
            return c
    return None


def _param_cols(df_cols: List[str], patterns: List[str]) -> List[str]:
    import fnmatch
    result: List[str] = []
    for pat in patterns:
        for c in df_cols:
            if (fnmatch.fnmatch(c.upper(), pat.upper()) or
                    fnmatch.fnmatch(c, pat)):
                if c not in result:
                    result.append(c)
    return result


# ── Data builders ─────────────────────────────────────────────────────────────

def _build_wfr_data(df: pd.DataFrame,
                    lot_col: str, wafer_col: str,
                    prog6248_col: Optional[str],
                    progU1U5_col: Optional[str],
                    x_col: Optional[str],
                    y_col: Optional[str],
                    sort_lot_col: Optional[str] = None,
                    material_col: Optional[str] = None,
                    dev_rev_col: Optional[str] = None) -> list:
    rows = []
    # Group by (lot, wafer, prog6248) so that wafers tested under multiple
    # class programs (e.g. BS622 → DS622 retest) each get their own row.
    group_keys = [lot_col, wafer_col]
    if prog6248_col and prog6248_col in df.columns:
        group_keys.append(prog6248_col)
    for group_vals, g in df.groupby(group_keys, sort=False):
        if len(group_keys) == 3:
            lot, wafer, prog6248_val = group_vals
        else:
            lot, wafer = group_vals
            prog6248_val = None

        def _first_str(col):
            if col and col in g.columns:
                vs = g[col].dropna()
                return str(vs.iloc[0]) if len(vs) else ''
            return ''

        def _irange(col):
            if col and col in g.columns:
                s = pd.to_numeric(g[col], errors='coerce').dropna()
                if len(s):
                    return int(s.min()), int(s.max())
            return None, None

        xmin, xmax = _irange(x_col)
        ymin, ymax = _irange(y_col)
        rows.append({
            'lot':      str(lot),
            'sort_lot': _first_str(sort_lot_col),
            'wafer':    str(int(float(wafer))) if str(wafer).replace('.','',1).isdigit() else str(wafer),
            'prog6248': str(prog6248_val) if prog6248_val is not None else _first_str(prog6248_col),
            'progU1U5': _first_str(progU1U5_col),
            'dev_rev':  _first_str(dev_rev_col),
            'xmin': xmin, 'xmax': xmax,
            'ymin': ymin, 'ymax': ymax,
            'n': len(g),
            'material': _first_str(material_col),
            'upm_med':  None,
        })
    return rows


def _build_pcm_rows(df: pd.DataFrame,
                    lot_col: str, wafer_col: str,
                    all_params: List[str],
                    param_group: Dict[str, str],
                    upm_refs: Optional[Dict[str, float]] = None,
                    prog6248_col: Optional[str] = None,
                    pkg_col: Optional[str] = None) -> list:
    def _norm_wfr(w):
        s = str(w)
        return str(int(float(s))) if s.replace('.', '', 1).isdigit() else s

    # Group by (lot, wafer, prog6248) — one PCM row set per program.
    # Within each group, deduplicate by VISUAL_ID (PKG) keeping the last result.
    # This allows the prog6248 filter to change the sample count correctly.
    _grp_keys = [lot_col, wafer_col]
    if prog6248_col and prog6248_col in df.columns:
        _grp_keys.append(prog6248_col)

    rows = []
    for group_vals, g in df.groupby(_grp_keys, sort=False):
        if len(_grp_keys) == 3:
            lot, wafer, prog6248_val = group_vals
        else:
            lot, wafer = group_vals
            prog6248_val = None
        lot_s, wfr_s = str(lot), _norm_wfr(wafer)
        prog_s = str(prog6248_val) if prog6248_val is not None else ''
        # Deduplicate by VISUAL_ID within this program group
        if pkg_col and pkg_col in g.columns:
            g = g.drop_duplicates(subset=[pkg_col], keep='last')
        for param in all_params:
            if param not in g.columns:
                continue
            col_data = g[param]
            if isinstance(col_data, pd.DataFrame):
                col_data = col_data.iloc[:, 0]
            s = pd.to_numeric(col_data, errors='coerce').dropna()
            s = s[s > 0]
            _upm_ref = None
            if upm_refs and param in upm_refs:
                try:
                    _upm_ref = float(upm_refs[param])
                except Exception:
                    _upm_ref = None
            if _upm_ref and _upm_ref > 0:
                s = (s / _upm_ref) * 100.0
            n = len(s)
            if n == 0:
                continue
            med = float(s.median())
            std = float(s.std()) if n > 1 else 0.0
            cv  = (_safe(std / abs(med) * 100) if med != 0 else None)
            dvs = [round(float(v), 5) for v in s.values[:_MAX_DIE_VALUES]]
            rows.append({
                'lot':       lot_s,
                'wafer':     wfr_s,
                'prog6248':  prog_s,
                'group':     param_group.get(param, ''),
                'param':     param,
                'n':         n,
                'median':    _safe(med),
                'std':       _safe(std),
                'cv':        cv,
                'min_val':   _safe(float(s.min())),
                'max_val':   _safe(float(s.max())),
                'die_values': dvs,
            })
    return rows


# ── Vmin Speed Flow Data Builder ──────────────────────────────────────────────

def _build_vmin_flow_data(
    df: pd.DataFrame,
    vmin_meta: dict,
    pkg_col: Optional[str] = None,
    upm_keys: Optional[list] = None,
    upm_950_ref: Optional[float] = None,
    sort_lot_col: Optional[str] = None,
    sort_wafer_col: Optional[str] = None,
    x_col: Optional[str] = None,
    y_col: Optional[str] = None,
    mat_col: Optional[str] = None,
    prog6248_col: Optional[str] = None,
) -> dict:
    """Per-module, per-instance speed flow data with per-frequency die-level rows.

    The idx suffix (_1/_2/_3/_4) identifies the core/atom instance, NOT a
    speed flow.  Core: _1=DCM0, _2=DCM1, _3=DCM2, _4=DCM3.

    Returns
    -------
    dict: {
        module_key: {
            label, upm_as_pct, upm_950_ref,
            instances: [{
                idx, label,
                freqs: [{
                    freq_mhz, freq_label, sk, n_valid,
                    rows: [[pkg, sort_lot, sort_wafer, x, y, vmin, upm_pct|null], ...]
                          (capped at 1000 per freq-instance)
                }]
            }]
        }
    }
    """
    _mod_disp = {
        'CORE': 'Core', 'ATOM': 'Atom', 'CCF': 'Ring',
        'core': 'Core', 'atom': 'Atom', 'ccf': 'Ring',
    }
    _inst_disp: dict = {
        'CORE': {1: 'DCM0', 2: 'DCM1', 3: 'DCM2', 4: 'DCM3'},
        'ATOM': {1: 'Atom0', 2: 'Atom1', 3: 'Atom2', 4: 'Atom3'},
        'CCF':  {1: 'CCF'},
        'core': {1: 'DCM0', 2: 'DCM1', 3: 'DCM2', 4: 'DCM3'},
        'atom': {1: 'Atom0', 2: 'Atom1', 3: 'Atom2', 4: 'Atom3'},
        'ccf':  {1: 'CCF'},
    }
    upm_avail = [k for k in (upm_keys or []) if k in df.columns]
    upm_950_key = next(
        (k for k in upm_avail if '950' in k and '107' in k),
        upm_avail[0] if upm_avail else None,
    )

    # Pre-extract UPM 950 series for XY pairing
    upm_950_series: Optional[pd.Series] = None
    if upm_950_key and upm_950_key in df.columns:
        ucol = df[upm_950_key]
        if isinstance(ucol, pd.DataFrame):
            ucol = ucol.iloc[:, 0]
        upm_950_series = pd.to_numeric(ucol, errors='coerce')

    # Pre-extract CSV columns as Series for fast access
    _pkg_s  = df[pkg_col].astype(str)           if pkg_col       and pkg_col       in df.columns else None
    _lot_s  = df[sort_lot_col].astype(str)       if sort_lot_col  and sort_lot_col  in df.columns else None
    _wfr_s  = df[sort_wafer_col].astype(str)     if sort_wafer_col and sort_wafer_col in df.columns else None
    _x_s    = pd.to_numeric(df[x_col],   errors='coerce') if x_col  and x_col  in df.columns else None
    _y_s    = pd.to_numeric(df[y_col],   errors='coerce') if y_col  and y_col  in df.columns else None
    _mat_s  = df[mat_col].astype(str)    if mat_col and mat_col in df.columns else None
    _prog_s = df[prog6248_col].astype(str) if prog6248_col and prog6248_col in df.columns else None

    result: dict = {}

    for module, entries in vmin_meta.items():
        # Group entries by idx
        by_idx: dict = {}
        for sk, fmhz, idx, _raw in entries:
            by_idx.setdefault(idx, []).append((sk, fmhz))

        instances = []
        for idx in sorted(by_idx.keys()):
            freq_list = sorted(by_idx[idx], key=lambda x: x[1], reverse=True)
            freqs_data: list = []

            for sk, fmhz in freq_list:
                if sk not in df.columns:
                    continue
                vcol = df[sk]
                if isinstance(vcol, pd.DataFrame):
                    vcol = vcol.iloc[:, 0]
                vmin_s = pd.to_numeric(vcol, errors='coerce')
                valid_pos = vmin_s.index[vmin_s.notna() & (vmin_s > 0)].tolist()
                n_valid = len(valid_pos)

                rows: list = []
                for pos in valid_pos:
                    vmin_val = round(float(vmin_s[pos]), 3)
                    pkg_val  = _pkg_s[pos]  if _pkg_s  is not None else None
                    lot_val  = _lot_s[pos]  if _lot_s  is not None else None
                    wfr_val  = _wfr_s[pos]  if _wfr_s  is not None else None
                    x_val    = int(_x_s[pos]) if _x_s is not None and pd.notna(_x_s[pos]) else None
                    y_val    = int(_y_s[pos]) if _y_s is not None and pd.notna(_y_s[pos]) else None
                    upm_pct  = None
                    if upm_950_series is not None and pos in upm_950_series.index:
                        uv = upm_950_series[pos]
                        if pd.notna(uv) and uv > 0:
                            upm_pct = round(float(uv) / upm_950_ref * 100, 1) if upm_950_ref else round(float(uv), 1)
                    mat_val  = _mat_s[pos].strip()  if _mat_s  is not None else None
                    if mat_val and mat_val.lower() in ('nan', 'none', ''):
                        mat_val = None
                    prog_val = _prog_s[pos]         if _prog_s is not None else None
                    rows.append([pkg_val, lot_val, wfr_val, x_val, y_val, vmin_val, upm_pct, mat_val, prog_val])

                freqs_data.append({
                    'freq_mhz':   fmhz,
                    'freq_label': f'{fmhz / 1000:g}G',
                    'sk':         sk,
                    'n_valid':    n_valid,
                    'rows':       rows,
                })

            inst_label = _inst_disp.get(module, {}).get(idx, f'Inst{idx}')
            instances.append({
                'idx':   idx,
                'label': inst_label,
                'freqs': freqs_data,
            })

        result[module] = {
            'label':       _mod_disp.get(module, module.capitalize()),
            'upm_as_pct':  upm_950_ref is not None,
            'upm_950_ref': float(upm_950_ref) if upm_950_ref else None,
            'instances':   instances,
        }

    return result


def _build_vmin_pass_table(
    df: pd.DataFrame,
    vmin_meta: dict,
    pkg_col: Optional[str] = None,
    sort_lot_col: Optional[str] = None,
    sort_wafer_col: Optional[str] = None,
    x_col: Optional[str] = None,
    y_col: Optional[str] = None,
    upm_keys: Optional[list] = None,
    upm_950_ref: Optional[float] = None,
    mat_col: Optional[str] = None,
    prog6248_col: Optional[str] = None,
) -> dict:
    """For each module/freq, group units by number of DCMs with valid Vmin > 0.

    Returns {module: {label, n_instances, freq_data: {str(fmhz): {
        freq_mhz, freq_label, groups: {4: {n, med_vmin, rows, n_total}, 2:..., 1:...}
    }}}}
    Rows: [pkg, sort_lot, sort_wafer, x, y, avg_vmin, upm_pct]
    """
    upm_avail = [k for k in (upm_keys or []) if k in df.columns]
    upm_950_key = next(
        (k for k in upm_avail if '950' in k and '107' in k),
        upm_avail[0] if upm_avail else None,
    )
    upm_950_series: Optional[pd.Series] = None
    if upm_950_key and upm_950_key in df.columns:
        ucol = df[upm_950_key]
        if isinstance(ucol, pd.DataFrame):
            ucol = ucol.iloc[:, 0]
        upm_950_series = pd.to_numeric(ucol, errors='coerce')

    _pkg_s  = df[pkg_col].astype(str)          if pkg_col        and pkg_col        in df.columns else None
    _lot_s  = df[sort_lot_col].astype(str)     if sort_lot_col   and sort_lot_col   in df.columns else None
    _wfr_s  = df[sort_wafer_col].astype(str)   if sort_wafer_col and sort_wafer_col in df.columns else None
    _x_s    = pd.to_numeric(df[x_col],   errors='coerce') if x_col  and x_col  in df.columns else None
    _y_s    = pd.to_numeric(df[y_col],   errors='coerce') if y_col  and y_col  in df.columns else None
    _mat_s  = df[mat_col].astype(str)   if mat_col and mat_col in df.columns else None
    _prog_s = df[prog6248_col].astype(str) if prog6248_col and prog6248_col in df.columns else None

    _mod_disp = {'CORE': 'Core', 'ATOM': 'Atom', 'CCF': 'Ring',
                 'core': 'Core', 'atom': 'Atom', 'ccf':  'Ring'}
    result: dict = {}

    for module, entries in vmin_meta.items():
        # {fmhz: {idx: col}}
        by_freq: dict = {}
        for sk, fmhz, idx, _ in entries:
            if sk in df.columns:
                by_freq.setdefault(fmhz, {})[idx] = sk

        freq_data: dict = {}
        for fmhz in sorted(by_freq.keys(), reverse=True):
            inst_cols = by_freq[fmhz]

            unit_dcm: dict  = {}   # "pkg|lot|wfr" -> {idx: vmin}
            unit_meta: dict = {}   # "pkg|lot|wfr" -> [lot, wfr, x, y, upm]
            unit_mat: dict  = {}   # "pkg|lot|wfr" -> material string
            unit_prog: dict = {}   # "pkg|lot|wfr" -> prog6248 string
            unit_pkg: dict  = {}   # "pkg|lot|wfr" -> original pkg string

            for idx, sk in inst_cols.items():
                vcol = df[sk]
                if isinstance(vcol, pd.DataFrame):
                    vcol = vcol.iloc[:, 0]
                vmin_s = pd.to_numeric(vcol, errors='coerce')
                valid_pos = vmin_s.index[vmin_s.notna() & (vmin_s > 0)]
                for pos in valid_pos:
                    pkg   = _pkg_s[pos] if _pkg_s is not None else str(pos)
                    lot_v = _lot_s[pos] if _lot_s is not None else None
                    wfr_v = _wfr_s[pos] if _wfr_s is not None else None
                    ukey  = f"{pkg}|{lot_v}|{wfr_v}"
                    if ukey not in unit_dcm:
                        unit_dcm[ukey] = {}
                        x_v   = int(_x_s[pos]) if _x_s is not None and pd.notna(_x_s[pos]) else None
                        y_v   = int(_y_s[pos]) if _y_s is not None and pd.notna(_y_s[pos]) else None
                        upm_v = None
                        if upm_950_series is not None and pos in upm_950_series.index:
                            uv = upm_950_series[pos]
                            if pd.notna(uv) and uv > 0:
                                upm_v = round(float(uv) / upm_950_ref * 100, 1) if upm_950_ref else round(float(uv), 1)
                        unit_meta[ukey] = [lot_v, wfr_v, x_v, y_v, upm_v]
                        unit_mat[ukey]  = _mat_s[pos].strip()  if _mat_s  is not None else None
                        unit_prog[ukey] = _prog_s[pos]         if _prog_s is not None else None
                        unit_pkg[ukey]  = pkg
                    unit_dcm[ukey][idx] = round(float(vmin_s[pos]), 3)

            # Bucket units for pass summary.
            # Core keeps 4/2/1 buckets; Atom gets cumulative 4/3/2/1 buckets.
            # CCF keeps the single available pass bucket.
            is_atom = module.lower() == 'atom'
            _priority_bins: dict = {4: [], 3: [], 2: [], 1: []} if is_atom else {4: [], 2: [], 1: []}
            for ukey, dcm_vals in unit_dcm.items():
              n = len(dcm_vals)
              if is_atom:
                if n == 4:
                  bucket = 4
                elif n == 3:
                  bucket = 3
                elif n == 2:
                  bucket = 2
                elif n == 1:
                  bucket = 1
                else:
                  continue
              elif n == 4:
                bucket = 4
              elif n >= 2:
                bucket = 2
              elif n == 1:
                bucket = 1
              else:
                continue

              meta     = unit_meta.get(ukey, [None, None, None, None, None])
              _sorted_v = sorted(dcm_vals.values())  # ascending: [best, ..., worst]
              _pfx  = [unit_pkg.get(ukey, ukey), meta[0], meta[1], meta[2], meta[3]]
              _sfx  = [meta[4], unit_mat.get(ukey), unit_prog.get(ukey)]
              # row[5] = Nth-smallest DCM vmin for bucket N:
              #   nKey=4 → sorted[3] = max (ALL 4 DCMs must pass threshold)
              #   nKey=2 → sorted[1] = 2nd-smallest (AT LEAST 2 DCMs must pass)
              #   nKey=1 → sorted[0] = min
              def _nth_v(target_n, sv=_sorted_v):
                  return round(sv[min(target_n - 1, len(sv) - 1)], 3)
              # row format: [pkg, lot, wafer, x, y, nth_vmin, upm_pct, mat, prog6248]
              _priority_bins[bucket].append(_pfx + [_nth_v(bucket)] + _sfx)
              if bucket == 4 and not is_atom:
                _priority_bins[2].append(_pfx + [_nth_v(2)] + _sfx)
              elif is_atom and bucket == 4:
                _priority_bins[3].append(_pfx + [_nth_v(3)] + _sfx)
                _priority_bins[2].append(_pfx + [_nth_v(2)] + _sfx)
              elif is_atom and bucket == 3:
                _priority_bins[2].append(_pfx + [_nth_v(2)] + _sfx)

            freq_groups: dict = {}
            dcm_order = [4, 3, 2, 1] if is_atom else [4, 2, 1]
            for n_dcm in dcm_order:
                g = _priority_bins[n_dcm]
                if not g:
                    continue
                vmins = [r[5] for r in g]  # r[5] = max_vmin (worst-case DCM)
                sv    = sorted(vmins)
                mid   = len(sv) // 2
                med   = sv[mid] if len(sv) % 2 == 1 else (sv[mid - 1] + sv[mid]) / 2
                freq_groups[n_dcm] = {
                    'n':        len(g),
                    'med_vmin': round(med, 3),
                    'rows':     g,
                    'n_total':  len(g),
                }

            if freq_groups:
                freq_data[str(fmhz)] = {
                    'freq_mhz':   fmhz,
                    'freq_label': f'{fmhz / 1000:g}G',
                    'groups':     freq_groups,
                }

        result[module] = {
            'label':       _mod_disp.get(module, module.capitalize()),
            'n_instances': len(set(e[2] for e in entries)),
            'freq_data':   freq_data,
        }

    return result

def generate_html(df: pd.DataFrame,
                  product_config: dict,
                  vmin_meta: dict,
                  output_path: str,
                 spec_lookup: Optional[dict] = None,
                 bm_path: Optional[str] = None) -> str:
    """Generate the CLASS analysis HTML page.
    df             : Normalised CLASS DataFrame (output of class_normalize)
    product_config : Parsed product-config JSON
    vmin_meta      : {module: [(short_key, freq_mhz, idx, raw_col), ...]}
    output_path    : Destination .html file
    spec_lookup    : Optional {param: (lsl, usl, target, unit, name)} dict

    Returns output_path.
    """
    cols = list(df.columns)

    # UPM reference values from input JSON for percent conversion.
    upm_ref_cfg = product_config.get('sort_upm_ref', {}) or {}
    upm_ref_num: Dict[str, float] = {}
    for _rk, _rv in upm_ref_cfg.items():
      try:
        _rf = float(_rv)
        if _rf > 0:
          upm_ref_num[_rk] = _rf
      except Exception:
        continue

    # ── Identify key columns ──────────────────────────────────────────────────
    lot_col   = next((c for c in ['Lot',   'SORT_LOT']   if c in cols), None)
    wafer_col = next((c for c in ['Wafer', 'SORT_WAFER'] if c in cols), None)
    if not lot_col or not wafer_col:
        raise RuntimeError("Lot/Wafer columns not found in DataFrame")

    x_col        = next((c for c in ['SORT_X', 'X'] if c in cols), None)
    y_col        = next((c for c in ['SORT_Y', 'Y'] if c in cols), None)
    sort_lot_col = 'SORT_LOT' if ('SORT_LOT' in cols and lot_col != 'SORT_LOT') else None

    prog6248_col   = _find_col(cols, 'PROGRAM_NAME', '6248')
    progU1U5_col   = _find_col(cols, 'PROGRAM_NAME', 'U1.U5')
    dev_rev_col    = _find_col(cols, 'DEVREVSTEP', 'U1') or _find_col(cols, 'DevRevStep', 'U1')

    # ── Interface bin (bin-1 = pass) ────────────────────────────────────────────
    _ibin_col = _find_col(cols, 'INTERFACE_BIN', '6248')
    ibin1_count = None
    ibin1_col_name = None
    if _ibin_col and _ibin_col in df.columns:
        _ibin_vals = pd.to_numeric(df[_ibin_col], errors='coerce')
        _ibin1_mask = (_ibin_vals == 1)
        _early_pkg_col = 'PKG' if 'PKG' in cols else None
        _early_lot_col = 'SORT_LOT' if 'SORT_LOT' in cols else lot_col
        _early_wfr_col = 'SORT_WAFER' if 'SORT_WAFER' in cols else wafer_col
        if _early_pkg_col and _early_lot_col in df.columns and _early_wfr_col in df.columns:
            # Count unique (PKG, lot, wafer) tuples — matches freq matrix unit logic
            ibin1_count = int(
                df.loc[_ibin1_mask, [_early_pkg_col, _early_lot_col, _early_wfr_col]]
                .drop_duplicates().shape[0]
            )
        elif _early_pkg_col:
            ibin1_count = int(df.loc[_ibin1_mask, _early_pkg_col].dropna().nunique())
        else:
            ibin1_count = int(_ibin1_mask.sum())
        ibin1_col_name = _ibin_col

    # ── Build param groups from product_config ─────────────────────────────────
    # Support both:
    #   (a) pre-built 'groups' list (legacy / dashboard.py _build_product_setup path)
    #   (b) raw JSON with sort_upm / sort_sicc / class_sicc keys (current ProductConfig)
    cfg_groups = product_config.get('groups', [])
    if not cfg_groups:
        _upm_k = list(product_config.get('sort_upm', {}).keys())
        _ss_k  = list(product_config.get('sort_sicc', {}).keys())
        _sc_k  = list(product_config.get('class_sicc', {}).keys())
        if _upm_k:
            cfg_groups.append({'name': 'UPM (Sort)',   'patterns': _upm_k})
        if _ss_k:
            cfg_groups.append({'name': 'SICC Sort',    'patterns': _ss_k})
        if _sc_k:
            cfg_groups.append({'name': 'SICC Class',   'patterns': _sc_k})
    all_params: List[str] = []
    param_group: Dict[str, str] = {}
    groups_ordered: List[str] = []

    # SICC groups from config (skip Vmin* groups – built as freq-aggregated rows below)
    for grp_cfg in cfg_groups:
        gname    = grp_cfg['name']
        if gname.startswith('Vmin '):
            continue
        patterns = grp_cfg.get('patterns', [])
        gparams  = _param_cols(cols, patterns)
        if gparams:
            groups_ordered.append(gname)
            for p in gparams:
                if p not in all_params:
                    all_params.append(p)
                param_group[p] = gname

    # Vmin groups: aggregate per (module, freq), highest freq first
    _mod_disp = {'core': 'Core', 'atom': 'Atom', 'ccf': 'Ring',
                 'CORE': 'Core', 'ATOM': 'Atom', 'CCF': 'Ring'}
    _vmin_group_names = {'core': 'Vmin Core', 'atom': 'Vmin Atom', 'ccf': 'Vmin Ring',
                         'CORE': 'Vmin Core', 'ATOM': 'Vmin Atom', 'CCF': 'Vmin Ring'}
    _vmin_agg_info: Dict[str, dict] = {}  # agg_key → {col_keys, gname, label}
    for module, entries in vmin_meta.items():
        gname = _vmin_group_names.get(module, f'Vmin {module.capitalize()}')
        if gname not in groups_ordered:
            groups_ordered.append(gname)
        _by_freq: Dict[int, list] = {}
        for short_key, fmhz, idx, _ in entries:
            if short_key in cols:
                _by_freq.setdefault(fmhz, []).append(short_key)
        for _fmhz in sorted(_by_freq.keys(), reverse=True):
            _freq_g  = f'{_fmhz / 1000:g}G'
            _agg_key = f'_agg_{module}_{_fmhz}'
            _agg_lbl = f'Vmin {_mod_disp.get(module, module.capitalize())} {_freq_g}'
            all_params.append(_agg_key)
            param_group[_agg_key] = gname
            _vmin_agg_info[_agg_key] = {
                'col_keys': _by_freq[_fmhz],
                'gname':    gname,
                'label':    _agg_lbl,
            }

    # ── PCM_PARAM_META ─────────────────────────────────────────────────────────
    # Build a label lookup from all product-config label sections
    _param_labels: Dict[str, str] = {}
    for _lk in ('sort_upm_labels', 'sort_sicc_labels', 'class_sicc_labels'):
        _param_labels.update(product_config.get(_lk, {}))
    # Aggregate Vmin labels (e.g. "Vmin Core 4.9G")
    for _agg_key, _ainfo in _vmin_agg_info.items():
        _param_labels[_agg_key] = _ainfo['label']

    pcm_param_meta: Dict[str, dict] = {}
    for p in all_params:
      sl = (spec_lookup or {}).get(p)
      is_upm_pct = p in upm_ref_num
      pcm_param_meta[p] = {
        'group':  param_group.get(p, ''),
        'lsl':    _safe(sl[0]) if sl else None,
        'usl':    _safe(sl[1]) if sl else None,
        'target': _safe(sl[2]) if sl else None,
        'unit':   ('%' if is_upm_pct else ((sl[3] if sl else '') or ('V' if p.startswith('_agg_') else ''))),
        'name':   (sl[4] if sl else '') or _param_labels.get(p, ''),
      }

    # ── Apply SICC targets and unit from product config ────────────────────────
    _sicc_tgt_cfg = product_config.get('sicc_targets', {})
    _sicc_unit    = str(product_config.get('sicc_unit', 'A'))
    _sort_tgt     = _sicc_tgt_cfg.get('sort',  {})
    _class_tgt    = _sicc_tgt_cfg.get('class', {})
    for _sp, _sm in pcm_param_meta.items():
        _spk = str(_sp).upper()
        if 'SICC' not in _spk:
            continue
        _is_sort  = 'SORT'  in _spk
        _is_class = 'CLASS' in _spk
        _tgt_dict = _sort_tgt if _is_sort else (_class_tgt if _is_class else {})
        if 'CORE' in _spk:
            _tv = _tgt_dict.get('core')
        elif 'ATOM' in _spk:
            _tv = _tgt_dict.get('atom')
        elif 'RING' in _spk or 'CCF' in _spk:
            _tv = _tgt_dict.get('ccf')
        else:
            _tv = None
        if _tv is not None:
            _sm['target'] = float(_tv)
        _sm['unit'] = _sicc_unit

    # ── Distribution panels (SICC Sort + SICC Class) ──────────────────────────
    dist_panels = []
    for grp_cfg in cfg_groups:
        gname    = grp_cfg['name']
        if 'sicc' in gname.lower() or 'dist' in gname.lower():
            gparams = [p for p in all_params if param_group.get(p) == gname]
            if gparams:
                dist_panels.append({'label': gname, 'params': gparams})

    if not dist_panels:
        # Fallback: first two groups
        for gn in groups_ordered[:2]:
            gparams = [p for p in all_params if param_group.get(p) == gn]
            if gparams:
                dist_panels.append({'label': gn, 'params': gparams})

    # ── XY panels ─────────────────────────────────────────────────────────────
    xy_panels = []

    # ── Static XY plots from product config (UPM vs SICC module tabs) ───────
    _xy_group_labels: Dict[str, str] = {
      'core': 'UPM vs SICC - CORE',
      'atom': 'UPM vs SICC - ATOM',
      'ring': 'UPM vs SICC - RING',
    }
    for _gk, _gcfg in product_config.get('xy_plots', {}).items():
      _panels_list = (_gcfg or {}).get('panels') if isinstance(_gcfg, dict) else _gcfg
      if not _panels_list:
        continue
      # Collect all params (x + ys) used across every sub-panel in this group
      _seen_p: set = set()
      _gparams: List[str] = []
      _panel_defs: List[dict] = []
      for _pc in _panels_list:
        _xk = _pc.get('x')
        _ysk = [y for y in (_pc.get('ys') or []) if y in cols]
        for _pk in [_xk] + _ysk:
          if _pk and _pk in cols and _pk not in _seen_p:
            _seen_p.add(_pk)
            _gparams.append(_pk)
        _panel_defs.append({
          'label': _pc.get('label'),
          'x': _xk if _xk in cols else None,
          'ys': _ysk or None,
          'height': _pc.get('height'),
          'params': [p for p in ([_xk] + (_pc.get('ys') or [])) if p and p in cols],
        })
      if not _gparams:
        continue
      _first_pc = _panel_defs[0]
      xy_panels.append({
        'label':  _xy_group_labels.get(_gk, str((_gcfg or {}).get('label', _gk)).replace('_', ' ').title()),
        'group':  '',
        'params': _gparams,
        'panels': _panel_defs,
        'x':      _first_pc.get('x'),
        'ys':     _first_pc.get('ys'),
      })

    # ── VMIN panels (one per module: Core, Atom, Ring) ──────────────────────────
    _vmin_tab_names = {'core': 'CORE', 'atom': 'ATOM', 'ccf': 'RING'}
    for module in ('core', 'atom', 'ccf'):
        entries = vmin_meta.get(module, [])
        gname   = _vmin_group_names.get(module, f'Vmin {module.capitalize()}')
        gparams = [e[0] for e in entries if e[0] in cols]
        if gparams:
            xy_panels.append({
                'label':  f'VMIN \u2014 {_vmin_tab_names.get(module, module.upper())}',
                'group':  gname,
                'params': gparams,
            })

    # ── Build data structures ─────────────────────────────────────────────────
    # Material column detection (added by add_material_type pipeline step)
    _mat_col = next((c for c in ['Material Type, Skew, BEOL Skew', 'Material Type']
                     if c in cols), None)
    # Fallback: build lot7→material map from collateral CSV files
    _lot7_material: dict = {}
    # Build lot→material from already-merged material column (full sort_lot as key)
    if _mat_col is not None and sort_lot_col and sort_lot_col in df.columns:
        _slc = df[[sort_lot_col, _mat_col]].drop_duplicates()
        for _, _rw in _slc.iterrows():
            _k = str(_rw[sort_lot_col]).strip()   # full lot string — no truncation
            _v = str(_rw[_mat_col]).strip()
            if _k and _v and _v not in ('nan', 'None', ''):
                _lot7_material.setdefault(_k, _v)
    if _mat_col is None:
        try:
            from _constants import _MATERIAL_DIR as _MD
            import csv as _csv
            import os as _os
            for _mf in sorted(_os.listdir(_MD)):
                if not _mf.lower().endswith('.csv'):
                    continue
                with open(_os.path.join(_MD, _mf), encoding='utf-8-sig') as _fh:
                    _mr = list(_csv.reader(_fh))
                if not _mr:
                    continue
                _hdr = [h.strip() for h in _mr[0]]
                try:
                    _li = _hdr.index('INTEL_LOT7')
                    _mi = _hdr.index('Material Type')
                except ValueError:
                    continue
                for _row in _mr[1:]:
                    if len(_row) > max(_li, _mi):
                        _k = _row[_li].strip()[:7]
                        _v = _row[_mi].strip()
                        if _k and _v and _k not in _lot7_material:
                            _lot7_material[_k] = _v
        except Exception:
            pass

    _upm_107_col = None
    wfr_data  = _build_wfr_data(df, lot_col, wafer_col,
                                  prog6248_col, progU1U5_col, x_col, y_col,
                                  sort_lot_col, material_col=_mat_col,
                                  dev_rev_col=dev_rev_col)
    _pcm_pkg_col = 'PKG' if 'PKG' in cols else None
    pcm_rows  = _build_pcm_rows(
      df, lot_col, wafer_col, all_params, param_group,
      upm_refs=upm_ref_num, prog6248_col=prog6248_col, pkg_col=_pcm_pkg_col
    )

    # ── Aggregate Vmin PCM rows (combine all instances per module+freq) ────────
    def _norm_wfr2(w):
        s = str(w)
        return str(int(float(s))) if s.replace('.', '', 1).isdigit() else s

    _vmin_grp_keys = [lot_col, wafer_col]
    for _grp_vals2, _g2 in df.groupby(_vmin_grp_keys, sort=False):
        _lot2, _wafer2 = _grp_vals2
        _lot2_s = str(_lot2)
        _wfr2_s = _norm_wfr2(_wafer2)
        for _agg_key, _ainfo in _vmin_agg_info.items():
            _agg_vals: list = []
            for _k in _ainfo['col_keys']:
                if _k in _g2.columns:
                    _cs = _g2[_k]
                    if isinstance(_cs, pd.DataFrame):
                        _cs = _cs.iloc[:, 0]
                    _cs2 = pd.to_numeric(_cs, errors='coerce')
                    _cs2 = _cs2[_cs2.notna() & (_cs2 > 0)]
                    _agg_vals.extend(_cs2.values.tolist())
            if not _agg_vals:
                continue
            _an   = len(_agg_vals)
            _amed = float(pd.Series(_agg_vals).median())
            _astd = float(pd.Series(_agg_vals).std()) if _an > 1 else 0.0
            _acv  = _safe(_astd / abs(_amed) * 100) if _amed != 0 else None
            _advs = [round(float(v), 5) for v in _agg_vals[:_MAX_DIE_VALUES]]
            pcm_rows.append({
                'lot':        _lot2_s,
                'wafer':      _wfr2_s,
                'prog6248':   '',
                'group':      _ainfo['gname'],
                'param':      _agg_key,
                'n':          _an,
                'median':     _safe(_amed),
                'std':        _safe(_astd),
                'cv':         _acv,
                'min_val':    _safe(float(min(_agg_vals))),
                'max_val':    _safe(float(max(_agg_vals))),
                'die_values': _advs,
            })

    # ── SICC Class Temperature medians (per token) ────────────────────────────
    _temp_col_map = {
        'CORE0': 'CLASS SICC TEMP CORE0', 'CORE1': 'CLASS SICC TEMP CORE1',
        'CORE2': 'CLASS SICC TEMP CORE2', 'CORE3': 'CLASS SICC TEMP CORE3',
        'ATOM0': 'CLASS SICC TEMP ATOM0', 'ATOM1': 'CLASS SICC TEMP ATOM1',
        'ATOM2': 'CLASS SICC TEMP ATOM2', 'ATOM3': 'CLASS SICC TEMP ATOM3',
        'RING':  'CLASS SICC TEMP RING',
    }
    sicc_class_temp: Dict[str, float] = {}
    for tok, col_key in _temp_col_map.items():
        if col_key in cols:
            _tv = pd.to_numeric(df[col_key], errors='coerce').dropna()
            if len(_tv):
                sicc_class_temp[tok] = round(float(_tv.median()), 3)

    # ── VF Chart reference overlay data ──────────────────────────────────────
    _vf_chart_data: dict = {}
    try:
        from _constants import _DEFAULT_SETUP_DIR as _SETUP_DIR
        _vf_json = Path(_SETUP_DIR) / 'VF_Chart' / 'NVL_N2P_CLASS_VF_tracker_plot_A_to_L_grouped.json'
        if _vf_json.is_file():
            _vf_chart_data = json.loads(_vf_json.read_text(encoding='utf-8'))
    except Exception:
        pass

    # Speed Flow tab data
    pkg_col      = 'PKG' if 'PKG' in cols else None
    # Collect IBIN=1 package IDs for interactive JS filter
    ibin1_pkgs   = []
    if ibin1_col_name and ibin1_col_name in df.columns and pkg_col and pkg_col in df.columns:
        _ibin_v2   = pd.to_numeric(df[ibin1_col_name], errors='coerce')
        # Deduplicate: a die retested under A/B/C all with IBIN=1 → listed once
        ibin1_pkgs = list(dict.fromkeys(df.loc[_ibin_v2 == 1, pkg_col].astype(str).dropna()))
    upm_keys     = list(product_config.get('sort_upm', {}).keys())
    _upm_107_col = next(
        (k for k in upm_keys if '950' in k and '107' in k),
        upm_keys[0] if upm_keys else None,
    )
    # Patch upm_med into wfr_data now that upm_keys is known
    if _upm_107_col and _upm_107_col in df.columns:
        _upm_ref_val = upm_ref_num.get(_upm_107_col)
        _grp_k2 = [lot_col, wafer_col]
        _upm_med_map: dict = {}
        for _gv2, _gg2 in df.groupby(_grp_k2, sort=False):
            _lot2 = str(_gv2[0])
            _wfr2 = str(int(float(_gv2[1]))) if str(_gv2[1]).replace('.','',1).isdigit() else str(_gv2[1])
            _us2 = pd.to_numeric(_gg2[_upm_107_col], errors='coerce').dropna()
            if len(_us2):
                _raw = float(_us2.median())
                if _upm_ref_val and _upm_ref_val > 0:
                    _upm_med_map[_lot2+'/'+_wfr2] = round(_raw / _upm_ref_val * 100.0, 3)
                else:
                    _upm_med_map[_lot2+'/'+_wfr2] = round(_raw, 4)
        _upm_is_pct = bool(_upm_ref_val and _upm_ref_val > 0)
        for _wr in wfr_data:
            _wr['upm_med'] = _upm_med_map.get(_wr['lot']+'/'+_wr['wafer'])
            _wr['upm_is_pct'] = _upm_is_pct
    upm_labels   = product_config.get('sort_upm_labels', {})
    upm_950_ref  = upm_ref_num.get('UPM 107_950')
    _csv_lot_col = 'SORT_LOT'   if 'SORT_LOT'   in cols else lot_col
    _csv_wfr_col = 'SORT_WAFER' if 'SORT_WAFER' in cols else wafer_col
    # composite set of "prog|pkg" — one entry per (program × unit) so a die
    # retested under multiple programs contributes to EACH program's denominator.
    # ibin1_wfr_pkgs: "prog|lot/wafer" -> [pkg, ...] for per-row denominator
    ibin1_pkg_key_set: set = set()
    ibin1_wfr_pkgs: dict = {}
    if (ibin1_col_name and ibin1_col_name in df.columns
            and pkg_col and pkg_col in df.columns
            and _csv_lot_col in df.columns and _csv_wfr_col in df.columns):
        _ibin_m    = pd.to_numeric(df[ibin1_col_name], errors='coerce') == 1
        _i1_pkgs   = df.loc[_ibin_m, pkg_col].astype(str)
        _i1_progs  = df.loc[_ibin_m, prog6248_col].astype(str) if prog6248_col and prog6248_col in df.columns else None
        _i1_lots   = df.loc[_ibin_m, _csv_lot_col].astype(str)
        _i1_wfrs   = df.loc[_ibin_m, _csv_wfr_col].astype(str)
        _wfr_sets: dict = {}
        for _i, _pkg in enumerate(_i1_pkgs):
            _prog_v = _i1_progs.iloc[_i] if _i1_progs is not None else ''
            _lot_v  = str(_i1_lots.iloc[_i])
            _wfr_raw = str(_i1_wfrs.iloc[_i])
            _wfr_v  = str(int(float(_wfr_raw))) if _wfr_raw.replace('.', '', 1).isdigit() else _wfr_raw
            ibin1_pkg_key_set.add(str(_prog_v) + '|' + _lot_v + '|' + _pkg)
            _wk = str(_prog_v) + '|' + _lot_v + '/' + _wfr_v
            _wfr_sets.setdefault(_wk, set()).add(_pkg)
        ibin1_wfr_pkgs = {k: sorted(v) for k, v in _wfr_sets.items()}
    flow_data    = _build_vmin_flow_data(
        df, vmin_meta, pkg_col, upm_keys, upm_950_ref=upm_950_ref,
        sort_lot_col=_csv_lot_col, sort_wafer_col=_csv_wfr_col,
        x_col=x_col, y_col=y_col, mat_col=_mat_col, prog6248_col=prog6248_col,
    )
    pass_table_data = _build_vmin_pass_table(
        df, vmin_meta, pkg_col, upm_keys=upm_keys, upm_950_ref=upm_950_ref,
        sort_lot_col=_csv_lot_col, sort_wafer_col=_csv_wfr_col,
        x_col=x_col, y_col=y_col, mat_col=_mat_col, prog6248_col=prog6248_col,
    )

    # ── Bin Matrix Data ────────────────────────────────────────────────────────
    import fnmatch
    try:
        import os
        from _constants import _DEFAULT_SETUP_DIR as _BM_SETUP_DIR
        _bm_dir = Path(__file__).parent.parent.parent / "BinSplitAnalysis"

        if bm_path and os.path.isfile(bm_path):
            _qdf_file = Path(bm_path)
        else:
            _qdf_file = Path(_BM_SETUP_DIR) / "NVL_BLLC_PO_BM.csv"

        # Load bin_matrix config — try BinSplitAnalysis/config/config.json first,
        # then fall back to ProductConfig in shared/setup/class-dashboard directly
        _qdf_cfg_file = _bm_dir / "config" / "config.json"
        if _qdf_cfg_file.exists():
            _qdf_cfg = json.loads(_qdf_cfg_file.read_text(encoding="utf-8"))
            _prod_cfg_rel = _qdf_cfg.get("productConfigFile", "")
            if _prod_cfg_rel:
                _prod_path = (_bm_dir / _prod_cfg_rel).resolve()
            else:
                _prod_path = None
        else:
            _qdf_cfg = {}
            # No BinSplitAnalysis present — resolve ProductConfig from setup dir
            import glob as _glob
            _pc_candidates = _glob.glob(os.path.join(_BM_SETUP_DIR, "*ProductConfig*.json"))
            _prod_path = Path(_pc_candidates[0]) if _pc_candidates else None

        if _prod_path and _prod_path.exists():
            _prod = json.loads(_prod_path.read_text(encoding="utf-8"))
            _bm = _prod.get("bin_matrix", {})
            _bm_dlcp = _bm.get("DLCP", {})
            _bm_prog = _bm.get("ProgramName", {})
            _upm_keys = list(_prod.get("sort_upm", {}).keys())
            _upm_key  = _upm_keys[0] if _upm_keys else ""
            _qdf_cfg.update({
                # df is already normalized — use the short key directly for speed column lookup
                "speedTokenPattern": _upm_key,
                "speedTarget":       _prod.get("sort_upm_ref", {}).get(_upm_key, 1.0),
                "passingQdfPattern": _bm.get("passingQdfPattern", ""),
                "wwPattern":         _bm.get("wwPattern", ""),
                "devRevStepPattern": _bm_dlcp.get("devRevStepPattern", ""),
                "dlcpExtractStart":  _bm_dlcp.get("dlcpExtractStart", 4),
                "dlcpExtractLength": _bm_dlcp.get("dlcpExtractLength", 2),
                "dlcpMap":           _bm_dlcp.get("dlcpMap", {}),
                "programNamePattern":_bm_prog.get("programNamePattern", ""),
                "tpRevStart":        _bm_prog.get("tpRevStart", 7),
                "tpRevLength":       _bm_prog.get("tpRevLength", 8),
            })
        
        qdf_rows = []
        if _qdf_file.suffix.lower() == '.csv':
            import csv
            with open(str(_qdf_file), 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                try:
                    _qdf_headers_xl = [str(x) for x in next(reader)]
                    for row in reader:
                        if any(v for v in row if str(v).strip()):
                            qdf_rows.append({_qdf_headers_xl[i]: (str(v) if str(v).strip() else "") for i, v in enumerate(row)})
                except StopIteration:
                    pass
        elif _qdf_file.suffix.lower() in ('.xls', '.xlsx'):
            import openpyxl
            _wb_in = openpyxl.load_workbook(str(_qdf_file))
            _ws_in = _wb_in.active
            _qdf_headers_xl = [str(c.value) if c.value is not None else "" for c in _ws_in[1]]
            for row in _ws_in.iter_rows(min_row=2, values_only=True):
                if any(v is not None for v in row):
                    qdf_rows.append({_qdf_headers_xl[i]: (str(v) if v is not None else "") for i, v in enumerate(row)})
        else:
            print(f"Warning: Unsupported Bin Matrix file extension: {_qdf_file.suffix}")

        _speed_col_pat = _qdf_cfg.get("speedTokenPattern", "VA-NA-UNIT-SPEED_PCT_*_CLASSHOT")
        _pas_qdf_pat   = _qdf_cfg.get("passingQdfPattern", "VA-NA-UNIT-PASSING_QDFS_*_CLASSHOT")
        _drev_pat      = _qdf_cfg.get("devRevStepPattern", "DEV_REV_STEP")

        _speed_col = next((c for c in cols if fnmatch.fnmatch(c.upper(), _speed_col_pat.upper())), None)
        _pas_qdf_col = next((c for c in cols if fnmatch.fnmatch(c.upper(), _pas_qdf_pat.upper())), None)
        _drev_col = next((c for c in cols if fnmatch.fnmatch(c.upper(), _drev_pat.upper())), None)
        _ww_pat = _qdf_cfg.get('wwPattern', '*WORKWEEK*')
        _ww_col = next((c for c in cols if fnmatch.fnmatch(c.upper(), _ww_pat.upper())), None)

        tp_start = _qdf_cfg.get("tpRevStart", 7)
        tp_len   = _qdf_cfg.get("tpRevLength", 8)
        dlcp_start = _qdf_cfg.get("dlcpExtractStart", 4)
        dlcp_len   = _qdf_cfg.get("dlcpExtractLength", 2)
        dlcp_map   = _qdf_cfg.get("dlcpMap", {})
        speed_tgt  = _qdf_cfg.get("speedTarget", 1.0)
        
        _local_ibin1_set = set(ibin1_pkgs) if ibin1_pkgs else None
        # Column name for direct per-row IBIN fallback when pkg_col is absent
        _ibin1_col_direct = ibin1_col_name if ibin1_col_name and ibin1_col_name in df.columns else None

        bin_matrix_rows = []
        for i, r in df.iterrows():
            # Always filter on IBIN=1 directly when the column is available — the PKG-set
            # approach is unreliable because the same PKG can appear in both pass and fail
            # rows (retested units), which would let non-IBIN=1 rows through.
            if _ibin1_col_direct:
                try:
                    if int(float(r[_ibin1_col_direct])) != 1: continue
                except (ValueError, TypeError):
                    continue
            elif _local_ibin1_set:
                if str(r.get(pkg_col, "")) not in _local_ibin1_set: continue
            spd_raw = r.get(_speed_col) if _speed_col else None
            try: spd_pct = (float(spd_raw) / speed_tgt * 100) if pd.notna(spd_raw) else None
            except: spd_pct = None
            
            prog = str(r.get(prog6248_col, "")) if prog6248_col else ""
            dev_rev = str(r.get(_drev_col, "")) if _drev_col else ""
            
            tp_rev = prog[tp_start:tp_start+tp_len] if prog else ""
            dlcp_key = dev_rev[dlcp_start:dlcp_start+dlcp_len] if dev_rev else ""
            
            bin_matrix_rows.append({
                "lot": str(r.get(lot_col, "")),
                "wafer": str(int(float(r.get(wafer_col, 0)))) if str(r.get(wafer_col, "0")).replace('.','',1).isdigit() else str(r.get(wafer_col, "")),
                "speed_pct": round(spd_pct, 2) if spd_pct is not None else None,
                "mat": str(r.get(_mat_col, "Not found")).strip() if _mat_col else "Not found",
                "tp_rev": tp_rev,
                "prog": prog,
                "dlcp": dlcp_map.get(dlcp_key, ""),
                "dlcp_key": dlcp_key,
                "dev_rev": dev_rev,
                "pas_qdf": str(r.get(_pas_qdf_col, "")) if _pas_qdf_col else "",
                "ww": str(r.get(_ww_col, "")) if _ww_col else ""
            })
    except Exception as e:
        bin_matrix_rows = []
        qdf_rows = []
        print(f"Warning: Failed to load Bin Matrix data: {e}")

    meta       = product_config
    title      = meta.get('title', 'CLASS Analysis')
    subtitle   = meta.get('subtitle', '')
    if prog6248_col and prog6248_col in df.columns:
        _pn = sorted(df[prog6248_col].dropna().unique().tolist())
        if _pn:
            from html import escape as _hesc
            _pstr = ', '.join(
                '<button class="tp-link" onclick="_showTpPopup(\'' + _hesc(str(p), quote=True) + '\')">' + _hesc(str(p)) + '</button>'
                for p in _pn
            )
            subtitle = _pstr if not subtitle else subtitle + '  –  ' + _pstr
    if progU1U5_col and progU1U5_col in df.columns:
        _pn2 = sorted(df[progU1U5_col].dropna().unique().tolist())
        if _pn2:
            from html import escape as _hesc
            _pstr2 = ', '.join(
                '<button class="tp-link" onclick="_showTpPopup(\'' + _hesc(str(p), quote=True) + '\')">' + _hesc(str(p)) + '</button>'
                for p in _pn2
            )
            subtitle = _pstr2 if not subtitle else subtitle + '  | U1.U5: ' + _pstr2
    date_str   = datetime.now().strftime('%Y-%m-%d %H:%M')
    n_lots     = len({r['lot'] for r in wfr_data})
    n_wfrs     = len(wfr_data)

    # ── Load test-program details from .msg build-report files ───────────────
    tp_info = _load_tp_info()

    _data_js_stem = Path(output_path).stem + '.data.js'
    html, data_js = _render_html(
        wfr_data         = wfr_data,
        pcm_rows         = pcm_rows,
        pcm_groups       = groups_ordered,
        pcm_param_meta   = pcm_param_meta,
        dist_panels      = dist_panels,
        xy_panels        = xy_panels,
        title            = title,
        subtitle         = subtitle,
        date_str         = date_str,
        n_lots           = n_lots,
        n_wfrs           = n_wfrs,
        prog6248_col     = prog6248_col,
        progU1U5_col     = progU1U5_col,
        x_col            = x_col,
        y_col            = y_col,
        sort_lot_col     = sort_lot_col,
        dev_rev_col      = dev_rev_col,
        flow_data        = flow_data,
        upm_labels       = upm_labels,
        upm_refs         = upm_ref_cfg,
        pass_table_data  = pass_table_data,
        lot7_material    = _lot7_material,
        sicc_class_temp  = sicc_class_temp,
        vf_chart_data    = _vf_chart_data,
        ibin1_count      = ibin1_count,
        ibin1_col_name   = ibin1_col_name,
        ibin1_pkgs       = ibin1_pkgs,
        ibin1_pkg_key_set = ibin1_pkg_key_set,
        ibin1_wfr_pkgs   = ibin1_wfr_pkgs,
        tp_info          = tp_info,
        bin_matrix_rows  = bin_matrix_rows,
        qdf_rows         = qdf_rows,
        data_js_filename = _data_js_stem,
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_wm_inject(html), encoding='utf-8')
    out.with_name(_data_js_stem).write_text(data_js, encoding='utf-8')

    # ── Write slim summary JSON for automation email builder ──────────────────
    try:
        _ibin1_set = set(ibin1_pkgs) if ibin1_pkgs else None
        _slim_pt: dict = {}
        for _mod, _mdata in (pass_table_data or {}).items():
            _slim_fd: dict = {}
            for _fk, _fd in (_mdata.get('freq_data') or {}).items():
                _slim_groups: dict = {}
                for _gk, _gd in (_fd.get('groups') or {}).items():
                    # Count only bin-1 dies so email % is relative to the same population
                    if _ibin1_set:
                        _n_b1 = sum(1 for _row in (_gd.get('rows') or []) if _row[0] in _ibin1_set)
                    else:
                        _n_b1 = _gd.get('n', 0)
                    _slim_groups[str(_gk)] = {
                        'n':        _n_b1,
                        'med_vmin': _gd.get('med_vmin'),
                    }
                _slim_fd[_fk] = {
                    'freq_mhz':   _fd['freq_mhz'],
                    'freq_label': _fd['freq_label'],
                    'groups':     _slim_groups,
                }
            _slim_pt[_mod] = {'label': _mdata.get('label', _mod), 'freq_data': _slim_fd}
        _summary_data = {
            'total_dies': len(df),
            'bin1_dies':  int(ibin1_count) if ibin1_count is not None else 0,
            'pass_table': _slim_pt,
        }
        out.with_name(out.stem + '.summary.json').write_text(
            json.dumps(_summary_data, separators=(',', ':')), encoding='utf-8'
        )
    except Exception as _e:
        pass  # summary JSON is optional; never break HTML generation

    return str(out)


# ── HTML renderer ─────────────────────────────────────────────────────────────

def _render_html(wfr_data, pcm_rows, pcm_groups, pcm_param_meta,
                 dist_panels, xy_panels, title, subtitle,
                 date_str, n_lots, n_wfrs,
                 prog6248_col, progU1U5_col, x_col, y_col,
                 sort_lot_col=None, dev_rev_col=None, flow_data=None, upm_labels=None,
                 upm_refs=None, pass_table_data=None, lot7_material=None,
                 sicc_class_temp=None, vf_chart_data=None,
                 ibin1_count=None, ibin1_col_name=None, ibin1_pkgs=None, ibin1_pkg_key_set=None,
                 ibin1_wfr_pkgs=None,
                 tp_info=None,
                 bin_matrix_rows=None, qdf_rows=None,
                 data_js_filename='data.js'):

    # ── Serialise data ────────────────────────────────────────────────────────
    WFR_DATA_JS        = json.dumps(wfr_data,              separators=(',', ':'))
    PCM_ROWS_JS        = json.dumps(pcm_rows,              separators=(',', ':'))
    PCM_GROUPS_JS      = json.dumps(pcm_groups,            separators=(',', ':'))
    PCM_PARAM_META_JS  = json.dumps(pcm_param_meta,        separators=(',', ':'))
    PCM_DIST_JS        = json.dumps(dist_panels,           separators=(',', ':'))
    PCM_XY_JS          = json.dumps(xy_panels,             separators=(',', ':'))
    FLOW_DATA_JS       = json.dumps(flow_data or {},       separators=(',', ':'))
    UPM_LABELS_JS      = json.dumps(upm_labels or {},      separators=(',', ':'))
    UPM_REFS_JS        = json.dumps(upm_refs or {},        separators=(',', ':'))
    PASS_TABLE_JS      = json.dumps(pass_table_data or {}, separators=(',', ':'))
    LOT7_MAT_JS        = json.dumps(lot7_material or {},   separators=(',', ':'))
    SICC_CLASS_TEMP_JS = json.dumps(sicc_class_temp or {}, separators=(',', ':'))
    VF_CHART_JS        = json.dumps(vf_chart_data  or {}, separators=(',', ':'))
    IBIN1_COUNT_JS     = json.dumps(ibin1_count)
    IBIN1_COL_JS       = json.dumps(ibin1_col_name or '')
    IBIN1_PKGS_JS         = json.dumps(ibin1_pkgs or [])
    IBIN1_PKG_KEY_SET_JS  = json.dumps(sorted(ibin1_pkg_key_set or []))
    IBIN1_WFR_PKGS_JS     = json.dumps(ibin1_wfr_pkgs or {},   separators=(',', ':'))
    TP_INFO_JS         = json.dumps(tp_info or {},         separators=(',', ':'))
    BM_ROWS_JS         = json.dumps(bin_matrix_rows or [], separators=(',', ':'))
    QDF_ROWS_JS        = json.dumps(qdf_rows or [],        separators=(',', ':'))

    DATA_JS_FILENAME   = data_js_filename
    # ── Build sidecar data JS ─────────────────────────────────────────────
    data_js = (
        "/* ── Sidecar data ──────────────────────────────────────────────── */\n"
        f"var WFR_DATA       = {WFR_DATA_JS};\n"
        f"var PCM_ROWS       = {PCM_ROWS_JS};\n"
        f"var PCM_GROUPS     = {PCM_GROUPS_JS};\n"
        f"var PCM_PARAM_META = {PCM_PARAM_META_JS};\n"
        f"var PCM_DIST_PANELS = {PCM_DIST_JS};\n"
        f"var PCM_XY_PANELS  = {PCM_XY_JS};\n"
        f"var FLOW_DATA      = {FLOW_DATA_JS};\n"
        f"var UPM_LABELS     = {UPM_LABELS_JS};\n"
        f"var UPM_REFS       = {UPM_REFS_JS};\n"
        f"var PASS_TABLE     = {PASS_TABLE_JS};\n"
        f"var LOT7_MAT       = {LOT7_MAT_JS};\n"
        f"var SICC_CLASS_TEMP = {SICC_CLASS_TEMP_JS};\n"
        f"var VF_CHART_DATA  = {VF_CHART_JS};\n"
        f"var IBIN1_COUNT    = {IBIN1_COUNT_JS};\n"
        f"var IBIN1_COL      = {IBIN1_COL_JS};\n"
        f"var IBIN1_PKGS        = {IBIN1_PKGS_JS};\n"
        f"var IBIN1_PKG_KEY_SET = new Set({IBIN1_PKG_KEY_SET_JS});\n"
        f"var IBIN1_WFR_PKGS    = {IBIN1_WFR_PKGS_JS};\n"
        f"var TP_INFO        = {TP_INFO_JS};\n"
        f"var BM_ROWS        = {BM_ROWS_JS};\n"
        f"var QDF_ROWS       = {QDF_ROWS_JS};\n"
        "function _lotMat(lot){var s=(lot||'')+'';return LOT7_MAT[s]||LOT7_MAT[s.slice(0,7)]||'Others';}\n"
    )

    # Tab labels
    xy_tab_labels = ''.join(
        f'<button class="tab-btn" onclick="showTab(this,\'tab-xy{i}\')">'
        f'\u00d7 {p["label"]}</button>'
        for i, p in enumerate(xy_panels)
    )
    xy_tab_divs_list = []
    for xi, xp in enumerate(xy_panels):
      _sub_panels = xp.get('panels') or [{'label': xp['label'], 'x': xp.get('x'), 'ys': xp.get('ys')}]
      if not _sub_panels:
        _sub_panels = [{'label': xp['label'], 'x': xp.get('x'), 'ys': xp.get('ys')}]
      _side_html = []
      for si, sp in enumerate(_sub_panels):
        _side_html.append(
          f'<div id="xyp{xi}_{si}-wrap" style="flex:1 1 calc(50% - 8px);min-width:360px;display:flex;flex-direction:column;'
          f'border-right:{"2px solid #dde" if si < len(_sub_panels)-1 else "none"}"></div>'
        )
      _panel_inner = (
        f'<div style="flex-shrink:0;border-bottom:3px solid #bcd">'
        f'<div style="background:#1a6e2b;border-bottom:1px solid #bcd;padding:4px 10px;'
        f'display:flex;align-items:center;gap:8px;cursor:pointer" onclick="toggleXYP({xi})">'
        f'<button id="xyp{xi}-toggle" style="border:none;background:none;cursor:pointer;'
        f'font-size:14px;color:#fff;padding:0 4px;line-height:1">&#9660;</button>'
        f'<span style="font-size:14px;font-weight:bold;color:#fff">&#9673; {xp["label"]}</span>'
        f'</div>'
        f'<div id="xyp{xi}-body" style="display:flex;flex-wrap:wrap;gap:8px;flex-direction:row;min-height:0">'
        + ''.join(_side_html) +
        f'</div></div>'
      )
      xy_tab_divs_list.append(f'<div id="tab-xy{xi}" class="tab-panel">{_panel_inner}</div>')
    xy_tab_divs = ''.join(xy_tab_divs_list)

    # Group card HTML (for variability tab — placeholders filled by JS)
    grp_cards_html = ''.join(
        f'''<div class="grp-card" id="card-grp-{_esc(g)}">
  <div class="grp-card-hdr"
       onclick="var c=this.parentElement;
                c.classList.toggle('gc-collapsed');
                this.querySelector('.gc-tog').textContent=
                c.classList.contains('gc-collapsed')?'+':'-'">
    <span class="gc-tog" style="font-size:22px;line-height:1;width:22px;
          display:inline-block;text-align:center">-</span>
    {g}
    <span style="font-weight:normal;font-size:10px;opacity:.7" id="card-grp-{_esc(g)}-cnt"></span>
    <button onclick="event.stopPropagation();downloadGrpCSV('{g.replace(chr(39), chr(92)+chr(39))}')"
            style="margin-left:auto;padding:2px 8px;font-size:10px;font-weight:bold;
                   border:none;border-radius:3px;background:#27ae60;color:#fff;cursor:pointer"
            onmouseover="this.style.background='#1e8449'"
            onmouseout="this.style.background='#27ae60'">&#11015; CSV</button>
  </div>
  <div class="grp-card-body">
    <svg id="svg-grp-{_esc(g)}" style="display:block;width:100%"></svg>
    <div class="grp-legend" style="padding:2px 8px 6px"></div>
  </div>
</div>'''
        for g in pcm_groups
    )

    # Distribution panel HTML
    dist_html = ''
    for di, dp in enumerate(dist_panels):
        dist_html += f'''<div style="flex-shrink:0;border-bottom:3px solid #bcd">
  <div style="background:#1a6e2b;border-bottom:1px solid #bcd;padding:4px 10px;
              display:flex;align-items:center;gap:8px;cursor:pointer"
       onclick="toggleDistP({di})">
    <button id="distp{di}-toggle" style="border:none;background:none;cursor:pointer;
            font-size:14px;color:#fff;padding:0 4px;line-height:1">&#9660;</button>
    <span style="font-size:14px;font-weight:bold;color:#fff">
      &#9673; Panel {di+1} &mdash; {dp['label']}</span>
  </div>
  <div id="distp{di}-body"></div>
</div>
'''

    col_hdr_sort_lot = f'<th style="{_TH_ST}">Sort_Lot</th>' if sort_lot_col else ''
    col_hdr_prog6248 = f'<th style="{_TH_ST}">Class Prog 6248</th>' if prog6248_col else ''
    col_hdr_u1u5     = f'<th style="{_TH_ST}">Sort Prog U1.U5</th>' if progU1U5_col else ''
    col_hdr_dev_rev  = f'<th style="{_TH_ST}">Layout</th>' if dev_rev_col else ''
    col_hdr_x        = f'<th style="{_TH_ST}">Sort_X</th>' if x_col else ''
    col_hdr_y        = f'<th style="{_TH_ST}">Sort_Y</th>' if y_col else ''

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%;overflow:hidden;font-family:Arial,sans-serif;
           background:#f0f2f5;color:#2c3e50;font-size:13px}}
/* ── Page layout ── */
#app{{display:flex;flex-direction:column;height:100vh}}
.page-hdr{{background:#1f3a50;color:#fff;padding:7px 14px;flex-shrink:0}}
.page-hdr h1{{font-size:14px;font-weight:bold}}
.page-hdr .sub{{font-size:11px;color:#aed6f1;margin-top:2px}}
.sub .tp-link{{color:#aed6f1;background:none;border:none;padding:0;font:inherit;font-size:11px;white-space:nowrap;cursor:default}}
.sub .tp-link.tp-has-info{{color:#ffd700;text-decoration:underline dotted;cursor:pointer}}
.sub .tp-link.tp-has-info:hover{{color:#fff;text-decoration:underline}}
.sub .tp-link.tp-no-info{{pointer-events:none}}
.info-bar{{display:flex;flex-wrap:wrap;gap:8px;padding:6px 14px;
           background:#2c3e50;color:#ecf0f1;font-size:11px;
           border-bottom:2px solid #1a252f;flex-shrink:0}}
.info-bar b{{color:#f1c40f}}
.info-bar .sep{{color:#4a6a8a;margin:0 2px}}
/* ── Tab bar ── */
.tabs{{display:flex;align-items:center;background:#1a252f;
       padding:5px 12px;gap:5px;border-bottom:3px solid #27ae60;flex-shrink:0;
       flex-wrap:wrap}}
.tab-btn{{padding:6px 18px;border:2px solid transparent;border-radius:5px;
          background:rgba(255,255,255,.07);color:#95a5a6;cursor:pointer;
          font-size:12px;font-weight:bold;
          transition:background .12s,color .12s,border-color .12s}}
.tab-btn:hover{{background:rgba(39,174,96,.20);color:#a9dfbf;
                border-color:rgba(39,174,96,.40)}}
.tab-btn.active{{background:#27ae60;color:#fff;border-color:#1e8449;
                 box-shadow:0 2px 6px rgba(39,174,96,.35)}}
/* ── Three-panel ── */
.three-panel{{display:flex;flex-direction:row;flex:1;min-height:0;
              overflow:hidden;gap:0}}
#panel1{{width:280px;min-width:120px;flex-shrink:0;background:#fff;
         display:flex;flex-direction:column;
         border-right:2px solid #d0d7de;overflow:hidden;position:relative}}
.p1-resize{{width:5px;flex-shrink:0;background:#d0d7de;cursor:col-resize;
            align-self:stretch;transition:background .15s;user-select:none}}
.p1-resize:hover,.p1-resize.dragging{{background:#2980b9}}
.sp12{{width:22px;flex-shrink:0;background:#ecf0f1;cursor:col-resize;
       display:flex;align-items:center;justify-content:center;
       border-left:1px solid #d0d7de;border-right:1px solid #d0d7de;
       user-select:none;position:relative;z-index:2}}
.sp12:hover{{background:#d6eaff}}
.sp12-btn{{background:none;border:none;font-size:13px;cursor:pointer;
           color:#2c3e50;line-height:1;padding:0;display:block}}
#panel2{{width:380px;min-width:160px;flex-shrink:0;background:#fff;
         display:flex;flex-direction:column;
         overflow:hidden;border-right:2px solid #d0d7de;transition:width .15s}}
#panel2.p2-hidden{{width:0!important;min-width:0!important;
                   overflow:hidden;border:none}}
.p2-hdr{{background:#34495e;color:#fff;padding:5px 10px;
         font-size:11px;font-weight:bold;flex-shrink:0;
         display:flex;align-items:center;gap:6px}}
.p2-body{{flex:1;overflow:auto}}
.sp23{{width:5px;flex-shrink:0;background:#d0d7de;cursor:col-resize;
       align-self:stretch;transition:background .15s;user-select:none}}
.sp23:hover,.sp23.dragging{{background:#2980b9}}
#panel3{{flex:1;min-width:0;overflow-y:auto;overflow-x:hidden;
         background:#f0f2f5;padding:6px}}
/* ── Tab panels ── */
.tab-panel{{display:none}}
.tab-panel.active{{display:block}}
#tab-flow.active{{display:flex;flex-direction:column}}
.xyp-body{{display:flex;flex-wrap:nowrap;gap:0;min-height:0;overflow-x:auto;overflow-y:hidden}}
.xyp-wrap{{flex:0 0 auto;min-width:360px;display:flex;flex-direction:column}}
.xyp-split{{width:6px;flex-shrink:0;background:#d0d7de;cursor:col-resize;user-select:none}}
.xyp-split:hover,.xyp-split.dragging{{background:#2980b9}}
.flow-split{{width:6px;flex-shrink:0;background:#d0d7de;cursor:col-resize;user-select:none}}
.flow-split:hover,.flow-split.dragging{{background:#2980b9}}
.flow-chart-card{{background:#fff;border:1px solid #d8e2ef;border-radius:6px;cursor:pointer;min-width:150px}}
.flow-chart-card:hover{{background:#eef6ff;border-color:#9cbce3}}
.flow-chart-card.pass{{min-width:170px}}
/* ── Panel 1 header + search ── */
.p1-hdr{{background:#2c3e50;color:#fff;padding:5px 8px;font-size:11px;
         font-weight:bold;display:flex;justify-content:space-between;
         align-items:center;flex-shrink:0}}
.cb{{background:none;border:1px solid #7f8c8d;color:#bdc3c7;
     font-size:10px;padding:1px 5px;cursor:pointer;border-radius:3px;
     margin-left:2px}}
.cb:hover{{background:#3d5166;color:#fff}}
.p1-search-row{{display:flex;flex-direction:column;gap:2px;padding:4px 5px;
                background:#f0f2f5;border-bottom:1px solid #dde;flex-shrink:0}}
/* ── Checkbox-dropdown filter ── */
.cbdd{{position:relative;width:100%}}
.cbdd-btn{{display:flex;align-items:center;justify-content:space-between;
           width:100%;padding:3px 6px;font-size:10px;border:1px solid #bbb;
           border-radius:3px;background:#fff;cursor:pointer;text-align:left;
           color:#2c3e50;gap:4px;box-sizing:border-box}}
.cbdd-btn:hover{{background:#eaf4ff;border-color:#3498db}}
.cbdd-btn .cbdd-lbl{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.cbdd-btn .cbdd-arrow{{color:#888;font-size:9px;flex-shrink:0}}
.cbdd-panel{{display:none;position:absolute;top:calc(100% + 2px);left:0;right:0;
             background:#fff;border:1px solid #bbb;border-radius:4px;
             box-shadow:0 4px 12px rgba(0,0,0,.18);z-index:500;
             max-height:220px;overflow-y:auto;min-width:160px}}
.cbdd-panel.open{{display:block}}
.cbdd-ctrl{{display:flex;gap:3px;padding:4px 6px;border-bottom:1px solid #eee;
            background:#f8f9fa;position:sticky;top:0}}
.cbdd-ctrl button{{font-size:9px;padding:1px 7px;border:1px solid #ccc;
                   border-radius:3px;background:#fff;cursor:pointer;color:#2c3e50}}
.cbdd-ctrl button:hover{{background:#d6eaff;border-color:#3498db}}
.cbdd-item{{display:flex;align-items:center;gap:5px;padding:3px 8px;
            font-size:10px;cursor:pointer;color:#2c3e50}}
.cbdd-item:hover{{background:#eaf4ff}}
.cbdd-item input{{margin:0;cursor:pointer;accent-color:#3498db}}
/* ── TP Info panel ── */
.tp-info-wrap{{flex-shrink:0;border-bottom:1px solid #c8d8e8;background:#f5f9ff}}
.tp-info-hdr{{display:flex;align-items:center;gap:5px;padding:4px 8px;
              background:#d0e4f5;cursor:pointer;user-select:none;font-size:10px;font-weight:bold;color:#1a3a5c}}
.tp-info-hdr:hover{{background:#bcd3ec}}
.tp-info-body{{padding:4px 8px 6px 8px;display:none}}
.tp-info-body.open{{display:block}}
.tp-card{{background:#fff;border:1px solid #cde;border-radius:4px;padding:5px 8px;margin-bottom:5px;font-size:10px}}
.tp-card:last-child{{margin-bottom:0}}
.tp-card-title{{font-size:10.5px;font-weight:bold;color:#1a3a5c;margin-bottom:4px;
                word-break:break-all;line-height:1.3}}
.tp-nick{{font-weight:normal;color:#2980b9}}
.tp-fields{{display:grid;grid-template-columns:auto 1fr;row-gap:2px;column-gap:6px;
            align-items:start;margin-bottom:4px}}
.tp-lbl{{color:#6a8aaa;font-weight:bold;white-space:nowrap}}
.tp-val{{color:#2c3e50;word-break:break-all;line-height:1.3}}
.tp-skip summary{{font-size:10px;color:#7d4e00;cursor:pointer;margin:3px 0 2px 0;font-weight:bold}}
.tp-skip-list{{display:flex;flex-wrap:wrap;gap:3px;margin-top:3px}}
.tp-mod{{background:#fff3cd;border:1px solid #ffc107;border-radius:3px;
         padding:1px 5px;font-size:9.5px;color:#7d4e00;white-space:nowrap}}
.p1-body{{flex:1;overflow-y:auto}}
/* ── Roll-Down Details/Simulator modal ── */
.ri-overlay{{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.55);
             z-index:9999;display:flex;align-items:flex-start;justify-content:center;
             padding:24px 10px;overflow-y:auto}}
.ri-card{{background:#fff;border-radius:8px;box-shadow:0 8px 40px rgba(0,0,0,0.35);
          width:min(980px,96vw);display:flex;flex-direction:column;overflow:hidden}}
.ri-hdr{{background:linear-gradient(135deg,#1a4a7a,#2574b0);color:#fff;padding:11px 16px;
         display:flex;align-items:center;justify-content:space-between;flex-shrink:0}}
.ri-body{{display:flex;flex-direction:column;overflow-y:auto;max-height:80vh}}
.ri-algo{{padding:0;border-bottom:1px solid #dce8f5;flex-shrink:0}}
.ri-algo summary{{padding:8px 16px;cursor:pointer;background:#eef5ff;
                  font-size:12px;font-weight:bold;color:#1a4a7a;user-select:none;list-style:none}}
.ri-algo summary::-webkit-details-marker{{display:none}}
.ri-algo summary::before{{content:'\\25BA\\00a0'}}
.ri-algo[open] summary::before{{content:'\\25BC\\00a0'}}
.ri-algo-inner{{padding:12px 16px 14px 16px;font-size:11px;color:#2c3e50;line-height:1.7;
                background:#fafcff;display:grid;grid-template-columns:1fr 1fr;gap:0 24px}}
.ri-sim-body{{padding:14px 16px}}
.ri-grid th{{padding:5px 10px;background:#2c3e50;color:#fff;border:1px solid #445;white-space:nowrap;font-weight:normal;font-size:11px}}
.ri-grid td{{padding:4px 8px;border:1px solid #dde;font-size:11px}}
.ri-grid input[type=number]{{width:82px;padding:2px 4px;border:1px solid #bbc;border-radius:3px;font-size:11px}}
.ri-res-row-landed{{background:#e8f5e9!important}}
/* ── Filter table ── */
.wfr-tbl{{border-collapse:collapse;width:100%;font-size:11px}}
.wfr-tbl th{{background:#34495e;color:#ecf0f1;padding:3px 7px;
             text-align:left;position:sticky;top:0;z-index:2;
             white-space:nowrap}}
.wfr-tbl td{{padding:3px 7px;border-bottom:1px solid #eee;
             white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:120px}}
.wfr-tbl tr.wfr-row{{cursor:pointer}}
.wfr-tbl tr.wfr-row.sel td{{background:#d6eaff}}
.wfr-tbl tr.wfr-row:not(.sel):hover td{{background:#eaf4ff}}
.wfr-tbl tr.lot-hdr td{{background:#2c3e50;color:#ecf0f1;font-weight:bold;
                         font-size:11px;cursor:pointer;padding:3px 7px}}
.wfr-tbl tr.lot-hdr:hover td{{background:#34495e}}
.wfr-tbl tr.wfr-hidden{{display:none}}
.col-rz{{position:absolute;right:0;top:0;bottom:0;width:5px;cursor:col-resize;
         background:transparent;z-index:3;user-select:none}}
.col-rz:hover{{background:rgba(255,255,255,0.35)}}
/* ── Param table (panel 2) ── */
.hm-tbl{{border-collapse:collapse;font-size:11px;
         white-space:nowrap;table-layout:auto;width:100%}}
.hm-tbl th{{background:#2c3e50;color:#fff;padding:4px 8px;
            text-align:right;position:sticky;top:0;z-index:1}}
.hm-tbl th:first-child{{text-align:left;position:sticky;left:0;z-index:2;
                         background:#2c3e50}}
.hm-tbl td{{padding:3px 8px;border-bottom:1px solid #eee;
            text-align:right;white-space:nowrap}}
.hm-tbl tbody tr:nth-child(even):not(.cat-hdr){{background:#f4f8ff}}
.hm-tbl td.tn{{position:sticky;left:0;background:#f8f9fa;text-align:left;
               cursor:pointer;border-right:2px solid #dde;z-index:1;
               max-width:200px;overflow:hidden;text-overflow:ellipsis}}
.hm-tbl td.tn:hover{{background:#eaf4ff}}
.hm-tbl tr.sel-row td{{background:#eaf4ff!important}}
.hm-tbl tr.sel-row td.tn{{background:#d6eaff!important;border-left:3px solid #2980b9;font-weight:bold}}
.hm-tbl tbody tr:not(.cat-hdr):hover{{background:#eaf4ff!important;cursor:pointer}}
.hm-tbl tr.cat-hdr td{{background:#2c3e50;color:#ecf0f1;font-weight:bold;
                        font-size:11px;cursor:pointer;padding:4px 8px}}
.hm-tbl tr.cat-hdr:hover td{{background:#34495e}}
.hm-tbl tr.grp-hidden{{display:none}}
.cell-r{{background:#fdecea!important;color:#c0392b;font-weight:bold}}
/* ── Variability group cards ── */
.grp-card{{background:#fff;border-radius:5px;
           box-shadow:0 1px 4px rgba(0,0,0,.10);margin-bottom:8px;overflow:hidden}}
.grp-card-hdr{{display:flex;align-items:center;gap:5px;padding:4px 10px;
               font-size:11px;font-weight:bold;color:#ecf0f1;background:#34495e;
               cursor:pointer;user-select:none}}
.grp-card-body{{padding:0}}
.grp-card.gc-collapsed .grp-card-body{{display:none}}
/* ── Toolbar (variability) ── */
#var-toolbar{{display:flex;flex-wrap:wrap;align-items:center;gap:5px;
              padding:5px 12px;background:#1f3a50;color:#ecf0f1;
              font-size:11px;flex-shrink:0;border-bottom:1px solid #1a252f}}
#var-toolbar label{{cursor:pointer;display:flex;align-items:center;gap:3px}}
#var-toolbar b{{color:#f1c40f}}
#var-toolbar select{{font-size:11px;padding:1px 3px;border-radius:3px;
                     border:1px solid #4a6278;background:#2c3e50;color:#ecf0f1}}
/* ── Param detail modal ── */
.pm-overlay{{position:fixed;inset:0;background:rgba(10,14,26,0.72);z-index:10000;display:none;align-items:center;justify-content:center}}
.pm-card{{background:#fff;border-radius:8px;box-shadow:0 8px 40px rgba(0,0,0,.45);width:min(96vw,860px);max-height:92vh;display:flex;flex-direction:column;overflow:hidden}}
.pm-hdr{{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;background:#2c3e50;color:#fff;flex-shrink:0}}
.pm-hdr-title{{font-size:13px;font-weight:bold;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;margin-right:8px}}
.pm-close{{background:none;border:1px solid #7f8c8d;color:#bdc3c7;font-size:16px;line-height:1;padding:2px 8px;border-radius:4px;cursor:pointer}}
.pm-close:hover{{background:#e74c3c;border-color:#e74c3c;color:#fff}}
.pm-body{{flex:1;overflow-y:auto;padding:12px 16px;background:#f0f2f5}}
.pm-stat-row{{display:flex;flex-wrap:wrap;background:#fff;border:1px solid #e0e0e0;border-radius:5px;margin-bottom:10px;overflow:hidden}}
.pm-stat{{display:inline-flex;flex-direction:column;align-items:center;gap:1px;padding:5px 14px;border-right:1px solid #eee}}
.pm-stat-lbl{{font-size:9px;color:#888;font-weight:bold;text-transform:uppercase;white-space:nowrap}}
.pm-stat-val{{font-size:14px;font-weight:bold}}
.pm-grp-leg{{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:11px;margin-top:6px;padding:4px 2px}}
.fm-card-r{{resize:both;overflow:auto;min-width:860px;min-height:400px;max-width:min(96vw,1400px);max-height:min(92vh,960px)}}
#fm-overlay{{position:fixed!important;top:60px;right:20px;bottom:auto;left:auto;background:none!important;pointer-events:none;z-index:9999!important;align-items:unset!important;justify-content:unset!important}}
#fm-overlay>.pm-card{{pointer-events:auto;border:2px solid #2c3e50}}
#fm-overlay .pm-hdr{{cursor:move;user-select:none;-webkit-user-select:none}}
</style>
</head>
<body>
<div id="app">

  <!-- Header -->
  <div class="page-hdr">
    <h1>{title}</h1>
    <div class="sub">{subtitle}</div>
  </div>

  <!-- Info bar -->
  <div class="info-bar">
    <span><b>Lots:</b> {n_lots}</span>
    <span class="sep">|</span>
    <span><b>Wafers:</b> {n_wfrs}</span>
    <span class="sep">|</span>
    <span><b>Generated:</b> {date_str}</span>
    <span id="ib-sel" style="margin-left:6px;color:#aed6f1"></span>
  </div>

  <!-- Tab bar -->
  <div class="tabs">
    <button class="tab-btn active" onclick="showTab(this,'tab-flow')">&#9889; Speed Flow (Freq/Vmin)</button>
    {xy_tab_labels}
  </div>

  <!-- Variability toolbar -->
  <div id="var-toolbar" style="display:none">
    <b>Group&nbsp;by:</b>
    <label><input type="checkbox" id="gby-none" checked
           onchange="setGby('none',this.checked)">None</label>
    <label><input type="checkbox" id="gby-lot"
           onchange="setGby('lot',this.checked)">Lot</label>
    <label><input type="checkbox" id="gby-wafer"
           onchange="setGby('wafer',this.checked)">Wafer</label>
    <span style="width:1px;background:#4a6278;align-self:stretch;margin:0 4px"></span>
    <b>Mode:</b>
    <label><input type="radio" name="var-mode" value="perdie" checked
           onchange="_VAR_PER_SITE=this.value==='perdie';drawAllCharts()">Per die</label>
    <label><input type="radio" name="var-mode" value="median"
           onchange="_VAR_PER_SITE=this.value==='perdie';drawAllCharts()">Median</label>
    <span style="width:1px;background:#4a6278;align-self:stretch;margin:0 4px"></span>
    <b>&#8597; H:</b>
    <input type="range" id="var-h-sl" min="200" max="900" step="25" value="480"
           oninput="_CHART_H=+this.value;
                    document.getElementById('var-h-val').textContent=this.value+'px';
                    drawAllCharts()"
           style="width:80px;accent-color:#3498db">
    <span id="var-h-val" style="min-width:34px;color:#aed6f1;font-size:10px">480px</span>
    <span style="width:1px;background:#4a6278;align-self:stretch;margin:0 4px"></span>
    <button onclick="downloadVarCSV()"
            style="padding:2px 8px;font-size:10px;font-weight:bold;border:none;
                   border-radius:3px;background:#27ae60;color:#fff;cursor:pointer"
            onmouseover="this.style.background='#1e8449'"
            onmouseout="this.style.background='#27ae60'">&#11015; CSV</button>
  </div>

  <!-- Three-panel layout -->
  <div class="three-panel">

    <!-- Panel 1: filter -->
    <div id="panel1">
      <div class="p1-hdr">
        <span style="display:flex;align-items:center;gap:4px">
          <input type="checkbox" id="master-cb" onclick="masterToggle(this)"
                 title="Select / deselect all" style="width:14px;height:14px;cursor:pointer;accent-color:#3498db">
          <button class="cb" id="show-sel-btn"
                  onclick="toggleShowSel()" title="Show selected only">Sel</button>
          <button class="cb" onclick="collapseAll()" title="Collapse all groups">&#9654;&#9654;</button>
        </span>
        &#128269; Filter
        <span id="row-info" style="font-weight:normal;font-size:10px;color:inherit"></span>
      </div>
      <div class="p1-search-row">
        <div class="cbdd" id="dd-lot">
          <button class="cbdd-btn" onclick="cbddToggle('lot')">
            <span class="cbdd-lbl" id="dd-lot-lbl">Lot ▼</span>
          </button>
          <div class="cbdd-panel" id="dd-lot-panel">
            <div class="cbdd-ctrl">
              <button onclick="cbddAll('lot')">All</button>
              <button onclick="cbddNone('lot')">None</button>
            </div>
            <div id="dd-lot-items"></div>
          </div>
        </div>
        <div class="cbdd" id="dd-wafer">
          <button class="cbdd-btn" onclick="cbddToggle('wafer')">
            <span class="cbdd-lbl" id="dd-wafer-lbl">Wafer ▼</span>
          </button>
          <div class="cbdd-panel" id="dd-wafer-panel">
            <div class="cbdd-ctrl">
              <button onclick="cbddAll('wafer')">All</button>
              <button onclick="cbddNone('wafer')">None</button>
            </div>
            <div id="dd-wafer-items"></div>
          </div>
        </div>
        <div class="cbdd" id="dd-prog">
          <button class="cbdd-btn" onclick="cbddToggle('prog')">
            <span class="cbdd-lbl" id="dd-prog-lbl">Prog ▼</span>
          </button>
          <div class="cbdd-panel" id="dd-prog-panel">
            <div class="cbdd-ctrl">
              <button onclick="cbddAll('prog')">All</button>
              <button onclick="cbddNone('prog')">None</button>
            </div>
            <div id="dd-prog-items"></div>
          </div>
        </div>
        <div class="cbdd" id="dd-layout">
          <button class="cbdd-btn" onclick="cbddToggle('layout')">
            <span class="cbdd-lbl" id="dd-layout-lbl">Layout ▼</span>
          </button>
          <div class="cbdd-panel" id="dd-layout-panel">
            <div class="cbdd-ctrl">
              <button onclick="cbddAll('layout')">All</button>
              <button onclick="cbddNone('layout')">None</button>
            </div>
            <div id="dd-layout-items"></div>
          </div>
        </div>
        {'<div class="cbdd" id="dd-prog6248"><button class="cbdd-btn" onclick="cbddToggle(\'prog6248\')"><span class="cbdd-lbl" id="dd-prog6248-lbl">Class Prog 6248 &#9660;</span></button><div class="cbdd-panel" id="dd-prog6248-panel"><div class="cbdd-ctrl"><button onclick="cbddAll(\'prog6248\')">All</button><button onclick="cbddNone(\'prog6248\')">None</button></div><div id="dd-prog6248-items"></div></div></div>' if prog6248_col else ''}
      </div>
      <!-- TP Info accordion (populated by JS) -->
      <div class="p1-body">
        <table class="wfr-tbl">
          <thead>
            <tr>
              {col_hdr_sort_lot}
              {col_hdr_prog6248}
              <th>Sort_Wafer</th>
              {col_hdr_u1u5}
              {col_hdr_dev_rev}
              {col_hdr_x}
              {col_hdr_y}
              <th style="{_TH_ST}">N</th>
            </tr>
          </thead>
          <tbody id="wfr-tbody"></tbody>
        </table>
      </div>
    </div>

    <!-- P1 resize handle -->
    <div class="p1-resize" id="p1-resize"></div>

    <!-- sp12: toggle panel 2 -->
    <div class="sp12" id="sp12" onmousedown="startSplitSp12(event)"
         title="Drag to resize | click to toggle table">
      <button class="sp12-btn" id="p2-toggle-btn"
              onclick="event.stopPropagation();toggleP2()"
              title="Toggle parameter table">&#9654;</button>
    </div>

    <!-- Panel 2: parameter table -->
    <div id="panel2" class="p2-hidden">
      <div class="p2-hdr">
        &#128202; Parameter Table
        <button onclick="downloadVarCSV()"
                style="margin-left:6px;padding:2px 8px;font-size:10px;font-weight:bold;
                       border:none;border-radius:3px;background:#27ae60;
                       color:#fff;cursor:pointer"
                onmouseover="this.style.background='#1e8449'"
                onmouseout="this.style.background='#27ae60'">&#11015; CSV</button>
      </div>
      <div class="p2-body">
        <table class="hm-tbl">
          <thead id="var-head"></thead>
          <tbody id="var-body"></tbody>
        </table>
      </div>
    </div>

    <!-- sp23 resize -->
    <div class="sp23" id="sp23" onmousedown="startSplitSp23(event)"></div>

    <!-- Panel 3: content area -->
    <div id="panel3">

      <!-- Variability tab -->
      <div id="tab-var" class="tab-panel">
        {grp_cards_html}
      </div>

      <!-- Distribution tab -->
      <div id="tab-dist" class="tab-panel">
        {dist_html}
      </div>

      <!-- XY (Vmin) tabs -->
      {xy_tab_divs}

      <!-- Speed Flow tab -->
      <div id="tab-flow" class="tab-panel active">
        <div style="background:#1f3a50; border-bottom: 2px solid #27ae60; display:flex; padding:5px 12px; gap:5px;">
          <button id="btn-flow-freq" class="tab-btn active" onclick="_setFlowSubTab('freq')">Freq Matrix</button>
          <button id="btn-flow-bin" class="tab-btn" onclick="_setFlowSubTab('bin')">Bin Matrix</button>
        </div>
        <div id="tab-flow-freq" style="display:block; flex:1; overflow:hidden;"><div id="tab-flow-body"></div></div>
        <div id="tab-flow-bin" style="display:none;"><div id="tab-flow-bin-body" style="padding:15px;"></div></div>
      </div>

    </div><!-- #panel3 -->
  </div><!-- .three-panel -->
</div><!-- #app -->

<script src="{DATA_JS_FILENAME}"></script>
<script>
/* ── Global state ──────────────────────────────────────────────────────── */
var SEL_WFR      = new Set();   // indices into WFR_DATA
var _SHOW_SEL    = false;
var _SEARCH      = {{lot:null,wafer:null,prog:null,layout:null,prog6248:null}};
var _GRP_COLLAPSE= {{}};   // material -> collapsed
var _FLOW_PROG_ACTIVE = new Set(); // prog6248 values to include in freq matrix
var _FLOW_LOT_ACTIVE  = new Set(); // "prog\x00lot" keys to include in freq matrix
var _FLOW_PROG_USER_HIDDEN = new Set(); // progs user explicitly deselected via dropdown
var _VAR_PER_SITE = true;
var _CHART_H     = 480;
var _GBY         = [];     // group-by fields
var SEL_PARAM    = null;
var _GRP_VIS     = {{}};
var _GRP_ROW_COLLAPSE = {{}};  // param-table group collapse
var _DRAW_PENDING = null;
var _DIST_COLLAPSED = {{}};
var _XY_COLLAPSED   = {{}};
var _FP_ST = {{}};
var _FP_GSTATS = {{}};  /* cached group stats per pid */
var _XY_DRAW_PENDING = {{}};
var _DRAG_CUR_A = {{x:null,y:null}};  /* XY drag cursor A — persists across redraws */
var _DRAG_CUR_B = {{}};               /* XY drag cursor B — keyed by pid */

PCM_GROUPS.forEach(function(g){{_GRP_VIS[g]=true;}});

// Init SEL_WFR = all
WFR_DATA.forEach(function(_,i){{SEL_WFR.add(i);}});

/* ── Test-Program Info panel ────────────────────────────────────────────── */
function _tpEsc(s){{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}
function toggleTpInfo(){{
  var b=document.getElementById('tp-info-body');
  var a=document.getElementById('tp-info-arrow');
  if(!b) return;
  var open=b.classList.toggle('open');
  if(a) a.innerHTML=open?'&#9660;':'&#9654;';
}}
function _renderTpDetails(){{
  var wrap=document.getElementById('tp-info-wrap');
  var body=document.getElementById('tp-info-body');
  var cnt=document.getElementById('tp-info-count');
  if(!wrap||!body) return;
  // Collect unique prog6248 names present in the loaded data
  var present={{}};
  WFR_DATA.forEach(function(w){{if(w.prog6248&&w.prog6248.trim()) present[w.prog6248.trim()]=1;}});
  var names=Object.keys(present).filter(function(k){{return TP_INFO[k];}});
  if(!names.length){{ wrap.style.display='none'; return; }}
  wrap.style.display='';
  if(cnt) cnt.textContent='('+names.length+')';
  var html='';
  names.forEach(function(tp){{
    var f=TP_INFO[tp]||{{}};
    html+='<div class="tp-card">';
    html+='<div class="tp-card-title">'+_tpEsc(f['Test Program Name']||tp);
    var nick=f['Test Program Short Name [Nick Name]']||'';
    if(nick) html+=' <span class="tp-nick">('+_tpEsc(nick)+')</span>';
    html+='</div>';
    // key fields grid
    var rows=[
      ['Built',      f['Built Date']||''],
      ['TOS',        f['TOS Profile']||''],
      ['PRIME',      (f['PRIME_DLL_PATH']||'').split('\\n')[0].replace(/^I:[^\\/]+[\\/]/i,'')],
      ['Step',       f['Stepping']||''],
      ['Products',   f['Products/Subfamily']||''],
      ['Integrator', f['Test Program Integrator']||''],
      ['Class',      f['Classification']||''],
    ];
    var hasField=false;
    var fhtml='<div class="tp-fields">';
    rows.forEach(function(r){{
      if(!r[1]) return;
      hasField=true;
      fhtml+='<span class="tp-lbl">'+_tpEsc(r[0])+':</span><span class="tp-val">'+_tpEsc(r[1])+'</span>';
    }});
    fhtml+='</div>';
    if(hasField) html+=fhtml;
    // skipped modules
    var skip=f['Skipped Modules']||'';
    if(skip){{
      var mods=skip.split(/[,\\n]/).map(function(s){{return s.trim();}}).filter(Boolean);
      html+='<details class="tp-skip"><summary>Skipped Modules ('+mods.length+')</summary>';
      html+='<div class="tp-skip-list">';
      mods.forEach(function(m){{html+='<span class="tp-mod">'+_tpEsc(m)+'</span>';}});
      html+='</div></details>';
    }}
    html+='</div>';
  }});
  body.innerHTML=html;
}}

/* ── Utility ────────────────────────────────────────────────────────────── */
function _rKey(r){{return r.lot+'/'+r.wafer+'/'+(r.prog6248||'');}}

function activeKeys(){{
  var s=new Set();
  SEL_WFR.forEach(function(i){{
    var w=WFR_DATA[i];
    if(!_cbddPass(w)) return;
    s.add(w.lot+'/'+w.wafer);                       // lot/wafer — for PCM/param-table rows
    s.add(w.lot+'/'+w.wafer+'/'+(w.prog6248||'')); // lot/wafer/prog — for speed-flow rows
    s.add(w.lot+'/'+w.wafer+'/');                   // lot/wafer/ — for prog-less rows (Vmin aggregates)
  }});
  return s;
}}
// Normalise a FLOW_DATA/PASS_TABLE row key to match activeKeys() format
// r[1]=sort_lot, r[2]=sort_wafer (may be "202.0"), r[8]=prog6248
function _flowNormKey(r){{
  var n=parseFloat(r[2]);
  return String(r[1]||'')+'/'+(isNaN(n)?String(r[2]||''):String(Math.round(n)))+'/'+(r[8]!=null?String(r[8]):'');
}}
// Filter rows by activeKeys and return {{n, med}} or null if empty
function _filtStats(rows, ak){{
  var fRows=rows.filter(function(r){{return ak.has(_flowNormKey(r));}});
  if(!fRows.length) return null;
  // deduplicate by prog|pkg to match IBIN denominator (avoids counting
  // the same physical unit twice when multiple CSV files are combined)
  var _seen=new Set();
  var uRows=fRows.filter(function(r){{
    var k=(r[8]!=null?String(r[8]):'')+'\u007c'+String(r[0]||'');
    if(_seen.has(k))return false; _seen.add(k); return true;
  }});
  var sv=uRows.map(function(r){{return r[5];}}).sort(function(a,b){{return a-b;}});
  var m=sv.length>>1;
  return {{n:uRows.length, med:sv.length%2?sv[m]:(sv[m-1]+sv[m])/2}};
}}

function _flowInstLowestDenoms(fd, ak){{
  var out={{}};
  (fd.instances||[]).forEach(function(inst){{
    var freqs=(inst.freqs||[]).slice().sort(function(a,b){{return a.freq_mhz-b.freq_mhz;}});
    if(!freqs.length){{out[inst.idx]=1;return;}}
    var low=freqs[0];
    var cnt=(low.rows||[]).filter(function(r){{return ak.has(_flowNormKey(r));}}).length;
    out[inst.idx]=cnt>0?cnt:1;
  }});
  return out;
}}

function _flowPassLowestDenom(ptd, ak){{
  if(!ptd || !ptd.freq_data) return 1;
  var keys=Object.keys(ptd.freq_data).map(Number);
  if(!keys.length) return 1;
  var low=Math.min.apply(null, keys);
  var fd2=ptd.freq_data[String(low)]||{{}};
  var seen={{}};
  Object.keys(fd2.groups||{{}}).forEach(function(nKey){{
    (fd2.groups[nKey].rows||[]).forEach(function(r){{
      if(ak.has(_flowNormKey(r))) seen[String(r[0]||'')]=1;
    }});
  }});
  var n=Object.keys(seen).length;
  return n>0?n:1;
}}

function _ibin1Denom(ak){{
  // Mirror _filtStats: filter by activeKeys (ak), deduplicate by prog|pkg.
  // A die retested under A/B/C counts once per program run — same as freq matrix.
  if(IBIN1_WFR_PKGS && Object.keys(IBIN1_WFR_PKGS).length){{
    var seen=new Set();
    WFR_DATA.forEach(function(w,i){{
      if(!SEL_WFR.has(i))return;
      if(!_cbddPass(w))return;
      var prog=(w.prog6248||'');
      var akKey=w.lot+'/'+w.wafer+'/'+prog;
      if(!ak.has(akKey))return;
      var wk=prog+'|'+(w.sort_lot||w.lot||'')+'/'+(w.wafer||'');
      (IBIN1_WFR_PKGS[wk]||[]).forEach(function(p){{seen.add(prog+'|'+p);}});
    }});
    return seen.size > 0 ? seen.size : (IBIN1_COUNT > 0 ? IBIN1_COUNT : 1);
  }}
  return (IBIN1_COUNT>0?IBIN1_COUNT:1);
}}

function _allActiveKeys(){{
  // Returns lot/wafer and lot/wafer/prog keys for wafers that pass ALL left
  // panel checkbox filters (lot, wafer, prog/prog6248, layout) AND are selected
  // in SEL_WFR. This keeps the freq/bin matrix in sync with the left panel.
  var s=new Set();
  WFR_DATA.forEach(function(w,i){{
    if(!SEL_WFR.has(i))return;
    if(!_cbddPass(w))return;
    s.add(w.lot+'/'+w.wafer);
    s.add(w.lot+'/'+w.wafer+'/'+(w.prog6248||''));
  }});
  return s;
}}

var _COLOUR_PAL = {json.dumps(_COLOUR_PAL, separators=(',',':'))};
var _cCache={{}};
function _cMap(){{
  var keys=[]; var map={{}};
  if(_GBY.length===0) return {{map:{{}},keys:[]}};
  SEL_WFR.forEach(function(i){{
    var w=WFR_DATA[i];
    var k=_GBY.map(function(f){{return w[f]||'';}}). join('/');
    if(!map[k]){{map[k]=_COLOUR_PAL[keys.length%_COLOUR_PAL.length]; keys.push(k);}}
  }});
  return {{map:map,keys:keys}};
}}
function _grpKey(r){{
  return _GBY.map(function(f){{return r[f]||'';}}).join('/');
}}
function _cPal(i){{return _COLOUR_PAL[i%_COLOUR_PAL.length];}}
function _pDisp(key){{var m=PCM_PARAM_META[key]||{{}};return m.name||key;}}

function _sRand(s){{var x=Math.sin(s+1)*10000; return x-Math.floor(x);}}
function _med(arr){{
  if(!arr.length)return null;
  var s=arr.slice().sort(function(a,b){{return a-b;}});
  var m=Math.floor(s.length/2);
  return s.length%2?s[m]:(s[m-1]+s[m])/2;
}}
function _fmt(v,p){{
  if(v==null||v!=v)return '';
  var f=parseFloat(v); p=p||4;
  if(Math.abs(f)>=1000)return f.toFixed(0);
  if(Math.abs(f)>=10)return f.toFixed(1);
  return f.toPrecision(p);
}}
function _fmtVmin(v){{
  if(v==null||v!=v)return '';
  return parseFloat(v).toFixed(3);
}}
function _fmtUpm(v){{
  if(v==null||v!=v)return '';
  return parseFloat(v).toFixed(1);
}}
function _fpUpmRef(paramKey){{
  var v=(UPM_REFS&&Object.prototype.hasOwnProperty.call(UPM_REFS,paramKey))?UPM_REFS[paramKey]:null;
  v=parseFloat(v);
  return (isFinite(v)&&v>0)?v:null;
}}
function _fpAsPct(v, ref){{
  if(ref==null||v==null||!isFinite(v))return v;
  return (v/ref)*100.0;
}}
function _fmtUpmAxis(v, asPct){{
  var s=_fmtUpm(v);
  return asPct?(s+'%'):s;
}}
function _niceStep(r){{
  if(r<=0||!isFinite(r))return 0.1;
  var m=Math.pow(10,Math.floor(Math.log10(r)));
  var s=r/m;
  return s<1.5?m:s<3?2*m:s<7?5*m:10*m;
}}

/* ── Shared helpers ──────────────────────────────────────────────────────── */
/* Build WFR lookup keyed by "lot/wafer" for program fields */
var _WFR_LOOKUP={{}};
(function(){{
  WFR_DATA.forEach(function(w){{_WFR_LOOKUP[w.lot+'/'+w.wafer]=w;}});
}})();

function _std(arr){{
  if(!arr||arr.length<2)return 0;
  var m=arr.reduce(function(a,b){{return a+b;}},0)/arr.length;
  var vr=arr.reduce(function(a,b){{return a+(b-m)*(b-m);}},0)/(arr.length-1);
  return Math.sqrt(vr);
}}
function _pct(arr,p){{
  if(!arr||!arr.length)return null;
  var s=arr.slice().sort(function(a,b){{return a-b;}});
  var i=(p/100)*(s.length-1);
  var lo=Math.floor(i),hi=Math.ceil(i);
  return s[lo]+(s[hi]-s[lo])*(i-lo);
}}
/* Group key for a PCM_ROWS row given gby array */
function _grpKeyWith(r,gby){{
  if(!gby||!gby.length)return 'All';
  var w=_WFR_LOOKUP[r.lot+'/'+r.wafer]||{{}};
  var parts=[];
  if(gby.indexOf('lot')>=0)parts.push(r.lot||'');
  if(gby.indexOf('wafer')>=0)parts.push(String(r.wafer||''));
  if(gby.indexOf('prog6248')>=0)parts.push(w.prog6248||'');
  if(gby.indexOf('progU1U5')>=0)parts.push(w.progU1U5||'');
  if(gby.indexOf('material')>=0)parts.push(w.material||(w.sort_lot?_lotMat(w.sort_lot):'Others'));
  return parts.join('/')||'All';
}}
/* Colour map for given gby: returns {{map:{{key:colour}}, keys:[...ordered]}} */
var _PALETTE=['#3498db','#e74c3c','#2ecc71','#9b59b6','#f39c12','#1abc9c','#e67e22','#34495e',
              '#16a085','#c0392b','#27ae60','#8e44ad','#d35400','#2c3e50','#7f8c8d','#f1c40f'];
function _cMapWith(rows,gby){{
  var keys=[],seen={{}};
  rows.forEach(function(r){{
    var k=_grpKeyWith(r,gby);
    if(!seen[k]){{seen[k]=true;keys.push(k);}}
  }});
  var map={{}};
  keys.forEach(function(k,i){{map[k]=_PALETTE[i%_PALETTE.length];}});
  return {{map:map,keys:keys}};
}}
/* OLS trend fit: returns {{m,b}} or null */
function _olsFit(pts){{
  var n=pts.length;
  if(n<2)return null;
  var sx=0,sy=0,sxx=0,sxy=0;
  pts.forEach(function(p){{sx+=p[0];sy+=p[1];sxx+=p[0]*p[0];sxy+=p[0]*p[1];}});
  var d=n*sxx-sx*sx;
  if(!d)return null;
  return {{m:(n*sxy-sx*sy)/d,b:(sy*sxx-sx*sxy)/d}};
}}
/* Theil-Sen trend fit: returns {{m,b}} or null */
function _theilsenFit(pts){{
  if(pts.length<2)return null;
  var slopes=[];
  for(var i=0;i<pts.length-1;i++){{
    for(var j=i+1;j<pts.length;j++){{
      var dx=pts[j][0]-pts[i][0];
      if(dx!==0)slopes.push((pts[j][1]-pts[i][1])/dx);
    }}
  }}
  if(!slopes.length)return null;
  var m=_pct(slopes,50);
  var ints=pts.map(function(p){{return p[1]-m*p[0];}});
  return {{m:m,b:_pct(ints,50)}};
}}
function _escH(s){{return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}

function _reportJsError(where, err){{
  try {{
    var msg = (err && err.message) ? err.message : String(err || 'unknown error');
    console.error('Dashboard JS error ['+where+']:', err || msg);
    var b = document.getElementById('js-error-banner');
    if(!b){{
      b = document.createElement('div');
      b.id = 'js-error-banner';
      b.style.cssText = 'position:fixed;left:10px;right:10px;top:10px;z-index:20000;padding:8px 12px;border:1px solid #b71c1c;border-radius:6px;background:#ffebee;color:#7f0000;font:12px Arial,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.2)';
      document.body.appendChild(b);
    }}
    b.textContent = 'JavaScript error in ' + where + ': ' + msg;
  }} catch(_e){{}}
}}

function _safeInnerHTML(el, html){{
  /* Blur any focused descendant first to prevent Chrome's
     "node no longer a child" DOMException during innerHTML replacement. */
  try {{
    var ae = document.activeElement;
    if(ae && ae !== document.body && el.contains(ae)) ae.blur();
  }} catch(_b){{}}
  try {{
    el.innerHTML = html;
  }} catch(e){{
    /* Fallback: manually clear children, then assign. */
    try {{
      while(el.firstChild) el.removeChild(el.firstChild);
      el.innerHTML = html;
    }} catch(e2){{ _reportJsError('_safeInnerHTML', e2); }}
  }}
}}

window.addEventListener('error', function(ev){{
  _reportJsError('window.onerror', ev && (ev.error || ev.message));
}});
window.addEventListener('unhandledrejection', function(ev){{
  _reportJsError('unhandledrejection', ev && ev.reason);
}});

function showTab(btn,id){{
  try {{
    document.querySelectorAll('.tab-btn').forEach(function(b){{b.classList.remove('active');}});
    document.querySelectorAll('.tab-panel').forEach(function(p){{p.classList.remove('active');}});
    btn.classList.add('active');
    var el=document.getElementById(id);
    if(el)el.classList.add('active');
    var vt=document.getElementById('var-toolbar');
    if(vt) vt.style.display=(id==='tab-var')?'flex':'none';
    // lazy render for distribution / xy tabs
    if(id==='tab-dist') buildDistTab();
    if(id==='tab-flow') buildFlowTab();
    var xym=id.match(/^tab-xy(\\d+)$/);
    if(xym)buildXYTab(+xym[1]);
  }} catch(e){{
    _reportJsError('showTab('+id+')', e);
  }}
}}

/* ── Panel 1 filter logic ─────────────────────────────────────────────── */
var _COL_WIDTHS={{}};
function _initWfrTblResize(){{
  var tbl=document.querySelector('.wfr-tbl');
  if(!tbl)return;
  var ths=Array.prototype.slice.call(tbl.querySelectorAll('thead tr th'));
  ths.forEach(function(th,ci){{
    var old=th.querySelector('.col-rz');
    if(old)old.remove();
    if(_COL_WIDTHS[ci]!==undefined)th.style.minWidth=_COL_WIDTHS[ci]+'px';
    var rz=document.createElement('span');
    rz.className='col-rz';
    rz.title='Drag to resize \u00b7 double-click to reset';
    rz.addEventListener('dblclick',function(e){{
      e.stopPropagation();
      th.style.minWidth='';
      delete _COL_WIDTHS[ci];
    }});
    rz.addEventListener('mousedown',function(e){{
      e.stopPropagation();e.preventDefault();
      var startX=e.clientX,startW=th.getBoundingClientRect().width;
      document.body.style.cursor='col-resize';
      function onMove(e2){{
        var w=Math.max(36,startW+e2.clientX-startX);
        th.style.minWidth=w+'px';
        _COL_WIDTHS[ci]=w;
      }}
      function onUp(){{
        document.body.style.cursor='';
        document.removeEventListener('mousemove',onMove);
        document.removeEventListener('mouseup',onUp);
      }}
      document.addEventListener('mousemove',onMove);
      document.addEventListener('mouseup',onUp);
    }});
    th.appendChild(rz);
  }});
}}
function buildWfrList(){{
  var tbody=document.getElementById('wfr-tbody');
  if(!tbody)return;
  var rows=[]; var slotIdx=0;
  // Group by material
  var matMap={{}}, matOrder=[];
  WFR_DATA.forEach(function(w,i){{
    var mat = w.material || (w.sort_lot ? _lotMat(w.sort_lot) : 'Others');
    if(!matMap[mat]){{matMap[mat]=[];matOrder.push(mat);}}
    matMap[mat].push({{w:w,i:i}});
  }});
  var selCount=SEL_WFR.size;
  document.getElementById('row-info').textContent=
    selCount===WFR_DATA.length?'':
    '('+selCount+'/'+WFR_DATA.length+' sel)';
  var _mcb=document.getElementById('master-cb');
  if(_mcb){{
    _mcb.checked=selCount===WFR_DATA.length;
    _mcb.indeterminate=selCount>0&&selCount<WFR_DATA.length;
  }}

  var showSortLot = {json.dumps(bool(sort_lot_col))}.toString()==='true';
  var showProg = {json.dumps(bool(prog6248_col))}.toString()==='true';
  var showU1U5  = {json.dumps(bool(progU1U5_col))}.toString()==='true';
  var showDevRev= {json.dumps(bool(dev_rev_col))}.toString()==='true';
  var showX    = {json.dumps(bool(x_col))}.toString()==='true';
  var showY    = {json.dumps(bool(y_col))}.toString()==='true';

  matOrder.forEach(function(mat){{
    var wfrs=matMap[mat];
    if(_GRP_COLLAPSE[mat]===undefined)_GRP_COLLAPSE[mat]=true;
    var collapsed=_GRP_COLLAPSE[mat];
    var lotSel=wfrs.every(function(e){{return SEL_WFR.has(e.i);}});
    var lotPart=!lotSel&&wfrs.some(function(e){{return SEL_WFR.has(e.i);}});
    var cbId='grp-cb-'+mat.replace(/[^a-zA-Z0-9]/g,'_');
    var totalDies=wfrs.reduce(function(s,e){{return s+e.w.n;}},0);
    var visWfrs=wfrs.filter(function(e){{return _cbddPass(e.w);}});
    var visDies=visWfrs.reduce(function(s,e){{return s+e.w.n;}},0);
    var filtActive=visWfrs.length!==wfrs.length;
    var hdrCount=filtActive
      ?'('+visWfrs.length+'/'+wfrs.length+' wafers, '+visDies.toLocaleString()+'/'+totalDies.toLocaleString()+' dies — filtered)'
      :'('+wfrs.length+' wafers, '+totalDies.toLocaleString()+' dies)';
    rows.push('<tr class="lot-hdr" onclick="_toggleLot(\\''+_escStr(mat)+'\\')">'+'<td colspan="20" style="padding:3px 6px">'+'<input type="checkbox" id="'+cbId+'" '+(lotSel?'checked':'')+' onclick="event.stopPropagation();_toggleGrpSel(\\''+_escStr(mat)+'\\')" style="vertical-align:middle;margin-right:4px;width:13px;height:13px;cursor:pointer;accent-color:#3498db">'+(collapsed?'&#9658;':'&#9660;')+' '+_escH(mat)+' <span style="font-weight:normal;font-size:10px;'+(filtActive?'color:#f39c12;':'opacity:.7')+'">'+hdrCount+'</span></td></tr>');
    if(lotPart)setTimeout((function(id){{return function(){{var el=document.getElementById(id);if(el)el.indeterminate=true;}}}})(cbId),0);
    wfrs.forEach(function(e){{
      var w=e.w; var i=e.i;
      var hidden=collapsed;
      if(!_cbddPass(w)) hidden=true;
      if(_SHOW_SEL&&!SEL_WFR.has(i))hidden=true;
      var sel=SEL_WFR.has(i);
      var tr='<tr class="wfr-row'+(sel?' sel':'')+(hidden?' wfr-hidden':'')
             +'" onclick="toggleWfr('+i+')" data-wi="'+i+'">';
      if(showSortLot)tr+='<td title="'+_escH(w.sort_lot||'')+'">'+_escH(w.sort_lot||'')+'</td>';
      if(showProg)tr+='<td title="'+_escH(w.prog6248||'')+'">'+_escH(w.prog6248||'')+'</td>';
      tr+='<td title="'+_escH(w.wafer)+'">'+_escH(w.wafer)+'</td>';
      if(showU1U5)tr+='<td title="'+_escH(w.progU1U5||'')+'">'+_escH(w.progU1U5||'')+'</td>';
      if(showDevRev)tr+='<td title="'+_escH(w.dev_rev||'')+'">'+_escH(w.dev_rev||'')+'</td>';
      if(showX){{var _xv=w.xmin!=null?w.xmin+'..'+w.xmax:'';tr+='<td title="'+_xv+'">'+_xv+'</td>';}}
      if(showY){{var _yv=w.ymin!=null?w.ymin+'..'+w.ymax:'';tr+='<td title="'+_yv+'">'+_yv+'</td>';}}
      tr+='<td style="text-align:right" title="'+w.n.toLocaleString()+'">'+w.n.toLocaleString()+'</td></tr>';
      rows.push(tr);
    }});
  }});
  tbody.innerHTML=rows.join('');
  _initWfrTblResize();
}}

function _toggleGrpSel(mat){{
  var wfrs=[];
  WFR_DATA.forEach(function(w,i){{
    var m=w.material||(w.sort_lot?_lotMat(w.sort_lot):'Others');
    if(m===mat)wfrs.push(i);
  }});
  var allSel=wfrs.every(function(i){{return SEL_WFR.has(i);}});
  wfrs.forEach(function(i){{if(allSel)SEL_WFR.delete(i);else SEL_WFR.add(i);}});
  buildWfrList();rerender();
}}
function toggleWfr(i){{
  if(SEL_WFR.has(i))SEL_WFR.delete(i); else SEL_WFR.add(i);
  buildWfrList(); rerender();
}}
function selAll(){{WFR_DATA.forEach(function(_,i){{SEL_WFR.add(i);}});buildWfrList();rerender();}}
function clrAll(){{SEL_WFR.clear();buildWfrList();rerender();}}
function masterToggle(cb){{if(cb.checked)selAll();else clrAll();}}

/* ── Checkbox-dropdown filter data ──────────────────────────────────────── */
var _CBDD_FIELDS = {{
  lot:      {{wfr:'lot',      lbl:'Lot'}},
  wafer:    {{wfr:'wafer',    lbl:'Wafer'}},
  prog:     {{wfr:'progU1U5', lbl:'Prog', also:'prog6248'}},
  layout:   {{wfr:'dev_rev',  lbl:'Layout'}},
  prog6248: {{wfr:'prog6248', lbl:'Class Prog 6248'}},
}};
var _CBDD_SEL = {{lot:null,wafer:null,prog:null,layout:null,prog6248:null}};

// ── Init: populate dropdown filters then build the wafer list ────────────
window.addEventListener('DOMContentLoaded', function(){{
  WFR_DATA.forEach(function(_,i){{SEL_WFR.add(i);}});
  cbddBuild();
  buildWfrList(); rerender();
}});
function collapseAll(){{
  WFR_DATA.forEach(function(w){{
    var m=w.material||(w.sort_lot?_lotMat(w.sort_lot):'Others');
    _GRP_COLLAPSE[m]=true;
  }});
  buildWfrList();
}}
function toggleShowSel(){{
  _SHOW_SEL=!_SHOW_SEL;
  var btn=document.getElementById('show-sel-btn');
  btn.style.background=_SHOW_SEL?'#2980b9':'';
  btn.style.color=_SHOW_SEL?'#fff':'';
  buildWfrList();
}}
function onSearch(f,v){{
  _SEARCH[f]=v;
  buildWfrList();
  // Any filter change may affect the freq/bin matrix (all filters are now
  // checked by _allActiveKeys), so always rerender the active tab.
  rerender();
}}

/* ── Checkbox-dropdown filter logic ──────────────────────────────────────── */
function _cbddWfrKey(field, w){{
  var f = _CBDD_FIELDS[field];
  if(!f) return '';
  var v = w[f.wfr] || '';
  return String(v);
}}
function _cbddWfrKeyAlt(field, w){{
  var f = _CBDD_FIELDS[field];
  if(!f || !f.also) return null;
  return String(w[f.also] || '');
}}

function cbddBuild(){{
  var fields = Object.keys(_CBDD_FIELDS);
  fields.forEach(function(field){{
    var vals = [];
    var seen = {{}};
    WFR_DATA.forEach(function(w){{
      var v = _cbddWfrKey(field, w);
      if(v && !seen[v]){{ seen[v]=1; vals.push(v); }}
      var v2 = _cbddWfrKeyAlt(field, w);
      if(v2 && v2!==v && !seen[v2]){{ seen[v2]=1; vals.push(v2); }}
    }});
    vals.sort();
    var container = document.getElementById('dd-'+field+'-items');
    if(!container) return;
    container.innerHTML = '';
    vals.forEach(function(v){{
      var item = document.createElement('label');
      item.className = 'cbdd-item';
      var cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = true; cb.value = v;
      cb.onchange = function(){{ cbddApply(field); }};
      item.appendChild(cb);
      item.appendChild(document.createTextNode(v));
      container.appendChild(item);
    }});
    _CBDD_SEL[field] = null;  // null = all selected
    cbddUpdateLabel(field);
  }});
}}

function cbddApply(field){{
  var container = document.getElementById('dd-'+field+'-items');
  if(!container){{ _CBDD_SEL[field]=null; }}
  else{{
    var checks = container.querySelectorAll('input[type=checkbox]');
    var sel = [];
    checks.forEach(function(cb){{ if(cb.checked) sel.push(cb.value); }});
    _CBDD_SEL[field] = (sel.length === checks.length) ? null : new Set(sel);
  }}
  cbddUpdateLabel(field);
  buildWfrList();
  try{{ buildParamTable(); }}catch(e){{ _reportJsError('buildParamTable',e); }}
  rerender();
}}

function cbddAll(field){{
  var container = document.getElementById('dd-'+field+'-items');
  if(container) container.querySelectorAll('input').forEach(function(cb){{cb.checked=true;}});
  _CBDD_SEL[field] = null;
  cbddUpdateLabel(field);
  buildWfrList(); rerender();
}}
function cbddNone(field){{
  var container = document.getElementById('dd-'+field+'-items');
  if(container) container.querySelectorAll('input').forEach(function(cb){{cb.checked=false;}});
  _CBDD_SEL[field] = new Set();
  cbddUpdateLabel(field);
  buildWfrList(); rerender();
}}

function cbddUpdateLabel(field){{
  var el = document.getElementById('dd-'+field+'-lbl');
  if(!el) return;
  var f = _CBDD_FIELDS[field];
  var sel = _CBDD_SEL[field];
  if(!sel){{ el.textContent = f.lbl + ' (All) ▼'; el.style.color=''; return; }}
  var n = sel.size;
  if(n===0){{ el.textContent = f.lbl + ' (none) ▼'; el.style.color='#e74c3c'; return; }}
  var vals = Array.from(sel).slice(0,2).join(', ');
  if(sel.size>2) vals += '  +' + (sel.size-2) + ' more';
  el.textContent = vals + ' ▼';
  el.style.color = '#1a6aaa';
}}

var _cbddOpen = null;
function cbddToggle(field){{
  var panel = document.getElementById('dd-'+field+'-panel');
  if(!panel) return;
  if(_cbddOpen && _cbddOpen !== field){{
    var prev = document.getElementById('dd-'+_cbddOpen+'-panel');
    if(prev) prev.classList.remove('open');
  }}
  panel.classList.toggle('open');
  _cbddOpen = panel.classList.contains('open') ? field : null;
}}
document.addEventListener('click', function(e){{
  if(_cbddOpen && !e.target.closest('.cbdd')){{
    var panel = document.getElementById('dd-'+_cbddOpen+'-panel');
    if(panel) panel.classList.remove('open');
    _cbddOpen = null;
  }}
}});

function _cbddPass(w){{
  var fields = Object.keys(_CBDD_FIELDS);
  for(var i=0;i<fields.length;i++){{
    var field = fields[i];
    var sel = _CBDD_SEL[field];
    if(!sel) continue;  // null = all
    var v = _cbddWfrKey(field, w);
    var v2 = _cbddWfrKeyAlt(field, w);
    if(!sel.has(v) && (v2===null || !sel.has(v2))) return false;
  }}
  return true;
}}
function _toggleLot(mat){{
  _GRP_COLLAPSE[mat]=!_GRP_COLLAPSE[mat];
  buildWfrList();
}}
function _escStr(s){{return s.replace(/\\\\/g,'\\\\\\\\').replace(/'/g,"\\\\'");}}
function _escH(s){{
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

/* ── Panel 2 toggle ─────────────────────────────────────────────────────── */
function toggleP2(){{
  var p2=document.getElementById('panel2');
  var btn=document.getElementById('p2-toggle-btn');
  var hidden=p2.classList.toggle('p2-hidden');
  btn.innerHTML=hidden?'&#9654;':'&#9664;';
  if(!hidden)buildParamTable();
}}

/* ── Panel resize (P1) ──────────────────────────────────────────────────── */
(function(){{
  var handle=document.getElementById('p1-resize');
  var p1=document.getElementById('panel1');
  var dragging=false,startX,startW;
  handle.addEventListener('mousedown',function(e){{
    dragging=true;startX=e.clientX;startW=p1.offsetWidth;
    handle.classList.add('dragging');e.preventDefault();
  }});
  document.addEventListener('mousemove',function(e){{
    if(!dragging)return;
    var w=Math.max(120,startW+(e.clientX-startX));
    p1.style.width=w+'px';
  }});
  document.addEventListener('mouseup',function(){{
    if(dragging){{dragging=false;handle.classList.remove('dragging');}}
  }});
}})();

/* SP12 drag */
function startSplitSp12(e){{
  var p2=document.getElementById('panel2');
  var startX=e.clientX,startW=p2.offsetWidth;
  function mm(ev){{
    var w=Math.max(0,startW+(ev.clientX-startX));
    p2.style.width=w+'px';
    if(p2.classList.contains('p2-hidden')&&w>20)p2.classList.remove('p2-hidden');
  }}
  function mu(){{document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);}}
  document.addEventListener('mousemove',mm);document.addEventListener('mouseup',mu);
  e.preventDefault();
}}

/* SP23 drag */
function startSplitSp23(e){{
  var p2=document.getElementById('panel2');
  var startX=e.clientX,startW=p2.offsetWidth;
  function mm(ev){{
    var w=Math.max(160,startW+(ev.clientX-startX));
    p2.style.width=w+'px';
  }}
  function mu(){{document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);}}
  document.addEventListener('mousemove',mm);document.addEventListener('mouseup',mu);
  e.preventDefault();
}}

/* ── Group-by ────────────────────────────────────────────────────────────── */
function setGby(field,checked){{
  if(field==='none'){{
    if(checked){{_GBY=[];['lot','wafer'].forEach(function(f){{
      var el=document.getElementById('gby-'+f);if(el)el.checked=false;
    }});}}
  }} else {{
    if(checked){{
      if(_GBY.indexOf(field)<0)_GBY.push(field);
      var ne=document.getElementById('gby-none');if(ne)ne.checked=false;
    }} else {{
      var idx=_GBY.indexOf(field);if(idx>=0)_GBY.splice(idx,1);
      if(!_GBY.length){{var ne2=document.getElementById('gby-none');if(ne2)ne2.checked=true;}}
    }}
  }}
  rerender();
}}

function rerender(){{
  try {{
    updateInfoBar();
    drawAllCharts();
    buildParamTable();
    var _dtp=document.getElementById('tab-dist');
    if(_dtp && _dtp.classList.contains('active')) buildDistTab();
    var _active=document.querySelector('.tab-panel.active');
    if(_active && _active.id){{
      var _xym=_active.id.match(/^tab-xy(\\d+)$/);
      if(_xym) buildXYTab(+_xym[1]);
    }}
    var _ftp=document.getElementById('tab-flow');
    if(_ftp && _ftp.classList.contains('active')){{
      buildFlowTab();
      renderBinMatrix();
    }}
    var _fmOvl=document.getElementById('fm-overlay');
    if(_fmOvl&&_fmOvl.style.display!=='none'&&_XY_ACTIVE_REBUILD){{
      if(_XY_ACTIVE_CID&&_XY_STATE[_XY_ACTIVE_CID]){{
        var _cs=_XY_STATE[_XY_ACTIVE_CID];
        _XY_CARRY_STATE={{groupByMat:!!_cs.groupByMat,groupByProg:!!_cs.groupByProg}};
      }}
      _XY_ACTIVE_REBUILD();
    }}
    /* Refresh distribution modal chart if it's currently open */
    var _pmOvl=document.getElementById('pm-overlay');
    if(_pmOvl&&_pmOvl.style.display!=='none'){{
      var _pmTi=document.getElementById('pm-title');
      if(_pmTi&&_pmTi._param){{
        var _p1=document.getElementById('panel1');
        if(_p1){{_pmOvl.style.left=(_p1.offsetLeft+_p1.offsetWidth)+'px';}}
        _buildParamModalChart(_pmTi._param);
      }}
    }}
  }} catch(e){{
    _reportJsError('rerender', e);
  }}
}}

function updateInfoBar(){{
  var ak=activeKeys();
  var n=0;
  PCM_ROWS.forEach(function(r){{if(ak.has(_rKey(r))&&PCM_ROWS.length&&r.param===PCM_ROWS[0].param)n+=r.n;}});
  document.getElementById('ib-sel').textContent=
    SEL_WFR.size===WFR_DATA.length?'':
    '('+SEL_WFR.size+' wafers selected)';
}}

/* ── Variability strip chart ─────────────────────────────────────────────── */
function drawAllCharts(){{
  if(_DRAW_PENDING){{cancelAnimationFrame(_DRAW_PENDING);_DRAW_PENDING=null;}}
  var ak=activeKeys();
  var cm=_cMap();
  var gi=0;
  var queue=PCM_GROUPS.slice();
  function _next(){{
    if(!queue.length){{_DRAW_PENDING=null;return;}}
    var grp=queue.shift();
    var gid=grp.replace(/[^a-zA-Z0-9]/g,'_');
    var svgEl=document.getElementById('svg-grp-'+gid);
    var card=document.getElementById('card-grp-'+gid);
    if(!svgEl){{gi++;_DRAW_PENDING=requestAnimationFrame(_next);return;}}
    if(!_GRP_VIS[grp]){{
      if(card)card.style.display='none';
      gi++;_DRAW_PENDING=requestAnimationFrame(_next);return;
    }}
    if(card)card.style.display='';
    var params=PCM_ROWS
      .filter(function(r){{return r.group===grp;}})
      .reduce(function(a,r){{if(a.indexOf(r.param)<0)a.push(r.param);return a;}},
              []);
    var cnt=document.getElementById('card-grp-'+gid+'-cnt');
    if(cnt)cnt.textContent='('+params.length+' params)';
    _drawGroupChart(svgEl,grp,gi,params,ak,cm);
    gi++;
    _DRAW_PENDING=requestAnimationFrame(_next);
  }}
  _DRAW_PENDING=requestAnimationFrame(_next);
}}

function _drawGroupChart(svgEl,grp,gi,params,ak,cm){{
  var gid=grp.replace(/[^a-zA-Z0-9]/g,'_');
  if(!params||!params.length){{svgEl.style.display='none';return;}}
  svgEl.style.display='block';
  var W=Math.max(svgEl.parentElement?svgEl.parentElement.clientWidth-8:700,300);
  var ML=90,MR=80,MT=32,MB=8;
  var xStep=Math.max(32,(W-ML-MR)/params.length);
  var CW=xStep*params.length;
  var CH=_CHART_H;
  var xLblH=Math.max(120,Math.min(260,params.reduce(function(mx,p){{
    return Math.max(mx,p.length);}},0)*8+20));
  var H=MT+CH+xLblH+MB;

  function xPos(i){{return ML+(i+0.5)*xStep;}}
  // Compute Y range
  var allVals=[];
  PCM_ROWS.forEach(function(r){{
    if(params.indexOf(r.param)<0)return;
    if(!ak.has(_rKey(r)))return;
    (_VAR_PER_SITE?r.die_values:[r.median]).forEach(function(v){{
      if(v!=null&&isFinite(v))allVals.push(v);
    }});
  }});
  var ylo,yhi;
  if(allVals.length>=2){{
    var srt=allVals.slice().sort(function(a,b){{return a-b;}});
    var p01=srt[Math.floor(srt.length*0.01)];
    var p99=srt[Math.min(srt.length-1,Math.ceil(srt.length*0.99))];
    var dr=p99-p01||Math.abs(p01)*0.1||0.1;
    var pad=dr*0.15,ns=_niceStep(dr);
    ylo=Math.floor((p01-pad)/ns)*ns;
    yhi=Math.ceil((p99+pad)/ns)*ns;
  }} else {{ylo=0;yhi=1;}}
  function yPos(v){{return MT+(1-(v-ylo)/(yhi-ylo))*CH;}}

  var p=[];
  p.push('<svg id="svg-grp-'+gid+'" viewBox="0 0 '+(ML+CW+MR)+' '+H+'" width="100%" height="'+H+'" xmlns="http://www.w3.org/2000/svg">');  // keep id for re-query
  p.push('<rect width="'+(ML+CW+MR)+'" height="'+H+'" fill="#f8f9fa"/>');
  p.push('<rect x="'+ML+'" y="'+MT+'" width="'+CW+'" height="'+CH+'" fill="white" stroke="#ccc" stroke-width="1"/>');
  // Y grid
  var yStep2=_niceStep((yhi-ylo)/5);
  var yStart2=Math.ceil(ylo/yStep2)*yStep2;
  for(var yi2=0;yi2<60;yi2++){{
    var yv2=yStart2+yi2*yStep2;
    if(yv2>yhi+yStep2*0.01)break;
    var yp2=+yPos(yv2).toFixed(1);
    p.push('<line x1="'+ML+'" y1="'+yp2+'" x2="'+(ML+CW)+'" y2="'+yp2+'" stroke="rgba(0,0,0,.07)" stroke-width=".7"/>');
    p.push('<text x="'+(ML-3)+'" y="'+yp2+'" text-anchor="end" dominant-baseline="middle" font-size="14" font-weight="bold" fill="#111">'+_fmt(yv2)+'</text>');
  }}
  p.push('<text transform="translate(16,'+(MT+CH/2)+') rotate(-90)" text-anchor="middle" dominant-baseline="middle" font-size="16" font-weight="bold" fill="#111">Value</text>');

  // Dots batched by colour
  var dotPaths={{}};
  params.forEach(function(param,i){{
    var xi=xPos(i);
    // Alternating column tint
    if(i%2)p.push('<rect x="'+(xi-xStep/2).toFixed(1)+'" y="'+MT+'" width="'+xStep.toFixed(1)+'" height="'+CH+'" fill="rgba(0,0,0,.02)"/>');
    // Selected highlight
    if(SEL_PARAM===param)p.push('<rect x="'+(xi-xStep/2).toFixed(1)+'" y="'+MT+'" width="'+xStep.toFixed(1)+'" height="'+CH+'" fill="rgba(52,152,219,.10)" stroke="#3498db" stroke-width="1.2"/>');
    // Spec lines
    var meta=PCM_PARAM_META[param]||{{}};
    if(meta.lsl!=null){{var yL=+yPos(meta.lsl).toFixed(1);if(yL>=MT&&yL<=MT+CH)p.push('<line x1="'+(xi-xStep*.45).toFixed(1)+'" y1="'+yL+'" x2="'+(xi+xStep*.45).toFixed(1)+'" y2="'+yL+'" stroke="#c0392b" stroke-width="1.5" stroke-dasharray="4,3" opacity=".85"/>');}}
    if(meta.usl!=null){{var yU=+yPos(meta.usl).toFixed(1);if(yU>=MT&&yU<=MT+CH)p.push('<line x1="'+(xi-xStep*.45).toFixed(1)+'" y1="'+yU+'" x2="'+(xi+xStep*.45).toFixed(1)+'" y2="'+yU+'" stroke="#2980b9" stroke-width="1.5" stroke-dasharray="4,3" opacity=".85"/>');}}

    // Collect dots
    var colDots=[];
    PCM_ROWS.forEach(function(r){{
      if(r.param!==param||!ak.has(_rKey(r)))return;
      var col=(_GBY.length>0?cm.map[_grpKey(r)]:null)||'#3498db';
      var vals=_VAR_PER_SITE?r.die_values:[r.median];
      vals.forEach(function(v,vi){{
        if(v==null||!isFinite(v))return;
        var yp3=+yPos(v).toFixed(1);
        if(yp3<MT||yp3>MT+CH)return;
        colDots.push({{col:col,ri:r.n,vi:vi,yp:yp3}});
      }});
    }});
    // Subsample
    var MAX=500;
    if(colDots.length>MAX){{
      var step=colDots.length/MAX;
      var smp=[];
      for(var si=0;si<MAX;si++)smp.push(colDots[Math.floor(si*step)]);
      colDots=smp;
    }}
    colDots.forEach(function(d){{
      var jitter=(_sRand(d.ri*997+d.vi)-.5)*xStep*.52;
      var cx=+(xi+jitter).toFixed(1);
      var cy=d.yp;
      if(!dotPaths[d.col])dotPaths[d.col]='';
      dotPaths[d.col]+='M'+cx+','+cy+'m-2.5,0a2.5,2.5,0,1,0,5,0a2.5,2.5,0,1,0,-5,0';
    }});
  }});
  // Emit dot paths
  Object.keys(dotPaths).forEach(function(col){{
    p.push('<path d="'+dotPaths[col]+'" fill="'+col+'" opacity=".70"/>');
  }});
  // Median diamonds (on top)
  params.forEach(function(param,i){{
    var xi=xPos(i);
    var vals2=[];
    PCM_ROWS.forEach(function(r){{
      if(r.param!==param||!ak.has(_rKey(r)))return;
      (_VAR_PER_SITE?r.die_values:[r.median]).forEach(function(v){{
        if(v!=null&&isFinite(v))vals2.push(v);
      }});
    }});
    var med2=_med(vals2);
    if(med2!=null){{
      var yp4=+yPos(med2).toFixed(1);
      if(yp4>=MT&&yp4<=MT+CH)
        p.push('<polygon points="'+xi.toFixed(1)+','+(yp4-7)+' '+(xi+7).toFixed(1)+','+yp4+' '+xi.toFixed(1)+','+(yp4+7)+' '+(xi-7).toFixed(1)+','+yp4+'" fill="#27ae60" stroke="#1a6e2b" stroke-width="1.2" opacity=".92"/>');
    }}
    // X label
    var _lblFull=((PCM_PARAM_META[param]||{{}}).name||param);
    var lbl=_lblFull.length>32?_lblFull.slice(0,31)+'\u2026':_lblFull;
    p.push('<text transform="translate('+xPos(i).toFixed(1)+','+(MT+CH+4)+') rotate(-48)" text-anchor="end" font-size="16" font-weight="bold" fill="#111" title="'+_escH(param)+'">'+_escH(lbl)+'</text>');
  }});
  // Axes
  p.push('<line x1="'+ML+'" y1="'+MT+'" x2="'+ML+'" y2="'+(MT+CH)+'" stroke="#aaa" stroke-width="1"/>');
  p.push('<line x1="'+ML+'" y1="'+(MT+CH)+'" x2="'+(ML+CW)+'" y2="'+(MT+CH)+'" stroke="#aaa" stroke-width="1"/>');
  p.push('</svg>');
  svgEl.outerHTML=p.join('');

  // Legend
  var card=document.getElementById('card-grp-'+grp.replace(/[^a-zA-Z0-9]/g,'_'));
  if(card){{
    var legDiv=card.querySelector('.grp-legend');
    if(legDiv){{
      if(cm.keys.length>0){{
        legDiv.innerHTML=cm.keys.map(function(k,ki){{
          return '<span style="display:inline-flex;align-items:center;gap:4px;margin:2px 8px;font-size:10px">'+
            '<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:'+_COLOUR_PAL[ki%_COLOUR_PAL.length]+'"></span>'+
            _escH(k)+'</span>';
        }}).join('');
      }} else legDiv.innerHTML='';
    }}
  }}
}}

function downloadGrpCSV(grp){{
  var ak=activeKeys();
  var params=PCM_ROWS.filter(function(r){{return r.group===grp;}})
    .reduce(function(a,r){{if(a.indexOf(r.param)<0)a.push(r.param);return a;}},[]);
  var lines=['lot,wafer,param,n,median,std,cv,min,max'];
  PCM_ROWS.forEach(function(r){{
    if(r.group!==grp||!ak.has(_rKey(r)))return;
    lines.push([r.lot,r.wafer,r.param,r.n,_fmt(r.median),_fmt(r.std),
      r.cv!=null?_fmt(r.cv):'',_fmt(r.min_val),_fmt(r.max_val)].join(','));
  }});
  _dlCSV(grp.replace(/[^a-zA-Z0-9]/g,'_')+'_var.csv',lines.join('\\n'));
}}

function downloadVarCSV(){{
  var ak=activeKeys();
  var lines=['lot,wafer,group,param,n,median,std,cv,min,max'];
  PCM_ROWS.forEach(function(r){{
    if(!ak.has(_rKey(r)))return;
    lines.push([r.lot,r.wafer,r.group,r.param,r.n,
      _fmt(r.median),_fmt(r.std),r.cv!=null?_fmt(r.cv):'',
      _fmt(r.min_val),_fmt(r.max_val)].join(','));
  }});
  _dlCSV('class_var.csv',lines.join('\\n'));
}}

function _dlCSV(fn,txt){{
  var a=document.createElement('a');
  a.href='data:text/csv;charset=utf-8,'+encodeURIComponent(txt);
  a.download=fn;a.click();
}}

/* ── Parameter table (Panel 2) ──────────────────────────────────────────── */
function buildParamTable(){{
  var p2=document.getElementById('panel2');
  if(!p2||p2.classList.contains('p2-hidden'))return;
  var ak=activeKeys();
  // Compute per-param stats across selected wafers
  var pStats={{}};
  PCM_ROWS.forEach(function(r){{
    if(!ak.has(_rKey(r)))return;
    if(!pStats[r.param])pStats[r.param]={{n:0,vals:[],group:r.group}};
    var s=pStats[r.param];
    s.n+=r.n;
    (r.die_values||[]).forEach(function(v){{if(v!=null&&isFinite(v))s.vals.push(v);}});
  }});

  var thead=document.getElementById('var-head');
  var tbody=document.getElementById('var-body');
  if(!thead||!tbody)return;
  thead.innerHTML='<tr><th style="text-align:left;min-width:160px;position:sticky;left:0;z-index:2;background:#2c3e50">Parameter</th><th style="min-width:44px">N</th><th style="min-width:70px">Median</th><th style="min-width:58px">Target</th><th style="min-width:62px">Multiplier</th><th style="min-width:58px">Min</th><th style="min-width:58px">Max</th><th style="min-width:86px">UPM Med (%)</th><th style="min-width:78px">Meas_Temp (C)</th><th style="min-width:50px">&sigma;</th><th style="min-width:50px">LSL</th><th style="min-width:50px">USL</th><th style="min-width:34px">Unit</th></tr>';

  function _medFromPStat(k){{
    var s = pStats[k];
    if(!s || !s.vals || !s.vals.length) return null;
    return _med(s.vals);
  }}

  var _upm950Key = null;
  Object.keys(pStats).some(function(k){{
    if(/UPM[ ]*107[_ -]*950/i.test(k)){{ _upm950Key = k; return true; }}
    return false;
  }});
  if(!_upm950Key){{
    Object.keys(UPM_LABELS||{{}}).some(function(k){{
      if(/107[_ -]*950/i.test(k)){{ _upm950Key = k; return true; }}
      return false;
    }});
  }}
  var _upmByWafer = {{}};
  if(_upm950Key) {{
    PCM_ROWS.forEach(function(r){{
      if(r.param === _upm950Key && ak.has(_rKey(r)))
        _upmByWafer[r.lot+'/'+r.wafer] = r.median;
    }});
  }}
  var _pWafers = {{}};
  PCM_ROWS.forEach(function(r){{
    if(!ak.has(_rKey(r))) return;
    if(!_pWafers[r.param]) _pWafers[r.param] = [];
    _pWafers[r.param].push(r.lot+'/'+r.wafer);
  }});

  var _tempByToken = SICC_CLASS_TEMP;  // keyed: CORE0/CORE1/CORE2/CORE3/ATOM0..ATOM3/RING

  function _tempMedForParam(param, grp){{
    if(!/SICC[ ]*Class/i.test(grp||'')) return '-';
    // param is like "CLASS SICC CORE0" / "CLASS SICC ATOM2" / "CLASS SICC RING"
    var m = String(param||'').match(/CLASS SICC (?:TEMP )?(CORE\\d|ATOM\\d|RING)/i);
    if(!m) return '-';
    var tok = m[1].toUpperCase();
    var tv = _tempByToken[tok];
    return tv==null ? '-' : _fmt(tv);
  }}

  var rows=[];
  var grpSeen={{}};
  PCM_GROUPS.forEach(function(grp){{
    var gparams=PCM_ROWS.reduce(function(a,r){{
      if(r.group===grp&&a.indexOf(r.param)<0)a.push(r.param);return a;}},[]);
    // sort _agg_ params by freq descending (handles different lot/wafer freq subsets)
    gparams.sort(function(a,b){{
      if(a.indexOf('_agg_')!==0||b.indexOf('_agg_')!==0)return 0;
      return parseInt(b.split('_').pop(),10)-parseInt(a.split('_').pop(),10);
    }});
    if(!gparams.length)return;
    if(!grpSeen[grp]){{
      grpSeen[grp]=true;
      var collapsed=_GRP_ROW_COLLAPSE[grp];
      rows.push('<tr class="cat-hdr" onclick="toggleGrpRow(\\''+_escStr(grp)+'\\')"><td colspan="12">'+(collapsed?'&#9658;':'&#9660;')+' '+_escH(grp)+'<span style="font-weight:normal;font-size:10px;color:#aed6f1"> ('+gparams.length+')</span></td></tr>');
    }}
    var collapsed2=_GRP_ROW_COLLAPSE[grp];
    gparams.forEach(function(param){{
      var s=pStats[param];
      var meta=PCM_PARAM_META[param]||{{}};
      var isSel=SEL_PARAM===param;
      var cls=(isSel?'sel-row':'')+(collapsed2?' grp-hidden':'');
      if(!s){{
        rows.push('<tr class="'+cls+'" onclick="selParam(\\''+_escStr(param)+'\\')"><td class="tn" title="'+_escH(param)+'">'+_escH(meta.name||param)+'</td><td colspan="11" style="color:#aaa;font-style:italic">no data</td></tr>');
        return;
      }}
      var vals3=s.vals;
      var med3=_med(vals3); var n3=s.n;
      var std3=vals3.length>1?(function(){{var m2=vals3.reduce(function(a,v){{return a+v;}},0)/vals3.length;return Math.sqrt(vals3.reduce(function(a,v){{return a+(v-m2)*(v-m2);}},0)/(vals3.length-1));}})():0;
      var min3=Math.min.apply(null,vals3); var max3=Math.max.apply(null,vals3);
      var _puv = [];
      if(_upm950Key && _pWafers[param]) {{ _pWafers[param].forEach(function(wk) {{ var v=_upmByWafer[wk]; if(v!=null) _puv.push(v); }}); }}
      var upmMed = /UPM/i.test(grp||'') ? '-' : (_puv.length ? _fmt(_med(_puv)) : '-');
      var tempMed = _tempMedForParam(param, grp);
      var targetVal = (meta.target!=null) ? _fmt(meta.target) : '';
      var multiplierVal = (meta.target!=null && meta.target!==0) ? (med3/meta.target*100).toFixed(2)+'%' : '';
      var medCls=(meta.lsl!=null&&med3<meta.lsl)||(meta.usl!=null&&med3>meta.usl)?' cell-r':'';
      rows.push('<tr class="'+cls+'" onclick="selParam(\\''+_escStr(param)+'\\')">'+
        '<td class="tn" title="'+_escH(param)+'">'+
        '<span onclick="event.stopPropagation();_showParamModal(\\''+_escStr(param)+'\\')" title="Show histogram" '+
        'style="cursor:pointer;margin-right:5px;font-size:11px;opacity:0.55" '+
        'onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=0.55">&#128202;</span>'+
        (param.indexOf('_agg_')===0 ? '<span onclick="event.stopPropagation();_showAggVminXY(\\''+_escStr(param)+'\\')"\
 title="XY scatter" style="cursor:pointer;margin-right:4px;font-size:12px;opacity:0.55"\
 onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=0.55">&#128200;</span>' : '')+
        _escH(meta.name||param)+'</td>'+
        '<td>'+n3+'</td>'+
        '<td class="'+medCls+'">'+_fmt(med3)+'</td>'+
        '<td>'+targetVal+'</td>'+
        '<td>'+multiplierVal+'</td>'+
        '<td>'+_fmt(min3)+'</td>'+
        '<td>'+_fmt(max3)+'</td>'+
        '<td>'+upmMed+'</td>'+
        '<td>'+tempMed+'</td>'+
        '<td>'+_fmt(std3)+'</td>'+
        '<td style="color:#c0392b">'+_fmt(meta.lsl)+'</td>'+
        '<td style="color:#2980b9">'+_fmt(meta.usl)+'</td>'+
        '<td style="color:#7f8c8d;font-size:10px">'+(meta.unit||'')+'</td>'+
        '</tr>');
    }});
  }});
  tbody.innerHTML=rows.join('');
}}

function toggleGrpRow(grp){{
  _GRP_ROW_COLLAPSE[grp]=!_GRP_ROW_COLLAPSE[grp];
  buildParamTable();
}}

function selParam(p){{
  SEL_PARAM=(SEL_PARAM===p)?null:p;
  buildParamTable();
  drawAllCharts();
  if(SEL_PARAM)_showParamModal(SEL_PARAM);
}}

/* ── Param detail modal ─────────────────────────────────────────────────── */
function _showParamModal(param){{
  var overlay=document.getElementById('pm-overlay');
  if(!overlay)return;
  /* Offset overlay so left panel (#panel1) remains interactive */
  var p1=document.getElementById('panel1');
  if(p1){{overlay.style.left=(p1.offsetLeft+p1.offsetWidth)+'px';}}
  var titleEl=document.getElementById('pm-title');
  var meta=PCM_PARAM_META[param]||{{}};
  if(titleEl){{titleEl.textContent=(meta.name||param)+(meta.name?' (— '+param+')':'');titleEl._param=param;}}
  _buildParamModalChart(param);
  overlay.style.display='flex';
}}
function _closeParamModal(){{
  var overlay=document.getElementById('pm-overlay');
  if(overlay)overlay.style.display='none';
  SEL_PARAM=null;
  buildParamTable();
  drawAllCharts();
}}
function _showTpPopup(tp){{
  var overlay=document.getElementById('pm-overlay');
  if(!overlay)return;
  var ti=(TP_INFO||{{}})[tp]||{{}};
  var esc=function(s){{s=String(s);s=s.split('&').join('&amp;');s=s.split('<').join('&lt;');s=s.split('>').join('&gt;');s=s.split('"').join('&quot;');return s;}};
  var rows='';
  if(ti.nick_name) rows+='<tr><td>Nick Name</td><td>'+esc(ti.nick_name)+'</td></tr>';
  if(ti.built_date) rows+='<tr><td>Built Date</td><td>'+esc(ti.built_date)+'</td></tr>';
  if(ti.tos_profile) rows+='<tr><td>TOS Profile</td><td>'+esc(ti.tos_profile)+'</td></tr>';
  if(ti.prime_path){{
    var pp=ti.prime_path,ki=pp.toLowerCase().indexOf('testprograms');
    if(ki>0)pp=pp.substring(ki);
    rows+='<tr><td>PRIME Path</td><td style="word-break:break-all">'+esc(pp)+'</td></tr>';
  }}
  if(ti.stepping) rows+='<tr><td>Stepping</td><td>'+esc(ti.stepping)+'</td></tr>';
  if(ti.products) rows+='<tr><td>Products</td><td>'+esc(ti.products)+'</td></tr>';
  if(ti.integrator) rows+='<tr><td>Integrator</td><td>'+esc(ti.integrator)+'</td></tr>';
  if(ti.classification) rows+='<tr><td>Classification</td><td>'+esc(ti.classification)+'</td></tr>';
  var fullBodyHtml='';
  if(ti.full_body){{
    var _fmtFb=function(s){{
      var H='https://',H2='http://',out='',i=0;
      while(i<s.length){{
        var ni=s.indexOf(H,i),n2=s.indexOf(H2,i);
        if(ni<0)ni=n2; else if(n2>=0&&n2<ni)ni=n2;
        if(ni<0){{out+=esc(s.substring(i));break;}}
        out+=esc(s.substring(i,ni));
        var e=ni;
        while(e<s.length&&s[e]!==' '&&s[e]!=='\\n'&&s[e]!=='\\t')e++;
        var url=s.substring(ni,e);
        out+='<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer" style="color:#4fc3f7">'+esc(url)+'</a>';
        i=e;
      }}
      return out;
    }};
    fullBodyHtml='<details open style="margin-top:8px"><summary style="cursor:pointer;font-weight:bold;font-size:11px;color:#aed6f1">&#128196; Full Build Report</summary>'
      +'<pre style="font-size:10px;max-height:420px;overflow-y:auto;background:#1a2530;color:#cdd5d9;padding:8px;border-radius:3px;margin:4px 0 0 0;white-space:pre-wrap;word-break:break-word">'+_fmtFb(ti.full_body)+'</pre></details>';
  }}
  var body;
  if(!rows&&!ti.skipped_modules&&!fullBodyHtml){{
    body='<div class="tp-card"><em>No build-report details loaded for this program.</em></div>';
  }}else{{
    var skipHtml='';
    if(ti.skipped_modules&&ti.skipped_modules.length){{
      skipHtml='<details style="margin-top:8px"><summary style="cursor:pointer;font-weight:bold">Skipped Modules ('+ti.skipped_modules.length+')</summary><ul style="margin:4px 0 0 16px;padding:0">';
      ti.skipped_modules.forEach(function(m){{skipHtml+='<li>'+esc(m)+'</li>';}});
      skipHtml+='</ul></details>';
    }}
    body='<div class="tp-card">'+(rows?'<table class="tp-tbl">'+rows+'</table>':'')+skipHtml+fullBodyHtml+'</div>';
  }}
  document.getElementById('pm-title').innerHTML='&#128196;&#160;'+esc(tp);
  document.getElementById('pm-body').innerHTML=body;
  overlay.style.display='flex';
}}
document.addEventListener('DOMContentLoaded',function(){{
  document.querySelectorAll('.sub .tp-link').forEach(function(btn){{
    var tp=btn.textContent,ti=(TP_INFO||{{}})[tp]||{{}};
    if(Object.keys(ti).length>0){{btn.classList.add('tp-has-info');}}
    else{{btn.classList.add('tp-no-info');btn.removeAttribute('onclick');}}
  }});
}});
document.addEventListener('keydown',function(e){{
  if(e.key==='Escape'){{_closeParamModal();_closeFlowModal();}}
}});

/* ── Speed Flow Tab ─────────────────────────────────────────────────────── */
var _FLOW_INST_PAL = ['#1a4a7a','#2e7d32','#6a1b9a','#bf360c','#00695c','#7b3f00','#283593','#880e4f'];
var _FLOW_CARD_W = {{}};
var _FLOW_MOD_ACTIVE = null;
var _FLOW_CHART_MOD_ACTIVE = null;
var _FLOW_UPM_PCT_RANGE = {{}};
var _FLOW_UPM_PCT_MIN = 75;
var _FLOW_UPM_PCT_MAX = 110;
var _FLOW_PASS_VMIN_RULE = {{}};
var _FLOW_PASS_AUTODOWNFLOW = {{}};  // per-module: {{mod: true/false}}
var _FLOW_SUMMARY_SHOW_TABLE = true;
var _FLOW_SUMMARY_OVERLAY_ALL = false;
var _FLOW_SUMMARY_PLOT_SEL = {{}};
var _FLOW_SUMMARY_CARD_VIS = {{}};
var _FLOW_SUMMARY_GROUP_MODE = 'module';
var _FLOW_SUMMARY_NORMALIZE_UPM = false;
var _FLOW_SUMMARY_NORMALIZE_UPM_PCT = 94.0;
var _FLOW_SUMMARY_PLOT_COLLAPSED = true;
var _FLOW_SUMMARY_PLOT_COLLAPSED = true;  // default minimized
var _FLOW_VF_FAM = {{}};
var _FLOW_VF_SER = {{}};
var _FLOW_CARDS_H = Math.round(window.innerHeight * 0.85);
var _FLOW_SUMMARY_W = 310;
var _FLOW_SUMMARY_H = 310;
var _FLOW_CARD_MIN_W = 440;
var _FLOW_CARD_DEF_W = 560;
var _FLOW_CARD_MAX_W = 1400;
var _FLOW_CARD_H     = {{}};
var _FLOW_CARD_MIN_H = 220;
var _FLOW_CARD_MAX_H = 3000;
var _FLOW_SUMMARY_XY_H = 460;
var _FLOW_SUMMARY_XY_W = 575;

function _flowSortMods(mods){{
  return (mods||[]).slice().sort(function(a,b){{
    function rk(m){{
      var s=(m||'').toLowerCase();
      if(s.indexOf('core')>=0) return 0;
      if(s.indexOf('atom')>=0) return 1;
      if(s.indexOf('ccf')>=0 || s.indexOf('ring')>=0) return 2;
      return 99;
    }}
    var da=rk(a), db=rk(b);
    if(da!==db) return da-db;
    return String(a).localeCompare(String(b));
  }});
}}

function _flowSubTabLabel(mod, fd){{
  var s=(mod||'').toLowerCase();
  if(s.indexOf('core')>=0) return 'CORE';
  if(s.indexOf('atom')>=0) return 'ATOM';
  if(s.indexOf('ccf')>=0 || s.indexOf('ring')>=0) return 'CCF';
  return String((fd&&fd.label)||mod||'').toUpperCase();
}}

function _filtUpmMed(rows, ak){{
  var fRows=(rows||[]).filter(function(r){{return ak.has(_flowNormKey(r));}});
  if(!fRows.length) return null;
  var _seen2=new Set();
  var uRows=fRows.filter(function(r){{
    var k=(r[8]!=null?String(r[8]):'')+'\u007c'+String(r[0]||'');
    if(_seen2.has(k))return false; _seen2.add(k); return true;
  }});
  var uv=uRows.map(function(r){{return r[6];}}).filter(function(v){{return v!==null&&v!==undefined&&v===v;}});
  return uv.length ? _med(uv) : null;
}}

function _flowProgSearch(q){{
  var s=q.toLowerCase();
  var list=document.getElementById('flow-prog-list');
  if(!list)return;
  list.querySelectorAll('[data-prog-row]').forEach(function(row){{
    var prog=(row.getAttribute('data-prog-row')||'').toLowerCase();
    row.style.display=(!s||prog.indexOf(s)>=0)?'':'none';
  }});
}}
function _flowProgDdToggle(){{
  var p=document.getElementById('flow-prog-dd-panel');
  if(!p)return;
  var open=p.style.display==='none';
  p.style.display=open?'block':'none';
  if(open){{
    setTimeout(function(){{
      document.addEventListener('click',function _flowProgDdClose(e){{
        var fp=document.getElementById('flow-prog-filter');
        if(!fp||!fp.contains(e.target)){{
          p.style.display='none';
          document.removeEventListener('click',_flowProgDdClose);
        }}
      }});
    }},0);
  }}
}}
function _flowProgUpdateLabel(){{
  var lbl=document.getElementById('flow-prog-dd-label');
  if(!lbl)return;
  var total=document.querySelectorAll('[id^=flow-lot-cb-]').length;
  var active=_FLOW_LOT_ACTIVE.size;
  if(active===0)lbl.textContent='None selected';
  else if(active>=total)lbl.textContent='All selected';
  else lbl.textContent=active+' / '+total+' selected';
}}
function _flowProgCbChange(cb){{
  var prog=cb.value;
  if(cb.checked){{
    _FLOW_PROG_ACTIVE.add(prog);
    _FLOW_PROG_USER_HIDDEN.delete(prog);
  }}else{{
    _FLOW_PROG_ACTIVE.delete(prog);
    _FLOW_PROG_USER_HIDDEN.add(prog);
  }}
  // sync all lots under this prog
  document.querySelectorAll('[id^=flow-lot-cb-]').forEach(function(c){{
    if(c.value.indexOf(prog+'\x00')===0){{
      c.checked=cb.checked;
      if(cb.checked)_FLOW_LOT_ACTIVE.add(c.value);else _FLOW_LOT_ACTIVE.delete(c.value);
    }}
  }});
  buildFlowTab();
  _flowProgUpdateLabel();
}}
function _flowLotCbChange(cb){{
  var key=cb.value; // "prog\x00lot"
  var sep=key.indexOf('\x00');
  var prog=sep>=0?key.substring(0,sep):key;
  if(cb.checked)_FLOW_LOT_ACTIVE.add(key);else _FLOW_LOT_ACTIVE.delete(key);
  // update prog checkbox state
  var lots=document.querySelectorAll('[id^=flow-lot-cb-]');
  var progLots=[]; lots.forEach(function(c){{if(c.value.indexOf(prog+'\x00')===0)progLots.push(c);}});
  var allOn=progLots.every(function(c){{return _FLOW_LOT_ACTIVE.has(c.value);}});
  var anyOn=progLots.some(function(c){{return _FLOW_LOT_ACTIVE.has(c.value);}});
  var pEl=document.getElementById('flow-prog-cb-'+prog.replace(/[^a-zA-Z0-9]/g,'_'));
  if(pEl){{pEl.checked=allOn;pEl.indeterminate=anyOn&&!allOn;}}
  if(allOn){{
    _FLOW_PROG_ACTIVE.add(prog);
    _FLOW_PROG_USER_HIDDEN.delete(prog);
  }}else if(!anyOn){{
    _FLOW_PROG_ACTIVE.delete(prog);
    _FLOW_PROG_USER_HIDDEN.add(prog);
  }}
  buildFlowTab();
  _flowProgUpdateLabel();
}}
function _flowProgSelectAll(checked){{
  document.querySelectorAll('[id^=flow-prog-cb-]').forEach(function(c){{
    c.checked=checked;c.indeterminate=false;
    if(checked){{
      _FLOW_PROG_ACTIVE.add(c.value);
      _FLOW_PROG_USER_HIDDEN.delete(c.value);
    }}else{{
      _FLOW_PROG_ACTIVE.delete(c.value);
      _FLOW_PROG_USER_HIDDEN.add(c.value);
    }}
  }});
  document.querySelectorAll('[id^=flow-lot-cb-]').forEach(function(c){{
    c.checked=checked;
    if(checked)_FLOW_LOT_ACTIVE.add(c.value);else _FLOW_LOT_ACTIVE.delete(c.value);
  }});
  buildFlowTab();
  _flowProgUpdateLabel();
}}

function _showFlowMod(mod){{
  _FLOW_MOD_ACTIVE=mod;
  Object.keys(FLOW_DATA||{{}}).forEach(function(m){{
    var idSafe=String(m||'').replace(/[^a-zA-Z0-9_-]/g,'_');
    var p=document.getElementById('flow-mod-'+idSafe);
    var b=document.getElementById('flow-mod-btn-'+idSafe);
    if(p) p.style.display=(m===mod)?'block':'none';
    if(b){{
      b.style.background=(m===mod)?'#1a4a7a':'#d0e4f8';
      b.style.color=(m===mod)?'#fff':'#1a4a7a';
    }}
  }});
}}

function _flowSafeId(s){{
  return String(s||'').replace(/[^a-zA-Z0-9_-]/g,'_');
}}

function _flowInlineArg(s){{
  var bs = String.fromCharCode(92);
  return String(s||'').split(bs).join(bs+bs).replace(/'/g,"\\'");
}}

function _flowCardWidth(mod){{
  var w = Number(_FLOW_CARD_W[mod]);
  if(!(w>0)) w = _FLOW_CARD_DEF_W;
  if(w < _FLOW_CARD_MIN_W) w = _FLOW_CARD_MIN_W;
  if(w > _FLOW_CARD_MAX_W) w = _FLOW_CARD_MAX_W;
  return w;
}}

function _flowApplyCardWidth(mod, w){{
  var ww = Math.max(_FLOW_CARD_MIN_W, Math.min(_FLOW_CARD_MAX_W, Number(w)||_FLOW_CARD_DEF_W));
  _FLOW_CARD_W[mod] = ww;
  var el = document.getElementById('flow-mod-'+_flowSafeId(mod));
  if(!el) return;
  el.style.flex = '0 0 auto';
  el.style.width = ww + 'px';
}}

function _flowStartCardResize(e, mod){{
  var el = document.getElementById('flow-mod-'+_flowSafeId(mod));
  if(!el) return;
  var r = el.getBoundingClientRect();
  var startX=e.clientX, startY=e.clientY, startW=r.width, startH=r.height;
  var body = document.body;
  if(body) body.style.cursor = 'nwse-resize';

  function mm(ev){{
    var nw = Math.max(_FLOW_CARD_MIN_W, Math.min(_FLOW_CARD_MAX_W, startW + ev.clientX - startX));
    var nh = Math.max(_FLOW_CARD_MIN_H, Math.min(_FLOW_CARD_MAX_H, startH + ev.clientY - startY));
    _FLOW_CARD_W[mod] = nw; _FLOW_CARD_H[mod] = nh;
    el.style.width = nw + 'px'; el.style.height = nh + 'px';
  }}
  function mu(){{
    document.removeEventListener('mousemove', mm);
    document.removeEventListener('mouseup', mu);
    if(body) body.style.cursor = '';
  }}

  document.addEventListener('mousemove', mm);
  document.addEventListener('mouseup', mu);
  e.preventDefault();
}}

function _flowFreqMedianPoints(mod, ak, upmLo, upmHi, applyUpmPct){{
  var fd = FLOW_DATA[mod] || {{}};
  var byFM = {{}}; // key = fmhz|material -> [vmins]
  var doNorm = _FLOW_SUMMARY_NORMALIZE_UPM;
  var normTarget = _FLOW_SUMMARY_NORMALIZE_UPM_PCT;

  // --- per-material OLS slopes from actual measured rows (data-driven correction) ---
  // Collect (upm, vmin) pairs per material across all instances/freqs in ak, then
  // compute slope via OLS. For materials with n < 15 points fall back to pooled slope.
  // This ensures the summary chart reflects material-specific Vmin vs UPM sensitivity,
  // e.g. BLLC sitting ~20 mV above R0 at equivalent UPM will remain visible after norm.
  var _matSlopes = {{}};  // mat -> slope (V per UPM%)
  if(doNorm){{
    var _rawUpm = {{}};  // mat -> [{{u, v}}]
    (fd.instances||[]).forEach(function(inst){{
      (inst.freqs||[]).forEach(function(fr){{
        (fr.rows||[]).forEach(function(r){{
          if(!ak.has(_flowNormKey(r))) return;
          var u = r[6], v = r[5], m = r[7] || _lotMat(r[1]) || 'Others';
          if(u===null||u===undefined||u!==u||v===null||v===undefined||v!==v) return;
          if(!_rawUpm[m]) _rawUpm[m] = [];
          _rawUpm[m].push({{u:+u, v:+v}});
        }});
      }});
    }});
    // OLS helper: returns slope from pairs array
    var _ols = function(pairs){{
      if(!pairs||pairs.length<2) return null;
      var n=pairs.length, sx=0, sy=0, sxx=0, sxy=0;
      pairs.forEach(function(p){{sx+=p.u;sy+=p.v;sxx+=p.u*p.u;sxy+=p.u*p.v;}});
      var denom=n*sxx-sx*sx;
      return denom===0?null:(n*sxy-sx*sy)/denom;
    }};
    // pooled slope across all materials
    var _allPairs = [];
    Object.keys(_rawUpm).forEach(function(m){{_allPairs=_allPairs.concat(_rawUpm[m]);}});
    var _pooledSlope = _ols(_allPairs);
    // assign per-material slope; fall back to pooled if n < 15
    Object.keys(_rawUpm).forEach(function(m){{
      var pairs = _rawUpm[m];
      var s = (pairs.length >= 15) ? _ols(pairs) : null;
      _matSlopes[m] = (s!==null) ? s : (_pooledSlope!==null ? _pooledSlope : 0);
    }});
  }}

  (fd.instances||[]).forEach(function(inst){{
    (inst.freqs||[]).forEach(function(fr){{
      var fmhz = fr.freq_mhz;
      (fr.rows||[]).forEach(function(r){{
        if(!ak.has(_flowNormKey(r))) return;
        if(applyUpmPct){{
          var up = r[6];
          if(up===null || up===undefined || up!==up) return;
          if(up < upmLo || up > upmHi) return;
        }}
        var vv = r[5];
        if(vv===null || vv===undefined || vv!==vv) return;
        if(doNorm){{
          var upmAct = r[6];
          if(upmAct!==null && upmAct!==undefined && upmAct===upmAct){{
            // Data-driven slope correction: shift each unit's Vmin to what it
            // would be at normTarget UPM%, using the per-material measured slope.
            // Vmin_adj = Vmin + slope * (normTarget - upmAct)
            var rowMat = r[7] || _lotMat(r[1]) || 'Others';
            var slope = (_matSlopes[rowMat]!==undefined) ? _matSlopes[rowMat] : 0;
            vv = vv + slope * (normTarget - upmAct);
          }}
        }}
        var mat = r[7] || _lotMat(r[1]);
        var k = String(fmhz) + '|' + String(mat||'Others');
        if(!byFM[k]) byFM[k] = [];
        byFM[k].push(+vv);
      }});
    }});
  }});
  var pts = [];
  var mats = [];
  Object.keys(byFM).forEach(function(k){{
    var vals = byFM[k];
    if(!vals || !vals.length) return;
    var p = k.split('|');
    var fmhz = parseFloat(p[0]);
    var mat = p.slice(1).join('|');
    var med = _med(vals);
    if(med===null || med===undefined || med!==med || !isFinite(fmhz)) return;
    pts.push([fmhz / 1000.0, med]);
    mats.push(mat || 'Others');
  }});
  return {{pts:pts, mats:mats}};
}}

function _flowSetUpmPctBound(mod, which, val){{
  var st = _FLOW_UPM_PCT_RANGE[mod] || {{lo:_FLOW_UPM_PCT_MIN, hi:_FLOW_UPM_PCT_MAX}};
  var v = +val;
  if(!(v===v)) return;
  if(which==='lo') st.lo = Math.max(_FLOW_UPM_PCT_MIN, Math.min(_FLOW_UPM_PCT_MAX, v));
  else st.hi = Math.max(_FLOW_UPM_PCT_MIN, Math.min(_FLOW_UPM_PCT_MAX, v));
  if(st.lo > st.hi){{
    if(which==='lo') st.hi = st.lo;
    else st.lo = st.hi;
  }}
  _FLOW_UPM_PCT_RANGE[mod] = st;
}}

function _flowApplyUpmPctBounds(mod){{
  var idSafe = _flowSafeId(mod);
  var loEl = document.getElementById('flow-upm-lo-' + idSafe);
  var hiEl = document.getElementById('flow-upm-hi-' + idSafe);
  if(!loEl || !hiEl) return;
  _flowSetUpmPctBound(mod, 'lo', loEl.value);
  _flowSetUpmPctBound(mod, 'hi', hiEl.value);
  var st = _FLOW_UPM_PCT_RANGE[mod] || {{lo:_FLOW_UPM_PCT_MIN, hi:_FLOW_UPM_PCT_MAX}};
  loEl.value = _fmtUpm(st.lo);
  hiEl.value = _fmtUpm(st.hi);
  _renderFlowFreqMedianXY(mod);
}}

function _flowUpmPctControlsHtml(mod, st){{
  var idSafe = _flowSafeId(mod);
  var modArg = _flowInlineArg(mod);
  return '<div style="padding:8px 10px;border:1px solid #dbe7f4;border-radius:6px;background:#f8fbff;margin-bottom:8px">'+
    '<div style="font-size:11px;color:#2c3e50;margin-bottom:6px"><b>UPM % constraint</b> (reference: UPM 107_950)</div>'+
    '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'+
    '<label style="font-size:11px;color:#2c3e50">Min %</label>'+
    '<input id="flow-upm-lo-'+idSafe+'" type="number" min="'+_FLOW_UPM_PCT_MIN+'" max="'+_FLOW_UPM_PCT_MAX+'" step="0.1" value="'+_fmtUpm(st.lo)+'" style="width:82px;padding:2px 4px">'+
    '<label style="font-size:11px;color:#2c3e50">Max %</label>'+
    '<input id="flow-upm-hi-'+idSafe+'" type="number" min="'+_FLOW_UPM_PCT_MIN+'" max="'+_FLOW_UPM_PCT_MAX+'" step="0.1" value="'+_fmtUpm(st.hi)+'" style="width:82px;padding:2px 4px">'+
    '<button onclick="_flowApplyUpmPctBounds(\\''+modArg+'\\')" style="padding:3px 10px;border:1px solid #98b2d2;border-radius:4px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">Apply</button>'+
    '<span style="font-size:11px;color:#5d6d7e">Applied window: '+_fmtUpm(st.lo)+'% to '+_fmtUpm(st.hi)+'%</span>'+
    '</div></div>';
}}

function _flowPassVminState(mod){{
  var st = _FLOW_PASS_VMIN_RULE[mod] || {{enabled:false, thresh:1.25}};
  return {{enabled: !!st.enabled, thresh: (st.thresh===st.thresh ? st.thresh : 1.25)}};
}}

function _flowPassVminApply(mod){{
  var idSafe = _flowSafeId(mod);
  var cb = document.getElementById('flow-pass-vmin-enable-'+idSafe);
  var tx = document.getElementById('flow-pass-vmin-thresh-'+idSafe);
  var st = _flowPassVminState(mod);
  if(cb) st.enabled = !!cb.checked;
  if(tx){{
    var v = parseFloat(tx.value);
    if(v===v) st.thresh = v;
    tx.value = st.thresh.toFixed(2);
  }}
  _FLOW_PASS_VMIN_RULE[mod] = st;
  buildFlowTab();
}}

function _flowPassVminControlsHtml(mod){{
  var idSafe = _flowSafeId(mod);
  var modArg = _flowInlineArg(mod);
  var st = _flowPassVminState(mod);
  return '<div style="padding:8px 10px;border:1px solid #dbe7f4;border-radius:6px;background:#f8fbff;margin-bottom:8px">'+
    '<div style="font-size:11px;color:#2c3e50;margin-bottom:6px"><b>Pass-summary Vmin roll-down ('+_escH(_flowSubTabLabel(mod, FLOW_DATA[mod]||{{}}))+')</b></div>'+ 
    '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'+
      '<label style="font-size:11px;color:#2c3e50;display:flex;align-items:center;gap:6px">'+
        '<input id="flow-pass-vmin-enable-'+idSafe+'" type="checkbox" '+(st.enabled?'checked':'')+' onchange="_flowPassVminApply(\\''+modArg+'\\')" '+
          'title="Enable roll-down. If a row Vmin is above the threshold, it is counted in the next lower frequency bin; the same test repeats until the row lands in a bin at or below the threshold or reaches the lowest bin." '+
          'style="width:13px;height:13px;cursor:pointer;accent-color:#3498db">'+
        '<span>Enable roll-down</span>'+ 
      '</label>'+ 
      '<label style="font-size:11px;color:#2c3e50">Threshold Vmin</label>'+ 
      '<input id="flow-pass-vmin-thresh-'+idSafe+'" type="number" step="0.01" value="'+_fmtVmin(st.thresh)+'" '+
        'onchange="_flowPassVminApply(\\''+modArg+'\\')" onkeydown="if(event.key===&quot;Enter&quot;){{event.preventDefault();_flowPassVminApply(\\''+modArg+'\\');}}" '+
        'title="Threshold in volts. Example: 1.25 means any row with Vmin > 1.25 rolls down to the next lower frequency bin, repeating until it reaches a bin at or below the threshold or the lowest bin." '+
        'style="width:88px;padding:2px 4px">'+ 
      '<button onclick="_flowPassVminApply(\\''+modArg+'\\')" title="Apply the roll-down checkbox and threshold to this module pass summary table" '+
        'style="padding:3px 10px;border:1px solid #98b2d2;border-radius:4px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">Apply</button>'+ 
      '<button onclick="_openRollinSim(\\''+modArg+'\\')" '+
        'title="Open Roll-Down Details/Simulator: enter DCM Vmin values and see which frequency bin each unit lands in after roll-down" '+
        'style="padding:3px 10px;border:1px solid #b57e2a;border-radius:4px;background:#fff3e0;color:#7d4e00;font-size:11px;cursor:pointer;font-weight:bold">'+
        '&#9660; Roll-Down Details/Simulator</button>'+
      '<span style="font-size:11px;color:#5d6d7e">Applies only to this module pass summary.</span>'+ 
    '</div></div>';
}}

function _flowAdjustedPassTable(mod){{
  var ptd = (PASS_TABLE||{{}})[mod];
  if(!ptd) return null;
  var srcFreq = ptd.freq_data || {{}};
  var freqKeys = Object.keys(srcFreq).map(Number).sort(function(a,b){{return b-a;}});
  var st = _flowPassVminState(mod);
  var out = {{label:(ptd.label||mod), upm_as_pct:!!ptd.upm_as_pct, upm_950_ref:ptd.upm_950_ref||null, freq_data:{{}}}};
  if(!freqKeys.length) return out;

  freqKeys.forEach(function(fmhz){{
    var sk = String(fmhz);
    out.freq_data[sk] = {{freq_mhz: fmhz, freq_label: (srcFreq[sk]&&srcFreq[sk].freq_label) ? srcFreq[sk].freq_label : (String(fmhz/1000)+'G'), groups:{{}}}};
  }});

  if(st.enabled){{
    // Per-unit rolldown: key by pkg only so each unit cascades once using
    // the best available Vmin data; destIdx then applies to ALL nKey groups.
    var _pkgD={{}}; // _pkgD[pkg]={{nKeys:{{nKey:1}},hIdx,vmins:{{fmhz:v}},rows:{{nKey:{{fmhz:row}}}}}}
    freqKeys.forEach(function(fmhz,idx){{
      var src=srcFreq[String(fmhz)]||{{}};
      // Process nKeys descending so highest-nKey vmin wins at each freq
      var _nkSorted=Object.keys(src.groups||{{}}).sort(function(a,b){{return parseInt(b)-parseInt(a);}});
      _nkSorted.forEach(function(nKey){{
        ((src.groups[nKey]||{{}}).rows||[]).forEach(function(row){{
          var pkg=row[0];
          // Key by prog+lot+wafer+pkg so each program's unit is tracked
          // independently — prevents cross-program merging that inflates counts.
          var unitKey=String(row[8]||'')+'\x7c'+String(row[1]||'')+'\x7c'+String(row[2]||'')+'\x7c'+String(pkg);
          if(!_pkgD[unitKey]) _pkgD[unitKey]={{pkg:pkg,nKeys:{{}},hIdx:idx,vmins:{{}},vmins_nk:{{}},rows:{{}}}};
          if(!_pkgD[unitKey].rows[nKey]) _pkgD[unitKey].rows[nKey]={{}};
          _pkgD[unitKey].rows[nKey][fmhz]=row;
          _pkgD[unitKey].nKeys[nKey]=1;
          if(_pkgD[unitKey].vmins[fmhz]===undefined) _pkgD[unitKey].vmins[fmhz]=row[5]; // prefer high nKey (population median)
          if(!_pkgD[unitKey].vmins_nk[nKey]) _pkgD[unitKey].vmins_nk[nKey]={{}};
          _pkgD[unitKey].vmins_nk[nKey][fmhz]=row[5]; // per-nKey vmin for independent rolldown
          if(idx<_pkgD[unitKey].hIdx) _pkgD[unitKey].hIdx=idx;
        }});
      }});
    }});
    // Population median Vmin per frequency for fit-line extrapolation when
    // a unit has no measured data at an intermediate frequency.
    var _medByFreq={{}};
    freqKeys.forEach(function(fmhz){{
      var sg=srcFreq[String(fmhz)]||{{}};
      var grp=(sg.groups&&(sg.groups['4']||sg.groups['3']||sg.groups['2']||sg.groups['1']))||null;
      if(grp&&grp.med_vmin!==undefined&&grp.med_vmin!==null) _medByFreq[fmhz]=grp.med_vmin;
    }});

    Object.keys(_pkgD).forEach(function(unitKey){{
      var ud=_pkgD[unitKey],pkg=ud.pkg,hIdx=ud.hIdx;
      var hFmhz=freqKeys[hIdx],hVmin=ud.vmins[hFmhz];
      var destIdx=hIdx,srcLbl=null,destVmin=hVmin;
      if(hVmin!==null&&hVmin!==undefined&&hVmin===hVmin&&hVmin>st.thresh){{
        srcLbl=(srcFreq[String(hFmhz)]&&srcFreq[String(hFmhz)].freq_label)||(hFmhz/1000+'G');
        var _mH=_medByFreq[hFmhz]; // population median at unit's highest tested freq
        for(var ti=hIdx+1;ti<freqKeys.length;ti++){{
          var lv=ud.vmins[freqKeys[ti]];
          // If unit has no data at this freq, extrapolate via population median delta
          if((lv===undefined||lv===null||lv!==lv)&&_mH!==undefined){{
            var _mT=_medByFreq[freqKeys[ti]];
            if(_mT!==undefined) lv=hVmin+(_mT-_mH);
          }}
          if(lv!==undefined&&lv!==null&&lv===lv&&lv<=st.thresh){{destIdx=ti;destVmin=lv;break;}}
        }}
        if(destIdx===hIdx) destIdx=freqKeys.length-1; // can't recover at any lower freq
      }}
      // Only carry nKey groups that have data at the unit's highest qualifying
      // frequency (hFmhz). This prevents a unit classified at a high nKey at
      // hFmhz (e.g. 4 DCMs at 5.6G) from also appearing in lower nKey groups
      // (e.g. 1 DCM at 4.8G where only one instance was tested) at every
      // frequency via carry-down, which would inflate "Below Threshold" counts.
      var _carryNKeys = Object.keys(ud.nKeys).filter(function(nKey){{
        return ud.rows[nKey] && ud.rows[nKey][hFmhz] !== undefined;
      }});
      if(!_carryNKeys.length) _carryNKeys = Object.keys(ud.nKeys); // safety fallback
      _carryNKeys.forEach(function(nKey){{
        var _nkRows=ud.rows[nKey]||{{}};
        // Best available row for this nKey (prefer hFmhz, else first found)
        var _hRow=_nkRows[hFmhz];
        if(!_hRow){{var _fks=Object.keys(_nkRows);_hRow=_fks.length?_nkRows[_fks[0]]:null;}}
        if(!_hRow) return; // no row for this nKey at all — skip
        // Use per-nKey vmin for rolldown: nKey=2 uses 2nd-smallest DCM vmin,
        // nKey=4 uses max — so each nKey group rolls down independently.
        var _vminsNK = (ud.vmins_nk&&ud.vmins_nk[nKey]) || ud.vmins;
        var hVminNK = _vminsNK[hFmhz];
        var destIdxNK=hIdx, srcLblNK=null, destVminNK=hVminNK;
        if(hVminNK!==null&&hVminNK!==undefined&&hVminNK===hVminNK&&hVminNK>st.thresh){{
          srcLblNK=(srcFreq[String(hFmhz)]&&srcFreq[String(hFmhz)].freq_label)||(hFmhz/1000+'G');
          var _mH2=_medByFreq[hFmhz];
          for(var ti2=hIdx+1;ti2<freqKeys.length;ti2++){{
            var lv2=_vminsNK[freqKeys[ti2]];
            if((lv2===undefined||lv2===null||lv2!==lv2)&&_mH2!==undefined){{
              var _mT2=_medByFreq[freqKeys[ti2]];
              if(_mT2!==undefined) lv2=hVminNK+(_mT2-_mH2);
            }}
            if(lv2!==undefined&&lv2!==null&&lv2===lv2&&lv2<=st.thresh){{destIdxNK=ti2;destVminNK=lv2;break;}}
          }}
          if(destIdxNK===hIdx) destIdxNK=freqKeys.length-1;
        }}
        // Passing units (destIdxNK===hIdx) carried to lower freqs only with auto-downflow.
        // Rolled units (destIdxNK>hIdx) always carry from landing freq downward.
        var _fiMax = (destIdxNK===hIdx && !_FLOW_PASS_AUTODOWNFLOW[mod]) ? hIdx+1 : freqKeys.length;
        for(var fi=destIdxNK;fi<_fiMax;fi++){{
          var fmhz2=freqKeys[fi];
          var dst=out.freq_data[String(fmhz2)];
          if(!dst.groups[nKey]) dst.groups[nKey]={{rows:[],rolledSrcs:{{}}}};
          if(!dst.groups[nKey].rolledSrcs) dst.groups[nKey].rolledSrcs={{}};
          var _rowToPush;
          if(_nkRows[fmhz2]){{
            _rowToPush=_nkRows[fmhz2];
          }} else if(fi===destIdxNK&&srcLblNK){{
            _rowToPush=_hRow.slice();
            _rowToPush[5]=Math.round(destVminNK*1000)/1000;
          }} else {{
            _rowToPush=_hRow;
          }}
          dst.groups[nKey].rows.push(_rowToPush);
          if(fi===destIdxNK&&srcLblNK&&destIdxNK!==hIdx){{
            dst.groups[nKey].rolledSrcs[pkg]=srcLblNK; // tag pkg → source freq label
          }}
        }}
      }});
    }});
  }} else {{
    freqKeys.forEach(function(fmhz){{
      var src=srcFreq[String(fmhz)]||{{}};
      Object.keys(src.groups||{{}}).forEach(function(nKey){{
        ((src.groups[nKey]||{{}}).rows||[]).forEach(function(row){{
          var dst=out.freq_data[String(fmhz)];
          if(!dst.groups[nKey]) dst.groups[nKey]={{rows:[],rolledSrcs:{{}}}};
          dst.groups[nKey].rows.push(row);
        }});
      }});
    }});
  }}

  // Auto-downflow: units that pass at a higher frequency are assumed to also
  // pass at all lower frequencies. Walk from highest to lowest freq, carrying
  // a cumulative union of rows (keyed by prog+pkg) into each lower freq group.
  // Works regardless of whether roll-down is also enabled. Per-module toggle.
  if(_FLOW_PASS_AUTODOWNFLOW[mod]){{
    var _nKeySet = new Set();
    freqKeys.forEach(function(fmhz){{
      var fd_ = out.freq_data[String(fmhz)];
      if(fd_) Object.keys(fd_.groups||{{}}).forEach(function(nk){{_nKeySet.add(nk);}});
    }});
    _nKeySet.forEach(function(nKey){{
      // Build running accumulated rows (prog+pkg → row) from high→low
      var accumulated = {{}};  // prog+pkg key → row
      freqKeys.forEach(function(fmhz){{
        var fd_ = out.freq_data[String(fmhz)];
        if(!fd_) return;
        if(!fd_.groups[nKey]) fd_.groups[nKey] = {{rows:[], rolledSrcs:{{}}}};
        var grp_ = fd_.groups[nKey];
        // Add new rows from this freq into the accumulator
        (grp_.rows||[]).forEach(function(row){{
          var k = String(row[8]||'')+'|'+String(row[0]||'');
          if(!accumulated[k]) accumulated[k] = row;
        }});
        // Always rebuild rows as the full deduplicated union so far
        grp_.rows = Object.keys(accumulated).map(function(k){{ return accumulated[k]; }});
      }});
    }});
  }}

  Object.keys(out.freq_data).forEach(function(sk){{
    var fd = out.freq_data[sk];
    Object.keys(fd.groups).forEach(function(nKey){{
      var g = fd.groups[nKey];
      if(!g.rows.length) return;
      var vmins = g.rows.map(function(r){{return r[5];}}).filter(function(v){{return v!==null&&v!==undefined&&v===v;}}).sort(function(a,b){{return a-b;}});
      if(!vmins.length) return;
      var mid = vmins.length >> 1;
      g.n = g.rows.length;
      g.med_vmin = vmins.length % 2 ? vmins[mid] : (vmins[mid - 1] + vmins[mid]) / 2;
      g.n_total = g.rows.length;
    }});
  }});
  return out;
}}

function _flowToggleAutoDownflow(mod, on){{
  _FLOW_PASS_AUTODOWNFLOW[mod] = !!on;
  buildFlowTab();
}}

function _renderFlowFreqMedianXY(mod){{
  var idSafe = _flowSafeId(mod);
  var slot = document.getElementById('flow-freqxy-slot-' + idSafe);
  if(!slot) return;
  var fd = FLOW_DATA[mod] || {{}};
  var ak = activeKeys();
  if(!_FLOW_UPM_PCT_RANGE[mod]) _FLOW_UPM_PCT_RANGE[mod] = {{lo:_FLOW_UPM_PCT_MIN, hi:_FLOW_UPM_PCT_MAX}};
  var st = _FLOW_UPM_PCT_RANGE[mod];
  var useUpmPct = !!fd.upm_as_pct;
  var d = _flowFreqMedianPoints(mod, ak, st.lo, st.hi, useUpmPct);
  if(!d.pts.length){{
    if(useUpmPct){{
      _safeInnerHTML(slot, _flowUpmPctControlsHtml(mod, st) +
        '<div style="padding:10px;color:#888;font-size:12px">No frequency median data for current selection within '+_fmtUpm(st.lo)+'% to '+_fmtUpm(st.hi)+'% UPM.</div>');
    }} else {{
      _safeInnerHTML(slot, '<div style="padding:10px;color:#888;font-size:12px">No frequency median data for current selection.</div>');
    }}
    return;
  }}
  var cid = 'flowfreq-' + idSafe;
  var controls = '';
  if(useUpmPct){{
    controls = _flowUpmPctControlsHtml(mod, st);
  }}
  _safeInnerHTML(slot, controls + _xyContainer(
    cid,
    'Freq vs Median Vmin (' + _escH(_flowSubTabLabel(mod, fd)) + ')',
    d.pts.length,
    true
  ));
  _xyInit(cid, d.pts, d.mats, 560, 320, _flowSubTabLabel(mod, fd), 'Freq (GHz)', true);
  var _s = _XY_STATE[cid];
  if(_s){{
    _s.xmax = 6.0;
    _s.ymax = 1.4;
    _xyRender(cid);
  }}
}}

function _flowInitSummaryState(mods){{
  mods = mods || [];
  mods.forEach(function(m){{
    if(_FLOW_SUMMARY_PLOT_SEL[m]===undefined) _FLOW_SUMMARY_PLOT_SEL[m]=true;
    if(_FLOW_SUMMARY_CARD_VIS[m]===undefined) _FLOW_SUMMARY_CARD_VIS[m]=true;
    if(_FLOW_PASS_VMIN_RULE[m]===undefined) _FLOW_PASS_VMIN_RULE[m]={{enabled:false, thresh:1.25}};
  }});
  Object.keys(_FLOW_SUMMARY_PLOT_SEL).forEach(function(m){{
    if(mods.indexOf(m)<0) delete _FLOW_SUMMARY_PLOT_SEL[m];
  }});
  Object.keys(_FLOW_SUMMARY_CARD_VIS).forEach(function(m){{
    if(mods.indexOf(m)<0) delete _FLOW_SUMMARY_CARD_VIS[m];
  }});
  Object.keys(_FLOW_PASS_VMIN_RULE).forEach(function(m){{
    if(mods.indexOf(m)<0) delete _FLOW_PASS_VMIN_RULE[m];
  }});
}}

function _flowSetCardVisible(mod, on){{
  _FLOW_SUMMARY_CARD_VIS[mod] = !!on;
  var el = document.getElementById('flow-mod-'+_flowSafeId(mod));
  if(el) el.style.display = _FLOW_SUMMARY_CARD_VIS[mod] ? 'block' : 'none';
  var rc = document.getElementById('flow-restore-chip-'+_flowSafeId(mod));
  if(rc) rc.style.display = _FLOW_SUMMARY_CARD_VIS[mod] ? 'none' : 'inline-flex';
  _renderFlowSummaryPlot(_flowSortMods(Object.keys(FLOW_DATA||{{}})));
}}

function _flowShowOnlyCard(mod){{
  var mods = _flowSortMods(Object.keys(FLOW_DATA||{{}}));
  mods.forEach(function(m){{ _FLOW_SUMMARY_CARD_VIS[m] = (m===mod); }});
  mods.forEach(function(m){{
    var el = document.getElementById('flow-mod-'+_flowSafeId(m));
    if(el) el.style.display = _FLOW_SUMMARY_CARD_VIS[m] ? 'block' : 'none';
    var rc = document.getElementById('flow-restore-chip-'+_flowSafeId(m));
    if(rc) rc.style.display = _FLOW_SUMMARY_CARD_VIS[m] ? 'none' : 'inline-flex';
  }});
  _renderFlowSummaryPlot(mods);
}}

function _flowShowAllCards(){{
  var mods = _flowSortMods(Object.keys(FLOW_DATA||{{}}));
  mods.forEach(function(m){{ _FLOW_SUMMARY_CARD_VIS[m] = true; }});
  mods.forEach(function(m){{
    var el = document.getElementById('flow-mod-'+_flowSafeId(m));
    if(el) el.style.display = 'block';
    var rc = document.getElementById('flow-restore-chip-'+_flowSafeId(m));
    if(rc) rc.style.display = 'none';
  }});
  _renderFlowSummaryPlot(mods);
}}

function _flowSummarySelectedMods(mods){{
  var vis = (mods||[]).filter(function(m){{return _FLOW_SUMMARY_CARD_VIS[m]!==false;}});
  var picked = vis.filter(function(m){{return !!_FLOW_SUMMARY_PLOT_SEL[m];}});
  return picked.length ? picked : (vis.length?[vis[0]]:[]);
}}

function _flowSetSummaryGroupMode(mode){{
  _FLOW_SUMMARY_GROUP_MODE = (mode==='material') ? 'material' : 'module';
  var cb = document.getElementById('flow-group-by-mat-cb');
  if(cb) cb.checked = (_FLOW_SUMMARY_GROUP_MODE==='material');
  _renderFlowSummaryPlot(_flowSortMods(Object.keys(FLOW_DATA||{{}})));
}}

function _vfFamilyForMod(mod){{
  var m = String(mod||'').toUpperCase();
  if(/CORE/.test(m)) return 'CORE';
  if(/ATOM/.test(m)) return 'ATOM';
  if(/CCF|RING/.test(m)) return 'CCF';
  return null;
}}

function _serCbId(fam,idx){{return 'flow-vf-ser-'+fam+'_'+idx;}}
function _flowToggleVfFamEl(el){{
  var fam=el.getAttribute('data-fam');
  _FLOW_VF_FAM[fam]=!!el.checked;
  (VF_CHART_DATA[fam]||[]).forEach(function(s,i){{
    _FLOW_VF_SER[fam+'::'+s.label]=!!el.checked;
    var cb=document.getElementById(_serCbId(fam,i));
    if(cb)cb.checked=!!el.checked;
  }});
  var sersEl=document.getElementById('flow-vf-sers-'+fam);
  if(sersEl) sersEl.style.display=el.checked?'inline-flex':'none';
  _updateVfFamCb(fam);
  _renderFlowSummaryPlot(_flowSortMods(Object.keys(FLOW_DATA||{{}})));
}}
function _flowToggleVfSerEl(el){{
  var fam=el.getAttribute('data-fam');
  var idx=+el.getAttribute('data-idx');
  var s=(VF_CHART_DATA[fam]||[])[idx];
  if(s)_FLOW_VF_SER[fam+'::'+s.label]=!!el.checked;
  _updateVfFamCb(fam);
  _renderFlowSummaryPlot(_flowSortMods(Object.keys(FLOW_DATA||{{}})));
}}
function _updateVfFamCb(fam){{
  var sers=VF_CHART_DATA[fam]||[];
  var allOn=sers.length>0&&sers.every(function(s){{return !!_FLOW_VF_SER[fam+'::'+s.label];}});
  var anyOn=sers.some(function(s){{return !!_FLOW_VF_SER[fam+'::'+s.label];}});
  _FLOW_VF_FAM[fam]=anyOn;
  var cb=document.getElementById('flow-vf-fam-'+fam);
  if(cb){{cb.checked=allOn;cb.indeterminate=anyOn&&!allOn;}}
}}
function _vfInterpUpm(fghz, upmPct, mat){{
  // Bilinear interpolation: for a given test frequency and material,
  // returns the expected Vmin at a specific UPM% by:
  //   1. collecting all material-matched VF series with their parsed UPM% labels
  //   2. for each series, interpolating Vmin at fghz (freq axis)
  //   3. then linearly interpolating/extrapolating those (UPM%, Vmin) pairs to upmPct
  // Returns null if insufficient VF data.

  // --- helper: linear interpolate pts [{{x,y}}] sorted by x at query xq ---
  var _interpFreq = function(pts, xq){{
    if(!pts || !pts.length) return null;
    var sp=pts.slice().sort(function(a,b){{return +a.x-+b.x;}});
    if(xq<=+sp[0].x) return +sp[0].y;
    if(xq>=+sp[sp.length-1].x) return +sp[sp.length-1].y;
    for(var i=0;i<sp.length-1;i++){{
      var x0=+sp[i].x, x1=+sp[i+1].x;
      if(xq>=x0&&xq<=x1){{
        var t=(xq-x0)/(x1-x0);
        return +sp[i].y + t*(+sp[i+1].y - +sp[i].y);
      }}
    }}
    return +sp[sp.length-1].y;
  }};

  // --- build material token list ---
  var matTokens = [];
  if(mat){{
    var ml=(mat+'').toLowerCase();
    if(ml.indexOf('bllc')>=0) matTokens.push('bllc');
    if(ml.indexOf('r0')>=0)   matTokens.push('r0');
    if(ml.indexOf('p0')>=0)   matTokens.push('p0');
    if(ml.indexOf('l0')>=0)   matTokens.push('l0');
    ml.split(/[\\s_\\-]+/).forEach(function(tok){{
      if(tok.length>1 && matTokens.indexOf(tok)<0) matTokens.push(tok);
    }});
  }}

  // --- parse UPM% from series label, e.g. "at 87% UPM" or "87% UPM" ---
  var _parseUpm = function(label){{
    var m=(label||'').match(/(\\d+(?:\\.\\d+)?)\\s*%\\s*UPM/i)
          || (label||'').match(/UPM[,\\s]+(\\d+(?:\\.\\d+)?)%/i);
    return m ? parseFloat(m[1]) : null;
  }};

  // --- collect (upmPct, vAtFreq) from matching series ---
  var uvPairs = []; // [{{u: upm_pct, v: vmin_at_fghz}}]
  var _addSeries = function(series, fam){{
    var u = _parseUpm(series.label);
    if(u===null) return;
    var v = _interpFreq(series.points||[], fghz);
    if(v===null) return;
    uvPairs.push({{u:u, v:v}});
  }};

  // pass 1: material-matched series
  if(matTokens.length){{
    Object.keys(VF_CHART_DATA||{{}}).forEach(function(fam){{
      (VF_CHART_DATA[fam]||[]).forEach(function(series){{
        var lbl=(series.label||'').toLowerCase();
        if(matTokens.some(function(tok){{return lbl.indexOf(tok)>=0;}}))
          _addSeries(series, fam);
      }});
    }});
  }}
  // pass 2: fallback — all series (if no material match)
  if(!uvPairs.length){{
    Object.keys(VF_CHART_DATA||{{}}).forEach(function(fam){{
      (VF_CHART_DATA[fam]||[]).forEach(function(series){{ _addSeries(series, fam); }});
    }});
  }}
  if(!uvPairs.length) return null;

  // de-dup by UPM% (keep first) and sort
  var seen={{}};
  uvPairs=uvPairs.filter(function(p){{
    if(seen[p.u]) return false;
    seen[p.u]=1; return true;
  }}).sort(function(a,b){{return a.u-b.u;}});

  if(uvPairs.length===1) return uvPairs[0].v; // only one curve, no interpolation possible

  // interpolate/extrapolate along UPM% axis
  if(upmPct<=uvPairs[0].u){{
    // extrapolate below
    var p0=uvPairs[0], p1=uvPairs[1];
    var t=(upmPct-p0.u)/(p1.u-p0.u);
    return p0.v + t*(p1.v-p0.v);
  }}
  if(upmPct>=uvPairs[uvPairs.length-1].u){{
    // extrapolate above
    var pn=uvPairs[uvPairs.length-1], pn1=uvPairs[uvPairs.length-2];
    var t=(upmPct-pn.u)/(pn.u-pn1.u);
    return pn.v + t*(pn.v-pn1.v);
  }}
  for(var i=0;i<uvPairs.length-1;i++){{
    if(upmPct>=uvPairs[i].u && upmPct<=uvPairs[i+1].u){{
      var t=(upmPct-uvPairs[i].u)/(uvPairs[i+1].u-uvPairs[i].u);
      return uvPairs[i].v + t*(uvPairs[i+1].v-uvPairs[i].v);
    }}
  }}
  return uvPairs[uvPairs.length-1].v;
}}

function _flowSetNormalizeUpm(enabled, pct){{
  _FLOW_SUMMARY_NORMALIZE_UPM = !!enabled;
  if(pct!==undefined && pct===pct && isFinite(+pct)) _FLOW_SUMMARY_NORMALIZE_UPM_PCT = +pct;
  var inp = document.getElementById('flow-normalize-upm-pct');
  if(inp) inp.disabled = !_FLOW_SUMMARY_NORMALIZE_UPM;
  _renderFlowSummaryPlot(_flowSortMods(Object.keys(FLOW_DATA||{{}})));
}}

function _buildVfControlsHtml(){{
  if(!Object.keys(VF_CHART_DATA||{{}}).length) return '';
  var _h='<a href="https://intel.sharepoint.com/:x:/r/sites/ftesdsexecution/_layouts/15/Doc.aspx?sourcedoc=%7B93DA59EF-F2D8-4862-8BCE-C7CAA7F7B713%7D&file=NVL_N2P_CLASS_VF_tracker.xlsx&nav=MTVfe0Q5MTI1RjhCLTEzQTctNDA3NC1BMDYwLUQ3NDhFODVENzI1OH0&action=default&mobileredirect=true" target="_blank" style="font-size:11px;font-weight:600;color:#555;white-space:nowrap;text-decoration:none" title="Open NVL_N2P_CLASS_VF_tracker.xlsx">Overlay product family VF \u2197</a>';
  Object.keys(VF_CHART_DATA||{{}}).forEach(function(fam){{
    var sers=VF_CHART_DATA[fam]||[];
    var allOn=sers.length>0&&sers.every(function(s){{return !!_FLOW_VF_SER[fam+'::'+s.label];}});
    var famOn=allOn||sers.some(function(s){{return !!_FLOW_VF_SER[fam+'::'+s.label];}});
    _h+='<span style="display:inline-flex;align-items:center;gap:3px;padding:2px 7px 2px 5px;border:1px solid #dbe7f4;border-radius:4px;background:#f0f7ff;margin-left:4px">';
    _h+='<label style="font-size:11px;font-weight:700;color:#1a4a7a;display:inline-flex;align-items:center;gap:3px;cursor:pointer"><input id="flow-vf-fam-'+fam+'" data-fam="'+fam+'" type="checkbox"'+(famOn?' checked':'')+' onchange="_flowToggleVfFamEl(this)" style="accent-color:#e67e22;width:13px;height:13px">'+fam+'</label>';
    _h+='<span id="flow-vf-sers-'+fam+'" style="display:'+(famOn?'inline-flex':'none')+';align-items:center;flex-wrap:wrap;gap:3px">';
    sers.forEach(function(s,si){{
      var sid=_serCbId(fam,si);
      var serOn=!!_FLOW_VF_SER[fam+'::'+s.label];
      _h+='<label style="font-size:10px;color:#2c3e50;display:inline-flex;align-items:center;gap:2px;cursor:pointer;margin-left:3px"><input id="'+sid+'" data-fam="'+fam+'" data-idx="'+si+'" type="checkbox"'+(serOn?' checked':'')+' onchange="_flowToggleVfSerEl(this)" style="accent-color:#e67e22;width:11px;height:11px">'+_escH(s.label||fam)+'</label>';
    }});
    _h+='</span>';
    _h+='</span>';
  }});
  return _h;
}}
function _initFlowTableResize(){{
  if(document.body.hasAttribute('data-ftr-init'))return;
  document.body.setAttribute('data-ftr-init','1');
  var _drag=null;
  document.addEventListener('mousedown',function(e){{
    var h=e.target;
    if(!h.hasAttribute('data-flow-trh'))return;
    var sec=document.getElementById('flow-cards-section');
    if(!sec)return;
    _drag={{el:sec,startY:e.clientY,startH:sec.offsetHeight}};
    e.preventDefault();
  }},true);
  document.addEventListener('mousemove',function(e){{
    if(!_drag)return;
    var newH=Math.max(80,_drag.startH+(e.clientY-_drag.startY));
    _drag.el.style.height=newH+'px';
    _FLOW_CARDS_H=newH;
  }});
  document.addEventListener('mouseup',function(){{_drag=null;}});
}}

function _flowToggleTablesAll(on){{
  _FLOW_SUMMARY_SHOW_TABLE = !!on;
  var cb = document.getElementById('flow-summary-show-table');
  if(cb) cb.checked = _FLOW_SUMMARY_SHOW_TABLE;
  document.querySelectorAll('.flow-summary-table-wrap').forEach(function(el){{
    el.style.display = _FLOW_SUMMARY_SHOW_TABLE ? 'block' : 'none';
  }});
}}

function _flowToggleSummaryPlot(){{
  _FLOW_SUMMARY_PLOT_COLLAPSED = !_FLOW_SUMMARY_PLOT_COLLAPSED;
  var body  = document.getElementById('flow-summary-plot-body');
  var arrow = document.getElementById('flow-summary-plot-arrow');
  if(body)  body.style.display  = _FLOW_SUMMARY_PLOT_COLLAPSED ? 'none' : 'block';
  if(arrow) arrow.innerHTML     = _FLOW_SUMMARY_PLOT_COLLAPSED ? '&#9660;' : '&#9650;';
  if(!_FLOW_SUMMARY_PLOT_COLLAPSED) _renderFlowSummaryPlot(Object.keys(FLOW_DATA||{{}}));
}}

function _flowToggleSummaryOverlay(on){{
  _FLOW_SUMMARY_OVERLAY_ALL = !!on;
  if(!_FLOW_SUMMARY_PLOT_COLLAPSED) _renderFlowSummaryPlot(_flowSortMods(Object.keys(FLOW_DATA||{{}})));
}}

function _flowToggleSummaryMod(mod,on){{
  _FLOW_SUMMARY_PLOT_SEL[mod] = !!on;
  _renderFlowSummaryPlot(_flowSortMods(Object.keys(FLOW_DATA||{{}})));
}}

function _renderFlowSummaryPlot(mods){{
  var slot = document.getElementById('flow-summary-plot-slot');
  if(!slot) return;
  mods = mods || _flowSortMods(Object.keys(FLOW_DATA||{{}}));
  var picked = _flowSummarySelectedMods(mods);
  var useMaterialGroup = (_FLOW_SUMMARY_GROUP_MODE==='material');
  var ak = activeKeys();
  var pts = [];
  var cats = [];
  var hovers = [];
  picked.forEach(function(mod){{
    var fd = FLOW_DATA[mod] || {{}};
    var modLbl = _flowSubTabLabel(mod, fd);
    if(!_FLOW_UPM_PCT_RANGE[mod]) _FLOW_UPM_PCT_RANGE[mod] = {{lo:_FLOW_UPM_PCT_MIN, hi:_FLOW_UPM_PCT_MAX}};
    var st = _FLOW_UPM_PCT_RANGE[mod];
    var d = _flowFreqMedianPoints(mod, ak, st.lo, st.hi, !!fd.upm_as_pct);
    for(var i=0;i<d.pts.length;i++){{
      pts.push(d.pts[i]);
      var matLbl = d.mats[i] || 'Unknown';
      cats.push(useMaterialGroup ? matLbl : modLbl);
      hovers.push('Module: '+modLbl+' | Material: '+matLbl);
    }}
  }});
  if(!pts.length){{
    _safeInnerHTML(slot,'<div style="padding:10px;color:#888;font-size:12px">No summary frequency median data for current selection.</div>');
    return;
  }}
  var cid='flow-summary-xy';
  var uniq = [];
  cats.forEach(function(c){{ if(uniq.indexOf(c)<0) uniq.push(c); }});
  var chips = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">';
  uniq.forEach(function(lbl, i){{
    var c = _PALETTE[i % _PALETTE.length];
    chips += '<span style="display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border:1px solid #d5e2f1;border-radius:999px;background:#f7fbff;font-size:11px;color:#234">'+
      '<span style="width:9px;height:9px;border-radius:50%;background:'+c+';display:inline-block"></span>'+_escH(lbl)+'</span>';
  }});
  // Build VF reference overlay lines
  var _VF_PAL = ['#e67e22','#8e44ad','#16a085','#c0392b','#d35400','#6c3483','#117a65','#922b21','#1a5276','#784212'];
  var vfLines = [];
  var _vci = 0;
  Object.keys(VF_CHART_DATA||{{}}).forEach(function(fam){{
    (VF_CHART_DATA[fam]||[]).forEach(function(series){{
      if(!_FLOW_VF_SER[fam+'::'+series.label]) return;
      var linePts = (series.points||[]).map(function(p){{return [+p.x, +p.y];}});
      if(!linePts.length) return;
      var col = _VF_PAL[_vci % _VF_PAL.length]; _vci++;
      vfLines.push({{label: series.label||fam, color: col, pts: linePts}});
      chips += '<span style="display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border:1px solid #f5cba7;border-radius:999px;background:#fef9f5;font-size:11px;color:#234">'+
        '<span style="width:26px;height:3px;border-bottom:2px dashed '+col+';display:inline-block"></span>'+_escH(series.label||fam)+'</span>';
    }});
  }});
  chips += '</div>';
  var ttl = 'Summary Freq vs Median Vmin';
  // Preserve user-resized dimensions before rebuilding the container
  var _rzOld=document.getElementById('xy-resize-flow-summary-xy');
  if(_rzOld&&_rzOld.offsetHeight>80) _FLOW_SUMMARY_XY_H=_rzOld.offsetHeight;
  if(_rzOld&&_rzOld.offsetWidth>80)  _FLOW_SUMMARY_XY_W=_rzOld.offsetWidth;
  _safeInnerHTML(slot, _xyContainer(cid, ttl, pts.length, true, true, _FLOW_SUMMARY_XY_H, _FLOW_SUMMARY_XY_W) + chips);
  _xyInit(cid, pts, cats, _FLOW_SUMMARY_XY_W, _FLOW_SUMMARY_XY_H, 'Summary', 'Freq (GHz)', true);
  var _s=_XY_STATE[cid];
  if(_s){{
    _s.groupBy = true;
    _s.noAxisLabels = true;
    _s.hoverMeta = hovers;
    _s.xmax=6.0;
    _s.ymax=1.4;
    _s.vfLines = vfLines.length ? vfLines : null;
    _xyRender(cid);
  }}
}}

function buildFlowTab(){{
  var ak=_allActiveKeys();
  var body = document.getElementById('tab-flow-body');
  if(!body) return;
  var mods = _flowSortMods(Object.keys(FLOW_DATA||{{}}));
  // Snapshot current card widths+heights before rebuild so user-resized dimensions are preserved
  mods.forEach(function(mod){{
    var el = document.getElementById('flow-mod-'+_flowSafeId(mod));
    if(el){{
      var r = el.getBoundingClientRect();
      var w = r.width || parseFloat(el.style.width);
      if(w > 0) _FLOW_CARD_W[mod] = w;
      var h = r.height || parseFloat(el.style.height);
      if(h > 0) _FLOW_CARD_H[mod] = h;
    }}
  }});
  if(!mods.length){{
    _safeInnerHTML(body, '<p style="padding:20px;color:#888">No Vmin flow data available.</p>');
    return;
  }}
  _flowInitSummaryState(mods);
  var html = '<div style="padding:10px">';
  if(IBIN1_COL) {{
    var _ibin1Cnt = _ibin1Denom(ak);
    html += '<div style="font-size:11px;color:#555;padding:5px 10px;background:#fff8e1;border:1px solid #f9a825;border-radius:4px;margin-bottom:8px;display:flex;align-items:center;gap:12px">'+
      '<span>&#128196; Pass tables: <b>'+_escH(IBIN1_COL)+'</b> = 1 units only</span>'+
      '<span style="color:#aaa">&nbsp;|&nbsp;</span>'+
      '<span>IBIN=1 count (current filter): <b>'+_ibin1Cnt.toLocaleString()+'</b>'+
      (IBIN1_COUNT!=null&&_ibin1Cnt!==IBIN1_COUNT?'<span style="color:#aaa;font-size:10px"> / '+Number(IBIN1_COUNT).toLocaleString()+' total</span>':'')+
      '</span></div>';
  }}
  // ── Collect all prog6248 + prog→lot map from FLOW_DATA rows ────────────
  var _flowAllProgs = [];
  var _flowProgSeen = {{}};
  var _flowProgLotMap = {{}};
  Object.keys(FLOW_DATA||{{}}).forEach(function(mod){{
    (FLOW_DATA[mod].instances||[]).forEach(function(inst){{
      (inst.freqs||[]).forEach(function(f){{
        (f.rows||[]).forEach(function(r){{
          var p = String(r[8]||''); var l = String(r[1]||'');
          if(p && !_flowProgSeen[p]){{_flowProgSeen[p]=1;_flowAllProgs.push(p);_flowProgLotMap[p]=[];}}
          if(p && l && _flowProgLotMap[p] && _flowProgLotMap[p].indexOf(l)<0) _flowProgLotMap[p].push(l);
        }});
      }});
    }});
  }});
  _flowAllProgs.sort();
  Object.keys(_flowProgLotMap).forEach(function(p){{ _flowProgLotMap[p].sort(); }});
  // ── Filter prog list to only those present in the current active keys ──
  var _akProgsAvail=new Set();
  WFR_DATA.forEach(function(w){{
    if(ak.has(w.lot+'/'+w.wafer+'/'+(w.prog6248||''))) _akProgsAvail.add(w.prog6248||'');
  }});
  _flowAllProgs=_flowAllProgs.filter(function(p){{return _akProgsAvail.has(p);}});
  // Sync _FLOW_PROG_ACTIVE / _FLOW_LOT_ACTIVE to the current filtered available set.
  // Collect-then-delete avoids unsafe mutation during forEach.
  // Progs no longer available are removed; newly available progs are re-added (handles filter-cleared case).
  var _toRemoveP=[];
  _FLOW_PROG_ACTIVE.forEach(function(p){{if(_flowAllProgs.indexOf(p)<0)_toRemoveP.push(p);}});
  _toRemoveP.forEach(function(p){{_FLOW_PROG_ACTIVE.delete(p);}});
  var _toRemoveL=[];
  _FLOW_LOT_ACTIVE.forEach(function(key){{
    var sep=key.indexOf('\x00');var prog=sep>=0?key.substring(0,sep):key;
    if(_flowAllProgs.indexOf(prog)<0)_toRemoveL.push(key);
  }});
  _toRemoveL.forEach(function(key){{_FLOW_LOT_ACTIVE.delete(key);}});
  // Add any progs not yet in the active sets.
  // When the left-panel prog6248 text filter is active it overrides the user's
  // dropdown deselections (_FLOW_PROG_USER_HIDDEN), so that typing a prog name
  // in the left panel always makes that prog visible in the freq matrix.
  // When the text filter is cleared, _FLOW_PROG_USER_HIDDEN is respected again.
  var _sp6248Active = !!(_SEARCH.prog6248||'').trim();
  _flowAllProgs.forEach(function(p){{
    if(!_FLOW_PROG_ACTIVE.has(p) && (_sp6248Active || !_FLOW_PROG_USER_HIDDEN.has(p))){{
      _FLOW_PROG_ACTIVE.add(p);
      (_flowProgLotMap[p]||[]).forEach(function(l){{_FLOW_LOT_ACTIVE.add(p+'\x00'+l);}});
    }}
  }});
  var _flowHasFilter = _flowAllProgs.length > 1 ||
    _flowAllProgs.some(function(p){{ return (_flowProgLotMap[p]||[]).length > 1; }});

  html += '<div style="padding:8px 10px;border:1px solid #dbe7f4;border-radius:6px;background:#f8fbff;margin-bottom:10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">'+
          '<label style="font-size:11px;color:#2c3e50;display:flex;align-items:center;gap:6px"><input id="flow-summary-show-table" type="checkbox" '+(_FLOW_SUMMARY_SHOW_TABLE?'checked':'')+' onchange="_flowToggleTablesAll(this.checked)" style="width:13px;height:13px;accent-color:#3498db">Show first table (all sections)</label>'+
      '<button onclick="_flowShowAllCards()" style="padding:3px 10px;border:1px solid #98b2d2;border-radius:4px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">Show all cards</button>'+
      '<span id="flow-restore-wrap" style="display:flex;gap:6px;flex-wrap:wrap"></span>';
  if(_flowHasFilter) {{
    html += '<div id="flow-prog-filter" style="position:relative;display:inline-flex;align-items:center;gap:6px">';
    html += '<span style="font-size:11px;color:#1a4a7a;font-weight:700;white-space:nowrap">Class Prog 6248:</span>';
    html += '<div style="position:relative;display:inline-block">';
    html += '<button id="flow-prog-dd-btn" onclick="_flowProgDdToggle()" style="padding:3px 24px 3px 10px;border:1px solid #98b2d2;border-radius:4px;background:#fff;color:#1a3a5c;font-size:12px;cursor:pointer;min-width:140px;text-align:left;position:relative">';
    html += '<span id="flow-prog-dd-label">All selected</span>';
    html += '<span style="position:absolute;right:7px;top:50%;transform:translateY(-50%);font-size:10px">&#9660;</span></button>';
    html += '<div id="flow-prog-dd-panel" style="display:none;position:absolute;left:0;top:100%;z-index:300;background:#fff;border:1px solid #98b2d2;border-radius:4px;box-shadow:0 3px 10px rgba(0,0,0,0.15);min-width:220px;padding:6px 0">';
    html += '<div style="padding:4px 8px;border-bottom:1px solid #e0eaf5">';
    html += '<input id="flow-prog-search" type="text" placeholder="Search prog..." oninput="_flowProgSearch(this.value)" style="width:100%;box-sizing:border-box;padding:3px 7px;border:1px solid #b8d0ea;border-radius:3px;font-size:11px;color:#1a3a5c;outline:none">';
    html += '</div>';
    html += '<div style="display:flex;gap:4px;padding:4px 8px;border-bottom:1px solid #e0eaf5;margin-bottom:4px">';
    html += '<button onclick="_flowProgSelectAll(true)" style="flex:1;padding:2px 6px;border:1px solid #98b2d2;border-radius:3px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">All</button>';
    html += '<button onclick="_flowProgSelectAll(false)" style="flex:1;padding:2px 6px;border:1px solid #98b2d2;border-radius:3px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">None</button>';
    html += '</div>';
    html += '<div id="flow-prog-list">';
    _flowAllProgs.forEach(function(p) {{
      var cbId = 'flow-prog-cb-' + p.replace(/[^a-zA-Z0-9]/g,'_');
      var lots = _flowProgLotMap[p]||[];
      var lotGrpId = 'flow-lot-grp-' + p.replace(/[^a-zA-Z0-9]/g,'_');
      html += '<div data-prog-row="'+_escH(p)+'">';
      html += '<div style="background:#f0f4f8;padding:4px 8px;border-bottom:1px solid #e8edf5;display:flex;align-items:center;gap:4px">';
      html += '<label style="display:flex;align-items:center;gap:6px;font-size:12px;font-weight:700;color:#1a3a5c;cursor:pointer;white-space:nowrap;flex:1">';
      html += '<input type="checkbox" id="'+cbId+'" value="'+_escH(p)+'" '+(_FLOW_PROG_ACTIVE.has(p)?'checked':'')+' onchange="_flowProgCbChange(this)" style="cursor:pointer;width:13px;height:13px">';
      html += '<span>'+_escH(p)+'</span></label>';
      if(lots.length > 0) {{
        html += '<button onclick="var g=document.getElementById(\\''+lotGrpId+'\\');var open=g.style.display===\\'none\\';g.style.display=open?\\'block\\':\\'none\\';this.textContent=open?\\'&#9660;\\':\\'&#9654;\\';" '+
                'style="border:none;background:none;cursor:pointer;color:#555;font-size:10px;padding:0 4px" title="Show/hide lots">&#9654;</button>';
      }}
      html += '</div>';
      if(lots.length > 0) {{
        html += '<div id="'+lotGrpId+'" style="display:none">';
        lots.forEach(function(l) {{
          var lotKey = p+'\x00'+l;
          var lotCbId = 'flow-lot-cb-'+lotKey.replace(/[^a-zA-Z0-9]/g,'_');
          html += '<label style="display:flex;align-items:center;gap:6px;padding:3px 12px 3px 28px;font-size:11px;color:#2c4a6e;cursor:pointer;white-space:nowrap;border-bottom:1px solid #f0f4f8" '+
                  'onmouseover="this.style.background=\\'#eef5fd\\'" onmouseout="this.style.background=\\'\\'">'; 
          html += '<input type="checkbox" id="'+lotCbId+'" value="'+_escH(lotKey)+'" '+(_FLOW_LOT_ACTIVE.has(lotKey)?'checked':'')+' onchange="_flowLotCbChange(this)" style="cursor:pointer;width:12px;height:12px">';
          html += '<span>'+_escH(l)+'</span></label>';
        }});
        html += '</div>';
      }}
      html += '</div>';  // close data-prog-row wrapper
    }});
    html += '</div></div></div></div>';  // close: flow-prog-list, dd-panel, inner button-wrapper, flow-prog-filter
  }}
  html += '</div>';

  if(!_FLOW_CARDS_H || _FLOW_CARDS_H < 200) _FLOW_CARDS_H = Math.round(window.innerHeight * 0.85);
  html += '<div id="flow-cards-section" style="height:'+_FLOW_CARDS_H+'px;overflow-y:auto;overflow-x:auto;border:1px solid #dbe7f4;border-radius:6px 6px 0 0;margin-top:0">';
  html += '<div id="flow-panels-wrap" style="padding:0;display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start">';
  mods.forEach(function(mod, mi){{
    var fd = FLOW_DATA[mod];
    var _instDenom = _flowInstLowestDenoms(fd, ak);
    var _ibin1D = IBIN1_COL ? _ibin1Denom(ak) : null;
    var idSafe = String(mod||'').replace(/[^a-zA-Z0-9_-]/g,'_');
    var insts = fd.instances || [];
    // collect all unique freq_mhz values, sorted descending
    var freqSet = {{}};
    insts.forEach(function(inst){{
      (inst.freqs||[]).forEach(function(f){{ freqSet[f.freq_mhz] = f.freq_label; }});
    }});
    var freqList = Object.keys(freqSet).map(Number).sort(function(a,b){{return b-a;}});
    // build lookup: freqLookup[instIdx][freq_mhz] = freq obj
    var freqLookup = {{}};
    insts.forEach(function(inst){{
      freqLookup[inst.idx] = {{}};
      (inst.freqs||[]).forEach(function(f){{ freqLookup[inst.idx][f.freq_mhz] = f; }});
    }});
    var _upmHdr = fd.upm_as_pct ? 'UPM Med %' : 'UPM Med';
      var modArg = _flowInlineArg(mod);
        var _isVis = _FLOW_SUMMARY_CARD_VIS[mod]!==false;
        var _cardW = _flowCardWidth(mod);
        var _cardH = _FLOW_CARD_H[mod];
        html += '<div id="flow-mod-'+idSafe+'" style="display:'+(_isVis?'block':'none')+';flex:0 0 auto;width:'+_cardW+'px;'+(_cardH?'height:'+_cardH+'px;':'')+';min-width:440px;min-height:220px;max-width:100%;resize:both;overflow:auto">';
        html += '<div class="flow-card" style="width:100%;display:flex;flex-direction:column;background:#fff;border:1px solid #c9d7e8;border-radius:6px;overflow:hidden">';
      html += '<div style="background:#1a4a7a;color:#fff;padding:8px 12px;font-weight:bold;font-size:13px;display:flex;align-items:center;justify-content:space-between;gap:10px">'
         + '<span>'+_escH(_flowSubTabLabel(mod,fd))+'<span style="font-weight:normal;font-size:11px;opacity:.85">  Speed Flow (Freq/Vmin)</span></span>'
         + '<span style="display:flex;align-items:center;gap:6px">'
         + '<button onmousedown="_flowStartCardResize(event,\\''+modArg+'\\')" title="Drag to resize card (width \u00d7 height)" style="padding:2px 8px;border:1px solid rgba(255,255,255,0.6);border-radius:4px;background:rgba(255,255,255,0.15);color:#fff;font-size:11px;cursor:nwse-resize">&#8663; Resize</button>'
         + '<button onclick="_flowShowOnlyCard(\\''+modArg+'\\')" style="padding:2px 8px;border:1px solid rgba(255,255,255,0.6);border-radius:4px;background:rgba(255,255,255,0.15);color:#fff;font-size:11px;cursor:pointer">Only</button>'
         + '<button onclick="_flowSetCardVisible(\\''+modArg+'\\',false)" style="padding:2px 8px;border:1px solid rgba(255,255,255,0.6);border-radius:4px;background:rgba(255,255,255,0.15);color:#fff;font-size:11px;cursor:pointer">Hide</button>'
         + '</span></div>';
        html += '<div style="padding:10px 12px 0 12px">'+_flowPassVminControlsHtml(mod)+'</div>';
        html += '<div class="flow-summary-table-wrap" style="padding:12px;overflow-x:auto;overflow-y:hidden;display:'+(_FLOW_SUMMARY_SHOW_TABLE?'block':'none')+'">';
    html += '<table style="border-collapse:collapse;font-size:12px;min-width:400px">';
    // thead — two-row: group header (colspan 3) then N | Vmin Med | UPM Med
    html += '<thead><tr>';
    html += '<th rowspan="2" style="padding:8px 16px;text-align:left;background:#1a4a7a;color:#fff;'+
            'border:1px solid #14396b;font-size:12px;white-space:nowrap">Freq</th>';
    insts.forEach(function(inst, ci){{
      var hc = _FLOW_INST_PAL[ci % _FLOW_INST_PAL.length];
      html += '<th colspan="3" style="padding:8px 16px;text-align:center;background:'+hc+';color:#fff;'+
              'border:1px solid rgba(0,0,0,0.25);font-size:12px;white-space:nowrap">'+
              _escH(inst.label)+'</th>';
    }});
    html += '</tr><tr>';
    insts.forEach(function(inst, ci){{
      var hc = _FLOW_INST_PAL[ci % _FLOW_INST_PAL.length];
      html += '<th style="padding:4px 10px;text-align:left;background:'+hc+';color:#fff;'+
              'border:1px solid rgba(0,0,0,0.2);font-weight:normal;font-size:11px;opacity:0.85">N</th>';
      html += '<th style="padding:4px 10px;text-align:left;background:'+hc+';color:#fff;'+
              'border:1px solid rgba(0,0,0,0.2);font-weight:normal;font-size:11px;opacity:0.85">Vmin Med</th>';
            html += '<th style="padding:4px 10px;text-align:left;background:'+hc+';color:#fff;'+
              'border:1px solid rgba(0,0,0,0.2);font-weight:normal;font-size:11px;opacity:0.85">'+_upmHdr+'</th>';
        }});
    html += '</tr></thead><tbody>';
    var _rdInstSt = _flowPassVminState(mod); // roll-down state for top table badges
    // one row per frequency
    freqList.forEach(function(fmhz, fi){{
      var bg = fi%2===0 ? '#f7fafd' : '#fff';
      html += '<tr style="background:'+bg+'">';
      html += '<td style="padding:7px 16px;font-weight:bold;color:#1a4a7a;border:1px solid #dde;'+
              'white-space:nowrap">'+_escH(freqSet[fmhz])+'</td>';
      insts.forEach(function(inst, ci){{
        var cc = _FLOW_INST_PAL[ci % _FLOW_INST_PAL.length];
        var fr = (freqLookup[inst.idx]||{{}})[fmhz];
        if(fr){{
          var _frRows=IBIN1_PKG_KEY_SET.size?(fr.rows||[]).filter(function(r){{return IBIN1_PKG_KEY_SET.has((r[8]||'')+'|'+(r[1]||'')+'|'+r[0]);}}):fr.rows;
          if(_flowHasFilter) _frRows=_frRows.filter(function(r){{
            var p=String(r[8]||''); var l=String(r[1]||'');
            return _FLOW_PROG_ACTIVE.has(p) && _FLOW_LOT_ACTIVE.has(p+'\x00'+l);
          }});
          var fs=_filtStats(_frRows,ak);
          var upmMed=_filtUpmMed(_frRows,ak);
          if(fs){{
            var _rdCands=_rdInstSt.enabled?_frRows.filter(function(r){{return ak.has(_flowNormKey(r))&&r[5]!==null&&r[5]!==undefined&&r[5]===r[5]&&r[5]>_rdInstSt.thresh;}}):[];
            var _nRdOut=_rdCands.length;
            var _rdRng='';
            if(_nRdOut>0){{
              var _rdVs=_rdCands.map(function(r){{return r[5];}}).sort(function(a,b){{return a-b;}});
              _rdRng='<span title="Roll-down candidate Vmin range: '+_fmtVmin(_rdVs[0])+' \u2013 '+_fmtVmin(_rdVs[_rdVs.length-1])+'" '+
                     'style="font-size:9px;color:#fff;background:#e67e22;border-radius:3px;padding:1px 5px;margin-left:2px;white-space:nowrap">'+
                     _fmtVmin(_rdVs[0])+'\u2013'+_fmtVmin(_rdVs[_rdVs.length-1])+'</span>';
            }}
            html += '<td style="padding:7px 14px;text-align:left;border:1px solid #dde;'+
                    'border-left:3px solid '+cc+';cursor:pointer;white-space:nowrap" '+
                    'onclick="showFlowDetail(\\''+modArg+'\\','+inst.idx+','+fmhz+')" '+
                    'onmouseover="this.style.background=&apos;#d6eaf8&apos;;this.style.color=&apos;#1a4a7a&apos;" '+
                    'onmouseout="this.style.background=&apos;'+bg+'&apos;;this.style.color=&apos;&apos;">'+
                    '<span style="color:#555">'+fs.n.toLocaleString()+'</span>'+
                    '<span style="color:#888;font-size:10px"> ('+(fs.n/(_ibin1D!=null?_ibin1D:(_instDenom[inst.idx]||1))*100).toFixed(1)+'%)</span>'+
                    (fr.n_valid>_frRows.length?'<span title="rows capped" style="color:#f39c12;font-size:9px"> *</span>':'')+
                    ' <span style="color:#3498db;font-size:10px">&#9654;</span>'+
                    (_nRdOut>0?'<span title="'+_nRdOut+' unit'+(_nRdOut>1?'s':'')+' Vmin>threshold \u2192 roll-down candidates" style="font-size:9px;color:#fff;background:#e67e22;border-radius:3px;padding:1px 4px;margin-left:3px;white-space:nowrap">&#8595;'+_nRdOut+'</span>'+_rdRng:'')+
                    '</td>';
            html += '<td style="padding:6px 12px;border:1px solid #dde;white-space:nowrap;color:'+cc+';font-size:11px">'+_fmtVmin(fs.med)+'</td>';
            html += '<td style="padding:6px 12px;border:1px solid #dde;white-space:nowrap;color:'+cc+';font-size:11px">'+
                    (upmMed==null?'—':(_fmtUpm(upmMed)+(fd.upm_as_pct?'%':'')))+'</td>';
          }} else {{
            html += '<td style="padding:7px 14px;border:1px solid #dde;border-left:3px solid '+cc+';color:#bbd">—</td>';
            html += '<td style="padding:6px 12px;border:1px solid #dde;color:#bbd;text-align:center">—</td>';
            html += '<td style="padding:6px 12px;border:1px solid #dde;color:#bbd;text-align:center">—</td>';
          }}
        }} else {{
          html += '<td style="padding:7px 14px;text-align:left;border:1px solid #dde;'+
                  'border-left:3px solid '+cc+';color:#ccc">—</td>';
          html += '<td style="padding:6px 12px;border:1px solid #dde;color:#ccc;text-align:center">—</td>';
          html += '<td style="padding:6px 12px;border:1px solid #dde;color:#ccc;text-align:center">—</td>';
        }}
      }});
      html += '</tr>';
    }});
    html += '</tbody></table>';
    // ── DCM Pass Summary table ────────────────────────────────────────────
    var ptd = _flowAdjustedPassTable(mod);
    if(ptd && Object.keys(ptd.freq_data||{{}}).length){{
      var _passDenom = _ibin1D!=null ? _ibin1D : _flowPassLowestDenom(ptd,ak);
      var sortedFmhz = Object.keys(ptd.freq_data).map(Number).sort(function(a,b){{return b-a;}});
      html += '<div style="margin-top:20px">';
      var _isCcf = /ccf/i.test(mod) || /ring/i.test(mod);
      var _isAtom = /atom/i.test(mod);
      var _grpUnit = _isAtom ? 'ATOM' : (_isCcf ? 'CCF' : 'DCM');
      var _dcmLabel=_isAtom ? {{4:'4 ATOM (all pass)',3:'3 ATOM (&#8805;3, incl 4)',2:'2 ATOM (&#8805;2, incl 3/4)',1:'Reset'}} : {{4:'4 '+_grpUnit+' (all pass)',2:'2 '+_grpUnit+' (&#8805;2, incl 4)',1:(_isCcf?'CCF pass':'Reset')}};
      var _dcmColors=_isAtom ? {{4:'#1a4a7a',3:'#2563eb',2:'#2e7d32',1:'#c62828'}} : {{4:'#1a4a7a',2:'#2e7d32',1:'#c62828'}};
      var _passCols = _isCcf ? [1] : (_isAtom ? [4,3,2] : [4,2]);
      html += '<div style="font-weight:bold;font-size:12px;color:#1a4a7a;margin-bottom:6px">'+_grpUnit+' Pass Summary <span style="font-weight:normal;font-size:10px;color:#888">(cumulative bins: higher-pass units also appear in lower qualifying bins)</span></div>';
      html += '<div style="margin-bottom:8px"><label style="font-size:11px;color:#2c3e50;display:inline-flex;align-items:center;gap:5px;cursor:pointer" title="When enabled, units passing at a higher frequency are automatically counted as passing at all lower frequencies"><input id="flow-pass-autodownflow-cb-'+idSafe+'" type="checkbox" '+((_FLOW_PASS_AUTODOWNFLOW[mod])?'checked':'')+' onchange="_flowToggleAutoDownflow(\\''+modArg+'\\',this.checked)" style="width:13px;height:13px;accent-color:#27ae60"> Enable auto-downflow <span style="color:#888;font-size:10px">(pass high freq \u2192 assume pass lower freq)</span></label></div>';
      html += '<table style="border-collapse:collapse;font-size:12px;min-width:300px">';
      html += '<thead>';
      html += '<tr>';
      html += '<th rowspan="2" style="padding:6px 14px;text-align:left;background:#1a4a7a;color:#fff;border:1px solid rgba(0,0,0,0.25);white-space:nowrap">Freq</th>';
      _passCols.forEach(function(n){{
        var dc=_dcmColors[n];
        html += '<th colspan="3" style="padding:6px 14px;text-align:center;background:'+dc+';color:#fff;border:1px solid rgba(0,0,0,0.25);white-space:nowrap">'+(_dcmLabel[n]||n+' DCM')+'</th>';
      }});
      html += '<th colspan="2" style="padding:6px 14px;text-align:center;background:#7f8c8d;color:#fff;border:1px solid rgba(0,0,0,0.25);white-space:nowrap">Below Threshold</th>';
      html += '</tr><tr>';
      _passCols.forEach(function(n){{
        var dc=_dcmColors[n];
        html += '<th style="padding:4px 10px;text-align:left;background:'+dc+';color:#fff;border:1px solid rgba(0,0,0,0.2);font-weight:normal;opacity:0.85">N</th>';
        html += '<th style="padding:4px 10px;text-align:left;background:'+dc+';color:#fff;border:1px solid rgba(0,0,0,0.2);font-weight:normal;opacity:0.85">Vmin Med</th>';
        html += '<th style="padding:4px 10px;text-align:left;background:'+dc+';color:#fff;border:1px solid rgba(0,0,0,0.2);font-weight:normal;opacity:0.85">'+_upmHdr+'</th>';
      }});
      html += '<th style="padding:4px 10px;text-align:left;background:#7f8c8d;color:#fff;border:1px solid rgba(0,0,0,0.2);font-weight:normal;opacity:0.85">N</th>';
      html += '<th style="padding:4px 10px;text-align:left;background:#7f8c8d;color:#fff;border:1px solid rgba(0,0,0,0.2);font-weight:normal;opacity:0.85">%</th>';
      html += '</tr></thead><tbody>';
      sortedFmhz.forEach(function(fmhz, fi){{
        var fd2 = ptd.freq_data[String(fmhz)];
        var bg  = fi%2===0 ? '#f7fafd' : '#fff';
        html += '<tr style="background:'+bg+'">';
        html += '<td style="padding:6px 14px;font-weight:bold;color:#1a4a7a;border:1px solid #dde;white-space:nowrap">'+_escH(fd2.freq_label)+'</td>';
        var _passRowMaxN=0; // track max qualifying N (kept for potential future use)
        _passCols.forEach(function(nDcm){{
          var dc=_dcmColors[nDcm];
          var grp = (fd2.groups||{{}})[nDcm];
          if(grp){{
            var _irows2=IBIN1_PKG_KEY_SET.size?(grp.rows||[]).filter(function(r){{return IBIN1_PKG_KEY_SET.has((r[8]||'')+'|'+(r[1]||'')+'|'+r[0]);}}):grp.rows||[];
            if(_flowHasFilter) _irows2=_irows2.filter(function(r){{return _FLOW_PROG_ACTIVE.has(String(r[8]||''));}});
            var fs2=_filtStats(_irows2,ak);
            if(fs2&&fs2.n>_passRowMaxN) _passRowMaxN=fs2.n; // accumulate max qualifying N
            var upmMed2=_filtUpmMed(_irows2,ak);
            if(fs2){{
              var _rdPkgSet2=grp.rolledSrcs?new Set(Object.keys(grp.rolledSrcs)):new Set();
              var _rdLandedVmins=_irows2.filter(function(r){{return _rdPkgSet2.has(r[0])&&ak.has(_flowNormKey(r))&&r[5]!==null&&r[5]!==undefined&&r[5]===r[5];}}).map(function(r){{return r[5];}}).sort(function(a,b){{return a-b;}});
              var _vRng='';
              if(_rdLandedVmins.length>=2){{_vRng='<div style="font-size:9px;color:#e67e22;white-space:nowrap;margin-top:2px">'+_fmtVmin(_rdLandedVmins[0])+'\u2013'+_fmtVmin(_rdLandedVmins[_rdLandedVmins.length-1])+'</div>';}}
              var _rdB2='';
              if(grp.rolledSrcs&&Object.keys(grp.rolledSrcs).length){{
                // Compute badge counts from filtered rows only (respects lot/wafer filter)
                var _rdFrom2={{}};
                _irows2.forEach(function(r){{var s=grp.rolledSrcs[r[0]];if(s)_rdFrom2[s]=(_rdFrom2[s]||0)+1;}});
                if(Object.keys(_rdFrom2).length){{
                  var _rdPal2=['#e67e22','#9b59b6','#1abc9c','#e74c3c','#3498db','#f39c12'];
                  var _rdSrcs2=Object.keys(_rdFrom2).sort(function(a,b){{var fa=parseFloat(a),fb=parseFloat(b);return fb-fa;}});
                  _rdSrcs2.forEach(function(sl,si){{
                    var cnt=_rdFrom2[sl];
                    var clr=_rdPal2[si%_rdPal2.length];
                    _rdB2+='<span title="'+cnt+' unit'+(cnt>1?'s':'')+' rolled in from '+_escH(sl)+'" style="font-size:9px;color:#fff;background:'+clr+';border-radius:3px;padding:1px 4px;margin-left:3px;white-space:nowrap">+'+cnt+'&#8595;'+_escH(sl)+'</span>';
                  }});
                }}
              }}
              html += '<td style="padding:7px 14px;text-align:left;border:1px solid #dde;'+
                      'border-left:3px solid '+dc+';cursor:pointer;white-space:nowrap" '+
                      'onclick="showPassDetail(\\''+modArg+'\\','+fmhz+','+nDcm+')" '+
                      'onmouseover="this.style.background=&apos;#d6eaf8&apos;;this.style.color=&apos;#1a4a7a&apos;" '+
                      'onmouseout="this.style.background=&apos;'+bg+'&apos;;this.style.color=&apos;&apos;">'+
                      '<span style="color:#555">'+fs2.n.toLocaleString()+'</span>'+
                      '<span style="color:#888;font-size:10px"> ('+(fs2.n/_passDenom*100).toFixed(1)+'%)</span>'+
                      ' <span style="color:#3498db;font-size:10px">&#9654;</span>'+_rdB2+_vRng+'</td>';
              html += '<td style="padding:6px 12px;border:1px solid #dde;color:'+dc+';font-size:11px">'+_fmtVmin(fs2.med)+'</td>';
              html += '<td style="padding:6px 12px;border:1px solid #dde;white-space:nowrap;color:'+dc+';font-size:11px">'+
                      (upmMed2==null?'—':(_fmtUpm(upmMed2)+(fd.upm_as_pct?'%':'')))+'</td>';
            }} else {{
              html += '<td style="padding:7px 14px;border:1px solid #dde;border-left:3px solid '+dc+';color:#bbd">—</td>';
              html += '<td style="padding:6px 12px;border:1px solid #dde;color:#bbd;text-align:center">—</td>';
              html += '<td style="padding:6px 12px;border:1px solid #dde;color:#bbd;text-align:center">—</td>';
            }}
          }} else {{
            html += '<td style="padding:7px 14px;border:1px solid #dde;border-left:3px solid '+dc+';color:#ccc">—</td>';
            html += '<td style="padding:6px 12px;border:1px solid #dde;color:#ccc;text-align:center">—</td>';
            html += '<td style="padding:6px 12px;border:1px solid #dde;color:#ccc;text-align:center">—</td>';
          }}
        }});
        // Below Threshold = units tested at this freq (from FLOW_DATA individual
        // DCMs) that don't qualify ≥2 DCMs in pass summary after roll-down.
        var _flowMod=FLOW_DATA[mod]||{{}};
        var _flowFreqRows=[];
        (_flowMod.instances||[]).forEach(function(inst){{
          (inst.freqs||[]).forEach(function(f){{
            if(f.freq_mhz===fmhz){{
              (f.rows||[]).forEach(function(r){{_flowFreqRows.push(r);}});
            }}
          }});
        }});
        var _fltRows=IBIN1_PKG_KEY_SET.size?_flowFreqRows.filter(function(r){{return IBIN1_PKG_KEY_SET.has((r[8]||'')+'|'+(r[1]||'')+'|'+r[0]);}}):_flowFreqRows;
        if(_flowHasFilter) _fltRows=_fltRows.filter(function(r){{return _FLOW_PROG_ACTIVE.has(String(r[8]||''));}});
        _fltRows=_fltRows.filter(function(r){{return ak.has(_flowNormKey(r));}});
        var _freqSeen=new Set();
        _fltRows.forEach(function(r){{_freqSeen.add((r[8]!=null?String(r[8]):'')+'\x7c'+String(r[0]||''));}});
        var _freqTotal=_freqSeen.size;
        var _rejN=_freqTotal-_passRowMaxN;
        if(_rejN<0) _rejN=0;
        if(_freqTotal>0){{
          var _rejPct=(_rejN/_freqTotal*100).toFixed(1);
          html += '<td style="padding:7px 14px;border:1px solid #dde;border-left:3px solid #7f8c8d;white-space:nowrap"><span style="color:#7f8c8d">'+_rejN.toLocaleString()+'</span></td>';
          html += '<td style="padding:6px 12px;border:1px solid #dde;color:#7f8c8d;font-size:11px">'+_rejPct+'%</td>';
        }} else {{
          html += '<td style="padding:7px 14px;border:1px solid #dde;border-left:3px solid #7f8c8d;color:#bbd">—</td>';
          html += '<td style="padding:6px 12px;border:1px solid #dde;color:#bbd;text-align:center">—</td>';
        }}
        html += '</tr>';
      }});
      html += '</tbody></table></div>';
    }}
    html += '</div>';  // close flow-summary-table-wrap
    html += '</div></div>';
  }});
  html += '</div>';  // close flow-panels-wrap
  html += '</div>';  // close flow-cards-section
  html += '<div data-flow-trh="1" style="height:8px;cursor:ns-resize;background:#d0deef;border-left:1px solid #dbe7f4;border-right:1px solid #dbe7f4;display:flex;align-items:center;justify-content:center;user-select:none">'+
          '<span style="font-size:9px;color:#90a4ae;pointer-events:none">&#9776;</span></div>';
  html += '<div style="padding:10px;border:1px solid #dbe7f4;border-radius:0 0 6px 6px;border-top:none;background:#fbfdff">'+
          '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:4px">'+
          '<button onclick="_flowToggleSummaryPlot()" id="flow-summary-plot-arrow" title="Expand/collapse summary plot" style="border:none;background:#e8f0fa;cursor:pointer;font-size:11px;color:#1a4a7a;padding:2px 6px;border-radius:3px;line-height:1">'+(_FLOW_SUMMARY_PLOT_COLLAPSED?'&#9660;':'&#9650;')+'</button>'+
          '<span style="font-weight:bold;font-size:12px;color:#1a4a7a">Summary Frequency vs Vmin Median</span>'+
          '<span style="font-size:11px;color:#5d6d7e;margin-left:4px">Plot modules:</span>'+
          '<span id="flow-mod-selectors" style="display:flex;gap:8px;flex-wrap:wrap"></span>'+
          '<span style="width:1px;background:#cfdced;align-self:stretch;margin:0 4px"></span>'+
          '<label style="font-size:11px;color:#2c3e50;display:flex;align-items:center;gap:4px"><input id="flow-group-by-mat-cb" type="checkbox" '+(_FLOW_SUMMARY_GROUP_MODE==='material'?'checked':'')+' onchange="_flowSetSummaryGroupMode(this.checked?\\'material\\':\\'module\\')" style="width:13px;height:13px;accent-color:#3498db">By material</label>'+
          '<span style="width:1px;background:#cfdced;align-self:stretch;margin:0 4px"></span>'+
          '<label style="font-size:11px;color:#2c3e50;display:flex;align-items:center;gap:4px"><input id="flow-normalize-upm-cb" type="checkbox" '+(_FLOW_SUMMARY_NORMALIZE_UPM?'checked':'')+' onchange="_flowSetNormalizeUpm(this.checked,+document.getElementById(\\'flow-normalize-upm-pct\\').value)" style="width:13px;height:13px;accent-color:#8e44ad">Norm to UPM%</label>'+
          '<input id="flow-normalize-upm-pct" type="number" min="70" max="115" step="0.5" value="'+_FLOW_SUMMARY_NORMALIZE_UPM_PCT+'" '+(!_FLOW_SUMMARY_NORMALIZE_UPM?'disabled':'')+' onchange="_flowSetNormalizeUpm(document.getElementById(\\'flow-normalize-upm-cb\\').checked,+this.value)" style="width:62px;padding:1px 4px;font-size:11px;border:1px solid #c5d5ea;border-radius:3px">'+
          '</div>'+
          '<div id="flow-summary-plot-body" style="display:'+(_FLOW_SUMMARY_PLOT_COLLAPSED?'none':'block')+'">'+
          '<div id="flow-vf-controls-row" style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:8px;padding:4px 0">'+
          _buildVfControlsHtml()+
          '</div>'+
          '<div id="flow-summary-plot-slot"></div>'+
          '</div>'+
          '</div>';
  html += '</div>';
  _safeInnerHTML(body, html);
  _initFlowTableResize();
  var rw = document.getElementById('flow-restore-wrap');
  if(rw){{
    var rhtml = '';
    mods.forEach(function(mod){{
      var idSafe = _flowSafeId(mod);
      var fd = FLOW_DATA[mod]||{{}};
      var modArg = _flowInlineArg(mod);
      var isVis = _FLOW_SUMMARY_CARD_VIS[mod]!==false;
      rhtml += '<button id="flow-restore-chip-'+idSafe+'" onclick="_flowSetCardVisible(\\''+modArg+'\\',true)" style="display:'+(isVis?'none':'inline-flex')+';padding:2px 8px;border:1px solid #98b2d2;border-radius:999px;background:#fff;color:#1a4a7a;font-size:11px;cursor:pointer">Show '+_escH(_flowSubTabLabel(mod,fd))+'</button>';
    }});
    _safeInnerHTML(rw, rhtml);
  }}
  var ms = document.getElementById('flow-mod-selectors');
  if(ms){{
    var mshtml = '';
    mods.forEach(function(mod){{
      var idSafe = _flowSafeId(mod);
      var fd = FLOW_DATA[mod]||{{}};
      var modArg = _flowInlineArg(mod);
      var checked = !!_FLOW_SUMMARY_PLOT_SEL[mod];
      mshtml += '<label style="font-size:11px;color:#2c3e50;display:flex;align-items:center;gap:4px"><input id="flow-summary-mod-'+idSafe+'" type="checkbox" '+(checked?'checked':'')+' onchange="_flowToggleSummaryMod(\\''+modArg+'\\',this.checked)" style="width:13px;height:13px;accent-color:#3498db">'+_escH(_flowSubTabLabel(mod,fd))+'</label>';
    }});
    _safeInnerHTML(ms, mshtml);
  }}
  _flowToggleTablesAll(_FLOW_SUMMARY_SHOW_TABLE);
  _flowToggleSummaryOverlay(_FLOW_SUMMARY_OVERLAY_ALL);
  if(!_FLOW_SUMMARY_PLOT_COLLAPSED) _renderFlowSummaryPlot(mods);
}}

function _flowChartPassLabel(mod, nDcm){{
  if(/atom/i.test(mod)){{
    if(nDcm===4) return 'Premium (4/4 ATOM)';
    if(nDcm===3) return '3 ATOM (>=3, incl 4)';
    if(nDcm===2) return '2 ATOM (>=2, incl 3/4)';
    if(nDcm===1) return 'Reset';
  }}
  if(/ccf/i.test(mod)||/ring/i.test(mod)){{
    if(nDcm===1) return 'CCF pass';
    return String(nDcm)+' CCF';
  }}
  if(nDcm===4) return 'Premium (4/4 DCM)';
  if(nDcm===2) return '2 DCM (>=2, incl 4)';
  if(nDcm===1) return 'Reset';
  return String(nDcm)+' pass';
}}

function buildFlowChartTab(){{
  var body=document.getElementById('tab-flow-chart-body');
  if(!body) return;
  var mods=_flowSortMods(Object.keys(FLOW_DATA||{{}}));
  if(!mods.length){{
    _safeInnerHTML(body,'<p style="padding:20px;color:#888">No Vmin flow data available.</p>');
    return;
  }}
  var ak=activeKeys();
  var html='<div style="padding:10px">';
  html+='<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">';
  mods.forEach(function(mod){{
    var fd=FLOW_DATA[mod]||{{}};
      var idSafe=String(mod||'').replace(/[^a-zA-Z0-9_-]/g,'_');
    var on=(_FLOW_CHART_MOD_ACTIVE===mod)||(!_FLOW_CHART_MOD_ACTIVE&&mod===mods[0]);
      html+='<button id="flow-chart-mod-btn-'+idSafe+'" onclick="_showFlowChartMod(&apos;'+mod+'&apos;)" '+
          'style="padding:5px 14px;border:1px solid #98b2d2;border-radius:4px;cursor:pointer;font-size:12px;font-weight:bold;'+
        'background:'+(on?'#1a4a7a':'#d0e4f8')+';color:'+(on?'#fff':'#1a4a7a')+'">'+_escH(_flowSubTabLabel(mod,fd))+'</button>';
  }});
  html+='</div>';

  mods.forEach(function(mod){{
    var fd=FLOW_DATA[mod]||{{}};
    var _instDenom = _flowInstLowestDenoms(fd, ak);
    var ptd=(PASS_TABLE||{{}})[mod];
    var _passDenom = IBIN1_COL ? _ibin1Denom(ak) : _flowPassLowestDenom(ptd,ak);
    var insts=fd.instances||[];
    var freqSet={{}};
    insts.forEach(function(inst){{
      (inst.freqs||[]).forEach(function(f){{freqSet[f.freq_mhz]=f.freq_label;}});
    }});
    var freqList=Object.keys(freqSet).map(Number).sort(function(a,b){{return b-a;}});
    var on=(_FLOW_CHART_MOD_ACTIVE===mod)||(!_FLOW_CHART_MOD_ACTIVE&&mod===mods[0]);
    html+='<div id="flow-chart-mod-'+mod+'" style="display:'+(on?'block':'none')+'">';
    html+='<div style="font-size:11px;color:#5d6d7e;margin:2px 0 10px 0">Cards are per-frequency; pass buckets are classified within the same frequency only.</div>';

    freqList.forEach(function(fmhz, fi){{
      var bg=fi%2===0?'#f7fafd':'#fff';
      html+='<div style="display:flex;align-items:flex-start;gap:10px;padding:8px;border:1px solid #e3ebf5;border-radius:6px;background:'+bg+';margin-bottom:8px">';
      html+='<div style="flex:0 0 72px;font-weight:bold;color:#1a4a7a;padding-top:6px">'+_escH(freqSet[fmhz])+'</div>';
      html+='<div style="flex:1;display:flex;gap:8px;flex-wrap:wrap">';

      insts.forEach(function(inst, ci){{
        var cc=_FLOW_INST_PAL[ci % _FLOW_INST_PAL.length];
        var fr=null;
        (inst.freqs||[]).forEach(function(f){{if(f.freq_mhz===fmhz)fr=f;}});
        if(!fr) return;
        var fs=_filtStats(fr.rows||[],ak);
        if(!fs) return;
        html+='<div class="flow-chart-card" onclick="showFlowDetail(&apos;'+mod+'&apos;,'+inst.idx+','+fmhz+')" '+
              'style="border-left:4px solid '+cc+'" title="Open detailed plot">';
        html+='<div style="padding:6px 8px 2px 8px;font-size:11px;color:#2c3e50;font-weight:bold">'+_escH(inst.label)+'</div>';
        html+='<div style="padding:0 8px 8px 8px;font-size:11px">'+
              '<div><b>'+fs.n.toLocaleString()+'</b> <span style="color:#7f8c8d">('+(fs.n/(_instDenom[inst.idx]||1)*100).toFixed(1)+'%)</span></div>'+ 
            '<div style="color:'+cc+'">'+_fmtVmin(fs.med)+'</div>'+ 
              '</div>';
        html+='</div>';
      }});

      var fd2=ptd && ptd.freq_data ? ptd.freq_data[String(fmhz)] : null;
      var passOrder=_isAtom ? [4,3,2] : [4,2];
      passOrder.forEach(function(nDcm){{
        if(!fd2 || !fd2.groups || !fd2.groups[nDcm]) return;
        var _irows2=IBIN1_PKG_KEY_SET.size?(fd2.groups[nDcm].rows||[]).filter(function(r){{return IBIN1_PKG_KEY_SET.has((r[8]||'')+'|'+(r[1]||'')+'|'+r[0]);}}):fd2.groups[nDcm].rows||[];
        var fs2=_filtStats(_irows2,ak);
        if(!fs2) return;
        var lbl=_flowChartPassLabel(mod,nDcm);
        var dc=nDcm===4?'#1a4a7a':(nDcm===3?'#2563eb':(nDcm===2?'#2e7d32':'#c62828'));
        var fill=nDcm===4?'#eaf1fb':(nDcm===3?'#eef3ff':(nDcm===2?'#edf8f0':'#fdeeee'));
        html+='<div class="flow-chart-card pass" onclick="showPassDetail(&apos;'+mod+'&apos;,'+fmhz+','+nDcm+')" '+
              'style="border-left:4px solid '+dc+';background:'+fill+'" title="Open detailed plot">';
        html+='<div style="padding:6px 8px 2px 8px;font-size:11px;color:#2c3e50;font-weight:bold">'+_escH(lbl)+'</div>';
        html+='<div style="padding:0 8px 8px 8px;font-size:11px">'+
              '<div><b>'+fs2.n.toLocaleString()+'</b> <span style="color:#7f8c8d">('+(fs2.n/_passDenom*100).toFixed(1)+'%)</span></div>'+
            '<div style="color:'+dc+'">'+_fmtVmin(fs2.med)+'</div>'+
              '</div>';
        html+='</div>';
      }});

      html+='</div></div>';
    }});

    html+='</div>';
  }});
  html+='</div>';
  _safeInnerHTML(body,html);
  if(!_FLOW_CHART_MOD_ACTIVE || !FLOW_DATA[_FLOW_CHART_MOD_ACTIVE]) _FLOW_CHART_MOD_ACTIVE=mods[0];
  _showFlowChartMod(_FLOW_CHART_MOD_ACTIVE);
}}

function _showFlowChartMod(mod){{
  _FLOW_CHART_MOD_ACTIVE=mod;
  Object.keys(FLOW_DATA||{{}}).forEach(function(m){{
    var idSafe=String(m||'').replace(/[^a-zA-Z0-9_-]/g,'_');
    var p=document.getElementById('flow-chart-mod-'+m);
    var b=document.getElementById('flow-chart-mod-btn-'+idSafe);
    if(p) p.style.display=(m===mod)?'block':'none';
    if(b){{
      b.style.background=(m===mod)?'#1a4a7a':'#d0e4f8';
      b.style.color=(m===mod)?'#fff':'#1a4a7a';
    }}
  }});
}}

function startFlowSplit(e, idx){{
  var left  = document.getElementById('flow-card-'+idx);
  var right = document.getElementById('flow-card-'+(idx+1));
  if(!left || !right) return;
  var minW = 320;
  var startX = e.clientX;
  var startLW = left.getBoundingClientRect().width;
  var startRW = right.getBoundingClientRect().width;
  var totalW = startLW + startRW;
  var handle = e.currentTarget || e.target;
  if(handle && handle.classList) handle.classList.add('dragging');

  function mm(ev){{
    var dx = ev.clientX - startX;
    var lw = startLW + dx;
    var rw = startRW - dx;
    if(lw < minW){{ lw = minW; rw = totalW - lw; }}
    if(rw < minW){{ rw = minW; lw = totalW - rw; }}
    lw = Math.max(minW, lw);
    rw = Math.max(minW, rw);
    left.style.flex = '0 0 auto';
    right.style.flex = '0 0 auto';
    left.style.width = lw + 'px';
    right.style.width = rw + 'px';
    _FLOW_CARD_W[idx] = lw;
    _FLOW_CARD_W[idx+1] = rw;
  }}
  function mu(){{
    document.removeEventListener('mousemove', mm);
    document.removeEventListener('mouseup', mu);
    if(handle && handle.classList) handle.classList.remove('dragging');
  }}
  document.addEventListener('mousemove', mm);
  document.addEventListener('mouseup', mu);
  e.preventDefault();
}}
function showFlowDetail(mod, instIdx, freqMhz){{
  var fd = FLOW_DATA[mod];
  if(!fd) return;
  var inst = null;
  (fd.instances||[]).forEach(function(i){{ if(i.idx===instIdx) inst=i; }});
  if(!inst) return;
  var freq = null;
  (inst.freqs||[]).forEach(function(f){{ if(f.freq_mhz===freqMhz) freq=f; }});
  if(!freq) return;

  var _fdAk=activeKeys();
  var selRows=freq.rows.filter(function(r){{
    if(!_fdAk.has(_flowNormKey(r))) return false;
    if(_FLOW_PROG_USER_HIDDEN.size>0&&!_FLOW_PROG_ACTIVE.has(String(r[8]||''))) return false;
    return true;
  }});
  var vminVals = selRows.map(function(r){{ return r[5]; }});
  var xyRows = selRows.filter(function(r){{ return r[6]!==null&&r[6]!==undefined; }});
  var xyPts  = xyRows.map(function(r){{ return [r[6],r[5]]; }});
  var xyMats = xyRows.map(function(r){{ return (r[7]&&r[7]!=='nan'&&r[7]!=='None')?r[7]:_lotMat(r[1]); }});
  var xyProgs = xyRows.map(function(r){{ return String(r[8]||''); }});
  var _fxCid = 'xy'+(++_xyCtr);
  var xLabel = fd.upm_as_pct
    ? 'UPM 107_950 (% ref='+(fd.upm_950_ref!==null?_fmtUpm(fd.upm_950_ref):'?')+')'
    : 'UPM 107_950';

  var nShowing = selRows.length;
  var titleHtml = '&#9889; Vmin '+_escH(fd.label)+' &mdash; <b>'+_escH(inst.label)+'</b>'+
                  ' &mdash; <b>'+_escH(freq.freq_label)+'</b>'+
                  ' &mdash; n='+nShowing.toLocaleString()+
                  (freq.n_valid > freq.rows.length ? ' <span style="font-size:11px;color:#888;font-weight:normal">('+freq.n_valid.toLocaleString()+' total)</span>' : '');

  var mean_v = vminVals.length ? vminVals.reduce(function(a,b){{return a+b;}},0)/vminVals.length : 0;
  var std_v  = _std(vminVals);
  var min_v  = vminVals.length ? Math.min.apply(null,vminVals) : 0;
  var max_v  = vminVals.length ? Math.max.apply(null,vminVals) : 0;
  var sv = vminVals.slice().sort(function(a,b){{return a-b;}});
  var med_v = sv.length ? (sv.length%2===0 ? (sv[sv.length/2-1]+sv[sv.length/2])/2 : sv[Math.floor(sv.length/2)]) : 0;
  var statsHtml = '<div style="display:flex;flex-wrap:wrap;gap:20px;margin-bottom:10px;'+
    'font-size:11px;background:#f5f9ff;border:1px solid #dde;border-radius:4px;padding:7px 14px">';
  statsHtml += '<span><b>N (selected):</b> '+nShowing.toLocaleString()+'</span>';
  if(freq.n_valid > nShowing) statsHtml += '<span style="color:#888">('+freq.n_valid.toLocaleString()+' total)</span>';
  if(vminVals.length){{
    statsHtml += '<span><b>Mean:</b> '+_fmtVmin(mean_v)+' V</span>';
    statsHtml += '<span><b>Median:</b> '+_fmtVmin(med_v)+' V</span>';
    statsHtml += '<span><b>&sigma;:</b> '+_fmtVmin(std_v)+'</span>';
    statsHtml += '<span><b>Min:</b> '+_fmtVmin(min_v)+'</span>';
    statsHtml += '<span><b>Max:</b> '+_fmtVmin(max_v)+'</span>';
  }}
  statsHtml += '</div>';

  var dlBtn = '<div style="text-align:right;margin-bottom:8px">'+
    '<button onclick="downloadFlowCSV(&apos;'+mod+'&apos;,'+instIdx+','+freqMhz+')" '+
    'style="padding:5px 14px;background:#1a4a7a;color:#fff;border:none;border-radius:4px;'+
    'cursor:pointer;font-size:11px">&#11123; Download CSV ('+nShowing.toLocaleString()+' rows)</button></div>';

  var bodyHtml = dlBtn + '<div style="display:flex;flex-wrap:wrap;gap:28px;padding:4px 0">';

  // Plot 1: Vmin distribution histogram
  bodyHtml += '<div>';
  bodyHtml += '<div style="font-weight:bold;font-size:12px;color:#2c3e50;margin-bottom:6px">'+
    'Vmin '+_escH(freq.freq_label)+' Distribution'+
    ' <span style="font-weight:normal;color:#888;font-size:11px">(n='+vminVals.length.toLocaleString()+')</span></div>';
  bodyHtml += _buildFlowUPMHist(vminVals, 340, 80);
  bodyHtml += '<div id="xy-modal-stats-'+_fxCid+'"></div>';
  bodyHtml += '</div>';

  // Plot 2: UPM 107_950 vs Vmin scatter (interactive)
  bodyHtml += '<div style="flex:1;min-width:460px">'+_xyContainer(_fxCid, _escH(xLabel)+' vs Vmin '+_escH(freq.freq_label), xyPts.length)+'</div>';

  bodyHtml += '</div>';

  _safeInnerHTML(document.getElementById('fm-title'), titleHtml);
  _safeInnerHTML(document.getElementById('fm-body'),  bodyHtml);
  document.getElementById('fm-overlay').style.display = 'block';
  if(xyPts.length) {{
    _xyInit(_fxCid, xyPts, xyMats, 460, 260, freq.freq_label, xLabel, undefined, xyProgs);
    if(_XY_STATE[_fxCid]) _XY_STATE[_fxCid].nTotal=vminVals.length;
    _xyUpdateModalStats(_fxCid);
  }}
  _XY_ACTIVE_REBUILD = function(){{ showFlowDetail(mod, instIdx, freqMhz); }};
}}

function showPassDetail(mod, freqMhz, nDcm){{
  var ptd = _flowAdjustedPassTable(mod);
  if(!ptd) return;
  var fd2 = (ptd.freq_data||{{}})[String(freqMhz)];
  if(!fd2) return;
  var grp = (fd2.groups||{{}})[nDcm];
  if(!grp) return;

  // rows: [pkg, sort_lot, sort_wafer, x, y, avg_vmin, upm_pct]
  var _pdAk=activeKeys();
  var selRows2=grp.rows.filter(function(r){{return _pdAk.has(_flowNormKey(r));}})
  var vminVals = selRows2.map(function(r){{ return r[5]; }});
  var xyRows2  = selRows2.filter(function(r){{ return r[6]!==null&&r[6]!==undefined; }});
  var xyPts    = xyRows2.map(function(r){{ return [r[6],r[5]]; }});
  var xyMats2  = xyRows2.map(function(r){{ return r[7]||_lotMat(r[1]); }});
  var xyProgs2 = xyRows2.map(function(r){{ return String(r[8]||''); }});
  var _pdCid   = 'xy'+(++_xyCtr);
  var modLabel = (FLOW_DATA[mod]||{{}}).label || mod;
  var upm_as_pct = (FLOW_DATA[mod]||{{}}).upm_as_pct;
  var upm_950_ref = (FLOW_DATA[mod]||{{}}).upm_950_ref;
  var passLabel = _flowChartPassLabel(mod, nDcm);
  var xLabel = upm_as_pct
    ? 'UPM 107_950 (% ref='+(upm_950_ref!==null?_fmtUpm(upm_950_ref):'?')+')'
    : 'UPM 107_950';

  var nShowing = selRows2.length;
  var titleHtml = '&#9889; Vmin '+_escH(modLabel)+' &mdash; <b>'+_escH(passLabel)+'</b>'+
                  ' &mdash; <b>'+_escH(fd2.freq_label)+'</b>'+
                  ' &mdash; n='+nShowing.toLocaleString()+
                  (grp.n_total > grp.rows.length ? ' <span style="font-size:11px;color:#888;font-weight:normal">('+grp.n_total.toLocaleString()+' total)</span>' : '');

  var mean_v = vminVals.length ? vminVals.reduce(function(a,b){{return a+b;}},0)/vminVals.length : 0;
  var std_v  = _std(vminVals);
  var min_v  = vminVals.length ? Math.min.apply(null,vminVals) : 0;
  var max_v  = vminVals.length ? Math.max.apply(null,vminVals) : 0;
  var sv2    = vminVals.slice().sort(function(a,b){{return a-b;}});
  var med_v  = sv2.length ? (sv2.length%2===0 ? (sv2[sv2.length/2-1]+sv2[sv2.length/2])/2 : sv2[Math.floor(sv2.length/2)]) : 0;

  var statsHtml = '<div style="display:flex;flex-wrap:wrap;gap:20px;margin-bottom:10px;'+
    'font-size:11px;background:#f5f9ff;border:1px solid #dde;border-radius:4px;padding:7px 14px">';
  statsHtml += '<span><b>N (selected):</b> '+nShowing.toLocaleString()+'</span>';
  if(grp.n_total > nShowing) statsHtml += '<span style="color:#888">('+grp.n_total.toLocaleString()+' total)</span>';
  if(vminVals.length){{
    statsHtml += '<span><b>Mean:</b> '+_fmtVmin(mean_v)+' V</span>';
    statsHtml += '<span><b>Median:</b> '+_fmtVmin(med_v)+' V</span>';
    statsHtml += '<span><b>&sigma;:</b> '+_fmtVmin(std_v)+'</span>';
    statsHtml += '<span><b>Min:</b> '+_fmtVmin(min_v)+'</span>';
    statsHtml += '<span><b>Max:</b> '+_fmtVmin(max_v)+'</span>';
  }}
  statsHtml += '</div>';

  var dlBtn = '<div style="text-align:right;margin-bottom:8px">'+
    '<button onclick="downloadPassCSV(&apos;'+mod+'&apos;,'+freqMhz+','+nDcm+')" '+
    'style="padding:5px 14px;background:#1a4a7a;color:#fff;border:none;border-radius:4px;'+
    'font-size:11px;cursor:pointer">&#11015; Download CSV</button></div>';

  var bodyHtml = statsHtml + dlBtn + '<div style="display:flex;flex-wrap:wrap;gap:20px">';
  bodyHtml += '<div><div style="font-weight:bold;font-size:12px;color:#2c3e50;margin-bottom:6px">'+
    'Avg Vmin '+_escH(fd2.freq_label)+' Distribution'+
    ' <span style="font-weight:normal;color:#888;font-size:11px">(n='+vminVals.length.toLocaleString()+')</span></div>';
  bodyHtml += _buildFlowUPMHist(vminVals, 340, 80)+'</div>';

  if(xyPts.length){{
    bodyHtml += '<div style="flex:1;min-width:280px">'+_xyContainer(_pdCid, _escH(xLabel)+' vs Avg Vmin '+_escH(fd2.freq_label), xyPts.length)+'</div>';
  }}
  bodyHtml += '</div>';

  _safeInnerHTML(document.getElementById('fm-title'), titleHtml);
  _safeInnerHTML(document.getElementById('fm-body'),  bodyHtml);
  document.getElementById('fm-overlay').style.display = 'block';
  if(xyPts.length) _xyInit(_pdCid, xyPts, xyMats2, 340, 200, fd2.freq_label, xLabel, undefined, xyProgs2);
}}

function showPassXY(mod, freqMhz, nDcm){{
  var ptd = _flowAdjustedPassTable(mod);
  if(!ptd) return;
  var fd2 = (ptd.freq_data||{{}})[String(freqMhz)];
  if(!fd2) return;
  var grp = (fd2.groups||{{}})[nDcm];
  if(!grp) return;
  var _pxAk=activeKeys();
  var _pxSel=grp.rows.filter(function(r){{return _pxAk.has(_flowNormKey(r));}})
  var xyRows3 = _pxSel.filter(function(r){{ return r[6]!==null&&r[6]!==undefined; }});
  var xyPts = xyRows3.map(function(r){{ return [r[6],r[5]]; }});
  var xyMats3 = xyRows3.map(function(r){{ return LOT7_MAT[((r[1]||'')+'').slice(0,7)]||'Others'; }});
  var xyProgs3 = xyRows3.map(function(r){{ return String(r[8]||''); }});
  if(!xyPts.length){{
    alert('No XY data available for this group (or none in current selection).');
    return;
  }}
  var modLabel   = (FLOW_DATA[mod]||{{}}).label || mod;
  var upm_as_pct = (FLOW_DATA[mod]||{{}}).upm_as_pct;
  var upm_950_ref= (FLOW_DATA[mod]||{{}}).upm_950_ref;
  var xLabel = upm_as_pct
    ? 'UPM 107_950 (% ref='+(upm_950_ref!==null?_fmtUpm(upm_950_ref):'?')+')'
    : 'UPM 107_950';
  var _pxCid = 'xy'+(++_xyCtr);
  var titleHtml = '&#128200; '+_escH(modLabel)+' &mdash; <b>'+_escH(_flowChartPassLabel(mod, nDcm))+'</b>'+
                  ' &mdash; <b>'+_escH(fd2.freq_label)+'</b>'+
                  ' &mdash; n='+xyPts.length.toLocaleString();
  var bodyHtml = '<div style="padding:4px 0">'+
    _xyContainer(_pxCid, _escH(xLabel)+' vs Avg Vmin '+_escH(fd2.freq_label), xyPts.length)+
    '</div>';
  document.getElementById('fm-title').innerHTML = titleHtml;
  document.getElementById('fm-body').innerHTML  = bodyHtml;
  document.getElementById('fm-overlay').style.display = 'block';
  _xyInit(_pxCid, xyPts, xyMats3, 480, 300, fd2.freq_label, xLabel, undefined, xyProgs3);
}}

function downloadPassCSV(mod, freqMhz, nDcm){{
  var ptd = _flowAdjustedPassTable(mod);
  if(!ptd) return;
  var fd2 = (ptd.freq_data||{{}})[String(freqMhz)];
  if(!fd2) return;
  var grp = (fd2.groups||{{}})[nDcm];
  if(!grp) return;

  var upm_as_pct = (FLOW_DATA[mod]||{{}}).upm_as_pct;
  var upmHdr = upm_as_pct ? 'UPM_107_950_pct' : 'UPM_107_950';
  var lines = ['Unit,Sort_Lot,Sort_Wafer,Sort_X,Sort_Y,AvgVmin_V,'+upmHdr+',Material'];
  grp.rows.forEach(function(r){{
    var mat = LOT7_MAT[((r[1]||'')+'').slice(0,7)] || 'Others';
    lines.push([
      r[0]!==null&&r[0]!==undefined?r[0]:'',
      r[1]!==null&&r[1]!==undefined?r[1]:'',
      r[2]!==null&&r[2]!==undefined?r[2]:'',
      r[3]!==null&&r[3]!==undefined?r[3]:'',
      r[4]!==null&&r[4]!==undefined?r[4]:'',
      r[5]!==null&&r[5]!==undefined?r[5]:'',
      r[6]!==null&&r[6]!==undefined?r[6]:'',
      mat
    ].join(','));
  }});
  var blob = new Blob([lines.join('\\n')], {{type:'text/csv'}});
  var url  = URL.createObjectURL(blob);
  var a    = document.createElement('a');
  a.href   = url;
  var modLabel = (FLOW_DATA[mod]||{{}}).label || mod;
  a.download = modLabel+'_'+nDcm+'DCM_'+fd2.freq_label+'.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}}

function downloadFlowCSV(mod, instIdx, freqMhz){{
  var fd = FLOW_DATA[mod];
  if(!fd) return;
  var inst = null;
  (fd.instances||[]).forEach(function(i){{ if(i.idx===instIdx) inst=i; }});
  if(!inst) return;
  var freq = null;
  (inst.freqs||[]).forEach(function(f){{ if(f.freq_mhz===freqMhz) freq=f; }});
  if(!freq) return;

  var upmHdr = fd.upm_as_pct
    ? 'UPM_107_950_pct'
    : 'UPM_107_950';
  var lines = ['Unit,Sort_Lot,Sort_Wafer,Sort_X,Sort_Y,Vmin_V,'+upmHdr+',Material'];
  freq.rows.forEach(function(r){{
    var mat = LOT7_MAT[((r[1]||'')+'').slice(0,7)] || 'Others';
    lines.push([
      r[0]!==null&&r[0]!==undefined?r[0]:'',
      r[1]!==null&&r[1]!==undefined?r[1]:'',
      r[2]!==null&&r[2]!==undefined?r[2]:'',
      r[3]!==null&&r[3]!==undefined?r[3]:'',
      r[4]!==null&&r[4]!==undefined?r[4]:'',
      r[5]!==null&&r[5]!==undefined?r[5]:'',
      r[6]!==null&&r[6]!==undefined?r[6]:'',
      mat
    ].join(','));
  }});
  var blob = new Blob([lines.join('\\n')], {{type:'text/csv'}});
  var url  = URL.createObjectURL(blob);
  var a    = document.createElement('a');
  a.href   = url;
  a.download = fd.label+'_'+inst.label+'_'+freq.freq_label+'.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}}

function _buildFlowUPMHist(vals, W, H){{
  if(!vals||!vals.length) return '<div style="color:#888;font-size:10px">No data</div>';
  var mn=Math.min.apply(null,vals), mx=Math.max.apply(null,vals);
  var mean=vals.reduce(function(a,b){{return a+b;}},0)/vals.length;
  var sd=_std(vals);
  if(mn===mx){{mn-=0.5;mx+=0.5;}}
  var nBins=Math.min(40,Math.max(10,Math.round(Math.sqrt(vals.length))));
  var step=(mx-mn)/nBins;
  var bins=new Array(nBins).fill(0);
  vals.forEach(function(v){{
    var b=Math.min(nBins-1,Math.floor((v-mn)/step));
    bins[b]++;
  }});
  var maxB=Math.max.apply(null,bins);
  var PA=32, svgW=W, svgH=H+PA;
  var bW=(svgW-2)/nBins;
  var hscale=maxB>0?H/maxB:1;
  var svg='<svg width="'+svgW+'" height="'+svgH+'" style="display:block;font-family:sans-serif">';
  bins.forEach(function(c,i){{
    var x=1+i*bW, bh=c*hscale;
    svg+='<rect x="'+x.toFixed(1)+'" y="'+(H-bh+2).toFixed(1)+'" width="'+(bW-1).toFixed(1)+
         '" height="'+bh.toFixed(1)+'" fill="#3498db" rx="1"/>';
  }});
  // mean line
  var mx_pos=1+(mean-mn)/step*bW;
  if(mx_pos>=0&&mx_pos<=svgW){{
    svg+='<line x1="'+mx_pos.toFixed(1)+'" y1="2" x2="'+mx_pos.toFixed(1)+'" y2="'+(H+2)+
         '" stroke="#e74c3c" stroke-width="1.5" stroke-dasharray="3,2"/>';
  }}
  // ±3σ lines (orange)
  if(sd>0){{
    [-1,1].forEach(function(sgn){{
      var xp=1+(mean+sgn*3*sd-mn)/step*bW;
      if(xp>=0&&xp<=svgW){{
        svg+='<line x1="'+xp.toFixed(1)+'" y1="2" x2="'+xp.toFixed(1)+'" y2="'+(H+2)+
             '" stroke="#f39c12" stroke-width="1" stroke-dasharray="2,2"/>';
      }}
    }});
  }}
  // axis labels
  svg+='<text x="2" y="'+(H+PA-18)+'" font-size="9" fill="#888">'+mn.toFixed(3)+'</text>';
  svg+='<text x="'+(svgW/2)+'" y="'+(H+PA-18)+'" font-size="9" fill="#e74c3c" text-anchor="middle">'+
      '&#956;='+_fmtVmin(mean)+'</text>';
  svg+='<text x="'+(svgW-2)+'" y="'+(H+PA-18)+'" font-size="9" fill="#888" text-anchor="end">'+
       mx.toFixed(3)+'</text>';
  // stats row
  if(sd>0){{
        svg+='<text x="'+(svgW/2)+'" y="'+(H+PA-6)+'" font-size="8.5" fill="#f39c12" text-anchor="middle">'+
          '&#963;='+_fmtVmin(sd)+'  &#177;3&#963;: ['+_fmtVmin(mean-3*sd)+', '+_fmtVmin(mean+3*sd)+']'+
         '</text>';
  }}
  svg+='</svg>';
  return svg;
}}

function _closeFlowModal(){{
  var el=document.getElementById('fm-overlay');
  if(el)el.style.display='none';
  _XY_ACTIVE_REBUILD=null;
}}
// Draggable flow panel
(function(){{
  var _fmDx=0,_fmDy=0,_fmDrg=false,_fmEl=null;
  function _fmInitDrag(){{
    _fmEl=document.getElementById('fm-overlay');
    if(!_fmEl) return;
    var hdr=_fmEl.querySelector('.pm-hdr');
    if(!hdr) return;
    hdr.addEventListener('mousedown',function(e){{
      if(e.button!==0||e.target.closest('.pm-close')) return;
      _fmDrg=true;
      var r=_fmEl.getBoundingClientRect();
      _fmDx=e.clientX-r.left; _fmDy=e.clientY-r.top;
      e.preventDefault();
    }});
    document.addEventListener('mousemove',function(e){{
      if(!_fmDrg||!_fmEl) return;
      var x=e.clientX-_fmDx, y=e.clientY-_fmDy;
      x=Math.max(0,Math.min(window.innerWidth-80,x));
      y=Math.max(0,Math.min(window.innerHeight-40,y));
      _fmEl.style.left=x+'px'; _fmEl.style.top=y+'px';
      _fmEl.style.right='auto'; _fmEl.style.bottom='auto';
    }});
    document.addEventListener('mouseup',function(){{ _fmDrg=false; }});
  }}
  document.addEventListener('DOMContentLoaded',_fmInitDrag);
}})();

function _showAggVminXY(aggKey){{
  var rest = aggKey.slice(5);           // strip '_agg_'
  var us   = rest.lastIndexOf('_');
  var mod  = rest.slice(0, us);         // 'CORE' / 'ATOM' / 'CCF'
  var freq_mhz = parseInt(rest.slice(us + 1), 10);
  var fd   = FLOW_DATA[mod];
  var meta = PCM_PARAM_META[aggKey] || {{}};
  if(!fd) return;
  var _axAk=activeKeys();
  var xyPts = [];
  var xyMats4 = [];
  var xyProgs4 = [];
  var totalN = 0;
  var freqLabel = (freq_mhz / 1000) + 'G';
  (fd.instances||[]).forEach(function(inst){{
    (inst.freqs||[]).forEach(function(fr){{
      if(fr.freq_mhz === freq_mhz){{
        freqLabel = fr.freq_label;
        var _frSel=fr.rows.filter(function(r){{
            if(!_axAk.has(_flowNormKey(r))) return false;
            if(_FLOW_PROG_USER_HIDDEN.size>0&&!_FLOW_PROG_ACTIVE.has(String(r[8]||''))) return false;
            return true;
          }})
        totalN += _frSel.length;
        _frSel.forEach(function(r){{
          if(r[6]!==null && r[6]!==undefined){{
            xyPts.push([r[6], r[5]]);
        xyMats4.push(r[7]&&r[7]!=='nan'&&r[7]!=='None'?r[7]:_lotMat(r[1]));
            xyProgs4.push(String(r[8]||''));
          }}
        }});
      }}
    }});
  }});
  var xLabel = fd.upm_as_pct
    ? 'UPM 107_950 (% ref='+(fd.upm_950_ref!==null?_fmtUpm(fd.upm_950_ref):'?')+')'
    : 'UPM 107_950';
  var _axCid = 'xy'+(++_xyCtr);
  var titleEl  = document.getElementById('fm-title');
  var bodyEl   = document.getElementById('fm-body');
  var overlay  = document.getElementById('fm-overlay');
  if(!titleEl||!bodyEl||!overlay) return;
  titleEl.innerHTML = '&#128200; '+_escH(meta.name||aggKey)+
    ' <span style="font-size:11px;font-weight:normal;color:#aaa">(n='+totalN.toLocaleString()+
    (xyPts.length<totalN?' | showing '+xyPts.length.toLocaleString():'')+
    ', all instances combined)</span>';
  // Collect all vmin vals for histogram (same rows as xyPts source)
  var vminVals4 = [];
  (fd.instances||[]).forEach(function(inst){{
    (inst.freqs||[]).forEach(function(fr){{
      if(fr.freq_mhz === freq_mhz){{
        fr.rows.filter(function(r){{
            if(!_axAk.has(_flowNormKey(r))) return false;
            if(_FLOW_PROG_USER_HIDDEN.size>0&&!_FLOW_PROG_ACTIVE.has(String(r[8]||''))) return false;
            return true;
          }}).forEach(function(r){{
          if(r[5]!==null&&r[5]!==undefined) vminVals4.push(r[5]);
        }});
      }}
    }});
  }});
  // Stats bar + histogram + XY scatter — matching freq matrix layout
  var sv4=vminVals4.slice().sort(function(a,b){{return a-b;}});
  var med4=sv4.length?(sv4.length%2===0?(sv4[sv4.length/2-1]+sv4[sv4.length/2])/2:sv4[Math.floor(sv4.length/2)]):0;
  var mean4=vminVals4.length?vminVals4.reduce(function(a,b){{return a+b;}},0)/vminVals4.length:0;
  var std4=0; if(vminVals4.length>1){{var _sm2=0;vminVals4.forEach(function(v){{_sm2+=(v-mean4)*(v-mean4);}});std4=Math.sqrt(_sm2/vminVals4.length);}}
  var min4=vminVals4.length?Math.min.apply(null,vminVals4):0;
  var max4=vminVals4.length?Math.max.apply(null,vminVals4):0;
  var statsHtml4='<div style="display:flex;flex-wrap:wrap;gap:20px;margin-bottom:10px;'+
    'font-size:11px;background:#f5f9ff;border:1px solid #dde;border-radius:4px;padding:7px 14px">';
  statsHtml4+='<span><b>N (selected):</b> '+totalN.toLocaleString()+'</span>';
  if(vminVals4.length){{
    statsHtml4+='<span><b>Mean:</b> '+_fmtVmin(mean4)+' V</span>';
    statsHtml4+='<span><b>Median:</b> '+_fmtVmin(med4)+' V</span>';
    statsHtml4+='<span><b>&sigma;:</b> '+_fmtVmin(std4)+'</span>';
    statsHtml4+='<span><b>Min:</b> '+_fmtVmin(min4)+'</span>';
    statsHtml4+='<span><b>Max:</b> '+_fmtVmin(max4)+'</span>';
  }}
  statsHtml4+='</div>';
  var bodyHtml4 = '<div style="display:flex;flex-wrap:wrap;gap:28px;padding:4px 0">';
  bodyHtml4 += '<div>';
  bodyHtml4 += '<div style="font-weight:bold;font-size:12px;color:#2c3e50;margin-bottom:6px">'+
    'Vmin '+_escH(freqLabel)+' Distribution'+
    ' <span style="font-weight:normal;color:#888;font-size:11px">(n='+vminVals4.length.toLocaleString()+')</span></div>';
  bodyHtml4 += _buildFlowUPMHist(vminVals4, 340, 80);
  bodyHtml4 += '<div id="xy-modal-stats-'+_axCid+'"></div>';
  bodyHtml4 += '</div>';
  bodyHtml4 += '<div style="flex:1;min-width:460px">'+_xyContainer(_axCid, _escH(xLabel)+' vs Vmin '+_escH(freqLabel), xyPts.length)+'</div>';
  bodyHtml4 += '</div>';
  bodyEl.innerHTML = bodyHtml4;
  overlay.style.display = 'block';
  if(xyPts.length) {{
    _xyInit(_axCid, xyPts, xyMats4, 460, 260, freqLabel, xLabel, undefined, xyProgs4);
    if(_XY_STATE[_axCid]) _XY_STATE[_axCid].nTotal=totalN;
    _xyUpdateModalStats(_axCid);
  }}
  _XY_ACTIVE_REBUILD = function(){{ _showAggVminXY(aggKey); }};
}}

function _buildFlowXYScatter(pts, W, H, vminLabel, xLabel){{
  xLabel = xLabel || 'UPM 107_950';
  if(!pts||!pts.length) return '<div style="color:#888;font-size:11px;padding:8px">No XY data available</div>';
  var xv=pts.map(function(p){{return p[0];}});
  var yv=pts.map(function(p){{return p[1];}});
  var xmin=Math.min.apply(null,xv), xmax=Math.max.apply(null,xv);
  var ymin=Math.min.apply(null,yv), ymax=Math.max.apply(null,yv);
  if(xmin===xmax){{xmin-=1;xmax+=1;}}
  if(ymin===ymax){{ymin-=0.001;ymax+=0.001;}}
  var PAL=48,PAB=30,PAT=12,PAR=8;
  var pw=W-PAL-PAR, ph=H-PAT-PAB;
  // Linear regression
  var n=pts.length, sumX=0,sumY=0,sumXY=0,sumXX=0;
  pts.forEach(function(p){{sumX+=p[0];sumY+=p[1];sumXY+=p[0]*p[1];sumXX+=p[0]*p[0];}});
  var denom=n*sumXX-sumX*sumX;
  var slope=denom!==0?(n*sumXY-sumX*sumY)/denom:0;
  var intercept=(sumY-slope*sumX)/n;
  // R²
  var ymean=sumY/n, ssTot=0, ssRes=0;
  pts.forEach(function(p){{
    ssTot+=(p[1]-ymean)*(p[1]-ymean);
    var pred=slope*p[0]+intercept;
    ssRes+=(p[1]-pred)*(p[1]-pred);
  }});
  var r2=ssTot>0?1-ssRes/ssTot:0;
  function sx(x){{return PAL+(x-xmin)/(xmax-xmin)*pw;}}
  function sy(y){{return PAT+ph-(y-ymin)/(ymax-ymin)*ph;}}
  var svg='<svg width="'+W+'" height="'+H+'" style="display:block;font-family:sans-serif">';
  // Axes
  svg+='<line x1="'+PAL+'" y1="'+PAT+'" x2="'+PAL+'" y2="'+(PAT+ph)+'" stroke="#ccc" stroke-width="1"/>';
  svg+='<line x1="'+PAL+'" y1="'+(PAT+ph)+'" x2="'+(PAL+pw)+'" y2="'+(PAT+ph)+'" stroke="#ccc" stroke-width="1"/>';
  // Scatter dots (sample to ≤800 for SVG performance)
  var step=pts.length>800?Math.ceil(pts.length/800):1;
  for(var i=0;i<pts.length;i+=step){{
    svg+='<circle cx="'+sx(pts[i][0]).toFixed(1)+'" cy="'+sy(pts[i][1]).toFixed(1)+
         '" r="1.5" fill="#3498db" fill-opacity="0.45"/>';
  }}
  // Trend line
  var tx1=xmin,ty1=slope*tx1+intercept;
  var tx2=xmax,ty2=slope*tx2+intercept;
  svg+='<line x1="'+sx(tx1).toFixed(1)+'" y1="'+sy(ty1).toFixed(1)+
       '" x2="'+sx(tx2).toFixed(1)+'" y2="'+sy(ty2).toFixed(1)+
       '" stroke="#e74c3c" stroke-width="1.5"/>';
  // Median diamond
  var sxv2=xv.slice().sort(function(a,b){{return a-b;}});
  var syv2=yv.slice().sort(function(a,b){{return a-b;}});
  var medX=sxv2.length%2===0?(sxv2[sxv2.length/2-1]+sxv2[sxv2.length/2])/2:sxv2[Math.floor(sxv2.length/2)];
  var medY=syv2.length%2===0?(syv2[syv2.length/2-1]+syv2[syv2.length/2])/2:syv2[Math.floor(syv2.length/2)];
  var dmx=sx(medX), dmy=sy(medY), dr=6;
  svg+='<polygon points="'+dmx.toFixed(1)+','+(dmy-dr).toFixed(1)+' '+
       (dmx+dr).toFixed(1)+','+dmy.toFixed(1)+' '+
       dmx.toFixed(1)+','+(dmy+dr).toFixed(1)+' '+
       (dmx-dr).toFixed(1)+','+dmy.toFixed(1)+'" '+
      'fill="#f39c12" stroke="#d35400" stroke-width="1.2" opacity="0.95"><title>Median X='+_fmtUpm(medX)+', Y='+_fmtVmin(medY)+'</title></polygon>';
  // X axis labels
  svg+='<text x="'+PAL+'" y="'+(H-18)+'" font-size="9" fill="#888">'+_fmtUpm(xmin)+'</text>';
  svg+='<text x="'+(PAL+pw)+'" y="'+(H-18)+'" font-size="9" fill="#888" text-anchor="end">'+_fmtUpm(xmax)+'</text>';
  svg+='<text x="'+(PAL+pw/2)+'" y="'+(H-4)+'" font-size="9" fill="#555" text-anchor="middle">'+xLabel+'</text>';
  // Y axis labels
  svg+='<text x="'+(PAL-3)+'" y="'+(PAT+4)+'" font-size="9" fill="#888" text-anchor="end">'+ymax.toFixed(3)+'</text>';
  svg+='<text x="'+(PAL-3)+'" y="'+(PAT+ph)+'" font-size="9" fill="#888" text-anchor="end">'+ymin.toFixed(3)+'</text>';
  svg+='<text x="10" y="'+(PAT+ph/2)+'" font-size="9" fill="#555" text-anchor="middle" '+
       'transform="rotate(-90,10,'+(PAT+ph/2)+')">Vmin '+_escH(vminLabel)+'</text>';
  // Median annotation (top-right)
    svg+='<text x="'+(PAL+pw-2)+'" y="'+(PAT+12)+'" font-size="9" fill="#d35400" '+
      'font-weight="bold" text-anchor="end">Med (X='+_fmtUpm(medX)+'; Y='+_fmtVmin(medY)+')</text>';
  // R² annotation
  svg+='<text x="'+(PAL+pw-2)+'" y="'+(PAT+24)+'" font-size="9" fill="#e74c3c" text-anchor="end">'+
       'R\u00b2='+r2.toFixed(3)+'</text>';
  svg+='</svg>';
  return svg;
}}

// ── Interactive XY scatter (group-by-material + crosshairs) ─────────────────
var _XY_STATE = {{}};
var _XY_USER_PREFS = {{}};  // persists logX/logY/H per cid across _xyInit calls
var _XY_DRAG_STATE = {{active:false, chartId:null, chIdx:-1}};
var _xyCtr = 0;
var _XY_ACTIVE_REBUILD = null;  // fn — set when a modal XY is opened
var _XY_ACTIVE_CID = null;      // cid of the currently open modal XY chart
var _XY_CARRY_STATE = null;     // group-by state to carry over across filter-driven rebuilds
// Document-level drag handlers (attached once)
document.addEventListener('mousemove', function(evt){{
  var ds=_XY_DRAG_STATE;
  if(!ds.active||!ds.chartId) return;
  var s=_XY_STATE[ds.chartId];
  if(!s) return;
  var svg=document.getElementById('xy-svg-'+ds.chartId);
  if(!svg){{ds.active=false;return;}}
  var rect=svg.getBoundingClientRect();
  var PAL=48,PAT=16,PAR=8,PAB=34;
  var pw=s.W-PAL-PAR, ph=s.H-PAT-PAB;
  var px=Math.max(PAL,Math.min(PAL+pw, evt.clientX-rect.left));
  var py=Math.max(PAT,Math.min(PAT+ph, evt.clientY-rect.top));
  s.ch[ds.chIdx]={{
    x:s.xmin+(px-PAL)/pw*(s.xmax-s.xmin),
    y:s.ymin+(PAT+ph-py)/ph*(s.ymax-s.ymin)
  }};
  _xyRender(ds.chartId);
}});
document.addEventListener('mouseup', function(){{ _XY_DRAG_STATE.active=false; }});

function _xyContainer(cid, titleStr, n, resizable, hideGroupBy, initH, initW){{
  var _rz = !!resizable;
  var _rzH = (initH && initH > 80) ? Math.round(initH) : 360;
  var _rzW = (initW && initW > 60) ? Math.round(initW) : null;
  var _plotHtml = _rz
    ? ('<div id="xy-resize-'+cid+'" style="width:'+(_rzW?_rzW+'px':'100%')+';height:'+_rzH+'px;min-width:200px;min-height:160px;resize:both;overflow:auto;border:1px solid #d9e2ef;border-radius:6px;background:#fff">'+
       '<div id="xy-wrap-'+cid+'" style="width:100%;height:100%"></div></div>')
    : ('<div id="xy-wrap-'+cid+'" style="width:100%;'+(_rzW?'max-width:'+_rzW+'px;':'')+'" ></div>');
  var _gbHtml = hideGroupBy ? '' :
    ('<label style="font-size:11px;cursor:pointer;white-space:nowrap;color:#2c3e50;user-select:none">'+
    '<input type="checkbox" id="xy-grp-chk-'+cid+'" onchange="_xyToggleGroup(\\''+cid+'\\')">'+
    ' by Material</label>'+
    '<label style="font-size:11px;cursor:pointer;white-space:nowrap;color:#2c3e50;user-select:none;margin-left:6px">'+
    '<input type="checkbox" id="xy-grp-prog-chk-'+cid+'" onchange="_xyToggleGroupProg(\\''+cid+'\\')">'+
    ' by Prog 6248</label>');
  return '<div id="xy-cont-'+cid+'" style="display:block;width:100%">'+
    '<div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">'+
    '<span style="font-weight:bold;font-size:12px;color:#2c3e50">'+titleStr+
    ' <span style="font-weight:normal;color:#888;font-size:11px">(n='+n+')</span></span>'+
    _gbHtml+'</div>'+
    '<div id="xy-ctrl-'+cid+'" style="margin-bottom:2px"></div>'+
    '<div id="xy-mat-ctrl-'+cid+'" style="display:none;padding:2px 0 4px 0"></div>'+
    _plotHtml+
    '<div id="xy-ch-disp-'+cid+'" style="font-family:monospace;font-size:10px;color:#555;'+
    'margin-top:3px;padding:2px 6px;background:#f5f5f5;border-radius:3px">'+
    '<span style="color:#e74c3c">CH1 \u2014</span>&nbsp;&nbsp;'+
    '<span style="color:#2980b9">CH2 \u2014</span></div>'+
    '<div id="xy-intv-disp-'+cid+'" style="font-family:monospace;font-size:10px;color:#555;'+
    'margin-top:3px;padding:2px 6px;background:#f5f5f5;border-radius:3px">'+
    'Intervals &mdash; X major/minor/smaller, Y major/minor/smaller</div>'+
    '<div id="xy-stats-'+cid+'" style="margin-top:6px"></div></div>';
}}

function _xyInit(cid, pts, mats, W, H, vminLabel, xLabel, polyFit, progs){{
  if(!pts||!pts.length) return;
  var xv=pts.map(function(p){{return p[0];}});
  var yv=pts.map(function(p){{return p[1];}});
  var xmin=Math.min.apply(null,xv), xmax=Math.max.apply(null,xv);
  var ymin=Math.min.apply(null,yv), ymax=Math.max.apply(null,yv);
  if(xmin===xmax){{xmin-=1;xmax+=1;}} if(ymin===ymax){{ymin-=0.001;ymax+=0.001;}}
  var xp=(xmax-xmin)*0.04, yp=(ymax-ymin)*0.06;
  _XY_STATE[cid]={{
    pts:pts, mats:mats, progs:progs||null, W:W, H:H, vminLabel:vminLabel, xLabel:xLabel,
    xmin:xmin-xp, xmax:xmax+xp, ymin:ymin-yp, ymax:ymax+yp,
    xmin0:xmin-xp, xmax0:xmax+xp, ymin0:ymin-yp, ymax0:ymax+yp,
    logX:false, logY:false,
    groupBy:true, groupByMat:true, groupByProg:false,
    polyFit:!!polyFit,
    rolldown:false, rdThreshold:null, rdTargetFreq:null, xref:null,
    showCh:[false,false],
    matList:[], matHidden:{{}}, progList:[],
    ch:[{{x:xmin+(xmax-xmin)*0.33,y:(ymin+ymax)/2}},
        {{x:xmin+(xmax-xmin)*0.67,y:(ymin+ymax)/2}}]
  }};
  // Build material list and auto-enable groupBy when multiple materials present
  var _ml=[]; if(mats) mats.forEach(function(m){{if(m&&_ml.indexOf(m)<0)_ml.push(m);}});
  _ml.sort();
  _XY_STATE[cid].matList=_ml;
  var _xrefBase=_fpUpmRef(xLabel);
  var _xvs=pts.map(function(p){{return p[0];}}).sort(function(a,b){{return a-b;}});
  var _xmed=_xvs.length%2===0?(_xvs[_xvs.length/2-1]+_xvs[_xvs.length/2])/2:_xvs[Math.floor(_xvs.length/2)];
  _XY_STATE[cid].xref=(_xrefBase!=null)?(_xrefBase*0.94):_xmed;
  // Build prog6248 list
  var _pl=[]; if(progs) progs.forEach(function(p){{if(p&&_pl.indexOf(p)<0)_pl.push(p);}});
  _pl.sort();
  _XY_STATE[cid].progList=_pl;
  if(_ml.length>1){{
    _XY_STATE[cid].groupBy=true;
    _XY_STATE[cid].groupByMat=true;
    var _gcb=document.getElementById('xy-grp-chk-'+cid);
    if(_gcb) _gcb.checked=true;
  }}
  // Snap to actual container width before first render
  var _iw=document.getElementById('xy-wrap-'+cid);
  var _rz=document.getElementById('xy-resize-'+cid);
  if(_rz&&_rz.clientWidth>60&&_rz.clientHeight>80){{
    _XY_STATE[cid].W=_rz.clientWidth;
    _XY_STATE[cid].H=_rz.clientHeight;
  }}else if(_iw&&_iw.offsetWidth>60){{
    var _ir=H/W;
    _XY_STATE[cid].W=_iw.offsetWidth;
    _XY_STATE[cid].H=Math.max(120,Math.round(_iw.offsetWidth*_ir));
  }}
  _xyRender(cid);
  // Init axis controls row
  var _cEl=document.getElementById('xy-ctrl-'+cid);
  if(_cEl){{
    var _iSt='width:46px;font-size:11px;padding:1px 3px;border:1px solid #ccc;border-radius:3px;text-align:center';
    _cEl.innerHTML=
      '<div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-bottom:3px;font-size:11px">'+
      '<label style="cursor:pointer;user-select:none"><input type="checkbox" id="xy-logx-'+cid+'" onchange="_xyToggleLog(\\''+cid+'\\',\\'x\\')"> logX</label>'+
      '<label style="cursor:pointer;user-select:none"><input type="checkbox" id="xy-logy-'+cid+'" onchange="_xyToggleLog(\\''+cid+'\\',\\'y\\')"> logY</label>'+
      '<span style="color:#aaa;margin:0 2px">|</span>'+
      '<span style="color:#888">X:</span>'+
      '<input id="xy-xmin-'+cid+'" type="text" placeholder="auto" style="'+_iSt+'">'+
      '<span style="color:#888">\u2013</span>'+
      '<input id="xy-xmax-'+cid+'" type="text" placeholder="auto" style="'+_iSt+'">'+
      '<span style="color:#aaa;margin:0 2px">|</span>'+
      '<span style="color:#888">Y:</span>'+
      '<input id="xy-ymin-'+cid+'" type="text" placeholder="auto" style="'+_iSt+'">'+
      '<span style="color:#888">\u2013</span>'+
      '<input id="xy-ymax-'+cid+'" type="text" placeholder="auto" style="'+_iSt+'">'+
      '<button onclick="_xyApplyRange(\\''+cid+'\\')" style="font-size:11px;padding:1px 6px;cursor:pointer;border:1px solid #bbb;border-radius:3px;background:#f0f0f0">Apply</button>'+
      '<span style="color:#aaa;margin:0 2px">|</span>'+
      '<span style="color:#888">H:</span>'+
      '<input id="xy-h-'+cid+'" type="text" value="'+(H|0)+'px" style="width:52px;font-size:11px;padding:1px 3px;border:1px solid #ccc;border-radius:3px;text-align:center" onchange="_xySetH(\\''+cid+'\\')">'+
      '<span style="color:#aaa;margin:0 2px">|</span>'+
      '<label style="cursor:pointer;user-select:none;color:#e74c3c"><input type="checkbox" id="xy-ch1-'+cid+'" data-cid="'+cid+'" data-chi="0" onchange="_xyToggleCh(this)"> Cur1</label>'+
      '<label style="cursor:pointer;user-select:none;color:#2980b9"><input type="checkbox" id="xy-ch2-'+cid+'" data-cid="'+cid+'" data-chi="1" onchange="_xyToggleCh(this)"> Cur2</label>'+
      '</div>';
  }}
  var _up=_XY_USER_PREFS[cid];
  if(_up){{
    var _upChanged=false;
    if(_up.logX){{_XY_STATE[cid].logX=true;var _lpx=document.getElementById('xy-logx-'+cid);if(_lpx)_lpx.checked=true;_upChanged=true;}}
    if(_up.logY){{_XY_STATE[cid].logY=true;var _lpy=document.getElementById('xy-logy-'+cid);if(_lpy)_lpy.checked=true;_upChanged=true;}}
    if(_up.H&&_up.H>80&&Math.abs(_up.H-_XY_STATE[cid].H)>4){{
      _XY_STATE[cid].H=_up.H;
      var _prz=document.getElementById('xy-resize-'+cid);if(_prz)_prz.style.height=_up.H+'px';
      var _phel=document.getElementById('xy-h-'+cid);if(_phel)_phel.value=Math.round(_up.H)+'px';
      _upChanged=true;
    }}
    if(_up.rolldown){{
      _XY_STATE[cid].rolldown=true;
      var _rdcb=document.getElementById('xy-rd-chk-'+cid);if(_rdcb)_rdcb.checked=true;
      var _rdw=document.getElementById('xy-rd-wrap-'+cid);if(_rdw)_rdw.style.display='inline-flex';
      _upChanged=true;
    }}
    if(_up.rdThreshold!=null){{
      _XY_STATE[cid].rdThreshold=_up.rdThreshold;
      var _rdtel=document.getElementById('xy-rd-thr-'+cid);if(_rdtel)_rdtel.value=_up.rdThreshold;
      _upChanged=true;
    }}
    if(_up.rdTargetFreq!=null){{
      _XY_STATE[cid].rdTargetFreq=_up.rdTargetFreq;
      var _rdfel=document.getElementById('xy-rd-freq-'+cid);if(_rdfel)_rdfel.value=_up.rdTargetFreq;
      _upChanged=true;
    }}
    if(_upChanged)_xyRender(cid);
  }}
  // Populate material filter buttons after all prefs are applied
  _xyUpdateMatBtns(cid);
  // ResizeObserver — re-render SVG when container width changes
  if(typeof ResizeObserver!=='undefined'){{
    var _riw=_rz||document.getElementById('xy-wrap-'+cid);
    if(_riw){{
      var _rzoRaf=0;
      (new ResizeObserver(function(entries){{
        cancelAnimationFrame(_rzoRaf);
        _rzoRaf=requestAnimationFrame(function(){{
          var e=entries[0]; if(!e) return;
          var nw=Math.floor(e.contentRect.width);
          var nh=Math.floor(e.contentRect.height||0);
          var _s=_XY_STATE[cid];
          if(!_s||nw<=60) return;
          if(document.getElementById('xy-resize-'+cid)){{
            nh=Math.max(120,nh||_s.H);
            if(Math.abs(_s.W-nw)>2 || Math.abs(_s.H-nh)>2){{
              _s.W=nw;
              _s.H=nh;
              // Persist H so plot survives filter/checkbox rebuilds
              if(!_XY_USER_PREFS[cid])_XY_USER_PREFS[cid]={{}};
              _XY_USER_PREFS[cid].H=nh;
              var _hInp=document.getElementById('xy-h-'+cid);
              if(_hInp)_hInp.value=Math.round(nh)+'px';
              _xyRender(cid);
            }}
          }} else if(Math.abs(_s.W-nw)>4){{
            _s.H=Math.max(120,Math.round(nw*(_s.H/_s.W)));
            _s.W=nw;
            _xyRender(cid);
          }}
        }});
      }})).observe(_riw);
    }}
  }}
  _XY_ACTIVE_CID = cid;
  // Restore carry-over group-by state (from filter-change rebuild)
  if(_XY_CARRY_STATE){{
    _XY_STATE[cid].groupByMat=_XY_CARRY_STATE.groupByMat;
    _XY_STATE[cid].groupByProg=_XY_CARRY_STATE.groupByProg;
    _XY_STATE[cid].groupBy=!!(_XY_STATE[cid].groupByMat||_XY_STATE[cid].groupByProg);
    var _cgcb=document.getElementById('xy-grp-chk-'+cid);
    if(_cgcb) _cgcb.checked=_XY_STATE[cid].groupByMat;
    var _cpgcb=document.getElementById('xy-grp-prog-chk-'+cid);
    if(_cpgcb) _cpgcb.checked=_XY_STATE[cid].groupByProg;
    _XY_CARRY_STATE=null;
    _xyRender(cid);
    _xyUpdateMatBtns(cid);
  }}
}}

function _xyRender(cid){{
  var s=_XY_STATE[cid]; if(!s) return;
  var wrap=document.getElementById('xy-wrap-'+cid); if(!wrap) return;
  wrap.innerHTML=_xyBuildSVG(s,cid);
  _xyRefreshStats(cid);
  _xyUpdateChDisp(cid);
  _xyUpdateIntvDisp(cid);
  _xyUpdateModalStats(cid);
}}

function _xyIntvStep(lo, hi, nMajor){{
  var rng = hi - lo;
  if(!(rng > 0) || !(nMajor > 0)) return 1;
  var raw = rng / nMajor;
  var mag = Math.pow(10, Math.floor(Math.log10(raw)));
  return (raw / mag <= 1.5 ? 1 : raw / mag <= 3 ? 2 : raw / mag <= 7 ? 5 : 10) * mag;
}}

function _xyUpdateIntvDisp(cid){{
  var s=_XY_STATE[cid]; if(!s) return;
  var el=document.getElementById('xy-intv-disp-'+cid); if(!el) return;
  var xMaj=_xyIntvStep(s.xmin,s.xmax,5);
  var yMaj=_xyIntvStep(s.ymin,s.ymax,5);
  var xMin=xMaj/5, yMin=yMaj/5;
  var xSm=xMin/5, ySm=yMin/5;
  el.innerHTML='<span style="color:#555"><b>Intervals</b> &mdash; X: major='+_fmt(xMaj,4)+', minor='+_fmt(xMin,4)+', smaller='+_fmt(xSm,4)+
    ' | Y: major='+_fmt(yMaj,4)+', minor='+_fmt(yMin,4)+', smaller='+_fmt(ySm,4)+'</span>';
}}

function _xyBuildSVG(s,cid){{
  var W=s.W,H=s.H,PAL=48,PAT=16,PAR=8,PAB=34;
  var pw=W-PAL-PAR, ph=H-PAT-PAB;
  var xmin=s.xmin,xmax=s.xmax,ymin=s.ymin,ymax=s.ymax;
  var _lx=!!(s.logX),_ly=!!(s.logY);
  if(_lx&&xmin<=0)xmin=1e-9; if(_lx&&xmax<=0)xmax=1;
  if(_ly&&ymin<=0)ymin=1e-9; if(_ly&&ymax<=0)ymax=1;
  var _lxA=_lx?Math.log10(xmin):xmin, _lxB=_lx?Math.log10(xmax):xmax;
  var _lyA=_ly?Math.log10(ymin):ymin, _lyB=_ly?Math.log10(ymax):ymax;
  function sx(x){{var _v=_lx?Math.log10(Math.max(x,1e-18)):x; return PAL+(_v-_lxA)/(_lxB-_lxA)*pw;}}
  function sy(y){{var _v=_ly?Math.log10(Math.max(y,1e-18)):y; return PAT+ph-(_v-_lyA)/(_lyB-_lyA)*ph;}}
  var COLORS=['#2980b9','#e74c3c','#27ae60','#9b59b6','#f39c12','#16a085','#e67e22','#34495e','#c0392b','#1abc9c'];
  var _groupArr=_xyGetGroupArr(s);
  var matList=[];
  if(s.groupBy&&_groupArr){{
    _groupArr.forEach(function(m){{if(m&&matList.indexOf(m)<0)matList.push(m);}});
    matList.sort();
  }}
  var cpId='xycp'+cid;
  var svg='<svg id="xy-svg-'+cid+'" width="'+W+'" height="'+H+'" '+
    'style="display:block;font-family:sans-serif;user-select:none" '+
    'onmousedown="_xyDragStart(event,\\''+cid+'\\',event.target.dataset.chi)">';
  svg+='<defs><clipPath id="'+cpId+'"><rect x="'+PAL+'" y="'+PAT+'" width="'+pw+'" height="'+ph+'"/></clipPath></defs>';
  svg+='<rect x="'+PAL+'" y="'+PAT+'" width="'+pw+'" height="'+ph+'" fill="#fafbfd" stroke="#ccc"/>';
  // Nice-tick grid
  function _ntk(lo,hi,n){{
    var rng=hi-lo; if(rng<=0||n<=0) return {{ticks:[lo],step:1}};
    var raw=rng/n, mag=Math.pow(10,Math.floor(Math.log10(raw)));
    var st=(raw/mag<=1.5?1:raw/mag<=3?2:raw/mag<=7?5:10)*mag;
    var t0=Math.ceil(lo/st-1e-9)*st, tk=[];
    for(var _t=t0;_t<=hi+st*1e-6;_t=Math.round((_t+st)*1e9)/1e9) tk.push(_t);
    return {{ticks:tk,step:st}};
  }}
  var _xG=_ntk(xmin,xmax,5), _yG=_ntk(ymin,ymax,5);
  // Log-scale tick helper (ticks in log10 space → actual values)
  function _xyLogTks(lo,hi){{
    var lLo=Math.log10(Math.max(lo,1e-18)),lHi=Math.log10(Math.max(hi,1e-18));
    return _ntk(lLo,lHi,5).ticks.map(function(t){{return Math.pow(10,t);}});
  }}
  // Number formatter for log-scale labels
  function _xyFmtN(v){{
    if(!isFinite(v)) return '?';
    var av=Math.abs(v);
    return av>=100?v.toFixed(0):av>=1?v.toFixed(2):av>=0.01?v.toFixed(3):v.toExponential(1);
  }}
  var _xTks=_lx?_xyLogTks(xmin,xmax):_xG.ticks;
  var _yTks=_ly?_xyLogTks(ymin,ymax):_yG.ticks;
  var _xMnSt=_lx?0:_xG.step/5, _yMnSt=_ly?0:_yG.step/5;
  // Minor grid lines (linear only)
  if(_xMnSt>0){{
  for(var _t=Math.ceil(xmin/_xMnSt-1e-9)*_xMnSt;_t<=xmax+_xMnSt*1e-6;_t=Math.round((_t+_xMnSt)*1e9)/1e9){{
    var _xx=sx(_t); if(_xx<PAL-0.5||_xx>PAL+pw+0.5) continue;
    svg+='<line x1="'+_xx.toFixed(1)+'" y1="'+PAT+'" x2="'+_xx.toFixed(1)+'" y2="'+(PAT+ph)+'" stroke="#f0f0f0" stroke-width="0.5"/>';
  }}
  }}
  if(_yMnSt>0){{
  for(var _t=Math.ceil(ymin/_yMnSt-1e-9)*_yMnSt;_t<=ymax+_yMnSt*1e-6;_t=Math.round((_t+_yMnSt)*1e9)/1e9){{
    var _yy=sy(_t); if(_yy<PAT-0.5||_yy>PAT+ph+0.5) continue;
    svg+='<line x1="'+PAL+'" y1="'+_yy.toFixed(1)+'" x2="'+(PAL+pw)+'" y2="'+_yy.toFixed(1)+'" stroke="#f0f0f0" stroke-width="0.5"/>';
  }}
  }}
  // Major grid + tick marks + labels
  _xTks.forEach(function(v){{
    var _xx=sx(v); if(_xx<PAL-0.5||_xx>PAL+pw+0.5) return;
    svg+='<line x1="'+_xx.toFixed(1)+'" y1="'+PAT+'" x2="'+_xx.toFixed(1)+'" y2="'+(PAT+ph)+'" stroke="#e0e0e0" stroke-width="0.8"/>';
    svg+='<line x1="'+_xx.toFixed(1)+'" y1="'+(PAT+ph)+'" x2="'+_xx.toFixed(1)+'" y2="'+(PAT+ph+4)+'" stroke="#999" stroke-width="1"/>';
    svg+='<text x="'+_xx.toFixed(1)+'" y="'+(PAT+ph+13)+'" font-size="8.5" fill="#666" text-anchor="middle">'+(_lx?_xyFmtN(v):v.toFixed(1))+'</text>';
  }});
  _yTks.forEach(function(v){{
    var _yy=sy(v); if(_yy<PAT-0.5||_yy>PAT+ph+0.5) return;
    svg+='<line x1="'+PAL+'" y1="'+_yy.toFixed(1)+'" x2="'+(PAL+pw)+'" y2="'+_yy.toFixed(1)+'" stroke="#e0e0e0" stroke-width="0.8"/>';
    svg+='<line x1="'+(PAL-4)+'" y1="'+_yy.toFixed(1)+'" x2="'+PAL+'" y2="'+_yy.toFixed(1)+'" stroke="#999" stroke-width="1"/>';
    svg+='<text x="'+(PAL-6)+'" y="'+(_yy+3.5).toFixed(1)+'" font-size="8.5" fill="#666" text-anchor="end">'+(_ly?_xyFmtN(v):_fmtVmin(v))+'</text>';
  }});
  if(s.groupBy&&matList.length){{
    var _gMeds={{}}, _gDiamSvg='';
    matList.forEach(function(mat,gi){{
      if(s.matHidden&&s.matHidden[mat]) return;
      var col=COLORS[gi%COLORS.length];
      var gpts=s.pts.filter(function(_,i){{return _groupArr[i]===mat;}});
      if(gpts.length>1){{
        if(s.polyFit && gpts.length>=3){{
          var poly = _xyPoly2Fit(gpts);
          if(poly){{
            var gx = gpts.map(function(p){{return p[0];}});
            var gMin = Math.max(xmin, Math.min.apply(null, gx));
            var gMax = Math.min(xmax, Math.max.apply(null, gx));
            var path = _xyPolyPath(poly, gMin, gMax, sx, sy, 64);
            if(path) svg += '<path d="'+path+'" fill="none" stroke="'+col+'" stroke-width="1.8" clip-path="url(#'+cpId+')" opacity="0.9"/>';
          }} else {{
            var reg=_xyRegress(gpts);
            if(reg){{
              var y1r=reg.slope*xmin+reg.intercept, y2r=reg.slope*xmax+reg.intercept;
              svg+='<line x1="'+sx(xmin).toFixed(1)+'" y1="'+sy(y1r).toFixed(1)+
                   '" x2="'+sx(xmax).toFixed(1)+'" y2="'+sy(y2r).toFixed(1)+
                   '" stroke="'+col+'" stroke-width="1.5" clip-path="url(#'+cpId+')" opacity="0.85"/>';
            }}
          }}
        }} else {{
          var reg=_xyRegress(gpts);
          if(reg){{
            var y1r=reg.slope*xmin+reg.intercept, y2r=reg.slope*xmax+reg.intercept;
            svg+='<line x1="'+sx(xmin).toFixed(1)+'" y1="'+sy(y1r).toFixed(1)+
                 '" x2="'+sx(xmax).toFixed(1)+'" y2="'+sy(y2r).toFixed(1)+
                 '" stroke="'+col+'" stroke-width="1.5" clip-path="url(#'+cpId+')" opacity="0.85"/>';
          }}
        }}
      }}
      var step=gpts.length>500?Math.ceil(gpts.length/500):1;
      svg+='<g clip-path="url(#'+cpId+')">';
      for(var pi=0;pi<gpts.length;pi+=step){{
        var _p=gpts[pi];
        var _tt='Category: '+mat+' | X='+_fmtUpm(_p[0])+' | Y='+_fmtVmin(_p[1]);
        svg+='<circle cx="'+sx(_p[0]).toFixed(1)+'" cy="'+sy(_p[1]).toFixed(1)+'" r="1.8" fill="'+col+'" stroke="#fff" stroke-width="0.5" opacity="0.92"><title>'+_escH(_tt)+'</title></circle>';
      }}
      svg+='</g>';
      if(gpts.length){{
        var _gsx=gpts.map(function(p){{return p[0];}}).slice().sort(function(a,b){{return a-b;}});
        var _gsy=gpts.map(function(p){{return p[1];}}).slice().sort(function(a,b){{return a-b;}});
        var _gmx=_gsx.length%2===0?(_gsx[_gsx.length/2-1]+_gsx[_gsx.length/2])/2:_gsx[Math.floor(_gsx.length/2)];
        var _gmy=_gsy.length%2===0?(_gsy[_gsy.length/2-1]+_gsy[_gsy.length/2])/2:_gsy[Math.floor(_gsy.length/2)];
        _gMeds[mat]={{x:_gmx,y:_gmy}};
        var _gdx=sx(_gmx),_gdy=sy(_gmy),_dr2=5;
        _gDiamSvg+='<polygon points="'+_gdx.toFixed(1)+','+(_gdy-_dr2).toFixed(1)+' '+(_gdx+_dr2).toFixed(1)+','+_gdy.toFixed(1)+' '+_gdx.toFixed(1)+','+(_gdy+_dr2).toFixed(1)+' '+(_gdx-_dr2).toFixed(1)+','+_gdy.toFixed(1)+'" fill="'+col+'" stroke="#fff" stroke-width="1.5" opacity="0.95" clip-path="url(#'+cpId+')" pointer-events="none"><title>'+_escH(mat)+' Median X='+_fmtUpm(_gmx)+' Y='+_fmtVmin(_gmy)+'</title></polygon>';
      }}
    }});
    svg+=_gDiamSvg;
  }}else{{
    if(s.polyFit && s.pts.length>=3){{
      var p2 = _xyPoly2Fit(s.pts);
      if(p2){{
        var path2 = _xyPolyPath(p2, xmin, xmax, sx, sy, 96);
        if(path2) svg+='<path d="'+path2+'" fill="none" stroke="#e74c3c" stroke-width="1.8" clip-path="url(#'+cpId+')" opacity="0.9"/>';
      }} else {{
        var reg=_xyRegress(s.pts);
        if(reg){{
          var y1r=reg.slope*xmin+reg.intercept, y2r=reg.slope*xmax+reg.intercept;
          svg+='<line x1="'+sx(xmin).toFixed(1)+'" y1="'+sy(y1r).toFixed(1)+
               '" x2="'+sx(xmax).toFixed(1)+'" y2="'+sy(y2r).toFixed(1)+
               '" stroke="#e74c3c" stroke-width="1.5" clip-path="url(#'+cpId+')" opacity="0.8"/>';
        }}
      }}
    }} else {{
      var reg=_xyRegress(s.pts);
      if(reg){{
        var y1r=reg.slope*xmin+reg.intercept, y2r=reg.slope*xmax+reg.intercept;
        svg+='<line x1="'+sx(xmin).toFixed(1)+'" y1="'+sy(y1r).toFixed(1)+
             '" x2="'+sx(xmax).toFixed(1)+'" y2="'+sy(y2r).toFixed(1)+
             '" stroke="#e74c3c" stroke-width="1.5" clip-path="url(#'+cpId+')" opacity="0.8"/>';
      }}
    }}
    var step=s.pts.length>800?Math.ceil(s.pts.length/800):1;
    svg+='<g clip-path="url(#'+cpId+')">';
    for(var pi=0;pi<s.pts.length;pi+=step){{
      var _p=s.pts[pi];
      var _cat=(s.mats&&s.mats[pi])?String(s.mats[pi]):'All';
      var _meta=(s.hoverMeta&&s.hoverMeta[pi])?(' | '+s.hoverMeta[pi]):'';
      var _tt='Category: '+_cat+' | X='+_fmtUpm(_p[0])+' | Y='+_fmtVmin(_p[1])+_meta;
      svg+='<circle cx="'+sx(_p[0]).toFixed(1)+'" cy="'+sy(_p[1]).toFixed(1)+'" r="1.5" fill="#3498db" stroke="#fff" stroke-width="0.5" opacity="0.82"><title>'+_escH(_tt)+'</title></circle>';
    }}
    svg+='</g>';
  }}
  // Median diamond + annotation (non-grouped only; grouped uses per-group diamonds)
  if(!s.groupBy){{
    var sxv2=s.pts.map(function(p){{return p[0];}}).slice().sort(function(a,b){{return a-b;}});
    var syv2=s.pts.map(function(p){{return p[1];}}).slice().sort(function(a,b){{return a-b;}});
    var medX=sxv2.length%2===0?(sxv2[sxv2.length/2-1]+sxv2[sxv2.length/2])/2:sxv2[Math.floor(sxv2.length/2)];
    var medY=syv2.length%2===0?(syv2[syv2.length/2-1]+syv2[syv2.length/2])/2:syv2[Math.floor(syv2.length/2)];
    var dmx=sx(medX),dmy=sy(medY),dr=6;
    svg+='<polygon points="'+dmx.toFixed(1)+','+(dmy-dr).toFixed(1)+' '+(dmx+dr).toFixed(1)+','+dmy.toFixed(1)+' '+
         dmx.toFixed(1)+','+(dmy+dr).toFixed(1)+' '+(dmx-dr).toFixed(1)+','+dmy.toFixed(1)+'" '+
         'fill="#f39c12" stroke="#d35400" stroke-width="1.2" opacity="0.95" pointer-events="none">'+
            '<title>Median X='+_fmtUpm(medX)+' Y='+_fmtVmin(medY)+'</title></polygon>';
          svg+='<text x="'+(PAL+pw-2)+'" y="'+(PAT+10)+'" font-size="9" fill="#d35400" font-weight="bold" text-anchor="end">Med (X='+_fmtUpm(medX)+'; Y='+_fmtVmin(medY)+')</text>';
    var reg3=_xyRegress(s.pts);
    if(reg3) svg+='<text x="'+(PAL+pw-2)+'" y="'+(PAT+22)+'" font-size="9" fill="#e74c3c" text-anchor="end">R\u00b2='+reg3.r2.toFixed(3)+'</text>';
  }}
  // Rolldown simulation — per group (Core/Atom/CCF) poly fit
  if(s.rolldown && s.rdThreshold!=null && s.rdTargetFreq!=null){{
    var _rdFt=+s.rdTargetFreq, _rdThr=+s.rdThreshold;
    var _rdGrps={{}};
    s.pts.forEach(function(p,i){{
      var _g=(s.mats&&s.mats[i])?String(s.mats[i]):'All';
      if(!_rdGrps[_g])_rdGrps[_g]={{pts:[],idxs:[]}};
      _rdGrps[_g].pts.push(p); _rdGrps[_g].idxs.push(i);
    }});
    var _rdSum={{}};
    var _rdLayer='';
    Object.keys(_rdGrps).forEach(function(grp){{
      var gd=_rdGrps[grp];
      var gp=gd.pts.length>=3?_xyPoly2Fit(gd.pts):null;
      var gr=gp?null:_xyRegress(gd.pts);
      var _gMn=gd.pts.reduce(function(a,p){{return a+p[1];}},0)/gd.pts.length;
      var rdF=function(x){{return gp?gp.a*x*x+gp.b*x+gp.c:(gr?gr.slope*x+gr.intercept:_gMn);}};
      var rdFt=rdF(_rdFt);
      _rdSum[grp]={{rec:0,fail:0}};
      gd.pts.forEach(function(p,li){{
        if(p[1]<=_rdThr)return;
        var vh=rdF(p[0]),dv=p[1]-vh,ve=rdFt+dv,ok=ve<=_rdThr;
        if(ok)_rdSum[grp].rec++;else _rdSum[grp].fail++;
        var cx=sx(_rdFt),cy=sy(ve),tr=4;
        var _om=(s.hoverMeta&&s.hoverMeta[gd.idxs[li]])?s.hoverMeta[gd.idxs[li]]:'';
        var tt='['+grp+'] ROLLDOWN | '+
               'Orig: F='+_fmtUpm(p[0])+' GHz Vmin='+_fmtVmin(p[1])+' | '+
               'Fit@F: '+_fmtVmin(vh)+' \u03b4='+(dv>=0?'+':'')+_fmtVmin(dv)+' | '+
               'Fit@Ft: '+_fmtVmin(rdFt)+' Est='+_fmtVmin(ve)+' | '+
               'Thr='+_fmtVmin(_rdThr)+' | '+
               (ok?'\u2713 RECOVERS at F='+_fmtUpm(_rdFt)+' GHz':'\u2717 STILL FAILS')+
               (_om?' | '+_om:'');
        var col=ok?'#27ae60':'#c0392b';
        _rdLayer+='<line x1="'+sx(p[0]).toFixed(1)+'" y1="'+sy(p[1]).toFixed(1)+
                   '" x2="'+cx.toFixed(1)+'" y2="'+cy.toFixed(1)+
                   '" stroke="'+col+'" stroke-width="0.7" stroke-dasharray="3,2" opacity="0.35" clip-path="url(#'+cpId+')"/>';
        var tp=ok
          ?(cx.toFixed(1)+','+(cy-tr).toFixed(1)+' '+(cx+tr).toFixed(1)+','+(cy+tr).toFixed(1)+' '+(cx-tr).toFixed(1)+','+(cy+tr).toFixed(1))
          :(cx.toFixed(1)+','+(cy+tr).toFixed(1)+' '+(cx+tr).toFixed(1)+','+(cy-tr).toFixed(1)+' '+(cx-tr).toFixed(1)+','+(cy-tr).toFixed(1));
        _rdLayer+='<polygon points="'+tp+'" fill="'+col+'" stroke="#fff" stroke-width="0.8" opacity="0.9" clip-path="url(#'+cpId+')" pointer-events="all"><title>'+_escH(tt)+'</title></polygon>';
      }});
    }});
    svg+=_rdLayer;
    if(_rdThr>=s.ymin&&_rdThr<=s.ymax){{
      svg+='<line x1="'+sx(s.xmin).toFixed(1)+'" y1="'+sy(_rdThr).toFixed(1)+
            '" x2="'+sx(s.xmax).toFixed(1)+'" y2="'+sy(_rdThr).toFixed(1)+
            '" stroke="#e74c3c" stroke-width="1.5" stroke-dasharray="6,3" opacity="0.75" clip-path="url(#'+cpId+')"/>';
      svg+='<text x="'+(PAL+4)+'" y="'+(sy(_rdThr)-3).toFixed(1)+'" font-size="9" fill="#e74c3c">Thr='+_fmtVmin(_rdThr)+'</text>';
    }}
    if(_rdFt>=s.xmin&&_rdFt<=s.xmax){{
      svg+='<line x1="'+sx(_rdFt).toFixed(1)+'" y1="'+sy(s.ymin).toFixed(1)+
            '" x2="'+sx(_rdFt).toFixed(1)+'" y2="'+sy(s.ymax).toFixed(1)+
            '" stroke="#27ae60" stroke-width="1.2" stroke-dasharray="4,3" opacity="0.6" clip-path="url(#'+cpId+')"/>';
      svg+='<text x="'+(sx(_rdFt)+3).toFixed(1)+'" y="'+(PAT+12)+'" font-size="9" fill="#27ae60">\u2192'+_fmtUpm(_rdFt)+'GHz</text>';
    }}
    var _rdLy=PAT+12;
    Object.keys(_rdSum).forEach(function(grp){{
      var gs=_rdSum[grp];if(!gs.rec&&!gs.fail)return;
      var tot=gs.rec+gs.fail,pct=(gs.rec/tot*100).toFixed(0);
      svg+='<text x="'+(PAL+4)+'" y="'+_rdLy+'" font-size="9" fill="#555" text-anchor="start">'+
           '\u25b2'+gs.rec+' \u25bc'+gs.fail+' ('+pct+'% rec) ['+_escH(grp)+']</text>';
      _rdLy+=12;
    }});
  }}
  // Axis names
  if(!s.noAxisLabels){{
    svg+='<text x="'+(PAL+pw/2)+'" y="'+(H-6)+'" font-size="9" fill="#555" text-anchor="middle">'+_escH(s.xLabel)+'</text>';
    svg+='<text x="10" y="'+(PAT+ph/2)+'" font-size="9" fill="#555" text-anchor="middle" transform="rotate(-90,10,'+(PAT+ph/2)+')">Vmin '+_escH(s.vminLabel)+'</text>';
  }}
  // (legend removed — shown in Group Stats table below)
  // VF reference overlay lines (drawn before crosshairs so they're underneath)
  if(s.vfLines && s.vfLines.length){{
    s.vfLines.forEach(function(vfl){{
      var lpts = vfl.pts;
      if(!lpts || lpts.length < 2) return;
      var col = vfl.color || '#e67e22';
      // Poly2 fit through defined points (sorted by x); fall back to polyline if < 3 pts
      var sortedPts = lpts.slice().sort(function(a,b){{return a[0]-b[0];}});
      var poly2 = sortedPts.length >= 3 ? _xyPoly2Fit(sortedPts) : null;
      if(poly2){{
        var _xs = sortedPts.map(function(p){{return p[0];}});
        var fitX0 = Math.max(xmin, _xs[0]);
        var fitX1 = Math.min(xmax, _xs[_xs.length-1]);
        var curvePath = _xyPolyPath(poly2, fitX0, fitX1, sx, sy, 80);
        if(curvePath) svg += '<path d="'+curvePath+'" fill="none" stroke="'+col+'" stroke-width="2" stroke-dasharray="6,3" clip-path="url(#'+cpId+')" opacity="0.85"><title>'+_escH(vfl.label||'')+'</title></path>';
      }} else {{
        var d2 = '';
        lpts.forEach(function(p){{
          var px = sx(p[0]), py = sy(p[1]);
          if(px < PAL - 1 || px > PAL + pw + 1) return;
          d2 += (d2==='' ? 'M' : 'L') + px.toFixed(1) + ',' + py.toFixed(1) + ' ';
        }});
        if(d2) svg += '<path d="'+d2+'" fill="none" stroke="'+col+'" stroke-width="2" stroke-dasharray="6,3" clip-path="url(#'+cpId+')" opacity="0.85"><title>'+_escH(vfl.label||'')+'</title></path>';
      }}
      // Small dot markers at each defined point
      svg += '<g clip-path="url(#'+cpId+')">';
      lpts.forEach(function(p){{
        var px = sx(p[0]), py = sy(p[1]);
        svg += '<circle cx="'+px.toFixed(1)+'" cy="'+py.toFixed(1)+'" r="3" fill="'+col+'" stroke="#fff" stroke-width="1" opacity="0.9"><title>'+_escH(vfl.label||'')+' x='+_fmtUpm(p[0])+' y='+_fmtVmin(p[1])+'</title></circle>';
      }});
      svg += '</g>';
      // End label at last in-range point
      var lastP = null;
      for(var li = lpts.length-1; li >= 0; li--){{
        var lpx = sx(lpts[li][0]);
        if(lpx >= PAL && lpx <= PAL+pw){{ lastP = lpts[li]; break; }}
      }}
      if(lastP && !s.noAxisLabels){{
        var lpx2 = sx(lastP[0]), lpy2 = sy(lastP[1]);
        var _la2 = lpx2 + 60 > PAL+pw ? 'end' : 'start';
        var _lx2 = _la2==='start' ? lpx2+5 : lpx2-5;
        svg += '<text x="'+_lx2.toFixed(1)+'" y="'+(lpy2-4).toFixed(1)+'" font-size="8" fill="'+col+'" font-weight="bold" text-anchor="'+_la2+'" pointer-events="none">'+_escH(vfl.label||'')+'</text>';
      }}
    }});
  }}
  // Crosshairs — only when enabled
  var chCols=['#e74c3c','#2980b9'];
  s.ch.forEach(function(ch,ci){{
    if(!s.showCh||!s.showCh[ci]) return;
    var cx2=Math.max(PAL,Math.min(PAL+pw,sx(ch.x)));
    var cy2=Math.max(PAT,Math.min(PAT+ph,sy(ch.y)));
    var col=chCols[ci];
    svg+='<line x1="'+cx2.toFixed(1)+'" y1="'+PAT+'" x2="'+cx2.toFixed(1)+'" y2="'+(PAT+ph)+'" stroke="'+col+'" stroke-width="1.2" stroke-dasharray="4,3" pointer-events="none"/>';
    svg+='<line x1="'+PAL+'" y1="'+cy2.toFixed(1)+'" x2="'+(PAL+pw)+'" y2="'+cy2.toFixed(1)+'" stroke="'+col+'" stroke-width="1.2" stroke-dasharray="4,3" pointer-events="none"/>';
    svg+='<circle cx="'+cx2.toFixed(1)+'" cy="'+cy2.toFixed(1)+'" r="7" fill="'+col+'" fill-opacity="0.15" stroke="'+col+'" stroke-width="1.8" style="cursor:move" data-chi="'+ci+'"><title>CH'+(ci+1)+' \u2014 drag to move</title></circle>';
    var lx=cx2+10,la='start'; if(lx+35>PAL+pw){{lx=cx2-10;la='end';}}
    svg+='<text x="'+lx.toFixed(1)+'" y="'+(cy2-4).toFixed(1)+'" font-size="8" fill="'+col+'" text-anchor="'+la+'" pointer-events="none">CH'+(ci+1)+'</text>';
  }});
  svg+='</svg>';
  return svg;
}}

function _xyRegress(pts){{
  if(!pts||pts.length<2) return null;
  var n=pts.length,sumX=0,sumY=0,sumXY=0,sumXX=0;
  pts.forEach(function(p){{sumX+=p[0];sumY+=p[1];sumXY+=p[0]*p[1];sumXX+=p[0]*p[0];}});
  var d=n*sumXX-sumX*sumX; if(d===0) return null;
  var slope=(n*sumXY-sumX*sumY)/d, intercept=(sumY-slope*sumX)/n;
  var ym=sumY/n,ssTot=0,ssRes=0;
  pts.forEach(function(p){{ssTot+=(p[1]-ym)*(p[1]-ym);ssRes+=(p[1]-(slope*p[0]+intercept))*(p[1]-(slope*p[0]+intercept));}});
  return {{slope:slope,intercept:intercept,r2:ssTot>0?1-ssRes/ssTot:0}};
}}

function _xyPoly2Fit(pts){{
  if(!pts || pts.length < 3) return null;
  var n=pts.length;
  var sx1=0,sx2=0,sx3=0,sx4=0,sy=0,sxy=0,sx2y=0;
  pts.forEach(function(p){{
    var x=+p[0], y=+p[1], x2=x*x;
    sx1+=x; sx2+=x2; sx3+=x2*x; sx4+=x2*x2;
    sy+=y; sxy+=x*y; sx2y+=x2*y;
  }});
  var A=[
    [sx4,sx3,sx2,sx2y],
    [sx3,sx2,sx1,sxy],
    [sx2,sx1,n,  sy ]
  ];
  for(var i=0;i<3;i++){{
    var piv=i;
    for(var r=i+1;r<3;r++) if(Math.abs(A[r][i])>Math.abs(A[piv][i])) piv=r;
    if(Math.abs(A[piv][i])<1e-12) return null;
    if(piv!==i){{var tmp=A[i];A[i]=A[piv];A[piv]=tmp;}}
    var d=A[i][i];
    for(var c=i;c<4;c++) A[i][c]/=d;
    for(var r2=0;r2<3;r2++){{
      if(r2===i) continue;
      var f=A[r2][i];
      for(var c2=i;c2<4;c2++) A[r2][c2]-=f*A[i][c2];
    }}
  }}
  return {{a:A[0][3], b:A[1][3], c:A[2][3]}};
}}

function _xyPolyPath(poly, x0, x1, sxFn, syFn, steps){{
  if(!poly || !isFinite(x0) || !isFinite(x1) || x1<=x0) return '';
  var n=Math.max(16, steps||64);
  var path='';
  for(var i=0;i<=n;i++){{
    var x=x0+(x1-x0)*i/n;
    var y=poly.a*x*x + poly.b*x + poly.c;
    var px=sxFn(x).toFixed(1), py=syFn(y).toFixed(1);
    path += (i===0 ? 'M' : 'L') + px + ',' + py;
  }}
  return path;
}}


function _xyGetGroupArr(s){{
  if(!s||!s.groupBy)return null;
  var byM=!!s.groupByMat, byP=!!(s.groupByProg&&s.progs);
  if(!byM&&!byP)return null;
  return s.pts.map(function(_,ii){{
    var parts=[];
    if(byM&&s.mats)parts.push(s.mats[ii]||'?');
    if(byP&&s.progs)parts.push(s.progs[ii]||'?');
    return parts.join(' | ');
  }});
}}
function _xyGroupFields(s){{
  /* Returns array of field labels matching groupBy selections */
  if(!s||!s.groupBy)return null;
  var f=[];
  if(s.groupByMat)f.push('Material');
  if(s.groupByProg&&s.progs)f.push('Prog');
  return f.length>1?f:null;
}}

function _xyCalcStats(cid){{
  var s=_XY_STATE[cid]; if(!s) return [];
  var COLORS=['#2980b9','#e74c3c','#27ae60','#9b59b6','#f39c12','#16a085','#e67e22','#34495e','#c0392b','#1abc9c'];
  var _groupArr=_xyGetGroupArr(s);
  var result=[];
  if(s.groupBy&&_groupArr){{
    var matList=[];
    _groupArr.forEach(function(m){{if(m&&matList.indexOf(m)<0)matList.push(m);}});
    matList.sort();
    var _xyGF=_xyGroupFields(s); /* null or ['Material','Prog'] */
    matList.forEach(function(mat,gi){{
      if(s.matHidden&&s.matHidden[mat])return;
      var col=COLORS[gi%COLORS.length];
      var gpts=s.pts.filter(function(_,ii){{return _groupArr[ii]===mat;}});
      if(!gpts.length)return;
      var reg=_xyRegress(gpts);
      var sxs=gpts.map(function(p){{return p[0];}}).sort(function(a,b){{return a-b;}});
      var sys=gpts.map(function(p){{return p[1];}}).sort(function(a,b){{return a-b;}});
      var medX=sxs.length%2===0?(sxs[sxs.length/2-1]+sxs[sxs.length/2])/2:sxs[Math.floor(sxs.length/2)];
      var medY=sys.length%2===0?(sys[sys.length/2-1]+sys[sys.length/2])/2:sys[Math.floor(sys.length/2)];
      var _gp=_xyGF?mat.split(' | '):null;
      result.push({{group:mat,gbyParts:_gp,n:gpts.length,r2:reg?reg.r2:null,medX:medX,medY:medY,fitM:reg?reg.slope:null,fitB:reg?reg.intercept:null,color:col}});
    }});
  }}else{{
    var reg=_xyRegress(s.pts);
    var sxs=s.pts.map(function(p){{return p[0];}}).sort(function(a,b){{return a-b;}});
    var sys=s.pts.map(function(p){{return p[1];}}).sort(function(a,b){{return a-b;}});
    var medX=sxs.length%2===0?(sxs[sxs.length/2-1]+sxs[sxs.length/2])/2:sxs[Math.floor(sxs.length/2)];
    var medY=sys.length%2===0?(sys[sys.length/2-1]+sys[sys.length/2])/2:sys[Math.floor(sys.length/2)];
    result.push({{group:'All',n:s.pts.length,r2:reg?reg.r2:null,medX:medX,medY:medY,fitM:reg?reg.slope:null,fitB:reg?reg.intercept:null,color:'#2980b9'}});
  }}
  return result;
}}

function _xyStatsRows(gs,xref){{
  var rows='';
  var _multiGbyXY=gs.length&&gs[0].gbyParts&&gs[0].gbyParts.length>1;
  gs.forEach(function(g,i){{
    var bg=i%2?'#fff':'#f0f2f5';
    var yAtX=(isFinite(xref)&&g.fitM!=null)?(+(g.fitM*xref+g.fitB).toFixed(4)):null;
    var eq=g.fitM!=null?('y='+g.fitM.toFixed(4)+'x'+(g.fitB>=0?'+':'')+g.fitB.toFixed(4)):'-';
    rows+='<tr style="background:'+bg+'">';
    if(_multiGbyXY&&g.gbyParts){{
      rows+='<td style="display:none"></td>';
      g.gbyParts.forEach(function(v,fi){{
        var prefix=fi===0?'<span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:'+g.color+';margin-right:4px;vertical-align:middle"></span>':'';
        rows+='<td style="padding:2px 5px;border:1px solid #ddd">'+prefix+_escH(v)+'</td>';
      }});
    }}else{{
      rows+='<td style="padding:2px 5px;border:1px solid #ddd"><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:'+g.color+';margin-right:4px;vertical-align:middle"></span>'+_escH(g.group)+'</td>';
    }}
    rows+='<td style="padding:2px 5px;border:1px solid #ddd;text-align:center">'+g.n+'</td>'
      +'<td style="padding:2px 5px;border:1px solid #ddd;text-align:center">'+(g.r2!=null?_fmt(g.r2,3):'-')+'</td>'
      +'<td style="padding:2px 5px;border:1px solid #ddd;text-align:center">'+_fmtUpm(g.medX)+'</td>'
      +'<td style="padding:2px 5px;border:1px solid #ddd;text-align:center">'+_fmtVmin(g.medY)+'</td>'
      +'<td style="padding:2px 5px;border:1px solid #ddd;text-align:center">'+(yAtX!=null?_fmtVmin(yAtX):'-')+'</td>'
      +'<td style="padding:2px 5px;border:1px solid #ddd;text-align:center;font-size:10px;color:#555">'+eq+'</td>'
      +'</tr>';
  }});
  return rows;
}}

function _xyBuildStatsTable(cid){{
  var s=_XY_STATE[cid]; if(!s) return '';
  var gs=_xyCalcStats(cid); if(!gs||!gs.length) return '';
  var xref=s.xref!=null?+s.xref:((s.xmin0+s.xmax0)/2);
  var xlo=parseFloat((s.xmin0).toFixed(4)), xhi=parseFloat((s.xmax0).toFixed(4));
  var xstep=parseFloat(((xhi-xlo)/200).toFixed(6));
  return '<div style="padding:6px;background:#f8f9fa;border:1px solid #ddd;border-radius:4px;overflow-x:auto">'
    +'<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;flex-wrap:wrap">'
      +'<b style="font-size:12px;color:#2c3e50">Group Stats</b>'
      +'<span style="font-size:11px;color:#555">X:</span>'
      +'<input type="range" id="xy-xref-sl-'+cid+'" data-cid="'+cid+'" min="'+xlo+'" max="'+xhi+'" step="'+xstep+'" value="'+xref.toFixed(4)+'" style="width:90px;accent-color:#3498db" oninput="_xyXrefSlider(this)">'
      +'<input type="number" id="xy-xref-txt-'+cid+'" data-cid="'+cid+'" min="'+xlo+'" max="'+xhi+'" step="'+xstep+'" value="'+xref.toFixed(4)+'" style="width:72px;font-size:11px;padding:1px 3px;border:1px solid #aaa;border-radius:3px;text-align:center" oninput="_xyXrefText(this)">'
      +'<span style="font-size:11px;color:#888">'+_escH(s.xLabel||'')+'</span>'
    +'</div>'
    +'<table style="border-collapse:collapse;width:100%;font-family:monospace;font-size:12px">'
    +'<thead style="background:#2c3e50;color:#fff"><tr>'
    +(function(){{var _xygf=_xyGroupFields(s);return _xygf?('<th style="padding:2px 5px;text-align:left;border:1px solid #666;display:none">Group</th>'+_xygf.map(function(f){{return '<th style="padding:2px 5px;text-align:left;border:1px solid #666">'+_escH(f)+'</th>';}}).join('')):'<th style="padding:2px 5px;text-align:left;border:1px solid #666">Group</th>';}})()
    +'<th style="padding:2px 5px;text-align:center;border:1px solid #666">N</th>'
    +'<th style="padding:2px 5px;text-align:center;border:1px solid #666">R\u00b2</th>'
    +'<th style="padding:2px 5px;text-align:center;border:1px solid #666">UPM (Med)</th>'
    +'<th style="padding:2px 5px;text-align:center;border:1px solid #666">Vmin (Med)</th>'
    +'<th style="padding:2px 5px;text-align:center;border:1px solid #666" id="xy-xref-hdr-'+cid+'">Y@X='+_fmtUpm(xref)+'</th>'
    +'<th style="padding:2px 5px;text-align:center;border:1px solid #666">Fit Line</th>'
    +'</tr></thead>'
    +'<tbody id="xy-stats-tbody-'+cid+'">'+_xyStatsRows(gs,xref)+'</tbody>'
    +'</table></div>';
}}

function _xyUpdateModalStats(cid){{
  var el=document.getElementById('xy-modal-stats-'+cid); if(!el) return;
  var s=_XY_STATE[cid]; if(!s||!s.pts||!s.pts.length) return;
  var _ga=s.groupBy?_xyGetGroupArr(s):null;
  var _gf=_xyGroupFields(s); /* null or ['Material','Prog'] when both active */
  var MC=['#2980b9','#e74c3c','#27ae60','#9b59b6','#f39c12','#16a085','#e67e22','#34495e','#c0392b','#1abc9c'];
  var TH='style="padding:3px 8px;border-bottom:2px solid #bcd;font-size:11px;white-space:nowrap;text-align:right"';
  var THL='style="padding:3px 8px;border-bottom:2px solid #bcd;font-size:11px;white-space:nowrap;text-align:left"';
  var TD='style="padding:2px 8px;font-size:11px;white-space:nowrap;text-align:right;border-bottom:1px solid #eee"';
  var TDL='style="padding:2px 8px;font-size:11px;white-space:nowrap;text-align:left;border-bottom:1px solid #eee"';
  var rows=[];
  if(_ga){{
    var matList=[];
    _ga.forEach(function(m){{if(m&&matList.indexOf(m)<0)matList.push(m);}});
    matList.sort();
    matList.forEach(function(mat,gi){{
      if(s.matHidden&&s.matHidden[mat]) return;
      var col=MC[gi%MC.length];
      var gvals=[];
      s.pts.forEach(function(p,i){{if(_ga[i]===mat) gvals.push(p[1]);}});
      if(!gvals.length) return;
      var sv=gvals.slice().sort(function(a,b){{return a-b;}});
      var med=sv.length%2===0?(sv[sv.length/2-1]+sv[sv.length/2])/2:sv[Math.floor(sv.length/2)];
      var mean=gvals.reduce(function(a,b){{return a+b;}},0)/gvals.length;
      var std2=0; gvals.forEach(function(v){{std2+=(v-mean)*(v-mean);}});std2=Math.sqrt(std2/gvals.length);
      var mn=Math.min.apply(null,gvals),mx=Math.max.apply(null,gvals);
      var gxvals=[];
      s.pts.forEach(function(p,i){{if(_ga[i]===mat) gxvals.push(p[0]);}});
      var sxv=gxvals.slice().sort(function(a,b){{return a-b;}});
      var medX=sxv.length?( sxv.length%2===0?(sxv[sxv.length/2-1]+sxv[sxv.length/2])/2:sxv[Math.floor(sxv.length/2)] ):null;
      /* Build group label cell(s) */
      var grpCells='';
      if(_gf){{
        var _parts=mat.split(' | ');
        _gf.forEach(function(f,fi){{
          var v=_parts[fi]!=null?_parts[fi]:mat;
          var prefix=fi===0?'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+col+';margin-right:5px"></span>':'';
          grpCells+='<td '+TDL+'>'+prefix+_escH(v)+'</td>';
        }});
      }}else{{
        grpCells='<td '+TDL+'><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+col+';margin-right:5px"></span>'+_escH(mat)+'</td>';
      }}
      rows.push('<tr>'+grpCells+
        '<td '+TD+'>'+gvals.length.toLocaleString()+'</td>'+
        '<td '+TD+'>'+(medX!=null?_fmtUpm(medX):'-')+'</td>'+
        '<td '+TD+'>'+_fmtVmin(mean)+' V</td>'+
        '<td '+TD+'>'+_fmtVmin(med)+' V</td>'+
        '<td '+TD+'>'+_fmtVmin(std2)+'</td>'+
        '<td '+TD+'>'+_fmtVmin(mn)+'</td>'+
        '<td '+TD+'>'+_fmtVmin(mx)+'</td>'+
        '</tr>');
    }});
  }}
  var nTot=s.nTotal!=null?s.nTotal:s.pts.length;
  var allVals=s.pts.map(function(p){{return p[1];}});
  var sv2=allVals.slice().sort(function(a,b){{return a-b;}});
  var med2=sv2.length%2===0?(sv2[sv2.length/2-1]+sv2[sv2.length/2])/2:sv2[Math.floor(sv2.length/2)];
  var mean2=allVals.reduce(function(a,b){{return a+b;}},0)/allVals.length;
  var std2b=0; allVals.forEach(function(v){{std2b+=(v-mean2)*(v-mean2);}});std2b=Math.sqrt(std2b/allVals.length);
  var mn2=Math.min.apply(null,allVals),mx2=Math.max.apply(null,allVals);
  var TDTOT='style="padding:3px 8px;font-size:11px;white-space:nowrap;text-align:right;font-weight:bold;border-top:2px solid #bcd;background:#f0f4fa"';
  var TDTOTL='style="padding:3px 8px;font-size:11px;white-space:nowrap;text-align:left;font-weight:bold;border-top:2px solid #bcd;background:#f0f4fa"';
  var allXVals=s.pts.map(function(p){{return p[0];}});
  var sx2=allXVals.slice().sort(function(a,b){{return a-b;}});
  var medX2=sx2.length%2===0?(sx2[sx2.length/2-1]+sx2[sx2.length/2])/2:sx2[Math.floor(sx2.length/2)];
  /* Total row: span or pad group column(s) */
  var totGrpCell=_gf
    ?('<td '+TDTOTL+' colspan="'+_gf.length+'">Total (selected)</td>')
    :('<td '+TDTOTL+'>Total (selected)</td>');
  rows.push('<tr>'+totGrpCell+
    '<td '+TDTOT+'>'+nTot.toLocaleString()+'</td>'+
    '<td '+TDTOT+'>'+_fmtUpm(medX2)+'</td>'+
    '<td '+TDTOT+'>'+_fmtVmin(mean2)+' V</td>'+
    '<td '+TDTOT+'>'+_fmtVmin(med2)+' V</td>'+
    '<td '+TDTOT+'>'+_fmtVmin(std2b)+'</td>'+
    '<td '+TDTOT+'>'+_fmtVmin(mn2)+'</td>'+
    '<td '+TDTOT+'>'+_fmtVmin(mx2)+'</td>'+
    '</tr>');
  /* Build header */
  var grpHdrCells=_gf
    ?_gf.map(function(f){{return '<th '+THL+'>'+_escH(f)+'</th>';}}).join('')
    :'<th '+THL+'>Material</th>';
  var html='<table style="border-collapse:collapse;margin-top:6px;width:100%">';
  html+='<thead><tr>'+grpHdrCells+'<th '+TH+'>N</th><th '+TH+'>UPM (Med)</th><th '+TH+'>Mean</th><th '+TH+'>Vmin (Med)</th><th '+TH+'>&sigma;</th><th '+TH+'>Min</th><th '+TH+'>Max</th></tr></thead>';
  html+='<tbody>'+rows.join('')+'</tbody></table>';
  el.innerHTML=html;
}}

function _xyRefreshStats(cid){{
  var el=document.getElementById('xy-stats-'+cid); if(!el) return;
  el.innerHTML=_xyBuildStatsTable(cid);
}}

function _xyRefreshStatsBody(cid,xref){{
  var tbody=document.getElementById('xy-stats-tbody-'+cid); if(!tbody) return;
  tbody.innerHTML=_xyStatsRows(_xyCalcStats(cid),xref);
}}

function _xyXrefSlider(el){{
  var cid=el.dataset.cid;
  var v=+el.value;
  _XY_STATE[cid].xref=v;
  var txt=document.getElementById('xy-xref-txt-'+cid); if(txt)txt.value=parseFloat(v).toFixed(4);
  var hdr=document.getElementById('xy-xref-hdr-'+cid); if(hdr)hdr.textContent='Y@X='+_fmtUpm(v);
  _xyRefreshStatsBody(cid,v);
}}

function _xyXrefText(el){{
  var cid=el.dataset.cid;
  var v=parseFloat(el.value);
  if(!isFinite(v))return;
  var s=_XY_STATE[cid]; if(!s)return;
  _XY_STATE[cid].xref=v;
  var sl=document.getElementById('xy-xref-sl-'+cid); if(sl)sl.value=v;
  var hdr=document.getElementById('xy-xref-hdr-'+cid); if(hdr)hdr.textContent='Y@X='+_fmtUpm(v);
  _xyRefreshStatsBody(cid,v);
}}


function _xyDragStart(evt, cid, chiStr){{
  var ci=parseInt(chiStr,10);
  if(isNaN(ci)) return;
  evt.preventDefault(); evt.stopPropagation();
  _XY_DRAG_STATE={{active:true,chartId:cid,chIdx:ci}};
}}

function _xyToggleCh(el){{
  var cid=el.dataset.cid; var ci=+el.dataset.chi;
  var s=_XY_STATE[cid]; if(!s) return;
  if(!s.showCh) s.showCh=[false,false];
  s.showCh[ci]=el.checked;
  _xyRender(cid);
}}

function _xyToggleGroup(cid){{
  var s=_XY_STATE[cid]; if(!s) return;
  var chk=document.getElementById('xy-grp-chk-'+cid);
  s.groupByMat=chk?chk.checked:!s.groupByMat;
  s.groupBy=!!(s.groupByMat||s.groupByProg);
  s.matHidden={{}};
  _xyRender(cid);
  _xyUpdateMatBtns(cid);
}}

function _xyToggleGroupProg(cid){{
  var s=_XY_STATE[cid]; if(!s) return;
  var chk=document.getElementById('xy-grp-prog-chk-'+cid);
  s.groupByProg=chk?chk.checked:!s.groupByProg;
  s.groupBy=!!(s.groupByMat||s.groupByProg);
  s.matHidden={{}};
  _xyRender(cid);
  _xyUpdateMatBtns(cid);
}}

function _xyToggleMat(cid, mat){{
  var s=_XY_STATE[cid]; if(!s) return;
  if(!s.matHidden) s.matHidden={{}};
  if(s.matHidden[mat]) {{ delete s.matHidden[mat]; }} else {{ s.matHidden[mat]=true; }}
  _xyRender(cid);
  _xyUpdateMatBtns(cid);
}}

function _xyToggleMatIdx(cid, idx){{
  var s=_XY_STATE[cid]; if(!s) return;
  var _ga=_xyGetGroupArr(s);
  var _lst=[];
  if(_ga) _ga.forEach(function(k){{if(k&&_lst.indexOf(k)<0)_lst.push(k);}});
  _lst.sort();
  var mat=_lst[idx]; if(!mat) return;
  _xyToggleMat(cid, mat);
}}

function _xyClearMatFilter(cid){{
  var s=_XY_STATE[cid]; if(!s) return;
  s.matHidden={{}};
  _xyRender(cid);
  _xyUpdateMatBtns(cid);
}}

function _xyUpdateMatBtns(cid){{
  var s=_XY_STATE[cid]; if(!s) return;
  var el=document.getElementById('xy-mat-ctrl-'+cid); if(!el) return;
  var _ga=_xyGetGroupArr(s);
  var _lst=[];
  if(_ga) _ga.forEach(function(k){{if(k&&_lst.indexOf(k)<0)_lst.push(k);}});
  _lst.sort();
  var _lbl='&#9660; Group filter:';
  if(!s.groupBy||!_lst.length){{el.style.display='none';return;}}
  el.style.display='';
  var _MC=['#2980b9','#e74c3c','#27ae60','#9b59b6','#f39c12','#16a085','#e67e22','#34495e','#c0392b','#1abc9c'];
  var html='<div style="display:flex;flex-wrap:wrap;gap:4px;padding:2px 0;align-items:center">';
  html+='<span style="font-size:10px;color:#666;font-weight:bold;white-space:nowrap">'+_lbl+'</span>';
  var _anyHid=s.matHidden&&Object.keys(s.matHidden).length>0;
  html+='<button data-cid="'+_escH(cid)+'" onclick="_xyClearMatFilter(this.dataset.cid)" '+
    'style="font-size:9px;padding:1px 6px;border:1px solid #bbb;border-radius:8px;cursor:pointer;'+
    'background:'+(_anyHid?'#e8f0fe':'#f0f0f0')+';color:#444;white-space:nowrap">Show all</button>';
  _lst.forEach(function(mat,gi){{
    var col=_MC[gi%_MC.length];
    var hidden=!!(s.matHidden&&s.matHidden[mat]);
    html+='<button data-cid="'+_escH(cid)+'" data-mi="'+gi+'" onclick="_xyToggleMatIdx(this.dataset.cid,+this.dataset.mi)" '+
      'style="font-size:10px;padding:2px 8px;border:2px solid '+col+';border-radius:10px;cursor:pointer;transition:background 0.1s,color 0.1s;'+
      'background:'+(hidden?'#fff':col)+';color:'+(hidden?col:'#fff')+';white-space:nowrap">'+
      (hidden?'<s>':'')+_escH(mat)+(hidden?'</s>':'')+'</button>';
  }});
  html+='</div>';
  _safeInnerHTML(el,html);
}}

function _xySaveUserState(cid){{
  var s=_XY_STATE[cid]; if(!s) return null;
  var rz=document.getElementById('xy-resize-'+cid);
  var _h=rz&&rz.offsetHeight>80?rz.offsetHeight:s.H;
  var _w=rz&&rz.offsetWidth>80?rz.offsetWidth:s.W;
  var _xCust=Math.abs(s.xmin-s.xmin0)>1e-9||Math.abs(s.xmax-s.xmax0)>1e-9;
  var _yCust=Math.abs(s.ymin-s.ymin0)>1e-9||Math.abs(s.ymax-s.ymax0)>1e-9;
  return {{logX:!!s.logX,logY:!!s.logY,H:_h,W:_w,
           xmin:_xCust?s.xmin:null,xmax:_xCust?s.xmax:null,
           ymin:_yCust?s.ymin:null,ymax:_yCust?s.ymax:null}};
}}

function _xyRestoreUserState(cid,saved){{
  if(!saved) return;
  var s=_XY_STATE[cid]; if(!s) return;
  var changed=false;
  if(saved.logX){{s.logX=true; var cb=document.getElementById('xy-logx-'+cid); if(cb)cb.checked=true; changed=true;}}
  if(saved.logY){{s.logY=true; var cb=document.getElementById('xy-logy-'+cid); if(cb)cb.checked=true; changed=true;}}
  if(saved.xmin!=null){{s.xmin=saved.xmin; changed=true;}}
  if(saved.xmax!=null){{s.xmax=saved.xmax; changed=true;}}
  if(saved.ymin!=null){{s.ymin=saved.ymin; changed=true;}}
  if(saved.ymax!=null){{s.ymax=saved.ymax; changed=true;}}
  if(saved.H&&Math.abs(saved.H-s.H)>4){{
    s.H=saved.H; changed=true;
    var rz=document.getElementById('xy-resize-'+cid);
    if(rz) rz.style.height=saved.H+'px';
    var hEl=document.getElementById('xy-h-'+cid); if(hEl) hEl.value=Math.round(saved.H)+'px';
  }}
  if(changed) _xyRender(cid);
}}

function _xyToggleLog(cid,axis){{
  var s=_XY_STATE[cid]; if(!s) return;
  var chk=document.getElementById('xy-log'+axis+'-'+cid);
  if(axis==='x') s.logX=chk?!!chk.checked:!s.logX;
  else s.logY=chk?!!chk.checked:!s.logY;
  if(!_XY_USER_PREFS[cid])_XY_USER_PREFS[cid]={{}};
  _XY_USER_PREFS[cid][axis==='x'?'logX':'logY']=axis==='x'?s.logX:s.logY;
  _xyRender(cid);
}}

function _xyApplyRange(cid){{
  var s=_XY_STATE[cid]; if(!s) return;
  function _pv(id,fb){{
    var el=document.getElementById(id); if(!el) return fb;
    var v=el.value.trim().toLowerCase();
    if(v===''||v==='auto') return null;
    var n=parseFloat(v); return isNaN(n)?null:n;
  }}
  var xn=_pv('xy-xmin-'+cid,null),xx=_pv('xy-xmax-'+cid,null);
  var yn=_pv('xy-ymin-'+cid,null),yx=_pv('xy-ymax-'+cid,null);
  s.xmin=xn!=null?xn:s.xmin0; s.xmax=xx!=null?xx:s.xmax0;
  s.ymin=yn!=null?yn:s.ymin0; s.ymax=yx!=null?yx:s.ymax0;
  _xyRender(cid);
}}

function _xySetH(cid){{
  var el=document.getElementById('xy-h-'+cid); if(!el) return;
  var h=parseInt(el.value.replace(/[^0-9]/g,''),10);
  if(isNaN(h)||h<80) return;
  var s=_XY_STATE[cid]; if(!s) return;
  s.H=h;
  if(!_XY_USER_PREFS[cid])_XY_USER_PREFS[cid]={{}};
  _XY_USER_PREFS[cid].H=h;
  var rz=document.getElementById('xy-resize-'+cid);
  if(rz) rz.style.height=h+'px';
  _xyRender(cid);
}}

function _flowRdCalc(mod){{
  var idSafe=_flowSafeId(mod);
  var thrEl=document.getElementById('flow-rd-thr-'+idSafe);
  var freqEl=document.getElementById('flow-rd-freq-'+idSafe);
  var resEl=document.getElementById('flow-rd-result-'+idSafe);
  if(!thrEl||!freqEl||!resEl)return;
  var thr=parseFloat(thrEl.value), tgtGhz=parseFloat(freqEl.value);
  if(!(thr===thr)||!(tgtGhz===tgtGhz)){{_safeInnerHTML(resEl,'<span style="color:#e74c3c">Enter valid threshold (V) and target frequency (GHz).</span>');return;}}
  var fd=FLOW_DATA[mod]||{{}}, ak=activeKeys();
  var instResults=[];
  (fd.instances||[]).forEach(function(inst){{
    var allPts=[];
    (inst.freqs||[]).forEach(function(fr){{
      var fGhz=fr.freq_mhz/1000.0;
      (fr.rows||[]).forEach(function(r){{
        if(!ak.has(_flowNormKey(r)))return;
        if(r[5]===null||r[5]===undefined||r[5]!==r[5])return;
        allPts.push([fGhz,r[5],String(r[7]||_lotMat(r[1])||'All')]);
      }});
    }});
    var nFail=0,nRec=0,nStill=0;
    if(allPts.length){{  
      var grps={{}};
      allPts.forEach(function(p){{if(!grps[p[2]])grps[p[2]]=[];grps[p[2]].push([p[0],p[1]]);}});
      var fits={{}};
      Object.keys(grps).forEach(function(g){{
        var gp=grps[g];
        fits[g]=gp.length>=3?_xyPoly2Fit(gp):_xyRegress(gp);
      }});
      var rdF=function(g,x){{
        var f=fits[g]; if(!f)return null;
        return f.a!==undefined?f.a*x*x+f.b*x+f.c:f.slope*x+f.intercept;
      }};
      allPts.forEach(function(p){{
        if(p[1]<=thr)return;
        nFail++;
        var fu=rdF(p[2],p[0]),ft=rdF(p[2],tgtGhz);
        if(fu===null||ft===null){{nStill++;return;}}
        ((p[1]-fu)+ft<=thr)?nRec++:nStill++;
      }});
    }}
    instResults.push({{label:inst.label,nFail:nFail,nRec:nRec,nStill:nStill}});
  }});
  var h='<table style="border-collapse:collapse;font-size:11px">';
  h+='<thead><tr>';
  h+='<th style="padding:4px 10px;background:#1a4a7a;color:#fff;border:1px solid #14396b">Instance</th>';
  h+='<th style="padding:4px 10px;background:#1a4a7a;color:#fff;border:1px solid #14396b">Fail (Vmin&gt;'+_fmtVmin(thr)+')</th>';
  h+='<th style="padding:4px 10px;background:#27ae60;color:#fff;border:1px solid #1e8449">&#9650; Recover</th>';
  h+='<th style="padding:4px 10px;background:#c0392b;color:#fff;border:1px solid #922b21">&#9660; Still Fail</th>';
  h+='<th style="padding:4px 10px;background:#1a4a7a;color:#fff;border:1px solid #14396b">% Rec</th>';
  h+='</tr></thead><tbody>';
  var totF=0,totR=0,totS=0;
  instResults.forEach(function(r,i){{
    var bg=i%2===0?'#f7fafd':'#fff';
    var pct=r.nFail>0?(r.nRec/r.nFail*100).toFixed(1)+'%':'—';
    h+='<tr style="background:'+bg+'">';
    h+='<td style="padding:4px 10px;border:1px solid #dde;font-weight:bold;color:#1a4a7a">'+_escH(r.label)+'</td>';
    h+='<td style="padding:4px 10px;border:1px solid #dde;text-align:center">'+r.nFail+'</td>';
    h+='<td style="padding:4px 10px;border:1px solid #dde;text-align:center;color:#27ae60;font-weight:bold">'+r.nRec+'</td>';
    h+='<td style="padding:4px 10px;border:1px solid #dde;text-align:center;color:#c0392b;font-weight:bold">'+r.nStill+'</td>';
    h+='<td style="padding:4px 10px;border:1px solid #dde;text-align:center">'+pct+'</td>';
    h+='</tr>';
    totF+=r.nFail;totR+=r.nRec;totS+=r.nStill;
  }});
  var totPct=totF>0?(totR/totF*100).toFixed(1)+'%':'—';
  h+='<tr style="background:#f0f4f8;font-weight:bold;border-top:2px solid #c9d7e8">';
  h+='<td style="padding:4px 10px;border:1px solid #dde">Total</td>';
  h+='<td style="padding:4px 10px;border:1px solid #dde;text-align:center">'+totF+'</td>';
  h+='<td style="padding:4px 10px;border:1px solid #dde;text-align:center;color:#27ae60">'+totR+'</td>';
  h+='<td style="padding:4px 10px;border:1px solid #dde;text-align:center;color:#c0392b">'+totS+'</td>';
  h+='<td style="padding:4px 10px;border:1px solid #dde;text-align:center">'+totPct+'</td>';
  h+='</tr></tbody></table>';
  h+='<div style="color:#888;font-size:10px;margin-top:3px">&#8594; '+tgtGhz+' GHz | threshold: '+_fmtVmin(thr)+' V | trend fit uses all freq data per instance</div>';
  _safeInnerHTML(resEl,h);
}}

function _xyToggleRolldown(cid){{
  var s=_XY_STATE[cid]; if(!s)return;
  var cb=document.getElementById('xy-rd-chk-'+cid);
  s.rolldown=cb?!!cb.checked:!s.rolldown;
  var w=document.getElementById('xy-rd-wrap-'+cid);
  if(w)w.style.display=s.rolldown?'inline-flex':'none';
  if(!_XY_USER_PREFS[cid])_XY_USER_PREFS[cid]={{}};
  _XY_USER_PREFS[cid].rolldown=s.rolldown;
  _xyRender(cid);
}}

function _xyApplyRolldown(cid){{
  var s=_XY_STATE[cid]; if(!s)return;
  var te=document.getElementById('xy-rd-thr-'+cid);
  var fe=document.getElementById('xy-rd-freq-'+cid);
  var t=te?parseFloat(te.value):NaN, f=fe?parseFloat(fe.value):NaN;
  s.rdThreshold=isNaN(t)?null:t;
  s.rdTargetFreq=isNaN(f)?null:f;
  if(!_XY_USER_PREFS[cid])_XY_USER_PREFS[cid]={{}};
  _XY_USER_PREFS[cid].rdThreshold=s.rdThreshold;
  _XY_USER_PREFS[cid].rdTargetFreq=s.rdTargetFreq;
  _xyRender(cid);
}}

function _xyUpdateChDisp(cid){{
  var s=_XY_STATE[cid]; if(!s) return;
  var el=document.getElementById('xy-ch-disp-'+cid); if(!el) return;
  var c1=s.ch[0],c2=s.ch[1];
  el.innerHTML='<span style="color:#e74c3c">CH1: X='+_fmtUpm(c1.x)+'&nbsp;&nbsp;Y='+_fmtVmin(c1.y)+'</span>'+
    '&nbsp;&nbsp;|&nbsp;&nbsp;'+
    '<span style="color:#2980b9">CH2: X='+_fmtUpm(c2.x)+'&nbsp;&nbsp;Y='+_fmtVmin(c2.y)+'</span>'+
    '&nbsp;&nbsp;|&nbsp;&nbsp;'+
    '<span style="color:#555">\u0394X='+_fmtUpm(Math.abs(c2.x-c1.x))+'&nbsp;&nbsp;\u0394Y='+_fmtVmin(Math.abs(c2.y-c1.y))+'</span>';
}}

var _PM_GBY=[];
function _setPmGby(field,checked){{
  if(field==='none'){{_PM_GBY=[];}}
  else{{
    var idx=_PM_GBY.indexOf(field);
    if(checked&&idx<0)_PM_GBY.push(field);
    else if(!checked&&idx>=0)_PM_GBY.splice(idx,1);
  }}
  if(_PM_GBY.length>0){{
    var none=document.getElementById('pm-gby-none');
    if(none)none.checked=false;
  }}else{{
    var none2=document.getElementById('pm-gby-none');
    if(none2)none2.checked=true;
  }}
  var curParam=document.getElementById('pm-title');
  if(curParam)_buildParamModalChart(curParam._param||SEL_PARAM);
}}
function _buildParamModalChart(param){{
  var cont=document.getElementById('pm-body');
  if(!cont)return;
  var meta=PCM_PARAM_META[param]||{{}};
  var ak=activeKeys();
  /* Build groupby toolbar */
  var gbyDefs=[['none','None'],['material','Material'],['prog6248','Prog-6248'],['progU1U5','Prog-U1U5']];
  var gbyBar='<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 8px;background:#f0f2f5;border-radius:5px;margin-bottom:8px">'
    +'<b style="font-size:12px;color:#2c3e50">Group by:</b>';
  gbyDefs.forEach(function(gd){{
    var isNone=gd[0]==='none';
    var chk=isNone?(_PM_GBY.length===0):(_PM_GBY.indexOf(gd[0])>=0);
    gbyBar+='<label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:3px">'
      +'<input type="'+(isNone?'radio':'checkbox')+'" id="pm-gby-'+gd[0]+'"'+(chk?' checked':'')
      +' onchange="_setPmGby(\\''+gd[0]+'\\',this.checked)">'+gd[1]+'</label>';
  }});
  gbyBar+='</div>';
  var cm=_cMapWith(PCM_ROWS.filter(function(r){{return r.param===param&&ak.has(_rKey(r));}}),_PM_GBY);
  /* Collect values per group-by key */
  var grpVals={{}},grpOrder=[];
  PCM_ROWS.forEach(function(r){{
    if(r.param!==param)return;
    if(!ak.has(_rKey(r)))return;
    var gk=_grpKeyWith(r,_PM_GBY);
    if(!grpVals[gk]){{grpVals[gk]=[];grpOrder.push(gk);}}
    (r.die_values||[]).forEach(function(v){{if(v!=null&&isFinite(v))grpVals[gk].push(v);}});
  }});
  var allVals=[];
  grpOrder.forEach(function(gk){{allVals=allVals.concat(grpVals[gk]);}});
  if(!allVals.length){{
    cont.innerHTML='<div style="padding:24px;color:#888;text-align:center">No data for active selection</div>';
    return;
  }}
  var srt=allVals.slice().sort(function(a,b){{return a-b;}});
  var p01=srt[Math.floor(srt.length*0.01)];
  var p99=srt[Math.min(srt.length-1,Math.ceil(srt.length*0.99))];
  var med=_med(allVals);
  var clipped=(srt.length>=10&&p99>p01)?allVals.filter(function(v){{return v>=p01&&v<=p99;}}):allVals;
  var sd=_std(clipped);
  var mu=allVals.reduce(function(a,v){{return a+v;}},0)/allVals.length;
  var s3lo=mu-3*sd,s3hi=mu+3*sd,s6lo=mu-6*sd,s6hi=mu+6*sd;
  var mn=srt[0],mx=srt[srt.length-1];
  var cv=(med&&med!==0)?Math.abs(sd/med*100):null;
  var lsl=meta.lsl!=null?+meta.lsl:null;
  var usl=meta.usl!=null?+meta.usl:null;
  var unit=meta.unit||'';
  /* Histogram */
  var rng=mx-mn||Math.abs(mn)*0.02||0.1;
  var nBins=Math.max(12,Math.min(50,Math.ceil(Math.sqrt(allVals.length)*2.5)));
  var binW=rng/nBins;
  var xPad=Math.max(rng*0.06,binW*0.5);
  var xLo=mn-xPad,xHi=mx+xPad,xRng=xHi-xLo;
  if(lsl!=null&&lsl>=xLo-5*rng)xLo=Math.min(xLo,lsl-xPad);
  if(usl!=null&&usl<=xHi+5*rng)xHi=Math.max(xHi,usl+xPad);
  xRng=xHi-xLo;
  var grpCounts={{}},maxCnt=1;
  grpOrder.forEach(function(gk){{
    var cnts=new Array(nBins).fill(0);
    grpVals[gk].forEach(function(v){{
      var bi=Math.min(Math.floor((v-mn)/binW),nBins-1);
      if(bi>=0&&bi<nBins)cnts[bi]++;
    }});
    grpCounts[gk]=cnts;
    var gc=Math.max.apply(null,cnts)||0;
    if(gc>maxCnt)maxCnt=gc;
  }});
  var maxY=Math.ceil(maxCnt*1.15)||1;
  /* SVG histogram */
  var svgW=820,svgH=300,ML=64,MR=20,MT=36,MB=68;
  var plotW=svgW-ML-MR,plotH=svgH-MT-MB;
  function xp(v){{return ML+(v-xLo)/xRng*plotW;}}
  function yp(c){{return MT+plotH-(c/maxY)*plotH;}}
  var p=['<svg width="100%" viewBox="0 0 '+svgW+' '+svgH+'" style="display:block;background:#f8f9fa">'];
  p.push('<rect x="'+ML+'" y="'+MT+'" width="'+plotW+'" height="'+plotH+'" fill="#fff" stroke="#ccc" stroke-width="1"/>');
  for(var yi=0;yi<=5;yi++){{
    var yv=Math.round(maxY*yi/5);
    var ypv=(MT+plotH-(yv/maxY)*plotH).toFixed(1);
    p.push('<line x1="'+ML+'" y1="'+ypv+'" x2="'+(ML+plotW)+'" y2="'+ypv+'" stroke="rgba(0,0,0,0.09)" stroke-width="0.8"/>');
    p.push('<text x="'+(ML-4)+'" y="'+ypv+'" text-anchor="end" dominant-baseline="middle" font-size="14" fill="#555">'+yv+'</text>');
  }}
  p.push('<text transform="translate(13,'+(MT+plotH/2)+') rotate(-90)" text-anchor="middle" font-size="14" fill="#555">Count</text>');
  var nGrps=grpOrder.length;
  var bpxW=binW/xRng*plotW;
  var barW=Math.max(0.5,(bpxW-1)/Math.max(1,nGrps));
  grpOrder.forEach(function(gk,gi){{
    var gcol=cm.map[gk]||_cPal(gi);
    var cnts=grpCounts[gk];
    var offsetX=(nGrps>1)?((gi-(nGrps-1)/2)*barW):0;
    for(var b=0;b<nBins;b++){{
      if(!cnts[b])continue;
      var bx=(xp(mn+b*binW)+offsetX).toFixed(1);
      var bh=(cnts[b]/maxY*plotH).toFixed(1);
      var by=(MT+plotH-cnts[b]/maxY*plotH).toFixed(1);
      p.push('<rect x="'+bx+'" y="'+by+'" width="'+(Math.max(0.5,barW-0.5)).toFixed(1)+'" height="'+bh+'" fill="'+gcol+'" opacity="0.72" rx="1"/>');
    }}
  }});
  function _vline(val,col,lbl,lblSide){{
    var xv=xp(val).toFixed(1);
    if(parseFloat(xv)<ML-2||parseFloat(xv)>ML+plotW+2)return;
    p.push('<line x1="'+xv+'" y1="'+MT+'" x2="'+xv+'" y2="'+(MT+plotH)+'" stroke="'+col+'" stroke-width="2" stroke-dasharray="5,4"/>');
    var anchor=(lblSide==='right')?'start':'end';
    var tx=(lblSide==='right')?(parseFloat(xv)+4):(parseFloat(xv)-4);
    p.push('<text x="'+tx+'" y="'+(MT-7)+'" text-anchor="'+anchor+'" font-size="13" font-weight="bold" fill="'+col+'">'+_escH(lbl)+'</text>');
  }}
  if(lsl!=null)_vline(lsl,'#c0392b','LSL','right');
  if(usl!=null)_vline(usl,'#2980b9','USL','left');
  if(med!=null)_vline(med,'#27ae60','Median','right');
  if(meta.target!=null)_vline(+meta.target,'#d35400','Target','left');
  _vline(s3lo,'#e67e22','-3σ','left');
  _vline(s3hi,'#e67e22','+3σ','right');
  _vline(s6lo,'#8e44ad','-6σ','left');
  _vline(s6hi,'#8e44ad','+6σ','right');
  for(var xi=0;xi<=7;xi++){{
    var xv2=xLo+xRng*xi/7;
    var xpv2=(ML+xi/7*plotW).toFixed(1);
    p.push('<text x="'+xpv2+'" y="'+(MT+plotH+18)+'" text-anchor="middle" font-size="13" fill="#555">'+_fmt(xv2)+'</text>');
  }}
  var unitLbl=unit?' ('+_escH(unit)+')':'';
  p.push('<text x="'+(ML+plotW/2)+'" y="'+(svgH-4)+'" text-anchor="middle" font-size="14" font-weight="bold" fill="#333">'+_escH(meta.name||param)+unitLbl+'</text>');
  p.push('</svg>');
  /* Strip chart */
  var sW=svgW,sH=70,sML=ML,sMR=MR,sMT=18,sMB=14;
  var sPlotW=sW-sML-sMR,sPlotH=sH-sMT-sMB;
  var ps=['<svg width="100%" viewBox="0 0 '+sW+' '+sH+'" style="display:block;background:#fff;border-top:1px solid #e8e8e8">'];
  ps.push('<rect x="'+sML+'" y="'+sMT+'" width="'+sPlotW+'" height="'+sPlotH+'" fill="#f8f9fa" rx="2"/>');
  if(lsl!=null){{var lx=xp(lsl).toFixed(1);ps.push('<line x1="'+lx+'" y1="'+sMT+'" x2="'+lx+'" y2="'+(sMT+sPlotH)+'" stroke="#c0392b" stroke-width="1.5" stroke-dasharray="4,3"/>');}}  
  if(usl!=null){{var ux=xp(usl).toFixed(1);ps.push('<line x1="'+ux+'" y1="'+sMT+'" x2="'+ux+'" y2="'+(sMT+sPlotH)+'" stroke="#2980b9" stroke-width="1.5" stroke-dasharray="4,3"/>');}}  
  if(med!=null){{var mx2=xp(med).toFixed(1);ps.push('<line x1="'+mx2+'" y1="'+sMT+'" x2="'+mx2+'" y2="'+(sMT+sPlotH)+'" stroke="#27ae60" stroke-width="2" stroke-dasharray="5,3"/>');}}  
  if(meta.target!=null){{var tx2=xp(+meta.target).toFixed(1);if(parseFloat(tx2)>=sML-2&&parseFloat(tx2)<=sML+sPlotW+2)ps.push('<line x1="'+tx2+'" y1="'+sMT+'" x2="'+tx2+'" y2="'+(sMT+sPlotH)+'" stroke="#d35400" stroke-width="2" stroke-dasharray="5,3"/>');}}  
  (function(){{var _sl=[[s3lo,'#e67e22'],[s3hi,'#e67e22'],[s6lo,'#8e44ad'],[s6hi,'#8e44ad']];_sl.forEach(function(sl){{var sx=xp(sl[0]).toFixed(1);if(parseFloat(sx)<sML-2||parseFloat(sx)>sML+sPlotW+2)return;ps.push('<line x1="'+sx+'" y1="'+sMT+'" x2="'+sx+'" y2="'+(sMT+sPlotH)+'" stroke="'+sl[1]+'" stroke-width="1.2" stroke-dasharray="3,3"/>');}})}})();
  if(srt.length>=4){{
    var q1=srt[Math.floor(srt.length*0.25)],q3=srt[Math.min(srt.length-1,Math.ceil(srt.length*0.75))];
    var qx1=Math.max(sML,xp(q1)),qx2=Math.min(sML+sPlotW,xp(q3));
    if(qx2>qx1)ps.push('<rect x="'+qx1.toFixed(1)+'" y="'+sMT+'" width="'+(qx2-qx1).toFixed(1)+'" height="'+sPlotH+'" fill="rgba(39,174,96,0.12)" stroke="#27ae60" stroke-width="1"/>');
  }}
  var stripDots={{}};
  grpOrder.forEach(function(gk,gi){{
    var gcol=cm.map[gk]||_cPal(gi);
    grpVals[gk].forEach(function(v,vi){{
      if(v<xLo||v>xHi)return;
      var cx=xp(v).toFixed(1);
      var jitter=((_sRand(gi*997+vi)-0.5)*sPlotH*0.7);
      var cy=(sMT+sPlotH/2+jitter).toFixed(1);
      if(!stripDots[gcol])stripDots[gcol]='';
      stripDots[gcol]+='M'+cx+','+cy+'m-3,0a3,3,0,1,0,6,0a3,3,0,1,0,-6,0';
    }});
  }});
  Object.keys(stripDots).forEach(function(col){{ps.push('<path d="'+stripDots[col]+'" fill="'+col+'" opacity="0.60"/>');}}); 
  ps.push('<text x="'+sML+'" y="12" font-size="11" fill="#888">Strip (each dot = one measurement)</text>');
  ps.push('</svg>');
  /* Stats row */
  function _sb(lbl,val,col){{
    return '<div class="pm-stat"><span class="pm-stat-lbl">'+lbl+'</span>'
      +'<span class="pm-stat-val" style="color:'+(col||'#2c3e50')+'">'+val+'</span></div>';
  }}
  var statsHtml='<div class="pm-stat-row">'
    +_sb('N',allVals.length)
    +_sb('Mean',_fmt(mu,4),'#2c3e50')
    +_sb('Median',_fmt(med,4),'#27ae60')
    +_sb('\u03c3',_fmt(sd,3))
    +_sb('Spread (%)',cv!=null?cv.toFixed(1)+'%':'\u2014')
    +_sb('\u00b13\u03c3',_fmt(s3lo,3)+' ~ '+_fmt(s3hi,3),'#e67e22')
    +_sb('\u00b16\u03c3',_fmt(s6lo,3)+' ~ '+_fmt(s6hi,3),'#8e44ad')
    +_sb('P1',p01!=null?_fmt(p01):'\u2014','#7f8c8d')
    +_sb('P99',p99!=null?_fmt(p99):'\u2014','#7f8c8d')
    +(lsl!=null?_sb('LSL',_fmt(lsl),'#c0392b'):'')
    +(usl!=null?_sb('USL',_fmt(usl),'#2980b9'):'')
    +(unit?_sb('Unit',_escH(unit),'#555'):'')
    +'</div>';
  /* Group legend */
  var legHtml='';
  if(grpOrder.length>1){{
    legHtml='<div class="pm-grp-leg">';
    grpOrder.forEach(function(gk,gi){{
      var gcol=cm.map[gk]||_cPal(gi);
      legHtml+='<span style="display:flex;align-items:center;gap:3px">'
        +'<span style="width:10px;height:10px;background:'+gcol+';display:inline-block;border-radius:2px"></span>'
        +_escH(gk)+'</span>';
    }});
    legHtml+='</div>';
  }}
  /* Per-group stats table */
  var grpTblHtml='';
  if(grpOrder.length>=1){{
    function _medArr(a){{if(!a.length)return null;var s=a.slice().sort(function(x,y){{return x-y;}});var n=s.length;return n%2===0?(s[n/2-1]+s[n/2])/2:s[Math.floor(n/2)];}}
    var _fLabels={{'none':'Group','lot':'Lot','wafer':'Wafer','prog6248':'Prog-6248','progU1U5':'Prog-U1U5','material':'Material'}};
    var _gbyFields=_PM_GBY.length?_PM_GBY:['none'];
    var _showGrpCol=grpOrder.length>1||_PM_GBY.length>0;
    /* Map gk → per-field value array and UPM values */
    var _grpFieldMap={{}};
    var grpUpm={{}};  /* gk -> [upm_med values per wafer] */
    PCM_ROWS.forEach(function(r){{
      if(r.param!==param)return;
      if(!ak.has(_rKey(r)))return;
      var gk=_grpKeyWith(r,_PM_GBY);
      if(!_grpFieldMap[gk]){{
        var w=_WFR_LOOKUP[r.lot+'/'+r.wafer]||{{}};
        _grpFieldMap[gk]=_gbyFields.map(function(f){{
          if(f==='lot')return r.lot||'';
          if(f==='wafer')return String(r.wafer||'');
          if(f==='prog6248')return w.prog6248||'';
          if(f==='progU1U5')return w.progU1U5||'';
          if(f==='material')return w.material||(w.sort_lot?_lotMat(w.sort_lot):'Others');
          return gk;
        }});
      }}
      var wu=_WFR_LOOKUP[r.lot+'/'+r.wafer]||{{}};
      if(wu.upm_med!=null){{if(!grpUpm[gk])grpUpm[gk]=[];grpUpm[gk].push(wu.upm_med);}}
    }});
    var _grpCols=_showGrpCol?_gbyFields.map(function(f){{return '<th style="padding:4px 10px;text-align:left">'+_escH(_fLabels[f]||f)+'</th>';}}).join(''):'';
    grpTblHtml='<div style="margin-top:10px;overflow-x:auto">'
      +'<table style="border-collapse:collapse;font-size:12px;white-space:nowrap">'
      +'<thead><tr style="background:#2c3e50;color:#ecf0f1">'
      +_grpCols
      +'<th style="padding:4px 10px;text-align:right">N Die</th>'
      +'<th style="padding:4px 10px;text-align:right">UPM (Med)</th>'
      +'<th style="padding:4px 10px;text-align:right">Min</th>'
      +'<th style="padding:4px 10px;text-align:right">Median</th>'
      +'<th style="padding:4px 10px;text-align:right">Max</th>'
      +'<th style="padding:4px 10px;text-align:right">&sigma;</th>'
      +'<th style="padding:4px 10px;text-align:right">Spread%</th>'
      +'</tr></thead><tbody>';
    var _upmIsPct=(function(){{var _k=Object.keys(_WFR_LOOKUP)[0];return !!(_k&&_WFR_LOOKUP[_k].upm_is_pct);}})();
    grpOrder.forEach(function(gk,gi){{
      var gcol=cm.map[gk]||_cPal(gi);
      var sv=grpVals[gk].slice().sort(function(a,b){{return a-b;}});
      var gmed=_medArr(sv);
      var gmu=sv.length?sv.reduce(function(a,v){{return a+v;}},0)/sv.length:null;
      var gsd=null;
      if(sv.length>1){{var gmu2=gmu;gsd=Math.sqrt(sv.reduce(function(a,v){{return a+(v-gmu2)*(v-gmu2);}},0)/sv.length);}}
      var gcv=(gmed&&gmed!==0&&gsd!=null)?Math.abs(gsd/gmed*100):null;
      var bgCol=(gi%2===0)?'#fff':'#f7f9fc';
      var gupm=_medArr(grpUpm[gk]||[]);
      var _upmStr=gupm!=null?(_fmt(gupm,2)+(_upmIsPct?'%':'')):'—';
      var fParts=_grpFieldMap[gk]||[gk];
      var tdCells=_showGrpCol?fParts.map(function(v,fi){{
        var prefix=fi===0?'<span style="display:inline-block;width:9px;height:9px;background:'+gcol+';border-radius:2px;margin-right:5px"></span>':'';
        return '<td style="padding:3px 10px;border-bottom:1px solid #eee">'+prefix+_escH(v)+'</td>';
      }}).join(''):'';
      grpTblHtml+='<tr style="background:'+bgCol+'">'
        +tdCells
        +'<td style="padding:3px 10px;border-bottom:1px solid #eee;text-align:right">'+sv.length+'</td>'
        +'<td style="padding:3px 10px;border-bottom:1px solid #eee;text-align:right">'+_upmStr+'</td>'
        +'<td style="padding:3px 10px;border-bottom:1px solid #eee;text-align:right">'+(sv.length?_fmt(sv[0]):'\u2014')+'</td>'
        +'<td style="padding:3px 10px;border-bottom:1px solid #eee;text-align:right">'+(gmed!=null?_fmt(gmed):'\u2014')+'</td>'
        +'<td style="padding:3px 10px;border-bottom:1px solid #eee;text-align:right">'+(sv.length?_fmt(sv[sv.length-1]):'\u2014')+'</td>'
        +'<td style="padding:3px 10px;border-bottom:1px solid #eee;text-align:right">'+(gsd!=null?_fmt(gsd,3):'\u2014')+'</td>'
        +'<td style="padding:3px 10px;border-bottom:1px solid #eee;text-align:right">'+(gcv!=null?gcv.toFixed(1)+'%':'\u2014')+'</td>'
        +'</tr>';
    }});
    grpTblHtml+='</tbody></table></div>';
  }}
  cont.innerHTML=gbyBar+statsHtml+p.join('')+ps.join('')+grpTblHtml;
}}

/* ── Distribution tab ────────────────────────────────────────────────────── */
var _PDLY_H_P={{}};      // pn -> svg height (default 350)
var _PDLY_GBY_P={{}};    // pn -> [field, ...]  e.g. ['lot','wafer']

function toggleDistP(n){{
  _DIST_COLLAPSED[n]=!_DIST_COLLAPSED[n];
  var btn=document.getElementById('distp'+n+'-toggle');
  if(btn)btn.innerHTML=_DIST_COLLAPSED[n]?'&#9654;':'&#9660;';
  var body=document.getElementById('distp'+n+'-body');
  if(body)body.style.display=_DIST_COLLAPSED[n]?'none':'';
}}

function toggleGbyP(pn,field){{
  if(!_PDLY_GBY_P[pn])_PDLY_GBY_P[pn]=[];
  var idx=_PDLY_GBY_P[pn].indexOf(field);
  if(field==='none'){{
    _PDLY_GBY_P[pn]=[];
  }}else{{
    if(idx>=0)_PDLY_GBY_P[pn].splice(idx,1);
    else _PDLY_GBY_P[pn].push(field);
  }}
  _reBuildDistPanel(pn);
  _syncGbyBtnsP(pn);
}}
function _syncGbyBtnsP(pn){{
  var gby=_PDLY_GBY_P[pn]||[];
  ['none','lot','wafer','prog6248','progU1U5'].forEach(function(f){{
    var el=document.getElementById('pdly-gby-'+pn+'-'+f);
    if(!el)return;
    if(f==='none')el.checked=(gby.length===0);
    else el.checked=(gby.indexOf(f)>=0);
  }});
}}
function _reBuildDistPanel(pn){{
  var body=document.getElementById('distp'+pn+'-body');
  if(!body)return;
  // preserve control bar
  var ctrl=body.querySelector('.pdly-ctrl');
  var html=_buildDistCards(pn);
  var oldCards=body.querySelector('.pdly-cards');
  if(oldCards)oldCards.remove();
  var div=document.createElement('div');
  div.className='pdly-cards';
  div.innerHTML=html;
  body.appendChild(div);
}}

function _buildDistCtrlBar(pn){{
  var h=_PDLY_H_P[pn]||350;
  var gby=_PDLY_GBY_P[pn]||[];
  var gbyFields=[
    {{id:'none',   label:'None'}},
    {{id:'lot',    label:'Lot'}},
    {{id:'wafer',  label:'Wafer'}},
    {{id:'prog6248',label:'Prog 6248'}},
    {{id:'progU1U5',label:'Prog U1.U5'}}
  ];
  var cbHtml=gbyFields.map(function(f){{
    var chk=(f.id==='none')?(gby.length===0):(gby.indexOf(f.id)>=0);
    return '<label style="margin-right:10px;cursor:pointer;font-size:12px;color:#ecf0f1">'
      +'<input type="checkbox" id="pdly-gby-'+pn+'-'+f.id+'" '+(chk?'checked':'')+' '
      +'onchange="toggleGbyP('+pn+',\\''+f.id+'\\')" style="margin-right:3px">'+f.label+'</label>';
  }}).join('');
  return '<div class="pdly-ctrl" style="background:#1f3a50;padding:7px 12px;display:flex;align-items:center;flex-wrap:wrap;gap:8px;border-radius:4px 4px 0 0">'
    +'<span style="color:#bdc3c7;font-size:12px;font-weight:600;margin-right:4px">Group by:</span>'
    +cbHtml
    +'<div style="flex:1"></div>'
    +'<label style="color:#bdc3c7;font-size:12px;margin-right:4px">H:</label>'
    +'<input type="range" min="150" max="900" step="25" value="'+h+'" '
    +'oninput="this.nextElementSibling.textContent=this.value;_PDLY_H_P['+pn+']=+this.value;_reBuildDistPanel('+pn+')" '
    +'style="width:110px;cursor:pointer">'
    +'<span style="color:#ecf0f1;font-size:12px;min-width:30px">'+h+'</span>'
    +'</div>';
}}

function _buildDistCards(pn){{
  var panel=PCM_DIST_PANELS[pn]; if(!panel)return '';
  var ak=activeKeys();
  var gby=_PDLY_GBY_P[pn]||[];
  var svgH=_PDLY_H_P[pn]||350;
  var svgW=700,ML=72,MR=100,MT=40,MB=72;
  var plotW=svgW-ML-MR, plotH=svgH-MT-MB;
  var cards=[];

  panel.params.forEach(function(param){{
    var meta=PCM_PARAM_META[param]||{{}};
    // Collect all matching rows
    var activeRows=[];
    PCM_ROWS.forEach(function(r){{
      if(r.param!==param||!ak.has(_rKey(r)))return;
      activeRows.push(r);
    }});
    if(!activeRows.length)return;

    // Build per-group value arrays
    var cm=_cMapWith(activeRows,gby);
    var grpVals={{}};  // key -> []
    cm.keys.forEach(function(k){{grpVals[k]=[];}});
    activeRows.forEach(function(r){{
      var k=_grpKeyWith(r,gby);
      (r.die_values||[]).forEach(function(v){{
        if(v!=null&&isFinite(v))grpVals[k].push(v);
      }});
    }});
    var allVals=[];
    cm.keys.forEach(function(k){{allVals=allVals.concat(grpVals[k]);}});
    if(!allVals.length)return;

    // Stats on all values
    var N=allVals.length;
    var sorted=allVals.slice().sort(function(a,b){{return a-b;}});
    var med=_pct(sorted,50), sd=_std(allVals);
    var cv=(med&&med!==0)?Math.abs(sd/med*100):null;
    var p1=_pct(sorted,1), p99=_pct(sorted,99);
    var mn=sorted[0],mx=sorted[sorted.length-1];
    if(mn===mx){{mn-=0.5;mx+=0.5;}}

    // Sigma shading
    var s3lo=med-3*sd, s3hi=med+3*sd;
    var s6lo=med-6*sd, s6hi=med+6*sd;
    var lsl=meta.lsl!=null?+meta.lsl:null;
    var usl=meta.usl!=null?+meta.usl:null;
    var tgt=meta.target!=null?+meta.target:null;

    // OOS detection
    var oosSpec=false,oosSigma=false;
    if(lsl!=null){{var below=sorted.filter(function(v){{return v<lsl;}}).length; if(below>0)oosSpec=true;}}
    if(usl!=null){{var above=sorted.filter(function(v){{return v>usl;}}).length; if(above>0)oosSpec=true;}}
    if(sd>0){{
      var out6=sorted.filter(function(v){{return v<s6lo||v>s6hi;}}).length;
      if(out6>0)oosSigma=true;
    }}

    // Bins
    var nBins=Math.max(10,Math.min(40,Math.ceil(Math.sqrt(N)*2.2)));
    var binW=(mx-mn)/nBins;
    // Build per-group bin arrays
    var nGrps=cm.keys.length;
    var grpBins={{}};
    cm.keys.forEach(function(k){{
      var ba=[]; for(var i=0;i<nBins;i++)ba.push(0);
      (grpVals[k]||[]).forEach(function(v){{
        var b=Math.min(nBins-1,Math.floor((v-mn)/binW));
        ba[b]++;
      }});
      grpBins[k]=ba;
    }});
    // Max bin height across all groups
    var maxY=0;
    cm.keys.forEach(function(k){{
      var m=Math.max.apply(null,grpBins[k]);
      if(m>maxY)maxY=m;
    }});
    if(!maxY)return;

    // SVG
    function xPx(v){{return ML+(v-mn)/(mx-mn)*plotW;}}
    function yPx(c){{return MT+plotH-(c/maxY)*plotH;}}
    var ps=['<svg width="100%" height="'+svgH+'" viewBox="0 0 '+svgW+' '+svgH+'" style="display:block;max-width:700px;font-family:sans-serif">'];
    ps.push('<rect width="'+svgW+'" height="'+svgH+'" fill="#f8f9fa"/>');
    ps.push('<rect x="'+ML+'" y="'+MT+'" width="'+plotW+'" height="'+plotH+'" fill="white" stroke="#ccc" stroke-width="1"/>');

    // ±6σ shading (red, light)
    if(sd>0){{
      var x6lo=Math.max(xPx(s6lo),ML), x6hi=Math.min(xPx(s6hi),ML+plotW);
      if(x6lo>ML)ps.push('<rect x="'+ML+'" y="'+MT+'" width="'+(x6lo-ML).toFixed(1)+'" height="'+plotH+'" fill="rgba(192,57,43,.10)"/>');
      if(x6hi<ML+plotW)ps.push('<rect x="'+x6hi.toFixed(1)+'" y="'+MT+'" width="'+(ML+plotW-x6hi).toFixed(1)+'" height="'+plotH+'" fill="rgba(192,57,43,.10)"/>');
    }}
    // ±3σ shading (orange, lighter)
    if(sd>0){{
      var x3lo=Math.max(xPx(s3lo),ML), x3hi=Math.min(xPx(s3hi),ML+plotW);
      if(x3lo>ML)ps.push('<rect x="'+ML+'" y="'+MT+'" width="'+(x3lo-ML).toFixed(1)+'" height="'+plotH+'" fill="rgba(230,126,34,.08)"/>');
      if(x3hi<ML+plotW)ps.push('<rect x="'+x3hi.toFixed(1)+'" y="'+MT+'" width="'+(ML+plotW-x3hi).toFixed(1)+'" height="'+plotH+'" fill="rgba(230,126,34,.08)"/>');
    }}

    // Y-axis grid + labels
    for(var yi=0;yi<=5;yi++){{
      var yv=maxY*yi/5, yp=+(MT+plotH-(yv/maxY)*plotH).toFixed(1);
      ps.push('<line x1="'+ML+'" y1="'+yp+'" x2="'+(ML+plotW)+'" y2="'+yp+'" stroke="rgba(0,0,0,.10)" stroke-width=".8"/>');
      ps.push('<text x="'+(ML-4)+'" y="'+yp+'" text-anchor="end" dominant-baseline="middle" font-size="11" fill="#555">'+Math.round(yv)+'</text>');
    }}
    ps.push('<text transform="translate(14,'+(MT+plotH/2)+') rotate(-90)" text-anchor="middle" font-size="11" fill="#555">Samples</text>');

    // Histogram bars (side-by-side per group)
    var bpxD=plotW/nBins;
    var barW=nGrps>1?Math.max(0.5,(bpxD-1)/nGrps):(bpxD-1);
    cm.keys.forEach(function(k,gi){{
      var col=cm.map[k];
      grpBins[k].forEach(function(cnt,bi){{
        if(!cnt)return;
        var bh=(cnt/maxY)*plotH;
        var bx=(ML+bi*bpxD+gi*barW).toFixed(1);
        var by=(MT+plotH-bh).toFixed(1);
        ps.push('<rect x="'+bx+'" y="'+by+'" width="'+barW.toFixed(1)+'" height="'+bh.toFixed(1)+'" fill="'+col+'" opacity=".80"/>');
      }});
    }});

    // X-axis ticks + labels
    var xStep=_niceStep((mx-mn)/6);
    var x0=Math.ceil(mn/xStep)*xStep;
    for(var xi=x0;xi<=mx+xStep*0.01;xi+=xStep){{
      var xp=+(xPx(xi)).toFixed(1);
      if(xp<ML||xp>ML+plotW+1)continue;
      ps.push('<line x1="'+xp+'" y1="'+(MT+plotH)+'" x2="'+xp+'" y2="'+(MT+plotH+4)+'" stroke="#888" stroke-width="1"/>');
      ps.push('<text x="'+xp+'" y="'+(MT+plotH+16)+'" text-anchor="middle" font-size="10" fill="#444">'+_fmt(xi,3)+'</text>');
    }}
    // X axis unit label
    var xLabel=_escH(meta.name||param)+(meta.unit?' ('+_escH(meta.unit)+')':'');
    ps.push('<text x="'+(ML+plotW/2)+'" y="'+(svgH-4)+'" text-anchor="middle" font-size="11" fill="#555">'+xLabel+'</text>');

    // Reference lines (drawn after bars)
    function specLine(val,col,dash,w){{
      if(val==null)return;
      var xsv=xPx(val);
      if(xsv<ML-1||xsv>ML+plotW+1)return;
      ps.push('<line x1="'+xsv.toFixed(1)+'" y1="'+MT+'" x2="'+xsv.toFixed(1)+'" y2="'+(MT+plotH)+'" stroke="'+col+'" stroke-width="'+(w||2)+'" stroke-dasharray="'+(dash||'5,3')+'" opacity=".9"/>');
    }}
    if(sd>0){{
      specLine(s3lo,'#e67e22','4,3',1.5); specLine(s3hi,'#e67e22','4,3',1.5);
      specLine(s6lo,'#c0392b','4,3',1.5); specLine(s6hi,'#c0392b','4,3',1.5);
    }}
    specLine(med,'#27ae60','6,3',2);
    specLine(lsl,'#e74c3c','6,3',2); specLine(usl,'#e74c3c','6,3',2);
    specLine(tgt,'#8e44ad','6,3',2);

    // Stats summary line (upper-right inside plot)
    var medLbl=_fmt(med,4), sdLbl=_fmt(sd,3), cvLbl=cv!=null?cv.toFixed(1):'—';
    var statsLine='N='+N+' | Med='+medLbl+' | σ='+sdLbl+' | Spread='+cvLbl+'%';
    ps.push('<text x="'+(ML+plotW-3)+'" y="'+(MT+14)+'" text-anchor="end" font-size="10" fill="#2c3e50" opacity=".75">'+statsLine+'</text>');

    // Right-side legend
    var legX=ML+plotW+6;
    var legItems=[
      {{label:'Median',col:'#27ae60'}},
      {{label:'±3σ',  col:'#e67e22'}},
      {{label:'±6σ',  col:'#c0392b'}},
    ];
    if(tgt!=null) legItems.push({{label:'Target',col:'#8e44ad'}});
    if(lsl!=null||usl!=null) legItems.push({{label:'LSL/USL',col:'#e74c3c'}});
    legItems.forEach(function(li,k){{
      var ly=MT+12+k*18;
      ps.push('<line x1="'+legX+'" y1="'+ly+'" x2="'+(legX+18)+'" y2="'+ly+'" stroke="'+li.col+'" stroke-width="2" stroke-dasharray="4,2"/>');
      ps.push('<text x="'+(legX+22)+'" y="'+(ly+4)+'" font-size="10" fill="#333">'+li.label+'</text>');
    }});
    // Group colour swatches in legend
    if(nGrps>1){{
      var swY=MT+12+legItems.length*18+6;
      ps.push('<text x="'+legX+'" y="'+swY+'" font-size="10" font-weight="600" fill="#333">Groups</text>');
      cm.keys.forEach(function(k,gi){{
        var sy=swY+14+gi*15;
        ps.push('<rect x="'+legX+'" y="'+(sy-8)+'" width="12" height="10" fill="'+cm.map[k]+'" opacity=".80"/>');
        ps.push('<text x="'+(legX+16)+'" y="'+sy+'" font-size="9" fill="#333">'+_escH(k)+'</text>');
      }});
    }}

    ps.push('</svg>');

    // Stats table
    function fmtN(v){{return v!=null&&isFinite(v)?_fmt(v,4):'—';}}
    var lslN  = lsl!=null?lsl:null;
    var uslN  = usl!=null?usl:null;
    var pctLSL= lslN!=null?+(sorted.filter(function(v){{return v<lslN;}}).length/N*100).toFixed(2):null;
    var pctUSL= uslN!=null?+(sorted.filter(function(v){{return v>uslN;}}).length/N*100).toFixed(2):null;
    var out6c = sd>0?sorted.filter(function(v){{return v<s6lo||v>s6hi;}}).length:0;
    var tblRows=[
      ['N', N],
      ['Median', fmtN(med)+(meta.unit?' '+meta.unit:'')],
      ['σ', fmtN(sd)],
      ['Spread (%)', cv!=null?cv.toFixed(2):'—'],
      ['P1', fmtN(p1)],
      ['P99', fmtN(p99)],
      ['Target', fmtN(tgt)],
      ['LSL', fmtN(lslN)],
      ['USL', fmtN(uslN)],
      ['% < LSL', pctLSL!=null?pctLSL+'%':'—'],
      ['% > USL', pctUSL!=null?pctUSL+'%':'—'],
      ['±6σ out', out6c],
    ];
    var tblHtml='<table style="font-size:11px;border-collapse:collapse;margin:6px 0 0 0;width:100%;max-width:360px">'
      +'<thead><tr><th style="background:#34495e;color:#ecf0f1;padding:3px 8px;text-align:left">Stat</th>'
      +'<th style="background:#34495e;color:#ecf0f1;padding:3px 8px;text-align:right">Value</th></tr></thead><tbody>';
    tblRows.forEach(function(row,ri){{
      var bg=ri%2?'#f8f9fa':'white';
      tblHtml+='<tr style="background:'+bg+'"><td style="padding:2px 8px;color:#555">'+row[0]+'</td>'
        +'<td style="padding:2px 8px;text-align:right;font-weight:600;color:#2c3e50">'+row[1]+'</td></tr>';
    }});
    tblHtml+='</tbody></table>';

    // OOS badge
    var oosHtml='';
    if(oosSpec) oosHtml+='<span style="background:#e74c3c;color:white;font-size:10px;padding:1px 6px;border-radius:10px;margin-left:6px">SPEC OOS</span>';
    else if(oosSigma) oosHtml+='<span style="background:#d35400;color:white;font-size:10px;padding:1px 6px;border-radius:10px;margin-left:6px">6σ OUT</span>';

    // Card border
    var cardBorder=oosSpec?'2px solid #e74c3c':oosSigma?'2px solid #e67e22':'1px solid #dee2e6';

    // Card header
    var cardHead='<div style="background:#f0f4f8;padding:5px 10px;font-size:12px;font-weight:600;color:#2c3e50;border-bottom:1px solid #dee2e6;display:flex;align-items:center" title="'+_escH(param)+'">'
      +_escH(meta.name||param)+(meta.unit?' <span style="font-weight:400;color:#7f8c8d;margin-left:4px">('+_escH(meta.unit)+')</span>':'')
      +oosHtml+'</div>';

    cards.push('<div style="display:inline-flex;flex-direction:column;vertical-align:top;margin:6px;border:'+cardBorder+';border-radius:4px;overflow:hidden;background:white">'
      +cardHead
      +'<div style="display:flex;gap:0">'
      +'<div>'+ps.join('')+'</div>'
      +'<div style="padding:6px;border-left:1px solid #eee;min-width:160px">'+tblHtml+'</div>'
      +'</div>'
      +'</div>');
  }});
  return cards.join('');
}}

function buildDistTab(){{
  PCM_DIST_PANELS.forEach(function(panel,pn){{
    var body=document.getElementById('distp'+pn+'-body');
    if(!body)return;
    if(!_PDLY_H_P[pn])_PDLY_H_P[pn]=350;
    if(!_PDLY_GBY_P[pn])_PDLY_GBY_P[pn]=[];
    if(!body.dataset.built){{
      body.dataset.built='1';
      var ctrl=_buildDistCtrlBar(pn);
      var cardsHtml=_buildDistCards(pn);
      body.innerHTML=ctrl+'<div class="pdly-cards" style="padding:8px;display:flex;flex-wrap:wrap;gap:6px">'+cardsHtml+'</div>';
      return;
    }}
    _reBuildDistPanel(pn);
  }});
}}

/* ── XY / Vmin tabs ──────────────────────────────────────────────────────── */
function toggleXYP(n){{
  _XY_COLLAPSED[n]=!_XY_COLLAPSED[n];
  var btn=document.getElementById('xyp'+n+'-toggle');
  if(btn)btn.innerHTML=_XY_COLLAPSED[n]?'&#9654;':'&#9660;';
  var body=document.getElementById('xyp'+n+'-body');
  if(body)body.style.display=_XY_COLLAPSED[n]?'none':'flex';
}}

// Init XY state for each panel × side (a,b)
PCM_XY_PANELS.forEach(function(cfg,xi3){{
  var pdefs=(cfg.panels&&cfg.panels.length)?cfg.panels:[cfg,cfg];
  pdefs.forEach(function(pdef,si){{
    var pid='xyp'+xi3+'_'+si;
    _FP_ST[pid]={{
      x:pdef.x||cfg.x||cfg.params[0]||null, xgrp:'',
      ys:pdef.ys||(pdef.params&&pdef.params.length>1?pdef.params.slice(1):[pdef.params&&pdef.params[0]||cfg.ys&&cfg.ys[0]||cfg.params[0]||null].filter(Boolean)), ygrp:'',
      logx:false, logy:false, perdie:true, trend:'ols', gby:['material'],
      h:pdef.height||cfg.height||400, xmin:'', xmax:'', ymin:'', ymax:'',
      showCur1:false, showCur2:false, xref:93, xref:93
    }};
  }});
}});

function buildXYTab(xi4){{
  var cfg=PCM_XY_PANELS[xi4];
  if(!cfg)return;
  var pdefs=(cfg.panels&&cfg.panels.length)?cfg.panels:[cfg,cfg];
  pdefs.forEach(function(_,si){{
    fpBuild('xyp'+xi4+'_'+si);
  }});
}}

/* toggleGbyFP: toggle group-by field for XY sub-panel */
function toggleGbyFP(pid,field){{
  var arr=_FP_ST[pid].gby||(_FP_ST[pid].gby=[]);
  if(field==='none'){{arr.splice(0,arr.length);}}
  else{{var idx=arr.indexOf(field);if(idx>=0)arr.splice(idx,1);else arr.push(field);}}
  fpBuild(pid);
  _syncGbyBtnsFP(pid);
}}
function _syncGbyBtnsFP(pid){{
  var gby=_FP_ST[pid].gby||[];
  ['none','lot','wafer','prog6248','progU1U5','material'].forEach(function(f){{
    var el=document.getElementById(pid+'-gby-'+f);
    if(!el)return;
    if(f==='none')el.checked=(gby.length===0);
    else el.checked=(gby.indexOf(f)>=0);
  }});
}}

function _fpAllParams(pid){{
  var m=pid.match(/^xyp(\\d+)_(\\d+)$/);
  if(!m)return [];
  var xiN=parseInt(m[1],10);
  var si=parseInt(m[2],10);
  var cfg=PCM_XY_PANELS[xiN]; if(!cfg)return [];
  var pdefs=(cfg.panels&&cfg.panels.length)?cfg.panels:[cfg,cfg];
  var pdef=pdefs[si]||pdefs[0]||cfg;
  return pdef.params||cfg.params||[];
}}

function _fpAllX(pid){{
  var st=_FP_ST[pid]||{{}};
  var all=_fpAllParams(pid);
  if(!st.xgrp)return all;
  return all.filter(function(p){{
    return ((PCM_PARAM_META[p]||{{}}).group||'')===st.xgrp;
  }});
}}

function _fpAllY(pid){{
  var st=_FP_ST[pid]||{{}};
  var all=_fpAllParams(pid);
  if(!st.ygrp)return all;
  return all.filter(function(p){{
    return ((PCM_PARAM_META[p]||{{}}).group||'')===st.ygrp;
  }});
}}

function _fpYDropToggle(pid){{
  var d=document.getElementById(pid+'-y-drop');
  if(d)d.style.display=(d.style.display==='none'?'block':'none');
}}
function _fpYSearch(pid,val){{
  var list=document.getElementById(pid+'-y-list'); if(!list)return;
  var q=(val||'').toLowerCase();
  var shown=0;
  list.querySelectorAll('label').forEach(function(lbl){{
    var txt=(lbl.textContent||'').toLowerCase();
    lbl.style.display=(q&&txt.indexOf(q)<0)?'none':'';
    if(lbl.style.display!=='none')shown++;
  }});
  var noEl=document.getElementById(pid+'-y-none');
  if(noEl)noEl.style.display=shown?'none':'block';
}}
function _fpYSelAll(pid){{
  var list=document.getElementById(pid+'-y-list'); if(!list)return;
  var st=_FP_ST[pid];
  list.querySelectorAll('label').forEach(function(lbl){{
    if(lbl.style.display==='none')return;
    var cb=lbl.querySelector('input[type=checkbox]'); if(!cb)return;
    var p=cb.value;
    if(!st.ys)st.ys=[];
    if(st.ys.indexOf(p)<0)st.ys.push(p);
    cb.checked=true;
  }});
  _fpUpdateYBtn(pid); fpBuild(pid);
}}
function _fpYClrAll(pid){{
  var list=document.getElementById(pid+'-y-list'); if(!list)return;
  var st=_FP_ST[pid];
  list.querySelectorAll('label').forEach(function(lbl){{
    if(lbl.style.display==='none')return;
    var cb=lbl.querySelector('input[type=checkbox]'); if(!cb)return;
    var p=cb.value;
    if(st.ys){{var idx=st.ys.indexOf(p);if(idx>=0)st.ys.splice(idx,1);}}
    cb.checked=false;
  }});
  _fpUpdateYBtn(pid); fpBuild(pid);
}}
function _fpToggleY(pid,p,checked){{
  var st=_FP_ST[pid];
  if(!st.ys)st.ys=[];
  if(checked&&st.ys.indexOf(p)<0)st.ys.push(p);
  else if(!checked){{var idx=st.ys.indexOf(p);if(idx>=0)st.ys.splice(idx,1);}}
  _fpUpdateYBtn(pid); fpBuild(pid);
}}
function _fpUpdateYBtn(pid){{
  var btn=document.getElementById(pid+'-y-btn'); if(!btn)return;
  var ys=_FP_ST[pid].ys||[];
  btn.textContent=ys.length===0?'Select Y\u2026':ys.length===1?ys[0]:ys.length+' Y params \u25bc';
}}

function _fpDownloadCSV(pid){{
  var st=_FP_ST[pid]; if(!st||!st.x||!st.ys||!st.ys.length)return;
  var ak=activeKeys();
  var upmRef=_fpUpmRef(st.x);
  var upmAsPct=(upmRef!=null);
  var xHeader=upmAsPct?(st.x+' (%)'):st.x;
  var xVals={{}};
  PCM_ROWS.forEach(function(r){{
    if(r.param!==st.x||!ak.has(_rKey(r)))return;
    var key=_rKey(r);
    var vals=st.perdie?r.die_values:[r.median];
    xVals[key]=(vals||[]).filter(function(v){{return v!=null&&isFinite(v);}}).map(function(v){{return v;}});
  }});
  var rows=['lot,wafer,'+xHeader+','+st.ys.join(',')];
  PCM_ROWS.forEach(function(r){{
    if(st.ys.indexOf(r.param)<0||!ak.has(_rKey(r)))return;
    var key=_rKey(r); var xs=xVals[key]||[];
    var yvs=st.perdie?r.die_values:[r.median];
    var n=Math.min(xs.length,yvs.length);
    for(var i=0;i<n;i++){{
      var rowVals=[r.lot,r.wafer,xs[i]];
      st.ys.forEach(function(yp){{if(yp===r.param)rowVals.push(yvs[i]);else rowVals.push('');}});
      rows.push(rowVals.join(','));
    }}
  }});
  var a=document.createElement('a');
  a.href='data:text/csv;charset=utf-8,'+encodeURIComponent(rows.join('\\n'));
  a.download='xy_'+st.x+'_vs_'+st.ys.join('_')+'.csv';
  a.click();
}}

/* tooltip shared div */
var _TT_EL=null;
function _getTT(){{
  if(!_TT_EL){{
    _TT_EL=document.createElement('div');
    _TT_EL.style.cssText='position:fixed;background:rgba(20,28,40,.93);color:#ecf0f1;font-size:12px;'
      +'padding:5px 11px;border-radius:5px;pointer-events:none;z-index:9999;display:none;'
      +'white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,.4);border:1px solid #4a6278';
    document.body.appendChild(_TT_EL);
  }}
  return _TT_EL;
}}

/* Crosshair drag cursors — two draggable cursors (A=red, B=blue) with ΔX/ΔY panel.
   Cursor positions persist across redraws via _DRAG_CUR_A / _DRAG_CUR_B[pid].
   NOTE: Cursors are now hidden (display:none) but code retained for future re-enabling.     */
function _initDragCursorsXY(svgEl,pid,ML,MT,plotW,plotH,xLo,xHi,yLo,yHi,fmtX,fmtY){{
  var NS='http://www.w3.org/2000/svg';
  var _c1V=!!(_FP_ST[pid]&&_FP_ST[pid].showCur1);
  var _c2V=!!(_FP_ST[pid]&&_FP_ST[pid].showCur2);
  var xRange=xHi-xLo||1,yRange=yHi-yLo||1;
  /* Restore or initialise cursor positions */
  var curX=(_DRAG_CUR_A.x!=null&&_DRAG_CUR_A.x>=xLo&&_DRAG_CUR_A.x<=xHi)?_DRAG_CUR_A.x:xLo+xRange*0.30;
  var curY=(_DRAG_CUR_A.y!=null&&_DRAG_CUR_A.y>=yLo&&_DRAG_CUR_A.y<=yHi)?_DRAG_CUR_A.y:yLo+yRange*0.50;
  if(!_DRAG_CUR_B[pid])_DRAG_CUR_B[pid]={{x:null,y:null}};
  var _b2=_DRAG_CUR_B[pid];
  var curX2=(_b2.x!=null&&_b2.x>=xLo&&_b2.x<=xHi)?_b2.x:Math.min(xHi,curX+xRange*0.20);
  var curY2=(_b2.y!=null&&_b2.y>=yLo&&_b2.y<=yHi)?_b2.y:curY;
  function v2px(v){{return ML+(v-xLo)/xRange*plotW;}}
  function v2py(v){{return MT+plotH-(v-yLo)/yRange*plotH;}}
  function px2v(px){{return xLo+(px-ML)/plotW*xRange;}}
  function py2v(py){{return yLo+(MT+plotH-py)/plotH*yRange;}}
  function clamp(v,lo,hi){{return v<lo?lo:v>hi?hi:v;}}
  function getSvgPt(e){{
    var pt=svgEl.createSVGPoint();pt.x=e.clientX;pt.y=e.clientY;
    var ctm=svgEl.getScreenCTM();if(!ctm)return null;
    var sp=pt.matrixTransform(ctm.inverse());return{{x:sp.x,y:sp.y}};
  }}
  function _mk(tag,attrs){{var el=document.createElementNS(NS,tag);Object.keys(attrs).forEach(function(k){{el.setAttribute(k,attrs[k]);}});el.style.pointerEvents='none';return el;}}
  function _mkTxt(sz,col){{return _mk('text',{{'font-size':sz,'fill':col,'font-weight':'bold','stroke':'white','stroke-width':'1.5','paint-order':'stroke'}});}}
  /* Cursor A (bright red-orange) */
  var vLA=_mk('line',{{x1:v2px(curX),x2:v2px(curX),y1:MT,y2:MT+plotH,stroke:'#ff3300','stroke-width':'2.5','stroke-dasharray':'5,2',opacity:'1'}});
  var hLA=_mk('line',{{x1:ML,x2:ML+plotW,y1:v2py(curY),y2:v2py(curY),stroke:'#ff3300','stroke-width':'2.5','stroke-dasharray':'5,2',opacity:'1'}});
  var txAx=_mkTxt('14','#ff3300');
  var txAy=_mkTxt('14','#ff3300');
  /* Cursor B (bright blue) */
  var vLB=_mk('line',{{x1:v2px(curX2),x2:v2px(curX2),y1:MT,y2:MT+plotH,stroke:'#0099ff','stroke-width':'2.5','stroke-dasharray':'5,2',opacity:'1'}});
  var hLB=_mk('line',{{x1:ML,x2:ML+plotW,y1:v2py(curY2),y2:v2py(curY2),stroke:'#0099ff','stroke-width':'2.5','stroke-dasharray':'5,2',opacity:'1'}});
  var txBtag=_mkTxt('11','#0077cc');
  var txBx=_mkTxt('13','#0077cc');
  var txBy=_mkTxt('13','#0077cc');
  /* Delta panel background + labels */
  var dBg=_mk('rect',{{rx:'4',ry:'4',fill:'rgba(255,255,255,0.92)',stroke:'#0099ff','stroke-width':'1.5'}});dBg.style.pointerEvents='none';
  var dTxtX=_mkTxt('12','#cc2200');
  var dTxtY=_mkTxt('12','#0077cc');
  function _fX(v){{return fmtX?fmtX(v):_fmt(v);}}
  function _fY(v){{return fmtY?fmtY(v):_fmt(v);}}
  function _updA(){{
    var px=v2px(curX),py=v2py(curY);
    vLA.setAttribute('x1',px.toFixed(1));vLA.setAttribute('x2',px.toFixed(1));
    hLA.setAttribute('y1',py.toFixed(1));hLA.setAttribute('y2',py.toFixed(1));
    var anc=px+plotW*0.55>ML+plotW?'end':'start';
    txAx.setAttribute('x',(anc==='end'?px-4:px+4).toFixed(1));txAx.setAttribute('y',(MT+14).toFixed(1));
    txAx.setAttribute('text-anchor',anc);txAx.textContent=_fX(curX);
    var ly=py-3<MT+14?py+15:py-4;
    txAy.setAttribute('x',(ML+5).toFixed(1));txAy.setAttribute('y',ly.toFixed(1));
    txAy.setAttribute('text-anchor','start');txAy.textContent=_fY(curY);
  }}
  function _updB(){{
    var px=v2px(curX2),py=v2py(curY2);
    vLB.setAttribute('x1',px.toFixed(1));vLB.setAttribute('x2',px.toFixed(1));
    hLB.setAttribute('y1',py.toFixed(1));hLB.setAttribute('y2',py.toFixed(1));
    var la=px+4>ML+plotW-50?'end':'start';
    txBtag.setAttribute('x',(la==='end'?px-4:px+4).toFixed(1));txBtag.setAttribute('y',(MT+12).toFixed(1));
    txBtag.setAttribute('text-anchor',la);txBtag.textContent='Cursor';
    var xba=px+4>ML+plotW-60?'end':'start';
    txBx.setAttribute('x',(xba==='end'?px-4:px+4).toFixed(1));txBx.setAttribute('y',(MT+28).toFixed(1));
    txBx.setAttribute('text-anchor',xba);txBx.textContent='X:'+_fX(curX2);
    var yly=py-3<MT+28?py+27:py-4;
    txBy.setAttribute('x',(ML+5).toFixed(1));txBy.setAttribute('y',yly.toFixed(1));
    txBy.setAttribute('text-anchor','start');txBy.textContent='Y:'+_fY(curY2);
  }}
  function _updDelta(){{
    var dx=Math.abs(curX2-curX),dy=Math.abs(curY2-curY);
    dTxtX.textContent='\u0394X: '+_fX(dx);dTxtY.textContent='\u0394Y: '+_fY(dy);
    var bW=118,bH=38,bX=ML+plotW-bW-4,bY=MT+4;
    dBg.setAttribute('x',bX);dBg.setAttribute('y',bY);dBg.setAttribute('width',bW);dBg.setAttribute('height',bH);
    dTxtX.setAttribute('x',(bX+bW/2).toFixed(1));dTxtX.setAttribute('y',(bY+14).toFixed(1));dTxtX.setAttribute('text-anchor','middle');
    dTxtY.setAttribute('x',(bX+bW/2).toFixed(1));dTxtY.setAttribute('y',(bY+30).toFixed(1));dTxtY.setAttribute('text-anchor','middle');
  }}
  _updA();_updB();_updDelta();
  _DRAG_CUR_A.x=curX;_DRAG_CUR_A.y=curY;_b2.x=curX2;_b2.y=curY2;
  if(_c1V){{[vLA,hLA,txAx,txAy].forEach(function(el){{svgEl.appendChild(el);}});}}
  if(_c2V){{[vLB,hLB,txBtag,txBx,txBy].forEach(function(el){{svgEl.appendChild(el);}});}}
  if(_c1V&&_c2V){{[dBg,dTxtX,dTxtY].forEach(function(el){{svgEl.appendChild(el);}});}}
  /* Transparent drag handle — picks nearest cursor on mousedown, then drags */
  var uH=_mk('rect',{{x:ML,y:MT,width:plotW,height:plotH,fill:'transparent'}});
  uH.style.pointerEvents='all';uH.style.cursor='crosshair';
  svgEl.appendChild(uH);
  var _uDrag=false,_uTgt='A';
  function _uMove(sp){{
    var px=clamp(sp.x,ML,ML+plotW),py=clamp(sp.y,MT,MT+plotH);
    if(_uTgt==='A'){{curX=px2v(px);curY=py2v(py);_DRAG_CUR_A.x=curX;_DRAG_CUR_A.y=curY;_updA();}}
    else{{curX2=px2v(px);curY2=py2v(py);_b2.x=curX2;_b2.y=curY2;_updB();}}
    _updDelta();
  }}
  function _onUM(e){{if(_uDrag){{var sp=getSvgPt(e);if(sp)_uMove(sp);}}}}
  function _onUU(){{_uDrag=false;document.removeEventListener('mousemove',_onUM);document.removeEventListener('mouseup',_onUU);}}
  uH.addEventListener('mousedown',function(e){{
    e.preventDefault();e.stopPropagation();
    var sp=getSvgPt(e);if(!sp)return;
    var px=clamp(sp.x,ML,ML+plotW),py=clamp(sp.y,MT,MT+plotH);
    var dA=Math.sqrt(Math.pow(px-v2px(curX),2)+Math.pow(py-v2py(curY),2));
    var dB=Math.sqrt(Math.pow(px-v2px(curX2),2)+Math.pow(py-v2py(curY2),2));
    _uTgt=(dA<=dB)?'A':'B';_uDrag=true;_uMove(sp);
    document.addEventListener('mousemove',_onUM);document.addEventListener('mouseup',_onUU);
  }});
  function _onUTM(e){{if(!_uDrag||!e.touches.length)return;e.preventDefault();var t=e.touches[0];var sp=getSvgPt({{clientX:t.clientX,clientY:t.clientY}});if(sp)_uMove(sp);}}
  function _onUTE(){{_uDrag=false;svgEl.removeEventListener('touchmove',_onUTM);svgEl.removeEventListener('touchend',_onUTE);}}
  uH.addEventListener('touchstart',function(e){{
    e.preventDefault();
    if(e.touches.length){{var t=e.touches[0];var sp=getSvgPt({{clientX:t.clientX,clientY:t.clientY}});if(sp){{
      var px=clamp(sp.x,ML,ML+plotW),py=clamp(sp.y,MT,MT+plotH);
      var dA=Math.sqrt(Math.pow(px-v2px(curX),2)+Math.pow(py-v2py(curY),2));
      var dB=Math.sqrt(Math.pow(px-v2px(curX2),2)+Math.pow(py-v2py(curY2),2));
      _uTgt=(dA<=dB)?'A':'B';_uDrag=true;_uMove(sp);}}}}
    svgEl.addEventListener('touchmove',_onUTM,{{passive:false}});svgEl.addEventListener('touchend',_onUTE);
  }},{{passive:false}});
}}

/* Main XY sub-panel builder */
function fpBuild(pid){{
  console.log('[DBG] fpBuild start',pid);
  var wrap=document.getElementById(pid+'-wrap');
  if(!wrap){{console.log('[DBG] no wrap',pid);return;}}
  var m=pid.match(/^xyp(\\d+)_(\\d+)$/);
  if(!m)return;
  var xiN=parseInt(m[1],10);
  var si=parseInt(m[2],10);
  var cfg=PCM_XY_PANELS[xiN]; if(!cfg){{console.log('[DBG] no cfg',xiN);return;}}
  var pdefs=(cfg.panels&&cfg.panels.length)?cfg.panels:[cfg,cfg];
  var pdef=pdefs[si]||pdefs[0]||cfg;
  var st=_FP_ST[pid];
  var allParams=pdef.params||cfg.params||[];
  var xAll=_fpAllX(pid);
  var yAll=_fpAllY(pid);
  var gby=st.gby||[];

  if(st.x&&xAll.indexOf(st.x)<0)st.x=xAll[0]||null;
  if(st.ys)st.ys=st.ys.filter(function(p){{return yAll.indexOf(p)>=0;}});

  // Row 1 – X/Y selectors
  var xGrpOpts=['<option value="">All</option>'].concat(PCM_GROUPS.map(function(g){{
    return '<option value="'+_escH(g)+'"'+(st.xgrp===g?' selected':'')+'>'+_escH(g)+'</option>';
  }})).join('');
  var yGrpOpts=['<option value="">All</option>'].concat(PCM_GROUPS.map(function(g){{
    return '<option value="'+_escH(g)+'"'+(st.ygrp===g?' selected':'')+'>'+_escH(g)+'</option>';
  }})).join('');

  var xOpts=xAll.map(function(p){{
    return '<option value="'+_escH(p)+'"'+(st.x===p?' selected':'')+'>'+_escH(_pDisp(p))+'</option>';
  }}).join('');
  var yBtnLabel=(!st.ys||!st.ys.length)?'Select Y\u2026':st.ys.length===1?_pDisp(st.ys[0]):st.ys.length+' Y params';
  var yListHtml=yAll.map(function(p){{
    var chk=(st.ys&&st.ys.indexOf(p)>=0)?'checked':'';
    return '<label style="display:flex;align-items:center;gap:5px;padding:2px 6px;cursor:pointer;font-size:12px;white-space:nowrap"'
      +' onmouseover="this.style.background=\\'#e8f0fe\\'" onmouseout="this.style.background=\\'\\'">'
      +'<input type="checkbox" value="'+_escH(p)+'" '+chk+' onchange="_fpToggleY(\\''+pid+'\\',\\''+_escH(p)+'\\',this.checked)">'
      +'<span style="font-size:12px">'+_escH(_pDisp(p))+'</span>'
      +'</label>';
  }}).join('');
  var xMetaU=(PCM_PARAM_META[st.x]||{{}}).unit||'';
  var xLabel=st.x?('&#10799; '+_escH(_pDisp(st.x))+(xMetaU?' ('+_escH(xMetaU)+')':'')):'Select X parameter\u2026';

  var row1='<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">'
    +'<span style="font-size:13px;font-weight:bold;color:#2c3e50">'+xLabel+'</span>'
    +'<span style="font-size:12px;display:flex;align-items:center;gap:2px">X grp:</span>'
    +'<select style="font-size:12px;padding:1px 3px;border-radius:3px;border:1px solid #ccc"'
    +' onchange="_FP_ST[\\''+pid+'\\'].xgrp=this.value;_FP_ST[\\''+pid+'\\'].x=_fpAllX(\\''+pid+'\\')[0]||null;fpBuild(\\''+pid+'\\')">'+xGrpOpts+'</select>'
    +'<span style="font-size:12px;color:#555">X:</span>'
    +'<select style="font-size:12px;padding:1px 3px;border-radius:3px;border:1px solid #ccc;max-width:180px"'
    +'>'+(st.x?'' : '<option value="">-- select X --</option>')+xOpts+'</select>'
    +'<span style="width:1px;background:#ccc;align-self:stretch;margin:0 1px"></span>'
    +'<span style="font-size:12px;display:flex;align-items:center;gap:2px">Y grp:</span>'
    +'<select style="font-size:12px;padding:1px 3px;border-radius:3px;border:1px solid #ccc"'
    +' onchange="_FP_ST[\\''+pid+'\\'].ygrp=this.value;_FP_ST[\\''+pid+'\\'].ys=null;fpBuild(\\''+pid+'\\')">'+yGrpOpts+'</select>'
    +'<span style="font-size:12px;color:#555">Y:</span>'
    +'<span style="position:relative;display:inline-block">'
      +'<button id="'+pid+'-y-btn" onclick="_fpYDropToggle(\\''+pid+'\\')"'
      +' style="font-size:12px;padding:1px 6px;border-radius:3px;border:1px solid #ccc;background:#fff;cursor:pointer;min-width:90px;max-width:200px;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+_escH(yBtnLabel)+'</button>'
      +'<div id="'+pid+'-y-drop" style="display:none;position:absolute;top:100%;left:0;z-index:9999;background:#fff;border:1px solid #ccc;border-radius:4px;box-shadow:0 4px 12px rgba(0,0,0,.15);min-width:260px;max-width:400px">'
        +'<div style="display:flex;align-items:center;gap:4px;padding:4px 5px;border-bottom:1px solid #e8e8e8;background:#f5f5f5">'
          +'<input id="'+pid+'-y-srch" placeholder="Search\u2026" oninput="_fpYSearch(\\''+pid+'\\',this.value)"'
          +' style="flex:1;font-size:12px;padding:2px 5px;border:1px solid #ccc;border-radius:3px">'
          +'<button onclick="_fpYSelAll(\\''+pid+'\\')" style="font-size:11px;padding:1px 5px;border-radius:3px;border:1px solid #bbb;background:#e8f0fe;cursor:pointer">All</button>'
          +'<button onclick="_fpYClrAll(\\''+pid+'\\')" style="font-size:11px;padding:1px 5px;border-radius:3px;border:1px solid #bbb;background:#fef0e8;cursor:pointer">Clr</button>'
        +'</div>'
        +'<div id="'+pid+'-y-list" style="max-height:240px;overflow-y:auto;padding:3px 0">'+yListHtml
          +'<div id="'+pid+'-y-none" style="display:none;padding:6px;color:#aaa;font-size:12px">No matches</div>'
        +'</div>'
      +'</div>'
    +'</span>'
    +'</div>';

  // Row 2 – logX/logY/perdie + trend + gby
  var trendOpts=[['none','None'],['ols','OLS'],['theilsen','T-S']].map(function(tv){{
    return '<label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:2px">'
      +'<input type="radio" name="'+pid+'-trend" value="'+tv[0]+'"'+(st.trend===tv[0]?' checked':'')
      +' onchange="_FP_ST[\\''+pid+'\\'].trend=this.value;fpBuild(\\''+pid+'\\')">'+tv[1]+'</label>';
  }}).join('');
  var gbyDefs=[['none','None'],['lot','Lot'],['wafer','Wfr'],['prog6248','P6248'],['progU1U5','PU1U5'],['material','Material']];
  var gbyCbs=gbyDefs.map(function(gd){{
    var chk=(gd[0]==='none')?(gby.length===0):(gby.indexOf(gd[0])>=0);
    return '<label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:2px">'
      +'<input type="checkbox" id="'+pid+'-gby-'+gd[0]+'"'+(chk?' checked':'')
      +' onchange="toggleGbyFP(\\''+pid+'\\',\\''+gd[0]+'\\')">'+gd[1]+'</label>';
  }}).join('');
  var row2='<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">'
    +'<label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:2px">'
      +'<input type="checkbox"'+(st.logx?' checked':'')+' onchange="_FP_ST[\\''+pid+'\\'].logx=this.checked;fpBuild(\\''+pid+'\\')">logX</label>'
    +'<label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:2px">'
      +'<input type="checkbox"'+(st.logy?' checked':'')+' onchange="_FP_ST[\\''+pid+'\\'].logy=this.checked;fpBuild(\\''+pid+'\\')">logY</label>'
    +'<label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:2px">'
      +'<input type="checkbox"'+(st.perdie?' checked':'')+' onchange="_FP_ST[\\''+pid+'\\'].perdie=this.checked;fpBuild(\\''+pid+'\\')">Per die</label>'
    +'<span style="width:1px;background:#ccc;align-self:stretch;margin:0 2px"></span>'
    +'<span style="font-size:12px;color:#555">Trend:</span>'+trendOpts
    +'<span style="width:1px;background:#ccc;align-self:stretch;margin:0 2px"></span>'
    +'<b style="font-size:12px;color:#555">Gby:</b>'+gbyCbs
    +'<span style="width:1px;background:#ccc;align-self:stretch;margin:0 2px"></span>'
    +'<label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:2px">'
      +'<input type="checkbox"'+(st.showCur1?' checked':'')+' onchange="_FP_ST[\\''+pid+'\\'].showCur1=this.checked;fpBuild(\\''+pid+'\\')">Cur1</label>'
    +'<label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:2px">'
      +'<input type="checkbox"'+(st.showCur2?' checked':'')+' onchange="_FP_ST[\\''+pid+'\\'].showCur2=this.checked;fpBuild(\\''+pid+'\\')">Cur2</label>'
    +'</div>';

  // Row 3 – ranges, H slider, CSV
  var h=st.h||400;
  var row3='<div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap">'
    +'<span style="font-size:12px;color:#555">X:</span>'
    +'<input type="number" placeholder="auto" title="X min" value="'+(st.xmin||'')+'"'
    +' oninput="_FP_ST[\\''+pid+'\\'].xmin=this.value;fpBuild(\\''+pid+'\\')" style="width:60px;font-size:12px;padding:1px 3px">'
    +'<span style="font-size:11px;color:#aaa">&ndash;</span>'
    +'<input type="number" placeholder="auto" title="X max" value="'+(st.xmax||'')+'"'
    +' oninput="_FP_ST[\\''+pid+'\\'].xmax=this.value;fpBuild(\\''+pid+'\\')" style="width:60px;font-size:12px;padding:1px 3px">'
    +'<span style="width:1px;background:#ccc;align-self:stretch;margin:0 2px"></span>'
    +'<span style="font-size:12px;color:#555">Y:</span>'
    +'<input type="number" placeholder="auto" title="Y min" value="'+(st.ymin||'')+'"'
    +' oninput="_FP_ST[\\''+pid+'\\'].ymin=this.value;fpBuild(\\''+pid+'\\')" style="width:60px;font-size:12px;padding:1px 3px">'
    +'<span style="font-size:11px;color:#aaa">&ndash;</span>'
    +'<input type="number" placeholder="auto" title="Y max" value="'+(st.ymax||'')+'"'
    +' oninput="_FP_ST[\\''+pid+'\\'].ymax=this.value;fpBuild(\\''+pid+'\\')" style="width:60px;font-size:12px;padding:1px 3px">'
    +'<span style="width:1px;background:#ccc;align-self:stretch;margin:0 3px"></span>'
    +'<span style="font-size:12px;display:flex;align-items:center;gap:3px">H'
      +'<input type="range" min="200" max="1000" step="25" value="'+h+'"'
      +' oninput="_FP_ST[\\''+pid+'\\'].h=+this.value;document.getElementById(\\''+pid+'-h-val\\').textContent=this.value+\\'px\\';fpBuild(\\''+pid+'\\')"'
      +' style="width:70px;accent-color:#3498db">'
      +'<span id="'+pid+'-h-val" style="min-width:30px;font-size:10px;color:#555">'+h+'px</span>'
    +'</span>'
    +'<span style="width:1px;background:#ccc;align-self:stretch;margin:0 2px"></span>'
    +'<button onclick="_fpDownloadCSV(\\''+pid+'\\')" title="Download CSV"'
    +' style="padding:2px 8px;font-size:11px;font-weight:bold;border:none;border-radius:3px;background:#27ae60;color:#fff;cursor:pointer"'
    +' onmouseover="this.style.background=\\'#1e8449\\'" onmouseout="this.style.background=\\'#27ae60\\'">&#11015; CSV</button>'
    +'</div>';

  wrap.innerHTML=
    (pdef.label?'<div style="padding:4px 10px;background:#eaf4ff;border-bottom:1px solid #c8dff5;font-size:12px;font-weight:bold;color:#1f3a50">'+_escH(pdef.label)+'</div>':'')+
    '<div style="display:flex;flex-direction:column;flex-shrink:0;background:#f8f9fa;border-bottom:1px solid #dde;padding:5px 10px;gap:4px">'
    +row1+row2+row3
    +'</div>'
    +'<div id="'+pid+'-cont" style="flex:1;overflow-y:auto;padding:0 8px 8px"></div>';

  console.log('[DBG] fpBuild calling _fpRenderChart',pid,'gby=',JSON.stringify(st.gby));
  _fpRenderChart(pid);
  console.log('[DBG] fpBuild done',pid);
}}

function _fpCalcGroupStats(pid,ptsByY,st,gby,upmRef){{
  /* One row per (group × Y-param). When gby is empty, one row per Y-param. */
  var multiY=st.ys.length>1;
  var result=[];
  var colorIdx=0;

  // Build keys: if gby active, group by gby; else single bucket ''
  st.ys.forEach(function(yp){{
    var buckets={{}};
    (ptsByY[yp]||[]).forEach(function(pt){{
      var k='';
      if(gby&&gby.length){{
        var fakeRow={{lot:pt.lot,wafer:pt.wafer}};
        k=_grpKeyWith(fakeRow,gby);
      }}
      if(!buckets[k])buckets[k]=[];
      buckets[k].push({{x:pt.x,y:pt.y}});
    }});
    var allKeys=Object.keys(buckets).sort();
    allKeys.forEach(function(k){{
      var pts=buckets[k];
      if(!pts.length)return;
      var n=pts.length;
      var sx=0,sy=0,sxx=0,syy=0,sxy=0;
      pts.forEach(function(p){{sx+=p.x;sy+=p.y;sxx+=p.x*p.x;syy+=p.y*p.y;sxy+=p.x*p.y;}});
      var num=n*sxy-sx*sy;
      var denom=Math.sqrt((n*sxx-sx*sx)*(n*syy-sy*sy));
      var r=denom>0?(num/denom):0;
      var r2=r*r;
      var ys=pts.map(function(p){{return p.y;}}).sort(function(a,b){{return a-b;}});
      var medY=ys.length%2?ys[Math.floor(ys.length/2)]:(ys[ys.length/2-1]+ys[ys.length/2])/2;
      var xs=pts.map(function(p){{return p.x;}}).sort(function(a,b){{return a-b;}});
      var medX=xs.length%2?xs[Math.floor(xs.length/2)]:(xs[xs.length/2-1]+xs[xs.length/2])/2;
      var fitPts=pts.slice(0,500).map(function(p){{return [p.x,p.y];}});
      var fit=_olsFit(fitPts);
      var _gColor=_PALETTE&&_PALETTE.length?_PALETTE[colorIdx%_PALETTE.length]:'#2980b9';
      colorIdx++;
      // Label: "group" or "group | Yparam" when multiY
      var label=k||(gby&&gby.length?'(all)':'');
      if(multiY)label=(label?label+' | ':'')+_pDisp(yp);
      /* Per-field parts for separate columns */
      var _gbyParts=[];
      if(gby&&gby.length){{var _fakeRow0=(ptsByY[yp]||[]).find(function(p){{return _grpKeyWith({{lot:p.lot,wafer:p.wafer}},gby)===k;}});
        if(_fakeRow0){{var _fw=_WFR_LOOKUP[_fakeRow0.lot+'/'+_fakeRow0.wafer]||{{}};gby.forEach(function(f){{if(f==='lot')_gbyParts.push(_fakeRow0.lot||'');else if(f==='wafer')_gbyParts.push(String(_fakeRow0.wafer||''));else if(f==='prog6248')_gbyParts.push(_fw.prog6248||'');else if(f==='progU1U5')_gbyParts.push(_fw.progU1U5||'');else if(f==='material')_gbyParts.push(_fw.material||(_fw.sort_lot?_lotMat(_fw.sort_lot):'Others'));else _gbyParts.push(k);}});}}
        else _gbyParts=[k];
      }}
      result.push({{group:label,gbyParts:_gbyParts,yp:yp,n:n,r2:r2,medX:medX,medY:medY,fitM:fit?fit.m:null,fitB:fit?fit.b:null,color:_gColor}});
    }});
  }});
  return result;
}}

/* Rebuild just the stats table body when ref-X input changes (no full chart re-render) */
function _fpRefreshStatsTable(pid){{
  var gs=_FP_GSTATS[pid];
  if(!gs||!gs.length)return;
  var st=_FP_ST[pid];
  var xref=parseFloat(st.xref);
  var tbody=document.getElementById(pid+'-stats-tbody');
  if(!tbody)return;
  var st=_FP_ST[pid];
  var gby=(st&&st.gby)||[];
  var _fpFLabels={{'lot':'Lot','wafer':'Wfr','prog6248':'P6248','progU1U5':'PU1U5','material':'Material'}};
  var multiGby=gs.length&&gs[0].gbyParts&&gs[0].gbyParts.length>1;
  /* Sync header group col(s) if multiple gby */
  var thead=tbody.parentNode?tbody.parentNode.querySelector('thead tr'):null;
  if(thead){{var groupTh=thead.querySelector('th');if(groupTh){{if(multiGby&&gby.length>1){{groupTh.style.display='none';var _xth=thead.querySelector('th._gby-extra');while(_xth){{_xth.remove();_xth=thead.querySelector('th._gby-extra');}}gby.forEach(function(f,fi){{var _nt=document.createElement('th');_nt.className='_gby-extra';_nt.style.cssText='padding:3px 6px;text-align:left;border:1px solid #666';_nt.textContent=_fpFLabels[f]||f;thead.insertBefore(_nt,groupTh.nextSibling?groupTh.nextSibling:null);if(fi===0)thead.insertBefore(_nt,groupTh);else thead.appendChild(_nt);}});}}else{{groupTh.style.display='';thead.querySelectorAll('th._gby-extra').forEach(function(e){{e.remove();}});}}}}}}
  var rows='';
  gs.forEach(function(g,i){{
    var bgColor=i%2?'#fff':'#f0f2f5';
    var vminAtRef=(isFinite(xref)&&g.fitM!=null)?(+(g.fitM*xref+g.fitB).toFixed(4)):null;
    var eqStr=g.fitM!=null?('y='+g.fitM.toFixed(4)+'x'+(g.fitB>=0?'+':'')+g.fitB.toFixed(4)):'-';
    var _gCol=g.color||'#2980b9';
    rows+='<tr style="background:'+bgColor+'">';
    if(multiGby&&g.gbyParts&&g.gbyParts.length>1){{
      rows+='<td style="display:none"></td>';
      g.gbyParts.forEach(function(v,fi){{
        var prefix=fi===0?'<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:'+_gCol+';margin-right:5px;vertical-align:middle"></span>':'';
        rows+='<td style="padding:3px 6px;border:1px solid #ddd">'+prefix+_escH(v)+'</td>';
      }});
    }}else{{
      rows+='<td style="padding:3px 6px;border:1px solid #ddd"><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:'+_gCol+';margin-right:5px;vertical-align:middle"></span>'+_escH(g.group)+'</td>';
    }}
    rows+='<td style="padding:3px 6px;border:1px solid #ddd;text-align:center">'+g.n+'</td>';
    rows+='<td style="padding:3px 6px;border:1px solid #ddd;text-align:center">'+_fmt(g.r2,3)+'</td>';
    rows+='<td style="padding:3px 6px;border:1px solid #ddd;text-align:center">'+_fmtUpm(g.medX)+'</td>';
    rows+='<td style="padding:3px 6px;border:1px solid #ddd;text-align:center">'+_fmtVmin(g.medY)+'</td>';
    rows+='<td style="padding:3px 6px;border:1px solid #ddd;text-align:center">'+(vminAtRef!=null?_fmtVmin(vminAtRef):'-')+'</td>';
    rows+='<td style="padding:3px 6px;border:1px solid #ddd;text-align:center;font-size:11px;color:#555">'+eqStr+'</td>';
    rows+='</tr>';
  }});
  tbody.innerHTML=rows;
}}

function _fpXrefSlider(el){{
  var pid=el.dataset.pid;
  var v=+el.value;
  _FP_ST[pid].xref=v;
  var txt=document.getElementById(pid+'-xref-txt');
  if(txt)txt.value=v.toFixed(1);
  var hdr=document.getElementById(pid+'-vmin-hdr');
  if(hdr)hdr.textContent='Y@X='+v.toFixed(1)+'%';
  _fpRefreshStatsTable(pid);
}}
function _fpXrefText(el){{
  var pid=el.dataset.pid;
  var v=parseFloat(el.value);
  if(!isFinite(v)||v<80||v>105)return;
  _FP_ST[pid].xref=v;
  var sl=document.getElementById(pid+'-xref');
  if(sl)sl.value=v;
  var hdr=document.getElementById(pid+'-vmin-hdr');
  if(hdr)hdr.textContent='Y@X='+v.toFixed(1)+'%';
  _fpRefreshStatsTable(pid);
}}

function _fpRenderChart(pid){{
  console.log('[DBG] _fpRenderChart start',pid);
  var cont=document.getElementById(pid+'-cont');
  if(!cont){{console.log('[DBG] no cont',pid);return;}}
  var st=_FP_ST[pid];
  if(!st||!st.x||!st.ys||!st.ys.length){{
    cont.innerHTML='<p style="padding:12px;color:#999;font-size:12px">Select X and Y parameters.</p>';
    return;
  }}
  console.log('[DBG] rendering x=',st.x,'ys=',st.ys,'gby=',st.gby);
  try{{
  var upmRef=_fpUpmRef(st.x);
  var upmAsPct=(upmRef!=null);
  var ak=activeKeys();
  var svgW=820, ML=90, MR=30, MT=40;
  var MB=40;
  var svgH=st.h||400;
  var plotW=svgW-ML-MR, plotH=svgH-MT-MB;
  var gby=st.gby||[];

  // Gather X values by (lot/wafer) key
  var xByKey={{}};
  PCM_ROWS.forEach(function(r){{
    if(r.param!==st.x||!ak.has(_rKey(r)))return;
    var key=_rKey(r);
    if(!xByKey[key])xByKey[key]=[];
    var vals=st.perdie?r.die_values:[r.median];
    (vals||[]).forEach(function(v,i){{if(v!=null&&isFinite(v))xByKey[key].push({{v:v,i:i,lot:r.lot,wafer:r.wafer}});}});
  }});

  // Build points per Y param
  var ptsByY={{}};
  st.ys.forEach(function(yp){{ptsByY[yp]=[];}});
  var allX=[],allY=[];

  st.ys.forEach(function(yp){{
    PCM_ROWS.forEach(function(r){{
      if(r.param!==yp||!ak.has(_rKey(r)))return;
      var key=_rKey(r);
      var xs=xByKey[key]||[];
      var yvs=st.perdie?(r.die_values||[]):[r.median];
      var n2=Math.min(xs.length,yvs.length);
      for(var ki=0;ki<n2;ki++){{
        var xv=xs[ki].v, yv=yvs[ki];
        if(xv==null||yv==null||!isFinite(xv)||!isFinite(yv))continue;
        ptsByY[yp].push({{x:xv,y:yv,lot:r.lot,wafer:r.wafer}});
        allX.push(xv); allY.push(yv);
      }}
    }});
  }});

  if(!allX.length){{
    cont.innerHTML='<p style="padding:12px;color:#999;font-size:12px">No data for selected parameters.</p>';
    return;
  }}

  // Determine ranges
  function p1p99(arr){{
    var s=arr.slice().sort(function(a,b){{return a-b;}});
    return [s[Math.floor(s.length*.01)],s[Math.min(s.length-1,Math.ceil(s.length*.99))]];
  }}
  var xr=p1p99(allX),yr=p1p99(allY);
  var xlo=(st.xmin!==''&&st.xmin!=null)?+st.xmin:xr[0];
  var xhi=(st.xmax!==''&&st.xmax!=null)?+st.xmax:xr[1];
  var ylo=(st.ymin!==''&&st.ymin!=null)?+st.ymin:yr[0];
  var yhi=(st.ymax!==''&&st.ymax!=null)?+st.ymax:yr[1];
  if(xlo===xhi){{xlo-=0.5;xhi+=0.5;}}
  if(ylo===yhi){{ylo-=0.5;yhi+=0.5;}}
  var xrng=xhi-xlo, yrng=yhi-ylo;
  var xpad=xrng*.08, ypad=yrng*.08;
  xlo-=xpad; xhi+=xpad; ylo-=ypad; yhi+=ypad;
  xrng=xhi-xlo; yrng=yhi-ylo;

  function px(xv){{return +(ML+(xv-xlo)/xrng*plotW).toFixed(1);}}
  function py(yv){{return +(MT+(1-(yv-ylo)/yrng)*plotH).toFixed(1);}}
  function fmtX(v){{return upmAsPct?_fmtUpmAxis(v,true):_fmt(v,3);}}
  function fmtY(v){{return _fmt(v,3);}}

  // Build colour map: if gby, colour by group; if multi-Y, colour by Y param; else single colour
  var colByPt=function(pt,yp,ypi){{
    if(gby.length>0){{
      var fakeRow={{lot:pt.lot,wafer:pt.wafer}};
      var k=_grpKeyWith(fakeRow,gby);
      return _PALETTE[_grpKey2idx(k,gby,allX)%_PALETTE.length];
    }}
    return _PALETTE[ypi%_PALETTE.length];
  }};
  // Build group colour map for gby legend
  var gbyCm=null;
  if(gby.length>0){{
    var allRows=[];
    PCM_ROWS.forEach(function(r){{if(ak.has(_rKey(r)))allRows.push(r);}});
    gbyCm=_cMapWith(allRows,gby);
  }}

  var psvg=['<svg id="'+pid+'-svg" width="100%" height="'+svgH+'" viewBox="0 0 '+svgW+' '+svgH+'" style="display:block;cursor:crosshair;font-family:sans-serif">'];
  psvg.push('<rect width="'+svgW+'" height="'+svgH+'" fill="#f8f9fa"/>');
  psvg.push('<rect x="'+ML+'" y="'+MT+'" width="'+plotW+'" height="'+plotH+'" fill="white" stroke="#ccc" stroke-width="1"/>');

  // X grid + ticks
  for(var xi6=0;xi6<=6;xi6++){{
    var xv5=xlo+xrng*xi6/6;
    var xp5=+(ML+xi6/6*plotW).toFixed(1);
    psvg.push('<line x1="'+xp5+'" y1="'+MT+'" x2="'+xp5+'" y2="'+(MT+plotH)+'" stroke="rgba(0,0,0,.08)" stroke-width=".8"/>');
    psvg.push('<text x="'+xp5+'" y="'+(MT+plotH+20)+'" text-anchor="middle" font-size="15" fill="#333">'+fmtX(xv5)+'</text>');
  }}
  // Y grid + ticks
  for(var yi5=0;yi5<=5;yi5++){{
    var yv5=ylo+yrng*yi5/5;
    var yp6=+(MT+plotH*(1-yi5/5)).toFixed(1);
    psvg.push('<line x1="'+ML+'" y1="'+yp6+'" x2="'+(ML+plotW)+'" y2="'+yp6+'" stroke="rgba(0,0,0,.08)" stroke-width=".8"/>');
    psvg.push('<text x="'+(ML-6)+'" y="'+yp6+'" text-anchor="end" dominant-baseline="middle" font-size="15" fill="#333">'+fmtY(yv5)+'</text>');
  }}

  // Scatter dots batched by colour
  // Collect points with colours
  var colBatches={{}};
  st.ys.forEach(function(yp,ypi){{
    (ptsByY[yp]||[]).forEach(function(pt){{
      var col;
      if(gby.length>0&&gbyCm){{
        var fakeRow={{lot:pt.lot,wafer:pt.wafer}};
        var gk=_grpKeyWith(fakeRow,gby);
        col=gbyCm.map[gk]||_PALETTE[0];
      }} else {{
        col=_PALETTE[ypi%_PALETTE.length];
      }}
      if(!colBatches[col])colBatches[col]='';
      var cx=px(pt.x), cy=py(pt.y);
      if(+cx<ML-3||+cx>ML+plotW+3||+cy<MT-3||+cy>MT+plotH+3)return;
      colBatches[col]+='M'+cx+','+cy+'m-3,0a3,3,0,1,0,6,0a3,3,0,1,0,-6,0';
    }});
  }});
  Object.keys(colBatches).forEach(function(col){{
    if(colBatches[col])psvg.push('<path d="'+colBatches[col]+'" fill="'+col+'" fill-opacity=".55" stroke="none"/>');
  }});

  // Trend lines + per-series/group median diamonds
  var medByY={{}};
  if(st.trend&&st.trend!=='none'){{
    if(gby.length>0&&gbyCm){{
      // Per-group fit lines
      var grpPts={{}};
      st.ys.forEach(function(yp){{
        (ptsByY[yp]||[]).forEach(function(pt){{
          var fakeRow={{lot:pt.lot,wafer:pt.wafer}};
          var gk=_grpKeyWith(fakeRow,gby);
          if(!grpPts[gk])grpPts[gk]={{pts:[],col:gbyCm.map[gk]||_PALETTE[0]}};
          grpPts[gk].pts.push(pt);
        }});
      }});
      Object.keys(grpPts).sort().forEach(function(gk){{
        var gd=grpPts[gk];
        var pts2=gd.pts.filter(function(p){{return +px(p.x)>=ML&&+px(p.x)<=ML+plotW&&+py(p.y)>=MT&&+py(p.y)<=MT+plotH;}});
        if(!pts2.length)return;
        var fitPts=pts2.slice(0,500).map(function(p){{return [p.x,p.y];}});
        var fit=st.trend==='ols'?_olsFit(fitPts):_theilsenFit(fitPts);
        if(!fit)return;
        var col=gd.col;
        var y1=fit.m*xlo+fit.b, y2=fit.m*xhi+fit.b;
        psvg.push('<line x1="'+px(xlo)+'" y1="'+py(y1)+'" x2="'+px(xhi)+'" y2="'+py(y2)+'"'
          +' stroke="'+col+'" stroke-width="2" stroke-dasharray="7,3" opacity=".85"/>');
        // Median diamond per group
        var sxm=pts2.map(function(p){{return p.x;}}).sort(function(a,b){{return a-b;}});
        var sym=pts2.map(function(p){{return p.y;}}).sort(function(a,b){{return a-b;}});
        var mx=sxm.length%2===0?(sxm[sxm.length/2-1]+sxm[sxm.length/2])/2:sxm[Math.floor(sxm.length/2)];
        var my=sym.length%2===0?(sym[sym.length/2-1]+sym[sym.length/2])/2:sym[Math.floor(sym.length/2)];
        var dx=px(mx), dy=py(my), dr=6;
        psvg.push('<polygon points="'+dx.toFixed(1)+','+(dy-dr).toFixed(1)+' '+(dx+dr).toFixed(1)+','+dy.toFixed(1)+' '+dx.toFixed(1)+','+(dy+dr).toFixed(1)+' '+(dx-dr).toFixed(1)+','+dy.toFixed(1)+'"'
          +' fill="'+col+'" stroke="#ffffff" stroke-width="1.4" opacity="0.95">'
            +'<title>'+_escH(gk)+' median X='+_fmtUpmAxis(mx,upmAsPct)+' Y='+_fmtVmin(my)+'</title></polygon>');
      }});
    }} else {{
      // No group-by: fit per Y param
      st.ys.forEach(function(yp,ypi){{
        var pts2=(ptsByY[yp]||[]).filter(function(p){{return +px(p.x)>=ML&&+px(p.x)<=ML+plotW&&+py(p.y)>=MT&&+py(p.y)<=MT+plotH;}});
        var fitPts=pts2.slice(0,300).map(function(p){{return [p.x,p.y];}});
      var fit=st.trend==='ols'?_olsFit(fitPts):_theilsenFit(fitPts);
      if(!fit)return;
      var col=_PALETTE[ypi%_PALETTE.length];
      // Clip to plot bounds
      var y1=fit.m*xlo+fit.b, y2=fit.m*xhi+fit.b;
      var x1=xlo,x2=xhi;
      // Simple clamp to data range
      psvg.push('<line x1="'+px(x1)+'" y1="'+py(y1)+'" x2="'+px(x2)+'" y2="'+py(y2)+'"'
        +' stroke="'+col+'" stroke-width="2" stroke-dasharray="7,3" opacity=".75"/>');

      // Median marker for this series: diamond at (median X, median Y)
      if(pts2.length){{
        var sxm=pts2.map(function(p){{return p.x;}}).sort(function(a,b){{return a-b;}});
        var sym=pts2.map(function(p){{return p.y;}}).sort(function(a,b){{return a-b;}});
        var mx=sxm.length%2===0?(sxm[sxm.length/2-1]+sxm[sxm.length/2])/2:sxm[Math.floor(sxm.length/2)];
        var my=sym.length%2===0?(sym[sym.length/2-1]+sym[sym.length/2])/2:sym[Math.floor(sym.length/2)];
        medByY[yp]={{x:mx,y:my,col:col}};
        var dx=px(mx), dy=py(my), dr=6;
        psvg.push('<polygon points="'+dx.toFixed(1)+','+(dy-dr).toFixed(1)+' '+(dx+dr).toFixed(1)+','+dy.toFixed(1)+' '+dx.toFixed(1)+','+(dy+dr).toFixed(1)+' '+(dx-dr).toFixed(1)+','+dy.toFixed(1)+'"'
          +' fill="'+col+'" stroke="#ffffff" stroke-width="1.4" opacity="0.95">'
            +'<title>'+_escH(_pDisp(yp))+' median X='+_fmtUpmAxis(mx,upmAsPct)+' Y='+_fmtVmin(my)+'</title></polygon>');
      }}
    }});
    }} // end else (no gby)
  }}

  // N label
  var totalPts=allX.length;
  psvg.push('<text x="'+(ML+4)+'" y="'+(MT-6)+'" font-size="15" fill="#999">n='+totalPts+'</text>');

  var xMeta=PCM_PARAM_META[st.x]||{{}};
  var _xUnit=(upmAsPct?'%':(xMeta.unit||''));
  // X axis label
  psvg.push('<text x="'+(ML+plotW/2)+'" y="'+(MT+plotH+34)+'" text-anchor="middle" font-size="15" fill="#333">'+_escH(_pDisp(st.x))+(_xUnit?' ('+_escH(_xUnit)+')':'')+'</text>');

  // Y axis label
  var yMeta=PCM_PARAM_META[st.ys[0]]||{{}};
  var yLabel=st.ys.length===1?(_escH(_pDisp(st.ys[0]))+(yMeta.unit?' ('+_escH(yMeta.unit)+')':'')):(yMeta.unit?'('+_escH(yMeta.unit)+')':'Vmin');
  psvg.push('<text transform="rotate(-90)" x="'+(-(MT+plotH/2))+'" y="16" text-anchor="middle" font-size="15" fill="#333">'+yLabel+'</text>');

  psvg.push('</svg>');
  
  /* Build group stats table */
  var groupStats=[];
  if(gby.length>0||st.ys.length>1){{
    try{{groupStats=_fpCalcGroupStats(pid,ptsByY,st,gby,upmRef);}}
    catch(e){{console.warn('_fpCalcGroupStats error:',e);}}
  }}
  _FP_GSTATS[pid]=groupStats;
  var statsTableHtml='';
  if(groupStats&&groupStats.length){{
    var xref=parseFloat(st.xref);
    var _fpFLbls={{'lot':'Lot','wafer':'Wfr','prog6248':'P6248','progU1U5':'PU1U5','material':'Material'}};
    var multiGbyInit=gby&&gby.length>1&&groupStats[0]&&groupStats[0].gbyParts&&groupStats[0].gbyParts.length>1;
    var tbodyRows='';
    groupStats.forEach(function(gs,i){{
      var bgColor=i%2?'#fff':'#f0f2f5';
      var vminAtRef=(isFinite(xref)&&gs.fitM!=null)?(+(gs.fitM*xref+gs.fitB).toFixed(4)):null;
      var eqStr=gs.fitM!=null?('y='+gs.fitM.toFixed(4)+'x'+(gs.fitB>=0?'+':'')+gs.fitB.toFixed(4)):'-';
      tbodyRows+='<tr style="background:'+bgColor+'">';
      if(multiGbyInit&&gs.gbyParts&&gs.gbyParts.length>1){{
        tbodyRows+='<td style="display:none"></td>';
        gs.gbyParts.forEach(function(v,fi){{
          var prefix=fi===0?'<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:'+(gs.color||'#2980b9')+';margin-right:5px;vertical-align:middle"></span>':'';
          tbodyRows+='<td style="padding:3px 6px;border:1px solid #ddd">'+prefix+_escH(v)+'</td>';
        }});
      }}else{{
        tbodyRows+='<td style="padding:3px 6px;border:1px solid #ddd"><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:'+(gs.color||'#2980b9')+';margin-right:5px;vertical-align:middle"></span>'+_escH(gs.group)+'</td>';
      }}
      tbodyRows+='<td style="padding:3px 6px;border:1px solid #ddd;text-align:center">'+gs.n+'</td>'
        +'<td style="padding:3px 6px;border:1px solid #ddd;text-align:center">'+_fmt(gs.r2,3)+'</td>'
        +'<td style="padding:3px 6px;border:1px solid #ddd;text-align:center">'+_fmtUpm(gs.medX)+'</td>'
        +'<td style="padding:3px 6px;border:1px solid #ddd;text-align:center">'+_fmtVmin(gs.medY)+'</td>'
        +'<td style="padding:3px 6px;border:1px solid #ddd;text-align:center">'+(vminAtRef!=null?_fmtVmin(vminAtRef):'-')+'</td>'
        +'<td style="padding:3px 6px;border:1px solid #ddd;text-align:center;font-size:11px;color:#555">'+eqStr+'</td>'
        +'</tr>';
    }});
    var grpThHtml=multiGbyInit
      ?('<th style="padding:3px 6px;text-align:left;border:1px solid #666;display:none">Group</th>'
        +gby.map(function(f){{return '<th style="padding:3px 6px;text-align:left;border:1px solid #666">'+(_fpFLbls[f]||f)+'</th>';}}).join(''))
      :'<th style="padding:3px 6px;text-align:left;border:1px solid #666">Group</th>';
    statsTableHtml='<div style="margin-top:12px;padding:8px;background:#f8f9fa;border:1px solid #ddd;border-radius:4px;overflow-x:auto">'
      +'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">'
        +'<b style="font-size:13px;color:#2c3e50">Group Stats</b>'
        +'<span style="font-size:12px;color:#555">X%:</span>'
        +'<input type="range" id="'+pid+'-xref" data-pid="'+pid+'" min="80" max="105" step="0.5" value="'+(isFinite(xref)?xref:93)+'" style="width:100px;accent-color:#3498db" oninput="_fpXrefSlider(this)">'
        +'<input type="number" id="'+pid+'-xref-txt" data-pid="'+pid+'" min="80" max="105" step="0.5" value="'+(isFinite(xref)?parseFloat(xref).toFixed(1):'93.0')+'" style="width:52px;font-size:12px;padding:1px 4px;border:1px solid #aaa;border-radius:3px;text-align:center" oninput="_fpXrefText(this)">'
      +'</div>'
      +'<table style="border-collapse:collapse;width:100%;font-family:monospace;font-size:13px">'
      +'<thead style="background:#2c3e50;color:#fff">'
        +'<tr>'
          +grpThHtml
          +'<th style="padding:3px 6px;text-align:center;border:1px solid #666">N</th>'
          +'<th style="padding:3px 6px;text-align:center;border:1px solid #666">R\u00b2</th>'
          +'<th style="padding:3px 6px;text-align:center;border:1px solid #666">Med X</th>'
          +'<th style="padding:3px 6px;text-align:center;border:1px solid #666">Med Y</th>'
          +'<th style="padding:3px 6px;text-align:center;border:1px solid #666" id="'+pid+'-vmin-hdr">Y@X='+(isFinite(xref)?parseFloat(xref).toFixed(1):'93.0')+'%</th>'
          +'<th style="padding:3px 6px;text-align:center;border:1px solid #666">Fit Line</th>'
        +'</tr>'
      +'</thead>'
      +'<tbody id="'+pid+'-stats-tbody">'+tbodyRows+'</tbody>'
      +'</table></div>';
  }}
  
  console.log('[DBG] setting innerHTML, groupStats.length=',groupStats.length);
  cont.innerHTML=psvg.join('')+statsTableHtml;

  // Attach crosshair and tooltip after SVG is in DOM
  var svgEl=document.getElementById(pid+'-svg');
  if(svgEl){{
    _initDragCursorsXY(svgEl,pid,ML,MT,plotW,plotH,xlo,xhi,ylo,yhi,fmtX,fmtY);
    // Tooltip
    var tt=_getTT();
    var allPts=[];
    var byKeyY={{}};
    st.ys.forEach(function(yp){{
      byKeyY[yp]={{}};
      (ptsByY[yp]||[]).forEach(function(pt){{
        allPts.push({{x:pt.x,y:pt.y,lot:pt.lot,wafer:pt.wafer,yp:yp}});
        var k=pt.lot+'/'+pt.wafer;
        if(!byKeyY[yp][k])byKeyY[yp][k]=[];
        byKeyY[yp][k].push(pt);
      }});
    }});
    svgEl.addEventListener('mousemove',function(e){{
      var rect=svgEl.getBoundingClientRect();
      var vb=svgEl.viewBox.baseVal;
      var sx=(e.clientX-rect.left)/rect.width*vb.width;
      var sy=(e.clientY-rect.top)/rect.height*vb.height;
      var best=null,bestD=22*22;
      allPts.forEach(function(pt){{
        var cx2=+px(pt.x),cy2=+py(pt.y);
        var d=(sx-cx2)*(sx-cx2)+(sy-cy2)*(sy-cy2);
        if(d<bestD){{bestD=d;best=pt;}}
      }});
      if(best){{
        var key=best.lot+'/'+best.wafer;
        var wmeta=_WFR_LOOKUP[key]||{{}};
        var sxr=(wmeta.xmin!=null&&wmeta.xmax!=null)?(String(wmeta.xmin)+'..'+String(wmeta.xmax)):'N/A';
        var syr=(wmeta.ymin!=null&&wmeta.ymax!=null)?(String(wmeta.ymin)+'..'+String(wmeta.ymax)):'N/A';
        var htmlTT='<b>'+_escH(best.lot)+' / '+_escH(String(best.wafer))+'</b><br>'
          +'Class_Lot: '+_escH(best.lot)+'<br>'
          +'Sort_Lot: '+_escH(wmeta.sort_lot||'')+'<br>'
          +'Sort_Wafer: '+_escH(String(wmeta.wafer||best.wafer||''))+'<br>'
          +'Class Prog 6248: '+_escH(wmeta.prog6248||'')+'<br>'
          +'Sort Prog U1.U5: '+_escH(wmeta.progU1U5||'')+'<br>'
          +'Sort_X: '+_escH(sxr)+'<br>'
          +'Sort_Y: '+_escH(syr)+'<br>'
          +'X: '+_fmtUpmAxis(best.x,upmAsPct)+(upmAsPct?'':(xMeta.unit?' '+_escH(xMeta.unit):''));
        if(st.ys.length===1){{
          htmlTT+='<br>Y: '+_fmt(best.y,4);
        }} else {{
          st.ys.forEach(function(yp){{
            var arr=(byKeyY[yp]&&byKeyY[yp][key])?byKeyY[yp][key]:[];
            if(!arr.length)return;
            var pick=arr[0], bestDx=Math.abs(arr[0].x-best.x);
            for(var ai=1;ai<arr.length;ai++){{
              var dx=Math.abs(arr[ai].x-best.x);
              if(dx<bestDx){{bestDx=dx;pick=arr[ai];}}
            }}
            htmlTT+='<br><b>'+_escH(yp)+'</b>: '+_fmt(pick.y,4);
          }});
        }}
        tt.innerHTML=htmlTT;
        tt.style.left=(e.clientX+14)+'px';
        tt.style.top=(e.clientY-48)+'px';
        tt.style.display='block';
      }} else {{
        tt.style.display='none';
      }}
    }});
    svgEl.addEventListener('mouseleave',function(){{tt.style.display='none';}});
  }}
  }} catch(e) {{
    cont.innerHTML='<p style="padding:12px;color:#c00;font-size:14px">Chart error: '+_escH(String(e))+'</p>';
    console.error('_fpRenderChart['+pid+']',e);
  }}
}}

/* Helper: map group key -> palette index deterministically */
var _GBY_IDX_CACHE={{}};
function _grpKey2idx(k,gby,_){{
  var cacheKey=gby.join('|')+'__'+k;
  if(_GBY_IDX_CACHE[cacheKey]!=null)return _GBY_IDX_CACHE[cacheKey];
  var seen=Object.keys(_GBY_IDX_CACHE).filter(function(ck){{return ck.indexOf(gby.join('|'))===0;}}).length;
  _GBY_IDX_CACHE[cacheKey]=seen;
  return seen;
}}

/* ── Roll-Down Details/Simulator ───────────────────────────────────────── */
function _openRollinSim(mod){{
  // Toggle: re-clicking closes the modal
  var ex=document.getElementById('ri-sim-overlay');
  if(ex){{document.body.removeChild(ex);return;}}

  var ptd=(PASS_TABLE||{{}})[mod]||{{}};
  var fd=FLOW_DATA[mod]||{{}};
  var st=_flowPassVminState(mod);
  var freqs=Object.keys(ptd.freq_data||{{}}).map(Number).sort(function(a,b){{return b-a;}});
  var modLabel=_flowSubTabLabel(mod,fd);

  // Population medians for extrapolation
  var popMed={{}};
  freqs.forEach(function(fmhz){{
    var sg=(ptd.freq_data||{{}})[String(fmhz)]||{{}};
    var grp=(sg.groups&&(sg.groups['4']||sg.groups['3']||sg.groups['2']||sg.groups['1']))||null;
    if(grp&&grp.med_vmin!=null) popMed[fmhz]=grp.med_vmin;
  }});
  var freqLabels={{}};
  freqs.forEach(function(fmhz){{
    freqLabels[fmhz]=((ptd.freq_data||{{}})[String(fmhz)]||{{}}).freq_label||(fmhz/1000+'G');
  }});
  // Fall back to some defaults if no pass table
  if(!freqs.length) freqs=[5400,5200,5100,5000,4800];
  freqs.forEach(function(fmhz){{if(!freqLabels[fmhz]) freqLabels[fmhz]=fmhz/1000+'G';}});

  var thrDefault=st.thresh!=null?_fmtVmin(st.thresh):'1.15';

  var ov=document.createElement('div');
  ov.id='ri-sim-overlay';
  ov.className='ri-overlay';
  ov.addEventListener('click',function(e){{if(e.target===ov){{document.body.removeChild(ov);}}}}); 

  var card=document.createElement('div');
  card.id='ri-sim-card';
  card.className='ri-card';
  card._riFreqs=freqs;
  card._riFreqLabels=freqLabels;
  card._riPopMed=popMed;

  // ── Header ──────────────────────────────────────────────────────────────
  var hdrEl=document.createElement('div');
  hdrEl.className='ri-hdr';
  hdrEl.innerHTML=
    '<div>'+
      '<span style="font-size:14px;font-weight:bold">&#9660; Roll-Down Details/Simulator</span>'+
      '<span style="font-size:12px;color:#aed6f1;margin-left:10px">\u2014 '+_tpEsc(modLabel)+'</span>'+
    '</div>'+
    '<button id="ri-close-btn" '+
      'style="background:rgba(255,255,255,0.15);border:1px solid rgba(255,255,255,0.3);'+
      'color:#fff;font-size:14px;cursor:pointer;padding:2px 10px;border-radius:4px">&#10005; Close</button>';
  hdrEl.querySelector('#ri-close-btn').addEventListener('click',function(){{
    var e=document.getElementById('ri-sim-overlay');if(e) document.body.removeChild(e);
  }});

  // ── Body ────────────────────────────────────────────────────────────────
  var bodyEl=document.createElement('div');
  bodyEl.className='ri-body';

  // Algorithm accordion
  var algoEl=document.createElement('details');
  algoEl.className='ri-algo';
  algoEl.innerHTML=
    '<summary>Roll-Down Algorithm</summary>'+
    '<div class="ri-algo-inner">'+
      '<div>'+
        '<b style="color:#1a4a7a">1 \u2014 Binning at Test Frequency</b><br>'+
        'Each package is measured at its highest certified frequency. '+
        'Based on measured average Vmin and how many DCMs pass, it is placed in a bin. '+
        'The simulator is generic \u2014 choose 1\u20134 DCMs above:<br>'+
        '<span style="color:#1a4a7a">&#9632;</span> <b>N DCM (All-Pass)</b> \u2014 all simulated DCMs pass (avg Vmin \u2264 threshold)<br>'+
        '<span style="color:#1a6b3a">&#9632;</span> <b>k DCM</b> \u2014 exactly k of N DCMs pass (k \u2265 2)<br>'+
        '<span style="color:#e67e22">&#9632;</span> <b>1 DCM</b> \u2014 only 1 of N DCMs passes<br>'+
        '<span style="color:#c62828">&#9632;</span> <b>Reset</b> \u2014 0 DCMs pass<br><br>'+
        '<b style="color:#1a4a7a">2 \u2014 Roll-Down Candidates</b><br>'+
        'A unit is a <em>roll-down candidate</em> when its average Vmin exceeds the '+
        'threshold at the current frequency. These units are evaluated at the next '+
        'lower frequency to see if they can qualify there instead.<br>'+
      '</div>'+
      '<div>'+
        '<b style="color:#1a4a7a">3 \u2014 Vmin Extrapolation</b><br>'+
        'If a unit was not tested at an intermediate frequency, its Vmin is '+
        'estimated using the population median delta:<br>'+
        '<code style="background:#e8f0fe;padding:2px 6px;border-radius:3px;font-size:10.5px">'+
          'V_est\u00a0=\u00a0V_high\u00a0+\u00a0(Med(F_target)\u00a0\u2212\u00a0Med(F_high))'+
        '</code><br>'+
        'This preserves the unit\u2019s relative position in the population.<br><br>'+
        '<b style="color:#1a4a7a">4 \u2014 Bin Preservation</b><br>'+
        'The DCM count classification (Premium/2\u202fDCM/Reset) is determined at the '+
        'original test frequency and is <em>preserved</em> as the unit rolls down. '+
        'Rolling changes only the <em>frequency bin</em>, not the quality class.<br>'+
      '</div>'+
    '</div>';

  // Simulator controls  
  var simEl=document.createElement('div');
  simEl.className='ri-sim-body';

  // Build frequency header cells with pop-median tooltip
  var freqThCells='';
  freqs.forEach(function(fmhz){{
    var pm=popMed[fmhz];
    freqThCells+=
      '<th style="padding:5px 10px;background:#2c3e50;color:#fff;border:1px solid #445;white-space:nowrap;text-align:center" '+
        'title="'+(pm!=null?'Population median Vmin: '+_fmtVmin(pm):'No median data')+'">'+
        _tpEsc(freqLabels[fmhz])+
        (pm!=null?'<br><span style="font-weight:normal;font-size:9px;color:#90caf9">med:'+_fmtVmin(pm)+'</span>':'')+
      '</th>';
  }});

  simEl.innerHTML=
    '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px">'+
      '<b style="font-size:12px;color:#1a4a7a">&#9660; DCM Roll-Down Simulator</b>'+
      '<label style="font-size:11px;color:#555;display:flex;align-items:center;gap:5px">'+
        'Threshold\u00a0(V):'+
        '<input id="ri-thresh" type="number" step="0.005" value="'+thrDefault+'" '+
          'style="width:78px;padding:2px 5px;border:1px solid #aac;border-radius:4px;font-size:11px">'+
      '</label>'+
      '<label style="font-size:11px;color:#555;display:flex;align-items:center;gap:5px">'+
        'DCMs\u00a0to\u00a0simulate:'+
        '<select id="ri-ndcm" onchange="_riRebuildRows()" '+
          'style="padding:2px 6px;border:1px solid #aac;border-radius:4px;font-size:11px">'+
          '<option value="1">1\u00a0DCM</option>'+
          '<option value="2">2\u00a0DCM</option>'+
          '<option value="3" selected>3\u00a0DCM</option>'+
          '<option value="4">4\u00a0DCM</option>'+
        '</select>'+
      '</label>'+
      '<button onclick="_riSimulate()" '+
        'style="padding:4px 16px;background:#1a4a7a;color:#fff;border:none;border-radius:4px;'+
               'font-size:12px;cursor:pointer;font-weight:bold">&#9654; Simulate</button>'+
      '<span style="font-size:10.5px;color:#888">Enter Vmin (V) per DCM per frequency. Leave blank if not tested \u2014 will be extrapolated.</span>'+
    '</div>'+
    '<div style="overflow-x:auto">'+
      '<table id="ri-grid" class="ri-grid" style="border-collapse:collapse;font-size:11px">'+
        '<thead><tr>'+
          '<th style="padding:5px 12px;background:#2c3e50;color:#fff;border:1px solid #445;white-space:nowrap;text-align:center">DCM</th>'+
          freqThCells+
        '</tr></thead>'+
        '<tbody id="ri-rows"></tbody>'+
      '</table>'+
    '</div>'+
    '<div id="ri-results" style="margin-top:14px"></div>';

  bodyEl.appendChild(algoEl);
  bodyEl.appendChild(simEl);
  card.appendChild(hdrEl);
  card.appendChild(bodyEl);
  ov.appendChild(card);
  document.body.appendChild(ov);
  _riRebuildRows();
}}

function _riRebuildRows(){{
  var card=document.getElementById('ri-sim-card');
  if(!card) return;
  var freqs=card._riFreqs||[];
  var tbody=document.getElementById('ri-rows');
  if(!tbody) return;
  var nDcm=parseInt((document.getElementById('ri-ndcm')||{{}}).value)||3;
  var html='';
  for(var d=1;d<=nDcm;d++){{
    var rowBg=(d%2===0?'#f7fafd':'#fff');
    html+='<tr style="background:'+rowBg+'">'+
      '<td style="padding:5px 12px;font-weight:bold;color:#1a4a7a;border:1px solid #dde;'+
          'white-space:nowrap;background:#eef5ff">DCM\u00a0'+d+'</td>';
    freqs.forEach(function(fmhz){{
      html+='<td style="padding:3px 6px;border:1px solid #dde;text-align:center">'+
        '<input id="ri-v-'+d+'-'+fmhz+'" type="number" step="0.001" placeholder="e.g.\u00a01.18" '+
          'style="width:82px;padding:2px 4px;border:1px solid #ccc;border-radius:3px;font-size:11px">'+
        '</td>';
    }});
    html+='</tr>';
  }}
  tbody.innerHTML=html;
  var res=document.getElementById('ri-results');
  if(res) res.innerHTML='';
}}

function _riSimulate(){{
  var card=document.getElementById('ri-sim-card');
  if(!card) return;
  var freqs=(card._riFreqs||[]).slice().sort(function(a,b){{return b-a;}}); // highest first
  var freqLabels=card._riFreqLabels||{{}};
  var popMed=card._riPopMed||{{}};
  var thrEl=document.getElementById('ri-thresh');
  var thresh=thrEl?parseFloat(thrEl.value):NaN;
  var nDcmEl=document.getElementById('ri-ndcm');
  var nDcm=nDcmEl?parseInt(nDcmEl.value):3;
  if(isNaN(thresh)){{alert('Please enter a valid threshold (V).');return;}}

  // Collect entered Vmin per DCM per frequency
  var dcmVmins=[];
  for(var d=1;d<=nDcm;d++){{
    var row={{}};
    freqs.forEach(function(fmhz){{
      var el=document.getElementById('ri-v-'+d+'-'+fmhz);
      var v=el?parseFloat(el.value):NaN;
      row[fmhz]=isNaN(v)?null:v;
    }});
    dcmVmins.push(row);
  }}

  // For each frequency (highest → lowest): compute per-DCM pass/fail,
  // extrapolating missing values from nearest higher-freq reading + pop-median delta
  var results=[];
  freqs.forEach(function(fmhz,fi){{
    var freqLabel=freqLabels[fmhz]||(fmhz/1000+'G');
    var dcmData=dcmVmins.map(function(dcmRow,di){{
      var v=dcmRow[fmhz];
      if(v===null){{
        // Extrapolate from the nearest higher frequency that has a value
        for(var hfi=0;hfi<fi;hfi++){{
          var hf=freqs[hfi];
          var hv=dcmRow[hf];
          if(hv!==null&&popMed[hf]!=null&&popMed[fmhz]!=null){{
            v=hv+(popMed[fmhz]-popMed[hf]);
            break;
          }}
        }}
      }}
      return {{dcm:di+1,vmin:v,pass:(v!==null&&v<=thresh),extrapolated:(dcmRow[fmhz]===null&&v!==null)}};
    }});
    var nPass=dcmData.filter(function(d){{return d.pass;}}).length;
    var validVs=dcmData.map(function(d){{return d.vmin;}}).filter(function(v){{return v!==null;}});
    var avgV=validVs.length?validVs.reduce(function(a,b){{return a+b;}},0)/validVs.length:null;
    // Bin assignment — generic for any nDcm: nPass DCMs passed, 0 = Reset
    var bin,binColor;
    var _BIN_COLORS=['#c62828','#e67e22','#2e7d32','#1a6b3a','#1a4a7a','#0d3060'];
    if(nPass===0){{
      bin='Reset';
      binColor='#c62828';
    }} else if(nPass===nDcm){{
      bin=nDcm===1?'1 DCM (Pass)':(nDcm+' DCM (All-Pass)');
      binColor='#1a4a7a';
    }} else {{
      bin=nPass+' DCM';
      // colour gradient: 1 pass = orange, more = progressively greener
      binColor=nPass===1?'#e67e22':(_BIN_COLORS[Math.min(nPass,_BIN_COLORS.length-1)]||'#2e7d32');
    }}
    results.push({{fmhz:fmhz,freqLabel:freqLabel,dcmData:dcmData,nPass:nPass,avgV:avgV,bin:bin,binColor:binColor}});
  }});

  // Find first landing index per DCM count: _landAt[k] = first freq where nPass >= k.
  // Landing is based on individual per-DCM pass/fail, not average Vmin.
  var _landAt={{}};
  for(var _k=1;_k<=nDcm;_k++){{
    for(var _ri=0;_ri<results.length;_ri++){{
      if(results[_ri].nPass>=_k){{_landAt[_k]=_ri;break;}}
    }}
  }}
  // Primary landing = first freq where ALL selected DCMs individually pass
  var landedIdx=(_landAt[nDcm]!==undefined)?_landAt[nDcm]:results.length-1;

  // Build result table
  var rhtml='<div style="font-weight:bold;font-size:12px;color:#1a4a7a;margin-bottom:8px">'+
    'Simulation Results '+
    '<span style="font-weight:normal;font-size:11px;color:#888">(threshold: '+thresh.toFixed(3)+'V, '+nDcm+' DCM'+(nDcm>1?'s':'')+')</span>'+
    '</div>';
  rhtml+='<div style="overflow-x:auto"><table style="border-collapse:collapse;font-size:11px;min-width:400px">';
  rhtml+='<thead><tr>';
  rhtml+='<th class="ri-grid th" style="padding:5px 10px;background:#1a4a7a;color:#fff;border:1px solid #334;white-space:nowrap">Freq</th>';
  rhtml+='<th style="padding:5px 10px;background:#1a4a7a;color:#fff;border:1px solid #334;white-space:nowrap">Avg\u00a0Vmin</th>';
  for(var d=1;d<=nDcm;d++)
    rhtml+='<th style="padding:5px 10px;background:#1a4a7a;color:#fff;border:1px solid #334;white-space:nowrap">DCM\u00a0'+d+'</th>';
  rhtml+='<th style="padding:5px 10px;background:#1a4a7a;color:#fff;border:1px solid #334;white-space:nowrap">Pass\u00a0DCMs</th>';
  rhtml+='<th style="padding:5px 10px;background:#1a4a7a;color:#fff;border:1px solid #334;white-space:nowrap">Bin</th>';
  rhtml+='<th style="padding:5px 10px;background:#1a4a7a;color:#fff;border:1px solid #334;white-space:nowrap">Roll-Down?</th>';
  rhtml+='</tr></thead><tbody>';

  results.forEach(function(r,ri){{
    var isLanded=(ri===landedIdx);
    // Highest DCM-count milestone that first lands at this row
    var _landK=null;
    for(var k=nDcm;k>=1;k--){{if(_landAt[k]===ri){{_landK=k;break;}}}}
    var rowBg=isLanded?'#e8f5e9':(_landK!==null?'#eaf2fb':(ri%2===0?'#f7fafd':'#fff'));
    rhtml+='<tr style="background:'+rowBg+'">';
    var _freqArrow=isLanded
      ?'<span title="All '+nDcm+' DCMs land here" style="color:#27ae60">&#9658;\u00a0</span>'
      :(_landK!==null?'<span title="'+_landK+' DCM(s) first land here" style="color:#2980b9">&#9654;\u00a0</span>':'');
    rhtml+='<td style="padding:5px 10px;font-weight:bold;color:#1a4a7a;border:1px solid #dde;white-space:nowrap">'+
      _freqArrow+_tpEsc(r.freqLabel)+'</td>';
    rhtml+='<td style="padding:5px 10px;border:1px solid #dde;font-weight:bold;color:'+(r.avgV!==null&&r.avgV>thresh?'#c62828':'#27ae60')+'">'+
      (r.avgV!==null?r.avgV.toFixed(4)+'V':'—')+'</td>';
    r.dcmData.forEach(function(d){{
      var vStr=d.vmin!==null?d.vmin.toFixed(4)+'V':'—';
      if(d.extrapolated) vStr='~'+vStr;
      rhtml+='<td style="padding:5px 10px;border:1px solid #dde;color:'+(d.pass?'#27ae60':'#c62828')+
        '" title="'+(d.extrapolated?'Extrapolated value (pop-median delta)':'Measured value')+'">'+
        vStr+(d.pass?'\u00a0\u2713':'\u00a0\u2717')+'</td>';
    }});
    rhtml+='<td style="padding:5px 10px;border:1px solid #dde;font-weight:bold;text-align:center;color:'+r.binColor+'">'+r.nPass+'/'+nDcm+'</td>';
    rhtml+='<td style="padding:5px 10px;border:1px solid #dde;font-weight:bold;color:'+r.binColor+'">'+_tpEsc(r.bin)+'</td>';
    var _rollCell;
    if(isLanded){{
      _rollCell='<span style="color:#27ae60;font-weight:bold">&#10003;\u00a0Lands\u00a0here\u00a0('+nDcm+'\u00a0DCM)</span>';
    }} else if(_landK!==null){{
      _rollCell='<span style="color:#2980b9;font-weight:bold">&#10003;\u00a0Lands\u00a0('+_landK+'\u00a0DCM)</span>';
    }} else if(ri<landedIdx){{
      _rollCell='<span style="color:#e67e22;font-weight:bold">&#8595;\u00a0Roll-Down</span>';
    }} else {{
      _rollCell='<span style="color:#aaa">\u2014</span>';
    }}
    rhtml+='<td style="padding:5px 10px;border:1px solid #dde;text-align:center">'+_rollCell+'</td>';
    rhtml+='</tr>';
  }});
  rhtml+='</tbody></table></div>';

  // Summary banner
  var landed=results[landedIdx];
  rhtml+='<div style="margin-top:12px;padding:10px 16px;background:#f0f6ff;border:2px solid '+landed.binColor+';border-radius:6px;font-size:12px">'+
    '<b style="color:#1a4a7a">&#9650; Final Result:\u00a0</b>'+
    '<span style="color:'+landed.binColor+';font-weight:bold;font-size:13px">'+_tpEsc(landed.bin)+'</span>'+
    '\u00a0<b>at</b>\u00a0'+
    '<span style="color:#1a4a7a;font-weight:bold">'+_tpEsc(landed.freqLabel)+'</span>'+
    (landedIdx>0
      ?' <span style="color:#e67e22;font-size:11px">(\u2193 rolled down from '+_tpEsc(results[0].freqLabel)+')</span>'
      :' <span style="color:#27ae60;font-size:11px">(no roll-down needed)</span>')+
    '</div>';

  var resEl=document.getElementById('ri-results');
  if(resEl) resEl.innerHTML=rhtml;
}}

/* ── Init ───────────────────────────────────────────────────────────────── */
try {{
  _renderTpDetails();
  buildWfrList();
  rerender();
}} catch(e) {{
  _reportJsError('init', e);
}}
// Activate first XY tab for render (lazy — only when user clicks)

function _setFlowSubTab(tab) {{
  var btnFreq = document.getElementById('btn-flow-freq');
  var btnBin  = document.getElementById('btn-flow-bin');
  var pFreq   = document.getElementById('tab-flow-freq');
  var pBin    = document.getElementById('tab-flow-bin');
  if (tab === 'freq') {{
    btnFreq.classList.add('active');
    btnBin.classList.remove('active');
    pFreq.style.display = 'block';
    pBin.style.display = 'none';
  }} else {{
    btnBin.classList.add('active');
    btnFreq.classList.remove('active');
    pBin.style.display = 'block';
    pFreq.style.display = 'none';
    renderBinMatrix();
  }}
}}

function _makeColsResizable(table) {{
  var ths = table.querySelectorAll('th');
  // Remove min/max-width constraints, snapshot computed widths, then fix layout
  ths.forEach(function(th) {{
    th.style.minWidth = '';
    th.style.maxWidth = '';
    th.style.width = th.offsetWidth + 'px';
  }});
  table.style.tableLayout = 'fixed';
  ths.forEach(function(th, ci) {{
    th.style.position = 'relative';
    th.style.overflow = 'hidden';
    if (ci >= 2) {{ th.style.whiteSpace = 'normal'; th.style.wordBreak = 'break-word'; }}
    else {{ th.style.whiteSpace = 'nowrap'; }}
    var grip = document.createElement('div');
    grip.style.cssText = 'position:absolute;right:0;top:0;width:8px;height:100%;cursor:col-resize;user-select:none;z-index:1;background:rgba(255,255,255,0.25);';
    grip.addEventListener('mousedown', function(e) {{
      e.preventDefault();
      var startX = e.pageX, startW = th.offsetWidth;
      function onMove(e) {{ th.style.width = Math.max(40, startW + e.pageX - startX) + 'px'; }}
      function onUp() {{ document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); }}
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    }});
    th.appendChild(grip);
  }});
}}

function _addTableHover(table) {{
  var tbody = table.querySelector('tbody');
  if (!tbody) return;
  // Row hover: save/restore each td's background directly
  tbody.querySelectorAll('tr').forEach(function(tr) {{
    tr.addEventListener('mouseover', function() {{
      Array.from(this.cells).forEach(function(td) {{
        td._bmOrigBg = td.style.background;
        td.style.background = '#bfdbfe';
      }});
    }});
    tr.addEventListener('mouseout', function() {{
      Array.from(this.cells).forEach(function(td) {{
        td.style.background = td._bmOrigBg !== undefined ? td._bmOrigBg : '';
      }});
    }});
  }});
  // Column hover: highlight th and outline column cells
  table.querySelectorAll('thead th').forEach(function(th, ci) {{
    th.addEventListener('mouseover', function() {{
      this._bmOrigFilter = this.style.filter;
      this.style.filter = 'brightness(1.25)';
      tbody.querySelectorAll('tr').forEach(function(tr) {{
        var td = tr.cells[ci];
        if (td) td.style.outline = '2px solid #2563a8';
      }});
    }});
    th.addEventListener('mouseout', function() {{
      this.style.filter = this._bmOrigFilter || '';
      tbody.querySelectorAll('tr').forEach(function(tr) {{
        var td = tr.cells[ci];
        if (td) td.style.outline = '';
      }});
    }});
  }});
}}

function _bmShowCard(id) {{
  var c = document.getElementById(id);
  if (!c) return;
  var bd = c.querySelector('.bm-card-body');
  var tb = c.querySelector('.bm-toggle-btn');
  if (c.style.display === 'none') {{
    // card hidden entirely — show it expanded
    c.style.display = 'block';
    if (bd) bd.style.display = 'block';
    if (tb) tb.textContent = 'Hide';
    // lazy-initialize table layout now that the card is visible
    requestAnimationFrame(function() {{
      c.querySelectorAll('table').forEach(function(t) {{
        var ths = t.querySelectorAll('thead th');
        if (ths.length > 1) {{
          var col2W = ths[1].offsetWidth;
          if (col2W > 0) {{
            var targetW = Math.round(col2W * 0.75) + 'px';
            for (var ci=2; ci<ths.length; ci++) {{ ths[ci].style.width = targetW; }}
            t.querySelectorAll('tbody tr').forEach(function(tr) {{
              for (var ci=2; ci<tr.cells.length; ci++) {{ tr.cells[ci].style.width = targetW; }}
            }});
          }}
        }}
        _makeColsResizable(t); _addTableHover(t);
      }});
    }});
  }} else if (bd && bd.style.display === 'none') {{
    // card visible but collapsed — expand it
    bd.style.display = 'block';
    if (tb) tb.textContent = 'Hide';
  }} else {{
    // card visible and expanded — collapse it
    if (bd) bd.style.display = 'none';
    if (tb) tb.textContent = 'Show';
  }}
  c.scrollIntoView({{behavior:'smooth', block:'nearest'}});
}}

function renderBinMatrix() {{
  var bdiv = document.getElementById('tab-flow-bin-body');
  if (!bdiv) return;
  if (!QDF_ROWS || !QDF_ROWS.length || !BM_ROWS || !BM_ROWS.length) {{
    bdiv.innerHTML = '<p style="padding:20px;color:#888">No Bin Matrix data or QDF configuration found.</p>';
    return;
  }}
  var allowedWfrs = new Set();
  // Build a map from "lot/wafer" -> material using ALL WFR_DATA
  // (bin matrix filter is independent of left panel selection)
  var wfrMatMap = {{}};
  var wfrProgMap = {{}};  // lot/wafer -> prog6248
  var _bmSp6248 = (_SEARCH.prog6248||'').toLowerCase();
  WFR_DATA.forEach(function(w) {{
    if(_bmSp6248&&(w.prog6248||'').toLowerCase().indexOf(_bmSp6248)<0)return;
    var kw = w.lot + '/' + w.wafer;
    allowedWfrs.add(kw);
    wfrMatMap[kw] = w.material || '(none)';
    wfrProgMap[kw] = w.prog6248 || '(none)';
  }});

  // Filter BM_ROWS using the active lot+wafer keys AND IBIN==1
  // (In BM_ROWS we already prefiltered IBIN==1 dies and we have .lot and .wafer)
  var _allBmRows = [];
  for (var i = 0; i < BM_ROWS.length; i++) {{
    var r = BM_ROWS[i];
    var kw = r.lot + '/' + r.wafer;
    if (allowedWfrs.has(kw)) {{
      _allBmRows.push(r); // In Python we already pre-filtered out non-IBIN1
    }}
  }}
  var passing = _allBmRows.slice();  // narrowed by product scope + filters

  function _f1(v){{ return isNaN(v)?'–':(+v).toFixed(1); }}
  function _pct(v){{ return isNaN(v)?'–':_f1(v)+'%'; }}

  // Build UPM median per lot/wafer from PCM_ROWS
  var _bmUpmKey = null;
  if (typeof PCM_ROWS !== 'undefined' && PCM_ROWS && PCM_ROWS.length) {{
    PCM_ROWS.some(function(r) {{
      if (/UPM[ ]*107[_ -]*950/i.test(r.param||'')) {{ _bmUpmKey = r.param; return true; }}
      return false;
    }});
    if (!_bmUpmKey && typeof UPM_LABELS !== 'undefined') {{
      Object.keys(UPM_LABELS||{{}}).some(function(k) {{
        if (/107[_ -]*950/i.test(k)) {{ _bmUpmKey = k; return true; }}
        return false;
      }});
    }}
    if (!_bmUpmKey) {{
      PCM_ROWS.some(function(r) {{
        if (/UPM/i.test(r.param||'')) {{ _bmUpmKey = r.param; return true; }}
        return false;
      }});
    }}
  }}
  // lot/wafer -> median UPM
  var _bmUpmByWafer = {{}};
  if (_bmUpmKey && typeof PCM_ROWS !== 'undefined') {{
    PCM_ROWS.forEach(function(r) {{
      if (r.param === _bmUpmKey) {{
        var kw = r.lot + '/' + r.wafer;
        if (allowedWfrs.has(kw)) _bmUpmByWafer[kw] = r.median;
      }}
    }});
  }}
  function _medOfArr(arr) {{
    if (!arr.length) return NaN;
    var s = arr.slice().sort(function(a,b){{return a-b;}});
    var m = Math.floor(s.length/2);
    return s.length%2 ? s[m] : (s[m-1]+s[m])/2;
  }}
  function _groupMedUpm(groupRows) {{
    var vals = [];
    groupRows.forEach(function(r) {{
      // Prefer speed_pct stored directly on each BM row (UPM% computed in Python)
      var v = (r.speed_pct != null) ? r.speed_pct : _bmUpmByWafer[r.lot+'/'+r.wafer];
      if (v != null && !isNaN(v)) vals.push(+v);
    }});
    return _medOfArr(vals);
  }}

  var groupFns = [
    {{ label: "Overall (by WW)", fn: function(r) {{ return r.ww || '(none)'; }} }},
    {{ label: "By DLCP",         fn: function(r) {{ return (r.ww||'(none)') + ' | ' + (r.dlcp||'(none)'); }} }},
    {{ label: "By TP Rev",       fn: function(r) {{ return (r.ww||'(none)') + ' | ' + (r.tp_rev||'(none)'); }} }},
    {{ label: "By Material",     fn: function(r) {{ return wfrMatMap[r.lot+'/'+r.wafer] || '(none)'; }} }},
    {{ label: "By Mat & TP Rev", fn: function(r) {{ return (wfrMatMap[r.lot+'/'+r.wafer]||'(none)') + ' | ' + (r.tp_rev||'(none)'); }} }}
  ];

    // ── Product tab setup (from QDF 'Product Name' column) ────────────────────
    var _bmProdNameCol = 'Product Name';
    var _bmProductsList = [];
    var _bmProdQdfMap = {{}};
    QDF_ROWS.forEach(function(r) {{
      var prod = (r[_bmProdNameCol] || '').trim() || '(none)';
      if (_bmProductsList.indexOf(prod) < 0) _bmProductsList.push(prod);
      (_bmProdQdfMap[prod] = _bmProdQdfMap[prod] || []).push(r);
    }});
    // Sort: BLLC-containing products first, then alphabetically
    _bmProductsList.sort(function(a, b) {{
      var aB = /BLLC/i.test(a) ? 0 : 1;
      var bB = /BLLC/i.test(b) ? 0 : 1;
      if (aB !== bB) return aB - bB;
      return a.localeCompare(b);
    }});
    var _bmActiveProd = _bmProductsList[0] || '';
    var _bmActiveQdfRows = _bmProdQdfMap[_bmActiveProd] || QDF_ROWS;
    // specCols excluding 'Product Name' (displayed as product tab label instead)
    var specCols = Object.keys(QDF_ROWS[0] || {{}}).filter(function(c){{ return c !== _bmProdNameCol; }});
    var qdfIdCol = specCols[0] || 'QDF';

    var html = '<div style="padding:10px">';
    if (IBIN1_COL) {{
      var _ibin1Cnt = _allBmRows.length;
      html += '<div style="font-size:11px;color:#555;padding:5px 10px;background:#fff8e1;border:1px solid #f9a825;border-radius:4px;margin-bottom:8px;display:flex;align-items:center;gap:12px">'+
        '<span>&#128196; Pass tables: <b>'+_escH(IBIN1_COL)+'</b> = 1 units only</span>'+
        '<span style="color:#aaa">&nbsp;|&nbsp;</span>'+
        '<span>IBIN=1 count (current product): <b id="bm-ibin1-cnt">'+_ibin1Cnt.toLocaleString()+'</b>'+
        (IBIN1_COUNT!=null?'<span style="color:#aaa;font-size:10px"> / '+Number(IBIN1_COUNT).toLocaleString()+' total</span>':'')+
        '</span></div>';
    }}

    // ── Product subtabs (by 'Product Name' column in QDF file) ────────────
    if (_bmProductsList.length > 1) {{
      var _bmProdTabColors = [
        {{ bg: '#1e40af', bgOff: '#dbeafe', textOff: '#1e3a8a' }},
        {{ bg: '#5b21b6', bgOff: '#ede9fe', textOff: '#4c1d95' }},
        {{ bg: '#065f46', bgOff: '#dcfce7', textOff: '#14532d' }},
        {{ bg: '#164e63', bgOff: '#cffafe', textOff: '#155e75' }},
        {{ bg: '#374151', bgOff: '#e5e7eb', textOff: '#1f2937' }},
      ];
      html += '<div style="display:flex;gap:0;margin-bottom:0;border:2px solid #c6d9f0;border-radius:6px 6px 0 0;overflow:hidden;border-bottom:none">';
      _bmProductsList.forEach(function(prod, pi) {{
        var col = _bmProdTabColors[pi % _bmProdTabColors.length];
        var isActive = prod === _bmActiveProd;
        html += '<button id="bm-prod-tab-btn-'+pi+'" onclick="_bmSetProdTab('+pi+')" '+
                'style="flex:1;padding:8px 14px;border:none;border-right:1px solid #c6d9f0;cursor:pointer;'+
                'font-size:12px;font-weight:'+(isActive?'700':'500')+';'+
                'color:'+(isActive?'#fff':col.textOff)+';background:'+(isActive?col.bg:col.bgOff)+';'+
                'border-bottom:'+(isActive?'3px solid '+col.bg:'3px solid transparent')+'">'+
                _escH(prod)+'</button>';
      }});
      html += '</div>';
      html += '<div style="border:2px solid #c6d9f0;border-top:none;border-radius:0 0 6px 6px;padding:5px 10px;'+
              'background:#f7faff;margin-bottom:10px;font-size:11px;color:#555;font-style:italic">'+
              'Showing QDFs for: <b id="bm-prod-active-label">'+_escH(_bmActiveProd)+'</b></div>';
    }}

    // ── Filter variables — initialized per-product by _bmScopeByProduct() ──
    var _dlcpAllRows = [], _dlcpVals = [], _dlcpKeyMap = {{}}, _dlcpDevRevMap = {{}};
    var _progVals = [], _progLotMap = {{}};
    var _bmDlcpActive = new Set(), _bmProgActive = new Set(), _bmLotActive = new Set();
    var _matVals = [], _bmMatActive = new Set();

    function _bmDlcpCbChange(cb) {{
      if (cb.checked) {{ _bmDlcpActive.add(cb.value); }} else {{ _bmDlcpActive.delete(cb.value); }}
      _bmApplyDlcpFilter();
    }}
    window._bmDlcpCbChange = _bmDlcpCbChange;
    function _bmDlcpSelectAll(checked) {{
      document.querySelectorAll('[id^=bm-dlcp-cb-]').forEach(function(c) {{
        c.checked = checked;
        if (checked) {{ _bmDlcpActive.add(c.value); }} else {{ _bmDlcpActive.delete(c.value); }}
      }});
      _bmApplyDlcpFilter();
    }}
    window._bmDlcpSelectAll = _bmDlcpSelectAll;
    function _bmApplyDlcpFilter() {{
      passing = _dlcpAllRows.filter(function(r) {{
        var prog = r.tp_rev || '(none)';
        var lot  = r.lot || '(none)';
        var mat  = wfrMatMap[r.lot+'/'+r.wafer] || '(none)';
        return _bmDlcpActive.has(r.dlcp || '(none)') &&
               _bmProgActive.has(prog) &&
               _bmLotActive.has(prog+'\x00'+lot) &&
               _bmMatActive.has(mat);
      }});
      _bmRebuildCards();
    }}

    function _bmProgCbChange(cb) {{
      var prog = cb.value;
      if (cb.checked) {{ _bmProgActive.add(prog); }} else {{ _bmProgActive.delete(prog); }}
      // sync all lots under this prog
      (_progLotMap[prog]||[]).forEach(function(lot) {{
        var key = prog+'\x00'+lot;
        if (cb.checked) _bmLotActive.add(key); else _bmLotActive.delete(key);
        var el = document.getElementById('bm-lot-cb-'+key.replace(/[^a-zA-Z0-9]/g,'_'));
        if (el) el.checked = cb.checked;
      }});
      _bmApplyDlcpFilter();
      _bmProgUpdateLabel();
    }}
    window._bmProgCbChange = _bmProgCbChange;
    function _bmLotCbChange(cb) {{
      var key = cb.value; // "prog\x00lot"
      var sep = key.indexOf('\x00');
      var prog = sep>=0 ? key.substring(0,sep) : key;
      if (cb.checked) _bmLotActive.add(key); else _bmLotActive.delete(key);
      // update prog checkbox state
      var lots = _progLotMap[prog]||[];
      var allOn  = lots.every(function(l)  {{ return _bmLotActive.has(prog+'\x00'+l); }});
      var anyOn  = lots.some(function(l)   {{ return _bmLotActive.has(prog+'\x00'+l); }});
      var pEl = document.getElementById('bm-prog-cb-'+prog.replace(/[^a-zA-Z0-9]/g,'_'));
      if (pEl) {{ pEl.checked = allOn; pEl.indeterminate = anyOn && !allOn; }}
      if (allOn) _bmProgActive.add(prog); else if (!anyOn) _bmProgActive.delete(prog);
      _bmApplyDlcpFilter();
      _bmProgUpdateLabel();
    }}
    window._bmLotCbChange = _bmLotCbChange;
    function _bmProgSelectAll(checked) {{
      document.querySelectorAll('[id^=bm-prog-cb-]').forEach(function(c) {{
        c.checked = checked; c.indeterminate = false;
        if (checked) {{ _bmProgActive.add(c.value); }} else {{ _bmProgActive.delete(c.value); }}
      }});
      document.querySelectorAll('[id^=bm-lot-cb-]').forEach(function(c) {{
        c.checked = checked;
        if (checked) {{ _bmLotActive.add(c.value); }} else {{ _bmLotActive.delete(c.value); }}
      }});
      _bmApplyDlcpFilter();
      _bmProgUpdateLabel();
    }}
    window._bmProgSelectAll = _bmProgSelectAll;
    function _bmProgUpdateLabel() {{
      var total = _progVals.length;
      var active = _bmProgActive.size;
      var lbl = document.getElementById('bm-prog-dd-label');
      if (!lbl) return;
      if (active === 0) lbl.textContent = 'None selected';
      else if (active === total) lbl.textContent = 'All selected';
      else lbl.textContent = active + ' / ' + total + ' selected';
    }}

    html += '<div id="bm-filter-area"></div>';
    function _bmProgDdToggle() {{
      var p = document.getElementById('bm-prog-dd-panel');
      if (!p) return;
      var open = p.style.display === 'none';
      p.style.display = open ? 'block' : 'none';
      if (open) {{
        // close on outside click
        setTimeout(function() {{
          document.addEventListener('click', function _bmProgDdClose(e) {{
            if (!document.getElementById('bm-prog-filter').contains(e.target)) {{
              p.style.display = 'none';
              document.removeEventListener('click', _bmProgDdClose);
            }}
          }});
        }}, 0);
      }}
    }}
    window._bmProgDdToggle = _bmProgDdToggle;
    function _bmProgSearch(q) {{
      var s = q.toLowerCase();
      var list = document.getElementById('bm-prog-list');
      if (!list) return;
      list.querySelectorAll('[data-prog-row]').forEach(function(row) {{
        var prog = (row.getAttribute('data-prog-row')||'').toLowerCase();
        row.style.display = (!s || prog.indexOf(s) >= 0) ? '' : 'none';
      }});
    }}
    window._bmProgSearch = _bmProgSearch;

    // ── Material dropdown helpers ──────────────────────────────────────────
    function _bmMatCbChange(cb) {{
      if (cb.checked) {{ _bmMatActive.add(cb.value); }} else {{ _bmMatActive.delete(cb.value); }}
      _bmMatUpdateLabel();
      _bmApplyDlcpFilter();
    }}
    window._bmMatCbChange = _bmMatCbChange;
    function _bmMatSelectAll(checked) {{
      document.querySelectorAll('[id^=bm-mat-cb-]').forEach(function(c) {{
        c.checked = checked;
        if (checked) {{ _bmMatActive.add(c.value); }} else {{ _bmMatActive.delete(c.value); }}
      }});
      _bmMatUpdateLabel();
      _bmApplyDlcpFilter();
    }}
    window._bmMatSelectAll = _bmMatSelectAll;
    function _bmMatUpdateLabel() {{
      var total  = _matVals.length;
      var active = _bmMatActive.size;
      var lbl = document.getElementById('bm-mat-dd-label');
      if (!lbl) return;
      if (active === 0)     lbl.textContent = 'None selected';
      else if (active === total) lbl.textContent = 'All selected';
      else lbl.textContent = active + ' / ' + total + ' selected';
    }}
    window._bmMatUpdateLabel = _bmMatUpdateLabel;
    function _bmMatDdToggle() {{
      var p = document.getElementById('bm-mat-dd-panel');
      if (!p) return;
      var open = p.style.display === 'none';
      p.style.display = open ? 'block' : 'none';
      if (open) {{
        setTimeout(function() {{
          document.addEventListener('click', function _bmMatDdClose(e) {{
            var wrap = document.getElementById('bm-mat-filter');
            if (wrap && !wrap.contains(e.target)) {{
              p.style.display = 'none';
              document.removeEventListener('click', _bmMatDdClose);
            }}
          }});
        }}, 0);
      }}
    }}
    window._bmMatDdToggle = _bmMatDdToggle;

    html += '<div style="padding:8px 10px;border:1px solid #dbe7f4;border-radius:6px;background:#f8fbff;margin-bottom:10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">'+
            '<span style="font-size:11px;color:#1a4a7a;font-weight:600;margin-right:2px">Cards:</span>'+
            '<button onclick="var cc=document.querySelectorAll(\\'.bm-card-wrapper\\');for(var i=0;i<cc.length;i++){{cc[i].style.display=\\'block\\';var b=cc[i].querySelector(\\'.bm-toggle-btn\\');if(b){{b.textContent=\\'Hide\\';var bd=cc[i].querySelector(\\'.bm-card-body\\');if(bd)bd.style.display=\\'block\\'}}}}" style="padding:3px 10px;border:1px solid #98b2d2;border-radius:4px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">Show all</button>'+
            '<button onclick="var cc=document.querySelectorAll(\\'.bm-card-wrapper\\');for(var i=0;i<cc.length;i++)cc[i].style.display=\\'none\\'" style="padding:3px 10px;border:1px solid #98b2d2;border-radius:4px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">Hide all</button>'+
            '<button onclick="var cc=document.querySelectorAll(\\'.bm-card-body\\');for(var i=0;i<cc.length;i++){{cc[i].style.display=\\'none\\';var b=cc[i].closest(\\'.bm-card-wrapper\\').querySelector(\\'.bm-toggle-btn\\');if(b)b.textContent=\\'Show\\'}}" style="padding:3px 10px;border:1px solid #98b2d2;border-radius:4px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">Collapse all</button>'+
            '<button onclick="var cc=document.querySelectorAll(\\'.bm-card-body\\');for(var i=0;i<cc.length;i++){{cc[i].style.display=\\'block\\';var b=cc[i].closest(\\'.bm-card-wrapper\\').querySelector(\\'.bm-toggle-btn\\');if(b)b.textContent=\\'Hide\\'}}" style="padding:3px 10px;border:1px solid #98b2d2;border-radius:4px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">Expand all</button>'+
            '</div>';
    // ── Individual table jump buttons (uses named helper _bmShowCard) ──────
    html += '<div style="padding:8px 10px;border:1px solid #c6d9f0;border-radius:6px;background:#eef5fd;margin-bottom:10px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">';
    html += '<span style="font-size:11px;color:#1a4a7a;font-weight:600;margin-right:2px">&#9654; Show table:</span>';
    groupFns.forEach(function(grp) {{
      var safeId2 = grp.label.replace(/[^a-zA-Z0-9_-]/g,'_');
      html += '<button onclick="_bmShowCard(\\'bm-card-' + safeId2 + '\\')" style="padding:3px 10px;border:1px solid #98b2d2;border-radius:4px;background:#fff;color:#1a4a7a;font-size:11px;cursor:pointer">' + _escH(grp.label) + '</button>';
    }});
    html += '</div>';
    html += '<div id="bm-qdf-filter" style="margin-bottom:8px"></div>';
    html += '<div id="bm-cards-section" style="overflow-x:auto;border:1px solid #dbe7f4;border-radius:6px;margin-top:0">';
    html += '<div style="padding:10px;display:flex;flex-direction:column;gap:10px;">';

    bdiv.innerHTML = html + '</div></div></div>';

    // ── QDF-SSpec row visibility filter ───────────────────────────────────
    var _bmHiddenQdfs = new Set();  // sspec values hidden from display (analysis unchanged)

    function _bmBuildQdfFilter() {{
      var fDiv = document.getElementById('bm-qdf-filter');
      if (!fDiv) return;
      if (!_bmActiveQdfRows || !_bmActiveQdfRows.length) {{ fDiv.innerHTML = ''; return; }}
      var allSpecs = _bmActiveQdfRows.map(function(r){{ return (r[qdfIdCol]||'').trim(); }}).filter(Boolean);
      // Reset hidden set to empty whenever we rebuild (product switch or init)
      _bmHiddenQdfs = new Set();
      var fid = 'bm-qdf-flt-body';
      var fHtml = '<div style="border:1px solid #c6d9f0;border-radius:6px;background:#eef5fd;margin-bottom:10px">';
      fHtml += '<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;cursor:pointer;user-select:none" '+
               'onclick="_bmQdfToggle()">';
      fHtml += '<span class="bm-qf-arr" id="bm-qdf-arr" style="font-size:10px;color:#1a4a7a">&#9654;</span>';
      fHtml += '<span style="font-size:11px;color:#1a4a7a;font-weight:700">QDF-SSpec Row Filter</span>';
      fHtml += '<span style="font-size:11px;color:#555;font-style:italic">(hide rows from display — analysis unchanged)</span>';
      fHtml += '<span id="bm-qdf-flt-count" style="margin-left:auto;font-size:11px;color:#555">All '+allSpecs.length+' visible</span>';
      fHtml += '</div>';
      fHtml += '<div id="'+fid+'" style="display:none;padding:6px 12px 10px;border-top:1px solid #c6d9f0">';
      fHtml += '<div style="display:flex;gap:6px;margin-bottom:6px">';
      fHtml += '<button onclick="_bmQdfSelectAll(true)" style="padding:2px 8px;border:1px solid #98b2d2;border-radius:4px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">Show all</button>';
      fHtml += '<button onclick="_bmQdfSelectAll(false)" style="padding:2px 8px;border:1px solid #98b2d2;border-radius:4px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">Hide all</button>';
      fHtml += '</div>';
      fHtml += '<div style="display:flex;flex-wrap:wrap;gap:4px">';
      allSpecs.forEach(function(sp) {{
        var cbId = 'bm-qdf-cb-' + sp.replace(/[^a-zA-Z0-9]/g,'_');
        fHtml += '<label style="display:flex;align-items:center;gap:3px;font-size:12px;color:#1a3a5c;cursor:pointer;'+
                 'background:#fff;border:1px solid #b8d4f0;border-radius:4px;padding:2px 8px;user-select:none">';
        fHtml += '<input type="checkbox" id="'+cbId+'" value="'+_escH(sp)+'" checked '+
                 'onchange="_bmQdfCbChange(this)" style="cursor:pointer;width:12px;height:12px">';
        fHtml += '<span>'+_escH(sp)+'</span></label>';
      }});
      fHtml += '</div></div></div>';
      fDiv.innerHTML = fHtml;
    }}
    function _bmQdfToggle() {{
      var b = document.getElementById('bm-qdf-flt-body');
      var a = document.getElementById('bm-qdf-arr');
      if (!b) return;
      var open = b.style.display === 'none';
      b.style.display = open ? '' : 'none';
      if (a) a.innerHTML = open ? '&#9660;' : '&#9654;';
    }}
    window._bmQdfToggle = _bmQdfToggle;
    function _bmQdfCbChange(cb) {{
      if (cb.checked) {{ _bmHiddenQdfs.delete(cb.value); }} else {{ _bmHiddenQdfs.add(cb.value); }}
      _bmQdfUpdateCount();
      _bmRebuildCards();
    }}
    window._bmQdfCbChange = _bmQdfCbChange;
    function _bmQdfSelectAll(checked) {{
      document.querySelectorAll('[id^=bm-qdf-cb-]').forEach(function(c) {{
        c.checked = checked;
        if (checked) {{ _bmHiddenQdfs.delete(c.value); }} else {{ _bmHiddenQdfs.add(c.value); }}
      }});
      _bmQdfUpdateCount();
      _bmRebuildCards();
    }}
    window._bmQdfSelectAll = _bmQdfSelectAll;
    function _bmQdfUpdateCount() {{
      var total = _bmActiveQdfRows ? _bmActiveQdfRows.length : 0;
      var hidden = _bmHiddenQdfs.size;
      var cnt = document.getElementById('bm-qdf-flt-count');
      if (!cnt) return;
      if (hidden === 0) cnt.textContent = 'All ' + total + ' visible';
      else cnt.textContent = (total - hidden) + ' / ' + total + ' visible  (' + hidden + ' hidden)';
    }}
    window._bmQdfUpdateCount = _bmQdfUpdateCount;

    _bmScopeByProduct();  // scopes data to active product, builds filters + cards
    function _bmSetProdTab(pi) {{
      _bmActiveProd = _bmProductsList[pi];
      _bmActiveQdfRows = _bmProdQdfMap[_bmActiveProd] || [];
      var _bmProdTabColors = [
        {{ bg: '#1e40af', bgOff: '#dbeafe', textOff: '#1e3a8a' }},
        {{ bg: '#5b21b6', bgOff: '#ede9fe', textOff: '#4c1d95' }},
        {{ bg: '#065f46', bgOff: '#dcfce7', textOff: '#14532d' }},
        {{ bg: '#164e63', bgOff: '#cffafe', textOff: '#155e75' }},
        {{ bg: '#374151', bgOff: '#e5e7eb', textOff: '#1f2937' }},
      ];
      _bmProductsList.forEach(function(p, i) {{
        var btn = document.getElementById('bm-prod-tab-btn-' + i);
        if (!btn) return;
        var col = _bmProdTabColors[i % _bmProdTabColors.length];
        var isActive = i === pi;
        btn.style.background   = isActive ? col.bg : col.bgOff;
        btn.style.color        = isActive ? '#fff' : col.textOff;
        btn.style.fontWeight   = isActive ? '700' : '500';
        btn.style.borderBottom = isActive ? ('3px solid ' + col.bg) : '3px solid transparent';
      }});
      var lbl = document.getElementById('bm-prod-active-label');
      if (lbl) lbl.innerHTML = _escH(_bmActiveProd);
      _bmScopeByProduct();  // rescopes data to new product, rebuilds filters + cards
    }}
    window._bmSetProdTab = _bmSetProdTab;

    function _bmScopeByProduct() {{
      // Build set of sspecs for the active product
      var _activeProdSpecSet = new Set();
      (_bmActiveQdfRows || []).forEach(function(r) {{
        var sp = (r[qdfIdCol] || '').trim();
        if (sp) _activeProdSpecSet.add(sp);
      }});
      // A wafer belongs to this product if >= 1 die has pas_qdf matching an active sspec
      var _activeProdWfrSet = new Set();
      _allBmRows.forEach(function(r) {{
        var kw = r.lot + '/' + r.wafer;
        if (_activeProdWfrSet.has(kw)) return;
        var qdfs = (r.pas_qdf || '').split('^');
        for (var qi = 0; qi < qdfs.length; qi++) {{
          if (_activeProdSpecSet.has(qdfs[qi].trim())) {{ _activeProdWfrSet.add(kw); break; }}
        }}
      }});
      // Scope rows — include all when only one product exists
      _dlcpAllRows = (_bmProductsList.length > 1)
        ? _allBmRows.filter(function(r) {{ return _activeProdWfrSet.has(r.lot + '/' + r.wafer); }})
        : _allBmRows.slice();
      passing = _dlcpAllRows.slice();
      // Update IBIN1 count display
      var _iCntEl = document.getElementById('bm-ibin1-cnt');
      if (_iCntEl) _iCntEl.textContent = _dlcpAllRows.length.toLocaleString();
      // Recompute DLCP values
      _dlcpVals = []; _dlcpKeyMap = {{}}; _dlcpDevRevMap = {{}};
      _dlcpAllRows.forEach(function(r) {{
        var lbl = r.dlcp || '(none)';
        if (_dlcpVals.indexOf(lbl) < 0) {{ _dlcpVals.push(lbl); _dlcpKeyMap[lbl] = {{}}; _dlcpDevRevMap[lbl] = {{}}; }}
        if (r.dlcp_key) _dlcpKeyMap[lbl][r.dlcp_key] = 1;
        if (r.dev_rev)  _dlcpDevRevMap[lbl][r.dev_rev] = 1;
      }});
      _dlcpVals.sort();
      _bmDlcpActive = new Set(_dlcpVals);
      // Recompute Prog + Lot values (keyed by tp_rev to match "By TP Rev" volumes exactly)
      _progVals = []; _progLotMap = {{}};
      _dlcpAllRows.forEach(function(r) {{
        var prog = r.tp_rev || '(none)';
        var lot  = r.lot || '(none)';
        if (_progVals.indexOf(prog) < 0) _progVals.push(prog);
        if (!_progLotMap[prog]) _progLotMap[prog] = [];
        if (_progLotMap[prog].indexOf(lot) < 0) _progLotMap[prog].push(lot);
      }});
      _progVals.sort();
      Object.keys(_progLotMap).forEach(function(p) {{ _progLotMap[p].sort(); }});
      _bmProgActive = new Set(_progVals);
      _bmLotActive = new Set();
      _progVals.forEach(function(p) {{
        (_progLotMap[p]||[]).forEach(function(l) {{ _bmLotActive.add(p+'\x00'+l); }});
      }});
      // Recompute Material values
      _matVals = [];
      _dlcpAllRows.forEach(function(r) {{
        var mat = wfrMatMap[r.lot+'/'+r.wafer] || '(none)';
        if (_matVals.indexOf(mat) < 0) _matVals.push(mat);
      }});
      _matVals.sort();
      _bmMatActive = new Set(_matVals);
      // Rebuild filter controls, QDF filter, and analysis cards
      _bmRebuildFilterHtml();
      _bmBuildQdfFilter();
      _bmRebuildCards();
    }}
    window._bmScopeByProduct = _bmScopeByProduct;

    function _bmRebuildFilterHtml() {{
      var fArea = document.getElementById('bm-filter-area');
      if (!fArea) return;
      var fHtml = '';
      // DLCP filter
      if (_dlcpVals.length > 0) {{
        fHtml += '<div id="bm-dlcp-filter" style="padding:7px 12px;border:1px solid #b8d4f0;border-radius:6px;background:#eef5fd;margin-bottom:10px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">';
        fHtml += '<span style="font-size:11px;color:#1a4a7a;font-weight:700;margin-right:4px">&#9660; DLCP:</span>';
        _dlcpVals.forEach(function(lbl) {{
          var keys    = Object.keys(_dlcpKeyMap[lbl]  || {{}}).join('/');
          var devrevs = Object.keys(_dlcpDevRevMap[lbl] || {{}}).join(', ');
          var dispLbl = lbl
            + (keys    ? '  [' + keys + ']'    : '')
            + (devrevs ? '  ' + devrevs        : '');
          var cbId = 'bm-dlcp-cb-' + lbl.replace(/[^a-zA-Z0-9]/g,'_');
          fHtml += '<label style="display:flex;align-items:center;gap:4px;font-size:12px;color:#1a3a5c;cursor:pointer;background:#fff;border:1px solid #b8d4f0;border-radius:4px;padding:2px 8px;user-select:none">'+
                  '<input type="checkbox" id="'+cbId+'" value="'+_escH(lbl)+'" checked '+
                  'onchange="_bmDlcpCbChange(this)" style="cursor:pointer;width:13px;height:13px">'+
                  '<span>'+_escH(dispLbl)+'</span></label>';
        }});
        fHtml += '<button onclick="_bmDlcpSelectAll(true)" '+
                'style="padding:2px 8px;border:1px solid #98b2d2;border-radius:4px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">All</button>';
        fHtml += '<button onclick="_bmDlcpSelectAll(false)" '+
                'style="padding:2px 8px;border:1px solid #98b2d2;border-radius:4px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">None</button>';
        fHtml += '</div>';
      }}
      // Prog filter
      if (_progVals.length > 0) {{
        fHtml += '<div id="bm-prog-filter" style="padding:7px 12px;border:1px solid #b8d4f0;border-radius:6px;background:#eef5fd;margin-bottom:10px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;position:relative">';
        fHtml += '<span style="font-size:11px;color:#1a4a7a;font-weight:700;margin-right:4px">TP Rev (Class Prog):</span>';
        fHtml += '<div style="position:relative;display:inline-block">';
        fHtml += '<button id="bm-prog-dd-btn" onclick="_bmProgDdToggle()" '+
                'style="padding:3px 24px 3px 10px;border:1px solid #98b2d2;border-radius:4px;background:#fff;color:#1a3a5c;font-size:12px;cursor:pointer;min-width:140px;text-align:left;position:relative">'+
                '<span id="bm-prog-dd-label">All selected</span>'+
                '<span style="position:absolute;right:7px;top:50%;transform:translateY(-50%);font-size:10px">&#9660;</span>'+
                '</button>';
        fHtml += '<div id="bm-prog-dd-panel" style="display:none;position:absolute;left:0;top:100%;z-index:200;background:#fff;border:1px solid #98b2d2;border-radius:4px;box-shadow:0 3px 10px rgba(0,0,0,0.15);min-width:200px;padding:6px 0">';
        fHtml += '<div style="padding:4px 8px;border-bottom:1px solid #e0eaf5">';
        fHtml += '<input id="bm-prog-search" type="text" placeholder="Search prog..." oninput="_bmProgSearch(this.value)" style="width:100%;box-sizing:border-box;padding:3px 7px;border:1px solid #b8d0ea;border-radius:3px;font-size:11px;color:#1a3a5c;outline:none">';
        fHtml += '</div>';
        fHtml += '<div style="display:flex;gap:4px;padding:4px 8px;border-bottom:1px solid #e0eaf5;margin-bottom:4px">';
        fHtml += '<button onclick="_bmProgSelectAll(true)" style="flex:1;padding:2px 6px;border:1px solid #98b2d2;border-radius:3px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">All</button>';
        fHtml += '<button onclick="_bmProgSelectAll(false)" style="flex:1;padding:2px 6px;border:1px solid #98b2d2;border-radius:3px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">None</button>';
        fHtml += '</div>';
        fHtml += '<div id="bm-prog-list">';
        _progVals.forEach(function(prog) {{
          var cbId = 'bm-prog-cb-' + prog.replace(/[^a-zA-Z0-9]/g,'_');
          var lots = _progLotMap[prog]||[];
          var lotGrpId = 'bm-lot-grp-' + prog.replace(/[^a-zA-Z0-9]/g,'_');
          fHtml += '<div data-prog-row="'+_escH(prog)+'">';
          fHtml += '<div style="background:#f0f4f8;padding:4px 8px;border-bottom:1px solid #e8edf5;display:flex;align-items:center;gap:4px">';
          fHtml += '<label style="display:flex;align-items:center;gap:6px;font-size:12px;font-weight:700;color:#1a3a5c;cursor:pointer;white-space:nowrap;flex:1">';
          fHtml += '<input type="checkbox" id="'+cbId+'" value="'+_escH(prog)+'" checked '+
                  'onchange="_bmProgCbChange(this)" style="cursor:pointer;width:13px;height:13px">';
          fHtml += '<span>'+_escH(prog)+'</span></label>';
          if (lots.length > 0) {{
            fHtml += '<button onclick="var g=document.getElementById(\\''+lotGrpId+'\\');var open=g.style.display===\\'none\\';g.style.display=open?\\'block\\':\\'none\\';this.textContent=open?\\'&#9660;\\':\\'&#9654;\\'" '+
                    'style="border:none;background:none;cursor:pointer;color:#555;font-size:10px;padding:0 4px" title="Show/hide lots">&#9654;</button>';
          }}
          fHtml += '</div>';
          if (lots.length > 0) {{
            fHtml += '<div id="'+lotGrpId+'" style="display:none">';
            lots.forEach(function(lot) {{
              var lotKey = prog+'\x00'+lot;
              var lotCbId = 'bm-lot-cb-' + lotKey.replace(/[^a-zA-Z0-9]/g,'_');
              fHtml += '<label style="display:flex;align-items:center;gap:6px;padding:3px 12px 3px 28px;font-size:11px;color:#2c4a6e;cursor:pointer;white-space:nowrap;border-bottom:1px solid #f0f4f8">';
              fHtml += '<input type="checkbox" id="'+lotCbId+'" value="'+_escH(lotKey)+'" checked '+
                      'onchange="_bmLotCbChange(this)" style="cursor:pointer;width:12px;height:12px">';
              fHtml += '<span>'+_escH(lot)+'</span></label>';
            }});
            fHtml += '</div>';
          }}
          fHtml += '</div>';  // close data-prog-row
        }});
        fHtml += '</div></div></div>';  // close: bm-prog-list, dd-panel, inner wrapper
        fHtml += '</div>';  // close: bm-prog-filter
      }}
      // Material filter
      if (_matVals.length > 0) {{
        fHtml += '<div id="bm-mat-filter" style="padding:7px 12px;border:1px solid #b8d4f0;border-radius:6px;background:#eef5fd;margin-bottom:10px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;position:relative">';
        fHtml += '<span style="font-size:11px;color:#1a4a7a;font-weight:700;margin-right:4px">Material:</span>';
        fHtml += '<div style="position:relative;display:inline-block">';
        fHtml += '<button id="bm-mat-dd-btn" onclick="_bmMatDdToggle()" '+
                'style="padding:3px 24px 3px 10px;border:1px solid #98b2d2;border-radius:4px;background:#fff;color:#1a3a5c;font-size:12px;cursor:pointer;min-width:140px;text-align:left;position:relative">'+
                '<span id="bm-mat-dd-label">All selected</span>'+
                '<span style="position:absolute;right:7px;top:50%;transform:translateY(-50%);font-size:10px">&#9660;</span>'+
                '</button>';
        fHtml += '<div id="bm-mat-dd-panel" style="display:none;position:absolute;left:0;top:100%;z-index:200;background:#fff;border:1px solid #98b2d2;border-radius:4px;box-shadow:0 3px 10px rgba(0,0,0,0.15);min-width:180px;padding:6px 0">';
        fHtml += '<div style="display:flex;gap:4px;padding:4px 8px;border-bottom:1px solid #e0eaf5;margin-bottom:4px">';
        fHtml += '<button onclick="_bmMatSelectAll(true)" style="flex:1;padding:2px 6px;border:1px solid #98b2d2;border-radius:3px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">All</button>';
        fHtml += '<button onclick="_bmMatSelectAll(false)" style="flex:1;padding:2px 6px;border:1px solid #98b2d2;border-radius:3px;background:#e8f1fb;color:#1a4a7a;font-size:11px;cursor:pointer">None</button>';
        fHtml += '</div>';
        fHtml += '<div id="bm-mat-list">';
        _matVals.forEach(function(mat) {{
          var cbId = 'bm-mat-cb-' + mat.replace(/[^a-zA-Z0-9]/g,'_');
          fHtml += '<label style="display:flex;align-items:center;gap:6px;padding:4px 12px;font-size:12px;color:#1a3a5c;cursor:pointer;white-space:nowrap;border-bottom:1px solid #f0f4f8">'+
                  '<input type="checkbox" id="'+cbId+'" value="'+_escH(mat)+'" checked '+
                  'onchange="_bmMatCbChange(this)" style="cursor:pointer;width:13px;height:13px">'+
                  '<span>'+_escH(mat)+'</span></label>';
        }});
        fHtml += '</div></div></div>';
        fHtml += '</div>';  // close bm-mat-filter
      }}
      fArea.innerHTML = fHtml;
    }}
    window._bmRebuildFilterHtml = _bmRebuildFilterHtml;

    function _bmRebuildCards() {{
      var cardsDiv = document.getElementById('bm-cards-section');
      if (!cardsDiv) return;
      var cHtml = '<div style="padding:10px;display:flex;flex-direction:column;gap:10px;">';
      groupFns.forEach(function(grp, gIdx) {{
        var groups = {{}};
        for (var i = 0; i < passing.length; i++) {{
          var r = passing[i];
          var key = grp.fn(r);
          (groups[key] = groups[key] || []).push(r);
        }}
        var gVals = Object.keys(groups).sort();
        var gStats = {{}};
        for (var i = 0; i < gVals.length; i++) {{
          var gv = gVals[i];
          var grows = groups[gv];
          var spds = grows.map(function(o){{return o.speed_pct;}}).filter(function(v){{return v!==null && !isNaN(v);}}).sort(function(a,b){{return a-b;}});
          var medSpd = NaN;
          if (spds.length) {{
            var m = Math.floor(spds.length/2);
            medSpd = spds.length % 2 ? spds[m] : (spds[m-1]+spds[m])/2;
          }}
          gStats[gv] = {{vol: grows.length, medSpd: medSpd, rows: grows}};
        }}

        var safeId = grp.label.replace(/[^a-zA-Z0-9_-]/g,'_');
        var _cardDisplay = (gIdx === 0) ? 'block' : 'none';
        cHtml += '<div id="bm-card-'+safeId+'" class="bm-card-wrapper" style="display:'+_cardDisplay+';width:100%;min-width:400px;min-height:80px;overflow:auto;resize:both;box-sizing:border-box;border:1px solid #c9d7e8;border-radius:6px;" >';
        cHtml += '<div class="flow-card" style="width:100%;display:flex;flex-direction:column;background:#fff;border:1px solid #c9d7e8;border-radius:6px;overflow:visible">';
        cHtml += '<div style="background:#1a4a7a;color:#fff;padding:8px 12px;font-weight:bold;font-size:13px;display:flex;align-items:center;justify-content:space-between;gap:10px">'
           + '<span>'+_escH(grp.label)+'</span>'
           + '<span style="display:flex;align-items:center;gap:6px">'
           + '<button class="bm-toggle-btn" onclick="var w=this.closest(\\'.bm-card-wrapper\\');var bd=w.querySelector(\\'.bm-card-body\\');if(bd.style.display===\\'none\\'){{bd.style.display=\\'block\\';this.textContent=\\'Hide\\'}}else{{bd.style.display=\\'none\\';this.textContent=\\'Show\\'}}" style="padding:2px 8px;border:1px solid rgba(255,255,255,0.6);border-radius:4px;background:rgba(255,255,255,0.15);color:#fff;font-size:11px;cursor:pointer">Hide</button>'
           + '<button onclick="this.closest(\\'.bm-card-wrapper\\').style.display=\\'none\\'" style="padding:2px 8px;border:1px solid rgba(255,255,255,0.6);border-radius:4px;background:rgba(255,100,100,0.25);color:#fff;font-size:11px;cursor:pointer">&times;</button>'
           + '</span></div>';

        cHtml += '<div class="bm-card-body" style="display:block;overflow:auto;">';
        cHtml += '<div style="padding:12px;overflow-x:auto;overflow-y:auto;">';
        cHtml += '<div style="overflow-x:auto;border:1px solid #d1d5db;border-radius:6px;background:#fff;"><table style="table-layout:auto;border-collapse:collapse;font-size:16px;text-align:left;"><thead><tr>';

        for (var i=0; i<specCols.length; i++) {{
          var _thSty = (i<2) ? 'width:1px;white-space:nowrap;' : 'width:55px;white-space:normal;word-break:break-word;overflow:hidden;';
          var _thFsz = '20px';
          cHtml += '<th title="'+_escH(specCols[i])+'" style="position:sticky;top:0;background:#2563a8;color:#fff;font-size:'+_thFsz+';padding:8px 6px;border:1px solid #1a4a7a;cursor:help;vertical-align:bottom;'+_thSty+'">'+_escH(specCols[i])+'</th>';
        }}
        for (var i=0; i<gVals.length; i++) {{
          var gv = gVals[i];
          var st = gStats[gv];
          cHtml += '<th title="'+_escH(gv)+'&#10;Med UPM%: '+_pct(_groupMedUpm(st.rows))+'&#10;Vol: '+st.vol+'" style="position:sticky;top:0;background:#1abc9c;color:#fff;font-size:20px;padding:5px 6px;border:1px solid #16a085;width:180px;white-space:normal;word-break:break-word;cursor:help;">'+
                  _escH(gv)+'<br/>Med UPM%: '+_pct(_groupMedUpm(st.rows))+'<br/>Vol: '+st.vol+'</th>';
        }}
        cHtml += '</tr></thead><tbody>';

        var _visQdfRows = _bmActiveQdfRows.filter(function(r){{ return !_bmHiddenQdfs.has((r[qdfIdCol]||'').trim()); }});
        _visQdfRows.forEach(function(qRow, ri) {{
          var sspec = (qRow[qdfIdCol] || '').trim();
          cHtml += '<tr style="background:'+(ri%2===0?'#f9fafb':'#fff')+';">';
          for (var i=0; i<specCols.length; i++) {{
            var _tdSty = (i<2) ? 'width:1px;white-space:nowrap;' : 'width:55px;white-space:normal;word-break:break-word;overflow:hidden;';
            var _tdFsz = (i===1) ? '14px' : '20px';
            cHtml += '<td style="padding:3px 8px;border:1px solid #b8d0ea;background:#dceefb;color:#1a3a5c;font-size:'+_tdFsz+';'+_tdSty+'">'+_escH(String(qRow[specCols[i]]||''))+'</td>';
          }}
          for (var i=0; i<gVals.length; i++) {{
            var gv = gVals[i];
            var st = gStats[gv];
            var cnt = 0;
            for (var k=0; k<st.rows.length; k++) {{
              var sq = (st.rows[k].pas_qdf || '').split('^');
              for (var x=0; x<sq.length; x++) if(sq[x].trim() === sspec.trim()) {{ cnt++; break; }}
            }}
            var p_pct = st.vol>0 ? (cnt/st.vol*100) : NaN;
            var bgcls = isNaN(p_pct)?'':( p_pct>=50?'background:#d4f5e2':'background:#fde8e8');
            var txtcls = isNaN(p_pct)?'':(p_pct>=50?'color:#1a7a2f;font-weight:600':'color:#c0392b;');
            cHtml += '<td style="padding:3px 5px;border:1px solid #e5e7eb;font-size:20px;'+txtcls+';'+bgcls+'">'+
                    (isNaN(p_pct)?'–':_f1(p_pct)+'%')+'<br/><span style="font-size:18px;color:#888;font-weight:normal;">'+cnt+'/'+st.vol+'</span></td>';
          }}
          cHtml += '</tr>';
        }});
        cHtml += '</tbody></table></div>';
        cHtml += '</div></div></div></div></div>';
      }});
      cHtml += '</div>';
      cardsDiv.innerHTML = cHtml;
      cardsDiv.querySelectorAll('table').forEach(function(t){{
        // skip tables inside hidden cards — they will be initialized on first reveal
        var _wrapper = t.closest('.bm-card-wrapper');
        if (_wrapper && _wrapper.style.display === 'none') return;
        var ths = t.querySelectorAll('thead th');
        if (ths.length > 1) {{
          var col2W = ths[1].offsetWidth;
          var targetW = Math.round(col2W * 0.75) + 'px';
          for (var ci=2; ci<ths.length; ci++) {{ ths[ci].style.width = targetW; }}
          t.querySelectorAll('tbody tr').forEach(function(tr) {{
            for (var ci=2; ci<tr.cells.length; ci++) {{ tr.cells[ci].style.width = targetW; }}
          }});
        }}
        _makeColsResizable(t); _addTableHover(t);
      }});
    }}
    _bmRebuildCards();
}}
</script>
<!-- Param detail modal -->
<div id="pm-overlay" class="pm-overlay" onclick="if(event.target===this)_closeParamModal()">
<div class="pm-card">
<div class="pm-hdr"><span class="pm-hdr-title" id="pm-title"></span>
<button class="pm-close" onclick="_closeParamModal()" title="Close (Esc)">&times;</button></div>
<div class="pm-body" id="pm-body"></div>
</div></div>
<!-- Speed Flow detail modal -->
<div id="fm-overlay" class="pm-overlay" style="display:none">
<div class="pm-card fm-card-r">
<div class="pm-hdr"><span class="pm-hdr-title" id="fm-title"></span>
<button class="pm-close" onclick="_closeFlowModal()" title="Close (Esc)">&times;</button></div>
<div class="pm-body" id="fm-body"></div>
</div></div>
</body>
</html>"""
    return html, data_js


_TH_ST = "background:#34495e;color:#ecf0f1;padding:3px 7px;text-align:left;white-space:nowrap"


