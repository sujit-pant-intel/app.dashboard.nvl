"""
trend_chart.py  (Plotly edition)
---------------------------------
Interactive trend chart: clustered stacked Interface-Bin fail% bars by time
interval (daily / weekly / bi-weekly / monthly), with FF and FF+DF yield
lines on a dual Y-axis.  Includes an overall Interface Bin pareto as a 2nd
chart.  Fully interactive (hover tooltips, zoom, pan) via Plotly.

Product config JSON (optional):
  Provides ibin group descriptions (from yield_targets[].fail_bucket) and
  yield targets (yield_targets[].yield) that are drawn as dashed reference
  lines and shown in hover info.

CSV columns (case-insensitive, flexible names):
    Date*         - run/wafer date
    Lot           - lot identifier
    Wafer         - wafer number
    Program Name  - full test program name  e.g. NCXSDJXL0H61C002620
    Interface Bin - integer bin number
    Count         - die count for that bin
    Total Dies    - total dies on wafer

Yield % = Count / Total Dies * 100
FF  yield  = bins {1,2}     / Total Dies * 100
FF+DF yield= bins {1,2,3,4} / Total Dies * 100
Fail bins  = bins NOT in {1,2,3,4}

Usage:
    python trend_chart.py data.csv
    python trend_chart.py data.csv --cfg "Product Config.json"
    python trend_chart.py data.csv --interval monthly --topn 10
"""

from __future__ import annotations

import sys
import os
import re
import json
import argparse
from pathlib import Path
from datetime import datetime
from collections import OrderedDict
from typing import Any

# Plotly
try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    HAVE_PLOTLY = True
except ImportError:
    HAVE_PLOTLY = False

INTERVALS = ['revision', 'weekly', 'bi-weekly', 'monthly']

_PASS_BINS  = {1, 2, 3, 4}
_FF_BINS    = {1, 2}
_FF_DF_BINS = {1, 2, 3, 4}

_FAIL_PALETTE = [
    '#E53935', '#1E88E5', '#43A047', '#FB8C00', '#8E24AA',
    '#00ACC1', '#F4511E', '#3949AB', '#00897B', '#FFB300',
    '#D81B60', '#039BE5', '#7CB342', '#6D4C41', '#546E7A',
    '#C62828', '#283593', '#2E7D32', '#E65100', '#4A148C',
]


# ============================================================================
# 1. Product config helpers
# ============================================================================

def load_product_config(cfg_path: str | Path) -> dict[str, Any]:
    """Load product config JSON, return dict with ibin_name, ibin_target, yield_target."""
    cfg_path = Path(cfg_path)
    raw = json.loads(cfg_path.read_text(encoding='utf-8'))

    ibin_name: dict[int, str]    = {}
    ibin_target: dict[int, float] = {}
    yield_target: dict[str, float] = {}

    for entry in raw.get('yield_targets', []):
        bin_str  = str(entry.get('bin', ''))
        label    = entry.get('fail_bucket', '') or ''
        yld_pct  = entry.get('yield')

        ibins = []
        for part in re.split(r'[/,\s]+', bin_str):
            part = part.strip()
            if part.isdigit():
                ibins.append(int(part))

        for ib in ibins:
            if label:
                ibin_name[ib] = label
            if yld_pct is not None:
                ibin_target[ib] = float(yld_pct)

        if bin_str in ('1/2', '1/2/3/4'):
            key = 'ff' if bin_str == '1/2' else 'ff_df'
            if yld_pct is not None:
                yield_target[key] = float(yld_pct)

    # ── Enrich ibin_name from bin_map (desc + cat), overrides yield_targets ─
    for bin_str, info in raw.get('bin_map', {}).items():
        try:
            ib = int(bin_str)
        except (ValueError, TypeError):
            continue
        desc = (info.get('desc') or '').strip()
        cat  = (info.get('cat')  or '').strip()
        if cat or desc:
            ibin_name[ib] = f'{cat} \u2014 {desc}' if (cat and desc and cat != desc) else (cat or desc)

    # Extract series names from fail_bucket labels
    ff_name   = 'SDS FF'
    ff_df_name = 'SDS FF+DF'
    for entry in raw.get('yield_targets', []):
        bin_str = str(entry.get('bin', ''))
        lbl = (entry.get('fail_bucket') or '').strip()
        if not lbl:
            continue
        if bin_str == '1/2':
            ff_name = lbl
        elif bin_str == '1/2/3/4':
            ff_df_name = lbl

    return {
        'ibin_name':    ibin_name,
        'ibin_target':  ibin_target,
        'yield_target': yield_target,
        'name':         raw.get('name', ''),
        'ff_name':      ff_name,
        'ff_df_name':   ff_df_name,
        'raw':          raw,
    }


def _find_auto_config(devrevstep: str = '') -> Path | None:
    """Search shared/setup/yield-dashboard/ for a matching .json.
    If devrevstep is given (e.g. '8PF5CV'), prefer a file whose name starts with it.
    Falls back to 'default' file, then first .json found.
    """
    here = Path(__file__).resolve().parent
    d = here.parents[4] / 'shared' / 'setup' / 'config' / 'yield-dashboard'
    if not d.exists():
        return None
    jsons = sorted(d.glob('*.json'))
    if not jsons:
        return None
    if devrevstep:
        key = devrevstep.upper()
        # Exact prefix match: filename starts with devrevstep
        for p in jsons:
            if p.name.upper().startswith(key):
                return p
    # Fallback: prefer 'default' file
    for p in jsons:
        if p.stem.lower().startswith('default'):
            return p
    return jsons[0]


# ============================================================================
# 2. Date / interval helpers
# ============================================================================

_TS_FMTS = (
    '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
    '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
    '%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M', '%m/%d/%Y',
    '%Y%m%d',
)


def _parse_date(s: str) -> datetime | None:
    s = (s or '').strip()
    # Strip timezone offset (+HH:MM or -HH:MM) so strptime formats work
    s = re.sub(r'[+-]\d{2}:\d{2}$', '', s).strip()
    for fmt in _TS_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    m = re.search(r'(\d{8})', s)
    if m:
        try:
            return datetime.strptime(m.group(1), '%Y%m%d')
        except ValueError:
            pass
    return None


def _interval_key(dt: datetime, interval: str, program: str = '') -> str:
    if interval == 'revision':
        return _prog_label(program) if program else dt.strftime('%Y-%m-%d')
    if interval == 'daily':
        return dt.strftime('%Y-%m-%d')
    if interval == 'weekly':
        iso_y, iso_w, _ = dt.isocalendar()
        return f'{iso_y}-W{iso_w:02d}'
    if interval == 'bi-weekly':
        iso_y, iso_w, _ = dt.isocalendar()
        bw = ((iso_w - 1) // 2) * 2 + 1
        return f'{iso_y}-W{bw:02d}/{bw+1:02d}'
    if interval == 'monthly':
        return dt.strftime('%Y-%m')
    return dt.strftime('%Y-%m-%d')


def _interval_sort_key(label: str) -> tuple:
    # Revision label e.g. '61A', '61B', '102C'
    m = re.match(r'^(\d+)([A-Z])$', label)
    if m:
        return (0, int(m.group(1)), ord(m.group(2)), 0)
    m = re.match(r'^(\d{4})-W(\d+)', label)
    if m:
        return (1, int(m.group(1)), int(m.group(2)), 0)
    m = re.match(r'^(\d{4})-(\d{2})$', label)
    if m:
        return (1, int(m.group(1)), int(m.group(2)), 0)
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', label)
    if m:
        return (1, int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (9999, 0, 0, 0)


# ============================================================================
# 3. Program label
# ============================================================================

def _prog_label(program_name: str, lot: str = '', wafer: str = '') -> str:
    """Extract a short run label, e.g. '61C' from 'NCXSDJXL0H61C002620'."""
    p = str(program_name).strip()
    m = re.search(r'[A-Z](\d{2}[A-Z])', p)
    if m:
        return m.group(1)
    m = re.search(r'(\d{2,3}[A-Z])', p)
    if m:
        return m.group(1)
    return p[-6:] if len(p) > 6 else (p or (lot[-4:] if lot else '?'))


# ============================================================================
# 4. CSV loading
# ============================================================================

_COL_ALIASES = {
    'date':    ['date', 'run date', 'run_date', 'rundate', 'timestamp', 'ts',
                'lots end date time', 'end date time', 'end_date_time'],
    'lot':     ['lot', 'lot_id', 'lotid', 'lot id'],
    'wafer':   ['wafer', 'wafer_num', 'wafer num', 'waferno', 'wafer no', 'wafer_id',
                'sort_wafer', 'sort wafer', 'sort partial wafer id'],
    'program':    ['program name', 'program_name', 'programname', 'program', 'tp', 'test program'],
    'devrevstep': ['devrevstep'],
    'sort_lot': ['sort_lot', 'sortlot', 'sort lot'],
    'ibin':    ['interface bin', 'interface_bin', 'ibin', 'bin', 'bin_num', 'bin num'],
    'count':   ['count', 'die count', 'die_count', 'fail count', 'fail_count'],
    'total':   ['total dies', 'total_dies', 'totaldies', 'total', 'dies',
                'interface_total_bin', 'interface total bin'],
    'material': ['material'],
    'fbin':   ['functional bin', 'functional_bin', 'fbin', 'fb'],
    'bin_desc': ['bin description', 'bindescription', 'bin_description'],
}


def _resolve_cols(header: list[str]) -> dict[str, int]:
    import re as _re
    # Normalize: strip trailing _DIGITS suffix (TRACE raw exports append job number)
    def _norm(h: str) -> str:
        return _re.sub(r'_\d+$', '', h.strip()).strip().lower()
    lower_hdr = [_norm(h) for h in header]
    out = {}
    for canon, aliases in _COL_ALIASES.items():
        for alias in aliases:
            if alias in lower_hdr:
                out[canon] = lower_hdr.index(alias)
                break
    # Detect UPM_0107*FULLDIE_0950* column (may have job-number suffix already stripped)
    import fnmatch as _fnmatch
    _upm950_pat = 'upm_0107*fulldie*0950*'
    for i, h in enumerate(header):
        if _fnmatch.fnmatch(_re.sub(r'_\d+$', '', h.strip()).strip().lower(), _upm950_pat):
            out['upm_950'] = i
            break
    return out


def _open_csv_source(path: Path):
    """
    Open a CSV, ZIP, or GZ file and return a text-mode file-like object.
    ZIP: uses the first .csv member found.
    GZ: assumes the compressed content is a CSV.
    """
    import io
    suffix = path.suffix.lower()
    if suffix == '.zip':
        import zipfile
        zf = zipfile.ZipFile(path, 'r')
        members = [m for m in zf.namelist() if m.lower().endswith('.csv')]
        if not members:
            members = zf.namelist()  # fallback: first member regardless
        if not members:
            raise ValueError(f'No CSV found inside ZIP: {path.name}')
        raw_bytes = zf.read(members[0])
        zf.close()
        return io.StringIO(raw_bytes.decode('utf-8-sig', errors='replace'))
    elif suffix in ('.gz', '.gzip'):
        import gzip
        with gzip.open(path, 'rb') as gz:
            raw_bytes = gz.read()
        return io.StringIO(raw_bytes.decode('utf-8-sig', errors='replace'))
    else:
        return open(path, newline='', encoding='utf-8-sig')


def load_material_data(product_prefix: str) -> dict[str, str]:
    """
    Load material type data from collateral lot-definition CSV.
    Returns dict: first_7_chars_of_intel_lot7 -> material_type
    E.g., {'Q603S6T': 'NVL816-BLLC-L0 AIO', ...}
    """
    import csv as _csv
    # trend_chart.py is at: .../app.yield.nvl/code/dashboard/yield-dashboard/yld/src/
    # Need to get to: .../app.yield.nvl/shared/material
    script_dir = Path(__file__).resolve().parent
    # Go up to find shared/material directory
    current = script_dir
    for i in range(10):  # Search up to 10 levels
        collateral_dir = current / 'shared' / 'material'
        if collateral_dir.exists():
            break
        current = current.parent
    
    if not collateral_dir.exists():
        return {}
    
    # Find lot-definition CSV starting with product_prefix
    lot_def_files = list(collateral_dir.glob(f'{product_prefix}*lot*definition*.csv'))
    if not lot_def_files:
        return {}
    
    material_map = {}
    for lot_file in lot_def_files:
        try:
            with open(lot_file, newline='', encoding='utf-8-sig') as fh:
                rdr = _csv.DictReader(fh)
                if not rdr.fieldnames:
                    continue
                # Find columns for INTEL_LOT7 and material type
                intel_lot_col = next((c for c in rdr.fieldnames if 'intel_lot' in c.lower()), None)
                wafer_col = next((c for c in rdr.fieldnames if c.strip().lower() == 'waferid'), None)
                mat_col = next((c for c in rdr.fieldnames if 'material' in c.lower()), None)
                
                if not intel_lot_col or not mat_col:
                    continue
                
                for row in rdr:
                    intel_lot_val = row.get(intel_lot_col, '').strip()
                    # Often the lot string has dots like 'Q604SB1.01'
                    clean_lot = intel_lot_val.replace('.', '')
                    mat_val = row.get(mat_col, '').strip()
                    if clean_lot and mat_val:
                        # Index by full lot, first 7 characters, and WaferID
                        lot7 = clean_lot[:7]
                        if wafer_col:
                            raw_w = row.get(wafer_col, '').strip()
                            try:
                                w_key = str(int(float(raw_w)))
                            except (ValueError, TypeError):
                                w_key = raw_w
                            material_map[(clean_lot, w_key)] = mat_val
                            material_map[(lot7, w_key)] = mat_val
                        material_map.setdefault(clean_lot, mat_val)
                        material_map.setdefault(lot7, mat_val)
        except Exception:
            pass
    
    return material_map


def load_csv(path: Path, log=None, grouping_mode: str = 'wafer') -> list[dict]:
    """Parse CSV / ZIP / GZ; return list of per-run dicts.
    
    grouping_mode: 'wafer' (default) = one bar per wafer
                   'lot' = one bar per lot (combines all wafers)
    """
    import csv as _csv
    path = Path(path)
    raw_rows = []
    fh = _open_csv_source(path)
    try:
        rdr = _csv.reader(fh)
        header = next(rdr, [])
        for row in rdr:
            if any(cell.strip() for cell in row):
                raw_rows.append(row)
    finally:
        fh.close()

    col = _resolve_cols(header)
    if 'ibin' not in col:
        raise ValueError(f'Cannot find Interface Bin column.\nHeader: {header}')
    if 'total' not in col and 'count' not in col:
        raise ValueError(f'Need at least a Count or Total Dies column.\nHeader: {header}')
    _per_unit_mode = 'count' not in col  # each row = 1 die

    def _get(row, key, default=''):
        idx = col.get(key)
        if idx is None or idx >= len(row):
            return default
        return row[idx].strip()

    _upm950_divisor = 9154.0  # MHz → % divisor for UPM_0107*0950*
    
    # Load material data from collateral (one-time, before processing rows)
    product_prefix = ''
    material_map = {}
    for row in raw_rows[:10]:  # Scan first 10 rows to find product prefix
        devrevstep_sample = _get(row, 'devrevstep', '')
        if devrevstep_sample:
            product_prefix = devrevstep_sample[:6]  # e.g., '8PF5CV' from devrevstep
            break
    if product_prefix:
        material_map = load_material_data(product_prefix)
    
    groups: dict[tuple, dict] = OrderedDict()
    for row in raw_rows:
        lot        = _get(row, 'lot', 'LOT?')
        wafer      = _get(row, 'wafer', '')
        program    = _get(row, 'program', '')
        devrevstep = _get(row, 'devrevstep', '')[:6]  # truncate to 6 chars for grouping
        sort_lot   = _get(row, 'sort_lot', '')
        
        # Material: first check CSV row, then fall back to collateral lookup
        _csv_material = _get(row, 'material', '')
        if not _csv_material:
            lookup_lot = sort_lot if sort_lot else lot
            lot7 = lookup_lot[:7]
            _w_key = ''
            if wafer:
                try:
                    _w_key = str(int(wafer[-2:])) if len(wafer) >= 2 else str(int(float(wafer)))
                except (ValueError, TypeError):
                    _w_key = wafer
            material = (material_map.get((lookup_lot, _w_key))
                        or material_map.get(lookup_lot)
                        or material_map.get((lot7, _w_key))
                        or material_map.get(lot7)
                        or '')
        else:
            material = _csv_material

        date_s  = _get(row, 'date', '')
        ibin_s  = _get(row, 'ibin', '')
        cnt_s   = _get(row, 'count', '1') if not _per_unit_mode else '1'
        tot_s   = '0' if _per_unit_mode else _get(row, 'total', '0')
        upm950_s = _get(row, 'upm_950', '')

        try:
            ibin = int(float(ibin_s))
        except (ValueError, TypeError):
            continue
        try:
            cnt = int(float(cnt_s))
        except (ValueError, TypeError):
            cnt = 0
        try:
            tot = int(float(tot_s))
        except (ValueError, TypeError):
            tot = 0

        # Group by wafer (default) or lot only
        if grouping_mode == 'lot':
            key = (lot[:7], program, material)
        else:
            key = (lot, wafer, program, material)
        
        if key not in groups:
            dt = _parse_date(date_s)
            if dt is None:
                dt = _parse_date(lot) or _parse_date(program)
            groups[key] = {
                'lot': lot[:7] if grouping_mode == 'lot' else lot,
                'wafer': wafer, 'program': program,
                'devrevstep': devrevstep,
                'sort_lot': sort_lot[:7] if grouping_mode == 'lot' else sort_lot,
                'material': material,
                'label': _prog_label(program, lot, wafer if grouping_mode == 'wafer' else ''),
                'date_str': date_s, 'date': dt,
                'total_dies': tot, 'bin_counts': {},
                'upm_950': [],  # per-die [ibin, upm_pct] pairs
            }
        grp = groups[key]
        if not grp.get('material') and material:
            grp['material'] = material
        if tot > grp['total_dies']:
            grp['total_dies'] = tot
        grp['bin_counts'][ibin] = grp['bin_counts'].get(ibin, 0) + cnt
        # Collect functional-bin breakdown per ibin
        fbin_s = _get(row, 'fbin', '')
        if fbin_s:
            try:
                fbin = int(float(fbin_s))
                fb_map = grp.setdefault('fb_counts', {})
                ib_fb  = fb_map.setdefault(ibin, {})
                ib_fb[fbin] = ib_fb.get(fbin, 0) + cnt
                # Collect bin_desc (bin setter string) for fail test module
                bdesc = _get(row, 'bin_desc', '')
                if bdesc:
                    mod_map = grp.setdefault('fb_modules', {})
                    ib_mod  = mod_map.setdefault(ibin, {})
                    ib_mod.setdefault(fbin, {})
                    ib_mod[fbin][bdesc] = ib_mod[fbin].get(bdesc, 0) + cnt
            except (ValueError, TypeError):
                pass
        # Store [ibin, upm_pct] per die so JS can classify HP/LP correctly
        if upm950_s:
            try:
                upm_pct = round(float(upm950_s) / _upm950_divisor * 100, 2)
                grp['upm_950'].append([ibin, upm_pct])
            except (ValueError, TypeError):
                pass

    result = []
    for grp in groups.values():
        total     = grp['total_dies'] or sum(grp['bin_counts'].values()) or 1
        ff_cnt    = sum(c for b, c in grp['bin_counts'].items() if b in _FF_BINS)
        ff_df_cnt = sum(c for b, c in grp['bin_counts'].items() if b in _FF_DF_BINS)
        fail_ibins = {b: c / total * 100
                      for b, c in grp['bin_counts'].items()
                      if b not in _PASS_BINS}
        r = {**grp, 'total_dies': total,
             'ff_yield':    ff_cnt    / total * 100,
             'ff_df_yield': ff_df_cnt / total * 100,
             'fail_ibins':  fail_ibins,
             'material':    grp.get('material', ''),
             'upm_950':     grp.get('upm_950', []),
             'fb_counts':   grp.get('fb_counts', {}),
             'fb_modules':  grp.get('fb_modules', {})}
        result.append(r)
        if log:
            n_f = sum(1 for v in fail_ibins.values() if v > 0)
            log(f'  [{grp["label"]}] lot={grp["lot"]} w={grp["wafer"]}  '
                f'FF={r["ff_yield"]:.1f}%  FF+DF={r["ff_df_yield"]:.1f}%  '
                f'fail_ibins={n_f}\n')
    return result


# ============================================================================
# 5. Grouping
# ============================================================================

def group_runs(runs: list[dict], interval: str) -> OrderedDict:
    """Group runs into {interval_label: [run, ...]} ordered chronologically.
    Within each group, runs are sorted by date (earliest to latest)."""
    grouped: dict[str, list] = {}
    for r in runs:
        if interval == 'revision':
            drs = r.get('devrevstep', '')
            key = drs if drs else _prog_label(r.get('program', ''), r.get('lot', ''), r.get('wafer', ''))
        elif r['date']:
            key = _interval_key(r['date'], interval, r.get('program', ''))
        else:
            key = r['lot']
        grouped.setdefault(key, []).append(r)

    # Sort runs within each group by (lot's last test date, run date) so all
    # runs for a lot are contiguous and the latest-tested lot appears last
    for group_runs_list in grouped.values():
        _no_date = datetime.min
        lot_last: dict[str, datetime] = {}
        for r in group_runs_list:
            lot = r['lot']
            dt = r['date'] or _no_date
            if dt > lot_last.get(lot, _no_date):
                lot_last[lot] = dt
        group_runs_list.sort(key=lambda r: (lot_last.get(r['lot'], _no_date),
                                            r['date'] or _no_date))

    def _sk(k):
        sk = _interval_sort_key(k)
        return (*sk, k) if sk != (9999, 0, 0) else (9999, 0, 0, k)

    return OrderedDict(sorted(grouped.items(), key=lambda kv: _sk(kv[0])))


# ============================================================================
# 6. Chart builders (Plotly)
# ============================================================================

def _ibin_display(ibin: int, cfg: dict | None) -> str:
    """Return 'IB N — Category' or just 'IB N'."""
    if cfg and cfg.get('ibin_name', {}).get(ibin):
        return f'IB {ibin} \u2014 {cfg["ibin_name"][ibin]}'
    return f'IB {ibin}'


def build_trend_chart(groups: OrderedDict,
                      top_n_fail_ibins: int = 8,
                      fail_thresh_pct: float = 0.0,
                      interval: str = 'weekly',
                      cfg: dict | None = None) -> 'go.Figure':
    """
    Plotly Figure: stacked clustered bars (fail% per iBin) + dual-Y yield lines.
    X-axis = run short labels; period separators shown as vertical dotted lines
    with interval labels as annotations above the chart.
    """
    # --- Top-N fail ibins by cumulative fail%
    global_fail: dict[int, float] = {}
    for runs in groups.values():
        for r in runs:
            for ib, pct in r['fail_ibins'].items():
                global_fail[ib] = global_fail.get(ib, 0) + pct

    top_ibins = sorted(
        [ib for ib, v in global_fail.items() if v >= fail_thresh_pct],
        key=lambda ib: global_fail[ib], reverse=True
    )[:top_n_fail_ibins]

    # --- Flatten runs in order
    all_runs_ordered: list[tuple[str, dict]] = []   # (iv_label, run)
    iv_start_indices: list[int]  = []               # x-index where interval starts
    iv_labels_ordered: list[str] = []

    for iv_label, iv_runs in groups.items():
        iv_start_indices.append(len(all_runs_ordered))
        iv_labels_ordered.append(iv_label)
        for r in iv_runs:
            all_runs_ordered.append((iv_label, r))

    n_runs = len(all_runs_ordered)
    if n_runs == 0:
        fig = go.Figure()
        fig.add_annotation(text='No runs to plot.', xref='paper', yref='paper',
                           x=0.5, y=0.5, showarrow=False, font_size=16)
        return fig

    x_pos      = list(range(n_runs))
    short_lbls = [(r.get('sort_lot') or r['label']) + 
                  (f' ({r.get("material", "")})' if r.get('material') else '')
                  for _, r in all_runs_ordered]

    # Build rich tooltip base per run
    def _run_tip(iv: str, r: dict) -> str:
        d = (r['date_str'] or
             (r['date'].strftime('%Y-%m-%d') if r['date'] else '\u2014'))
        return (f'<b>{r["label"]}</b> | Period: {iv}<br>'
                f'Lot: {r["lot"]}  Wafer: {r["wafer"]}<br>'
                f'Program: {r["program"]}<br>'
                f'Date: {d}  |  Dies: {r["total_dies"]:,}')

    run_tips = [_run_tip(iv, r) for iv, r in all_runs_ordered]

    ff_tgt     = cfg['yield_target'].get('ff')    if cfg else None
    ffdf_tgt   = cfg['yield_target'].get('ff_df') if cfg else None
    ff_name    = (cfg or {}).get('ff_name',   'SDS FF')
    ffdf_name  = (cfg or {}).get('ff_df_name', 'SDS FF+DF')
    chart_name = (cfg or {}).get('name', '')

    fig = make_subplots(specs=[[{'secondary_y': True}]])

    # --- Stacked bar traces (one per fail iBin)
    for bi, ibin in enumerate(top_ibins):
        ibin_lbl = _ibin_display(ibin, cfg)
        tgt      = cfg['ibin_target'].get(ibin) if cfg else None
        bar_y    = []
        hover    = []
        for idx, (iv, r) in enumerate(all_runs_ordered):
            pct = r['fail_ibins'].get(ibin, 0.0)
            bar_y.append(pct)
            htxt = (f'{run_tips[idx]}<br>\u2500\u2500\u2500\u2500\u2500<br>'
                    f'<b>{ibin_lbl}</b><br>Fail: <b>{pct:.2f}%</b>')
            if tgt is not None:
                htxt += f'<br>Target: {tgt:.1f}%'
            hover.append(htxt)

        fig.add_trace(go.Bar(
            x=x_pos, y=bar_y,
            name=ibin_lbl,
            hovertext=hover, hoverinfo='text',
            marker_color=_FAIL_PALETTE[bi % len(_FAIL_PALETTE)],
            marker_line_color='white', marker_line_width=0.4,
            opacity=0.85,
            legendgroup='fail_bins',
        ), secondary_y=False)

    # --- FF yield line
    ff_y  = [r['ff_yield']    for _, r in all_runs_ordered]
    ffdf_y= [r['ff_df_yield'] for _, r in all_runs_ordered]

    ff_hover = [
        f'{run_tips[i]}<br>\u2500\u2500\u2500\u2500\u2500<br>'
        f'<b>{ff_name}</b>: {ff_y[i]:.2f}%'
        + (f'<br>Target: {ff_tgt:.1f}%' if ff_tgt is not None else '')
        for i in range(n_runs)
    ]
    fig.add_trace(go.Scatter(
        x=x_pos, y=ff_y,
        mode='lines+markers+text',
        name=ff_name,
        line=dict(color='#1a73e8', width=2.5),
        marker=dict(size=8),
        text=[f'{v:.1f}%' for v in ff_y],
        textposition='top center', textfont=dict(size=9, color='#1a73e8'),
        hovertext=ff_hover, hoverinfo='text',
        legendgroup='yield_lines',
    ), secondary_y=True)

    ffdf_hover = [
        f'{run_tips[i]}<br>\u2500\u2500\u2500\u2500\u2500<br>'
        f'<b>{ffdf_name}</b>: {ffdf_y[i]:.2f}%'
        + (f'<br>Target: {ffdf_tgt:.1f}%' if ffdf_tgt is not None else '')
        for i in range(n_runs)
    ]
    fig.add_trace(go.Scatter(
        x=x_pos, y=ffdf_y,
        mode='lines+markers+text',
        name=ffdf_name,
        line=dict(color='#2e7d32', width=2.5, dash='dash'),
        marker=dict(size=8, symbol='square'),
        text=[f'{v:.1f}%' for v in ffdf_y],
        textposition='bottom center', textfont=dict(size=9, color='#2e7d32'),
        hovertext=ffdf_hover, hoverinfo='text',
        legendgroup='yield_lines',
    ), secondary_y=True)

    # --- Yield target reference lines
    if ff_tgt is not None:
        fig.add_hline(y=ff_tgt, line_dash='dot', line_color='#1a73e8',
                      line_width=1.5, opacity=0.5, secondary_y=True,
                      annotation_text=f'{ff_name} target {ff_tgt:.1f}%',
                      annotation_position='right',
                      annotation_font_size=10)
    if ffdf_tgt is not None:
        fig.add_hline(y=ffdf_tgt, line_dash='dot', line_color='#2e7d32',
                      line_width=1.5, opacity=0.5, secondary_y=True,
                      annotation_text=f'{ffdf_name} target {ffdf_tgt:.1f}%',
                      annotation_position='right',
                      annotation_font_size=10)

    # --- Period dividers + annotations
    shapes, annots = [], []
    for bi, (bnd, iv_name) in enumerate(zip(iv_start_indices, iv_labels_ordered)):
        end = iv_start_indices[bi + 1] if bi + 1 < len(iv_start_indices) else n_runs
        mid = (bnd + end - 1) / 2

        if bnd > 0:
            shapes.append(dict(
                type='line', x0=bnd - 0.5, x1=bnd - 0.5,
                y0=0, y1=1, yref='paper',
                line=dict(color='#95a5a6', width=1.2, dash='dot'),
            ))
        annots.append(dict(
            x=mid, y=1.06, xref='x', yref='paper',
            text=f'<b>{iv_name}</b>',
            showarrow=False,
            font=dict(size=11, color='#2c3e50'),
            xanchor='center',
        ))

    # --- Y-axis range
    max_stack = max(
        (sum(r['fail_ibins'].get(ib, 0) for ib in top_ibins) for _, r in all_runs_ordered),
        default=0.0,
    )
    fail_ylim = min(100.0, max(max_stack * 1.25, 5.0))

    fig.update_layout(
        barmode='stack',
        plot_bgcolor='#f9f9fb',
        paper_bgcolor='white',
        title=dict(
            text=((f'<b>{chart_name}</b> — ' if chart_name else '')
                  + f'Interface Bin Fail vs. Yield Trend \u2014 <b>{interval}</b> intervals<br>'
                  f'<sup>{n_runs} run{"s" if n_runs != 1 else ""}, '
                  f'{len(groups)} period{"s" if len(groups) != 1 else ""}</sup>'),
            font=dict(size=16),
        ),
        xaxis=dict(
            tickvals=x_pos,
            ticktext=short_lbls,
            tickfont=dict(size=10),
            tickangle=-35,
            showgrid=False,
            title='SORT LOT',
        ),
        yaxis=dict(
            title='Interface Bin Fail (%)',
            range=[0, fail_ylim],
            gridcolor='#e8e8e8',
            zeroline=True, zerolinecolor='#ccc',
        ),
        yaxis2=dict(
            title='Yield (%)',
            range=[0, 105],
            overlaying='y', side='right',
            showgrid=False,
        ),
        legend=dict(
            orientation='v',
            x=1.09, y=1.0,
            bgcolor='rgba(255,255,255,0.85)',
            bordercolor='#ddd', borderwidth=1,
            font=dict(size=11),
        ),
        shapes=shapes,
        annotations=annots,
        margin=dict(l=60, r=200, t=110, b=80),
        hovermode='closest',
        hoverlabel=dict(bgcolor='white', font_size=12, bordercolor='#ccc'),
        autosize=True,
    )
    return fig


def build_pareto_vertical_chart(runs: list[dict],
                                top_n: int = 20,
                                cfg: dict | None = None):
    """
    Fail Pareto Chart (Percentage) — vertical bars.
    X-axis: iBin number labels.
    Left Y-axis: % failure per bin (averaged across runs).
    Right Y-axis: cumulative % reaching 100%.
    Returns (fig, table_rows) where table_rows is a list of dicts.
    """
    # Auto-load product config for ibin descriptions if not supplied
    if cfg is None:
        drs = runs[0].get('devrevstep', '') if runs else ''
        auto = _find_auto_config(drs)
        if auto:
            try:
                cfg = load_product_config(auto)
            except Exception:
                cfg = None
    global_fail: dict[int, float] = {}
    run_count = len(runs)
    for r in runs:
        for ib, pct in r['fail_ibins'].items():
            global_fail[ib] = global_fail.get(ib, 0) + pct

    if not global_fail:
        fig = go.Figure()
        fig.add_annotation(text='No fail bin data.', xref='paper', yref='paper',
                           x=0.5, y=0.5, showarrow=False, font_size=16)
        return fig, []

    avg_fail     = {ib: v / run_count for ib, v in global_fail.items()}
    sorted_ibins = sorted(avg_fail, key=lambda ib: avg_fail[ib], reverse=True)[:top_n]
    total_avg    = sum(avg_fail[ib] for ib in sorted_ibins) or 1.0

    x_labels, bar_vals, hover_txt, colors = [], [], [], []
    table_rows = []
    bin_map = (cfg or {}).get('raw', {}).get('bin_map', {})
    for i, ib in enumerate(sorted_ibins):
        pct  = avg_fail[ib]
        lbl  = _ibin_display(ib, cfg)
        info = bin_map.get(str(ib), {})
        cat  = (info.get('cat') or '').strip()
        desc = (info.get('desc') or '').strip()
        n_fail = sum(r.get('bin_counts', {}).get(ib, 0) for r in runs)
        x_labels.append(lbl)
        bar_vals.append(pct)
        colors.append(_FAIL_PALETTE[i % len(_FAIL_PALETTE)])
        hover_txt.append(
            f'<b>{lbl}</b><br>Avg Fail: <b>{pct:.2f}%</b><br>'
            f'Across {run_count} run{"s" if run_count != 1 else ""}'
        )
        table_rows.append({'ib': ib, 'cat': cat, 'desc': desc,
                           'n_fail': n_fail, 'pct': pct})

    cum_vals: list[float] = []
    running = 0.0
    for v in bar_vals:
        running += v / total_avg * 100
        cum_vals.append(running)

    fig = make_subplots(specs=[[{'secondary_y': True}]])

    fig.add_trace(go.Bar(
        x=x_labels, y=bar_vals,
        name='Avg Fail (%)',
        marker_color=colors,
        marker_line_color='#1a252f', marker_line_width=0.8,
        opacity=0.9,
        hovertext=hover_txt, hoverinfo='text',
        text=[f'{v:.2f}%' for v in bar_vals],
        textposition='outside', textfont=dict(size=10, color='#333', family='Arial'),
    ), secondary_y=False)

    fig.add_trace(go.Scatter(
        x=x_labels, y=cum_vals,
        mode='lines+markers',
        name='Cumulative %',
        line=dict(color='#e67e22', width=2.5),
        marker=dict(size=7, color='#e67e22'),
        hovertemplate='<b>%{x}</b><br>Cumulative: %{y:.1f}%<extra></extra>',
    ), secondary_y=True)

    fig.add_hline(y=80, line_dash='dash', line_color='#e74c3c',
                  line_width=1.8, opacity=0.8,
                  annotation_text='80% cumulative', annotation_position='top right',
                  annotation_font_size=11, annotation_font_color='#e74c3c',
                  secondary_y=True)

    n = len(sorted_ibins)
    chart_name_pv = (cfg or {}).get('name', '')
    title_pv = ((f'<b>{chart_name_pv}</b> \u2014 ' if chart_name_pv else '')
                + f'<b>Fail Pareto Chart (Percentage)</b><br>'
                f'<sup>Top {n} fail bins, averaged across '
                f'{run_count} run{"s" if run_count != 1 else ""}</sup>')
    fig.update_layout(
        plot_bgcolor='#f9f9fb',
        paper_bgcolor='white',
        title=dict(
            text=title_pv,
            font=dict(size=16),
            x=0.5,
            xanchor='center',
        ),
        xaxis=dict(
            title='Interface Bin',
            gridcolor='#e8e8e8',
            tickangle=-35, tickfont=dict(size=10),
        ),
        yaxis=dict(
            title='Fail (%)',
            gridcolor='#e8e8e8', zeroline=True, zerolinecolor='#ccc',
            range=[0, max(bar_vals) * 1.15] if bar_vals else None,
            ticksuffix='%',
        ),
        yaxis2=dict(
            title='Cumulative (%)',
            range=[0, 105],
            showgrid=False,
            ticksuffix='%',
        ),
        legend=dict(x=1.08, y=1.0, bgcolor='rgba(255,255,255,0.85)',
                    bordercolor='#ddd', borderwidth=1),
        margin=dict(l=70, r=120, t=90, b=120),
        hovermode='closest',
        hoverlabel=dict(bgcolor='white', font_size=12, bordercolor='#ccc'),
        autosize=True,
        bargap=0.25,
    )
    return fig, table_rows


def build_pareto_chart(runs: list[dict],
                       top_n: int = 20,
                       cfg: dict | None = None) -> 'go.Figure':
    """
    Overall Interface Bin Pareto -- horizontal bar chart + cumulative % line.
    Sorted by average fail% across all runs.
    """
    global_fail: dict[int, float] = {}
    run_count = len(runs)
    for r in runs:
        for ib, pct in r['fail_ibins'].items():
            global_fail[ib] = global_fail.get(ib, 0) + pct

    if not global_fail:
        fig = go.Figure()
        fig.add_annotation(text='No fail bin data.', xref='paper', yref='paper',
                           x=0.5, y=0.5, showarrow=False, font_size=16)
        return fig

    avg_fail     = {ib: v / run_count for ib, v in global_fail.items()}
    sorted_ibins = sorted(avg_fail, key=lambda ib: avg_fail[ib], reverse=True)[:top_n]
    total_avg    = sum(avg_fail[ib] for ib in sorted_ibins) or 1.0

    y_labels, bar_vals, hover_txt, colors = [], [], [], []
    for i, ib in enumerate(sorted_ibins):
        pct  = avg_fail[ib]
        lbl  = _ibin_display(ib, cfg)
        tgt  = cfg['ibin_target'].get(ib) if cfg else None
        y_labels.append(lbl)
        bar_vals.append(pct)
        colors.append(_FAIL_PALETTE[i % len(_FAIL_PALETTE)])
        htxt = (f'<b>{lbl}</b><br>Avg Fail: <b>{pct:.2f}%</b><br>'
                f'Total fail sum: {global_fail[ib]:.2f}%<br>'
                f'Across {run_count} run{"s" if run_count != 1 else ""}')
        if tgt is not None:
            htxt += f'<br>Target: {tgt:.1f}%'
        hover_txt.append(htxt)

    cum_vals: list[float] = []
    running = 0.0
    for v in bar_vals:
        running += v / total_avg * 100
        cum_vals.append(running)

    fig = make_subplots(specs=[[{'secondary_y': True}]])

    fig.add_trace(go.Bar(
        y=y_labels, x=bar_vals,
        orientation='h',
        name='Avg Fail (%)',
        marker_color=colors,
        marker_line_color='white', marker_line_width=0.5,
        opacity=0.9,
        hovertext=hover_txt, hoverinfo='text',
        text=[f'{v:.2f}%' for v in bar_vals],
        textposition='outside', textfont=dict(size=10, color='#333', family='Arial'),
    ), secondary_y=False)

    fig.add_trace(go.Scatter(
        y=y_labels, x=cum_vals,
        mode='lines+markers',
        name='Cumulative %',
        line=dict(color='#e67e22', width=2.5),
        marker=dict(size=7, color='#e67e22'),
        hovertemplate='<b>%{y}</b><br>Cumulative: %{x:.1f}%<extra></extra>',
    ), secondary_y=True)

    fig.add_vline(x=80, line_dash='dash', line_color='#e74c3c',
                  line_width=1.8, opacity=0.8,
                  annotation_text='80%', annotation_position='top right',
                  annotation_font_size=11, annotation_font_color='#e74c3c')

    n = len(sorted_ibins)
    chart_name_p = (cfg or {}).get('name', '')
    title_p = ((f'<b>{chart_name_p}</b> \u2014 ' if chart_name_p else '')
               + f'Overall Interface Bin Fail Pareto<br>'
               f'<sup>Top {n} fail bins, averaged across '
               f'{run_count} run{"s" if run_count != 1 else ""}</sup>')
    fig.update_layout(
        plot_bgcolor='#f9f9fb',
        paper_bgcolor='white',
        title=dict(
            text=title_p,
            font=dict(size=16),
        ),
        xaxis=dict(
            title='Average Fail (%)',
            gridcolor='#e8e8e8',
            range=[0, max(bar_vals) * 1.25 if bar_vals else 10]),
        xaxis2=dict(title='Cumulative (%)', range=[0, 110],
                    overlaying='x', side='top', showgrid=False),
        yaxis=dict(autorange='reversed', tickfont=dict(size=10),
                   showgrid=False),
        yaxis2=dict(range=[0, 110], overlaying='y', side='right',
                    showgrid=False, visible=False),
        legend=dict(x=1.08, y=1.0, bgcolor='rgba(255,255,255,0.85)',
                    bordercolor='#ddd', borderwidth=1),
        margin=dict(l=220, r=120, t=90, b=60),
        hovermode='closest',
        hoverlabel=dict(bgcolor='white', font_size=12, bordercolor='#ccc'),
        autosize=True,
    )
    return fig


# ============================================================================
# 7. HTML generation
# ============================================================================

def generate_html(csv_path: Path, groups: OrderedDict, runs: list[dict],
                  trend_fig: 'go.Figure', pareto_fig: 'go.Figure',
                  output_path: Path,
                  interval: str = 'revision',
                  top_n: int = 8,
                  cfg_path: str = '',
                  cfg: dict | None = None,
                  pareto_vertical_fig: 'go.Figure | None' = None,
                  pareto_table_rows: list | None = None,
                  grouping_mode: str = 'lot') -> None:
    """Generate a fully interactive self-contained HTML report.

    The report embeds all run data as JSON and uses JavaScript + Plotly.react()
    to refilter/regroup live in the browser — no server needed.
    """
    ts_now = datetime.now().strftime('%Y-%m-%d %H:%M')

    # ── Serialize run data for JS ──────────────────────────────────────────
    ibin_names   = (cfg or {}).get('ibin_name',   {})
    yield_target = (cfg or {}).get('yield_target', {})

    runs_json_list = []
    for r in runs:
        date_s = r['date'].strftime('%Y-%m-%d') if r.get('date') else (
            (r.get('date_str') or '')[:10])
        runs_json_list.append({
            'lot':        r['lot'],
            'wafer':      r.get('wafer', ''),
            'sort_lot':   r.get('sort_lot', ''),
            'material':   r.get('material', ''),
            'program':    r['program'],
            'date':       date_s,
            'total_dies': r['total_dies'],
            'bin_counts': {str(k): v for k, v in r.get('bin_counts', {}).items()},
            'fb_counts':  {str(ib): {str(fb): cnt for fb, cnt in fb_map.items()}
                           for ib, fb_map in r.get('fb_counts', {}).items()},
            'fb_modules': {str(ib): {str(fb): max(bdesc_map, key=bdesc_map.get)
                           for fb, bdesc_map in fb_bdesc_map.items()}
                           for ib, fb_bdesc_map in r.get('fb_modules', {}).items()},
            'dies':       r.get('upm_950', []),  # [[ibin, upm_pct], ...]
        })

    all_progs  = sorted({r['program'] for r in runs})
    all_lots   = sorted({r['lot'] for r in runs})
    all_ibins  = sorted({ib for r in runs for ib in r.get('bin_counts', {})})
    fail_ibins = [ib for ib in all_ibins if ib not in _PASS_BINS]

    # lot -> [wafers]
    lot_wafers: dict[str, list[str]] = {}
    for r in runs:
        lot_wafers.setdefault(r['lot'], set()).add(r.get('wafer', ''))
    lot_wafers = {lot: sorted(ws) for lot, ws in lot_wafers.items()}

    # Build fb_map: FB number str -> {cat, desc} from Pass-Bin-Map and fB93xx
    _raw_cfg = (cfg or {}).get('raw', {})
    _fb_map = {}
    for fb_str, info in _raw_cfg.get('Pass-Bin-Map', {}).items():
        _fb_map[str(fb_str)] = {'cat': info.get('cat', ''), 'desc': info.get('desc', '')}
    for entry in _raw_cfg.get('fB93xx', []):
        fb_str = str(entry.get('FB', ''))
        if fb_str:
            _fb_map[fb_str] = {'cat': entry.get('name', ''), 'desc': entry.get('description', '')}

    data_js = json.dumps({
        'runs':         runs_json_list,
        'ibin_names':   {str(k): v for k, v in ibin_names.items()},
        'bin_map':      _raw_cfg.get('bin_map', {}),
        'fb_map':       _fb_map,
        'yield_target': {k: v for k, v in yield_target.items()},
        'pass_bins':    list(_PASS_BINS),
        'ff_bins':      list(_FF_BINS),
        'ff_df_bins':   list(_FF_DF_BINS),
        'palette':      _FAIL_PALETTE,
        'chart_name':   (cfg or {}).get('name', ''),
        'ff_name':      (cfg or {}).get('ff_name', 'SDS FF'),
        'ff_df_name':   (cfg or {}).get('ff_df_name', 'SDS FF+DF'),
    }, ensure_ascii=False, separators=(',', ':'))

    cfg_note = (f' &nbsp;|&nbsp; Config: <code>{Path(cfg_path).name}</code>'
                if cfg_path else '')

    # ── Build program + ibin checkbox HTML ────────────────────────────────
    prog_checks = ''.join(
        f'<label class="cb-lbl">'
        f'<input type="checkbox" class="prog-cb" value="{p}" checked> '
        f'<span>{p}</span></label>'
        for p in all_progs
    )
    ibin_checks = ''.join(
        f'<label class="cb-lbl" data-fail="{str(ib not in _PASS_BINS).lower()}">'
        f'<input type="checkbox" class="ibin-cb" value="{ib}" checked> '
        f'<span>iBin {ib}'
        + (f' — {ibin_names[ib]}' if ib in ibin_names else '')
        + '</span></label>'
        for ib in all_ibins
    )
    def _wafer_items(lot):
        items = []
        for w in lot_wafers[lot]:
            val   = f'{lot}::{w}'
            label = w or '(no wafer)'
            # Get material for this wafer if available
            wafer_key = f'{lot}::{w}'
            wafer_mat = wafer_materials.get(wafer_key, '')
            wafer_title = f'Wafer: {label}' + (f' - {wafer_mat}' if wafer_mat else '')
            items.append(
                f'<label class="cb-lbl wafer-item" data-lot="{lot}" title="{wafer_title}">'
                f'<input type="checkbox" class="wafer-cb" value="{val}" checked> '
                f'<span>{label}</span></label>'
            )
        return ''.join(items)

    # lot -> material (first non-empty value seen)
    lot_material: dict[str, str] = {}
    # lot::wafer -> material (per-wafer material tracking)
    wafer_materials: dict[str, str] = {}
    # lot -> set of unique materials for this lot
    lot_materials_set: dict[str, set[str]] = {}
    for r in runs:
        lot = r['lot']
        wafer = r.get('wafer', '')
        material = r.get('material', '')
        if lot not in lot_material:
            lot_material[lot] = material
        wafer_key = f'{lot}::{wafer}'
        if wafer_key not in wafer_materials:
            wafer_materials[wafer_key] = material
        if lot not in lot_materials_set:
            lot_materials_set[lot] = set()
        if material:
            lot_materials_set[lot].add(material)

    # Group lots by first 7 chars of sort_lot for cleaner sidebar display
    from collections import OrderedDict as _OD
    lot_groups: dict[str, list[str]] = _OD()
    for lot in all_lots:
        prefix = lot[:7]
        lot_groups.setdefault(prefix, []).append(lot)

    def _material_wafer_items(lot, material):
        """Return wafers for a specific lot+material combination."""
        items = []
        for w in lot_wafers[lot]:
            wafer_key = f'{lot}::{w}'
            wafer_mat = wafer_materials.get(wafer_key, '')
            # Only include wafers with matching material
            if wafer_mat == material:
                val = wafer_key
                label = w or '(no wafer)'
                wafer_title = f'Wafer: {label}' + (f' - {wafer_mat}' if wafer_mat else '')
                items.append(
                    f'<label class="cb-lbl wafer-item" data-lot="{lot}" title="{wafer_title}">'
                    f'<input type="checkbox" class="wafer-cb" value="{val}" checked> '
                    f'<span>{label}</span></label>'
                )
        return ''.join(items)

    def _lot_group_html(prefix: str, lots_in_group: list[str]) -> str:
        # Derive material for the prefix group (first non-empty value among lots)
        prefix_mat = next((lot_material[l] for l in lots_in_group if lot_material.get(l)), '')
        single = len(lots_in_group) == 1 and lots_in_group[0] == prefix
        # Build individual lot rows
        lot_rows = ''
        for lot in lots_in_group:
            mat = lot_material.get(lot, '')
            mat_span = f' <span style="color:#7fb3d3;font-size:10px">({mat})</span>' if mat else ''
            mat_title = f' - {mat}' if mat else ''
            
            # Check if lot has multiple materials (for lot-grouping mode)
            lot_has_multi_mats = len(lot_materials_set.get(lot, set())) > 1
            
            if grouping_mode == 'lot' and lot_has_multi_mats:
                # Build material nesting for lots with multiple materials
                material_rows = ''
                for material in sorted(lot_materials_set.get(lot, [])):
                    if material:  # Skip empty materials
                        material_rows += (
                            f'<div class="material-row" style="margin-left:20px">'
                            f'<input type="checkbox" class="material-cb" id="mat-cb-{lot}-{material}" '
                            f'value="{lot}::{material}" checked>'
                            f'<label for="mat-cb-{lot}-{material}" style="color:#7fb3d3">{material}</label>'
                            f'<span class="wafer-arrow" onclick="toggleMaterialWafers(this)" '
                            f'data-lot="{lot}" data-material="{material}">&#9654;</span>'
                            f'</div>'
                            f'<div class="material-drop" id="mdrop-{lot}-{material}" style="display:none;margin-left:20px">'
                            f'{_material_wafer_items(lot, material)}'
                            f'</div>'
                        )
                lot_rows += (
                    f'<div class="lot-row">'
                    f'<input type="checkbox" class="lot-cb" id="lot-cb-{lot}" value="{lot}" checked onchange="toggleLotWafers(this)">'
                    f'<label for="lot-cb-{lot}" class="lot-label" title="Lot: {lot}{mat_title}">{lot}{mat_span}</label>'
                    f'<span class="wafer-arrow" onclick="toggleWaferDrop(this)" data-lot="{lot}">&#9654;</span>'
                    f'</div>'
                    f'<div class="wafer-drop" id="wdrop-{lot}" style="display:none">'
                    f'{material_rows}'
                    f'</div>'
                )
            else:
                # Standard flat wafer list (wafer mode or single-material lot)
                lot_rows += (
                    f'<div class="lot-row">'
                    f'<input type="checkbox" class="lot-cb" id="lot-cb-{lot}" value="{lot}" checked onchange="toggleLotWafers(this)">'
                    f'<label for="lot-cb-{lot}" class="lot-label" title="Lot: {lot}{mat_title}">{lot}{mat_span}</label>'
                    f'<span class="wafer-arrow" onclick="toggleWaferDrop(this)" data-lot="{lot}">&#9654;</span>'
                    f'</div>'
                    f'<div class="wafer-drop" id="wdrop-{lot}">{_wafer_items(lot)}</div>'
                )
        
        if single:
            # Only one lot in group — no extra nesting; still show material on the row
            return f'<div class="lot-group" data-prefix="{prefix}">{lot_rows}</div>'
        # Multiple lots share this prefix — add group header with material
        grp_id = f'lotgrp-{prefix}'
        mat_tag = f' <span style="color:#7fb3d3;font-size:10px">({prefix_mat})</span>' if prefix_mat else ''
        prefix_title = f'{prefix}... ({len(lots_in_group)} lots)' + (f' - {prefix_mat}' if prefix_mat else '')
        return (
            f'<div class="lot-group" data-prefix="{prefix}">'
            f'<div class="lot-group-hdr">'
            f'<input type="checkbox" class="lot-grp-cb" data-grp="{prefix}" checked onchange="toggleLotGroup(this)">'
            f'<span class="lot-group-lbl" onclick="toggleLotGroupDrop(\'{grp_id}\')" title="{prefix_title}">'
            f'{prefix}&#8230;{mat_tag} ({len(lots_in_group)} lots) &#9654;</span>'
            f'</div>'
            f'<div class="lot-group-drop" id="{grp_id}" style="display:none">'
            + lot_rows +
            f'</div>'
            f'</div>'
        )

    lot_wafer_checks = ''.join(
        _lot_group_html(prefix, lots)
        for prefix, lots in lot_groups.items()
    )

    # ── Pareto table HTML ──────────────────────────────────────────────────
    def _build_pareto_table(rows):
        if not rows:
            return ''
        toolbar = (
            '<div class="pareto-comment-toolbar">'
            '<button class="btn-comment-action" onclick="exportParetoTableCsv()">&#8681; Export Table CSV</button>'
            '<button class="btn-comment-action" onclick="exportComments()">&#8681; Export Comments CSV</button>'
            '<label class="btn-comment-action" style="cursor:pointer">'
            '&#8679; Import Comments CSV'
            '<input type="file" accept=".csv" style="display:none" onchange="importComments(this)">'
            '</label>'
            '</div>'
        )
        hdr = ('<table class="pareto-tbl" id="pareto-summary-tbl"><thead><tr>'
               '<th>IB</th><th>Description</th>'
               '<th>N Fail</th><th>Fail (%)</th><th>Comment</th></tr></thead><tbody>')
        body = ''.join(
            f'<tr><td>{r["ib"]}</td>'
            f'<td>{(r["cat"] + " \u2014 " + r["desc"]) if (r["cat"] and r["desc"] and r["cat"] != r["desc"]) else (r["cat"] or r["desc"])}</td>'
            f'<td>{r["n_fail"]}</td><td>{r["pct"]:.2f}%</td>'
            f'<td><textarea class="pareto-comment" data-ib="{r["ib"]}" rows="1" placeholder="Add comment..."></textarea></td></tr>'
            for r in rows
        )
        return toolbar + hdr + body + '</tbody></table>'

    pareto_table_html = _build_pareto_table(pareto_table_rows or [])

    # ── Plotly JS: embed inline (no CDN/network dependency) ─────────────────
    _lib_dir = Path(__file__).resolve().parents[3] / 'shared' / 'library'
    _plotly_js_files = sorted(_lib_dir.glob('plotly*.min.js')) if _lib_dir.exists() else []
    if _plotly_js_files:
        _plotly_js_content = _plotly_js_files[-1].read_text(encoding='utf-8')
        _plotly_inline = f'<script>{_plotly_js_content}</script>'
        _plotly_inline_at_end = ''
        print(f'Using local Plotly: {_plotly_js_files[-1].name}')
    else:
        _plotly_inline = '<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>'
        _plotly_inline_at_end = ''
        print('Using Plotly CDN fallback')

    # ── Static chart embeds ───────────────────────────────────────────────────
    # Plotly 6.x injects "responsive":true into Plotly.newPlot configs.
    # Plotly.js 3.x handles this correctly, so strip is just a safety measure.
    from plotly.offline import plot as _plotly_plot
    import re as _re
    def _make_div(fig, cfg=None):
        d = _plotly_plot(fig, output_type='div', include_plotlyjs=False,
                         config=cfg or {'displayModeBar': True})
        d = _re.sub(r',?\s*"responsive"\s*:\s*true', '', d)
        m = _re.search(r'<div id="([^"]+)"[^>]*class="plotly-graph-div"', d)
        return d, (m.group(1) if m else '')

    trend_div,      _trend_id    = _make_div(trend_fig,
                                              {'displayModeBar': True, 'scrollZoom': True})
    pareto_div,     _pareto_h_id = _make_div(pareto_fig)
    if pareto_vertical_fig is not None:
        pareto_vert_div, _pareto_v_id = _make_div(pareto_vertical_fig)
    else:
        pareto_vert_div, _pareto_v_id = '<p style="color:#888">Not available</p>', ''

    html = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>iBin Fail vs Yield Trend — {csv_path.name}</title>
{_plotly_inline}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Arial,sans-serif;background:#f0f3f7;display:flex;height:100vh;overflow:hidden}}

/* ── Sidebar ── */
#sidebar{{width:280px;min-width:220px;background:#1a252f;color:#ecf0f1;
  display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}}
#sidebar-header{{padding:12px 14px 8px;border-bottom:1px solid #2c3e50}}
#sidebar-header h1{{font-size:14px;color:#3498db;line-height:1.3}}
#sidebar-header .meta{{font-size:10px;color:#7f8c8d;margin-top:3px}}
#sidebar-watermark{{padding:4px 14px;font-size:10px;font-weight:bold;color:#3498db;border-bottom:1px solid #2c3e50}}
#sidebar-body{{overflow-y:auto;flex:1;padding:0 0 12px}}
#sidebar-body::-webkit-scrollbar{{width:4px}}
#sidebar-body::-webkit-scrollbar-thumb{{background:#2c3e50}}

.ctrl-section{{padding:8px 12px 4px}}
.ctrl-section h3{{font-size:11px;font-weight:bold;color:#95a5a6;
  text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.sep{{height:1px;background:#2c3e50;margin:4px 10px}}

/* interval radios */
.iv-row{{display:flex;flex-wrap:wrap;gap:4px}}
.iv-row label{{font-size:11px;cursor:pointer;padding:3px 8px;border-radius:4px;
  background:#243342;color:#bdc3c7}}
.date-range-row{{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:11px}}
.date-range-row label{{display:flex;align-items:center;gap:3px;cursor:pointer;color:#bdc3c7}}
.date-range-row{{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:11px}}
.date-range-row label{{display:flex;align-items:center;gap:3px;cursor:pointer;color:#bdc3c7}}
.iv-row input[type=radio]{{display:none}}
.iv-row input[type=radio]:checked + span{{color:#fff;font-weight:bold}}
.iv-row label:has(input:checked){{background:#2980b9}}

/* filter btn row */
.btn-row{{display:flex;gap:4px;margin-bottom:6px}}
.btn-row button{{flex:1;font-size:10px;padding:3px 0;border:none;border-radius:3px;
  cursor:pointer;font-weight:bold}}
.btn-all{{background:#1f618d;color:white}}
.btn-none{{background:#555;color:white}}
.btn-fail{{background:#7d1f1f;color:white}}

/* checkboxes */
.cb-list{{max-height:220px;overflow-y:scroll;background:#243342;border-radius:4px;
  padding:4px;scrollbar-width:thin;scrollbar-color:#3498db #1a252f}}
.cb-list::-webkit-scrollbar{{width:10px}}
.cb-list::-webkit-scrollbar-track{{background:#1a252f;border-radius:4px}}
.cb-list::-webkit-scrollbar-thumb{{background:#3498db;border-radius:4px;min-height:40px}}
.cb-list::-webkit-scrollbar-thumb:hover{{background:#5dade2}}
.cb-lbl{{display:flex;align-items:center;gap:5px;font-size:11px;color:#bdc3c7;
  padding:2px 4px;cursor:pointer;border-radius:3px}}
.cb-lbl:hover{{background:#2c3e50}}
.cb-lbl input{{cursor:pointer;accent-color:#3498db}}
.cb-lbl span{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
/* lot-wafer nested */
.lot-group{{margin-bottom:2px}}
.lot-group-hdr{{display:flex;align-items:center;gap:5px;padding:2px 4px;border-radius:3px;cursor:pointer}}
.lot-group-hdr:hover{{background:#2c3e50}}
.lot-group-lbl{{font-size:11px;color:#aed6f1;font-weight:bold;flex:1;cursor:pointer;white-space:nowrap}}
.lot-group-drop{{padding-left:12px}}
.lot-row{{display:flex;align-items:center;gap:5px;padding:2px 4px;border-radius:3px}}
.lot-row:hover{{background:#2c3e50}}
.lot-label{{font-size:11px;color:#ecf0f1;cursor:pointer;flex:1;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}}
.wafer-arrow{{font-size:9px;color:#7f8c8d;cursor:pointer;user-select:none;
  padding:0 2px;flex-shrink:0}}
.wafer-arrow:hover{{color:#3498db}}
.wafer-drop{{display:none;padding-left:18px;margin-top:2px;background:#1a252f;
  border-left:2px solid #2c3e50;margin-left:0px}}
.wafer-drop.show{{display:block}}
.wafer-drop .cb-lbl{{font-size:10px;color:#95a5a6;padding-left:4px}}

/* options */
.opt-grid{{display:grid;grid-template-columns:auto 1fr;gap:4px 8px;align-items:center}}
.opt-grid label{{font-size:11px;color:#bdc3c7}}
.opt-grid input{{background:#243342;border:1px solid #2c3e50;color:white;
  border-radius:3px;padding:2px 6px;font-size:11px;width:70px}}

/* generate button */
#gen-btn{{margin:10px 12px 4px;padding:8px;background:#27ae60;color:white;
  font-size:13px;font-weight:bold;border:none;border-radius:5px;cursor:pointer;width:calc(100% - 24px)}}
#gen-btn:hover{{background:#2ecc71}}
#gen-btn:active{{background:#1e8449}}

/* stats bar */
#stats-bar{{display:flex;gap:6px;padding:4px 12px;flex-wrap:wrap}}
.stat-chip{{background:#2c3e50;border-radius:4px;padding:2px 8px;font-size:10px;color:#7f8c8d}}
.stat-chip b{{color:#3498db}}

/* ── Main area ── */
#main{{flex:1;display:flex;flex-direction:column;overflow:hidden}}
#tabs{{display:flex;background:#fff;border-bottom:2px solid #dce3eb;padding:0 16px}}
.tab{{padding:10px 18px;cursor:pointer;font-size:13px;color:#7f8c8d;
  border-bottom:2px solid transparent;margin-bottom:-2px}}
.tab.active{{color:#2980b9;border-bottom-color:#2980b9;font-weight:bold}}
#tab-content{{flex:1;overflow:auto;padding:12px 16px;display:flex;flex-direction:column}}

/* chart containers */
.chart-card{{background:white;border-radius:8px;padding:12px;margin-bottom:14px;
  box-shadow:0 1px 4px rgba(0,0,0,.08);display:flex;flex-direction:column}}
.chart-card h2{{font-size:15px;color:#2c3e50;margin-bottom:8px;flex-shrink:0}}
.chart-wrap{{width:100%;height:520px;min-height:200px;min-width:300px;
  position:relative;resize:both;overflow:hidden;box-sizing:border-box;
  border:1px solid #e0e0e0;border-radius:4px}}
.chart-wrap > div{{width:100% !important;height:100% !important}}

/* run table */
#run-table{{width:100%;border-collapse:collapse;font-size:12px}}
#run-table th{{background:#2c3e50;color:white;padding:6px 10px;text-align:left;
  position:sticky;top:0;z-index:2}}
#run-table td{{padding:4px 10px;border-bottom:1px solid #e8eaed}}
#run-table tr:hover td{{background:#f0f4f8}}
.yld-ok{{color:#27ae60;font-weight:bold}}
.yld-mid{{color:#f39c12;font-weight:bold}}
.yld-low{{color:#e74c3c;font-weight:bold}}
code{{background:#eef;padding:1px 4px;border-radius:3px;font-size:11px}}

/* pareto summary table */
.fb-drill-tbl{{width:100%;border-collapse:collapse;font-size:12px;margin-top:4px}}
.fb-drill-tbl th{{background:#1a5276;color:white;padding:6px 10px;text-align:left;font-weight:bold;position:sticky;top:0}}
.fb-drill-tbl th.num{{text-align:right}}
.fb-drill-tbl td{{padding:5px 10px;border-bottom:1px solid #e8eaed;vertical-align:middle}}
.fb-drill-tbl td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.fb-drill-tbl tr:hover td{{background:#eaf4fb}}
.fb-drill-tbl td:first-child,.fb-drill-tbl td:nth-child(2){{font-weight:bold;color:#1a5276}}
.pareto-tbl{{width:100%;border-collapse:collapse;font-size:12px;margin-top:16px}}
.pareto-tbl th{{background:#2c3e50;color:white;padding:6px 10px;text-align:left;font-weight:bold}}
.pareto-tbl td{{padding:4px 10px;border-bottom:1px solid #e8eaed;vertical-align:middle}}
.pareto-tbl tr:hover td{{background:#f0f4f8}}
.pareto-tbl td:first-child{{font-weight:bold;color:#1a5276}}
.pareto-comment-toolbar{{display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap}}
.btn-comment-action{{background:#2c3e50;color:#ecf0f1;border:1px solid #3d5166;border-radius:4px;
  padding:4px 10px;font-size:11px;cursor:pointer;transition:background 0.15s}}
.btn-comment-action:hover{{background:#3d5166}}
.pareto-comment{{width:100%;min-width:160px;padding:4px 6px;border:1px solid #ddd;border-radius:4px;
  font-size:12px;font-family:inherit;resize:vertical;background:#fffef0;
  transition:border-color 0.2s}}
.pareto-comment:focus{{outline:none;border-color:#1a73e8;background:#fff}}
.pareto-comment.saved{{border-color:#27ae60;background:#f0fff4}}
/* resizable columns */
th.resizable{{position:relative;overflow:visible}}
th.resizable .col-resizer{{position:absolute;right:0;top:0;bottom:0;width:5px;
  cursor:col-resize;user-select:none;z-index:3;background:transparent}}
th.resizable .col-resizer:hover{{background:rgba(255,255,255,0.3)}}

/* ── DLCP (exact match to bin_distribution_html.py) ─────────────────── */
.dlcp-ctrl{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;background:#fff;padding:7px 12px;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex-shrink:0}}
.dlcp-sumbox{{background:transparent;border-radius:0;padding:0;box-shadow:none;flex-shrink:0;display:flex;flex-direction:column;gap:6px;align-items:stretch}}
.dlcp-sum-panel{{background:#fff;border-radius:6px;padding:8px 14px;box-shadow:0 1px 4px rgba(0,0,0,.1);display:flex;flex-direction:column;gap:4px;min-width:0}}
.dlcp-sum-panel-ttl{{font-size:15px;font-weight:bold;text-transform:uppercase;letter-spacing:.7px;color:#fff;background:#5d6d7e;border-radius:3px;padding:1px 8px;margin-bottom:4px;align-self:flex-start}}
.dlcp-sumrow{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}}
.dlcp-sum-grp{{display:flex;flex-direction:column;padding:4px 14px;border-left:3px solid #dde;min-width:110px}}
.dlcp-sum-grp.pass{{border-color:#2980b9}}.dlcp-sum-grp.marg{{border-color:#d4ac0d}}.dlcp-sum-grp.fail{{border-color:#c0392b}}
.dlcp-sum-lbl{{font-size:17px;color:#000;text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px}}
.dlcp-sum-val{{font-size:26px;font-weight:bold;color:#2c3e50}}.dlcp-sum-pct{{font-size:17px;color:#666;margin-left:4px}}
.dlcp-sum-pct-big{{font-size:33px;font-weight:bold;line-height:1.1}}
.dlcp-sum-sub{{font-size:15px;color:#aaa;margin-top:1px}}
.dlcp-inner{{display:flex;gap:0;flex:1;min-height:0}}
.dlcp-left{{display:flex;flex-direction:column;gap:6px;min-width:0;flex:1;overflow:hidden}}
.dlcp-panel-hdr{{display:flex;align-items:center;gap:5px;flex-shrink:0}}
.dlcp-pbtn{{background:#ecf0f1;border:1px solid #bdc3c7;border-radius:3px;font-size:11px;padding:1px 7px;cursor:pointer;color:#2c3e50;white-space:nowrap}}
.dlcp-pbtn:hover{{background:#d5dbde}}
.dlcp-flt-row input{{width:100%;box-sizing:border-box;font-size:11px;padding:2px 4px;border:1px solid #ccd;border-radius:2px}}
.dlcp-sec-ttl{{font-size:11px;font-weight:bold;color:#5d6d7e;text-transform:uppercase;letter-spacing:.5px;flex-shrink:0}}
.dlcp-tw{{overflow:auto;background:#fff;border-radius:6px;padding:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex:1;min-height:0}}
#dlcp-tbl-pane{{display:flex;flex-direction:column;flex:1;min-height:0;overflow:hidden}}
.dlcp-t{{border-collapse:collapse;font-size:12px;white-space:nowrap;width:100%}}
.dlcp-t th{{background:#2c3e50;color:#ecf0f1;padding:5px 10px;text-align:left;position:sticky;top:0;z-index:1}}
.dlcp-t td{{padding:4px 10px;border-bottom:1px solid #eee}}
.dlcp-t tr:nth-child(even) td{{background:#f7f9fc}}.dlcp-t tr:hover td{{background:#eaf3fb}}
.dlcp-t tr.dlcp-rsel td{{background:#d0eaff!important;font-weight:bold}}
.dlcp-t tr.dlcp-runsel td{{opacity:.4}}
.dlcp-t tr{{cursor:pointer}}
.dlcp-ddbtn{{background:none;border:none;color:#aed6f1;cursor:pointer;font-size:10px;padding:0 2px;vertical-align:middle;margin-left:3px}}
.dlcp-ddbtn.on{{color:#f1c40f}}
.dlcp-dd{{position:fixed;background:#fff;border:1px solid #aaa;border-radius:4px;box-shadow:0 4px 16px rgba(0,0,0,.2);z-index:30000;min-width:160px;max-width:260px;font-size:12px;color:#2c3e50}}
.dlcp-dd-srch{{width:100%;box-sizing:border-box;padding:5px 8px;border:none;border-bottom:1px solid #ddd;font-size:12px;outline:none}}
.dlcp-dd-acts{{display:flex;gap:4px;padding:4px 6px;border-bottom:1px solid #eee}}
.dlcp-dd-acts button{{flex:1;padding:2px 6px;font-size:11px;cursor:pointer;border:1px solid #bdc3c7;background:#ecf0f1;border-radius:3px}}
.dlcp-dd-list{{max-height:200px;overflow-y:auto;padding:4px 0}}
.dlcp-dd-item{{display:flex;align-items:center;gap:6px;padding:3px 10px;cursor:pointer}}
.dlcp-dd-item:hover{{background:#eaf0fb}}
.dlcp-dd-foot{{padding:4px 8px;border-top:1px solid #eee;text-align:right}}
.dlcp-dd-foot button{{padding:3px 12px;font-size:11px;cursor:pointer;background:#2c3e50;color:#fff;border:none;border-radius:3px}}
.dlcp-splitter{{width:14px;flex-shrink:0;display:flex;align-items:center;justify-content:center;cursor:pointer;background:#e8ecf0;border-left:1px solid #d0d8e8;border-right:1px solid #d0d8e8;transition:background .15s;user-select:none}}
.dlcp-splitter:hover{{background:#c8d4e8}}
.dlcp-split-arrow{{font-size:12px;color:#5d6d7e}}
.dlcp-cw{{flex:1;background:#fff;border-radius:6px;padding:8px;box-shadow:0 1px 4px rgba(0,0,0,.1);display:flex;flex-direction:column;min-width:0;overflow:hidden;margin-left:8px}}
.dlcp-t .num{{text-align:right}}
.dlcp-note{{font-size:10px;color:#666;background:#f8f9fa;border:1px solid #e4e4e4;border-radius:4px;padding:5px 10px;line-height:1.8;flex-shrink:0}}
.dlcp-note b{{color:#444}}
.upm-hist-overlay{{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.45);z-index:32000;align-items:center;justify-content:center}}
.upm-hist-overlay.open{{display:flex}}
.upm-hist-box{{background:#f0f2f5;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.4);width:800px;max-width:96vw;height:540px;max-height:92vh;display:flex;flex-direction:column;overflow:hidden;resize:both;min-width:420px;min-height:340px}}
.upm-hist-drag{{cursor:move;background:#1f618d;color:#fff;padding:7px 14px;display:flex;align-items:center;justify-content:space-between;user-select:none;flex-shrink:0}}
.upm-hist-body{{display:flex;flex-direction:column;flex:1;padding:10px;gap:8px;min-height:0;overflow:hidden}}
.upm-hist-stats{{display:flex;flex-wrap:wrap;gap:6px;background:#fff;border-radius:6px;padding:8px 12px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex-shrink:0}}
.upm-hist-stat-grp{{display:flex;flex-direction:column;padding:3px 12px;border-left:3px solid #dde;min-width:100px}}
.upm-hist-stat-lbl{{font-size:10px;color:#888;text-transform:uppercase;letter-spacing:.4px;font-weight:bold}}
.upm-hist-stat-val{{font-size:14px;font-weight:bold;color:#2c3e50}}
.upm-hist-cv-wrap{{flex:1;min-height:0;display:flex}}
</style>
</head>
<body>

<!-- ═══ SIDEBAR ═══ -->
<div id="sidebar">
  <div id="sidebar-header">
    <h1>&#128200; iBin Fail vs. Yield Trend</h1>
    <div class="meta">
      <b>{csv_path.name}</b>{cfg_note}<br>
      Generated: {ts_now}
    </div>
  </div>
  <div id="sidebar-watermark">Pant, Sujit N &mdash; GEMS FTE</div>
  <div id="sidebar-body">

    <!-- Grouping -->
    <div class="ctrl-section">
      <h3>Grouping</h3>
      <div class="iv-row">
        <label><input type="radio" name="groupby" value="lot" checked><span>Program / Lot</span></label>
        <label><input type="radio" name="groupby" value="wafer"><span>Program / Lot / Wafer</span></label>
      </div>
    </div>
    <div class="sep"></div>

    <!-- Interval -->
    <div class="ctrl-section">
      <h3>Interval</h3>
      <div class="iv-row">
        {''.join(f'<label><input type="radio" name="interval" value="{iv}"{"checked" if iv==interval else ""}><span>{iv.capitalize()}</span></label>' for iv in INTERVALS)}
      </div>
    </div>
    <div class="sep"></div>

    <!-- Lot / Wafer filter -->
    <div class="ctrl-section">
      <h3>Lot / Wafer</h3>
      <input type="text" id="lot-search" placeholder="Filter lots..." 
        style="width:100%;box-sizing:border-box;background:#243342;border:1px solid #2c3e50;
        color:#bdc3c7;padding:4px 6px;border-radius:3px;font-size:11px;margin-bottom:6px"
        oninput="filterLots(this.value)">
      <div class="btn-row">
        <button class="btn-all" onclick="lotWaferAll(true)">All</button>
        <button class="btn-none" onclick="lotWaferAll(false)">None</button>
      </div>
      <div class="cb-list" id="lot-wafer-list" style="max-height:350px">{lot_wafer_checks}</div>
    </div>
    <div class="sep"></div>

    <!-- Program filter -->
    <div class="ctrl-section">
      <h3>Test Program</h3>
      <div class="btn-row">
        <button class="btn-all" onclick="selAll('.prog-cb')">All</button>
        <button class="btn-none" onclick="selNone('.prog-cb')">None</button>
      </div>
      <div class="cb-list" id="prog-list">{prog_checks}</div>
    </div>
    <div class="sep"></div>

    <!-- Interface Bin filter -->
    <div class="ctrl-section">
      <h3>Interface Bin</h3>
      <div class="btn-row">
        <button class="btn-all" onclick="selAll('.ibin-cb')">All</button>
        <button class="btn-none" onclick="selNone('.ibin-cb')">None</button>
        <button class="btn-fail" onclick="selFail()">Fail only</button>
      </div>
      <div class="cb-list" id="ibin-list">{ibin_checks}</div>
    </div>
    <div class="sep"></div>

    <!-- Date Range -->
    <div class="ctrl-section">
      <h3>Date Range</h3>
      <div class="date-range-row">
        <label><input type="radio" name="datemode" value="all" checked> All</label>
        <label><input type="radio" name="datemode" value="4w"> 4 wks</label>
        <label><input type="radio" name="datemode" value="6w"> 6 wks</label>
        <label><input type="radio" name="datemode" value="12w"> 12 wks</label>
        <label><input type="radio" name="datemode" value="custom"> Custom</label>
      </div>
      <div id="custom-date-row" style="display:none;margin-top:6px">
        <div style="display:grid;grid-template-columns:auto 1fr;gap:4px 6px;align-items:center;font-size:11px">
          <span style="color:#95a5a6">From</span>
          <input type="date" id="date-from" style="background:#2c3e50;color:#ecf0f1;border:1px solid #3d5166;border-radius:3px;padding:2px 4px;font-size:11px;width:100%">
          <span style="color:#95a5a6">To</span>
          <input type="date" id="date-to" style="background:#2c3e50;color:#ecf0f1;border:1px solid #3d5166;border-radius:3px;padding:2px 4px;font-size:11px;width:100%">
        </div>
      </div>
    </div>
    <div class="sep"></div>

    <!-- Options -->
    <div class="ctrl-section">
      <h3>Options</h3>
      <div class="opt-grid">
        <label>Top N iBins</label>
        <input type="number" id="topn-input" value="{top_n}" min="1" max="30">
        <label>Min fail% thresh</label>
        <input type="number" id="thresh-input" value="0.0" min="0" step="0.5">
      </div>
    </div>

    <button id="gen-btn" onclick="rebuildCharts()">&#9654;&#xFE0E; Apply Filters</button>

    <!-- Stats -->
    <div class="sep"></div>
    <div id="stats-bar"></div>
  </div>
</div>

<!-- ═══ MAIN ═══ -->
<div id="main">
  <div id="tabs">
    <div class="tab active" onclick="showTab('trend',this)">&#128204; Trend</div>
    <div class="tab" onclick="showTab('pareto-h',this)">&#128202; Pareto (horizontal)</div>
    <div class="tab" onclick="showTab('pareto-v',this)">&#128202; Pareto (by bin)</div>
    <div class="tab" onclick="showTab('table',this)">&#128209; Run Table</div>
    <div class="tab" onclick="showTab('dlcp',this)">&#9889; DLCP</div>
  </div>
  <div id="tab-content">
    <div id="tab-trend">
      <div class="chart-card">
        <h2 style="margin-bottom:8px">&#128204; iBin Fail Trend
          <button onclick="exportTrendCsv()" style="font-size:11px;margin-left:10px;padding:2px 8px;cursor:pointer;border:1px solid #aaa;border-radius:3px;background:#f5f5f5">&#8681; CSV</button></h2>
        <div id="trend-chart" class="chart-wrap">{trend_div}</div>
      </div>
      <div class="chart-card" id="trend-fb-drilldown" style="display:none">
        <h2 id="trend-fb-title">Functional Bin Breakdown — IB <span id="trend-fb-ib"></span>
          <button onclick="exportFbDrilldownCsv('trend-fb-thead','trend-fb-tbody','fb_drilldown')" style="font-size:11px;margin-left:10px;padding:2px 8px;cursor:pointer;border:1px solid #aaa;border-radius:3px;background:#f5f5f5">&#8681; CSV</button></h2>
        <div style="overflow-x:auto"><table class="fb-drill-tbl" id="trend-fb-tbl">
          <thead id="trend-fb-thead"><tr><th>Interface Bin</th><th>Lot (Wafers)</th><th>Functional Bin</th><th>Description</th><th>Fail Test Module</th>
            <th class="num">Total Tested</th><th class="num">Fail Count</th><th class="num">Fail %</th></tr></thead>
          <tbody id="trend-fb-tbody"></tbody>
        </table></div>
      </div>
    </div>
    <div id="tab-pareto-h" style="display:none">
      <div class="chart-card">
        <h2>Interface Bin Fail Pareto (horizontal)</h2>
        <div id="pareto-h-chart" class="chart-wrap">{pareto_div}</div>
      </div>
      <div class="chart-card" id="pareto-h-fb-drilldown" style="display:none">
        <h2 id="pareto-h-fb-title">Functional Bin Breakdown — IB <span id="pareto-h-fb-ib"></span>
          <button onclick="exportFbDrilldownCsv('pareto-h-fb-thead','pareto-h-fb-tbody','fb_drilldown')" style="font-size:11px;margin-left:10px;padding:2px 8px;cursor:pointer;border:1px solid #aaa;border-radius:3px;background:#f5f5f5">&#8681; CSV</button></h2>
        <div style="overflow-x:auto"><table class="fb-drill-tbl" id="pareto-h-fb-tbl">
          <thead id="pareto-h-fb-thead"><tr><th>Interface Bin</th><th>Lot (Wafers)</th><th>Functional Bin</th><th>Description</th><th>Fail Test Module</th>
            <th class="num">Total Tested</th><th class="num">Fail Count</th><th class="num">Fail %</th></tr></thead>
          <tbody id="pareto-h-fb-tbody"></tbody>
        </table></div>
      </div>
    </div>
    <div id="tab-pareto-v" style="display:none">
      <div class="chart-card">
        <h2>Interface Bin Fail Pareto — by bin</h2>
        <div id="pareto-v-chart" class="chart-wrap">{pareto_vert_div}</div>
        {pareto_table_html}
      </div>
      <div class="chart-card" id="pareto-v-fb-drilldown" style="display:none">
        <h2 id="pareto-v-fb-title">Functional Bin Breakdown — IB <span id="pareto-v-fb-ib"></span>
          <button onclick="exportFbDrilldownCsv('pareto-v-fb-thead','pareto-v-fb-tbody','fb_drilldown_v')" style="font-size:11px;margin-left:10px;padding:2px 8px;cursor:pointer;border:1px solid #aaa;border-radius:3px;background:#f5f5f5">&#8681; CSV</button></h2>
        <div style="overflow-x:auto"><table class="fb-drill-tbl" id="pareto-v-fb-tbl">
          <thead id="pareto-v-fb-thead"><tr><th>Interface Bin</th><th>Lot (Wafers)</th><th>Functional Bin</th><th>Description</th><th>Fail Test Module</th>
            <th class="num">Total Tested</th><th class="num">Fail Count</th><th class="num">Fail %</th></tr></thead>
          <tbody id="pareto-v-fb-tbody"></tbody>
        </table></div>
      </div>
    </div>
    <div id="tab-table" style="display:none">
      <div class="chart-card" style="overflow:auto">
        <table id="run-table">
          <thead><tr>
            <th>Period</th><th>Date</th><th>Lot</th><th>Wafer</th>
            <th>Program</th><th>FF Yield%</th><th>FF+DF Yield%</th>
            <th>Top Fail Bins</th>
          </tr></thead>
          <tbody id="run-table-body"></tbody>
        </table>
      </div>
    </div>
    <div id="tab-dlcp" style="display:none;flex-direction:column;padding:8px;gap:6px;overflow:hidden">
      <div class="dlcp-ctrl">
        <label style="font-weight:bold">UPM Threshold:</label>
        <input type="range" id="dlcp-sl" min="70" max="100" step="0.5" value="92.5" style="width:180px" oninput="dlcpSliderT()">
        <input type="number" id="dlcp-tv-inp" min="70" max="100" step="0.5" value="92.5" style="width:64px;font-size:13px;padding:2px 4px;border:1px solid #aac;border-radius:3px;text-align:right" oninput="dlcpTxtT(this.value)" onchange="dlcpTxtT(this.value)">
        <span style="color:#1a5276;font-size:13px">%</span>
        <button onclick="dlcpOpenHistT()" style="margin-left:12px;padding:4px 16px;font-size:13px;font-weight:bold;background:linear-gradient(135deg,#1a5276,#2980b9);color:#fff;border:none;border-radius:5px;cursor:pointer;box-shadow:0 2px 6px rgba(0,0,0,.25)">&#128202; UPM Distribution</button>
        <span id="dlcp-no-data-msg" style="color:#c0392b;font-weight:bold;display:none">No UPM die data available.</span>
      </div>
      <div class="dlcp-sumbox" id="dlcp-sumbox"></div>
      <div class="dlcp-inner" style="flex:1;min-height:300px">
        <div class="dlcp-left" id="dlcp-left-pane">
          <div class="dlcp-panel-hdr">
            <span class="dlcp-sec-ttl" style="flex:1">Per-Wafer Detail</span>
            <button class="dlcp-pbtn" onclick="dlcpSelAllT()">&#9745; All</button>
            <button class="dlcp-pbtn" onclick="dlcpSelNoneT()">&#9746; None</button>
            <button class="dlcp-pbtn" onclick="dlcpDownloadCsvT()">&#8681; CSV</button>
            <button class="dlcp-pbtn" onclick="dlcpClearFiltersT()">&#10005; Filters</button>
          </div>
          <div id="dlcp-tbl-pane"><div class="dlcp-tw"><table class="dlcp-t"><thead>
            <tr>
              <th rowspan="2">Lot <button class="dlcp-ddbtn" id="dlcp-dd-btn-0" onclick="event.stopPropagation();dlcpDdOpenT(0,this)">&#9660;</button></th>
              <th rowspan="2">Wafer <button class="dlcp-ddbtn" id="dlcp-dd-btn-1" onclick="event.stopPropagation();dlcpDdOpenT(1,this)">&#9660;</button></th>
              <th rowspan="2">Material <button class="dlcp-ddbtn" id="dlcp-dd-btn-2" onclick="event.stopPropagation();dlcpDdOpenT(2,this)">&#9660;</button></th>
              <th class="num" rowspan="2">Total</th><th class="num" rowspan="2">Med UPM%</th>
              <th class="num" colspan="2" style="background:#1a5276">HP (IB1/2, UPM&ge;thr)</th>
              <th class="num" colspan="2" style="background:#7d6608">LP (IB1-4, below thr)</th>
              <th class="num" colspan="2" style="background:#7b241c">Fail (IB&gt;4)</th>
              <th class="num" colspan="2" style="background:#1a7a4a">FF+DF (IB1-4)</th>
              <th class="num" colspan="2" style="background:#1e8449">FF (IB 1,2)</th>
              <th class="num" colspan="2" style="background:#117a65">DF (IB 3-4)</th>
              <th class="num" colspan="2" style="background:#7d3c98">ATOM DF (IB 3)</th>
              <th class="num" colspan="2" style="background:#922b21">CORE DF (IB 4)</th></tr>
            <tr>
              <th class="num" style="background:#1a5276">#</th><th class="num" style="background:#1a5276">% of IB1-4</th>
              <th class="num" style="background:#7d6608">#</th><th class="num" style="background:#7d6608">% of IB1-4</th>
              <th class="num" style="background:#7b241c">#</th><th class="num" style="background:#7b241c">% of total</th>
              <th class="num" style="background:#1a7a4a">#</th><th class="num" style="background:#1a7a4a">% of total</th>
              <th class="num" style="background:#1e8449">#</th><th class="num" style="background:#1e8449">% of IB1-4</th>
              <th class="num" style="background:#117a65">#</th><th class="num" style="background:#117a65">% of IB1-4</th>
              <th class="num" style="background:#7d3c98">#</th><th class="num" style="background:#7d3c98">% of IB1-4</th>
              <th class="num" style="background:#922b21">#</th><th class="num" style="background:#922b21">% of IB1-4</th>
            </tr>
          </thead><tbody id="dlcp-flt-row-t"></tbody><tbody id="dlcp-tb-t"></tbody></table></div></div>
          <div class="dlcp-note" id="dlcp-note-t"></div>
        </div>
        <div class="dlcp-splitter" id="dlcp-splitter-t" onclick="dlcpSplitterToggleT()">
          <span class="dlcp-split-arrow" id="dlcp-split-arrow-t">&#9654;</span>
        </div>
        <div class="dlcp-cw" id="dlcp-right-pane-t">
          <div class="dlcp-panel-hdr" style="margin-bottom:4px">
            <div style="font-size:11px;color:#666;flex:1">CDF of UPM% &mdash; HP/LP (solid) | FF IB1,2 / DF IB3,4 (dashed) | red dashed = threshold</div>
            <button class="dlcp-pbtn" onclick="dlcpSavePngT()">&#128247; PNG</button>
          </div>
          <canvas id="dlcp-cv-t" style="display:block;width:100%;flex:1;border:1px solid #dde;border-radius:4px;min-height:180px"></canvas>
        </div>
      </div>
    </div>

<script>
// ═══════════════════════════════════════ DATA ═══════════════════════════════
const DATA = {data_js};
// Plot element IDs — static embeds use UUIDs; JS targets those same elements
const _TREND_EL   = '{_trend_id}'   || 'trend-chart';
const _PARETO_H_EL= '{_pareto_h_id}'|| 'pareto-h-chart';
const _PARETO_V_EL= '{_pareto_v_id}'|| 'pareto-v-chart';
const PASS_BINS  = new Set(DATA.pass_bins);
const FF_BINS    = new Set(DATA.ff_bins);
const FF_DF_BINS = new Set(DATA.ff_df_bins);
const PALETTE    = DATA.palette;

function ibinLabel(ib) {{
  const n = DATA.ibin_names[String(ib)];
  return n ? `iBin ${{ib}} \u2014 ${{n}}` : `iBin ${{ib}}`;
}}

// ═══════════════════════════════════════ TABS ══════════════════════════════
let _activeTab = 'trend';
let _firstLoad = true;  // skip redundant Plotly.react on initial load (static embed already shows chart)
const _PLOTLY_CFG     = {{displayModeBar:true, scrollZoom:true}};
const _PLOTLY_CFG_STD = {{displayModeBar:true}};
function resizeActiveChart() {{
  if (typeof Plotly === 'undefined') return;
  const ids = {{'trend':_TREND_EL,'pareto-h':_PARETO_H_EL,'pareto-v':_PARETO_V_EL}};
  const el = document.getElementById(ids[_activeTab]);
  if (el) Plotly.Plots.resize(el);
}}
window.addEventListener('resize', function() {{ resizeActiveChart(); if(_activeTab==='dlcp') requestAnimationFrame(_dlcpRenderCdfT); }});
// ResizeObserver: fires when user drags the chart-wrap handle
const _ro = new ResizeObserver(entries => {{
  for (const e of entries) {{
    const el = e.target;
    if (typeof Plotly !== 'undefined' && el.querySelector('.plotly')) Plotly.Plots.resize(el);
  }}
}});
document.querySelectorAll('.chart-wrap').forEach(el => _ro.observe(el));
function showTab(name, btn) {{
  document.querySelectorAll('#tab-content > div').forEach(d => d.style.display = 'none');
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  var tabEl = document.getElementById('tab-' + name);
  if (!tabEl) return;
  tabEl.style.display = 'flex';
  tabEl.style.flexDirection = 'column';
  // Use passed element (this) — avoids reliance on global event object (broken in Firefox)
  if (btn) btn.classList.add('active');
  _activeTab = name;
  resizeActiveChart();
  // Lazy render — wrapped in try/catch so errors never block tab switching
  try {{
    const runs = window._pendingRuns;
    if (runs && (name === 'pareto-h' || name === 'pareto-v') && !_paretoRendered) {{
      const pareto = buildParetoTraces(runs, 20);
      Plotly.react(_PARETO_H_EL, pareto.traces, pareto.layout, _PLOTLY_CFG_STD).then(() => {{
        document.getElementById(_PARETO_H_EL).on('plotly_click', function(d) {{
          const pt = d.points[0];
          const ibNum = parseInt((pt.x || '').toString().match(/\\d+/)?.[0]);
          if (!isNaN(ibNum)) showFbDrilldown(ibNum, window._lastFilteredRuns, 'pareto-h', window._lastFilteredRuns);
        }});
      }});
      const paretoV = buildParetoVertTraces(runs, 20);
      Plotly.react(_PARETO_V_EL, paretoV.traces, paretoV.layout, _PLOTLY_CFG_STD).then(() => {{
        document.getElementById(_PARETO_V_EL).on('plotly_click', function(d) {{
          const pt = d.points[0];
          const ibNum = parseInt((pt.x || '').toString().match(/\\d+/)?.[0]);
          if (!isNaN(ibNum)) showFbDrilldown(ibNum, window._lastFilteredRuns, 'pareto-v', window._lastFilteredRuns);
        }});
      }});
      updateParetoTable(paretoV.tableRows);
      _paretoRendered = true;
    }}
    if (runs && name === 'dlcp') updateDlcp(runs);
  }} catch(e) {{ console.warn('showTab lazy render error:', e); }}
}}

// ═══════════════════════════════════════ FILTER HELPERS ════════════════════
function selAll(sel)  {{ document.querySelectorAll(sel).forEach(c => c.checked = true);  }}
function selNone(sel) {{ document.querySelectorAll(sel).forEach(c => c.checked = false); }}
function toggleLotWafers(cb) {{
  const lot  = cb.value;
  const drop = document.getElementById('wdrop-' + lot);
  const checked = cb.checked;
  if (drop) {{
    drop.querySelectorAll('.wafer-cb').forEach(c => c.checked = checked);
    if (!checked) drop.style.display = 'none';
  }}
}}
function toggleLotGroup(cb) {{
  const prefix = cb.dataset.grp;
  const checked = cb.checked;
  // Toggle all lot-cb inside this group
  const grp = cb.closest('.lot-group');
  if (!grp) return;
  grp.querySelectorAll('.lot-cb').forEach(lotCb => {{
    lotCb.checked = checked;
    toggleLotWafers(lotCb);
  }});
}}
function toggleLotGroupDrop(id) {{
  const el = document.getElementById(id);
  if (!el) return;
  const open = el.style.display !== 'none';
  el.style.display = open ? 'none' : '';
  // flip arrow in the label
  const lbl = el.previousElementSibling && el.previousElementSibling.querySelector('.lot-group-lbl');
  if (lbl) lbl.innerHTML = lbl.innerHTML.replace(open ? '&#9660;' : '&#9654;', open ? '&#9654;' : '&#9660;');
}}
function toggleWaferDrop(span) {{
  const lot  = span.dataset.lot;
  const drop = document.getElementById('wdrop-' + lot);
  if (!drop) return;
  const open = drop.classList.contains('show');
  if (open) {{
    drop.classList.remove('show');
    span.innerHTML = '&#9654;';
  }} else {{
    drop.classList.add('show');
    span.innerHTML = '&#9660;';
  }}
}}

function toggleMaterialWafers(span) {{
  const lot = span.dataset.lot;
  const material = span.dataset.material;
  const drop = document.getElementById('mdrop-' + lot + '-' + material);
  if (!drop) return;
  const open = drop.classList.contains('show');
  if (open) {{
    drop.classList.remove('show');
    span.innerHTML = '&#9654;';
  }} else {{
    drop.classList.add('show');
    span.innerHTML = '&#9660;';
  }}
}}
function filterLots(query) {{
  const lowerQuery = query.toLowerCase();
  document.querySelectorAll('.lot-group').forEach(group => {{
    // Match against any lot-label or lot-group-lbl inside the group
    const labels = [...group.querySelectorAll('.lot-label, .lot-group-lbl')];
    const matches = labels.some(el => el.textContent.toLowerCase().includes(lowerQuery));
    group.style.display = matches ? '' : 'none';
  }});
}}
function lotWaferAll(checked) {{
  document.querySelectorAll('.lot-group').forEach(grp => {{
    if (grp.style.display === 'none') return;
    grp.querySelectorAll('.lot-cb').forEach(cb => {{
      cb.checked = checked;
      const drop = document.getElementById('wdrop-' + cb.value);
      if (drop) drop.querySelectorAll('.wafer-cb').forEach(c => c.checked = checked);
    }});
    const grpCb = grp.querySelector('.lot-grp-cb');
    if (grpCb) grpCb.checked = checked;
  }});
}}
function selFail() {{
  document.querySelectorAll('.ibin-cb').forEach(c => {{
    c.checked = (c.closest('.cb-lbl').dataset.fail === 'true');
  }});
}}

// ═══════════════════════════════════════ GROUPING ══════════════════════════
function getMondayOf(d) {{
  const day = d.getDay();
  const diff = (day === 0) ? -6 : 1 - day;
  const m = new Date(d); m.setDate(d.getDate() + diff);
  return m.toISOString().slice(0, 10);
}}
function getWorkWeek(dateStr) {{
  const d = new Date(dateStr + 'T00:00:00');
  const jan4 = new Date(d.getFullYear(), 0, 4);
  const startOfW1 = new Date(jan4);
  startOfW1.setDate(jan4.getDate() - ((jan4.getDay() + 6) % 7));
  const ww = Math.floor((d - startOfW1) / (7 * 24 * 3600 * 1000)) + 1;
  return 'WW' + String(ww).padStart(2, '0');
}}
function getRevisionKey(program) {{
  if (!program) return 'Unknown';
  let m = program.match(/[A-Z](\\d{{2}}[A-Z])/);
  if (m) return m[1];
  m = program.match(/(\\d{{2,3}}[A-Z])/);
  if (m) return m[1];
  return program.length > 6 ? program.slice(-6) : program;
}}
function revSortKey(rev) {{
  const m = rev.match(/^(\\d+)([A-Z])$/);
  if (m) return [parseInt(m[1]), m[2].charCodeAt(0)];
  return [9999, 0];
}}
function getPeriodKey(run, interval) {{
  if (interval === 'revision') return getRevisionKey(run.program);
  const dateStr = run.date || '';
  if (!dateStr) return 'Unknown';
  const d = new Date(dateStr + 'T00:00:00');
  if (isNaN(d)) return 'Unknown';
  if (interval === 'weekly')    return getWorkWeek(dateStr);
  if (interval === 'bi-weekly') {{
    const mon = getMondayOf(d);
    const ms  = new Date(mon + 'T00:00:00').getTime();
    const wk  = Math.floor(ms / (7 * 24 * 3600 * 1000));
    const bwk = Math.floor(wk / 2) * 2;
    const bwkDate = new Date(bwk * 7 * 24 * 3600 * 1000).toISOString().slice(0, 10);
    return getWorkWeek(bwkDate);
  }}
  if (interval === 'monthly')   return dateStr.slice(0, 7);
  return dateStr.slice(0, 10);
}}
function groupRuns(runs, interval) {{
  const g = {{}};
  for (const r of runs) {{
    const k = getPeriodKey(r, interval);
    (g[k] = g[k] || []).push(r);
  }}
  // Within each group, sort by (lot's last date, run date) so all runs for a
  // lot are contiguous and the latest-tested lot appears at the end
  for (const grpRuns of Object.values(g)) {{
    const lotLast = {{}};
    for (const r of grpRuns) {{
      if (r.date && (!lotLast[r.lot] || r.date > lotLast[r.lot])) lotLast[r.lot] = r.date;
    }}
    grpRuns.sort((a, b) => {{
      const la = lotLast[a.lot] || '', lb = lotLast[b.lot] || '';
      if (la !== lb) return la < lb ? -1 : 1;
      const da = a.date || '', db = b.date || '';
      return da < db ? -1 : da > db ? 1 : 0;
    }});
  }}
  const entries = Object.entries(g);
  if (interval === 'revision') {{
    entries.sort((a, b) => {{
      const ka = revSortKey(a[0]), kb = revSortKey(b[0]);
      return ka[0] !== kb[0] ? ka[0] - kb[0] : ka[1] - kb[1];
    }});
  }} else {{
    entries.sort((a, b) => a[0] < b[0] ? -1 : 1);
  }}
  return Object.fromEntries(entries);
}}

// ═══════════════════════════════════════ PER-RUN STATS ═════════════════════
function runStats(run) {{
  let total = run.total_dies || 0;
  if (!total) total = Object.values(run.bin_counts).reduce((s, v) => s + v, 0) || 1;
  const failIbins = {{}};
  let ff = 0, ffdf = 0;
  for (const [ibStr, cnt] of Object.entries(run.bin_counts)) {{
    const ib = parseInt(ibStr);
    if (!PASS_BINS.has(ib)) failIbins[ib] = cnt / total * 100;
    if (FF_BINS.has(ib))    ff   += cnt;
    if (FF_DF_BINS.has(ib)) ffdf += cnt;
  }}
  return {{ failIbins, ffYield: ff / total * 100, ffDfYield: ffdf / total * 100 }};
}}
function runLabel(r) {{
  const p = r.program.length > 18 ? r.program.slice(-18) : r.program;
  return `${{r.wafer || '?'}}-${{p}}`;
}}

// ═══════════════════════════════════════ AGGREGATE BY LOT ════════════════
// Merge multiple wafers belonging to the same (program, lot) into one run.
function aggregateByLot(runs) {{
  const map = new Map();
  for (const r of runs) {{
    const lot7 = (r.lot || '').substring(0, 7);
    const key = r.program + '\x00' + lot7 + '\x00' + (r.material || '');
    if (!map.has(key)) {{
      map.set(key, {{
        lot: lot7, wafer: '', sort_lot: ((r.sort_lot || r.lot) || '').substring(0, 7),
        material: r.material, program: r.program,
        date: r.date, total_dies: 0, bin_counts: {{}}, fb_counts: {{}}, fb_modules: {{}}, dies: [],
        _wafers: [], _sourceRuns: [], _n: 0,
      }});
    }}
    const agg = map.get(key);
    agg.total_dies += (r.total_dies || 0);
    agg._n++;
    agg._wafers.push(r.wafer || '?');
    agg._sourceRuns.push(r);
    if (r.date && (!agg.date || r.date > agg.date)) agg.date = r.date;
    for (const [ib, cnt] of Object.entries(r.bin_counts))
      agg.bin_counts[ib] = (agg.bin_counts[ib] || 0) + cnt;
    for (const [ib, fbMap] of Object.entries(r.fb_counts || {{}})) {{
      if (!agg.fb_counts[ib]) agg.fb_counts[ib] = {{}};
      for (const [fb, cnt] of Object.entries(fbMap))
        agg.fb_counts[ib][fb] = (agg.fb_counts[ib][fb] || 0) + cnt;
    }}
    for (const [ib, fbMap] of Object.entries(r.fb_modules || {{}})) {{
      if (!agg.fb_modules[ib]) agg.fb_modules[ib] = {{}};
      Object.assign(agg.fb_modules[ib], fbMap);
    }}
    if (r.dies && r.dies.length) agg.dies.push(...r.dies);
  }}
  return [...map.values()].map(agg => ({{
    ...agg,
    wafer: agg._wafers.length === 1 ? agg._wafers[0] : `${{agg._n}}W`,
  }}));
}}

// ═══════════════════════════════════════ BUILD TREND CHART ═════════════════
function buildTrendTraces(groups, topN, thresh, groupMode) {{
  const flat = [];  // {{period, run, stats}}
  const globalFail = {{}};
  for (const [period, runs] of Object.entries(groups)) {{
    for (const run of runs) {{
      const stats = runStats(run);
      flat.push({{ period, run, stats }});
      for (const [ib, pct] of Object.entries(stats.failIbins))
        globalFail[ib] = (globalFail[ib] || 0) + pct;
    }}
  }}
  if (!flat.length) return {{ traces: [], layout: {{}}, flat }};

  const topIbins = Object.entries(globalFail)
    .filter(([, v]) => v >= thresh)
    .sort((a, b) => b[1] - a[1]).slice(0, topN)
    .map(([ib]) => parseInt(ib));

  const xPos    = flat.map((_, i) => i);
  const xLabels = flat.map(({{ run }}) => {{
    const base = run.sort_lot || run.lot;
    const wfr  = groupMode === 'wafer' ? ` W${{run.wafer || '?'}}` : '';
    const mat  = run.material ? `(${{run.material}})` : '';
    return (base + wfr + (mat ? `\n${{mat}}` : ''));
  }});
  const traces  = [];

  topIbins.forEach((ib, bi) => {{
    const y     = flat.map(({{ stats }}) => stats.failIbins[ib] || 0);
    const hover = flat.map(({{ period, run, stats }}) => {{
      const pct = (stats.failIbins[ib] || 0).toFixed(2);
      const totalDies = run.total_dies || Object.values(run.bin_counts || {{}}).reduce((s,v)=>s+v,0) || 1;
      const nFail = Math.round((pct / 100) * totalDies);
      return `<b>${{runLabel(run)}}</b><br>Period: ${{period}}<br>Lot: ${{run.lot}}(${{run.material || '?'}})&nbsp;&nbsp;Wafer: ${{run.wafer}}<br>Program: ${{run.program}}<br>Date: ${{run.date}}<br>\u2500\u2500\u2500\u2500<br><b>${{ibinLabel(ib)}}</b><br>Fail: <b>${{nFail}} (${{pct}}%)</b>`;
    }});
    traces.push({{ type:'bar', x:xPos, y, name:ibinLabel(ib),
      hovertext:hover, hoverinfo:'text',
      marker:{{color:PALETTE[bi%PALETTE.length],line:{{color:'white',width:0.4}}}},
      opacity:0.85, yaxis:'y' }});
  }});

  const ffY    = flat.map(({{ stats }}) => stats.ffYield);
  const ffdfY  = flat.map(({{ stats }}) => stats.ffDfYield);
  const ffTgt  = DATA.yield_target.ff   ?? null;
  const ffdfTgt= DATA.yield_target.ff_df ?? null;

  const ffName   = DATA.ff_name   || 'SDS FF';
  const ffdfName = DATA.ff_df_name || 'SDS FF+DF';

  // Build IB/FB breakdown for hover - filter to specific IBs
  const buildIbFbBreakdown = (run, includeIBs) => {{
    const breakdown = [];
    const fbCounts = run.fb_counts || {{}};
    const totalDies = run.total_dies || Object.values(run.bin_counts || {{}}).reduce((s,v)=>s+v,0) || 1;
    
    // Filter IBs: sort and include only those in includeIBs array
    const sortedIBs = Object.keys(fbCounts).map(Number).sort((a,b)=>a-b);
    for (const ib of sortedIBs) {{
      if (includeIBs && !includeIBs.includes(ib)) continue;  // Skip if not in filter list
      const fbData = fbCounts[ib] || {{}};
      const ibCount = Object.values(fbData).reduce((s,v)=>s+v,0);
      const ibPct = ((ibCount / totalDies) * 100).toFixed(2);
      
      const fbList = [];
      const sortedFBs = Object.keys(fbData).map(Number).sort((a,b)=>a-b);
      for (const fb of sortedFBs) {{
        const fbCnt = fbData[fb] || 0;
        const fbPct = ((fbCnt / totalDies) * 100).toFixed(2);
        fbList.push(`FB${{fb}}: ${{fbCnt}}(${{fbPct}}%)`);
      }}
      breakdown.push(`IB${{ib}}: ${{ibCount}}(${{ibPct}}%)<br>&nbsp;&nbsp;${{fbList.join(', ')}}`);
    }}
    
    // Add summary for specific FBs
    const targetFBs = [126, 226, 326, 426];
    const fbSummary = [];
    for (const targetFB of targetFBs) {{
      let fbTotal = 0;
      for (const fbData of Object.values(fbCounts)) {{
        fbTotal += fbData[targetFB] || 0;
      }}
      if (fbTotal > 0) {{
        const fbPct = ((fbTotal / totalDies) * 100).toFixed(2);
        fbSummary.push(`FB${{targetFB}}: ${{fbTotal}}(${{fbPct}}%)`);
      }}
    }}
    
    if (breakdown.length) {{
      let result = '<br>\u2500\u2500\u2500\u2500<br>' + breakdown.join('<br>');
      if (fbSummary.length) result += '<br>\u2500\u2500\u2500\u2500<br>' + fbSummary.join(', ');
      return result;
    }}
    return '';
  }};

  traces.push({{ type:'scatter', x:xPos, y:ffY,
    mode:'lines+markers+text', name:ffName,
    line:{{color:'#1a73e8',width:2.5}}, marker:{{size:8}},
    text:ffY.map(v => v.toFixed(1)+'%'), textposition:'top center',
    textfont:{{size:9,color:'#1a73e8'}},
    hovertext:flat.map(({{ run, stats }}) => `<b>${{run.sort_lot || runLabel(run)}}</b><br>${{ffName}}: <b>${{stats.ffYield.toFixed(2)}}%</b>${{buildIbFbBreakdown(run, [1, 2])}}`),
    hoverinfo:'text', legendgroup:'yield_lines', yaxis:'y2' }});

  traces.push({{ type:'scatter', x:xPos, y:ffdfY,
    mode:'lines+markers+text', name:ffdfName,
    line:{{color:'#2e7d32',width:2.5,dash:'dash'}}, marker:{{size:8,symbol:'square'}},
    text:ffdfY.map(v => v.toFixed(1)+'%'), textposition:'bottom center',
    textfont:{{size:9,color:'#2e7d32'}},
    hovertext:flat.map(({{ run, stats }}) => `<b>${{run.sort_lot || runLabel(run)}}</b><br>${{ffdfName}}: <b>${{stats.ffDfYield.toFixed(2)}}%</b>${{buildIbFbBreakdown(run, [1, 2, 3, 4])}}`),
    hoverinfo:'text', legendgroup:'yield_lines', yaxis:'y2' }});

  // Period dividers
  const shapes = [], annots = [];
  let idx = 0;
  for (const [period, runs] of Object.entries(groups)) {{
    const start = idx, end = idx + runs.length;
    const mid   = (start + end - 1) / 2;
    if (start > 0)
      shapes.push({{ type:'line', x0:start-0.5, x1:start-0.5, y0:0, y1:1,
        yref:'paper', line:{{color:'#95a5a6',width:1.2,dash:'dot'}} }});
    annots.push({{ x:mid, y:1.06, xref:'x', yref:'paper', text:`<b>${{period}}</b>`,
      showarrow:false, font:{{size:11,color:'#2c3e50'}}, xanchor:'center' }});
    idx = end;
  }}

  // Target lines + annotations
  const hlines = [], tgtAnnots = [];
  if (ffTgt != null) {{
    hlines.push({{ type:'line', x0:0, x1:1, xref:'paper',
      y0:ffTgt, y1:ffTgt, yref:'y2',
      line:{{color:'#1a73e8',width:2.5,dash:'dot'}}, opacity:0.85 }});
    tgtAnnots.push({{ x:1, xref:'paper', y:ffTgt, yref:'y2',
      text:`${{ffName}} target ${{ffTgt.toFixed(1)}}%`,
      showarrow:false, xanchor:'left', font:{{size:10,color:'#1a73e8'}},
      bgcolor:'rgba(255,255,255,0.7)' }});
  }}
  if (ffdfTgt != null) {{
    hlines.push({{ type:'line', x0:0, x1:1, xref:'paper',
      y0:ffdfTgt, y1:ffdfTgt, yref:'y2',
      line:{{color:'#2e7d32',width:2.5,dash:'dot'}}, opacity:0.85 }});
    tgtAnnots.push({{ x:1, xref:'paper', y:ffdfTgt, yref:'y2',
      text:`${{ffdfName}} target ${{ffdfTgt.toFixed(1)}}%`,
      showarrow:false, xanchor:'left', font:{{size:10,color:'#2e7d32'}},
      bgcolor:'rgba(255,255,255,0.7)' }});
  }}

  const maxStack = flat.reduce((mx, {{ stats }}) => {{
    const s = topIbins.reduce((t, ib) => t + (stats.failIbins[ib]||0), 0);
    return Math.max(mx, s);
  }}, 0);
  const failYlim = Math.min(100, Math.max(maxStack * 1.25, 5));

  const layout = {{
    barmode:'stack', plot_bgcolor:'#f9f9fb', paper_bgcolor:'white',
    title: {{ text: `${{DATA.chart_name ? '<b>' + DATA.chart_name + '</b> \u2014 ' : ''}}Interface Bin Fail vs. Yield Trend`, font:{{size:16}}, y:0.97, yanchor:'top' }},
    xaxis: {{ tickvals:xPos, ticktext:xLabels, tickfont:{{size:11}},
      tickangle:-45, showgrid:false, title:'SORT LOT',
      automargin:true }},
    yaxis: {{ title:'Interface Bin Fail (%)', range:[0,failYlim],
      gridcolor:'#e8e8e8', zeroline:true, zerolinecolor:'#ccc' }},
    yaxis2: {{ title:'Yield (%)', range:[0,105], overlaying:'y', side:'right',
      showgrid:false }},
    legend: {{ orientation:'v', x:1.01, y:0.0, xanchor:'left', yanchor:'bottom',
      bgcolor:'rgba(255,255,255,0.85)', bordercolor:'#ddd', borderwidth:1 }},
    shapes: [...shapes, ...hlines], annotations: [...annots, ...tgtAnnots],
    margin: {{ l:60, r:180, t:100, b:220 }},
    hovermode:'closest', autosize:true,
  }};

  return {{ traces, layout, flat }};
}}

// ═══════════════════════════════════════ BUILD PARETO (HORIZONTAL) ═══════
function buildParetoTraces(runs, topN) {{
  const totals = {{}};
  const n = runs.length || 1;
  for (const run of runs) {{
    for (const [ib, pct] of Object.entries(runStats(run).failIbins))
      totals[ib] = (totals[ib] || 0) + pct;
  }}
  const sorted = Object.entries(totals)
    .map(([ib, t]) => ({{ ib:parseInt(ib), avg:t/n }}))
    .sort((a, b) => b.avg - a.avg).slice(0, topN);

  const x = sorted.map(e => ibinLabel(e.ib));
  const y = sorted.map(e => e.avg);
  return {{
    traces: [{{ type:'bar', x, y,
      marker:{{color:sorted.map((_, i) => PALETTE[i%PALETTE.length])}},
      hovertemplate:'%{{x}}<br>Avg Fail: %{{y:.2f}}%<extra></extra>',
      name:'Avg Fail %' }}],
    layout: {{
      plot_bgcolor:'#f9f9fb', paper_bgcolor:'white',
      title: {{ text: (DATA.chart_name ? '<b>' + DATA.chart_name + '</b> \u2014 ' : '') + 'Overall Interface Bin Fail Pareto', font:{{size:16}} }},
      xaxis:{{tickangle:-45, tickfont:{{size:11}}, automargin:true}},
      yaxis:{{title:'Avg Fail (%)', gridcolor:'#e8e8e8'}},
      margin:{{l:60,r:40,t:60,b:180}}, showlegend:false, autosize:true,
    }},
  }};
}}

// ═══════════════════════════════════════ BUILD PARETO (VERTICAL / BY BIN) ═
function buildParetoVertTraces(runs, topN) {{
  const totals = {{}};
  const n = runs.length || 1;
  for (const run of runs) {{
    for (const [ib, pct] of Object.entries(runStats(run).failIbins))
      totals[ib] = (totals[ib] || 0) + pct;
  }}
  const sorted = Object.entries(totals)
    .map(([ib, t]) => ({{ ib:parseInt(ib), avg:t/n }}))
    .sort((a, b) => b.avg - a.avg).slice(0, topN);

  if (!sorted.length) return {{ traces:[], layout:{{}}, tableRows:[] }};

  const totalAvg = sorted.reduce((s, e) => s + e.avg, 0) || 1;
  const x = sorted.map(e => ibinLabel(e.ib));
  const y = sorted.map(e => e.avg);
  const cum = [];
  let running = 0;
  for (const v of y) {{ running += v / totalAvg * 100; cum.push(running); }}

  const binMap = DATA.bin_map || {{}};
  const tableRows = sorted.map(e => {{
    const info = binMap[String(e.ib)] || {{}};
    const nFail = runs.reduce((s, r) => s + ((r.bin_counts || {{}})[String(e.ib)] || 0), 0);
    return {{ ib: e.ib, cat: info.cat || '', desc: info.desc || '', nFail, pct: e.avg }};
  }});

  const maxY = y[0] || 1;
  return {{
    traces: [
      {{ type:'bar', x, y,
        name:'Avg Fail (%)',
        marker:{{color:sorted.map((_, i) => PALETTE[i%PALETTE.length]),
                line:{{color:'#1a252f',width:0.8}}}},
        opacity:0.9,
        text:y.map(v => v.toFixed(2)+'%'), textposition:'outside',
        textfont:{{size:10,color:'#333'}},
        hovertemplate:'%{{x}}<br>Avg Fail: %{{y:.2f}}%<extra></extra>' }},
      {{ type:'scatter', x, y:cum,
        name:'Cumulative %', yaxis:'y2',
        mode:'lines+markers',
        line:{{color:'#e67e22',width:2.5}},
        marker:{{size:7,color:'#e67e22'}},
        hovertemplate:'%{{x}}<br>Cumulative: %{{y:.1f}}%<extra></extra>' }},
    ],
    layout: {{
      plot_bgcolor:'#f9f9fb', paper_bgcolor:'white',
      title: {{ text: (DATA.chart_name ? '<b>' + DATA.chart_name + '</b> \u2014 ' : '') + '<b>Fail Pareto Chart (Percentage)</b>', font:{{size:16}} }},
      xaxis:{{tickangle:-45, tickfont:{{size:10}}, automargin:true}},
      yaxis:{{title:'Fail (%)', gridcolor:'#e8e8e8', range:[0,maxY*1.2], ticksuffix:'%'}},
      yaxis2:{{title:'Cumulative (%)', overlaying:'y', side:'right',
               range:[0,105], showgrid:false, ticksuffix:'%'}},
      legend:{{x:1.08,y:1.0,bgcolor:'rgba(255,255,255,0.85)',bordercolor:'#ddd',borderwidth:1}},
      margin:{{l:70,r:120,t:60,b:180}}, autosize:true,
    }},
    tableRows,
  }};
}}

// ═══════════════════════════════════════ UPDATE PARETO TABLE ══════════════
function updateParetoTable(tableRows) {{
  const saved = loadComments();
  const wrapper = document.querySelector('#tab-pareto-v .chart-card');
  if (!wrapper) return;
  const oldTbl = wrapper.querySelector('.pareto-tbl');
  if (oldTbl) oldTbl.remove();
  if (!tableRows || !tableRows.length) return;

  const filteredRuns = window._lastFilteredRuns || [];
  const grandTotal = filteredRuns.reduce((s, r) => s + (r.total_dies || Object.values(r.bin_counts || {{}}).reduce((a,v)=>a+v,0) || 0), 0) || 1;

  let html = '<table class="pareto-tbl" id="pareto-summary-tbl"><thead><tr>'
    + '<th>Interface Bin</th><th>Description</th>'
    + '<th class="num">Total Tested</th><th class="num">Fail Count</th>'
    + '<th class="num">Fail %</th><th>Comment</th></tr></thead><tbody>';

  for (const r of tableRows) {{
    const ibStr = String(r.ib);
    const desc = (r.cat && r.desc && r.cat !== r.desc) ? r.cat + ' \u2014 ' + r.desc : (r.cat || r.desc || '');
    const pct = r.pct.toFixed(2);
    const savedCmt = (saved[ibStr] || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    html += `<tr><td>${{r.ib}}</td><td>${{desc}}</td>`
          + `<td class="num">${{grandTotal.toLocaleString()}}</td>`
          + `<td class="num">${{r.nFail.toLocaleString()}}</td><td class="num">${{pct}}%</td>`
          + `<td><textarea class="pareto-comment" data-ib="${{ibStr}}" rows="1" placeholder="Add comment...">${{savedCmt}}</textarea></td></tr>`;
  }}

  html += '</tbody></table>';
  wrapper.insertAdjacentHTML('beforeend', html);
  initParetoComments();
  const tbl = wrapper.querySelector('.pareto-tbl');
  if (tbl) resizableCols(tbl);
}}

// ═══════════════════════════════════════ TABLE + STATS ═════════════════════
function updateTable(flat) {{
  const tbody = document.getElementById('run-table-body');
  if (!flat || !flat.length) {{ tbody.innerHTML = ''; return; }}
  // Build all rows at once (single innerHTML = much faster than 579 insertAdjacentHTML)
  const rows = [];
  for (const {{ period, run, stats }} of flat) {{
    const top5 = Object.entries(stats.failIbins)
      .sort((a, b) => b[1] - a[1]).slice(0, 5)
      .map(([ib, p]) => `iBin ${{ib}}: ${{parseFloat(p).toFixed(1)}}%`).join(' | ');
    const ffCls = stats.ffYield >= 80 ? 'yld-ok' : (stats.ffYield >= 50 ? 'yld-mid' : 'yld-low');
    rows.push(`<tr><td>${{period}}</td><td>${{run.date}}</td><td>${{run.lot}}</td><td>${{run.wafer}}</td>`
      + `<td>${{run.program}}</td>`
      + `<td class="${{ffCls}}" style="text-align:right">${{stats.ffYield.toFixed(1)}}%</td>`
      + `<td style="text-align:right">${{stats.ffDfYield.toFixed(1)}}%</td>`
      + `<td style="font-size:11px;color:#555">${{top5}}</td></tr>`);
  }}
  tbody.innerHTML = rows.join('');
  const runTbl = document.getElementById('run-table');
  if (runTbl) resizableCols(runTbl);
}}
function updateStats(runs, flat) {{
  const f = flat || [];
  const progs = new Set(runs.map(r => r.program));
  const n = f.length || 1;
  const avgFF   = f.reduce((s, e) => s + e.stats.ffYield,   0) / n;
  const avgFFDF = f.reduce((s, e) => s + e.stats.ffDfYield, 0) / n;
  const ffLbl   = DATA.ff_name   || 'SDS FF';
  const ffdfLbl = DATA.ff_df_name || 'SDS FF+DF';
  document.getElementById('stats-bar').innerHTML =
    `<div class="stat-chip">Runs: <b>${{flat.length}}</b></div>
     <div class="stat-chip">Programs: <b>${{progs.size}}</b></div>
     <div class="stat-chip">Avg ${{ffLbl}}: <b>${{avgFF.toFixed(1)}}%</b></div>
     <div class="stat-chip">Avg ${{ffdfLbl}}: <b>${{avgFFDF.toFixed(1)}}%</b></div>`;
}}

// ═══════════════════════════════════════ MAIN REBUILD ══════════════════════
function rebuildCharts() {{
  const interval = document.querySelector('input[name="interval"]:checked')?.value || 'revision';
  const topN   = parseInt(document.getElementById('topn-input').value)   || 8;
  const thresh = parseFloat(document.getElementById('thresh-input').value) || 0;

  const selProgs  = new Set([...document.querySelectorAll('.prog-cb:checked')].map(c => c.value));
  const selIbins  = new Set([...document.querySelectorAll('.ibin-cb:checked')].map(c => parseInt(c.value)));
  const selLots   = new Set([...document.querySelectorAll('.lot-cb:checked')].map(c => c.value));
  const selWafers = new Set([...document.querySelectorAll('.wafer-cb:checked')].map(c => c.value));
  const dateMode  = document.querySelector('input[name="datemode"]:checked')?.value || 'all';
  const dateFrom  = document.getElementById('date-from').value;  // YYYY-MM-DD or ''
  const dateTo    = document.getElementById('date-to').value;
  function dateInRange(d) {{
    if (!d) return true;
    if (dateMode === 'all') return true;
    const wks = dateMode === '4w' ? 4 : dateMode === '6w' ? 6 : dateMode === '12w' ? 12 : 0;
    if (wks) {{
      const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - wks * 7);
      return new Date(d + 'T00:00:00') >= cutoff;
    }}
    if (dateMode === 'custom') {{
      if (dateFrom && d < dateFrom) return false;
      if (dateTo   && d > dateTo)   return false;
      return true;
    }}
    return true;
  }}

  const filteredRuns = DATA.runs
    .filter(r => dateInRange(r.date))
    .filter(r => selProgs.has(r.program))
    .filter(r => selLots.has(r.lot))
    .filter(r => selWafers.has(r.lot + '::' + (r.wafer || '')))
    .map(r => ({{
      ...r,
      bin_counts: Object.fromEntries(
        Object.entries(r.bin_counts).filter(([ib]) => selIbins.has(parseInt(ib)))
      ),
    }}));

  // Store globally so click handlers can access filtered runs
  window._lastFilteredRuns = filteredRuns;

  const groupMode = document.querySelector('input[name="groupby"]:checked')?.value || 'lot';
  const runsForChart = groupMode === 'lot' ? aggregateByLot(filteredRuns) : filteredRuns;
  const groups = groupRuns(runsForChart, interval);
  const {{ traces, layout, flat }} = buildTrendTraces(groups, topN, thresh, groupMode);
  window._lastFlat = flat;

  // Guard all Plotly calls — if CDN fails, tables/DLCP/tabs still work
  if (typeof Plotly !== 'undefined') {{
    if (!_firstLoad) {{
      Plotly.react(_TREND_EL, traces, layout, _PLOTLY_CFG).then(() => {{
      document.getElementById(_TREND_EL).on('plotly_click', function(d) {{
        const pt = d.points[0];
        if (pt.data.type === 'bar') {{
          const ibNum = parseInt((pt.data.name || '').match(/\\d+/)?.[0]);
          if (!isNaN(ibNum)) {{
            const entry = (window._lastFlat || [])[pt.pointIndex];
            const barRuns = entry ? (entry.run._sourceRuns || [entry.run]) : window._lastFilteredRuns;
            showFbDrilldown(ibNum, barRuns, 'trend', window._lastFilteredRuns);
          }}
        }} else if (pt.data.type === 'scatter' && pt.data.hoverinfo === 'text') {{
          const htArr = pt.data.hovertext;
          const hoverText = Array.isArray(htArr) ? (htArr[pt.pointIndex] || '') : (htArr || '');
          const ex = d.event ? d.event.pageX : (pt.xaxis ? pt.xaxis._offset : 200);
          const ey = d.event ? d.event.pageY : 200;
          if (hoverText) showStickyTooltip(hoverText, ex, ey);
        }}
      }});
    }});
  }} else {{
    // Plotly not loaded — show inline warning in the trend chart area
    var tc = document.getElementById('trend-chart');
    if (tc) tc.innerHTML = '<div style="padding:20px;color:#e74c3c;font-family:Arial;font-size:13px">&#9888; Plotly chart library failed to load.<br>Check shared/library/ path.</div>';
  }}

  const pareto = buildParetoTraces(filteredRuns, 20);
  // Lazy: only render pareto/DLCP charts if their tab is currently visible
  if ((typeof Plotly !== 'undefined') && !_firstLoad && (_activeTab === 'pareto-h' || _activeTab === 'pareto-v' || !_paretoRendered)) {{
    Plotly.react(_PARETO_H_EL, pareto.traces, pareto.layout, _PLOTLY_CFG_STD).then(() => {{
      document.getElementById(_PARETO_H_EL).on('plotly_click', function(d) {{
        const pt = d.points[0];
        const ibNum = parseInt((pt.x || '').toString().match(/\\d+/)?.[0]);
        if (!isNaN(ibNum)) showFbDrilldown(ibNum, window._lastFilteredRuns, 'pareto-h', window._lastFilteredRuns);
      }});
    }});
    const paretoV = buildParetoVertTraces(filteredRuns, 20);
    Plotly.react(_PARETO_V_EL, paretoV.traces, paretoV.layout, _PLOTLY_CFG_STD).then(() => {{
      document.getElementById(_PARETO_V_EL).on('plotly_click', function(d) {{
        const pt = d.points[0];
        const ibNum = parseInt((pt.x || '').toString().match(/\\d+/)?.[0]);
        if (!isNaN(ibNum)) showFbDrilldown(ibNum, window._lastFilteredRuns, 'pareto-v', window._lastFilteredRuns);
      }});
    }});
    updateParetoTable(paretoV.tableRows);
    if (_activeTab === 'pareto-h' || _activeTab === 'pareto-v') _paretoRendered = true;
  }}
  if (_activeTab === 'dlcp') updateDlcp(filteredRuns);

  // On first load: skip heavy DOM/Plotly ops — static embed already shows chart.
  // Only update stats (fast). Table + pareto charts render lazily on user action.
  if (_firstLoad) {{
    updateStats(filteredRuns, flat);
    window._lastFilteredRuns = filteredRuns;
    window._lastFlat = flat;
    window._pendingRuns = filteredRuns;
    _firstLoad = false;
    return;
  }}

  // Always update table/stats (cheap DOM ops)
  updateTable(flat);
  updateStats(filteredRuns, flat);
  // Stash filtered runs so lazy tabs can render on first show
  window._pendingRuns = filteredRuns;
  _firstLoad = false;
}}

// ═══════════════════════════════════════ STICKY TOOLTIP (FF/FF+DF HOVER) ═
function showStickyTooltip(text, x, y) {{
  if (!text) return;
  
  // Remove existing tooltip if any
  const existing = document.getElementById('sticky-tooltip');
  if (existing) existing.remove();
  
  const tooltip = document.createElement('div');
  tooltip.id = 'sticky-tooltip';
  tooltip.style.position = 'fixed';
  tooltip.style.top = (y + 10) + 'px';
  tooltip.style.left = (x + 10) + 'px';
  tooltip.style.background = '#fff';
  tooltip.style.border = '2px solid #2c3e50';
  tooltip.style.borderRadius = '6px';
  tooltip.style.padding = '12px';
  tooltip.style.maxWidth = '400px';
  tooltip.style.maxHeight = '300px';
  tooltip.style.overflowY = 'auto';
  tooltip.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
  tooltip.style.zIndex = '10000';
  tooltip.style.fontSize = '12px';
  tooltip.style.fontFamily = 'monospace';
  tooltip.style.whiteSpace = 'pre-wrap';
  tooltip.style.wordWrap = 'break-word';
  
  tooltip.innerHTML = (text || '').replace(/\\n/g, '<br>');
  
  // Add close button
  const closeBtn = document.createElement('button');
  closeBtn.innerHTML = '✕';
  closeBtn.style.position = 'absolute';
  closeBtn.style.top = '4px';
  closeBtn.style.right = '4px';
  closeBtn.style.background = '#e74c3c';
  closeBtn.style.color = 'white';
  closeBtn.style.border = 'none';
  closeBtn.style.borderRadius = '50%';
  closeBtn.style.width = '24px';
  closeBtn.style.height = '24px';
  closeBtn.style.cursor = 'pointer';
  closeBtn.style.fontSize = '14px';
  closeBtn.style.padding = '0';
  closeBtn.onclick = () => tooltip.remove();
  tooltip.appendChild(closeBtn);
  
  document.body.appendChild(tooltip);
  
  // Close on escape key
  const closeOnEsc = (e) => {{
    if (e.key === 'Escape') {{
      tooltip.remove();
      document.removeEventListener('keydown', closeOnEsc);
    }}
  }};
  document.addEventListener('keydown', closeOnEsc);
  
  // Close when clicking outside
  setTimeout(() => {{
    document.addEventListener('click', (e) => {{
      if (e.target !== tooltip && !tooltip.contains(e.target)) {{
        tooltip.remove();
      }}
    }}, {{ once: true }});
  }}, 0);
}}

// ═══════════════════════════════════════ FB DRILLDOWN TABLE ═
// ═══════════════════════════════════════ COLUMN RESIZE ═══════════════════
function resizableCols(table) {{
  const ths = table.querySelectorAll('thead th');
  ths.forEach(th => {{
    th.classList.add('resizable');
    const hdl = document.createElement('div');
    hdl.className = 'col-resizer';
    th.appendChild(hdl);
    let startX, startW;
    hdl.addEventListener('mousedown', e => {{
      startX = e.pageX;
      startW = th.offsetWidth;
      const onMove = ev => {{ th.style.minWidth = Math.max(40, startW + ev.pageX - startX) + 'px'; }};
      const onUp   = () => {{ document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); }};
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
      e.preventDefault();
    }});
  }});
}}
function resizeAllTables() {{
  document.querySelectorAll('table').forEach(t => resizableCols(t));
}}

function showFbDrilldown(ibNum, runs, tabPrefix, selectedRuns) {{
  const ibStr   = String(ibNum);
  const binMap  = DATA.bin_map || {{}};
  const ibInfo  = binMap[ibStr] || {{}};

  // Calculate total dies from selected wafers (sidebar selections)
  let barTotal = 0;
  const selectedList = selectedRuns || runs;
  for (const run of selectedList) {{
    barTotal += run.total_dies || Object.values(run.bin_counts).reduce((s,v)=>s+v,0) || 1;
  }}
  
  // Aggregate fb_counts from clicked bar runs, but use barTotal from selected wafers
  const fbTotals = {{}};    // fb -> {{cnt, lotWafers: Map<lot, Set<wafer>>}}
  const fbModules = {{}};   // fb -> {{bdesc -> count}}
  for (const run of runs) {{
    const lot = run.sort_lot || run.lot || '';
    const wafer = run.wafer || '';
    const ibFb = (run.fb_counts || {{}})[ibStr] || {{}};
    for (const [fb, cnt] of Object.entries(ibFb)) {{
      if (!fbTotals[fb]) fbTotals[fb] = {{cnt: 0, lotWafers: new Map()}};
      fbTotals[fb].cnt += cnt;
      if (lot) {{
        if (!fbTotals[fb].lotWafers.has(lot)) fbTotals[fb].lotWafers.set(lot, new Set());
        if (wafer) fbTotals[fb].lotWafers.get(lot).add(wafer);
      }}
    }}
    const ibMod = (run.fb_modules || {{}})[ibStr] || {{}};
    for (const [fb, bdesc] of Object.entries(ibMod)) {{
      if (!fbModules[fb]) fbModules[fb] = {{}};
      fbModules[fb][bdesc] = (fbModules[fb][bdesc] || 0) + 1;
    }}
  }}

  // Return sorted list of unique bin description strings for an FB (by frequency)
  function _fbBdescs(fb) {{
    const bmap = fbModules[String(fb)];
    if (!bmap) return [];
    return Object.entries(bmap).sort((a,b)=>b[1]-a[1]).map(e=>e[0]);
  }}

  // Build FB description from fb_map (Pass-Bin-Map + fB93xx) with bin_map fallback
  function fbDesc(fb) {{
    const fbInfo = (DATA.fb_map || {{}})[String(fb)];
    if (fbInfo) {{
      const cat  = fbInfo.cat  || '';
      const desc = fbInfo.desc || '';
      if (cat && desc && cat !== desc) return `${{cat}} \u2014 ${{desc}}`;
      return cat || desc;
    }}
    // Fallback: try bin_map (interface-bin level)
    const ibInfo = (DATA.bin_map || {{}})[String(fb)] || {{}};
    const cat  = ibInfo.cat  || '';
    const desc = ibInfo.desc || ibInfo.description || '';
    if (cat && desc && cat !== desc) return `${{cat}} \u2014 ${{desc}}`;
    if (cat || desc) return cat || desc;
    // Last fallback: use the raw bin description string from the CSV
    const bdescs = _fbBdescs(fb);
    return bdescs.length > 0 ? bdescs[0] : '';
  }}

  // Sort by fail count descending
  const rows = Object.entries(fbTotals)
    .map(([fb, d]) => {{
      const lotWaferStr = [...d.lotWafers.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([lot, wafers]) => wafers.size ? `${{lot}}(${{[...wafers].sort().join(',')}})` : lot)
        .join(', ');
      return {{fb: parseInt(fb), cnt: d.cnt, lotWaferStr}};
    }})
    .sort((a, b) => b.cnt - a.cnt);

  const hasFbData = rows.length > 0;
  const ibLabel = ibinLabel(ibNum) || `IB ${{ibNum}}`;
  const ibCat  = ibInfo.cat  || '';
  const ibDsc  = ibInfo.desc || ibInfo.description || '';
  const ibDescTxt = (ibCat && ibDsc && ibCat !== ibDsc) ? ` \u2014 ${{ibCat}} / ${{ibDsc}}`
                  : (ibCat || ibDsc) ? ` \u2014 ${{ibCat || ibDsc}}` : '';

  let html = '';
  if (!hasFbData) {{
    html = `<tr><td colspan="8" style="text-align:center;color:#888;padding:12px">No functional bin data available for IB ${{ibNum}}</td></tr>`;
  }} else {{
    for (const {{lotWaferStr, fb, cnt}} of rows) {{
      const pct = barTotal > 0 ? (cnt / barTotal * 100).toFixed(2) : '—';
      const desc = fbDesc(fb) || ibInfo.desc || '';
      const mods = _fbBdescs(fb);
      let modCell = '';
      if (mods.length > 0) {{
        const first = mods[0];
        const disp = first.length > 45 ? first.substring(0, 43) + '..' : first;
        const tip = mods.join('&#10;').replace(/"/g, '&quot;');
        modCell = `<span title="${{tip}}">${{disp}}</span>` + (mods.length > 1 ? ` <span style="color:#aaa;font-size:10px">(+${{mods.length-1}} more)</span>` : '');
      }}
      html += `<tr>
        <td>${{ibNum}}</td>
        <td>${{lotWaferStr || '—'}}</td>
        <td>FB${{fb}}</td>
        <td>${{desc}}</td>
        <td>${{modCell}}</td>
        <td class="num">${{barTotal}}</td>
        <td class="num">${{cnt.toLocaleString()}}</td>
        <td class="num">${{pct}}%</td>
      </tr>`;
    }}
  }}

  document.getElementById(`${{tabPrefix}}-fb-ib`).textContent = `${{ibNum}}${{ibDescTxt}}`;
  document.getElementById(`${{tabPrefix}}-fb-tbody`).innerHTML = html;
  const drillCard = document.getElementById(`${{tabPrefix}}-fb-drilldown`);
  drillCard.style.display = '';
  drillCard.scrollIntoView({{behavior:'smooth', block:'nearest'}});
  const tbl = document.getElementById(`${{tabPrefix}}-fb-tbl`);
  if (tbl) resizableCols(tbl);
}}

// ═══════════════════════════════════════ DLCP SPLIT ANALYSIS (bin_distribution layout) ═══════

// ── State ──────────────────────────────────────────────────────────────────
var _dlcpT = 92.5;
var _dlcpDeselT = new Set();
var _dlcpFltValsT = {{}};
var _dlcpDdFltT = {{}};
var _dlcpDdCurColT = -1;
var _dlcpDdPendingT = null;

function updateDlcp(runs) {{
  window._dlcpRuns = runs;
  _dlcpRenderT();
}}

function _dlcpRowKeyT(lot, wafer) {{ return String(lot) + '|' + String(wafer); }}
function _dlcpIsRowSelT(key) {{ return !_dlcpDeselT.has(key); }}

function _dlcpComputeRowsT() {{
  var res = [], hasDie = false;
  var runs = window._dlcpRuns || [];
  runs.forEach(function(run) {{
    if (!run) return;
    var dies = run.dies || [];
    if (dies.length) hasDie = true;
    var nA=0, nB=0, nC=0, nFF=0, nDF3=0, nDF4=0, uv=[];
    dies.forEach(function(d) {{
      var ib=d[0], up=d[1];
      if (up != null) uv.push(up);
      if ((ib===1||ib===2) && up!=null && up>=_dlcpT) nA++;
      else if (ib!=null && ib>=1 && ib<=4) nB++;
      else nC++;
      if (ib===1||ib===2) nFF++;
      else if (ib===3) nDF3++;
      else if (ib===4) nDF4++;
    }});
    if (!dies.length) {{
      var bc = run.bin_counts || {{}};
      Object.keys(bc).forEach(function(b) {{
        var ibv=parseInt(b), cnt=bc[b]||0;
        if (ibv>=1&&ibv<=4) {{ if(ibv===1||ibv===2)nFF+=cnt; else if(ibv===3)nDF3+=cnt; else if(ibv===4)nDF4+=cnt; nB+=cnt; }}
        else nC+=cnt;
      }});
    }}
    var med=null;
    if (uv.length) {{
      uv.sort(function(a,b){{return a-b;}});
      var m=Math.floor(uv.length/2);
      med=uv.length%2===0?(uv[m-1]+uv[m])/2:uv[m];
    }}
    res.push({{
      lot:run.lot||'', wafer:run.wafer||'', mat:run.material||'',
      tot:run.total_dies||(nA+nB+nC), med:med,
      nA:nA, nB:nB, nC:nC, nFF:nFF, nDF34:nDF3+nDF4, nDF3:nDF3, nDF4:nDF4
    }});
  }});
  return {{rows:res, noDies:!hasDie}};
}}

function _dlcpRenderSummaryT(tA,tB,tC,tN,medAll,tFF,tDF34,tDF3,tDF4) {{
  var sb=document.getElementById('dlcp-sumbox'); if(!sb)return;
  if(!tN){{sb.innerHTML='<span style="color:#999;font-size:12px">No data</span>';return;}}
  var mTxt=medAll!=null?medAll.toFixed(2)+'%':'\u2014';
  var tIB14=tFF+(tDF3||0)+(tDF4||0);
  var row1='<div class="dlcp-sum-panel">'
    +'<div class="dlcp-sum-panel-ttl" style="background:#34495e">Overview</div>'
    +'<div class="dlcp-sumrow">'
    +'<div class="dlcp-sum-grp"><div class="dlcp-sum-lbl">Total Die</div><div class="dlcp-sum-val">'+tN+'</div></div>'
    +'<div class="dlcp-sum-grp"><div class="dlcp-sum-lbl">Med UPM%</div><div class="dlcp-sum-val">'+mTxt+'</div></div>'
    +'<div class="dlcp-sum-grp" style="border-color:#1a7a4a"><div class="dlcp-sum-lbl">FF+DF Yield</div><div class="dlcp-sum-pct-big" style="color:#1a7a4a">'+(tN>0?((tIB14||0)/tN*100).toFixed(1):0)+'%</div><div class="dlcp-sum-sub">N='+(tIB14||0)+' \u00b7 of total</div></div>'
    +'<div class="dlcp-sum-grp" style="border-color:#1e8449"><div class="dlcp-sum-lbl">FF (IB 1,2) Yield</div><div class="dlcp-sum-pct-big" style="color:#1e8449">'+(tN>0?((tFF||0)/tN*100).toFixed(1):0)+'%</div><div class="dlcp-sum-sub">N='+(tFF||0)+' \u00b7 of total</div></div>'
    +'<div class="dlcp-sum-grp fail"><div class="dlcp-sum-lbl">Fail (IB&gt;4)</div><div class="dlcp-sum-pct-big" style="color:#c0392b">'+(tN>0?(tC/tN*100).toFixed(1):0)+'%</div><div class="dlcp-sum-sub">N='+tC+' \u00b7 of total</div></div>'
    +'</div></div>';
  var row2='<div class="dlcp-sum-panel">'
    +'<div class="dlcp-sum-panel-ttl" style="background:#1a5276">DLCP Split</div>'
    +'<div class="dlcp-sumrow">'
    +'<div class="dlcp-sum-grp pass"><div class="dlcp-sum-lbl">HP (IB1/2, UPM\u2265thr)</div><div class="dlcp-sum-pct-big" style="color:#1a5276">'+(tA+tB>0?(tA/(tA+tB)*100).toFixed(1):0)+'%</div><div class="dlcp-sum-sub">N='+tA+' \u00b7 of IB1-4</div></div>'
    +'<div class="dlcp-sum-grp marg"><div class="dlcp-sum-lbl">LP (IB1-4, below thr)</div><div class="dlcp-sum-pct-big" style="color:#ba6b0a">'+(tA+tB>0?(tB/(tA+tB)*100).toFixed(1):0)+'%</div><div class="dlcp-sum-sub">N='+tB+' \u00b7 of IB1-4</div></div>'
    +'</div>'
    +'<div style="font-size:13px;font-weight:bold;color:#555;text-transform:uppercase;letter-spacing:.5px;margin:8px 0 3px 4px;border-bottom:1px solid #e0e0e0;padding-bottom:3px">FF/DF Breakdown</div>'
    +'<div class="dlcp-sumrow">'
    +'<div class="dlcp-sum-grp" style="border-color:#1e8449"><div class="dlcp-sum-lbl" style="font-size:13px">FF (IB 1,2)</div><div class="dlcp-sum-pct-big" style="color:#1e8449;font-size:26px">'+(tIB14>0?((tFF||0)/tIB14*100).toFixed(1):0)+'%</div><div class="dlcp-sum-sub" style="font-size:12px">N='+(tFF||0)+' \u00b7 of IB1-4</div></div>'
    +'<div class="dlcp-sum-grp" style="border-color:#117a65"><div class="dlcp-sum-lbl" style="font-size:13px">DF (IB 3-4)</div><div class="dlcp-sum-pct-big" style="color:#117a65;font-size:26px">'+(tIB14>0?((tDF34||0)/tIB14*100).toFixed(1):0)+'%</div><div class="dlcp-sum-sub" style="font-size:12px">N='+(tDF34||0)+' \u00b7 of IB1-4</div></div>'
    +'<div class="dlcp-sum-grp" style="border-color:#7d3c98"><div class="dlcp-sum-lbl" style="font-size:13px">ATOM DF (IB 3)</div><div class="dlcp-sum-pct-big" style="color:#7d3c98;font-size:26px">'+(tIB14>0?((tDF3||0)/tIB14*100).toFixed(1):0)+'%</div><div class="dlcp-sum-sub" style="font-size:12px">N='+(tDF3||0)+' \u00b7 of IB1-4</div></div>'
    +'<div class="dlcp-sum-grp" style="border-color:#a04000"><div class="dlcp-sum-lbl" style="font-size:13px">CORE DF (IB 4)</div><div class="dlcp-sum-pct-big" style="color:#a04000;font-size:26px">'+(tIB14>0?((tDF4||0)/tIB14*100).toFixed(1):0)+'%</div><div class="dlcp-sum-sub" style="font-size:12px">N='+(tDF4||0)+' \u00b7 of IB1-4</div></div>'
    +'</div></div>';
  sb.innerHTML=row1+row2;
}}

function _dlcpBuildFilterRowT() {{
  var fr=document.getElementById('dlcp-flt-row-t'); if(!fr)return;
  var nc=21, html='<tr style="background:#f0f4ff">';
  for(var ci=0;ci<nc;ci++) {{
    if(ci<3){{html+='<td></td>';continue;}}
    html+='<td style="padding:1px 3px"><input data-ci="'+ci+'" placeholder="e.g. &gt;50" style="width:100%;box-sizing:border-box;font-size:10px;padding:1px 3px;border:1px solid #ccd;border-radius:2px" oninput="dlcpFltInputT('+ci+',this.value)"></td>';
  }}
  fr.innerHTML=html+'</tr>';
}}
function _dlcpNumValT(txt){{var s=txt.replace(/%/g,'').replace(/\u2014/g,'').trim();var n=parseFloat(s);return isNaN(n)?null:n;}}
function _dlcpNumTestT(fv,cellTxt){{
  var m=fv.match(/^(>=|<=|!=|>|<|=)?\s*([\d.]+)$/);if(!m)return true;
  var op=m[1]||'=',thr=parseFloat(m[2]);var val=_dlcpNumValT(cellTxt);if(val===null)return false;
  if(op==='>') return val>thr;if(op==='<') return val<thr;if(op==='>=') return val>=thr;
  if(op==='<=') return val<=thr;if(op==='!=') return val!==thr;return val===thr;
}}
function dlcpFltInputT(ci,val){{_dlcpFltValsT[ci]=(val||'').trim();_dlcpApplyFilterT();}}
function _dlcpApplyFilterT(){{
  var tb=document.getElementById('dlcp-tb-t');if(!tb)return;
  var rows=tb.getElementsByTagName('tr');
  for(var i=0;i<rows.length;i++){{
    var cells=rows[i].getElementsByTagName('td');var show=true;
    var colVals=[cells[0]?cells[0].textContent:'',cells[1]?cells[1].textContent:'',cells[2]?cells[2].textContent:''];
    [0,1,2].forEach(function(ci){{var fs=_dlcpDdFltT[ci];if(fs&&fs.size>0&&!fs.has(colVals[ci]))show=false;}});
    Object.keys(_dlcpFltValsT).forEach(function(ci){{
      var fv=_dlcpFltValsT[ci];if(!fv)return;
      var cellTxt=cells[parseInt(ci)]?cells[parseInt(ci)].textContent:'';
      if(!_dlcpNumTestT(fv,cellTxt))show=false;
    }});
    rows[i].style.display=show?'':'none';
  }}
}}
function _dlcpEscT(s){{return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}

function _dlcpRenderTableT(){{
  var r=_dlcpComputeRowsT();
  var tb=document.getElementById('dlcp-tb-t');if(!tb)return;
  var nd=document.getElementById('dlcp-no-data-msg');
  if(nd)nd.style.display=r.noDies?'':'none';
  if(r.noDies){{tb.innerHTML='<tr><td colspan="21" style="padding:14px;color:#7f8c8d;text-align:center">No die-level UPM data available.</td></tr>';_dlcpRenderSummaryT(0,0,0,0,null,0,0,0,0);return;}}
  var tA=0,tB=0,tC=0,tN=0,tFF=0,tDF34=0,tDF3=0,tDF4=0,allUv=[],html='';
  r.rows.forEach(function(x){{
    var t=x.nA+x.nB+x.nC;if(!t)return;
    var key=_dlcpRowKeyT(x.lot,x.wafer);
    var isSel=_dlcpIsRowSelT(key);
    var ddOk=true;
    var ddVals=[x.lot,x.wafer,x.mat];
    [0,1,2].forEach(function(ci){{var fs=_dlcpDdFltT[ci];if(fs&&fs.size>0&&!fs.has(ddVals[ci]))ddOk=false;}});
    var visStyle=ddOk?'':'display:none';
    var f12=x.nA+x.nB, f14=x.nFF+x.nDF3+x.nDF4;
    if(ddOk){{tA+=isSel?x.nA:0;tB+=isSel?x.nB:0;tC+=isSel?x.nC:0;tN+=isSel?t:0;
              tFF+=isSel?x.nFF:0;tDF34+=isSel?x.nDF34:0;tDF3+=isSel?x.nDF3:0;tDF4+=isSel?x.nDF4:0;}}
    html+='<tr data-key="'+_dlcpEscT(key)+'" class="'+(isSel?'dlcp-rsel':'dlcp-runsel')+'" style="'+visStyle+'" onclick="dlcpRowClickT(this.getAttribute(\\'data-key\\'))">'+
      '<td>'+_dlcpEscT(x.lot)+'</td>'+'<td>'+_dlcpEscT(x.wafer)+'</td>'+'<td style="color:#555;font-size:11px">'+_dlcpEscT(x.mat)+'</td>'+
      '<td class="num">'+t+'</td>'+'<td class="num">'+(x.med!=null?x.med.toFixed(2)+'%':'\u2014')+'</td>'+
      '<td class="num" style="color:#1a5276;font-weight:bold">'+x.nA+'</td>'+'<td class="num" style="color:#1a5276">'+(f12>0?(x.nA/f12*100).toFixed(1):'\u2014')+'%</td>'+
      '<td class="num" style="color:#ba6b0a">'+x.nB+'</td>'+'<td class="num" style="color:#ba6b0a">'+(f12>0?(x.nB/f12*100).toFixed(1):'\u2014')+'%</td>'+
      '<td class="num" style="color:#922b21">'+x.nC+'</td>'+'<td class="num" style="color:#922b21">'+(t>0?(x.nC/t*100).toFixed(1):'\u2014')+'%</td>'+
      '<td class="num" style="color:#1a7a4a;font-weight:bold">'+f14+'</td>'+'<td class="num" style="color:#1a7a4a">'+(t>0?(f14/t*100).toFixed(1):'\u2014')+'%</td>'+
      '<td class="num" style="color:#1e8449;font-weight:bold">'+x.nFF+'</td>'+'<td class="num" style="color:#1e8449">'+(f14>0?(x.nFF/f14*100).toFixed(1):'\u2014')+'%</td>'+
      '<td class="num" style="color:#117a65;font-weight:bold">'+x.nDF34+'</td>'+'<td class="num" style="color:#117a65">'+(f14>0?(x.nDF34/f14*100).toFixed(1):'\u2014')+'%</td>'+
      '<td class="num" style="color:#7d3c98">'+x.nDF3+'</td>'+'<td class="num" style="color:#7d3c98">'+(f14>0?(x.nDF3/f14*100).toFixed(1):'\u2014')+'%</td>'+
      '<td class="num" style="color:#a04000">'+x.nDF4+'</td>'+'<td class="num" style="color:#a04000">'+(f14>0?(x.nDF4/f14*100).toFixed(1):'\u2014')+'%</td>'+
      '</tr>';
  }});
  tb.innerHTML=html;
  _dlcpBuildFilterRowT();
  var visKeys=new Set();
  var tbEl=document.getElementById('dlcp-tb-t');
  if(tbEl){{var trs=tbEl.getElementsByTagName('tr');for(var vi=0;vi<trs.length;vi++){{if(trs[vi].style.display!=='none'){{var vk=trs[vi].getAttribute('data-key');if(vk&&!_dlcpDeselT.has(vk))visKeys.add(vk);}}}};}}
  (window._dlcpRuns||[]).forEach(function(run){{
    var k=_dlcpRowKeyT(run.lot||'',run.wafer||'');
    if(!visKeys.has(k))return;
    (run.dies||[]).forEach(function(d){{if(d[1]!=null)allUv.push(d[1]);}});
  }});
  allUv.sort(function(a,b){{return a-b;}});
  var medAll=null;
  if(allUv.length){{var m2=Math.floor(allUv.length/2);medAll=allUv.length%2===0?(allUv[m2-1]+allUv[m2])/2:allUv[m2];}}
  _dlcpRenderSummaryT(tA,tB,tC,tN,medAll,tFF,tDF34,tDF3,tDF4);
  var noteEl=document.getElementById('dlcp-note-t');
  if(noteEl)noteEl.innerHTML='<b>HP%</b> = HP / (HP+LP) &nbsp;|&nbsp; <b>LP%</b> = LP / (HP+LP) &nbsp;|&nbsp; <b>Fail%</b> = Fail / Total &nbsp;|&nbsp; <b>FF/DF%</b> = count / IB1-4 total &nbsp;|&nbsp; Threshold: <b>'+_dlcpT.toFixed(1)+'%</b>';
}}

function _dlcpRenderCdfT(){{
  var cv=document.getElementById('dlcp-cv-t');if(!cv)return;
  var W=cv.clientWidth||560,H=cv.clientHeight||280;
  cv.width=W;cv.height=H;
  var ctx=cv.getContext('2d');ctx.clearRect(0,0,W,H);
  var hp=[],lp=[],ff=[],df=[];
  var MAX_CDF=80000,_cdfTot=0;
  var runs=window._dlcpRuns||[];
  runs.forEach(function(run){{if(!run||!run.dies)return;var k=_dlcpRowKeyT(run.lot||'',run.wafer||'');if(!_dlcpIsRowSelT(k))return;_cdfTot+=run.dies.length;}});
  var _cdfStep=_cdfTot>MAX_CDF?Math.ceil(_cdfTot/MAX_CDF):1,_cdfI=0;
  runs.forEach(function(run){{
    if(!run||!run.dies)return;
    var k=_dlcpRowKeyT(run.lot||'',run.wafer||'');if(!_dlcpIsRowSelT(k))return;
    run.dies.forEach(function(d){{
      _cdfI++;if(_cdfI%_cdfStep!==0)return;
      var ib=d[0],up=d[1];if(up==null)return;
      if(ib===1||ib===2){{ff.push(up);if(up>=_dlcpT)hp.push(up);else lp.push(up);}}
      else if(ib===3||ib===4){{df.push(up);lp.push(up);}}
    }});
  }});
  hp.sort(function(a,b){{return a-b;}});lp.sort(function(a,b){{return a-b;}});
  ff.sort(function(a,b){{return a-b;}});df.sort(function(a,b){{return a-b;}});
  if(!hp.length&&!lp.length){{
    ctx.fillStyle='#999';ctx.font='13px Arial';ctx.textAlign='center';
    ctx.fillText('No UPM die data in selected wafers',W/2,H/2);return;
  }}
  var ML=52,MR=16,MT=22,MB=42,PW=W-ML-MR,PH=H-MT-MB;
  var all=hp.concat(lp).concat(ff).concat(df);
  var xMn=Math.floor(Math.min.apply(null,all)*2)/2-1,xMx=Math.ceil(Math.max.apply(null,all)*2)/2+1;
  if(xMx-xMn<4){{xMn-=2;xMx+=2;}}
  function xp(v){{return ML+(v-xMn)/(xMx-xMn)*PW;}}
  function yp(v){{return MT+PH-v/100*PH;}}
  ctx.strokeStyle='#e8e8e8';ctx.lineWidth=1;
  for(var yi=0;yi<=4;yi++){{ctx.beginPath();ctx.moveTo(ML,yp(yi*25));ctx.lineTo(ML+PW,yp(yi*25));ctx.stroke();}}
  if(_dlcpT>=xMn&&_dlcpT<=xMx){{
    var tx=xp(_dlcpT);ctx.save();ctx.strokeStyle='#e74c3c';ctx.lineWidth=1.5;ctx.setLineDash([5,4]);
    ctx.beginPath();ctx.moveTo(tx,MT);ctx.lineTo(tx,MT+PH);ctx.stroke();
    ctx.setLineDash([]);ctx.fillStyle='#e74c3c';ctx.font='11px Arial';ctx.textAlign='center';
    ctx.fillText(_dlcpT.toFixed(1)+'%',tx,MT-5);ctx.restore();
  }}
  function drawCdf(arr,col,dash){{
    if(!arr.length)return;var n=arr.length;
    ctx.save();ctx.strokeStyle=col;ctx.lineWidth=2;if(dash)ctx.setLineDash([6,3]);
    ctx.beginPath();ctx.moveTo(xp(arr[0]),yp(0));
    for(var i=0;i<n;i++){{ctx.lineTo(xp(arr[i]),yp((i+1)/n*100));if(i<n-1)ctx.lineTo(xp(arr[i+1]),yp((i+1)/n*100));}}
    ctx.lineTo(ML+PW,yp(100));ctx.stroke();ctx.restore();
  }}
  drawCdf(df,'#8e44ad',true);drawCdf(ff,'#27ae60',true);drawCdf(lp,'#e67e22',false);drawCdf(hp,'#2980b9',false);
  ctx.strokeStyle='#555';ctx.lineWidth=1;
  ctx.beginPath();ctx.moveTo(ML,MT);ctx.lineTo(ML,MT+PH);ctx.lineTo(ML+PW,MT+PH);ctx.stroke();
  ctx.fillStyle='#555';ctx.font='11px Arial';ctx.textAlign='right';
  for(var yi2=0;yi2<=4;yi2++)ctx.fillText(yi2*25+'%',ML-4,yp(yi2*25)+4);
  ctx.textAlign='center';
  var rng=xMx-xMn,stp=rng>20?5:rng>10?2:1,xs=Math.ceil(xMn/stp)*stp;
  for(var xv=xs;xv<=xMx;xv+=stp)ctx.fillText(xv.toFixed(0)+'%',xp(xv),MT+PH+14);
  ctx.fillStyle='#2c3e50';ctx.font='bold 11px Arial';ctx.textAlign='center';
  ctx.fillText('UPM %',ML+PW/2,H-4);
  ctx.save();ctx.translate(13,MT+PH/2);ctx.rotate(-Math.PI/2);ctx.fillText('Cumulative %',0,0);ctx.restore();
  var ib14=ff.length+df.length;
  function lgPct(n,d){{return d>0?(n/d*100).toFixed(1)+'%':'0%';}}
  function drawLgEntry(x,y,lineCol,lineDash,pctTxt,nTxt,pctCol){{
    if(lineDash){{ctx.save();ctx.strokeStyle=lineCol;ctx.lineWidth=2;ctx.setLineDash([6,3]);ctx.beginPath();ctx.moveTo(x,y+2);ctx.lineTo(x+22,y+2);ctx.stroke();ctx.restore();}}
    else{{ctx.fillStyle=lineCol;ctx.fillRect(x,y+1,22,3);}}
    ctx.font='bold 12px Arial';ctx.fillStyle=pctCol||lineCol;ctx.textAlign='left';ctx.fillText(pctTxt,x+26,y+8);
    var pw=ctx.measureText(pctTxt).width;ctx.font='10px Arial';ctx.fillStyle='#aaa';ctx.fillText(' '+nTxt,x+26+pw,y+8);
  }}
  var ly=MT+8;
  drawLgEntry(ML,ly,'#2980b9',false,lgPct(hp.length,ib14),'HP  n='+hp.length,'#2980b9');
  drawLgEntry(ML+210,ly,'#e67e22',false,lgPct(lp.length,ib14),'LP  n='+lp.length,'#e67e22');
  var ly2=ly+16;
  drawLgEntry(ML,ly2,'#27ae60',true,lgPct(ff.length,ib14),'FF IB1,2  n='+ff.length,'#27ae60');
  drawLgEntry(ML+210,ly2,'#8e44ad',true,lgPct(df.length,ib14),'DF IB3,4  n='+df.length,'#8e44ad');
}}

function _dlcpRenderT(){{
  _dlcpRenderTableT();
  requestAnimationFrame(_dlcpRenderCdfT);
}}

function dlcpSliderT(){{
  var sl=document.getElementById('dlcp-sl');if(!sl)return;
  _dlcpT=parseFloat(sl.value);
  var inp=document.getElementById('dlcp-tv-inp');if(inp)inp.value=_dlcpT.toFixed(1);
  _dlcpRenderT();rebuildCharts();
}}
function dlcpTxtT(val){{
  var v=parseFloat(val);if(isNaN(v))return;
  v=Math.max(70,Math.min(100,v));_dlcpT=v;
  var sl=document.getElementById('dlcp-sl');if(sl)sl.value=v;
  _dlcpRenderT();rebuildCharts();
}}

function dlcpRowClickT(key){{
  if(_dlcpDeselT.has(key))_dlcpDeselT.delete(key);else _dlcpDeselT.add(key);
  var tb=document.getElementById('dlcp-tb-t');if(!tb)return;
  var rows=tb.getElementsByTagName('tr');
  for(var i=0;i<rows.length;i++){{
    var k=rows[i].getAttribute('data-key');
    if(k===key){{rows[i].classList.toggle('dlcp-runsel',_dlcpDeselT.has(key));rows[i].classList.toggle('dlcp-rsel',!_dlcpDeselT.has(key));}}
  }}
  _dlcpRenderT();
}}
function dlcpSelAllT(){{_dlcpDeselT.clear();_dlcpRenderTableT();_dlcpRenderT();}}
function dlcpSelNoneT(){{
  var r=_dlcpComputeRowsT();r.rows.forEach(function(x){{_dlcpDeselT.add(_dlcpRowKeyT(x.lot,x.wafer));}});
  _dlcpRenderTableT();_dlcpRenderT();
}}

function dlcpDdOpenT(col,btn){{
  if(_dlcpDdCurColT===col){{_dlcpDdCloseT();return;}}
  _dlcpDdCurColT=col;
  var panel=document.getElementById('dlcp-dd-panel-t');if(!panel)return;
  var srch=document.getElementById('dlcp-dd-srch-t');if(srch)srch.value='';
  var r=_dlcpComputeRowsT(),vals=[],seen=new Set();
  r.rows.forEach(function(x){{var v=col===0?x.lot:col===1?x.wafer:x.mat;if(!seen.has(v)){{seen.add(v);vals.push(v);}}}});
  vals.sort();
  var cur=_dlcpDdFltT[col]||null;
  _dlcpDdPendingT=cur?new Set(cur):null;
  var lst=document.getElementById('dlcp-dd-list-t');if(!lst)return;
  lst.innerHTML=vals.map(function(v){{
    var chk=(!_dlcpDdPendingT||_dlcpDdPendingT.has(v))?'checked':'';
    return '<label class="dlcp-dd-item"><input type="checkbox" value="'+_dlcpEscT(v)+'" '+chk+' onchange="dlcpDdChkT(this)"> <span>'+_dlcpEscT(v)+'</span></label>';
  }}).join('');
  var r2=btn.getBoundingClientRect();
  panel.style.display='block';panel.style.left=r2.left+'px';panel.style.top=(r2.bottom+2)+'px';
  document.querySelectorAll('.dlcp-ddbtn').forEach(function(b){{b.classList.remove('on');}});
  btn.classList.add('on');
  setTimeout(function(){{document.addEventListener('click',_dlcpDdOutsideT,{{once:true}});}},0);
}}
function _dlcpDdOutsideT(){{dlcpDdApplyT();}}
function _dlcpDdCloseT(){{
  _dlcpDdCurColT=-1;
  var panel=document.getElementById('dlcp-dd-panel-t');if(panel)panel.style.display='none';
  document.querySelectorAll('.dlcp-ddbtn').forEach(function(b){{b.classList.remove('on');}});
}}
function dlcpDdChkT(inp){{
  var v=inp.value,chk=inp.checked;
  if(!_dlcpDdPendingT){{
    var r=_dlcpComputeRowsT();_dlcpDdPendingT=new Set();
    r.rows.forEach(function(x){{_dlcpDdPendingT.add(_dlcpDdCurColT===0?x.lot:_dlcpDdCurColT===1?x.wafer:x.mat);}});
  }}
  if(chk)_dlcpDdPendingT.add(v);else _dlcpDdPendingT.delete(v);
}}
function dlcpDdSelAllT(){{
  _dlcpDdPendingT=null;
  var lst=document.getElementById('dlcp-dd-list-t');if(!lst)return;
  lst.querySelectorAll('input[type=checkbox]').forEach(function(cb){{cb.checked=true;}});
}}
function dlcpDdSelNoneT(){{
  _dlcpDdPendingT=new Set();
  var lst=document.getElementById('dlcp-dd-list-t');if(!lst)return;
  lst.querySelectorAll('input[type=checkbox]').forEach(function(cb){{cb.checked=false;}});
}}
function dlcpDdSearchT(q){{
  q=(q||'').toLowerCase();
  var lst=document.getElementById('dlcp-dd-list-t');if(!lst)return;
  lst.querySelectorAll('.dlcp-dd-item').forEach(function(el){{el.style.display=(!q||el.textContent.toLowerCase().indexOf(q)>=0)?'':'none';}});
}}
function dlcpDdApplyT(){{
  if(_dlcpDdCurColT>=0){{
    _dlcpDdFltT[_dlcpDdCurColT]=(_dlcpDdPendingT&&_dlcpDdPendingT.size>0)?_dlcpDdPendingT:null;
    var btn2=document.getElementById('dlcp-dd-btn-'+_dlcpDdCurColT);
    if(btn2)btn2.classList.toggle('on',!!_dlcpDdFltT[_dlcpDdCurColT]);
  }}
  _dlcpDdCloseT();_dlcpRenderTableT();_dlcpRenderT();
}}
function dlcpClearFiltersT(){{
  _dlcpFltValsT={{}};_dlcpDdFltT={{}};
  document.querySelectorAll('.dlcp-ddbtn').forEach(function(b){{b.classList.remove('on');}});
  var fr=document.getElementById('dlcp-flt-row-t');if(fr){{var inps=fr.getElementsByTagName('input');for(var i=0;i<inps.length;i++)inps[i].value='';}}
  var tb=document.getElementById('dlcp-tb-t');if(tb){{var rows=tb.getElementsByTagName('tr');for(var i=0;i<rows.length;i++)rows[i].style.display='';}}
  _dlcpRenderT();
}}
function dlcpSplitterToggleT(){{
  var rp=document.getElementById('dlcp-right-pane-t');
  var arr=document.getElementById('dlcp-split-arrow-t');
  if(!rp||!arr)return;
  var hidden=rp.style.display==='none';
  rp.style.display=hidden?'':'none';
  arr.innerHTML=hidden?'&#9654;':'&#9664;';
  if(!hidden)requestAnimationFrame(_dlcpRenderCdfT);
}}
function dlcpSavePngT(){{
  var cv=document.getElementById('dlcp-cv-t');if(!cv)return;
  var a=document.createElement('a');a.href=cv.toDataURL('image/png');a.download='dlcp_cdf.png';a.click();
}}
function dlcpDownloadCsv(){{dlcpDownloadCsvT();}}
function dlcpDownloadCsvT(){{
  var r=_dlcpComputeRowsT();
  var hdr=['Lot','Wafer','Material','Total','Med UPM%','HP#','HP%','LP#','LP%','Fail#','Fail%','FF+DF#','FF+DF%','FF#','FF%','DF#','DF%','ATOM DF#','ATOM DF%','CORE DF#','CORE DF%'];
  var tb=document.getElementById('dlcp-tb-t'),visKeys=new Set();
  if(tb){{var trs=tb.getElementsByTagName('tr');for(var vi=0;vi<trs.length;vi++){{if(trs[vi].style.display!=='none'){{var vk=trs[vi].getAttribute('data-key');if(vk)visKeys.add(vk);}}}};}}
  function q(s){{var v=String(s==null?'':s);return(v.indexOf(',')>=0||v.indexOf('"')>=0)?'"'+v.replace(/"/g,'""')+'"':v;}}
  var lines=[hdr.join(',')];
  r.rows.forEach(function(x){{
    var k=_dlcpRowKeyT(x.lot,x.wafer);
    if(visKeys.size>0&&!visKeys.has(k))return;
    var t=x.nA+x.nB+x.nC;if(!t)return;
    var f12=x.nA+x.nB,f14=x.nFF+x.nDF3+x.nDF4;
    lines.push([x.lot,x.wafer,x.mat,t,x.med!=null?x.med.toFixed(2):'',
      x.nA,f12>0?(x.nA/f12*100).toFixed(1):'',x.nB,f12>0?(x.nB/f12*100).toFixed(1):'',
      x.nC,t>0?(x.nC/t*100).toFixed(1):'',f14,t>0?(f14/t*100).toFixed(1):'',
      x.nFF,f14>0?(x.nFF/f14*100).toFixed(1):'',x.nDF34,f14>0?(x.nDF34/f14*100).toFixed(1):'',
      x.nDF3,f14>0?(x.nDF3/f14*100).toFixed(1):'',x.nDF4,f14>0?(x.nDF4/f14*100).toFixed(1):''].map(q).join(','));
  }});
  var blob=new Blob([lines.join('\\n')],{{type:'text/csv'}});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='dlcp_table.csv';a.click();
}}

function dlcpOpenHistT(){{
  var ov=document.getElementById('upm-hist-overlay-t');if(!ov)return;
  ov.classList.add('open');
  var box=document.getElementById('upm-hist-box-t'),drag=document.getElementById('upm-hist-drag-t');
  if(box&&drag&&!drag._histDrag){{
    drag._histDrag=true;
    drag.addEventListener('mousedown',function(e){{
      e.preventDefault();var startX=e.clientX,startY=e.clientY,r=box.getBoundingClientRect();
      var startL=r.left,startT2=r.top;box.style.position='fixed';box.style.margin='0';
      function mm(e2){{box.style.left=(startL+e2.clientX-startX)+'px';box.style.top=(startT2+e2.clientY-startY)+'px';}}
      function mu(){{document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);}}
      document.addEventListener('mousemove',mm);document.addEventListener('mouseup',mu);
    }});
  }}
  requestAnimationFrame(_dlcpRenderHistT);
}}
function dlcpCloseHistT(){{
  var ov=document.getElementById('upm-hist-overlay-t');if(ov)ov.classList.remove('open');
}}
function _dlcpHistStatsT(arr){{
  if(!arr||!arr.length)return null;
  var n=arr.length,sorted=arr.slice().sort(function(a,b){{return a-b;}});
  var mean=sorted.reduce(function(s,v){{return s+v;}},0)/n;
  var sigma=Math.sqrt(sorted.reduce(function(s,v){{return s+(v-mean)*(v-mean);}},0)/n);
  var med=n%2===0?(sorted[n/2-1]+sorted[n/2])/2:sorted[Math.floor(n/2)];
  function pct(p){{var i=(p/100)*(n-1),lo=Math.floor(i),hi=Math.ceil(i);return lo===hi?sorted[lo]:sorted[lo]+(sorted[hi]-sorted[lo])*(i-lo);}}
  return{{n:n,mean:mean,med:med,sigma:sigma,min:sorted[0],max:sorted[n-1],p5:pct(5),p25:pct(25),p75:pct(75),p95:pct(95)}};
}}
function _dlcpRenderHistT(){{
  var cv=document.getElementById('upm-hist-cv-t');if(!cv)return;
  var W=cv.clientWidth||740,H=cv.clientHeight||260;cv.width=W;cv.height=H;
  var ctx=cv.getContext('2d');ctx.clearRect(0,0,W,H);
  var MAX_HIST=80000,_hTot=0;
  var runs=window._dlcpRuns||[];
  runs.forEach(function(run){{if(!run||!run.dies)return;var k=_dlcpRowKeyT(run.lot||'',run.wafer||'');if(!_dlcpIsRowSelT(k))return;_hTot+=run.dies.length;}});
  var _hStep=_hTot>MAX_HIST?Math.ceil(_hTot/MAX_HIST):1,_hI=0,hp2=[],lp2=[];
  runs.forEach(function(run){{
    if(!run||!run.dies)return;
    var k=_dlcpRowKeyT(run.lot||'',run.wafer||'');if(!_dlcpIsRowSelT(k))return;
    run.dies.forEach(function(d){{
      _hI++;if(_hI%_hStep!==0)return;
      var ib=d[0],up=d[1];if(up==null)return;
      if((ib===1||ib===2)&&up>=_dlcpT)hp2.push(up);else if(ib!=null&&ib>=1&&ib<=4)lp2.push(up);
    }});
  }});
  var all2=hp2.concat(lp2);
  if(!all2.length){{
    ctx.fillStyle='#999';ctx.font='13px Arial';ctx.textAlign='center';ctx.fillText('No UPM data in selected wafers',W/2,H/2);
    var sd=document.getElementById('upm-hist-stats-t');if(sd)sd.innerHTML='<span style="color:#999">No data</span>';return;
  }}
  var asP=_dlcpHistStatsT(all2),hsP=_dlcpHistStatsT(hp2),lsP=_dlcpHistStatsT(lp2);
  function fmt(v){{return v==null?'\u2014':v.toFixed(2);}}
  function sCard(lbl,s,col){{
    if(!s)return '<div class="upm-hist-stat-grp" style="border-color:'+col+'"><div class="upm-hist-stat-lbl" style="color:'+col+'">'+lbl+'</div><div class="upm-hist-stat-val">N=0</div></div>';
    return'<div class="upm-hist-stat-grp" style="border-color:'+col+'"><div class="upm-hist-stat-lbl" style="color:'+col+'">'+lbl+'</div><div class="upm-hist-stat-val" style="color:'+col+'">N='+s.n+'</div>'
      +'<div style="font-size:11px;color:#444">Median: <b>'+fmt(s.med)+'%</b> &nbsp; Mean: <b>'+fmt(s.mean)+'%</b></div>'
      +'<div style="font-size:11px;color:#444">\u03c3: <b>'+fmt(s.sigma)+'%</b> &nbsp; Min: '+fmt(s.min)+'% &nbsp; Max: '+fmt(s.max)+'%</div>'
      +'<div style="font-size:10px;color:#777">P5: '+fmt(s.p5)+'% &nbsp; P25: '+fmt(s.p25)+'% &nbsp; P75: '+fmt(s.p75)+'% &nbsp; P95: '+fmt(s.p95)+'%</div></div>';
  }}
  var sd2=document.getElementById('upm-hist-stats-t');
  if(sd2)sd2.innerHTML=sCard('All IB1-4',asP,'#2c3e50')+sCard('HP (IB1/2 \u2265thr)',hsP,'#2980b9')+sCard('LP (IB1-4 <thr)',lsP,'#e67e22');
  var xMn2=Math.floor(Math.min.apply(null,all2)),xMx2=Math.ceil(Math.max.apply(null,all2));
  if(xMx2-xMn2<4){{xMn2-=2;xMx2+=2;}}
  var bins=Math.min(80,Math.max(20,Math.round((xMx2-xMn2)*2))),bw2=(xMx2-xMn2)/bins;
  function mBins2(arr2){{var b=new Array(bins).fill(0);arr2.forEach(function(v){{var i2=Math.min(bins-1,Math.floor((v-xMn2)/bw2));if(i2>=0)b[i2]++;}});return b;}}
  var hpB2=mBins2(hp2),lpB2=mBins2(lp2),maxC2=0;
  for(var i=0;i<bins;i++){{var s2=hpB2[i]+lpB2[i];if(s2>maxC2)maxC2=s2;}}
  if(!maxC2)return;
  var ML2=46,MR2=14,MT2=20,MB2=38,PW2=W-ML2-MR2,PH2=H-MT2-MB2;
  function xp2(v){{return ML2+(v-xMn2)/(xMx2-xMn2)*PW2;}}
  ctx.strokeStyle='#ececec';ctx.lineWidth=1;
  for(var gi=0;gi<=4;gi++){{var gy=MT2+PH2*gi/4;ctx.beginPath();ctx.moveTo(ML2,gy);ctx.lineTo(ML2+PW2,gy);ctx.stroke();}}
  for(var bi=0;bi<bins;bi++){{
    var bx0=xp2(xMn2+bi*bw2)+0.5,bx1=xp2(xMn2+(bi+1)*bw2)-0.5,bW2=Math.max(1,bx1-bx0);
    var lpH=lpB2[bi]/maxC2*PH2,hpH=hpB2[bi]/maxC2*PH2;
    if(lpB2[bi]>0){{ctx.fillStyle='rgba(230,126,34,0.78)';ctx.fillRect(bx0,MT2+PH2-lpH,bW2,lpH);}}
    if(hpB2[bi]>0){{ctx.fillStyle='rgba(41,128,185,0.82)';ctx.fillRect(bx0,MT2+PH2-lpH-hpH,bW2,hpH);}}
  }}
  if(_dlcpT>=xMn2&&_dlcpT<=xMx2){{
    var tx2=xp2(_dlcpT);ctx.save();ctx.strokeStyle='#e74c3c';ctx.lineWidth=2;ctx.setLineDash([5,4]);
    ctx.beginPath();ctx.moveTo(tx2,MT2);ctx.lineTo(tx2,MT2+PH2);ctx.stroke();
    ctx.setLineDash([]);ctx.fillStyle='#e74c3c';ctx.font='11px Arial';ctx.textAlign='center';
    ctx.fillText(_dlcpT.toFixed(1)+'%',tx2,MT2-5);ctx.restore();
  }}
  ctx.strokeStyle='#555';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(ML2,MT2);ctx.lineTo(ML2,MT2+PH2);ctx.lineTo(ML2+PW2,MT2+PH2);ctx.stroke();
  ctx.fillStyle='#555';ctx.font='10px Arial';ctx.textAlign='right';
  for(var yi3=0;yi3<=4;yi3++)ctx.fillText(Math.round(maxC2*yi3/4),ML2-4,MT2+PH2-PH2*yi3/4+4);
  ctx.textAlign='center';
  var rng2=xMx2-xMn2,xstp2=rng2>40?5:rng2>20?2:1,xs2=Math.ceil(xMn2/xstp2)*xstp2;
  for(var xv2=xs2;xv2<=xMx2;xv2+=xstp2)ctx.fillText(xv2+'%',xp2(xv2),MT2+PH2+13);
  ctx.fillStyle='#2c3e50';ctx.font='bold 11px Arial';ctx.textAlign='center';
  ctx.fillText('UPM %',ML2+PW2/2,H-4);
  ctx.save();ctx.translate(12,MT2+PH2/2);ctx.rotate(-Math.PI/2);ctx.fillText('Count',0,0);ctx.restore();
}}

(function(){{
  var cv=document.getElementById('dlcp-cv-t');
  if(cv&&window.ResizeObserver&&!cv._dlcpRO){{
    cv._dlcpRO=true;
    new ResizeObserver(function(){{if(_activeTab==='dlcp')requestAnimationFrame(_dlcpRenderCdfT);}}).observe(cv);
  }}
}})();

// ═══════════════════════════════════════ PARETO COMMENTS ══════════════════
const COMMENT_KEY = 'pareto_comments';
function loadComments() {{
  try {{ return JSON.parse(localStorage.getItem(COMMENT_KEY) || '{{}}'); }} catch(e) {{ return {{}}; }}
}}
function saveComment(ib, text) {{
  const all = loadComments();
  if (text) all[ib] = text; else delete all[ib];
  localStorage.setItem(COMMENT_KEY, JSON.stringify(all));
}}
function initParetoComments() {{
  const saved = loadComments();
  document.querySelectorAll('.pareto-comment').forEach(ta => {{
    const ib = ta.dataset.ib;
    if (saved[ib]) {{ ta.value = saved[ib]; ta.classList.add('saved'); }}
    ta.addEventListener('input', () => ta.classList.remove('saved'));
    ta.addEventListener('blur', () => {{
      saveComment(ib, ta.value.trim());
      ta.classList.toggle('saved', !!ta.value.trim());
    }});
  }});
}}

function exportParetoTableCsv() {{
  const saved = loadComments();
  const rows = [['Interface Bin', 'Description', 'Total Tested', 'Fail Count', 'Fail %', 'Comment']];
  document.querySelectorAll('#pareto-summary-tbl tbody tr').forEach(tr => {{
    const cells = tr.querySelectorAll('td');
    if (cells.length < 5) return;
    const ib    = cells[0].textContent.trim();
    const desc  = cells[1].textContent.trim();
    const total = cells[2].textContent.trim();
    const nf    = cells[3].textContent.trim();
    const pct   = cells[4].textContent.trim();
    const cmt   = saved[ib] || '';
    rows.push([ib, desc, total, nf, pct, cmt]);
  }});
  const csv = rows.map(r => r.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(',')).join('\\n');
  const a = document.createElement('a');
  a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
  a.download = 'pareto_summary.csv';
  a.click();
}}

function exportComments() {{
  const saved = loadComments();
  const rows = [['IB', 'Comment']];
  document.querySelectorAll('.pareto-comment').forEach(ta => {{
    rows.push([ta.dataset.ib, saved[ta.dataset.ib] || '']);
  }});
  const csv = rows.map(r => r.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(',')).join('\\n');
  const a = document.createElement('a');
  a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
  a.download = 'pareto_comments.csv';
  a.click();
}}

function importComments(input) {{
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {{
    const lines = e.target.result.split(/\\r?\\n/).slice(1); // skip header
    lines.forEach(line => {{
      if (!line.trim()) return;
      const m = line.match(/^"(\\d+)","((?:[^"]|"")*)"/);
      if (!m) return;
      const ib = m[1], text = m[2].replace(/""/g, '"');
      saveComment(ib, text);
    }});
    initParetoComments();  // refresh visible textareas
    input.value = '';       // reset file picker
  }};
  reader.readAsText(file);
}}

var _paretoRendered = false;
window.addEventListener('load', () => {{ rebuildCharts(); initParetoComments(); resizeAllTables(); }});

// Listen to interval radio changes
document.querySelectorAll('input[name="interval"]').forEach(rb =>
  rb.addEventListener('change', rebuildCharts));
document.querySelectorAll('input[name="groupby"]').forEach(
  rb => rb.addEventListener('change', rebuildCharts));

// Listen to date mode radio changes
document.querySelectorAll('input[name="datemode"]').forEach(rb => rb.addEventListener('change', () => {{
  document.getElementById('custom-date-row').style.display =
    document.querySelector('input[name="datemode"]:checked')?.value === 'custom' ? 'block' : 'none';
  rebuildCharts();
}}));
document.getElementById('date-from').addEventListener('change', rebuildCharts);
document.getElementById('date-to').addEventListener('change', rebuildCharts);
</script>
</body>
</html>
'''
    output_path.write_text(html, encoding='utf-8')
    print(f'Wrote interactive report: {output_path}')


# ============================================================================
# 8. CLI
# ============================================================================

def main():
    ap = argparse.ArgumentParser(description='Interactive iBin Fail vs. Yield Trend')
    ap.add_argument('csv', help='Input CSV file')
    ap.add_argument('--cfg', default='',
                    help='Product config JSON (auto-detected if omitted)')
    ap.add_argument('--interval', choices=INTERVALS, default='weekly')
    ap.add_argument('--topn',   type=int,   default=8)
    ap.add_argument('--thresh', type=float, default=0.0)
    ap.add_argument('--group',  choices=['wafer', 'lot'], default='lot',
                    help='Histogram grouping: lot (default) = one bar per lot, wafer = one bar per wafer')
    ap.add_argument('--out',    default='',
                    help='Output HTML path (default: <csv>_trend.html)')
    args = ap.parse_args()

    if not HAVE_PLOTLY:
        print('ERROR: plotly not installed.  Run: pip install plotly', file=sys.stderr)
        sys.exit(1)

    csv_path = Path(args.csv).resolve()
    if not csv_path.exists():
        print(f'ERROR: file not found: {csv_path}', file=sys.stderr)
        sys.exit(1)

    # Load product config
    cfg, cfg_path = None, ''
    cfg_src = args.cfg or ''
    if not cfg_src:
        runs_preview = load_csv(csv_path, grouping_mode=args.group)
        drs = runs_preview[0].get('devrevstep', '') if runs_preview else ''
        auto = _find_auto_config(drs)
        if auto:
            cfg_src = str(auto)
            print(f'Auto-detected product config: {auto.name}')
    if cfg_src and Path(cfg_src).exists():
        cfg      = load_product_config(cfg_src)
        cfg_path = cfg_src
        print(f'Loaded product config: {Path(cfg_src).name}')
    else:
        print('No product config - ibin names and targets not shown.')

    print(f'Loading {csv_path} ... (grouping mode: {args.group})')
    runs = load_csv(csv_path, log=lambda s: print(s, end=''), grouping_mode=args.group)
    print(f'Loaded {len(runs)} run(s).')

    groups = group_runs(runs, args.interval)
    print(f'Grouped into {len(groups)} {args.interval} period(s).')

    print('Building charts ...')
    trend_fig  = build_trend_chart(groups, top_n_fail_ibins=args.topn,
                                    fail_thresh_pct=args.thresh,
                                    interval=args.interval, cfg=cfg)
    pareto_fig = build_pareto_chart(runs, top_n=20, cfg=cfg)
    pareto_vertical_fig, pareto_table_rows = build_pareto_vertical_chart(runs, top_n=20, cfg=cfg)

    out_path = (Path(args.out).resolve() if args.out
                else csv_path.parent / (csv_path.stem + '_trend.html'))
    generate_html(csv_path, groups, runs, trend_fig, pareto_fig, out_path,
                  interval=args.interval, top_n=args.topn, cfg_path=cfg_path, cfg=cfg,
                  pareto_vertical_fig=pareto_vertical_fig,
                  pareto_table_rows=pareto_table_rows,
                  grouping_mode=args.group)


if __name__ == '__main__':
    main()
