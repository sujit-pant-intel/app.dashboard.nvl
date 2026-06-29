# =============================================================================
# apply_reticle_mapping.py  -  Reticle mapping merge for yield CSV
# =============================================================================
#
# Steps:
# // 1. Take the CSV defined in "Output CSV" in GUI (passed as csv_path).
# // 2. Extract Data from DevRevStep_* column. All rows will have same value.
# //    Use the first non-null row value to identify the product prefix.
# // 3. In the Collateral folder, look for filename that contains the 1st 6
# //    characters of the DevRevStep_* value. Open that file.
# // 4. Rename DieX and DieY columns as SORT_X and SORT_Y.
# // 5. Convert to known center die using:
# //       offset_x = round((DieX.min() + DieX.max()) / 2)
# //       offset_y = round((DieY.min() + DieY.max()) / 2)
# //       SORT_X = DieX - offset_x
# //       SORT_Y = DieY - offset_y
# // 6. Merge reticle data into the output CSV based on SORT_X and SORT_Y.
# //    Only merge fields: Layout, Device, LayoutX, LayoutY, ReticleDieX,
# //    ReticleDieY, Reticle. If these fields are already present, skip merge.
# // 7. Use the merged CSV for further analysis. Copy to output folder;
# //    zip the output folder once all analysis is complete.
# // 8. Provide checkbox to save merged file. If checked, save as
# //    <Output CSV>_reticle_merged.csv in the same folder as Output CSV.
# //    Default is false.
# =============================================================================

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pandas as pd

from csv_utils import (
    CHUNK_SIZE,
    detect_encoding,
    iter_chunks,
    read_csv_smart,
    sniff_columns,
)


# Reticle mapping columns merged into the output CSV
RETICLE_MERGE_COLS = [
    'Layout', 'Device', 'LayoutX', 'LayoutY',
    'ReticleDieX', 'ReticleDieY', 'Reticle',
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_collateral_file(collateral_dir: str, prefix6: str) -> str | None:
    """Return the first file in collateral_dir whose name contains prefix6
    (case-insensitive).  Returns None when the folder is missing or empty."""
    if not os.path.isdir(collateral_dir):
        return None
    prefix_upper = prefix6.upper()
    for fname in sorted(os.listdir(collateral_dir)):
        if prefix_upper in fname.upper():
            return os.path.join(collateral_dir, fname)
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def apply_reticle_mapping(
    csv_path: str,
    collateral_dir: str,
    save_merged: bool = False,
    output_dir: str | None = None,
    log_cb=None,
    chunksize: int | None = None,
) -> str:
    """Apply reticle mapping to *csv_path* and return the path to the
    CSV that should be used for further analysis.

    Parameters
    ----------
    csv_path:
        Path to the AQUA output CSV ("Output CSV" from the GUI).
    collateral_dir:
        Folder containing Reticle_Mapping CSV files.
    save_merged:
        If True, also save a ``<stem>_reticle_merged.csv`` next to *csv_path*.
    output_dir:
        If provided, write the merged CSV here for use by downstream
        analysis steps.  The caller should zip this folder when done.
    log_cb:
        Optional ``callable(msg: str)`` for progress messages.
    chunksize:
        Rows per chunk when streaming the AQUA CSV.  Defaults to
        ``csv_utils.CHUNK_SIZE`` (100 000).

    Returns
    -------
    str
        Path to the CSV intended for further analysis:
        - *output_dir* copy when output_dir is given
        - the original *csv_path* on any error or when merge is skipped
    """

    def _log(msg: str) -> None:
        if log_cb:
            log_cb(msg)
        else:
            print(msg)

    _chunksize = chunksize or CHUNK_SIZE

    # ── Guard: CSV must exist ─────────────────────────────────────────────────
    if not os.path.isfile(csv_path):
        _log(f'Reticle mapping: CSV not found: {csv_path}')
        return csv_path

    # ── Detect encoding once — reused for all reads of this file ─────────────
    encoding = detect_encoding(csv_path)

    # ── Step 1 & 2: Sniff header, find DevRevStep_* col, read ALL unique values ─
    # Peak RAM: header only (no data rows loaded yet).
    all_cols = sniff_columns(csv_path, encoding=encoding)
    if not all_cols:
        _log('Reticle mapping: could not read CSV header – skipping.')
        return csv_path

    dev_rev_col: str | None = next(
        (c for c in all_cols if c.lower().startswith('devrevstep')), None)
    if not dev_rev_col:
        _log('Reticle mapping: no DevRevStep_* column found – skipping.')
        return csv_path

    # ── Step 6 pre-check: skip only when ALL merge cols are present AND fully
    # populated (no null rows needing enrichment).  A previous partial-run
    # (e.g. only one product was processed) leaves null rows that must still
    # be merged – so we only bail when coverage is complete.
    if all(c in all_cols for c in RETICLE_MERGE_COLS):
        # Quick sample-check: read a modest slice and see whether any row
        # has null in the first merge col.  If fully populated, skip merge.
        try:
            _sample = pd.read_csv(csv_path, usecols=[RETICLE_MERGE_COLS[0]],
                                  encoding=encoding, low_memory=False)
            if _sample[RETICLE_MERGE_COLS[0]].notna().all():
                _log('Reticle mapping: all merge columns already fully populated – skipping merge.')
                return csv_path
            _log('Reticle mapping: merge columns present but some rows are null – re-merging.')
        except Exception:
            pass  # on any error, proceed with merge

    # Read the full DevRevStep column to collect all unique prefixes
    try:
        df_drv = pd.read_csv(csv_path, usecols=[dev_rev_col],
                             encoding=encoding, low_memory=False)
    except Exception as exc:
        _log(f'Reticle mapping: failed to read DevRevStep column: {exc}')
        return csv_path

    non_null_drv = df_drv[dev_rev_col].dropna().astype(str)
    if non_null_drv.empty:
        _log('Reticle mapping: DevRevStep_* column is empty – skipping.')
        return csv_path

    # Collect all unique 6-char prefixes (preserving order)
    all_prefixes = list(dict.fromkeys(v[:6] for v in non_null_drv.unique() if len(v) >= 6))
    _log(f'Reticle mapping: DevRevStep prefixes found = {all_prefixes}')

    # ── Step 3: Build a combined reticle lookup for all prefixes ─────────────
    # Each prefix maps to its own reticle file; offsets are computed per-file.
    # We store a dict: prefix6 → df_reticle (with SORT_X, SORT_Y already computed)
    prefix_reticle: dict[str, pd.DataFrame] = {}
    for prefix6 in all_prefixes:
        collateral_file = find_collateral_file(collateral_dir, prefix6)
        if not collateral_file:
            _log(f'Reticle mapping: no collateral file for prefix {prefix6!r} – rows with this prefix will be unmatched.')
            continue
        _log(f'Reticle mapping: prefix {prefix6!r} → {os.path.basename(collateral_file)}')
        try:
            df_ret = pd.read_csv(collateral_file)
        except Exception as exc:
            _log(f'Reticle mapping: failed to read {os.path.basename(collateral_file)}: {exc}')
            continue
        if 'DieX' not in df_ret.columns or 'DieY' not in df_ret.columns:
            _log(f'Reticle mapping: {os.path.basename(collateral_file)} missing DieX/DieY – skipping.')
            continue
        df_ret = df_ret.copy()
        die_x = df_ret['DieX'].astype(float)
        die_y = df_ret['DieY'].astype(float)
        offset_x = round((die_x.min() + die_x.max()) / 2)
        offset_y = round((die_y.min() + die_y.max()) / 2)
        _log(f'Reticle mapping: {prefix6!r} offsets  offset_x={offset_x}, offset_y={offset_y}')
        df_ret['SORT_X'] = (die_x - offset_x).astype(int)
        df_ret['SORT_Y'] = (die_y - offset_y).astype(int)
        df_ret = df_ret.drop(columns=['DieX', 'DieY'])
        avail = [c for c in RETICLE_MERGE_COLS if c in df_ret.columns]
        if not avail:
            _log(f'Reticle mapping: {os.path.basename(collateral_file)} has no merge columns – skipping.')
            continue
        keep = ['SORT_X', 'SORT_Y'] + avail
        df_ret = df_ret[keep].drop_duplicates(subset=['SORT_X', 'SORT_Y'])
        prefix_reticle[prefix6] = df_ret

    if not prefix_reticle:
        _log('Reticle mapping: no usable collateral files found – skipping.')
        return csv_path

    # If all prefixes share the same reticle (identical offsets / same file),
    # build one combined lookup (tagged with _prefix6 column internally).
    # For the streaming merge we need to know which prefix each row belongs to.
    # We add a temporary column _R_PREFIX to the chunk for joining.

    available_merge_cols = sorted(
        {c for df_r in prefix_reticle.values() for c in df_r.columns if c not in ('SORT_X', 'SORT_Y')}
    )
    available_merge_cols = [c for c in RETICLE_MERGE_COLS if c in available_merge_cols]

    # ── Check SORT_X / SORT_Y exist in output CSV ─────────────────────────────
    if 'SORT_X' not in all_cols or 'SORT_Y' not in all_cols:
        _log('Reticle mapping: output CSV missing SORT_X/SORT_Y columns – skipping merge.')
        return csv_path

    # ── Step 7: Determine output paths ───────────────────────────────────────
    merged_name = Path(csv_path).stem + '_reticle_material.csv'
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        analysis_csv = os.path.join(output_dir, merged_name)
    else:
        analysis_csv = str(Path(csv_path).with_name(merged_name))

    # ── Step 6 (streaming): merge AQUA CSV in chunks ─────────────────────────
    # When multiple prefixes are present each chunk row is matched to the
    # correct reticle lookup via its prefix6.  If only one prefix exists the
    # fast single-table path is used.

    partial_reticle_cols = [c for c in RETICLE_MERGE_COLS if c in all_cols]

    first_chunk = True
    matched_total = 0
    row_total = 0

    _log(f'Reticle mapping: streaming {_chunksize:,}-row chunks → {analysis_csv}')

    try:
        for chunk in iter_chunks(csv_path, chunksize=_chunksize, encoding=encoding):
            if partial_reticle_cols:
                chunk = chunk.drop(columns=partial_reticle_cols, errors='ignore')

            # Always split by prefix so rows from product A are never merged
            # against product B's reticle table (the old single_prefix fast-path
            # caused one product's data to eclipse the other when only one
            # collateral file was found for multiple DevRevStep prefixes).
            chunk = chunk.copy()
            chunk['_R_PREFIX'] = chunk[dev_rev_col].astype(str).str[:6]
            parts = []
            for pfx, sub in chunk.groupby('_R_PREFIX', sort=False):
                df_r = prefix_reticle.get(pfx)
                if df_r is not None:
                    # Preserve original row order through the merge by saving
                    # and restoring the chunk index (pandas merge resets it).
                    orig_idx = sub.index
                    merged_sub = sub.reset_index(drop=True).merge(
                        df_r, on=['SORT_X', 'SORT_Y'], how='left')
                    merged_sub.index = orig_idx
                else:
                    merged_sub = sub.copy()
                    for mc in available_merge_cols:
                        if mc not in merged_sub.columns:
                            merged_sub[mc] = None
                parts.append(merged_sub)
            chunk_merged = pd.concat(parts).sort_index()
            chunk_merged = chunk_merged.drop(columns=['_R_PREFIX'], errors='ignore')

            if available_merge_cols:
                matched_total += int(chunk_merged[available_merge_cols[0]].notna().sum())
            row_total += len(chunk_merged)

            chunk_merged.to_csv(
                analysis_csv,
                mode='a' if not first_chunk else 'w',
                index=False,
                header=first_chunk,
            )
            first_chunk = False

    except Exception as exc:
        _log(f'Reticle mapping: streaming merge failed: {exc}')
        return csv_path

    _log(f'Reticle mapping: {matched_total:,}/{row_total:,} rows matched.')

    # ── Step 8: Optionally zip the merged CSV inside the output folder ─────────
    if save_merged and os.path.isfile(analysis_csv):
        _zip_path = str(Path(analysis_csv).with_suffix('.zip'))
        try:
            with zipfile.ZipFile(_zip_path, 'w', zipfile.ZIP_DEFLATED) as _zf:
                _zf.write(analysis_csv, Path(analysis_csv).name)
            _log(f'Reticle mapping: zipped merged CSV → {_zip_path}')
        except Exception as exc:
            _log(f'Reticle mapping: could not zip merged CSV: {exc}')

    _log(f'Reticle mapping: analysis CSV → {analysis_csv}')
    return analysis_csv


# ---------------------------------------------------------------------------
# Zip helper  (called at the end of the pipeline)
# ---------------------------------------------------------------------------

def zip_output_folder(output_dir: str, log_cb=None) -> str | None:
    """Zip *output_dir* into ``<output_dir>.zip``.  Returns the zip path, or
    None on failure."""

    def _log(msg: str) -> None:
        if log_cb:
            log_cb(msg)
        else:
            print(msg)

    if not os.path.isdir(output_dir):
        return None

    zip_path = output_dir.rstrip('/\\') + '.zip'
    try:
        base = Path(output_dir)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fpath in sorted(base.rglob('*')):
                if fpath.is_file():
                    zf.write(fpath, fpath.relative_to(base.parent))
        _log(f'Reticle mapping: zipped output folder → {zip_path}')
        return zip_path
    except Exception as exc:
        _log(f'Reticle mapping: could not zip output folder: {exc}')
        return None
