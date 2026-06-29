import sys
from pathlib import Path
import re as _re
import pandas as pd


def _wm_inject(html: str) -> str:
    _wm = (
        '<div id="_wm_div" style="position:fixed;top:8px;right:12px;font-size:10px;'
        'font-weight:600;pointer-events:none;z-index:99999;'
        'font-family:Arial,sans-serif;user-select:none;letter-spacing:0.04em;'
    'padding:2px 6px;border-radius:3px;background:transparent;color:rgba(255,255,255,0.95);">'
        'Pant, Sujit N \u2014 GEMS FTE</div>'
        '<script>(function(){'
    'var _all=document.querySelectorAll("#_wm_div");'
    'for(var _i=0;_i<_all.length-1;_i++){_all[_i].remove();}'
        'function _wm_color(){'
        'var d=document.getElementById("_wm_div");if(!d)return;'
    'd.style.color="rgba(255,255,255,0.95)";'
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
    from csv_utils import detect_encoding, sniff_columns, iter_chunks, read_csv_smart, CHUNK_SIZE
    _HAS_CSV_UTILS = True
except ImportError:
    _HAS_CSV_UTILS = False


def generate(data_path, out_dir=None, tbl_path=None):
    data_csv = Path(data_path)

    # ── Column detection (header only) ──────────────────────────────────────
    if _HAS_CSV_UTILS:
        encoding = detect_encoding(data_csv)
        all_cols = sniff_columns(data_csv, encoding=encoding)
    else:
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

    # ── Accumulate bin counts (vectorized) ────────────────────────────────
    bin_counts: dict = {}
    total = 0

    if _HAS_CSV_UTILS:
        _bc_df = read_csv_smart(data_csv, usecols=[col], encoding=encoding)
    else:
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
    wafer_col = next((c for c in all_cols
                      if 'sort_wafer' in c.lower()
                      or ('wafer' in c.lower() and 'sort_wafer' not in c.lower())), None)
    mat_col   = next((c for c in all_cols if 'material' in c.lower()), None)
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
    if tbl_path:
        try:
            import json as _json_pc
            _pc_txt = Path(tbl_path).read_text(encoding='utf-8', errors='ignore')
            _pc_data = _json_pc.loads(_pc_txt)
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
    _ic_load_cols = list(dict.fromkeys(_ic_group_cols + ([mat_col] if mat_col else []) + [col] + ([fb_col] if fb_col else []) + hw_fields + _upm_extra))
    _ic_rows = []
    try:
        if _HAS_CSV_UTILS:
            _df_ic = read_csv_smart(data_csv, usecols=_ic_load_cols, encoding=encoding)
        else:
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
                        _dies_ic.append(_entry)
                _ic_rows.append({
                    'program':  str(_kd_ic.get(prog_col, '')),
                    'lot':      str(_kd_ic.get(lot_col, '')),
                    'wafer':    str(_kd_ic.get(wafer_col, '')),
                    'material': _mat_ic,
                    'binCounts': _bc_ic,
                    'ibToFb':   _ib_fb_ic,
                    'ibToHw':   _ib_hw_ic,
                    'total':    int(len(_gdf_ic)),
                    'dies':     _dies_ic,
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
            _ic_rows = [{'program': prog_val, 'lot': lot_val, 'wafer': 'all',
                         'material': '', 'binCounts': _bc_all, 'ibToFb': _ib_fb_all, 'total': int(total)}]
    except Exception as _e_ic:
        _ic_rows = [{'program': prog_val, 'lot': lot_val, 'wafer': 'all',
                     'material': '',
                     'binCounts': {k: v for k, v in bin_counts.items() if str(k).isdigit()},
                     'ibToFb': {}, 'ibToHw': {}, 'total': int(total)}]


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
    # ── Reticle mapping (auto-discovered from collateral/reticle/) ─────────────
    _ret_map = {}         # "x,y" -> [rx, ry, shotIdx]
    _ret_shots = []       # [[xMin, yMin, xMax, yMax], ...] per shot in wafer die coords
    _ret_site_totals = {} # "rx,ry" -> count of unique shots containing that site
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
        _ret_candidates = [p for p in _ret_candidates if Path(p).is_file()
                           and 'reticle' in Path(p).name.lower()]
        if _ret_candidates:
            _ret_csv_path = Path(_ret_candidates[0])
            _ret_df = pd.read_csv(_ret_csv_path)
            _rc = {c.lower().replace(' ', '').replace('_', ''): c for c in _ret_df.columns}
            _rdx = _rc.get('diex')
            _rdy = _rc.get('diey')
            _rrx = _rc.get('reticlediex')
            _rry = _rc.get('reticlediey')
            _rrs = _rc.get('reticleshot')
            if _rdx and _rdy and _rrx and _rry and _rrs:
                _ret_df2 = _ret_df[[_rdx, _rdy, _rrx, _rry, _rrs]].dropna().copy()
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
                print(f'Reticle map loaded: {_ret_csv_path.name} ({len(_ret_shots)} shots, {len(_ret_map)} dies, offsets={_ret_offset_x},{_ret_offset_y})')
    except Exception as _e_ret:
        print(f'Reticle map not loaded: {_e_ret}')

    _ic_fail_bins = [b for b in _ic_all_bins if b.isdigit() and int(b) > 4]
    _ic_data_json = _json_ic.dumps({
        'bins': _ic_all_bins, 'total': int(total), 'rows': _ic_rows,
        'binColors': _ic_bin_colors, 'binBuckets': _ic_bin_buckets,
        'legendGroups': dict(_ic_legend_groups), 'yieldDefs': _ic_yield_defs,
        'failBins': _ic_fail_bins, 'hasMaterial': bool(mat_col),
        'hasFunctionalBin': bool(fb_col),
        'fbDescriptions': _fb_descriptions,
        'upmCols': [{'key': d['key'], 'label': d['label'], 'divisor': d.get('div')} for d in _upm_col_defs],
        'hasUpm': bool(_upm_col_defs and _x_col and _y_col),
        'upmStart': 5,
        'hasReticle': bool(_ret_map),
        'retMap': _ret_map,
        'retShots': _ret_shots,
        'retSiteTotals': _ret_site_totals,
    }, ensure_ascii=False)

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
        '.ftw{overflow-x:auto;max-height:calc(100vh - 320px);overflow-y:auto}\n'
        '.ftbl{border-collapse:collapse;font-size:13px;white-space:nowrap;width:100%}\n'
        '.ftbl th{background:#2c3e50;color:#ecf0f1;padding:6px 12px;text-align:left;'
        'position:sticky;top:0;z-index:1}\n'
        '.ftbl td{padding:5px 12px;border-bottom:1px solid #eee}\n'
        '.flt-btn{background:none;border:none;color:#aed6f1;cursor:pointer;font-size:11px;padding:0 0 0 4px;vertical-align:middle;opacity:.85}\n'
        '.flt-btn:hover{opacity:1;color:#fff}\n'
        '.flt-btn.active{color:#f1c40f!important;opacity:1}\n'
        '.dd-panel{position:fixed;background:#fff;border:1px solid #aaa;border-radius:4px;box-shadow:0 4px 16px rgba(0,0,0,.18);z-index:25000;min-width:180px;max-width:280px;font-family:Arial,sans-serif;font-size:12px;color:#2c3e50}\n'
        '.dd-panel .dd-search{width:100%;box-sizing:border-box;padding:5px 8px;border:none;border-bottom:1px solid #ddd;font-size:12px;outline:none}\n'
        '.dd-panel .dd-acts{display:flex;gap:4px;padding:4px 6px;border-bottom:1px solid #eee}\n'
        '.dd-panel .dd-acts button{flex:1;padding:2px 6px;font-size:11px;cursor:pointer;border:1px solid #bdc3c7;background:#ecf0f1;border-radius:3px}\n'
        '.dd-panel .dd-list{max-height:200px;overflow-y:auto;padding:4px 0}\n'
        '.dd-panel .dd-item{display:flex;align-items:center;gap:6px;padding:3px 10px;cursor:pointer}\n'
        '.dd-panel .dd-item:hover{background:#eaf0fb}\n'
        '.dd-panel .dd-item input{margin:0;cursor:pointer}\n'
        '.dd-panel .dd-footer{padding:4px 8px;border-top:1px solid #eee;text-align:right}\n'
        '.dd-panel .dd-footer button{padding:3px 12px;font-size:11px;cursor:pointer;background:#2c3e50;color:#fff;border:none;border-radius:3px}\n'
        '.fr{cursor:pointer;transition:background .1s}\n'
        '.fr:hover td{background:#f0f4ff}\n'
        '.frs td{background:#d6eaff!important;font-weight:bold}\n'
        '.num{text-align:right}\n'
        '.ys{background:#fff;border-radius:6px;padding:10px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex:1 1 53%;min-width:0;overflow-y:auto;overflow-x:hidden;max-height:calc(100vh - 320px)}\n'
        '.yt{font-size:15px;font-weight:bold;color:#2c3e50;margin-bottom:8px}\n'
        '.ytbl{border-collapse:collapse;font-size:13px;width:100%;table-layout:fixed}\n'
        '.ytbl th{background:#2c3e50;color:#ecf0f1;padding:6px 12px;text-align:left;position:sticky;top:0;z-index:1}\n'
        '.ytbl td{padding:5px 12px;border-bottom:1px solid #dde;text-align:left}\n'
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
        '.fb-fbfilt{margin-top:12px;padding:8px 10px;background:#f4f7fb;border-radius:6px;border:1px solid #d0d8e8}\n'
        '.fb-ffhdr{font-size:13px;font-weight:bold;color:#2c3e50;margin-bottom:6px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}\n'
        '.fb-cblist{display:flex;flex-wrap:wrap;gap:5px;margin-top:4px}\n'
        '.fb-cbitem{display:flex;align-items:center;gap:3px;font-size:12px;padding:2px 7px;border-radius:3px;background:#fff;border:1px solid #cdd5e0;cursor:pointer}\n'
        '.fb-cbitem:hover{background:#eaf2ff}\n'
        '.fb-cbitem input{cursor:pointer;margin:0}\n'
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
        '.dlcp-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.45);z-index:22000;pointer-events:none}\n'
        '.dlcp-overlay.open{display:flex;align-items:flex-start;justify-content:center;padding-top:36px;pointer-events:none}\n'
        '.dlcp-box{background:#f0f2f5;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,.35);width:94vw;max-width:1340px;height:72vh;min-width:600px;min-height:340px;max-width:98vw;max-height:95vh;display:flex;flex-direction:column;pointer-events:auto;overflow:hidden;resize:both}\n'
        '.dlcp-drag{cursor:move;background:#1f618d;color:#fff;padding:8px 14px;border-radius:8px 8px 0 0;display:flex;align-items:center;justify-content:space-between;user-select:none;flex-shrink:0}\n'
        '.dlcp-body{display:flex;flex-direction:column;flex:1;padding:8px;gap:6px;min-height:0;overflow:hidden}\n'
        '.dlcp-ctrl{display:flex;align-items:center;gap:12px;flex-wrap:wrap;background:#fff;padding:7px 12px;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex-shrink:0}\n'
        '.dlcp-sumbox{background:#fff;border-radius:6px;padding:8px 14px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex-shrink:0;display:flex;gap:18px;flex-wrap:wrap;align-items:center}\n'
        '.dlcp-sum-grp{display:flex;flex-direction:column;padding:4px 14px;border-left:3px solid #dde;min-width:110px}\n'
        '.dlcp-sum-grp.pass{border-color:#2980b9}.dlcp-sum-grp.marg{border-color:#d4ac0d}.dlcp-sum-grp.fail{border-color:#c0392b}\n'
        '.dlcp-sum-lbl{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px}\n'
        '.dlcp-sum-val{font-size:17px;font-weight:bold;color:#2c3e50}.dlcp-sum-pct{font-size:11px;color:#666;margin-left:4px}\n'
        '.dlcp-inner{display:flex;gap:8px;flex:1;min-height:0}\n'
        '.dlcp-left{display:flex;flex-direction:column;gap:6px;min-width:0;flex:1.2}\n'
        '.dlcp-sec-ttl{font-size:11px;font-weight:bold;color:#5d6d7e;text-transform:uppercase;letter-spacing:.5px;flex-shrink:0}\n'
        '.dlcp-tw{overflow:auto;background:#fff;border-radius:6px;padding:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);flex:1;min-height:0}\n'
        '.dlcp-t{border-collapse:collapse;font-size:12px;white-space:nowrap;width:100%}\n'
        '.dlcp-t th{background:#2c3e50;color:#ecf0f1;padding:5px 10px;text-align:left;position:sticky;top:0;z-index:1}\n'
        '.dlcp-t td{padding:4px 10px;border-bottom:1px solid #eee}\n'
        '.dlcp-t tr:nth-child(even) td{background:#f7f9fc}.dlcp-t tr:hover td{background:#eaf3fb}\n'
        '.dlcp-t .num{text-align:right}\n'
        '.dlcp-cw{flex:1;background:#fff;border-radius:6px;padding:8px;box-shadow:0 1px 4px rgba(0,0,0,.1);display:flex;flex-direction:column;min-width:0}\n'
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

    _html_info = (
        '<div class="ib">'
        f'<span>TEST PROGRAM: <b>{_esc(prog_val)}</b></span>'
        f'<span>LOTS: <b>{_esc(lot_val)}</b></span>'
        f'<span>TOTAL WAFERS: <b>{_esc(wafer_cnt)}</b></span>'
        f'<span>TOTAL UNITS: <b>{total:,}</b></span>'
        '</div>\n'
    )

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
        '      <thead><tr>\n'
        + ('        <th>TestProgram <button class="flt-btn" id="ft-fb-0" onclick="event.stopPropagation();IC.ftDdOpen(0,this)" title="Filter">&#9660;</button></th><th>Lot <button class="flt-btn" id="ft-fb-1" onclick="event.stopPropagation();IC.ftDdOpen(1,this)" title="Filter">&#9660;</button></th><th>Wafer <button class="flt-btn" id="ft-fb-2" onclick="event.stopPropagation();IC.ftDdOpen(2,this)" title="Filter">&#9660;</button></th><th>MaterialType <button class="flt-btn" id="ft-fb-3" onclick="event.stopPropagation();IC.ftDdOpen(3,this)" title="Filter">&#9660;</button></th><th class="num">Total</th>\n' if mat_col else '        <th>TestProgram <button class="flt-btn" id="ft-fb-0" onclick="event.stopPropagation();IC.ftDdOpen(0,this)" title="Filter">&#9660;</button></th><th>Lot <button class="flt-btn" id="ft-fb-1" onclick="event.stopPropagation();IC.ftDdOpen(1,this)" title="Filter">&#9660;</button></th><th>Wafer <button class="flt-btn" id="ft-fb-2" onclick="event.stopPropagation();IC.ftDdOpen(2,this)" title="Filter">&#9660;</button></th><th class="num">Total</th>\n')
        + '      </tr>\n      </thead>\n'
        '      <tbody id="filter-tbody"></tbody>\n'
        '    </table>\n'
        '  </div>\n'
        '</div>\n'
        '</div>\n'
        '<div class="yp" id="yp-ys" style="flex:1 1 53%;min-width:0">\n'
        '<div class="yp-bar" onclick="ypTgl(\'ys\')">'
        '<span class="yp-ttl">&#9654; Yield Summary (filtered)</span>'
        '<span class="yp-btns" onclick="event.stopPropagation()">'
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
        '      <th class="num">ACTUAL (%)</th><th class="num">EXPECTED (%)</th><th class="num">DIFF (%)</th>\n'
        '    </tr></thead>\n'
        '    <tbody id="yield-tbody"></tbody>\n'
        '  </table>\n'
        '</div>\n'
        '</div>\n'
        '</div>\n'
        '</div>\n'
        +('<!-- UPM Heatmap popup -->\n'
        '<div id="upm-modal" class="upm-overlay">\n'
        '  <div class="upm-box" id="upm-box">\n'
        '    <div class="upm-drag" id="upm-drag"><b>UPM Wafer Heatmap</b>'
        '<button onclick="IC.refreshUpm()" style="background:none;border:none;color:#fff;font-size:16px;cursor:pointer;margin-right:8px" title="Refresh">&#x21bb;</button>'
        '<button onclick="IC.closeUpmModal()" style="background:none;border:none;color:#fff;font-size:20px;cursor:pointer;line-height:1">&times;</button></div>\n'
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
        '          <button id="wmd-tab-upm" class="wm-tbtn on" onclick="IC._wmdTabSel(\'upm\',IC._wmdRiVal())" style="font-size:11px">&#128200; UPM Heatmap</button>\n'
        '          <button id="wmd-tab-pat" class="wm-tbtn" onclick="IC._wmdTabSel(\'pattern\',IC._wmdRiVal())" style="font-size:11px">&#128205; Pattern</button>\n'
        '        </div>\n'
        '        <div id="wmd-upm-pane" class="wmd-sec" style="flex:1">\n'
        '          <div class="wmd-sec-ttl">UPM Heatmap <span id="wmd-upm-sel" style="font-weight:normal;text-transform:none;font-size:11px"></span></div>\n'
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
        '        <span id="dlcp-tv" style="font-weight:bold;color:#1a5276;min-width:48px;font-size:14px">92.5%</span>\n'
        '        <span id="dlcp-cs"></span>\n'
        '      </div>\n'
        '      <div class="dlcp-sumbox" id="dlcp-sumbox"></div>\n'
        '      <div class="dlcp-inner">\n'
        '        <div class="dlcp-left">\n'
        '          <div class="dlcp-sec-ttl">Per-Wafer Detail</div>\n'
        '          <div class="dlcp-tw"><table class="dlcp-t"><thead>\n'
        '            <tr><th rowspan="2">Lot</th><th rowspan="2">Wafer</th><th rowspan="2">Material</th>\n'
        '              <th class="num" rowspan="2">Total</th><th class="num" rowspan="2">Med UPM%</th>\n'
        '              <th class="num" colspan="2" style="background:#1a5276">HP (IB1/2, UPM\u2265thr)</th>\n'
        '              <th class="num" colspan="2" style="background:#7d6608">LP (IB1-4, below thr)</th>\n'
        '              <th class="num" colspan="2" style="background:#7b241c">Fail (IB&gt;4)</th></tr>\n'
        '            <tr><th class="num" style="background:#1a5276">#</th><th class="num" style="background:#1a5276">%</th>\n'
        '              <th class="num" style="background:#7d6608">#</th><th class="num" style="background:#7d6608">%</th>\n'
        '              <th class="num" style="background:#7b241c">#</th><th class="num" style="background:#7b241c">%</th></tr>\n'
        '          </thead><tbody id="dlcp-tb"></tbody></table></div>\n'
        '          <div class="dlcp-note" id="dlcp-note"></div>\n'
        '        </div>\n'
        '        <div class="dlcp-cw">\n'
        '          <div style="font-size:11px;color:#666;margin-bottom:4px;flex-shrink:0">CDF of UPM% \u2014 HP (blue) vs LP (orange) | red dashed = threshold</div>\n'
        '          <canvas id="dlcp-cv" style="display:block;width:100%;flex:1;border:1px solid #dde;border-radius:4px;min-height:180px"></canvas>\n'
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
        '      <thead><tr><th>Functional Bin</th><th>Category</th><th>Description</th><th class="num">Count</th><th class="num">% of IB</th><th class="num">Fail %</th></tr></thead>\n'
        '      <tbody id="fb-modal-tbody"></tbody>\n'
        '    </table>\n'
        '    <div class="fb-fbfilt">\n'
        '      <div class="fb-ffhdr">Filter by Functional Bin\n'
        '        <button class="cb" onclick="IC.selectAllFbs()">All</button>\n'
        '        <button class="cb" onclick="IC.clearFbs()">None</button>\n'
        '        <button class="cb" onclick="IC.showFbWaferMap()">Show Wafer Distribution &#9654;</button>\n'
        '        <button class="cb" onclick="IC.showBhHwModal()">HW Breakdown &#128295;</button>\n'
        +('        <button class="cb" onclick="IC.showUpmModal()">UPM Heatmap &#128202;</button>\n' if _upm_col_defs and _x_col and _y_col else '')
        +'      </div>\n'
        '      <div id="fb-cblist" class="fb-cblist"></div>\n'
        '    </div>\n'
        '    <div class="fb-wm-sec" id="fb-wm-sec" style="display:none">\n'
        '      <div class="fb-wm-ttl">Wafer Distribution &mdash; IB <span id="fb-wm-ib"></span> (selected FBs) &nbsp;<small style="color:#7f8c8d;font-weight:normal">click tile to jump to wafer</small></div>\n'
        '      <div id="fb-wm-grid" class="fb-wm-grid"></div>\n'
        '    </div>\n'
        '    </div>\n'
        '  </div>\n'
        '</div>\n'
    )

    # JavaScript — pure ES5-compatible, no external deps, works offline
    import json as _json
    _hw_combo_table_bh_json = _json.dumps(_hw_combo_table_bh)
    _hw_fields_bh_json = _json.dumps([str(c) for c in hw_fields]) if hw_fields else '[]'
    _html_script = (
      '<script>\nvar DATA=' + _ic_data_json + ';\n'
      'var HW_COMBO_TABLE_BH=' + _hw_combo_table_bh_json + ';\n'
      'var HW_FIELDS_BH=' + _hw_fields_bh_json + ';\n'
      + r'''var IC=(function(){
'use strict';
var AB=DATA.bins;
var sB=new Set(AB);
var _fbModalIb=null,_fbModalFbKeys=[],_fbChecked=new Set();
var _wmdFbScopeRi=null;
var _upmOpen=false,_upmMetricIdx=0;
var _dlcpOpen=false,_dlcpT=92.5,_dlcpUi=0;
var _wmOpen=false;
var _ySelIdx=-1;
var sR=new Set(DATA.rows.map(function(_,i){return i;}));
var lR=-1;
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
        if(Object.keys(ibFb).length>0){
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
    var tipExtra=isFail?'&#10;Fail%: '+failPct.toFixed(2)+'% of fail population':'';
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
var _ftDdState={};var _ftDdOpen_=null;
function ftDdOpen(col,btn){
  if(_ftDdOpen_){_ftDdClose();}
  var allVals=[];
  var seen=new Set();
  DATA.rows.forEach(function(row){
    var cols=[row.program,row.lot,row.wafer].concat(DATA.hasMaterial?[row.material||'']:[]);
    var v=String(cols[col]||'');
    if(!seen.has(v)){seen.add(v);allVals.push(v);}
  });
  allVals.sort(function(a,b){return a.localeCompare(b);});
  var allowed=_ftDdState[col];
  var checked=allowed?new Set(allowed):new Set(allVals);
  var panel=document.createElement('div');
  panel.className='dd-panel';
  panel.innerHTML='<input class="dd-search" placeholder="Search\u2026">'
    +'<div class="dd-acts"><button class="ft-sel-all">Select All</button><button class="ft-clr">Clear</button></div>'
    +'<div class="dd-list" id="ft-dd-list"></div>'
    +'<div class="dd-footer"><button class="ft-ok">OK</button></div>';
  document.body.appendChild(panel);
  var r=btn.getBoundingClientRect();
  panel.style.top=(r.bottom+2)+'px';
  panel.style.left=Math.min(r.left,window.innerWidth-200)+'px';
  _ftDdOpen_={panel:panel,col:col,btn:btn,allVals:allVals,checked:checked,
    renderList:function(vals){
      var list=document.getElementById('ft-dd-list');if(!list)return;
      var h='';vals.forEach(function(v){var c=_ftDdOpen_.checked.has(v)?' checked':'';
        h+='<label class="dd-item"><input type="checkbox"'+c+' data-val="'+v.replace(/&/g,'&amp;').replace(/"/g,'&quot;')+'">'+v.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</label>';
      });list.innerHTML=h;
      list.querySelectorAll('input').forEach(function(inp){inp.onchange=function(){_ftDdOpen_.toggle(inp,inp.dataset.val);};});
    },
    toggle:function(cb,v){if(cb.checked)_ftDdOpen_.checked.add(v);else _ftDdOpen_.checked.delete(v);},
    selAll:function(){_ftDdOpen_.allVals.forEach(function(v){_ftDdOpen_.checked.add(v);});_ftDdOpen_.renderList(_ftDdOpen_.allVals);},
    clearAll:function(){_ftDdOpen_.checked.clear();_ftDdOpen_.renderList(_ftDdOpen_.allVals);},
    apply:function(){
      var c=_ftDdOpen_.col,chk=_ftDdOpen_.checked,all=_ftDdOpen_.allVals;
      _ftDdState[c]=(chk.size===all.length)?null:new Set(chk);
      var b=document.getElementById('ft-fb-'+c);if(b)b.classList.toggle('active',!!_ftDdState[c]);
      _ftDdClose();rFilter();
    }
  };
  _ftDdOpen_.renderList(allVals);
  panel.querySelector('.dd-search').oninput=function(){var q=(this.value||'').toLowerCase();var fl=q?_ftDdOpen_.allVals.filter(function(v){return v.toLowerCase().indexOf(q)>=0;}):_ftDdOpen_.allVals;_ftDdOpen_.renderList(fl);};
  panel.querySelector('.ft-sel-all').onclick=function(){_ftDdOpen_.selAll();};
  panel.querySelector('.ft-clr').onclick=function(){_ftDdOpen_.clearAll();};
  panel.querySelector('.ft-ok').onclick=function(){_ftDdOpen_.apply();};
  setTimeout(function(){document.addEventListener('mousedown',_ftDdOutside);},0);
}
function _ftDdClose(){
  if(!_ftDdOpen_)return;
  document.removeEventListener('mousedown',_ftDdOutside);
  if(_ftDdOpen_.panel.parentNode)_ftDdOpen_.panel.parentNode.removeChild(_ftDdOpen_.panel);
  _ftDdOpen_=null;
}
function _ftDdOutside(e){if(_ftDdOpen_&&!_ftDdOpen_.panel.contains(e.target)){_ftDdOpen_.apply();}}
function rFilter(){
  var tbody=document.getElementById('filter-tbody');
  var html='';
  DATA.rows.forEach(function(row,i){
    var cols=[row.program,row.lot,row.wafer].concat(DATA.hasMaterial?[row.material||'']:[]);
    var show=Object.keys(_ftDdState).every(function(ci){
      var s=_ftDdState[ci];return !s||s.has(String(cols[parseInt(ci)]||''));
    });
    if(!show)return;
    var sel=sR.has(i);
    html+='<tr class="fr'+(sel?' frs':'')+'" onclick="IC.toggleRow('+i+',event)">';
    html+='<td>'+esc(row.program)+'</td><td>'+esc(row.lot)+'</td><td>'+esc(row.wafer)+'</td>';    if(DATA.hasMaterial)html+='<td>'+esc(row.material||'')+'</td>';    html+='<td class="num">'+row.total.toLocaleString()+'</td></tr>';
  });
  tbody.innerHTML=html;
  document.getElementById('row-sel-info').textContent=
    sR.size<DATA.rows.length?'('+sR.size+'/'+DATA.rows.length+' selected)':'';
  if(_dlcpOpen){_dlcpRender();}
  if(_wmOpen){_wmRender();}
}
function rYield(){
  var fc=gFC(),cn=fc.counts,tot=fc.total;
  var tbody=document.getElementById('yield-tbody');
  var ysInfo=document.getElementById('ys-info');
  if(ysInfo)ysInfo.textContent='Total Wafers\u202f=\u202f'+sR.size+'\u2002\u2014\u2002n\u202f=\u202f'+tot.toLocaleString()+' dies';
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
    html+='<tr class="'+rowCls+'"'+rowClick+'>';
    html+='<td>'+esc(def.bins)+'</td><td>'+esc(def.bucket)+'</td>';
    html+='<td'+(actualCls||'')+'>'+pct.toFixed(1)+'% <span style="color:#888;font-size:10px">(n\u202f=\u202f'+cnt.toLocaleString()+')</span></td>';
    html+='<td>'+(def.expected?def.expected+'%':'')+'</td>';
    html+='<td class="'+diffCls+'">'+(diff===null?'\u2014':(diff>0?'+':'')+diff.toFixed(1)+'%')+'</td></tr>';
  });
  tbody.innerHTML=html;
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
  rChart();rLegend();rFilter();rYield();
  if(window._updatePareto)_updatePareto();
  document.getElementById('sel-info').textContent=
    sR.size<DATA.rows.length?'('+sR.size+'/'+DATA.rows.length+' wafers)':'';
  /* Cascade to open panels */
  if(_fbFilterIb!==null){refreshFb();}
  if(_bhHwOpen){_renderHwSection();}
  if(_upmOpen){_renderUpmMaps();}
  if(_dlcpOpen){_dlcpRender();}
  if(_wmOpen){_wmRender();}
  _wmRenderInline();
}
function clickBar(bin){
  if(DATA.hasFunctionalBin){showFbModal(bin);}
  else{clickLegend(bin,null);}
}
/* Module-level state for FB modal re-render */
var _fbModalTotals={},_fbModalIbTotal=0,_fbModalAllTot=0;
var _fbFilterIb=null; /* IB currently filtered in histogram by FB/HW selection */
function showFbModal(ib){
  _wmdFbScopeRi=null;
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
  var _fm=document.getElementById('fb-modal');
  if(_fm){_fm.style.left='';_fm.style.top='';_fm.style.transform='';}
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
  var titleSuffix=(_fbChecked.size<fbKeys.length)?' \u2014 '+_fbChecked.size+'/'+fbKeys.length+' FBs selected':'';
  document.getElementById('fb-modal-title').textContent=
    'Interface Bin '+_fbModalIb+(ibCat?' ['+ibCat+']':'')+
    ' \u2014 Functional Bin Breakdown ('+chkTotal.toLocaleString()+' / '+ibTotal.toLocaleString()+' die)'+titleSuffix;
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
      var lbl=cnt.toLocaleString()+' ('+pct.toFixed(1)+'% IB | '+fbFailPct.toFixed(2)+'% fail)'+(fbDsc?' \u2014 '+esc(fbDsc.substring(0,20)):'');
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
  fbKeys.forEach(function(fb){
    var cnt=fbTotals[fb];
    var pct=ibTotal>0?cnt/ibTotal*100:0;
    var fbi=fbDesc[fb]||{};
    var fbFP=allTot>0?cnt/allTot*100:0;
    var op=_fbChecked.has(fb)?'':'opacity:0.3;';
    html+='<tr style="'+op+'"><td>FB'+fb+'</td><td>'+esc(ibCat)+'</td><td>'+esc(fbi.desc||'')+'</td><td class="num">'+cnt.toLocaleString()+'</td><td class="num">'+pct.toFixed(1)+'%</td><td class="num">'+fbFP.toFixed(2)+'%</td></tr>';
  });
  tbody.innerHTML=html;
  /* Refresh HW popup if open */
  if(_bhHwOpen)_renderHwSection();
}
/* --- HW Breakdown draggable popup --- */
var _bhHwSel=new Set();
var _bhHwOpen=false;
var _hwColFilter={};  /* col name -> text filter string */
var _hwAllEntries=[];  /* [{lot,wafer,hwIdx,cnt}] — populated by _renderHwSection */
function showBhHwModal(){
  if(!_fbModalIb)return;
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
  /* Apply per-column text filters */
  var filtered=entries.filter(function(e){
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
    return pass;
  });
  var hwSel=_bhHwSel;
  /* Apply preferred display column order — Sort Partial Wafer ID always last */
  var _hwPrefOrder=['Cell ID','Unit Tester ID','Unit Tester Site ID','CellID','UnitTesterID','UnitTesterSiteID','Unit TIU','Thermal Head Id'];
  var orderedCols=_hwPrefOrder.filter(function(c){return cols.indexOf(c)>=0;}).concat(cols.filter(function(c){return _hwPrefOrder.indexOf(c)<0&&c.toLowerCase().indexOf('sort partial wafer')<0;})).concat(cols.filter(function(c){return c.toLowerCase().indexOf('sort partial wafer')>=0;}));
  var fixedCols=['Lot','Wafer'];
  var allDisplayCols=fixedCols.concat(orderedCols);
  var hdr='<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap">'
    +'<span style="color:#888;font-size:12px">'+filtered.length+' / '+entries.length+' rows &nbsp;&bull;&nbsp; '+grandTotal.toLocaleString()+' die est</span>'
    +'<button class="cb" style="font-size:11px;padding:1px 7px" onclick="IC.bhHwSelAll()">All</button>'
    +'<button class="cb" style="font-size:11px;padding:1px 7px" onclick="IC.bhHwClrAll()">None</button>'
    +'<button class="cb" style="font-size:11px;padding:1px 7px" onclick="IC.bhHwClrColFilters()">Clear Filters</button></div>';
  var filterCols=['Lot','Wafer'].concat(orderedCols);
  var th='<tr><th style="width:30px"></th>'+['Lot','Wafer','Count','%'].concat(orderedCols).map(function(c){
    return'<th style="text-align:left;white-space:normal;word-wrap:break-word">'+esc(c)+'</th>';
  }).join('')+'</tr>';
  var filterRow='<tr><td></td>'+['Lot','Wafer','Count','%'].concat(orderedCols).map(function(c){
    if(c==='Count'||c==='%')return'<td></td>';
    var val=_hwColFilter[c]||'';
    return'<td><input type="text" data-hw-fcol="'+esc(c)+'" value="'+esc(val)+'" placeholder="\u2026" style="width:100%;box-sizing:border-box;font-size:11px;padding:2px 4px;border:1px solid #ccc;border-radius:3px" oninput="IC.hwTxtFilter(this)"></td>';
  }).join('')+'</tr>';
  var trs=filtered.map(function(e){
    var combo=tbl[parseInt(e.hwIdx)]||{};
    var pct=grandTotal>0?(e.cnt/grandTotal*100).toFixed(1):'0.0';
    var sel=hwSel.size===0||hwSel.has(e.hwIdx);
    var chk=sel?'checked':'';
    var op=sel?'1':'0.4';
    return '<tr style="opacity:'+op+'">'
      +'<td><input type="checkbox" data-hw-bh="'+e.hwIdx+'" '+chk+' onclick="IC.bhHwChk(this)"></td>'
      +'<td>'+esc(e.lot)+'</td><td>'+esc(e.wafer)+'</td>'
      +'<td>'+e.cnt.toLocaleString()+'</td><td>'+pct+'%</td>'
      +orderedCols.map(function(c){return'<td>'+esc(String(combo[c]||''))+'</td>';}).join('')
      +'</tr>';
  }).join('');
  hwBody.innerHTML=hdr+'<div style="overflow-y:auto;flex:1;min-height:0"><table class="stbl" style="width:100%;table-layout:auto"><thead>'+th+filterRow+'</thead><tbody>'+trs+'</tbody></table></div>';
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
  if(_wmdFbScopeRi!==null){_wmdShowFbForWafer(String(_fbModalIb),_wmdFbScopeRi);return;}
  if(_fbModalIb===null)return;
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
function _renderFbCb(){
  var el=document.getElementById('fb-cblist');if(!el)return;
  var fbDesc=DATA.fbDescriptions||{};
  var html='';
  _fbModalFbKeys.forEach(function(fb){
    var chk=_fbChecked.has(fb)?' checked':'';
    var fbd=(fbDesc[fb]&&fbDesc[fb].desc)?fbDesc[fb].desc:'';
    html+='<label class="fb-cbitem" title="FB'+fb+(fbd?' \u2014 '+fbd:'')+'">'
      +'<input type="checkbox"'+chk+' data-fb="'+fb+'" onchange="IC.fbCbChange(this)"> FB'+fb
      +(fbd?'<span style="color:#888;font-size:11px;max-width:70px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;vertical-align:bottom"> '+esc(fbd.substring(0,18))+'</span>':'')
      +'</label>';
  });
  el.innerHTML=html;
}
function legendClick(bin,event){
  if(event&&(event.ctrlKey||event.metaKey)){showFbModal(bin);}
  else{clickLegend(bin,event);}
}
function fbCbChange(cb){var fb=cb.dataset.fb;if(cb.checked)_fbChecked.add(fb);else _fbChecked.delete(fb);_renderFbChart();rChart();if(_upmOpen)_renderUpmMaps();}
function selectAllFbs(){_fbModalFbKeys.forEach(function(fb){_fbChecked.add(fb);});_renderFbCb();_renderFbChart();rChart();if(_upmOpen)_renderUpmMaps();}
function clearFbs(){_fbChecked.clear();_renderFbCb();_renderFbChart();rChart();if(_upmOpen)_renderUpmMaps();}
function showFbWaferMap(){
  if(!_fbModalIb)return;
  /* Sync checkbox state */
  (document.querySelectorAll('#fb-cblist input[type=checkbox]')||[]).forEach(function(inp){
    if(inp.checked)_fbChecked.add(inp.dataset.fb);else _fbChecked.delete(inp.dataset.fb);
  });
  var sec=document.getElementById('fb-wm-sec'),grid=document.getElementById('fb-wm-grid'),ibEl=document.getElementById('fb-wm-ib');
  if(!sec||!grid)return;
  if(ibEl)ibEl.textContent=_fbModalIb;
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
  _wmdFbScopeRi=null;
  _fbFilterIb=null;
  document.getElementById('fb-overlay').classList.remove('open');
  closeBhHwModal();
  rChart();
}
function _wmdShowFbForWafer(ibk,ri){
  var row=DATA.rows[ri];if(!row)return;
  var fbMap=(row.ibToFb||{})[String(ibk)]||{};
  var fbTotals={};var ibTotal=(row.binCounts||{})[String(ibk)]||0;
  Object.keys(fbMap).forEach(function(fb){fbTotals[fb]=(fbTotals[fb]||0)+fbMap[fb];});
  var fbKeys=Object.keys(fbTotals).sort(function(a,b){return fbTotals[b]-fbTotals[a];});
  var fc=gFC();
  _wmdFbScopeRi=ri;
  _fbModalIb=+ibk;_fbModalFbKeys=fbKeys.slice();_fbChecked=new Set(fbKeys);
  _fbFilterIb=+ibk;
  _fbModalTotals=fbTotals;_fbModalIbTotal=ibTotal;_fbModalAllTot=fc.total;
  var lbl=(row.lot||'')+' W'+(row.wafer||'');
  _renderFbCb();_renderFbChart();
  var tm=document.getElementById('fb-modal-title');
  if(tm)tm.textContent='IB'+ibk+' \u2014 '+lbl+' \u2014 FB Breakdown';
  var fwm=document.getElementById('fb-wm-sec');if(fwm)fwm.style.display='none';
  var _fm=document.getElementById('fb-modal');
  if(_fm){_fm.style.left='';_fm.style.top='';_fm.style.transform='';}
  document.getElementById('fb-overlay').classList.add('open');
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
function toggleRow(idx,event){
  if(event&&event.shiftKey&&lR>=0){
    var lo=Math.min(idx,lR),hi=Math.max(idx,lR);
    for(var i=lo;i<=hi;i++)sR.add(i);
  }else if(event&&(event.ctrlKey||event.metaKey)){
    if(sR.has(idx)){if(sR.size>1)sR.delete(idx);}else sR.add(idx);
  }else{
    if(sR.size===DATA.rows.length){sR.clear();sR.add(idx);}
    else if(sR.size===1&&sR.has(idx)){DATA.rows.forEach(function(_,i){sR.add(i);});}
    else if(sR.has(idx)){sR.delete(idx);}
    else{sR.add(idx);}
  }
  lR=idx;upd();
}
function selectAllRows(){
  var visible=[];
  DATA.rows.forEach(function(row,i){
    var cols=[row.program,row.lot,row.wafer].concat(DATA.hasMaterial?[row.material||'']:[]);
    var show=Object.keys(_ftDdState).every(function(ci){var s=_ftDdState[ci];return !s||s.has(String(cols[parseInt(ci)]||''));});
    if(show)visible.push(i);
  });
  visible.forEach(function(i){sR.add(i);});lR=-1;upd();
}
function clearRows(){sR.clear();if(DATA.rows.length)sR.add(0);lR=-1;upd();}
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
  _renderUpmMaps();
}
function closeUpmModal(){
  _upmOpen=false;
  document.getElementById('upm-modal').classList.remove('open');
}
function setUpmMetric(idx){_upmMetricIdx=idx;_renderUpmMaps();}
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
      var ib=d[2],fb=d[3];
      var ibMatch=(_fbModalIb!==null)?(String(ib)===String(_fbModalIb)):(sB.size===AB.length||sB.has(String(ib)));
      var fbMatch=(!ibMatch)||(fb===null)||(String(_fbModalIb)!==String(ib))||(_fbChecked.size===0)||_fbChecked.has(String(fb));
      if(ibMatch&&fbMatch)filteredVals.push(v);
    });
  });
  var lo=allVals.length?Math.min.apply(null,allVals):0;
  var hi=allVals.length?Math.max.apply(null,allVals):100;
  var rng=(hi-lo)||1;
  // Auto-detect unit: values >200 are raw MHz, otherwise already percent
  var isMHz=(hi>200);
  var unit=isMHz?' MHz':'%';
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
  sR.forEach(function(ri){
    var row=DATA.rows[ri];
    if(!row||!row.dies||!row.dies.length)return;
    var dies=row.dies;
    var xs=[],ys=[];
    dies.forEach(function(d){if(d[0]!==null&&d[0]!==undefined){xs.push(d[0]);ys.push(d[1]);}});
    if(!xs.length)return;
    var xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs);
    var yMax=Math.max.apply(null,ys),yMin=Math.min.apply(null,ys);
    // Fixed canvas width so all wafers render the same physical size regardless of die count
    var pad=2, FIXED_W=150;
    var xCnt=xMax-xMin+1, yCnt=yMax-yMin+1;
    var cs=Math.max(1,(FIXED_W-pad*2)/xCnt);
    var xSpan=xMax-xMin,ySpan=yMax-yMin;
    var csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;
    var W=FIXED_W, H=Math.round(yCnt*csy+pad*2);
    var rects=[];
    dies.forEach(function(d){
      var x=d[0],y=d[1],ib=d[2],fb=d[3],hw=d[4],uv=d[(DATA.upmStart||5)+upmIdx];
      if(x===null||x===undefined)return;
      var px=(pad+(x-xMin)*cs).toFixed(2),py=(pad+(yMax-y)*csy).toFixed(2);
      var t=(uv!==null&&uv!==undefined)?Math.max(0,Math.min(1,(uv-lo)/rng)):null;
      var fill=_upmColor(t);
      var ibMatch=(_fbModalIb!==null)?(String(ib)===String(_fbModalIb)):(sB.size===AB.length||sB.has(String(ib)));
      var fbMatch=(!ibMatch)||(fb===null)||(String(_fbModalIb)!==String(ib))||(_fbChecked.size===0)||_fbChecked.has(String(fb));
      var hwMatch=(_bhHwSel.size===0)||(hw===null)||_bhHwSel.has(String(hw));
      var opacity=(ibMatch&&fbMatch&&hwMatch)?'1':'0.12';
      var upmStr=uv!==null&&uv!==undefined?fmtBoth(Number(uv)):'no UPM';
      var tipStr=upmStr+'|IB'+ib+(fb!==null?' FB'+fb:'')+(hw!==null?' HW'+hw:'')+'  ('+x+','+y+')';
      rects.push('<rect x="'+px+'" y="'+py+'" width="'+(cs*0.92).toFixed(2)+'" height="'+(csy*0.92).toFixed(2)+'" fill="'+fill+'" opacity="'+opacity+'" data-tip="'+tipStr+'"/>');
    });
    var lbl=(row.lot||'')+' W'+(row.wafer||'');
    mapsHtml+='<div class="upm-ww"><div class="upm-wlbl">'+lbl+'</div><svg width="'+W+'" height="'+H+'" style="display:block">'+rects.join('')+'</svg></div>';
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
  _setupUpmBodyHover();
}
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
    } else {_upmTipHide();}
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
var _wmActiveTab='impact';
var _wmRetChecked=null; /* Set of "rx,ry" strings for highlighted sites, or null */
var _wmSiteToShots=null; /* lazy cache: "rx,ry" -> Set of shot indices */
var _wmOpen=false;
var _wmdOpen=false,_wmdRi=-1;
var _wmdDX=0,_wmdDY=0,_wmdDragging=false;

function _wmVisRows(){
  var out=[];
  sR.forEach(function(ri){if(_wmSelRows===null||_wmSelRows.has(ri))out.push(ri);});
  return out;
}
function _wmIsFail(ib){return ib===null||ib===undefined||ib>=_wmFailThresh;}
function _wmBinActive(ib){return _wmBinChecked===null||_wmBinChecked.has(ib);}

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
    lotMap[lot].push({ri:ri,w:w});
  });
  var threshRow='<div class="wm-thresh-row"><span style="font-size:11px;color:#555;white-space:nowrap">Fail\u202f\u2265\u202f</span>';
  [1,2,3,4,5].forEach(function(t){
    threshRow+='<button class="wm-tbtn'+(_wmFailThresh===t?' on':'')+'" onclick="IC._wmSetThresh('+t+')">IB'+t+'</button>';
  });
  threshRow+='</div>';
  if(!lotOrder.length){ctrl.innerHTML=threshRow;return;}
  var filtRow='<span style="font-size:11px;color:#555;white-space:nowrap;align-self:center">Wafers:</span>'
    +'<span class="wm-selall" onclick="IC._wmSelectAll(true)">All</span>'
    +' <span class="wm-selall" onclick="IC._wmSelectAll(false)">None</span>'
    +'<div class="wm-filtbar">';
  lotOrder.forEach(function(lot){
    filtRow+='<div class="wm-lot-grp"><span class="wm-lot-lbl" onclick="IC._wmToggleLot(\''+_wmEsc(lot)+'\')" title="Toggle all in lot">'+_wmEsc(lot)+'</span>';
    lotMap[lot].forEach(function(item){
      var chk=(_wmSelRows===null||_wmSelRows.has(item.ri));
      filtRow+='<label class="wm-wcb"><input type="checkbox" '+(chk?'checked':'')+' onchange="IC._wmToggleRow('+item.ri+',this.checked)">W'+_wmEsc(item.w)+'</label>';
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
  var sites=Object.keys(siteFailCount);
  if(!sites.length){el.innerHTML='<span style="color:#7f8c8d;font-size:11px">No fail dies mapped to reticle sites for selected wafers/bins.</span>';return;}
  /* Sort by fail count desc */
  sites.sort(function(a,b){return siteFailCount[b]-siteFailCount[a];});
  /* Total shots on wafer for reference (use retSiteTotals) */
  var nWafers=vis.filter(function(ri){var r=DATA.rows[ri];return r&&r.dies&&r.dies.length;}).length;
  var h='<div style="font-size:10px;color:#555;margin-bottom:6px"><b>Reticle Site Fail Analysis</b> \u2014 '+sites.length+' reticle site(s) with fails across '+nWafers+' wafer(s). '
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
  sites.forEach(function(sk){
    var parts=sk.split(',');var rx=parts[0],ry=parts[1];
    var fc=siteFailCount[sk];
    var pctOfFail=grandTotalFail>0?(fc/grandTotalFail*100).toFixed(1):'0.0';
    var waferHits=Object.keys(siteFailShots[sk]).length;
    var hitPct=nWafers>0?(waferHits/nWafers*100).toFixed(0):0;
    var heatPct=waferHits/nWafers;
    var totShots=(DATA.retSiteTotals&&DATA.retSiteTotals[sk])||1;
    var bg=heatPct>=0.7?'#fde8e8':heatPct>=0.4?'#fef3cd':altRow?'#f0f4fb':'#fff';
    var isChk=_wmRetChecked&&_wmRetChecked.has(sk);
    h+='<tr style="background:'+bg+'">'
      +'<td style="padding:3px 6px;text-align:center"><input type="checkbox"'+(isChk?' checked':'')+' onchange="IC._wmRetSiteToggle(\''+sk+'\',this.checked)" style="cursor:pointer;width:13px;height:13px"></td>'
      +'<td style="padding:3px 8px;text-align:center">'+rx+'</td>'
      +'<td style="padding:3px 8px;text-align:center">'+ry+'</td>'
      +'<td style="padding:3px 8px;text-align:right">'+fc+'</td>'
      +'<td style="padding:3px 8px;text-align:right">'+pctOfFail+'%</td>'
      +'<td style="padding:3px 8px;text-align:right">'+waferHits+'/'+nWafers+'</td>'
      +'<td style="padding:3px 8px;text-align:right;font-weight:'+(heatPct>=0.7?'bold':'normal')+';color:'+(heatPct>=0.7?'#c0392b':heatPct>=0.4?'#e67e22':'#27ae60')+'">'+(+hitPct)+'%</td>'
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
  var FIXED_W=180,pad=2;
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
      ibSeen[ibKey]=fill;
      /* opacity: fade if bin is unchecked */
      var binOn=(_wmBinChecked===null||_wmBinChecked.has(ibKey));
      var opacity=binOn?'1':'0.08';
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
      var clickable=isFail&&ibKey!==null&&binOn;
      rects.push('<rect x="'+px+'" y="'+py+'" width="'+(cs*0.9).toFixed(1)+'"'
        +' height="'+(csy*0.9).toFixed(1)+'" fill="'+fill+'" opacity="'+opacity+'"'
        +' data-ib="'+(ibKey!==null?ibKey:'')+'" data-tip="('+x+','+y+') '+(ibKey!==null?'IB'+ibKey:'no IB')+'"'
        +' style="cursor:'+(clickable?'pointer':'default')+'"'
        +(isFail&&ibKey!==null&&cs>3&&binOn?' stroke="rgba(0,0,0,.25)" stroke-width="0.3"':'')+'/>');
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
    /* wafer circle clip: center and radii in SVG pixel space */
    var cx=(pad+(xCtr-xMin)*cs+cs*0.45).toFixed(1);
    var cy=(pad+(yMax-yCtr)*csy+csy*0.45).toFixed(1);
    var rx=(xRad*cs+cs*0.5).toFixed(1);
    var ry=(yRad*csy+csy*0.5).toFixed(1);
    var clipId='wmc-'+ri;
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
          var sx=(pad+(shot[0]-xMin)*cs).toFixed(1),sy=(pad+(yMax-shot[3])*csy).toFixed(1);
          var sw=((shot[2]-shot[0]+1)*cs).toFixed(1),sh=((shot[3]-shot[1]+1)*csy).toFixed(1);
          retOutlines+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#2471a3" stroke-width="0.5" opacity="0.3"/>';
        });
        DATA.retShots.forEach(function(shot,si){
          if(!failShotIdx.has(si))return;
          var sx=(pad+(shot[0]-xMin)*cs).toFixed(1),sy=(pad+(yMax-shot[3])*csy).toFixed(1);
          var sw=((shot[2]-shot[0]+1)*cs).toFixed(1),sh=((shot[3]-shot[1]+1)*csy).toFixed(1);
          retOutlines+='<rect x="'+sx+'" y="'+sy+'" width="'+sw+'" height="'+sh+'" fill="none" stroke="#c0392b" stroke-width="1.2" opacity="0.9"/>';
        });
      }
    }
    /* wafer map: clicking title opens detail popup; die click opens FB */
    mapsHtml+='<div style="text-align:center">'
      +'<div class="wm-wlbl" style="cursor:pointer;text-decoration:underline" onclick="IC._wmdOpen('+ri+')" title="Open wafer detail">'+lbl+'</div>'
      +'<svg width="'+W+'" height="'+H+'" style="display:block">'+clipDef+'<g clip-path="url(#'+clipId+')">'+rects.join('')+retOutlines+'</g>'+borderCircle+'</svg>'
      +'<div style="font-size:10px;color:'+pCol+';font-weight:bold;margin-top:2px">'+primary+'</div>'
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
      ibh+='</div><div style="font-size:10px;font-weight:bold;color:'+bdCol+';white-space:nowrap;margin-left:4px">\u2192'+bestDim.toUpperCase()+'</div></div>';
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
  /* reset to UPM tab when opening a new wafer */
  _wmdTab='upm';
  var uPane=document.getElementById('wmd-upm-pane');var pPane=document.getElementById('wmd-pat-pane');
  var uBtn=document.getElementById('wmd-tab-upm');var pBtn=document.getElementById('wmd-tab-pat');
  if(uPane)uPane.style.display='flex';if(pPane)pPane.style.display='none';
  if(uBtn)uBtn.classList.add('on');if(pBtn)pBtn.classList.remove('on');
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
      var analyzeBtn=DATA.hasFunctionalBin?'<button onclick="IC._wmdShowFbForWafer(\''+ibk+'\','+ri+')" title="FB breakdown for IB'+ibk+' on this wafer" style="background:none;border:none;cursor:pointer;font-size:11px;padding:0 2px;line-height:1;flex-shrink:0">🔬</button>':'';
      ibH+='<div style="display:flex;align-items:center;gap:5px;font-size:11px;margin-bottom:3px">'
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

  /* ---- UPM heatmap ---- */
  var upmBody=document.getElementById('wmd-upm-body');
  var upmSel=document.getElementById('wmd-upm-sel');
  if(upmBody){
    var uCols=DATA.upmCols||[];
    if(!uCols.length||!row.dies){upmBody.innerHTML='<span style="color:#aaa">No UPM data</span>';return;}
    /* col selector buttons */
    var selH='';
    uCols.forEach(function(uc,ui){
      selH+='<button class="wm-tbtn'+(_wmdUpmIdx===ui?' on':'')+'" onclick="IC._wmdUpmSel('+ri+','+ui+')" style="font-size:10px;padding:1px 6px">'+_wmEsc(uc.label||'U'+ui)+'</button>';
    });
    if(upmSel)upmSel.innerHTML=selH;

    var upmIdx=_wmdUpmIdx;
    var colMeta=uCols[upmIdx]||{};
    var dies=row.dies;
    var xs=[],ys=[];
    dies.forEach(function(d){if(d[0]!==null)xs.push(d[0]);});
    dies.forEach(function(d){if(d[1]!==null)ys.push(d[1]);});
    if(!xs.length){upmBody.innerHTML='<span style="color:#aaa">No die coords</span>';return;}
    var xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs);
    var yMin=Math.min.apply(null,ys),yMax=Math.max.apply(null,ys);
    var xCnt=xMax-xMin+1;
    var pad=4,FIXED_W=320;
    var cs=Math.max(2,(FIXED_W-pad*2)/xCnt);
    var xSpan=xMax-xMin,ySpan=yMax-yMin;
    var csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;
    var W=FIXED_W,H=Math.round((yMax-yMin+1)*csy+pad*2);
    var ustart=DATA.upmStart||5;
    var allVals=[];
    dies.forEach(function(d){var v=d[ustart+upmIdx];if(v!==null&&v!==undefined)allVals.push(v);});
    var lo=allVals.length?Math.min.apply(null,allVals):0;
    var hi=allVals.length?Math.max.apply(null,allVals):100;
    var rng=(hi-lo)||1;
    var isMHz=(hi>200);
    var fmt=function(v){return isMHz?Math.round(v)+'MHz':v.toFixed(2)+'%';};
    var rects=[];
    dies.forEach(function(d){
      var x=d[0],y=d[1],ib=d[2],hw=d[4];
      var uv=d[ustart+upmIdx];
      if(x===null||x===undefined)return;
      var px=(pad+(x-xMin)*cs).toFixed(1),py=(pad+(yMax-y)*csy).toFixed(1);
      var t=(uv!==null&&uv!==undefined)?Math.max(0,Math.min(1,(uv-lo)/rng)):null;
      var fill=(t!==null)?_upmColor(t):'#bdc3c7';
      var ibs=ib!==null?'IB'+ib:'';
      var uvs=uv!==null?fmt(uv):'no UPM';
      rects.push('<rect x="'+px+'" y="'+py+'" width="'+(cs*0.9).toFixed(1)+'" height="'+(csy*0.9).toFixed(1)+'" fill="'+fill+'" data-tip="('+x+','+y+') '+uvs+' '+ibs+'"/>');
    });
    /* gradient legend */
    var lgH='<defs><linearGradient id="wmd-lg" x1="0" x2="1" y1="0" y2="0">'
      +'<stop offset="0%" stop-color="#dc0000"/><stop offset="50%" stop-color="#ffffff"/><stop offset="100%" stop-color="#0032dc"/>'
      +'</linearGradient></defs>'
      +'<rect x="0" y="0" width="'+W+'" height="14" fill="url(#wmd-lg)"/>'
      +'<text x="2" y="11" font-size="9" font-family="Arial" fill="#fff">'+fmt(lo)+'</text>'
      +'<text x="'+(W-2)+'" y="11" font-size="9" font-family="Arial" fill="#fff" text-anchor="end">'+fmt(hi)+'</text>';
    var svgH='<svg width="'+W+'" height="'+(H+14)+'" style="display:block"><g transform="translate(0,14)">'+rects.join('')+'</g>'+lgH+'</svg>';
    upmBody.innerHTML=svgH;
    _wmdSetupHover(upmBody);
  }
}
var _wmdUpmIdx=0;
var _wmdTab='upm';
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
    rects.push('<rect x="'+px+'" y="'+py+'" width="'+(cs*0.9).toFixed(1)+'" height="'+(csy*0.9).toFixed(1)+'" fill="'+fill+'" data-tip="('+x+','+y+') '+(ib!==null&&ib!==undefined?'IB'+ib:'no IB')+'"/>');
  });
  var sc=_wmScorePattern(failXn,failYn);
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
  patBody.innerHTML='<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start">'
    +'<div>'+svgH+'</div>'
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
    hasDie=true;var nA=0,nB=0,nC=0,uv=[];
    row.dies.forEach(function(d){
      var ib=d[2],up=d.length>uI?d[uI]:null;
      if(up!=null)uv.push(up);
      if((ib===1||ib===2)&&up!=null&&up>=_dlcpT)nA++;
      else if(ib!=null&&ib>=1&&ib<=4)nB++;
      else nC++;
    });
    uv.sort(function(a,b){return a-b;});
    var med=null;if(uv.length){var m=Math.floor(uv.length/2);med=uv.length%2===0?(uv[m-1]+uv[m])/2:uv[m];}
    res.push({lot:row.lot||'',wafer:row.wafer||'',mat:row.material||'',tot:row.dies.length,med:med,nA:nA,nB:nB,nC:nC});
  });
  return{rows:res,noDies:!hasDie};
}
function _dlcpRenderSummary(tA,tB,tC,tN,medAll){
  var sb=document.getElementById('dlcp-sumbox');if(!sb)return;
  if(!tN){sb.innerHTML='<span style="color:#999;font-size:12px">No data</span>';return;}
  var mTxt=medAll!=null?medAll.toFixed(2)+'%':'\u2014';
  sb.innerHTML='<div class="dlcp-sum-grp"><div class="dlcp-sum-lbl">Total Die</div><div class="dlcp-sum-val">'+tN+'</div></div>'
    +'<div class="dlcp-sum-grp"><div class="dlcp-sum-lbl">Med UPM%</div><div class="dlcp-sum-val">'+mTxt+'</div></div>'
    +'<div class="dlcp-sum-grp pass"><div class="dlcp-sum-lbl">HP (IB1/2, UPM\u2265thr)</div><div class="dlcp-sum-val" style="color:#1a5276">'+tA+'<span class="dlcp-sum-pct">'+(tA+tB>0?(tA/(tA+tB)*100).toFixed(1):0)+'% of IB1-4)</span></div></div>'
    +'<div class="dlcp-sum-grp marg"><div class="dlcp-sum-lbl">LP (IB1-4, below thr)</div><div class="dlcp-sum-val" style="color:#ba6b0a">'+tB+'<span class="dlcp-sum-pct">'+(tA+tB>0?(tB/(tA+tB)*100).toFixed(1):0)+'% of IB1-4)</span></div></div>'
    +'<div class="dlcp-sum-grp fail"><div class="dlcp-sum-lbl">Fail (IB&gt;4)</div><div class="dlcp-sum-val" style="color:#c0392b">'+tC+'<span class="dlcp-sum-pct">'+(tN>0?(tC/tN*100).toFixed(1):0)+'% of total)</span></div></div>';
}
function _dlcpRenderTable(){
  var r=_dlcpComputeRows(),tA=0,tB=0,tC=0,tN=0,allUv=[],html='';
  var tb=document.getElementById('dlcp-tb');if(!tb)return;
  if(r.noDies){tb.innerHTML='<tr><td colspan="11" style="padding:14px;color:#7f8c8d;text-align:center">No die-level UPM data. Re-run pipeline with upmInfo configured.</td></tr>';_dlcpRenderSummary(0,0,0,0,null);return;}
  r.rows.forEach(function(x){
    var t=x.nA+x.nB+x.nC;if(!t)return;
    var f12=x.nA+x.nB;
    tA+=x.nA;tB+=x.nB;tC+=x.nC;tN+=t;
    html+='<tr>'
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
      +'</tr>';
  });
  tb.innerHTML=html;
  var uI=(DATA.upmStart||5)+_dlcpUi;
  sR.forEach(function(ri){var row=DATA.rows[ri];if(!row||!row.dies)return;row.dies.forEach(function(d){var up=d.length>uI?d[uI]:null;if(up!=null)allUv.push(up);});});
  allUv.sort(function(a,b){return a-b;});
  var medAll=null;if(allUv.length){var m2=Math.floor(allUv.length/2);medAll=allUv.length%2===0?(allUv[m2-1]+allUv[m2])/2:allUv[m2];}
  _dlcpRenderSummary(tA,tB,tC,tN,medAll);
  var nd=document.getElementById('dlcp-note');
  if(nd)nd.innerHTML='<b>HP%</b> = HP / (HP+LP) &nbsp;|&nbsp; <b>LP%</b> = LP / (HP+LP) &nbsp;|&nbsp; <b>Fail%</b> = Fail / Total all bins &nbsp;|&nbsp; Threshold applied: <b>'+_dlcpT.toFixed(1)+'%</b>';
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
  var hp=[],lp=[];
  sR.forEach(function(ri){
    var row=DATA.rows[ri];if(!row||!row.dies)return;
    row.dies.forEach(function(d){
      var ib=d[2],up=d.length>uI?d[uI]:null;if(up==null)return;
      if((ib===1||ib===2)&&up>=_dlcpT)hp.push(up);
      else if(ib!=null&&ib>=1&&ib<=4)lp.push(up);
    });
  });
  hp.sort(function(a,b){return a-b;});lp.sort(function(a,b){return a-b;});
  if(!hp.length&&!lp.length){
    ctx.fillStyle='#999';ctx.font='13px Arial';ctx.textAlign='center';ctx.fillText('No UPM die data in selected wafers',W/2,H/2);return;
  }
  var ML=52,MR=16,MT=22,MB=42,PW=W-ML-MR,PH=H-MT-MB;
  var all=hp.concat(lp);
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
  function drawCdf(arr,col){if(!arr.length)return;
    ctx.save();ctx.strokeStyle=col;ctx.lineWidth=2;var n=arr.length;
    ctx.beginPath();ctx.moveTo(xp(arr[0]),yp(0));
    for(var i=0;i<n;i++){ctx.lineTo(xp(arr[i]),yp((i+1)/n*100));if(i<n-1)ctx.lineTo(xp(arr[i+1]),yp((i+1)/n*100));}
    ctx.lineTo(ML+PW,yp(100));ctx.stroke();ctx.restore();
  }
  drawCdf(lp,'#e67e22');drawCdf(hp,'#2980b9');
  ctx.strokeStyle='#555';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(ML,MT);ctx.lineTo(ML,MT+PH);ctx.lineTo(ML+PW,MT+PH);ctx.stroke();
  ctx.fillStyle='#555';ctx.font='11px Arial';ctx.textAlign='right';
  for(var yi2=0;yi2<=4;yi2++){ctx.fillText(yi2*25+'%',ML-4,yp(yi2*25)+4);}
  ctx.textAlign='center';var rng=xMx-xMn,stp=rng>20?5:rng>10?2:1,xs=Math.ceil(xMn/stp)*stp;
  for(var xv=xs;xv<=xMx;xv+=stp){ctx.fillText(xv.toFixed(0)+'%',xp(xv),MT+PH+14);}
  ctx.fillStyle='#2c3e50';ctx.font='bold 11px Arial';ctx.textAlign='center';
  ctx.fillText('UPM %',ML+PW/2,H-4);
  ctx.save();ctx.translate(13,MT+PH/2);ctx.rotate(-Math.PI/2);ctx.fillText('Cumulative %',0,0);ctx.restore();
  var ly=MT+8;
  ctx.fillStyle='#2980b9';ctx.fillRect(ML,ly,22,3);ctx.fillStyle='#2c3e50';ctx.font='11px Arial';ctx.textAlign='left';ctx.fillText('HP (n='+hp.length+')',ML+26,ly+4);
  ctx.fillStyle='#e67e22';ctx.fillRect(ML+130,ly,22,3);ctx.fillText('LP (n='+lp.length+')',ML+156,ly+4);
}
function _dlcpRender(){
  _dlcpRenderTable();
  requestAnimationFrame(_dlcpRenderCdf);
}
function dlcpSlider(){
  var sl=document.getElementById('dlcp-sl');if(!sl)return;
  _dlcpT=parseFloat(sl.value);
  var tv=document.getElementById('dlcp-tv');if(tv)tv.textContent=_dlcpT.toFixed(1)+'%';
  _dlcpRender();
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
  /* sync slider display */
  var sl=document.getElementById('dlcp-sl');if(sl){sl.value=_dlcpT;}
  var tv=document.getElementById('dlcp-tv');if(tv)tv.textContent=_dlcpT.toFixed(1)+'%';
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
function exportCsv(){
  // Export currently visible (filtered) rows as CSV including all bin columns
  var active=Array.from(sR).sort(function(a,b){return a-b;});
  var bins=DATA.bins;
  var fixedHdrs=['Program','Lot','Wafer','Material','Total'];
  var hdr=fixedHdrs.concat(bins.map(function(b){return 'IB'+b+'_count';})).concat(bins.map(function(b){return 'IB'+b+'_pct';}));
  var lines=[hdr.join(',')];
  active.forEach(function(i){
    var r=DATA.rows[i];
    var tot=r.total||0;
    var fixed=[r.program||'',r.lot||'',r.wafer||'',r.material||'',tot];
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
  gFC:gFC,DATA:DATA,sR:sR,rFilter:rFilter,ftDdOpen:ftDdOpen,showFbModal:showFbModal,closeFbModal:closeFbModal,
  fbCbChange:fbCbChange,selectAllFbs:selectAllFbs,clearFbs:clearFbs,showFbWaferMap:showFbWaferMap,fbTileClick:fbTileClick,
  bhHwChk:bhHwChk,bhHwSelAll:bhHwSelAll,bhHwClrAll:bhHwClrAll,bhHwClrColFilters:bhHwClrColFilters,
  hwTxtFilter:hwTxtFilter,showBhHwModal:showBhHwModal,closeBhHwModal:closeBhHwModal,
  refreshFb:refreshFb,refreshUpm:refreshUpm,selectYieldBins:selectYieldBins,
  lgSearch:lgSearch,showUpmModal:showUpmModal,closeUpmModal:closeUpmModal,
  _wmRetSiteToggle:_wmRetSiteToggle,_wmRetClear:_wmRetClear,_wmRenderReticle:_wmRenderReticle,setUpmMetric:setUpmMetric,
  openDlcpModal:openDlcpModal,closeDlcpModal:closeDlcpModal,dlcpSlider:dlcpSlider,dlcpSetCol:dlcpSetCol,
  openWmModal:openWmModal,closeWmModal:closeWmModal,
  exportCsv:exportCsv,exportYieldCsv:exportYieldCsv,
  _wmToggleRow:_wmToggleRow,_wmToggleLot:_wmToggleLot,_wmSelectAll:_wmSelectAll,_wmSetThresh:_wmSetThresh,
  _wmTab:_wmTab,_wmToggleBin:_wmToggleBin,_wmToggleBinAll:_wmToggleBinAll,
  _wmdOpen:_wmdOpen,_wmdClose:_wmdClose,_wmdUpmSel:_wmdUpmSel,_wmdTabSel:_wmdTabSel,_wmdRiVal:_wmdRiVal,_wmdShowFbForWafer:_wmdShowFbForWafer,
  _wmInlineToggle:_wmInlineToggle};
})();
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
    html_out.write_text(_wm_inject(html), encoding='utf-8')


def main():
    if len(sys.argv) < 2:
        print('Usage: bin_distribution_html.py <csv_path> [out_dir] [fail_bucket_table_path]')
        sys.exit(2)
    csvp = sys.argv[1]
    if not (csvp.lower().endswith('.csv') or csvp.lower().endswith('.csv.gz')):
        print(f'Skipping non-CSV file: {csvp}')
        sys.exit(0)
    outd = sys.argv[2] if len(sys.argv) > 2 else None
    tbl  = sys.argv[3] if len(sys.argv) > 3 else None
    generate(csvp, outd, tbl_path=tbl)


if __name__ == '__main__':
    main()
