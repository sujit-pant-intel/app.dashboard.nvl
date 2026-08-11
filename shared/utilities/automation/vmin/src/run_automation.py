"""
run_automation.py  —  VMIN Dashboard Automation
================================================
Full pipeline:
  1. Pull AQUA data for NCXSDJXL0H61* lots (vmin config) → base_dir/data/
  2. Split CSV by (TestProgram, Operation) → per-TP gzip files
  3. For each TP that has new data: run run_vmin.py --headless --json
  4. Send email with results + run log

--local-csv shortcut (two modes, auto-detected):
  • Raw AQUA sort CSV (has Program Name / Operation columns or wide _{op} suffixes)
    → existing split pipeline: per-TP snapshots → decompress → run_vmin.py
  • Pre-processed per-TP vmin CSV (no AQUA markers; JSL only accepts CSV or JMP)
    → direct mode: decompress .gz/.7z/.zip to plain CSV if needed, then
      call run_vmin.py directly — no AQUA split round-trip.

Usage:
  python run_automation.py                    # full live run
  python run_automation.py --dry-run          # show plan, no exec
  python run_automation.py --force            # rerun all TPs regardless of change
  python run_automation.py --local-csv <path> # skip AQUA pull, use existing file
                                              # (<path> may be .csv, .gz, .7z, .zip)
  python run_automation.py --local-csv "C:\\work\\vmin\\data\\*.csv"  # glob

Output layout (under base_dir):
  data/
    NCXSDJXL0H61_<ts>.csv.gz   (raw AQUA pull)
    programs/
      <tp_key>.csv.gz           (per-TP data snapshots)
  output/
    NVL_VMIN_<YYYYMMDD_HHMMSS>/
      <tp_key>/
        _vmin_manifest.json     (written by run_vmin.py)
        *.jmpprj                (JMP project output)
  run_log.html                  (cumulative automation history)
"""
from __future__ import annotations

import argparse
import csv
import gzip
import html as _html_mod
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

# ── Unicode safety ─────────────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Repo layout ────────────────────────────────────────────────────────────────
_HERE      = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent.parent.parent.parent   # app.yield.nvl/

# ── Key paths ─────────────────────────────────────────────────────────────────
_AQUA_CFG   = _REPO_ROOT / "shared" / "setup" / "automation" / "vmin-dashboard" / "NVL_Sort_VMIN - Automation Dashboard.txt"
_RUN_VMIN   = _REPO_ROOT / "code" / "dashboard" / "vmin-dashboard" / "src" / "py" / "run_vmin.py"
_EMAIL_CFG  = _REPO_ROOT / "shared" / "setup" / "automation" / "vmin-dashboard" / "email_config.json"
_RUN_CFG    = _REPO_ROOT / "shared" / "setup" / "automation" / "vmin-dashboard" / "run_config.json"
_7Z_EXE     = Path(r"C:\Program Files\7-Zip\7z.exe")

# ── AQUA executables (GAR first, AMR fallback) ────────────────────────────────
_AQUA_EXE_GAR = r"\\gar.corp.intel.com\ec\proj\ba\aqua\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"
_AQUA_EXE_AMR = r"\\amr.corp.intel.com\ec\proj\fm\MPD\AQUA\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"
_DEFAULT_AQUA_SERVER = "AMR"   # AMR (US region) is preferred; GAR as fallback

# ── Defaults ──────────────────────────────────────────────────────────────────
_BASE_DIR     = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\vmin")
_DATA_DIR     = _BASE_DIR / "data"
_RUN_LOG      = _BASE_DIR / "run_log.html"
_EMAIL_TO     = "sujit.n.pant@intel.com"
_TASK_NAME    = "NVL-BLLC VMIN Automation"
_DEFAULT_DAYS = 7     # raw archive retention in days (1 week); matches AQUA LastNWwsStartDateTime=1


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — AQUA pull
# ─────────────────────────────────────────────────────────────────────────────

def _aqua_report_name(cfg_path: Path) -> str:
    """Read report name from AQUA config file.
    Handles both '@ Report : Name' and legacy 'ReportName = Name' formats.
    """
    try:
        for line in cfg_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r'\s*@\s*Report\s*:\s*(.+)', line, re.IGNORECASE)
            if m:
                return m.group(1).strip()
            m = re.match(r'\s*ReportName\s*=\s*(.+)', line, re.IGNORECASE)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return "NVL_Sort_VMIN"


def _compress_aqua_to_7z(csv_path: Path) -> Path | None:
    """Compress csv_path to .csv.7z alongside it, delete original. Returns .csv.7z path."""
    if not _7Z_EXE.exists():
        _log("  7z.exe not found — skipping compression")
        return None
    out_7z = csv_path.with_suffix(".csv.7z")
    try:
        result = subprocess.run(
            [str(_7Z_EXE), "a", "-t7z", "-mx=5", str(out_7z), str(csv_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0 and out_7z.exists():
            _log(f"  Compressed → {out_7z.name}  ({out_7z.stat().st_size:,} bytes)")
            csv_path.unlink(missing_ok=True)
            return out_7z
        _log(f"  WARNING: 7z compression failed rc={result.returncode}")
    except Exception as e:
        _log(f"  WARNING: _compress_aqua_to_7z: {e}")
    return None


def _rotate_raw_archives(data_dir: Path, keep_days: int, dry_run: bool) -> None:
    """Delete raw AQUA pull archives older than keep_days days.
    Targets:
      data/NVL_VMIN_YYYYMMDD_HHMMSS.*
      data/programs/*/raw_YYYYMMDD_HHMMSS.*
    Per-TP snapshots ({key}.csv.gz) are NOT touched.
    """
    cutoff = datetime.now().timestamp() - keep_days * 86400
    removed = 0
    # Root-level pull files: NVL_VMIN_YYYYMMDD_HHMMSS.*
    for p in data_dir.glob("NVL_VMIN_*"):
        if p.is_file() and p.stat().st_mtime < cutoff:
            _log(f"  [rotate] removing {p.name}")
            if not dry_run:
                p.unlink(missing_ok=True)
            removed += 1
    # Per-program raw archives: programs/*/raw_YYYYMMDD_HHMMSS.*
    for p in data_dir.glob("programs/*/raw_*"):
        if p.is_file() and p.stat().st_mtime < cutoff:
            _log(f"  [rotate] removing {p.parent.name}/{p.name}")
            if not dry_run:
                p.unlink(missing_ok=True)
            removed += 1
    if removed:
        _log(f"  [rotate] removed {removed} archive(s) older than {keep_days} day(s)")
    else:
        _log(f"  [rotate] nothing to remove (keep_days={keep_days})")


def pull_aqua(aqua_exe: str, report_config: Path, data_dir: Path, dry_run: bool,
              aqua_server: str = _DEFAULT_AQUA_SERVER) -> Path | None:
    """Run AquaCmdLine.exe with the repo config. Returns path to the downloaded file."""
    data_dir.mkdir(parents=True, exist_ok=True)
    ts       = _ts()
    out_base = data_dir / f"NVL_VMIN_{ts}"
    out_req  = out_base.with_suffix(".zip")

    report_name = _aqua_report_name(report_config)
    temp_dir    = Path(os.environ.get("TEMP", tempfile.gettempdir()))
    temp_pat    = f"{report_name}*.CSV"

    cmd = [
        aqua_exe,
        "-AquaServer",    aqua_server,
        "-ReportConfig",  str(report_config),
        "-OutputFileName", str(out_req),
    ]

    _log(f"{'DRY-RUN  ' if dry_run else ''}AQUA pull → {out_base}.*")
    _log(f"  Config : {report_config}")
    _log(f"  Server : {aqua_server}")
    _log(f"  CMD    : {' '.join(cmd)}")

    if dry_run:
        _log("  DRY-RUN: skipping AQUA, returning dummy path")
        return out_base.with_suffix(".csv.gz")

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

    # Primary: new CSV/gz written to %TEMP% (AQUA always writes the full parsed
    # output here, regardless of -OutputFileName)
    after_temp = {p.resolve() for p in temp_dir.glob(temp_pat)}
    new_csvs   = sorted(after_temp - before_temp, key=lambda p: p.stat().st_mtime)
    if new_csvs:
        src  = max(new_csvs, key=lambda p: p.stat().st_mtime)
        dest = data_dir / f"NVL_VMIN_{ts}{src.suffix}"
        shutil.copy2(src, dest)
        _log(f"  Output from %TEMP%: {src.name} ({src.stat().st_size:,} bytes) → {dest.name}")
        return dest

    # Cache-hit fallback: AQUA reused its %TEMP% cache and wrote no new file.
    # Use the most-recent matching file in %TEMP% if it was modified within 24h.
    _24h = 24 * 3600
    recent_cached = [
        p for p in temp_dir.glob(temp_pat)
        if (time.time() - p.stat().st_mtime) < _24h
    ]
    if recent_cached:
        src  = max(recent_cached, key=lambda p: p.stat().st_mtime)
        dest = data_dir / f"NVL_VMIN_{ts}{src.suffix}"
        shutil.copy2(src, dest)
        age_min = int((time.time() - src.stat().st_mtime) / 60)
        _log(f"  Output from %TEMP% cache ({age_min}m old): {src.name} "
             f"({src.stat().st_size:,} bytes) → {dest.name}")
        return dest

    # Fallback: file written directly to data_dir (may be partial / no string data)
    written = [p for p in data_dir.glob(f"{out_base.name}*") if p.stat().st_size > 0]
    if written:
        out = max(written, key=lambda p: p.stat().st_mtime)
        _log(f"  Output from data_dir (fallback): {out.name} ({out.stat().st_size:,} bytes)")
        return out

    _log("  ERROR: AQUA produced no output file")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Read & split CSV by (TestProgram, Operation)
# ─────────────────────────────────────────────────────────────────────────────

def _read_aqua_file(path: Path) -> tuple[list[dict], str]:
    """Read an AQUA output file (.csv, .csv.gz, .zip, .7z). Returns (rows, delimiter)."""
    def _inner_from_bytes(raw: bytes) -> str:
        if raw[:6] == b'7z\xbc\xaf\x27\x1c':
            import tempfile, subprocess as _sp
            with tempfile.TemporaryDirectory() as _tmp:
                _tmp_p = Path(_tmp)
                _sp.run([str(_7Z_EXE), "e", str(path), f"-o{_tmp}", "-y"],
                        check=True, capture_output=True)
                for _pat in ("*.csv", "*.csv.gz", "*.zip"):
                    _hits = sorted(_tmp_p.glob(_pat))
                    if _hits:
                        return _inner_from_bytes(_hits[0].read_bytes())
            raise ValueError(f"No CSV/zip/gz found inside {path.name}")
        elif raw[:2] == b'PK':
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = z.namelist()
                pick  = next((n for n in names if n.lower().endswith('.csv')), names[0])
                return _inner_from_bytes(z.read(pick))
        elif raw[:2] == b'\x1f\x8b':
            return _inner_from_bytes(gzip.decompress(raw))
        else:
            return raw.decode("utf-8-sig", errors="replace")

    inner      = _inner_from_bytes(path.read_bytes())
    first_line = inner.split("\n")[0]
    delim      = "\t" if "\t" in first_line else ","
    rows       = list(csv.DictReader(io.StringIO(inner), delimiter=delim))
    return rows, delim


def _write_gz(rows: list[dict], fieldnames: list[str], path: Path) -> None:
    """Write rows as gzip-compressed CSV."""
    buf = io.StringIO()
    w   = csv.DictWriter(buf, fieldnames=fieldnames,
                         extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for row in rows:
        w.writerow({k: row.get(k, "") for k in fieldnames})
    path.write_bytes(gzip.compress(buf.getvalue().encode("utf-8"), compresslevel=6))


def _safe_filename(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', s).strip()


def _prog_group(key: str) -> str:
    """Extract program letter group from a VMIN TP key.
    e.g. 'NCXSDJXL0H61C002620_119325' -> '0H61C'
    Falls back to '0H61X' if no match.
    """
    m = re.search(r'0H61([A-Za-z])', key)
    return f"0H61{m.group(1).upper()}" if m else "0H61X"


def split_by_tp_oper(rows: list[dict]) -> dict[str, tuple[list[dict], list[str]]]:
    """Split AQUA rows by (TestProgram, Operation). Returns {safe_key: (rows, fieldnames)}."""
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

    # ── Wide format ─────────────────────────────────────────────────────────
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

    # ── Tall / single-op format ─────────────────────────────────────────────
    _log("  Tall/single-op format")
    prog_col = next((h for h in headers if h.lower() in
                     ("program name", "testprogram", "test program", "program")), None)
    op_col   = next((h for h in headers if h.lower() == "operation"), None)

    for row in rows:
        prog = (row.get(prog_col) or "").strip() if prog_col else ""
        op   = (row.get(op_col) or (next(iter(op_codes), "unknown"))).strip()
        key  = _safe_filename(f"{prog}_{op}") if prog else f"unknown_{op}"
        if key not in groups:
            groups[key] = ([], list(row.keys()))
        groups[key][0].append(row)

    for key, (rws, _) in groups.items():
        _log(f"    {key}: {len(rws):,} rows")

    return groups


def _lot_wafer_set(rows: list[dict]) -> frozenset:
    """Frozenset of (lot, wafer, date) for change-detection."""
    if not rows:
        return frozenset()
    hdrs      = list(rows[0].keys())
    lot_col   = next((h for h in hdrs if h.lower() in
                      ("lot", "sort_lot", "lot number", "lot_number", "lot id")), None)
    wafer_col = next((h for h in hdrs if h.lower() in
                      ("wafer", "sort_wafer", "wafer number", "wafer_number", "wafer id")), None)
    date_col  = next((h for h in hdrs if h.lower() in
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
    """Maintain data_dir/programs/{prog_group}/{key}.csv.gz.
    Falls back to reading from old flat location for backward compat.
    Returns (gz_path, changed)."""
    prog_group = _prog_group(key)
    sub_dir  = data_dir / "programs" / prog_group
    gz_path  = sub_dir / f"{key}.csv.gz"
    # Backward compat: old flat location before per-program folder restructure
    _old_gz  = data_dir / "programs" / f"{key}.csv.gz"

    if not dry_run:
        sub_dir.mkdir(parents=True, exist_ok=True)

    # Prefer new location; fall back to old flat file for reading
    _read_path = gz_path if gz_path.exists() else (_old_gz if _old_gz.exists() else gz_path)
    try:
        old_rows, old_fields = _read_aqua_file(_read_path)
        old_lw = _lot_wafer_set(old_rows)
        new_lw = _lot_wafer_set(new_rows)
        col_changed = len(old_fields) != len(fieldnames)
        if old_lw == new_lw and not col_changed:
            _log(f"  {key}: unchanged ({len(old_rows):,} rows) — skipping")
            return gz_path, False
        if col_changed:
            _log(f"  {key}: column count changed ({len(old_fields)} → {len(fieldnames)}) → replacing")
        else:
            added   = len(new_lw - old_lw)
            removed = len(old_lw - new_lw)
            _log(f"  {key}: changed (+{added}/-{removed} lot-wafer pairs) → replacing")
    except (FileNotFoundError, OSError):
        _log(f"  {key}: new — creating ({len(new_rows):,} rows)")

    if not dry_run:
        _write_gz(new_rows, fieldnames, gz_path)
        _log(f"    → {gz_path.stat().st_size:,} bytes")
        # Remove stale flat-location file if it exists
        _old_gz.unlink(missing_ok=True)
    else:
        _log(f"    DRY-RUN: would write {gz_path}")

    return gz_path, True


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Run run_vmin.py for each TP
# ─────────────────────────────────────────────────────────────────────────────

def _decompress_gz_to_csv(gz_path: Path, dest_csv: Path) -> None:
    """Decompress a .csv.gz snapshot to a plain CSV file."""
    raw = gz_path.read_bytes()
    # may be .gz or plain CSV (if snapshot was written as plain)
    if raw[:2] == b'\x1f\x8b':
        data = gzip.decompress(raw)
    else:
        data = raw
    dest_csv.write_bytes(data)


def _compress_csv_to_7z(csv_path: Path) -> None:
    """Compress csv_path to .csv.7z alongside it, then delete the plain CSV."""
    if not _7Z_EXE.exists():
        _log(f"  7z.exe not found at {_7Z_EXE} — leaving plain CSV")
        return
    out_7z = csv_path.with_suffix(".csv.7z")
    try:
        result = subprocess.run(
            [str(_7Z_EXE), "a", "-t7z", "-mx=5", str(out_7z), str(csv_path)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0 and out_7z.exists():
            _log(f"  Compressed CSV → {out_7z.name}  ({out_7z.stat().st_size:,} bytes)")
            csv_path.unlink(missing_ok=True)
        else:
            _log(f"  WARNING: 7z compression failed rc={result.returncode} — CSV kept")
    except Exception as e:
        _log(f"  WARNING: _compress_csv_to_7z: {e} — CSV kept")


def _is_aqua_format(rows: list) -> bool:
    """Return True if rows look like raw AQUA sort data.

    AQUA data is in either:
    - Wide format: column names have a _{op_code} suffix, e.g. "Program Name_119325"
    - Tall format: has "Program Name" (or equivalent) AND "Operation" columns

    A per-TP vmin CSV (already split / pre-processed) will NOT have these markers.
    """
    if not rows:
        return False
    hdrs = list(rows[0].keys())
    if any(re.search(r'_\d{5,6}$', h) for h in hdrs):
        return True
    lower = {h.lower() for h in hdrs}
    if 'operation' in lower and any(
            x in lower for x in ('program name', 'testprogram', 'test program', 'program')):
        return True
    return False


def _extract_csv_from_file(src: Path, dest_dir: Path) -> Path:
    """Ensure a plain .csv exists for JMP.

    - If *src* is already a .csv → return it unchanged (no copy).
    - If *src* is .gz / .csv.gz → decompress and write to dest_dir.
    - If *src* is .7z            → extract first .csv member to dest_dir.
    - If *src* is .zip           → extract first .csv member to dest_dir.

    Returns the path to the plain CSV.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    if ext == '.csv':
        return src                     # already plain; use as-is

    # Build output name: strip inner .csv from double-extension (e.g. foo.csv.gz → foo.csv)
    stem = src.stem
    if stem.lower().endswith('.csv'):
        stem = stem[:-4]
    dest = dest_dir / f"{stem}.csv"

    raw = src.read_bytes()
    if raw[:2] == b'\x1f\x8b':
        # gzip-compressed
        dest.write_bytes(gzip.decompress(raw))
    elif raw[:6] == b'7z\xbc\xaf\x27\x1c':
        # 7-Zip archive
        with tempfile.TemporaryDirectory() as _tmp:
            subprocess.run(
                [str(_7Z_EXE), "e", str(src), f"-o{_tmp}", "-y"],
                check=True, capture_output=True)
            csv_files = sorted(Path(_tmp).glob("*.csv"))
            if not csv_files:
                raise RuntimeError(f"No CSV found inside {src.name}")
            dest.write_bytes(csv_files[0].read_bytes())
    elif raw[:2] == b'PK':
        # ZIP archive
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            csv_name = next(
                (n for n in names if n.lower().endswith('.csv')), names[0])
            dest.write_bytes(z.read(csv_name))
    else:
        # Unrecognised — treat as plain text CSV
        dest.write_bytes(raw)

    _log(f"  Extracted CSV: {dest.name}  ({dest.stat().st_size:,} bytes)")
    return dest


def run_vmin_direct(
    csv_path: Path,
    output_dir: Path,
    dry_run: bool,
    split_by_category: bool = False,
) -> tuple[bool, str]:
    """Run run_vmin.py directly on a plain CSV — no AQUA split / gz round-trip.

    Used when *--local-csv* points to a pre-processed per-TP vmin CSV
    (i.e. not raw AQUA sort data).  JSL only accepts CSV or JMP, so the
    caller must pass a plain .csv here.

    Returns (success, output_dir_str).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_str = str(output_dir)

    cfg = {
        "data_file":       str(csv_path),
        "output_dir":      out_str,
        "auto_gen_config": True,
    }
    if split_by_category:
        cfg["split_by_category"] = True
    json_path = output_dir / "input.json"
    if not dry_run:
        json_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    _log(f"  [direct] input.json → {json_path}")

    if dry_run:
        _log(f"  [direct] DRY-RUN: would run run_vmin.py --headless --json {json_path}")
        return True, out_str

    cmd = [sys.executable, str(_RUN_VMIN), "--headless", "--json", str(json_path)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    _log(f"  [direct] Running run_vmin.py → {out_str}")

    try:
        result = subprocess.run(
            cmd, capture_output=False, text=True,
            timeout=7200, env=env, cwd=str(_RUN_VMIN.parent),
        )
        ok = result.returncode == 0
        if not ok:
            _log(f"  [direct] WARNING: run_vmin.py exited rc={result.returncode}")
        return ok, out_str
    except subprocess.TimeoutExpired:
        _log(f"  [direct] ERROR: run_vmin.py timed out")
        return False, out_str


def run_vmin_for_group(
    group_key: str,
    gz_path: Path,
    run_dir: Path,
    dry_run: bool,
    split_by_category: bool = False,
) -> tuple[bool, str]:
    """
    Build input.json for the TP group and call run_vmin.py --headless --json.
    Decompresses the .csv.gz snapshot to a plain CSV for JMP, then copies
    JMP output to the network-share group_dir and cleans up local temp.
    Returns (success, output_dir)  where output_dir is the network-share path.
    """
    # ── Network-share destination (for final output + report links) ───────────
    group_dir = run_dir / group_key
    group_dir.mkdir(parents=True, exist_ok=True)

    # ── Local temp dir (JMP reads/writes here — avoids UNC path mangling) ─────
    _local_tmp = Path(r"C:\Temp\vmin-run") / group_key
    _local_tmp.mkdir(parents=True, exist_ok=True)
    # Use a separate output sub-dir so run_vmin.py's "clear output folder"
    # doesn't delete the input CSV (both used to live in the same dir).
    _local_out = _local_tmp / "out"
    _local_out.mkdir(parents=True, exist_ok=True)

    csv_path = _local_tmp / f"{group_key}.csv"
    if not dry_run:
        _log(f"  [{group_key}] Decompressing → {csv_path}")
        _decompress_gz_to_csv(gz_path, csv_path)

    cfg = {
        "data_file":       str(csv_path),
        "output_dir":      str(_local_out) + "\\",   # local — JMP safe; NOT same dir as csv_path
        "auto_gen_config": True,
    }
    if split_by_category:
        cfg["split_by_category"] = True

    json_path = _local_tmp / "input.json"
    if not dry_run:
        json_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    _log(f"  [{group_key}] input.json → {json_path}")

    output_dir = str(group_dir)   # network path returned to caller

    if dry_run:
        _log(f"  [{group_key}] DRY-RUN: would run run_vmin.py --headless --json {json_path}")
        return True, output_dir

    cmd = [sys.executable, str(_RUN_VMIN), "--headless", "--json", str(json_path)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    _log(f"  [{group_key}] Running run_vmin.py  (local tmp: {_local_tmp})")

    try:
        result = subprocess.run(
            cmd, capture_output=False, text=True,
            timeout=7200, env=env, cwd=str(_RUN_VMIN.parent),
        )
        ok = result.returncode == 0
        if not ok:
            _log(f"  [{group_key}] WARNING: run_vmin.py exited rc={result.returncode}")
    except subprocess.TimeoutExpired:
        _log(f"  [{group_key}] ERROR: run_vmin.py timed out")
        return False, output_dir

    # ── Copy output from local out dir → network share ─────────────────────────
    _log(f"  [{group_key}] Copying output → {group_dir}")
    import shutil as _shutil
    for _f in _local_out.iterdir():
        if _f.is_file():
            try:
                _shutil.copy2(_f, group_dir / _f.name)
            except Exception as _ce:
                _log(f"  [{group_key}] WARNING: could not copy {_f.name}: {_ce}")
        elif _f.is_dir():
            try:
                _dst = group_dir / _f.name
                if _dst.exists():
                    _shutil.rmtree(_dst)
                _shutil.copytree(_f, _dst)
            except Exception as _ce:
                _log(f"  [{group_key}] WARNING: could not copy dir {_f.name}: {_ce}")

    # Clean up local temp (CSV + json + any JMP scratch)
    try:
        _shutil.rmtree(_local_tmp, ignore_errors=True)
    except Exception:
        pass

    return ok, output_dir


# ─────────────────────────────────────────────────────────────────────────────
# Step 3b — Run run_vmin.py from in-memory rows (no data persistence)
# ─────────────────────────────────────────────────────────────────────────────

def run_vmin_from_rows(
    group_key: str,
    rows: list[dict],
    fieldnames: list[str],
    run_dir: Path,
    dry_run: bool,
    split_by_category: bool = False,
) -> tuple[bool, str]:
    """Write rows to a local temp CSV, run run_vmin.py, copy output, delete temp.

    Input data is never persisted to the network share — only the output folder
    is saved.  Equivalent to run_vmin_for_group but takes in-memory rows instead
    of a .gz snapshot on disk.
    """
    group_dir = run_dir / group_key
    group_dir.mkdir(parents=True, exist_ok=True)

    _local_tmp = Path(r"C:\Temp\vmin-run") / group_key
    _local_tmp.mkdir(parents=True, exist_ok=True)
    _local_out = _local_tmp / "out"
    _local_out.mkdir(parents=True, exist_ok=True)

    csv_path = _local_tmp / f"{group_key}.csv"
    if not dry_run:
        _log(f"  [{group_key}] Writing {len(rows):,} rows → {csv_path}")
        buf = io.StringIO()
        w   = csv.DictWriter(buf, fieldnames=fieldnames,
                             extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})
        csv_path.write_text(buf.getvalue(), encoding="utf-8")
        _log(f"  [{group_key}] CSV: {csv_path.stat().st_size:,} bytes")

    cfg = {
        "data_file":       str(csv_path),
        "output_dir":      str(_local_out) + "\\",
        "auto_gen_config": True,
    }
    if split_by_category:
        cfg["split_by_category"] = True

    json_path = _local_tmp / "input.json"
    if not dry_run:
        json_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    _log(f"  [{group_key}] input.json → {json_path}")

    output_dir = str(group_dir)

    if dry_run:
        _log(f"  [{group_key}] DRY-RUN: would run run_vmin.py --headless --json {json_path}")
        return True, output_dir

    cmd = [sys.executable, str(_RUN_VMIN), "--headless", "--json", str(json_path)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    _log(f"  [{group_key}] Running run_vmin.py  (local tmp: {_local_tmp})")

    import shutil as _shutil
    try:
        result = subprocess.run(
            cmd, capture_output=False, text=True,
            timeout=7200, env=env, cwd=str(_RUN_VMIN.parent),
        )
        ok = result.returncode == 0
        if not ok:
            _log(f"  [{group_key}] WARNING: run_vmin.py exited rc={result.returncode}")
    except subprocess.TimeoutExpired:
        _log(f"  [{group_key}] ERROR: run_vmin.py timed out")
        _shutil.rmtree(_local_tmp, ignore_errors=True)
        return False, output_dir

    # ── Copy output from local dir → run_dir/group_key ─────────────────────────
    _log(f"  [{group_key}] Copying output → {group_dir}")
    for _f in _local_out.iterdir():
        if _f.is_file():
            try:
                _shutil.copy2(_f, group_dir / _f.name)
            except Exception as _ce:
                _log(f"  [{group_key}] WARNING: could not copy {_f.name}: {_ce}")
        elif _f.is_dir():
            try:
                _dst = group_dir / _f.name
                if _dst.exists():
                    _shutil.rmtree(_dst)
                _shutil.copytree(_f, _dst)
            except Exception as _ce:
                _log(f"  [{group_key}] WARNING: could not copy dir {_f.name}: {_ce}")

    # Clean up all local temp (CSV + json + JMP scratch) — input data not saved
    _shutil.rmtree(_local_tmp, ignore_errors=True)

    return ok, output_dir


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Build HTML report
# ─────────────────────────────────────────────────────────────────────────────

_REPORT_CSS = """
<style>
body{font-family:Segoe UI,Arial,sans-serif;background:#1a252f;color:#e8f0f7;
     margin:0;padding:16px 28px 60px}
h1{color:#4fc3f7;border-bottom:2px solid #4fc3f7;padding-bottom:8px;margin-bottom:4px}
.ts{color:#90a4ae;font-size:0.85em;margin-top:0}
.ok{color:#66bb6a;font-size:0.85em;font-weight:bold}
.fail{color:#ef5350;font-size:0.85em;font-weight:bold}
table{border-collapse:collapse;width:100%;font-size:0.9em;margin-top:12px}
th{background:#263950;color:#4fc3f7;padding:8px 12px;text-align:left;white-space:nowrap}
td{padding:6px 12px;border-bottom:1px solid #1e3a55;color:#cde}
tr:hover td{background:#1a3050}
a{color:#4fc3f7;text-decoration:none}
a:hover{text-decoration:underline}
</style>
"""


def _build_run_report(
    run_dir: Path,
    results: list[tuple[str, bool, str]],   # (key, ok, output_dir)
    aqua_file: str,
    run_ts: str,
) -> Path:
    """Build report.html inside run_dir. Returns report path."""
    rows_html = ""
    for key, ok, output_dir in results:
        status   = '<span class="ok">&#10004; OK</span>' if ok else '<span class="fail">&#10008; FAILED</span>'

        _m = re.search(r'(\d{8})_(\d{6})', output_dir)
        _ts_label = ""
        if _m:
            _d, _t = _m.group(1), _m.group(2)
            _ts_label = (f"<br><span style='color:#90a4ae;font-size:0.8em'>"
                         f"{_d[:4]}-{_d[4:6]}-{_d[6:]} {_t[:2]}:{_t[2:4]}:{_t[4:]}</span>")

        _od   = Path(output_dir)
        _dash = _od / "vmin_dashboard.html"
        _idx  = _od / "vmin_dashboard" / "vmin_dashboard_index.html"
        if _dash.exists():
            link_html = f'<a href="{_dash.as_uri()}">vmin_dashboard.html</a>'
        elif _idx.exists():
            link_html = f'<a href="{_idx.as_uri()}">vmin_dashboard_index.html</a>'
        else:
            link_html = "—"

        rows_html += (
            f"<tr><td style='font-family:monospace;font-size:0.85em'>{key}{_ts_label}</td>"
            f"<td>{status}</td>"
            f"<td>{link_html}</td></tr>\n"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>NVL816-BLLC VMIN Report — {run_ts}</title>
{_REPORT_CSS}
</head>
<body>
<h1>&#128202;&nbsp;NVL816-BLLC VMIN Automation Report</h1>
<p class="ts">Run: {run_ts} &nbsp;|&nbsp; AQUA: {Path(aqua_file).name}</p>
<table>
  <tr><th>TP Key</th><th>Status</th><th>Dashboard</th></tr>
  {rows_html}
</table>
</body>
</html>
"""
    rpt = run_dir / "report.html"
    rpt.write_text(html, encoding="utf-8")
    _log(f"Report written: {rpt}")
    return rpt

# ─────────────────────────────────────────────────────────────────────────────
# Step 4b — Run log
# ─────────────────────────────────────────────────────────────────────────────

_RUN_LOG_CSS = """
<style>
body{font-family:Segoe UI,Arial,sans-serif;background:#1a252f;color:#e8f0f7;
     margin:0;padding:16px 28px 60px}
h1,h2{color:#4fc3f7}
h2{font-size:1em;border-bottom:1px solid #263950;padding-bottom:4px;margin-top:20px}
.ts{color:#90a4ae;font-size:0.85em}
.ok{color:#66bb6a;font-weight:bold}
.fail{color:#ef5350;font-weight:bold}
table{border-collapse:collapse;width:100%;font-size:0.88em;margin:6px 0 12px}
th{background:#263950;color:#4fc3f7;padding:5px 10px;text-align:left}
td{padding:4px 10px;border-bottom:1px solid #1e3a55;color:#cde}
a{color:#4fc3f7}
</style>
"""

_RUN_LOG_HEADER_TPL = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8">
<title>VMIN Dashboard — Run Log</title>
{css}
</head>
<body>
<h1>VMIN Dashboard — Automation Run Log</h1>
<p class="ts">Auto-generated by run_automation.py &nbsp;|&nbsp;
Updated: <span id="ts">{ts}</span></p>
<!-- RUNS -->
"""

_RUN_LOG_FOOTER = "\n</body>\n</html>\n"


def _make_run_section(
    run_ts: str,
    aqua_file: str,
    results: list[tuple[str, bool, str]],
    report_path: Path | None = None,
) -> str:
    rows_html = ""
    for key, ok, output_dir in results:
        status = '<span class="ok">&#10004; OK</span>' if ok else '<span class="fail">&#10008; FAILED</span>'
        rows_html += f"<tr><td>{key}</td><td>{status}</td><td class='ts'>{output_dir}</td></tr>\n"

    report_link = ""
    if report_path and report_path.exists():
        report_link = f' &nbsp;|&nbsp; <a href="{report_path.as_uri()}">&#128196; Report</a>'

    keys_str = ", ".join(r[0] for r in results)
    return f"""
<h2>Run: {run_ts} &mdash; TPs: {keys_str}</h2>
<p class="ts">AQUA: {Path(aqua_file).name}{report_link}</p>
<table>
  <tr><th>TP Key</th><th>Status</th><th>Output</th></tr>
  {rows_html}
</table>
"""


def update_run_log(
    results: list[tuple[str, bool, str]],
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
        header  = _RUN_LOG_HEADER_TPL.format(
            css=_RUN_LOG_CSS, ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        updated = header + section + _RUN_LOG_FOOTER

    run_log.write_text(updated, encoding="utf-8")
    _log(f"Run log updated: {run_log}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Email
# ─────────────────────────────────────────────────────────────────────────────

_SMTP_SERVER = "smtpauth.intel.com"
_SMTP_PORT   = 587
_SMTP_FROM   = "sujit.n.pant@intel.com"


def _send_via_outlook(to: str, subject: str, body_html: str, attachments: list[str]) -> None:
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
    except Exception as _send_err:
        _log(f"  Outlook COM: Send() raised {_send_err!r} — email likely dispatched.")
    _log("  Email sent via Outlook COM.")


def _send_via_smtp(to: str, subject: str, body_html: str, attachments: list[str]) -> None:
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text      import MIMEText
    from email.mime.base      import MIMEBase
    from email                import encoders

    msg            = MIMEMultipart("mixed")
    msg["From"]    = _SMTP_FROM
    msg["To"]      = to
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


def send_email(
    to: str,
    subject: str,
    body_html: str,
    dry_run: bool,
    attachments: list[str] | None = None,
) -> None:
    _log(f"{'DRY-RUN: ' if dry_run else ''}Sending email → {to}")
    if dry_run:
        _log(f"  Subject : {subject}")
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


def _extract_vmin_alerts(output_dir: str) -> dict:
    """Read _vmin_alerts.json from output_dir.  Returns {} if not present."""
    try:
        p = Path(output_dir) / "_vmin_alerts.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _email_body_html(
    run_ts: str,
    aqua_file: str,
    results: list[tuple[str, bool, str]],
    run_log: Path,
    out_dir: Path | None = None,
) -> str:
    rows = ""
    for key, ok, output_dir in sorted(results, key=lambda r: r[0], reverse=True):
        status = "\u2714 OK" if ok else "\u2716 FAILED"
        color  = "#66bb6a" if ok else "#ef5350"

        _m = re.search(r'(\d{8})_(\d{6})', output_dir)
        _ts_label = ""
        if _m:
            _d, _t = _m.group(1), _m.group(2)
            _ts_label = (f"<br><span style='color:#888;font-size:0.8em'>"
                         f"{_d[:4]}-{_d[4:6]}-{_d[6:]} {_t[:2]}:{_t[2:4]}:{_t[4:]}</span>")
        display_key = f"{key}{_ts_label}"

        # Link to vmin_dashboard.html (single) or index (split); fall back to plain key
        _od = Path(output_dir)
        _dash   = _od / "vmin_dashboard.html"
        _idx    = _od / "vmin_dashboard" / "vmin_dashboard_index.html"
        if _dash.exists():
            link = f'<a href="{_dash.as_uri()}">vmin_dashboard.html</a>'
        elif _idx.exists():
            link = f'<a href="{_idx.as_uri()}">vmin_dashboard_index.html</a>'
        else:
            link = f'<span style="font-family:monospace;font-size:0.9em">{key}</span>'
        rows += (
            f"<tr><td style='font-family:monospace;font-size:0.85em'>{display_key}</td>"
            f"<td style='color:{color};font-weight:bold'>{status}</td>"
            f"<td>{link}</td></tr>\n"
        )

    overall = "OK" if all(r[1] for r in results) else "FAILED"

    # ── All available reports section ─────────────────────────────────────────
    all_reports_html = ""
    _search_dir = out_dir if out_dir is not None else run_log.parent / "output"
    if _search_dir.exists():
        import glob as _glob
        _rpts = sorted(
            _glob.glob(str(_search_dir / "NVL_VMIN_*" / "report.html")),
            key=lambda p: os.path.getmtime(p),
            reverse=True,
        )
        if _rpts:
            _rpt_rows = ""
            for _rp in _rpts:
                _rp = Path(_rp)
                _run_name = _rp.parent.name
                _m2 = re.search(r'(\d{8})_(\d{6})', _run_name)
                _label = _run_name
                if _m2:
                    _d2, _t2 = _m2.group(1), _m2.group(2)
                    _label = f"{_d2[:4]}-{_d2[4:6]}-{_d2[6:]} {_t2[:2]}:{_t2[2:4]}"
                _rpt_rows += (f"<tr><td style='font-family:monospace;font-size:0.85em'>"
                              f"<a href='{_rp.as_uri()}'>{_label}</a></td></tr>\n")
            all_reports_html = f"""
<h3 style="color:#0071c5;margin-top:20px;margin-bottom:4px">All Available Reports</h3>
<table border="1" cellpadding="5" cellspacing="0"
       style="border-collapse:collapse;width:100%;font-size:0.85em">
  <tr style="background:#e3eef8"><th style="text-align:left">Run</th></tr>
  {_rpt_rows}
</table>"""

    # ── Vmin Alerts section (spread > 12% + ADTL violations) ─────────────────
    import html as _html_mod
    all_spread: list[tuple[str, str, float]] = []   # (key, xcat, max_cv)
    all_adtl:   list[tuple[str, str, str, float, float]] = []  # (key, xcat, issue, vmin, limit)
    for _key, _ok, _od in results:
        if not _ok:
            continue
        _alerts = _extract_vmin_alerts(_od)
        for _s in _alerts.get("spread_outliers", []):
            all_spread.append((_key, _s["xcat"], _s["max_cv"]))
        for _a in _alerts.get("adtl_violations", []):
            all_adtl.append((_key, _a["xcat"], _a["issue"], _a["vmin"], _a["limit"]))

    alerts_html = ""
    if all_spread or all_adtl:
        _alert_rows_html = ""
        if all_spread:
            _alert_rows_html += (
                "<tr style='background:#fff3e0'>"
                f"<td colspan='5' style='padding:6px 8px;font-weight:bold;color:#e65100'>"
                f"&#9888; Spread &gt; 12%  ({len(all_spread)} test(s))</td></tr>\n"
                "<tr style='background:#fafafa;font-size:0.8em'>"
                "<th style='padding:4px 8px'>TP Key</th>"
                "<th style='padding:4px 8px'>Test</th>"
                "<th style='padding:4px 8px;text-align:right'>Max Spread%</th>"
                "<td colspan='2'></td></tr>\n"
            )
            for _k, _x, _cv in sorted(all_spread, key=lambda t: -t[2]):
                _alert_rows_html += (
                    f"<tr>"
                    f"<td style='font-family:monospace;font-size:0.8em;padding:3px 8px'>{_html_mod.escape(_k)}</td>"
                    f"<td style='font-size:0.8em;padding:3px 8px;white-space:nowrap'>{_html_mod.escape(_x)}</td>"
                    f"<td style='color:#e65100;font-weight:bold;text-align:right;padding:3px 8px'>{_cv:.2f}%</td>"
                    f"<td colspan='2'></td></tr>\n"
                )
        if all_adtl:
            _alert_rows_html += (
                "<tr style='background:#fce4ec'>"
                f"<td colspan='5' style='padding:6px 8px;font-weight:bold;color:#b71c1c'>"
                f"&#9888; ADTL Violations  ({len(all_adtl)} test(s))</td></tr>\n"
                "<tr style='background:#fafafa;font-size:0.8em'>"
                "<th style='padding:4px 8px'>TP Key</th>"
                "<th style='padding:4px 8px'>Test</th>"
                "<th style='padding:4px 8px'>Issue</th>"
                "<th style='padding:4px 8px;text-align:right'>VMIN (V)</th>"
                "<th style='padding:4px 8px;text-align:right'>Limit (V)</th></tr>\n"
            )
            for _k, _x, _iss, _vm, _lim in all_adtl:
                _alert_rows_html += (
                    f"<tr>"
                    f"<td style='font-family:monospace;font-size:0.8em;padding:3px 8px'>{_html_mod.escape(_k)}</td>"
                    f"<td style='font-size:0.8em;padding:3px 8px;white-space:nowrap'>{_html_mod.escape(_x)}</td>"
                    f"<td style='color:#b71c1c;font-weight:bold;padding:3px 8px'>{_html_mod.escape(_iss)}</td>"
                    f"<td style='text-align:right;padding:3px 8px'>{_vm:.4f}</td>"
                    f"<td style='text-align:right;padding:3px 8px;color:#888'>{_lim:.4f}</td></tr>\n"
                )
        alerts_html = f"""
<h3 style="color:#b71c1c;margin-top:20px;margin-bottom:4px">&#9888; Vmin Alerts</h3>
<table border="1" cellpadding="0" cellspacing="0"
       style="border-collapse:collapse;width:100%;font-size:0.9em">
  {_alert_rows_html}
</table>"""

    return f"""
<html><body style="font-family:Segoe UI,Arial;color:#222;max-width:720px">
<h2 style="color:#0071c5;margin-bottom:4px">NVL816-BLLC Vmin Dashboard \u2014 {overall}</h2>
<p style="color:#555;font-size:0.9em;margin-top:0">{run_ts}</p>
<table border="1" cellpadding="6" cellspacing="0"
       style="border-collapse:collapse;width:100%;font-size:0.9em">
  <tr style="background:#0071c5;color:#fff">
    <th>TP Key</th><th>Status</th><th>Dashboard</th>
  </tr>
  {rows}
</table>
<p style="color:#888;font-size:0.8em;margin-top:12px">
  AQUA: {aqua_file}<br>
  Full history: <a href="{run_log.as_uri()}">run_log.html</a>
</p>
{alerts_html}
{all_reports_html}
</body></html>
"""


def _send_no_new_data_email(base_dir: Path, args) -> None:
    ecfg: dict = {}
    if _EMAIL_CFG.exists():
        try:
            ecfg = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
        except Exception:
            pass
    to = (ecfg.get("email_to_report") or getattr(args, "email", _EMAIL_TO) or _EMAIL_TO)

    last_report_link = ""
    out_dir = base_dir / "output"
    if out_dir.exists():
        for r in sorted(out_dir.iterdir(), reverse=True):
            rpt = r / "report.html"
            if rpt.exists():
                last_report_link = (
                    f'<p>Last report: <a href="{rpt.as_uri()}">{rpt.name}</a> '
                    f'(from run <code>{r.name}</code>)</p>'
                )
                break

    run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = f"""<!DOCTYPE html><html><body style="font-family:sans-serif">
<h2 style="color:#6c3483">NVL VMIN Automation — No Data Returned</h2>
<p>Run at <strong>{run_ts}</strong>: AQUA pull completed but no rows matched the
current filters/program criteria. No VMIN jobs were generated for this run.</p>
{last_report_link}
<hr/><p style="font-size:0.85em;color:#888">Pant, Sujit N — GEMS FTE</p>
</body></html>"""

    send_email(to=to, subject="NVL816-BLLC VMIN Dashboard",
               body_html=body, dry_run=getattr(args, "dry_run", False))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Auto-pull AQUA vmin data, run run_vmin.py per-TP, send email.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--aqua-exe",      default=_AQUA_EXE_AMR)
    ap.add_argument("--aqua-server",   default=_DEFAULT_AQUA_SERVER,
                    help="AquaCmdLine.exe -AquaServer value (e.g. GAR, AMR, ZSC)")
    ap.add_argument("--report-config", default=str(_AQUA_CFG))
    ap.add_argument("--base-dir",      default=str(_BASE_DIR))
    ap.add_argument("--days",          type=int, default=_DEFAULT_DAYS,
                    help="(unused — raw retention is managed manually)")
    ap.add_argument("--local-csv",     default=None,
                    help="Skip AQUA pull; use this existing CSV/gz/zip/7z (glob ok). "
                         "If the file is .gz/.7z/.zip it is decompressed to a plain "
                         "CSV first (JSL accepts CSV or JMP only). A plain .csv is "
                         "passed straight through.")
    ap.add_argument("--force",         action="store_true",
                    help="Kept for backward compat (no-op: all TPs always run)")
    ap.add_argument("--keys",          default=None,
                    help="Comma-separated substrings to filter TP keys "
                         "(e.g. '0H61C,119325'). Only matching keys run.")
    ap.add_argument("--dry-run",       action="store_true")
    ap.add_argument("--email",         default=_EMAIL_TO)
    args = ap.parse_args()

    base_dir = Path(args.base_dir)
    out_dir  = base_dir / "output"
    run_log  = base_dir / "run_log.html"
    ts       = _ts()
    raw_archived_file: Path | None = None

    _log("=" * 65)
    _log(f"run_automation (VMIN)  [{'DRY-RUN' if args.dry_run else 'LIVE'}]")
    _log(f"Base dir   : {base_dir}")
    _log(f"run_vmin   : {_RUN_VMIN}")
    _log("=" * 65)

    # ── 1. Acquire AQUA data ─────────────────────────────────────────────────
    _aqua_tmp: Path | None = None
    if args.local_csv:
        import glob as _glob
        _matches = sorted(_glob.glob(args.local_csv), key=os.path.getmtime)
        if _matches:
            aqua_file = Path(_matches[-1])
            _log(f"Local CSV: {aqua_file}  ({len(_matches)} match(es))")
        elif "*" in args.local_csv or "?" in args.local_csv:
            _log(f"ERROR: no files matched glob: {args.local_csv!r}")
            sys.exit(1)
        else:
            aqua_file = Path(args.local_csv)
            _log(f"Local CSV: {aqua_file}")
    else:
        # Pull AQUA into a temp dir first; archive a copy under base_dir/data/raw.
        _aqua_tmp = Path(tempfile.mkdtemp(prefix="vmin_aqua_"))
        aqua_exe = args.aqua_exe
        if not Path(aqua_exe).exists():
            if Path(_AQUA_EXE_GAR).exists():
                aqua_exe = _AQUA_EXE_GAR
                _log(f"  AMR exe not found, using GAR: {aqua_exe}")
            else:
                _log(f"  WARNING: neither AQUA exe found — attempting AMR anyway")
        aqua_file = pull_aqua(
            aqua_exe=aqua_exe,
            report_config=Path(args.report_config),
            data_dir=_aqua_tmp,
            dry_run=args.dry_run,
            aqua_server=args.aqua_server,
        )
        if aqua_file is None:
            shutil.rmtree(_aqua_tmp, ignore_errors=True)
            _ecfg: dict = {}
            if _EMAIL_CFG.exists():
                try:
                    _ecfg = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
                except Exception:
                    pass
            _to = (_ecfg.get("email_to_alert") or _ecfg.get("email_to_report")
                   or args.email or _EMAIL_TO)
            send_email(
                to=_to,
                subject="NVL816-BLLC VMIN Automation — AQUA PULL FAILED",
                body_html=f"""<html><body style='font-family:sans-serif'>
<h2 style='color:#c0392b'>VMIN Automation: AQUA Pull Failed</h2>
<p>Run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<p>AquaCmdLine.exe produced no output. Check network/AQUA server availability.</p>
<hr/><p style='font-size:0.85em;color:#888'>Pant, Sujit N — GEMS FTE</p>
</body></html>""",
                dry_run=args.dry_run,
            )
            sys.exit(1)

    # Archive this run's raw AQUA pull (one file per run, no dedupe/compare).
    if not args.local_csv:
        raw_dir = base_dir / "data" / "raw"
        raw_ext = "".join(aqua_file.suffixes) or ".csv"
        raw_copy = raw_dir / f"raw_{ts}{raw_ext}"
        if args.dry_run:
            _log(f"  DRY-RUN: would archive raw AQUA file -> {raw_copy}")
        else:
            raw_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(aqua_file, raw_copy)
            _log(f"  Raw AQUA archived: {raw_copy}")
            # Process from the archived copy so persisted input == processed input.
            aqua_file = raw_copy
            raw_archived_file = raw_copy

    # ── 2. Read & detect format ────────────────────────────────────────────────
    _log(f"\nReading: {aqua_file}")
    try:
        rows, _ = _read_aqua_file(aqua_file)
    except Exception as e:
        _log(f"ERROR reading file: {e}")
        if _aqua_tmp:
            shutil.rmtree(_aqua_tmp, ignore_errors=True)
        sys.exit(1)
    col_count = len(rows[0]) if rows else 0
    _log(f"  {len(rows):,} rows,  {col_count} cols")

    # Delete the AQUA pull temp dir — data is now in memory
    if _aqua_tmp:
        shutil.rmtree(_aqua_tmp, ignore_errors=True)
        _log("  AQUA pull temp cleaned up.")

    _direct_mode = not _is_aqua_format(rows)
    results: list[tuple[str, bool, str]] = []
    run_dir  = out_dir / f"NVL_VMIN_{ts}"
    report_path: Path | None = None

    # Load split_by_category from run_config.json
    split_by_cat: bool = False
    if _RUN_CFG.exists():
        try:
            _vc = json.loads(_RUN_CFG.read_text(encoding="utf-8"))
            split_by_cat = bool(_vc.get("split_by_category", False))
        except Exception:
            pass

    if _direct_mode:
        # ── Direct mode (pre-processed per-TP CSV) ────────────────────────────────
        _log("  File is not raw AQUA sort format — direct CSV mode (no TP/op split)")
        if args.local_csv and not args.local_csv.startswith("\\\\"):
            import glob as _glob2
            _lc_matches = sorted(_glob2.glob(args.local_csv), key=os.path.getmtime)
            _lc_file = Path(_lc_matches[-1] if _lc_matches else args.local_csv)
            run_dir = _lc_file.parent / "output" / f"NVL_VMIN_{ts}"
            _log(f"  Direct mode output → {run_dir}")
        run_dir.mkdir(parents=True, exist_ok=True)
        _csv_work   = run_dir / "_direct_input"
        csv_for_jmp = _extract_csv_from_file(aqua_file, _csv_work)
        _log(f"  CSV for JMP: {csv_for_jmp}")
        _label = csv_for_jmp.stem or "direct"
        _log(f"\n--- {_label} ---")
        ok, output_dir = run_vmin_direct(csv_for_jmp, run_dir / _label, args.dry_run,
                                         split_by_category=split_by_cat)
        results.append((_label, ok, output_dir))
        report_path = _build_run_report(
            run_dir=run_dir, results=results,
            aqua_file=str(aqua_file), run_ts=ts.replace("_", " "))
        update_run_log(results=results, aqua_file=str(aqua_file),
                       run_log=run_log, dry_run=args.dry_run, report_path=report_path)

    else:
        # ── AQUA split mode — process from memory, no data files saved ────────────
        _log("\nSplitting by TP/op…")
        groups = split_by_tp_oper(rows)
        del rows   # free memory
        if not groups:
            _log("No groups found — nothing to process.")
            _send_no_new_data_email(base_dir, args)
            sys.exit(0)

        # Load excluded ops
        excl_ops: set[str] = set()
        try:
            _ec = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
            excl_ops = {str(o) for o in _ec.get("excluded_ops", [])}
        except Exception:
            pass

        to_run: list[tuple[str, list[dict], list[str]]] = []
        for key, (tp_rows, fieldnames) in groups.items():
            m_op = re.search(r'_(\d{5,6})$', key)
            if m_op and m_op.group(1) in excl_ops:
                _log(f"  {key}: op excluded — skipping")
                continue
            to_run.append((key, tp_rows, fieldnames))

        if args.keys:
            _kf = [s.strip() for s in args.keys.split(',') if s.strip()]
            to_run = [(k, r, f) for k, r, f in to_run if any(sf in k for sf in _kf)]
            _log(f"  --keys filter '{args.keys}' → {len(to_run)} key(s)")

        if not to_run:
            _log("No TPs after filtering — sending 'no new data' email.")
            _send_no_new_data_email(base_dir, args)
            sys.exit(0)

        # Group by program letter
        _by_prog: dict[str, list[tuple[str, list[dict], list[str]]]] = {}
        for k, r, f in to_run:
            _by_prog.setdefault(_prog_group(k), []).append((k, r, f))

        _log(f"\nRunning VMIN for {len(to_run)} TP(s) across "
             f"{len(_by_prog)} program group(s):")
        for _pg, _ks in sorted(_by_prog.items()):
            _log(f"  {_pg}: {[k for k, _, _ in _ks]}")

        for _pg in sorted(_by_prog):
            _pg_run_dir = out_dir / f"NVL_VMIN_{_pg}_{ts}"
            _pg_run_dir.mkdir(parents=True, exist_ok=True)
            for key, tp_rows, fieldnames in _by_prog[_pg]:
                _log(f"\n--- {key} ---")
                ok, output_dir = run_vmin_from_rows(
                    key, tp_rows, fieldnames, _pg_run_dir, args.dry_run,
                    split_by_category=split_by_cat)
                results.append((key, ok, output_dir))
            # Per-program report + run-log
            _pg_results = [(k, ok, od) for k, ok, od in results
                           if _prog_group(k) == _pg]
            _pg_rpt = _build_run_report(
                run_dir=_pg_run_dir, results=_pg_results,
                aqua_file=str(aqua_file), run_ts=ts.replace("_", " "))
            update_run_log(
                results=_pg_results, aqua_file=str(aqua_file),
                run_log=run_log, dry_run=args.dry_run, report_path=_pg_rpt)
            _log(f"  {_pg} report: {_pg_rpt}")
            report_path = _pg_rpt
            run_dir     = _pg_run_dir

    # ── Email ──────────────────────────────────────────────────────────────────
    ecfg: dict = {}
    if _EMAIL_CFG.exists():
        try:
            ecfg = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
        except Exception:
            pass

    email_to = ecfg.get("email_to_report") or args.email or _EMAIL_TO
    run_ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    overall  = "OK" if all(r[1] for r in results) else "FAILED"
    body = _email_body_html(
        run_ts=run_ts,
        aqua_file=str(aqua_file),
        results=results,
        run_log=run_log,
        out_dir=out_dir,
    )
    send_email(
        to=email_to,
        subject=f"NVL816-BLLC Vmin Dashboard \u2014 {overall} ({len(results)} TP(s))",
        body_html=body,
        dry_run=args.dry_run,
    )

    # Save a persistent copy to reports/
    if not args.dry_run and report_path and report_path.exists():
        _reports_dir = base_dir / "reports"
        _reports_dir.mkdir(parents=True, exist_ok=True)
        _ts_label = datetime.now().strftime("%Y%m%d_%H%M%S")
        _report_save = _reports_dir / f"VMIN_Report_{_ts_label}.html"
        shutil.copy2(report_path, _report_save)
        _log(f"Report saved: {_report_save}")

    # Compress archived raw input after processing is fully complete.
    if raw_archived_file and not args.dry_run and raw_archived_file.exists():
        _compress_aqua_to_7z(raw_archived_file)

    _log("\n" + "=" * 65)
    _log(f"DONE — {sum(r[1] for r in results)}/{len(results)} TP(s) OK")
    _log("=" * 65)


if __name__ == "__main__":
    main()
