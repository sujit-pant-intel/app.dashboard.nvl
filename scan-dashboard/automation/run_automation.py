"""
run_automation.py
=================
Automated scan dashboard generation for NCXSDJXL0H61* programs.

Workflow
--------
1.  Pull AQUA data (or use local CSV via --local-csv).

1b. Split the AQUA data by (full TestProgram name, Operation code):
        NCXSDJXL0H61C002620_119325
        NCXSDJXL0H61D*_132322
    Stored under  data/programs/{letter}/*.7z
    Replace rule:  if the lot/wafer set for a TP-oper changed → overwrite its gz.
                   If no change → leave the gz untouched (skip).

2.  For each changed TP:
    - Extract the accumulated CSV from .7z to a temp dir
    - Write run_config.json: {"input": [csv_path], "output": "<output_dir>"}
    - Run pipeline.py --run-config run_config.json
    - Parse output data.js for scan summary stats

3.  Update run_log.html (cumulative, one section per run).
4.  Send HTML summary email.

Output base (default)
---------------------
  \\\\samba.zsc10.intel.com\\nfs\\zsc10\\disks\\gsc_gwa011\\users\\snpant\\auto\\scan
    data\\
        programs\\
            0H61C\\
                NCXSDJXL0H61C002620_119325.7z   ← per-TP-oper accumulated
                raw_YYYYMMDD_HHMMSS.7z           ← raw AQUA pull snapshot
            0H61D\\
                …
    output\\
        NVL_0H61C_YYYYMMDD_HHMMSS\\
            NCXSDJXL0H61C002620_119325\\
                dashboard\\
                    index.html
                    data.js
    run_log.html

Usage
-----
  python run_automation.py                                        # full run (AQUA pull)
  python run_automation.py --dry-run                             # plan only
  python run_automation.py --local-csv "C:\\work\\scan\\data\\scan_data.CSV"
  python run_automation.py --base-dir C:\\work\\auto\\scan       # override output root
  python run_automation.py --force                               # rerun even if unchanged
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

# ── Ensure UTF-8 output on Windows ─────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE        = Path(__file__).resolve().parent
_REPO_ROOT   = _HERE.parent.parent          # app.dashboard.nvl/
_PIPELINE    = _REPO_ROOT / "scan-dashboard" / "src" / "pipeline.py"
_AQUA_CFG    = _REPO_ROOT / "shared" / "setup" / "automation" / "scan-dashboard" / "NVL_Sort_Scan - Dashboard.txt"
_YIELD_TGT   = _REPO_ROOT / "shared" / "setup" / "config" / "scan-dashboard" / "yield-estimate-per-fault-count.csv"

# ── Defaults ───────────────────────────────────────────────────────────────────
_BASE_DIR    = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\scan")
_DATA_DIR    = _BASE_DIR / "data"
_RUN_LOG     = _BASE_DIR / "run_log.html"
_EMAIL_TO    = "sujit.n.pant@intel.com"
_DEFAULT_DAYS = 7

_AQUA_EXE_GAR = r"\\PGSAPP3301.gar.corp.intel.com\Installer\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"
_AQUA_EXE_AMR = r"\\amr.corp.intel.com\ec\proj\fm\MPD\AQUA\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"

_EMAIL_CFG    = _REPO_ROOT / "shared" / "setup" / "automation" / "scan-dashboard" / "scan_setup_config.json"
_7Z_EXE       = Path(r"C:\Program Files\7-Zip\7z.exe")

_SMTP_SERVER  = "smtpauth.intel.com"
_SMTP_PORT    = 587
_SMTP_FROM    = "sujit.n.pant@intel.com"


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — AQUA pull
# ─────────────────────────────────────────────────────────────────────────────

def _aqua_report_name(config_path: Path) -> str:
    try:
        for line in config_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if line.strip().startswith("@ Report :"):
                return line.strip().split(":", 1)[1].strip()
    except Exception:
        pass
    return "NVL_Sort_Yield"


def _compress_aqua_to_7z(gz_path: Path) -> Path | None:
    """Re-compress a .csv.gz AQUA snapshot to .7z (better compression).
    Returns the new .7z path on success, or None on failure.
    The original .csv.gz is deleted after successful compression.
    """
    if not _7Z_EXE.exists():
        return None
    if gz_path.suffix != ".gz" or not gz_path.stem.endswith(".csv"):
        return None

    csv_path = gz_path.with_suffix("")                          # strip .gz → .csv
    z7_path  = gz_path.parent / (gz_path.stem[:-4] + ".7z")    # NAME.7z
    try:
        with gzip.open(gz_path, "rb") as fi, open(csv_path, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        result = subprocess.run(
            [str(_7Z_EXE), "a", "-mx=5", "-mmt=on", str(z7_path), str(csv_path)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            _log(f"  WARNING: 7z compression failed: {result.stderr.strip()[:200]}")
            return None
        try: csv_path.unlink()
        except Exception: pass
        try: gz_path.unlink()
        except Exception: pass
        return z7_path
    except Exception as e:
        _log(f"  WARNING: _compress_aqua_to_7z: {e}")
        return None
    finally:
        if csv_path.exists():
            try: csv_path.unlink()
            except Exception: pass


def _compress_csv_to_7z(csv_path: Path) -> Path | None:
    """Compress a plain .csv file to .7z. Deletes original on success."""
    if not _7Z_EXE.exists():
        return None
    z7_path = csv_path.with_suffix(".7z")
    try:
        result = subprocess.run(
            [str(_7Z_EXE), "a", "-mx=5", "-mmt=on", str(z7_path), str(csv_path)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            _log(f"  WARNING: 7z compress failed: {result.stderr.strip()[:200]}")
            return None
        try: csv_path.unlink()
        except Exception: pass
        return z7_path
    except Exception as e:
        _log(f"  WARNING: _compress_csv_to_7z: {e}")
        return None


def pull_aqua(aqua_exe: str, report_config: Path, data_dir: Path, dry_run: bool) -> Path | None:
    """Run AquaCmdLine.exe with the repo config. Returns path to the downloaded file."""
    data_dir.mkdir(parents=True, exist_ok=True)
    ts       = _ts()
    out_base = data_dir / f"NCXSDJXL0H61_{ts}"
    out_req  = out_base.with_suffix(".zip")

    report_name = _aqua_report_name(report_config)
    temp_dir    = Path(os.environ.get("TEMP", tempfile.gettempdir()))
    temp_pat    = f"{report_name}*.CSV"

    _exe_lower   = str(aqua_exe).lower()
    _aqua_server = "AMR" if "amr" in _exe_lower else "GAR"

    cmd = [
        aqua_exe,
        "-AquaServer",     _aqua_server,
        "-ReportConfig",   str(report_config),
        "-OutputFileName", str(out_req),
    ]

    _log(f"{'DRY-RUN  ' if dry_run else ''}AQUA pull → {out_base}.*")
    _log(f"  Config : {report_config}")
    _log(f"  CMD    : {' '.join(cmd)}")

    if dry_run:
        _log("  DRY-RUN: skipping AQUA, returning dummy path")
        return out_base.with_suffix(".csv")

    before_temp = {p.resolve() for p in temp_dir.glob(temp_pat)}

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.stdout.strip():
            _log(f"  AQUA: {result.stdout.strip()[:400]}")
        if result.returncode != 0:
            _log(f"  ERROR: AQUA rc={result.returncode}\n{result.stderr.strip()[:400]}")
            return None
    except FileNotFoundError:
        _log(f"  ERROR: AquaCmdLine.exe not found: {aqua_exe}")
        return None
    except subprocess.TimeoutExpired:
        _log("  ERROR: AQUA timed out")
        return None

    written = [p for p in data_dir.glob(f"{out_base.name}*") if p.stat().st_size > 0]
    if written:
        out = max(written, key=lambda p: p.stat().st_mtime)
        _log(f"  Output: {out.name} ({out.stat().st_size:,} bytes)")
        return out

    after_temp = {p.resolve() for p in temp_dir.glob(temp_pat)}
    new_csvs   = sorted(after_temp - before_temp, key=lambda p: p.stat().st_mtime)
    if new_csvs:
        src  = max(new_csvs, key=lambda p: p.stat().st_mtime)
        dest = data_dir / f"NCXSDJXL0H61_{ts}.csv"
        shutil.copy2(src, dest)
        _log(f"  Fallback from %TEMP%: {src.name} → {dest.name}")
        return dest

    _log("  ERROR: AQUA produced no output file")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Read & split CSV by (TestProgram full name, Operation)
# ─────────────────────────────────────────────────────────────────────────────

def _read_aqua_file(path: Path) -> tuple[list[dict], str]:
    """Read an AQUA output file (.csv, .csv.gz, .zip, .7z).
    Handles nested chains: 7z→csv, 7z→csv.gz, zip→csv, gz→csv.
    Returns (rows, delimiter).
    """
    def _inner_from_bytes(raw: bytes, src_path: Path = path) -> str:
        if raw[:6] == b'7z\xbc\xaf\x27\x1c':
            with tempfile.TemporaryDirectory() as _tmp:
                _tmp_p = Path(_tmp)
                subprocess.run(
                    [str(_7Z_EXE), "e", str(src_path), f"-o{_tmp}", "-y"],
                    check=True, capture_output=True,
                )
                for _pat in ("*.csv", "*.csv.gz", "*.zip"):
                    _hits = sorted(_tmp_p.glob(_pat))
                    if _hits:
                        return _inner_from_bytes(_hits[0].read_bytes(), _hits[0])
            raise ValueError(f"No CSV/zip/gz found inside {src_path.name}")
        elif raw[:2] == b'PK':
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = z.namelist()
                pick  = next((n for n in names if n.lower().endswith('.csv')), names[0])
                return _inner_from_bytes(z.read(pick))
        elif raw[:2] == b'\x1f\x8b':
            return _inner_from_bytes(gzip.decompress(raw))
        else:
            return raw.decode("utf-8-sig", errors="replace")

    inner = _inner_from_bytes(path.read_bytes())
    first_line = inner.split("\n")[0]
    delim = "\t" if "\t" in first_line else ","
    rows  = list(csv.DictReader(io.StringIO(inner), delimiter=delim))
    return rows, delim


def _write_gz(rows: list[dict], fieldnames: list[str], path: Path) -> None:
    """Write rows as gzip-compressed CSV (UTF-8, comma-delimited)."""
    buf = io.StringIO()
    w   = csv.DictWriter(buf, fieldnames=fieldnames,
                         extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for row in rows:
        w.writerow({k: row.get(k, "") for k in fieldnames})
    path.write_bytes(gzip.compress(buf.getvalue().encode("utf-8"), compresslevel=6))


def _safe_filename(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', s).strip()


def split_by_tp_oper(rows: list[dict]) -> dict[str, tuple[list[dict], list[str]]]:
    """Split AQUA rows by (full TestProgram name, Operation code).

    Handles both wide format (columns like 'Program Name_119325') and
    tall/single-op format (columns 'Program Name' + 'Operation').

    Returns:
        dict  safe_key → (rows, fieldnames)
        safe_key = "{safe_tp_name}_{op_code}"
    """
    if not rows:
        return {}

    headers    = list(rows[0].keys())
    header_set = set(headers)

    op_codes: set[str] = set()
    for h in headers:
        m = re.search(r'_(\d{5,6})$', h)
        if m:
            op_codes.add(m.group(1))

    groups: dict[str, tuple[list[dict], list[str]]] = {}

    # ── Wide format ───────────────────────────────────────────────────────────
    if len(op_codes) >= 1:
        _log(f"  Wide format — ops: {sorted(op_codes)}")
        common_cols = [h for h in headers if not re.search(r'_\d{5,6}$', h)]

        for op in sorted(op_codes):
            prog_col = f"Program Name_{op}"
            if prog_col not in header_set:
                continue

            by_prog: dict[str, list[dict]] = {}
            for row in rows:
                prog = (row.get(prog_col) or "").strip()
                if not prog or prog.upper() in ("N/A", "NA", "NONE", "-", ""):
                    continue
                narrow: dict = {}
                for col in common_cols:
                    narrow[col] = row.get(col, "")
                for col, val in row.items():
                    if col.endswith(f"_{op}"):
                        narrow[col] = val
                by_prog.setdefault(prog, []).append(narrow)

            for prog, prog_rows in by_prog.items():
                key  = _safe_filename(f"{prog}_{op}")
                hdrs = list(prog_rows[0].keys())
                groups[key] = (prog_rows, hdrs)
                _log(f"    {key}: {len(prog_rows):,} rows")

        return groups

    # ── Tall / single-op format ───────────────────────────────────────────────
    _log("  Tall/single-op format")
    prog_col = next((h for h in headers if h.lower() in
                     ("program name", "testprogram", "test program", "program")), None)
    op_col   = next((h for h in headers if h.lower() == "operation"), None)

    for row in rows:
        prog = (row.get(prog_col) or "").strip() if prog_col else ""
        op   = (row.get(op_col)   or (next(iter(op_codes), "unknown"))).strip()
        key  = _safe_filename(f"{prog}_{op}") if prog else f"unknown_{op}"
        if key not in groups:
            groups[key] = ([], list(row.keys()))
        groups[key][0].append(row)

    for key, (rws, _) in groups.items():
        _log(f"    {key}: {len(rws):,} rows")

    return groups


def _lot_wafer_set(rows: list[dict]) -> frozenset:
    """Return a frozenset of (lot, wafer, date) strings for change-detection."""
    if not rows:
        return frozenset()
    hdrs = list(rows[0].keys())

    def _bare(h: str) -> str:
        return re.sub(r'_\d{4,}$', '', h).lower()

    lot_col   = next((h for h in hdrs if _bare(h) in
                      ("lot", "sort_lot", "lot number", "lot_number", "lot id")), None)
    wafer_col = next((h for h in hdrs if _bare(h) in
                      ("wafer", "sort_wafer", "wafer number", "wafer_number", "wafer id")), None)
    date_col  = next((h for h in hdrs if _bare(h) in
                      ("date", "test date", "test_date", "testdate",
                       "start date", "start_date", "finish date", "finish_date",
                       "insertion", "insert_date", "lots end date time",
                       "lots end date", "lots_end_date_time")), None)
    if not lot_col or not wafer_col:
        return frozenset()
    return frozenset(
        (
            str(r.get(lot_col,   "")).strip(),
            str(r.get(wafer_col, "")).strip(),
            str(r.get(date_col,  "")).strip() if date_col else "",
        )
        for r in rows
    )


def update_tp_gz(
    key: str,
    new_rows: list[dict],
    fieldnames: list[str],
    data_dir: Path,
    dry_run: bool,
) -> tuple[Path, bool]:
    """Maintain data_dir/programs/{letter}/{key}.7z.

    • If file doesn't exist        → create it.
    • If lot/wafer set changed     → replace it entirely.
    • If identical                 → leave untouched.

    Returns (gz_path, changed).
    """
    prog_dir = data_dir / "programs"
    _m = re.search(r'[0-9A-Za-z]H61([A-Za-z])', key)
    _letter_sub = f"0H61{_m.group(1).upper()}" if _m else "0H61X"
    letter_dir  = prog_dir / _letter_sub
    gz_path     = letter_dir / f"{key}.csv.gz"
    z7_path     = letter_dir / f"{key}.7z"
    cache_path  = z7_path if z7_path.exists() else gz_path

    if not dry_run:
        letter_dir.mkdir(parents=True, exist_ok=True)

    _log(f"  {key}: writing {len(new_rows):,} rows")

    if not dry_run:
        _write_gz(new_rows, fieldnames, gz_path)
        _log(f"    → {gz_path.stat().st_size:,} bytes (gz)")
        _final = _compress_aqua_to_7z(gz_path)
        if _final:
            _log(f"    → compressed: {_final.name}  ({_final.stat().st_size:,} bytes)")
            return _final, True
    else:
        _log(f"    DRY-RUN: would write {gz_path}")

    return gz_path, True


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Run scan pipeline for a TP key
# ─────────────────────────────────────────────────────────────────────────────

def _extract_7z_to_csv(z7_path: Path, tmp_dir: str) -> Path | None:
    """Extract a .7z (or .csv.gz) archive to tmp_dir and return path to CSV file."""
    tmp = Path(tmp_dir)

    if z7_path.suffix.lower() == ".7z":
        if not _7Z_EXE.exists():
            _log(f"  ERROR: 7z.exe not found at {_7Z_EXE}")
            return None
        result = subprocess.run(
            [str(_7Z_EXE), "e", str(z7_path), f"-o{tmp}", "-y"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            _log(f"  ERROR: 7z extract failed: {result.stderr.strip()[:200]}")
            return None
        # Find extracted CSV (prefer largest)
        for pat in ("*.csv", "*.CSV"):
            hits = sorted(tmp.glob(pat), key=lambda p: p.stat().st_size, reverse=True)
            if hits:
                return hits[0]
        # Fallback: look for csv.gz
        hits = sorted(tmp.glob("*.csv.gz"), key=lambda p: p.stat().st_size, reverse=True)
        if hits:
            out_csv = hits[0].with_suffix("")  # strip .gz
            with gzip.open(hits[0], "rb") as fi, open(out_csv, "wb") as fo:
                shutil.copyfileobj(fi, fo)
            return out_csv
        _log(f"  ERROR: no CSV found after extracting {z7_path.name}")
        return None

    if z7_path.suffix.lower() == ".gz" and z7_path.stem.endswith(".csv"):
        out_csv = tmp / z7_path.stem
        with gzip.open(z7_path, "rb") as fi, open(out_csv, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        return out_csv

    if z7_path.suffix.lower() == ".csv":
        dest = tmp / z7_path.name
        shutil.copy2(z7_path, dest)
        return dest

    _log(f"  ERROR: unsupported archive format: {z7_path.suffix}")
    return None


def run_pipeline_for_tp(
    tp_key: str,
    tp_archive: Path,
    run_dir: Path,
    dry_run: bool,
) -> tuple[bool, Path, Path | None]:
    """Extract accumulated CSV from archive, build run_config.json, run pipeline.py.

    Returns (success, tp_output_dir, data_js_path).
    """
    tp_output_dir = run_dir / tp_key

    if dry_run:
        _log(f"  [{tp_key}] DRY-RUN: would extract {tp_archive.name} and run pipeline")
        return True, tp_output_dir, None

    if not tp_archive.exists():
        _log(f"  [{tp_key}] ERROR: archive not found: {tp_archive}")
        return False, tp_output_dir, None

    tmp_dir_obj = tempfile.TemporaryDirectory(prefix="scan_auto_")
    tmp_dir     = tmp_dir_obj.name
    try:
        csv_path = _extract_7z_to_csv(tp_archive, tmp_dir)
        if csv_path is None:
            return False, tp_output_dir, None
        _log(f"  [{tp_key}] Extracted: {csv_path.name}  ({csv_path.stat().st_size:,} bytes)")

        run_dir.mkdir(parents=True, exist_ok=True)

        cfg = {
            "input":  [str(csv_path)],
            "output": str(tp_output_dir),
        }
        json_path = run_dir / f"run_config_{tp_key}.json"
        json_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        _log(f"  [{tp_key}] run_config → {json_path}")
        _log(f"  [{tp_key}] output    → {tp_output_dir}")

        cmd = [sys.executable, str(_PIPELINE), "--run-config", str(json_path)]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}

        try:
            proc = subprocess.run(
                cmd, capture_output=False, text=True,
                timeout=7200, env=env, cwd=str(_PIPELINE.parent),
            )
            ok = proc.returncode == 0
            if not ok:
                _log(f"  [{tp_key}] WARNING: pipeline rc={proc.returncode}")
        except subprocess.TimeoutExpired:
            _log(f"  [{tp_key}] ERROR: pipeline timed out")
            return False, tp_output_dir, None

        data_js = tp_output_dir / "dashboard" / "data.js"
        return ok, tp_output_dir, (data_js if data_js.exists() else None)

    finally:
        tmp_dir_obj.cleanup()


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Parse pipeline output for email summary
# ─────────────────────────────────────────────────────────────────────────────

def _yield_target_from_best_wafer(scan_data: dict) -> str:
    """Compute scan yield target using the best (lowest-fail-rate) wafer as reference.

    For each unique (IP, MODULE, BLOCK, REGION) instance observed in per_ip:
        p_inst = failing_dies_on_best_wafer / total_dies_best_wafer
    Y_target = product(1 - p_inst)  over all instances seen on the best wafer

    Returns a formatted string like '94.32%', or '–' if data is insufficient.
    """
    import math
    try:
        meta    = scan_data.get("meta", {})
        die_map = scan_data.get("die_map", [])
        per_ip  = scan_data.get("per_ip",  [])
        tdpw    = meta.get("total_dies_per_wafer", {})

        if not tdpw or not die_map or not per_ip:
            return "–"

        # ── Step 1: count failing dies per wafer from die_map ─────────────────
        fail_per_wk: dict = {}
        for d in die_map:
            lot = str(d.get("LOT", ""))
            wfr = d.get("WAFER")
            if wfr is None:
                continue
            wk = f"{lot}|{int(wfr)}"
            fail_per_wk[wk] = fail_per_wk.get(wk, 0) + 1

        # ── Step 2: find best wafer (min fail rate) ───────────────────────────
        best_wk         = None
        best_fail_rate  = float("inf")
        for wk, total in tdpw.items():
            if total <= 0:
                continue
            rate = fail_per_wk.get(wk, 0) / total
            if rate < best_fail_rate:
                best_fail_rate = rate
                best_wk        = wk

        if best_wk is None:
            return "–"

        best_total_dies = tdpw[best_wk]
        _log(f"  Yield target ref: {best_wk}  fail_rate={best_fail_rate*100:.2f}%  "
             f"({fail_per_wk.get(best_wk,0)}/{best_total_dies} dies failing)")

        # ── Step 3: per-instance fail counts on best wafer ───────────────────
        # Key = (IP, MODULE, BLOCK, REGION); value = set of unique die keys
        inst_fails: dict = {}
        best_lot, best_wfr_str = best_wk.split("|", 1)
        for r in per_ip:
            r_lot = str(r.get("LOT", ""))
            r_wfr = r.get("WAFER")
            if r_wfr is None:
                continue
            if f"{r_lot}|{int(r_wfr)}" != best_wk:
                continue
            inst_key = (
                str(r.get("IP",     "")),
                str(r.get("MODULE", "")),
                str(r.get("BLOCK",  "")),
                str(r.get("REGION", "")),
            )
            die_key = f"{r.get('X')}_{r.get('Y')}"
            inst_fails.setdefault(inst_key, set()).add(die_key)

        if not inst_fails:
            return "–"

        # ── Step 4: Poisson yield product ─────────────────────────────────────
        y = 1.0
        for inst_key, failing_dies in inst_fails.items():
            p = len(failing_dies) / best_total_dies
            y *= (1.0 - p)

        return f"{y * 100:.2f}%"

    except Exception as e:
        _log(f"  WARNING: _yield_target_from_best_wafer: {e}")
        return "–"


def _parse_scan_summary(data_js_path: Path) -> dict:
    """Parse pipeline data.js and return scan summary metrics.

    Returns dict with keys:
        total_dies, lots, num_wafers,
        ff_pct    (FF  = IB bins 1+2 yield),
        ff_df_pct (FF+DF = IB bins 1+2+3+4 yield),
        top_ips [(ip, count, target_pct_or_none), ...], top_fails [(key, count), ...],
        ips_above_target [(ip, count, obs_pct, tgt_pct, delta_pct, modules), ...],
        total_fc (total fault count across all dies)
    """
    try:
        text = data_js_path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'const SCAN_DATA\s*=\s*', text)
        if not m:
            return {}
        decoder     = json.JSONDecoder()
        scan_data, _ = decoder.raw_decode(text, m.end())

        meta     = scan_data.get("meta", {})
        die_map  = scan_data.get("die_map", [])
        per_ip   = scan_data.get("per_ip", [])

        # ── Total dies ────────────────────────────────────────────────────────
        tdpw = meta.get("total_dies_per_wafer", {})
        total_dies   = sum(tdpw.values())
        num_wafers   = len(tdpw)
        lots         = sorted(set(k.split("|")[0] for k in tdpw))

        # ── FF / FF+DF: IB-based yield ────────────────────────────────────────
        # FF     = IB bins 1+2 (Fully Functional at-speed)
        # FF+DF  = IB bins 1+2+3+4 (FF + Design Functional)
        # Falls back to scan-test-type counts when IB data is absent.
        die_bins = scan_data.get("die_bins", {})
        if die_bins and total_dies > 0:
            ib_n: dict[str, int] = {}
            for wdict in die_bins.values():
                for ent in wdict.values():
                    ib = str(ent.get("ib", "")).strip()
                    if ib:
                        ib_n[ib] = ib_n.get(ib, 0) + 1
            ff_dies    = ib_n.get("1", 0) + ib_n.get("2", 0)
            ff_df_dies = ff_dies + ib_n.get("3", 0) + ib_n.get("4", 0)
            ff_pct     = ff_dies    / total_dies * 100
            ff_df_pct  = ff_df_dies / total_dies * 100
        else:
            # No IB data — fall back to scan test-type counts
            failing_cs  = sum(1 for d in die_map if d.get("CHAIN", 0) > 0 or d.get("STUCKAT", 0) > 0)
            failing_all = len(die_map)
            ff_pct    = (total_dies - failing_cs)  / total_dies * 100 if total_dies > 0 else 0.0
            ff_df_pct = (total_dies - failing_all) / total_dies * 100 if total_dies > 0 else 0.0

        # ── Top IP failures ───────────────────────────────────────────────────
        ip_counter: Counter = Counter()
        ip_module_counter: Counter = Counter()
        for r in per_ip:
            ip = (r.get("IP") or "").strip()
            if ip:
                ip_counter[ip] += 1
                mod = (r.get("MODULE") or "").strip()
                if mod:
                    ip_module_counter[(ip, mod)] += 1

        # ── Per-IP target from best wafer (good wafer reference) ─────────────
        ip_target_pct: dict[str, float] = {}
        try:
            if tdpw and die_map and per_ip:
                fail_per_wk: dict[str, int] = {}
                for d in die_map:
                    lot = str(d.get("LOT", ""))
                    wfr = d.get("WAFER")
                    if wfr is None:
                        continue
                    wk = f"{lot}|{int(wfr)}"
                    fail_per_wk[wk] = fail_per_wk.get(wk, 0) + 1

                best_wk = None
                best_fail_rate = float("inf")
                for wk, total in tdpw.items():
                    if total <= 0:
                        continue
                    rate = fail_per_wk.get(wk, 0) / total
                    if rate < best_fail_rate:
                        best_fail_rate = rate
                        best_wk = wk

                if best_wk and tdpw.get(best_wk, 0) > 0:
                    best_total_dies = tdpw[best_wk]
                    ip_fail_dies: dict[str, set[str]] = {}
                    for r in per_ip:
                        r_lot = str(r.get("LOT", ""))
                        r_wfr = r.get("WAFER")
                        if r_wfr is None:
                            continue
                        if f"{r_lot}|{int(r_wfr)}" != best_wk:
                            continue
                        ip = (r.get("IP") or "").strip()
                        if not ip:
                            continue
                        die_key = f"{r.get('X')}_{r.get('Y')}"
                        ip_fail_dies.setdefault(ip, set()).add(die_key)

                    ip_target_pct = {
                        ip: len(failing_dies) / best_total_dies * 100.0
                        for ip, failing_dies in ip_fail_dies.items()
                    }
        except Exception:
            ip_target_pct = {}

        # ── Top Module/Block/Region failures ─────────────────────────────────
        fail_counter: Counter = Counter()
        for r in per_ip:
            mod = (r.get("MODULE") or "").strip()
            blk = (r.get("BLOCK")  or "").strip()
            reg = (r.get("REGION") or "").strip()
            if mod:
                fail_counter[f"{mod}/{blk}/{reg}"] += 1

        # ── Total fault count (sum of per-die fail counts) ────────────────────
        total_fc = sum(
            d.get("CHAIN", 0) + d.get("STUCKAT", 0) +
            d.get("ATSPEED", 0) + d.get("DIAG", 0)
            for d in die_map
        )

        # ── Yield target: best-wafer Poisson model, CSV lookup as fallback ───────
        yield_target = _yield_target_from_best_wafer(scan_data)
        if yield_target == "–":
            yield_target = _lookup_yield_target(total_fc, _YIELD_TGT)

        ips_above_target: list[tuple[str, int, float, float, float, str]] = []
        if total_dies > 0:
            for ip, cnt in ip_counter.most_common():
                tgt = ip_target_pct.get(ip)
                if tgt is None:
                    continue
                obs = cnt / total_dies * 100.0
                if obs > tgt:
                    mods = [
                        (m, c) for (i, m), c in ip_module_counter.items()
                        if i == ip
                    ]
                    mods.sort(key=lambda x: x[1], reverse=True)
                    # Deduplicate module names (case-insensitive) while preserving rank order.
                    seen_mods: set[str] = set()
                    uniq_mods: list[str] = []
                    for m, _c in mods:
                        k = m.strip().upper()
                        if not k or k in seen_mods:
                            continue
                        seen_mods.add(k)
                        uniq_mods.append(m)
                        if len(uniq_mods) >= 3:
                            break
                    mods_str = ", ".join(uniq_mods) if uniq_mods else "-"
                    ips_above_target.append((ip, cnt, obs, tgt, obs - tgt, mods_str))

        return {
            "total_dies":   total_dies,
            "num_wafers":   num_wafers,
            "lots":         lots,
            "ff_pct":       ff_pct,
            "ff_df_pct":    ff_df_pct,
            "top_ips":      [
                (ip, cnt, ip_target_pct.get(ip))
                for ip, cnt in ip_counter.most_common(5)
            ],
            "top_fails":    fail_counter.most_common(5),
            "ips_above_target": ips_above_target,
            "total_fc":     total_fc,
            "yield_target": yield_target,
        }
    except Exception as e:
        _log(f"  WARNING: _parse_scan_summary: {e}")
        return {}


def _lookup_yield_target(fc: int, target_csv: Path) -> str:
    """Look up the nearest yield target % for the given total fault count."""
    if not target_csv.exists():
        return "–"
    try:
        rows = []
        with open(target_csv, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                # Column may have BOM prefix stripped by utf-8-sig
                fc_key  = next((k for k in row if "fault" in k.lower()), None)
                pct_key = next((k for k in row if "target" in k.lower()), None)
                if fc_key and pct_key:
                    try:
                        rows.append((int(row[fc_key]), float(row[pct_key])))
                    except Exception:
                        pass
        if not rows:
            return "–"
        rows.sort(key=lambda x: abs(x[0] - fc))
        return f"{rows[0][1]:.2f}%"
    except Exception:
        return "–"


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Watermark HTML outputs
# ─────────────────────────────────────────────────────────────────────────────

_WATERMARK_CSS  = """
<style id="_wm_style">
#_wm_badge {
  position: fixed; top: 8px; right: 14px; z-index: 99999;
  background: #6c3483; color: #ffffff;
  font: bold 11px/1.4 Arial, sans-serif;
  padding: 3px 10px; border-radius: 4px;
  letter-spacing: 0.3px; pointer-events: none; white-space: nowrap;
}
</style>
"""
_WATERMARK_HTML = '<div id="_wm_badge">Pant, Sujit N &mdash; GEMS FTE</div>'


def _inject_watermark(html_path: Path) -> None:
    try:
        text = html_path.read_text(encoding="utf-8", errors="replace")
        if "_wm_badge" in text:
            return
        if "</head>" in text:
            text = text.replace("</head>", _WATERMARK_CSS + "</head>", 1)
        else:
            text = _WATERMARK_CSS + text
        text = re.sub(
            r'(<body[^>]*>)',
            r'\1\n' + _WATERMARK_HTML,
            text, count=1, flags=re.IGNORECASE,
        )
        if _WATERMARK_HTML not in text:
            text = text + _WATERMARK_HTML
        html_path.write_text(text, encoding="utf-8")
    except Exception as e:
        _log(f"  watermark warning: {html_path.name}: {e}")


def _watermark_output_dir(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    html_files = list(output_dir.rglob("*.html"))
    _log(f"  Watermarking {len(html_files)} HTML file(s) in {output_dir.name}")
    for f in html_files:
        _inject_watermark(f)


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Build run report HTML
# ─────────────────────────────────────────────────────────────────────────────

_REPORT_CSS = """
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Segoe UI,Arial,sans-serif;background:#1a252f;color:#e8f0f7;padding:24px}
h1{color:#4fc3f7;font-size:1.3em;margin-bottom:14px;border-bottom:2px solid #4fc3f7;padding-bottom:8px}
h2{color:#4fc3f7;font-size:1.1em;margin:20px 0 10px}
.ts{color:#90a4ae;font-size:0.85em}
table{border-collapse:collapse;width:100%;margin-bottom:18px;font-size:0.9em}
th{background:#263950;color:#4fc3f7;padding:7px 12px;text-align:left;white-space:nowrap}
td{padding:5px 12px;border-bottom:1px solid #1e3a55;color:#cde;vertical-align:top}
tr:hover td{background:#1a3050}
.ok{color:#66bb6a;font-weight:bold}
.fail{color:#ef5350;font-weight:bold}
.pct-hi{color:#66bb6a;font-weight:bold}
.pct-lo{color:#ef5350;font-weight:bold}
.pct-ok{color:#ffa726;font-weight:bold}
.top-list{font-size:0.85em;line-height:1.7}
a{color:#4fc3f7}
a:hover{color:#80d8ff}
</style>
"""


def _build_run_report(
    run_dir: Path,
    run_ts: str,
    aqua_file: str,
    tp_results: list[tuple],   # (tp_key, ok, tp_output_dir, data_js_path)
    letter: str = "",
    base_dir: Path | None = None,
) -> Path | None:
    """Generate report.html in run_dir summarising this automation run."""

    def _pct_cls(v: float, hi: float = 95.0, lo: float = 85.0) -> str:
        return "pct-hi" if v >= hi else ("pct-lo" if v < lo else "pct-ok")

    summary_rows = ""
    for tp_key, ok, tp_output_dir, data_js_path in tp_results:
        smry = _parse_scan_summary(data_js_path) if data_js_path and data_js_path.exists() else {}

        op_m  = re.search(r'_(\d{5,6})$', tp_key)
        op    = op_m.group(1) if op_m else "?"

        die_s    = f"{smry.get('total_dies', 0):,}" if smry else "–"
        ff_v     = smry.get("ff_pct",    0.0)
        ff_df_v  = smry.get("ff_df_pct", 0.0)
        ff_s     = f"{ff_v:.2f}%"    if smry else "–"
        ff_df_s  = f"{ff_df_v:.2f}%" if smry else "–"
        ff_cls   = _pct_cls(ff_v)    if smry else "ts"
        ff_df_cls = _pct_cls(ff_df_v) if smry else "ts"

        # Top IP failures
        top_ips = smry.get("top_ips", [])
        ip_html = ""
        for ent in top_ips[:3]:
            ip = ent[0]
            cnt = ent[1]
            tgt = ent[2] if len(ent) > 2 else None
            pct = cnt / smry.get("total_dies", 1) * 100
            if tgt is None:
                ip_html += f"<div>{ip} <span class='ts'>(Obs {pct:.1f}%, {cnt:,})</span></div>"
            else:
                ip_html += f"<div>{ip} <span class='ts'>(Obs {pct:.1f}%, {cnt:,} | Tgt {tgt:.1f}%)</span></div>"
        ip_html = ip_html or "–"

        # Top Module/Block/Region failures
        top_fails = smry.get("top_fails", [])
        fail_html = ""
        for fail_key, cnt in top_fails[:3]:
            pct = cnt / smry.get("total_dies", 1) * 100
            fail_html += f"<div>{fail_key} <span class='ts'>({pct:.1f}%, {cnt:,})</span></div>"
        fail_html = fail_html or "–"

        above_target = smry.get("ips_above_target", [])
        above_html = ""
        for ip, _cnt, obs, tgt, delta, mods in above_target:
            above_html += (
                f"<div>{ip} <span class='ts'>(Obs {obs:.1f}% | Tgt {tgt:.1f}% | +{delta:.1f}% | Mod {mods})</span></div>"
            )
        above_html = above_html or "–"

        # Dashboard link
        index_html = tp_output_dir / "dashboard" / "index.html"
        if ok and index_html.exists():
            dash_link = f'<a href="{index_html.as_uri()}">&#128202; {tp_key}</a>'
        else:
            dash_link = f'<span class="ts">{tp_key}</span>'

        status_html = f'<span class="ok">&#10004; OK</span>' if ok else f'<span class="fail">&#10008; FAIL</span>'

        # Lots summary
        lots     = smry.get("lots", [])
        num_w    = smry.get("num_wafers", 0)
        lots_str = f"{len(lots)} lot(s), {num_w} wafer(s): {', '.join(lots[:4])}" if lots else "–"

        summary_rows += f"""
<tr>
  <td>{dash_link}</td>
  <td>{status_html}</td>
  <td class='ts'>{op}</td>
  <td>{die_s}</td>
  <td class='{ff_cls}'>{ff_s}</td>
  <td class='{ff_df_cls}'>{ff_df_s}</td>
  <td class='top-list'>{ip_html}</td>
  <td class='top-list'>{fail_html}</td>
    <td class='top-list'>{above_html}</td>
</tr>
<tr><td colspan='9' class='ts' style='padding:2px 12px 8px'>{lots_str}</td></tr>
"""

    title_str = (f"Scan Dashboard \u2014 NVL816-BLLC 0H61{letter.upper()} \u2014 {run_ts}"
                 if letter else f"Scan Dashboard \u2014 NVL816-BLLC \u2014 {run_ts}")

    # ── History section ───────────────────────────────────────────────────────
    history_html = ""
    if base_dir and run_dir:
        _tp_keys = [r[0] for r in tp_results]
        _hist    = _collect_history(base_dir, letter, run_dir, _tp_keys)
        if _hist:
            _hist_rows = ""
            for _run_label, _tp_summaries in _hist:
                for _tp_key, _smry, _idx_html in _tp_summaries:
                    if not _smry:
                        continue
                    _op_m  = re.search(r'_(\d{5,6})$', _tp_key)
                    _op    = _op_m.group(1) if _op_m else "?"
                    _die_s = f"{_smry.get('total_dies', 0):,}"
                    _ff_v  = _smry.get('ff_pct',    0.0)
                    _fdf_v = _smry.get('ff_df_pct', 0.0)
                    _ff_s  = f"{_ff_v:.2f}%"
                    _fdf_s = f"{_fdf_v:.2f}%"
                    _ff_c  = _pct_cls(_ff_v)
                    _fdf_c = _pct_cls(_fdf_v)
                    _top_d = _smry.get('total_dies', 1) or 1
                    _ip_h  = "".join(
                        (
                            f"<div>{ent[0]} <span class='ts'>(Obs {ent[1]/_top_d*100:.1f}%, {ent[1]:,})</span></div>"
                            if len(ent) <= 2 or ent[2] is None
                            else f"<div>{ent[0]} <span class='ts'>(Obs {ent[1]/_top_d*100:.1f}%, {ent[1]:,} | Tgt {ent[2]:.1f}%)</span></div>"
                        )
                        for ent in _smry.get('top_ips', [])[:3]
                    ) or "\u2013"
                    _fail_h = "".join(
                        f"<div>{k} <span class='ts'>({cnt/_top_d*100:.1f}%, {cnt:,})</span></div>"
                        for k, cnt in _smry.get('top_fails', [])[:3]
                    ) or "\u2013"
                    _above_h = "".join(
                        f"<div>{ip} <span class='ts'>(Obs {obs:.1f}% | Tgt {tgt:.1f}% | +{delta:.1f}% | Mod {mods})</span></div>"
                        for ip, _cnt, obs, tgt, delta, mods in _smry.get('ips_above_target', [])
                    ) or "\u2013"
                    _lots  = _smry.get('lots', [])
                    _nw    = _smry.get('num_wafers', 0)
                    _lot_s = (f"{', '.join(_lots[:3])}{'\u2026' if len(_lots)>3 else ''} ({_nw}W)"
                              if _lots else "\u2013")
                    _k_cell = (f'<a href="{_idx_html.as_uri()}" class="ts">{_tp_key}</a>'
                               if _idx_html and _idx_html.exists()
                               else f'<span class="ts">{_tp_key}</span>')
                    _hist_rows += f"""
<tr style="opacity:0.7">
  <td class='ts' colspan='2' style='padding:4px 12px'>{_run_label}</td>
  <td class='ts'>{_k_cell}</td>
  <td class='ts'>{_op}</td>
  <td class='ts'>{_die_s}</td>
  <td class='{_ff_c}' style='opacity:0.8'>{_ff_s}</td>
  <td class='{_fdf_c}' style='opacity:0.8'>{_fdf_s}</td>
  <td class='top-list ts'>{_ip_h}</td>
  <td class='top-list ts'>{_fail_h}</td>
    <td class='top-list ts'>{_above_h}</td>
</tr>
<tr style="opacity:0.7">
    <td colspan='10' class='ts' style='padding:1px 12px 8px'>{_lot_s}</td>
</tr>"""
            if _hist_rows:
                history_html = f"""
<h2>&#128337; Run History</h2>
<table>
<thead><tr>
  <th colspan='2'>Run</th><th>Dashboard</th><th>Op</th><th>Die</th>
  <th>FF (1+2)</th><th>FF+DF (1+2+3+4)</th>
    <th>Top IP Failures</th><th>Top Scan Failures</th><th>IPs Above Target</th>
</tr></thead>
<tbody>{_hist_rows}</tbody>
</table>"""

    html = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8">
<title>{title_str}</title>
{_REPORT_CSS}
</head><body>
<h1>&#128202; NVL816-BLLC Scan Dashboard &mdash; Run Report</h1>
<p class="ts">Generated: {run_ts} &nbsp;|&nbsp; AQUA: {Path(aqua_file).name}</p>
<table>
<thead><tr>
  <th>Dashboard</th><th>Status</th><th>Op</th><th>Die</th>
  <th>FF (1+2)</th><th>FF+DF (1+2+3+4)</th>
    <th>Top IP Failures</th><th>Top Scan Failures</th><th>IPs Above Target</th>
</tr></thead>
<tbody>{summary_rows}</tbody>
</table>
{history_html}
</body></html>
"""
    report_path = run_dir / "report.html"
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(html, encoding="utf-8")
        _log(f"  Report: {report_path}")
        return report_path
    except Exception as e:
        _log(f"  WARNING: could not write report: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Run log
# ─────────────────────────────────────────────────────────────────────────────

_RUN_LOG_CSS = """
<style>
body{font-family:Segoe UI,Arial;background:#1a252f;color:#e8f0f7;padding:16px}
h1{color:#4fc3f7;border-bottom:2px solid #4fc3f7;padding-bottom:8px;margin-bottom:10px}
h2{color:#4fc3f7;font-size:1em;margin:18px 0 6px}
table{border-collapse:collapse;width:100%;margin-bottom:8px;font-size:0.88em}
th{background:#263950;color:#4fc3f7;padding:5px 10px;text-align:left}
td{padding:4px 10px;border-bottom:1px solid #1e3a55;color:#cde}
.ts{color:#90a4ae;font-size:0.82em}
.ok{color:#66bb6a} .fail{color:#ef5350}
a{color:#4fc3f7}
</style>
"""

_RUN_LOG_HEADER = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Scan Dashboard — Run Log</title>
{css}
</head>
<body>
<h1>Scan Dashboard — Automation Run Log</h1>
<p class="ts">Auto-generated by run_automation.py &nbsp;|&nbsp;
Updated: <span id="ts">{ts}</span></p>
<!-- RUNS -->
""".format(css=_RUN_LOG_CSS, ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

_RUN_LOG_FOOTER = "\n</body>\n</html>\n"


def _make_run_section(
    run_ts: str,
    aqua_file: str,
    results: list[tuple],
    report_path: Path | None = None,
) -> str:
    rows_html = ""
    for r in results:
        tp_key, ok, output_dir = r[0], r[1], r[2]
        dash = Path(output_dir) / "dashboard" / "index.html"
        link   = f'<a href="{dash.as_uri()}">{tp_key}</a>' if dash.exists() else tp_key
        status = '<span class="ok">&#10004; OK</span>' if ok else '<span class="fail">&#10008; FAILED</span>'
        rows_html += f"<tr><td>{link}</td><td>{status}</td><td class='ts'>{output_dir}</td></tr>\n"

    report_link = ""
    if report_path and report_path.exists():
        report_link = (f' &nbsp;|&nbsp; '
                       f'<a href="{report_path.as_uri()}">&#128196; Report</a>')

    ops_str = ", ".join(r[0] for r in results)
    return f"""
<h2>Run: {run_ts} &mdash; op(s) updated: {ops_str}</h2>
<p class="ts">AQUA: {Path(aqua_file).name}{report_link}</p>
<table>
  <tr><th>TP Key</th><th>Status</th><th>Output</th></tr>
  {rows_html}
</table>
"""


def update_run_log(
    results: list[tuple],
    aqua_file: str,
    run_log: Path,
    dry_run: bool,
    report_path: Path | None = None,
) -> None:
    run_ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    section = _make_run_section(run_ts, aqua_file, results, report_path=report_path)

    if dry_run:
        _log(f"DRY-RUN: would prepend to {run_log}")
        return

    run_log.parent.mkdir(parents=True, exist_ok=True)

    if run_log.exists():
        existing = run_log.read_text(encoding="utf-8")
        if "<!-- RUNS -->" in existing:
            updated = existing.replace("<!-- RUNS -->", "<!-- RUNS -->\n" + section, 1)
        elif "</body>" in existing:
            updated = existing.replace("</body>", section + "\n</body>", 1)
        else:
            updated = existing + section
    else:
        updated = _RUN_LOG_HEADER + section + _RUN_LOG_FOOTER

    run_log.write_text(updated, encoding="utf-8")
    _log(f"Run log updated: {run_log}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 — Send email
# ─────────────────────────────────────────────────────────────────────────────

def _collect_history(
    base_dir: Path,
    letter: str,
    current_run_dir: Path,
    tp_keys: list[str],
    n_past: int = 5,
) -> list[tuple]:
    """Return [(run_label, [(tp_key, smry_or_None)]), ...] for previous runs, newest first.

    Collects history from ALL program letters (not just the current letter) so that
    e.g. the 0H61D email also shows recent 0H61C runs for context.
    """
    output_dir = base_dir / "output"
    if not output_dir.exists():
        return []

    # Collect all NVL_0H61* run dirs, excluding the current one
    try:
        all_dirs = [
            d for d in output_dir.iterdir()
            if d.is_dir()
            and re.search(r'^NVL_0H61[A-Za-z]_\d{8}_\d{6}$', d.name, re.IGNORECASE)
            and d.resolve() != current_run_dir.resolve()
        ]
    except OSError:
        return []
    past_dirs = sorted(all_dirs, key=lambda d: d.name, reverse=True)[:n_past]

    history: list[tuple] = []
    for rd in past_dirs:
        m = re.search(r'_(\d{8})_(\d{6})$', rd.name)
        lm = re.search(r'NVL_(0H61[A-Za-z])_', rd.name, re.IGNORECASE)
        run_letter = lm.group(1).upper() if lm else ""
        if m:
            d, t = m.group(1), m.group(2)
            run_label = f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}  [{run_letter}]"
        else:
            run_label = rd.name

        # For same-letter runs use the known tp_keys; for other-letter runs
        # discover TP subdirs from the run dir itself.
        if run_letter.endswith(letter.upper()):
            run_tp_keys = tp_keys
        else:
            run_tp_keys = sorted(
                sub.name for sub in rd.iterdir()
                if sub.is_dir() and not sub.name.startswith("run_config")
            )

        tp_summaries: list[tuple] = []
        for tp_key in run_tp_keys:
            try:
                dj = rd / tp_key / "dashboard" / "data.js"
                smry = _parse_scan_summary(dj) if dj.exists() else None
                index_html = rd / tp_key / "dashboard" / "index.html"
                tp_summaries.append((tp_key, smry, index_html if index_html.exists() else None))
            except OSError:
                tp_summaries.append((tp_key, None, None))
        if any(s for _, s, _ in tp_summaries):
            history.append((run_label, tp_summaries))
    return history


def _build_email_body(
    run_ts: str,
    aqua_file: str,
    tp_results: list[tuple],   # (tp_key, ok, tp_output_dir, data_js_path)
    run_log: Path | None,
    report_path: Path | None = None,
    base_dir: Path | None = None,
    current_run_dir: Path | None = None,
    letter: str = "",
) -> str:
    """Build HTML email body with scan summary table + run history."""

    def _pct_color(v: float, hi: float = 95.0, lo: float = 85.0) -> str:
        return "#66bb6a" if v >= hi else ("#ef5350" if v < lo else "#ffa726")

    rows_html = ""
    for tp_key, ok, tp_output_dir, data_js_path in sorted(
        tp_results, key=lambda r: r[0], reverse=True
    ):
        smry = _parse_scan_summary(data_js_path) if data_js_path and data_js_path.exists() else {}

        op_m = re.search(r'_(\d{5,6})$', tp_key)
        op   = op_m.group(1) if op_m else "?"

        die_s    = f"{smry.get('total_dies', 0):,}" if smry else "–"
        ff_v     = smry.get("ff_pct",    0.0)
        ff_df_v  = smry.get("ff_df_pct", 0.0)
        ff_s     = f"{ff_v:.2f}%"    if smry else "–"
        ff_df_s  = f"{ff_df_v:.2f}%" if smry else "–"
        ff_col   = _pct_color(ff_v)    if smry else "#90a4ae"
        ff_df_col = _pct_color(ff_df_v) if smry else "#90a4ae"

        top_ips   = smry.get("top_ips",   [])
        top_fails = smry.get("top_fails", [])
        top_dies  = smry.get("total_dies", 1) or 1

        ip_text = "; ".join(
            (
                f"{ent[0]} (Obs {ent[1]/top_dies*100:.1f}%, {ent[1]:,})"
                if len(ent) <= 2 or ent[2] is None
                else f"{ent[0]} (Obs {ent[1]/top_dies*100:.1f}%, {ent[1]:,} | Tgt {ent[2]:.1f}%)"
            )
            for ent in top_ips[:3]
        ) or "–"

        fail_text = "; ".join(
            f"{k} ({cnt/top_dies*100:.1f}%, {cnt:,})"
            for k, cnt in top_fails[:3]
        ) or "–"

        above_target = smry.get("ips_above_target", [])
        above_text = "; ".join(
            f"{ip} (Obs {obs:.1f}% | Tgt {tgt:.1f}% | +{delta:.1f}% | Mod {mods})"
            for ip, _cnt, obs, tgt, delta, mods in above_target
        ) or "–"

        index_html = Path(tp_output_dir) / "dashboard" / "index.html"
        if ok and index_html.exists():
            key_cell = (f'<a href="{index_html.as_uri()}" '
                        f'style="color:#ffffff;font-weight:bold;text-decoration:none">'
                        f'{tp_key}</a>')
        else:
            key_cell = f'<span style="color:#ffffff;font-weight:bold">{tp_key}</span>'

        st_color = "#66bb6a" if ok else "#ef5350"
        st_text  = "&#10004; OK" if ok else "&#10008; FAIL"

        lots    = smry.get("lots", [])
        num_w   = smry.get("num_wafers", 0)
        lot_str = f"{', '.join(lots[:3])}{'…' if len(lots) > 3 else ''} ({num_w}W)" if lots else "–"

        rows_html += f"""
<tr>
  <td style="background:#263950;padding:6px 12px">{key_cell}</td>
  <td style="color:{st_color};font-weight:bold">{st_text}</td>
  <td style="color:#90a4ae;font-size:0.9em">{op}</td>
  <td style="color:#cde">{die_s}</td>
  <td style="color:{ff_col};font-weight:bold">{ff_s}</td>
  <td style="color:{ff_df_col};font-weight:bold">{ff_df_s}</td>
  <td style="color:#cde;font-size:0.88em">{ip_text}</td>
  <td style="color:#cde;font-size:0.88em">{fail_text}</td>
    <td style="color:#cde;font-size:0.88em">{above_text}</td>
</tr>
<tr>
    <td colspan="9" style="color:#546e7a;font-size:0.82em;padding:1px 12px 8px">{lot_str}</td>
</tr>"""

    report_link = ""
    if report_path and report_path.exists():
        report_link = (f'<p><a href="{report_path.as_uri()}" '
                       f'style="color:#4fc3f7">&#128196; Full Report</a></p>')

    overall = "OK" if all(r[1] for r in tp_results) else "FAILED"

    # ── History section ───────────────────────────────────────────────────────
    history_html = ""
    if base_dir and current_run_dir and letter:
        tp_keys = [r[0] for r in tp_results]
        history = _collect_history(base_dir, letter, current_run_dir, tp_keys)
        if history:
            hist_rows = ""
            for run_label, tp_summaries in history:
                for tp_key, smry, index_html in tp_summaries:
                    if not smry:
                        continue
                    op_m = re.search(r'_(\d{5,6})$', tp_key)
                    op   = op_m.group(1) if op_m else "?"
                    die_s   = f"{smry.get('total_dies', 0):,}"
                    ff_v    = smry.get('ff_pct',    0.0)
                    ff_df_v = smry.get('ff_df_pct', 0.0)
                    ff_s    = f"{ff_v:.2f}%"
                    ff_df_s = f"{ff_df_v:.2f}%"
                    top_dies = smry.get('total_dies', 1) or 1
                    ip_text = "; ".join(
                        (
                            f"{ent[0]} (Obs {ent[1]/top_dies*100:.1f}%, {ent[1]:,})"
                            if len(ent) <= 2 or ent[2] is None
                            else f"{ent[0]} (Obs {ent[1]/top_dies*100:.1f}%, {ent[1]:,} | Tgt {ent[2]:.1f}%)"
                        )
                        for ent in smry.get('top_ips', [])[:3]
                    ) or "–"
                    fail_text = "; ".join(
                        f"{k} ({cnt/top_dies*100:.1f}%, {cnt:,})"
                        for k, cnt in smry.get('top_fails', [])[:3]
                    ) or "–"
                    above_text = "; ".join(
                        f"{ip} (Obs {obs:.1f}% | Tgt {tgt:.1f}% | +{delta:.1f}% | Mod {mods})"
                        for ip, _cnt, obs, tgt, delta, mods in smry.get('ips_above_target', [])
                    ) or "–"
                    lots   = smry.get('lots', [])
                    num_w  = smry.get('num_wafers', 0)
                    lot_str = f"{', '.join(lots[:3])}{'…' if len(lots)>3 else ''} ({num_w}W)" if lots else "–"
                    if index_html:
                        key_cell = (f'<a href="{index_html.as_uri()}" '
                                    f'style="color:#90a4ae;text-decoration:none">{tp_key}</a>')
                    else:
                        key_cell = tp_key
                    hist_rows += f"""
<tr style="opacity:0.65">
  <td style="color:#546e7a;font-size:0.82em;padding:4px 12px" colspan="2">{run_label}</td>
  <td style="color:#90a4ae;padding:4px 8px">{key_cell}</td>
  <td style="color:#90a4ae;font-size:0.9em;padding:4px 8px">{op}</td>
  <td style="color:#90a4ae;padding:4px 8px">{die_s}</td>
  <td style="color:#90a4ae;padding:4px 8px">{ff_s}</td>
  <td style="color:#90a4ae;padding:4px 8px">{ff_df_s}</td>
  <td style="color:#90a4ae;font-size:0.85em;padding:4px 8px">{ip_text}</td>
  <td style="color:#90a4ae;font-size:0.85em;padding:4px 8px">{fail_text}</td>
    <td style="color:#90a4ae;font-size:0.85em;padding:4px 8px">{above_text}</td>
</tr>
<tr style="opacity:0.65">
    <td colspan="10" style="color:#3d5a6e;font-size:0.78em;padding:1px 12px 6px">{lot_str}</td>
</tr>"""
            if hist_rows:
                history_html = f"""
<h3 style="color:#546e7a;font-size:0.9em;margin:20px 0 6px">&#128337; Run History</h3>
<table border="0" cellpadding="4" cellspacing="0"
       style="border-collapse:collapse;width:100%;font-size:0.88em;
              background:#151f2b;border-radius:4px">
  <tr style="background:#1a2a3a">
    <th style="color:#546e7a;padding:5px 12px;text-align:left" colspan="2">Date</th>
    <th style="color:#546e7a;text-align:left">Run</th>
    <th style="color:#546e7a">Op</th>
    <th style="color:#546e7a">Die</th>
    <th style="color:#546e7a">FF(1+2)</th>
    <th style="color:#546e7a">FF+DF(1+2+3+4)</th>
    <th style="color:#546e7a;text-align:left">Top IP Fail</th>
    <th style="color:#546e7a;text-align:left">Top Scan Fail</th>
        <th style="color:#546e7a;text-align:left">IPs Above Target</th>
  </tr>
  {hist_rows}
</table>"""

    return f"""
<html>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#1a252f;color:#e8f0f7;
             padding:24px;max-width:900px">
<h2 style="color:#4fc3f7;margin-bottom:4px">
  &#128202; NVL816-BLLC Scan Dashboard &mdash; {overall}
</h2>
<p style="color:#90a4ae;font-size:0.88em;margin-top:0">{run_ts}</p>
{report_link}
<table border="0" cellpadding="6" cellspacing="0"
       style="border-collapse:collapse;width:100%;font-size:0.9em;
              background:#1e2e3d;border-radius:6px">
  <tr style="background:#263950">
    <th style="color:#4fc3f7;padding:8px 12px;text-align:left">Run</th>
    <th style="color:#4fc3f7">Status</th>
    <th style="color:#4fc3f7">Op</th>
    <th style="color:#4fc3f7">Die</th>
    <th style="color:#4fc3f7">FF(1+2)</th>
    <th style="color:#4fc3f7">FF+DF(1+2+3+4)</th>
    <th style="color:#4fc3f7;text-align:left">Top IP Fail</th>
    <th style="color:#4fc3f7;text-align:left">Top Scan Fail</th>
        <th style="color:#4fc3f7;text-align:left">IPs Above Target</th>
  </tr>
  {rows_html}
</table>
<p style="color:#546e7a;font-size:0.8em;margin-top:12px">
  AQUA: {aqua_file}<br>
  Full history: <a href="{run_log.as_uri() if run_log else '#'}" style="color:#4fc3f7">run_log.html</a>
</p>
{history_html}
<hr style="border:1px solid #263950;margin-top:16px"/>
<p style="color:#546e7a;font-size:0.8em">Pant, Sujit N &mdash; GEMS FTE</p>
</body>
</html>
"""


def _send_via_outlook(to: str, subject: str, body_html: str,
                      attachments: list[str]) -> None:
    import win32com.client as _w
    _ol = _w.Dispatch("Outlook.Application")
    _m  = _ol.CreateItem(0)
    _m.To       = to
    _m.Subject  = subject
    _m.HTMLBody = body_html
    for att in attachments:
        if Path(att).exists():
            _m.Attachments.Add(att)
            _log(f"  Attaching : {Path(att).name}")
    try:
        _m.Send()
    except Exception as e:
        _log(f"  Outlook COM: Send() raised {e!r} — email likely dispatched.")
    _log("  Email sent via Outlook COM.")


def _send_via_smtp(to: str, subject: str, body_html: str,
                   attachments: list[str]) -> None:
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text      import MIMEText
    from email.mime.base      import MIMEBase
    from email                import encoders

    msg           = MIMEMultipart("mixed")
    msg["From"]   = _SMTP_FROM
    msg["To"]     = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    for att in attachments:
        p = Path(att)
        if p.exists():
            part = MIMEBase("application", "octet-stream")
            part.set_payload(p.read_bytes())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=p.name)
            msg.attach(part)
            _log(f"  Attaching : {p.name}")
    with smtplib.SMTP(_SMTP_SERVER, _SMTP_PORT, timeout=30) as s:
        s.starttls()
        s.sendmail(_SMTP_FROM, [a.strip() for a in to.split(";")], msg.as_string())
    _log(f"  Email sent via SMTP ({_SMTP_SERVER}).")


def _build_email_report_html(output_dir: Path, run_ts: str,
                              excluded_keys: list | None = None) -> str:
    """Build self-contained sidebar+history HTML for scan email reports.

    Tabs: Summary (latest per program) | 0H61A | 0H61B | ...
    Columns: Run Date | Op | Dies | Wafers | FF% | FF+DF% | Yield Tgt | Total FC | Top IPs
    """
    from collections import defaultdict

    _excluded = set(excluded_keys or [])

    run_pattern = re.compile(r'^NVL_0H(\d+)([A-Za-z])_(\d{8}_\d{6})$')
    tp_pattern  = re.compile(r'(?:0H)?(\d+)([A-Za-z]).*?_(\d{5,6})$')
    history: dict[str, list[dict]] = defaultdict(list)

    for rd in sorted(output_dir.iterdir()):
        if not rd.is_dir():
            continue
        m = run_pattern.match(rd.name)
        if not m:
            continue
        gen, letter, ts = m.group(1), m.group(2).upper(), m.group(3)
        prog_key = f"{gen}{letter}"
        dt_str = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}"
        for tp_dir in sorted(rd.iterdir()):
            if not tp_dir.is_dir():
                continue
            if tp_dir.name in _excluded:
                continue
            tm = tp_pattern.search(tp_dir.name)
            if not tm or tm.group(1) != gen or tm.group(2).upper() != letter:
                continue
            data_js = tp_dir / "dashboard" / "data.js"
            sm = _parse_scan_summary(data_js) if data_js.exists() else {}
            history[prog_key].append({
                "ts":     ts,
                "dt_str": dt_str,
                "op":     tm.group(3),
                "tp_key": tp_dir.name,
                "tp_dir": tp_dir,
                "sm":     sm,
            })

    for k in history:
        history[k].sort(key=lambda x: x["ts"], reverse=True)

    sorted_keys = sorted(
        history.keys(),
        key=lambda k: (int(k[:-1]), k[-1]),
        reverse=True,
    )

    def _idx_uri(entry):
        idx = entry["tp_dir"] / "dashboard" / "index.html"
        return idx.as_uri() if idx.exists() else ""

    def _fmt_pct(v):
        try:
            return f"{float(v):.1f}%"
        except Exception:
            return "–"

    def _ff_color(v):
        try:
            f = float(v)
            return "#66bb6a" if f >= 60 else "#ffa726" if f >= 40 else "#ef5350"
        except Exception:
            return "#90a4ae"

    def _top_ips_html(sm):
        tips = sm.get("top_ips", [])
        total = sm.get("total_dies", 0) or 1
        if not tips:
            return "–"
        lines = []
        for ip, cnt, tgt in tips[:5]:
            obs_pct = cnt / total * 100
            tgt_str = f" | Tgt {tgt:.1f}%" if tgt is not None else ""
            lines.append(f"{ip} (Obs {obs_pct:.1f}%, {cnt:,}{tgt_str})")
        return "<br>".join(lines)

    def _top_fails_html(sm):
        fails = sm.get("top_fails", [])
        total = sm.get("total_dies", 0) or 1
        if not fails:
            return "–"
        lines = []
        for key, cnt in fails[:5]:
            pct = cnt / total * 100
            lines.append(f"{key} ({pct:.1f}%, {cnt:,})")
        return "<br>".join(lines)

    def _ips_above_tgt_html(sm):
        iat = sm.get("ips_above_target", [])
        if not iat:
            return "–"
        lines = []
        for ip, cnt, obs, tgt, delta, mods in iat[:13]:
            lines.append(
                f"{ip} (Obs {obs:.1f}% | Tgt {tgt:.1f}% | +{delta:.1f}% | Mod {mods})"
            )
        return "<br>".join(lines)

    COL_HDR = (
        "<th>Run Date</th>"
        "<th>Op</th>"
        "<th>Die</th>"
        "<th>FF<br><small>(1+2)</small></th>"
        "<th>FF+DF<br><small>(1+2+3+4)</small></th>"
        "<th>Top IP Failures</th>"
        "<th>Top Scan Failures</th>"
        "<th>IPs Above Target</th>"
    )

    def _data_row(entry, is_latest=False, prog_prefix=""):
        sm   = entry["sm"]
        ff   = _fmt_pct(sm.get("ff_pct", ""))
        ffdf = _fmt_pct(sm.get("ff_df_pct", ""))
        dies = f"{sm.get('total_dies', 0):,}" if sm.get("total_dies") else "–"
        tips = _top_ips_html(sm)
        tfails = _top_fails_html(sm)
        iat  = _ips_above_tgt_html(sm)
        link = _idx_uri(entry)
        date_cell = (f'<a href="{link}" class="tl">{entry["dt_str"]}</a>'
                     if link else entry["dt_str"])
        if is_latest:
            date_cell += ' <span class="latest-badge">latest</span>'
        row_cls = ' class="latest-row"' if is_latest else ""
        return (
            f'<tr{row_cls}>'
            f'{prog_prefix}'
            f'<td class="c-date">{date_cell}</td>'
            f'<td class="c-op mono">{entry["op"]}</td>'
            f'<td class="c-num">{dies}</td>'
            f'<td class="c-num" style="color:{_ff_color(sm.get("ff_pct",""))};font-weight:bold">{ff}</td>'
            f'<td class="c-num" style="color:{_ff_color(sm.get("ff_df_pct",""))};font-weight:bold">{ffdf}</td>'
            f'<td class="c-detail">{tips}</td>'
            f'<td class="c-detail">{tfails}</td>'
            f'<td class="c-detail c-iat">{iat}</td>'
            f'</tr>\n'
        )

    # ── Summary panel ─────────────────────────────────────────────────────────
    sum_rows = ""
    for k in sorted_keys:
        if not history[k]:
            continue
        e    = history[k][0]
        link = _idx_uri(e)
        prog_cell = (
            f'<td class="c-prog"><a href="{link}" class="tl">'
            f'<span class="prog-pill">0H{k}</span></a></td>'
            if link else
            f'<td class="c-prog"><span class="prog-pill">0H{k}</span></td>'
        )
        sum_rows += _data_row(e, prog_prefix=prog_cell)

    summary_panel = (
        f'<div id="panel-summary" class="panel active">\n'
        f'  <h2 class="panel-hdr">&#128202; Scan Summary \u2014 Latest Run per Program</h2>\n'
        f'  <p class="panel-sub">Generated: {run_ts}</p>\n'
        f'  <div class="tbl-wrap">\n'
        f'  <table class="data-tbl">\n'
        f'    <thead><tr><th>Program</th>{COL_HDR}</tr></thead>\n'
        f'    <tbody>{sum_rows}</tbody>\n'
        f'  </table>\n'
        f'  </div>\n'
        f'</div>'
    )

    # ── Per-program panels ────────────────────────────────────────────────────
    prog_panels = ""
    for k in sorted_keys:
        entries = history[k]
        if not entries:
            continue
        def _prog_cell(e, ltr):
            link = _idx_uri(e)
            pill = f'<span class="prog-pill">0H{ltr}</span>'
            return (
                f'<td class="c-prog"><a href="{link}" class="tl">{pill}</a></td>'
                if link else f'<td class="c-prog">{pill}</td>'
            )
        hist_rows = "".join(
            _data_row(e, i == 0, prog_prefix=_prog_cell(e, k))
            for i, e in enumerate(entries)
        )
        latest_ff = _fmt_pct(entries[0]["sm"].get("ff_pct", ""))
        try:
            badge_col = _ff_color(entries[0]["sm"].get("ff_pct", ""))
        except Exception:
            badge_col = "#90a4ae"
        prog_panels += (
            f'<div id="panel-{k}" class="panel">\n'
            f'  <h2 class="panel-hdr">\n'
            f'    <span class="prog-pill">0H{k}</span>\n'
            f'    <span class="yld-badge" style="background:{badge_col}">{latest_ff} FF</span>\n'
            f'    <span class="panel-sub-inline">{len(entries)} run{"s" if len(entries)!=1 else ""}</span>\n'
            f'  </h2>\n'
            f'  <div class="tbl-wrap">\n'
            f'  <table class="data-tbl">\n'
            f'    <thead><tr><th>Program</th>{COL_HDR}</tr></thead>\n'
            f'    <tbody>{hist_rows}</tbody>\n'
            f'  </table>\n'
            f'  </div>\n'
            f'</div>\n'
        )

    # ── Sidebar ───────────────────────────────────────────────────────────────
    sb = '<li><button class="tab-btn active" data-panel="summary">&#128202;&nbsp;Summary</button></li>\n'
    for k in sorted_keys:
        if not history[k]:
            continue
        ff = _fmt_pct(history[k][0]["sm"].get("ff_pct", ""))
        n  = len(history[k])
        sb += (
            f'<li><button class="tab-btn" data-panel="{k}">'
            f'<span class="nav-prog">0H{k}</span>'
            f'<span class="nav-meta">{n} run{"s" if n!=1 else ""} &bull; FF: {ff}</span>'
            f'</button></li>\n'
        )

    CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: Segoe UI, Arial, sans-serif; font-size: 15px;
  background: #0f1923; color: #dce9f5; display: flex; min-height: 100vh;
}
#sidebar {
  width: 240px; flex-shrink: 0; background: #141f2b;
  border-right: 1px solid #1e3048; position: sticky; top: 0;
  height: 100vh; overflow-y: auto; padding: 0 0 24px;
}
#sb-hdr { background: #0f1923; border-bottom: 1px solid #1e3048; padding: 14px 16px 10px; }
#sb-hdr h3 { color: #4fc3f7; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }
#sb-hdr p  { color: #546e7a; font-size: 12px; margin-top: 3px; }
#sidebar ul { list-style: none; padding: 6px 0; }
.tab-btn {
  width: 100%; background: none; border: none; border-left: 3px solid transparent;
  cursor: pointer; text-align: left; padding: 10px 14px; color: #78909c;
  display: flex; flex-direction: column; gap: 3px; font-size: 15px;
  transition: background .15s, color .15s;
}
.tab-btn:hover { background: #1a2f45; color: #dce9f5; border-left-color: #546e7a; }
.tab-btn.active { background: #1a3a55; color: #4fc3f7; border-left-color: #4fc3f7; font-weight: bold; }
.nav-prog { font-size: 15px; }
.nav-meta { font-size: 12px; color: #546e7a; }
#main { flex: 1; padding: 22px 28px 60px; overflow-x: auto; min-width: 0; }
.panel { display: none; }
.panel.active { display: block; }
.panel-hdr {
  font-size: 18px; color: #4fc3f7;
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding-bottom: 8px; border-bottom: 2px solid #1e3048; margin-bottom: 6px;
}
.panel-sub { color: #546e7a; font-size: 13px; margin-bottom: 14px; margin-top: 4px; }
.panel-sub-inline { color: #546e7a; font-size: 13px; font-weight: normal; }
.prog-pill { background: #1a3a55; color: #80cbc4; border-radius: 4px; padding: 2px 10px; font-family: monospace; font-size: 16px; }
.yld-badge { color: #fff; border-radius: 12px; padding: 2px 10px; font-size: 14px; font-weight: bold; }
.latest-badge { background: #4fc3f7; color: #0f1923; border-radius: 8px; padding: 1px 6px; font-size: 11px; font-weight: bold; margin-left: 4px; vertical-align: middle; }
.tbl-wrap { overflow-x: auto; margin-top: 6px; }
.data-tbl { border-collapse: collapse; width: 100%; font-size: 14px; min-width: 1100px; }
.data-tbl th {
  background: #1a3a55; color: #4fc3f7; padding: 8px 12px; text-align: left;
  border-bottom: 2px solid #0f1923; font-size: 13px; white-space: nowrap;
}
.data-tbl th small { color: #607d8b; font-weight: normal; display: block; font-size: 12px; }
.data-tbl td { padding: 6px 12px; border-bottom: 1px solid #1a2f45; vertical-align: middle; text-align: left; }
.data-tbl tr:hover td { background: #14253a; }
.latest-row td { background: #0f2233 !important; }
.c-date { white-space: nowrap; color: #90a4ae; }
.c-prog { }
.c-op   { white-space: nowrap; color: #80cbc4; }
.c-num  { white-space: nowrap; }
.c-detail { font-size: 12px; color: #b0bec5; line-height: 1.6; min-width: 200px; }
.c-iat  { color: #ffcc80; }
.mono   { font-family: monospace; font-size: 13px; }
.tl     { color: #4fc3f7; text-decoration: none; }
.tl:hover { text-decoration: underline; }
"""

    JS = """
(function(){
  var btns   = document.querySelectorAll('.tab-btn');
  var panels = document.querySelectorAll('.panel');
  btns.forEach(function(b){
    b.addEventListener('click', function(){
      btns.forEach(function(x){x.classList.remove('active');});
      panels.forEach(function(p){p.classList.remove('active');});
      b.classList.add('active');
      var p = document.getElementById('panel-'+b.dataset.panel);
      if(p) p.classList.add('active');
    });
  });
})();
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>NVL816-BLLC Scan Report \u2014 {run_ts}</title>
<style>{CSS}</style>
</head>
<body>
<nav id="sidebar">
  <div id="sb-hdr">
    <h3>NVL816 Scan</h3>
    <p>{run_ts}</p>
  </div>
  <ul>{sb}</ul>
</nav>
<div id="main">
  {summary_panel}
  {prog_panels}
</div>
<script>{JS}</script>
</body>
</html>"""


def send_email(
    to: str,
    subject: str,
    body_html: str,
    dry_run: bool,
    attachments: list[str] | None = None,
) -> None:
    _log(f"{'DRY-RUN: ' if dry_run else ''}Sending email → {to}")
    if dry_run:
        _log(f"  Subject   : {subject}")
        return

    atts = attachments or []
    try:
        _send_via_outlook(to, subject, body_html, atts)
        return
    except ImportError:
        _log("  win32com not available — falling back to SMTP.")
    except Exception as e:
        _log(f"  Outlook COM failed ({e}) — falling back to SMTP.")

    try:
        _send_via_smtp(to, subject, body_html, atts)
    except Exception as e:
        _log(f"  ERROR sending email via SMTP: {e}")


def _send_no_new_data_email(base_dir: Path, args) -> None:
    ecfg: dict = {}
    if _EMAIL_CFG.exists():
        try:
            ecfg = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
        except Exception:
            pass
    to = (ecfg.get("email_to_report")
          or ecfg.get("email_to")
          or getattr(args, "email", _EMAIL_TO)
          or _EMAIL_TO)

    last_report_link = ""
    out_dir = base_dir / "output"
    if out_dir.exists():
        runs = sorted(out_dir.iterdir(), reverse=True)
        for r in runs:
            rpt = r / "report.html"
            if rpt.exists():
                last_report_link = (
                    f'<p>Last report: <a href="{rpt}">{rpt.name}</a> '
                    f'(from run <code>{r.name}</code>)</p>'
                )
                break

    run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = f"""<!DOCTYPE html><html><body style="font-family:sans-serif">
<h2 style="color:#4fc3f7">NVL Scan Dashboard — No New Data</h2>
<p>Run at <strong>{run_ts}</strong>: AQUA pull completed but no new lot/wafer data
was detected since the last run. Pipeline was not re-executed.</p>
{last_report_link}
<hr/><p style="font-size:0.85em;color:#888">Pant, Sujit N — GEMS FTE</p>
</body></html>"""

    send_email(to=to, subject="NVL816-BLLC Scan Dashboard",
               body_html=body, dry_run=getattr(args, "dry_run", False))


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cleanup_old_runs(base_dir: Path, letter: str, keep: int = 10, dry_run: bool = False) -> None:
    """Delete oldest run dirs for a letter, keeping the most recent `keep` runs.
    Tagged runs (.tag file) are always preserved regardless of position.
    """
    output_dir = base_dir / "output"
    if not output_dir.exists():
        return
    pattern = f"NVL_0H61{letter}_"
    try:
        all_dirs = sorted(
            [d for d in output_dir.iterdir()
             if d.is_dir() and d.name.upper().startswith(pattern.upper())],
            key=lambda d: d.name, reverse=True,   # newest first
        )
    except OSError:
        return
    tagged   = [d for d in all_dirs if (d / ".tag").exists()]
    untagged = [d for d in all_dirs if not (d / ".tag").exists()]
    to_delete = untagged[keep:]                # keep newest `keep` untagged; delete the rest
    if not to_delete:
        return
    _log(f"  Cleanup 0H61{letter}: keeping {min(keep, len(untagged))} run(s), "
         f"removing {len(to_delete)} old run(s)  "
         f"({len(tagged)} tagged run(s) preserved)")
    for d in to_delete:
        if dry_run:
            _log(f"    DRY-RUN: would delete {d.name}")
            continue
        try:
            shutil.rmtree(d)
            _log(f"    Deleted: {d.name}")
        except Exception as e:
            _log(f"    WARNING: could not delete {d.name}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Auto-pull AQUA + split by TP/op + run scan dashboards + email.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--aqua-exe",      default=_AQUA_EXE_AMR)
    ap.add_argument("--report-config", default=str(_AQUA_CFG))
    ap.add_argument("--base-dir",      default=str(_BASE_DIR))
    ap.add_argument("--days",          type=int, default=_DEFAULT_DAYS)
    ap.add_argument("--local-csv",     default=None,
                    help="Skip AQUA pull; use this existing CSV/7z/zip (glob ok)")
    ap.add_argument("--keys",          default=None,
                    help="Comma-separated key substrings to filter (e.g. '0H61C,119325')")
    ap.add_argument("--force",         action="store_true",
                    help="Rerun even if data unchanged")
    ap.add_argument("--dry-run",       action="store_true")
    ap.add_argument("--email",         default=_EMAIL_TO)
    ap.add_argument("--keep-runs",     type=int, default=None, metavar="N",
                    help="Keep the N most-recent output run folders per program letter "
                         "after this run; older folders are deleted automatically. "
                         "0 = disabled. Reads from email_config.json (keep_runs) "
                         "when not set; default in config is 10.")
    args = ap.parse_args()

    base_dir = Path(args.base_dir)
    data_dir = base_dir / "data"
    run_log  = base_dir / "run_log.html"
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")

    _log("=" * 65)
    _log(f"scan run_automation  [{'DRY-RUN' if args.dry_run else 'LIVE'}]")
    _log(f"Base dir  : {base_dir}")
    _log(f"Pipeline  : {_PIPELINE}")
    _log("=" * 65)

    # ── 1. Get AQUA / local scan data ─────────────────────────────────────────
    _local_7z_tmpdir = None
    if args.local_csv:
        import glob as _glob
        _matches = sorted(_glob.glob(args.local_csv), key=os.path.getmtime)
        if _matches:
            aqua_file = Path(_matches[-1])
            _log(f"Local CSV: {aqua_file}  ({len(_matches)} match(es))")
        elif '*' in args.local_csv or '?' in args.local_csv:
            _log(f"ERROR: no files matched glob: {args.local_csv!r}")
            sys.exit(1)
        else:
            aqua_file = Path(args.local_csv)
            _log(f"Local CSV: {aqua_file}")

        # If a .7z archive, extract to temp dir first
        if aqua_file.suffix.lower() == ".7z":
            _local_7z_tmpdir = tempfile.TemporaryDirectory(prefix="scan_auto_local_")
            _7z_out = Path(_local_7z_tmpdir.name)
            _log(f"  Extracting {aqua_file.name} → {_7z_out}")
            try:
                subprocess.run(
                    [str(_7Z_EXE), "e", str(aqua_file), f"-o{_7z_out}", "-y"],
                    check=True, capture_output=True,
                )
            except Exception as e:
                _log(f"  ERROR extracting: {e}")
                sys.exit(1)
            _extracted = None
            for _pat in ("*.csv", "*.CSV", "*.csv.gz"):
                _hits = sorted(_7z_out.glob(_pat), key=lambda p: p.stat().st_size, reverse=True)
                if _hits:
                    _extracted = _hits[0]
                    break
            if _extracted is None:
                _log("  ERROR: no CSV found inside archive")
                sys.exit(1)
            _log(f"  Extracted: {_extracted.name}  ({_extracted.stat().st_size:,} bytes)")
            aqua_file = _extracted
    else:
        aqua_file = pull_aqua(
            aqua_exe=args.aqua_exe,
            report_config=Path(args.report_config),
            data_dir=data_dir,
            dry_run=args.dry_run,
        )
        if aqua_file is None:
            _log("AQUA pull failed — aborting.")
            ecfg = {}
            if _EMAIL_CFG.exists():
                try: ecfg = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
                except Exception: pass
            err_to = ecfg.get("email_to_alert", ecfg.get("email_to_report", args.email)) or args.email
            send_email(
                to=err_to,
                subject="NVL816-BLLC Scan Dashboard",
                body_html="<p>AQUA pull failed. Check automation logs.</p>",
                dry_run=args.dry_run,
            )
            sys.exit(1)

    # ── 2. Split by (TestProgram, Operation) ──────────────────────────────────
    _log(f"\nReading: {aqua_file}")
    new_rows, _ = _read_aqua_file(aqua_file)
    _log(f"  {len(new_rows):,} rows")

    groups = split_by_tp_oper(new_rows)
    if not groups and not args.dry_run:
        _log("No groups found — nothing to run.")
        sys.exit(0)

    # ── 2a. Per-program-letter raw snapshots ──────────────────────────────────
    _ts_match = re.search(r'(\d{8}_\d{6})', Path(aqua_file).stem)
    _raw_ts   = _ts_match.group(1) if _ts_match else datetime.now().strftime("%Y%m%d_%H%M%S")

    _letter_rows: dict[str, tuple[list[dict], list[str]]] = {}
    for _key, (_krows, _khdrs) in groups.items():
        _m = re.search(r'[0-9A-Za-z]H61([A-Za-z])', _key)
        _letter = f"0H61{_m.group(1).upper()}" if _m else "0H61X"
        if _letter not in _letter_rows:
            _letter_rows[_letter] = ([], list(_khdrs))
        _lrows, _lhdrs = _letter_rows[_letter]
        _lrows.extend(_krows)
        for _h in _khdrs:
            if _h not in _lhdrs:
                _lhdrs.append(_h)

    _log("\nDistributing raw AQUA data to per-program folders…")
    for _letter, (_lrows, _lhdrs) in sorted(_letter_rows.items()):
        _letter_dir = data_dir / "programs" / _letter
        _raw_dest   = _letter_dir / f"raw_{_raw_ts}.csv.gz"
        _raw_z7     = _letter_dir / f"raw_{_raw_ts}.7z"
        if not args.dry_run:
            _letter_dir.mkdir(parents=True, exist_ok=True)
            if _raw_dest.exists() or _raw_z7.exists():
                _log(f"  {_letter}/raw_{_raw_ts}.*  already exists — skipping")
            else:
                _write_gz(_lrows, _lhdrs, _raw_dest)
                _log(f"  {_letter}/raw_{_raw_ts}.csv.gz  ({len(_lrows):,} rows, {_raw_dest.stat().st_size:,} bytes)")
                _z7 = _compress_aqua_to_7z(_raw_dest)
                if _z7:
                    _log(f"    → compressed: {_z7.name}  ({_z7.stat().st_size:,} bytes)")
        else:
            _log(f"  DRY-RUN: would write {_letter}/raw_{_raw_ts}.7z ({len(_lrows):,} rows)")

    # Remove combined raw file from data/ root if it came from AQUA pull
    if not args.dry_run and not args.local_csv:
        try:
            _af = Path(aqua_file)
            if _af.exists() and _af.parent.resolve() == data_dir.resolve():
                _af.unlink()
                _log(f"  Removed combined raw file: {_af.name}")
        except Exception as e:
            _log(f"  WARNING: could not remove combined raw file: {e}")

    # ── 3. Build list of TP keys to run ───────────────────────────────────────
    prog_dir = data_dir / "programs"
    # No persistent per-TP gz files; always run from current AQUA pull
    keys_to_run = sorted(groups.keys())

    # ── Excluded ops (email_config.json → excluded_ops): skip execution entirely ──
    _excl_ops: set[str] = set()
    try:
        _ec = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
        _excl_ops = {str(o) for o in _ec.get("excluded_ops", [])}
    except Exception:
        pass
    if _excl_ops:
        _before = len(keys_to_run)
        keys_to_run = [
            k for k in keys_to_run
            if not any(k.endswith(f"_{op}") for op in _excl_ops)
        ]
        _skipped = _before - len(keys_to_run)
        if _skipped:
            _log(f"  excluded_ops {sorted(_excl_ops)} → skipped {_skipped} key(s)")

    if args.keys:
        _kf = [s.strip() for s in args.keys.split(",") if s.strip()]
        keys_to_run = [k for k in keys_to_run if any(f in k for f in _kf)]
        _log(f"  --keys filter '{args.keys}' → {len(keys_to_run)} key(s)")

    _log(f"\nTP keys to run ({len(keys_to_run)}): {keys_to_run or '(none)'}")

    if not keys_to_run:
        _log("Nothing to run — sending no-new-data email and exiting.")
        _send_no_new_data_email(base_dir, args)
        if _local_7z_tmpdir:
            _local_7z_tmpdir.cleanup()
        sys.exit(0)

    # ── 4. Group by letter and run ────────────────────────────────────────────
    _letter_groups: dict[str, list[str]] = {}
    for _k in sorted(keys_to_run):
        _m = re.search(r'[0-9A-Za-z]H61([A-Za-z])', _k)
        _letter_groups.setdefault(_m.group(1).upper() if _m else "X", []).append(_k)
    _log(f"\nProgram groups: {list(_letter_groups.keys())} ({len(_letter_groups)} run folder(s))")

    env        = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    all_results: list[tuple] = []

    for _letter, _letter_keys in sorted(_letter_groups.items(), reverse=True):
        run_dir = base_dir / "output" / f"NVL_0H61{_letter}_{ts}"
        _log(f"\n{'='*65}")
        _log(f"=== Program 0H61{_letter}  ({len(_letter_keys)} TP(s))  →  {run_dir.name} ===")

        tp_results: list[tuple] = []   # (tp_key, ok, tp_output_dir, data_js_path)

        # Merge all TPs for this letter into one combined dataset so the pipeline
        # runs once with a single combined CSV (e.g. L0 + L5 both containing H61E).
        # The L0 key (matching '0H61') is used as the primary identifier.
        _primary_key = next(
            (k for k in sorted(_letter_keys) if re.search(r'0H61', k)),
            sorted(_letter_keys)[0]
        )
        if len(_letter_keys) > 1:
            _comb_rows: list[dict] = []
            _comb_hdrs: list[str] = []
            for _k in sorted(_letter_keys):
                if _k in groups:
                    _k_rows, _k_hdrs = groups[_k]
                    _comb_rows.extend(_k_rows)
                    for _h in _k_hdrs:
                        if _h not in _comb_hdrs:
                            _comb_hdrs.append(_h)
            _log(f"  Merging {len(_letter_keys)} TPs → combined: {len(_comb_rows):,} rows  (primary: {_primary_key})")
            groups[_primary_key] = (_comb_rows, _comb_hdrs)
        _exec_keys = [_primary_key]

        for tp_key in _exec_keys:
            _m_key  = re.search(r'[0-9A-Za-z]H61([A-Za-z])', tp_key)
            _sub    = f"0H61{_m_key.group(1).upper()}" if _m_key else "0H61X"
            # Write temp gz from in-memory data (deleted after pipeline; raw_<ts>.7z is the archival copy)
            _tp_letter_dir = prog_dir / _sub
            if not args.dry_run:
                _tp_letter_dir.mkdir(parents=True, exist_ok=True)
            archive = _tp_letter_dir / f"tmp_{tp_key}.csv.gz"
            if tp_key in groups and not args.dry_run:
                _tp_rows, _tp_hdrs = groups[tp_key]
                _write_gz(_tp_rows, _tp_hdrs, archive)

            _log(f"\n  TP: {tp_key}")
            ok, tp_output_dir, data_js_path = run_pipeline_for_tp(
                tp_key, archive, run_dir, args.dry_run,
            )

            if not args.dry_run and ok:
                _watermark_output_dir(tp_output_dir / "dashboard")

            tp_results.append((tp_key, ok, tp_output_dir, data_js_path))
            all_results.append((tp_key, ok, str(tp_output_dir), str(data_js_path or "")))
            # Delete temp gz (raw_<ts>.7z is the archival copy; no persistent per-TP files)
            if not args.dry_run:
                try:
                    if archive.exists() and archive.name.startswith("tmp_"):
                        archive.unlink()
                except Exception:
                    pass

        # ── Per-letter report ─────────────────────────────────────────────────
        report_path: Path | None = None
        if not args.dry_run and tp_results:
            report_path = _build_run_report(
                run_dir,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                str(aqua_file),
                tp_results,
                letter=_letter,
                base_dir=base_dir,
            )

        # ── Per-letter run log ────────────────────────────────────────────────
        _log(f"\nUpdating run log for 0H61{_letter}…")
        update_run_log(
            results=[(r[0], r[1], r[2]) for r in tp_results],
            aqua_file=str(aqua_file),
            run_log=run_log,
            dry_run=args.dry_run,
            report_path=report_path,
        )

        # ── Per-letter email config + cleanup (no email sent yet) ────────────
        ecfg: dict = {}
        if _EMAIL_CFG.exists():
            try: ecfg = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
            except Exception: pass
        _keep_runs = args.keep_runs if args.keep_runs is not None else max(1, int(ecfg.get("keep_runs", 10)))

        # ── Auto-cleanup old runs for this letter ──────────────────────────────
        if _keep_runs > 0:
            _log(f"\nAuto-cleanup 0H61{_letter} (keep={_keep_runs})…")
            _cleanup_old_runs(base_dir, _letter, keep=_keep_runs, dry_run=args.dry_run)

    # ── Send single consolidated email after all letters are processed ─────────
    ecfg: dict = {}
    if _EMAIL_CFG.exists():
        try: ecfg = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
        except Exception: pass
    to = ecfg.get("email_to_report") or ecfg.get("email_to") or args.email

    if not args.dry_run and all_results:
        run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        excl_keys = list(ecfg.get("excluded_keys", []))
        body = _build_email_report_html(
            base_dir / "output", run_ts,
            excluded_keys=excl_keys,
        )
        ts_label = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Save a persistent copy to reports/
        _reports_dir = base_dir / "reports"
        _reports_dir.mkdir(parents=True, exist_ok=True)
        _report_save = _reports_dir / f"Scan_Report_{ts_label}.html"
        _report_save.write_text(body, encoding="utf-8")
        _log(f"Report saved: {_report_save}")
        _att_dir  = Path(tempfile.mkdtemp(prefix="nvl_scan_att_"))
        try:
            att_path = _att_dir / f"NVL816-BLLC Scan Report {ts_label}.html"
            att_path.write_text(body, encoding="utf-8")
            send_email(to=to, subject="NVL816-BLLC Scan Report",
                       body_html=body, dry_run=args.dry_run,
                       attachments=[str(att_path)])
        finally:
            shutil.rmtree(_att_dir, ignore_errors=True)
    elif args.dry_run:
        _log(f"DRY-RUN: would send consolidated email → {to}")

    if _local_7z_tmpdir:
        _local_7z_tmpdir.cleanup()

    _log("\n" + "=" * 65)
    _log(f"Done. {len(all_results)} TP(s) processed.")
    _log("=" * 65)


if __name__ == "__main__":
    main()
