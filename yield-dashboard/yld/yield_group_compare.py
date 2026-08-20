"""yield_group_compare.py — Group-based TP comparison (new, independent of CompareFrame).

Loads one-or-more index.html run folders (or imports a Dashboard.html), lets the
user assign each TP to a group (default 2 groups: Group A / Group B, more can
be added), then reuses the EXISTING yield_trend.generate_report() renderer so
the output looks and behaves exactly like the current compare_report.html —
Yield Table, Bin Fail Summary, SICC/UPM, CDYN, Digital Dashboard — just with
each column being a group instead of a single run.

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
    generate_report, _safe_html_out_path,
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
            rbd['func_bin_rows'] = [
                {'ibin': ib, 'fbin': fb, 'fail_bucket': '', 'fail_count': cnt,
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
# ones yield_trend.generate_report() already expects (name/data/bin_data/
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
    """Aggregate a group's member runs into one record shaped for generate_report()."""
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


def _strip_run_summary_section(out_path: Path) -> None:
    """Remove the Run Summary section — Program/Lot/Wafer are per-TP metadata
    that has no single value once runs are pooled into a group, so it only
    ever shows em-dashes for a group report."""
    html = out_path.read_text(encoding='utf-8')
    stripped = re.sub(
        r'<div class="section">\s*<h2>[^<]*Run Summary</h2>.*?</div>\s*</div>',
        '', html, count=1, flags=re.DOTALL)
    stripped = re.sub(
        r'<div class="dash-link">Source:.*?</div>\s*',
        '', stripped, count=1, flags=re.DOTALL)
    if stripped != html:
        out_path.write_text(stripped, encoding='utf-8')


def _strip_digital_dashboard_section(out_path: Path) -> None:
    """Remove the Digital Dashboard (Sub Module / Yield Loss) comparison table —
    not meaningful once individual TPs are pooled into groups."""
    html = out_path.read_text(encoding='utf-8')
    found = _find_section_block(html, 'Digital Dashboard')
    if found:
        _block, s, e = found
        out_path.write_text(html[:s] + html[e:], encoding='utf-8')


def _move_watermark_to_footer(out_path: Path) -> None:
    """Replace generate_report()'s fixed top-right watermark badge with a
    subtle static footer line at the very end of the page."""
    html = out_path.read_text(encoding='utf-8')
    stripped = re.sub(
        r'<div[^>]*id=["\']_wm_div["\'][^>]*>[\s\S]*?</div>\s*<script[^>]*>[\s\S]*?</script>',
        '', html)
    footer = ('<div style="text-align:center;color:#aaa;font-size:11px;'
              'margin:24px 0 8px;padding-top:8px;border-top:1px solid #e0e0e0">'
              'Pant, Sujit N &mdash; GEMS FTE</div>')
    if '</body>' in stripped:
        stripped = stripped.replace('</body>', footer + '\n</body>', 1)
    else:
        stripped += footer
    if stripped != html:
        out_path.write_text(stripped, encoding='utf-8')


# NOTE: no separate watermark injection needed here — generate_report() in
# yield_trend.py already writes the file through _wm_inject(), which adds the
# same "Pant, Sujit N — GEMS FTE" badge (id=_wm_div) to every report.


_PCT_RE = re.compile(r'([+-]?\d+\.\d{2,})%')


def _round_percents_to_one_decimal(out_path: Path) -> None:
    """Reformat every N.NN% (or longer) in the report to a single decimal place."""
    html = out_path.read_text(encoding='utf-8')
    rounded = _PCT_RE.sub(lambda m: f'{float(m.group(1)):.1f}%', html)
    if rounded != html:
        out_path.write_text(rounded, encoding='utf-8')


def _find_section_block(html: str, header_substr: str):
    """Return (block_html, start_idx, end_idx) for the first depth-balanced
    <div class="section">...</div> block whose <h2> contains header_substr."""
    idx = 0
    while True:
        idx = html.find('<div class="section">', idx)
        if idx == -1:
            return None
        i, depth, end = idx, 0, None
        while i < len(html):
            nd = html.find('<div', i)
            nc = html.find('</div>', i)
            if nc == -1:
                break
            if nd != -1 and nd <= nc:
                depth += 1
                i = nd + 4
            else:
                depth -= 1
                i = nc + 6
                if depth == 0:
                    end = i
                    break
        if end is None:
            idx += len('<div class="section">')
            continue
        block = html[idx:end]
        h2_m = re.search(r'<h2>(.*?)</h2>', block)
        if h2_m and header_substr in h2_m.group(1):
            return block, idx, end
        idx = end


_TAB_DEFS = [
    ('gc-yield', 'Yield Compare', ['Yield Information', 'Yield Table']),
    ('gc-ibin', 'Interface Bin Compare', ['Bin Fail Summary', 'Interface Bin Fail Pareto']),
    ('gc-fbin', 'Functional Bin Compare', ['Functional Bin Fail Summary', 'Functional Bin Fail Pareto']),
]
_SIDE_BY_SIDE_TABS = {'gc-ibin', 'gc-fbin'}


def _build_tabbed_layout(out_path: Path) -> None:
    """Pull the Yield / Interface Bin / Functional Bin chart+table sections out of
    the long scrolling report and regroup each pair under its own tab (table and
    plot placed side by side for the Interface/Functional Bin tabs)."""
    html = out_path.read_text(encoding='utf-8')

    insert_at = None
    tab_content: dict[str, str] = {}
    for tab_id, _label, headers in _TAB_DEFS:
        pieces = []
        for h in headers:
            found = _find_section_block(html, h)
            if not found:
                continue
            block, s, e = found
            pieces.append(block)
            if insert_at is None:
                insert_at = s
            html = html[:s] + html[e:]
        if tab_id in _SIDE_BY_SIDE_TABS and len(pieces) == 2:
            tab_content[tab_id] = (
                '<div style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap">'
                f'<div style="flex:1;min-width:340px">{pieces[0]}</div>'
                f'<div style="flex:1;min-width:340px">{pieces[1]}</div>'
                '</div>'
            )
        else:
            tab_content[tab_id] = ''.join(pieces)

    if insert_at is None or not any(tab_content.values()):
        return

    buttons = ''.join(
        f'<div class="gc-tab-btn{" active" if i == 0 else ""}" '
        f'onclick="gcShowTab(\'{tid}\',this)">{label}</div>'
        for i, (tid, label, _h) in enumerate(_TAB_DEFS)
    )
    panels = ''.join(
        f'<div class="gc-tab-panel" id="{tid}" style="display:{"block" if i == 0 else "none"}">'
        f'{tab_content[tid]}</div>'
        for i, (tid, _l, _h) in enumerate(_TAB_DEFS)
    )
    tabs_html = (
        '<div class="gc-tabs-wrap">'
        '<style>'
        '.gc-tab-btn{display:inline-block;padding:8px 18px;margin-right:4px;cursor:pointer;'
        'background:#dce1e7;color:#2c3e50;border-radius:6px 6px 0 0;font-weight:bold;font-size:16px}'
        '.gc-tab-btn.active{background:#2980b9;color:white}'
        '</style>'
        f'<div class="gc-tabs-bar">{buttons}</div>'
        f'{panels}'
        '</div>'
        '<script>'
        'function gcShowTab(id,btn){'
        'document.querySelectorAll(".gc-tab-panel").forEach(function(p){p.style.display="none";});'
        'document.querySelectorAll(".gc-tab-btn").forEach(function(b){b.classList.remove("active");});'
        'document.getElementById(id).style.display="block";'
        'btn.classList.add("active");'
        '}'
        '</script>'
    )

    html = html[:insert_at] + tabs_html + html[insert_at:]
    out_path.write_text(html, encoding='utf-8')


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
        self._run_btn = _btn(btn_row, '\u25b6  Generate Group Report', self._generate,
                             color=GRN, acolor=AGRN)
        self._run_btn.config(font=('Arial', 10, 'bold'), pady=5)
        self._run_btn.pack(side='left', expand=True, fill='x', padx=(0, 4))
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

    def _generate(self):
        if not self._records:
            messagebox.showwarning('No runs', 'Add at least one index.html first.')
            return
        out_str = self._out_var.get().strip()
        out_path = Path(out_str) if out_str else Path(self._records[0]['indexHtml']).parent / 'group_compare.html'

        group_records = []
        for g in self._groups:
            members = [rec for rec, gv in zip(self._records, self._group_vars) if gv.get() == g]
            if not members:
                continue
            group_records.append(build_group_record(g, members))
            self._log_write(f'{g}: {len(members)} TP(s) aggregated\n')

        if not group_records:
            messagebox.showwarning('No groups', 'Assign at least one run to a group.')
            return

        try:
            out_path = _safe_html_out_path(out_path, 'group_compare.html')
            generate_report(group_records, out_path)
            _strip_run_summary_section(out_path)
            _strip_digital_dashboard_section(out_path)
            _round_percents_to_one_decimal(out_path)
            _build_tabbed_layout(out_path)
            _move_watermark_to_footer(out_path)
            self._last_report_path = str(out_path)
            self._log_write(f'Done \u2192 {out_path}\n')
            self._open_btn.configure(state='normal')
        except Exception as exc:
            self._log_write(f'ERROR: {exc}\n')

    def _open_report(self):
        if self._last_report_path and os.path.isfile(self._last_report_path):
            os.startfile(self._last_report_path)

    def _log_write(self, msg: str):
        self._log.configure(state='normal')
        self._log.insert('end', msg)
        self._log.see('end')
        self._log.configure(state='disabled')
