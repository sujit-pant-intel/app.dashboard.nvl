"""
manage_automation.py  —  GUI + automation engine for scan-dashboard automation.

GUI tabs:
  1. Email & Filter   — recipients + excluded keys (email_config.json)
  2. Run History      — NVL_H61_YYYYMMDD/ run folders; view/delete old runs
  3. Data Files       — data/programs/*.7z and raw AQUA pull snapshots
  4. Schedule         — Windows Task Scheduler: create, check, run now, remove

Automation engine (merged from former src/run_automation.py): AQUA pull,
per-TP-oper pipeline run, run log update, email report — see
run_automation_main() below. The Schedule tab and Task Scheduler both
invoke this same file with CLI flags to trigger the engine.

Usage:
    python manage_automation.py                          # launch GUI
    python manage_automation.py --dry-run                 # run automation engine (plan only)
    python manage_automation.py --local-csv "C:\\work\\scan\\data\\scan_data.CSV"
    python manage_automation.py --base-dir "C:\\work\\auto\\scan" --force
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
import threading
import tkinter as tk
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

# ── Ensure UTF-8 output on Windows (for CLI/automation-engine console output) ──
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── defaults ────────────────────────────────────────────────────────────────
_HERE      = Path(__file__).resolve().parent
_SELF      = Path(__file__).resolve()   # this file, self-invoked for the automation engine
_REPO_ROOT = _HERE.parent   # app.dashboard.nvl/
_BASE_DIR  = Path.home() / "auto" / "scan"
_VENV_PY   = next((p for p in [
               _REPO_ROOT.parent / ".venv" / "Scripts" / "python.exe",
               Path(r"Y:\tools\scripts\.venv\Scripts\python.exe"),
               Path(r"C:\scripts\.venv\Scripts\python.exe"),
             ] if p.exists()), None)
_PYTHON    = str(_VENV_PY) if _VENV_PY else sys.executable
_CFG_NAME  = "scan_setup_config.json"
_CFG_DIR   = _REPO_ROOT / "shared" / "setup" / "automation" / "scan-dashboard"
_EMAIL_TO  = "sujit.n.pant@intel.com"
_TASK_NAME = "NVL-BLLC Scan Automation"  # base; actual name is per-product
_LAUNCH_BAT_DIR = _HERE / "launch-bat"  # where scheduled-task .bat launchers live

_DEFAULT_PRODUCT_CFG = lambda: {
    "base_dir":        str(_BASE_DIR),
    "program_series":  "H61",
    "email_to_report": _EMAIL_TO,
    "email_to_alert":  _EMAIL_TO,
    "excluded_ops":    [],
    "excluded_keys":   [],
}

# ── colours ───────────────────────────────────────────────────────────────────
BG         = "#1a252f"
BG2        = "#1e2e3d"
BG3        = "#263950"
FG         = "#e8f0f7"
FG_DIM     = "#90a4ae"
ACCENT     = "#4fc3f7"
GREEN      = "#66bb6a"
RED        = "#ef5350"
AMBER      = "#ffa726"
FONT_MONO  = ("Courier New", 9)
FONT_UI    = ("Segoe UI", 10)
FONT_TITLE = ("Segoe UI", 13, "bold")
FONT_GROUP = ("Segoe UI", 10, "bold")


# ═════════════════════════════════════════════════════════════════════════
# Automation engine  (merged from former src/run_automation.py)
# ═════════════════════════════════════════════════════════════════════════

# ── Paths ──────────────────────────────────────────────────────────────────────
_PIPELINE    = _REPO_ROOT / "scan-dashboard" / "scan-dashboard.py"
_AQUA_CFG    = _REPO_ROOT / "shared" / "setup" / "automation" / "scan-dashboard" / "NVL_Sort_Scan - Dashboard.txt"
_YIELD_TGT   = _REPO_ROOT / "shared" / "setup" / "config" / "scan-dashboard" / "yield-estimate-per-fault-count.csv"

# ── Defaults ───────────────────────────────────────────────────────────────────
_RA_BASE_DIR = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\scan")
_DATA_DIR    = _RA_BASE_DIR / "data"
_RUN_LOG     = _RA_BASE_DIR / "run_log.html"
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
    ts          = _ts()
    report_name = _aqua_report_name(report_config)
    safe_name   = report_name.replace(" - ", "_").replace(" ", "_")
    out_base    = data_dir / f"{safe_name}_{ts}"
    out_req     = out_base.with_suffix(".zip")

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
        dest = data_dir / f"{safe_name}_{ts}.csv"
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
    _m = re.search(r'([A-Za-z]\d{2}[A-Za-z])', key)
    _letter_sub = _m.group(1).upper() if _m else "0H61X"
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

        # Resolve HRY config by sniffing DevRevStep prefix directly — avoids importlib overhead
        _hry_cfg = None
        try:
            import pandas as _pd2
            _cfg_dir = _REPO_ROOT / "shared" / "setup" / "config" / "scan-dashboard"
            _cfg_csvs = [p for p in sorted(_cfg_dir.glob("*.csv"))
                         if p.name != "yield-estimate-per-fault-count.csv"] if _cfg_dir.exists() else []
            if _cfg_csvs:
                _dhdr = _pd2.read_csv(str(csv_path), nrows=500, low_memory=False, dtype=str)
                _drc = next((c for c in _dhdr.columns if c.upper().startswith("DEVREVSTEP")), None)
                if _drc:
                    _pfx = _dhdr[_drc].dropna().iloc[0][:6].upper()
                    _hry_cfg = next((p for p in _cfg_csvs if p.name.upper().startswith(_pfx)), None)
                if _hry_cfg is None:
                    _hry_cfg = _cfg_csvs[0]
        except Exception:
            pass

        cfg = {
            "input":  [str(csv_path)],
            "output": str(tp_output_dir),
        }
        if _hry_cfg:
            cfg["config"] = str(_hry_cfg)
            _log(f"  [{tp_key}] HRY config → {_hry_cfg.name}")
        else:
            _log(f"  [{tp_key}] HRY config → (not resolved; pipeline will auto-select)")
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
        # unique die keys per IP across all wafers (for accurate fail %)
        ip_fail_dies_all: dict[str, set[str]] = {}
        for r in per_ip:
            ip = (r.get("IP") or "").strip()
            if ip:
                ip_counter[ip] += 1
                die_key = f"{r.get('X')}_{r.get('Y')}_{r.get('LOT')}_{r.get('WAFER')}"
                ip_fail_dies_all.setdefault(ip, set()).add(die_key)
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
        # count unique failing dies per mod/blk/reg (not raw records)
        mbr_fail_dies: dict[str, set[str]] = {}
        for r in per_ip:
            mod = (r.get("MODULE") or "").strip()
            blk = (r.get("BLOCK")  or "").strip()
            reg = (r.get("REGION") or "").strip()
            if mod:
                die_key = f"{r.get('X')}_{r.get('Y')}_{r.get('LOT')}_{r.get('WAFER')}"
                mbr_fail_dies.setdefault(f"{mod}/{blk}/{reg}", set()).add(die_key)
        fail_counter: Counter = Counter({k: len(v) for k, v in mbr_fail_dies.items()})

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
                # use unique failing dies so multi-instance IPs aren't over-counted
                uniq_fail_cnt = len(ip_fail_dies_all.get(ip, set()))
                obs = uniq_fail_cnt / total_dies * 100.0
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
                    ips_above_target.append((ip, uniq_fail_cnt, obs, tgt, obs - tgt, mods_str))

        return {
            "total_dies":   total_dies,
            "num_wafers":   num_wafers,
            "lots":         lots,
            "ff_pct":       ff_pct,
            "ff_df_pct":    ff_df_pct,
            "top_ips":      [
                (ip, len(ip_fail_dies_all.get(ip, set())), ip_target_pct.get(ip))
                for ip, cnt in ip_counter.most_common(5)
            ],  # counts = unique failing dies
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
    product_name: str = "NVL816-BLLC",
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

    title_str = (f"Scan Dashboard \u2014 {product_name} {letter.upper()} \u2014 {run_ts}"
                 if letter else f"Scan Dashboard \u2014 {product_name} \u2014 {run_ts}")

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
<h1>&#128202; {product_name} Scan Dashboard &mdash; Run Report</h1>
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
<p class="ts">Auto-generated by manage_automation.py &nbsp;|&nbsp;
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
    run_prefix: str = "NVL",
) -> list[tuple]:
    """Return [(run_label, [(tp_key, smry_or_None)]), ...] for previous runs, newest first.

    Collects history from ALL program letters (not just the current letter) so that
    e.g. the 0H61D email also shows recent 0H61C runs for context.
    """
    output_dir = base_dir / "output"
    if not output_dir.exists():
        return []

    # Collect all <run_prefix>_* run dirs, excluding the current one
    try:
        all_dirs = [
            d for d in output_dir.iterdir()
            if d.is_dir()
            and re.search(r'^' + re.escape(run_prefix) + r'_[A-Za-z]\d{2}[A-Za-z]_\d{8}_\d{6}$', d.name, re.IGNORECASE)
            and d.resolve() != current_run_dir.resolve()
        ]
    except OSError:
        return []
    past_dirs = sorted(all_dirs, key=lambda d: d.name, reverse=True)[:n_past]

    history: list[tuple] = []
    for rd in past_dirs:
        m = re.search(r'_(\d{8})_(\d{6})$', rd.name)
        lm = re.search(re.escape(run_prefix) + r'_([A-Za-z]\d{2}[A-Za-z])_', rd.name, re.IGNORECASE)
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
  &#128202; {product_name} Scan Dashboard &mdash; {overall}
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
                              excluded_keys: list | None = None,
                              product_name: str = "NVL816-BLLC") -> str:
    """Build self-contained sidebar+history HTML for scan email reports.

    Tabs: Summary (latest per program) | 0H61A | 0H61B | ...
    Columns: Run Date | Op | Dies | Wafers | FF% | FF+DF% | Yield Tgt | Total FC | Top IPs
    """
    from collections import defaultdict

    _excluded = set(excluded_keys or [])

    run_pattern = re.compile(r'^[A-Za-z0-9]+_([A-Za-z](\d+)[A-Za-z])_(\d{8}_\d{6})$')
    tp_pattern  = re.compile(r'([A-Za-z]\d{2}[A-Za-z]).*?_(\d{5,6})$')
    history: dict[str, list[dict]] = defaultdict(list)

    for rd in sorted(output_dir.iterdir()):
        if not rd.is_dir():
            continue
        m = run_pattern.match(rd.name)
        if not m:
            continue
        prog_key = m.group(1).upper()  # e.g. H61G, M61H
        ts = m.group(3)
        dt_str = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}"
        for tp_dir in sorted(rd.iterdir()):
            if not tp_dir.is_dir():
                continue
            if tp_dir.name in _excluded:
                continue
            tm = tp_pattern.search(tp_dir.name)
            if not tm:
                continue
            data_js = tp_dir / "dashboard" / "data.js"
            sm = _parse_scan_summary(data_js) if data_js.exists() else {}
            history[prog_key].append({
                "ts":     ts,
                "dt_str": dt_str,
                "op":     tm.group(2),
                "tp_key": tp_dir.name,
                "tp_dir": tp_dir,
                "sm":     sm,
            })

    for k in history:
        history[k].sort(key=lambda x: x["ts"], reverse=True)

    sorted_keys = sorted(
        history.keys(),
        key=lambda k: (int(k[1:-1]), k[-1]),
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
            f'<span class="prog-pill">{k}</span></a></td>'
            if link else
            f'<td class="c-prog"><span class="prog-pill">{k}</span></td>'
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
            pill = f'<span class="prog-pill">{ltr}</span>'
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
            f'    <span class="prog-pill">{k}</span>\n'
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
            f'<span class="nav-prog">{k}</span>'
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
<title>{product_name} Scan Report \u2014 {run_ts}</title>
<style>{CSS}</style>
</head>
<body>
<nav id="sidebar">
  <div id="sb-hdr">
    <h3>{product_name}</h3>
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


def _send_no_new_data_email(base_dir: Path, args, product_name: str = "NVL816-BLLC") -> None:
    _pcfg: dict = {}
    if _EMAIL_CFG.exists():
        try:
            _d = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
            _prods = _d.get("products", {})
            if getattr(args, "product", None) and args.product in _prods:
                _pcfg = _prods[args.product]
            else:
                for _p in _prods.values():
                    try:
                        if Path(_p.get("base_dir", "")).resolve() == base_dir.resolve():
                            _pcfg = _p
                            break
                    except Exception:
                        pass
            if not _pcfg:
                _pcfg = next(iter(_prods.values()), _d)
        except Exception:
            pass
    to = (_pcfg.get("email_to_report")
          or _pcfg.get("email_to")
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
<h2 style="color:#4fc3f7">{product_name} Scan Dashboard — No New Data</h2>
<p>Run at <strong>{run_ts}</strong>: AQUA pull completed but no new lot/wafer data
was detected since the last run. Pipeline was not re-executed.</p>
{last_report_link}
<hr/><p style="font-size:0.85em;color:#888">Pant, Sujit N — GEMS FTE</p>
</body></html>"""

    send_email(to=to, subject=f"{product_name} Scan Dashboard",
               body_html=body, dry_run=getattr(args, "dry_run", False))


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cleanup_old_runs(base_dir: Path, letter: str, keep: int = 10, dry_run: bool = False, run_prefix: str = "NVL") -> None:
    """Delete oldest run dirs for a letter, keeping the most recent `keep` runs.
    Tagged runs (.tag file) are always preserved regardless of position.
    """
    output_dir = base_dir / "output"
    if not output_dir.exists():
        return
    pattern = f"{run_prefix}_{letter}_"
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
    _log(f"  Cleanup {letter}: keeping {min(keep, len(untagged))} run(s), "
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

def run_automation_main() -> None:
    ap = argparse.ArgumentParser(
        description="Auto-pull AQUA + split by TP/op + run scan dashboards + email.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--aqua-exe",      default=_AQUA_EXE_AMR)
    ap.add_argument("--report-config", default=str(_AQUA_CFG))
    ap.add_argument("--base-dir",      default=str(_RA_BASE_DIR))
    ap.add_argument("--product",       default=None,
                    help="Product name key in scan_setup_config.json (e.g. 'NVLG-512'); "
                         "takes priority over base-dir path matching")
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

    # Override --report-config from product config's aqua_pull_config if not set explicitly.
    _prod_cfg_early = {}
    if _EMAIL_CFG.exists():
        try:
            _d0 = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
            _prods0 = _d0.get("products", {})
            if args.product and args.product in _prods0:
                _prod_cfg_early = _prods0[args.product]
            else:
                for _p0 in _prods0.values():
                    try:
                        if Path(_p0.get("base_dir", "")).resolve() == base_dir.resolve():
                            _prod_cfg_early = _p0
                            break
                    except Exception:
                        pass
                # Fallback: match by folder name (drive-letter vs UNC)
                if not _prod_cfg_early:
                    for _p0 in _prods0.values():
                        if Path(_p0.get("base_dir", "")).name == base_dir.name:
                            _prod_cfg_early = _p0
                            break
        except Exception:
            pass
    _aqcfg_from_prod = _prod_cfg_early.get("aqua_pull_config", "")
    if _aqcfg_from_prod and "--report-config" not in sys.argv:
        _resolved_aqcfg = _REPO_ROOT / _aqcfg_from_prod
        if _resolved_aqcfg.exists():
            args.report_config = str(_resolved_aqcfg)

    # Resolve per-product config: --product name > base_dir path match > first product.
    def _load_prod_cfg() -> dict:
        if not _EMAIL_CFG.exists():
            return {}
        try:
            _d = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
        except Exception:
            return {}
        _prods = _d.get("products", {})
        if args.product and args.product in _prods:
            return _prods[args.product]
        for _p in _prods.values():
            try:
                if Path(_p.get("base_dir", "")).resolve() == base_dir.resolve():
                    return _p
            except Exception:
                pass
        # Fallback: match by the final folder name (handles drive-letter vs UNC mismatch)
        for _k, _p in _prods.items():
            if Path(_p.get("base_dir", "")).name == base_dir.name:
                return _p
        return next(iter(_prods.values()), _d)

    # Resolve display product name for email subjects / HTML titles.
    _product_name: str = "NVL816-BLLC"
    try:
        if _EMAIL_CFG.exists():
            _d0 = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
            _prods0 = _d0.get("products", {})
            if args.product and args.product in _prods0:
                _product_name = args.product
            elif _prods0:
                # Try exact resolved path, then fall back to folder-name match
                _product_name = next(
                    (k for k, v in _prods0.items()
                     if Path(v.get("base_dir", "")).resolve() == base_dir.resolve()),
                    None
                ) or next(
                    (k for k, v in _prods0.items()
                     if Path(v.get("base_dir", "")).name == base_dir.name),
                    next(iter(_prods0))
                )
    except Exception:
        pass

    # Leading alpha chars of product name, e.g. "NVLG" from "NVLG-512", "NVL" from "NVL816-BLLC"
    _run_prefix = re.match(r'([A-Za-z]+)', _product_name).group(1) if _product_name else "NVL"

    _log("=" * 65)
    _log(f"scan run_automation  [{'DRY-RUN' if args.dry_run else 'LIVE'}]")
    _log(f"Product   : {_product_name}")
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
            _pcfg  = _load_prod_cfg()
            err_to = _pcfg.get("email_to_alert", _pcfg.get("email_to_report", args.email)) or args.email
            send_email(
                to=err_to,
                subject=f"{_product_name} Scan Dashboard",
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
        _m = re.search(r'([A-Za-z]\d{2}[A-Za-z])', _key)
        _letter = _m.group(1).upper() if _m else "0H61X"
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

    # ── Excluded ops (scan_setup_config.json → excluded_ops): skip execution entirely ──
    _excl_ops: set[str] = set()
    try:
        _excl_ops = {str(o) for o in _load_prod_cfg().get("excluded_ops", [])}
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
        _send_no_new_data_email(base_dir, args, _product_name)
        if _local_7z_tmpdir:
            _local_7z_tmpdir.cleanup()
        sys.exit(0)

    # ── 4. Group by letter and run ────────────────────────────────────────────
    _letter_groups: dict[str, list[str]] = {}
    for _k in sorted(keys_to_run):
        _m = re.search(r'([A-Za-z]\d{2}[A-Za-z])', _k)
        _letter_groups.setdefault(_m.group(1).upper() if _m else "?", []).append(_k)
    _log(f"\nProgram groups: {list(_letter_groups.keys())} ({len(_letter_groups)} run folder(s))")

    env        = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    all_results: list[tuple] = []

    for _letter, _letter_keys in sorted(_letter_groups.items(), reverse=True):
        run_dir = base_dir / "output" / f"{_run_prefix}_{_letter}_{ts}"
        _log(f"\n{'='*65}")
        _log(f"=== Program {_letter}  ({len(_letter_keys)} TP(s))  →  {run_dir.name} ===")

        tp_results: list[tuple] = []   # (tp_key, ok, tp_output_dir, data_js_path)

        # Merge all TPs for this letter into one combined dataset so the pipeline
        # runs once with a single combined CSV (e.g. L0 + L5 both containing H61E).
        # The L0 key (matching '0H61') is used as the primary identifier.
        _primary_key = next(
            (k for k in sorted(_letter_keys) if re.search(re.escape(_letter), k)),
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
            _m_key  = re.search(r'([A-Za-z]\d{2}[A-Za-z])', tp_key)
            _sub    = _m_key.group(1).upper() if _m_key else "0H61X"
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
                product_name=_product_name,
            )

        # ── Per-letter run log ────────────────────────────────────────────────
        _log(f"\nUpdating run log for {_letter}…")
        update_run_log(
            results=[(r[0], r[1], r[2]) for r in tp_results],
            aqua_file=str(aqua_file),
            run_log=run_log,
            dry_run=args.dry_run,
            report_path=report_path,
        )

        # ── Per-letter email config + cleanup (no email sent yet) ────────────
        _keep_runs = args.keep_runs if args.keep_runs is not None else max(1, int(_load_prod_cfg().get("keep_runs", 10)))

        # ── Auto-cleanup old runs for this letter ──────────────────────────────
        if _keep_runs > 0:
            _log(f"\nAuto-cleanup {_letter} (keep={_keep_runs})…")
            _cleanup_old_runs(base_dir, _letter, keep=_keep_runs, dry_run=args.dry_run, run_prefix=_run_prefix)

    # ── Send single consolidated email after all letters are processed ─────────
    _pcfg = _load_prod_cfg()
    to    = _pcfg.get("email_to_report") or _pcfg.get("email_to") or args.email

    if not args.dry_run and all_results:
        run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        excl_keys = list(_pcfg.get("excluded_keys", []))
        body = _build_email_report_html(
            base_dir / "output", run_ts,
            excluded_keys=excl_keys,
            product_name=_product_name,
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
            att_path = _att_dir / f"{_product_name} Scan Report {ts_label}.html"
            att_path.write_text(body, encoding="utf-8")
            send_email(to=to, subject=f"{_product_name} Scan Report",
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


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_config(cfg_path: Path) -> dict:
    """Load full config; migrate flat (legacy) format to per-product structure."""
    if cfg_path.exists():
        try:
            d = json.loads(cfg_path.read_text(encoding="utf-8"))
            if "products" not in d:
                if "email_to" in d and "email_to_report" not in d:
                    d["email_to_report"] = d.pop("email_to")
                prod_cfg = _DEFAULT_PRODUCT_CFG()
                for k in ("base_dir", "program_series", "email_to_report", "email_to_alert",
                          "excluded_ops", "excluded_keys", "keep_runs"):
                    if k in d:
                        prod_cfg[k] = d[k]
                d = {"products": {"NVL816-BLLC": prod_cfg}}
            return d
        except Exception:
            pass
    return {"products": {"NVL816-BLLC": _DEFAULT_PRODUCT_CFG()}}


def _save_config(cfg_path: Path, cfg: dict) -> None:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _open_html(path) -> None:
    """Open an HTML file (local or UNC) in Edge with a proper file:// URI.

    Chrome blocks UNC file:// links; Edge handles them correctly when the
    URI uses four leading slashes (file:////server/share/...).
    """
    import subprocess
    p = str(path).replace("\\", "/")
    if p.startswith("//"):          # UNC  \\server\... → //server/...
        uri = "file://" + p         # → file:////server/...  (4 slashes total)
    else:
        uri = "file:///" + p.lstrip("/")
    subprocess.Popen(["cmd", "/c", "start", "msedge", uri])


def _discover_keys(base_dir: Path, program_series: str = "H61", run_prefix: str = "NVL") -> list[str]:
    """Discover TP keys from run folder subfolders under base_dir/output.

    base_dir already scopes to a single product, so any dir ending in a
    run timestamp (_YYYYMMDD_HHMMSS) is treated as a run folder — no
    dependency on the exact product prefix/program_series digits.
    """
    keys: set[str] = set()
    output_dir = base_dir / "output"
    if output_dir.exists():
        pattern = re.compile(r'_\d{8}_\d{6}$')
        for run_dir in output_dir.iterdir():
            if run_dir.is_dir() and pattern.search(run_dir.name):
                for sub in run_dir.iterdir():
                    if sub.is_dir():
                        keys.add(sub.name)
    return sorted(keys)


def _group_keys(keys: list[str], program_series: str = "H61") -> dict[str, list[str]]:
    # group by full variant key e.g. H61G, M61H so H61G and M61G are separate groups
    groups: dict[str, list[str]] = {}
    for k in keys:
        m = re.search(r'([A-Za-z]\d{2}[A-Za-z])', k)
        group_key = m.group(1).upper() if m else "?"
        groups.setdefault(group_key, []).append(k)
    return dict(sorted(groups.items(), reverse=True))


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


def _dir_size(p: Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except Exception:
        return 0


def _mtime_str(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class AutomationManager(tk.Frame):
    def __init__(self, master, base_dir: Path) -> None:
        super().__init__(master, bg=BG)
        self.cfg_path  = _CFG_DIR / _CFG_NAME
        self._all_cfg  = _load_config(self.cfg_path)

        self._products: dict[str, Path] = {
            k: Path(v.get("base_dir", str(_BASE_DIR)))
            for k, v in self._all_cfg["products"].items()
        }

        default_label = next(
            (k for k, p in self._products.items() if p == base_dir),
            next(iter(self._products))
        )
        self._product_var = tk.StringVar(value=default_label)
        self._load_product(default_label)

        self._apply_styles()
        self._build_ui()

    @property
    def _task_name(self) -> str:
        return f"Scan Automation [{self._product_var.get()}]"

    def _load_product(self, label: str) -> None:
        self.cfg            = self._all_cfg["products"][label]
        self.base_dir       = Path(self.cfg.get("base_dir", str(_BASE_DIR)))
        self.program_series = self.cfg.get("program_series", "H61")
        self.excluded       = set(self.cfg.get("excluded_keys", []))
        self.excluded_ops   = set(str(o) for o in self.cfg.get("excluded_ops", []))
        # Run-folder prefix (e.g. 'NVLG' for 'NVLG-512') — must match run_automation_main()'s _run_prefix
        _rp_m = re.match(r'([A-Za-z]+)', label)
        self._run_prefix = _rp_m.group(1) if _rp_m else "NVL"

    def _select_product_btn(self, label: str) -> None:
        self._product_var.set(label)
        for lbl, btn in self._prod_btns.items():
            btn.config(bg=ACCENT if lbl == label else BG2,
                       fg=BG    if lbl == label else FG_DIM)
        self._switch_product(label)

    def _switch_product(self, label: str) -> None:
        self._load_product(label)
        self._base_dir_label.set(str(self.base_dir))
        self.report_email_var.set(self.cfg.get("email_to_report", _EMAIL_TO))
        self.alert_email_var.set(self.cfg.get("email_to_alert",
                                 self.cfg.get("email_to_report", _EMAIL_TO)))
        self._populate_email()
        self._refresh_history()
        self._refresh_data()
        if hasattr(self, "_sched_task_name_var"):
            self._sched_task_name_var.set(self._task_name)
        self._sched_refresh()

    def _build_ui(self) -> None:
        hdr = tk.Frame(self, bg=BG3)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Scan Automation Manager", font=FONT_TITLE,
                 bg=BG3, fg=ACCENT).pack(side="left", padx=14, pady=8)

        if len(self._products) > 1:
            sw = tk.Frame(hdr, bg=BG3)
            sw.pack(side="left", padx=10)
            tk.Label(sw, text="Product:", font=FONT_UI, bg=BG3, fg=FG_DIM).pack(side="left", padx=(0, 4))
            self._prod_btns: dict[str, tk.Button] = {}
            for _lbl in self._products:
                _btn = tk.Button(
                    sw, text=_lbl, font=FONT_UI, relief="flat", cursor="hand2",
                    bg=ACCENT if _lbl == self._product_var.get() else BG2,
                    fg=BG    if _lbl == self._product_var.get() else FG_DIM,
                    activebackground=ACCENT, activeforeground=BG,
                    command=lambda l=_lbl: self._select_product_btn(l),
                    padx=8, pady=3,
                )
                _btn.pack(side="left", padx=2)
                self._prod_btns[_lbl] = _btn

        self._base_dir_label = tk.StringVar(value=str(self.base_dir))
        info = tk.Frame(hdr, bg=BG3)
        info.pack(side="left", padx=4)
        tk.Label(info, textvariable=self._base_dir_label, font=("Segoe UI", 10, "bold"),
                 bg=BG3, fg="#5BB8FF").pack(anchor="w")
        tk.Label(info, text=f"config: {self.cfg_path}", font=("Segoe UI", 9),
                 bg=BG3, fg="#7ECFFF").pack(anchor="w")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 8))

        self._tab_email    = tk.Frame(nb, bg=BG)
        self._tab_history  = tk.Frame(nb, bg=BG)
        self._tab_data     = tk.Frame(nb, bg=BG)
        self._tab_schedule = tk.Frame(nb, bg=BG)

        nb.add(self._tab_email,    text="  Email & Filter  ")
        nb.add(self._tab_history,  text="  Run History  ")
        nb.add(self._tab_data,     text="  Data Files  ")
        nb.add(self._tab_schedule, text="  Schedule  ")

        self._build_email_tab()
        self._build_history_tab()
        self._build_data_tab()
        self._build_schedule_tab()

        nb.bind("<<NotebookTabChanged>>", self._on_tab_change)

    def _apply_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook",         background=BG,  borderwidth=0)
        style.configure("TNotebook.Tab",     background=BG3, foreground=FG_DIM,
                         padding=[12, 5], font=FONT_UI)
        style.map("TNotebook.Tab",
                  background=[("selected", BG2)],
                  foreground=[("selected", ACCENT)])
        style.configure("Treeview",          background=BG2, foreground=FG,
                         fieldbackground=BG2, rowheight=22, font=FONT_MONO)
        style.configure("Treeview.Heading",  background=BG3, foreground=ACCENT,
                         relief="flat", font=FONT_UI)
        style.map("Treeview",
                  background=[("selected", BG3)],
                  foreground=[("selected", ACCENT)])
        style.configure("TScrollbar",        background=BG3, troughcolor=BG,
                         arrowcolor=FG_DIM, borderwidth=0)
        style.configure("TSpinbox",          fieldbackground=BG2, foreground=FG,
                         background=BG3, arrowcolor=ACCENT, font=FONT_MONO)

    def _btn(self, parent, text: str, cmd, bg: str = BG3, fg: str = FG,
             padx: "int | tuple" = 8, pady: int = 4) -> tk.Button:
        return tk.Button(
            parent, text=text, command=cmd,
            bg=bg, fg=fg, activebackground=BG2, activeforeground=ACCENT,
            font=FONT_UI, relief="flat", cursor="hand2",
            padx=padx, pady=pady,
        )

    def _on_tab_change(self, event) -> None:
        idx = event.widget.index("current")
        if idx == 1:
            self._refresh_history()
        elif idx == 2:
            self._refresh_data()
        elif idx == 3:
            self._sched_refresh()

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 1 — Email & Filter
    # ═════════════════════════════════════════════════════════════════════════

    def _build_email_tab(self) -> None:
        p   = self._tab_email
        pad = dict(padx=14, pady=6)

        top = tk.Frame(p, bg=BG)
        top.pack(fill="x", padx=14, pady=(10, 2))
        tk.Label(top, text=f"Config: {self.cfg_path}", font=("Segoe UI", 8),
                 bg=BG, fg=FG_DIM).pack(side="left")
        self._btn(top, "Cancel", self.winfo_toplevel().destroy, fg=FG_DIM
                  ).pack(side="right", padx=(6, 0))
        self._btn(top, "Save", self._save_email,
                  bg="#1b5e20", fg="#00ff7f").pack(side="right")

        # Recipients
        frm = tk.LabelFrame(p, text="  Recipients  ", font=FONT_UI,
                             bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm.pack(fill="x", **pad)

        self.report_email_var = tk.StringVar(value=self.cfg.get("email_to_report", _EMAIL_TO))
        self.alert_email_var  = tk.StringVar(
            value=self.cfg.get("email_to_alert",
                               self.cfg.get("email_to_report", _EMAIL_TO)))

        for row, label, var, color, note in [
            (0, "Report To:", self.report_email_var, GREEN, "Final report (semicolons OK)"),
            (1, "Alerts To:", self.alert_email_var,  AMBER, "AQUA errors / pipeline failures"),
        ]:
            tk.Label(frm, text=label, font=FONT_UI, bg=BG, fg=color
                     ).grid(row=row, column=0, sticky="w", padx=8, pady=4)
            tk.Entry(frm, textvariable=var, font=FONT_UI, bg=BG2, fg=FG,
                     insertbackground=FG, relief="flat", width=48
                     ).grid(row=row, column=1, padx=8, pady=4, sticky="ew")
            tk.Label(frm, text=note, font=("Segoe UI", 7), bg=BG, fg=FG_DIM
                     ).grid(row=row, column=2, sticky="w", padx=(0, 8))
        frm.columnconfigure(1, weight=1)

        # Excluded Op Codes
        frm_ops = tk.LabelFrame(p, text="  Excluded Op Codes  ", font=FONT_UI,
                                bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_ops.pack(fill="x", **pad)

        tk.Label(frm_ops, bg=BG, fg=FG_DIM, font=("Segoe UI", 8),
                 text="5-6 digit op codes skipped in email report."
                 ).pack(anchor="w", padx=8, pady=(4, 0))

        self._ops_tags_frame = tk.Frame(frm_ops, bg=BG)
        self._ops_tags_frame.pack(fill="x", padx=8, pady=(4, 2))

        ops_add_row = tk.Frame(frm_ops, bg=BG)
        ops_add_row.pack(anchor="w", padx=8, pady=(0, 6))
        tk.Label(ops_add_row, text="Add:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).pack(side="left", padx=(0, 4))
        self._ops_entry_var = tk.StringVar()
        ops_entry = tk.Entry(ops_add_row, textvariable=self._ops_entry_var,
                             font=FONT_MONO, bg=BG2, fg=FG, insertbackground=FG,
                             relief="flat", width=10)
        ops_entry.pack(side="left", padx=(0, 4))
        ops_entry.bind("<Return>", lambda _e: self._add_op())
        self._btn(ops_add_row, "+ Add", self._add_op).pack(side="left")
        self._refresh_ops_tags()

        # Program filter
        frm_prog = tk.LabelFrame(p, text="  Program Filter  ", font=FONT_UI,
                                 bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_prog.pack(fill="both", expand=True, **pad)

        tk.Label(frm_prog, bg=BG, fg=FG_DIM, font=("Segoe UI", 8),
                 text="Unchecked programs are excluded from the email report "
                      "(pipeline still runs)."
                 ).pack(anchor="w", padx=8, pady=(4, 0))

        tb = tk.Frame(frm_prog, bg=BG)
        tb.pack(fill="x", padx=8, pady=(2, 0))
        self._btn(tb, "✔ All",     self._select_all).pack(side="left", padx=(0, 6))
        self._btn(tb, "✘ None",    self._deselect_all).pack(side="left", padx=(0, 6))
        self._btn(tb, "↺ Refresh", self._populate_email).pack(side="left")

        cf = tk.Frame(frm_prog, bg=BG)
        cf.pack(fill="both", expand=True, padx=8, pady=6)

        self.email_canvas = tk.Canvas(cf, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(cf, orient="vertical", command=self.email_canvas.yview)
        self.email_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.email_canvas.pack(side="left", fill="both", expand=True)

        self.email_inner = tk.Frame(self.email_canvas, bg=BG)
        self._email_cwin = self.email_canvas.create_window(
            (0, 0), window=self.email_inner, anchor="nw")
        self.email_inner.bind(
            "<Configure>",
            lambda e: self.email_canvas.configure(
                scrollregion=self.email_canvas.bbox("all")))
        self.email_canvas.bind(
            "<Configure>",
            lambda e: self.email_canvas.itemconfig(self._email_cwin, width=e.width))
        self.email_canvas.bind(
            "<MouseWheel>",
            lambda e: self.email_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x", padx=14, pady=(0, 12))
        self.email_status = tk.StringVar()
        tk.Label(bot, textvariable=self.email_status, font=("Segoe UI", 9),
                 bg=BG, fg=GREEN).pack(side="left")

        self.check_vars: dict[str, tk.BooleanVar] = {}
        self._populate_email()

    def _populate_email(self) -> None:
        for w in self.email_inner.winfo_children():
            w.destroy()
        self.check_vars.clear()

        keys   = _discover_keys(self.base_dir, self.program_series, run_prefix=self._run_prefix)
        groups = _group_keys(keys, self.program_series)

        if not keys:
            tk.Label(self.email_inner,
                     text="No TP keys found.\nRun automation first, then refresh.",
                     font=FONT_UI, bg=BG, fg=FG_DIM).pack(padx=12, pady=20)
            return

        for letter, tp_keys in groups.items():
            hdr = tk.Frame(self.email_inner, bg=BG3)
            hdr.pack(fill="x", pady=(8, 0))
            tk.Label(hdr, text=f"  {letter}", font=FONT_GROUP,
                     bg=BG3, fg=ACCENT).pack(side="left", padx=6, pady=4)
            n_excl = sum(1 for k in tp_keys if k in self.excluded)
            if n_excl:
                tk.Label(hdr, text=f"{n_excl} excluded", font=("Segoe UI", 8),
                         bg=BG3, fg=AMBER).pack(side="right", padx=8)

            grp = tk.Frame(self.email_inner, bg=BG2)
            grp.pack(fill="x", pady=(0, 2))

            for tp_key in tp_keys:
                included = tp_key not in self.excluded
                var = tk.BooleanVar(value=included)
                self.check_vars[tp_key] = var

                row = tk.Frame(grp, bg=BG2)
                row.pack(fill="x", padx=4, pady=1)

                tk.Checkbutton(
                    row, variable=var, bg=BG2, fg=FG,
                    activebackground=BG2, activeforeground=ACCENT,
                    selectcolor=BG3, relief="flat", cursor="hand2",
                    command=lambda k=tp_key, v=var: self._on_toggle(k, v),
                ).pack(side="left")

                m_op   = re.search(r'_(\d{5,6})$', tp_key)
                op_lbl = f"op {m_op.group(1)}" if m_op else ""

                key_lbl = tk.Label(row, text=tp_key, font=FONT_MONO,
                                   bg=BG2, fg=FG if included else FG_DIM)
                key_lbl.pack(side="left", padx=(2, 10))
                tk.Label(row, text=op_lbl, font=("Segoe UI", 8),
                         bg=BG2, fg=FG_DIM).pack(side="left")
                state_lbl = tk.Label(row, font=("Segoe UI", 8), bg=BG2,
                                     text="included" if included else "EXCLUDED",
                                     fg=GREEN if included else RED)
                state_lbl.pack(side="right", padx=8)

                var._label     = state_lbl   # type: ignore[attr-defined]
                var._key_label = key_lbl     # type: ignore[attr-defined]

    def _on_toggle(self, key: str, var: tk.BooleanVar) -> None:
        included = var.get()
        if included:
            self.excluded.discard(key)
        else:
            self.excluded.add(key)
        try:
            var._label.config(text="included" if included else "EXCLUDED",  # type: ignore
                               fg=GREEN if included else RED)
            var._key_label.config(fg=FG if included else FG_DIM)            # type: ignore
        except Exception:
            pass

    def _select_all(self) -> None:
        self.excluded.clear()
        for k, v in self.check_vars.items():
            v.set(True)
            self._on_toggle(k, v)

    def _deselect_all(self) -> None:
        for k, v in self.check_vars.items():
            v.set(False)
            self._on_toggle(k, v)

    def _save_email(self) -> None:
        report_to = self.report_email_var.get().strip()
        alert_to  = self.alert_email_var.get().strip() or report_to
        if not report_to:
            messagebox.showerror("Error", "Report recipient cannot be empty.")
            return
        label = self._product_var.get()
        self._all_cfg["products"][label].update({
            "email_to_report": report_to,
            "email_to_alert":  alert_to,
            "excluded_ops":    sorted(self.excluded_ops),
            "excluded_keys":   sorted(self.excluded),
        })
        try:
            _save_config(self.cfg_path, self._all_cfg)
            self.cfg = self._all_cfg["products"][label]
            n = len(self.excluded)
            self.email_status.set(
                f"Saved — {n} key(s) excluded." if n else "Saved — all keys included."
            )
            self._populate_email()
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def _refresh_ops_tags(self) -> None:
        for w in self._ops_tags_frame.winfo_children():
            w.destroy()
        if not self.excluded_ops:
            tk.Label(self._ops_tags_frame, text="(none)", font=("Segoe UI", 8),
                     bg=BG, fg=FG_DIM).pack(side="left")
            return
        for op in sorted(self.excluded_ops):
            tag = tk.Frame(self._ops_tags_frame, bg=BG3, bd=0)
            tag.pack(side="left", padx=(0, 4), pady=2)
            tk.Label(tag, text=op, font=FONT_MONO, bg=BG3, fg=AMBER
                     ).pack(side="left", padx=(6, 2), pady=2)
            tk.Button(tag, text="✕", font=("Segoe UI", 8), bg=BG3, fg=RED,
                      activebackground=RED, activeforeground=BG,
                      relief="flat", cursor="hand2", bd=0,
                      command=lambda o=op: self._remove_op(o)
                      ).pack(side="left", padx=(0, 4), pady=2)

    def _add_op(self) -> None:
        raw = self._ops_entry_var.get().strip()
        if not raw:
            return
        if not re.fullmatch(r'\d{5,6}', raw):
            messagebox.showerror("Invalid op code",
                                 f"'{raw}' is not a valid 5-6 digit op code.")
            return
        self.excluded_ops.add(raw)
        self._ops_entry_var.set("")
        self._refresh_ops_tags()

    def _remove_op(self, op: str) -> None:
        self.excluded_ops.discard(op)
        self._refresh_ops_tags()

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 2 — Run History
    # ═════════════════════════════════════════════════════════════════════════

    def _build_history_tab(self) -> None:
        p = self._tab_history

        # Row 1 - navigation & selection
        tb = tk.Frame(p, bg=BG)
        tb.pack(fill="x", padx=12, pady=(10, 2))
        self._btn(tb, "Refresh",       self._refresh_history).pack(side="left", padx=(0, 6))
        self._btn(tb, "Select All",    self._hist_select_all).pack(side="left", padx=(0, 6))
        self._btn(tb, "Clear",         self._hist_clear_sel).pack(side="left", padx=(0, 6))
        self._btn(tb, "Tag",           self._hist_tag,
                  bg="#2d3b2d", fg="#a5d6a7").pack(side="left", padx=(0, 6))
        self._btn(tb, "Open Dashboard", self._hist_open_html,
                  bg="#1a3550", fg="#80d8ff").pack(side="left", padx=(0, 6))
        tk.Label(tb, text="Keep:", bg=BG, fg=FG_DIM, font=FONT_UI).pack(side="left", padx=(16, 2))
        self._keep_runs_var = tk.IntVar(value=int(self.cfg.get("keep_runs", 10)))
        ttk.Spinbox(tb, from_=1, to=50, width=4,
                    textvariable=self._keep_runs_var, font=FONT_MONO).pack(side="left", padx=(0, 4))
        tk.Label(tb, text="runs", bg=BG, fg=FG_DIM, font=FONT_UI).pack(side="left", padx=(0, 8))
        self._btn(tb, "Cleanup Now", self._hist_cleanup_auto,
                  bg="#1a2e20", fg="#a5d6a7").pack(side="left", padx=(0, 6))

        # Row 2 - report actions & delete
        tb2 = tk.Frame(p, bg=BG)
        tb2.pack(fill="x", padx=12, pady=(0, 4))
        self._btn(tb2, "Send Report",    self._hist_send_email,
                  bg="#1a3a5c", fg="#90caf9").pack(side="left", padx=(0, 6))
        self._btn(tb2, "Save Report",    self._hist_save_report,
                  bg="#1a3a3c", fg="#80deea").pack(side="left", padx=(0, 6))
        self._btn(tb2, "Delete + Data",  lambda: self._hist_delete(include_data=True),
                  bg="#6b3a00", fg="#ffd180").pack(side="right", padx=(6, 0))
        self._btn(tb2, "Delete Run",     lambda: self._hist_delete(include_data=False),
                  bg="#5d1a1a", fg="#ffcdd2").pack(side="right", padx=(0, 6))


        cols = ("tag", "folder", "date", "tps", "size")
        self.hist_tree = ttk.Treeview(p, columns=cols, show="headings",
                                      selectmode="extended")
        self._hist_sort_desc = True
        self.hist_tree.heading("tag",    text="Tag",           anchor="w")
        self.hist_tree.heading("folder", text="Run Folder",    anchor="w")
        self.hist_tree.heading("date",   text="Date / Time ↓", anchor="w",
                               command=self._hist_toggle_sort)
        self.hist_tree.heading("tps",    text="TPs",           anchor="center")
        self.hist_tree.heading("size",   text="Size",          anchor="e")
        self.hist_tree.column("tag",    width=70,  stretch=False)
        self.hist_tree.column("folder", width=300, stretch=True)
        self.hist_tree.column("date",   width=150, stretch=False)
        self.hist_tree.column("tps",    width=60,  stretch=False, anchor="center")
        self.hist_tree.column("size",   width=90,  stretch=False, anchor="e")

        vsb = ttk.Scrollbar(p, orient="vertical",   command=self.hist_tree.yview)
        hsb = ttk.Scrollbar(p, orient="horizontal", command=self.hist_tree.xview)
        self.hist_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        hsb.pack(side="bottom", fill="x",    padx=12, pady=(0, 0))
        vsb.pack(side="right",  fill="y")
        self.hist_tree.pack(fill="both", expand=True, padx=(12, 0), pady=(0, 0))
        self.hist_tree.bind("<Double-1>", self._hist_tag_dblclick)

        self._hist_ctx = tk.Menu(self, tearoff=0, bg=BG2, fg=FG,
                                 activebackground=BG3, activeforeground=ACCENT,
                                 font=FONT_UI, bd=0)
        self._hist_ctx.add_command(label="🌐  Open Dashboard in Browser",
                                   command=self._hist_open_html)
        self._hist_ctx.add_command(label="📋  Copy file:// link to Clipboard",
                                   command=lambda: self._hist_open_html(copy_only=True))
        self._hist_ctx.add_separator()
        self._hist_ctx.add_command(label="✉  Send Report", command=self._hist_send_email)
        self._hist_ctx.add_command(label="💾  Save Report", command=self._hist_save_report)
        self._hist_ctx.add_command(label="🏷  Tag Run",    command=self._hist_tag)
        self.hist_tree.bind("<Button-3>", self._hist_show_ctx)

        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x", padx=12, pady=(4, 8))
        self.hist_status = tk.StringVar()
        tk.Label(bot, textvariable=self.hist_status, font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM).pack(side="left")

        self._refresh_history()

    def _hist_toggle_sort(self) -> None:
        self._hist_sort_desc = not self._hist_sort_desc
        arrow = "↓" if self._hist_sort_desc else "↑"
        self.hist_tree.heading("date", text=f"Date / Time {arrow}")
        self._refresh_history()

    def _refresh_history(self) -> None:
        self.hist_tree.delete(*self.hist_tree.get_children())
        output_dir = self.base_dir / "output"
        if not output_dir.exists():
            self.hist_status.set(f"No output/ folder found under {self.base_dir}")
            return

        def _folder_ts(d):
            m = re.search(r'(\d{8})[_T](\d{6})', d.name)
            return (m.group(1) + m.group(2)) if m else d.name

        # base_dir already scopes to this product's output/ — show any run folder
        pattern = re.compile(r'_\d{8}_\d{6}$')
        folders = sorted(
            [d for d in output_dir.iterdir()
             if d.is_dir() and pattern.search(d.name)],
            key=_folder_ts, reverse=self._hist_sort_desc,
        )

        for d in folders:
            m = re.search(r'(\d{8})[_T](\d{6})', d.name)
            if m:
                date_str = (f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"
                            f" {m.group(2)[:2]}:{m.group(2)[2:4]}:{m.group(2)[4:]}")
            else:
                m2 = re.search(r'(\d{8})', d.name)
                date_str = (f"{m2.group(1)[:4]}-{m2.group(1)[4:6]}-{m2.group(1)[6:]}" if m2 else "?")

            subfolders = [x.name for x in d.iterdir() if x.is_dir()]
            tp_count   = len(subfolders)
            if tp_count == 1:
                folder_disp = f"{d.name}/{subfolders[0]}"
            elif tp_count > 1:
                _names = ", ".join(subfolders[:2])
                folder_disp = f"{d.name}/{_names}{'…' if tp_count > 2 else ''}"
            else:
                folder_disp = d.name

            sz       = _dir_size(d)
            tag_file = d / ".tag"
            tag      = tag_file.read_text(encoding="utf-8").strip() if tag_file.exists() else ""
            self.hist_tree.insert("", "end", iid=str(d),
                                  values=(tag, folder_disp, date_str, tp_count, _fmt_size(sz)))

        self.hist_status.set(f"{len(folders)} run folder(s)")

    def _hist_select_all(self) -> None:
        self.hist_tree.selection_set(self.hist_tree.get_children())

    def _hist_clear_sel(self) -> None:
        self.hist_tree.selection_remove(self.hist_tree.get_children())

    def _hist_show_ctx(self, event) -> None:
        iid = self.hist_tree.identify_row(event.y)
        if iid:
            if iid not in self.hist_tree.selection():
                self.hist_tree.selection_set(iid)
            self._hist_ctx.post(event.x_root, event.y_root)

    def _hist_open_html(self, copy_only: bool = False) -> None:
        """Open dashboard/index.html in the default browser, or copy its file:// URL."""
        import os
        sel = self.hist_tree.selection()
        if not sel:
            self.hist_status.set("No run selected.")
            return

        _sd_oh = re.search(r'(\d+)$', self.program_series)
        _dstr_oh = _sd_oh.group(1) if _sd_oh else '61'
        links: list[str] = []
        for iid in sel:
            run_dir = Path(iid)
            # Look for dashboard/index.html inside each TP subfolder
            for sub in sorted(run_dir.iterdir()):
                if sub.is_dir() and re.search(_dstr_oh + r'[A-Za-z]', sub.name):
                    dash = sub / "dashboard" / "index.html"
                    if dash.exists():
                        links.append(str(dash))
                        break
            # Fallback: report.html
            rpt = run_dir / "report.html"
            if not links and rpt.exists():
                links.append(str(rpt))

        if not links:
            self.hist_status.set("No dashboard HTML found in selected run(s).")
            return

        if copy_only:
            self.clipboard_clear()
            self.clipboard_append(links[0])
            self.hist_status.set(f"Copied: {links[0]}")
            return

        for link in links[:3]:   # open at most 3 at once
            _open_html(link)
        self.hist_status.set(f"Opened {len(links)} dashboard(s).")

    def _hist_tag_dblclick(self, event) -> None:
        iid = self.hist_tree.identify_row(event.y)
        col = self.hist_tree.identify_column(event.x)
        if iid and col == "#1":
            self._hist_tree_tag_edit(iid)

    def _hist_tree_tag_edit(self, iid: str) -> None:
        """Inline edit the tag cell for a single run."""
        run_dir  = Path(iid)
        tag_file = run_dir / ".tag"
        cur_tag  = tag_file.read_text(encoding="utf-8").strip() if tag_file.exists() else ""
        dlg = tk.Toplevel(self)
        dlg.title("Edit Tag")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.geometry("300x100")
        dlg.transient(self)

        tk.Label(dlg, text="Tag:", font=FONT_UI, bg=BG, fg=FG).pack(padx=12, pady=(12, 0))
        var = tk.StringVar(value=cur_tag)
        entry = tk.Entry(dlg, textvariable=var, font=FONT_MONO, bg=BG2, fg=FG,
                         insertbackground=FG, relief="flat", width=28)
        entry.pack(padx=12, pady=4)
        entry.select_range(0, "end")
        entry.focus_set()

        def _apply(_evt=None):
            tag = var.get().strip()
            try:
                if tag:
                    tag_file.write_text(tag, encoding="utf-8")
                else:
                    tag_file.unlink(missing_ok=True)
                self.hist_tree.set(iid, "tag", tag)
            except Exception as e:
                messagebox.showerror("Tag error", str(e))
            dlg.destroy()

        entry.bind("<Return>", _apply)
        entry.bind("<Escape>", lambda _e: dlg.destroy())

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(padx=16, pady=(2, 14))
        self._btn(btn_row, "Apply",  _apply,       bg="#1b5e20", fg="#00ff7f").pack(side="left", padx=(0, 6))
        self._btn(btn_row, "Cancel", dlg.destroy,  fg=FG_DIM).pack(side="left")

    def _hist_tag(self) -> None:
        """Batch-tag selected runs."""
        sel = self.hist_tree.selection()
        if not sel:
            messagebox.showinfo("Nothing selected", "Select run(s) to tag.")
            return

        dlg = tk.Toplevel(self)
        dlg.title("Tag Runs")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.geometry("340x110")
        dlg.transient(self)

        tk.Label(dlg, text=f"Tag {len(sel)} run(s):", font=FONT_UI,
                 bg=BG, fg=FG).pack(padx=12, pady=(12, 0))
        var = tk.StringVar()
        entry = tk.Entry(dlg, textvariable=var, font=FONT_MONO, bg=BG2, fg=FG,
                         insertbackground=FG, relief="flat", width=32)
        entry.pack(padx=12, pady=4)
        entry.focus_set()

        def _apply(_evt=None):
            tag = var.get().strip()
            errors: list[str] = []
            for iid in sel:
                run_dir  = Path(iid)
                tag_file = run_dir / ".tag"
                try:
                    if tag:
                        tag_file.write_text(tag, encoding="utf-8")
                    else:
                        tag_file.unlink(missing_ok=True)
                    self.hist_tree.set(iid, "tag", tag)
                except Exception as e:
                    errors.append(f"{Path(iid).name}: {e}")
            dlg.destroy()
            if errors:
                messagebox.showerror("Tag errors", "\n".join(errors))
            self.hist_status.set(
                f"Tagged {len(sel)} run(s) as '{tag}'." if tag
                else f"Cleared tag on {len(sel)} run(s)."
            )

        entry.bind("<Return>", _apply)
        entry.bind("<Escape>", lambda _e: dlg.destroy())

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(padx=16, pady=(2, 14))
        self._btn(btn_row, "Apply",  _apply,      bg="#1b5e20", fg="#00ff7f").pack(side="left", padx=(0, 6))
        self._btn(btn_row, "Cancel", dlg.destroy, fg=FG_DIM).pack(side="left")

    def _hist_send_email(self) -> None:
        """Scan all output dirs, build sidebar+history report, send email."""
        out_dir = self.base_dir / "output"
        if not out_dir.exists():
            messagebox.showinfo("No output folder",
                                f"No output/ directory found under:\n{self.base_dir}")
            return

        to = self.cfg.get("email_to_report", _EMAIL_TO)

        # ── Preview: find latest run per prog_key ─────────────────────────────
        prog_preview: dict = {}
        for d in out_dir.iterdir():
            if not d.is_dir():
                continue
            m = re.search(r'([A-Za-z]\d{2}[A-Za-z])_(\d{8}_\d{6})$', d.name)
            if not m:
                continue
            prog_key = m.group(1).upper()
            ts = m.group(2)
            if prog_key not in prog_preview or ts > prog_preview[prog_key][1]:
                prog_preview[prog_key] = (d, ts)

        if not prog_preview:
            messagebox.showinfo("No runs",
                                f"No run folders found in {out_dir}.")
            return

        sorted_keys = sorted(prog_preview.keys())
        latest_ts = max(v[1] for v in prog_preview.values())
        preview_lines = "\n".join(
            f"  {k}: {prog_preview[k][0].name}"
            for k in sorted_keys
        )
        if not messagebox.askyesno(
            "Send Combined Report",
            f"Scan all output dirs and send combined scan report?\n\n"
            f"Latest per program:\n{preview_lines}\n\nTo: {to}",
        ):
            return

        self.hist_status.set("Building combined report…")
        self.update_idletasks()
        label = self._product_var.get()

        def _send():
            try:
                import tempfile as _tmp
                from datetime import datetime as _dt

                run_ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
                _excl  = self.cfg.get("excluded_keys", [])
                body_html = _build_email_report_html(
                    out_dir, run_ts, excluded_keys=_excl, product_name=label,
                )

                tmp = Path(_tmp.mkdtemp(prefix="nvl_scan_"))
                try:
                    att_name = f"{label} Scan Report {latest_ts}.html"
                    att_path = tmp / att_name
                    att_path.write_text(body_html, encoding="utf-8")

                    send_email(
                        to=to,
                        subject=f"{label} Scan Report — {latest_ts}",
                        body_html=body_html,
                        dry_run=False,
                        attachments=[str(att_path)],
                    )
                    # ── Also save to reports/ ──────────────────────────────
                    _reports_dir = self.base_dir / "reports"
                    _reports_dir.mkdir(parents=True, exist_ok=True)
                    _saved = _reports_dir / f"Scan_Report_{latest_ts}.html"
                    _saved.write_text(body_html, encoding="utf-8")
                    n = len(sorted_keys)
                    self.after(0, lambda: self.hist_status.set(
                        f"Sent to {to}  ({'+'.join(sorted_keys)} — {n} program(s))  •  Saved → {_saved.name}"))
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)

            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Send failed", str(e)))
                self.after(0, lambda: self.hist_status.set("Send failed."))

        threading.Thread(target=_send, daemon=True).start()

    def _hist_save_report(self) -> None:
        """Build combined scan report HTML and save to reports/ folder — no email."""
        out_dir = self.base_dir / "output"
        if not out_dir.exists():
            messagebox.showinfo("No output folder",
                                f"No output/ directory found under:\n{self.base_dir}")
            return
        reports_dir = self.base_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        self.hist_status.set("Building report…")
        self.update_idletasks()
        label = self._product_var.get()

        def _save():
            try:
                from datetime import datetime as _dt
                run_ts  = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
                ts_file = _dt.now().strftime("%Y%m%d_%H%M%S")
                _excl   = self.cfg.get("excluded_keys", [])
                body    = _build_email_report_html(out_dir, run_ts, excluded_keys=_excl, product_name=label)
                out_path = reports_dir / f"Scan_Report_{ts_file}.html"
                out_path.write_text(body, encoding="utf-8")
                def _done():
                    self.hist_status.set(f"Saved \u2192 {out_path.name}")
                    _open_html(out_path)
                self.after(0, _done)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Save failed", str(e)))
                self.after(0, lambda: self.hist_status.set("Save failed."))

        threading.Thread(target=_save, daemon=True).start()

    def _hist_cleanup_auto(self) -> None:
        """Preview then delete oldest run dirs per letter, keeping the last N untagged runs.
        Shows a preview with folder sizes before confirming. Tagged runs are always preserved.
        """
        import tkinter.scrolledtext as _st

        keep = max(1, self._keep_runs_var.get())
        output_dir = self.base_dir / "output"
        if not output_dir.exists():
            messagebox.showinfo("Cleanup", "No output/ folder found.")
            return

        # base_dir already scopes to this product's output/ — group any run folder
        pattern = re.compile(r'([A-Za-z]\d{2}[A-Za-z])_\d{8}_\d{6}$')
        letter_groups: dict[str, list] = {}
        for d in output_dir.iterdir():
            if d.is_dir():
                m = pattern.search(d.name)
                if m:
                    letter = m.group(1).upper()  # full group key e.g. H61G
                    letter_groups.setdefault(letter, []).append(d)

        # Build candidates list
        candidates: list[Path] = []
        for letter in sorted(letter_groups):
            folders = sorted(letter_groups[letter], key=lambda d: d.name, reverse=True)
            kept = 0
            for d in folders:
                if (d / ".tag").exists():
                    continue
                if kept < keep:
                    kept += 1
                    continue
                candidates.append(d)

        if not candidates:
            messagebox.showinfo("Cleanup",
                                f"Nothing to delete — already at or under {keep} run(s) per letter.")
            return

        # ── Preview dialog ────────────────────────────────────────────────────
        dlg = tk.Toplevel(self)
        dlg.title("Cleanup Old Runs — Preview")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.transient(self)
        dlg.minsize(480, 320)

        tk.Label(dlg, text="Folders to be deleted", font=FONT_TITLE,
                 bg=BG, fg=ACCENT).pack(padx=16, pady=(14, 2))
        tk.Label(dlg,
                 text=f"Keep last {keep} run(s) per letter  ·  tagged runs always preserved",
                 font=("Segoe UI", 9), bg=BG, fg=FG_DIM).pack(padx=16)

        frm = tk.LabelFrame(dlg, text=f"  {len(candidates)} folder(s) will be deleted  ",
                            font=FONT_UI, bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm.pack(fill="both", expand=True, padx=16, pady=(8, 4))
        txt = _st.ScrolledText(frm, height=10, font=FONT_MONO,
                               bg=BG2, fg=FG, relief="flat", state="normal", wrap="none")
        total_sz = 0
        for d in candidates:
            sz = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            total_sz += sz
            sz_str = (f"{sz / 1_048_576:.1f} MB" if sz >= 1_048_576
                      else f"{sz // 1024} KB")
            txt.insert("end", f"  {d.name}  ({sz_str})\n")
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=4, pady=4)

        total_str = (f"{total_sz / 1_048_576:.1f} MB" if total_sz >= 1_048_576
                     else f"{total_sz // 1024} KB")
        tk.Label(dlg, text=f"Total freed: ~{total_str}",
                 font=("Segoe UI", 9), bg=BG, fg=FG_DIM).pack(padx=16, pady=(0, 4))

        def _on_confirm() -> None:
            # Persist keep_runs to config
            self.cfg["keep_runs"] = keep
            _save_config(self.cfg_path, self.cfg)

            errors: list[str] = []
            deleted = 0
            for d in candidates:
                try:
                    shutil.rmtree(d)
                    if self.hist_tree.exists(str(d)):
                        self.hist_tree.delete(str(d))
                    deleted += 1
                except Exception as e:
                    errors.append(f"{d.name}: {e}")

            dlg.destroy()
            if errors:
                messagebox.showerror("Cleanup errors", "\n".join(errors))
            self.hist_status.set(
                f"Cleanup: deleted {deleted} run(s)."
                + (f"  {len(errors)} error(s)." if errors else "")
            )
            try:
                self._refresh_data()
            except Exception:
                pass

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(padx=16, pady=(4, 14))
        self._btn(btn_row, "🗑 Delete Old Runs", _on_confirm,
                  bg="#7b1c1c", fg="#ffcdd2").pack(side="left", padx=(0, 6))
        self._btn(btn_row, "Cancel", dlg.destroy, fg=FG_DIM).pack(side="left")

    def _hist_delete(self, include_data: bool = False) -> None:
        """Delete selected run folder(s).  When include_data=True also removes
        data/programs/{letter}/ files if no other runs for that letter remain."""
        sel = self.hist_tree.selection()
        if not sel:
            messagebox.showinfo("Nothing selected",
                                "Select one or more run folders to delete.")
            return

        output_dir   = self.base_dir / "output"
        programs_dir = self.base_dir / "data" / "programs"
        sel_set      = set(sel)

        to_delete: list[tuple[Path, list[Path]]] = []
        for iid in sel:
            run_dir    = Path(iid)
            data_files: list[Path] = []
            if include_data:
                km = re.search(r'([A-Za-z]\d{2}[A-Za-z])', run_dir.name, re.IGNORECASE)
                if km:
                    letter   = km.group(1).upper()  # full group key e.g. H61G
                    prog_dir = programs_dir / letter
                    remaining = [
                        d for d in output_dir.iterdir()
                        if d.is_dir()
                        and re.search(re.escape(letter), d.name, re.IGNORECASE)
                        and str(d) not in sel_set
                    ] if output_dir.exists() else []
                    if prog_dir.is_dir() and not remaining:
                        data_files = sorted(f for f in prog_dir.iterdir() if f.is_file())
            to_delete.append((run_dir, data_files))

        lines: list[str] = []
        for run_dir, data_files in to_delete:
            lines.append(f"  Run folder : {run_dir.name}")
            if include_data:
                if data_files:
                    for df in data_files:
                        lines.append(f"  Data file  : {df.name}")
                else:
                    lines.append("  Data file  : (none found)")
            lines.append("")

        action = "output folder(s) + ALL data file(s)" if include_data else "output folder(s) only"
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Permanently delete {len(sel)} run(s) — {action}?\n\n"
            + "\n".join(lines).rstrip(),
        ):
            return

        errors: list[str] = []
        deleted = 0
        for run_dir, data_files in to_delete:
            try:
                shutil.rmtree(run_dir)
                self.hist_tree.delete(str(run_dir))
                deleted += 1
            except Exception as e:
                errors.append(f"{run_dir.name}: {e}")
            if include_data:
                for df in data_files:
                    if df.exists():
                        try:
                            df.unlink()
                        except Exception as e:
                            errors.append(f"{df.name}: {e}")

        if errors:
            messagebox.showerror("Delete errors", "\n".join(errors))
        suffix = " + data file(s)" if include_data else ""
        self.hist_status.set(
            f"Deleted {deleted} run(s){suffix}." +
            (f"  {len(errors)} error(s)." if errors else "")
        )
        try:
            self._refresh_data()
        except Exception:
            pass

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 3 — Data Files
    # ═════════════════════════════════════════════════════════════════════════

    def _build_data_tab(self) -> None:
        p = self._tab_data

        tb = tk.Frame(p, bg=BG)
        tb.pack(fill="x", padx=12, pady=(10, 4))
        self._btn(tb, "↺ Refresh",         self._refresh_data).pack(side="left", padx=(0, 6))
        self._btn(tb, "✔ Select All",      self._data_select_all).pack(side="left", padx=(0, 6))
        self._btn(tb, "✘ Clear",           self._data_clear_sel).pack(side="left", padx=(0, 6))
        self._btn(tb, "🗑 Delete Selected", self._data_delete,
                  bg="#7b1c1c", fg="#ffcdd2").pack(side="right")

        frm_raw = tk.LabelFrame(
            p, text="  Raw AQUA Pull Snapshots  (data/raw/)  ",
            font=FONT_UI, bg=BG, fg=ACCENT, bd=1, relief="groove",
        )
        frm_raw.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.raw_tree = self._make_tree(frm_raw, ("filename", "size", "modified"))
        self.raw_tree.heading("filename", text="File",     anchor="w")
        self.raw_tree.heading("size",     text="Size",     anchor="e")
        self.raw_tree.heading("modified", text="Modified", anchor="w")
        self.raw_tree.column("filename", width=400, stretch=True)
        self.raw_tree.column("size",     width=90,  stretch=False, anchor="e")
        self.raw_tree.column("modified", width=140, stretch=False)

        frm_prog = tk.LabelFrame(
            p, text="  Per-Program Data Cache  (data/programs/)  ",
            font=FONT_UI, bg=BG, fg=ACCENT, bd=1, relief="groove",
        )
        frm_prog.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.prog_tree = self._make_tree(frm_prog, ("filename", "letter", "size", "modified"))
        self.prog_tree.heading("filename", text="File",     anchor="w")
        self.prog_tree.heading("letter",   text="Letter",   anchor="w")
        self.prog_tree.heading("size",     text="Size",     anchor="e")
        self.prog_tree.heading("modified", text="Modified", anchor="w")
        self.prog_tree.column("filename", width=340, stretch=True)
        self.prog_tree.column("letter",   width=70,  stretch=False)
        self.prog_tree.column("size",     width=90,  stretch=False, anchor="e")
        self.prog_tree.column("modified", width=140, stretch=False)

        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x", padx=12, pady=(0, 8))
        self.data_status = tk.StringVar()
        tk.Label(bot, textvariable=self.data_status, font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM).pack(side="left")

        self._refresh_data()

    def _make_tree(self, parent: tk.Widget, cols: tuple) -> ttk.Treeview:
        frm  = tk.Frame(parent, bg=BG)
        frm.pack(fill="both", expand=True, padx=6, pady=4)
        tree = ttk.Treeview(frm, columns=cols, show="headings", selectmode="extended")
        vsb  = ttk.Scrollbar(frm, orient="vertical",   command=tree.yview)
        hsb  = ttk.Scrollbar(frm, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        vsb.pack(side="right",  fill="y")
        tree.pack(side="left",  fill="both", expand=True)
        return tree

    def _refresh_data(self) -> None:
        self.raw_tree.delete(*self.raw_tree.get_children())

        data_dir = self.base_dir / "data"
        prog_dir = data_dir / "programs"

        # Raw snapshots: data/raw/ (primary location)
        raw_files: list[Path] = []
        raw_dir = data_dir / "raw"
        if raw_dir.exists():
            for f in raw_dir.iterdir():
                if f.is_file():
                    raw_files.append(f)
        # Also check data/programs/{letter}/ for raw_* files (older layout)
        if prog_dir.exists():
            for sub in sorted(prog_dir.iterdir()):
                if sub.is_dir():
                    for f in sub.iterdir():
                        if f.is_file() and f.stem.startswith("raw_"):
                            raw_files.append(f)
        # Legacy: any files directly in data/
        if data_dir.exists():
            for f in data_dir.iterdir():
                if f.is_file():
                    raw_files.append(f)
        raw_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        for f in raw_files:
            display = f"{f.parent.name}/{f.name}" if f.parent != data_dir else f.name
            self.raw_tree.insert("", "end", iid=str(f),
                                 values=(display, _fmt_size(f.stat().st_size), _mtime_str(f)))

        # Per-program accumulated archives: non-raw *.7z / *.gz
        self.prog_tree.delete(*self.prog_tree.get_children())
        prog_count = 0
        if prog_dir.exists():
            for sub in sorted(prog_dir.iterdir()):
                if sub.is_dir():
                    files = sorted(
                        [f for f in sub.iterdir()
                         if f.is_file()
                         and not f.stem.startswith("raw_")
                         and f.suffix in (".7z", ".gz")],
                        key=lambda f: f.stat().st_mtime, reverse=True,
                    )
                    for f in files:
                        self.prog_tree.insert("", "end", iid=str(f),
                                             values=(f.name, sub.name,
                                                     _fmt_size(f.stat().st_size),
                                                     _mtime_str(f)))
                        prog_count += 1

        self.data_status.set(
            f"{len(raw_files)} raw snapshot(s)   |   {prog_count} program cache file(s)")

    def _data_select_all(self) -> None:
        self.raw_tree.selection_set(self.raw_tree.get_children())
        self.prog_tree.selection_set(self.prog_tree.get_children())

    def _data_clear_sel(self) -> None:
        self.raw_tree.selection_remove(self.raw_tree.get_children())
        self.prog_tree.selection_remove(self.prog_tree.get_children())

    def _data_delete(self) -> None:
        sel = list(self.raw_tree.selection()) + list(self.prog_tree.selection())
        if not sel:
            messagebox.showinfo("Nothing selected", "Select files to delete.")
            return
        names = [Path(s).name for s in sel]
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Permanently delete {len(sel)} file(s)?\n\n" + "\n".join(names),
        ):
            return
        errors = []
        for iid in sel:
            try:
                Path(iid).unlink()
                for tree in (self.raw_tree, self.prog_tree):
                    try:
                        tree.delete(iid)
                    except Exception:
                        pass
            except Exception as e:
                errors.append(f"{Path(iid).name}: {e}")
        if errors:
            messagebox.showerror("Delete errors", "\n".join(errors))
        self.data_status.set(
            f"Deleted {len(sel) - len(errors)} file(s)." +
            (f"  {len(errors)} error(s)." if errors else "")
        )

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 4 — Schedule
    # ═════════════════════════════════════════════════════════════════════════

    def _build_schedule_tab(self) -> None:
        import sys as _sys
        p   = self._tab_schedule
        pad = dict(padx=14, pady=6)

        _python = _PYTHON
        _script = str(_SELF)

        frm_st = tk.LabelFrame(p, text="  Task Status  ", font=FONT_UI,
                               bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_st.pack(fill="x", **pad)

        self._sched_dot   = tk.Label(frm_st, text="●", font=("Segoe UI", 14), bg=BG, fg=FG_DIM)
        self._sched_dot.grid(row=0, column=0, padx=(10, 4), pady=6, sticky="w")
        self._sched_state = tk.Label(frm_st, text="Checking…", font=FONT_GROUP, bg=BG, fg=FG_DIM)
        self._sched_state.grid(row=0, column=1, sticky="w", pady=6)
        self._btn(frm_st, "↺ Refresh", self._sched_refresh
                  ).grid(row=0, column=5, padx=10, pady=4, sticky="e")

        for col, lbl, attr in [
            (0, "Next Run:",    "_sched_next"),
            (1, "Last Run:",    "_sched_last"),
            (2, "Last Result:", "_sched_result"),
        ]:
            tk.Label(frm_st, text=lbl, font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                     ).grid(row=1, column=col * 2,
                            sticky="w", padx=(10 if col == 0 else 4, 0), pady=(0, 6))
            lv = tk.Label(frm_st, text="—", font=FONT_MONO, bg=BG, fg=FG)
            lv.grid(row=1, column=col * 2 + 1, sticky="w", padx=(4, 14), pady=(0, 6))
            setattr(self, attr, lv)
        frm_st.columnconfigure(5, weight=1)

        frm_cfg = tk.LabelFrame(p, text="  Configuration  ", font=FONT_UI,
                                bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_cfg.pack(fill="x", **pad)

        self._sched_task_name_var = tk.StringVar(value=self._task_name)
        for row, lbl, var_or_val in [
            (0, "Task name:", self._sched_task_name_var),
            (1, "Script:",    _script),
            (2, "Python:",    _python),
        ]:
            tk.Label(frm_cfg, text=lbl, font=FONT_UI, bg=BG, fg=FG_DIM
                     ).grid(row=row, column=0, sticky="w", padx=(10, 4), pady=3)
            if isinstance(var_or_val, tk.StringVar):
                tk.Label(frm_cfg, textvariable=var_or_val, font=FONT_MONO, bg=BG, fg=ACCENT,
                         anchor="w", wraplength=520
                         ).grid(row=row, column=1, sticky="w", padx=(0, 10), pady=3)
            else:
                tk.Label(frm_cfg, text=var_or_val, font=FONT_MONO, bg=BG, fg=FG,
                         anchor="w", wraplength=520
                         ).grid(row=row, column=1, sticky="w", padx=(0, 10), pady=3)
        frm_cfg.columnconfigure(1, weight=1)

        time_row = tk.Frame(frm_cfg, bg=BG)
        time_row.grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 8))
        tk.Label(time_row, text="Daily run time:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).pack(side="left", padx=(0, 8))
        self._sched_hour = ttk.Spinbox(time_row, from_=0, to=23, width=4,
                                       format="%02.0f", font=FONT_MONO)
        self._sched_hour.set("07")
        self._sched_hour.pack(side="left")
        tk.Label(time_row, text=":", font=FONT_MONO, bg=BG, fg=FG).pack(side="left", padx=2)
        self._sched_min = ttk.Spinbox(time_row, from_=0, to=59, width=4,
                                      format="%02.0f", font=FONT_MONO)
        self._sched_min.set("00")
        self._sched_min.pack(side="left")
        tk.Label(time_row, text="(daily, runs while logged in)",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM).pack(side="left", padx=(8, 0))

        btn_row = tk.Frame(p, bg=BG)
        btn_row.pack(fill="x", padx=14, pady=(2, 4))
        self._btn(btn_row, "✔ Create / Update", self._sched_create,
                  bg="#1b5e20", fg="#c8e6c9").pack(side="left", padx=(0, 8))
        self._btn(btn_row, "⏵ Run Now", self._sched_run_now,
                  bg="#1a3a5c", fg="#90caf9").pack(side="left", padx=(0, 8))
        self._btn(btn_row, "⟳ Rerun (Cached)", self._sched_rerun,
                  bg="#2d3a1e", fg="#b9f0a0").pack(side="left", padx=(0, 8))
        self._btn(btn_row, "🗑 Remove Task", self._sched_remove,
                  bg="#7b1c1c", fg="#ffcdd2").pack(side="left")

        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x", padx=14, pady=(0, 10))
        self._sched_status = tk.StringVar()
        tk.Label(bot, textvariable=self._sched_status,
                 font=("Segoe UI", 9), bg=BG, fg=GREEN).pack(side="left")

        self._sched_refresh()

    def _sched_refresh(self) -> None:
        import subprocess as _sp, csv as _csv, io as _io
        try:
            r = _sp.run(
                ["schtasks", "/query", "/tn", self._task_name, "/fo", "csv", "/v"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                self._sched_dot.config(fg=FG_DIM)
                self._sched_state.config(text="Not scheduled", fg=FG_DIM)
                for attr in ("_sched_next", "_sched_last", "_sched_result"):
                    getattr(self, attr).config(text="—", fg=FG)
                return
            lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
            if len(lines) >= 2:
                rows = list(_csv.reader(_io.StringIO("\n".join(lines))))
                hdr  = rows[0]
                dat  = rows[1] if len(rows) > 1 else []
                def _col(name: str) -> str:
                    try:
                        return dat[hdr.index(name)] if name in hdr else "—"
                    except Exception:
                        return "—"
                status   = _col("Status")
                next_run = _col("Next Run Time")
                last_run = _col("Last Run Time")
                last_res = _col("Last Result")

                colour = GREEN if status in ("Ready", "Running") else \
                         AMBER if status == "Disabled"            else FG_DIM
                self._sched_dot.config(fg=colour)
                self._sched_state.config(text=status, fg=colour)
                self._sched_next.config(text=next_run if next_run not in ("N/A", "") else "—")
                self._sched_last.config(text=last_run if last_run not in ("N/A", "") else "—")
                res_fg = GREEN if last_res in ("0", "0x0") else \
                         RED   if last_res not in ("—", "", "267011") else FG
                self._sched_result.config(text=last_res, fg=res_fg)
            else:
                self._sched_state.config(text="Unknown", fg=AMBER)
        except Exception as e:
            self._sched_state.config(text="Error", fg=RED)
            self._sched_status.set(f"Error querying task: {e}")

    def _write_launcher_bat(self) -> Path:
        """Write a .bat launcher so /tr stays under 261 chars."""
        import sys as _sys
        safe_label = re.sub(r'[^\w]', '_', self._task_name)
        _LAUNCH_BAT_DIR.mkdir(parents=True, exist_ok=True)
        bat_path   = _LAUNCH_BAT_DIR / f"_launch_{safe_label}.bat"
        product    = self._product_var.get()
        line = (f'"{_PYTHON}" "{_SELF}"'
                f' --base-dir "{self.base_dir}" --product "{product}"')
        bat_path.write_text(f"@echo off\r\n{line}\r\n", encoding="utf-8")
        return bat_path

    def _sched_create(self) -> None:
        import sys as _sys, subprocess as _sp
        hh = self._sched_hour.get().zfill(2)
        mm = self._sched_min.get().zfill(2)
        if (not hh.isdigit() or not mm.isdigit()
                or not (0 <= int(hh) <= 23)
                or not (0 <= int(mm) <= 59)):
            messagebox.showerror("Invalid time", f"Invalid time value: {hh}:{mm}")
            return
        tr  = (f'"{_PYTHON}" "{_SELF}"'
               f' --base-dir "{self.base_dir}" --product "{self._product_var.get()}"')
        if len(tr) > 261:
            bat = self._write_launcher_bat()
            tr  = f'cmd /c "{bat}"'
        if len(tr) > 261:
            messagebox.showerror(
                "Path too long",
                f"Command is still too long ({len(tr)} chars).\n"
                f"Move the project to a shorter path.")
            return
        cmd = ["schtasks", "/create", "/tn", self._task_name,
               "/tr", tr, "/sc", "daily", "/st", f"{hh}:{mm}", "/f"]
        try:
            r = _sp.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                self._sched_status.set(f"Task '{self._task_name}' created — runs daily at {hh}:{mm}.")
            else:
                messagebox.showerror("schtasks failed",
                                     r.stderr.strip() or r.stdout.strip() or "Unknown error")
            self._sched_refresh()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _sched_run_now(self) -> None:
        import sys as _sys, subprocess as _sp
        if not messagebox.askyesno("Run Now",
                                   f'Start "{self._task_name}" immediately?\n\n'
                                   "This kicks off a full AQUA pull + pipeline run.\n"
                                   "A console window will open showing live progress."):
            return
        script = str(_SELF)
        try:
            _sp.Popen(
                [_PYTHON, script, "--base-dir", str(self.base_dir),
                 "--product", self._product_var.get()],
                creationflags=_sp.CREATE_NEW_CONSOLE,
            )
            self._sched_status.set("Started in new console window.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _sched_rerun(self) -> None:
        """Rerun dialog: optional --keys + --local-csv, then run --force."""
        import sys as _sys, subprocess as _sp

        dlg = tk.Toplevel(self)
        dlg.title("Rerun (Cached)")
        dlg.configure(bg=BG)
        dlg.resizable(True, True)
        dlg.geometry("820x580")
        dlg.transient(self)

        top_bar = tk.Frame(dlg, bg=BG)
        top_bar.pack(fill="x", padx=12, pady=(10, 6))
        status_var = tk.StringVar(value="Ready.")
        tk.Label(top_bar, textvariable=status_var, font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM).pack(side="right", padx=(8, 0))

        opts = tk.Frame(dlg, bg=BG)
        opts.pack(fill="x", padx=12, pady=(0, 4))

        tk.Label(opts, text="--keys filter:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        keys_var = tk.StringVar()
        tk.Entry(opts, textvariable=keys_var, font=FONT_MONO,
                 bg=BG2, fg=FG, insertbackground=FG, relief="flat", width=30
                 ).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=4)
        tk.Label(opts, text="e.g. 119325 or 61C or XH61H  (blank = all)",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                 ).grid(row=0, column=2, sticky="w", pady=4)

        tk.Label(opts, text="--local-csv:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).grid(row=1, column=0, sticky="w", padx=(0, 6), pady=4)
        csv_var = tk.StringVar()
        tk.Entry(opts, textvariable=csv_var, font=FONT_MONO,
                 bg=BG2, fg=FG, insertbackground=FG, relief="flat", width=30
                 ).grid(row=1, column=1, sticky="ew", padx=(0, 4), pady=4)

        def _browse():
            from tkinter import filedialog
            f = filedialog.askopenfilename(
                title="Select Scan CSV / 7z",
                filetypes=[("Data files", "*.csv *.CSV *.csv.gz *.7z"), ("All", "*.*")],
                initialdir=str(self.base_dir / "data"),
            )
            if f:
                csv_var.set(f)
        self._btn(opts, "Browse", _browse, bg=BG3
                  ).grid(row=1, column=2, sticky="w", pady=4)
        tk.Label(opts, text="(blank = use cached programs/*.7z)",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                 ).grid(row=1, column=3, sticky="w", padx=(6, 0), pady=4)
        opts.columnconfigure(1, weight=1)

        log_frm = tk.Frame(dlg, bg=BG)
        log_frm.pack(fill="both", expand=True, padx=12, pady=(4, 8))
        log_txt = tk.Text(log_frm, font=FONT_MONO, bg="#0d1b2a", fg="#c8e6c9",
                          insertbackground=FG, relief="flat", wrap="none", state="disabled")
        log_vsb = ttk.Scrollbar(log_frm, orient="vertical",   command=log_txt.yview)
        log_hsb = ttk.Scrollbar(log_frm, orient="horizontal", command=log_txt.xview)
        log_txt.configure(yscrollcommand=log_vsb.set, xscrollcommand=log_hsb.set)
        log_txt.tag_config("err",  foreground="#ef9a9a")
        log_txt.tag_config("warn", foreground=AMBER)
        log_txt.tag_config("ok",   foreground=GREEN)
        log_hsb.pack(side="bottom", fill="x")
        log_vsb.pack(side="right",  fill="y")
        log_txt.pack(side="left",   fill="both", expand=True)

        _proc: list[_sp.Popen | None] = [None]
        _running = [False]
        start_btn_ref: list = [None]

        def _append(line: str) -> None:
            lo = line.lower()
            tag = ""
            if any(w in lo for w in ("error", "traceback", "failed", "exception")):
                tag = "err"
            elif "warning" in lo:
                tag = "warn"
            elif any(w in lo for w in (" ok ", "→ ok", "sent", "email sent")):
                tag = "ok"
            log_txt.config(state="normal")
            log_txt.insert("end", line + "\n", tag)
            log_txt.see("end")
            log_txt.config(state="disabled")

        def _do_run():
            keys_val = keys_var.get().strip()
            csv_val  = csv_var.get().strip()
            cmd = [_PYTHON, str(_SELF), "--force",
                   "--base-dir", str(self.base_dir),
                   "--product", self._product_var.get()]
            if keys_val:
                cmd += ["--keys", keys_val]
            if csv_val:
                cmd += ["--local-csv", csv_val]
            _append("$ " + " ".join(cmd))
            _append("-" * 60)
            try:
                proc = _sp.Popen(
                    cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                )
                _proc[0] = proc
                for line in proc.stdout:
                    dlg.after(0, _append, line.rstrip())
                proc.wait()
                rc = proc.returncode
                dlg.after(0, _append, "-" * 60)
                dlg.after(0, _append, f"Exit code: {rc}")
                dlg.after(0, status_var.set,
                          f"Done — exit {rc}" if rc == 0 else f"FAILED (exit {rc})")
            except Exception as exc:
                dlg.after(0, _append, f"ERROR: {exc}")
                dlg.after(0, status_var.set, "Error launching process.")
            finally:
                _running[0] = False
                dlg.after(0, lambda: start_btn_ref[0].config(
                    text="▶ Start", bg="#00c853", fg="#002200", command=_start))

        def _start():
            if _running[0]:
                return
            _running[0] = True
            log_txt.config(state="normal")
            log_txt.delete("1.0", "end")
            log_txt.config(state="disabled")
            status_var.set("Running…")
            start_btn_ref[0].config(text="Running…", bg=AMBER, fg=BG, command=lambda: None)
            threading.Thread(target=_do_run, daemon=True).start()

        start_btn = self._btn(top_bar, "▶ Start", _start, bg="#00c853", fg="#002200")
        start_btn.pack(side="left", padx=(0, 8))
        start_btn_ref[0] = start_btn
        self._btn(top_bar, "✕ Close", dlg.destroy, fg=FG_DIM).pack(side="left")

    def _sched_remove(self) -> None:
        import subprocess as _sp
        if not messagebox.askyesno("Remove Task",
                                   f'Delete scheduled task "{self._task_name}"?'):
            return
        try:
            r = _sp.run(["schtasks", "/delete", "/tn", self._task_name, "/f"],
                        capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                self._sched_status.set(f"Task '{self._task_name}' removed.")
            else:
                msg = r.stderr.strip() or r.stdout.strip()
                if "cannot find" in msg.lower():
                    self._sched_status.set(f"Task '{self._task_name}' was not scheduled.")
                else:
                    messagebox.showerror("schtasks /delete failed",
                                         msg or "Unknown error")
            self._sched_refresh()
        except Exception as e:
            messagebox.showerror("Error", str(e))


# ─────────────────────────────────────────────────────────────────────────────

def _write_startup_bat() -> None:
    """Regenerate all per-product launcher .bat files on startup."""
    cfg = _load_config(_CFG_DIR / _CFG_NAME)
    _LAUNCH_BAT_DIR.mkdir(parents=True, exist_ok=True)
    for product_name, prod_cfg in cfg.get("products", {}).items():
        # must match AutomationManager._task_name's naming scheme or a stale duplicate .bat gets regenerated
        safe_label   = re.sub(r'[^\w]', '_', f"Scan Automation [{product_name}]")
        bat_path     = _LAUNCH_BAT_DIR / f"_launch_{safe_label}.bat"
        product_base = prod_cfg.get("base_dir", str(_BASE_DIR))
        line = (f'"{_PYTHON}" "{_SELF}"'
                f' --base-dir "{product_base}" --product "{product_name}"')
        bat_path.write_text(f"@echo off\r\n{line}\r\n", encoding="utf-8")


def main() -> None:
    cfg = _load_config(_CFG_DIR / _CFG_NAME)
    first_product = next(iter(cfg["products"].values()), {})
    base_dir = Path(first_product.get("base_dir", str(_BASE_DIR)))
    # keep bat current so the scheduled task always uses the right path
    _write_startup_bat()
    root = tk.Tk()
    root.title("Scan Dashboard Automation Manager")
    root.configure(bg=BG)
    root.resizable(True, True)
    root.minsize(700, 520)
    root.geometry("860x620")
    AutomationManager(root, base_dir).pack(fill="both", expand=True)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_automation_main()
    else:
        main()
