"""scan-dashboard.py -- HRY Scan Analysis: GUI app + headless pipeline, in one file.

Merges the former hry_frame.py (Tkinter GUI tab), pipeline.py (RAWSTR
processing + dashboard build), and dashboard.py (HRYApp launcher) into a
single module.

Usage:
  python scan-dashboard.py                                   # launch GUI
  python scan-dashboard.py settings.scancfg.json              # launch GUI, auto-load settings
  python scan-dashboard.py --input data.csv --config hry_config.csv --output ./results
"""

from __future__ import annotations

import sys
sys.dont_write_bytecode = True
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

import re
import zlib
import json
import shutil
import argparse
import subprocess
import threading
import webbrowser
from pathlib import Path

import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

# ---------------------------------------------------------------------------
# Default paths (relative to this script → shared area)
# ---------------------------------------------------------------------------
_SRC_DIR      = Path(__file__).parent
_SCRIPT_DIR   = _SRC_DIR.parent                                   # scan-dashboard/
_REPO_ROOT    = _SRC_DIR.parents[1]                               # app.dashboard.nvl/
_SHARED_CFG   = _REPO_ROOT / "shared" / "setup" / "config" / "scan-dashboard"
_SHARED_OUT   = _SHARED_CFG                                       # default output dir
_TEMPLATE_DIR = _SRC_DIR                                          # template index.html
_PLOTLY_JS      = _REPO_ROOT / "shared" / "library" / "plotly-2.32.0.min.js"
_SHARED_RETICLE = _REPO_ROOT / "shared" / "reticle"
_SHARED_MATERIAL = _REPO_ROOT / "shared" / "material"
_WAFER_TOOLS    = Path(r"C:\scripts\app.yield.nvl\code\utilities\wafer_tools")


def _find_default_config(input_csv: str | Path | None = None) -> Path | None:
    """Return the HRY config CSV whose name starts with the devrevstep prefix of the input.

    Falls back to the first *.csv alphabetically if no match or input not given.
    Excludes yield-estimate-per-fault-count.csv (not an HRY config).
    """
    if not _SHARED_CFG.exists():
        return None
    csvs = [p for p in sorted(_SHARED_CFG.glob("*.csv"))
            if p.name != "yield-estimate-per-fault-count.csv"]
    if not csvs:
        return None
    if input_csv is not None:
        # Sniff devrevstep prefix (first 6 chars) from the first DevRevStep* column
        try:
            import pandas as _pd, re as _re_drc
            _df_hdr = _pd.read_csv(str(input_csv), nrows=100, low_memory=False)
            _drc = next((c for c in _df_hdr.columns if c.upper().startswith('DEVREVSTEP')), None)
            if _drc:
                _prefix = str(_df_hdr[_drc].dropna().iloc[0])[:6].upper()
                _match = next((p for p in csvs if p.name.upper().startswith(_prefix)), None)
                if _match:
                    print(f"[pipeline] HRY config auto-selected by devrevstep '{_prefix}': {_match.name}")
                    return _match
        except Exception:
            pass
    return csvs[0]


def build_process_to_product_map() -> dict:
    """Parse material CSV filenames to build {process_prefix: {product, stepping}}.

    e.g. '8PF5CV-NVL816-BLLC_L0_lot_definition_l1.csv'
          → {'8PF5CV': {'product': 'NVL816-BLLC', 'stepping': 'L0'}}
    """
    import re as _re
    pmap: dict = {}
    if not _SHARED_MATERIAL.exists():
        return pmap
    for fpath in sorted(_SHARED_MATERIAL.glob("*.csv")):
        m = _re.match(r'^(8\w+?)-(.+?)_([A-Z]\d)', fpath.stem)
        if m:
            pmap[m.group(1)] = {'product': m.group(2), 'stepping': m.group(3)}
    return pmap


def load_material_lookup() -> dict:
    """Load shared/material/*.csv → lookup keyed by '{lot7}|{wafer_int}'.

    Mirrors yield-dashboard logic: INTEL_LOT/INTEL_LOT7 truncated to 7 chars,
    WaferID numeric (trailing digits extracted for formatted values like 'Q615S1B-03').
    Supports both BLLC and NVLG material CSV formats.
    """
    import re as _re
    lookup: dict = {}
    if not _SHARED_MATERIAL.exists():
        return lookup
    for fpath in sorted(_SHARED_MATERIAL.glob("*.csv")):
        try:
            dm = pd.read_csv(fpath, dtype=str)
            dm.columns = [c.strip() for c in dm.columns]
            cl = {c.lower(): c for c in dm.columns}

            # Accept INTEL_LOT7 or INTEL_LOT (both truncated to 7 chars)
            intel_lot_col = cl.get('intel_lot7') or cl.get('intel_lot')
            # Accept WaferID or Intel WaferID
            wfr_col = cl.get('waferid') or cl.get('intel waferid')
            mat_col = next((c for k, c in cl.items() if 'material type' in k), None) \
                      or cl.get('material')
            step_col   = cl.get('stepping')
            aio_col    = cl.get('aio/bb') or cl.get('aio_bb')
            lotnum_col = cl.get('lot#') or cl.get('lot_num') or cl.get('lot number')

            if not (intel_lot_col and wfr_col):
                print(f"[pipeline] WARN: material {fpath.name}: missing INTEL_LOT/WaferID columns")
                continue

            for _, row in dm.iterrows():
                lot_id = str(row.get(intel_lot_col, '')).strip()
                if not lot_id or lot_id == 'nan':
                    continue
                lot7 = lot_id[:7]

                wfr_raw = str(row.get(wfr_col, '')).strip()
                if not wfr_raw or wfr_raw == 'nan':
                    continue
                # Extract trailing digits: handles "3", "03", "Q615S1B-03"
                try:
                    wfr_num = int(float(wfr_raw))
                except ValueError:
                    m_wfr = _re.search(r'(\d+)$', wfr_raw)
                    if not m_wfr:
                        continue
                    wfr_num = int(m_wfr.group(1))

                mat_str    = str(row.get(mat_col,    '') if mat_col    else '').strip()
                step_str   = str(row.get(step_col,   '') if step_col   else '').strip()
                aio_str    = str(row.get(aio_col,    '') if aio_col    else '').strip()
                lotnum_str = str(row.get(lotnum_col, '') if lotnum_col else '').strip()
                prog_str = ''
                if mat_str:
                    base = mat_str.split()[0]
                    m = _re.match(r'^(.+)-([A-Z]\d)$', base)
                    prog_str = m.group(1) if m else base
                entry = {
                    'lot_num':  lotnum_str,
                    'program':  prog_str,
                    'material': mat_str,
                    'stepping': step_str,
                    'aio_bb':   aio_str,
                }
                lookup[f"{lot7}|{wfr_num}"] = entry
        except Exception as e:
            print(f"[pipeline] WARN: material {fpath.name}: {e}")
    n_files = len(list(_SHARED_MATERIAL.glob("*.csv")))
    print(f"[pipeline] Material lookup: {len(lookup)} wafer entries from {n_files} file(s)")
    return lookup

# ---------------------------------------------------------------------------
# DEFLATE32 decoder
# ---------------------------------------------------------------------------
_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_CHAR_MAP = {c: i for i, c in enumerate(_CHARS)}

def _deflate32_decode(val: str) -> str:
    """
    Decode a DEFLATE32_<encoded> string to the raw result text.
    If the value is already a plain bitstring (chars: 0/1/8/9), return it as-is.
    Returns '' if val is empty, NaN, or cannot be decoded.
    """
    if not isinstance(val, str):
        return ""
    val = val.strip()
    if not val:
        return ""
    # Already a raw bitstring (e.g. from a pre-decoded file)
    if not val.startswith("DEFLATE32_"):
        return val
    # Strip DEFLATE32_ prefix and decode
    encoded = val[10:].rstrip("=")
    if not encoded:
        return ""
    try:
        bits = "".join(bin(_CHAR_MAP[c])[2:].zfill(5) for c in encoded if c in _CHAR_MAP)
        pad = (8 - len(bits) % 8) % 8
        bits += "0" * pad
        raw = bytes(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))
        return zlib.decompress(raw, -8).decode("utf-8")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
def load_config(cfg_path: str) -> pd.DataFrame:
    df = pd.read_csv(cfg_path)
    df.columns = df.columns.str.strip().str.upper()
    # normalize IP_Ln (underscore) → IP-Ln (hyphen) for consistency
    df.columns = [re.sub(r"^IP_L(\d+)$", r"IP-L\1", c) for c in df.columns]
    df["INDEX"] = df["INDEX"].astype(int)
    if "PARTITION" not in df.columns:
        df["PARTITION"] = ""
    # new-format decoders use IP-L0/L1/L2/… hierarchy; old format uses a flat IP column
    ip_level_cols = sorted([c for c in df.columns if re.match(r"^IP-L\d+$", c)],
                           key=lambda c: int(c[4:]))
    if ip_level_cols:
        for col in ip_level_cols:
            df[col] = df[col].fillna("")
        df["IP"] = df[ip_level_cols].apply(
            lambda r: next((v for v in reversed(r.tolist()) if v), r.iloc[0]), axis=1
        )
    else:
        ip_level_cols = ["IP-L0"]
        df["IP-L0"] = df["IP"]
    fault_cols = [c for c in ("STUCKAT_FAULTS", "ATSPEED_FAULTS") if c in df.columns]
    for fc in fault_cols:
        df[fc] = pd.to_numeric(df[fc], errors="coerce").fillna(0).astype(int)
    return df[["MODULE", "TEST", "IP"] + ip_level_cols + ["REGION", "PARTITION", "INDEX"] + fault_cols]


# ---------------------------------------------------------------------------
# Column name parser
# ---------------------------------------------------------------------------
# old: TESTTYPE_BLOCK_HRY_K_SUBFLOW_DFT_VRAIL_VCORNER_FREQ_STEP_POR_HRY_RAWSTR_ID
_COL_RE_OLD = re.compile(
    r"^(SCN_\w+)::(CHAIN|STUCKAT|ATSPEED|DIAG)_(\w+)_HRY_([KE])_(\w+)_\w+_\w+_(\w+)_(\w+)_\w+_POR_HRY_RAWSTR_(\d+)$",
    re.IGNORECASE,
)
# new: TESTTYPE_ALL_BLOCK_K_SUBFLOW_DFT_VRAIL_VCORNER_FREQ[_STEP]_HRY_RAWSTR_ID
_COL_RE_NEW = re.compile(
    r"^(SCN_\w+)::(CHAIN|STUCKAT|ATSPEED|DIAG)_\w+_(\w+)_([KE])_(\w+)_\w+_\w+_(\w+)_(\w+)(?:_\w+)?_HRY_RAWSTR_(\d+)$",
    re.IGNORECASE,
)

def _parse_col(col: str) -> dict | None:
    m = _COL_RE_OLD.match(col) or _COL_RE_NEW.match(col)
    if not m:
        return None
    return {
        "col":      col,
        "module":   m.group(1).upper(),   # SCN_ATOM
        "testtype": m.group(2).upper(),   # ATSPEED
        "block":    m.group(3).upper(),   # ATOM0
        "kill":     m.group(4).upper(),   # K / E
        "subflow":  m.group(5).upper(),   # PREHVQK / BEGIN
        "vcorner":  m.group(6).upper(),   # NOM
        "freq":     m.group(7).upper(),   # LFM / HFM
        "jobid":    m.group(8),
    }


# ---------------------------------------------------------------------------
# Identity columns
# ---------------------------------------------------------------------------
_ID_COLS_MAP = {
    "VISUAL_ID":  "VISUAL_ID",
    "SORT_LOT":   "LOT",
    "SORT_WAFER": "WAFER",
    "SORT_X":     "X",
    "SORT_Y":     "Y",
}

# ---------------------------------------------------------------------------
# DEFLATE32 / LOGTRACKER helpers  (AP/CR core-failure extraction)
# ---------------------------------------------------------------------------
_D32C = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
_D32M = {c: i for i, c in enumerate(_D32C)}


def _d32r(s: str) -> str:
    """Decode a DEFLATE32_… encoded column value → plain UTF-8 text."""
    if not isinstance(s, str) or not s.startswith('DEFLATE32_'):
        return ''
    try:
        enc  = s[10:].strip('=')
        bits = ''.join(bin(_D32M[c])[2:].zfill(5) for c in enc if c in _D32M)
        bits += '0' * (8 - len(bits) % 8)
        raw  = bytes(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))
        return zlib.decompress(raw, -8).decode('utf-8')
    except Exception:
        return ''


def _apcr_fft(decoded: str) -> str | None:
    """Return first non-TRACKERCLEAR test-instance token from a LOGTRACKER string."""
    for tok in decoded.split('|'):
        tok = tok.strip()
        if tok and '::' in tok and 'TRACKERCLEAR' not in tok:
            return tok
    return None


def _apcr_label(col: str) -> str | None:
    """LOGTRACKER_AP1_119325 → 'AP1',  LOGTRACKER_CR0_119325 → 'CR0'."""
    try:
        part = col.upper().split('LOGTRACKER_')[1].split('_')[0]
        return part if re.match(r'^(AP|CR)\d$', part) else None
    except (IndexError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Status helper
# ---------------------------------------------------------------------------
def _get_status(bitstr: str, idx: int) -> str:
    if not bitstr:
        return "MISSING"
    if bitstr[0] != "1":
        return "RESET_FAIL"
    if idx >= len(bitstr):
        return "UNASSIGNED"
    b = bitstr[idx]
    if b == "0": return "FAIL"
    if b == "1": return "PASS"
    if b == "8": return "UNTESTED"
    return "UNASSIGNED"


# ---------------------------------------------------------------------------
# Reticle layout builder (shared/reticle/*.csv → wmRender-compatible dict)
# ---------------------------------------------------------------------------
def build_reticle_layouts() -> dict:
    """Load all reticle CSVs and return {prefix6: wmRender layout dict}."""
    layouts: dict = {}
    if not _SHARED_RETICLE.exists():
        print(f"[pipeline] WARN: shared reticle dir not found: {_SHARED_RETICLE}")
        return layouts
    for fpath in sorted(_SHARED_RETICLE.glob("*.csv")):
        try:
            rt = pd.read_csv(fpath)
            if "DieX" not in rt.columns or "DieY" not in rt.columns:
                continue
            dx = rt["DieX"].astype(float)
            dy = rt["DieY"].astype(float)
            off_x = round((dx.min() + dx.max()) / 2)
            off_y = round((dy.min() + dy.max()) / 2)
            try:
                prefix = str(rt["Layout"].dropna().iloc[0]).strip()[:6].upper()
            except Exception:
                prefix = fpath.stem[:6].upper()
            sx_col = (dx - off_x).round().astype(int)
            sy_col = (dy - off_y).round().astype(int)
            entry: dict = {"x": sx_col.tolist(), "y": sy_col.tolist()}
            if "Reticle" in rt.columns:
                entry["reticle"] = rt["Reticle"].fillna("").astype(str).tolist()
            if all(c in rt.columns for c in ["LayoutX", "LayoutY", "Reticle"]):
                try:
                    lx_col = rt["LayoutX"].astype(int)
                    ly_col = rt["LayoutY"].astype(int)
                    if "ReticleDieX" in rt.columns and "ReticleDieY" in rt.columns:
                        rdx_col = rt["ReticleDieX"].astype(int)
                        rdy_col = rt["ReticleDieY"].astype(int)
                    else:
                        smx = rt.groupby(["LayoutX","LayoutY"])["DieX"].transform("min").round().astype(int)
                        smy = rt.groupby(["LayoutX","LayoutY"])["DieY"].transform("min").round().astype(int)
                        rdx_col = (dx.round().astype(int) - smx).astype(int)
                        rdy_col = (dy.round().astype(int) - smy).astype(int)
                    shot_order = sorted({(int(lx), int(ly)) for lx, ly in zip(lx_col, ly_col)})
                    shot_idx_m = {k: i for i, k in enumerate(shot_order)}
                    si_col = [shot_idx_m[(int(lx), int(ly))] for lx, ly in zip(lx_col, ly_col)]
                    ret_col = rt["Reticle"].astype(int)
                    entry["ret_map"] = {
                        f"{int(sx)},{int(sy)}": [int(rdx), int(rdy), int(si)]
                        for sx, sy, rdx, rdy, si in zip(sx_col, sy_col, rdx_col, rdy_col, si_col)
                    }
                    rsn: dict = {}
                    for rdx, rdy, rv in zip(rdx_col, rdy_col, ret_col):
                        k = f"{int(rdx)},{int(rdy)}"
                        if k not in rsn:
                            rsn[k] = int(rv)
                    entry["ret_site_num"] = rsn
                    shot_bounds: dict = {}
                    for sx, sy, lx, ly in zip(sx_col, sy_col, lx_col, ly_col):
                        k = (int(lx), int(ly))
                        si, sy_i = int(sx), int(sy)
                        if k not in shot_bounds:
                            shot_bounds[k] = [si, sy_i, si, sy_i]
                        else:
                            b = shot_bounds[k]
                            if si   < b[0]: b[0] = si
                            if sy_i < b[1]: b[1] = sy_i
                            if si   > b[2]: b[2] = si
                            if sy_i > b[3]: b[3] = sy_i
                    entry["ret_shots"] = [shot_bounds[k] for k in shot_order]
                except Exception as _e:
                    print(f"[pipeline]   WARN: shot data for {fpath.name}: {_e}")
            layouts[prefix] = entry
            print(f"[pipeline] Reticle: {prefix} = {len(entry['x']):,} dies ({fpath.name})")
        except Exception as e:
            print(f"[pipeline] WARN: reticle {fpath.name}: {e}")
    return layouts


# ---------------------------------------------------------------------------
# Die-map builder (aggregate per_ip records into per-die summary)
# ---------------------------------------------------------------------------
_TT_KEYS = ("CHAIN", "STUCKAT", "ATSPEED", "DIAG")

def build_die_map(records: list) -> list:
    """Aggregate per_ip failure records into per-(lot,wafer,die) objects for wafer map."""
    dm: dict = {}
    for r in records:
        lot = r.get("LOT", "")
        wfr = r.get("WAFER")
        vid = r.get("VISUAL_ID", "")
        key = f"{lot}|{wfr}|{r.get('X','')}_{r.get('Y','')}"
        if key not in dm:
            dm[key] = {
                "LOT": lot,
                "WAFER": int(wfr) if wfr is not None else None,
                "X": r.get("X"),
                "Y": r.get("Y"),
                "VISUAL_ID": vid,
                "Layout": r.get("LAYOUT") or str(lot)[:6].upper(),
                "IB": None,
                "FB": None,
                "CHAIN": 0, "STUCKAT": 0, "ATSPEED": 0, "DIAG": 0,
                "_fails": {tt: set() for tt in _TT_KEYS},
            }
        d = dm[key]
        ib = r.get("IB")
        fb = r.get("FB")
        if ib is not None and str(ib).strip() not in ('', 'nan', 'None'):
            try:
                d["IB"] = str(int(float(ib)))
            except Exception:
                d["IB"] = str(ib)
        if fb is not None and str(fb).strip() not in ('', 'nan', 'None'):
            try:
                d["FB"] = str(int(float(fb)))
            except Exception:
                d["FB"] = str(fb)
        tt   = r.get("TESTTYPE", "").upper()
        pair = f"{r.get('BLOCK','')}:{r.get('REGION','')}:{r.get('IP','')}"
        if tt in _TT_KEYS:
            d["_fails"][tt].add(pair)
            d[tt] = len(d["_fails"][tt])
    result = []
    for d in dm.values():
        entry = {k: v for k, v in d.items() if not k.startswith("_")}
        for tt in _TT_KEYS:
            entry[f"fails_{tt.lower()}"] = ",".join(sorted(d["_fails"][tt]))
        all_fails: set = set()
        for tt in _TT_KEYS:
            all_fails.update(d["_fails"][tt])
        entry["fails"] = ",".join(sorted(all_fails))
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Yield target loader
# ---------------------------------------------------------------------------
_YIELD_TARGET_CSV = _SHARED_CFG / "yield-estimate-per-fault-count.csv"

def _load_yield_target() -> list:
    """Read yield-estimate-per-fault-count.csv → [{fc, pct}, ...].
    Returns [] if the file is missing or unreadable."""
    path = _YIELD_TARGET_CSV
    if not path.exists():
        print(f"[pipeline] WARN: yield target CSV not found: {path}")
        return []
    try:
        df = pd.read_csv(path)
        # Accept flexible column names: first col = fault count, second = target %
        cols = df.columns.tolist()
        records = [
            {"fc": int(row[cols[0]]), "pct": float(row[cols[1]])}
            for _, row in df.iterrows()
            if pd.notna(row[cols[0]]) and pd.notna(row[cols[1]])
        ]
        print(f"[pipeline] yield_target: {len(records)} points from {path.name}")
        return records
    except Exception as e:
        print(f"[pipeline] WARN: could not load yield target CSV: {e}")
        return []


# ---------------------------------------------------------------------------
# Main process
# ---------------------------------------------------------------------------
def process(csv_path: str, cfg_path: str, keep_tests=None) -> dict:
    """
    Parse RAWSTR CSV and return:
      {
        "meta":   { lots, wafers, modules, testtypes, blocks, subflows, vcorners, freqs,
                    total_dies_per_wafer: {"LOT|WAFER": N} },
        "per_ip": [ {LOT, WAFER, X, Y, VISUAL_ID, MODULE, TESTTYPE, BLOCK, SUBFLOW,
                     PARTITION, IP, REGION, STATUS}, ... ]
      }
    Only FAIL and RESET_FAIL rows are included to keep the output compact.
    """
    print(f"[pipeline] Loading CSV: {csv_path}")
    _tmp_dir = None
    csv_path = str(csv_path)
    _ext = Path(csv_path).suffix.lower()
    if _ext == ".gz":
        import gzip, io
        print(f"[pipeline] Decompressing .gz …")
        with gzip.open(csv_path, "rb") as _gf:
            df = pd.read_csv(io.BytesIO(_gf.read()), low_memory=False, dtype=str)
    elif _ext == ".zip":
        import zipfile
        print(f"[pipeline] Extracting .zip …")
        with zipfile.ZipFile(csv_path) as _zf:
            _names = [n for n in _zf.namelist() if n.lower().endswith(".csv")]
            if not _names:
                raise FileNotFoundError(f"No CSV found inside archive: {csv_path}")
            print(f"[pipeline] Using: {_names[0]}")
            with _zf.open(_names[0]) as _zcsv:
                df = pd.read_csv(_zcsv, low_memory=False, dtype=str)
    elif _ext == ".7z":
        import tempfile, subprocess
        _tmp_dir = tempfile.mkdtemp(prefix="scan_pipeline_")
        print(f"[pipeline] Extracting .7z → {_tmp_dir}")
        _7z_exe = shutil.which("7z") or shutil.which("7za") or r"C:\Program Files\7-Zip\7z.exe"
        if not _7z_exe or not os.path.exists(_7z_exe):
            raise FileNotFoundError("7-Zip not found. Install 7-Zip or add it to PATH.")
        subprocess.run([_7z_exe, "e", csv_path, f"-o{_tmp_dir}", "-y"],
                       check=True, capture_output=True)
        _csvs = sorted(Path(_tmp_dir).glob("*.[cC][sS][vV]"))
        if not _csvs:
            raise FileNotFoundError(f"No CSV found inside archive: {csv_path}")
        csv_path = str(_csvs[0])
        print(f"[pipeline] Using: {Path(csv_path).name}")
        df = pd.read_csv(csv_path, low_memory=False, dtype=str)
    else:
        df = pd.read_csv(csv_path, low_memory=False, dtype=str)
    for _nc in ["SORT_WAFER", "SORT_X", "SORT_Y"]:
        if _nc in df.columns:
            df[_nc] = pd.to_numeric(df[_nc], errors="coerce")
    print(f"[pipeline] {len(df)} rows, {len(df.columns)} columns")

    cfg = load_config(cfg_path)
    print(f"[pipeline] Config: {len(cfg)} IP entries across {cfg['MODULE'].nunique()} modules")


    # identity columns
    available_id = {k: v for k, v in _ID_COLS_MAP.items() if k in df.columns}
    id_df = df[list(available_id.keys())].rename(columns=available_id).reset_index(drop=True)

    # Normalize lot IDs: strip trailing session-sequence digit from 9-char lots
    # e.g. 'Q552S9PB1' (9 chars, session=1 appended) → 'Q552S9PB' (8 chars canonical)
    if "LOT" in id_df.columns:
        _lot_mask = id_df["LOT"].astype(str).str.len() == 9
        if _lot_mask.any():
            id_df.loc[_lot_mask, "LOT"] = id_df.loc[_lot_mask, "LOT"].astype(str).str[:8]
            print(f"[pipeline] Lot normalization: stripped session suffix from {_lot_mask.sum()} rows")

    # Detect Interface Bin (IB) and Functional Bin (FB) columns (various TRACE naming conventions)
    _ib_src = next((c for c in df.columns
                    if c.upper().startswith("INTERFACE_BIN")
                    or c.upper().startswith("SORT_INTERFACE_BIN")
                    or c.upper() in ("SORT_IBIN", "IBIN", "IB")), None)
    _fb_src = next((c for c in df.columns
                    if c.upper().startswith("FUNCTIONAL_BIN")
                    or c.upper().startswith("SORT_FUNCTIONAL_BIN")
                    or c.upper() in ("SORT_FBIN", "FBIN", "FB")), None)
    if _ib_src:
        id_df['IB'] = df[_ib_src].values
        print(f"[pipeline] IB column: {_ib_src}")
    if _fb_src:
        id_df['FB'] = df[_fb_src].values
        print(f"[pipeline] FB column: {_fb_src}")

    # Pass fault-count columns through so the dashboard can aggregate by testtype
    for _fc_col in ('STUCKAT_FAULTS', 'ATSPEED_FAULTS'):
        if _fc_col in df.columns:
            id_df[_fc_col] = pd.to_numeric(df[_fc_col], errors='coerce')
            print(f"[pipeline] Fault column: {_fc_col}")

    # Detect material column directly in the scan CSV (TRACE Sort exports often include it).
    import re as _re2
    _devrev_col = next((c for c in df.columns if c.upper().startswith('DEVREVSTEP')), None)

    # Derive Layout key from DevRevStep (first 6 chars) so reticle lookup uses process code not lot
    if _devrev_col:
        id_df['LAYOUT'] = df[_devrev_col].astype(str).str[:6].str.upper()

    # Build reticle layouts early — needed for the correct total-die denominator
    reticle_layout = build_reticle_layouts()

    # Re-align reticle ret_map keys to the actual scan die coordinate range.
    # The midpoint centering in build_reticle_layouts() may be off by ±1 per axis
    # when the tester uses an asymmetric zero convention (e.g. X: -10..+11, Y: -11..+10
    # for a 22-column grid).  We correct per-axis by shifting to match scan_min.
    if "X" in id_df.columns and "Y" in id_df.columns:
        try:
            _sx_min = int(id_df["X"].dropna().astype(float).min())
            _sy_min = int(id_df["Y"].dropna().astype(float).min())
            for _pfx_r, _lay in reticle_layout.items():
                if not _lay.get("x"):
                    continue
                _dx = _sx_min - min(_lay["x"])
                _dy = _sy_min - min(_lay["y"])
                if _dx == 0 and _dy == 0:
                    continue
                _lay["x"] = [v + _dx for v in _lay["x"]]
                _lay["y"] = [v + _dy for v in _lay["y"]]
                if "ret_map" in _lay:
                    _lay["ret_map"] = {
                        f"{int(k.split(',')[0])+_dx},{int(k.split(',')[1])+_dy}": v
                        for k, v in _lay["ret_map"].items()
                    }
                print(f"[pipeline] Reticle {_pfx_r}: re-aligned by dx={_dx:+d}, dy={_dy:+d} "
                      f"to match scan range")
        except Exception as _re:
            print(f"[pipeline] WARN: reticle re-alignment failed: {_re}")

    # total dies per wafer: use reticle row count (e.g. 393 for 8PF5CV) as denominator;
    # fall back to CSV row count only if no matching reticle found.
    total_dies_per_wafer = {}
    if "LOT" in id_df.columns and "WAFER" in id_df.columns:
        for (lot, wfr), grp in id_df.groupby(["LOT", "WAFER"]):
            prefix = str(lot)[:6].upper()
            rt_total = len(reticle_layout.get(prefix, {}).get("x", []))
            total_dies_per_wafer[f"{lot}|{int(wfr)}"] = rt_total if rt_total else len(grp)

    # find SCN RAWSTR columns
    scn_cols = [_parse_col(c) for c in df.columns]
    scn_cols = [c for c in scn_cols if c is not None]
    print(f"[pipeline] Found {len(scn_cols)} SCN RAWSTR columns")
    if not scn_cols:
        prefixes = sorted({c.split("::")[0] for c in df.columns if "::" in c})
        hint = (
            f"\n  This CSV has {len(df.columns)} columns with {len(prefixes)} '::' prefixes: "
            + ", ".join(prefixes[:8]) + ("..." if len(prefixes) > 8 else "")
            + "\n  The scan dashboard requires columns matching:"
            + "\n    SCN_<MODULE>::<CHAIN|STUCKAT|ATSPEED|DIAG>_<BLOCK>_HRY_<K|E>_..._POR_HRY_RAWSTR_<ID>"
            + "\n  This looks like a yield/sort CSV. You need a SCAN HRY RAWSTR export from TRACE."
        )
        raise ValueError("No SCN RAWSTR columns found in the CSV." + hint)
    if keep_tests:
        _before = len(scn_cols)
        scn_cols = [c for c in scn_cols
                    if f"{c['testtype']}:{c['module']}:{c['block']}" in keep_tests]
        print(f"[pipeline] Test filter: kept {len(scn_cols)}/{_before} columns")
        if not scn_cols:
            raise ValueError("No SCN columns remain after applying test filter.")

    # metadata
    lots   = sorted(id_df["LOT"].dropna().unique().tolist())   if "LOT"   in id_df else []
    wafers = sorted(id_df["WAFER"].dropna().unique().tolist()) if "WAFER" in id_df else []
    # col_names: map from "MODULE|TESTTYPE|BLOCK|SUBFLOW" → sorted list of original CSV column names
    # Stored once in meta (not per record) so the JSON stays compact.
    _cn: dict = {}
    for c in scn_cols:
        tk = f"{c['module'].replace('SCN_','',1)}|{c['testtype']}|{c['block']}|{c['subflow']}"
        _cn.setdefault(tk, set()).add(c["col"])
    col_names = {k: sorted(v) for k, v in _cn.items()}

    # Detect test-program column in scan CSV (TRACE exports vary)
    _prog_col = next((c for c in df.columns
                      if c.upper() in ("TEST_PROGRAM", "SORT_PROGRAM",
                                       "PROGRAM", "TP_NAME", "TPNAME")
                      or 'program name' in c.lower()
                      or 'program_name' in c.lower()), None)
    _prog_by_lot: dict = {}
    if _prog_col and "LOT" in id_df.columns:
        for lot, grp in id_df.groupby("LOT"):
            vals = df.loc[grp.index, _prog_col].dropna().unique()
            if len(vals):
                _prog_by_lot[str(lot)] = str(vals[0]).strip()
        print(f"[pipeline] Program column '{_prog_col}': {len(_prog_by_lot)} lots")

    # Material enrichment — shared/material/ CSVs only, no fallback
    _mat = load_material_lookup()

    lot_material: dict = {}
    for wk in total_dies_per_wafer:
        lot_part, wafer_part = wk.split('|', 1)
        # Key: lot7|wafer_int  (% 100 handles slot-encoded wafers like 703 → 3)
        _lot7 = lot_part[:7]
        try:
            _wafer_int = int(float(wafer_part)) % 100
        except (ValueError, TypeError):
            _wafer_int = None
        info = _mat.get(f"{_lot7}|{_wafer_int}") if _wafer_int is not None else None
        if info:
            entry = dict(info)
            if _prog_by_lot.get(lot_part):
                entry['program'] = _prog_by_lot[lot_part]
            lot_material[wk] = entry
    if lot_material:
        print(f"[pipeline] Material match: {len(lot_material)}/{len(total_dies_per_wafer)} wafer(s) enriched")
    else:
        print("[pipeline] Material lookup: no matches (shared/material/ may be empty or lot IDs differ)")

    n_mat_files = len(list(_SHARED_MATERIAL.glob("*.csv"))) if _SHARED_MATERIAL.exists() else 0

    meta = {
        "lots":      lots,
        "wafers":    [int(w) for w in wafers],
        "modules":   sorted({c["module"].replace("SCN_", "", 1) for c in scn_cols}),
        "testtypes": sorted({c["testtype"] for c in scn_cols}),
        "blocks":    sorted({c["block"]    for c in scn_cols}),
        "subflows":  sorted({c["subflow"]  for c in scn_cols}),
        "vcorners":  sorted({c["vcorner"]  for c in scn_cols}),
        "freqs":     sorted({c["freq"]     for c in scn_cols}),
        "cfg_partitions": sorted({p for p in cfg["PARTITION"].unique() if p}),
        "total_dies_per_wafer": total_dies_per_wafer,
        "col_names":    col_names,
        "lot_material":  lot_material,
        "has_material_files": n_mat_files > 0,
        # per-IP fault counts from config: {block|region|ip: N}
        "cfg_fault_counts": {
            "stuckat": {
                f"{row['REGION']}|{row['IP']}": int(row["STUCKAT_FAULTS"])
                for _, row in cfg.iterrows() if "STUCKAT_FAULTS" in cfg.columns and pd.notna(row["STUCKAT_FAULTS"])
            },
            "atspeed": {
                f"{row['REGION']}|{row['IP']}": int(row["ATSPEED_FAULTS"])
                for _, row in cfg.iterrows() if "ATSPEED_FAULTS" in cfg.columns and pd.notna(row["ATSPEED_FAULTS"])
            },
        },
        # all decoder entries so the UI can show regions even with zero failures
        "cfg_entries": [
            {
                "MODULE":    row["MODULE"].replace("SCN_", "", 1),
                "BLOCK":     next((c["block"] for c in scn_cols
                                   if c["module"].replace("SCN_","",1) == row["MODULE"]
                                   or c["module"] == row["MODULE"]), ""),
                "PARTITION": row.get("PARTITION", ""),
                "REGION":    row["REGION"],
                "IP":        row["IP"],
                **{c: row[c] for c in cfg.columns if re.match(r"^IP-L\d+$", c)},
            }
            for _, row in cfg.iterrows()
        ],
    }

    # decode + aggregate
    records = []
    for col_info in scn_cols:
        module   = col_info["module"]
        block    = col_info["block"]
        col_name = col_info["col"]

        module_key = module.replace("SCN_", "", 1)
        subflow    = col_info["subflow"]
        cfg_sub = cfg[(cfg["MODULE"] == module_key) & (cfg["TEST"] == block)]
        if cfg_sub.empty:
            cfg_sub = cfg[(cfg["MODULE"] == module) & (cfg["TEST"] == block)]
        if cfg_sub.empty:  # new-format: subflow as TEST key
            cfg_sub = cfg[(cfg["MODULE"] == module_key) & (cfg["TEST"] == subflow)]
        if cfg_sub.empty:
            cfg_sub = cfg[(cfg["MODULE"] == module) & (cfg["TEST"] == subflow)]
        if cfg_sub.empty:  # single-IP products: TEST is IP name, not in column — match module only
            cfg_sub = cfg[cfg["MODULE"].isin([module_key, module])]
        if cfg_sub.empty:
            print(f"[pipeline]   WARN: no config for ({module_key}, block={block}, subflow={subflow}) — skipping")
            continue

        decoded = df[col_name].apply(_deflate32_decode)

        for _, cfg_row in cfg_sub.iterrows():
            idx       = int(cfg_row["INDEX"])
            partition = cfg_row["PARTITION"]
            ip        = cfg_row["IP"]
            ip_levels = {c: cfg_row[c] for c in cfg_sub.columns if re.match(r"^IP-L\d+$", c)}
            region    = cfg_row["REGION"]

            statuses = decoded.apply(lambda s, i=idx: _get_status(s, i))
            fail_mask = statuses.isin(["FAIL"])  # only reset=1 AND bit=0; RESET_FAIL excluded
            if not fail_mask.any():
                continue

            sub_id = id_df[fail_mask].copy()
            sub_st = statuses[fail_mask].values

            for j, (_, id_row) in enumerate(sub_id.iterrows()):
                rec = id_row.to_dict()
                rec.update({
                    "MODULE":    module.replace("SCN_", "", 1),
                    "TESTTYPE":  col_info["testtype"],
                    "BLOCK":     block,
                    "SUBFLOW":   col_info["subflow"],
                    "VCORNER":   col_info["vcorner"],
                    "FREQ":      col_info["freq"],
                    "PARTITION": partition,
                    "IP":        ip,
                    **ip_levels,
                    "REGION":    region,
                    "STATUS":    sub_st[j],
                })
                records.append(rec)

    print(f"[pipeline] {len(records)} failure records")

    # Build die_map (reticle_layout already built above)
    die_map = build_die_map(records)
    print(f"[pipeline] die_map: {len(die_map)} unique failing dies")

    # die_bins: per-wafer IB/FB for ALL dies (needed for wafer-map IB/FB overlay).
    # Stored as {LOT|WAFER: {x,y: {ib?, fb?}}} — only positions with bin data.
    die_bins: dict = {}
    _has_ib = "IB" in id_df.columns
    _has_fb = "FB" in id_df.columns
    if (_has_ib or _has_fb) and all(c in id_df.columns for c in ["LOT", "WAFER", "X", "Y"]):
        _b = id_df[["LOT", "WAFER", "X", "Y"]
                   + (["IB"] if _has_ib else [])
                   + (["FB"] if _has_fb else [])].copy()
        _b["_xy"] = (_b["X"].astype(float).round().astype(int).astype(str) + "," +
                     _b["Y"].astype(float).round().astype(int).astype(str))
        _b["_wk"] = _b["LOT"].astype(str) + "/W" + _b["WAFER"].apply(
            lambda w: str(int(float(w))))
        if _has_ib:
            _b["IB"] = _b["IB"].fillna("").astype(str).str.strip().replace(
                {"nan": "", "null": ""})
        if _has_fb:
            _b["FB"] = _b["FB"].fillna("").astype(str).str.strip().replace(
                {"nan": "", "null": ""})
        for wk, grp in _b.groupby("_wk"):
            wdict: dict = {}
            for xy, ib, fb in zip(
                grp["_xy"],
                grp["IB"] if _has_ib else [""] * len(grp),
                grp["FB"] if _has_fb else [""] * len(grp),
            ):
                ent: dict = {}
                if ib:
                    ent["ib"] = ib
                if fb:
                    ent["fb"] = fb
                if ent:
                    wdict[xy] = ent
            if wdict:
                die_bins[wk] = wdict
        print(f"[pipeline] die_bins: {sum(len(v) for v in die_bins.values()):,} positions"
              f" across {len(die_bins)} wafers")

    # Load yield target reference from shared setup CSV
    yield_target = _load_yield_target()

    # -------------------------------------------------------------------------
    # AP/CR LOGTRACKER extraction — decode which Core (CR) / Atom-Partition (AP)
    # caused each die to fail and store the first-failing-test name per group.
    # Result is merged directly onto each die_map entry as  die["ap_cr"] = {grp: fft}.
    # -------------------------------------------------------------------------
    _ap_cols = sorted([c for c in df.columns if re.search(r'LOGTRACKER_AP\d', c, re.I)
                       and 'TRACKER_ATOM' not in c.upper()])
    _cr_cols = sorted([c for c in df.columns if re.search(r'LOGTRACKER_CR\d', c, re.I)
                       and 'TRACKER_CORE' not in c.upper()])
    _trk_cols = _ap_cols + _cr_cols
    _apcr_groups: list[tuple[str, str]] = [
        (_apcr_label(c), c) for c in _trk_cols if _apcr_label(c)
    ]

    if _apcr_groups and all(c in id_df.columns for c in ["LOT", "WAFER", "X", "Y"]):
        # Copy tracker columns into id_df (same row order — positionally aligned)
        _grp_fft_cols: dict[str, str] = {}
        for grp, orig_col in _apcr_groups:
            fft_col = f'__fft_{grp}'
            id_df[fft_col] = df[orig_col].apply(
                lambda v: _apcr_fft(_d32r(v))
                if isinstance(v, str) and v.startswith('DEFLATE32_') else None
            )
            _grp_fft_cols[grp] = fft_col

        # Filter to rows that have at least one decoded FFT value
        _any_fft = pd.Series(False, index=id_df.index)
        for fc in _grp_fft_cols.values():
            _any_fft |= id_df[fc].notna()
        _trk_df = id_df[_any_fft].copy()

        # Build lookup  "LOT/WWAFER/x,y" → {grp: fft_str}
        _apcr_lookup: dict[str, dict] = {}
        if not _trk_df.empty:
            _trk_df['__lot'] = _trk_df['LOT'].astype(str)
            _trk_df['__wfr'] = _trk_df['WAFER'].apply(lambda w: str(int(float(w))))
            _trk_df['__x']   = _trk_df['X'].apply(lambda v: str(int(float(v))))
            _trk_df['__y']   = _trk_df['Y'].apply(lambda v: str(int(float(v))))
            for _, row in _trk_df.iterrows():
                key = f"{row['__lot']}/W{row['__wfr']}/{row['__x']},{row['__y']}"
                if key not in _apcr_lookup:
                    _apcr_lookup[key] = {}
                for grp, fc in _grp_fft_cols.items():
                    fft = row.get(fc)
                    if fft and isinstance(fft, str):
                        _apcr_lookup[key][grp] = fft

        # Merge into die_map entries
        for entry in die_map:
            try:
                lot = str(entry['LOT'])
                wfr = str(int(float(str(entry['WAFER']))))
                x   = str(int(float(str(entry['X']))))
                y   = str(int(float(str(entry['Y']))))
            except (ValueError, TypeError):
                entry['ap_cr'] = {}
                continue
            entry['ap_cr'] = _apcr_lookup.get(f'{lot}/W{wfr}/{x},{y}', {})

        n_apcr = sum(1 for e in die_map if e.get('ap_cr'))
        print(f"[pipeline] ap_cr: {n_apcr}/{len(die_map)} failing dies have LOGTRACKER data "
              f"({len(_apcr_groups)} groups: {[g for g, _ in _apcr_groups]})")

        # Also inject ap_cr into die_bins so non-scan-failing dies still show AP/CR in tooltip
        n_bins_apcr = 0
        for wk, wdict in die_bins.items():
            for xy, ent in wdict.items():
                apcr = _apcr_lookup.get(f'{wk}/{xy}', {})
                if apcr:
                    ent['ap_cr'] = apcr
                    n_bins_apcr += 1
        if n_bins_apcr:
            print(f"[pipeline] ap_cr: {n_bins_apcr} die_bins positions also have LOGTRACKER data")

        id_df.drop(columns=list(_grp_fft_cols.values()), errors='ignore', inplace=True)
    else:
        for entry in die_map:
            entry.setdefault('ap_cr', {})
        if not _apcr_groups:
            print("[pipeline] ap_cr: no LOGTRACKER_AP/CR columns found in CSV")

    if _tmp_dir:
        shutil.rmtree(_tmp_dir, ignore_errors=True)
    return {"meta": meta, "per_ip": records,
            "die_map": die_map, "reticle_layout": reticle_layout,
            "die_bins": die_bins, "yield_target": yield_target}


# ---------------------------------------------------------------------------
# Output: write dashboard/index.html + dashboard/data.js
# ---------------------------------------------------------------------------
def write_dashboard(result: dict, output_dir: Path, standalone: bool = False):
    """Write data.js and copy index.html template to output_dir/dashboard/.

    If standalone=True, the data.js content and Plotly are embedded directly
    into index.html so it can be opened without a local server.
    """
    dash_dir = output_dir / "dashboard"
    dash_dir.mkdir(parents=True, exist_ok=True)

    # Copy template HTML
    src_html = _TEMPLATE_DIR / "index.html"
    dst_html = dash_dir / "index.html"
    if src_html.exists():
        shutil.copy2(src_html, dst_html)
    else:
        raise FileNotFoundError(f"Template not found: {src_html}")

    # Inject WAFERMAP_JS (SVG renderer) into the HTML
    try:
        import sys as _sys
        _wt = str(_WAFER_TOOLS)
        if _wt not in _sys.path:
            _sys.path.insert(0, _wt)
        from wafer_map import WAFERMAP_JS
        _html = dst_html.read_text(encoding="utf-8")
        # Inject BEFORE the main <script> block so wmRender is defined before
        # any dashboard code runs (avoids cross-script-block timing issues).
        _marker = '\n<script>\n"use strict";'
        if _marker in _html:
            _html = _html.replace(_marker, '\n' + WAFERMAP_JS + _marker, 1)
        else:
            _html = _html.replace("</body>", WAFERMAP_JS + "\n</body>", 1)
        dst_html.write_text(_html, encoding="utf-8")
        print("[pipeline] WAFERMAP_JS injected")
    except Exception as _wme:
        print(f"[pipeline] WARN: WAFERMAP_JS not injected: {_wme}")

    # Copy Plotly library
    if _PLOTLY_JS.exists():
        shutil.copy2(_PLOTLY_JS, dash_dir / _PLOTLY_JS.name)
    else:
        print(f"[pipeline] WARNING: Plotly not found at {_PLOTLY_JS}")

    # Write data.js
    data_js = dash_dir / "data.js"
    js_content = "// Auto-generated by scan-dashboard.py -- do not edit manually\n"
    js_content += f"const SCAN_DATA = {json.dumps(result, separators=(',', ':'))};\n"
    data_js.write_text(js_content, encoding="utf-8")

    # ------------------------------------------------------------------
    # Standalone mode: embed data.js (and Plotly) inline so the HTML
    # can be opened directly without a web server or sibling files.
    # ------------------------------------------------------------------
    if standalone:
        _html = dst_html.read_text(encoding="utf-8")
        # Replace <script src="data.js"></script> with inline data
        # Escape </script> inside JS content so it doesn't prematurely close the tag
        _safe_js = js_content.replace('</script>', r'<\/script>')
        _html = _html.replace(
            '<script src="data.js"></script>',
            f'<script>\n{_safe_js}</script>',
        )
        # Replace <script src="plotly-*.min.js"> with inline Plotly if available
        import re as _re
        def _inline_plotly(m):
            src = m.group(1)
            pjs = dash_dir / src
            if pjs.exists():
                print(f"[pipeline] Inlining Plotly ({pjs.stat().st_size:,} bytes)")
                _pjs = pjs.read_text(encoding="utf-8").replace('</script>', r'<\/script>')
                return f'<script>\n{_pjs}\n</script>'
            return m.group(0)
        _html = _re.sub(r'<script src="(plotly[^"]+)"></script>', _inline_plotly, _html)
        dst_html.write_text(_html, encoding="utf-8")
        print(f"[pipeline] Standalone HTML: {dst_html.stat().st_size:,} bytes")

    print(f"[pipeline] Dashboard written: {dst_html}")
    print(f"[pipeline]   data.js: {data_js.stat().st_size:,} bytes")
    print(f"HRY_DASHBOARD:{dst_html}")
    return str(dst_html)


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------
def _merge_results(results: list) -> dict:
    """Merge process() results from multiple CSV inputs into one."""
    if len(results) == 1:
        return results[0]
    import copy
    merged = copy.deepcopy(results[0])
    for r in results[1:]:
        m  = r.get("meta", {})
        mm = merged.get("meta", {})
        for key in ("lots", "wafers", "modules", "testtypes", "blocks",
                    "subflows", "vcorners", "freqs"):
            if key in m and key in mm:
                mm[key] = sorted(set(mm[key]) | set(m[key]),
                                 key=lambda x: (type(x).__name__, x))
        tdw = m.get("total_dies_per_wafer")
        if tdw:
            mm.setdefault("total_dies_per_wafer", {}).update(tdw)
        for lst_key in ("per_ip", "die_map"):
            if lst_key in r:
                merged.setdefault(lst_key, []).extend(r[lst_key])
        if "reticle_layout" in r:
            merged.setdefault("reticle_layout", {}).update(r["reticle_layout"])
        if "die_bins" in r:
            db = merged.setdefault("die_bins", {})
            for wk, wdict in r["die_bins"].items():
                db.setdefault(wk, {}).update(wdict)
    return merged


# ===========================================================================
# GUI: HRYFrame  (Tkinter tab embedded in HRYApp below)
# ===========================================================================

# -- Palette (matches vmin / yield dashboards) --------------------------------
BG   = '#1a252f'
BG2  = '#2c3e50'
FG   = '#ecf0f1'
FG2  = '#95a5a6'
ABLU = '#3498db'
GRN  = '#27ae60'


class HRYFrame(tk.Frame):
    """HRY Scan Analysis tab."""

    def __init__(self, parent=None, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._proc = None
        self._dashboard_path = ''
        self._build_ui()

    def _build_ui(self):
        def _btn(parent, text, cmd, color=ABLU, acolor='#5dade2'):
            return tk.Button(parent, text=text, command=cmd,
                             bg=color, fg='white', activebackground=acolor,
                             relief='flat', cursor='hand2',
                             font=('Arial', 9), padx=8, pady=3)

        def _lf(text, color=FG2):
            return tk.LabelFrame(self, text=text, bg=BG, fg=color,
                                 font=('Arial', 8, 'bold'), padx=6, pady=4,
                                 relief='groove', bd=1)

        def _field(parent, label, var, browse_cmd):
            row = tk.Frame(parent, bg=BG)
            row.pack(fill='x', pady=2)
            tk.Label(row, text=label, width=16, anchor='w',
                     bg=BG, fg=FG, font=('Arial', 9)
                     ).pack(side='left')
            tk.Entry(row, textvariable=var, width=52,
                     bg=BG2, fg=FG, insertbackground=FG,
                     relief='flat', font=('Consolas', 9)
                     ).pack(side='left', expand=True, fill='x', padx=(4, 4))
            _btn(row, '...', browse_cmd, color='#1f618d').pack(side='left')

        tk.Label(self, text='HRY Scan Analysis',
                 bg=BG, fg=ABLU, font=('Arial', 13, 'bold')
                 ).pack(fill='x', padx=10, pady=(10, 4))

        # -- Settings row: Load / Save settings -------------------------------
        stt_row = tk.Frame(self, bg=BG)
        stt_row.pack(fill='x', padx=10, pady=(0, 6))
        _btn(stt_row, '  Load Settings  ', self._load_settings,
             color='#1f618d', acolor=ABLU).pack(side='left', padx=(0, 4))
        _btn(stt_row, '  Save Settings  ', self._save_settings,
             color='#1f618d', acolor=ABLU).pack(side='left', padx=(0, 4))

        # -- Input CSV files (multi-file) -------------------------------------
        frm_input = _lf(
            'Input CSV files  (one or more — each processed, then combined)',
            ABLU)
        frm_input.pack(fill='x', padx=10, pady=(0, 4))

        _lb_outer = tk.Frame(frm_input, bg=BG)
        _lb_outer.pack(fill='x', pady=(2, 0))
        _lb_scroll_y = tk.Scrollbar(_lb_outer, orient='vertical')
        _lb_scroll_x = tk.Scrollbar(_lb_outer, orient='horizontal')
        self._input_listbox = tk.Listbox(
            _lb_outer, height=4, selectmode='extended',
            bg=BG2, fg=FG, selectbackground='#1f618d', selectforeground='white',
            activestyle='none', font=('Consolas', 9), relief='flat',
            yscrollcommand=_lb_scroll_y.set,
            xscrollcommand=_lb_scroll_x.set)
        _lb_scroll_y.config(command=self._input_listbox.yview)
        _lb_scroll_x.config(command=self._input_listbox.xview)
        _lb_scroll_y.pack(side='right', fill='y')
        _lb_scroll_x.pack(side='bottom', fill='x')
        self._input_listbox.pack(side='left', fill='both', expand=True)

        _lb_btn_row = tk.Frame(frm_input, bg=BG)
        _lb_btn_row.pack(fill='x', pady=(4, 0))
        _btn(_lb_btn_row, '  Add CSV / GZ / ZIP / 7Z File(s)  ', self._add_input_files,
             color='#1f618d').pack(side='left', padx=(0, 4))
        _btn(_lb_btn_row, '  Remove Selected  ', self._remove_selected_files,
             color='#7b241c', acolor='#a93226').pack(side='left')
        tk.Label(_lb_btn_row,
                 text='Tip: select multiple files for a combined run.',
                 bg=BG, fg=FG2, font=('Arial', 8)).pack(side='left', padx=(8, 0))

        # -- Output folder ----------------------------------------------------
        frm_out = _lf('Output folder', ABLU)
        frm_out.pack(fill='x', padx=10, pady=(0, 4))
        self._out_var = tk.StringVar()
        _field(frm_out, 'Output folder:', self._out_var, self._browse_outdir)

        # -- HRY Config CSV ---------------------------------------------------
        frm_cfg = _lf('HRY Config CSV  (leave blank to auto-detect from shared/setup/config/scan-dashboard/)', ABLU)
        frm_cfg.pack(fill='x', padx=10, pady=(0, 4))
        self._cfg_var = tk.StringVar()
        _field(frm_cfg, 'Config CSV:', self._cfg_var,
               lambda: self._browse_file(self._cfg_var,
                   [('CSV files', '*.csv *.CSV'), ('All files', '*.*')]))

        # -- Options ----------------------------------------------------------
        frm_opts = _lf('Options', ABLU)
        frm_opts.pack(fill='x', padx=10, pady=(0, 4))
        self._standalone_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            frm_opts,
            text='Build standalone HTML  (single shareable file with all data embedded)',
            variable=self._standalone_var,
            bg=BG, fg=FG, selectcolor=BG2,
            activebackground=BG, activeforeground=FG,
            font=('Arial', 9),
        ).pack(anchor='w')

        # -- Run / Open Dashboard buttons -------------------------------------
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill='x', padx=10, pady=(6, 4))

        self._run_btn = _btn(btn_row, '  Run HRY Scan Analysis  ', self._run,
                             color=GRN, acolor='#2ecc71')
        self._run_btn.config(font=('Arial', 10, 'bold'), pady=5)
        self._run_btn.pack(side='left', fill='x', expand=True, padx=(0, 4))

        self._dash_btn = _btn(btn_row, '  Open HRY Scan Dashboard  ', self._open_dashboard,
                              color='#1f618d', acolor='#2980b9')
        self._dash_btn.config(font=('Arial', 10, 'bold'), pady=5, state='disabled')
        self._dash_btn.pack(side='left', fill='x', expand=True, padx=(4, 0))

        # -- Output log -------------------------------------------------------
        log_frm = _lf('Output', FG2)
        log_frm.pack(fill='both', expand=True, padx=10, pady=(0, 8))
        self._log = scrolledtext.ScrolledText(
            log_frm, height=12,
            font=('Consolas', 9), bg='#0d1b26', fg='#a8d8ea',
            relief='flat', insertbackground=FG, state='disabled')
        self._log.pack(fill='both', expand=True)

    # -- Browse helpers -------------------------------------------------------

    def _browse_file(self, var, filetypes):
        p = filedialog.askopenfilename(filetypes=filetypes)
        if p:
            var.set(p)

    def _browse_outdir(self):
        p = filedialog.askdirectory()
        if p:
            self._out_var.set(p)

    def _browse_dir_into(self, var):
        p = filedialog.askdirectory()
        if p:
            var.set(p)

    def _add_input_files(self):
        paths = filedialog.askopenfilenames(
            title='Add CSV / GZ / ZIP / 7Z files',
            filetypes=[('CSV / GZ / ZIP / 7Z', '*.csv *.CSV *.gz *.csv.gz *.zip *.ZIP *.7z *.7Z'),
                       ('All files', '*.*')])
        existing = set(self._input_listbox.get(0, tk.END))
        for p in paths:
            if p and p not in existing:
                self._input_listbox.insert(tk.END, p)
                existing.add(p)
        if paths and not self._out_var.get().strip():
            self._out_var.set(str(Path(paths[0]).parent / 'output'))

    def _remove_selected_files(self):
        for i in reversed(self._input_listbox.curselection()):
            self._input_listbox.delete(i)

    # -- Load / Save Settings -------------------------------------------------

    def _load_settings(self):
        p = filedialog.askopenfilename(
            title='Load HRY Scan Settings',
            filetypes=[('Scan settings', '*.scancfg.json *.json'),
                       ('All files', '*.*')])
        if p:
            self._load_settings_file(p)

    def _load_settings_file(self, p):
        """Load settings from a JSON/scancfg.json path into the UI fields."""
        try:
            data = json.loads(Path(p).read_text(encoding='utf-8-sig'))
            # support old 'inputs'/'input' keys and new 'input_files'
            _files = data.get('input_files', [])
            if not _files:
                _old = data.get('inputs', data.get('input', ''))
                _files = _old if isinstance(_old, list) else ([_old] if _old else [])
            self._input_listbox.delete(0, tk.END)
            for f in _files:
                if f:
                    self._input_listbox.insert(tk.END, f)
            self._out_var.set(data.get('output_dir', data.get('output', '')))
            self._cfg_var.set(data.get('config', ''))
            self._standalone_var.set(bool(data.get('standalone', False)))
            dp = data.get('dashboard_path', '')
            if dp and os.path.isfile(dp):
                self._dashboard_path = dp
                self.after(0, lambda: self._dash_btn.configure(state='normal'))
            self._log_write(f'Settings loaded from: {p}\n')
        except Exception as exc:
            messagebox.showerror('Load failed', f'Could not load settings:\n{exc}')

    def auto_load(self, json_path: str):
        """Call after the main-loop starts to pre-populate fields from a JSON file."""
        self.after(0, lambda: self._load_settings_file(json_path))

    def _save_settings(self):
        out = self._out_var.get().strip()
        initial_dir = os.path.dirname(out) if out and os.path.isdir(os.path.dirname(out)) else ''
        p = filedialog.asksaveasfilename(
            title='Save HRY Scan Settings',
            initialdir=initial_dir,
            initialfile='scan_settings.scancfg.json',
            defaultextension='.json',
            filetypes=[('Scan settings', '*.scancfg.json *.json'),
                       ('All files', '*.*')])
        if not p:
            return
        try:
            _files = list(self._input_listbox.get(0, tk.END))
            data = {
                'input_files':    _files,
                'output_dir':     self._out_var.get().strip(),
                'config':         self._cfg_var.get().strip(),
                'standalone':     self._standalone_var.get(),
                'dashboard_path': self._dashboard_path,
            }
            Path(p).write_text(json.dumps(data, indent=2, ensure_ascii=False),
                               encoding='utf-8')
            self._log_write(f'Settings saved to: {p}\n')
        except Exception as exc:
            messagebox.showerror('Save failed', f'Could not save settings:\n{exc}')

    # -- Log ------------------------------------------------------------------

    def _log_write(self, msg):
        def _do():
            self._log.configure(state='normal')
            self._log.insert('end', msg)
            self._log.see('end')
            self._log.configure(state='disabled')
        self.after(0, _do)

    def _set_running(self, running):
        def _do():
            self._run_btn.configure(
                state='disabled' if running else 'normal',
                text='  Running...  ' if running else '  Run HRY Scan Analysis  ')
        self.after(0, _do)

    # -- Open Dashboard -------------------------------------------------------

    def _open_dashboard(self):
        p = self._dashboard_path
        if not p or not os.path.isfile(p):
            out = self._out_var.get().strip()
            if out:
                p = os.path.join(out, 'dashboard', 'index.html')
        if p and os.path.isfile(p):
            webbrowser.open(Path(p).as_uri())
        else:
            messagebox.showinfo('Not found',
                'Dashboard not found yet.\nRun the analysis first.')

    # -- Run ------------------------------------------------------------------

    def _run(self):
        input_files = [f.strip() for f in self._input_listbox.get(0, tk.END)
                       if f.strip()]
        out_dir = self._out_var.get().strip()

        if not input_files:
            messagebox.showwarning('Missing input',
                'Add at least one CSV file.')
            return
        for f in input_files:
            if not os.path.isfile(f):
                messagebox.showerror('Not found',
                    f'Input file not found:\n{f}')
                return
        if not out_dir:
            messagebox.showwarning('Missing output', 'Select an Output folder.')
            return

        Path(out_dir).mkdir(parents=True, exist_ok=True)
        # Clean previous output so stale files don't carry over
        dash_dir = Path(out_dir) / 'dashboard'
        if dash_dir.exists():
            shutil.rmtree(dash_dir)
        self._dashboard_path = ''
        self._log.configure(state='normal')
        self._log.delete('1.0', 'end')
        self._log.configure(state='disabled')
        self.after(0, lambda: self._dash_btn.configure(state='disabled'))
        self._set_running(True)
        threading.Thread(
            target=self._worker, args=(input_files, out_dir),
            daemon=True).start()

    def _worker(self, input_files, out_dir):
        def log(msg):
            self._log_write(msg + '\n')

        try:
            out_path = Path(out_dir)
            csv_paths = []

            for i, inp in enumerate(input_files, 1):
                inp_path = Path(inp)
                log('=' * 60)
                log(f'Input {i}/{len(input_files)}: {inp_path.name}')
                log('=' * 60)
                csv_paths.append(str(inp_path))

            log('')
            log('=' * 60)
            log('Pipeline  (reticle + material enrichment + dashboard)')
            log('=' * 60)
            for p in csv_paths:
                log(f'  Input  : {p}')
            log(f'  Output : {out_path}')

            cmd = [sys.executable, '-u', str(Path(__file__).resolve()), '--output', str(out_path)]
            for p in csv_paths:
                cmd += ['--input', p]
            cfg_path = self._cfg_var.get().strip()
            if cfg_path:
                cmd += ['--config', cfg_path]
            if self._standalone_var.get():
                cmd.append('--standalone')

            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
            for line in self._proc.stdout:
                self._log_write(line)
                stripped = line.strip()
                if stripped.startswith('HRY_DASHBOARD:'):
                    hp = stripped[len('HRY_DASHBOARD:'):].strip()
                    if os.path.isfile(hp):
                        self._dashboard_path = hp
                        self.after(0,
                            lambda: self._dash_btn.configure(state='normal'))
            self._proc.wait()
            rc = self._proc.returncode

            if rc == 0:
                log('\nPipeline complete.')
                dash = str(out_path / 'dashboard' / 'index.html')
                if not self._dashboard_path and os.path.isfile(dash):
                    self._dashboard_path = dash
                    self.after(0,
                        lambda: self._dash_btn.configure(state='normal'))
                if self._dashboard_path:
                    log(f'  Dashboard: {self._dashboard_path}')
            else:
                log(f'\nPipeline exited with code {rc}')

        except Exception as exc:
            self._log_write(f'\nERROR: {exc}\n')
        finally:
            self._set_running(False)


# ===========================================================================
# GUI launcher window
# ===========================================================================

class HRYApp(tk.Tk):
    def __init__(self, auto_load_json: str = ''):
        super().__init__()
        self.title('HRY Scan Analysis')
        self.geometry('920x820')
        self.configure(bg=BG)
        style = ttk.Style(self)
        style.theme_use('default')
        style.configure('App.TNotebook', background=BG, borderwidth=0, tabmargins=[2, 4, 2, 0])
        style.configure('App.TNotebook.Tab', background='#253545', foreground=FG2,
                        padding=[14, 5], font=('Arial', 9, 'bold'), borderwidth=0)
        style.map('App.TNotebook.Tab',
                  background=[('selected', BG), ('active', BG2)],
                  foreground=[('selected', ABLU), ('active', FG)])
        nb = ttk.Notebook(self, style='App.TNotebook')
        nb.pack(fill='both', expand=True, padx=0, pady=0)
        self._hry_tab = HRYFrame(nb)
        nb.add(self._hry_tab, text='   HRY Scan Analysis   ')
        if auto_load_json:
            self._hry_tab.auto_load(auto_load_json)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_run_config(path: str) -> dict:
    """Load a JSON run-config file and return its contents as a dict.

    Supported keys (all optional unless noted):
      input        : str | list[str]   – TRACE CSV path(s)           [required]
      config       : str               – HRY config CSV path
      output       : str               – output directory
      keep_tests   : str               – "ATSPEED:ATOM:ATOM3,..."
      standalone   : bool              – embed all assets into single HTML
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if "input" not in data:
        raise ValueError(f"Run-config JSON must contain an 'input' key: {path}")
    if isinstance(data["input"], str):
        data["input"] = [data["input"]]
    return data


def main():
    os.umask(0o002)  # ensure generated files are group-writable on NFS/Samba
    _def_cfg = _find_default_config()  # initial guess for help text only
    ap = argparse.ArgumentParser(
        description="Scan RAWSTR pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Run-config JSON example (--run-config run.json):
  {
    "input":      ["C:/work/scan/data/scan_data.CSV"],
    "config":     "C:/scripts/.../8PF5CV-nvlcpu_n8gl0_HRY_config.csv",
    "output":     "C:/work/scan/data/output",
    "keep_tests": "ATSPEED:ATOM:ATOM3",
    "standalone": false
  }
CLI args override any key set in the JSON file.
""",
    )
    ap.add_argument("--run-config", dest="run_config", metavar="JSON",
                    help="JSON file with run parameters (see epilog for schema)")
    ap.add_argument("--input", action="append", metavar="CSV",
                    help="Input TRACE CSV (repeatable; overrides run-config)")
    ap.add_argument("--config", default=None, metavar="CSV",
                    help=f"HRY config CSV (default: shared/setup/config/scan-dashboard/*.csv, "
                         f"found: {_def_cfg.name if _def_cfg else 'none'})")
    ap.add_argument("--output", default=None, metavar="DIR",
                    help=f"Output directory (default: {_SHARED_OUT})")
    ap.add_argument("--keep-tests", dest="keep_tests", default=None, metavar="FILTER",
                    help="Comma-separated TESTTYPE:MODULE:BLOCK to include (empty = all)")
    ap.add_argument("--standalone", action="store_true", default=None,
                    help="Build standalone HTML with all data embedded")
    args = ap.parse_args()

    # ------------------------------------------------------------------
    # Merge: JSON run-config supplies defaults; CLI args override
    # ------------------------------------------------------------------
    rc: dict = {}
    if args.run_config:
        rc = _load_run_config(args.run_config)

    inputs     = args.input      or rc.get("input")
    # re-run auto-select now that we know the input path
    _def_cfg   = _find_default_config(inputs[0] if inputs else None)
    hry_config = args.config     or rc.get("config") or (str(_def_cfg) if _def_cfg else None)
    output     = args.output     or rc.get("output") or str(_SHARED_OUT)
    keep_tests_str = args.keep_tests if args.keep_tests is not None else rc.get("keep_tests", "")
    standalone = args.standalone or rc.get("standalone", True)

    if not inputs:
        ap.error("--input (or 'input' in run-config JSON) is required")
    if not hry_config:
        ap.error("--config is required: no *.csv found in shared/setup/config/scan-dashboard/")

    keep_tests = (
        set(keep_tests_str.strip().split(",")) if keep_tests_str and keep_tests_str.strip() else None
    )
    results = [process(inp, hry_config, keep_tests=keep_tests) for inp in inputs]
    result = _merge_results(results)

    out_dir = Path(output)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_dashboard(result, out_dir, standalone=standalone)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].startswith('--'):
        main()
    else:
        _json_arg = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith('.json') else ''
        HRYApp(auto_load_json=_json_arg).mainloop()
