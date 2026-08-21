"""yield_group_compare.py — Group-based TP comparison (new, independent of CompareFrame).

Loads one-or-more index.html run folders (or imports a Dashboard.html), lets the
user assign each TP to a group (default 2 groups: Group A / Group B, more can
be added), then generates a self-contained interactive HTML report with live
Plotly charts — Yield, Interface Bin, and Functional Bin tabs with drag-to-resize
splitters, threshold/top-N controls, and delta columns.

A single index.html/BinDistribution.html can itself contain multiple test
programs pooled together (distinguishable by the 'program' field on each
lot/wafer row in the report's embedded DATA blob, alongside real per-row
binCounts) — so loading one index.html can add several TP rows, one per
distinct program actually found in its die-level data, each independently
assignable to any group.

Aggregation (per group, before the report is rendered) is counts-based: bin
fail counts and die totals are summed across the TPs assigned to a group,
then percentages are recomputed from the summed counts (never averaged from
each TP's own percentage), so results stay accurate regardless of how many
TPs land in a group or how their die counts differ. SICC/UPM/CDYN values
come from files scoped to the whole run (not per-program), so they're only
attached when a run contributes a single TP; averaged weighted by n_rows
when SICC/UPM/CDYN of multiple whole-runs are pooled into the same group.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

from yield_trend import (
    find_bin_html, parse_bin_html,
    find_group_medians, parse_group_medians,
    find_cdyn_medians, parse_cdyn_medians,
    parse_dashboard, read_xlsx, HAVE_OPENPYXL,
)

# -- palette (matches CompareFrame) -------------------------------------------
BG, BG2, FG, FG2 = '#1a252f', '#2c3e50', '#ecf0f1', '#95a5a6'
ABLU, GRN, AGRN = '#3498db', '#27ae60', '#2ecc71'
DEFAULT_GROUPS = ['Group A', 'Group B']


def _btn(parent, text, cmd, color=ABLU, acolor='#5dade2', width=None):
    kw = {'width': width} if width else {}
    return tk.Button(parent, text=text, command=cmd, bg=color, fg='white',
                      activebackground=acolor, relief='flat', cursor='hand2',
                      font=('Arial', 9), padx=8, pady=3, **kw)


def _lf(parent, text, color=FG2):
    return tk.LabelFrame(parent, text=text, bg=BG, fg=color,
                          font=('Arial', 8, 'bold'), padx=6, pady=4,
                          relief='groove', bd=1)


# ---------------------------------------------------------------------------
# Data discovery / extraction — one record per loaded run
# ---------------------------------------------------------------------------

def _read_bin_data_json(bin_html: Path) -> dict | None:
    """Parse the 'var DATA = {...}' blob embedded in a *_BinDistribution.html."""
    try:
        txt = bin_html.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return None
    m = re.search(r'var DATA\s*=\s*', txt)
    if not m:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(txt, m.end())
        return data
    except Exception:
        return None


def _split_bin_counts_by_program(data: dict) -> dict[str, dict[str, int]]:
    """Sum each lot/wafer row's real binCounts per distinct 'program' value."""
    by_prog: dict[str, dict[str, int]] = {}
    for row in (data.get('rows') or []):
        prog = str(row.get('program') or 'Unknown')
        e = by_prog.setdefault(prog, {})
        for b, c in (row.get('binCounts') or {}).items():
            e[b] = e.get(b, 0) + int(c)
    return by_prog


def _split_fb_counts_by_program(data: dict) -> dict[str, dict[tuple, int]]:
    """Sum each lot/wafer row's real ibToFb (Functional Bin) counts per program."""
    by_prog: dict[str, dict[tuple, int]] = {}
    for row in (data.get('rows') or []):
        prog = str(row.get('program') or 'Unknown')
        e = by_prog.setdefault(prog, {})
        for ib, fbmap in (row.get('ibToFb') or {}).items():
            for fb, c in (fbmap or {}).items():
                key = (ib, fb)
                e[key] = e.get(key, 0) + int(c)
    return by_prog


def _combo_count(bin_counts: dict, bin_key: str) -> int:
    """Sum bin_counts for every individual bin number in a combo key like '1/2/3/4'."""
    return sum(bin_counts.get(n, 0) for n in re.findall(r'\d+', bin_key))


def _read_wm_pat_ib_to_fb(bin_html: Path) -> dict[tuple, int]:
    """Sum real per-wafer Interface->Functional Bin counts from the wafer-map
    '<script id="wm-pat-json">' JSON block. This is a second, independent FB-column
    detection pass (separate from the one that builds DATA.rows[].ibToFb), so it can
    have real data even on runs where DATA.rows[].ibToFb came back empty."""
    try:
        txt = bin_html.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return {}
    m = re.search(r'<script type="application/json" id="wm-pat-json">(.*?)</script>', txt, re.DOTALL)
    if not m:
        return {}
    try:
        wm = json.loads(m.group(1))
    except Exception:
        return {}
    out: dict[tuple, int] = {}
    for wdata in (wm.get('wafers') or {}).values():
        for ib, fbmap in (wdata.get('ibToFb') or {}).items():
            for fb, c in (fbmap or {}).items():
                key = (str(ib), str(fb))
                out[key] = out.get(key, 0) + int(c)
    return out


def _build_tp_bin_data(bin_counts: dict, fb_counts: dict, whole_run_bin_data: dict | None) -> dict:
    """Recompute yield_rows/bin_summary_rows/func_bin_rows from ONE program's real
    bin/FB counts, reusing the whole run's combo definitions and category/
    description/fail-bucket labels as templates — only the counts and
    percentages are specific to this TP."""
    total = sum(bin_counts.values())
    yield_rows = []
    for row in ((whole_run_bin_data or {}).get('yield_rows') or []):
        cnt = _combo_count(bin_counts, row['bin'])
        yield_rows.append({'bin': row['bin'], 'fail_bucket': row.get('fail_bucket', ''),
                            'yield_pct': (cnt / total * 100) if total else None,
                            'expected_pct': row.get('expected_pct')})
    summary_rows = []
    for row in ((whole_run_bin_data or {}).get('bin_summary_rows') or []):
        cnt = bin_counts.get(row['ibin'], 0)
        summary_rows.append({'ibin': row['ibin'], 'cat': row.get('cat', ''),
                              'desc': row.get('desc', ''), 'fail_bucket': row.get('fail_bucket', ''),
                              'fail_count': cnt,
                              'fail_pct': (cnt / total * 100) if total else None})
    fb_label = {(row['ibin'], row['fbin']): row.get('fail_bucket', '')
                for row in ((whole_run_bin_data or {}).get('func_bin_rows') or [])}
    func_bin_rows = [
        {'ibin': ib, 'fbin': fb, 'fail_bucket': fb_label.get((ib, fb), ''),
         'fail_count': cnt, 'fail_pct': (cnt / total * 100) if total else None}
        for (ib, fb), cnt in fb_counts.items()
    ]
    return {'yield_rows': yield_rows, 'bin_summary_rows': summary_rows,
            'bin_fail_rows': [], 'func_bin_rows': func_bin_rows}


def _find_xlsx_in_dir(output_dir: Path):
    candidates = sorted(output_dir.glob('*_out.xlsx'),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def build_tp_records(index_html: Path) -> list[dict]:
    """Return one record per real test program found in this run's die-level data.

    Splits on the 'program' field of each lot/wafer row inside the run's
    *_BinDistribution.html DATA blob, using that row's own real binCounts —
    no reconstruction/approximation needed since the source data already
    carries per-program counts. Falls back to one whole-run record when the
    DATA blob is unavailable or only contains a single program.
    """
    output_dir = index_html.parent
    bin_html = find_bin_html(output_dir)
    data = _read_bin_data_json(bin_html) if bin_html else None
    whole_run_bin_data = parse_bin_html(bin_html) if bin_html else None

    num_die_whole = None
    if HAVE_OPENPYXL:
        xlsx_p = _find_xlsx_in_dir(output_dir)
        if xlsx_p:
            xdata = read_xlsx(xlsx_p)
            num_die_whole = xdata.get('num_die') if xdata else None

    gm_p = find_group_medians(output_dir)
    group_medians = parse_group_medians(gm_p) if gm_p else []
    cdyn_p = find_cdyn_medians(output_dir)
    cdyn_medians = parse_cdyn_medians(cdyn_p) if cdyn_p else []

    by_prog = _split_bin_counts_by_program(data) if data else {}
    fb_by_prog = _split_fb_counts_by_program(data) if data else {}

    if len(by_prog) <= 1:
        tp_name = next(iter(by_prog), None) or output_dir.name
        rbd = whole_run_bin_data
        # Always PREFER the real ibToFb JSON read over parse_bin_html's own
        # func_bin_rows: parse_bin_html can silently fill func_bin_rows from
        # FP_DATA (Fail Pareto), which has no true Interface Bin field and
        # fakes ibin=fbin — that looks "non-empty" but is actually bogus and
        # won't align with other runs' real (ibin, fbin) pairs in the table.
        fb_counts = next(iter(fb_by_prog.values()), {})
        if fb_counts:
            fb_diag = 'DATA.rows[].ibToFb'
        else:
            fb_counts = _read_wm_pat_ib_to_fb(bin_html) if bin_html else {}
            fb_diag = 'WM_PAT wafer-map ibToFb' if fb_counts else (
                'parse_bin_html (no true IB pairing)' if (rbd or {}).get('func_bin_rows') else 'none')
        if fb_counts:
            bin_counts_single = next(iter(by_prog.values()), {})
            total = sum(bin_counts_single.values()) or num_die_whole or 0
            rbd = dict(rbd) if rbd else {}
            # exact (ibin,fbin) pair lookup — only hits when parse_bin_html had real pairs
            fb_label_pair = {(str(row['ibin']), str(row['fbin'])): row.get('fail_bucket', '')
                             for row in (rbd.get('func_bin_rows') or [])}
            # fbin-only lookup covers parse_bin_html's fake ibin=fbin rows from FP_DATA
            fb_label_fbin = {str(row['fbin']): row.get('fail_bucket', '')
                             for row in (rbd.get('func_bin_rows') or [])}
            # ibin-only fallback from bin_summary_rows; use cat when fail_bucket is absent
            ib_label = {str(row['ibin']): (row.get('fail_bucket', '') or row.get('cat', ''))
                        for row in (rbd.get('bin_summary_rows') or [])}
            rbd['func_bin_rows'] = [
                {'ibin': ib, 'fbin': fb,
                 'fail_bucket': (fb_label_pair.get((str(ib), str(fb)), '')
                                 or fb_label_fbin.get(str(fb), '')
                                 or ib_label.get(str(ib), '')),
                 'fail_count': cnt,
                 'fail_pct': (cnt / total * 100) if total else None}
                for (ib, fb), cnt in fb_counts.items()
            ]
        return [{
            'name': output_dir.name, 'tp': tp_name, 'indexHtml': str(index_html),
            'numDie': num_die_whole, 'rawBinData': rbd,
            'groupMedians': group_medians, 'cdynMedians': cdyn_medians, 'fbDiag': fb_diag,
        }]

    # Multiple programs found in this run's die-level data — split for real
    return [{
        'name': f'{output_dir.name} [{prog}]', 'tp': prog, 'indexHtml': str(index_html),
        'numDie': sum(bin_counts.values()),
        'rawBinData': _build_tp_bin_data(bin_counts, fb_by_prog.get(prog, {}), whole_run_bin_data),
        # SICC/UPM/CDYN medians are only recorded per whole run, not per
        # program, so they can't be honestly attributed to just one TP here.
        'groupMedians': [], 'cdynMedians': [],
    } for prog, bin_counts in by_prog.items()]


def resolve_index_html_from_dashboard(dash_dir: Path, index_href: str) -> Path | None:
    """Mirror find_xlsx's href resolution to get the run's index.html path."""
    if not index_href:
        return None
    href = re.sub(r'^file:///', '', index_href).replace('/', os.sep)
    idx = dash_dir / href if not os.path.isabs(href) else Path(href)
    if idx.is_dir():
        idx = idx / 'index.html'
    return idx if idx.exists() else None


# ---------------------------------------------------------------------------
# Group aggregation — counts-based, produces a record shaped exactly like the
# ones _write_interactive_report() already expects (name/data/bin_data/
# upm_data/cdyn_data), so the report renderer needs no changes at all.
# ---------------------------------------------------------------------------

def _aggregate_yield_rows(members: list[dict]) -> list[dict]:
    """Reconstruct per-bin die counts from each run's yield_pct * numDie, sum, re-percent."""
    acc: dict[str, dict] = {}
    total_die = sum(m['numDie'] or 0 for m in members)
    for m in members:
        nd = m['numDie'] or 0
        if not nd:
            continue
        for row in ((m['rawBinData'] or {}).get('yield_rows') or []):
            b, pct = row.get('bin'), row.get('yield_pct')
            if b is None or pct is None:
                continue
            e = acc.setdefault(b, {'count': 0.0, 'expected': row.get('expected_pct'),
                                    'fail_bucket': row.get('fail_bucket', '')})
            e['count'] += pct / 100.0 * nd
    return [{'bin': b, 'fail_bucket': e['fail_bucket'],
             'yield_pct': (e['count'] / total_die * 100) if total_die else None,
             'expected_pct': e['expected']} for b, e in acc.items()]


def _aggregate_bin_counts(members: list[dict], key: str) -> list[dict]:
    """Sum real fail_count (bin_summary_rows / bin_fail_rows already carry counts)."""
    acc: dict[str, dict] = {}
    total_die = sum(m['numDie'] or 0 for m in members)
    for m in members:
        for row in ((m['rawBinData'] or {}).get(key) or []):
            ibin = row.get('ibin')
            if ibin is None:
                continue
            e = acc.setdefault(ibin, {'cat': row.get('cat', ''), 'desc': row.get('desc', ''),
                                       'fail_bucket': row.get('fail_bucket', ''), 'count': 0})
            if row.get('fail_count') is not None:
                e['count'] += row['fail_count']
    return [{'ibin': ibin, 'cat': e['cat'], 'desc': e['desc'], 'fail_bucket': e['fail_bucket'],
             'fail_count': e['count'],
             'fail_pct': (e['count'] / total_die * 100) if total_die else None}
            for ibin, e in acc.items()]


def _aggregate_func_bin_counts(members: list[dict]) -> list[dict]:
    """Sum real fail_count in func_bin_rows, keyed by (ibin, fbin) pair."""
    acc: dict[tuple, dict] = {}
    total_die = sum(m['numDie'] or 0 for m in members)
    for m in members:
        for row in ((m['rawBinData'] or {}).get('func_bin_rows') or []):
            ibin, fbin = row.get('ibin'), row.get('fbin')
            if ibin is None:
                continue
            key = (ibin, fbin)
            e = acc.setdefault(key, {'fail_bucket': row.get('fail_bucket', ''), 'count': 0})
            if not e['fail_bucket'] and row.get('fail_bucket'):
                e['fail_bucket'] = row['fail_bucket']
            if row.get('fail_count') is not None:
                e['count'] += row['fail_count']
    return [{'ibin': ibin, 'fbin': fbin, 'fail_bucket': e['fail_bucket'],
             'fail_count': e['count'],
             'fail_pct': (e['count'] / total_die * 100) if total_die else None}
            for (ibin, fbin), e in acc.items()]


def _aggregate_upm(members: list[dict]) -> list[dict]:
    """Weighted average (by n_rows) of SICC/UPM values across runs, per test."""
    acc: dict[str, dict] = {}
    for m in members:
        for row in (m['groupMedians'] or []):
            test = row.get('test')
            if test is None:
                continue
            e = acc.setdefault(test, {'n': 0.0, 'act': 0.0, 'mult': 0.0, 'upm': 0.0,
                                       'target': row.get('sicc_target')})
            n = row.get('n_rows') or 0
            e['n'] += n
            for src, dst in (('sicc_actual', 'act'), ('multiple', 'mult'), ('upm_pct', 'upm')):
                if row.get(src) is not None:
                    e[dst] += row[src] * n
    out = []
    for test, e in acc.items():
        n = e['n'] or None
        out.append({'test': test, 'n_rows': e['n'],
                     'sicc_actual': (e['act'] / n) if n else None,
                     'sicc_target': e['target'],
                     'multiple':    (e['mult'] / n) if n else None,
                     'upm_pct':     (e['upm'] / n) if n else None})
    return out


def _aggregate_cdyn(members: list[dict]) -> list[dict]:
    """Plain average across runs, per (test, type) — no per-row sample size available."""
    acc: dict[tuple, dict] = {}
    for m in members:
        for row in (m['cdynMedians'] or []):
            key = (row.get('test'), row.get('type'))
            e = acc.setdefault(key, {'act': [], 'ratio': [], 'expected': row.get('expected')})
            if row.get('actual') is not None:
                e['act'].append(row['actual'])
            if row.get('ratio') is not None:
                e['ratio'].append(row['ratio'])
    out = []
    for (test, typ), e in acc.items():
        out.append({'test': test, 'type': typ, 'expected': e['expected'],
                     'actual': (sum(e['act']) / len(e['act'])) if e['act'] else None,
                     'ratio':  (sum(e['ratio']) / len(e['ratio'])) if e['ratio'] else None})
    return out


def build_group_record(group_name: str, members: list[dict]) -> dict:
    """Aggregate a group's member runs into one record shaped for _write_interactive_report()."""
    total_die = sum(m['numDie'] or 0 for m in members)
    yield_rows = _aggregate_yield_rows(members)
    summary_rows = _aggregate_bin_counts(members, 'bin_summary_rows')
    fail_rows = [] if summary_rows else _aggregate_bin_counts(members, 'bin_fail_rows')
    func_bin_rows = _aggregate_func_bin_counts(members)
    bin_data = ({'yield_rows': yield_rows, 'bin_summary_rows': summary_rows,
                 'bin_fail_rows': fail_rows, 'func_bin_rows': func_bin_rows}
                if (yield_rows or summary_rows or fail_rows or func_bin_rows) else None)
    ts = ', '.join(m['tp'] for m in members)
    return {
        'name': group_name, 'ts': f'{len(members)} run(s): {ts}',
        'index_href': '', 'xlsx_path': '',
        'data': {'num_die': total_die, 'col_headers': ['Yield Loss'],
                  'groups': [], 'totals': None, 'col_is_pct': [True]} if total_die else None,
        'bin_data': bin_data,
        'upm_data': _aggregate_upm(members),
        'cdyn_data': _aggregate_cdyn(members),
    }

# Interactive report — self-contained HTML, JS does all aggregation + Plotly
# ---------------------------------------------------------------------------

# Two levels up from yld/ → shared/library/
_PLOTLY_JS_PATH = Path(__file__).resolve().parent.parent.parent / 'shared' / 'library' / 'plotly-cartesian.min.js'


def _load_plotly_js() -> str:
    if _PLOTLY_JS_PATH.exists():
        return f'<script>{_PLOTLY_JS_PATH.read_text(encoding="utf-8")}</script>'
    return '<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>'


def _make_serializable(records: list[dict]) -> list[dict]:
    """Flatten rawBinData into top-level fields so every value is JSON-native."""
    out = []
    for rec in records:
        rbd = rec.get('rawBinData') or {}
        out.append({
            'tp': rec.get('tp', ''),
            'name': rec.get('name', ''),
            'numDie': rec.get('numDie') or 0,
            'yield_rows': rbd.get('yield_rows') or [],
            'bin_summary_rows': rbd.get('bin_summary_rows') or [],
            'bin_fail_rows': rbd.get('bin_fail_rows') or [],
            'func_bin_rows': rbd.get('func_bin_rows') or [],
            'groupMedians': rec.get('groupMedians') or [],
            'cdynMedians': rec.get('cdynMedians') or [],
        })
    return out


# Placeholders replaced at write time: __PLOTLY_TAG__ __GC_RECORDS__ __GC_ASSIGNMENTS__ __GC_GROUPS__
_GC_HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Test Program Comparison</title>
__PLOTLY_TAG__
<!-- NEW TEMPLATE v2 -->
<style>
*{box-sizing:border-box}
body{font-family:Arial,sans-serif;margin:0;background:#f0f2f5;color:#2c3e50}
#ctrl{background:#1e2d3d;color:#ecf0f1;padding:12px 20px;border-bottom:3px solid #2980b9}
#ctrl h2{margin:0 0 8px;font-size:16px;color:#5dade2;letter-spacing:.3px}
#tp-table{width:100%;border-collapse:collapse}
#tp-table td{padding:3px 8px;font-size:12px;font-family:Consolas,monospace}
#tp-table tr:nth-child(even){background:rgba(255,255,255,.06)}
#tp-table tr:hover{background:rgba(93,173,226,.15)}
#tp-table select{background:#0d1b26;color:#ecf0f1;border:1px solid #3498db;
  padding:2px 4px;border-radius:3px;font-size:12px}
.grp-inp{background:#0d1b26;color:#ecf0f1;border:1px solid #5dade2;
  padding:3px 7px;border-radius:3px;width:110px;font-size:12px}
.cbtn{background:#2980b9;color:#fff;border:none;padding:4px 12px;border-radius:4px;
  cursor:pointer;font-size:12px;font-weight:bold;transition:background .15s}
.cbtn:hover{background:#3498db}
.cbtn.grn{background:#1e8449}.cbtn.grn:hover{background:#27ae60}
.tab-bar{background:#fff;padding:0 20px;border-bottom:2px solid #d5d8dc;display:flex;gap:0;
  box-shadow:0 2px 4px rgba(0,0,0,.07)}
.tab-btn{padding:10px 20px;cursor:pointer;border:none;background:none;font-size:13px;
  font-weight:600;color:#7f8c8d;border-bottom:3px solid transparent;margin-bottom:-2px;
  transition:color .15s,border-color .15s}
.tab-btn:hover{color:#2c3e50}
.tab-btn.active{color:#2980b9;border-bottom-color:#2980b9}
.tab-panel{display:none;padding:16px 20px}
.tab-panel.active{display:block}
.two-col{display:flex;gap:0;align-items:stretch;width:100%;min-height:500px;max-height:calc(100vh - 220px);resize:vertical;overflow:auto}
.two-col>.card{overflow:auto;box-sizing:border-box;min-width:80px}
.two-col>.card:first-child{flex:0 0 45%;width:45%}
.two-col>.card:last-child{flex:1 1 0;display:flex;flex-direction:column}
.splitter{width:20px;flex:0 0 20px;cursor:col-resize;background:#dde3ea;
  border-left:1px solid #c8d0da;border-right:1px solid #c8d0da;
  transition:background .15s;align-self:stretch;
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px}
.splitter:hover,.splitter.dragging{background:#c8d6e0}
.spl-btn{width:16px;height:16px;padding:0;border:1px solid #aaa;border-radius:2px;
  background:#fff;color:#555;font-size:9px;cursor:pointer;display:flex;
  align-items:center;justify-content:center;line-height:1;flex-shrink:0}
.spl-btn:hover{background:#2980b9;color:#fff;border-color:#2980b9}
.card{background:#fff;border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.09);
  padding:16px;margin-bottom:14px;box-sizing:border-box}
h3{font-size:13px;font-weight:700;color:#1a252f;margin:0 0 10px;
  padding-bottom:6px;border-bottom:2px solid #ebedef;text-transform:uppercase;
  letter-spacing:.5px}
/* table wrapper — no internal scroll; table grows to fit all rows */
.tbl-wrap{width:100%;overflow-x:auto}
.cmp-tbl{border-collapse:collapse;font-size:14px;width:100%;min-width:max-content}
.cmp-tbl th{background:#34495e;color:#ecf0f1;padding:6px 10px;text-align:left;
  white-space:nowrap;font-size:13px}
.cmp-tbl td{padding:4px 10px;border-bottom:1px solid #eee;white-space:nowrap}
.cmp-tbl tr:hover td{background:#f9f9f9!important}
.cmp-tbl tbody tr.sel td{background:#d6eaf8!important;outline:2px solid #2980b9;}
td.num{text-align:left;font-variant-numeric:tabular-nums}
/* ---- ctrl ---- */
input[type=range]{accent-color:#3498db;width:130px;vertical-align:middle}
input[type=number]{background:#0d1b26;color:#ecf0f1;border:1px solid #5dade2;
  padding:3px 5px;border-radius:3px;width:60px;font-size:12px}
.ctrl-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:9px}
.ctrl-lbl{font-size:11px;color:#95a5a6}
.chart-div{flex:1;min-height:200px;overflow:hidden;position:relative}
.chart-resizer{position:absolute;bottom:0;right:0;width:18px;height:18px;cursor:nwse-resize;
  background:linear-gradient(135deg,transparent 40%,#a0adb8 40%,#a0adb8 55%,transparent 55%,
    transparent 65%,#a0adb8 65%,#a0adb8 80%,transparent 80%);
  z-index:10;border-radius:0 0 4px 0}
.chart-resizer:hover{background:linear-gradient(135deg,transparent 40%,#2980b9 40%,#2980b9 55%,transparent 55%,
    transparent 65%,#2980b9 65%,#2980b9 80%,transparent 80%)}
footer{text-align:center;color:#aaa;font-size:11px;margin:20px 0 8px;
  padding-top:8px;border-top:1px solid #e0e0e0}
.search-inp{width:100%;padding:5px 8px;margin-bottom:8px;border:1px solid #bdc3c7;
  border-radius:4px;font-size:12px;box-sizing:border-box;color:#2c3e50}
.search-inp:focus{outline:none;border-color:#3498db}
.cmp-tbl th.sortable{cursor:pointer;user-select:none;position:relative;padding-right:22px!important}
.cmp-tbl th.sortable::after{content:' \u2195';font-size:10px;opacity:0.5;position:absolute;right:5px}
.cmp-tbl th.sort-asc::after{content:' \u25b2';opacity:1}
.cmp-tbl th.sort-desc::after{content:' \u25bc';opacity:1}
</style>
</head>
<body>
<div id="ctrl">
  <h2>&#128202; Test Program Comparison</h2>
  <div id="groups-row" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px"></div>
  <button class="cbtn" onclick="addGroup()">+ Add Group</button>
  <div style="margin-top:8px;max-height:190px;overflow-y:auto">
    <table id="tp-table"></table>
  </div>
  <div class="ctrl-row">
    <span class="ctrl-lbl">Pareto threshold:</span>
    <input type="range" id="threshold" min="0" max="5" step="0.1" value="0.1"
      oninput="document.getElementById('thr-val').textContent=parseFloat(this.value).toFixed(1)+'%';gcDebounce();">
    <span id="thr-val" style="font-size:12px;width:34px">0.1%</span>
    <span class="ctrl-lbl" style="margin-left:8px">Top N bars:</span>
    <input type="number" id="topn" value="20" min="1" max="500" oninput="gcDebounce()">
    <span class="ctrl-lbl" style="margin-left:8px">Top fail bins (yield):</span>
    <input type="number" id="topfail" value="16" min="1" max="200" oninput="gcDebounce()">
    <button class="cbtn grn" style="margin-left:6px" onclick="applyGroups()">&#9654; Refresh</button>
  </div>
</div>

<div class="tab-bar">
  <button class="tab-btn active" onclick="showTab('yield',this)">Yield</button>
  <button class="tab-btn" onclick="showTab('ibin',this)">Interface Bin</button>
  <button class="tab-btn" onclick="showTab('fbin',this)">Functional Bin</button>
</div>

<div id="tab-yield" class="tab-panel active">
  <div class="two-col">
    <div class="card">
      <h3>Yield Table</h3>
      <input type="text" class="search-inp" id="search-yield" placeholder="Filter rows…" oninput="filterTable('table-yield',this.value)">
      <div class="tbl-wrap"><div id="table-yield"></div></div>
    </div>
    <div class="splitter" onmousedown="startSplit(event,this)">
      <button class="spl-btn" onmousedown="event.stopPropagation()" onclick="togglePanel(this,'left')" title="Hide/show table">&#9668;</button>
      <button class="spl-btn" onmousedown="event.stopPropagation()" onclick="togglePanel(this,'right')" title="Hide/show chart">&#9658;</button>
    </div>
    <div class="card">
      <h3>Yield &amp; Fail Chart</h3>
      <div id="chart-yield" class="chart-div">
        <div class="chart-resizer" onmousedown="startHResize(event,this)" title="Drag to resize chart"></div>
      </div>
    </div>
  </div>
</div>
<div id="tab-ibin" class="tab-panel">
  <div class="two-col">
    <div class="card">
      <h3>Bin Fail Summary <small style="font-weight:normal;color:#888">(click row to highlight)</small></h3>
      <input type="text" class="search-inp" id="search-ibin" placeholder="Filter rows…" oninput="filterTable('table-ibin',this.value)">
      <div class="tbl-wrap"><div id="table-ibin"></div></div>
    </div>
    <div class="splitter" onmousedown="startSplit(event,this)">
      <button class="spl-btn" onmousedown="event.stopPropagation()" onclick="togglePanel(this,'left')" title="Hide/show table">&#9668;</button>
      <button class="spl-btn" onmousedown="event.stopPropagation()" onclick="togglePanel(this,'right')" title="Hide/show chart">&#9658;</button>
    </div>
    <div class="card">
      <h3>Interface Bin Pareto</h3>
      <div id="chart-ibin" class="chart-div">
        <div class="chart-resizer" onmousedown="startHResize(event,this)" title="Drag to resize chart"></div>
      </div>
    </div>
  </div>
</div>
<div id="tab-fbin" class="tab-panel">
  <div class="two-col">
    <div class="card">
      <h3>Functional Bin Table <small style="font-weight:normal;color:#888">(click row to highlight)</small></h3>
      <input type="text" class="search-inp" id="search-fbin" placeholder="Filter rows…" oninput="filterTable('table-fbin',this.value)">
      <div class="tbl-wrap"><div id="table-fbin"></div></div>
    </div>
    <div class="splitter" onmousedown="startSplit(event,this)">
      <button class="spl-btn" onmousedown="event.stopPropagation()" onclick="togglePanel(this,'left')" title="Hide/show table">&#9668;</button>
      <button class="spl-btn" onmousedown="event.stopPropagation()" onclick="togglePanel(this,'right')" title="Hide/show chart">&#9658;</button>
    </div>
    <div class="card">
      <h3>Functional Bin Pareto</h3>
      <div id="chart-fbin" class="chart-div">
        <div class="chart-resizer" onmousedown="startHResize(event,this)" title="Drag to resize chart"></div>
      </div>
    </div>
  </div>
</div>

<footer>Pant, Sujit N &mdash; GEMS FTE</footer>

<script>
var GC_RECORDS = __GC_RECORDS__;
var GC_ASSIGNMENTS = __GC_ASSIGNMENTS__;
var GC_GROUPS = __GC_GROUPS__;

// debounce so slider/topN don't fire on every pixel
var _gcTimer=null;
function gcDebounce(){clearTimeout(_gcTimer);_gcTimer=setTimeout(applyGroups,280);}

/* ---- panel toggle (arrow buttons in splitter) ---- */
function togglePanel(btn,side){
  var spl=btn.closest('.splitter');
  var left=spl.previousElementSibling,right=spl.nextElementSibling;
  if(side==='left'){
    var hide=left.style.display!=='none';
    left.style.display=hide?'none':'';
    if(!hide){left.style.flex='0 0 45%';left.style.width='45%';}
    btn.textContent=hide?'\u25BA':'\u25C4';
  }else{
    var hide=right.style.display!=='none';
    right.style.display=hide?'none':'';
    btn.textContent=hide?'\u25C4':'\u25BA';
  }
  setTimeout(function(){
    var cd=right.querySelector('.chart-div');
    if(cd&&cd.id)try{Plotly.Plots.resize(cd);}catch(x){}
  },50);
}
/* ---- drag-to-resize chart (diagonal grip) ---- */
function startHResize(e,grip){
  e.preventDefault();
  var cd=grip.parentElement;
  if(!cd||!cd.classList.contains('chart-div')) return;
  var startX=e.clientX,startY=e.clientY;
  var startW=cd.getBoundingClientRect().width,startH=cd.getBoundingClientRect().height;
  function onMove(ev){
    var w=Math.max(200,startW+(ev.clientX-startX));
    var h=Math.max(150,startH+(ev.clientY-startY));
    cd.style.flex='0 0 auto';cd.style.width=w+'px';cd.style.height=h+'px';
    if(cd.id)try{Plotly.Plots.resize(cd);}catch(x){}
  }
  function onUp(){
    document.removeEventListener('mousemove',onMove);
    document.removeEventListener('mouseup',onUp);
    if(cd.id)try{Plotly.Plots.resize(cd);}catch(x){}
  }
  document.addEventListener('mousemove',onMove);
  document.addEventListener('mouseup',onUp);
}
/* ---- drag-to-resize splitter ---- */
function startSplit(e,spl){
  e.preventDefault();
  var left=spl.previousElementSibling,right=spl.nextElementSibling;
  var container=spl.parentElement;
  var startX=e.clientX,startW=left.getBoundingClientRect().width;
  spl.classList.add('dragging');
  function onMove(ev){
    var totalW=container.getBoundingClientRect().width-6;
    var newW=Math.max(80,Math.min(totalW-80,startW+(ev.clientX-startX)));
    left.style.flex='0 0 '+newW+'px';left.style.width=newW+'px';
    // resize any Plotly chart inside right panel
    var cd=right.querySelector('.chart-div');if(cd&&cd.id)try{Plotly.Plots.resize(cd);}catch(x){}
  }
  function onUp(){
    spl.classList.remove('dragging');
    document.removeEventListener('mousemove',onMove);
    document.removeEventListener('mouseup',onUp);
    var cd=right.querySelector('.chart-div');if(cd&&cd.id)try{Plotly.Plots.resize(cd);}catch(x){}
  }
  document.addEventListener('mousemove',onMove);
  document.addEventListener('mouseup',onUp);
}

var COLORS=['#3498db','#e74c3c','#2ecc71','#f39c12','#9b59b6','#1abc9c','#e67e22','#34495e','#16a085','#c0392b'];
var FAIL_COLORS=['#e74c3c','#e67e22','#f39c12','#2ecc71','#1abc9c','#3498db','#9b59b6','#e8177d','#95a5a6','#34495e'];
var LINE_COLORS=['#1a73e8','#e53935','#2e7d32','#f57c00'];
var KEY_BINS=['1/2/3/4','1/2'];
var KEY_BIN_TITLES={'1/2/3/4':'FF+DF (Bin 1/2/3/4)','1/2':'FF (Bin 1/2)'};

function esc(s){var d=document.createElement('div');d.textContent=(s==null?'':String(s));return d.innerHTML;}
function binAllGood(b){var ns=(String(b).match(/\\d+/g)||[]);return ns.length&&ns.every(function(n){return parseInt(n)<=4;});}
function pctClass(s){
  var n=parseFloat(s); if(isNaN(n)||s==null)return '';
  if(n>5) return 'p-hi'; if(n>2) return 'p-md'; if(n>0.5) return 'p-lo'; if(n>0) return 'p-ok';
  return '';
}

/* ---- JS aggregation mirrors ---- */
function aggYield(members){
  var td=members.reduce(function(s,m){return s+(m.numDie||0);},0),acc={};
  members.forEach(function(m){
    var nd=m.numDie||0;if(!nd)return;
    (m.yield_rows||[]).forEach(function(r){
      if(r.bin==null||r.yield_pct==null)return;
      if(!acc[r.bin])acc[r.bin]={count:0,exp:r.expected_pct,fb:r.fail_bucket||''};
      acc[r.bin].count+=r.yield_pct/100*nd;
    });
  });
  return Object.keys(acc).map(function(b){
    return{bin:b,fail_bucket:acc[b].fb,yield_pct:td?acc[b].count/td*100:null,expected_pct:acc[b].exp};
  });
}

function aggBin(members,key){
  var td=members.reduce(function(s,m){return s+(m.numDie||0);},0),acc={};
  members.forEach(function(m){
    (m[key]||[]).forEach(function(r){
      if(r.ibin==null)return;
      if(!acc[r.ibin])acc[r.ibin]={cat:r.cat||'',desc:r.desc||'',fb:r.fail_bucket||'',count:0};
      if(r.fail_count!=null)acc[r.ibin].count+=r.fail_count;
    });
  });
  return Object.keys(acc).map(function(ib){var e=acc[ib];
    return{ibin:ib,cat:e.cat,desc:e.desc,fail_bucket:e.fb,fail_count:e.count,
           fail_pct:td?e.count/td*100:null};
  });
}

function aggFbin(members){
  var td=members.reduce(function(s,m){return s+(m.numDie||0);},0),acc={};
  members.forEach(function(m){
    (m.func_bin_rows||[]).forEach(function(r){
      if(r.ibin==null)return;
      var k=r.ibin+'|'+r.fbin;
      if(!acc[k])acc[k]={ib:r.ibin,fb:r.fbin,bucket:r.fail_bucket||'',count:0};
      if(!acc[k].bucket&&r.fail_bucket)acc[k].bucket=r.fail_bucket;
      if(r.fail_count!=null)acc[k].count+=r.fail_count;
    });
  });
  return Object.values(acc).map(function(e){
    return{ibin:e.ib,fbin:e.fb,fail_bucket:e.bucket,fail_count:e.count,
           fail_pct:td?e.count/td*100:null};
  });
}

/* ---- group / UI management ---- */
function renameGroup(idx){
  var inp=document.querySelectorAll('.grp-inp')[idx];
  var nv=inp.value.trim();if(!nv||nv===GC_GROUPS[idx])return;
  var old=GC_GROUPS[idx];GC_GROUPS[idx]=nv;inp.dataset.orig=nv;
  document.querySelectorAll('#tp-table select').forEach(function(sel){
    Array.from(sel.options).forEach(function(o){if(o.value===old){o.value=nv;o.textContent=nv;}});
    if(sel.value===old)sel.value=nv;
  });
  applyGroups();
}
function addGroup(){
  var n=GC_GROUPS.length+1,name='Group '+String.fromCharCode(64+n);
  while(GC_GROUPS.indexOf(name)>=0)name='Group '+(++n);
  GC_GROUPS.push(name);renderGroupsRow();
  document.querySelectorAll('#tp-table select').forEach(function(sel){
    var opt=document.createElement('option');opt.value=name;opt.textContent=name;
    sel.insertBefore(opt,sel.lastElementChild);
  });
}
function renderGroupsRow(){
  var row=document.getElementById('groups-row');row.innerHTML='';
  GC_GROUPS.forEach(function(g,i){
    var inp=document.createElement('input');
    inp.className='grp-inp';inp.value=g;inp.dataset.orig=g;
    inp.onblur=function(){renameGroup(i);};
    inp.onkeydown=function(e){if(e.key==='Enter')renameGroup(i);};
    row.appendChild(inp);
  });
}
function renderTpTable(){
  var tbl=document.getElementById('tp-table');tbl.innerHTML='';
  GC_RECORDS.forEach(function(rec,i){
    var tr=document.createElement('tr');
    var td1=document.createElement('td');
    td1.textContent=rec.tp+'  ('+rec.name+', die='+rec.numDie+')';
    var td2=document.createElement('td');td2.style.width='155px';
    var sel=document.createElement('select');sel.id='assign-'+i;
    GC_GROUPS.forEach(function(g){
      var o=document.createElement('option');o.value=g;o.textContent=g;
      if(g===GC_ASSIGNMENTS[i])o.selected=true;sel.appendChild(o);
    });
    var excl=document.createElement('option');excl.value='(exclude)';excl.textContent='(exclude)';
    if(GC_ASSIGNMENTS[i]==='(exclude)')excl.selected=true;
    sel.appendChild(excl);
    sel.onchange=function(){applyGroups();};
    td2.appendChild(sel);tr.appendChild(td1);tr.appendChild(td2);tbl.appendChild(tr);
  });
}
function showTab(id,btn){
  document.querySelectorAll('.tab-panel').forEach(function(p){p.classList.remove('active');p.style.display='none';});
  document.querySelectorAll('.tab-btn').forEach(function(b){b.classList.remove('active');});
  var panel=document.getElementById('tab-'+id);panel.classList.add('active');panel.style.display='block';
  btn.classList.add('active');
}
function buildGroupMap(){
  var map={};
  GC_RECORDS.forEach(function(rec,i){
    var sel=document.getElementById('assign-'+i);if(!sel)return;
    var g=sel.value;if(g==='(exclude)')return;
    if(!map[g])map[g]=[];map[g].push(rec);
  });
  return map;
}

/* ---- exact CSS from static report ---- */
var ID_COLORS=['#2980b9','#27ae60','#e74c3c','#f39c12','#8e44ad','#16a085','#d35400','#2c3e50','#c0392b','#1abc9c'];
var CAT_PALETTE=['#dbeeff','#e0f5e0','#fef3cd','#fde0d0','#ece0f8','#d0f4f4','#fce4ec','#e8f5e9','#fff3e0','#e3f2fd','#f3e5f5','#e8eaf6'];

/* cell highlight: bold-red when |v - mean| > 10 pp */
function cellHl(v,rowVals,extraStyle){
  var nums=rowVals.filter(function(x){return x!=null;});
  if(v==null)return '<td class="num" style="'+(extraStyle||'')+'">';
  var alert=nums.length>=2&&Math.abs(v-nums.reduce(function(a,b){return a+b;},0)/nums.length)>10;
  var st=(extraStyle||'')+(alert?'color:#c0392b;font-weight:bold;':'');
  return '<td class="num" style="'+st+'">';
}

/* delta td */
function deltaTd(v,base,bg,invert){
  if(base!=null&&v!=null){
    var d=v-base,sign=d>0?'+':'';
    var c=d===0?'#555':(d>0?(invert?'#27ae60':'#c0392b'):(invert?'#c0392b':'#27ae60'));
    return '<td class="num" style="color:'+c+';font-weight:bold'+(bg?';background:'+bg:'')+'">'+sign+d.toFixed(2)+'%</td>';
  }
  return '<td class="num"'+(bg?' style="background:'+bg+'"':'')+'>&#8212;</td>';
}

/* run-header th with ID_COLOR */
function runTh(name,ri,extra){
  return '<th style="background:'+ID_COLORS[ri%ID_COLORS.length]+';color:#fff;font-weight:bold;padding:5px 8px'+(extra?';'+extra:'')+'">'+esc(name)+'</th>';
}
/* delta-header th */
function deltaTh(a,b){
  return '<th style="background:#34495e;color:#fff;font-weight:bold;padding:5px 8px;font-size:11px">\u0394 '+esc(a)+'<br>vs '+esc(b)+'</th>';
}

/* ---- YIELD TABLE (exact replica of build_rdnd_table_html) ---- */
function buildYieldTable(groups,byG,binOrder){
  var hdr='<th>BIN</th><th>Fail Bucket</th><th>Expected (%)</th>';
  groups.forEach(function(g,ri){hdr+=runTh(g,ri);});
  if(groups.length>=2){
    for(var ri=1;ri<groups.length;ri++){
      hdr+=deltaTh(groups[ri],groups[0]);
      if(ri>=2)hdr+=deltaTh(groups[ri],groups[ri-1]);
    }
  }
  var rows='';
  (binOrder||[]).forEach(function(b){
    var fb='',exp=null;
    groups.forEach(function(g){var r=byG[g].find(function(x){return x.bin===b;});if(r){if(r.fail_bucket)fb=r.fail_bucket;if(r.expected_pct!=null)exp=r.expected_pct;}});
    var cells='<td style="white-space:nowrap;font-size:20px">'+esc(b)+'</td>'
              +'<td style="font-size:20px">'+esc(fb)+'</td>'
              +'<td class="num" style="color:#555">'+(exp!=null?exp.toFixed(1)+'%':'')+'</td>';
    var runVals=groups.map(function(g){var r=byG[g].find(function(x){return x.bin===b;});return r&&r.yield_pct!=null?r.yield_pct:null;});
    var allVals=runVals.slice();
    runVals.forEach(function(v){cells+=cellHl(v,allVals,'')+(v!=null?v.toFixed(1)+'%':'')+'</td>';});
    if(groups.length>=2){
      var isYieldBin=['1/2/3/4','1/2','1'].indexOf(b)>=0;
      for(var ri=1;ri<groups.length;ri++){
        cells+=deltaTd(runVals[ri],runVals[0],null,isYieldBin);
        if(ri>=2)cells+=deltaTd(runVals[ri],runVals[ri-1],null,isYieldBin);
      }
    }
    rows+='<tr>'+cells+'</tr>';
  });
  return '<table class="cmp-tbl"><thead><tr>'+hdr+'</tr></thead><tbody>'+rows+'</tbody></table>';
}

/* ---- IBIN TABLE (exact replica of build_bin_fail_table_html) ---- */
function buildIbinTable(groups,byG,useSummary){
  var catColor={},catPalIdx=0;
  var allRows=[];
  // collect union of ibins from first non-empty group
  var srcKey=useSummary?'bin_summary_rows':'bin_fail_rows';
  groups.forEach(function(g){if(!allRows.length)(byG[g]||[]).forEach(function(r){allRows.push({ibin:r.ibin,cat:r.cat||'',desc:r.desc||'',fail_bucket:r.fail_bucket||'',fb:r.fail_bucket||''});});});

  var hdr=useSummary?'<th>Bin</th><th>Category</th><th>Description</th>':'<th>Interface Bin</th><th>Fail Bucket</th>';
  groups.forEach(function(g,ri){
    hdr+=useSummary
      ?'<th style="background:'+ID_COLORS[ri%ID_COLORS.length]+';color:#fff;font-weight:bold;padding:5px 8px">'+esc(g)+'<br><span style="font-size:11px;font-weight:normal">Yield/Fail%</span></th>'
      :runTh(g,ri);
  });
  if(groups.length>=2){for(var ri=1;ri<groups.length;ri++){hdr+=deltaTh(groups[ri],groups[0]);if(ri>=2)hdr+=deltaTh(groups[ri],groups[ri-1]);}}

  var rows='';
  allRows.forEach(function(row){
    var ib=row.ibin;
    var bg='#ffffff';
    if(useSummary){
      var ck=(row.cat||'').trim().toLowerCase();
      if(ck){if(catColor[ck]==null){catColor[ck]=CAT_PALETTE[catPalIdx%CAT_PALETTE.length];catPalIdx++;}bg=catColor[ck];}
    }
    var cells=useSummary
      ?('<td style="background:'+bg+'">'+esc(ib)+'</td>'
        +'<td style="background:'+bg+'">'+esc(row.cat||'')+'</td>'
        +'<td style="background:'+bg+'">'+esc(row.desc||'')+'</td>')
      :('<td>'+esc(ib)+'</td><td>'+esc(row.fb||'')+'</td>');
    var runVals=groups.map(function(g){var r=(byG[g]||[]).find(function(x){return x.ibin===ib;});return r&&r.fail_pct!=null?r.fail_pct:null;});
    var allVals=runVals.slice();
    runVals.forEach(function(v){cells+=cellHl(v,allVals,useSummary?'background:'+bg+';':'')+(v!=null?v.toFixed(2)+'%':'\u2014')+'</td>';});
    var isGoodIb=String(ib)==='1';
    if(groups.length>=2){for(var ri=1;ri<groups.length;ri++){cells+=deltaTd(runVals[ri],runVals[0],useSummary?bg:null,isGoodIb);if(ri>=2)cells+=deltaTd(runVals[ri],runVals[ri-1],useSummary?bg:null,isGoodIb);}}
    rows+='<tr>'+cells+'</tr>';
  });
  return '<table class="cmp-tbl" style="border-collapse:collapse"><thead><tr>'+hdr+'</tr></thead><tbody>'+rows+'</tbody></table>';
}

/* ---- FBIN TABLE (exact replica of build_func_bin_table_html) ---- */
function buildFbinTable(groups,byG){
  // union of (ibin,fbin) keys
  var keyMap={};
  groups.forEach(function(g){
    (byG[g]||[]).forEach(function(r){
      var k=r.ibin+'|'+r.fbin;
      if(!keyMap[k])keyMap[k]={ibin:r.ibin,fbin:r.fbin,fail_bucket:r.fail_bucket||''};
      else if(!keyMap[k].fail_bucket&&r.fail_bucket)keyMap[k].fail_bucket=r.fail_bucket;
    });
  });
  var allRows=Object.values(keyMap).sort(function(a,b){
    var ai=parseInt(a.ibin)||9999,bi2=parseInt(b.ibin)||9999;
    if(ai!==bi2)return ai-bi2;
    return (parseInt(a.fbin)||9999)-(parseInt(b.fbin)||9999);
  });
  var hdr='<th>Interface Bin</th><th>Functional Bin</th><th>Fail Bucket</th>';
  groups.forEach(function(g,ri){hdr+=runTh(g,ri);});
  if(groups.length>=2){for(var ri=1;ri<groups.length;ri++){hdr+=deltaTh(groups[ri],groups[0]);if(ri>=2)hdr+=deltaTh(groups[ri],groups[ri-1]);}}
  var rows='';
  allRows.forEach(function(row){
    var cells='<td>'+esc(row.ibin)+'</td><td>'+esc(row.fbin||'')+'</td><td>'+esc(row.fail_bucket)+'</td>';
    var runVals=groups.map(function(g){var r=(byG[g]||[]).find(function(x){return x.ibin===row.ibin&&x.fbin===row.fbin;});return r&&r.fail_pct!=null?r.fail_pct:null;});
    var allVals=runVals.slice();
    runVals.forEach(function(v){cells+=cellHl(v,allVals,'')+(v!=null?v.toFixed(1)+'%':'\u2014')+'</td>';});
    var isGoodFb=String(row.fbin)==='101';
    if(groups.length>=2){for(var ri=1;ri<groups.length;ri++){cells+=deltaTd(runVals[ri],runVals[0],null,isGoodFb);if(ri>=2)cells+=deltaTd(runVals[ri],runVals[ri-1],null,isGoodFb);}}
    rows+='<tr>'+cells+'</tr>';
  });
  return '<table class="cmp-tbl" style="border-collapse:collapse"><thead><tr>'+hdr+'</tr></thead><tbody>'+rows+'</tbody></table>';
}

/* ---- chart highlight on row click ---- */
var _selIbin=null, _selFbin=null;

function _doHighlight(chartId, yMatch, stateVar, tr){
  // toggle off
  if(window['_sel'+stateVar]===tr){
    tr.classList.remove('sel');window['_sel'+stateVar]=null;
    var div=document.getElementById(chartId);if(!div||!div.data)return;
    div.data.forEach(function(_t,ti){
      var n=(div.data[ti].y||[]).length;
      Plotly.restyle(chartId,{'marker.opacity':[Array(n).fill(0.85)]},[ti]);
    });
    return;
  }
  if(window['_sel'+stateVar])window['_sel'+stateVar].classList.remove('sel');
  tr.classList.add('sel');window['_sel'+stateVar]=tr;
  var div=document.getElementById(chartId);if(!div||!div.data)return;
  div.data.forEach(function(_t,ti){
    var ops=(div.data[ti].y||[]).map(function(lbl){
      return String(lbl).indexOf(yMatch)>=0?1.0:0.2;
    });
    Plotly.restyle(chartId,{'marker.opacity':[ops]},[ti]);
  });
}

// row-click callbacks stored by table — called as strings from onclick attr
var _ibinRowLabels=[], _fbinRowLabels=[];
function onIbinRowClick(tr,ri){_doHighlight('chart-ibin',_ibinRowLabels[ri],'Ibin',tr);}
function onFbinRowClick(tr,ri){_doHighlight('chart-fbin',_fbinRowLabels[ri],'Fbin',tr);}

/* ---- YIELD — bars from ibin data, lines from yield_rows ---- */
function renderYield(gmap){
  var groups=Object.keys(gmap);

  // ibin data for stacked fail bars (same source as ibin chart)
  var byGib={}, ibMax={}, ibCat={}, ibDesc={};
  groups.forEach(function(g){
    var agg=aggBin(gmap[g],'bin_summary_rows');
    if(!agg.length) agg=aggBin(gmap[g],'bin_fail_rows');
    byGib[g]=agg;
    agg.forEach(function(r){
      // skip ibins 1-4 (good bins)
      if(parseInt(r.ibin)<=4) return;
      var v=r.fail_pct||0;
      if(v>(ibMax[r.ibin]||0)){ibMax[r.ibin]=v;ibCat[r.ibin]=r.cat||'';ibDesc[r.ibin]=r.desc||'';}
    });
  });

  var topFail=parseInt((document.getElementById('topfail')||{value:'16'}).value)||16;
  // sort desc so index 0 = highest fail; reverse for trace push so highest ends up last (top of legend/stack)
  var failBins=Object.keys(ibMax).sort(function(a,b){return ibMax[b]-ibMax[a];}).slice(0,topFail).reverse();

  // yield_rows data for key-bin lines only
  var byGy={};
  groups.forEach(function(g){ byGy[g]=aggYield(gmap[g]); });
  var yBinOrder=byGy[groups[0]]?byGy[groups[0]].map(function(r){return r.bin;}):[];
  var kbins=yBinOrder.filter(function(b){return KEY_BINS.indexOf(b)>=0;});
  if(!kbins.length) kbins=yBinOrder.filter(binAllGood).slice(0,2);

  var traces=[];

  // stacked bars per ibin — highest-failing last so it appears on top of legend
  failBins.forEach(function(ib,si){
    var parts=['IB '+ib];
    if(ibCat[ib]) parts.push(ibCat[ib]);
    if(ibDesc[ib]) parts.push(ibDesc[ib]);
    var segLbl=parts.join(' - ');
    var vals=groups.map(function(g){var r=byGib[g].find(function(x){return x.ibin===ib;});return r&&r.fail_pct!=null?+r.fail_pct.toFixed(2):0;});
    var texts=vals.map(function(v){return v>=0.8?v.toFixed(1)+'%':'';});
    traces.push({type:'bar',name:segLbl,x:groups,y:vals,
      text:texts,textposition:'inside',insidetextanchor:'middle',
      textfont:{size:10,color:'white'},constraintext:'inside',
      marker:{color:FAIL_COLORS[si%FAIL_COLORS.length],opacity:0.85,
              line:{color:'white',width:0.5}},
      yaxis:'y',hovertemplate:'%{y:.2f}%<extra>'+esc(segLbl)+'</extra>'});
  });

  // line per key-bin on right y-axis + full-width shapes for expected
  var shapes=[], annotations=[];
  kbins.slice(0,2).forEach(function(b,ki){
    var lbl=KEY_BIN_TITLES[b]||('Bin '+b);
    var vals=groups.map(function(g){var r=byGy[g].find(function(x){return x.bin===b;});return r&&r.yield_pct!=null?+r.yield_pct.toFixed(2):null;});
    var ptLabels=vals.map(function(v){return v!=null?v.toFixed(1)+'%':'';});
    traces.push({type:'scatter',mode:'lines+markers+text',name:lbl,
      x:groups,y:vals,cliponaxis:false,
      text:ptLabels,textposition:'top center',textfont:{size:11,color:LINE_COLORS[ki]},
      marker:{size:9,color:LINE_COLORS[ki]},
      line:{width:2.5,color:LINE_COLORS[ki]},
      yaxis:'y2',hovertemplate:'%{y:.1f}%<extra>'+esc(lbl)+'</extra>'});
    var firstVal=vals.find(function(v){return v!=null;});
    if(firstVal!=null){
      annotations.push({xref:'paper',yref:'y2',x:0,y:firstVal,
        text:'<b>'+lbl+'</b> '+firstVal.toFixed(1)+'%',
        showarrow:false,xanchor:'left',yanchor:'bottom',
        font:{size:11,color:LINE_COLORS[ki]}});
    }
    var expVals=groups.map(function(g){var r=byGy[g].find(function(x){return x.bin===b;});return r&&r.expected_pct!=null?r.expected_pct:null;});
    var expVal=expVals.find(function(v){return v!=null;});
    if(expVal!=null){
      shapes.push({type:'line',xref:'paper',yref:'y2',
        x0:0,y0:expVal,x1:1,y1:expVal,
        line:{color:LINE_COLORS[ki],width:1.6,dash:'dot'}});
      annotations.push({xref:'paper',yref:'y2',x:0,y:expVal,
        text:'Exp '+expVal.toFixed(1)+'%',
        showarrow:false,xanchor:'left',yanchor:'bottom',
        font:{size:10,color:LINE_COLORS[ki]}});
    }
  });

  // y-axis range: max stacked ibin total × 2.5 to leave headroom above bars
  var allStackedTotals=groups.map(function(g){
    return failBins.reduce(function(s,ib){var r=byGib[g].find(function(x){return x.ibin===ib;});return s+(r&&r.fail_pct!=null?r.fail_pct:0);},0);
  });
  var maxFail=Math.max.apply(null,allStackedTotals.concat([0]));
  var failYlim=Math.min(100,Math.max(5,maxFail*2.5));

  document.getElementById('table-yield').innerHTML=buildYieldTable(groups,byGy,yBinOrder);
  makeSortable(document.querySelector('#table-yield table'));

  Plotly.react('chart-yield',traces,{
    barmode:'stack',
    yaxis:{title:{text:'Fail (%)',standoff:10},titlefont:{size:13},side:'left',range:[0,failYlim],
           gridcolor:'#e5e5e5',showgrid:true,zeroline:true,gridwidth:1,griddash:'dash'},
    yaxis2:{title:{text:'Yield (%)',standoff:10},titlefont:{size:13},side:'right',overlaying:'y',range:[0,100],
            showgrid:false,zeroline:false},
    xaxis:{tickangle:-15,tickfont:{size:12},range:[-0.5,groups.length-0.5]},
    title:{text:'Yield (%) and Fail (%) Chart',font:{size:14,color:'#2c3e50',weight:'bold'}},
    legend:{orientation:'v',x:1.08,y:1,xanchor:'left',yanchor:'top',font:{size:12},bgcolor:'rgba(255,255,255,0.85)',bordercolor:'#ccc',borderwidth:1},
    shapes:shapes,annotations:annotations,
    autosize:true,
    margin:{l:80,r:160,t:50,b:80},
    plot_bgcolor:'#fafbfc',paper_bgcolor:'#fff'
  },{responsive:true});
}

/* ---- INTERFACE BIN pareto ---- */
function renderIbin(gmap){
  var thr=parseFloat(document.getElementById('threshold').value)||0.1;
  var topn=parseInt(document.getElementById('topn').value)||20;
  var groups=Object.keys(gmap),byG={},ibMax={},ibBkt={};
  groups.forEach(function(g){
    var agg=aggBin(gmap[g],'bin_summary_rows');
    if(!agg.length)agg=aggBin(gmap[g],'bin_fail_rows');
    byG[g]=agg;
    agg.forEach(function(r){var v=r.fail_pct||0;if(v>(ibMax[r.ibin]||0)){ibMax[r.ibin]=v;ibBkt[r.ibin]=r.fail_bucket||'';}});
  });
  var major=Object.keys(ibMax).filter(function(k){return ibMax[k]>=thr;})
    .sort(function(a,b){return ibMax[b]-ibMax[a];}).slice(0,topn);
  var minor=Object.keys(ibMax).filter(function(k){return ibMax[k]<thr;});
  var labels=major.concat(minor.length?['__MISC__']:[]);
  var yLbls=labels.map(function(k){
    return k==='__MISC__'?'Misc (<'+thr.toFixed(1)+'%)':'iBin '+k+' \u2502 '+(ibBkt[k]||'');
  });

  // build table — exact static replica; also populate row-labels for chart highlight
  _ibinRowLabels=major.map(function(ib){return 'iBin '+ib+' \u2502';});
  var useSummary=groups.some(function(g){return (byG[g]||[]).some(function(r){return r.cat;});});
  _selIbin=null;
  document.getElementById('table-ibin').innerHTML=buildIbinTable(groups,byG,useSummary);
  makeSortable(document.querySelector('#table-ibin table'));
  // re-attach onclick after innerHTML replace
  document.querySelectorAll('#table-ibin tbody tr').forEach(function(tr,ri){
    tr.style.cursor='pointer';
    tr.onclick=function(){onIbinRowClick(tr,ri);};
  });

  var traces=groups.map(function(g,gi){
    var agg=byG[g]||[];
    var vals=labels.map(function(k){
      if(k==='__MISC__')return minor.reduce(function(s,mk){var r=agg.find(function(x){return x.ibin===mk;});return s+(r?r.fail_pct||0:0);},0);
      var r=agg.find(function(x){return x.ibin===k;});return r?r.fail_pct||0:0;
    });
    return{type:'bar',orientation:'h',name:g,x:vals,y:yLbls,
      marker:{color:COLORS[gi%COLORS.length],opacity:0.85},
      hovertemplate:'%{x:.2f}%<extra>'+esc(g)+'</extra>'};
  });
  Plotly.react('chart-ibin',traces,{
    barmode:'group',xaxis:{title:'Fail (%)'},
    yaxis:{autorange:'reversed'},
    title:{text:'Interface Bin Pareto (\u2265'+thr.toFixed(1)+'%)',font:{size:14,color:'#2c3e50'}},
    height:Math.max(320,labels.length*44),margin:{l:230,r:15,t:50,b:40},
    legend:{orientation:'h',y:-0.1},
    plot_bgcolor:'#fafbfc',paper_bgcolor:'#fff'
  },{responsive:true});
}

/* ---- FUNCTIONAL BIN pareto ---- */
function renderFbin(gmap){
  var thr=parseFloat(document.getElementById('threshold').value)||0.1;
  var topn=parseInt(document.getElementById('topn').value)||20;
  var groups=Object.keys(gmap),byG={},kMax={},kBkt={};
  groups.forEach(function(g){
    var agg=aggFbin(gmap[g]);byG[g]=agg;
    agg.forEach(function(r){var k=r.ibin+'|'+r.fbin,v=r.fail_pct||0;
      if(v>(kMax[k]||0)){kMax[k]=v;kBkt[k]=r.fail_bucket||'';}
    });
  });
  var major=Object.keys(kMax).filter(function(k){return kMax[k]>=thr;})
    .sort(function(a,b){return kMax[b]-kMax[a];}).slice(0,topn);
  var minor=Object.keys(kMax).filter(function(k){return kMax[k]<thr;});
  var labels=major.concat(minor.length?['__MISC__']:[]);
  var yLbls=labels.map(function(k){
    if(k==='__MISC__')return 'Misc (<'+thr.toFixed(1)+'%)';
    var p=k.split('|');return 'iBin '+p[0]+' \u2192 fBin '+p[1]+' \u2502 '+(kBkt[k]||'');
  });

  _fbinRowLabels=major.map(function(k){var p=k.split('|');return 'iBin '+p[0]+' \u2192 fBin '+p[1];});
  _selFbin=null;
  document.getElementById('table-fbin').innerHTML=buildFbinTable(groups,byG);
  makeSortable(document.querySelector('#table-fbin table'));
  document.querySelectorAll('#table-fbin tbody tr').forEach(function(tr,ri){
    tr.style.cursor='pointer';
    tr.onclick=function(){onFbinRowClick(tr,ri);};
  });

  var traces=groups.map(function(g,gi){
    var agg=byG[g]||[];
    var vals=labels.map(function(k){
      if(k==='__MISC__')return minor.reduce(function(s,mk){var p=mk.split('|');var r=agg.find(function(x){return x.ibin===p[0]&&x.fbin===p[1];});return s+(r?r.fail_pct||0:0);},0);
      var p=k.split('|');var r=agg.find(function(x){return x.ibin===p[0]&&x.fbin===p[1];});return r?r.fail_pct||0:0;
    });
    return{type:'bar',orientation:'h',name:g,x:vals,y:yLbls,
      marker:{color:COLORS[gi%COLORS.length],opacity:0.85},
      hovertemplate:'%{x:.2f}%<extra>'+esc(g)+'</extra>'};
  });
  Plotly.react('chart-fbin',traces,{
    barmode:'group',xaxis:{title:'Fail (%)'},
    yaxis:{autorange:'reversed'},
    title:{text:'Functional Bin Pareto (\u2265'+thr.toFixed(1)+'%)',font:{size:14,color:'#2c3e50'}},
    height:Math.max(320,labels.length*44),margin:{l:270,r:15,t:50,b:40},
    legend:{orientation:'h',y:-0.1},
    plot_bgcolor:'#fafbfc',paper_bgcolor:'#fff'
  },{responsive:true});
}

function filterTable(divId,q){
  var div=document.getElementById(divId);if(!div)return;
  var tbl=div.querySelector('table');if(!tbl)return;
  var lq=(q||'').toLowerCase().trim();
  Array.from(tbl.tBodies[0].rows).forEach(function(tr){
    tr.style.display=(!lq||tr.textContent.toLowerCase().indexOf(lq)>=0)?'':'none';
  });
}
function makeSortable(tbl){
  if(!tbl||!tbl.tHead||!tbl.tHead.rows[0])return;
  Array.from(tbl.tHead.rows[0].cells).forEach(function(th,ci){
    th.classList.add('sortable');th._sortDir=0;
    th.addEventListener('click',function(){
      var dir=th._sortDir===1?-1:1;
      Array.from(tbl.tHead.rows[0].cells).forEach(function(h){h._sortDir=0;h.classList.remove('sort-asc','sort-desc');});
      th._sortDir=dir;th.classList.add(dir===1?'sort-asc':'sort-desc');
      var rows=Array.from(tbl.tBodies[0].rows);
      rows.sort(function(a,b){
        var av=a.cells[ci]?a.cells[ci].textContent.trim():'';
        var bv=b.cells[ci]?b.cells[ci].textContent.trim():'';
        var an=parseFloat(av.replace('%','')),bn=parseFloat(bv.replace('%',''));
        if(!isNaN(an)&&!isNaN(bn))return dir*(an-bn);
        return dir*av.localeCompare(bv);
      });
      rows.forEach(function(r){tbl.tBodies[0].appendChild(r);});
      // re-apply active search filter after sort
      var wrap=tbl.parentElement;if(wrap&&wrap.id){var inp=document.getElementById('search-'+wrap.id.replace('table-',''));if(inp&&inp.value)filterTable(wrap.id,inp.value);}
    });
  });
}

function applyGroups(){
  var gmap=buildGroupMap();
  if(!Object.keys(gmap).length)return;
  renderYield(gmap);renderIbin(gmap);renderFbin(gmap);
}

renderGroupsRow();
renderTpTable();
applyGroups();

// relay chart-div resize events to Plotly so responsive layout reflows
(function(){
  var ids=['chart-yield','chart-ibin','chart-fbin'];
  if(typeof ResizeObserver==='undefined') return;
  var ro=new ResizeObserver(function(entries){
    entries.forEach(function(e){
      if(ids.indexOf(e.target.id)>=0) Plotly.Plots.resize(e.target);
    });
  });
  ids.forEach(function(id){var el=document.getElementById(id);if(el)ro.observe(el);});
})();
</script>
</body>
</html>"""


def _write_interactive_report(records: list[dict], assignments: list[str], out_path: Path) -> None:
    """Write a self-contained interactive HTML; all aggregation and charting runs in the browser."""
    records_json = json.dumps(_make_serializable(records), separators=(',', ':'))
    assignments_json = json.dumps(assignments, separators=(',', ':'))
    seen: set = set()
    groups: list[str] = []
    for g in assignments:
        if g not in seen:
            seen.add(g)
            groups.append(g)
    html = (_GC_HTML_TEMPLATE
            .replace('__PLOTLY_TAG__', _load_plotly_js())
            .replace('__GC_RECORDS__', records_json)
            .replace('__GC_ASSIGNMENTS__', assignments_json)
            .replace('__GC_GROUPS__', json.dumps(groups, separators=(',', ':'))))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding='utf-8')


def run_group_compare_headless(
    group_index_htmls: list[tuple[str, Path]],
    out_path: Path,
    log=print,
) -> Path | None:
    """Headless group compare for automation — no tkinter required.

    Each (group_name, index_html) pair produces one or more individual TP
    records (via build_tp_records).  All records are passed directly to
    _write_interactive_report together with their group assignments so the
    browser-side JS can aggregate them — the same way the GUI does it.
    """
    all_recs: list[dict] = []
    all_assignments: list[str] = []
    groups_seen: set[str] = set()

    for group_name, index_html in group_index_htmls:
        if not index_html.exists():
            log(f'  SKIP {group_name}: {index_html} not found')
            continue
        try:
            recs = build_tp_records(index_html)
        except Exception as exc:
            log(f'  ERROR loading {index_html}: {exc}')
            continue
        if not recs:
            continue
        for rec in recs:
            all_recs.append(rec)
            all_assignments.append(group_name)
        groups_seen.add(group_name)
        log(f'  {group_name}: {len(recs)} TP(s), die={sum(r["numDie"] or 0 for r in recs)}')

    if len(groups_seen) < 2:
        log(f'  Group compare skipped — need \u22652 groups, got {len(groups_seen)}')
        return None

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_interactive_report(all_recs, all_assignments, out_path)
        log(f'  Group compare \u2192 {out_path}')
        return out_path
    except Exception as exc:
        log(f'  ERROR generating group compare: {exc}')
        return None


# ---------------------------------------------------------------------------
# GUI — Group Compare tab
# ---------------------------------------------------------------------------

class GroupCompareFrame(tk.Frame):
    """Load runs (index.html files or a Dashboard.html), assign each to a
    group, then render the SAME report layout as CompareFrame's output —
    one column per group instead of per run. Independent of CompareFrame."""

    def __init__(self, parent=None, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._records: list[dict] = []
        self._group_vars: list[tk.StringVar] = []   # one per TP record, holds assigned group name
        self._groups: list[str] = list(DEFAULT_GROUPS)
        self._out_var = tk.StringVar()
        self._last_report_path = ''
        self._build_ui()

    def _build_ui(self):
        P = {'padx': 10, 'pady': 4}
        tk.Label(self, text='Group Compare  —  assign runs to groups, same report layout',
                 bg=BG, fg=ABLU, font=('Arial', 13, 'bold')
                 ).pack(fill='x', padx=10, pady=(8, 2))

        frm1 = _lf(self, 'Step 1 — Load runs', ABLU)
        frm1.pack(fill='x', **P)
        row1 = tk.Frame(frm1, bg=BG)
        row1.pack(fill='x')
        _btn(row1, 'Add index.html…', self._add_index_files).pack(side='left', padx=(0, 4))
        _btn(row1, 'Import Dashboard.html…', self._import_dashboard, color='#1f618d').pack(side='left', padx=(0, 4))
        _btn(row1, 'Remove last', self._remove_selected, color='#935116').pack(side='left', padx=(0, 4))
        _btn(row1, 'Save Setup…', self._save_setup, color='#6c3483').pack(side='left', padx=(0, 4))
        _btn(row1, 'Load Setup…', self._load_setup, color='#6c3483').pack(side='left')

        frm2 = _lf(self, 'Step 2 — Groups', '#9b59b6')
        frm2.pack(fill='x', **P)
        self._groups_row = tk.Frame(frm2, bg=BG)
        self._groups_row.pack(fill='x')
        _btn(frm2, '+ Add Group', self._add_group, color='#1f618d').pack(anchor='w', pady=(4, 0))

        frm3 = _lf(self, 'Step 3 — Assign each TP to a group', '#9b59b6')
        frm3.pack(fill='both', expand=True, **P)
        canvas = tk.Canvas(frm3, bg=BG2, borderwidth=0, highlightthickness=0, height=180)
        vsb = tk.Scrollbar(frm3, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        self._rows_frame = tk.Frame(canvas, bg=BG2)
        self._rows_win = canvas.create_window((0, 0), window=self._rows_frame, anchor='nw')
        self._rows_frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfig(self._rows_win, width=e.width))

        frm4 = _lf(self, 'Step 4 — Output', FG2)
        frm4.pack(fill='x', **P)
        out_row = tk.Frame(frm4, bg=BG)
        out_row.pack(fill='x')
        tk.Label(out_row, text='Output file:', bg=BG, fg=FG, font=('Arial', 9),
                 width=11, anchor='w').pack(side='left')
        tk.Entry(out_row, textvariable=self._out_var, width=46, bg=BG2, fg=FG,
                  insertbackground=FG, relief='flat', font=('Consolas', 9)
                  ).pack(side='left', padx=(0, 4), expand=True, fill='x')
        _btn(out_row, '…', self._browse_out, width=3).pack(side='left')

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=(6, 2), padx=10, fill='x')
        self._interactive_btn = _btn(btn_row, '\u26a1  Generate Interactive Report', self._generate_interactive,
                                     color='#1f618d', acolor='#2980b9')
        self._interactive_btn.config(font=('Arial', 10, 'bold'), pady=5)
        self._interactive_btn.pack(side='left', expand=True, fill='x', padx=(0, 4))
        self._open_btn = _btn(btn_row, '  Open Report  ', self._open_report,
                              color='#935116', acolor='#ca6f1e')
        self._open_btn.config(font=('Arial', 10, 'bold'), pady=5, state='disabled')
        self._open_btn.pack(side='left')

        log_frm = _lf(self, 'Log', FG2)
        log_frm.pack(fill='both', expand=False, **P)
        self._log = scrolledtext.ScrolledText(log_frm, height=6, state='disabled',
                                               font=('Consolas', 8), bg='#0d1b26',
                                               fg='#a8d8ea', relief='flat')
        self._log.pack(fill='both', expand=True)

        self._render_groups_row()

    # -- groups -----------------------------------------------------------------

    def _render_groups_row(self):
        for w in self._groups_row.winfo_children():
            w.destroy()
        for i, g in enumerate(self._groups):
            v = tk.StringVar(value=g)
            e = tk.Entry(self._groups_row, textvariable=v, width=14, bg=BG2, fg=FG,
                         insertbackground=FG, relief='flat', font=('Consolas', 9))
            e.pack(side='left', padx=(0, 6))
            e.bind('<FocusOut>', lambda ev, idx=i, var=v: self._rename_group(idx, var.get()))
            e.bind('<Return>', lambda ev, idx=i, var=v: self._rename_group(idx, var.get()))

    def _rename_group(self, idx: int, new_name: str):
        new_name = new_name.strip()
        if not new_name or new_name == self._groups[idx]:
            return
        old_name = self._groups[idx]
        self._groups[idx] = new_name
        for v in self._group_vars:
            if v.get() == old_name:
                v.set(new_name)
        self._render_groups_row()

    def _add_group(self):
        n = len(self._groups) + 1
        name = f'Group {chr(64 + n)}' if n <= 26 else f'Group {n}'
        while name in self._groups:
            n += 1
            name = f'Group {chr(64 + n)}' if n <= 26 else f'Group {n}'
        self._groups.append(name)
        self._render_groups_row()
        self._render_run_rows()

    # -- loading ------------------------------------------------------------------

    def _add_run(self, rec: dict):
        self._records.append(rec)
        self._group_vars.append(tk.StringVar(value=self._groups[(len(self._records) - 1) % len(self._groups)]))
        self._render_run_rows()

    def _add_run_new_group(self, rec: dict, group_index: int):
        """Add a run and put it in its own group (creating groups as needed) —
        used when one index.html splits into several real TPs, so they don't
        silently wrap around and merge into the existing default groups."""
        while len(self._groups) <= group_index:
            self._add_group()
        self._records.append(rec)
        self._group_vars.append(tk.StringVar(value=self._groups[group_index]))
        self._render_run_rows()

    def _render_run_rows(self):
        for w in self._rows_frame.winfo_children():
            w.destroy()
        for i, rec in enumerate(self._records):
            row_bg = BG2 if i % 2 == 0 else '#253545'
            row = tk.Frame(self._rows_frame, bg=row_bg)
            row.pack(fill='x')
            tk.Label(row, text=f'{rec["tp"]}  (from {rec["name"]}, die={rec["numDie"]})',
                     bg=row_bg, fg=FG, font=('Consolas', 9), anchor='w'
                     ).pack(side='left', fill='x', expand=True, padx=4, pady=3)
            tk.OptionMenu(row, self._group_vars[i], *self._groups, '(exclude)').pack(side='right', padx=4)

    def _add_index_files(self):
        paths = filedialog.askopenfilenames(
            title='Select index.html file(s)',
            filetypes=[('index.html', 'index.html'), ('HTML files', '*.html'), ('All', '*.*')])
        for p in paths:
            try:
                recs = build_tp_records(Path(p))
                if len(recs) > 1:
                    base = len(self._groups)
                    for gi, rec in enumerate(recs):
                        self._add_run_new_group(rec, base + gi)
                        self._log_write(f'Loaded TP {rec["tp"]} — die={rec["numDie"]}\n')
                else:
                    for rec in recs:
                        self._add_run(rec)
                        self._log_write(f'Loaded TP {rec["tp"]} — die={rec["numDie"]}\n')
                        if rec.get('fbDiag'):
                            self._log_write(f'  FB data source: {rec["fbDiag"]}\n')
            except Exception as exc:
                self._log_write(f'ERROR loading {p}: {exc}\n')
        if paths and not self._out_var.get():
            self._out_var.set(str(Path(paths[0]).parent / 'group_compare.html'))

    def _import_dashboard(self):
        p = filedialog.askopenfilename(title='Select Dashboard.html',
                                        filetypes=[('HTML files', '*.html'), ('All', '*.*')])
        if not p:
            return
        dash = Path(p)
        try:
            recs = parse_dashboard(dash)
        except Exception as exc:
            messagebox.showerror('Parse error', str(exc))
            return
        for r in recs:
            idx = resolve_index_html_from_dashboard(dash.parent, r.get('index_href'))
            if not idx:
                self._log_write(f'  [{r["name"]}] index.html not found, skipped\n')
                continue
            try:
                recs = build_tp_records(idx)
                if len(recs) > 1:
                    base = len(self._groups)
                    for gi, rec in enumerate(recs):
                        self._add_run_new_group(rec, base + gi)
                        self._log_write(f'Loaded TP {rec["tp"]} — die={rec["numDie"]}\n')
                else:
                    for rec in recs:
                        self._add_run(rec)
                        self._log_write(f'Loaded TP {rec["tp"]} — die={rec["numDie"]}\n')
                        if rec.get('fbDiag'):
                            self._log_write(f'  FB data source: {rec["fbDiag"]}\n')
            except Exception as exc:
                self._log_write(f'ERROR loading {r["name"]}: {exc}\n')
        if not self._out_var.get():
            self._out_var.set(str(dash.parent / 'group_compare.html'))

    def _remove_selected(self):
        # simplest: remove the last-loaded TP row (row list has no multi-select widget)
        if self._records:
            self._records.pop()
            self._group_vars.pop()
            self._render_run_rows()

    def _browse_out(self):
        p = filedialog.asksaveasfilename(title='Save report as', defaultextension='.html',
                                          filetypes=[('HTML files', '*.html')])
        if p:
            self._out_var.set(p)

    # -- save / load setup ----------------------------------------------------

    def _save_setup(self):
        if not self._records:
            messagebox.showwarning('Nothing to save', 'Load at least one index.html first.')
            return
        p = filedialog.asksaveasfilename(title='Save setup as', defaultextension='.json',
                                          filetypes=[('JSON files', '*.json')])
        if not p:
            return
        setup = {
            'groups': self._groups,
            'out_path': self._out_var.get(),
            'runs': [{'indexHtml': rec['indexHtml'], 'tp': rec['tp'], 'group': gv.get()}
                     for rec, gv in zip(self._records, self._group_vars)],
        }
        try:
            Path(p).write_text(json.dumps(setup, indent=2), encoding='utf-8')
            self._log_write(f'Saved setup \u2192 {p}\n')
        except Exception as exc:
            messagebox.showerror('Save failed', str(exc))

    def _load_setup(self):
        p = filedialog.askopenfilename(title='Load setup', filetypes=[('JSON files', '*.json'), ('All', '*.*')])
        if not p:
            return
        try:
            setup = json.loads(Path(p).read_text(encoding='utf-8'))
        except Exception as exc:
            messagebox.showerror('Load failed', str(exc))
            return

        self._records.clear()
        self._group_vars.clear()
        self._groups = list(setup.get('groups') or DEFAULT_GROUPS)
        self._out_var.set(setup.get('out_path', ''))
        self._render_groups_row()

        cache: dict[str, list[dict]] = {}
        for entry in setup.get('runs', []):
            idx_html, tp, group = entry.get('indexHtml'), entry.get('tp'), entry.get('group')
            if not idx_html:
                continue
            try:
                if idx_html not in cache:
                    cache[idx_html] = build_tp_records(Path(idx_html))
                rec = next((r for r in cache[idx_html] if r['tp'] == tp), None)
                if rec is None:
                    self._log_write(f'WARNING: TP {tp} not found in {idx_html} anymore, skipped\n')
                    continue
                self._records.append(rec)
                if group not in self._groups:
                    self._groups.append(group)
                self._group_vars.append(tk.StringVar(value=group))
            except Exception as exc:
                self._log_write(f'ERROR loading {idx_html}: {exc}\n')
        self._render_groups_row()
        self._render_run_rows()
        self._log_write(f'Loaded setup from {p} \u2014 {len(self._records)} TP(s)\n')

    # -- generate -----------------------------------------------------------------

    def _generate_interactive(self):
        if not self._records:
            messagebox.showwarning('No runs', 'Add at least one index.html first.')
            return
        out_str = self._out_var.get().strip()
        out_path = Path(out_str) if out_str else Path(self._records[0]['indexHtml']).parent / 'group_compare.html'
        assignments = [gv.get() for gv in self._group_vars]
        try:
            _write_interactive_report(self._records, assignments, out_path)
            self._last_report_path = str(out_path)
            self._log_write(f'Interactive \u2192 {out_path}\n')
            self._open_btn.configure(state='normal')
        except Exception as exc:
            self._log_write(f'ERROR: {exc}\n')

    def _open_report(self):
        if self._last_report_path and os.path.isfile(self._last_report_path):
            import subprocess, shutil
            path = self._last_report_path
            # Try Chrome first (supports --start-maximized reliably)
            chrome = None
            for candidate in [
                r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            ]:
                if os.path.isfile(candidate):
                    chrome = candidate
                    break
            if chrome is None:
                chrome = shutil.which('chrome') or shutil.which('google-chrome')
            try:
                if chrome:
                    subprocess.Popen([chrome, '--start-maximized', path])
                else:
                    subprocess.Popen(['cmd', '/c', 'start', '', '/max', path], shell=False)
            except Exception:
                os.startfile(path)

    def _log_write(self, msg: str):
        self._log.configure(state='normal')
        self._log.insert('end', msg)
        self._log.see('end')
        self._log.configure(state='disabled')
