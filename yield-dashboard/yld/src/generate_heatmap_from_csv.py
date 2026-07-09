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
    from csv_utils import detect_encoding, sniff_columns, read_csv_smart
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
