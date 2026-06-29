"""
Scan Dashboard Pipeline - RAWSTR approach
==========================================
Reads a TRACE CSV containing SCN_*::*_HRY_RAWSTR_* columns.
Each RAWSTR value is DEFLATE32-compressed; after decoding it is a character
string where:
  index 0        = reset pin  (1 = reset passed, anything else = test not valid)
  index 1 .. N   = per-IP result:
                     0 = FAIL
                     1 = PASS
                     8 = UNTESTED  (TotalFailCaptureCount limit reached)
                     9 = UNASSIGNED (no HRYIndex maps here)

An IP is considered FAIL when:  reset[0] == "1"  AND  bit[INDEX] == "0"

Column name format:
  SCN_{MOD}::{TESTTYPE}_{BLOCK}_HRY_{KILL}_{SUBFLOW}_{DFT}_{VRAIL}_{VCORNER}_{FREQ}_{STEPPING}_POR_HRY_RAWSTR_{JOBID}

Config CSV:  MODULE, TEST, IP, REGION, PARTITION, INDEX, ...

Output:
  <output_dir>/dashboard/index.html   (copy of template)
  <output_dir>/dashboard/data.js      (const SCAN_DATA = {...};)

Usage:
  python pipeline.py --input data.csv --config hry_config.csv --output ./results
"""

from __future__ import annotations

import re
import zlib
import json
import shutil
import argparse
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Default paths (relative to this script → shared area)
# ---------------------------------------------------------------------------
_SRC_DIR      = Path(__file__).parent
_SCRIPT_DIR   = _SRC_DIR.parent                                   # scan-dashboard/
_REPO_ROOT    = _SRC_DIR.parents[1]                               # app.dashboard.nvl/
_SHARED_CFG   = _REPO_ROOT / "shared" / "setup" / "config" / "scan-dashboard"
_SHARED_OUT   = _SHARED_CFG                                       # default output dir
_TEMPLATE_DIR = _SCRIPT_DIR / "src" / "dashboard"                # template index.html
_PLOTLY_JS      = _REPO_ROOT / "shared" / "library" / "plotly-2.32.0.min.js"
_SHARED_RETICLE = _REPO_ROOT / "shared" / "reticle"
_SHARED_MATERIAL = _REPO_ROOT / "shared" / "material"
_WAFER_TOOLS    = Path(r"C:\scripts\app.yield.nvl\code\utilities\wafer_tools")


def _find_default_config() -> Path | None:
    """Return the first *.csv found in shared/setup/scan-dashboard/, or None."""
    if _SHARED_CFG.exists():
        csvs = sorted(_SHARED_CFG.glob("*.csv"))
        if csvs:
            return csvs[0]
    return None


def load_material_lookup() -> dict:
    """Load shared/material/*.csv and return a lookup dict.

    Keys (both registered for each row so 7-char and 8-char Intel lot IDs both match):
      '{intel_lot_id}|{WaferID_int}'   e.g. 'Q603S6R|1'  or 'Q529P1V0|1'
    Value: {'lot_num', 'program', 'material', 'stepping', 'aio_bb'}
    """


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
    """Load shared/material/*.csv and return a lookup dict.

    Keys (both registered for each row so 7-char and 8-char Intel lot IDs both match):
      '{intel_lot_id}|{WaferID_int}'   e.g. 'Q603S6R|1'  or 'Q529P1V0|1'
    Value: {'lot_num', 'program', 'material', 'stepping', 'aio_bb'}
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

            # Collect ALL Intel lot ID columns (7-char, 8-char, various names)
            intel_lot_cols = [c for k, c in cl.items()
                              if 'intel' in k and 'lot' in k
                              and 'wafer' not in k and 'tsmc' not in k]
            tsmc_col   = cl.get('tsmc_lot') or cl.get('tsmc lot')
            wfr_col    = cl.get('waferid') or cl.get('wafer_id') or cl.get('wafer')
            mat_col    = next((c for k, c in cl.items()
                               if 'material type' in k or k == 'material'), None)
            step_col   = cl.get('stepping')
            aio_col    = cl.get('aio/bb') or cl.get('aio_bb')
            lotnum_col = cl.get('lot#') or cl.get('lot_num') or cl.get('lot number')

            lot_cols = intel_lot_cols or ([tsmc_col] if tsmc_col else [])
            if not (wfr_col and lot_cols):
                print(f"[pipeline] WARN: material {fpath.name}: missing lot/wafer columns")
                continue

            for _, row in dm.iterrows():
                wfr_id = str(row.get(wfr_col, '')).strip()
                if not wfr_id or wfr_id == 'nan':
                    continue
                try:
                    wfr_num = int(float(wfr_id))
                except Exception:
                    continue
                mat_str    = str(row.get(mat_col,    '') if mat_col    else '').strip()
                step_str   = str(row.get(step_col,   '') if step_col   else '').strip()
                aio_str    = str(row.get(aio_col,    '') if aio_col    else '').strip()
                lotnum_str = str(row.get(lotnum_col, '') if lotnum_col else '').strip()
                # Derive program: 'NVL816-BLLC-L0 AIO' → 'NVL816-BLLC'
                prog_str = ''
                if mat_str:
                    base = mat_str.split()[0]  # 'NVL816-BLLC-L0'
                    m = _re.match(r'^(.+)-([A-Z]\d)$', base)
                    prog_str = m.group(1) if m else base
                entry = {
                    'lot_num':  lotnum_str,
                    'program':  prog_str,
                    'material': mat_str,
                    'stepping': step_str,
                    'aio_bb':   aio_str,
                }
                # Register under every Intel lot ID variant found in this row
                # so both Q529P1V (7-char) and Q529P1V0 (8-char) resolve
                seen_ids: set = set()
                for lc in lot_cols:
                    lot_id = str(row.get(lc, '')).strip()
                    if lot_id and lot_id != 'nan' and lot_id not in seen_ids:
                        seen_ids.add(lot_id)
                        lookup[f"{lot_id}|{wfr_num}"] = entry
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
    df["INDEX"] = df["INDEX"].astype(int)
    return df[["MODULE", "TEST", "IP", "REGION", "PARTITION", "INDEX"]]


# ---------------------------------------------------------------------------
# Column name parser
# ---------------------------------------------------------------------------
_COL_RE = re.compile(
    r"^(SCN_\w+)::(CHAIN|STUCKAT|ATSPEED|DIAG)_(\w+?)_HRY_([KE])_(\w+?)_\w+?_\w+?_(\w+?)_(\w+?)_\w+?_POR_HRY_RAWSTR_(\d+)$",
    re.IGNORECASE,
)

def _parse_col(col: str) -> dict | None:
    m = _COL_RE.match(col)
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
        key = f"{lot}|{wfr}|{vid}"
        if key not in dm:
            dm[key] = {
                "LOT": lot,
                "WAFER": int(wfr) if wfr is not None else None,
                "X": r.get("X"),
                "Y": r.get("Y"),
                "VISUAL_ID": vid,
                "Layout": str(lot)[:6].upper(),
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

    # Detect material column directly in the scan CSV (TRACE Sort exports often include it).
    # Strategy 1: a column with 'material' in the name (e.g. 'Material')
    # Strategy 2: DevRevStep* column (e.g. 'DevRevStep_119325' = '8PF5CVL')
    #             combined with Program Name* column for the program field
    import re as _re2
    _csv_mat_col    = next((c for c in df.columns if 'material' in c.lower()), None)
    _devrev_col     = next((c for c in df.columns if c.upper().startswith('DEVREVSTEP')), None)
    _progname_col   = next((c for c in df.columns
                            if 'program name' in c.lower() or 'program_name' in c.lower()), None)
    _csv_mat_by_wk: dict = {}

    if all(c in id_df.columns for c in ["LOT", "WAFER"]):
        if _csv_mat_col:
            # Strategy 1: explicit Material column (e.g. 'NVL816-BLLC-L0 AIO')
            id_df['_MAT_TMP'] = df[_csv_mat_col].values
            for (lot, wfr), grp in id_df.groupby(["LOT", "WAFER"]):
                vals = grp['_MAT_TMP'].dropna().unique()
                mat_str = str(vals[0]).strip() if len(vals) else ''
                if not mat_str or mat_str == 'nan':
                    continue
                prog_str, step_str, aio_str = '', '', ''
                base = mat_str.split()[0]
                m2 = _re2.match(r'^(.+)-([A-Z]\d)$', base)
                if m2:
                    prog_str = m2.group(1)
                    step_str = m2.group(2)
                else:
                    prog_str = base
                parts = mat_str.split()
                if len(parts) >= 2:
                    aio_str = parts[-1]
                _csv_mat_by_wk[f"{lot}|{int(wfr)}"] = {
                    'lot_num': '', 'program': prog_str,
                    'material': mat_str, 'stepping': step_str, 'aio_bb': aio_str,
                }
            id_df.drop(columns=['_MAT_TMP'], inplace=True)
            print(f"[pipeline] CSV Material column '{_csv_mat_col}': {len(_csv_mat_by_wk)} wafer(s)")

        elif _devrev_col:
            # Strategy 2: DevRevStep column e.g. '8PF5CVL' → process=8PF5CV, step=L0
            _proc_map = build_process_to_product_map()
            id_df['_DR_TMP'] = df[_devrev_col].values
            if _progname_col:
                id_df['_PN_TMP'] = df[_progname_col].values
            for (lot, wfr), grp in id_df.groupby(["LOT", "WAFER"]):
                dr_vals = grp['_DR_TMP'].dropna().unique()
                dr = str(dr_vals[0]).strip() if len(dr_vals) else ''
                if not dr or dr == 'nan':
                    continue
                pn = ''
                if _progname_col and '_PN_TMP' in grp.columns:
                    pn_vals = grp['_PN_TMP'].dropna().unique()
                    pn = str(pn_vals[0]).strip() if len(pn_vals) else ''
                # Parse '8PF5CVL' → proc='8PF5CV', step_char='L'
                dr_m = _re2.match(r'^(8\w+[A-Z]{2})(\w?)$', dr)
                proc = dr_m.group(1) if dr_m else dr
                step_char = dr_m.group(2) if dr_m else ''
                stepping = f"{step_char}0" if step_char and step_char.isalpha() else step_char
                prod_info = _proc_map.get(proc, {})
                product = prod_info.get('product', '')
                # Prefer stepping from DevRevStep; fall back to filename stepping
                if not stepping:
                    stepping = prod_info.get('stepping', '')
                mat_str = f"{product}-{stepping}" if product and stepping else (product or proc)
                _csv_mat_by_wk[f"{lot}|{int(wfr)}"] = {
                    'lot_num': '', 'program': pn or dr,
                    'material': mat_str, 'stepping': stepping, 'aio_bb': '',
                }
            id_df.drop(columns=['_DR_TMP', '_PN_TMP'], errors='ignore', inplace=True)
            print(f"[pipeline] DevRevStep column '{_devrev_col}': {len(_csv_mat_by_wk)} wafer(s)")

    # Build reticle layouts early — needed for the correct total-die denominator
    reticle_layout = build_reticle_layouts()

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

    # Material enrichment
    _mat = load_material_lookup()

    lot_material: dict = {}
    for wk in total_dies_per_wafer:
        lot_part, wafer_part = wk.split('|', 1)
        # Mirror yield-dashboard logic:
        #   LOT7  = first 7 chars of lot
        #   WAFER2 = last 2 chars of absolute SORT_WAFER as int (e.g. 707→7, 712→12)
        #            matches the 1-based WaferID in the shared material CSV
        _lot7 = lot_part[:7]
        try:
            _wafer2 = int(str(int(wafer_part))[-2:])
        except (ValueError, TypeError):
            _wafer2 = None
        # Try exact key first, then LOT7|WAFER2 (yield-dashboard convention)
        info = (_mat.get(wk)
                or (_mat.get(f"{_lot7}|{_wafer2}") if _wafer2 is not None else None))
        if info:
            entry = dict(info)
            # If CSV has an explicit program column, prefer it
            if _prog_by_lot.get(lot_part):
                entry['program'] = _prog_by_lot[lot_part]
        else:
            # Fall back to material parsed directly from the scan CSV
            entry = _csv_mat_by_wk.get(wk)
        if entry:
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
        "total_dies_per_wafer": total_dies_per_wafer,
        "col_names":    col_names,
        "lot_material":  lot_material,
        "has_material_files": n_mat_files > 0 or bool(_csv_mat_by_wk),
    }

    # decode + aggregate
    records = []
    for col_info in scn_cols:
        module   = col_info["module"]
        block    = col_info["block"]
        col_name = col_info["col"]

        cfg_sub = cfg[(cfg["MODULE"] == module) & (cfg["TEST"] == block)]
        if cfg_sub.empty:
            print(f"[pipeline]   WARN: no config for ({module}, {block}) — skipping")
            continue

        decoded = df[col_name].apply(_deflate32_decode)

        for _, cfg_row in cfg_sub.iterrows():
            idx       = int(cfg_row["INDEX"])
            partition = cfg_row["PARTITION"]
            ip        = cfg_row["IP"]
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
    js_content = "// Auto-generated by pipeline.py -- do not edit manually\n"
    js_content += f"const SCAN_DATA = {json.dumps(result, separators=(',', ':'))};\n"
    data_js.write_text(js_content, encoding="utf-8")

    # Inject <script src="data.js"> into index.html so SCAN_DATA is defined
    _html = dst_html.read_text(encoding="utf-8")
    _html = _html.replace("</head>", '<script src="data.js"></script>\n</head>', 1)
    dst_html.write_text(_html, encoding="utf-8")

    # ------------------------------------------------------------------
    # Standalone mode: embed data.js (and Plotly) inline so the HTML
    # can be opened directly without a web server or sibling files.
    # ------------------------------------------------------------------
    if standalone:
        _html = dst_html.read_text(encoding="utf-8")
        # Replace <script src="data.js"></script> with inline data
        _html = _html.replace(
            '<script src="data.js"></script>',
            f'<script>\n{js_content}</script>',
        )
        # Replace <script src="plotly-*.min.js"> with inline Plotly if available
        import re as _re
        def _inline_plotly(m):
            src = m.group(1)
            pjs = dash_dir / src
            if pjs.exists():
                print(f"[pipeline] Inlining Plotly ({pjs.stat().st_size:,} bytes)")
                return f'<script>\n{pjs.read_text(encoding="utf-8")}\n</script>'
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
    _def_cfg = _find_default_config()
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
    main()
