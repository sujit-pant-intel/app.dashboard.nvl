# =============================================================================
# add_material_type.py  -  Material type merge for yield CSV
# =============================================================================
#
# Steps (mirrors JSL add_material_type_nvl.jsl logic):
# // 1. Take the yield CSV (passed as csv_path).
# // 2. Extract DevRevStep_* column, use first 6 chars as product prefix.
# // 3. In the collateral/material folder, find CSV whose filename contains
# //    that 6-char prefix (same pattern as reticle mapping lookup).
# // 4. From the yield CSV, derive:
# //       LOT7 = first 7 chars of the lot column (Lot_119325, Lot_132322, etc.)
# //       WAFER2 = last 2 chars of the wafer column (SORT_WAFER), as integer
# // 5. Merge material data into yield CSV on LOT7 == INTEL_LOT7 and
# //    WAFER2 == WaferID.
# //    Columns added: TSMC_LOT, Material Type Skew BEOL Skew, Material Type,
# //    Production Lot.
# // 6. Return the path to the updated CSV for further analysis.
# =============================================================================

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from csv_utils import (
    CHUNK_SIZE,
    detect_encoding,
    sniff_columns,
)


# Columns to merge from the material lookup table
MATERIAL_MERGE_COLS = [
    'TSMC_LOT',
    'Material Type, Skew, BEOL Skew',
    'Material Type',
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_material_candidates(collateral_dir: str, prefix6: str) -> list[str]:
    """Return all CSVs in collateral_dir, sorted alphabetically.

    The ``prefix6`` argument is kept for backwards compatibility but is no
    longer used for filename filtering.  Material file names use device codes
    (e.g. ``8PF6CV``) while the DevRevStep column uses product codes (e.g.
    ``NCXSDJ``) — the two share no common prefix.  Lot-number matching in
    Step 6 naturally selects the files that cover the lots present in the
    yield CSV, so it is safe to return *all* CSV files here.
    """
    if not os.path.isdir(collateral_dir):
        return []
    return [
        os.path.join(collateral_dir, fname)
        for fname in sorted(os.listdir(collateral_dir))
        if fname.lower().endswith('.csv')
    ]


def _detect_lot_wafer_columns(all_cols: list[str]) -> tuple[str | None, str | None]:
    """Detect the lot and wafer column names from the CSV header.
    Returns (lot_col, wafer_col) or (None, None) if not found."""
    # Priority order matches the JSL logic
    lot_col = None
    wafer_col = None

    # SORT_LOT_U1.U5 is preferred: values are already 7-char sort lot IDs
    if 'SORT_LOT_U1.U5' in all_cols and 'SORT_WAFER_U1.U5' in all_cols:
        return 'SORT_LOT_U1.U5', 'SORT_WAFER_U1.U5'

    # Fallback to CLASS lot column (9-char; first 7 match INTEL_LOT7)
    if 'Lot_119325_U1.U5' in all_cols and 'SORT_WAFER_U1.U5' in all_cols:
        return 'Lot_119325_U1.U5', 'SORT_WAFER_U1.U5'

    # Standard SORT columns
    if 'SORT_WAFER' in all_cols:
        wafer_col = 'SORT_WAFER'
        if 'Lot_119325' in all_cols:
            lot_col = 'Lot_119325'
        elif 'Lot_132322' in all_cols:
            lot_col = 'Lot_132322'
        elif 'Lot_1331195' in all_cols:
            lot_col = 'Lot_1331195'

    # Fallback to generic LOT/WAFER
    if not lot_col:
        for c in all_cols:
            if c.upper() == 'LOT' or c.lower().startswith('lot_'):
                lot_col = c
                break
    if not wafer_col:
        for c in all_cols:
            if c.upper() == 'WAFER' or c.upper() == 'SORT_WAFER':
                wafer_col = c
                break

    return lot_col, wafer_col


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def add_material_type(
    csv_path: str,
    collateral_dir: str,
    output_dir: str | None = None,
    log_cb=None,
) -> str:
    """Add material type columns to *csv_path* and return the path to the
    updated CSV.

    The original CSV is never modified.  The enriched copy is written to
    *output_dir* (creating it if needed).  If *output_dir* is None a temp
    directory is used and the caller is responsible for cleanup.

    Parameters
    ----------
    csv_path:
        Path to the yield CSV.
    collateral_dir:
        Folder containing lot-definition CSV files (collateral/material).
    output_dir:
        Directory to write the enriched CSV into.  Defaults to a temp dir.
    log_cb:
        Optional ``callable(msg: str)`` for progress messages.

    Returns
    -------
    str
        Path to the enriched CSV in output_dir, or original csv_path on
        error/skip.
    """

    def _log(msg: str) -> None:
        if log_cb:
            log_cb(msg)
        else:
            print(msg)

    # ── Guard: CSV must exist ─────────────────────────────────────────────────
    if not os.path.isfile(csv_path):
        _log(f'Material type: CSV not found: {csv_path}')
        return csv_path

    # ── Detect encoding ───────────────────────────────────────────────────────
    encoding = detect_encoding(csv_path)

    # ── Step 1: Sniff header ──────────────────────────────────────────────────
    all_cols = sniff_columns(csv_path, encoding=encoding)
    if not all_cols:
        _log('Material type: could not read CSV header – skipping.')
        return csv_path

    # ── Pre-check: only skip if ALL material columns are FULLY populated (zero nulls) ──
    # Do NOT skip when material is only partially filled (e.g. P0 rows have data but
    # R0 rows don't after a multi-CSV merge) — we need to fill the empty rows.
    mat_cols = [c for c in all_cols if 'material' in c.lower()]
    if mat_cols:
        try:
            df_check = pd.read_csv(csv_path, usecols=mat_cols,
                                   encoding=encoding, low_memory=False)
            if df_check[mat_cols].notna().all().all():
                _log('Material type: all material columns fully populated – skipping.')
                return csv_path
            elif df_check[mat_cols].notna().any().any():
                _log('Material type: material columns partially populated – will fill empty rows.')
            else:
                _log('Material type: Material columns present but empty – will drop and re-merge.')
        except Exception:
            pass

    # ── Step 2: Get ALL unique DevRevStep prefixes across all rows ───────────
    dev_rev_col: str | None = next(
        (c for c in all_cols if c.lower().startswith('devrevstep')), None)
    if not dev_rev_col:
        _log('Material type: no DevRevStep_* column found – skipping.')
        return csv_path

    try:
        df_drv = pd.read_csv(csv_path, usecols=[dev_rev_col],
                             encoding=encoding, low_memory=False)
    except Exception as exc:
        _log(f'Material type: failed to read DevRevStep column: {exc}')
        return csv_path

    non_null_drv = df_drv[dev_rev_col].dropna().astype(str)
    if non_null_drv.empty:
        _log('Material type: DevRevStep_* column is empty – skipping.')
        return csv_path

    # Collect all unique 6-char prefixes present in the CSV
    all_prefixes = list(dict.fromkeys(v[:6] for v in non_null_drv.unique() if len(v) >= 6))
    _log(f'Material type: DevRevStep prefixes found = {all_prefixes}')

    # ── Step 3: Find all candidate material files for every prefix ────────────
    seen_files: set[str] = set()
    candidates: list[str] = []
    for prefix6 in all_prefixes:
        for f in find_material_candidates(collateral_dir, prefix6):
            if f not in seen_files:
                seen_files.add(f)
                candidates.append(f)
    if not candidates:
        _log(f'Material type: no collateral files found for prefixes {all_prefixes} in {collateral_dir}')
        return csv_path

    _log(f'Material type: {len(candidates)} candidate file(s): {[os.path.basename(c) for c in candidates]}')

    # ── Step 4: Detect lot/wafer columns in yield CSV ─────────────────────────
    lot_col, wafer_col = _detect_lot_wafer_columns(all_cols)
    if not lot_col or not wafer_col:
        _log(
            f'Material type: could not detect lot/wafer columns '
            f'(found lot={lot_col!r}, wafer={wafer_col!r}) – skipping.'
        )
        return csv_path

    _log(f'Material type: using lot={lot_col!r}, wafer={wafer_col!r}')

    # ── Step 5: Read yield CSV, derive LOT7/WAFER2 ───────────────────────────
    try:
        df = pd.read_csv(csv_path, encoding=encoding, low_memory=False)
    except Exception as exc:
        _log(f'Material type: failed to read yield CSV: {exc}')
        return csv_path

    df = df.copy()  # defragment before column assignments to suppress PerformanceWarning

    # Derive LOT7 = first 7 characters of lot column (kept in output)
    df['LOT7'] = df[lot_col].astype(str).str[:7]

    # Derive WAFER2 = wafer number as integer, then mod 100.
    # Must convert to numeric first to avoid float-string artifacts
    # (e.g. pandas may store 202 as 202.0 → '202.0'[-2:] = '.0' → 0).
    _wafer_num = pd.to_numeric(df[wafer_col], errors='coerce')
    df['WAFER2'] = (_wafer_num.round().astype('Int64') % 100).astype(float)

    # Also add Production Lot column
    if 'Production Lot' not in df.columns:
        df['Production Lot'] = df[lot_col]

    # Save and drop ALL existing material merge columns before the merge to prevent
    # pandas from creating _x/_y suffix columns.  We restore original non-null values
    # after the merge so pre-existing data (e.g. P0 rows in a merged CSV) is preserved.
    _orig_mat_vals: dict = {}
    for _mc in MATERIAL_MERGE_COLS:
        if _mc in df.columns:
            _orig_mat_vals[_mc] = df[_mc].copy()
            df.drop(columns=[_mc], inplace=True)
    if _orig_mat_vals:
        _log(f'Material type: saving existing columns for restore after merge: {list(_orig_mat_vals)}')

    # ── Step 6: Collect lookup rows from ALL matching candidate files ────────
    # Each material CSV covers different lot numbers; we must search every file
    # so wafers from different lots all get their material type populated.
    lot7_vals = set(df['LOT7'].dropna().unique())
    lookup_frames: list[pd.DataFrame] = []
    used_files: list[str] = []
    all_merge_cols: set[str] = set()

    for material_file in candidates:
        try:
            df_material = pd.read_csv(material_file)
        except Exception as exc:
            _log(f'Material type: could not read {os.path.basename(material_file)}: {exc}')
            continue

        if 'INTEL_LOT7' not in df_material.columns or 'WaferID' not in df_material.columns:
            _log(f'Material type: {os.path.basename(material_file)} missing INTEL_LOT7/WaferID – skipping.')
            continue

        merge_cols_available = [c for c in MATERIAL_MERGE_COLS if c in df_material.columns]
        if not merge_cols_available:
            _log(f'Material type: {os.path.basename(material_file)} has no merge columns – skipping.')
            continue

        df_material['_WaferID_num'] = pd.to_numeric(df_material['WaferID'], errors='coerce')
        df_lookup = df_material[['INTEL_LOT7', '_WaferID_num'] + merge_cols_available].copy()
        # Truncate INTEL_LOT7 to first 7 chars to match yields that use only 7-char lot IDs
        df_lookup['INTEL_LOT7'] = df_lookup['INTEL_LOT7'].astype(str).str[:7]
        df_lookup = df_lookup.drop_duplicates(subset=['INTEL_LOT7', '_WaferID_num'])

        mat_lots = set(df_lookup['INTEL_LOT7'].dropna().unique())
        if not lot7_vals.intersection(mat_lots):
            _log(f'Material type: {os.path.basename(material_file)} – no matching lots, skipping.')
            continue

        lookup_frames.append(df_lookup)
        used_files.append(material_file)
        all_merge_cols.update(merge_cols_available)
        _log(f'Material type: {os.path.basename(material_file)} – matched lots, adding to lookup.')

    df_merged = None
    used_file = used_files[0] if used_files else None

    if lookup_frames:
        # Combine all matching lookup tables; later rows win on duplicate keys
        merge_cols_available = [c for c in MATERIAL_MERGE_COLS if c in all_merge_cols]
        combined_lookup = pd.concat(lookup_frames, ignore_index=True, sort=False)
        # Fill any missing merge columns with NaN so concat doesn't drop them
        for _mc in merge_cols_available:
            if _mc not in combined_lookup.columns:
                combined_lookup[_mc] = None
        combined_lookup = combined_lookup.drop_duplicates(
            subset=['INTEL_LOT7', '_WaferID_num'], keep='last'
        )
        # Split lookup into rows with WaferID and rows without (lot-level wildcard)
        _lkp_with_wafer = combined_lookup[combined_lookup['_WaferID_num'].notna()]
        _lkp_lot_only   = combined_lookup[combined_lookup['_WaferID_num'].isna()].drop_duplicates(subset=['INTEL_LOT7'], keep='last')

        # Pass 1: merge on lot + wafer (precise)
        df_merged = df.merge(
            _lkp_with_wafer[['INTEL_LOT7', '_WaferID_num'] + merge_cols_available],
            left_on=['LOT7', 'WAFER2'],
            right_on=['INTEL_LOT7', '_WaferID_num'],
            how='left',
        )
        df_merged.drop(columns=['INTEL_LOT7', '_WaferID_num'], inplace=True, errors='ignore')

        # Pass 2: for rows still missing material, fall back to lot-only wildcard rows
        if not _lkp_lot_only.empty and merge_cols_available:
            _still_missing = df_merged[merge_cols_available[0]].isna()
            if _still_missing.any():
                _lkp_lo = _lkp_lot_only[['INTEL_LOT7'] + merge_cols_available].rename(columns={'INTEL_LOT7': '_lot7_lo'})
                _fallback = df_merged.loc[_still_missing, ['LOT7']].merge(
                    _lkp_lo, left_on='LOT7', right_on='_lot7_lo', how='left'
                ).drop(columns=['LOT7', '_lot7_lo'])
                _fallback.index = df_merged.index[_still_missing]
                for _mc in merge_cols_available:
                    if _mc in _fallback.columns:
                        df_merged.loc[_still_missing, _mc] = _fallback[_mc].values
        # Restore original non-null material values: rows that already had material
        # data (e.g. P0 rows) keep their original values; empty rows (e.g. R0 rows)
        # get the newly merged values.
        df_merged = df_merged.reset_index(drop=True)
        for _mc, _orig_s in _orig_mat_vals.items():
            _orig_s = _orig_s.reset_index(drop=True)
            if _mc in df_merged.columns:
                # Keep original where non-null, else use merged result
                df_merged[_mc] = _orig_s.where(_orig_s.notna(), df_merged[_mc])
            else:
                df_merged[_mc] = _orig_s
        if len(used_files) > 1:
            _log(f'Material type: merged lookup from {len(used_files)} files: {[os.path.basename(f) for f in used_files]}')

    if df_merged is None:
        _log(f'Material type: no matching lots found in any candidate file – leaving columns empty.')
        # Still add empty columns so downstream code doesn't break
        for col in MATERIAL_MERGE_COLS:
            if col not in df.columns:
                df[col] = None
        df_merged = df

    n_matched = df_merged[MATERIAL_MERGE_COLS[0]].notna().sum() if MATERIAL_MERGE_COLS[0] in df_merged.columns else 0
    n_before = len(df_merged)
    if used_files:
        _log(f'Material type: used {len(used_files)} file(s), matched {n_matched}/{n_before} rows.')

    # ── Step 7: Write to output_dir (never modify the original) ─────────────
    try:
        if output_dir is None:
            import tempfile as _tmp
            output_dir = _tmp.mkdtemp(prefix='material_tmp_')
        os.makedirs(output_dir, exist_ok=True)
        out_name = Path(csv_path).stem + '_material_merged.csv'  # intermediate; reticle step renames to _reticle_material.csv
        out_path = os.path.join(output_dir, out_name)
        df_merged.to_csv(out_path, index=False, encoding=encoding)
        _log(f'Material type: enriched CSV saved to {out_path}')
        return out_path
    except Exception as exc:
        _log(f'Material type: failed to write CSV: {exc}')
        return csv_path
