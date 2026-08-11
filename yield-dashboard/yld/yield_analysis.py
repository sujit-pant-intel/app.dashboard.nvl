"""yield_analysis.py — Merged wafer / bin / heatmap analysis.

Consolidates: bin_distribution_html, generate_heatmap_from_csv,
  generate_bin_wafer_heatmaps, generate_plots_from_csv, generate_flowchart

Cross-group dependency: imports _filter_lot_wafer (shared) and yield_data.
Lazy imports of csv_utils are updated to yield_data.
"""
from __future__ import annotations

# ════════════════════════════════════════════════════════════════
# (formerly bin_distribution_html.py)
# ════════════════════════════════════════════════════════════════
import os
import sys
from pathlib import Path
import re as _re
import pandas as pd

# ── Shared filter-by-Lot/Wafer dependency (lives in shared/utilities/dashboard_common/) ──
def _find_repo_root(start: Path) -> Path:
    """Walk up from `start` to the ancestor containing a shared/ dir (robust to repo reshuffles)."""
    current = Path(start).resolve()
    for _ in range(12):
        if (current / "shared").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return current

_DASHBOARD_COMMON = str(_find_repo_root(Path(__file__).parent) / "shared" / "utilities" / "dashboard_common")
if _DASHBOARD_COMMON not in sys.path:
    sys.path.insert(0, _DASHBOARD_COMMON)

from _filter_lot_wafer import FILTER_TABLE_CSS, FILTER_DD_JS, make_filter_js


def generate(data_path, out_dir=None, tbl_path=None):
    data_csv = Path(data_path)

    # ── Column detection (header only) ──────────────────────────────────────
    encoding = None
    for _enc in ('utf-8-sig', 'utf-16', 'latin-1'):
        try:
            _tmp = pd.read_csv(data_csv, nrows=0, encoding=_enc)
            all_cols = list(_tmp.columns)
            encoding = _enc
            break
        except Exception:
            continue
    else:
        all_cols = []

    col = next((c for c in all_cols
                if 'INTERFACE_BIN' in c.upper() and 'TOTAL' not in c.upper()),
               None)
    if not col:
        print('No INTERFACE_BIN column found in CSV')
        return

    fb_col = next((c for c in all_cols
                   if 'FUNCTIONAL_BIN' in c.upper() and 'TOTAL' not in c.upper()),
                  None)

    sdt_ib_col   = next((c for c in all_cols if 'INTERFACE_TOTAL_BIN' in c.upper()), None)
    sdt_fb_col_sdt = next((c for c in all_cols if 'FUNCTIONAL_TOTAL_BIN' in c.upper()), None)
    sdt_desc_col = next((c for c in all_cols if 'CTRL_UB_X_K_SDTBIN' in c.upper()), None)

    # ── Accumulate bin counts (vectorized) ────────────────────────────────
    bin_counts: dict = {}
    total = 0

    _bc_df = pd.read_csv(data_csv, usecols=[col], encoding=encoding, low_memory=False)
    total = len(_bc_df)
    # Extract first numeric value from each cell (covers "1", "01", "B1" etc.)
    _bc_series = _bc_df[col].astype(str).str.extract(r'(\d+)', expand=False)
    _bc_vc = _bc_series.dropna().value_counts()
    bin_counts = {str(k): int(v) for k, v in _bc_vc.items()}
    # Count empty/missing
    _bc_empty = int(_bc_series.isna().sum())
    if _bc_empty:
        bin_counts[''] = _bc_empty
    del _bc_df, _bc_series, _bc_vc

    # Prepare sorted labels (numeric sort)
    def _sort_key(x):
        try:
            return (0, int(x))
        except Exception:
            return (1, str(x))

    labels = sorted(list(bin_counts.keys()), key=_sort_key)
    values = [(bin_counts.get(l, 0) / total) * 100 for l in labels]

    out_dir = Path(out_dir) if out_dir else data_csv.parent / 'output'
    out_dir.mkdir(parents=True, exist_ok=True)
    html_out = out_dir / f"{data_csv.stem}_BinDistribution.html"

    # ── HTML: embedded plot image + HTML table ───────────────────────────────
    # Load FAIL BUCKET table
    def _load_fail_bucket_table():
        _tbl = Path(tbl_path) if tbl_path else None
        if _tbl is not None and _tbl.suffix.lower() == '.json' and _tbl.exists():
            try:
                import json as _json
                _jdata = _json.loads(_tbl.read_text(encoding='utf-8'))
                _yt = _jdata.get('yield_targets', _jdata) if isinstance(_jdata, dict) else _jdata
                rows = []
                for entry in _yt:
                    if not isinstance(entry, dict):
                        continue
                    rows.append((
                        str(entry.get('bin', '')),
                        str(entry.get('fail_bucket', '')),
                        str(entry.get('yield', entry.get('expected_yield_percent', ''))),
                    ))
                if rows:
                    return rows
            except Exception:
                pass
        return [
            ('1/2',                                           'SDS FF yield',                    '67.8'),
            ('1/2/3/4',                                       'SDS FF+DF yield',                 '86.0'),
            ('1',                                             'SDS FF (No Repair) yield',        '60.0'),
            ('2',                                             'MBIST Repair',                    '11.8'),
            ('3/4',                                           'Recovery (Defeatured)',            '17.5'),
            ('3',                                             'Recovery (Atom Defeatured)',        '9.0'),
            ('4',                                             'Recovery (Core Defeatured)',        '9.0'),
            ('41/42/47/76/77/81/82',                          'SCAN (post-recovery)',              '5.0'),
            ('20/21/33/60/61/62/63/65',                       'ARRAY MBIST (post-recovery)',       '3.1'),
            ('11/13/16/25/27/28/32/36/39/46/48/51/64/71/74/75', 'ANALOG (post-recovery)',          '0.3'),
            ('7/8/9/10/15/18/43',                             'TPI (Foundry)',                    '1.9'),
            ('31/88/91/94/97/98/99 + 93',                     'TPI (Bump/DiePrep/Test)',          '1.1'),
            ('19/35',                                         'RESET',                            '1.1'),
            ('12/44/45/70/80/85/86',                          'Functional',                       '0.8'),
            ('26',                                            'HVQK',                             '0.5'),
        ]

    pct_map = {k: (v / total) * 100 for k, v in bin_counts.items()}
    def _compute_pct(bin_field):
        return sum(pct_map.get(n, 0.0) for n in _re.findall(r"\d+", bin_field))

    raw_rows = _load_fail_bucket_table()

    # ── Load FB descriptions from bindef CSV ────────────────────────────────
    # The bindef CSV has rows like "FB101,DESCRIPTION_TEXT" or "B/C,DESCRIPTION"
    _fb_desc_map: dict = {}  # fb_number_str -> description string
    try:
        _bm_paths = []
        if tbl_path:
            _bm_paths.append(Path(tbl_path))
        # Also search for *_bindef.csv near the CSV and out_dir
        _bm_out = Path(out_dir) if out_dir else data_csv.parent / 'output'
        for _bm_search in filter(None, [_bm_out, data_csv.parent]):
            for _bm_cand in Path(_bm_search).glob('*_bindef.csv'):
                if _bm_cand not in _bm_paths:
                    _bm_paths.append(_bm_cand)
            for _bm_cand in Path(_bm_search).glob('*bindef*.csv'):
                if _bm_cand not in _bm_paths:
                    _bm_paths.append(_bm_cand)
        for _bm_p in _bm_paths:
            if _bm_p.exists() and _bm_p.suffix.lower() == '.csv':
                try:
                    _bd_df = pd.read_csv(_bm_p, encoding='utf-8', header=0,
                                         on_bad_lines='skip')
                    if _bd_df.shape[1] >= 2:
                        for _, _bd_row in _bd_df.iterrows():
                            _bd_key = str(_bd_row.iloc[0]).strip()
                            _bd_val = str(_bd_row.iloc[1]).strip()
                            # Match FB<num> entries
                            _bd_m = _re.match(r'^FB(\d+)$', _bd_key, _re.IGNORECASE)
                            if _bd_m:
                                _fb_desc_map[_bd_m.group(1)] = _bd_val
                    if _fb_desc_map:
                        break
                except Exception:
                    pass
    except Exception:
        pass

    # Build FB descriptions dict: desc from bindef, category = IB's fail bucket
    # (category is the same as the IB that the FB maps to, resolved at JS runtime)
    _fb_descriptions: dict = {}  # fb_number_str -> {"desc": "..."}
    for _fb_k, _fb_v in _fb_desc_map.items():
        _fb_descriptions[_fb_k] = {'desc': str(_fb_v)}

    # ── Load fB93xx (handler/skip bin) descriptions from product config JSON ──
    try:
        import json as _json93
        import re as _re93
        _yt_path93 = Path(tbl_path) if tbl_path else None
        if _yt_path93 and _yt_path93.exists() and _yt_path93.suffix.lower() == '.json':
            _raw93 = _yt_path93.read_text(encoding='utf-8')
            _jdata93 = None
            try:
                _jdata93 = _json93.loads(_raw93)
            except Exception:
                pass
            _fb93xx_list = []
            if _jdata93 and isinstance(_jdata93, dict):
                _fb93xx_list = _jdata93.get('fB93xx', [])
            if not _fb93xx_list:
                _m93 = _re93.search(r'"fB93xx"\s*:\s*(\[.*?\])', _raw93, _re93.DOTALL)
                if _m93:
                    try:
                        _fb93xx_list = _json93.loads(_m93.group(1))
                    except Exception:
                        pass
            for _e93 in _fb93xx_list:
                if isinstance(_e93, dict) and 'FB' in _e93 and 'description' in _e93:
                    _fb_descriptions[str(_e93['FB'])] = {'desc': str(_e93['description'])}
            # Pass-Bin-Map: {"101": {"cat": "...", "desc": "..."}, ...} overrides bindef + fB93xx
            _pbm = _jdata93.get('Pass-Bin-Map', {}) if _jdata93 else {}
            for _pbm_k, _pbm_v in _pbm.items():
                if isinstance(_pbm_v, dict):
                    _entry = {}
                    if 'desc' in _pbm_v:
                        _entry['desc'] = str(_pbm_v['desc'])
                    if 'cat' in _pbm_v:
                        _entry['cat'] = str(_pbm_v['cat'])
                    if _entry:
                        _fb_descriptions[str(_pbm_k)] = _entry
    except Exception:
        pass

    table_rows = []  # (bin, yield_pct_str, expected_str, fail_bucket, over_target)
    for row in raw_rows:
        bin_field = row[0]
        fail_bucket = row[1] if len(row) > 1 else ''
        expected_str = row[2] if len(row) > 2 else ''
        pct = _compute_pct(bin_field)
        pct_str = f"{pct:.1f}%"
        # Highlight logic: over target when pct > expected, bin doesn't contain '1'
        over = False
        try:
            if expected_str:
                exp_val = float(expected_str.replace('%', ''))
                nums = _re.findall(r"\d+", bin_field)
                has_one = any(n == '1' for n in nums)
                has_group = '1/2/3/4' in bin_field.replace(' ', '')
                if (not has_one) and (not has_group) and pct > exp_val:
                    over = True
        except Exception:
            pass
        table_rows.append((bin_field, pct_str, expected_str, fail_bucket, over))

    # Info row values — detect columns from the already-sniffed header, then
    # load only those columns (tiny read) to retrieve their distinct values.
    prog_col  = next((c for c in all_cols if 'program' in c.lower()), None)
    lot_col   = (next((c for c in all_cols if c.lower() == 'sort_lot'), None) or
                 next((c for c in all_cols if 'lot' in c.lower() and 'slot' not in c.lower()), None))
    wafer_col = (next((c for c in all_cols if c.upper() == 'SORT_WAFER'), None) or
                 next((c for c in all_cols if c.lower() == 'sort_wafer'), None) or
                 next((c for c in all_cols if 'sort_wafer' in c.lower()), None) or
                 next((c for c in all_cols if 'wafer' in c.lower() and 'partial' not in c.lower()), None))
    mat_col   = (next((c for c in all_cols if c.lower() == 'material type'), None) or
                 next((c for c in all_cols if 'material type' in c.lower()), None) or
                 next((c for c in all_cols if 'material' in c.lower()), None))
    date_col  = (next((c for c in all_cols if 'end_date' in c.lower()), None) or
                 next((c for c in all_cols if 'start_date' in c.lower()), None) or
                 next((c for c in all_cols if 'date' in c.lower()), None))
    # ── UPM / die-coordinate column detection ───────────────────────────────
    _x_col = next((c for c in all_cols if c == 'SORT_X'), None)
    _y_col = next((c for c in all_cols if c == 'SORT_Y'), None)
    _upm_raw_cols = [c for c in all_cols if 'UPM' in c.upper()]
    def _upm_label(cn):
        m = _re.search(r'_(\d{4})_MED', cn)
        if m:
            return f'{int(m.group(1))}mV'
        return cn.split('_')[0]
    # Read upmInfo from product config JSON to get MHz→% divisors
    _upm_divisors = {}  # col_name -> divisor (e.g. 9154 MHz)
    _upm_display_names = {}  # col_name -> display name from JSON
    _recov_ibin_config = {}  # {ib_str: {'trackerSide': 'ap'/'cr', 'hardFail': True/False}}
    if tbl_path:
        try:
            import json as _json_pc
            _pc_txt = Path(tbl_path).read_text(encoding='utf-8', errors='ignore')
            _pc_data = _json_pc.loads(_pc_txt)
            # ── Recovery analysis per-iBin config ─────────────────────────
            _ra_cfg = _pc_data.get('recovAnalysis', {})
            _recov_ibin_config = {str(k): v for k, v in _ra_cfg.get('ibinConfig', {}).items()}
            for _ui_e in _pc_data.get('upmInfo', []):
                if len(_ui_e) >= 3 and _ui_e[2]:
                    _ui_name = str(_ui_e[0])
                    _ui_pat = str(_ui_e[1])
                    _ui_div = float(_ui_e[2])
                    # Use ordered-token matching (same as sicc_processor)
                    _toks = [t for t in _ui_pat.split('*') if t]
                    for _uc in _upm_raw_cols:
                        _uc_up = _uc.upper()
                        _pos = 0
                        _ok = True
                        for _tk in _toks:
                            _idx = _uc_up.find(_tk.upper(), _pos)
                            if _idx < 0:
                                _ok = False
                                break
                            _pos = _idx + len(_tk)
                        if _ok:
                            _upm_divisors[_uc] = _ui_div
                            _upm_display_names[_uc] = _ui_name
            if not _upm_divisors:
                pass  # no match — will fall back to all UPM cols
        except Exception as _e_pc:
            pass  # silently ignore product config read errors
    else:
        pass  # no tbl_path — UPM divisors not applied
    # Only keep UPM columns that have a matched divisor from upmInfo.
    # If no upmInfo matched anything, fall back to all UPM columns.
    if _upm_divisors:
        _upm_filtered = [c for c in _upm_raw_cols if c in _upm_divisors]
    else:
        _upm_filtered = _upm_raw_cols
    _upm_col_defs = [{'col': c, 'key': f'u{i}',
                      'label': _upm_display_names.get(c, _upm_label(c)),
                      'div': _upm_divisors.get(c)}
             for i, c in enumerate(_upm_filtered)]
    # Single UPM Med column: prefer 950mV, then any SDS, then first available
    _upm_med_col = (
        next((d for d in _upm_col_defs if '950' in d['label']), None)
        or next((d for d in _upm_col_defs if 'SDS' in d['label'].upper()), None)
        or (_upm_col_defs[0] if _upm_col_defs else None)
    )
    # Info cols + HW breakdown will be derived from the main _df_ic read below
    # to avoid redundant CSV reads.
    _info_cols = [c for c in (prog_col, lot_col, wafer_col) if c]
    prog_val = ''
    lot_val = ''
    wafer_cnt = ''


    # --- Hardware Commonality Breakdown ---
    # Fields to use
    hw_fields = [
      next((c for c in all_cols if c.lower().startswith('sort partial wafer id')), None),
      next((c for c in all_cols if c.lower().startswith('cell id')), None),
      next((c for c in all_cols if c.lower().startswith('unit tester id')), None),
      next((c for c in all_cols if c.lower().startswith('unit tester site id')), None),
      next((c for c in all_cols if c.lower().startswith('unit tiu')), None),
      next((c for c in all_cols if c.lower().startswith('thermal head id')), None),
    ]
    hw_fields = [c for c in hw_fields if c]
    hw_breakdown = {}

    def _esc(s):
      return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    trows_html = ''
    for bin_field, pct_str, expected_str, fail_bucket, over in table_rows:
      style = ' style="color:red;font-weight:bold"' if over else ''
      trows_html += (
        f'<tr><td>{_esc(bin_field)}</td>'
        f'<td>{_esc(fail_bucket)}</td>'
        f'<td class="num"{style}>{_esc(pct_str)}</td>'
        f'<td class="num">{_esc(expected_str)}</td></tr>\n'
      )

    # --- Hardware breakdown HTML for modal (injected by JS) ---
    def hw_breakdown_html(bin_val):
      if bin_val not in hw_breakdown or hw_breakdown[bin_val] is None:
        return '<div class="hw-section"><b>No hardware breakdown data available.</b></div>'
      df = hw_breakdown[bin_val]
      if df.empty:
        return '<div class="hw-section"><b>No hardware breakdown data available.</b></div>'
      cols = hw_fields + ['count', 'percent']
      ths = ''.join(f'<th>{_esc(c)}</th>' for c in cols)
      trs = ''
      for _, row in df.iterrows():
        tds = ''.join(f'<td>{_esc(row[c])}</td>' for c in hw_fields)
        tds += f'<td class="num">{int(row["count"])}</td><td class="num">{row["percent"]:.2f}%</td>'
        trs += f'<tr>{tds}</tr>'
      return f'<div class="hw-section"><h4>Hardware Commonality Breakdown</h4><table class="stbl"><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table></div>'

    # Save hardware breakdown as a JS-accessible object (for modal injection)
    _hw_breakdown_json = {k: v.to_dict(orient='records') for k, v in hw_breakdown.items() if v is not None}

    # ── Interactive histogram HTML ──────────────────────────────────────────
    import json as _json_ic
    from collections import OrderedDict as _OD_ic

    # Build per-wafer data so JS can filter/aggregate dynamically
    _ic_group_cols = [c for c in [prog_col, lot_col, wafer_col] if c]
    # HW fields (already resolved above as hw_fields list)
    _upm_extra = ([_x_col] if _x_col else []) + ([_y_col] if _y_col else []) + [d['col'] for d in _upm_col_defs]
    _sdt_extra_cols = [c for c in [sdt_ib_col, sdt_fb_col_sdt, sdt_desc_col] if c]
    _ic_load_cols = list(dict.fromkeys(_ic_group_cols + ([mat_col] if mat_col else []) + ([date_col] if date_col else []) + [col] + ([fb_col] if fb_col else []) + hw_fields + _upm_extra + _sdt_extra_cols))
    _ic_rows = []
    try:
        _df_ic = pd.read_csv(data_csv, usecols=_ic_load_cols, encoding=encoding, low_memory=False)
        # Derive info values from this single read (avoids separate CSV load)
        if prog_col and prog_col in _df_ic.columns:
            prog_val = ','.join(str(x) for x in _df_ic[prog_col].dropna().unique())
        if lot_col and lot_col in _df_ic.columns:
            lot_val = ','.join(str(x) for x in _df_ic[lot_col].dropna().unique())
        if wafer_col and wafer_col in _df_ic.columns:
            wafer_cnt = str(len(_df_ic[wafer_col].dropna().unique()))
        _df_ic['_ib'] = pd.to_numeric(
            _df_ic[col].astype(str).str.extract(r'(\d+)', expand=False), errors='coerce')
        # Build hw_breakdown from same dataframe (avoids separate CSV load)
        if hw_fields:
            _hw_avail = [c for c in hw_fields if c in _df_ic.columns]
            if _hw_avail and '_ib' in _df_ic.columns:
                _valid_hwb = _df_ic[_df_ic['_ib'].notna()].copy()
                if not _valid_hwb.empty:
                    for _hc in _hw_avail:
                        _valid_hwb[_hc] = _valid_hwb[_hc].fillna('').astype(str)
                    _hwb_grp = _valid_hwb.groupby(['_ib'] + _hw_avail).size().reset_index(name='count')
                    _hwb_ib_totals = _valid_hwb.groupby('_ib').size()
                    for _bv, _bg in _hwb_grp.groupby('_ib'):
                        _bvs = str(int(_bv))
                        _bg2 = _bg[_hw_avail + ['count']].copy()
                        _bg2['percent'] = _bg2['count'] / int(_hwb_ib_totals[_bv]) * 100
                        hw_breakdown[_bvs] = _bg2
        if fb_col:
            _df_ic['_fb'] = pd.to_numeric(
                _df_ic[fb_col].astype(str).str.extract(r'(\d+)', expand=False), errors='coerce')
        if _ic_group_cols:
            for _keys_ic, _gdf_ic in _df_ic.groupby(_ic_group_cols):
                if not isinstance(_keys_ic, tuple):
                    _keys_ic = (_keys_ic,)
                _kd_ic = dict(zip(_ic_group_cols, _keys_ic))
                _bc_ic = {str(int(k)): int(v)
                          for k, v in _gdf_ic['_ib'].dropna().astype(int).value_counts().items()}
                # Build IB→FB cross-tab for this wafer group
                _ib_fb_ic = {}
                if fb_col and '_fb' in _gdf_ic.columns:
                    _valid_ic = _gdf_ic[_gdf_ic['_ib'].notna() & _gdf_ic['_fb'].notna()]
                    for (_ib_v, _fb_v), _cnt_v in _valid_ic.groupby(['_ib', '_fb']).size().items():
                        _ib_s = str(int(_ib_v))
                        _fb_s = str(int(_fb_v))
                        _ib_fb_ic.setdefault(_ib_s, {})[_fb_s] = int(_cnt_v)
                # Build IB→HW combo index cross-tab for this wafer group
                _ib_hw_ic = {}
                if hw_fields:
                    _valid_hw = _gdf_ic[_gdf_ic['_ib'].notna()]
                    if not _valid_hw.empty:
                        _hw_vals = _valid_hw[hw_fields].fillna('').values.astype(str)
                        _ib_arr_hw = _valid_hw['_ib'].values.astype(int)
                        _hk_arr = ['\x1f'.join(row) for row in _hw_vals]
                        # Single pass: group by (ib, hk) using dict
                        _ib_hk_counts = {}
                        for _i_hw in range(len(_ib_arr_hw)):
                            _key = (_ib_arr_hw[_i_hw], _hk_arr[_i_hw])
                            _ib_hk_counts[_key] = _ib_hk_counts.get(_key, 0) + 1
                        for (_ibv_hw, _hk_hw), _cnt_hw in _ib_hk_counts.items():
                            _ib_hw_ic.setdefault(str(_ibv_hw), {})[_hk_hw] = _cnt_hw
                _mat_ic = str(_gdf_ic[mat_col].iloc[0]) if mat_col and not _gdf_ic[mat_col].dropna().empty else ''
                _date_ic = ''
                if date_col and date_col in _gdf_ic.columns:
                    _dv_s = _gdf_ic[date_col].dropna()
                    if not _dv_s.empty:
                        _dv_str = str(_dv_s.iloc[0])
                        _dm = _re.match(r'(\d{4}-\d{2}-\d{2})', _dv_str)
                        _date_ic = _dm.group(1) if _dm else _dv_str[:20]
                # SDT Bins aggregation (filter: SDS IB <= 4; group by unique combo, count)
                _sdt_bins_ic = []
                if sdt_ib_col:
                    _sdt_filt = _gdf_ic[_gdf_ic['_ib'].notna() & (_gdf_ic['_ib'] <= 4)]
                    if not _sdt_filt.empty:
                        _sdt_filt = _sdt_filt.copy()
                        _sdt_keys = ['_ib']
                        if '_fb' in _sdt_filt.columns:
                            _sdt_keys.append('_fb')
                        if sdt_ib_col in _sdt_filt.columns:
                            _sdt_filt['_sdt_ib'] = pd.to_numeric(
                                _sdt_filt[sdt_ib_col].astype(str).str.extract(r'(\d+)', expand=False), errors='coerce')
                            _sdt_keys.append('_sdt_ib')
                        if sdt_fb_col_sdt and sdt_fb_col_sdt in _sdt_filt.columns:
                            _sdt_filt['_sdt_fb'] = pd.to_numeric(
                                _sdt_filt[sdt_fb_col_sdt].astype(str).str.extract(r'(\d+)', expand=False), errors='coerce')
                            _sdt_keys.append('_sdt_fb')
                        if sdt_desc_col and sdt_desc_col in _sdt_filt.columns:
                            _sdt_filt['_sdt_desc'] = _sdt_filt[sdt_desc_col].fillna('').astype(str).str.strip()
                            _sdt_keys.append('_sdt_desc')
                        for _sdt_key_g, _sdt_cnt_g in _sdt_filt.groupby(_sdt_keys, dropna=False).size().items():
                            if not isinstance(_sdt_key_g, tuple):
                                _sdt_key_g = (_sdt_key_g,)
                            _sdt_kd = dict(zip(_sdt_keys, _sdt_key_g))
                            _sdt_bins_ic.append([
                                int(_sdt_kd['_ib']) if pd.notna(_sdt_kd.get('_ib')) else None,
                                int(_sdt_kd['_fb']) if '_fb' in _sdt_kd and pd.notna(_sdt_kd.get('_fb')) else None,
                                int(_sdt_kd['_sdt_ib']) if '_sdt_ib' in _sdt_kd and pd.notna(_sdt_kd.get('_sdt_ib')) else None,
                                int(_sdt_kd['_sdt_fb']) if '_sdt_fb' in _sdt_kd and pd.notna(_sdt_kd.get('_sdt_fb')) else None,
                                str(_sdt_kd.get('_sdt_desc', '')),
                                int(_sdt_cnt_g),
                            ])
                # Per-die data for UPM heatmap: [x, y, ib, fb, u0]
                _dies_ic = []
                if _x_col and _y_col and _x_col in _gdf_ic.columns and _y_col in _gdf_ic.columns:
                    import numpy as _np_die
                    _die_cols = [_x_col, _y_col, '_ib']
                    _has_fb_col = '_fb' in _gdf_ic.columns
                    if _has_fb_col:
                        _die_cols.append('_fb')
                    _upm_present = [d['col'] for d in _upm_col_defs if d['col'] in _gdf_ic.columns]
                    _upm_present_divs = [d.get('div') for d in _upm_col_defs if d['col'] in _gdf_ic.columns]
                    _die_cols += _upm_present
                    _die_arr = _gdf_ic[_die_cols].to_numpy(dtype=float)
                    # Per-die SDT IB/FB arrays for SDT bin interactive analysis
                    _sdt_ib_die = None
                    _sdt_fb_die_arr = None
                    if sdt_ib_col and sdt_ib_col in _gdf_ic.columns:
                        _sdt_ib_die = pd.to_numeric(
                            _gdf_ic[sdt_ib_col].astype(str).str.extract(r'(\d+)', expand=False),
                            errors='coerce').to_numpy(dtype=float)
                    if sdt_fb_col_sdt and sdt_fb_col_sdt in _gdf_ic.columns:
                        _sdt_fb_die_arr = pd.to_numeric(
                            _gdf_ic[sdt_fb_col_sdt].astype(str).str.extract(r'(\d+)', expand=False),
                            errors='coerce').to_numpy(dtype=float)
                    _upm_start = 4 if _has_fb_col else 3
                    _n_upm = len(_upm_present)
                    # Build per-die HW combo key array (string, replaced with int index after _hw_combo_seen is built)
                    _hw_key_arr = None
                    if hw_fields:
                        _hw_vals_die = _gdf_ic[hw_fields].fillna('').astype(str).to_numpy()
                        _hw_key_arr = ['\x1f'.join(row) for row in _hw_vals_die]
                    # Vectorized UPM conversion: apply divisors
                    if _n_upm > 0:
                        _upm_block = _die_arr[:, _upm_start:_upm_start + _n_upm].copy()
                        for _ui in range(_n_upm):
                            _div = _upm_present_divs[_ui]
                            if _div:
                                _upm_block[:, _ui] = _np_die.round(_upm_block[:, _ui] / _div * 100, 2)
                        _die_arr[:, _upm_start:_upm_start + _n_upm] = _upm_block
                    # Convert to list-of-lists: [x, y, ib, fb, hw, upm0, upm1, ...]
                    # hw at index 4 (string key now, replaced with int later); upm always starts at index 5
                    _nan_mask = _np_die.isnan(_die_arr)
                    _n_pad = len(_upm_col_defs) - _n_upm
                    for _ri in range(len(_die_arr)):
                        _row_v = _die_arr[_ri]
                        _entry = []
                        # x(0), y(1), ib(2) — always int
                        for _ci in range(3):
                            _entry.append(None if _nan_mask[_ri, _ci] else int(_row_v[_ci]))
                        # fb(3) — int or None
                        if _has_fb_col:
                            _entry.append(None if _nan_mask[_ri, 3] else int(_row_v[3]))
                        else:
                            _entry.append(None)
                        # hw(4) — string key placeholder, converted to int index in second pass
                        _entry.append(_hw_key_arr[_ri] if _hw_key_arr is not None else None)
                        # upm values (5+)
                        for _ci in range(_upm_start, _upm_start + _n_upm):
                            _entry.append(None if _nan_mask[_ri, _ci] else float(_row_v[_ci]))
                        if _n_pad:
                            _entry += [None] * _n_pad
                        # sdt ib, sdt fb (always appended; index = 5 + len(_upm_col_defs) and +1)
                        _entry.append(None if _sdt_ib_die is None or _np_die.isnan(_sdt_ib_die[_ri]) else int(_sdt_ib_die[_ri]))
                        _entry.append(None if _sdt_fb_die_arr is None or _np_die.isnan(_sdt_fb_die_arr[_ri]) else int(_sdt_fb_die_arr[_ri]))
                        _dies_ic.append(_entry)
                _upm_med_ic = []
                if _upm_med_col:
                    _uc_med = _upm_med_col['col']
                    if _uc_med in _gdf_ic.columns:
                        _umed_v = pd.to_numeric(_gdf_ic[_uc_med], errors='coerce').median()
                        _div_med = _upm_med_col.get('div')
                        if _div_med and not pd.isna(_umed_v):
                            _umed_v = round(float(_umed_v) / _div_med * 100, 2)
                        _upm_med_ic = [None if pd.isna(_umed_v) else round(float(_umed_v), 2)]
                    else:
                        _upm_med_ic = [None]
                _ic_rows.append({
                    'program':  str(_kd_ic.get(prog_col, '')),
                    'lot':      str(_kd_ic.get(lot_col, '')),
                    'wafer':    str(_kd_ic.get(wafer_col, '')),
                    'material': _mat_ic,
                    'date':     _date_ic,
                    'binCounts': _bc_ic,
                    'ibToFb':   _ib_fb_ic,
                    'ibToHw':   _ib_hw_ic,
                    'total':    int(len(_gdf_ic)),
                    'dies':     _dies_ic,
                    'sdtBins':  _sdt_bins_ic,
                    'upmMed':   _upm_med_ic,
                })
        else:
            _bc_all = {str(int(k)): int(v) for k, v in
                       _df_ic['_ib'].dropna().astype(int).value_counts().items()}
            _ib_fb_all = {}
            if fb_col and '_fb' in _df_ic.columns:
                _valid_all = _df_ic[_df_ic['_ib'].notna() & _df_ic['_fb'].notna()]
                for (_ib_v, _fb_v), _cnt_v in _valid_all.groupby(['_ib', '_fb']).size().items():
                    _ib_s = str(int(_ib_v))
                    _fb_s = str(int(_fb_v))
                    _ib_fb_all.setdefault(_ib_s, {})[_fb_s] = int(_cnt_v)
            # Build dies data for fallback case (when no group-by columns)
            _dies_fallback = []
            if _x_col and _y_col and _x_col in _df_ic.columns and _y_col in _df_ic.columns:
                import numpy as _np_die_fb
                _die_cols_fb = [_x_col, _y_col, '_ib']
                _has_fb_col_fb = '_fb' in _df_ic.columns
                if _has_fb_col_fb:
                    _die_cols_fb.append('_fb')
                _upm_present_fb = [d['col'] for d in _upm_col_defs if d['col'] in _df_ic.columns]
                _upm_present_divs_fb = [d.get('div') for d in _upm_col_defs if d['col'] in _df_ic.columns]
                _die_cols_fb += _upm_present_fb
                _die_arr_fb = _df_ic[_die_cols_fb].to_numpy(dtype=float)
                _sdt_ib_die_fb = None
                _sdt_fb_die_arr_fb = None
                if sdt_ib_col and sdt_ib_col in _df_ic.columns:
                    _sdt_ib_die_fb = pd.to_numeric(
                        _df_ic[sdt_ib_col].astype(str).str.extract(r'(\d+)', expand=False),
                        errors='coerce').to_numpy(dtype=float)
                if sdt_fb_col_sdt and sdt_fb_col_sdt in _df_ic.columns:
                    _sdt_fb_die_arr_fb = pd.to_numeric(
                        _df_ic[sdt_fb_col_sdt].astype(str).str.extract(r'(\d+)', expand=False),
                        errors='coerce').to_numpy(dtype=float)
                _upm_start_fb = 4 if _has_fb_col_fb else 3
                _n_upm_fb = len(_upm_present_fb)
                _hw_key_arr_fb = None
                if hw_fields:
                    _hw_vals_die_fb = _df_ic[hw_fields].fillna('').astype(str).to_numpy()
                    _hw_key_arr_fb = ['\x1f'.join(row) for row in _hw_vals_die_fb]
                if _n_upm_fb > 0:
                    _upm_block_fb = _die_arr_fb[:, _upm_start_fb:_upm_start_fb + _n_upm_fb].copy()
                    for _ui_fb in range(_n_upm_fb):
                        _div_fb = _upm_present_divs_fb[_ui_fb]
                        if _div_fb:
                            _upm_block_fb[:, _ui_fb] = _np_die_fb.round(_upm_block_fb[:, _ui_fb] / _div_fb * 100, 2)
                    _die_arr_fb[:, _upm_start_fb:_upm_start_fb + _n_upm_fb] = _upm_block_fb
                _nan_mask_fb = _np_die_fb.isnan(_die_arr_fb)
                _n_pad_fb = len(_upm_col_defs) - _n_upm_fb
                for _ri_fb in range(len(_die_arr_fb)):
                    _row_v_fb = _die_arr_fb[_ri_fb]
                    _entry_fb = []
                    for _ci_fb in range(3):
                        _entry_fb.append(None if _nan_mask_fb[_ri_fb, _ci_fb] else int(_row_v_fb[_ci_fb]))
                    if _has_fb_col_fb:
                        _entry_fb.append(None if _nan_mask_fb[_ri_fb, 3] else int(_row_v_fb[3]))
                    else:
                        _entry_fb.append(None)
                    _entry_fb.append(_hw_key_arr_fb[_ri_fb] if _hw_key_arr_fb is not None else None)
                    for _ci_fb in range(_upm_start_fb, _upm_start_fb + _n_upm_fb):
                        _entry_fb.append(None if _nan_mask_fb[_ri_fb, _ci_fb] else float(_row_v_fb[_ci_fb]))
                    if _n_pad_fb:
                        _entry_fb += [None] * _n_pad_fb
                    _entry_fb.append(None if _sdt_ib_die_fb is None or _np_die_fb.isnan(_sdt_ib_die_fb[_ri_fb]) else int(_sdt_ib_die_fb[_ri_fb]))
                    _entry_fb.append(None if _sdt_fb_die_arr_fb is None or _np_die_fb.isnan(_sdt_fb_die_arr_fb[_ri_fb]) else int(_sdt_fb_die_arr_fb[_ri_fb]))
                    _dies_fallback.append(_entry_fb)
            _upm_med_fb = []
            if _upm_med_col:
                _uc_med_fb = _upm_med_col['col']
                if _uc_med_fb in _df_ic.columns:
                    _umed_v_fb = pd.to_numeric(_df_ic[_uc_med_fb], errors='coerce').median()
                    _div_med_fb = _upm_med_col.get('div')
                    if _div_med_fb and not pd.isna(_umed_v_fb):
                        _umed_v_fb = round(float(_umed_v_fb) / _div_med_fb * 100, 2)
                    _upm_med_fb = [None if pd.isna(_umed_v_fb) else round(float(_umed_v_fb), 2)]
                else:
                    _upm_med_fb = [None]
            _ic_rows = [{'program': prog_val, 'lot': lot_val, 'wafer': 'all',
                         'material': '', 'binCounts': _bc_all, 'ibToFb': _ib_fb_all, 'total': int(total),
                         'dies': _dies_fallback if _dies_fallback else None, 'upmMed': _upm_med_fb}]
    except Exception as _e_ic:
        _ic_rows = [{'program': prog_val, 'lot': lot_val, 'wafer': 'all',
                     'material': '',
                     'binCounts': {k: v for k, v in bin_counts.items() if str(k).isdigit()},
                     'ibToFb': {}, 'ibToHw': {}, 'total': int(total), 'dies': None, 'upmMed': []}]


    # Build unique HW combo table from ibToHw data (must run AFTER _ic_rows is populated)
    _hw_combo_seen: dict = {}
    for _r_bh in _ic_rows:
        for _ib_bh, _hw_map_bh in (_r_bh.get('ibToHw') or {}).items():
            for _hk_bh in _hw_map_bh:
                if _hk_bh not in _hw_combo_seen:
                    _hw_combo_seen[_hk_bh] = len(_hw_combo_seen)
    # Replace string keys with integer indices in each row's ibToHw and die array hw slot (index 4)
    for _r_bh in _ic_rows:
        _new_ibhw: dict = {}
        for _ib_bh, _hw_map_bh in (_r_bh.get('ibToHw') or {}).items():
            _new_ibhw[_ib_bh] = {str(_hw_combo_seen[_hk_bh]): _cnt_bh
                                  for _hk_bh, _cnt_bh in _hw_map_bh.items()
                                  if _hk_bh in _hw_combo_seen}
        _r_bh['ibToHw'] = _new_ibhw
        # Replace hw string keys in die array index 4 with integer combo indices
        if _hw_combo_seen:
            for _d in (_r_bh.get('dies') or []):
                if len(_d) > 4 and isinstance(_d[4], str):
                    _d[4] = _hw_combo_seen.get(_d[4])  # int or None
    # Build combo table list [{col: val, ...}, ...]
    _hw_combo_table_bh: list = []
    if hw_fields and _hw_combo_seen:
        for _hk_bh in sorted(_hw_combo_seen, key=lambda k: _hw_combo_seen[k]):
            _vals_bh = _hk_bh.split('\x1f')
            _hw_combo_table_bh.append(dict(zip(hw_fields, _vals_bh)))

    _ic_all_bins = sorted(
        set(b for r in _ic_rows for b in r['binCounts'].keys()),
        key=lambda x: int(x) if x.isdigit() else 9999
    )
    _IC_PALETTE = ['#3498db', '#27ae60', '#e67e22', '#9b59b6', '#e74c3c',
                   '#1abc9c', '#f39c12', '#2980b9', '#16a085', '#d35400',
                   '#8e44ad', '#c0392b']
    _ic_bucket_colors: dict = {}
    _ic_bin_colors: dict = {}
    _ic_bin_buckets: dict = {}
    for _ib_ic in _ic_all_bins:
        _bkt_ic = ''
        for _rr_ic in raw_rows:
            if any(_ib_ic == t for t in _re.findall(r'\d+', str(_rr_ic[0]))):
                _bkt_ic = str(_rr_ic[1]) if len(_rr_ic) > 1 else ''
                break
        _ic_bin_buckets[_ib_ic] = _bkt_ic
        _bk = _bkt_ic or 'Other'
        if _bk not in _ic_bucket_colors:
            _ic_bucket_colors[_bk] = _IC_PALETTE[len(_ic_bucket_colors) % len(_IC_PALETTE)]
        _ic_bin_colors[_ib_ic] = _ic_bucket_colors[_bk]

    _ic_legend_groups: dict = _OD_ic()
    for _ib_ic in _ic_all_bins:
        _bk_lg = _ic_bin_buckets[_ib_ic] or 'Other'
        _ic_legend_groups.setdefault(_bk_lg, []).append(_ib_ic)

    _ic_yield_defs = [
        {'bins': row[0], 'bucket': row[1] if len(row) > 1 else '',
         'expected': row[2] if len(row) > 2 else '',
         'bins_list': _re.findall(r'\d+', str(row[0]))}
        for row in raw_rows
    ]
    # ── Reticle mapping (auto-discovered from collateral/reticle/ or shared/reticle/) ──
    _ret_map = {}         # "x,y" -> [rx, ry, shotIdx]
    _ret_shots = []       # [[xMin, yMin, xMax, yMax], ...] per shot in wafer die coords
    _ret_site_totals = {} # "rx,ry" -> count of unique shots containing that site
    _ret_site_num = {}    # "rx,ry" -> die-loc number (1-based) within one reticle field
    try:
        import glob as _glob
        _ret_candidates = []
        _search_base = data_csv.parent
        for _ in range(4):
            for _rpat in ['collateral/reticle/*.csv', 'collateral/Reticle/*.csv']:
                _ret_candidates.extend(_glob.glob(str(_search_base / _rpat)))
            _search_base = _search_base.parent
        # Also search relative to this script file (covers cases where data CSV is in temp/output dir)
        try:
            _script_base = Path(__file__).resolve().parent
            for _ in range(4):
                for _rpat in ['collateral/reticle/*.csv', 'collateral/Reticle/*.csv']:
                    _ret_candidates.extend(_glob.glob(str(_script_base / _rpat)))
                _script_base = _script_base.parent
        except Exception:
            pass
        # Also search shared/reticle/ under repo root (walk up until 'shared/' is found)
        try:
            _rb = Path(__file__).resolve().parent
            for _ in range(10):
                if (_rb / 'shared' / 'reticle').is_dir():
                    _ret_candidates.extend(_glob.glob(str(_rb / 'shared' / 'reticle' / '*.csv')))
                    break
                _rb = _rb.parent
        except Exception:
            pass
        _ret_candidates = [p for p in _ret_candidates if Path(p).is_file()
                           and 'reticle' in Path(p).name.lower()]
        # Filter by DevRevStep prefix so the correct layout is chosen (e.g. 8PF6CV for R0, 8PF5CV for L0).
        # Without this, alphabetically-first CSV wins and L0 (4-die) beats R0 (6-die).
        _drs_col = next((c for c in all_cols if c.lower().startswith('devrevstep')), None)
        _drs_prefix6 = ''
        if _drs_col:
            try:
                _drs_series = pd.read_csv(data_csv, usecols=[_drs_col], nrows=500,
                                          encoding=encoding, low_memory=False)[_drs_col]
                _drs_val = next((str(v).strip() for v in _drs_series.dropna() if str(v).strip()), '')
                _drs_prefix6 = _drs_val[:6].upper()
            except Exception:
                pass
        if _drs_prefix6 and _ret_candidates:
            _filtered = [p for p in _ret_candidates if _drs_prefix6 in Path(p).name.upper()]
            if _filtered:
                _ret_candidates = _filtered
                if os.getenv('YLD_DEBUG'):
                    print(f'Reticle: DevRevStep prefix {_drs_prefix6!r} -> {len(_filtered)} candidate(s)')
            else:
                if os.getenv('YLD_DEBUG'):
                    print(f'Reticle: no candidates match DevRevStep prefix {_drs_prefix6!r}, using all {len(_ret_candidates)} candidates')
        if _ret_candidates:
            _ret_csv_path = Path(_ret_candidates[0])
            _ret_df = pd.read_csv(_ret_csv_path)
            _rc = {c.lower().replace(' ', '').replace('_', ''): c for c in _ret_df.columns}
            _rdx = _rc.get('diex')
            _rdy = _rc.get('diey')
            _rrx = _rc.get('reticlediex')
            _rry = _rc.get('reticlediey')
            _rrs = _rc.get('reticleshot')
            _rrl = _rc.get('reticle')  # die-loc number within the reticle field
            if _rdx and _rdy and _rrx and _rry and _rrs:
                _cols_to_load = [_rdx, _rdy, _rrx, _rry, _rrs]
                if _rrl: _cols_to_load.append(_rrl)
                _ret_df2 = _ret_df[_cols_to_load].dropna().copy()
                _ret_df2[_rdx] = _ret_df2[_rdx].astype(int)
                _ret_df2[_rdy] = _ret_df2[_rdy].astype(int)
                _ret_df2[_rrx] = _ret_df2[_rrx].astype(int)
                _ret_df2[_rry] = _ret_df2[_rry].astype(int)
                # Compute SORT_X/SORT_Y offsets (same as apply_reticle_mapping.py)
                _ret_offset_x = round((_ret_df2[_rdx].min() + _ret_df2[_rdx].max()) / 2)
                _ret_offset_y = round((_ret_df2[_rdy].min() + _ret_df2[_rdy].max()) / 2)
                _ret_df2['_SX'] = (_ret_df2[_rdx] - _ret_offset_x).astype(int)
                _ret_df2['_SY'] = (_ret_df2[_rdy] - _ret_offset_y).astype(int)
                _shot_names = list(_ret_df2[_rrs].unique())
                _shot_to_idx = {s: i for i, s in enumerate(_shot_names)}
                _shot_bbox = {}
                for _, _rv in _ret_df2.iterrows():
                    _s = _rv[_rrs]; _dx2 = int(_rv['_SX']); _dy2 = int(_rv['_SY'])
                    if _s not in _shot_bbox:
                        _shot_bbox[_s] = [_dx2, _dy2, _dx2, _dy2]
                    else:
                        _b = _shot_bbox[_s]
                        _b[0] = min(_b[0], _dx2); _b[1] = min(_b[1], _dy2)
                        _b[2] = max(_b[2], _dx2); _b[3] = max(_b[3], _dy2)
                _ret_shots = [_shot_bbox[s] for s in _shot_names]
                for _, _rv in _ret_df2.iterrows():
                    _key = f"{int(_rv['_SX'])},{int(_rv['_SY'])}"
                    _ret_map[_key] = [int(_rv[_rrx]), int(_rv[_rry]),
                                      _shot_to_idx[_rv[_rrs]]]
                _site_shots_tmp = {}
                for _, _rv in _ret_df2.iterrows():
                    _sk2 = f"{int(_rv[_rrx])},{int(_rv[_rry])}"
                    _site_shots_tmp.setdefault(_sk2, set()).add(_rv[_rrs])
                _ret_site_totals = {k: len(v) for k, v in _site_shots_tmp.items()}
                # Build die-loc number lookup: "rx,ry" -> Reticle number (1-based)
                if _rrl:
                    for _, _rv in _ret_df2.iterrows():
                        _sk3 = f"{int(_rv[_rrx])},{int(_rv[_rry])}"
                        if _sk3 not in _ret_site_num:
                            try:
                                _ret_site_num[_sk3] = int(_rv[_rrl])
                            except (ValueError, TypeError):
                                pass
                if os.getenv('YLD_DEBUG'):
                    print(f'Reticle map loaded: {_ret_csv_path.name} ({len(_ret_shots)} shots, {len(_ret_map)} dies, {len(_ret_site_num)} die-locs, offsets={_ret_offset_x},{_ret_offset_y})')
    except Exception as _e_ret:
        print(f'Reticle map not loaded: {_e_ret}')

    _ic_fail_bins = [b for b in _ic_all_bins if b.isdigit() and int(b) > 4]

    # ── Pre-compute wafer pattern scores (default: all bins, IB≥5 fail threshold) ──
    import math as _math_ps
    for _ps_row in _ic_rows:
        _ps_dies = _ps_row.get('dies') or []
        if not _ps_dies:
            _ps_row['patScores'] = None
            continue
        _ps_xs = [d[0] for d in _ps_dies if d[0] is not None]
        if not _ps_xs:
            _ps_row['patScores'] = None
            continue
        _ps_ys = [d[1] for d in _ps_dies if d[0] is not None]
        _ps_xctr = (min(_ps_xs) + max(_ps_xs)) / 2
        _ps_yctr = (min(_ps_ys) + max(_ps_ys)) / 2
        _ps_xrad = (max(_ps_xs) - min(_ps_xs)) / 2 or 1
        _ps_yrad = (max(_ps_ys) - min(_ps_ys)) / 2 or 1
        _ps_fxn, _ps_fyn, _ps_fax, _ps_fay = [], [], [], []
        for _ps_d in _ps_dies:
            _ps_x, _ps_y, _ps_ib = _ps_d[0], _ps_d[1], _ps_d[2]
            if _ps_x is None or _ps_ib is None:
                continue
            if _ps_ib >= 5:
                _ps_fxn.append((_ps_x - _ps_xctr) / _ps_xrad)
                _ps_fyn.append((_ps_y - _ps_yctr) / _ps_yrad)
                _ps_fax.append(_ps_x)
                _ps_fay.append(_ps_y)
        _ps_n = len(_ps_fxn)
        if _ps_n == 0:
            _ps_row['patScores'] = {'center': 0.0, 'edge': 0.0, 'donut': 0.0,
                                    'systematic': 0.0, 'reticle': 0.0, 'random': 1.0, 'failDies': 0}
            continue
        _ps_radii = sorted(_math_ps.sqrt(x * x + y * y) for x, y in zip(_ps_fxn, _ps_fyn))
        _ps_zI = sum(1 for r in _ps_radii if r < 0.4)
        _ps_zM = sum(1 for r in _ps_radii if 0.4 <= r < 0.7)
        _ps_zO = sum(1 for r in _ps_radii if r >= 0.7)
        _ps_fI, _ps_fM, _ps_fO = _ps_zI / _ps_n, _ps_zM / _ps_n, _ps_zO / _ps_n
        _ps_cen = round(max(0.0, min(1.0, (_ps_fI - 0.16) / 0.4 + 0.5)), 2)
        _ps_edg = round(max(0.0, min(1.0, (_ps_fO - 0.51) / 0.3 + 0.5)), 2)
        _ps_don = round(max(0.0, min(1.0, (_ps_fM - 0.33) / 0.25 + 0.5 - (_ps_fI + _ps_fO) * 0.3)), 2)
        _ps_q = [0, 0, 0, 0]
        for _ps_xn, _ps_yn in zip(_ps_fxn, _ps_fyn):
            if _ps_xn >= 0 and _ps_yn >= 0: _ps_q[0] += 1
            elif _ps_xn < 0 and _ps_yn >= 0: _ps_q[1] += 1
            elif _ps_xn < 0 and _ps_yn < 0: _ps_q[2] += 1
            else: _ps_q[3] += 1
        _ps_syst = round(min(1.0, (max(_ps_q) - min(_ps_q)) / _ps_n * 2.5) * min(1.0, _ps_n / 20), 2)
        _ps_ru = 1 - abs(_ps_fI / 0.16 - 1) * 0.3 - abs(_ps_fO / 0.51 - 1) * 0.3
        _ps_rnd = round(max(0.0, min(1.0, _ps_ru * (1 - _ps_syst * 0.5))), 2)
        _ps_ret = 0.0
        if _ret_map and _ret_site_totals and _ps_fax:
            _ps_ss, _ps_sc2 = {}, {}
            for _ps_ax, _ps_ay in zip(_ps_fax, _ps_fay):
                _ps_rk = f'{int(_ps_ax)},{int(_ps_ay)}'
                _ps_info = _ret_map.get(_ps_rk)
                if not _ps_info:
                    continue
                _ps_sk = f'{_ps_info[0]},{_ps_info[1]}'
                _ps_ss.setdefault(_ps_sk, set()).add(str(_ps_info[2]))
                _ps_sc2[_ps_sk] = _ps_sc2.get(_ps_sk, 0) + 1
            if _ps_ss:
                _ps_tm, _ps_ws, _ps_mx = 0, 0.0, 0.0
                for _ps_sk, _ps_shots in _ps_ss.items():
                    _ps_ts = _ret_site_totals.get(_ps_sk, 1)
                    _ps_s = len(_ps_shots) / _ps_ts
                    _ps_c = _ps_sc2[_ps_sk]
                    _ps_tm += _ps_c
                    _ps_ws += _ps_s * _ps_c
                    if _ps_s > _ps_mx:
                        _ps_mx = _ps_s
                if _ps_tm:
                    _ps_ret = round(min(1.0, (_ps_ws / _ps_tm * 0.4 + _ps_mx * 0.6) * min(1.0, _ps_n / 15)), 2)
        _ps_row['patScores'] = {
            'center': _ps_cen, 'edge': _ps_edg, 'donut': _ps_don,
            'systematic': _ps_syst, 'reticle': _ps_ret, 'random': _ps_rnd,
            'failDies': _ps_n,
        }

    _ic_data_json = _json_ic.dumps({
        'bins': _ic_all_bins, 'total': int(total), 'rows': _ic_rows,
        'binColors': _ic_bin_colors, 'binBuckets': _ic_bin_buckets,
        'legendGroups': dict(_ic_legend_groups), 'yieldDefs': _ic_yield_defs,
        'failBins': _ic_fail_bins, 'hasMaterial': bool(mat_col), 'hasDate': bool(date_col), 'hasSdt': bool(sdt_ib_col),
        'hasSdtDie': bool(sdt_ib_col and _x_col and _y_col), 'sdtDieStart': 5 + len(_upm_col_defs),
        'hasFunctionalBin': bool(fb_col),
        'fbDescriptions': _fb_descriptions,
        'upmCols': [{'key': d['key'], 'label': d['label'], 'divisor': d.get('div')} for d in _upm_col_defs],
        'hasUpm': bool(_upm_col_defs and _x_col and _y_col),
        'hasUpmMed': bool(_upm_med_col),
        'upmStart': 5,
        'hasReticle': bool(_ret_map),
        'retMap': _ret_map,
        'retShots': _ret_shots,
        'retSiteTotals': _ret_site_totals,
        'retSiteNum': _ret_site_num,
    }, ensure_ascii=False)

    # ── Bin Recovery Analysis pre-computation ──────────────────────────────────
    # Detects LOGTRACKER_AP/CR/SLCE columns; decodes DEFLATE32 strings; builds per-
    # (lot|wafer, functional-bin) test pareto for IB 3 (ATOM) and IB 4 (Core).
    # Token format per spec: AP_ID | step_index | flag | test_instance_name
    _recov_data      = {}  # {lot|wafer_key: {fbin_str: [{test,total,byGroup}]}}
    _recov_tracked   = {}  # {lot|wafer_key: {fbin_str: N_dies_with_tracker_data}}
    _recov_groups    = {}  # {ib_str: [group_labels]}
    _recov_die_grps  = {}  # {lot|wafer_key: {"x|y": [group_names]}} — per-die group membership
    _recov_hard_fail = {}  # {ib_str: True/False}

    try:
        import zlib as _zlib_r
        _ap_cols_r   = sorted([c for c in all_cols if 'LOGTRACKER_AP' in c.upper()
                               and 'TRACKER_ATOM' not in c.upper()])
        _cr_cols_r   = sorted([c for c in all_cols if 'LOGTRACKER_CR' in c.upper()
                               and 'TRACKER_CORE' not in c.upper()])
        _slce_cols_r = sorted([c for c in all_cols if 'LOGTRACKER_SLCE' in c.upper()
                               and 'TRACKER_ATOM' not in c.upper()
                               and 'TRACKER_CORE' not in c.upper()])

        if (_ap_cols_r or _cr_cols_r or _slce_cols_r) and col and lot_col and wafer_col and fb_col:
            _r_load = list(dict.fromkeys(
                [col, lot_col, wafer_col, fb_col]
                + ([_x_col] if _x_col else [])
                + ([_y_col] if _y_col else [])
                + _ap_cols_r + _cr_cols_r + _slce_cols_r))
            _r_load = [c for c in _r_load if c in all_cols]

            try:
                _rdf = pd.read_csv(data_csv, usecols=_r_load, encoding=encoding, low_memory=False)
            except Exception:
                _rdf = pd.read_csv(data_csv, usecols=_r_load, encoding=encoding, low_memory=False)

            _all_tracker_cols = _ap_cols_r + _cr_cols_r + _slce_cols_r
            _rdf['_rib'] = pd.to_numeric(
                _rdf[col].astype(str).str.extract(r'(\d+)', expand=False), errors='coerce')
            # Keep only rows that have at least one DEFLATE32 value in any tracker column.
            # This is fully generic: no iBin list needed — the data itself drives which bins
            # appear in the analysis (iBin 1/2 passes have no tracker data, fail bins do).
            if _all_tracker_cols:
                _has_d32 = pd.Series(False, index=_rdf.index)
                for _tc0 in _all_tracker_cols:
                    if _tc0 in _rdf.columns:
                        _has_d32 |= _rdf[_tc0].astype(str).str.startswith('DEFLATE32_')
                _rdf = _rdf[_has_d32].copy()
            else:
                _rdf = _rdf.iloc[0:0].copy()  # no tracker cols → nothing to do

            if len(_rdf) > 0:
                # ── inline DEFLATE32 decoder — matches spec's deflate32_decode ──
                _D32C = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
                _D32M = {c: i for i, c in enumerate(_D32C)}

                def _d32r(s):
                    if not isinstance(s, str) or not s.startswith('DEFLATE32_'):
                        return str(s)
                    try:
                        enc = s[10:].strip('=')
                        bits = ''.join(bin(_D32M[c])[2:].zfill(5) for c in enc if c in _D32M)
                        bits += '0' * (8 - len(bits) % 8)
                        raw = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
                        return _zlib_r.decompress(raw, -8).decode('utf-8')
                    except Exception:
                        return ''

                def _ff(decoded):
                    """Extract first non-TRACKERCLEAR test from decoded LOGTRACKER string.
                    Format per spec: AP_ID|step_index|flag|test_instance_name (newline-separated).
                    Splits by | on full string — test_instance_name contains :: and no TRACKERCLEAR.
                    """
                    for tok in decoded.split('|'):
                        tok = tok.strip()
                        if tok and '::' in tok and 'TRACKERCLEAR' not in tok:
                            return tok
                    return None

                # ── group label extraction — matches spec's split approach ───
                def _grp_label(col_name):
                    """col.split('LOGTRACKER_')[1].split('_')[0]  → 'AP2', 'CR3', etc."""
                    try:
                        return col_name.split('LOGTRACKER_')[1].split('_')[0]
                    except (IndexError, AttributeError):
                        return None

                # ── vectorised: decode all tracker columns at once ──────────
                for _tc in _all_tracker_cols:
                    if _tc in _rdf.columns:
                        _rdf[f'__f_{_tc}'] = _rdf[_tc].apply(
                            lambda v: _ff(_d32r(v)) if isinstance(v, str) else None)

                # ── Build _recov_groups dynamically per-iBin ─────────────────
                # For each iBin present in the data, detect which AP/CR groups
                # have at least one non-None decoded value → those are the column
                # headers for that bin's analysis table.  Fully generic: no iBin
                # list needed; new bins appear automatically when they have data.
                # ── per-iBin tracker side helper (uses product config if available) ─
                def _rib_cols_hf(rib_str):
                    """Return (tracker_cols, is_hard_fail) for this iBin.
                    Uses recovAnalysis.ibinConfig from the product JSON when available;
                    falls back to all tracker cols with hard_fail=False."""
                    cfg = _recov_ibin_config.get(rib_str, {})
                    side = cfg.get('trackerSide', 'auto')
                    hf   = bool(cfg.get('hardFail', False))
                    if side == 'ap':
                        return _ap_cols_r, hf
                    elif side == 'cr':
                        return _cr_cols_r, hf
                    elif side == 'slce':
                        return _slce_cols_r, hf
                    else:
                        return _all_tracker_cols, hf

                _recov_groups    = {}
                _recov_hard_fail = {}  # {ib_str: True/False}
                for _rib_val, _rib_sub in _rdf.groupby('_rib'):
                    try:
                        _rib_str = str(int(_rib_val))
                    except (ValueError, TypeError):
                        continue
                    _use_tc_g, _is_hf_g = _rib_cols_hf(_rib_str)
                    _recov_hard_fail[_rib_str] = _is_hf_g
                    _active = sorted({
                        _grp_label(_tc)
                        for _tc in _use_tc_g
                        if f'__f_{_tc}' in _rib_sub.columns
                        and _rib_sub[f'__f_{_tc}'].notna().any()
                        and _grp_label(_tc)
                    })
                    if _active:
                        _recov_groups[_rib_str] = _active

                def _mkpareto(df_sub, tracker_cols, per_die=False):
                    """Return list of {test, total, byGroup} sorted by total desc.
                    per_die=True: total = unique die count per test (matches hard_fail=True in
                      reference implementation).  per_die=False: per-tracker-occurrence counting.
                    """
                    if per_die:
                        # Per-die: each die counted at most once per test across all groups
                        _die_tests = {}  # index → {test: set_of_groups}
                        for _tc2 in tracker_cols:
                            _fc2 = f'__f_{_tc2}'
                            if _fc2 not in df_sub.columns:
                                continue
                            _gn = _grp_label(_tc2)
                            if not _gn:
                                continue
                            for _idx2, _t2 in df_sub[_fc2].dropna().items():
                                if _idx2 not in _die_tests:
                                    _die_tests[_idx2] = {}
                                if _t2 not in _die_tests[_idx2]:
                                    _die_tests[_idx2][_t2] = set()
                                _die_tests[_idx2][_t2].add(_gn)
                        if not _die_tests:
                            return []
                        _tt, _tg = {}, {}
                        for _idx2, _tests in _die_tests.items():
                            for _t2, _grps in _tests.items():
                                _tt[_t2] = _tt.get(_t2, 0) + 1
                                if _t2 not in _tg:
                                    _tg[_t2] = {}
                                for _g in _grps:
                                    _tg[_t2][_g] = _tg[_t2].get(_g, 0) + 1
                        return sorted(
                            [{'test': _tn, 'total': _tt[_tn], 'byGroup': _tg.get(_tn, {})}
                             for _tn in _tt],
                            key=lambda x: -x['total'])
                    else:
                        rows_p = []
                        for _tc2 in tracker_cols:
                            _fc2 = f'__f_{_tc2}'
                            if _fc2 not in df_sub.columns:
                                continue
                            _gn = _grp_label(_tc2)
                            if not _gn:
                                continue
                            for _t2, _n2 in df_sub[_fc2].dropna().value_counts().items():
                                rows_p.append({'t': _t2, 'g': _gn, 'n': int(_n2)})
                        if not rows_p:
                            return []
                        _tmp = pd.DataFrame(rows_p)
                        _piv = _tmp.groupby(['t', 'g'])['n'].sum().unstack(fill_value=0)
                        _piv['_tot'] = _piv.sum(axis=1)
                        _piv = _piv.sort_values('_tot', ascending=False)
                        return [{'test': _tn, 'total': int(_tr['_tot']),
                                 'byGroup': {_g: int(_tr[_g]) for _g in _piv.columns
                                             if _g != '_tot' and _tr.get(_g, 0) > 0}}
                                for _tn, _tr in _piv.iterrows()]

                # ── helper: count dies with ≥1 non-None decoded AP and CR tracker ─
                _ap_decode_cols   = [f'__f_{_tc}' for _tc in _ap_cols_r]
                _cr_decode_cols   = [f'__f_{_tc}' for _tc in _cr_cols_r]
                _slce_decode_cols = [f'__f_{_tc}' for _tc in _slce_cols_r]

                def _count_tracked(df_sub):
                    """Returns {'ap': N_ap, 'cr': N_cr, 'slce': N_slce} — unique dies with AP/CR/SLCE data.
                    Tracked separately so each section can use the right denominator."""
                    _fc_ap   = [c for c in _ap_decode_cols   if c in df_sub.columns]
                    _fc_cr   = [c for c in _cr_decode_cols   if c in df_sub.columns]
                    _fc_slce = [c for c in _slce_decode_cols if c in df_sub.columns]
                    n_ap   = int(df_sub[_fc_ap].notnull().any(axis=1).sum())   if _fc_ap   else 0
                    n_cr   = int(df_sub[_fc_cr].notnull().any(axis=1).sum())   if _fc_cr   else 0
                    n_slce = int(df_sub[_fc_slce].notnull().any(axis=1).sum()) if _fc_slce else 0
                    return {'ap': n_ap, 'cr': n_cr, 'slce': n_slce}

                # ── per (lot|wafer, ib, fbin) ────────────────────────────────────
                # Include iBin in groupby so we can look up the correct tracker cols
                # and per_die flag for each iBin.
                for (_lv, _wv, _ibv, _fbv), _wfg in _rdf.groupby([lot_col, wafer_col, '_rib', fb_col]):
                    _wkey = f'{_lv}|{_wv}'
                    try:
                        _rib_str = str(int(float(_ibv)))
                        _fbstr = str(int(float(_fbv)))
                    except (ValueError, TypeError):
                        continue
                    _use_tc, _is_hf = _rib_cols_hf(_rib_str)
                    _pareto = _mkpareto(_wfg, _use_tc, per_die=_is_hf)
                    _ntrk = _count_tracked(_wfg)
                    if _pareto:
                        _recov_data.setdefault(_wkey, {})[_fbstr] = _pareto
                    if _ntrk['ap'] > 0 or _ntrk['cr'] > 0 or _ntrk.get('slce', 0) > 0:
                        _recov_tracked.setdefault(_wkey, {})[_fbstr] = _ntrk

                # ── aggregate all-wafers entry ───────────────────────────────
                _recov_data['all']    = {}
                _recov_tracked['all'] = {}
                for (_ibv2, _fbv2), _ffg in _rdf.groupby(['_rib', fb_col]):
                    try:
                        _rib_str2 = str(int(float(_ibv2)))
                        _fbstr2 = str(int(float(_fbv2)))
                    except (ValueError, TypeError):
                        continue
                    _use_tc2, _is_hf2 = _rib_cols_hf(_rib_str2)
                    _par2  = _mkpareto(_ffg, _use_tc2, per_die=_is_hf2)
                    _ntrk2 = _count_tracked(_ffg)
                    if _par2:
                        _recov_data['all'][_fbstr2] = _par2
                    if _ntrk2['ap'] > 0 or _ntrk2['cr'] > 0 or _ntrk2.get('slce', 0) > 0:
                        _recov_tracked['all'][_fbstr2] = _ntrk2

                _n3 = sum(1 for k, v in _recov_data.items()
                          if k != 'all' for fb, rows in v.items() if rows)
                if _n3 > 0 and os.getenv('YLD_DEBUG'):
                    print(f'Recovery analysis: {len(_ap_cols_r)} AP cols, {len(_cr_cols_r)} CR cols, '
                          f'{len(_slce_cols_r)} SLCE cols, '
                          f'{len(_rdf)} recovery dies, {_n3} wafer×fbin entries built')
                # ── per-die group membership for exact heatmap filtering ──
                if _x_col and _y_col and _x_col in _rdf.columns and _y_col in _rdf.columns:
                    _dg_fc_pairs = [(f'__f_{_tc_d}', _grp_label(_tc_d))
                                    for _tc_d in _all_tracker_cols
                                    if f'__f_{_tc_d}' in _rdf.columns and _grp_label(_tc_d)]
                    for (_lv_d, _wv_d), _wg_d in _rdf.groupby([lot_col, wafer_col]):
                        _wkey_d = f'{_lv_d}|{_wv_d}'
                        _dg = {}
                        for _fc_d, _gn_d in _dg_fc_pairs:
                            _mask_d = _wg_d[_fc_d].notna()
                            if not _mask_d.any():
                                continue
                            _xs_d = _wg_d.loc[_mask_d, _x_col].values
                            _ys_d = _wg_d.loc[_mask_d, _y_col].values
                            for _xi_d, _yi_d in zip(_xs_d, _ys_d):
                                try:
                                    _dk_d = f'{int(_xi_d)}|{int(_yi_d)}'
                                except (ValueError, TypeError):
                                    continue
                                if _dk_d not in _dg:
                                    _dg[_dk_d] = []
                                if _gn_d not in _dg[_dk_d]:
                                    _dg[_dk_d].append(_gn_d)
                        if _dg:
                            _recov_die_grps[_wkey_d] = _dg
    except Exception as _e_recov:
        print(f'Recovery pre-computation skipped: {_e_recov}')

    # ── Bin Description analysis ───────────────────────────────────────────────
    # Uses the 'Bin Description_*' DLCP column which encodes the exact bin-setter
    # test for every fail die (100% coverage for all fail bins; 0% for pass bins).
    # Parsed format: B{DataBin}_FAIL_{TP1}_{TP2}_{TEST...}_{suffix}
    #   → TP1_TP2::TEST...   e.g. SCN_ATOM::STUCKAT_ATOM_SB_K_BEGIN_N_VATOM_NOM_LFM_OCC
    _bindesc_data = {}  # {wafer_key: {fb_str: [{test, total}]}}
    _bindesc_ibs  = {}  # {ib_str: True} — iBins that have Bin Description data

    try:
        import re as _re_bd
        _bd_candidates = [c for c in all_cols
                          if c.lower().startswith('bin description') or
                             c.lower().startswith('bin_description')]
        if _bd_candidates and lot_col and wafer_col and col and fb_col:
            # Pick the column with the most non-null values
            _bd_counts = {}
            for _bdc in _bd_candidates:
                try:
                    _s = pd.read_csv(data_csv, usecols=[_bdc], encoding=encoding, low_memory=False)[_bdc]
                    _bd_counts[_bdc] = int(_s.notna().sum())
                except Exception:
                    _bd_counts[_bdc] = 0
            _bd_col = max(_bd_counts, key=lambda c: _bd_counts[c])

            if _bd_counts[_bd_col] > 0:
                _bd_load = list(dict.fromkeys([lot_col, wafer_col, col, fb_col, _bd_col]))
                _bd_load = [c for c in _bd_load if c in all_cols]
                try:
                    _bddf = pd.read_csv(data_csv, usecols=_bd_load, encoding=encoding, low_memory=False)
                except Exception:
                    _bddf = pd.read_csv(data_csv, usecols=_bd_load, encoding=encoding, low_memory=False)

                def _parse_bd(val):
                    """'B42340010_FAIL_SCN_ATOM_STUCKAT_ATOM_SB_K_BEGIN_N_VATOM_NOM_LFM_OCC_2'
                       → 'SCN_ATOM::STUCKAT_ATOM_SB_K_BEGIN_N_VATOM_NOM_LFM_OCC'
                    """
                    if not isinstance(val, str):
                        return None
                    for _marker in ('_FAIL_', '_PASS_'):
                        _mi = val.find(_marker)
                        if _mi >= 0:
                            _rest = val[_mi + len(_marker):]
                            _rest = _re_bd.sub(r'_\d+$', '', _rest)
                            _parts = _rest.split('_', 2)
                            if len(_parts) >= 3:
                                return _parts[0] + '_' + _parts[1] + '::' + _parts[2]
                            return _rest
                    return None

                _bddf['_bdtest'] = _bddf[_bd_col].map(_parse_bd)
                _bddf = _bddf.dropna(subset=['_bdtest'])
                _bddf['_brib'] = pd.to_numeric(
                    _bddf[col].astype(str).str.extract(r'(\d+)', expand=False), errors='coerce')
                _bddf = _bddf.dropna(subset=['_brib'])

                # Per (lot, wafer, iBin, fBin)
                for (_lv, _wv, _ibv, _fbv), _grp in \
                        _bddf.groupby([lot_col, wafer_col, '_brib', fb_col]):
                    _wkey = f'{_lv}|{_wv}'
                    try:
                        _rib_str = str(int(float(_ibv)))
                        _fbstr   = str(int(float(_fbv)))
                    except (ValueError, TypeError):
                        continue
                    _vc   = _grp['_bdtest'].value_counts()
                    _rows = [{'test': str(_t), 'total': int(_n)} for _t, _n in _vc.items()]
                    if _rows:
                        _bindesc_data.setdefault(_wkey, {})[_fbstr] = _rows
                        _bindesc_ibs[_rib_str] = True

                # All-wafers aggregate
                _bindesc_data['all'] = {}
                for (_ibv2, _fbv2), _grp2 in _bddf.groupby(['_brib', fb_col]):
                    try:
                        _rib_str2 = str(int(float(_ibv2)))
                        _fbstr2   = str(int(float(_fbv2)))
                    except (ValueError, TypeError):
                        continue
                    _vc2   = _grp2['_bdtest'].value_counts()
                    _rows2 = [{'test': str(_t), 'total': int(_n)} for _t, _n in _vc2.items()]
                    if _rows2:
                        _bindesc_data['all'][_fbstr2] = _rows2

                _n_bd = sum(1 for k, v in _bindesc_data.items()
                            if k != 'all' for fb, r in v.items() if r)
                if os.getenv('YLD_DEBUG'):
                    print(f'Bin Description: {len(_bddf)} dies, {len(_bindesc_ibs)} iBins, '
                          f'{_n_bd} wafer×fbin entries')
    except Exception as _e_bd:
        print(f'Bin Description analysis skipped: {_e_bd}')

    import json as _json_r
    _recov_data_json      = _json_r.dumps(_recov_data,      ensure_ascii=False)
    _recov_die_grps_json  = _json_r.dumps(_recov_die_grps or None, ensure_ascii=False)
    _recov_tracked_json   = _json_r.dumps(_recov_tracked,   ensure_ascii=False)
    _recov_groups_json    = _json_r.dumps(_recov_groups,    ensure_ascii=False)
    _recov_hard_fail_json = _json_r.dumps(_recov_hard_fail, ensure_ascii=False)
    _bindesc_data_json    = _json_r.dumps(_bindesc_data,    ensure_ascii=False)
    _bindesc_ibs_json     = _json_r.dumps(_bindesc_ibs,     ensure_ascii=False)
    _has_recovery = bool(_recov_data) or bool(_bindesc_data)

    # Build HTML in parts to keep CSS/JS {} away from Python f-string expansion
    _html_head = (
        '<!doctype html>\n<html>\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<title>Yield Distribution \u2014 Interactive</title>\n'
        '<style>\n'
        '*{box-sizing:border-box;margin:0;padding:0}\n'
        'body{font-family:Arial,sans-serif;font-size:14px;background:#f0f2f5;color:#2c3e50}\n'
        '.pw{padding:10px 10px;max-width:none;width:100%;margin:0}\n'
        '.ib{display:flex;flex-wrap:wrap;gap:8px;padding:8px 12px;background:#2c3e50;'
        'color:#ecf0f1;border-radius:6px;margin-bottom:10px;font-size:13px}\n'
        '.ib b{color:#f1c40f}\n'
        '.mr{display:flex;gap:10px;margin-bottom:10px}\n'
        '.cp{flex:1;min-width:0;background:#fff;border-radius:6px;padding:10px;'
        'box-shadow:0 1px 4px rgba(0,0,0,.1)}\n'
        '.ctr{display:flex;align-items:baseline;gap:8px;margin-bottom:6px}\n'
        '.ct{font-size:15px;font-weight:bold;color:#2c3e50}\n'
        '.si{font-size:13px;color:#7f8c8d}\n'
        '.hs{width:100%;display:block}\n'
        '.lp{width:360px;flex-shrink:0;background:#fff;border-radius:6px;padding:10px;'
        'box-shadow:0 1px 4px rgba(0,0,0,.1);overflow-y:auto;max-height:480px}\n'
        '.lh{font-size:14px;font-weight:bold;color:#2c3e50;margin-bottom:4px;'
        'display:flex;justify-content:space-between;align-items:center}\n'
        '.lb{display:flex;gap:4px}\n'
        '.lhi{font-size:12px;color:#95a5a6;margin-bottom:6px}\n'
        '.lg-search{width:100%;box-sizing:border-box;padding:4px 8px;font-size:12px;border:1px solid #cdd5e0;border-radius:3px;margin-bottom:6px;outline:none}\n'
        '.lg-search:focus{border-color:#3498db}\n'
        '.lg{margin-bottom:10px}\n'
        '.lbk{font-size:12px;font-weight:bold;color:#7f8c8d;text-transform:uppercase;'
        'letter-spacing:.5px;padding:2px 4px;border-bottom:1px solid #eee;margin-bottom:4px;'
        'cursor:pointer;border-radius:3px}\n'
        '.lbk:hover{background:#f0f0f0}\n'
        '.li{display:grid;grid-template-columns:12px minmax(0,1fr) 84px;align-items:flex-start;column-gap:7px;padding:5px 6px;cursor:pointer;'
        'border-radius:3px;transition:background .1s;user-select:none}\n'
        '.li:hover{background:#f0f4ff}\n'
        '.la{background:#eaf2ff}\n'
        '.ld{width:10px;height:10px;border-radius:50%;flex-shrink:0;border:1px solid rgba(0,0,0,.12);margin-top:4px}\n'
        '.lt{flex:1;min-width:0;display:flex;flex-direction:column}\n'
        '.ln{font-size:13px;font-weight:bold;color:#2c3e50;white-space:nowrap}\n'
        '.ldesc{font-size:12px;color:#6b7785;line-height:1.25;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n'
        '.lmeta{font-size:12px;color:#7f8c8d;white-space:nowrap;text-align:right;line-height:1.3;padding-left:6px;min-width:84px}\n'
        '.fy-row{display:flex;flex-wrap:nowrap;gap:10px;align-items:flex-start;margin-bottom:10px}\n'
        '.yp{background:#fff;border:1px solid #ccd;border-radius:5px;box-shadow:0 1px 4px rgba(0,0,0,.1);display:flex;flex-direction:column;min-width:0}\n'
        '.yp-bar{display:flex;justify-content:space-between;align-items:center;background:#2c3e50;color:#ecf0f1;padding:5px 10px;border-radius:4px 4px 0 0;cursor:pointer;user-select:none;flex-shrink:0}\n'
        '.yp-ttl{font-size:12px;font-weight:bold;flex:1;margin-right:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n'
        '.yp-btns{display:flex;gap:4px;flex-shrink:0;align-items:center}\n'
        '.yp-btn{background:none;border:1px solid rgba(255,255,255,.35);color:#ecf0f1;cursor:pointer;font-size:11px;padding:1px 7px;border-radius:3px;line-height:1.4;white-space:nowrap}\n'
        '.yp-btn:hover{background:rgba(255,255,255,.15)}\n'
        '.yp-body{overflow:auto;min-height:30px;box-sizing:border-box;flex:1}\n'
        '.yp-body.yp-col{display:none}\n'
        '.yp.yp-max{position:fixed;top:8px;left:8px;right:8px;bottom:8px;z-index:10000;margin:0}\n'
        '.yp.yp-max .yp-body{height:calc(100% - 36px);overflow:auto;resize:none}\n'
        '.wm-inline{background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.12);margin-bottom:10px;overflow:hidden}\n'
        '.wm-inline-hdr{background:#145a32;color:#fff;font-size:12px;font-weight:bold;padding:6px 14px;display:flex;align-items:center;justify-content:space-between;cursor:pointer;user-select:none}\n'
        '.wm-inline-body{padding:8px}\n'
        '.wm-inline-maps{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-start}\n'
        '.wm-inline-card{text-align:center;cursor:pointer}\n'
        '.wm-inline-card:hover .wm-inline-lbl{text-decoration:underline}\n'
        '.wm-inline-lbl{font-size:11px;font-weight:bold;color:#2c3e50;margin-bottom:2px}\n'
        '.wm-inline-tag{font-size:10px;font-weight:bold;margin-top:2px}\n'
        '.fs{background:#fff;border-radius:6px;padding:10px;'
        'box-shadow:0 1px 4px rgba(0,0,0,.1);flex:0 1 47%;max-width:47%;min-width:0}\n'
        '.fh{display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap}\n'
        '.ft{font-size:15px;font-weight:bold;color:#2c3e50}\n'
        '.ri{font-size:13px;color:#7f8c8d}\n'
        + FILTER_TABLE_CSS
        +
        '.num{text-align:right}\n'
        '.ys{background:#fff;border-radius:6px;padding:10px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex:1 1 53%;min-width:0;overflow-y:auto;overflow-x:hidden;max-height:calc(100vh - 320px)}\n'
        '.yt{font-size:15px;font-weight:bold;color:#2c3e50;margin-bottom:8px}\n'
        '.ytbl{border-collapse:collapse;font-size:13px;width:100%;table-layout:fixed}\n'
        '.ytbl th{background:#2c3e50;color:#ecf0f1;padding:6px 12px;text-align:left;position:sticky;top:0;z-index:1}\n'
        '.ytbl td{padding:5px 12px;border-bottom:1px solid #dde;text-align:left;vertical-align:middle}\n'
        '.ytbl th,.ytbl td{overflow-wrap:anywhere;word-break:break-word}\n'
        '.ytbl th:nth-child(1),.ytbl td:nth-child(1){width:14%}\n'
        '.ytbl th:nth-child(2),.ytbl td:nth-child(2){width:38%}\n'
        '.ytbl th:nth-child(3),.ytbl td:nth-child(3),.ytbl th:nth-child(4),.ytbl td:nth-child(4),.ytbl th:nth-child(5),.ytbl td:nth-child(5){width:16%}\n'
        '.ytbl tr:nth-child(even) td{background:#eaf0fb}\n'
        '.ytbl tr:hover td{background:#d6eaff}\n'
        '.ytbl tr.ysel td{background:#b3d4ff!important;font-weight:bold}\n'
        '.ytbl tr.yclickable{cursor:pointer}\n'
        '.yg{color:#1f7a3f;font-weight:bold}\n'
        '.yr{color:#c0392b;font-weight:bold}\n'
        '.yn{color:#7f8c8d}\n'
        '.cb{padding:3px 10px;font-size:12px;cursor:pointer;border:1px solid #bdc3c7;'
        'background:#ecf0f1;border-radius:3px;color:#2c3e50}\n'
        '.cb:hover{background:#d5dbde}\n'
        '.pps{background:#fff;border-radius:6px;padding:10px;box-shadow:0 1px 4px rgba(0,0,0,.1);margin-bottom:10px}\n'
        '.sh{font-size:15px;font-weight:bold;color:#2c3e50;margin-bottom:8px}\n'
        '.stbl{border-collapse:collapse;font-size:12px}\n'
        '.stbl th{background:#2c3e50;color:#ecf0f1;padding:6px 12px;text-align:left}\n'
        '.stbl td{padding:5px 12px;border-bottom:1px solid #dde}\n'
        '.stbl tr:nth-child(even) td{background:#eaf0fb}\n'
        '.stbl tr:hover td{background:#d6eaff}\n'
        '.sdt-tbl{border-collapse:collapse;font-size:12px;width:auto}\n'
        '.sdt-tbl th{background:#2c3e50;color:#ecf0f1;padding:5px 8px;text-align:left;white-space:nowrap}\n'
        '.sdt-tbl td{padding:4px 8px;border-bottom:1px solid #dde;white-space:nowrap;text-align:left}\n'
        '.sdt-tbl tr:nth-child(even) td{background:#eaf0fb}\n'
        '.sdt-tbl tr:hover td{background:#d6eaff}\n'
        '.sdt-tbl .num{text-align:right}\n'
        '.fb-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:transparent;z-index:10000;pointer-events:none}\n'
        '.fb-overlay.open{display:block}\n'
        '.fb-modal{background:#fff;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.25);width:50vw;height:50vh;max-width:92vw;max-height:90vh;display:flex;flex-direction:column;padding:0;position:fixed;top:60px;left:50%;transform:translateX(-50%);resize:both;overflow:hidden;min-width:320px;min-height:200px;pointer-events:auto}\n'
        '.fb-modal-inner{flex:1;overflow-y:auto;padding:8px 24px 16px;min-height:0}\n'
        '.fb-drag{cursor:move;background:#2c3e50;color:#fff;padding:8px 14px;border-radius:8px 8px 0 0;display:flex;align-items:center;justify-content:space-between;user-select:none;flex-shrink:0}\n'
        '.fb-chart{width:100%;display:block}\n'
        '.fb-tbl{border-collapse:collapse;font-size:12px;width:100%;margin-top:12px}\n'
        '.fb-tbl th{background:#2c3e50;color:#ecf0f1;padding:5px 10px;text-align:left}\n'
        '.fb-tbl td{padding:4px 10px;border-bottom:1px solid #eee}\n'
        '.fb-tbl tr:nth-child(even) td{background:#f7f9fc}\n'
        '.fb-tbl .num{text-align:right}\n'
        '.fb-wm-sec{margin-top:12px}\n'
        '.fb-wm-sec{margin-top:12px}\n'
        '.fb-wm-ttl{font-size:13px;font-weight:bold;color:#2c3e50;margin-bottom:6px}\n'
        '.fb-wm-grid{display:flex;flex-wrap:wrap;gap:6px;max-height:240px;overflow-y:auto;padding:6px;background:#f4f7fb;border-radius:6px;border:1px solid #d0d8e8}\n'
        '.fb-wm-tile{min-width:85px;padding:5px 7px;border-radius:4px;font-size:11px;text-align:center;border:1px solid rgba(0,0,0,.13);cursor:pointer;transition:box-shadow .1s}\n'
        '.fb-wm-tile:hover{box-shadow:0 2px 8px rgba(0,0,0,.22)}\n'
        '.fb-wm-lot{font-weight:bold;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12px}\n'
        '.fb-wm-wfr{font-size:11px}\n'
        '.fb-wm-cnt{font-weight:bold;margin-top:2px;font-size:12px}\n'
        '.fb-wm-mat{font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;opacity:.9}\n'
        '.bh-hw-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:transparent;z-index:20000;pointer-events:none}\n'
        '.bh-hw-overlay.open{display:block}\n'
        '.bh-hw-box{position:fixed;top:80px;left:50%;transform:translateX(-50%);width:50vw;height:50vh;min-width:340px;min-height:200px;max-width:96vw;max-height:90vh;background:#fff;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.35);display:none;flex-direction:column;pointer-events:auto;resize:both;overflow:hidden}\n'
        '.bh-hw-overlay.open .bh-hw-box{display:flex}\n'
        '.bh-hw-drag{cursor:move;background:#2c3e50;color:#fff;padding:8px 14px;border-radius:8px 8px 0 0;display:flex;align-items:center;justify-content:space-between;user-select:none;flex-shrink:0}\n'
        '.bh-hw-body{overflow:hidden;padding:8px 12px 12px;flex:1;font-size:14px;display:flex;flex-direction:column;min-height:0}\n'
        '.upm-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:transparent;z-index:18000;pointer-events:none}\n'
        '.upm-overlay.open{display:block}\n'
        '.upm-box{background:#fff;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.3);width:50vw;height:50vh;max-width:95vw;max-height:90vh;min-width:320px;min-height:200px;position:fixed;top:80px;left:50%;transform:translateX(-50%);display:none;flex-direction:column;pointer-events:auto;resize:both;overflow:hidden}\n'
        '.upm-overlay.open .upm-box{display:flex}\n'
        '.upm-drag{cursor:move;background:#1a5276;color:#fff;padding:8px 14px;border-radius:8px 8px 0 0;display:flex;align-items:center;justify-content:space-between;user-select:none;flex-shrink:0}\n'
        '.upm-body{overflow-y:auto;padding:10px 14px 14px;flex:1;font-size:13px;min-height:0}\n'
        '.upm-mbtn{padding:4px 12px;border-radius:4px;border:1px solid #2980b9;background:#fff;cursor:pointer;font-size:12px;color:#2980b9;margin-right:6px;margin-bottom:6px}\n'
        '.upm-mbtn.active{background:#2980b9;color:#fff}\n'
        '.upm-maps{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px}\n'
        '.upm-ww{background:#f8f9fa;border:1px solid #dde;border-radius:6px;padding:8px;text-align:center}\n'
        '.upm-wlbl{font-size:12px;font-weight:bold;color:#2c3e50;margin-bottom:4px}\n'
        '.upm-lgd{display:flex;align-items:center;gap:8px;margin-top:10px;font-size:11px;color:#555}\n'
        '.upm-lgd-bar{flex:1;height:14px;border-radius:3px;background:linear-gradient(to right,rgb(220,0,0),rgb(255,120,0),rgb(240,215,0),rgb(0,210,60),rgb(0,50,220))}\n'
        '.dlcp-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:transparent;z-index:22000;pointer-events:none}\n'
        '.dlcp-overlay.open{display:block;pointer-events:none}\n'
        '.dlcp-box{background:#f0f2f5;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.35);width:94vw;max-width:1340px;height:72vh;min-width:600px;min-height:340px;max-width:98vw;max-height:95vh;display:flex;flex-direction:column;pointer-events:auto;overflow:hidden;resize:both}\n'
        '.dlcp-drag{cursor:move;background:#1f618d;color:#fff;padding:8px 14px;border-radius:8px 8px 0 0;display:flex;align-items:center;justify-content:space-between;user-select:none;flex-shrink:0}\n'
        '.dlcp-body{display:flex;flex-direction:column;flex:1;padding:8px;gap:6px;min-height:0;overflow:hidden}\n'
        '.dlcp-ctrl{display:flex;align-items:center;gap:12px;flex-wrap:wrap;background:#fff;padding:7px 12px;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex-shrink:0}\n'
        '.dlcp-sumbox{background:transparent;border-radius:0;padding:0;box-shadow:none;flex-shrink:0;display:flex;flex-direction:column;gap:6px;align-items:stretch}\n'
        '.dlcp-sum-panel{background:#fff;border-radius:6px;padding:8px 14px;box-shadow:0 1px 4px rgba(0,0,0,.1);display:flex;flex-direction:column;gap:4px;min-width:0}\n'
        '.dlcp-sum-panel-ttl{font-size:15px;font-weight:bold;text-transform:uppercase;letter-spacing:.7px;color:#fff;background:#5d6d7e;border-radius:3px;padding:1px 8px;margin-bottom:4px;align-self:flex-start}\n'
        '.dlcp-sumrow{display:flex;gap:10px;flex-wrap:wrap;align-items:center}\n'
        '.dlcp-sum-grp{display:flex;flex-direction:column;padding:4px 14px;border-left:3px solid #dde;min-width:110px}\n'
        '.dlcp-sum-grp.pass{border-color:#2980b9}.dlcp-sum-grp.marg{border-color:#d4ac0d}.dlcp-sum-grp.fail{border-color:#c0392b}\n'
        '.dlcp-sum-lbl{font-size:17px;color:#000;text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px}\n'
        '.dlcp-sum-val{font-size:26px;font-weight:bold;color:#2c3e50}.dlcp-sum-pct{font-size:17px;color:#666;margin-left:4px}\n'
        '.dlcp-sum-pct-big{font-size:33px;font-weight:bold;line-height:1.1}\n'
        '.dlcp-sum-sub{font-size:15px;color:#aaa;margin-top:1px}\n'
        '.dlcp-inner{display:flex;gap:0;flex:1;min-height:0}\n'
        '.dlcp-left{display:flex;flex-direction:column;gap:6px;min-width:0;flex:1;overflow:hidden}\n'
        '.dlcp-panel-hdr{display:flex;align-items:center;gap:5px;flex-shrink:0}\n'
        '.dlcp-pbtn{background:#ecf0f1;border:1px solid #bdc3c7;border-radius:3px;font-size:11px;padding:1px 7px;cursor:pointer;color:#2c3e50;white-space:nowrap}\n'
        '.dlcp-pbtn:hover{background:#d5dbde}\n'
        '.dlcp-flt-row input{width:100%;box-sizing:border-box;font-size:11px;padding:2px 4px;border:1px solid #ccd;border-radius:2px}\n'
        '.dlcp-sec-ttl{font-size:11px;font-weight:bold;color:#5d6d7e;text-transform:uppercase;letter-spacing:.5px;flex-shrink:0}\n'
        '.dlcp-tw{overflow:auto;background:#fff;border-radius:6px;padding:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex:1;min-height:0}\n'
        '#dlcp-tbl-pane{display:flex;flex-direction:column;flex:1;min-height:0;overflow:hidden}\n'
        '.dlcp-t{border-collapse:collapse;font-size:12px;white-space:nowrap;width:100%}\n'
        '.dlcp-t th{background:#2c3e50;color:#ecf0f1;padding:5px 10px;text-align:left;position:sticky;top:0;z-index:1}\n'
        '.dlcp-t td{padding:4px 10px;border-bottom:1px solid #eee}\n'
        '.dlcp-t tr:nth-child(even) td{background:#f7f9fc}.dlcp-t tr:hover td{background:#eaf3fb}\n'
        '.dlcp-t tr.dlcp-rsel td{background:#d0eaff!important;font-weight:bold}\n'
        '.dlcp-t tr.dlcp-runsel td{opacity:.4}\n'
        '.dlcp-t tr{cursor:pointer}\n'
        '.dlcp-ddbtn{background:none;border:none;color:#aed6f1;cursor:pointer;font-size:10px;padding:0 2px;vertical-align:middle;margin-left:3px}\n'
        '.dlcp-ddbtn.on{color:#f1c40f}\n'
        '.dlcp-dd{position:fixed;background:#fff;border:1px solid #aaa;border-radius:4px;box-shadow:0 4px 16px rgba(0,0,0,.2);z-index:30000;min-width:160px;max-width:260px;font-size:12px;color:#2c3e50}\n'
        '.dlcp-dd-srch{width:100%;box-sizing:border-box;padding:5px 8px;border:none;border-bottom:1px solid #ddd;font-size:12px;outline:none}\n'
        '.dlcp-dd-acts{display:flex;gap:4px;padding:4px 6px;border-bottom:1px solid #eee}\n'
        '.dlcp-dd-acts button{flex:1;padding:2px 6px;font-size:11px;cursor:pointer;border:1px solid #bdc3c7;background:#ecf0f1;border-radius:3px}\n'
        '.dlcp-dd-list{max-height:200px;overflow-y:auto;padding:4px 0}\n'
        '.dlcp-dd-item{display:flex;align-items:center;gap:6px;padding:3px 10px;cursor:pointer}\n'
        '.dlcp-dd-item:hover{background:#eaf0fb}\n'
        '.dlcp-dd-foot{padding:4px 8px;border-top:1px solid #eee;text-align:right}\n'
        '.dlcp-dd-foot button{padding:3px 12px;font-size:11px;cursor:pointer;background:#2c3e50;color:#fff;border:none;border-radius:3px}\n'
        '.dlcp-splitter{width:14px;flex-shrink:0;display:flex;align-items:center;justify-content:center;cursor:pointer;background:#e8ecf0;border-left:1px solid #d0d8e8;border-right:1px solid #d0d8e8;transition:background .15s;user-select:none}\n'
        '.dlcp-splitter:hover{background:#c8d4e8}\n'
        '.dlcp-split-arrow{font-size:12px;color:#5d6d7e}\n'
        '.dlcp-cw{flex:1;background:#fff;border-radius:6px;padding:8px;box-shadow:0 1px 4px rgba(0,0,0,.1);display:flex;flex-direction:column;min-width:0;overflow:hidden;margin-left:8px}\n'
        '.dlcp-t .num{text-align:right}\n'
        '.dlcp-cw{flex:1;background:#fff;border-radius:6px;padding:8px;box-shadow:0 1px 4px rgba(0,0,0,.1);display:flex;flex-direction:column;min-width:0;resize:horizontal;overflow:hidden;min-width:150px}\n'
        '.upm-hist-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.45);z-index:32000;align-items:center;justify-content:center}\n'
        '.upm-hist-overlay.open{display:flex}\n'
        '.upm-hist-box{background:#f0f2f5;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.4);width:800px;max-width:96vw;height:540px;max-height:92vh;display:flex;flex-direction:column;overflow:hidden;resize:both;min-width:420px;min-height:340px}\n'
        '.upm-hist-drag{cursor:move;background:#1f618d;color:#fff;padding:7px 14px;display:flex;align-items:center;justify-content:space-between;user-select:none;flex-shrink:0}\n'
        '.upm-hist-body{display:flex;flex-direction:column;flex:1;padding:10px;gap:8px;min-height:0;overflow:hidden}\n'
        '.upm-hist-stats{display:flex;flex-wrap:wrap;gap:6px;background:#fff;border-radius:6px;padding:8px 12px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex-shrink:0}\n'
        '.upm-hist-stat-grp{display:flex;flex-direction:column;padding:3px 12px;border-left:3px solid #dde;min-width:100px}\n'
        '.upm-hist-stat-lbl{font-size:10px;color:#888;text-transform:uppercase;letter-spacing:.4px;font-weight:bold}\n'
        '.upm-hist-stat-val{font-size:14px;font-weight:bold;color:#2c3e50}\n'
        '.upm-hist-cv-wrap{flex:1;min-height:0;display:flex}\n'
        '.dlcp-note{font-size:10px;color:#666;background:#f8f9fa;border:1px solid #e4e4e4;border-radius:4px;padding:5px 10px;line-height:1.8;flex-shrink:0}\n'
        '.dlcp-note b{color:#444}\n'
        '.wm-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.45);z-index:23000;pointer-events:none}\n'
        '.wm-overlay.open{display:block;pointer-events:none}\n'
        '.wm-box{position:absolute;left:3vw;top:36px;background:#f0f2f5;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.35);width:94vw;max-width:1400px;height:72vh;min-width:640px;min-height:360px;max-height:95vh;display:flex;flex-direction:column;pointer-events:auto;overflow:hidden;resize:both}\n'
        '.wm-drag{cursor:move;background:#145a32;color:#fff;padding:8px 14px;border-radius:8px 8px 0 0;display:flex;align-items:center;justify-content:space-between;user-select:none;flex-shrink:0}\n'
        '.wm-body{display:flex;flex-direction:column;flex:1;padding:8px;gap:6px;min-height:0;overflow:hidden}\n'
        '.wm-inner{display:flex;gap:8px;flex:1;min-height:0;overflow:auto}\n'
        '.wm-left{display:flex;flex-direction:column;gap:6px;flex:none;width:55%;min-width:180px;min-height:200px;resize:both;overflow:hidden}\n'
        '.wm-maps-wrap{overflow:auto;background:#fff;border-radius:6px;padding:8px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex:1;min-height:0}\n'
        '.wm-maps{display:flex;flex-wrap:wrap;gap:10px}\n'
        '.wm-wlbl{font-size:11px;font-weight:bold;color:#2c3e50;text-align:center;margin-bottom:3px}\n'
        '.wm-right{display:flex;flex-direction:column;gap:4px;flex:1;min-width:200px;min-height:200px;resize:both;overflow:hidden}\n'
        '.wm-tbl-wrap{overflow:auto;background:#fff;border-radius:6px;padding:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex:1;min-height:0}\n'
        '.wm-t{border-collapse:collapse;font-size:12px;width:100%}\n'
        '.wm-t th{background:#145a32;color:#fff;padding:4px 8px;text-align:left;position:sticky;top:0;z-index:1}\n'
        '.wm-t td{padding:3px 8px;border-bottom:1px solid #eee;font-size:11px}\n'
        '.wm-t tr:nth-child(even) td{background:#f7f9fc}\n'
        '.wm-bar-bg{background:#e8e8e8;border-radius:3px;height:8px;width:90px;display:inline-block;vertical-align:middle}\n'
        '.wm-bar-fg{height:8px;border-radius:3px;display:block}\n'
        '.wm-legend{display:flex;flex-wrap:wrap;gap:8px;font-size:11px;padding:4px 8px;background:#fff;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex-shrink:0}\n'
        '.wm-lgi{display:flex;align-items:center;gap:4px}\n'
        '.wm-lgsw{width:14px;height:14px;border-radius:2px;flex-shrink:0}\n'
        '.wm-note{font-size:10px;color:#666;background:#f8f9fa;border:1px solid #e4e4e4;border-radius:4px;padding:4px 8px;line-height:1.7;flex-shrink:0}\n'
        '.wm-ctrl{display:flex;align-items:flex-start;gap:8px;flex-shrink:0;padding:4px 0 2px 0;flex-wrap:wrap}\n'
        '.wm-filtbar{background:#fff;border:1px solid #d5d8dc;border-radius:5px;padding:4px 8px;display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center;flex:1;max-height:72px;overflow-y:auto}\n'
        '.wm-lot-grp{display:flex;align-items:center;gap:4px;white-space:nowrap;font-size:11px}\n'
        '.wm-lot-lbl{font-weight:bold;color:#2c3e50;cursor:pointer;padding:1px 4px;border-radius:3px;background:#eaf0fb;border:1px solid #aec6ef}\n'
        '.wm-lot-lbl:hover{background:#d0e4fc}\n'
        '.wm-wcb{font-size:11px;cursor:pointer;display:flex;align-items:center;gap:2px;padding:1px 4px;border-radius:3px}\n'
        '.wm-wcb:hover{background:#f0f4fa}\n'
        '.wm-wcb input{cursor:pointer;margin:0}\n'
        '.wm-ib-btn{font-size:11px;padding:2px 7px;border-radius:3px;border:1px solid #ccc;cursor:pointer;background:#f5f5f5}\n'
        '.wm-ib-btn.active{border-color:#2471a3;background:#d6eaf8;color:#1a5276;font-weight:bold}\n'
        '.wm-selall{font-size:10px;color:#2471a3;cursor:pointer;text-decoration:underline;white-space:nowrap}\n'
        '.wm-thresh-row{display:flex;align-items:center;gap:5px;flex-shrink:0;padding:3px 0}\n'
        '.wm-tbtn{font-size:11px;padding:2px 9px;border-radius:12px;border:1px solid #aaa;cursor:pointer;background:#f5f5f5;white-space:nowrap}\n'
        '.wm-tbtn.on{border-color:#145a32;background:#d5f5e3;color:#145a32;font-weight:bold}\n'
        '.wm-impact{background:#fff;border-radius:5px;padding:5px 8px;box-shadow:0 1px 3px rgba(0,0,0,.1);flex:1;overflow:auto;min-height:0}\n'
        '.wm-tabs{display:flex;gap:0;border-bottom:2px solid #d5d8dc;flex-shrink:0}\n'
        '.wm-tab{font-size:11px;padding:4px 12px;cursor:pointer;border:1px solid transparent;border-bottom:none;border-radius:5px 5px 0 0;color:#666;background:none;white-space:nowrap}\n'
        '.wm-tab.on{border-color:#d5d8dc;background:#fff;color:#145a32;font-weight:bold;margin-bottom:-2px}\n'
        '.wm-tabpane{display:none;flex:1;min-height:0;overflow:auto}\n'
        '.wm-tabpane.on{display:flex;flex-direction:column}\n'
        '.wm-binrow{display:flex;flex-wrap:wrap;gap:4px 8px;font-size:11px;padding:4px 6px;background:#fff;border-radius:5px;box-shadow:0 1px 3px rgba(0,0,0,.1);flex-shrink:0}\n'
        '.wm-bincb{display:flex;align-items:center;gap:3px;cursor:pointer;padding:1px 4px;border-radius:3px;white-space:nowrap}\n'
        '.wm-bincb:hover{background:#f0f4fa}\n'
        '.wm-bincb input{cursor:pointer;margin:0}\n'
        '.wm-binsw{width:10px;height:10px;border-radius:2px;flex-shrink:0}\n'
        '.wmd-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.4);z-index:25000;pointer-events:none}\n'
        '.wmd-overlay.open{display:block;pointer-events:none}\n'
        '.wmd-box{position:absolute;left:5%;top:5%;width:88%;height:88%;background:#f8f9fa;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.4);display:flex;flex-direction:column;pointer-events:auto;overflow:hidden;resize:both;min-width:480px;min-height:320px}\n'
        '.wmd-drag{cursor:move;background:#1a5276;color:#fff;padding:7px 14px;border-radius:8px 8px 0 0;display:flex;align-items:center;justify-content:space-between;user-select:none;flex-shrink:0}\n'
        '.wmd-body{display:flex;gap:8px;flex:1;min-height:0;padding:8px;overflow:hidden}\n'
        '.wmd-col{display:flex;flex-direction:column;gap:6px;min-width:0}\n'
        '.wmd-sec{background:#fff;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);padding:8px;flex-shrink:0}\n'
        '.wmd-sec-ttl{font-size:10px;font-weight:bold;color:#5d6d7e;text-transform:uppercase;letter-spacing:.4px;margin-bottom:5px}\n'
        '.wmd-hw-t{border-collapse:collapse;font-size:11px;width:100%}\n'
        '.wmd-hw-t th{background:#1a5276;color:#fff;padding:3px 7px;text-align:left}\n'
        '.wmd-hw-t td{padding:2px 7px;border-bottom:1px solid #eee;font-size:11px}\n'
        '.wm-impact-ttl{font-size:10px;font-weight:bold;color:#5d6d7e;text-transform:uppercase;letter-spacing:.4px;margin-bottom:4px}\n'
        '.wm-impact-row{display:flex;align-items:center;gap:6px;font-size:11px;margin-bottom:2px}\n'
        '.wm-impact-lbl{width:80px;min-width:80px;text-align:right;font-weight:bold;white-space:nowrap;flex-shrink:0}\n'
        '.wm-impact-bar{flex:1;background:#e8e8e8;border-radius:3px;height:8px;position:relative}\n'
        '.wm-impact-fill{height:8px;border-radius:3px;position:absolute;left:0;top:0}\n'
        '.wm-impact-pct{width:36px;font-size:10px;color:#555}\n'
        '</style>\n</head>\n<body>\n<div class="pw">\n'
    )

    # Compute FF/DF totals for info bar
    _ff_count   = sum(bin_counts.get(str(b), 0) for b in [1, 2])
    _ffdf_count = sum(bin_counts.get(str(b), 0) for b in [1, 2, 3, 4])  # IB1-4 total for denominator
    _df34_count = bin_counts.get('3', 0) + bin_counts.get('4', 0)  # DF (IB3-4) count
    _df3_count  = bin_counts.get('3', 0)
    _df4_count  = bin_counts.get('4', 0)
    _ib14_count = _ffdf_count  # IB1-4 total denominator
    _ff_pct   = f'{_ff_count/_ib14_count*100:.1f}' if _ib14_count else '0.0'
    _df34_pct = f'{_df34_count/_ib14_count*100:.1f}' if _ib14_count else '0.0'
    _df3_pct  = f'{_df3_count/_ib14_count*100:.1f}' if _ib14_count else '0.0'
    _df4_pct  = f'{_df4_count/_ib14_count*100:.1f}' if _ib14_count else '0.0'

    _html_info = (
        '<div class="ib" style="flex-direction:column;gap:3px">'
        '<div style="display:flex;flex-wrap:wrap;gap:8px">'
        f'<span>TEST PROGRAM: <b>{_esc(prog_val)}</b></span>'
        f'<span>LOTS: <b>{_esc(lot_val)}</b></span>'
        f'<span>TOTAL WAFERS: <b>{_esc(wafer_cnt)}</b></span>'
        f'<span>TOTAL UNITS: <b>{total:,}</b></span>'
        '</div>'
        '<div style="display:flex;flex-wrap:wrap;gap:8px;font-size:12px">'
        f'<span>FF <small style="color:#aed6f1">(IB 1+2)</small>: <b style="color:#aed6f1">{_ff_count:,}</b> <span style="color:#aed6f1">({_ff_pct}% of IB1-4)</span></span>'
        f'<span>DF <small style="color:#a9dfbf">(IB 3-4)</small>: <b style="color:#a9dfbf">{_df34_count:,}</b> <span style="color:#a9dfbf">({_df34_pct}% of IB1-4)</span></span>'
        f'<span>ATOM DF <small style="color:#f0b27a">(IB 3)</small>: <b style="color:#f0b27a">{_df3_count:,}</b> <span style="color:#f0b27a">({_df3_pct}% of IB1-4)</span></span>'
        f'<span>CORE DF <small style="color:#f1948a">(IB 4)</small>: <b style="color:#f1948a">{_df4_count:,}</b> <span style="color:#f1948a">({_df4_pct}% of IB1-4)</span></span>'
        '</div>'
        '</div>\n'
    )

    _upm_med_ths = ''  # thead is now built dynamically by _buildFilterThead() (see _filter_lot_wafer.py)

    _html_layout = (
        '<div class="mr">\n'
        '  <div class="cp">\n'
        '    <div class="ctr">\n'
        '      <span class="ct">&#128200; Yield by Interface Bin</span>\n'
        '      <span id="sel-info" class="si"></span>\n'
        '    </div>\n'
        '    <svg id="hist-svg" class="hs"></svg>\n'
        '  </div>\n'
        '  <div class="lp">\n'
        '    <div class="lh">Interface Bins\n'
        '      <div class="lb">\n'
        '        <button class="cb" onclick="IC.toggleAllBins(true)">All</button>\n'
        '        <button class="cb" onclick="IC.toggleAllBins(false)">None</button>\n'
        '      </div>\n'
        '    </div>\n'
        +('    <div class="lhi">Click to isolate &bull; Ctrl+click for FB breakdown</div>\n' if fb_col else '    <div class="lhi">Click to isolate &bull; Ctrl+click multi-select</div>\n')
        +'    <input class="lg-search" id="lg-search" placeholder="&#128269; Filter bins\u2026" oninput="IC.lgSearch(this.value)">\n'
        +'    <div id="bin-legend"></div>\n'
        '  </div>\n'
        '</div>\n'
        '<div class="fy-row">\n'
        '<div class="yp" id="yp-filter" style="flex:0 1 47%;max-width:47%;min-width:0">\n'
        '<div class="yp-bar" onclick="ypTgl(\'filter\')">'
        '<span class="yp-ttl"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:middle;margin-right:4px"><path d="M10 18h4v-2h-4v2zM3 6v2h18V6H3zm3 7h12v-2H6v2z"/></svg> Filter by Lot / Wafer</span>'
        '<span class="yp-btns" onclick="event.stopPropagation()">'
        '<button class="yp-btn" onclick="IC.selectAllRows()">Select All</button>'
        '<button class="yp-btn" onclick="IC.clearRows()">Clear</button>'
        '<button class="yp-btn" onclick="IC.exportCsv()" title="Export visible rows to CSV">&#8681; CSV</button>'
        '<span id="row-sel-info" style="font-size:10px;color:rgba(255,255,255,.75);margin:0 4px"></span>'
        '<button class="yp-btn" id="ypmin-filter" onclick="ypTgl(\'filter\')" title="Collapse / Expand">&#8722;</button>'
        '<button class="yp-btn" id="ypmax-filter" onclick="ypMax(\'filter\')" title="Full screen">&#10064;</button>'
        '</span></div>\n'
        '<div class="yp-body" id="ypb-filter" style="padding:6px 8px;max-height:calc(100vh - 260px)">\n'
        '  <div class="ftw">\n'
        '    <table class="ftbl">\n'
        '      <thead id="filter-thead"></thead>\n'
        '      <tbody id="filter-tbody"></tbody>\n'
        '    </table>\n'
        '  </div>\n'
        '</div>\n'
        '</div>\n'
        '<div class="yp" id="yp-ys" style="flex:1 1 53%;min-width:0">\n'
        '<div class="yp-bar" onclick="ypTgl(\'ys\')">'
        '<span class="yp-ttl">&#9654; Yield Summary (filtered)</span>'
        '<span class="yp-btns" onclick="event.stopPropagation()">'
        '<label style="font-size:11px;font-weight:normal;color:rgba(255,255,255,.85);cursor:pointer;display:inline-flex;align-items:center;gap:4px;margin-right:6px" title="Split Actual column by Material Type (L0=AIO, L5=AIO+BB)">'
        '<input type="checkbox" id="ys-split" onchange="IC.rYield()" style="cursor:pointer"> Split by Material</label>'
        '<button class="yp-btn" onclick="IC.exportYieldCsv()" title="Export to CSV">&#8681; CSV</button>'
        '<button class="yp-btn" onclick="IC.openDlcpModal()">&#128202; DLCP</button>'
        '<button class="yp-btn" onclick="window.open(\'wafermap.html\',\'_blank\')">&#127759; Wafer Map</button>'
        '<button class="yp-btn" id="ypmin-ys" onclick="ypTgl(\'ys\')" title="Collapse / Expand">&#8722;</button>'
        '<button class="yp-btn" id="ypmax-ys" onclick="ypMax(\'ys\')" title="Full screen">&#10064;</button>'
        '</span></div>\n'
        '<div class="yp-body" id="ypb-ys" style="padding:6px 8px;max-height:calc(100vh - 260px)">\n'
        '  <div id="ys-info" style="font-size:11px;color:#555;padding:2px 6px 4px 6px"></div>\n'
        '  <table class="ytbl">\n'
        '    <thead id="yield-thead"><tr>\n'
        '      <th>BIN</th><th>FAIL BUCKET</th>\n'
        '      <th class="num">ACTUAL (%)</th><th class="num">EXPECTED (%)</th><th class="num">DIFF (%)</th><th></th>\n'
        '    </tr></thead>\n'
        '    <!-- header updated dynamically by rYield() -->\n'
        '    <tbody id="yield-tbody"></tbody>\n'
        '  </table>\n'
        '</div>\n'
        '</div>\n'
        '</div>\n'
        + ('<div class="fy-row">\n'
           '<div class="yp" id="yp-sdt" style="flex:0 0 auto;width:fit-content">\n'
           '<div class="yp-bar" onclick="ypTgl(\'sdt\')">'           '<span class="yp-ttl">&#128202; SDT Bins (SDS IB &le; 4, filtered)</span>'
           '<span class="yp-btns" onclick="event.stopPropagation()">'
           '<button class="yp-btn" onclick="IC.exportSdtCsv()" title="Download CSV">&#8681; CSV</button>'
           '<button class="yp-btn" id="ypmin-sdt" onclick="ypTgl(\'sdt\')" title="Collapse / Expand">&#43;</button>'
           '<button class="yp-btn" id="ypmax-sdt" onclick="ypMax(\'sdt\')" title="Full screen">&#10064;</button>'
           '</span></div>\n'
           '<div class="yp-body yp-col" id="ypb-sdt" style="padding:6px 8px;max-height:calc(100vh - 260px)">\n'
           '  <table class="sdt-tbl">\n'
           '    <thead><tr>\n'
           '      <th onclick="IC.sdtSort(0)" style="cursor:pointer">SDS IB <span id="sdt-sh-0">&#9650;</span></th><th onclick="IC.sdtSort(1)" style="cursor:pointer">SDS FB <span id="sdt-sh-1"></span></th><th onclick="IC.sdtSort(2)" style="cursor:pointer">SDT IB <span id="sdt-sh-2"></span></th><th onclick="IC.sdtSort(3)" style="cursor:pointer">SDT FB <span id="sdt-sh-3"></span></th><th onclick="IC.sdtSort(4)" style="cursor:pointer">Bin Description <span id="sdt-sh-4"></span></th><th class="num" onclick="IC.sdtSort(5)" style="cursor:pointer">Count <span id="sdt-sh-5"></span></th><th class="num" onclick="IC.sdtSort(6)" style="cursor:pointer" title="Count / total count for same SDS IB">% SDS IB <span id="sdt-sh-6"></span></th><th class="num" onclick="IC.sdtSort(7)" style="cursor:pointer" title="Count / total SDS IB 1+2+3+4">% SDS 1-4 <span id="sdt-sh-7"></span></th><th class="num" onclick="IC.sdtSort(8)" style="cursor:pointer" title="Fail count / total dies in selected wafer">Fail % <span id="sdt-sh-8"></span></th>\n'
           '    </tr></thead>\n'
           '    <tbody id="sdt-tbody"></tbody>\n'
           '  </table>\n'
           '</div>\n'
           '</div>\n'
           '</div>\n'
           if sdt_ib_col else '')
        + '</div>\n'
        +('<!-- UPM Heatmap popup -->\n'
        '<div id="upm-modal" class="upm-overlay">\n'
        '  <div class="upm-box" id="upm-box">\n'
        '    <div class="upm-drag" id="upm-drag"><b>Wafer Heatmap</b>'
        '<button id="upm-mode-btn" onclick="IC._upmToggleMode()" title="Switch between Canvas (fast) and SVG" style="background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.4);color:#fff;font-size:11px;cursor:pointer;padding:2px 9px;border-radius:4px;margin-right:4px">&#128247; SVG mode</button>'
        '<button onclick="IC._upmZoomOut()" title="Zoom out" style="background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.4);color:#fff;font-size:13px;cursor:pointer;padding:0 7px;border-radius:4px;margin-right:2px;line-height:1.6">&#8722;</button>'
        '<span id="upm-zoom-lbl" style="font-size:11px;color:#ecf0f1;min-width:34px;display:inline-block;text-align:center">100%</span>'
        '<button onclick="IC._upmZoomIn()" title="Zoom in" style="background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.4);color:#fff;font-size:13px;cursor:pointer;padding:0 7px;border-radius:4px;margin-right:8px;line-height:1.6">&#43;</button>'
        '<button onclick="IC.refreshUpm()" style="background:none;border:none;color:#fff;font-size:16px;cursor:pointer;margin-right:8px" title="Refresh">&#x21bb;</button>'
        '<button onclick="IC.closeUpmModal()" style="background:none;border:none;color:#fff;font-size:20px;cursor:pointer;line-height:1">&times;</button></div>\n'
        '    <div id="upm-ctrl-wrap" style="flex-shrink:0;background:#f7f9fc;border-bottom:1px solid #dde4ee">\n'
        '      <div style="display:flex;align-items:center">\n'
        '        <div id="upm-dieLoc-bar" style="display:none;padding:3px 8px;font-size:10px;flex:1;min-width:0"></div>\n'
        '        <div id="upm-fb-filter" style="display:none;padding:3px 8px;font-size:11px">'
        '<span style="font-weight:bold;color:#2c3e50;margin-right:6px">FB filter:</span>'
        '<button class="cb" id="upm-fb-dd-btn" onclick="IC._upmFbDdToggle()" style="padding:1px 9px;font-size:11px">&#9660; Choose FBs</button>'
        '<button class="cb" onclick="IC.selectAllFbs()" style="padding:1px 7px;font-size:11px">All</button>'
        '<button class="cb" onclick="IC.clearFbs()" style="padding:1px 7px;font-size:11px">None</button>'
        '<span id="upm-fb-summary" style="margin-left:8px;font-size:11px;color:#555"></span>'
        '</div>\n'
        '      </div>\n'
        '      <div id="upm-fb-dd" style="display:none;padding:8px 10px;border-top:1px solid #e0e8f0;background:#fff">'
        '<input id="upm-fb-search" type="text" placeholder="Search FB / description…" oninput="IC._upmFbSearch(this.value)" style="width:100%;box-sizing:border-box;padding:4px 8px;font-size:12px;border:1px solid #c5cfe0;border-radius:4px;margin-bottom:6px">'
        '<div id="upm-fb-cblist" style="max-height:240px;overflow-y:auto"></div>'
        '</div>\n'
        '    </div>\n'
        '    <div class="upm-body" id="upm-body"></div>\n'
        '  </div>\n'
        '</div>\n'
        if _upm_col_defs and _x_col and _y_col else '')
        +'<!-- BH HW draggable popup -->\n'
        '<div id="bh-hw-modal" class="bh-hw-overlay">\n'
        '  <div class="bh-hw-box" id="bh-hw-box">\n'
        '    <div class="bh-hw-drag" id="bh-hw-drag">\n'
        '      <b id="bh-hw-modal-title">HW Breakdown</b>\n'
        '      <button onclick="IC.closeBhHwModal()" style="background:none;border:none;color:#fff;font-size:20px;cursor:pointer;line-height:1">&times;</button>\n'
        '    </div>\n'
        '    <div class="bh-hw-body" id="bh-hw-body"></div>\n'
        '  </div>\n'
        '</div>\n'
        '<!-- Wafer Pattern modal -->\n'
        '<div class="wm-overlay" id="wm-overlay">\n'
        '  <div class="wm-box" id="wm-box">\n'
        '    <div class="wm-drag" id="wm-drag"><b>&#127759; Wafer Pattern Analysis</b>\n'
        '      <button id="wm-mode-btn" onclick="IC._wmToggleCanvasMode()" title="Switch to Canvas for fast interactive debug" style="background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.4);color:#fff;font-size:11px;cursor:pointer;padding:2px 9px;border-radius:4px;margin-right:8px">&#128247; SVG mode</button>\n'
        '      <button onclick="IC.closeWmModal()" style="background:none;border:none;color:#fff;font-size:20px;cursor:pointer;line-height:1">&times;</button>\n'
        '    </div>\n'
        '    <div class="wm-body">\n'
        '      <div class="wm-ctrl" id="wm-ctrl"></div>\n'
        '      <div class="wm-inner">\n'
        '        <div class="wm-left">\n'
        '          <div style="font-size:11px;font-weight:bold;color:#5d6d7e;text-transform:uppercase;letter-spacing:.5px;flex-shrink:0">Wafer Maps &mdash; click die to open IB distribution</div>\n'
        '          <div class="wm-maps-wrap"><div class="wm-maps" id="wm-maps"></div></div>\n'
        '        </div>\n'
        '        <div class="wm-right">\n'
        '          <div class="wm-tabs" id="wm-tabs">\n'
        '            <button class="wm-tab on" id="wm-tab-impact" onclick="IC._wmTab(\'impact\')">&#128269; Bin Impact</button>\n'
        +(f'            <button class="wm-tab" id="wm-tab-reticle" onclick="IC._wmTab(\'reticle\')">&#127760; Reticle</button>\n' if _ret_map else '')
        +'            <button class="wm-tab" id="wm-tab-guide" onclick="IC._wmTab(\'guide\')">&#8505; Guide</button>\n'        +'          </div>\n'
        '          <div class="wm-tabpane on" id="wm-pane-impact">\n'
        '            <div class="wm-impact" id="wm-impact"><div id="wm-impact-body"><span style="color:#aaa;font-size:11px">No data yet</span></div></div>\n'
        '          </div>\n'
        '          <div class="wm-tabpane" id="wm-pane-guide">\n'
        '            <div style="padding:10px;overflow-y:auto;font-size:11px;flex:1">\n'
        '              <table style="border-collapse:collapse;width:100%">\n'
        '                <thead><tr>\n'
        '                  <th style="background:#145a32;color:#fff;padding:4px 8px;text-align:left;white-space:nowrap">Pattern</th>\n'
        '                  <th style="background:#145a32;color:#fff;padding:4px 8px;text-align:left">What it means</th>\n'
        '                  <th style="background:#145a32;color:#fff;padding:4px 8px;text-align:left">Typical process suspects <span style="font-weight:normal;font-size:10px">(hover for full text)</span></th>\n'
        '                </tr></thead>\n'
        '                <tbody>\n'
        '                  <tr>\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;font-weight:bold;color:#c0392b;white-space:nowrap;vertical-align:top">&#11044; CENTER</td>\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top">Failures concentrated at the wafer center. Center-hot or center-cold process non-uniformity.</td>\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top;font-size:10px;line-height:1.8;max-width:260px;overflow:hidden" title="CMP: center dishing / over-polish, slurry delivery from center port, pad wear hotspot | Deposition (CVD/PVD): center-thick or center-thin film, showerhead center zone drift | Thermal anneal: center-hot lamp or susceptor, radial temperature gradient | Etch: center etch-rate bias, plasma density peak at center (ICP/CCP mode) | Ion implant: beam centering drift, dose non-uniformity at scan center | Litho / spin-coat: puddle dispense non-uniformity, focus offset at wafer center">\n'
        '                      <b>Deposition (CVD/PVD):</b> center-thick or center-thin film, showerhead center zone drift<br>\n'
        '                      <b>Thermal anneal:</b> center-hot lamp or susceptor, radial temperature gradient<br>\n'
        '                      <b>Etch:</b> center etch-rate bias, plasma density peak at center (ICP/CCP mode)<br>\n'
        '                      <b>Ion implant:</b> beam centering drift, dose non-uniformity at scan center<br>\n'
        '                      <b>Litho / spin-coat:</b> puddle dispense non-uniformity, focus offset at wafer center\n'
        '                    </td>\n'
        '                  </tr>\n'
        '                  <tr style="background:#f7f9fc">\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;font-weight:bold;color:#e67e22;white-space:nowrap;vertical-align:top">&#11044; EDGE</td>\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top">Failures concentrated at the wafer periphery (outer ~15% radius). Edge-boundary process effects.</td>\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top;font-size:10px;line-height:1.8;max-width:260px;overflow:hidden" title="Litho: resist edge-bead, focus/dose rolloff at wafer edge, overlay error near edge, scanner chuck edge distortion | CMP: edge fast-polish (pad overhang), carrier ring pressure artifact, retaining ring wear | Deposition: edge gas-flow boundary, film thickness rolloff, shadow from wafer bevel | Etch: edge gas flow non-uniformity, wafer-chuck edge clamping gap, boundary plasma effects | Thermal: wafer edge heat loss (susceptor edge gap), edge oxidation rate difference | Wafer: edge chips / micro-cracks from handling, dicing street contamination, wafer bow stress">\n'
        '                      <b>CMP:</b> edge fast-polish (pad overhang), carrier ring pressure artifact, retaining ring wear<br>\n'
        '                      <b>Deposition:</b> edge gas-flow boundary, film thickness rolloff, shadow from wafer bevel<br>\n'
        '                      <b>Etch:</b> edge gas flow non-uniformity, wafer-chuck edge clamping gap, boundary plasma effects<br>\n'
        '                      <b>Thermal:</b> wafer edge heat loss (susceptor edge gap), edge oxidation rate difference<br>\n'
        '                      <b>Wafer:</b> edge chips / micro-cracks from handling, dicing street contamination, wafer bow stress\n'
        '                    </td>\n'
        '                  </tr>\n'
        '                  <tr>\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;font-weight:bold;color:#8e44ad;white-space:nowrap;vertical-align:top">&#11044; DONUT</td>\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top">Ring of failures at mid-radius (~40&ndash;70% of wafer radius). Annular process non-uniformity.</td>\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top;font-size:10px;line-height:1.8;max-width:260px;overflow:hidden" title="CMP: multi-zone carrier head pressure ring artifact, annular pad groove pattern | Deposition: showerhead mid-radius flow ring, annular gas injection pattern | Etch: plasma standing wave / mode transition at mid-radius, radial temperature ring | Spin-coat: solvent evaporation ring (Marangoni effect), resist thickness ring at mid-radius | Litho: lens aberration ring, dose annular non-uniformity from illuminator | Thermal: radial annular temperature band from susceptor zone boundary">\n'
        '                      <b>Deposition:</b> showerhead mid-radius flow ring, annular gas injection pattern<br>\n'
        '                      <b>Etch:</b> plasma standing wave / mode transition at mid-radius, radial temperature ring<br>\n'
        '                      <b>Spin-coat:</b> solvent evaporation ring (Marangoni effect), resist thickness ring at mid-radius<br>\n'
        '                      <b>Litho:</b> lens aberration ring, dose annular non-uniformity from illuminator<br>\n'
        '                      <b>Thermal:</b> radial annular temperature band from susceptor zone boundary\n'
        '                    </td>\n'
        '                  </tr>\n'
        '                  <tr style="background:#f7f9fc">\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;font-weight:bold;color:#2471a3;white-space:nowrap;vertical-align:top">&#11044; SYSTEMATIC</td>\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top">Failures repeat at the <b>same die XY positions within every reticle field</b> across the wafer. Pattern tiles with the reticle step pitch. Strong evidence of a within-field, field-periodic defect.</td>\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top;font-size:10px;line-height:1.8;max-width:260px;overflow:hidden" title="Litho / reticle: mask particle or reticle CD defect, OPC error on weak feature, phase-shift mask phase defect, lens aberration at specific field position, flare hotspot | Design / layout: weak point in layout (antenna rule violation, stress riser at corner, density DRC near limit) | Process at die level: repeating via/contact open, poly short, or STI crack at fixed cell location due to local pattern density effect | CMP within die: dishing at specific dense array location, scratch at fixed position from pad conditioning groove | Etch: micro-loading effect at specific pattern density location, hardmask CD bias at repeating dense feature">\n'
        '                      <b>Design / layout:</b> weak point in layout (antenna rule violation, stress riser at corner, density DRC near limit) that prints at a specific die XY<br>\n'
        '                      <b>Process at die level:</b> repeating via/contact open, poly short, or STI crack at fixed cell location due to local pattern density effect<br>\n'
        '                      <b>CMP within die:</b> dishing at specific dense array location, scratch at fixed position from pad conditioning groove<br>\n'
        '                      <b>Etch:</b> micro-loading effect at specific pattern density location, hardmask CD bias at repeating dense feature\n'
        '                    </td>\n'
        '                  </tr>\n'
        '                  <tr style="background:#e8f4f8">\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;font-weight:bold;color:#1f618d;white-space:nowrap;vertical-align:top">&#11044; RETICLE</td>\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top">Same die position <b>within the reticle field</b> (rx, ry) fails across the majority of shots on the wafer. Independent of wafer-level location. Strong evidence of a reticle-born defect.</td>\n'
        '                    <td style="padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top;font-size:10px;line-height:1.8;max-width:260px;overflow:hidden" title="Litho / mask: reticle particle, chrome defect, or phase defect at fixed field position | OPC / CD: weak SRAF or assist feature, insufficient OPC on a critical edge, sub-resolution feature printing at marginal dose | Lens aberration: coma or astigmatism at a fixed field (x,y) position in the scanner | Reticle haze: crystalline growth on reticle absorber causing local CD shift | Design: layout density hotspot or antenna violation at a specific cell position that amplifies litho or etch sensitivity">\n'
        '                      <b>Litho / mask:</b> reticle particle, chrome defect, or phase defect at fixed field position<br>\n'
        '                      <b>OPC / CD:</b> weak SRAF or assist feature, insufficient OPC on a critical edge<br>\n'
        '                      <b>Lens aberration:</b> coma or astigmatism at a fixed field position in the scanner<br>\n'
        '                      <b>Reticle haze:</b> crystalline growth on reticle absorber causing local CD shift<br>\n'
        '                      <b>Design hotspot:</b> layout density anomaly at a specific cell position\n'
        '                    </td>\n'
        '                  </tr>\n'
        '                  <tr>\n'
        '                    <td style="padding:6px 8px;font-weight:bold;color:#27ae60;white-space:nowrap;vertical-align:top">&#11044; RANDOM</td>\n'
        '                    <td style="padding:6px 8px;vertical-align:top">Failures scattered with no spatial pattern. No dominant wafer-level or field-level signature.</td>\n'
        '                    <td style="padding:6px 8px;vertical-align:top;font-size:10px;line-height:1.8;max-width:260px;overflow:hidden" title="Particles / contamination: tool-born particles, ambient contamination, wafer handling damage | Crystal defects: random dislocations, stacking faults, EPI hillocks, substrate pit | Equipment transients: plasma arcing, electrostatic discharge (ESD), pressure/temperature spike | Random dopant fluctuation (RDF): intrinsic statistical variability at advanced nodes (<10nm) | Statistical yield floor: no assignable cause - inherent process capability limit">\n'
        '                      <b>Crystal defects:</b> random dislocations, stacking faults, EPI hillocks, substrate pit<br>\n'
        '                      <b>Equipment transients:</b> plasma arcing, electrostatic discharge (ESD), pressure/temperature spike<br>\n'
        '                      <b>Random dopant fluctuation (RDF):</b> intrinsic statistical variability at advanced nodes (&lt;10nm)<br>\n'
        '                      <b>Statistical yield floor:</b> no assignable cause &mdash; inherent process capability limit\n'
        '                    </td>\n'
        '                  </tr>\n'
        '                </tbody>\n'
        '              </table>\n'
        '              <div style="margin-top:10px;padding:6px 8px;background:#fafafa;border:1px solid #e4e4e4;border-radius:4px;font-size:10px;color:#666;line-height:1.7">\n'
        '                <b>Score interpretation:</b> Each score is 0\u2013100% and measures how strongly the fail die distribution matches that spatial pattern. Scores are <i>not</i> mutually exclusive \u2014 a wafer can show both Center and Systematic signatures simultaneously. Low fail count (n\u003c20) reduces score reliability \u2014 check the n= count in the Fail% column.<br>\n'
        '                <b>Primary</b> is the pattern with the highest score. <b>Driver IB</b> is the bin with the most fail dies (all bins within 80% of the top count are listed).\n'
        '              </div>\n'
        '            </div>\n'
        '          </div>\n'
        +(f'          <div class="wm-tabpane" id="wm-pane-reticle">\n'
          f'            <div class="wm-impact" id="wm-reticle-body" style="padding:8px"><span style="color:#aaa;font-size:11px">Select wafers to view reticle analysis</span></div>\n'
          f'          </div>\n' if _ret_map else '')
        +'          <div class="wm-binrow" id="wm-binrow"></div>\n'
        '          <div class="wm-legend" id="wm-legend"></div>\n'
        '          <div class="wm-note" id="wm-note"></div>\n'
        '        </div>\n'
        '      </div>\n'
        '      <div class="wm-scores" style="flex:0 0 25%;min-height:80px;max-height:40%;background:#fff;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);display:flex;flex-direction:column;overflow:hidden;resize:vertical">\n'
        '        <div style="background:#145a32;color:#fff;font-size:11px;font-weight:bold;padding:4px 10px;flex-shrink:0">&#128202; Pattern Scores</div>\n'
        '        <div class="wm-tbl-wrap"><table class="wm-t"><thead><tr>\n'
        '          <th>Lot</th><th>Wafer</th><th>Material</th><th>Primary</th><th>Fail%</th><th>Driver IB</th><th>Center</th><th title="Edge pattern: excess failures near the wafer edge ring">Edge</th><th title="Donut pattern: ring of failures between center and edge">Donut</th><th title="Systematic: non-random, repeating failures at fixed reticle or process positions (same die XY across wafers)">Syst.</th>'+('<th title="Reticle: fails repeat at the same within-reticle site (rx,ry) across multiple shots \u2014 mask defect / litho weak point">Ret.</th>' if _ret_map else '')+'<th title="Random: failures with no spatial pattern \u2014 scattered across the wafer">Rnd</th>\n'
        '        </tr></thead><tbody id="wm-tbody"></tbody></table></div>\n'
        '      </div>\n'
        '    </div>\n'
        '  </div>\n'
        '</div>\n'
        '<!-- Wafer detail popup -->\n'
        '<div class="wmd-overlay" id="wmd-overlay">\n'
        '  <div class="wmd-box" id="wmd-box">\n'
        '    <div class="wmd-drag" id="wmd-drag"><b id="wmd-title">Wafer Detail</b>\n'
        '      <button onclick="IC._wmdClose()" style="background:none;border:none;color:#fff;font-size:20px;cursor:pointer;line-height:1">&times;</button>\n'
        '    </div>\n'
        '    <div class="wmd-body">\n'
        '      <div class="wmd-col" style="flex:0 0 auto;width:220px">\n'
        '        <div class="wmd-sec" id="wmd-ib-sec">\n'
        '          <div class="wmd-sec-ttl">IB Distribution</div>\n'
        '          <div id="wmd-ib-body"></div>\n'
        '        </div>\n'
        '        <div class="wmd-sec" id="wmd-hw-sec">\n'
        '          <div class="wmd-sec-ttl">HW Breakout</div>\n'
        '          <div id="wmd-hw-body"></div>\n'
        '        </div>\n'
        '      </div>\n'
        '      <div class="wmd-col" style="flex:1;min-width:0;overflow:auto;display:flex;flex-direction:column">\n'
        '        <div style="display:flex;gap:4px;flex-shrink:0;margin-bottom:4px">\n'
        '          <button id="wmd-tab-upm" class="wm-tbtn on" onclick="IC._wmdTabSel(\'upm\',IC._wmdRiVal())" style="font-size:11px">&#128200; Heatmap</button>\n'
        '          <button id="wmd-tab-pat" class="wm-tbtn" onclick="IC._wmdTabSel(\'pattern\',IC._wmdRiVal())" style="font-size:11px">&#128205; Pattern</button>\n'
        '        </div>\n'
        '        <div id="wmd-upm-pane" class="wmd-sec" style="flex:1">\n'
        '          <div class="wmd-sec-ttl">FB Map</div>\n'
        '          <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">\n'
        '            <label style="display:inline-flex;align-items:center;gap:5px;font-size:12px;cursor:pointer;background:#eef2f7;border:1px solid #c8d4e0;border-radius:4px;padding:3px 9px;user-select:none">'
        '<input type="checkbox" id="wmd-hm-chk" onchange="IC._wmdHeatModeSel(this.checked?\'upm\':\'fb\',IC._wmdRiVal())" style="cursor:pointer;width:13px;height:13px">'
        'UPM heatmap</label>'
        '<button style="background:#ecf0f1;border:1px solid #bdc3c7;border-radius:4px;font-size:13px;cursor:pointer;padding:0 7px;line-height:1.6" onclick="IC._wmdZoomOut()" title="Zoom out">&#8722;</button>'
        '<span id="wmd-zoom-lbl" style="font-size:11px;color:#555;min-width:32px;display:inline-block;text-align:center">100%</span>'
        '<button style="background:#ecf0f1;border:1px solid #bdc3c7;border-radius:4px;font-size:13px;cursor:pointer;padding:0 7px;line-height:1.6" onclick="IC._wmdZoomIn()" title="Zoom in">&#43;</button>'
        '<span id="wmd-upm-sel" style="font-size:11px;color:#555"></span>'
        '          </div>\n'
        '          <div id="wmd-upm-body" style="overflow:auto"></div>\n'
        '        </div>\n'
        '        <div id="wmd-pat-pane" class="wmd-sec" style="flex:1;display:none">\n'
        '          <div class="wmd-sec-ttl">&#128205; Pattern Analysis</div>\n'
        '          <div id="wmd-pattern-body" style="overflow:auto;padding:4px"></div>\n'
        '        </div>\n'
        '      </div>\n'
        '    </div>\n'
        '  </div>\n'
        '</div>\n'
        '<!-- DLCP Split in-page modal -->\n'
        '<div class="dlcp-overlay" id="dlcp-overlay">\n'
        '  <div class="dlcp-box" id="dlcp-box">\n'
        '    <div class="dlcp-drag" id="dlcp-drag"><b>&#128202; DLCP Split Analysis</b>\n'
        '      <button onclick="IC.closeDlcpModal()" style="background:none;border:none;color:#fff;font-size:20px;cursor:pointer;line-height:1">&times;</button>\n'
        '    </div>\n'
        '    <div class="dlcp-body">\n'
        '      <div class="dlcp-ctrl">\n'
        '        <label style="font-weight:bold">UPM Threshold:</label>\n'
        '        <input type="range" id="dlcp-sl" min="70" max="100" step="0.5" value="92.5" style="width:180px" oninput="IC.dlcpSlider()">\n'
        '        <input type="number" id="dlcp-tv-inp" min="70" max="100" step="0.5" value="92.5" style="width:64px;font-size:13px;padding:2px 4px;border:1px solid #aac;border-radius:3px;text-align:right" oninput="IC.dlcpTxtInput(this.value)" onchange="IC.dlcpTxtInput(this.value)">\n'
        '        <span style="color:#1a5276;font-size:13px">%</span>\n'
        '        <button onclick="IC.dlcpOpenHistModal()" style="margin-left:12px;padding:4px 16px;font-size:13px;font-weight:bold;background:linear-gradient(135deg,#1a5276,#2980b9);color:#fff;border:none;border-radius:5px;cursor:pointer;box-shadow:0 2px 6px rgba(0,0,0,.25);letter-spacing:.3px" title="Show UPM distribution histogram with stats">&#128202; UPM Distribution</button>\n'
        '        <span id="dlcp-cs"></span>\n'
        '      </div>\n'
        '      <div class="dlcp-sumbox" id="dlcp-sumbox"></div>\n'
        '      <div class="dlcp-inner">\n'
        '        <div class="dlcp-left" id="dlcp-left-pane">\n'
        '          <div class="dlcp-panel-hdr">\n'
        '            <span class="dlcp-sec-ttl" style="flex:1">Per-Wafer Detail</span>\n'
        '            <button class="dlcp-pbtn" onclick="IC.dlcpSelAll()">&#9745; All</button>\n'
        '            <button class="dlcp-pbtn" onclick="IC.dlcpSelNone()">&#9746; None</button>\n'
        '            <button class="dlcp-pbtn" onclick="IC.dlcpDownloadCsv()" title="Download table as CSV">&#8681; CSV</button>\n'
        '            <button class="dlcp-pbtn" onclick="IC.dlcpClearFilters()" title="Clear all column filters">&#10005; Filters</button>\n'
        '          </div>\n'
        '          <div id="dlcp-tbl-pane"><div class="dlcp-tw"><table class="dlcp-t"><thead>\n'
        '            <tr>\n'
        '              <th rowspan="2">Lot <button class="dlcp-ddbtn" id="dlcp-dd-btn-0" onclick="event.stopPropagation();IC.dlcpDdOpen(0,this)" title="Filter">&#9660;</button></th>\n'
        '              <th rowspan="2">Wafer <button class="dlcp-ddbtn" id="dlcp-dd-btn-1" onclick="event.stopPropagation();IC.dlcpDdOpen(1,this)" title="Filter">&#9660;</button></th>\n'
        '              <th rowspan="2">Material <button class="dlcp-ddbtn" id="dlcp-dd-btn-2" onclick="event.stopPropagation();IC.dlcpDdOpen(2,this)" title="Filter">&#9660;</button></th>\n'
        '              <th class="num" rowspan="2">Total</th><th class="num" rowspan="2">Med UPM%</th>\n'
        '              <th class="num" colspan="2" style="background:#1a5276">HP (IB1/2, UPM\u2265thr)</th>\n'
        '              <th class="num" colspan="2" style="background:#7d6608">LP (IB1-4, below thr)</th>\n'
        '              <th class="num" colspan="2" style="background:#7b241c">Fail (IB&gt;4)</th>\n'
        '              <th class="num" colspan="2" style="background:#1a7a4a">FF+DF (IB1-4)</th>\n'
        '              <th class="num" colspan="2" style="background:#1e8449">FF (IB 1,2)</th>\n'
        '              <th class="num" colspan="2" style="background:#117a65">DF (IB 3-4)</th>\n'
        '              <th class="num" colspan="2" style="background:#7d3c98">ATOM DF (IB 3)</th>\n'
        '              <th class="num" colspan="2" style="background:#922b21">CORE DF (IB 4)</th></tr>\n'
        '            <tr><th class="num" style="background:#1a5276">#</th><th class="num" style="background:#1a5276">% of IB1-4</th>\n'
        '              <th class="num" style="background:#7d6608">#</th><th class="num" style="background:#7d6608">% of IB1-4</th>\n'
        '              <th class="num" style="background:#7b241c">#</th><th class="num" style="background:#7b241c">% of total</th>\n'
        '              <th class="num" style="background:#1a7a4a">#</th><th class="num" style="background:#1a7a4a">% of total</th>\n'
        '              <th class="num" style="background:#1e8449">#</th><th class="num" style="background:#1e8449">% of IB1-4</th>\n'
        '              <th class="num" style="background:#117a65">#</th><th class="num" style="background:#117a65">% of IB1-4</th>\n'
        '              <th class="num" style="background:#7d3c98">#</th><th class="num" style="background:#7d3c98">% of IB1-4</th>\n'
        '              <th class="num" style="background:#922b21">#</th><th class="num" style="background:#922b21">% of IB1-4</th></tr>\n'
        '          </thead><tbody id="dlcp-flt-row"></tbody><tbody id="dlcp-tb"></tbody></table></div></div>\n'
        '          <div class="dlcp-note" id="dlcp-note"></div>\n'
        '        </div>\n'
        '        <div class="dlcp-splitter" id="dlcp-splitter" onclick="IC.dlcpSplitterToggle()"><span class="dlcp-split-arrow" id="dlcp-split-arrow">&#9654;</span></div>\n'
        '        <div class="dlcp-cw" id="dlcp-right-pane">\n'
        '          <div class="dlcp-panel-hdr" style="margin-bottom:4px">\n'
        '            <div style="font-size:11px;color:#666;flex:1">CDF of UPM% \u2014 HP/LP (solid) | FF IB1,2 / DF IB3,4 (dashed) | red dashed = threshold</div>\n'
        '            <button class="dlcp-pbtn" onclick="IC.dlcpSavePng()" title="Save CDF as PNG">&#128247; PNG</button>\n'
        '          </div>\n'
        '          <div id="dlcp-plt-pane" style="flex:1;display:flex;flex-direction:column;min-height:0">\n'
        '          <canvas id="dlcp-cv" style="display:block;width:100%;flex:1;border:1px solid #dde;border-radius:4px;min-height:180px"></canvas>\n'
        '          </div>\n'
        '        </div>\n'
        '      </div>\n'
        '    </div>\n'
        '  </div>\n'
        '</div>\n'
        '<!-- UPM Histogram modal -->\n'
        '<div class="upm-hist-overlay" id="upm-hist-overlay" onclick="if(event.target===this)IC.dlcpCloseHistModal()">\n'
        '  <div class="upm-hist-box" id="upm-hist-box">\n'
        '    <div class="upm-hist-drag" id="upm-hist-drag">\n'
        '      <span style="font-weight:bold;font-size:13px">&#128202; UPM% Distribution</span>\n'
        '      <button onclick="IC.dlcpCloseHistModal()" style="background:none;border:none;color:#fff;font-size:20px;cursor:pointer;line-height:1">&times;</button>\n'
        '    </div>\n'
        '    <div class="upm-hist-body">\n'
        '      <div class="upm-hist-stats" id="upm-hist-stats"></div>\n'
        '      <div class="upm-hist-cv-wrap">\n'
        '        <canvas id="upm-hist-cv" style="display:block;width:100%;flex:1;border:1px solid #dde;border-radius:4px;min-height:180px"></canvas>\n'
        '      </div>\n'
        '      <div style="font-size:10px;color:#888;text-align:center;flex-shrink:0">Blue = HP &nbsp;|&nbsp; Orange = LP &nbsp;|&nbsp; Red dashed = threshold &nbsp;|&nbsp; Bars stacked: LP bottom, HP top</div>\n'
        '    </div>\n'
        '  </div>\n'
        '</div>\n'
        '<!-- DLCP dropdown panel -->\n'
        '<div class="dlcp-dd" id="dlcp-dd-panel" style="display:none" onclick="event.stopPropagation()">\n'
        '  <input class="dlcp-dd-srch" id="dlcp-dd-srch" placeholder="&#128269; Search\u2026" oninput="IC.dlcpDdSearch(this.value)">\n'
        '  <div class="dlcp-dd-acts"><button onclick="IC.dlcpDdSelAll()">All</button><button onclick="IC.dlcpDdSelNone()">None</button></div>\n'
        '  <div class="dlcp-dd-list" id="dlcp-dd-list"></div>\n'
        '  <div class="dlcp-dd-foot"><button onclick="IC.dlcpDdApply()">Apply</button></div>\n'
        '</div>\n'
        '        </div>\n'
        '      </div>\n'
        '    </div>\n'
        '  </div>\n'
        '</div>\n'
        '<!-- FB drill-down modal -->\n'
        '<div class="fb-overlay" id="fb-overlay">\n'
        '  <div class="fb-modal" id="fb-modal">\n'
        '    <div class="fb-drag" id="fb-drag">\n'
        '      <b id="fb-modal-title">Functional Bin Breakdown</b>\n'
        '      <button onclick="IC.refreshFb()" style="background:none;border:none;color:#fff;font-size:16px;cursor:pointer;margin-right:8px" title="Refresh">&#x21bb;</button>\n'
        '      <button onclick="IC.closeFbModal()" style="background:none;border:none;color:#fff;font-size:20px;cursor:pointer;line-height:1">&times;</button>\n'
        '    </div>\n'
        '    <div class="fb-modal-inner">\n'
        '    <svg id="fb-chart" class="fb-chart"></svg>\n'
        '    <table class="fb-tbl">\n'
        '      <thead>\n'
        '        <tr><th style="width:60px;white-space:nowrap"><button class="cb" onclick="IC.selectAllFbs()" style="padding:1px 6px;font-size:11px">All</button><button class="cb" onclick="IC.clearFbs()" style="padding:1px 6px;font-size:11px">None</button></th>'
        '<th>Functional Bin</th><th>Category</th><th>Description</th><th class="num">Count</th><th class="num">% of IB</th><th class="num">Fail %</th></tr>\n'
        '      </thead>\n'
        '      <tbody id="fb-modal-tbody"></tbody>\n'
        '    </table>\n'
        '    <div style="padding:4px 0 6px;display:flex;flex-wrap:wrap;gap:4px">'
        '<button class="cb" onclick="IC.showFbWaferMap()">Show Wafer Distribution &#9654;</button>'
        '<button class="cb" onclick="IC.showBhHwModal()">HW Breakdown &#128295;</button>'
        +('<button class="cb" onclick="IC.showUpmModal()">Heatmap &#128202;</button>' if _upm_col_defs and _x_col and _y_col else '')
        +('<button class="cb" id="recov-btn" onclick="IC.showRecovModal()" style="display:none">Bin Analysis &#128300;</button>' if (_has_recovery or (sdt_ib_col and _x_col and _y_col)) else '')
        +'</div>\n'
        '    <div class="fb-wm-sec" id="fb-wm-sec" style="display:none">\n'
        '      <div class="fb-wm-ttl">Wafer Distribution &mdash; IB <span id="fb-wm-ib"></span> (selected FBs) &nbsp;<small style="color:#7f8c8d;font-weight:normal">click tile to jump to wafer</small></div>\n'
        '      <div id="fb-wm-grid" class="fb-wm-grid"></div>\n'
        '    </div>\n'
        '    <div id="recov-sec" style="display:none;padding:10px 0 0 0">\n'
        '      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">\n'
        '        <b style="font-size:13px;color:#2c3e50">Bin Analysis &mdash; IB <span id="recov-ib-lbl"></span></b>\n'
        '        <span id="recov-die-count" style="font-size:11px;color:#5d6b7a"></span>\n'
        '      </div>\n'
        '      <div id="recov-coverage-warn" style="display:none;margin-bottom:6px;padding:5px 8px;border-radius:4px;font-size:11px"></div>\n'
        '      <div id="recov-caption" style="margin-bottom:6px;font-size:11px;color:#7f8c8d">First failing test per AP/CR tracker group. Only dies that ran the recovery screening flow have tracker data &mdash; dies that failed via a different path are not captured.</div>\n'
        '      <div id="recov-grp-filter" style="display:none;margin-bottom:8px;padding:5px 8px;background:#f8f9fa;border:1px solid #e0e0e0;border-radius:4px"></div>\n'
        '      <div id="recov-tbl-wrap" style="overflow-x:auto">\n'
        '        <div id="recov-tbl-content"></div>\n'
        '      </div>\n'
        +(  '      <div id="fb-sdt-sec" style="display:none;padding:8px 0 0 0;border-top:1px solid #e8edf2;margin-top:8px">\n'
            '        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">\n'
            '          <b style="font-size:12px;color:#2c3e50">SDT Groups &mdash; IB <span id="fb-sdt-ib"></span></b>\n'
            '          <span style="font-size:11px;color:#7f8c8d">uncheck to fade on heatmap</span>\n'
            '        </div>\n'
            '        <div id="fb-sdt-cblist" style="display:flex;flex-wrap:wrap;gap:5px"></div>\n'
            '      </div>\n'
        if sdt_ib_col and _x_col and _y_col else '')
        +'    </div>\n'
        +'    </div>\n'
        '  </div>\n'
        '</div>\n'
    )

    # JavaScript — pure ES5-compatible, no external deps, works offline
    import json as _json
    _hw_combo_table_bh_json = _json.dumps(_hw_combo_table_bh)
    _hw_fields_bh_json = _json.dumps([str(c) for c in hw_fields]) if hw_fields else '[]'
    # ── Shared filter JS: provides rFilter, sortFilter, ftDdOpen, toggleRow, selectAllRows, clearRows ──
    _ic_filter_js = make_filter_js(
        on_change_calls='upd();',
        sel_var='sR',
        toggle_fn='IC.toggleRow',
    )
    _html_script = (
      '<script>\nvar DATA=' + _ic_data_json + ';\n'
      'var HW_COMBO_TABLE_BH=' + _hw_combo_table_bh_json + ';\n'
      'var HW_FIELDS_BH=' + _hw_fields_bh_json + ';\n'
      'var RECOV_DATA='     + _recov_data_json     + ';\n'
      'var RECOV_DIE_GRPS=' + _recov_die_grps_json  + ';\n'
      'var RECOV_TRACKED='  + _recov_tracked_json   + ';\n'
      'var RECOV_GROUPS='  + _recov_groups_json  + ';\n'
      'var RECOV_HF='      + _recov_hard_fail_json + ';\n'
      'var BINDESC_DATA='  + _bindesc_data_json  + ';\n'
      'var BINDESC_IBS='   + _bindesc_ibs_json   + ';\n'
      + FILTER_DD_JS
      + r'''var IC=(function(){
'use strict';
var AB=DATA.bins;
var sB=new Set(AB);
var _fbModalIb=null,_fbModalFbKeys=[],_fbChecked=new Set();
var _upmOpen=false,_upmMetricIdx=0,_upmDieLoc=null;
var _upmCanvasMode=true,_upmObserver=null,_upmRenderedRis=new Set();
var _upmLo=0,_upmHi=100,_upmRng=100,_upmIsMHz=false,_upmDivisor=0,_upmHasDieLoc=false;
var _upmZoom=1;
var _dlcpOpen=false,_dlcpT=92.5,_dlcpUi=0;
var _wmOpen=false;
var _sdtSecOpen=false,_sdtChecked=new Set(),_sdtCombos=[];
var _sdtSortCol=0,_sdtSortDir=1,_sdtRows=[];
var _ftSortCol=null,_ftSortDir=-1;  // kept for exportCsv compat — also defined by make_filter_js
var _ftIdxs=DATA.rows.map(function(_,i){return i;});
var _SDT_PALETTE=['#3498db','#e74c3c','#27ae60','#e67e22','#9b59b6','#1abc9c','#f39c12','#2980b9','#c0392b','#16a085'];
var _ySelIdx=-1;
var sR=new Set(DATA.rows.map(function(_,i){return i;}));
var lR=-1;  // kept for any external refs; shared filter uses _ftLR internally
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function dk(h){try{var r=Math.max(0,parseInt(h.slice(1,3),16)-35),g=Math.max(0,parseInt(h.slice(3,5),16)-35),b=Math.max(0,parseInt(h.slice(5,7),16)-35);return 'rgb('+r+','+g+','+b+')';}catch(e){return '#555';}}
function gFC(){
  var c={},t=0;
  sR.forEach(function(i){
    var rw=DATA.rows[i];t+=rw.total;
    Object.keys(rw.binCounts).forEach(function(b){
      var cnt=rw.binCounts[b];
      if(_fbFilterIb!==null&&String(b)===String(_fbFilterIb)){
        var ibFb=(rw.ibToFb||{})[String(_fbFilterIb)]||{};
        if(Object.keys(ibFb).length>0&&_fbChecked.size>0){
          var filtCnt=0;_fbChecked.forEach(function(fb){filtCnt+=(ibFb[fb]||0);});cnt=filtCnt;
        }
        if(_bhHwSel.size>0){
          var ibHw=(rw.ibToHw||{})[String(_fbFilterIb)]||{};
          var hwTot=rw.binCounts[String(_fbFilterIb)]||0,hwSel=0;
          _bhHwSel.forEach(function(idx){hwSel+=(ibHw[idx]||0);});
          if(hwTot>0)cnt=Math.round(cnt*hwSel/hwTot);
        }
      }
      c[b]=(c[b]||0)+cnt;
    });
  });
  return{counts:c,total:t||1};
}
function rChart(){
  var svg=document.getElementById('hist-svg');
  var W=svg.clientWidth||680,H=458,pl=58,pr=14,pt=38,pb=136,cW=W-pl-pr,cH=H-pt-pb;
  var fc=gFC(),cn=fc.counts,tot=fc.total;
  var dB=AB.filter(function(b){return sB.has(b);});
  var showRefLines=(dB.length===AB.length);
  var refLegendEntries=[];
  if(!dB.length){svg.innerHTML='';return;}
  var maxPct=0;
  dB.forEach(function(b){var v=tot>0?(cn[b]||0)/tot*100:0;if(v>maxPct)maxPct=v;});
  if(showRefLines){
    ['1/2','1/2/3/4'].forEach(function(refBins){
      DATA.yieldDefs.forEach(function(def){
        if(String(def.bins||'').replace(/\s+/g,'')!==refBins)return;
        var cnt=def.bins_list.reduce(function(s,b){return s+(cn[b]||0);},0);
        var actual=tot>0?cnt/tot*100:0;
        var expected=def.expected?parseFloat(def.expected):NaN;
        if(actual>maxPct)maxPct=actual;
        if(!isNaN(expected)&&expected>maxPct)maxPct=expected;
      });
    });
  }
  /* Reference line for the selected yield row */
  var ySelLine=null;
  if(_ySelIdx>=0&&DATA.yieldDefs[_ySelIdx]){
    var _ydSel=DATA.yieldDefs[_ySelIdx];
    var _ydCnt=_ydSel.bins_list.reduce(function(s,b){return s+(cn[b]||0);},0);
    var _ydPct=tot>0?_ydCnt/tot*100:0;
    if(_ydPct>maxPct)maxPct=_ydPct;
    ySelLine={value:_ydPct,label:esc(_ydSel.bucket)+' total: '+_ydPct.toFixed(1)+'%'};
  }
  var yMax=maxPct<=0?10:Math.max(5,Math.ceil(maxPct*1.2/5)*5);
  var yStep=yMax<=15?2:yMax<=30?5:yMax<=60?10:20;
  var n=dB.length,step=cW/n,bw=Math.max(3,Math.min(44,step*.74));
  var p=[];
  p.push('<rect width="'+W+'" height="'+H+'" fill="#f8f9fa"/>');
  for(var yg=0;yg<=yMax;yg+=yStep){
    var yp=pt+cH-(yg/yMax)*cH;
    p.push('<line x1="'+pl+'" x2="'+(W-pr)+'" y1="'+yp.toFixed(1)+'" y2="'+yp.toFixed(1)+'" stroke="#e0e0e0" stroke-dasharray="3,3"/>');
    p.push('<text x="'+(pl-5)+'" y="'+(yp+4).toFixed(1)+'" text-anchor="end" font-family="Arial" font-size="13" fill="#555">'+yg+'%</text>');
  }
  p.push('<line x1="'+pl+'" x2="'+pl+'" y1="'+pt+'" y2="'+(pt+cH)+'" stroke="#aaa"/>');
  p.push('<line x1="'+pl+'" x2="'+(W-pr)+'" y1="'+(pt+cH)+'" y2="'+(pt+cH)+'" stroke="#aaa"/>');
  if(showRefLines){
    var refDefs=[
      {bins:'1/2', key:'FF', expectedColor:'#0057ff', actualColor:'#2f80ff'},
      {bins:'1/2/3/4', key:'FF+DF', expectedColor:'#00a83a', actualColor:'#22c55e'}
    ];
    refDefs.forEach(function(ref,ri){
      var def=null;
      DATA.yieldDefs.some(function(d){ if(String(d.bins||'').replace(/\s+/g,'')===ref.bins){ def=d; return true; } return false; });
      if(!def)return;
      var cnt=def.bins_list.reduce(function(s,b){return s+(cn[b]||0);},0);
      var actual=tot>0?cnt/tot*100:0;
      var expected=def.expected?parseFloat(def.expected):NaN;
      var lineMeta=[];
      if(!isNaN(expected))lineMeta.push({value:expected,label:ref.key+' expected '+expected.toFixed(1)+'%',color:ref.expectedColor,dash:'7,4'});
      if(!isNaN(actual))lineMeta.push({value:actual,label:ref.key+' actual '+actual.toFixed(1)+'%',color:ref.actualColor,dash:'2,0'});
      lineMeta.forEach(function(meta,mi){
        if(meta.value<0||meta.value>yMax)return;
        var yp=pt+cH-(meta.value/yMax)*cH;
        p.push('<line x1="'+pl+'" x2="'+(W-pr)+'" y1="'+yp.toFixed(1)+'" y2="'+yp.toFixed(1)+'" stroke="'+meta.color+'" stroke-width="'+(mi===0?3.2:3.8)+'"'+(meta.dash&&meta.dash!=='2,0'?' stroke-dasharray="'+meta.dash+'"':'')+' opacity="0.98"/>');
        refLegendEntries.push({label:meta.label,color:meta.color,dash:meta.dash});
      });
    });
  }
  /* Yield-row reference line */
  if(ySelLine&&ySelLine.value>0&&ySelLine.value<=yMax){
    var _ysyp=pt+cH-(ySelLine.value/yMax)*cH;
    p.push('<line x1="'+pl+'" x2="'+(W-pr)+'" y1="'+_ysyp.toFixed(1)+'" y2="'+_ysyp.toFixed(1)+'" stroke="#9b59b6" stroke-width="2.5" stroke-dasharray="5,3" opacity="0.9"/>');
    refLegendEntries.push({label:ySelLine.label,color:'#9b59b6',dash:'5,3'});
  }
  /* Compute total fail die (bins > 4) for fail% */
  var failTot=0;dB.forEach(function(b){if(b.match(/^\d+$/)&&parseInt(b)>4)failTot+=(cn[b]||0);});
  var ylx=11,yly=pt+cH/2;
  p.push('<text transform="rotate(-90 '+ylx+' '+yly+')" x="'+ylx+'" y="'+yly+'" text-anchor="middle" font-family="Arial" font-size="13" fill="#555">Yield (%)</text>');
  for(var i=0;i<n;i++){
    var bin=dB[i],cnt=cn[bin]||0,pct=tot>0?cnt/tot*100:0;
    var isFail=bin.match(/^\d+$/)&&parseInt(bin)>4;
    var failPct=isFail&&failTot>0?cnt/failTot*100:0;
    var x=pl+i*step+(step-bw)/2,bh=Math.max(0,(pct/yMax)*cH),y=pt+cH-bh;
    var col=DATA.binColors[bin]||'#3498db',stk=dk(col);
    var bkt=DATA.binBuckets[bin]?(' ('+DATA.binBuckets[bin]+')'):'' ;
    var tipExtra=isFail?'&#10;Fail: '+cnt.toLocaleString()+' ('+failPct.toFixed(2)+'% of fail population)':'';
    p.push('<rect data-bin="'+bin+'" x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" width="'+bw.toFixed(1)+'" height="'+bh.toFixed(1)+'" fill="'+col+'" stroke="'+stk+'" stroke-width="0.5" rx="2" style="cursor:pointer" onclick="IC.clickBar(\''+bin+'\')"><title>Bin '+bin+bkt+': '+pct.toFixed(2)+'%&#10;'+cnt.toLocaleString()+' / '+tot.toLocaleString()+' die'+tipExtra+'</title></rect>');
    if(pct>=yMax*0.025){ var lbl=pct.toFixed(1)+'%'+(isFail&&failPct>0?' (F:'+failPct.toFixed(1)+'%)':''); p.push('<text x="'+(x+bw/2).toFixed(1)+'" y="'+(y-3).toFixed(1)+'" text-anchor="middle" font-family="Arial" font-size="11" fill="#333" style="cursor:pointer" onclick="IC.clickBar(\''+bin+'\')">'+lbl+'</text>');}
    var lx=x+bw/2,ly=pt+cH+14;
    p.push('<text x="'+lx.toFixed(1)+'" y="'+ly+'" text-anchor="end" font-family="Arial" font-size="13" fill="#444" transform="rotate(-45 '+lx.toFixed(1)+' '+ly+')" style="cursor:pointer" onclick="IC.clickBar(\''+bin+'\')">'+bin+'</text>');
  }
  var ns=sR.size,nt=DATA.rows.length;
  var ttl=ns<nt?'Yield ('+ns+'/'+nt+' wafers selected)':'Yield Distribution';
  p.push('<text x="'+(pl+cW/2).toFixed(1)+'" y="'+(pt-16)+'" text-anchor="middle" font-family="Arial" font-size="16" font-weight="bold" fill="#2c3e50">'+ttl+'</text>');
  var xAxisLabelY=H-55;
  p.push('<text x="'+(pl+cW/2).toFixed(1)+'" y="'+xAxisLabelY.toFixed(1)+'" text-anchor="middle" font-family="Arial" font-size="13" fill="#555">Interface Bins</text>');
  if(refLegendEntries.length){
    var legendRight=W-pr-8;
    var legendBaseY=pt+cH+34;
    refLegendEntries.forEach(function(meta,idx){
      var ly=legendBaseY+(idx*13);
      p.push('<line x1="'+(legendRight-210)+'" x2="'+(legendRight-188)+'" y1="'+ly+'" y2="'+ly+'" stroke="'+meta.color+'" stroke-width="3"'+(meta.dash&&meta.dash!=='2,0'?' stroke-dasharray="'+meta.dash+'"':'')+'/>' );
      p.push('<text x="'+(legendRight-182)+'" y="'+(ly+4)+'" font-family="Arial" font-size="12" font-weight="bold" fill="'+meta.color+'">'+meta.label+'</text>');
    });
  }
  svg.setAttribute('viewBox','0 0 '+W+' '+H);
  svg.setAttribute('height',H);
  svg.innerHTML=p.join('');
}
function rLegend(){
  var el=document.getElementById('bin-legend');
  var fc=gFC(),cn=fc.counts,tot=fc.total;
  var html='';
  Object.keys(DATA.legendGroups).forEach(function(bkt){
    html+='<div class="lg"><div class="lbk" onclick="IC.toggleBucket(\''+esc(bkt)+'\')" title="Click to select all in group">'+esc(bkt)+'</div>';
    DATA.legendGroups[bkt].forEach(function(bin){
      var active=sB.has(bin),color=DATA.binColors[bin]||'#3498db';
      var cnt=cn[bin]||0,pct=tot>0?cnt/tot*100:0;
      var desc=DATA.binBuckets[bin]||'';
      var _lgFn=DATA.hasFunctionalBin?'IC.legendClick(\''+bin+'\',event)':'IC.clickLegend(\''+bin+'\',event)';
      var _lgTip=DATA.hasFunctionalBin?'Bin '+bin+': click to isolate in chart\nCtrl+click for FB breakdown':'Bin '+bin+': '+esc(DATA.binBuckets[bin]||'')+'\nCtrl+click to multi-select';
      html+='<div class="li'+(active?' la':'')+'" onclick="'+_lgFn+'" title="'+_lgTip+'">';
      html+='<span class="ld" style="background:'+(active?color:'#ccc')+'"></span>';
      html+='<span class="lt"><span class="ln">Bin '+bin+'</span><span class="ldesc">'+esc(desc)+'</span></span>';
      html+='<span class="lmeta">'+pct.toFixed(1)+'%<br>n = '+cnt.toLocaleString()+'</span>';
      html+='</div>';
    });
    html+='</div>';
  });
  el.innerHTML=html;
  /* Re-apply search filter if active */
  var srch=document.getElementById('lg-search');if(srch&&srch.value)lgSearch(srch.value);
}
function lgSearch(q){
  var lo=(q||'').toLowerCase().trim();
  var groups=document.querySelectorAll('#bin-legend .lg');
  groups.forEach(function(grp){
    var anyVis=false;
    grp.querySelectorAll('.li').forEach(function(item){
      var ln=item.querySelector('.ln'),ld=item.querySelector('.ldesc');
      var txt=((ln?ln.textContent:'')+' '+(ld?ld.textContent:'')).toLowerCase();
      var show=!lo||txt.indexOf(lo)>=0;
      item.style.display=show?'':'none';
      if(show)anyVis=true;
    });
    grp.style.display=anyVis?'':'none';
  });
}
// filter JS from _filter_lot_wafer.py: rFilter, sortFilter, ftDdOpen, toggleRow, selectAllRows, clearRows
''' + _ic_filter_js + r'''
function _computeDlcpByFb(){
  // Single pass over selected dies → {fbStr:{hp,lp}} using current _dlcpT/_dlcpUi
  if(!DATA.hasUpm)return null;
  var uI=(DATA.upmStart||5)+_dlcpUi,out={};
  sR.forEach(function(ri){
    var row=DATA.rows[ri];if(!row||!row.dies)return;
    row.dies.forEach(function(d){
      var ib=d[2],fb=d[3],up=d.length>uI?d[uI]:null;
      if(fb===null||fb===undefined)return;
      var fbs=String(fb);
      if(!out[fbs])out[fbs]={hp:0,lp:0};
      if((ib===1||ib===2)&&up!=null&&up>=_dlcpT)out[fbs].hp++;
      else out[fbs].lp++;
    });
  });
  return out;
}
// Classify a material string into 'L0', 'L5', or '' (unknown/empty)
function _matCat(m){
  if(!m||!m.trim())return '';
  var u=m.toUpperCase();
  if(/\bL0\b/.test(u))return 'L0';
  if(/\bL5\b/.test(u))return 'L5';
  return '';
}
function rYield(){
  var fc=gFC(),cn=fc.counts,tot=fc.total;
  var tbody=document.getElementById('yield-tbody');
  var thead=document.getElementById('yield-thead');
  var ysInfo=document.getElementById('ys-info');
  if(ysInfo)ysInfo.textContent='Total Wafers\u202f=\u202f'+sR.size+'\u2002\u2014\u2002n\u202f=\u202f'+tot.toLocaleString()+' dies';
  var _dlcpByFb=_computeDlcpByFb();
  // Determine split mode
  var _splitCb=document.getElementById('ys-split');
  var doSplit=_splitCb?_splitCb.checked:false;
  // Build per-category counts when splitting
  // catCounts[cat] = {bin: count, ...}, catTotals[cat] = total dies
  var _cats=[];
  var _catCounts={},_catTotals={};
  if(doSplit){
    var _catSet={};
    sR.forEach(function(ri){
      var row=DATA.rows[ri];
      var cat=_matCat(row.material||'');
      _catSet[cat]=1;
      if(!_catCounts[cat]){_catCounts[cat]={};_catTotals[cat]=0;}
      _catTotals[cat]+=row.total||0;
      var bc=row.binCounts||{};
      Object.keys(bc).forEach(function(b){_catCounts[cat][b]=(_catCounts[cat][b]||0)+bc[b];});
    });
    // Order: L0, L5, empty — only those present
    ['L0','L5',''].forEach(function(c){if(_catSet[c])_cats.push(c);});
  }
  var hasSplit=doSplit&&_cats.length>=1;
  // Update table layout for Yield Summary: auto when split, fixed when not
  var _ytbl=document.querySelector('.ytbl');
  if(_ytbl){_ytbl.style.tableLayout=hasSplit?'auto':'fixed';}
  // Update header
  if(thead){
    var thHtml='<tr><th>BIN</th><th>FAIL BUCKET</th>';
    if(hasSplit){
      _cats.forEach(function(c){
        var lbl=c==='L0'?'AIO L0':c==='L5'?'AIO_BB L5':'other';
        thHtml+='<th class="num" style="white-space:nowrap">ACTUAL ('+lbl+', %)</th>';
      });
    } else {
      thHtml+='<th class="num">ACTUAL (%)</th>';
    }
    var showDiff=!(hasSplit&&_cats.length>1);
    thHtml+='<th class="num">EXPECTED (%)</th>'+(showDiff?'<th class="num">DIFF (%)</th>':'')+'<th></th></tr>';
    thead.innerHTML=thHtml;
  }
  var html='';
  DATA.yieldDefs.forEach(function(def,di){
    var cnt=def.bins_list.reduce(function(s,b){return s+(cn[b]||0);},0);
    var pct=tot>0?cnt/tot*100:0;
    var exp=def.expected?parseFloat(def.expected):NaN;
    var diff=!isNaN(exp)?(pct-exp):null;
    var hasBin1=def.bins_list.indexOf('1')>=0;
    var diffCls='yn';
    if(diff!==null){
      if(hasBin1){diffCls=diff>0?'yg':diff<0?'yr':'yn';}
      else{diffCls=diff>0?'yr':diff<0?'yg':'yn';}
    }
    var actualCls='';
    if(!isNaN(exp)&&diff!==null&&diff!==0){actualCls=' class="'+diffCls+'"';}
    var hasAny=def.bins_list.some(function(b){return AB.indexOf(b)>=0;});
    var rowCls='yclickable'+(_ySelIdx===di?' ysel':'');
    var rowClick=hasAny?' onclick="IC.selectYieldBins('+di+')" title="Click to filter histogram to these bins; click again to clear"':'';
    // HP/LP annotation from die-level DLCP data
    var _dlcpTag='';
    if(_dlcpByFb){
      var _dhp=0,_dlp=0;
      def.bins_list.forEach(function(b){var e=_dlcpByFb[b];if(e){_dhp+=e.hp;_dlp+=e.lp;}});
      var _dn=_dhp+_dlp;
      if(_dn>0){
        var _hpPct=(_dhp/_dn*100).toFixed(1),_lpPct=(_dlp/_dn*100).toFixed(1);
        _dlcpTag='<br><span style="font-size:10px;white-space:nowrap">'
          +'<span style="color:#1a5276">HP\u202f'+_hpPct+'%\u202f('+_dhp+')</span>'
          +'<span style="color:#aaa">\u202f|\u202f</span>'
          +'<span style="color:#ba6b0a">LP\u202f'+_lpPct+'%\u202f('+_dlp+')</span>'
          +'</span>';
      }
    }
    html+='<tr class="'+rowCls+'"'+rowClick+'>';
    html+='<td>'+esc(def.bins)+'</td><td>'+esc(def.bucket)+'</td>';
    if(hasSplit){
      _cats.forEach(function(c){
        var cCn=_catCounts[c]||{},cTot=_catTotals[c]||0;
        var cCnt=def.bins_list.reduce(function(s,b){return s+(cCn[b]||0);},0);
        var cPct=cTot>0?cCnt/cTot*100:0;
        var cDiff=!isNaN(exp)?(cPct-exp):null;
        var cDiffCls='yn';
        if(cDiff!==null){cDiffCls=hasBin1?(cDiff>0?'yg':cDiff<0?'yr':'yn'):(cDiff>0?'yr':cDiff<0?'yg':'yn');}
        var cCls=((!isNaN(exp)&&cDiff!==null&&cDiff!==0)?' class="num '+cDiffCls+'"':' class="num"');
        html+='<td'+cCls+' style="white-space:nowrap">'+cPct.toFixed(1)+'% <span style="color:#888;font-size:10px">(n\u202f=\u202f'+cCnt.toLocaleString()+')</span></td>';
      });
    } else {
      html+='<td'+(actualCls||'')+'>'+pct.toFixed(1)+'% <span style="color:#888;font-size:10px">(n\u202f=\u202f'+cnt.toLocaleString()+')</span>'+_dlcpTag+'</td>';
    }
    html+='<td>'+(def.expected?def.expected+'%':'')+'</td>';
    if(!(hasSplit&&_cats.length>1)){html+='<td class="'+diffCls+'">'+(diff===null?'\u2014':(diff>0?'+':'')+diff.toFixed(1)+'%')+'</td>';}
    var _abjs='['+def.bins_list.join(',')+']';
    html+='<td><button title="Analyze '+esc(def.bins)+'" onclick="IC._analyzeBins('+_abjs+')" style="background:none;border:1px solid #c8d4e0;border-radius:3px;cursor:pointer;font-size:11px;padding:0 4px;line-height:16px;color:#1a5276">&#128300;</button></td></tr>';
  });
  tbody.innerHTML=html;
}
function rSdt(){
  if(!DATA.hasSdt){return;}
  var combined={};
  sR.forEach(function(ri){
    var row=DATA.rows[ri];
    if(!row.sdtBins)return;
    row.sdtBins.forEach(function(b){
      var key=String(b[0])+'|'+String(b[1])+'|'+String(b[2])+'|'+String(b[3])+'|'+String(b[4]);
      if(!combined[key]){combined[key]={v:b,cnt:0};}
      combined[key].cnt+=b[5];
    });
  });
  var entries=Object.keys(combined).map(function(k){var e=combined[k];return{b:e.v,cnt:e.cnt};});
  // Compute per-SDS-IB totals and grand total for % columns
  var _sdsTotals={};
  var _sdsGrandTotal=0;
  var _fc=gFC(),_totDies=_fc.total;
  entries.forEach(function(e){
    var sk=String(e.b[0]);
    _sdsTotals[sk]=(_sdsTotals[sk]||0)+e.cnt;
    _sdsGrandTotal+=e.cnt;
  });
  var _sNullV=_sdtSortDir>0?1e15:-1e15;
  entries.sort(function(x,y){
    var av,bv;
    if(_sdtSortCol===4){av=String(x.b[4]||'');bv=String(y.b[4]||'');return _sdtSortDir*(av<bv?-1:av>bv?1:0);}
    else if(_sdtSortCol===5){av=x.cnt;bv=y.cnt;}
    else if(_sdtSortCol===6){var xt=_sdsTotals[String(x.b[0])]||0,yt=_sdsTotals[String(y.b[0])]||0;av=xt>0?x.cnt/xt:0;bv=yt>0?y.cnt/yt:0;}
    else if(_sdtSortCol===7){av=_sdsGrandTotal>0?x.cnt/_sdsGrandTotal:0;bv=_sdsGrandTotal>0?y.cnt/_sdsGrandTotal:0;}
    else if(_sdtSortCol===8){av=_totDies>0?x.cnt/_totDies:0;bv=_totDies>0?y.cnt/_totDies:0;}
    else{av=x.b[_sdtSortCol]===null||x.b[_sdtSortCol]===undefined?_sNullV:x.b[_sdtSortCol];bv=y.b[_sdtSortCol]===null||y.b[_sdtSortCol]===undefined?_sNullV:y.b[_sdtSortCol];}
    return _sdtSortDir*(av-bv);
  });
  var html='';
  _sdtRows=entries.map(function(e){var b=e.b;return[b[0]===null||b[0]===undefined?null:b[0],b[1]===null||b[1]===undefined?null:b[1],b[2]===null||b[2]===undefined?null:b[2],b[3]===null||b[3]===undefined?null:b[3],b[4]||'',e.cnt];});
  entries.forEach(function(e){
    var b=e.b;
    var _sdsTot=_sdsTotals[String(b[0])]||0;
    var _pctIb=_sdsTot>0?(e.cnt/_sdsTot*100).toFixed(1)+'%':'\u2014';
    var _pctAll=_sdsGrandTotal>0?(e.cnt/_sdsGrandTotal*100).toFixed(1)+'%':'\u2014';
    var _pctWafer=_totDies>0?(e.cnt/_totDies*100).toFixed(2)+'%':'\u2014';
    html+='<tr>'
      +'<td>'+(b[0]===null||b[0]===undefined?'&mdash;':b[0])+'</td>'
      +'<td>'+(b[1]===null||b[1]===undefined?'&mdash;':b[1])+'</td>'
      +'<td>'+(b[2]===null||b[2]===undefined?'&mdash;':b[2])+'</td>'
      +'<td>'+(b[3]===null||b[3]===undefined?'&mdash;':b[3])+'</td>'
      +'<td>'+esc(b[4]||'')+'</td>'
      +'<td class="num">'+e.cnt.toLocaleString()+'</td>'
      +'<td class="num">'+_pctIb+'</td>'
      +'<td class="num">'+_pctAll+'</td>'
      +'<td class="num">'+(_pctWafer||'\u2014')+'</td>'
      +'</tr>';
  });
  var tbody=document.getElementById('sdt-tbody');
  if(tbody)tbody.innerHTML=html||'<tr><td colspan="9" style="color:#aaa;text-align:center">No data</td></tr>';
  for(var _si=0;_si<9;_si++){var _sh=document.getElementById('sdt-sh-'+_si);if(_sh)_sh.innerHTML=(_si===_sdtSortCol)?(_sdtSortDir>0?'&#9650;':'&#9660;'):'';}
}
function sdtSort(col){
  if(_sdtSortCol===col){_sdtSortDir=-_sdtSortDir;}else{_sdtSortCol=col;_sdtSortDir=1;}
  rSdt();
}
function showSdtSec(){_showSdtSec(_fbModalIb);}
function _showSdtSec(ib){
  if(!DATA.hasSdtDie||ib===null||ib===undefined)return;
  ib=parseInt(ib);
  var sec=document.getElementById('fb-sdt-sec');
  if(!sec)return;
  var ibEl=document.getElementById('fb-sdt-ib');
  if(ibEl)ibEl.textContent=ib;
  var sdtStart=DATA.sdtDieStart||7;
  // Aggregate unique (sdtIb, sdtFb) combos from die data for this IB
  var comboCounts={};
  sR.forEach(function(ri){
    var row=DATA.rows[ri];
    if(!row||!row.dies)return;
    row.dies.forEach(function(d){
      if(d[2]!==ib)return;
      var si=d[sdtStart],sf=d[sdtStart+1];
      if(si===null||si===undefined)return;
      var key=String(si)+'|'+(sf===null||sf===undefined?'':String(sf));
      if(!comboCounts[key])comboCounts[key]={sdtIb:si,sdtFb:sf,count:0};
      comboCounts[key].count++;
    });
  });
  // Build desc lookup from sdtBins aggregation data
  var sdtDescMap={};
  sR.forEach(function(ri){
    var row=DATA.rows[ri];
    if(!row||!row.sdtBins)return;
    row.sdtBins.forEach(function(b){
      if(b[0]!==ib)return;
      var key=String(b[2])+'|'+(b[3]===null||b[3]===undefined?'':String(b[3]));
      if(!sdtDescMap[key]&&b[4])sdtDescMap[key]=b[4];
    });
  });
  var comboKeys=Object.keys(comboCounts);
  comboKeys.sort(function(a,b){return comboCounts[b].count-comboCounts[a].count;});
  _sdtCombos=comboKeys.map(function(k,i){
    var c=comboCounts[k];
    return{key:k,sdtIb:c.sdtIb,sdtFb:c.sdtFb,desc:sdtDescMap[k]||'',count:c.count,color:_SDT_PALETTE[i%_SDT_PALETTE.length]};
  });
  _sdtChecked=new Set(comboKeys);
  // Render combo checkboxes
  var cbHtml='';
  _sdtCombos.forEach(function(combo){
    cbHtml+='<label style="display:flex;align-items:center;gap:4px;font-size:12px;cursor:pointer;padding:3px 8px;border-radius:3px;background:#fff;border:1px solid #cdd5e0;white-space:nowrap">'
      +'<input type="checkbox" checked data-sdtkey="'+esc(combo.key)+'" onchange="IC._sdtCbChange(this)">'
      +'SDT IB '+combo.sdtIb+(combo.sdtFb!==null&&combo.sdtFb!==undefined?' / SDT FB '+combo.sdtFb:'')+': '+esc(combo.desc||'—')
      +' <span style="color:#999;font-size:10px">'+combo.count.toLocaleString()+'</span>'
      +'</label>';
  });
  var cbEl=document.getElementById('fb-sdt-cblist');
  if(cbEl)cbEl.innerHTML=cbHtml||'<span style="color:#aaa;font-size:12px">No SDT die data for IB '+ib+'</span>';
  sec.style.display='block';
  _sdtSecOpen=true;
}
function _sdtCbChange(cb){
  var key=cb.dataset.sdtkey;
  if(cb.checked)_sdtChecked.add(key);else _sdtChecked.delete(key);
  if(_upmOpen)_renderUpmMaps();else showFbWaferMap();
}
function selectYieldBins(di){
  if(_ySelIdx===di){_ySelIdx=-1;sB=new Set(AB);}
  else{
    _ySelIdx=di;
    var def=DATA.yieldDefs[di];
    var valid=def.bins_list.filter(function(b){return AB.indexOf(b)>=0;});
    sB=new Set(valid);
  }
  upd();
}
function upd(){
  rChart();rLegend();rFilter();rYield();if(DATA.hasSdt)rSdt();
  if(window._updatePareto)_updatePareto();
  document.getElementById('sel-info').textContent=
    sR.size<DATA.rows.length?'('+sR.size+'/'+DATA.rows.length+' wafers)':'';
  /* Cascade to open panels */
  if(_fbFilterIb!==null){refreshFb();}
  if(_bhHwOpen){_renderHwSection();}
  if(_upmOpen){_renderUpmMaps();}
  if(_dlcpOpen){_dlcpRender();}
  if(_wmOpen){_wmRender();}
  if(_recovOpen){_renderRecov();}
  if(_sdtSecOpen){if(_upmOpen)_renderUpmMaps();else{var _fbwmEl=document.getElementById('fb-wm-sec');if(_fbwmEl&&_fbwmEl.style.display!=='none')showFbWaferMap();}}
  _wmRenderInline();
}
function clickBar(bin){
  if(DATA.hasFunctionalBin){showFbModal(bin);}
  else{clickLegend(bin,null);}
}
/* Module-level state for FB modal re-render */
var _fbModalTotals={},_fbModalIbTotal=0,_fbModalAllTot=0;
var _fbFilterIb=null; /* IB currently filtered in histogram by FB/HW selection */
var _wmdFbScopeRi=null; /* when set: fb-modal is scoped to a single wafer row index */
var _recovOpen=false; /* Bin Recovery Analysis panel visible */
var _recovGrpChecked=new Set(); /* AP/CR group filter — empty=no filter, non-empty=filter by selected groups */
var _fbCbTimer=null; /* debounce handle for FB checkbox expensive redraws */
function showFbModal(ib){
  _wmdFbScopeRi=null; /* clear wafer-scope: this is a multi-wafer aggregation */
  /* Aggregate FB counts for the given IB across selected wafers */
  var fbTotals={},ibTotal=0;
  var ibCat=DATA.binBuckets[String(ib)]||'';
  sR.forEach(function(i){
    var row=DATA.rows[i];if(!row)return;
    var fbMap=(row.ibToFb||{})[String(ib)];
    if(fbMap){Object.keys(fbMap).forEach(function(fb){fbTotals[fb]=(fbTotals[fb]||0)+fbMap[fb];});}
    ibTotal+=(row.binCounts[String(ib)]||0);
  });
  var fbKeys=Object.keys(fbTotals).sort(function(a,b){return fbTotals[b]-fbTotals[a];});
  if(!fbKeys.length){
    document.getElementById('fb-modal-title').textContent='Interface Bin '+ib+' \u2014 No Functional Bin data';
    document.getElementById('fb-chart').innerHTML='';
    document.getElementById('fb-modal-tbody').innerHTML='<tr><td colspan="6" style="color:#888">No FB data available for this IB</td></tr>';
    var _fm0=document.getElementById('fb-modal');
    if(_fm0){_fm0.style.left='';_fm0.style.top='';_fm0.style.transform='';}
    document.getElementById('fb-overlay').classList.add('open');
    return;
  }
  var fc=gFC();
  /* Store state so _renderFbChart can re-render on FB toggle */
  _fbModalIb=ib;_fbModalFbKeys=fbKeys.slice();_fbChecked=new Set(fbKeys);
  _fbFilterIb=ib;
  _fbModalTotals=fbTotals;_fbModalIbTotal=ibTotal;_fbModalAllTot=fc.total;
  _renderFbCb();
  _renderFbChart();
  /* If HW popup is open for a different IB, reset it */
  if(_bhHwOpen){
    document.getElementById('bh-hw-modal-title').textContent='HW Breakdown \u2014 IB '+ib;
    _bhHwSel.clear();
    _renderHwSection();
  }
  var fwm=document.getElementById('fb-wm-sec');if(fwm)fwm.style.display='none';
  /* Show/hide Recovery button + section based on IB */
  var _rbtn=document.getElementById('recov-btn');
  if(_rbtn)_rbtn.style.display=(
    (RECOV_GROUPS&&RECOV_GROUPS[String(ib)]&&RECOV_GROUPS[String(ib)].length>0)||
    (BINDESC_IBS&&BINDESC_IBS[String(ib)])||
    (DATA.hasSdtDie&&parseInt(ib)>=1&&parseInt(ib)<=4))?'':'none';
  var _rsec=document.getElementById('recov-sec');
  if(_rsec)_rsec.style.display='none';
  _recovOpen=false;
  _recovGrpChecked=new Set((RECOV_GROUPS&&RECOV_GROUPS[String(ib)])||[]);
  var _sdtsec=document.getElementById('fb-sdt-sec');
  if(_sdtsec)_sdtsec.style.display='none';
  _sdtSecOpen=false;
  var _fm=document.getElementById('fb-modal');
  if(_fm){_fm.style.left='';_fm.style.top='';_fm.style.transform='';}
  document.getElementById('fb-overlay').classList.add('open');
}
/* ---- Wafer-scoped FB analysis: opens fb-modal for a single wafer × IB ---- */
function _wmdShowFbForWafer(ibk,ri){
  var row=DATA.rows[ri];if(!row)return;
  var ib=parseInt(ibk,10);
  var fbMap=(row.ibToFb||{})[String(ib)]||{};
  var fbTotals={};
  Object.keys(fbMap).forEach(function(fb){fbTotals[fb]=fbMap[fb];});
  var ibTotal=row.binCounts[String(ib)]||0;
  var fbKeys=Object.keys(fbTotals).sort(function(a,b){return fbTotals[b]-fbTotals[a];});
  var ibCat=DATA.binBuckets[String(ib)]||'';
  var rowLbl=(row.lot||'')+(row.wafer?' W'+row.wafer:'');
  /* Set wafer scope so refreshFb re-reads from this wafer only */
  _wmdFbScopeRi=ri;
  _fbModalIb=ib;_fbModalFbKeys=fbKeys.slice();_fbChecked=new Set(fbKeys);
  _fbFilterIb=ib;
  _fbModalTotals=fbTotals;_fbModalIbTotal=ibTotal;_fbModalAllTot=row.total||ibTotal;
  /* Update title to indicate wafer scope */
  var titleEl=document.getElementById('fb-modal-title');
  if(titleEl)titleEl.textContent='IB'+ib+(ibCat?' ['+ibCat+']':'')+' \u2014 '+rowLbl+' \u2014 FB Breakdown ('+ibTotal.toLocaleString()+' die)';
  _renderFbCb();_renderFbChart();
  /* Reset HW popup if open */
  if(_bhHwOpen){
    document.getElementById('bh-hw-modal-title').textContent='HW Breakdown \u2014 IB'+ib+' \u2014 '+rowLbl;
    _bhHwSel.clear();_renderHwSection();
  }
  var fwm=document.getElementById('fb-wm-sec');if(fwm)fwm.style.display='none';
  /* Show/hide recovery button */
  var _rbtn=document.getElementById('recov-btn');
  if(_rbtn)_rbtn.style.display=(
    (RECOV_GROUPS&&RECOV_GROUPS[String(ib)]&&RECOV_GROUPS[String(ib)].length>0)||
    (BINDESC_IBS&&BINDESC_IBS[String(ib)])||
    (DATA.hasSdtDie&&ib>=1&&ib<=4))?'':'none';
  var _rsec=document.getElementById('recov-sec');if(_rsec)_rsec.style.display='none';
  _recovOpen=false;
  _recovGrpChecked=new Set((RECOV_GROUPS&&RECOV_GROUPS[String(ib)])||[]);
  var _sdtsec=document.getElementById('fb-sdt-sec');if(_sdtsec)_sdtsec.style.display='none';
  _sdtSecOpen=false;
  var _fm2=document.getElementById('fb-modal');
  if(_fm2){_fm2.style.left='';_fm2.style.top='';_fm2.style.transform='';}
  document.getElementById('fb-overlay').classList.add('open');
}
function _renderFbChart(){
  if(!_fbModalIb)return;
  var fbTotals=_fbModalTotals,ibTotal=_fbModalIbTotal,allTot=_fbModalAllTot;
  var fbKeys=_fbModalFbKeys;
  var ibCat=DATA.binBuckets[String(_fbModalIb)]||'';
  var fbDesc=DATA.fbDescriptions||{};
  /* Compute checked-FB total for title */
  var chkTotal=0;
  fbKeys.forEach(function(fb){if(_fbChecked.has(fb))chkTotal+=(fbTotals[fb]||0);});
  var chkIbPct=ibTotal>0?chkTotal/ibTotal*100:0;
  var chkFailPct=allTot>0?chkTotal/allTot*100:0;
  var titleSuffix=(_fbChecked.size<fbKeys.length)?' \u2014 '+_fbChecked.size+'/'+fbKeys.length+' FBs':'';
  var titlePcts=' \u2502 '+chkIbPct.toFixed(1)+'% of IB \u2502 '+chkFailPct.toFixed(2)+'% fail';
  document.getElementById('fb-modal-title').textContent=
    'Interface Bin '+_fbModalIb+(ibCat?' ['+ibCat+']':'')+
    ' \u2014 Functional Bin Breakdown ('+chkTotal.toLocaleString()+' / '+ibTotal.toLocaleString()+' die'+titlePcts+')'+titleSuffix;
  /* Render SVG bar chart — only checked FBs, bars resize based on selection */
  var svg=document.getElementById('fb-chart');
  /* Filter to only checked FBs for chart (up to 30) */
  var chkFbs=fbKeys.filter(function(fb){return _fbChecked.has(fb);});
  var unchkFbs=fbKeys.filter(function(fb){return !_fbChecked.has(fb);});
  var allShow=fbKeys.slice(0,30); /* always show all for label positioning */
  var n=Math.min(fbKeys.length,30);
  var W=svg.clientWidth||750,pl=58,pr=14,pt=12,pb=10;
  /* Reserve right-side space for inline label text so bars don't overflow */
  var labelW=200;
  var cW=Math.max(60,W-pl-pr-labelW);
  /* Height based on total rows (checked shown full, unchecked shown as thin grey) */
  var barH=Math.max(5,Math.min(14,11));
  var gap=3;
  var H=Math.max(80,n*(barH+gap)+pt+pb+24);
  var cH=H-pt-pb;
  var maxCnt=0;
  allShow.forEach(function(fb){if(_fbChecked.has(fb)&&fbTotals[fb]>maxCnt)maxCnt=fbTotals[fb];});
  if(maxCnt===0){allShow.forEach(function(fb){if(fbTotals[fb]>maxCnt)maxCnt=fbTotals[fb];});}
  if(maxCnt===0)maxCnt=1;
  var p=[];
  p.push('<rect width="'+W+'" height="'+H+'" fill="#f8f9fa" rx="4"/>');
  var PAL=['#e74c3c','#e67e22','#f39c12','#2ecc71','#1abc9c','#3498db','#9b59b6','#e91e63','#00bcd4','#8bc34a','#ff9800','#795548'];
  for(var i=0;i<n;i++){
    var fb=allShow[i],cnt=fbTotals[fb],pct=ibTotal>0?cnt/ibTotal*100:0;
    var fbi=fbDesc[fb]||{};
    var fbDsc=fbi.desc||'';
    var y=pt+gap+(barH+gap)*i;
    var sel=_fbChecked.has(fb);
    var clr=sel?PAL[i%PAL.length]:'#ddd';
    var bw=sel?Math.max(2,(cnt/maxCnt)*cW):2;
    var fbFailPct=allTot>0?cnt/allTot*100:0;
    var txtClr=sel?'#444':'#bbb';
    p.push('<rect x="'+pl+'" y="'+y.toFixed(1)+'" width="'+bw.toFixed(1)+'" height="'+barH.toFixed(1)+'" fill="'+clr+'" rx="3"><title>FB'+fb+(ibCat?' ['+ibCat+']':'')+(fbDsc?' '+fbDsc:'')+': '+cnt.toLocaleString()+' ('+pct.toFixed(1)+'%)</title></rect>');
    p.push('<text x="'+(pl-4)+'" y="'+(y+barH/2+4).toFixed(1)+'" text-anchor="end" font-family="Arial" font-size="12" fill="'+txtClr+'">FB'+fb+'</text>');
    if(sel){
      var lbl=cnt.toLocaleString()+' ('+pct.toFixed(1)+'% IB | '+fbFailPct.toFixed(2)+'% fail)'+(fbDsc?' \u2014 '+esc(fbDsc.substring(0,45)):'');
      p.push('<text x="'+(pl+bw+5).toFixed(1)+'" y="'+(y+barH/2+4).toFixed(1)+'" font-family="Arial" font-size="11" fill="#555">'+lbl+'</text>');
    }
  }
  if(fbKeys.length>n){
    p.push('<text x="'+(pl+10)+'" y="'+(H-4)+'" font-family="Arial" font-size="11" fill="#888">\u2026 and '+(fbKeys.length-n)+' more bins</text>');
  }
  svg.setAttribute('viewBox','0 0 '+W+' '+H);
  svg.setAttribute('height',H);
  svg.innerHTML=p.join('');
  /* Render table — unchecked rows dimmed */
  var tbody=document.getElementById('fb-modal-tbody');
  var html='';
  var tblSelCount=0;
  fbKeys.forEach(function(fb){
    var cnt=fbTotals[fb];
    var pct=ibTotal>0?cnt/ibTotal*100:0;
    var fbi=fbDesc[fb]||{};
    var fbFP=allTot>0?cnt/allTot*100:0;
    var op=_fbChecked.has(fb)?'':'opacity:0.3;';
    if(_fbChecked.has(fb))tblSelCount+=cnt;
    var chkAttr=_fbChecked.has(fb)?' checked':'';
    html+='<tr style="'+op+'"><td style="text-align:center"><input type="checkbox"'+chkAttr+' data-fb="'+fb+'" onchange="IC.fbCbChange(this)"></td><td>FB'+fb+'</td><td>'+esc(fbi.cat||ibCat)+'</td><td>'+esc(fbi.desc||'')+'</td><td class="num">'+cnt.toLocaleString()+'</td><td class="num">'+pct.toFixed(1)+'%</td><td class="num">'+fbFP.toFixed(2)+'%</td></tr>';
  });
  /* Total row for selected FBs */
  if(fbKeys.length>1){
    var tblSelIbPct=ibTotal>0?tblSelCount/ibTotal*100:0;
    var tblSelFailPct=allTot>0?tblSelCount/allTot*100:0;
    html+='<tr style="background:#d6eaff;font-weight:bold;border-top:2px solid #2471a3">'
      +'<td></td><td colspan="3" style="color:#1a3a5c">Total (selected '+_fbChecked.size+' / '+fbKeys.length+')</td>'
      +'<td class="num" style="color:#1a3a5c">'+tblSelCount.toLocaleString()+'</td>'
      +'<td class="num" style="color:#2471a3">'+tblSelIbPct.toFixed(1)+'%</td>'
      +'<td class="num" style="color:#c0392b">'+tblSelFailPct.toFixed(2)+'%</td>'
      +'</tr>';
  }
  tbody.innerHTML=html;
  /* Refresh HW popup if open */
  if(_bhHwOpen)_renderHwSection();
}
/* --- HW Breakdown draggable popup --- */
var _bhHwSel=new Set();
var _bhHwOpen=false;
var _hwColFilter={};  /* col name -> text filter string */
var _hwAllEntries=[];  /* [{lot,wafer,hwIdx,cnt}] — populated by _renderHwSection */
var _hwGroupByCols=null;  /* Set of active group-by col names; null = all */
function showBhHwModal(){
  if(!_fbModalIb)return;
  _hwGroupByCols=null;  /* reset on each open */
  _bhHwOpen=true;
  document.getElementById('bh-hw-modal-title').textContent='HW Breakdown \u2014 IB '+_fbModalIb;
  document.getElementById('bh-hw-modal').classList.add('open');
  _renderHwSection();
}
function closeBhHwModal(){
  document.getElementById('bh-hw-modal').classList.remove('open');
  _bhHwOpen=false;
  _bhHwSel.clear();
  _hwColFilter={};
  if(_fbFilterIb!==null){rChart();}
}
function _renderHwSection(){
  var hwBody=document.getElementById('bh-hw-body');
  if(!hwBody)return;
  var tbl=HW_COMBO_TABLE_BH||[];var cols=HW_FIELDS_BH||[];
  if(!tbl.length||!cols.length||!_fbModalIb){hwBody.innerHTML='<p style="color:#888;padding:8px">No HW data available.</p>';return;}
  /* Build per-(lot, wafer, hwIdx) entries */
  var entries=[];var grandTotal=0;
  sR.forEach(function(i){
    var row=DATA.rows[i];if(!row)return;
    var ibHw=(row.ibToHw||{})[String(_fbModalIb)]||{};
    var ibFb=(row.ibToFb||{})[String(_fbModalIb)]||{};
    var ibTot=row.binCounts[String(_fbModalIb)]||0;
    var fbTotal=0;_fbChecked.forEach(function(fb){fbTotal+=(ibFb[fb]||0);});
    var ratio=ibTot>0?(Object.keys(ibFb).length>0?fbTotal/ibTot:1):1;
    Object.keys(ibHw).forEach(function(hwIdx){
      var cnt=Math.round(ibHw[hwIdx]*ratio);
      if(cnt>0){entries.push({lot:row.lot||'',wafer:row.wafer||'',hwIdx:hwIdx,cnt:cnt});grandTotal+=cnt;}
    });
  });
  _hwAllEntries=entries;
  if(!entries.length){hwBody.innerHTML='<p style="color:#888;padding:8px">No HW data for IB '+_fbModalIb+'.</p>';return;}
  entries.sort(function(a,b){return b.cnt-a.cnt;});
  /* Apply preferred display column order — Sort Partial Wafer ID always last */
  var _hwPrefOrder=['Cell ID','Unit Tester ID','Unit Tester Site ID','CellID','UnitTesterID','UnitTesterSiteID','Unit TIU','Thermal Head Id'];
  var orderedCols=_hwPrefOrder.filter(function(c){return cols.indexOf(c)>=0;}).concat(cols.filter(function(c){return _hwPrefOrder.indexOf(c)<0&&c.toLowerCase().indexOf('sort partial wafer')<0;})).concat(cols.filter(function(c){return c.toLowerCase().indexOf('sort partial wafer')>=0;}));
  /* Initialise group-by to all HW columns on first open */
  if(_hwGroupByCols===null){_hwGroupByCols=new Set(orderedCols);}
  var activeGroupCols=orderedCols.filter(function(c){return _hwGroupByCols.has(c);});
  /* Group entries by active cols */
  var groupMap={};
  entries.forEach(function(e){
    var combo=tbl[parseInt(e.hwIdx)]||{};
    var key=activeGroupCols.length>0?activeGroupCols.map(function(c){return String(combo[c]||'');}).join('\x00'):('__hw__'+e.hwIdx);
    if(!groupMap[key])groupMap[key]={lot:e.lot,wafer:e.wafer,hwIdx:e.hwIdx,cnt:0,combo:combo};
    groupMap[key].cnt+=e.cnt;
  });
  var grouped=Object.values(groupMap).sort(function(a,b){return b.cnt-a.cnt;});
  var displayCols=activeGroupCols.length>0?activeGroupCols:orderedCols;
  var fixedCols=['Lot','Wafer'];
  var allDisplayCols=fixedCols.concat(displayCols);
  /* Apply per-column text filters to grouped entries */
  var filteredG=grouped.filter(function(e){
    var pass=true;
    Object.keys(_hwColFilter).forEach(function(c){
      if(!pass)return;
      var q=_hwColFilter[c].toLowerCase();
      var v;
      if(c==='Lot'){v=e.lot;}
      else if(c==='Wafer'){v=e.wafer;}
      else{v=String(e.combo[c]||'');}
      if(v.toLowerCase().indexOf(q)<0)pass=false;
    });
    return pass;
  });
  /* Build Group By bar */
  var gbBar='<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:5px 6px;background:#f0f4ff;border-radius:4px;border:1px solid #c5d4f0;margin-bottom:7px">'
    +'<span style="font-size:11px;font-weight:bold;color:#2c3e50;white-space:nowrap">Group By:</span>';
  orderedCols.forEach(function(c){
    var chk=_hwGroupByCols.has(c)?'checked':'';
    gbBar+='<label style="font-size:11px;display:flex;align-items:center;gap:3px;cursor:pointer;white-space:nowrap">'
      +'<input type="checkbox" '+chk+' data-hwgb="'+esc(c)+'" onchange="IC.hwGbChange(this)"> '+esc(c)+'</label>';
  });
  gbBar+='<button class="cb" style="font-size:11px;padding:1px 7px" onclick="IC.hwGbAll()">All</button>'
    +'<button class="cb" style="font-size:11px;padding:1px 7px" onclick="IC.hwGbNone()">None</button></div>';
  var hdr='<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap">'
    +'<span style="color:#888;font-size:12px">'+filteredG.length+' / '+grouped.length+' rows &nbsp;&bull;&nbsp; '+grandTotal.toLocaleString()+' die est</span>'
    +'<button class="cb" style="font-size:11px;padding:1px 7px" onclick="IC.bhHwSelAll()">All</button>'
    +'<button class="cb" style="font-size:11px;padding:1px 7px" onclick="IC.bhHwClrAll()">None</button>'
    +'<button class="cb" style="font-size:11px;padding:1px 7px" onclick="IC.bhHwClrColFilters()">Clear Filters</button></div>';
  var filterCols=['Lot','Wafer'].concat(displayCols);
  var th='<tr><th style="width:30px"></th>'+['Lot','Wafer','Count','%'].concat(displayCols).map(function(c){
    return'<th style="text-align:left;white-space:normal;word-wrap:break-word">'+esc(c)+'</th>';
  }).join('')+'</tr>';
  var filterRow='<tr><td></td>'+['Lot','Wafer','Count','%'].concat(displayCols).map(function(c){
    if(c==='Count'||c==='%')return'<td></td>';
    var val=_hwColFilter[c]||'';
    return'<td><input type="text" data-hw-fcol="'+esc(c)+'" value="'+esc(val)+'" placeholder="\u2026" style="width:100%;box-sizing:border-box;font-size:11px;padding:2px 4px;border:1px solid #ccc;border-radius:3px" oninput="IC.hwTxtFilter(this)"></td>';
  }).join('')+'</tr>';
  var hwSel=_bhHwSel;
  var trs=filteredG.map(function(e){
    var pct=grandTotal>0?(e.cnt/grandTotal*100).toFixed(1):'0.0';
    var sel=hwSel.size===0||hwSel.has(e.hwIdx);
    var chk=sel?'checked':'';
    var op=sel?'1':'0.4';
    return '<tr style="opacity:'+op+'">'
      +'<td><input type="checkbox" data-hw-bh="'+e.hwIdx+'" '+chk+' onclick="IC.bhHwChk(this)"></td>'
      +'<td>'+esc(e.lot)+'</td><td>'+esc(e.wafer)+'</td>'
      +'<td>'+e.cnt.toLocaleString()+'</td><td>'+pct+'%</td>'
      +displayCols.map(function(c){return'<td>'+esc(String(e.combo[c]||''))+'</td>';}).join('')
      +'</tr>';
  }).join('');
  hwBody.innerHTML=gbBar+hdr+'<div style="overflow-y:auto;flex:1;min-height:0"><table class="stbl" style="width:100%;table-layout:auto"><thead>'+th+filterRow+'</thead><tbody>'+trs+'</tbody></table></div>';
}
function bhHwChk(cb){
  var all=document.querySelectorAll('#bh-hw-body input[type=checkbox]');
  var anyUn=false;all.forEach(function(c){if(!c.checked)anyUn=true;});
  _bhHwSel.clear();
  if(anyUn){all.forEach(function(c){if(c.checked)_bhHwSel.add(c.dataset.hwBh);});}
  _renderHwSection();refreshFb();if(_upmOpen)_renderUpmMaps();
}
function bhHwSelAll(){_bhHwSel.clear();_renderHwSection();refreshFb();if(_upmOpen)_renderUpmMaps();}
function bhHwClrAll(){_bhHwSel.clear();_bhHwSel.add('__none__');_renderHwSection();refreshFb();if(_upmOpen)_renderUpmMaps();}
function bhHwClrColFilters(){_hwColFilter={};_bhHwSel.clear();_renderHwSection();refreshFb();if(_upmOpen)_renderUpmMaps();}
function hwGbChange(cb){
  if(!_hwGroupByCols)return;
  var col=cb.getAttribute('data-hwgb');
  if(cb.checked){_hwGroupByCols.add(col);}else{_hwGroupByCols.delete(col);}
  _renderHwSection();refreshFb();if(_upmOpen)_renderUpmMaps();
}
function hwGbAll(){_hwGroupByCols=null;_renderHwSection();refreshFb();if(_upmOpen)_renderUpmMaps();}
function hwGbNone(){_hwGroupByCols=new Set();_renderHwSection();refreshFb();if(_upmOpen)_renderUpmMaps();}
function hwTxtFilter(inp){
  var col=inp.getAttribute('data-hw-fcol');
  var val=(inp.value||'').trim();
  var cursorPos=inp.selectionStart;
  if(val){_hwColFilter[col]=val;}else{delete _hwColFilter[col];}
  _syncHwTxtFilter();
  _renderHwSection();refreshFb();if(_upmOpen)_renderUpmMaps();
  /* Restore focus to the same filter input after re-render */
  var restored=document.querySelector('input[data-hw-fcol="'+col+'"]');
  if(restored){restored.focus();restored.selectionStart=restored.selectionEnd=cursorPos;}
}
function _syncHwTxtFilter(){
  var tbl=HW_COMBO_TABLE_BH||[];var cols=HW_FIELDS_BH||[];
  var hasFilter=Object.keys(_hwColFilter).length>0;
  _bhHwSel.clear();
  if(!hasFilter)return;
  _hwAllEntries.forEach(function(e){
    var combo=tbl[parseInt(e.hwIdx)]||{};
    var pass=true;
    Object.keys(_hwColFilter).forEach(function(c){
      if(!pass)return;
      var q=_hwColFilter[c].toLowerCase();
      var v;
      if(c==='Lot'){v=e.lot;}
      else if(c==='Wafer'){v=e.wafer;}
      else{v=String(combo[c]||'');}
      if(v.toLowerCase().indexOf(q)<0)pass=false;
    });
    if(pass)_bhHwSel.add(e.hwIdx);
  });
  if(_bhHwSel.size===0)_bhHwSel.add('__none__');
}
function refreshFb(){
  if(_fbModalIb===null)return;
  /* If in wafer-scope mode, re-aggregate from that single wafer only */
  if(_wmdFbScopeRi!==null){_wmdShowFbForWafer(String(_fbModalIb),_wmdFbScopeRi);return;}
  var ib=_fbModalIb;
  var fbTotals={},ibTotal=0;
  sR.forEach(function(i){
    var row=DATA.rows[i];if(!row)return;
    var ibTot=row.binCounts[String(ib)]||0;
    var hwRatio=1;
    if(_bhHwSel.size>0){
      var ibHw=(row.ibToHw||{})[String(ib)]||{};
      var hwSel=0;_bhHwSel.forEach(function(idx){hwSel+=(ibHw[idx]||0);});
      hwRatio=ibTot>0?hwSel/ibTot:0;
    }
    var fbMap=(row.ibToFb||{})[String(ib)];
    if(fbMap){Object.keys(fbMap).forEach(function(fb){fbTotals[fb]=(fbTotals[fb]||0)+Math.round(fbMap[fb]*hwRatio);});}
    ibTotal+=Math.round(ibTot*hwRatio);
  });
  var fbKeys=Object.keys(fbTotals).sort(function(a,b){return fbTotals[b]-fbTotals[a];});
  var fc=gFC();
  _fbModalFbKeys=fbKeys.slice();_fbModalTotals=fbTotals;_fbModalIbTotal=ibTotal;_fbModalAllTot=fc.total;
  _renderFbCb();_renderFbChart();rChart();
}
function refreshUpm(){if(_upmOpen)_renderUpmMaps();}
var _upmFbDdOpen=false;
var _upmFbSearchVal='';
function _upmFbDdToggle(){
  _upmFbDdOpen=!_upmFbDdOpen;
  var dd=document.getElementById('upm-fb-dd');if(dd)dd.style.display=_upmFbDdOpen?'':'none';
  var btn=document.getElementById('upm-fb-dd-btn');if(btn)btn.innerHTML=(_upmFbDdOpen?'&#9650;':'&#9660;')+' Choose FBs';
  if(_upmFbDdOpen){
    _upmFbSearchVal='';var si=document.getElementById('upm-fb-search');if(si)si.value='';
    _renderFbCb();
    /* close on outside click */
    setTimeout(function(){
      document.addEventListener('click',function _upmFbOut(e){
        var dd2=document.getElementById('upm-fb-dd'),btn2=document.getElementById('upm-fb-dd-btn');
        if((dd2&&dd2.contains(e.target))||(btn2&&btn2.contains(e.target)))return;
        document.removeEventListener('click',_upmFbOut);
        _upmFbDdOpen=false;
        if(dd2)dd2.style.display='none';
        if(btn2)btn2.innerHTML='&#9660; Choose FBs';
      });
    },0);
  }
}
function _upmFbSearch(v){_upmFbSearchVal=(v||'').toLowerCase().trim();_renderFbCbList();}
function _renderFbCbList(){
  var upmCbList=document.getElementById('upm-fb-cblist');if(!upmCbList)return;
  var ibCat=DATA.binBuckets[String(_fbModalIb)]||'';
  var fbDesc=DATA.fbDescriptions||{};
  var ibTotal=_fbModalIbTotal,allTot=_fbModalAllTot;
  var q=_upmFbSearchVal;
  var html='<table style="width:100%;border-collapse:collapse;font-size:12px">';
  html+='<thead><tr style="background:#2c3e50;color:#ecf0f1">';
  html+='<th style="padding:3px 6px;width:28px"></th>';
  html+='<th style="padding:3px 6px;text-align:left">FB</th>';
  html+='<th style="padding:3px 6px;text-align:left">Category</th>';
  html+='<th style="padding:3px 6px;text-align:left">Description</th>';
  html+='<th style="padding:3px 6px;text-align:right">Count</th>';
  html+='<th style="padding:3px 6px;text-align:right">% IB</th>';
  html+='<th style="padding:3px 6px;text-align:right">Fail%</th>';
  html+='</tr></thead><tbody>';
  var shown=0;
  _fbModalFbKeys.forEach(function(fb,idx){
    var fbi=fbDesc[fb]||{};
    var desc=fbi.desc||'';
    var fbCat=fbi.cat||ibCat;
    if(q&&('fb'+fb).toLowerCase().indexOf(q)<0&&desc.toLowerCase().indexOf(q)<0&&fbCat.toLowerCase().indexOf(q)<0)return;
    shown++;
    var chk=_fbChecked.has(fb)?' checked':'';
    var cnt=_fbModalTotals[fb]||0;
    var ibPct=ibTotal>0?cnt/ibTotal*100:0;
    var failPct=allTot>0?cnt/allTot*100:0;
    var bg=idx%2===0?'#fff':'#f7f9fc';
    var op=_fbChecked.has(fb)?'':'opacity:0.45;';
    html+='<tr data-fb="'+fb+'" style="background:'+bg+';'+op+'">';
    html+='<td style="text-align:center;padding:3px 4px"><input type="checkbox"'+chk+' data-fb="'+fb+'" onchange="IC.fbCbChange(this)"></td>';
    html+='<td style="padding:3px 6px;white-space:nowrap;font-weight:bold">FB'+esc(fb)+'</td>';
    html+='<td style="padding:3px 6px;white-space:nowrap">'+esc(fbCat)+'</td>';
    html+='<td style="padding:3px 6px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+esc(desc)+'">'+esc(desc)+'</td>';
    html+='<td style="padding:3px 6px;text-align:right">'+cnt.toLocaleString()+'</td>';
    html+='<td style="padding:3px 6px;text-align:right;color:#2471a3">'+ibPct.toFixed(1)+'%</td>';
    html+='<td style="padding:3px 6px;text-align:right;color:#c0392b">'+failPct.toFixed(2)+'%</td>';
    html+='</tr>';
  });
  if(shown===0)html+='<tr><td colspan="7" style="padding:6px;color:#888;text-align:center">No FBs match</td></tr>';
  html+='</tbody></table>';
  upmCbList.innerHTML=html;
}
function _renderFbCb(){
  /* Update summary badge */
  var sumEl=document.getElementById('upm-fb-summary');
  if(sumEl){
    var sel=_fbChecked.size,tot=_fbModalFbKeys.length;
    sumEl.textContent=sel<tot?(sel+'/'+tot+' FBs selected'):(tot+' FBs (all)')
  }
  /* Rebuild dropdown list if open */
  if(_upmFbDdOpen)_renderFbCbList();
  /* Show/hide bar */
  var upmBar=document.getElementById('upm-fb-filter');
  if(upmBar)upmBar.style.display=(_fbModalFbKeys.length>0)?'':'none';
}
function legendClick(bin,event){
  if(event&&(event.ctrlKey||event.metaKey)){showFbModal(bin);}
  else{clickLegend(bin,event);}
}
function fbCbChange(cb){var fb=cb.dataset.fb;if(cb.checked)_fbChecked.add(fb);else _fbChecked.delete(fb);
  /* Sync the other checkbox for this FB (table row vs UPM dropdown) */
  document.querySelectorAll('input[type=checkbox][data-fb="'+fb+'"]').forEach(function(el){el.checked=_fbChecked.has(fb);});
  /* Sync row opacity in UPM dropdown list */
  if(_upmFbDdOpen)(document.querySelectorAll('#upm-fb-cblist tr[data-fb="'+fb+'"]')||[]).forEach(function(tr){tr.style.opacity=_fbChecked.has(fb)?'':'0.45';});
  /* Update summary badge */
  _renderFbCb();
  _renderFbChart();rChart(); /* fast: pre-aggregated binCounts, no die iteration */
  var _fwmEl=document.getElementById('fb-wm-sec');
  if(_fwmEl&&_fwmEl.style.display!=='none')showFbWaferMap(); /* refresh wafer tiles if visible */
  /* debounce only the expensive per-die redraws */
  if(_fbCbTimer)clearTimeout(_fbCbTimer);
  _fbCbTimer=setTimeout(function(){_fbCbTimer=null;if(_upmOpen)_renderUpmMaps();if(_recovOpen)_renderRecov();},120);}
function selectAllFbs(){if(_fbCbTimer){clearTimeout(_fbCbTimer);_fbCbTimer=null;}_fbModalFbKeys.forEach(function(fb){_fbChecked.add(fb);});_renderFbChart();_renderFbCb();rChart();if(_upmOpen)_renderUpmMaps();if(_recovOpen)_renderRecov();}
function clearFbs(){if(_fbCbTimer){clearTimeout(_fbCbTimer);_fbCbTimer=null;}_fbChecked.clear();_renderFbChart();_renderFbCb();rChart();if(_upmOpen)_renderUpmMaps();if(_recovOpen)_renderRecov();}
function showFbWaferMap(){
  if(!_fbModalIb)return;
  /* Sync FB checkbox state from table */
  document.querySelectorAll('#fb-modal-tbody input[type=checkbox]').forEach(function(inp){
    if(inp.checked)_fbChecked.add(inp.dataset.fb);else _fbChecked.delete(inp.dataset.fb);
  });
  var sec=document.getElementById('fb-wm-sec'),grid=document.getElementById('fb-wm-grid'),ibEl=document.getElementById('fb-wm-ib');
  if(!sec||!grid)return;
  if(ibEl)ibEl.textContent=_fbModalIb;
  /* ── SDT die-map mode ── */
  if(_sdtSecOpen&&DATA.hasSdtDie){
    var _sdtIb=parseInt(_fbModalIb);
    var _sdtStart=DATA.sdtDieStart||7;
    var _sdtFill=_wmIbColor(_sdtIb);
    var _sdtHtml='';
    sR.forEach(function(ri){
      var row=DATA.rows[ri];
      if(!row||!row.dies||!row.dies.length)return;
      var xs=[],ys=[];
      row.dies.forEach(function(d){if(d[0]!==null&&d[0]!==undefined&&d[2]===_sdtIb){xs.push(d[0]);ys.push(d[1]);}});
      if(!xs.length)return;
      var xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs);
      var yMin=Math.min.apply(null,ys),yMax=Math.max.apply(null,ys);
      var pad=2,FIXED_W=140;
      var xCnt=xMax-xMin+1,yCnt=yMax-yMin+1;
      var cs=Math.max(1,(FIXED_W-pad*2)/xCnt);
      var W=FIXED_W,H=Math.round(yCnt*cs+pad*2);
      var rects=[];
      row.dies.forEach(function(d){
        var x=d[0],y=d[1],ibV=d[2];
        if(x===null||x===undefined||ibV!==_sdtIb)return;
        var si=d[_sdtStart],sf=d[_sdtStart+1];
        var dkey=si===null||si===undefined?null:String(si)+'|'+(sf===null||sf===undefined?'':String(sf));
        var fill,opacity;
        if(dkey!==null&&_sdtChecked.has(dkey)){fill=_sdtFill;opacity='1';}
        else{fill='#b0b8c4';opacity='0.22';}
        var px=(pad+(x-xMin)*cs).toFixed(2),py=(pad+(yMax-y)*cs).toFixed(2);
        var tipStr='('+x+','+y+') IB '+ibV+' SDT_IB '+(si===null?'\u2014':si)+' SDT_FB '+(sf===null?'\u2014':sf);
        rects.push('<rect x="'+px+'" y="'+py+'" width="'+(cs*0.9).toFixed(2)+'" height="'+(cs*0.9).toFixed(2)+'" fill="'+fill+'" opacity="'+opacity+'" data-tip="'+tipStr+'"/>');
      });
      if(!rects.length)return;
      var lbl=(row.lot||'')+' W'+(row.wafer||'');
      _sdtHtml+='<div style="text-align:center"><div style="font-size:10px;font-weight:bold;color:#2c3e50;margin-bottom:2px">'+esc(lbl)+'</div><svg width="'+W+'" height="'+H+'" style="display:block">'+rects.join('')+'</svg></div>';
    });
    grid.innerHTML=_sdtHtml||'<span style="color:#aaa;font-size:12px">No die data for IB '+_sdtIb+'</span>';
    if(!grid._sdtTipBound){
      grid._sdtTipBound=true;
      var _tip=document.createElement('div');
      _tip.style.cssText='position:fixed;pointer-events:none;background:rgba(30,40,60,0.92);color:#f5f7fa;font-size:11px;padding:3px 8px;border-radius:3px;z-index:9999;display:none;white-space:nowrap';
      document.body.appendChild(_tip);
      grid.addEventListener('mousemove',function(ev){var t=ev.target;if(t&&t.tagName==='rect'&&t.dataset&&t.dataset.tip){_tip.style.display='block';_tip.style.left=(ev.clientX+12)+'px';_tip.style.top=(ev.clientY-8)+'px';_tip.textContent=t.dataset.tip;}else{_tip.style.display='none';}});
      grid.addEventListener('mouseleave',function(){_tip.style.display='none';});
    }
    var _ttl=document.querySelector('#fb-wm-sec .fb-wm-ttl');
    if(_ttl)_ttl.innerHTML='SDT Die Locations &mdash; IB <span id="fb-wm-ib">'+_fbModalIb+'</span> &nbsp;<small style="color:#7f8c8d;font-weight:normal">uncheck group to fade</small>';
    sec.style.display='block';
    return;
  }
  var waferData=[],maxCnt=0;
  sR.forEach(function(i){
    var row=DATA.rows[i];if(!row)return;
    var ibFbMap=((row.ibToFb||{})[String(_fbModalIb)])||{};
    var cnt=0;_fbChecked.forEach(function(fb){cnt+=(ibFbMap[fb]||0);});
    var ibTotal=row.binCounts[String(_fbModalIb)]||0;
    waferData.push({lot:row.lot,wafer:row.wafer,material:row.material||'',cnt:cnt,ibTotal:ibTotal,idx:i});
    if(cnt>maxCnt)maxCnt=cnt;
  });
  waferData.sort(function(a,b){return b.cnt-a.cnt;});
  var html='';
  waferData.forEach(function(wd){
    var intensity=maxCnt>0?wd.cnt/maxCnt:0;
    var r=Math.round(220-intensity*130),g=Math.round(235-intensity*130),b=255;
    var fg=intensity>0.55?'#fff':'#1a2a4a';
    var bg='rgb('+r+','+g+','+b+')';
    var pct=wd.ibTotal>0?(wd.cnt/wd.ibTotal*100).toFixed(1):'0.0';
    var lotShort=String(wd.lot).length>10?String(wd.lot).slice(-10):String(wd.lot);
    html+='<div class="fb-wm-tile" style="background:'+bg+';color:'+fg+'" onclick="IC.fbTileClick('+wd.idx+')"'
      +' title="'+esc(wd.lot)+' Wafer '+esc(wd.wafer)+': '+wd.cnt.toLocaleString()+' die with selected FBs / '+wd.ibTotal.toLocaleString()+' total IB die">'
      +'<div class="fb-wm-lot" style="color:'+fg+'">'+esc(lotShort)+'</div>'
      +'<div class="fb-wm-wfr" style="color:'+fg+'">W'+esc(wd.wafer)+'</div>'
      +(DATA.hasMaterial&&wd.material?'<div class="fb-wm-mat" style="color:'+fg+'">'+esc(wd.material)+'</div>':'')
      +'<div class="fb-wm-cnt" style="color:'+fg+'">'+wd.cnt.toLocaleString()+'<br><small>'+pct+'% of IB</small></div>'
      +'</div>';
  });
  if(!html)html='<div style="color:#888;padding:10px">No data for selected FBs</div>';
  grid.innerHTML=html;
  sec.style.display='block';
}
function fbTileClick(rowIdx){
  document.getElementById('fb-overlay').classList.remove('open');
  // Reset all column filters so the target row is guaranteed visible in the tbody
  Object.keys(_ftDdState).forEach(function(k){_ftDdState[k]=null;});
  for(var _ci=0;_ci<5;_ci++){var _b=document.getElementById('ft-fb-'+_ci);if(_b)_b.classList.remove('active');}
  sR.clear();sR.add(rowIdx);lR=rowIdx;upd();
  // Navigate the parent frame to the wafermap for this specific lot+wafer
  if(typeof WM_URL==='string'&&WM_URL){
    var _wmRow=DATA.rows[rowIdx];
    var _wmTarget=WM_URL;
    if(typeof WM_FILES==='object'&&WM_FILES&&_wmRow){
      var _lotUrl=WM_FILES[String(_wmRow.lot)];
      if(_lotUrl)_wmTarget=_lotUrl+'#wafer-'+encodeURIComponent(String(_wmRow.wafer));
    }
    try{var par=window.parent;if(par&&par.__pl){par.__pl(_wmTarget);} else{var f=par.document.getElementById('frame');if(f){f.src=_wmTarget;}else{throw 0;}}}
    catch(e){try{window.parent.postMessage({navFrame:_wmTarget},'*');}catch(e2){}}
  }
  setTimeout(function(){
    var trs=document.querySelectorAll('#filter-tbody tr');
    if(trs[rowIdx])trs[rowIdx].scrollIntoView({behavior:'smooth',block:'nearest'});
  },150);
}
function closeFbModal(e){
  _fbFilterIb=null;
  _wmdFbScopeRi=null; /* clear wafer scope on close */
  document.getElementById('fb-overlay').classList.remove('open');
  closeBhHwModal();
  var _sdtsec2=document.getElementById('fb-sdt-sec');
  if(_sdtsec2)_sdtsec2.style.display='none';
  _sdtSecOpen=false;
  rChart();
}
/* Open FB modal for the bin in the group that has the most dies */
function _analyzeBins(bins){
  if(!bins||!bins.length)return;
  var fc=gFC(),cn=fc.counts;
  var best=bins[0],bestCnt=cn[String(best)]||0;
  bins.forEach(function(b){var c=cn[String(b)]||0;if(c>bestCnt){bestCnt=c;best=b;}});
  showFbModal(best);
}
function clickLegend(bin,event){
  _ySelIdx=-1;
  if(event&&(event.ctrlKey||event.metaKey)){
    if(sB.has(bin)){if(sB.size>1)sB.delete(bin);}else sB.add(bin);
  }else{
    if(sB.size===1&&sB.has(bin)){AB.forEach(function(b){sB.add(b);});}
    else{sB.clear();sB.add(bin);}
  }
  upd();
}
function toggleBucket(bkt){
  _ySelIdx=-1;
  var bins=DATA.legendGroups[bkt]||[];
  var all=bins.every(function(b){return sB.has(b);});
  if(all){bins.forEach(function(b){if(sB.size>1)sB.delete(b);});}
  else{bins.forEach(function(b){sB.add(b);});}
  upd();
}
function toggleAllBins(state){
  _ySelIdx=-1;
  if(state){AB.forEach(function(b){sB.add(b);});}
  else{sB.clear();if(AB.length)sB.add(AB[0]);}
  upd();
}
// _visibleIdxs / toggleRow / selectAllRows / clearRows now from make_filter_js()
// Backward-compat alias so any code calling _visibleIdxs() still works
function _visibleIdxs(){return _visibleFtIdxs();}
if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',function(){upd();window.addEventListener('resize',rChart);_setupBhHwDrag();_setupFbDrag();_setupUpmDrag();});
}else{upd();window.addEventListener('resize',rChart);_setupBhHwDrag();_setupFbDrag();_setupUpmDrag();}
function _setupFbDrag(){
  var drag=document.getElementById('fb-drag');
  var box=document.getElementById('fb-modal');
  if(!drag||!box)return;
  var dX=0,dY=0,dragging=false;
  drag.addEventListener('mousedown',function(e){
    dragging=true;
    var r=box.getBoundingClientRect();
    box.style.left=r.left+'px';box.style.top=r.top+'px';box.style.transform='none';
    dX=e.clientX-r.left;dY=e.clientY-r.top;e.preventDefault();
  });
  document.addEventListener('mousemove',function(e){
    if(!dragging)return;
    var nx=e.clientX-dX,ny=e.clientY-dY;
    var mw=window.innerWidth-box.offsetWidth,mh=window.innerHeight-box.offsetHeight;
    box.style.left=Math.max(0,Math.min(mw,nx))+'px';
    box.style.top=Math.max(0,Math.min(mh,ny))+'px';
  });
  document.addEventListener('mouseup',function(){dragging=false;});
}
function _setupBhHwDrag(){
  var drag=document.getElementById('bh-hw-drag');
  var box=document.getElementById('bh-hw-box');
  if(!drag||!box)return;
  var dX=0,dY=0,dragging=false;
  drag.addEventListener('mousedown',function(e){
    dragging=true;
    var r=box.getBoundingClientRect();
    if(box.style.left==='50%'||!box.style.left){
      box.style.left=r.left+'px';box.style.top=r.top+'px';box.style.transform='none';
    }
    dX=e.clientX-r.left;dY=e.clientY-r.top;e.preventDefault();
  });
  document.addEventListener('mousemove',function(e){
    if(!dragging)return;
    var nx=e.clientX-dX,ny=e.clientY-dY;
    var mw=window.innerWidth-box.offsetWidth,mh=window.innerHeight-box.offsetHeight;
    box.style.left=Math.max(0,Math.min(mw,nx))+'px';
    box.style.top=Math.max(0,Math.min(mh,ny))+'px';
  });
  document.addEventListener('mouseup',function(){dragging=false;});
}
function showUpmModal(){
  if(!DATA.hasUpm)return;
  _upmOpen=true;
  var m=document.getElementById('upm-box');
  if(m){m.style.left='';m.style.top='';m.style.transform='';}
  document.getElementById('upm-modal').classList.add('open');
  _renderFbCb(); /* show FB filter bar if an IB is active */
  _renderUpmMaps();
}
function closeUpmModal(){
  _upmOpen=false;
  /* reset dropdown state */
  _upmFbDdOpen=false;
  var dd=document.getElementById('upm-fb-dd');if(dd)dd.style.display='none';
  var btn=document.getElementById('upm-fb-dd-btn');if(btn)btn.innerHTML='&#9660; Choose FBs';
  document.getElementById('upm-modal').classList.remove('open');
}
function setUpmMetric(idx){_upmMetricIdx=idx;_renderUpmMaps();}
function _upmDieLocToggle(n){
  if(_upmDieLoc===null){
    var _all=new Set();Object.values(DATA.retSiteNum||{}).forEach(function(v){_all.add(+v);});_upmDieLoc=_all;
  }
  if(_upmDieLoc.has(n)){_upmDieLoc.delete(n);if(_upmDieLoc.size===0)_upmDieLoc=null;}
  else{_upmDieLoc.add(n);}
  _renderUpmMaps();
}
function _upmDieLocAll(){_upmDieLoc=null;_renderUpmMaps();}
/* AP/CR group filter — drives FB histogram and heatmap */
/* _recovGrpChecked empty = no filter (show all FBs); non-empty = filter to matching FBs */
function _recovGrpChk(cb){
  var g=cb.dataset.grp;
  if(cb.checked)_recovGrpChecked.add(g);else _recovGrpChecked.delete(g);
  _applyRecovGrpFilter();
  _renderRecov();
}
function _recovGrpClrAll(){_recovGrpSetNone();}
function _recovGrpSetAll(){
  var grps=(RECOV_GROUPS&&_fbModalIb)?RECOV_GROUPS[String(_fbModalIb)]||[]:[];
  _recovGrpChecked=new Set(grps);
  _applyRecovGrpFilter();
  _renderRecov();
}
function _recovGrpSetNone(){
  _recovGrpChecked.clear();
  _applyRecovGrpFilter();
  _renderRecov();
}
/* per-die group check: true if die at (x,y) in wafer wk has any checked group */
function _dieGrpActive(wk,x,y){
  var dg=RECOV_DIE_GRPS&&RECOV_DIE_GRPS[wk]&&RECOV_DIE_GRPS[wk][Math.round(x)+'|'+Math.round(y)];
  return dg?dg.some(function(g){return _recovGrpChecked.has(g);}):false;
}
function _applyRecovGrpFilter(){
  if(_recovGrpChecked.size===0){
    /* No groups checked — dim all dies */
    _fbChecked=new Set();
  }else{
    /* Filter to FBs that have failures in any checked group */
    var selKeys=[];
    sR.forEach(function(i){var row=DATA.rows[i];if(!row)return;selKeys.push(row.lot+'|'+row.wafer);});
    var keys=(selKeys.length===DATA.rows.length)?['all']:selKeys;
    var _ibFbSet=new Set(_fbModalFbKeys);
    var fbsWithGroup=new Set();
    keys.forEach(function(wk){
      var wData=RECOV_DATA&&RECOV_DATA[wk];if(!wData)return;
      Object.keys(wData).forEach(function(fb){
        if(!_ibFbSet.has(fb))return; /* restrict to current IB */
        var rows=wData[fb];if(!rows)return;
        var hit=rows.some(function(r){
          return Object.keys(r.byGroup).some(function(g){return _recovGrpChecked.has(g)&&r.byGroup[g]>0;});
        });
        if(hit)fbsWithGroup.add(fb);
      });
    });
    /* Fall back to all IB FBs if no RECOV_DATA found for selected groups */
    _fbChecked=fbsWithGroup.size>0?fbsWithGroup:new Set(_fbModalFbKeys);
  }
  /* Sync FB checkboxes in the table + UPM bar */
  document.querySelectorAll('#fb-modal-tbody input[type=checkbox],#upm-fb-cblist input[type=checkbox]').forEach(function(inp){
    inp.checked=_fbChecked.has(inp.dataset.fb);
  });
  _renderFbChart();
  rChart();
  if(_upmOpen)_renderUpmMaps();
  if(_wmdOpen&&_wmdRi>=0)_wmdRender(_wmdRi);
}
/* ── Bin Recovery Analysis ─────────────────────────────────────────────── */
function showRecovModal(){
  var sec=document.getElementById('recov-sec');
  if(!sec)return;
  if(_recovOpen){
    sec.style.display='none';_recovOpen=false;
    _sdtSecOpen=false;
    _fbChecked=new Set(_fbModalFbKeys); /* restore full FB set on close */
    _renderFbChart();rChart();
    if(_upmOpen)_renderUpmMaps();
    if(_wmdOpen&&_wmdRi>=0)_wmdRender(_wmdRi);
    return;
  }
  _recovOpen=true;
  sec.style.display='block';
  _applyRecovGrpFilter(); /* sync _fbChecked + heatmap with current group selection */
  _renderRecov();
  if(DATA.hasSdtDie&&_fbModalIb!==null&&parseInt(_fbModalIb)>=1&&parseInt(_fbModalIb)<=4){
    _showSdtSec(_fbModalIb);
  }
}
/* Classify test name into defect category (per spec detection checkpoint table) */
function _recovCategory(test){
  if(!test)return{label:'',bg:'#eee',fg:'#333'};
  var t=test.toUpperCase();
  if(t.indexOf('STUCKAT')>=0)         return{label:'Scan Stuckat',bg:'#e74c3c',fg:'#fff'};
  if(t.indexOf('ATSPEED')>=0&&t.indexOf('VMIN')>=0) return{label:'Scan Vmin',bg:'#e67e22',fg:'#fff'};
  if(t.indexOf('CHAIN')>=0)           return{label:'Scan Chain',bg:'#c0392b',fg:'#fff'};
  if((t.indexOf('LSA')>=0||t.indexOf('SSA')>=0||t.indexOf('XSA')>=0)&&t.indexOf('VMIN')>=0)
                                       return{label:'Array Vmin',bg:'#f39c12',fg:'#fff'};
  if(t.indexOf('SBFT')>=0&&t.indexOf('VMIN')>=0) return{label:'Func Vmin',bg:'#8e44ad',fg:'#fff'};
  if(t.indexOf('SCBD')>=0)            return{label:'Scan Defect',bg:'#c0392b',fg:'#fff'};
  if(t.indexOf('VMIN')>=0)            return{label:'Vmin',bg:'#d35400',fg:'#fff'};
  if(t.indexOf('TPI_')>=0||t.startsWith('TPI_')) return{label:'TPI',bg:'#5d6d7e',fg:'#fff'};
  if(t.indexOf('_RESET_')>=0||t.indexOf('DRV_RESET')>=0) return{label:'Reset',bg:'#7f8c8d',fg:'#fff'};
  return{label:'Other',bg:'#7f8c8d',fg:'#fff'};
}
/* ── Bin Description section (one table, grouped by Scan/Array/Func/TPI/Other) ── */
function _buildBDSection(bdMerged,bdTotalDie,ib){
  var tests=Object.keys(bdMerged).sort(function(a,b){return bdMerged[b]-bdMerged[a];});
  if(!tests.length)return'';
  var _bdGrpOrder=['Scan','Array','Func','TPI','Other'];
  var _bdGrp={};
  tests.forEach(function(test){
    var tp=test.indexOf('::')>=0?test.split('::')[0]:test;
    var tpu=tp.toUpperCase();
    var gn=/^SCN_/.test(tpu)?'Scan':/^ARR_/.test(tpu)?'Array':/^FUN_/.test(tpu)?'Func':/^TPI_/.test(tpu)?'TPI':'Other';
    if(!_bdGrp[gn])_bdGrp[gn]=[];
    _bdGrp[gn].push(test);
  });
  var s='<div style="margin:4px 0 2px;padding:3px 7px;background:#1a5276;border-left:3px solid #2980b9;font-size:11px;font-weight:bold;color:#fff">'
    +'Bin Description \u2014 '+bdTotalDie.toLocaleString()+' total IB'+esc(ib)+' die (100% coverage)</div>';
  s+='<table class="fb-tbl" style="width:100%;margin-bottom:10px">';
  s+='<thead><tr><th>Type</th><th>Failing Test (Bin Description)</th>'
   +'<th class="num">Total</th><th class="num">%\u00b9</th></tr>';
  s+='<tr><td colspan="4" style="font-size:10px;color:#888;padding:1px 4px 3px">'
   +'\u00b9 unique dies with this bin setter \u00f7 total IB'+esc(ib)+' die count.</td></tr></thead><tbody>';
  _bdGrpOrder.forEach(function(gn){
    var gtests=_bdGrp[gn];if(!gtests||!gtests.length)return;
    s+='<tr><td colspan="4" style="background:#2c3e50;color:#ecf0f1;font-size:10px;font-weight:bold;padding:2px 7px;letter-spacing:0.5px">'+esc(gn)+'</td></tr>';
    gtests.forEach(function(test){
      var tot=bdMerged[test];
      var pct=bdTotalDie>0?(tot/bdTotalDie*100).toFixed(1):'\u2014';
      var cat=_recovCategory(test);
      var parts=test.split('::');
      var dTest=parts.length>=2
        ?'<span style="color:#aaa;font-size:10px">'+esc(parts[0])+'::\u00a0</span>'+esc(parts.slice(1).join('::'))
        :esc(test);
      s+='<tr>'
        +'<td style="white-space:nowrap"><span style="background:'+cat.bg+';color:'+cat.fg+';font-size:10px;padding:1px 5px;border-radius:3px;white-space:nowrap">'+esc(cat.label)+'</span></td>'
        +'<td style="font-size:11px;word-break:break-all"><span title="'+esc(test)+'">'+dTest+'</span></td>'
        +'<td class="num"><b>'+tot+'</b></td>'
        +'<td class="num">'+pct+'%</td></tr>';
    });
  });
  s+='</tbody></table>';
  return s;
}
function _renderRecov(){
  var sec=document.getElementById('recov-sec');
  if(!sec||!_recovOpen||!_fbModalIb)return;
  var ib=String(_fbModalIb);
  var grps=(RECOV_GROUPS&&RECOV_GROUPS[ib])||[];
  var isHF=!!(RECOV_HF&&RECOV_HF[ib]);
  var hasBD=!!(BINDESC_IBS&&BINDESC_IBS[ib]);
  /* Nothing to show if neither Bin Description nor LOGTRACKER data for this IB */
  if(!grps.length&&!hasBD)return;
  var _ibLabels={'1':'Full Function','2':'Defeatured Function','3':'ATOM Recovery',
    '4':'Core Recovery','8':'DC Fail','19':'Reset Fail','26':'HVQK Stress',
    '41':'ATOM Hard Fail — Functional','42':'ATOM Hard Fail — Scan Stuckat'};
  var ibLbl=document.getElementById('recov-ib-lbl');
  if(ibLbl)ibLbl.textContent=ib+(_ibLabels[ib]?' — '+_ibLabels[ib]:'');
  var _capEl=document.getElementById('recov-caption');
  if(_capEl){
    _capEl.innerHTML=hasBD
      ?'Bin setter from the DLCP <b>Bin Description</b> column &mdash; 100% die coverage.'
       +(grps.length>0?' AP/CR/SLCE LOGTRACKER detail shown below.':'')
      :'First failing test per AP/CR/SLCE tracker group. Only dies that ran the recovery screening flow have tracker data &mdash; dies that failed via a different path are not captured.';
  }
  /* Group filter checkboxes — AP/CR groups drive FB histogram + heatmap */
  var _isAp0=function(g){return/^AP/i.test(g);};
  var _isCr0=function(g){return/^CR/i.test(g);};
  var _isSlce0=function(g){return/^SLCE/i.test(g);};
  var _apGrps0=grps.filter(_isAp0);
  var _crGrps0=grps.filter(_isCr0);
  var _slceGrps0=grps.filter(_isSlce0);
  var _gfDiv=document.getElementById('recov-grp-filter');
  if(_gfDiv){
    if(_apGrps0.length+_crGrps0.length+_slceGrps0.length>0){
      var _allGrpsChk=(grps.length>0&&grps.every(function(g){return _recovGrpChecked.has(g);}))?' checked':'';
      var _noneChk=(_recovGrpChecked.size===0)?' checked':'';
      var _gh='<label style="font-size:11px;margin-right:9px;cursor:pointer;font-weight:bold">'
        +'<input type="checkbox"'+_allGrpsChk+' onchange="IC._recovGrpSetAll()" style="margin-right:3px">All</label>'
        +'<label style="font-size:11px;margin-right:12px;cursor:pointer;font-weight:bold">'
        +'<input type="checkbox"'+_noneChk+' onchange="IC._recovGrpSetNone()" style="margin-right:3px">None</label>'
        +'<span style="border-left:1px solid #ccc;margin:0 10px 0 2px"></span>';
      if(_apGrps0.length>0){
        _gh+='<span style="font-size:11px;color:#888;margin-right:3px">AP:</span>';
        _apGrps0.forEach(function(g){
          var c=_recovGrpChecked.has(g)?' checked':'';
          _gh+='<label style="font-size:11px;margin-right:7px;cursor:pointer;white-space:nowrap">'
            +'<input type="checkbox"'+c+' data-grp="'+g+'" onchange="IC._recovGrpChk(this)" style="margin-right:2px">'+g+'</label>';
        });
        if(_crGrps0.length>0)_gh+='<span style="margin-right:8px"></span>';
      }
      if(_crGrps0.length>0){
        _gh+='<span style="font-size:11px;color:#888;margin-right:3px">CR:</span>';
        _crGrps0.forEach(function(g){
          var c=_recovGrpChecked.has(g)?' checked':'';
          _gh+='<label style="font-size:11px;margin-right:7px;cursor:pointer;white-space:nowrap">'
            +'<input type="checkbox"'+c+' data-grp="'+g+'" onchange="IC._recovGrpChk(this)" style="margin-right:2px">'+g+'</label>';
        });
      }
      _gfDiv.innerHTML=_gh;
      _gfDiv.style.display='';
    }else{_gfDiv.style.display='none';}
  }
  /* Collect wafer keys for selected rows */
  var selKeys=[];
  sR.forEach(function(i){
    var row=DATA.rows[i];if(!row)return;
    selKeys.push(row.lot+'|'+row.wafer);
  });
  var useAll=(selKeys.length===DATA.rows.length);
  var keys=useAll?['all']:selKeys;
  /* Count total IB dies for current selection + checked FBs */
  var totalDie=0;
  sR.forEach(function(i){
    var row=DATA.rows[i];if(!row)return;
    var ibFb=(row.ibToFb||{})[ib]||{};
    if(_fbChecked.size>0){
      _fbChecked.forEach(function(fb){totalDie+=(ibFb[fb]||0);});
    }else{
      totalDie+=(row.binCounts[ib]||0);
    }
  });
  /* Merge LOGTRACKER pareto + AP/CR tracked counts */
  var merged={};  /* test → {total, byGroup:{...}} */
  var apTrackedDie=0,crTrackedDie=0,slceTrackedDie=0;
  if(grps.length>0){
    var _ibFbSet=new Set(_fbModalFbKeys);
    keys.forEach(function(wk){
      var wData=RECOV_DATA[wk];if(!wData)return;
      Object.keys(wData).forEach(function(fb){
        if(!_ibFbSet.has(fb))return; /* restrict to current IB */
        if(_recovGrpChecked.size===0||(_fbChecked.size>0&&!_fbChecked.has(fb)))return;
        var rows=wData[fb];if(!rows)return;
        rows.forEach(function(r){
          if(!merged[r.test])merged[r.test]={total:0,byGroup:{}};
          merged[r.test].total+=r.total;
          Object.keys(r.byGroup).forEach(function(g){
            merged[r.test].byGroup[g]=(merged[r.test].byGroup[g]||0)+r.byGroup[g];
          });
        });
      });
    });
    keys.forEach(function(wk){
      var wt=RECOV_TRACKED[wk];if(!wt)return;
      Object.keys(wt).forEach(function(fb){
        if(!_ibFbSet.has(fb))return; /* restrict to current IB */
        if(_recovGrpChecked.size===0||(_fbChecked.size>0&&!_fbChecked.has(fb)))return;
        var e=wt[fb];
        if(e&&typeof e==='object'){apTrackedDie+=(e.ap||0);crTrackedDie+=(e.cr||0);slceTrackedDie+=(e.slce||0);}
        else if(typeof e==='number'){apTrackedDie+=e;crTrackedDie+=e;slceTrackedDie+=e;}
      });
    });
  }
  var trackedDie=Math.max(apTrackedDie,crTrackedDie,slceTrackedDie);
  /* Coverage warning — suppress when Bin Description provides 100% coverage */
  var _warnEl=document.getElementById('recov-coverage-warn');
  if(_warnEl){
    if(!hasBD&&totalDie>0&&trackedDie<totalDie){
      var _cov=trackedDie/totalDie*100;
      var _covPct=_cov.toFixed(0)+'%';
      var _warnBg,_warnFg,_warnMsg;
      if(_cov<20){
        _warnBg='#fef3cd';_warnFg='#856404';
        _warnMsg='&#9888; Low coverage: only '+_covPct+' of IB'+ib+' dies ('+trackedDie.toLocaleString()+' of '+totalDie.toLocaleString()+') have AP/CR/SLCE tracker data. The AP/CR/SLCE LOGTRACKER only records dies that went through the recovery screening flow. Dies that failed before or via a different test path are not captured here — these results do NOT represent all IB '+ib+' fails.';
      }else if(_cov<70){
        _warnBg='#fff3e0';_warnFg='#e65100';
        _warnMsg='&#9432; Partial coverage: '+_covPct+' of IB'+ib+' dies ('+trackedDie.toLocaleString()+' of '+totalDie.toLocaleString()+') have AP/CR/SLCE tracker data. Dies that failed via a path not in the recovery screening flow are excluded.';
      }else{
        _warnBg='#e8f5e9';_warnFg='#2e7d32';
        _warnMsg='&#10003; Good coverage: '+_covPct+' of IB'+ib+' dies have AP/CR/SLCE tracker data.';
      }
      _warnEl.style.display='';
      _warnEl.style.background=_warnBg;
      _warnEl.style.color=_warnFg;
      _warnEl.innerHTML=_warnMsg;
    }else{
      _warnEl.style.display='none';
      _warnEl.innerHTML='';
    }
  }
  /* Sort entries; split by tracker type */
  var _isAp=function(g){return /^AP/i.test(g);};
  var _isCr=function(g){return /^CR/i.test(g);};
  var _isSlce=function(g){return /^SLCE/i.test(g);};
  var apGrps=grps.filter(_isAp);
  var crGrps=grps.filter(_isCr);
  var slceGrps=grps.filter(_isSlce);
  var entries=Object.keys(merged).sort(function(a,b){return merged[b].total-merged[a].total;});
  var apEntries=entries.filter(function(t){return Object.keys(merged[t].byGroup).some(_isAp);});
  var crEntries=entries.filter(function(t){return Object.keys(merged[t].byGroup).some(_isCr);});
  var slceEntries=entries.filter(function(t){return Object.keys(merged[t].byGroup).some(_isSlce);});
  var otherEntries=entries.filter(function(t){
    return !Object.keys(merged[t].byGroup).some(_isAp)&&!Object.keys(merged[t].byGroup).some(_isCr)&&!Object.keys(merged[t].byGroup).some(_isSlce);
  });
  var cntEl=document.getElementById('recov-die-count');
  if(cntEl){
    var _parts=[];
    if(hasBD) _parts.push('BD: 100%');
    if(apEntries.length&&apTrackedDie>0) _parts.push('AP: '+apTrackedDie.toLocaleString()+'/'+totalDie.toLocaleString());
    if(crEntries.length&&crTrackedDie>0) _parts.push('CR: '+crTrackedDie.toLocaleString()+'/'+totalDie.toLocaleString());
    if(slceEntries.length&&slceTrackedDie>0) _parts.push('SLCE: '+slceTrackedDie.toLocaleString()+'/'+totalDie.toLocaleString());
    if(!_parts.length) _parts.push(trackedDie.toLocaleString()+'/'+totalDie.toLocaleString());
    var _nTests=hasBD?(function(){var m={};keys.forEach(function(wk){var d=BINDESC_DATA&&BINDESC_DATA[wk];if(!d)return;Object.keys(d).forEach(function(fb){if(_fbChecked.size>0&&!_fbChecked.has(fb))return;(d[fb]||[]).forEach(function(r){m[r.test]=1;});});});return Object.keys(m).length;}()):entries.length;
    cntEl.textContent=_nTests+' test'+(_nTests===1?'':'s')+' \u2014 '+_parts.join(' | ')+' die'
      +(selKeys.length<DATA.rows.length?' ('+selKeys.length+'/'+DATA.rows.length+' wafers)':' (all wafers)');
  }
  /* ── build one table per tracker type ─── */
  function _buildSection(secEntries,secGrps,secTracked,secLabel,isHardFail){
    /* Filter columns to currently checked groups; filter rows to those with any visible count */
    var visGrps=(_recovGrpChecked.size===0)?[]:secGrps.filter(function(g){return _recovGrpChecked.has(g);});
    if(!secEntries.length||!visGrps.length)return'';
    secEntries=secEntries.filter(function(t){return visGrps.some(function(g){return (merged[t].byGroup[g]||0)>0;});});
    if(!secEntries.length)return'';
    /* Hard-fail bins: denominator = total IB die count (per-die counting in Python).
       Normal bins: denominator = dies with tracker data for this section. */
    var den=isHardFail?totalDie:(secTracked>0?secTracked:totalDie);
    var trkTxt=isHardFail
      ?totalDie.toLocaleString()+' total IB'+ib+' die'
      :(secTracked<totalDie
        ?secTracked.toLocaleString()+' of '+totalDie.toLocaleString()+' die tracked'
        :secTracked.toLocaleString()+' die');
    var footnoteText=isHardFail
      ?'\u00b9 unique dies with this test \u00f7 total IB'+ib+' die count. A die may appear in multiple rows if different AP/CR groups recorded different first-failing tests.'
      :'\u00b9 tracker occurrences \u00f7 dies with '+esc(secLabel.split(' ')[0])+' LOGTRACKER data. May exceed 100% if multiple groups on one die fail at different tests.';
    /* Group entries by Scan / Array / Func / Other */
    var _grpOrder=['Scan','Array','Func','Other'];
    var _catToGrp={
      'Scan Stuckat':'Scan','Scan Vmin':'Scan','Scan Defect':'Scan',
      'Array Vmin':'Array',
      'Func Vmin':'Func',
      'Vmin':'Other','Other':'Other','':'Other'
    };
    var _byGrp={};
    secEntries.forEach(function(test){
      var lbl=_recovCategory(test).label;
      var gn=_catToGrp[lbl]||'Other';
      if(!_byGrp[gn])_byGrp[gn]=[];
      _byGrp[gn].push(test);
    });
    var colCount=visGrps.length+4; /* Type | Test | GP0..n | Total | % */
    var s='<div style="margin:4px 0 2px;padding:3px 7px;background:#f0f4f8;border-left:3px solid #4a90d9;font-size:11px;font-weight:bold;color:#2c3e50">'
      +esc(secLabel)+' \u2014 '+trkTxt+'</div>';
    s+='<table class="fb-tbl" style="width:100%;margin-bottom:10px">';
    s+='<thead><tr><th>Type</th><th>First Failing Test</th>';
    visGrps.forEach(function(g){s+='<th class="num">'+esc(g)+'</th>';});
    s+='<th class="num">Total</th><th class="num">%\u00b9</th></tr>';
    s+='<tr><td colspan="'+colCount+'" style="font-size:10px;color:#888;padding:1px 4px 3px">';
    s+=footnoteText+'</td></tr></thead><tbody>';
    _grpOrder.forEach(function(gn){
      var tests=_byGrp[gn];
      if(!tests||!tests.length)return;
      /* Category group header row */
      s+='<tr><td colspan="'+colCount+'" style="background:#2c3e50;color:#ecf0f1;font-size:10px;font-weight:bold;padding:2px 7px;letter-spacing:0.5px">'+esc(gn)+'</td></tr>';
      tests.forEach(function(test){
        var m=merged[test];
        var rowTotal=visGrps.reduce(function(acc,g){return acc+(m.byGroup[g]||0);},0);
        var pct=den>0?(rowTotal/den*100).toFixed(1):'\u2014';
        var cat=_recovCategory(test);
        var parts=test.split('::');
        var dTest=parts.length>=2
          ?'<span style="color:#888;font-size:10px">'+esc(parts[0])+'::\u00a0</span>'+esc(parts.slice(1).join('::'))
          :esc(test);
        s+='<tr>'
          +'<td style="white-space:nowrap"><span style="background:'+cat.bg+';color:'+cat.fg+';font-size:10px;padding:1px 5px;border-radius:3px;white-space:nowrap">'+esc(cat.label)+'</span></td>'
          +'<td style="font-size:11px;word-break:break-all"><span title="'+esc(test)+'">'+dTest+'</span></td>';
        visGrps.forEach(function(g){s+='<td class="num">'+(m.byGroup[g]||0)+'</td>';});
        s+='<td class="num"><b>'+rowTotal+'</b></td>'
          +'<td class="num">'+pct+'%</td></tr>';
      });
    });
    s+='</tbody></table>';
    return s;
  }
  var wrap=document.getElementById('recov-tbl-content');
  if(!wrap)return;
  var html='';
  /* ── 1. Bin Description section (top; 100% coverage for fail bins) ── */
  if(hasBD){
    var bdMerged={};
    keys.forEach(function(wk){
      var wData=BINDESC_DATA&&BINDESC_DATA[wk];if(!wData)return;
      Object.keys(wData).forEach(function(fb){
        if(_fbChecked.size>0&&!_fbChecked.has(fb))return;
        var rows=wData[fb];if(!rows)return;
        rows.forEach(function(r){bdMerged[r.test]=(bdMerged[r.test]||0)+r.total;});
      });
    });
    if(Object.keys(bdMerged).length) html+=_buildBDSection(bdMerged,totalDie,ib);
  }
  /* ── 2. AP/CR/SLCE LOGTRACKER section (primary for iBin 3/4/5; detail for others) ── */
  if(entries.length>0){
    if(hasBD){
      html+='<div style="margin:10px 0 4px;padding:3px 7px;background:#d6eaf8;border-left:3px solid #7fb3d3;font-size:11px;font-weight:bold;color:#1a5276">AP/CR/SLCE LOGTRACKER Detail</div>';
    }
    if(apEntries.length>0) html+=_buildSection(apEntries,apGrps,apTrackedDie,'ATOM Tracker (AP groups)',isHF);
    if(crEntries.length>0) html+=_buildSection(crEntries,crGrps,crTrackedDie,'Core Tracker (CR groups)',isHF);
    if(slceEntries.length>0) html+=_buildSection(slceEntries,slceGrps,slceTrackedDie,'Slice Tracker (SLCE groups)',isHF);
    if(otherEntries.length>0) html+=_buildSection(otherEntries,grps,trackedDie,'Tracker',isHF);
  }
  if(!html){wrap.innerHTML='<p style="color:#888;text-align:center;padding:10px">No tracker data for current selection.</p>';return;}
  wrap.innerHTML=html;
}
/* ───────────────────────────────────────────────────────────────────────── */
function _upmColor(pct){
  if(pct===null||pct===undefined)return'#bbb';
  var t=Math.max(0,Math.min(1,pct));
  // vivid: red(low)→orange→yellow→lime→blue(high)
  var stops=[[220,0,0],[255,120,0],[240,215,0],[0,210,60],[0,50,220]];
  var seg=t*4,i=Math.floor(seg),f=seg-i;
  if(i>=4){var s=stops[4];return'rgb('+s[0]+','+s[1]+','+s[2]+')';}  
  var c1=stops[i],c2=stops[i+1];
  return'rgb('+Math.round(c1[0]+(c2[0]-c1[0])*f)+','+Math.round(c1[1]+(c2[1]-c1[1])*f)+','+Math.round(c1[2]+(c2[2]-c1[2])*f)+')';
}
function _renderUpmMaps(){
  var body=document.getElementById('upm-body');
  if(!body)return;
  var uCols=DATA.upmCols||[];
  if(!uCols.length){body.innerHTML='<div style="color:#888">No UPM data available.</div>';return;}
  var upmIdx=_upmMetricIdx;
  var colMeta=uCols[upmIdx]||{};
  var colLabel=colMeta.label||'';
  // Collect values only from "active" dies (IB+FB match) for color scale and distribution
  var allVals=[],filteredVals=[];
  sR.forEach(function(ri){
    var row=DATA.rows[ri];if(!row||!row.dies)return;
    row.dies.forEach(function(d){
      var v=d[(DATA.upmStart||5)+upmIdx];if(v===null||v===undefined)return;
      allVals.push(v);
      var x=d[0],y=d[1],ib=d[2],fb=d[3];
      var ibMatch=(_fbModalIb!==null)?(String(ib)===String(_fbModalIb)):(sB.size===AB.length||sB.has(String(ib)));
      var fbMatch;
      if(_recovOpen&&ibMatch&&RECOV_DIE_GRPS){fbMatch=(_recovGrpChecked.size>0)&&_dieGrpActive(row.lot+'|'+row.wafer,x,y);}
      else{fbMatch=(!ibMatch)||(fb===null)||(String(_fbModalIb)!==String(ib))||_fbChecked.has(String(fb));}
      if(ibMatch&&fbMatch)filteredVals.push(v);
    });
  });
  // Percentile-based color scale (P2–P98): dynamic on both ends, outlier-resistant
  var _sv=allVals.slice().sort(function(a,b){return a-b;});
  var lo=_sv.length?_sv[Math.max(0,Math.floor(_sv.length*0.02))]:0;
  var hi=_sv.length?_sv[Math.min(_sv.length-1,Math.floor(_sv.length*0.98))]:100;
  // Auto-detect unit: use raw max to check for MHz (not clamped hi)
  var isMHz=(_sv.length?_sv[_sv.length-1]:hi)>200;
  var unit=isMHz?' MHz':'%';
  var rng=(hi-lo)||1;
  /* store in module vars so _upmRenderTile can access them */
  _upmLo=lo;_upmHi=hi;_upmRng=rng;_upmIsMHz=isMHz;
  _upmDivisor=(colMeta.divisor&&colMeta.divisor>0)?colMeta.divisor:0;
  _upmHasDieLoc=DATA.hasReticle&&DATA.retSiteNum&&Object.keys(DATA.retSiteNum).length>0;
  var fmtVal=function(v){return isMHz?Math.round(v)+unit:v.toFixed(2)+unit;};
  var fmtBoth=function(v){
    if(isMHz){var pct=(colMeta.divisor&&colMeta.divisor>0)?v/colMeta.divisor*100:NaN;
      return Math.round(v)+'MHz'+(isNaN(pct)?'':' ('+pct.toFixed(1)+'%)');}
    var raw=(colMeta.divisor&&colMeta.divisor>0)?Math.round(v*colMeta.divisor/100):NaN;
    return v.toFixed(2)+'%'+(isNaN(raw)?'':' ('+raw+'MHz)');
  };
  var titleHtml='<div style="font-size:12px;font-weight:bold;color:#1a3a6a;margin-bottom:6px">UPM @'+colLabel
    +' &mdash; range: '+fmtVal(lo)+' to '+fmtVal(hi)
    +(_bhHwSel.size>0?' &nbsp;<span style="background:#e67e22;color:#fff;font-size:10px;padding:1px 6px;border-radius:3px">HW filtered</span>':'')
    +'</div>';
  var mapsHtml='<div class="upm-maps">';
  /* die-loc filter bar */
  var hasDieLoc=DATA.hasReticle&&DATA.retSiteNum&&Object.keys(DATA.retSiteNum).length>0;
  var _dlBarEl=document.getElementById('upm-dieLoc-bar');
  if(_dlBarEl){
    if(hasDieLoc){
      var _dlNums=[];Object.values(DATA.retSiteNum).forEach(function(v){if(_dlNums.indexOf(+v)<0)_dlNums.push(+v);});
      _dlNums.sort(function(a,b){return a-b;});
      var _dlH='<span style="color:#555;margin-right:4px">Die Loc:</span>'
        +'<button class="wm-tbtn'+(_upmDieLoc===null?' on':'')+'" onclick="IC._upmDieLocAll()" style="font-size:10px;padding:1px 5px;margin-right:3px">All</button>';
      _dlNums.forEach(function(n){
        var _on=_upmDieLoc===null||_upmDieLoc.has(n);
        _dlH+='<button class="wm-tbtn'+(_on?' on':'')+'" onclick="IC._upmDieLocToggle('+n+')" style="font-size:10px;padding:1px 5px;margin-right:2px">'+n+'</button>';
      });
      _dlBarEl.innerHTML=_dlH;_dlBarEl.style.display='';
    }else{_dlBarEl.style.display='none';}
  }
  /* Build placeholder tiles — actual rendering deferred to IntersectionObserver */
  sR.forEach(function(ri){
    var row=DATA.rows[ri];
    if(!row||!row.dies||!row.dies.length)return;
    var _pDies=row.dies,_pxs=[],_pys=[];
    _pDies.forEach(function(d){if(d[0]!==null&&d[0]!==undefined){_pxs.push(d[0]);_pys.push(d[1]);}});
    if(!_pxs.length)return;
    var _pxMin=Math.min.apply(null,_pxs),_pxMax=Math.max.apply(null,_pxs);
    var _pyMin=Math.min.apply(null,_pys),_pyMax=Math.max.apply(null,_pys);
    var _pPad=2,_pFW=Math.round(150*_upmZoom);
    var _pCs=Math.max(1,(_pFW-_pPad*2)/(_pxMax-_pxMin+1));
    var _pXSpan=_pxMax-_pxMin,_pYSpan=_pyMax-_pyMin;
    var _pCsy=(_pXSpan>0&&_pYSpan>0)?(_pCs*_pXSpan/_pYSpan):_pCs;
    var _pW=_pFW,_pH=Math.round((_pyMax-_pyMin+1)*_pCsy+_pPad*2);
    var lbl=(row.lot||'')+' W'+(row.wafer||'');
    mapsHtml+='<div class="upm-ww upm-tile-ph" data-ri="'+ri+'">'+'<div class="upm-wlbl">'+lbl+'</div>'+'<div class="upm-tile-content" style="width:'+_pW+'px;height:'+_pH+'px;background:#e8ecf0;border-radius:3px;display:inline-block"></div>'+'</div>';
  });
  mapsHtml+='</div>';
  var hwNote=_bhHwSel.size>0?(' &nbsp;&bull;&nbsp; HW: '+_bhHwSel.size+' selected'):'';
  var lgHtml='<div class="upm-lgd"><span style="color:#dc0000">'+fmtVal(lo)+'</span><div class="upm-lgd-bar"></div><span style="color:#0032dc">'+fmtVal(hi)+'</span></div>';
  lgHtml+='<div style="font-size:11px;color:#7f8c8d;margin-top:3px">&#9632; grey=no UPM &nbsp;&bull;&nbsp; dimmed=other IB &nbsp;&bull;&nbsp; filter: '+((_fbModalIb!==null)?'IB '+_fbModalIb:'histogram selection')+hwNote+' &nbsp;&bull;&nbsp; hover die for value</div>';
  /* ── Mini distribution histogram ─────────────────────────────────── */
  var distHtml='';
  if(filteredVals.length>1){
    var nBins=20;
    var dLo=Math.min.apply(null,filteredVals),dHi=Math.max.apply(null,filteredVals);
    var dRng=(dHi-dLo)||1;
    var bins=[];for(var _b=0;_b<nBins;_b++)bins.push(0);
    filteredVals.forEach(function(v){
      var bi=Math.min(nBins-1,Math.floor((v-dLo)/dRng*nBins));
      bins[bi]++;
    });
    var maxBin=Math.max.apply(null,bins)||1;
    var dW=480,dH=80,pl=4,pr=4,pt=6,pb=18;
    var cW=dW-pl-pr,cH=dH-pt-pb;
    var bw=cW/nBins,p2=[];
    p2.push('<rect width="'+dW+'" height="'+dH+'" fill="#f0f4fa" rx="4"/>');
    for(var _bi=0;_bi<nBins;_bi++){
      var bh2=bins[_bi]/maxBin*cH;
      var bx=pl+_bi*bw,by=pt+cH-bh2;
      var t2=_bi/(nBins-1);
      p2.push('<rect x="'+bx.toFixed(1)+'" y="'+by.toFixed(1)+'" width="'+(bw-1).toFixed(1)+'" height="'+bh2.toFixed(1)+'" fill="'+_upmColor(t2)+'" rx="1"><title>'+fmtVal(dLo+_bi/nBins*dRng)+' – '+fmtVal(dLo+(_bi+1)/nBins*dRng)+': '+bins[_bi]+' die</title></rect>');
    }
    p2.push('<text x="'+pl+'" y="'+(dH-4)+'" font-family="Arial" font-size="10" fill="#888">'+fmtVal(dLo)+'</text>');
    p2.push('<text x="'+(dW-pr)+'" y="'+(dH-4)+'" font-family="Arial" font-size="10" fill="#888" text-anchor="end">'+fmtVal(dHi)+'</text>');
    p2.push('<text x="'+(dW/2)+'" y="'+(dH-4)+'" font-family="Arial" font-size="10" fill="#555" text-anchor="middle">n='+filteredVals.length+' die &nbsp; med='+fmtVal(filteredVals.slice().sort(function(a,b){return a-b;})[Math.floor(filteredVals.length/2)])+'</text>');
    distHtml='<div style="margin-top:10px"><div style="font-size:11px;font-weight:bold;color:#2c3e50;margin-bottom:3px">UPM Distribution (filtered dies)</div>'
      +'<svg width="'+dW+'" height="'+dH+'" style="display:block;max-width:100%">'+p2.join('')+'</svg></div>';
  }
  body.innerHTML=titleHtml+mapsHtml+lgHtml+distHtml;
  /* lazy tile rendering via IntersectionObserver */
  _upmRenderedRis=new Set();
  if(_upmObserver){_upmObserver.disconnect();_upmObserver=null;}
  _upmObserver=new IntersectionObserver(function(entries){
    entries.forEach(function(entry){
      if(entry.isIntersecting){
        var _phRi=parseInt(entry.target.dataset.ri,10);
        var _phEl=entry.target.querySelector('.upm-tile-content');
        if(_phEl)_upmRenderTile(_phRi,_phEl);
      }
    });
  },{root:body,rootMargin:'300px 0px'});
  body.querySelectorAll('.upm-tile-ph[data-ri]').forEach(function(el){_upmObserver.observe(el);});
  _setupUpmBodyHover();
}
function _upmRenderTile(ri,container){
  var row=DATA.rows[ri];
  if(!row||!row.dies||!row.dies.length)return;
  var dies=row.dies,upmIdx=_upmMetricIdx,uStart=DATA.upmStart||5;
  var xs=[],ys=[];
  dies.forEach(function(d){if(d[0]!==null&&d[0]!==undefined){xs.push(d[0]);ys.push(d[1]);}});
  if(!xs.length)return;
  var xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs);
  var yMin=Math.min.apply(null,ys),yMax=Math.max.apply(null,ys);
  var pad=2,FIXED_W=Math.round(150*_upmZoom);
  var cs=Math.max(1,(FIXED_W-pad*2)/(xMax-xMin+1));
  var xSpan=xMax-xMin,ySpan=yMax-yMin;
  var csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;
  var W=FIXED_W,H=Math.round((yMax-yMin+1)*csy+pad*2);
  var lo=_upmLo,rng=_upmRng,isMHz=_upmIsMHz,hasDieLoc=_upmHasDieLoc;
  var wKey=row.lot+'|'+row.wafer;
  function _fmtB(v){if(isMHz){var pct=_upmDivisor>0?v/_upmDivisor*100:NaN;return Math.round(v)+'MHz'+(isNaN(pct)?'':' ('+pct.toFixed(1)+'%)');} var raw=_upmDivisor>0?Math.round(v*_upmDivisor/100):NaN;return v.toFixed(2)+'%'+(isNaN(raw)?'':' ('+raw+'MHz)');}
  if(_upmCanvasMode){
    var cv=document.createElement('canvas');
    cv.width=W;cv.height=H;cv.style.display='block';
    var ctx=cv.getContext('2d');
    dies.forEach(function(d){
      var x=d[0],y=d[1],ib=d[2],fb=d[3],hw=d[4],uv=d[uStart+upmIdx];
      if(x===null||x===undefined)return;
      var t=(uv!==null&&uv!==undefined)?Math.max(0,Math.min(1,(uv-lo)/rng)):null;
      var ibMatch=(_fbModalIb!==null)?(String(ib)===String(_fbModalIb)):(sB.size===AB.length||sB.has(String(ib)));
      var fbMatch;
      if(_recovOpen&&ibMatch&&RECOV_DIE_GRPS){fbMatch=(_recovGrpChecked.size>0)&&_dieGrpActive(wKey,x,y);}
      else{fbMatch=(!ibMatch)||(fb===null)||(String(_fbModalIb)!==String(ib))||_fbChecked.has(String(fb));}
      var hwMatch=(_bhHwSel.size===0)||(hw===null)||_bhHwSel.has(String(hw));
      var active=ibMatch&&fbMatch&&hwMatch;
      if(active&&_sdtSecOpen&&DATA.hasSdtDie){var _ss=DATA.sdtDieStart||7;var _si2=d[_ss],_sf2=d[_ss+1];var _sk2=_si2===null||_si2===undefined?null:String(_si2)+'|'+(_sf2===null||_sf2===undefined?'':String(_sf2));if(_sk2===null||!_sdtChecked.has(_sk2))active=false;}
      if(active&&hasDieLoc&&_upmDieLoc!==null&&DATA.retMap){var _ru=DATA.retMap[x+','+y];if(_ru){var _du=DATA.retSiteNum[_ru[0]+','+_ru[1]];if(_du!==undefined&&!_upmDieLoc.has(+_du))active=false;}else{active=false;}}
      ctx.globalAlpha=active?1:0.12;
      ctx.fillStyle=_upmColor(t);
      ctx.fillRect(pad+(x-xMin)*cs,pad+(yMax-y)*csy,cs*0.92,csy*0.92);
    });
    ctx.globalAlpha=1;
    if(DATA.hasReticle&&DATA.retShots&&DATA.retShots.length){
      DATA.retShots.forEach(function(shot){ctx.strokeStyle='#4a90d9';ctx.lineWidth=0.8;ctx.globalAlpha=0.6;ctx.strokeRect(pad+(shot[0]-xMin)*cs,pad+(yMax-shot[3])*csy,(shot[2]-shot[0]+1)*cs,(shot[3]-shot[1]+1)*csy);ctx.globalAlpha=1;});
    }
    var xCtr=(xMin+xMax)/2,yCtr=(yMin+yMax)/2;
    ctx.strokeStyle='#bdc3c7';ctx.lineWidth=1;
    ctx.beginPath();ctx.ellipse(pad+(xCtr-xMin)*cs+cs*0.46,pad+(yMax-yCtr)*csy+csy*0.46,(xMax-xMin)/2*cs+cs*0.5,(yMax-yMin)/2*csy+csy*0.5,0,0,2*Math.PI);ctx.stroke();
    if(hasDieLoc&&DATA.retSiteNum&&cs>=4){var _dlFs3=Math.max(4,Math.min(7,Math.round(cs*0.42)));ctx.font='bold '+_dlFs3+'px Arial';ctx.textAlign='right';ctx.textBaseline='top';ctx.fillStyle='#000';ctx.globalAlpha=1;dies.forEach(function(d){var x=d[0],y=d[1];if(x===null||x===undefined)return;var _inf=DATA.retMap&&DATA.retMap[x+','+y];if(!_inf)return;var _dtag=String(DATA.retSiteNum[_inf[0]+','+_inf[1]]||'');if(!_dtag)return;ctx.fillText(_dtag,pad+(x-xMin)*cs+cs-0.5,pad+(yMax-y)*csy+0.5);});}
    cv._upmDl={};cv._xMnU=xMin;cv._yMxU=yMax;cv._csU=cs;cv._csyU=csy;cv._padU=pad;cv._fmtB=_fmtB;
    dies.forEach(function(d){var x=d[0],y=d[1];if(x===null||x===undefined)return;cv._upmDl[x+','+y]={ib:d[2],fb:d[3],uv:d[uStart+upmIdx],x:x,y:y};});
    if(!cv._upmHv){cv._upmHv=true;
      cv.addEventListener('mousemove',function(e){
        var r2=cv.getBoundingClientRect(),sx=cv.width/r2.width,sy=cv.height/r2.height;
        var dx2=Math.round(cv._xMnU+(e.clientX-r2.left)*sx/cv._csU-cv._padU/cv._csU);
        var dy2=Math.round(cv._yMxU-(e.clientY-r2.top)*sy/cv._csyU+cv._padU/cv._csyU);
        var dd=cv._upmDl[dx2+','+dy2];
        if(!dd){_upmTipHide();return;}
        var tipTxt=(dd.uv!==null&&dd.uv!==undefined)?cv._fmtB(Number(dd.uv)):'no UPM';
        _upmTip(e,'UPM '+tipTxt+'|IB'+(dd.ib!==null?dd.ib:'?')+(dd.fb!==null?' FB'+dd.fb:'')+'  ('+dd.x+','+dd.y+')');
      });
      cv.addEventListener('mouseleave',function(){_upmTipHide();});
    }
    container.innerHTML='';container.appendChild(cv);
  } else {
    /* SVG path — duplicate rect bug fixed */
    var rects=[];
    dies.forEach(function(d){
      var x=d[0],y=d[1],ib=d[2],fb=d[3],hw=d[4],uv=d[uStart+upmIdx];
      if(x===null||x===undefined)return;
      var px=(pad+(x-xMin)*cs).toFixed(2),py=(pad+(yMax-y)*csy).toFixed(2);
      var t=(uv!==null&&uv!==undefined)?Math.max(0,Math.min(1,(uv-lo)/rng)):null;
      var fill=_upmColor(t);
      var ibMatch=(_fbModalIb!==null)?(String(ib)===String(_fbModalIb)):(sB.size===AB.length||sB.has(String(ib)));
      var fbMatch;
      if(_recovOpen&&ibMatch&&RECOV_DIE_GRPS){fbMatch=(_recovGrpChecked.size>0)&&_dieGrpActive(wKey,x,y);}
      else{fbMatch=(!ibMatch)||(fb===null)||(String(_fbModalIb)!==String(ib))||_fbChecked.has(String(fb));}
      var hwMatch=(_bhHwSel.size===0)||(hw===null)||_bhHwSel.has(String(hw));
      var opacity=(ibMatch&&fbMatch&&hwMatch)?'1':'0.12';
      if(opacity==='1'&&_sdtSecOpen&&DATA.hasSdtDie){var _sdtSt=DATA.sdtDieStart||7;var _si=d[_sdtSt],_sf=d[_sdtSt+1];var _sdtK=_si===null||_si===undefined?null:String(_si)+'|'+(_sf===null||_sf===undefined?'':String(_sf));if(_sdtK===null||!_sdtChecked.has(_sdtK))opacity='0.12';}
      if(opacity!=='0.12'&&hasDieLoc&&_upmDieLoc!==null&&DATA.retMap){var _rmU=DATA.retMap[x+','+y];if(_rmU){var _dlU=DATA.retSiteNum[_rmU[0]+','+_rmU[1]];if(_dlU!==undefined&&!_upmDieLoc.has(+_dlU))opacity='0.07';}else{opacity='0.07';}}
      var dlTipInfo='';
      if(hasDieLoc&&DATA.retMap){var _rmTip=DATA.retMap[x+','+y];if(_rmTip){var _dlTip=DATA.retSiteNum[_rmTip[0]+','+_rmTip[1]];if(_dlTip!==undefined)dlTipInfo=' loc'+_dlTip;}}
      var tipStr='UPM '+(uv!==null&&uv!==undefined?_fmtB(Number(uv)):'no UPM')+'|IB'+ib+(fb!==null?' FB'+fb:'')+(hw!==null?' HW'+hw:'')+dlTipInfo+'  ('+x+','+y+')';
      rects.push('<rect x="'+px+'" y="'+py+'" width="'+(cs*0.92).toFixed(2)+'" height="'+(csy*0.92).toFixed(2)+'" fill="'+fill+'" opacity="'+opacity+'" data-tip="'+tipStr+'"/>');
      if(hasDieLoc&&DATA.retMap&&cs>=4){var _rmDl=DATA.retMap[x+','+y];if(_rmDl){var _dlN=DATA.retSiteNum[_rmDl[0]+','+_rmDl[1]];if(_dlN!==undefined){var _dlFs=Math.max(4,Math.min(9,Math.round(cs*0.42)));var _dlTc=_wmContrast(fill);rects.push('<text x="'+(parseFloat(px)+cs*0.45).toFixed(2)+'" y="'+(parseFloat(py)+csy*0.5+_dlFs*0.36).toFixed(2)+'" text-anchor="middle" font-size="'+_dlFs+'" fill="'+_dlTc+'" stroke="'+(_dlTc==='#fff'?'#0a0f1a':'#f5faff')+'" stroke-width="0.6" paint-order="stroke" font-weight="bold" pointer-events="none" opacity="'+opacity+'">'+_dlN+'</text>');}}}
    });
    var _retO2='';
    if(DATA.hasReticle&&DATA.retShots&&DATA.retShots.length){DATA.retShots.forEach(function(shot){var _sx=(pad+(shot[0]-xMin)*cs).toFixed(2),_sy=(pad+(yMax-shot[3])*csy).toFixed(2),_sw=((shot[2]-shot[0]+1)*cs).toFixed(2),_sh=((shot[3]-shot[1]+1)*csy).toFixed(2);_retO2+='<rect x="'+_sx+'" y="'+_sy+'" width="'+_sw+'" height="'+_sh+'" fill="none" stroke="#4a90d9" stroke-width="0.8" opacity="0.6"/>';});}
    container.innerHTML='<svg width="'+W+'" height="'+H+'" style="display:block">'+rects.join('')+_retO2+'</svg>';
  }
  _upmRenderedRis.add(ri);
}
function _upmToggleMode(){
  _upmCanvasMode=!_upmCanvasMode;
  var btn=document.getElementById('upm-mode-btn');
  if(btn)btn.innerHTML=_upmCanvasMode?'&#128247; SVG mode':'&#9889; Fast mode';
  var body=document.getElementById('upm-body');if(!body)return;
  body.querySelectorAll('.upm-tile-ph[data-ri]').forEach(function(el){
    var ri=parseInt(el.dataset.ri,10);
    if(_upmRenderedRis.has(ri)){var tc=el.querySelector('.upm-tile-content');if(tc)_upmRenderTile(ri,tc);}
  });
}
function _upmSetZoom(z){
  _upmZoom=Math.max(0.5,Math.min(4,z));
  var lbl=document.getElementById('upm-zoom-lbl');if(lbl)lbl.textContent=Math.round(_upmZoom*100)+'%';
  _renderUpmMaps();
}
function _upmZoomIn(){_upmSetZoom(_upmZoom+0.5);}
function _upmZoomOut(){_upmSetZoom(_upmZoom-0.5);}
function _wmdZoomSet(z){
  _wmdZoom=Math.max(0.5,Math.min(4,z));
  var lbl=document.getElementById('wmd-zoom-lbl');if(lbl)lbl.textContent=Math.round(_wmdZoom*100)+'%';
  if(_wmdOpen&&_wmdRi>=0)_wmdRender(_wmdRi);
}
function _wmdZoomIn(){_wmdZoomSet(_wmdZoom+0.5);}
function _wmdZoomOut(){_wmdZoomSet(_wmdZoom-0.5);}
function _wmSetZoom(z){
  _wmZoom=Math.max(0.5,Math.min(4,z));
  var lbl=document.getElementById('wm-zoom-lbl');if(lbl)lbl.textContent=Math.round(_wmZoom*100)+'%';
  _wmRender();
}
function _wmZoomIn(){_wmSetZoom(_wmZoom+0.5);}
function _wmZoomOut(){_wmSetZoom(_wmZoom-0.5);}
function _upmTip(e,tip){
  var t=document.getElementById('upm-tooltip');
  if(!t){t=document.createElement('div');t.id='upm-tooltip';
    t.style.cssText='position:fixed;background:rgba(20,20,40,0.92);color:#fff;font-size:11px;padding:5px 9px;border-radius:4px;pointer-events:none;z-index:30000;box-shadow:0 2px 6px rgba(0,0,0,.4);line-height:1.6';
    document.body.appendChild(t);}
  t.innerHTML=tip.split('|').join('<br>');
  t.style.left=(e.clientX+14)+'px';t.style.top=(e.clientY-10)+'px';t.style.display='block';
}
function _upmTipHide(){
  var t=document.getElementById('upm-tooltip');if(t)t.style.display='none';
}
// Delegated mousemove on upm-body for tooltip (SVG innerHTML loses inline handlers)
function _setupUpmBodyHover(){
  var body=document.getElementById('upm-body');
  if(!body||body._upmHoverBound)return;
  body._upmHoverBound=true;
  body.addEventListener('mousemove',function(e){
    var el=e.target;
    if(el&&el.tagName==='rect'&&el.dataset&&el.dataset.tip){
      _upmTip(e,el.dataset.tip);
    } else if(el.tagName!=='CANVAS'){_upmTipHide();}
  });
  body.addEventListener('mouseleave',function(){_upmTipHide();});
}
function _setupUpmDrag(){
  var drag=document.getElementById('upm-drag');
  var box=document.getElementById('upm-box');
  if(!drag||!box)return;
  var dX=0,dY=0,dragging=false;
  drag.addEventListener('mousedown',function(e){
    dragging=true;
    var r=box.getBoundingClientRect();
    box.style.left=r.left+'px';box.style.top=r.top+'px';box.style.transform='none';
    dX=e.clientX-r.left;dY=e.clientY-r.top;e.preventDefault();
  });
  document.addEventListener('mousemove',function(e){
    if(!dragging)return;
    var nx=e.clientX-dX,ny=e.clientY-dY;
    var mw=window.innerWidth-box.offsetWidth,mh=window.innerHeight-box.offsetHeight;
    box.style.left=Math.max(0,Math.min(mw,nx))+'px';
    box.style.top=Math.max(0,Math.min(mh,ny))+'px';
  });
  document.addEventListener('mouseup',function(){dragging=false;});
}
document.addEventListener('keydown',function(e){if(e.key==='Escape'){if(_wmdOpen){_wmdClose();}else if(_wmOpen){closeWmModal();}else if(_dlcpOpen){closeDlcpModal();}else if(_upmOpen){closeUpmModal();}else if(_bhHwOpen){closeBhHwModal();}else{closeFbModal();}}});
window._upmTip=_upmTip;window._upmTipHide=_upmTipHide;
/* ---- Wafer Pattern Analysis modal ---- */
var _wmPal=['#c0392b','#922b21','#8e44ad','#2471a3','#0e6655','#784212','#1a5276','#6e2f8a','#cb4335','#117a65'];
function _wmIbColor(ib){
  if(ib===1)return'#27ae60';
  if(ib===2)return'#2980b9';
  if(ib===3||ib===4)return'#e67e22';
  if(ib===null||ib===undefined)return'#bdc3c7';
  return _wmPal[(ib-5)%_wmPal.length];
}
var _wmSelRows=null;
var _wmFailThresh=5;
var _wmBinChecked=null; /* null=all; Set of IB ints to include in scoring */
var _wmBadgePinned=new Set(); /* IB ints pinned via badge checkboxes (Show Only) */
var _wmActiveTab='impact';
var _wmRetChecked=null; /* Set of "rx,ry" strings for highlighted sites, or null */
var _wmCriteriaMissOnly=false; /* when true, show only wafers that miss a yield criteria */
var _wmCriteriaDisabled=new Set(); /* indices of yieldDefs to skip in criteria check */
var _wmSiteToShots=null; /* lazy cache: "rx,ry" -> Set of shot indices */
var _wmOpen=false;
var _wmCanvasMode=true,_wmObserver=null,_wmRenderedRis=new Set();
var _wmZoom=1;
var _wmdOpen=false,_wmdRi=-1;
var _wmdDX=0,_wmdDY=0,_wmdDragging=false;
var _wmdZoom=1;

function _wmVisRows(){
  var out=[];
  sR.forEach(function(ri){
    if(_wmSelRows!==null&&!_wmSelRows.has(ri))return;
    if(_wmCriteriaMissOnly&&!_wmGetCriteriaMissBins(DATA.rows[ri]).length)return;
    out.push(ri);
  });
  return out;
}
function _wmIsFail(ib){return ib===null||ib===undefined||ib>=_wmFailThresh;}
function _wmBinActive(ib){return _wmBinChecked===null||_wmBinChecked.has(ib);}
function _wmGetCriteriaMissBins(row){
  if(!row)return[];
  var bc=row.binCounts||{},total=row.total||1,out=[];
  (DATA.yieldDefs||[]).forEach(function(def,_di){
    if(_wmCriteriaDisabled.has(_di))return;
    if(!def.expected)return;
    var exp=parseFloat(def.expected);if(isNaN(exp))return;
    var cnt=def.bins_list.reduce(function(s,b){return s+(bc[b]||0);},0);
    var pct=total>0?cnt/total*100:0;
    var hasBin1=def.bins_list.indexOf('1')>=0;
    var fails=hasBin1?(pct<exp):(pct>exp);
    if(fails)def.bins_list.forEach(function(b){if(out.indexOf(b)<0)out.push(b);});
  });
  return out;
}
function _wmGetCriteriaMissInfo(row){
  if(!row)return[];
  var bc=row.binCounts||{},total=row.total||1,out=[];
  (DATA.yieldDefs||[]).forEach(function(def,_di){
    if(_wmCriteriaDisabled.has(_di))return;
    if(!def.expected)return;
    var exp=parseFloat(def.expected);if(isNaN(exp))return;
    var cnt=def.bins_list.reduce(function(s,b){return s+(bc[b]||0);},0);
    var pct=total>0?cnt/total*100:0;
    var hasBin1=def.bins_list.indexOf('1')>=0;
    var fails=hasBin1?(pct<exp):(pct>exp);
    if(fails)out.push('Bin '+def.bins+' ('+def.bucket+'): '+pct.toFixed(1)+'% vs '+exp+'% exp');
  });
  return out;
}
function _wmCritRebuildRows(){
  var defs=DATA.yieldDefs||[];
  var tbody=document.getElementById('wm-cc-tbody');
  if(!tbody)return;
  var rows=defs.map(function(def,i){
    var dis=_wmCriteriaDisabled.has(i);
    return '<tr style="border-bottom:1px solid #eee"><td style="padding:4px 8px"><input type="checkbox" id="wm-cc-'+i+'" '+(dis?'':'checked')+' onchange="IC._wmCritCfgToggle('+i+',this.checked)" style="cursor:pointer"></td>'
      +'<td style="padding:4px 8px;font-size:12px">'+(def.bucket||'\u2014')+'</td>'
      +'<td style="padding:4px 8px;font-size:11px;color:#555">IB'+(def.bins||def.bins_list.join('/'))+'</td>'
      +'<td style="padding:4px 8px;font-size:12px;font-weight:bold;color:#2471a3">'+(def.expected||'\u2014')+'%</td></tr>';
  }).join('');
  tbody.innerHTML=rows;
  _wmCritCfgUpdateCount();
}
function _wmCritLoadJson(file){
  if(!file)return;
  var reader=new FileReader();
  reader.onload=function(e){
    try{
      var obj=JSON.parse(e.target.result);
      var targets=obj.yield_targets||obj.yieldTargets||obj;
      if(!Array.isArray(targets))throw new Error('Expected array under yield_targets');
      var newDefs=targets.map(function(t){
        var bins=String(t.bin||t.bins||'');
        var bins_list=bins.split('/').map(function(b){return b.trim();}).filter(Boolean);
        return{bins:bins,bin:bins,bucket:t.fail_bucket||t.bucket||'',expected:String(t.yield||t.expected||''),bins_list:bins_list};
      });
      DATA.yieldDefs=newDefs;
      _wmCriteriaDisabled=new Set();
      _wmCritRebuildRows();
      _wmBuildCtrl();_wmRender();
      var inf=document.getElementById('wm-crit-json-info');
      if(inf){inf.style.color='#27ae60';inf.textContent='\u2713 Loaded '+newDefs.length+' criteria from '+file.name;}
    }catch(err){
      var inf=document.getElementById('wm-crit-json-info');
      if(inf){inf.style.color='#c0392b';inf.textContent='\u2716 '+err.message;}
    }
  };
  reader.readAsText(file);
}
function _wmShowCriteriaCfg(){
  var ex=document.getElementById('wm-crit-cfg-modal');if(ex){ex.remove();return;}
  var defs=DATA.yieldDefs||[];
  var tableRows=defs.length?defs.map(function(def,i){
    var dis=_wmCriteriaDisabled.has(i);
    return '<tr style="border-bottom:1px solid #eee"><td style="padding:4px 8px"><input type="checkbox" id="wm-cc-'+i+'" '+(dis?'':'checked')+' onchange="IC._wmCritCfgToggle('+i+',this.checked)" style="cursor:pointer"></td>'
      +'<td style="padding:4px 8px;font-size:12px">'+(def.bucket||'\u2014')+'</td>'
      +'<td style="padding:4px 8px;font-size:11px;color:#555">IB'+(def.bins||def.bins_list.join('/'))+'</td>'
      +'<td style="padding:4px 8px;font-size:12px;font-weight:bold;color:#2471a3">'+(def.expected||'\u2014')+'%</td></tr>';
  }).join(''):'<tr><td colspan="4" style="padding:10px;color:#888;text-align:center">No criteria loaded \u2014 load a JSON file below</td></tr>';
  var m=document.createElement('div');m.id='wm-crit-cfg-modal';
  m.style.cssText='position:fixed;z-index:99999;top:50%;left:50%;transform:translate(-50%,-50%);background:#fff;border:2px solid #1f618d;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.4);min-width:400px;max-width:580px;max-height:82vh;display:flex;flex-direction:column;font-family:Arial,sans-serif;font-size:12px';
  m.innerHTML='<div style=\'background:#1f618d;color:#fff;padding:8px 14px;border-radius:6px 6px 0 0;display:flex;justify-content:space-between;align-items:center;cursor:move;user-select:none\' id=\'wm-crit-cfg-hdr\'>'
    +'<b>&#9881; Yield Criteria Configuration</b>'
    +'<button onclick=\'document.getElementById(&quot;wm-crit-cfg-modal&quot;).remove()\' style=\'background:none;border:none;color:#fff;font-size:16px;cursor:pointer;padding:0 4px\'>&times;</button></div>'
    +'<div style=\'padding:6px 10px;background:#eaf0fb;font-size:11px;color:#555;border-bottom:1px solid #d0d8e8\'>Check items to include in the &quot;Criteria Miss Only&quot; filter. Load a custom JSON to override targets.</div>'
    +'<div style=\'padding:5px 10px;border-bottom:1px solid #e8eef6;background:#f7f9fc;display:flex;align-items:center;gap:8px;flex-shrink:0\'>'
    +'<span style=\'font-size:11px;font-weight:bold;color:#1f618d;white-space:nowrap\'>&#128193; Load JSON:</span>'
    +'<input type="file" id="wm-crit-json-inp" accept=".json" style="font-size:11px;flex:1;min-width:0" onchange="IC._wmCritLoadJson(this.files[0])">'
    +'<span id="wm-crit-json-info" style="font-size:11px;white-space:nowrap"></span></div>'
    +'<div style=\'display:flex;gap:6px;padding:6px 10px;border-bottom:1px solid #eee;flex-shrink:0\'>'
    +'<button onclick=\'IC._wmCritCfgAll(true)\' style=\'font-size:11px;padding:2px 10px;cursor:pointer;border:1px solid #bbb;border-radius:3px;background:#f5f5f5\'>Select All</button>'
    +'<button onclick=\'IC._wmCritCfgAll(false)\' style=\'font-size:11px;padding:2px 10px;cursor:pointer;border:1px solid #bbb;border-radius:3px;background:#f5f5f5\'>Clear All</button>'
    +'<span id=\'wm-crit-cfg-count\' style=\'font-size:11px;color:#555;margin-left:auto;line-height:2\'></span></div>'
    +'<div style=\'overflow-y:auto;flex:1\'>'
    +'<table style=\'border-collapse:collapse;width:100%\'><thead><tr style=\'background:#2c3e50;color:#fff\'>'
    +'<th style=\'padding:4px 8px;text-align:left\'>Enable</th><th style=\'padding:4px 8px;text-align:left\'>Bucket</th>'
    +'<th style=\'padding:4px 8px;text-align:left\'>Bins</th><th style=\'padding:4px 8px;text-align:left\'>Target</th></tr></thead>'
    +'<tbody id="wm-cc-tbody">'+tableRows+'</tbody></table></div>'
    +'<div style=\'padding:6px 10px;border-top:1px solid #eee;text-align:right;flex-shrink:0\'>'
    +'<button onclick=\'document.getElementById(&quot;wm-crit-cfg-modal&quot;).remove()\' style=\'font-size:11px;padding:3px 14px;cursor:pointer;background:#1f618d;color:#fff;border:none;border-radius:4px\'>Done</button></div>';
  document.body.appendChild(m);
  _wmCritCfgUpdateCount();
  (function(){var dx=0,dy=0,drag=false;var hd=document.getElementById('wm-crit-cfg-hdr');if(hd){hd.addEventListener('mousedown',function(e){if(e.button!==0)return;drag=true;dx=e.clientX-m.offsetLeft;dy=e.clientY-m.offsetTop;e.preventDefault();});document.addEventListener('mousemove',function(e){if(!drag)return;m.style.transform='none';m.style.left=(e.clientX-dx)+'px';m.style.top=(e.clientY-dy)+'px';});document.addEventListener('mouseup',function(){drag=false;});}})();
}
function _wmCritCfgToggle(i,on){
  if(on){_wmCriteriaDisabled.delete(i);}else{_wmCriteriaDisabled.add(i);}
  _wmCritCfgUpdateCount();
  _wmBuildCtrl();_wmRender();
}
function _wmCritCfgAll(on){
  var defs=DATA.yieldDefs||[];
  if(on){_wmCriteriaDisabled=new Set();}else{defs.forEach(function(_,i){_wmCriteriaDisabled.add(i);});}
  defs.forEach(function(_,i){var cb=document.getElementById('wm-cc-'+i);if(cb)cb.checked=on;});
  _wmCritCfgUpdateCount();
  _wmBuildCtrl();_wmRender();
}
function _wmCritCfgUpdateCount(){
  var defs=DATA.yieldDefs||[];var n=defs.length-_wmCriteriaDisabled.size;
  var el=document.getElementById('wm-crit-cfg-count');if(el)el.textContent=n+' / '+defs.length+' active';
}

function _wmScorePattern(failXn,failYn){
  var n=failXn.length;
  if(!n)return{center:0,edge:0,donut:0,systematic:0,random:1};
  var radii=[],N=n;
  for(var i=0;i<N;i++) radii.push(Math.sqrt(failXn[i]*failXn[i]+failYn[i]*failYn[i]));
  radii.sort(function(a,b){return a-b;});
  var zI=0,zM=0,zO=0;
  radii.forEach(function(r){if(r<0.4)zI++;else if(r<0.7)zM++;else zO++;});
  var fI=zI/N,fM=zM/N,fO=zO/N;
  var eI=0.16,eM=0.33,eO=0.51;
  var centerScore=Math.max(0,Math.min(1,(fI-eI)/0.4+0.5));
  var edgeScore  =Math.max(0,Math.min(1,(fO-eO)/0.3+0.5));
  var donutScore =Math.max(0,Math.min(1,(fM-eM)/0.25+0.5-(fI+fO)*0.3));
  var q=[0,0,0,0];
  for(var j=0;j<N;j++){
    var xi=failXn[j],yi=failYn[j];
    if(xi>=0&&yi>=0)q[0]++;else if(xi<0&&yi>=0)q[1]++;
    else if(xi<0&&yi<0)q[2]++;else q[3]++;
  }
  var qImbal=(Math.max.apply(null,q)-Math.min.apply(null,q))/N;
  /* scale systematic by sample size: need ≥20 fails for full confidence;
     small wafers with 1-5 fails can trivially land in one quadrant by chance */
  var sampleConf=Math.min(1,N/20);
  var systematicScore=Math.min(1,qImbal*2.5)*sampleConf;
  var ru=1-Math.abs(fI/eI-1)*0.3-Math.abs(fO/eO-1)*0.3;
  var randomScore=Math.max(0,Math.min(1,ru*(1-systematicScore*0.5)));
  return{center:+centerScore.toFixed(2),edge:+edgeScore.toFixed(2),donut:+donutScore.toFixed(2),systematic:+systematicScore.toFixed(2),random:+randomScore.toFixed(2)};
}
function _wmScoreReticle(actX,actY){
  /* Score how strongly fails cluster at the same within-reticle site across shots.
     actX/actY: actual wafer die coords of fail dies.
     Uses DATA.retMap ("x,y"->[rx,ry,shotIdx]) and DATA.retSiteTotals ("rx,ry"->N). */
  if(!DATA.retMap||!DATA.retSiteTotals||!actX||!actX.length)return 0;
  var rm=DATA.retMap,st=DATA.retSiteTotals;
  var siteShots={};   /* "rx,ry" -> object{shotIdx:true} */
  var siteCnt={};     /* "rx,ry" -> fail die count */
  var N=actX.length;
  for(var i=0;i<N;i++){
    var info=rm[actX[i]+','+actY[i]];
    if(!info)continue;
    var sk=info[0]+','+info[1];
    var si=String(info[2]);
    if(!siteShots[sk]){siteShots[sk]={};siteCnt[sk]=0;}
    siteShots[sk][si]=true;
    siteCnt[sk]++;
  }
  var sites=Object.keys(siteShots);
  if(!sites.length)return 0;
  var maxSiteScore=0,weightedSum=0,totalMapped=0;
  sites.forEach(function(sk){
    var totShots=st[sk]||1;
    var failShots=Object.keys(siteShots[sk]).length;
    var score=failShots/totShots;
    var cnt=siteCnt[sk];
    totalMapped+=cnt;
    weightedSum+=score*cnt;
    if(score>maxSiteScore)maxSiteScore=score;
  });
  if(!totalMapped)return 0;
  /* blend weighted-average score (detects broad field pattern) with max-site score (single hotspot) */
  var raw=(weightedSum/totalMapped)*0.4+maxSiteScore*0.6;
  var sampleConf=Math.min(1,N/15);
  return Math.min(1,raw*sampleConf);
}
function _wmPrimary(sc){
  var best='random',bv=sc.random;
  ['center','edge','donut','systematic','reticle'].forEach(function(k){if(sc[k]!==undefined&&sc[k]>bv){bv=sc[k];best=k;}});
  return{center:'CENTER',edge:'EDGE',donut:'DONUT',systematic:'SYSTEMATIC',reticle:'RETICLE',random:'RANDOM'}[best]||best.toUpperCase();
}
var _pColors={CENTER:'#c0392b',EDGE:'#e67e22',DONUT:'#8e44ad',SYSTEMATIC:'#2471a3',RETICLE:'#1f618d',RANDOM:'#27ae60'};

function _wmBarHtml(v,maxW){
  maxW=maxW||60;
  var w=Math.round(v*maxW),col=v<0.35?'#27ae60':v<0.65?'#e67e22':'#c0392b';
  return'<div class="wm-bar-bg" style="width:'+maxW+'px"><div class="wm-bar-fg" style="width:'+w+'px;background:'+col+'"></div></div>'+(v*100).toFixed(0)+'%';
}
function _wmEsc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/'/g,'&#39;');}

/* ---------- Ctrl builder ---------- */
function _wmBuildCtrl(){
  var ctrl=document.getElementById('wm-ctrl');
  if(!ctrl)return;
  var lotMap={},lotOrder=[];
  sR.forEach(function(ri){
    var row=DATA.rows[ri];if(!row)return;
    var lot=row.lot||'\u2014',w=row.wafer||'?';
    if(!lotMap[lot]){lotMap[lot]=[];lotOrder.push(lot);}
    lotMap[lot].push({ri:ri,w:w,m:row.material||''});
  });
  var threshRow='<div class="wm-thresh-row"><span style="font-size:11px;color:#555;white-space:nowrap">Fail\u202f\u2265\u202f</span>';
  [1,2,3,4,5].forEach(function(t){
    threshRow+='<button class="wm-tbtn'+(_wmFailThresh===t?' on':'')+'" onclick="IC._wmSetThresh('+t+')">IB'+t+'</button>';
  });
  threshRow+='<label style="display:inline-flex;align-items:center;gap:4px;font-size:11px;cursor:pointer;white-space:nowrap;border:1px solid '+(_wmCriteriaMissOnly?'#c0392b':'#bbb')+';border-radius:12px;padding:2px 9px;color:'+(_wmCriteriaMissOnly?'#c0392b':'#555')+';background:'+(_wmCriteriaMissOnly?'#fdecea':'#fff')+';margin-left:10px" title="Show only wafers where at least one bin does not meet its expected yield target"><input type="checkbox" '+(_wmCriteriaMissOnly?'checked':'')+' onchange="IC._wmToggleCriteriaMiss(this.checked)" style="cursor:pointer;margin:0">\u26a0 Criteria Miss Only</label>'
    +'<button id="wm-criteria-cfg-btn" onclick="IC._wmShowCriteriaCfg()" title="Configure which yield targets to check" style="font-size:11px;padding:2px 8px;border-radius:10px;border:1px solid '+(_wmCriteriaDisabled.size>0?'#f39c12':'#bbb')+';background:'+(_wmCriteriaDisabled.size>0?'rgba(243,156,18,0.15)':'#fff')+';color:'+(_wmCriteriaDisabled.size>0?'#7d6608':'#555')+';cursor:pointer;white-space:nowrap;margin-left:2px">\u2699 Criteria</button>'
    +'<span style="border-left:1px solid #ccc;margin:0 10px;height:14px;display:inline-block;vertical-align:middle"></span>'
    +'<span style="font-size:11px;color:#555;white-space:nowrap">Zoom:</span>'
    +'<button class="wm-tbtn" onclick="IC._wmZoomOut()" title="Zoom out" style="padding:1px 8px">&#8722;</button>'
    +'<span id="wm-zoom-lbl" style="font-size:11px;color:#555;min-width:34px;display:inline-block;text-align:center">'+Math.round(_wmZoom*100)+'%</span>'
    +'<button class="wm-tbtn" onclick="IC._wmZoomIn()" title="Zoom in" style="padding:1px 8px">&#43;</button>'
    +'</div>';
  if(!lotOrder.length){ctrl.innerHTML=threshRow;return;}
  var filtRow='<span style="font-size:11px;color:#555;white-space:nowrap;align-self:center">Wafers:</span>'
    +'<span class="wm-selall" onclick="IC._wmSelectAll(true)">All</span>'
    +' <span class="wm-selall" onclick="IC._wmSelectAll(false)">None</span>'
    +'<div class="wm-filtbar">';
  lotOrder.forEach(function(lot){
    var _lotMats=[];lotMap[lot].forEach(function(item){if(item.m&&_lotMats.indexOf(item.m)<0)_lotMats.push(item.m);});
    var _mFirst=_lotMats[0]||'',_mAll=_lotMats.join(', ');
    var _lotMatTag=_mFirst?'<span style="font-size:9px;color:#8e6a2a;margin-left:3px;font-weight:normal" title="'+_wmEsc(_mAll)+'">['+_wmEsc(_mFirst.length>20?_mFirst.slice(0,19)+'\u2026':_mFirst)+(_lotMats.length>1?'+'+(_lotMats.length-1):'')+']</span>':'';
    filtRow+='<div class="wm-lot-grp"><span class="wm-lot-lbl" onclick="IC._wmToggleLot(\''+_wmEsc(lot)+'\')" title="'+(_mAll?_wmEsc(_mAll):'Toggle all in lot')+'">'+_wmEsc(lot)+_lotMatTag+'</span>';
    lotMap[lot].forEach(function(item){
      var chk=(_wmSelRows===null||_wmSelRows.has(item.ri));
      var matTag=item.m?'<span style="font-size:9px;color:#8e6a2a;margin-left:2px">['+_wmEsc(item.m)+']</span>':'';
      filtRow+='<label class="wm-wcb" title="'+(item.m?_wmEsc(item.m):'')+'"><input type="checkbox" '+(chk?'checked':'')+' onchange="IC._wmToggleRow('+item.ri+',this.checked)">W'+_wmEsc(item.w)+matTag+'</label>';
    });
    filtRow+='</div>';
  });
  filtRow+='</div>';
  ctrl.innerHTML=threshRow+'<div style="display:flex;align-items:flex-start;gap:8px;flex-wrap:wrap;margin-top:4px">'+filtRow+'</div>';
}

/* ---------- Bin checkbox row (below legend) ---------- */
function _wmBuildBinRow(ibsPresent){
  var row=document.getElementById('wm-binrow');
  if(!row)return;
  if(!ibsPresent||!ibsPresent.length){row.innerHTML='';return;}
  var h='<span style="font-size:10px;color:#666;white-space:nowrap;align-self:center">IB:</span>'
    +'<label class="wm-bincb" title="All"><input type="checkbox" '+((_wmBinChecked===null)?'checked':'')+' onchange="IC._wmToggleBinAll(this.checked)"><b>All</b></label>';
  ibsPresent.forEach(function(ib){
    var on=(_wmBinChecked===null||_wmBinChecked.has(ib));
    h+='<label class="wm-bincb"><input type="checkbox" '+(on?'checked':'')+' onchange="IC._wmToggleBin('+ib+',this.checked)">IB'+ib+'</label>';
  });
  row.innerHTML=h;
}
function _wmToggleBinAll(on){_wmBinChecked=on?null:new Set();_wmRender();}
function _wmToggleBin(ib,on){
  if(_wmBinChecked===null){
    /* expand to all bins that were present */
    var all=[];
    sR.forEach(function(ri){var row=DATA.rows[ri];if(!row||!row.dies)return;row.dies.forEach(function(d){if(d[2]!==null&&d[2]!==undefined)all.push(d[2]);});});
    _wmBinChecked=new Set(all);
  }
  if(on)_wmBinChecked.add(ib);else _wmBinChecked.delete(ib);
  _wmRender();
}
function _wmBadgeToggle(ib,on){
  /* Show Only: checking a badge shows ONLY those pinned bins; unchecking all resets to show all */
  if(on)_wmBadgePinned.add(ib);else _wmBadgePinned.delete(ib);
  _wmBinChecked=_wmBadgePinned.size>0?new Set(_wmBadgePinned):null;
  _wmRender();
  if(_wmdOpen&&_wmdRi>=0)_wmdRenderPattern(_wmdRi);
}
function _wmBadgeClearAll(){
  _wmBadgePinned.clear();
  _wmBinChecked=null;
  _wmRender();
  if(_wmdOpen&&_wmdRi>=0)_wmdRenderPattern(_wmdRi);
}

/* ---------- Tab switcher ---------- */
function _wmTab(t){
  _wmActiveTab=t;
  ['impact','reticle','guide'].forEach(function(n){
    var btn=document.getElementById('wm-tab-'+n),pane=document.getElementById('wm-pane-'+n);
    if(btn)btn.classList.toggle('on',n===t);
    if(pane)pane.classList.toggle('on',n===t);
  });
  if(t==='reticle')_wmRenderReticle();
}
function _wmGetSiteShots(){
  if(_wmSiteToShots)return _wmSiteToShots;
  _wmSiteToShots={};
  if(DATA.hasReticle&&DATA.retMap){
    Object.keys(DATA.retMap).forEach(function(k){
      var info=DATA.retMap[k];var sk=info[0]+','+info[1];
      if(!_wmSiteToShots[sk])_wmSiteToShots[sk]=new Set();
      _wmSiteToShots[sk].add(info[2]);
    });
  }
  return _wmSiteToShots;
}
function _wmRetSiteToggle(sk,on){
  if(on){if(!_wmRetChecked)_wmRetChecked=new Set();_wmRetChecked.add(sk);}
  else{if(_wmRetChecked){_wmRetChecked.delete(sk);if(_wmRetChecked.size===0)_wmRetChecked=null;}}
  _wmRender();
}
function _wmRetClear(){_wmRetChecked=null;_wmRender();_wmRenderReticle();}
/* ---------- Reticle analysis tab render ---------- */
function _wmRenderReticle(){
  var el=document.getElementById('wm-reticle-body');
  if(!el)return;
  if(!DATA.hasReticle||!DATA.retMap||!DATA.retSiteTotals){el.innerHTML='<span style="color:#aaa;font-size:11px">No reticle mapping loaded.</span>';return;}
  /* Aggregate fail counts per reticle site (rx,ry) across selected wafers */
  var vis=_wmVisRows();
  var siteFailShots={};  /* "rx,ry" -> Set of shotIdx where any fail occurred */
  var siteTotalShots=DATA.retSiteTotals;  /* "rx,ry" -> total shots on wafer */
  var siteFailCount={};  /* "rx,ry" -> total fail die count */
  var grandTotalFail=0;
  vis.forEach(function(ri){
    var row=DATA.rows[ri];if(!row||!row.dies)return;
    row.dies.forEach(function(d){
      var x=d[0],y=d[1],ib=d[2];
      if(x===null||x===undefined)return;
      if(!_wmIsFail(ib))return;
      var binOn=(_wmBinChecked===null||_wmBinChecked.has(ib));
      if(!binOn)return;
      var info=DATA.retMap[x+','+y];
      if(!info)return;
      var sk=info[0]+','+info[1];
      var si=String(info[2]);
      if(!siteFailShots[sk])siteFailShots[sk]={};
      if(!siteFailShots[sk][ri])siteFailShots[sk][ri]=new Set();
      siteFailShots[sk][ri].add(si);
      siteFailCount[sk]=(siteFailCount[sk]||0)+1;
      grandTotalFail++;
    });
  });
  /* Build full site list: all mapped sites (from retSiteTotals) + any with fails */
  var allSiteKeys=DATA.retSiteTotals?Object.keys(DATA.retSiteTotals):[];
  Object.keys(siteFailCount).forEach(function(sk){if(allSiteKeys.indexOf(sk)<0)allSiteKeys.push(sk);});
  if(!allSiteKeys.length){el.innerHTML='<span style="color:#7f8c8d;font-size:11px">No fail dies mapped to reticle sites for selected wafers/bins.</span>';return;}
  /* Sort by fail count desc; sites with no fails go last, sorted by rx,ry */
  allSiteKeys.sort(function(a,b){
    var fa=siteFailCount[a]||0,fb=siteFailCount[b]||0;
    if(fb!==fa)return fb-fa;
    var pa=a.split(','),pb=b.split(',');
    return (+pa[1]- +pb[1])||( +pa[0]- +pb[0]);
  });
  /* Total shots on wafer for reference (use retSiteTotals) */
  var nWafers=vis.filter(function(ri){var r=DATA.rows[ri];return r&&r.dies&&r.dies.length;}).length;
  var nSitesWithFails=Object.keys(siteFailCount).length;
  var h='<div style="font-size:10px;color:#555;margin-bottom:6px"><b>Reticle Site Fail Analysis</b> \u2014 '
    +allSiteKeys.length+' reticle site(s) in layout'
    +(nSitesWithFails?', '+nSitesWithFails+' with fails':'')+' across '+nWafers+' wafer(s). '
    +'Each row shows a unique within-reticle position (rx,ry). High <b>Hit%</b> = same mask site fails on many wafers = strong reticle pattern suspect.'
    +(_wmRetChecked&&_wmRetChecked.size>0?' \u2014 <a href="#" onclick="IC._wmRetClear();return false" style="color:#c0392b;font-weight:bold">\u00d7 Clear highlights</a>':'')
    +'</div>'
    +'<table style="border-collapse:collapse;font-size:11px;width:100%">'
    +'<thead><tr>'
    +'<th style="background:#1f618d;color:#fff;padding:4px 6px;text-align:center" title="Check to highlight this site\'s shots on the wafer maps">\u2611</th>'
    +'<th style="background:#1f618d;color:#fff;padding:4px 8px;text-align:center">rx</th>'
    +'<th style="background:#1f618d;color:#fff;padding:4px 8px;text-align:center">ry</th>'
    +'<th style="background:#1f618d;color:#fff;padding:4px 8px;text-align:right">Fail Dies</th>'
    +'<th style="background:#1f618d;color:#fff;padding:4px 8px;text-align:right">% of Fails</th>'
    +'<th style="background:#1f618d;color:#fff;padding:4px 8px;text-align:right" title="Number of wafers where this reticle site has at least one fail">Wafer Hits</th>'
    +'<th style="background:#1f618d;color:#fff;padding:4px 8px;text-align:right" title="Fraction of displayed wafers where this reticle site has any fail die">Hit% (wafers)</th>'
    +'<th style="background:#1f618d;color:#fff;padding:4px 8px;text-align:right" title="Total shots this reticle site appears on the wafer">Shots/wafer</th>'
    +'</tr></thead><tbody>';
  var altRow=false;
  allSiteKeys.forEach(function(sk){
    var parts=sk.split(',');var rx=parts[0],ry=parts[1];
    var fc=siteFailCount[sk]||0;
    var pctOfFail=grandTotalFail>0&&fc>0?(fc/grandTotalFail*100).toFixed(1):'0.0';
    var waferHits=siteFailShots[sk]?Object.keys(siteFailShots[sk]).length:0;
    var hitPct=nWafers>0?(waferHits/nWafers*100).toFixed(0):0;
    var heatPct=nWafers>0?waferHits/nWafers:0;
    var totShots=(DATA.retSiteTotals&&DATA.retSiteTotals[sk])||1;
    var bg=fc===0?'#f8f8f8':heatPct>=0.7?'#fde8e8':heatPct>=0.4?'#fef3cd':altRow?'#f0f4fb':'#fff';
    var isChk=_wmRetChecked&&_wmRetChecked.has(sk);
    h+='<tr style="background:'+bg+'">'
      +'<td style="padding:3px 6px;text-align:center"><input type="checkbox"'+(isChk?' checked':'')+' onchange="IC._wmRetSiteToggle(\''+sk+'\',this.checked)" style="cursor:pointer;width:13px;height:13px"></td>'
      +'<td style="padding:3px 8px;text-align:center">'+rx+'</td>'
      +'<td style="padding:3px 8px;text-align:center">'+ry+'</td>'
      +'<td style="padding:3px 8px;text-align:right'+(fc===0?';color:#bbb':'')+'">'+fc+'</td>'
      +'<td style="padding:3px 8px;text-align:right'+(fc===0?';color:#bbb':'')+'">'+pctOfFail+'%</td>'
      +'<td style="padding:3px 8px;text-align:right'+(fc===0?';color:#bbb':'')+'">'+waferHits+'/'+nWafers+'</td>'
      +'<td style="padding:3px 8px;text-align:right;font-weight:'+(heatPct>=0.7?'bold':'normal')+';color:'+(heatPct>=0.7?'#c0392b':heatPct>=0.4?'#e67e22':fc===0?'#bbb':'#27ae60')+'">'+(+hitPct)+'%</td>'
      +'<td style="padding:3px 8px;text-align:right;color:#888">'+totShots+'</td>'
      +'</tr>';
    altRow=!altRow;
  });
  h+='</tbody></table>'
    +'<div style="margin-top:8px;font-size:10px;color:#888"><b>Color:</b> <span style="background:#fde8e8;padding:1px 5px;border-radius:2px">Red ≥70% wafer hit rate</span> &nbsp; <span style="background:#fef3cd;padding:1px 5px;border-radius:2px">Yellow 40\u201369%</span> &nbsp; <span style="background:#fff;border:1px solid #dde;padding:1px 5px;border-radius:2px">White &lt;40% (likely random)</span></div>';
  el.innerHTML=h;
}
function _wmSetThresh(t){_wmFailThresh=t;_wmRender();}
function _wmSelectAll(on){_wmSelRows=on?null:new Set();_wmRender();}
function _wmToggleRow(ri,checked){
  if(_wmSelRows===null){_wmSelRows=new Set();sR.forEach(function(r){_wmSelRows.add(r);});}
  if(checked)_wmSelRows.add(ri);else _wmSelRows.delete(ri);
  _wmRender();
}
function _wmToggleLot(lot){
  var inLot=[];
  sR.forEach(function(ri){var row=DATA.rows[ri];if(row&&(row.lot||'\u2014')===lot)inLot.push(ri);});
  if(!inLot.length)return;
  if(_wmSelRows===null){_wmSelRows=new Set();sR.forEach(function(r){_wmSelRows.add(r);});}
  var allOn=inLot.every(function(ri){return _wmSelRows.has(ri);});
  inLot.forEach(function(ri){if(allOn)_wmSelRows.delete(ri);else _wmSelRows.add(ri);});
  _wmRender();
}
function _wmToggleCriteriaMiss(on){
  _wmCriteriaMissOnly=on;
  /* refresh ctrl to update checkbox/button styling */
  _wmBuildCtrl();
  var maps=document.getElementById('wm-maps');
  if(maps)maps._wmHover=false; /* force hover re-init */
  _wmRender();
}

/* ---------- Main render ---------- */
function _wmRender(){
  var maps=document.getElementById('wm-maps');
  var tbody=document.getElementById('wm-tbody');
  var note=document.getElementById('wm-note');
  var legend=document.getElementById('wm-legend');
  var impactBody=document.getElementById('wm-impact-body');
  if(!maps||!tbody)return;
  if(!DATA||!DATA.rows){maps.innerHTML='<span style="color:#999">No data</span>';return;}
  if(_wmSelRows!==null){
    var prev=_wmSelRows;_wmSelRows=new Set();
    prev.forEach(function(ri){if(sR.has(ri))_wmSelRows.add(ri);});
  }
  _wmBuildCtrl();
  var vis=_wmVisRows();
  if(!vis.length||!vis.some(function(ri){var r=DATA.rows[ri];return r&&r.dies&&r.dies.length;})){
    maps.innerHTML='<span style="color:#7f8c8d;font-size:12px">No die-level data for selected wafers.</span>';
    tbody.innerHTML='';if(note)note.innerHTML='';if(legend)legend.innerHTML='';return;
  }
  var FIXED_W=Math.round(180*_wmZoom),pad=2;
  var mapsHtml='',tbHtml='';
  var allPrimary={};
  var ibSeen={};
  var ibPatAcc={};
  var failIbsAll=new Set(); /* all fail IB values seen */

  vis.forEach(function(ri){
    var row=DATA.rows[ri];
    if(!row||!row.dies||!row.dies.length)return;
    var dies=row.dies;
    var xs=[],ys=[];
    dies.forEach(function(d){if(d[0]!==null&&d[0]!==undefined){xs.push(d[0]);ys.push(d[1]);}});
    if(!xs.length)return;
    var xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs);
    var yMin=Math.min.apply(null,ys),yMax=Math.max.apply(null,ys);
    var xCnt=xMax-xMin+1,yCnt=yMax-yMin+1;
    var cs=Math.max(1,(FIXED_W-pad*2)/xCnt);
    var xSpan=xMax-xMin,ySpan=yMax-yMin;
    var csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;
    var W=FIXED_W,H=Math.round(yCnt*csy+pad*2);
    var xCtr=(xMin+xMax)/2,yCtr=(yMin+yMax)/2;
    var xRad=(xMax-xMin)/2||1,yRad=(yMax-yMin)/2||1;
    var ibCoords={};
    var failXn=[],failYn=[],failActX=[],failActY=[];
    var totalDies=0,failDies=0;
    var failShotIdx=new Set();
    dies.forEach(function(d){
      var x=d[0],y=d[1],ib=d[2];
      if(x===null||x===undefined)return;
      totalDies++;
      var fill=_wmIbColor(ib);
      var xn=(x-xCtr)/xRad,yn=(y-yCtr)/yRad;
      var isFail=_wmIsFail(ib);
      var ibKey=ib!==null&&ib!==undefined?ib:null;
      ibSeen[ibKey]=fill;
      var binOn=(_wmBinChecked===null||_wmBinChecked.has(ibKey));
      if(isFail&&ibKey!==null){
        failIbsAll.add(ibKey);
        if(binOn){
          failXn.push(xn);failYn.push(yn);failActX.push(x);failActY.push(y);failDies++;
          if(DATA.hasReticle&&DATA.retMap){var _ri=DATA.retMap[x+','+y];if(_ri)failShotIdx.add(_ri[2]);}
          if(!ibCoords[ibKey])ibCoords[ibKey]={xn:[],yn:[],ax:[],ay:[]};
          ibCoords[ibKey].xn.push(xn);ibCoords[ibKey].yn.push(yn);
          ibCoords[ibKey].ax.push(x);ibCoords[ibKey].ay.push(y);
        }
      }
    });
    /* accumulate per-bin pattern */
    Object.keys(ibCoords).forEach(function(ibk){
      var c=ibCoords[ibk];
      var sc=_wmScorePattern(c.xn,c.yn);
      var ibRet=DATA.hasReticle&&c.ax.length>0?_wmScoreReticle(c.ax,c.ay):0;
      if(!ibPatAcc[ibk])ibPatAcc[ibk]={center:0,edge:0,donut:0,systematic:0,reticle:0,random:0,cnt:0,dies:0};
      var a=ibPatAcc[ibk];
      a.center+=sc.center;a.edge+=sc.edge;a.donut+=sc.donut;a.systematic+=sc.systematic;a.reticle+=ibRet;a.random+=sc.random;a.cnt++;a.dies+=c.xn.length;
    });
    var sc=_wmScorePattern(failXn,failYn);
    var retScore=DATA.hasReticle&&failDies>0?_wmScoreReticle(failActX,failActY):0;
    if(DATA.hasReticle){sc.reticle=retScore;}
    var primary=_wmPrimary(sc);
    var failPct=totalDies>0?(failDies/totalDies*100).toFixed(1)+'%':'—';
    /* driver IB — only shown when primary pattern score < 100% */
    var patKey=primary.toLowerCase();
    var driverIb='\u2014';
    if(failDies>0){
      /* find top count, then collect all bins within 80% of it */
      var topCnt=0;
      Object.keys(ibCoords).forEach(function(ibk){
        var n=ibCoords[ibk].xn.length;if(n>topCnt)topCnt=n;
      });
      var thresh80=topCnt*0.8;
      var drivers=[];
      Object.keys(ibCoords).sort(function(a,b){return ibCoords[b].xn.length-ibCoords[a].xn.length;}).forEach(function(ibk){
        var n=ibCoords[ibk].xn.length;
        if(n>=thresh80)drivers.push('IB'+ibk+'(n='+n+')');
      });
      if(drivers.length)driverIb=drivers.join(', ');
    }
    var lbl=_wmEsc((row.lot||'')+' W'+(row.wafer||''));
    allPrimary[primary]=(allPrimary[primary]||0)+1;
    var pCol=_pColors[primary]||'#555';
    /* wafer map: clicking title opens detail popup; die click opens FB */
    var _cmBins=_wmGetCriteriaMissBins(row);
    var _cmInfo=_wmGetCriteriaMissInfo(row);
    var _cmBadgeHtml='';
    if(_cmBins.length){
      _cmBadgeHtml='<div style="display:flex;flex-wrap:wrap;gap:2px;justify-content:center;margin-top:3px">';
      _cmBins.forEach(function(b){
        var col=DATA.binColors[b]||'#e74c3c';
        var pinned=_wmBadgePinned.has(+b);
        _cmBadgeHtml+='<label style="display:inline-flex;align-items:center;gap:2px;font-size:9px;cursor:pointer;border:1px solid '+col+';border-radius:3px;padding:1px 3px;background:'+(pinned?col+'33':'#fff')+';color:#333;white-space:nowrap" title="Show Only IB'+b+' on wafer map">'
          +'<input type="checkbox" '+(pinned?'checked':'')+' onchange="IC._wmBadgeToggle('+(+b)+',this.checked)" style="cursor:pointer;margin:0;width:9px;height:9px">'
          +'IB'+b+'</label>';
      });
      _cmBadgeHtml+='</div>';
    }
    var _cmCardStyle='text-align:center'+(_cmBins.length?';border:2px solid rgba(192,57,43,0.4);border-radius:5px;padding:2px':'');
    var _cmWlblExtra=_cmBins.length?'<span style="color:#c0392b;margin-left:3px;font-size:11px">&#9888;</span>':'';
    var _cmTitle=_cmBins.length?_wmEsc('Miss: '+_cmInfo.join(' \u2502 ')):'Open wafer detail';
    mapsHtml+='<div class="wm-tile-ph" data-ri="'+ri+'" style="'+_cmCardStyle+'">'
      +'<div class="wm-wlbl" style="cursor:pointer;text-decoration:underline" onclick="IC._wmdOpen('+ri+')" title="'+_cmTitle+'">'+lbl+_cmWlblExtra+'</div>'
      +'<div class="wm-tile-content" style="width:'+W+'px;height:'+H+'px;background:#e8ecf0;border-radius:3px;display:inline-block"></div>'
      +'<div style="font-size:10px;color:'+pCol+';font-weight:bold;margin-top:2px">'+primary+'</div>'
      +_cmBadgeHtml
      +'</div>';
    tbHtml+='<tr>'
      +'<td style="white-space:nowrap;font-size:10px">'+_wmEsc(row.lot||'')+'</td>'
      +'<td style="white-space:nowrap;cursor:pointer;color:#1a5276;text-decoration:underline" onclick="IC._wmdOpen('+ri+')" title="Open wafer detail">'+_wmEsc(row.wafer||'')+'</td>'
      +'<td style="white-space:nowrap;font-size:10px">'+_wmEsc(row.material||'')+'</td>'
      +'<td style="font-weight:bold;color:'+pCol+'">'+primary+'</td>'
      +'<td>'+failPct+'<span style="font-size:9px;color:#999;margin-left:3px">(n='+failDies+')</span></td>'
      +'<td style="font-size:10px;color:#555;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+_wmEsc(driverIb)+'">'+_wmEsc(driverIb)+'</td>'
      +'<td>'+_wmBarHtml(sc.center)+'</td>'
      +'<td>'+_wmBarHtml(sc.edge)+'</td>'
      +'<td>'+_wmBarHtml(sc.donut)+'</td>'
      +'<td>'+_wmBarHtml(sc.systematic)+'</td>'
      +(DATA.hasReticle?'<td>'+_wmBarHtml(retScore)+'</td>':'')
      +'<td>'+_wmBarHtml(sc.random)+'</td>'
      +'</tr>';
  });

  maps.innerHTML=mapsHtml||'<span style="color:#999;font-size:12px">No wafers with die data</span>';
  _wmRenderedRis=new Set();
  if(_wmObserver){_wmObserver.disconnect();_wmObserver=null;}
  var _wMapWrap=document.querySelector('.wm-maps-wrap');
  _wmObserver=new IntersectionObserver(function(entries){
    entries.forEach(function(entry){
      if(entry.isIntersecting){
        var _phRi=parseInt(entry.target.dataset.ri,10);
        var _phEl=entry.target.querySelector('.wm-tile-content');
        if(_phEl)_wmRenderTile(_phRi,_phEl);
      }
    });
  },{root:_wMapWrap,rootMargin:'300px 0px'});
  maps.querySelectorAll('.wm-tile-ph[data-ri]').forEach(function(el){_wmObserver.observe(el);});
  tbody.innerHTML=tbHtml;

  /* --- Bin impact tab --- */
  var ibKeys=Object.keys(ibPatAcc).sort(function(a,b){return +a- +b;});
  if(impactBody&&ibKeys.length){
    var dims=['center','edge','donut','systematic','random'];
    if(DATA.hasReticle)dims.splice(4,0,'reticle');
    var dimLabels={center:'Center',edge:'Edge',donut:'Donut',systematic:'Syst.',reticle:'Reticle',random:'Random'};
    var ibh='<div style="font-size:10px;color:#888;margin-bottom:6px">Avg pattern score per fail bin (across displayed wafers)</div>';
    ibKeys.forEach(function(ibk){
      var a=ibPatAcc[ibk],cnt=a.cnt||1;
      var nDies=a.dies||0;
      var col=_wmIbColor(+ibk);
      var bestDim='random',bestVal=a.random/cnt;
      dims.forEach(function(d){if(a[d]/cnt>bestVal){bestVal=a[d]/cnt;bestDim=d;}});
      var bdCol=_pColors[bestDim.toUpperCase()]||'#555';
      ibh+='<div class="wm-impact-row" style="margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid #eee">'
        +'<div class="wm-impact-lbl" style="color:'+col+'">IB'+ibk+'<span style="font-size:9px;color:#999;margin-left:3px">(n='+nDies+')</span></div>'
        +'<div style="flex:1;display:flex;flex-wrap:wrap;gap:3px 8px">';
      dims.forEach(function(d){
        var v=a[d]/cnt;
        var bc=v<0.35?'#27ae60':v<0.65?'#e67e22':'#c0392b';
        var extraStyle=d==='reticle'?'font-weight:bold;':'';
        ibh+='<div style="display:inline-flex;align-items:center;gap:2px;font-size:10px">'
          +'<span style="width:'+( d==='reticle'?'40':'30')+'px;color:'+(d==='reticle'?'#1f618d':'#666')+';'+extraStyle+'">'+dimLabels[d]+'</span>'
          +'<div class="wm-impact-bar" style="width:44px"><div class="wm-impact-fill" style="width:'+Math.round(v*44)+'px;background:'+bc+'"></div></div>'
          +'<span style="width:28px;font-size:10px;color:#555">'+Math.round(v*100)+'%</span>'
          +'</div>';
      });
      ibh+='</div><div style="font-size:10px;font-weight:bold;color:'+bdCol+';white-space:nowrap;margin-left:4px">\u2192'+bestDim.toUpperCase()+'</div>'
        +'<button title="Analyze IB'+ibk+' (all selected wafers)" onclick="IC.showFbModal('+ibk+')" '
        +'style="background:none;border:1px solid #c8d4e0;border-radius:3px;cursor:pointer;font-size:10px;padding:0 4px;line-height:16px;color:#1a5276;flex-shrink:0;margin-left:6px">&#128300;</button>'
        +'</div>';
    });
    impactBody.innerHTML=ibh;
  } else if(impactBody){impactBody.innerHTML='<span style="color:#aaa;font-size:11px">No fail die data</span>';}

  /* bin checkbox row — all IBs seen in data */
  var allIbArr=[];
  Object.keys(ibSeen).forEach(function(k){if(k!=='null'&&k!==null)allIbArr.push(+k);});
  allIbArr.sort(function(a,b){return a-b;});
  _wmBuildBinRow(allIbArr);

  /* dynamic legend */
  if(legend){
    var legParts=[{ib:1,label:'IB1'},{ib:2,label:'IB2'},{ib:3,label:'IB3/4'},{ib:null,label:'N/A'}];
    Object.keys(ibSeen).sort(function(a,b){return(+a||0)-(+b||0);}).forEach(function(k){var v=+k;if(v>4)legParts.push({ib:v,label:'IB'+v});});
    var lh='';
    legParts.forEach(function(lp){
      var col=_wmIbColor(lp.ib);
      var clk=(lp.ib!==null&&lp.ib>4&&DATA.hasFunctionalBin)
        ?' style="cursor:pointer" onclick="IC.showFbModal('+lp.ib+')" title="Open IB'+lp.ib+' distribution"':' style="cursor:default"';
      var failMark=_wmIsFail(lp.ib)&&lp.ib!==null?'*':'';
      lh+='<div class="wm-lgi"'+clk+'><div class="wm-lgsw" style="background:'+col+'"></div>'+_wmEsc(lp.label)+failMark+'</div>';
    });
    lh+='<span style="font-size:10px;color:#888">*\u202f=\u202ffail (\u2265IB'+_wmFailThresh+')</span>';
    legend.innerHTML=lh;
  }
  var keys=Object.keys(allPrimary).sort(function(a,b){return allPrimary[b]-allPrimary[a];});
  if(note)note.innerHTML='<b>Fail\u202f=\u202fIB\u2265'+_wmFailThresh+'.</b> Click wafer label or table row for UPM heatmap + HW detail. Click fail die for FB distribution.';
  _wmSetupHover();_wmSetupClick();
}

function _wmSetupHover(){
  var maps=document.getElementById('wm-maps');
  if(!maps||maps._wmHover)return;
  maps._wmHover=true;
  maps.addEventListener('mousemove',function(e){
    var el=e.target;
    if(el&&el.tagName==='rect'&&el.dataset&&el.dataset.tip){
      var t=document.getElementById('wm-tip');
      if(!t){t=document.createElement('div');t.id='wm-tip';
        t.style.cssText='position:fixed;background:rgba(20,20,40,0.92);color:#fff;font-size:11px;padding:4px 8px;border-radius:4px;pointer-events:none;z-index:30001;box-shadow:0 2px 6px rgba(0,0,0,.4)';
        document.body.appendChild(t);}
      t.textContent=el.dataset.tip;
      t.style.left=(e.clientX+12)+'px';t.style.top=(e.clientY-8)+'px';t.style.display='block';
    }else{var t2=document.getElementById('wm-tip');if(t2)t2.style.display='none';}
  });
  maps.addEventListener('mouseleave',function(){var t=document.getElementById('wm-tip');if(t)t.style.display='none';});
}
function _wmSetupClick(){
  var maps=document.getElementById('wm-maps');
  if(!maps||maps._wmClick)return;
  maps._wmClick=true;
  maps.addEventListener('click',function(e){
    var el=e.target;
    if(el&&el.tagName==='rect'&&el.dataset){
      var ibStr=el.dataset.ib;
      if(ibStr!==''&&ibStr!==undefined){
        var ib=parseInt(ibStr,10);
        if(ib>4&&DATA.hasFunctionalBin){IC.showFbModal(ib);}
      }
    }
  });
}
/* ---- Canvas/SVG mode toggle ---- */
function _wmToggleCanvasMode(){
  _wmCanvasMode=!_wmCanvasMode;
  var btn=document.getElementById('wm-mode-btn');
  if(btn)btn.innerHTML=_wmCanvasMode?'&#128247; SVG mode':'&#9889; Fast mode';
  var maps=document.getElementById('wm-maps');if(!maps)return;
  maps.querySelectorAll('.wm-tile-ph[data-ri]').forEach(function(el){
    var ri=parseInt(el.dataset.ri,10);
    if(_wmRenderedRis.has(ri)){var tc=el.querySelector('.wm-tile-content');if(tc)_wmRenderTile(ri,tc);}
  });
}
/* ---- Per-tile renderer (SVG or canvas) ---- */
function _wmRenderTile(ri,container){
  var row=DATA.rows[ri];
  if(!row||!row.dies||!row.dies.length)return;
  var dies=row.dies;
  var xs=[],ys=[];
  dies.forEach(function(d){if(d[0]!==null&&d[0]!==undefined){xs.push(d[0]);ys.push(d[1]);}});
  if(!xs.length)return;
  var FIXED_W=Math.round(180*_wmZoom),pad=2;
  var xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs);
  var yMin=Math.min.apply(null,ys),yMax=Math.max.apply(null,ys);
  var xCnt=xMax-xMin+1,yCnt=yMax-yMin+1;
  var cs=Math.max(1,(FIXED_W-pad*2)/xCnt);
  var xSpan=xMax-xMin,ySpan=yMax-yMin;
  var csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;
  var W=FIXED_W,H=Math.round(yCnt*csy+pad*2);
  var xCtr=(xMin+xMax)/2,yCtr=(yMin+yMax)/2;
  var xRad=xSpan/2||1,yRad=ySpan/2||1;
  var cxE=(pad+(xCtr-xMin)*cs+cs*0.45).toFixed(1);
  var cyE=(pad+(yMax-yCtr)*csy+csy*0.45).toFixed(1);
  var rxE=(xRad*cs+cs*0.5).toFixed(1);
  var ryE=(yRad*csy+csy*0.5).toFixed(1);
  var failShotIdx3=new Set();
  container.style.width=W+'px';container.style.height=H+'px';container.style.background='';container.style.borderRadius='';
  if(_wmCanvasMode){
    var cv=document.createElement('canvas');
    cv.width=W;cv.height=H;cv.style.display='block';
    var ctx=cv.getContext('2d');
    dies.forEach(function(d){
      var x=d[0],y=d[1],ib=d[2];
      if(x===null||x===undefined)return;
      var ibKey=ib!==null&&ib!==undefined?ib:null;
      var binOn=(_wmBinChecked===null||_wmBinChecked.has(ibKey));
      ctx.globalAlpha=binOn?1:0.08;
      ctx.fillStyle=_wmIbColor(ib);
      ctx.fillRect(pad+(x-xMin)*cs,pad+(yMax-y)*csy,cs*0.9,csy*0.9);
      if(_wmIsFail(ib)&&ibKey!==null&&binOn&&DATA.hasReticle&&DATA.retMap){var _rt=DATA.retMap[x+','+y];if(_rt)failShotIdx3.add(_rt[2]);}
    });
    ctx.globalAlpha=1;
    if(DATA.hasReticle&&DATA.retShots&&DATA.retShots.length){
      var _hlSC=null;
      if(_wmRetChecked&&_wmRetChecked.size>0){var _sC=_wmGetSiteShots();_hlSC=new Set();_wmRetChecked.forEach(function(sk){if(_sC[sk])_sC[sk].forEach(function(si){_hlSC.add(si);});});}
      DATA.retShots.forEach(function(shot,si){
        var sx=pad+(shot[0]-xMin)*cs,sy=pad+(yMax-shot[3])*csy,sw=(shot[2]-shot[0]+1)*cs,sh=(shot[3]-shot[1]+1)*csy;
        if(_hlSC){ctx.strokeStyle=_hlSC.has(si)?'#f39c12':'#ddd';ctx.lineWidth=_hlSC.has(si)?1.5:0.5;}
        else{ctx.strokeStyle=failShotIdx3.has(si)?'#c0392b':'#2471a3';ctx.lineWidth=failShotIdx3.has(si)?1.2:0.5;ctx.globalAlpha=failShotIdx3.has(si)?0.9:0.3;}
        ctx.strokeRect(sx,sy,sw,sh);ctx.globalAlpha=1;
      });
    }
    ctx.strokeStyle='#bdc3c7';ctx.lineWidth=1;
    ctx.beginPath();ctx.ellipse(parseFloat(cxE),parseFloat(cyE),parseFloat(rxE),parseFloat(ryE),0,0,2*Math.PI);ctx.stroke();
    /* Die-loc numbers on canvas */
    if(DATA.hasReticle&&DATA.retSiteNum&&cs>=4){
      var _dlFs2=Math.max(4,Math.min(7,Math.round(cs*0.55)));
      ctx.font='bold '+_dlFs2+'px Arial';ctx.textAlign='right';ctx.textBaseline='top';
      dies.forEach(function(d){
        var x=d[0],y=d[1];if(x===null||x===undefined)return;
        var _inf=DATA.retMap&&DATA.retMap[x+','+y];if(!_inf)return;
        var _dtag=String(DATA.retSiteNum[_inf[0]+','+_inf[1]]||'');if(!_dtag)return;
        ctx.fillStyle='#000';ctx.globalAlpha=1;
        ctx.fillText(_dtag,pad+(x-xMin)*cs+cs-0.5,pad+(yMax-y)*csy+0.5);
      });
    }
    /* Canvas hover: build die lookup, attach listener once */
    cv._dl2={};cv._xMn2=xMin;cv._yMx2=yMax;cv._cs2=cs;cv._csy2=csy;cv._pad2=pad;
    dies.forEach(function(d){
      var x=d[0],y=d[1];if(x===null||x===undefined)return;
      var ib=d[2],fb=d[3];
      var uStart=DATA.upmStart||5;
      var uv=(DATA.upmCols&&DATA.upmCols.length)?DATA.upmCols.map(function(_,i){var v=d[uStart+i];return(v!==undefined&&v!==null)?v:'';}).join('|'):'';
      cv._dl2[x+','+y]={ib:ib!==undefined?ib:null,fb:fb!==undefined?fb:null,uv:uv,x:x,y:y};
    });
    if(!cv._hvBound2){cv._hvBound2=true;
      cv.addEventListener('mousemove',function(e){
        var r=cv.getBoundingClientRect(),sx=cv.width/r.width,sy=cv.height/r.height;
        var cx3=(e.clientX-r.left)*sx,cy3=(e.clientY-r.top)*sy;
        var dx=Math.round(cv._xMn2+(cx3-cv._pad2)/cv._cs2);
        var dy=Math.round(cv._yMx2-(cy3-cv._pad2)/cv._csy2);
        var dd=cv._dl2&&cv._dl2[dx+','+dy];
        if(!dd){var _t2=document.getElementById('wm-tip');if(_t2)_t2.style.display='none';return;}
        var t=document.getElementById('wm-tip');
        if(!t){t=document.createElement('div');t.id='wm-tip';
          t.style.cssText='position:fixed;background:rgba(20,20,40,0.92);color:#fff;font-size:11px;padding:4px 8px;border-radius:4px;pointer-events:none;z-index:30001;box-shadow:0 2px 6px rgba(0,0,0,.4)';
          document.body.appendChild(t);}
        var upmLine='';if(DATA.upmCols&&DATA.upmCols.length&&dd.uv){var _ue=DATA.upmCols[0],_ut=_ue&&_ue.divisor,_uv=parseFloat(dd.uv.split('|')[0]);if(!isNaN(_uv))upmLine=' UPM:'+(_ut?(_uv).toFixed(2)+'%':_uv);}
        t.textContent='('+dd.x+','+dd.y+') '+(dd.ib!==null?'IB'+dd.ib:'no IB')+(dd.fb!==null?' FB'+dd.fb:'')+upmLine;
        var m=12,left=e.clientX+m,top=e.clientY-8;
        if(left+(t.offsetWidth||100)>window.innerWidth)left=e.clientX-(t.offsetWidth||100)-m;
        t.style.left=left+'px';t.style.top=top+'px';t.style.display='block';
      });
      cv.addEventListener('mouseleave',function(){
        var t=document.getElementById('wm-tip');if(t)t.style.display='none';
      });
    }
    container.innerHTML='';container.appendChild(cv);
  } else {
    var clipIdT='wmc-'+ri;
    var clipDefT='<defs><clipPath id="'+clipIdT+'"><ellipse cx="'+cxE+'" cy="'+cyE+'" rx="'+rxE+'" ry="'+ryE+'"/></clipPath></defs>';
    var borderCircleT='<ellipse cx="'+cxE+'" cy="'+cyE+'" rx="'+rxE+'" ry="'+ryE+'" fill="none" stroke="#bdc3c7" stroke-width="1"/>';
    var rectsT=[];
    dies.forEach(function(d){
      var x=d[0],y=d[1],ib=d[2];
      if(x===null||x===undefined)return;
      var px=(pad+(x-xMin)*cs).toFixed(1),py=(pad+(yMax-y)*csy).toFixed(1);
      var fill=_wmIbColor(ib);
      var ibKey=ib!==null&&ib!==undefined?ib:null;
      var binOn=(_wmBinChecked===null||_wmBinChecked.has(ibKey));
      var opacity=binOn?'1':'0.08';
      var isFail=_wmIsFail(ib);
      if(isFail&&ibKey!==null&&binOn&&DATA.hasReticle&&DATA.retMap){var _rt2=DATA.retMap[x+','+y];if(_rt2)failShotIdx3.add(_rt2[2]);}
      var clickable=isFail&&ibKey!==null&&binOn;
      rectsT.push('<rect x="'+px+'" y="'+py+'" width="'+(cs*0.9).toFixed(1)+'" height="'+(csy*0.9).toFixed(1)+'" fill="'+fill+'" opacity="'+opacity+'" data-ib="'+(ibKey!==null?ibKey:'')+'" data-tip="('+x+','+y+') '+(ibKey!==null?'IB'+ibKey:'no IB')+'" style="cursor:'+(clickable?'pointer':'default')+'"'+(isFail&&ibKey!==null&&cs>3&&binOn?' stroke="rgba(0,0,0,.25)" stroke-width="0.3"':'')+'/>');
    });
    var retOutlinesT='';
    if(DATA.hasReticle&&DATA.retShots&&DATA.retShots.length){
      var _hlST=null;
      if(_wmRetChecked&&_wmRetChecked.size>0){var _sT=_wmGetSiteShots();_hlST=new Set();_wmRetChecked.forEach(function(sk){if(_sT[sk])_sT[sk].forEach(function(si){_hlST.add(si);});});}
      if(_hlST){
        DATA.retShots.forEach(function(shot,si){
          var sx=(pad+(shot[0]-xMin)*cs).toFixed(1),sy=(pad+(yMax-shot[3])*csy).toFixed(1),sw=((shot[2]-shot[0]+1)*cs).toFixed(1),sh=((shot[3]-shot[1]+1)*csy).toFixed(1);
          if(_hlST.has(si)){retOutlinesT+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#f39c12" stroke-width="1.5" opacity="0.95"/>';}
          else{retOutlinesT+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#ddd" stroke-width="0.5" opacity="0.2"/>';}
        });
      } else {
        DATA.retShots.forEach(function(shot,si){
          var sx=(pad+(shot[0]-xMin)*cs).toFixed(1),sy=(pad+(yMax-shot[3])*csy).toFixed(1),sw=((shot[2]-shot[0]+1)*cs).toFixed(1),sh=((shot[3]-shot[1]+1)*csy).toFixed(1);
          retOutlinesT+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#2471a3" stroke-width="0.5" opacity="0.3"/>';
        });
        DATA.retShots.forEach(function(shot,si){
          if(!failShotIdx3.has(si))return;
          var sx=(pad+(shot[0]-xMin)*cs).toFixed(1),sy=(pad+(yMax-shot[3])*csy).toFixed(1),sw=((shot[2]-shot[0]+1)*cs).toFixed(1),sh=((shot[3]-shot[1]+1)*csy).toFixed(1);
          retOutlinesT+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#c0392b" stroke-width="1.2" opacity="0.9"/>';
        });
      }
    }
    container.innerHTML='<svg width="'+W+'" height="'+H+'" style="display:block">'+clipDefT+'<g clip-path="url(#'+clipIdT+')">'+rectsT.join('')+retOutlinesT+'</g>'+borderCircleT+'</svg>';
  }
  _wmRenderedRis.add(ri);
}

/* ============================================================
   Wafer Detail Popup  (_wmd*)
   Shows: IB distribution bar, HW breakout, UPM heatmap
   ============================================================ */
function _wmdOpen(ri){
  _wmdOpen=true;_wmdRi=ri;
  var ov=document.getElementById('wmd-overlay');if(ov)ov.classList.add('open');
  var box=document.getElementById('wmd-box');
  if(box){
    var W=Math.min(window.innerWidth*0.88,1100),H=window.innerHeight*0.84;
    box.style.width=W+'px';box.style.height=H+'px';
    box.style.left=Math.max(0,(window.innerWidth-W)/2)+'px';
    box.style.top='5%';
  }
  _wmdHeatMode='fb';
  _wmdRender(ri);
  /* init drag once */
  var drag=document.getElementById('wmd-drag');
  if(drag&&!drag._wmdDrag){
    drag._wmdDrag=true;
    drag.addEventListener('mousedown',function(e){
      if(e.target.tagName==='BUTTON')return;
      e.preventDefault();_wmdDragging=true;
      var r=box.getBoundingClientRect();_wmdDX=e.clientX-r.left;_wmdDY=e.clientY-r.top;
      var cap=document.getElementById('wmd-cap');
      if(!cap){cap=document.createElement('div');cap.id='wmd-cap';
        cap.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;z-index:24999;cursor:move';
        document.body.appendChild(cap);}
      cap.style.display='block';
    });
    document.addEventListener('mousemove',function(e){
      if(!_wmdDragging)return;
      var bx=document.getElementById('wmd-box');if(!bx)return;
      var nx=e.clientX-_wmdDX,ny=e.clientY-_wmdDY;
      bx.style.left=Math.max(0,Math.min(window.innerWidth-bx.offsetWidth,nx))+'px';
      bx.style.top=Math.max(0,Math.min(window.innerHeight-bx.offsetHeight,ny))+'px';
    });
    document.addEventListener('mouseup',function(){
      if(!_wmdDragging)return;_wmdDragging=false;
      var cap=document.getElementById('wmd-cap');if(cap)cap.style.display='none';
    });
  }
}
function _wmdClose(){
  _wmdOpen=false;
  var ov=document.getElementById('wmd-overlay');if(ov)ov.classList.remove('open');
}
function _wmdRender(ri){
  var row=DATA.rows[ri];
  if(!row){return;}
  var lbl=(row.lot||'')+' W'+(row.wafer||'');
  var title=document.getElementById('wmd-title');
  if(title)title.textContent='\u26fa Wafer Detail \u2014 '+lbl;
  /* default to Pattern tab (IB-colored wafer map matching panel) */
  _wmdTab='pattern';
  var uPane=document.getElementById('wmd-upm-pane');var pPane=document.getElementById('wmd-pat-pane');
  var uBtn=document.getElementById('wmd-tab-upm');var pBtn=document.getElementById('wmd-tab-pat');
  if(uPane)uPane.style.display='none';if(pPane)pPane.style.display='flex';
  if(uBtn)uBtn.classList.remove('on');if(pBtn)pBtn.classList.add('on');
  var patBody=document.getElementById('wmd-pattern-body');if(patBody)patBody.innerHTML='';

  /* ---- IB distribution ---- */
  var ibBody=document.getElementById('wmd-ib-body');
  if(ibBody){
    var ibCounts={};var total=0;
    if(row.binCounts){Object.keys(row.binCounts).forEach(function(k){ibCounts[k]=row.binCounts[k];total+=row.binCounts[k];});}
    else if(row.dies){row.dies.forEach(function(d){var ib=d[2];if(ib===null||ib===undefined)return;ibCounts[String(ib)]=(ibCounts[String(ib)]||0)+1;total++;});}
    var ibH='';
    var ibKeys=Object.keys(ibCounts).sort(function(a,b){return +a- +b;});
    ibKeys.forEach(function(ibk){
      var cnt=ibCounts[ibk],pct=total?cnt/total*100:0;
      var col=_wmIbColor(+ibk);
      var barW=Math.round(pct);
      var analyzeBtn='<button title="Analyze IB'+ibk+' for this wafer" onclick="IC._wmdShowFbForWafer(\''+ibk+'\','+ri+')" '
        +'style="background:none;border:1px solid #c8d4e0;border-radius:3px;cursor:pointer;font-size:10px;padding:0 3px;line-height:14px;color:#1a5276;flex-shrink:0">&#128300;</button>';
      ibH+='<div style="display:flex;align-items:center;gap:4px;font-size:11px;margin-bottom:3px">'
        +'<span style="width:28px;font-weight:bold;color:'+col+'">IB'+ibk+'</span>'
        +'<div style="flex:1;background:#e8e8e8;border-radius:3px;height:10px"><div style="height:10px;border-radius:3px;background:'+col+';width:'+barW+'%"></div></div>'
        +'<span style="width:32px;font-size:10px">'+pct.toFixed(1)+'%</span>'
        +'<span style="width:36px;font-size:10px;color:#888">'+cnt+'</span>'
        +analyzeBtn
        +'</div>';
    });
    ibBody.innerHTML=ibH||'<span style="color:#aaa">No IB data</span>';
  }

  /* ---- HW breakout ---- */
  var hwBody=document.getElementById('wmd-hw-body');
  if(hwBody){
    var hwCounts={};var hwNames=DATA.hwNames||{};
    if(row.dies){
      row.dies.forEach(function(d){
        var hw=d[4];if(hw===null||hw===undefined)return;
        hwCounts[String(hw)]=(hwCounts[String(hw)]||0)+1;
      });
    }
    var hwKeys=Object.keys(hwCounts).sort(function(a,b){return hwCounts[b]-hwCounts[a];});
    var hwH='<table class="wmd-hw-t"><thead><tr><th>HW</th><th>Name</th><th>n</th></tr></thead><tbody>';
    hwKeys.slice(0,15).forEach(function(k){
      hwH+='<tr><td style="font-weight:bold">'+_wmEsc(k)+'</td><td style="font-size:10px;color:#555">'+_wmEsc(hwNames[k]||'')+'</td><td>'+hwCounts[k]+'</td></tr>';
    });
    hwH+='</tbody></table>';
    hwBody.innerHTML=hwKeys.length?hwH:'<span style="color:#aaa;font-size:11px">No HW data</span>';
  }

  /* ---- Heatmap (FB or UPM mode) ---- */
  var upmBody=document.getElementById('wmd-upm-body');
  var upmSel=document.getElementById('wmd-upm-sel');
  if(upmBody){
    var uCols=DATA.upmCols||[];
    var dies=row.dies;
    if(!dies||!dies.length){upmBody.innerHTML='<span style="color:#aaa">No die data</span>';return;}
    /* UPM col selector buttons — only in UPM mode */
    var selH='';
    if(_wmdHeatMode==='upm'){
      uCols.forEach(function(uc,ui){
        selH+='<button class="wm-tbtn'+(_wmdUpmIdx===ui?' on':'')+'" onclick="IC._wmdUpmSel('+ri+','+ui+')" style="font-size:10px;padding:1px 6px">'+_wmEsc(uc.label||'U'+ui)+'</button>';
      });
    }
    if(upmSel)upmSel.innerHTML=selH;
    /* sync checkbox state */
    var _hmChk=document.getElementById('wmd-hm-chk');
    if(_hmChk)_hmChk.checked=(_wmdHeatMode==='upm');
    var upmIdx=_wmdUpmIdx;
    var xs=[],ys=[];
    dies.forEach(function(d){if(d[0]!==null)xs.push(d[0]);});
    dies.forEach(function(d){if(d[1]!==null)ys.push(d[1]);});
    if(!xs.length){upmBody.innerHTML='<span style="color:#aaa">No die coords</span>';return;}
    var xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs);
    var yMin=Math.min.apply(null,ys),yMax=Math.max.apply(null,ys);
    var xCnt=xMax-xMin+1;
    var pad=4,FIXED_W=Math.round(320*_wmdZoom);
    var cs=Math.max(2,(FIXED_W-pad*2)/xCnt);
    var xSpan=xMax-xMin,ySpan=yMax-yMin;
    var csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;
    var W=FIXED_W,H=Math.round((yMax-yMin+1)*csy+pad*2);
    var rects=[],svgH='';
    if(_wmdHeatMode==='upm'){
      if(!uCols.length){upmBody.innerHTML='<span style="color:#aaa">No UPM data</span>';return;}
      var ustart=DATA.upmStart||5;
      var allVals=[];
      dies.forEach(function(d){var v=d[ustart+upmIdx];if(v!==null&&v!==undefined)allVals.push(v);});
      var lo=allVals.length?Math.min.apply(null,allVals):0;
      var hi=allVals.length?Math.max.apply(null,allVals):100;
      var rng=(hi-lo)||1;
      var isMHz=(hi>200);
      var fmt=function(v){return isMHz?Math.round(v)+'MHz':v.toFixed(2)+'%';};
      dies.forEach(function(d){
        var x=d[0],y=d[1],ib=d[2],fb=d[3],hw=d[4];
        var uv=d[ustart+upmIdx];
        if(x===null||x===undefined)return;
        var px=(pad+(x-xMin)*cs).toFixed(1),py=(pad+(yMax-y)*csy).toFixed(1);
        var t=(uv!==null&&uv!==undefined)?Math.max(0,Math.min(1,(uv-lo)/rng)):null;
        var fill=(t!==null)?_upmColor(t):'#bdc3c7';
        var fbActive;
        if(_recovOpen&&_fbModalIb&&String(ib)===String(_fbModalIb)&&RECOV_DIE_GRPS){fbActive=(_recovGrpChecked.size>0)&&_dieGrpActive(row.lot+'|'+row.wafer,x,y);}
        else{fbActive=(!_fbModalIb)||(ib===null)||(String(ib)!==String(_fbModalIb))||(fb===null)||(_fbChecked.size===0)||_fbChecked.has(String(fb));}
        var ibs=ib!==null?'IB'+ib:'';
        var uvs=uv!==null?fmt(uv):'no UPM';
        rects.push('<rect x="'+px+'" y="'+py+'" width="'+(cs*0.9).toFixed(1)+'" height="'+(csy*0.9).toFixed(1)+'" fill="'+fill+'" opacity="'+(fbActive?'1':'0.12')+'" data-tip="('+x+','+y+') '+uvs+' '+ibs+'"/>');
      });
      var lgH='<defs><linearGradient id="wmd-lg" x1="0" x2="1" y1="0" y2="0">'
        +'<stop offset="0%" stop-color="#dc0000"/><stop offset="50%" stop-color="#ffffff"/><stop offset="100%" stop-color="#0032dc"/>'
        +'</linearGradient></defs>'
        +'<rect x="0" y="0" width="'+W+'" height="14" fill="url(#wmd-lg)"/>'
        +'<text x="2" y="11" font-size="9" font-family="Arial" fill="#fff">'+fmt(lo)+'</text>'
        +'<text x="'+(W-2)+'" y="11" font-size="9" font-family="Arial" fill="#fff" text-anchor="end">'+fmt(hi)+'</text>';
      svgH='<svg width="'+W+'" height="'+(H+14)+'" style="display:block"><g transform="translate(0,14)">'+rects.join('')+'</g>'+lgH+'</svg>';
    } else {
      /* FB coloring mode */
      var _hmPal=['#e74c3c','#e67e22','#f39c12','#2ecc71','#1abc9c','#3498db','#9b59b6','#e91e63','#00bcd4','#8bc34a','#ff9800','#795548'];
      var fbDesc2=DATA.fbDescriptions||{};
      var fbSeen=[];
      dies.forEach(function(d){var f=d[3];if(f!==null&&f!==undefined&&fbSeen.indexOf(String(f))<0)fbSeen.push(String(f));});
      fbSeen.sort(function(a,b){return +a-+b;});
      var fbColorMap={};
      fbSeen.forEach(function(f,ii){fbColorMap[f]=_hmPal[ii%_hmPal.length];});
      dies.forEach(function(d){
        var x=d[0],y=d[1],ib=d[2],fb=d[3];
        if(x===null||x===undefined)return;
        var px=(pad+(x-xMin)*cs).toFixed(1),py=(pad+(yMax-y)*csy).toFixed(1);
        var fill=fb!==null&&fb!==undefined?fbColorMap[String(fb)]||'#95a5a6':'#bdc3c7';
        var fbActive;
        if(_recovOpen&&_fbModalIb&&String(ib)===String(_fbModalIb)&&RECOV_DIE_GRPS){fbActive=(_recovGrpChecked.size>0)&&_dieGrpActive(row.lot+'|'+row.wafer,x,y);}
        else{fbActive=(!_fbModalIb)||(ib===null)||(String(ib)!==String(_fbModalIb))||(fb===null)||(_fbChecked.size===0)||_fbChecked.has(String(fb));}
        var ibs=ib!==null?'IB'+ib:'';
        var fbs=fb!==null?'FB'+fb:'';
        rects.push('<rect x="'+px+'" y="'+py+'" width="'+(cs*0.9).toFixed(1)+'" height="'+(csy*0.9).toFixed(1)+'" fill="'+fill+'" opacity="'+(fbActive?'1':'0.12')+'" data-tip="('+x+','+y+') '+fbs+' '+ibs+'"/>');
      });
      var legHtml='<div style="display:flex;flex-wrap:wrap;gap:3px;margin-top:4px;font-size:10px">';
      fbSeen.forEach(function(f){
        var col=fbColorMap[f]||'#95a5a6';
        var desc=(fbDesc2[f]&&fbDesc2[f].desc)?fbDesc2[f].desc:'';
        legHtml+='<span style="display:inline-flex;align-items:center;gap:2px">'
          +'<svg width="9" height="9" style="flex-shrink:0"><rect width="9" height="9" fill="'+col+'"/></svg>'
          +'FB'+_wmEsc(f)+(desc?'\u00a0'+_wmEsc(desc.substring(0,12)):'')+'</span>';
      });
      legHtml+='</div>';
      svgH='<svg width="'+W+'" height="'+H+'" style="display:block">'+rects.join('')+'</svg>'+legHtml;
    }
    upmBody.innerHTML=svgH;
    _wmdSetupHover(upmBody);
  }
  /* if pattern tab is active (default), render the pattern view */
  if(_wmdTab==='pattern')_wmdRenderPattern(ri);
}
var _wmdUpmIdx=0;
var _wmdHeatMode='fb';
var _wmdTab='upm';
/* contrast helper: black text on light fill, white on dark */
function _wmContrast(hex){
  if(!hex||hex[0]!=='#')return'#fff';
  var h=hex.length===4?hex[1]+hex[1]+hex[2]+hex[2]+hex[3]+hex[3]:hex.slice(1);
  var r=parseInt(h.slice(0,2),16)/255,g=parseInt(h.slice(2,4),16)/255,b=parseInt(h.slice(4,6),16)/255;
  return(0.299*r+0.587*g+0.114*b)>0.55?'#000':'#fff';
}
function _wmdRiVal(){return _wmdRi;}
function _wmdTabSel(tab,ri){
  _wmdTab=tab;
  var uBtn=document.getElementById('wmd-tab-upm');
  var pBtn=document.getElementById('wmd-tab-pat');
  var uPane=document.getElementById('wmd-upm-pane');
  var pPane=document.getElementById('wmd-pat-pane');
  if(uBtn){uBtn.classList.toggle('on',tab==='upm');}
  if(pBtn){pBtn.classList.toggle('on',tab==='pattern');}
  if(uPane){uPane.style.display=tab==='upm'?'flex':'none';}
  if(pPane){pPane.style.display=tab==='pattern'?'flex':'none';}
  if(tab==='pattern'){_wmdRenderPattern(ri);}
}
function _wmdUpmSel(ri,ui){_wmdUpmIdx=ui;_wmdRender(ri);}
function _wmdHeatModeSel(mode,ri){
  _wmdHeatMode=mode;
  var _hmChk=document.getElementById('wmd-hm-chk');
  if(_hmChk)_hmChk.checked=(mode==='upm');
  _wmdRender(ri);
}
function _wmdSetupHover(el){
  if(!el||el._wmdHov)return;
  el._wmdHov=true;
  el.addEventListener('mousemove',function(e){
    var t=e.target;
    if(t&&t.tagName==='rect'&&t.dataset&&t.dataset.tip){
      var tip=document.getElementById('wm-tip');
      if(!tip){tip=document.createElement('div');tip.id='wm-tip';
        tip.style.cssText='position:fixed;background:rgba(20,20,40,0.92);color:#fff;font-size:11px;padding:4px 8px;border-radius:4px;pointer-events:none;z-index:30001;box-shadow:0 2px 6px rgba(0,0,0,.4)';
        document.body.appendChild(tip);}
      tip.textContent=t.dataset.tip;
      tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY-8)+'px';tip.style.display='block';
    }else{var tip2=document.getElementById('wm-tip');if(tip2)tip2.style.display='none';}
  });
  el.addEventListener('mouseleave',function(){var tip=document.getElementById('wm-tip');if(tip)tip.style.display='none';});
}

function _wmdRenderPattern(ri){
  var patBody=document.getElementById('wmd-pattern-body');
  if(!patBody)return;
  var row=DATA.rows[ri];
  if(!row||!row.dies||!row.dies.length){patBody.innerHTML='<span style="color:#aaa;font-size:11px">No die-level data</span>';return;}
  var dies=row.dies;
  var xs=[],ys=[];
  dies.forEach(function(d){if(d[0]!==null&&d[0]!==undefined){xs.push(d[0]);ys.push(d[1]);}});
  if(!xs.length){patBody.innerHTML='<span style="color:#aaa">No coordinates</span>';return;}
  var xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs);
  var yMin=Math.min.apply(null,ys),yMax=Math.max.apply(null,ys);
  var xCnt=xMax-xMin+1;
  var FIXED_W=300,pad=4;
  var cs=Math.max(2,(FIXED_W-pad*2)/xCnt);
  var xSpan=xMax-xMin,ySpan=yMax-yMin;
  var csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;
  var W=FIXED_W,H=Math.round((yMax-yMin+1)*csy+pad*2);
  var xCtr=(xMin+xMax)/2,yCtr=(yMin+yMax)/2;
  var xRad=(xMax-xMin)/2||1,yRad=(yMax-yMin)/2||1;
  var failXn=[],failYn=[],failActX=[],failActY=[];
  var totalDies=0,failDies=0;
  var failShotIdx=new Set();
  var rects=[];
  dies.forEach(function(d){
    var x=d[0],y=d[1],ib=d[2];
    if(x===null||x===undefined)return;
    totalDies++;
    var px=(pad+(x-xMin)*cs).toFixed(1),py=(pad+(yMax-y)*csy).toFixed(1);
    var fill=_wmIbColor(ib);
    var isFail=_wmIsFail(ib);
    if(isFail&&ib!==null&&ib!==undefined){
      var xn=(x-xCtr)/xRad,yn=(y-yCtr)/yRad;
      failXn.push(xn);failYn.push(yn);failActX.push(x);failActY.push(y);failDies++;
      if(DATA.hasReticle&&DATA.retMap){var _ri=DATA.retMap[x+','+y];if(_ri)failShotIdx.add(_ri[2]);}
    }
    var _binOn=(_wmBinChecked===null||_wmBinChecked.has(ib));
    rects.push('<rect x="'+px+'" y="'+py+'" width="'+(cs*0.9).toFixed(1)+'" height="'+(csy*0.9).toFixed(1)+'" fill="'+fill+'" opacity="'+(_binOn?'1':'0.08')+'" data-tip="('+x+','+y+') '+(ib!==null&&ib!==undefined?'IB'+ib:'no IB')+'"/>');
  });
  var sc=_wmScorePattern(failXn,failYn);
  var primary=_wmPrimary(sc);
  var pCol=_pColors[primary]||'#555';
  var failPct=totalDies>0?(failDies/totalDies*100).toFixed(1)+'%':'—';
  var _ecx=(pad+(xCtr-xMin)*cs+cs*0.45).toFixed(1);
  var _ecy=(pad+(yMax-yCtr)*csy+csy*0.45).toFixed(1);
  var _erx=(xRad*cs+cs*0.5).toFixed(1);
  var _ery=(yRad*csy+csy*0.5).toFixed(1);
  var clipId='wmpat-'+ri;
  var clipDef='<defs><clipPath id="'+clipId+'"><ellipse cx="'+_ecx+'" cy="'+_ecy+'" rx="'+_erx+'" ry="'+_ery+'"/></clipPath></defs>';
  var borderCircle='<ellipse cx="'+_ecx+'" cy="'+_ecy+'" rx="'+_erx+'" ry="'+_ery+'" fill="none" stroke="#bdc3c7" stroke-width="1"/>';
  var retOutlines='';
  if(DATA.hasReticle&&DATA.retShots&&DATA.retShots.length){
    var _hlShots=null;
    if(_wmRetChecked&&_wmRetChecked.size>0){
      var _s2s=_wmGetSiteShots();_hlShots=new Set();
      _wmRetChecked.forEach(function(sk){if(_s2s[sk])_s2s[sk].forEach(function(si){_hlShots.add(si);});});
    }
    if(_hlShots){
      DATA.retShots.forEach(function(shot,si){
        var sx=(pad+(shot[0]-xMin)*cs).toFixed(1),sy=(pad+(yMax-shot[3])*csy).toFixed(1);
        var sw=((shot[2]-shot[0]+1)*cs).toFixed(1),sh=((shot[3]-shot[1]+1)*csy).toFixed(1);
        if(_hlShots.has(si)){retOutlines+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#f39c12" stroke-width="2.5" opacity="0.95"/>';}
        else{retOutlines+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#ddd" stroke-width="0.5" opacity="0.2"/>';}
      });
    } else {
      DATA.retShots.forEach(function(shot,si){
        var sx=(pad+(shot[0]-xMin)*cs).toFixed(1),sy=(pad+(yMax-shot[3])*csy).toFixed(1);
        var sw=((shot[2]-shot[0]+1)*cs).toFixed(1),sh=((shot[3]-shot[1]+1)*csy).toFixed(1);
        retOutlines+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#2471a3" stroke-width="0.8" opacity="0.35"/>';
      });
      DATA.retShots.forEach(function(shot,si){
        if(!failShotIdx.has(si))return;
        var sx=(pad+(shot[0]-xMin)*cs).toFixed(1),sy=(pad+(yMax-shot[3])*csy).toFixed(1);
        var sw=((shot[2]-shot[0]+1)*cs).toFixed(1),sh=((shot[3]-shot[1]+1)*csy).toFixed(1);
        retOutlines+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#c0392b" stroke-width="2" opacity="0.9"/>';
      });
    }
  }
  var svgH='<svg width="'+W+'" height="'+H+'" style="display:block">'+clipDef+'<g clip-path="url(#'+clipId+')">'+rects.join('')+retOutlines+'</g>'+borderCircle+'</svg>';
  /* score bars */
  var scoreRows=[['center','Center','#c0392b'],['edge','Edge','#e67e22'],['donut','Donut','#8e44ad'],['systematic','Systematic','#2471a3']];
  if(DATA.hasReticle)scoreRows.push(['reticle','Reticle','#1f618d']);
  scoreRows.push(['random','Random','#27ae60']);
  var scH='<div style="margin-top:8px">'
    +'<div style="font-size:13px;font-weight:bold;color:'+pCol+';margin-bottom:8px;padding:4px 8px;border-radius:4px;background:'+pCol+'22">'+primary
    +'<span style="font-size:10px;color:#666;font-weight:normal;margin-left:8px">fail: '+failPct+' (n='+failDies+')</span></div>';
  scoreRows.forEach(function(kv){
    var k=kv[0],label=kv[1],col=kv[2];
    var v=sc[k]||0;
    var w=Math.round(v*100);
    scH+='<div style="display:flex;align-items:center;gap:6px;margin-bottom:5px;font-size:11px">'
      +'<span style="width:76px;text-align:right;font-weight:bold;color:'+col+'">'+label+'</span>'
      +'<div style="flex:1;background:#e8e8e8;border-radius:3px;height:11px"><div style="height:11px;border-radius:3px;background:'+col+';width:'+w+'%"></div></div>'
      +'<span style="width:36px;font-size:10px;text-align:right">'+w+'%</span>'
      +'</div>';
  });
  scH+='</div>';
  /* Build criteria badge checkboxes (Show Only, same as main panel hover badges) */
  var _cmBinsZ=_wmGetCriteriaMissBins(row);
  var badgeHtmlZ='';
  if(_cmBinsZ.length){
    badgeHtmlZ='<div style="display:flex;flex-wrap:wrap;gap:3px;margin-top:6px;align-items:center"><span style="font-size:10px;color:#555;margin-right:2px">Show Only:</span>';
    _cmBinsZ.forEach(function(b){
      var col=DATA.binColors[b]||'#e74c3c';
      var pinned=_wmBadgePinned.has(+b);
      badgeHtmlZ+='<label style="display:inline-flex;align-items:center;gap:2px;font-size:10px;cursor:pointer;border:1px solid '+col+';border-radius:3px;padding:1px 4px;background:'+(pinned?col+'33':'#fff')+';color:#333;white-space:nowrap" title="Show Only IB'+b+'">'
        +'<input type="checkbox" '+(pinned?'checked':'')+' onchange="IC._wmBadgeToggle('+(+b)+',this.checked)" style="cursor:pointer;margin:0;width:11px;height:11px">'
        +'IB'+b+'</label>';
    });
    badgeHtmlZ+='<button onclick="IC._wmBadgeClearAll()" style="font-size:9px;padding:1px 5px;border-radius:3px;border:1px solid #aaa;background:#f5f5f5;cursor:pointer;margin-left:4px">Reset</button></div>';
  }
  patBody.innerHTML='<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start">'
    +'<div>'+svgH+badgeHtmlZ+'</div>'
    +'<div style="flex:1;min-width:160px">'+scH+'</div>'
    +'</div>';
  _wmdSetupHover(patBody);
}

var _wmInlineOpen=true;
function _wmInlineToggle(){
  _wmInlineOpen=!_wmInlineOpen;
  var body=document.getElementById('wm-inline-body');
  var tog=document.getElementById('wm-inline-tog');
  if(body)body.style.display=_wmInlineOpen?'block':'none';
  if(tog)tog.innerHTML=_wmInlineOpen?'&#9650;':'&#9660;';
}
function _wmRenderInline(){
  var cont=document.getElementById('wm-maps-inline');
  if(!cont)return;
  if(!DATA||!DATA.rows){cont.innerHTML='<span style="color:#999;font-size:12px">No data</span>';return;}
  var vis=[];
  DATA.rows.forEach(function(row,i){if(sR.has(i)&&row.dies&&row.dies.length)vis.push(i);});
  if(!vis.length){cont.innerHTML='<span style="color:#aaa;font-size:12px">No wafers selected.</span>';return;}
  var FIXED_W=140,pad=2;
  var html='';
  vis.forEach(function(ri){
    var row=DATA.rows[ri];
    var dies=row.dies;
    var xs=[],ys=[];
    dies.forEach(function(d){if(d[0]!==null&&d[0]!==undefined){xs.push(d[0]);ys.push(d[1]);}});
    if(!xs.length)return;
    var xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs);
    var yMin=Math.min.apply(null,ys),yMax=Math.max.apply(null,ys);
    var xCnt=xMax-xMin+1,yCnt=yMax-yMin+1;
    var cs=Math.max(1,(FIXED_W-pad*2)/xCnt);
    var xSpan=xMax-xMin,ySpan=yMax-yMin;
    var csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;
    var W=FIXED_W,H=Math.round(yCnt*csy+pad*2);
    var xCtr=(xMin+xMax)/2,yCtr=(yMin+yMax)/2;
    var xRad=(xMax-xMin)/2||1,yRad=(yMax-yMin)/2||1;
    var failXn=[],failYn=[],failActX=[],failActY=[],totalDies=0,failDies=0;
    var failShotIdx=new Set();
    var rects=[];
    dies.forEach(function(d){
      var x=d[0],y=d[1],ib=d[2];
      if(x===null||x===undefined)return;
      totalDies++;
      var px=(pad+(x-xMin)*cs).toFixed(1),py=(pad+(yMax-y)*csy).toFixed(1);
      var fill=_wmIbColor(ib);
      var xn=(x-xCtr)/xRad,yn=(y-yCtr)/yRad;
      var isFail=_wmIsFail(ib);
      var ibKey=ib!==null&&ib!==undefined?ib:null;
      var binOn=(_wmBinChecked===null||_wmBinChecked.has(ibKey));
      var opacity=binOn?'1':'0.08';
      if(isFail&&ibKey!==null&&binOn){
        failXn.push(xn);failYn.push(yn);failActX.push(x);failActY.push(y);failDies++;
        if(DATA.hasReticle&&DATA.retMap){var _ri=DATA.retMap[x+','+y];if(_ri)failShotIdx.add(_ri[2]);}
      }
      rects.push('<rect x="'+px+'" y="'+py+'" width="'+(cs*0.9).toFixed(1)+'"'
        +' height="'+(csy*0.9).toFixed(1)+'" fill="'+fill+'" opacity="'+opacity+'"/>');
    });
    var sc=_wmScorePattern(failXn,failYn);
    var primary=_wmPrimary(sc);
    var pCol=_pColors[primary]||'#555';
    var cx=(pad+(xCtr-xMin)*cs+cs*0.45).toFixed(1);
    var cy=(pad+(yMax-yCtr)*csy+csy*0.45).toFixed(1);
    var rx=(xRad*cs+cs*0.5).toFixed(1);
    var ry=(yRad*csy+csy*0.5).toFixed(1);
    var clipId='wmi-'+ri;
    var clipDef='<defs><clipPath id="'+clipId+'"><ellipse cx="'+cx+'" cy="'+cy+'" rx="'+rx+'" ry="'+ry+'"/></clipPath></defs>';
    var borderCircle='<ellipse cx="'+cx+'" cy="'+cy+'" rx="'+rx+'" ry="'+ry+'" fill="none" stroke="#bdc3c7" stroke-width="1"/>';
    var retOutlines='';
    if(DATA.hasReticle&&DATA.retShots&&DATA.retShots.length){
      var _hlShots=null;
      if(_wmRetChecked&&_wmRetChecked.size>0){
        var _s2s=_wmGetSiteShots();_hlShots=new Set();
        _wmRetChecked.forEach(function(sk){if(_s2s[sk])_s2s[sk].forEach(function(si){_hlShots.add(si);});});
      }
      if(_hlShots){
        DATA.retShots.forEach(function(shot,si){
          var sx=(pad+(shot[0]-xMin)*cs).toFixed(1),sy=(pad+(yMax-shot[3])*csy).toFixed(1);
          var sw=((shot[2]-shot[0]+1)*cs).toFixed(1),sh=((shot[3]-shot[1]+1)*csy).toFixed(1);
          if(_hlShots.has(si)){retOutlines+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#f39c12" stroke-width="1.5" opacity="0.95"/>';}
          else{retOutlines+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#ddd" stroke-width="0.5" opacity="0.2"/>';}
        });
      } else {
        DATA.retShots.forEach(function(shot,si){
          var sx=(pad+(shot[0]-xMin)*cs).toFixed(1);
          var sy=(pad+(yMax-shot[3])*csy).toFixed(1);
          var sw=((shot[2]-shot[0]+1)*cs).toFixed(1);
          var sh=((shot[3]-shot[1]+1)*csy).toFixed(1);
          retOutlines+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#2471a3" stroke-width="0.5" opacity="0.3"/>';
        });
        DATA.retShots.forEach(function(shot,si){
          if(!failShotIdx.has(si))return;
          var sx=(pad+(shot[0]-xMin)*cs).toFixed(1);
          var sy=(pad+(yMax-shot[3])*csy).toFixed(1);
          var sw=((shot[2]-shot[0]+1)*cs).toFixed(1);
          var sh=((shot[3]-shot[1]+1)*csy).toFixed(1);
          retOutlines+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#c0392b" stroke-width="1.2" opacity="0.9"/>';
        });
      }
    }
    var lbl=_wmEsc((row.lot||'')+' W'+(row.wafer||''));
    var failPct=totalDies>0?(failDies/totalDies*100).toFixed(1)+'%':'—';
    html+='<div class="wm-inline-card" onclick="IC._wmdOpen('+ri+')" title="Open wafer detail: '+lbl+'">'
      +'<div class="wm-inline-lbl">'+lbl+'</div>'
      +'<svg width="'+W+'" height="'+H+'" style="display:block">'+clipDef+'<g clip-path="url(#'+clipId+')">'+rects.join('')+retOutlines+'</g>'+borderCircle+'</svg>'
      +'<div class="wm-inline-tag" style="color:'+pCol+'">'+primary+' <span style="font-size:9px;color:#888;font-weight:normal">'+failPct+'</span></div>'
      +'</div>';
  });
  cont.innerHTML=html;
  _wmdSetupHover(cont);
}

function openWmModal(){
  _wmOpen=true;_wmSelRows=null;
  var ov=document.getElementById('wm-overlay');if(ov)ov.classList.add('open');
  var box=document.getElementById('wm-box');
  if(box){
    var W=Math.min(window.innerWidth*0.94,1400),H=window.innerHeight*0.72;
    box.style.width=W+'px';box.style.height=H+'px';
    box.style.left=Math.max(0,(window.innerWidth-W)/2)+'px';
    box.style.top='36px';
  }
  _wmRender();
  _wmInitDrag();
}
var _wmDX=0,_wmDY=0,_wmDragging=false;
function _wmInitDrag(){
  var drag=document.getElementById('wm-drag');
  if(!drag||drag._wmDrag)return;
  drag._wmDrag=true;
  drag.addEventListener('mousedown',function(e){
    if(e.target.tagName==='BUTTON')return;
    var box=document.getElementById('wm-box');if(!box)return;
    e.preventDefault();_wmDragging=true;
    var r=box.getBoundingClientRect();
    _wmDX=e.clientX-r.left;_wmDY=e.clientY-r.top;
    var cap=document.getElementById('wm-cap');
    if(!cap){cap=document.createElement('div');cap.id='wm-cap';
      cap.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;z-index:29999;cursor:move';
      document.body.appendChild(cap);}
    cap.style.display='block';
  });
  document.addEventListener('mousemove',function(e){
    if(!_wmDragging)return;
    var box=document.getElementById('wm-box');if(!box)return;
    var nx=e.clientX-_wmDX,ny=e.clientY-_wmDY;
    box.style.left=Math.max(0,Math.min(window.innerWidth-box.offsetWidth,nx))+'px';
    box.style.top=Math.max(0,Math.min(window.innerHeight-box.offsetHeight,ny))+'px';
  });
  document.addEventListener('mouseup',function(){
    if(!_wmDragging)return;_wmDragging=false;
    var cap=document.getElementById('wm-cap');if(cap)cap.style.display='none';
  });
}
function closeWmModal(){
  _wmOpen=false;
  var ov=document.getElementById('wm-overlay');if(ov)ov.classList.remove('open');
  var t=document.getElementById('wm-tip');if(t)t.style.display='none';
}
/* ---- DLCP Split in-page modal ---- */
function _dlcpEsc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function _dlcpComputeRows(){
  var uI=(DATA.upmStart||5)+_dlcpUi,res=[],hasDie=false;
  sR.forEach(function(ri){
    var row=DATA.rows[ri];if(!row||!row.dies||!row.dies.length)return;
    hasDie=true;var nA=0,nB=0,nC=0,nFF=0,nDF3=0,nDF4=0,uv=[];
    row.dies.forEach(function(d){
      var ib=d[2],up=d.length>uI?d[uI]:null;
      if(up!=null)uv.push(up);
      if((ib===1||ib===2)&&up!=null&&up>=_dlcpT)nA++;
      else if(ib!=null&&ib>=1&&ib<=4)nB++;
      else nC++;
      if(ib===1||ib===2)nFF++;
      else if(ib===3)nDF3++;
      else if(ib===4)nDF4++;
    });
    /* For median: sort sampled values if die count is large */
    var med=null;
    if(uv.length){
      if(uv.length>20000){
        /* reservoir-sample 20k for median estimation on large wafers */
        var samp=new Array(20000);
        for(var _si=0;_si<Math.min(uv.length,20000);_si++)samp[_si]=uv[_si];
        for(var _si=20000;_si<uv.length;_si++){var _j=Math.floor((_si+1)*Math.random());if(_j<20000)samp[_j]=uv[_si];}
        uv=samp;
      }
      uv.sort(function(a,b){return a-b;});
      var m=Math.floor(uv.length/2);med=uv.length%2===0?(uv[m-1]+uv[m])/2:uv[m];
    }
    res.push({lot:row.lot||'',wafer:row.wafer||'',mat:row.material||'',tot:row.dies.length,med:med,nA:nA,nB:nB,nC:nC,nFF:nFF,nDF34:nDF3+nDF4,nDF3:nDF3,nDF4:nDF4});
  });
  return{rows:res,noDies:!hasDie};
}
function _dlcpRenderSummary(tA,tB,tC,tN,medAll,tFF,tDF34,tDF3,tDF4){
  var sb=document.getElementById('dlcp-sumbox');if(!sb)return;
  if(!tN){sb.innerHTML='<span style="color:#999;font-size:12px">No data</span>';return;}
  var mTxt=medAll!=null?medAll.toFixed(2)+'%':'\u2014';
  var tIB14=tFF+(tDF3||0)+(tDF4||0); // IB1-4 total for denominator
  // Row 1: totals + HP/LP/Fail
  // Panel 1: Overview
  var row1='<div class="dlcp-sum-panel">'
    +'<div class="dlcp-sum-panel-ttl" style="background:#34495e">Overview</div>'
    +'<div class="dlcp-sumrow">'
    +'<div class="dlcp-sum-grp"><div class="dlcp-sum-lbl">Total Die</div><div class="dlcp-sum-val">'+tN+'</div></div>'
    +'<div class="dlcp-sum-grp"><div class="dlcp-sum-lbl">Med UPM%</div><div class="dlcp-sum-val">'+mTxt+'</div></div>'
    +'<div class="dlcp-sum-grp" style="border-color:#1a7a4a"><div class="dlcp-sum-lbl">FF+DF (IB1-4) Yield</div><div class="dlcp-sum-pct-big" style="color:#1a7a4a">'+(tN>0?((tIB14||0)/tN*100).toFixed(1):0)+'%</div><div class="dlcp-sum-sub">N='+(tIB14||0)+' \u00b7 of total</div></div>'
    +'<div class="dlcp-sum-grp" style="border-color:#1e8449"><div class="dlcp-sum-lbl">FF (IB 1,2) Yield</div><div class="dlcp-sum-pct-big" style="color:#1e8449">'+(tN>0?((tFF||0)/tN*100).toFixed(1):0)+'%</div><div class="dlcp-sum-sub">N='+(tFF||0)+' \u00b7 of total</div></div>'
    +'<div class="dlcp-sum-grp fail"><div class="dlcp-sum-lbl">Fail (IB&gt;4)</div><div class="dlcp-sum-pct-big" style="color:#c0392b">'+(tN>0?(tC/tN*100).toFixed(1):0)+'%</div><div class="dlcp-sum-sub">N='+tC+' \u00b7 of total</div></div>'
    +'</div></div>';
  // Panel 2: DLCP Split + FF/DF rows
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
}
var _dlcpFltVals={}; // {colIdx: filterText}
var _dlcpDdFlt={}; // {colIdx: Set of selected values} — null entry = all allowed
var _dlcpDdCurCol=-1;
var _dlcpDdPending=null; // temp checkbox state while dropdown is open
var _dlcpDesel=new Set(); // 'lot|wafer' keys that are DE-selected (default all selected)
function _dlcpRowKey(lot,wafer){return String(lot)+'|'+String(wafer);}
function _dlcpIsRowSel(key){return !_dlcpDesel.has(key);}
function dlcpSelAll(){_dlcpDesel.clear();_dlcpRenderTable();_dlcpRender();}
function dlcpSelNone(){
  var r=_dlcpComputeRows();
  r.rows.forEach(function(x){_dlcpDesel.add(_dlcpRowKey(x.lot,x.wafer));});
  _dlcpRenderTable();_dlcpRender();
}
function dlcpRowClick(key){
  if(_dlcpDesel.has(key))_dlcpDesel.delete(key);
  else _dlcpDesel.add(key);
  // update visual without full re-render
  var tb=document.getElementById('dlcp-tb');if(!tb)return;
  var rows=tb.getElementsByTagName('tr');
  for(var i=0;i<rows.length;i++){
    var k=rows[i].getAttribute('data-key');
    if(k===key){
      rows[i].classList.toggle('dlcp-runsel',_dlcpDesel.has(key));
      rows[i].classList.toggle('dlcp-rsel',!_dlcpDesel.has(key));
    }
  }
  _dlcpRender();
}
// Dropdown filter
function dlcpDdOpen(col,btn){
  if(_dlcpDdCurCol===col){
    _dlcpDdClose();return;
  }
  _dlcpDdCurCol=col;
  var panel=document.getElementById('dlcp-dd-panel');if(!panel)return;
  var srch=document.getElementById('dlcp-dd-srch');if(srch)srch.value='';
  // collect unique values
  var r=_dlcpComputeRows();
  var vals=[];
  r.rows.forEach(function(x){
    var v=col===0?x.lot:col===1?x.wafer:x.mat;
    if(vals.indexOf(v)<0)vals.push(v);
  });
  vals.sort();
  var cur=_dlcpDdFlt[col]||null;
  _dlcpDdPending=cur?new Set(cur):null; // null = all checked
  var lst=document.getElementById('dlcp-dd-list');if(!lst)return;
  lst.innerHTML=vals.map(function(v){
    var chk=(!_dlcpDdPending||_dlcpDdPending.has(v))?'checked':'';
    return '<label class="dlcp-dd-item"><input type="checkbox" value="'+_dlcpEsc(v)+'" '+chk+' onchange="IC.dlcpDdChk(this)"> <span>'+_dlcpEsc(v)+'</span></label>';
  }).join('');
  // position near button
  var r2=btn.getBoundingClientRect();
  panel.style.display='block';
  panel.style.left=r2.left+'px';
  panel.style.top=(r2.bottom+2)+'px';
  // mark btn
  document.querySelectorAll('.dlcp-ddbtn').forEach(function(b){b.classList.remove('on');});
  btn.classList.add('on');
  // close on outside click
  setTimeout(function(){
    document.addEventListener('click',_dlcpDdOutside,{once:true});
  },0);
}
function _dlcpDdOutside(){
  _dlcpDdApply();
}
function _dlcpDdClose(){
  _dlcpDdCurCol=-1;
  var panel=document.getElementById('dlcp-dd-panel');if(panel)panel.style.display='none';
  document.querySelectorAll('.dlcp-ddbtn').forEach(function(b){b.classList.remove('on');});
}
function dlcpDdChk(inp){
  var v=inp.value,chk=inp.checked;
  if(!_dlcpDdPending){
    // was all-selected; now deselect one → start explicit set with all minus this
    var r=_dlcpComputeRows();
    _dlcpDdPending=new Set();
    r.rows.forEach(function(x){
      var vv=_dlcpDdCurCol===0?x.lot:_dlcpDdCurCol===1?x.wafer:x.mat;
      _dlcpDdPending.add(vv);
    });
  }
  if(chk)_dlcpDdPending.add(v);
  else _dlcpDdPending.delete(v);
}
function dlcpDdSelAll(){
  _dlcpDdPending=null;
  var lst=document.getElementById('dlcp-dd-list');if(!lst)return;
  lst.querySelectorAll('input[type=checkbox]').forEach(function(cb){cb.checked=true;});
}
function dlcpDdSelNone(){
  _dlcpDdPending=new Set();
  var lst=document.getElementById('dlcp-dd-list');if(!lst)return;
  lst.querySelectorAll('input[type=checkbox]').forEach(function(cb){cb.checked=false;});
}
function dlcpDdSearch(q){
  q=q.toLowerCase();
  var lst=document.getElementById('dlcp-dd-list');if(!lst)return;
  lst.querySelectorAll('.dlcp-dd-item').forEach(function(el){
    var txt=el.textContent.toLowerCase();
    el.style.display=(!q||txt.indexOf(q)>=0)?'':'none';
  });
}
function dlcpDdApply(){
  if(_dlcpDdCurCol>=0){
    _dlcpDdFlt[_dlcpDdCurCol]=(_dlcpDdPending&&_dlcpDdPending.size>0)?_dlcpDdPending:null;
    // update btn highlight
    var btn=document.getElementById('dlcp-dd-btn-'+_dlcpDdCurCol);
    if(btn)btn.classList.toggle('on',!!_dlcpDdFlt[_dlcpDdCurCol]);
  }
  _dlcpDdClose();
  _dlcpRenderTable();
  _dlcpRender();
}
function dlcpSplitterToggle(){
  var rp=document.getElementById('dlcp-right-pane');
  var arr=document.getElementById('dlcp-split-arrow');
  if(!rp||!arr)return;
  var hidden=rp.style.display==='none';
  rp.style.display=hidden?'':'none';
  arr.innerHTML=hidden?'&#9654;':'&#9664;';
  requestAnimationFrame(_dlcpRenderCdf);
}
function _dlcpBuildFilterRow(){
  var fr=document.getElementById('dlcp-flt-row');if(!fr)return;
  // 21 columns: cols 0-2 handled by dropdowns, cols 3-20 get numeric filter
  var nc=21,html='<tr style="background:#f0f4ff">';
  for(var ci=0;ci<nc;ci++){
    if(ci<3){html+='<td></td>';continue;} // Lot/Wafer/Mat use dropdowns
    html+='<td style="padding:1px 3px"><input data-ci="'+ci+'" placeholder="e.g. &gt;50" title="Numeric filter: use &gt; &lt; &gt;= &lt;= = != or plain number for exact match" style="width:100%;box-sizing:border-box;font-size:10px;padding:1px 3px;border:1px solid #ccd;border-radius:2px" oninput="IC.dlcpFltInput('+ci+',this.value)"></td>';
  }
  html+='</tr>';
  fr.innerHTML=html;
}
function dlcpFltInput(ci,val){
  _dlcpFltVals[ci]=val.trim();
  _dlcpApplyFilter();
}
// Parse a cell text value to a number (strips %, —, spaces)
function _dlcpNumVal(txt){
  var s=txt.replace(/%/g,'').replace(/\u2014/g,'').trim();
  var n=parseFloat(s);
  return isNaN(n)?null:n;
}
// Test a numeric filter expression against a cell value string.
// Supports: >N  <N  >=N  <=N  =N  !=N  N (exact)
function _dlcpNumTest(fv,cellTxt){
  var m=fv.match(/^(>=|<=|!=|>|<|=)?\s*([\d.]+)$/);
  if(!m)return true; // not a valid expression → don't filter
  var op=m[1]||'=',threshold=parseFloat(m[2]);
  var val=_dlcpNumVal(cellTxt);
  if(val===null)return false; // cell has no number → fail
  if(op==='>') return val>threshold;
  if(op==='<') return val<threshold;
  if(op==='>=')return val>=threshold;
  if(op==='<=')return val<=threshold;
  if(op==='!=')return val!==threshold;
  return val===threshold; // '=' or plain number
}
function _dlcpApplyFilter(){
  var tb=document.getElementById('dlcp-tb');if(!tb)return;
  var rows=tb.getElementsByTagName('tr');
  for(var i=0;i<rows.length;i++){
    var cells=rows[i].getElementsByTagName('td');
    var key=rows[i].getAttribute('data-key');
    var show=true;
    // Dropdown filters for cols 0,1,2
    var colVals=[cells[0]?cells[0].textContent:'',cells[1]?cells[1].textContent:'',cells[2]?cells[2].textContent:''];
    [0,1,2].forEach(function(ci){
      var fset=_dlcpDdFlt[ci];
      if(fset&&fset.size>0&&!fset.has(colVals[ci]))show=false;
    });
    // Numeric filters for cols 3+
    Object.keys(_dlcpFltVals).forEach(function(ci){
      var fv=_dlcpFltVals[ci];
      if(!fv)return;
      var ci2=parseInt(ci);
      var cellTxt=cells[ci2]?cells[ci2].textContent:'';
      if(!_dlcpNumTest(fv,cellTxt))show=false;
    });
    rows[i].style.display=show?'':'none';
  }
}
function dlcpClearFilters(){
  _dlcpFltVals={};_dlcpDdFlt={};
  document.querySelectorAll('.dlcp-ddbtn').forEach(function(b){b.classList.remove('on');});
  var fr=document.getElementById('dlcp-flt-row');if(fr){var inps=fr.getElementsByTagName('input');for(var i=0;i<inps.length;i++)inps[i].value='';}
  var tb=document.getElementById('dlcp-tb');if(tb){var rows=tb.getElementsByTagName('tr');for(var i=0;i<rows.length;i++)rows[i].style.display='';}
  _dlcpRender();
}
function dlcpTogglePanel(which){
  var id=which==='tbl'?'dlcp-tbl-pane':'dlcp-plt-pane';
  var el=document.getElementById(id);if(!el)return;
  el.style.display=el.style.display==='none'?'':'none';
  if(which==='plt')requestAnimationFrame(_dlcpRenderCdf);
}
function dlcpDownloadCsv(){
  var r=_dlcpComputeRows();
  var hdr=['Lot','Wafer','Material','Total','Med UPM%','HP#','HP%','LP#','LP%','Fail#','Fail%','FF+DF(IB1-4)#','FF+DF(IB1-4)% of total','FF(IB1,2)#','FF% of IB1-4','DF(IB3-4)#','DF(IB3-4)% of IB1-4','ATOM DF(IB3)#','ATOM DF(IB3)% of IB1-4','CORE DF(IB4)#','CORE DF(IB4)% of IB1-4'];
  // only export visible+selected rows
  var tb=document.getElementById('dlcp-tb');
  var visKeys=new Set();
  if(tb){var rows=tb.getElementsByTagName('tr');for(var i=0;i<rows.length;i++){if(rows[i].style.display!=='none'){var k=rows[i].getAttribute('data-key');if(k)visKeys.add(k);}}}
  function q(s){var v=String(s==null?'':s);return(v.indexOf(',')>=0||v.indexOf('"')>=0)?'"'+v.replace(/"/g,'""')+'"':v;}
  var lines=[hdr.join(',')];
  r.rows.forEach(function(x){
    var k=_dlcpRowKey(x.lot,x.wafer);
    if(visKeys.size>0&&!visKeys.has(k))return;
    var t=x.nA+x.nB+x.nC;if(!t)return;
    var f12=x.nA+x.nB,f14=x.nFF+x.nDF3+x.nDF4;
    lines.push([x.lot,x.wafer,x.mat,t,x.med!=null?x.med.toFixed(2):'',
      x.nA,f12>0?(x.nA/f12*100).toFixed(1):'',
      x.nB,f12>0?(x.nB/f12*100).toFixed(1):'',
      x.nC,t>0?(x.nC/t*100).toFixed(1):'',
      f14,t>0?(f14/t*100).toFixed(1):'',
      x.nFF,f14>0?(x.nFF/f14*100).toFixed(1):'',
      x.nDF34,f14>0?(x.nDF34/f14*100).toFixed(1):'',
      x.nDF3,f14>0?(x.nDF3/f14*100).toFixed(1):'',
      x.nDF4,f14>0?(x.nDF4/f14*100).toFixed(1):''].map(q).join(','));
  });
  var blob=new Blob([lines.join('\n')],{type:'text/csv'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='dlcp_table.csv';a.click();
}
function dlcpSavePng(){
  var cv=document.getElementById('dlcp-cv');if(!cv)return;
  var a=document.createElement('a');a.href=cv.toDataURL('image/png');a.download='dlcp_cdf.png';a.click();
}
function dlcpOpenHistModal(){
  var ov=document.getElementById('upm-hist-overlay');if(!ov)return;
  ov.classList.add('open');
  var box=document.getElementById('upm-hist-box'),drag=document.getElementById('upm-hist-drag');
  if(box&&drag&&!drag._histDrag){
    drag._histDrag=true;
    drag.addEventListener('mousedown',function(e){
      e.preventDefault();
      var startX=e.clientX,startY=e.clientY,r=box.getBoundingClientRect();
      var startL=r.left,startT=r.top;
      box.style.position='fixed';box.style.margin='0';
      function mm(e){box.style.left=(startL+e.clientX-startX)+'px';box.style.top=(startT+e.clientY-startY)+'px';}
      function mu(){document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);}
      document.addEventListener('mousemove',mm);document.addEventListener('mouseup',mu);
    });
  }
  requestAnimationFrame(_dlcpRenderHist);
}
function dlcpCloseHistModal(){
  var ov=document.getElementById('upm-hist-overlay');if(ov)ov.classList.remove('open');
}
function _dlcpHistStats(arr){
  if(!arr||!arr.length)return null;
  var n=arr.length,sorted=arr.slice().sort(function(a,b){return a-b;});
  var mean=sorted.reduce(function(s,v){return s+v;},0)/n;
  var sigma=Math.sqrt(sorted.reduce(function(s,v){return s+(v-mean)*(v-mean);},0)/n);
  var med=n%2===0?(sorted[n/2-1]+sorted[n/2])/2:sorted[Math.floor(n/2)];
  function pct(p){var i=(p/100)*(n-1),lo=Math.floor(i),hi=Math.ceil(i);return lo===hi?sorted[lo]:sorted[lo]+(sorted[hi]-sorted[lo])*(i-lo);}
  return {n:n,mean:mean,med:med,sigma:sigma,min:sorted[0],max:sorted[n-1],p5:pct(5),p25:pct(25),p75:pct(75),p95:pct(95)};
}
function _dlcpRenderHist(){
  var cv=document.getElementById('upm-hist-cv');if(!cv)return;
  var W=cv.clientWidth||740,H=cv.clientHeight||260;
  cv.width=W;cv.height=H;
  var ctx=cv.getContext('2d');ctx.clearRect(0,0,W,H);
  var uI=(DATA.upmStart||5)+_dlcpUi,hp=[],lp=[];
  /* Systematic downsampling for large datasets */
  var MAX_HIST=80000;
  var _hTot=0;
  sR.forEach(function(ri){var row=DATA.rows[ri];if(!row||!row.dies)return;var k=_dlcpRowKey(row.lot||'',row.wafer||'');if(!_dlcpIsRowSel(k))return;_hTot+=row.dies.length;});
  var _hStep=_hTot>MAX_HIST?Math.ceil(_hTot/MAX_HIST):1;
  var _hI=0;
  sR.forEach(function(ri){
    var row=DATA.rows[ri];if(!row||!row.dies)return;
    var k=_dlcpRowKey(row.lot||'',row.wafer||'');
    if(!_dlcpIsRowSel(k))return;
    row.dies.forEach(function(d){
      _hI++;if(_hI%_hStep!==0)return;
      var ib=d[2],up=d.length>uI?d[uI]:null;if(up==null)return;
      if((ib===1||ib===2)&&up>=_dlcpT)hp.push(up);
      else if(ib!=null&&ib>=1&&ib<=4)lp.push(up);
    });
  });
  var all=hp.concat(lp);
  if(!all.length){
    ctx.fillStyle='#999';ctx.font='13px Arial';ctx.textAlign='center';ctx.fillText('No UPM data in selected wafers',W/2,H/2);
    var sd=document.getElementById('upm-hist-stats');if(sd)sd.innerHTML='<span style="color:#999">No data</span>';return;
  }
  // Stats
  var asP=_dlcpHistStats(all),hsP=_dlcpHistStats(hp),lsP=_dlcpHistStats(lp);
  function fmt(v){return v==null?'\u2014':v.toFixed(2);}
  function sCard(lbl,s,col){
    if(!s)return '<div class="upm-hist-stat-grp" style="border-color:'+col+'"><div class="upm-hist-stat-lbl" style="color:'+col+'">'+lbl+'</div><div class="upm-hist-stat-val">N=0</div></div>';
    return '<div class="upm-hist-stat-grp" style="border-color:'+col+'">'
      +'<div class="upm-hist-stat-lbl" style="color:'+col+'">'+lbl+'</div>'
      +'<div class="upm-hist-stat-val" style="color:'+col+'">N='+s.n+'</div>'
      +'<div style="font-size:11px;color:#444">Median: <b>'+fmt(s.med)+'%</b> &nbsp; Mean: <b>'+fmt(s.mean)+'%</b></div>'
      +'<div style="font-size:11px;color:#444">\u03c3: <b>'+fmt(s.sigma)+'%</b> &nbsp; Min: '+fmt(s.min)+'% &nbsp; Max: '+fmt(s.max)+'%</div>'
      +'<div style="font-size:10px;color:#777">P5: '+fmt(s.p5)+'% &nbsp; P25: '+fmt(s.p25)+'% &nbsp; P75: '+fmt(s.p75)+'% &nbsp; P95: '+fmt(s.p95)+'%</div>'
      +'</div>';
  }
  var sd=document.getElementById('upm-hist-stats');
  if(sd)sd.innerHTML=sCard('All IB1-4',asP,'#2c3e50')+sCard('HP (IB1/2 \u2265thr)',hsP,'#2980b9')+sCard('LP (IB1-4 <thr)',lsP,'#e67e22');
  // Histogram bins
  var xMn=Math.floor(Math.min.apply(null,all)),xMx=Math.ceil(Math.max.apply(null,all));
  if(xMx-xMn<4){xMn-=2;xMx+=2;}
  var bins=Math.min(80,Math.max(20,Math.round((xMx-xMn)*2))),bw=(xMx-xMn)/bins;
  function mBins(arr){var b=new Array(bins).fill(0);arr.forEach(function(v){var i=Math.min(bins-1,Math.floor((v-xMn)/bw));if(i>=0)b[i]++;});return b;}
  var hpB=mBins(hp),lpB=mBins(lp),maxC=0;
  for(var i=0;i<bins;i++){var s2=hpB[i]+lpB[i];if(s2>maxC)maxC=s2;}
  if(!maxC)return;
  var ML=46,MR=14,MT=20,MB=38,PW=W-ML-MR,PH=H-MT-MB;
  function xp(v){return ML+(v-xMn)/(xMx-xMn)*PW;}
  // Grid
  ctx.strokeStyle='#ececec';ctx.lineWidth=1;
  for(var gi=0;gi<=4;gi++){var gy=MT+PH*gi/4;ctx.beginPath();ctx.moveTo(ML,gy);ctx.lineTo(ML+PW,gy);ctx.stroke();}
  // Stacked bars
  for(var bi=0;bi<bins;bi++){
    var bx0=xp(xMn+bi*bw)+0.5,bx1=xp(xMn+(bi+1)*bw)-0.5,bW=Math.max(1,bx1-bx0);
    var lpH=lpB[bi]/maxC*PH,hpH=hpB[bi]/maxC*PH;
    if(lpB[bi]>0){ctx.fillStyle='rgba(230,126,34,0.78)';ctx.fillRect(bx0,MT+PH-lpH,bW,lpH);}
    if(hpB[bi]>0){ctx.fillStyle='rgba(41,128,185,0.82)';ctx.fillRect(bx0,MT+PH-lpH-hpH,bW,hpH);}
  }
  // Threshold
  if(_dlcpT>=xMn&&_dlcpT<=xMx){
    ctx.save();ctx.strokeStyle='#e74c3c';ctx.lineWidth=2;ctx.setLineDash([5,4]);
    var tx=xp(_dlcpT);ctx.beginPath();ctx.moveTo(tx,MT);ctx.lineTo(tx,MT+PH);ctx.stroke();
    ctx.setLineDash([]);ctx.fillStyle='#e74c3c';ctx.font='11px Arial';ctx.textAlign='center';
    ctx.fillText(_dlcpT.toFixed(1)+'%',tx,MT-5);ctx.restore();
  }
  // Axes
  ctx.strokeStyle='#555';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(ML,MT);ctx.lineTo(ML,MT+PH);ctx.lineTo(ML+PW,MT+PH);ctx.stroke();
  ctx.fillStyle='#555';ctx.font='10px Arial';ctx.textAlign='right';
  for(var yi=0;yi<=4;yi++){ctx.fillText(Math.round(maxC*yi/4),ML-4,MT+PH-PH*yi/4+4);}
  ctx.textAlign='center';
  var rng=xMx-xMn,xstp=rng>40?5:rng>20?2:1,xs=Math.ceil(xMn/xstp)*xstp;
  for(var xv=xs;xv<=xMx;xv+=xstp)ctx.fillText(xv+'%',xp(xv),MT+PH+13);
  ctx.fillStyle='#2c3e50';ctx.font='bold 11px Arial';ctx.textAlign='center';
  ctx.fillText('UPM %',ML+PW/2,H-4);
  ctx.save();ctx.translate(12,MT+PH/2);ctx.rotate(-Math.PI/2);ctx.fillText('Count',0,0);ctx.restore();
}
function _dlcpRenderTable(){
  var r=_dlcpComputeRows(),tA=0,tB=0,tC=0,tN=0,tFF=0,tDF34=0,tDF3=0,tDF4=0,allUv=[],html='';
  var tb=document.getElementById('dlcp-tb');if(!tb)return;
  if(r.noDies){tb.innerHTML='<tr><td colspan="21" style="padding:14px;color:#7f8c8d;text-align:center">No die-level UPM data. Re-run pipeline with upmInfo configured.</td></tr>';_dlcpRenderSummary(0,0,0,0,null,0,0,0,0);return;}
  r.rows.forEach(function(x){
    var t=x.nA+x.nB+x.nC;if(!t)return;
    var key=_dlcpRowKey(x.lot,x.wafer);
    var isSel=_dlcpIsRowSel(key);
    // Apply dropdown/text filters for visibility
    var ddOk=true;
    var ddVals=[x.lot,x.wafer,x.mat];
    [0,1,2].forEach(function(ci){var fs=_dlcpDdFlt[ci];if(fs&&fs.size>0&&!fs.has(ddVals[ci]))ddOk=false;});
    var visStyle=ddOk?'':'display:none';
    var f12=x.nA+x.nB;
    var f14=x.nFF+x.nDF3+x.nDF4; // IB1-4 total (FF+DF)
    if(ddOk){tA+=isSel?x.nA:0;tB+=isSel?x.nB:0;tC+=isSel?x.nC:0;tN+=isSel?t:0;tFF+=isSel?x.nFF:0;tDF34+=isSel?x.nDF34:0;tDF3+=isSel?x.nDF3:0;tDF4+=isSel?x.nDF4:0;}
    html+='<tr data-key="'+_dlcpEsc(key)+'" class="'+(isSel?'dlcp-rsel':'dlcp-runsel')+'" style="'+visStyle+'" onclick="IC.dlcpRowClick(\''+key.replace(/'/g,"\\'")+'\')">' 
      +'<td>'+_dlcpEsc(x.lot)+'</td>'
      +'<td>'+_dlcpEsc(x.wafer)+'</td>'
      +'<td style="color:#555;font-size:11px">'+_dlcpEsc(x.mat)+'</td>'
      +'<td class="num">'+t+'</td>'
      +'<td class="num">'+(x.med!=null?x.med.toFixed(2)+'%':'\u2014')+'</td>'
      +'<td class="num" style="color:#1a5276;font-weight:bold">'+x.nA+'</td>'
      +'<td class="num" style="color:#1a5276">'+(f12>0?(x.nA/f12*100).toFixed(1):'\u2014')+'%</td>'
      +'<td class="num" style="color:#ba6b0a">'+x.nB+'</td>'
      +'<td class="num" style="color:#ba6b0a">'+(f12>0?(x.nB/f12*100).toFixed(1):'\u2014')+'%</td>'
      +'<td class="num" style="color:#922b21">'+x.nC+'</td>'
      +'<td class="num" style="color:#922b21">'+(t>0?(x.nC/t*100).toFixed(1):'\u2014')+'%</td>'
      +'<td class="num" style="color:#1a7a4a;font-weight:bold">'+f14+'</td>'
      +'<td class="num" style="color:#1a7a4a">'+(t>0?(f14/t*100).toFixed(1):'\u2014')+'%</td>'
      +'<td class="num" style="color:#1e8449;font-weight:bold">'+x.nFF+'</td>'
      +'<td class="num" style="color:#1e8449">'+(f14>0?(x.nFF/f14*100).toFixed(1):'\u2014')+'%</td>'
      +'<td class="num" style="color:#117a65;font-weight:bold">'+x.nDF34+'</td>'
      +'<td class="num" style="color:#117a65">'+(f14>0?(x.nDF34/f14*100).toFixed(1):'\u2014')+'%</td>'
      +'<td class="num" style="color:#7d3c98">'+x.nDF3+'</td>'
      +'<td class="num" style="color:#7d3c98">'+(f14>0?(x.nDF3/f14*100).toFixed(1):'\u2014')+'%</td>'
      +'<td class="num" style="color:#a04000">'+x.nDF4+'</td>'
      +'<td class="num" style="color:#a04000">'+(f14>0?(x.nDF4/f14*100).toFixed(1):'\u2014')+'%</td>'
      +'</tr>';
  });
  tb.innerHTML=html;
  _dlcpBuildFilterRow();
  // do NOT re-apply text filter here; dropdown filter applied during render
  // Build set of visible+selected row keys from rendered table (respects ALL filters)
  var visKeys=new Set();
  var tbEl=document.getElementById('dlcp-tb');
  if(tbEl){var trs=tbEl.getElementsByTagName('tr');for(var vi=0;vi<trs.length;vi++){if(trs[vi].style.display!=='none'){var vk=trs[vi].getAttribute('data-key');if(vk&&!_dlcpDesel.has(vk))visKeys.add(vk);}}}
  var uI=(DATA.upmStart||5)+_dlcpUi;
  sR.forEach(function(ri){
    var row=DATA.rows[ri];if(!row||!row.dies)return;
    var k=_dlcpRowKey(row.lot||'',row.wafer||'');
    if(!visKeys.has(k))return;
    row.dies.forEach(function(d){var up=d.length>uI?d[uI]:null;if(up!=null)allUv.push(up);});
  });
  allUv.sort(function(a,b){return a-b;});
  var medAll=null;if(allUv.length){var m2=Math.floor(allUv.length/2);medAll=allUv.length%2===0?(allUv[m2-1]+allUv[m2])/2:allUv[m2];}
  _dlcpRenderSummary(tA,tB,tC,tN,medAll,tFF,tDF34,tDF3,tDF4);
  var nd=document.getElementById('dlcp-note');
  if(nd)nd.innerHTML='<b>HP%</b> = HP / (HP+LP) &nbsp;|&nbsp; <b>LP%</b> = LP / (HP+LP) &nbsp;|&nbsp; <b>Fail%</b> = Fail / Total &nbsp;|&nbsp; <b>FF/DF%</b> = count / IB1-4 total &nbsp;|&nbsp; Threshold: <b>'+_dlcpT.toFixed(1)+'%</b>';
}
function _dlcpRenderCdf(){
  var cv=document.getElementById('dlcp-cv');if(!cv)return;
  var W=cv.clientWidth||560,H=cv.clientHeight||280;
  cv.width=W;cv.height=H;
  var ctx=cv.getContext('2d');ctx.clearRect(0,0,W,H);
  if(!DATA.upmCols||!DATA.upmCols.length){
    ctx.fillStyle='#999';ctx.font='13px Arial';ctx.textAlign='center';ctx.fillText('No UPM columns configured',W/2,H/2);return;
  }
  var uI=(DATA.upmStart||5)+_dlcpUi;
  var hp=[],lp=[],ff=[],df=[];
  /* Systematic downsampling: cap at 80k points so large datasets don't stall the browser */
  var MAX_CDF=80000;
  var _cdfTot=0;
  sR.forEach(function(ri){var row=DATA.rows[ri];if(!row||!row.dies)return;var k=_dlcpRowKey(row.lot||'',row.wafer||'');if(!_dlcpIsRowSel(k))return;_cdfTot+=row.dies.length;});
  var _cdfStep=_cdfTot>MAX_CDF?Math.ceil(_cdfTot/MAX_CDF):1;
  var _cdfI=0;
  sR.forEach(function(ri){
    var row=DATA.rows[ri];if(!row||!row.dies)return;
    var k=_dlcpRowKey(row.lot||'',row.wafer||'');
    if(!_dlcpIsRowSel(k))return;
    row.dies.forEach(function(d){
      _cdfI++;if(_cdfI%_cdfStep!==0)return;
      var ib=d[2],up=d.length>uI?d[uI]:null;if(up==null)return;
      if(ib===1||ib===2){ff.push(up);if(up>=_dlcpT)hp.push(up);else lp.push(up);}
      else if(ib===3||ib===4){df.push(up);lp.push(up);}
    });
  });
  hp.sort(function(a,b){return a-b;});lp.sort(function(a,b){return a-b;});
  ff.sort(function(a,b){return a-b;});df.sort(function(a,b){return a-b;});
  if(!hp.length&&!lp.length){
    ctx.fillStyle='#999';ctx.font='13px Arial';ctx.textAlign='center';ctx.fillText('No UPM die data in selected wafers',W/2,H/2);return;
  }
  var ML=52,MR=16,MT=22,MB=42,PW=W-ML-MR,PH=H-MT-MB;
  var all=hp.concat(lp).concat(ff).concat(df);
  var xMn=Math.floor(Math.min.apply(null,all)*2)/2-1,xMx=Math.ceil(Math.max.apply(null,all)*2)/2+1;
  if(xMx-xMn<4){xMn-=2;xMx+=2;}
  function xp(v){return ML+(v-xMn)/(xMx-xMn)*PW;}
  function yp(v){return MT+PH-v/100*PH;}
  ctx.strokeStyle='#e8e8e8';ctx.lineWidth=1;
  for(var yi=0;yi<=4;yi++){ctx.beginPath();ctx.moveTo(ML,yp(yi*25));ctx.lineTo(ML+PW,yp(yi*25));ctx.stroke();}
  if(_dlcpT>=xMn&&_dlcpT<=xMx){
    ctx.save();ctx.strokeStyle='#e74c3c';ctx.lineWidth=1.5;ctx.setLineDash([5,4]);
    var tx=xp(_dlcpT);ctx.beginPath();ctx.moveTo(tx,MT);ctx.lineTo(tx,MT+PH);ctx.stroke();
    ctx.setLineDash([]);ctx.fillStyle='#e74c3c';ctx.font='11px Arial';ctx.textAlign='center';
    ctx.fillText(_dlcpT.toFixed(1)+'%',tx,MT-5);ctx.restore();
  }
  function drawCdf(arr,col,dash){if(!arr.length)return;
    ctx.save();ctx.strokeStyle=col;ctx.lineWidth=2;if(dash)ctx.setLineDash([6,3]);
    var n=arr.length;
    ctx.beginPath();ctx.moveTo(xp(arr[0]),yp(0));
    for(var i=0;i<n;i++){ctx.lineTo(xp(arr[i]),yp((i+1)/n*100));if(i<n-1)ctx.lineTo(xp(arr[i+1]),yp((i+1)/n*100));}
    ctx.lineTo(ML+PW,yp(100));ctx.stroke();ctx.restore();
  }
  drawCdf(df,'#8e44ad',true);drawCdf(ff,'#27ae60',true);
  drawCdf(lp,'#e67e22',false);drawCdf(hp,'#2980b9',false);
  ctx.strokeStyle='#555';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(ML,MT);ctx.lineTo(ML,MT+PH);ctx.lineTo(ML+PW,MT+PH);ctx.stroke();
  ctx.fillStyle='#555';ctx.font='11px Arial';ctx.textAlign='right';
  for(var yi2=0;yi2<=4;yi2++){ctx.fillText(yi2*25+'%',ML-4,yp(yi2*25)+4);}
  ctx.textAlign='center';var rng=xMx-xMn,stp=rng>20?5:rng>10?2:1,xs=Math.ceil(xMn/stp)*stp;
  for(var xv=xs;xv<=xMx;xv+=stp){ctx.fillText(xv.toFixed(0)+'%',xp(xv),MT+PH+14);}
  ctx.fillStyle='#2c3e50';ctx.font='bold 11px Arial';ctx.textAlign='center';
  ctx.fillText('UPM %',ML+PW/2,H-4);
  ctx.save();ctx.translate(13,MT+PH/2);ctx.rotate(-Math.PI/2);ctx.fillText('Cumulative %',0,0);ctx.restore();
  // Legend row 1: HP, LP
  var ly=MT+8;
  var ib14=ff.length+df.length,tot=hp.length+lp.length;
  function lgPct(n,d){return d>0?(n/d*100).toFixed(1)+'%':'0%';}
  function drawLgEntry(x,y,lineCol,lineDash,pctTxt,nTxt,pctCol){if(lineDash){ctx.save();ctx.strokeStyle=lineCol;ctx.lineWidth=2;ctx.setLineDash([6,3]);ctx.beginPath();ctx.moveTo(x,y+2);ctx.lineTo(x+22,y+2);ctx.stroke();ctx.restore();}else{ctx.fillStyle=lineCol;ctx.fillRect(x,y+1,22,3);}ctx.font='bold 12px Arial';ctx.fillStyle=pctCol||lineCol;ctx.textAlign='left';ctx.fillText(pctTxt,x+26,y+8);var pw=ctx.measureText(pctTxt).width;ctx.font='10px Arial';ctx.fillStyle='#aaa';ctx.fillText(' '+nTxt,x+26+pw,y+8);}
  drawLgEntry(ML,ly,'#2980b9',false,lgPct(hp.length,ib14),'HP  n='+hp.length,'#2980b9');
  drawLgEntry(ML+210,ly,'#e67e22',false,lgPct(lp.length,ib14),'LP  n='+lp.length,'#e67e22');
  // Legend row 2: FF, DF (dashed)
  var ly2=ly+16;
  drawLgEntry(ML,ly2,'#27ae60',true,lgPct(ff.length,ib14),'FF IB1,2  n='+ff.length,'#27ae60');
  drawLgEntry(ML+210,ly2,'#8e44ad',true,lgPct(df.length,ib14),'DF IB3,4  n='+df.length,'#8e44ad');
}
function _dlcpRender(){
  _dlcpRenderTable();
  requestAnimationFrame(_dlcpRenderCdf);
}
function dlcpSlider(){
  var sl=document.getElementById('dlcp-sl');if(!sl)return;
  _dlcpT=parseFloat(sl.value);
  var inp=document.getElementById('dlcp-tv-inp');if(inp)inp.value=_dlcpT.toFixed(1);
  _dlcpRender();
  rYield();
}
function dlcpTxtInput(val){
  var v=parseFloat(val);
  if(isNaN(v))return;
  v=Math.max(70,Math.min(100,v));
  _dlcpT=v;
  var sl=document.getElementById('dlcp-sl');if(sl)sl.value=v;
  _dlcpRender();
  rYield();
}
function dlcpSetCol(i){
  _dlcpUi=parseInt(i)||0;_dlcpRender();
}
function openDlcpModal(){
  _dlcpOpen=true;
  var ov=document.getElementById('dlcp-overlay');if(ov)ov.classList.add('open');
  /* populate UPM col selector if multiple cols */
  var cs=document.getElementById('dlcp-cs');
  if(cs&&DATA.upmCols&&DATA.upmCols.length>1){
    var h='<label>UPM Col: </label><select onchange="IC.dlcpSetCol(this.value)" style="font-size:12px;padding:2px">';
    DATA.upmCols.forEach(function(c,i){h+='<option value="'+i+'">'+c.label+'</option>';});
    h+='</select>';cs.innerHTML=h;
  }
  /* sync slider/textbox display */
  var sl=document.getElementById('dlcp-sl');if(sl){sl.value=_dlcpT;}
  var inp=document.getElementById('dlcp-tv-inp');if(inp)inp.value=_dlcpT.toFixed(1);
  _dlcpRender();
  /* set up drag */
  (function(){
    var box=document.getElementById('dlcp-box'),drag=document.getElementById('dlcp-drag');
    if(!box||!drag||drag._dlcpDrag)return;
    drag._dlcpDrag=true;
    var dragging=false,dX=0,dY=0;
    drag.addEventListener('mousedown',function(e){
      dragging=true;
      box.style.transform='none';
      var r=box.getBoundingClientRect();
      box.style.left=r.left+'px';box.style.top=r.top+'px';
      dX=e.clientX-r.left;dY=e.clientY-r.top;e.preventDefault();
    });
    document.addEventListener('mousemove',function(e){
      if(!dragging)return;
      var nx=e.clientX-dX,ny=e.clientY-dY;
      var mw=window.innerWidth-box.offsetWidth,mh=window.innerHeight-box.offsetHeight;
      box.style.left=Math.max(0,Math.min(mw,nx))+'px';
      box.style.top=Math.max(0,Math.min(mh,ny))+'px';
    });
    document.addEventListener('mouseup',function(){dragging=false;});
  })();
  if(window.ResizeObserver){
    var cv=document.getElementById('dlcp-cv');
    if(cv&&!cv._dlcpRO){cv._dlcpRO=true;new ResizeObserver(function(){if(_dlcpOpen)requestAnimationFrame(_dlcpRenderCdf);}).observe(cv);}
  }
}
function closeDlcpModal(){
  _dlcpOpen=false;
  var ov=document.getElementById('dlcp-overlay');if(ov)ov.classList.remove('open');
}
function exportYieldCsv(){
  // Export Yield Summary table (all bins, current filtered counts)
  var fc=gFC(),cn=fc.counts,tot=fc.total;
  var hdr=['BIN','FAIL BUCKET','ACTUAL (%)','EXPECTED (%)','DIFF (%)'];
  var lines=[hdr.join(',')];
  function q(s){var v=String(s==null?'':s);return(v.indexOf(',')>=0||v.indexOf('"')>=0)?'"'+v.replace(/"/g,'""')+'"':v;}
  DATA.yieldDefs.forEach(function(def){
    var cnt=def.bins_list.reduce(function(s,b){return s+(cn[b]||0);},0);
    var pct=tot>0?cnt/tot*100:0;
    var exp=def.expected?parseFloat(def.expected):NaN;
    var diff=!isNaN(exp)?(pct-exp):null;
    var diffStr=diff===null?'\u2014':(diff>0?'+':'')+diff.toFixed(1)+'%';
    lines.push([q(def.bins),q(def.bucket),q(pct.toFixed(1)+'%'),q(def.expected?def.expected+'%':''),q(diffStr)].join(','));
  });
  var blob=new Blob([lines.join('\r\n')],{type:'text/csv'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='yield_summary.csv';document.body.appendChild(a);a.click();
  setTimeout(function(){document.body.removeChild(a);URL.revokeObjectURL(a.href);},100);
}
function exportSdtCsv(){
  var hdr=['SDS IB','SDS FB','SDT IB','SDT FB','Bin Description','Count'];
  function q(s){var v=String(s==null?'':s);return(v.indexOf(',')>=0||v.indexOf('"')>=0||v.indexOf('\n')>=0)?'"'+v.replace(/"/g,'""')+'"':v;}
  var lines=[hdr.join(',')];
  (_sdtRows||[]).forEach(function(r){
    lines.push([q(r[0]),q(r[1]),q(r[2]),q(r[3]),q(r[4]),q(r[5])].join(','));
  });
  var blob=new Blob([lines.join('\r\n')],{type:'text/csv'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='sdt_bins.csv';document.body.appendChild(a);a.click();
  setTimeout(function(){document.body.removeChild(a);URL.revokeObjectURL(a.href);},100);
}
function exportCsv(){
  // Export currently visible (filtered) rows as CSV including all bin columns
  var active=Array.from(sR).sort(function(a,b){return a-b;});
  var bins=DATA.bins;
  var upmHdrs=DATA.hasUpmMed?['UPM_Med']:[];
  var fixedHdrs=['Program','Lot','Wafer','Material'].concat(upmHdrs).concat(['DateTested','Total']);
  var hdr=fixedHdrs.concat(bins.map(function(b){return 'IB'+b+'_count';})).concat(bins.map(function(b){return 'IB'+b+'_pct';}));
  var lines=[hdr.join(',')];
  active.forEach(function(i){
    var r=DATA.rows[i];
    var tot=r.total||0;
    var upmVals=DATA.hasUpmMed?(r.upmMed||[]).map(function(v){return v!==null&&v!==undefined?v:'';}):[]; 
    var fixed=[r.program||'',r.lot||'',r.wafer||'',r.material||''].concat(upmVals).concat([r.date||'',tot]);
    var cnts=bins.map(function(b){return r.binCounts&&r.binCounts[b]!=null?r.binCounts[b]:0;});
    var pcts=bins.map(function(b){
      var c=r.binCounts&&r.binCounts[b]!=null?r.binCounts[b]:0;
      return tot>0?(c/tot*100).toFixed(2):0;
    });
    lines.push(fixed.concat(cnts).concat(pcts).map(function(v){
      var s=String(v);return s.indexOf(',')>=0||s.indexOf('"')>=0?'"'+s.replace(/"/g,'""')+'"':s;
    }).join(','));
  });
  var blob=new Blob([lines.join('\r\n')],{type:'text/csv'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='yield_summary.csv';document.body.appendChild(a);a.click();
  setTimeout(function(){document.body.removeChild(a);URL.revokeObjectURL(a.href);},100);
}
return{clickBar:clickBar,clickLegend:clickLegend,legendClick:legendClick,toggleBucket:toggleBucket,
  toggleAllBins:toggleAllBins,toggleRow:toggleRow,selectAllRows:selectAllRows,clearRows:clearRows,
  gFC:gFC,DATA:DATA,sR:sR,rFilter:rFilter,sortFilter:sortFilter,ftDdOpen:ftDdOpen,showFbModal:showFbModal,closeFbModal:closeFbModal,
  fbCbChange:fbCbChange,selectAllFbs:selectAllFbs,clearFbs:clearFbs,showFbWaferMap:showFbWaferMap,fbTileClick:fbTileClick,
  _upmFbDdToggle:_upmFbDdToggle,_upmFbSearch:_upmFbSearch,
  bhHwChk:bhHwChk,bhHwSelAll:bhHwSelAll,bhHwClrAll:bhHwClrAll,bhHwClrColFilters:bhHwClrColFilters,
  hwGbChange:hwGbChange,hwGbAll:hwGbAll,hwGbNone:hwGbNone,
  hwTxtFilter:hwTxtFilter,showBhHwModal:showBhHwModal,closeBhHwModal:closeBhHwModal,
  refreshFb:refreshFb,refreshUpm:refreshUpm,selectYieldBins:selectYieldBins,
  lgSearch:lgSearch,showUpmModal:showUpmModal,closeUpmModal:closeUpmModal,_upmToggleMode:_upmToggleMode,_upmZoomIn:_upmZoomIn,_upmZoomOut:_upmZoomOut,_wmZoomIn:_wmZoomIn,_wmZoomOut:_wmZoomOut,_wmdZoomIn:_wmdZoomIn,_wmdZoomOut:_wmdZoomOut,
  _upmDieLocToggle:_upmDieLocToggle,_upmDieLocAll:_upmDieLocAll,
  showRecovModal:showRecovModal,_recovCategory:_recovCategory,
  _recovGrpChk:_recovGrpChk,_recovGrpClrAll:_recovGrpClrAll,
  _recovGrpSetAll:_recovGrpSetAll,_recovGrpSetNone:_recovGrpSetNone,
  _wmRetSiteToggle:_wmRetSiteToggle,_wmRetClear:_wmRetClear,_wmRenderReticle:_wmRenderReticle,_wmToggleCanvasMode:_wmToggleCanvasMode,setUpmMetric:setUpmMetric,
  openDlcpModal:openDlcpModal,closeDlcpModal:closeDlcpModal,dlcpSlider:dlcpSlider,dlcpTxtInput:dlcpTxtInput,dlcpSetCol:dlcpSetCol,
  dlcpFltInput:dlcpFltInput,dlcpClearFilters:dlcpClearFilters,dlcpTogglePanel:dlcpTogglePanel,dlcpDownloadCsv:dlcpDownloadCsv,dlcpSavePng:dlcpSavePng,
  dlcpOpenHistModal:dlcpOpenHistModal,dlcpCloseHistModal:dlcpCloseHistModal,
  dlcpSelAll:dlcpSelAll,dlcpSelNone:dlcpSelNone,dlcpRowClick:dlcpRowClick,
  dlcpDdOpen:dlcpDdOpen,dlcpDdChk:dlcpDdChk,dlcpDdSelAll:dlcpDdSelAll,dlcpDdSelNone:dlcpDdSelNone,dlcpDdSearch:dlcpDdSearch,dlcpDdApply:dlcpDdApply,
  dlcpSplitterToggle:dlcpSplitterToggle,
  openWmModal:openWmModal,closeWmModal:closeWmModal,
  exportCsv:exportCsv,exportYieldCsv:exportYieldCsv,exportSdtCsv:exportSdtCsv,
  rYield:rYield,
  _wmToggleRow:_wmToggleRow,_wmToggleLot:_wmToggleLot,_wmSelectAll:_wmSelectAll,_wmSetThresh:_wmSetThresh,
  _wmTab:_wmTab,_wmToggleBin:_wmToggleBin,_wmToggleBinAll:_wmToggleBinAll,_wmToggleCriteriaMiss:_wmToggleCriteriaMiss,
  _wmShowCriteriaCfg:_wmShowCriteriaCfg,_wmCritCfgToggle:_wmCritCfgToggle,_wmCritCfgAll:_wmCritCfgAll,_wmCritLoadJson:_wmCritLoadJson,
  _wmdOpen:_wmdOpen,_wmdClose:_wmdClose,_wmdUpmSel:_wmdUpmSel,_wmdHeatModeSel:_wmdHeatModeSel,_wmdTabSel:_wmdTabSel,_wmdRiVal:_wmdRiVal,_wmdShowFbForWafer:_wmdShowFbForWafer,_analyzeBins:_analyzeBins,
  _wmInlineToggle:_wmInlineToggle,
  showSdtSec:showSdtSec,_sdtCbChange:_sdtCbChange,sdtSort:sdtSort};
})();
/* Auto-open FB modal when loaded with #ib=N hash (e.g. from wafer pattern analysis popup) */
window.addEventListener('load',function(){
  var params=new URLSearchParams(location.search);
  var isModal=(params.get('modal')==='1');
  if(isModal){
    /* Hide main page — show only the fb-modal */
    var pw=document.querySelector('.pw');if(pw)pw.style.display='none';
    document.documentElement.style.background='transparent';
    document.body.style.background='transparent';
    /* Make fb-overlay fill the iframe viewport so modal stays in frame */
    var style=document.createElement('style');
    style.textContent='.fb-overlay.open{background:transparent!important}.fb-modal{top:20px!important;transform:translateX(-50%)!important;width:92vw!important;height:90vh!important;max-width:none!important;max-height:none!important}';
    document.head.appendChild(style);
  }
  var m=location.hash.match(/[#&]ib=(\d+)/i);
  if(m){
    var ib=parseInt(m[1],10);
    if(isModal){
      var fLot=params.get('lot'),fWafer=params.get('wafer');
      if((fLot||fWafer)&&typeof sR!=='undefined'&&typeof DATA!=='undefined'){
        var _sRf=new Set();
        sR.forEach(function(i){var row=DATA.rows&&DATA.rows[i];if(!row)return;if(fLot&&String(row.lot||'')!==fLot)return;if(fWafer&&String(row.wafer||'')!==fWafer)return;_sRf.add(i);});
        if(_sRf.size>0){sR=_sRf;}
      }
    }
    setTimeout(function(){if(typeof showFbModal==='function')showFbModal(ib);},isModal?300:600);
  }
});
function ypTgl(id){
  var b=document.getElementById('ypb-'+id);
  if(!b)return;
  var col=b.classList.toggle('yp-col');
  var btn=document.getElementById('ypmin-'+id);
  if(btn)btn.innerHTML=col?'&#43;':'&#8722;';
}
function ypMax(id){
  var p=document.getElementById('yp-'+id);
  if(!p)return;
  var on=p.classList.toggle('yp-max');
  var btn=document.getElementById('ypmax-'+id);
  if(btn)btn.innerHTML=on?'&#10006;':'&#10064;';
  var b=document.getElementById('ypb-'+id);
  if(b)b.classList.remove('yp-col');
  if(on){document.body.style.overflow='hidden';}else{document.body.style.overflow='';}
}
</script>
</body></html>'''
    )

    html = _html_head + _html_info + _html_layout + _html_script
    html_out.write_text(html, encoding='utf-8')


def main():
    if len(sys.argv) < 2:
        print('Usage: bin_distribution_html.py <csv_path> [out_dir] [fail_bucket_table_path]')
        sys.exit(2)
    csvp = sys.argv[1]
    if not (csvp.lower().endswith('.csv') or csvp.lower().endswith('.csv.gz') or csvp.lower().endswith('.zip')):
        print(f'Skipping non-CSV file: {csvp}')
        sys.exit(0)
    outd = sys.argv[2] if len(sys.argv) > 2 else None
    tbl  = sys.argv[3] if len(sys.argv) > 3 else None
    generate(csvp, outd, tbl_path=tbl)


if __name__ == '__main__':
    main()
_bindist_main = main  # stable alias for yield_pipeline dispatch


# ════════════════════════════════════════════════════════════════
# (formerly generate_heatmap_from_csv.py)
# ════════════════════════════════════════════════════════════════
import sys
import io
from pathlib import Path
import re
import colorsys
import hashlib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')


def _wm_inject(html: str) -> str:
    _wm = (
        '<div id="_wm_div" style="position:fixed;top:8px;right:12px;font-size:10px;'
        'font-weight:600;pointer-events:none;z-index:99999;'
        'font-family:Arial,sans-serif;user-select:none;letter-spacing:0.04em;'
        'padding:2px 6px;border-radius:3px;background:transparent;">'
        'Pant, Sujit N \u2014 GEMS FTE</div>'
        '<script>(function(){'
        'if(window!==window.top){var _d=document.getElementById("_wm_div");if(_d)_d.style.display="none";return;}'
        'function _wm_color(){'
        'var d=document.getElementById("_wm_div");if(!d)return;'
        'var bg=window.getComputedStyle(document.body).backgroundColor;'
        'var m=bg.match(/\\d+/g);'
        'if(m&&m.length>=3){'
        'var r=+m[0],g=+m[1],b=+m[2];'
        'var lum=0.299*r+0.587*g+0.114*b;'
        'd.style.color=lum<128?"rgba(255,255,255,0.9)":"rgba(20,20,20,0.75)";'
        '}else{d.style.color="rgba(255,255,255,0.9)";}'
        '}'
        'if(document.readyState==="loading")'
        '{document.addEventListener("DOMContentLoaded",_wm_color);}'
        'else{_wm_color();}'
        '})();</script>'
    )
    import re as _re_wm
    if '</body>' not in html:
        return html
    html = _re_wm.sub(
        r'<div[^>]*id=["\']_wm_div["\'][^>]*>[\s\S]*?</div>\s*<script[^>]*>[\s\S]*?</script>',
        '', html)
    html = _re_wm.sub(r'<div[^>]*>[^<]*GEMS FTE[^<]*</div>', '', html)
    return html.replace('</body>', _wm + '\n</body>', 1)
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib import colors as mcolors

try:
    from yield_data import detect_encoding, sniff_columns, read_csv_smart
    _HAS_CSV_UTILS = True
except ImportError:
    _HAS_CSV_UTILS = False


def _load_fail_bucket_table(tbl_path):
    _BUILTIN = [
        ('1/2', 'SDS FF yield', '67.8'),
        ('1/2/3/4', 'SDS FF+DF yield', '86.0'),
        ('1', 'SDS FF (No Repair) yield', '60.0'),
        ('2', 'MBIST Repair', '11.8'),
        ('3/4', 'Recovery (Defeatured)', '17.5'),
        ('3', 'Recovery (Atom Defeatured)', '9.0'),
        ('4', 'Recovery (Core Defeatured)', '9.0'),
        ('41/42/47/76/77/81/82', 'SCAN (post-recovery)', '5.0'),
        ('20/21/33/60/61/62/63/65', 'ARRAY MBIST (post-recovery)', '3.1'),
        ('11/13/16/25/27/28/32/36/39/46/48/51/64/71/74/75', 'ANALOG (post-recovery)', '0.3'),
        ('7/8/9/10/15/18/43', 'TPI (Foundry)', '1.9'),
        ('31/88/91/94/97/98/99 + 93', 'TPI (Bump/DiePrep/Test)', '1.1'),
        ('19/35', 'RESET', '1.1'),
        ('12/44/45/70/80/85/86', 'Functional', '0.8'),
        ('26', 'HVQK', '0.5'),
    ]
    if tbl_path is not None:
        p = Path(tbl_path)
        if p.suffix.lower() == '.json' and p.exists():
            try:
                import json as _json
                rows = []
                for entry in _json.loads(p.read_text(encoding='utf-8')):
                    rows.append((
                        str(entry.get('bin', '')),
                        str(entry.get('fail_bucket', '')),
                        str(entry.get('expected_yield_percent', '')),
                    ))
                if rows:
                    return rows
            except Exception:
                pass
    return _BUILTIN


def _parse_bin_tokens(bin_field: str):
    # return list of numeric tokens as strings in the bin_field
    return re.findall(r"\d+", str(bin_field))


def _compute_pct_map(df, bin_col='INTERFACE_BIN_119325'):
    # build counts per numeric token across the csv similar to the generator
    counts_series = df[bin_col].fillna('').astype(str).str.strip()
    token_counts = {}
    for cell in counts_series:
        nums = re.findall(r"\d+", cell)
        if not nums:
            token_counts.setdefault('', 0)
            if cell == '':
                token_counts[''] += 1
            continue
        for n in nums:
            token_counts[n] = token_counts.get(n, 0) + 1
    total = len(df)
    pct_map = {k: (v / total) * 100 for k, v in token_counts.items()}
    return pct_map


def _compute_group_pct(bin_field: str, pct_map: dict):
    nums = _parse_bin_tokens(bin_field)
    s = 0.0
    for n in nums:
        s += pct_map.get(n, 0.0)
    return s


def find_coord_columns(df):
    # robustly find Sort_X and Sort_Y columns
    x_col = next((c for c in df.columns if 'sort_x' in c.lower() or c.lower() == 'x' or 'coordx' in c.lower()), None)
    y_col = next((c for c in df.columns if 'sort_y' in c.lower() or c.lower() == 'y' or 'coordy' in c.lower()), None)
    # fallback: try wafer die X/Y naming patterns
    if not x_col:
        x_col = next((c for c in df.columns if c.lower().endswith('_x') or c.lower().endswith('x_coord') or 'posx' in c.lower()), None)
    if not y_col:
        y_col = next((c for c in df.columns if c.lower().endswith('_y') or c.lower().endswith('y_coord') or 'posy' in c.lower()), None)
    return x_col, y_col


def _to_coord_series(df, col):
    # Try numeric conversion first, then extract integer tokens from strings as a fallback.
    if col not in df.columns:
        return None
    s = pd.to_numeric(df[col], errors='coerce')
    # If numeric parsing produced mostly NaN or a single constant zero, try regex extraction
    if s.isnull().all() or (s.nunique() == 1 and (s.dropna().empty or s.dropna().iloc[0] == 0)):
        # extract first integer token from string
        extracted = df[col].astype(str).str.extract(r'(-?\d+)', expand=False)
        if extracted.notnull().any():
            try:
                s2 = pd.to_numeric(extracted, errors='coerce')
                # if extraction yields values, use them (fill NaN with original numeric if available)
                s = s2.fillna(s)
            except Exception:
                pass
    # If still all NaN, return None
    if s.isnull().all():
        return None
    # If series is constant or overwhelmingly zeros, treat as unusable
    try:
        nonzero_ratio = (s != 0).sum() / max(1, len(s))
        unique_vals = s.nunique(dropna=True)
        if unique_vals <= 1 or nonzero_ratio < 0.05:
            return None
    except Exception:
        pass
    # convert to integer coordinates (rounding down) and keep full-length series (fill NaN with 0)
    return s.fillna(0).astype(int)


def _sanitize_label(s: str):
    return re.sub(r"[^0-9A-Za-z_-]", '_', s)


def generate_heatmaps(csv_path, out_dir=None, tbl_path=None, bin_col='INTERFACE_BIN_119325', gui=False, html_only=False, interactive=True, render_wafermap=True, render_ibin_wafermap=True):
    import shutil
    csvp = Path(csv_path)
    outd = Path(out_dir) if out_dir else csvp.parent / 'output'
    if outd.exists():
        shutil.rmtree(outd)
    outd.mkdir(parents=True, exist_ok=True)

    # ── Hardware commonality fields (matched by prefix; real columns may have a _XXXXXX suffix) ──
    _HW_FIELD_PREFIXES_HM = ["Cell ID", "Unit Tester Id", "Unit Tester Site Id", "Unit TIU", "Thermal Head Id", "Sort Partial Wafer ID"]
    _HW_FIELD_NAMES_HM = _HW_FIELD_PREFIXES_HM  # kept for reference

    # ── Sniff columns first; load only what we need ───────────────────────────
    if _HAS_CSV_UTILS:
        _enc = detect_encoding(csvp)
        _all_cols = sniff_columns(csvp, encoding=_enc)

        # Resolve actual bin column name from header
        if bin_col not in _all_cols:
            _alt_bin = next((c for c in _all_cols
                             if 'INTERFACE_BIN' in c.upper()
                             and 'TOTAL' not in c.upper()), None)
            if _alt_bin:
                bin_col = _alt_bin

        # Identify coordinate columns from the header
        _x_hdr = next((c for c in _all_cols
                        if 'sort_x' in c.lower() or c.lower() == 'x'
                        or 'coordx' in c.lower()
                        or c.lower().endswith('_x')
                        or 'posx' in c.lower()), None)
        _y_hdr = next((c for c in _all_cols
                        if 'sort_y' in c.lower() or c.lower() == 'y'
                        or 'coordy' in c.lower()
                        or c.lower().endswith('_y')
                        or 'posy' in c.lower()), None)

        # Reticle overlay columns
        _lx_hdr = next((c for c in _all_cols if c.lower() in ('layoutx', 'layout_x')), None)
        _ly_hdr = next((c for c in _all_cols if c.lower() in ('layouty', 'layout_y')), None)
        _ret_hdr = next((c for c in _all_cols
                          if c.lower() in ('reticle', 'reticle_number', 'reticlenumber')), None)

        # Lot / wafer columns (needed for per-wafer table)
        _lot_hdr = next((c for c in _all_cols if c.lower() == 'lot'), None)
        if _lot_hdr is None:
            _lot_hdr = next((c for c in _all_cols if 'lot' in c.lower()), None)
        _wafer_hdr = next((c for c in _all_cols if 'sort_wafer' in c.lower()), None)
        if _wafer_hdr is None:
            _wafer_hdr = next((c for c in _all_cols if 'wafer' in c.lower()), None)

        _needed = {bin_col}
        for _c in (_x_hdr, _y_hdr, _lx_hdr, _ly_hdr, _ret_hdr, _lot_hdr, _wafer_hdr):
            if _c:
                _needed.add(_c)
        for _pfx_hw in _HW_FIELD_PREFIXES_HM:
            _pfx_lower = _pfx_hw.lower()
            _match_hw = next((c for c in _all_cols if c.lower().startswith(_pfx_lower)), None)
            if _match_hw:
                _needed.add(_match_hw)

        df = read_csv_smart(csvp, usecols=list(_needed), encoding=_enc)
    else:
        df = pd.read_csv(csvp, dtype=object)

    _hw_cols_hm = [
        next((c for c in df.columns if c.lower().startswith(pfx.lower())), None)
        for pfx in _HW_FIELD_PREFIXES_HM
    ]
    _hw_cols_hm = [c for c in _hw_cols_hm if c is not None]

    # load fail bucket table (fall back to built-in list if parsing fails)
    table_rows = _load_fail_bucket_table(tbl_path if tbl_path else None)
    if not table_rows:
        # fallback hard-coded table (match generator fallback)
        table_rows = [
            ('1/2/3/4', None, 'SDS FF+DF yield'),
            ('1/2', None, 'SDS FF yield'),
            ('1', None, 'SDS FF (No Repair) yield'),
            ('2', None, 'MBIST Repair'),
            ('3/4', None, 'Recovery (Defeatured)'),
            ('3', None, 'Recovery (Atom Defeatured)'),
            ('4', None, 'Recovery (Core Defeatured)'),
            ('41/42/47/76/77/81/82', None, 'SCAN (post-recovery)'),
            ('20/21/33/60/61/62/63/65', None, 'ARRAY MBIST (post-recovery)'),
            ('11/13/16/25/27/28/32/36/39/46/48/51/64/71/74/75', None, 'ANALOG (post-recovery)'),
            ('7/8/9/10/15/18/43', None, 'TPI (Foundry)'),
            ('31/88/91/94/97/98/99 + 93', None, 'TPI (Bump/DiePrep/Test)'),
            ('19/35', None, 'RESET'),
            ('12/44/45/70/80/85/86', None, 'Functional'),
            ('26', None, 'HVQK'),
        ]

    pct_map = _compute_pct_map(df, bin_col=bin_col)

    # Build canonical mapping from fallback rows so parsed table entries use the known descriptions
    # Build canonical maps: bin_key -> fail_bucket_desc and expected yield
    canonical_desc = {}
    canonical_expected = {}
    for rt in table_rows:
        k = re.sub(r"\s+", '', str(rt[0]).lower())
        # rt format expected: (bin, fail_bucket, expected)
        descv = rt[1] if len(rt) >= 2 and rt[1] else ''
        expv = rt[2] if len(rt) >= 3 and rt[2] else ''
        canonical_desc[k] = descv
        canonical_expected[k] = expv

    # Build enriched table: (bin_field, computed_pct, expected, desc)
    enriched = []
    for tup in table_rows:
        bin_field = tup[0]
        # prefer third column as expected/description depending on parsing; normalize desc using canonical_map
        raw_desc = tup[2] if len(tup) >= 3 and tup[2] else (tup[1] if len(tup) >= 2 else '')
        # use canonical mapping to avoid accidental concatenation
        k = re.sub(r"\s+", '', str(bin_field).lower())
        desc = canonical_desc.get(k, raw_desc)
        expected = canonical_expected.get(k, '')
        # try to find a numeric expected value in the row (best-effort)
        if len(tup) >= 3 and tup[2] and re.search(r"\d", tup[2]):
            expected = tup[2]
        elif len(tup) >= 2 and tup[1] and re.search(r"\d", tup[1]):
            expected = tup[1]
        comp = _compute_group_pct(bin_field, pct_map)
        enriched.append((bin_field, comp, expected, desc))

    x_col, y_col = find_coord_columns(df)
    if not x_col or not y_col:
        print('Warning: could not find Sort_X/Sort_Y columns; heatmaps require coordinate columns', file=sys.stderr)

    # Build coordinate integer series robustly from the found columns
    hx = _to_coord_series(df, x_col) if x_col else None
    hy = _to_coord_series(df, y_col) if y_col else None
    if hx is None or hy is None:
        print('Warning: could not parse numeric Sort_X/Sort_Y values from CSV; heatmap generation will be limited', file=sys.stderr)

    # Compute global X/Y axis limits from the entire CSV once — all heatmaps share the same range.
    global_xl, global_xh, global_yl, global_yh = None, None, None, None
    if hx is not None and hy is not None:
        buf = 1
        global_xl = int(hx.min()) - buf
        global_xh = int(hx.max()) + buf
        global_yl = int(hy.min()) - buf
        global_yh = int(hy.max()) + buf

    # ── detect LayoutX / LayoutY / Reticle columns for reticle overlay ─
    _lx_col_h = next((c for c in df.columns if c.lower() in ('layoutx', 'layout_x')), None)
    _ly_col_h = next((c for c in df.columns if c.lower() in ('layouty', 'layout_y')), None)
    _ret_col_h = next((c for c in df.columns if c.lower() in ('reticle', 'reticle_number', 'reticlenumber')), None)
    _has_reticle_h = bool(_lx_col_h and _ly_col_h)
    if _has_reticle_h:
        print(f'Reticle overlay enabled (bin heatmap): LayoutX={_lx_col_h}, LayoutY={_ly_col_h}'
              + (f', Reticle={_ret_col_h}' if _ret_col_h else ''))

    # For each fail-bucket row where any numeric token >4 and computed_pct > expected (if expected numeric), create heatmap
    for bin_field, comp_pct, expected_str, desc in enriched:
        nums = _parse_bin_tokens(bin_field)
        if not nums:
            continue
        # generate heatmaps for ALL bins that have any occurrence in the CSV
        if comp_pct <= 0:
            continue

        # create mask for rows that belong to this bin category (any numeric token match)
        # Vectorized: build a regex that matches any target bin as a standalone number
        target_set = set(nums)
        _bin_pat = r'(?<!\d)(?:' + '|'.join(re.escape(str(n)) for n in sorted(target_set, key=int)) + r')(?!\d)'
        mask = df[bin_col].fillna('').astype(str).str.contains(_bin_pat, regex=True, na=False)
        # ── hardware commonality breakdown for this bin ───────────────────────
        __rows = []
        if _hw_cols_hm:
            _hw_bin_total = int(mask.sum())
            if _hw_bin_total > 0:
                try:
                    _hw_bin_df = df.loc[mask, _hw_cols_hm].fillna('').astype(str)
                    _hw_grp = _hw_bin_df.groupby(_hw_cols_hm, sort=False).size().reset_index(name='count')
                    _hw_grp = _hw_grp.sort_values('count', ascending=False).head(100)
                    for _, _hr in _hw_grp.iterrows():
                        _hrow = {c: str(_hr[c]) for c in _hw_cols_hm}
                        _hrow['count'] = int(_hr['count'])
                        _hrow['pct'] = f"{_hr['count'] / _hw_bin_total * 100:.1f}%"
                        __rows.append(_hrow)
                except Exception:
                    pass
        # Build per-coordinate fallout percent: for each (x,y) position compute percentage of rows at that position that match mask
        if hx is not None and hy is not None:
            # use integer coordinate series for grouping
            coord_df = pd.DataFrame({'_hx': hx, '_hy': hy})
            grouped = coord_df.join(df[bin_col]).groupby(['_hx', '_hy'])
            coords = []
            vals = []
            for (xv, yv), grp in grouped:
                total_at_pos = len(grp)
                if total_at_pos == 0:
                    continue
                hits = mask[grp.index].sum()
                pct = (hits / total_at_pos) * 100
                coords.append((int(xv), int(yv)))
                vals.append(pct)

            if not coords:
                print(f'No coordinate data found for bin {bin_field}; skipping heatmap', file=sys.stderr)
                continue

            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            width = max_x - min_x + 1
            height = max_y - min_y + 1
            grid = np.full((height, width), np.nan)
            for (xv, yv), v in zip(coords, vals):
                xi = yv - min_y  # note: rows are y
                yi = xv - min_x  # cols are x
                try:
                    grid[xi, yi] = v
                except Exception:
                    continue

            # compute per-lot-wafer percentages BEFORE creating figure so we can size dynamically
            lot_col = (next((c for c in df.columns if c.lower() == 'sort_lot'), None) or
                       next((c for c in df.columns if 'lot' in c.lower() and 'slot' not in c.lower()), None))
            wafer_col = next((c for c in df.columns if 'wafer' in c.lower() or 'sort_wafer' in c.lower()), None)
            mat_col = next((c for c in df.columns if 'material' in c.lower()), None)
            group_cols = []
            if lot_col:
                group_cols.append(lot_col)
            if wafer_col:
                group_cols.append(wafer_col)
            if not group_cols:
                total = len(df)
                hits = int(mask.sum())
                fail_pct = (hits / total) * 100 if total else 0.0
                lw_rows = [('ALL', 'ALL', '', total, hits, f'{fail_pct:.2f}%')]
            else:
                grouped2 = df.groupby(group_cols)
                lw_rows = []
                for gkeys, grp in grouped2:
                    total = len(grp)
                    hits = int(mask[grp.index].sum())
                    pct = (hits / total) * 100 if total else 0.0
                    if isinstance(gkeys, tuple):
                        keys = gkeys
                    else:
                        keys = (gkeys,)
                    lot_val = keys[0] if len(keys) >= 1 else ''
                    wafer_val = keys[1] if len(keys) >= 2 else ''
                    mat_val = str(grp[mat_col].iloc[0]) if mat_col and not grp[mat_col].dropna().empty else ''
                    lw_rows.append((str(lot_val), str(wafer_val), mat_val, total, hits, f'{pct:.2f}%'))
                # sort by fail count descending so worst wafers appear first
                lw_rows.sort(key=lambda r: r[4], reverse=True)

            n_rows = max(1, len(lw_rows))
            cell_in = 0.22  # inches per unique coordinate value on each axis
            n_unique_x = max(1, len(np.unique(xs)))
            n_unique_y = max(1, len(np.unique(ys)))
            plot_w = max(4.0, n_unique_x * cell_in)
            plot_h = max(4.0, n_unique_y * cell_in)
            fig_w = min(12, plot_w + 1.5)
            fig_h = min(10, plot_h + 0.8)
            fig, ax_heat = plt.subplots(figsize=(fig_w, fig_h))

            # use raw coords (not shifted) for triangulation and contouring
            xs_raw = np.array(xs, dtype=float)
            ys_raw = np.array(ys, dtype=float)
            vals_raw = np.array(vals)

            # round values to integers for display
            vals_raw = np.rint(vals_raw).astype(float)

            # ── wafer_map_simple.py approach: center at origin, scale Y ──
            _wcx = (xs_raw.min() + xs_raw.max()) / 2.0
            _wcy = (ys_raw.min() + ys_raw.max()) / 2.0
            _xr  = xs_raw.max() - xs_raw.min()
            _yr  = ys_raw.max() - ys_raw.min()
            _die_dy = (_xr / _yr) if _yr > 0 else 1.0
            # center and scale
            xs_plot = (xs_raw - _wcx)
            ys_plot = (ys_raw - _wcy) * _die_dy

            # Bins 1/2/3/4 are yield bins (higher = better): green=100%, red=0%
            # All other bins are fail bins (higher = worse): green=0%, red=dynamic max
            _yield_tokens = {'1', '2', '3', '4'}
            _is_yield_bin = bool(set(_parse_bin_tokens(bin_field)) <= _yield_tokens)

            if _is_yield_bin:
                _cmap_name = 'RdYlGn'        # NOT reversed: green=high, red=low
                _vmax = 100
                _norm_h = mcolors.Normalize(vmin=0, vmax=100)
                step = 10
                levels = list(range(0, 101, step))
            else:
                _cmap_name = 'RdYlGn_r'      # reversed: green=low, red=high
                _vmax = 100  # fixed 0-100% scale for easy comparison
                _norm_h = mcolors.Normalize(vmin=0, vmax=_vmax)
                step = 10
                levels = list(range(0, _vmax + step, step))

            # create triangulation and filled contour (on centered+scaled coords)
            # ── wafer_map_simple.py: draw each die as a Rectangle box ──
            _die_dx = 1.0
            gap = 0.9
            cf = None
            _cmap_obj = matplotlib.colormaps[_cmap_name]
            # vectorized colormap: compute all colors at once, NaN dies get gray
            _clrs_all = np.where(
                np.isnan(vals_raw)[:, None],
                np.array([[0.8, 0.8, 0.8, 1.0]]),
                _cmap_obj(_norm_h(np.where(np.isnan(vals_raw), 0, vals_raw)))
            )
            for _i in range(len(xs_plot)):
                _rect = mpatches.Rectangle(
                    (xs_plot[_i] - _die_dx * gap / 2, ys_plot[_i] - _die_dy * gap / 2),
                    _die_dx * gap, _die_dy * gap,
                    linewidth=0.3, edgecolor='gray', facecolor=_clrs_all[_i],
                    rasterized=True
                )
                ax_heat.add_patch(_rect)

            # annotate top 5 hotspots
            try:
                top_idx = np.argsort(vals_raw)[-5:][::-1]
                for i in top_idx:
                    ax_heat.text(xs_plot[i], ys_plot[i], f"{int(vals_raw[i])}%", color='black', fontsize=7, ha='center', va='center', fontweight='bold')
            except Exception:
                pass

            ax_heat.set_title(
                f'Wafer heatmap bin {bin_field} {"yield %" if _is_yield_bin else "fallout %"} ({"higher = better" if _is_yield_bin else "higher = worse"})',
                fontsize=13)
            ax_heat.set_xlabel('Sort X', fontsize=11)
            ax_heat.set_ylabel('Sort Y', fontsize=11)

            # ── round wafer (wafer_map_simple.py): no outline, dies form the shape ─
            ax_heat.set_aspect('equal')
            # Axis limits: max absolute centered coord + 5%
            _xext_h = (abs(xs_plot).max() + 0.5) * 1.025
            _yext_h = (abs(ys_plot).max() + 0.5 * _die_dy) * 1.025
            ax_heat.set_xlim(-_xext_h, _xext_h)
            ax_heat.set_ylim(-_yext_h, _yext_h)
            # Y-axis ticks: remap back to original Sort_Y values
            y_ticks = [t for t in ax_heat.get_yticks() if -_yext_h <= t <= _yext_h]
            ax_heat.set_yticks(y_ticks)
            ax_heat.set_yticklabels([f"{v / _die_dy + _wcy:.0f}" for v in y_ticks], fontsize=8)
            # X-axis ticks: remap back to original Sort_X values
            x_ticks = [t for t in ax_heat.get_xticks() if -_xext_h <= t <= _xext_h]
            ax_heat.set_xticks(x_ticks)
            ax_heat.set_xticklabels([f"{v + _wcx:.0f}" for v in x_ticks], fontsize=8)
            ax_heat.set_xlim(-_xext_h, _xext_h)
            ax_heat.set_ylim(-_yext_h, _yext_h)
            ax_heat.axhline(0, color="black", linewidth=0.5, linestyle="--", alpha=0.3)
            ax_heat.axvline(0, color="black", linewidth=0.5, linestyle="--", alpha=0.3)
            ax_heat.grid(True, alpha=0.2)

            # colorbar via ScalarMappable
            try:
                _sm = plt.cm.ScalarMappable(cmap=_cmap_name, norm=_norm_h)
                _sm.set_array([])
                cbar = fig.colorbar(_sm, ax=ax_heat, orientation='vertical', fraction=0.046, pad=0.04)
                cbar.set_label("% yield" if _is_yield_bin else "% fail", fontsize=10)
            except Exception:
                pass

            # ── reticle overlay: grid lines + reticle numbers ──────────
            # Build lookup once; _draw_reticle(ax) reuses it for both composite
            # and per-wafer SVGs so the boundary lines are always visible.
            _rlookup_h = {}
            _xs_u = []
            _ys_u = []
            _ret_labels_h = []  # list of (x_plot, y_plot, label_str)
            if _has_reticle_h and hx is not None and hy is not None:
                _rdf_h = pd.DataFrame({
                    'sx': hx.values,
                    'sy': hy.values,
                    'lx': pd.to_numeric(df[_lx_col_h], errors='coerce').values,
                    'ly': pd.to_numeric(df[_ly_col_h], errors='coerce').values,
                }).dropna()
                if not _rdf_h.empty:
                    for _, _rr in _rdf_h.iterrows():
                        _rlookup_h[(int(_rr['sx']), int(_rr['sy']))] = (_rr['lx'], _rr['ly'])
                    _xs_u = sorted(_rdf_h['sx'].astype(int).unique())
                    _ys_u = sorted(_rdf_h['sy'].astype(int).unique())
                if _ret_col_h:
                    _ret_df = pd.DataFrame({
                        'sx': hx.values,
                        'sy': hy.values,
                        'ret': df[_ret_col_h].values,
                    }).dropna()
                    _ret_seen = set()
                    for _, _rr in _ret_df.iterrows():
                        _rk = (int(_rr['sx']), int(_rr['sy']))
                        if _rk in _ret_seen:
                            continue
                        _ret_seen.add(_rk)
                        try:
                            _ret_labels_h.append((
                                _rk[0] - _wcx,
                                (_rk[1] - _wcy) * _die_dy,
                                str(int(_rr['ret']))
                            ))
                        except (ValueError, TypeError):
                            pass

            def _draw_reticle(ax):
                """Draw reticle boundary lines and number labels onto any axis."""
                if not _rlookup_h or not _xs_u or not _ys_u:
                    return
                for _yi in _ys_u:
                    for _xi_idx in range(len(_xs_u) - 1):
                        _k1 = (_xs_u[_xi_idx], _yi)
                        _k2 = (_xs_u[_xi_idx + 1], _yi)
                        _lx1 = _rlookup_h.get(_k1, (None,))[0]
                        _lx2 = _rlookup_h.get(_k2, (None,))[0]
                        if _lx1 is not None and _lx2 is not None and _lx1 != _lx2:
                            _bx = ((_xs_u[_xi_idx] + _xs_u[_xi_idx + 1]) / 2 - _wcx)
                            _by_c = (_yi - _wcy) * _die_dy
                            ax.plot([_bx, _bx],
                                    [_by_c - _die_dy * 0.5, _by_c + _die_dy * 0.5],
                                    color='blue', linewidth=0.5, alpha=0.8, zorder=5)
                for _xi in _xs_u:
                    for _yi_idx in range(len(_ys_u) - 1):
                        _k1 = (_xi, _ys_u[_yi_idx])
                        _k2 = (_xi, _ys_u[_yi_idx + 1])
                        _ly1 = _rlookup_h.get(_k1, (None, None))[1]
                        _ly2 = _rlookup_h.get(_k2, (None, None))[1]
                        if _ly1 is not None and _ly2 is not None and _ly1 != _ly2:
                            _bx_c = (_xi - _wcx)
                            _by = ((_ys_u[_yi_idx] + _ys_u[_yi_idx + 1]) / 2 - _wcy) * _die_dy
                            ax.plot([_bx_c - 0.5, _bx_c + 0.5], [_by, _by],
                                    color='blue', linewidth=0.5, alpha=0.8, zorder=5)
                for _rx, _ry, _rlbl in _ret_labels_h:
                    ax.text(_rx, _ry, _rlbl,
                            ha='center', va='center', fontsize=3,
                            color='black', fontweight='bold', alpha=0.9, zorder=6)

            _draw_reticle(ax_heat)

            # Prepare HTML table data (no longer drawn in matplotlib)
            _info_rows = [
                ('BIN',               bin_field),
                ('FAIL BUCKET',       desc if desc else ''),
                ('YIELD (%)',         f"{int(round(comp_pct))}%"),
                ('EXPECTED YIELD (%)', expected_str if expected_str else ''),
            ]
            _lw_col_labels = ['LOT', 'WAFER'] + (['MATERIAL TYPE'] if mat_col else []) + ['Total Count', 'Count', '% FAIL']
            _lw_rows = lw_rows
            # TOTAL row: sum counts across all wafers
            _lw_total_count = sum(r[3 if mat_col else 2] for r in lw_rows)
            _lw_fail_count  = sum(r[4 if mat_col else 3] for r in lw_rows)
            _lw_total_pct   = (_lw_fail_count / _lw_total_count * 100
                               if _lw_total_count else 0.0)

            # adjust overall spacing
            try:
                fig.subplots_adjust(top=0.93, bottom=0.08, left=0.07, right=0.95)
            except Exception:
                pass

            def _esc(s):
                return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

            # ── Composite SVG — captured NOW before per-wafer loop changes
            # the matplotlib "current figure" and may affect fig's layout state.
            import io as _io_svg
            _comp_svg = None
            lw_svgs = []
            if not render_wafermap:
                print(f'  Bin Heatmap render skipped (render_wafermap=False) — stats only.')
            else:
                from xml.etree import ElementTree as _ET_svg
                _ET_svg.register_namespace('', 'http://www.w3.org/2000/svg')
                _ET_svg.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
                _comp_svgid = f'hmcomp_{_sanitize_label(bin_field)}'
                try:
                    _comp_buf = _io_svg.StringIO()
                    fig.savefig(_comp_buf, format='svg', bbox_inches='tight')
                    _comp_s = re.sub(r'<\?xml[^>]*\?>', '', _comp_buf.getvalue()).strip()
                    _comp_rt = _ET_svg.fromstring(_comp_s)
                    if _comp_rt.get('viewBox') is None:
                        _crw = _comp_rt.get('width', '800pt'); _crh = _comp_rt.get('height', '600pt')
                        def _cpt(s):
                            try: return float(re.sub(r'[^\d.]', '', s))
                            except: return 0
                        _comp_rt.set('viewBox', f'0 0 {_cpt(_crw):.1f} {_cpt(_crh):.1f}')
                    _comp_rt.attrib.pop('width', None); _comp_rt.attrib.pop('height', None)
                    _comp_rt.set('width', '100%'); _comp_rt.set('id', _comp_svgid)
                    _cidmap = {}
                    for _cel in _comp_rt.iter():
                        _coid = _cel.get('id')
                        if _coid and _coid != _comp_svgid:
                            _cnid = f'{_comp_svgid}_{_coid}'; _cidmap[_coid] = _cnid; _cel.set('id', _cnid)
                    if _cidmap:
                        def _cfu(v):
                            return re.sub(r'url\(#([^)]+)\)',
                                lambda m: f'url(#{_cidmap.get(m.group(1), m.group(1))})', v)
                        _cxlh = '{http://www.w3.org/1999/xlink}href'
                        for _cel in _comp_rt.iter():
                            for _ca, _cav in list(_cel.attrib.items()):
                                if 'url(#' in _cav: _cel.set(_ca, _cfu(_cav))
                                if _ca in (_cxlh, 'href') and _cav.startswith('#'):
                                    _cref = _cav[1:]
                                    if _cref in _cidmap: _cel.set(_ca, f'#{_cidmap[_cref]}')
                    _comp_svg = _ET_svg.tostring(_comp_rt, encoding='unicode')
                except Exception as _comp_exc:
                    print(f'  Warning: composite SVG failed ({_comp_exc}), will use PNG fallback', file=sys.stderr)
                    _comp_svg = None

                # ── Per-wafer SVGs for interactive lot/wafer table ──────────
                _group_ok = (lot_col or wafer_col) and hx is not None and hy is not None
                if not _group_ok:
                    print(f'  Per-wafer SVGs skipped: lot_col={lot_col!r} wafer_col={wafer_col!r} hx_ok={hx is not None} hy_ok={hy is not None}', file=sys.stderr)
                if _group_ok:
                    from xml.etree import ElementTree as ET
                    # Build (lot_str, wafer_str) -> row-index mapping using a plain
                    # 2-column groupby — avoids null-byte separator issues on Windows.
                    _lw_index_map = {}
                    if lot_col and wafer_col:
                        _lw_df = pd.DataFrame({
                            '_l': df[lot_col].fillna('').astype(str),
                            '_w': df[wafer_col].fillna('').astype(str),
                        })
                        for (_kl, _kw), _gs in _lw_df.groupby(['_l', '_w'], sort=False):
                            _lw_index_map[(str(_kl), str(_kw))] = _gs.index
                    elif lot_col:
                        _lot_ser = df[lot_col].fillna('').astype(str)
                        for _kl, _gs in _lot_ser.groupby(_lot_ser, sort=False):
                            _lw_index_map[(str(_kl), '')] = _gs.index
                    else:
                        _wfr_ser = df[wafer_col].fillna('').astype(str)
                        for _kw, _gs in _wfr_ser.groupby(_wfr_ser, sort=False):
                            _lw_index_map[(str(_kw), '')] = _gs.index
                    # Register SVG namespaces once outside the wafer loop
                    ET.register_namespace('', 'http://www.w3.org/2000/svg')
                    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
                    print(f'  Per-wafer SVGs: rendering {len(lw_rows)} wafer(s)…')
                    # Reuse a single figure across wafers to avoid construction overhead
                    _lw_fig = plt.figure(figsize=(fig_w, fig_h))
                    for _lw_r in lw_rows:
                        try:
                            _lv, _wv = _lw_r[0], _lw_r[1]
                            _lw_idx = _lw_index_map.get((_lv, _wv))
                            if _lw_idx is None or len(_lw_idx) == 0:
                                lw_svgs.append((_lv, _wv, None, None))
                                continue
                            _lx = hx[_lw_idx].values.astype(float)
                            _ly = hy[_lw_idx].values.astype(float)
                            _lmv = mask[_lw_idx].values
                            _cdf = pd.DataFrame({'x': _lx.astype(int), 'y': _ly.astype(int),
                                                 'm': _lmv.astype(np.int8)})
                            _cg = _cdf.groupby(['x', 'y'], sort=False)['m'].agg(['sum', 'count'])
                            _lw_xs = _cg.index.get_level_values('x').values.astype(float)
                            _lw_ys = _cg.index.get_level_values('y').values.astype(float)
                            _lw_vs = _cg['sum'].values / _cg['count'].values * 100
                            _lw_fig.clear()
                            _lw_ax = _lw_fig.add_subplot(111)
                            _lw_xp = _lw_xs - _wcx
                            _lw_yp = (_lw_ys - _wcy) * _die_dy
                            _vs_rint2 = np.rint(_lw_vs).astype(float)
                            _clrs2 = _cmap_obj(_norm_h(_vs_rint2))
                            for _di in range(len(_lw_xp)):
                                _lw_ax.add_patch(mpatches.Rectangle(
                                    (_lw_xp[_di] - _die_dx * gap / 2, _lw_yp[_di] - _die_dy * gap / 2),
                                    _die_dx * gap, _die_dy * gap,
                                    linewidth=0.3, edgecolor='gray', facecolor=_clrs2[_di],
                                    rasterized=True
                                ))
                            _lw_ax.set_title(f'Lot {_lv}  Wafer {_wv}  —  bin {bin_field}', fontsize=10)
                            _lw_ax.set_aspect('equal')
                            _lw_ax.set_xlim(-_xext_h, _xext_h)
                            _lw_ax.set_ylim(-_yext_h, _yext_h)
                            _lw_ax.set_xlabel('Sort X', fontsize=9)
                            _lw_ax.set_ylabel('Sort Y', fontsize=9)
                            _lw_ticks_y = [t for t in _lw_ax.get_yticks() if -_yext_h <= t <= _yext_h]
                            _lw_ax.set_yticks(_lw_ticks_y)
                            _lw_ax.set_yticklabels([f'{v / _die_dy + _wcy:.0f}' for v in _lw_ticks_y], fontsize=7)
                            _lw_ticks_x = [t for t in _lw_ax.get_xticks() if -_xext_h <= t <= _xext_h]
                            _lw_ax.set_xticks(_lw_ticks_x)
                            _lw_ax.set_xticklabels([f'{v + _wcx:.0f}' for v in _lw_ticks_x], fontsize=7)
                            _lw_ax.axhline(0, color='black', linewidth=0.5, linestyle='--', alpha=0.3)
                            _lw_ax.axvline(0, color='black', linewidth=0.5, linestyle='--', alpha=0.3)
                            _lw_ax.grid(True, alpha=0.2)
                            _draw_reticle(_lw_ax)
                            try:
                                _sm2 = plt.cm.ScalarMappable(cmap=_cmap_name, norm=_norm_h)
                                _sm2.set_array([])
                                _lw_fig.colorbar(_sm2, ax=_lw_ax, fraction=0.046, pad=0.04,
                                                 label='% yield' if _is_yield_bin else '% fail')
                            except Exception:
                                pass
                            _lw_fig.tight_layout()
                            _svgbuf = _io_svg.StringIO()
                            _lw_fig.savefig(_svgbuf, format='svg', bbox_inches='tight')
                            _svgs = re.sub(r'<\?xml[^>]*\?>', '', _svgbuf.getvalue()).strip()
                            _svgid = f'hmsvg_{_sanitize_label(str(_lv))}_{_sanitize_label(str(_wv))}_{_sanitize_label(bin_field)}'
                            try:
                                _rt = ET.fromstring(_svgs)
                                if _rt.get('viewBox') is None:
                                    _rw = _rt.get('width', '800pt'); _rh = _rt.get('height', '600pt')
                                    def _pt3(s):
                                        try: return float(re.sub(r'[^\d.]', '', s))
                                        except: return 0
                                    _rt.set('viewBox', f'0 0 {_pt3(_rw):.1f} {_pt3(_rh):.1f}')
                                _rt.attrib.pop('width', None); _rt.attrib.pop('height', None)
                                _rt.set('width', '100%'); _rt.set('id', _svgid)
                                _idmap2 = {}
                                for _el2 in _rt.iter():
                                    _oid2 = _el2.get('id')
                                    if _oid2 and _oid2 != _svgid:
                                        _nid2 = f'{_svgid}_{_oid2}'; _idmap2[_oid2] = _nid2; _el2.set('id', _nid2)
                                if _idmap2:
                                    def _fu2(v):
                                        return re.sub(r'url\(#([^)]+)\)',
                                            lambda m: f'url(#{_idmap2.get(m.group(1), m.group(1))})', v)
                                    _xlhref = '{http://www.w3.org/1999/xlink}href'
                                    for _el2 in _rt.iter():
                                        for _a2, _av2 in list(_el2.attrib.items()):
                                            if 'url(#' in _av2: _el2.set(_a2, _fu2(_av2))
                                            if _a2 in (_xlhref, 'href') and _av2.startswith('#'):
                                                _ref2 = _av2[1:]
                                                if _ref2 in _idmap2: _el2.set(_a2, f'#{_idmap2[_ref2]}')
                                _svgs = ET.tostring(_rt, encoding='unicode')
                            except Exception:
                                pass
                            lw_svgs.append((_lv, _wv, _svgs, _svgid))
                        except Exception as _lw_exc:
                            print(f'    Warning: per-wafer SVG failed for ({_lv}, {_wv}): {_lw_exc}', file=sys.stderr)
                            lw_svgs.append((_lv, _wv, None, None))
                    plt.close(_lw_fig)  # close shared figure after all wafers are done

            def _build_heatmap_html(b64_png, lw_svgs=None, composite_svg=None):
                info_rows_html = ''.join(
                    f'<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>\n'
                    for k, v in _info_rows
                )
                # Build SVG containers (hidden by default)
                svg_blocks = ''
                svg_id_map = {}  # (lot, wafer) -> svgid
                if lw_svgs:
                    for _lv2, _wv2, _svgstr, _sid in lw_svgs:
                        if _svgstr and _sid:
                            svg_id_map[(_lv2, _wv2)] = _sid
                            svg_blocks += (
                                f'<div id="wrap_{_sid}" class="hm-wafer-view" '
                                f'style="display:none;width:570px;min-width:200px">'
                                f'{_svgstr}</div>\n'
                            )
                lw_rows_html = ''
                for r in _lw_rows:
                    _sid2 = svg_id_map.get((r[0], r[1]))
                    _onclick = f' onclick="hmShowWafer(\'{_sid2}\',this)"' if _sid2 else ''
                    _cursor = ' cursor:pointer;' if _sid2 else ''
                    lw_rows_html += (
                        f'<tr class="lw-row"{_onclick} style="{_cursor}">'
                        f'<td>{_esc(r[0])}</td>'
                        f'<td>{_esc(r[1])}</td>'
                        + (f'<td>{_esc(r[2])}</td>' if mat_col else '')
                        + f'<td class="num">{r[3 if mat_col else 2]:,}</td>'
                        f'<td class="num">{r[4 if mat_col else 3]:,}</td>'
                        f'<td class="num">{_esc(r[5 if mat_col else 4])}</td>'
                        f'</tr>\n'
                    )
                lw_total_html = (
                    f'<tr style="font-weight:bold;background:#dde8f7">'
                    + (f'<td colspan="3">TOTAL</td>' if mat_col else f'<td colspan="2">TOTAL</td>')
                    + f'<td class="num">{_lw_total_count:,}</td>'
                    f'<td class="num">{_lw_fail_count:,}</td>'
                    f'<td class="num">{_lw_total_pct:.2f}%</td>'
                    f'</tr>\n'
                )
                lw_header = ''.join(f'<th>{_esc(c)}</th>' for c in _lw_col_labels)
                _has_wafer_svgs = bool(svg_blocks)
                _js = ''
                if _has_wafer_svgs:
                    _js = """
<script>
function hmShowWafer(svgId, row) {
  var allViews = document.querySelectorAll('.hm-wafer-view');
  var allRows  = document.querySelectorAll('.lw-row');
  var composite = document.getElementById('hm-composite');
  var alreadyActive = row.classList.contains('lw-active');
  allViews.forEach(function(d){ d.style.display='none'; });
  allRows.forEach(function(r){ r.classList.remove('lw-active'); r.style.background=''; });
  if (alreadyActive) {
    composite.style.display='block';
  } else {
    composite.style.display='none';
    var el = document.getElementById('wrap_' + svgId);
    if (el) el.style.display='block';
    row.classList.add('lw-active');
    row.style.background='#ddeeff';
  }
}
</script>
<style>
.lw-row:hover td { background:#f0f4ff !important; cursor:pointer; }
.lw-row.lw-active td { background:#ddeeff !important; }
</style>"""
                _hint = ' <span style="font-size:10px;font-weight:normal;color:#666">(click row to show wafer map)</span>' if _has_wafer_svgs else ''
                _comp_inner = (composite_svg if composite_svg
                               else f'<img src="data:image/png;base64,{b64_png}" style="max-width:100%;height:auto"/>')
                # ── hardware breakdown modal ───────────────────────────────────
                import json as _json_hw_hm
                _hw_css = _hw_btn = _hw_modal = _hw_js_hm = ''
                if _hw_cols_hm and __rows:
                    _hw_css = (
                        '.hm-hw-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;'
                        'background:rgba(0,0,0,.5);z-index:9999;align-items:center;justify-content:center}'
                        '.hm-hw-overlay.open{display:flex}'
                        '.hm-hw-box{background:#fff;border-radius:6px;padding:16px;max-width:90vw;'
                        'max-height:80vh;overflow:auto;min-width:400px;box-shadow:0 8px 32px rgba(0,0,0,.3)}'
                        '.hm-hw-tbl{border-collapse:collapse;font-size:11px;width:100%}'
                        '.hm-hw-tbl th{background:#2c3e50;color:#fff;padding:4px 10px;'
                        'white-space:nowrap;text-align:left}'
                        '.hm-hw-tbl td{padding:3px 10px;border-bottom:1px solid #eee;white-space:nowrap}'
                        '.hm-hw-tbl tr:hover td{background:#f0f4ff}'
                    )
                    _hw_btn = (
                        f'<button onclick="document.getElementById(\'hm-hw-modal\').classList.add(\'open\')" '
                        f'style="margin:4px 0 8px;padding:4px 12px;font-size:11px;cursor:pointer;'
                        f'border:1px solid #2980b9;background:#ebf5fb;border-radius:3px;color:#1a5276">'
                        f'&#9741; Hardware Breakdown</button>'
                    )
                    _hw_modal = (
                        f'<div id="hm-hw-modal" class="hm-hw-overlay" '
                        f'onclick="if(event.target===this)this.classList.remove(\'open\')">'
                        f'<div class="hm-hw-box">'
                        f'<div style="display:flex;align-items:center;margin-bottom:10px">'
                        f'<b style="font-size:13px;flex:1">Hardware Commonality \u2014 Bin {_esc(bin_field)}</b>'
                        f'<button onclick="document.getElementById(\'hm-hw-modal\').classList.remove(\'open\')" '
                        f'style="border:none;background:none;font-size:18px;cursor:pointer;color:#666">'
                        f'&times;</button></div>'
                        f'<div id="hm-hw-table"></div></div></div>'
                    )
                    _hw_data_j = _json_hw_hm.dumps({'cols': _hw_cols_hm, 'rows': __rows})
                    _hw_js_hm = (
                        '<script>(function(){'
                        'var d=' + _hw_data_j + ';var cols=d.cols;var rows=d.rows;'
                        'var _gbCols=new Set(cols);'
                        'function _render(){'
                        'var activeCols=cols.filter(function(c){return _gbCols.has(c);});'
                        'var displayCols=activeCols.length>0?activeCols:cols;'
                        'var groupMap={};'
                        'rows.forEach(function(r){'
                        '  var key=activeCols.length>0?activeCols.map(function(c){return String(r[c]||"");}).join("\\x00"):("__idx__"+rows.indexOf(r));'
                        '  if(!groupMap[key])groupMap[key]={row:r,cnt:0};'
                        '  groupMap[key].cnt+=r.count;'
                        '});'
                        'var grouped=Object.values(groupMap).sort(function(a,b){return b.cnt-a.cnt;});'
                        'var totalCnt=grouped.reduce(function(s,e){return s+e.cnt;},0);'
                        'var t=document.getElementById("hm-hw-table");'
                        'var gbBar=\'<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:5px 6px;background:#f0f4ff;border-radius:4px;border:1px solid #c5d4f0;margin-bottom:8px">\''
                        '  +\'<span style="font-size:11px;font-weight:bold;color:#2c3e50;white-space:nowrap">Group By:</span>\';'
                        'cols.forEach(function(c){'
                        '  var chk=_gbCols.has(c)?"checked":"";'
                        '  gbBar+=\'<label style="font-size:11px;display:flex;align-items:center;gap:3px;cursor:pointer;white-space:nowrap">\''
                        '    +\'<input type="checkbox" \'+chk+\' data-gbcol="\'+c.replace(/"/g,"&quot;")+\'" onchange="_hmHwGbChange(this)"> \'+c+\'</label>\';'
                        '});'
                        'gbBar+=\'<button onclick="_hmHwGbAll()" style="font-size:11px;padding:2px 8px;cursor:pointer;border:1px solid #888;border-radius:3px;background:#fff">All</button>\''
                        '  +\'<button onclick="_hmHwGbNone()" style="font-size:11px;padding:2px 8px;cursor:pointer;border:1px solid #888;border-radius:3px;background:#fff">None</button></div>\';'
                        'var th=displayCols.map(function(c){return"<th>"+c+"</th>";}).join("")'
                        '  +"<th>Count</th><th>%</th>";'
                        'var tb=grouped.map(function(e){'
                        '  var pct=totalCnt>0?(e.cnt/totalCnt*100).toFixed(1)+"%" :"0.0%";'
                        '  return"<tr>"+displayCols.map(function(c){return"<td>"+String(e.row[c]||"")+"</td>";}).join("")'
                        '    +"<td style=\'text-align:right\'>"+e.cnt+"</td><td style=\'text-align:right\'>"+pct+"</td></tr>";'
                        '}).join("");'
                        't.innerHTML=gbBar+"<table class=\'hm-hw-tbl\'><thead><tr>"+th+"</tr></thead><tbody>"+tb+"</tbody></table>";'
                        '}'
                        'window._hmHwGbChange=function(cb){'
                        '  var c=cb.getAttribute("data-gbcol");'
                        '  if(cb.checked){_gbCols.add(c);}else{_gbCols.delete(c);}'
                        '  _render();};'
                        'window._hmHwGbAll=function(){cols.forEach(function(c){_gbCols.add(c);});_render();};'
                        'window._hmHwGbNone=function(){_gbCols.clear();_render();};'
                        '_render();'
                        '})()</script>'
                    )
                return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body{{margin:0;padding:8px;background:#fff;font-family:Arial,sans-serif;font-size:12px}}
img{{max-width:100%;height:auto;display:block;margin-bottom:14px}}
.info-table,.lw-table{{border-collapse:collapse;font-size:11px;margin-bottom:14px}}
.info-table td,.lw-table th,.lw-table td{{padding:3px 10px;border:1px solid #ccc;white-space:nowrap;text-align:left}}
.info-table td:first-child,.lw-table th{{font-weight:bold;background:#f0f0f0}}
.lw-table tr:hover td{{background:#f5f5f5}}
.num{{text-align:right}}
#hm-composite{{max-width:700px}}
{_hw_css}</style>{_js}</head><body>
<div id="hm-composite">{_comp_inner}</div>
{svg_blocks}
<table class="info-table">{info_rows_html}</table>
{_hw_btn}{_hw_modal}
<h3 style="font-size:12px;margin:8px 0 4px">Lot / Wafer breakdown{_hint}</h3>
<table class="lw-table"><thead><tr>{lw_header}</tr></thead>
<tbody>{lw_rows_html}{lw_total_html}</tbody></table>
{_hw_js_hm}</body></html>"""

            # When running in GUI mode, place heatmap PNGs under output/heatmap
            if gui:
                heat_dir = outd / 'heatmap'
                heat_dir.mkdir(parents=True, exist_ok=True)
                out_name = heat_dir / f"{csvp.stem}_Heatmap_bin_{_sanitize_label(bin_field)}.png"
            else:
                out_name = outd / f"{csvp.stem}_Heatmap_bin_{_sanitize_label(bin_field)}.png"
            # If html_only requested, save only HTML with inline SVG
            svg_out = None
            html_out = None
            if html_only:
                try:
                    heat_dir = outd / 'heatmap' if gui else outd
                    heat_dir.mkdir(parents=True, exist_ok=True)
                    html_out = heat_dir / f"{csvp.stem}_Heatmap_bin_{_sanitize_label(bin_field)}.html"
                    if _comp_svg:
                        html_out.write_text(_wm_inject(_build_heatmap_html(None, lw_svgs, _comp_svg)), encoding='utf-8')
                    else:
                        import io as _io_fb, base64 as _b64_fb
                        _fb = _io_fb.BytesIO()
                        fig.savefig(_fb, format='png', dpi=200, bbox_inches='tight')
                        _fb.seek(0)
                        html_out.write_text(_wm_inject(_build_heatmap_html(
                            _b64_fb.b64encode(_fb.read()).decode('ascii'), lw_svgs)), encoding='utf-8')
                except Exception:
                    html_out = None
                plt.close(fig)
                if html_out:
                    print('Wrote', html_out)
                continue

            # Save PNG (plot only) and HTML (inline SVG + tables)
            try:
                fig.savefig(out_name, dpi=200, bbox_inches='tight')
            except Exception:
                fig.savefig(out_name, dpi=200)

            # HTML: inline SVG composite (or fallback PNG) + tables
            try:
                html_out = out_name.with_suffix('.html')
                if _comp_svg:
                    html_out.write_text(_wm_inject(_build_heatmap_html(None, lw_svgs, _comp_svg)), encoding='utf-8')
                else:
                    import base64
                    b64 = base64.b64encode(out_name.read_bytes()).decode('ascii')
                    html_out.write_text(_wm_inject(_build_heatmap_html(b64, lw_svgs)), encoding='utf-8')
            except Exception:
                html_out = None
            plt.close(fig)
            print('Wrote', out_name)
            if html_out:
                print('Wrote', html_out)

    # Generate the combined all-IBIN wafer map at the end
    if render_ibin_wafermap:
        try:
            _wm_col = bin_col if bin_col in df.columns else next(
                (c for c in df.columns if 'INTERFACE_BIN' in c.upper() and 'TOTAL' not in c.upper()), bin_col)
            generate_all_ibin_wafer_map(csv_path, out_dir=out_dir, bin_col=_wm_col, gui=gui, interactive=interactive, tbl_path=tbl_path)
        except Exception as _wm_exc:
            print(f'Warning: all-IBIN wafer map skipped: {_wm_exc}', file=sys.stderr)
    else:
        print('  Wafermap (IBIN) skipped (render_ibin_wafermap=False).')


def generate_all_ibin_wafer_map(csv_path, out_dir=None, bin_col=None, gui=False, interactive=True, bindef_path=None, tbl_path=None):
    """
    Generate one figure per lot, each showing a grid of per-wafer scatter maps
    colored by IBIN category.

    Pass bins  (IBIN 1-4)  → green family
    Fail bins  (IBIN > 4)  → distinct colors, one per bin number

    interactive=True  → inline SVG per wafer + clickable JS legend (default)
    interactive=False → PNG per wafer + static HTML legend (no JavaScript)

    Writes  <stem>_IBIN_WaferMap_<lot>.html  into  out_dir/heatmap/  (one per lot).
    Returns list of (lot_label, html_path) tuples.
    """
    import math
    try:
        import io, base64
        import matplotlib.patches as mpatches

        csvp = Path(csv_path)
        outd = Path(out_dir) if out_dir else csvp.parent / 'output'
        heat_dir = outd / 'heatmap'
        heat_dir.mkdir(parents=True, exist_ok=True)

        df = pd.read_csv(csvp, dtype=object)

        # ── auto-detect IBIN column ────────────────────────────────────────
        if bin_col is None or bin_col not in df.columns:
            bin_col = next((c for c in df.columns if 'INTERFACE_BIN' in c.upper() and 'TOTAL' not in c.upper()), None)
        if bin_col is None:
            print('generate_all_ibin_wafer_map: no INTERFACE_BIN column found', file=sys.stderr)
            return []

        # ── coordinate columns ─────────────────────────────────────────────
        x_col, y_col = find_coord_columns(df)
        if not x_col or not y_col:
            print('generate_all_ibin_wafer_map: no Sort_X/Sort_Y columns found', file=sys.stderr)
            return []
        hx = _to_coord_series(df, x_col)
        hy = _to_coord_series(df, y_col)
        if hx is None or hy is None:
            print('generate_all_ibin_wafer_map: could not parse numeric coordinates', file=sys.stderr)
            return []

        # ── lot / wafer columns ────────────────────────────────────────────
        # Prefer SORT_LOT (common to all merged CSVs) to avoid picking a
        # product-specific column (e.g. LOTFROMFS) that is NaN for other products.
        lot_col = next((c for c in df.columns if c.lower() == 'sort_lot'), None)
        if lot_col is None:
            lot_col = next((c for c in df.columns if c.lower() == 'lot'), None)
        if lot_col is None:
            lot_col = next((c for c in df.columns if 'lot' in c.lower() and 'slot' not in c.lower()), None)
        wafer_col = next((c for c in df.columns if 'sort_wafer' in c.lower()), None)
        if wafer_col is None:
            wafer_col = next((c for c in df.columns if c.lower() == 'wafer'), None)
        if wafer_col is None:
            wafer_col = next((c for c in df.columns if 'wafer' in c.lower()), None)
        mat_col_wm = next((c for c in df.columns if 'material' in c.lower()), None)
        fb_col_wm  = next((c for c in df.columns
                           if 'FUNCTIONAL_BIN' in c.upper() and 'TOTAL' not in c.upper()), None)

        # ── hardware commonality columns (prefix match; real columns may have _XXXXXX suffix) ──
        _HW_FIELD_PREFIXES_WM = ["Cell ID", "Unit Tester Id", "Unit Tester Site Id", "Unit TIU", "Thermal Head Id", "Sort Partial Wafer ID"]
        _hw_cols_wm = [
            next((c for c in df.columns if c.lower().startswith(pfx.lower())), None)
            for pfx in _HW_FIELD_PREFIXES_WM
        ]
        _hw_cols_wm = [c for c in _hw_cols_wm if c is not None]

        # ── FB descriptions from bindef CSV ───────────────────────────────
        _fb_desc_wm: dict = {}
        try:
            _bm_paths_wm = []
            if bindef_path and Path(bindef_path).exists():
                _bm_paths_wm.append(Path(bindef_path))
            _bm_out_wm = Path(out_dir) if out_dir else csvp.parent / 'output'
            for _bm_search_wm in [_bm_out_wm, csvp.parent]:
                for _bm_cand_wm in sorted(Path(_bm_search_wm).glob('*_bindef.csv')):
                    if _bm_cand_wm not in _bm_paths_wm:
                        _bm_paths_wm.append(_bm_cand_wm)
                for _bm_cand_wm in sorted(Path(_bm_search_wm).glob('*bindef*.csv')):
                    if _bm_cand_wm not in _bm_paths_wm:
                        _bm_paths_wm.append(_bm_cand_wm)
            for _bm_p_wm in _bm_paths_wm:
                if _bm_p_wm.exists() and _bm_p_wm.suffix.lower() == '.csv':
                    try:
                        _bd_df_wm = pd.read_csv(str(_bm_p_wm), encoding='utf-8', header=0,
                                                 on_bad_lines='skip')
                        if _bd_df_wm.shape[1] >= 2:
                            for _, _bd_row_wm in _bd_df_wm.iterrows():
                                _bd_key_wm = str(_bd_row_wm.iloc[0]).strip()
                                _bd_val_wm = str(_bd_row_wm.iloc[1]).strip()
                                _bd_m_wm = re.match(r'^FB(\d+)$', _bd_key_wm, re.IGNORECASE)
                                if _bd_m_wm:
                                    _fb_desc_wm[_bd_m_wm.group(1)] = _bd_val_wm
                        if _fb_desc_wm:
                            break
                    except Exception:
                        pass
        except Exception:
            pass

        # ── fB93xx (handler/skip bin) descriptions from product config JSON ──
        try:
            import json as _json93_wm, re as _re93_wm
            _yt_path93_wm = None
            if tbl_path and Path(tbl_path).exists():
                _yt_path93_wm = Path(tbl_path)
            elif bindef_path and Path(bindef_path).suffix.lower() == '.json' and Path(bindef_path).exists():
                _yt_path93_wm = Path(bindef_path)
            if _yt_path93_wm:
                _raw93_wm = _yt_path93_wm.read_text(encoding='utf-8')
                _jdata93_wm = None
                try:
                    _jdata93_wm = _json93_wm.loads(_raw93_wm)
                except Exception:
                    pass
                _fb93xx_list_wm = []
                if _jdata93_wm and isinstance(_jdata93_wm, dict):
                    _fb93xx_list_wm = _jdata93_wm.get('fB93xx', [])
                if not _fb93xx_list_wm:
                    _m93_wm = _re93_wm.search(r'"fB93xx"\s*:\s*(\[.*?\])', _raw93_wm, _re93_wm.DOTALL)
                    if _m93_wm:
                        try:
                            _fb93xx_list_wm = _json93_wm.loads(_m93_wm.group(1))
                        except Exception:
                            pass
                for _e93_wm in _fb93xx_list_wm:
                    if isinstance(_e93_wm, dict) and 'FB' in _e93_wm and 'description' in _e93_wm:
                        _fb_desc_wm[str(_e93_wm['FB'])] = str(_e93_wm['description'])
        except Exception:
            pass

        # ── global axis limits (consistent across all subplots / lots) ────
        x_min, x_max = int(hx.min()), int(hx.max())
        y_min, y_max = int(hy.min()), int(hy.max())
        nx = x_max - x_min + 1
        ny = y_max - y_min + 1

        # ── colour scheme ──────────────────────────────────────────────────
        table_rows = _load_fail_bucket_table(None)
        _PASS_COLORS = {'1': '#00ff44', '2': '#00ff44', '3': '#3d3d3d', '4': '#b0b0b0'}
        _PASS_LABEL  = {
            '1': 'Bin 1  Pass (FF NoRepair)',
            '2': 'Bin 2  Pass (MBIST Repair)',
            '3': 'Bin 3  Pass (Atom Defeatured)',
            '4': 'Bin 4  Pass (Core Defeatured)',
        }
        _PASS_HATCH = {'1': '', '2': '///', '3': '', '4': ''}
        # edgecolor per pass bin — controls hatch line color
        _PASS_EDGE  = {'1': 'gray', '2': '#005500', '3': 'gray', '4': 'gray'}

        # Build per-bin-number description lookup from the fail bucket table
        # e.g. '26' -> 'HVQK', '7' -> 'TPI (Foundry)'
        _bin_desc_map: dict = {}
        for _tup in table_rows:
            _bin_field = str(_tup[0])
            _desc = _tup[1] if len(_tup) >= 2 else ''
            # description may be in column 1 (builtin) or column 2 (parsed); pick non-numeric one
            if _desc and re.search(r'\d', _desc) and len(_tup) >= 3 and _tup[2] and not re.search(r'\d', str(_tup[2])):
                _desc = str(_tup[2])
            for _bn in re.findall(r'\d+', _bin_field):
                if _bn not in _bin_desc_map and _desc:
                    _bin_desc_map[_bn] = _desc

        # Pre-assign fail colors and hatches in ascending numeric order across ALL data
        # so the same bin always gets the same color/hatch regardless of lot/wafer order.
        _fail_ibin_color: dict = {}
        _fail_ibin_hatch: dict = {}
        all_fail_nums = sorted(
            {n for cell in df[bin_col].fillna('').astype(str)
               for n in re.findall(r'\d+', cell) if n not in _PASS_COLORS},
            key=lambda s: int(s)
        )
        # Paired (color, hatch) palette - no greens, green = pass bins 1-4.
        # Bright/vivid entries are solid (hatch=''). Darker secondary variants carry
        # a pre-assigned hatch so they look distinct from their brighter siblings.
        # Each bin gets a UNIQUE palette slot; no two bins share a color.
        _FAIL_PALETTE = [
            # --- vivid solid: bright red, orange, blue, purple, pink, cyan ---
            ('#ff0000', ''),   ('#ff6600', ''),   ('#ff8800', ''),   ('#ffcc00', ''),
            ('#0055ff', ''),   ('#00aaff', ''),   ('#aa00ff', ''),   ('#cc00ff', ''),
            ('#ff0066', ''),   ('#ff33aa', ''),   ('#00bbee', ''),   ('#ff3333', ''),
            ('#6699ff', ''),   ('#cc0099', ''),   ('#ffaa00', ''),   ('#336bff', ''),
            # --- medium solid ---
            ('#cc0000', ''),   ('#cc4400', ''),   ('#cc9900', ''),   ('#0033cc', ''),
            ('#6600cc', ''),   ('#dd4499', ''),   ('#dd2288', ''),   ('#0099cc', ''),
            ('#ff6666', ''),   ('#ffdd55', ''),   ('#5500cc', ''),   ('#ff5500', ''),
            # --- darker -> pre-hatched to differ visually from medium variants ---
            ('#990000', '///'),  ('#994400', '///'),  ('#cc7700', 'xxx'),  ('#003399', 'xxx'),
            ('#660099', 'xxx'),  ('#005580', '+++'),  ('#990066', '+++'),  ('#003d5c', '---'),
            ('#660000', '////'), ('#cc3300', '////'), ('#e6b800', 'xxxx'), ('#000099', 'xxxx'),
            ('#330066', '++++'), ('#7700aa', '++++'), ('#550000', '||||'), ('#1a0066', '||||'),
        ]
        _FAIL_PALETTE_N = len(_FAIL_PALETTE)

        def _md5f(n_int: int, salt: str) -> float:
            """Deterministic float in [0,1) from bin number + salt via MD5."""
            h = hashlib.md5(f'{salt}:{n_int}'.encode()).hexdigest()
            return int(h[:8], 16) / 0xFFFFFFFF

        # Assign palette entries - each bin gets a unique slot.
        # MD5 picks initial index; bump forward if taken.
        _assigned_indices: set = set()
        for n in sorted(all_fail_nums, key=lambda s: int(s) if s.isdigit() else 0):
            try:
                ni = int(n)
            except ValueError:
                ni = hash(n) & 0xFFFF
            idx = int(_md5f(ni, 'color') * _FAIL_PALETTE_N) % _FAIL_PALETTE_N
            for _off in range(_FAIL_PALETTE_N):
                candidate = (idx + _off) % _FAIL_PALETTE_N
                if candidate not in _assigned_indices:
                    idx = candidate
                    break
            _assigned_indices.add(idx)
            _fail_ibin_color[n] = _FAIL_PALETTE[idx][0]
            _fail_ibin_hatch[n] = _FAIL_PALETTE[idx][1]

        def _get_fail_color(n_str: str) -> str:
            if n_str not in _fail_ibin_color:
                try:
                    ni = int(n_str)
                except ValueError:
                    ni = hash(n_str) & 0xFFFF
                idx = int(_md5f(ni, 'color') * _FAIL_PALETTE_N) % _FAIL_PALETTE_N
                _fail_ibin_color[n_str] = _FAIL_PALETTE[idx][0]
                _fail_ibin_hatch[n_str] = _FAIL_PALETTE[idx][1]
            return _fail_ibin_color[n_str]
            return _fail_ibin_color[n_str]

        def _get_fail_hatch(n_str: str) -> str:
            _get_fail_color(n_str)  # ensure assigned
            return _fail_ibin_hatch.get(n_str, '')

        def _classify(cell):
            """Returns (label, facecolor, hatch, edgecolor)."""
            nums = re.findall(r'\d+', str(cell))
            if not nums:
                return 'unknown', '#95a5a6', '', 'gray'
            n = nums[0]
            if n in _PASS_COLORS:
                return (_PASS_LABEL.get(n, f'Bin {n} Pass'),
                        _PASS_COLORS[n],
                        _PASS_HATCH.get(n, ''),
                        _PASS_EDGE.get(n, 'gray'))
            desc = _bin_desc_map.get(n, '')
            lbl = f'Bin {n}  {desc}' if desc else f'Bin {n}'
            return lbl, _get_fail_color(n), _get_fail_hatch(n), 'gray'

        def _leg_order(item):
            lbl, _ = item  # item = (lbl, (clr, htch))
            m = re.search(r'Bin (\d+)', lbl)
            if 'Pass' in lbl:
                return (0, int(m.group(1)) if m else 99)
            return (1, int(m.group(1)) if m else 9999)

        # ── classify all rows ──────────────────────────────────────────────
        labels_col  = df[bin_col].fillna('').astype(str).apply(lambda c: _classify(c)[0])
        colors_col  = df[bin_col].fillna('').astype(str).apply(lambda c: _classify(c)[1])
        hatches_col = df[bin_col].fillna('').astype(str).apply(lambda c: _classify(c)[2])
        edges_col   = df[bin_col].fillna('').astype(str).apply(lambda c: _classify(c)[3])

        def _bin_short_label(cell):
            """Return just the bin number as a short string for die annotation, empty for pass bins."""
            nums = re.findall(r'\d+', str(cell))
            if not nums:
                return ''
            return '' if nums[0] in _PASS_COLORS else nums[0]
        bin_short_col = df[bin_col].fillna('').astype(str).apply(_bin_short_label)

        # Build working frame with lot/wafer assignments
        work = pd.DataFrame({
            'x':        hx.values,
            'y':        hy.values,
            'label':    labels_col.values,
            'color':    colors_col.values,
            'hatch':    hatches_col.values,
            'edge':     edges_col.values,
            'bin_short': bin_short_col.values,
        })
        work['_lot']   = df[lot_col].fillna('UNKNOWN').astype(str).values   if lot_col   else 'ALL'
        work['_wafer'] = df[wafer_col].fillna('UNKNOWN').astype(str).values if wafer_col else 'ALL'
        work['_material'] = df[mat_col_wm].fillna('').astype(str).values if mat_col_wm else ''
        # Embed first numeric FB per die so JS can filter by FB on the wafer map
        if fb_col_wm:
            work['_fb'] = (
                df[fb_col_wm].fillna('').astype(str)
                .apply(lambda v: re.findall(r'\d+', v)[0] if re.findall(r'\d+', v) else '0')
                .values
            )
        else:
            work['_fb'] = '0'

        # Embed HW combo index per die so JS can highlight by HW selection
        _hw_combo_table_js = []  # list of {col: val, ...} — indexed by position = data-hw value
        if _hw_cols_wm:
            _hw_key_series = df[_hw_cols_wm].fillna('').astype(str).apply(
                lambda r: '\x1f'.join(r.values), axis=1)
            _hw_combo_uniq = list(dict.fromkeys(_hw_key_series))
            _hw_combo_idx_map = {k: i for i, k in enumerate(_hw_combo_uniq)}
            work['_hw_idx'] = _hw_key_series.map(_hw_combo_idx_map).fillna(0).astype(int).values
            for _hwck in _hw_combo_uniq:
                _hwcvals = _hwck.split('\x1f')
                _hw_combo_table_js.append(dict(zip(_hw_cols_wm, _hwcvals)))
        else:
            work['_hw_idx'] = 0

        # ── pre-compute hardware breakdown per bin number ──────────────────────
        _hw_data_wm = {}  # {bin_num_str: [row_dicts]}
        _hw_fb_data_wm = {}  # {bin_num_str: {fb_str: [row_dicts]}} — per-FB breakdown for JS reactivity
        if _hw_cols_wm:
            for _bn_hw in sorted(all_fail_nums + list(_PASS_COLORS.keys()),
                                 key=lambda s: int(s) if s.isdigit() else 0):
                _pat_hw = r'(?<!\d)' + re.escape(_bn_hw) + r'(?!\d)'
                _bn_mask_hw = df[bin_col].fillna('').astype(str).str.contains(
                    _pat_hw, regex=True, na=False)
                _bn_total_hw = int(_bn_mask_hw.sum())
                if _bn_total_hw == 0:
                    continue
                try:
                    _hw_grp = (
                        df.loc[_bn_mask_hw, _hw_cols_wm]
                        .fillna('').astype(str)
                        .groupby(_hw_cols_wm, sort=False).size()
                        .reset_index(name='count')
                    )
                    _hw_grp = _hw_grp.sort_values('count', ascending=False).head(100)
                    _hw_rows_wm = []
                    for _, _hr in _hw_grp.iterrows():
                        _hrow = {c: str(_hr[c]) for c in _hw_cols_wm}
                        _hrow['count'] = int(_hr['count'])
                        _hrow['pct'] = f"{_hr['count'] / _bn_total_hw * 100:.1f}%"
                        _hw_rows_wm.append(_hrow)
                    _hw_data_wm[_bn_hw] = _hw_rows_wm
                    # Per-FB breakdown so JS can filter by selected FBs reactively
                    if fb_col_wm:
                        try:
                            _bn_sub_hw = df.loc[_bn_mask_hw].copy()
                            _bn_sub_hw['_hwfb'] = (
                                _bn_sub_hw[fb_col_wm].fillna('').astype(str)
                                .apply(lambda v: re.findall(r'\d+', v)[0] if re.findall(r'\d+', v) else '0')
                            )
                            for _fbv_hw, _fbv_grp_hw in _bn_sub_hw.groupby('_hwfb', sort=False):
                                _hw_fb_grp = (
                                    _fbv_grp_hw[_hw_cols_wm]
                                    .fillna('').astype(str)
                                    .groupby(_hw_cols_wm, sort=False).size()
                                    .reset_index(name='count')
                                )
                                _hw_fb_grp = _hw_fb_grp.sort_values('count', ascending=False).head(50)
                                _hw_fb_rows_wm = []
                                for _, _hfr in _hw_fb_grp.iterrows():
                                    _hfrow = {c: str(_hfr[c]) for c in _hw_cols_wm}
                                    _hfrow['count'] = int(_hfr['count'])
                                    _hw_fb_rows_wm.append(_hfrow)
                                _hw_fb_data_wm.setdefault(_bn_hw, {})[str(_fbv_hw)] = _hw_fb_rows_wm
                        except Exception:
                            pass
                except Exception:
                    pass

        # ── detect LayoutX / LayoutY / Reticle columns for reticle overlay ─
        _lx_col = next((c for c in df.columns if c.lower() in
                        ('layoutx', 'layout_x')), None)
        _ly_col = next((c for c in df.columns if c.lower() in
                        ('layouty', 'layout_y')), None)
        _ret_col = next((c for c in df.columns if c.lower() in
                         ('reticle', 'reticle_number', 'reticlenumber')), None)
        _has_reticle = bool(_lx_col and _ly_col)
        if _has_reticle:
            work['_layoutx']  = pd.to_numeric(df[_lx_col], errors='coerce').values
            work['_layouty']  = pd.to_numeric(df[_ly_col], errors='coerce').values
            if _ret_col:
                work['_reticle'] = df[_ret_col].values
            print(f'Reticle overlay enabled: LayoutX={_lx_col}, LayoutY={_ly_col}'
                  + (f', Reticle={_ret_col}' if _ret_col else ''))

        # ── Load reticle shot bboxes + die-loc numbers from shared reticle CSV ─
        # These are passed to _draw_wafer_on_ax for both composite and per-wafer maps.
        # Works for all reticle types defined in shared/reticle/ (8PF5CV, 8PF6CV, 8PY6CV, …).
        _ret_shots_data: list = []   # [[xMin_SX, yMin_SY, xMax_SX, yMax_SY], …] per shot
        _ret_die_num: dict  = {}     # {(SX, SY): reticle_num_int} — die-loc label per die
        try:
            import glob as _glob_ret
            _ret_cands_wm: list = []
            # (a) search shared/reticle/ by walking up from this script's location
            _rb_wm = Path(__file__).resolve().parent
            for _ in range(12):
                if (_rb_wm / 'shared' / 'reticle').is_dir():
                    _ret_cands_wm.extend(
                        _glob_ret.glob(str(_rb_wm / 'shared' / 'reticle' / '*.csv')))
                    break
                _rb_wm = _rb_wm.parent
            # (b) also search collateral/reticle/ near the data CSV
            _srch_wm = csvp.parent
            for _ in range(5):
                for _rpat_wm in ['collateral/reticle/*.csv', 'collateral/Reticle/*.csv']:
                    _ret_cands_wm.extend(_glob_ret.glob(str(_srch_wm / _rpat_wm)))
                _srch_wm = _srch_wm.parent
            # deduplicate & keep only files with 'reticle' in the name
            _ret_cands_wm = sorted({p for p in _ret_cands_wm
                                    if Path(p).is_file() and 'reticle' in Path(p).name.lower()})
            # filter by DevRevStep prefix (e.g. 8PF6CV, 8PF5CV) so the right layout is used
            _drs_col_wm = next((c for c in df.columns if c.lower().startswith('devrevstep')), None)
            _drs_pfx_wm = ''
            if _drs_col_wm:
                _drs_pfx_wm = next(
                    (str(v).strip()[:6].upper() for v in df[_drs_col_wm].dropna() if str(v).strip()),
                    '')
            if _drs_pfx_wm and _ret_cands_wm:
                _filt_wm = [p for p in _ret_cands_wm if _drs_pfx_wm in Path(p).name.upper()]
                if _filt_wm:
                    _ret_cands_wm = _filt_wm
            if _ret_cands_wm:
                _rdf_wm = pd.read_csv(_ret_cands_wm[0])
                _rca_wm = {c.lower().replace(' ', '').replace('_', ''): c
                           for c in _rdf_wm.columns}
                _rdx_wm  = _rca_wm.get('diex')
                _rdy_wm  = _rca_wm.get('diey')
                _rrs_wm  = _rca_wm.get('reticleshot')
                _rnum_wm = _rca_wm.get('reticle')
                if _rdx_wm and _rdy_wm and _rrs_wm:
                    _cols_wm = [_rdx_wm, _rdy_wm, _rrs_wm] + ([_rnum_wm] if _rnum_wm else [])
                    _rdf2_wm = _rdf_wm[_cols_wm].dropna().copy()
                    _rdf2_wm[_rdx_wm] = _rdf2_wm[_rdx_wm].astype(int)
                    _rdf2_wm[_rdy_wm] = _rdf2_wm[_rdy_wm].astype(int)
                    # offset to match Sort_X / Sort_Y coordinate space
                    _ret_ox_wm = round(
                        (_rdf2_wm[_rdx_wm].min() + _rdf2_wm[_rdx_wm].max()) / 2)
                    _ret_oy_wm = round(
                        (_rdf2_wm[_rdy_wm].min() + _rdf2_wm[_rdy_wm].max()) / 2)
                    _rdf2_wm['_sx'] = (_rdf2_wm[_rdx_wm] - _ret_ox_wm).astype(int)
                    _rdf2_wm['_sy'] = (_rdf2_wm[_rdy_wm] - _ret_oy_wm).astype(int)
                    # build per-shot bounding boxes [xMin, yMin, xMax, yMax] in SX/SY space
                    _shot_bb_wm: dict = {}
                    for _, _rrv in _rdf2_wm.iterrows():
                        _ss = _rrv[_rrs_wm]
                        _sxv = int(_rrv['_sx']); _syv = int(_rrv['_sy'])
                        if _ss not in _shot_bb_wm:
                            _shot_bb_wm[_ss] = [_sxv, _syv, _sxv, _syv]
                        else:
                            _b = _shot_bb_wm[_ss]
                            if _sxv < _b[0]: _b[0] = _sxv
                            if _syv < _b[1]: _b[1] = _syv
                            if _sxv > _b[2]: _b[2] = _sxv
                            if _syv > _b[3]: _b[3] = _syv
                    _ret_shots_data = list(_shot_bb_wm.values())
                    # build die-loc number map: (SX, SY) -> Reticle sequential number
                    if _rnum_wm:
                        for _, _rrv in _rdf2_wm.iterrows():
                            try:
                                _ret_die_num[(int(_rrv['_sx']), int(_rrv['_sy']))] = \
                                    int(float(_rrv[_rnum_wm]))
                            except (ValueError, TypeError):
                                pass
                    print(f'IBIN reticle: {Path(_ret_cands_wm[0]).name} '
                          f'({len(_ret_shots_data)} shots, {len(_ret_die_num)} die-loc entries, '
                          f'offset=({_ret_ox_wm},{_ret_oy_wm}))')
        except Exception as _e_ret_wm:
            print(f'IBIN reticle load skipped: {_e_ret_wm}')
        # embed die-loc number per die into work DataFrame (used for data-dielo SVG attribute)
        if _ret_die_num:
            _wx_int = work['x'].astype(int); _wy_int = work['y'].astype(int)
            work['_dielo'] = [str(_ret_die_num.get((int(_xi), int(_yi)), '0'))
                              for _xi, _yi in zip(_wx_int, _wy_int)]
        else:
            work['_dielo'] = '0'

        # ── wafer_map_simple.py: center at origin, scale Y for circular appearance ─
        _ibin_wcx = (x_min + x_max) / 2.0
        _ibin_wcy = (y_min + y_max) / 2.0
        _ibin_xr  = x_max - x_min
        _ibin_yr  = y_max - y_min
        _ibin_die_dy = (_ibin_xr / _ibin_yr) if _ibin_yr > 0 else 1.0

        lots       = sorted(work['_lot'].unique())
        cell_in    = 0.20   # target inches per coordinate step per subplot
        tick_step_x = max(1, nx // 10)
        tick_step_y = max(1, ny // 10)

        # ── helper: draw one wafer map on an axis ──────────────────────────
        _ibin_die_dx = 1.0
        _ibin_gap = 0.9

        def _draw_wafer_on_ax(ax, wdf, title_str, show_reticle=True, fontscale=1.0,
                              reticle_fontscale=None, ret_shots_data=None, ret_die_num=None):
            """Draw Rectangle-based wafer map (wafer_map_simple.py style) on ax."""
            # Compute per-lot center and Y-scale so each product's wafer looks
            # circular regardless of its SORT_X range.  Using the global range
            # for die_dy causes mixed-product lots (e.g. BLLC with narrower x-range
            # than the main product) to render as an eclipse instead of a circle.
            _wx_vals = wdf['x'].values.astype(float)
            _wy_vals = wdf['y'].values.astype(float)
            _local_wcx = (_wx_vals.min() + _wx_vals.max()) / 2.0 if len(_wx_vals) else _ibin_wcx
            _local_wcy = (_wy_vals.min() + _wy_vals.max()) / 2.0 if len(_wy_vals) else _ibin_wcy
            _local_xr = _wx_vals.max() - _wx_vals.min() if len(_wx_vals) else _ibin_xr
            _local_yr = _wy_vals.max() - _wy_vals.min() if len(_wy_vals) else _ibin_yr
            _local_die_dy = (_local_xr / _local_yr) if _local_yr > 0 else 1.0

            wx = (_wx_vals - _local_wcx)
            wy = (_wy_vals - _local_wcy) * _local_die_dy
            wclrs  = wdf['color'].tolist()
            wlbls  = wdf['label'].tolist()
            whatch = wdf['hatch'].tolist() if 'hatch' in wdf.columns else [''] * len(wx)
            wedge  = wdf['edge'].tolist()  if 'edge'  in wdf.columns else ['gray'] * len(wx)
            wbnums = wdf['bin_short'].tolist() if 'bin_short' in wdf.columns else [''] * len(wx)
            wfbs   = wdf['_fb'].tolist() if '_fb' in wdf.columns else ['0'] * len(wx)
            whwidx = wdf['_hw_idx'].tolist() if '_hw_idx' in wdf.columns else [0] * len(wx)
            wdielo = wdf['_dielo'].tolist() if '_dielo' in wdf.columns else ['0'] * len(wx)

            for _di in range(len(wx)):
                _rect = mpatches.Rectangle(
                    (wx[_di] - _ibin_die_dx * _ibin_gap / 2,
                     wy[_di] - _local_die_dy * _ibin_gap / 2),
                    _ibin_die_dx * _ibin_gap,
                    _local_die_dy * _ibin_gap,
                    linewidth=0.3, edgecolor=wedge[_di], facecolor=wclrs[_di],
                    hatch=whatch[_di]
                )
                # gid encodes label + FB + HW combo index + die-loc for SVG post-processing
                _rect.set_gid(f'die_{_di}_{wlbls[_di].replace(" ","_")}__FB{wfbs[_di]}__HW{whwidx[_di]}__DLOC{wdielo[_di]}')
                ax.add_patch(_rect)

            ax.set_title(title_str, fontsize=8 * fontscale, pad=2)
            ax.set_aspect('equal', adjustable='box')
            # Axis limits: max absolute centered coord + 5%
            _xext = (abs(wx).max() + 0.5 * _ibin_die_dx) * 1.025 if len(wx) else 1
            _yext = (abs(wy).max() + 0.5 * _local_die_dy) * 1.025 if len(wy) else 1
            ax.set_xlim(-_xext, _xext)
            ax.set_ylim(-_yext, _yext)
            _yt = [t for t in ax.get_yticks() if -_yext <= t <= _yext]
            ax.set_yticks(_yt)
            ax.set_yticklabels([f"{v / _local_die_dy + _local_wcy:.0f}" for v in _yt], fontsize=6 * fontscale)
            _xt = [t for t in ax.get_xticks() if -_xext <= t <= _xext]
            ax.set_xticks(_xt)
            ax.set_xticklabels([f"{v + _local_wcx:.0f}" for v in _xt], fontsize=6 * fontscale)
            ax.tick_params(labelsize=6 * fontscale)
            ax.set_axisbelow(True)
            ax.set_xlabel('X', fontsize=6 * fontscale, labelpad=1)
            ax.set_ylabel('Y', fontsize=6 * fontscale, labelpad=1)
            ax.set_xlim(-_xext, _xext)
            ax.set_ylim(-_yext, _yext)

            # reticle overlay
            if show_reticle and _has_reticle and '_layoutx' in wdf.columns:
                _rdf = wdf[['x', 'y', '_layoutx', '_layouty']].dropna()
                if not _rdf.empty:
                    _rlookup = {}
                    for _, _rr in _rdf.iterrows():
                        _rlookup[(_rr['x'], _rr['y'])] = (_rr['_layoutx'], _rr['_layouty'])
                    _xs = sorted(_rdf['x'].unique())
                    _ys = sorted(_rdf['y'].unique())
                    for _yi in _ys:
                        for _xi_idx in range(len(_xs) - 1):
                            _k1 = (_xs[_xi_idx], _yi)
                            _k2 = (_xs[_xi_idx + 1], _yi)
                            _lx1 = _rlookup.get(_k1, (None,))[0]
                            _lx2 = _rlookup.get(_k2, (None,))[0]
                            if _lx1 is not None and _lx2 is not None and _lx1 != _lx2:
                                _bx = ((_xs[_xi_idx] + _xs[_xi_idx + 1]) / 2 - _local_wcx)
                                _by_c = (_yi - _local_wcy) * _local_die_dy
                                ax.plot([_bx, _bx],
                                        [_by_c - _local_die_dy * 0.5, _by_c + _local_die_dy * 0.5],
                                        color='blue', linewidth=0.5, alpha=0.8, zorder=5)
                    for _xi in _xs:
                        for _yi_idx in range(len(_ys) - 1):
                            _k1 = (_xi, _ys[_yi_idx])
                            _k2 = (_xi, _ys[_yi_idx + 1])
                            _ly1 = _rlookup.get(_k1, (None, None))[1]
                            _ly2 = _rlookup.get(_k2, (None, None))[1]
                            if _ly1 is not None and _ly2 is not None and _ly1 != _ly2:
                                _bx_c = (_xi - _local_wcx)
                                _by = ((_ys[_yi_idx] + _ys[_yi_idx + 1]) / 2 - _local_wcy) * _local_die_dy
                                ax.plot([_bx_c - 0.5, _bx_c + 0.5], [_by, _by],
                                        color='blue', linewidth=0.5, alpha=0.8, zorder=5)
                if '_reticle' in wdf.columns:
                    _ret_fs = (reticle_fontscale if reticle_fontscale is not None else fontscale) * 0.9
                    for _, _rr in wdf[['x', 'y', '_reticle']].dropna().iterrows():
                        try:
                            ax.text(_rr['x'] - _local_wcx,
                                    (_rr['y'] - _local_wcy) * _local_die_dy,
                                    str(int(_rr['_reticle'])),
                                    ha='center', va='center', fontsize=_ret_fs,
                                    color='black', fontweight='bold', alpha=0.9, zorder=6)
                        except (ValueError, TypeError):
                            pass

            # ── Reticle shot outlines (blue rectangles) from shared reticle CSV ─
            # Drawn regardless of whether the data CSV has LayoutX/LayoutY columns.
            if show_reticle and ret_shots_data:
                for _shb in ret_shots_data:
                    # _shb = [xMin_SX, yMin_SY, xMax_SX, yMax_SY] in Sort_X/Sort_Y space
                    _sx_lo = _shb[0] - _local_wcx - _ibin_die_dx / 2
                    _sy_lo = (_shb[1] - _local_wcy - 0.5) * _local_die_dy
                    _bw = (_shb[2] - _shb[0] + _ibin_die_dx)
                    _bh = (_shb[3] - _shb[1] + 1.0) * _local_die_dy
                    ax.add_patch(mpatches.Rectangle(
                        (_sx_lo, _sy_lo), _bw, _bh,
                        linewidth=0.375, edgecolor='#2471a3', facecolor='none',
                        zorder=7, alpha=0.85
                    ))

            # ── Die-loc numbers from shared reticle CSV ──────────────────────────
            # Shows the sequential die position (1–N) within each reticle shot.
            if show_reticle and ret_die_num:
                _dloc_fs = (reticle_fontscale if reticle_fontscale is not None
                            else fontscale) * 2.0
                for (_sdx, _sdy), _snum in ret_die_num.items():
                    _dpx = _sdx - _local_wcx
                    _dpy = (_sdy - _local_wcy) * _local_die_dy
                    ax.text(_dpx, _dpy, str(_snum),
                            ha='center', va='center', fontsize=_dloc_fs,
                            color='#1a5276', fontweight='bold', alpha=0.85, zorder=8)

            # collect legend entries
            legend_dict = {}
            for lbl, clr, htch, edg in zip(wlbls, wclrs, whatch, wedge):
                if lbl not in legend_dict:
                    legend_dict[lbl] = (clr, htch, edg)
            return legend_dict

        def _swatch_svg(facecolor, hatch, edgecolor, size=14):
            """Return a tiny inline SVG swatch showing facecolor + hatch pattern."""
            s = size
            h = hatch.strip() if hatch else ''
            # Density: more repeated chars → tighter spacing
            density = len(h)
            spacing = max(2, s // max(1, density))
            hc = edgecolor
            lines = []
            if h:
                if 'x' in h.lower():
                    for i in range(-s, s * 2, spacing):
                        lines.append(f'<line x1="{i}" y1="{s}" x2="{i+s}" y2="0" stroke="{hc}" stroke-width="1"/>')
                        lines.append(f'<line x1="{i}" y1="0" x2="{i+s}" y2="{s}" stroke="{hc}" stroke-width="1"/>')
                elif '/' in h:
                    for i in range(-s, s * 2, spacing):
                        lines.append(f'<line x1="{i}" y1="{s}" x2="{i+s}" y2="0" stroke="{hc}" stroke-width="1"/>')
                elif '\\' in h:
                    for i in range(-s, s * 2, spacing):
                        lines.append(f'<line x1="{i}" y1="0" x2="{i+s}" y2="{s}" stroke="{hc}" stroke-width="1"/>')
                elif '+' in h:
                    for i in range(0, s + spacing, spacing):
                        lines.append(f'<line x1="0" y1="{i}" x2="{s}" y2="{i}" stroke="{hc}" stroke-width="1"/>')
                        lines.append(f'<line x1="{i}" y1="0" x2="{i}" y2="{s}" stroke="{hc}" stroke-width="1"/>')
                elif '-' in h:
                    for i in range(0, s + spacing, spacing):
                        lines.append(f'<line x1="0" y1="{i}" x2="{s}" y2="{i}" stroke="{hc}" stroke-width="1"/>')
                elif '|' in h:
                    for i in range(0, s + spacing, spacing):
                        lines.append(f'<line x1="{i}" y1="0" x2="{i}" y2="{s}" stroke="{hc}" stroke-width="1"/>')
                elif 'o' in h:
                    r = max(1, spacing // 3)
                    for ix in range(spacing // 2, s, spacing):
                        for iy in range(spacing // 2, s, spacing):
                            lines.append(f'<circle cx="{ix}" cy="{iy}" r="{r}" fill="none" stroke="{hc}" stroke-width="1"/>')
                elif '.' in h:
                    for ix in range(spacing // 2, s, spacing):
                        for iy in range(spacing // 2, s, spacing):
                            lines.append(f'<circle cx="{ix}" cy="{iy}" r="1" fill="{hc}"/>')
                elif '*' in h:
                    r = max(1, spacing // 3)
                    for ix in range(spacing // 2, s, spacing):
                        for iy in range(spacing // 2, s, spacing):
                            lines.append(f'<line x1="{ix-r}" y1="{iy}" x2="{ix+r}" y2="{iy}" stroke="{hc}" stroke-width="1"/>')
                            lines.append(f'<line x1="{ix}" y1="{iy-r}" x2="{ix}" y2="{iy+r}" stroke="{hc}" stroke-width="1"/>')
            cid = re.sub(r'[^a-zA-Z0-9]', '_', f'sw_{facecolor}_{h[:4] if h else "s"}')
            pat = '\n'.join(lines)
            return (
                f'<svg width="{s}" height="{s}" viewBox="0 0 {s} {s}" '
                f'style="display:inline-block;vertical-align:middle;margin-right:3px;'
                f'border:1px solid {edgecolor};border-radius:1px;flex-shrink:0">'
                f'<defs><clipPath id="{cid}"><rect width="{s}" height="{s}"/></clipPath></defs>'
                f'<rect width="{s}" height="{s}" fill="{facecolor}"/>'
                f'<g clip-path="url(#{cid})">{pat}</g>'
                f'</svg>'
            )

        def _fig_to_b64(fig):
            """Render a matplotlib figure to a base64-encoded PNG string."""
            buf_io = io.BytesIO()
            fig.savefig(buf_io, format='png', dpi=200, bbox_inches='tight')
            plt.close(fig)
            buf_io.seek(0)
            return base64.b64encode(buf_io.read()).decode('ascii')

        def _fig_to_svg_with_tooltips(fig, svg_id='wafersvg'):
            """Render figure to inline SVG; inject <title> tooltips and data-bin on die patches.
            All internal IDs are prefixed with svg_id to prevent cross-SVG <defs> conflicts
            (hatch <pattern> and <clipPath> IDs are shared across the DOM when SVGs are inlined).
            """
            from xml.etree import ElementTree as ET
            buf_io = io.StringIO()
            fig.savefig(buf_io, format='svg', bbox_inches='tight')
            plt.close(fig)
            svg_str = buf_io.getvalue()
            try:
                # Strip XML declaration so it can be inlined in HTML
                svg_str = re.sub(r'<\?xml[^>]*\?>', '', svg_str).strip()
                ns = 'http://www.w3.org/2000/svg'
                xlink_ns = 'http://www.w3.org/1999/xlink'
                ET.register_namespace('', ns)
                ET.register_namespace('xlink', xlink_ns)
                root = ET.fromstring(svg_str)
                # Remove fixed width/height but keep viewBox for responsive scaling
                if root.get('viewBox') is None:
                    w = root.get('width', '800pt')
                    h = root.get('height', '600pt')
                    def _pt(s):
                        try: return float(re.sub(r'[^\d.]', '', s))
                        except: return float(s) if s else 0
                    root.set('viewBox', f'0 0 {_pt(w):.1f} {_pt(h):.1f}')
                root.attrib.pop('width', None)
                root.attrib.pop('height', None)
                root.set('width', '100%')
                root.set('id', svg_id)
                # ── Step 1: inject data-bin / tooltip BEFORE id renaming ──────────
                for g_elem in root.iter(f'{{{ns}}}g'):
                    gid = g_elem.get('id', '')
                    if gid.startswith('die_'):
                        parts = gid.split('_', 2)
                        raw = parts[2] if len(parts) >= 3 else gid
                        # Extract die-loc embedded as __DLOC<n> suffix (parse first — rightmost)
                        dloc_val = '0'
                        if '__DLOC' in raw:
                            raw, dloc_part = raw.rsplit('__DLOC', 1)
                            _dm = re.match(r'(\d+)', dloc_part)
                            dloc_val = _dm.group(1) if _dm else '0'
                        # Extract HW combo index embedded as __HW<n> suffix
                        hw_val = '0'
                        if '__HW' in raw:
                            raw, hw_part = raw.rsplit('__HW', 1)
                            _hwm = re.match(r'(\d+)', hw_part)
                            hw_val = _hwm.group(1) if _hwm else '0'
                        # Extract FB value embedded as __FB<n> suffix
                        fb_val = '0'
                        if '__FB' in raw:
                            raw, fb_part = raw.rsplit('__FB', 1)
                            _fm = re.match(r'(\d+)', fb_part)
                            fb_val = _fm.group(1) if _fm else '0'
                        tip_text = raw.replace('_', ' ')
                        g_elem.set('data-bin', tip_text)
                        g_elem.set('data-fb', fb_val)
                        g_elem.set('data-hw', hw_val)
                        g_elem.set('data-dielo', dloc_val)
                        title_el = ET.Element(f'{{{ns}}}title')
                        title_el.text = tip_text + (f' (FB {fb_val})' if fb_val and fb_val != '0' else '')
                        g_elem.insert(0, title_el)
                # ── Step 2: prefix every internal ID with svg_id ─────────────────
                # Multiple inline SVGs share one DOM; matplotlib generates identical
                # pattern/clipPath IDs for same hatch style across wafers, causing
                # the last definition to silently override all earlier ones.
                id_map = {}
                for el in root.iter():
                    old_id = el.get('id')
                    if old_id and old_id != svg_id:
                        new_id = f'{svg_id}_{old_id}'
                        id_map[old_id] = new_id
                        el.set('id', new_id)
                if id_map:
                    xhref = f'{{{xlink_ns}}}href'
                    def _fix_url(val):
                        return re.sub(
                            r'url\(#([^)]+)\)',
                            lambda m: f'url(#{id_map.get(m.group(1), m.group(1))})',
                            val)
                    for el in root.iter():
                        for attr, val in list(el.attrib.items()):
                            if 'url(#' in val:
                                el.set(attr, _fix_url(val))
                            if attr in (xhref, 'href') and val.startswith('#'):
                                ref = val[1:]
                                if ref in id_map:
                                    el.set(attr, f'#{id_map[ref]}')
                        # Fix url refs inside <style> text blocks
                        if el.tag == f'{{{ns}}}style' and el.text and 'url(#' in el.text:
                            el.text = _fix_url(el.text)
                return ET.tostring(root, encoding='unicode')
            except Exception:
                return svg_str

        _HTML_STYLE = """\
html,body{margin:0;padding:8px;background:#fff;font-family:Arial,sans-serif;font-size:12px}
img{max-width:100%;height:auto;display:block;margin-bottom:10px}
table{border-collapse:collapse;font-size:11px}
.wm-sum-tbl{font-size:17px}
th,td{padding:3px 10px;border:1px solid #ccc;white-space:nowrap}
th{background:#2c3e50;color:#ecf0f1;font-weight:bold}
tr:hover td{background:#f0f4ff}
h2{font-size:14px;color:#2c3e50;margin:16px 0 6px}
.fb-panel{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:2px solid #2c3e50;box-shadow:0 -4px 16px rgba(0,0,0,.15);z-index:9999;padding:8px 14px;font-family:Arial,sans-serif;font-size:13px;display:none}
.fb-panel.open{display:flex;flex-direction:column}
.fb-panel .fb-resize{height:7px;background:#e2e8f0;cursor:ns-resize;display:flex;align-items:center;justify-content:center;user-select:none;flex-shrink:0;margin:-8px -14px 6px -14px;border-radius:0}
.fb-panel .fb-resize:hover,.fb-panel .fb-resize.dragging{background:#2980b9}
.fb-panel .fb-resize::after{content:'\2014';color:#aaa;font-size:10px}
.fb-panel .fb-resize:hover::after,.fb-panel .fb-resize.dragging::after{color:#fff}
.fb-phdr{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px}
.fb-ptitle{font-size:13px;font-weight:bold;color:#2c3e50;flex:1;min-width:0}
.fb-cblist{display:flex;flex-wrap:wrap;gap:5px;overflow-y:auto;flex:0 0 auto;max-width:55%;min-width:160px;align-content:flex-start}
.fb-scope{font-size:11px;color:#555;padding:2px 8px;background:#eef3fb;border-radius:3px;white-space:nowrap;margin-bottom:4px;align-self:flex-start}
.fb-body{display:flex;flex-direction:row;flex:1;min-height:0;gap:14px;overflow:hidden}
.fb-chart{flex:1;overflow-y:auto;min-width:200px;padding-top:2px}
.fb-cbitem{display:flex;align-items:center;gap:3px;font-size:12px;padding:3px 8px;border-radius:3px;background:#f4f7fb;border:1px solid #cdd5e0;cursor:pointer}
.fb-cbitem:hover{background:#eaf2ff}
.fb-cbitem input{cursor:pointer;margin:0}
.fb-btn{padding:3px 10px;font-size:12px;cursor:pointer;border:1px solid #bdc3c7;background:#ecf0f1;border-radius:3px;color:#2c3e50;white-space:nowrap}
.fb-btn:hover{background:#d5dbde}
.fb-btn.prim{background:#1a4a8a;border-color:#2563eb;color:#fff}
.fb-btn.prim:hover{background:#2563eb}
.v-resize-handle{height:7px;background:#e2e8f0;cursor:ns-resize;border-radius:0 0 4px 4px;display:flex;align-items:center;justify-content:center;user-select:none;transition:background .15s;margin-top:1px}
.v-resize-handle:hover,.v-resize-handle.dragging{background:#2980b9}
.v-resize-handle::after{content:'—';color:#aaa;font-size:10px}
.v-resize-handle:hover::after,.v-resize-handle.dragging::after{color:#fff}
.h-resize-handle{width:7px;background:#e2e8f0;cursor:ew-resize;border-radius:0 4px 4px 0;align-self:stretch;display:flex;align-items:center;justify-content:center;user-select:none;transition:background .15s;margin-left:1px;flex-shrink:0}
.h-resize-handle:hover,.h-resize-handle.dragging{background:#2980b9}
.h-resize-handle::after{content:'⋮';color:#aaa;font-size:12px}
.h-resize-handle:hover::after,.h-resize-handle.dragging::after{color:#fff}
.comp-corner-resize{position:absolute;bottom:0;right:0;width:20px;height:20px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#bdc3c7 50%);opacity:.5;z-index:2;border-radius:0 0 4px 0}
.comp-corner-resize:hover{opacity:1;background:linear-gradient(135deg,transparent 50%,#2980b9 50%)}"""

        out_paths = []

        for lot_label in lots:
            lot_work  = work[work['_lot'] == lot_label]
            wafers    = sorted(lot_work['_wafer'].unique())
            n_wafers  = len(wafers)

            # ── build per-wafer summary table rows ─────────────────────────
            waf_rows_html = ''
            for wafer in wafers:
                wdf   = lot_work[lot_work['_wafer'] == wafer]
                total = len(wdf)
                n_pass = wdf['label'].str.contains('Pass').sum()
                n_fail = total - n_pass
                pass_pct = n_pass / total * 100 if total else 0
                fail_pct = n_fail / total * 100 if total else 0
                _wm_mat_val = str(wdf['_material'].iloc[0]) if '_material' in wdf.columns and len(wdf) > 0 else ''
                _wm_wafer_safe = str(wafer).replace("'", "\\'")
                waf_rows_html += (
                    f'<tr class="wm-row" id="wm-row-{wafer}" onclick="wmSelectWafer(\'{_wm_wafer_safe}\', event)" style="cursor:pointer">'
                    f'<td>{wafer}</td>'
                    + (f'<td>{_wm_mat_val}</td>' if mat_col_wm else '')
                    + f'<td style="text-align:right">{total:,}</td>'
                    f'<td style="text-align:right;color:green">{n_pass:,} ({pass_pct:.2f}%)</td>'
                    f'<td style="text-align:right;color:#c0392b">{n_fail:,} ({fail_pct:.2f}%)</td></tr>\n'
                )

            # ══════════════════════════════════════════════════════════════
            # 1) COMPOSITE plot — manually-positioned square axes (no gaps)
            # ══════════════════════════════════════════════════════════════
            cols_grid = max(1, math.ceil(math.sqrt(n_wafers)))
            rows_grid = math.ceil(n_wafers / cols_grid)

            # Each wafer subplot is a square cell.  Reserve space for legend
            # on the right and title on top.
            cell_size = 0.90                   # inches per wafer cell (SVG quality baseline)
            legend_in = 0.81                   # inches reserved for legend
            title_in  = 0.009                  # inches reserved for suptitle
            pad       = 0.11                   # inches gap between cells
            fig_w = cols_grid * cell_size + (cols_grid - 1) * pad + legend_in + 0.4
            fig_h = rows_grid * cell_size + (rows_grid - 1) * pad + title_in + 0.3
            # Display size in HTML: constrain the SVG container to the figure's natural
            # CSS-pixel size so the browser doesn't stretch it to full window width.
            # matplotlib SVG uses 72pt/inch; browsers render at 96px/inch
            _comp_max_w = int(fig_w * 96 * 2.25)    # display scale
            _comp_rendered_h = _comp_max_w * fig_h / fig_w
            # Legend top: aligned with top-row wafer subplot titles.
            # Subplot top edge (figure fraction from bottom):
            #   y_origin + (rows_grid-1)*(ch+ypad) + ch  where ch=cell_size/fig_h, ypad=pad/fig_h
            _ch_frac = cell_size / fig_h
            _yp_frac = pad / fig_h
            _top_axes_top_frac = 0.02 + (rows_grid - 1) * (_ch_frac + _yp_frac) + _ch_frac
            # In SVG (y=0 at top): px from top = (1 - frac) * rendered_h; +20px for title text
            _comp_leg_top = int((1.0 - _top_axes_top_frac) * _comp_rendered_h) + 20

            fig_comp = plt.figure(figsize=(fig_w, fig_h))
            seen_legend: dict = {}

            # Compute normalised position of each cell (left, bottom, w, h)
            plot_area_w = (cols_grid * cell_size + (cols_grid - 1) * pad) / fig_w
            plot_area_h = (rows_grid * cell_size + (rows_grid - 1) * pad) / fig_h
            x_origin = 0.02                    # left margin (fraction)
            y_origin = 0.02                    # bottom margin (fraction)
            cw = cell_size / fig_w             # cell width  (fraction)
            ch = cell_size / fig_h             # cell height (fraction)
            xpad = pad / fig_w
            ypad = pad / fig_h

            for idx, wafer in enumerate(wafers):
                r, c_idx = divmod(idx, cols_grid)
                # row 0 = top row visually → invert row index
                row_inv = rows_grid - 1 - r
                left   = x_origin + c_idx * (cw + xpad)
                bottom = y_origin + row_inv * (ch + ypad)

                ax = fig_comp.add_axes([left, bottom, cw, ch])
                wdf = lot_work[lot_work['_wafer'] == wafer]
                if wdf.empty:
                    ax.set_visible(False)
                    continue
                leg = _draw_wafer_on_ax(ax, wdf,
                                        f'W{wafer} (n={len(wdf):,})',
                                        fontscale=0.42,
                                        ret_shots_data=_ret_shots_data,
                                        ret_die_num=_ret_die_num)
                # keep axis tick labels for composite view
                ax.tick_params(labelsize=2.5)
                seen_legend.update(leg)

            # shared legend info (used for composite HTML legend below)
            lot_label_counts = lot_work['label'].value_counts().to_dict()
            legend_items = sorted(seen_legend.items(), key=_leg_order)
            handles = [mpatches.Patch(facecolor=c, hatch=h, edgecolor=e,
                                      label=f'{l}  (n={lot_label_counts.get(l, 0):,})')
                       for l, (c, h, e) in legend_items]
            # Render composite as interactive SVG (matplotlib legend omitted — replaced by HTML legend)
            fig_comp.suptitle(
                f'Lot {lot_label}  ({n_wafers} wafer{"s" if n_wafers != 1 else ""})',
                fontsize=5, fontweight='bold', y=1.005, va='bottom')
            _comp_svg_id  = f'compsvg_{_sanitize_label(str(lot_label))}'
            _comp_svg_str = _fig_to_svg_with_tooltips(fig_comp, _comp_svg_id)

            # ══════════════════════════════════════════════════════════════
            # 2) INDIVIDUAL per-wafer plots — one figure each (SVG only)
            # ══════════════════════════════════════════════════════════════
            # Each wafer entry: (wafer, svg_str, svg_id, leg_items, label_counts)
            # Static PNG view is rasterized browser-side from the same SVG — no
            # second matplotlib render needed, halving generation time & file size.
            per_wafer_imgs = []
            _sw = min(9, max(3.0, nx * 0.165) + 1.1)
            _sh = min(7.5, max(3.0, ny * 0.165) + 0.6)

            for wafer in wafers:
                wdf = lot_work[lot_work['_wafer'] == wafer]
                if wdf.empty:
                    continue
                total = len(wdf)
                n_pass = wdf['label'].str.contains('Pass').sum()
                pass_pct = n_pass / total * 100 if total else 0
                title_str = f'Lot {lot_label} \u2014 Wafer {wafer}  (n={total:,}, pass={pass_pct:.1f}%)'

                fig_svg, ax_svg = plt.subplots(figsize=(_sw, _sh))
                leg = _draw_wafer_on_ax(ax_svg, wdf, title_str, fontscale=0.75,
                                          reticle_fontscale=0.75 * 3.025,
                                          ret_shots_data=_ret_shots_data,
                                          ret_die_num=_ret_die_num)
                fig_svg.tight_layout()
                _svg_id = f'wsvg_{lot_label}_{wafer}'.replace(' ', '_')
                _svg_str = _fig_to_svg_with_tooltips(fig_svg, _svg_id)  # closes fig

                wafer_label_counts = wdf['label'].value_counts().to_dict()
                leg_items = sorted(leg.items(), key=_leg_order)
                per_wafer_imgs.append((wafer, _svg_str, _svg_id, leg_items, wafer_label_counts))

            # ══════════════════════════════════════════════════════════════
            # 3) Assemble single HTML — both views embedded, toggle button
            # ══════════════════════════════════════════════════════════════
            def _build_leg_html_interactive(leg_items, wlcounts, svg_id, scale=1.0):
                _fs1 = round(9 * scale, 1)
                _fs2 = round(8 * scale, 1)
                _fs3 = round(10 * scale, 1)
                _fs4 = round(9 * scale, 1)
                _sw_sz = round(12 * scale)
                h = '<div class="ibin-legend" data-svgid="{sid}">'.format(sid=svg_id)
                h += f'<div style="font-weight:bold;margin-bottom:2px;color:#2c3e50;font-size:{_fs1}px">Interface Bin <span style="font-weight:normal;font-size:{_fs2}px">(click to highlight)</span></div>'
                for _ll, (_lc, _lh, _le) in leg_items:
                    _cnt = wlcounts.get(_ll, 0)
                    _sw2 = _swatch_svg(_lc, _lh, _le, size=_sw_sz)
                    h += (
                        f'<div class="leg-item" data-bin="{_ll}" data-svgid="{svg_id}" '
                        f'onclick="ibinToggle(this,event)" '
                        f'style="display:flex;align-items:center;padding:2px 4px;cursor:pointer;border-radius:3px;margin:0">'
                        f'{_sw2}'
                        f'<span style="font-size:{_fs3}px;color:#003366">{_ll}</span>'
                        f'<span style="font-size:{_fs4}px;color:#666;margin-left:auto;padding-left:8px">n={_cnt:,}</span>'
                        f'</div>'
                    )
                h += '</div>'
                return h

            def _build_leg_html_static(leg_items, wlcounts, scale=1.0):
                _fs1 = round(9 * scale, 1); _fs3 = round(10 * scale, 1); _fs4 = round(9 * scale, 1)
                _sw_sz = round(12 * scale)
                h = f'<div style="border:1px solid #ccc;border-radius:4px;padding:4px 6px;background:#fafafa;display:flex;flex-direction:column;gap:1px;min-width:160px;max-width:260px">'
                h += f'<div style="font-weight:bold;margin-bottom:2px;color:#2c3e50;font-size:{_fs1}px">Interface Bin</div>'
                for _ll, (_lc, _lh, _le) in leg_items:
                    _cnt = wlcounts.get(_ll, 0)
                    _sw2 = _swatch_svg(_lc, _lh, _le, size=_sw_sz)
                    h += (
                        f'<div style="display:flex;align-items:center;padding:2px 4px;border-radius:3px;margin:0">'
                        f'{_sw2}'
                        f'<span style="font-size:{_fs3}px;color:#003366">{_ll}</span>'
                        f'<span style="font-size:{_fs4}px;color:#666;margin-left:auto;padding-left:8px">n={_cnt:,}</span>'
                        f'</div>'
                    )
                h += '</div>'
                return h

            per_wafer_html = ''
            for _wi, (_wlabel, _wsvg, _svg_id, _leg_items, _wlcounts) in enumerate(per_wafer_imgs):
                _canvas_id = f'canvas_{_svg_id}'
                _leg_interactive = _build_leg_html_interactive(_leg_items, _wlcounts, _svg_id, scale=1.331)
                _leg_static      = _build_leg_html_static(_leg_items, _wlcounts, scale=1.331)
                _wlabel_safe = str(_wlabel).replace("'", "\\'")
                # First wafer: inline SVG (renders immediately).
                # Subsequent wafers: SVG stored in <template> for lazy DOM construction.
                if _wi == 0:
                    _svg_slot = f'<div id="wsvg-wrap-{_wlabel}" style="width:100%">{_wsvg}</div>'
                else:
                    _svg_slot = (
                        f'<template id="wm-svg-tmpl-{_wlabel}">{_wsvg}</template>'
                        f'<div id="wsvg-wrap-{_wlabel}" style="width:100%;min-height:360px;'
                        f'display:flex;align-items:center;justify-content:center;color:#aaa;font-size:12px">'
                        f'&#9200; Loading\u2026</div>'
                    )
                per_wafer_html += (
                    f'<div id="wm-section-{_wlabel}" class="wm-wafer-section">'
                    f'<h2>Wafer {_wlabel}</h2>\n'
                    # interactive view
                    f'<div style="display:flex;align-items:flex-start;gap:8px">'
                    f'<div style="width:900px;min-width:200px;flex-shrink:0">'
                    + _svg_slot +
                    f'<div class="h-resize-handle" id="comp-h-resize" onmousedown="startHResize(event,"comp-svg-wrap","comp-svg-w")"></div>'
                    f'</div>'
                    f'<div style="flex-shrink:0;padding-top:32px">{_leg_interactive}</div>'
                    f'</div>\n'
                    f'</div>\n'
                )

            lot_safe = _sanitize_label(str(lot_label))
            # Build composite legend — uses the same ibinToggle JS targeting _comp_svg_id
            _comp_leg_html = _build_leg_html_interactive(
                sorted(seen_legend.items(), key=_leg_order),
                lot_label_counts,
                _comp_svg_id,
            )

            # ══════════════════════════════════════════════════════════════
            # 2.5) PATTERN ANALYSIS — mode bin per die position (all wafers)
            # ══════════════════════════════════════════════════════════════
            _pat_style = {}
            for _, _plr in lot_work[['label','color','hatch','edge']].drop_duplicates(subset=['label']).iterrows():
                _pat_style[_plr['label']] = (_plr['color'], _plr['hatch'], _plr['edge'])
            _pat_df = lot_work.groupby(['x','y'])['label'].agg(lambda s: s.mode().iloc[0]).reset_index()
            _pat_df['color']     = _pat_df['label'].map(lambda l: _pat_style.get(l, ('#95a5a6','','gray'))[0])
            _pat_df['hatch']     = _pat_df['label'].map(lambda l: _pat_style.get(l, ('#95a5a6','','gray'))[1])
            _pat_df['edge']      = _pat_df['label'].map(lambda l: _pat_style.get(l, ('#95a5a6','','gray'))[2])
            _pat_df['bin_short'] = _pat_df['label'].apply(
                lambda l: '' if 'Pass' in l else (re.search(r'Bin (\d+)', l).group(1) if re.search(r'Bin (\d+)', l) else ''))
            _pat_df['_fb'] = '0'
            _pat_df['_hw_idx'] = 0
            if '_layoutx' in lot_work.columns:
                _pr = lot_work.groupby(['x','y'])[['_layoutx','_layouty']].first().reset_index()
                _pat_df = _pat_df.merge(_pr, on=['x','y'], how='left')
            if '_reticle' in lot_work.columns:
                _prn = lot_work.groupby(['x','y'])[['_reticle']].first().reset_index()
                _pat_df = _pat_df.merge(_prn, on=['x','y'], how='left')
            _psw = min(9.0, max(5.0, nx * 0.15))
            _psh = min(7.5, max(4.0, ny * 0.15))
            fig_pat, ax_pat = plt.subplots(figsize=(_psw, _psh))
            _pat_leg = _draw_wafer_on_ax(
                ax_pat, _pat_df,
                f'Lot {lot_label}  \u2014  Wafer Pattern Analysis  ({n_wafers} wafer{"s" if n_wafers!=1 else ""}, mode bin per position)',
                fontscale=1.2, reticle_fontscale=3.8,
                ret_shots_data=_ret_shots_data, ret_die_num=_ret_die_num)
            fig_pat.tight_layout()
            _pat_svg_id  = f'patsvg_{lot_safe}'
            _pat_svg_str = _fig_to_svg_with_tooltips(fig_pat, _pat_svg_id)
            _pat_leg_items    = sorted(_pat_leg.items(), key=_leg_order)
            _pat_label_counts = _pat_df['label'].value_counts().to_dict()
            _pat_leg_html = _build_leg_html_interactive(_pat_leg_items, _pat_label_counts, _pat_svg_id, scale=1.5)
            # Reticle summary table for pattern tab
            _ret_tbl_html = ''
            if _has_reticle and '_layoutx' in lot_work.columns:
                _ret_grp = lot_work[['x','y','_layoutx','_layouty']].dropna().drop_duplicates(['x','y'])
                if '_reticle' in lot_work.columns:
                    _ret_num_grp = lot_work[['x','y','_reticle']].dropna().drop_duplicates(['x','y'])
                    _ret_grp = _ret_grp.merge(_ret_num_grp, on=['x','y'], how='left')
                _ret_uniq = _ret_grp.groupby(['_layoutx','_layouty']).agg(
                    die_count=('x','count'),
                    **({'reticle': ('_reticle', 'first')} if '_reticle' in _ret_grp.columns else {})
                ).reset_index().sort_values(['_layouty','_layoutx'])
                if not _ret_uniq.empty:
                    _ret_tbl_html = '<h3 style="margin:16px 0 4px;color:#2c3e50;font-size:13px">Reticle Information</h3>'
                    _ret_tbl_html += '<table style="border-collapse:collapse;font-size:12px;margin-bottom:12px"><thead><tr>'
                    _ret_hcols = ['LayoutX','LayoutY'] + (['Reticle'] if '_reticle' in _ret_uniq.columns else []) + ['Die Count']
                    _ret_tbl_html += ''.join(f'<th style="background:#2c3e50;color:#fff;padding:4px 10px;text-align:left">{c}</th>' for c in _ret_hcols)
                    _ret_tbl_html += '</tr></thead><tbody>'
                    for _ri, _rrow in _ret_uniq.iterrows():
                        _ret_tbl_html += '<tr>'
                        _ret_tbl_html += f'<td style="padding:3px 10px;border-bottom:1px solid #eee">{int(_rrow["_layoutx"])}</td>'
                        _ret_tbl_html += f'<td style="padding:3px 10px;border-bottom:1px solid #eee">{int(_rrow["_layouty"])}</td>'
                        if '_reticle' in _ret_uniq.columns:
                            try:
                                _ret_tbl_html += f'<td style="padding:3px 10px;border-bottom:1px solid #eee">{int(_rrow["reticle"])}</td>'
                            except (ValueError, TypeError):
                                _ret_tbl_html += f'<td style="padding:3px 10px;border-bottom:1px solid #eee">{_rrow.get("reticle","")}</td>'
                        _ret_tbl_html += f'<td style="padding:3px 10px;border-bottom:1px solid #eee;text-align:right">{int(_rrow["die_count"]):,}</td>'
                        _ret_tbl_html += '</tr>'
                    _ret_tbl_html += '</tbody></table>'
            _IBIN_JS = """<div id="ibin-fb-panel" class="fb-panel">
  <div class="fb-resize" id="fb-panel-resize"></div>
  <div class="fb-phdr">
    <span id="ibin-fb-title" class="fb-ptitle">FB Filter</span>
    <button class="fb-btn" onclick="ibinFbSelAll()">All</button>
    <button class="fb-btn" onclick="ibinFbClrAll()">None</button>
    <button class="fb-btn prim" onclick="ibinFbApply()">&#9670; Highlight on Map</button>
    <button class="fb-btn" onclick="ibinFbClear()">Show All</button>
    <button class="fb-btn" onclick="ibinFbClose()">&times; Close</button>
  </div>
  <span id="ibin-fb-scope" class="fb-scope"></span>
  <div class="fb-body">
    <div style="display:flex;flex-direction:column;gap:6px;overflow-y:auto;flex:0 0 auto;max-width:55%;min-width:160px">
      <div id="ibin-fb-cblist" class="fb-cblist" style="max-width:100%"></div>
      <div id="ibin-dloc-row" style="display:none;border-top:1px solid #cdd5e0;padding-top:5px">
        <div style="font-size:11px;color:#555;margin-bottom:3px;font-weight:bold;display:flex;align-items:center;gap:6px">&#9635; Reticle Die Loc
          <button class="fb-btn" style="font-size:10px;padding:1px 5px" onclick="ibinDlocSelAll()">All</button>
          <button class="fb-btn" style="font-size:10px;padding:1px 5px" onclick="ibinDlocClrAll()">None</button>
        </div>
        <div id="ibin-dloc-cblist" class="fb-cblist" style="max-width:100%"></div>
      </div>
    </div>
    <div id="ibin-fb-chart" class="fb-chart"></div>
  </div>
</div>
<script>
var _ibinFbBin=null,_ibinFbSvgId=null;
function ibinToggle(el,ev) {
  if(ev&&(ev.ctrlKey||ev.metaKey)){
    // Ctrl+click: classic toggle highlight
    var bin=el.getAttribute('data-bin');
    var svgId=el.getAttribute('data-svgid');
    var svg=document.getElementById(svgId);
    if(!svg)return;
    el.classList.toggle('active');
    var legend=el.closest('.ibin-legend');
    var activeItems=legend?legend.querySelectorAll('.leg-item.active'):[];
    var activeBins=Array.from(activeItems).map(function(i){return i.getAttribute('data-bin');});
    var dies=svg.querySelectorAll('g[data-bin]');
    dies.forEach(function(g){
      if(activeBins.length===0){g.style.opacity='1';g.style.filter='';}
      else{var db=g.getAttribute('data-bin');var match=activeBins.some(function(ab){return db===ab;});g.style.opacity=match?'1':'0.10';g.style.filter=match?'drop-shadow(0 0 3px #000)':'';}
    });
    return;
  }
  // Regular click: open FB filter panel for this IB
  var bin=el.getAttribute('data-bin');
  var svgId=el.getAttribute('data-svgid');
  var hasFb=false;
  var svg=document.getElementById(svgId);
  if(svg){var testDie=svg.querySelector('g[data-fb]');hasFb=testDie&&testDie.getAttribute('data-fb')!=='0';}
  if(!hasFb){
    // No FB data — fall back to toggle
    el.classList.toggle('active');
    var legend2=el.closest('.ibin-legend');
    var activeItems2=legend2?legend2.querySelectorAll('.leg-item.active'):[];
    var activeBins2=Array.from(activeItems2).map(function(i){return i.getAttribute('data-bin');});
    if(svg){svg.querySelectorAll('g[data-bin]').forEach(function(g){if(activeBins2.length===0){g.style.opacity='1';g.style.filter='';}else{var db=g.getAttribute('data-bin');var match=activeBins2.some(function(ab){return db===ab;});g.style.opacity=match?'1':'0.10';g.style.filter=match?'drop-shadow(0 0 3px #000)':'';}});}
    return;
  }
  ibinFbOpen(bin, svgId);
}
var _ibinFbCnts={},_ibinFbTotal=0;
function ibinFbOpen(bin, svgId){
  _ibinFbBin=bin; _ibinFbSvgId=svgId;
  var fbCnts={},ibTotal=0;
  // Collect dies from wafer-filtered sections (respects _wmSel when available)
  var _sel=typeof _wmSel!=='undefined'?_wmSel:new Set();
  var secs=Array.from(document.querySelectorAll('.wm-wafer-section'));
  var toScan=_sel.size>0?secs.filter(function(s){return _sel.has(s.id.replace('wm-section-',''));}):secs;
  toScan.forEach(function(sec){sec.querySelectorAll('g[data-bin]').forEach(function(g){if(g.getAttribute('data-bin')===bin){ibTotal++;var fb=g.getAttribute('data-fb')||'0';fbCnts[fb]=(fbCnts[fb]||0)+1;}});});
  _ibinFbCnts=fbCnts; _ibinFbTotal=ibTotal;
  var scopeEl=document.getElementById('ibin-fb-scope');
  if(scopeEl){scopeEl.textContent=_sel.size>0?'Scope: Wafer '+Array.from(_sel).join(', '):'Scope: All wafers';}
  var fbKeys=Object.keys(fbCnts).filter(function(k){return k!=='0';}).sort(function(a,b){return fbCnts[b]-fbCnts[a];});
  document.getElementById('ibin-fb-title').textContent='IB: '+bin+' \u2014 '+ibTotal.toLocaleString()+' die \u2014 Select FBs to highlight';
  var el=document.getElementById('ibin-fb-cblist');
  var html='';
  if(!fbKeys.length){
    html='<span style="color:#888;font-size:12px">No Functional Bin data available for this IB</span>';
  }else{
    fbKeys.forEach(function(fb){
      var cnt=fbCnts[fb],pct=ibTotal>0?cnt/ibTotal*100:0;
      var _fbd=typeof FBDESC!=='undefined'&&FBDESC[fb]?'<span title="'+_escFb(FBDESC[fb])+'" style="color:#777;font-size:11px;margin-left:3px;max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;vertical-align:bottom;cursor:help">'+_escFb(FBDESC[fb].substring(0,40))+'</span>':'';
      html+='<label class="fb-cbitem"><input type="checkbox" checked data-fb="'+fb+'" onchange="ibinFbApply()"> <b>FB'+fb+'</b>'+_fbd+' <span style="color:#888;font-size:11px">'+cnt+'('+pct.toFixed(1)+'%)</span></label>';
    });
  }
  el.innerHTML=html;
  // Populate die-loc checkboxes
  var dlocCnts={};
  toScan.forEach(function(sec){sec.querySelectorAll('g[data-bin]').forEach(function(g){if(g.getAttribute('data-bin')===bin){var dl=g.getAttribute('data-dielo')||'0';if(dl!=='0')dlocCnts[dl]=(dlocCnts[dl]||0)+1;}});});
  var dlocKeys=Object.keys(dlocCnts).filter(function(k){return k!=='0';}).sort(function(a,b){return parseInt(a)-parseInt(b);});
  var dlocEl=document.getElementById('ibin-dloc-cblist'),dlocRow=document.getElementById('ibin-dloc-row');
  if(dlocKeys.length&&dlocEl&&dlocRow){
    var dlocHtml='';
    dlocKeys.forEach(function(dl){var cnt=dlocCnts[dl],pct=ibTotal>0?cnt/ibTotal*100:0;dlocHtml+='<label class="fb-cbitem"><input type="checkbox" checked data-dielo="'+dl+'" onchange="ibinFbApply()"> <b>Loc '+dl+'</b> <span style="color:#888;font-size:11px">'+cnt+'('+pct.toFixed(1)+'%)</span></label>';});
    dlocEl.innerHTML=dlocHtml;dlocRow.style.display='block';
  }else if(dlocRow){dlocRow.style.display='none';}
  document.getElementById('ibin-fb-panel').classList.add('open');
  ibinFbRenderChart();
  ibinFbApply();
}
function ibinFbRenderChart(){
  var chartEl=document.getElementById('ibin-fb-chart');
  if(!chartEl)return;
  var cbs=document.querySelectorAll('#ibin-fb-cblist input[type=checkbox]');
  var sel=new Set();cbs.forEach(function(cb){if(cb.checked)sel.add(cb.dataset.fb);});
  var fbCnts=_ibinFbCnts||{};
  var total=Math.max(_ibinFbTotal,1);
  var fbKeys=Object.keys(fbCnts).filter(function(k){return k!=='0';}).sort(function(a,b){return fbCnts[b]-fbCnts[a];});
  if(!fbKeys.length){chartEl.innerHTML='';return;}
  var barH=18,gap=4,padL=52,padT=10,padB=8;
  var N=fbKeys.length;
  var H=padT+N*(barH+gap)+padB;
  var maxCnt=fbCnts[fbKeys[0]]||1;
  var barW=260;
  var s='<svg width="'+(padL+barW+145)+'" height="'+H+'" style="display:block;font-family:Arial,sans-serif" xmlns="http://www.w3.org/2000/svg">';
  fbKeys.forEach(function(fb,i){
    var y=padT+i*(barH+gap);
    var cnt=fbCnts[fb],pct=total>0?cnt/total*100:0;
    var bw=Math.max(2,Math.round(cnt/maxCnt*barW));
    var active=sel.size===0||sel.has(fb);
    var fill=active?'#1a4a8a':'#ccd5e0';
    var txtFill=active?'#333':'#aaa';
    s+='<rect x="'+padL+'" y="'+y+'" width="'+bw+'" height="'+barH+'" fill="'+fill+'" rx="2"/>';
    s+='<text x="'+(padL-4)+'" y="'+(y+barH*0.72)+'" text-anchor="end" font-size="11" fill="'+txtFill+'">FB'+fb+'</text>';
    s+='<text x="'+(padL+bw+5)+'" y="'+(y+barH*0.72)+'" font-size="10" fill="'+txtFill+'">'+cnt+' ('+pct.toFixed(1)+'%)</text>';
  });
  s+='</svg>';
  chartEl.innerHTML=s;
}
function ibinFbApply(){
  if(!_ibinFbBin)return;
  var cbs=document.querySelectorAll('#ibin-fb-cblist input[type=checkbox]');
  var sel=new Set();cbs.forEach(function(cb){if(cb.checked)sel.add(cb.dataset.fb);});
  var dlocCbs=document.querySelectorAll('#ibin-dloc-cblist input[type=checkbox]');
  var dlocSel=new Set();dlocCbs.forEach(function(cb){if(cb.checked)dlocSel.add(cb.dataset.dielo);});
  var dlocRowEl=document.getElementById('ibin-dloc-row');
  var dlocActive=!!(dlocRowEl&&dlocRowEl.style.display!=='none');
  var _sel=typeof _wmSel!=='undefined'?_wmSel:new Set();
  var secs=Array.from(document.querySelectorAll('.wm-wafer-section'));
  var toApply=_sel.size>0?secs.filter(function(s){return _sel.has(s.id.replace('wm-section-',''));}):secs;
  toApply.forEach(function(sec){
    sec.querySelectorAll('g[data-bin]').forEach(function(g){
      var db=g.getAttribute('data-bin'),fb=g.getAttribute('data-fb')||'0',dl=g.getAttribute('data-dielo')||'0';
      if(db===_ibinFbBin){
        var fbOk=sel.size===0||sel.has(fb);var dlOk=!dlocActive||(dlocSel.size>0&&dlocSel.has(dl));
        if(fbOk&&dlOk){g.style.opacity='1';g.style.filter='drop-shadow(0 0 3px #ff0)';}
        else{g.style.opacity='0.08';g.style.filter='';}
      }else{g.style.opacity='0.10';g.style.filter='';}
    });
  });
  if(_ibinFbSvgId){var _orig=document.getElementById(_ibinFbSvgId);if(_orig&&!_orig.closest('.wm-wafer-section')){_orig.querySelectorAll('g[data-bin]').forEach(function(g){var db=g.getAttribute('data-bin'),fb=g.getAttribute('data-fb')||'0',dl=g.getAttribute('data-dielo')||'0';if(db===_ibinFbBin){var fbOk=sel.size===0||sel.has(fb);var dlOk=!dlocActive||(dlocSel.size>0&&dlocSel.has(dl));if(fbOk&&dlOk){g.style.opacity='1';g.style.filter='drop-shadow(0 0 3px #ff0)';}else{g.style.opacity='0.08';g.style.filter='';}}else{g.style.opacity='0.10';g.style.filter='';}});}}
  ibinFbRenderChart();
  var _hwM=document.getElementById('ibin-hw-modal');if(_hwM&&_hwM.classList.contains('open')&&typeof _ibinHwBin!=='undefined'&&_ibinHwBin){ibinHwRenderList();ibinHwApply();}
}
function ibinFbClear(){
  document.querySelectorAll('.wm-wafer-section').forEach(function(sec){sec.querySelectorAll('g[data-bin]').forEach(function(g){g.style.opacity='1';g.style.filter='';});});
  if(_ibinFbSvgId){var svg=document.getElementById(_ibinFbSvgId);if(svg)svg.querySelectorAll('g[data-bin]').forEach(function(g){g.style.opacity='1';g.style.filter='';});}
  document.getElementById('ibin-fb-panel').classList.remove('open');
  _ibinFbBin=null;_ibinFbSvgId=null;
}
function ibinFbClose(){ibinFbClear();if(typeof ibinHwClose!=='undefined')ibinHwClose();}
(function(){
  var handle=document.getElementById('fb-panel-resize');
  var panel=document.getElementById('ibin-fb-panel');
  if(!handle||!panel)return;
  handle.addEventListener('mousedown',function(e){
    e.preventDefault();
    handle.classList.add('dragging');
    var startY=e.clientY,startH=panel.getBoundingClientRect().height;
    function mm(ev){panel.style.height=Math.max(60,startH-(ev.clientY-startY))+'px';}
    function mu(){document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);handle.classList.remove('dragging');}
    document.addEventListener('mousemove',mm);document.addEventListener('mouseup',mu);
  });
})();
function ibinFbSelAll(){document.querySelectorAll('#ibin-fb-cblist input').forEach(function(cb){cb.checked=true;});ibinFbApply();}
function ibinFbClrAll(){document.querySelectorAll('#ibin-fb-cblist input').forEach(function(cb){cb.checked=false;});ibinFbApply();}
function ibinDlocSelAll(){document.querySelectorAll('#ibin-dloc-cblist input').forEach(function(cb){cb.checked=true;});ibinFbApply();}
function ibinDlocClrAll(){document.querySelectorAll('#ibin-dloc-cblist input').forEach(function(cb){cb.checked=false;});ibinFbApply();}
</script>
<style>
.leg-item:hover { background:#eef4ff !important; }
.leg-item.active { background:#ddeeff; outline:2px solid #0055ff; border-radius:4px; }
.ibin-legend { border:1px solid #ccc; border-radius:4px; padding:4px 6px; background:#fafafa;
               display:flex; flex-direction:column; gap:1px; align-items:stretch; min-width:160px; max-width:260px; }
.ibin-legend > div:first-child { margin-bottom:2px; }
.mode-btn { padding:5px 14px; border:1px solid #aaa; border-radius:4px; cursor:pointer;
            background:#f0f0f0; font-size:12px; font-family:Arial,sans-serif; }
.mode-btn.mode-active { background:#2c3e50; color:#fff; border-color:#2c3e50; font-weight:bold; }
.mode-bar { display:flex; align-items:center; gap:8px; margin-bottom:12px; }
.wm-row{cursor:pointer}
.wm-row:hover td{background:#f0f4ff!important}
.wm-row.wm-active td{background:#ddeeff!important;font-weight:bold}
.ibin-hw-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:transparent;z-index:20000;pointer-events:none}
.ibin-hw-overlay.open{display:block}
.ibin-hw-box{position:fixed;top:80px;left:50%;transform:translateX(-50%);width:50vw;height:50vh;min-width:340px;min-height:200px;max-width:96vw;max-height:90vh;background:#fff;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.35);display:none;flex-direction:column;pointer-events:auto;resize:both;overflow:hidden}
.ibin-hw-overlay.open .ibin-hw-box{display:flex}
.ibin-hw-drag{cursor:move;padding:10px 14px 6px;background:#2c3e50;border-radius:6px 6px 0 0;color:#fff;display:flex;align-items:center;user-select:none;flex-shrink:0}
.ibin-hw-body{padding:10px 14px 14px;flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden}
.ibin-hw-tbl{border-collapse:collapse;font-size:11px;width:100%}
.ibin-hw-tbl th{background:#2c3e50;color:#fff;padding:4px 10px;white-space:nowrap;text-align:left}
.ibin-hw-tbl td{padding:3px 10px;border-bottom:1px solid #eee;white-space:nowrap}
.ibin-hw-tbl tr:hover td{background:#f0f4ff}
.hw-btn{font-size:10px;padding:2px 6px;cursor:pointer;border:1px solid #2980b9;background:#ebf5fb;border-radius:3px;color:#1a5276}
.hw-btn:hover{background:#d6eaf8}</style>"""
            import json as _json_fbdesc_ibin
            _IBIN_JS = ('<script>\nvar FBDESC=' + _json_fbdesc_ibin.dumps(_fb_desc_wm)
                        + ';\nfunction _escFb(s){return String(s).replace(/&/g,\'&amp;\').replace(/</g,\'&lt;\').replace(/>/g,\'&gt;\');}\n</script>\n'
                        ) + _IBIN_JS
            if _hw_cols_wm:
                _hw_show_fn = (
                    'var _ibinHwBin=null,_ibinHwSel=new Set(),_ibinHwColFilter={},_ibinHwGroupByCols=null;'
                    'function showIbHwModal(label){'
                    '_ibinHwBin=label;'
                    'var modal=document.getElementById("ibin-hw-modal");'
                    'var title=document.getElementById("ibin-hw-modal-title");'
                    'if(!modal)return;'
                    'if(title)title.textContent="HW Breakdown \u2014 "+label;'
                    '_ibinHwGroupByCols=null;'  # reset group-by on new bin
                    'ibinHwRenderList();'
                    'modal.classList.add("open");}'
                    'function _ibinHwGetOrderedCols(){'
                    'var cols=(typeof HW_COLS!=="undefined")?HW_COLS:[];'
                    'var _hwPrefOrder=["Cell ID","Unit Tester ID","Unit Tester Site ID","CellID","UnitTesterID","UnitTesterSiteID","Unit TIU","Thermal Head Id"];'
                    'return _hwPrefOrder.filter(function(c){return cols.indexOf(c)>=0;}).concat(cols.filter(function(c){return _hwPrefOrder.indexOf(c)<0&&c.toLowerCase().indexOf("sort partial wafer")<0;})).concat(cols.filter(function(c){return c.toLowerCase().indexOf("sort partial wafer")>=0;}));}'
                    'function ibinHwRenderList(){'
                    'if(!_ibinHwBin)return;'
                    'var container=document.getElementById("ibin-hw-modal-body");'
                    'var cols=(typeof HW_COLS!=="undefined")?HW_COLS:[];'
                    'var tbl=(typeof HW_COMBO_TABLE!=="undefined")?HW_COMBO_TABLE:[];'
                    'if(!container){return;}'
                    'if(!tbl.length){container.innerHTML=\'<p style="color:#888">No hardware data.</p>\';return;}'
                    'var cbs=document.querySelectorAll(\'#ibin-fb-cblist input[type=checkbox]\');'
                    'var fbSel=new Set();cbs.forEach(function(cb){if(cb.checked)fbSel.add(cb.dataset.fb);});'
                    'var wmSel=typeof _wmSel!=="undefined"?_wmSel:new Set();'
                    'var orderedCols=_ibinHwGetOrderedCols();'
                    # initialise group-by to all columns on first render
                    'if(_ibinHwGroupByCols===null){_ibinHwGroupByCols=new Set(orderedCols);}'
                    'var activeCols=orderedCols.filter(function(c){return _ibinHwGroupByCols.has(c);});'
                    # collect raw die counts per (wafer, hwIdx)
                    'var rawEntries=[],grandTotal=0,_wHw={};'
                    'var secs=Array.from(document.querySelectorAll(".wm-wafer-section"));'
                    'var actSecs=wmSel.size>0?secs.filter(function(s){return wmSel.has(s.id.replace("wm-section-",""));}):secs;'
                    'actSecs.forEach(function(sec){'
                    '  var w=sec.id.replace("wm-section-","");if(!_wHw[w])_wHw[w]={};'
                    '  sec.querySelectorAll("g[data-bin]").forEach(function(g){'
                    '    var db=g.getAttribute("data-bin"),fb=g.getAttribute("data-fb")||"0",hw=g.getAttribute("data-hw")||"0";'
                    '    if(db===_ibinHwBin&&(fbSel.size===0||fbSel.has(fb))){_wHw[w][hw]=(_wHw[w][hw]||0)+1;grandTotal++;}'
                    '  });'
                    '});'
                    'if(wmSel.size===0&&grandTotal===0&&typeof _ibinFbSvgId!=="undefined"&&_ibinFbSvgId){'
                    '  var orig=document.getElementById(_ibinFbSvgId);'
                    '  if(orig&&!orig.closest(".wm-wafer-section")){'
                    '    if(!_wHw[""])_wHw[""]={};'
                    '    orig.querySelectorAll("g[data-bin]").forEach(function(g){'
                    '      var db=g.getAttribute("data-bin"),fb=g.getAttribute("data-fb")||"0",hw=g.getAttribute("data-hw")||"0";'
                    '      if(db===_ibinHwBin&&(fbSel.size===0||fbSel.has(fb))){_wHw[""][hw]=(_wHw[""][hw]||0)+1;grandTotal++;}'
                    '    });'
                    '  }'
                    '}'
                    'Object.keys(_wHw).forEach(function(w){Object.keys(_wHw[w]).forEach(function(hw){rawEntries.push({lot:_ibinLot||"?",wafer:w,hwIdx:hw,cnt:_wHw[w][hw]});});});'
                    # re-group by activeCols composite key
                    'var groupMap={};'
                    'rawEntries.forEach(function(e){'
                    '  var combo=tbl[parseInt(e.hwIdx)]||{};'
                    '  var key=activeCols.length>0?activeCols.map(function(c){return String(combo[c]||"");}).join("\\x00"):("__hw__"+e.hwIdx);'
                    '  if(!groupMap[key])groupMap[key]={lot:e.lot,wafer:"(all)",hwIdx:e.hwIdx,cnt:0,combo:combo};'
                    '  groupMap[key].cnt+=e.cnt;'
                    '});'
                    'var entries=Object.values(groupMap).sort(function(a,b){return b.cnt-a.cnt;});'
                    'if(!entries.length){container.innerHTML=\'<p style="color:#888">No matching die for current selection.</p>\';return;}'
                    'var filtered=entries.filter(function(e){'
                    '  var pass=true;'
                    '  Object.keys(_ibinHwColFilter).forEach(function(c){'
                    '    if(!pass)return;var q=(_ibinHwColFilter[c]||"").toLowerCase();if(!q)return;'
                    '    var v=String(e.combo[c]||"");'
                    '    if(v.toLowerCase().indexOf(q)<0)pass=false;'
                    '  });return pass;'
                    '});'
                    'var fbLabel=fbSel.size?" [FB "+Array.from(fbSel).join(",")+"]":"";'
                    # build group-by selector bar
                    'var gbBar=\'<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:5px 6px 5px;background:#f0f4ff;border-radius:4px;border:1px solid #c5d4f0;margin-bottom:7px">\''
                    '  +\'<span style="font-size:11px;font-weight:bold;color:#2c3e50;white-space:nowrap">Group By:</span>\';'
                    'orderedCols.forEach(function(c){'
                    '  var chk=_ibinHwGroupByCols.has(c)?"checked":"";'
                    '  gbBar+=\'<label style="font-size:11px;display:flex;align-items:center;gap:3px;cursor:pointer;white-space:nowrap">\''
                    '    +\'<input type="checkbox" \'+chk+\' data-gbcol="\'+c.replace(/"/g,"&quot;")+\'" onchange="ibinHwGbChange(this)"> \'+c+\'</label>\';'
                    '});'
                    'gbBar+=\'<button class="hw-btn" onclick="ibinHwGbAll()">All</button>\''
                    '  +\'<button class="hw-btn" onclick="ibinHwGbNone()">None</button></div>\';'
                    'var hdr=\'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap">\''
                    '  +\'<span style="color:#888;font-size:12px">\'+filtered.length+\' / \'+entries.length+\' rows &nbsp;&bull;&nbsp; \'+grandTotal.toLocaleString()+\' die\'+fbLabel+\'</span>\''
                    '  +\'<button class="hw-btn" onclick="ibinHwSelAll()">✓ All</button>\''
                    '  +\'<button class="hw-btn" onclick="ibinHwClrAll()">✗ None</button>\''
                    '  +\'<button class="hw-btn" onclick="ibinHwClrColFilters()">Clear Filters</button>\''
                    '  +\'</div>\';'
                    'var displayCols=activeCols.length>0?activeCols:orderedCols;'
                    'var th=\'<tr><th style="width:30px"></th>\'+["Count","%"].concat(displayCols).map(function(c){return\'<th style="text-align:left;white-space:normal;word-wrap:break-word">\'+c+\'</th>\';}).join("")+\'</tr>\';'
                    'var filterRow=\'<tr><td></td>\'+["Count","%"].concat(displayCols).map(function(c){'
                    '  if(c==="Count"||c==="%")return"<td></td>";'
                    '  var val=(_ibinHwColFilter[c]||"").replace(/"/g,"&quot;");'
                    '  return\'<td><input type="text" data-hw-fcol="\'+c+\'" value="\'+val+\'" placeholder="\u2026" style="width:100%;box-sizing:border-box;font-size:11px;padding:2px 4px;border:1px solid #ccc;border-radius:3px" oninput="ibinHwTxtFilter(this)"></td>\';'
                    '}).join("")+\'</tr>\';'
                    'var trs=filtered.map(function(e){'
                    '  var pct=grandTotal>0?(e.cnt/grandTotal*100).toFixed(1):"0.0";'
                    '  var sel=_ibinHwSel.size===0||_ibinHwSel.has(e.hwIdx);'
                    '  var op=sel?"1":"0.4";var chk=sel?"checked":"";'
                    '  return\'<tr style="opacity:\'+op+\'">\''
                    '    +\'<td><input type="checkbox" data-hw-idx="\'+e.hwIdx+\'" \'+chk+\' onclick="event.stopPropagation();ibinHwChkChange(this)"></td>\''
                    '    +\'<td>\'+e.cnt.toLocaleString()+\'</td><td>\'+pct+\'%</td>\''
                    '    +displayCols.map(function(c){return"<td>"+String(e.combo[c]||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")+"</td>";}).join("")'
                    '    +\'</tr>\';'
                    '}).join("");'
                    'container.innerHTML=gbBar+hdr+\'<div style="overflow-y:auto;flex:1;min-height:0"><table class="ibin-hw-tbl" style="width:100%;table-layout:auto"><thead>\'+th+filterRow+\'</thead><tbody>\'+trs+\'</tbody></table></div>\';'
                    '}'
                    'function ibinHwGbChange(cb){'
                    'var c=cb.getAttribute("data-gbcol");'
                    'if(cb.checked){_ibinHwGroupByCols.add(c);}else{_ibinHwGroupByCols.delete(c);}'
                    '_ibinHwColFilter={};_ibinHwSel.clear();ibinHwRenderList();}'
                    'function ibinHwGbAll(){'
                    'var orderedCols=_ibinHwGetOrderedCols();'
                    'orderedCols.forEach(function(c){_ibinHwGroupByCols.add(c);});'
                    '_ibinHwColFilter={};_ibinHwSel.clear();ibinHwRenderList();}'
                    'function ibinHwGbNone(){'
                    '_ibinHwGroupByCols.clear();'
                    '_ibinHwColFilter={};_ibinHwSel.clear();ibinHwRenderList();}'
                    'function ibinHwTxtFilter(input){'
                    'var col=input.getAttribute("data-hw-fcol");var val=input.value;'
                    'if(val){_ibinHwColFilter[col]=val;}else{delete _ibinHwColFilter[col];}'
                    'ibinHwRenderList();}'
                    'function ibinHwClrColFilters(){_ibinHwColFilter={};ibinHwRenderList();}'
                    'function ibinHwRowClick(hwIdx){'
                    'var cb=document.querySelector(\'#ibin-hw-modal-body input[data-hw-idx="\'+hwIdx+\'"]\');'
                    'if(cb){cb.checked=!cb.checked;ibinHwChkChange(cb);}}'
                    'function ibinHwChkChange(cb){'
                    'var all=document.querySelectorAll(\'#ibin-hw-modal-body input[type=checkbox]\');'
                    'var anyUnchk=false;all.forEach(function(c){if(!c.checked)anyUnchk=true;});'
                    '_ibinHwSel.clear();'
                    'if(anyUnchk){all.forEach(function(c){if(c.checked)_ibinHwSel.add(c.dataset.hwIdx);});}'
                    'if(_ibinHwSel.size===0&&anyUnchk)_ibinHwSel.add(\'__none__\');'
                    'ibinHwRenderList();ibinHwApply();}'
                    'function ibinHwSelAll(){'
                    'document.querySelectorAll(\'#ibin-hw-modal-body input[type=checkbox]\').forEach(function(c){c.checked=true;});'
                    '_ibinHwSel.clear();ibinHwApply();ibinHwRenderList();}'
                    'function ibinHwClrAll(){'
                    'var all=document.querySelectorAll(\'#ibin-hw-modal-body input[type=checkbox]\');'
                    'all.forEach(function(c){c.checked=false;});_ibinHwSel.clear();_ibinHwSel.add(\'__none__\');ibinHwApply();ibinHwRenderList();}'
                    'function ibinHwApply(){'
                    'if(!_ibinHwBin)return;'
                    'var cbs=document.querySelectorAll(\'#ibin-fb-cblist input[type=checkbox]\');'
                    'var fbSel=new Set();cbs.forEach(function(cb){if(cb.checked)fbSel.add(cb.dataset.fb);});'
                    'var wmSel=typeof _wmSel!=="undefined"?_wmSel:new Set();'
                    'var secs=Array.from(document.querySelectorAll(".wm-wafer-section"));'
                    'var actSecs=wmSel.size>0?secs.filter(function(s){return wmSel.has(s.id.replace("wm-section-",""));}):secs;'
                    'function _applyDie(g){'
                    '  var db=g.getAttribute("data-bin"),fb=g.getAttribute("data-fb")||"0",hw=g.getAttribute("data-hw")||"0";'
                    '  var inBin=db===_ibinHwBin,fbMatch=fbSel.size===0||fbSel.has(fb),hwMatch=_ibinHwSel.size===0||_ibinHwSel.has(hw);'
                    '  if(inBin&&fbMatch&&hwMatch){g.style.opacity="1";g.style.filter="drop-shadow(0 0 3px #e67e22)";}'
                    '  else if(inBin){g.style.opacity="0.12";g.style.filter="";}'
                    '  else{g.style.opacity="0.08";g.style.filter="";}}'
                    'actSecs.forEach(function(sec){sec.querySelectorAll("g[data-bin]").forEach(_applyDie);});'
                    'if(typeof _ibinFbSvgId!=="undefined"&&_ibinFbSvgId){'
                    '  var orig=document.getElementById(_ibinFbSvgId);'
                    '  if(orig&&!orig.closest(".wm-wafer-section"))orig.querySelectorAll("g[data-bin]").forEach(_applyDie);'
                    '}}'
                    'function ibinHwClose(){'
                    'var modal=document.getElementById("ibin-hw-modal");'
                    'if(modal)modal.classList.remove("open");'
                    '_ibinHwBin=null;_ibinHwSel.clear();_ibinHwColFilter={};'
                    'if(typeof ibinFbApply!=="undefined")ibinFbApply();}'
                    'document.addEventListener("DOMContentLoaded",function(){'
                    'var box=document.getElementById("ibin-hw-box");'
                    'var drag=document.getElementById("ibin-hw-drag");'
                    'if(!box||!drag)return;'
                    'var ox=0,oy=0,bx=0,by=0;'
                    'drag.addEventListener("mousedown",function(e){'
                    '  if(e.target.tagName==="BUTTON")return;'
                    '  e.preventDefault();'
                    '  var r=box.getBoundingClientRect();'
                    '  box.style.transform="none";'
                    '  bx=r.left;by=r.top;'
                    '  box.style.left=bx+"px";box.style.top=by+"px";'
                    '  ox=e.clientX-bx;oy=e.clientY-by;'
                    '  function mm(ev){'
                    '    bx=ev.clientX-ox;by=ev.clientY-oy;'
                    '    var maxX=window.innerWidth-box.offsetWidth,maxY=window.innerHeight-40;'
                    '    bx=Math.max(0,Math.min(bx,maxX));by=Math.max(0,Math.min(by,maxY));'
                    '    box.style.left=bx+"px";box.style.top=by+"px";}'
                    '  function mu(){document.removeEventListener("mousemove",mm);document.removeEventListener("mouseup",mu);}'
                    '  document.addEventListener("mousemove",mm);document.addEventListener("mouseup",mu);'
                    '});'
                    '});'
                )
                _IBIN_JS = (
                    '<script>\nvar HW_DATA=' + _json_fbdesc_ibin.dumps(_hw_data_wm)
                    + ';\nvar HW_COMBO_TABLE=' + _json_fbdesc_ibin.dumps(_hw_combo_table_js)
                    + ';\nvar HW_COLS=' + _json_fbdesc_ibin.dumps(_hw_cols_wm)
                    + ';\nvar _ibinLot=' + _json_fbdesc_ibin.dumps(str(lot_label))
                    + ';\n' + _hw_show_fn + '\n</script>\n'
                ) + _IBIN_JS
                # Inject HW Breakdown button into the FB filter panel header
                _IBIN_JS = _IBIN_JS.replace(
                    '<button class="fb-btn" onclick="ibinFbClose()">&times; Close</button>',
                    '<button class="hw-btn" onclick="if(typeof showIbHwModal!==\'undefined\'&&_ibinFbBin)showIbHwModal(_ibinFbBin)">&#128296; HW Breakdown</button>'
                    '<button class="fb-btn" onclick="ibinFbClose()">&times; Close</button>',
                    1
                )
            _IBIN_JS += (
                '<script>\n'
                'var _wmSel=new Set();\n'
                'var _wmAllWafers=[];\n'
                'var _wmLastSel=null;\n'
                'function wmSelectWafer(wafer,ev){\n'
                '  var isCtrl=ev&&(ev.ctrlKey||ev.metaKey);\n'
                '  var isShift=ev&&ev.shiftKey;\n'
                '  if(isShift&&_wmLastSel!==null&&_wmAllWafers.length){\n'
                '    var from=_wmAllWafers.indexOf(_wmLastSel);\n'
                '    var to=_wmAllWafers.indexOf(wafer);\n'
                '    if(from>=0&&to>=0){var lo=Math.min(from,to),hi=Math.max(from,to);for(var k=lo;k<=hi;k++)_wmSel.add(_wmAllWafers[k]);}\n'
                '    else _wmSel.add(wafer);\n'
                '  } else if(isCtrl){\n'
                '    if(_wmSel.has(wafer))_wmSel.delete(wafer); else _wmSel.add(wafer);\n'
                '  } else {\n'
                '    if(_wmSel.size===1&&_wmSel.has(wafer)){_wmSel.clear();}\n'
                '    else{_wmSel.clear();_wmSel.add(wafer);}\n'
                '  }\n'
                '  _wmLastSel=wafer;\n'
                '  _wmUpdateView();\n'
                '}\n'
                'function _wmUpdateView(){\n'
                '  document.querySelectorAll(\'.wm-row\').forEach(function(r){\n'
                '    var w=r.id.replace(\'wm-row-\',\'\');\n'
                '    if(_wmSel.has(w)){r.classList.add(\'wm-active\');r.style.background=\'#ddeeff\';}\n'
                '    else{r.classList.remove(\'wm-active\');r.style.background=\'\';}\n'
                '  });\n'
                '  var secs=document.querySelectorAll(\'.wm-wafer-section\');\n'
                '  var _hwMvUp=document.getElementById(\'ibin-hw-modal\');if(_hwMvUp&&_hwMvUp.classList.contains(\'open\')&&typeof _ibinHwBin!==\'undefined\'&&_ibinHwBin){ibinHwRenderList();ibinHwApply();}\n'
                '  if(_wmSel.size===0){secs.forEach(function(s){s.style.display=\'\';});return;}\n'
                '  secs.forEach(function(s){\n'
                '    var w=s.id.replace(\'wm-section-\',\'\');\n'
                '    if(_wmSel.has(w))_wmEnsureRendered(w);\n'
                '    s.style.display=_wmSel.has(w)?\'\':\'none\';\n'
                '  });\n'
                '  var firstW=null;\n'
                '  for(var k=0;k<_wmAllWafers.length;k++){if(_wmSel.has(_wmAllWafers[k])){firstW=_wmAllWafers[k];break;}}\n'
                '  if(firstW){var el=document.getElementById(\'wm-section-\'+firstW);if(el)setTimeout(function(){el.scrollIntoView({behavior:\'smooth\',block:\'start\'});},50);}\n'
                '  if(typeof _ibinFbBin!==\'undefined\'&&_ibinFbBin&&document.getElementById(\'ibin-fb-panel\').classList.contains(\'open\'))ibinFbOpen(_ibinFbBin,_ibinFbSvgId);\n'
                '  var _hwMv=document.getElementById(\'ibin-hw-modal\');if(_hwMv&&_hwMv.classList.contains(\'open\')&&typeof _ibinHwBin!==\'undefined\'&&_ibinHwBin){ibinHwRenderList();ibinHwApply();}\n'
                '}\n'
                'function wmShowAll(){\n'
                '  _wmSel.clear();_wmLastSel=null;\n'
                '  document.querySelectorAll(\'.wm-row\').forEach(function(r){r.classList.remove(\'wm-active\');r.style.background=\'\';});\n'
                '  document.querySelectorAll(\'.wm-wafer-section\').forEach(function(s){s.style.display=\'\';});\n'
                '  var _hwMvSa=document.getElementById(\'ibin-hw-modal\');if(_hwMvSa&&_hwMvSa.classList.contains(\'open\')&&typeof _ibinHwBin!==\'undefined\'&&_ibinHwBin){ibinHwRenderList();ibinHwApply();}\n'
                '}\n'
'// Lazy SVG render — <template> elements used for non-first wafers\n'
                'var _wmRendered=new Set();\n'
                'function _wmEnsureRendered(w){\n'
                '  var ws=String(w);if(_wmRendered.has(ws))return;_wmRendered.add(ws);\n'
                '  var tmpl=document.getElementById(\'wm-svg-tmpl-\'+ws);if(!tmpl)return;\n'
                '  var wrap=document.getElementById(\'wsvg-wrap-\'+ws);if(!wrap)return;\n'
                '  wrap.innerHTML=\'\';wrap.appendChild(document.importNode(tmpl.content,true));tmpl.remove();\n'
                '}\n'
                '(function(){\n'
                '  if(typeof IntersectionObserver===\'undefined\')return;\n'
                '  var _wio=new IntersectionObserver(function(ents){\n'
                '    ents.forEach(function(e){if(e.isIntersecting){_wmEnsureRendered(e.target.id.replace(\'wm-section-\',\'\'));_wio.unobserve(e.target);}});\n'
                '  },{rootMargin:\'400px\'});\n'
                '  document.querySelectorAll(\'.wm-wafer-section[id]\').forEach(function(s){_wio.observe(s);});\n'
                '})();\n'
                '// Hash routing: #wafer-W or #wafers-W1,W2,W3\n'
                '(function(){\n'
                '  function _chk(){\n'
                '    var h=window.location.hash;\n'
                '    if(h&&h.indexOf(\'#wafer-\')===0){\n'
                '      var w=decodeURIComponent(h.slice(7)); _wmSel.clear();_wmSel.add(w);_wmLastSel=w;_wmUpdateView();\n'
                '    } else if(h&&h.indexOf(\'#wafers-\')===0){\n'
                '      var ws=decodeURIComponent(h.slice(8)).split(\',\');\n'
                '      _wmSel.clear(); ws.forEach(function(w){if(w)_wmSel.add(w);});\n'
                '      _wmLastSel=ws[ws.length-1]||null; _wmUpdateView();\n'
                '    }\n'
                '  }\n'
                '  window.addEventListener(\'load\',function(){\n'
                '    _wmAllWafers=Array.from(document.querySelectorAll(\'.wm-row\')).map(function(r){return r.id.replace(\'wm-row-\',\'\');});\n'
                '    _chk();\n'
                '  });\n'
                '  window.addEventListener(\'hashchange\',_chk);\n'
                '})();\n'
                '</script>\n'
            )
            _wm_mat_th = '<th>Material Type</th>' if mat_col_wm else ''
            _toggle_bar = ''

            page = f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{_HTML_STYLE}
.wpa-overlay{{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.45);z-index:23000;pointer-events:none}}
.wpa-overlay.open{{display:block;pointer-events:none}}
.wpa-box{{position:absolute;left:3vw;top:36px;background:#f0f2f5;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.35);width:94vw;max-width:1400px;height:80vh;min-width:640px;min-height:360px;max-height:95vh;display:flex;flex-direction:column;pointer-events:auto;overflow:hidden;resize:both}}
.wpa-drag{{cursor:move;background:#145a32;color:#fff;padding:8px 14px;border-radius:8px 8px 0 0;display:flex;align-items:center;justify-content:space-between;user-select:none;flex-shrink:0}}
.wpa-body{{flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden;padding:10px}}
.wpa-tabs{{display:flex;gap:4px;margin-bottom:8px;flex-shrink:0}}
.wpa-tab{{padding:5px 14px;border:1px solid #aaa;border-radius:4px;cursor:pointer;background:#f0f0f0;font-size:12px;font-family:Arial,sans-serif}}
.wpa-tab.on{{background:#145a32;color:#fff;border-color:#145a32;font-weight:bold}}
.wpa-pane{{display:none;flex:1;min-height:0;overflow:auto}}
.wpa-pane.on{{display:flex;flex-direction:row;gap:16px;flex-wrap:wrap;align-items:flex-start}}
.wpa-pane-scroll{{display:none;flex:1;min-height:0;overflow:auto;padding:4px}}
.wpa-pane-scroll.on{{display:block}}
.wm-split-bar{{height:8px;background:#d1d5db;cursor:ns-resize;display:flex;align-items:center;justify-content:center;user-select:none;margin:4px 0;border-radius:4px;transition:background .15s}}
.wm-split-bar:hover,.wm-split-bar.dragging{{background:#94a3b8}}
.wm-split-grip{{color:#9ca3af;font-size:16px;letter-spacing:3px;line-height:1;pointer-events:none}}
#wm-top-panel.constrained{{overflow:auto}}
</style></head><body>
{_IBIN_JS}
<div id="wm-top-panel"><div style="display:flex;align-items:flex-start;gap:12px;flex-wrap:wrap">
<div id="comp-svg-wrap" style="width:{_comp_max_w}px;flex-shrink:0;position:relative">{_comp_svg_str}<div class="comp-corner-resize" id="comp-corner-resize" onmousedown="startCornerResize(event,&quot;comp-svg-wrap&quot;,&quot;comp-svg-w&quot;)"></div></div>
<div id="comp-legend" style="flex-shrink:0;padding-top:32px">{_comp_leg_html}</div>
</div>
<script>
(function(){{
  var TOP_FRAC = {_top_axes_top_frac:.4f};
  window.alignLegend = function alignLegend() {{
    var wrap = document.getElementById('comp-svg-wrap');
    var leg  = document.getElementById('comp-legend');
    if (!wrap || !leg) return;
    var h = wrap.getBoundingClientRect().height;
    if (h > 0) leg.style.paddingTop = Math.round((1.0 - TOP_FRAC) * h + 20) + 'px';
  }}
  window.addEventListener('load', alignLegend);
  window.addEventListener('resize', alignLegend);
}})();
// ── Resize handles ─────────────────────────────────────────────────────────
(function(){{
  var LS='ibwm_';
  function sv(k,v){{try{{localStorage.setItem(LS+k,String(v));}}catch(e){{}}}}
  function gv(k){{try{{return localStorage.getItem(LS+k);}}catch(e){{return null;}}}}
  window.startSvgResize=function(e,wrapperId,storageKey){{
    e.preventDefault();
    var wrap=document.getElementById(wrapperId);if(!wrap)return;
    var handle=e.currentTarget;handle.classList.add('dragging');
    var svg=wrap.querySelector('svg');
    var startY=e.clientY,startH=svg?parseInt(svg.getAttribute('height')||svg.getBoundingClientRect().height)||400:400;
    function mm(ev){{
      var h=Math.max(80,startH+(ev.clientY-startY));
      if(svg)svg.setAttribute('height',h);
      wrap.style.height=h+'px';
    }}
    function mu(){{
      document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);
      handle.classList.remove('dragging');
      var h=svg?svg.getAttribute('height'):null;
      if(storageKey&&h)sv(storageKey,h);
    }}
    document.addEventListener('mousemove',mm);document.addEventListener('mouseup',mu);
  }};
  window.startCornerResize=function(e,wrapperId,storageKey){{
    e.preventDefault();
    var wrap=document.getElementById(wrapperId);if(!wrap)return;
    var startX=e.clientX,startW=wrap.getBoundingClientRect().width;
    function mm(ev){{
      var w=Math.max(200,startW+(ev.clientX-startX));
      wrap.style.width=w+'px';
      if(typeof window.alignLegend==='function')window.alignLegend();
    }}
    function mu(){{
      document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);
      if(storageKey)sv(storageKey,wrap.getBoundingClientRect().width);
    }}
    document.addEventListener('mousemove',mm);document.addEventListener('mouseup',mu);
  }};
  window.addEventListener('load',function(){{
    var cw=gv('comp-svg-w');
    var cw_el=document.getElementById('comp-svg-wrap');
    if(cw&&cw_el)cw_el.style.width=cw+'px';
  }});
}})();
function wpaOpen(){{
  document.getElementById('wpa-overlay').classList.add('open');
}}
function wpaClose(){{
  document.getElementById('wpa-overlay').classList.remove('open');
}}
function wpaTab(t){{
  document.querySelectorAll('.wpa-tab').forEach(function(b){{b.classList.toggle('on',b.dataset.tab===t);}});
  document.querySelectorAll('.wpa-pane,.wpa-pane-scroll').forEach(function(p){{p.classList.remove('on');}});
  var el=document.getElementById('wpa-pane-'+t);if(el)el.classList.add('on');
}}
(function(){{
  var drag=document.getElementById('wpa-drag');
  var box=document.getElementById('wpa-box');
  if(!drag||!box)return;
  var ox=0,oy=0,bx=0,by=0;
  drag.addEventListener('mousedown',function(e){{
    e.preventDefault();
    ox=e.clientX;oy=e.clientY;
    var r=box.getBoundingClientRect();bx=r.left;by=r.top;
    function mm(ev){{box.style.left=(bx+(ev.clientX-ox))+'px';box.style.top=(by+(ev.clientY-oy))+'px';}}
    function mu(){{document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);}}
    document.addEventListener('mousemove',mm);document.addEventListener('mouseup',mu);
  }});
}})();
window.startPanelSplit=function(e){{
  e.preventDefault();
  var bar=e.currentTarget;bar.classList.add('dragging');
  var top=document.getElementById('wm-top-panel');if(!top)return;
  top.classList.add('constrained');
  var sy=e.clientY,sh=top.getBoundingClientRect().height;
  function mm(ev){{var h=Math.max(80,sh+(ev.clientY-sy));top.style.height=h+'px';}}
  function mu(){{
    document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);
    bar.classList.remove('dragging');
    try{{localStorage.setItem('ibwm_top_h',String(top.getBoundingClientRect().height));}}catch(ex){{}}
  }}
  document.addEventListener('mousemove',mm);document.addEventListener('mouseup',mu);
}};
window.addEventListener('load',function(){{
  try{{
    var h=localStorage.getItem('ibwm_top_h');
    var top=document.getElementById('wm-top-panel');
    if(top&&h){{top.classList.add('constrained');top.style.height=h+'px';}}
  }}catch(ex){{}}
}});
</script>
<div style="display:flex;align-items:center;gap:8px;margin:4px 0 2px">
<span style="font-size:20px;font-weight:bold;color:#2c3e50"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:middle;margin-right:4px"><path d="M10 18h4v-2h-4v2zM3 6v2h18V6H3zm3 7h12v-2H6v2z"/></svg> Wafer Summary</span>
<button style="padding:3px 10px;font-size:17px;cursor:pointer;border:1px solid #bdc3c7;background:#ecf0f1;border-radius:3px;color:#2c3e50" onclick="wmShowAll()">&#9723; Show All</button>

<span style="font-size:17px;color:#888">(click to select \u2014 Ctrl+click for multi-select \u2014 Shift+click for range)</span>
</div>
<table class="wm-sum-tbl">
<thead><tr><th>Wafer</th>{_wm_mat_th}<th>Total Dies</th><th>Pass</th><th>Fail</th></tr></thead>
<tbody>{waf_rows_html}</tbody></table>
</div>
<div class="wm-split-bar" id="wm-split-bar" onmousedown="startPanelSplit(event)"><div class="wm-split-grip">&#xB7;&#xB7;&#xB7;&#xB7;&#xB7;&#xB7;&#xB7;&#xB7;</div></div>
<div id="wm-bot-panel">
<h2>Individual Wafer Maps</h2>
{_toggle_bar}
{per_wafer_html}
</div>
<!-- Wafer Pattern Analysis modal -->
<div class="wpa-overlay" id="wpa-overlay">
  <div class="wpa-box" id="wpa-box">
    <div class="wpa-drag" id="wpa-drag">
      <b>&#127759; Wafer Pattern Analysis &mdash; {lot_label} &mdash; {n_wafers} wafer{"s" if n_wafers!=1 else ""} (mode bin per position)</b>
      <button onclick="wpaClose()" style="background:none;border:none;color:#fff;font-size:20px;cursor:pointer;line-height:1">&times;</button>
    </div>
    <div class="wpa-body">
      <div class="wpa-tabs">
        <button class="wpa-tab on" data-tab="modemap" onclick="wpaTab('modemap')">&#128300; Mode Map</button>
        {'<button class="wpa-tab" data-tab="reticle" onclick="wpaTab(\'reticle\')">&#127760; Reticle</button>' if _ret_tbl_html else ''}
        <button class="wpa-tab" data-tab="guide" onclick="wpaTab('guide')">&#8505; Guide</button>
      </div>
      <div class="wpa-pane on" id="wpa-pane-modemap">
        <div style="flex:0 0 auto">{_pat_svg_str}</div>
        <div style="flex-shrink:0;padding-top:8px">{_pat_leg_html}</div>
      </div>
      {'<div class="wpa-pane-scroll" id="wpa-pane-reticle">' + _ret_tbl_html + '<p style="font-size:11px;color:#666;margin:6px 0 0">Blue lines on the mode map show reticle shot boundaries.</p></div>' if _ret_tbl_html else ''}
      <div class="wpa-pane-scroll" id="wpa-pane-guide">
        <p style="font-size:12px;color:#2c3e50;font-weight:bold;margin:0 0 6px">About this map</p>
        <p style="font-size:12px;color:#444;margin:0 0 8px">Each die position shows the <b>most common (mode) Interface Bin</b> across all {n_wafers} wafer{"s" if n_wafers!=1 else ""} in this lot. This reveals <b>systematic spatial patterns</b> that repeat wafer-to-wafer — such as center/edge/reticle defectivity.</p>
        <p style="font-size:12px;color:#444;margin:0 0 8px"><b>Blue lines</b> show reticle shot boundaries. Failures that align with reticle grid suggest mask or litho issues. Radially symmetric patterns suggest process non-uniformity (CMP, dep, etch).</p>
        <p style="font-size:12px;color:#444;margin:0"><b>Click a bin</b> in the legend to highlight / fade that bin on the map.</p>
      </div>
    </div>
  </div>
</div>
<div id="ibin-hw-modal" class="ibin-hw-overlay">
  <div class="ibin-hw-box" id="ibin-hw-box">
    <div class="ibin-hw-drag" id="ibin-hw-drag">
      <b id="ibin-hw-modal-title" style="font-size:13px;flex:1">HW Breakdown</b>
      <button onclick="ibinHwClose()" style="border:none;background:none;font-size:20px;cursor:pointer;color:#aaa;line-height:1;padding:0 0 0 10px">&times;</button>
    </div>
    <div class="ibin-hw-body" id="ibin-hw-modal-body"></div>
  </div>
</div>
<div id="wpa-die-tip" style="display:none;position:fixed;z-index:30000;background:#1a1a2e;color:#fff;font-size:12px;font-family:Arial,sans-serif;padding:4px 9px;border-radius:4px;pointer-events:none;white-space:nowrap;max-width:300px;box-shadow:0 2px 8px rgba(0,0,0,.45)"></div>
<script>
(function(){{
  var tip=document.getElementById('wpa-die-tip');
  function _showTip(text,x,y){{tip.textContent=text;tip.style.display='block';_moveTip(x,y);}}
  function _moveTip(x,y){{var ox=x+14,oy=y-32;if(ox+tip.offsetWidth>window.innerWidth-6)ox=x-tip.offsetWidth-10;if(oy<4)oy=y+16;tip.style.left=ox+'px';tip.style.top=oy+'px';}}
  function _hideTip(){{tip.style.display='none';}}
  function _initWpaTip(){{
    document.querySelectorAll('#wpa-pane-modemap svg').forEach(function(svg){{
      // Remove native <title> tooltips inside this SVG so the browser tooltip does not
      // show with its built-in delay and does not linger after the mouse leaves a die.
      svg.querySelectorAll('g[data-bin] > title').forEach(function(t){{t.remove();}});
      svg.addEventListener('mousemove',function(e){{
        var el=e.target;
        while(el&&el!==svg){{
          if(el.tagName&&el.tagName.toLowerCase()==='g'&&el.getAttribute('data-bin')){{
            var txt=el.getAttribute('data-bin');
            var fb=el.getAttribute('data-fb');
            if(fb&&fb!=='0')txt+=' (FB '+fb+')';
            _showTip(txt,e.clientX,e.clientY);
            return;
          }}
          el=el.parentElement;
        }}
        _hideTip();
      }});
      svg.addEventListener('mouseleave',_hideTip);
    }});
  }}
  // Hook wpaTab so tooltip init runs when the mode-map pane becomes visible
  var _origWpaOpen=window.wpaOpen;
  window.wpaOpen=function(){{if(_origWpaOpen)_origWpaOpen();setTimeout(_initWpaTip,60);}};
  var _origWpaTab=window.wpaTab;
  window.wpaTab=function(t){{if(_origWpaTab)_origWpaTab(t);if(t==='modemap')setTimeout(_initWpaTip,60);}};
  window.addEventListener('load',_initWpaTip);
}})();
</script>
</body></html>"""

            out_html = heat_dir / f'{csvp.stem}_IBIN_WaferMap_{lot_safe}.html'
            out_html.write_text(_wm_inject(page), encoding='utf-8')
            print(f'Wrote {out_html}')
            out_paths.append((lot_label, out_html))

        return out_paths

    except Exception as exc:
        print(f'generate_all_ibin_wafer_map: {exc}', file=sys.stderr)
        return []


def main():
    if len(sys.argv) < 2:
        print('Usage: generate_heatmap_from_csv.py <csv_path> [out_dir] [fail_bucket_table_path]')
        sys.exit(2)
    # support optional --gui flag anywhere in argv to save heatmaps to output/heatmap/
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    gui = '--gui' in sys.argv or '--Gui' in sys.argv
    html_only = '--html-only' in sys.argv or '--html' in sys.argv
    interactive = '--static' not in sys.argv
    wafermap_only = '--wafermap-only' in sys.argv
    bindef = next((a[len('--bindef='):] for a in sys.argv if a.startswith('--bindef=')), None)
    if len(args) < 1:
        print('Usage: generate_heatmap_from_csv.py <csv_path> [out_dir] [fail_bucket_table_path] [--gui]')
        sys.exit(2)
    csvp = args[0]
    if not (csvp.lower().endswith('.csv') or csvp.lower().endswith('.csv.gz') or csvp.lower().endswith('.zip')):
        print(f'Skipping non-CSV file: {csvp}')
        sys.exit(0)
    outd = args[1] if len(args) > 1 else None
    tbl = args[2] if len(args) > 2 else None
    if wafermap_only:
        generate_all_ibin_wafer_map(csvp, out_dir=outd, gui=gui, interactive=interactive, bindef_path=bindef, tbl_path=tbl)
    else:
        generate_heatmaps(csvp, out_dir=outd, tbl_path=tbl, gui=gui, html_only=html_only, interactive=interactive)


if __name__ == '__main__':
    main()
_heatmap_main = main  # stable alias for yield_pipeline dispatch


# ════════════════════════════════════════════════════════════════
# (formerly generate_bin_wafer_heatmaps.py)
# ════════════════════════════════════════════════════════════════
import argparse
import os
import sys
import math
import textwrap
from collections import defaultdict

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
try:
    import seaborn as sns
except ImportError:
    sns = None  # seaborn optional — only needed for generate_bin_wafer_heatmaps


def parse_args():
    p = argparse.ArgumentParser(description="Generate wafer heatmaps for bins > 4")
    p.add_argument("--data", required=True, help="Yield CSV (with Sort_X/Sort_Y/Lot/Wafer and bin column)")
    p.add_argument("--bindef", required=False, help="Parsed bindef CSV containing Expected Yield(%%) values")
    p.add_argument("--outdir", required=False, default="output", help="Output folder for generated PNGs")
    p.add_argument("--force", action="store_true", help="Generate heatmaps even if expected yield is missing")
    p.add_argument("--bincol", default="INTERFACE_BIN_119325", help="Column name for bin field")
    p.add_argument("--failbuckets", required=False, help="Path to fail_bucket_table.txt with combined-bin expected yields")
    p.add_argument("--sortx", default="Sort_X", help="Column name for wafer X coordinate")
    p.add_argument("--sorty", default="Sort_Y", help="Column name for wafer Y coordinate")
    p.add_argument("--lotcol", default="Lot", help="Column name for Lot")
    p.add_argument("--wafercol", default="Wafer", help="Column name for Wafer")
    p.add_argument("--layoutx", default=None, help="Column name for reticle LayoutX coordinate")
    p.add_argument("--layouty", default=None, help="Column name for reticle LayoutY coordinate")
    p.add_argument("--reticle", default=None, help="Column name for Reticle number")
    return p.parse_args()


def load_expected_yields(bindef_path):
    # Try to read expected yields into dict {bin:int -> expected_pct:float}
    if not bindef_path:
        return {}
    try:
        df = pd.read_csv(bindef_path)
    except Exception:
        return {}
    expected = {}
    # Look for explicit columns 'Bin' and 'Expected Yield(%)' or parse header rows
    cols = [c.strip().lower() for c in df.columns]
    if "expected yield(%)" in df.columns:
        keycol = df.columns[0]
        for _, r in df.iterrows():
            try:
                b = str(r[keycol]).strip()
                exp = float(r["Expected Yield(%)"])
            except Exception:
                continue
            # bins may contain slashes; map each numeric token
            for tok in [t for t in b.replace("+", "/").split("/") if t]:
                try:
                    expected[int(tok)] = exp
                except Exception:
                    pass
    else:
        # fallback: attempt to find any numeric-like column and a third column as expected
        for col in df.columns:
            if col.lower().startswith("bin") or col.lower().startswith("fb"):
                # assume third column is expected
                try:
                    for _, r in df.iterrows():
                        b = str(r[col])
                        exp = float(r.iloc[2])
                        for tok in [t for t in b.replace("+", "/").split("/") if t]:
                            try:
                                expected[int(tok)] = exp
                            except Exception:
                                pass
                except Exception:
                    pass
                break
    return expected


def extract_numeric_bins(cell):
    # Extract integer tokens from a cell like '1/2/3' or '31/88/91'
    if pd.isna(cell):
        return []
    s = str(cell)
    tokens = []
    for part in s.replace("+", "/").split("/"):
        part = part.strip()
        if not part:
            continue
        try:
            tokens.append(int(part))
        except Exception:
            # try to pick integers inside string
            import re
            m = re.findall(r"(\d+)", part)
            for mm in m:
                tokens.append(int(mm))
    return tokens


def prepare_heatmap_matrix(df, bin_number, bincol, sortx, sorty, lotcol, wafercol, groups=None, display_bin=None):
    # For each Lot+Wafer, compute fail% for given bin (count fail rows / total rows *100)
    # We assume each row is a unit; if the yield CSV provides a percentage column, use counts
    group = df.groupby([lotcol, wafercol])
    records = []
    for (lot, wafer), sub in group:
        total = len(sub)
        # count rows where bin column contains the bin_number or any in bin_number list
        if isinstance(bin_number, (list, tuple, set)):
            matches = sub[bincol].apply(lambda c: any(b in extract_numeric_bins(c) for b in bin_number))
        else:
            matches = sub[bincol].apply(lambda c: bin_number in extract_numeric_bins(c))
        fails = matches.sum()
        fail_pct = (fails / total) * 100 if total > 0 else 0.0
        # take median/first Sort_X/Y from this wafer
        sx = sub[sortx].dropna().astype(float).median() if sortx in sub.columns else np.nan
        sy = sub[sorty].dropna().astype(float).median() if sorty in sub.columns else np.nan
        # collect unique numeric bins present on this wafer (for 'bins per wafer' column)
        bins_on_wafer = set()
        for v in sub[bincol].dropna().unique():
            for tok in extract_numeric_bins(v):
                bins_on_wafer.add(tok)
        bins_list = sorted(bins_on_wafer)
        bins_str = "/".join(str(x) for x in bins_list) if bins_list else ""
        # determine fail-bucket labels for this wafer (may match multiple groups)
        fb_str = ""
        if groups:
            # If display_bin is a single numeric bin, prefer the fail-bucket label that contains that bin
            try:
                if display_bin is not None:
                    db = str(display_bin).strip()
                    db_tokens = extract_numeric_bins(db)
                    if len(db_tokens) == 1:
                        db_int = int(db_tokens[0])
                        for g in groups:
                            if db_int in g.get("bins", []):
                                fb_str = g.get("label", "")
                                break
            except Exception:
                fb_str = ""
            # fallback: populate based on bins present on wafer
            if not fb_str:
                fb_labels = []
                for g in groups:
                    if any(b in bins_on_wafer for b in g.get("bins", [])):
                        fb_labels.append(g.get("label", ""))
                fb_str = ",".join(sorted(set([l for l in fb_labels if l])))
        # BinDisplay: use provided display_bin or the numeric bin(s) on wafer
        bin_display = display_bin if display_bin is not None else bins_str
        records.append({
            "Lot": lot,
            "Wafer": wafer,
            "Fail%": fail_pct,
            "Sort_X": sx,
            "Sort_Y": sy,
            "Bins": bins_str,
            "Fails": int(fails),
            "Total": int(total),
            "FailBucket": fb_str,
            "BinDisplay": bin_display,
        })
    recdf = pd.DataFrame(records)
    # produce matrix keyed by Sort_Y (rows) and Sort_X (cols)
    # handle missing or NaN coordinates by dropping those wafers from heatmap but keep in table
    mat = None
    if not recdf.empty and recdf["Sort_X"].notna().any() and recdf["Sort_Y"].notna().any():
        # aggregate by coordinate: sum Fails and Total across wafers at same location
        piv = recdf.dropna(subset=["Sort_X", "Sort_Y"]).copy()
        agg = piv.groupby(["Sort_Y", "Sort_X"]).agg({"Fails": "sum", "Total": "sum"}).reset_index()
        # compute combined fail % per location
        agg["Fail%"] = agg.apply(lambda r: (r["Fails"] / r["Total"]) * 100.0 if r["Total"] > 0 else 0.0, axis=1)
        # pivot to matrix
        mat = agg.pivot(index="Sort_Y", columns="Sort_X", values="Fail%").sort_index(ascending=False)
    return recdf, mat


def _overlay_reticle_grid(ax, mat, reticle_info):
    """Overlay reticle boundary lines and reticle numbers on a seaborn heatmap."""
    if reticle_info is None or mat is None:
        return

    cols = list(mat.columns)   # Sort_X values (ascending)
    rows = list(mat.index)     # Sort_Y values (descending as displayed)

    # Build lookup: (Sort_X, Sort_Y) -> (LayoutX, LayoutY, Reticle)
    lookup = {}
    for _, r in reticle_info.iterrows():
        sx = r["Sort_X"]
        sy = r["Sort_Y"]
        lx = r.get("LayoutX")
        ly = r.get("LayoutY")
        ret = r.get("Reticle") if "Reticle" in reticle_info.columns else None
        lookup[(sx, sy)] = (lx, ly, ret)

    # Draw vertical line segments where LayoutX changes between adjacent columns
    for j in range(len(cols) - 1):
        for i in range(len(rows)):
            key_left = (cols[j], rows[i])
            key_right = (cols[j + 1], rows[i])
            lx_left = lookup.get(key_left, (None,))[0]
            lx_right = lookup.get(key_right, (None,))[0]
            if lx_left is not None and lx_right is not None and lx_left != lx_right:
                ax.plot([j + 1, j + 1], [i, i + 1], color="blue", linewidth=2, alpha=0.8)

    # Draw horizontal line segments where LayoutY changes between adjacent rows
    for i in range(len(rows) - 1):
        for j in range(len(cols)):
            key_top = (cols[j], rows[i])
            key_bottom = (cols[j], rows[i + 1])
            ly_top = lookup.get(key_top, (None, None))[1]
            ly_bottom = lookup.get(key_bottom, (None, None))[1]
            if ly_top is not None and ly_bottom is not None and ly_top != ly_bottom:
                ax.plot([j, j + 1], [i + 1, i + 1], color="blue", linewidth=2, alpha=0.8)

    # Annotate each cell with its reticle number
    for i, sy in enumerate(rows):
        for j, sx in enumerate(cols):
            info = lookup.get((sx, sy))
            if info and info[2] is not None:
                try:
                    reticle_num = int(info[2])
                    ax.text(j + 0.5, i + 0.5, str(reticle_num),
                            ha="center", va="center", fontsize=5,
                            color="black", fontweight="bold", alpha=0.7)
                except (ValueError, TypeError):
                    pass


def _overlay_reticle_grid_round(ax, mat, reticle_info, die_dx, die_dy, gap, cx, cy):
    """Overlay reticle boundary lines and numbers on the round wafer map."""
    if reticle_info is None or mat is None:
        return

    cols = sorted(mat.columns.values.astype(float))
    rows = sorted(mat.index.values.astype(float))

    # Build lookup: (Sort_X, Sort_Y) -> (LayoutX, LayoutY, Reticle)
    lookup = {}
    for _, r in reticle_info.iterrows():
        sx = r["Sort_X"]
        sy = r["Sort_Y"]
        lx = r.get("LayoutX")
        ly = r.get("LayoutY")
        ret = r.get("Reticle") if "Reticle" in reticle_info.columns else None
        lookup[(sx, sy)] = (lx, ly, ret)

    # Draw vertical boundary lines where LayoutX changes between adjacent columns
    for j in range(len(cols) - 1):
        for i in range(len(rows)):
            key_left = (cols[j], rows[i])
            key_right = (cols[j + 1], rows[i])
            lx_left = lookup.get(key_left, (None,))[0]
            lx_right = lookup.get(key_right, (None,))[0]
            if lx_left is not None and lx_right is not None and lx_left != lx_right:
                bx = ((cols[j] + cols[j + 1]) / 2 - cx) * die_dx
                by = (rows[i] - cy) * die_dy
                ax.plot([bx, bx], [by - die_dy * gap / 2, by + die_dy * gap / 2],
                        color="blue", linewidth=1.5, alpha=0.8)

    # Draw horizontal boundary lines where LayoutY changes between adjacent rows
    for i in range(len(rows) - 1):
        for j in range(len(cols)):
            key_top = (cols[j], rows[i])
            key_bottom = (cols[j], rows[i + 1])
            ly_top = lookup.get(key_top, (None, None))[1]
            ly_bottom = lookup.get(key_bottom, (None, None))[1]
            if ly_top is not None and ly_bottom is not None and ly_top != ly_bottom:
                bx = (cols[j] - cx) * die_dx
                by = ((rows[i] + rows[i + 1]) / 2 - cy) * die_dy
                ax.plot([bx - die_dx * gap / 2, bx + die_dx * gap / 2], [by, by],
                        color="blue", linewidth=1.5, alpha=0.8)

    # Annotate each cell with its reticle number
    for sy_val in rows:
        for sx_val in cols:
            info = lookup.get((sx_val, sy_val))
            if info and info[2] is not None:
                try:
                    reticle_num = int(info[2])
                    ax.text((sx_val - cx) * die_dx, (sy_val - cy) * die_dy,
                            str(reticle_num),
                            ha="center", va="center", fontsize=5,
                            color="black", fontweight="bold", alpha=0.7)
                except (ValueError, TypeError):
                    pass


def render_heatmap_and_table(outpath, csv_stem, bin_number, recdf, mat, expected_val=None, display_bin=None, reticle_info=None):
    sns.set(style="whitegrid")
    # use a wider page to accommodate the table columns
    fig = plt.figure(figsize=(14, 10))
    gs = fig.add_gridspec(3, 1, height_ratios=(2, 3, 2))

    # Title
    ax_title = fig.add_subplot(gs[0, 0])
    ax_title.axis("off")
    # allow bin_number to be either numeric or a label (group)
    title_text = f"{csv_stem}  {bin_number} Wafer Fallout"
    ax_title.text(0.5, 0.5, title_text, ha="center", va="center", fontsize=14, fontweight="bold")

    # Heatmap — round wafer map using individual die rectangles
    # (follows wafer_map_simple.py logic exactly)
    ax_hm = fig.add_subplot(gs[1, 0])
    if mat is not None:
        _cols_v = mat.columns.values.astype(float)   # Sort_X values
        _rows_v = mat.index.values.astype(float)     # Sort_Y values

        # Center coordinates at (0,0) — same as wafer_map_simple.py
        cx = (_cols_v.min() + _cols_v.max()) / 2.0
        cy = (_rows_v.min() + _rows_v.max()) / 2.0
        _cols_c = _cols_v - cx   # centered X
        _rows_c = _rows_v - cy   # centered Y

        # Scale Y so wafer appears circular  (wafer_map_simple.py: die_dy = x_range / y_range)
        x_range = _cols_v.max() - _cols_v.min()
        y_range = _rows_v.max() - _rows_v.min()
        die_dx = 1.0
        die_dy = (x_range / y_range) if y_range > 0 else 1.0

        # Colormap: Reds for fail%
        vmax = max(1.0, np.nanmax(mat.values))
        cmap = plt.cm.Reds
        norm_cm = plt.Normalize(vmin=0, vmax=vmax)

        # Draw each die as a rectangle (wafer_map_simple.py style)
        gap = 0.9
        for sy_raw, sy_c in zip(_rows_v, _rows_c):
            for sx_raw, sx_c in zip(_cols_v, _cols_c):
                try:
                    val = mat.loc[sy_raw, sx_raw]
                except KeyError:
                    continue
                if np.isnan(val):
                    continue
                px = sx_c * die_dx
                py = sy_c * die_dy
                color = cmap(norm_cm(val))
                rect = mpatches.Rectangle(
                    (px - die_dx * gap / 2, py - die_dy * gap / 2),
                    die_dx * gap, die_dy * gap,
                    linewidth=0.3, edgecolor="gray", facecolor=color
                )
                ax_hm.add_patch(rect)

        # wafer_map_simple.py: NO circle outline — dies themselves form the round shape

        # Overlay reticle grid if available
        if reticle_info is not None:
            _overlay_reticle_grid_round(ax_hm, mat, reticle_info,
                                        die_dx, die_dy, gap, cx, cy)

        # Colorbar
        sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm_cm)
        sm.set_array([])
        cbar = plt.colorbar(sm, ax=ax_hm, fraction=0.046, pad=0.04)
        cbar.set_label("% fail", fontsize=12)

        # Axis limits: max absolute centered coord + 5%
        extent_x = (abs(_cols_c).max() * die_dx + die_dx * 0.5) * 1.025
        extent_y = (abs(_rows_c).max() * die_dy + die_dy * 0.5) * 1.025
        ax_hm.set_xlim(-extent_x, extent_x)
        ax_hm.set_ylim(-extent_y, extent_y)
        ax_hm.set_aspect("equal")
        ax_hm.set_xlabel("Sort_X", fontsize=12)
        ax_hm.set_ylabel("Sort_Y", fontsize=12)
        # Remap Y-axis ticks to original Sort_Y values (undo die_dy scaling + centering)
        y_ticks = [t for t in ax_hm.get_yticks() if -extent_y <= t <= extent_y]
        ax_hm.set_yticks(y_ticks)
        ax_hm.set_yticklabels([f"{v / die_dy + cy:.0f}" if die_dy != 0
                                else f"{v:.0f}" for v in y_ticks])
        # Remap X-axis ticks to original Sort_X values (undo centering)
        x_ticks = [t for t in ax_hm.get_xticks() if -extent_x <= t <= extent_x]
        ax_hm.set_xticks(x_ticks)
        ax_hm.set_xticklabels([f"{v + cx:.0f}" for v in x_ticks])
        ax_hm.set_xlim(-extent_x, extent_x)
        ax_hm.set_ylim(-extent_y, extent_y)
        ax_hm.axhline(0, color="black", linewidth=0.5, linestyle="--", alpha=0.3)
        ax_hm.axvline(0, color="black", linewidth=0.5, linestyle="--", alpha=0.3)
        ax_hm.grid(True, alpha=0.2)
        ax_hm.set_title("Wafer Fallout Heatmap (higher = worse)")
    else:
        ax_hm.text(0.5, 0.5, "No wafer coordinates available for heatmap", ha="center", va="center")
        ax_hm.axis("off")

    # Table
    ax_tab = fig.add_subplot(gs[2, 0])
    ax_tab.axis("off")
    # build table: Lot, Wafer, Bin (or group label), Yield (Fail%), Expected Yield, Fail Bucket
    tdf = recdf[["Lot", "Wafer", "Fail%", "Bins", "FailBucket", "BinDisplay"]].copy()
    tdf = tdf.sort_values(by="Fail%", ascending=False).reset_index(drop=True)
    # limit to top 50 rows to keep image readable
    tdf = tdf.head(50)
    # prepare cell text with numeric formatting for Fail% and expected value
    cell_rows = []
    expected_str = f"{expected_val:.2f}" if expected_val is not None else ""
    for _, r in tdf.iterrows():
        # prefer BinDisplay for the Bin column; fallback to provided display_bin or bin_number
        bin_col = r.get("BinDisplay") or display_bin or str(bin_number)
        cell_rows.append([
            str(r["Lot"]),
            str(r["Wafer"]),
            str(bin_col),
            f"{r['Fail%']:.2f}",
            expected_str,
            r.get("FailBucket", "")
        ])
    # compute column widths proportional to max text length to avoid clipping
    try:
        cols = list(zip(*(["Lot","Wafer","Bin","Yield","Expected","Fail Bucket"] ,) + tuple([tuple(r) for r in cell_rows])))
    except Exception:
        cols = None
    col_widths = None
    if cols:
        try:
            max_lens = [max(len(str(x)) for x in col) for col in cols]
            total_chars = sum(max_lens) or 1
            # base fractions
            col_widths = [max(0.08, ml / total_chars * 1.1) for ml in max_lens]
            # normalize to sum to ~0.95 to leave margins
            s = sum(col_widths) or 1.0
            col_widths = [w / s * 0.95 for w in col_widths]
        except Exception:
            col_widths = None

    tbl = ax_tab.table(cellText=cell_rows, colLabels=["Lot", "Wafer", "Bin", "Yield", "Expected", "Fail Bucket"], colWidths=col_widths, loc="center", cellLoc='left')
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    # scale table rows slightly for readability
    tbl.scale(1.2, 1.2)

    # Apply conditional coloring per user's request:
    # For standalone per-bin heatmaps (display_bin is a single numeric value):
    #  - If bin == 1: highlight row red when Yield < Expected
    #  - Otherwise (all other standalone bins): highlight row red when Yield > Expected
    # For group heatmaps (non-numeric display_bin), keep previous behavior (highlight when Yield > Expected)
    def _is_single_numeric(s):
        try:
            toks = extract_numeric_bins(str(s))
            return len(toks) == 1
        except Exception:
            return False

    single_bin = _is_single_numeric(display_bin)

    # header row bgcolor
    for col in range(len(cell_rows[0])):
        try:
            cell = tbl[(-1, col)]
            cell.set_facecolor("#cccccc")
        except Exception:
            pass

    for i, row in enumerate(cell_rows):
        try:
            yield_val = float(row[3]) if row[3] != "" else None
            exp_val = float(expected_val) if expected_val is not None else None
        except Exception:
            yield_val = None
            exp_val = None
        highlight = False
        if exp_val is not None and yield_val is not None:
            if single_bin:
                # single numeric bin behavior
                try:
                    bin_tok = extract_numeric_bins(str(display_bin))[0]
                    if int(bin_tok) == 1:
                        highlight = (yield_val < exp_val)
                    else:
                        highlight = (yield_val > exp_val)
                except Exception:
                    highlight = (yield_val > exp_val)
            else:
                # group/default behavior: highlight when yield > expected
                highlight = (yield_val > exp_val)
        face = "#ff9999" if highlight else "white"
        for col in range(len(row)):
            try:
                cell = tbl[(i, col)]
                cell.set_facecolor(face)
            except Exception:
                pass

    plt.tight_layout()
    fig.savefig(outpath, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main():
    args = parse_args()
    data_path = args.data
    bindef_path = args.bindef
    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)

    try:
        df = pd.read_csv(data_path, dtype=object)
    except Exception as e:
        print(f"Failed to read data CSV: {e}", file=sys.stderr)
        sys.exit(2)

    expected = load_expected_yields(bindef_path)
    csv_stem = os.path.splitext(os.path.basename(data_path))[0]

    # Auto-detect IBIN column if the specified one is missing
    if args.bincol not in df.columns:
        alt = next((c for c in df.columns if 'INTERFACE_BIN' in c.upper() and 'TOTAL' not in c.upper()), None)
        if alt:
            print(f"Column '{args.bincol}' not found in CSV; auto-detected '{alt}'")
            args.bincol = alt
        else:
            print(f"Error: column '{args.bincol}' not found in CSV.\n"
                  f"Available columns: {list(df.columns)}", file=sys.stderr)
            sys.exit(2)

    # collect all numeric bins present in dataset
    bins_present = set()
    for v in df[args.bincol].dropna().unique():
        for tok in extract_numeric_bins(v):
            bins_present.add(tok)

    print(f"Found {len(bins_present)} numeric bins in data: {sorted(bins_present)}")
    print(f"Loaded expected yields for {len(expected)} bins from bindef")

    # Resolve Lot/Wafer column names if defaults not present
    lot_candidates = [args.lotcol, "SORT_LOT", "Lot_119325", "LOT", "LOT_119325"]
    lotcol_name = next((c for c in lot_candidates if c in df.columns), None)
    if lotcol_name is None:
        lotcol_name = next((c for c in df.columns if "lot" in c.lower()), args.lotcol)

    wafer_candidates = [args.wafercol, "SORT_WAFER", "Wafer_119325", "WAFER", "WAFER_119325"]
    wafercol_name = next((c for c in wafer_candidates if c in df.columns), None)
    if wafercol_name is None:
        wafercol_name = next((c for c in df.columns if "wafer" in c.lower()), args.wafercol)

    print(f"Using Lot column: {lotcol_name}; Wafer column: {wafercol_name}")

    # Resolve Sort_X/Sort_Y column names robustly (many CSVs use uppercase or different names)
    sortx_candidates = [args.sortx, "SORT_X", "SortX", "sort_x", "X"]
    sortx_name = next((c for c in sortx_candidates if c in df.columns), None)
    if sortx_name is None:
        sortx_name = next((c for c in df.columns if "sort_x" in c.lower() or c.lower() == 'x'), args.sortx)

    sorty_candidates = [args.sorty, "SORT_Y", "SortY", "sort_y", "Y"]
    sorty_name = next((c for c in sorty_candidates if c in df.columns), None)
    if sorty_name is None:
        sorty_name = next((c for c in df.columns if "sort_y" in c.lower() or c.lower() == 'y'), args.sorty)

    print(f"Using Sort_X column: {sortx_name}; Sort_Y column: {sorty_name}")

    # Detect LayoutX / LayoutY / Reticle columns for reticle grid overlay
    layoutx_candidates = [args.layoutx] if args.layoutx else []
    layoutx_candidates += ["LayoutX", "layoutX", "LAYOUTX", "layout_x", "Layout_X", "LAYOUT_X"]
    layoutx_col = next((c for c in layoutx_candidates if c and c in df.columns), None)

    layouty_candidates = [args.layouty] if args.layouty else []
    layouty_candidates += ["LayoutY", "layoutY", "LAYOUTY", "layout_y", "Layout_Y", "LAYOUT_Y"]
    layouty_col = next((c for c in layouty_candidates if c and c in df.columns), None)

    reticle_candidates = [args.reticle] if args.reticle else []
    reticle_candidates += ["Reticle", "reticle", "RETICLE", "Reticle_Number", "ReticleNumber"]
    reticle_col = next((c for c in reticle_candidates if c and c in df.columns), None)

    reticle_info = None
    if layoutx_col and layouty_col:
        ri_cols = [sortx_name, sorty_name, layoutx_col, layouty_col]
        if reticle_col:
            ri_cols.append(reticle_col)
        reticle_info = df[ri_cols].drop_duplicates(subset=[sortx_name, sorty_name]).copy()
        rename_map = {sortx_name: "Sort_X", sorty_name: "Sort_Y",
                      layoutx_col: "LayoutX", layouty_col: "LayoutY"}
        if reticle_col:
            rename_map[reticle_col] = "Reticle"
        reticle_info = reticle_info.rename(columns=rename_map)
        print(f"Reticle overlay enabled: LayoutX={layoutx_col}, LayoutY={layouty_col}" +
              (f", Reticle={reticle_col}" if reticle_col else ""))
    else:
        print("Reticle overlay: LayoutX/LayoutY columns not found, skipping overlay")

    # parse fail-bucket groups if provided
    groups = []
    if args.failbuckets:
        try:
            with open(args.failbuckets, "r", encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln or ln.startswith("#"):
                        continue
                    # expect pipe-separated lines: | bins | label | expected |
                    if "|" in ln:
                        parts = [p.strip() for p in ln.split("|") if p.strip()]
                        if len(parts) >= 3:
                            bins_str = parts[0]
                            label = parts[1]
                            try:
                                expected_val = float(parts[2])
                            except Exception:
                                expected_val = None
                            # parse bin tokens
                            bin_tokens = []
                            for tok in bins_str.replace("+", "/").split("/"):
                                tok = tok.strip()
                                if not tok:
                                    continue
                                try:
                                    bin_tokens.append(int(tok))
                                except Exception:
                                    # maybe token contains non-digit; extract digits
                                    import re
                                    for m in re.findall(r"(\d+)", tok):
                                        try:
                                            bin_tokens.append(int(m))
                                        except Exception:
                                            pass
                            if bin_tokens:
                                groups.append({"label": label, "bins": sorted(set(bin_tokens)), "expected": expected_val, "bins_str": bins_str})
        except Exception:
            print(f"Failed to read fail-buckets file: {args.failbuckets}")

    if groups:
        print(f"Loaded {len(groups)} fail-bucket groups from {args.failbuckets}")

    # Build map of expected yields for single-bin groups from fail-buckets (bin -> expected)
    group_expected_map = {}
    for g in groups:
        try:
            if g.get("expected") is not None and isinstance(g.get("bins"), (list, tuple)) and len(g.get("bins")) == 1:
                group_expected_map[int(g["bins"][0])] = float(g.get("expected"))
        except Exception:
            continue

    # for each bin > 4, compute aggregated yield across entire dataset
    for b in sorted(bins_present):
        if b <= 4:
            continue
        # overall yield% for bin = (count where bin present) / total rows *100
        total = len(df)
        if total == 0:
            continue
        present_count = df[args.bincol].apply(lambda c: b in extract_numeric_bins(c)).sum()
        yield_pct = (present_count / total) * 100.0
        exp = expected.get(b, None)
        # fallback to single-bin expected value from fail-bucket groups
        if exp is None:
            exp = group_expected_map.get(int(b), None)
        # create image if expected exists and yield_pct > expected
        if exp is not None and yield_pct > exp:
            recdf, mat = prepare_heatmap_matrix(df, b, args.bincol, sortx_name, sorty_name, lotcol_name, wafercol_name, groups=groups, display_bin=str(b))
            outpath = os.path.join(outdir, f"{csv_stem}_Bin{b}_WaferHeatmap.png")
            render_heatmap_and_table(outpath, csv_stem, b, recdf, mat, expected_val=exp, display_bin=str(b), reticle_info=reticle_info)
            print(f"Wrote {outpath}")
        else:
            # If expected missing, optionally force-generation based on flag
            if exp is None:
                if args.force:
                    recdf, mat = prepare_heatmap_matrix(df, b, args.bincol, sortx_name, sorty_name, lotcol_name, wafercol_name, groups=groups, display_bin=str(b))
                    outpath = os.path.join(outdir, f"{csv_stem}_Bin{b}_WaferHeatmap.png")
                    render_heatmap_and_table(outpath, csv_stem, b, recdf, mat, expected_val=None, display_bin=str(b), reticle_info=reticle_info)
                    print(f"Wrote (forced) {outpath} (yield {yield_pct:.3f}%)")
                else:
                    print(f"Skipping Bin {b}: no expected yield found; overall yield {yield_pct:.3f}%")
            else:
                print(f"Skipping Bin {b}: yield {yield_pct:.3f}% <= expected {exp}")

    # Now evaluate fail-bucket groups (combined bins)
    for g in groups:
        bins_list = g["bins"]
        total = len(df)
        if total == 0:
            continue
        # Skip groups that contain any bin <= 4 per user rule
        if any(int(b) <= 4 for b in bins_list):
            print(f"Skipping group '{g.get('label')}' because it contains bin(s) <= 4: {bins_list}")
            continue
        present_count = df[args.bincol].apply(lambda c: any(b in extract_numeric_bins(c) for b in bins_list)).sum()
        yield_pct = (present_count / total) * 100.0
        exp = g.get("expected", None)
        label_safe = "_".join([t for t in g.get("label", "group").replace("/","_").replace(" ","_").replace("(","").replace(")","").split()])
        outpath = os.path.join(outdir, f"{csv_stem}_{label_safe}_GroupHeatmap.png")
        if exp is not None and yield_pct > exp:
            recdf, mat = prepare_heatmap_matrix(df, bins_list, args.bincol, sortx_name, sorty_name, lotcol_name, wafercol_name, groups=groups, display_bin=g.get('bins_str'))
            render_heatmap_and_table(outpath, csv_stem, label_safe, recdf, mat, expected_val=exp, reticle_info=reticle_info)
            print(f"Wrote group heatmap {outpath} (yield {yield_pct:.3f}% > expected {exp})")
        else:
            print(f"Skipping group '{g.get('label')}' (yield {yield_pct:.3f}% <= expected {exp})")


if __name__ == "__main__":
    main()


# ════════════════════════════════════════════════════════════════
# (formerly generate_plots_from_csv.py)
# ════════════════════════════════════════════════════════════════
import sys
import io
import base64
import json
import re
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def _wm_inject(html: str) -> str:
    _wm = (
        '<div id="_wm_div" style="position:fixed;top:8px;right:12px;font-size:10px;'
        'font-weight:600;pointer-events:none;z-index:99999;'
        'font-family:Arial,sans-serif;user-select:none;letter-spacing:0.04em;'
        'padding:2px 6px;border-radius:3px;background:transparent;">'
        'Pant, Sujit N \u2014 GEMS FTE</div>'
        '<script>(function(){'
        'function _wm_color(){'
        'var d=document.getElementById("_wm_div");if(!d)return;'
        'var bg=window.getComputedStyle(document.body).backgroundColor;'
        'var m=bg.match(/\\d+/g);'
        'if(m&&m.length>=3){'
        'var r=+m[0],g=+m[1],b=+m[2];'
        'var lum=0.299*r+0.587*g+0.114*b;'
        'd.style.color=lum<128?"rgba(255,255,255,0.9)":"rgba(20,20,20,0.75)";'
        '}else{d.style.color="rgba(255,255,255,0.9)";}'
        '}'
        'if(document.readyState==="loading")'
        '{document.addEventListener("DOMContentLoaded",_wm_color);}'
        'else{_wm_color();}'
        '})();</script>'
    )
    import re as _re_wm
    if '</body>' not in html:
        return html
    html = _re_wm.sub(
        r'<div[^>]*id=["\']_wm_div["\'][^>]*>[\s\S]*?</div>\s*<script[^>]*>[\s\S]*?</script>',
        '', html)
    html = _re_wm.sub(r'<div[^>]*>[^<]*GEMS FTE[^<]*</div>', '', html)
    return html.replace('</body>', _wm + '\n</body>', 1)

try:
    from yield_data import detect_encoding, sniff_columns, read_csv_smart
    _HAS_CSV_UTILS = True
except ImportError:
    _HAS_CSV_UTILS = False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _apply_filter(df, fspec):
    """Return a boolean mask for rows matching *fspec*. Returns all-True if no fspec."""
    if not fspec:
        return pd.Series([True] * len(df), index=df.index)
    col = fspec.get('column')
    match = fspec.get('match', {})
    method = match.get('method', 'equals')
    value = str(match.get('value', ''))
    if col not in df.columns:
        return pd.Series([False] * len(df), index=df.index)
    s = df[col].fillna('').astype(str)
    _has_wc = ('*' in value or '?' in value)
    if _has_wc:
        import fnmatch
        return s.apply(lambda v: fnmatch.fnmatch(v, value))
    if method == 'starts_with':
        return s.str.startswith(value)
    if method == 'contains':
        return s.str.contains(value, regex=False)
    if method == 'equals':
        return s == value
    if method == 'regex':
        return s.str.match(value)
    return pd.Series([True] * len(df), index=df.index)


def _filter_columns(df, fspec):
    """
    Match *df column names* against the filter spec.
    Used when 'field' is not a real column — the filter is treated as a
    column-name selector rather than a row-value filter.
    Returns a list of matching column names.
    """
    if not fspec:
        return []
    match = fspec.get('match', {})
    method = match.get('method', 'equals')
    value = str(match.get('value', ''))
    cols = list(df.columns)
    _has_wc = ('*' in value or '?' in value)
    if _has_wc:
        import fnmatch
        return [c for c in cols if fnmatch.fnmatch(c, value)]
    if method == 'starts_with':
        return [c for c in cols if c.startswith(value)]
    if method == 'contains':
        return [c for c in cols if value in c]
    if method == 'equals':
        return [c for c in cols if c == value]
    if method == 'regex':
        return [c for c in cols if re.search(value, c)]
    return []


def _fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('ascii')


def _stats_table_html(series):
    """Return an HTML table of descriptive statistics for a numeric series."""
    s = pd.to_numeric(series, errors='coerce').dropna()
    if s.empty:
        return ''
    stats = {
        'Count': f'{len(s):,}',
        'Mean':  f'{s.mean():.4g}',
        'Median': f'{s.median():.4g}',
        'Std Dev': f'{s.std():.4g}',
        'Min': f'{s.min():.4g}',
        'Max': f'{s.max():.4g}',
        'P25': f'{s.quantile(0.25):.4g}',
        'P75': f'{s.quantile(0.75):.4g}',
    }
    rows = ''.join(f'<tr><td>{k}</td><td>{v}</td></tr>' for k, v in stats.items())
    return (
        '<table class="stats">'
        '<tr><th colspan="2">Statistics</th></tr>'
        f'{rows}'
        '</table>'
    )


# ---------------------------------------------------------------------------
# analysis runners
# ---------------------------------------------------------------------------

def _run_distribution(df, spec):
    """Return (fig, stats_html, title) for a distribution analysis.

    Multi-column mode: when *field* is not a real column, the filter is applied
    against column *names* to collect matching columns, and a histogram grid is
    produced — all under the single *tag* section.
    """
    tag = spec.get('tag', 'distribution')
    field = spec.get('field')
    fspec = spec.get('filter')
    agg = spec.get('aggregation', {})
    output_cfg = spec.get('output', {})

    # ----------------------------------------------------------------
    # multi-column mode: field not a real column → match column names
    # ----------------------------------------------------------------
    if field not in df.columns:
        matched_cols = _filter_columns(df, fspec)
        if not matched_cols:
            raise ValueError(
                f"Field '{field}' not found in CSV and no columns matched the filter spec."
            )
        return _run_multi_histogram(df, matched_cols, tag, agg, output_cfg)

    # ----------------------------------------------------------------
    # single-column mode (original behaviour)
    # ----------------------------------------------------------------

    mask = _apply_filter(df, fspec)
    sub = df[mask]

    # value counts
    vc = sub[field].fillna('').astype(str).value_counts().sort_index()

    # aggregation
    mode = agg.get('mode', 'count')
    base_cfg = agg.get('base', {})
    base_val = None
    if mode == 'percentage':
        btype = base_cfg.get('type', 'fixed')
        if btype == 'fixed':
            base_val = float(base_cfg.get('value', len(sub)))
        elif btype == 'column_count':
            bcol = base_cfg.get('column', field)
            if bcol in df.columns:
                base_val = float(df[bcol].notna().sum())
            else:
                base_val = float(len(sub))
        else:
            base_val = float(len(sub))

    show_abs = output_cfg.get('show_absolute', True)
    show_pct = output_cfg.get('show_percentage', False)
    pct_label = output_cfg.get('percentage_label', '%')

    labels = list(vc.index)
    counts = list(vc.values)
    pcts = [(c / base_val * 100) if base_val else 0.0 for c in counts] if mode == 'percentage' else []

    # figure
    n = max(len(labels), 1)
    fig_w = min(max(10, n * 0.55), 32)
    fig, ax = plt.subplots(figsize=(fig_w, 6))

    x = np.arange(n)
    bar_w = 0.35 if (show_abs and show_pct) else 0.6

    if show_abs and show_pct and pcts:
        bars1 = ax.bar(x - bar_w / 2, counts, bar_w, label='Count', color='#2980b9')
        ax2 = ax.twinx()
        bars2 = ax2.bar(x + bar_w / 2, pcts, bar_w, label=f'{pct_label}', color='#e67e22', alpha=0.85)
        ax2.set_ylabel(f'Percentage ({pct_label})')
        ax2.tick_params(labelsize=8)
        ax.set_ylabel('Count')
        # combined legend
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, loc='upper right', fontsize=9)
    elif show_pct and pcts:
        ax.bar(x, pcts, bar_w, color='#e67e22')
        ax.set_ylabel(f'Percentage ({pct_label})')
        for xi, v in zip(x, pcts):
            ax.text(xi, v + 0.1, f'{v:.2f}{pct_label}', ha='center', va='bottom', fontsize=7)
    else:
        ax.bar(x, counts, bar_w, color='#2980b9')
        ax.set_ylabel('Count')
        for xi, v in zip(x, counts):
            ax.text(xi, v + 0.1, str(v), ha='center', va='bottom', fontsize=7)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    ax.set_xlabel(field)
    ax.set_title(tag, fontsize=12, weight='bold')
    ax.tick_params(axis='y', labelsize=8)
    fig.tight_layout()

    # stats on the filtered field values (numeric if possible)
    stats_html = _stats_table_html(sub[field])
    # also add row count summary
    summary = (
        f'<p class="summary">Filtered rows: <b>{len(sub):,}</b> / Total rows: <b>{len(df):,}</b>'
        + (f' &nbsp;|&nbsp; Base for %: <b>{int(base_val):,}</b>' if base_val is not None else '')
        + '</p>'
    )
    return fig, summary + stats_html, tag


def _run_multi_histogram(df, cols, tag, agg, output_cfg, bins=20):
    """
    Plot one histogram per column in *cols* arranged in a 2-column grid.
    When mode='percentage', x-axis = value/base*100 (no decimals).
    Returns (fig, stats_html, tag).
    """
    n = len(cols)
    ncols = min(2, n)
    nrows = (n + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(11 * ncols, 5 * nrows), squeeze=False)

    mode = agg.get('mode', 'count')
    base_cfg = agg.get('base', {})
    pct_label = output_cfg.get('percentage_label', '%')

    base_val = None
    if mode == 'percentage':
        btype = base_cfg.get('type', 'fixed')
        if btype == 'fixed':
            base_val = float(base_cfg.get('value', len(df)))
        elif btype == 'column_count':
            bcol = base_cfg.get('column')
            base_val = float(df[bcol].notna().sum()) if bcol and bcol in df.columns else float(len(df))

    use_pct_x = (mode == 'percentage' and base_val is not None)

    stats_parts = []
    for idx, col in enumerate(cols):
        ax = axes[idx // ncols][idx % ncols]
        series_raw = pd.to_numeric(df[col], errors='coerce').dropna()
        short = col.split('_119325')[0]  # strip suffix for readability
        ax.set_title(short, fontsize=9, weight='bold')

        if series_raw.empty:
            ax.text(0.5, 0.5, 'No numeric data', ha='center', va='center', transform=ax.transAxes)
            continue

        # convert to % of base when mode=percentage
        series_plot = (series_raw / base_val * 100) if use_pct_x else series_raw

        counts, bin_edges = np.histogram(series_plot, bins=bins)
        bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])
        bar_w = (bin_edges[1] - bin_edges[0]) * 0.85

        ax.bar(bin_centers, counts, width=bar_w, color='#2980b9', edgecolor='white', alpha=0.9)
        ax.set_ylabel('Count', fontsize=8)
        if use_pct_x:
            ax.set_xlabel(pct_label, fontsize=8)
            ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'{v:.0f}'))
        ax.tick_params(axis='x', labelsize=7, rotation=20)
        ax.tick_params(axis='y', labelsize=7)

        s = series_plot
        if use_pct_x:
            stats_parts.append(
                f'<tr><td class="col-name">{short}</td>'
                f'<td>{len(s):,}</td>'
                f'<td>{s.mean():.0f}</td>'
                f'<td>{s.median():.0f}</td>'
                f'<td>{s.std():.0f}</td>'
                f'<td>{s.min():.0f}</td>'
                f'<td>{s.max():.0f}</td>'
                f'</tr>'
            )
        else:
            stats_parts.append(
                f'<tr><td class="col-name">{short}</td>'
                f'<td>{len(s):,}</td>'
                f'<td>{s.mean():.4g}</td>'
                f'<td>{s.median():.4g}</td>'
                f'<td>{s.std():.4g}</td>'
                f'<td>{s.min():.4g}</td>'
                f'<td>{s.max():.4g}</td>'
                f'</tr>'
            )

    # hide any unused subplots
    for idx in range(n, nrows * ncols):
        axes[idx // ncols][idx % ncols].set_visible(False)

    fig.suptitle(tag, fontsize=13, weight='bold', y=1.01)
    fig.tight_layout()

    unit = pct_label if use_pct_x else ''
    base_note = (f' &nbsp;|&nbsp; Base: <b>{int(base_val):,}</b>'
                 if use_pct_x else '')
    stats_html = (
        f'<p class="summary">Columns matched: <b>{n}</b>{base_note}</p>'
        + '<table class="stats">'
        + f'<tr><th>Column</th><th>Count</th><th>Mean{unit}</th><th>Median{unit}</th>'
          f'<th>Std Dev{unit}</th><th>Min{unit}</th><th>Max{unit}</th></tr>'
        + ''.join(stats_parts)
        + '</table>'
    )
    return fig, stats_html, tag


def _run_histogram(df, spec):
    """Return (fig, stats_html, title) for a histogram analysis of a numeric column."""
    tag = spec.get('tag', 'histogram')
    field = spec.get('field')
    fspec = spec.get('filter')
    agg = spec.get('aggregation', {})
    output_cfg = spec.get('output', {})
    bins = spec.get('bins', 20)

    if field not in df.columns:
        raise ValueError(f"Field '{field}' not found in CSV columns: {list(df.columns)}")

    mask = _apply_filter(df, fspec)
    sub = df[mask]

    series = pd.to_numeric(sub[field], errors='coerce').dropna()
    if series.empty:
        raise ValueError(f"No numeric data in field '{field}' after filtering.")

    mode = agg.get('mode', 'count')
    base_cfg = agg.get('base', {})
    base_val = None
    if mode == 'percentage':
        btype = base_cfg.get('type', 'fixed')
        if btype == 'fixed':
            base_val = float(base_cfg.get('value', len(sub)))
        elif btype == 'column_count':
            bcol = base_cfg.get('column', field)
            base_val = float(df[bcol].notna().sum()) if bcol in df.columns else float(len(sub))
        else:
            base_val = float(len(sub))

    pct_label = output_cfg.get('percentage_label', '%')
    use_pct_x = (mode == 'percentage' and base_val is not None)

    # convert to % of base when mode=percentage
    series_plot = (series / base_val * 100) if use_pct_x else series

    counts, bin_edges = np.histogram(series_plot, bins=bins)
    bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])
    bar_w = (bin_edges[1] - bin_edges[0]) * 0.85

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(bin_centers, counts, width=bar_w, color='#2980b9', edgecolor='white', alpha=0.9)
    ax.set_ylabel('Count')
    xlabel = field.split('_119325')[0] if '_119325' in field else field
    if use_pct_x:
        ax.set_xlabel(f'{xlabel}  (% of {int(base_val):,})')
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'{v:.0f}{pct_label}'))
    else:
        ax.set_xlabel(xlabel)
    ax.set_title(tag, fontsize=12, weight='bold')
    ax.tick_params(axis='x', labelsize=8, rotation=30)
    ax.tick_params(axis='y', labelsize=8)
    fig.tight_layout()

    s = series_plot
    unit = pct_label if use_pct_x else ''
    base_note = (f' &nbsp;|&nbsp; Base: <b>{int(base_val):,}</b>' if use_pct_x else '')
    stats_html = (
        f'<p class="summary">Rows: <b>{len(s):,}</b> / Total: <b>{len(df):,}</b>{base_note}</p>'
        + '<table class="stats">'
        + f'<tr><th colspan="2">{tag} — Statistics</th></tr>'
        + f'<tr><td>Count</td><td>{len(s):,}</td></tr>'
        + f'<tr><td>Mean</td><td>{s.mean():.0f}{unit}</td></tr>'
        + f'<tr><td>Median</td><td>{s.median():.0f}{unit}</td></tr>'
        + f'<tr><td>Std Dev</td><td>{s.std():.0f}{unit}</td></tr>'
        + f'<tr><td>Min</td><td>{s.min():.0f}{unit}</td></tr>'
        + f'<tr><td>Max</td><td>{s.max():.0f}{unit}</td></tr>'
        + f'<tr><td>P25</td><td>{s.quantile(0.25):.0f}{unit}</td></tr>'
        + f'<tr><td>P75</td><td>{s.quantile(0.75):.0f}{unit}</td></tr>'
        + '</table>'
    )
    return fig, stats_html, tag


def _run_xy(df, spec):
    """Return (fig, stats_html, title) for an xy scatter analysis."""
    tag = spec.get('tag', 'xy')
    xcol = spec.get('x')
    ycol = spec.get('y')
    fspec = spec.get('filter')

    for c in (xcol, ycol):
        if c not in df.columns:
            raise ValueError(f"Column '{c}' not found in CSV columns: {list(df.columns)}")

    mask = _apply_filter(df, fspec)
    sub = df[mask]

    xv = pd.to_numeric(sub[xcol], errors='coerce')
    yv = pd.to_numeric(sub[ycol], errors='coerce')
    valid = xv.notna() & yv.notna()
    xv, yv = xv[valid], yv[valid]

    fig, ax = plt.subplots(figsize=(9, 6))
    ax.scatter(xv, yv, s=15, alpha=0.6, color='#2980b9', edgecolors='none')
    ax.set_xlabel(xcol)
    ax.set_ylabel(ycol)
    ax.set_title(tag, fontsize=12, weight='bold')
    ax.tick_params(labelsize=8)
    fig.tight_layout()

    # correlation
    try:
        corr = xv.corr(yv)
        corr_str = f'<p class="summary">Pearson r: <b>{corr:.4f}</b> &nbsp;|&nbsp; Points: <b>{len(xv):,}</b></p>'
    except Exception:
        corr_str = ''
    stats_x = _stats_table_html(xv)
    stats_y = _stats_table_html(yv)
    stats_html = (
        corr_str
        + f'<div class="stats-row"><div><b>{xcol}</b>{stats_x}</div>'
        + f'<div><b>{ycol}</b>{stats_y}</div></div>'
    )
    return fig, stats_html, tag


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

CHART_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: Arial, sans-serif; background: #f0f4f8; color: #222; padding: 16px; }
h1 { font-size: 15px; margin-bottom: 18px; color: #1a3c5e; }
.analysis { background: #fff; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.12);
            margin-bottom: 32px; padding: 18px 20px; }
.analysis h2 { font-size: 13px; color: #2c3e50; margin-bottom: 12px; border-bottom: 1px solid #e0e0e0;
               padding-bottom: 6px; }
.chart-img { max-width: 100%; height: auto; display: block; margin: 0 auto 14px; }
.summary { font-size: 12px; color: #555; margin-bottom: 10px; }
table.stats { border-collapse: collapse; font-size: 12px; margin-top: 8px; }
table.stats th { background: #2c3e50; color: #fff; padding: 4px 10px; text-align: left; }
table.stats td { padding: 3px 10px; border-bottom: 1px solid #e8e8e8; }
table.stats tr:nth-child(even) td { background: #f7f9fc; }
.stats-row { display: flex; gap: 24px; flex-wrap: wrap; }
.stats-row > div { flex: 1; min-width: 200px; }
.error { color: #c0392b; font-size: 12px; padding: 8px; background: #fdecea;
         border-radius: 4px; margin-top: 8px; }
"""


def generate_plots(csv_path, plot_spec_path, out_dir=None):
    """
    Generate a combined HTML with all analyses defined in *plot_spec_path*.
    Returns the path to the written HTML, or None on failure.
    """
    csv_path = Path(csv_path)
    plot_spec_path = Path(plot_spec_path)

    try:
        spec = json.loads(plot_spec_path.read_text(encoding='utf-8-sig'))
    except Exception as e:
        print(f'ERROR reading plot spec: {e}', file=sys.stderr)
        return None

    if not isinstance(spec, dict) or 'analyses' not in spec:
        print(f'ERROR: plot spec must be a JSON object with an "analyses" array.', file=sys.stderr)
        return None

    analyses = spec.get('analyses', [])
    if not analyses:
        print('No analyses defined in plot spec.', file=sys.stderr)
        return None

    # ── Load CSV with only the columns required by all analyses ───────────────
    if _HAS_CSV_UTILS:
        _enc = detect_encoding(csv_path)
        _all_cols = sniff_columns(csv_path, encoding=_enc)

        # Helper that mirrors _filter_columns but works on a plain list
        def _match_cols_from_list(cols, fspec):
            if not fspec:
                return []
            match = fspec.get('match', {})
            method = match.get('method', 'equals')
            value = str(match.get('value', ''))
            _has_wc = ('*' in value or '?' in value)
            if _has_wc:
                import fnmatch
                return [c for c in cols if fnmatch.fnmatch(c, value)]
            if method == 'starts_with':
                return [c for c in cols if c.startswith(value)]
            if method == 'contains':
                return [c for c in cols if value in c]
            if method == 'equals':
                return [c for c in cols if c == value]
            if method == 'regex':
                return [c for c in cols if re.search(value, c)]
            return []

        _needed: set = set()
        for _a in analyses:
            _atype = _a.get('type', '')
            _field = _a.get('field')
            _fspec = _a.get('filter')
            _agg = _a.get('aggregation', {})

            # field column (single-column modes)
            if _field and _field in _all_cols:
                _needed.add(_field)
            elif _field and _field not in _all_cols:
                # multi-column distribution: match column names
                _needed.update(_match_cols_from_list(_all_cols, _fspec))

            # filter column (row filter)
            if _fspec:
                _fc = _fspec.get('column')
                if _fc and _fc in _all_cols:
                    _needed.add(_fc)

            # aggregation base column
            _base = _agg.get('base', {})
            if _base.get('type') == 'column_count':
                _bc = _base.get('column', _field)
                if _bc and _bc in _all_cols:
                    _needed.add(_bc)

            # xy axes
            for _ax in (_a.get('x'), _a.get('y')):
                if _ax and _ax in _all_cols:
                    _needed.add(_ax)

        _usecols = list(_needed) if _needed else None
        try:
            df = read_csv_smart(csv_path, usecols=_usecols, encoding=_enc)
        except Exception as e:
            print(f'ERROR reading CSV: {e}', file=sys.stderr)
            return None
    else:
        try:
            df = pd.read_csv(csv_path, dtype=object)
        except Exception as e:
            print(f'ERROR reading CSV: {e}', file=sys.stderr)
            return None

    out_dir = Path(out_dir) if out_dir else csv_path.parent / 'output'
    out_dir.mkdir(parents=True, exist_ok=True)

    out_paths = {}  # {tag: path_str}

    for idx, analysis in enumerate(analyses):
        atype = analysis.get('type', '')
        tag = analysis.get('tag', f'analysis_{idx}')
        safe_tag = _safe_id(tag)
        try:
            if atype == 'distribution':
                fig, stats_html, title = _run_distribution(df, analysis)
            elif atype == 'histogram':
                fig, stats_html, title = _run_histogram(df, analysis)
            elif atype == 'xy':
                fig, stats_html, title = _run_xy(df, analysis)
            else:
                section_html = (
                    f'<div class="analysis"><h2>{tag}</h2>'
                    f'<p class="error">Unknown analysis type: <b>{atype}</b></p></div>'
                )
                print(f'ERROR: unknown type "{atype}" for tag {tag}', file=sys.stderr)
                _write_tag_html(out_dir, csv_path.stem, tag, safe_tag, section_html)
                out_paths[tag] = str(out_dir / f'{csv_path.stem}_{safe_tag}.html')
                print(f'Wrote: {tag} :: {out_paths[tag]}')
                continue

            b64 = _fig_to_b64(fig)
            plt.close(fig)
            section_html = (
                f'<div class="analysis" id="{safe_tag}">'
                f'<h2>{tag}</h2>'
                f'<img class="chart-img" src="data:image/png;base64,{b64}"/>'
                f'{stats_html}'
                f'</div>'
            )
            print(f'Plotted: {tag}')
        except Exception as e:
            section_html = (
                f'<div class="analysis"><h2>{tag}</h2>'
                f'<p class="error">Error: {e}</p></div>'
            )
            print(f'ERROR plotting {tag}: {e}', file=sys.stderr)

        _write_tag_html(out_dir, csv_path.stem, tag, safe_tag, section_html)
        out_paths[tag] = str(out_dir / f'{csv_path.stem}_{safe_tag}.html')
        print(f'Wrote: {tag} :: {out_paths[tag]}')

    return out_paths


def _safe_id(s):
    return re.sub(r'[^a-zA-Z0-9_-]', '_', s)


def _write_tag_html(out_dir, stem, tag, safe_tag, section_html):
    """Write one standalone HTML page for a single analysis tag."""
    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{tag}</title>
<style>{CHART_CSS}
.col-name {{ font-size:11px; padding:2px 8px; max-width:340px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
</style>
</head>
<body>
{section_html}
</body>
</html>"""
    out_file = out_dir / f'{stem}_{safe_tag}.html'
    out_file.write_text(_wm_inject(html), encoding='utf-8')


def main():
    if len(sys.argv) < 3:
        print('Usage: generate_plots_from_csv.py <csv_path> <plot_spec_json> [out_dir]')
        sys.exit(2)
    result = generate_plots(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    if not result:
        sys.exit(1)
    for tag, path in result.items():
        print(f'  {tag}: {path}')


if __name__ == '__main__':
    main()


# ════════════════════════════════════════════════════════════════
# (formerly generate_flowchart.py)
# ════════════════════════════════════════════════════════════════
try:
    from graphviz import Digraph
except ImportError:
    Digraph = None  # graphviz optional — only needed for generate_flowchart


def build_graph(out_path: str = 'pipeline_flow') -> None:
    g = Digraph('yield_pipeline', format='svg')
    g.attr(rankdir='LR', fontsize='12')

    g.node('AQUA', 'AQUA fetch\n(optional)')
    g.node('Parse', 'Parse BinDefinitions\n(parse_bindef_to_crystalball.py)')
    g.node('GetDD', 'Append to Dashboard\n(get_dd_update.py)')
    g.node('Files', 'Outputs:\n- yield CSV\n- bindef CSV\n- dashboard.xlsx')

    g.edge('AQUA', 'Parse', label='yield CSV')
    g.edge('Parse', 'GetDD', label='bindef CSV')
    g.edge('AQUA', 'GetDD', label='yield CSV')
    g.edge('GetDD', 'Files')

    g.attr(label='Yield -> Bindef -> Dashboard pipeline', labelloc='t')
    g.render(out_path, cleanup=True)
    print('Generated', out_path + '.svg')


if __name__ == '__main__':
    build_graph()

