"""pcm_data_utils.py — Shared PCM/etest data-loading utilities.

Self-contained module used by yield-dashboard's parametric runner and any
other dashboard that needs to load PCM CSVs without depending on
etest-dashboard's internal implementation.

No imports from etest-dashboard or any other dashboard.
"""
from __future__ import annotations

import math
import os
import re
import zipfile as _zipfile_mod
from typing import Dict, List, Optional, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# Repo-root discovery
# ---------------------------------------------------------------------------

def _find_repo_root(start: str) -> str:
    d = start
    for _ in range(8):
        if os.path.isdir(os.path.join(d, "shared")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.abspath(os.path.join(start, "..", "..", ".."))


_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = _find_repo_root(_HERE)

# ---------------------------------------------------------------------------
# Path constants — all relative to shared/
# ---------------------------------------------------------------------------

_MATERIAL_DIR = os.path.join(_REPO_ROOT, "shared", "material")
_SPEC_CSV     = os.path.join(_REPO_ROOT, "shared", "spec", "wat",
                             "N2P_NVL816_WAT_PDK1.0_target.csv")
_MAT_COLS     = ["Material Type, Skew, BEOL Skew"]

# ---------------------------------------------------------------------------
# ZIP-aware file utilities
# ---------------------------------------------------------------------------

_ZIP_SEP = "::"


def _zip_basename(p: str) -> str:
    if _ZIP_SEP in p:
        return p.split(_ZIP_SEP, 1)[1].rsplit("/", 1)[-1]
    return os.path.basename(p)


def _walk_dir_and_zips(d: str):
    """Yield (fname, path) for every file under *d*, including zip contents."""
    if not os.path.isdir(d):
        return
    for root, _dirs, files in os.walk(d):
        for fname in files:
            full = os.path.join(root, fname)
            if fname.lower().endswith(".zip"):
                try:
                    with _zipfile_mod.ZipFile(full, "r") as _zf:
                        for member in _zf.namelist():
                            if member.endswith("/"):
                                continue
                            mfname = member.rsplit("/", 1)[-1]
                            yield mfname, full + _ZIP_SEP + member
                except Exception:
                    pass
            else:
                yield fname, full


def _read_csv(path: str, **kwargs) -> pd.DataFrame:
    """pandas.read_csv that transparently handles zip references (archive.zip::member)."""
    if _ZIP_SEP in path:
        zip_path, member = path.split(_ZIP_SEP, 1)
        with _zipfile_mod.ZipFile(zip_path, "r") as _zf:
            with _zf.open(member) as _fh:
                return pd.read_csv(_fh, **kwargs)
    return pd.read_csv(path, **kwargs)

# ---------------------------------------------------------------------------
# PCM data helpers
# ---------------------------------------------------------------------------

def _load_spec_lookup(path: Optional[str] = None) -> Dict[str, Tuple]:
    """Load spec CSV → {param: (sl, sh, tgt, unit, name)}."""
    _path = path if path else _SPEC_CSV
    if not os.path.isfile(_path):
        return {}
    df = pd.read_csv(_path)
    df.columns = [c.strip() for c in df.columns]

    param_col = next((c for c in ["WAT naming", "Parameter"] if c in df.columns), None)
    name_col  = next((c for c in ["Device naming", "Naming", "Name"] if c in df.columns), None)
    sl_col    = next((c for c in ["Spec Low", "Spec_Low"]   if c in df.columns), None)
    sh_col    = next((c for c in ["Spec High", "Spec_High"] if c in df.columns), None)
    tgt_col   = "Target" if "Target" in df.columns else None
    unit_col  = "Unit"   if "Unit"   in df.columns else None

    if param_col is None:
        return {}

    def _fv(row, col):
        if col is None:
            return float("nan")
        try:
            v = row[col]
            return float(v) if pd.notna(v) else float("nan")
        except Exception:
            return float("nan")

    lookup: Dict[str, Tuple] = {}
    seen: set = set()
    for _, row in df.iterrows():
        p = str(row.get(param_col, "")).strip()
        if not p or p in seen:
            continue
        seen.add(p)
        sl   = _fv(row, sl_col)
        sh   = _fv(row, sh_col)
        tgt  = _fv(row, tgt_col)
        unit = str(row.get(unit_col, "")).strip() if unit_col else ""
        name = str(row.get(name_col, "")).strip() if name_col else ""
        if not (math.isnan(sl) and math.isnan(sh) and math.isnan(tgt)):
            lookup[p] = (sl, sh, tgt, unit, name)
    return lookup


def _find_material_csv(tech_prefix: str, lot7: str) -> Optional[str]:
    """Return the material CSV that best matches tech_prefix and lot7."""
    if not os.path.isdir(_MATERIAL_DIR):
        return None
    candidates = [
        os.path.join(_MATERIAL_DIR, f)
        for f in sorted(os.listdir(_MATERIAL_DIR))
        if f.lower().endswith(".csv") and tech_prefix.lower() in f.lower()
    ]
    if lot7 and candidates:
        for fpath in candidates:
            try:
                col_check = pd.read_csv(fpath, nrows=1).columns.tolist()
                if "INTEL_LOT7" not in col_check:
                    continue
                lots_in_file = set(
                    pd.read_csv(fpath, usecols=["INTEL_LOT7"])
                    ["INTEL_LOT7"].dropna().astype(str).str.strip()
                )
                if lot7 in lots_in_file:
                    return fpath
            except Exception:
                pass
        return candidates[0]
    if candidates:
        return candidates[0]
    for fname in sorted(os.listdir(_MATERIAL_DIR)):
        if fname.lower().endswith(".csv") and "lot" in fname.lower():
            return os.path.join(_MATERIAL_DIR, fname)
    return None


def _get_lot_material(lot_id: str, csv_path: str) -> str:
    """Return the 'Material Type, Skew, BEOL Skew' value for this lot, or ''."""
    lot7 = lot_id[:7]
    fname = _zip_basename(csv_path)
    m = re.match(r"^([A-Z0-9]+)-[A-Z]-", fname, re.IGNORECASE)
    tech_prefix = m.group(1) if m else ""
    mat_csv = _find_material_csv(tech_prefix, lot7)
    if not mat_csv:
        return ""
    col = "Material Type, Skew, BEOL Skew"
    try:
        df_mat = pd.read_csv(mat_csv, low_memory=False)
        df_mat.columns = [c.strip() for c in df_mat.columns]
        lot7_col = "INTEL_LOT7" if "INTEL_LOT7" in df_mat.columns else None
        if lot7_col and col in df_mat.columns:
            rows = df_mat[df_mat[lot7_col].astype(str).str.strip() == lot7]
            if not rows.empty:
                val = rows[col].dropna().astype(str).str.strip()
                val = val[val.str.len() > 0]
                if not val.empty:
                    return val.iloc[0]
    except Exception:
        pass
    return ""


def _load_and_merge(
    lot_csv_map: Dict[str, str],
    log,
) -> pd.DataFrame:
    """Load PCM CSVs for each lot, join material info, combine into one DataFrame."""
    frames: List[pd.DataFrame] = []

    for lot_id, csv_path in lot_csv_map.items():
        log(f"[Load ] Lot {lot_id}: {_zip_basename(csv_path)}")
        try:
            df = _read_csv(csv_path, low_memory=False)
        except Exception as ex:
            log(f"[WARN ] Could not read {csv_path}: {ex}")
            continue

        log(f"        {len(df):,} rows, {len(df.columns)} columns")

        if "Lot" not in df.columns:
            df["Lot"] = lot_id

        lot7 = lot_id[:7]
        fname = _zip_basename(csv_path)
        m = re.match(r"^([A-Z0-9]+)-[A-Z]-", fname, re.IGNORECASE)
        tech_prefix = m.group(1) if m else ""

        mat_csv = _find_material_csv(tech_prefix, lot7)
        if mat_csv:
            log(f"[Mat  ] {os.path.basename(mat_csv)}")
            try:
                df_mat = pd.read_csv(mat_csv, low_memory=False)
                df_mat.columns = [c.strip() for c in df_mat.columns]

                lot7_col  = "INTEL_LOT7" if "INTEL_LOT7" in df_mat.columns else None
                wid_col   = next((c for c in df_mat.columns
                                  if "WAFERID" in c.upper() or "WAFER ID" in c.upper()), None)
                mat_keep  = [c for c in _MAT_COLS if c in df_mat.columns]

                if lot7_col and mat_keep:
                    df_mat["_ml7"] = df_mat[lot7_col].astype(str).str.strip()
                    if wid_col:
                        df_mat["_mwid"] = pd.to_numeric(df_mat[wid_col], errors="coerce")
                        df = pd.concat([df, pd.DataFrame(
                            {"_ml7": lot7,
                             "_mwid": pd.to_numeric(df["Wafer"], errors="coerce")},
                            index=df.index)], axis=1)
                        dedup = (df_mat[["_ml7", "_mwid"] + mat_keep]
                                 .drop_duplicates(subset=["_ml7", "_mwid"]))
                        df = df.merge(dedup, on=["_ml7", "_mwid"], how="left").copy()
                        n_matched = df[mat_keep[0]].notna().sum()
                    else:
                        df = pd.concat([df, pd.DataFrame({"_ml7": lot7}, index=df.index)], axis=1)
                        dedup = (df_mat[["_ml7"] + mat_keep]
                                 .drop_duplicates(subset=["_ml7"]))
                        df = df.merge(dedup, on="_ml7", how="left").copy()
                        n_matched = df[mat_keep[0]].notna().sum() if mat_keep else 0

                    df.drop(columns=["_ml7", "_mwid"], errors="ignore", inplace=True)
                    log(f"        Material joined: {n_matched:,}/{len(df):,} rows matched")

                    combined_col = "Material Type, Skew, BEOL Skew"
                    if combined_col in df.columns:
                        df = df.copy()
                        df["Material"] = df[combined_col].fillna("").astype(str)
                    else:
                        df["Material"] = ""

            except Exception as ex:
                log(f"[WARN ] Material join failed: {ex}")
                df["Material"] = ""
        else:
            log(f"[Mat  ] No material CSV found for {lot_id}")
            df["Material"] = ""

        frames.append(df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True, sort=False)
    log(f"[Merge] Combined: {len(combined):,} rows × {len(combined.columns)} columns")
    return combined
