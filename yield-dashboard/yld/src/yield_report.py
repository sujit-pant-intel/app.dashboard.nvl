#!/usr/bin/env python3
"""
yield_report.py
---------------
Weekly pareto yield report.  Reads Dashboard.html, resolves each run's
*_BinDistribution.html, groups runs by ISO calendar week, and generates
a standalone HTML report with a bin-fail pareto chart per week.

Usage:
    python yield_report.py Dashboard.html
    python yield_report.py Dashboard.html --out my_report.html
    python yield_report.py Dashboard.html --weeks 8
"""

import sys
import os
import re
import argparse
import io
import base64
from pathlib import Path
from datetime import datetime, timedelta


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
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    HAVE_MPL = True
except ImportError:
    HAVE_MPL = False


# Re-use parse helpers from compare_runs
try:
    import compare_runs as _cr
    _parse_dashboard  = _cr.parse_dashboard
    _find_bin_html    = _cr.find_bin_html
    _parse_bin_html   = _cr.parse_bin_html
    _find_xlsx        = _cr.find_xlsx
    HAVE_CR = True
except ImportError:
    HAVE_CR = False
    _parse_dashboard = None


# ---------------------------------------------------------------------------
# Colour palette (matches compare_runs)
# ---------------------------------------------------------------------------

_WEEK_COLORS = [
    '#2980b9', '#27ae60', '#e74c3c', '#f39c12',
    '#8e44ad', '#16a085', '#d35400', '#2c3e50',
    '#c0392b', '#1abc9c', '#7f8c8d', '#8e44ad',
]


def _esc(s: str) -> str:
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _fig_b64(fig, dpi: int = 130) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('ascii')


INTERVALS = ['daily', 'weekly', 'bi-weekly', 'monthly']

# ---------------------------------------------------------------------------
# 1.  Timestamp → period key helpers
# ---------------------------------------------------------------------------

_TS_FMTS = (
    '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
    '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y',
    '%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M', '%m/%d/%Y',
    '%Y%m%d',
)


def _parse_ts(ts: str) -> datetime | None:
    ts = (ts or '').strip()
    for fmt in _TS_FMTS:
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            pass
    m = re.search(r'(\d{8})', ts)
    if m:
        try:
            return datetime.strptime(m.group(1), '%Y%m%d')
        except ValueError:
            pass
    return None


def _ts_to_isoweek(ts: str) -> str | None:
    """Return 'YYYY-Www' or None if unparseable (kept for back-compat)."""
    dt = _parse_ts(ts)
    if dt:
        iso_yr, iso_wk, _ = dt.isocalendar()
        return f'{iso_yr}-W{iso_wk:02d}'
    return None


def _ts_to_period(ts: str, interval: str) -> str | None:
    """Return period key string based on interval."""
    dt = _parse_ts(ts)
    if dt is None:
        return None
    if interval == 'daily':
        return dt.strftime('%Y-%m-%d')
    elif interval == 'weekly':
        iso_yr, iso_wk, _ = dt.isocalendar()
        return f'{iso_yr}-W{iso_wk:02d}'
    elif interval == 'bi-weekly':
        iso_yr, iso_wk, _ = dt.isocalendar()
        biweek = ((iso_wk - 1) // 2) * 2 + 1   # odd week = start of bi-week pair
        return f'{iso_yr}-BW{biweek:02d}'
    elif interval == 'monthly':
        return dt.strftime('%Y-%m')
    return _ts_to_isoweek(ts)  # fallback


def _period_sort_key(period_str: str) -> datetime:
    """Return a datetime suitable for sorting any period key."""
    # daily: YYYY-MM-DD
    for fmt in ('%Y-%m-%d', '%Y-%m'):
        try:
            return datetime.strptime(period_str, fmt)
        except ValueError:
            pass
    # weekly: YYYY-Www
    m = re.match(r'^(\d{4})-W(\d{2})$', period_str)
    if m:
        yr, wk = int(m.group(1)), int(m.group(2))
        jan4 = datetime(yr, 1, 4)
        return jan4 - timedelta(days=jan4.weekday()) + timedelta(weeks=wk - 1)
    # bi-weekly: YYYY-BWnn
    m = re.match(r'^(\d{4})-BW(\d{2})$', period_str)
    if m:
        yr, wk = int(m.group(1)), int(m.group(2))
        jan4 = datetime(yr, 1, 4)
        return jan4 - timedelta(days=jan4.weekday()) + timedelta(weeks=wk - 1)
    return datetime.min


def _week_start(week_str: str) -> datetime:
    """Return Monday of the ISO week 'YYYY-Www' (kept for back-compat)."""
    return _period_sort_key(week_str)


# ---------------------------------------------------------------------------
# 2b.  Build runs_data from a CSV / ZIP / GZ file (via trend_chart.load_csv)
# ---------------------------------------------------------------------------

def runs_from_csv(csv_path: Path, log=None, interval: str = 'weekly') -> list[dict]:
    """
    Load an ibin CSV/ZIP/GZ file and return a list of run dicts in the same
    format produced by load_run_data(), so they can be passed to generate_report.

    Each run dict contains:
        name, label, ts, week, run_date, bin_data (with bin_summary_rows + yield_rows)
    """
    import trend_chart as tc

    runs = tc.load_csv(csv_path, log=log)
    result = []
    for r in runs:
        # Build bin_summary_rows from bin_counts
        total = r.get('total_dies', 0) or 1
        bin_summary_rows = []
        for ibin, cnt in r.get('bin_counts', {}).items():
            fail_pct = cnt / total * 100 if total else 0.0
            bin_summary_rows.append({
                'ibin': ibin,
                'fail_count': cnt,
                'fail_pct': fail_pct,
                'desc': '',
            })

        # Build yield_rows for the pass bins (1/2 = FF, 1/2/3/4 = FF+DF)
        pass_cnt_ff    = sum(r['bin_counts'].get(b, 0) for b in (1, 2))
        pass_cnt_ffdf  = sum(r['bin_counts'].get(b, 0) for b in (1, 2, 3, 4))
        yield_rows = [
            {'bin': '1/2',     'yield_pct': pass_cnt_ff   / total * 100 if total else None},
            {'bin': '1/2/3/4', 'yield_pct': pass_cnt_ffdf / total * 100 if total else None},
        ]

        run_date = r.get('date')
        ts_str   = r.get('date_str', '') or (run_date.strftime('%Y-%m-%d') if run_date else '')
        period   = _ts_to_period(ts_str, interval)
        if period is None and run_date:
            period = _ts_to_period(run_date.strftime('%Y-%m-%d'), interval)
        if period is None:
            period = 'unknown'

        result.append({
            'name':     r.get('label', r.get('lot', '')),
            'label':    r.get('label', ''),
            'ts':       ts_str,
            'week':     period,
            'run_date': run_date,
            'bin_data': {
                'bin_summary_rows': bin_summary_rows,
                'yield_rows':       yield_rows,
            },
        })
        if log:
            log(f'  [{r.get("label", "")}]  period={period}  {len(bin_summary_rows)} bins\n')
    return result


# ---------------------------------------------------------------------------
# 2.  Load run data (bin_fail) for a list of records
# ---------------------------------------------------------------------------

def load_run_data(records: list[dict], dash_dir: Path,
                  log=None, interval: str = 'weekly') -> list[dict]:
    """
    For each record return an augmented dict with:
        bin_data  — output of _parse_bin_html or None
        week      — 'YYYY-Www'  (derived from ts or xlsx mtime)
        run_date  — datetime object
    """
    import compare_runs as cr
    result = []
    for rec in records:
        href = rec.get('index_href', '')
        output_dir = None
        if href:
            href_clean = re.sub(r'^file:///', '', href).replace('/', os.sep)
            idx_path   = Path(href_clean) if os.path.isabs(href_clean) else dash_dir / href_clean
            output_dir = idx_path.parent

        bin_data = None
        run_dt   = None
        if output_dir and output_dir.exists():
            bin_p = cr.find_bin_html(output_dir)
            if bin_p:
                bin_data = cr.parse_bin_html(bin_p)
                try:
                    run_dt = datetime.fromtimestamp(bin_p.stat().st_mtime)
                except Exception:
                    pass
            if run_dt is None:
                xlsx_p = cr.find_xlsx(dash_dir, href)
                if xlsx_p:
                    try:
                        run_dt = datetime.fromtimestamp(xlsx_p.stat().st_mtime)
                    except Exception:
                        pass

        # Try ts field for date
        period = _ts_to_period(rec.get('ts', ''), interval)
        if period is None and run_dt:
            period = _ts_to_period(run_dt.strftime('%Y-%m-%d'), interval)
        if period is None:
            # Pull date from stem  e.g.  NCXSDJXP0H51M202611-1  → 202611 ≈ 2026 wk11
            m = re.search(r'(\d{6})(?![\d])', rec.get('stem', ''))
            if m:
                ds = m.group(1)
                try:
                    dt = datetime.strptime(ds, '%Y%m')
                    period = _ts_to_period(dt.strftime('%Y-%m-%d'), interval)
                except ValueError:
                    pass
        if period is None:
            period = 'unknown'

        result.append({
            **rec,
            'bin_data': bin_data,
            'week': period,      # kept as 'week' key for back-compat
            'run_date': run_dt,
        })
        if log:
            status = f'{bin_p.name}' if (output_dir and output_dir.exists() and bin_data) else 'no bin data'
            log(f'  [{rec["name"]}]  period={period}  {status}\n')
    return result


# ---------------------------------------------------------------------------
# 3.  Group runs by ISO week
# ---------------------------------------------------------------------------

def group_by_week(runs: list[dict]) -> dict[str, list[dict]]:
    """Return OrderedDict  period_str → [run, ...] sorted chronologically."""
    from collections import OrderedDict
    week_map: dict[str, list] = {}
    for r in runs:
        week_map.setdefault(r['week'], []).append(r)
    return OrderedDict(sorted(week_map.items(), key=lambda kv: _period_sort_key(kv[0])))


# ---------------------------------------------------------------------------
# 4.  Pareto charts
# ---------------------------------------------------------------------------

def _ibin_label(ibin_key: str, ibin_desc: dict, cfg: dict | None) -> str:
    """Return display label for an ibin, using product config names if available."""
    desc = ''
    if cfg:
        ibin_name_map = cfg.get('ibin_name', {})
        try:
            k_int = int(float(ibin_key))
            desc = ibin_name_map.get(k_int, ibin_name_map.get(str(k_int), ''))
        except (ValueError, TypeError):
            pass
    if not desc:
        desc = ibin_desc.get(ibin_key, '')
    return f'iBin {ibin_key}' + (f'  — {str(desc)[:40]}' if desc else '')


def build_weekly_ibin_pareto(week_str: str, week_runs: list[dict],
                              top_n: int = 15, cfg: dict | None = None) -> str:
    """Horizontal bar pareto of iBin fail% for all runs in one period."""
    if not HAVE_MPL:
        return ''

    # Prefer bin_summary_rows (new format) → func_bin_rows → bin_fail_rows
    def _get_rows(run):
        bd = run.get('bin_data') or {}
        return (bd.get('bin_summary_rows') or
                bd.get('func_bin_rows') or
                bd.get('bin_fail_rows') or [])

    valid = [r for r in week_runs if _get_rows(r)]
    if not valid:
        return ''

    # Aggregate: sum fail_count per ibin across all runs in the week
    ibin_counts: dict[str, int]   = {}
    ibin_total:  dict[str, int]   = {}   # total die across runs that have this bin
    ibin_desc:   dict[str, str]   = {}

    for run in valid:
        for row in _get_rows(run):
            k = str(row.get('ibin', '')).strip()
            if not k:
                continue
            cnt = row.get('fail_count') or 0
            ibin_counts[k]  = ibin_counts.get(k, 0) + cnt
            desc = row.get('desc') or row.get('fail_bucket') or ''
            if desc:
                ibin_desc[k] = desc

    if not ibin_counts:
        return ''

    total_fails = sum(ibin_counts.values()) or 1
    sorted_ibins = sorted(ibin_counts, key=lambda k: ibin_counts[k], reverse=True)[:top_n]

    # Pareto: horizontal bar + cumulative % line
    n     = len(sorted_ibins)
    counts = [ibin_counts[k] for k in sorted_ibins]
    cum_pcts = []
    running = 0
    for c in counts:
        running += c
        cum_pcts.append(running / total_fails * 100)

    bar_pcts  = [c / total_fails * 100 for c in counts]
    y_pos = np.arange(n)

    fig, ax_bar = plt.subplots(figsize=(10, max(4, n * 0.52)))
    ax_cum = ax_bar.twinx()

    colors = [_WEEK_COLORS[i % len(_WEEK_COLORS)] for i in range(n)]
    bars = ax_bar.barh(y_pos, bar_pcts, color=colors, alpha=0.82, edgecolor='white', linewidth=0.4)

    for bar, pct, cnt in zip(bars, bar_pcts, counts):
        if pct >= 0.05:
            ax_bar.text(bar.get_width() + 0.05, bar.get_y() + bar.get_height() / 2,
                        f'{pct:.2f}%  (n={cnt:,})',
                        va='center', ha='left', fontsize=7.5)

    ax_cum.plot(cum_pcts, y_pos, marker='o', linewidth=2, color='#2c3e50',
                markersize=4, alpha=0.9, label='Cumulative %')
    ax_cum.axvline(80, color='#e74c3c', linestyle='--', linewidth=1.2, alpha=0.8, label='80%')

    ylabels = [_ibin_label(k, ibin_desc, cfg) for k in sorted_ibins]

    ax_bar.set_yticks(y_pos)
    ax_bar.set_yticklabels(ylabels, fontsize=8)
    ax_bar.invert_yaxis()
    ax_bar.set_xlabel('Fail (%)')
    ax_bar.set_title(
        f'Interface Bin Fail Pareto  \u2014  {week_str}  '
        f'({len(valid)} run{"s" if len(valid) != 1 else ""},'
        f' {sum(counts):,} total failures)',
        fontsize=12, weight='bold'
    )

    ax_cum.set_ylabel('Cumulative Fail (%)')
    ax_cum.set_ylim(0, 110)
    ax_cum.legend(fontsize=8, loc='lower right')
    ax_bar.grid(axis='x', linestyle='--', alpha=0.4)
    fig.tight_layout()
    return _fig_b64(fig)


def build_weekly_yield_trend(weeks_data: dict, metric: str = 'ff_df') -> str:
    """
    Line chart: FF+DF yield (bin 1/2/3/4) per week, one point = median of week.
    metric: 'ff_df' uses bin '1/2/3/4', 'ff' uses '1/2'.
    """
    if not HAVE_MPL:
        return ''

    bin_key = '1/2/3/4' if metric == 'ff_df' else '1/2'
    label   = 'FF+DF Yield (Bin 1/2/3/4)' if metric == 'ff_df' else 'FF Yield (Bin 1/2)'

    week_labels = []
    medians     = []
    all_vals    = []

    for wk, runs in weeks_data.items():
        vals = []
        for r in runs:
            bd = r.get('bin_data') or {}
            for row in bd.get('yield_rows', []):
                if row.get('bin') == bin_key and row.get('yield_pct') is not None:
                    vals.append(row['yield_pct'])
        if vals:
            week_labels.append(wk)
            med = sorted(vals)[len(vals) // 2]
            medians.append(med)
            all_vals.append(vals)

    if not medians:
        return ''

    n   = len(week_labels)
    x   = np.arange(n)
    fig, ax = plt.subplots(figsize=(max(7, n * 1.1), 4))

    ax.plot(x, medians, marker='o', linewidth=2.4, color='#2980b9',
            markersize=8, label='Weekly median', zorder=4)

    for xi, (med, vals) in enumerate(zip(medians, all_vals)):
        if len(vals) > 1:
            ax.vlines(xi, min(vals), max(vals), color='#aaa', linewidth=1.5,
                      zorder=2, label='Range' if xi == 0 else '')
        ax.text(xi, med + 0.3, f'{med:.1f}%', ha='center', va='bottom',
                fontsize=8, weight='bold', color='#2980b9')

    ax.set_xticks(x)
    ax.set_xticklabels(week_labels, rotation=30, ha='right', fontsize=9)
    ax.set_ylabel('Yield (%)')
    ax.set_ylim(0, 105)
    ax.set_title(f'{label} — Weekly Trend', fontsize=12, weight='bold')
    ax.grid(axis='y', linestyle='--', alpha=0.4)
    ax.legend(fontsize=8)
    fig.tight_layout()
    return _fig_b64(fig)


def build_weekly_top_fails_trend(weeks_data: dict, top_n: int = 8,
                                  cfg: dict | None = None) -> str:
    """
    Stacked area / grouped bar: top failing iBins per period as % of total fails.
    """
    if not HAVE_MPL:
        return ''

    def _get_rows(run):
        bd = run.get('bin_data') or {}
        return (bd.get('bin_summary_rows') or
                bd.get('func_bin_rows') or
                bd.get('bin_fail_rows') or [])

    # Find global top_n iBins by total fail count
    global_counts: dict[str, int] = {}
    for runs in weeks_data.values():
        for r in runs:
            for row in _get_rows(r):
                k = str(row.get('ibin', '')).strip()
                if k:
                    global_counts[k] = global_counts.get(k, 0) + (row.get('fail_count') or 0)

    top_bins = sorted(global_counts, key=lambda k: global_counts[k], reverse=True)[:top_n]
    if not top_bins:
        return ''

    week_labels = list(weeks_data.keys())
    n_weeks = len(week_labels)
    n_bins  = len(top_bins)

    # Build matrix: weeks × bins  (fail % of total fails that week)
    matrix = np.zeros((n_weeks, n_bins))
    for wi, wk in enumerate(week_labels):
        bin_cnts: dict[str, int] = {}
        for r in weeks_data[wk]:
            for row in _get_rows(r):
                k = str(row.get('ibin', '')).strip()
                if k:
                    bin_cnts[k] = bin_cnts.get(k, 0) + (row.get('fail_count') or 0)
        total = sum(bin_cnts.values()) or 1
        for bi, bk in enumerate(top_bins):
            matrix[wi, bi] = bin_cnts.get(bk, 0) / total * 100

    x = np.arange(n_weeks)
    bar_w = 0.65

    fig, ax = plt.subplots(figsize=(max(8, n_weeks * 1.1), 5))
    bottoms = np.zeros(n_weeks)
    for bi, bk in enumerate(top_bins):
        vals = matrix[:, bi]
        lbl  = _ibin_label(bk, {}, cfg)
        ax.bar(x, vals, bar_w, bottom=bottoms,
               label=lbl, color=_WEEK_COLORS[bi % len(_WEEK_COLORS)],
               alpha=0.82, edgecolor='white', linewidth=0.3)
        for wi, (v, b) in enumerate(zip(vals, bottoms)):
            if v >= 2.0:
                ax.text(wi, b + v / 2, f'{v:.1f}%',
                        ha='center', va='center', fontsize=6.5,
                        color='white', weight='bold')
        bottoms += vals

    ax.set_xticks(x)
    ax.set_xticklabels(week_labels, rotation=30, ha='right', fontsize=9)
    ax.set_ylabel('% of Total Failures')
    ax.set_title(f'Top {n_bins} iBin Fail Mix — Week-over-Week', fontsize=12, weight='bold')
    ax.legend(fontsize=7.5, bbox_to_anchor=(1.01, 1), loc='upper left', borderaxespad=0)
    ax.grid(axis='y', linestyle='--', alpha=0.4)
    fig.tight_layout()
    return _fig_b64(fig)


# ---------------------------------------------------------------------------
# 5.  HTML generation
# ---------------------------------------------------------------------------

def generate_report(dash_path: Path, runs_data: list[dict],
                    output_path: Path, weeks_back: int = 0,
                    interval: str = 'weekly',
                    cfg: dict | None = None) -> None:
    """
    Generate the pareto report HTML.
    weeks_back=0 → include all runs; weeks_back=N → last N periods only.
    interval: 'daily' | 'weekly' | 'bi-weekly' | 'monthly'
    cfg: product config dict (from trend_chart.load_product_config) for ibin names.
    """
    # Apply period filter
    if weeks_back > 0:
        cutoff_week = _week_start_n_back(weeks_back)
        runs_data = [r for r in runs_data
                     if _period_sort_key(r['week']) >= cutoff_week or r['week'] == 'unknown']

    weeks_data = group_by_week(runs_data)
    n_total = sum(len(v) for v in weeks_data.values())

    sections_html = ''

    # ── Yield trend ──────────────────────────────────────────────────────
    trend_b64 = build_weekly_yield_trend(weeks_data, metric='ff_df')
    if trend_b64:
        sections_html += (
            '<div class="section">'
            f'<h2>&#128200; FF+DF Yield \u2014 {interval.title()} Trend</h2>'
            f'<img class="chart" src="data:image/png;base64,{trend_b64}"/>'
            '</div>'
        )

    trend_ff_b64 = build_weekly_yield_trend(weeks_data, metric='ff')
    if trend_ff_b64:
        sections_html += (
            '<div class="section">'
            f'<h2>&#128200; FF Yield \u2014 {interval.title()} Trend</h2>'
            f'<img class="chart" src="data:image/png;base64,{trend_ff_b64}"/>'
            '</div>'
        )

    # ── Stacked fail-mix chart ────────────────────────────────────────────────
    mix_b64 = build_weekly_top_fails_trend(weeks_data, cfg=cfg)
    if mix_b64:
        sections_html += (
            '<div class="section">'
            f'<h2>&#128203; iBin Fail Mix \u2014 {interval.title()}-over-{interval.title()}</h2>'
            f'<img class="chart" src="data:image/png;base64,{mix_b64}"/>'
            '</div>'
        )

    # ── Per-period pareto charts ──────────────────────────────────────────────
    for week_str, week_runs in weeks_data.items():
        pareto_b64 = build_weekly_ibin_pareto(week_str, week_runs, cfg=cfg)
        period_dt  = _period_sort_key(week_str)
        period_label = (period_dt.strftime('%b %d, %Y') if week_str != 'unknown'
                        else 'Unknown period')
        card_rows  = ''.join(
            f'<li style="font-size:13px;color:#aaa">{_esc(r["name"])}'
            f'{(" — "+_esc(r["ts"])) if r.get("ts") else ""}</li>'
            for r in week_runs
        )
        sections_html += f'''
<div class="section">
  <h2>&#128204; {_esc(week_str)}&ensp;<span style="font-size:18px;color:#7f8c8d;font-weight:normal">
    ({period_label} — {len(week_runs)} run{"s" if len(week_runs)!=1 else ""})</span></h2>
  <ul style="margin:4px 0 10px 18px;padding:0">{card_rows}</ul>
  {'<img class="chart" src="data:image/png;base64,' + pareto_b64 + '"/>' if pareto_b64
   else '<p style="color:#888">No bin data available for this period.</p>'}
</div>'''

    ts_now = datetime.now().strftime('%Y-%m-%d %H:%M')
    html = f'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Weekly Yield Pareto Report</title>
<style>
body{{font-family:Arial,sans-serif;background:#f4f6f8;margin:0;padding:16px 24px}}
h1{{font-size:28px;color:#2c3e50;margin-bottom:4px}}
h2{{font-size:22px;color:#2c3e50;margin:18px 0 8px;padding-bottom:4px;border-bottom:2px solid #dce1e7}}
.subtitle{{font-size:16px;color:#7f8c8d;margin-bottom:20px}}
.dash-link{{font-size:16px;color:#2980b9;margin-bottom:6px;display:block}}
.dash-link a{{color:#2980b9;text-decoration:none}}
.dash-link a:hover{{text-decoration:underline}}
.section{{background:#fff;border-radius:8px;padding:16px 18px;margin-bottom:18px;
  box-shadow:0 1px 4px rgba(0,0,0,.08)}}
.chart{{max-width:100%;height:auto;display:block;margin:8px 0}}
</style>
</head>
<body>
<h1>&#128200; Yield Pareto Report</h1>
<div class="dash-link">Source: <a href="{_esc(str(dash_path.name))}">{_esc(str(dash_path.name))}</a></div>
<div class="subtitle">
  Generated: {ts_now} &nbsp;|&nbsp;
  Interval: <b>{interval.title()}</b> &nbsp;|&nbsp;
  Periods: <b>{len(weeks_data)}</b> &nbsp;|&nbsp;
  Runs: <b>{n_total}</b>
  {f'&nbsp;|&nbsp; Last <b>{weeks_back}</b> period(s)' if weeks_back > 0 else ''}
</div>
{sections_html}
</body>
</html>'''

    output_path.write_text(_wm_inject(html), encoding='utf-8')
    print(f'Wrote weekly report: {output_path}')


def _week_start_n_back(n: int) -> datetime:
    """Return the Monday of the ISO week that is n weeks before the current week."""
    today = datetime.today()
    iso_yr, iso_wk, _ = today.isocalendar()
    current_monday    = today - timedelta(days=today.weekday())
    return current_monday - timedelta(weeks=n - 1)


# ---------------------------------------------------------------------------
# 6.  Update Dashboard.html — REPORT section links
# ---------------------------------------------------------------------------

def update_dashboard_report_links(dash_path: Path, report_path: Path) -> None:
    """Inject a link to report_path into the <!-- REPORT_START/END --> section."""
    from datetime import datetime as _dt
    dash_path   = Path(dash_path)
    report_path = Path(report_path)
    if not dash_path.exists():
        return

    content = dash_path.read_text(encoding='utf-8')

    REPORT_START = '<!-- REPORT_START -->'
    REPORT_END   = '<!-- REPORT_END -->'
    YIELD_END    = '<!-- YIELD_END -->'
    COMPARE_END  = '<!-- COMPARE_END -->'

    # Ensure REPORT section exists
    if REPORT_START not in content:
        anchor = COMPARE_END if COMPARE_END in content else (
                 YIELD_END   if YIELD_END   in content else '</body>')
        insert_after = anchor if anchor != '</body>' else ''
        inject_section = (
            '\n<h2 class="section-header">&#128196; Report</h2>\n'
            + REPORT_START + '\n' + REPORT_END
        )
        if anchor == '</body>':
            content = content.replace('</body>', inject_section + '\n</body>', 1)
        else:
            content = content.replace(anchor, anchor + inject_section, 1)

    try:
        href = os.path.relpath(str(report_path), str(dash_path.parent)).replace('\\', '/')
    except Exception:
        href = report_path.as_uri()

    stem = report_path.stem
    ts   = _dt.now().strftime('%Y-%m-%d %H:%M')

    new_block = (
        f'<div class="run-block" data-stem="{stem}">\n'
        f'<div class="run-header" onclick="toggle(this)">'
        f'<span class="arrow">&#9660;</span> {stem}'
        f'<span class="ts"> - {ts}</span></div>\n'
        f'<div class="run-body">\n'
        f'<a class="run-link report-link" href="{href}" target="_blank">{stem}</a>\n'
        f'</div>\n</div>'
    )

    block_re = re.compile(
        r'<div class="run-block" data-stem="' + re.escape(stem) +
        r'">\s*<div[^>]*>[\s\S]*?</div>\s*</div>', re.MULTILINE)
    if block_re.search(content):
        content = block_re.sub(new_block, content)
    else:
        content = content.replace(REPORT_START, REPORT_START + '\n' + new_block)

    dash_path.write_text(content, encoding='utf-8')
    print(f'Updated {dash_path.name} with report link.')


# ---------------------------------------------------------------------------
# 7.  Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description='Weekly pareto yield report from Dashboard.html')
    p.add_argument('dashboard', help='Path to Dashboard.html')
    p.add_argument('--out', default='', help='Output HTML path (default: next to Dashboard.html)')
    p.add_argument('--weeks', type=int, default=0,
                   help='Limit to last N ISO weeks (0 = all)')
    args = p.parse_args()

    if not HAVE_CR:
        print('ERROR: compare_runs.py not found on sys.path', file=sys.stderr)
        sys.exit(1)
    if not HAVE_MPL:
        print('WARNING: matplotlib not installed — charts will be skipped.')

    dash_path = Path(args.dashboard).resolve()
    if not dash_path.exists():
        print(f'ERROR: Dashboard.html not found: {dash_path}', file=sys.stderr)
        sys.exit(1)

    dash_dir = dash_path.parent
    print(f'Parsing {dash_path} …')
    records = _parse_dashboard(dash_path)
    if not records:
        print('ERROR: No run blocks found.', file=sys.stderr)
        sys.exit(1)

    print(f'Found {len(records)} run(s). Loading bin data …')
    runs_data = load_run_data(records, dash_dir, log=lambda s: print(s, end=''))

    out_path = (Path(args.out).resolve() if args.out
                else dash_dir / 'yield_weekly_report.html')

    print('Generating weekly pareto report …')
    generate_report(dash_path, runs_data, out_path, weeks_back=args.weeks)
    update_dashboard_report_links(dash_path, out_path)

    try:
        os.startfile(str(out_path))
    except Exception:
        pass


if __name__ == '__main__':
    main()
