"""class_merge.py — Load and normalize CLASS/package-test CSV data.

Responsibilities
----------------
1. Load the AQUA class CSV (CSV, CSV.GZ, or ZIP).
2. Map long AQUA column names → short canonical keys using the product config.
3. Discover PASSFLOW Vmin columns dynamically.
4. Add computed columns: ss_fc (sort SICC fullchip) and sc_fc (class SICC fullchip).
5. Merge reticle mapping (Layout, Device, LayoutX/Y, Reticle) from shared/reticle/.
6. Merge material type from shared/material/.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from _constants import _RETICLE_DIR, _MATERIAL_DIR
from csv_utils import detect_encoding, read_csv_smart, sniff_columns

# ---------------------------------------------------------------------------
# Reticle helpers (adapted from yield-dashboard apply_reticle_mapping.py)
# ---------------------------------------------------------------------------
_RETICLE_MERGE_COLS = ['Layout', 'Device', 'LayoutX', 'LayoutY', 'ReticleDieX', 'ReticleDieY', 'Reticle']
_MATERIAL_MERGE_COLS = ['TSMC_LOT', 'Material Type, Skew, BEOL Skew', 'Material Type']


def _find_collateral(directory: str, prefix6: str) -> Optional[str]:
    if not os.path.isdir(directory):
        return None
    prefix_up = prefix6.upper()
    for fname in sorted(os.listdir(directory)):
        if prefix_up in fname.upper() and fname.lower().endswith('.csv'):
            return os.path.join(directory, fname)
    return None


# ---------------------------------------------------------------------------
# Column-discovery helpers
# ---------------------------------------------------------------------------

def _find_col(all_cols: List[str], fragment: str, *, case: bool = False) -> Optional[str]:
    """Return the first column whose name contains *fragment*."""
    needle = fragment if case else fragment.upper()
    for c in all_cols:
        haystack = c if case else c.upper()
        if needle in haystack:
            return c
    return None


def _discover_vmin_columns(
    all_cols: List[str],
    vmin_freq_search: Dict[str, object],
) -> Dict[str, List[Tuple[str, str, str]]]:
    """
    Returns a dict: module → sorted list of (short_key, freq_mhz_str, raw_col).

    For module 'core', pattern 'UPSVFPASSFLOW_U1PU5_6248_CLASSHOT_CR_':
      raw col  '...CR_4.900_1'  →  short_key 'vc_4900_1', freq '4900', idx '1'
    """
    result: Dict[str, List] = {}
    for module, pattern_cfg in vmin_freq_search.items():
        patterns: List[str]
        if isinstance(pattern_cfg, list):
            patterns = [str(p) for p in pattern_cfg if p]
        elif pattern_cfg:
            patterns = [str(pattern_cfg)]
        else:
            patterns = []

        pat_ups = [p.upper() for p in patterns]
        if not pat_ups:
            continue

        prefix = {'core': 'vc', 'atom': 'va', 'ccf': 'vccf'}.get(module, f'v{module}')
        entries = []
        seen_short: set = set()
        for col in all_cols:
            col_up = col.upper()
            match_pat = next((p for p in pat_ups if p in col_up), None)
            if not match_pat:
                continue
            # Extract freq and index after the pattern
            # e.g.  '...CR_4.900_1' → freq='4900', idx='1'
            tail = col[col_up.index(match_pat) + len(match_pat):]
            m = re.match(r'^([\d.]+)_(\d+)', tail)
            if not m:
                continue
            freq_raw, idx = m.group(1), m.group(2)
            freq_mhz = str(int(float(freq_raw) * 1000))   # '4.900' → '4900'
            short_key = f'{prefix}_{freq_mhz}_{idx}'
            if short_key in seen_short:
                continue
            seen_short.add(short_key)
            entries.append((short_key, freq_mhz, idx, col))
        # Sort by (freq, idx)
        entries.sort(key=lambda t: (int(t[1]), int(t[2])))
        if entries:
            result[module] = entries
    return result


# ---------------------------------------------------------------------------
# Main normalisation
# ---------------------------------------------------------------------------

class ClassMerger:
    """Loads and normalises the class CSV, then merges collateral."""

    def __init__(
        self,
        csv_path: str,
        product_config: dict,
        log_cb=None,
    ):
        self.csv_path = str(csv_path)
        self.cfg = product_config
        self._log = log_cb or (lambda msg: print(msg))
        self.df: Optional[pd.DataFrame] = None
        self.all_cols: List[str] = []
        # Populated after normalize_columns()
        self.vmin_meta: Dict = {}   # module → [(short_key, freq_mhz, idx, raw_col), ...]
        self.available_keys: set = set()  # short keys actually present in df after normalisation

    # ------------------------------------------------------------------
    def load(self) -> 'ClassMerger':
        self._log(f'Loading: {self.csv_path}')
        self.df = read_csv_smart(self.csv_path)
        self.all_cols = list(self.df.columns)
        self._log(f'  Rows: {len(self.df):,}   Columns: {len(self.all_cols):,}')
        return self

    # ------------------------------------------------------------------
    def normalize_columns(self) -> 'ClassMerger':
        """Rename long AQUA column names to canonical short keys in self.df."""
        if self.df is None:
            raise RuntimeError('Call load() first')
        cfg = self.cfg
        rename: Dict[str, str] = {}

        # ── Identity columns ──────────────────────────────────────────
        id_map = {
            'lot':   cfg.get('sort_lot_col',   'SORT_LOT_U1.U5'),
            'wafer': cfg.get('sort_wafer_col', 'SORT_WAFER_U1.U5'),
            'sx':    cfg.get('sort_x_col',     'SORT_X_U1.U5'),
            'sy':    cfg.get('sort_y_col',     'SORT_Y_U1.U5'),
            'pkg':   cfg.get('visual_id_col',  'VISUAL_ID'),
            'dvrs':  cfg.get('devrevstep_col', ''),
        }
        for short, raw in id_map.items():
            if raw and raw in self.all_cols:
                rename[raw] = short
            else:
                # Try case-insensitive partial match as fallback
                found = _find_col(self.all_cols, raw.split('_')[0]) if raw else None
                if found:
                    rename[found] = short

        # ── Sort UPM ─────────────────────────────────────────────────
        for short, raw in cfg.get('sort_upm', {}).items():
            if raw in self.all_cols:
                rename[raw] = short
            else:
                # Pattern fallback: match on voltage suffix token
                m_volt = re.search(r'_(\d{4})_MED', raw)
                m_lib  = re.search(r'_(0107|0704)_', raw)
                if m_volt and m_lib:
                    needle = f'_{m_lib.group(1)}_'
                    volt   = f'_{m_volt.group(1)}_MED'
                    found  = next((c for c in self.all_cols if needle in c and volt in c), None)
                    if found:
                        rename[found] = short

        # ── Sort SICC ─────────────────────────────────────────────────
        for short, raw in cfg.get('sort_sicc', {}).items():
            if raw in self.all_cols:
                rename[raw] = short
            else:
                # Match on domain token (VCCATOM0, VCCCORE1, VCCR, …)
                m_dom = re.search(r'VCC(\w+)\|', raw)
                domain = m_dom.group(0) if m_dom else None
                if domain:
                    found = next((c for c in self.all_cols
                                  if domain in c and '119325_U1' in c), None)
                    if found:
                        rename[found] = short

        # ── Class SICC ───────────────────────────────────────────────
        for short, raw in cfg.get('class_sicc', {}).items():
            if raw in self.all_cols:
                rename[raw] = short
            else:
                # Match on the tag token: IA00, AT03, CCF, …
                m_tag = re.search(r'CLASSHOT_([A-Z0-9]+)-V2', raw)
                tag   = m_tag.group(1) if m_tag else None
                if tag:
                    found = next((c for c in self.all_cols
                                  if f'CLASSHOT_{tag}' in c), None)
                    if found:
                        rename[found] = short

        self.df.rename(columns=rename, inplace=True)
        self.all_cols = list(self.df.columns)
        self._log(f'  Renamed {len(rename)} columns to canonical short keys.')

        # ── Discover Vmin / PASSFLOW columns ─────────────────────────
        self.vmin_meta = _discover_vmin_columns(
            self.all_cols, cfg.get('vmin_freq_search', {})
        )
        for module, entries in self.vmin_meta.items():
            for short_key, freq_mhz, idx, raw_col in entries:
                if raw_col in self.df.columns:
                    self.df.rename(columns={raw_col: short_key}, inplace=True)
            self._log(f'  Vmin {module}: {len(entries)} columns discovered '
                      f'({len(set(e[1] for e in entries))} freq points)')

        self.all_cols = list(self.df.columns)

        # Defragment after many renames to avoid PerformanceWarning later
        self.df = self.df.copy()

        # Track which short keys actually exist
        all_short = set()
        for d in (cfg.get('sort_upm', {}), cfg.get('sort_sicc', {}), cfg.get('class_sicc', {})):
            all_short |= set(d.keys())
        for entries in self.vmin_meta.values():
            all_short |= {e[0] for e in entries}
        self.available_keys = all_short & set(self.all_cols)

        return self

    # ------------------------------------------------------------------
    def add_computed_columns(self) -> 'ClassMerger':
        """Add fullchip SICC columns (sum of individual domains)."""
        df = self.df

        # Sort SICC fullchip = ATOM0-3 + CORE0-3 + RING
        sort_parts = ['ss_a0','ss_a1','ss_a2','ss_a3',
                      'ss_c0','ss_c1','ss_c2','ss_c3','ss_r']
        present = [c for c in sort_parts if c in df.columns]
        if present:
            df['ss_fc'] = df[present].sum(axis=1, skipna=False)
            self.available_keys.add('ss_fc')

        # Class SICC fullchip = CORE0-3 + ATOM0-3 + RING
        cls_parts = ['sc_c0','sc_c1','sc_c2','sc_c3',
                     'sc_a0','sc_a1','sc_a2','sc_a3','sc_r']
        present = [c for c in cls_parts if c in df.columns]
        if present:
            df['sc_fc'] = df[present].sum(axis=1, skipna=False)
            self.available_keys.add('sc_fc')

        return self

    # ------------------------------------------------------------------
    def merge_reticle(self) -> 'ClassMerger':
        """Merge Layout/Device/Reticle from shared/reticle/ into self.df."""
        df = self.df
        if df is None:
            return self

        # Already merged?
        if 'Layout' in df.columns:
            return self

        # Determine prefix from devrevstep
        prefix6 = self._get_prefix6()
        if not prefix6:
            self._log('  [reticle] Could not determine DevRevStep prefix – skipping.')
            return self

        ret_file = _find_collateral(_RETICLE_DIR, prefix6)
        if not ret_file:
            self._log(f'  [reticle] No file found for prefix {prefix6} in {_RETICLE_DIR}')
            return self

        self._log(f'  [reticle] Merging from {ret_file}')
        try:
            rdf = pd.read_csv(ret_file, low_memory=False)
        except Exception as exc:
            self._log(f'  [reticle] ERROR reading reticle file: {exc}')
            return self

        # Sort X/Y must be present
        if 'sx' not in df.columns or 'sy' not in df.columns:
            self._log('  [reticle] sx/sy columns missing – skipping reticle merge.')
            return self

        # Normalise DieX/DieY in reticle file → SORT_X/Y offsets
        if 'DieX' in rdf.columns and 'DieY' in rdf.columns:
            ox = round((rdf['DieX'].min() + rdf['DieX'].max()) / 2)
            oy = round((rdf['DieY'].min() + rdf['DieY'].max()) / 2)
            rdf = rdf.copy()
            rdf['sx'] = rdf['DieX'] - ox
            rdf['sy'] = rdf['DieY'] - oy
        elif 'SORT_X' in rdf.columns and 'SORT_Y' in rdf.columns:
            rdf = rdf.rename(columns={'SORT_X': 'sx', 'SORT_Y': 'sy'})
        else:
            self._log('  [reticle] No DieX/SORT_X columns in reticle file – skipping.')
            return self

        merge_cols = [c for c in _RETICLE_MERGE_COLS if c in rdf.columns]
        if not merge_cols:
            return self

        df['_sx_i'] = df['sx'].round().astype('Int64')
        df['_sy_i'] = df['sy'].round().astype('Int64')
        rdf['_sx_i'] = rdf['sx'].round().astype('Int64')
        rdf['_sy_i'] = rdf['sy'].round().astype('Int64')

        pre_cols = set(df.columns)
        df = df.merge(rdf[['_sx_i','_sy_i'] + merge_cols], on=['_sx_i','_sy_i'], how='left')
        df.drop(columns=['_sx_i','_sy_i'], inplace=True)
        self.df = df
        new_cols = set(df.columns) - pre_cols
        self._log(f'  [reticle] Added columns: {sorted(new_cols)}')
        return self

    # ------------------------------------------------------------------
    def merge_material(self) -> 'ClassMerger':
        """Merge Material Type from shared/material/ into self.df."""
        df = self.df
        if df is None:
            return self
        if 'Material Type' in df.columns:
            return self

        mat_files = []
        if os.path.isdir(_MATERIAL_DIR):
            mat_files = [os.path.join(_MATERIAL_DIR, f)
                         for f in sorted(os.listdir(_MATERIAL_DIR))
                         if f.lower().endswith('.csv')]
        if not mat_files:
            self._log(f'  [material] No CSV files in {_MATERIAL_DIR}')
            return self

        # Detect lot column
        lot_col = 'lot' if 'lot' in df.columns else None
        wafer_col = 'wafer' if 'wafer' in df.columns else None
        if not lot_col:
            self._log('  [material] Lot column not found – skipping material merge.')
            return self

        merged_any = False
        for mat_file in mat_files:
            try:
                mdf = pd.read_csv(mat_file, low_memory=False)
            except Exception:
                continue
            if 'INTEL_LOT7' not in mdf.columns:
                continue

            df['_lot7'] = df[lot_col].astype(str).str[:7]
            mdf['_lot7'] = mdf['INTEL_LOT7'].astype(str).str[:7]

            merge_cols = [c for c in _MATERIAL_MERGE_COLS if c in mdf.columns]
            if not merge_cols:
                continue

            if wafer_col and 'WaferID' in mdf.columns:
                df['_wfr2'] = pd.to_numeric(df[wafer_col], errors='coerce').astype('Int64')
                mdf['_wfr2'] = pd.to_numeric(mdf['WaferID'], errors='coerce').astype('Int64')
                df = df.merge(mdf[['_lot7','_wfr2'] + merge_cols], on=['_lot7','_wfr2'], how='left')
                df.drop(columns=['_lot7','_wfr2'], errors='ignore', inplace=True)
            else:
                df = df.merge(mdf[['_lot7'] + merge_cols], on='_lot7', how='left')
                df.drop(columns=['_lot7'], errors='ignore', inplace=True)

            merged_any = True

        self.df = df
        if merged_any:
            self._log('  [material] Merged material type columns.')
        return self

    # ------------------------------------------------------------------
    def _get_prefix6(self) -> str:
        """Derive the 6-char DevRevStep prefix from the df or product config."""
        # Try from the devrevstep config column
        cfg_prefix = self.cfg.get('devrevstep_prefix', '')
        if cfg_prefix:
            return cfg_prefix[:6]

        # Try from the actual df column (renamed to 'dvrs')
        if self.df is not None and 'dvrs' in self.df.columns:
            val = self.df['dvrs'].dropna().iloc[0] if not self.df['dvrs'].dropna().empty else ''
            return str(val)[:6]
        return ''

    # ------------------------------------------------------------------
    def get_dataframe(self) -> pd.DataFrame:
        return self.df


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def load_and_prepare(
    csv_path: str,
    product_config: dict,
    log_cb=None,
) -> Tuple[pd.DataFrame, Dict]:
    """Full pipeline: load → normalize → compute → reticle → material.

    Returns (df, vmin_meta) where vmin_meta = {module: [(short_key, freq, idx, raw), ...]}.
    """
    merger = ClassMerger(csv_path, product_config, log_cb=log_cb)
    merger.load()
    merger.normalize_columns()
    merger.add_computed_columns()
    merger.merge_reticle()
    merger.merge_material()
    return merger.get_dataframe(), merger.vmin_meta
