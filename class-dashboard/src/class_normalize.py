"""class_normalize.py — Column normalization for CLASS/package-test CSV.

Takes a CSV (already material + reticle merged by add_material_type /
apply_reticle_mapping) and:
  1. Renames long AQUA column names → canonical short keys (from product config)
  2. Auto-discovers PASSFLOW Vmin columns and renames them
  3. Adds computed fullchip SICC columns (ss_fc, sc_fc)
  4. Adds 'Lot' / 'Wafer' aliases required by generate_pcm_html

Public API
----------
    normalize(df_or_path, product_config, log_cb=None) -> (df, vmin_meta)

vmin_meta structure
-------------------
    {
        "core": [("vc_4900_1", 4900, 1, "VA-IN-NA-..._CR_4.900_1"), ...],
        "atom": [("va_3800_1", 3800, 1, "VA-IN-NA-..._AT_3.800_1"), ...],
        "ccf":  [("vf_4400_1", 4400, 1, "VA-IN-NA-..._CCF_4.400_1"), ...],
    }
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from csv_utils import detect_encoding, read_csv_smart


# ---------------------------------------------------------------------------
# Module prefix map: CR → vc_, AT → va_, CCF → vf_
# ---------------------------------------------------------------------------
_MOD_PREFIX = {"core": "vc_", "atom": "va_", "ccf": "vf_"}
_MOD_TAG    = {"core": "CR",  "atom": "AT",  "ccf": "CCF"}


# ---------------------------------------------------------------------------
# Vmin column discovery
# ---------------------------------------------------------------------------

def _discover_vmin_columns(
    all_cols: List[str],
    vmin_freq_search: Dict[str, object],
) -> Dict[str, List[Tuple[str, int, int, str]]]:
    """Return {module: [(short_key, freq_mhz_int, idx, raw_col), ...]}."""
    result: Dict[str, List] = {}
    for module, search_cfg in vmin_freq_search.items():
        patterns: List[str]
        if isinstance(search_cfg, list):
            patterns = [str(p) for p in search_cfg if p]
        elif search_cfg:
            patterns = [str(search_cfg)]
        else:
            patterns = []
        if not patterns:
            continue

        prefix = _MOD_PREFIX.get(module, f"v{module[0]}_")
        entries: List[Tuple] = []
        for col in all_cols:
            if not any(p in col for p in patterns):
                continue
            # Extract freq and index from trailing _<freq>_<idx>
            m = re.search(r'_(\d+\.\d+)_(\d+)$', col)
            if not m:
                continue
            freq_str = m.group(1)          # e.g. "4.900"
            idx      = int(m.group(2))     # 1-4
            freq_mhz = int(round(float(freq_str) * 1000))  # 4900
            short_key = f"{prefix}{freq_mhz}_{idx}"
            entries.append((short_key, freq_mhz, idx, col))
        if entries:
            result[module] = entries
    return result


# ---------------------------------------------------------------------------
# Column name helpers
# ---------------------------------------------------------------------------

def _find_col(cols: List[str], needle: str) -> str | None:
    """Case-insensitive substring match; returns first hit."""
    nl = needle.lower()
    return next((c for c in cols if nl in c.lower()), None)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def normalize(
    df_or_path,
    product_config: dict,
    log_cb=None,
) -> Tuple[pd.DataFrame, Dict]:
    """Normalize a CLASS CSV/DataFrame.

    Parameters
    ----------
    df_or_path : str | Path | pd.DataFrame
        Path to merged CSV or already-loaded DataFrame.
    product_config : dict
        Parsed product config JSON (NCXSDJ-CLASS-ProductConfig-L0.json).
    log_cb : callable, optional
        Progress logger.

    Returns
    -------
    (df, vmin_meta)
        df        – normalized DataFrame with short keys + Lot/Wafer aliases
        vmin_meta – {module: [(short_key, freq_mhz, idx, raw_col), ...]}
    """

    def _log(msg: str) -> None:
        if log_cb:
            log_cb(msg)

    cfg = product_config

    # ── Load ──────────────────────────────────────────────────────────────────
    if isinstance(df_or_path, pd.DataFrame):
        df = df_or_path.copy()
    else:
        path = str(df_or_path)
        enc  = detect_encoding(path)
        df   = read_csv_smart(path, encoding=enc)
        _log(f'Normalize: loaded {len(df):,} rows, {len(df.columns)} columns')

    all_cols = list(df.columns)

    # ── Build rename map ───────────────────────────────────────────────────────
    rename: Dict[str, str] = {}

    # ── Identity / ID columns ─────────────────────────────────────────────────
    id_map = {
        'SORT_LOT':   cfg.get('sort_lot_col',   'SORT_LOT_U1.U5'),
        'SORT_WAFER': cfg.get('sort_wafer_col', 'SORT_WAFER_U1.U5'),
        'SORT_X':     cfg.get('sort_x_col',     'SORT_X_U1.U5'),
        'SORT_Y':     cfg.get('sort_y_col',     'SORT_Y_U1.U5'),
        'PKG':        cfg.get('visual_id_col',  'VISUAL_ID'),
    }
    for short, raw in id_map.items():
        if raw and raw in all_cols and short not in all_cols:
            rename[raw] = short
        elif raw and raw not in all_cols:
            found = _find_col(all_cols, raw.split('_')[0]) if raw else None
            if found and found not in rename.values():
                rename[found] = short

    # ── Sort UPM ──────────────────────────────────────────────────────────────
    for short, raw in cfg.get('sort_upm', {}).items():
        if raw in all_cols:
            rename[raw] = short
        else:
            m_volt = re.search(r'_(\d{4})_MED', raw)
            m_lib  = re.search(r'_(0107|0704)_', raw)
            if m_volt and m_lib:
                found = next(
                    (c for c in all_cols
                     if f'_{m_lib.group(1)}_' in c and f'_{m_volt.group(1)}_MED' in c
                     and c not in rename),
                    None,
                )
                if found:
                    rename[found] = short

    # ── Sort SICC ─────────────────────────────────────────────────────────────
    for short, raw in cfg.get('sort_sicc', {}).items():
        if raw in all_cols:
            rename[raw] = short
        else:
            m_dom = re.search(r'VCC(\w+)\|', raw)
            domain = m_dom.group(0) if m_dom else None
            if domain:
                found = next(
                    (c for c in all_cols
                     if domain in c and '119325_U1' in c and c not in rename),
                    None,
                )
                if found:
                    rename[found] = short

    # ── Class SICC ────────────────────────────────────────────────────────────
    for short, raw in cfg.get('class_sicc', {}).items():
        if raw in all_cols:
            rename[raw] = short
        else:
            m_tag = re.search(r'CLASSHOT_([A-Z0-9]+)-V2_Value', raw)
            tag   = m_tag.group(1) if m_tag else None
            if tag:
                found = next(
                    (c for c in all_cols
                     if f'CLASSHOT_{tag}-V2_Value' in c and c not in rename),
                    None,
                )
                if found:
                    rename[found] = short

    # ── Class SICC Temperature ─────────────────────────────────────────────────
    # Rename CLASSHOT_<TAG>-V*_Temperature columns to "CLASS SICC TEMP <label>"
    # matching the same TOKEN tags used in class_sicc config.
    # Prefer V2 over V1 for each tag.
    _temp_tag_to_short = {
        'IA00': 'CLASS SICC TEMP CORE0', 'IA01': 'CLASS SICC TEMP CORE1',
        'IA02': 'CLASS SICC TEMP CORE2', 'IA03': 'CLASS SICC TEMP CORE3',
        'AT00': 'CLASS SICC TEMP ATOM0', 'AT01': 'CLASS SICC TEMP ATOM1',
        'AT02': 'CLASS SICC TEMP ATOM2', 'AT03': 'CLASS SICC TEMP ATOM3',
        'CCF':  'CLASS SICC TEMP RING',
    }
    for tag, short_temp in _temp_tag_to_short.items():
        if short_temp in all_cols:
            continue
        # Try V2 first, then V1
        found_temp = None
        for v in ('V2', 'V1'):
            found_temp = next(
                (c for c in all_cols
                 if f'CLASSHOT_{tag}-{v}_Temperature' in c and c not in rename),
                None,
            )
            if found_temp:
                break
        if found_temp:
            rename[found_temp] = short_temp

    df = df.copy()   # defragment before renames
    df.rename(columns=rename, inplace=True)
    all_cols = list(df.columns)
    _log(f'Normalize: renamed {len(rename)} columns to canonical short keys')

    # ── Discover Vmin / PASSFLOW columns ──────────────────────────────────────
    vmin_meta = _discover_vmin_columns(all_cols, cfg.get('vmin_freq_search', {}))

    # Build short_key -> [raw_col, ...] for all raw cols present in df
    # (multiple raw cols per short_key can arise when CSVs from different TPs are concatenated)
    from collections import defaultdict as _ddict
    _sk_to_raws: dict = _ddict(list)
    for module, entries in vmin_meta.items():
        for short_key, freq_mhz, idx, raw_col in entries:
            if raw_col in df.columns and raw_col not in _sk_to_raws[short_key]:
                _sk_to_raws[short_key].append(raw_col)

    # Coalesce into a single column per short_key, then drop the raw cols
    for short_key, raw_cols in _sk_to_raws.items():
        if not raw_cols:
            continue
        if len(raw_cols) == 1:
            df.rename(columns={raw_cols[0]: short_key}, inplace=True)
        else:
            # Combine all raw cols (fill NaN from left to right), rename first
            combined = df[raw_cols[0]].copy()
            for other in raw_cols[1:]:
                combined = combined.combine_first(df[other])
            df.drop(columns=raw_cols, inplace=True)
            df[short_key] = combined

    for module, entries in vmin_meta.items():
        n_unique = len(set(e[0] for e in entries))
        n_freqs  = len(set(e[1] for e in entries))
        _log(f'Normalize: Vmin {module}: {n_unique} columns ({n_freqs} freq points)')

    df = df.copy()   # defragment after many renames
    all_cols = list(df.columns)

    # ── Computed: fullchip SICC ────────────────────────────────────────────────
    sort_parts = ['ss_a0','ss_a1','ss_a2','ss_a3',
                  'ss_c0','ss_c1','ss_c2','ss_c3','ss_r']
    present = [c for c in sort_parts if c in all_cols]
    if present and 'ss_fc' not in all_cols:
        df['ss_fc'] = df[present].apply(pd.to_numeric, errors='coerce').sum(axis=1, skipna=False)

    cls_parts = ['sc_c0','sc_c1','sc_c2','sc_c3',
                 'sc_a0','sc_a1','sc_a2','sc_a3','sc_r']
    present = [c for c in cls_parts if c in all_cols]
    if present and 'sc_fc' not in all_cols:
        df['sc_fc'] = df[present].apply(pd.to_numeric, errors='coerce').sum(axis=1, skipna=False)

    # ── Lot / Wafer aliases for generate_pcm_html ─────────────────────────────
    # generate_pcm_html groups by 'Lot' and 'Wafer' (capital)
    if 'Lot' not in df.columns:
        _lot_src = next(
            (c for c in ['SORT_LOT', 'Lot_119325_U1.U5', 'Lot_6248_CLASSHOT']
             if c in df.columns),
            None,
        )
        if _lot_src:
            df['Lot'] = df[_lot_src]

    if 'Wafer' not in df.columns:
        _wfr_src = next(
            (c for c in ['SORT_WAFER', 'SORT_WAFER_U1.U5']
             if c in df.columns),
            None,
        )
        if _wfr_src:
            df['Wafer'] = df[_wfr_src]

    # Ensure Material and Layout columns exist (may come from merges)
    if 'Material' not in df.columns:
        # Try common variants from add_material_type
        for _mc in ['Material Type', 'Material Type, Skew, BEOL Skew']:
            if _mc in df.columns:
                df['Material'] = df[_mc]
                break
        else:
            df['Material'] = ''

    if 'Layout' not in df.columns:
        df['Layout'] = ''

    # ── DLCP decoding ─────────────────────────────────────────────────────────
    # Config is under cfg["bin_matrix"]["DLCP"]
    if 'DLCP' not in df.columns:
        _bm_dlcp    = cfg.get('bin_matrix', {}).get('DLCP', {})
        _dlcp_start = _bm_dlcp.get('dlcpExtractStart')
        _dlcp_len   = _bm_dlcp.get('dlcpExtractLength')
        _dlcp_map   = _bm_dlcp.get('dlcpMap', {})
        _drev_pat   = _bm_dlcp.get('devRevStepPattern', '')
        # Locate DevRevStep column — match by pattern prefix (strip wildcard)
        _drev_prefix = _drev_pat.split('*')[0].lower() if _drev_pat else 'devrevstep'
        _drev_col = next(
            (c for c in df.columns if c.lower().startswith(_drev_prefix)),
            None,
        )
        if _drev_col is None:
            # Fallback: exact devrevstep_col from top-level config
            _drev_raw = cfg.get('devrevstep_col', '')
            if _drev_raw and _drev_raw in df.columns:
                _drev_col = _drev_raw
        if _drev_col and _dlcp_start is not None and _dlcp_len:
            s, l = int(_dlcp_start), int(_dlcp_len)
            df['DLCP'] = (
                df[_drev_col]
                .astype(str)
                .str[s:s + l]
                .map(_dlcp_map)
                .fillna('')
            )
            _log(f'Normalize: DLCP decoded from "{_drev_col}" '
                 f'[{s}:{s+l}] — {df["DLCP"].value_counts().to_dict()}')
        else:
            if not _drev_col:
                _log('Normalize: DLCP skipped — no DevRevStep column found')
            else:
                _log('Normalize: DLCP skipped — dlcpExtractStart/Length not in bin_matrix.DLCP config')

    # Deduplicate vmin_meta: keep one entry per short_key (for downstream HTML)
    vmin_meta_clean: Dict = {}
    for module, entries in vmin_meta.items():
        seen: set = set()
        deduped = []
        for short_key, freq_mhz, idx, raw_col in entries:
            if short_key not in seen:
                seen.add(short_key)
                deduped.append((short_key, freq_mhz, idx, raw_col))
        if deduped:
            vmin_meta_clean[module] = deduped

    _log(f'Normalize: done — {len(df):,} rows, {len(df.columns)} cols')
    return df, vmin_meta_clean
