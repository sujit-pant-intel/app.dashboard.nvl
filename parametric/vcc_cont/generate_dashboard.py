#!/usr/bin/env python3
"""
VccCont BIN8 Dashboard v2
Approach: track BIN8 failures by Functional Bin + Kill Test (from SETBIN column).
SETBIN format: DATA_BIN|KILL_TEST_FULL_NAME|FLAGS
"""
import argparse, json, os, re, sys
import pandas as pd
from collections import defaultdict


def _pick_prog_dir(default_path: str) -> str:
    """Return test program directory: use default if accessible, else prompt user.
    Tries tkinter folder dialog first; falls back to plain input() if unavailable."""
    if os.path.isdir(default_path):
        return default_path
    print(f'[prog] Default program path not found:\n  {default_path}')
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        messagebox.showinfo(
            'VccCont BIN8 Dashboard',
            'Select the root test program directory\n'
            '(e.g. NCXSDJXL0H61C002620)',
            parent=root,
        )
        # Start the browser at the closest existing ancestor of the default path
        _init = default_path
        while _init and not os.path.isdir(_init):
            _init = os.path.dirname(_init)
        chosen = filedialog.askdirectory(
            title='Select test program root directory',
            initialdir=_init or os.path.expanduser('~'),
            mustexist=True,
            parent=root,
        )
        root.destroy()
        if not chosen:
            print('[prog] No directory selected — limits will be empty.')
            return ''
        return chosen
    except Exception as _e:
        print(f'[prog] tkinter unavailable ({_e}), falling back to text input.')
        chosen = input('Enter full path to test program root directory: ').strip().strip('"')
        return chosen if os.path.isdir(chosen) else ''

def _show_run_options_dialog(n_wafers: int, saved: dict) -> dict:
    """Show a tkinter dialog letting the user configure run options.
    Returns a dict with keys: focus_mode (bool), focus_wafers (int).
    Falls back to auto-threshold silently if tkinter is unavailable."""
    _def_thr = int(saved.get('focus_wafers', 50))
    _auto = n_wafers <= _def_thr
    try:
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
        root.title('VccCont BIN8 — Run Options')
        root.resizable(False, False)
        root.attributes('-topmost', True)
        # Centre the window
        root.update_idletasks()
        w, h = 420, 210
        x = (root.winfo_screenwidth()  - w) // 2
        y = (root.winfo_screenheight() - h) // 2
        root.geometry(f'{w}x{h}+{x}+{y}')

        result = {'focus_mode': _auto, 'focus_wafers': _def_thr}

        tk.Label(root, text=f'Detected: {n_wafers} wafer(s) in CSV',
                 font=('Segoe UI', 10, 'bold')).pack(pady=(14, 2))
        tk.Label(root, text='Auto-enable threshold for Live Mode (wafers):',
                 font=('Segoe UI', 9)).pack()
        thr_var = tk.IntVar(value=_def_thr)
        thr_sb  = tk.Spinbox(root, from_=1, to=9999, textvariable=thr_var, width=8,
                              font=('Segoe UI', 9))
        thr_sb.pack(pady=(2, 8))

        chk_var = tk.BooleanVar(value=_auto)
        chk = tk.Checkbutton(
            root,
            text='\u26a1 Embed raw data for interactive pin inspect (Live Mode)',
            variable=chk_var, font=('Segoe UI', 9, 'bold'),
            fg='#1a7a1a' if _auto else '#555555',
        )
        chk.pack()
        note_text = '(auto-enabled: wafers \u2264 threshold)' if _auto else '(wafers > threshold — check to force embed)'
        note_lbl = tk.Label(root, text=note_text, font=('Segoe UI', 8), fg='#666666')
        note_lbl.pack(pady=(1, 10))

        def _update_note(*_):
            thr = thr_var.get() if thr_var.get() else _def_thr
            auto_now = n_wafers <= thr
            chk_var.set(auto_now)
            note_lbl.config(
                text='(auto-enabled: wafers \u2264 threshold)' if auto_now
                     else '(wafers > threshold — check to force embed)')
        thr_var.trace_add('write', _update_note)

        def _ok():
            result['focus_mode']   = chk_var.get()
            result['focus_wafers'] = max(1, thr_var.get() if thr_var.get() else _def_thr)
            root.destroy()
        def _cancel():
            result['focus_mode']   = _auto
            result['focus_wafers'] = _def_thr
            root.destroy()

        btn_frame = tk.Frame(root)
        btn_frame.pack()
        tk.Button(btn_frame, text='OK',     width=10, command=_ok,     font=('Segoe UI', 9)).pack(side=tk.LEFT,  padx=6)
        tk.Button(btn_frame, text='Cancel', width=10, command=_cancel, font=('Segoe UI', 9)).pack(side=tk.RIGHT, padx=6)
        root.protocol('WM_DELETE_WINDOW', _cancel)
        root.mainloop()
        return result
    except Exception as _e:
        print(f'[focus] tkinter unavailable ({_e}) — using auto-threshold ({_def_thr} wafers).')
        return {'focus_mode': _auto, 'focus_wafers': _def_thr}


# Resolve wafer_tools: try relative path, then scan candidate roots across drives/folders
_WT_REL  = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          '..', '..', '..', 'app.yield.nvl', 'code', 'utilities', 'wafer_tools'))
def _find_wafer_tools():
    if os.path.isdir(os.path.join(_WT_REL, 'wafer_map')):
        return _WT_REL
    _ayn_tail = os.path.join('app.yield.nvl', 'code', 'utilities', 'wafer_tools')
    # candidate roots: env-derived + common drive letters + sibling of this file's drive root
    _roots = []
    for _ev in ('SCRIPTS_ROOT', 'TOOLS_ROOT'):
        _v = os.environ.get(_ev)
        if _v: _roots.append(_v)
    for _drv in ('C', 'D', 'E', 'Y'):
        _roots += [_drv + r':\scripts', _drv + r':\tools\scripts']
    # also try the same scripts folder this repo lives in, regardless of drive
    _roots.append(os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 '..', '..', '..')))
    for _r in dict.fromkeys(_roots):  # preserve order, deduplicate
        _cand = os.path.join(_r, _ayn_tail)
        if os.path.isdir(os.path.join(_cand, 'wafer_map')):
            return _cand
    return os.path.join(_roots[0] if _roots else '', _ayn_tail)  # best-guess fallback
_WP_DIR  = _find_wafer_tools()

_AYN_ROOT  = os.path.dirname(os.path.dirname(_WP_DIR))  # …/app.yield.nvl
_TRACE_DIR = os.path.join(_AYN_ROOT, 'utilities', 'trace')

if _WP_DIR not in sys.path:
    sys.path.insert(0, _WP_DIR)
from wafer_pattern_analysis import score_wafer, WaferPattern, WpaHtmlBuilder
from wafer_pattern_analysis._wpa_js import WPA_SCORE_JS
from wafer_map import WAFERMAP_JS
from wafer_analysis_parametric.reticle import load_reticle_map

# Local Plotly — shared/library/ in app.dashboard.nvl (3 levels up from src/)
_PLOTLY_LOCAL = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              '..', '..',
                                              'shared', 'library', 'plotly-2.32.0.min.js'))
if not os.path.isfile(_PLOTLY_LOCAL):
    raise FileNotFoundError(f'plotly-2.32.0.min.js not found at {_PLOTLY_LOCAL}')
_pjs  = open(_PLOTLY_LOCAL, encoding='utf-8').read()
# Escape </ so HTML parser never sees </script inside the block (standard technique)
_pjs_safe = _pjs.replace('</', r'<\/')
_PLOTLY_TAG = f'<script>{_pjs_safe}</script>'

_DEFAULT_CSV  = (r'C:\scripts\app.yield.nvl\docs\issue_tracker\parametric'
                 r'\vcc_cont_bin8\data'
                 r'\61A-61B-Yield.CSV')
# Canonical limit source — can be overridden with --prog at runtime.
_DEFAULT_PROG = r'I:\program\1001\prod\hdmtprogs\nvl_ncx_sds\NCXSDJXL0H61C002620'
# These are set dynamically in main() once the program path is resolved.
PROG_61C      = _DEFAULT_PROG
_JSON_DIR_61C = os.path.join(PROG_61C, 'Modules', 'TPI_VCC', 'InputFiles')
_DEFAULT_JSON = os.path.join(_JSON_DIR_61C, 'VCC_SDS_VSIM_START.json')
_DEFAULT_OUT  = (r'C:\scripts\app.yield.nvl\docs\issue_tracker\parametric'
                 r'\vcc_cont_bin8\output\vcccont-bin8-analysis.html')

IB_COL   = 'INTERFACE_BIN_119325'
FB_COL   = 'FUNCTIONAL_BIN_119325'
DB_COL   = 'DATA_BIN_119325'
SETBIN   = 'TPI_BIN::CTRL_UB_X_K_BIN_X_X_X_X_SETBIN_119325'
LOT_COL  = 'Lot_119325'
PROG_COL = 'Program Name_119325'
WFR_COL  = 'SORT_WAFER'
X_COL    = 'SORT_X'
Y_COL    = 'SORT_Y'
DEVREVSTEP_COL = 'DevRevStep_119325'
TARGET_IBIN  = 8          # primary BIN for limits / kill analysis
TARGET_IBINS = [8, 80, 89]  # all failure IBs collected in dashboard

# Reticle collateral folder (shared/reticle/ in app.dashboard.nvl — 3 levels up from src/)
_RETICLE_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', '..', 'shared', 'reticle'))

# Material collateral folder (shared/material/ in app.dashboard.nvl — 3 levels up from src/)
_MATERIAL_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', '..', 'shared', 'material'))


# Columns to merge from material lookup — mirrors add_material_type.py
_MATERIAL_MERGE_COLS = ['Material Type, Skew, BEOL Skew', 'Material Type']

def _merge_material(df: 'pd.DataFrame', material_dir: str) -> 'pd.DataFrame':
    """Merge material columns from shared/material/ lot-definition CSVs.
    Join: LOT7 (first 7 chars of Lot column) + SORT_WAFER == INTEL_LOT7 + WaferID.
    Adds 'Material Type, Skew, BEOL Skew' and 'Material Type' columns;
    rows with no match get empty string."""
    if not os.path.isdir(material_dir):
        print(f'  [material] directory not found: {material_dir}')
        return df
    if LOT_COL not in df.columns or WFR_COL not in df.columns:
        print('  [material] lot/wafer columns missing — skipping')
        return df
    mat_frames = []
    for fname in sorted(os.listdir(material_dir)):
        if not fname.lower().endswith('.csv'):
            continue
        fpath = os.path.join(material_dir, fname)
        try:
            mat_frames.append(pd.read_csv(fpath, low_memory=False))
        except Exception as _e:
            print(f'  [material] skip {fname}: {_e}')
    if not mat_frames:
        print('  [material] no CSV files found')
        return df
    mat_all = pd.concat(mat_frames, ignore_index=True)
    _lot7_col = next((c for c in mat_all.columns if c.upper() == 'INTEL_LOT7'), None)
    _wfr_col  = next((c for c in mat_all.columns if c.upper() == 'WAFERID'), None)
    if not _lot7_col or not _wfr_col:
        print(f'  [material] key columns not found (lot7={_lot7_col}, wfr={_wfr_col})')
        return df
    # Find which merge columns are available in this combined frame
    _avail_merge = [c for c in _MATERIAL_MERGE_COLS if c in mat_all.columns]
    if not _avail_merge:
        # Fallback: any column with 'material' in the name
        _avail_merge = [c for c in mat_all.columns if 'material' in c.lower()]
    if not _avail_merge:
        print('  [material] no material columns found in lookup files')
        return df
    # Build per-column lookups: (lot7, wafer_int) -> value
    _lookup_cols = [_lot7_col, _wfr_col] + _avail_merge
    mat_sub = mat_all[[c for c in _lookup_cols if c in mat_all.columns]].copy()
    lookups: dict = {col: {} for col in _avail_merge}
    for _, _mrow in mat_sub.iterrows():
        _lot7 = str(_mrow[_lot7_col]).strip()
        try:
            _wid = int(_mrow[_wfr_col])
        except (ValueError, TypeError):
            continue
        for col in _avail_merge:
            if col in _mrow.index and pd.notna(_mrow[col]):
                val = str(_mrow[col]).strip()
                if val:
                    lookups[col][(_lot7, _wid)] = val
    if not any(lookups.values()):
        print('  [material] no valid entries in lookup')
        return df
    lot7_ser = df[LOT_COL].astype(str).str[:7]
    # WAFER2 = last 2 chars of SORT_WAFER as integer (e.g. 501 → "01" → 1)
    wfr_ser  = pd.to_numeric(df[WFR_COL].astype(str).str[-2:], errors='coerce').fillna(0).astype(int)
    df = df.copy()
    for col in _avail_merge:
        df[col] = [
            lookups[col].get((lot7, wfr), '')
            for lot7, wfr in zip(lot7_ser, wfr_ser)
        ]
    # Primary display column is first available merge col
    _primary = _avail_merge[0]
    n_filled = (df[_primary] != '').sum()
    print(f'  [material] merged: {n_filled}/{len(df)} rows filled ({len(lookups[_primary])} lookup entries) [{_primary}]')
    return df


def parse_setbin(v):
    if pd.isna(v): return None, None
    p = str(v).split('|')
    return (p[1].strip() if len(p) > 1 else None), (p[2].strip() if len(p) > 2 else None)

def phase_of(s):
    """Map a kill-test or column name to its flow-phase label.
    Flow order (matching vcccont_mimcap): Pre-Surge → Post-Surge → Stress → SDS-Final → SDT-Start → SDT-Final.
    """
    if not s: return 'OTHER'
    # Condition-based: more specific first
    if 'PRESURGE' in s:   return 'Pre-Surge'
    if 'SDTFINAL' in s:   return 'SDT-Final'
    if 'SDTSTART' in s:   return 'SDT-Start'
    if 'ISVM' in s:       return 'ISVM-EDC'
    # flow_kw-based for POSTSURGE series (SDT keywords already caught above)
    if 'FINAL' in s:      return 'SDS-Final'
    if 'STRESS' in s:     return 'Stress'
    if 'ETEMP' in s:      return 'Post-Surge-HT'  # elevated temp variant
    if 'POSTSURGE' in s or 'START' in s:  return 'Post-Surge'
    return 'OTHER'

def railtype_of(s):
    if not s: return '?'
    for r in ('VLCDPS', 'LCDPS', 'HVDPS', 'HCDPS'):
        if r in s: return r[:-3]
    return '?'

def short_kill(s):
    if not s: return '?'
    s = s.replace('TPI_VCC::', '').replace('_119325', '')
    s = re.sub(r'^CONT_', '', s)
    rtype = next((r for r in ('HVDPS', 'HCDPS', 'LCDPS', 'VLCDPS') if r in s), '')
    port  = next((p for p in ('K_START', 'E_START', 'K_FINAL') if p in s), '')
    phase = next((p for p in ('PRESURGE', 'POSTSURGE', 'ISVM', 'SDTFINAL', 'SDTSTART', 'FINAL', 'STRESS', 'START') if p in s), '')
    parts = [x for x in [rtype, port, phase] if x]
    return ' \u00b7 '.join(parts) if parts else s[:60]

# Full column regex covering all K-mode (kill) and E-mode (EDC) cont columns
# Groups: (rail_cs, mode K|E, flow_kw, condition, pin_fragment, runid)
# Condition group is open-ended: matches any _SERIAL_V2, _PARALLEL_EDC, _SERIAL_EDC
# so Stress / SDS-Final / SDT-Start / SDT-Final columns are all captured.
_COL_RE = re.compile(
    r'TPI_VCC::CONT_(\w+?)DPS_DC_([KE])_(\w+?)_X_X_X_X_'
    r'(\w+_(?:SERIAL_V2|PARALLEL_EDC|SERIAL_EDC))'
    r'_((?:HC|VLC|HV|LC)\d+.+?)_(\d+)$'
)

def parse_col(col):
    """Returns (cs, mode, flow_kw, cond, pin) or None."""
    m = _COL_RE.match(col)
    if m:
        cs, mode, flow_kw, cond, pin, _ = m.groups()
        return cs, mode, flow_kw, cond, pin
    return None

def load_limits(jpath):
    lim = {}
    if not jpath or not os.path.exists(jpath): return lim
    data = json.load(open(jpath, encoding='utf-8'))
    for cs, entries in data.get('ConfigSets', {}).items():
        for e in entries:
            pin = e.get('Pin', '')
            if not pin: continue
            lim[pin] = {
                'usl': float(e['UpperLimit'])  if e.get('UpperLimit')  is not None else None,
                'lsl': float(e['LowerLimit'])  if e.get('LowerLimit')  is not None else None,
                'cs': cs,
            }
    return lim

def col_to_pin(col, lim):
    # Sort pins longest-first to avoid substring collisions (e.g. VCCIA vs VCCATOM)
    for pin in sorted(lim, key=len, reverse=True):
        if pin in col: return pin
    return None

def resolve_limits_from_trace(df, operation='119325'):
    """
    Load limits from the 61C program directly first (canonical source).
    If not available, fall back to trace auto-detection, then --json arg.
    Merges START + FINAL + SDT_START JSON files so all phase limits are covered.
    Returns a merged lim dict, or {} on failure.
    """
    # ── Primary: use 61C InputFiles directly ─────────────────────────────────
    _61c_jsons = [
        ('VCC_SDS_VSIM_START.json',   'SDS-Start limits'),
        ('VCC_SDS_VSIM_STRESS.json',  'Stress limits'),
        ('VCC_SDS_VSIM_FINAL.json',   'SDS-Final limits'),
        ('VCC_SDTSTART_VSIM.json',    'SDT-Start limits'),
        ('VCC_SDTFINAL_VSIM.json',    'SDT-Final limits'),
    ]
    merged = {}
    if os.path.isdir(_JSON_DIR_61C):
        for fname, label in _61c_jsons:
            jpath = os.path.join(_JSON_DIR_61C, fname)
            if os.path.isfile(jpath):
                lim = load_limits(jpath)
                for pin, v in lim.items():
                    if pin not in merged:   # START takes precedence
                        merged[pin] = v
                print(f'  [61C] {fname}: {len(lim)} pins ({label})')
        if merged:
            print(f'  [61C] Total merged limits: {len(merged)} pins')
            return merged
    # ── Fallback: trace auto-detection ───────────────────────────────────────
    try:
        sys.path.insert(0, _TRACE_DIR)
        import trace_bridge
        lots = []
        if LOT_COL in df.columns:
            lots = sorted(df[LOT_COL].dropna().unique().tolist())
        for lot in lots[:5]:
            try:
                defs = trace_bridge.xeus_get(str(lot), operation=operation)
            except Exception as e:
                print(f'  [trace] {lot}: {e}')
                continue
            for d in defs:
                stpl_dir = d.get('stplDirectory') or d.get('rootTpDirectory') or ''
                if not stpl_dir or not os.path.isdir(stpl_dir):
                    continue
                prog = d.get('programName', '')[:30]
                for dirpath, dirs, files in os.walk(stpl_dir):
                    if 'TPI_VCC' not in dirpath:
                        continue
                    for fn in files:
                        if fn == 'VCC_SDS_VSIM_START.json':
                            jpath = os.path.join(dirpath, fn)
                            lim = load_limits(jpath)
                            if lim:
                                print(f'  [trace] program={prog}')
                                print(f'  [trace] limits={fn} ({len(lim)} pins)')
                                return lim
    except Exception as e:
        print(f'  [trace] resolve_limits failed: {e}')
    return {}

def load_xeus_bin_summary(lots, operation='119325'):
    """
    Fast XEUS query: one xeus_bin_dist call per lot to get BIN8 wafer counts.
    Returns {lot: {'bin8_count': N, 'wafer_count': M, 'wafers': [...]}}
    Much faster than per-unit xeus_units (one call per lot vs one per wafer).
    """
    try:
        sys.path.insert(0, _TRACE_DIR)
        import trace_bridge
        summary = {}
        for lot in lots:
            try:
                r = trace_bridge.xeus_bin_dist(str(lot), operation=operation, bin_kind='interface')
                bin8_entry = next((d for d in r.get('distribution', []) if d['bin'] == TARGET_IBIN), None)
                bin8_count = bin8_entry['count'] if bin8_entry else 0
                all_matches = r.get('allMatches', [])
                wafer_names = [m.get('name', '') for m in all_matches]
                summary[lot] = {
                    'bin8_count': bin8_count,
                    'wafer_count': len(all_matches),
                    'wafers': wafer_names,
                }
                print(f'  [xeus] {lot}: BIN{TARGET_IBIN}={bin8_count} across {len(all_matches)} wafers')
            except Exception as e:
                print(f'  [xeus] {lot}: {e}')
        return summary
    except Exception as e:
        print(f'  [xeus] load_xeus_bin_summary failed: {e}')
        return {}


def _parse_mtpl_test_setup(mtpl_path):
    """Parse TPI_VCC.mtpl → {instance_name: {config_file, config_set, levels_tc}}
    for all SPEXVccContinuity blocks. No fallback — returns {} if file missing."""
    if not os.path.isfile(mtpl_path):
        return {}
    result = {}
    try:
        with open(mtpl_path, 'r', encoding='utf-8', errors='ignore') as f:
            txt = f.read()
        pat = re.compile(r'CSharpTest\s+SPEXVccContinuity\s+(\S+)\s*\{([^}]+)\}', re.DOTALL)
        for m in pat.finditer(txt):
            name = m.group(1).strip()
            body = m.group(2)
            def _get(key, _body=body):
                km = re.search(r'\b' + key + r'\s*=\s*([^;]+);', _body)
                return km.group(1).strip().strip('"') if km else ''
            config_file_raw = _get('ConfigFile')
            # Extract just the filename — ConfigFile may be an expression like
            # GetEnvironmentVariable(...) + "/path/to/File.json"; strip('"') on
            # the raw value removes the closing quote, so match the filename directly.
            cf_m = re.search(r'([\w][\w.\-]*\.json)', config_file_raw)
            config_file_clean = cf_m.group(1) if cf_m else ''
            levels_tc_raw = _get('LevelsTc')
            levels_tc = levels_tc_raw.split('::')[-1] if '::' in levels_tc_raw else levels_tc_raw
            result[name] = {
                'config_file': config_file_clean,
                'config_set':  _get('ConfigSet'),
                'levels_tc':   levels_tc,
            }
    except Exception as e:
        print(f'  [mtpl] parse error: {e}')
    return result


def _parse_lvl_force_per_pin(lvl_path, level_name):
    """Parse a specific named Levels block from LevelsSequences.lvl.
    Returns {pin_name: vforce_str} for entries with StartMeasurement = TRUE only.
    No fallback — returns {} if level not found or file missing."""
    if not os.path.isfile(lvl_path) or not level_name:
        return {}
    try:
        with open(lvl_path, encoding='utf-8', errors='replace') as f:
            txt = f.read()
        # MTPL LevelsTc values often carry a trailing _lvl suffix not present in the file
        _candidates = [level_name]
        if level_name.endswith('_lvl'):
            _candidates.append(level_name[:-4])
        m = None
        for _ln in _candidates:
            pat = re.compile(r'\bLevels\s+' + re.escape(_ln) + r'\s*\{')
            m = pat.search(txt)
            if m:
                break
        if not m:
            return {}
        si = m.end(); depth = 1; i = si
        while i < len(txt) and depth:
            if txt[i] == '{': depth += 1
            elif txt[i] == '}': depth -= 1
            i += 1
        body = txt[si:i-1]
        result = {}
        for pm in re.finditer(r'(\w+)\s*\{([^}]+)\}', body):
            pin_name = pm.group(1)
            pin_body = pm.group(2)
            sm = re.search(r'StartMeasurement\s*=\s*(\w+)', pin_body)
            if not sm or sm.group(1).upper() != 'TRUE':
                continue
            vf = re.search(r'VForce\s*=\s*([^;]+);', pin_body)
            if vf:
                result[pin_name] = vf.group(1).strip()
        return result
    except Exception as e:
        print(f'  [lvl] parse error for {level_name}: {e}')
        return {}


def _parse_json_config_set(json_path, config_set):
    """Parse ConfigSets[config_set] from a VCC JSON limits file.
    Returns (force_overrides, pin_limits) where:
      force_overrides: {pin: force_val}              — only pins with a 'Force' field
      pin_limits:      {pin: (lsl, usl, type_str)}   — LowerLimit/UpperLimit (raw float) + Type
    No fallback — returns ({}, {}) if file missing."""
    if not os.path.isfile(json_path):
        return {}, {}
    try:
        import json as _json
        with open(json_path, encoding='utf-8') as f:
            data = _json.load(f)
        pins = data.get('ConfigSets', {}).get(config_set, [])
        force_ov = {e['Pin']: e['Force'] for e in pins if 'Force' in e and 'Pin' in e}
        pin_lim  = {e['Pin']: (e.get('LowerLimit'), e.get('UpperLimit'), e.get('Type', ''))
                    for e in pins if 'Pin' in e
                    and (e.get('LowerLimit') is not None or e.get('UpperLimit') is not None)}
        return force_ov, pin_lim
    except Exception as e:
        print(f'  [json] config set parse error for {config_set}: {e}')
        return {}, {}


def _parse_json_force_override(json_path, config_set):
    """Backward-compat wrapper — returns only force overrides dict."""
    return _parse_json_config_set(json_path, config_set)[0]


def _resolve_prog_dir(prog_name, prog_root, fallback_prog):
    """Map a program name → its directory.
    Uses prog_root/prog_name only — no fallback to any other program.
    Each program MUST have its own directory for correct limit loading."""
    if prog_root:
        candidate = os.path.join(prog_root, prog_name)
        if os.path.isdir(candidate):
            return candidate
        print(f'  [prog] WARNING: {candidate} not found under prog_root — limits will be EMPTY for {prog_name}')
        print(f'  [prog] Set --prog-root to the folder containing all program subdirectories.')
    elif fallback_prog and os.path.isdir(fallback_prog):
        # Only use fallback if it IS the exact program directory (same name)
        if os.path.basename(fallback_prog) == prog_name:
            return fallback_prog
        print(f'  [prog] WARNING: --prog set to {os.path.basename(fallback_prog)} but analyzing {prog_name}.')
        print(f'  [prog] No fallback — limits will be EMPTY for {prog_name}. Use --prog-root instead.')
    else:
        print(f'  [prog] WARNING: No program directory configured for {prog_name} — limits will be EMPTY.')
        print(f'  [prog] Use --prog-root pointing to the parent folder containing all program subdirectories.')
    return ''


def analyze_program(df_prog, prog_dir, args_json=None, focus_mode=False):
    """Run the full analysis pipeline for one program slice.
    Returns a result dict consumed by build_html."""
    prog_name = str(df_prog[PROG_COL].iloc[0]) if PROG_COL in df_prog.columns and len(df_prog) else 'unknown'
    print(f'\n-- Analyzing {prog_name} ({len(df_prog)} rows, prog_dir={prog_dir or "(none)"}, focus_mode={focus_mode}) --')

    json_dir  = os.path.join(prog_dir, 'Modules', 'TPI_VCC', 'InputFiles') if prog_dir else ''
    isvm_json = os.path.join(json_dir, 'VCC_SDS_ISVM.json') if json_dir else ''
    _ISVM_JSON_PATHS = [isvm_json]

    # ── Limits ──────────────────────────────────────────────────────────────
    # lim           = union of all phases (for pin name discovery via col_to_pin)
    # lim_by_flow   = {flow_kw: limits_dict} — phase-specific USL/LSL lookup
    # K_FINAL columns → FINAL.json, K_STRESS → STRESS.json, K_START → START.json, etc.
    lim = {}
    lim_by_flow = {}
    _JSON_FLOW_MAP = [
        ('VCC_SDS_VSIM_START.json',  ('START',)),
        ('VCC_SDS_VSIM_STRESS.json', ('STRESS',)),
        ('VCC_SDS_VSIM_FINAL.json',  ('FINAL',)),
        ('VCC_SDTSTART_VSIM.json',   ('SDTSTART',)),
        ('VCC_SDTFINAL_VSIM.json',   ('SDTFINAL',)),
    ]
    if json_dir and os.path.isdir(json_dir):
        for fname, flow_kws in _JSON_FLOW_MAP:
            jpath = os.path.join(json_dir, fname)
            if os.path.isfile(jpath):
                phase_lim = load_limits(jpath)
                for kw in flow_kws:
                    lim_by_flow[kw] = phase_lim
                for pin, v in phase_lim.items():
                    if pin not in lim: lim[pin] = v   # START takes precedence for union
        print(f'  [lim] {len(lim)} pins, {len(lim_by_flow)} phase dicts from {json_dir}')
    if not lim:
        lim = resolve_limits_from_trace(df_prog)
    if not lim and args_json:
        lim = load_limits(args_json)
    print(f'  pin limits: {len(lim)} | e.g.: {list(lim.keys())[:4]}')

    bin8 = df_prog[df_prog[IB_COL].isin(TARGET_IBINS)].copy()
    print(f'  IB{TARGET_IBINS}: {len(bin8)} dies')

    parsed_cols = {}
    for col in df_prog.columns:
        p = parse_col(col)
        if p: parsed_cols[col] = p
    _EDC_CONDS = ('ISVM_PARALLEL_EDC', 'ISVM_SERIAL_EDC')
    k_cols = [c for c, v in parsed_cols.items() if v[1]=='K' and v[3] not in _EDC_CONDS]
    e_cols = [c for c, v in parsed_cols.items() if v[1]=='E' or v[3] in _EDC_CONDS]

    lots_all = sorted(str(l) for l in df_prog[LOT_COL].dropna().unique()) if LOT_COL in df_prog.columns else []
    _mat_col = (next((c for c in df_prog.columns if c == 'Material Type, Skew, BEOL Skew'), None) or
                next((c for c in df_prog.columns if 'skew' in c.lower() and 'material' in c.lower()), None) or
                next((c for c in df_prog.columns if c.lower() == 'material type'), None) or
                next((c for c in df_prog.columns if 'material type' in c.lower()), None) or
                next((c for c in df_prog.columns if 'material' in c.lower()), None))

    # ── Per-die data ─────────────────────────────────────────────────────────
    dies = []
    for idx, row in bin8.iterrows():
        wfr  = int(row[WFR_COL])  if pd.notna(row.get(WFR_COL))  else 0
        x    = int(row[X_COL])    if pd.notna(row.get(X_COL))    else 0
        y    = int(row[Y_COL])    if pd.notna(row.get(Y_COL))    else 0
        lot  = str(row[LOT_COL])  if LOT_COL  in row.index and pd.notna(row[LOT_COL])  else ''
        prog = str(row[PROG_COL]) if PROG_COL in row.index and pd.notna(row[PROG_COL]) else ''
        fbin = int(row[FB_COL])   if pd.notna(row.get(FB_COL))   else 0
        dbin = int(row[DB_COL])   if pd.notna(row.get(DB_COL))   else 0
        ibin = int(row[IB_COL])   if pd.notna(row.get(IB_COL))   else 0
        kill_full, _ = parse_setbin(row.get(SETBIN))
        phase = phase_of(kill_full); rtype = railtype_of(kill_full); kill_s = short_kill(kill_full)
        effective_kill = kill_full or ''
        if effective_kill:
            kill_match_cols = [c for c in k_cols if effective_kill+'_' in c]
            if not kill_match_cols:
                kn = effective_kill.replace('TPI_VCC::','')
                kill_match_cols = [c for c in k_cols if kn in c]
        else:
            kill_match_cols = k_cols
        fail_map = {}
        for col in kill_match_cols:
            val = pd.to_numeric(row.get(col), errors='coerce')
            if pd.isna(val): continue
            cs, mode, flow_kw, cond, pin_frag = parsed_cols[col]
            col_phase = phase_of(col)
            matched_pin = col_to_pin(col, lim)
            if matched_pin:
                # Use phase-specific limits; fall back to union lim if phase not found
                _plim = lim_by_flow.get(flow_kw, lim).get(matched_pin, lim.get(matched_pin, {}))
                usl = _plim.get('usl'); lsl = _plim.get('lsl')
                exceeded = (usl is not None and val>usl) or (lsl is not None and val<lsl)
                if not exceeded: continue
                ratio = val/usl if (usl and usl>0) else abs(val)
                key = (matched_pin, col_phase)
                prev = fail_map.get(key)
                if prev is None or ratio > prev['_r']:
                    fail_map[key] = {'pin': matched_pin, 'phase': col_phase,
                                     'val': round(val*1000,3),
                                     'usl': round(usl*1000,3) if usl else None,
                                     'lsl': round(lsl*1000,3) if lsl else None,
                                     'cs': cs, 'has_lim': True, '_r': ratio}
            else:
                ratio = abs(val); key = (pin_frag, col_phase); prev = fail_map.get(key)
                if prev is None or ratio > prev['_r']:
                    fail_map[key] = {'pin': pin_frag, 'phase': col_phase,
                                     'val': round(val*1000,3), 'usl': None, 'lsl': None,
                                     'cs': cs, 'has_lim': False, '_r': ratio}
        fail_list = sorted(fail_map.values(), key=lambda d: (-int(d['has_lim']), -d['_r']))
        for f in fail_list: f.pop('_r', None)
        edc_cs = {}
        for col in e_cols:
            val = pd.to_numeric(row.get(col), errors='coerce')
            if pd.isna(val): continue
            cs, mode, flow_kw, cond, pin_frag = parsed_cols[col]
            matched_pin = col_to_pin(col, lim)
            usl = lim_by_flow.get(flow_kw, lim).get(matched_pin, lim.get(matched_pin, {})).get('usl') if matched_pin else None
            lsl = lim_by_flow.get(flow_kw, lim).get(matched_pin, lim.get(matched_pin, {})).get('lsl') if matched_pin else None
            if cs not in edc_cs:
                edc_cs[cs] = {'n_fail':0,'n_total':0,'worst':0.0,'worst_lsl':None,
                              'usl':round(usl*1000,3) if usl else None,
                              'lsl':round(lsl*1000,3) if lsl else None}
            edc_cs[cs]['n_total'] += 1
            val_m = round(val*1000,3)
            if usl is not None and val>usl:
                edc_cs[cs]['n_fail'] += 1
                if val_m > edc_cs[cs]['worst']: edc_cs[cs]['worst'] = val_m
            elif lsl is not None and val<lsl:
                edc_cs[cs]['n_fail'] += 1
                if edc_cs[cs]['worst_lsl'] is None or val_m < edc_cs[cs]['worst_lsl']:
                    edc_cs[cs]['worst_lsl'] = val_m
        devrevstep = str(row[DEVREVSTEP_COL]) if DEVREVSTEP_COL in row.index and pd.notna(row.get(DEVREVSTEP_COL)) else ''
        material   = str(row[_mat_col]).strip() if _mat_col and _mat_col in row.index and pd.notna(row.get(_mat_col)) else ''
        dies.append({'k': lot[:8]+'_'+str(wfr)+'_'+str(x)+'_'+str(y),
                     'wfr': wfr, 'x': x, 'y': y, 'lot': lot, 'prog': prog,
                     'ibin': ibin, 'fbin': fbin, 'dbin': dbin, 'kill': kill_s, 'kill_full': kill_full or '',
                     'xeus_kill': '', 'phase': phase, 'rtype': rtype, 'drs': devrevstep,
                     'material': material, 'pins': fail_list, 'edc': edc_cs})

    print(f'  Done: {len(dies)} BIN8 dies')

    # ── Wafer map ────────────────────────────────────────────────────────────
    all_map = defaultdict(list)
    for _, row in df_prog.iterrows():
        prog = str(row.get(PROG_COL,'')) if PROG_COL in df_prog.columns else ''
        lot  = str(row.get(LOT_COL,''))  if LOT_COL  in df_prog.columns else ''
        wfr  = int(row[WFR_COL]) if pd.notna(row.get(WFR_COL)) else 0
        x    = int(row[X_COL])   if pd.notna(row.get(X_COL))   else 0
        y    = int(row[Y_COL])   if pd.notna(row.get(Y_COL))   else 0
        ibin = int(row[IB_COL])  if pd.notna(row.get(IB_COL))  else 0
        fbin = int(row[FB_COL])  if pd.notna(row.get(FB_COL))  else 0
        key  = prog+'|'+lot+'|'+str(wfr)
        all_map[key].append([x, y, ibin, fbin if ibin in TARGET_IBINS else 0])

    # ── Summaries ────────────────────────────────────────────────────────────
    fb_map = defaultdict(lambda: {'count':0,'kills':defaultdict(int),'pins':defaultdict(int),'wafers':set()})
    for d in dies:
        fb = d['fbin']; fb_map[fb]['count'] += 1; fb_map[fb]['kills'][d['kill']] += 1
        fb_map[fb]['wafers'].add((d['lot'], d['wfr']))
        seen = set()
        for p in d['pins']:
            if p['pin'] not in seen: fb_map[fb]['pins'][p['pin']] += 1; seen.add(p['pin'])
    fb_list = []
    for fb, s in sorted(fb_map.items(), key=lambda kv: -kv[1]['count']):
        top_kill = max(s['kills'], key=s['kills'].get) if s['kills'] else '?'
        # Sort wafers: by lot then wafer number; store as [{lot, wfr}] objects
        wfr_objs = sorted([{'lot': lot, 'wfr': wfr} for lot, wfr in s['wafers']],
                           key=lambda x: (x['lot'], x['wfr']))
        fb_list.append({'fbin': fb, 'count': s['count'], 'top_kill': top_kill,
                        'kill_n': s['kills'].get(top_kill,0), 'kills': dict(s['kills']),
                        'pins': [{'pin':p,'n':c} for p,c in sorted(s['pins'].items(),key=lambda x:-x[1])[:6]],
                        'wafers': wfr_objs})
    if not any(f['fbin']==899 for f in fb_list):
        fb_list.append({'fbin':899,'count':0,'top_kill':'—','kill_n':0,'kills':{},'pins':[],'wafers':[]})

    wfr_map = defaultdict(lambda: {'count':0,'lot':'','prog':'','material':'','fbins':defaultdict(int)})
    for d in dies:
        wk = (d['lot'],d['prog'],d['wfr']); wfr_map[wk]['count']+=1
        wfr_map[wk]['lot']=d['lot']; wfr_map[wk]['prog']=d['prog']; wfr_map[wk]['fbins'][d['fbin']]+=1
        if d.get('material'): wfr_map[wk]['material'] = d['material']
    wfr_list = sorted([{'wfr':k[2],'lot':k[0],'prog':k[1],'count':v['count'],'material':v['material'],
                         'fbins':dict((str(fb),c) for fb,c in v['fbins'].items())}
                        for k,v in wfr_map.items()], key=lambda x: -x['count'])

    kill_map = defaultdict(lambda: {'count':0,'phase':'','rtype':'','fbins':defaultdict(int),'pins':defaultdict(int),'full':''})
    for d in dies:
        k=d['kill']; kill_map[k]['count']+=1; kill_map[k]['phase']=d['phase']
        kill_map[k]['rtype']=d['rtype']; kill_map[k]['full']=d['kill_full']
        kill_map[k]['fbins'][d['fbin']]+=1
        seen=set()
        for p in d['pins']:
            if p['pin'] not in seen: kill_map[k]['pins'][p['pin']]+=1; seen.add(p['pin'])
    kill_list = sorted([{'kill':k,'full':v['full'],'count':v['count'],'phase':v['phase'],'rtype':v['rtype'],
                          'fbins':dict((str(fb),c) for fb,c in v['fbins'].items()),
                          'pins':[{'pin':p,'n':c} for p,c in sorted(v['pins'].items(),key=lambda x:-x[1])[:8]]}
                         for k,v in kill_map.items()], key=lambda x:-x['count'])

    pin_map = defaultdict(lambda: {'count':0,'fbins':defaultdict(int),'phases':defaultdict(int)})
    for d in dies:
        seen=set()
        for p in d['pins']:
            pin=p['pin']
            if pin not in seen:
                pin_map[pin]['count']+=1; pin_map[pin]['fbins'][d['fbin']]+=1
                pin_map[pin]['phases'][p['phase']]+=1; seen.add(pin)
    pin_list = sorted([{'pin':p,'count':v['count'],
                         'fbins':dict((str(fb),c) for fb,c in v['fbins'].items()),
                         'phases':dict(v['phases'])}
                        for p,v in pin_map.items()], key=lambda x:-x['count'])

    lot_list  = sorted(set(d['lot']  for d in dies))
    prog_list = sorted(set(d['prog'] for d in dies))

    # ── Force voltages — per-pin from mtpl+lvl, no fallback ──────────────────
    _lvl_path   = os.path.join(prog_dir, 'LevelsSequences.lvl') if prog_dir else ''
    _mtpl_path  = os.path.join(prog_dir, 'Modules', 'TPI_VCC', 'TPI_VCC.mtpl') if prog_dir else ''
    _mtpl_setup = _parse_mtpl_test_setup(_mtpl_path)
    if _mtpl_setup:
        print(f'  [mtpl] {len(_mtpl_setup)} SPEXVccContinuity instances parsed')
    # Build flat force_by_cs {cs: force_str} from POSTSURGE_SERIAL_V2 level blocks
    _cs_to_setup = {}
    for _inst_name, _inst in _mtpl_setup.items():
        if 'POSTSURGE_SERIAL_V2' in _inst_name and _inst.get('levels_tc'):
            for _cs in ('VLC', 'LC', 'HC', 'HV'):
                if f'CONT_{_cs}DPS' in _inst_name and _cs not in _cs_to_setup:
                    _cs_to_setup[_cs] = _inst
                    break
    _force_by_cs  = {}
    _force_all_lvl = {}  # kept for build_flow_data compat; populated below
    for _cs, _setup in _cs_to_setup.items():
        _pin_forces = _parse_lvl_force_per_pin(_lvl_path, _setup['levels_tc'])
        if _pin_forces:
            _force_by_cs[_cs] = next(iter(_pin_forces.values()))
    if _force_by_cs:
        print(f'  [lvl] force by CS: {_force_by_cs}')
    else:
        print(f'  [lvl] force: N/A (no program directory or dc_spex levels found)')

    for d in dies:
        for p in d['pins']:
            p['force_val'] = _force_by_cs.get(p.get('cs',''), '')

    # ── Rail summary ─────────────────────────────────────────────────────────
    rail_list = []
    for pin in sorted(lim, key=lambda p: (lim[p].get('cs',''), p)):
        ldata=lim[pin]; usl_raw=ldata.get('usl'); cs=ldata.get('cs','?')
        force=_force_by_cs.get(cs,'')
        acc={}
        for d in dies:
            for p in d['pins']:
                if p['pin']!=pin: continue
                ph=p['phase']
                if ph not in acc: acc[ph]={'vals':[],'n_fail':0}
                acc[ph]['vals'].append(p['val'])
                if p['has_lim']: acc[ph]['n_fail']+=1
        def _rs(s):
            v=sorted(s['vals']); n=len(v)
            if not n: return None
            med=v[n//2] if n%2 else (v[n//2-1]+v[n//2])/2
            return {'n':n,'n_fail':s['n_fail'],'med':round(med,2),'worst':round(v[-1],2)}
        phases={ph:_rs(s) for ph,s in acc.items()}
        rail_list.append({'pin':pin,'cs':cs,'force':force,
                          'usl':round(usl_raw*1000,3) if usl_raw else None,'phases':phases})
    print(f'  Rail summary: {len(rail_list)} pins')

    # ── Surge delta ──────────────────────────────────────────────────────────
    passd_df = df_prog[df_prog[IB_COL]==1].copy()
    def _surge_rail(c):
        m=re.search(r'_([A-Z][A-Z0-9]+)_119325',c); return m.group(1) if m else None
    def _surge_dps(c):
        m=re.search(r'CONT_([A-Z]+)DPS_',c); return m.group(1) if m else 'HV'
    _sg_post={}
    for c in df_prog.columns:
        if 'POSTSURGE' in c and 'K_START' in c and 'SERIAL_V2' in c and 'FINAL' not in c and c.startswith('TPI_VCC::CONT_'):
            r=_surge_rail(c)
            if r: _sg_post[r]=c
    _sg_pre_e={}; _sg_pre_k={}
    for c in df_prog.columns:
        if 'PRESURGE' in c and 'SERIAL_V2' in c and c.startswith('TPI_VCC::CONT_'):
            r=_surge_rail(c)
            if not r: continue
            if 'E_START' in c: _sg_pre_e[r]=c
            elif 'K_START' in c: _sg_pre_k[r]=c
    _surge_cols={}; _surge_meta={}
    for _r,_post_c in _sg_post.items():
        _pre_e_c=_sg_pre_e.get(_r); _pre_k_c=_sg_pre_k.get(_r)
        if not _pre_e_c and not _pre_k_c: continue
        _surge_cols[_r]=(_pre_k_c,_pre_e_c,_post_c)
        _pre_s=passd_df[_pre_e_c] if _pre_e_c else passd_df[_pre_k_c]
        if _pre_e_c and _pre_k_c: _pre_s=passd_df[_pre_k_c].combine_first(passd_df[_pre_e_c])
        _pav=passd_df[_post_c].dropna()*1000; _pre_pav=_pre_s.dropna()*1000
        _surge_meta[_r]={'pre_p99':round(float(_pre_pav.quantile(0.99)),3) if len(_pre_pav) else 0.0,
                          'post_p99':round(float(_pav.quantile(0.99)),3) if len(_pav) else 0.0,
                          'pre_med':round(float(_pre_pav.median()),3) if len(_pre_pav) else 0.0,
                          'post_med':round(float(_pav.median()),3) if len(_pav) else 0.0}
    _surge_dies=[]
    for _,_row in bin8.iterrows():
        _d={'wfr':int(_row[WFR_COL]) if pd.notna(_row[WFR_COL]) else None,
             'x':int(_row[X_COL]) if pd.notna(_row[X_COL]) else None,
             'y':int(_row[Y_COL]) if pd.notna(_row[Y_COL]) else None,
             'lot':str(_row[LOT_COL])}
        _any=False
        for _r,(_pre_k_c,_pre_e_c,_post_c) in _surge_cols.items():
            _pre_raw=None
            if _pre_k_c and pd.notna(_row[_pre_k_c]): _pre_raw=_row[_pre_k_c]
            elif _pre_e_c and pd.notna(_row[_pre_e_c]): _pre_raw=_row[_pre_e_c]
            _post_raw=_row[_post_c] if pd.notna(_row[_post_c]) else None
            if _pre_raw is not None and _post_raw is not None:
                _d[_r]={'pre':round(float(_pre_raw)*1000,3),'post':round(float(_post_raw)*1000,3)}; _any=True
        if _any: _surge_dies.append(_d)
    _surge_dps={}
    def _surge_dps_name(c):
        m=re.search(r'CONT_([A-Z]+)DPS_',c); return m.group(1) if m else 'HV'
    for _r in sorted(_surge_meta): _surge_dps.setdefault(_surge_dps_name(_sg_post[_r]),[]).append(_r)
    surge_data_obj={'meta':_surge_meta,'dies':_surge_dies,'dps_groups':_surge_dps}

    # ── ISVM EDC ─────────────────────────────────────────────────────────────
    def _edc_rail(col):
        m=re.search(r'_([A-Z][A-Z0-9]+)_119325$',col); return m.group(1) if m else None
    _edc_pre_cols={}; _edc_post_cols={}
    for c in df_prog.columns:
        if 'ISVM' in c and 'E_START' in c and c.startswith('TPI_VCC::CONT_'):
            r=_edc_rail(c)
            if r: _edc_pre_cols[r]=c
    for c in df_prog.columns:
        if 'POSTSURGE' in c and 'K_START' in c and c.startswith('TPI_VCC::CONT_'):
            r=_edc_rail(c)
            if r and r in _edc_pre_cols: _edc_post_cols[r]=c
    _edc_meta={}
    for _r in _edc_pre_cols:
        if _r not in _edc_post_cols: continue
        _ppre=passd_df[_edc_pre_cols[_r]].dropna()*1000
        _ppost=passd_df[_edc_post_cols[_r]].dropna()*1000
        _edc_meta[_r]={'pre_p99':round(float(_ppre.quantile(0.99)),3) if len(_ppre) else 0.0,
                        'post_p99':round(float(_ppost.quantile(0.99)),3) if len(_ppost) else 0.0,
                        'pre_med':round(float(_ppre.median()),3) if len(_ppre) else 0.0,
                        'post_med':round(float(_ppost.median()),3) if len(_ppost) else 0.0}
    _edc_dies=[]
    for _,_row in bin8.iterrows():
        _d={'wfr':int(_row[WFR_COL]) if pd.notna(_row[WFR_COL]) else None,
             'x':int(_row[X_COL]) if pd.notna(_row[X_COL]) else None,
             'y':int(_row[Y_COL]) if pd.notna(_row[Y_COL]) else None,
             'lot':str(_row[LOT_COL])}
        _any=False
        for _r in _edc_meta:
            _pre_raw=_row[_edc_pre_cols[_r]] if pd.notna(_row[_edc_pre_cols[_r]]) else None
            _post_raw=_row[_edc_post_cols[_r]] if pd.notna(_row[_edc_post_cols[_r]]) else None
            if _pre_raw is not None and _post_raw is not None:
                _d[_r]={'pre':round(float(_pre_raw)*1000,3),'post':round(float(_post_raw)*1000,3)}; _any=True
        if _any: _edc_dies.append(_d)
    _edc_groups={}
    for _r in sorted(_edc_meta):
        _dm=re.search(r'CONT_([A-Z]+)DPS_',_edc_pre_cols[_r])
        _edc_groups.setdefault(_dm.group(1) if _dm else 'OTHER',[]).append(_r)
    _EDC_USL_DEFAULT=1000.0; _EDC_LSL_DEFAULT=0.0
    _edc_limits={}
    if isvm_json and os.path.isfile(isvm_json):
        try:
            with open(isvm_json, encoding='utf-8') as _jf: _jdata=json.load(_jf)
            for _dps_type,_pinlist in _jdata.get('ConfigSets',{}).items():
                for _entry in (_pinlist or []):
                    _pin=_entry.get('Pin',''); _rail=_pin.rsplit('_',1)[-1]
                    if not _rail: continue
                    _edc_limits[_rail]={'usl':round(float(_entry.get('UpperLimit',1.0))*1000,3),
                                        'lsl':round(float(_entry.get('LowerLimit',0.0))*1000,3)}
        except Exception as _e: print(f'  [edc lim] {_e}')
    for _r in _edc_meta:
        _lim=_edc_limits.get(_r,{})
        _edc_meta[_r]['usl']=_lim.get('usl',_EDC_USL_DEFAULT)
        _edc_meta[_r]['lsl']=_lim.get('lsl',_EDC_LSL_DEFAULT)
    _edc_limit_stats={}
    for _r,_ec in _edc_pre_cols.items():
        _usl_r=_edc_limits.get(_r,{}).get('usl',_EDC_USL_DEFAULT)
        _lsl_r=_edc_limits.get(_r,{}).get('lsl',_EDC_LSL_DEFAULT)
        _all_v=pd.to_numeric(bin8[_ec],errors='coerce').dropna()*1000
        if not len(_all_v): continue
        _edc_limit_stats[_r]={'n_hi':int((_all_v>_usl_r).sum()),'n_lo':int((_all_v<_lsl_r).sum()),
                               'n_total':int(len(_all_v)),'median':round(float(_all_v.median()),2),
                               'worst':round(float(_all_v.max()),2),'usl':_usl_r,'lsl':_lsl_r}
    edc_data_obj={'meta':_edc_meta,'dies':_edc_dies,'groups':_edc_groups,'limit_stats':_edc_limit_stats}

    # ── Rail × Condition ─────────────────────────────────────────────────────
    _CONDS=['Pre-Surge','Post-Surge','ISVM-EDC']; _RAILS=['VLC','LC','HV','HC']
    _rc_die_sets={(c,r):set() for c in _CONDS for r in _RAILS}
    _pin_cond=defaultdict(lambda: defaultdict(int))
    for _d in dies:
        _dk=_d['k']
        for _p in _d['pins']:
            if not _p.get('has_lim',False): continue
            _ph=_p['phase']; _cs=_p.get('cs','').upper()
            _rail=next((_r for _r in _RAILS if _cs.startswith(_r)),None)
            if _ph in ('Pre-Surge','Post-Surge') and _rail: _rc_die_sets[(_ph,_rail)].add(_dk)
            _pin_cond[_p['pin']][_ph]+=1
        for _cs_key,_edc_info in _d.get('edc',{}).items():
            if _edc_info.get('n_fail',0)>0:
                _rail=next((_r for _r in _RAILS if _cs_key.upper().startswith(_r)),None)
                if _rail: _rc_die_sets[('ISVM-EDC',_rail)].add(_dk)
    _rc_counts={f'{c}|{r}':len(_rc_die_sets[(c,r)]) for c in _CONDS for r in _RAILS}
    _pin_rows=sorted([{'pin':_pin,'pre':_cmap.get('Pre-Surge',0),'post':_cmap.get('Post-Surge',0),
                        'edc':_cmap.get('ISVM-EDC',0),'total':sum(_cmap.values())}
                       for _pin,_cmap in _pin_cond.items()],key=lambda x:-x['total'])
    rail_cond_obj={'counts':_rc_counts,'conds':_CONDS,'rails':_RAILS,'pin_rows':_pin_rows[:60]}

    # ── Pin Distribution (all limit pins) + Detail raw data (top 5 failing) ────
    pin_distrib, detail_data = _compute_pin_distrib(
        df_prog=df_prog, lim=lim, lim_by_flow=lim_by_flow, k_cols=k_cols,
        parsed_cols=parsed_cols, pin_list=pin_list, lot_list=lot_list,
        focus_mode=focus_mode)

    flow_data=build_flow_data(dies,_force_by_cs,lim, lim_by_flow=lim_by_flow, prog_dir=prog_dir, force_all_levels=_force_all_lvl)
    report_html=build_report_html(
        dies=dies,pin_list=pin_list,kill_list=kill_list,wfr_list=wfr_list,fb_list=fb_list,lim=lim,
        total_dies=len(df_prog),bin8_count=len(bin8),pass_count=int((df_prog[IB_COL]==1).sum()),
        lots=lot_list,progs=prog_list)

    import math as _math
    _max_r=max((_math.sqrt(d[0]**2+d[1]**2) for ds in all_map.values() for d in ds),default=10.0)

    # phase_kills for top_pins with fbs/vals/usl/lsl
    # Phase 3: accumulate min/max/med summaries instead of raw val lists
    phase_kills=defaultdict(lambda:{'n':0,'pins':defaultdict(int),'pin_fbs':defaultdict(lambda:defaultdict(int)),'pin_vals':defaultdict(list)})
    for d in dies:
        ph=d.get('phase','OTHER'); phase_kills[ph]['n']+=1
        seen_pins=set()
        for p in d.get('pins',[]):
            pin=p['pin']; phase_kills[ph]['pins'][pin]+=1
            phase_kills[ph]['pin_vals'][pin].append(p['val'])
            if pin not in seen_pins:
                phase_kills[ph]['pin_fbs'][pin][d['fbin']]+=1; seen_pins.add(pin)

    # Convert raw val lists → {min, max, med} summaries to reduce JSON size
    def _val_summary(vals):
        if not vals: return {'min': None, 'max': None, 'med': None}
        sv = sorted(vals)
        return {'min': round(sv[0], 2), 'max': round(sv[-1], 2),
                'med': round(sv[len(sv)//2], 2)}

    return {
        'prog_name':   prog_name,
        'prog_dir':    prog_dir,
        'dies':        dies,
        'all_map':     dict(all_map),
        'fb_list':     fb_list,
        'wfr_list':    wfr_list,
        'kill_list':   kill_list,
        'pin_list':    pin_list,
        'rail_list':   rail_list,
        'flow_data':   flow_data,
        'surge_data':  surge_data_obj,
        'edc_data':    edc_data_obj,
        'rail_cond':   rail_cond_obj,
        'report_html': report_html,
        'lim':         lim,
        'force_by_cs': _force_by_cs,
        'phase_kills': {ph:{'n':v['n'],
                            'pins':dict(v['pins']),
                            'pin_fbs':{p:dict(fb) for p,fb in v['pin_fbs'].items()},
                            'pin_stats':{p:_val_summary(vals) for p,vals in v['pin_vals'].items()}}
                        for ph,v in phase_kills.items()},
        'total_dies':  len(df_prog),
        'bin8_count':  len(bin8),
        'pass_count':  int((df_prog[IB_COL]==1).sum()),
        'lots':        lot_list,
        'progs':       prog_list,
        'wfr_radius':  round(_max_r+0.5,2),
        'drs_vals':    sorted(df_prog[DEVREVSTEP_COL].dropna().unique().tolist()) if DEVREVSTEP_COL in df_prog.columns else [],
        'pin_distrib': pin_distrib,
        'detail_data': detail_data,
    }


def _compute_pin_distrib(df_prog, lim, lim_by_flow, k_cols, parsed_cols, pin_list, lot_list, focus_mode=False):
    """Compute per-pin, per-phase distribution stats for all limit pins.
    pin_distrib[pin] = {usl, lsl,
      phases: {ph_label: {col, bins, counts, counts_fail, mean, sigma, median,
                           p1, p99, n_total, n_fail, n3, n6, n12, cp, cpk,
                           wfr_stats: {'lot::wfr': {n,s,s2,n3,n6,n12,nf}}}},
      phase_list: [...ordered...]}
    detail_data[pin] = {ph_label: [[lot_idx, wfr, x, y, val_mV], ...]} for top 5 pins
    In Live Mode (focus_mode=True): wfr_stats is omitted (JS rebuilds from RAW_PIN_DATA);
    detail_data is omitted (RAW_PIN_DATA covers all pins).
    """
    _PHASE_ORDER = ['Pre-Surge', 'Post-Surge', 'Post-Surge-HT', 'Stress',
                    'SDS-Final', 'SDT-Start', 'SDT-Final', 'ISVM-EDC', 'OTHER']
    import math as _m

    # Map pin -> {phase_label -> (best_col, n_nonnan)}
    pin_phase_cols = {}
    for col in k_cols:
        p = parsed_cols.get(col)
        if not p: continue
        matched_pin = col_to_pin(col, lim)
        if not matched_pin: continue
        ph_label = phase_of(col)
        nn = int(df_prog[col].notna().sum())
        if nn == 0: continue
        prev = pin_phase_cols.setdefault(matched_pin, {}).get(ph_label)
        if prev is None or nn > prev[1]:
            pin_phase_cols[matched_pin][ph_label] = (col, nn)

    lot_idx_map = {lot: i for i, lot in enumerate(lot_list)}
    top5_pins = set(p['pin'] for p in pin_list[:5])

    def _quantile(sv, q):
        if not sv: return 0.0
        idx = q * (len(sv) - 1)
        lo, hi = int(idx), min(int(idx) + 1, len(sv) - 1)
        return sv[lo] + (idx - lo) * (sv[hi] - sv[lo])

    def _phase_stats(col, usl, lsl):
        ser = pd.to_numeric(df_prog[col], errors='coerce').dropna() * 1000  # mV
        vals = ser.values.tolist()
        if not vals: return None
        n_total = len(vals); sv = sorted(vals)
        mean_v = sum(vals) / n_total
        variance = sum((v - mean_v) ** 2 for v in vals) / n_total
        sigma = _m.sqrt(variance) if variance > 0 else 0.0
        median = _quantile(sv, 0.5)
        p1 = _quantile(sv, 0.01); p99 = _quantile(sv, 0.99)
        p25 = _quantile(sv, 0.25); p75 = _quantile(sv, 0.75)
        min_val = sv[0]; max_val = sv[-1]
        n_fail = 0
        if usl is not None: n_fail += sum(1 for v in vals if v > usl)
        if lsl is not None: n_fail += sum(1 for v in vals if v < lsl)
        lo_edge = min(p1, lsl if lsl is not None else p1)
        hi_edge = max(p99, usl if usl is not None else p99)
        # If there are fails, extend hi_edge to the 95th pct of failing values
        # so fails are spread across bins rather than all clamped into the last bin
        if usl is not None:
            fails_above = sorted([v for v in vals if v > usl])
            if fails_above:
                fail_p95 = _quantile(fails_above, 0.95)
                hi_edge = max(hi_edge, fail_p95)
        if lsl is not None:
            fails_below = sorted([v for v in vals if v < lsl])
            if fails_below:
                fail_p05 = _quantile(fails_below, 0.05)
                lo_edge = min(lo_edge, fail_p05)
        spread = hi_edge - lo_edge
        if spread <= 0: spread = max(abs(mean_v) * 0.01, 0.001)
        lo_edge -= spread * 0.05; hi_edge += spread * 0.05
        # Vectorized histogram using numpy-style binning
        import numpy as _np
        n_bins = 30
        vals_arr = _np.array(vals)
        bins_arr = _np.linspace(lo_edge, hi_edge, n_bins + 1)
        bins = [round(float(b), 3) for b in bins_arr]
        bin_w = (hi_edge - lo_edge) / n_bins
        idxs = _np.clip(((vals_arr - lo_edge) / bin_w).astype(int), 0, n_bins - 1)
        counts = _np.bincount(idxs, minlength=n_bins).tolist()
        fail_mask = _np.zeros(len(vals), dtype=bool)
        if usl is not None: fail_mask |= (vals_arr > usl)
        if lsl is not None: fail_mask |= (vals_arr < lsl)
        counts_fail = _np.bincount(idxs[fail_mask], minlength=n_bins).tolist()
        n3 = int(_np.sum(_np.abs(vals_arr - mean_v) > 3*sigma)) if sigma > 0 else 0
        n6 = int(_np.sum(_np.abs(vals_arr - mean_v) > 6*sigma)) if sigma > 0 else 0
        n12 = int(_np.sum(_np.abs(vals_arr - mean_v) > 12*sigma)) if sigma > 0 else 0
        cp = round((usl - lsl) / (6 * sigma), 3) if (usl and lsl and sigma > 0) else None
        cpk = None
        if sigma > 0 and usl is not None and lsl is not None:
            cpk = round(min((usl - mean_v) / (3 * sigma), (mean_v - lsl) / (3 * sigma)), 3)
        # Phase 1: skip wfr_stats in Live Mode — JS rebuilds them from RAW_PIN_DATA
        wfr_stats = {}
        if not focus_mode and WFR_COL in df_prog.columns and LOT_COL in df_prog.columns:
            # Vectorized per-wafer stats using groupby + agg instead of Python loops
            ser_col = pd.to_numeric(df_prog[col], errors='coerce').dropna() * 1000
            tmp = df_prog.loc[ser_col.index, [LOT_COL, WFR_COL]].copy()
            tmp['_v'] = ser_col
            for (lot_k, wfr_k), grp in tmp.groupby([LOT_COL, WFR_COL]):
                gv = grp['_v'].values
                if not len(gv): continue
                gn = len(gv); gs = float(gv.sum()); gs2 = float((gv**2).sum())
                g3 = int(_np.sum(_np.abs(gv-mean_v)>3*sigma)) if sigma>0 else 0
                g6 = int(_np.sum(_np.abs(gv-mean_v)>6*sigma)) if sigma>0 else 0
                g12= int(_np.sum(_np.abs(gv-mean_v)>12*sigma)) if sigma>0 else 0
                gnf = 0
                if usl is not None: gnf += sum(1 for v in gv if v > usl)
                if lsl is not None: gnf += sum(1 for v in gv if v < lsl)
                wfr_stats[f'{lot_k}::{wfr_k}'] = {
                    'n': gn, 's': round(gs, 4), 's2': round(gs2, 4),
                    'n3': g3, 'n6': g6, 'n12': g12, 'nf': gnf}
        return {
            'col': col,
            'usl': usl, 'lsl': lsl,
            'bins': bins, 'counts': counts, 'counts_fail': counts_fail,
            'mean': round(mean_v, 3), 'sigma': round(sigma, 3),
            'median': round(median, 3), 'p1': round(p1, 3), 'p99': round(p99, 3),
            'p25': round(p25, 3), 'p75': round(p75, 3),
            'min_val': round(min_val, 3), 'max_val': round(max_val, 3),
            'n_total': n_total, 'n_fail': n_fail,
            'n3': n3, 'n6': n6, 'n12': n12, 'cp': cp, 'cpk': cpk,
            'wfr_stats': wfr_stats,
        }

    pin_distrib = {}
    detail_data = {}

    for pin, ldata in lim.items():
        phase_col_map = pin_phase_cols.get(pin, {})
        if not phase_col_map: continue
        phases_data = {}
        for ph_label in _PHASE_ORDER:
            if ph_label not in phase_col_map: continue
            col = phase_col_map[ph_label][0]
            # Use phase-specific limits for this column's flow_kw
            _pcol = parse_col(col)
            _fkw = _pcol[2] if _pcol else None
            _plim = lim_by_flow.get(_fkw, lim).get(pin, lim.get(pin, {}))
            usl_raw = _plim.get('usl')
            lsl_raw = _plim.get('lsl')
            usl = round(usl_raw * 1000, 4) if usl_raw is not None else None
            lsl = round(lsl_raw * 1000, 4) if lsl_raw is not None else None
            stats = _phase_stats(col, usl, lsl)
            if stats: phases_data[ph_label] = stats
        if not phases_data: continue
        phase_list = [p for p in _PHASE_ORDER if p in phases_data]
        # Top-level usl/lsl: use START phase limits (Pre-Surge) for the modal header display
        _start_ph = next((p for p in phase_list if 'Pre-Surge' in p or 'Post-Surge' in p), phase_list[0])
        _start_lim = lim.get(pin, {})  # START limits for the header
        _top_usl = round(_start_lim['usl'] * 1000, 4) if _start_lim.get('usl') is not None else None
        _top_lsl = round(_start_lim['lsl'] * 1000, 4) if _start_lim.get('lsl') is not None else None
        pin_distrib[pin] = {'usl': _top_usl, 'lsl': _top_lsl,
                            'phases': phases_data, 'phase_list': phase_list}
        if pin in top5_pins:
            detail_phases = {}
            for ph_label, (col, _) in phase_col_map.items():
                # Vectorized: avoid slow iterrows() — use pandas operations directly
                sub = df_prog[[col, LOT_COL, WFR_COL, X_COL, Y_COL]].copy() if all(
                    c in df_prog.columns for c in [LOT_COL, WFR_COL, X_COL, Y_COL]) else df_prog[[col]].copy()
                sub['_v'] = pd.to_numeric(sub[col], errors='coerce') * 1000
                sub = sub.dropna(subset=['_v'])
                if sub.empty: continue
                if LOT_COL in sub.columns:
                    li = sub[LOT_COL].astype(str).map(lot_idx_map).fillna(0).astype(int)
                else: li = pd.Series(0, index=sub.index)
                wfr = sub[WFR_COL].fillna(0).astype(int) if WFR_COL in sub.columns else pd.Series(0, index=sub.index)
                xv  = sub[X_COL].fillna(0).astype(int)  if X_COL  in sub.columns else pd.Series(0, index=sub.index)
                yv  = sub[Y_COL].fillna(0).astype(int)  if Y_COL  in sub.columns else pd.Series(0, index=sub.index)
                vals_mv = sub['_v'].round(2)
                rows = list(zip(li.tolist(), wfr.tolist(), xv.tolist(), yv.tolist(), vals_mv.tolist()))
                if rows: detail_phases[ph_label] = rows
            detail_data[pin] = detail_phases
            total_rows = sum(len(v) for v in detail_phases.values())
            print(f'  [distrib] {pin}: {total_rows} raw values, {len(detail_phases)} phases (detail pin)')
        else:
            fs = phases_data.get(phase_list[0], {})
            print(f'  [distrib] {pin}: {len(phase_list)} phases | sigma={fs.get("sigma","?")}mV Cpk={fs.get("cpk","?")}')

    return pin_distrib, detail_data


def _build_raw_pin_data(df_prog, lim, lim_by_flow, k_cols, parsed_cols, lot_idx_map):
    """Build raw per-die pin measurements for ALL limit pins (focus / Live Mode).
    raw[pin][phase_label] = [[lot_idx, wfr, x, y, val_mV], ...]
    Same row format as detail_data so JS can reuse the same scatter table.
    """
    _PHASE_ORDER = ['Pre-Surge', 'Post-Surge', 'Post-Surge-HT', 'Stress',
                    'SDS-Final', 'SDT-Start', 'SDT-Final', 'ISVM-EDC', 'OTHER']
    # Map pin -> {phase_label -> best_col}
    pin_phase_cols = {}
    for col in k_cols:
        p = parsed_cols.get(col)
        if not p: continue
        matched_pin = col_to_pin(col, lim)
        if not matched_pin: continue
        ph_label = phase_of(col)
        nn = int(df_prog[col].notna().sum())
        if nn == 0: continue
        prev = pin_phase_cols.setdefault(matched_pin, {}).get(ph_label)
        if prev is None or nn > prev[1]:
            pin_phase_cols[matched_pin][ph_label] = (col, nn)

    raw = {}
    _need_cols = [c for c in [LOT_COL, WFR_COL, X_COL, Y_COL] if c in df_prog.columns]
    for pin in lim:
        phase_col_map = pin_phase_cols.get(pin, {})
        if not phase_col_map: continue
        pin_phases = {}
        for ph_label in _PHASE_ORDER:
            if ph_label not in phase_col_map: continue
            col = phase_col_map[ph_label][0]
            sub = df_prog[_need_cols + [col]].copy()
            sub['_v'] = pd.to_numeric(sub[col], errors='coerce') * 1000
            sub = sub.dropna(subset=['_v'])
            if sub.empty: continue
            li  = sub[LOT_COL].astype(str).map(lot_idx_map).fillna(0).astype(int) if LOT_COL in sub.columns else pd.Series(0, index=sub.index)
            wfr = sub[WFR_COL].fillna(0).astype(int) if WFR_COL in sub.columns else pd.Series(0, index=sub.index)
            xv  = sub[X_COL].fillna(0).astype(int)   if X_COL  in sub.columns else pd.Series(0, index=sub.index)
            yv  = sub[Y_COL].fillna(0).astype(int)   if Y_COL  in sub.columns else pd.Series(0, index=sub.index)
            rows = list(zip(li.tolist(), wfr.tolist(), xv.tolist(), yv.tolist(),
                            sub['_v'].round(2).tolist()))
            if rows: pin_phases[ph_label] = rows
        if pin_phases:
            raw[pin] = pin_phases
    print(f'  [focus] RAW_PIN_DATA: {len(raw)} pins embedded')
    return raw


def main():
    global PROG_61C, _JSON_DIR_61C

    _DEFAULT_SETUP = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'setup.json')

    ap = argparse.ArgumentParser()
    ap.add_argument('--csv',       default=None)
    ap.add_argument('--json',      default=None)
    ap.add_argument('--out',       default=None)
    ap.add_argument('--prog',      default=None,
                    help='Single program directory (used when CSV has 1 program or as fallback).')
    ap.add_argument('--prog-root', default=None, dest='prog_root',
                    help='Root folder containing one subfolder per program name.')
    ap.add_argument('--setup',       default=_DEFAULT_SETUP)
    ap.add_argument('--focus-wafers', default=None, dest='focus_wafers', type=int,
                    help='Auto-embed raw pin data when wafer count <= N (default 50). 0 = never auto.')
    ap.add_argument('--no-gui',       action='store_true', dest='no_gui',
                    help='Skip the run-options dialog; use auto-threshold only.')
    ap.add_argument('--live-mode',    action='store_true', dest='live_mode',
                    help='Force Live Mode (embed raw pin data) regardless of wafer count.')
    args = ap.parse_args()

    saved = {}
    if os.path.isfile(args.setup):
        try:
            with open(args.setup, encoding='utf-8') as _sf: saved=json.load(_sf)
            print(f'[setup] Loaded: {args.setup}')
        except Exception as _e: print(f'[setup] WARNING: {_e}')

    def _resolve(cli_val, saved_key, builtin_default):
        if cli_val is not None: return cli_val
        if saved.get(saved_key): return saved[saved_key]
        return builtin_default

    args.csv         = _resolve(args.csv,         'csv',          _DEFAULT_CSV)
    args.json        = _resolve(args.json,        'json',         _DEFAULT_JSON)
    args.out         = _resolve(args.out,         'out',          _DEFAULT_OUT)
    args.prog        = _resolve(args.prog,        'prog',         None)
    args.prog_root   = _resolve(args.prog_root,   'prog_root',    None)
    if args.focus_wafers is None:
        _saved_fw = saved.get('focus_wafers')
        args.focus_wafers = int(_saved_fw) if _saved_fw is not None else 50

    # backward compat: set PROG_61C for any code that still refs it
    if args.prog:
        PROG_61C = args.prog.strip().strip('"')
    elif args.prog_root:
        PROG_61C = args.prog_root
    else:
        PROG_61C = _pick_prog_dir(_DEFAULT_PROG)
    _JSON_DIR_61C = os.path.join(PROG_61C, 'Modules', 'TPI_VCC', 'InputFiles') if PROG_61C else ''
    print(f'[prog] fallback: {PROG_61C or "(none)"}')
    if args.prog_root: print(f'[prog] prog_root: {args.prog_root}')

    # Suggest prog_root if not set
    if not args.prog_root and PROG_61C:
        _suggested_root = os.path.dirname(PROG_61C)
        print(f'  [prog] TIP: Set --prog-root "{_suggested_root}" so each program loads its own limits.')

    print('Loading', os.path.basename(args.csv), '...')
    df = pd.read_csv(args.csv, low_memory=False)
    df = _merge_material(df, _MATERIAL_DIR)

    # ── Focus / Live Mode detection ───────────────────────────────────────────
    _n_wafers = df[WFR_COL].nunique() if WFR_COL in df.columns else 0
    if not args.no_gui:
        _opt = _show_run_options_dialog(_n_wafers, saved)
        _focus_mode    = _opt['focus_mode']
        _focus_wafers  = _opt['focus_wafers']
        args.focus_wafers = _focus_wafers        # persist updated threshold
    else:
        _focus_mode   = args.live_mode or ((_n_wafers <= args.focus_wafers) if args.focus_wafers > 0 else False)
        _focus_wafers = args.focus_wafers
    print(f'[focus] {_n_wafers} wafer(s) | threshold={_focus_wafers} | live_mode={_focus_mode}')

    # ── Split CSV by program and analyze each ────────────────────────────────
    unique_progs = sorted(df[PROG_COL].dropna().unique().tolist()) if PROG_COL in df.columns else ['(unknown)']
    print(f'  Programs: {unique_progs}')

    # Determine output directory and base name from args.out
    _out_abs  = os.path.abspath(args.out)
    # If args.out is a directory (or has no .html extension), use it as the output folder
    if os.path.isdir(_out_abs) or not _out_abs.lower().endswith('.html'):
        _out_dir  = _out_abs if os.path.isdir(_out_abs) else os.path.dirname(_out_abs)
        _out_base = 'vcccont-bin8-analysis'
    else:
        _out_dir  = os.path.dirname(_out_abs)
        _out_base = os.path.splitext(os.path.basename(_out_abs))[0]
    os.makedirs(_out_dir, exist_ok=True)
    # Clean output folder before regenerating to avoid stale cached HTML files
    import shutil as _shutil
    for _f in os.listdir(_out_dir):
        if _f.endswith('.html'):
            try: os.remove(os.path.join(_out_dir, _f))
            except Exception: pass
    print(f'  [out] Cleaned {_out_dir}')

    # ── Generate one HTML per program ─────────────────────────────────────────
    generated_files = {}   # pname → file path (basename only, for cross-links)
    for _pname in unique_progs:
        _safe  = re.sub(r'[^A-Za-z0-9_\-]', '_', _pname)
        if len(unique_progs) == 1:
            _out_file = os.path.join(_out_dir, _out_base + '.html')
        else:
            _out_file = os.path.join(_out_dir, f'{_out_base}-{_safe}.html')
        generated_files[_pname] = os.path.basename(_out_file)

    for _pname in unique_progs:
        try:
            _df_prog  = df[df[PROG_COL]==_pname].copy() if PROG_COL in df.columns else df.copy()
            _prog_dir = _resolve_prog_dir(_pname, args.prog_root, PROG_61C)
            r = analyze_program(_df_prog, _prog_dir, args_json=args.json, focus_mode=_focus_mode)

            # Per-program reticle map — derived from this program's devrevstep values
            import math as _math
            _ret_map_raw = load_reticle_map(_df_prog, _RETICLE_DIR, devrevstep_col=DEVREVSTEP_COL)
            print(f'  [reticle] {_pname}: {len(_ret_map_raw)} entries' if _ret_map_raw else f'  [reticle] {_pname}: none')
            _ret_js = {f'{k[0]},{k[1]}': list(v) for k,v in _ret_map_raw.items()}
            _wpa_ret_map = {f'{k[0]},{k[1]}': list(v) for k,v in _ret_map_raw.items()}
            _wpa_shot_boxes: dict = {}
            for (sx,sy),(rdx,rdy,si) in _ret_map_raw.items():
                b=_wpa_shot_boxes.setdefault(si,{'x0':sx,'y0':sy,'x1':sx,'y1':sy})
                b['x0']=min(b['x0'],sx); b['x1']=max(b['x1'],sx)
                b['y0']=min(b['y0'],sy); b['y1']=max(b['y1'],sy)
            _wpa_ret_shots=[[b['x0'],b['y0'],b['x1'],b['y1']] for _,b in sorted(_wpa_shot_boxes.items())]
            _wpa_site_shots: dict = {}
            for (sx,sy),(rdx,rdy,si) in _ret_map_raw.items():
                _wpa_site_shots.setdefault(f'{rdx},{rdy}',set()).add(si)
            _wpa_ret_site_totals={k:len(v) for k,v in _wpa_site_shots.items()}

            # Build per-program WPA (only this program's wafers)
            _wpa=WpaHtmlBuilder(fail_thr=TARGET_IBIN)
            if _wpa_ret_map:
                _wpa.set_global_reticle(reticle_map=_wpa_ret_map,reticle_shots=_wpa_ret_shots,
                                        reticle_site_totals=_wpa_ret_site_totals)
            _wfr_mat = {f"{w['lot']}|{w['prog']}|{w['wfr']}": w.get('material','') for w in r['wfr_list']}
            for wk,wdies in sorted(r['all_map'].items()):
                parts=wk.split('|')
                _wpa.add_wafer(key=f'{parts[1] if len(parts)>1 else ""}::{parts[2] if len(parts)>2 else ""}',
                               dies=[(d[0],d[1],d[2]) for d in wdies],
                               lot=parts[1] if len(parts)>1 else '',
                               wafer=parts[2] if len(parts)>2 else '',
                               material=_wfr_mat.get(wk,''),
                               program=parts[0] if parts else '')
            _wpa_html=_wpa.build(btn_label='&#128300; Pattern Analysis',trigger_id='cp-wpa-btn',
                                  standalone=False,watermark='Pant, Sujit N \u2014 GEMS FTE')

            # ── Focus / Live Mode: embed raw pin data for all limit pins ─────
            if _focus_mode:
                _lot_idx_map = {lot: i for i, lot in enumerate(r['lots'])}
                _raw_pin_data = _build_raw_pin_data(
                    df_prog=_df_prog, lim=r['lim'], lim_by_flow={},
                    k_cols=[c for c in _df_prog.columns if _COL_RE.match(c)],
                    parsed_cols={c: parse_col(c) for c in _df_prog.columns if _COL_RE.match(c)},
                    lot_idx_map=_lot_idx_map,
                )
            else:
                _raw_pin_data = {}

            html = build_html(
                prog_result      = r,
                all_progs        = None,
                current_prog     = _pname,
                reticle_json     = json.dumps(_ret_js, ensure_ascii=False, separators=(',',':')),
                csv_path         = args.csv,
                raw_pin_data     = _raw_pin_data,
                focus_wafer_count= _n_wafers if _focus_mode else 0,
            )
            html = html.replace('</body>', WAFERMAP_JS+'\n'+_POPUP_INIT_JS+'\n'+_wpa_html+'\n</body>', 1)
            _WM=('<div style="position:fixed;top:6px;right:10px;font-size:11px;'
                 'color:#ffffff;background:#6c3483;pointer-events:none;z-index:99999;'
                 'font-family:Arial,sans-serif;font-weight:bold;user-select:none;'
                 'padding:3px 10px;border-radius:12px;letter-spacing:0.03em;'
                 'box-shadow:0 2px 8px rgba(0,0,0,0.5);">Pant, Sujit N \u2014 GEMS FTE</div>')
            html=html[:html.rfind('</body>')]+'\n'+_WM+'\n</body>'+html[html.rfind('</body>')+len('</body>'):]
            _out_file = os.path.join(_out_dir, generated_files[_pname])
            with open(_out_file,'w',encoding='utf-8') as fh: fh.write(html)
            print(f'  [{_pname}] -> {os.path.basename(_out_file)} ({os.path.getsize(_out_file)//1024}KB)')
        except Exception as _prog_err:
            import traceback as _tb
            print(f'\n[ERROR] Program "{_pname}" failed — skipping.\n  {_prog_err}')
            _tb.print_exc()
            continue

    # ── Generate index.html (master page with sidebar + iframe) ──────────────
    if len(unique_progs) > 1:
        import html as _hm
        _first_file = generated_files[unique_progs[0]]
        _sidebar_links = ''
        for i, _pname in enumerate(unique_progs):
            _label = _hm.escape(os.path.basename(_pname) or _pname)
            _fname = generated_files[_pname]
            _color = '#4a9fd4' if i == 0 else '#667788'
            _bg    = '#1e3050' if i == 0 else 'transparent'
            _sidebar_links += (
                f'<a id="nav_{i}" href="#" '
                f'onclick="loadProg(\'{_fname}\',{i});return false;" '
                f'style="display:block;padding:7px 12px;font-size:0.75rem;font-weight:700;'
                f'text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
                f'color:{_color};background:{_bg};border-left:3px solid '
                f'{"#4a9fd4" if i==0 else "transparent"};transition:all 0.12s;" '
                f'title="{_hm.escape(_pname)}">{_label}</a>\n'
            )
        _index_html = f'''<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>VccCont BIN8 — Program Selector</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0f1a;display:flex;height:100vh;overflow:hidden;font-family:system-ui,sans-serif}}
#sidebar{{width:200px;flex-shrink:0;background:#0a0f1a;border-right:2px solid #1e3050;
          display:flex;flex-direction:column;overflow-y:auto;height:100vh}}
#sidebar-hdr{{padding:14px 12px 8px;font-size:0.65rem;color:#334455;
              text-transform:uppercase;letter-spacing:.08em;font-weight:700;
              border-bottom:1px solid #1e3050;margin-bottom:4px}}
#csv-info{{padding:4px 12px 8px;font-size:0.68rem;color:#445566;border-bottom:1px solid #1e3050;margin-bottom:4px}}
#main-frame{{flex:1;border:none;height:100vh}}
</style>
</head>
<body>
<div id="sidebar">
  <div id="sidebar-hdr">Test Program</div>
  <div id="csv-info">{_hm.escape(os.path.basename(args.csv))}</div>
  {_sidebar_links}
</div>
<iframe id="main-frame" src="{_hm.escape(_first_file)}" title="Dashboard"></iframe>
<script>
var _navCount = {len(unique_progs)};
var _activeTab = 'overview';
function loadProg(file, idx) {{
  // Capture the active tab from the current iframe before navigating
  try {{
    var fr = document.getElementById('main-frame');
    var curTab = fr.contentWindow && fr.contentWindow._activeTabId;
    if(curTab) _activeTab = curTab;
  }} catch(e) {{}}
  var frame = document.getElementById('main-frame');
  frame.onload = function() {{
    try {{
      var win = frame.contentWindow;
      if(_activeTab && _activeTab !== 'overview' && win && win.showTab) {{
        // Find the matching tab button in the loaded page
        var btns = win.document.querySelectorAll('.tab-btn');
        var btn = null;
        btns.forEach(function(b) {{
          if(b.getAttribute('onclick') && b.getAttribute('onclick').indexOf("'"+_activeTab+"'") >= 0) btn = b;
        }});
        if(btn) win.showTab(_activeTab, btn);
      }}
    }} catch(e) {{}}
  }};
  frame.src = file;
  for(var i=0;i<_navCount;i++) {{
    var a = document.getElementById('nav_'+i);
    if(!a) continue;
    var active = (i===idx);
    a.style.color = active?'#4a9fd4':'#667788';
    a.style.background = active?'#1e3050':'transparent';
    a.style.borderLeftColor = active?'#4a9fd4':'transparent';
  }}
}}
</script>
</body></html>'''
        _index_path = os.path.join(_out_dir, 'index.html')
        with open(_index_path, 'w', encoding='utf-8') as fh: fh.write(_index_html)
        print(f'  index.html -> {_index_path}')
        args.out = _index_path   # GUI opens index.html for multi-program
    else:
        # Update args.out to point to first program's file (for GUI to open)
        args.out = os.path.join(_out_dir, generated_files[unique_progs[0]])
    print('Dashboard written:',args.out,' ('+str(os.path.getsize(args.out)//1024)+'KB)')



def build_report_html(dies, pin_list, kill_list, wfr_list, fb_list, lim,
                       total_dies, bin8_count, pass_count, lots, progs):
    """Generate static HTML for the Report tab: summary, phase breakdown, EDC, findings, recommendations."""
    import html as _h
    import datetime

    PHASE_ORDER  = ['ISVM-EDC', 'Pre-Surge', 'Post-Surge', 'Stress', 'SDS-Final', 'SDT-Start', 'SDT-Final']
    PHASE_COLORS = {
        'ISVM-EDC': '#5aabff', 'Pre-Surge': '#4ecdc4', 'Post-Surge': '#48cae4',
        'Stress': '#ffd166', 'SDS-Final': '#ff6b6b', 'SDT-Start': '#c77dff', 'SDT-Final': '#a06fdd',
    }

    # --- Phase breakdown from dies ---
    phase_kills = {}
    for d in dies:
        ph = d.get('phase', 'OTHER')
        if ph not in phase_kills:
            phase_kills[ph] = {'n': 0, 'pins': {}}
        phase_kills[ph]['n'] += 1
        for p in d.get('pins', []):
            pin = p['pin']
            phase_kills[ph]['pins'][pin] = phase_kills[ph]['pins'].get(pin, 0) + 1

    # --- EDC statistics from die['edc'] ---
    # edc = {cs: {n_fail, n_total, worst, usl, lsl}}  (n_fail = pins over limit for that CS)
    edc_die_count  = 0   # dies with at least one CS flagged
    edc_by_cs      = {}  # cs -> {n_dies, n_pins_total, worst_val}
    for d in dies:
        edc = d.get('edc') or {}
        die_has_edc = False
        for cs, cs_d in edc.items():
            if cs_d.get('n_fail', 0) > 0:
                die_has_edc = True
                if cs not in edc_by_cs:
                    edc_by_cs[cs] = {'n_dies': 0, 'n_pins': cs_d.get('n_total', 0), 'worst': 0.0}
                edc_by_cs[cs]['n_dies'] += 1
                w = cs_d.get('worst', 0) or 0
                if w > edc_by_cs[cs]['worst']:
                    edc_by_cs[cs]['worst'] = w
        if die_has_edc:
            edc_die_count += 1

    bin8_pct = bin8_count / total_dies * 100 if total_dies else 0
    pass_pct = pass_count / total_dies * 100 if total_dies else 0
    n_wafers = len(set(d['wfr'] for d in dies))

    dominant_phase = max(
        (p for p in phase_kills if p != 'ISVM-EDC'),
        key=lambda p: phase_kills[p]['n'], default='Post-Surge'
    )
    dominant_n   = phase_kills.get(dominant_phase, {}).get('n', 0)
    dominant_pct = dominant_n / bin8_count * 100 if bin8_count else 0

    worst_wfr = wfr_list[0] if wfr_list else None
    report_date = datetime.date.today().strftime('%Y-%m-%d')

    # --- Helpers ---
    tbl_style = 'width:100%;border-collapse:collapse;font-size:0.82rem'
    def hrow(*cells):
        return '<tr>' + ''.join(
            '<th style="padding:5px 10px;text-align:left;color:#556677;font-size:0.71rem;'
            'text-transform:uppercase;border-bottom:2px solid #2a4060">' + c + '</th>'
            for c in cells) + '</tr>'
    def drow(*cells):
        return '<tr>' + ''.join(
            '<td style="padding:4px 10px;border-bottom:1px solid #1e3050">' + c + '</td>'
            for c in cells) + '</tr>'
    def card(title, body, color='#4a9fd4', subtitle=''):
        sub_html = ('<div style="font-size:0.72rem;color:#556677;margin-bottom:8px">' + subtitle + '</div>'
                    if subtitle else '')
        return (
            '<div style="background:#141c2e;border:1px solid #1e3050;border-radius:8px;'
            'padding:14px 18px;margin-bottom:16px">'
            '<div style="font-size:0.85rem;font-weight:700;color:' + color + ';margin-bottom:4px;'
            'text-transform:uppercase;letter-spacing:.04em">' + title + '</div>'
            + sub_html + body + '</div>'
        )
    def pill(label, color):
        return ('<span style="font-size:0.72rem;padding:1px 7px;border-radius:3px;'
                'background:' + color + '22;color:' + color + '">' + _h.escape(label) + '</span>')
    def note_box(text, color='#4a9fd4'):
        return ('<div style="background:' + color + '11;border-left:3px solid ' + color + ';'
                'border-radius:0 4px 4px 0;padding:8px 12px;margin:8px 0;font-size:0.8rem;color:#c0ccd8">'
                + text + '</div>')

    # ── 1. SUMMARY ──────────────────────────────────────────────────────────────
    # Per-IBIN breakdown (8, 80, 89)
    _ibin_counts = {}
    for d in dies:
        _ibin_counts[d['ibin']] = _ibin_counts.get(d['ibin'], 0) + 1
    _ibin_detail = '  '.join(
        f'IB{ib}: {_ibin_counts[ib]:,}'
        for ib in sorted(_ibin_counts)
    )
    stat_items = [
        ('Total Dies', '{:,}'.format(total_dies), '#c0ccd8'),
        ('Fail IBINs (8/80/89)', '{:,}'.format(bin8_count), '#ff6b6b'),
        ('Fail %', '{:.2f}%'.format(bin8_pct), '#ff9966'),
        ('Pass %', '{:.1f}%'.format(pass_pct), '#4ecdc4'),
        ('Lots', str(len(lots)), '#8ab4d4'),
        ('Wafers w/ Fails', str(n_wafers), '#c77dff'),
        ('EDC Over-Range', str(edc_die_count), '#5aabff'),
    ]
    stat_boxes = ''.join(
        '<div style="background:#1a2235;border:1px solid #2a4060;border-radius:6px;'
        'padding:8px 16px;min-width:120px">'
        '<div style="font-size:0.7rem;color:#556677;text-transform:uppercase">' + k + '</div>'
        '<div style="font-size:1.4rem;font-weight:700;color:' + vc + '">' + v + '</div></div>'
        for k, v, vc in stat_items
    )
    # Per-IBIN breakdown row shown below stat boxes
    _ibin_breakdown_html = (
        '<div style="margin-top:8px;font-size:0.78rem;color:#8ab4d4">'
        '<b style="color:#ff9966">IBIN breakdown:</b>&nbsp;&nbsp;'
        + '&nbsp;&nbsp;&nbsp;'.join(
            '<b style="color:#ff6b6b">IB{}</b>: {:,} ({:.1f}%)'.format(
                ib, _ibin_counts[ib],
                _ibin_counts[ib] / bin8_count * 100 if bin8_count else 0)
            for ib in sorted(_ibin_counts)
        )
        + '</div>'
    )
    meta = (
        '<div style="margin-top:10px;font-size:0.8rem;color:#667788">'
        'Programs: ' + ' &nbsp;|&nbsp; '.join(_h.escape(p) for p in progs) + '<br>'
        'Lots: ' + ' &nbsp;|&nbsp; '.join(_h.escape(l) for l in lots) + '<br>'
        'Report generated: ' + report_date + '</div>'
    )
    conclusion = note_box(
        '<b style="color:#4ecdc4">&#x25cf; Conclusion</b>'
        '<ul style="margin:6px 0 0 0;padding-left:18px;line-height:2.0">'
        '<li>BIN8 yield loss (~5&ndash;17% per wafer) is driven by <b>elevated rail resistance</b> '
        'detected by PRESURGE and POSTSURGE VSIM.</li>'
        '<li><b>POSTSURGE K_START is the BIN8 kill port.</b></li>'
        '<li><b>ISVM</b> is EDC (engineering data collection) only &mdash; same defect correlation '
        'but <b>does not set BIN8</b>.</li>'
        '<li><b>PRESURGE K_START</b> is a <b>kill port</b> for 61A lots (T01/T02 wafers: W503, W504); '
        'PRESURGE E_START (61B/T03) is a pre-surge measurement port and does not set BIN8.</li>'
        '<li><b>VCCIA</b> is the <b>primary</b> failing rail &mdash; perimeter-ring supply; '
        '2.6&times; wafer median on W510, up to <b>71&times;</b> per-die on W507.</li>'
        '<li><b>VNNAON</b> is the <b>secondary</b> failing rail &mdash; up to <b>34&times;</b> on W510 '
        'far-edge (X&le;&minus;11).</li>'
        '<li><b>VCCR</b> is a <b>third, separate</b> failure path &mdash; ~52 unique BIN8 dies exceed '
        'PASS p99 (83.5&thinsp;m&#937;); interior rail; <i>distinct root cause from VCCIA/VNNAON</i>.</li>'
        '<li><b>VCCSRAM</b> is <b>not elevated</b> (1.0&times;, confirmed).</li>'
        '<li><b>VCCCORExATOMx</b> rails: median fold 1.0&times; (no systematic elevation); '
        'VCCCORE0ATOM3 shows <b>scattered tail exceedances</b> &mdash; 14/213 BIN8 POSTSURGE dies &gt;&nbsp;PASS&nbsp;p99 '
        '(6.6%), VCCATOM2/3 on W507/W511 similarly. Not a primary kill rail but worth monitoring.</li>'
        '<li>Hardware die file confirms <b>VCCIA is perimeter-only</b> (zero central pads) and '
        '<b>VCCR is central-only</b> (zero edge pads).</li>'
        '<li>VCCR failures <b>cannot be a wafer-edge probe artifact</b> &mdash; VCCR has no pads '
        'at the die edge by design.</li>'
        '<li>Fail modes span <b>at least 4 distinct signatures</b> &mdash; not a simple two-population split '
        '(see Fail Mode Classification below).</li>'
        '</ul>',
        '#4ecdc4'
    )
    stats_html = '<div style="display:flex;gap:12px;flex-wrap:wrap">' + stat_boxes + '</div>' + _ibin_breakdown_html + meta

    # ── 0. TOP CARD: derived entirely from data ──────────────────────────────────
    from collections import Counter as _Counter

    def _rail(pin): return pin.rsplit('_', 1)[-1]
    def _dps(pin):
        p = pin.upper()
        for g in ('VLC', 'HV', 'HC', 'LC'):
            if p.startswith(g): return g
        return '?'
    DPS_COL = {'VLC': '#5aabff', 'HV': '#ff6b6b', 'HC': '#ffcc44', 'LC': '#4ecdc4'}

    # Per-wafer X/rail/phase stats from dies
    _wfr_xs  = {}; _wfr_ys = {}; _wfr_rl = {}; _wfr_ph = {}
    for d in dies:
        w = d['wfr']
        _wfr_xs.setdefault(w, []).append(d['x'])
        _wfr_ys.setdefault(w, []).append(d['y'])
        if w not in _wfr_rl: _wfr_rl[w] = _Counter()
        if w not in _wfr_ph: _wfr_ph[w] = _Counter()
        _wfr_ph[w][d['phase']] += 1
        for p in d['pins']:
            _wfr_rl[w][_rail(p['pin'])] += 1

    # Compute overall edge threshold for display labels (from all-die max |x|)
    _all_x_abs  = [abs(d['x']) for d in dies] or [1]
    _max_x      = max(_all_x_abs)
    _edge_thr   = int(_max_x * 0.6)   # outer ~40% of X radius = "edge"

    # Augmented wafer stats — edge_pct from score_wafer() (module canonical)
    _wfr_stats = []
    for w in wfr_list:
        wnum = w['wfr']
        xs   = _wfr_xs.get(wnum, [])
        ys   = _wfr_ys.get(wnum, [])
        n    = w['count']
        mx   = sorted(xs)[len(xs) // 2] if xs else 0
        pat  = score_wafer(xs=xs, ys=ys)
        ep   = pat.edge_pct
        tr   = _wfr_rl[wnum].most_common(1)[0] if _wfr_rl.get(wnum) else ('?', 0)
        dp   = _wfr_ph[wnum].most_common(1)[0][0] if _wfr_ph.get(wnum) else '?'
        _wfr_stats.append({'wfr': wnum, 'lot': w['lot'], 'count': n,
                           'median_x': mx, 'edge_pct': ep,
                           'top_rail': tr[0], 'top_rail_n': tr[1], 'dom_phase': dp})
    _edge_wafers = [w for w in _wfr_stats if w['edge_pct'] >= 40]
    _top_rails   = pin_list[:12]

    # Kill port from kill_list (most common full kill name)
    _kill_port = 'K_START'
    for kl in kill_list:
        f = kl.get('full', '')
        if 'K_START' in f: _kill_port = 'K_START'; break
        if 'E_START' in f: _kill_port = 'E_START'; break

    # Build top-rail table
    _rail_rows = ''
    for rk in _top_rails:
        pin   = rk['pin']; rail = _rail(pin); dgrp = _dps(pin)
        cnt   = rk['count']; pct = cnt / bin8_count * 100 if bin8_count else 0
        bar_w = max(2, int(pct * 1.5))
        bar   = ('<div style="display:flex;align-items:center;gap:6px">'
                 '<div style="width:' + str(bar_w) + 'px;height:6px;background:#ff9966;border-radius:2px"></div>'
                 '<span>' + '{:.1f}%'.format(pct) + '</span></div>')
        ph_d  = max(rk['phases'], key=rk['phases'].get) if rk['phases'] else '?'
        ph_c  = PHASE_COLORS.get(ph_d, '#4a9fd4')
        dc    = DPS_COL.get(dgrp, '#8ab4d4')
        tw    = sorted([(w, _wfr_rl[w][rail]) for w in _wfr_rl
                        if _wfr_rl[w].get(rail, 0) > 0], key=lambda kv: -kv[1])[:3]
        wfr_s = ', '.join('W' + str(w) + '(' + str(c) + ')' for w, c in tw) or '&mdash;'
        _rail_rows += (
            '<tr>'
            '<td style="padding:5px 10px;border-bottom:1px solid #1e3050;color:#e0c040;font-weight:700">'
            + _h.escape(rail) + '</td>'
            '<td style="padding:5px 10px;border-bottom:1px solid #1e3050">'
            '<span style="background:' + dc + '22;color:' + dc + ';padding:1px 7px;'
            'border-radius:3px;font-size:0.72rem">' + dgrp + '</span></td>'
            '<td style="padding:5px 10px;border-bottom:1px solid #1e3050"><b>' + str(cnt) + '</b></td>'
            '<td style="padding:5px 10px;border-bottom:1px solid #1e3050">' + bar + '</td>'
            '<td style="padding:5px 10px;border-bottom:1px solid #1e3050">'
            '<span style="color:' + ph_c + ';font-size:0.78rem">' + _h.escape(ph_d) + '</span></td>'
            '<td style="padding:5px 10px;border-bottom:1px solid #1e3050;font-size:0.78rem;color:#8ab4d4">'
            + wfr_s + '</td></tr>'
        )
    _rail_tbl = (
        '<table style="' + tbl_style + ';margin-bottom:14px"><thead>'
        + hrow('Rail', 'DPS', 'Fail Dies', '% of Fails', 'Dominant Phase', 'Top Wafers (n)')
        + '</thead><tbody>' + _rail_rows + '</tbody></table>'
    )

    # Key finding bullets — fully data-driven
    _top_bullets = []
    _top_bullets.append(
        '<b>' + _h.escape(dominant_phase) + '</b> is the dominant kill phase: '
        '<b>' + '{:.1f}%'.format(dominant_pct) + '</b> of fail-IBIN kills '
        '(' + str(dominant_n) + '&thinsp;/&thinsp;' + str(bin8_count) + ' dies). '
        'Kill port: <b>' + _kill_port + '</b>. '
        'ISVM = EDC only &mdash; does <b>not</b> set IB8/80/89.'
    )
    if _top_rails:
        r1 = _top_rails[0]; r1n = _rail(r1['pin'])
        r1p = r1['count'] / bin8_count * 100 if bin8_count else 0
        r1h = max(r1['phases'], key=r1['phases'].get) if r1['phases'] else '?'
        rs  = ('<b>' + _h.escape(r1n) + '</b> is the top failing rail: '
               '<b>' + str(r1['count']) + ' dies</b> (' + '{:.1f}%'.format(r1p) + ' of fail IBINs), '
               'dominant phase: ' + _h.escape(r1h) + '.')
        if len(_top_rails) >= 2:
            r2 = _top_rails[1]
            rs += ('  <b>' + _h.escape(_rail(r2['pin'])) + '</b>: '
                   + str(r2['count']) + ' dies ('
                   + '{:.1f}%'.format(r2['count'] / bin8_count * 100 if bin8_count else 0) + '%).')
        if len(_top_rails) >= 3:
            rs += ('  Others: '
                   + ', '.join('<b>' + _h.escape(_rail(r['pin'])) + '</b>'
                               for r in _top_rails[2:5]) + '.')
        _top_bullets.append(rs)
    if _edge_wafers:
        ew = ', '.join('W' + str(w['wfr']) for w in _edge_wafers[:4])
        _top_bullets.append(
            '<b>' + str(len(_edge_wafers)) + ' wafer'
            + ('s' if len(_edge_wafers) > 1 else '') + '</b> show edge-biased fails (' + ew + '): '
            '&ge;40% of fail dies in outer columns (|X|&ge;' + str(_edge_thr) + '). '
            'Consistent with wafer-edge process variation.'
        )
    if edc_die_count:
        _top_bullets.append(
            '<b>' + str(edc_die_count) + ' of ' + str(bin8_count) + ' fail-IBIN dies</b> ('
            + '{:.1f}%'.format(edc_die_count / bin8_count * 100 if bin8_count else 0)
            + ') show ISVM EDC over-range &mdash; elevated resistance present <i>before</i> surge.'
        )
    if worst_wfr:
        _ws0 = next((w for w in _wfr_stats if w['wfr'] == worst_wfr['wfr']), {})
        _en  = (' Edge-biased ({:.0f}% in outer cols).'.format(_ws0['edge_pct'])
                if _ws0.get('edge_pct', 0) >= 40 else '')
        _top_bullets.append(
            'Worst wafer: <b>W' + str(worst_wfr['wfr']) + '</b> ('
            + _h.escape(worst_wfr['lot']) + '): <b>' + str(worst_wfr['count'])
            + '</b> fail-IBIN dies ('
            + '{:.1f}%'.format(worst_wfr['count'] / bin8_count * 100 if bin8_count else 0)
            + ' of total fails).' + _en
        )
    if len(lots) > 1:
        _top_bullets.append(
            'Data spans <b>' + str(len(lots)) + '</b> lots: '
            + ', '.join(_h.escape(l) for l in lots) + '.'
            + (' Programs: ' + ', '.join(_h.escape(p) for p in progs) + '.'
               if len(progs) > 1 else '')
        )
    top_card_html = (
        _rail_tbl
        + '<ul style="margin:0;padding-left:18px;line-height:1.9">'
        + ''.join('<li style="font-size:0.83rem;color:#c0ccd8;margin-bottom:4px">' + b + '</li>'
                  for b in _top_bullets if b)
        + '</ul>'
    )

    # ── 2. TEST METHOD ───────────────────────────────────────────────────────────
    method_html = (
        '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px">'
        + '<div style="background:#1a1010;border-left:3px solid #5aabff;padding:10px 14px;border-radius:0 6px 6px 0">'
          '<div style="font-size:0.77rem;font-weight:700;color:#5aabff;margin-bottom:4px">ISVM &mdash; EDC Only</div>'
          '<div style="font-size:0.8rem;color:#c0ccd8">Current-force / voltage-measure. IForce = 5&thinsp;mA per rail. '
          'R&nbsp;=&nbsp;V/I. Runs <i>before</i> PRESURGE. Collected as <b>Engineering Data Collection</b> &mdash; '
          'no pass/fail limits applied. <b>Does NOT set BIN8.</b> '
          'Elevated ISVM values identify the same underlying defect as PRESURGE/POSTSURGE.</div></div>'
        + '<div style="background:#1a1010;border-left:3px solid #4ecdc4;padding:10px 14px;border-radius:0 6px 6px 0">'
          '<div style="font-size:0.77rem;font-weight:700;color:#4ecdc4;margin-bottom:4px">PRESURGE &mdash; Diagnostic</div>'
          '<div style="font-size:0.8rem;color:#c0ccd8">VSIM (V-force / I-measure) before surge. '
          'Exit port: <b>E_START</b> (early-exit routing flag). Surfaces elevated resistance '
          'but is <b>diagnostic</b> &mdash; does not directly set BIN8. '
          'Sense voltages: VCCIA 25&thinsp;mV, VNNAON 50&thinsp;mV, VCCR 50&thinsp;mV.</div></div>'
        + '<div style="background:#1e1010;border-left:3px solid #ff6b6b;padding:10px 14px;border-radius:0 6px 6px 0">'
          '<div style="font-size:0.77rem;font-weight:700;color:#ff6b6b;margin-bottom:4px">POSTSURGE &mdash; BIN8 Kill</div>'
          '<div style="font-size:0.8rem;color:#c0ccd8">VSIM (V-force / I-measure) after surge. '
          'Exit port: <b>K_START</b> &mdash; this is the <b>BIN8 kill port</b>. '
          'Same rail set as PRESURGE. Elevated post-surge resistance &rarr; BIN8 assignment. '
          'VCCSRAM is <b>not elevated</b> (1.0&times;). VCCR shows elevation in ~52 unique BIN8 dies '
          '(28% of 192 unique) &mdash; a <b>separate interior-die defect signature</b>.</div></div>'
        + '</div>'
    )

    # ── 3. EDC SECTION ──────────────────────────────────────────────────────────
    edc_note = note_box(
        '<b style="color:#5aabff">ISVM EDC is measurement-only &mdash; it does NOT set IB8/80/89.</b> '
        'However, EDC over-range values identify the same high-resistance dies that are killed by POSTSURGE. '
        'The count below shows how many fail-IBIN dies also had EDC readings above the engineering reference limits '
        'at the time of test, providing a pre-surge resistance baseline.',
        '#5aabff'
    )
    if edc_by_cs:
        edc_rows = []
        for cs, cs_d in sorted(edc_by_cs.items(), key=lambda x: -x[1]['n_dies']):
            nd   = cs_d['n_dies']
            pct  = nd / bin8_count * 100 if bin8_count else 0
            bar_w = max(2, int(pct * 1.5))
            bar   = ('<div style="display:flex;align-items:center;gap:6px">'
                     '<div style="width:' + str(bar_w) + 'px;height:7px;background:#5aabff;border-radius:2px"></div>'
                     '<span>' + '{:.1f}%'.format(pct) + ' of fails</span></div>')
            w_str = ('{:.2f} m&#937;'.format(cs_d['worst']) if cs_d.get('worst') else '&mdash;')
            edc_rows.append(drow(
                '<span style="color:#5aabff;font-weight:700">' + _h.escape(cs) + '</span>',
                '<b>' + str(nd) + '</b> / ' + str(bin8_count),
                bar,
                w_str,
            ))
        edc_tbl = ('<table style="' + tbl_style + '"><thead>' +
                   hrow('ConfigSet (CS)', 'Dies Over EDC Ref', '% of Fails', 'Worst Measured') +
                   '</thead><tbody>' + ''.join(edc_rows) + '</tbody></table>')
        edc_summary = ('<div style="font-size:0.82rem;color:#c0ccd8;margin-bottom:8px">'
                       '<b style="color:#5aabff">' + str(edc_die_count) + '</b> of '
                       '<b>' + str(bin8_count) + '</b> fail-IBIN dies (' +
                       '{:.1f}%'.format(edc_die_count / bin8_count * 100 if bin8_count else 0) +
                       ') have at least one EDC reading above the engineering reference limit.</div>')
        edc_html = edc_note + edc_summary + edc_tbl
    else:
        edc_html = (edc_note +
                    '<div style="color:#445566;font-size:0.82rem;padding:8px 0">'
                    'No EDC limit data available &mdash; ISVM JSON not loaded or no over-range readings.</div>')

    # ── 4. PHASE BREAKDOWN ───────────────────────────────────────────────────────
    ph_rows = []
    for ph in PHASE_ORDER:
        pk  = phase_kills.get(ph, {'n': 0, 'pins': {}})
        n   = pk['n']
        is_edc = (ph == 'ISVM-EDC')
        pct = n / bin8_count * 100 if bin8_count and not is_edc else 0
        bar_w = max(0, int(pct * 1.2))
        col = PHASE_COLORS.get(ph, '#4a9fd4')
        if is_edc:
            bar = '<span style="font-size:0.75rem;color:#5aabff;font-style:italic">EDC only &mdash; not a kill</span>'
        else:
            bar = ('<div style="display:flex;align-items:center;gap:6px">'
                   '<div style="width:' + str(bar_w) + 'px;height:8px;background:' + col + ';border-radius:2px;min-width:2px"></div>'
                   '<span>' + '{:.1f}%'.format(pct) + '</span></div>')
        top3 = sorted(pk['pins'].items(), key=lambda x: -x[1])[:3]
        top3_str = ', '.join(p + '(' + str(c) + ')' for p, c in top3) if top3 else '&mdash;'
        ph_rows.append(drow(
            '<span style="color:' + col + ';font-weight:700">' + _h.escape(ph) + '</span>',
            '<b>' + str(n) + '</b>' + (' <span style="font-size:0.7rem;color:#5aabff">(EDC)</span>' if is_edc else ''),
            bar,
            '<span style="font-size:0.75rem;color:#8ab4d4">' + top3_str + '</span>',
        ))
    phase_tbl = ('<table style="' + tbl_style + '"><thead>' +
                 hrow('Phase', 'Fail Kills', '% of Fail Kills', 'Top Pins') +
                 '</thead><tbody>' + ''.join(ph_rows) + '</tbody></table>')

    # ── 5. TOP FAILING PINS ──────────────────────────────────────────────────────
    pin_rows = []
    for pp in pin_list[:15]:
        ph_pills = ' '.join(
            pill(ph + ':' + str(c), PHASE_COLORS.get(ph, '#4a9fd4'))
            for ph, c in sorted(pp.get('phases', {}).items(), key=lambda x: -x[1])
        )
        pin_rows.append(drow(
            '<code style="color:#e0c040">' + _h.escape(pp['pin']) + '</code>',
            '<b>' + str(pp['count']) + '</b>',
            ph_pills,
        ))
    pin_tbl = ('<table style="' + tbl_style + '"><thead>' +
               hrow('Pin', 'Dies', 'Phase Distribution') +
               '</thead><tbody>' + ''.join(pin_rows) + '</tbody></table>')

    # ── 6. WORST WAFERS ──────────────────────────────────────────────────────────
    wfr_rows = []
    for w in wfr_list[:12]:
        wfr_rows.append(drow(
            '<b>W' + str(w['wfr']) + '</b>',
            _h.escape(w['lot']),
            '<b style="color:#ff9966">' + str(w['count']) + '</b>',
            '{:.1f}%'.format(w['count'] / bin8_count * 100 if bin8_count else 0),
        ))
    wfr_tbl = ('<table style="' + tbl_style + '"><thead>' +
               hrow('Wafer', 'Lot', 'Fail Count', '% of Total Fails') +
               '</thead><tbody>' + ''.join(wfr_rows) + '</tbody></table>')

    # ── 7. PER-WAFER FAIL ANALYSIS ───────────────────────────────────────────────
    _pw_rows = ''
    for ws in _wfr_stats[:15]:
        ep   = ws['edge_pct']
        ec   = '#ff6b6b' if ep >= 50 else ('#ffcc44' if ep >= 25 else '#4ecdc4')
        ew   = max(1, int(ep * 0.8))
        ebar = ('<div style="display:flex;align-items:center;gap:5px">'
                '<div style="width:' + str(ew) + 'px;height:6px;background:' + ec + ';border-radius:2px"></div>'
                '<span style="color:' + ec + '">' + '{:.0f}%'.format(ep) + '</span></div>')
        xv   = ws['median_x']
        xc   = '#ff6b6b' if abs(xv) >= _edge_thr else '#c0ccd8'
        phc  = PHASE_COLORS.get(ws['dom_phase'], '#4a9fd4')
        _pw_rows += drow(
            '<b>W' + str(ws['wfr']) + '</b>',
            _h.escape(ws['lot']),
            '<b style="color:#ff9966">' + str(ws['count']) + '</b>',
            ebar,
            '<span style="color:' + xc + '">' + ('+' if xv > 0 else '') + str(xv) + '</span>',
            '<code style="color:#e0c040">' + _h.escape(ws['top_rail']) + '</code>'
            + ' <span style="font-size:0.74rem;color:#556677">(' + str(ws['top_rail_n']) + ')</span>',
            '<span style="color:' + phc + ';font-size:0.78rem">' + _h.escape(ws['dom_phase']) + '</span>',
        )
    pop_html = (
        '<table style="' + tbl_style + '"><thead>'
        + hrow('Wafer', 'Lot', 'Fails', 'Edge Skew', 'Median X', 'Top Rail (n)', 'Kill Phase')
        + '</thead><tbody>' + _pw_rows + '</tbody></table>'
        + note_box(
            'Edge Skew = % of fail-IBIN dies with |X| &ge; ' + str(_edge_thr)
            + ' (outer ~40% of wafer X range).  '
            '<span style="color:#ff6b6b">&#x25cf;</span> &ge;50% &nbsp;'
            '<span style="color:#ffcc44">&#x25cf;</span> &ge;25% &nbsp;'
            '<span style="color:#4ecdc4">&#x25cf;</span> &lt;25%.',
            '#556677')
    )

    # ── 8. FINDINGS — data-driven ────────────────────────────────────────────────
    findings = []
    findings.append(
        '<b>' + _h.escape(dominant_phase) + '</b> is the dominant kill phase ('
        + '{:.1f}%'.format(dominant_pct) + '% of fail-IBIN kills, '
        + str(dominant_n) + '&thinsp;/&thinsp;' + str(bin8_count) + ' dies). '
        'Kill port: <b>' + _kill_port + '</b>. ISVM is EDC only &mdash; does not set IB8/80/89.'
    )
    for rk in _top_rails[:5]:
        pin  = rk['pin']; rail = _rail(pin); dgrp = _dps(pin)
        cnt  = rk['count']; pct = cnt / bin8_count * 100 if bin8_count else 0
        ph_d = max(rk['phases'], key=rk['phases'].get) if rk['phases'] else '?'
        tw   = sorted([(w, _wfr_rl[w][rail]) for w in _wfr_rl
                       if _wfr_rl[w].get(rail, 0) > 0], key=lambda kv: -kv[1])[:3]
        wn   = ', '.join('W' + str(w) + '&nbsp;(' + str(c) + ')' for w, c in tw)
        findings.append(
            '<b>' + _h.escape(rail) + '</b> [' + dgrp + ']: <b>' + str(cnt)
            + ' fail-IBIN dies</b> (' + '{:.1f}%'.format(pct) + '%). '
            'Dominant phase: ' + _h.escape(ph_d) + '.'
            + ('  Top wafers: ' + wn + '.' if wn else '')
        )
    if edc_die_count:
        findings.append(
            '<b>' + str(edc_die_count) + ' of ' + str(bin8_count) + ' fail-IBIN dies</b> ('
            + '{:.1f}%'.format(edc_die_count / bin8_count * 100 if bin8_count else 0)
            + ') show ISVM EDC readings above reference limits &mdash; '
            'elevated resistance confirmed <i>before</i> surge.'
        )
    if _edge_wafers:
        ew = ', '.join('W' + str(w['wfr']) for w in _edge_wafers[:5])
        findings.append(
            '<b>Edge-biased fail-IBIN distribution</b> on '
            + str(len(_edge_wafers)) + ' wafer'
            + ('s' if len(_edge_wafers) > 1 else '') + ': ' + ew + '. '
            '&ge;40% of fail-IBIN dies in outer die columns (|X|&ge;' + str(_edge_thr) + '). '
            'Possible root cause: wafer-edge process variation (CMP, deposition, probe pressure).'
        )
    if worst_wfr:
        findings.append(
            'Worst wafer: <b>W' + str(worst_wfr['wfr']) + '</b> ('
            + _h.escape(worst_wfr['lot']) + '): <b>'
            + str(worst_wfr['count']) + ' fail-IBIN dies</b> ('
            + '{:.1f}%'.format(worst_wfr['count'] / bin8_count * 100 if bin8_count else 0) + ' of total fails).'
        )
    if pin_list:
        top5 = ', '.join('<code>' + _h.escape(_rail(p['pin'])) + '</code>' for p in pin_list[:5])
        findings.append('Top 5 failing rails by fail-IBIN count: ' + top5 + '.')
    if len(lots) > 1:
        findings.append(
            'Data spans <b>' + str(len(lots)) + '</b> lots (' + ', '.join(_h.escape(l) for l in lots) + ').'
            + (' Programs: ' + ', '.join(_h.escape(p) for p in progs) + '.'
               if len(progs) > 1 else '')
        )
    findings_html = ('<ul style="margin:0;padding-left:18px;line-height:1.9">' +
                     ''.join('<li style="font-size:0.83rem;color:#c0ccd8;margin-bottom:4px">' + f + '</li>'
                             for f in findings) + '</ul>')

    # ── 9. RECOMMENDATIONS — data-driven ─────────────────────────────────────────
    recs = []
    _seen_dps = set()
    for rk in _top_rails[:8]:
        pin  = rk['pin']; rail = _rail(pin); dgrp = _dps(pin)
        cnt  = rk['count']; pct = cnt / bin8_count * 100 if bin8_count else 0
        if dgrp in _seen_dps: continue
        _seen_dps.add(dgrp)
        _ew = [w['wfr'] for w in _wfr_stats if w['top_rail'] == rail and w['edge_pct'] >= 40]
        if _ew:
            recs.append(
                '<b>[' + dgrp + ' &mdash; ' + _h.escape(rail) + '] Edge-biased pattern '
                '(' + ', '.join('W' + str(w) for w in _ew[:4]) + '):</b> '
                'Fail dies concentrated at outer die columns &mdash; '
                'investigate wafer-level process uniformity: CMP thickness, '
                'deposition gradient at wafer edge, probe pressure variation. '
                'Compare fail-IBIN clock position across wafers: consistent clock position '
                '= wafer-level systematic; random = per-wafer excursion.'
            )
        else:
            recs.append(
                '<b>[' + dgrp + ' &mdash; ' + _h.escape(rail) + '] Distributed pattern '
                '(' + str(cnt) + ' dies, ' + '{:.1f}%'.format(pct) + '%):</b> '
                'No strong edge bias. '
                'Investigate per-die or package-level defects: '
                'bump integrity, underfill voiding, or solder joint resistance on the '
                + dgrp + ' DPS rail. '
                'Map fail-IBIN die X/Y coordinates &mdash; '
                'if specific die positions repeat across wafers, suspect a reticle-level systematic.'
            )
    if edc_die_count:
        recs.append(
            '<b>EDC pre-surge baseline (' + str(edc_die_count) + ' dies):</b> '
            'Plot ISVM voltage vs. POSTSURGE voltage per-die to quantify surge-induced delta. '
            'High pre-surge + high post-surge = pre-existing defect. '
            'Normal pre-surge + high post-surge = surge-induced degradation.'
        )
    if len(progs) > 1:
        recs.append(
            '<b>Deduplicate re-run wafers</b> before yield reporting: '
            + str(len(progs)) + ' programs in this dataset ('
            + ', '.join(_h.escape(p) for p in progs) + '). '
            'Use the latest program result for each wafer to avoid double-counting fail-IBIN failures.'
        )
    recs.append(
        '<b>Cross-check with die bump layout:</b> '
        'Map fail-IBIN X/Y positions against bump layout (P1 large vs. P2 small bumps) per DPS group. '
        'Bumps at die edges vs. interior determine whether failures are '
        'probe artifacts or true rail resistance elevation.'
    )
    rec_html = ('<ul style="margin:0;padding-left:18px;line-height:1.9">' +
                ''.join('<li style="font-size:0.83rem;color:#c0ccd8;margin-bottom:8px">' + r + '</li>'
                        for r in recs) + '</ul>')

    # ── Layout ──────────────────────────────────────────────────────────────────
    def two_col(a, b):
        return ('<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:0">'
                + a + b + '</div>')

    return (
        card('Key Findings', findings_html, '#4ecdc4') +
        card('Recommendations', rec_html, '#ffd166') +
        card('BIN8 Fail Summary &mdash; Top Rails', top_card_html, '#ff9966',
             'Derived from this dataset. Scroll down for phase breakdown, EDC correlation, and wafer detail.') +
        card('Summary', stats_html) +
        card('Test Method: ISVM / PRESURGE / POSTSURGE', method_html, '#48cae4',
             'Understanding which tests are diagnostic vs. kill is critical for root-cause analysis.') +
        card('ISVM EDC &mdash; Measurement-Only Correlation', edc_html, '#5aabff',
             'EDC over-range identifies dies with elevated pre-surge resistance. Not a BIN8 kill.') +
        card('BIN8 Kills by Test Phase', phase_tbl, '#48cae4') +
        two_col(
            card('Top Failing Pins (by die count)', pin_tbl, '#e0c040'),
            card('Worst Wafers by BIN8 Count', wfr_tbl, '#c77dff'),
        ) +
        card('Per-Wafer Fail Analysis', pop_html, '#ff9966',
             'Edge Skew and top failing rail per wafer, derived from this dataset.')
    )


def build_flow_data(dies, force_by_cs=None, lim=None, lim_by_flow=None, prog_dir=None, force_all_levels=None):
    """Parse FLW + MTPL(regex) to build ordered flow structure with kill stats."""
    import xml.etree.ElementTree as ET
    from collections import defaultdict as _dd

    if force_by_cs is None:
        force_by_cs = {}
    if lim is None:
        lim = {}
    if lim_by_flow is None:
        lim_by_flow = {}
    if force_all_levels is None:
        force_all_levels = {}

    PROG = prog_dir or ''
    flw_path  = os.path.join(PROG, 'Modules', 'TPI_VCC', 'TPI_VCC.flw')  if PROG else ''
    mtpl_path = os.path.join(PROG, 'Modules', 'TPI_VCC', 'TPI_VCC.mtpl') if PROG else ''
    lvl_path  = os.path.join(PROG, 'LevelsSequences.lvl')                 if PROG else ''
    json_dir  = os.path.join(PROG, 'Modules', 'TPI_VCC', 'InputFiles')    if PROG else ''

    # Parse mtpl → per-instance test setup (no fallback)
    _all_inst = _parse_mtpl_test_setup(mtpl_path)

    # Build per-group test_setup:
    # {grp_id: {cs: {levels_tc, config_file, config_set, pin_forces, json_override}}}
    # Uses the FLW group_tests list for direct instance lookup — no keyword guessing.

    def _build_group_test_setup(grp_id):
        """Return {cs: {levels_tc, config_file, config_set, pin_forces, json_override}}
        by looking up the FLW group's test list directly in the parsed MTPL instances."""
        if not _all_inst:
            return {}
        tests_in_group = group_tests.get(grp_id, [])
        if not tests_in_group:
            return {}
        result = {}
        for inst_name in tests_in_group:
            inst = _all_inst.get(inst_name)
            if not inst:
                continue
            cs = next((c for c in ('VLC', 'LC', 'HC', 'HV') if f'CONT_{c}DPS' in inst_name), None)
            if not cs or cs in result:
                continue
            lvl_name    = inst.get('levels_tc', '')
            cfg_file    = inst.get('config_file', '')
            cfg_set     = inst.get('config_set', '')
            pin_forces  = _parse_lvl_force_per_pin(lvl_path, lvl_name) if lvl_name else {}
            json_path   = os.path.join(json_dir, cfg_file) if cfg_file else ''
            json_over, pin_lim = _parse_json_config_set(json_path, cfg_set) if json_path else ({}, {})
            # Merge forces: JSON overrides lvl
            merged = dict(pin_forces)
            merged.update(json_over)
            result[cs] = {
                'levels_tc':      lvl_name,
                'config_file':    cfg_file,
                'config_set':     cfg_set,
                'pin_forces':     merged,       # {pin: force_str}
                'pin_limits':     pin_lim,       # {pin: (lsl_v, usl_v)}
                'json_override':  bool(json_over),
                'json_override_pins': list(json_over.keys()),
            }
        return result

    def _force_for_group(grp_id):
        """Return flat {cs: representative_force_str} for backward-compat display."""
        setup = _build_group_test_setup(grp_id)
        if setup:
            out = {}
            for cs, sd in setup.items():
                pf = sd.get('pin_forces', {})
                if pf:
                    out[cs] = next(iter(pf.values()))
            return out if out else force_by_cs
        return force_by_cs

    # Ordered flow: matches actual program execution order
    # Reference: vcccont_mimcap flow comment:
    #   Pre-Surge → Post-Surge(SDS-Start) → Stress → SDS-Final → SDT-Start → SDT-Final
    FLOW_ORDER = [
        ('ISVM_EDC',                    'ISVM-EDC',     '#5aabff'),
        ('VSIM_PRESURGE_600MV_V2',      'Pre-Surge',    '#4ecdc4'),
        ('VSIM_POSTSURGE_NOM_V2',       'Post-Surge',   '#48cae4'),
        ('VSIM_STRESS_V2',              'Stress',       '#ffd166'),
        ('VSIM_FINAL_V2',               'SDS-Final',    '#ff6b6b'),
        ('VSIM_SDTSTART_V2',            'SDT-Start',    '#c77dff'),
        ('VSIM_SDTFINAL_V2',            'SDT-Final',    '#a06fdd'),
    ]
    GROUP_LABELS = {
        'VSIM_PRESURGE_600MV_V2':      'Pre-Surge VSIM (600mV)',
        'VSIM_POSTSURGE_NOM_V2':       'Post-Surge VSIM (nom)',
        'VSIM_STRESS_V2':              'Stress VSIM',
        'VSIM_FINAL_V2':               'SDS-Final VSIM',
        'VSIM_SDTSTART_V2':            'SDT-Start VSIM',
        'VSIM_SDTFINAL_V2':            'SDT-Final VSIM',
        'ISVM_EDC':                    'EDC ISVM',
    }
    EDC_GROUPS = {'ISVM_EDC'}

    # 1. Parse FLW → group → test list
    group_tests = _dd(list)
    try:
        root = ET.parse(flw_path).getroot()
        for fi in root.findall('FlowItem'):
            nm = fi.get('name', '')
            if '::' not in nm or '.' not in nm: continue
            grp, tst = nm.split('::',1)[-1].split('.',1)
            group_tests[grp].append(tst)
    except Exception as e:
        print(f'  [flow] FLW parse error: {e}')

    # 2. Parse MTPL via regex (ET fails due to non-XML syntax)
    test_params = {}
    try:
        with open(mtpl_path, 'r', encoding='utf-8', errors='ignore') as f:
            mtpl_txt = f.read()
        for name, body in re.findall(r'<Test\s+name="(CONT_[^"]+)">(.*?)</Test>', mtpl_txt, re.DOTALL):
            lv  = (re.search(r'<(?:Levels|LevelsTc)>(.*?)</', body, re.I) or re.search(r'$','')  )
            ft  = (re.search(r'<(?:ForceType|force_type)>(.*?)</', body, re.I) or re.search(r'$',''))
            fv  = (re.search(r'<(?:ForceVoltage|ForceValue|forcingvoltage)>(.*?)</', body, re.I) or re.search(r'$',''))
            cfg = (re.search(r'<ConfigSet>(.*?)</', body, re.I) or re.search(r'$',''))
            test_params[name] = {
                'levels': lv.group(1).strip() if lv.lastindex else '',
                'force_type': ft.group(1).strip() if ft.lastindex else '',
                'force_val':  fv.group(1).strip() if fv.lastindex else '',
                'config_set': cfg.group(1).strip() if cfg.lastindex else '',
            }
    except Exception as e:
        print(f'  [flow] MTPL parse error: {e}')

    # 3. BIN8 kill stats per phase from dies data
    phase_kills = _dd(lambda: {'n': 0, 'pins': _dd(int), 'pin_fbs': _dd(lambda: _dd(int)), 'pin_vals': _dd(list)})
    # Die key sets per phase — used for cross-phase following-phase preview
    phase_die_sets = _dd(set)
    # Per-phase die pin-value map: {phase: {die_key: {pin: val_mV}}}
    phase_die_pin_vals = _dd(lambda: _dd(dict))
    for d in dies:
        ph = d.get('phase', 'OTHER')
        dk = d.get('k', '')
        phase_kills[ph]['n'] += 1
        phase_die_sets[ph].add(dk)
        seen_pins = set()
        for p in d.get('pins', []):
            pin = p['pin']
            phase_kills[ph]['pins'][pin] += 1
            phase_kills[ph]['pin_vals'][pin].append(p['val'])   # mV
            if pin not in seen_pins:
                phase_kills[ph]['pin_fbs'][pin][d['fbin']] += 1
                seen_pins.add(pin)
            # Store worst val per (die, pin) for cross-phase comparison
            prev = phase_die_pin_vals[ph][dk].get(pin)
            if prev is None or p['val'] > prev:
                phase_die_pin_vals[ph][dk][pin] = p['val']

    # Flow order for cross-phase preview (phase_label → lim_by_flow key)
    _FLOW_SEQ = [
        ('Pre-Surge',  'PRESURGE'),
        ('Post-Surge', 'POSTSURGE'),
        ('Stress',     'STRESS'),
        ('SDS-Final',  'FINAL'),
        ('SDT-Start',  'START'),
        ('SDT-Final',  'SDTFINAL'),
    ]
    _PHASE_SEQ_LABELS = [ph for ph, _ in _FLOW_SEQ]

    def _next_phase_exceed(current_phase_label):
        """For dies killed in current_phase, compute how many already exceed each
        subsequent phase's USL. Returns list of dicts (ordered, subsequent phases only)."""
        cur_idx = next((i for i, (p, _) in enumerate(_FLOW_SEQ) if p == current_phase_label), None)
        if cur_idx is None or cur_idx >= len(_FLOW_SEQ) - 1:
            return []
        die_pin_vals = phase_die_pin_vals.get(current_phase_label, {})
        if not die_pin_vals:
            return []
        result_list = []
        for subseq_ph, subseq_fkw in _FLOW_SEQ[cur_idx + 1:]:
            # Get limits for subsequent phase
            subseq_lim = lim_by_flow.get(subseq_fkw) or lim_by_flow.get(subseq_ph) or lim
            if not subseq_lim:
                continue
            exceed_dies = set()
            usl_vals = []
            for dk, pin_vals_map in die_pin_vals.items():
                for pin, val_mv in pin_vals_map.items():
                    pin_lim = subseq_lim.get(pin, {})
                    usl_raw = pin_lim.get('usl')
                    if usl_raw is None:
                        continue
                    usl_mv = round(usl_raw * 1000, 3)
                    usl_vals.append(usl_mv)
                    if val_mv > usl_mv:
                        exceed_dies.add(dk)
            if not usl_vals:
                continue  # no limits defined for this subsequent phase
            usl_min = round(min(usl_vals), 1)
            usl_max = round(max(usl_vals), 1)
            usl_str = (str(usl_min) + ' mV') if usl_min == usl_max else (str(usl_min) + '–' + str(usl_max) + ' mV')
            result_list.append({
                'phase':   subseq_ph,
                'n':       len(exceed_dies),
                'usl_str': usl_str,
                'usl_min': usl_min,
                'usl_max': usl_max,
            })
        return result_list

    # 4. Build output structure — each group is its own phase column
    result = {'phases': []}
    flw_parsed = bool(group_tests)  # False when FLW was inaccessible

    for grp_id, phase_label, phase_color in FLOW_ORDER:
        cont_tests = [t for t in group_tests.get(grp_id, []) if t.startswith('CONT_') and 'ALL_DC' not in t]
        if flw_parsed and grp_id not in group_tests:
            continue  # skip groups not in this program (only when FLW was actually read)

        pk = phase_kills.get(phase_label, {'n': 0, 'pins': {}, 'pin_fbs': {}, 'pin_vals': {}})
        top_pins = sorted(pk['pins'].items(), key=lambda x: x[1], reverse=True)
        pin_fbs  = pk.get('pin_fbs', {})
        pin_vals = pk.get('pin_vals', {})

        # EDC limits: per CS type, collect USL/LSL from lim dict (mV)
        edc_limits = {}
        if grp_id in EDC_GROUPS and lim:
            for pin, ldata in lim.items():
                cs = ldata.get('cs', '')
                if not cs: continue
                if cs not in edc_limits:
                    edc_limits[cs] = {
                        'usl': round(ldata['usl'] * 1000, 3) if ldata.get('usl') else None,
                        'lsl': round(ldata['lsl'] * 1000, 3) if ldata.get('lsl') else None,
                        'n_pins': 0,
                    }
                edc_limits[cs]['n_pins'] += 1

        next_exceed = _next_phase_exceed(phase_label)

        group_entry = {
            'id':              grp_id,
            'label':           GROUP_LABELS.get(grp_id, grp_id),
            'phase':           phase_label,
            'color':           phase_color,
            'n_tests':         len(cont_tests),
            'force':           _force_for_group(grp_id),
            'test_setup':      _build_group_test_setup(grp_id),
            'edc':             grp_id in EDC_GROUPS,
            'edc_limits':      edc_limits,
            'bin8_kills':      pk['n'],
            'next_phase_exceed': next_exceed,
            'top_pins':   [{'pin': p, 'n': n,
                             'fbs': dict((str(fb), c) for fb, c in pin_fbs.get(p, {}).items()),
                             'min_val': round(min(pin_vals.get(p, [0])), 2),
                             'max_val': round(max(pin_vals.get(p, [0])), 2),
                             'med_val': round(sorted(pin_vals.get(p, [0]))[len(pin_vals.get(p,[0]))//2], 2),
                             'usl': round(lim[p]['usl'] * 1000, 3) if p in lim and lim[p].get('usl') else None,
                             'lsl': round(lim[p]['lsl'] * 1000, 3) if p in lim and lim[p].get('lsl') else None}
                           for p, n in top_pins],
            # All pins from lim dict (includes 0-fail pins) — for the "All Pins" view
            'all_lim_pins': sorted([
                {'pin': p,
                 'n':   pk['pins'].get(p, 0),
                 'fbs': dict((str(fb), c) for fb, c in pin_fbs.get(p, {}).items()),
                 'min_val': round(min(pin_vals.get(p, [0])), 2) if pin_vals.get(p) else None,
                 'max_val': round(max(pin_vals.get(p, [0])), 2) if pin_vals.get(p) else None,
                 'med_val': round(sorted(pin_vals.get(p, [0]))[len(pin_vals.get(p,[0]))//2], 2) if pin_vals.get(p) else None,
                 'usl': round(v['usl'] * 1000, 3) if v.get('usl') else None,
                 'lsl': round(v['lsl'] * 1000, 3) if v.get('lsl') else None}
                for p, v in lim.items()
            ], key=lambda x: -x['n']),
        }

        result['phases'].append({'id': grp_id, 'label': phase_label, 'color': phase_color, 'groups': [group_entry]})

    total_kills = sum(p['bin8_kills'] for ph in result['phases'] for p in ph['groups'])
    result['flw_ok'] = flw_parsed
    print(f'  [flow] {len(result["phases"])} phases, {sum(len(p["groups"]) for p in result["phases"])} groups, {total_kills} die-kills attributed, flw_ok={flw_parsed}')
    return result


def build_html(prog_result, all_progs=None, current_prog=None,
               reticle_json='{}', csv_path='',
               plotly_src='https://cdn.plot.ly/plotly-2.35.2.min.js',
               raw_pin_data=None, focus_wafer_count=0):
    """Build a single-program dashboard HTML.
    prog_result       — result dict from analyze_program()
    all_progs         — {pname: filename} for sidebar nav links (None = no sidebar)
    current_prog      — the program name shown in this file
    raw_pin_data      — {pin: {phase: [[lot_idx,wfr,x,y,val_mV],...]}} for Live Mode
    focus_wafer_count — number of wafers embedded in raw_pin_data (0 = disabled)
    """
    import html as _html_mod

    r          = prog_result
    pk         = r.get('phase_kills', {})
    _lim       = r.get('lim', {})
    lots       = r['lots']
    progs      = r['progs']
    total_dies = r['total_dies']
    bin8_count = r['bin8_count']
    pass_count = r['pass_count']
    bin8_pct   = '{:.1f}'.format(bin8_count / total_dies * 100) if total_dies else '0'

    # Patch flow_data top_pins / all_lim_pins with full stats
    # Phase 3: read pre-computed pin_stats {min,max,med} instead of raw val lists
    def _tp(ph_data, lim):
        result = []
        for p, n in sorted(ph_data.get('pins',{}).items(), key=lambda x: -x[1]):
            ps   = ph_data.get('pin_stats',{}).get(p,{})
            pfbs = ph_data.get('pin_fbs',{}).get(p,{})
            result.append({'pin':p,'n':n,
                'fbs':{str(fb):c for fb,c in pfbs.items()},
                'min_val':ps.get('min'),'max_val':ps.get('max'),'med_val':ps.get('med'),
                'usl':round(lim[p]['usl']*1000,3) if p in lim and lim[p].get('usl') else None,
                'lsl':round(lim[p]['lsl']*1000,3) if p in lim and lim[p].get('lsl') else None})
        return result

    _fd = r['flow_data']
    if _fd and 'phases' in _fd:
        for _ph in _fd['phases']:
            for _grp in _ph.get('groups',[]):
                _ph_label = _grp.get('phase','')
                _ph_data  = pk.get(_ph_label, {})
                _grp['top_pins'] = _tp(_ph_data, _lim)
                _grp['all_lim_pins'] = [
                    {'pin':p,'n':_ph_data.get('pins',{}).get(p,0),
                     'fbs':{str(fb):c for fb,c in _ph_data.get('pin_fbs',{}).get(p,{}).items()},
                     'min_val':_ph_data.get('pin_stats',{}).get(p,{}).get('min'),
                     'max_val':_ph_data.get('pin_stats',{}).get(p,{}).get('max'),
                     'med_val':_ph_data.get('pin_stats',{}).get(p,{}).get('med'),
                     'usl':round(_lim[p]['usl']*1000,3) if p in _lim and _lim[p].get('usl') else None,
                     'lsl':round(_lim[p]['lsl']*1000,3) if p in _lim and _lim[p].get('lsl') else None}
                    for p in sorted(_lim, key=lambda x: -_ph_data.get('pins',{}).get(x,0))
                ]

    # ── Program sidebar (shows when all_progs has > 1 entry) ─────────────────
    if all_progs and len(all_progs) > 1:
        _sidebar_items = ''
        for pname, fname in all_progs.items():
            _label   = _html_mod.escape(os.path.basename(pname) or pname)
            _is_cur  = (pname == current_prog)
            _style   = ('background:#1e3050;color:#4a9fd4;border-left:3px solid #4a9fd4;'
                        if _is_cur else
                        'background:transparent;color:#667788;border-left:3px solid transparent;')
            _href    = fname if not _is_cur else '#'
            _onclick = '' if not _is_cur else ' onclick="return false"'
            _sidebar_items += (
                f'<a href="{_html_mod.escape(fname)}" {_onclick} '
                f'style="display:block;padding:7px 12px;font-size:0.75rem;font-weight:700;'
                f'text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
                f'transition:all 0.12s;{_style}" '
                f'title="{_html_mod.escape(pname)}">{_label}</a>'
            )
        sidebar_html = (
            '<div id="prog-sidebar" style="position:fixed;left:0;top:0;width:190px;height:100vh;'
            'background:#0a0f1a;border-right:2px solid #1e3050;overflow-y:auto;z-index:500">'
            '<div style="padding:8px 12px 4px;font-size:0.65rem;color:#334455;'
            'text-transform:uppercase;letter-spacing:.08em;font-weight:700">Test Program</div>'
            + _sidebar_items +
            '</div>'
        )
        wrap_style  = ''
        main_style  = 'margin-left:200px'
        prog_tab_html = '<div id="prog-tab-row" style="display:none"></div>'
    else:
        sidebar_html = ''
        wrap_style   = ''
        main_style   = ''
        prog_tab_html = '<div id="prog-tab-row" style="display:none"></div>'

    # ── Program info bar ──────────────────────────────────────────────────────
    _csv_name = os.path.basename(csv_path) if csv_path else ''
    _csv_dir  = os.path.dirname(csv_path)  if csv_path else ''
    _prog_info_html = (
        '<div id="prog-info-bar" style="background:#0a0f1a;border-bottom:1px solid #162030;'
        'padding:5px 20px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;'
        'font-size:0.74rem;color:#556677">'
        + (f'<span>&#128196;&nbsp;<span style="color:#8ab4d4">Program:</span>&nbsp;'
           f'<b style="color:#c0deff" title="{_html_mod.escape(r.get("prog_dir","")or "")}">'
           f'{_html_mod.escape(current_prog or "")}</b></span>')
        + (f'<span title="{_html_mod.escape(csv_path or "")}">'
           f'&#128190;&nbsp;<span style="color:#8ab4d4">CSV:</span>&nbsp;'
           f'<b style="color:#c0deff">{_html_mod.escape(_csv_name)}</b>'
           f'&nbsp;<span style="color:#334455">{_html_mod.escape(_csv_dir)}</span></span>'
           if _csv_name else '')
        + '</div>'
    )

    _plotly_tag = _PLOTLY_TAG
    print('  Plotly: inlined from', _PLOTLY_LOCAL)

    _html = (_HTML_TEMPLATE
        .replace('__PROG_SIDEBAR__',  sidebar_html)
        .replace('__MAIN_STYLE__',    main_style)
        .replace('__PROG_NAMES__',    json.dumps(list(all_progs.keys()) if all_progs else []))
        .replace('__FIRST_PROG__',    json.dumps(current_prog or ''))
        .replace('__DIES__',          json.dumps(r['dies'],      ensure_ascii=False, separators=(',',':')))
        .replace('__ALL_MAP__',       json.dumps(r['all_map'],   ensure_ascii=False, separators=(',',':')))
        .replace('__WFR_RADIUS__',    str(r['wfr_radius']))
        .replace('__FB_LIST__',       json.dumps(r['fb_list'],   ensure_ascii=False, separators=(',',':')))
        .replace('__WFR_LIST__',      json.dumps(r['wfr_list'],  ensure_ascii=False, separators=(',',':')))
        .replace('__KILL_LIST__',     json.dumps(r['kill_list'], ensure_ascii=False, separators=(',',':')))
        .replace('__PIN_LIST__',      json.dumps(r['pin_list'],  ensure_ascii=False, separators=(',',':')))
        .replace('__PIN_DISTRIB__',    json.dumps(r['pin_distrib'], ensure_ascii=False, separators=(',',':')))
        .replace('__DETAIL_PINS__',    json.dumps({} if focus_wafer_count > 0 else r['detail_data'], ensure_ascii=False, separators=(',',':')))
        .replace('__RAW_PIN_DATA__',   json.dumps(raw_pin_data or {}, ensure_ascii=False, separators=(',',':')))
        .replace('__FOCUS_WAFER_COUNT__', str(focus_wafer_count))
        .replace('__RAIL_LIST__',     json.dumps(r['rail_list'], ensure_ascii=False, separators=(',',':')))
        .replace('__FLOW_DATA__',     json.dumps(_fd,            ensure_ascii=False, separators=(',',':')))
        .replace('__SURGE_DATA__',    json.dumps(r['surge_data'],  ensure_ascii=False, separators=(',',':')))
        .replace('__EDC_DATA__',      json.dumps(r['edc_data'],    ensure_ascii=False, separators=(',',':')))
        .replace('__RAIL_COND_DATA__',json.dumps(r['rail_cond'],   ensure_ascii=False, separators=(',',':')))
        .replace('__REPORT_HTML__',   r['report_html'])
        .replace('__TOTAL_DIES__',    '{:,}'.format(total_dies))
        .replace('__BIN8_COUNT__',    str(bin8_count))
        .replace('__PASS_COUNT__',    '{:,}'.format(pass_count))
        .replace('__BIN8_PCT__',      bin8_pct)
        .replace('__LOTS_STR__',      ', '.join(lots))
        .replace('__TARGET_IBIN__',   str(TARGET_IBIN))
        .replace('__LOTS_JS__',       json.dumps(lots))
        .replace('__PROGS_JS__',      json.dumps(progs))
        .replace('__RETICLE_MAP__',   reticle_json)
        .replace('__DRS_LIST__',      json.dumps(r['drs_vals'], ensure_ascii=False, separators=(',',':')))
        .replace('__PLOTLY_TAG__',    _plotly_tag)
        .replace('__PATTERN_SCORE_JS__', WPA_SCORE_JS)
        .replace('__PROG_INFO__',     _prog_info_html)
        .replace('__PROG_TABS__',     prog_tab_html)
        .replace('__LIVE_DISPLAY__', 'inline-flex' if focus_wafer_count > 0 else 'none')
        .replace('__LIVE_CHECKED__', 'checked' if focus_wafer_count > 0 else '')
    )
    return _html


    _csv_dir   = os.path.dirname(csv_path)  if csv_path else ''
    _prog_info_html = (
        '<div style="background:#0a0f1a;border-bottom:1px solid #162030;padding:5px 20px;'
        'display:flex;align-items:center;gap:16px;flex-wrap:wrap;font-size:0.74rem;color:#556677">'
        + ('<span title="' + _html_mod.escape(prog_path) + '">'
           '&#128196;&nbsp;<span style="color:#8ab4d4">Program:</span>&nbsp;'
           '<b style="color:#c0deff">' + _html_mod.escape(_prog_name) + '</b></span>'
           if _prog_name else
           '<span style="color:#445566">&#128196;&nbsp;Program: <i>not set</i></span>')
        + ('<span title="' + _html_mod.escape(csv_path) + '">'
           '&#128190;&nbsp;<span style="color:#8ab4d4">CSV:</span>&nbsp;'
           '<b style="color:#c0deff">' + _html_mod.escape(_csv_name) + '</b>'
           '&nbsp;<span style="color:#334455">' + _html_mod.escape(_csv_dir) + '</span></span>'
           if _csv_name else '')
        + '</div>'
    )
    # Compute wafer outline radius: max Euclidean distance from origin across all dies
    import math as _math
    _all_map_obj = json.loads(all_map_json)
    _max_r = 0.0
    for _dies in _all_map_obj.values():
        for _d in _dies:
            _r = _math.sqrt(_d[0]**2 + _d[1]**2)
            if _r > _max_r:
                _max_r = _r
    # Round up to nearest integer and add 0.5 die padding for visual clarity
    _wfr_radius = round(_max_r + 0.5, 2)
    # Build Plotly script tag: direct embed with <\/ escaping (synchronous, no CSP issues)
    _plotly_tag = _PLOTLY_TAG
    print('  Plotly: inlined (<\\/ escaped) from', _PLOTLY_LOCAL)
    return (_HTML_TEMPLATE
        .replace('__DIES__',      dies_json)
        .replace('__ALL_MAP__',   all_map_json)
        .replace('__WFR_RADIUS__', str(_wfr_radius))
        .replace('__FB_LIST__',   fb_json)
        .replace('__WFR_LIST__',  wfr_json)
        .replace('__KILL_LIST__', kill_json)
        .replace('__PIN_LIST__',  pin_json)
        .replace('__RAIL_LIST__', rail_json)
        .replace('__FLOW_DATA__', flow_json)
        .replace('__SURGE_DATA__',   surge_json)
        .replace('__EDC_DATA__',      edc_json)
        .replace('__RAIL_COND_DATA__', rail_cond_json)
        .replace('__REPORT_HTML__', report_html)
        .replace('__TOTAL_DIES__', '{:,}'.format(total_dies))
        .replace('__BIN8_COUNT__', str(bin8_count))
        .replace('__PASS_COUNT__', '{:,}'.format(pass_count))
        .replace('__BIN8_PCT__',   bin8_pct)
        .replace('__LOTS_STR__',   ', '.join(lots))
        .replace('__TARGET_IBIN__', str(TARGET_IBIN))
        .replace('__LOTS_JS__',    lots_js)
        .replace('__PROGS_JS__',   progs_js)
        .replace('__RETICLE_MAP__', reticle_json)
        .replace('__DRS_LIST__',    drs_json)
        .replace('__PLOTLY_TAG__',  _plotly_tag)
        .replace('__PATTERN_SCORE_JS__', WPA_SCORE_JS)
        .replace('__PROG_INFO__', _prog_info_html)
    )


# ── Composite popup HTML ───────────────────────────────────────────────────────
# Self-contained popup opened by openCompositeWindow(). Reads data via
# window.opener.*. __IS__ is replaced at JS runtime with filter-state JSON.
_COMP_POPUP_HTML = (
'<!DOCTYPE html><html><head><meta charset="utf-8"><title>BIN8 Composite View</title>'
'<style>*{box-sizing:border-box;margin:0;padding:0}'
'body{background:#0a1018;color:#c0ccd8;font-family:Arial,sans-serif;font-size:12px;overflow:hidden}'
'.layout{display:flex;height:100vh}'
'.pane-l{width:570px;flex:0 0 570px;padding:10px 12px;overflow-y:auto;border-right:2px solid #1e3050;display:flex;flex-direction:column;gap:8px}'
'.pane-r{flex:1;padding:10px 12px;overflow-y:auto}'
'.filt{background:#0d1520;border:1px solid #1a3050;border-radius:6px;padding:8px 10px}'
'.flab{font-size:0.68rem;color:#556677;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}'
'.frow{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start}'
'.fcol{display:flex;flex-direction:column;gap:2px;max-height:140px;overflow-y:auto;min-width:110px}'
'h2{font-size:13px;font-weight:700;color:#8ab4d4;padding-bottom:5px;border-bottom:1px solid #1e3050;margin-bottom:6px}'
'label.cb{display:flex;align-items:center;gap:3px;cursor:pointer;font-size:0.7rem;white-space:nowrap}'
'button.tog{font-size:0.62rem;background:#1a2235;border:1px solid #2a4060;color:#8ab4d4;border-radius:3px;padding:1px 5px;cursor:pointer;margin-left:3px}'
'</style></head><body>'
'<div class="layout">'
'<div class="pane-l">'
'<h2>&#9707; Composite View &mdash; BIN8</h2>'
'<div class="filt"><div class="flab">Filters</div><div class="frow">'
'<div><div class="flab">Program</div><div id="cp-prog" class="fcol"></div></div>'
'<div><div class="flab">Lot</div><div id="cp-lot" class="fcol"></div></div>'
'<div><div class="flab">Wafer<button class="tog" onclick="togAll(\'cp-wfr\',true)">All</button>'
'<button class="tog" onclick="togAll(\'cp-wfr\',false)">None</button></div>'
'<div id="cp-wfr" class="fcol"></div></div>'
'<div><div class="flab">FB<button class="tog" onclick="togAll(\'cp-fb\',true)">All</button>'
'<button class="tog" onclick="togAll(\'cp-fb\',false)">None</button></div>'
'<div id="cp-fb" class="fcol"></div></div>'
'<div><div class="flab">Failing Pin<button class="tog" onclick="togAll(\'cp-pin\',true)">All</button>'
'<button class="tog" onclick="togAll(\'cp-pin\',false)">None</button></div>'
'<div id="cp-pin" class="fcol" style="min-width:170px"></div></div>'
'<div><div class="flab">Color by</div>'
'<select id="cp-color" onchange="render()" style="background:#1a2235;border:1px solid #2a4060;'
'color:#c0ccd8;padding:3px 6px;font-size:0.72rem;border-radius:4px;margin-top:2px">'
'<option value="fbin">Functional Bin</option><option value="phase">Kill Phase</option>'
'<option value="rtype">Rail Type</option><option value="pin">Failing Pin (CS)</option>'
'</select></div>'
'</div></div>'
'<div id="comp-svg" style="text-align:center;margin-top:4px"></div>'
'<div id="comp-legend" style="font-size:10px;color:#8ab4d4;margin-top:4px"></div>'
'</div>'
'<div class="pane-r"><h2 id="tiles-hdr">Per-Wafer Tiles</h2>'
'<div id="tiles-div" style="display:flex;flex-wrap:wrap;gap:8px;align-items:flex-start"></div>'
'</div></div>'
'<script>__DATA__'
'var PAD=3,TW_LG=520,TW_SM=240;'
'var xMin=_wmXMin,xMax=_wmXMax,yMin=_wmYMin,yMax=_wmYMax;'
'var xCnt=xMax-xMin+1,yCnt=yMax-yMin+1,xSpan=xMax-xMin,ySpan=yMax-yMin;'
'function chk(id){return Array.from(document.getElementById(id).querySelectorAll(\'input:checked\')).map(function(c){return c.value;});}'
'function togAll(id,v){document.getElementById(id).querySelectorAll(\'input\').forEach(function(c){c.checked=v;});render();}'
'function mkCb(id,val,lbl,col,chkd){'
'  var w=document.createElement(\'label\');w.className=\'cb\';w.style.color=col||\'#c0ccd8\';'
'  var c=document.createElement(\'input\');c.type=\'checkbox\';c.value=val;c.checked=chkd;'
'  c.style.accentColor=col||\'#4a9fd4\';c.addEventListener(\'change\',render);'
'  w.appendChild(c);w.appendChild(document.createTextNode(\' \'+lbl));'
'  document.getElementById(id).appendChild(w);}'
'function geom(TW){'
'  var cs=(TW-PAD*2)/xCnt,csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;'
'  var TH=Math.round(yCnt*csy+PAD*2),xCtr=(xMin+xMax)/2,yCtr=(yMin+yMax)/2;'
'  var xRad=(xMax-xMin)/2||1,yRad=(yMax-yMin)/2||1;'
'  return{cs:cs,csy:csy,TH:TH,'
'    eRx:+(xRad*cs+cs*0.5).toFixed(1),eRy:+(yRad*csy+csy*0.5).toFixed(1),'
'    eCx:+(PAD+(xCtr-xMin)*cs+cs*0.5).toFixed(1),eCy:+(PAD+(yMax-yCtr)*csy+csy*0.5).toFixed(1)};}'
'function retSvg(g){if(!_wmRetShots||!_wmRetShots.length)return \'\';var s=\'\';'
'  _wmRetShots.forEach(function(sh){'
'    var rn=sh[0],x0=sh[1],y0=sh[2],x1=sh[3],y1=sh[4];'
'    var sx=(PAD+(x0-xMin)*g.cs).toFixed(1),sy=(PAD+(yMax-y1)*g.csy).toFixed(1);'
'    var sw=((x1-x0+1)*g.cs).toFixed(1),sh2=((y1-y0+1)*g.csy).toFixed(1);'
'    var lx=(PAD+((x0+x1)/2-xMin)*g.cs).toFixed(1),ly=(PAD+(yMax-(y0+y1)/2)*g.csy).toFixed(1);'
'    s+=\'<rect x="\'+sx+\'" y="\'+sy+\'" width="\'+sw+\'" height="\'+sh2+\'" fill="none" stroke="rgba(180,140,255,0.65)" stroke-width="1.5" stroke-dasharray="4,2"/>\';'
'    s+=\'<text x="\'+lx+\'" y="\'+ly+\'" text-anchor="middle" dominant-baseline="middle" font-size="10" fill="rgba(210,170,255,0.9)" font-weight="bold">R\'+rn+\'<\\/text>\';'
'  });return s;}'
'function tileColor(isB8,show,fbin,cm,di,spi){'
'  if(!isB8)return \'#0d1520\';if(!show)return \'#142030\';'
'  if(cm===\'phase\')return di?(PHASE_COL[di.phase]||\'#556677\'):\'#556677\';'
'  if(cm===\'rtype\')return di?(RTYPE_COL[di.rtype]||\'#556677\'):\'#556677\';'
'  if(cm===\'pin\'){'
'    if(!di||!di.pins.length)return \'#334455\';'
'    var pd=spi.size>0&&spi.size<PIN_LIST.length?di.pins.filter(function(p){return spi.has(p.pin);}):di.pins;'
'    if(!pd.length)return \'#334455\';'
'    var wp=pd.reduce(function(b,p){return(p.usl?p.val/p.usl:0)>(b.usl?b.val/b.usl:0)?p:b;},pd[0]);'
'    return CS_COL[wp.cs]||\'#ff6b6b\';}'
'  return fbColor(fbin);}'
'function render(){'
'  var sp=new Set(chk(\'cp-prog\')),sl=new Set(chk(\'cp-lot\'));'
'  var sw=new Set(chk(\'cp-wfr\').map(Number)),sf=new Set(chk(\'cp-fb\').map(Number));'
'  var spi=new Set(chk(\'cp-pin\'));'
'  var cm=document.getElementById(\'cp-color\').value;'
'  var dim={};DIES.forEach(function(d){'
'    if(sp.has(d.prog)&&sl.has(d.lot)&&sw.has(d.wfr)&&sf.has(d.fbin))dim[d.x+\',\'+d.y+\',\'+d.lot+\',\'+d.wfr]=d;});'
'  // posMap: x+","+y → {hits, fb, rep} — respects prog/lot/wafer/FB/pin filters and color mode'
'  var posMap={},nW=0;'
'  Object.keys(ALL_MAP).filter(function(k){var p=k.split(\'|\');return sp.has(p[0])&&sl.has(p[1])&&sw.has(+p[2]);}).forEach(function(wk){'
'    var pp=wk.split(\'|\'),lot=pp[1],wfr=+pp[2];nW++;'
'    ALL_MAP[wk].forEach(function(d){'
'      if(TARGET_IBINS.has(d[2])&&sf.has(d[3])){'
'        var di=dim[d[0]+\',\'+d[1]+\',\'+lot+\',\'+wfr];'
'        if(spi.size>0&&spi.size<PIN_LIST.length&&!(di&&di.pins.some(function(p){return spi.has(p.pin);})))return;'
'        var key=d[0]+\',\'+d[1];'
'        if(!posMap[key]){posMap[key]={hits:0,fb:d[3],rep:di||null};}'
'        posMap[key].hits++;if(!posMap[key].rep&&di)posMap[key].rep=di;}});});'
'  if(!nW)nW=1;'
'  var gL=geom(TW_LG),gS=geom(TW_SM),rL=retSvg(gL),rS=retSvg(gS);'
'  var ci=\'cpc0\';'
'  var cc=\'<defs><clipPath id="\'+ci+\'"><ellipse cx="\'+gL.eCx+\'" cy="\'+gL.eCy+\'" rx="\'+gL.eRx+\'" ry="\'+gL.eRy+\'"/><\\/clipPath><\\/defs>\';'
'  var cb=\'<ellipse cx="\'+gL.eCx+\'" cy="\'+gL.eCy+\'" rx="\'+gL.eRx+\'" ry="\'+gL.eRy+\'" fill="none" stroke="#a0bcd8" stroke-width="3"/>\';'
'  var ad=new Set();Object.values(ALL_MAP).forEach(function(ds){ds.forEach(function(d){ad.add(d[0]+\',\'+d[1]);});});'
'  var cr=[];ad.forEach(function(k){'
'    var xy=k.split(\',\'),x=+xy[0],y=+xy[1];'
'    var px=(PAD+(x-xMin)*gL.cs).toFixed(1),py=(PAD+(yMax-y)*gL.csy).toFixed(1);'
'    var dw=(gL.cs*0.9).toFixed(1),dh=(gL.csy*0.9).toFixed(1);'
'    var pm=posMap[k];var fl,op;'
'    if(!pm){fl=\'#0d1828\';op=\'0.7\';}'
'    else{var t=pm.hits/nW;op=(Math.min(1,Math.max(0.4,t+0.35))).toFixed(2);fl=tileColor(true,true,pm.fb,cm,pm.rep,spi);}'
'    cr.push(\'<rect x="\'+px+\'" y="\'+py+\'" width="\'+dw+\'" height="\'+dh+\'" fill="\'+fl+\'" opacity="\'+op+\'"/>\');});'
'  var nPos=Object.keys(posMap).length;'
'  document.getElementById(\'comp-svg\').innerHTML='
'    \'<svg xmlns="http://www.w3.org/2000/svg" width="\'+TW_LG+\'" height="\'+gL.TH+\'">\'+cc+\'<g clip-path="url(#\'+ci+\')">\'+ cr.join(\'\')+rL+\'<\\/g>\'+cb+\'<\\/svg>\';'
'  document.getElementById(\'comp-legend\').innerHTML='
'    \'<b>\'+nPos+\'</b> BIN8 pos · <b>\'+nW+\'</b> wafer\'+(nW!==1?\'s\':\'\')+\' · color: \'+cm;'
'  var ti=0,th=\'\';'
'  Object.keys(ALL_MAP).filter(function(k){var p=k.split(\'|\');return sp.has(p[0])&&sl.has(p[1])&&sw.has(+p[2]);}).sort().forEach(function(wk){'
'    var p=wk.split(\'|\'),lot=p[1],wfr=+p[2];var cid=\'cpt\'+(ti++);'
'    var cd=\'<defs><clipPath id="\'+cid+\'"><ellipse cx="\'+gS.eCx+\'" cy="\'+gS.eCy+\'" rx="\'+gS.eRx+\'" ry="\'+gS.eRy+\'"/><\\/clipPath><\\/defs>\';'
'    var be=\'<ellipse cx="\'+gS.eCx+\'" cy="\'+gS.eCy+\'" rx="\'+gS.eRx+\'" ry="\'+gS.eRy+\'" fill="none" stroke="#a0bcd8" stroke-width="2"/>\';'
'    var rects=[],b8c=0;'
'    ALL_MAP[wk].forEach(function(d){'
'      var x=d[0],y=d[1],ib=d[2],fb=d[3];'
'      var isB8=(ib===TARGET_IBIN)&&sf.has(fb);'
'      var show=isB8;'
'      if(isB8&&spi.size>0&&spi.size<PIN_LIST.length){'
'        var di2=dim[x+\',\'+y+\',\'+lot+\',\'+wfr];show=!!(di2&&di2.pins.some(function(pin){return spi.has(pin.pin);}));}'
'      if(show)b8c++;'
'      var di=isB8?dim[x+\',\'+y+\',\'+lot+\',\'+wfr]:null;'
'      var fl=tileColor(isB8,show,fb,cm,di,spi);'
'      var px=(PAD+(x-xMin)*gS.cs).toFixed(1),py=(PAD+(yMax-y)*gS.csy).toFixed(1);'
'      var dw=(gS.cs*0.9).toFixed(1),dh=(gS.csy*0.9).toFixed(1);'
'      rects.push(\'<rect x="\'+px+\'" y="\'+py+\'" width="\'+dw+\'" height="\'+dh+\'" fill="\'+fl+\'"/>\');});'
'    th+=\'<div style="text-align:center;margin:4px">\''
'      +\'<div style="font-size:9px;color:#8ab4d4;margin-bottom:2px">\'+lot+\' W\'+wfr+\'<\\/div>\''
'      +\'<svg xmlns="http://www.w3.org/2000/svg" width="\'+TW_SM+\'" height="\'+gS.TH+\'">\'+cd+\'<g clip-path="url(#\'+cid+\')">\'+ rects.join(\'\')+rS+\'<\\/g>\'+be+\'<\\/svg>\''
'      +(b8c?\'<div style="font-size:9px;color:#ff8080;margin-top:1px">\'+b8c+\' BIN8<\\/div>\':'
'            \'<div style="font-size:9px;color:#3a5070;margin-top:1px">&mdash;<\\/div>\')'
'      +\'<\\/div>\';});'
'  if(!th)th=\'<div style="color:#445566;padding:20px">No wafers selected<\\/div>\';'
'  document.getElementById(\'tiles-div\').innerHTML=th;'
'  document.getElementById(\'tiles-hdr\').textContent=\'Per-Wafer Tiles (\'+nW+\' wafer\'+(nW!==1?\'s\':\'\')+\')\';}'
'(function(){'
'  var IS=__IS__;'
'  PROGS.forEach(function(p){mkCb(\'cp-prog\',p,p,\'#8ab4d4\',IS.progs.indexOf(p)>=0);});'
'  LOTS.forEach(function(l){mkCb(\'cp-lot\',l,l,\'#8ab4d4\',IS.lots.indexOf(l)>=0);});'
'  var uW=DIES.map(function(d){return d.wfr;}).filter(function(v,i,a){return a.indexOf(v)===i;}).sort(function(a,b){return a-b;});'
'  uW.forEach(function(w){mkCb(\'cp-wfr\',w,\'W\'+w,\'#8ab4d4\',IS.wfrs.indexOf(w)>=0);});'
'  FB_LIST.forEach(function(f){mkCb(\'cp-fb\',f.fbin,\'FB \'+f.fbin+\' (\'+f.count+\')\',fbColor(f.fbin),IS.fbs.indexOf(f.fbin)>=0);});'
'  PIN_LIST.forEach(function(pin){mkCb(\'cp-pin\',pin.pin,pin.pin+\' (\'+pin.count+\')\',CS_COL[pin.cs]||\'#8ab4d4\',IS.pins.indexOf(pin.pin)>=0);});'
'  document.getElementById(\'cp-color\').value=IS.color;'
'  // debug: show data summary before first render'
'  var _dbg=document.createElement(\'div\');'
'  _dbg.id=\'_cp_dbg\';'
'  _dbg.style=\'font-size:10px;color:#aaa;padding:4px 8px;background:#0d1520;margin:4px 0;border:1px solid #223;border-radius:4px\';'
'  _dbg.textContent=\'[DBG] IS.progs=\'+JSON.stringify(IS.progs)+\' IS.lots=\'+JSON.stringify(IS.lots)+\' IS.wfrs=\'+JSON.stringify(IS.wfrs)+\' IS.fbs=\'+JSON.stringify(IS.fbs)+\' xMin=\'+_wmXMin+\' xMax=\'+_wmXMax+\' yMin=\'+_wmYMin+\' yMax=\'+_wmYMax+\' ALL_MAP keys=\'+Object.keys(ALL_MAP).length+\' DIES=\'+DIES.length;'
'  document.body.insertBefore(_dbg,document.body.firstChild);'
'  try{'
'  render();'
'  }catch(e){'
'    document.getElementById(\'comp-svg\').innerHTML=\'<pre style="color:#ff8888;font-size:11px;white-space:pre-wrap">RENDER ERROR: \'+e.message+\'\\n\'+e.stack+\'</pre>\';'
'  }'
'})();'
'</script>'
'</body></html>'
)

def _make_comp_popup_js(html):
    """Escape popup HTML for embedding as a JS single-quoted string."""
    s = html.replace('\\', '\\\\').replace("'", "\\'")
    s = s.replace('\r\n', '\\n').replace('\n', '\\n')
    s = s.replace('</', r'<\/')
    return "'" + s + "'"

_COMP_POPUP_JS = _make_comp_popup_js(_COMP_POPUP_HTML)

# Injected after WAFERMAP_JS (last script in body) so that:
#  - All DOM elements (incl. comp-overlay) are already parsed
#  - wmRender is already defined
# Handles both fresh page load (popup opened for the first time) and
# hash-only navigation (popup already open, "Composite View" clicked again).
_POPUP_INIT_JS = (
    '<script>\n'
    '(function(){\n'
    '  function _cpAutoOpen(){\n'
    '    var _h=location.hash;\n'
    '    if(_h.indexOf(\'#cpstate=\')!==0)return;\n'
    '    try{\n'
    '      var _st=JSON.parse(decodeURIComponent(_h.slice(9)));\n'
    '      _cpShowOverlay(_st);\n'
    '    }catch(e){console.error(\'composite auto-open failed:\',e);}\n'
    '  }\n'
    '  _cpAutoOpen();\n'
    '  window.addEventListener(\'hashchange\',_cpAutoOpen);\n'
    '})();\n'
    '</script>'
)

_HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>VccCont BIN8 &#8212; NVL816-BLLC 61B</title>
__PLOTLY_TAG__
<script>
window.onerror=function(msg,src,line,col,err){
  var d=document.getElementById('_err_overlay');
  if(!d){d=document.createElement('div');d.id='_err_overlay';
    d.style='position:fixed;top:0;left:0;right:0;background:#1a0808;border-bottom:3px solid #ff4444;color:#ff9999;font-family:monospace;font-size:13px;padding:14px 18px;z-index:99999;white-space:pre-wrap;max-height:40vh;overflow:auto';
    document.documentElement.appendChild(d);}
  d.textContent+='JS ERROR: '+msg+'\n  at '+src+':'+line+':'+col+'\n'+(err&&err.stack?err.stack:'')+'\n\n';
  return false;
};
</script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#c0ccd8;font-family:system-ui,sans-serif;font-size:14px}
.hdr{background:#141c2e;border-bottom:2px solid #1e3a5f;padding:10px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.hdr h1{font-size:1.1rem;font-weight:700;color:#e0eaf8}
.stat-pill{background:#1e2d45;border:1px solid #2a4060;border-radius:20px;padding:3px 12px;font-size:0.78rem;color:#8ab4d4}
.stat-pill b{color:#c0deff}
.tab-bar{display:flex;background:#0f1520;border-bottom:1px solid #1e3050;padding:0 16px}
.tab-btn{padding:10px 20px;cursor:pointer;font-size:0.85rem;font-weight:600;color:#556677;border-bottom:3px solid transparent;transition:all 0.15s}
.tab-btn:hover{color:#8ab4d4}
.tab-btn.active{color:#4a9fd4;border-bottom-color:#4a9fd4}
.tab-panel{display:none;padding:16px 20px}
.tab-panel.active{display:block}
.fbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px;padding:10px 14px;background:#0f1520;border:1px solid #1e3050;border-radius:8px}
.dd-wrap{position:relative;display:inline-block;vertical-align:middle}
.dd-btn{display:inline-flex;align-items:center;gap:5px;background:#1a2235;border:1px solid #2a4060;border-radius:5px;color:#8ab4d4;padding:5px 10px;font-size:0.78rem;cursor:pointer;white-space:nowrap}
.dd-btn:hover{border-color:#4a9fd4;color:#c0deff}
.dd-arr{font-size:0.64rem;opacity:0.7}
.dd-panel{display:none;position:absolute;top:calc(100% + 4px);left:0;z-index:600;background:#111827;border:1px solid #2a4060;border-radius:7px;min-width:260px;max-width:560px;width:300px;padding:8px;box-shadow:0 10px 32px rgba(0,0,0,0.65);flex-direction:column;gap:6px;resize:horizontal;overflow:auto}
.dd-panel.open{display:flex}
.dd-search{width:100%;box-sizing:border-box;background:#0d1520;border:1px solid #2a4060;border-radius:4px;color:#c0ccd8;padding:4px 8px;font-size:0.76rem;outline:none}
.dd-search::placeholder{color:#445566}
.dd-search:focus{border-color:#4a7aaa}
.dd-tree-inner{max-height:280px;overflow-y:auto;display:flex;flex-direction:column;gap:1px;resize:vertical}
.dd-actions{display:flex;gap:5px;flex-shrink:0}
.dd-actions button{font-size:0.68rem;background:#1a2235;border:1px solid #2a4060;color:#8ab4d4;border-radius:3px;padding:1px 7px;cursor:pointer}
.dd-actions button:hover{border-color:#4a9fd4}
.fbar label{font-size:0.77rem;color:#667788;white-space:nowrap}
.fbar select,.fbar input{background:#1a2235;border:1px solid #2a4060;border-radius:5px;color:#c0ccd8;padding:4px 8px;font-size:0.78rem;cursor:pointer}
.fbtn{cursor:pointer;padding:3px 11px;border-radius:14px;font-size:0.74rem;font-weight:700;border:1.5px solid;transition:all 0.15s;white-space:nowrap;background:#1a1f2e}
table{width:100%;border-collapse:collapse;font-size:0.82rem}
th{background:#141c2e;color:#8ab4d4;text-align:left;padding:7px 10px;border-bottom:2px solid #1e3050;white-space:nowrap;cursor:pointer;user-select:none}
th:hover{color:#c0deff}
td{padding:6px 10px;border-bottom:1px solid #141c2e;vertical-align:top}
tr:hover td{background:#0f1a2a}
.tbl-wrap{overflow-x:auto;max-height:520px;overflow-y:auto;border:1px solid #1e3050;border-radius:8px}
.card{background:#0f1520;border:1px solid #1e3050;border-radius:8px;padding:14px}
.card h3{font-size:0.85rem;font-weight:700;color:#8ab4d4;margin-bottom:10px;text-transform:uppercase;letter-spacing:.05em}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:0.72rem;font-weight:700}
.badge-pre{background:#1a3a2a;color:#4ecdc4}
.badge-post{background:#3a1a1a;color:#ff6b6b}
.badge-isvm{background:#1a2a3a;color:#5aabff}
.badge-hv{background:#2a1e1a;color:#f4a261}
.badge-hc{background:#2a1a2a;color:#c77dff}
.badge-lc{background:#1a2a1a;color:#84a98c}
.badge-vlc{background:#1a1e2a;color:#48cae4}
.pin-tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:0.71rem;background:#1a2235;border:1px solid #2a4060;margin:1px;color:#8ab4d4}
.pdm-xp-tab{padding:4px 11px;font-size:0.74rem;font-weight:700;cursor:pointer;border-radius:4px;border:1px solid #2a4060;background:#0d1520;color:#8ab4d4;margin-right:3px}
.pdm-xp-tab.active{background:#1e3a5f;border-color:#4a9fd4;color:#c0deff}
.pdm-xp-panel{}
.pin-fail{background:#2a1a1a;border-color:#6a2a2a;color:#ff9999}
.ov-card{flex:1;min-width:120px;background:#0f1520;border:1px solid #1e3050;border-radius:8px;padding:12px 16px;text-align:center}
.ov-num{font-size:2rem;font-weight:800;color:#4a9fd4;line-height:1}
.ov-num.red{color:#e05c5c}
.ov-num.amber{color:#ffd166}
.ov-label{font-size:0.73rem;color:#556677;margin-top:4px}
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:#0d1117}
::-webkit-scrollbar-thumb{background:#2a4060;border-radius:3px}
.modal-bg{position:fixed;inset:0;z-index:1000;background:#000000bb;display:flex;align-items:center;justify-content:center}
.modal-box{background:#0f1520;border:1px solid #2a4060;border-radius:10px;width:min(960px,96vw);max-height:92vh;display:flex;flex-direction:column;overflow:hidden}
.modal-hdr{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid #1e3050;flex-shrink:0}
.modal-body{overflow-y:auto;padding:16px 18px;flex:1}
/* ── COMPOSITE OVERLAY ── */
#comp-overlay{display:none;width:100%;overflow:hidden}
#comp-overlay .cp-topbar{display:flex;align-items:center;justify-content:space-between;padding:6px 12px;background:#0a1018;border-bottom:2px solid #1e3050;flex-shrink:0}
#comp-overlay .cp-layout{display:flex;height:calc(100vh - 90px)}
#comp-overlay .cp-pane-1{width:200px;min-width:120px;padding:8px 10px;overflow:hidden;display:flex;flex-direction:column;height:calc(100vh - 90px);box-sizing:border-box;flex-shrink:0;background:#0a1018}
/* ── dropdown filter widget ── */
.cp-dd-wrap{position:relative;width:100%}
.cp-dd-btn{width:100%;background:#141c2e;border:1px solid #2a4060;color:#8ab4d4;border-radius:5px;
  padding:5px 10px;cursor:pointer;font-size:0.73rem;text-align:left;display:flex;justify-content:space-between;align-items:center}
.cp-dd-btn:hover{border-color:#4a9fd4;color:#c0deff}
.cp-dd-panel{display:none;position:absolute;z-index:200;top:calc(100% + 2px);left:0;right:0;
  background:#0d1520;border:1px solid #2a4060;border-radius:5px;box-shadow:0 4px 14px rgba(0,0,0,0.6);padding:4px 0}
.cp-dd-panel.open{display:block}
.cp-dd-acts{display:flex;gap:4px;padding:3px 8px 4px;border-bottom:1px solid #1e3050}
.cp-dd-acts button{font-size:0.65rem;background:#1a2235;border:1px solid #2a4060;color:#8ab4d4;border-radius:3px;padding:1px 7px;cursor:pointer}
.cp-dd-acts button:hover{background:#253550;color:#c0deff}
.cp-dd-search{display:block;width:calc(100% - 16px);margin:4px 8px;padding:3px 7px;background:#0d1520;border:1px solid #2a4060;border-radius:4px;color:#c0ccd8;font-size:0.72rem;outline:none}
.cp-dd-search:focus{border-color:#4a9fd4}
#comp-overlay .cp-pane-2{width:537px;min-width:200px;padding:8px 10px;overflow:hidden;display:flex;flex-direction:column;height:calc(100vh - 90px);box-sizing:border-box;flex-shrink:0}
#comp-overlay .cp-pane-3{flex:1;min-width:200px;padding:8px 10px;overflow:hidden;display:flex;flex-direction:column;height:calc(100vh - 90px);box-sizing:border-box;gap:0}
.cp-resize-handle{height:5px;cursor:ns-resize;background:#1a2a40;border-radius:2px;flex-shrink:0;margin:2px 0;transition:background 0.15s}.cp-resize-handle:hover,.cp-resize-handle:active{background:#3a6090}
.cp-vresize-handle{width:5px;cursor:ew-resize;background:#1e3050;flex-shrink:0;transition:background 0.15s;align-self:stretch}.cp-vresize-handle:hover,.cp-vresize-handle:active{background:#3a6090}
#comp-overlay .cp-filt{background:#0d1520;border:1px solid #1a3050;border-radius:6px;padding:8px 10px}
#comp-overlay .cp-flab{font-size:0.68rem;color:#556677;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}
#comp-overlay .cp-frow{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start}
#comp-overlay .cp-fcol{display:flex;flex-direction:column;gap:2px;max-height:140px;overflow-y:auto;min-width:110px}
#comp-overlay .cp-h2{font-size:13px;font-weight:700;color:#8ab4d4;padding-bottom:5px;border-bottom:1px solid #1e3050;margin-bottom:6px}
#comp-overlay label.cp-cb{display:flex;align-items:center;gap:3px;cursor:pointer;font-size:0.7rem;white-space:nowrap}
#comp-overlay button.cp-tog{font-size:0.62rem;background:#1a2235;border:1px solid #2a4060;color:#8ab4d4;border-radius:3px;padding:1px 5px;cursor:pointer;margin-left:3px}
</style>
</head>
<body>
__PROG_SIDEBAR__
<div style="__MAIN_STYLE__">
<div class="hdr">
  <h1>&#9679; VccCont BIN8 Analysis</h1>
  <span class="stat-pill">Total: <b id="stat-pill-total">__TOTAL_DIES__</b></span>
  <span class="stat-pill">BIN8: <b id="stat-pill-bin8">__BIN8_COUNT__ (__BIN8_PCT__%)</b></span>
  <span class="stat-pill">Pass: <b id="stat-pill-pass">__PASS_COUNT__</b></span>
</div>
__PROG_INFO__
__PROG_TABS__
<div class="tab-bar">
  <div class="tab-btn active" onclick="showTab('overview',this)">Overview</div>
  <div class="tab-btn" onclick="showTab('flowdiag',this)">Test Flow</div>
  <div class="tab-btn" onclick="showTab('wafermap',this)">Wafer Map</div>
  <div class="tab-btn" onclick="showTab('pareto',this)">Fail Pareto</div>
  <div class="tab-btn" onclick="showTab('dietable',this)">Die Table</div>
  <div class="tab-btn" onclick="showTab('surge',this)">&#9651; Pre/Post Surge</div>
  <div class="tab-btn" onclick="showTab('edc',this)">&#9889; ISVM EDC</div>
  <div class="tab-btn" onclick="showTab('railcmp',this)">&#9646; Rail Compare</div>
  <div class="tab-btn" onclick="showTab('report',this)">&#128196; Report</div>
  <div class="tab-btn" onclick="togglePinPicker()" style="margin-left:auto;background:#1a2c1a;border-left:2px solid #2a5040;color:#ffd166;font-weight:700">&#128202; Pin Dist</div>
  <label id="live-mode-tab" style="display:__LIVE_DISPLAY__;align-items:center;gap:5px;cursor:pointer;font-size:0.78rem;font-weight:700;padding:4px 10px;background:#0d2010;border-left:2px solid #1a5030;color:#69f0ae;white-space:nowrap" title="Embed raw data was active — histogram and stats recompute live from raw die measurements">
    <input type="checkbox" id="live-mode-cb" __LIVE_CHECKED__ onchange="toggleLiveMode()" style="accent-color:#69f0ae;cursor:pointer"> &#9889; Live
  </label>
</div>

<!-- OVERVIEW -->
<div id="tab-overview" class="tab-panel active">
  <div id="ov-banner" style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px"></div>
  <div class="row2" style="margin-bottom:16px">
    <div class="card">
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;flex-wrap:wrap">
        <h3 style="margin:0">Bin Breakdown</h3>
        <div style="display:flex;gap:4px;margin-left:8px">
          <button id="ov-ib-btn-all" onclick="_ovSetIB(null,this)" style="padding:3px 10px;font-size:0.73rem;font-weight:700;cursor:pointer;border-radius:4px;border:1px solid #2a4060;background:#1e3050;color:#4a9fd4">All</button>
          <button id="ov-ib-btn-8"   onclick="_ovSetIB(8,this)"    style="padding:3px 10px;font-size:0.73rem;font-weight:700;cursor:pointer;border-radius:4px;border:1px solid #2a4060;background:#0d1520;color:#ff6b6b">IB 8</button>
          <button id="ov-ib-btn-80"  onclick="_ovSetIB(80,this)"   style="padding:3px 10px;font-size:0.73rem;font-weight:700;cursor:pointer;border-radius:4px;border:1px solid #2a4060;background:#0d1520;color:#ffd166">IB 80</button>
          <button id="ov-ib-btn-89"  onclick="_ovSetIB(89,this)"   style="padding:3px 10px;font-size:0.73rem;font-weight:700;cursor:pointer;border-radius:4px;border:1px solid #2a4060;background:#0d1520;color:#c77dff">IB 89</button>
        </div>
      </div>
      <div class="tbl-wrap" id="fb-tbl"></div>
    </div>
    <div class="card" style="padding:8px">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;padding:4px 6px">
        <span style="font-size:0.77rem;color:#667788">FB filter:</span>
        <select id="ov-fb-filter" onchange="drawOvWfr()" style="background:#1a2235;border:1px solid #2a4060;border-radius:5px;color:#c0ccd8;padding:3px 8px;font-size:0.78rem"><option value="all">All FBs</option></select>
      </div>
      <div id="ov-wfr" style="height:260px"></div>
    </div>
  </div>
  <div class="row2" style="margin-bottom:16px">
    <div class="card" style="padding:8px"><div id="fb-pie" style="height:280px"></div></div>
  </div>
  <div class="card" style="margin-bottom:16px"><h3>Failing Pin Detail — click an FB row above</h3><div id="fb-pin-detail"></div></div>
  <div class="card" id="fb-dist-card" style="display:none"><h3>Pin Value Distribution <span style="font-size:0.75rem;color:#445566;font-weight:400">(full data range &nbsp;|&nbsp; red dashed = USL)</span></h3><div id="fb-pin-dist" style="min-height:220px"></div></div>
  <div class="card" style="margin-top:16px">
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px">
      <h3 style="margin:0">Rail Summary &#8212; Impedance per Phase</h3>
      <span id="rail-fb-badge" style="font-size:0.77rem;color:#667788">All BIN8 dies</span>
      <button id="rail-reset-btn" onclick="buildRailSummary(null)" style="display:none;background:#1a2235;border:1px solid #2a4060;color:#8ab4d4;border-radius:4px;padding:2px 10px;cursor:pointer;font-size:0.75rem;margin-left:auto">Show All BIN8</button>
      <span style="font-size:0.72rem;color:#334455;margin-left:auto" id="rail-reset-btn-placeholder">click an FB row above to filter &nbsp;\u00b7&nbsp; click a rail row to see failing dies</span>
    </div>
    <div id="rail-summary-wrap"><p style="color:#445566;padding:8px">Loading&#8230;</p></div>
    <div id="rail-detail-wrap" style="display:none;margin-top:10px;padding-top:10px;border-top:1px solid #1e3050"></div>
  </div>
</div>

<!-- WAFER MAP -->
<div id="tab-wafermap" class="tab-panel">
  <!-- Compact dropdown filter bar -->
  <div id="wm-filter-panel" style="background:#0f1520;border-bottom:1px solid #1e3050;padding:6px 14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <div id="wm-prog-wrap" style="display:flex;align-items:center;gap:6px">
      <span style="font-size:0.71rem;color:#556677;font-weight:700;text-transform:uppercase;letter-spacing:.04em">Prog</span>
      <div id="wm-prog-cbs" style="display:flex;flex-direction:row;gap:6px;flex-wrap:wrap"></div>
    </div>
    <div class="dd-wrap">
      <button id="wm-lot-btn" class="dd-btn" onclick="_ddToggle('wm-lot-panel',event)">Lot/Wafer&nbsp;<span class="dd-lbl">All</span>&nbsp;<span class="dd-arr">&#9660;</span></button>
      <div class="dd-panel" id="wm-lot-panel" onclick="event.stopPropagation()">
        <input class="dd-search" type="text" placeholder="Search lot or wafer&#8230;" oninput="_ddSearch('wm-lot-wfr-tree',this.value)">
        <div class="dd-actions">
          <button onclick="_wmTreeToggleAll('wm-lot-wfr-tree',true);onWmFilter();_ddLabelUpdate('wm-lot-btn','wm-lot-wfr-tree','lot')">All</button>
          <button onclick="_wmTreeToggleAll('wm-lot-wfr-tree',false);onWmFilter();_ddLabelUpdate('wm-lot-btn','wm-lot-wfr-tree','lot')">None</button>
        </div>
        <div id="wm-lot-wfr-tree" class="dd-tree-inner"></div>
      </div>
    </div>
    <div class="dd-wrap">
      <button id="wm-fb-btn" class="dd-btn" onclick="_ddToggle('wm-ib-panel',event)">IB/Func&nbsp;<span class="dd-lbl">All</span>&nbsp;<span class="dd-arr">&#9660;</span></button>
      <div class="dd-panel" id="wm-ib-panel" onclick="event.stopPropagation()">
        <input class="dd-search" type="text" placeholder="Search IB or FB&#8230;" oninput="_ddSearch('wm-ib-fb-tree',this.value)">
        <div class="dd-actions">
          <button onclick="_wmTreeToggleAll('wm-ib-fb-tree',true);onWmFilter();_ddLabelUpdate('wm-fb-btn','wm-ib-fb-tree','ib')">All</button>
          <button onclick="_wmTreeToggleAll('wm-ib-fb-tree',false);onWmFilter();_ddLabelUpdate('wm-fb-btn','wm-ib-fb-tree','ib')">None</button>
        </div>
        <div id="wm-ib-fb-tree" class="dd-tree-inner"></div>
      </div>
    </div>
    <div class="dd-wrap">
      <button id="wm-pin-btn" class="dd-btn" onclick="_ddToggle('wm-pin-panel',event)">Failing Pin&nbsp;<span class="dd-lbl">All</span>&nbsp;<span class="dd-arr">&#9660;</span></button>
      <div class="dd-panel" id="wm-pin-panel" onclick="event.stopPropagation()">
        <input class="dd-search" type="text" placeholder="Search pin&#8230;" oninput="_ddSearchFlat('wm-pin-cbs',this.value)">
        <div class="dd-actions">
          <button onclick="wmToggleAll('wm-pin-cbs',true);_ddLabelUpdate('wm-pin-btn','wm-pin-cbs','')">All</button>
          <button onclick="wmToggleAll('wm-pin-cbs',false);_ddLabelUpdate('wm-pin-btn','wm-pin-cbs','')">None</button>
        </div>
        <div id="wm-pin-cbs" class="dd-tree-inner"></div>
      </div>
    </div>
    <label style="font-size:0.75rem;color:#667788;display:flex;align-items:center;gap:5px">
      Color
      <select id="wm-color" onchange="onWmFilter();if(typeof _cpRender==='function')_cpRender()" style="background:#1a2235;border:1px solid #2a4060;border-radius:5px;color:#c0ccd8;padding:3px 7px;font-size:0.76rem">
        <option value="fbin">Func Bin</option>
        <option value="phase">Kill Phase</option>
        <option value="rtype">Rail Type</option>
        <option value="pin">Failing Pin</option>
        <option value="site">Reticle Site</option>
        <option value="freq">Fail Freq %</option>
      </select>
    </label>
    <div id="wm-drs-badge" style="font-size:0.72rem;color:#8ab4d4"></div>
    <span id="wm-cnt" style="font-size:0.75rem;color:#445566;margin-left:auto"></span>
  </div>
  <!-- Composite overlay moved here inline by initWM() -->
  <div id="wm-scatter" style="display:none"></div>
  <div id="wm-wpa-btn" style="display:none"></div>
  <div id="wm-bar" style="display:none"></div>
</div>

<!-- DIE DETAIL MODAL (single tabbed window, drag-to-move, no outside-click dismiss) -->
<div id="die-detail-modal" style="display:none;position:fixed;inset:0;z-index:6000;pointer-events:none">
  <div id="die-detail-box" style="pointer-events:all;position:absolute;top:70px;left:50%;transform:translateX(-50%);width:min(740px,96vw);max-height:88vh;background:#0f1520;border:1px solid #2a4060;border-radius:10px;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 12px 48px rgba(0,0,0,0.82)">
    <!-- Drag handle / header -->
    <div id="die-detail-hdr" style="cursor:move;background:#0a1525;border-bottom:1px solid #1e3050;padding:7px 14px;display:flex;align-items:center;gap:8px;user-select:none;flex-shrink:0">
      <span style="font-size:0.74rem;font-weight:700;color:#8ab4d4;text-transform:uppercase;letter-spacing:.05em">&#9673; Die Inspector</span>
      <span id="die-detail-ident" style="font-size:0.76rem;color:#c0deff;margin-left:2px"></span>
      <div style="display:flex;gap:3px;margin-left:12px">
        <button id="ddi-tab-info" onclick="_ddiTab('info')" style="background:#1e3a5f;color:#8ab4d4;border:1px solid #2a5080;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:0.72rem;font-weight:700">&#128229; Die Info</button>
        <button id="ddi-tab-chart" onclick="_ddiTab('chart')" style="background:#0d1828;color:#445566;border:1px solid #1e3050;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:0.72rem">&#128202; Pin Chart</button>
      </div>
      <span style="font-size:0.65rem;color:#334455;margin-left:4px">drag to move</span>
      <button onclick="_ddiClose()" style="margin-left:auto;background:none;border:none;color:#8ab4d4;cursor:pointer;font-size:1.2rem;line-height:1;padding:0 3px" title="Close">&times;</button>
    </div>
    <!-- Tab panels -->
    <div style="overflow-y:auto;flex:1">
      <div id="ddi-panel-info" style="padding:10px 14px"></div>
      <div id="ddi-panel-chart" style="display:none;padding:4px 8px 10px">
        <div style="display:flex;align-items:center;gap:8px;padding:4px 6px 4px;border-bottom:1px solid #1a3050;margin-bottom:4px">
          <span style="font-size:0.73rem;color:#4ecdc4">&#128202;</span>
          <span id="ddi-chart-pin" style="font-size:0.82rem;font-weight:700;color:#c0deff"></span>
          <button onclick="_wmDieCloseChart()" style="background:#1a2235;border:1px solid #2a4060;color:#8ab4d4;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:0.71rem">&#8592; Back to Die Info</button>
          <button onclick="showPinDist(_wmDieCurPin)" style="margin-left:auto;background:#0d2540;border:1px solid #2a5080;color:#4ecdc4;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:0.71rem">&#9635; Full inspector &rarr;</button>
        </div>
        <div id="ddi-chart" style="height:380px"></div>
      </div>
    </div>
  </div>
</div>

<!-- PARETO -->
<div id="tab-pareto" class="tab-panel">
  <!-- Compact dropdown filter bar -->
  <div id="par-filter-panel" style="background:#0f1520;border:1px solid #1e3050;border-radius:8px;padding:8px 14px;margin-bottom:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <div id="par-prog-wrap" style="display:flex;align-items:center;gap:6px">
      <span style="font-size:0.71rem;color:#556677;font-weight:700;text-transform:uppercase;letter-spacing:.04em">Prog</span>
      <div id="par-prog-cbs" style="display:flex;flex-direction:row;gap:6px;flex-wrap:wrap"></div>
    </div>
    <div class="dd-wrap">
      <button id="par-lot-btn" class="dd-btn" onclick="_ddToggle('par-lot-panel',event)">Lot/Wafer&nbsp;<span class="dd-lbl">All</span>&nbsp;<span class="dd-arr">&#9660;</span></button>
      <div class="dd-panel" id="par-lot-panel" onclick="event.stopPropagation()">
        <input class="dd-search" type="text" placeholder="Search lot or wafer&#8230;" oninput="_ddSearch('par-lot-wfr-tree',this.value)">
        <div class="dd-actions">
          <button onclick="_wmTreeToggleAll('par-lot-wfr-tree',true);drawPareto();_ddLabelUpdate('par-lot-btn','par-lot-wfr-tree','lot')">All</button>
          <button onclick="_wmTreeToggleAll('par-lot-wfr-tree',false);drawPareto();_ddLabelUpdate('par-lot-btn','par-lot-wfr-tree','lot')">None</button>
        </div>
        <div id="par-lot-wfr-tree" class="dd-tree-inner"></div>
      </div>
    </div>
    <div class="dd-wrap">
      <button id="par-fb-btn" class="dd-btn" onclick="_ddToggle('par-ib-panel',event)">IB/Func&nbsp;<span class="dd-lbl">All</span>&nbsp;<span class="dd-arr">&#9660;</span></button>
      <div class="dd-panel" id="par-ib-panel" onclick="event.stopPropagation()">
        <input class="dd-search" type="text" placeholder="Search IB or FB&#8230;" oninput="_ddSearch('par-ib-fb-tree',this.value)">
        <div class="dd-actions">
          <button onclick="_wmTreeToggleAll('par-ib-fb-tree',true);drawPareto();_ddLabelUpdate('par-fb-btn','par-ib-fb-tree','ib')">All</button>
          <button onclick="_wmTreeToggleAll('par-ib-fb-tree',false);drawPareto();_ddLabelUpdate('par-fb-btn','par-ib-fb-tree','ib')">None</button>
        </div>
        <div id="par-ib-fb-tree" class="dd-tree-inner"></div>
      </div>
    </div>
    <div class="dd-wrap">
      <button id="par-ph-btn" class="dd-btn" onclick="_ddToggle('par-ph-panel',event)">Phase&nbsp;<span class="dd-lbl">All</span>&nbsp;<span class="dd-arr">&#9660;</span></button>
      <div class="dd-panel" id="par-ph-panel" onclick="event.stopPropagation()">
        <div class="dd-actions">
          <button onclick="parToggleAll('par-ph-cbs',true);_ddLabelUpdate('par-ph-btn','par-ph-cbs','')">All</button>
          <button onclick="parToggleAll('par-ph-cbs',false);_ddLabelUpdate('par-ph-btn','par-ph-cbs','')">None</button>
        </div>
        <div id="par-ph-cbs" class="dd-tree-inner"></div>
      </div>
    </div>
    <span id="par-cnt" style="font-size:0.78rem;color:#556677;white-space:nowrap;margin-left:auto"></span>
  </div>
  <div style="margin-bottom:16px">
    <div class="card" style="padding:8px"><div id="par-plot" style="height:420px"></div></div>
  </div>
  <div class="row2">
    <div class="card" style="padding:8px"><div id="par-phase-plot" style="height:300px"></div></div>
    <div class="card" style="padding:8px"><div id="par-fb-plot" style="height:300px"></div></div>
  </div>
</div>

<!-- DIE TABLE -->
<div id="tab-dietable" class="tab-panel">
  <div class="fbar">
    <label>Lot:<select id="dt-lot" onchange="buildDieTable()"><option value="all">All</option></select></label>
    <label>Wafer:<select id="dt-wfr" onchange="buildDieTable()"><option value="all">All</option></select></label>
    <label>FB:<select id="dt-fb" onchange="buildDieTable()"><option value="all">All</option></select></label>
    <label>Kill:<select id="dt-kill" onchange="buildDieTable()"><option value="all">All</option></select></label>
    <label>Pin:<select id="dt-pin" onchange="buildDieTable()"><option value="all">All</option></select></label>
    <label>Search:<input id="dt-srch" type="text" placeholder="X Y lot keyword..." oninput="buildDieTable()" style="width:140px"></label>
    <span id="dt-cnt" style="font-size:0.78rem;color:#556677;margin-left:auto"></span>
  </div>
  <div class="tbl-wrap">
    <table><thead><tr>
      <th onclick="dtSort('lot')">Lot</th><th onclick="dtSort('wfr')">W#</th>
      <th onclick="dtSort('material')">Material</th>
      <th onclick="dtSort('x')">X</th><th onclick="dtSort('y')">Y</th>
      <th onclick="dtSort('fbin')">FB</th><th onclick="dtSort('dbin')">DataBin</th>
      <th onclick="dtSort('kill')">Kill Test</th>
      <th onclick="dtSort('phase')">Phase</th><th onclick="dtSort('rtype')">Rail</th>
      <th>Failing Pins (m&#937;)</th>
    </tr></thead><tbody id="dt-body"></tbody></table>
  </div>
</div>

<!-- ISVM EDC -->
<div id="tab-edc" class="tab-panel">
  <div style="background:#0f1520;border:1px solid #1e3050;border-radius:8px;padding:12px 18px;margin-bottom:14px">
    <div style="font-size:0.88rem;font-weight:700;color:#4ecdc4;margin-bottom:8px">ISVM EDC vs POSTSURGE VSIM &mdash; Same Rail, Different Measurement Methods</div>
    <div style="font-size:0.78rem;color:#8ab4d4;line-height:1.7;margin-bottom:8px">
      <b style="color:#ffd166">Pre (x-axis)</b> = ISVM EDC at <code>E_START</code> (before surge pulse) &mdash; current-force method, higher absolute values (50&ndash;200&thinsp;m&Omega; range).<br>
      <b style="color:#48cae4">Post (y-axis)</b> = POSTSURGE VSIM at <code>K_START</code> (after surge pulse) &mdash; VSIM resistance method, lower absolute values (5&ndash;80&thinsp;m&Omega; range).<br>
      The two measurements are <b>not directly comparable in magnitude</b> &mdash; each axis uses its own PASS p99 threshold (dashed lines). Quadrant classification is relative to each method&apos;s own threshold.
    </div>
    <div style="font-size:0.76rem;color:#667788">&#9888; Key question: if EDC and VSIM agree on which dies are over-range, the defect is rail-level. If only VSIM shows a tail (Q2), the defect is invisible to EDC &mdash; suggesting a different failure mode or test-method sensitivity difference.</div>
  </div>

  <!-- Coverage table for EDC -->
  <div style="background:#0f1520;border:1px solid #1e3050;border-radius:8px;padding:12px 18px;margin-bottom:14px">
    <div style="font-size:0.82rem;font-weight:700;color:#5aabff;margin-bottom:8px">Lot / Wafer Coverage &mdash; ISVM EDC</div>
    <div style="overflow-x:auto;margin-bottom:10px">
    <table style="border-collapse:collapse;font-size:0.78rem;width:auto">
      <thead>
        <tr style="background:#131a2a;color:#8ab4d4">
          <th style="padding:5px 10px;border:1px solid #2a4060;text-align:left">Lot</th>
          <th style="padding:5px 10px;border:1px solid #2a4060">Program</th>
          <th style="padding:5px 10px;border:1px solid #2a4060">Wafers</th>
          <th style="padding:5px 10px;border:1px solid #2a4060">EDC Port</th>
          <th style="padding:5px 10px;border:1px solid #2a4060">EDC Sets BIN8?</th>
          <th style="padding:5px 10px;border:1px solid #2a4060">PRESURGE Port</th>
          <th style="padding:5px 10px;border:1px solid #2a4060">PRESURGE Kills BIN8?</th>
          <th style="padding:5px 10px;border:1px solid #2a4060;text-align:right">BIN8&nbsp;@&nbsp;PRESURGE</th>
          <th style="padding:5px 10px;border:1px solid #2a4060">POSTSURGE Kill</th>
          <th style="padding:5px 10px;border:1px solid #2a4060;text-align:right">BIN8&nbsp;@&nbsp;POSTSURGE</th>
          <th style="padding:5px 10px;border:1px solid #2a4060;text-align:right">Paired in analysis</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#e0eaf8;font-weight:700">Q603S6T01</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:center">61A</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#8ab4d4">W503&ndash;W512</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#ffd166;font-weight:700;text-align:center">E_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#4ecdc4;text-align:center">&#10005; No &mdash; EDC only</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#48cae4;font-weight:700;text-align:center">E_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#ff6b6b;text-align:center">&#10004; Yes &mdash; K_START kill port</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#ff6b6b;text-align:right;font-weight:700">152</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:center">K_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:right">24</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#4ecdc4;text-align:right"><b>24</b> <span style="color:#556677;font-size:0.72rem">(152 pre-killed: EDC run, no VSIM pair)</span></td>
        </tr>
        <tr style="background:#0d1420">
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#e0eaf8;font-weight:700">Q603S6T02</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:center">61A</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#8ab4d4">W501, W502</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#ffd166;font-weight:700;text-align:center">E_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#4ecdc4;text-align:center">&#10005; No &mdash; EDC only</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#48cae4;font-weight:700;text-align:center">E_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#4ecdc4;text-align:center">&#10005; Diagnostic only</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#556677;text-align:right">0</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:center">K_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:right">3</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#4ecdc4;text-align:right"><b>3</b></td>
        </tr>
        <tr>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#e0eaf8;font-weight:700">Q603S6T03</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:center">61B</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#8ab4d4">W506&ndash;W512</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#ffd166;font-weight:700;text-align:center">E_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#4ecdc4;text-align:center">&#10005; No &mdash; EDC only</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#48cae4;font-weight:700;text-align:center">E_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#4ecdc4;text-align:center">&#10005; Diagnostic only</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#556677;text-align:right">0</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:center">K_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:right">92</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#4ecdc4;text-align:right"><b>92</b></td>
        </tr>
        <tr style="background:#0a1018;font-weight:700">
          <td style="padding:5px 10px;border:1px solid #2a4060;color:#8ab4d4" colspan="8">Total</td>
          <td style="padding:5px 10px;border:1px solid #2a4060"></td>
          <td style="padding:5px 10px;border:1px solid #2a4060;color:#c0ccd8;text-align:right">119</td>
          <td style="padding:5px 10px;border:1px solid #2a4060;color:#4ecdc4;text-align:right">119 of 349</td>
        </tr>
      </tbody>
    </table>
    </div>
    <div style="font-size:0.78rem;color:#667788;line-height:1.6">
      &#9888; ISVM EDC is <b>measurement-only</b> &mdash; it does <b>not set BIN8</b>.
      However, a die whose ISVM EDC reading exceeds the PASS&nbsp;p99 threshold (dashed yellow line on scatter) has a measurably elevated pre-surge rail resistance, confirming the same structural defect visible in POSTSURGE VSIM.
      The 152&nbsp;T01 dies killed at PRESURGE&nbsp;K_START had EDC run before kill, but have no POSTSURGE data &mdash; they are <b>excluded</b> from the paired scatter below.
      &mdash; For the selected rail: <b style="color:#ffd166" id="edc-usl-n">?</b> of <b id="edc-usl-tot">?</b> paired BIN8 dies have ISVM EDC &gt;&nbsp;PASS&nbsp;p99&nbsp;=&nbsp;<b style="color:#ffd166" id="edc-usl-thresh">?</b>&thinsp;m&Omega; &mdash; these are USL exceedances even in the EDC-only (no-BIN) measurement.
    </div>
  </div>

  <!-- EDC Limit Exceedance Summary Table -->
  <div style="margin-bottom:14px">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;cursor:pointer" onclick="toggleEdcLimTbl()">
      <span style="font-size:0.78rem;font-weight:700;color:#8ab4d4;text-transform:uppercase;letter-spacing:.06em">&#9660; EDC Limit Exceedance Summary (all BIN8 dies)</span>
      <span id="edc-lim-tbl-toggle" style="font-size:0.72rem;color:#556677">(click to collapse)</span>
    </div>
    <div id="edc-lim-tbl-wrap" style="overflow-x:auto">
      <table id="edc-lim-tbl" style="border-collapse:collapse;font-size:0.78rem;width:auto"></table>
    </div>
  </div>

  <!-- DPS type + rail checkboxes (populated by JS) -->
  <div style="margin-bottom:8px">
    <span style="font-size:0.72rem;color:#556677;font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin-right:8px">DPS Type:</span>
    <span id="edc-dps-cbs" style="display:inline-flex;gap:8px;flex-wrap:wrap"></span>
  </div>
  <div style="margin-bottom:14px">
    <span style="font-size:0.72rem;color:#556677;font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin-right:8px">Rail:</span>
    <span id="edc-rail-cbs" style="display:inline-flex;gap:6px;flex-wrap:wrap"></span>
  </div>
  <div class="row2" style="margin-bottom:14px">
    <div class="card" style="padding:8px"><div id="edc-scatter" style="height:380px"></div></div>
    <div class="card" style="padding:8px"><div id="edc-delta" style="height:380px"></div></div>
  </div>
  <div class="card" style="margin-bottom:14px">
    <h3>Quadrant Classification &mdash; <span id="edc-rail-label">VCCATOM0</span></h3>
    <div id="edc-quad-tbl"></div>
  </div>
  <div id="edc-conclusion"></div>
</div>

<!-- SURGE DELTA -->
<div id="tab-railcmp" class="tab-panel">
  <!-- header -->
  <div style="background:#0f1520;border:1px solid #1e3050;border-radius:8px;padding:10px 16px;margin-bottom:12px">
    <div style="font-size:0.88rem;font-weight:700;color:#4ecdc4;margin-bottom:4px">&#9646; Rail &times; Condition Fail Comparison</div>
    <div style="font-size:0.76rem;color:#8ab4d4;line-height:1.6">
      For each power rail (VLC / LC / HV / HC): number of BIN8 dies with at least one limit-exceeded pin
      at <b style="color:#ffd166">Pre-Surge</b>, <b style="color:#48cae4">Post-Surge</b>, and <b style="color:#c77dff">ISVM-EDC</b> conditions.
      A die can appear in multiple rails/conditions if multiple rails failed.
    </div>
  </div>
  <!-- summary matrix table -->
  <div style="background:#0d1520;border:1px solid #1e3050;border-radius:8px;padding:10px 16px;margin-bottom:12px;overflow-x:auto">
    <div style="font-size:0.8rem;font-weight:700;color:#5aabff;margin-bottom:8px">Summary Matrix &mdash; Die Count</div>
    <table id="rc-matrix" style="border-collapse:collapse;font-size:0.8rem;min-width:400px"></table>
  </div>
  <!-- bar chart -->
  <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px">
    <div style="flex:1;min-width:340px;background:#0d1520;border:1px solid #1e3050;border-radius:8px;padding:8px">
      <div id="rc-bar" style="height:320px"></div>
    </div>
    <div style="flex:1;min-width:340px;background:#0d1520;border:1px solid #1e3050;border-radius:8px;padding:8px">
      <div id="rc-pin-bar" style="height:320px"></div>
    </div>
  </div>
  <!-- variability plot -->
  <div style="background:#0d1520;border:1px solid #1e3050;border-radius:8px;padding:10px 16px;margin-bottom:12px">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:4px">
      <div>
        <span style="font-size:0.8rem;font-weight:700;color:#5aabff">Measurement Variability &mdash; % Above USL</span>
        <span style="font-size:0.72rem;color:#556677;margin-left:8px">(BIN8 failing dies only &middot; values &gt;0% are limit-exceeds)</span>
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        <label style="font-size:0.74rem;color:#8ab4d4">Pin:</label>
        <select id="rc-var-pin" onchange="_rcDrawVarPlot()"
                style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:4px;padding:2px 6px;font-size:0.74rem">
          <option value="__all__">All top pins</option>
        </select>
        <label style="font-size:0.74rem;color:#8ab4d4">Condition:</label>
        <select id="rc-var-cond" onchange="_rcDrawVarPlot()"
                style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:4px;padding:2px 6px;font-size:0.74rem">
          <option value="__all__">All</option>
        </select>
      </div>
    </div>
    <div id="rc-var-plot" style="height:360px"></div>
  </div>
  <!-- pin breakdown table -->
  <div style="background:#0d1520;border:1px solid #1e3050;border-radius:8px;padding:10px 16px;overflow-x:auto">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <div style="font-size:0.8rem;font-weight:700;color:#5aabff">Pin Fail Breakdown by Condition (top pins by total BIN8 die count)</div>
      <input id="rc-pin-filter" type="text" placeholder="filter pin..." oninput="_rcRenderPinTable()"
             style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:4px;padding:3px 8px;font-size:0.74rem;width:160px">
    </div>
    <table id="rc-pin-table" style="border-collapse:collapse;font-size:0.76rem;width:100%;min-width:500px"></table>
  </div>
</div>

<div id="tab-surge" class="tab-panel">
  <div style="background:#0f1520;border:1px solid #1e3050;border-radius:8px;padding:12px 18px;margin-bottom:14px">
    <div style="font-size:0.88rem;font-weight:700;color:#4ecdc4;margin-bottom:8px">Pre-Surge vs Post-Surge Resistance &mdash; Surge Delta Analysis</div>

    <!-- Lot / wafer coverage table -->
    <div style="overflow-x:auto;margin-bottom:10px">
    <table style="border-collapse:collapse;font-size:0.78rem;width:auto">
      <thead>
        <tr style="background:#131a2a;color:#8ab4d4">
          <th style="padding:5px 10px;border:1px solid #2a4060;text-align:left">Lot</th>
          <th style="padding:5px 10px;border:1px solid #2a4060">Program</th>
          <th style="padding:5px 10px;border:1px solid #2a4060">Wafers</th>
          <th style="padding:5px 10px;border:1px solid #2a4060">PRESURGE port</th>
          <th style="padding:5px 10px;border:1px solid #2a4060">PRESURGE kills BIN8?</th>
          <th style="padding:5px 10px;border:1px solid #2a4060;text-align:right">BIN8 killed&nbsp;@&nbsp;PRESURGE</th>
          <th style="padding:5px 10px;border:1px solid #2a4060">POSTSURGE kill</th>
          <th style="padding:5px 10px;border:1px solid #2a4060;text-align:right">BIN8 killed&nbsp;@&nbsp;POSTSURGE</th>
          <th style="padding:5px 10px;border:1px solid #2a4060;text-align:right">Paired in this analysis</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#e0eaf8;font-weight:700">Q603S6T01</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:center">61A</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#8ab4d4">W503&ndash;W512</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#ffd166;font-weight:700;text-align:center">K_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#ff6b6b;text-align:center">&#10004; Yes &mdash; kill port</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#ff6b6b;text-align:right;font-weight:700">152</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:center">K_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:right">24</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#4ecdc4;text-align:right"><b>24</b> <span style="color:#556677;font-size:0.72rem">(passed pre, failed post)</span></td>
        </tr>
        <tr style="background:#0d1420">
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#e0eaf8;font-weight:700">Q603S6T02</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:center">61A</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#8ab4d4">W501, W502</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#48cae4;font-weight:700;text-align:center">E_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#4ecdc4;text-align:center">&#10005; Diagnostic only</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#556677;text-align:right">0</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:center">K_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:right">3</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#4ecdc4;text-align:right"><b>3</b></td>
        </tr>
        <tr>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#e0eaf8;font-weight:700">Q603S6T03</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:center">61B</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#8ab4d4">W506&ndash;W512</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#48cae4;font-weight:700;text-align:center">E_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#4ecdc4;text-align:center">&#10005; Diagnostic only</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#556677;text-align:right">0</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:center">K_START</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#c0ccd8;text-align:right">92</td>
          <td style="padding:5px 10px;border:1px solid #1e3050;color:#4ecdc4;text-align:right"><b>92</b></td>
        </tr>
        <tr style="background:#0a1018;font-weight:700">
          <td style="padding:5px 10px;border:1px solid #2a4060;color:#8ab4d4" colspan="5">Total</td>
          <td style="padding:5px 10px;border:1px solid #2a4060;color:#ff6b6b;text-align:right">152</td>
          <td style="padding:5px 10px;border:1px solid #2a4060"></td>
          <td style="padding:5px 10px;border:1px solid #2a4060;color:#c0ccd8;text-align:right">119</td>
          <td style="padding:5px 10px;border:1px solid #2a4060;color:#4ecdc4;text-align:right">119 of 349</td>
        </tr>
      </tbody>
    </table>
    </div>

    <div style="font-size:0.78rem;color:#667788;line-height:1.6;margin-top:4px">
      &#9888; The 152 T01 dies killed at <b style="color:#ffd166">PRESURGE K_START</b> have no post-surge data and are <b>excluded</b> from the paired analysis below &mdash; they are the analysis blind spot.
      Only dies that survived PRESURGE (T01: 24 marginal dies; T02+T03: all dies) reach POSTSURGE and can be paired.
      &mdash; For each of the <b id="sg-n-total">?</b> paired dies, pre-surge (x) vs post-surge (y) resistance is plotted in m&Omega;.
      The y&nbsp;=&nbsp;x line marks zero surge-induced delta; dashed lines mark PASS p99 thresholds.
    </div>
  </div>
  <!-- DPS type + rail checkboxes (populated by JS) -->
  <div style="margin-bottom:8px">
    <span style="font-size:0.72rem;color:#556677;font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin-right:8px">DPS Type:</span>
    <span id="sg-dps-cbs" style="display:inline-flex;gap:8px;flex-wrap:wrap"></span>
  </div>
  <div style="margin-bottom:14px">
    <span style="font-size:0.72rem;color:#556677;font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin-right:8px">Rail:</span>
    <span id="sg-rail-cbs" style="display:inline-flex;gap:6px;flex-wrap:wrap"></span>
  </div>
  <div class="row2" style="margin-bottom:14px">
    <div class="card" style="padding:8px"><div id="sg-scatter" style="height:380px"></div></div>
    <div class="card" style="padding:8px"><div id="sg-delta" style="height:380px"></div></div>
  </div>
  <div class="card" style="margin-bottom:14px">
    <h3>Quadrant Classification &mdash; <span id="sg-rail-label">VCCIA</span></h3>
    <div id="sg-quad-tbl"></div>
  </div>
  <div id="sg-conclusion"></div>
</div>

<!-- REPORT -->
<div id="tab-report" class="tab-panel">
  <div style="max-width:1100px;margin:0 auto">
    __REPORT_HTML__
  </div>
</div>

<!-- TEST FLOW -->
<div id="tab-flowdiag" class="tab-panel">
  <div style="padding:7px 14px 6px;background:#141824;border-bottom:1px solid #2a3550;display:flex;align-items:center;gap:14px;flex-wrap:wrap">
    <span style="font-size:0.78rem;color:#8899aa">Test flow &mdash; CONT_ tests by phase. <b style="color:#c0ccd8">Click a phase box</b> to see force conditions and BIN8 kill stats. Scroll right to see all phases &#8594;</span>
    <span id="flow-summary" style="font-size:0.78rem;color:#4a7aaa;margin-left:auto"></span>
  </div>
  <div id="flow-flw-warn" style="display:none;background:#1a1000;border-bottom:1px solid #5a3800;padding:5px 14px;font-size:0.75rem;color:#c08020">&#9888; Program FLW/MTPL not accessible &mdash; force conditions unavailable. BIN8 kill counts are still shown from CSV data.</div>
  <!-- Filter bar — always visible -->
  <div id="flow-filter-bar" style="display:flex;gap:10px;align-items:center;padding:6px 14px;background:#0f1520;border-bottom:1px solid #1e3050;flex-wrap:wrap">
    <div id="flow-prog-wrap" style="display:flex;align-items:center;gap:6px">
      <span style="font-size:0.71rem;color:#556677;font-weight:700;text-transform:uppercase;letter-spacing:.04em">Prog</span>
      <div id="flow-prog-cbs" style="display:flex;flex-direction:row;gap:6px;flex-wrap:wrap"></div>
    </div>
    <div class="dd-wrap">
      <button id="flow-lot-btn" class="dd-btn" onclick="_ddToggle('flow-lot-panel',event)">Lot/Wafer&nbsp;<span class="dd-lbl">All</span>&nbsp;<span class="dd-arr">&#9660;</span></button>
      <div class="dd-panel" id="flow-lot-panel" onclick="event.stopPropagation()">
        <input class="dd-search" type="text" placeholder="Search lot or wafer&#8230;" oninput="_ddSearch('flow-lot-wfr-tree',this.value)">
        <div class="dd-actions">
          <button onclick="_wmTreeToggleAll('flow-lot-wfr-tree',true);drawFlowDiagram();_ddLabelUpdate('flow-lot-btn','flow-lot-wfr-tree','lot')">All</button>
          <button onclick="_wmTreeToggleAll('flow-lot-wfr-tree',false);drawFlowDiagram();_ddLabelUpdate('flow-lot-btn','flow-lot-wfr-tree','lot')">None</button>
        </div>
        <div id="flow-lot-wfr-tree" class="dd-tree-inner"></div>
      </div>
    </div>
    <div class="dd-wrap">
      <button id="flow-fb-btn" class="dd-btn" onclick="_ddToggle('flow-ib-panel',event)">IB/Func&nbsp;<span class="dd-lbl">All</span>&nbsp;<span class="dd-arr">&#9660;</span></button>
      <div class="dd-panel" id="flow-ib-panel" onclick="event.stopPropagation()">
        <input class="dd-search" type="text" placeholder="Search IB or FB&#8230;" oninput="_ddSearch('flow-ib-fb-tree',this.value)">
        <div class="dd-actions">
          <button onclick="_wmTreeToggleAll('flow-ib-fb-tree',true);drawFlowDiagram();_ddLabelUpdate('flow-fb-btn','flow-ib-fb-tree','ib')">All</button>
          <button onclick="_wmTreeToggleAll('flow-ib-fb-tree',false);drawFlowDiagram();_ddLabelUpdate('flow-fb-btn','flow-ib-fb-tree','ib')">None</button>
        </div>
        <div id="flow-ib-fb-tree" class="dd-tree-inner"></div>
      </div>
    </div>
    <span id="flow-filter-cnt" style="font-size:0.75rem;color:#445566;margin-left:auto"></span>
  </div>
  <div style="overflow-x:auto;padding:14px 14px 6px">
    <div id="flow-diagram" style="display:flex;gap:18px;align-items:flex-start;min-height:140px"></div>
  </div>
  <div style="border-top:1px solid #252d40;margin:0 14px"></div>
  <div id="flow-detail" style="padding:12px 14px;overflow-y:auto;max-height:520px">
    <div style="color:#445566;font-size:0.8rem;padding:8px 0">Click a test group above to see force conditions and BIN8 kill stats.</div>
  </div>
</div>

<!-- TEST SETUP STICKY POPUPS CONTAINER -->
<div id="ts-popup-container" style="position:fixed;bottom:16px;right:16px;z-index:9500;display:flex;flex-direction:column-reverse;gap:10px;pointer-events:none"></div>

<!-- PIN DISTRIBUTION MODAL -->
<div id="pin-dist-modal" style="display:none;position:fixed;inset:0;z-index:9000;pointer-events:none">
  <div id="pdm-box" style="pointer-events:all;position:absolute;top:60px;left:50%;transform:translateX(-50%);background:#0f1a2e;border:1px solid #2a4060;border-radius:10px;width:min(860px,95vw);max-height:90vh;overflow:auto;resize:both;display:flex;flex-direction:column;box-shadow:0 12px 48px rgba(0,0,0,0.82)">
    <div id="pdm-hdr" style="cursor:move;user-select:none;flex-shrink:0;background:#131c30;border-bottom:1px solid #1e3050;padding:10px 16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <span id="pdm-title" style="font-size:0.95rem;font-weight:700;color:#e0eaf8"></span>
      <select id="pdm-pin-sel" onchange="showPinDist(this.value)" onclick="event.stopPropagation()" style="background:#131a2a;border:1px solid #2a5080;color:#c0deff;border-radius:4px;padding:2px 6px;font-size:0.78rem;cursor:pointer;max-width:180px"></select>
      <span id="pdm-col" style="font-size:0.72rem;color:#445566"></span>
      <span id="pdm-mode" style="font-size:0.72rem;padding:2px 8px;border-radius:10px;background:#1e3050;color:#8ab4d4"></span>
      <span style="font-size:0.65rem;color:#334455;margin-left:4px">drag to move</span>
      <button onclick="closePinDist()" style="margin-left:auto;background:#1e3050;border:1px solid #2a5080;color:#8ab4d4;cursor:pointer;font-size:0.75rem;font-weight:700;padding:3px 12px;border-radius:5px;letter-spacing:.03em" title="Close">Close</button>
    </div>

    <div style="padding:6px 16px 6px;border-bottom:1px solid #0d1828;background:#080e18;display:none;flex-shrink:0;resize:both;overflow:auto;min-height:90px;min-width:240px;max-height:440px" id="pdm-focus-filter"></div>
    <div style="padding:12px 16px;overflow-y:auto;flex:1">
    <!-- Phase stats — always visible, updates live with lot/wafer/pass filter -->
    <div id="pdm-xp-stats-card" style="display:none;background:#07111a;border:1px solid #1a3040;border-radius:6px;margin-bottom:10px;padding:8px 12px">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:5px;font-size:0.74rem;color:#8ab4d4">
        <span style="font-size:0.75rem;font-weight:700;color:#69f0ae">&#9776; Phase Stats</span>
        <label>Pass filter: <select id="xp-st-passph" onchange="_xpRenderStats()" style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem"><option value="">(none &mdash; all dies)</option></select></label>
        <span id="xp-st-pass-info" style="font-size:0.69rem;color:#445566"></span>
        <span style="font-size:0.69rem;color:#334455;margin-left:auto" id="xp-st-src-note"></span>
      </div>
      <div id="pdm-xp-stats-tbl" style="overflow-x:auto"></div>
    </div>
    <!-- Parametric Analysis panel — summary always; per-die tabs require Live Mode -->
    <div id="pdm-xphase" style="display:none;border-bottom:1px solid #1a3040;background:#07111a;margin:-12px -16px 12px;padding:6px 16px 0">
      <div style="display:flex;align-items:center;gap:8px;padding-bottom:6px;cursor:pointer" onclick="_xpToggle()">
        <span id="pdm-xp-chevron" style="font-size:0.72rem;color:#69f0ae;transition:transform 0.15s">&#9654;</span>
        <span style="font-size:0.78rem;font-weight:700;color:#69f0ae">Parametric Analysis</span>
        <div style="display:flex;gap:3px;flex-wrap:wrap" id="pdm-xp-tabs" onclick="event.stopPropagation()">
          <button class="pdm-xp-tab" onclick="_xpShow('violin',this)">&#9675; Box/Violin</button>
          <button class="pdm-xp-tab" onclick="_xpShow('sdhist',this)">&#9636; Histogram</button>
          <button class="pdm-xp-tab pdm-xp-live" onclick="_xpShow('xyplot',this)">&#9711; XY Plot</button>
          <button class="pdm-xp-tab pdm-xp-live" onclick="_xpShow('overlay',this)">&#8767; Overlay</button>
          <button class="pdm-xp-tab pdm-xp-live" onclick="_xpShow('cdf',this)">&#8767; CDF</button>
          <button class="pdm-xp-tab pdm-xp-live" onclick="_xpShow('wmap',this)">&#9632; Wafer</button>
        </div>
      </div>
      <div id="pdm-xp-body" style="display:none;padding-bottom:6px">
        <!-- Histogram & Stats tab — the primary per-pin view (Overlay SDS + SDT) -->
        <div id="pdm-xp-sdhist" class="pdm-xp-panel" style="display:block">
          <div id="pdm-filter-note" style="font-size:0.72rem;color:#445566;margin-bottom:6px"></div>
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
            <label style="font-size:0.73rem;color:#8ab4d4;display:inline-flex;align-items:center;gap:4px;cursor:pointer">
              <input type="checkbox" id="pdm-overlay-cb" onchange="_pdmOverlayToggle()" style="accent-color:#ffd166;cursor:pointer"> Overlay SDS
            </label>
            <label style="font-size:0.73rem;color:#8ab4d4;display:inline-flex;align-items:center;gap:4px;cursor:pointer">
              <input type="checkbox" id="pdm-overlay-sdt" onchange="_pdmOverlayToggle()" style="accent-color:#c77dff;cursor:pointer"> + SDT
            </label>
            <label style="font-size:0.73rem;color:#8ab4d4;display:inline-flex;align-items:center;gap:4px;cursor:pointer">
              <input type="checkbox" id="pdm-sigma-cb" onchange="_pdmToggleSigma()" style="accent-color:#ffd166;cursor:pointer"> σ lines
            </label>
            <label style="font-size:0.73rem;color:#8ab4d4;display:inline-flex;align-items:center;gap:4px;cursor:pointer;margin-left:auto">
              <input type="checkbox" id="xp-hist-logy" checked onchange="_pdmToggleLog()" style="accent-color:#48cae4;cursor:pointer"> Log Y
            </label>
            <span id="pdm-overlay-note" style="font-size:0.68rem;color:#445566"></span>
          </div>
          <div id="pdm-overlay-ph-filter" style="display:none;flex-wrap:wrap;gap:4px;padding:4px 0 2px"></div>
          <div id="pdm-hist-phases" style="display:flex;flex-wrap:wrap;gap:3px;margin-bottom:6px"></div>
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:0.74rem;color:#8ab4d4">
            <label>Pass filter &mdash; only dies passing: <select id="xp-hist-passph" onchange="_histPassCacheInvalidate();_pdmOverlayToggle()" style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem"><option value="">(none &mdash; all dies)</option></select></label>
            <span id="xp-hist-pass-info" style="font-size:0.69rem;color:#445566"></span>
          </div>
          <div id="pdm-chart" style="height:300px;margin-bottom:14px"></div>
          <div id="pdm-stats" style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px"></div>
          <div id="pdm-detail-tbl" style="display:none">
            <div style="font-size:0.8rem;color:#8ab4d4;font-weight:700;margin-bottom:6px">&#9733; Full data &mdash; filtered die list <span id="pdm-det-cnt" style="color:#556677;font-weight:400"></span></div>
            <div class="tbl-wrap" style="max-height:200px">
              <table><thead><tr><th>Lot</th><th>W#</th><th>X</th><th>Y</th><th>Value (mV)</th><th>vs USL</th></tr></thead>
              <tbody id="pdm-det-tbody"></tbody></table>
            </div>
          </div>
        </div>
        <!-- Overlay histogram tab — available in all modes -->
        <div id="pdm-xp-overlay" class="pdm-xp-panel" style="display:none">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px;font-size:0.74rem;color:#8ab4d4">
            <label><input type="checkbox" id="xp-ov-logy" checked onchange="_xpRenderOverlay()" style="accent-color:#a78bfa"> Log Y</label>
            <label><input type="checkbox" id="xp-ov-norm" onchange="_xpRenderOverlay()" style="accent-color:#69f0ae"> Normalize (density)</label>
            <label><input type="checkbox" id="xp-ov-usl" checked onchange="_xpRenderOverlay()" style="accent-color:#ff6b6b"> USL line</label>
          </div>
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:0.74rem;color:#8ab4d4">
            <label>Pass filter &mdash; only dies passing: <select id="xp-ov-passph" onchange="_xpRenderOverlay()" style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem"><option value="">(none &mdash; all dies)</option></select></label>
            <span id="xp-ov-pass-info" style="font-size:0.69rem;color:#445566"></span>
          </div>
          <div id="pdm-xp-overlay-chart" style="height:340px"></div>
          <div id="pdm-xp-overlay-stats" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;overflow-x:auto"></div>
        </div>
        <div id="pdm-xp-grid" class="pdm-xp-panel" style="display:none">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">
            <span style="font-size:0.71rem;color:#445566" id="pdm-xp-grid-info"></span>
            <button onclick="_xpDownloadGrid()" style="margin-left:auto;font-size:0.71rem;padding:2px 10px;background:#1e3050;border:1px solid #2a5080;color:#8ab4d4;border-radius:4px;cursor:pointer">&#11015; Download CSV</button>
          </div>
          <div id="pdm-xp-grid-tbl" style="overflow-x:auto;max-height:260px"></div>
        </div>

        <div id="pdm-xp-wmap" class="pdm-xp-panel" style="display:none">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px;font-size:0.74rem;color:#8ab4d4">
            <label>Phase: <select id="xp-wm-ph" onchange="_xpWmPhaseChange()" style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem"></select></label>
            <label>Wafer: <select id="xp-wm-wfr" onchange="_xpRenderWmap()" style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem"></select></label>
            <label>Color: <select id="xp-wm-mode" onchange="_xpWmModeChange()" style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem">
              <option value="val">Value (mV)</option>
              <option value="delta">Delta vs ref phase</option>
            </select></label>
            <label id="xp-wm-ref-wrap" style="display:none">Ref: <select id="xp-wm-ref" onchange="_xpRenderWmap()" style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem"></select></label>
          </div>
          <div style="font-size:0.69rem;color:#445566;margin-bottom:5px">&#9679; Dark/missing spots = no measurement for that die in this phase (screened earlier in flow or not tested)</div>
          <div id="pdm-xp-wmap-chart" style="height:520px"></div>
        </div>
        <div id="pdm-xp-cdf" class="pdm-xp-panel" style="display:none">
          <div style="background:#090f1a;border-left:3px solid #69f0ae;border-radius:0 5px 5px 0;padding:7px 12px;margin-bottom:8px;display:flex;align-items:center;gap:16px;flex-wrap:wrap">
            <span style="font-size:0.72rem;color:#8ab4d4">Cumulative distribution &mdash; each curve is one phase. <b style="color:#c0deff">Rightward tail shift = degradation.</b> Tick marks &#9664; = USL crossing per phase.</span>
            <label style="margin-left:auto;font-size:0.72rem;color:#8ab4d4;display:inline-flex;align-items:center;gap:4px;cursor:pointer"><input type="checkbox" id="xp-cdf-log" onchange="_xpRenderCdf()" style="accent-color:#69f0ae"> Log X</label>
          </div>
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:0.74rem;color:#8ab4d4">
            <label>Pass filter &mdash; only dies passing: <select id="xp-cdf-passph" onchange="_xpRenderCdf()" style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem"><option value="">(none &mdash; all dies)</option></select></label>
            <span id="xp-cdf-pass-info" style="font-size:0.69rem;color:#445566"></span>
          </div>
          <div id="pdm-xp-cdf-chart" style="height:420px"></div>
          <div id="pdm-xp-cdf-stats" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:10px"></div>
        </div>
        <!-- XY Scatter Plot tab — requires Live Mode -->
        <div id="pdm-xp-xyplot" class="pdm-xp-panel" style="display:none">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px;font-size:0.74rem;color:#8ab4d4">
            <label>X axis: <select id="xp-xy-x" onchange="_xpXyAxisChange()" style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem"></select></label>
            <label>Y axis: <select id="xp-xy-y" onchange="_xpXyAxisChange()" style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem"></select></label>
            <label><input type="checkbox" id="xp-xy-usl" checked onchange="_xpRenderXyPlot()" style="accent-color:#ff6b6b"> USL lines</label>
            <label><input type="checkbox" id="xp-xy-diag" checked onchange="_xpRenderXyPlot()" style="accent-color:#ffd166"> y=x diagonal</label>
            <label><input type="checkbox" id="xp-xy-sigma" onchange="_xpRenderXyPlot()" style="accent-color:#48cae4"> σ lines</label>
            <label><input type="checkbox" id="xp-xy-logx" onchange="_xpRenderXyPlot()" style="accent-color:#a78bfa"> Log X</label>
            <label><input type="checkbox" id="xp-xy-logy" onchange="_xpRenderXyPlot()" style="accent-color:#a78bfa"> Log Y</label>
            <label style="margin-left:8px"><input type="checkbox" id="xp-xy-autoscale" checked onchange="_xpXyToggleAutoScale()" style="accent-color:#69f0ae"> Auto range</label>
            <label style="margin-left:8px"><input type="checkbox" id="xp-xy-density" onchange="_xpRenderXyPlot()" style="accent-color:#ff9f43"> Density color</label>
          </div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px;font-size:0.74rem;color:#8ab4d4">
            <label>Pass filter &mdash; only dies passing: <select id="xp-xy-passph" onchange="_xpXyPassChange()" style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem"><option value="">(none &mdash; all dies)</option></select></label>
            <span id="xp-xy-pass-info" style="font-size:0.69rem;color:#445566"></span>
          </div>
          <div id="xp-xy-range-row" style="display:none;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:6px;font-size:0.73rem;color:#8ab4d4">
            <span style="color:#556677">X range:</span>
            <input type="number" id="xp-xy-xmin" placeholder="auto" onchange="_xpRenderXyPlot()" style="width:72px;background:#0d1828;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.72rem">
            <span style="color:#334455">&ndash;</span>
            <input type="number" id="xp-xy-xmax" placeholder="auto" onchange="_xpRenderXyPlot()" style="width:72px;background:#0d1828;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.72rem">
            <span style="color:#556677;margin-left:8px">Y range:</span>
            <input type="number" id="xp-xy-ymin" placeholder="auto" onchange="_xpRenderXyPlot()" style="width:72px;background:#0d1828;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.72rem">
            <span style="color:#334455">&ndash;</span>
            <input type="number" id="xp-xy-ymax" placeholder="auto" onchange="_xpRenderXyPlot()" style="width:72px;background:#0d1828;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.72rem">
            <button onclick="_xpXyAutoRange()" style="font-size:0.71rem;padding:2px 9px;background:#1e3050;border:1px solid #2a5080;color:#8ab4d4;border-radius:4px;cursor:pointer">&#8634; Reset</button>
          </div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px;font-size:0.73rem;color:#8ab4d4">
            <span style="color:#556677">Y&minus;X offset threshold:</span>
            <input type="range" id="xp-xy-offset" min="-500" max="500" step="0.1" value="0" oninput="document.getElementById('xp-xy-offset-num').value=this.value;_xpUpdateXyOffset()" style="width:140px;accent-color:#69f0ae">
            <input type="number" id="xp-xy-offset-num" value="0" step="0.1" oninput="var v=+this.value;document.getElementById('xp-xy-offset').value=Math.max(-500,Math.min(500,v));_xpUpdateXyOffset()" style="width:64px;background:#0d1828;border:1px solid #2a4060;color:#69f0ae;border-radius:3px;padding:2px 5px;font-size:0.73rem;font-weight:700;text-align:right">
            <span style="color:#445566">mV</span>
            <button onclick="document.getElementById('xp-xy-offset').value=0;document.getElementById('xp-xy-offset-num').value=0;_xpUpdateXyOffset()" style="font-size:0.69rem;padding:1px 7px;background:#1e3050;border:1px solid #2a5080;color:#8ab4d4;border-radius:3px;cursor:pointer">Reset</button>
          </div>
          <div id="pdm-xp-xyplot-chart" style="height:380px"></div>
          <div id="pdm-xp-xyplot-stats" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;font-size:0.72rem"></div>
          <div id="pdm-xp-xyplot-offset-stats" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:6px;font-size:0.72rem"></div>
          <div id="pdm-xp-xyplot-sigma" style="margin-top:10px;overflow-x:auto"></div>
        </div>
        <div id="pdm-xp-violin" class="pdm-xp-panel">
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:4px;font-size:0.74rem;color:#8ab4d4">
            <label>Mode: <select id="xp-vl-mode" onchange="_xpRenderViolin()" style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem">
              <option value="box">Box</option>
              <option value="violin">Violin</option>
            </select></label>
            <label><input type="checkbox" id="xp-vl-pts" checked onchange="_xpRenderViolin()" style="accent-color:#4ecdc4"> Outlier pts</label>
            <label><input type="checkbox" id="xp-vl-usl" checked onchange="_xpRenderViolin()" style="accent-color:#ff6b6b"> USL line</label>
            <label><input type="checkbox" id="xp-vl-sigma" checked onchange="_xpRenderViolin()" style="accent-color:#ffd166"> σ markers</label>
            <label><input type="checkbox" id="xp-vl-hidesdt" onchange="_xpRenderViolin()" style="accent-color:#c77dff"> Show SDT</label>
            <label><input type="checkbox" id="xp-vl-autorange" checked onchange="_vlToggleAutoRange()" style="accent-color:#69f0ae"> Auto-adjust Y</label>
          </div>
          <div id="xp-vl-range-row" style="display:none;align-items:center;gap:8px;margin-bottom:4px;font-size:0.74rem;color:#8ab4d4">
            <span>Y min:</span><input id="xp-vl-ymin" type="number" step="any" oninput="_vlApplyManualRange()" style="width:80px;background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem">
            <span>Y max:</span><input id="xp-vl-ymax" type="number" step="any" oninput="_vlApplyManualRange()" style="width:80px;background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem">
            <button onclick="_vlAutoFillRange()" style="font-size:0.69rem;padding:1px 8px;background:#1e3050;border:1px solid #2a5080;color:#8ab4d4;border-radius:3px;cursor:pointer">Auto-fill</button>
          </div>
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:0.74rem;color:#8ab4d4">
            <label>Pass filter &mdash; only dies passing: <select id="xp-vl-passph" onchange="_xpRenderViolin()" style="background:#131a2a;border:1px solid #2a4060;color:#c0ccd8;border-radius:3px;padding:2px 5px;font-size:0.73rem"><option value="">(none — all dies)</option></select></label>
            <span id="xp-vl-pass-info" style="font-size:0.69rem;color:#445566"></span>
          </div>
          <div id="pdm-vl-phase-tabs" style="display:flex;flex-wrap:wrap;gap:3px;margin-bottom:6px"></div>
          <div id="pdm-xp-violin-chart" style="height:400px"></div>
          <div id="pdm-xp-vl-pills" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px"></div>
          <div id="pdm-xp-vl-following" style="margin-top:0"></div>
          <div id="pdm-xp-violin-stats" style="margin-top:8px;overflow-x:auto"></div>
        </div>
      </div><!-- /pdm-xp-body -->
    </div><!-- /pdm-xphase -->
    </div><!-- /single-scroll -->
  </div><!-- /pdm-box -->
</div><!-- /pin-dist-modal -->
<div id="pin-dist-picker" style="display:none;position:fixed;top:44px;right:10px;z-index:8000;background:#0f1a2e;border:1px solid #2a4060;border-radius:8px;padding:10px 14px;min-width:260px;box-shadow:0 4px 24px rgba(0,0,0,0.6)">
  <div style="font-size:0.72rem;color:#8ab4d4;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">Pin Distribution Inspector</div>
  <div style="font-size:0.71rem;color:#445566;margin-bottom:8px">Select up to 5 pins for full detail data. Others show per-wafer stats.</div>
  <select id="pdp-sel" style="width:100%;background:#1a2235;border:1px solid #2a4060;color:#c0ccd8;padding:5px 8px;font-size:0.8rem;border-radius:4px;margin-bottom:8px"></select>
  <button onclick="_pdpInspect()" style="width:100%;background:#1e3a5f;border:1px solid #2a5080;color:#8ab4d4;border-radius:5px;padding:6px;cursor:pointer;font-size:0.82rem;font-weight:700">&#128202; Inspect Pin</button>
  <div style="margin-top:8px;font-size:0.7rem;color:#334455">Click any bar in Fail Pareto to inspect that pin directly.</div>
</div>

<!-- FB PARETO MODAL -->
<div id="fb-pareto-modal" class="modal-bg" style="display:none" onclick="if(event.target===this)closeFBModal()">
  <div class="modal-box">
    <div class="modal-hdr">
      <span id="fb-modal-fb"></span>
      <span id="fb-modal-title" style="color:#e0eaf8;font-weight:700;font-size:0.95rem"></span>
      <span id="fb-modal-sub" style="color:#667788;font-size:0.77rem"></span>
      <button onclick="closeFBModal()" style="margin-left:auto;background:#1a2235;border:1px solid #2a4060;color:#8ab4d4;border-radius:5px;padding:4px 12px;cursor:pointer;font-size:0.85rem">× Close</button>
    </div>
    <div class="modal-body">
      <div id="fb-modal-chart" style="height:340px;margin-bottom:14px"></div>
      <div id="fb-modal-table"></div>
    </div>
  </div>
</div>

<script>
// ── Single-program data ────────────────────────────────────────────────────
const PROG_NAMES    = __PROG_NAMES__;
var   _curProg      = __FIRST_PROG__;

// ── PIN DISTRIBUTION ──────────────────────────────────────────────────────────
var _pdmOpen = false;
var _pdmPin = null;
var _pdmPhase = null;
var _pdmLogY = true;  // default log scale for Y-axis
var _pdmShowSigma = false;  // sigma lines hidden by default
// Focus / Live Mode state — initialised to false here; set to true after constants are declared below
var _focusModeActive = false;
var _pdmFocusFilter  = null;  // {lots: Set, wfrs: Set} — modal-local, persists across phase switches
// Cache for cross-die index used by XY plot (invalidated on pin/filter change)
var _xpIdxCache = { pin:null, filtKey:null, idx:null };
function _xpIdxCacheInvalidate(){ _xpIdxCache.pin=null; _xpIdxCache.filtKey=null; _xpIdxCache.idx=null; }

// Cache for pass-filter keys and recomputed phase data (invalidated on filter/pass-phase change)
var _histPassCache = { pin:null, passPh:null, filtKey:null, passKeys:null, phaseData:{} };
function _histPassCacheInvalidate(){ _histPassCache.pin=null; _histPassCache.passPh=null; _histPassCache.filtKey=null; _histPassCache.passKeys=null; _histPassCache.phaseData={}; }

function _focusHasPin(pin){ return _focusModeActive && RAW_PIN_DATA && !!RAW_PIN_DATA[pin]; }

// ── on-the-fly histogram + stats from RAW_PIN_DATA (or fallback DETAIL_PINS) ──
function _recomputeFromRaw(pin, phase, selLots, selWfrs, passKeys, rawSrcOverride){
  var _src = rawSrcOverride || (RAW_PIN_DATA && RAW_PIN_DATA[pin]) || (DETAIL_PINS && DETAIL_PINS[pin]) || {};
  var rows = _src[phase];
  if(!rows||!rows.length) return null;
  var phData = PIN_DISTRIB[pin] ? ((PIN_DISTRIB[pin].phases||{})[phase]||{}) : {};
  var usl = phData.usl != null ? phData.usl : (PIN_DISTRIB[pin]||{}).usl;
  var lsl = phData.lsl != null ? phData.lsl : (PIN_DISTRIB[pin]||{}).lsl;
  // filter rows
  var vals = [];
  rows.forEach(function(r){ if(selLots.has(LOTS[r[0]])&&selWfrs.has(r[1])) {
    if(!passKeys||passKeys.has(r[1]+'::'+r[2]+'::'+r[3])) vals.push(r[4]);
  }});
  if(!vals.length) return null;
  var n = vals.length;
  var sum=0; for(var i=0;i<n;i++) sum+=vals[i];
  var mean = sum/n;
  var varSum=0; for(var i=0;i<n;i++){ var d=vals[i]-mean; varSum+=d*d; }
  var sigma = Math.sqrt(varSum/n);
  var sv = vals.slice().sort(function(a,b){return a-b;});
  function _q(frac){ var idx=frac*(n-1),lo=Math.floor(idx),hi=Math.min(lo+1,n-1); return sv[lo]+(idx-lo)*(sv[hi]-sv[lo]); }
  var median=_q(0.5), p1=_q(0.01), p99=_q(0.99);
  var n_fail=0;
  if(usl!=null) for(var i=0;i<n;i++) if(vals[i]>usl) n_fail++;
  if(lsl!=null) for(var i=0;i<n;i++) if(vals[i]<lsl) n_fail++;
  // histogram
  var N_BINS=50;
  var lo_edge = Math.min(p1, lsl!=null?lsl:p1);
  var hi_edge = Math.max(p99, usl!=null?usl:p99);
  var spread = hi_edge - lo_edge; if(spread<=0) spread = Math.max(Math.abs(mean)*0.01,0.001);
  lo_edge -= spread*0.05; hi_edge += spread*0.05;
  var bw = (hi_edge - lo_edge)/N_BINS;
  var bins=[],counts=[],counts_fail=[];
  for(var i=0;i<=N_BINS;i++) bins.push(Math.round((lo_edge+i*bw)*1000)/1000);
  for(var i=0;i<N_BINS;i++){ counts.push(0); counts_fail.push(0); }
  vals.forEach(function(v){
    var idx=Math.min(N_BINS-1,Math.max(0,Math.floor((v-lo_edge)/bw)));
    counts[idx]++;
    if((usl!=null&&v>usl)||(lsl!=null&&v<lsl)) counts_fail[idx]++;
  });
  var n3=0,n6=0,n12=0;
  if(sigma>0) vals.forEach(function(v){
    var d=Math.abs(v-mean)/sigma;
    if(d>3) n3++; if(d>6) n6++; if(d>12) n12++;
  });
  var cp=null,cpk=null;
  if(sigma>0&&usl!=null&&lsl!=null){
    cp=Math.round(((usl-lsl)/(6*sigma))*1000)/1000;
    cpk=Math.round((Math.min((usl-mean),(mean-lsl))/(3*sigma))*1000)/1000;
  }
  // wfr_stats rebuilt from raw for filtered-stats recalc compatibility
  var wfr_stats={};
  rows.forEach(function(r){
    var lot=LOTS[r[0]], w=r[1], v=r[4];
    if(!selLots.has(lot)||!selWfrs.has(w)) return;
    var k=lot+'::'+w;
    if(!wfr_stats[k]) wfr_stats[k]={n:0,s:0,s2:0,n3:0,n6:0,n12:0,nf:0};
    var s=wfr_stats[k]; s.n++; s.s+=v; s.s2+=v*v;
    if(sigma>0){ var d=Math.abs(v-mean)/sigma; if(d>3)s.n3++; if(d>6)s.n6++; if(d>12)s.n12++; }
    if((usl!=null&&v>usl)||(lsl!=null&&v<lsl)) s.nf++;
  });
  return {
    col: phData.col||'', usl:usl, lsl:lsl,
    bins:bins, counts:counts, counts_fail:counts_fail,
    mean:Math.round(mean*1000)/1000, sigma:Math.round(sigma*1000)/1000,
    median:Math.round(median*1000)/1000, p1:Math.round(p1*1000)/1000, p99:Math.round(p99*1000)/1000,
    n_total:n, n_fail:n_fail, n3:n3, n6:n6, n12:n12, cp:cp, cpk:cpk,
    wfr_stats:wfr_stats
  };
}

function _initPdmFocusFilter(){
  // Build default: all lots + all wafers found in RAW_PIN_DATA
  var allLots = new Set(LOTS);
  var allWfrs = new Set();
  DIES.forEach(function(d){ allWfrs.add(d.wfr); });
  _pdmFocusFilter = {lots: allLots, wfrs: allWfrs};
}

function _pdmGetFilter(){
  if(_focusModeActive && _pdmFocusFilter) return _pdmFocusFilter;
  return _getPdmFilter();
}

function _pdmToggleLog(){
  var cb=document.getElementById('xp-hist-logy');
  _pdmLogY = cb ? cb.checked : false;
  if(_pdmPin && _pdmPhase) _renderPinDist(_pdmPin, PIN_DISTRIB[_pdmPin], _pdmPin in DETAIL_PINS || _focusHasPin(_pdmPin), _pdmPhase);
}
function _pdmToggleSigma(){
  _pdmShowSigma = document.getElementById('pdm-sigma-cb').checked;
  if(_pdmPin && _pdmPhase) _renderPinDist(_pdmPin, PIN_DISTRIB[_pdmPin], _pdmPin in DETAIL_PINS || _focusHasPin(_pdmPin), _pdmPhase);
}
function closePinDist(){
  document.getElementById('pin-dist-modal').style.display='none';
  _pdmOpen=false; _pdmPin=null; _pdmPhase=null;
}
function _pdStatPill(label,val,color){
  return '<div style="background:#0d1828;border:1px solid #1e3050;border-radius:6px;padding:6px 12px;min-width:90px">'+
    '<div style="font-size:0.67rem;color:#445566;text-transform:uppercase;letter-spacing:.04em;margin-bottom:2px">'+label+'</div>'+
    '<div style="font-size:0.95rem;font-weight:700;color:'+(color||'#c0deff')+'">'+val+'</div></div>';
}
function _getPdmFilter(){
  var treeId = (_activeTabId==='flowdiag') ? 'flow-lot-wfr-tree' : 'par-lot-wfr-tree';
  var selLots=[], selWfrs=[];
  var lotInputs=document.querySelectorAll('#'+treeId+' input[data-type=lot]:checked');
  var wfrInputs=document.querySelectorAll('#'+treeId+' input[data-type=wfr]:checked');
  if(lotInputs.length){ lotInputs.forEach(function(cb){selLots.push(cb.value);}); }
  else { selLots=LOTS.slice(); }
  if(wfrInputs.length){ wfrInputs.forEach(function(cb){selWfrs.push(+cb.value);}); }
  else { var uW=[]; DIES.forEach(function(d){if(uW.indexOf(d.wfr)<0)uW.push(d.wfr);}); selWfrs=uW; }
  return {lots:new Set(selLots), wfrs:new Set(selWfrs)};
}
var _PHASE_CLR = {'Pre-Surge':'#4ecdc4','Post-Surge':'#48cae4','Post-Surge-HT':'#06a0c4',
  'Stress':'#ffd166','SDS-Final':'#ff6b6b','SDT-Start':'#c77dff','SDT-Final':'#a06fdd',
  'ISVM-EDC':'#5aabff','OTHER':'#556677'};
function showPinDist(pin, defaultPhase){
  // Enforce one-window: close die-detail-modal if open
  var _ddm=document.getElementById('die-detail-modal');
  if(_ddm&&_ddm.style.display!=='none') _ddiClose();
  var pd=PIN_DISTRIB[pin];
  if(!pd){ alert('No distribution data for pin: '+pin); return; }
  _pdmPin=pin;
  _histPassCacheInvalidate();  // new pin — clear cached pass keys and phase data
  _xpIdxCacheInvalidate();     // new pin — clear cross-die index cache
  var isDetail= pin in DETAIL_PINS || _focusHasPin(pin);
  var msel=document.getElementById('pdm-pin-sel'); if(msel) msel.value=pin;
  var _modeLabel = _focusHasPin(pin) ? '\u26a1 Live — all pins' : (pin in DETAIL_PINS ? '\u2605 Full data' : 'Stats mode');
  document.getElementById('pdm-mode').textContent=_modeLabel;
  document.getElementById('pdm-mode').style.background = _focusHasPin(pin) ? '#0d2d0d' : '#1e3050';
  document.getElementById('pdm-mode').style.color      = _focusHasPin(pin) ? '#69f0ae' : '#8ab4d4';
  // Ensure focus filter is initialised
  if(_focusModeActive && !_pdmFocusFilter) _initPdmFocusFilter();
  // Build phase tabs
  var phases=pd.phase_list||[];
  // Also add phases present only in RAW_PIN_DATA (may have more phases than pre-computed)
  if(_focusHasPin(pin)){ Object.keys(RAW_PIN_DATA[pin]||{}).forEach(function(ph){ if(phases.indexOf(ph)<0) phases.push(ph); }); }
  // Use defaultPhase if provided and available, else first phase
  _pdmPhase=(defaultPhase&&phases.indexOf(defaultPhase)>=0)?defaultPhase:(phases[0]||null);
  // Render in-modal focus filter panel
  _renderFocusFilterPanel(pin);
  // Cross-phase panel
  _xpPin = pin;
  var xpEl = document.getElementById('pdm-xphase');
  var _xpAvail = _xpHasPhases(pin);
  if(xpEl) xpEl.style.display = _xpAvail ? 'block' : 'none';
  if(_xpAvail){
    var xpBody=document.getElementById('pdm-xp-body');
    if(xpBody) xpBody.style.display='block';
    var chev=document.getElementById('pdm-xp-chevron'); if(chev){chev.style.transform='rotate(90deg)';chev.textContent='\u25BA';}
    // Show live-mode tab opacity cue
    document.querySelectorAll('.pdm-xp-live').forEach(function(b){
      b.style.opacity=_focusModeActive?'1':'0.4';
      b.title=_focusModeActive?'':'Requires Live Mode (re-generate with fewer wafers)';
    });
    // Default tab: Box/Violin (works in both live and non-live mode)
    var defTab = 'violin';
    _xpActiveTab = defTab;
    var allTabs=['stats','overlay','violin','grid','wmap','cdf','xyplot','sdhist'];
    document.querySelectorAll('.pdm-xp-tab').forEach(function(b){ b.classList.remove('active'); });
    document.querySelectorAll('.pdm-xp-tab').forEach(function(b){
      if(b.getAttribute('onclick')&&b.getAttribute('onclick').indexOf("'"+defTab+"'")>=0) b.classList.add('active');
    });
    allTabs.forEach(function(t){
      var el=document.getElementById('pdm-xp-'+t); if(el) el.style.display=t===defTab?'block':'none';
    });
    // Reset pass-filter selectors and XY phase selectors so they repopulate for the new pin
    ['xp-vl-passph','xp-cdf-passph','xp-xy-passph','xp-hist-passph'].forEach(function(id){
      var el=document.getElementById(id); if(!el) return;
      el.innerHTML='<option value="">(none \u2014 all dies)</option>';
    });
    // Reset XY axis selects so they repopulate for the new pin's phase list
    ['xp-xy-x','xp-xy-y','xp-dl-a','xp-dl-b'].forEach(function(id){
      var el=document.getElementById(id); if(!el) return; el.innerHTML='';
    });
    _xyForceAutoRange=true;
    _pdmFilterSearch='';  // reset lot/wafer search on pin switch
    _xpRender();
  }
  // Widen modal for cross-phase
  var modalBox = document.getElementById('pdm-box');
  if(modalBox){ modalBox.style.width = _xpAvail ? 'min(1000px,97vw)' : 'min(860px,95vw)';
    // Reset to center position on each new open
    modalBox.style.transform='translateX(-50%)';
    modalBox.style.left='50%'; modalBox.style.top='60px'; }
  document.getElementById('pin-dist-modal').style.display='block';
  _pdmOpen=true;
  _renderPinDist(pin, pd, isDetail, _pdmPhase);
  // Trigger chart resize after modal is visible and has real dimensions
  setTimeout(function(){ if(typeof Plotly!=='undefined'&&document.getElementById('pdm-chart')) Plotly.Plots.resize('pdm-chart'); }, 120);
}
function _pdmSwitchPhase(pin, phase){
  _pdmPhase=phase;
  var pd=PIN_DISTRIB[pin]||{};
  var isDetail= pin in DETAIL_PINS || _focusHasPin(pin);
  _renderPinDist(pin, pd, isDetail, phase);
}
function _pdmOverlayToggle(){
  if(_pdmPin && _pdmPhase) _renderPinDist(_pdmPin, PIN_DISTRIB[_pdmPin], _pdmPin in DETAIL_PINS || _focusHasPin(_pdmPin), _pdmPhase);
}
function _renderPinDist(pin, pd, isDetail, phase){
  // Focus Live Mode: substitute on-the-fly computed phData
  var _useLive = _focusHasPin(pin);
  // Raw data source: prefer live RAW_PIN_DATA, fall back to embedded DETAIL_PINS per-die rows
  var _rawSrc = (RAW_PIN_DATA && RAW_PIN_DATA[pin]) ? RAW_PIN_DATA[pin]
              : (DETAIL_PINS && DETAIL_PINS[pin]    ? DETAIL_PINS[pin] : null);
  var _hasRaw = !!_rawSrc;
  var _rawPhData = null;
  // Build pass filter keys for histogram if a pass-phase is selected and raw data is available
  var _histPP=(document.getElementById('xp-hist-passph')||{}).value||'';
  var _histPK=null;
  if(_hasRaw){
    var _filt = _pdmFocusFilter || _getPdmFilter();
    // Build a cache key to detect when pass-keys must be rebuilt
    var _filtKey=_histPP+'|'+(_filt.lots?Array.from(_filt.lots).sort().join(','):'')+'|'+(_filt.wfrs?Array.from(_filt.wfrs).sort().join(','):'');
    if(_histPP){
      // Reuse cached passKeys if pin/passPh/filter haven't changed
      if(_histPassCache.pin===pin && _histPassCache.passPh===_histPP && _histPassCache.filtKey===_filtKey){
        _histPK=_histPassCache.passKeys;
      } else {
        var _histPPhase=(PIN_DISTRIB[pin]||{}).phases&&(PIN_DISTRIB[pin].phases||{})[_histPP]||(PIN_DISTRIB[pin]||{});
        var _histPUsl=_histPPhase.usl!=null?_histPPhase.usl:(PIN_DISTRIB[pin]||{}).usl;
        var _histPLsl=_histPPhase.lsl!=null?_histPPhase.lsl:(PIN_DISTRIB[pin]||{}).lsl;
        _histPK=new Set();
        ((_rawSrc[_histPP])||[]).forEach(function(r){
          if(!_filt.lots.has(LOTS[r[0]])||!_filt.wfrs.has(r[1])) return;
          if((_histPUsl==null||r[4]<=_histPUsl)&&(_histPLsl==null||r[4]>=_histPLsl)) _histPK.add(r[0]+'::'+r[1]+'::'+r[2]+'::'+r[3]);
        });
        // Store in cache (also reset per-phase data cache)
        _histPassCache.pin=pin; _histPassCache.passPh=_histPP; _histPassCache.filtKey=_filtKey;
        _histPassCache.passKeys=_histPK; _histPassCache.phaseData={};
      }
      var _hi=document.getElementById('xp-hist-pass-info');
      if(_hi) _hi.textContent=_histPK.size+' dies pass '+_histPP;
    } else {
      // No pass filter — clear any stale cache
      if(_histPassCache.pin!==pin||_histPassCache.passPh||_histPassCache.filtKey!==_filtKey){
        _histPassCache.pin=pin; _histPassCache.passPh=''; _histPassCache.filtKey=_filtKey;
        _histPassCache.passKeys=null; _histPassCache.phaseData={};
      }
      var _hi2=document.getElementById('xp-hist-pass-info'); if(_hi2) _hi2.textContent='';
    }
    if(_useLive || _histPP){
      // Reuse cached per-phase recomputed data if available
      if(!_useLive && _histPassCache.phaseData[phase]){
        _rawPhData = _histPassCache.phaseData[phase];
      } else if(_useLive){
        // Live mode: use RAW_PIN_DATA via _recomputeFromRaw
        _rawPhData = _recomputeFromRaw(pin, phase, _filt.lots, _filt.wfrs, _histPK);
      } else {
        // Detail-pin mode: pass _rawSrc so recomputeFromRaw reads DETAIL_PINS rows
        _rawPhData = _recomputeFromRaw(pin, phase, _filt.lots, _filt.wfrs, _histPK, _rawSrc);
        if(_rawPhData) _histPassCache.phaseData[phase]=_rawPhData;
      }
    }
  } else if(_histPP) {
    // No raw data available — pass filter cannot be applied
    var _hiWarn=document.getElementById('xp-hist-pass-info');
    if(_hiWarn) _hiWarn.textContent='\u26a0 Pass filter requires Live Mode raw data';
  }
  var phData = _rawPhData || ((pd&&pd.phases||{})[phase]);
  if(!phData){
    document.getElementById('pdm-chart').innerHTML='<div style="color:#445566;padding:20px">No data for phase: '+phase+'</div>';
    return;
  }
  // Update col label and highlight active tab
  document.getElementById('pdm-col').textContent=(phData.col||'').replace('TPI_VCC::','').replace('_119325','');
  var _pdPhaseList = (pd&&pd.phase_list)||[];
  if(_useLive) Object.keys(RAW_PIN_DATA[pin]||{}).forEach(function(ph){ if(_pdPhaseList.indexOf(ph)<0) _pdPhaseList.push(ph); });
  // Build (or update) phase selector buttons in histogram tab
  var _phBtnDiv=document.getElementById('pdm-hist-phases');
  if(_phBtnDiv){
    if(_phBtnDiv._pin!==pin){
      _phBtnDiv.innerHTML='';
      _pdPhaseList.forEach(function(ph){
        var _col=_PHASE_CLR[ph]||'#8ab4d4';
        var _btn=document.createElement('button');
        _btn.id='pdm-ph-'+ph.replace(/[^a-zA-Z0-9]/g,'_');
        _btn.textContent=ph;
        _btn.style.cssText='font-size:0.71rem;padding:2px 9px;border-radius:4px;cursor:pointer;border:1px solid '+_col+'44;background:#0d1520;color:'+_col+';transition:background 0.12s,border-color 0.12s';
        _btn.onclick=(function(p){ return function(){ _pdmSwitchPhase(pin,p); }; })(ph);
        _phBtnDiv.appendChild(_btn);
      });
      _phBtnDiv._pin=pin;
    }
    _pdPhaseList.forEach(function(ph){
      var _btn2=document.getElementById('pdm-ph-'+ph.replace(/[^a-zA-Z0-9]/g,'_'));
      if(!_btn2) return;
      var _col2=_PHASE_CLR[ph]||'#8ab4d4';
      _btn2.style.background=ph===phase?_col2+'33':'#0d1520';
      _btn2.style.borderColor=ph===phase?_col2:_col2+'44';
    });
  }
  // In Live mode phData is already filtered — pull aggregates directly from it
  var filt = _useLive ? (_pdmFocusFilter||_getPdmFilter()) : _getPdmFilter();
  var fn=0,fs=0,fs2=0,fn3=0,fn6=0,fn12=0,fnf=0;
  if(_useLive){
    // stats already filtered in _rawPhData
    fn=phData.n_total; fnf=phData.n_fail; fn3=phData.n3; fn6=phData.n6; fn12=phData.n12;
    fs=Math.round(phData.mean*fn*1000)/1000; fs2=0; // fs2 not critical, just for filtSigma fallback
  } else {
  Object.entries(phData.wfr_stats||{}).forEach(function(e){
    var parts=e[0].split('::'), lot=parts[0], wfr=+parts[1];
    if(!filt.lots.has(lot)||!filt.wfrs.has(wfr)) return;
    var s=e[1]; fn+=s.n; fs+=s.s; fs2+=s.s2; fn3+=s.n3; fn6+=s.n6; fn12+=s.n12; fnf+=s.nf;
  });
  } // end else (non-live mode wfr_stats aggregation)
  var filtMean=_useLive?phData.mean:(fn>0?fs/fn:phData.mean);
  var filtSigma=_useLive?phData.sigma:(fn>0?Math.sqrt(Math.max(0,fs2/fn-filtMean*filtMean)):phData.sigma);
  // Use phase-specific USL/LSL (each phase has its own limits)
  var phUsl=phData.usl!=null?phData.usl:pd.usl;
  var phLsl=phData.lsl!=null?phData.lsl:pd.lsl;
  var filtCp=(phUsl&&phLsl&&filtSigma>0)?((phUsl-phLsl)/(6*filtSigma)):null;
  var filtCpk=(phUsl&&phLsl&&filtSigma>0)?Math.min((phUsl-filtMean)/(3*filtSigma),(filtMean-phLsl)/(3*filtSigma)):null;
  var allWfrs=new Set(); DIES.forEach(function(d){allWfrs.add(d.wfr);});
  var isFiltered=filt.lots.size<LOTS.length||filt.wfrs.size<allWfrs.size;
  document.getElementById('pdm-filter-note').textContent=isFiltered?'Filtered: '+filt.lots.size+' lot(s), '+filt.wfrs.size+' wafer(s)':'';
  // Chart
  var _overlayAll = document.getElementById('pdm-overlay-cb')&&document.getElementById('pdm-overlay-cb').checked;
  var bins=phData.bins, cnts=phData.counts, cntsF=phData.counts_fail;
  var midpoints=[]; for(var i=0;i<cnts.length;i++) midpoints.push(round2((bins[i]+bins[i+1])/2));
  var cntsPass=cnts.map(function(c,i){return c-cntsF[i];});
  var phColor=_PHASE_CLR[phase]||'#48cae4';
  var shapes=[]; var lineTraces=[]; var annotations=[];
  // ── Overlay mode: SDS phases only, high-contrast colors ──────────────────
  var overlayTraces=[];
  var _overlayIncSdt=document.getElementById('pdm-overlay-sdt')&&document.getElementById('pdm-overlay-sdt').checked;
  // High-contrast palette for dark-mode overlay (Pre-Surge anchor + SDS phases)
  var _OVERLAY_CLR={'Pre-Surge':'#4ecdc4','Post-Surge':'#48cae4','Post-Surge-HT':'#06a0c4','SDS-Start':'#ffd166','SDS-Final':'#ff6b6b','SDT-Start':'#c77dff','SDT-Final':'#a06fdd'};
  var _SDS_PHASES=['Pre-Surge','Post-Surge','Post-Surge-HT','Stress','SDS-Start','SDS-Final'];
  var _SDT_OVERLAY=['SDT-Start','SDT-Final'];
  // ── Per-phase-group x-axis range (non-overlay) — works in both Live and stats mode ──
  var _PHASE_GROUPS=[['Pre-Surge','Post-Surge','Post-Surge-HT','Stress','SDS-Start','SDS-Final'],['SDT-Start','SDT-Final']];
  var _groupXRange=null;
  if(!_overlayAll){
    var _filt3=_focusHasPin(pin)?(_pdmFocusFilter||_getPdmFilter()):null;
    var _grp=null; _PHASE_GROUPS.forEach(function(g){if(g.indexOf(phase)>=0) _grp=g;});
    if(_grp&&_grp.length>1){
      var _rxMin=Infinity,_rxMax=-Infinity;
      _grp.forEach(function(gph){
        // Use static pre-computed bins for axis range — avoids N recomputeFromRaw calls per render
        var _gpd=(pd&&pd.phases&&pd.phases[gph])||null;
        // For the active phase use already-computed phData (handles live filtered data)
        if(gph===phase) _gpd=phData;
        if(!_gpd||!_gpd.bins) return;
        _rxMin=Math.min(_rxMin,_gpd.bins[0]); _rxMax=Math.max(_rxMax,_gpd.bins[_gpd.bins.length-1]);
      });
      if(isFinite(_rxMin)) _groupXRange=[_rxMin,_rxMax];
    }
  }
  if(_overlayAll && _hasRaw){
    // Mirror overlay-tab pattern: shared x-axis covering all phases; raw data per phase with fallback to precomputed
    var _filt2=_pdmFocusFilter||_getPdmFilter();
    var _allOvPhases=_SDS_PHASES.concat(_overlayIncSdt?_SDT_OVERLAY:[]);
    var _refBins=phData.bins||[];
    var _nBinsOv=_refBins.length-1;
    if(_nBinsOv>0){
      // Extend x-axis to cover all phases (raw value range or precomputed bin range)
      var _xMinOv=_refBins[0], _xMaxOv=_refBins[_nBinsOv];
      _allOvPhases.forEach(function(ph){
        var _rawPh=_rawSrc&&_rawSrc[ph];
        if(_rawPh&&_rawPh.length){
          _rawPh.forEach(function(r){ if(_xMinOv>r[4]) _xMinOv=r[4]; if(_xMaxOv<r[4]) _xMaxOv=r[4]; });
        } else {
          var _b=((pd.phases||{})[ph]||{}).bins||[]; if(_b.length){ _xMinOv=Math.min(_xMinOv,_b[0]); _xMaxOv=Math.max(_xMaxOv,_b[_b.length-1]); }
        }
      });
      var _refMid=[];
      for(var _ri=0;_ri<_nBinsOv;_ri++) _refMid.push(_xMinOv+(_ri+0.5)*(_xMaxOv-_xMinOv)/_nBinsOv);
      function _histFromValsOv(vals){
        var y=new Array(_nBinsOv).fill(0);
        vals.forEach(function(v){
          var bi=Math.floor((v-_xMinOv)/(_xMaxOv-_xMinOv)*_nBinsOv);
          bi=Math.max(0,Math.min(_nBinsOv-1,bi)); y[bi]++;
        });
        return y;
      }
      function _resampleOv(bins,counts){
        var out=new Array(_nBinsOv).fill(0);
        for(var i=0;i<_nBinsOv;i++){
          var mx=_refMid[i],refW=_refBins[i+1]-_refBins[i];
          for(var k=0;k<bins.length-1;k++){
            if(mx>=bins[k]&&mx<bins[k+1]){
              var srcW=bins[k+1]-bins[k];
              out[i]=srcW>0?Math.round(counts[k]*refW/srcW):0; break;
            }
          }
        }
        return out;
      }
      _allOvPhases.forEach(function(ph){
        var _s=(pd.phases||{})[ph]; if(!_s) return;
        var _y, _rawPh=_rawSrc&&_rawSrc[ph];
        if(_rawPh&&_rawPh.length){
          var _filtVals=[];
          _rawPh.forEach(function(r){
            if(!_filt2.lots.has(LOTS[r[0]])||!_filt2.wfrs.has(r[1])) return;
            if(_histPK&&ph===_histPP&&!_histPK.has(r[0]+"::"+r[1]+"::"+r[2]+"::"+r[3])) return;
            _filtVals.push(r[4]);
          });
          if(!_filtVals.length) return;
          _y=_histFromValsOv(_filtVals);
        } else {
          var _bins=_s.bins||[],_counts=_s.counts||[];
          if(!_bins.length) return;
          _y=_resampleOv(_bins,_counts);
        }
        var _pClr=_OVERLAY_CLR[ph]||(_PHASE_CLR[ph]||"#8ab4d4");
        var _isActive=ph===phase;
        overlayTraces.push({x:_refMid,y:_y,type:"scatter",mode:"lines",name:ph,
          fill:_isActive?"tozeroy":"none",fillcolor:_pClr+(_isActive?"33":"00"),
          line:{color:_pClr,width:_isActive?2.5:1.5,shape:"spline",smoothing:0.5},
          opacity:_isActive?0.9:0.65,
          hovertemplate:"%{x:.3f}mV: %{y}<extra>"+ph+"</extra>"});
      });
    }
    var _oNote=document.getElementById("pdm-overlay-note");
    if(_oNote) _oNote.textContent=(_overlayIncSdt?"SDS+SDT":"SDS")+" overlay | x-axis: "+phase+(_refBins.length>1?" ["+round2(_refBins[0])+"\u2013"+round2(_refBins[_refBins.length-1])+"] mV":"");
  } else if(_overlayAll && !_hasRaw){
    // Non-live: resample all phases onto a single shared grid for aligned overlay lines
    var _ovPhases2=_SDS_PHASES.concat(_overlayIncSdt?_SDT_OVERLAY:[]).filter(function(ph){ return !!((pd&&pd.phases)||{})[ph]; });
    var _gMin=Infinity, _gMax=-Infinity;
    _SDS_PHASES.concat(_overlayIncSdt?_SDT_OVERLAY:[]).forEach(function(ph){
      var _b2=((pd&&pd.phases)||{})[ph]; if(!_b2||!_b2.bins||!_b2.bins.length) return;
      _gMin=Math.min(_gMin,_b2.bins[0]); _gMax=Math.max(_gMax,_b2.bins[_b2.bins.length-1]);
    });
    if(!isFinite(_gMin)){ var _rb=phData.bins||[]; _gMin=_rb[0]; _gMax=_rb[_rb.length-1]; }
    // Build shared midpoints grid
    var _nBinsOv2=70, _bwOv2=(_gMax-_gMin)/_nBinsOv2||0.001;
    var _sharedMids2=[];
    for(var _bi2=0;_bi2<_nBinsOv2;_bi2++) _sharedMids2.push(round2(_gMin+(_bi2+0.5)*_bwOv2));
    _ovPhases2.forEach(function(ph){
      var _pd2=(pd.phases||{})[ph]; if(!_pd2||!_pd2.bins) return;
      // Resample pre-computed bins onto shared grid (piecewise-constant)
      var _cnt2=new Array(_nBinsOv2).fill(0);
      for(var _bi2i=0;_bi2i<_nBinsOv2;_bi2i++){
        var _mx=_sharedMids2[_bi2i];
        for(var _k=0;_k<_pd2.bins.length-1;_k++){
          if(_mx>=_pd2.bins[_k]&&_mx<_pd2.bins[_k+1]){
            var _srcW=_pd2.bins[_k+1]-_pd2.bins[_k];
            _cnt2[_bi2i]=_srcW>0?Math.round(_pd2.counts[_k]*_bwOv2/_srcW):0; break;
          }
        }
      }
      var _pClr=_OVERLAY_CLR[ph]||(_PHASE_CLR[ph]||'#8ab4d4');
      var _isActive=ph===phase;
      overlayTraces.push({x:_sharedMids2,y:_cnt2,type:'scatter',mode:'lines',name:ph,
        fill:_isActive?'tozeroy':'none',fillcolor:_pClr+(_isActive?'33':'00'),
        line:{color:_pClr,width:_isActive?2.5:1.5,shape:'spline',smoothing:0.5},
        opacity:_isActive?0.9:0.55,
        hovertemplate:'%{x:.3f}mV: %{y}<extra>'+ph+'</extra>'});
    });
    var _oNote3=document.getElementById('pdm-overlay-note');
    if(_oNote3) _oNote3.textContent=(_overlayIncSdt?'SDS+SDT':'SDS')+' overlay (stats) | x-axis: '+phase+' ['+round2(_gMin)+'\u2013'+round2(_gMax)+'] mV';
    // Build phase toggle buttons with All/Pass/Fail cycle
    var _phFilterDiv=document.getElementById('pdm-overlay-ph-filter');
    if(_phFilterDiv){
      _phFilterDiv.style.display='flex';
      _phFilterDiv._modes={};   // ph -> 0=All,1=Pass,2=Fail,3=None
      _phFilterDiv._ydata={};   // ph -> {all,pass,fail,x} using phase's own midpoints
      // Pre-store all/pass/fail y arrays for each phase (own midpoints, no resampling)
      _ovPhases2.forEach(function(ph){
        var _pd2=(pd.phases||{})[ph]; if(!_pd2||!_pd2.bins) return;
        var _bins2=_pd2.bins;
        var _mids2=_bins2.slice(0,-1).map(function(b,j){ return (_bins2[j]+_bins2[j+1])/2; });
        var _widths2=_bins2.slice(0,-1).map(function(b,j){ return _bins2[j+1]-_bins2[j]; });
        var _yAll=_pd2.counts.slice();
        var _yCf=(_pd2.counts_fail||[]).slice();
        var _yPass=_yAll.map(function(v,i){ return Math.max(0,v-(_yCf[i]||0)); });
        _phFilterDiv._ydata[ph]={x:_mids2, w:_widths2, all:_yAll, pass:_yPass, fail:_yCf};
        _phFilterDiv._modes[ph]=0;
      });
      _phFilterDiv.innerHTML='<span style="font-size:0.68rem;color:#556677;margin-right:4px;align-self:center">Phase filter \u2014 click to cycle All\u2192Pass\u2192Fail\u2192None:</span>';
      var _MODE_SUFFIX=['','  \u2713P','  \u2717F','  \u2205'];
      var _MODE_TITLE=['All dies','Pass only (excl. fails)','Fail only','Hidden (none)'];
      _ovPhases2.forEach(function(ph, ti){
        var _pClr=_OVERLAY_CLR[ph]||(_PHASE_CLR[ph]||'#8ab4d4');
        var _isActive=ph===phase;
        var btn=document.createElement('button');
        btn.id='pdm-ovph-btn-'+ph.replace(/[^a-zA-Z0-9]/g,'_');
        btn.textContent=ph+_MODE_SUFFIX[0];
        btn.title=_MODE_TITLE[0]+' for '+ph;
        btn.style.cssText='font-size:0.69rem;padding:2px 8px;border-radius:10px;cursor:pointer;'
          +'border:1px solid '+_pClr+';background:'+_pClr+'33;color:'+_pClr
          +';font-weight:'+(_isActive?'700':'400')+';transition:opacity 0.15s';
        btn.onclick=(function(p,i){
          return function(){
            var fd=document.getElementById('pdm-overlay-ph-filter');
            var m=(fd._modes[p]+1)%4;
            fd._modes[p]=m;
            var c2=_OVERLAY_CLR[p]||(_PHASE_CLR[p]||'#8ab4d4');
            var yd=fd._ydata[p];
            var _S=['','  \u2713P','  \u2717F','  \u2205'];
            var _T=['All dies','Pass only','Fail only','Hidden'];
            if(m===3){
              Plotly.restyle('pdm-chart',{visible:'legendonly'},[i]);
            } else {
              var newY=m===0?yd.all:m===1?yd.pass:yd.fail;
              var htpl=m===0?'%{x:.2f}mV: %{y} (all)<extra>'+p+'</extra>'
                     :m===1?'%{x:.2f}mV: %{y} (pass)<extra>'+p+' \u2713</extra>'
                           :'%{x:.2f}mV: %{y} (fail)<extra>'+p+' \u2717</extra>';
              Plotly.restyle('pdm-chart',{visible:true,x:[yd.x],width:[yd.w],y:[newY],hovertemplate:htpl},[i]);
            }
            this.textContent=p+_S[m];
            this.title=_T[m]+' for '+p;
            this.style.background=m===0?c2+'33':m===1?'#0d2d0d':m===2?'#2a0a0a':'#07111a';
            this.style.color=m===0?c2:m===1?'#69f0ae':m===2?'#ff6b6b':'#334455';
            this.style.borderColor=m===0?c2:m===1?'#69f0ae':m===2?'#ff6b6b':'#1e3050';
            this.style.opacity=m===3?'0.35':'1';
          };
        })(ph, ti);
        _phFilterDiv.appendChild(btn);
      });
    }
  } else {
    var _oNote2=document.getElementById('pdm-overlay-note');
    if(_oNote2) _oNote2.textContent='';
    var _phFD=document.getElementById('pdm-overlay-ph-filter');
    if(_phFD){ _phFD.style.display='none'; _phFD._hidden=new Set(); }
  }
  // Helper: add a labelled vertical line with hover info as an invisible scatter trace
  // y must be a valid positive value on log scale — use 1 (log10(1)=0, always in range)
  function _vline(xv, color, width, dash, label, hoverLabel){
    shapes.push({type:'line',x0:xv,x1:xv,y0:0,y1:1,yref:'paper',line:{color:color,width:width,dash:dash}});
    // Invisible vertical line trace spanning full height — hoverable anywhere along the line
    lineTraces.push({x:[xv,xv],y:[0,1e9],mode:'lines',line:{color:'rgba(0,0,0,0)',width:12},
      hovertemplate:'<b>'+hoverLabel+'</b><br>'+xv+' mV<extra></extra>',
      name:label,showlegend:false});
    if(label==='USL'||label==='LSL'){
      var _lblY=label==='LSL'?0.91:0.98;
      annotations.push({x:xv, y:_lblY, xref:'x', yref:'paper',
        text:'<b>'+label+'</b> '+xv,
        showarrow:false, xanchor:'left', yanchor:'bottom',
        font:{size:10, color:color},
        bgcolor:'rgba(7,17,26,0.7)', borderpad:2});
    } else if(label==='Mean'){
      annotations.push({x:xv, y:0.97, xref:'x', yref:'paper',
        text:'\u03bc',
        showarrow:false, xanchor:'center', yanchor:'top',
        font:{size:11, color:color},
        bgcolor:'rgba(7,17,26,0.6)', borderpad:2});
    } else if(label.indexOf('\u03C3')>=0){
      // sigma lines: +/- prefix; anchor left for positive, right for negative
      var _isPos=label.charAt(0)==='+';
      // Stagger y by sigma order: 3σ→0.88, 6σ→0.78, 12σ→0.68
      var _sigN=parseInt(label.replace(/[^0-9]/g,''))||3;
      var _sigY=_sigN===3?0.88:_sigN===6?0.78:0.68;
      annotations.push({x:xv, y:_sigY, xref:'x', yref:'paper',
        text:'<i>'+label+'</i>',
        showarrow:false, xanchor:_isPos?'left':'right', yanchor:'middle',
        font:{size:9, color:color},
        bgcolor:'rgba(7,17,26,0.6)', borderpad:1});
    }
  }
  if(_overlayAll && overlayTraces.length){
    // Per-phase USL lines in overlay mode
    var _ovPlForUsl=(_focusHasPin(pin)?(_SDS_PHASES.concat(_overlayIncSdt?_SDT_OVERLAY:[]).filter(function(ph){return !!(RAW_PIN_DATA[pin]||{})[ph];})):(_SDS_PHASES.concat(_overlayIncSdt?_SDT_OVERLAY:[]).filter(function(ph){return !!((pd&&pd.phases)||{})[ph];})));
    var _seenUsl={};
    _ovPlForUsl.forEach(function(ph){
      var _phd2=_focusHasPin(pin)?(_recomputeFromRaw(pin,ph,(_pdmFocusFilter||_getPdmFilter()).lots,(_pdmFocusFilter||_getPdmFilter()).wfrs)||((pd&&pd.phases||{})[ph])):((pd&&pd.phases||{})[ph]);
      if(!_phd2) return;
      var _pusl=_phd2.usl!=null?_phd2.usl:(pd&&pd.usl);
      if(_pusl==null) return;
      var _uslKey=round2(_pusl);
      var _pClr=_OVERLAY_CLR[ph]||(_PHASE_CLR[ph]||'#ff9999');
      var _isActive=ph===phase;
      shapes.push({type:'line',x0:_pusl,x1:_pusl,y0:0,y1:1,yref:'paper',line:{color:_pClr,width:_isActive?2:1,dash:'dot'}});
      lineTraces.push({x:[_pusl],y:[_pdmLogY?1:0],mode:'markers',marker:{size:10,opacity:0},
        hovertemplate:'<b>'+ph+' USL</b><br>'+round2(_pusl)+' mV<extra></extra>',
        name:ph+' USL',showlegend:false});
      if(!_seenUsl[_uslKey]){
        annotations.push({x:_pusl,y:1,xref:'x',yref:'paper',
          text:'<b>'+ph+'</b><br>USL:'+_pusl,
          showarrow:false,xanchor:'left',yanchor:'top',
          font:{size:9,color:_pClr},bgcolor:'rgba(7,17,26,0.75)',borderpad:2});
        _seenUsl[_uslKey]=true;
      }
    });
  } else {
    if(phUsl!=null) _vline(phUsl,'#ff4444',2,'dot','USL','USL \u2014 Upper Spec Limit');
    if(phLsl!=null) _vline(phLsl,'#4488ff',2,'dot','LSL','LSL \u2014 Lower Spec Limit');
  }
  // Pre-compute sigma counts here so they're available for both line hover and stat pills
  var _nTotal=fn||phData.n_total;
  var _n3=fn>0?fn3:phData.n3, _n6=fn>0?fn6:phData.n6, _n12=fn>0?fn12:phData.n12;
  if(filtSigma>0 && _pdmShowSigma){
    var _sigCounts={3:_n3,6:_n6,12:_n12};
    [[3,'#ffd166'],[6,'#ffaa00'],[12,'#ff6600']].forEach(function(sg){
      [1,-1].forEach(function(sign){
        var xv=round2(filtMean+sign*sg[0]*filtSigma);
        var side=sign>0?'+':'\u2212';
        var cnt=_sigCounts[sg[0]];
        var pct=_nTotal>0?((cnt/_nTotal)*100).toFixed(1)+'%':'?';
        _vline(xv,sg[1],1,'dash',side+sg[0]+'\u03C3',side+sg[0]+'\u03C3 threshold: '+xv+' mV<br>'+cnt.toLocaleString()+' dies beyond ('+pct+')');
      });
    });
    _vline(round2(filtMean),'rgba(72,202,228,0.6)',1,'dashdot','Mean','Mean: '+round2(filtMean)+' mV');
  }
  var _xaxisCfg={title:'Value (mV)',gridcolor:'#1e3050'};
  if(_overlayAll&&overlayTraces.length&&isFinite(_gMin)) _xaxisCfg.range=[_gMin,_gMax];
  else if(_groupXRange) _xaxisCfg.range=_groupXRange;
  // When pass filter active in single-phase mode, clip x-axis at USL
  if(_histPK&&phUsl!=null&&!(_overlayAll&&overlayTraces.length)){
    var _clipMin=_xaxisCfg.range?_xaxisCfg.range[0]:(phLsl!=null?phLsl-(phUsl-phLsl)*0.15:null);
    _xaxisCfg.range=[_clipMin, phUsl+(phUsl-(phLsl!=null?phLsl:0))*0.05];
  }
  var _histTraces;
  if(_overlayAll && overlayTraces.length){
    _histTraces = overlayTraces;
  } else if(_hasRaw && _rawSrc[phase]){
    // Raw values available — Plotly computes its own bins; bingroup keeps both traces aligned
    var _rVals=[], _rFail=[];
    var _filt4=_pdmFocusFilter||_getPdmFilter();
    (_rawSrc[phase]||[]).forEach(function(r){
      if(!_filt4.lots.has(LOTS[r[0]])||!_filt4.wfrs.has(r[1])) return;
      if(_histPK&&!_histPK.has(r[0]+'::'+r[1]+'::'+r[2]+'::'+r[3])) return;
      _rVals.push(r[4]);
      // Only mark as fail when no pass filter is active; passing dies are by definition within spec
      if(!_histPK&&((phUsl!=null&&r[4]>phUsl)||(phLsl!=null&&r[4]<phLsl))) _rFail.push(r[4]);
    });
    _histTraces=[
      {x:_rVals,type:'histogram',name:_histPP?phase+'\u00a0\u2713\u202f'+_histPP:phase,bingroup:'h1',
       marker:{color:phColor+'88',line:{width:0}},
       hovertemplate:'%{x:.3f}mV: %{y} dies<extra>'+phase+((_histPP)?' (pass '+_histPP+')':'')+'</extra>'},
      {x:_rFail,type:'histogram',name:'Fail\u00a0(>USL/<LSL)',bingroup:'h1',
       marker:{color:'rgba(255,107,107,0.75)',line:{width:0}},
       hovertemplate:'%{x:.3f}mV: %{y} fails<extra>Fail</extra>'}
    ];
    if(!_overlayAll&&!_groupXRange) delete _xaxisCfg.range;
  } else {
    // Fallback: pre-computed bins — bars at midpoints
    _histTraces=[
      {x:midpoints,y:cnts,type:'bar',name:phase,
       marker:{color:phColor+'88',line:{width:0}},
       hovertemplate:'%{x}mV: %{y} dies<extra>'+phase+'</extra>'},
      {x:midpoints,y:cntsF,type:'bar',name:'Fail\u00a0(>USL/<LSL)',
       marker:{color:'rgba(255,107,107,0.75)',line:{width:0}},
       hovertemplate:'%{x}mV: %{y} fails<extra>Fail</extra>'}
    ];
  }
  Plotly.react('pdm-chart',_histTraces.concat(lineTraces),Object.assign(L({
    title:pin+' \u2014 '+phase+(isFiltered?' (filtered)':''),
    xaxis:_xaxisCfg, yaxis:{title:'Die count',gridcolor:'#1e3050',type:_pdmLogY?'log':'linear'},
    barmode:'overlay', shapes:shapes, annotations:annotations, legend:{orientation:'h',y:1.18,x:0,xanchor:'left'}, margin:{t:80,l:55,r:80,b:55}
  }),{}),{responsive:true,displayModeBar:false});
  // Stats pills
  var failPct=fn>0?((fnf/fn)*100).toFixed(2)+'%':(phData.n_fail/phData.n_total*100).toFixed(2)+'%';
  var s='';
  s+=_pdStatPill('N total',_nTotal.toLocaleString(),'#c0deff');
  s+=_pdStatPill('N fail',(fn>0?fnf:phData.n_fail).toLocaleString(),'#ff8080');
  s+=_pdStatPill('%fail',failPct,'#ffd166');
  s+=_pdStatPill('Mean',round2(filtMean)+'mV','#48cae4');
  s+=_pdStatPill('\u03C3 (sigma)',round2(filtSigma)+'mV','#a78bfa');
  if(filtCp!=null) s+=_pdStatPill('Cp',round2(filtCp),filtCp>=1.33?'#69f0ae':filtCp>=1.0?'#ffd166':'#ff6b6b');
  if(filtCpk!=null) s+=_pdStatPill('Cpk',round2(filtCpk),filtCpk>=1.33?'#69f0ae':filtCpk>=1.0?'#ffd166':'#ff6b6b');
  s+=_pdStatPill('USL',phUsl!=null?phUsl+'mV':'\u2014','#ff9999');
  s+=_pdStatPill('LSL',phLsl!=null?phLsl+'mV':'\u2014','#88aaff');
  s+=_pdStatPill('Median',round2(phData.median)+'mV','#8ab4d4');
  var _p3=_nTotal>0?((_n3/_nTotal)*100).toFixed(1)+'%':'?';
  var _p6=_nTotal>0?((_n6/_nTotal)*100).toFixed(1)+'%':'?';
  var _p12=_nTotal>0?((_n12/_nTotal)*100).toFixed(1)+'%':'?';
  var _t3=round2(filtMean+3*filtSigma), _t6=round2(filtMean+6*filtSigma), _t12=round2(filtMean+12*filtSigma);
  s+=_pdStatPill('>3\u03C3',_n3.toLocaleString()+'<div style="font-size:0.65rem;color:#997a00;margin-top:1px">&gt;'+_t3+'mV &bull; '+_p3+'</div>','#ffd166');
  s+=_pdStatPill('>6\u03C3',_n6.toLocaleString()+'<div style="font-size:0.65rem;color:#886000;margin-top:1px">&gt;'+_t6+'mV &bull; '+_p6+'</div>','#ffaa00');
  s+=_pdStatPill('>12\u03C3',_n12.toLocaleString()+'<div style="font-size:0.65rem;color:#883300;margin-top:1px">&gt;'+_t12+'mV &bull; '+_p12+'</div>','#ff6600');
  // ── Following Phase Failure Preview (per-pin, incremental/cumulative) ──────
  var _nFailCur = fn>0 ? fnf : phData.n_fail;  // failures at current phase (filtered)
  var _FLOW_ORD_P=['Pre-Surge','Post-Surge','Stress','SDS-Final','SDT-Start','SDT-Final'];
  var _phIdxP=_FLOW_ORD_P.indexOf(phase);
  var _nextPanelHtml='';
  if(_phIdxP>=0 && _phIdxP<_FLOW_ORD_P.length-1){
    var _subPhsP=_FLOW_ORD_P.slice(_phIdxP+1);
    var _panRowsP=[];
    _subPhsP.forEach(function(sPh){
      var sPhData=(pd.phases||{})[sPh];
      if(!sPhData||sPhData.usl==null) return;
      var sUsl=sPhData.usl;
      var nExcP=0, nTotP=0;
      var binsP=phData.bins||[], cntsP=phData.counts||[];
      for(var i=0;i<cntsP.length;i++){
        if(!cntsP[i]) continue;
        var midP=(binsP[i]+(binsP[i+1]!=null?binsP[i+1]:binsP[i]))/2;
        nTotP+=cntsP[i];
        if(midP>sUsl) nExcP+=cntsP[i];
      }
      _panRowsP.push({phase:sPh, nCum:nExcP, nInc:Math.max(0,nExcP-_nFailCur), usl:round2(sUsl), total:nTotP});
    });
    if(_panRowsP.length){
      var _incId='pdm-inc-cb-'+pin.replace(/[^a-zA-Z0-9]/g,'_');
      function _buildRows(panRows,inc,phClr,nFailC){
        return panRows.map(function(e){
          var val=inc?e.nInc:e.nCum;
          var pct=e.total>0?Math.round(val/e.total*100):0;
          var bw=Math.max(0,Math.min(100,pct));
          var phc=phClr[e.phase]||'#8ab4d4';
          var badge=inc&&val===0&&e.nCum>0?'<span style="font-size:0.65rem;color:#4a6a4a;margin-left:3px">(all caught earlier)</span>':'';
          return '<div style="display:flex;align-items:center;gap:8px;padding:3px 0;border-bottom:1px solid #1a2538">'
            +'<span style="min-width:80px;font-size:0.72rem;font-weight:700;color:'+phc+'">'+e.phase+'</span>'
            +'<span style="min-width:80px;font-size:0.70rem;color:#667788;font-family:monospace">USL '+e.usl+' mV</span>'
            +'<span style="min-width:50px;font-size:0.72rem;font-weight:700;color:'+(val>0?'#ff9966':'#4a6a4a')+'">'+(inc&&val>0?'+':'')+val.toLocaleString()+badge+'</span>'
            +'<div style="flex:1;background:#1a2538;border-radius:3px;height:6px;overflow:hidden">'
              +'<div style="height:100%;width:'+bw+'%;background:'+phc+';border-radius:3px;opacity:0.8"></div>'
            +'</div>'
            +'<span style="min-width:38px;text-align:right;font-size:0.70rem;color:#667788">'+pct+'%</span>'
            +'</div>';
        }).join('');
      }
      var _phColP=_PHASE_CLR[phase]||'#48cae4';
      _nextPanelHtml='<div style="width:100%;margin-top:8px;padding:6px 11px;background:#090f1a;border-left:3px solid '+_phColP+';border-radius:0 5px 5px 0">'
        +'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">'
          +'<div style="flex:1;font-size:0.70rem;font-weight:700;color:'+_phColP+';text-transform:uppercase;letter-spacing:.04em">\u25b6 Following Phase Failure Preview \u2014 '+pin+'</div>'
          +'<label style="display:inline-flex;align-items:center;gap:4px;cursor:pointer;font-size:0.69rem;color:#8ab4d4;white-space:nowrap">'
            +'<input type="checkbox" id="'+_incId+'" checked style="accent-color:'+_phColP+';cursor:pointer"> Incremental'
          +'</label>'
        +'</div>'
        +'<div style="font-size:0.69rem;color:#445566;margin-bottom:4px" id="'+_incId+'_desc">Additional failures beyond the '+_nFailCur+' already failing at '+phase+'</div>'
        +'<div id="'+_incId+'_rows">'+_buildRows(_panRowsP,true,_PHASE_CLR,_nFailCur)+'</div>'
        +'</div>';
      // Wire checkbox toggle after render
      setTimeout(function(){
        var cb=document.getElementById(_incId);
        if(!cb) return;
        var rowsEl=document.getElementById(_incId+'_rows');
        var descEl=document.getElementById(_incId+'_desc');
        var _pR=_panRowsP.slice(), _nF=_nFailCur, _pC=_PHASE_CLR, _ph=phase;
        cb.addEventListener('change',function(){
          var inc=cb.checked;
          if(rowsEl) rowsEl.innerHTML=_buildRows(_pR,inc,_pC,_nF);
          if(descEl) descEl.textContent=inc
            ?'Additional failures beyond the '+_nF+' already failing at '+_ph
            :'Total measurements exceeding each subsequent phase USL';
        });
      },30);
    }
  }
  document.getElementById('pdm-stats').innerHTML=s
    +_nextPanelHtml
    +'<div style="width:100%;margin-top:8px;padding:7px 11px;background:#0a1018;border-left:3px solid #2a4060;border-radius:0 5px 5px 0;font-size:0.72rem;color:#556677;line-height:1.6">'
    +'<span style="color:#8ab4d4;font-weight:700">&#9432; Note: </span>'
    +'<b>N total</b> and <b>N fail</b> count <em>all</em> dies with a measured value for this pin/phase — including non-BIN8 dies killed earlier in the test flow by unrelated tests (e.g. functional scan, speed). '
    +'Those dies have genuine continuity failures but do not appear in the BIN8 Bin Breakdown since a different test claimed the bin first. '
    +'<b>N fail may therefore exceed the BIN8 Bin Breakdown pin count for this pin.</b>'
    +'</div>';
  // Detail table
  var detDiv=document.getElementById('pdm-detail-tbl');
  if(isDetail){
    // Focus Live Mode: use RAW_PIN_DATA rows; fall back to DETAIL_PINS for top-5 precomputed
    var detPhases = _useLive ? (RAW_PIN_DATA[pin]||{}) : (DETAIL_PINS[pin]||{});
    var rawRows=detPhases[phase]||[];
    var filtered=rawRows.filter(function(r){return filt.lots.has(LOTS[r[0]])&&filt.wfrs.has(r[1]);});
    filtered.sort(function(a,b){return Math.abs(b[4])>Math.abs(a[4])?1:-1;});
    document.getElementById('pdm-det-cnt').textContent='('+filtered.length+' dies, top 200 by value)';
    var tbRows=filtered.slice(0,200).map(function(r){
      var lot=LOTS[r[0]]||('L'+r[0]); var v=r[4];
      var vs=phUsl!=null?(v>phUsl?'<span style="color:#ff6b6b">+'+(round2(v-phUsl))+'</span>':round2(v-phUsl)):'';
      var vc=v>(phUsl||Infinity)?'#ff6b6b':v<(phLsl||-Infinity)?'#88aaff':'#c0ccd8';
      return '<tr><td style="color:#8ab4d4;font-size:0.77rem">'+lot+'</td><td>W'+r[1]+'</td><td>'+r[2]+'</td><td>'+r[3]+'</td>'+
        '<td style="color:'+vc+';font-weight:700">'+v+'</td><td>'+vs+'</td></tr>';
    }).join('');
    document.getElementById('pdm-det-tbody').innerHTML=tbRows||'<tr><td colspan="6" style="color:#445566;padding:10px">No data for filter</td></tr>';
    detDiv.style.display='block';
  } else { detDiv.style.display='none'; }
}
function round2(v){ return Math.round(v*100)/100; }

// ── In-modal Focus Filter panel (lot/wafer checkboxes) ─────────────────────────
var _pdmFilterSearch = '';  // search string for lot/wafer filter
// Build lot/wafer tree for pin inspect — identical structure to _buildLotWfrTree
function _buildPdmLotTree(container, lotWfrs, pin){
  container.innerHTML='';
  var _matByWfr={};
  WFR_LIST.forEach(function(w){_matByWfr[w.lot+'|'+w.wfr]=w.material||'';});
  function _abbrevMat(m){
    if(!m) return '';
    var i1=m.indexOf(' - '); if(i1<0) return m;
    var i2=m.indexOf(' - ',i1+3); return i2>=0?m.substring(0,i2)+'..':m;
  }
  var selLots=_pdmFocusFilter.lots, selWfrs=_pdmFocusFilter.wfrs;
  function _triggerUpdate(){
    var summEl=document.getElementById('pdm-filt-summary');
    if(summEl){var _allL=document.querySelectorAll('#pdm-focus-filter input[data-type=lot]').length;summEl.textContent=(selLots.size>=_allL||!_allL)?'All':selLots.size+'/'+_allL;}
    if(_pdmPin){_renderPinDist(_pdmPin,PIN_DISTRIB[_pdmPin]||{},true,_pdmPhase);_xpRender();}
  }
  Object.keys(lotWfrs).sort().forEach(function(lot){
    var wfrs=Array.from(lotWfrs[lot]).sort(function(a,b){return a-b;});
    var lotId='pdm_lot_'+lot.replace(/[^a-z0-9]/gi,'_');
    var wfrDivId=lotId+'_wfrs';
    var _lotMats=[];
    wfrs.forEach(function(w){var m=_matByWfr[lot+'|'+w]||'';if(m&&_lotMats.indexOf(m)<0)_lotMats.push(m);});
    var _displayMats=_lotMats.length>1?_lotMats.map(_abbrevMat):_lotMats;
    var _lotMatTag=_lotMats.length?'<span style="font-size:0.67rem;color:#4ecdc4;margin-left:4px;font-weight:700" title="'+_lotMats.join(', ')+'">['+_displayMats.join('+')+']</span>':'';
    var nWChkInit=wfrs.filter(function(w){return selWfrs.has(w);}).length;
    // Lot row — <details> dropdown
    var lotDetails=document.createElement('details');
    lotDetails.style.cssText='margin-bottom:1px';
    var lotSum=document.createElement('summary');
    lotSum.style.cssText='display:flex;align-items:center;gap:4px;padding:2px 4px;border-radius:3px;cursor:pointer;list-style:none;outline:none';
    var lotCbEl=document.createElement('input');lotCbEl.type='checkbox';lotCbEl.dataset.type='lot';lotCbEl.value=lot;
    lotCbEl.checked=nWChkInit>0; lotCbEl.indeterminate=nWChkInit>0&&nWChkInit<wfrs.length;
    lotCbEl.style.cssText='accent-color:#4a9fd4;cursor:pointer;flex-shrink:0';
    lotCbEl.addEventListener('click',function(e){e.stopPropagation();});
    var lotLbl=document.createElement('span');
    lotLbl.style.cssText='font-size:0.76rem;color:#8ab4d4;flex:1;display:flex;align-items:center;gap:4px;pointer-events:none';
    lotLbl.innerHTML=lot+' '+_lotMatTag+' <span style="color:#445566;font-size:0.7rem">'+nWChkInit+'/'+wfrs.length+'W</span>';
    lotSum.appendChild(lotCbEl); lotSum.appendChild(lotLbl);
    lotDetails.appendChild(lotSum);
    container.appendChild(lotDetails);
    var lotCb=lotCbEl;
    var wfrDiv=document.createElement('div');
    wfrDiv.style.cssText='padding-left:20px;flex-direction:column;gap:1px';
    var matGroups={}, noMatWfrs=[];
    wfrs.forEach(function(w){
      var m=_matByWfr[lot+'|'+w]||'';
      if(m){if(!matGroups[m])matGroups[m]=[];matGroups[m].push(w);}
      else noMatWfrs.push(w);
    });
    var matKeys=Object.keys(matGroups).sort();
    function _updateLotCb(){
      var allW=wfrDiv.querySelectorAll('input[data-type=wfr]');
      var nChk=wfrDiv.querySelectorAll('input[data-type=wfr]:checked').length;
      lotCb.checked=nChk>0; lotCb.indeterminate=(nChk>0&&nChk<allW.length);
      if(nChk>0) selLots.add(lot); else selLots.delete(lot);
      _triggerUpdate();
    }
    lotCb.addEventListener('change',function(){
      wfrDiv.querySelectorAll('input[data-type=wfr],input[data-type=matgrp]').forEach(function(cb){cb.checked=lotCb.checked;cb.indeterminate=false;});
      wfrs.forEach(function(w){if(lotCb.checked)selWfrs.add(w);else selWfrs.delete(w);});
      if(lotCb.checked) selLots.add(lot); else selLots.delete(lot);
      _triggerUpdate();
    });
    // Material group sections — <details> dropdown
    matKeys.forEach(function(mat){
      var grpWfrs=matGroups[mat];
      var matDetails=document.createElement('details');
      matDetails.style.cssText='margin-top:2px';
      var matSum=document.createElement('summary');
      matSum.style.cssText='display:flex;align-items:center;gap:4px;padding:1px 2px;border-radius:3px;cursor:pointer;list-style:none;outline:none';
      var matCbEl=document.createElement('input');matCbEl.type='checkbox';matCbEl.dataset.type='matgrp';matCbEl.dataset.lot=lot;
      var nGI=grpWfrs.filter(function(w){return selWfrs.has(w);}).length;
      matCbEl.checked=nGI>0; matCbEl.indeterminate=nGI>0&&nGI<grpWfrs.length;
      matCbEl.style.cssText='accent-color:#4ecdc4;cursor:pointer;flex-shrink:0';
      matCbEl.addEventListener('click',function(e){e.stopPropagation();});
      var matLbl=document.createElement('span');
      matLbl.style.cssText='font-size:0.71rem;color:#4ecdc4;flex:1;display:flex;align-items:center;gap:4px;pointer-events:none';
      matLbl.title=mat;
      matLbl.innerHTML='<span style="font-weight:600">'+mat+'</span> <span style="color:#445566;font-size:0.67rem">('+grpWfrs.length+'W)</span>';
      matSum.appendChild(matCbEl); matSum.appendChild(matLbl);
      matDetails.appendChild(matSum);
      wfrDiv.appendChild(matDetails);
      var matCb=matCbEl;
      var matWfrDiv=document.createElement('div');
      matWfrDiv.style.cssText='padding-left:16px;flex-direction:column;gap:1px';
      grpWfrs.forEach(function(w){
        var wl=document.createElement('label');
        wl.style.cssText='display:flex;align-items:center;gap:4px;cursor:pointer;font-size:0.72rem;color:#c0ccd8;padding:1px 0';
        var wcb=document.createElement('input');wcb.type='checkbox';wcb.dataset.type='wfr';wcb.dataset.lot=lot;wcb.value=w;
        wcb.checked=selWfrs.has(w); wcb.style.cssText='accent-color:#4a9fd4;cursor:pointer';
        wcb.addEventListener('change',function(){
          if(wcb.checked)selWfrs.add(w);else selWfrs.delete(w);
          var allG=matWfrDiv.querySelectorAll('input[data-type=wfr]');
          var nG=matWfrDiv.querySelectorAll('input[data-type=wfr]:checked').length;
          matCb.checked=nG>0; matCb.indeterminate=nG>0&&nG<allG.length;
          _updateLotCb();
        });
        wl.appendChild(wcb);
        wl.appendChild(Object.assign(document.createElement('span'),{textContent:' W'+String(w).padStart(2,'0')+' \u2014 '+mat}));
        matWfrDiv.appendChild(wl);
      });
      matCb.addEventListener('change',function(){
        matWfrDiv.querySelectorAll('input[data-type=wfr]').forEach(function(cb){
          cb.checked=matCb.checked; cb.indeterminate=false;
          if(matCb.checked)selWfrs.add(+cb.value);else selWfrs.delete(+cb.value);
        });
        _updateLotCb();
      });
      matDetails.appendChild(matWfrDiv);
    });
    noMatWfrs.forEach(function(w){
      var wl=document.createElement('label');
      wl.style.cssText='display:flex;align-items:center;gap:4px;cursor:pointer;font-size:0.74rem;color:#c0ccd8;padding:1px 0';
      var wcb=document.createElement('input');wcb.type='checkbox';wcb.dataset.type='wfr';wcb.dataset.lot=lot;wcb.value=w;
      wcb.checked=selWfrs.has(w); wcb.style.cssText='accent-color:#4a9fd4;cursor:pointer';
      wcb.addEventListener('change',function(){
        if(wcb.checked)selWfrs.add(w);else selWfrs.delete(w);
        _updateLotCb();
      });
      wl.appendChild(wcb);
      wl.appendChild(Object.assign(document.createElement('span'),{textContent:' W'+String(w).padStart(2,'0')}));
      wfrDiv.appendChild(wl);
    });
    lotDetails.appendChild(wfrDiv);
  });
}
function _renderFocusFilterPanel(pin){
  var el=document.getElementById('pdm-focus-filter');
  if(!el) return;
  if(!_focusModeActive || !RAW_PIN_DATA || !RAW_PIN_DATA[pin]){ el.style.display='none'; return; }
  if(!_pdmFocusFilter) _initPdmFocusFilter();
  // Build lot -> wafer set from raw data
  var lotWfrs={};
  Object.values(RAW_PIN_DATA[pin]).forEach(function(rows){
    rows.forEach(function(r){
      var lot=LOTS[r[0]]; if(!lot) return;
      if(!lotWfrs[lot]) lotWfrs[lot]=new Set();
      lotWfrs[lot].add(r[1]);
    });
  });
  var selLots=_pdmFocusFilter.lots, selWfrs=_pdmFocusFilter.wfrs;
  var lotKeys=Object.keys(lotWfrs).sort();
  // Apply search filter
  var _srch=(_pdmFilterSearch||'').toLowerCase().trim();
  if(_srch){
    lotKeys=lotKeys.filter(function(lot){
      if(lot.toLowerCase().indexOf(_srch)>=0) return true;
      return Array.from(lotWfrs[lot]).some(function(w){return ('w'+String(w).padStart(2,'0')).indexOf(_srch)>=0||(String(w)).indexOf(_srch)>=0;});
    });
  }
  var filteredLotWfrs={};
  lotKeys.forEach(function(lot){filteredLotWfrs[lot]=lotWfrs[lot];});
  // Count selected
  var nSelLots=0;
  lotKeys.forEach(function(lot){ if(selLots.has(lot)) nSelLots++; });
  var lbl=(nSelLots>=lotKeys.length||!lotKeys.length)?'All':nSelLots+'/'+lotKeys.length;
  // Render as compact dropdown bar (same pattern as wafer map Lot/Wafer filter)
  el.style.cssText='padding:4px 16px;border-bottom:1px solid #0d1828;background:#080e18;flex-shrink:0;display:block';
  el.innerHTML='<div class="dd-wrap" style="display:inline-flex;align-items:center">'
    +'<button id="pdm-filt-btn" class="dd-btn" onclick="_ddToggle(\'pdm-filt-panel\',event)">&#127757; Lot/Wafer&nbsp;<span id="pdm-filt-summary" class="dd-lbl">'+lbl+'</span>&nbsp;<span class="dd-arr">&#9660;</span></button>'
    +'<div class="dd-panel" id="pdm-filt-panel" onclick="event.stopPropagation()" style="min-width:280px;max-width:420px">'
    +'<input class="dd-search" id="pdm-filt-search" type="text" placeholder="&#128269; Search lot / wafer&hellip;" value="'+(_pdmFilterSearch||'').replace(/"/g,'&quot;')+'" oninput="_pdmFilterSearch=this.value;_renderFocusFilterPanel(\''+pin+'\');">'
    +'<div class="dd-actions">'
    +'<button onclick="_pdmFocusAllLots(true)">All</button>'
    +'<button onclick="_pdmFocusAllLots(false)">None</button>'
    +'</div>'
    +'<div id="pdm-filt-tree" class="dd-tree-inner" style="max-height:260px;resize:vertical"></div>'
    +'</div>'
    +'</div>';
  _buildPdmLotTree(document.getElementById('pdm-filt-tree'), filteredLotWfrs, pin);
}
function _pdmFocusAllLots(on){
  if(!_pdmFocusFilter) return;
  document.querySelectorAll('#pdm-focus-filter input[data-type=lot]').forEach(function(cb){
    cb.checked=on; cb.indeterminate=false;
    if(on) _pdmFocusFilter.lots.add(cb.value);
    else   _pdmFocusFilter.lots.delete(cb.value);
  });
  document.querySelectorAll('#pdm-focus-filter input[data-type=wfr]').forEach(function(cb){
    cb.checked=on;
    if(on) _pdmFocusFilter.wfrs.add(+cb.value);
    else   _pdmFocusFilter.wfrs.delete(+cb.value);
  });
  document.querySelectorAll('#pdm-focus-filter input[data-type=matgrp]').forEach(function(cb){
    cb.checked=on; cb.indeterminate=false;
  });
  var summEl=document.getElementById('pdm-filt-summary');
  if(summEl){var _allL=document.querySelectorAll('#pdm-focus-filter input[data-type=lot]').length;summEl.textContent=(_pdmFocusFilter.lots.size>=_allL||!_allL)?'All':_pdmFocusFilter.lots.size+'/'+_allL;}
  if(_pdmPin){_renderPinDist(_pdmPin,PIN_DISTRIB[_pdmPin]||{},true,_pdmPhase);_xpRender();}
}
function toggleLiveMode(){
  _focusModeActive = document.getElementById('live-mode-cb').checked;
  if(_focusModeActive && !_pdmFocusFilter) _initPdmFocusFilter();
  if(_pdmOpen && _pdmPin){
    var pd = PIN_DISTRIB[_pdmPin]||{};
    var isDetail = _pdmPin in DETAIL_PINS || _focusHasPin(_pdmPin);
    var _ml = _focusHasPin(_pdmPin) ? '\u26a1 Live \u2014 all pins' : (_pdmPin in DETAIL_PINS ? '\u2605 Full data' : 'Stats mode');
    document.getElementById('pdm-mode').textContent=_ml;
    document.getElementById('pdm-mode').style.background = _focusHasPin(_pdmPin) ? '#0d2d0d' : '#1e3050';
    document.getElementById('pdm-mode').style.color      = _focusHasPin(_pdmPin) ? '#69f0ae' : '#8ab4d4';
    _renderFocusFilterPanel(_pdmPin);
    _renderPinDist(_pdmPin, pd, isDetail, _pdmPhase);
    // Refresh live-tab opacity and re-render cross-phase
    document.querySelectorAll('.pdm-xp-live').forEach(function(b){
      b.style.opacity=_focusModeActive?'1':'0.4';
      b.title=_focusModeActive?'':'Requires Live Mode (re-generate with fewer wafers)';
    });
    // If currently on a live-only tab, switch to stats
    if(!_focusModeActive && ['grid','wmap','cdf'].indexOf(_xpActiveTab)>=0){
      _xpShow('stats', document.querySelectorAll('.pdm-xp-tab')[0]);
    } else { _xpRender(); }
  }
}
// ── Parametric Analysis (tabs) ────────────────────────────────────────────────
var _xpPin = null;
var _xpActiveTab = 'violin';
var _vlStatsOnly = false;


function _xpHasPhases(pin){
  var pd=PIN_DISTRIB[pin]; return pd && pd.phase_list && pd.phase_list.length > 1;
}

function _xpShow(tab, btn){
  _xpActiveTab = tab;
  document.querySelectorAll('.pdm-xp-tab').forEach(function(b){ b.classList.remove('active'); });
  if(btn) btn.classList.add('active');
  ['stats','overlay','grid','wmap','cdf','violin','xyplot','sdhist'].forEach(function(t){
    var el = document.getElementById('pdm-xp-'+t);
    if(el) el.style.display = t===tab ? 'block' : 'none';
  });
  _xpRender();
  // XY plot: re-render when switching to tab (also handles non-focus mode where _xpRender skips it)
  if(tab==='xyplot') _xpRenderXyPlot();
}

function _xpBuildIndex(pin){
  // Build {key -> {phase -> val_mV}} where key = lot::wfr::x::y
  var idx={};
  if(!RAW_PIN_DATA||!RAW_PIN_DATA[pin]) return idx;
  var filt = _pdmFocusFilter || _getPdmFilter();
  Object.entries(RAW_PIN_DATA[pin]).forEach(function(e){
    var ph=e[0];
    e[1].forEach(function(r){
      if(!filt.lots.has(LOTS[r[0]])||!filt.wfrs.has(r[1])) return;
      var k=r[0]+'::'+r[1]+'::'+r[2]+'::'+r[3];
      if(!idx[k]) idx[k]={li:r[0],wfr:r[1],x:r[2],y:r[3]};
      idx[k][ph]=r[4];
    });
  });
  return idx;
}

function _xpBuildIndexWxy(pin){
  // Build {wfr::x::y -> {phase -> val_mV}} — lot-agnostic key for cross-lot XY matching.
  // When different lots are tested at different phases, the same physical die (wfr,x,y)
  // would never match in lot-keyed index.  Last value wins if duplicates exist within a phase.
  if(!RAW_PIN_DATA||!RAW_PIN_DATA[pin]) return {};
  var filt = _pdmFocusFilter || _getPdmFilter();
  var _filterLots = filt && filt.lots && filt.lots.size > 0;
  var _filterWfrs = filt && filt.wfrs && filt.wfrs.size > 0;
  var _fk=(_filterLots?Array.from(filt.lots).sort().join(','):'*')+'|'+(_filterWfrs?Array.from(filt.wfrs).sort().join(','):'*');
  if(_xpIdxCache.pin===pin && _xpIdxCache.filtKey===_fk && _xpIdxCache.idx) return _xpIdxCache.idx;
  var idx={};
  Object.entries(RAW_PIN_DATA[pin]).forEach(function(e){
    var ph=e[0];
    e[1].forEach(function(r){
      if(_filterLots && !filt.lots.has(LOTS[r[0]])) return;  // respect lot filter
      if(_filterWfrs && !filt.wfrs.has(r[1])) return;
      var k=r[1]+'::'+r[2]+'::'+r[3];
      if(!idx[k]) idx[k]={li:r[0],wfr:r[1],x:r[2],y:r[3]};
      idx[k][ph]=r[4];  // last lot's value wins per phase
    });
  });
  _xpIdxCache.pin=pin; _xpIdxCache.filtKey=_fk; _xpIdxCache.idx=idx;
  return idx;
}

function _xpToggle(){
  var body=document.getElementById('pdm-xp-body');
  var chev=document.getElementById('pdm-xp-chevron');
  if(!body) return;
  var open=body.style.display==='none';
  body.style.display=open?'block':'none';
  if(chev){ chev.style.transform=open?'rotate(90deg)':''; chev.textContent='\u25BA'; }
  if(open) _xpRender();
}
function _xpPhases(pin){
  if(!RAW_PIN_DATA||!RAW_PIN_DATA[pin]) return [];
  var _PHASE_ORDER=['Pre-Surge','Post-Surge','Post-Surge-HT','Stress','SDS-Final','SDT-Start','SDT-Final','ISVM-EDC','OTHER'];
  var ph=Object.keys(RAW_PIN_DATA[pin]);
  ph.sort(function(a,b){ return _PHASE_ORDER.indexOf(a)-_PHASE_ORDER.indexOf(b); });
  return ph;
}

function _xpRender(){
  if(!_xpPin) return;
  _xpRenderStats();  // always update — reacts to lot/wafer/pass filter
  if(_xpActiveTab==='overlay') _xpRenderOverlay();
  if(_xpActiveTab==='violin')  _xpRenderViolin();  // works in both live and non-live mode
  if(!_focusModeActive) return;  // per-die tabs below require live mode
  if(_xpActiveTab==='grid')    _xpRenderGrid();
  if(_xpActiveTab==='wmap')    _xpRenderWmap();
  if(_xpActiveTab==='cdf')     _xpRenderCdf();
  if(_xpActiveTab==='violin')  _xpRenderViolin();
  if(_xpActiveTab==='xyplot')  _xpRenderXyPlot();
  if(_xpActiveTab==='sdhist')  _xpRenderSdHist();
}

// ── Tab: Stats (always visible above Cross-Phase panel) ────────────────────
function _xpRenderStats(){
  var pin=_xpPin; if(!pin) return;
  var pd=PIN_DISTRIB[pin]||{};
  var phases=pd.phase_list||_xpPhases(pin)||[];
  var tblEl=document.getElementById('pdm-xp-stats-tbl');
  var srcNote=document.getElementById('xp-st-src-note');
  var cardEl=document.getElementById('pdm-xp-stats-card');
  if(cardEl) cardEl.style.display='block';
  if(!phases.length){ if(tblEl) tblEl.innerHTML='<div style="color:#445566;font-size:0.73rem;padding:8px">No multi-phase data.</div>'; return; }
  // Pass filter
  var passPhElSt=document.getElementById('xp-st-passph');
  var passPhSt=passPhElSt?passPhElSt.value:'';
  if(passPhElSt&&passPhElSt.options.length<=1){
    phases.forEach(function(ph){ passPhElSt.innerHTML+='<option value="'+ph+'">Pass '+ph+'</option>'; });
    passPhElSt.selectedIndex=0;
    passPhSt=passPhElSt?passPhElSt.value:'';
  }
  var filt=_pdmFocusFilter||_getPdmFilter();
  var _filterWfrs=filt&&filt.wfrs&&filt.wfrs.size>0;
  var _filterLots=filt&&filt.lots&&filt.lots.size>0;
  var useLive=_focusModeActive&&RAW_PIN_DATA&&RAW_PIN_DATA[pin];
  // Build pass-set (only meaningful in live mode)
  var passKeysSt=null;
  if(passPhSt&&useLive){
    var _pUsl=(pd.phases&&pd.phases[passPhSt])?pd.phases[passPhSt].usl:pd.usl;
    if(_pUsl!=null){
      passKeysSt=new Set();
      var _pRows=(RAW_PIN_DATA[pin]||{})[passPhSt]||[];
      _pRows.forEach(function(r){
        if(_filterLots&&!filt.lots.has(LOTS[r[0]])) return;
        if(_filterWfrs&&!filt.wfrs.has(r[1])) return;
        if(r[4]<=_pUsl) passKeysSt.add(r[0]+'::'+r[1]+'::'+r[2]+'::'+r[3]);
      });
    }
  }
  var infoElSt=document.getElementById('xp-st-pass-info');
  if(infoElSt) infoElSt.textContent=passKeysSt?passKeysSt.size+' dies pass '+passPhSt:'';
  if(srcNote) srcNote.textContent=useLive?'\u26a1 Live \u2014 computed from filtered data':'\u26a0\ufe0f Pre-computed (enable Live Mode for filter-aware stats)';
  var cols=['Phase','N','Fail','Fail%','Mean (mV)','\u03c3 (mV)','Median','P1','P99','Cp','Cpk','\u03bc+3\u03c3','USL'];
  var hdr=cols.map(function(c){ return '<th style="padding:4px 9px;border:1px solid #1e3050;font-size:0.72rem;background:#131a2a;color:#8ab4d4;position:sticky;top:0;white-space:nowrap">'+c+'</th>'; }).join('');
  var rows='';
  phases.forEach(function(ph){
    var clr=_PHASE_CLR[ph]||'#8ab4d4';
    var usl=(pd.phases&&pd.phases[ph]&&pd.phases[ph].usl!=null)?pd.phases[ph].usl:pd.usl;
    var s;
    if(useLive){
      var rawRows=(RAW_PIN_DATA[pin]||{})[ph]||[];
      var vals=[];
      rawRows.forEach(function(r){
        if(_filterLots&&!filt.lots.has(LOTS[r[0]])) return;
        if(_filterWfrs&&!filt.wfrs.has(r[1])) return;
        if(passKeysSt&&!passKeysSt.has(r[0]+'::'+r[1]+'::'+r[2]+'::'+r[3])) return;
        vals.push(r[4]);
      });
      if(!vals.length){ s={n_total:0,n_fail:0,usl:usl}; }
      else {
        var _n=vals.length,_sm=0,_sm2=0;
        vals.forEach(function(v){_sm+=v;_sm2+=v*v;});
        var _mean=_sm/_n,_sigma=Math.sqrt(Math.max(0,_sm2/_n-_mean*_mean));
        var _sv=vals.slice().sort(function(a,b){return a-b;});
        var _p1=_sv[Math.max(0,Math.floor(_n*0.01))],_p99=_sv[Math.min(_n-1,Math.floor(_n*0.99))];
        var _med=_sv[Math.floor(_n/2)];
        var _nFail=usl!=null?vals.filter(function(v){return v>usl;}).length:0;
        var _cpk=usl!=null&&_sigma>0?(usl-_mean)/(3*_sigma):null;
        var _cp=usl!=null&&_sigma>0?(usl/_sigma/6)*2:null;
        s={n_total:_n,n_fail:_nFail,mean:_mean,sigma:_sigma,p1:_p1,p99:_p99,median:_med,cpk:_cpk,cp:_cp,mu3s:round2(_mean+3*_sigma),usl:usl};
      }
    } else {
      s=Object.assign({},(pd.phases||{})[ph]||{});
      s.usl=usl;
    }
    var nTotal=s.n_total||0;
    var failPct=nTotal?((s.n_fail||0)/nTotal*100).toFixed(2)+'%':'\u2014';
    var failClr=(s.n_fail>0)?'#ff6b6b':'#c0ccd8';
    var cpkV=s.cpk!=null?s.cpk.toFixed(3):'\u2014';
    var cpV =s.cp !=null?s.cp.toFixed(3):'\u2014';
    var cpkClr=s.cpk!=null&&s.cpk<1?'#ff6b6b':s.cpk!=null&&s.cpk<1.33?'#ffd166':'#69f0ae';
    var mu3s=s.mu3s!=null?s.mu3s:(s.mean!=null&&s.sigma!=null?round2(s.mean+3*s.sigma):'\u2014');
    rows+='<tr>';
    rows+='<td style="padding:3px 9px;border:1px solid #0d1828;font-size:0.72rem;font-weight:700;color:'+clr+'">'+ph+'</td>';
    rows+='<td style="padding:3px 9px;border:1px solid #0d1828;font-size:0.72rem;color:#c0ccd8">'+(nTotal||'\u2014')+'</td>';
    rows+='<td style="padding:3px 9px;border:1px solid #0d1828;font-size:0.72rem;color:'+failClr+'">'+((s.n_fail)||0)+'</td>';
    rows+='<td style="padding:3px 9px;border:1px solid #0d1828;font-size:0.72rem;color:'+failClr+'">'+failPct+'</td>';
    rows+='<td style="padding:3px 9px;border:1px solid #0d1828;font-size:0.72rem;color:#c0ccd8">'+(s.mean!=null?s.mean.toFixed(2):'\u2014')+'</td>';
    rows+='<td style="padding:3px 9px;border:1px solid #0d1828;font-size:0.72rem;color:#c0ccd8">'+(s.sigma!=null?s.sigma.toFixed(2):'\u2014')+'</td>';
    rows+='<td style="padding:3px 9px;border:1px solid #0d1828;font-size:0.72rem;color:#8ab4d4">'+(s.median!=null?s.median.toFixed(2):'\u2014')+'</td>';
    rows+='<td style="padding:3px 9px;border:1px solid #0d1828;font-size:0.72rem;color:#445566">'+(s.p1!=null?s.p1.toFixed(1):'\u2014')+'</td>';
    rows+='<td style="padding:3px 9px;border:1px solid #0d1828;font-size:0.72rem;color:#445566">'+(s.p99!=null?s.p99.toFixed(1):'\u2014')+'</td>';
    rows+='<td style="padding:3px 9px;border:1px solid #0d1828;font-size:0.72rem;color:#c0ccd8">'+cpV+'</td>';
    rows+='<td style="padding:3px 9px;border:1px solid #0d1828;font-size:0.72rem;color:'+cpkClr+';font-weight:700">'+cpkV+'</td>';
    rows+='<td style="padding:3px 9px;border:1px solid #0d1828;font-size:0.72rem;color:#ffd166">'+mu3s+'</td>';
    rows+='<td style="padding:3px 9px;border:1px solid #0d1828;font-size:0.72rem;color:#ff6b6b">'+(usl!=null?usl.toFixed(3):'\u2014')+'</td>';
    rows+='</tr>';
  });
  tblEl.innerHTML='<table style="border-collapse:collapse;width:100%;font-size:0.72rem"><thead><tr>'+hdr+'</tr></thead><tbody>'+rows+'</tbody></table>'
    +(useLive?'':'<div style="font-size:0.69rem;color:#556677;padding:4px 0">&#9889; Enable Live Mode for filter-aware stats</div>');
}

// ── Tab: Overlay Histogram (always available) ─────────────────────────────────
function _xpRenderOverlay(){
  var pin=_xpPin; if(!pin) return;
  var pd=PIN_DISTRIB[pin]||{};
  var phases=pd.phase_list||[];
  var normCb=document.getElementById('xp-ov-norm');
  var uslCb=document.getElementById('xp-ov-usl');
  var logyCb=document.getElementById('xp-ov-logy');
  var doNorm=normCb&&normCb.checked;
  var showUsl=uslCb&&uslCb.checked;
  var doLogY=logyCb?logyCb.checked:false;
  var passPhElOv=document.getElementById('xp-ov-passph');
  // Populate pass-phase selector on first call
  if(passPhElOv&&passPhElOv.options.length<=1){
    phases.forEach(function(ph){ passPhElOv.innerHTML+='<option value="'+ph+'">Pass '+ph+'</option>'; });
    var _ovDefIdx=phases.indexOf('Post-Surge');
    if(_ovDefIdx>=0&&passPhElOv) passPhElOv.selectedIndex=_ovDefIdx+1;
  }
  // Read value AFTER populating so the default is honoured on first render
  var passPhOv=passPhElOv?passPhElOv.value:'';
  // Build pass-set for overlay
  var passKeysOv=null;
  var filt=_pdmFocusFilter||_getPdmFilter();
  if(passPhOv){
    var passUslOv=(pd.phases&&pd.phases[passPhOv])?pd.phases[passPhOv].usl:pd.usl;
    if(passUslOv!=null){
      passKeysOv=new Set();
      var pRowsOv=(RAW_PIN_DATA[pin]||{})[passPhOv]||[];
      pRowsOv.forEach(function(r){
        if(filt.lots.has(LOTS[r[0]])&&filt.wfrs.has(r[1])&&r[4]<=passUslOv)
          passKeysOv.add(r[0]+'::'+r[1]+'::'+r[2]+'::'+r[3]);
      });
    }
  }
  var infoElOv=document.getElementById('xp-ov-pass-info');
  if(infoElOv) infoElOv.textContent=passKeysOv?passKeysOv.size+' dies pass '+passPhOv:'';

  // SDT phases are hidden by default (legendonly)
  var _SDT_PHASES=['SDT-Start','SDT-Final'];
  // SDS phases = everything that is NOT SDT
  function _isSdtPhase(ph){ return _SDT_PHASES.indexOf(ph)>=0; }

  // Use SDS-Final as reference axis; fall back to first available phase
  var _PHASE_PREF=['SDS-Final','Stress','Post-Surge','Pre-Surge','SDT-Start','SDT-Final'];
  var refPh=null;
  for(var _pi=0;_pi<_PHASE_PREF.length;_pi++){
    if(phases.indexOf(_PHASE_PREF[_pi])>=0){ refPh=_PHASE_PREF[_pi]; break; }
  }
  if(!refPh) refPh=phases[0];
  var refS=(pd.phases||{})[refPh]||{};
  var refBins=refS.bins||[];
  if(!refBins.length) return;
  var nBins=refBins.length-1;
  var refMid=refBins.slice(0,-1).map(function(b,j){ return (b+refBins[j+1])/2; });
  var xMin=refBins[0], xMax=refBins[nBins];

  // Resample any phase's histogram onto refMid using piecewise-constant lookup
  function resample(bins, counts){
    var out=new Array(nBins).fill(0);
    if(!bins.length) return out;
    for(var i=0;i<nBins;i++){
      var mx=refMid[i];
      var refW=refBins[i+1]-refBins[i];
      for(var k=0;k<bins.length-1;k++){
        if(mx>=bins[k] && mx<bins[k+1]){
          var srcW=bins[k+1]-bins[k];
          out[i]=srcW>0?Math.round(counts[k]*refW/srcW):0;
          break;
        }
      }
    }
    return out;
  }

  var traces=[];
  var _COLS=['#29b6f6','#69f0ae','#ffd166','#ff6b6b','#c77dff','#ff9800','#00e5ff','#f06292'];
  // Helper: build histogram from raw values on refMid grid
  function _histFromVals(vals){
    var y=new Array(nBins).fill(0);
    vals.forEach(function(v){
      var bi=Math.floor((v-xMin)/(xMax-xMin)*nBins);
      bi=Math.max(0,Math.min(nBins-1,bi)); y[bi]++;
    });
    return y;
  }
  phases.forEach(function(ph,i){
    var s=(pd.phases||{})[ph]; if(!s) return;
    var y;
    if(passKeysOv&&RAW_PIN_DATA&&RAW_PIN_DATA[pin]&&RAW_PIN_DATA[pin][ph]){
      var rawRowsOv=(RAW_PIN_DATA[pin]||{})[ph]||[];
      var filtVals=[];
      rawRowsOv.forEach(function(r){
        if(!filt.lots.has(LOTS[r[0]])||!filt.wfrs.has(r[1])) return;
        if(!passKeysOv.has(r[0]+'::'+r[1]+'::'+r[2]+'::'+r[3])) return;
        filtVals.push(r[4]);
      });
      if(!filtVals.length) return;
      y=_histFromVals(filtVals);
    } else {
      var bins=s.bins||[]; var counts=s.counts||[];
      if(!bins.length) return;
      y=resample(bins, counts);
    }
    if(doNorm){ var tot=y.reduce(function(a,b){return a+b;},0); if(tot>0) y=y.map(function(v){return v/tot;}); }
    var isSdt=_isSdtPhase(ph);
    traces.push({x:refMid, y:y, type:'scatter', mode:'lines', name:ph,
                 line:{color:_COLS[i%_COLS.length],width:2}, opacity:0.85,
                 visible:isSdt?'legendonly':true});
  });

  var shapes=[];
  var ovAnnotations=[];
  var uslHoverTraces=[];
  if(showUsl){
    // Group phases by USL value; draw one line per distinct USL, label with all phases sharing it
    var seenUsl={};
    var _SDS_ANNOT_COLS=['#ff6b6b','#ff9966','#ffaa44','#ffcc44','#ff8888','#ffaacc'];
    var _sac=0;
    phases.forEach(function(ph){
      if(_isSdtPhase(ph)) return;
      var s=(pd.phases||{})[ph]; if(!s) return;
      var u=s.usl!=null?s.usl:pd.usl; if(u==null) return;
      var key=u.toFixed(3);
      if(!seenUsl[key]) seenUsl[key]={usl:u,phs:[],col:_SDS_ANNOT_COLS[_sac++%_SDS_ANNOT_COLS.length]};
      seenUsl[key].phs.push(ph);
    });
    Object.values(seenUsl).forEach(function(entry){
      var u=entry.usl, lc=entry.col, phList=entry.phs;
      var key=u.toFixed(3);
      var labelText='<b>USL '+key+' mV</b><br>'+phList.join(', ');
      shapes.push({type:'line',x0:u,x1:u,y0:0,y1:1,yref:'paper',
                   line:{color:lc,width:1.5,dash:'dash'}});
      // Annotation: show phase names + USL value
      ovAnnotations.push({x:u, y:0.97, xref:'x', yref:'paper',
        text:'<b>USL '+key+'</b><br><span style="font-size:9px">'+phList.join('<br>')+'</span>',
        showarrow:false, xanchor:'left', yanchor:'top',
        font:{size:10, color:lc},
        bgcolor:'rgba(7,17,26,0.82)', bordercolor:lc, borderwidth:1, borderpad:3});
      // Invisible wide line trace for hover tooltip
      uslHoverTraces.push({
        x:[u,u], y:[0,1], yaxis:'y', mode:'lines',
        line:{color:'rgba(0,0,0,0)',width:18},
        showlegend:false, hoverinfo:'text',
        hovertext:'USL = '+key+' mV<br>Phases: '+phList.join(', '),
        name:'USL '+key+' mV'
      });
    });
  }
  var layout={
    paper_bgcolor:'#07111a', plot_bgcolor:'#07111a',
    font:{color:'#8ab4d4',size:11},
    margin:{l:48,r:16,t:10,b:40},
    xaxis:{title:{text:'Value (mV) \u2014 axis locked to '+refPh,font:{size:10}},
           color:'#445566',gridcolor:'#0d1828',zeroline:false,range:[xMin,xMax]},
    yaxis:{title:{text:doNorm?'Density':'Count',font:{size:10}},color:'#445566',gridcolor:'#0d1828',
           type:doLogY?'log':'linear'},
    legend:{font:{size:10},bgcolor:'rgba(0,0,0,0)'},
    shapes:shapes, annotations:ovAnnotations, showlegend:true
  };
  Plotly.react('pdm-xp-overlay-chart', traces.concat(uslHoverTraces), layout, {responsive:true, displayModeBar:false});
  // ── Stat table below overlay chart ───────────────────────────────────────
  var ovStEl=document.getElementById('pdm-xp-overlay-stats');
  if(ovStEl){
    var filt2=_pdmFocusFilter||_getPdmFilter();
    var useLive2=_focusModeActive&&RAW_PIN_DATA&&RAW_PIN_DATA[pin];
    var _fw=filt2&&filt2.wfrs&&filt2.wfrs.size>0;
    var _fl=filt2&&filt2.lots&&filt2.lots.size>0;
    var hdr2='<tr style="background:#131a2a;position:sticky;top:0">';
    ['Phase','N','Mean (mV)','σ (mV)','Median','P1','P99','Cpk','μ+3σ','Fail>USL','Fail%','USL'].forEach(function(c){
      hdr2+='<th style="padding:3px 9px;border:1px solid #1e3050;font-size:0.71rem;color:#8ab4d4;white-space:nowrap">'+c+'</th>';
    });
    hdr2+='</tr>';
    var rows2='';
    phases.forEach(function(ph){
      var clr=_PHASE_CLR[ph]||'#8ab4d4';
      var usl=(pd.phases&&pd.phases[ph])?pd.phases[ph].usl:pd.usl;
      var s2;
      if(useLive2&&RAW_PIN_DATA[pin][ph]){
        var rr=RAW_PIN_DATA[pin][ph]||[]; var vals2=[];
        rr.forEach(function(r){
          if(_fl&&!filt2.lots.has(LOTS[r[0]])) return;
          if(_fw&&!filt2.wfrs.has(r[1])) return;
          if(passKeysOv&&!passKeysOv.has(r[0]+'::'+r[1]+'::'+r[2]+'::'+r[3])) return;
          vals2.push(r[4]);
        });
        if(!vals2.length){ s2={n:0}; }
        else {
          var _n2=vals2.length,_s=0,_s2=0;
          vals2.forEach(function(v){_s+=v;_s2+=v*v;});
          var _m=_s/_n2,_sg=Math.sqrt(Math.max(0,_s2/_n2-_m*_m));
          var _sv2=vals2.slice().sort(function(a,b){return a-b;});
          var _nf=usl!=null?vals2.filter(function(v){return v>usl;}).length:null;
          var _cpk=usl!=null&&_sg>0?(usl-_m)/(3*_sg):null;
          s2={n:_n2,mean:_m,sigma:_sg,
            p1:_sv2[Math.max(0,Math.floor(_n2*0.01))],
            p99:_sv2[Math.min(_n2-1,Math.floor(_n2*0.99))],
            median:_sv2[Math.floor(_n2/2)],
            cpk:_cpk,mu3s:round2(_m+3*_sg),nFail:_nf};
        }
      } else {
        var ps=(pd.phases&&pd.phases[ph])||{};
        s2={n:ps.n_total,mean:ps.mean,sigma:ps.sigma,p1:ps.p1,p99:ps.p99,median:null,
          cpk:ps.cpk,mu3s:ps.mean!=null&&ps.sigma!=null?round2(ps.mean+3*ps.sigma):null,
          nFail:ps.n_fail};
      }
      var fc=s2.nFail>0?'#ff6b6b':'#c0ccd8';
      var cpkClr=s2.cpk==null?'#445566':s2.cpk<1?'#ff6b6b':s2.cpk<1.33?'#ffd166':'#69f0ae';
      var fp=s2.n>0&&s2.nFail!=null?round2(s2.nFail/s2.n*100)+'%':'—';
      rows2+='<tr style="border-bottom:1px solid #0d1828">';
      rows2+='<td style="padding:3px 9px;font-size:0.72rem;font-weight:700;color:'+clr+'">'+ph+'</td>';
      rows2+='<td style="padding:3px 9px;font-size:0.72rem">'+s2.n+'</td>';
      rows2+='<td style="padding:3px 9px;font-size:0.72rem;color:#c0ccd8">'+(s2.mean!=null?s2.mean.toFixed(3):'—')+'</td>';
      rows2+='<td style="padding:3px 9px;font-size:0.72rem;color:#a78bfa">'+(s2.sigma!=null?s2.sigma.toFixed(3):'—')+'</td>';
      rows2+='<td style="padding:3px 9px;font-size:0.72rem;color:#8ab4d4">'+(s2.median!=null?s2.median.toFixed(3):'—')+'</td>';
      rows2+='<td style="padding:3px 9px;font-size:0.72rem;color:#445566">'+(s2.p1!=null?s2.p1.toFixed(3):'—')+'</td>';
      rows2+='<td style="padding:3px 9px;font-size:0.72rem;color:#445566">'+(s2.p99!=null?s2.p99.toFixed(3):'—')+'</td>';
      rows2+='<td style="padding:3px 9px;font-size:0.72rem;color:'+cpkClr+';font-weight:700">'+(s2.cpk!=null?s2.cpk.toFixed(3):'—')+'</td>';
      rows2+='<td style="padding:3px 9px;font-size:0.72rem;color:#ffd166">'+(s2.mu3s!=null?s2.mu3s:'—')+'</td>';
      rows2+='<td style="padding:3px 9px;font-size:0.72rem;color:'+fc+';font-weight:'+(s2.nFail>0?'700':'400')+'">'+(s2.nFail!=null?s2.nFail:'—')+'</td>';
      rows2+='<td style="padding:3px 9px;font-size:0.72rem;color:'+fc+'">'+fp+'</td>';
      rows2+='<td style="padding:3px 9px;font-size:0.72rem;color:#ff6b6b">'+(usl!=null?usl.toFixed(3):'—')+'</td>';
      rows2+='</tr>';
    });
    ovStEl.innerHTML='<table style="border-collapse:collapse;width:100%;font-size:0.72rem"><thead><tr>'+hdr2+'</tr></thead><tbody>'+rows2+'</tbody></table>';
  }
}

// ── Tab: SDS/SDT Histogram (per-phase histogram with phase selector buttons) ─
function _xpRenderSdHist(){
  var pin=_xpPin; if(!pin) return;
  var phases=_xpPhases(pin);
  // Populate pass filter on first show for this pin
  var passPhEl=document.getElementById('xp-hist-passph');
  if(passPhEl&&passPhEl.options.length<=1&&phases.length){
    phases.forEach(function(ph){ passPhEl.innerHTML+='<option value="'+ph+'">Pass '+ph+'</option>'; });
    // Only default to Post-Surge when raw data is available; otherwise leave at (none) to avoid
    // showing a selected filter that silently does nothing in stats-only mode
    if(_focusHasPin(pin)){
      var _defIdx=phases.indexOf('Post-Surge'); if(_defIdx>=0) passPhEl.selectedIndex=_defIdx+1;
    }
    // Re-render histogram with the default filter applied
    _pdmOverlayToggle(); return;
  }
  // Resize chart in case it was hidden on initial render
  setTimeout(function(){ if(typeof Plotly!=='undefined'&&document.getElementById('pdm-chart')) Plotly.Plots.resize('pdm-chart'); }, 40);
}

// ── Tab 1: Phase Grid Table ─────────────────────────────────────────────────
function _xpRenderGrid(){
  var pin=_xpPin;
  var phases=_xpPhases(pin);
  var idx=_xpBuildIndex(pin);
  var pd=PIN_DISTRIB[pin]||{};
  // USL per phase
  var phUsl={};
  phases.forEach(function(ph){ phUsl[ph]=(pd.phases&&pd.phases[ph])?pd.phases[ph].usl:pd.usl; });
  var rows=Object.values(idx);
  rows.sort(function(a,b){ return a.wfr-b.wfr||a.x-b.x||a.y-b.y; });
  var hdr='<tr style="background:#131a2a;color:#8ab4d4;position:sticky;top:0">'
    +'<th style="padding:3px 7px;border:1px solid #1e3050;font-size:0.72rem">Lot</th>'
    +'<th style="padding:3px 7px;border:1px solid #1e3050;font-size:0.72rem">W#</th>'
    +'<th style="padding:3px 7px;border:1px solid #1e3050;font-size:0.72rem">X</th>'
    +'<th style="padding:3px 7px;border:1px solid #1e3050;font-size:0.72rem">Y</th>';
  phases.forEach(function(ph){
    var c=_PHASE_CLR[ph]||'#8ab4d4';
    hdr+='<th style="padding:3px 7px;border:1px solid #1e3050;font-size:0.71rem;color:'+c+'">'+ph+'</th>';
  });
  hdr+='</tr>';
  var tbody='';
  rows.slice(0,400).forEach(function(r){
    var lot=LOTS[r.li]||('L'+r.li);
    tbody+='<tr style="border-bottom:1px solid #0d1828">'
      +'<td style="padding:2px 6px;font-size:0.71rem;color:#8ab4d4">'+lot+'</td>'
      +'<td style="padding:2px 6px;font-size:0.71rem">W'+String(r.wfr).padStart(2,'0')+'</td>'
      +'<td style="padding:2px 6px;font-size:0.71rem">'+r.x+'</td>'
      +'<td style="padding:2px 6px;font-size:0.71rem">'+r.y+'</td>';
    phases.forEach(function(ph){
      var v=r[ph];
      if(v==null){ tbody+='<td style="padding:2px 6px;font-size:0.71rem;color:#2a4060">\u2014</td>'; return; }
      var usl=phUsl[ph];
      var fail=usl!=null&&v>usl;
      var vc=fail?'#ff6b6b':'#c0ccd8';
      var bg=fail?'background:#2a0a0a;':'';
      tbody+='<td style="padding:2px 6px;font-size:0.71rem;font-weight:'+(fail?'700':'400')+';color:'+vc+';'+bg+'">'+round2(v)+'</td>';
    });
    tbody+='</tr>';
  });
  var note=rows.length>400?'<div style="font-size:0.69rem;color:#445566;padding:3px 0">Showing first 400 of '+rows.length+' dies</div>':'';
  document.getElementById('pdm-xp-grid-tbl').innerHTML=note+'<table style="border-collapse:collapse;width:100%;font-size:0.72rem"><thead>'+hdr+'</thead><tbody>'+tbody+'</tbody></table>';
  var infoEl=document.getElementById('pdm-xp-grid-info');
  if(infoEl) infoEl.textContent=rows.length+' dies \u00d7 '+phases.length+' phases'+(rows.length>400?' (table shows first 400)':'');
  _xpGridRows=rows; _xpGridPhases=phases; _xpGridPhUsl=phUsl;
}
var _xpGridRows=[], _xpGridPhases=[], _xpGridPhUsl={};
function _xpDownloadGrid(){
  var pin=_xpPin||'pin';
  var hdr=['Lot','W#','X','Y'].concat(_xpGridPhases).join(',');
  var lines=[hdr];
  _xpGridRows.forEach(function(r){
    var lot=LOTS[r.li]||('L'+r.li);
    var cols=[lot,'W'+String(r.wfr).padStart(2,'0'),r.x,r.y];
    _xpGridPhases.forEach(function(ph){ cols.push(r[ph]!=null?r[ph]:''); });
    lines.push(cols.join(','));
  });
  var blob=new Blob([lines.join('\r\n')],{type:'text/csv'});
  var a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download=pin.replace(/[^a-zA-Z0-9_]/g,'_')+'_cross_phase.csv'; a.click();
}

// ── Tab 2 (was 3): Delta Histogram ──────────────────────────────────────────
function _xpPopulateSelects(ids, phases, defaults){
  ids.forEach(function(id,i){
    var sel=document.getElementById(id); if(!sel) return;
    sel.innerHTML=''; phases.forEach(function(ph){ sel.innerHTML+='<option>'+ph+'</option>'; });
    if(defaults[i]!=null&&defaults[i]<phases.length) sel.selectedIndex=defaults[i];
  });
}

// ── Tab 3 (was 4): Wafer Map ────────────────────────────────────────────────
function _xpWmPhaseChange(){
  var pin=_xpPin; if(!pin) return;
  var phases=_xpPhases(pin);
  var selPhEl=document.getElementById('xp-wm-ph');
  var ph=selPhEl?selPhEl.value:phases[0]; if(!ph) return;
  var filt=_pdmFocusFilter||_getPdmFilter();
  var rawRows=(RAW_PIN_DATA[pin]||{})[ph]||[];
  var wfrs=[]; rawRows.forEach(function(r){if(filt.lots.has(LOTS[r[0]])&&filt.wfrs.has(r[1])&&wfrs.indexOf(r[1])<0) wfrs.push(r[1]);});
  wfrs.sort(function(a,b){return a-b;});
  var selWfrEl=document.getElementById('xp-wm-wfr');
  if(selWfrEl) selWfrEl.innerHTML=wfrs.map(function(w){return '<option value="'+w+'">W'+String(w).padStart(2,'0')+'</option>';}).join('');
  _xpRenderWmap();
}
function _xpWmModeChange(){
  var modeEl=document.getElementById('xp-wm-mode');
  var refWrap=document.getElementById('xp-wm-ref-wrap');
  var refEl=document.getElementById('xp-wm-ref');
  var mode=modeEl?modeEl.value:'val';
  if(refWrap) refWrap.style.display=mode==='delta'?'inline':'none';
  if(mode==='delta'&&refEl&&refEl.options.length===0){
    var phases=_xpPhases(_xpPin);
    phases.forEach(function(ph){ refEl.innerHTML+='<option>'+ph+'</option>'; });
  }
  _xpRenderWmap();
}
function _xpRenderWmap(){
  var pin=_xpPin; if(!pin) return;
  var phases=_xpPhases(pin);
  if(!phases.length) return;
  var selPhEl=document.getElementById('xp-wm-ph');
  var selWfrEl=document.getElementById('xp-wm-wfr');
  var modeEl=document.getElementById('xp-wm-mode');
  var refWrap=document.getElementById('xp-wm-ref-wrap');
  var refEl=document.getElementById('xp-wm-ref');
  // Only populate phase select if empty (preserve user selection)
  if(selPhEl&&selPhEl.options.length===0){
    phases.forEach(function(ph){ selPhEl.innerHTML+='<option>'+ph+'</option>'; });
  }
  var mode=modeEl?modeEl.value:'val';
  if(refWrap) refWrap.style.display=mode==='delta'?'inline':'none';
  // Populate wafer selector from currently-selected phase
  var selPh=selPhEl?selPhEl.value:phases[0];
  var rawRows=(RAW_PIN_DATA[pin]||{})[selPh]||[];
  var filt=_pdmFocusFilter||_getPdmFilter();
  var wfrs=[]; rawRows.forEach(function(r){if(filt.lots.has(LOTS[r[0]])&&filt.wfrs.has(r[1])&&wfrs.indexOf(r[1])<0) wfrs.push(r[1]);});
  wfrs.sort(function(a,b){return a-b;});
  if(selWfrEl){
    var curWfr=selWfrEl.value;
    selWfrEl.innerHTML=wfrs.map(function(w){return '<option value="'+w+'"'+(String(w)===curWfr?' selected':'')+'>W'+String(w).padStart(2,'0')+'</option>';}).join('');
  }
  var selWfr=selWfrEl?+selWfrEl.value:wfrs[0];
  var refPh=refEl?refEl.value:phases[0];
  // Build arrays
  var xs=[],ys=[],vals=[],texts=[];
  var refMap={};
  if(mode==='delta'){
    var refRows=(RAW_PIN_DATA[pin]||{})[refPh]||[];
    refRows.forEach(function(r){if(r[1]===selWfr&&filt.lots.has(LOTS[r[0]])) refMap[r[2]+'::'+r[3]]=r[4];});
  }
  rawRows.forEach(function(r){
    if(r[1]!==selWfr||!filt.lots.has(LOTS[r[0]])) return;
    var v=r[4];
    if(mode==='delta'){ var rv=refMap[r[2]+'::'+r[3]]; if(rv==null) return; v=round2(r[4]-rv); }
    xs.push(r[2]); ys.push(r[3]); vals.push(round2(v));
    texts.push('('+r[2]+','+r[3]+')<br>'+selPh+': '+r[4]+'mV'+(mode==='delta'?' Δ='+v+'mV':''));
  });
  var pd=PIN_DISTRIB[pin]||{};
  var usl=(pd.phases&&pd.phases[selPh])?pd.phases[selPh].usl:pd.usl;
  Plotly.react('pdm-xp-wmap-chart',[{
    x:xs,y:ys,z:vals,mode:'markers',type:'scatter',
    marker:{size:12,color:vals,colorscale:mode==='delta'?'RdBu':'YlOrRd',reversescale:mode!=='delta',
      colorbar:{title:(mode==='delta'?'Δ':'val')+' mV',thickness:12,len:0.7},
      cmin:mode==='delta'?-Math.max.apply(null,vals.map(Math.abs)):undefined,
      cmax:mode==='delta'?Math.max.apply(null,vals.map(Math.abs)):undefined,
      line:{width:vals.map(function(v){return usl!=null&&v>usl?1.5:0;}),color:'#ff4444'}},
    text:texts,hovertemplate:'%{text}<extra></extra>'
  }],Object.assign(L({
    xaxis:{title:'X',gridcolor:'#1e3050',zeroline:false},
    yaxis:{title:'Y',gridcolor:'#1e3050',zeroline:false,scaleanchor:'x',scaleratio:1},
    margin:{t:20,l:50,r:60,b:50}
  }),{}),{responsive:true,displayModeBar:true});
}

// ── Tab 4 (was 5): CDF overlay ──────────────────────────────────────────────
function _xpRenderCdf(){
  var pin=_xpPin; if(!pin) return;
  var phases=_xpPhases(pin);
  var filt=_pdmFocusFilter||_getPdmFilter();
  var pd=PIN_DISTRIB[pin]||{};
  var logX=document.getElementById('xp-cdf-log')&&document.getElementById('xp-cdf-log').checked;
  var passPhEl=document.getElementById('xp-cdf-passph');
  // Populate pass-phase selector on first call; read passPh AFTER so default is honoured
  if(passPhEl&&passPhEl.options.length<=1){
    phases.forEach(function(ph){ passPhEl.innerHTML+='<option value="'+ph+'">Pass '+ph+'</option>'; });
    var _cdfDefIdx=phases.indexOf('Post-Surge');
    if(_cdfDefIdx>=0) passPhEl.selectedIndex=_cdfDefIdx+1;
  }
  var passPh=passPhEl?passPhEl.value:'';
  // Build pass-set
  var passKeys=null;
  if(passPh){
    var passUsl=(pd.phases&&pd.phases[passPh])?pd.phases[passPh].usl:pd.usl;
    if(passUsl!=null){
      passKeys=new Set();
      var pRows=(RAW_PIN_DATA[pin]||{})[passPh]||[];
      pRows.forEach(function(r){
        if(filt.lots.has(LOTS[r[0]])&&filt.wfrs.has(r[1])&&r[4]<=passUsl)
          passKeys.add(r[0]+'::'+r[1]+'::'+r[2]+'::'+r[3]);
      });
    }
  }
  var infoEl=document.getElementById('xp-cdf-pass-info');
  if(infoEl) infoEl.textContent=passKeys?passKeys.size+' dies pass '+passPh:'';
  var traces=[];
  var statRows=[];
  var _DASH_PATTERNS=['solid','dash','dot','dashdot','longdash','longdashdot'];
  // Helper: thin a sorted array to ~2000 points but keep all tail (>99th pct) points
  function _thin(sortedVals){
    var n=sortedVals.length;
    if(n<=2000) return sortedVals.slice();
    var tailIdx=Math.floor(n*0.97);
    var body=sortedVals.slice(0,tailIdx);
    var tail=sortedVals.slice(tailIdx);
    var step=Math.max(1,Math.floor(body.length/1400));
    var thinned=[];
    for(var i=0;i<body.length;i+=step) thinned.push(body[i]);
    return thinned.concat(tail);
  }
  phases.forEach(function(ph,phIdx){
    var rawRows=(RAW_PIN_DATA[pin]||{})[ph]||[];
    var vals=[];
    rawRows.forEach(function(r){
      if(!filt.lots.has(LOTS[r[0]])||!filt.wfrs.has(r[1])) return;
      if(passKeys&&!passKeys.has(r[0]+'::'+r[1]+'::'+r[2]+'::'+r[3])) return;
      vals.push(r[4]);
    });
    if(!vals.length) return;
    vals.sort(function(a,b){return a-b;});
    var n=vals.length;
    var thinned=_thin(vals);
    var pcts=[], nAtPt=[];
    thinned.forEach(function(v){
      var lo=0,hi=n-1;
      while(lo<hi){var m=(lo+hi)>>1; if(vals[m]<v)lo=m+1; else hi=m;}
      pcts.push(round2((lo+1)/n*100));
      nAtPt.push(lo+1);
    });
    var col=_PHASE_CLR[ph]||'#8ab4d4';
    var dash=_DASH_PATTERNS[phIdx%_DASH_PATTERNS.length];
    traces.push({x:thinned,y:pcts,customdata:nAtPt.map(function(c){return c+' / '+n;}),mode:'lines',name:ph,
      line:{color:col,width:2.5,shape:'hv',dash:dash},
      hovertemplate:'<b>'+ph+'</b><br>%{x:.2f} mV<br>%{y:.1f}% \u2014 %{customdata} dies<extra></extra>'});
    var usl=(pd.phases&&pd.phases[ph])?pd.phases[ph].usl:pd.usl;
    if(usl!=null){
      var nBelow=vals.filter(function(v){return v<=usl;}).length;
      var failPct=round2((1-nBelow/n)*100);
      var passY=round2(nBelow/n*100);
      traces.push({x:[usl,usl],y:[Math.max(0,passY-6),Math.min(100,passY+6)],
        mode:'lines',showlegend:false,
        line:{color:col,width:3},
        hovertemplate:'<b>'+ph+' USL</b> '+usl+' mV<br>'+failPct+'% exceed<extra></extra>'});
      statRows.push({ph:ph,col:col,n:n,nFail:Math.round((1-nBelow/n)*n),failPct:failPct,usl:usl});
    }
  });
  Plotly.react('pdm-xp-cdf-chart',traces,Object.assign(L({
    xaxis:{title:'Value (mV)',gridcolor:'#1e3050',zeroline:false,type:logX?'log':'linear'},
    yaxis:{title:'Cumulative %',gridcolor:'#1e3050',range:[0,100],ticksuffix:'%'},
    legend:{orientation:'h',y:1.1,bgcolor:'rgba(0,0,0,0)',font:{size:11}},
    hovermode:'closest',
    margin:{t:20,l:60,r:15,b:55}
  }),{}),{responsive:true,displayModeBar:false});
  // Stat pills per phase
  var statsEl=document.getElementById('pdm-xp-cdf-stats');
  if(statsEl){
    statsEl.innerHTML=statRows.map(function(s){
      var vc=s.failPct>5?'#ff6b6b':s.failPct>1?'#ffd166':'#69f0ae';
      return '<div style="background:#0d1828;border:1px solid '+s.col+'44;border-left:3px solid '+s.col+';border-radius:6px;padding:5px 10px;min-width:120px">'
        +'<div style="font-size:0.68rem;color:'+s.col+';font-weight:700;margin-bottom:2px">'+s.ph+'</div>'
        +'<div style="font-size:0.88rem;font-weight:700;color:'+vc+'">'+s.failPct+'%</div>'
        +'<div style="font-size:0.67rem;color:#445566">'+s.nFail+' / '+s.n+' fail &gt; USL '+s.usl+'mV</div>'
        +'</div>';
    }).join('');
  }
}

// ── Tab 5: Violin / Box ───────────────────────────────────────────────────────
var _vlLastTracePhases=[];
var _vlLastPd={};
function _vlSwitchPhase(pin, ph){
  _pdmPhase=ph;
  var _vlTabDiv=document.getElementById('pdm-vl-phase-tabs');
  if(_vlTabDiv){
    Array.from(_vlTabDiv.children).forEach(function(btn){
      var _bph=btn.getAttribute('data-ph');
      var _col=btn.getAttribute('data-col');
      btn.style.background=_bph===ph?_col+'33':'#0d1520';
      btn.style.borderColor=_bph===ph?_col:_col+'44';
    });
  }
  _vlStatsOnly=true;
  _xpRenderViolin();
  _vlStatsOnly=false;
}
function _xpRenderViolin(){
  var pin=_xpPin; if(!pin) return;
  var phases=_xpPhases(pin);
  var filt=_pdmFocusFilter||_getPdmFilter();
  var pd=PIN_DISTRIB[pin]||{};
  // In non-live mode force box and hide live-only controls
  var _isLive=_focusModeActive&&phases.length>0;
  var _modeEl=document.getElementById('xp-vl-mode');
  var _ptsEl=document.getElementById('xp-vl-pts');
  if(_modeEl) _modeEl.parentElement.style.display=_isLive?'':'none';
  if(_ptsEl)  _ptsEl.parentElement.style.display=_isLive?'':'none';
  var mode=_isLive?((document.getElementById('xp-vl-mode')||{}).value||'box'):'box';
  var showPts=_isLive&&document.getElementById('xp-vl-pts')&&document.getElementById('xp-vl-pts').checked;
  var showUsl=document.getElementById('xp-vl-usl')&&document.getElementById('xp-vl-usl').checked;
  var showSigma=!(document.getElementById('xp-vl-sigma'))||document.getElementById('xp-vl-sigma').checked;
  var hideSdt=document.getElementById('xp-vl-hidesdt')&&!document.getElementById('xp-vl-hidesdt').checked;
  var _SDT_PHASES={'SDT-Start':1,'SDT-Final':1};
  var _EXCLUDE_PHASES={'ISVM-EDC':1};  // voltage measurement — excluded from current-measure box
  var _vlFilterLots=filt&&filt.lots&&filt.lots.size>0;
  var _vlFilterWfrs=filt&&filt.wfrs&&filt.wfrs.size>0;
  var passPhEl=document.getElementById('xp-vl-passph');
  // Populate on first call — read passPh AFTER so default takes effect
  if(passPhEl&&passPhEl.options.length<=1){
    phases.forEach(function(ph){ passPhEl.innerHTML+='<option value="'+ph+'">Pass '+ph+'</option>'; });
    var _vlDefIdx=phases.indexOf('Post-Surge');
    if(_vlDefIdx>=0) passPhEl.selectedIndex=_vlDefIdx+1;
  }
  var passPh=passPhEl?passPhEl.value:'';
  // Build pass-set: keys of dies ≤ USL at passPh
  var passKeys=null;
  if(passPh){
    var passUsl=(pd.phases&&pd.phases[passPh])?pd.phases[passPh].usl:pd.usl;
    if(passUsl!=null){
      passKeys=new Set();
      var pRows=(RAW_PIN_DATA[pin]||{})[passPh]||[];
      pRows.forEach(function(r){
        if(_vlFilterLots&&!filt.lots.has(LOTS[r[0]])) return;
        if(_vlFilterWfrs&&!filt.wfrs.has(r[1])) return;
        if(r[4]<=passUsl) passKeys.add(r[0]+'::'+r[1]+'::'+r[2]+'::'+r[3]);
      });
    }
  }
  var infoEl=document.getElementById('xp-vl-pass-info');
  if(infoEl) infoEl.textContent=passKeys?passKeys.size+' dies pass '+passPh:'';
  var traces=[];
  var tracePhases=[];
  var phaseStats={};  // {ph: {mean, sigma, name}} for sigma markers
  phases.forEach(function(ph){
    if(hideSdt&&_SDT_PHASES[ph]) return;  // skip SDT phases (no trace = no x-axis label)
    if(_EXCLUDE_PHASES[ph]) return;  // ISVM-EDC is voltage — exclude from current-measure box
    var rawRows=(RAW_PIN_DATA[pin]||{})[ph]||[];
    var vals=[];
    rawRows.forEach(function(r){
      if(_vlFilterLots&&!filt.lots.has(LOTS[r[0]])) return;
      if(_vlFilterWfrs&&!filt.wfrs.has(r[1])) return;
      if(passKeys&&!passKeys.has(r[0]+'::'+r[1]+'::'+r[2]+'::'+r[3])) return;
      vals.push(r[4]);
    });
    if(!vals.length) return;
    var col=_PHASE_CLR[ph]||'#8ab4d4';
    var usl=(pd.phases&&pd.phases[ph])?pd.phases[ph].usl:pd.usl;
    var nFail=usl!=null?vals.filter(function(v){return v>usl;}).length:0;
    var nm=ph+(nFail>0?' ('+nFail+' fail)':'');
    // Compute mean + sigma for this phase (used by sigma markers)
    var _n=vals.length, _s=0, _s2=0;
    vals.forEach(function(v){_s+=v;_s2+=v*v;});
    var _mean=_s/_n, _sigma=Math.sqrt(Math.max(0,_s2/_n-_mean*_mean));
    phaseStats[nm]={mean:_mean,sigma:_sigma};
    if(mode==='violin'){
      traces.push({type:'violin',y:vals,name:nm,
        box:{visible:true,width:0.3},
        meanline:{visible:true},
        points:showPts?'outliers':'none',
        jitter:0.3,pointpos:0,
        line:{color:col,width:1.5},fillcolor:col+'44',
        marker:{color:col,size:3,opacity:0.5}});
    } else {
      traces.push({type:'box',y:vals,name:nm,
        boxpoints:showPts?'outliers':false,
        jitter:0.4,pointpos:0,
        line:{color:col,width:1.5},fillcolor:col+'33',
        marker:{color:col,size:3,opacity:0.6},
        whiskerwidth:0.5});
    }
    tracePhases.push(ph);
  });
  // ── Build / update phase tabs in violin panel ─────────────────────────
  var _vlTabDiv=document.getElementById('pdm-vl-phase-tabs');
  if(_vlTabDiv){
    // Always use full phase_list so tabs include all phases regardless of SDT/live filter
    var _vlAllPhs=(pd.phase_list||Object.keys(pd.phases||{}));
    if(_vlTabDiv._pin!==pin){
      _vlTabDiv.innerHTML='';
      _vlAllPhs.forEach(function(ph){
        var _col=_PHASE_CLR[ph]||'#8ab4d4';
        var _btn=document.createElement('button');
        _btn.setAttribute('data-ph',ph);
        _btn.setAttribute('data-col',_col);
        _btn.textContent=ph;
        _btn.style.cssText='font-size:0.71rem;padding:2px 9px;border-radius:4px;cursor:pointer;border:1px solid '+_col+'44;background:#0d1520;color:'+_col+';transition:background 0.12s,border-color 0.12s';
        _btn.onclick=(function(p){ return function(){ _vlSwitchPhase(pin,p); }; })(ph);
        _vlTabDiv.appendChild(_btn);
      });
      _vlTabDiv._pin=pin;
      // Auto-select active phase if set, else default to first available
      if(!_pdmPhase||_vlAllPhs.indexOf(_pdmPhase)<0) _pdmPhase=_vlAllPhs[0]||_pdmPhase;
    }
    // Highlight active tab
    Array.from(_vlTabDiv.children).forEach(function(btn){
      var _bph=btn.getAttribute('data-ph');
      var _col=btn.getAttribute('data-col');
      btn.style.background=_bph===_pdmPhase?_col+'33':'#0d1520';
      btn.style.borderColor=_bph===_pdmPhase?_col:_col+'44';
    });
  }
  // Non-live fallback: build precomputed box traces from PIN_DISTRIB summary stats
  if(!traces.length && pd.phases){
    var _nlPhaseList=pd.phase_list||Object.keys(pd.phases);
    _nlPhaseList.forEach(function(ph){
      if(hideSdt&&_SDT_PHASES[ph]) return;  // respect SDT filter so axis auto-adjusts
      if(_EXCLUDE_PHASES[ph]) return;  // ISVM-EDC is voltage — exclude from current-measure box
      var ps=pd.phases[ph]; if(!ps) return;
      var col=_PHASE_CLR[ph]||'#8ab4d4';
      var nFail=ps.n_fail||0;
      var nm=ph+(nFail>0?' ('+nFail+' fail)':'');
      phaseStats[nm]={mean:ps.mean,sigma:ps.sigma};
      // Use actual min/max as fences so the whisker range reflects true data spread
      var _lf=ps.min_val!=null?ps.min_val:(ps.p1!=null?ps.p1:ps.mean-3*ps.sigma);
      var _uf=ps.max_val!=null?ps.max_val:(ps.p99!=null?ps.p99:ps.mean+3*ps.sigma);
      traces.push({type:'box',name:nm,
        x:[nm],
        lowerfence:[_lf],
        q1:[ps.p25!=null?ps.p25:ps.mean-0.6745*ps.sigma],
        median:[ps.median!=null?ps.median:ps.mean],
        mean:[ps.mean],
        q3:[ps.p75!=null?ps.p75:ps.mean+0.6745*ps.sigma],
        upperfence:[_uf],
        line:{color:col,width:1.5},fillcolor:col+'33',
        whiskerwidth:0.5});
      // Jitter dots: reconstruct y-values from histogram bins, render as invisible box with points
      var _binEdges=ps.bins||[]; var _binCounts=ps.counts||[];
      if(_binEdges.length>1&&_binCounts.length>0){
        var _totalN=ps.n_total||1;
        var _maxPts=800; var _scale=Math.min(1,_maxPts/_totalN);
        var _jY=[];
        for(var _bi=0;_bi<_binCounts.length;_bi++){
          var _cnt=_binCounts[_bi]; if(!_cnt) continue;
          var _blo=_binEdges[_bi], _bhi=_binEdges[_bi+1];
          var _nPts=Math.max(1,Math.round(_cnt*_scale));
          for(var _pi=0;_pi<_nPts;_pi++) _jY.push(_blo+Math.random()*(_bhi-_blo));
        }
        // Anchor dots at whisker extremes so dot cloud spans full box range
        if(_jY.length&&_lf<_jY[0]) _jY.unshift(_lf);
        if(_jY.length&&_uf>_jY[_jY.length-1]) _jY.push(_uf);
        if(_jY.length){
          traces.push({type:'box',y:_jY,x:_jY.map(function(){return nm;}),name:nm,
            boxpoints:'all',jitter:0.4,pointpos:0,
            marker:{color:col,size:3,opacity:0.45},
            line:{color:'rgba(0,0,0,0)',width:0},
            fillcolor:'rgba(0,0,0,0)',whiskerwidth:0,
            showlegend:false,hoverinfo:'skip'});
        }
      }
      tracePhases.push(ph);
    });
  }
  if(!traces.length){
    Plotly.purge('pdm-xp-violin-chart');
    document.getElementById('pdm-xp-violin-chart').innerHTML='<div style="color:#445566;padding:20px;font-size:0.8rem">No data for current filter.</div>';
    return;
  }
  // Build USL shapes, annotations, and hover traces
  _vlLastTracePhases=tracePhases;
  _vlLastPd=pd;
  var _vlSA=showUsl?_vlBuildUslShapesAnnots(tracePhases,pd,null):{shapes:[],annots:[]};
  // Sigma marker scatter traces (mean+3σ/6σ/12σ per phase as dash-line markers)
  var sigmaTraces=[];
  if(showSigma){
    var _sigCfg=[
      {mult:3, color:'#ffd166', sym:'line-ew-open', sz:18, lbl:'3\u03c3'},
      {mult:6, color:'#ff9f1c', sym:'line-ew-open', sz:14, lbl:'6\u03c3'},
      {mult:12,color:'#ff6b6b', sym:'line-ew-open', sz:10, lbl:'12\u03c3'}
    ];
    _sigCfg.forEach(function(cfg){
      var xs=[], ys=[], txts=[];
      traces.forEach(function(tr){
        var st=phaseStats[tr.name]; if(!st||st.sigma<=0) return;
        xs.push(tr.name);
        var yv=Math.round((st.mean+cfg.mult*st.sigma)*100)/100;
        ys.push(yv); txts.push(cfg.lbl+': '+yv+' mV (\u03bc+'+(cfg.mult)+'\u03c3)');
      });
      if(!xs.length) return;
      sigmaTraces.push({type:'scatter',mode:'markers+text',x:xs,y:ys,
        name:cfg.lbl,text:txts.map(function(){return cfg.lbl;}),
        hovertext:txts,hovertemplate:'%{hovertext}<extra>'+cfg.lbl+'</extra>',
        textposition:'middle right',textfont:{color:cfg.color,size:10},
        marker:{symbol:cfg.sym,size:cfg.sz,color:cfg.color,line:{color:cfg.color,width:2}},
        showlegend:true});
    });
  }
  // Invisible scatter traces per unique USL for hover info
  var uslHoverTraces=[];
  if(showUsl){
    var _seenH={};
    tracePhases.forEach(function(ph,i){
      var usl=(pd.phases&&pd.phases[ph])?pd.phases[ph].usl:pd.usl;
      if(usl==null) return;
      if(!_seenH[usl]) _seenH[usl]={usl:usl,names:[],phs:[]};
      _seenH[usl].names.push(traces[i].name);
      _seenH[usl].phs.push(ph);
    });
    Object.keys(_seenH).forEach(function(k){
      var h=_seenH[k];
      uslHoverTraces.push({type:'scatter',mode:'lines',
        x:h.names,y:h.names.map(function(){return h.usl;}),
        line:{color:'rgba(255,68,68,0.01)',width:16},
        hovertemplate:'<b>USL: '+h.usl+' mV</b><br>'+h.phs.join(', ')+'<extra>USL</extra>',
        showlegend:false,name:'USL '+h.usl+' mV'});
    });
  }
  var _vlAutoRange=!(document.getElementById('xp-vl-autorange'))||document.getElementById('xp-vl-autorange').checked;
  var _vlYaxis={title:'Value (mV)',gridcolor:'#1e3050',zeroline:false,autorange:true};
  if(!_vlAutoRange){
    var _vlYmin=parseFloat(document.getElementById('xp-vl-ymin').value);
    var _vlYmax=parseFloat(document.getElementById('xp-vl-ymax').value);
    if(!isNaN(_vlYmin)&&!isNaN(_vlYmax)&&_vlYmax>_vlYmin) _vlYaxis={title:'Value (mV)',gridcolor:'#1e3050',zeroline:false,range:[_vlYmin,_vlYmax]};
  }
  var layout = Object.assign(L({
    yaxis:_vlYaxis,
    xaxis:{gridcolor:'#1e3050'},
    shapes:_vlSA.shapes,
    annotations:_vlSA.annots,
    legend:{orientation:'h',y:1.08},
    margin:{t:20,l:60,r:130,b:55}
  }),{});
  if(mode==='violin') layout.violinmode='group';
  else layout.boxmode='group';
  var _allTraces=traces.concat(uslHoverTraces).concat(sigmaTraces);
  // ── Critical stats table per phase ──────────────────────────────────────
  // Use full phase_list so stats table and pills cover ALL phases (including SDT)
  var _statsPhaseList=pd.phase_list||tracePhases;
  var vlStatsEl=document.getElementById('pdm-xp-violin-stats');
  if(vlStatsEl){
    var _vsHdr='<tr style="background:#131a2a;position:sticky;top:0">';
    ['Phase','N','Mean (mV)','\u03c3 (mV)','Median','Cp','Cpk','\u03bc+3\u03c3','Fail>USL','Fail%'].forEach(function(c){
      _vsHdr+='<th style="padding:3px 9px;border:1px solid #1e3050;font-size:0.71rem;color:#8ab4d4;white-space:nowrap">'+c+'</th>';
    });
    _vsHdr+='</tr>';
    var _vsBody='';
    _statsPhaseList.forEach(function(ph){
      // ── Source stats: raw per-die data (live) or PIN_DISTRIB summary (non-live) ──
      var _vm,_vsg,_med,_vn,_nFail,_failPct,_cp,_cpk,usl,_vals=[];
      if(_isLive){
        var rawRows=(RAW_PIN_DATA[pin]||{})[ph]||[];
        rawRows.forEach(function(r){
          if(!filt.lots.has(LOTS[r[0]])||!filt.wfrs.has(r[1])) return;
          if(passKeys&&!passKeys.has(r[0]+'::'+r[1]+'::'+r[2]+'::'+r[3])) return;
          _vals.push(r[4]);
        });
        if(!_vals.length) return;
        var _vs=0,_vs2=0; _vals.forEach(function(v){_vs+=v;_vs2+=v*v;});
        _vn=_vals.length; _vm=_vs/_vn; _vsg=Math.sqrt(Math.max(0,_vs2/_vn-_vm*_vm));
        var _sorted=_vals.slice().sort(function(a,b){return a-b;});
        _med=_sorted[Math.floor(_vn/2)];
        usl=(pd.phases&&pd.phases[ph])?pd.phases[ph].usl:pd.usl;
        _nFail=usl!=null?_vals.filter(function(v){return v>usl;}).length:null;
        _failPct=_nFail!=null?round2(_nFail/_vn*100):null;
        _cp=usl!=null&&_vsg>0?round2((usl-_vm)/(3*_vsg)*2)/2:null;
        _cpk=usl!=null&&_vsg>0?round2((usl-_vm)/(3*_vsg)):null;
      } else {
        var _nlSt=(pd.phases&&pd.phases[ph])||null; if(!_nlSt) return;
        _vn=_nlSt.n_total||0; _vm=_nlSt.mean; _vsg=_nlSt.sigma; _med=_nlSt.median;
        usl=_nlSt.usl!=null?_nlSt.usl:pd.usl;
        _nFail=_nlSt.n_fail||0; _failPct=_vn>0?round2(_nFail/_vn*100):null;
        _cp=_nlSt.cp; _cpk=_nlSt.cpk;
      }
      var _mu3s=round2(_vm+3*_vsg);
      var col=_PHASE_CLR[ph]||'#8ab4d4';
      var _cpkClr=_cpk==null?'#445566':_cpk<1?'#ff6b6b':_cpk<1.33?'#ffd166':'#69f0ae';
      var _failClr=_nFail>0?'#ff6b6b':'#69f0ae';
      _vsBody+='<tr style="border-bottom:1px solid #0d1828">';
      _vsBody+='<td style="padding:3px 9px;font-size:0.72rem;font-weight:700;color:'+col+'">'+ph+'</td>';
      _vsBody+='<td style="padding:3px 9px;font-size:0.72rem">'+_vn+'</td>';
      _vsBody+='<td style="padding:3px 9px;font-size:0.72rem;color:#c0ccd8">'+round2(_vm)+'</td>';
      _vsBody+='<td style="padding:3px 9px;font-size:0.72rem;color:#a78bfa">'+round2(_vsg)+'</td>';
      _vsBody+='<td style="padding:3px 9px;font-size:0.72rem;color:#8ab4d4">'+round2(_med)+'</td>';
      _vsBody+='<td style="padding:3px 9px;font-size:0.72rem;color:#c0ccd8">'+(_cp!=null?_cp:'\u2014')+'</td>';
      _vsBody+='<td style="padding:3px 9px;font-size:0.72rem;color:'+_cpkClr+';font-weight:700">'+(_cpk!=null?_cpk:'\u2014')+'</td>';
      _vsBody+='<td style="padding:3px 9px;font-size:0.72rem;color:#ffd166">'+_mu3s+'</td>';
      _vsBody+='<td style="padding:3px 9px;font-size:0.72rem;color:#ff9999">'+(usl!=null?usl:'\u2014')+'</td>';
      _vsBody+='<td style="padding:3px 9px;font-size:0.72rem;color:'+_failClr+';font-weight:'+((_nFail||0)>0?'700':'400')+'">'+(_nFail!=null?_nFail:'\u2014')+'</td>';
      _vsBody+='<td style="padding:3px 9px;font-size:0.72rem;color:'+_failClr+'">'+(_failPct!=null?_failPct+'%':'\u2014')+'</td>';
      _vsBody+='</tr>';
      // Capture stats for active phase pills
      if(ph===_pdmPhase){
        var _vlN3=0,_vlN6=0,_vlN12=0;
        if(_isLive){
          if(_vsg>0) _vals.forEach(function(v){ var d=Math.abs(v-_vm)/_vsg; if(d>3)_vlN3++; if(d>6)_vlN6++; if(d>12)_vlN12++; });
        } else {
          var _nlStP=(pd.phases&&pd.phases[ph])||{}; _vlN3=_nlStP.n3||0; _vlN6=_nlStP.n6||0; _vlN12=_nlStP.n12||0;
        }
        var _vlLsl=(pd.phases&&pd.phases[ph])?pd.phases[ph].lsl:pd.lsl;
        var _vlCp2=usl!=null&&_vlLsl!=null&&_vsg>0?round2((usl-_vlLsl)/(6*_vsg)):null;
        var _vlCpk2=usl!=null&&_vlLsl!=null&&_vsg>0?round2(Math.min((usl-_vm),((_vm-_vlLsl)))/(3*_vsg)):null;
        var _vlFPct=_nFail!=null&&_vn>0?(_nFail/_vn*100).toFixed(2)+'%':'\u2014';
        var _vlP3=_vn>0?((_vlN3/_vn)*100).toFixed(1)+'%':'?', _vlP6=_vn>0?((_vlN6/_vn)*100).toFixed(1)+'%':'?', _vlP12=_vn>0?((_vlN12/_vn)*100).toFixed(1)+'%':'?';
        var _vlT3=round2(_vm+3*_vsg),_vlT6=round2(_vm+6*_vsg),_vlT12=round2(_vm+12*_vsg);
        var _vlCpkClr=_vlCpk2==null?'#a78bfa':_vlCpk2<1?'#ff6b6b':_vlCpk2<1.33?'#ffd166':'#69f0ae';
        var _vlFClr=(_nFail||0)>0?'#ff8080':'#69f0ae';
        var _vlPs='';
        _vlPs+=_pdStatPill('N total',_vn.toLocaleString(),'#c0deff');
        _vlPs+=_pdStatPill('N fail',(_nFail!=null?_nFail:'\u2014'),_vlFClr);
        _vlPs+=_pdStatPill('%fail',_vlFPct,'#ffd166');
        _vlPs+=_pdStatPill('Mean',round2(_vm)+'mV','#48cae4');
        _vlPs+=_pdStatPill('\u03C3 (sigma)',round2(_vsg)+'mV','#a78bfa');
        if(_vlCp2!=null) _vlPs+=_pdStatPill('Cp',_vlCp2,_vlCp2>=1.33?'#69f0ae':_vlCp2>=1.0?'#ffd166':'#ff6b6b');
        if(_vlCpk2!=null) _vlPs+=_pdStatPill('Cpk',_vlCpk2,_vlCpkClr);
        _vlPs+=_pdStatPill('USL',usl!=null?usl+'mV':'\u2014','#ff9999');
        _vlPs+=_pdStatPill('LSL',_vlLsl!=null?_vlLsl+'mV':'\u2014','#88aaff');
        _vlPs+=_pdStatPill('Median',round2(_med)+'mV','#8ab4d4');
        _vlPs+=_pdStatPill('>3\u03C3',_vlN3.toLocaleString()+'<div style="font-size:0.65rem;color:#997a00;margin-top:1px">&gt;'+_vlT3+'mV &bull; '+_vlP3+'</div>','#ffd166');
        _vlPs+=_pdStatPill('>6\u03C3',_vlN6.toLocaleString()+'<div style="font-size:0.65rem;color:#886000;margin-top:1px">&gt;'+_vlT6+'mV &bull; '+_vlP6+'</div>','#ffaa00');
        _vlPs+=_pdStatPill('>12\u03C3',_vlN12.toLocaleString()+'<div style="font-size:0.65rem;color:#883300;margin-top:1px">&gt;'+_vlT12+'mV &bull; '+_vlP12+'</div>','#ff6600');
        var _vlPillEl=document.getElementById('pdm-xp-vl-pills'); if(_vlPillEl) _vlPillEl.innerHTML=_vlPs;
        // ── Following Phase Failure Preview (per-pin, incremental/cumulative) ──
        var _vlFollowEl=document.getElementById('pdm-xp-vl-following');
        if(_vlFollowEl){
          var _vlNFailCur=_nFail!=null?_nFail:0;
          var _vlFlowOrd=['Pre-Surge','Post-Surge','Stress','SDS-Final','SDT-Start','SDT-Final'];
          var _vlPhIdx=_vlFlowOrd.indexOf(ph);
          var _vlFollowHtml='';
          if(_vlPhIdx>=0&&_vlPhIdx<_vlFlowOrd.length-1){
            var _vlSubPhs=_vlFlowOrd.slice(_vlPhIdx+1);
            var _vlPanRows=[];
            // Non-live: get current phase histogram for bin-centre approximation
            var _nlStCur=!_isLive?(pd.phases&&pd.phases[ph])||null:null;
            var _nlBins=_nlStCur?(_nlStCur.bins||[]):[];
            var _nlCnts=_nlStCur?(_nlStCur.counts||[]):[];
            _vlSubPhs.forEach(function(sPh){
              var sPhData=(pd.phases||{})[sPh];
              if(!sPhData||sPhData.usl==null) return;
              var sUsl=sPhData.usl;
              var nExcVl,nTot;
              if(_isLive){
                nExcVl=_vals.filter(function(v){return v>sUsl;}).length;
                nTot=_vals.length;
              } else {
                // Approximate by summing histogram bins whose centre exceeds sUsl
                nExcVl=0; nTot=_nlStCur?(_nlStCur.n_total||0):0;
                for(var _bi=0;_bi<_nlCnts.length;_bi++){
                  if(_nlBins.length<_bi+2) break;
                  var _bc=(_nlBins[_bi]+_nlBins[_bi+1])/2;
                  if(_bc>sUsl) nExcVl+=_nlCnts[_bi];
                }
              }
              _vlPanRows.push({phase:sPh,nCum:nExcVl,nInc:Math.max(0,nExcVl-_vlNFailCur),usl:round2(sUsl),total:nTot});
            });
            if(_vlPanRows.length){
              var _vlIncId='pdm-vl-inc-cb-'+pin.replace(/[^a-zA-Z0-9]/g,'_');
              function _vlBuildRows(panRows,inc,phClr,nFailC){
                return panRows.map(function(e){
                  var val=inc?e.nInc:e.nCum;
                  var pct=e.total>0?Math.round(val/e.total*100):0;
                  var bw=Math.max(0,Math.min(100,pct));
                  var phc=phClr[e.phase]||'#8ab4d4';
                  var badge=inc&&val===0&&e.nCum>0?'<span style="font-size:0.65rem;color:#4a6a4a;margin-left:3px">(all caught earlier)</span>':'';
                  return '<div style="display:flex;align-items:center;gap:8px;padding:3px 0;border-bottom:1px solid #1a2538">'
                    +'<span style="min-width:80px;font-size:0.72rem;font-weight:700;color:'+phc+'">'+e.phase+'</span>'
                    +'<span style="min-width:80px;font-size:0.70rem;color:#667788;font-family:monospace">USL '+e.usl+' mV</span>'
                    +'<span style="min-width:50px;font-size:0.72rem;font-weight:700;color:'+(val>0?'#ff9966':'#4a6a4a')+'">'+(inc&&val>0?'+':'')+val.toLocaleString()+badge+'</span>'
                    +'<div style="flex:1;background:#1a2538;border-radius:3px;height:6px;overflow:hidden">'
                      +'<div style="height:100%;width:'+bw+'%;background:'+phc+';border-radius:3px;opacity:0.8"></div>'
                    +'</div>'
                    +'<span style="min-width:38px;text-align:right;font-size:0.70rem;color:#667788">'+pct+'%</span>'
                    +'</div>';
                }).join('');
              }
              var _vlPhColP=_PHASE_CLR[ph]||'#48cae4';
              _vlFollowHtml='<div style="width:100%;margin-top:8px;padding:6px 11px;background:#090f1a;border-left:3px solid '+_vlPhColP+';border-radius:0 5px 5px 0">'
                +'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">'
                  +'<div style="flex:1;font-size:0.70rem;font-weight:700;color:'+_vlPhColP+';text-transform:uppercase;letter-spacing:.04em">\u25b6 Following Phase Failure Preview \u2014 '+pin+'</div>'
                  +'<label style="display:inline-flex;align-items:center;gap:4px;cursor:pointer;font-size:0.69rem;color:#8ab4d4;white-space:nowrap">'
                    +'<input type="checkbox" id="'+_vlIncId+'" checked style="accent-color:'+_vlPhColP+';cursor:pointer"> Incremental'
                  +'</label>'
                +'</div>'
                +'<div style="font-size:0.69rem;color:#445566;margin-bottom:4px" id="'+_vlIncId+'_desc">Additional failures beyond the '+_vlNFailCur+' already failing at '+ph+'</div>'
                +'<div id="'+_vlIncId+'_rows">'+_vlBuildRows(_vlPanRows,true,_PHASE_CLR,_vlNFailCur)+'</div>'
                +'</div>'
                +(!_isLive?'<div style="font-size:0.69rem;color:#3a5060;margin-top:3px;padding:2px 4px">&#9432; Counts approximated from histogram bins (50-bin resolution).</div>':'')
                +'<div style="width:100%;margin-top:8px;padding:7px 11px;background:#0a1018;border-left:3px solid #2a4060;border-radius:0 5px 5px 0;font-size:0.72rem;color:#556677;line-height:1.6">'
                +'<span style="color:#8ab4d4;font-weight:700">&#9432; Note: </span>'
                +'<b>N total</b> and <b>N fail</b> count <em>all</em> dies with a measured value for this pin/phase \u2014 including non-BIN8 dies killed earlier in the test flow by unrelated tests (e.g. functional scan, speed). '
                +'Those dies have genuine continuity failures but do not appear in the BIN8 Bin Breakdown since a different test claimed the bin first. '
                +'<b>N fail may therefore exceed the BIN8 Bin Breakdown pin count for this pin.</b>'
                +'</div>';
              setTimeout(function(){
                var cb=document.getElementById(_vlIncId);
                if(!cb) return;
                var rowsEl=document.getElementById(_vlIncId+'_rows');
                var descEl=document.getElementById(_vlIncId+'_desc');
                var _pR=_vlPanRows.slice(),_nF=_vlNFailCur,_pC=_PHASE_CLR,_ph=ph;
                cb.addEventListener('change',function(){
                  var inc=cb.checked;
                  if(rowsEl) rowsEl.innerHTML=_vlBuildRows(_pR,inc,_pC,_nF);
                  if(descEl) descEl.textContent=inc
                    ?'Additional failures beyond the '+_nF+' already failing at '+_ph
                    :'Total measurements exceeding each subsequent phase USL';
                });
              },30);
            }
          }
          _vlFollowEl.innerHTML=_vlFollowHtml;
        }
      }
    });
    vlStatsEl.innerHTML='<table style="border-collapse:collapse;font-size:0.72rem"><thead>'+_vsHdr+'</thead><tbody>'+_vsBody+'</tbody></table>';
  }
  // Delay to ensure the panel is visible and has real pixel dimensions
  var _vlDiv = document.getElementById('pdm-xp-violin-chart');
  if(_vlStatsOnly){ _vlStatsOnly=false; return; }  // stats-only tab switch — skip chart redraw
  _vlDiv.innerHTML = '<div style="color:#556677;padding:20px;font-size:0.8rem">Rendering\u2026</div>';
  setTimeout(function(){
    try {
      Plotly.purge(_vlDiv);
      Plotly.newPlot(_vlDiv, _allTraces, layout, {responsive:true, displayModeBar:false});
      // Sync USL line visibility and auto-range when legend items are toggled
      _vlDiv.on('plotly_restyle', function(){
        var _su=document.getElementById('xp-vl-usl')&&document.getElementById('xp-vl-usl').checked;
        var vis={}; (_vlDiv.data||[]).forEach(function(t,i){vis[i]=t.visible!=='legendonly';});
        var sa=_su?_vlBuildUslShapesAnnots(_vlLastTracePhases,_vlLastPd,vis):{shapes:[],annots:[]};
        var _upd={shapes:sa.shapes,annotations:sa.annots};
        var _ar=!(document.getElementById('xp-vl-autorange'))||document.getElementById('xp-vl-autorange').checked;
        if(_ar) _upd['yaxis.autorange']=true;
        Plotly.relayout(_vlDiv,_upd);
      });
    } catch(e) {
      _vlDiv.innerHTML = '<div style="color:#ff6b6b;padding:12px;font-size:0.78rem">Chart error: '+e.message+'</div>';
    }
  }, 80);
}
function _vlToggleAutoRange(){
  var cb=document.getElementById('xp-vl-autorange');
  var row=document.getElementById('xp-vl-range-row');
  if(row) row.style.display=(cb&&cb.checked)?'none':'flex';
  if(cb&&cb.checked){
    var _vlDiv=document.getElementById('pdm-xp-violin-chart');
    Plotly.relayout(_vlDiv,{'yaxis.autorange':true});
  }
}
function _vlApplyManualRange(){
  var _vlDiv=document.getElementById('pdm-xp-violin-chart');
  var mn=parseFloat(document.getElementById('xp-vl-ymin').value);
  var mx=parseFloat(document.getElementById('xp-vl-ymax').value);
  if(!isNaN(mn)&&!isNaN(mx)&&mx>mn) Plotly.relayout(_vlDiv,{'yaxis.range':[mn,mx],'yaxis.autorange':false});
}
function _vlAutoFillRange(){
  // Read current axis range from chart and fill inputs
  var _vlDiv=document.getElementById('pdm-xp-violin-chart');
  var rng=(_vlDiv&&_vlDiv.layout&&_vlDiv.layout.yaxis&&_vlDiv.layout.yaxis.range)||null;
  if(rng&&rng.length===2){
    document.getElementById('xp-vl-ymin').value=Math.round(rng[0]*100)/100;
    document.getElementById('xp-vl-ymax').value=Math.round(rng[1]*100)/100;
  }
}
function _vlBuildUslShapesAnnots(tracePhases,pd,visMap){
  var shapes=[],annots=[],seen={};
  tracePhases.forEach(function(ph,i){
    if(visMap&&!visMap[i]) return;
    var usl=(pd.phases&&pd.phases[ph])?pd.phases[ph].usl:pd.usl;
    if(usl==null) return;
    var key=usl+'';
    if(!seen[key]) seen[key]={usl:usl,phs:[]};
    seen[key].phs.push(ph);
  });
  Object.keys(seen).forEach(function(k){
    var s=seen[k];
    shapes.push({type:'line',xref:'paper',yref:'y',x0:0,x1:1,y0:s.usl,y1:s.usl,
      line:{color:'#ff4444',width:1.5,dash:'dot'}});
    annots.push({xref:'paper',yref:'y',x:1.01,y:s.usl,
      text:'<b>USL '+s.usl+' mV</b><br>'+s.phs.join(', '),
      xanchor:'left',yanchor:'middle',showarrow:false,
      font:{size:9,color:'#ff8888'},
      bgcolor:'rgba(13,24,40,0.88)',bordercolor:'#553333',borderwidth:1,borderpad:3});
  });
  return {shapes:shapes,annots:annots};
}

// ── Tab: XY Scatter Plot ─────────────────────────────────────────────────────
var _xyForceAutoRange=true;
var _xyCachedXs=[],_xyCachedYs=[],_xyCachedPhX='',_xyCachedPhY='',_xyCachedN=0;
function _xpXyToggleAutoScale(){
  var cb=document.getElementById('xp-xy-autoscale');
  var row=document.getElementById('xp-xy-range-row');
  if(row) row.style.display=(cb&&cb.checked)?'none':'flex';
  if(cb&&cb.checked){ _xyForceAutoRange=true; _xpRenderXyPlot(); }
}
function _xpXyAxisChange(){ _xyForceAutoRange=true; _xpRenderXyPlot(); }
function _xpXyPassChange(){ _xyForceAutoRange=true; _xpRenderXyPlot(); }
function _xpUpdateXyOffset(xs,ys,phX,phY,n){
  // Allow calling with no args (from slider) using cached values
  if(xs!==undefined){ _xyCachedXs=xs; _xyCachedYs=ys; _xyCachedPhX=phX; _xyCachedPhY=phY; _xyCachedN=n; }
  var _xs=_xyCachedXs, _ys=_xyCachedYs, _phX=_xyCachedPhX, _phY=_xyCachedPhY, _n=_xyCachedN;
  if(!_n) return;
  var offEl=document.getElementById('xp-xy-offset-num');
  var offset=offEl?+offEl.value:0;
  var nYgtX=0, nXgtY=0, nWithin=0;
  for(var _i=0;_i<_n;_i++){
    var _d=_ys[_i]-_xs[_i];
    if(_d>offset) nYgtX++;
    else if(_d<-offset) nXgtY++;
    else nWithin++;
  }
  var offStEl=document.getElementById('pdm-xp-xyplot-offset-stats');
  if(!offStEl) return;
  var _offLbl=offset===0?'Y = X':Math.abs(offset)+'mV band';
  offStEl.innerHTML=
    '<div style="font-size:0.71rem;color:#556677;width:100%;margin-bottom:3px">Offset threshold: <b style="color:#69f0ae">'+offset+' mV</b> &mdash; dies where Y−X &gt; threshold or X−Y &gt; threshold</div>'+
    _pdStatPill(_phY+' > '+_phX+' + '+offset+'mV',nYgtX+' ('+Math.round(nYgtX/_n*100)+'%)','#69f0ae')+
    _pdStatPill(_phX+' > '+_phY+' + '+offset+'mV',nXgtY+' ('+Math.round(nXgtY/_n*100)+'%)','#ff9966')+
    _pdStatPill('Within \u00b1'+offset+'mV',nWithin+' ('+Math.round(nWithin/_n*100)+'%)','#8ab4d4');
}
function _xpXyAutoRange(){
  _xyForceAutoRange=true;
  ['xp-xy-xmin','xp-xy-xmax','xp-xy-ymin','xp-xy-ymax'].forEach(function(id){
    var el=document.getElementById(id); if(el) el.value='';
  });
  _xpRenderXyPlot();
}
function _xpRenderXyPlot(){
  var pin=_xpPin; if(!pin) return;
  var phases=_xpPhases(pin);
  if(phases.length<1) return;
  var pd=PIN_DISTRIB[pin]||{};
  // Populate X/Y selects; default X=Post-Surge, Y=SDS-Final
  var selX=document.getElementById('xp-xy-x');
  var selY=document.getElementById('xp-xy-y');
  if(selX&&selX.options.length===0){
    phases.forEach(function(ph){
      selX.innerHTML+='<option value="'+ph+'">'+ph+'</option>';
      selY.innerHTML+='<option value="'+ph+'">'+ph+'</option>';
    });
    var _defX='Post-Surge', _defY='SDS-Final';
    var _xi=phases.indexOf(_defX); var _yi=phases.indexOf(_defY);
    if(_xi>=0) selX.selectedIndex=_xi;
    if(_yi>=0) selY.selectedIndex=_yi;
    else if(_xi>=0&&phases.length>1){ selY.selectedIndex=phases.length-1; }
    _xyForceAutoRange=true;
  }
  var phX=selX?selX.value:phases[0];
  var phY=selY?selY.value:(phases.length>1?phases[1]:phases[0]);
  var showUsl=document.getElementById('xp-xy-usl')&&document.getElementById('xp-xy-usl').checked;
  var showDiag=document.getElementById('xp-xy-diag')&&document.getElementById('xp-xy-diag').checked;
  var logX=document.getElementById('xp-xy-logx')&&document.getElementById('xp-xy-logx').checked;
  var logY=document.getElementById('xp-xy-logy')&&document.getElementById('xp-xy-logy').checked;
  // Pass filter — use innerHTML assignment to avoid option stacking across renders
  var passPhElXy=document.getElementById('xp-xy-passph');
  var passPhXy=passPhElXy?passPhElXy.value:'';
  if(passPhElXy&&passPhElXy.options.length<=1){
    var _ppOpts='<option value="">(none &mdash; all dies)</option>';
    phases.forEach(function(ph){ _ppOpts+='<option value="'+ph+'">Pass '+ph+'</option>'; });
    passPhElXy.innerHTML=_ppOpts;
    var _xyDefIdx=phases.indexOf('Post-Surge');
    if(_xyDefIdx>=0) passPhElXy.selectedIndex=_xyDefIdx+1;
    passPhXy=passPhElXy.value;
  }
  var passUslXy=null;
  var filtXy=_pdmFocusFilter||_getPdmFilter();
  if(passPhXy){
    passUslXy=(pd.phases&&pd.phases[passPhXy]&&pd.phases[passPhXy].usl!=null)?pd.phases[passPhXy].usl:pd.usl;
  }
  // Build per-die index and collect (x,y) pairs — use lot-agnostic key for cross-phase matching
  var idx=_xpBuildIndexWxy(pin);
  // Count passing dies for info label
  var _xyPassCount=0;
  if(passPhXy){ Object.values(idx).forEach(function(r){ if(r[passPhXy]!=null&&(passUslXy==null||r[passPhXy]<=passUslXy)) _xyPassCount++; }); }
  var infoElXy=document.getElementById('xp-xy-pass-info');
  if(infoElXy) infoElXy.textContent=passPhXy?_xyPassCount+' dies pass '+passPhXy:'';
  var xs=[],ys=[],txts=[],clrs=[],dieKeys=[];
  var uslX=(pd.phases&&pd.phases[phX])?pd.phases[phX].usl:pd.usl;
  var uslY=(pd.phases&&pd.phases[phY])?pd.phases[phY].usl:pd.usl;
  Object.entries(idx).forEach(function(e){
    var k=e[0],r=e[1];
    // Pass filter: die must have pass-phase value and it must be ≤ USL
    if(passPhXy&&(r[passPhXy]==null||(passUslXy!=null&&r[passPhXy]>passUslXy))) return;
    if(r[phX]==null||r[phY]==null) return;
    var failX=uslX!=null&&r[phX]>uslX;
    var failY=uslY!=null&&r[phY]>uslY;
    xs.push(r[phX]); ys.push(r[phY]); dieKeys.push(k);
    var lot=LOTS[r.li]||('L'+r.li);
    txts.push(lot+' W'+String(r.wfr).padStart(2,'0')+' ('+r.x+','+r.y+')<br>'+phX+': '+r[phX]+'mV<br>'+phY+': '+r[phY]+'mV');
    clrs.push(failX&&failY?'#ff4444':failX?'#ff9800':failY?'#ffd166':'#48cae4');
  });
  if(!xs.length){
    // ── Diagnostic: find best overlapping pair and suggest / auto-select ──
    var _diagEl=document.getElementById('pdm-xp-xyplot-chart');
    var _allPh=_xpPhases(pin);
    var _bestN=0, _bestA=null, _bestB=null;
    var _phaseCounts={};
    _allPh.forEach(function(pa){
      var _na=0;
      Object.values(idx).forEach(function(r){ if(r[pa]!=null) _na++; });
      _phaseCounts[pa]=_na;
      _allPh.forEach(function(pb){
        if(pb===pa) return;
        var _nov=0;
        Object.values(idx).forEach(function(r){ if(r[pa]!=null&&r[pb]!=null) _nov++; });
        if(_nov>_bestN){ _bestN=_nov; _bestA=pa; _bestB=pb; }
      });
    });
    // If a valid pair found, auto-select it and re-render
    if(_bestA&&_bestB&&_bestN>0){
      var sX=document.getElementById('xp-xy-x'), sY=document.getElementById('xp-xy-y');
      if(sX) for(var _i=0;_i<sX.options.length;_i++) if(sX.options[_i].value===_bestA){sX.selectedIndex=_i;break;}
      if(sY) for(var _j=0;_j<sY.options.length;_j++) if(sY.options[_j].value===_bestB){sY.selectedIndex=_j;break;}
      _xyForceAutoRange=true;
      _xpRenderXyPlot(); return;
    }
    // No valid pair at all — show diagnostic
    var _phInfo=_allPh.map(function(ph){
      return '<b style="color:'+(_PHASE_CLR[ph]||'#8ab4d4')+'">'+ph+'</b>: '+(_phaseCounts[ph]||0)+' dies';
    }).join(' &nbsp;|&nbsp; ');
    _diagEl.innerHTML='<div style="padding:14px;font-size:0.76rem;color:#8ab4d4">'
      +'<div style="color:#ffd166;margin-bottom:8px">&#9888; No dies measured at both <b>'+phX+'</b> and <b>'+phY+'</b> in the current filter.</div>'
      +'<div style="margin-bottom:6px;color:#556677">The XY plot requires one die to appear in <i>both</i> selected phases (same Lot/W#/X/Y). '
      +'This typically means different lots or wafers were measured at each phase.</div>'
      +'<div style="font-size:0.72rem;color:#445566">Dies per phase (current filter):<br>'+_phInfo+'</div>'
      +(idx&&Object.keys(idx).length===0?'<div style="margin-top:6px;color:#445566;font-size:0.71rem">&#9888; Live Mode filter may be empty &mdash; try selecting lots/wafers in the focus panel.</div>':'')
      +'</div>';
    return;
  }
  var n=xs.length;
  // ── Stats helpers ──────────────────────────────────────────────────────────
  function _statOf(arr){
    var _n=arr.length, _s=0, _s2=0;
    arr.forEach(function(v){_s+=v;_s2+=v*v;});
    var _m=_s/_n, _sg=Math.sqrt(Math.max(0,_s2/_n-_m*_m));
    return {mean:_m,sigma:_sg,n:_n};
  }
  var stX=_statOf(xs), stY=_statOf(ys);
  // ── Auto-range ─────────────────────────────────────────────────────────────
  // Auto-range: always when checkbox is checked; also on first render or forced
  var _autoScale=!(document.getElementById('xp-xy-autoscale'))||document.getElementById('xp-xy-autoscale').checked;
  var xMinEl=document.getElementById('xp-xy-xmin'), xMaxEl=document.getElementById('xp-xy-xmax');
  var yMinEl=document.getElementById('xp-xy-ymin'), yMaxEl=document.getElementById('xp-xy-ymax');
  function _pad(v,dir){ var pad=(stX.sigma||Math.abs(v)*0.02||1); return dir<0?v-pad*0.5:v+pad*0.5; }
  function _padY(v,dir){ var pad=(stY.sigma||Math.abs(v)*0.02||1); return dir<0?v-pad*0.5:v+pad*0.5; }
  if(_autoScale||_xyForceAutoRange){
    // Use union of X and Y ranges so both axes share the same scale
    var _xMin=Math.min.apply(null,xs), _xMax=Math.max.apply(null,xs);
    var _yMin=Math.min.apply(null,ys), _yMax=Math.max.apply(null,ys);
    var _uMin=Math.min(_xMin,_yMin), _uMax=Math.max(_xMax,_yMax);
    if(xMinEl) xMinEl.value=Math.round(_uMin*100)/100;
    if(xMaxEl) xMaxEl.value=Math.round(_uMax*100)/100;
    if(yMinEl) yMinEl.value=Math.round(_uMin*100)/100;
    if(yMaxEl) yMaxEl.value=Math.round(_uMax*100)/100;
    // Default offset threshold = 1σ of Y axis (4 decimal precision preserves small values)
    var _defOff=stY.sigma>0?Math.round(stY.sigma*10000)/10000:0;
    var _offNum=document.getElementById('xp-xy-offset-num');
    var _offRng=document.getElementById('xp-xy-offset');
    if(_offNum) _offNum.value=_defOff;
    if(_offRng) _offRng.value=Math.max(-500,Math.min(500,_defOff));
    _xyForceAutoRange=false;
  }
  // In auto-scale mode, always read back the just-computed values; in manual mode use inputs
  var rXmin=_autoScale?(xMinEl?+xMinEl.value:null):(xMinEl&&xMinEl.value!==''?+xMinEl.value:null);
  var rXmax=_autoScale?(xMaxEl?+xMaxEl.value:null):(xMaxEl&&xMaxEl.value!==''?+xMaxEl.value:null);
  var rYmin=_autoScale?(yMinEl?+yMinEl.value:null):(yMinEl&&yMinEl.value!==''?+yMinEl.value:null);
  var rYmax=_autoScale?(yMaxEl?+yMaxEl.value:null):(yMaxEl&&yMaxEl.value!==''?+yMaxEl.value:null);
  // ── Shapes & annotations ───────────────────────────────────────────────────
  var shapes=[], annots=[];
  if(showUsl){
    if(uslX!=null){
      shapes.push({type:'line',x0:uslX,x1:uslX,y0:0,y1:1,yref:'paper',line:{color:'#ff4444',width:1.5,dash:'dash'}});
      annots.push({x:uslX,y:0.98,xref:'x',yref:'paper',text:'<b>USL-X</b><br>'+uslX,showarrow:false,xanchor:'left',yanchor:'top',font:{size:10,color:'#ff8888'},bgcolor:'rgba(7,17,26,0.7)',borderpad:2});
    }
    if(uslY!=null){
      shapes.push({type:'line',x0:0,x1:1,xref:'paper',y0:uslY,y1:uslY,line:{color:'#ff6b6b',width:1.5,dash:'dash'}});
      annots.push({x:0.02,y:uslY,xref:'paper',yref:'y',text:'<b>USL-Y '+uslY+'</b>',showarrow:false,xanchor:'left',yanchor:'bottom',font:{size:10,color:'#ff8888'},bgcolor:'rgba(7,17,26,0.7)',borderpad:2});
    }
  }
  if(showDiag){
    var _dMin=Math.min(rXmin!=null?rXmin:Math.min.apply(null,xs),rYmin!=null?rYmin:Math.min.apply(null,ys));
    var _dMax=Math.max(rXmax!=null?rXmax:Math.max.apply(null,xs),rYmax!=null?rYmax:Math.max.apply(null,ys));
    shapes.push({type:'line',x0:_dMin,x1:_dMax,y0:_dMin,y1:_dMax,line:{color:'rgba(255,209,102,0.45)',width:1.5,dash:'dot'}});
  }
  // ── Mean/sigma annotation lines on chart ──────────────────────────────────
  var showSigmaLines=document.getElementById('xp-xy-sigma')&&document.getElementById('xp-xy-sigma').checked;
  var _SIGCOLS={1:'rgba(100,200,255,0.55)',2:'rgba(100,200,255,0.65)',3:'rgba(255,200,80,0.7)',6:'rgba(255,130,60,0.75)',12:'rgba(255,68,68,0.8)'};
  if(showSigmaLines){
    [1,2,3,6,12].forEach(function(k){
      var col=_SIGCOLS[k]||'#fff';
      var xv=round2(stX.mean+k*stX.sigma);
      var yv=round2(stY.mean+k*stY.sigma);
      var nxOut=xs.filter(function(v){return v>xv;}).length;
      var nyOut=ys.filter(function(v){return v>yv;}).length;
      if(stX.sigma>0){
        shapes.push({type:'line',x0:xv,x1:xv,y0:0,y1:1,yref:'paper',line:{color:col,width:1.5,dash:'dot'}});
        annots.push({x:xv,y:0.01,xref:'x',yref:'paper',
          text:'\u03bc+'+k+'\u03c3<br>'+xv+'mV',
          showarrow:false,xanchor:'left',yanchor:'bottom',
          font:{size:9,color:col},bgcolor:'rgba(7,17,26,0.75)',borderpad:2,
          hovertext:'X: \u03bc+'+k+'\u03c3 = '+xv+' mV | '+nxOut+' dies ('+Math.round(nxOut/n*100)+'%) exceed'});
      }
      if(stY.sigma>0){
        shapes.push({type:'line',x0:0,x1:1,xref:'paper',y0:yv,y1:yv,line:{color:col,width:1.5,dash:'dot'}});
        annots.push({x:0.01,y:yv,xref:'paper',yref:'y',
          text:'\u03bc+'+k+'\u03c3 '+yv+'mV',
          showarrow:false,xanchor:'left',yanchor:'bottom',
          font:{size:9,color:col},bgcolor:'rgba(7,17,26,0.75)',borderpad:2,
          hovertext:'Y: \u03bc+'+k+'\u03c3 = '+yv+' mV | '+nyOut+' dies ('+Math.round(nyOut/n*100)+'%) exceed'});
      }
    });
  }
  // ── Stat pills ─────────────────────────────────────────────────────────────
  var nFailX=uslX!=null?xs.filter(function(v){return v>uslX;}).length:null;
  var nFailY=uslY!=null?ys.filter(function(v){return v>uslY;}).length:null;
  var nBoth=uslX!=null&&uslY!=null?xs.filter(function(v,i){return v>uslX&&ys[i]>uslY;}).length:null;
  var statsEl=document.getElementById('pdm-xp-xyplot-stats');
  if(statsEl){
    var pills=_pdStatPill('N dies',n,'#c0deff')
      +_pdStatPill('\u03bc X',round2(stX.mean)+' mV','#48cae4')
      +_pdStatPill('\u03c3 X',round2(stX.sigma)+' mV','#a78bfa')
      +_pdStatPill('\u03bc Y',round2(stY.mean)+' mV','#69f0ae')
      +_pdStatPill('\u03c3 Y',round2(stY.sigma)+' mV','#c77dff');
    if(nFailX!=null) pills+=_pdStatPill('Fail '+phX,nFailX+' ('+Math.round(nFailX/n*100)+'%)','#ff9800');
    if(nFailY!=null) pills+=_pdStatPill('Fail '+phY,nFailY+' ('+Math.round(nFailY/n*100)+'%)','#ffd166');
    if(nBoth!=null) pills+=_pdStatPill('Fail Both',nBoth+' ('+Math.round(nBoth/n*100)+'%)','#ff4444');
    statsEl.innerHTML=pills;
  }
  // ── Y−X offset stats (μ+Nσ slider) ───────────────────────────────────────────
  _xpUpdateXyOffset(xs, ys, phX, phY, n);
  // ── Sigma outlier table ────────────────────────────────────────────────────
  var sigmaEl=document.getElementById('pdm-xp-xyplot-sigma');
  if(sigmaEl){
    var _SKLEVS=[1,2,3,6,12];
    var _SKLCL=['#64c8ff','#64c8ff','#ffd166','#ff9800','#ff4444'];
    var clrX2=_PHASE_CLR[phX]||'#48cae4', clrY2=_PHASE_CLR[phY]||'#69f0ae';
    function _fmt4(v){ return v!=null?(Math.round(v*10000)/10000):'\u2014'; }
    // Distribution summary cards
    var distHdr='<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:7px">';
    distHdr+='<div style="background:#0d1828;border-left:3px solid '+clrX2+';border-radius:0 4px 4px 0;padding:4px 10px;font-size:0.72rem">';
    distHdr+='<span style="font-weight:700;color:'+clrX2+'">X \u2014 '+phX+'</span><br>';
    distHdr+='<span style="color:#8ab4d4">&mu; = <b>'+_fmt4(stX.mean)+'</b> mV</span>&nbsp;&nbsp;';
    distHdr+='<span style="color:#a78bfa">&sigma; = <b>'+_fmt4(stX.sigma)+'</b> mV</span>&nbsp;&nbsp;';
    distHdr+='<span style="color:#445566">N = '+n+'</span></div>';
    distHdr+='<div style="background:#0d1828;border-left:3px solid '+clrY2+';border-radius:0 4px 4px 0;padding:4px 10px;font-size:0.72rem">';
    distHdr+='<span style="font-weight:700;color:'+clrY2+'">Y \u2014 '+phY+'</span><br>';
    distHdr+='<span style="color:#8ab4d4">&mu; = <b>'+_fmt4(stY.mean)+'</b> mV</span>&nbsp;&nbsp;';
    distHdr+='<span style="color:#a78bfa">&sigma; = <b>'+_fmt4(stY.sigma)+'</b> mV</span>&nbsp;&nbsp;';
    distHdr+='<span style="color:#445566">N = '+n+'</span></div>';
    distHdr+='<div style="background:#0d1828;border-left:3px solid #556677;border-radius:0 4px 4px 0;padding:4px 10px;font-size:0.71rem;color:#556677">';
    distHdr+='Offset default = 1&sigma;(Y) = <b style="color:#69f0ae">'+_fmt4(stY.sigma)+'</b> mV</div>';
    distHdr+='</div>';
    var shdr='<tr style="background:#0d1828;position:sticky;top:0">';
    ['\u03c3 level',
     'X ('+phX+'): \u03bc+N\u03c3','X beyond','X %',
     'Y ('+phY+'): \u03bc+N\u03c3','Y beyond','Y %',
     'Both exceed'].forEach(function(c){
      shdr+='<th style="padding:3px 9px;border:1px solid #1e3050;font-size:0.71rem;color:#8ab4d4;white-space:nowrap">'+c+'</th>';
    });
    shdr+='</tr>';
    var sbody='';
    _SKLEVS.forEach(function(k,ki){
      var col=_SKLCL[ki];
      var xThr=Math.round((stX.mean+k*stX.sigma)*10000)/10000;
      var yThr=Math.round((stY.mean+k*stY.sigma)*10000)/10000;
      var nxOut=xs.filter(function(v){return v>xThr;}).length;
      var nyOut=ys.filter(function(v){return v>yThr;}).length;
      var nbOut=xs.filter(function(v,i){return v>xThr&&ys[i]>yThr;}).length;
      var xPct=round2(nxOut/n*100), yPct=round2(nyOut/n*100);
      var xcl=nxOut>0?col:'#445566', ycl=nyOut>0?col:'#445566';
      sbody+='<tr style="border-bottom:1px solid #0a1520">';
      sbody+='<td style="padding:3px 9px;font-size:0.72rem;font-weight:700;color:'+col+'">&mu;+'+k+'&sigma;</td>';
      sbody+='<td style="padding:3px 9px;font-size:0.72rem;color:'+clrX2+'">'+xThr+'</td>';
      sbody+='<td style="padding:3px 9px;font-size:0.72rem;color:'+xcl+';font-weight:'+(nxOut>0?'700':'400')+'">'+nxOut+'</td>';
      sbody+='<td style="padding:3px 9px;font-size:0.72rem;color:'+xcl+'">'+xPct+'%</td>';
      sbody+='<td style="padding:3px 9px;font-size:0.72rem;color:'+clrY2+'">'+yThr+'</td>';
      sbody+='<td style="padding:3px 9px;font-size:0.72rem;color:'+ycl+';font-weight:'+(nyOut>0?'700':'400')+'">'+nyOut+'</td>';
      sbody+='<td style="padding:3px 9px;font-size:0.72rem;color:'+ycl+'">'+yPct+'%</td>';
      sbody+='<td style="padding:3px 9px;font-size:0.72rem;color:'+(nbOut>0?col:'#445566')+';font-weight:'+(nbOut>0?'700':'400')+'">'+nbOut+'</td>';
      sbody+='</tr>';
    });
    sigmaEl.innerHTML=distHdr
      +'<table style="border-collapse:collapse;font-size:0.72rem"><thead>'+shdr+'</thead><tbody>'+sbody+'</tbody></table>';
  }
  // ── Plot ───────────────────────────────────────────────────────────────────
  var clrX=_PHASE_CLR[phX]||'#48cae4';
  var clrY=_PHASE_CLR[phY]||'#69f0ae';
  var xaxisCfg={title:phX+' (mV)',gridcolor:'#1e3050',zeroline:false,color:clrX,type:logX?'log':'linear'};
  var yaxisCfg={title:phY+' (mV)',gridcolor:'#1e3050',zeroline:false,color:clrY,type:logY?'log':'linear'};
  if(rXmin!=null||rXmax!=null){ xaxisCfg.range=[rXmin!=null?rXmin:null,rXmax!=null?rXmax:null]; }
  if(rYmin!=null||rYmax!=null){ yaxisCfg.range=[rYmin!=null?rYmin:null,rYmax!=null?rYmax:null]; }
  var useDensity=document.getElementById('xp-xy-density')&&document.getElementById('xp-xy-density').checked;
  var markerCfg, densData=null;
  if(useDensity){
    // KDE-approximated density: count neighbours within ~2% of axis range
    var xSpan=Math.max.apply(null,xs)-Math.min.apply(null,xs)||1;
    var ySpan=Math.max.apply(null,ys)-Math.min.apply(null,ys)||1;
    var rx=xSpan*0.04, ry=ySpan*0.04;
    var dens=xs.map(function(_,i){
      var cnt=0;
      for(var j=0;j<xs.length;j++){
        if(Math.abs(xs[j]-xs[i])<=rx&&Math.abs(ys[j]-ys[i])<=ry) cnt++;
      }
      return cnt;
    });
    densData=dens;
    markerCfg={color:dens,colorscale:'Plasma',showscale:true,reversescale:false,
      colorbar:{thickness:10,len:0.6,x:1.02,title:{text:'Density',font:{size:9,color:'#8ab4d4'}},
        tickfont:{size:8,color:'#556677'},bgcolor:'rgba(0,0,0,0)',bordercolor:'rgba(0,0,0,0)'},
      size:5,opacity:0.85,line:{width:0}};
  } else {
    markerCfg={color:clrs,size:5,opacity:0.75,line:{width:0}};
  }
  Plotly.react('pdm-xp-xyplot-chart',[{
    x:xs,y:ys,mode:'markers',type:'scatter',
    marker:markerCfg,
    text:txts,customdata:densData,
    hovertemplate:densData?'%{text}<br>\u007e%{customdata} nearby units<extra></extra>':'%{text}<extra></extra>',
    name:phX+' vs '+phY,
    cliponaxis:false
  }],Object.assign(L({
    xaxis:xaxisCfg,
    yaxis:yaxisCfg,
    shapes:shapes,annotations:annots,
    legend:{orientation:'h',y:1.08},
    hovermode:'closest',
    margin:{t:20,l:60,r:15,b:55}
  }),{}),{responsive:true,displayModeBar:true});
}

// ── Die Detail Modal helpers ────────────────────────────────────────────
function _ddiOpen(){
  // Enforce one-window: close pin-dist-modal if open
  if(typeof _pdmOpen!=='undefined'&&_pdmOpen) closePinDist();
  document.getElementById('die-detail-modal').style.display='block';
}
function _ddiClose(){
  document.getElementById('die-detail-modal').style.display='none';
  var cd=document.getElementById('ddi-chart');
  if(cd){try{Plotly.purge(cd);}catch(e){} cd.innerHTML='';}
  _wmDieCurPin=null;
}
function _ddiTab(tab){
  document.getElementById('ddi-panel-info').style.display=tab==='info'?'block':'none';
  document.getElementById('ddi-panel-chart').style.display=tab==='chart'?'block':'none';
  var ti=document.getElementById('ddi-tab-info'),tc=document.getElementById('ddi-tab-chart');
  ti.style.background=tab==='info'?'#1e3a5f':'#0d1828'; ti.style.color=tab==='info'?'#8ab4d4':'#445566';
  ti.style.border=tab==='info'?'1px solid #2a5080':'1px solid #1e3050'; ti.style.fontWeight=tab==='info'?'700':'400';
  tc.style.background=tab==='chart'?'#0d2540':'#0d1828'; tc.style.color=tab==='chart'?'#4ecdc4':'#445566';
  tc.style.border=tab==='chart'?'1px solid #2a5080':'1px solid #1e3050'; tc.style.fontWeight=tab==='chart'?'700':'400';
}
(function(){
  // Drag-to-move for die detail modal
  var box=document.getElementById('die-detail-box');
  var hdr=document.getElementById('die-detail-hdr');
  var mx=0,my=0;
  hdr.addEventListener('mousedown',function(e){
    if(e.target.tagName==='BUTTON') return;
    e.preventDefault(); mx=e.clientX; my=e.clientY;
    function mv(e){
      var dx=e.clientX-mx,dy=e.clientY-my; mx=e.clientX; my=e.clientY;
      box.style.transform='none';
      box.style.left=Math.max(0,box.offsetLeft+dx)+'px';
      box.style.top=Math.max(0,box.offsetTop+dy)+'px';
    }
    function up(){ document.removeEventListener('mousemove',mv); document.removeEventListener('mouseup',up); }
    document.addEventListener('mousemove',mv); document.addEventListener('mouseup',up);
  });
})();

(function(){
  // Drag-to-move for pin distribution modal
  var box=document.getElementById('pdm-box');
  var hdr=document.getElementById('pdm-hdr');
  var mx=0,my=0;
  hdr.addEventListener('mousedown',function(e){
    if(e.target.tagName==='BUTTON'||e.target.tagName==='INPUT'||e.target.tagName==='LABEL') return;
    e.preventDefault(); mx=e.clientX; my=e.clientY;
    function mv(e){
      var dx=e.clientX-mx,dy=e.clientY-my; mx=e.clientX; my=e.clientY;
      box.style.transform='none';
      box.style.left=Math.max(0,box.offsetLeft+dx)+'px';
      box.style.top=Math.max(0,box.offsetTop+dy)+'px';
    }
    function up(){ document.removeEventListener('mousemove',mv); document.removeEventListener('mouseup',up); }
    document.addEventListener('mousemove',mv); document.addEventListener('mouseup',up);
  });
})();

// ── Inline die-detail pin chart ────────────────────────────────────────────
var _wmDieCurPin=null;
function _wmPinChart(pin){
  _wmDieCurPin=pin;
  var pd=PIN_DISTRIB[pin]; if(!pd) return;
  _ddiTab('chart');
  document.getElementById('ddi-chart-pin').textContent=pin;
  var chartDiv=document.getElementById('ddi-chart');
  if(!chartDiv) return;
  // Check Live Mode data
  var phases=pd.phase_list||Object.keys(pd.phases||{});
  var hasRaw=RAW_PIN_DATA&&phases.some(function(ph){return((RAW_PIN_DATA[pin]||{})[ph]||[]).length>0;});
  if(!hasRaw){
    chartDiv.innerHTML='<div style="color:#445566;padding:20px 16px;font-size:0.8rem">&#9888; Live Mode required for inline chart.<br><button onclick="_ddiClose();showPinDist(\''+pin+'\')" style="margin-top:8px;background:#0d2540;border:1px solid #2a5080;color:#4ecdc4;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:0.78rem">Open full inspector &rarr;</button></div>';
    return;
  }
  var filt=_pdmFocusFilter||_getPdmFilter();
  var traces=[],tracePhases=[];
  phases.forEach(function(ph){
    var rawRows=(RAW_PIN_DATA[pin]||{})[ph]||[];
    var vals=[];
    rawRows.forEach(function(r){
      if(filt.lots.has(LOTS[r[0]])&&filt.wfrs.has(r[1])) vals.push(r[4]);
    });
    if(!vals.length) return;
    var col=_PHASE_CLR[ph]||'#8ab4d4';
    var usl=(pd.phases&&pd.phases[ph])?pd.phases[ph].usl:pd.usl;
    var nFail=usl!=null?vals.filter(function(v){return v>usl;}).length:0;
    traces.push({type:'box',y:vals,name:ph+(nFail>0?' ('+nFail+' fail)':''),
      boxpoints:'outliers',jitter:0.4,pointpos:0,
      line:{color:col,width:1.5},fillcolor:col+'33',
      marker:{color:col,size:3,opacity:0.6},whiskerwidth:0.5});
    tracePhases.push(ph);
  });
  if(!traces.length){chartDiv.innerHTML='<div style="color:#445566;padding:20px;font-size:0.8rem">No data after filter.</div>';return;}
  // USL hover traces
  var uslHov=[];
  var _sh={};
  tracePhases.forEach(function(ph,i){
    var usl=(pd.phases&&pd.phases[ph])?pd.phases[ph].usl:pd.usl; if(usl==null) return;
    if(!_sh[usl]) _sh[usl]={usl:usl,names:[],phs:[]};
    _sh[usl].names.push(traces[i].name); _sh[usl].phs.push(ph);
  });
  Object.keys(_sh).forEach(function(k){
    var h=_sh[k];
    uslHov.push({type:'scatter',mode:'lines',x:h.names,y:h.names.map(function(){return h.usl;}),
      line:{color:'rgba(255,68,68,0.01)',width:16},
      hovertemplate:'<b>USL: '+h.usl+' mV</b><br>'+h.phs.join(', ')+'<extra>USL</extra>',
      showlegend:false});
  });
  var sa=_vlBuildUslShapesAnnots(tracePhases,pd,null);
  var layout=L({
    yaxis:{title:'mV',gridcolor:'#1e3050',zeroline:false},
    xaxis:{gridcolor:'#1e3050'},
    shapes:sa.shapes,annotations:sa.annots,
    boxmode:'group',
    legend:{orientation:'h',y:1.1,font:{size:10}},
    margin:{t:10,l:48,r:120,b:40},
    paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'#080e18'
  });
  try{
    Plotly.purge(chartDiv);
    Plotly.newPlot(chartDiv,traces.concat(uslHov),layout,{responsive:true,displayModeBar:false});
  }catch(e){
    chartDiv.innerHTML='<div style="color:#ff6b6b;padding:10px;font-size:0.78rem">'+e.message+'</div>';
  }
}
function _wmDieCloseChart(){
  _ddiTab('info');
  var cd=document.getElementById('ddi-chart');
  if(cd){try{Plotly.purge(cd);}catch(e){} cd.innerHTML='';}
  _wmDieCurPin=null;
}

// ── Pin picker GUI ─────────────────────────────────────────────────────────────
var _pdPickerOpen=false;
function togglePinPicker(){
  _pdPickerOpen=!_pdPickerOpen;
  document.getElementById('pin-dist-picker').style.display=_pdPickerOpen?'block':'none';
}
function _pdpInspect(){
  var sel=document.getElementById('pdp-sel');
  if(sel.value) showPinDist(sel.value);
}
(function(){
  // Defer until all const declarations are initialized
  setTimeout(function(){
  var sel=document.getElementById('pdp-sel');
  var msel=document.getElementById('pdm-pin-sel');
  var detailSet=new Set(Object.keys(DETAIL_PINS));
  // Sort: detail pins first (by fail count), then alpha
  var pins=Object.keys(PIN_DISTRIB);
  var failCounts={}; PIN_LIST.forEach(function(p){failCounts[p.pin]=p.count;});
  pins.sort(function(a,b){
    var ad=detailSet.has(a)?1:0, bd=detailSet.has(b)?1:0;
    if(ad!==bd) return bd-ad;  // detail pins first
    var af=failCounts[a]||0, bf=failCounts[b]||0;
    if(af!==bf) return bf-af;  // then by fail count
    return a<b?-1:a>b?1:0;
  });
  var sep=false;
  pins.forEach(function(p){
    if(!detailSet.has(p)&&!sep){
      var optSep=document.createElement('option');optSep.disabled=true;optSep.text='──────────────────────';
      sel.appendChild(optSep);
      var optSep2=document.createElement('option');optSep2.disabled=true;optSep2.text='──────────────────────';
      msel.appendChild(optSep2); sep=true;
    }
    var opt=document.createElement('option');
    opt.value=p;
    var fc=failCounts[p]?(' ('+failCounts[p]+' fail)'):'';
    var star=detailSet.has(p)?'\u2605 ':'';
    opt.text=star+p+fc;
    if(detailSet.has(p)) opt.style.color='#ffd166';
    sel.appendChild(opt);
    var opt2=opt.cloneNode(true); msel.appendChild(opt2);
  });
  // Auto-select first detail pin
  var firstDetail=pins.find(function(p){return detailSet.has(p);});
  if(firstDetail) sel.value=firstDetail;
  },0);
})();

const DIES       = __DIES__;
const ALL_MAP    = __ALL_MAP__;
const WFR_RADIUS = __WFR_RADIUS__;
const FB_LIST    = __FB_LIST__;
const WFR_LIST   = __WFR_LIST__;
const KILL_LIST  = __KILL_LIST__;
const PIN_LIST   = __PIN_LIST__;
const PIN_DISTRIB = __PIN_DISTRIB__;
const DETAIL_PINS = __DETAIL_PINS__;
const RAW_PIN_DATA = __RAW_PIN_DATA__;
const FOCUS_WAFER_COUNT = __FOCUS_WAFER_COUNT__;
const RAIL_LIST  = __RAIL_LIST__;
const FLOW_DATA  = __FLOW_DATA__;
const SURGE_DATA = __SURGE_DATA__;
const EDC_DATA   = __EDC_DATA__;
const RAIL_COND_DATA = __RAIL_COND_DATA__;
const TARGET_IBIN  = __TARGET_IBIN__;
const TARGET_IBINS = new Set([8,80,89]);
const BIN8_COUNT  = __BIN8_COUNT__;
const LOTS  = __LOTS_JS__;
const PROGS = __PROGS_JS__;
const RETICLE_MAP = __RETICLE_MAP__;
const DRS_LIST    = __DRS_LIST__;

// Set live-mode flag now that FOCUS_WAFER_COUNT is defined
_focusModeActive = FOCUS_WAFER_COUNT > 0;

// ── Wafer map geometry (computed once from ALL_MAP + RETICLE_MAP) ──────────
var _wmXMin,_wmXMax,_wmYMin,_wmYMax;
(function(){
  // Use reduce (not Math.min.apply) — apply crashes when xs.length > ~65K (many wafers × many dies)
  var xMin=Infinity,xMax=-Infinity,yMin=Infinity,yMax=-Infinity;
  Object.values(ALL_MAP).forEach(function(dies){dies.forEach(function(d){
    if(d[0]<xMin)xMin=d[0];if(d[0]>xMax)xMax=d[0];
    if(d[1]<yMin)yMin=d[1];if(d[1]>yMax)yMax=d[1];
  });});
  _wmXMin=xMin;_wmXMax=xMax;_wmYMin=yMin;_wmYMax=yMax;
})();
// Reticle shot bounding boxes: [shotIdx, x0, y0, x1, y1] — one box per unique shot (grouped by shotIdx)
var _wmRetShots=(function(){
  var keys=Object.keys(RETICLE_MAP);if(!keys.length)return[];
  var sb={};
  keys.forEach(function(k){
    var p=k.split(','),sx=+p[0],sy=+p[1],si=RETICLE_MAP[k][2];  // si = shotIdx
    if(!sb[si]){sb[si]={x0:sx,y0:sy,x1:sx,y1:sy};}
    var b=sb[si];
    if(sx<b.x0)b.x0=sx;if(sx>b.x1)b.x1=sx;
    if(sy<b.y0)b.y0=sy;if(sy>b.y1)b.y1=sy;
  });
  // sort by shotIdx so _wmRetShots[si][0]===si and wmRender label si+1 matches
  return Object.entries(sb).sort(function(a,b){return +a[0]-(+b[0]);}).map(function(e){var si=+e[0],b=e[1];return[si,b.x0,b.y0,b.x1,b.y1];});
  // each entry: [shotIdx, x0, y0, x1, y1] (integer die coordinates)
})();
// Reticle site totals — unique shot count per site, for _wmScoreReticle (wafer_pattern module API)
var RETICLE_SITE_TOTALS=(function(){
  var ts={};
  Object.values(RETICLE_MAP).forEach(function(v){
    var sk=v[0]+','+v[1];
    if(!ts[sk])ts[sk]=new Set();
    ts[sk].add(v[2]);
  });
  var out={};Object.keys(ts).forEach(function(k){out[k]=ts[k].size;});
  return out;
})();
// Reticle site numbers — {"rdx,rdy": die-loc-number}
// rdx = Reticle integer (intra-shot die position), rdy = 0
// Sorted ascending Y (bottom-row first), ascending X — spec Option B
// For NVL816: keys are "1,0".."N,0" so sort by x ascending → site num equals Reticle value
var RETICLE_SITE_NUM=(function(){
  var sites={};
  Object.values(RETICLE_MAP).forEach(function(v){sites[v[0]+','+v[1]]=true;});
  var sorted=Object.keys(sites).sort(function(a,b){
    var ap=a.split(','),bp=b.split(',');
    var dy=(+ap[1])-(+bp[1]);  // ascending Y (bottom-row first, per spec)
    return dy!==0?dy:(+ap[0])-(+bp[0]);  // then left to right
  });
  var out={};
  sorted.forEach(function(k,i){out[k]=i+1;});
  return out;
})();

const FB_PAL = {804:'#29b6f6',806:'#26d9b0',807:'#a78bfa',808:'#ffb347',
  809:'#ff6e40',811:'#ff5252',812:'#ff1744',815:'#e8c999',816:'#ffd740',
  817:'#80cbc4',818:'#b0bec5',819:'#ffab76',820:'#80deea',821:'#b388ff',
  822:'#fff176',823:'#69f0ae',824:'#40c4ff',825:'#ea80fc',827:'#18ffff',
  828:'#ff9100',829:'#7986cb',830:'#9575cd',831:'#f48fb1',833:'#40e0ff',
  835:'#f72585',837:'#be00ff',899:'#ffe57a'};
const PHASE_COL = {'Pre-Surge':'#4ecdc4','Post-Surge':'#48cae4','Post-Surge-HT':'#06a0c4','Stress':'#ffd166','SDS-Final':'#ff6b6b','SDT-Start':'#c77dff','SDT-Final':'#a06fdd','ISVM-EDC':'#5aabff',OTHER:'#556677',PRESURGE:'#4ecdc4',POSTSURGE:'#ff6b6b',ISVM:'#5aabff'};
const RTYPE_COL = {HV:'#f4a261',HC:'#c77dff',LC:'#84a98c',VLC:'#48cae4','?':'#556677'};
const CS_COL    = {HV:'#f4a261',HC:'#c77dff',LC:'#84a98c',VLC:'#48cae4'};
function fbColor(fb){return FB_PAL[fb]||'#5577aa';}
function fbBadge(fb){var c=fbColor(fb);return '<span style="background:'+c+'22;border:1px solid '+c+';color:'+c+';border-radius:4px;padding:1px 7px;font-size:0.75rem;font-weight:700">FB '+fb+'</span>';}
function phaseBadge(p){var cls={'Pre-Surge':'badge-pre','Post-Surge':'badge-post','Stress':'badge-post','SDS-Final':'badge-post','SDT-Start':'badge-isvm','SDT-Final':'badge-isvm','ISVM-EDC':'badge-isvm','PRESURGE':'badge-pre','POSTSURGE':'badge-post','ISVM':'badge-isvm'}[p]||'';return '<span class="badge '+cls+'">'+p+'</span>';}
function rtypeBadge(r){var cls={HV:'badge-hv',HC:'badge-hc',LC:'badge-lc',VLC:'badge-vlc'}[r]||'';return '<span class="badge '+cls+'">'+r+'</span>';}
function L(ex){return Object.assign({paper_bgcolor:'#0f1520',plot_bgcolor:'#0f1520',font:{color:'#8ab4d4',size:11},xaxis:{gridcolor:'#1e3050',zerolinecolor:'#1e3050'},yaxis:{gridcolor:'#1e3050',zerolinecolor:'#1e3050'},margin:{t:40,l:50,r:20,b:60}},ex);}
const PC={responsive:true,displayModeBar:false};

var _activeTabId = 'overview';
function showTab(id,el){
  _activeTabId = id;
  document.querySelectorAll('.tab-panel').forEach(function(p){p.classList.remove('active');});
  document.querySelectorAll('.tab-btn').forEach(function(b){b.classList.remove('active');});
  document.getElementById('tab-'+id).classList.add('active');
  el.classList.add('active');
  if(id==='wafermap'){initWM();var _ov2=document.getElementById('comp-overlay');if(!_ov2||_ov2.style.display==='none'||!_ov2.style.display){var _sp=PROGS.slice(),_sl=LOTS.slice(),_sw=[...new Set(DIES.map(function(d){return d.wfr;}))],_sf=FB_LIST.map(function(f){return f.fbin;});_cpShowOverlay({progs:_sp,lots:_sl,wfrs:_sw,fbs:_sf,pins:[],colorMode:'fbin'});}
  // After composite view is set up, sync cp- → wm- so filter bars show same state
  setTimeout(function(){_syncCpToWm();},50);}
  if(id==='pareto'){initPareto();drawPareto();}
  if(id==='dietable'){initDT();buildDieTable();}
  if(id==='flowdiag'){initFlowFilters();drawFlowDiagram();}
  if(id==='surge'){initSurge();}
  if(id==='edc'){initEdc();}
  if(id==='railcmp'){initRailCmp();}
}

// ── RAIL × CONDITION COMPARISON ───────────────────────────────────────────────
var _rcInit=false;
function initRailCmp(){
  if(_rcInit)return; _rcInit=true;
  var D=RAIL_COND_DATA;
  var CONDS=D.conds, RAILS=D.rails;
  var COND_CLR={'Pre-Surge':'#ffd166','Post-Surge':'#48cae4','ISVM-EDC':'#c77dff'};

  // Matrix table
  var th='<tr style="background:#131a2a"><th style="padding:5px 12px;border:1px solid #2a4060;text-align:left;color:#8ab4d4">Condition</th>';
  RAILS.forEach(function(r){th+='<th style="padding:5px 12px;border:1px solid #2a4060;text-align:center;color:#8ab4d4">'+r+'</th>';});
  th+='<th style="padding:5px 12px;border:1px solid #2a4060;text-align:center;color:#8ab4d4">Total</th></tr>';
  var tbody='';
  CONDS.forEach(function(c,ci){
    var tot=0;
    var cells=RAILS.map(function(r){var v=D.counts[c+'|'+r]||0;tot+=v;return v;});
    var bg=ci%2?'background:#0a1018;':'';
    tbody+='<tr style="'+bg+'"><td style="padding:4px 12px;border:1px solid #1e3050;color:'+(COND_CLR[c]||'#c0ccd8')+';font-weight:700">'+c+'</td>';
    cells.forEach(function(v){tbody+='<td style="padding:4px 12px;border:1px solid #1e3050;text-align:center;color:#e0eaf8;font-weight:'+(v>0?'700':'400')+'">'+v+'</td>';});
    tbody+='<td style="padding:4px 12px;border:1px solid #1e3050;text-align:center;color:#4ecdc4;font-weight:700">'+tot+'</td></tr>';
  });
  document.getElementById('rc-matrix').innerHTML=th+tbody;

  // Grouped bar chart — rail on x, one trace per condition
  var traces=CONDS.map(function(c){
    return {name:c,type:'bar',x:RAILS,
            y:RAILS.map(function(r){return D.counts[c+'|'+r]||0;}),
            marker:{color:COND_CLR[c]||'#8ab4d4'},
            hovertemplate:'%{x} · '+c+': <b>%{y}</b> dies<extra></extra>'};
  });
  Plotly.newPlot('rc-bar',traces,L({
    title:{text:'BIN8 Dies by Rail &amp; Condition',font:{size:12}},
    barmode:'group',
    xaxis:{title:'Rail Type'},yaxis:{title:'BIN8 Die Count'},
    legend:{orientation:'h',y:-0.22},
    margin:{t:40,l:50,r:10,b:80}
  }),PC);

  // Top-10 pins bar (by total)
  var topPins=D.pin_rows.slice(0,12);
  var pinTraces=CONDS.map(function(c){
    var fld=c==='Pre-Surge'?'pre':c==='Post-Surge'?'post':'edc';
    return {name:c,type:'bar',orientation:'h',
            y:topPins.map(function(p){return p.pin;}),
            x:topPins.map(function(p){return p[fld]||0;}),
            marker:{color:COND_CLR[c]||'#8ab4d4'},
            hovertemplate:'%{y}: <b>%{x}</b> dies<extra></extra>'};
  });
  Plotly.newPlot('rc-pin-bar',pinTraces,L({
    title:{text:'Top Failing Pins by Condition',font:{size:12}},
    barmode:'stack',
    xaxis:{title:'BIN8 Die Count'},yaxis:{autorange:'reversed',tickfont:{size:9}},
    legend:{orientation:'h',y:-0.18},
    margin:{t:40,l:280,r:10,b:60}
  }),PC);

  // Build pin variability data from DIES
  window._rcPinValMap = {};
  DIES.forEach(function(d){
    // K-mode pins — Pre-Surge / Post-Surge
    d.pins.forEach(function(p){
      if(!p.has_lim) return;
      var lim=p.usl||p.lsl; if(!lim) return;
      if(!window._rcPinValMap[p.pin]) window._rcPinValMap[p.pin]={};
      if(!window._rcPinValMap[p.pin][p.phase])
        window._rcPinValMap[p.pin][p.phase]={vals:[],usl:p.usl||null,lsl:p.lsl||null};
      window._rcPinValMap[p.pin][p.phase].vals.push(p.val);
    });
    // ISVM-EDC — one worst-case value per configset per die
    Object.keys(d.edc||{}).forEach(function(cs){
      var info=d.edc[cs];
      if(!info.n_fail) return;
      // USL violation: worst is the highest value seen
      var hasUSL = info.worst && info.usl;
      // LSL violation: worst_lsl is the lowest value seen
      var hasLSL = (info.worst_lsl!==null && info.worst_lsl!==undefined) && info.lsl;
      if(!hasUSL && !hasLSL) return;
      var key=cs+'_EDC';
      if(!window._rcPinValMap[key]) window._rcPinValMap[key]={};
      if(!window._rcPinValMap[key]['ISVM-EDC'])
        window._rcPinValMap[key]['ISVM-EDC']={vals:[],usl:info.usl||null,lsl:info.lsl||null};
      if(hasUSL) window._rcPinValMap[key]['ISVM-EDC'].vals.push(info.worst);
      if(hasLSL) window._rcPinValMap[key]['ISVM-EDC'].vals.push(info.worst_lsl);
    });
  });

  // Populate pin selector with top pins that actually have variability data
  var pinSel=document.getElementById('rc-var-pin');
  var condSel=document.getElementById('rc-var-cond');
  var allPinsWithData=[];
  D.pin_rows.forEach(function(r){
    if(Object.keys(window._rcPinValMap[r.pin]||{}).length>0) allPinsWithData.push(r.pin);
  });
  // EDC configset entries
  ['VLC','LC','HV','HC'].forEach(function(cs){
    var k=cs+'_EDC'; if(window._rcPinValMap[k]) allPinsWithData.push(k);
  });
  allPinsWithData.forEach(function(pin){
    var o=document.createElement('option'); o.value=pin;
    // abbreviate long names for display
    o.text=pin.length>35?pin.slice(0,33)+'\u2026':pin;
    o.title=pin; pinSel.appendChild(o);
  });
  CONDS.forEach(function(c){
    var o=document.createElement('option'); o.value=c; o.text=c; condSel.appendChild(o);
  });

  _rcDrawVarPlot();
  _rcRenderPinTable();
}

function _rcDrawVarPlot(){
  var D=RAIL_COND_DATA;
  var CONDS=D.conds;
  var COND_CLR={'Pre-Surge':'#ffd166','Post-Surge':'#48cae4','ISVM-EDC':'#c77dff'};
  var pvm=window._rcPinValMap||{};

  var selPin=(document.getElementById('rc-var-pin')||{}).value||'__all__';
  var selCond=(document.getElementById('rc-var-cond')||{}).value||'__all__';

  var pinsToShow = selPin==='__all__'
    ? D.pin_rows.slice(0,10).map(function(r){return r.pin;}).filter(function(p){return pvm[p];})
        .concat(['VLC_EDC','LC_EDC','HV_EDC','HC_EDC'].filter(function(k){return pvm[k];}))
    : [selPin];
  var condsToShow = selCond==='__all__' ? CONDS : [selCond];

  var traces=[];
  condsToShow.forEach(function(c){
    var xs=[], ys=[], texts=[];
    pinsToShow.forEach(function(pin){
      var pd=(pvm[pin]||{})[c]; if(!pd||!pd.vals.length) return;
      var shortPin=pin.length>28 ? pin.slice(0,26)+'\u2026' : pin;
      pd.vals.forEach(function(v){
        var pct;
        // Use the limit that was actually violated for normalization
        if(pd.usl && v>pd.usl)       pct=(v/pd.usl-1)*100;   // + above USL
        else if(pd.lsl && v<pd.lsl)  pct=(v/pd.lsl-1)*100;   // - below LSL
        else { var lim=pd.usl||pd.lsl; pct=(v/lim-1)*100; }  // fallback
        xs.push(shortPin);
        ys.push(parseFloat(pct.toFixed(3)));
        texts.push(pin+'<br>'+c+'<br>val='+v+(pd.usl?' USL='+pd.usl:'')+(pd.lsl?' LSL='+pd.lsl:''));
      });
    });
    if(!xs.length) return;
    traces.push({
      name:c, type:'box', x:xs, y:ys,
      text:texts,
      marker:{color:COND_CLR[c]||'#8ab4d4', size:5, opacity:0.7},
      line:{color:COND_CLR[c]||'#8ab4d4', width:1.5},
      fillcolor:(COND_CLR[c]||'#8ab4d4')+'33',
      boxpoints:'all', jitter:0.35, pointpos:0,
      hovertemplate:'%{text}<extra></extra>'
    });
  });

  var isSinglePin = pinsToShow.length===1;
  Plotly.react('rc-var-plot', traces.length?traces:[{type:'scatter',x:[],y:[]}], L({
    title:{text: isSinglePin
      ? 'Variability: '+pinsToShow[0]+' &mdash; % from violated limit'
      : 'Failing Value Distribution &mdash; % from Violated Limit (+ above USL, \u2212 below LSL)',
      font:{size:11}},
    boxmode:'group',
    xaxis:{tickangle:-30, tickfont:{size:8}},
    yaxis:{title:'% from violated limit', zeroline:true, zerolinecolor:'#ff4444', zerolinewidth:1.5},
    shapes:[{type:'line',xref:'paper',x0:0,x1:1,y0:0,y1:0,
             line:{color:'#ff4444',width:1.5,dash:'dot'}}],
    annotations:[{xref:'paper',yref:'y',x:1.01,y:0,text:'Limit',
                  showarrow:false,font:{color:'#ff4444',size:10},xanchor:'left'}],
    legend:{orientation:'h',y:-0.25},
    margin:{t:55,l:65,r:45,b: isSinglePin?60:120}
  }), PC);
}

function _rcRenderPinTable(){
  var D=RAIL_COND_DATA;
  var filt=(document.getElementById('rc-pin-filter')||{value:''}).value.toLowerCase();
  var rows=D.pin_rows.filter(function(p){return !filt||p.pin.toLowerCase().includes(filt);});
  var COND_CLR={'Pre-Surge':'#ffd166','Post-Surge':'#48cae4','ISVM-EDC':'#c77dff'};
  var th='<thead><tr style="background:#131a2a">'
    +'<th style="padding:5px 10px;border:1px solid #2a4060;text-align:left;color:#8ab4d4;min-width:240px">Pin</th>'
    +'<th style="padding:5px 10px;border:1px solid #2a4060;text-align:center;color:'+COND_CLR['Pre-Surge']+'">Pre-Surge</th>'
    +'<th style="padding:5px 10px;border:1px solid #2a4060;text-align:center;color:'+COND_CLR['Post-Surge']+'">Post-Surge</th>'
    +'<th style="padding:5px 10px;border:1px solid #2a4060;text-align:center;color:'+COND_CLR['ISVM-EDC']+'">ISVM-EDC</th>'
    +'<th style="padding:5px 10px;border:1px solid #2a4060;text-align:center;color:#4ecdc4">Total</th>'
    +'</tr></thead>';
  var tbody='<tbody>';
  rows.forEach(function(p,i){
    var bg=i%2?'background:#0a1018;':'';
    var bar=function(v,tot){
      if(!v)return '<td style="padding:4px 10px;border:1px solid #1e3050;text-align:center;color:#334455">0</td>';
      var pct=Math.round(v/tot*100);
      return '<td style="padding:4px 10px;border:1px solid #1e3050;text-align:center"><span style="color:#e0eaf8;font-weight:700">'+v+'</span><span style="color:#556677;font-size:0.68rem"> ('+pct+'%)</span></td>';
    };
    tbody+='<tr style="'+bg+'">'
      +'<td style="padding:4px 10px;border:1px solid #1e3050;color:#8ab4d4;font-family:monospace;font-size:0.72rem;word-break:break-all">'+p.pin+'</td>'
      +bar(p.pre,p.total)+bar(p.post,p.total)+bar(p.edc,p.total)
      +'<td style="padding:4px 10px;border:1px solid #1e3050;text-align:center;color:#4ecdc4;font-weight:700">'+p.total+'</td>'
      +'</tr>';
  });
  tbody+='</tbody>';
  var tbl=document.getElementById('rc-pin-table');
  if(tbl)tbl.innerHTML=th+tbody;
}

// ── OVERVIEW ──────────────────────────────────────────────────────────────────
function initOverview(){
  var topFB=FB_LIST[0]||{};
  var ibMap={};DIES.forEach(function(d){ibMap[d.ibin]=(ibMap[d.ibin]||0)+1;});
  var ibSummary=Object.keys(ibMap).map(Number).sort(function(a,b){return ibMap[b]-ibMap[a];})
    .map(function(ib){return 'IB'+ib+': '+ibMap[ib];}).join(' | ');
  document.getElementById('ov-banner').innerHTML=[
    ['Failing Dies',DIES.length,'red'],
    ['IB Breakdown',ibSummary||'—','amber'],
    ['Kill Tests',KILL_LIST.length,''],
    ['Wafers Affected',WFR_LIST.length,''],
    ['Top FB','FB '+(topFB.fbin||'?')+' ('+(topFB.count||0)+')',''],
  ].map(function(r){return '<div class="ov-card"><div class="ov-num '+r[2]+'" style="font-size:'+(r[0]==='IB Breakdown'?'1rem':'2rem')+'">'+r[1]+'</div><div class="ov-label">'+r[0]+'</div></div>';}).join('');

  var killCols={PRESURGE:'#4ecdc4',POSTSURGE:'#ff6b6b',HVDPS:'#f4a261',HCDPS:'#c77dff',LCDPS:'#84a98c',VLCDPS:'#48cae4',K_START:'#8ab4d4'};
  var _selFB=null;
  function showFBDetail(fb){
    _selFB=fb;
    // highlight selected row
    document.querySelectorAll('#fb-tbl tr[data-fb]').forEach(function(r){
      r.style.background=r.dataset.fb==String(fb.fbin)?fbColor(fb.fbin)+'22':'';
    });
    // build rail table from DIES
    var fbDies=DIES.filter(function(d){return d.fbin===fb.fbin;});
    var pinStats={};
    fbDies.forEach(function(d){
      d.pins.forEach(function(p){
        if(!pinStats[p.pin]) pinStats[p.pin]={vals:[],usl:p.usl,lsl:p.lsl,phase:p.phase,has_lim:p.has_lim,force_val:p.force_val||''};
        pinStats[p.pin].vals.push(p.val);
        if(p.usl) pinStats[p.pin].usl=p.usl;
        if(p.lsl!=null) pinStats[p.pin].lsl=p.lsl;
        if(p.force_val) pinStats[p.pin].force_val=p.force_val;
      });
    });
    var rows=Object.entries(pinStats).sort(function(a,b){return b[1].vals.length-a[1].vals.length;});
    if(!rows.length){
      document.getElementById('fb-pin-detail').innerHTML='<p style="color:#445566">No pin data for FB '+fb.fbin+'</p>';
      return;
    }
    var c=fbColor(fb.fbin);
    var header='<div style="border-left:3px solid '+c+';padding:8px 14px;margin-bottom:10px;background:#0d111888">'
      +'<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">'
      +'<span style="color:'+c+';font-weight:800;font-size:1rem">FB '+fb.fbin+'</span>'
      +'<span style="color:#e0eaf8;font-weight:600">'+fb.count+' dies</span>'
      +'<span style="font-size:0.77rem;color:#667788">Kill: '+fb.top_kill+'</span>'
      +'<span style="font-size:0.73rem;color:#445566">'+_fmtWaferList(fb.wafers)+'</span>'
      +'</div>';
    var tbl='<table style="width:100%;max-width:960px"><thead><tr>'
      +'<th>Pin</th><th>Phase</th><th>Force</th><th>Dies Failing</th><th>Lowest (mV)</th><th>Median (mV)</th><th>Worst (mV)</th><th>LSL (mV)</th><th>USL (mV)</th><th>Worst/USL</th>'
      +'</tr></thead><tbody>';
    rows.forEach(function(e){
      var pin=e[0],s=e[1];
      var n=s.vals.length;
      var sorted=s.vals.slice().sort(function(a,b){return a-b;});
      var lowest=sorted[0];
      var med=sorted.length%2?sorted[(sorted.length-1)/2]:(sorted[sorted.length/2-1]+sorted[sorted.length/2])/2;
      var worst=sorted[sorted.length-1];
      var usl=s.usl;
      var lsl=s.lsl!=null?s.lsl:null;
      var ratio=usl?+(worst/usl).toFixed(2):null;
      var ratioStr=ratio?(ratio>1?'<span style="color:#ff9999;font-weight:700">'+ratio+'\u00d7</span>':'<span style="color:#4ecdc4">'+ratio+'\u00d7</span>'):'—';
      var uslStr=usl?usl:'—';
      var lslStr=lsl!=null?lsl:'—';
      var fv=s.force_val||'—';
      tbl+='<tr><td style="font-weight:700;color:#c0ccd8">'+pin+'</td>'
        +'<td>'+phaseBadge(s.phase)+'</td>'
        +'<td style="color:#8ab4d4;font-size:0.77rem">'+fv+'</td>'
        +'<td><b style="color:#e0eaf8">'+n+'</b> <span style="color:#445566;font-size:0.75rem">('+Math.round(n/fb.count*100)+'%)</span></td>'
        +'<td style="color:#48cae4">'+lowest.toFixed(2)+'</td>'
        +'<td style="color:#8ab4d4">'+med.toFixed(2)+'</td>'
        +'<td style="color:#ffd166">'+worst.toFixed(2)+'</td>'
        +'<td style="color:#667788">'+lslStr+'</td>'
        +'<td style="color:#556677">'+uslStr+'</td>'
        +'<td>'+ratioStr+'</td>'
        +'</tr>';
    });
    tbl+='</tbody></table>';
    document.getElementById('fb-pin-detail').innerHTML=header+tbl+'</div>';

    // ── Distribution chart: bins span full data range; x-axis shows mean±3σ or up to worst failing value
    var distTraces=[];
    rows.forEach(function(e,i){
      var pin=e[0],s=e[1];
      if(s.vals.length<3)return;
      var n=s.vals.length;
      var mean=s.vals.reduce(function(a,b){return a+b;},0)/n;
      var std=Math.sqrt(s.vals.reduce(function(a,v){return a+(v-mean)*(v-mean);},0)/(n-1));
      if(std===0)return;
      var dataMin=Math.min.apply(null,s.vals), dataMax=Math.max.apply(null,s.vals);
      // x-axis window: mean±3σ, but always extend to include worst value and USL
      var xMin=Math.min(mean-3*std, dataMin);
      var xMax=Math.max(mean+3*std, dataMax, s.usl||0);
      var colors=['#4a9fd4','#4ecdc4','#ffd166','#ff6b6b','#c77dff','#84a98c','#48cae4'];
      // bins span full data range so out-of-spec values are never clipped
      var binSize=(dataMax-dataMin)/Math.max(20, Math.min(40, Math.ceil(Math.sqrt(n))));
      distTraces.push({x:s.vals,type:'histogram',name:pin,opacity:0.7,
        xbins:{start:dataMin,end:dataMax+binSize,size:binSize||0.1},
        marker:{color:colors[i%colors.length]},
        hovertemplate:pin+': %{x:.2f} mV<br>Count: %{y}<extra></extra>'});
      // store usl + range for shapes
      distTraces[distTraces.length-1]._usl=s.usl;
      distTraces[distTraces.length-1]._xMin=xMin;
      distTraces[distTraces.length-1]._xMax=xMax;
    });
    if(distTraces.length){
      // Build USL shapes (one per trace that has a USL within its range)
      var shapes=[];
      distTraces.forEach(function(t){
        if(t._usl){
          shapes.push({type:'line',x0:t._usl,x1:t._usl,y0:0,y1:1,yref:'paper',
            line:{color:'#ff4444',width:2,dash:'dash'},
            label:{text:'USL '+t._usl,textposition:'top right',font:{size:10,color:'#ff9999'}}});
        }
      });
      // Global x-range = union of all traces' windows (mean±3σ extended to worst+USL)
      var gMin=Math.min.apply(null,distTraces.map(function(t){return t._xMin;}));
      var gMax=Math.max.apply(null,distTraces.map(function(t){return t._xMax;}));
      distTraces.forEach(function(t){delete t._usl;delete t._xMin;delete t._xMax;});
      document.getElementById('fb-dist-card').style.display='';
      Plotly.react('fb-pin-dist',distTraces,L({
        barmode:'overlay',
        xaxis:{title:'Measured (mV)',range:[gMin,gMax]},
        yaxis:{title:'Dies'},
        shapes:shapes,
        legend:{orientation:'h',y:1.08},
        margin:{t:30,l:52,r:20,b:50},
      }),PC);
    } else {
      document.getElementById('fb-dist-card').style.display='none';
    }
  }
  window.showFBDetail=showFBDetail;
  // Build IB/FB table via shared helper (respects _ovIBFilter)
  _ovIBFilter=null;
  _ovSetIB(null, document.getElementById('ov-ib-btn-all'));

  Plotly.newPlot('fb-pie',[{type:'pie',labels:FB_LIST.map(function(f){return 'FB '+f.fbin;}),values:FB_LIST.map(function(f){return f.count;}),marker:{colors:FB_LIST.map(function(f){return fbColor(f.fbin);})},textinfo:'label+percent',hovertemplate:'%{label}: %{value}<extra></extra>',hole:0.4}],L({title:'BIN8 by Functional Bin',margin:{t:40,l:10,r:10,b:10},showlegend:false}),PC);

  // Populate ov-fb-filter dropdown (clear first so re-init on program switch works)
  var ovFbSel=document.getElementById('ov-fb-filter');
  while(ovFbSel.options.length>1) ovFbSel.remove(1);
  FB_LIST.forEach(function(f){var o=document.createElement('option');o.value=f.fbin;o.text='FB '+f.fbin+' ('+f.count+')';ovFbSel.appendChild(o);});
  drawOvWfr();

  // Auto-show first FB on load; init rail summary
  buildRailSummary(null);
  if(FB_LIST.length) showFBDetail(FB_LIST[0]);
}

// ── Wafer badge helpers ────────────────────────────────────────────────────────
// fb.wafers is now [{lot, wfr}, ...]
function _fmtWaferBadges(wafers){
  if(!wafers||!wafers.length) return '<span style="color:#334455">—</span>';
  var n=wafers.length;
  // Group by lot for compact display
  var lots={};
  wafers.forEach(function(w){
    var ls=w.lot?w.lot.slice(-6):'';
    (lots[ls]=lots[ls]||[]).push(w.wfr);
  });
  var lotKeys=Object.keys(lots);
  // Short inline: show first 3 wafers, then popup
  var shown=wafers.slice(0,3).map(function(w){return 'W'+w.wfr;}).join(' ');
  var extra=n>3?' <span style="color:#445566;font-size:0.7rem">+</span>':'';
  var popId='wfp'+Math.random().toString(36).slice(2,8);
  var popContent=lotKeys.map(function(ls){
    return '<div style="margin-bottom:4px"><span style="color:#8ab4d4;font-weight:700">'+ls+'</span>: '
      +lots[ls].map(function(w){return 'W'+w;}).join(', ')+'</div>';
  }).join('');
  return '<span style="cursor:default" title="'+wafers.map(function(w){return (w.lot||'?')+'/ W'+w.wfr;}).join(', ')+'"'
    +' onclick="event.stopPropagation();_showWfrPopup(this,\''+encodeURIComponent(popContent)+'\')">'
    +'<span style="font-size:0.73rem;color:#8ab4d4">'+shown+extra+'</span>'
    +'<span style="font-size:0.68rem;color:#445566"> ('+n+')</span></span>';
}
function _fmtWaferList(wafers){
  if(!wafers||!wafers.length) return '—';
  return wafers.map(function(w){return (w.lot?w.lot.slice(-6)+'/':'')+' W'+w.wfr;}).join(', ');
}
var _wfrPopup=null;
function _showWfrPopup(el, encoded){
  if(_wfrPopup) { _wfrPopup.remove(); _wfrPopup=null; }
  var div=document.createElement('div');
  div.style.cssText='position:fixed;z-index:9500;background:#0f1a2e;border:1px solid #2a4060;'
    +'border-radius:7px;padding:10px 14px;font-size:0.78rem;color:#c0ccd8;max-width:320px;'
    +'box-shadow:0 4px 20px rgba(0,0,0,0.7);cursor:default';
  div.innerHTML='<div style="font-size:0.72rem;color:#8ab4d4;font-weight:700;margin-bottom:6px;text-transform:uppercase;letter-spacing:.04em">Lot / Wafer</div>'
    +decodeURIComponent(encoded)
    +'<div style="margin-top:8px;text-align:right"><button onclick="this.parentElement.parentElement.remove()" style="background:#1a2235;border:1px solid #2a4060;color:#8ab4d4;border-radius:4px;padding:2px 10px;cursor:pointer;font-size:0.78rem">&times; Close</button></div>';
  var r=el.getBoundingClientRect();
  div.style.top=(r.bottom+6)+'px';
  div.style.left=Math.min(r.left,window.innerWidth-330)+'px';
  document.body.appendChild(div);
  _wfrPopup=div;
  setTimeout(function(){ document.addEventListener('click',function h(e){if(!div.contains(e.target)){div.remove();_wfrPopup=null;document.removeEventListener('click',h);}},true); },10);
}
// ── RAIL SUMMARY ───────────────────────────────────────────────────────────────
var _railFB=null;
function buildRailSummary(fbFilter){
  if(fbFilter!==undefined) _railFB=fbFilter;
  var totalDies=_railFB!==null?DIES.filter(function(d){return d.fbin===_railFB;}).length:BIN8_COUNT;
  // Gather all phase names across all rails
  var allPhases=[];
  RAIL_LIST.forEach(function(r){Object.keys(r.phases).forEach(function(ph){if(allPhases.indexOf(ph)<0)allPhases.push(ph);});});
  // Re-compute stats per pin for the active FB filter
  var railData;
  if(_railFB===null){
    railData=RAIL_LIST;
  } else {
    var fbDies=DIES.filter(function(d){return d.fbin===_railFB;});
    railData=RAIL_LIST.map(function(r){
      var phAcc={};
      fbDies.forEach(function(d){
        d.pins.forEach(function(p){
          if(p.pin!==r.pin)return;
          if(!phAcc[p.phase]) phAcc[p.phase]={vals:[],n_fail:0};
          phAcc[p.phase].vals.push(p.val);
          if(p.has_lim) phAcc[p.phase].n_fail++;
        });
      });
      var phases={};
      Object.entries(phAcc).forEach(function(e){
        var sv=e[1].vals.slice().sort(function(a,b){return a-b;});
        var n=sv.length,med=n%2?sv[(n-1)/2]:(sv[n/2-1]+sv[n/2])/2;
        phases[e[0]]={n:n,n_fail:e[1].n_fail,med:+med.toFixed(2),worst:+sv[n-1].toFixed(2)};
      });
      return {pin:r.pin,cs:r.cs,force:r.force,usl:r.usl,phases:phases};
    });
  }
  // Collect which phases actually have data
  var seenPh=[];
  railData.forEach(function(r){Object.keys(r.phases).forEach(function(ph){if(seenPh.indexOf(ph)<0)seenPh.push(ph);});});
  // Sort: total fails desc, then pin name
  railData=railData.slice().sort(function(a,b){
    var af=Object.values(a.phases).reduce(function(s,v){return s+v.n_fail;},0);
    var bf=Object.values(b.phases).reduce(function(s,v){return s+v.n_fail;},0);
    return bf!==af?bf-af:(a.pin<b.pin?-1:1);
  });
  // Update badge
  var badge=document.getElementById('rail-fb-badge');
  var resetBtn=document.getElementById('rail-reset-btn');
  var placeholder=document.getElementById('rail-reset-btn-placeholder');
  if(_railFB!==null){
    badge.innerHTML=fbBadge(_railFB)+' <span style="color:#667788;font-size:0.75rem">'+totalDies+' dies</span>';
    if(resetBtn){resetBtn.style.display='';if(placeholder)placeholder.style.display='none';}
  } else {
    badge.textContent='All '+totalDies+' BIN8 dies';
    if(resetBtn){resetBtn.style.display='none';if(placeholder)placeholder.style.display='';}
  }
  // Build grouped phase columns
  var phList=seenPh.sort();
  var thead='<thead><tr><th rowspan="2">Pin</th><th rowspan="2">CS</th><th rowspan="2">Force</th><th rowspan="2">USL (mV)</th>';
  phList.forEach(function(ph){
    var c=PHASE_COL[ph]||'#667788';
    thead+='<th colspan="3" style="color:'+c+';text-align:center;border-bottom:1px solid #1e3050;font-size:0.75rem">'+ph+'</th>';
  });
  thead+='</tr><tr>';
  phList.forEach(function(ph){
    var c=PHASE_COL[ph]||'#667788';
    thead+='<th style="color:'+c+';font-weight:600;font-size:0.73rem">Fails</th>'
      +'<th style="color:'+c+';font-weight:600;font-size:0.73rem">Med (mV)</th>'
      +'<th style="color:'+c+';font-weight:600;font-size:0.73rem">Worst (mV)</th>';
  });
  thead+='</tr></thead>';
  var tbody='<tbody>';
  railData.forEach(function(r){
    var totalFails=Object.values(r.phases).reduce(function(s,v){return s+v.n_fail;},0);
    var dimmed=totalFails===0?' style="opacity:0.35"':'';
    var onclick=totalFails>0?' onclick="showRailDetail(\''+r.pin.replace(/\\/g,'\\\\').replace(/'/g,'\\\'')+'\')" style="cursor:pointer" title="Click to see failing dies"':'';
    tbody+='<tr'+dimmed+onclick+'>'
      +'<td style="font-weight:700;color:#c0ccd8;font-size:0.78rem;white-space:nowrap">'+r.pin+'</td>'
      +'<td>'+rtypeBadge(r.cs)+'</td>'
      +'<td style="color:#8ab4d4;font-size:0.74rem;white-space:nowrap">'+(r.force||'\u2014')+'</td>'
      +'<td style="color:#667788">'+(r.usl!==null?r.usl:'\u2014')+'</td>';
    phList.forEach(function(ph){
      var s=r.phases[ph];
      if(!s){tbody+='<td style="color:#2a3a4a">\u2014</td><td style="color:#2a3a4a">\u2014</td><td style="color:#2a3a4a">\u2014</td>';return;}
      var pct=totalDies>0?(s.n_fail/totalDies*100).toFixed(1):'0.0';
      var fStr=s.n_fail>0?'<b style="color:#ff9999">'+s.n_fail+'</b> <span style="color:#445566;font-size:0.71rem">('+pct+'%)</span>':'<span style="color:#334455">0</span>';
      var over=r.usl&&s.worst>r.usl;
      tbody+='<td>'+fStr+'</td>'
        +'<td style="color:#8ab4d4">'+(s.med!==null?s.med.toFixed(2):'\u2014')+'</td>'
        +'<td style="color:'+(over?'#ffd166':'#667788')+'">'+(s.worst!==null?s.worst.toFixed(2):'\u2014')+'</td>';
    });
    tbody+='</tr>';
  });
  tbody+='</tbody>';
  document.getElementById('rail-summary-wrap').innerHTML='<div class="tbl-wrap"><table>'+thead+tbody+'</table></div>';
  document.getElementById('rail-detail-wrap').style.display='none';
}
function showRailDetail(pin){
  // Highlight selected row
  var wrap=document.getElementById('rail-summary-wrap');
  wrap.querySelectorAll('tr[onclick]').forEach(function(r){r.style.background='';});
  var hits=wrap.querySelectorAll('tr[onclick]');
  for(var i=0;i<hits.length;i++){if(hits[i].textContent.indexOf(pin)>=0){hits[i].style.background='#1e2d4588';break;}}
  var src=_railFB!==null?DIES.filter(function(d){return d.fbin===_railFB;}):DIES;
  var failing=src.filter(function(d){return d.pins.some(function(p){return p.pin===pin;});});
  failing=failing.slice().sort(function(a,b){
    var av=a.pins.find(function(p){return p.pin===pin;}),bv=b.pins.find(function(p){return p.pin===pin;});
    return (bv?bv.val:0)-(av?av.val:0);
  });
  // Find USL for this pin
  var uslForPin=null;
  RAIL_LIST.forEach(function(r){if(r.pin===pin&&r.usl!==null)uslForPin=r.usl;});
  var rows=failing.map(function(d){
    var p=d.pins.find(function(x){return x.pin===pin;});
    var usl=p.usl||uslForPin;
    var ratio=usl?+(p.val/usl).toFixed(2):null;
    var ratioStr=ratio?(ratio>1?'<b style="color:#ff9999">'+ratio+'\u00d7</b>':'<span style="color:#4ecdc4">'+ratio+'\u00d7</span>'):'\u2014';
    return '<tr><td style="color:#8ab4d4;font-size:0.77rem">'+d.lot+'</td><td>W'+d.wfr+'</td><td>'+d.x+'</td><td>'+d.y+'</td>'
      +'<td>'+fbBadge(d.fbin)+'</td><td>'+phaseBadge(p.phase)+'</td>'
      +'<td style="color:#ffd166;font-weight:700">'+p.val.toFixed(2)+'</td>'
      +'<td style="color:#667788">'+(usl||'\u2014')+'</td>'
      +'<td>'+ratioStr+'</td></tr>';
  }).join('');
  var det=document.getElementById('rail-detail-wrap');
  det.style.display='';
  det.innerHTML='<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">'
    +'<span style="font-weight:700;color:#c0ccd8;font-size:0.9rem">'+pin+'</span>'
    +'<span style="color:#667788;font-size:0.77rem">'+failing.length+' failing '+(failing.length===1?'die':'dies')+'</span>'
    +'<button onclick="document.getElementById(\'rail-detail-wrap\').style.display=\'none\'" style="margin-left:auto;background:#1a2235;border:1px solid #2a4060;color:#8ab4d4;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:0.75rem">\u00d7 Close</button></div>'
    +'<div class="tbl-wrap"><table><thead><tr>'
    +'<th>Lot</th><th>W#</th><th>X</th><th>Y</th><th>FB</th><th>Phase</th>'
    +'<th>Val (mV)</th><th>USL (mV)</th><th>Ratio</th>'
    +'</tr></thead><tbody>'+(rows||'<tr><td colspan="9" style="color:#445566;text-align:center;padding:20px">No failing dies</td></tr>')+'</tbody></table></div>';
}

// ── FB PARETO MODAL ────────────────────────────────────────────────────────────
function showFBPareto(fb){
  var fbDies=DIES.filter(function(d){return d.fbin===fb.fbin;});
  // Aggregate per-pin stats across all dies in this FB
  var pinMap={};
  fbDies.forEach(function(d){
    var seen=new Set();
    d.pins.forEach(function(p){
      if(seen.has(p.pin))return;seen.add(p.pin);
      if(!pinMap[p.pin])pinMap[p.pin]={n:0,phase:p.phase,cs:p.cs,usl:p.usl,force:p.force_val||'',vals:[]};
      pinMap[p.pin].n++;
      pinMap[p.pin].vals.push(p.val);
      if(p.usl)pinMap[p.pin].usl=p.usl;
      if(p.force_val)pinMap[p.pin].force=p.force_val;
    });
  });
  var sorted=Object.entries(pinMap).sort(function(a,b){return b[1].n-a[1].n;});
  var c=fbColor(fb.fbin);
  document.getElementById('fb-modal-fb').innerHTML=fbBadge(fb.fbin);
  document.getElementById('fb-modal-title').textContent='Failing Pin Pareto';
  document.getElementById('fb-modal-sub').textContent=fb.count+' dies · '+sorted.length+' failing pins · Kill: '+fb.top_kill;
  // ── Pareto chart
  var pins=sorted.map(function(e){return e[0];});
  var cnts=sorted.map(function(e){return e[1].n;});
  var cols=sorted.map(function(e){return CS_COL[e[1].cs]||'#5577aa';});
  var tot=cnts.reduce(function(s,v){return s+v;},0),cum=0;
  var cumPct=cnts.map(function(v){cum+=v;return +(cum/tot*100).toFixed(1);});
  // Phase breakdown stacked traces
  var phaseMap={};
  fbDies.forEach(function(d){
    var seen=new Set();
    d.pins.forEach(function(p){
      if(seen.has(p.pin+p.phase))return;seen.add(p.pin+p.phase);
      if(!phaseMap[p.phase])phaseMap[p.phase]={};
      phaseMap[p.phase][p.pin]=(phaseMap[p.phase][p.pin]||0)+1;
    });
  });
  var phTraces=Object.entries(phaseMap).map(function(e){
    var pc=PHASE_COL[e[0]]||'#667788';
    return{x:pins,y:pins.map(function(p){return e[1][p]||0;}),name:e[0],type:'bar',
      marker:{color:pc},hovertemplate:e[0]+': %{y} dies<extra></extra>'};
  });
  phTraces.push({x:pins,y:cumPct,name:'Cum%',type:'scatter',mode:'lines+markers',yaxis:'y2',
    line:{color:'#ffd166',width:2},marker:{size:4},hovertemplate:'Cum: %{y}%<extra></extra>'});
  Plotly.react('fb-modal-chart',phTraces,L({
    barmode:'stack',
    title:'FB '+fb.fbin+' — Failing Pins ('+fb.count+' dies)',
    xaxis:{tickangle:-55,tickfont:{size:9}},
    yaxis:{title:'# Dies'},
    yaxis2:{title:'Cum%',overlaying:'y',side:'right',range:[0,105]},
    legend:{orientation:'h',y:1.07},
    margin:{t:45,l:52,r:58,b:160},
  }),{responsive:true,displayModeBar:false});
  // ── Pin table
  var rows=sorted.map(function(e){
    var pin=e[0],s=e[1];
    var sv=s.vals.slice().sort(function(a,b){return a-b;});
    var n=sv.length;
    var lowest=sv[0];
    var med=n%2?sv[(n-1)/2]:(sv[n/2-1]+sv[n/2])/2;
    var worst=sv[n-1];
    var pct=(s.n/fb.count*100).toFixed(1);
    var ratio=s.usl?+(worst/s.usl).toFixed(2):null;
    var ratioStr=ratio?(ratio>1?'<b style="color:#ff9999">'+ratio+'×</b>':'<span style="color:#4ecdc4">'+ratio+'×</span>'):'—';
    return '<tr>'
      +'<td style="font-weight:700;color:#c0ccd8">'+pin+'</td>'
      +'<td>'+phaseBadge(s.phase)+'</td>'
      +'<td>'+rtypeBadge(s.cs)+'</td>'
      +'<td style="color:#8ab4d4;font-size:0.75rem">'+(s.force||'—')+'</td>'
      +'<td><b style="color:#e0eaf8">'+s.n+'</b> <span style="color:#445566;font-size:0.74rem">('+pct+'%)</span></td>'
      +'<td style="color:#48cae4">'+lowest.toFixed(2)+'</td>'
      +'<td style="color:#8ab4d4">'+med.toFixed(2)+'</td>'
      +'<td style="color:#ffd166">'+worst.toFixed(2)+'</td>'
      +'<td style="color:#667788">'+(s.usl||'—')+'</td>'
      +'<td>'+ratioStr+'</td>'
      +'</tr>';
  }).join('');
  document.getElementById('fb-modal-table').innerHTML=
    '<table><thead><tr>'
    +'<th>Pin</th><th>Phase</th><th>CS</th><th>Force</th>'
    +'<th>Dies Failing</th><th>Lowest (mV)</th><th>Median (mV)</th><th>Worst (mV)</th><th>USL (mV)</th><th>Ratio</th>'
    +'</tr></thead><tbody>'+rows+'</tbody></table>';
  document.getElementById('fb-pareto-modal').style.display='flex';
}
function closeFBModal(){document.getElementById('fb-pareto-modal').style.display='none';}

// ── OVERVIEW WAFER BAR (with FB filter) ───────────────────────────────────────
function drawOvWfr(){
  var fbF=document.getElementById('ov-fb-filter').value;
  var wfrs;
  if(fbF==='all'){
    wfrs=WFR_LIST;
  } else {
    var fbN=+fbF;
    wfrs=WFR_LIST.map(function(w){
      var n=w.fbins&&w.fbins[String(fbN)]?w.fbins[String(fbN)]:0;
      return {wfr:w.wfr,lot:w.lot,prog:w.prog,count:n};
    }).filter(function(w){return w.count>0;});
  }
  var xs=wfrs.map(function(w){return 'W'+w.wfr+' ('+w.lot.slice(-5)+')';});
  var ys=wfrs.map(function(w){return w.count;});
  var title=fbF==='all'?'BIN8 per Wafer':'BIN8 per Wafer \u2014 FB '+fbF;
  var ymax=Math.max.apply(null,ys);
  Plotly.react('ov-wfr',[{type:'bar',x:xs,y:ys,marker:{color:ys.map(function(c){return c>50?'#e05c5c':c>20?'#ffd166':'#4a9fd4';})},text:ys,textposition:'outside',hovertemplate:'%{x}: %{y}<extra></extra>'}],L({title:title,xaxis:{tickangle:-45},yaxis:{title:'BIN8 count',range:[0,ymax*1.25]},margin:{t:40,l:50,r:20,b:100}}),PC);
}

// ── WAFER MAP ──────────────────────────────────────────────────────────────────
var _wmInit=false;
function _mkCb(containerId,value,label,color,checked,onChange){
  var wrap=document.createElement('label');
  wrap.style.cssText='display:flex;align-items:center;gap:5px;cursor:pointer;font-size:0.78rem;color:'+(color||'#c0ccd8')+';white-space:nowrap';
  var cb=document.createElement('input');cb.type='checkbox';cb.value=value;cb.checked=checked;
  cb.style.cssText='accent-color:'+(color||'#4a9fd4')+';cursor:pointer;width:13px;height:13px';
  cb.addEventListener('change',onChange||onWmFilter);
  wrap.appendChild(cb);wrap.appendChild(document.createTextNode('\u00a0'+label));
  document.getElementById(containerId).appendChild(wrap);
}
// Toggle All/None for a group, then cascade forward
function wmToggleAll(containerId,checked){
  document.getElementById(containerId).querySelectorAll('input[type=checkbox]').forEach(function(cb){cb.checked=checked;});
  var cascadeMap={'wm-prog-cbs':onProgChange,'wm-lot-cbs':onLotChange,'wm-wfr-cbs':onWfrChange,'wm-fb-cbs':onFBChange};
  var fn=cascadeMap[containerId];
  if(fn) fn(); else onWmFilter();
}
function _wmChecked(containerId){
  return Array.from(document.getElementById(containerId).querySelectorAll('input:checked')).map(function(cb){return cb.value;});
}
function _setCbs(containerId,activeSet,castFn){
  document.getElementById(containerId).querySelectorAll('input[type=checkbox]').forEach(function(cb){
    cb.checked=activeSet.has(castFn?castFn(cb.value):cb.value);
  });
}
// ── Cascade helpers (each recalculates downstream from DIES) ────────────────
function onProgChange(){
  var selProgs=new Set(_wmChecked('wm-prog-cbs'));
  var activeLots=new Set(),activeWfrs=new Set(),activeFBs=new Set(),activePins=new Set();
  DIES.forEach(function(d){
    if(!selProgs.has(d.prog)) return;
    activeLots.add(d.lot);activeWfrs.add(d.wfr);activeFBs.add(d.fbin);
    d.pins.forEach(function(p){activePins.add(p.pin);});
  });
  _setCbs('wm-lot-cbs',activeLots);
  _setCbs('wm-wfr-cbs',activeWfrs,Number);
  _setCbs('wm-fb-cbs',activeFBs,Number);
  _setCbs('wm-pin-cbs',activePins);
  drawWM();
}
function onLotChange(){
  var selProgs=new Set(_wmChecked('wm-prog-cbs'));
  var selLots =new Set(_wmChecked('wm-lot-cbs'));
  var activeWfrs=new Set(),activeFBs=new Set(),activePins=new Set();
  DIES.forEach(function(d){
    if(!selProgs.has(d.prog)||!selLots.has(d.lot)) return;
    activeWfrs.add(d.wfr);activeFBs.add(d.fbin);
    d.pins.forEach(function(p){activePins.add(p.pin);});
  });
  _setCbs('wm-wfr-cbs',activeWfrs,Number);
  _setCbs('wm-fb-cbs',activeFBs,Number);
  _setCbs('wm-pin-cbs',activePins);
  drawWM();
}
function onWfrChange(){
  var selProgs=new Set(_wmChecked('wm-prog-cbs'));
  var selLots =new Set(_wmChecked('wm-lot-cbs'));
  var selWfrs =new Set(_wmChecked('wm-wfr-cbs').map(Number));
  var activeFBs=new Set(),activePins=new Set();
  DIES.forEach(function(d){
    if(!selProgs.has(d.prog)||!selLots.has(d.lot)||!selWfrs.has(d.wfr)) return;
    activeFBs.add(d.fbin);
    d.pins.forEach(function(p){activePins.add(p.pin);});
  });
  _setCbs('wm-fb-cbs',activeFBs,Number);
  _setCbs('wm-pin-cbs',activePins);
  drawWM();
}
function onFBChange(){
  var selProgs=new Set(_wmChecked('wm-prog-cbs'));
  var selLots =new Set(_wmChecked('wm-lot-cbs'));
  var selWfrs =new Set(_wmChecked('wm-wfr-cbs').map(Number));
  var selFBs  =new Set(_wmChecked('wm-fb-cbs').map(Number));
  var activePins=new Set();
  DIES.forEach(function(d){
    if(!selProgs.has(d.prog)||!selLots.has(d.lot)||!selWfrs.has(d.wfr)||!selFBs.has(d.fbin)) return;
    d.pins.forEach(function(p){activePins.add(p.pin);});
  });
  _setCbs('wm-pin-cbs',activePins);
  drawWM();
}
// ── Shared tree-filter helpers ────────────────────────────────────────────────
function _buildLotWfrTree(containerId, dies, onChange){
  var container=document.getElementById(containerId); if(!container)return;
  container.innerHTML='';
  // Build lot → sorted wafers map
  var lotMap={};
  dies.forEach(function(d){
    if(!lotMap[d.lot]) lotMap[d.lot]=new Set();
    lotMap[d.lot].add(d.wfr);
  });
  var _matByWfr={};
  WFR_LIST.forEach(function(w){_matByWfr[w.lot+'|'+w.wfr]=w.material||'';});
  // Abbreviate material name after 2nd " - "
  function _abbrevMat(m){
    if(!m) return '';
    var i1=m.indexOf(' - '); if(i1<0) return m;
    var i2=m.indexOf(' - ',i1+3); return i2>=0?m.substring(0,i2)+'..':m;
  }
  Object.keys(lotMap).sort().forEach(function(lot){
    var wfrs=Array.from(lotMap[lot]).sort(function(a,b){return a-b;});
    var lotId='tree_lot_'+containerId+'_'+lot.replace(/[^a-z0-9]/gi,'_');
    var wfrDivId=lotId+'_wfrs';
    // Aggregate unique materials for this lot
    var _lotMats=[];
    wfrs.forEach(function(w){var m=_matByWfr[lot+'|'+w]||'';if(m&&_lotMats.indexOf(m)<0)_lotMats.push(m);});
    var _lotMatTag=_lotMats.length?'<span style="font-size:0.67rem;color:#4ecdc4;margin-left:4px;font-weight:700" title="'+_lotMats.join(', ')+'">['+_lotMats.map(_abbrevMat).join('+')+']</span>':'';
    // Lot row — <details> dropdown
    var lotDetails=document.createElement('details');
    lotDetails.style.cssText='margin-bottom:1px';
    var lotSum=document.createElement('summary');
    lotSum.style.cssText='display:flex;align-items:center;gap:4px;padding:2px 4px;border-radius:3px;cursor:pointer;list-style:none;outline:none';
    var lotCbEl=document.createElement('input');lotCbEl.type='checkbox';lotCbEl.dataset.type='lot';lotCbEl.value=lot;lotCbEl.checked=true;lotCbEl.style.cssText='accent-color:#4a9fd4;cursor:pointer;flex-shrink:0';
    lotCbEl.addEventListener('click',function(e){e.stopPropagation();});
    var lotLbl=document.createElement('span');
    lotLbl.style.cssText='font-size:0.76rem;color:#8ab4d4;flex:1;display:flex;align-items:center;gap:4px;pointer-events:none';
    lotLbl.innerHTML=lot+' '+_lotMatTag+' <span style="color:#445566;font-size:0.7rem">('+wfrs.length+'W)</span>';
    lotSum.appendChild(lotCbEl); lotSum.appendChild(lotLbl);
    lotDetails.appendChild(lotSum);
    container.appendChild(lotDetails);
    var lotCb=lotCbEl;
    // Wafer child area (inside <details>)
    var wfrDiv=document.createElement('div');
    wfrDiv.dataset.ddRole='children';
    wfrDiv.style.cssText='padding-left:20px;flex-direction:column;gap:1px';
    // Group wafers by material
    var matGroups={};
    var noMatWfrs=[];
    wfrs.forEach(function(w){
      var m=_matByWfr[lot+'|'+w]||'';
      if(m){if(!matGroups[m])matGroups[m]=[];matGroups[m].push(w);}
      else noMatWfrs.push(w);
    });
    var matKeys=Object.keys(matGroups).sort();
    function _updateLotCb(){
      var allW=wfrDiv.querySelectorAll('input[data-type=wfr]');
      var nChk=wfrDiv.querySelectorAll('input[data-type=wfr]:checked').length;
      lotCb.checked=nChk>0; lotCb.indeterminate=(nChk>0&&nChk<allW.length);
      if(onChange) onChange();
    }
    lotCb.addEventListener('change',function(){
      wfrDiv.querySelectorAll('input[data-type=wfr],input[data-type=matgrp]').forEach(function(cb){cb.checked=lotCb.checked;cb.indeterminate=false;});
      if(onChange) onChange();
    });
    // Material group sections — <details> dropdown
    matKeys.forEach(function(mat){
      var grpWfrs=matGroups[mat];
      var matDetails=document.createElement('details');
      matDetails.style.cssText='margin-top:2px';
      var matSum=document.createElement('summary');
      matSum.style.cssText='display:flex;align-items:center;gap:4px;padding:1px 2px;border-radius:3px;cursor:pointer;list-style:none;outline:none';
      var matCbEl=document.createElement('input');matCbEl.type='checkbox';matCbEl.dataset.type='matgrp';matCbEl.dataset.lot=lot;matCbEl.checked=true;matCbEl.style.cssText='accent-color:#4ecdc4;cursor:pointer;flex-shrink:0';
      matCbEl.addEventListener('click',function(e){e.stopPropagation();});
      var matLbl=document.createElement('span');
      matLbl.style.cssText='font-size:0.71rem;color:#4ecdc4;flex:1;display:flex;align-items:center;gap:4px;pointer-events:none';
      matLbl.title=mat;
      matLbl.innerHTML='<span style="font-weight:600">'+mat+'</span> <span style="color:#445566;font-size:0.67rem">('+grpWfrs.length+'W)</span>';
      matSum.appendChild(matCbEl); matSum.appendChild(matLbl);
      matDetails.appendChild(matSum);
      wfrDiv.appendChild(matDetails);
      var matCb=matCbEl;
      var matWfrDiv=document.createElement('div');
      matWfrDiv.style.cssText='padding-left:16px;flex-direction:column;gap:1px';
      grpWfrs.forEach(function(w){
        var wl=document.createElement('label');
        wl.style.cssText='display:flex;align-items:center;gap:4px;cursor:pointer;font-size:0.72rem;color:#667788;padding:1px 0';
        var wcb=document.createElement('input');wcb.type='checkbox';wcb.dataset.type='wfr';wcb.dataset.lot=lot;wcb.value=w;wcb.checked=true;
        wcb.style.cssText='accent-color:#4a9fd4;cursor:pointer';
        wcb.addEventListener('change',function(){
          var allG=matWfrDiv.querySelectorAll('input[data-type=wfr]');
          var nG=matWfrDiv.querySelectorAll('input[data-type=wfr]:checked').length;
          matCb.checked=nG>0; matCb.indeterminate=(nG>0&&nG<allG.length);
          _updateLotCb();
        });
        wl.appendChild(wcb);
        wl.appendChild(Object.assign(document.createElement('span'),{textContent:' W'+w+' \u2014 '+mat}));
        matWfrDiv.appendChild(wl);
      });
      matCb.addEventListener('change',function(){
        matWfrDiv.querySelectorAll('input[data-type=wfr]').forEach(function(cb){cb.checked=matCb.checked;cb.indeterminate=false;});
        _updateLotCb();
      });
      matDetails.appendChild(matWfrDiv);
    });
    // Wafers with no material (flat)
    noMatWfrs.forEach(function(w){
      var wl=document.createElement('label');
      wl.style.cssText='display:flex;align-items:center;gap:4px;cursor:pointer;font-size:0.74rem;color:#667788;padding:1px 0';
      var wcb=document.createElement('input');wcb.type='checkbox';wcb.dataset.type='wfr';wcb.dataset.lot=lot;wcb.value=w;wcb.checked=true;
      wcb.style.cssText='accent-color:#4a9fd4;cursor:pointer';
      wcb.addEventListener('change',function(){_updateLotCb();});
      wl.appendChild(wcb);
      wl.appendChild(Object.assign(document.createElement('span'),{textContent:' W'+w}));
      wfrDiv.appendChild(wl);
    });
    lotDetails.appendChild(wfrDiv);
  });
}

function _buildIbFbTree(containerId, dies, fbList, onChange){
  var container=document.getElementById(containerId); if(!container)return;
  container.innerHTML='';
  // Build IB → FBs map from DIES
  var ibFbMap={};
  dies.forEach(function(d){
    if(!ibFbMap[d.ibin]) ibFbMap[d.ibin]=new Set();
    ibFbMap[d.ibin].add(d.fbin);
  });
  var ibColors={8:'#ff6b6b',80:'#ffd166',89:'#c77dff'};
  Object.keys(ibFbMap).map(Number).sort(function(a,b){return a-b;}).forEach(function(ib){
    var fbs=Array.from(ibFbMap[ib]).sort(function(a,b){return a-b;});
    var ibId='tree_ib_'+containerId+'_'+ib;
    var fbDivId=ibId+'_fbs';
    var ibColor=ibColors[ib]||'#8ab4d4';
    // IB row
    var ibRow=document.createElement('div');
    ibRow.style.cssText='display:flex;align-items:center;gap:4px;padding:2px 4px;border-radius:3px';
    ibRow.innerHTML='<span style="cursor:pointer;color:#445566;font-size:10px;user-select:none" '
      +'onclick="var d=document.getElementById(\''+fbDivId+'\');d.style.display=d.style.display===\'none\'?\'flex\':\'none\';this.textContent=d.style.display===\'none\'?\'\\u25b6\':\'\\u25bc\'">&#9658;</span>'
      +'<label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:0.76rem;color:'+ibColor+';font-weight:700;flex:1">'
      +'<input type="checkbox" data-type="ib" value="'+ib+'" checked style="accent-color:'+ibColor+';cursor:pointer"> IB '+ib+' <span style="color:#445566;font-size:0.7rem;font-weight:400">('+fbs.length+' FB)</span></label>';
    container.appendChild(ibRow);
    var ibCb=ibRow.querySelector('input[data-type=ib]');
    ibCb.addEventListener('change',function(){
      document.querySelectorAll('#'+fbDivId+' input[data-type=fb]').forEach(function(cb){cb.checked=ibCb.checked;});
      if(onChange) onChange();
    });
    // FB child rows
    var fbDiv=document.createElement('div');
    fbDiv.id=fbDivId;
    fbDiv.dataset.ddRole='children';
    fbDiv.style.cssText='display:none;flex-direction:column;gap:1px;padding-left:20px';
    fbs.forEach(function(fb){
      var fl=fbList.find(function(f){return f.fbin===fb;});
      var fbl=document.createElement('label');
      fbl.style.cssText='display:flex;align-items:center;gap:4px;cursor:pointer;font-size:0.74rem;padding:1px 0';
      var fbc=document.createElement('input');fbc.type='checkbox';fbc.dataset.type='fb';fbc.dataset.ib=ib;fbc.value=fb;fbc.checked=true;
      fbc.style.cssText='accent-color:'+fbColor(fb)+';cursor:pointer';
      fbc.addEventListener('change',function(){
        var allFbs=document.querySelectorAll('#'+fbDivId+' input[data-type=fb]');
        var nChk=document.querySelectorAll('#'+fbDivId+' input[data-type=fb]:checked').length;
        ibCb.checked=nChk>0; ibCb.indeterminate=(nChk>0&&nChk<allFbs.length);
        if(onChange) onChange();
      });
      var cnt=fl?fl.count:0;
      fbl.appendChild(fbc);
      fbl.appendChild(document.createTextNode(' '));
      var sp=document.createElement('span');sp.style.color=fbColor(fb);sp.style.fontWeight='700';sp.textContent='FB '+fb;
      fbl.appendChild(sp);
      if(cnt) fbl.appendChild(Object.assign(document.createElement('span'),{style:'color:#445566;font-size:0.7rem',textContent:' ('+cnt+')'}));
      fbDiv.appendChild(fbl);
    });
    container.appendChild(fbDiv);
  });
}

function _wmTreeToggleAll(containerId, checked){
  document.getElementById(containerId).querySelectorAll('input[type=checkbox]').forEach(function(cb){
    cb.checked=checked; cb.indeterminate=false;
  });
  // Sync to composite view after toggle
  if(typeof _syncWmToCp==='function') _syncWmToCp();
}
// ── Shared dropdown helpers ────────────────────────────────────────────────────
function _ddToggle(panelId,evt){
  if(evt) evt.stopPropagation();
  var p=document.getElementById(panelId);
  var opening=!p.classList.contains('open');
  document.querySelectorAll('.dd-panel.open').forEach(function(pp){pp.classList.remove('open');});
  if(opening) p.classList.add('open');
}
document.addEventListener('click',function(){
  document.querySelectorAll('.dd-panel.open').forEach(function(p){p.classList.remove('open');});
});
function _ddSearch(treeId,q){
  q=(q||'').toLowerCase().trim();
  var tree=document.getElementById(treeId); if(!tree) return;
  var ch=Array.from(tree.children); var i=0;
  while(i<ch.length){
    var row=ch[i];
    var hasToggle=row.querySelector&&row.querySelector('[data-dd-toggle]');
    var nextIsChild=i+1<ch.length&&ch[i+1].dataset&&ch[i+1].dataset.ddRole==='children';
    if(hasToggle&&nextIsChild){
      var childDiv=ch[i+1];
      if(!q){row.style.display='';childDiv.style.display='';i+=2;continue;}
      var pTxt=row.textContent.toLowerCase();
      var cLabels=Array.from(childDiv.querySelectorAll('label'));
      var cMatch=cLabels.filter(function(l){return l.textContent.toLowerCase().includes(q);});
      if(pTxt.includes(q)||cMatch.length){
        row.style.display='';childDiv.style.display='flex';
        cLabels.forEach(function(l){l.style.display=(pTxt.includes(q)||l.textContent.toLowerCase().includes(q))?'':'none';});
      } else {row.style.display='none';childDiv.style.display='none';}
      i+=2;
    } else {
      row.style.display=(!q||row.textContent.toLowerCase().includes(q))?'':'none';
      i++;
    }
  }
}
function _ddLabelUpdate(btnId,treeId,dataType){
  var btn=document.getElementById(btnId); if(!btn) return;
  var sel=dataType
    ? document.querySelectorAll('#'+treeId+' input[data-type='+dataType+']:checked').length
    : document.querySelectorAll('#'+treeId+' input[type=checkbox]:checked').length;
  var tot=dataType
    ? document.querySelectorAll('#'+treeId+' input[data-type='+dataType+']').length
    : document.querySelectorAll('#'+treeId+' input[type=checkbox]').length;
  var lbl=btn.querySelector('.dd-lbl');
  if(lbl) lbl.textContent=(sel>=tot||!tot)?'All':sel+'/'+tot;
}
function _ddSearchFlat(containerId,q){
  q=(q||'').toLowerCase().trim();
  var c=document.getElementById(containerId); if(!c) return;
  Array.from(c.children).forEach(function(row){
    row.style.display=(!q||row.textContent.toLowerCase().includes(q))?'':'none';
  });
}

// ── Overview IB tab filter ─────────────────────────────────────────────────────
var _ovIBFilter=null;
function _ovSetIB(ib, btn){
  _ovIBFilter=ib;
  ['all','8','80','89'].forEach(function(k){
    var b=document.getElementById('ov-ib-btn-'+k);
    if(!b)return;
    var active=(ib===null&&k==='all')||(ib!==null&&String(ib)===k);
    b.style.background=active?'#1e3050':'#0d1520';
    b.style.opacity=active?'1':'0.6';
  });
  _ovRebuildFbTable();
}
function _ovRebuildFbTable(){
  if(typeof initOverview==='undefined')return;
  // Re-render only the FB table section using current IB filter
  var ibMap={};DIES.forEach(function(d){ibMap[d.ibin]=(ibMap[d.ibin]||0)+1;});
  var totalFail=DIES.length;
  var killCols={PRESURGE:'#4ecdc4',POSTSURGE:'#ff6b6b',HVDPS:'#f4a261',HCDPS:'#c77dff',LCDPS:'#84a98c',VLCDPS:'#48cae4',K_START:'#8ab4d4'};
  var ibOrder=Object.keys(ibMap).map(Number).sort(function(a,b){return ibMap[b]-ibMap[a];});
  if(_ovIBFilter!==null) ibOrder=ibOrder.filter(function(ib){return ib===_ovIBFilter;});
  var fbRows='';
  ibOrder.forEach(function(ib){
    var ibDies=ibMap[ib]||0;
    var ibPct=(totalFail>0?(ibDies/totalFail*100).toFixed(1):'0');
    var ibColor=ib===8?'#ff6b6b':ib===80?'#ffd166':'#c77dff';
    if(_ovIBFilter===null){
      fbRows+='<tr style="background:#0d1828"><td colspan="6"><span style="color:'+ibColor+';font-weight:800;font-size:0.88rem">IB '+ib+'</span>'
        +'&nbsp;<b style="color:#e0eaf8">'+ibDies+'</b>'
        +' <span style="color:#445566;font-size:0.75rem">('+ibPct+'%)</span></td></tr>';
    }
    FB_LIST.filter(function(fb){return DIES.some(function(d){return d.ibin===ib&&d.fbin===fb.fbin;});})
    .forEach(function(fb){
      var pct=(fb.count/BIN8_COUNT*100).toFixed(1);
      var kh=fb.top_kill.split(' \u00b7 ').map(function(p){var kc=killCols[p]||'#667788';return '<span style="color:'+kc+';font-weight:700">'+p+'</span>';}).join(' <span style="color:#334455">\u00b7</span> ');
      var pt=fb.pins.slice(0,4).map(function(p){return '<span class="pin-tag pin-fail">'+p.pin+' \u00d7'+p.n+'</span>';}).join('');
      var wt=_fmtWaferBadges(fb.wafers);
      var c=fbColor(fb.fbin);
      fbRows+='<tr data-fb="'+fb.fbin+'" style="cursor:pointer" onclick="(function(f){showFBDetail(f);showFBPareto(f);})(FB_LIST.find(function(f){return f.fbin==='+fb.fbin+';}))" title="Click to see full pin pareto">'
        +'<td style="padding-left:'+((_ovIBFilter===null)?'18px':'6px')+'"><span style="color:'+c+';font-weight:700">FB '+fb.fbin+'</span></td>'
        +'<td><b style="color:#e0eaf8">'+fb.count+'</b></td><td style="color:#667788">'+pct+'%</td>'
        +'<td style="font-size:0.77rem">'+kh+' <span style="color:#445566;font-size:0.72rem">('+fb.kill_n+')</span></td>'
        +'<td>'+pt+'</td><td style="font-size:0.74rem">'+wt+'</td></tr>';
    });
  });
  var tblEl=document.getElementById('fb-tbl');
  if(tblEl) tblEl.innerHTML='<table><thead><tr><th>IB / FB</th><th>#</th><th>%</th><th>Primary Kill</th><th>Top Pins</th><th>Wafers</th></tr></thead><tbody>'+fbRows+'</tbody></table>';
}


function initWM(){
  if(_wmInit) return; _wmInit=true;
  // Move composite overlay inside the wafermap tab so it renders inline (not as popup)
  var _ov=document.getElementById('comp-overlay'),_wt=document.getElementById('tab-wafermap');
  if(_ov&&_wt&&_ov.parentElement!==_wt){_wt.appendChild(_ov);}
  PROGS.forEach(function(p){_mkCb('wm-prog-cbs',p,p,'#8ab4d4',true,function(){onWmFilter();_ddLabelUpdate('wm-lot-btn','wm-lot-wfr-tree','lot');_ddLabelUpdate('wm-fb-btn','wm-ib-fb-tree','ib');});});
  if(PROGS.length<=1){var pw=document.getElementById('wm-prog-wrap');if(pw)pw.style.display='none';}
  // Lot/Wafer tree — use onWmFilter so composite view also updates
  _buildLotWfrTree('wm-lot-wfr-tree', DIES, function(){onWmFilter();_ddLabelUpdate('wm-lot-btn','wm-lot-wfr-tree','lot');});
  // IB/FB tree — use onWmFilter so composite view also updates
  _buildIbFbTree('wm-ib-fb-tree', DIES, FB_LIST, function(){onWmFilter();_ddLabelUpdate('wm-fb-btn','wm-ib-fb-tree','ib');});
  // Failing pin checkboxes
  PIN_LIST.forEach(function(p){_mkCb('wm-pin-cbs',p.pin,p.pin+' ('+p.count+')',CS_COL[p.cs]||'#8ab4d4',true,function(){onWmFilter();_ddLabelUpdate('wm-pin-btn','wm-pin-cbs','');});});
}
function onWmFilter(){drawWM(); _syncWmToCp();}
var _cpSyncTimer=null;
function _syncWmToCp(){
  // Debounce: wait 80ms after last change before re-rendering
  if(_cpSyncTimer) clearTimeout(_cpSyncTimer);
  _cpSyncTimer=setTimeout(function(){
    var _lw=_wmGetSelLotsWfrs();
    var cpLotEl=document.getElementById('cp-lot');
    if(cpLotEl) cpLotEl.querySelectorAll('input[type=checkbox]').forEach(function(cb){
      cb.checked=_lw.lots.has(cb.value);
    });
    var cpWfrEl=document.getElementById('cp-wfr');
    if(cpWfrEl) cpWfrEl.querySelectorAll('input[type=checkbox]').forEach(function(cb){
      cb.checked=_lw.wfrs.has(+cb.value);
    });
    var _sfbs=_wmGetSelFBs();
    var cpFbEl=document.getElementById('cp-fb');
    if(cpFbEl) cpFbEl.querySelectorAll('input[type=checkbox]').forEach(function(cb){
      cb.checked=(_sfbs.size===0)||_sfbs.has(+cb.value);
    });
    var _spins=new Set(_wmChecked('wm-pin-cbs'));
    var cpPinEl=document.getElementById('cp-pin');
    if(cpPinEl) cpPinEl.querySelectorAll('input[type=checkbox]').forEach(function(cb){
      cb.checked=(_spins.size===0||_spins.size===PIN_LIST.length)||_spins.has(cb.value);
    });
    if(typeof _cpRender==='function'){ _cpRender(); }
    if(typeof _cpUpdateSelCounts==='function') _cpUpdateSelCounts();
  },80);
}
function _syncCpToWm(){
  // Sync cp- selections back to wm- lot/wafer/FB tree (initial alignment)
  var cpLots=new Set(Array.from(document.querySelectorAll('#cp-lot input:checked')).map(function(c){return c.value;}));
  var cpWfrs=new Set(Array.from(document.querySelectorAll('#cp-wfr input:checked')).map(function(c){return +c.value;}));
  var cpFbs=new Set(Array.from(document.querySelectorAll('#cp-fb input:checked')).map(function(c){return +c.value;}));
  document.querySelectorAll('#wm-lot-wfr-tree input[data-type=lot]').forEach(function(cb){
    cb.checked=cpLots.size===0||cpLots.has(cb.value);
  });
  document.querySelectorAll('#wm-lot-wfr-tree input[data-type=wfr]').forEach(function(cb){
    cb.checked=cpWfrs.size===0||cpWfrs.has(+cb.value);
  });
  document.querySelectorAll('#wm-ib-fb-tree input[data-type=fb]').forEach(function(cb){
    cb.checked=cpFbs.size===0||cpFbs.has(+cb.value);
  });
}
function _wmGetSelLotsWfrs(){
  var selLots=new Set(),selWfrs=new Set(),selLotWfrs=new Set();
  document.querySelectorAll('#wm-lot-wfr-tree input[data-type=lot]:checked').forEach(function(cb){selLots.add(cb.value);});
  document.querySelectorAll('#wm-lot-wfr-tree input[data-type=wfr]:checked').forEach(function(cb){selWfrs.add(+cb.value);selLotWfrs.add(cb.dataset.lot+'|'+cb.value);});
  if(!selLots.size) document.querySelectorAll('#wm-lot-wfr-tree input[data-type=lot]').forEach(function(cb){selLots.add(cb.value);});
  if(!selWfrs.size) document.querySelectorAll('#wm-lot-wfr-tree input[data-type=wfr]').forEach(function(cb){selWfrs.add(+cb.value);selLotWfrs.add(cb.dataset.lot+'|'+cb.value);});
  return{lots:selLots,wfrs:selWfrs,lotWfrs:selLotWfrs};
}
function _wmGetSelFBs(){
  var selFBs=new Set();
  document.querySelectorAll('#wm-ib-fb-tree input[data-type=fb]:checked').forEach(function(cb){selFBs.add(+cb.value);});
  return selFBs;
}
function drawWM(){
  var selProgs=new Set(_wmChecked('wm-prog-cbs'));
  var _lw=_wmGetSelLotsWfrs();
  var selLots=_lw.lots, selWfrs=_lw.wfrs, selLotWfrs=_lw.lotWfrs;
  var selFBs=_wmGetSelFBs();
  var selPins=new Set(_wmChecked('wm-pin-cbs'));
  var passXY=[],b8XY=[];
  Object.entries(ALL_MAP).forEach(function(e){
    var parts=e[0].split('|');var prog=parts[0],lot=parts[1],wfr=+parts[2];
    if(!selProgs.has(prog)||!selLotWfrs.has(lot+'|'+wfr))return;
    e[1].forEach(function(d){
      if(!TARGET_IBINS.has(d[2])){passXY.push([d[0],d[1],prog,lot,wfr]);}
      else if(selFBs.has(d[3])){b8XY.push([d[0],d[1],d[3],prog,lot,wfr]);}
    });
  });
  var b8d=DIES.filter(function(d){
    return selProgs.has(d.prog)&&selLotWfrs.has(d.lot+'|'+d.wfr)&&selFBs.has(d.fbin)
      &&(selPins.size===0||selPins.size===PIN_LIST.length||d.pins.some(function(p){return selPins.has(p.pin);}));
  });
  if(selPins.size>0&&selPins.size<PIN_LIST.length){
    var bk=new Set(b8d.map(function(d){return d.prog+'|'+d.lot+'|'+d.wfr+'|'+d.x+'|'+d.y;}));
    b8XY=b8XY.filter(function(d){return bk.has(d[3]+'|'+d[4]+'|'+d[5]+'|'+d[0]+'|'+d[1]);});
  }
  function hover(d){
    var det=b8d.find(function(x){return x.x===d[0]&&x.y===d[1]&&x.lot===d[4];});
    if(!det)return '('+d[0]+','+d[1]+') FB '+d[2];
    return '('+det.x+','+det.y+') '+det.lot+' W'+det.wfr+'<br>FB '+det.fbin+'<br>Kill: '+det.kill+'<br>'+det.pins.slice(0,3).map(function(p){return p.pin+':'+p.val+'mV';}).join(', ');
  }
  document.getElementById('wm-cnt').textContent=b8XY.length+' BIN8 / '+passXY.length+' pass';
  _ddLabelUpdate('wm-lot-btn','wm-lot-wfr-tree','lot');
  _ddLabelUpdate('wm-fb-btn','wm-ib-fb-tree','ib');
  _ddLabelUpdate('wm-pin-btn','wm-pin-cbs','');
  // DevRevStep badge
  var drsSet=new Set(b8d.map(function(d){return d.drs||'';}));drsSet.delete('');
  var drsEl=document.getElementById('wm-drs-badge');
  if(drsEl) drsEl.textContent=drsSet.size?'DRS: '+[...drsSet].sort().join(', '):'';
  var _retToggle=document.getElementById('wm-ret-toggle');
  var _dielocToggle=document.getElementById('wm-dieloc-toggle');
  var _showRet=_retToggle&&_retToggle.checked&&_wmRetShots.length>0;
  var _showDieLoc=_dielocToggle&&_dielocToggle.checked&&_wmRetShots.length>0;
  // convert _wmRetShots [shotIdx,x0,y0,x1,y1] → wmRender format [[x0,y0,x1,y1],...] (integer coords)
  // renderer adds +1 to span so outline reaches outer edge of last die cell
  var _shots=_showRet?_wmRetShots.map(function(s){return[s[1],s[2],s[3],s[4]];}):[];
  // helper: show die detail panel
  function _showDieDetail(die){
    var ph=die.pins.map(function(p){var col=p.has_lim===false?'#ffd166':'#ff9999';var lbl=p.has_lim===false?'(no limit)':'USL '+p.usl;var chartBtn=PIN_DISTRIB[p.pin]?'<button onclick="_wmPinChart(\''+p.pin+'\')" style="background:#0d2540;border:1px solid #2a5080;color:#4ecdc4;border-radius:3px;padding:1px 7px;cursor:pointer;font-size:0.7rem" title="Show box/phase chart below">&#128202;</button>':'';return '<tr><td class="pin-tag pin-fail" style="color:'+col+'">'+p.pin+'</td><td style="color:'+col+'">'+p.val+' mV</td><td style="color:#667788">'+lbl+'</td><td>'+phaseBadge(p.phase)+'</td><td>'+chartBtn+'</td></tr>';}).join('');
    _ddiOpen(); _ddiTab('info');
    var _ddi=document.getElementById('die-detail-ident'); if(_ddi) _ddi.textContent='W'+die.wfr+' ('+die.lot+') X='+die.x+' Y='+die.y;
    var xeusRow=die.xeus_kill?'<span style="font-size:0.75rem;background:#1a2a3a;border:1px solid #2a4a6a;border-radius:4px;padding:1px 7px;color:#48cae4" title="Confirmed by TRACE/XEUS">\u2714 XEUS: '+die.xeus_kill.replace('CONT_','').replace(/_119325$/,'')+'</span>':'';
    var _ri=RETICLE_MAP[die.x+','+die.y];
    // _ri = [rdx=Reticle die-loc, rdy=0, shotIdx]
    var retRow=_ri?'<span style="font-size:0.75rem;background:#0d2020;border:1px solid #1a4040;border-radius:4px;padding:1px 7px;color:#80d4cc" title="Die-loc within reticle field \u00b7 Shot number">\u25c6 die-loc '+_ri[0]+' shot '+(_ri[2]+1)+'</span>':'';
    document.getElementById('ddi-panel-info').innerHTML='<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px"><span>W<b>'+die.wfr+'</b> ('+die.lot+')</span><span>X=<b>'+die.x+'</b> Y=<b>'+die.y+'</b></span>'+retRow+fbBadge(die.fbin)+phaseBadge(die.phase)+rtypeBadge(die.rtype)+'<span style="font-size:0.78rem;color:#8ab4d4">Kill: '+die.kill+'</span>'+xeusRow+'</div>'+(ph?'<table style="max-width:560px"><thead><tr><th>Pin</th><th>Measured (mV)</th><th>Limit</th><th>Phase</th><th></th></tr></thead><tbody>'+ph+'</tbody></table>':(die.kill?'<p style="color:#c08020;font-size:0.82rem">&#9888; Kill test identified (<b>'+die.phase+'</b>) \u2014 test exited via port\u00a02 (Alarm\u00a0Redirect) before completing the measurement sequence. No pin measurement is recorded in the CSV for alarm exits.</p>':'<p style="color:#445566;font-size:0.82rem">No pin violations detected</p>'));
  }
  // build tile containers, render each with wmRender()
  var _wkList=Object.keys(ALL_MAP).filter(function(k){var p=k.split('|');return selProgs.has(p[0])&&selLotWfrs.has(p[1]+'|'+p[2]);}).sort();
  var tilesHtml='';
  _wkList.forEach(function(wk,ti){
    var p=wk.split('|'),lot=p[1],wfr=+p[2];
    var b8cnt=ALL_MAP[wk].filter(function(d){
      if(!TARGET_IBINS.has(d[2])||!selFBs.has(d[3]))return false;
      if(selPins.size===0||selPins.size===PIN_LIST.length)return true;
      var det=b8d.find(function(x){return x.x===d[0]&&x.y===d[1]&&x.lot===lot&&x.wfr===wfr;});
      return det&&det.pins.some(function(p){return selPins.has(p.pin);});
    }).length;
    tilesHtml+='<div style="text-align:center">'
      +'<div style="font-size:9px;color:#8ab4d4;margin-bottom:2px">'+lot+' W'+wfr+'</div>'
      +'<div id="wmtile_'+ti+'"></div>'
      +(b8cnt?'<div style="font-size:9px;color:#ff8080;margin-top:1px">'+b8cnt+' BIN8</div>'
             :'<div style="font-size:9px;color:#3a5070;margin-top:1px">&mdash;</div>')
      +'</div>';
  });
  if(!_wkList.length)tilesHtml='<div style="color:#445566;padding:20px">No wafers selected</div>';
  var scDiv=document.getElementById('wm-scatter');
  scDiv.innerHTML=tilesHtml;
  _wkList.forEach(function(wk,ti){
    var p=wk.split('|'),lot=p[1],wfr=+p[2];
    var wDies=ALL_MAP[wk].map(function(d){return{x:d[0],y:d[1],ibin:d[2],fbin:d[3],lot:lot,wfr:wfr};});
    var divEl=document.getElementById('wmtile_'+ti);
    if(!divEl)return;
    wmRender(divEl,{
      dies:wDies,
      colorFn:function(d){
        if(!TARGET_IBINS.has(d.ibin)||!selFBs.has(d.fbin))return'#0d1520';
        if(selPins.size>0&&selPins.size<PIN_LIST.length){
          var det=b8d.find(function(x){return x.x===d.x&&x.y===d.y&&x.lot===d.lot&&x.wfr===d.wfr;});
          var mp=det?det.pins.filter(function(p){return selPins.has(p.pin);}):[];
          if(!mp.length)return'#0d1520';
          // color by worst pin ratio: green(1x) → yellow → red(≥2x)
          var ratio=mp.reduce(function(b,p){return p.usl&&p.val/p.usl>b?p.val/p.usl:b;},0);
          var t=Math.min(1,(ratio-1));// 0 at ratio=1x, 1 at ratio≥2x
          var r=Math.round(255),g=Math.round(255*(1-t)),b2=0;
          return 'rgb('+r+','+g+','+b2+')';
        }
        return fbColor(d.fbin);
      },
      tooltipFn:function(d){
        if(!TARGET_IBINS.has(d.ibin)||!selFBs.has(d.fbin))return null;
        var det=b8d.find(function(x){return x.x===d.x&&x.y===d.y&&x.lot===d.lot&&x.wfr===d.wfr;});
        if(!det)return'('+d.x+','+d.y+') IB '+d.ibin+' FB '+d.fbin;
        var _ri=RETICLE_MAP[d.x+','+d.y];
        var _sn=_ri?RETICLE_SITE_NUM[_ri[0]+','+_ri[1]]:null;
        var lines=[];
        lines.push('<b style="color:#8ab4d4">'+det.lot+'</b>'
          +' &nbsp;W<b>'+det.wfr+'</b>'
          +' &nbsp;('+det.x+','+det.y+')'
          +(_sn!=null?' &nbsp;site&nbsp;<b>'+_sn+'</b>':''));
        lines.push('<span style="color:#aaa">IB:</span> <b style="color:#ff6b6b">'+TARGET_IBIN+'</b>'
          +' &nbsp;<span style="color:#aaa">FB:</span> <b>'+det.fbin+'</b>'
          +' &nbsp;<span style="color:#aaa">DB:</span> '+det.dbin);
        lines.push('<span style="color:#aaa">Phase:</span> '+det.phase
          +' &nbsp;<span style="color:#aaa">Rail:</span> '+det.rtype);
        lines.push('<span style="color:#aaa">Kill test:</span> <span style="color:#ffd166">'+det.kill+'</span>');
        if(det.kill_full&&det.kill_full!==det.kill){
          lines.push('<span style="color:#778899;font-size:10px">'+det.kill_full+'</span>');
        }
        if(det.pins&&det.pins.length){
          lines.push('<hr style="border:none;border-top:1px solid #2a4060;margin:3px 0">');
          lines.push('<span style="color:#aaa">Pin failures:</span>');
          det.pins.slice(0,6).forEach(function(p){
            var bar='';
            if(p.usl!=null){
              var pct=Math.min(100,Math.round(p.val/p.usl*100));
              bar=' <span style="display:inline-block;width:'+Math.min(40,pct*0.4)+'px;height:5px;background:'+(pct>=100?'#ff6b6b':'#ffd166')+';vertical-align:middle;border-radius:2px"></span>';
            }
            lines.push('&nbsp;&nbsp;<b style="color:#c0e0ff">'+p.pin+'</b>'
              +' <span style="color:#ffd166">'+p.val+'mV</span>'
              +(p.usl!=null?' / USL:'+p.usl+'mV':'')
              +bar
              +' <span style="color:#778899;font-size:10px">'+p.phase+'</span>');
          });
          if(det.pins.length>6)lines.push('&nbsp;&nbsp;<span style="color:#556677">…+'+(det.pins.length-6)+' more</span>');
        }
        return lines.join('<br>');
      },
      retShots:_shots,
      retShotLabels:false,
      retMap:_showRet?RETICLE_MAP:{},
      retSiteNum:_showDieLoc?RETICLE_SITE_NUM:{},
      width:200,
      bgColor:'none',
      borderColor:'#a0bcd8',
      shotColor:'#2471a3',
    });
    // click → show die detail panel
    (function(wDies,lot,wfr){
      var svg=divEl.querySelector('svg');
      if(!svg)return;
      svg.addEventListener('click',function(ev){
        var t=ev.target;
        if(t.tagName!=='rect'||!t.hasAttribute('data-i'))return;
        var d=wDies[+t.getAttribute('data-i')];
        if(!d||!TARGET_IBINS.has(d.ibin)||!selFBs.has(d.fbin))return;
        if(selPins.size>0&&selPins.size<PIN_LIST.length){
          var det2=b8d.find(function(x){return x.x===d.x&&x.y===d.y&&x.lot===lot&&x.wfr===wfr;});
          if(!(det2&&det2.pins.some(function(p){return selPins.has(p.pin);})))return;
        }
        var die=b8d.find(function(x){return x.x===d.x&&x.y===d.y&&x.lot===lot&&x.wfr===wfr;});
        if(die)_showDieDetail(die);
      });
    })(wDies,lot,wfr);
  });
  Plotly.react('wm-bar',[{type:'bar',x:WFR_LIST.map(function(w){return 'W'+w.wfr;}),y:WFR_LIST.map(function(w){return w.count;}),marker:{color:WFR_LIST.map(function(w){return selWfrs.has(w.wfr)?'#ffd166':'#4a9fd4';})},text:WFR_LIST.map(function(w){return w.count;}),textposition:'outside',hovertemplate:'W%{x}: %{y} BIN8<extra></extra>'}],L({title:'BIN8 per Wafer',xaxis:{tickangle:-45},yaxis:{title:'BIN8 count'},margin:{t:40,l:50,r:20,b:90}}),PC);
  updatePatternPanel(b8XY,passXY);
}

// ── PATTERN RECOGNITION (wafer_pattern module — single source of truth) ──────
__PATTERN_SCORE_JS__
var _patConcl={
  CENTER:'<b>Center pattern</b> — Fails concentrated at wafer center. VCC continuity root causes: <b>CMP over-polish at center</b> (pad wear hotspot reduces bump height → high resistance), <b>CVD/PVD center-thick film</b> (showerhead center zone drift on barrier/seed layers), <b>plasma center bias</b> (ICP density peak → localized etch damage). Recommend: CMP head pressure profile, film thickness map center trend.',
  EDGE:'<b>Edge pattern</b> — Fails concentrated near wafer edge (&gt;70% radius). VCC continuity root causes: <b>edge exclusion process non-uniformity</b> (resist pull-back, deposition shadowing, etch over-etch), <b>wafer handling damage</b> (edge contact chipping → bump damage), <b>BEOL seam cracking</b> at field edge from CTE mismatch. Recommend: edge yield trend by DieX/DieY ring, handler contact map.',
  DONUT:'<b>Donut pattern</b> — Annular ring at 40–70% radius. VCC continuity root causes: <b>CMP carrier head zone boundary</b> (mid-zone pressure ring artifact → dishing non-uniformity), <b>showerhead annular flow ring</b> (CVD gas injection at mid-radius), <b>plasma standing wave</b> at ICP mode transition radius, <b>Marangoni ring</b> in spin-coat (solvent evaporation ring). Recommend: CMP radial profile, film uniformity scan.',
  SYSTEMATIC:'<b>Systematic / spatial cluster</b> — The <i>same die XY coordinates</i> fail repeatedly across multiple wafers and lots. In a composite map this is the most actionable pattern: random defects would scatter; a persistent spatial hotspot points to a <b>fixed process non-uniformity</b> that is wafer-position-dependent.<br><br>'+
    '<b>VCC continuity root causes to investigate:</b><br>'+
    '&bull; <b>Reticle field site bias</b> — if all clustered dies originated from the same reticle field position (same LayoutX/LayoutY), suspect a <b>mask particle, OPC error, or CD non-uniformity</b> at that field location. Use the <i>Reticle Sites</i> panel to check site concentration.<br>'+
    '&bull; <b>Handler socket / nest defect</b> — a chipped or contaminated socket damages bump pads on dies at a fixed handler pocket position; shows up as a persistent cluster that shifts or disappears when the handler is swapped.<br>'+
    '&bull; <b>CMP or film deposition spatial gradient</b> — if the cluster is off-center but not edge-ring, suspect a <i>showerhead spoke</i>, carrier head retaining ring asymmetry, or polishing pad groove leaving systematic thickness non-uniformity at a fixed wafer sector.<br>'+
    '&bull; <b>Chuck / platen thermal zone</b> — localized temperature non-uniformity on the test chuck elevates contact resistance at a fixed wafer quadrant. Check if the cluster aligns with a known platen zone boundary.<br>'+
    '&bull; <b>Wafer-level ID / tracking artifact</b> — if the cluster correlates with wafer orientation marks or ID notch position, suspect handling contact or ID laser damage at a fixed edge sector.<br><br>'+
    '<b>Next steps:</b> (1) Overlay the fail cluster on the <i>Reticle Site map</i> — if clustered dies share the same reticle field position, that field location is the prime suspect (mask/litho defect). (2) Use <i>Freq&nbsp;% Map</i> to confirm the cluster is stable across lots (not a one-lot excursion). (3) Check if the cluster moves when a different handler or socket nest is used (handler swap experiment).',
  RETICLE:'<b>Reticle-level systematic</b> — Same within-reticle site (LayoutX, LayoutY) fails across multiple shots. VCC continuity root causes: <b>mask particle at a fixed reticle location</b> → repeating via/contact CD defect in bump pad or UBM layer, <b>OPC error on weak VCC bump feature</b>, <b>reticle CD non-uniformity</b> at specific field position. Examine fails by Reticle number — if one reticle dominates, escalate to litho/reticle team.',
  RANDOM:'<b>Random / no dominant spatial pattern</b> — Fails scattered across the wafer with no strong radial or quadrant bias. VCC continuity interpretation: likely <b>random particle defects</b> (bump-level contamination or via opens), <b>independent per-die alarm events</b> (power-on transient), or <b>mixed root causes</b> across multiple lots. Low fail count may also prevent pattern detection — check n count.'
};
function updatePatternPanel(b8XY,passXY,barsId,conclId){
  var panel=document.getElementById(barsId||'wm-pat-bars');
  var concl=document.getElementById(conclId||'wm-pat-conclusion');
  if(!panel||!concl)return;
  var n=b8XY.length;
  if(!n){
    panel.innerHTML='<span style="color:#445566;font-size:0.78rem">No BIN8 dies visible</span>';
    concl.innerHTML='';return;
  }
  // Normalise to unit radius
  var failXn=b8XY.map(function(d){return d[0]/WFR_RADIUS;});
  var failYn=b8XY.map(function(d){return d[1]/WFR_RADIUS;});
  var sc=_wmScorePattern(failXn,failYn);
  sc.reticle=_wmScoreReticle(b8XY.map(function(d){return d[0];}),b8XY.map(function(d){return d[1];}),RETICLE_MAP,RETICLE_SITE_TOTALS);
  var primary=_wmPrimary(sc);
  var order=['center','edge','donut','systematic','reticle','random'];
  var html='<div style="font-size:0.72rem;color:#556677;margin-bottom:6px">n='+n+' BIN8 | Primary: <b style="color:'+(_pColors[primary]||'#c0ccd8')+'">'+primary+'</b></div>';
  order.forEach(function(k){
    var v=sc[k]||0;var pct=Math.round(v*100);
    var barW=Math.round(v*160);
    var col=v<0.35?'#27ae60':v<0.65?'#e67e22':'#c0392b';
    var lbl={center:'Center',edge:'Edge',donut:'Donut',systematic:'Systematic',reticle:'Reticle',random:'Random'}[k];
    html+='<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">'
      +'<span style="width:80px;font-size:0.75rem;color:'+(_pColors[k]||'#9ab4cc')+';text-align:right">'+lbl+'</span>'
      +'<div style="width:160px;height:10px;background:#1a2235;border-radius:5px;overflow:hidden">'
      +'<div style="width:'+barW+'px;height:100%;background:'+col+';border-radius:5px;transition:width 0.3s"></div></div>'
      +'<span style="font-size:0.73rem;color:#8ab4d4;width:34px">'+pct+'%</span>'
      +'</div>';
  });
  panel.innerHTML=html;
  concl.innerHTML=(_patConcl[primary]||'')+
    (sc.reticle>=0.4&&primary!=='RETICLE'?'<br><br><span style="color:#c77dff">&#9670; Also elevated Reticle score ('+Math.round(sc.reticle*100)+'%) — check if fails cluster at specific LayoutX/Y positions across reticle shots.</span>':'')+
    (sc.systematic>=0.4?'<br><br>'+_buildSystTable(b8XY):'');
}

// Build a grouped-by-XY fail location table for the systematic conclusion
var _hlSel=new Set();  // persists across table rebuilds: "x,y" keys
function _buildSystTable(b8XY){
  var grp={};
  b8XY.forEach(function(d){
    var k=d[0]+','+d[1];
    if(!grp[k]){grp[k]={x:d[0],y:d[1],count:0,wafers:[]};}
    grp[k].count++;
    var wlbl=(d[4]||'?')+' W'+(d[5]||'?');
    if(grp[k].wafers.indexOf(wlbl)===-1)grp[k].wafers.push(wlbl);
  });
  var rows=Object.values(grp).filter(function(r){return r.count>1;})
    .sort(function(a,b){return b.count-a.count;});
  if(!rows.length)return'';
  var uid='syst-tbl-'+Date.now();
  var tbl='<div id="'+uid+'" style="display:none;margin-top:8px;max-height:220px;overflow-y:auto">'
    +'<div style="font-size:0.72rem;color:#556677;margin-bottom:4px">Click rows to toggle highlight &mdash; multi-select supported.</div>'
    +'<table style="border-collapse:collapse;width:100%;font-size:0.75rem">'
    +'<thead><tr style="background:#0d1a2a;color:#8ab4d4">'
    +'<th style="padding:3px 6px;width:18px"></th>'
    +'<th style="padding:3px 8px;text-align:left">Die XY</th>'
    +'<th style="padding:3px 8px;text-align:right">Hits</th>'
    +'<th style="padding:3px 8px;text-align:left">Wafers</th>'
    +'</tr></thead><tbody>';
  rows.forEach(function(r,i){
    var bg=i%2===0?'#0d1520':'#111a28';
    var k=r.x+','+r.y;
    var sel=_hlSel.has(k);
    tbl+='<tr id="sr_'+k.replace(',','_')+'" data-k="'+k+'" style="background:'+(sel?'#1a3a1a':bg)+';cursor:pointer;outline:'+(sel?'1px solid #4ecdc4':'none')+'" onclick="_toggleSystRow(this,'+r.x+','+r.y+')">'
      +'<td style="padding:3px 6px;text-align:center;color:#4ecdc4">'+(sel?'&#10003;':'')+'</td>'
      +'<td style="padding:3px 8px;color:#ffd166;font-family:monospace">('+r.x+',&thinsp;'+r.y+')</td>'
      +'<td style="padding:3px 8px;text-align:right;color:#e67e22;font-weight:700">'+r.count+'</td>'
      +'<td style="padding:3px 8px;color:#9ab4cc">'+_fmtWaferBadges(r.wafers)+'</td>'
      +'</tr>';
  });
  tbl+='</tbody></table></div>';
  var btn='<button data-tgt="'+uid+'" data-n="'+rows.length+'" onclick="_toggleSystTbl(this)"'
    +' style="margin-top:8px;font-size:0.75rem;background:#0d1a2a;border:1px solid #2a4060;'
    +'color:#4a9fd4;border-radius:4px;padding:3px 10px;cursor:pointer">'
    +'&#9654; Show repeated die locations ('+rows.length+' XY positions)</button>';
  return btn+tbl;
}
function _toggleSystTbl(btn){
  var w=document.getElementById(btn.dataset.tgt);
  if(!w)return;
  var open=w.style.display==='none';
  w.style.display=open?'block':'none';
  btn.innerHTML=open?'&#9660; Hide repeated die locations'
    :'&#9654; Show repeated die locations ('+btn.dataset.n+' XY positions)';
}
function _toggleSystRow(tr,x,y){
  var k=x+','+y;
  if(_hlSel.has(k)){
    _hlSel.delete(k);
    tr.style.background=tr.sectionRowIndex%2===0?'#0d1520':'#111a28';
    tr.style.outline='none';
    tr.cells[0].innerHTML='';
  } else {
    _hlSel.add(k);
    tr.style.background='#1a3a1a';
    tr.style.outline='1px solid #4ecdc4';
    tr.cells[0].innerHTML='&#10003;';
  }
  _applyHighlights();
}
function _applyHighlights(){
  document.querySelectorAll('rect[data-hl]').forEach(function(r){
    r.removeAttribute('stroke');r.removeAttribute('stroke-width');r.removeAttribute('data-hl');
  });
  if(!_hlSel.size)return;
  var firstEl=null;
  _hlSel.forEach(function(k){
    var p=k.split(',');
    document.querySelectorAll('#wm-scatter rect[data-x="'+p[0]+'"][data-y="'+p[1]+'"], #comp-svg rect[data-x="'+p[0]+'"][data-y="'+p[1]+'"], #tiles-div rect[data-x="'+p[0]+'"][data-y="'+p[1]+'"]').forEach(function(r){
      r.setAttribute('stroke','#ffff00');r.setAttribute('stroke-width','2');r.setAttribute('data-hl','1');
      if(!firstEl)firstEl=r;
    });
  });
  if(firstEl)firstEl.closest('svg').scrollIntoView({behavior:'smooth',block:'nearest'});
}
function _highlightDieXY(x,y){
  document.querySelectorAll('rect[data-hl]').forEach(function(r){
    r.removeAttribute('stroke');r.removeAttribute('stroke-width');r.removeAttribute('data-hl');
  });
  var all=Array.from(document.querySelectorAll('#wm-scatter rect[data-x="'+x+'"][data-y="'+y+'"], #comp-svg rect[data-x="'+x+'"][data-y="'+y+'"], #tiles-div rect[data-x="'+x+'"][data-y="'+y+'"]'));
  if(!all.length){alert('No visible die at ('+x+', '+y+') in current filter.');return;}
  all.forEach(function(r){r.setAttribute('stroke','#ffff00');r.setAttribute('stroke-width','2');r.setAttribute('data-hl','1');});
  all[0].closest('svg').scrollIntoView({behavior:'smooth',block:'nearest'});
  setTimeout(function(){document.querySelectorAll('rect[data-hl]').forEach(function(r){r.removeAttribute('stroke');r.removeAttribute('stroke-width');r.removeAttribute('data-hl');});},4000);
}


// ── COMPOSITE OVERLAY FUNCTIONS ────────────────────────────────────────────────
var _cpPAD=3,_cpTW_LG=488,_cpTW_SM=240;
function _cpChk(id){return Array.from(document.getElementById(id).querySelectorAll('input:checked')).map(function(c){return c.value;});}
function _cpTogAll(id,v){document.getElementById(id).querySelectorAll('input').forEach(function(c){c.checked=v;});_cpRender();_cpUpdateSelCounts();}
function _cpFilterDrop(id,q){
  var lc=q.toLowerCase();
  document.getElementById(id).querySelectorAll('label.cp-cb').forEach(function(lbl){
    lbl.style.display=(!q||lbl.textContent.toLowerCase().includes(lc))?'':'none';
  });
}
function _cpTogDrop(panelId){
  var p=document.getElementById(panelId);
  if(!p)return;
  var wasOpen=p.classList.contains('open');
  // close all other dropdowns first
  document.querySelectorAll('.cp-dd-panel.open').forEach(function(el){el.classList.remove('open');});
  if(!wasOpen)p.classList.add('open');
}
// close dropdowns when clicking outside
document.addEventListener('click',function(ev){
  if(!ev.target.closest('.cp-dd-wrap'))
    document.querySelectorAll('.cp-dd-panel.open').forEach(function(el){el.classList.remove('open');});
});
function _cpUpdateSelCounts(){
  var _map=[['cp-prog','cp-prog-sel-cnt'],['cp-lot','cp-lot-sel-cnt'],
            ['cp-wfr','cp-wfr-sel-cnt'],['cp-fb','cp-fb-sel-cnt'],['cp-pin','cp-pin-sel-cnt']];
  _map.forEach(function(m){
    var el=document.getElementById(m[1]);if(!el)return;
    var all=document.getElementById(m[0]).querySelectorAll('input');
    var chk=document.getElementById(m[0]).querySelectorAll('input:checked');
    el.textContent=chk.length===all.length?'(all)':'('+chk.length+'/'+all.length+')';
    el.style.color=chk.length===all.length?'#445566':'#ffd166';
  });
}
function _cpMkCb(id,val,lbl,col,chkd){
  var w=document.createElement('label');w.className='cp-cb';w.style.color=col||'#c0ccd8';
  var c=document.createElement('input');c.type='checkbox';c.value=val;c.checked=chkd;
  c.style.accentColor=col||'#4a9fd4';c.addEventListener('change',function(){_cpRender();_cpUpdateSelCounts();});
  w.appendChild(c);w.appendChild(document.createTextNode(' '+lbl));
  document.getElementById(id).appendChild(w);}
function _cpContrast(hex){
  if(!hex||hex[0]!=='#')return'#fff';
  var h=hex.length===4?hex[1]+hex[1]+hex[2]+hex[2]+hex[3]+hex[3]:hex.slice(1);
  var lum=0.299*parseInt(h.slice(0,2),16)/255+0.587*parseInt(h.slice(2,4),16)/255+0.114*parseInt(h.slice(4,6),16)/255;
  return lum>0.55?'#000':'#fff';}
function _cpGeom(TW){
  var xMin=_wmXMin,xMax=_wmXMax,yMin=_wmYMin,yMax=_wmYMax;
  var xCnt=xMax-xMin+1,yCnt=yMax-yMin+1,xSpan=xMax-xMin,ySpan=yMax-yMin;
  var PAD=_cpPAD;
  var cs=(TW-PAD*2)/xCnt,csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;
  var TH=Math.round(yCnt*csy+PAD*2),xCtr=(xMin+xMax)/2,yCtr=(yMin+yMax)/2;
  var xRad=(xMax-xMin)/2||1,yRad=(yMax-yMin)/2||1;
  return{cs:cs,csy:csy,TH:TH,PAD:PAD,xMin:xMin,xMax:xMax,yMin:yMin,yMax:yMax,
    eRx:+(xRad*cs+cs*0.5).toFixed(1),eRy:+(yRad*csy+csy*0.5).toFixed(1),
    eCx:+(PAD+(xCtr-xMin)*cs+cs*0.5).toFixed(1),eCy:+(PAD+(yMax-yCtr)*csy+csy*0.5).toFixed(1)};}
function _cpRetSvg(g,showLabel,showShots){
  if(!showShots||!_wmRetShots||!_wmRetShots.length)return '';var s='';
  _wmRetShots.forEach(function(sh){
    var rn=sh[0],x0=sh[1],y0=sh[2],x1=sh[3],y1=sh[4];
    var sx=(g.PAD+(x0-g.xMin)*g.cs).toFixed(1),sy=(g.PAD+(g.yMax-y1)*g.csy).toFixed(1);
    var sw=((x1-x0+1)*g.cs).toFixed(1),sh2=((y1-y0+1)*g.csy).toFixed(1);
    s+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh2+'" fill="none" stroke="rgba(180,140,255,0.65)" stroke-width="1.5" stroke-dasharray="4,2"/>';
    if(showLabel){
      // true centre of the shot rect
      var lx=(g.PAD+((x0+x1)/2-g.xMin)*g.cs+g.cs*0.5).toFixed(1);
      var ly=(g.PAD+(g.yMax-(y0+y1)/2)*g.csy+g.csy*0.5).toFixed(1);
      s+='<text x="'+lx+'" y="'+ly+'" text-anchor="middle" dominant-baseline="middle" font-size="10" fill="rgba(210,170,255,0.95)" font-weight="bold" stroke="#0a0f1a" stroke-width="2" paint-order="stroke">'+String(rn+1)+'</text>';
    }
  });return s;}
function _cpTileColor(isB8,show,fbin,cm,di,spi){
  if(!isB8)return '#0d1520';if(!show)return '#142030';
  if(cm==='phase')return di?(PHASE_COL[di.phase]||'#556677'):'#556677';
  if(cm==='rtype')return di?(RTYPE_COL[di.rtype]||'#556677'):'#556677';
  if(cm==='pin'){
    if(!di||!di.pins.length)return '#0d1520';
    var pd=spi.size>0&&spi.size<PIN_LIST.length?di.pins.filter(function(p){return spi.has(p.pin);}):di.pins;
    if(!pd.length)return '#0d1520';
    // heatmap: worst ratio above USL → green(1x)→yellow→red(≥2x)
    var ratio=pd.reduce(function(b,p){return p.usl&&p.val/p.usl>b?p.val/p.usl:b;},1);
    var t=Math.min(1,Math.max(0,(ratio-1)));// 0 at 1x, 1 at ≥2x
    var rr=255,gg=Math.round(255*(1-t)),bb=0;
    return 'rgb('+rr+','+gg+','+bb+')';}
  return fbColor(fbin);}
// ── Pre-compute pixel positions for all die locations (called once) ───────────
var _cpPixCache=null,_cpPixGeomKey=null;
function _cpEnsurePixCache(){
  var gL=_cpGeom(_cpTW_LG);
  var gKey=gL.cs.toFixed(4)+','+gL.csy.toFixed(4)+','+gL.xMin+','+gL.yMax;
  if(_cpPixCache&&_cpPixGeomKey===gKey) return _cpPixCache;
  var dw=(gL.cs*0.9).toFixed(1), dh=(gL.csy*0.9).toFixed(1);
  var diePx={};
  // collect all unique die positions across ALL_MAP
  Object.values(ALL_MAP).forEach(function(ds){
    ds.forEach(function(d){
      var k=d[0]+','+d[1];
      if(!diePx[k]) diePx[k]=[
        (gL.PAD+(d[0]-gL.xMin)*gL.cs).toFixed(1),
        (gL.PAD+(gL.yMax-d[1])*gL.csy).toFixed(1)
      ];
    });
  });
  _cpPixCache={gL:gL,dw:dw,dh:dh,diePx:diePx};
  _cpPixGeomKey=gKey;
  return _cpPixCache;
}
function _cpRender(){
  var sp=new Set(_cpChk('cp-prog')),sl=new Set(_cpChk('cp-lot'));
  var sw=new Set(_cpChk('cp-wfr').map(Number)),sf=new Set(_cpChk('cp-fb').map(Number));
  var spi=new Set(_cpChk('cp-pin'));
  var cmEl=document.getElementById('wm-color');  // use shared horizontal-bar color select
  var cm=cmEl?cmEl.value:'fbin';
  var dim={};DIES.forEach(function(d){
    if(sp.has(d.prog)&&sl.has(d.lot)&&sw.has(d.wfr)&&sf.has(d.fbin))dim[d.x+','+d.y+','+d.lot+','+d.wfr]=d;});
  var posMap={},nW=0;
  Object.keys(ALL_MAP).filter(function(k){var p=k.split('|');return sp.has(p[0])&&sl.has(p[1])&&sw.has(+p[2]);}).forEach(function(wk){
    var pp=wk.split('|'),lot=pp[1],wfr=+pp[2];nW++;
    ALL_MAP[wk].forEach(function(d){
      if(TARGET_IBINS.has(d[2])&&sf.has(d[3])){
        var di=dim[d[0]+','+d[1]+','+lot+','+wfr];
        if(spi.size>0&&spi.size<PIN_LIST.length&&!(di&&di.pins.some(function(p){return spi.has(p.pin);})))return;
        var key=d[0]+','+d[1];
        if(!posMap[key]){posMap[key]={hits:0,fb:d[3],rep:di||null,wk:new Set()};}
        posMap[key].hits++;posMap[key].wk.add(wk);if(!posMap[key].rep&&di)posMap[key].rep=di;}});});;
  if(!nW)nW=1;
  window._cpLastPosMap=posMap;window._cpLastNW=nW;
  var _showRetCp=(function(){var t=document.getElementById('wm-ret-toggle');return t&&t.checked&&_wmRetShots.length>0;})();
  // Use pre-cached pixel positions — avoids recomputing toFixed() for every die on every render
  var _pc=_cpEnsurePixCache();
  var gL=_pc.gL, dw=_pc.dw, dh=_pc.dh, _diePx=_pc.diePx;
  var rL=_cpRetSvg(gL,true,_showRetCp);
  var ci='cpc0';
  var cc='<defs><clipPath id="'+ci+'"><ellipse cx="'+gL.eCx+'" cy="'+gL.eCy+'" rx="'+gL.eRx+'" ry="'+gL.eRy+'"/></clipPath></defs>';
  var cb='<ellipse cx="'+gL.eCx+'" cy="'+gL.eCy+'" rx="'+gL.eRx+'" ry="'+gL.eRy+'" fill="none" stroke="#a0bcd8" stroke-width="3"/>';
  var ad=new Set();Object.values(ALL_MAP).forEach(function(ds){ds.forEach(function(d){ad.add(d[0]+','+d[1]);});});
  var cr=[];ad.forEach(function(k){
    var xy=_diePx[k];if(!xy)return;
    var px=xy[0],py=xy[1];
    var x=+k.split(',')[0],y=+k.slice(k.indexOf(',')+1);
    var pm=posMap[k];var fl,op;
    if(!pm){fl='#0d1828';op='0.7';}
    else{
      var t=pm.hits/nW;
      if(cm==='freq'){fl=_cpFreqColor(t);op='1';}
      else if(cm==='site'){var _rm0=RETICLE_MAP[k];fl=_cpSiteColor(_rm0?RETICLE_SITE_NUM[_rm0[0]+','+_rm0[1]]:null);op=(Math.min(1,Math.max(0.5,t+0.4))).toFixed(2);}
      else{op=(Math.min(1,Math.max(0.4,t+0.35))).toFixed(2);fl=_cpTileColor(true,true,pm.fb,cm,pm.rep,spi);}
    }
    cr.push('<rect x="'+px+'" y="'+py+'" width="'+dw+'" height="'+dh+'" fill="'+fl+'" opacity="'+op+'" data-x="'+x+'" data-y="'+y+'"/>');    var _rm2=RETICLE_MAP[k];if(_rm2&&document.getElementById('wm-dieloc-toggle')&&document.getElementById('wm-dieloc-toggle').checked){var _sn2=RETICLE_SITE_NUM[_rm2[0]+','+_rm2[1]];if(_sn2!=null){var _nfs2=Math.max(4,Math.min(9,Math.round(gL.cs*0.4)));var _tc2=_cpContrast(fl);cr.push('<text x="'+(parseFloat(px)+gL.cs*0.45).toFixed(1)+'" y="'+(parseFloat(py)+gL.csy*0.5+_nfs2*0.35).toFixed(1)+'" text-anchor="middle" font-size="'+_nfs2+'" fill="'+_tc2+'" stroke="'+(_tc2==='#fff'?'#0a0f1a':'#f5faff')+'" stroke-width="0.8" paint-order="stroke" font-weight="bold" pointer-events="none">'+_sn2+'</text>');}}});
  var nPos=Object.keys(posMap).length;
  document.getElementById('comp-svg').innerHTML=
    '<svg xmlns="http://www.w3.org/2000/svg" width="'+_cpTW_LG+'" height="'+gL.TH+'">'+cc+'<g clip-path="url(#'+ci+')">'+cr.join('')+rL+'</g>'+cb+'</svg>';
  document.getElementById('comp-legend').innerHTML=
    '<b>'+nPos+'</b> BIN8 pos \u00b7 <b>'+nW+'</b> wafer'+(nW!==1?'s':'')+' \u00b7 color: '+cm;
  // individual tiles via wmRender() — identical rendering to main-page tiles
  var _showRetTile=(function(){var t=document.getElementById('wm-ret-toggle');return t&&t.checked;})();
  var _cpShots=_showRetTile?_wmRetShots.map(function(s){return[s[1],s[2],s[3],s[4]];}):[];
  var _cpWkList=Object.keys(ALL_MAP).filter(function(k){var p=k.split('|');return sp.has(p[0])&&sl.has(p[1])&&sw.has(+p[2]);}).sort();
  var _cpTD=[];var th='';
  _cpWkList.forEach(function(wk,ti){
    var p=wk.split('|'),lot=p[1],wfr=+p[2];
    var b8c=ALL_MAP[wk].filter(function(d){
      if(!TARGET_IBINS.has(d[2])||!sf.has(d[3]))return false;
      if(spi.size===0||spi.size===PIN_LIST.length)return true;
      var di2=dim[d[0]+','+d[1]+','+lot+','+wfr];return!!(di2&&di2.pins.some(function(pin){return spi.has(pin.pin);}));
    }).length;
    _cpTD.push({wk:wk,lot:lot,wfr:wfr,ti:ti,b8c:b8c});
    th+='<div style="text-align:center;margin:4px">'
      +'<div style="font-size:9px;color:#8ab4d4;margin-bottom:2px">'+lot+' W'+wfr+'</div>'
      +'<div id="cptile_'+ti+'"></div>'
      +(b8c?'<div style="font-size:9px;color:#ff8080;margin-top:1px">'+b8c+' BIN8</div>'
            :'<div style="font-size:9px;color:#3a5070;margin-top:1px">&mdash;</div>')
      +'</div>';
  });
  if(!th)th='<div style="color:#445566;padding:20px">No wafers selected</div>';
  document.getElementById('tiles-div').innerHTML=th;
  document.getElementById('tiles-hdr').textContent='Per-Wafer Tiles ('+nW+' wafer'+(nW!==1?'s':'')+')';
  // ── Pattern score update when cp-pat-section is visible ──
  var _cpPat=document.getElementById('cp-pat-section');
  if(_cpPat&&_cpPat.style.display!=='none'){
    var _cpB8=[],_cpPass=[];
    Object.keys(ALL_MAP).forEach(function(wk){
      var pp=wk.split('|');
      if(!sp.has(pp[0])||!sl.has(pp[1])||!sw.has(+pp[2]))return;
      var prog=pp[0],lot=pp[1],wfr=+pp[2];
      ALL_MAP[wk].forEach(function(d){
        if(TARGET_IBINS.has(d[2])&&sf.has(d[3])){_cpB8.push([d[0],d[1],d[3],prog,lot,wfr]);}
        else{_cpPass.push([d[0],d[1]]);}
      });
    });
    updatePatternPanel(_cpB8,_cpPass,'cp-pat-bars','cp-pat-conclusion');
  }
  // ── DPS site analysis update ──
  var _cpSite=document.getElementById('cp-site-section');
  if(_cpSite&&_cpSite.style.display!=='none'){
    document.getElementById('cp-site-body').innerHTML=_cpBuildSiteTable(posMap,nW);
  }
  // ── Reticle shot analysis update ──
  var _cpShot=document.getElementById('cp-shot-section');
  if(_cpShot&&_cpShot.style.display!=='none'){
    try{document.getElementById('cp-shot-body').innerHTML=_cpBuildShotTable(posMap,nW);}
    catch(e){document.getElementById('cp-shot-body').innerHTML='<span style="color:#ff6b6b;font-size:0.78rem">Shot analysis error: '+e.message+'</span>';}
  }
  // ── Render per-wafer tiles asynchronously in batches of 8 to keep UI responsive
  var _cpRenderSeq=(_cpRenderSeq||0)+1; var _seq=_cpRenderSeq;
  (function _batchTiles(idx){
    if(_seq!==_cpRenderSeq)return;  // cancelled by newer render
    var bEnd=Math.min(idx+8,_cpTD.length);
    for(var i=idx;i<bEnd;i++){
      var t=_cpTD[i];
      var divEl=document.getElementById('cptile_'+t.ti);
      if(!divEl)continue;
      var wDies=ALL_MAP[t.wk].map(function(d){return{x:d[0],y:d[1],ibin:d[2],fbin:d[3],lot:t.lot,wfr:t.wfr};});
      (function(lot,wfr){
        wmRender(divEl,{
          dies:wDies,
          colorFn:function(d){
            var isB8=TARGET_IBINS.has(d.ibin)&&sf.has(d.fbin);
            if(!isB8)return'#0d1520';
            var di=dim[d.x+','+d.y+','+lot+','+wfr];
            var show=spi.size===0||spi.size===PIN_LIST.length||!!(di&&di.pins.some(function(p){return spi.has(p.pin);}));
            if(cm==='freq'){var pm2=posMap[d.x+','+d.y];return pm2?_cpFreqColor(pm2.hits/nW):'#192538';}
            if(cm==='site'){var _rm3=RETICLE_MAP[d.x+','+d.y];return _cpSiteColor(_rm3?RETICLE_SITE_NUM[_rm3[0]+','+_rm3[1]]:null);}
            return _cpTileColor(true,show,d.fbin,cm,di,spi);
          },
          tooltipFn:function(d){
            if(!TARGET_IBINS.has(d.ibin)||!sf.has(d.fbin))return null;
            var di=dim[d.x+','+d.y+','+d.lot+','+d.wfr];
            if(!di)return'('+d.x+','+d.y+') IB '+d.ibin+' FB '+d.fbin;
            var _ri=RETICLE_MAP[d.x+','+d.y];
            var _sn=_ri?RETICLE_SITE_NUM[_ri[0]+','+_ri[1]]:null;
            var lines=[];
            lines.push('<b style="color:#8ab4d4">'+di.lot+'</b>'
              +' &nbsp;W<b>'+di.wfr+'</b>'
              +' &nbsp;('+di.x+','+di.y+')'
              +(_sn!=null?' &nbsp;site&nbsp;<b>'+_sn+'</b>':''));
            lines.push('<span style="color:#aaa">IB:</span> <b style="color:#ff6b6b">'+TARGET_IBIN+'</b>'
              +' &nbsp;<span style="color:#aaa">FB:</span> <b>'+di.fbin+'</b>'
              +' &nbsp;<span style="color:#aaa">DB:</span> '+di.dbin);
            lines.push('<span style="color:#aaa">Phase:</span> '+di.phase
              +' &nbsp;<span style="color:#aaa">Rail:</span> '+di.rtype);
            lines.push('<span style="color:#aaa">Kill test:</span> <span style="color:#ffd166">'+di.kill+'</span>');
            if(di.kill_full&&di.kill_full!==di.kill){
              lines.push('<span style="color:#778899;font-size:10px">'+di.kill_full+'</span>');
            }
            if(di.pins&&di.pins.length){
              lines.push('<hr style="border:none;border-top:1px solid #2a4060;margin:3px 0">');
              lines.push('<span style="color:#aaa">Pin failures:</span>');
              di.pins.slice(0,6).forEach(function(p){
                var bar='';
                if(p.usl!=null){
                  var pct=Math.min(100,Math.round(p.val/p.usl*100));
                  bar=' <span style="display:inline-block;width:'+Math.min(40,pct*0.4)+'px;height:5px;background:'+(pct>=100?'#ff6b6b':'#ffd166')+';vertical-align:middle;border-radius:2px"></span>';
                }
                lines.push('&nbsp;&nbsp;<b style="color:#c0e0ff">'+p.pin+'</b>'
                  +' <span style="color:#ffd166">'+p.val+'mV</span>'
                  +(p.usl!=null?' / USL:'+p.usl+'mV':'')
                  +bar
                  +' <span style="color:#778899;font-size:10px">'+p.phase+'</span>');
              });
              if(di.pins.length>6)lines.push('&nbsp;&nbsp;<span style="color:#556677">\u2026+'+(di.pins.length-6)+' more</span>');
            }
            return lines.join('<br>');
          },
          retShots:_cpShots,
          retShotLabels:false,
          retMap:RETICLE_MAP,
          retSiteNum:(document.getElementById('wm-dieloc-toggle')&&document.getElementById('wm-dieloc-toggle').checked)?RETICLE_SITE_NUM:{},
          width:_cpTW_SM,
          bgColor:'none',
          borderColor:'#a0bcd8',
          shotColor:'#2471a3',
        });
      })(t.lot,t.wfr);
    }
    if(bEnd<_cpTD.length) requestAnimationFrame(function(){_batchTiles(bEnd);});
  })(0);
}
// ── DPS SITE ANALYSIS ─────────────────────────────────────────────────────────
var _cpSitePalette=['#e74c3c','#3498db','#2ecc71','#f39c12','#9b59b6','#1abc9c',
  '#e67e22','#48c9b0','#85c1e9','#f1948a','#a9cce3','#a3e4d7',
  '#f9e79f','#d7bde2','#82e0aa','#f0b27a'];
function _cpSiteColor(sn){
  if(sn===null||sn===undefined)return'#1a2538';
  return _cpSitePalette[Math.abs(+sn)%_cpSitePalette.length];
}
function _cpFreqColor(t){
  if(t<=0)return'#0d1828';
  // blue -> orange -> red  (matches semiconductor defect convention)
  var r,g,b;
  if(t<0.33){var s=t/0.33;r=Math.round(20+s*220);g=Math.round(80+s*80);b=Math.round(200-s*200);}
  else if(t<0.67){var s=(t-0.33)/0.34;r=255;g=Math.round(160-s*100);b=0;}
  else{r=255;g=Math.round(60*(1-(t-0.67)/0.33));b=0;}
  return'rgb('+r+','+g+','+b+')';
}
function _cpBuildSiteTable(posMap,nW){
  // Precompute total die count per site from RETICLE_MAP
  var _siteDieTotals={};
  Object.values(RETICLE_MAP).forEach(function(v){
    var sn=(RETICLE_SITE_NUM[v[0]+','+v[1]]!=null)?String(RETICLE_SITE_NUM[v[0]+','+v[1]]):'?';
    _siteDieTotals[sn]=(_siteDieTotals[sn]||0)+1;
  });
  var siteMap={},totalB8=0,unmapped=0;
  Object.keys(posMap).forEach(function(k){
    var pm=posMap[k];totalB8+=pm.hits;
    var rm=RETICLE_MAP[k];
    var sn=(rm&&RETICLE_SITE_NUM[rm[0]+','+rm[1]]!=null)?String(RETICLE_SITE_NUM[rm[0]+','+rm[1]]):'?';
    if(sn==='?')unmapped+=pm.hits;
    if(!siteMap[sn])siteMap[sn]={count:0,pos:0};
    siteMap[sn].count+=pm.hits;siteMap[sn].pos++;
  });
  if(!totalB8)return'<span style="color:#445566;font-size:0.78rem">No BIN8 data in current filter</span>';
  var rows=Object.keys(siteMap).map(function(sn){return{sn:sn,count:siteMap[sn].count,pos:siteMap[sn].pos,pct:siteMap[sn].count/totalB8};}).sort(function(a,b){return b.count-a.count;});
  var maxPct=rows[0]?rows[0].pct:1;
  var topPct=rows[0]?rows[0].pct:0;
  var html='';
  if(topPct>=0.5){
    html+='<div style="background:#2a1a0a;border-left:3px solid #e67e22;padding:6px 10px;font-size:0.77rem;color:#ffd166;margin-bottom:8px;border-radius:0 4px 4px 0">'
      +'&#9888; Site <b>'+rows[0].sn+'</b> = <b>'+Math.round(topPct*100)+'%</b> of BIN8 fails. '
      +'Potential tester contact defect on this site. '
      +'<span style="color:#aaa">Recommend tester swap experiment to confirm.</span></div>';
  } else if(topPct>=0.3){
    html+='<div style="background:#0d1a2a;border-left:3px solid #2471a3;padding:6px 10px;font-size:0.77rem;color:#8ab4d4;margin-bottom:8px;border-radius:0 4px 4px 0">'
      +'Site <b>'+rows[0].sn+'</b> leads with '+Math.round(topPct*100)+'% &#8212; partial concentration, not clearly single-site. '
      +'<span style="color:#aaa">Run lot-split by tester to isolate.</span></div>';
  } else {
    html+='<div style="background:#0d1a2a;border-left:3px solid #27ae60;padding:6px 10px;font-size:0.77rem;color:#8ab4d4;margin-bottom:8px;border-radius:0 4px 4px 0">'
      +'Fails spread across multiple sites &#8212; not a single-site contact issue. '
      +'<span style="color:#aaa">Review XY cluster on Freq % map for wafer-level pattern.</span></div>';
  }
  html+='<table style="border-collapse:collapse;width:100%;font-size:0.73rem"><thead>'
    +'<tr style="background:#0d1a2a;color:#8ab4d4">'
    +'<th style="padding:3px 8px;text-align:left">Site #</th>'
    +'<th style="padding:3px 8px;text-align:right">Fail Hits</th>'
    +'<th style="padding:3px 8px;text-align:right">% of Total</th>'
    +'<th style="padding:3px 8px;text-align:right" title="Unique (x,y) wafer cells for this site with ≥1 failure / total cells for this site">XY Cells w/ Fail</th>'
    +'<th style="padding:3px 8px">Distribution</th>'
    +'</tr></thead><tbody>';
  rows.forEach(function(r,i){
    var bg=i%2===0?'#0d1520':'#111a28';
    var barW=Math.round(r.pct/maxPct*80);
    var barCol=r.pct>=0.5?'#e67e22':r.pct>=0.3?'#f1c40f':'#2471a3';
    var siteCol=r.sn==='?'?'#556677':_cpSitePalette[Math.abs(+r.sn)%_cpSitePalette.length];
    html+='<tr style="background:'+bg+'">'
      +'<td style="padding:3px 8px;color:'+siteCol+';font-weight:700">'+r.sn+'</td>'
      +'<td style="padding:3px 8px;text-align:right;color:#e67e22">'+r.count+'</td>'
      +'<td style="padding:3px 8px;text-align:right;color:#ffd166">'+Math.round(r.pct*100)+'%</td>';
    var sDieTotal=_siteDieTotals[r.sn]||0;
    var covStr=sDieTotal>0?(r.pos+'/'+sDieTotal):String(r.pos);
    var covCol=sDieTotal>0&&r.pos>=sDieTotal?'#e67e22':(sDieTotal>0&&r.pos>=sDieTotal*0.5?'#f1c40f':'#9ab4cc');
    html+='<td style="padding:3px 8px;text-align:right;color:'+covCol+'" title="'+r.pos+' unique (x,y) positions failing out of '+sDieTotal+' total for this site">'+covStr+'</td>'
      +'<td style="padding:3px 8px"><div style="width:'+barW+'px;height:6px;background:'+barCol+';border-radius:3px"></div></td>'
      +'</tr>';
  });
  html+='</tbody></table>';
  html+='<div style="margin-top:6px;font-size:0.7rem;color:#445566">'
    +totalB8+' fail hits \u00b7 '+Object.keys(posMap).length+' unique XY cells \u00b7 '+nW+' wafer'+(nW!==1?'s':'')
    +(unmapped?' \u00b7 <span style="color:#556677">'+unmapped+' hits not in reticle map</span>':'')
    +'<br><span style="color:#334455">&#9654; <b>Site #</b> = intra-shot die location (1 to N per reticle field). <b>XY Cells w/ Fail</b> = unique wafer (x,y) positions for that site that had &ge;1 fail / total such positions on the wafer.</span>'
    +'<br><span style="color:#334455">&#9654; Tester swap: rerun same lot on different tester \u2014 if cluster moves with tester, site is causal.</span>'
    +'<br><span style="color:#334455">&#9654; Freq % map: use \"Freq % Map\" button above to see fail rate per die position normalized by wafer count.</span>'
    +'</div>';
  return html;
}
// ── RETICLE SHOT ANALYSIS ─────────────────────────────────────────────────────
// Aggregates failures by shot index (which reticle printing on the wafer)
// to detect field-level systematic defects (mask particle, OPC error, etc.).
function _cpBuildShotTable(posMap,nW){
  // Precompute total die count per shot from RETICLE_MAP
  var _shotDieTotals={};
  Object.values(RETICLE_MAP).forEach(function(v){
    var sk=String(v[2]);
    _shotDieTotals[sk]=(_shotDieTotals[sk]||0)+1;
  });
  var shotMap={},totalB8=0,unmapped=0;
  Object.keys(posMap).forEach(function(k){
    var pm=posMap[k];totalB8+=pm.hits;
    var rm=RETICLE_MAP[k];
    var si=(rm!=null)?rm[2]:null;
    if(si===null||si===undefined){unmapped+=pm.hits;}
    var siKey=(si!=null)?String(si):'?';
    if(!shotMap[siKey])shotMap[siKey]={count:0,pos:0,si:si,wfrs:new Set(),worst:{key:k,hits:pm.hits}};
    shotMap[siKey].count+=pm.hits;
    shotMap[siKey].pos++;
    pm.wk.forEach(function(w){shotMap[siKey].wfrs.add(w);});
    if(pm.hits>shotMap[siKey].worst.hits)shotMap[siKey].worst={key:k,hits:pm.hits};
  });
  if(!totalB8)return'<span style="color:#445566;font-size:0.78rem">No fail data in current filter</span>';
  if(!Object.keys(RETICLE_MAP).length)return'<span style="color:#556677;font-size:0.78rem">Reticle map not loaded &mdash; DevRevStep required.</span>';
  var rows=Object.keys(shotMap).map(function(sk){
    var sm=shotMap[sk],si=sm.si;
    var shotBox=(si!=null&&_wmRetShots[si])?_wmRetShots[si]:null;
    var pos=shotBox?'('+shotBox[1]+','+shotBox[2]+')&nbsp;&rarr;&nbsp;('+shotBox[3]+','+shotBox[4]+')':'&mdash;';
    var nTotal=_shotDieTotals[sk]||0;
    var nWfrs=sm.wfrs.size;
    return{sk:sk,si:si,count:sm.count,pos:sm.pos,nTotal:nTotal,nWfrs:nWfrs,
           wPct:nWfrs/nW,bbox:pos,worst:sm.worst.key};
  }).sort(function(a,b){return b.nWfrs-a.nWfrs||b.count-a.count;});
  var maxWPct=rows[0]?rows[0].wPct:1;
  var topWPct=rows[0]?rows[0].wPct:0;
  var html='';
  // Pattern score alert — based on wafer % of top shot
  if(topWPct>=0.5){
    html+='<div style="background:#2a1a0a;border-left:3px solid #e67e22;padding:6px 10px;font-size:0.77rem;color:#ffd166;margin-bottom:8px;border-radius:0 4px 4px 0">'
      +'&#9888; Shot <b>'+(rows[0].si!=null?rows[0].si+1:rows[0].sk)+'</b>: <b>'+Math.round(topWPct*100)+'%</b> of wafers failed in this field. '
      +'Field-level defect likely (mask particle, CD non-uniformity, or OPC error). '
      +'<span style="color:#aaa">Check lot-level reticle usage log.</span></div>';
  } else if(topWPct>=0.3){
    html+='<div style="background:#0d1a2a;border-left:3px solid #2471a3;padding:6px 10px;font-size:0.77rem;color:#8ab4d4;margin-bottom:8px;border-radius:0 4px 4px 0">'
      +'Shot <b>'+(rows[0].si!=null?rows[0].si+1:rows[0].sk)+'</b> leads with '+Math.round(topWPct*100)+'% of wafers &mdash; partial field concentration. '
      +'<span style="color:#aaa">Cross-check with intra-field site pattern.</span></div>';
  } else {
    html+='<div style="background:#0d1a2a;border-left:3px solid #27ae60;padding:6px 10px;font-size:0.77rem;color:#8ab4d4;margin-bottom:8px;border-radius:0 4px 4px 0">'
      +'Fails spread across multiple reticle shots &mdash; not a single-field defect. '
      +'<span style="color:#aaa">Consistent with wafer-level or per-die systematic.</span></div>';
  }
  html+='<table style="border-collapse:collapse;width:100%;font-size:0.73rem"><thead>'
    +'<tr style="background:#0d1a2a;color:#8ab4d4">'
    +'<th style="padding:3px 8px;text-align:left">Shot #</th>'
    +'<th style="padding:3px 8px;text-align:left">Shot Field (die coords)</th>'
    +'<th style="padding:3px 8px;text-align:right">Wafers w/ Fail</th>'
    +'<th style="padding:3px 8px;text-align:right">% Wafers</th>'
    +'<th style="padding:3px 8px;text-align:right">Fail Hits</th>'
    +'<th style="padding:3px 8px;text-align:right">Die Cov</th>'
    +'<th style="padding:3px 8px;text-align:left">Worst Die Pos</th>'
    +'<th style="padding:3px 8px">Distribution</th>'
    +'</tr></thead><tbody>';
  rows.forEach(function(r,i){
    var bg=i%2===0?'#0d1520':'#111a28';
    var barW=Math.round(r.wPct/Math.max(maxWPct,0.01)*80);
    var barCol=r.wPct>=0.5?'#e67e22':(r.wPct>=0.3?'#f1c40f':'#5b8dee');
    var shotNum=(r.si!=null)?String(r.si+1):r.sk;
    var shotCol=r.sk==='?'?'#556677':'#5b8dee';
    var covStr=r.nTotal>0?(r.pos+'/'+r.nTotal):(r.pos+'/?');
    var covCol=r.nTotal>0&&r.pos>=r.nTotal?'#e67e22':(r.nTotal>0&&r.pos>=r.nTotal*0.5?'#f1c40f':'#9ab4cc');
    html+='<tr style="background:'+bg+'">'
      +'<td style="padding:3px 8px;color:'+shotCol+';font-weight:700">'+shotNum+'</td>'
      +'<td style="padding:3px 8px;color:#9ab4cc;font-size:0.68rem">'+r.bbox+'</td>'
      +'<td style="padding:3px 8px;text-align:right;color:#e67e22">'+r.nWfrs+'/'+nW+'</td>'
      +'<td style="padding:3px 8px;text-align:right;color:#ffd166">'+Math.round(r.wPct*100)+'%</td>'
      +'<td style="padding:3px 8px;text-align:right;color:#9ab4cc">'+r.count+'</td>'
      +'<td style="padding:3px 8px;text-align:right;color:'+covCol+'">'+covStr+'</td>'
      +'<td style="padding:3px 8px;color:#c0e0ff;font-size:0.68rem;font-family:monospace">('+r.worst+')</td>'
      +'<td style="padding:3px 8px"><div style="width:'+barW+'px;height:6px;background:'+barCol+';border-radius:3px"></div></td>'
      +'</tr>';
  });
  html+='</tbody></table>';
  html+='<div style="margin-top:6px;font-size:0.7rem;color:#445566">'
    +totalB8+' fail hits &middot; '+rows.length+' shot field'+(rows.length!==1?'s':'')+' &middot; '+nW+' wafer'+(nW!==1?'s':'')
    +(unmapped?' &middot; <span style="color:#556677">'+unmapped+' hits not in reticle map</span>':'')
    +'<br><span style="color:#334455">&#9654; <b>% Wafers</b> = fraction of wafers with &ge;1 failing die in that shot field. <b>Die Cov</b> = failing die positions / total die positions per shot. <b>Worst Die Pos</b> = (x,y) with most hits.</span>'
    +'<br><span style="color:#334455">&#9654; High % Wafers in one shot suggests a field-level defect (mask particle, CD variation, OPC error).</span>'
    +'</div>';
  return html;
}
function _cpTogShotSection(){
  var s=document.getElementById('cp-shot-section');
  if(!s)return;
  var chk=document.getElementById('wm-shot-toggle');
  var open=s.style.display==='none'||s.style.display==='';
  if(chk)chk.checked=open;
  s.style.display=open?'block':'none';
  if(open){document.getElementById('cp-shot-body').innerHTML=_cpBuildShotTable(window._cpLastPosMap||{},window._cpLastNW||1);}
}
function _cpTogSiteSection(){
  var s=document.getElementById('cp-site-section');
  if(!s)return;
  var open=s.style.display==='none'||s.style.display==='';
  s.style.display=open?'block':'none';
  if(open){document.getElementById('cp-site-body').innerHTML=_cpBuildSiteTable(window._cpLastPosMap||{},window._cpLastNW||1);}
}
function closeCompositeOverlay(){
  document.getElementById('cp-pat-section').style.display='none';
  document.getElementById('cp-site-section').style.display='none';
  document.getElementById('cp-shot-section').style.display='none';
  var chk=document.getElementById('wm-shot-toggle');if(chk)chk.checked=false;
}
function _cpStartResize(e,innerId,dir){
  e.preventDefault();
  var div=document.getElementById(innerId);
  var startY=e.clientY,startH=div.offsetHeight;
  var sign=(dir==='down')?1:-1;
  function onMove(ev){div.style.height=Math.max(40,startH+sign*(ev.clientY-startY))+'px';div.style.maxHeight='none';}
  function onUp(){document.removeEventListener('mousemove',onMove);document.removeEventListener('mouseup',onUp);}
  document.addEventListener('mousemove',onMove);
  document.addEventListener('mouseup',onUp);
}
function _cpStartResizeH(e,paneClass){
  e.preventDefault();
  var pane=document.querySelector('#comp-overlay .'+paneClass);
  var startX=e.clientX,startW=pane.offsetWidth;
  function onMove(ev){
    var nw=Math.max(120,Math.min(window.innerWidth*0.7,startW+(ev.clientX-startX)));
    pane.style.width=nw+'px';
  }
  function onUp(){document.removeEventListener('mousemove',onMove);document.removeEventListener('mouseup',onUp);}
  document.addEventListener('mousemove',onMove);
  document.addEventListener('mouseup',onUp);
}
function _openPatScoreInComposite(){
  initWM();
  var selProgs=_wmChecked('wm-prog-cbs');
  var selLots=_wmChecked('wm-lot-cbs');
  var selWfrs=_wmChecked('wm-wfr-cbs').map(Number);
  var selFBs=_wmChecked('wm-fb-cbs').map(Number);
  var selPins=_wmChecked('wm-pin-cbs');
  if(!selProgs.length)selProgs=PROGS.slice();
  if(!selLots.length)selLots=LOTS.slice();
  if(!selWfrs.length)selWfrs=[...new Set(DIES.map(function(d){return d.wfr;}))];
  if(!selFBs.length)selFBs=FB_LIST.map(function(f){return f.fbin;});
  var colorMode=(document.getElementById('wm-color')&&document.getElementById('wm-color').value)||'fbin';
  document.getElementById('cp-pat-section').style.display='';
  _cpShowOverlay({progs:selProgs,lots:selLots,wfrs:selWfrs,fbs:selFBs,pins:selPins,colorMode:colorMode});
}

// ── COMPOSITE WINDOW ───────────────────────────────────────────────────────────
// Populate checkboxes from state object and show the overlay.
function _cpShowOverlay(s){
  initWM();
  ['cp-prog','cp-lot','cp-wfr','cp-fb','cp-pin'].forEach(function(id){document.getElementById(id).innerHTML='';});
  PROGS.forEach(function(p){_cpMkCb('cp-prog',p,p,'#8ab4d4',s.progs.indexOf(p)>=0);});
  LOTS.forEach(function(l){_cpMkCb('cp-lot',l,l,'#8ab4d4',s.lots.indexOf(l)>=0);});
  [...new Set(DIES.map(function(d){return d.wfr;}))].sort(function(a,b){return a-b;}).forEach(function(w){
    _cpMkCb('cp-wfr',w,'W'+w,'#8ab4d4',s.wfrs.indexOf(w)>=0);});
  FB_LIST.forEach(function(f){_cpMkCb('cp-fb',f.fbin,'FB '+f.fbin+' ('+f.count+')',fbColor(f.fbin),s.fbs.indexOf(f.fbin)>=0);});
  PIN_LIST.forEach(function(pin){_cpMkCb('cp-pin',pin.pin,pin.pin+' ('+pin.count+')',CS_COL[pin.cs]||'#8ab4d4',s.pins.indexOf(pin.pin)>=0);});
  document.getElementById('cp-color').value=s.colorMode;
  document.getElementById('comp-overlay').style.display='block';
  document.getElementById('cp-site-section').style.display='block';
  document.getElementById('cp-pat-section').style.display='block';
  var _sc=document.getElementById('wm-shot-toggle');
  document.getElementById('cp-shot-section').style.display=(_sc&&_sc.checked)?'block':'none';
  _cpUpdateSelCounts();
  _cpRender();
}
// Open composite view in a separate browser window.
function openCompositeWindow(){
  initWM();
  var selProgs=_wmChecked('wm-prog-cbs');
  var selLots =_wmChecked('wm-lot-cbs');
  var selWfrs =_wmChecked('wm-wfr-cbs').map(Number);
  var selFBs  =_wmChecked('wm-fb-cbs').map(Number);
  var selPins =_wmChecked('wm-pin-cbs');
  if(!selProgs.length)selProgs=PROGS.slice();
  if(!selLots.length)selLots=LOTS.slice();
  if(!selWfrs.length)selWfrs=[...new Set(DIES.map(function(d){return d.wfr;}))];
  if(!selFBs.length)selFBs=FB_LIST.map(function(f){return f.fbin;});
  var colorMode=document.getElementById('wm-color')&&document.getElementById('wm-color').value||'fbin';
  var state={progs:selProgs,lots:selLots,wfrs:selWfrs,fbs:selFBs,pins:selPins,colorMode:colorMode};
  // If this IS the popup already, just show the overlay in-place.
  if(location.hash.indexOf('#cpstate=')===0){_cpShowOverlay(state);return;}
  // Encode state in the URL hash — popup reads it on load, no cross-window JS needed.
  var url=location.href.split('#')[0]+'#cpstate='+encodeURIComponent(JSON.stringify(state));
  // window.open reuses named window if still open, navigating it to the new URL.
  var w=window.open(url,'vcccont_b8_comp',
    'width=1600,height=950,resizable=yes,scrollbars=yes,toolbar=no,menubar=no,location=no,status=no');
  if(!w){alert('Pop-up blocked \u2014 please allow pop-ups for this page.');return;}
  w.focus();
}

// ── PARETO ──────────────────────────────────────────────────────────────────────
var _pInit=false;
function initPareto(){
  if(_pInit) return; _pInit=true;
  // Phase checkboxes
  [{p:'Pre-Surge',c:'#4ecdc4'},{p:'Post-Surge',c:'#48cae4'},{p:'Stress',c:'#ffd166'},{p:'SDS-Final',c:'#ff6b6b'},{p:'SDT-Start',c:'#c77dff'},{p:'SDT-Final',c:'#a06fdd'}].forEach(function(o){
    _mkParCb('par-ph-cbs', o.p, o.p, o.c, true);
  });
  // Program checkboxes
  PROGS.forEach(function(p){_mkParCb('par-prog-cbs',p,p,'#8ab4d4',true,function(){drawPareto();_ddLabelUpdate('par-lot-btn','par-lot-wfr-tree','lot');_ddLabelUpdate('par-fb-btn','par-ib-fb-tree','ib');});});
  // Lot/Wafer tree
  _buildLotWfrTree('par-lot-wfr-tree', DIES, function(){drawPareto();_ddLabelUpdate('par-lot-btn','par-lot-wfr-tree','lot');});
  // IB/FB tree
  _buildIbFbTree('par-ib-fb-tree', DIES, FB_LIST, function(){drawPareto();_ddLabelUpdate('par-fb-btn','par-ib-fb-tree','ib');});
  // Hide prog row if only one program
  if(PROGS.length<=1){var pw=document.getElementById('par-prog-wrap');if(pw)pw.style.display='none';}
}
function _mkParCb(containerId,value,label,color,checked,onChange){
  var wrap=document.createElement('label');
  wrap.style.cssText='display:flex;align-items:center;gap:5px;cursor:pointer;font-size:0.78rem;color:'+(color||'#c0ccd8')+';white-space:nowrap';
  var cb=document.createElement('input');cb.type='checkbox';cb.value=value;cb.checked=checked;
  cb.style.cssText='accent-color:'+(color||'#4a9fd4')+';cursor:pointer;width:13px;height:13px';
  cb.addEventListener('change',onChange||drawPareto);
  wrap.appendChild(cb);wrap.appendChild(document.createTextNode('\u00a0'+label));
  document.getElementById(containerId).appendChild(wrap);
}
function _parChecked(containerId){
  return Array.from(document.getElementById(containerId).querySelectorAll('input:checked')).map(function(cb){return cb.value;});
}
function parToggleAll(containerId,checked){
  document.getElementById(containerId).querySelectorAll('input[type=checkbox]').forEach(function(cb){cb.checked=checked;});
  drawPareto();
}
function onParProgChange(){
  // cascade: prog → lot → wafer
  var selProgs=new Set(_parChecked('par-prog-cbs'));
  var activeLots=new Set(),activeWfrs=new Set();
  DIES.forEach(function(d){if(!selProgs.has(d.prog))return;activeLots.add(d.lot);activeWfrs.add(d.wfr);});
  document.getElementById('par-lot-cbs').querySelectorAll('input').forEach(function(cb){cb.checked=activeLots.has(cb.value);});
  document.getElementById('par-wfr-cbs').querySelectorAll('input').forEach(function(cb){cb.checked=activeWfrs.has(+cb.value);});
  drawPareto();
}
function onParLotChange(){
  // cascade: lot → wafer
  var selProgs=new Set(_parChecked('par-prog-cbs'));
  var selLots=new Set(_parChecked('par-lot-cbs'));
  var activeWfrs=new Set();
  DIES.forEach(function(d){if(!selProgs.has(d.prog)||!selLots.has(d.lot))return;activeWfrs.add(d.wfr);});
  document.getElementById('par-wfr-cbs').querySelectorAll('input').forEach(function(cb){cb.checked=activeWfrs.has(+cb.value);});
  drawPareto();
}
function drawPareto(){
  var selProgs=new Set(_parChecked('par-prog-cbs'));
  // Lot/Wafer from tree
  var selLots=new Set(),selWfrs=new Set();
  document.querySelectorAll('#par-lot-wfr-tree input[data-type=lot]:checked').forEach(function(cb){selLots.add(cb.value);});
  document.querySelectorAll('#par-lot-wfr-tree input[data-type=wfr]:checked').forEach(function(cb){selWfrs.add(+cb.value);});
  if(!selLots.size) document.querySelectorAll('#par-lot-wfr-tree input[data-type=lot]').forEach(function(cb){selLots.add(cb.value);});
  if(!selWfrs.size) document.querySelectorAll('#par-lot-wfr-tree input[data-type=wfr]').forEach(function(cb){selWfrs.add(+cb.value);});
  // IB/FB from tree
  var selFBs=new Set();
  document.querySelectorAll('#par-ib-fb-tree input[data-type=fb]:checked').forEach(function(cb){selFBs.add(+cb.value);});
  var dies=DIES.filter(function(d){
    if(selProgs.size&&!selProgs.has(d.prog))return false;
    if(!selLots.has(d.lot))return false;
    if(!selWfrs.has(d.wfr))return false;
    if(selFBs.size&&!selFBs.has(d.fbin))return false;
    return true;
  });
  var selPhs=new Set(_parChecked('par-ph-cbs'));
  if(selPhs.size) dies=dies.filter(function(d){return selPhs.has(d.phase);});
  var pc={};
  dies.forEach(function(d){var seen=new Set();d.pins.forEach(function(p){if(selPhs.size&&!selPhs.has(p.phase))return;if(!seen.has(p.pin)){pc[p.pin]=(pc[p.pin]||0)+1;seen.add(p.pin);}});});
  var sorted=Object.entries(pc).sort(function(a,b){return b[1]-a[1];}).slice(0,30);
  if(!sorted.length){Plotly.react('par-plot',[],L({title:'No data for selection'}),PC);return;}
  var tot=sorted.reduce(function(s,e){return s+e[1];},0),cum=0;
  var cumPct=sorted.map(function(e){cum+=e[1];return +(cum/tot*100).toFixed(1);});
  var pins=sorted.map(function(e){return e[0];}), cnts=sorted.map(function(e){return e[1];});
  var pCS={};DIES.forEach(function(d){d.pins.forEach(function(p){if(!pCS[p.pin])pCS[p.pin]=p.cs;});});
  var cols=pins.map(function(p){return CS_COL[pCS[p]]||'#5577aa';});
  document.getElementById('par-cnt').textContent=tot+' die-pin fails / '+dies.length+' BIN8 dies';
  var phLbl=selPhs.size<6?' ['+[...selPhs].join(',')+']':'', fbLbl=selFBs.size&&selFBs.size<FB_LIST.length?' [FB '+[...selFBs].join(',')+']':'';
  Plotly.react('par-plot',[
    {x:pins,y:cnts,type:'bar',name:'BIN8 dies',marker:{color:cols},text:cnts,textposition:'outside',hovertemplate:'%{x}: %{y} dies<extra></extra>'},
    {x:pins,y:cumPct,type:'scatter',mode:'lines+markers',name:'Cum%',yaxis:'y2',line:{color:'#ffd166',width:2},marker:{size:5},hovertemplate:'%{y}%<extra></extra>'},
  ],L({title:'Failing Pins \u2014 BIN8'+fbLbl+phLbl,xaxis:{tickangle:-60,tickfont:{size:9}},yaxis:{title:'BIN8 dies'},yaxis2:{title:'Cum%',overlaying:'y',side:'right',range:[0,105]},legend:{orientation:'h',y:1.05},margin:{t:50,l:52,r:58,b:180}}),PC);
  // Click bar to show pin distribution
  (function(){
    var el=document.getElementById('par-plot');
    el.removeAllListeners&&el.removeAllListeners('plotly_click');
    el.on('plotly_click',function(data){
      if(data&&data.points&&data.points.length&&data.points[0].data.type==='bar'){
        var pin=data.points[0].x;
        if(pin&&PIN_DISTRIB[pin]) showPinDist(pin);
      }
    });
  })();
  var top10=sorted.slice(0,10).map(function(e){return e[0];});
  var _phList=['Pre-Surge','Post-Surge','Post-Surge-HT','Stress','SDS-Final','SDT-Start','SDT-Final'];
  var _phCols=['#4ecdc4','#48cae4','#ffd166','#ff6b6b','#c77dff','#a06fdd'];
  var phTr=_phList.map(function(ph,i){var ys=top10.map(function(pin){var c=0;dies.forEach(function(d){var s=new Set();d.pins.forEach(function(p){if(p.pin===pin&&p.phase===ph&&!s.has(p.pin)){c++;s.add(p.pin);}});});return c;});return{x:top10,y:ys,name:ph,type:'bar',marker:{color:_phCols[i]}};});
  Plotly.react('par-phase-plot',phTr,L({barmode:'stack',title:'Phase breakdown (top 10 pins)',xaxis:{tickangle:-45,tickfont:{size:9}},yaxis:{title:'dies'},legend:{orientation:'h',y:1.05},margin:{t:50,l:50,r:20,b:120}}),PC);
  var fbTr=FB_LIST.map(function(fb){var ys=top10.map(function(pin){var c=0;dies.filter(function(d){return d.fbin===fb.fbin;}).forEach(function(d){var s=new Set();d.pins.forEach(function(p){if(p.pin===pin&&!s.has(p.pin)){c++;s.add(p.pin);}});});return c;});return{x:top10,y:ys,name:'FB '+fb.fbin,type:'bar',marker:{color:fbColor(fb.fbin)}};});
  Plotly.react('par-fb-plot',fbTr,L({barmode:'stack',title:'FB breakdown (top 10 pins)',xaxis:{tickangle:-45,tickfont:{size:9}},yaxis:{title:'dies'},legend:{orientation:'h',y:1.05},margin:{t:50,l:50,r:20,b:120}}),PC);
}

// ── DIE TABLE ──────────────────────────────────────────────────────────────────
var _dtS='fbin',_dtA=false,_dtI=false;
function initDT(){
  if(_dtI) return;_dtI=true;
  var ls=document.getElementById('dt-lot'),ws=document.getElementById('dt-wfr'),fs=document.getElementById('dt-fb'),ks=document.getElementById('dt-kill'),ps=document.getElementById('dt-pin');
  [...new Set(DIES.map(function(d){return d.lot;}))].sort().forEach(function(v){var o=document.createElement('option');o.value=o.text=v;ls.appendChild(o);});
  [...new Set(DIES.map(function(d){return d.wfr;}))].sort(function(a,b){return a-b;}).forEach(function(v){var o=document.createElement('option');o.value=v;o.text='W'+v;ws.appendChild(o);});
  FB_LIST.forEach(function(f){var o=document.createElement('option');o.value=f.fbin;o.text='FB '+f.fbin+' ('+f.count+')';fs.appendChild(o);});
  KILL_LIST.forEach(function(k){var o=document.createElement('option');o.value=k.kill;o.text=k.kill+' ('+k.count+')';ks.appendChild(o);});
  PIN_LIST.forEach(function(p){var o=document.createElement('option');o.value=p.pin;o.text=p.pin+' ('+p.count+')';ps.appendChild(o);});
}
function dtSort(col){if(_dtS===col)_dtA=!_dtA;else{_dtS=col;_dtA=(col==='x'||col==='y');}buildDieTable();}
function buildDieTable(){
  var lv=document.getElementById('dt-lot').value,wv=document.getElementById('dt-wfr').value,fv=document.getElementById('dt-fb').value,kv=document.getElementById('dt-kill').value,pv=document.getElementById('dt-pin').value,sv=document.getElementById('dt-srch').value.toLowerCase().trim();
  var data=DIES.filter(function(d){
    if(lv!=='all'&&d.lot!==lv)return false;
    if(wv!=='all'&&String(d.wfr)!==wv)return false;
    if(fv!=='all'&&d.fbin!==+fv)return false;
    if(kv!=='all'&&d.kill!==kv)return false;
    if(pv!=='all'&&!d.pins.some(function(p){return p.pin===pv;}))return false;
    if(sv){var s=(d.x+' '+d.y+' '+d.lot+' '+(d.material||'')+' '+d.fbin+' '+d.kill+' '+d.pins.map(function(p){return p.pin;}).join(' ')).toLowerCase();if(!s.includes(sv))return false;}
    return true;
  });
  data.sort(function(a,b){var av=a[_dtS],bv=b[_dtS];if(typeof av==='string'){av=av.toLowerCase();bv=bv.toLowerCase();}return _dtA?(av>bv?1:av<bv?-1:0):(av<bv?1:av>bv?-1:0);});
  document.getElementById('dt-cnt').textContent=data.length+' of '+DIES.length+' dies';
  var rows=data.slice(0,500).map(function(d){
    var c=fbColor(d.fbin);
    var ph=d.pins.length?d.pins.slice(0,5).map(function(p){var col=p.has_lim===false?'#ffd166':'#ff9999';var lbl=p.has_lim===false?' (no lim)':'';return '<span class="pin-tag pin-fail" style="border-color:'+(p.has_lim===false?'#6a6a2a':'#6a2a2a')+';color:'+col+'" title="'+p.phase+' '+p.val+'mV'+(p.usl?' USL '+p.usl:'')+'">'+p.pin+' '+p.val+lbl+'</span>';}).join('')+(d.pins.length>5?'<span style="color:#445566;font-size:0.72rem"> +'+( d.pins.length-5)+' more</span>':''):'<span style="color:#334455">\u2014</span>';
    return '<tr><td style="color:#8ab4d4;font-size:0.77rem">'+d.lot+'</td><td style="color:#c0ccd8">W'+d.wfr+'</td><td style="color:#4ecdc4;font-size:0.74rem">'+(d.material||'')+'</td><td>'+d.x+'</td><td>'+d.y+'</td><td><span style="color:'+c+';font-weight:700">'+d.fbin+'</span></td><td style="color:#556677;font-size:0.74rem">'+d.dbin+'</td><td style="font-size:0.77rem;font-weight:600;color:#c0ccd8">'+d.kill+'</td><td>'+phaseBadge(d.phase)+'</td><td>'+rtypeBadge(d.rtype)+'</td><td>'+ph+'</td></tr>';
  }).join('');
  document.getElementById('dt-body').innerHTML=rows||'<tr><td colspan="11" style="color:#445566;text-align:center;padding:20px">No dies match filter</td></tr>';
}

// ── TEST FLOW ──────────────────────────────────────────────────────────────────
var _selFlowGroup = null;
var _flowInited = false;

// ── Flow filter helpers ───────────────────────────────────────────────────────
function _mkFlowCb(containerId, value, label, color, checked, onChange){
  var wrap=document.getElementById(containerId);
  if(!wrap) return;
  var id='flcb_'+containerId+'_'+value;
  var div=document.createElement('div');
  div.style.cssText='display:flex;align-items:center;gap:5px;cursor:pointer';
  div.innerHTML='<input type="checkbox" id="'+id+'" value="'+value+'" style="cursor:pointer;accent-color:'+color+'"'+(checked?' checked':'')+'>'+
    '<label for="'+id+'" style="font-size:0.75rem;color:'+color+';cursor:pointer;white-space:nowrap">'+label+'</label>';
  div.querySelector('input').addEventListener('change', onChange);
  wrap.appendChild(div);
}
function _flowChecked(containerId){
  var wrap=document.getElementById(containerId); if(!wrap) return [];
  return Array.from(wrap.querySelectorAll('input[type=checkbox]:checked')).map(function(i){return i.value;});
}
function flowToggleAll(containerId, checked){
  var wrap=document.getElementById(containerId); if(!wrap) return;
  wrap.querySelectorAll('input[type=checkbox]').forEach(function(i){i.checked=checked;});
  drawFlowDiagram();
}
function _flowSetWfrs(activeLotSet){
  var wrap=document.getElementById('flow-wfr-cbs'); if(!wrap) return;
  var prev=new Set(_flowChecked('flow-wfr-cbs').map(Number));
  wrap.innerHTML='';
  var uWfrs=[];
  DIES.forEach(function(d){ if(activeLotSet.has(d.lot) && uWfrs.indexOf(d.wfr)<0) uWfrs.push(d.wfr); });
  uWfrs.sort(function(a,b){return a-b;});
  uWfrs.forEach(function(w){ _mkFlowCb('flow-wfr-cbs',String(w),'W'+w,'#8ab4d4',prev.size===0||prev.has(w),drawFlowDiagram); });
}
function onFlowProgChange(){
  var selP=new Set(_flowChecked('flow-prog-cbs'));
  var activeLots=new Set(); DIES.forEach(function(d){ if(!selP.size||selP.has(d.prog)) activeLots.add(d.lot); });
  var wrapL=document.getElementById('flow-lot-cbs'); if(!wrapL) return;
  wrapL.querySelectorAll('input').forEach(function(i){ i.disabled=!activeLots.has(i.value); i.checked=activeLots.has(i.value); });
  var selL=new Set(_flowChecked('flow-lot-cbs'));
  _flowSetWfrs(selL.size?selL:activeLots);
  drawFlowDiagram();
}
function onFlowLotChange(){
  var selL=new Set(_flowChecked('flow-lot-cbs'));
  _flowSetWfrs(selL);
  drawFlowDiagram();
}
var _flowFBActive = new Set(); // kept for compat, not actively used
function initFlowFilters(){
  if(_flowInited) return;
  _flowInited=true;
  // Programs
  PROGS.forEach(function(p){ _mkFlowCb('flow-prog-cbs',p,p,'#8ab4d4',true,function(){drawFlowDiagram();_ddLabelUpdate('flow-lot-btn','flow-lot-wfr-tree','lot');_ddLabelUpdate('flow-fb-btn','flow-ib-fb-tree','ib');}); });
  // Lot/Wafer tree
  _buildLotWfrTree('flow-lot-wfr-tree', DIES, function(){drawFlowDiagram();_ddLabelUpdate('flow-lot-btn','flow-lot-wfr-tree','lot');});
  // IB/FB tree
  _buildIbFbTree('flow-ib-fb-tree', DIES, FB_LIST, function(){drawFlowDiagram();_ddLabelUpdate('flow-fb-btn','flow-ib-fb-tree','ib');});
  // Hide prog row if only one program
  if(PROGS.length<=1){var pw=document.getElementById('flow-prog-wrap');if(pw)pw.style.display='none';}
}

// ── Dynamic kill count from DIES with current filters ─────────────────────────
function _flowKillStats(phaseName, isEdc){
  var selP=new Set(_flowChecked('flow-prog-cbs'));
  var selL=new Set(),selW=new Set(),selFB=new Set();
  document.querySelectorAll('#flow-lot-wfr-tree input[data-type=lot]:checked').forEach(function(cb){selL.add(cb.value);});
  document.querySelectorAll('#flow-lot-wfr-tree input[data-type=wfr]:checked').forEach(function(cb){selW.add(+cb.value);});
  if(!selL.size) document.querySelectorAll('#flow-lot-wfr-tree input[data-type=lot]').forEach(function(cb){selL.add(cb.value);});
  if(!selW.size) document.querySelectorAll('#flow-lot-wfr-tree input[data-type=wfr]').forEach(function(cb){selW.add(+cb.value);});
  document.querySelectorAll('#flow-ib-fb-tree input[data-type=fb]:checked').forEach(function(cb){selFB.add(+cb.value);});
  var n=0; var pins={}; var edcCS={};
  DIES.forEach(function(d){
    if(selP.size && !selP.has(d.prog)) return;
    if(!selL.has(d.lot)) return;
    if(!selW.has(d.wfr)) return;
    if(selFB.size && !selFB.has(d.fbin)) return;
    if(isEdc){
      // Count dies that have any EDC supply failure
      var edcFails = d.edc||{};
      var hasFail=false;
      Object.entries(edcFails).forEach(function(e){
        var cs=e[0], cs_d=e[1];
        if(cs_d.n_fail>0){
          hasFail=true;
          if(!edcCS[cs]) edcCS[cs]={n_dies:0, n_pins:cs_d.n_total, worst:0};
          edcCS[cs].n_dies++;
          if(cs_d.worst>edcCS[cs].worst) edcCS[cs].worst=cs_d.worst;
        }
      });
      if(hasFail) n++;
    } else {
      if(d.phase!==phaseName) return;
      n++;
      (d.pins||[]).forEach(function(p){ pins[p.pin]=(pins[p.pin]||0)+1; });
    }
  });
  var topPins=Object.entries(pins).sort(function(a,b){return b[1]-a[1];}).slice(0,5).map(function(e){return {pin:e[0],n:e[1]};});
  return {n:n, top_pins:topPins, edc_cs:edcCS};
}

function selectFlowGroup(gid, redraw){
  _selFlowGroup = gid;
  if(redraw!==false) drawFlowDiagram();
  // Find group data
  var grpData = null;
  (FLOW_DATA.phases||[]).forEach(function(ph){
    ph.groups.forEach(function(g){ if(g.id===gid) grpData=g; });
  });
  if(!grpData) return;
  var det = document.getElementById('flow-detail');
  var forceCells = Object.entries(grpData.force||{}).map(function(e){
    return '<tr><td style="padding:3px 10px 3px 0;color:#8ab4d4;white-space:nowrap">'+e[0]+'</td>'+
           '<td style="padding:3px 0;color:#c0ccd8;font-family:monospace">'+e[1]+'</td></tr>';
  }).join('');
  var liveStats = _flowKillStats(grpData.phase, grpData.edc);
  // EDC supply table: merge static limits with live die-count data
  var edcTable = '';
  if(grpData.edc){
    var edcLims = grpData.edc_limits||{};
    var liveCS  = liveStats.edc_cs||{};
    var allCS   = Array.from(new Set(Object.keys(edcLims).concat(Object.keys(liveCS)))).sort();
    var rows = allCS.map(function(cs){
      var lim_d = edcLims[cs]||{};
      var liv_d = liveCS[cs]||{};
      var nDies = liv_d.n_dies||0;
      var nPins = lim_d.n_pins||liv_d.n_pins||'?';
      var usl   = lim_d.usl!=null ? lim_d.usl+' mV' : '—';
      var lsl   = lim_d.lsl!=null ? lim_d.lsl+' mV' : '—';
      var worst = liv_d.worst ? liv_d.worst+' mV' : '—';
      var clr   = nDies>0 ? '#ffbb66' : '#4a6a4a';
      return '<tr>'+
        '<td style="padding:3px 10px 3px 0;color:#8ab4d4;white-space:nowrap;font-family:monospace">'+cs+'</td>'+
        '<td style="padding:3px 10px;color:#c0ccd8;font-family:monospace;text-align:right">'+lsl+'</td>'+
        '<td style="padding:3px 10px;color:#c0ccd8;font-family:monospace;text-align:right">'+usl+'</td>'+
        '<td style="padding:3px 10px;color:'+clr+';font-family:monospace;text-align:right">'+nDies+' / '+nPins+' pins</td>'+
        '<td style="padding:3px 0;color:#aacccc;font-family:monospace;text-align:right">'+worst+'</td>'+
        '</tr>';
    }).join('');
    if(rows){
      edcTable = '<div style="min-width:400px">'+
        '<div style="font-size:0.77rem;font-weight:700;color:#8899aa;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">'+
          'EDC Supply Analysis (measurement only — no kill)'+
        '</div>'+
        '<div style="font-size:0.7rem;color:#445566;margin-bottom:6px">'+liveStats.n+' dies exceed at least one supply limit (filtered)</div>'+
        '<table style="border-collapse:collapse;font-size:0.78rem">'+
          '<tr style="font-size:0.7rem;color:#445566">'+
            '<th style="padding:2px 10px 4px 0;text-align:left">Supply CS</th>'+
            '<th style="padding:2px 10px 4px;text-align:right">LSL</th>'+
            '<th style="padding:2px 10px 4px;text-align:right">USL</th>'+
            '<th style="padding:2px 10px 4px;text-align:right">Dies Over Limit</th>'+
            '<th style="padding:2px 0 4px;text-align:right">Worst Value</th>'+
          '</tr>'+
          rows+
        '</table></div>';
    }
  }
  var _flowPins = grpData.top_pins || [];
  var pinCount = _flowPins.length;
  var _pinFbSet = {};
  _flowPins.forEach(function(p){ Object.keys(p.fbs||{}).forEach(function(fb){ _pinFbSet[fb]=1; }); });
  var _pinFbAll = Object.keys(_pinFbSet).map(Number).sort(function(a,b){return a-b;});
  var _fpId = 'fp_' + Math.random().toString(36).slice(2);
  var _fpDetailId = _fpId + '_det';

  // ── shared helper: build stats panel HTML for a pin entry ───────────────
  function _fpStatHtml(p){
    if(!p)return'';
    var fbRows=(Object.keys(p.fbs||{}).length
      ? Object.keys(p.fbs).sort(function(a,b){return (p.fbs[b]||0)-(p.fbs[a]||0);}).map(function(fb){
          var cnt=p.fbs[fb]||0, pct=p.n>0?Math.round(cnt/p.n*100):0, c=fbColor(+fb);
          return '<tr>'
            +'<td><span style="color:'+c+';font-weight:700">FB '+fb+'</span></td>'
            +'<td><b style="color:#e0eaf8">'+cnt+'</b> <span style="color:#445566;font-size:0.73rem">('+pct+'%)</span></td>'
            +'<td style="padding-left:8px"><span style="display:inline-block;height:8px;width:'+Math.max(4,Math.round(pct*1.2))+'px;background:'+c+';border-radius:2px"></span></td>'
            +'</tr>';
        }).join('')
      : '<tr><td colspan="3" style="color:#445566;font-size:0.75rem">No fails in current filter</td></tr>');
    return '<div style="display:flex;align-items:baseline;gap:10px;margin-bottom:6px;border-bottom:1px solid #1e3050;padding-bottom:4px">'
        +'<span style="font-family:monospace;color:#8ab4d4;font-weight:700">'+p.pin+'</span>'
        +(p.n>0?'<span style="color:#ff9966">'+p.n+' dies failing</span>':'<span style="color:#4a6a4a;font-size:0.75rem">0 fails in this phase</span>')
      +'</div>'
      +'<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px">'
        +(p.lsl!=null?'<span style="font-size:0.75rem"><span style="color:#445566">LSL:</span> <b style="color:#a0b8c8">'+p.lsl+' mV</b></span>':'')
        +(p.usl!=null?'<span style="font-size:0.75rem"><span style="color:#445566">USL:</span> <b style="color:#a0b8c8">'+p.usl+' mV</b></span>':'')
        +(p.min_val!=null?'<span style="font-size:0.75rem"><span style="color:#445566">Lowest:</span> <b style="color:#48cae4">'+p.min_val+' mV</b></span>':'')
        +(p.med_val!=null?'<span style="font-size:0.75rem"><span style="color:#445566">Median:</span> <b style="color:#8ab4d4">'+p.med_val+' mV</b></span>':'')
        +(p.max_val!=null?'<span style="font-size:0.75rem"><span style="color:#445566">Worst:</span> <b style="color:#ffd166">'+p.max_val+' mV</b></span>':'')
        +(p.usl&&p.max_val?'<span style="font-size:0.75rem"><span style="color:#445566">Worst/USL:</span> <b style="color:'+(p.max_val>p.usl?'#ff9999':'#4ecdc4')+'">'+(p.max_val/p.usl).toFixed(2)+'\u00d7</b></span>':'')
      +'</div>'
      +(p.n>0?'<div style="font-size:0.73rem;color:#445566;margin-bottom:4px">FB Breakdown</div>'
        +'<table style="width:100%;border-collapse:collapse;font-size:0.78rem">'+fbRows+'</table>':'');
  }

  // ── left: Failing / All-pins toggle ──────────────────────────────────────
  var _allPins = grpData.all_lim_pins || [];
  var _leftTabId = _fpId+'_lt';
  var _leftListId = _fpId+'_ll';

  function _buildLeftRows(pins, allMode){
    return pins.map(function(p, i){
      var hasFail = p.n > 0;
      var dimStyle = (!hasFail && allMode) ? 'opacity:0.45;' : '';
      var hasDist = !!(PIN_DISTRIB && PIN_DISTRIB[p.pin]);
      return '<div data-lrow="'+i+'" data-lmode="'+(allMode?'a':'f')+'" data-fpid3="'+_fpId+'" '
        +'style="'+dimStyle+'display:flex;justify-content:space-between;align-items:center;'
        +'padding:4px 6px;border-bottom:1px solid #1a2538;cursor:pointer;border-left:3px solid transparent;'
        +'transition:border-color 0.15s,background 0.1s" '
        +'onmouseover="this.style.background=\'#0f1a2a\'" onmouseout="if(this.dataset.sel!==\'1\')this.style.background=\'\'">'
        +'<span style="font-family:monospace;font-size:0.78rem;color:#8ab4d4">'+p.pin+'</span>'
        +'<span style="display:flex;align-items:center;gap:5px;flex-shrink:0;margin-left:6px">'
        +(hasFail ? '<span style="font-size:0.76rem;color:#ff9966">'+p.n+' dies</span>' : '<span style="font-size:0.72rem;color:#2a4a3a">'+(p.usl!=null?'USL:'+p.usl:'—')+'</span>')
        +(hasDist ? '<span onclick="event.stopPropagation();showPinDist(\''+p.pin+'\',\''+grpData.phase+'\')" title="Distribution chart" style="cursor:pointer;font-size:0.72rem;color:#ffd166;background:#1e2c14;border:1px solid #3a5020;border-radius:3px;padding:0 4px;line-height:1.5">&#128202;</span>' : '')
        +'</span>'
        +'</div>';
    }).join('');
  }

  var _failRows = _buildLeftRows(_flowPins, false);
  var _allRows  = _buildLeftRows(_allPins,  true);

  var leftCol =
    '<div style="display:flex;gap:0;margin-bottom:4px;border:1px solid #1e3050;border-radius:4px;overflow:hidden">'
      +'<button id="'+_leftTabId+'_f" onclick="_fpSwitchTab(\''+_fpId+'\',false)" '
        +'style="flex:1;padding:4px 0;font-size:0.73rem;font-weight:700;cursor:pointer;'
        +'background:#1e3050;color:#4a9fd4;border:none">Failing ('+_flowPins.length+')</button>'
      +'<button id="'+_leftTabId+'_a" onclick="_fpSwitchTab(\''+_fpId+'\',true)" '
        +'style="flex:1;padding:4px 0;font-size:0.73rem;font-weight:700;cursor:pointer;'
        +'background:#0d1520;color:#445566;border:none;border-left:1px solid #1e3050">All Pins ('+_allPins.length+')</button>'
    +'</div>'
    // Failing list (shown by default)
    +'<div id="'+_leftListId+'_f" style="max-height:280px;overflow-y:auto;border:1px solid #1e2d45;border-radius:4px">'
      +_failRows
    +'</div>'
    // All-pins list (hidden by default)
    +'<div id="'+_leftListId+'_a" style="display:none;max-height:280px;overflow-y:auto;border:1px solid #1e2d45;border-radius:4px">'
      +_allRows
    +'</div>';

  // ── right: FB dropdown filter + clickable rows → stats panel ─────────────
  // FB filter: custom dropdown toggle
  var fbDropId = _fpId+'_fbdrop';
  var fbCbs = _pinFbAll.map(function(fb){
    var c = fbColor(fb);
    return '<label style="display:flex;align-items:center;gap:4px;padding:3px 6px;cursor:pointer;font-size:0.75rem;white-space:nowrap">'
      +'<input type="checkbox" data-fpfb="'+fb+'" data-fpid="'+_fpId+'" checked style="accent-color:'+c+'">'
      +'<span style="color:'+c+'">FB '+fb+'</span></label>';
  }).join('');

  var interactiveRows = _flowPins.map(function(p, i){
    var fbBars = _pinFbAll.map(function(fb){
      var cnt = (p.fbs&&p.fbs[String(fb)])||0;
      var pct = p.n>0 ? Math.round(cnt/p.n*100) : 0;
      var c = fbColor(fb);
      return cnt>0
        ? '<span data-pfb="'+fb+'" style="display:inline-flex;align-items:center;gap:3px;margin:0 5px 0 0;font-size:0.71rem;color:'+c+'">'
          +'FB'+fb+' <b>'+cnt+'</b>'
          +'<span style="display:inline-block;width:'+Math.max(4,Math.round(pct*0.4))+'px;height:5px;background:'+c+';border-radius:2px;opacity:0.8"></span></span>'
        : '<span data-pfb="'+fb+'" style="display:none"></span>';
    }).join('');
    var hasDist2 = !!(PIN_DISTRIB && PIN_DISTRIB[p.pin]);
    return '<div data-fpirow="'+i+'" data-fpid2="'+_fpId+'" '
      +'style="padding:5px 8px;border-bottom:1px solid #1a2538;cursor:pointer;border-left:3px solid transparent;transition:border-color 0.15s,background 0.1s" '
      +'onmouseover="this.style.background=\'#0f1a2a\'" onmouseout="if(this.dataset.sel!==\'1\')this.style.background=\'\'">'
      +'<div style="display:flex;justify-content:space-between;align-items:center">'
      +'<span style="font-family:monospace;font-size:0.78rem;color:#8ab4d4">'+p.pin+'</span>'
      +'<span style="display:flex;align-items:center;gap:5px;flex-shrink:0;margin-left:8px">'
      +'<span style="font-size:0.78rem;color:#ff9966">'+p.n+' dies</span>'
      +(hasDist2 ? '<span onclick="event.stopPropagation();showPinDist(\''+p.pin+'\',\''+grpData.phase+'\')" title="Distribution chart" style="cursor:pointer;font-size:0.72rem;color:#ffd166;background:#1e2c14;border:1px solid #3a5020;border-radius:3px;padding:0 4px;line-height:1.5">&#128202;</span>' : '')
      +'</span></div>'
      +'<div style="margin-top:2px;display:flex;flex-wrap:wrap">'+fbBars+'</div>'
      +'</div>';
  }).join('');

  var topPinsHtml =
    // FB dropdown
    '<div style="position:relative;margin-bottom:5px">'
      +'<button onclick="document.getElementById(\''+fbDropId+'\').style.display=document.getElementById(\''+fbDropId+'\').style.display===\'none\'?\'block\':\'none\'" '
      +'style="background:#1a2235;border:1px solid #2a4060;color:#8ab4d4;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:0.75rem;width:100%">'
      +'&#9660; Filter by FB ('+_pinFbAll.length+')</button>'
      +'<div id="'+fbDropId+'" style="display:none;position:absolute;z-index:100;top:100%;left:0;right:0;background:#141c2e;border:1px solid #2a4060;border-radius:4px;max-height:160px;overflow-y:auto;box-shadow:0 4px 12px rgba(0,0,0,0.5)">'
      +fbCbs+'</div>'
    +'</div>'
    // rows
    +'<div id="'+_fpId+'" style="max-height:220px;overflow-y:auto;border:1px solid #1e2d45;border-radius:4px">'
    +interactiveRows+'</div>'
    // stats panel (hidden until pin clicked)
    +'<div id="'+_fpDetailId+'" style="display:none;margin-top:8px;background:#0d1520;border:1px solid #1e3050;border-radius:6px;padding:8px 10px;font-size:0.78rem"></div>';

  // wire FB filter + row click after render
  if(_flowPins.length){
    setTimeout(function(){
      // FB filter checkboxes
      document.querySelectorAll('input[data-fpid="'+_fpId+'"]').forEach(function(cb){
        cb.addEventListener('change', function(){
          var active={};
          document.querySelectorAll('input[data-fpid="'+_fpId+'"]').forEach(function(c){ if(c.checked) active[c.getAttribute('data-fpfb')]=1; });
          var container=document.getElementById(_fpId);
          if(!container)return;
          container.querySelectorAll('[data-pfb]').forEach(function(el){
            el.style.display = active[el.getAttribute('data-pfb')] ? '' : 'none';
          });
        });
      });
      // close dropdown on outside click
      document.addEventListener('click', function(ev){
        var drop=document.getElementById(fbDropId);
        if(!drop)return;
        var btn=drop.previousSibling;
        if(!drop.contains(ev.target)&&(!btn||!btn.contains(ev.target))){
          drop.style.display='none';
        }
      });
      // right column row click → stats panel
      document.querySelectorAll('[data-fpid2="'+_fpId+'"]').forEach(function(row){
        row.addEventListener('click', function(){
          document.querySelectorAll('[data-fpid2="'+_fpId+'"]').forEach(function(r){
            r.style.borderLeftColor='transparent'; r.style.background=''; r.dataset.sel='';
          });
          this.style.borderLeftColor='#4a9fd4'; this.style.background='#0f1a2a'; this.dataset.sel='1';
          var idx=+this.getAttribute('data-fpirow');
          var p=grpData.top_pins[idx];
          var det=document.getElementById(_fpDetailId);
          if(!det||!p)return;
          det.style.display='block';
          det.innerHTML=_fpStatHtml(p);
        });
      });
      // left column row click → stats panel (same)
      document.querySelectorAll('[data-fpid3="'+_fpId+'"]').forEach(function(row){
        row.addEventListener('click', function(){
          document.querySelectorAll('[data-fpid3="'+_fpId+'"]').forEach(function(r){
            r.style.borderLeftColor='transparent'; r.style.background=''; r.dataset.sel='';
          });
          this.style.borderLeftColor='#ffd166'; this.style.background='#0f1a2a'; this.dataset.sel='1';
          var idx=+this.getAttribute('data-lrow');
          var allMode=this.getAttribute('data-lmode')==='a';
          var src=allMode?(grpData.all_lim_pins||[]):grpData.top_pins;
          var p=src[idx];
          var det=document.getElementById(_fpDetailId);
          if(!det||!p)return;
          det.style.display='block';
          det.innerHTML=_fpStatHtml(p);
        });
      });
    }, 50);
  }
  var killLine = grpData.edc
    ? '<div style="font-size:0.77rem;color:#8899aa">No BIN8 kill — EDC is measurement-only</div>'
    : '<div style="font-size:0.77rem;color:#8899aa">BIN8 kills (filtered): <b style="color:#ff9966">'+liveStats.n+'</b></div>';
  // Following Phase Failure Preview removed from flow panel — see per-pin histogram modal.
  var nextPhasePanel = '';
  det.innerHTML =
    '<div style="display:flex;flex-wrap:wrap;gap:24px;align-items:flex-start">' +
    '<div style="min-width:240px">' +
    '<div style="font-size:0.9rem;font-weight:700;color:'+grpData.color+';margin-bottom:6px">'+grpData.label+
      (grpData.edc?'<span style="margin-left:7px;font-size:0.7rem;background:#5aabff22;color:#5aabff;border:1px solid #5aabff44;border-radius:3px;padding:1px 6px;vertical-align:middle">EDC</span>':'')+
    '</div>'+
    '<div style="font-size:0.78rem;color:#667788;margin-bottom:12px">Phase: '+grpData.phase+'</div>'+
    '<div style="font-size:0.77rem;color:#8899aa;margin-bottom:4px">CONT_ test instances: <b style="color:#c0ccd8">'+grpData.n_tests+'</b></div>'+
    killLine+
    '</div>'+
    (forceCells ? '<div><div style="font-size:0.77rem;font-weight:700;color:#8899aa;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Force Conditions</div>'+
    '<table style="border-collapse:collapse">'+forceCells+'</table></div>' : '')+
    edcTable+
    (!grpData.edc && (leftCol||topPinsHtml)
      ? '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start;min-width:480px;flex:2">'
          +'<div style="flex:1;min-width:200px">'
            +'<div style="font-size:0.77rem;font-weight:700;color:#8899aa;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Failing Pins <span style="font-weight:400;color:#445566">('+pinCount+' total)</span></div>'
            +leftCol
          +'</div>'
          +'<div style="flex:1;min-width:280px">'
            +'<div style="font-size:0.77rem;font-weight:700;color:#4a9fd4;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">&#9998; Pin Analysis <span style="font-weight:400;color:#445566;font-size:0.72rem">click a pin for stats</span></div>'
            +topPinsHtml
            +'<div style="font-size:0.7rem;color:#445566;margin-top:5px;line-height:1.4">&#9432; Counts = unique dies with that pin &gt; USL. One die may fail multiple pins.</div>'
            +nextPhasePanel
          +'</div>'
        +'</div>'
      : (!grpData.edc && nextPhasePanel ? '<div style="flex:2;min-width:300px">'+nextPhasePanel+'</div>' : ''))+
    '</div>';
}

// ── tab switch for left column Failing/All-pins toggle ────────────────────────
function _fpSwitchTab(fpId, allMode){
  var listF=document.getElementById(fpId+'_ll_f');
  var listA=document.getElementById(fpId+'_ll_a');
  var btnF=document.getElementById(fpId+'_lt_f');
  var btnA=document.getElementById(fpId+'_lt_a');
  if(listF) listF.style.display=allMode?'none':'';
  if(listA) listA.style.display=allMode?'':'none';
  if(btnF){btnF.style.background=allMode?'#0d1520':'#1e3050';btnF.style.color=allMode?'#445566':'#4a9fd4';}
  if(btnA){btnA.style.background=allMode?'#1e3050':'#0d1520';btnA.style.color=allMode?'#4a9fd4':'#445566';}
}

// ── Test Setup Sticky Popup ───────────────────────────────────────────────────
function showTestSetupPopup(groupId, groupLabel){
  var container = document.getElementById('ts-popup-container');
  if(!container) return;
  // Find the group data
  var grpData = null;
  (FLOW_DATA.phases||[]).forEach(function(ph){
    ph.groups.forEach(function(g){ if(g.id===groupId) grpData=g; });
  });
  if(!grpData) return;
  // Remove existing popup for same group (toggle)
  var existing = document.getElementById('ts-popup-'+groupId);
  if(existing){ existing.remove(); return; }
  var setup = grpData.test_setup || {};
  var hasSetup = Object.keys(setup).length > 0;
  // Build content
  var content = '';
  if(!hasSetup){
    content = '<div style="color:#445566;font-size:0.79rem;padding:4px 0">Test setup not available<br><span style="font-size:0.71rem">(no program directory or mtpl not found)</span></div>';
  } else {
    var cssOrder = ['VLC','LC','HC','HV'];
    cssOrder.forEach(function(cs){
      var sd = setup[cs];
      if(!sd) return;
      var forces = sd.pin_forces || {};
      var pinLims = sd.pin_limits || {};
      var allPins = Array.from(new Set(Object.keys(forces).concat(Object.keys(pinLims))));
      var pinRows = allPins.map(function(pin){
        var forceVal = forces[pin] || '';
        var lim = pinLims[pin] || [null, null, ''];
        var lsl = lim[0]; var usl = lim[1]; var ltype = (lim[2]||'').toLowerCase();
        function _fmtLim(v){
          if(v==null) return '\u2014';
          if(ltype==='voltage') return (v*1000).toFixed(1)+' mV';
          var ua=v*1e6;
          return (Math.abs(ua)>=0.1 ? ua.toFixed(2)+' \u00b5A' : (v*1e9).toFixed(1)+' nA');
        }
        var tag = (sd.json_override_pins && sd.json_override_pins.indexOf(pin)>=0)
          ? ' <span style="color:#ffd166;font-size:0.67rem">(JSON)</span>'
          : (forceVal ? ' <span style="color:#556677;font-size:0.67rem">(lvl)</span>' : '');
        var limCell = (lsl!=null||usl!=null)
          ? '<span style="color:#445566;font-size:0.67rem">LSL:</span><span style="color:#a0b8cc;font-family:monospace;font-size:0.72rem"> '+_fmtLim(lsl)+'</span>'
            +'<span style="color:#445566;font-size:0.67rem;margin-left:6px">USL:</span><span style="color:#a0b8cc;font-family:monospace;font-size:0.72rem"> '+_fmtLim(usl)+'</span>'
          : '';
        return '<tr>'
          +'<td style="padding:1px 10px 1px 8px;color:#8ab4d4;font-family:monospace;font-size:0.75rem;white-space:nowrap">'+pin+'</td>'
          +(forceVal ? '<td style="padding:1px 6px 1px 0;color:#69f0ae;font-family:monospace;font-size:0.78rem;font-weight:700">'+forceVal+'</td><td style="padding:1px 6px 1px 0">'+tag+'</td>' : '<td colspan="2"></td>')
          +'<td style="padding:1px 0 1px 6px">'+limCell+'</td>'
          +'</tr>';
      }).join('');
      content +=
        '<div style="margin-bottom:10px">'+
          '<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:4px">'+
            '<div style="font-size:0.74rem;font-weight:700;color:#8ab4d4;text-transform:uppercase;letter-spacing:.05em">Rail: '+cs+'</div>'+
            '<div style="font-size:0.68rem;color:#556677">← under test at cont. force &nbsp;·&nbsp; other rails at nominal</div>'+
          '</div>'+
          '<div style="font-size:0.71rem;color:#556677;margin-bottom:2px">Levels: <span style="color:#a0b8cc;font-family:monospace">'+( sd.levels_tc||'N/A')+'</span></div>'+
          '<div style="font-size:0.71rem;color:#556677;margin-bottom:4px">Config: <span style="color:#a0b8cc;font-family:monospace">'+(sd.config_file||'N/A')+'</span> / <span style="color:#a0b8cc;font-family:monospace">'+(sd.config_set||'N/A')+'</span>'+
            (sd.json_override?' <span style="color:#ffd166;font-size:0.68rem">\u26a0 JSON force override active</span>':'')+
          '</div>'+
          (pinRows
            ? '<table style="border-collapse:collapse">'
                +'<tr style="font-size:0.67rem;color:#445566"><th style="padding:0 10px 3px 8px;text-align:left">Pin</th><th style="padding:0 6px 3px 0;text-align:left" title="Continuity test force (forced low to check connection)">Cont. Force</th><th></th><th style="padding:0 0 3px 6px;text-align:left">Limits (from JSON)</th></tr>'
                +pinRows+'</table>'
            : '<div style="font-size:0.71rem;color:#445566">No pin data found</div>')+
        '</div>';
    });
    content += '<div style="font-size:0.69rem;color:#3a5a3a;background:#0a1a0a;border:1px solid #1e3a1e;border-radius:4px;margin-top:4px;padding:5px 8px">'
      +'<b style="color:#4ecdc4">ℹ</b> During each rail measurement: that rail\u2019s pins are forced to the continuity test voltage (shown above). '
      +'All other rails remain at their prior nominal operating voltage.</div>';
  }
  // Create popup
  var popup = document.createElement('div');
  popup.id = 'ts-popup-'+groupId;
  popup.style.cssText = 'pointer-events:all;background:#0d1828;border:1.5px solid #2a4060;border-radius:8px;'+
    'min-width:320px;max-width:420px;max-height:70vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.7);';
  // Title bar (draggable)
  var titleBar = document.createElement('div');
  titleBar.style.cssText = 'cursor:move;user-select:none;background:#131c30;border-bottom:1px solid #1e3050;padding:8px 10px;'+
    'display:flex;align-items:center;gap:8px;border-radius:8px 8px 0 0;';
  titleBar.innerHTML =
    '<span style="font-size:0.7rem;text-transform:uppercase;letter-spacing:.07em;color:#5aabff;font-weight:700">\u2139 Test Setup</span>'+
    '<span style="font-size:0.8rem;font-weight:700;color:#c0ccd8;flex:1">'+groupLabel+'</span>'+
    '<button id="ts-close-'+groupId+'" style="background:#1e2a3c;border:1px solid #2a4060;color:#8ab4d4;cursor:pointer;'+
      'font-size:0.75rem;font-weight:700;padding:2px 9px;border-radius:4px;flex-shrink:0" title="Close">\u2715</button>';
  popup.appendChild(titleBar);
  // Body
  var body = document.createElement('div');
  body.style.cssText = 'padding:10px 12px 10px;';
  body.innerHTML = content;
  popup.appendChild(body);
  container.appendChild(popup);
  // Close button
  document.getElementById('ts-close-'+groupId).addEventListener('click', function(){ popup.remove(); });
  // Drag
  var isDrag=false, ox=0, oy=0;
  titleBar.addEventListener('mousedown', function(e){
    isDrag=true; ox=e.clientX-popup.getBoundingClientRect().left; oy=e.clientY-popup.getBoundingClientRect().top;
    popup.style.position='fixed'; popup.style.bottom='auto'; popup.style.right='auto';
    container.style.display='block';
    e.preventDefault();
  });
  document.addEventListener('mousemove', function(e){
    if(!isDrag) return;
    popup.style.left=(e.clientX-ox)+'px'; popup.style.top=(e.clientY-oy)+'px';
  });
  document.addEventListener('mouseup', function(){ isDrag=false; });
}

function drawFlowDiagram(){
  var wrap = document.getElementById('flow-diagram');
  var det  = document.getElementById('flow-detail');
  try {
  if(!FLOW_DATA||!FLOW_DATA.phases||!FLOW_DATA.phases.length){
    wrap.innerHTML='<div style="padding:18px;color:#445566;font-size:0.82rem">No test flow data available. Program path not found or inaccessible.</div>';
    return;
  }
  // Note if FLW wasn't accessible (force conditions won't be shown)
  var flwWarn = document.getElementById('flow-flw-warn');
  if(flwWarn) flwWarn.style.display = FLOW_DATA.flw_ok ? 'none' : 'block';
  wrap.innerHTML='';
  var totalGroups=0, totalKills=0;
  FLOW_DATA.phases.forEach(function(ph){ totalGroups+=ph.groups.length; });

  FLOW_DATA.phases.forEach(function(phase, pi){
    var group=phase.groups[0];
    var ks=_flowKillStats(phase.label, group.edc);
    if(!group.edc) totalKills+=ks.n;
    var isSel=(_selFlowGroup===group.id);
    var col=document.createElement('div');
    col.style.cssText='display:flex;flex-direction:column;gap:8px;min-width:170px;max-width:210px;flex-shrink:0;';
    var hdr=document.createElement('div');
    hdr.style.cssText='background:#1e2a3c;border:1.5px solid '+phase.color+'66;border-radius:7px;padding:7px 10px;text-align:center;font-size:0.82rem;font-weight:700;color:'+phase.color+';white-space:nowrap;';
    hdr.textContent=phase.label;
    col.appendChild(hdr);
    var box=document.createElement('div');
    box.dataset.gid=group.id;
    box.style.cssText='cursor:pointer;border:2px solid '+(isSel?group.color:'#2a3a5a')+';background:'+(isSel?'#1e2a4a':'#171e2e')+';border-radius:7px;padding:9px 11px;transition:.12s;';
    var forceStr=Object.entries(group.force||{}).map(function(e){ return e[0]+':'+e[1]; }).join(' ');
    box.innerHTML=
      '<div style="font-size:0.81rem;font-weight:700;color:'+(isSel?group.color:'#c0d0e0')+';margin-bottom:3px;display:flex;align-items:center;justify-content:space-between">'+
        '<span>'+group.label+(group.edc?'<span style="margin-left:6px;font-size:0.66rem;background:#5aabff22;color:#5aabff;border:1px solid #5aabff44;border-radius:3px;padding:1px 5px;vertical-align:middle">EDC</span>':'')+'</span>'+
        '<button onclick="event.stopPropagation();showTestSetupPopup(\''+group.id+'\',\''+group.label+'\')" title="Test setup details" style="background:#1e2a3c;border:1px solid #3a5080;color:#7ab4d4;cursor:pointer;font-size:0.72rem;font-weight:700;padding:1px 6px;border-radius:4px;line-height:1.4;flex-shrink:0">&#8505;</button>'+
      '</div>'+
      (forceStr?'<div style="font-size:0.68rem;color:#7a9aba;font-family:monospace;margin-bottom:3px">'+forceStr+'</div>':'')+
      '<div style="font-size:0.67rem;color:#6a8aaa;margin-top:2px">'+group.n_tests+' CONT_ tests</div>'+
      (group.edc
        ?(ks.n?'<div style="margin-top:4px;font-size:0.67rem;background:#5aabff22;color:#5aabff;border:1px solid #5aabff44;border-radius:3px;display:inline-block;padding:1px 6px">EDC: '+ks.n+' supplies over limit</div>':'<div style="margin-top:4px;font-size:0.67rem;color:#556677">EDC &#8212; no failures</div>')
        :(ks.n?'<div style="margin-top:4px;font-size:0.67rem;background:#ff6b6b22;color:#ff9966;border:1px solid #ff6b6b44;border-radius:3px;display:inline-block;padding:1px 6px">BIN8: '+ks.n+' kills</div>':'<div style="margin-top:4px;font-size:0.67rem;color:#556677">&#10003; no kills</div>'));
    box.addEventListener('click',function(){ selectFlowGroup(group.id); });
    box.addEventListener('mouseenter',function(){ if(_selFlowGroup!==group.id) box.style.opacity='0.78'; });
    box.addEventListener('mouseleave',function(){ box.style.opacity='1'; });
    col.appendChild(box);
    wrap.appendChild(col);
    if(pi<FLOW_DATA.phases.length-1){
      var arr=document.createElement('div');
      arr.style.cssText='display:flex;align-items:flex-start;padding-top:38px;color:#3a4a6a;font-size:1.4rem;flex-shrink:0;';
      arr.textContent='\u2192';
      wrap.appendChild(arr);
    }
  });
  var sumEl=document.getElementById('flow-summary');
  if(sumEl) sumEl.textContent=totalGroups+' test groups \u00b7 '+totalKills+' BIN8 kills (filtered)';
  var cntEl=document.getElementById('flow-filter-cnt');
  if(cntEl) cntEl.textContent=totalKills+' kills shown';
  _ddLabelUpdate('flow-lot-btn','flow-lot-wfr-tree','lot');
  _ddLabelUpdate('flow-fb-btn','flow-ib-fb-tree','ib');
  // Auto-select: if a group was previously selected keep it; otherwise pick first group with kills
  if(_selFlowGroup){
    selectFlowGroup(_selFlowGroup, false);
  } else {
    var firstKill = null;
    for(var _fi=0;_fi<FLOW_DATA.phases.length;_fi++){
      var _fg=FLOW_DATA.phases[_fi].groups[0];
      if(!_fg.edc && _fg.bin8_kills>0){firstKill=_fg.id;break;}
    }
    if(!firstKill && FLOW_DATA.phases.length) firstKill=FLOW_DATA.phases[0].groups[0].id;
    if(firstKill) selectFlowGroup(firstKill, false);
  }
  } catch(e) {
    if(wrap) wrap.innerHTML='<div style="padding:18px;color:#ff6b6b;font-size:0.82rem;font-family:monospace">drawFlowDiagram error: '+e+'</div>';
    console.error('drawFlowDiagram error:', e);
  }
}

// ── ISVM EDC ──────────────────────────────────────────────────────────────────
var _edcRail = '';
var _edcDps  = '';
function _cbLabel(text, isActive, accentColor, extra) {
  var lbl = document.createElement('label');
  var bdr = isActive?accentColor:'#2a4060';
  var clr = isActive?accentColor:'#8ab4d4';
  lbl.style.cssText = 'display:inline-flex;align-items:center;gap:5px;cursor:pointer;font-size:0.78rem;font-weight:700;padding:3px 10px;background:#131a2a;border:1px solid '+bdr+';border-radius:5px;color:'+clr+(extra||'');
  var cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.style.accentColor = accentColor;
  cb.style.cursor = 'pointer';
  cb.checked = isActive;
  lbl.appendChild(cb);
  lbl.appendChild(document.createTextNode(' '+text));
  lbl._cb = cb;
  return lbl;
}
function edcRebuildDps(activeDps) {
  var dpsList = Object.keys(EDC_DATA.groups).sort();
  var box = document.getElementById('edc-dps-cbs');
  box.innerHTML = '';
  dpsList.forEach(function(dps){
    var lbl = _cbLabel(dps, dps===activeDps, '#4ecdc4');
    lbl._cb.addEventListener('change', function(){
      if(!lbl._cb.checked){lbl._cb.checked=true;return;}
      edcRebuildDps(dps);
      edcRebuildRails(dps, null);
    });
    box.appendChild(lbl);
  });
}
function edcRebuildRails(dps, activeRail) {
  var rails = EDC_DATA.groups[dps] || [];
  _edcDps = dps;
  if(!activeRail || rails.indexOf(activeRail)<0) activeRail = rails[0] || '';
  var box = document.getElementById('edc-rail-cbs');
  box.innerHTML = '';
  rails.forEach(function(r){
    var lbl = _cbLabel(r, r===activeRail, '#ffd166');
    lbl._cb.addEventListener('change', function(){
      if(!lbl._cb.checked){lbl._cb.checked=true;return;}
      edcRebuildRails(dps, r);
    });
    box.appendChild(lbl);
  });
  _edcRail = activeRail;
  document.getElementById('edc-rail-label').textContent = activeRail;
  drawEdc();
}
function initEdc(){
  var dpsList = Object.keys(EDC_DATA.groups).sort();
  var firstDps = dpsList[0] || '';
  edcRebuildDps(firstDps);
  edcRebuildRails(firstDps, null);
  buildEdcLimTbl();
}
function toggleEdcLimTbl(){
  var wrap = document.getElementById('edc-lim-tbl-wrap');
  var tog  = document.getElementById('edc-lim-tbl-toggle');
  var hdr  = tog.previousElementSibling;
  if(wrap.style.display==='none'){
    wrap.style.display='block';
    tog.textContent='(click to collapse)';
    hdr.textContent=hdr.textContent.replace('\u25ba','\u25bc');
  } else {
    wrap.style.display='none';
    tog.textContent='(click to expand)';
    hdr.textContent=hdr.textContent.replace('\u25bc','\u25ba');
  }
}
function buildEdcLimTbl(){
  var ls = EDC_DATA.limit_stats || {};
  var n8 = BIN8_COUNT;
  // group by DPS type, sorted
  var byDps = {};
  Object.keys(EDC_DATA.groups).sort().forEach(function(dps){
    (EDC_DATA.groups[dps]||[]).forEach(function(r){
      if(ls[r]) (byDps[dps]=byDps[dps]||[]).push(r);
    });
  });
  var hdr = '<thead><tr style="background:#131a2a;color:#8ab4d4;font-size:0.75rem">'
    +'<th style="padding:4px 10px;border:1px solid #2a4060;text-align:left">DPS</th>'
    +'<th style="padding:4px 10px;border:1px solid #2a4060;text-align:left">Rail</th>'
    +'<th style="padding:4px 10px;border:1px solid #2a4060;text-align:right">LSL (mV)</th>'
    +'<th style="padding:4px 10px;border:1px solid #2a4060;text-align:right">USL (mV)</th>'
    +'<th style="padding:4px 10px;border:1px solid #2a4060;text-align:right">BIN8 tested</th>'
    +'<th style="padding:4px 10px;border:1px solid #2a4060;text-align:right">&lt; LSL</th>'
    +'<th style="padding:4px 10px;border:1px solid #2a4060;text-align:right">&gt; USL</th>'
    +'<th style="padding:4px 10px;border:1px solid #2a4060;text-align:right">Any fail</th>'
    +'<th style="padding:4px 10px;border:1px solid #2a4060;text-align:right">Median (mV)</th>'
    +'<th style="padding:4px 10px;border:1px solid #2a4060;text-align:right">Worst (mV)</th>'
    +'</tr></thead>';
  var rows = '';
  var odd = false;
  Object.keys(byDps).sort().forEach(function(dps){
    var rails = byDps[dps];
    rails.forEach(function(r, ri){
      var s = ls[r];
      var nFail = s.n_lo + s.n_hi;
      var bgRow = odd ? 'background:#0d1420' : '';
      odd = !odd;
      var loCell = s.n_lo > 0
        ? '<td style="padding:4px 10px;border:1px solid #1e3050;text-align:right;color:#ffd166;font-weight:700">'+s.n_lo+'</td>'
        : '<td style="padding:4px 10px;border:1px solid #1e3050;text-align:right;color:#556677">0</td>';
      var hiCell = s.n_hi > 0
        ? '<td style="padding:4px 10px;border:1px solid #1e3050;text-align:right;color:#ff6b6b;font-weight:700">'+s.n_hi+'</td>'
        : '<td style="padding:4px 10px;border:1px solid #1e3050;text-align:right;color:#556677">0</td>';
      var anyCell = nFail > 0
        ? '<td style="padding:4px 10px;border:1px solid #1e3050;text-align:right;color:#e05c5c;font-weight:700">'+nFail+'</td>'
        : '<td style="padding:4px 10px;border:1px solid #1e3050;text-align:right;color:#556677">0</td>';
      rows += '<tr style="'+bgRow+'">'
        +'<td style="padding:4px 10px;border:1px solid #1e3050;color:#4ecdc4;font-size:0.72rem">'+dps+'</td>'
        +'<td style="padding:4px 10px;border:1px solid #1e3050;color:#e0eaf8;font-weight:700;cursor:pointer;text-decoration:underline dotted" onclick="edcJumpToRail(\''+dps+'\',\''+r+'\')">'+r+'</td>'
        +'<td style="padding:4px 10px;border:1px solid #1e3050;text-align:right;color:#667788">'+s.lsl.toFixed(1)+'</td>'
        +'<td style="padding:4px 10px;border:1px solid #1e3050;text-align:right;color:#667788">'+s.usl.toFixed(1)+'</td>'
        +'<td style="padding:4px 10px;border:1px solid #1e3050;text-align:right;color:#c0ccd8">'+s.n_total+'</td>'
        +loCell+hiCell+anyCell
        +'<td style="padding:4px 10px;border:1px solid #1e3050;text-align:right;color:#8ab4d4">'+s.median.toFixed(1)+'</td>'
        +'<td style="padding:4px 10px;border:1px solid #1e3050;text-align:right;color:'+(s.worst>s.usl?'#ff6b6b':s.worst>s.usl*0.9?'#ffd166':'#8ab4d4')+'">'+s.worst.toFixed(1)+'</td>'
        +'</tr>';
    });
  });
  document.getElementById('edc-lim-tbl').innerHTML = hdr+'<tbody>'+rows+'</tbody>';
}
function edcJumpToRail(dps, rail){
  // Select the DPS radio then the rail checkbox and redraw
  document.querySelectorAll('#edc-dps-cbs input[type=radio]').forEach(function(r){
    if(r.value===dps){ r.checked=true; edcRebuildRails(dps, rail); }
  });
  // expand if collapsed
  var wrap = document.getElementById('edc-lim-tbl-wrap');
  if(wrap.style.display==='none') toggleEdcLimTbl();
}
function drawEdc(){
  var rail = _edcRail;
  var meta = EDC_DATA.meta[rail];
  var rd   = EDC_DATA.dies.filter(function(d){ return d[rail]; });
  if(!meta||rd.length===0){Plotly.purge('edc-scatter');Plotly.purge('edc-delta');return;}
  var prePP  = meta.pre_p99, postPP = meta.post_p99;
  var preUSL = (meta.usl != null) ? meta.usl : 1000.0;  // program USL in mV
  var Q_COLORS = ['#ff6b6b','#ffd166','#48cae4','#556677'];
  var Q_LABELS = [
    'EDC over-range & VSIM over-range \u2014 defect visible to both methods',
    'EDC normal, VSIM over-range \u2014 VSIM-only tail (EDC insensitive)',
    'EDC over-range, VSIM normal \u2014 EDC-only flag (VSIM healed or different mode)',
    'Both normal \u2014 no rail elevation detected'];
  var Q_INTERP = [
    'Consistent structural defect; both measurement methods agree.',
    'Defect NOT caught by EDC current-force method; VSIM CONT measurement sees it post-surge.',
    'EDC flags marginal contact; VSIM measurement post-surge is within spec.',
    'Rail not elevated by either method; BIN8 from another mechanism.'];
  var q = [[],[],[],[]];
  rd.forEach(function(d){
    var hp=d[rail].pre>prePP, hP=d[rail].post>postPP;
    q[hp&&hP?0:(!hp&&hP?1:(hp&&!hP?2:3))].push(d);
  });
  // Actual program USL exceedance count + update summary line
  var nUslExceed = rd.filter(function(d){ return d[rail].pre > preUSL; }).length;
  var _euN   = document.getElementById('edc-usl-n');
  var _euTot = document.getElementById('edc-usl-tot');
  var _euThr = document.getElementById('edc-usl-thresh');
  if(_euN)   _euN.textContent   = nUslExceed;
  if(_euTot) _euTot.textContent = rd.length;
  if(_euThr) _euThr.textContent = preUSL.toFixed(1);
  var maxPre  = Math.max(Math.max.apply(null,rd.map(function(d){return d[rail].pre;})), prePP*1.05, preUSL*1.02, 0.1);
  var maxPost = Math.max(Math.max.apply(null,rd.map(function(d){return d[rail].post;})), postPP*1.05, 0.1);
  var traces = q.map(function(pts,qi){
    return {type:'scatter',mode:'markers',name:Q_LABELS[qi]+'  (n='+pts.length+')',
      x:pts.map(function(p){return p[rail].pre;}),
      y:pts.map(function(p){return p[rail].post;}),
      marker:{color:Q_COLORS[qi],size:7,opacity:0.8},
      text:pts.map(function(p){return 'W'+p.wfr+' ('+p.x+','+p.y+')';}),
      hovertemplate:'%{text}<br>EDC: %{x:.2f} mV<br>VSIM: %{y:.2f} mV<extra></extra>'};
  });
  Plotly.react('edc-scatter', traces, L({
    title: rail+' \u2014 EDC (pre) vs VSIM POSTSURGE (post)',
    xaxis:{title:'ISVM EDC E_START (mV)', range:[0, maxPre]},
    yaxis:{title:'POSTSURGE K_START (mV)', range:[0, maxPost]},
    shapes:[
      {type:'line',x0:prePP, x1:prePP, y0:0,y1:maxPost,line:{color:'#ffd166',dash:'dash',width:1}},
      {type:'line',x0:0,x1:maxPre,y0:postPP,y1:postPP, line:{color:'#48cae4',dash:'dash',width:1}},
      {type:'line',x0:preUSL,x1:preUSL,y0:0,y1:maxPost,line:{color:'#ff6b6b',dash:'solid',width:1.5}},
    ],
    annotations:[
      {x:prePP,  y:maxPost*0.03,text:'EDC p99='+prePP.toFixed(1),      showarrow:false,font:{color:'#ffd166',size:9}},
      {x:maxPre*0.02,y:postPP,  text:'VSIM p99='+postPP.toFixed(1),    showarrow:false,font:{color:'#48cae4',size:9},xanchor:'left'},
      {x:preUSL, y:maxPost*0.12,text:'USL='+preUSL.toFixed(0)+'mV',showarrow:false,font:{color:'#ff6b6b',size:9},xanchor:'right'},
    ],
    legend:{font:{size:9},orientation:'h',y:-0.28},
    margin:{t:40,l:60,r:20,b:90}
  }), PC);
  var deltas = rd.map(function(d){
    var pre=d[rail].pre/prePP, post=d[rail].post/postPP;
    return post-pre;
  });
  var sorted = deltas.slice().sort(function(a,b){return a-b;});
  var medD = sorted[Math.floor(sorted.length/2)];
  Plotly.react('edc-delta',[
    {type:'histogram',x:deltas,nbinsx:40,marker:{color:'#4a9fd4',opacity:0.8},name:'\u0394 (post/p99 \u2212 pre/p99)'}
  ],L({
    title: rail+' \u2014 Normalized Delta (VSIM/p99 \u2212 EDC/p99)',
    xaxis:{title:'Normalized \u0394 (post/p99 \u2212 pre/p99)'},
    yaxis:{title:'Count'},
    shapes:[{type:'line',x0:0,x1:0,y0:0,y1:1,yref:'paper',line:{color:'#ff6b6b',dash:'dash',width:1.5}}],
    annotations:[{x:medD,y:0.92,yref:'paper',text:'median \u0394='+medD.toFixed(2),
      showarrow:true,arrowhead:2,arrowcolor:'#4ecdc4',font:{color:'#4ecdc4',size:10}}],
    margin:{t:40,l:50,r:20,b:50}
  }), PC);
  var tbl='<table style="width:auto;border-collapse:collapse;font-size:0.82rem"><thead><tr>'+
    '<th>Quadrant</th><th>EDC</th><th>VSIM</th>'+
    '<th style="text-align:right">Count</th><th style="text-align:right">%</th><th>Interpretation</th></tr></thead><tbody>';
  q.forEach(function(pts,qi){
    var pct=rd.length?(pts.length/rd.length*100).toFixed(1):'0';
    tbl+='<tr><td style="color:'+Q_COLORS[qi]+';font-weight:700;padding:6px 10px">Q'+(qi+1)+'</td>'+
      '<td style="padding:6px 10px;color:#8ab4d4">'+(qi===0||qi===2?'High':'Normal')+'</td>'+
      '<td style="padding:6px 10px;color:#8ab4d4">'+(qi===0||qi===1?'High':'Normal')+'</td>'+
      '<td style="padding:6px 10px;text-align:right"><b>'+pts.length+'</b></td>'+
      '<td style="padding:6px 10px;text-align:right;color:#556677">'+pct+'%</td>'+
      '<td style="padding:6px 10px;font-size:0.78rem;color:#8ab4d4">'+Q_INTERP[qi]+'</td></tr>';
  });
  tbl+='</tbody></table>';
  document.getElementById('edc-quad-tbl').innerHTML = tbl;
  var q2n = q[1].length, q1n = q[0].length, total = rd.length;
  var concl = '';
  // Program USL exceedance banner
  if(nUslExceed > 0){
    concl += '\u26a0\ufe0f <b style="color:#ff6b6b">'+nUslExceed+' die(s) exceed the program EDC USL ('+preUSL.toFixed(0)+'\u202fmV)</b> &mdash; '
      +'these are <b>actual test failures</b> by the program spec even though EDC uses a diagnostic exit port (FailPort=3) and does not set BIN8. '
      +'The red solid line on the scatter marks this threshold. ';
  } else {
    concl += '\u2705 <b>No program EDC USL exceedances for '+rail+'</b> (USL='+preUSL.toFixed(0)+'\u202fmV). '
      +'All BIN8 dies are within the ISVM spec even though they fail POSTSURGE VSIM. ';
  }
  // Quadrant commentary
  if(q2n > 0){
    concl += '\u26a0\ufe0f <b>'+q2n+' die(s) show VSIM over-range but EDC within p99</b> (Q2 \u2014 VSIM-only tail). '
      +'The ISVM EDC current-force measurement does <b>not detect</b> this failure mode &mdash; the defect is only visible in the POSTSURGE VSIM resistance measurement. ';
  } else {
    concl += '\u2705 <b>No VSIM-only tail for '+rail+'.</b> ';
  }
  if(q1n > 0) concl += ' <b>'+q1n+'</b> die(s) are flagged by both methods (Q1 \u2014 consistent structural defect).';
  concl += ' Normalized median delta = <b>'+medD.toFixed(2)+'</b> (0 = methods agree in severity).';
  document.getElementById('edc-conclusion').innerHTML =
    '<div style="background:#0f1520;border-left:3px solid #ffd166;padding:12px 18px;border-radius:0 6px 6px 0;'+
    'margin-top:4px;font-size:0.82rem;color:#c0ccd8;line-height:1.8">'+
    '<b style="color:#ffd166">&#x25cf; Conclusion &mdash; '+rail+':</b> '+concl+'</div>';
}

// ── SURGE DELTA ───────────────────────────────────────────────────────────────
var _sgRail = '';
var _sgDps  = '';
function sgRebuildDps(activeDps) {
  var dpsList = Object.keys(SURGE_DATA.dps_groups).sort();
  var box = document.getElementById('sg-dps-cbs');
  box.innerHTML = '';
  dpsList.forEach(function(dps){
    var lbl = _cbLabel(dps, dps===activeDps, '#4ecdc4');
    lbl._cb.addEventListener('change', function(){
      if(!lbl._cb.checked){lbl._cb.checked=true;return;}
      sgRebuildDps(dps);
      sgRebuildRails(dps, null);
    });
    box.appendChild(lbl);
  });
}
function sgRebuildRails(dps, activeRail) {
  var rails = SURGE_DATA.dps_groups[dps] || [];
  _sgDps = dps;
  if(!activeRail || rails.indexOf(activeRail)<0) activeRail = rails[0] || '';
  var box = document.getElementById('sg-rail-cbs');
  box.innerHTML = '';
  rails.forEach(function(r){
    var lbl = _cbLabel(r, r===activeRail, '#4ecdc4');
    lbl._cb.addEventListener('change', function(){
      if(!lbl._cb.checked){lbl._cb.checked=true;return;}
      sgRebuildRails(dps, r);
    });
    box.appendChild(lbl);
  });
  _sgRail = activeRail;
  document.getElementById('sg-rail-label').textContent = activeRail;
  drawSurge();
}
function initSurge(){
  var dpsList = Object.keys(SURGE_DATA.dps_groups).sort();
  // Default to HV if present, else first
  var firstDps = dpsList.indexOf('HV')>=0 ? 'HV' : (dpsList[0]||'');
  sgRebuildDps(firstDps);
  sgRebuildRails(firstDps, 'VCCIA');
}
function drawSurge(){
  var rail = _sgRail;
  var meta = SURGE_DATA.meta[rail];
  var allDies = SURGE_DATA.dies;
  var rd = allDies.filter(function(d){return d[rail];});
  document.getElementById('sg-n-total').textContent = rd.length;
  if(!meta||rd.length===0){Plotly.purge('sg-scatter');Plotly.purge('sg-delta');return;}
  var prePP  = meta.pre_p99;
  var postPP = meta.post_p99;
  var Q_COLORS = ['#ff6b6b','#ffd166','#48cae4','#556677'];
  var Q_LABELS = [
    'High pre + high post \u2014 pre-existing defect (Mode 1/2/3)',
    'Normal pre + high post \u2014 surge-induced damage',
    'High pre + normal post \u2014 pre-existing, healed by surge',
    'Both normal \u2014 no resistance elevation (unrelated BIN8)'];
  var Q_INTERP = [
    'Structural defect present before surge; surge does not change it.',
    'Surge created or worsened the resistance elevation.',
    'Pre-existing marginal defect that normalized post-surge.',
    'BIN8 killed by another mechanism \u2014 not rail-resistance based.'];
  var q = [[],[],[],[]];
  rd.forEach(function(d){
    var pre=d[rail].pre, post=d[rail].post;
    var hp=pre>prePP, hP=post>postPP;
    q[hp&&hP?0:(!hp&&hP?1:(hp&&!hP?2:3))].push({pre:pre,post:post,wfr:d.wfr,x:d.x,y:d.y,lot:d.lot});
  });
  var maxVal = Math.max(
    Math.max.apply(null,rd.map(function(d){return d[rail].pre;})),
    Math.max.apply(null,rd.map(function(d){return d[rail].post;})),
    postPP*1.1,prePP*1.1,0.1
  );
  var traces = q.map(function(pts,qi){
    return {type:'scatter',mode:'markers',name:Q_LABELS[qi]+'  (n='+pts.length+')',
      x:pts.map(function(p){return p.pre;}),
      y:pts.map(function(p){return p.post;}),
      marker:{color:Q_COLORS[qi],size:7,opacity:0.8,line:{width:0}},
      text:pts.map(function(p){return 'W'+p.wfr+' ('+p.x+','+p.y+')';}),
      hovertemplate:'%{text}<br>Pre: %{x:.2f} mV<br>Post: %{y:.2f} mV<extra></extra>'};
  });
  traces.push({type:'scatter',mode:'lines',name:'y = x (\u0394=0)',showlegend:false,
    x:[0,maxVal],y:[0,maxVal],line:{color:'#2a4060',dash:'dot',width:1.5},hoverinfo:'skip'});
  Plotly.react('sg-scatter',traces,L({
    title:rail+' \u2014 Pre vs Post Surge (mV)',
    xaxis:{title:'Pre-Surge (mV)',range:[0,maxVal]},
    yaxis:{title:'Post-Surge (mV)',range:[0,maxVal]},
    shapes:[
      {type:'line',x0:prePP,x1:prePP,y0:0,y1:maxVal,line:{color:'#ffd166',dash:'dash',width:1}},
      {type:'line',x0:0,x1:maxVal,y0:postPP,y1:postPP,line:{color:'#ffd166',dash:'dash',width:1}},
    ],
    annotations:[
      {x:prePP,y:maxVal*0.03,text:'pre p99='+prePP.toFixed(1),showarrow:false,font:{color:'#ffd166',size:9}},
      {x:maxVal*0.02,y:postPP,text:'post p99='+postPP.toFixed(1),showarrow:false,font:{color:'#ffd166',size:9},xanchor:'left'},
    ],
    legend:{font:{size:9},orientation:'h',y:-0.25},
    margin:{t:40,l:60,r:20,b:80}
  }),PC);
  var deltas = rd.map(function(d){return d[rail].post-d[rail].pre;});
  var sorted = deltas.slice().sort(function(a,b){return a-b;});
  var medD = sorted[Math.floor(sorted.length/2)];
  Plotly.react('sg-delta',[
    {type:'histogram',x:deltas,nbinsx:40,marker:{color:'#4a9fd4',opacity:0.8},name:'\u0394 = Post \u2212 Pre'}
  ],L({
    title:rail+' \u2014 Surge Delta (\u0394 = Post \u2212 Pre)',
    xaxis:{title:'\u0394 Resistance (mV)'},
    yaxis:{title:'Count'},
    shapes:[{type:'line',x0:0,x1:0,y0:0,y1:1,yref:'paper',line:{color:'#ff6b6b',dash:'dash',width:1.5}}],
    annotations:[{x:medD,y:0.95,yref:'paper',text:'median \u0394='+medD.toFixed(2)+'mV',
      showarrow:true,arrowhead:2,arrowcolor:'#4ecdc4',font:{color:'#4ecdc4',size:10}}],
    margin:{t:40,l:50,r:20,b:50}
  }),PC);
  var tbl='<table style="width:auto;border-collapse:collapse;font-size:0.82rem"><thead><tr>'+
    '<th>Quadrant</th><th>Pre (mV)</th><th>Post (mV)</th><th style="text-align:right">Count</th><th style="text-align:right">%</th><th>Interpretation</th></tr></thead><tbody>';
  q.forEach(function(pts,qi){
    var pct=rd.length?(pts.length/rd.length*100).toFixed(1):'0';
    tbl+='<tr><td style="color:'+Q_COLORS[qi]+';font-weight:700;padding:6px 10px">'+Q_LABELS[qi].split('\u2014')[0].trim()+'</td>'+
      '<td style="padding:6px 10px;color:#8ab4d4">'+(qi===0||qi===2?'High':'Normal')+'</td>'+
      '<td style="padding:6px 10px;color:#8ab4d4">'+(qi===0||qi===1?'High':'Normal')+'</td>'+
      '<td style="padding:6px 10px;text-align:right"><b>'+pts.length+'</b></td>'+
      '<td style="padding:6px 10px;text-align:right;color:#556677">'+pct+'%</td>'+
      '<td style="padding:6px 10px;font-size:0.78rem;color:#8ab4d4">'+Q_INTERP[qi]+'</td></tr>';
  });
  tbl+='</tbody></table>';
  document.getElementById('sg-quad-tbl').innerHTML=tbl;
  var q2n=q[1].length;
  var concl = q2n===0
    ? '\u2705 <b>No surge-induced damage detected for '+rail+'.</b> Zero dies show the &ldquo;normal pre-surge + high post-surge&rdquo; pattern. '+
      'The surge event does <b>not create or worsen resistance elevation</b> &mdash; all elevated readings were pre-existing. '+
      'This confirms a <b>manufacturing / assembly defect</b>, not a test-induced artifact.'
    : '\u26a0\ufe0f <b>'+q2n+' die(s) show possible surge-induced damage on '+rail+'</b>: normal pre-surge but elevated post-surge. Requires further investigation.';
  concl += ' Median surge delta = <b>'+medD.toFixed(2)+'&thinsp;m&Omega;</b> (expected 0 if no surge effect). '+
    q[0].length+' of '+rd.length+' paired dies have elevation on both measurements.';
  document.getElementById('sg-conclusion').innerHTML=
    '<div style="background:#0f1520;border-left:3px solid #4ecdc4;padding:12px 18px;border-radius:0 6px 6px 0;'+
    'margin-top:4px;font-size:0.82rem;color:#c0ccd8;line-height:1.8">'+
    '<b style="color:#4ecdc4">&#x25cf; Conclusion &mdash; '+rail+':</b> '+concl+'</div>';
}

// ── Init ──────────────────────────────────────────────────────────────────────
(function init(){
  // Populate prog info bar
  var nameEl=document.getElementById('prog-info-name');
  if(nameEl){nameEl.textContent=_curProg;}
  try{ initOverview(); } catch(e){ console.error('initOverview failed:',e); }
})();
</script>

<!-- COMPOSITE OVERLAY -->
<div id="comp-overlay">
<!-- top bar: title + buttons -->
<div class="cp-topbar">
  <h2 class="cp-h2" style="border:none;margin:0;font-size:0.9rem">&#9707; Composite View &#8212; BIN8</h2>
  <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <label style="font-size:0.72rem;color:#667788;cursor:pointer;display:flex;align-items:center;gap:4px">
      <input type="checkbox" id="wm-ret-toggle" onchange="drawWM();if(typeof _cpRender==='function')_cpRender();" checked style="accent-color:#c77dff;cursor:pointer"> Reticle overlay
    </label>
    <label style="font-size:0.72rem;color:#80d4cc;cursor:pointer;display:flex;align-items:center;gap:4px">
      <input type="checkbox" id="wm-dieloc-toggle" onchange="drawWM();if(typeof _cpRender==='function')_cpRender();" style="accent-color:#80d4cc;cursor:pointer"> Die-loc #
    </label>
    <button id="cp-wpa-btn" style="display:none"></button>
    <button onclick="var s=document.getElementById('cp-pat-section');var wasHidden=s.style.display==='none'||!s.style.display;s.style.display=wasHidden?'block':'none';if(wasHidden)_cpRender();" style="font-size:0.78rem;background:#1a2a1a;border:1px solid #2a6a2a;color:#4ecdc4;border-radius:4px;padding:3px 10px;cursor:pointer" title="Toggle Pattern Score panel">&#128300; Pattern Score</button>
    <button onclick="_cpTogSiteSection()" style="font-size:0.78rem;background:#1a1a2a;border:1px solid #6a3aaa;color:#c77dff;border-radius:4px;padding:3px 10px;cursor:pointer" title="Reticle site map overlay + fail frequency heatmap">&#128205; Reticle Sites</button>
    <label style="font-size:0.72rem;color:#5b8dee;cursor:pointer;display:flex;align-items:center;gap:4px" title="Show/hide Reticle Shot Analysis panel">
      <input type="checkbox" id="wm-shot-toggle" onchange="_cpTogShotSection()" checked style="accent-color:#5b8dee;cursor:pointer"> Shot Analysis
    </label>
  </div>
</div>
<div class="cp-layout">
  <!-- PANE 1: Filters — hidden; containers kept in DOM for _cpShowOverlay state -->
  <div class="cp-pane-1" style="display:none">
    <div class="cp-flab" style="margin-bottom:6px;font-size:0.75rem;color:#8ab4d4;font-weight:700">Filters</div>
    <div style="flex:1;overflow-y:auto;overflow-x:hidden;min-height:0">
    <div class="cp-filt" style="min-width:0">
      <div class="cp-frow" style="flex-direction:column;gap:8px">

        <!-- Program dropdown -->
        <div class="cp-dd-wrap">
          <button class="cp-dd-btn" onclick="_cpTogDrop('cp-prog-drop')" id="cp-prog-btn">Program <span id="cp-prog-sel-cnt"></span> &#9660;</button>
          <div id="cp-prog-drop" class="cp-dd-panel">
            <div class="cp-dd-acts"><button onclick="_cpTogAll('cp-prog',true)">All</button><button onclick="_cpTogAll('cp-prog',false)">None</button></div>
            <input class="cp-dd-search" placeholder="Search..." oninput="_cpFilterDrop('cp-prog',this.value)">
            <div id="cp-prog" class="cp-fcol" style="max-height:160px;overflow-y:auto"></div>
          </div>
        </div>

        <!-- Lot dropdown -->
        <div class="cp-dd-wrap">
          <button class="cp-dd-btn" onclick="_cpTogDrop('cp-lot-drop')" id="cp-lot-btn">Lot <span id="cp-lot-sel-cnt"></span> &#9660;</button>
          <div id="cp-lot-drop" class="cp-dd-panel">
            <div class="cp-dd-acts"><button onclick="_cpTogAll('cp-lot',true)">All</button><button onclick="_cpTogAll('cp-lot',false)">None</button></div>
            <input class="cp-dd-search" placeholder="Search..." oninput="_cpFilterDrop('cp-lot',this.value)">
            <div id="cp-lot" class="cp-fcol" style="max-height:200px;overflow-y:auto"></div>
          </div>
        </div>

        <!-- Wafer dropdown -->
        <div class="cp-dd-wrap">
          <button class="cp-dd-btn" onclick="_cpTogDrop('cp-wfr-drop')" id="cp-wfr-btn">Wafer <span id="cp-wfr-sel-cnt"></span> &#9660;</button>
          <div id="cp-wfr-drop" class="cp-dd-panel">
            <div class="cp-dd-acts"><button onclick="_cpTogAll('cp-wfr',true)">All</button><button onclick="_cpTogAll('cp-wfr',false)">None</button></div>
            <input class="cp-dd-search" placeholder="Search..." oninput="_cpFilterDrop('cp-wfr',this.value)">
            <div id="cp-wfr" class="cp-fcol" style="max-height:220px;overflow-y:auto"></div>
          </div>
        </div>

        <!-- Func Bin dropdown -->
        <div class="cp-dd-wrap">
          <button class="cp-dd-btn" onclick="_cpTogDrop('cp-fb-drop')" id="cp-fb-btn">Func Bin <span id="cp-fb-sel-cnt"></span> &#9660;</button>
          <div id="cp-fb-drop" class="cp-dd-panel">
            <div class="cp-dd-acts"><button onclick="_cpTogAll('cp-fb',true)">All</button><button onclick="_cpTogAll('cp-fb',false)">None</button></div>
            <input class="cp-dd-search" placeholder="Search..." oninput="_cpFilterDrop('cp-fb',this.value)">
            <div id="cp-fb" class="cp-fcol" style="max-height:200px;overflow-y:auto"></div>
          </div>
        </div>

        <!-- Failing Pin dropdown -->
        <div class="cp-dd-wrap">
          <button class="cp-dd-btn" onclick="_cpTogDrop('cp-pin-drop')" id="cp-pin-btn">Failing Pin <span id="cp-pin-sel-cnt"></span> &#9660;</button>
          <div id="cp-pin-drop" class="cp-dd-panel">
            <div class="cp-dd-acts"><button onclick="_cpTogAll('cp-pin',true)">All</button><button onclick="_cpTogAll('cp-pin',false)">None</button></div>
            <input class="cp-dd-search" placeholder="Search..." oninput="_cpFilterDrop('cp-pin',this.value)">
            <div id="cp-pin" class="cp-fcol" style="max-height:220px;overflow-y:auto"></div>
          </div>
        </div>

        <div>
          <div class="cp-flab">Color by</div>
          <select id="cp-color" onchange="_cpRender()" style="background:#1a2235;border:1px solid #2a4060;color:#c0ccd8;padding:3px 6px;font-size:0.72rem;border-radius:4px;margin-top:2px;width:100%">
            <option value="fbin">Functional Bin</option>
            <option value="phase">Kill Phase</option>
            <option value="rtype">Rail Type</option>
            <option value="pin">Failing Pin (CS)</option>
            <option value="site">Reticle Site #</option>
            <option value="freq">Fail Freq %</option>
          </select>
        </div>
      </div>
    </div>
    </div>
  </div>
  <!-- resize handle hidden since pane-1 is hidden -->
  <div class="cp-vresize-handle" style="display:none" onmousedown="_cpStartResizeH(event,'cp-pane-1')"></div>
  <!-- PANE 2: Composite wafer + Reticle Site -->
  <div class="cp-pane-2">
    <div id="cp-map-scroll" style="flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;display:flex;flex-direction:column;align-items:center;justify-content:center">
      <div id="comp-svg" style="text-align:center"></div>
      <div id="comp-legend" style="font-size:10px;color:#8ab4d4;margin-top:4px;text-align:center"></div>
    </div>
    <div class="cp-resize-handle" onmousedown="_cpStartResize(event,'cp-site-inner','up')"></div>
    <div id="cp-site-section" style="display:block;border-top:1px solid #2a1a50;flex-shrink:0">
      <div id="cp-site-inner" style="display:flex;flex-direction:column;height:28vh;min-height:60px;overflow:hidden">
        <div style="display:flex;align-items:center;justify-content:space-between;padding-top:6px;margin-bottom:4px;flex-shrink:0">
          <span class="cp-h2" style="border:none;margin:0;font-size:0.78rem">&#128205; Reticle Site Analysis</span>
          <div style="display:flex;gap:4px;align-items:center">
            <button onclick="document.getElementById('wm-color').value='site';onWmFilter();_cpRender()" style="font-size:0.68rem;background:#1a1a2a;border:1px solid #6a3aaa;color:#c77dff;border-radius:3px;padding:2px 7px;cursor:pointer">Color by Site</button>
            <button onclick="document.getElementById('wm-color').value='freq';onWmFilter();_cpRender()" style="font-size:0.68rem;background:#1a2235;border:1px solid #2a4060;color:#4a9fd4;border-radius:3px;padding:2px 7px;cursor:pointer">Freq % Map</button>
            <button onclick="document.getElementById('cp-site-section').style.display='none'" style="font-size:0.72rem;background:#1a2235;border:1px solid #445566;color:#8ab4d4;border-radius:4px;padding:1px 8px;cursor:pointer">&#10005;</button>
          </div>
        </div>
        <div id="cp-site-body" style="overflow-y:auto;overscroll-behavior:contain;flex:1;min-height:0"></div>
      </div>
    </div>
    <div class="cp-resize-handle" onmousedown="_cpStartResize(event,'cp-shot-inner','up')"></div>
    <div id="cp-shot-section" style="display:block;border-top:1px solid #1a2a50;flex-shrink:0">
      <div id="cp-shot-inner" style="display:flex;flex-direction:column;height:26vh;min-height:60px;overflow:hidden">
        <div style="display:flex;align-items:center;justify-content:space-between;padding-top:6px;margin-bottom:4px;flex-shrink:0">
          <span class="cp-h2" style="border:none;margin:0;font-size:0.78rem">&#9654; Reticle Shot Analysis</span>
          <div style="display:flex;gap:4px;align-items:center">
            <button onclick="document.getElementById('wm-color').value='freq';onWmFilter();_cpRender()" style="font-size:0.68rem;background:#1a2235;border:1px solid #2a4060;color:#4a9fd4;border-radius:3px;padding:2px 7px;cursor:pointer">Freq % Map</button>
            <button onclick="_cpTogShotSection()" style="font-size:0.72rem;background:#1a2235;border:1px solid #445566;color:#8ab4d4;border-radius:4px;padding:1px 8px;cursor:pointer">&#10005;</button>
          </div>
        </div>
        <div id="cp-shot-body" style="overflow-y:auto;overscroll-behavior:contain;flex:1;min-height:0"></div>
      </div>
    </div>
  </div>
  <div class="cp-vresize-handle" onmousedown="_cpStartResizeH(event,'cp-pane-2')"></div>
  <!-- PANE 3: Per-wafer tiles + Pattern Score -->
  <div class="cp-pane-3">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;flex-shrink:0">
      <h2 class="cp-h2" id="tiles-hdr" style="border:none;margin:0;font-size:0.82rem">Per-Wafer Tiles</h2>
    </div>
    <div id="tiles-div" style="flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;display:flex;flex-wrap:wrap;gap:8px;align-items:flex-start"></div>
    <div class="cp-resize-handle" onmousedown="_cpStartResize(event,'cp-pat-inner','up')"></div>
    <div id="cp-pat-section" style="display:block;border-top:2px solid #1e3050;flex-shrink:0">
      <div id="cp-pat-inner" style="display:flex;flex-direction:column;height:32vh;min-height:60px;overflow:hidden">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;padding-top:6px;flex-shrink:0">
          <span class="cp-h2" style="border:none;margin:0;font-size:0.78rem">&#128300; Pattern Score</span>
          <button onclick="document.getElementById('cp-pat-section').style.display='none'" style="font-size:0.72rem;background:#1a2235;border:1px solid #445566;color:#8ab4d4;border-radius:4px;padding:1px 8px;cursor:pointer">&#10005;</button>
        </div>
        <div style="overflow-y:auto;overscroll-behavior:contain;flex:1;min-height:0">
          <div id="cp-pat-bars" style="display:flex;flex-direction:column;gap:4px;margin-bottom:8px"></div>
          <div id="cp-pat-conclusion" style="font-size:0.78rem;line-height:1.6;color:#9ab4cc;background:#0a1018;border:1px solid #1e3050;border-radius:6px;padding:8px 10px"></div>
        </div>
      </div>
    </div>
  </div>
</div>
</div>

</div>
</body>
</html>
"""

# ── GUI (merged from gui.py) ────────────────────────────────────────────────
import threading, zipfile, gzip, tempfile, shutil

def _gui_resolve_csv(path, log_fn):
    """Extract/decompress CSV from ZIP / GZ / 7Z to a temp dir; return (csv_path, tmp_dir)."""
    low = path.lower()
    if low.endswith('.zip'):
        log_fn(f'Extracting CSV from ZIP: {os.path.basename(path)}')
        tmp = tempfile.mkdtemp(prefix='vcccont_')
        with zipfile.ZipFile(path, 'r') as z:
            csvs = [n for n in z.namelist() if n.lower().endswith('.csv')]
            if not csvs:
                raise ValueError('No .csv file found inside the ZIP archive.')
            pref = [c for c in csvs if 'yield' in c.lower()]
            chosen = pref[0] if pref else csvs[0]
            z.extract(chosen, tmp)
            log_fn(f'  Using: {chosen}')
        return os.path.join(tmp, chosen), tmp
    if low.endswith('.gz'):
        log_fn(f'Decompressing GZ: {os.path.basename(path)}')
        tmp = tempfile.mkdtemp(prefix='vcccont_')
        base = os.path.basename(path[:-3]) if low.endswith('.csv.gz') else os.path.splitext(os.path.basename(path))[0] + '.csv'
        out_path = os.path.join(tmp, base)
        with gzip.open(path, 'rb') as gz_in, open(out_path, 'wb') as f_out:
            shutil.copyfileobj(gz_in, f_out)
        log_fn(f'  Decompressed to: {base}')
        return out_path, tmp
    if low.endswith('.7z'):
        log_fn(f'Extracting CSV from 7Z: {os.path.basename(path)}')
        tmp = tempfile.mkdtemp(prefix='vcccont_')
        try:
            import py7zr
            with py7zr.SevenZipFile(path, mode='r') as z:
                all_names = z.getnames()
                csvs = [n for n in all_names if n.lower().endswith('.csv')]
                if not csvs:
                    raise ValueError('No .csv file found inside the 7Z archive.')
                pref = [c for c in csvs if 'yield' in c.lower()]
                chosen = pref[0] if pref else csvs[0]
                z.extract(path=tmp, targets=[chosen])
                log_fn(f'  Using: {chosen}')
            return os.path.join(tmp, chosen), tmp
        except ImportError:
            # fallback: try 7z.exe on PATH
            import subprocess as _sp
            result = _sp.run(['7z', 'e', path, '-o' + tmp, '*.csv', '-r', '-y'],
                             capture_output=True, text=True)
            csvs = [os.path.join(tmp, f) for f in os.listdir(tmp) if f.lower().endswith('.csv')]
            if not csvs:
                raise ValueError('No .csv extracted from 7Z. Install py7zr or ensure 7z.exe is on PATH.')
            pref = [c for c in csvs if 'yield' in os.path.basename(c).lower()]
            chosen = pref[0] if pref else csvs[0]
            log_fn(f'  Using: {os.path.basename(chosen)}')
            return chosen, tmp
    return path, None


try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    _TK_AVAILABLE = True
except ImportError:
    _TK_AVAILABLE = False

if _TK_AVAILABLE:
    # colour constants
    _BG      = '#0d1525'
    _BG2     = '#141c2e'
    _BG3     = '#1a2235'
    _BORDER  = '#1e3050'
    _FG      = '#c0ccd8'
    _FG_DIM  = '#556677'
    _ACCENT  = '#4a9fd4'
    _GREEN   = '#4ecdc4'
    _RED     = '#ff6b6b'
    _GOLD    = '#ffd166'
    _FONT    = ('Segoe UI', 10)
    _FONT_SM = ('Segoe UI', 9)
    _FONT_HD = ('Segoe UI', 12, 'bold')

    class _App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title('VccCont BIN8 Dashboard Generator')
            self.resizable(True, True)
            self.minsize(620, 520)
            self.configure(bg=_BG)
            self._last_html = tk.StringVar(value='')
            self._tmp_dir   = None
            self._proc      = None
            self._build_ui()
            self._refresh_open_btn()
            if os.path.isfile(_DEFAULT_CSV):
                self._csv_var.set(_DEFAULT_CSV)
            if os.path.isdir(_DEFAULT_PROG):
                self._prog_var.set(_DEFAULT_PROG)

        def _build_ui(self):
            pad = dict(padx=18, pady=0)
            hdr = tk.Frame(self, bg=_BG, pady=16)
            hdr.pack(fill='x', **pad)
            tk.Label(hdr, text='VccCont BIN8 Dashboard', font=_FONT_HD,
                     bg=_BG, fg=_ACCENT).pack(anchor='w')
            tk.Label(hdr, text='Generate and open the BIN8 failure analysis dashboard from a yield CSV / ZIP / GZ / 7Z export.',
                     font=_FONT_SM, bg=_BG, fg=_FG_DIM, wraplength=560, justify='left').pack(anchor='w', pady=(2, 0))
            tk.Frame(self, bg=_BORDER, height=1).pack(fill='x', padx=18, pady=(0, 12))

            card = self._card(self, 'Inputs')
            card.pack(fill='x', padx=18, pady=(0, 10))

            self._csv_var = tk.StringVar()
            self._make_file_row(card, 'CSV / ZIP / GZ / 7Z', self._csv_var, self._browse_csv,
                                tip='Yield CSV export, or a ZIP / GZ / 7Z archive containing it')

            self._prog_var = tk.StringVar()
            self._prog_root_var = tk.StringVar()
            self._make_file_row(card, 'Program root', self._prog_root_var, self._browse_prog_root,
                                tip='Folder containing one subfolder per program name.')

            self._out_var = tk.StringVar()
            self._make_file_row(card, 'Output folder', self._out_var, self._browse_out,
                                tip='Dashboard HTML will be written here as vcccont-bin8-analysis.html')

            tk.Frame(card, bg=_BORDER, height=1).pack(fill='x', padx=14, pady=(4, 8))
            setup_row = tk.Frame(card, bg=_BG2)
            setup_row.pack(fill='x', padx=14, pady=(0, 10))
            tk.Label(setup_row, text='Setup file', font=_FONT_SM, bg=_BG2, fg=_FG,
                     width=14, anchor='w').pack(side='left')
            self._setup_var = tk.StringVar(value='')
            tk.Entry(setup_row, textvariable=self._setup_var, bg=_BG3, fg=_FG,
                     insertbackground=_FG, relief='flat', bd=0, font=_FONT_SM,
                     highlightbackground=_BORDER, highlightthickness=1
                     ).pack(side='left', fill='x', expand=True, ipady=5, padx=(0, 8))
            self._btn(setup_row, '📂  Load', self._on_load_setup, _GOLD).pack(side='left', padx=(0, 6))
            self._btn(setup_row, '💾  Save', self._on_save_setup, _GREEN).pack(side='left')
            tk.Label(card, text='Load or save all paths above as a JSON preset file.',
                     font=('Segoe UI', 8), bg=_BG2, fg=_FG_DIM, anchor='w'
                     ).pack(fill='x', padx=14, pady=(0, 8))

            live_row = tk.Frame(card, bg=_BG2)
            live_row.pack(fill='x', padx=14, pady=(0, 10))
            self._live_mode_var = tk.BooleanVar(value=False)
            tk.Checkbutton(live_row,
                           text='⚡ Live Mode — embed raw data for interactive pin inspect',
                           variable=self._live_mode_var,
                           bg=_BG2, fg=_FG, selectcolor=_BG3,
                           activebackground=_BG2, activeforeground=_FG,
                           font=_FONT_SM).pack(side='left')

            btn_frame = tk.Frame(self, bg=_BG)
            btn_frame.pack(fill='x', padx=18, pady=(4, 0))
            self._gen_btn = self._btn(btn_frame, '⚙  Generate Dashboard', self._on_generate, _ACCENT)
            self._gen_btn.pack(side='left', padx=(0, 10))
            self._open_btn = self._btn(btn_frame, '▶  Open Dashboard', self._on_open, _GREEN)
            self._open_btn.pack(side='left')
            self._cancel_btn = self._btn(btn_frame, '✕  Cancel', self._on_cancel, _RED)
            self._cancel_btn.pack(side='left', padx=(10, 0))
            self._cancel_btn.config(state='disabled')

            pb_frame = tk.Frame(self, bg=_BG)
            pb_frame.pack(fill='x', padx=18, pady=(10, 0))
            self._pb = ttk.Progressbar(pb_frame, mode='indeterminate', length=580)
            self._pb.pack(fill='x')

            tk.Label(self, text='Log', font=_FONT_SM, bg=_BG, fg=_FG_DIM, anchor='w').pack(fill='x', padx=18, pady=(10, 2))
            log_frame = tk.Frame(self, bg=_BG3, bd=1, relief='flat',
                                 highlightbackground=_BORDER, highlightthickness=1)
            log_frame.pack(fill='both', expand=True, padx=18, pady=(0, 16))
            self._log = tk.Text(log_frame, bg=_BG3, fg=_FG, font=('Consolas', 9),
                                relief='flat', bd=0, state='disabled', wrap='word', insertbackground=_FG)
            vsb = ttk.Scrollbar(log_frame, orient='vertical', command=self._log.yview)
            self._log.configure(yscrollcommand=vsb.set)
            vsb.pack(side='right', fill='y')
            self._log.pack(side='left', fill='both', expand=True, padx=6, pady=6)
            self._log.tag_config('ok',   foreground=_GREEN)
            self._log.tag_config('err',  foreground=_RED)
            self._log.tag_config('warn', foreground=_GOLD)
            self._log.tag_config('dim',  foreground=_FG_DIM)
            self._log.tag_config('acc',  foreground=_ACCENT)

        def _card(self, parent, title):
            outer = tk.Frame(parent, bg=_BG2, bd=1, relief='flat',
                             highlightbackground=_BORDER, highlightthickness=1)
            tk.Label(outer, text=title, font=('Segoe UI', 9, 'bold'),
                     bg=_BG2, fg=_ACCENT).pack(anchor='w', padx=14, pady=(10, 4))
            return outer

        def _btn(self, parent, text, cmd, color):
            return tk.Button(parent, text=text, command=cmd,
                             bg=_BG3, fg=color, activebackground=_BG2, activeforeground=color,
                             relief='flat', bd=0, font=_FONT_SM, padx=14, pady=7,
                             cursor='hand2', highlightbackground=_BORDER, highlightthickness=1)

        def _make_file_row(self, parent, label, var, browse_cmd, tip=''):
            row = tk.Frame(parent, bg=_BG2)
            row.pack(fill='x', padx=14, pady=(0, 10))
            tk.Label(row, text=label, font=_FONT_SM, bg=_BG2, fg=_FG,
                     width=14, anchor='w').pack(side='left')
            tk.Entry(row, textvariable=var, bg=_BG3, fg=_FG, insertbackground=_FG,
                     relief='flat', bd=0, font=_FONT_SM,
                     highlightbackground=_BORDER, highlightthickness=1
                     ).pack(side='left', fill='x', expand=True, ipady=5, padx=(0, 8))
            tk.Button(row, text='Browse…', command=browse_cmd,
                      bg=_BG3, fg=_ACCENT, activebackground=_BG2, activeforeground=_ACCENT,
                      relief='flat', bd=0, font=_FONT_SM, padx=10, pady=4,
                      cursor='hand2', highlightbackground=_BORDER, highlightthickness=1
                      ).pack(side='left')
            if tip:
                tk.Label(parent, text=tip, font=('Segoe UI', 8), bg=_BG2,
                         fg=_FG_DIM, anchor='w').pack(fill='x', padx=14, pady=(0, 6))

        def _browse_csv(self):
            p = filedialog.askopenfilename(
                title='Select yield CSV, ZIP, GZ, or 7Z',
                filetypes=[('All supported', '*.csv *.zip *.gz *.7z'),
                           ('CSV', '*.csv'), ('ZIP', '*.zip'),
                           ('GZ', '*.gz'), ('7Z', '*.7z'), ('All', '*.*')])
            if p: self._csv_var.set(p)

        def _browse_prog_root(self):
            _cur = self._prog_root_var.get().strip()
            _init = _cur
            while _init and not os.path.isdir(_init):
                _init = os.path.dirname(_init)
            p = filedialog.askdirectory(
                title='Select program root folder',
                initialdir=_init or os.path.expanduser('~'))
            if p: self._prog_root_var.set(p)

        def _browse_out(self):
            p = filedialog.askdirectory(title='Select output folder')
            if p: self._out_var.set(p)

        def _on_load_setup(self):
            _cur = self._setup_var.get().strip()
            path = filedialog.askopenfilename(
                title='Load setup JSON',
                initialdir=os.path.dirname(_cur) if _cur else os.path.expanduser('~'),
                initialfile=os.path.basename(_cur) if _cur else 'setup.json',
                filetypes=[('JSON', '*.json'), ('All', '*.*')])
            if not path: return
            try:
                with open(path, encoding='utf-8') as _f: data = json.load(_f)
            except Exception as e:
                messagebox.showerror('Load failed', f'Could not read setup file:\n{e}')
                return
            self._setup_var.set(path)
            if data.get('csv'):       self._csv_var.set(data['csv'])
            if data.get('prog'):      self._prog_var.set(data['prog'])
            if data.get('prog_root'): self._prog_root_var.set(data['prog_root'])
            if data.get('out'):       self._out_var.set(data['out'])
            if 'live_mode' in data:   self._live_mode_var.set(bool(data['live_mode']))
            self._log_line(f'[setup] Loaded: {path}', 'acc')

        def _on_save_setup(self):
            _cur = self._setup_var.get().strip()
            path = filedialog.asksaveasfilename(
                title='Save setup JSON',
                initialdir=os.path.dirname(_cur) if _cur else os.path.expanduser('~'),
                initialfile=os.path.basename(_cur) if _cur else 'setup.json',
                defaultextension='.json',
                filetypes=[('JSON', '*.json'), ('All', '*.*')])
            if not path: return
            try:
                data = {}
                if os.path.isfile(path):
                    with open(path, encoding='utf-8') as _f: data = json.load(_f)
            except Exception: data = {}
            data.update({'csv': self._csv_var.get().strip(), 'prog': self._prog_var.get().strip(),
                         'prog_root': self._prog_root_var.get().strip(),
                         'out': self._out_var.get().strip(), 'live_mode': self._live_mode_var.get()})
            try:
                with open(path, 'w', encoding='utf-8') as _f: json.dump(data, _f, indent=2)
                self._setup_var.set(path)
                self._log_line(f'[setup] Saved: {path}', 'ok')
            except Exception as e:
                messagebox.showerror('Save failed', f'Could not write setup file:\n{e}')

        def _log_write(self, text, tag=''):
            def _do():
                self._log.config(state='normal')
                self._log.insert('end', text, tag)
                self._log.see('end')
                self._log.config(state='disabled')
            self.after(0, _do)

        def _log_line(self, text, tag=''):
            self._log_write(text + '\n', tag)

        def _refresh_open_btn(self):
            html = self._last_html.get()
            self._open_btn.config(state='normal' if (html and os.path.isfile(html)) else 'disabled')

        def _on_generate(self):
            csv_path = self._csv_var.get().strip()
            out_dir  = self._out_var.get().strip()
            if not csv_path:
                messagebox.showerror('Missing input', 'Please select a CSV or archive file.')
                return
            if not os.path.isfile(csv_path):
                messagebox.showerror('File not found', f'Cannot find:\n{csv_path}')
                return
            if not out_dir:
                messagebox.showerror('Missing output', 'Please select an output folder.')
                return
            if out_dir.lower().endswith('.html') or os.path.isfile(out_dir):
                out_dir = os.path.dirname(out_dir)
            os.makedirs(out_dir, exist_ok=True)
            out_html = os.path.join(out_dir, 'vcccont-bin8-analysis.html')

            self._log.config(state='normal'); self._log.delete('1.0', 'end'); self._log.config(state='disabled')
            self._gen_btn.config(state='disabled')
            self._cancel_btn.config(state='normal')
            self._pb.start(12)
            self._tmp_dir = None

            def _worker():
                try:
                    resolved_csv, self._tmp_dir = _gui_resolve_csv(csv_path, lambda m: self._log_line(m, 'dim'))
                    self._log_line(f'Input:  {resolved_csv}', 'dim')
                    self._log_line(f'Output: {out_html}', 'dim')
                    self._log_line('─' * 60, 'dim')

                    prog_dir      = self._prog_var.get().strip()
                    prog_root_dir = self._prog_root_var.get().strip()
                    cmd = [sys.executable, os.path.abspath(__file__),
                           '--csv', resolved_csv, '--out', out_html, '--no-gui']
                    if prog_root_dir: cmd += ['--prog-root', prog_root_dir]
                    elif prog_dir:    cmd += ['--prog', prog_dir]
                    if self._live_mode_var.get(): cmd += ['--live-mode']

                    self._proc = __import__('subprocess').Popen(
                        cmd, stdout=__import__('subprocess').PIPE,
                        stderr=__import__('subprocess').STDOUT,
                        text=True, cwd=os.path.dirname(os.path.abspath(__file__)))

                    for line in self._proc.stdout:
                        line = line.rstrip(); ll = line.lower()
                        tag = ('err'  if ('error' in ll or 'traceback' in ll or 'exception' in ll) else
                               'warn' if ('warning' in ll or 'warn' in ll) else
                               'ok'   if 'dashboard written' in ll else
                               'dim'  if (line.startswith('  [') or line.startswith('  Done')) else '')
                        self._log_line(line, tag)

                    rc = self._proc.wait()

                    def _done():
                        self._pb.stop()
                        self._gen_btn.config(state='normal')
                        self._cancel_btn.config(state='disabled')
                        if rc == 0:
                            _index = os.path.join(out_dir, 'index.html')
                            _open_target = _index if os.path.isfile(_index) else out_html
                            self._last_html.set(_open_target)
                            self._log_line(''); self._log_line(f'✔  Dashboard written: {out_html}', 'ok')
                            self._refresh_open_btn()
                        else:
                            self._log_line(f'✘  Process exited with code {rc}', 'err')
                        if self._tmp_dir and os.path.isdir(self._tmp_dir):
                            shutil.rmtree(self._tmp_dir, ignore_errors=True)
                    self.after(0, _done)
                except Exception as exc:
                    def _err():
                        self._pb.stop(); self._gen_btn.config(state='normal')
                        self._cancel_btn.config(state='disabled')
                        self._log_line(f'✘  {exc}', 'err')
                    self.after(0, _err)

            threading.Thread(target=_worker, daemon=True).start()

        def _on_cancel(self):
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                self._log_line('⚠  Cancelled by user.', 'warn')
            self._pb.stop()
            self._gen_btn.config(state='normal')
            self._cancel_btn.config(state='disabled')

        def _on_open(self):
            html = self._last_html.get()
            if not html or not os.path.isfile(html):
                messagebox.showinfo('Not found', 'Generate the dashboard first.')
                return
            os.startfile(html)

    def _launch_gui():
        app = _App()
        s = ttk.Style()
        s.theme_use('clam')
        s.configure('TScrollbar', background=_BG3, troughcolor=_BG2, bordercolor=_BORDER, arrowcolor=_FG_DIM)
        s.configure('TProgressbar', troughcolor=_BG2, background=_ACCENT, bordercolor=_BORDER,
                    lightcolor=_ACCENT, darkcolor=_ACCENT)
        app.update_idletasks()
        w, h = 680, 560
        sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
        app.geometry(f'{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}')
        app.mainloop()


if __name__ == "__main__":
    # Launch GUI when run with no arguments (or only --setup); otherwise run headless
    _cli_args = [a for a in sys.argv[1:] if not a.startswith('--setup')]
    if not _cli_args and _TK_AVAILABLE:
        _launch_gui()
    else:
        main()
