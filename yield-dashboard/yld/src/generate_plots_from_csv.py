"""
generate_plots_from_csv.py
--------------------------
Reads a plot-spec JSON and a yield CSV, produces one combined HTML with all
requested charts embedded as base64 PNG images.

Supported analysis types
------------------------
distribution
    Field distribution bar chart.
    filter:      optional row filter before counting
    aggregation: optional mode=percentage with base (fixed value or column)
    output:      show_absolute, show_percentage, percentage_label

xy
    Scatter plot of one CSV column vs another.
    filter:      optional row filter

Filter spec (used by both types)
---------------------------------
{
  "column": "<col>",
  "match": {
    "method": "starts_with" | "contains" | "equals" | "regex",
    "value":  "<string>"
  }
}

Aggregation spec (distribution only)
--------------------------------------
{
  "mode": "percentage",
  "base": {
    "type": "fixed",          -- divide by a literal number
    "value": 9154
  }
  -- OR --
  "base": {
    "type": "column_count",   -- divide by count of rows where column != NaN
    "column": "<col>"
  }
}

CLI
---
python generate_plots_from_csv.py <csv_path> <plot_spec_json_path> [out_dir]
"""

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
    from csv_utils import detect_encoding, sniff_columns, read_csv_smart
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
