"""
PCM Merge GUI — Etest Dashboard
================================
Load a yield / die-level CSV that contains SORT_X, SORT_Y, SORT_LOT and
DevRevStep columns.  The tool:

  1. Derives the PCM filename from DevRevStep + SORT_LOT
     (e.g.  DevRevStep=8PF5CVL  +  SORT_LOT=Q603S6T0
      →  shared/etest/9-sites/8PF5CV-L-Q603S6T0-PCM.csv)
  2. Maps SORT_X / SORT_Y → reticle (LayoutX, LayoutY) via the
     reticle-mapping CSV in shared/reticle/.
  3. Applies Inverse Distance Weighting (IDW) to extrapolate the 9 measured
     reticle sites to every reticle on the wafer.
  4. Merges the reconstructed PCM values back into the original CSV rows.
  5. Saves the merged output CSV.

Usage
-----
    python pcm_merge_gui.py
"""

import json
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

import pandas as pd
import numpy as np
from _constants import _walk_dir_and_zips, _read_csv, _zip_basename, _zip_isfile


# ─────────────────────────────────────────────────────────────────────────────
# Path constants  (relative to this script's directory)
# ─────────────────────────────────────────────────────────────────────────────
_HERE          = os.path.dirname(os.path.abspath(__file__))
# Walk up from _HERE until we find the directory that contains 'shared/'
# This works for both dev layout  (…/etest-dashboard/src/)
# and deployed layout             (…/etest-dashboard/run/src/)
def _find_repo_root(start: str) -> str:
    d = start
    for _ in range(8):  # safety cap — never walk more than 8 levels
        if os.path.isdir(os.path.join(d, "shared")):
            return d
        parent = os.path.dirname(d)
        if parent == d:  # reached filesystem root
            break
        d = parent
    # Fallback: 3 levels up (original behaviour)
    return os.path.abspath(os.path.join(start, "..", "..", ".."))
_REPO_ROOT     = _find_repo_root(_HERE)
_NINE_SITE_DIR   = os.path.join(_REPO_ROOT, "shared", "etest", "9-sites")
_FULL_SITE_DIR   = os.path.join(_REPO_ROOT, "shared", "etest", "full-sites")
_RETICLE_DIR     = os.path.join(_REPO_ROOT, "shared", "reticle")
_MATERIAL_DIR    = os.path.join(_REPO_ROOT, "shared", "material")
_YIELD_DATA_DIR  = os.environ.get('ETEST_DATA_DIR', r"C:\work\etest")
_SPEC_CSV        = os.path.join(_REPO_ROOT, "shared", "spec", "wat",
                                "N2P_NVL816_WAT_PDK1.0_target.csv")

# Cached spec lookup: param_name -> (spec_low, spec_high, target, unit)
_spec_lookup_cache: "dict | None" = None

# Column name aliases for spec limits (handles both formats)
_SPEC_PARAM_COLS  = ["Parameter", "WAT naming"]
_SPEC_LOW_COLS    = ["Spec_Low",  "Spec Low"]
_SPEC_HIGH_COLS   = ["Spec_High", "Spec High"]
_SPEC_TARGET_COLS = ["Target"]

def _load_spec_df():
    """Build and cache a param→(sl, sh, tgt, unit) lookup from the spec CSV.

    Supports two file formats:
      • Definition format: one row per param  (columns: 'WAT naming', 'Spec Low', …)
      • Violation/output format: one row per die×param (columns: 'Parameter',
        'Spec_Low', 'Spec_High', …) — deduplicated to get unique limits.
    Returns the lookup dict, or None if the file is not found.
    """
    global _spec_lookup_cache
    if _spec_lookup_cache is not None:
        return _spec_lookup_cache
    if not os.path.isfile(_SPEC_CSV):
        return None
    df = pd.read_csv(_SPEC_CSV)
    df.columns = [c.strip() for c in df.columns]

    # Detect which column holds the parameter name
    param_col = next((c for c in _SPEC_PARAM_COLS if c in df.columns), None)
    sl_col    = next((c for c in _SPEC_LOW_COLS    if c in df.columns), None)
    sh_col    = next((c for c in _SPEC_HIGH_COLS   if c in df.columns), None)
    tgt_col   = next((c for c in _SPEC_TARGET_COLS if c in df.columns), None)
    if param_col is None:
        return None   # unrecognised format

    def _fv(row, col):
        if col is None:
            return float("nan")
        try:
            v = row[col]
            return float(v) if pd.notna(v) else float("nan")
        except Exception:
            return float("nan")

    # Deduplicate: take first occurrence of each parameter name
    seen = set()
    lookup = {}
    for _, row in df.iterrows():
        p = str(row.get(param_col, "")).strip()
        if not p or p in seen:
            continue
        seen.add(p)
        sl  = _fv(row, sl_col)
        sh  = _fv(row, sh_col)
        tgt = _fv(row, tgt_col)
        unit = str(row.get("Unit", "")).strip() if pd.notna(row.get("Unit", "")) else ""
        # Only store if at least one limit is defined
        if not (sl != sl and sh != sh):
            lookup[p] = (sl, sh, tgt, unit)

    _spec_lookup_cache = lookup
    return lookup

def _get_spec(param: str):
    """Return (spec_low, target, spec_high, unit) for a parameter.
    Values may be NaN.  Returns (nan, nan, nan, '') if param not found."""
    lookup = _load_spec_df()
    nan = float("nan")
    if lookup is None or param not in lookup:
        return nan, nan, nan, ""
    sl, sh, tgt, unit = lookup[param]
    return sl, tgt, sh, unit


def write_spec_violations(df_out: "pd.DataFrame", pcm_cols: list,
                          stem: str, out_folder: str, log) -> int:
    """
    Check every PCM column in df_out against Spec Low / Spec High from the
    spec CSV.  Writes a violation report to:
        <out_folder>/spec-violation/<stem>-violations.csv
    Returns the number of violation rows written (0 if none).
    """
    spec_lookup = _load_spec_df()
    if spec_lookup is None:
        log("[Spec ] spec CSV not found — skipping violation check")
        return 0

    # Identify columns that have any spec limit
    checked = [c for c in pcm_cols if c in spec_lookup]
    log(f"[Spec ] Spec limits loaded for {len(spec_lookup)} params; "
        f"{len(checked)}/{len(pcm_cols)} selected params have limits")
    if not checked:
        log("[Spec ] No spec limits defined for the selected parameters — skipping")
        return 0

    # ID columns to carry into the violation report
    id_cols = [c for c in
               ["SORT_LOT", "SORT_X", "SORT_Y", "Reticle", "ReticleShot"]
               if c in df_out.columns]

    records = []
    for col in checked:
        sl, sh, tgt, unit = spec_lookup[col][:4]
        vals = pd.to_numeric(df_out[col], errors="coerce")
        has_sl = sl == sl   # not NaN
        has_sh = sh == sh

        for row_idx, val in vals.items():
            if val != val:   # NaN
                continue
            viol = None
            if has_sl and val < sl:
                viol = "below_LSL"
                dev  = val - sl      # negative
            elif has_sh and val > sh:
                viol = "above_USL"
                dev  = val - sh      # positive
            if viol:
                rec = {c: df_out.at[row_idx, c] for c in id_cols}
                rec["Parameter"]  = col
                rec["Value"]      = val
                rec["Spec_Low"]   = sl   if has_sl else ""
                rec["Target"]     = tgt  if tgt == tgt else ""
                rec["Spec_High"]  = sh   if has_sh else ""
                rec["Unit"]       = unit
                rec["Violation"]  = viol
                rec["Deviation"]  = round(dev, 6)
                records.append(rec)

    if not records:
        log(f"[Spec ] ✓  No violations found for {len(checked)} checked parameters")
        return 0

    viol_dir = os.path.join(out_folder, "spec-violation")
    os.makedirs(viol_dir, exist_ok=True)
    viol_path = os.path.join(viol_dir, f"{stem}-violations.csv")

    col_order = id_cols + ["Parameter", "Value", "Spec_Low", "Target",
                           "Spec_High", "Unit", "Violation", "Deviation"]
    df_viol = pd.DataFrame(records, columns=col_order)
    df_viol.to_csv(viol_path, index=False, encoding='utf-8')

    n_rows    = len(df_viol)
    n_params  = df_viol["Parameter"].nunique()
    n_dies    = df_viol[id_cols].drop_duplicates().shape[0] if id_cols else n_rows
    log(f"[Spec ] ⚠  {n_rows} violation row(s)  |  {n_params} param(s)  |  "
        f"~{n_dies} die(s)  →  {viol_path}", "err")
    return n_rows

# ─────────────────────────────────────────────────────────────────────────────
# Backend helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_devrevstep(devrevstep: str):
    """Return (prefix6, step) from a DevRevStep string.

    The first 6 characters are the technology prefix (e.g. '8PF6CV').
    The manufacturing STEP is always the LAST character (e.g. 'R', 'P', 'L').
    Any characters between position 6 and the last are variant codes (e.g. 'E'
    in '8PF6CVER') and are NOT used for etest filename matching.

    Examples:
      '8PF5CVL'  → ('8PF5CV', 'L')
      '8PF6CVR'  → ('8PF6CV', 'R')
      '8PF6CVER' → ('8PF6CV', 'R')   # E is a variant code, step=last char
    """
    s = str(devrevstep).strip()
    if len(s) >= 7:
        return s[:6], s[-1]   # last char is always the step
    if len(s) == 6:
        return s, ""
    return s, ""


def _guess_etest_path(devrevstep: str, sort_lot: str):
    """
    Return (path, found) for the 9-site PCM CSV.

    Priority:
      1. Exact match: <prefix6>-<char7>-<sort_lot>-PCM.csv
      2. Fuzzy match: any <prefix6>-<char7>-*-PCM.csv in the 9-sites dir
      3. Any <prefix6>-*-PCM.csv as last resort

    'found' is True only when the file actually exists.
    """
    prefix6, char7 = _parse_devrevstep(devrevstep)
    tech_prefix = f"{prefix6}-{char7}-" if char7 else f"{prefix6}-"

    # Candidate exact stems to try (lot as-is, and with trailing '0' suffix)
    _lot_variants = [sort_lot] + ([sort_lot + "0"] if sort_lot else [])
    for _lv in _lot_variants:
        stem = f"{prefix6}-{char7}-{_lv}-PCM.csv" if char7 else f"{prefix6}-{_lv}-PCM.csv"
        p = os.path.join(_NINE_SITE_DIR, stem)
        if os.path.isfile(p):
            return p, True

    stem_exact = (
        f"{prefix6}-{char7}-{sort_lot}-PCM.csv" if char7
        else f"{prefix6}-{sort_lot}-PCM.csv"
    )
    exact_path = os.path.join(_NINE_SITE_DIR, stem_exact)

    # Fuzzy: walk 9-sites recursively (including inside .zip files); prefer files containing sort_lot
    if os.path.isdir(_NINE_SITE_DIR):
        lot_matches, tech_matches, wide_matches = [], [], []
        for f, full in _walk_dir_and_zips(_NINE_SITE_DIR):
            if not f.endswith("-PCM.csv"):
                continue
            if f.startswith(tech_prefix) and sort_lot and sort_lot in f:
                lot_matches.append(full)
            elif f.startswith(tech_prefix):
                tech_matches.append(full)
            elif f.startswith(prefix6):
                wide_matches.append(full)
        if lot_matches:
            return sorted(lot_matches)[0], True
        if tech_matches:
            return sorted(tech_matches)[0], True
        if wide_matches:
            return sorted(wide_matches)[0], True

    # Nothing found — return the expected path so the user sees what's missing
    return exact_path, False


def _guess_material_file(devrevstep: str, sort_lot: str = "") -> str:
    """Return the material/lot-definition CSV that best matches DevRevStep + lot.

    Priority:
      1. File containing prefix6 AND whose INTEL_LOT7 column contains sort_lot[:7]
      2. Any file containing prefix6 in the filename
      3. Any lot-definition CSV as last resort
    """
    prefix6, _ = _parse_devrevstep(devrevstep)
    if not os.path.isdir(_MATERIAL_DIR):
        return ""

    candidates = [
        os.path.join(_MATERIAL_DIR, f)
        for f in sorted(os.listdir(_MATERIAL_DIR))
        if f.lower().endswith(".csv") and prefix6.upper() in f.upper()
    ]

    # If we have a sort_lot, prefer the file that actually contains it
    lot7 = sort_lot[:7] if len(sort_lot) >= 7 else sort_lot
    if lot7 and candidates:
        for fpath in candidates:
            try:
                _lots = set(
                    pd.read_csv(fpath, usecols=["INTEL_LOT7"])
                    ["INTEL_LOT7"].dropna().astype(str).str.strip()
                )
                if lot7 in _lots:
                    return fpath
            except Exception:
                pass
        # No exact lot match — fall back to first prefix match
        return candidates[0]

    if candidates:
        return candidates[0]

    # Last resort: any lot-definition file
    for fname in sorted(os.listdir(_MATERIAL_DIR)):
        if fname.lower().endswith(".csv") and "lot" in fname.lower():
            return os.path.join(_MATERIAL_DIR, fname)
    return ""


def _guess_reticle_map(devrevstep: str) -> str:
    """Try to find a reticle-mapping CSV matching the technology prefix."""
    prefix6 = _parse_devrevstep(devrevstep)[0]   # e.g. "8PF5CV" or "8PF6CV"
    # Look in shared/reticle/ for a matching file
    if os.path.isdir(_RETICLE_DIR):
        # Exact technology match first
        for fname in sorted(os.listdir(_RETICLE_DIR)):
            if fname.endswith(".csv") and prefix6 in fname and "Reticle" in fname:
                return os.path.join(_RETICLE_DIR, fname)
        # Fallback: any Reticle_Mapping file
        for fname in sorted(os.listdir(_RETICLE_DIR)):
            if fname.endswith(".csv") and "Reticle" in fname:
                return os.path.join(_RETICLE_DIR, fname)
    return ""


def _list_available_etest_files():
    """Return sorted list of PCM CSV filenames in the 9-sites directory (recursive, including inside .zip files)."""
    if not os.path.isdir(_NINE_SITE_DIR):
        return []
    result = []
    for f, _full in _walk_dir_and_zips(_NINE_SITE_DIR):
        if f.endswith(".csv"):
            result.append(f)
    return sorted(result)


def _guess_full_site_files(devrevstep: str) -> list:
    """
    Return sorted list of full-site PCM CSV paths for this technology+step.
    Matches ALL lots for the prefix, e.g. all 8PF5CV-L-*-PCM.csv.
    Searches recursively through subfolders.
    Returns empty list if directory missing or no matches.
    """
    if not os.path.isdir(_FULL_SITE_DIR):
        return []
    prefix6, char7 = _parse_devrevstep(devrevstep)
    tech_prefix = f"{prefix6}-{char7}-" if char7 else f"{prefix6}-"
    matches = []
    for f, full in _walk_dir_and_zips(_FULL_SITE_DIR):
        if f.startswith(tech_prefix) and f.endswith("-PCM.csv"):
            matches.append(full)
    return sorted(matches)


def _infer_devrevstep_lot(df: pd.DataFrame):
    """Return (devrevstep, sort_lot) from the DataFrame, or ('', '') if missing.

    Handles column names with suffixes, e.g. 'DevRevStep_119325'.
    """
    devrevstep = sort_lot = ""

    # DevRevStep — exact or startswith match
    for col in df.columns:
        if col.upper() == "DEVREVSTEP" or col.upper().startswith("DEVREVSTEP_"):
            _ser = df[col] if isinstance(df[col], pd.Series) else df[col].iloc[:, 0]
            vals = _ser.dropna().astype(str).unique()
            if len(vals):
                devrevstep = vals[0]
            break

    # SORT_LOT — exact or startswith match
    for col in df.columns:
        if col.upper() == "SORT_LOT" or col.upper().startswith("SORT_LOT_"):
            _ser = df[col] if isinstance(df[col], pd.Series) else df[col].iloc[:, 0]
            vals = _ser.dropna().astype(str).unique()
            if len(vals):
                sort_lot = vals[0]
            break

    return devrevstep, sort_lot


def _normalise_sort_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Rename Sort_X/sort_x → SORT_X, etc. (handles suffixed column names like DevRevStep_119325)."""
    rename = {}
    _seen: dict = {}  # normalized name → first original col seen
    for col in df.columns:
        u = col.upper()
        if u == "SORT_X":
            rename[col] = "SORT_X"
        elif u == "SORT_Y":
            rename[col] = "SORT_Y"
        elif u == "SORT_LOT" or u.startswith("SORT_LOT_"):
            rename[col] = "SORT_LOT"
        elif u == "DEVREVSTEP" or u.startswith("DEVREVSTEP_"):
            rename[col] = "DevRevStep"
    df = df.rename(columns=rename) if rename else df
    # Drop duplicate columns that arise from multi-session merges
    # (e.g. DevRevStep_119325 + DevRevStep_232619 both → DevRevStep)
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated(keep='first')]
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Core pipeline  (runs in a background thread)
# ─────────────────────────────────────────────────────────────────────────────

_ID_COLS = {
    "Technology", "Layout", "Lot", "Wafer", "TestProgram", "TestProgramVersion",
    "Fab", "Step", "Equipment", "EquipmentType", "TestDateTime", "TestDate",
    "TimeLoaded", "WaferResultID", "Site", "LayoutX", "LayoutY", "Map", "MapID",
    "ReticleShotRadius", "Reticle", "ReticleShot", "DieX", "DieY", "SORT_X", "SORT_Y",
}


def run_pipeline(
    input_csv: str,
    etest_csv: str,
    reticle_map_csv: str,
    output_csv: str,
    log,
    wafer_filter=None,      # list of str wafer IDs, or None for all wafers
    full_site_csvs=None,    # list of full-site CSV paths (shape reference), or None
    alpha: float = 0.5,     # blending weight: 1.0 = pure IDW, 0.0 = pure shape
    idw_power: int = 2,
    material_csv: str = "",  # optional lot-definition CSV for material info merge
    df_yield_cache=None,    # pre-loaded yield DataFrame (avoids re-reading CSV each lot)
    pcm_filter: str = "",   # wildcard(s) to select PCM columns, e.g. "*Con*" or "*Vts*,*Isat*"
):
    """
    Full merge pipeline implementing the Hybrid Wafer Map Reconstruction spec.

    Mode A — 9-site only (no full_site_csvs):
        FinalMap = IDW from the 9 measured real site values.

    Mode B — Hybrid (full_site_csvs provided):
        Uses 9-site CSV as the magnitude anchor for this lot and
        full-site CSVs (all lots, same DevRevStep) as the spatial
        shape reference, following the 5-step spec:
          Step 1: SampleShape  = full_site_mean − Median(full_site_mean)
          Step 2: V_IDW        = IDW from 9 real site values (p=2)
          Step 3: WaferScale   = Median( (V_9site_i − RealMedian) /
                                         (SampleShape_i + ε) )
          Step 4: Hybrid       = α·V_IDW + (1−α)·(RealMedian + WaferScale·SampleShape)
          Step 5: FinalMap     = Hybrid + (RealMedian − Median(Hybrid))
    """
    EPS = 1e-9

    # ── 1. Load yield CSV ─────────────────────────────────────────────────────
    import time as _time
    _t0 = _time.perf_counter()
    log(f"[Load ] Yield CSV: {os.path.basename(input_csv)}")
    if df_yield_cache is not None:
        df_yield = df_yield_cache
    else:
        df_yield = _normalise_sort_cols(pd.read_csv(input_csv, low_memory=False))
    log(f"        {len(df_yield):,} rows,  {len(df_yield.columns)} columns  ({_time.perf_counter()-_t0:.1f}s)")
    if "SORT_X" not in df_yield.columns or "SORT_Y" not in df_yield.columns:
        raise ValueError("Input CSV must have SORT_X and SORT_Y columns (case-insensitive).")

    # Log wafers present in the input file
    _sw_col = next((c for c in df_yield.columns
                    if c.upper() in {"SORT_WAFER", "WAFER", "WAFERID"}), None)
    if _sw_col:
        _input_wafers = sorted(df_yield[_sw_col].dropna().astype(str).unique())
        log(f"[Load ] Input wafers ({_sw_col}): {_input_wafers}")

    # ── 2. Load reticle mapping ───────────────────────────────────────────────
    _t1 = _time.perf_counter()
    log(f"[Load ] Reticle mapping: {os.path.basename(reticle_map_csv)}")
    df_map = pd.read_csv(reticle_map_csv)
    log(f"        {len(df_map):,} rows  ({_time.perf_counter()-_t1:.1f}s)")
    if "SORT_X" not in df_map.columns or "SORT_Y" not in df_map.columns:
        x_mid = (df_map["DieX"].max() + df_map["DieX"].min()) / 2
        y_mid = (df_map["DieY"].max() + df_map["DieY"].min()) / 2
        df_map["SORT_X"] = df_map["DieX"] - x_mid
        df_map["SORT_Y"] = df_map["DieY"] - y_mid
        log(f"[Map  ] SORT_X/Y computed (centre {x_mid}, {y_mid})")

    # Reticle attribute columns to carry forward (all except coordinate/geometry cols)
    _RETICLE_ATTR_COLS = [c for c in
        ["Reticle", "ReticleShot", "Columns", "Concentric", "Grid",
         "Radial", "Rows", "Sectors", "Radius"]
        if c in df_map.columns]

    sort_to_layout = (
        df_map[["SORT_X", "SORT_Y", "LayoutX", "LayoutY"] + _RETICLE_ATTR_COLS]
        .drop_duplicates(subset=["SORT_X", "SORT_Y"])
        .reset_index(drop=True)
    )
    all_reticles = (
        df_map[["LayoutX", "LayoutY"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    all_xy = all_reticles[["LayoutX", "LayoutY"]].values.astype(float)
    log(f"[Map  ] {len(all_reticles):,} unique reticles,  "
        f"{len(sort_to_layout):,} unique SORT_X/Y positions")

    # ── Detect unique SORT_LOT values for per-lot etest routing ─────────────
    _lot_col = next((c for c in df_yield.columns if c.upper() == "SORT_LOT"), None)
    _drs_col = next((c for c in df_yield.columns
                     if c.upper() == "DEVREVSTEP" or c.upper().startswith("DEVREVSTEP_")), None)
    _lots = (df_yield[_lot_col].dropna().astype(str).unique().tolist() if _lot_col else [])

    # ── 3-8. PCM etest merge (skipped if no etest CSV provided) ─────────────
    pcm_cols = []
    lot_reticle_pcm: dict = {}   # lot_str (or None) → {(LayoutX, LayoutY): {param: val}}
    if etest_csv and _zip_isfile(etest_csv):
        _t2 = _time.perf_counter()
        log(f"[Load ] 9-site etest: {_zip_basename(etest_csv)}")
        df_9 = _read_csv(etest_csv)
        log(f"        {len(df_9):,} rows  ({_time.perf_counter()-_t2:.1f}s)")

        # Identify primary lot and warn if etest filename doesn't match
        _primary_lot = _lots[0] if _lots else None
        if _primary_lot:
            etest_stem = _zip_basename(etest_csv)
            if _primary_lot not in etest_stem:
                log(f"[Warn ] SORT_LOT={_primary_lot!r} not found in etest filename "
                    f"{etest_stem!r} — PCM values may be from a different lot!")

        wafer_col = next((c for c in ["Wafer", "WAFER", "wafer"] if c in df_9.columns), None)
        if wafer_col and wafer_filter:
            keep = {str(w) for w in wafer_filter}
            available = set(df_9[wafer_col].astype(str).unique())
            df_9 = df_9[df_9[wafer_col].astype(str).isin(keep)]
            if len(df_9) == 0:
                raise ValueError(
                    f"Wafer filter {wafer_filter} matched no rows in the etest CSV.\n"
                    f"Available wafers in etest: {sorted(available, key=lambda v: int(v) if v.isdigit() else v)}\n"
                    f"Check that the SORT_WAFER decoding is correct (e.g. 703 → 3)."
                )
            log(f"[9-site] Filtered to wafer(s): {wafer_filter}  → {len(df_9):,} rows")
        else:
            wafers = list(df_9[wafer_col].unique()) if wafer_col else ["(all)"]
            log(f"[9-site] Using all wafers: {wafers}")

        # ── 4. PCM parameter detection ────────────────────────────────────────
        pcm_cols = [
            c for c in df_9.columns
            if c not in _ID_COLS and pd.api.types.is_numeric_dtype(df_9[c])
        ]
        log(f"[PCM  ] {len(pcm_cols)} numeric parameters detected")

        # Apply wildcard filter if provided
        if pcm_filter and pcm_filter.strip():
            import fnmatch
            patterns = [p.strip() for p in pcm_filter.split(",") if p.strip()]
            pcm_cols = [
                c for c in pcm_cols
                if any(fnmatch.fnmatch(c.upper(), pat.upper()) for pat in patterns)
            ]
            log(f"[PCM  ] Filter '{pcm_filter}' → {len(pcm_cols)} params matched: "
                + (str(pcm_cols[:8]) + (" …" if len(pcm_cols) > 8 else "")))
            if not pcm_cols:
                raise ValueError(
                    f"No PCM columns matched filter '{pcm_filter}'. "
                    "Check spelling / wildcard (e.g. *Con* or Isat*).")

        # ── 5. Average 9-site values at each (LayoutX, LayoutY) ──────────────
        site_mean_9 = (
            df_9.copy().groupby(["LayoutX", "LayoutY"])[pcm_cols]
            .mean()
            .reset_index()
        )
        n_sites   = len(site_mean_9)
        site_xy_9 = site_mean_9[["LayoutX", "LayoutY"]].values.astype(float)
        vals_9    = site_mean_9[pcm_cols].values.astype(float)
        real_median = np.nanmedian(vals_9, axis=0)
        log(f"[IDW  ] {n_sites} measured site positions (magnitude anchor)")

        # ── 6. IDW from 9 sites to all reticles ──────────────────────────────
        diff_9  = all_xy[:, np.newaxis, :] - site_xy_9[np.newaxis, :, :]
        dist_9  = np.sqrt((diff_9 ** 2).sum(axis=2))
        exact_9 = dist_9 == 0
        w_9     = 1.0 / (dist_9 ** idw_power + EPS)
        nw_9    = w_9 / (w_9.sum(axis=1, keepdims=True) + EPS)
        V_IDW   = nw_9 @ vals_9
        for i in range(len(all_xy)):
            hits = np.where(exact_9[i])[0]
            if len(hits):
                V_IDW[i] = vals_9[hits[0]]

        # ── 7. Hybrid reconstruction ──────────────────────────────────────────
        if full_site_csvs:
            log(f"[Hybrid] Loading {len(full_site_csvs)} full-site file(s) as spatial shape reference")
            df_full = pd.concat([_read_csv(p) for p in full_site_csvs], ignore_index=True)
            log(f"[Hybrid] {len(df_full):,} total rows across all lots")

            pcm_full = [c for c in pcm_cols if c in df_full.columns]
            if len(pcm_full) < len(pcm_cols):
                log(f"[Hybrid] {len(pcm_cols)-len(pcm_full)} params missing in full-site → IDW fallback for those")

            full_mean = (
                df_full.groupby(["LayoutX", "LayoutY"])[pcm_full]
                .mean()
                .reset_index()
            )
            full_xy   = full_mean[["LayoutX", "LayoutY"]].values.astype(float)
            full_vals = full_mean[pcm_full].values.astype(float)
            log(f"[Hybrid] {len(full_mean)} unique positions in full-site shape reference")

            full_med    = np.nanmedian(full_vals, axis=0)
            SampleShape = full_vals - full_med

            diff_f  = all_xy[:, np.newaxis, :] - full_xy[np.newaxis, :, :]
            dist_f  = np.sqrt((diff_f ** 2).sum(axis=2))
            exact_f = dist_f == 0
            w_f     = 1.0 / (dist_f ** idw_power + EPS)
            nw_f    = w_f / (w_f.sum(axis=1, keepdims=True) + EPS)
            SampleShape_all = nw_f @ SampleShape
            for i in range(len(all_xy)):
                hits = np.where(exact_f[i])[0]
                if len(hits):
                    SampleShape_all[i] = SampleShape[hits[0]]

            diff_s  = site_xy_9[:, np.newaxis, :] - full_xy[np.newaxis, :, :]
            dist_s  = np.sqrt((diff_s ** 2).sum(axis=2))
            nn_idx  = dist_s.argmin(axis=1)
            shape_at_9 = SampleShape[nn_idx]

            vals_9_sub   = site_mean_9[pcm_full].values.astype(float)
            real_med_sub = np.nanmedian(vals_9_sub, axis=0)
            scale_factor = (vals_9_sub - real_med_sub) / (shape_at_9 + EPS)
            WaferScale   = np.nanmedian(scale_factor, axis=0)
            log(f"[Hybrid] WaferScale  median={np.nanmedian(WaferScale):.4f}  "
                f"range=[{np.nanmin(WaferScale):.4f}, {np.nanmax(WaferScale):.4f}]")

            SampleScaled_all = real_med_sub + WaferScale * SampleShape_all
            pcm_full_idx     = [pcm_cols.index(c) for c in pcm_full]
            V_IDW_sub        = V_IDW[:, pcm_full_idx]
            Hybrid_sub       = alpha * V_IDW_sub + (1 - alpha) * SampleScaled_all

            HybridMed    = np.nanmedian(Hybrid_sub, axis=0)
            FinalMap_sub = Hybrid_sub + (real_med_sub - HybridMed)

            for j, c in enumerate(pcm_full):
                V_IDW[:, pcm_cols.index(c)] = FinalMap_sub[:, j]

            log(f"[Hybrid] Complete  (α={alpha},  median-enforced,  "
                f"{len(full_mean)} shape positions → {len(all_reticles)} reticles)")
            mode_str = f"Hybrid α={alpha}"
        else:
            log(f"[IDW  ] Pure 9-site IDW — {len(all_reticles)} reticles × {len(pcm_cols)} params")
            mode_str = "9-site IDW"

        # ── 8. Build reticle → PCM lookup ─────────────────────────────────────
        _t3 = _time.perf_counter()
        reticle_pcm = {
            (float(row["LayoutX"]), float(row["LayoutY"])): {
                p: V_IDW[i, j] for j, p in enumerate(pcm_cols)
            }
            for i, row in all_reticles.iterrows()
        }
        lot_reticle_pcm[_primary_lot] = reticle_pcm
        log(f"[Time ] primary IDW+lookup: {_time.perf_counter()-_t3:.1f}s")

        # ── 8b. Supplementary lots — IDW for any additional SORT_LOTs ─────────
        _gui_prefix = _zip_basename(etest_csv)[:6]
        for _extra_lot in _lots:
            if _extra_lot == _primary_lot:
                continue
            _extra_drs = ""
            if _drs_col and _lot_col:
                _extra_rows = df_yield[df_yield[_lot_col].astype(str) == _extra_lot]
                _drs_vals   = _extra_rows[_drs_col].dropna().astype(str).unique()
                if len(_drs_vals):
                    _extra_drs = _drs_vals[0]
            _extra_prefix = _extra_drs[:6] if _extra_drs else ""

            if not _extra_prefix or _gui_prefix == _extra_prefix:
                _extra_et = etest_csv
            else:
                _auto_et, _found_et = _guess_etest_path(_extra_drs, _extra_lot)
                if not _found_et:
                    log(f"[Warn ] Lot {_extra_lot!r}: no etest CSV found — PCM skipped for this lot")
                    continue
                log(f"[Auto ] Lot {_extra_lot!r}: etest → {_zip_basename(_auto_et)}")
                _extra_et = _auto_et

            try:
                _t_extra = _time.perf_counter()
                df_extra = _read_csv(_extra_et)
                log(f"[Load ] Lot {_extra_lot!r} etest: {_zip_basename(_extra_et)}  "
                    f"({len(df_extra):,} rows)")
                _wc = next((c for c in ["Wafer", "WAFER", "wafer"] if c in df_extra.columns), None)
                if _wc and wafer_filter:
                    df_extra = df_extra[df_extra[_wc].astype(str).isin({str(w) for w in wafer_filter})]
                _extra_pcm = [c for c in pcm_cols if c in df_extra.columns]
                if not _extra_pcm:
                    log(f"[Warn ] Lot {_extra_lot!r}: no matching PCM columns — skipped")
                    continue
                _sm  = df_extra.groupby(["LayoutX", "LayoutY"])[_extra_pcm].mean().reset_index()
                _sxy = _sm[["LayoutX", "LayoutY"]].values.astype(float)
                _v   = _sm[_extra_pcm].values.astype(float)
                _diff = all_xy[:, np.newaxis, :] - _sxy[np.newaxis, :, :]
                _dist = np.sqrt((_diff ** 2).sum(axis=2))
                _w    = 1.0 / (_dist ** idw_power + EPS)
                _nw   = _w / (_w.sum(axis=1, keepdims=True) + EPS)
                _vidw = _nw @ _v
                for _i in range(len(all_xy)):
                    _hits = np.where(_dist[_i] == 0)[0]
                    if len(_hits):
                        _vidw[_i] = _v[_hits[0]]
                _lot_pcm = {}
                for _i, _row in all_reticles.iterrows():
                    _xy = (float(_row["LayoutX"]), float(_row["LayoutY"]))
                    _lot_pcm[_xy] = {c: _vidw[_i, j] for j, c in enumerate(_extra_pcm)}
                lot_reticle_pcm[_extra_lot] = _lot_pcm
                log(f"[IDW  ] Lot {_extra_lot!r}: IDW complete  "
                    f"({len(_extra_pcm)} params,  {len(_sm)} sites)  ({_time.perf_counter()-_t_extra:.1f}s)")
            except Exception as _ex:
                log(f"[Warn ] Lot {_extra_lot!r}: etest processing failed — {_ex}")

        if len(_lots) > 1:
            log(f"[PCM  ] {len(pcm_cols)} params across {len(lot_reticle_pcm)} lot(s): "
                + str(list(lot_reticle_pcm.keys())))
        log(f"[Time ] all IDW (this lot): {_time.perf_counter()-_t2:.1f}s total")
    else:
        log("[PCM  ] No etest CSV provided — skipping PCM merge (reticle + material only)")
        mode_str = "Reticle+Material only"
        reticle_pcm = {}

    # ── 9. Merge into yield CSV ───────────────────────────────────────────────
    merged  = df_yield.merge(
        sort_to_layout[["SORT_X", "SORT_Y", "LayoutX", "LayoutY"] + _RETICLE_ATTR_COLS],
        on=["SORT_X", "SORT_Y"], how="left",
    )
    if _RETICLE_ATTR_COLS:
        log(f"[Merge] Reticle attributes added: {_RETICLE_ATTR_COLS}")
    matched = merged["LayoutX"].notna().sum()
    log(f"[Merge] {matched:,}/{len(merged):,} yield rows matched to a reticle")
    if matched == 0:
        log("[Merge] WARNING: no rows matched — check SORT_X/Y alignment.", "err")

    # Fill PCM values — use per-lot lookup for mixed-lot CSVs
    # Build all new columns at once then concat to avoid DataFrame fragmentation.
    _fallback_pcm = reticle_pcm   # single-lot path (or {} when no etest)
    if pcm_cols:
        _has_layout = merged["LayoutX"].notna()
        _layout_xy  = list(zip(
            merged["LayoutX"].where(_has_layout),
            merged["LayoutY"].where(_has_layout),
        ))
        _lot_keys = (
            merged[_lot_col].astype(str).tolist()
            if _lot_col and _lot_col in merged.columns
            else [None] * len(merged)
        )
        _new_cols: dict = {}
        for p in pcm_cols:
            vals = []
            for i, (has_xy, xy, lot_key) in enumerate(
                zip(_has_layout, _layout_xy, _lot_keys)
            ):
                if not has_xy or pd.isna(xy[0]):
                    vals.append(np.nan)
                else:
                    pcm_dict = lot_reticle_pcm.get(lot_key, _fallback_pcm)
                    vals.append(pcm_dict.get((float(xy[0]), float(xy[1])), {}).get(p, np.nan))
            _new_cols[p] = vals
        merged = pd.concat(
            [merged, pd.DataFrame(_new_cols, index=merged.index)],
            axis=1,
        )

    orig_cols = set(df_yield.columns)
    for col in ["LayoutX", "LayoutY"]:
        if col not in orig_cols:
            merged.drop(columns=[col], errors="ignore", inplace=True)

    # ── 10. Material / lot-definition merge ───────────────────────────────────
    if material_csv and os.path.isfile(material_csv):
        log(f"[Mat  ] Loading material file: {os.path.basename(material_csv)}")
        df_mat = pd.read_csv(material_csv)
        df_mat.columns = [c.strip() for c in df_mat.columns]

        lot7_col    = next((c for c in df_mat.columns if c.upper() == "INTEL_LOT7"), None)
        wafer_col_m = next((c for c in df_mat.columns if c.upper() == "WAFERID"), None)

        if lot7_col is None:
            log("[Mat  ] WARNING: INTEL_LOT7 column not found — skipping material merge")
        else:
            # ── Derive LOT7 from yield (first 7 chars of SORT_LOT) ───────────
            lot_src = next((c for c in merged.columns if c == "SORT_LOT"), None)
            if lot_src is None:
                # fallback: Lot_NNNNNN style
                lot_src = next((c for c in merged.columns
                                if c.lower().startswith("lot_")), None)
            if lot_src is None:
                log("[Mat  ] WARNING: no lot column found in yield CSV — skipping material merge")
                lot7_col = None
            else:
                merged["_mat_lot7"] = merged[lot_src].astype(str).str[:7]
                log(f"[Mat  ] LOT7 derived from '{lot_src}' → "
                    f"e.g. {merged['_mat_lot7'].iloc[0]!r}")

            # ── Derive WAFER2: last 2 chars of SORT_WAFER as int ────────────
            # e.g. SORT_WAFER=503 → '03' → 3  (matches WaferID=3.0 in mat CSV)
            sw_col = next((c for c in merged.columns
                           if c.upper() in {"SORT_WAFER", "SORT_WAFER_U1.U5"}), None)
            if sw_col:
                merged["_mat_wafer"] = pd.to_numeric(
                    merged[sw_col].astype(str).str[-2:], errors="coerce"
                )
                log(f"[Mat  ] WAFER2 derived from '{sw_col}' last-2-digits "
                    f"(e.g. 503→3, 814→14)")
            else:
                merged["_mat_wafer"] = np.nan
                log("[Mat  ] WARNING: no SORT_WAFER column found — wafer-level join will be skipped")

        if lot7_col:
            _MAT_KEEP = [c for c in [
                "Material Type", "Device Skew", "MG4 split",
                "AIO/BB", "Vy CD+", "Remark", "inline scrap",
                "Material Type, Skew, BEOL Skew", "Purpose",
            ] if c in df_mat.columns]

            # Drop columns already in merged to avoid _x/_y suffixes
            cols_already = [c for c in _MAT_KEEP if c in merged.columns]
            if cols_already:
                merged.drop(columns=cols_already, inplace=True)
                log(f"[Mat  ] Replaced existing columns: {cols_already}")

            df_mat["_mat_lot7"]   = df_mat[lot7_col].astype(str).str.strip()
            df_mat["_mat_wafer"]  = pd.to_numeric(df_mat[wafer_col_m], errors="coerce") \
                                     if wafer_col_m else np.nan

            # ── Per-wafer join ────────────────────────────────────────────────
            mat_dedup = (
                df_mat[["_mat_lot7", "_mat_wafer"] + _MAT_KEEP]
                .drop_duplicates(subset=["_mat_lot7", "_mat_wafer"])
            )
            merged = merged.merge(mat_dedup, on=["_mat_lot7", "_mat_wafer"], how="left")
            n_matched = merged[_MAT_KEEP[0]].notna().sum() if _MAT_KEEP else 0
            log(f"[Mat  ] Per-wafer join (INTEL_LOT7 + WaferID) "
                f"→ {n_matched:,}/{len(merged):,} rows matched")

            if n_matched == 0:
                # Fall back to lot-only join
                merged.drop(columns=_MAT_KEEP, errors="ignore", inplace=True)
                mat_lot_dedup = (
                    df_mat[["_mat_lot7"] + _MAT_KEEP]
                    .drop_duplicates(subset=["_mat_lot7"])
                )
                merged = merged.merge(mat_lot_dedup, on="_mat_lot7", how="left")
                n_matched = merged[_MAT_KEEP[0]].notna().sum() if _MAT_KEEP else 0
                log(f"[Mat  ] Lot-level fallback → {n_matched:,}/{len(merged):,} rows matched")

            merged.drop(columns=["_mat_lot7", "_mat_wafer"], errors="ignore", inplace=True)
            log(f"[Mat  ] Columns added: {_MAT_KEEP}")

    # ── 11. Save ──────────────────────────────────────────────────────────────
    merged.to_csv(output_csv, index=False, encoding='utf-8')
    log(f"[Done ] [{mode_str}]  {len(merged):,} rows × {len(merged.columns)} cols  →  {output_csv}", "ok")

    return merged, pcm_cols


# ─────────────────────────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────────────────────────

class PCMMergeFrame(tk.Frame):
    """Embeddable ETest / PCM Merge widget — works both as a tab inside a
    larger ttk.Notebook and as the sole content of a standalone PCMMergeGUI
    window.  All real logic lives here."""

    # ── Colour palette ────────────────────────────────────────────────────────
    BG       = "#1e1e2e"
    PANEL    = "#2a2a3e"
    ACCENT   = "#7c6af7"
    FG       = "#cdd6f4"
    ENTRY_BG = "#313244"
    BTN_RUN  = "#a6e3a1"
    BTN_FG   = "#1e1e2e"
    SECTION  = "#89b4fa"
    DESC_FG  = "#a6adc8"
    HDR_BG   = "#313244"
    ERR_FG   = "#f38ba8"
    OK_FG    = "#a6e3a1"

    def __init__(self, parent=None, advanced: bool = False, **kw):
        super().__init__(parent, bg=self.BG, **kw)
        self._advanced = advanced

        self._df_out        = None
        self._pcm_cols      = []
        self._full_site_files = []   # detected full-site paths for current DevRevStep
        self._input_wafer_is_sort_wafer = False  # True when input uses 3-digit SORT_WAFER

        self._build_styles()
        self._build_ui()

    # ── Style setup ──────────────────────────────────────────────────────────

    def _build_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TFrame",         background=self.BG)
        s.configure("TLabel",         background=self.BG, foreground=self.FG,
                    font=("Segoe UI", 9))
        s.configure("Desc.TLabel",    background=self.BG, foreground=self.DESC_FG,
                    font=("Segoe UI", 8, "italic"))
        s.configure("Section.TLabel", background=self.BG, foreground=self.SECTION,
                    font=("Segoe UI", 9, "bold"))
        s.configure("TEntry",         fieldbackground=self.ENTRY_BG,
                    foreground=self.FG, insertcolor=self.FG)
        s.configure("TCombobox",      fieldbackground=self.ENTRY_BG,
                    foreground=self.FG, selectbackground=self.ACCENT)
        s.configure("TCheckbutton",   background=self.BG, foreground=self.FG,
                    font=("Segoe UI", 9))
        s.configure("Browse.TButton", background=self.ACCENT, foreground="#ffffff",
                    font=("Segoe UI", 8), padding=(6, 2))
        s.map("Browse.TButton",  background=[("active", "#9b8ff9")])
        s.configure("Run.TButton", background=self.BTN_RUN, foreground=self.BTN_FG,
                    font=("Segoe UI", 10, "bold"), padding=(14, 5))
        s.map("Run.TButton",     background=[("active", "#94d88f")])
        s.configure("TNotebook",       background=self.BG, borderwidth=0)
        s.configure("TNotebook.Tab",   background=self.PANEL, foreground=self.FG,
                    font=("Segoe UI", 9, "bold"), padding=(12, 5))
        s.map("TNotebook.Tab",
              background=[("selected", self.ACCENT)],
              foreground=[("selected", "#ffffff")])
        s.configure("TSeparator",  background="#45475a")

    # ── Main UI layout ────────────────────────────────────────────────────────

    def _build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=6)

        tab_merge = ttk.Frame(nb, style="TFrame")
        nb.add(tab_merge, text="  PCM Merge  ")
        self._build_merge_tab(tab_merge)

        # ── Tab 2: ETest / PCM Dashboard ─────────────────────────────────────
        try:
            from pcm_dashboard_frame import PCMDashboardFrame
            tab_dash = PCMDashboardFrame(nb)
            nb.add(tab_dash, text="  PCM Dashboard  ")
        except Exception as _e:
            import traceback
            tab_dash_err = ttk.Frame(nb, style="TFrame")
            nb.add(tab_dash_err, text="  PCM Dashboard  ")
            ttk.Label(
                tab_dash_err,
                text=f"PCM Dashboard tab failed to load:\n{_e}\n\n{traceback.format_exc()}",
                style="Desc.TLabel", wraplength=700, justify="left",
            ).pack(padx=20, pady=20)



    # ─────────────────────────────────────────────────────────────────────────
    # Tab 1 — PCM Merge
    # ─────────────────────────────────────────────────────────────────────────

    def _build_merge_tab(self, parent):
        pad = {"padx": 10, "pady": 3}
        adv = self._advanced   # shorthand

        ttk.Label(
            parent,
            text=(
                "Load a yield / die-level CSV with SORT_X, SORT_Y, SORT_LOT and "
                "DevRevStep.  The tool auto-locates the 9-site PCM etest file, "
                "reconstructs full-wafer PCM via IDW, and merges the values into "
                "the yield CSV."
            ),
            style="Desc.TLabel", wraplength=720, justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 2))

        if not adv:
            ttk.Label(
                parent,
                text="Simple mode — etest, reticle map and material are auto-detected.  "
                     "Run  dashboard.py -d  for advanced options.",
                foreground=self.OK_FG, background=self.BG,
                font=("Segoe UI", 8, "italic"),
            ).grid(row=0, column=0, columnspan=3, sticky="e", padx=12, pady=(10, 2))

        ttk.Separator(parent, orient="horizontal").grid(
            row=1, column=0, columnspan=3, sticky="ew", padx=10, pady=(4, 2))

        # ── Section: Input ────────────────────────────────────────────────────
        ttk.Label(parent, text="① Input Yield / Die-level CSV(s)  [CSV or ZIP]",
                  style="Section.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 0))
        ttk.Label(parent,
                  text="Must contain columns: SORT_X, SORT_Y, SORT_LOT, DevRevStep "
                       "(case-insensitive).  Add individual CSVs or ZIP archives.",
                  style="Desc.TLabel").grid(
            row=3, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 2))

        # File list + buttons
        list_frame = ttk.Frame(parent, style="TFrame")
        list_frame.grid(row=4, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 2))
        list_frame.columnconfigure(0, weight=1)

        self._input_listbox = tk.Listbox(
            list_frame, height=4, selectmode="extended",
            bg=self.ENTRY_BG, fg=self.FG, font=("Consolas", 8),
            relief="flat", bd=0, highlightthickness=1,
            highlightcolor=self.ACCENT, activestyle="none",
            selectbackground=self.ACCENT, selectforeground="#ffffff",
        )
        self._input_listbox.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        _vsb = ttk.Scrollbar(list_frame, orient="vertical",
                             command=self._input_listbox.yview)
        _vsb.grid(row=0, column=1, sticky="ns")
        self._input_listbox.config(yscrollcommand=_vsb.set)

        btn_frame = ttk.Frame(parent, style="TFrame")
        btn_frame.grid(row=5, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 2))
        ttk.Button(btn_frame, text="+ Add CSV(s)", style="Browse.TButton",
                   command=self._add_input_csvs).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="+ Add ZIP", style="Browse.TButton",
                   command=self._add_input_zip).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="✕ Remove Selected", style="Browse.TButton",
                   command=self._remove_selected_inputs).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Clear All", style="Browse.TButton",
                   command=lambda: self._input_listbox.delete(0, "end")
                   ).pack(side="left")

        # Detected metadata (shown for the first file in the list)
        info_frame = ttk.Frame(parent, style="TFrame")
        info_frame.grid(row=6, column=0, columnspan=3, sticky="ew", padx=14, pady=(0, 4))
        ttk.Label(info_frame, text="DevRevStep:", style="Desc.TLabel").pack(side="left")
        self._drs_var = tk.StringVar(value="—")
        ttk.Label(info_frame, textvariable=self._drs_var,
                  foreground=self.SECTION, background=self.BG,
                  font=("Consolas", 9)).pack(side="left", padx=(4, 20))
        ttk.Label(info_frame, text="SORT_LOT:", style="Desc.TLabel").pack(side="left")
        self._lot_var = tk.StringVar(value="—")
        ttk.Label(info_frame, textvariable=self._lot_var,
                  foreground=self.SECTION, background=self.BG,
                  font=("Consolas", 9)).pack(side="left", padx=(4, 0))

        ttk.Separator(parent, orient="horizontal").grid(
            row=6, column=0, columnspan=3, sticky="ew", padx=10, pady=(4, 2))

        # ── Sections ②③④⑤ — only shown in advanced mode ─────────────────────
        # Vars are always initialised so _run/_pipeline_thread always work.
        self._etest_var      = tk.StringVar()
        self._rmap_var       = tk.StringVar()
        self._mat_var        = tk.StringVar()
        self._pcm_filter_var = tk.StringVar(value="*Con*")
        self._pcm_groups: list = []   # [{name, patterns}, ...] from default setup
        self._use_full_site  = tk.BooleanVar(value=False)
        self._alpha_var      = tk.DoubleVar(value=0.5)
        self._fs_status_var  = tk.StringVar(value="(not detected yet)")
        self._wafer_var      = tk.StringVar(value="all")
        self._wafer_combo    = None   # may stay None in simple mode

        # ── PCM parameter groups — always visible ─────────────────────────────
        ttk.Label(parent, text="Parameter Groups", style="Section.TLabel").grid(
            row=7, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 0))
        ttk.Label(parent,
                  text="Select which parameter groups to merge.  "
                       "Use the custom filter for extra wildcard patterns.",
                  style="Desc.TLabel", wraplength=680, justify="left",
        ).grid(row=8, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 2))

        grp_outer = ttk.Frame(parent, style="TFrame")
        grp_outer.grid(row=9, column=0, columnspan=3, sticky="ew",
                        padx=14, pady=(0, 2))
        grp_outer.columnconfigure(0, weight=1)

        self._grp_lb = tk.Listbox(
            grp_outer, selectmode="extended", height=5,
            bg=self.ENTRY_BG, fg=self.FG, selectbackground=self.ACCENT,
            selectforeground="#ffffff", font=("Consolas", 8),
            borderwidth=1, relief="sunken", exportselection=False,
        )
        self._grp_lb.grid(row=0, column=0, sticky="ew")
        grp_sb = ttk.Scrollbar(grp_outer, orient="vertical",
                                command=self._grp_lb.yview)
        grp_sb.grid(row=0, column=1, sticky="ns")
        self._grp_lb.configure(yscrollcommand=grp_sb.set)

        grp_btns = ttk.Frame(grp_outer, style="TFrame")
        grp_btns.grid(row=0, column=2, sticky="n", padx=(6, 0))
        ttk.Button(grp_btns, text="Select All", style="Browse.TButton",
                   command=lambda: self._grp_lb.select_set(0, "end")
                   ).pack(fill="x", pady=1)
        ttk.Button(grp_btns, text="Clear", style="Browse.TButton",
                   command=lambda: self._grp_lb.select_clear(0, "end")
                   ).pack(fill="x", pady=1)

        # Custom wildcard filter
        filt_frame = ttk.Frame(parent, style="TFrame")
        filt_frame.grid(row=10, column=0, columnspan=3, sticky="ew",
                         padx=14, pady=(2, 2))
        ttk.Label(filt_frame, text="Custom filter:", style="Desc.TLabel").pack(side="left")
        ttk.Entry(filt_frame, textvariable=self._pcm_filter_var, width=22).pack(
            side="left", padx=6)
        ttk.Label(filt_frame,
                  text="extra wildcard(s), comma-separated  e.g.  *Rs*,*Rc*   (merged with groups)",
                  style="Desc.TLabel").pack(side="left", padx=(0, 4))

        # Populate groups on startup
        self._load_merge_groups()

        # ── Full-site hybrid toggle — always visible (enabled only when files found) ──
        hybrid_frame = ttk.Frame(parent, style="TFrame")
        hybrid_frame.grid(row=11, column=0, columnspan=3, sticky="ew", padx=14, pady=(2, 4))
        self._fs_check = ttk.Checkbutton(
            hybrid_frame, text="Use full-site Hybrid mode  (auto-detected; checked when data available)",
            variable=self._use_full_site, command=self._on_fs_toggle,
        )
        self._fs_check.pack(side="left")
        ttk.Label(hybrid_frame, textvariable=self._fs_status_var,
                  foreground=self.DESC_FG, background=self.BG,
                  font=("Consolas", 8)).pack(side="left", padx=(10, 0))

        ttk.Separator(parent, orient="horizontal").grid(
            row=12, column=0, columnspan=3, sticky="ew", padx=10, pady=(2, 2))

        if adv:
            # ── Section: Etest PCM ────────────────────────────────────────────
            ttk.Label(parent, text="② 9-Site Etest PCM CSV  (auto-detected)",
                      style="Section.TLabel").grid(
                row=13, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 0))
            ttk.Label(parent, text=f"Looked up in: {_NINE_SITE_DIR}",
                      style="Desc.TLabel").grid(
                row=14, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 2))
            ttk.Label(parent, text="File:").grid(row=15, column=0, sticky="e", **pad)
            ttk.Entry(parent, textvariable=self._etest_var, width=58).grid(
                row=15, column=1, sticky="ew", padx=4)
            ttk.Button(parent, text="Browse…", style="Browse.TButton",
                       command=self._browse_etest).grid(row=15, column=2, padx=4)

            # Wafer filter
            wf_frame = ttk.Frame(parent, style="TFrame")
            wf_frame.grid(row=16, column=0, columnspan=3, sticky="ew", padx=14, pady=(0, 2))
            ttk.Label(wf_frame, text="Wafer(s) to use:", style="Desc.TLabel").pack(side="left")
            self._wafer_combo = ttk.Combobox(wf_frame, textvariable=self._wafer_var,
                                             state="readonly", width=18)
            self._wafer_combo["values"] = ["all"]
            self._wafer_combo.pack(side="left", padx=6)
            ttk.Button(wf_frame, text="Refresh", style="Browse.TButton",
                       command=self._refresh_wafers).pack(side="left")

            ttk.Separator(parent, orient="horizontal").grid(
                row=17, column=0, columnspan=3, sticky="ew", padx=10, pady=(4, 2))

            # ── Section: Full-site reference ──────────────────────────────────
            # (checkbox is always-visible at row=11; this section adds α control)
            ttk.Label(parent, text="③ Full-Site Reference  (optional, auto-detected)",
                      style="Section.TLabel").grid(
                row=18, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 0))
            ttk.Label(parent,
                      text=(f"All lots for this technology from: {_FULL_SITE_DIR}\n"
                            "When enabled, all matching lot files are combined → better accuracy."),
                      style="Desc.TLabel", wraplength=680, justify="left").grid(
                row=19, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 2))

            # Alpha blending slider (adv only)
            alpha_frame = ttk.Frame(parent, style="TFrame")
            alpha_frame.grid(row=20, column=0, columnspan=3, sticky="ew", padx=14, pady=(0, 4))
            ttk.Label(alpha_frame, text="Blending α  (1.0 = pure IDW,  0.0 = pure shape):",
                      style="Desc.TLabel").pack(side="left")
            self._alpha_slider = tk.Scale(
                alpha_frame, from_=0.0, to=1.0, resolution=0.05,
                orient="horizontal", length=200, variable=self._alpha_var,
                bg=self.PANEL, fg=self.FG, troughcolor=self.ENTRY_BG,
                highlightthickness=0, relief="flat")
            self._alpha_slider.pack(side="left", padx=(8, 4))
            self._alpha_lbl = ttk.Label(alpha_frame, text="0.50", style="Desc.TLabel",
                                        foreground=self.SECTION, background=self.BG,
                                        font=("Consolas", 9))
            self._alpha_lbl.pack(side="left")
            self._alpha_var.trace_add("write", lambda *_: self._alpha_lbl.config(
                text=f"{self._alpha_var.get():.2f}"))

            ttk.Separator(parent, orient="horizontal").grid(
                row=21, column=0, columnspan=3, sticky="ew", padx=10, pady=(4, 2))

            # ── Section: Reticle Mapping ──────────────────────────────────────
            ttk.Label(parent, text="④ Reticle Mapping CSV  (auto-detected)",
                      style="Section.TLabel").grid(
                row=22, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 0))
            ttk.Label(parent, text="Maps SORT_X/Y → LayoutX/LayoutY for IDW reconstruction.",
                      style="Desc.TLabel").grid(
                row=23, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 2))
            ttk.Label(parent, text="File:").grid(row=24, column=0, sticky="e", **pad)
            ttk.Entry(parent, textvariable=self._rmap_var, width=58).grid(
                row=24, column=1, sticky="ew", padx=4)
            ttk.Button(parent, text="Browse…", style="Browse.TButton",
                       command=self._browse_rmap).grid(row=24, column=2, padx=4)

            ttk.Separator(parent, orient="horizontal").grid(
                row=25, column=0, columnspan=3, sticky="ew", padx=10, pady=(4, 2))

            # ── Section: Material / Lot Info ──────────────────────────────────
            ttk.Label(parent, text="⑤ Material / Lot Info CSV  (auto-detected, optional)",
                      style="Section.TLabel").grid(
                row=26, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 0))
            ttk.Label(parent,
                      text="Adds Material Type, Device Skew, MG4 split, AIO/BB, Vy CD+ … "
                           "joined on INTEL_LOT7 ↔ SORT_LOT[:7].",
                      style="Desc.TLabel").grid(
                row=27, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 2))
            ttk.Label(parent, text="File:").grid(row=28, column=0, sticky="e", **pad)
            ttk.Entry(parent, textvariable=self._mat_var, width=58).grid(
                row=28, column=1, sticky="ew", padx=4)
            ttk.Button(parent, text="Browse…", style="Browse.TButton",
                       command=self._browse_material).grid(row=28, column=2, padx=4)

            ttk.Separator(parent, orient="horizontal").grid(
                row=29, column=0, columnspan=3, sticky="ew", padx=10, pady=(4, 2))

        # ── Section: Output (always shown) ────────────────────────────────────
        _out_row = 30 if adv else 13
        ttk.Label(parent, text="② Output Folder" if not adv else "⑥ Output Folder",
                  style="Section.TLabel").grid(
            row=_out_row, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 0))
        ttk.Label(parent,
                  text="Each input file is saved as  <filename>-merged.csv  in this folder.",
                  style="Desc.TLabel").grid(
            row=_out_row+1, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 2))
        self._out_var = tk.StringVar()
        ttk.Label(parent, text="Folder:").grid(row=_out_row+2, column=0, sticky="e", **pad)
        ttk.Entry(parent, textvariable=self._out_var, width=58).grid(
            row=_out_row+2, column=1, sticky="ew", padx=4)
        ttk.Button(parent, text="Browse…", style="Browse.TButton",
                   command=self._browse_output).grid(row=_out_row+2, column=2, padx=4)

        ttk.Separator(parent, orient="horizontal").grid(
            row=_out_row+3, column=0, columnspan=3, sticky="ew", padx=10, pady=8)

        # ── Run / Save / Load buttons ─────────────────────────────────────────
        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=_out_row+4, column=0, columnspan=3, pady=4)
        ttk.Button(btn_frame, text="📂 Load Setup",
                   command=self._load_config).pack(side="left", padx=6)
        self._run_btn = ttk.Button(btn_frame, text="▶  Run Merge",
                                   style="Run.TButton", command=self._run)
        self._run_btn.pack(side="left", padx=6)
        ttk.Button(btn_frame, text="💾 Save Setup",
                   command=self._save_config).pack(side="left", padx=6)

        # ── Log console ───────────────────────────────────────────────────────
        ttk.Label(parent, text="Log", style="Section.TLabel").grid(
            row=_out_row+5, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 0))
        self._log_box = scrolledtext.ScrolledText(
            parent, height=10, state="disabled",
            bg="#11111b", fg=self.FG, font=("Consolas", 9),
            relief="flat", bd=0, insertbackground=self.FG,
        )
        self._log_box.grid(row=_out_row+6, column=0, columnspan=3,
                           sticky="nsew", padx=10, pady=(2, 10))
        self._log_box.tag_config("err", foreground=self.ERR_FG)
        self._log_box.tag_config("ok",  foreground=self.OK_FG)

        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(_out_row+6, weight=1)

        # Bind auto-fill callbacks
        self._input_listbox.bind("<<ListboxSelect>>", self._on_list_select)
        self._input_listbox.bind("<ButtonRelease-1>", self._on_list_select)

    # ─────────────────────────────────────────────────────────────────────────
    # Browse callbacks
    # ─────────────────────────────────────────────────────────────────────────

    def _add_input_csvs(self):
        init_dir = (
            _YIELD_DATA_DIR if os.path.isdir(_YIELD_DATA_DIR)
            else os.path.expanduser("~")
        )
        paths = filedialog.askopenfilenames(
            title="Add Input CSV(s) / GZ(s)",
            initialdir=init_dir,
            filetypes=[("CSV / GZ files", "*.csv *.gz *.csv.gz"), ("CSV", "*.csv"),
                       ("GZ", "*.gz *.csv.gz"), ("All files", "*.*")],
        )
        for p in paths:
            self._add_to_list(p)
        if paths:
            self._on_list_first()

    def _add_input_zip(self):
        init_dir = (
            _YIELD_DATA_DIR if os.path.isdir(_YIELD_DATA_DIR)
            else os.path.expanduser("~")
        )
        p = filedialog.askopenfilename(
            title="Add ZIP archive containing CSV(s)",
            initialdir=init_dir,
            filetypes=[("ZIP archives", "*.zip"), ("All files", "*.*")],
        )
        if p:
            self._add_to_list(p)
            self._on_list_first()

    def _add_to_list(self, path):
        """Add path to listbox if not already present."""
        existing = list(self._input_listbox.get(0, "end"))
        if path not in existing:
            self._input_listbox.insert("end", path)

    def _remove_selected_inputs(self):
        for idx in reversed(self._input_listbox.curselection()):
            self._input_listbox.delete(idx)

    def _on_list_select(self, _event=None):
        """When user clicks the list, refresh metadata from the first selected item."""
        sel = self._input_listbox.curselection()
        if sel:
            self._refresh_metadata_from(self._input_listbox.get(sel[0]))

    def _on_list_first(self):
        """Refresh metadata from the first item in the list."""
        if self._input_listbox.size() > 0:
            self._refresh_metadata_from(self._input_listbox.get(0))

    def _refresh_metadata_from(self, path):
        """Read DevRevStep/SORT_LOT from a CSV path (or first CSV in a ZIP) and update the info labels + auto-detect etest/rmap/material."""
        import zipfile as _zf, tempfile as _tmp
        csv_path = path
        _tmpdir = None
        try:
            if path.lower().endswith(".zip"):
                _tmpdir = _tmp.mkdtemp(prefix="pcm_merge_")
                with _zf.ZipFile(path) as z:
                    csvs_in_zip = [n for n in z.namelist() if n.lower().endswith(".csv")]
                    if not csvs_in_zip:
                        return
                    z.extract(csvs_in_zip[0], _tmpdir)
                    csv_path = os.path.join(_tmpdir, csvs_in_zip[0])
            if not os.path.isfile(csv_path):
                return
            # Pass original path (ZIP or CSV) as wafer_source so _refresh_wafers
            # can scan all CSVs inside a ZIP for complete wafer list
            self._on_input_changed_path(csv_path, wafer_source=path)
        except Exception as exc:
            self._log(f"[Auto] Could not read metadata from {os.path.basename(path)}: {exc}", "err")
        finally:
            if _tmpdir:
                import shutil
                shutil.rmtree(_tmpdir, ignore_errors=True)

    def _browse_input(self):
        """Legacy single-file browse — adds to list."""
        self._add_input_csvs()

    def _browse_etest(self):
        p = filedialog.askopenfilename(
            title="Select 9-Site PCM Etest CSV",
            initialdir=_NINE_SITE_DIR if os.path.isdir(_NINE_SITE_DIR) else ".",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if p:
            self._etest_var.set(p)
            self._refresh_wafers()  # no input path → falls back to etest CSV

    def _browse_rmap(self):
        p = filedialog.askopenfilename(
            title="Select Reticle Mapping CSV",
            initialdir=_RETICLE_DIR if os.path.isdir(_RETICLE_DIR) else ".",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if p:
            self._rmap_var.set(p)

    def _browse_material(self):
        p = filedialog.askopenfilename(
            title="Select Material / Lot Definition CSV",
            initialdir=_MATERIAL_DIR if os.path.isdir(_MATERIAL_DIR) else ".",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if p:
            self._mat_var.set(p)

    def _browse_output(self):
        p = filedialog.askdirectory(
            title="Select Output Folder",
            initialdir=(
                _YIELD_DATA_DIR if os.path.isdir(_YIELD_DATA_DIR)
                else os.path.expanduser("~")
            ),
        )
        if p:
            self._out_var.set(p)

    # ─────────────────────────────────────────────────────────────────────────
    # Auto-fill callbacks
    # ─────────────────────────────────────────────────────────────────────────

    def _try_autoload_input(self):
        """If _YIELD_DATA_DIR exists, auto-add CSVs to the listbox."""
        if not os.path.isdir(_YIELD_DATA_DIR):
            return
        csvs = [
            f for f in os.listdir(_YIELD_DATA_DIR)
            if f.lower().endswith(".csv") or f.lower().endswith(".gz")
        ]
        if len(csvs) == 1:
            full = os.path.join(_YIELD_DATA_DIR, csvs[0])
            self._add_to_list(full)
            self._on_list_first()
            # Auto-set output folder
            if not self._out_var.get():
                self._out_var.set(_YIELD_DATA_DIR)

    def _on_input_changed(self, *_):
        """Legacy trace callback — no longer used for StringVar. Kept for compat."""
        pass

    def _on_input_changed_path(self, path: str, wafer_source: str = ""):
        """Read DevRevStep+SORT_LOT from path and trigger auto-detect.
        wafer_source: original file to pass to _refresh_wafers (may be a ZIP)."""
        if not path or not os.path.isfile(path):
            return
        wafer_source = wafer_source or path
        try:
            # Read just first few rows to extract metadata
            df_head = pd.read_csv(path, nrows=10)
            df_head = _normalise_sort_cols(df_head)
            drs, lot = _infer_devrevstep_lot(df_head)

            self._drs_var.set(drs or "—")
            self._lot_var.set(lot or "—")

            if drs and lot:
                etest_path, found = _guess_etest_path(drs, lot)
                if found:
                    self._etest_var.set(etest_path)
                    self._log(f"[Auto] Etest file: {_zip_basename(etest_path)}")
                    self._refresh_wafers(wafer_source)  # scan ZIP or CSV for full wafer list
                else:
                    self._log(
                        f"[Auto] Exact etest file not found for lot {lot!r}.\n"
                        f"       Expected: {os.path.basename(etest_path)}\n"
                        f"       Available in 9-sites/:\n"
                        + "\n".join(f"         {f}" for f in _list_available_etest_files()[:12]),
                        "err",
                    )

                rmap_path = _guess_reticle_map(drs)
                if rmap_path:
                    self._rmap_var.set(rmap_path)
                else:
                    self._log("[Auto] Reticle mapping not auto-detected — please browse.", "err")

                # Material / lot-definition auto-detect
                mat_path = _guess_material_file(drs, lot)
                if mat_path:
                    self._mat_var.set(mat_path)
                    self._log(f"[Auto] Material: {os.path.basename(mat_path)}")
                else:
                    self._log("[Auto] Material file not auto-detected — browse if available.")

                # Full-site detection
                fs_files = _guess_full_site_files(drs)
                self._full_site_files = fs_files
                if fs_files:
                    self._fs_status_var.set(
                        f"✓  {len(fs_files)} file(s) found — check box to use"
                    )
                    self._use_full_site.set(True)   # auto-enable when available
                    self._log(
                        f"[Auto] Full-site: {len(fs_files)} file(s) for "
                        f"{os.path.basename(os.path.dirname(fs_files[0]))}/"
                        f"{os.path.basename(fs_files[0])} … (auto-enabled)"
                    )
                else:
                    self._fs_status_var.set("(no full-site files found for this technology)")
                    self._use_full_site.set(False)

        except Exception as exc:
            self._log(f"[Auto] Could not read input CSV: {exc}", "err")

    def _on_fs_toggle(self):
        if self._use_full_site.get() and not self._full_site_files:
            messagebox.showwarning(
                "No full-site files",
                "No full-site reference files were detected for this technology.\n"
                "The 9-site CSV will be used instead.",
            )
            self._use_full_site.set(False)

    def _autofill_output(self, *_):
        """No longer used — output is a folder now."""
        pass

    @staticmethod
    def _decode_sort_wafer(val: str) -> str:
        """
        Convert a SORT_WAFER value to the etest Wafer ID.
        SORT_WAFER is 3 digits where the first digit is a lot/step prefix:
          e.g.  '803' → last 2 digits '03' → int → '3'
                '814' → '14'
        Values that are already 1-2 digits are returned as-is (int-stripped).
        """
        s = val.strip()
        if len(s) == 3 and s.isdigit():
            return str(int(s[-2:]))   # '803' → '3',  '814' → '14'
        try:
            return str(int(s))        # strip leading zeros on shorter values
        except ValueError:
            return s

    _WAFER_COL_CANDIDATES = ["Wafer", "WAFER", "wafer", "WaferID", "Wafer_ID",
                              "SORT_WAFER", "Sort_Wafer"]

    def _refresh_wafers(self, input_csv_path: str = ""):
        """Populate the wafer combo from the INPUT yield file (CSV or ZIP).
        Shows raw values from the input; decoding happens later at run-time.
        Falls back to the etest CSV only if no wafer column is found in the input.
        ZIP scanning runs in a background thread to avoid blocking the UI."""
        import zipfile as _zf, tempfile as _tmp
        _wafer_cols = set(self._WAFER_COL_CANDIDATES)
        _sort_wafer_cols = {"SORT_WAFER", "Sort_Wafer"}

        def _collect_wafers_from_csv(csv_path):
            """Return (wafer_col, set_of_raw_values) from a single CSV, or (None, set())."""
            try:
                df = pd.read_csv(csv_path, usecols=lambda c: c in _wafer_cols)
                wc = next((c for c in self._WAFER_COL_CANDIDATES if c in df.columns), None)
                if wc:
                    return wc, set(df[wc].dropna().astype(str).unique())
            except Exception:
                pass
            return None, set()

        def _apply_wafers(found_col, all_raw):
            """Update the combo on the main thread."""
            if found_col and all_raw:
                wafers_raw = sorted(all_raw, key=lambda v: (int(v) if v.isdigit() else v))
                if self._wafer_combo:
                    self._wafer_combo["values"] = ["all"] + wafers_raw
                self._wafer_var.set("all")
                self._input_wafer_is_sort_wafer = found_col in _sort_wafer_cols
            else:
                # fallback: read from etest CSV
                etest_path = self._etest_var.get().strip()
                if not etest_path or not os.path.isfile(etest_path):
                    return
                try:
                    df = pd.read_csv(etest_path,
                                     usecols=lambda c: c in _wafer_cols)
                    wc = next(
                        (c for c in self._WAFER_COL_CANDIDATES if c in df.columns), None
                    )
                    if wc:
                        wafers = sorted(df[wc].dropna().astype(str).unique(),
                                        key=lambda v: (int(v) if v.isdigit() else v))
                        if self._wafer_combo:
                            self._wafer_combo["values"] = ["all"] + wafers
                        self._wafer_var.set("all")
                except Exception:
                    pass

        def _scan_in_background():
            all_raw = set()
            found_col = None
            try:
                if input_csv_path.lower().endswith(".zip") and os.path.isfile(input_csv_path):
                    tmpdir = _tmp.mkdtemp(prefix="pcm_wfr_")
                    try:
                        with _zf.ZipFile(input_csv_path) as z:
                            csv_names = [n for n in z.namelist() if n.lower().endswith(".csv")]
                            for name in csv_names:
                                z.extract(name, tmpdir)
                                wc, vals = _collect_wafers_from_csv(os.path.join(tmpdir, name))
                                if wc:
                                    found_col = wc
                                    all_raw |= vals
                    finally:
                        import shutil
                        shutil.rmtree(tmpdir, ignore_errors=True)
                elif input_csv_path and os.path.isfile(input_csv_path):
                    found_col, all_raw = _collect_wafers_from_csv(input_csv_path)
            except Exception:
                pass
            # Schedule combo update back on main thread
            self.after(0, lambda: _apply_wafers(found_col, all_raw))

        if input_csv_path:
            threading.Thread(target=_scan_in_background, daemon=True).start()
        else:
            _apply_wafers(None, set())

    # ─────────────────────────────────────────────────────────────────────────
    # Save / Load config
    # ─────────────────────────────────────────────────────────────────────────

    def _collect_config(self) -> dict:
        """Gather all GUI fields into a serialisable dict."""
        return {
            "input_files":   list(self._input_listbox.get(0, "end")),
            "etest_csv":     self._etest_var.get(),
            "rmap_csv":      self._rmap_var.get(),
            "material_csv":  self._mat_var.get(),
            "output_folder": self._out_var.get(),
            "wafer_filter":  self._wafer_var.get(),
            "pcm_filter":    self._pcm_filter_var.get(),
            "selected_groups": [
                self._pcm_groups[i]["name"]
                for i in self._grp_lb.curselection()
                if i < len(self._pcm_groups)
            ],
            "use_full_site": self._use_full_site.get(),
            "alpha":         round(self._alpha_var.get(), 3),
        }

    def _apply_config(self, cfg: dict):
        """Populate all GUI fields from a config dict."""
        # Input file list
        self._input_listbox.delete(0, "end")
        for p in cfg.get("input_files", []):
            self._add_to_list(p)

        # Load etest path but clear it if stale (auto-detect will re-fill it)
        _et = cfg.get("etest_csv", "")
        self._etest_var.set(_et if _et and _zip_isfile(_et) else "")
        self._rmap_var.set(cfg.get("rmap_csv", ""))
        self._mat_var.set(cfg.get("material_csv", ""))
        self._out_var.set(cfg.get("output_folder", ""))
        self._wafer_var.set(cfg.get("wafer_filter", "all"))
        self._pcm_filter_var.set(cfg.get("pcm_filter", ""))
        self._use_full_site.set(bool(cfg.get("use_full_site", False)))
        alpha = cfg.get("alpha", 0.5)
        try:
            self._alpha_var.set(float(alpha))
        except (TypeError, ValueError):
            self._alpha_var.set(0.5)

        # Restore parameter group selection
        target_groups = set(cfg.get("selected_groups") or [])
        if target_groups:
            self._grp_lb.select_clear(0, "end")
            for i, g in enumerate(self._pcm_groups):
                if g["name"] in target_groups:
                    self._grp_lb.selection_set(i)

        # Refresh metadata display from first input if present
        paths = list(self._input_listbox.get(0, "end"))
        if paths:
            self._refresh_metadata_from(paths[0])

    def _save_config(self):
        path = filedialog.asksaveasfilename(
            title="Save GUI Setup",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            cfg = self._collect_config()
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2)
            messagebox.showinfo("Saved", f"Setup saved to:\n{path}")
        except Exception as exc:
            messagebox.showerror("Save Error", str(exc))

    def _load_config(self):
        path = filedialog.askopenfilename(
            title="Load GUI Setup",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            self._apply_config(cfg)
        except Exception as exc:
            messagebox.showerror("Load Error", str(exc))

    # Run
    # ─────────────────────────────────────────────────────────────────────────

    def _run(self):
        input_paths  = list(self._input_listbox.get(0, "end"))
        etest_csv    = self._etest_var.get().strip()
        rmap_csv     = self._rmap_var.get().strip()
        out_folder   = self._out_var.get().strip()
        wafer_sel    = self._wafer_var.get().strip()
        use_fs       = self._use_full_site.get() and bool(self._full_site_files)

        if not input_paths:
            messagebox.showerror("No input", "Add at least one input CSV or ZIP file.")
            return

        for label, path in [("Reticle mapping CSV", rmap_csv)]:
            if not path:
                messagebox.showerror("Missing input", f"Please select: {label}")
                return
            if not os.path.isfile(path):
                messagebox.showerror("File not found", f"{label} not found:\n{path}")
                return

        if not use_fs:
            if etest_csv and not _zip_isfile(etest_csv):
                messagebox.showerror("File not found", f"9-site etest CSV not found:\n{etest_csv}")
                return
            # etest_csv may be blank — pipeline will skip PCM merge and do reticle+material only

        if not out_folder:
            messagebox.showerror("Missing output", "Please specify an output folder.")
            return
        os.makedirs(out_folder, exist_ok=True)

        wafer_filter   = None if wafer_sel == "all" else [
            self._decode_sort_wafer(wafer_sel)
            if getattr(self, "_input_wafer_is_sort_wafer", False)
            else wafer_sel
        ]
        full_site_csvs = self._full_site_files if use_fs else None
        alpha          = round(self._alpha_var.get(), 2)
        material_csv   = self._mat_var.get().strip()
        pcm_filter     = self._get_combined_pcm_filter()

        self._run_btn.config(state="disabled")
        threading.Thread(
            target=self._pipeline_thread,
            args=(input_paths, etest_csv, rmap_csv, out_folder,
                  wafer_filter, full_site_csvs, alpha, material_csv, pcm_filter),
            daemon=True,
        ).start()

    def _pipeline_thread(self, input_paths, etest_csv, rmap_csv, out_folder,
                         wafer_filter, full_site_csvs, alpha, material_csv, pcm_filter):
        import zipfile as _zf, tempfile as _tmp
        import datetime

        # Collect all log lines for the log file
        _log_lines = []
        _log_lines.append(f"PCM Merge Log — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        _log_lines.append(f"Output folder : {out_folder}")
        _log_lines.append(f"Etest CSV     : {etest_csv or '(none — reticle+material only)'}")
        _log_lines.append(f"Reticle map   : {rmap_csv}")
        _log_lines.append(f"Material CSV  : {material_csv or '(none)'}")
        _log_lines.append(f"Wafer filter  : {wafer_filter or 'all'}")
        _log_lines.append(f"PCM filter    : {pcm_filter or '(none)'}")
        _log_lines.append(f"Full-site     : {len(full_site_csvs) if full_site_csvs else 0} file(s)")
        _log_lines.append(f"Alpha         : {alpha}")
        _log_lines.append("-" * 70)

        def _log_capture(msg, tag=""):
            self._log(msg, tag)
            _log_lines.append(msg)

        try:
            # ── Clean output folder before each run ───────────────────────────
            import shutil, glob as _glob
            if os.path.isdir(out_folder):
                removed = []
                # Remove *-merged.csv files
                for f in _glob.glob(os.path.join(out_folder, "*-merged.csv")):
                    os.remove(f)
                    removed.append(os.path.basename(f))
                # Remove spec-violation subfolder
                viol_dir = os.path.join(out_folder, "spec-violation")
                if os.path.isdir(viol_dir):
                    shutil.rmtree(viol_dir)
                    removed.append("spec-violation/")
                # Remove old merge-log files
                for f in _glob.glob(os.path.join(out_folder, "merge-log-*.txt")):
                    os.remove(f)
                    removed.append(os.path.basename(f))
                if removed:
                    self._log(f"[Clean] Removed {len(removed)} item(s) from output folder")
            os.makedirs(out_folder, exist_ok=True)

            # Expand ZIPs into individual CSV paths
            all_csv_tasks = []   # list of (csv_path, stem, tmpdir_or_None)
            for src in input_paths:
                if src.lower().endswith(".zip"):
                    tmpdir = _tmp.mkdtemp(prefix="pcm_merge_")
                    with _zf.ZipFile(src) as z:
                        csvs_in_zip = [n for n in z.namelist() if n.lower().endswith(".csv")]
                        if not csvs_in_zip:
                            self._log(f"[Skip ] ZIP has no CSVs: {os.path.basename(src)}", "err")
                            continue
                        self._log(f"[ZIP  ] {os.path.basename(src)}: "
                                  f"{len(csvs_in_zip)} CSV file(s) found inside")
                        for name in csvs_in_zip:
                            z.extract(name, tmpdir)
                            csv_path = os.path.join(tmpdir, name)
                            stem = os.path.splitext(os.path.basename(name))[0]
                            all_csv_tasks.append((csv_path, stem, tmpdir))
                else:
                    _bn = os.path.basename(src)
                    stem = os.path.splitext(_bn)[0]
                    if stem.lower().endswith('.csv'):   # strip .csv from .csv.gz
                        stem = os.path.splitext(stem)[0]
                    all_csv_tasks.append((src, stem, None))

            if not all_csv_tasks:
                self._log("[Error] No valid CSV files to process.", "err")
                return

            n_total = len(all_csv_tasks)
            self._log(f"[Batch] {n_total} file(s) to process")

            last_df_out = None
            last_pcm_cols = []
            seen_tmpdirs = set()
            batch_violations = {}   # stem -> n_violation_rows

            for idx, (csv_path, stem, tmpdir) in enumerate(all_csv_tasks, 1):
                _log_capture(f"\n─── [{idx}/{n_total}] {stem} ───")
                out_csv = os.path.join(out_folder, f"{stem}-merged.csv")
                try:
                    # ── Per-file auto-detect material CSV and etest if not locked ──
                    _drs, _lot = _infer_devrevstep_lot(
                        _normalise_sort_cols(pd.read_csv(csv_path, nrows=5))
                    )
                    # ── Per-file auto-detect: material, etest, rmap ───────────
                    _file_material_csv = material_csv
                    _file_etest_csv    = etest_csv
                    _file_rmap_csv     = rmap_csv

                    if _drs:
                        _prefix6 = _drs[:6]  # e.g. '8PF5CV' or '8PF6CV'

                        # Material: always pick the file that contains this lot
                        _auto_mat = _guess_material_file(_drs, _lot)
                        if not material_csv or not os.path.isfile(material_csv):
                            _file_material_csv = _auto_mat
                            if _auto_mat:
                                _log_capture(f"[Auto ] Material auto-detected: "
                                             f"{os.path.basename(_auto_mat)}")
                        elif _auto_mat and os.path.abspath(_auto_mat) != os.path.abspath(material_csv):
                            _file_material_csv = _auto_mat
                            _log_capture(
                                f"[Auto ] Material switched to "
                                f"{os.path.basename(_auto_mat)} (lot {_lot[:7]!r})"
                            )

                        # Etest: switch if GUI etest is wrong technology
                        if etest_csv and _zip_isfile(etest_csv):
                            _gui_et_prefix = _zip_basename(etest_csv)[:6]
                            if _gui_et_prefix != _prefix6:
                                _auto_et, _found = _guess_etest_path(_drs, _lot)
                                if _found:
                                    _file_etest_csv = _auto_et
                                    _log_capture(
                                        f"[Auto ] Etest switched to "
                                        f"{_zip_basename(_auto_et)} "
                                        f"(tech {_prefix6} ≠ GUI {_gui_et_prefix})"
                                    )
                        elif not etest_csv and _lot:
                            _auto_et, _found = _guess_etest_path(_drs, _lot)
                            if _found:
                                _file_etest_csv = _auto_et
                                _log_capture(f"[Auto ] Etest auto-detected: "
                                             f"{_zip_basename(_auto_et)}")

                        # Reticle map: switch if GUI rmap is wrong technology, or auto-detect if empty
                        if rmap_csv and os.path.isfile(rmap_csv):
                            _gui_rm_prefix = os.path.basename(rmap_csv)[:6]
                            if _gui_rm_prefix != _prefix6:
                                _auto_rm = _guess_reticle_map(_drs)
                                if _auto_rm and os.path.isfile(_auto_rm):
                                    _file_rmap_csv = _auto_rm
                                    _log_capture(
                                        f"[Auto ] Reticle map switched to "
                                        f"{os.path.basename(_auto_rm)} "
                                        f"(tech {_prefix6} ≠ GUI {_gui_rm_prefix})"
                                    )
                        elif not rmap_csv:
                            _auto_rm = _guess_reticle_map(_drs)
                            if _auto_rm and os.path.isfile(_auto_rm):
                                _file_rmap_csv = _auto_rm
                                _log_capture(f"[Auto ] Reticle map auto-detected: "
                                             f"{os.path.basename(_auto_rm)}")

                    df_out, pcm_cols = run_pipeline(
                        input_csv=csv_path,
                        etest_csv=_file_etest_csv,
                        reticle_map_csv=_file_rmap_csv,
                        output_csv=out_csv,
                        log=_log_capture,
                        wafer_filter=wafer_filter,
                        full_site_csvs=full_site_csvs,
                        alpha=alpha,
                        material_csv=_file_material_csv,
                        pcm_filter=pcm_filter,
                    )
                    last_df_out   = df_out
                    last_pcm_cols = pcm_cols

                    # ── Spec violation check ──────────────────────────────────
                    n_viol = write_spec_violations(df_out, pcm_cols, stem,
                                                   out_folder, _log_capture)
                    batch_violations[stem] = n_viol

                except Exception as exc:
                    import traceback
                    _log_capture(f"ERROR processing {stem}: {exc}", "err")
                    _log_capture(traceback.format_exc(), "err")
                    batch_violations[stem] = -1
                finally:
                    if tmpdir and tmpdir not in seen_tmpdirs:
                        seen_tmpdirs.add(tmpdir)

            # Cleanup temp dirs
            import shutil
            for td in seen_tmpdirs:
                shutil.rmtree(td, ignore_errors=True)

            # ── Batch violation summary ───────────────────────────────────────
            n_with_viols = sum(1 for v in batch_violations.values() if v > 0)
            if batch_violations:
                _log_capture(f"\n[Spec ] Violation summary ({n_with_viols}/{n_total} file(s) with violations):")
                for s, n in batch_violations.items():
                    tag = "err" if n > 0 else ""
                    _log_capture(f"         {'⚠' if n > 0 else '✓'}  {s}: {n} violation row(s)", tag)

            self._df_out   = last_df_out
            self._pcm_cols = last_pcm_cols


            _log_capture(f"\n[Batch] Done — {n_total} file(s) → {out_folder}", "ok")

            # ── Write log file ────────────────────────────────────────────────
            import datetime as _dt
            log_path = os.path.join(
                out_folder,
                f"merge-log-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
            )
            try:
                with open(log_path, "w", encoding="utf-8") as lf:
                    lf.write("\n".join(_log_lines))
                self._log(f"[Log  ] Log file saved → {log_path}", "ok")
            except Exception as le:
                self._log(f"[Log  ] Could not write log file: {le}", "err")

        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            self._log(f"ERROR: {exc}", "err")
            self._log(tb, "err")
            _log_lines.append(f"ERROR: {exc}")
            _log_lines.append(tb)
            # Still try to write the log even on failure
            try:
                import datetime as _dt
                log_path = os.path.join(
                    out_folder,
                    f"merge-log-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
                )
                os.makedirs(out_folder, exist_ok=True)
                with open(log_path, "w", encoding="utf-8") as lf:
                    lf.write("\n".join(_log_lines))
                self._log(f"[Log  ] Log file saved → {log_path}")
            except Exception:
                pass
        finally:
            self.after(0, lambda: self._run_btn.config(state="normal"))

    # ─────────────────────────────────────────────────────────────────────────
    # Log helper
    # ─────────────────────────────────────────────────────────────────────────

    def _log(self, msg: str, tag: str = ""):
        def _append():
            self._log_box.config(state="normal")
            self._log_box.insert("end", msg + "\n", tag)
            self._log_box.see("end")
            self._log_box.config(state="disabled")
        self.after(0, _append)

    # ─────────────────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────
    # Parameter group helpers
    # ─────────────────────────────────────────────────────────────────────────

    _DEFAULT_GROUPS = [
        {"name": "Conductance",            "patterns": ["Con_*"]},
        {"name": "Capacitance",            "patterns": ["Cmim_*", "Cmin_*"]},
        {"name": "Vts N-FET",             "patterns": ["Vts_RN*", "Vts_N*", "Vtl_N*"]},
        {"name": "Vts P-FET",             "patterns": ["Vts_RP*", "Vts_P*", "Vtl_P*"]},
        {"name": "Vts GAA / Stacked",     "patterns": ["Vts_GAA*", "Vts_GBA*", "Vts_DAA*",
                                                        "Vts_DBA*", "Vts_UAA*", "Vts_UBA*"]},
        {"name": "Isat N-FET",            "patterns": ["Isat_RN*", "Isat_N*"]},
        {"name": "Isat P-FET",            "patterns": ["Isat_RP*", "Isat_P*"]},
        {"name": "Isat GAA / Stacked",    "patterns": ["Isat_GAA*", "Isat_GBA*", "Isat_DAA*",
                                                        "Isat_DBA*", "Isat_UAA*", "Isat_UBA*"]},
        {"name": "Ioff N-FET",            "patterns": ["Ioff_RN*"]},
        {"name": "Ioff P-FET",            "patterns": ["Ioff_RP*"]},
        {"name": "Contact Resistance",    "patterns": ["Rc_*"]},
        {"name": "Sheet Resistance",      "patterns": ["Rs_*", "RDL_*", "SPA_*"]},
        {"name": "Propagation Delay",     "patterns": ["Td_*"]},
        {"name": "Power (Pwr)",            "patterns": ["Pwr_*"]},
        {"name": "Power-Off (Poff)",       "patterns": ["Poff_*"]},
        {"name": "Breakdown / Other",     "patterns": ["VbdGO_*", "VBD_*", "Isb_*"]},
    ]

    def _load_merge_groups(self):
        """Populate the parameter-group listbox from built-in defaults."""
        self._pcm_groups = list(self._DEFAULT_GROUPS)
        self._grp_lb.delete(0, "end")
        for g in self._pcm_groups:
            label = f"{g['name']:<24s}  ({', '.join(g['patterns'])})"
            self._grp_lb.insert("end", label)
        # Select all by default
        self._grp_lb.select_set(0, "end")

    def _get_combined_pcm_filter(self) -> str:
        """Build a combined wildcard filter from selected groups + custom entry.

        Returns a comma-separated string of wildcards (e.g. 'Con_*,Rc_*,*Rs*').
        Empty string means 'all parameters' (no filtering).
        """
        parts = []
        for i in self._grp_lb.curselection():
            if i < len(self._pcm_groups):
                parts.extend(self._pcm_groups[i]["patterns"])
        custom = self._pcm_filter_var.get().strip()
        if custom:
            parts.extend([p.strip() for p in custom.split(",") if p.strip()])
        return ",".join(parts)

    # ─────────────────────────────────────────────────────────────────────────
    # Close
    # ─────────────────────────────────────────────────────────────────────────

    def _on_close(self):
        try:
            self.destroy()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Standalone window wrapper — keeps etest-dashboard/dashboard.py unchanged
# ─────────────────────────────────────────────────────────────────────────────

class PCMMergeGUI(tk.Tk):
    """Thin tk.Tk wrapper around PCMMergeFrame for standalone use."""

    def __init__(self, advanced: bool = False):
        super().__init__()
        self.title("PCM Merge — Etest Dashboard" + ("  [advanced]" if advanced else ""))
        self.minsize(760, 480 if not advanced else 640)
        self.configure(bg=PCMMergeFrame.BG)

        self._frame = PCMMergeFrame(self, advanced=advanced)
        self._frame.pack(fill="both", expand=True)

        # Watermark — sits on top of the frame
        _wm = tk.Label(self, text="Pant, Sujit N — GEMS FTE",
                       bg="#6c3483", fg="white", font=("Arial", 8, "bold"),
                       padx=6, pady=2)
        _wm.place(relx=1.0, y=4, anchor="ne")
        _wm.lift()

        self.protocol("WM_DELETE_WINDOW", self._frame._on_close)
        # Auto-load if only one CSV exists in the default yield data directory
        self.after(100, self._frame._try_autoload_input)

    def _apply_config(self, cfg: dict):
        """Delegate to the embedded frame (called from etest dashboard.py)."""
        self._frame._apply_config(cfg)


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = PCMMergeGUI()
    app.mainloop()
