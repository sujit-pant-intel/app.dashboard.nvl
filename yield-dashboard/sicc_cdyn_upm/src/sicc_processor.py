"""sicc_processor.py — Pure-Python SICC / UPM / CDYN CSV processor.

Replaces the JSL pipeline scripts:
  1. process_sicc_upm.jsl   — rename, sums, UPM %
  2. calculate_median_and_plot.jsl — per-group medians, target merge
  3. cdyn_distribution_analysis.jsl — CDYN column detection + medians

Usage::
    from sicc_processor import load_config, process_csv
    cfg  = load_config('path/to/testlist.jsl')   # or .json
    data = process_csv('input.csv', cfg, target_csv='sicc_target.csv')

``data`` is a dict consumed by ``generate_dashboard_html.generate_html``.
"""

import json
import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Wildcard / ordered-token matching (mirrors JSL's OrderedLike)
# ---------------------------------------------------------------------------
def _ordered_like(text: str, pattern: str) -> bool:
    """Return True if all tokens in *pattern* (split on ``*``) appear
    inside *text* in order (case-insensitive)."""
    tokens = [t for t in pattern.split('*') if t]
    if not tokens:
        return True
    pos = 0
    text_up = text.upper()
    for tok in tokens:
        idx = text_up.find(tok.upper(), pos)
        if idx < 0:
            return False
        pos = idx + len(tok)
    return True


def _find_col(df_cols, pattern: str) -> Optional[str]:
    """Return first column name matching *pattern* (wildcard), or None."""
    for c in df_cols:
        if _ordered_like(c, pattern):
            return c
    return None


# ---------------------------------------------------------------------------
# Config loading — supports .jsl and .json
# ---------------------------------------------------------------------------
def load_config(config_path: str) -> dict:
    """Load testlist config from a ``.jsl`` or ``.json`` file.

    JSON format::

        {
          "siccList":      [["pattern", "friendly name"], ...],
          "siccTotalList": [["SumColName", "Col1", "Col2", ...], ...],
          "columnConfigs": [["NewColName", "src_pattern", divisor], ...],
          "cdynList":      [["pattern", "friendly name"], ...]   // optional
        }
    """
    p = Path(config_path)
    if not p.exists():
        return {}
    text = p.read_text(encoding='utf-8')
    if p.suffix.lower() == '.json':
        return json.loads(text)
    # Assume JSL
    return _parse_jsl_config(text)


def _parse_jsl_config(text: str) -> dict:
    """Best-effort parser for testlist.jsl — extracts renameList, TotalList,
    columnConfigs (and optionally cdynList) from JMP Scripting Language source."""

    def _jsl_entries(block: str) -> list:
        """Extract inner ``{ ... }`` items from a JSL list block."""
        entries = []
        depth, start = 0, -1
        for i, ch in enumerate(block):
            if ch == '{':
                if depth == 0:
                    start = i + 1
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    inner = block[start:i]
                    # Extract quoted strings
                    strs = re.findall(r'"([^"]*)"', inner)
                    # Extract standalone numbers that are NOT inside quotes
                    # (needed for columnConfigs divisor e.g. 9154)
                    in_q = [False] * len(inner)
                    for m in re.finditer(r'"[^"]*"', inner):
                        for k in range(m.start(), m.end()):
                            in_q[k] = True
                    nums = [m.group(1) for m in re.finditer(r'\b(\d+(?:\.\d+)?)\b', inner)
                            if not in_q[m.start()]]
                    combined = strs + nums
                    if combined:
                        entries.append(combined)
                    start = -1
        return entries

    def _extract_block(name: str) -> str:
        m = re.search(r'\b' + re.escape(name) + r'\s*=\s*\{', text)
        if not m:
            return ''
        depth, end = 0, m.end() - 1
        for i in range(m.end() - 1, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        return text[m.end():end]

    rename_block  = _extract_block('renameList')
    total_block   = _extract_block('TotalList')
    col_block     = _extract_block('columnConfigs')

    rename_list = []
    for e in _jsl_entries(rename_block):
        if len(e) >= 2:
            rename_list.append([e[0], e[1]])

    total_list = []
    for e in _jsl_entries(total_block):
        if e:
            total_list.append(e)

    column_configs = []
    for e in _jsl_entries(col_block):
        if len(e) >= 3:
            try:
                column_configs.append([e[0], e[1], float(e[2])])
            except (ValueError, IndexError):
                pass

    return {
        'siccList':      rename_list,
        'siccTotalList': total_list,
        'columnConfigs': column_configs,
    }


# ---------------------------------------------------------------------------
# Histogram helper
# ---------------------------------------------------------------------------
def _make_hist(vals: np.ndarray, n_bins: int = 40) -> dict:
    vals = vals[np.isfinite(vals)]
    if len(vals) == 0:
        return {'edges': [], 'counts': []}
    n = min(n_bins, max(8, len(vals) // 5))
    counts, edges = np.histogram(vals, bins=n)
    return {
        'edges':  [round(float(e), 8) for e in edges],
        'counts': [int(c) for c in counts],
    }


# ---------------------------------------------------------------------------
# Main processing function
# ---------------------------------------------------------------------------
def process_csv(csv_path: str,
                config: dict,
                target_csv: str = '',
                cdyn_targets: Optional[dict] = None,
                override_targets: Optional[dict] = None,
                override_cdyn_targets: Optional[dict] = None,
                build_histograms: bool = False) -> dict:
    """Process a sort-data CSV using *config* and return a data dict
    suitable for ``generate_dashboard_html.generate_html``.

    Parameters
    ----------
    csv_path              : path to the raw sort CSV
    config                : dict from ``load_config``
    target_csv            : path to SICC target CSV (TestName, Target columns; legacy)
    cdyn_targets          : dict mapping CDYN friendly names → target values (optional)
    override_targets      : SICC/UPM targets from product config — take precedence over config/CSV
    override_cdyn_targets : CDYN targets from product config — take precedence over config
    """
    df = pd.read_csv(csv_path, dtype=object)
    df = df.copy()  # defragment on entry to suppress PerformanceWarning from column additions
    col_names = list(df.columns)

    rename_list    = config.get('siccList',      config.get('renameList', []))
    total_list     = config.get('siccTotalList', config.get('totalList', []))
    upm_info_list   = config.get('upmInfo', config.get('columnConfigs', []))
    cdyn_list      = config.get('cdynList', [])

    # ── Step 1: Rename SICC columns ────────────────────────────────────────
    rename_map   = {}   # original_col → new_name
    used_targets = {}   # new_name → count (for deduplication)

    for pat, new_name in rename_list:
        matched_cols = [col for col in col_names if col not in rename_map and _ordered_like(col, pat)]
        if not matched_cols:
            continue
        # Rename the first match to the friendly name
        count = used_targets.get(new_name, 0)
        final_name = new_name if count == 0 else f'{new_name} ({count + 1})'
        rename_map[matched_cols[0]] = final_name
        used_targets[new_name] = count + 1
        # Track any additional matches (e.g. V1 vs V2 variants) for coalescing after rename
        if len(matched_cols) > 1:
            rename_map.setdefault('_extra_coalesce_', [])
            rename_map['_extra_coalesce_'].append((final_name, matched_cols[1:]))

    _extra_coalesce = rename_map.pop('_extra_coalesce_', [])
    df = df.rename(columns=rename_map)

    # Fill NaN in renamed columns from additional variant columns (e.g. V2 when V1 was renamed)
    for final_name, extra_cols in _extra_coalesce:
        if final_name in df.columns:
            for extra_col in extra_cols:
                if extra_col in df.columns:
                    df[final_name] = df[final_name].combine_first(
                        pd.to_numeric(df[extra_col], errors='coerce')
                    )

    # ── Step 2: Compute sum columns ────────────────────────────────────────
    # Supports grouped/derived totals, e.g.:
    #   ["SICC FULLCHIP", "SICC CORE 0.95", "SICC ATOM 0.95", "SICC RING 0.95"]
    # by resolving entries iteratively until no new sum can be created.
    sum_col_names = []
    pending_totals = [entry for entry in total_list if isinstance(entry, list) and len(entry) >= 2]
    while pending_totals:
        progressed = False
        still_pending = []

        for entry in pending_totals:
            sum_name = entry[0]
            src_cols = entry[1:]
            avail = [c for c in src_cols if c in df.columns]

            # If none of the source columns exist yet, defer this entry.
            # This allows derived totals to resolve after their dependencies are computed.
            if not avail:
                still_pending.append(entry)
                continue

            numeric_cols = pd.DataFrame(
                {c: pd.to_numeric(df[c], errors='coerce') for c in avail}
            )
            # min_count=1: row must have at least 1 non-NaN src value to produce
            # a sum; rows where ALL src cols are NaN stay NaN (not 0) so that
            # _make_row's dropna() correctly excludes them from the median.
            df[sum_name] = numeric_cols.sum(axis=1, min_count=1)
            if sum_name not in sum_col_names:
                sum_col_names.append(sum_name)
            progressed = True

        if not progressed:
            break
        pending_totals = still_pending

    # ── Step 3: UPM columns (distribution only — not in SICC heatmap) ─────
    upm_col_names: list[str] = []       # empty: UPM excluded from SICC heatmap
    _upm_dist_cols: list[str] = []      # UPM columns for distribution chart
    _upm_targets: dict = {}             # display_name → target% (from upmInfo)
    for entry in upm_info_list:
        if len(entry) < 3:
            continue
        new_name, src_pat = entry[0], entry[1]
        try:
            divisor = float(entry[2])
        except (ValueError, TypeError):
            divisor = np.nan
        src_col = _find_col(col_names, src_pat)
        if src_col and new_name not in df.columns:
            src_vals = pd.to_numeric(df[src_col], errors='coerce')
            scaled_vals = src_vals / divisor * 100 if np.isfinite(divisor) and divisor != 0 else pd.Series(np.nan, index=src_vals.index)

            # Prefer true UPM percent values as-is when source already looks like percent.
            src_valid = src_vals.dropna()
            scaled_valid = scaled_vals.dropna()
            src_pct_like = (len(src_valid) > 0 and (src_valid.between(0, 100).mean() >= 0.9))
            scaled_pct_like = (len(scaled_valid) > 0 and (scaled_valid.between(0, 100).mean() >= 0.9))

            if src_pct_like and not scaled_pct_like:
                df[new_name] = src_vals
            elif scaled_pct_like:
                df[new_name] = scaled_vals
            else:
                # Fallback to scaled behavior to preserve legacy expectation when both are ambiguous.
                df[new_name] = scaled_vals if np.isfinite(divisor) and divisor != 0 else src_vals

            _upm_dist_cols.append(new_name)
        # Extract target from 4th element if present (e.g. "94%" → 94)
        if len(entry) >= 4:
            tgt_str = str(entry[3]).replace('%', '').strip()
            try:
                _upm_targets[new_name] = float(tgt_str)
            except (ValueError, TypeError):
                pass

    # ── Step 4: CDYN columns ───────────────────────────────────────────────
    cdyn_col_names: list[str] = []
    cdyn_rename: dict[str, str] = {}

    if cdyn_list:
        for pat, friendly in cdyn_list:
            for col in col_names:
                if col not in cdyn_rename and _ordered_like(col, pat):
                    cdyn_rename[col] = friendly
                    break
        if cdyn_rename:
            df = df.rename(columns=cdyn_rename)
            cdyn_col_names = list(cdyn_rename.values())
    else:
        # Auto-detect columns that look like CDYN tests
        cdyn_col_names = [
            c for c in df.columns
            if re.search(r'cdyn', c, re.I) or
               (re.search(r'_og_', c, re.I) and re.search(r'_v1_', c, re.I))
        ]

    # ── Step 5: Identify grouping / metadata columns ───────────────────────
    # Defragment DataFrame after repeated column insertions (SICC totals, UPM, CDYN)
    df = df.copy()

    def _col(patterns: list) -> Optional[str]:
        for p in patterns:
            c = next((c for c in df.columns if p.lower() in c.lower()), None)
            if c:
                return c
        return None

    # Prefer SORT_LOT (present in all CSVs after merge) so mixed-product
    # merges don't silently drop rows that are NaN in product-specific columns
    # (e.g. LOTFROMFS only exists in some CSVs and causes groupby to skip rows).
    lot_col = next((c for c in df.columns if c.lower() == 'sort_lot'), None)
    if not lot_col:
        lot_col = _col(['lot']) if not any('slot' in c.lower() for c in df.columns if 'lot' in c.lower()) else None
    if not lot_col:
        lot_col = next((c for c in df.columns if c.lower() == 'lot' or
                        ('lot' in c.lower() and 'slot' not in c.lower())), None)
    wfr_col = (next((c for c in df.columns if 'sort_wafer' in c.lower()), None) or
               next((c for c in df.columns if c.lower() == 'wafer' or 'wafer' in c.lower()), None))
    prg_col = next((c for c in df.columns
                    if 'testprogram' in c.lower() or 'program' in c.lower()), None)
    mat_col = next((c for c in df.columns if 'material' in c.lower()), None)
    x_col   = next((c for c in df.columns if 'sort_x' in c.lower() or c.lower() == 'x'), None)
    y_col   = next((c for c in df.columns if 'sort_y' in c.lower() or c.lower() == 'y'), None)

    group_cols = [c for c in [prg_col, lot_col, wfr_col] if c]

    # ── Extra columns for shared filter panel (same CSV as bin_distribution) ──
    _date_col = (next((c for c in df.columns if 'end_date'   in c.lower()), None) or
                 next((c for c in df.columns if 'start_date' in c.lower()), None) or
                 next((c for c in df.columns if 'date'       in c.lower()), None))
    _ib_col = next((c for c in df.columns
                    if 'interface_bin' in c.lower() and 'total' not in c.lower()), None)
    _upm_med_col_fp = _upm_dist_cols[0] if _upm_dist_cols else None

    # -- Step 6: Numeric conversion --
    sicc_col_names = list(used_targets.keys()) + sum_col_names
    # deduplicate while preserving order
    seen: set = set()
    sicc_col_names = [c for c in sicc_col_names
                      if c in df.columns and not (c in seen or seen.add(c))]
    all_analysis_cols = sicc_col_names + _upm_dist_cols

    # ── Auto-detect fallback: if renameList matched nothing, scan the CSV ─
    if not sicc_col_names and not _upm_dist_cols:
        _META = {prg_col, lot_col, wfr_col, mat_col, x_col, y_col}
        _meta_kw = {'lot', 'wafer', 'program', 'material', 'x', 'y',
                    'slot', 'site', 'bin', 'pass', 'fail', 'date', 'time',
                    'id', 'index', 'seq', 'part', 'tester', 'head'}
        auto_cols = []
        for c in df.columns:
            if c in _META or c is None:
                continue
            cl = c.lower()
            if any(kw in cl for kw in _meta_kw):
                continue
            # must be numeric-ish
            sample = pd.to_numeric(df[c], errors='coerce')
            if sample.notna().sum() > len(df) * 0.3:
                auto_cols.append(c)
        # Split into SICC-like and CDYN-like based on column name hints
        auto_sicc, auto_cdyn = [], []
        for c in auto_cols:
            cl = c.upper()
            if 'UPM' in cl:
                pass  # UPM handled via upmInfo, not auto-detect
            elif 'CDYN' in cl or ('_OG_' in cl and '_V1_' in cl):
                auto_cdyn.append(c)
            else:
                auto_sicc.append(c)
        sicc_col_names = auto_sicc
        if not cdyn_col_names:
            cdyn_col_names = auto_cdyn
        all_analysis_cols = sicc_col_names + _upm_dist_cols

    for c in all_analysis_cols + cdyn_col_names:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')

    # ── Step 7: Load SICC targets ──────────────────────────────────────────
    # Priority: targets embedded in config JSON > target_csv argument
    targets: dict = {}

    # 1) Read from config dict (sicc_targets + upm_targets)
    cfg_sicc = config.get('sicc_targets', {})
    cfg_upm  = config.get('upm_targets', {})
    for name, val in cfg_sicc.items():
        try:
            targets[str(name).strip().upper()] = float(val)
        except (ValueError, TypeError):
            pass
    for name, val in cfg_upm.items():
        try:
            targets[str(name).strip().upper()] = float(val)
        except (ValueError, TypeError):
            pass

    # 2) Fallback: read from separate target CSV (legacy)
    if not targets and target_csv and Path(target_csv).is_file():
        try:
            tdf = pd.read_csv(target_csv, dtype=object)
            tc = tdf.columns.tolist()
            tn_col = next((c for c in tc if 'testname' in c.lower()), tc[0])
            tg_col = next((c for c in tc if 'target' in c.lower()),
                          tc[1] if len(tc) > 1 else None)
            if tg_col:
                for _, row in tdf.iterrows():
                    key = str(row[tn_col]).strip().upper()
                    try:
                        targets[key] = float(str(row[tg_col]).replace(',', ''))
                    except ValueError:
                        pass
        except Exception:
            pass

    # 3) CDYN targets: from config dict (keyed by friendly name from cdynList)
    cfg_cdyn_tgt = config.get('cdyn_targets', {})
    resolved_cdyn_targets: dict = {}
    for name, val in cfg_cdyn_tgt.items():
        try:
            resolved_cdyn_targets[str(name).strip()] = float(val)
        except (ValueError, TypeError):
            pass
    # merge with any explicitly passed cdyn_targets argument
    if cdyn_targets:
        resolved_cdyn_targets.update(cdyn_targets)
    # 4) Apply overrides from product config JSON — highest priority
    if override_targets:
        targets.update(override_targets)
    if override_cdyn_targets:
        resolved_cdyn_targets.update(override_cdyn_targets)
    # Merge UPM targets from upmInfo into main targets dict
    for _n, _v in _upm_targets.items():
        targets[_n.upper()] = _v

    # ── Step 8: Build SICC/CDYN → UPM die-pair mapping ────────────────────
    _pair_map: dict[str, str] = {}  # sicc_or_cdyn_col → upm_col
    # Build set of actual UPM column names in DataFrame for fuzzy fallback
    _upm_cols_in_df = set(_upm_dist_cols)
    def _resolve_upm_col(name: str) -> str | None:
        """Return actual UPM column name in df, or None."""
        if name in _upm_cols_in_df:
            return name
        # Try case-insensitive match
        nl = name.lower()
        for u in _upm_cols_in_df:
            if u.lower() == nl:
                return u
        return None

    for cfg_entry in config.get('SiccTableConfig', []):
        if len(cfg_entry) >= 4 and cfg_entry[2] and cfg_entry[3]:
            resolved = _resolve_upm_col(cfg_entry[3])
            if resolved:
                _pair_map[cfg_entry[2]] = resolved
    for cfg_entry in config.get('cdynTableConfig', []):
        if len(cfg_entry) >= 4 and cfg_entry[2] and cfg_entry[3]:
            resolved = _resolve_upm_col(cfg_entry[3])
            if resolved:
                _pair_map[cfg_entry[2]] = resolved

    # ── Step 9: Per-wafer medians + histograms ─────────────────────────────
    rows = []
    _analysis_cols_present = [c for c in all_analysis_cols if c in df.columns]
    _cdyn_cols_present = [c for c in cdyn_col_names if c in df.columns]

    def _make_row(grp: pd.DataFrame, program: str, lot: str,
                  wafer: str, material: str) -> dict:
        medians, hists, cdyn_meds = {}, {}, {}
        die_pairs: dict[str, dict] = {}
        for c in _analysis_cols_present:
            vals = grp[c].dropna().values
            if len(vals):
                medians[c] = round(float(np.median(vals)), 8)
                if build_histograms:
                    hists[c] = _make_hist(vals)
                # Populate die_pairs if this column has a paired UPM column
                upm_partner = _pair_map.get(c)
                if upm_partner and upm_partner in grp.columns:
                    # Paired: keep only rows where both SICC and UPM are valid
                    mask = grp[c].notna() & grp[upm_partner].notna()
                    s_vals = grp.loc[mask, c].values
                    u_vals = grp.loc[mask, upm_partner].values
                    if len(s_vals):
                        die_pairs[c] = {
                            's': [round(float(v), 8) for v in s_vals],
                            'u': [round(float(v), 8) for v in u_vals],
                        }
        for c in _cdyn_cols_present:
            vals = grp[c].dropna().values
            if len(vals):
                cdyn_meds[c] = round(float(np.median(vals)), 8)
                if build_histograms:
                    hists[c] = _make_hist(vals)
                # Populate die_pairs for CDYN columns too
                upm_partner = _pair_map.get(c)
                if upm_partner and upm_partner in grp.columns:
                    mask = grp[c].notna() & grp[upm_partner].notna()
                    s_vals = grp.loc[mask, c].values
                    u_vals = grp.loc[mask, upm_partner].values
                    if len(s_vals):
                        die_pairs[c] = {
                            's': [round(float(v), 8) for v in s_vals],
                            'u': [round(float(v), 8) for v in u_vals],
                        }
        return {
            'program':    program,
            'lot':        lot,
            'wafer':      wafer,
            'material':   material,
            'total':      len(grp),
            'medians':    medians,
            'hists':      hists,
            'cdyn':       cdyn_meds,
            'die_pairs':  die_pairs,
            # ── Shared filter-panel fields (same CSV → same data contract) ──
            'date':       (str(grp[_date_col].dropna().iloc[0])
                           if _date_col and _date_col in grp.columns
                           and not grp[_date_col].dropna().empty else ''),
            'binCounts':  ({str(k): int(v)
                            for k, v in grp[_ib_col].astype(str)
                              .str.extract(r'(\d+)', expand=False)
                              .dropna().value_counts().items()}
                           if _ib_col and _ib_col in grp.columns else {}),
            'upmMed':     ([round(float(np.median(grp[_upm_med_col_fp].dropna().values)), 4)]
                           if _upm_med_col_fp and _upm_med_col_fp in grp.columns
                           and len(grp[_upm_med_col_fp].dropna()) > 0 else None),
        }

    if group_cols:
        for keys, grp in df.groupby(group_cols, sort=False):
            if not isinstance(keys, tuple):
                keys = (keys,)
            kd = dict(zip(group_cols, keys))
            mat_val = ''
            if mat_col and mat_col in grp.columns:
                nn = grp[mat_col].dropna()
                if not nn.empty:
                    mat_val = str(nn.iloc[0])
            rows.append(_make_row(
                grp,
                program  = str(kd.get(prg_col, '')),
                lot      = str(kd.get(lot_col, '')),
                wafer    = str(kd.get(wfr_col, '')),
                material = mat_val,
            ))
    else:
        rows.append(_make_row(df, '', '', 'ALL', ''))

    return {
        'rows':         rows,
        'sicc_columns': sicc_col_names,
        'upm_columns':  upm_col_names,
        'cdyn_columns': cdyn_col_names,
        'targets':      targets,
        'cdyn_targets': resolved_cdyn_targets,
        'csv_name':     Path(csv_path).name,
        'group_cols': {
            'program':  prg_col,
            'lot':      lot_col,
            'wafer':    wfr_col,
            'material': mat_col,
            'x':        x_col,
            'y':        y_col,
        },
        'sicc_table_config': config.get('SiccTableConfig', []),
        'cdyn_table_config': config.get('cdynTableConfig', []),
        'upm_dist_cols':     _upm_dist_cols,
        'upm_info':          upm_info_list,
        # Die-level DataFrame with all column transforms (UPM %, SICC/CDYN renames)
        # applied.  Callers that need per-die data can use this directly.
        'df':           df,
    }
