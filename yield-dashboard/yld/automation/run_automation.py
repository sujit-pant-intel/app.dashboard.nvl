"""
run_automation.py
=================
Automated yield dashboard generation for NCXSDJXL0H61* programs.

Workflow
--------
1.  Pull AQUA data (or use local gz via --local-csv).

1b. Split the AQUA data by (full TestProgram name, Operation code):
        NCXSDJXL0H61A002618_119325.csv.gz
        NCXSDJXL0H61B002618_119325.csv.gz
        NCXSDJXL0H61C002618_132322.csv.gz  …
    Stored under  data/programs/*.csv.gz
    Replace rule:  if the lot/wafer set for a TP-oper changed → overwrite its gz.
                   If no change → leave the gz untouched (skip).

2.  Run a single pipeline.py --json pass with DataCSV = ALL programs/*.csv.gz
    (current pull + any previously stored programs not in this pull).

3.  Update run_log.html (cumulative, one section per run).
4.  Send HTML summary email.

Output base (default)
---------------------
  \\\\samba.zsc10.intel.com\\nfs\\zsc10\\disks\\gsc_gwa011\\users\\snpant\\auto
    data\\
        programs\\
            NCXSDJXL0H61A002618_119325.csv.gz   ← per-TP-oper (grows/replaced)
            NCXSDJXL0H61B002618_119325.csv.gz
            …
        NCXSDJXL0H61_<ts>.*                      ← raw AQUA pull snapshots
    NVL_0H61_<YYYYMMDD>\\                         ← pipeline output
    Dashboard.html
    input.json
    run_log.html

Usage
-----
  python run_automation.py                                       # full run (AQUA pull)
  python run_automation.py --dry-run                            # plan only
  python run_automation.py --local-csv "C:\\data\\*gz"          # use local gz (glob)
  python run_automation.py --base-dir C:\\work\\auto            # override output root
  python run_automation.py --days 14                            # AQUA look-back days
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
from datetime import datetime, timezone
from pathlib import Path

# ── Ensure UTF-8 output on Windows ─────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE        = Path(__file__).resolve().parent
_REPO_ROOT   = _HERE.parent.parent.parent   # app.dashboard.nvl/
_PIPELINE    = _REPO_ROOT / "yield-dashboard" / "yld" / "src" / "pipeline.py"
_COMPARE_RUNS = _HERE / "compare_runs.py"
_AQUA_CFG   = _REPO_ROOT / "shared" / "setup" / "automation" / "yield-dashboard" / "NVL_Sort_Yield - AutoPull.txt"

# ── Defaults ───────────────────────────────────────────────────────────────────
_BASE_DIR    = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\yield")
_DATA_DIR    = _BASE_DIR / "data"
_RUN_LOG     = _BASE_DIR / "run_log.html"
_EMAIL_TO    = "sujit.n.pant@intel.com"
_DEFAULT_DAYS = 7

_AQUA_EXE_GAR = r"\\gar.corp.intel.com\ec\proj\ba\aqua\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"
_AQUA_EXE_AMR = r"\\FMSAPP3301.amr.corp.intel.com\Installer\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"

_TP_FOLDER    = r"I:\program\1001\prod\hdmtprogs\nvl_ncx_sds"
_PROD_CFG_DIR = _REPO_ROOT / "shared" / "setup" / "config" / "yield-dashboard"
_EMAIL_CFG    = _REPO_ROOT / "shared" / "setup" / "automation" / "yield-dashboard" / "yield_setup_config.json"
_7Z_EXE       = Path(r"C:\Program Files\7-Zip\7z.exe")


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
    z7_path  = gz_path.parent / (gz_path.stem[:-4] + ".7z")   # NAME.7z
    try:
        # 1. Decompress .csv.gz → .csv
        with gzip.open(gz_path, "rb") as fi, open(csv_path, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        # 2. Compress .csv → .7z
        result = subprocess.run(
            [str(_7Z_EXE), "a", "-mx=5", "-mmt=on", str(z7_path), str(csv_path)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            _log(f"  WARNING: 7z compression failed: {result.stderr.strip()[:200]}")
            return None
        # 3. Delete originals
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


def pull_aqua(aqua_exe: str, report_config: Path, data_dir: Path, dry_run: bool) -> Path | None:
    """Run AquaCmdLine.exe with the repo config. Returns path to the downloaded file."""
    data_dir.mkdir(parents=True, exist_ok=True)
    ts       = _ts()
    out_base = data_dir / f"NCXSDJXL0H61_{ts}"
    out_req  = out_base.with_suffix(".zip")   # AQUA ignores extension; we glob after

    report_name = _aqua_report_name(report_config)
    temp_dir    = Path(os.environ.get("TEMP", tempfile.gettempdir()))
    temp_pat    = f"{report_name}*.CSV"

    # Derive server name from exe path (amr → AMR, default GAR)
    _exe_lower = str(aqua_exe).lower()
    _aqua_server = "AMR" if "amr" in _exe_lower else "GAR"

    cmd = [
        aqua_exe,
        "-AquaServer",    _aqua_server,
        "-ReportConfig",  str(report_config),
        "-OutputFileName", str(out_req),
    ]

    _log(f"{'DRY-RUN  ' if dry_run else ''}AQUA pull → {out_base}.*")
    _log(f"  Config : {report_config}")
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

    # Primary: any file written to data_dir with our stem
    written = [p for p in data_dir.glob(f"{out_base.name}*") if p.stat().st_size > 0]
    if written:
        out = max(written, key=lambda p: p.stat().st_mtime)
        _log(f"  Output: {out.name} ({out.stat().st_size:,} bytes)")
        return out

    # Fallback: new CSV in %TEMP%
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
# Step 2 — Read & split CSV by (variant, operation)
# ─────────────────────────────────────────────────────────────────────────────

def _read_aqua_file(path: Path) -> tuple[list[dict], str]:
    """
    Read an AQUA output file (.csv, .csv.gz, .zip, .7z).
    Handles nested chains: 7z→zip→csv, 7z→csv.gz, 7z→csv, zip→csv, gz→csv.
    Returns (rows, delimiter).
    """
    def _inner_from_bytes(raw: bytes) -> str:
        """Recursively unwrap zip/gz layers until we have plain CSV text."""
        if raw[:6] == b'7z\xbc\xaf\x27\x1c':
            import tempfile, subprocess as _sp
            with tempfile.TemporaryDirectory() as _tmp:
                _tmp_p = Path(_tmp)
                _sp.run([str(_7Z_EXE), "e", str(path), f"-o{_tmp}", "-y"],
                        check=True, capture_output=True)
                # Prefer .csv > .csv.gz > .zip (in case nested)
                for _pat in ("*.csv", "*.csv.gz", "*.zip"):
                    _hits = sorted(_tmp_p.glob(_pat))
                    if _hits:
                        return _inner_from_bytes(_hits[0].read_bytes())
            raise ValueError(f"No CSV/zip/gz found inside {path.name}")
        elif raw[:2] == b'PK':
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                # Pick first .csv; if none, pick first entry and recurse
                names = z.namelist()
                pick = next((n for n in names if n.lower().endswith('.csv')), names[0])
                return _inner_from_bytes(z.read(pick))
        elif raw[:2] == b'\x1f\x8b':
            return _inner_from_bytes(gzip.decompress(raw))
        else:
            return raw.decode("utf-8-sig", errors="replace")

    inner = _inner_from_bytes(path.read_bytes())
    first_line = inner.split("\n")[0]
    delim = "\t" if "\t" in first_line else ","
    rows = list(csv.DictReader(io.StringIO(inner), delimiter=delim))
    return rows, delim


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Split by (TestProgram full name, Operation) and maintain per-TP gzs
# ─────────────────────────────────────────────────────────────────────────────

def _write_gz(rows: list[dict], fieldnames: list[str], path: Path) -> None:
    """Write rows as gzip-compressed CSV (UTF-8, comma-delimited)."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames,
                       extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for row in rows:
        w.writerow({k: row.get(k, "") for k in fieldnames})
    path.write_bytes(gzip.compress(buf.getvalue().encode("utf-8"), compresslevel=6))


def _safe_filename(s: str) -> str:
    """Strip characters that are unsafe in filenames."""
    return re.sub(r'[\\/:*?"<>|]', '_', s).strip()


def split_by_tp_oper(rows: list[dict]) -> dict[str, tuple[list[dict], list[str]]]:
    """
    Split AQUA rows by (full TestProgram name, Operation code).

    Returns:
        dict  safe_key → (rows, fieldnames)
        safe_key = "{safe_tp_name}_{op_code}"
            e.g. "NCXSDJXL0H61A002618_119325"
                 "NCXSDJXL0H61B002618_119325"
                 "NCXSDJXL0H61C002618_132322"

    Wide format: columns like 'Program Name_119325', 'Lot_119325', …
        Each row spans all ops; extract per-op subset and rename columns
        (strip _{op} suffix).  Common columns (no suffix) are always included.

    Tall format: one row per die per op; has 'Program Name' and 'Operation' columns.
    """
    if not rows:
        return {}

    headers    = list(rows[0].keys())
    header_set = set(headers)

    # Detect op codes embedded in column names (5-6 digit numbers as suffix)
    op_codes: set[str] = set()
    for h in headers:
        m = re.search(r'_(\d{5,6})$', h)
        if m:
            op_codes.add(m.group(1))

    groups: dict[str, tuple[list[dict], list[str]]] = {}

    # ── Wide format ────────────────────────────────────────────────────────
    # Also handle single-op wide format (all columns have one _{op} suffix,
    # e.g. "Program Name_119325" and data rows have multiple programs in that column).
    if len(op_codes) >= 1:
        _log(f"  Wide format — ops: {sorted(op_codes)}")
        common_cols = [h for h in headers if not re.search(r'_\d{5,6}$', h)]

        for op in sorted(op_codes):
            prog_col = f"Program Name_{op}"
            if prog_col not in header_set:
                continue

            # Narrow rows for this op: common cols + op-specific cols renamed
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
        op   = (row.get(op_col)   or (next(iter(op_codes), "unknown"))).strip()
        key  = _safe_filename(f"{prog}_{op}") if prog else f"unknown_{op}"
        if key not in groups:
            groups[key] = ([], list(row.keys()))
        groups[key][0].append(row)

    for key, (rws, _) in groups.items():
        _log(f"    {key}: {len(rws):,} rows")

    return groups


def _lot_wafer_set(rows: list[dict]) -> frozenset:
    """Return a frozenset of (lot, wafer, date) strings for change-detection.
    Date is included so a re-test of the same lot/wafer with a new test date triggers a re-run.
    """
    if not rows:
        return frozenset()
    hdrs      = list(rows[0].keys())
    # Strip session suffixes like "_119325" when matching column names so that
    # AQUA columns such as "LOTS End Date Time_119325" are correctly identified.
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
    """Write data_dir/programs/{letter}/{key}.csv.gz (always — no fingerprint check).

    Returns (gz_path, True) always (or (gz_path, False) in dry-run).
    """
    prog_dir = data_dir / "programs"
    _m = re.search(r'[0-9A-Za-z]H61([A-Za-z])', key)
    _letter_sub = f"0H61{_m.group(1).upper()}" if _m else "0H61X"
    letter_dir  = prog_dir / _letter_sub
    gz_path     = letter_dir / f"{key}.csv.gz"
    z7_path     = letter_dir / f"{key}.7z"

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
# Step 3 — Run pipeline for each group
# ─────────────────────────────────────────────────────────────────────────────

def _combine_gz(gz_files: list[Path], out_path: Path, dry_run: bool) -> Path:
    """
    Concatenate all per-TP gz files into a single combined.csv.gz.
    Column set = union of all files; missing values filled with empty string.
    Returns out_path.
    """
    if not gz_files:
        return out_path

    all_rows: list[dict] = []
    all_cols: list[str]  = []

    for f in gz_files:
        if not f.exists():
            _log(f"  DRY-RUN: {f.name} (not yet written)")
            continue
        rows, _ = _read_aqua_file(f)
        if not rows:
            continue
        for col in rows[0].keys():
            if col not in all_cols:
                all_cols.append(col)
        all_rows.extend(rows)

    _log(f"Combining {len(gz_files)} gz files → {len(all_rows):,} rows, {len(all_cols)} cols")

    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_gz(all_rows, all_cols, out_path)
        _log(f"  Combined gz: {out_path.name}  ({out_path.stat().st_size:,} bytes)")
    else:
        _log(f"  DRY-RUN: would write {out_path}")

    return out_path


_WATERMARK_CSS = """
<style id="_wm_style">
#_wm_badge {
  position: fixed;
  top: 8px;
  right: 14px;
  z-index: 99999;
  background: #6c3483;
  color: #ffffff;
  font: bold 11px/1.4 Arial, sans-serif;
  padding: 3px 10px;
  border-radius: 4px;
  letter-spacing: 0.3px;
  pointer-events: none;
  white-space: nowrap;
}
</style>
"""
_WATERMARK_HTML = '<div id="_wm_badge">Pant, Sujit N &mdash; GEMS FTE</div>'


def _inject_watermark(html_path: Path) -> None:
    """Inject the watermark badge into an HTML file in-place (idempotent)."""
    try:
        text = html_path.read_text(encoding="utf-8", errors="replace")
        if "_wm_badge" in text or "_wm_div" in text:
            return   # already watermarked
        # Insert CSS before </head> (or at top if no </head>)
        if "</head>" in text:
            text = text.replace("</head>", _WATERMARK_CSS + "</head>", 1)
        else:
            text = _WATERMARK_CSS + text
        # Insert badge div after <body …> tag
        import re as _re
        text = _re.sub(
            r'(<body[^>]*>)',
            r'\1\n' + _WATERMARK_HTML,
            text, count=1, flags=_re.IGNORECASE,
        )
        if _WATERMARK_HTML not in text:   # no <body> tag at all
            text = text + _WATERMARK_HTML
        html_path.write_text(text, encoding="utf-8")
    except Exception as e:
        _log(f"  watermark warning: {html_path.name}: {e}")


def _watermark_output_dir(output_dir: str) -> None:
    """Watermark all HTML files in the pipeline output folder."""
    d = Path(output_dir)
    if not d.exists():
        return
    html_files = list(d.rglob("*.html"))
    _log(f"  Watermarking {len(html_files)} HTML file(s) in {d.name}")
    for f in html_files:
        _inject_watermark(f)


def _rebuild_dashboard_html_for_tp(tp_key: str, base_dir: Path, out_path: Path | None = None) -> Path | None:
    """Synthesize Dashboard_{tp_key}.html at base_dir from existing historical run folders.

    Used when pipeline.py has not created/updated it (e.g. first run or fresh samba).
    Scans output/ for NVL_0H61_* run folders, finds the matching TP sub-dir in each,
    and builds a minimal Dashboard HTML with one run-block per historical run.
    Returns the written path, or None if no runs were found.
    """
    output_dir = base_dir / "output"
    if not output_dir.exists():
        return None

    # Strip the op-suffix to get the TP prefix (e.g. NCXSDJXL0H61C002620)
    tp_prefix = re.sub(r'_\d{5,6}$', '', tp_key)

    run_folders = sorted(
        [d for d in output_dir.iterdir()
         if d.is_dir() and re.search(r'_\d{8}_\d{6}$', d.name)],
        key=lambda d: d.name,   # ascending = oldest first; we'll reverse for display
    )

    # run_folders is sorted ascending (oldest first); we scan newest-last so
    # that same-date duplicates overwrite earlier same-day entries in seen_keys.
    seen_keys: dict[str, tuple] = {}   # dated_key → block tuple (newest wins)
    for rf in run_folders:
        # Find matching c_dirs (same prefix, not _R0)
        c_dirs = sorted(
            [d for d in rf.iterdir()
             if d.is_dir()
             and d.name.startswith(tp_prefix)
             and not d.name.endswith('_R0')],
            key=lambda d: d.name,
            reverse=True,
        )
        if not c_dirs:
            continue
        c_dir = c_dirs[0]
        index_html = c_dir / 'index.html'
        if not index_html.exists():
            continue
        href = os.path.relpath(str(index_html), str(base_dir)).replace('\\', '/')
        m = re.search(r'(\d{8})_(\d{6})$', rf.name)
        date_str = m.group(1) if m else '00000000'
        time_str = m.group(2) if m else '000000'
        ts = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]} {time_str[:2]}:{time_str[2:4]}"
        dated_key = f"{c_dir.name}_{date_str}"
        seen_keys[dated_key] = (  # overwrite → last (newest) run for this date wins
            dated_key,
            f'<div class="run-block" data-stem="{dated_key}">\n'
            f'<div class="run-header" onclick="toggle(this)">'
            f'<span class="arrow">&#9660;</span> {c_dir.name}'
            f'<span class="ts"> - {ts}</span></div>\n'
            f'<div class="run-body">\n'
            f'<a class="run-link report-link" href="{href}" target="_blank">Yield Report</a>\n'
            f'</div>\n</div>',
        )

    # Convert to list; seen_keys is ordered (Python 3.7+) oldest→newest (ascending scan)
    blocks = list(seen_keys.values())

    if not blocks:
        return None

    # Newest first in the HTML (reversed from our oldest-first scan order)
    blocks_html = '\n'.join(b for _, b in reversed(blocks))
    page_css = (
        '*{box-sizing:border-box;margin:0;padding:0}'
        'body{font-family:Arial,sans-serif;background:#1a252f;color:#ecf0f1;padding:16px}'
        'h1{font-size:16px;margin-bottom:14px;color:#3498db}'
        '.run-block{background:#2c3e50;border-radius:6px;margin-bottom:10px;overflow:hidden}'
        '.run-header{padding:10px 14px;cursor:pointer;display:flex;align-items:center;'
        'gap:6px;font-weight:bold;font-size:13px;user-select:none}'
        '.run-header:hover{background:#34495e}'
        '.arrow{font-size:10px;transition:transform .2s}'
        '.run-header.collapsed .arrow{transform:rotate(-90deg)}'
        '.ts{font-weight:normal;font-size:11px;color:#95a5a6;margin-left:auto}'
        '.run-body{padding:8px 14px 12px;display:flex;flex-wrap:wrap;gap:6px}'
        '.run-link{display:inline-block;padding:5px 10px;border-radius:4px;'
        'font-size:12px;text-decoration:none;white-space:nowrap}'
        '.report-link{background:#2980b9;color:#fff}'
        '.report-link:hover{background:#3498db}'
    )
    page_js = (
        "function toggle(hdr){"
        "hdr.classList.toggle('collapsed');"
        "hdr.nextElementSibling.style.display="
        "hdr.classList.contains('collapsed')?'none':'';}"
    )
    html = (
        '<!DOCTYPE html>\n<html lang="en">\n<head>'
        '<meta charset="utf-8">'
        f'<title>Dashboard {tp_key}</title>'
        f'<style>{page_css}</style>'
        f'<script>{page_js}</script>'
        '</head>\n<body>\n'
        f'<h1>Dashboard &mdash; {tp_key}</h1>\n'
        '<!-- YIELD_START -->\n'
        f'{blocks_html}\n'
        '<!-- YIELD_END -->\n'
        '</body>\n</html>\n'
    )
    dash_path = out_path or (base_dir / "output" / "misc" / f'Dashboard_{tp_key}.html')
    dash_path.parent.mkdir(parents=True, exist_ok=True)
    dash_path.write_text(html, encoding='utf-8')
    return dash_path


def _stamp_dashboard_block(dash_html_path: Path, block_key: str, date_str: str) -> None:
    """Rename the undated run-block in Dashboard HTML to a dated one.

    Each daily run writes a block with data-stem="tp_key" (always the same).
    By renaming it to data-stem="tp_key_YYYYMMDD" AFTER the pipeline writes it,
    previous days' blocks survive and compare_runs.py can show day-over-day trends.
    If a same-day dated block already exists (re-run), it is removed first.
    """
    if not dash_html_path.exists():
        return
    try:
        content = dash_html_path.read_text(encoding='utf-8')
        dated_key = f"{block_key}_{date_str}"
        # Remove any same-day dated block that may exist from an earlier re-run
        if f'data-stem="{dated_key}"' in content:
            block_re = re.compile(
                r'<div class="run-block" data-stem="' + re.escape(dated_key) + r'">'
                r'[\s\S]*?</div>\s*</div>',
                re.MULTILINE,
            )
            content = block_re.sub('', content)
        # Rename the freshly-written undated block to dated
        if f'data-stem="{block_key}"' in content:
            content = re.sub(
                r'data-stem="' + re.escape(block_key) + r'"',
                f'data-stem="{dated_key}"',
                content,
                count=1,
            )
            # Also add date in brackets to the display name in the run-header
            # e.g. "> tp_key<span" → "> tp_key (YYYY-MM-DD)<span"
            _fmt_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            content = re.sub(
                r'(</span>\s*)(' + re.escape(block_key) + r')(<span\s+class="ts")',
                rf'\g<1>\g<2> ({_fmt_date})\g<3>',
                content,
                count=1,
            )
            dash_html_path.write_text(content, encoding='utf-8')
            _log(f"  Dashboard block stamped: {block_key} → {dated_key} ({_fmt_date})")
        else:
            _log(f"  WARNING: block data-stem=\"{block_key}\" not found in {dash_html_path.name}")
    except Exception as _e:
        _log(f"  WARNING: _stamp_dashboard_block failed: {_e}")


def _named_attachment(src: Path, display_name: str, tmp_dir: Path) -> str:
    """Copy src to tmp_dir/<display_name> so Outlook shows the desired filename."""
    dest = tmp_dir / display_name
    if Path(src).resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return str(dest)


def _find_product_config(variant_letter: str) -> str | None:
    """
    Find a matching product config JSON for this variant.
    Variant letter maps to a DevRevStep prefix: A→8PF5CV, B→8PF6CV, etc.
    (best-effort — reads existing configs and matches on stem).
    """
    if not _PROD_CFG_DIR.exists():
        return None
    for p in _PROD_CFG_DIR.glob("*.json"):
        stem = p.stem.upper()
        # E.g. '8PF5CV - SORT - Product Config - BB+AIO-L0'
        if "SORT" in stem:
            return str(p)
    return None


def run_pipeline_for_group(
    group_key: str,
    csv_path: Path,
    base_dir: Path,
    dry_run: bool,
) -> tuple[bool, str]:
    """
    Build input.json for the group and run pipeline.py --json.
    Returns (success, output_dir).
    """
    # Derive variant letter from group key '61A-119325' → 'A'
    m = re.match(r'61([A-Z])-(\d+)', group_key)
    variant = m.group(1) if m else "X"
    op_code = m.group(2) if m else "unknown"

    group_dir  = base_dir / group_key
    group_dir.mkdir(parents=True, exist_ok=True)

    identifier    = f"{group_key}_{datetime.now().strftime('%Y%m%d')}"
    output_folder = str(group_dir)
    dashboard     = str(group_dir / "Dashboard.html")
    prod_cfg      = _find_product_config(variant)
    tp_folder     = str(Path(_TP_FOLDER))

    cfg = {
        "DataCSV":             [str(csv_path)],
        "output_folder":       output_folder,
        "dashboard":           dashboard,
        "identifier":          identifier,
        "TestProgram_folder":  tp_folder,
        "run_parametric":      True,
        "keep_pcm_idw":        False,
    }
    if prod_cfg:
        cfg["product_config_json"] = prod_cfg

    json_path = group_dir / "input.json"
    json_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    _log(f"  [{group_key}] input.json → {json_path}")

    output_dir = str(Path(output_folder) / identifier)

    if dry_run:
        _log(f"  [{group_key}] DRY-RUN: would run pipeline.py --json {json_path}")
        return True, output_dir

    cmd = [sys.executable, str(_PIPELINE), "--json", str(json_path)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    _log(f"  [{group_key}] Running pipeline → {output_dir}")

    try:
        result = subprocess.run(cmd, capture_output=False, text=True,
                                timeout=3600, env=env, cwd=str(_PIPELINE.parent))
        ok = result.returncode == 0
        if not ok:
            _log(f"  [{group_key}] WARNING: pipeline exited rc={result.returncode}")
        return ok, output_dir
    except subprocess.TimeoutExpired:
        _log(f"  [{group_key}] ERROR: pipeline timed out")
        return False, output_dir


# ─────────────────────────────────────────────────────────────────────────────
# Compare-card helpers  (used by _build_run_report)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_yield_summary(tp_dir: Path, row_filter: "set | None" = None) -> dict | None:
    """Parse *_BinDistribution.html (or legacy digital_dashboard.html) → {die, bins, repair_bins, sums}.
    row_filter: optional set of "lot|wafer" strings; when provided only those rows are counted.
    """
    # Try BinDistribution.html first (current pipeline output)
    bd_files = sorted(tp_dir.glob("*_BinDistribution.html"))
    dd = bd_files[0] if bd_files else tp_dir / "digital_dashboard.html"
    if not dd.exists():
        return None
    try:
        txt = dd.read_text(encoding="utf-8", errors="replace")

        # --- New format: var DATA = {...} in *_BinDistribution.html ---
        m_data = re.search(r'var DATA\s*=\s*', txt)
        if m_data:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(txt, m_data.end())
            all_rows = data.get("rows", [])
            # Apply lot/wafer filter if provided
            if row_filter:
                rows = [r for r in all_rows
                        if (str(r.get("lot", "")) + "|" + str(r.get("wafer", ""))) in row_filter]
            else:
                rows = all_rows
            total = sum(sum(row.get("binCounts", {}).values()) for row in rows)
            # Sum bin counts and functional bin counts across filtered wafers
            bin_totals: dict[str, int] = {}
            fb_totals:  dict[str, int] = {}
            for row in rows:
                for b, cnt in row.get("binCounts", {}).items():
                    bin_totals[b] = bin_totals.get(b, 0) + int(cnt)
                for _ib, fbmap in row.get("ibToFb", {}).items():
                    for fb, cnt in fbmap.items():
                        fb_totals[fb] = fb_totals.get(fb, 0) + int(cnt)

            def _pct(cnt: int) -> str:
                return f"{cnt / total * 100:.1f}%" if total > 0 else "–"

            named_bins  = {f"Bin {b}": _pct(c) for b, c in bin_totals.items()}
            repair_bins = {fb: _pct(c) for fb, c in fb_totals.items() if fb in ("198", "201", "202")}
            # --- HP/LP from die-level DLCP data (IB 1-4, threshold 92.5%, first UPM col) ---
            dlcp: dict = {}
            upm_med: str | None = None
            if data.get("hasUpm"):
                _ui = int(data.get("upmStart") or 5)  # first UPM column index
                _hp_tot = 0; _lp_tot = 0
                _upm_vals: list[float] = []
                for _dr in rows:
                    for _d in _dr.get("dies", []):
                        if len(_d) < 4:
                            continue
                        _ibi = int(_d[2]) if isinstance(_d[2], (int, float)) else -1
                        if _ibi not in (1, 2, 3, 4):
                            continue  # only DLCP dies
                        _up  = _d[_ui] if len(_d) > _ui else None
                        if _up is not None:
                            _upm_vals.append(float(_up))
                        if _ibi in (1, 2) and _up is not None and _up >= 92.5:
                            _hp_tot += 1
                        else:
                            _lp_tot += 1
                _dn = _hp_tot + _lp_tot
                if _dn >= 10:  # suppress if too few DLCP dies
                    dlcp = {"hp": f"{_hp_tot/_dn*100:.1f}%", "lp": f"{_lp_tot/_dn*100:.1f}%",
                            "n": _dn, "hp_n": _hp_tot, "lp_n": _lp_tot}
                if _upm_vals:
                    _upm_s = sorted(_upm_vals)
                    _nu    = len(_upm_s)
                    _umed  = _upm_s[_nu // 2] if _nu % 2 else (_upm_s[_nu//2 - 1] + _upm_s[_nu//2]) / 2
                    upm_med = f"{_umed:.1f}%"
            # ── Extract FF / FF+DF targets from yieldDefs ────────────────────
            ff_tgt   = "–"
            ffdf_tgt = "–"
            for _yd in data.get("yieldDefs", []):
                _bins_key = str(_yd.get("bins", "")).replace(" ", "")
                _exp = _yd.get("expected")
                if _exp is not None:
                    if _bins_key == "1/2":
                        ff_tgt = f"{float(_exp):.1f}%"
                    elif _bins_key == "1/2/3/4":
                        ffdf_tgt = f"{float(_exp):.1f}%"
            return {"die": f"{total:,}", "bins": named_bins, "repair_bins": repair_bins,
                    "sums": {}, "dlcp": dlcp, "upm_med": upm_med,
                    "ff_tgt": ff_tgt, "ffdf_tgt": ffdf_tgt}

        # --- Legacy format: digital_dashboard.html with DD_ROWS ---
        m_die = re.search(r"# Die: <b>([\d,]+)</b>", txt)
        total_die = m_die.group(1) if m_die else "–"
        m_rows = re.search(r"var DD_ROWS\s*=\s*(\[.*?\])\s*;", txt, re.DOTALL)
        if not m_rows:
            return {"die": total_die, "bins": {}, "repair_bins": {}, "sums": {}}
        rows = json.loads(m_rows.group(1))
        named_bins: dict[str, str] = {}
        repair_bins: dict[str, str] = {}
        section_sums: dict[str, str] = {}
        cur_section = ""
        for row in rows:
            cells = row.get("cells", [])
            if not cells:
                continue
            name = cells[0]
            val  = cells[1] if len(cells) > 1 else ""
            val  = re.sub(r'\s*\([\d,]+\)\s*$', '', val).strip()
            if   name.startswith("ARR_"):        cur_section = "ARR"
            elif name.startswith("FUN_"):        cur_section = "FUN"
            elif name.startswith("SCN_"):        cur_section = "SCN"
            elif re.match(r"^Bin \d+$", name):  cur_section = "Bins"
            if row.get("bold") and cur_section and cur_section not in section_sums:
                section_sums[cur_section] = val
            if re.match(r"^Bin \d+$", name):
                named_bins[name] = val
            m_repair = re.match(r"^Repair Bin (\d+)", name)
            if m_repair:
                repair_bins[m_repair.group(1)] = val
        return {"die": total_die, "bins": named_bins, "repair_bins": repair_bins, "sums": section_sums}
    except Exception:
        return None


def _extract_per_material_summaries(tp_dir: Path) -> list[tuple[str, dict | None]]:
    """Read BinDistribution.html in *tp_dir*, group rows by material type field,
    and return [(mat_type, summary_dict), ...] sorted by material type.
    Returns an empty list if only one material type is found (no breakdown needed).
    Falls back to empty list on any error."""
    bd_files = sorted(tp_dir.glob("*_BinDistribution.html"))
    dd = bd_files[0] if bd_files else tp_dir / "digital_dashboard.html"
    if not dd.exists():
        return []
    try:
        txt = dd.read_text(encoding="utf-8", errors="replace")
        m_data = re.search(r'var DATA\s*=\s*', txt)
        if not m_data:
            return []
        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(txt, m_data.end())
        all_rows = data.get("rows", [])
        if not all_rows:
            return []

        # Try "material" field first, then fall back to "lot"
        mat_col = None
        for candidate in ("material", "material_type", "materialType", "lot"):
            if candidate in all_rows[0]:
                mat_col = candidate
                break
        if not mat_col:
            return []

        # Group rows by material type
        from collections import defaultdict as _dd
        mat_rows: dict[str, list[str]] = _dd(list)
        for row in all_rows:
            mat_id = str(row.get(mat_col, "")).strip() or "UNKNOWN"
            lot    = str(row.get("lot",   "")).strip()
            wafer  = str(row.get("wafer", "")).strip()
            mat_rows[mat_id].append(f"{lot}|{wafer}")

        if len(mat_rows) <= 1:
            # Only one material type — no sub-breakdown
            return []

        results = []
        for mat_id in sorted(mat_rows):
            rf   = set(mat_rows[mat_id])
            smry = _extract_yield_summary(tp_dir, row_filter=rf)
            results.append((mat_id, smry))
        return results
    except Exception:
        return []


def _build_compare_section(sorted_groups: list, run_dir: Path) -> str:
    """HTML for the two comparison cards shown at top of report.html."""

    def _is_stale(item) -> bool:
        return len(item) > 3 and str(item[3]).startswith("prev:")

    def _op(item) -> str:
        m = re.search(r"_(\d{5,6})$", item[0])
        return m.group(1) if m else "?"

    _auto_dir = run_dir.parent.parent  # …/auto/

    # ── Load FF / FF+DF targets from product config ───────────────────────────
    _ff_tgt = _ff_df_tgt = None
    try:
        cfg_path = _find_product_config("C")
        if cfg_path:
            _cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
            for entry in _cfg.get("yield_targets", []):
                bins_set = set(entry.get("bin", "").split("/"))
                y = entry.get("yield")
                if bins_set == {"1", "2"}:
                    _ff_tgt = float(y)
                elif bins_set == {"1", "2", "3", "4"}:
                    _ff_df_tgt = float(y)
    except Exception:
        pass

    def _parse_pct(s: str) -> float:
        try:
            return float(str(s).replace("%", "").strip())
        except Exception:
            return 0.0

    def _write_cross_compare_html(letter_data: list, auto_dir: Path) -> "Path | None":
        """Generate a standalone cross-program comparison HTML and save to auto_dir.
        letter_data: list of (letter, smry, tp_output_dir)"""
        if not letter_data:
            return None
        clrs = {"C": "#43a047", "B": "#1e88e5", "A": "#fb8c00"}

        def _hbar(letter: str, val: float, max_v: float, lbl: str) -> str:
            pct   = min(100.0, val / max_v * 100) if max_v > 0 else 0.0
            color = clrs.get(letter, "#90a4ae")
            disp  = f"{val:.1f}%" if val > 0 else "\u2013"
            return (
                f"<div style='display:flex;align-items:center;gap:6px;margin:3px 0'>"
                f"<span style='width:36px;font-size:0.85em;text-align:right;color:#90a4ae'>{lbl}</span>"
                f"<div style='width:200px;background:#263950;height:14px;border-radius:3px'>"
                f"<div style='width:{pct:.1f}%;background:{color};height:14px;border-radius:3px'></div></div>"
                f"<span style='font-size:0.88em;min-width:46px;color:#cde'>{disp}</span>"
                f"</div>"
            )

        def _grp(title: str, pts: list, max_v: float, tgt: float | None = None) -> str:
            bars     = "".join(_hbar(l, v, max_v, f"61{l}") for l, v in pts)
            tgt_note = (f"<div style='font-size:0.8em;color:#78909c;margin-left:44px'>"
                        f"Target:\u00a0{tgt:.1f}%</div>"
                        if tgt is not None else "")
            return (
                f"<div style='margin-bottom:14px'>"
                f"<div style='font-size:0.92em;font-weight:600;color:#4fc3f7;"
                f"margin-bottom:3px;margin-left:44px'>{title}</div>"
                f"{bars}{tgt_note}</div>"
            )

        ff_pts, ffdf_pts, fb198_pts, fb201_pts, fb202_pts = [], [], [], [], []
        for letter, smry, _ in letter_data:
            bins = smry.get("bins", {})        if smry else {}
            rb   = smry.get("repair_bins", {}) if smry else {}
            b1   = _parse_pct(bins.get("Bin 1", "0"))
            b2   = _parse_pct(bins.get("Bin 2", "0"))
            b3   = _parse_pct(bins.get("Bin 3", "0"))
            b4   = _parse_pct(bins.get("Bin 4", "0"))
            ff_pts.append((letter,   b1 + b2))
            ffdf_pts.append((letter, b1 + b2 + b3 + b4))
            fb198_pts.append((letter, _parse_pct(rb.get("198", "0"))))
            fb201_pts.append((letter, _parse_pct(rb.get("201") or rb.get("2") or "0")))
            fb202_pts.append((letter, _parse_pct(rb.get("202", "0"))))

        rep_max = max(
            max((v for _, v in fb198_pts), default=0),
            max((v for _, v in fb201_pts), default=0),
            max((v for _, v in fb202_pts), default=0), 1.0
        ) * 1.35

        chart_html = (
            "<div style='display:flex;gap:40px;flex-wrap:wrap;margin-bottom:28px'>"
            f"<div>{_grp('FF (1+2)', ff_pts, 100.0, _ff_tgt)}"
            f"{_grp('FF+DF (1+2+3+4)', ffdf_pts, 100.0, _ff_df_tgt)}</div>"
            "<div style='border-left:1px solid #2e4a6a;padding-left:28px'>"
            f"{_grp('FB198 Vmin Repair', fb198_pts, rep_max)}"
            f"{_grp('FB201 Vnom Repair', fb201_pts, rep_max)}"
            f"{_grp('FB202 Vmax Repair', fb202_pts, rep_max)}"
            "</div></div>"
        )

        ff_tgt_s    = f"{_ff_tgt:.1f}%"    if _ff_tgt    is not None else "\u2013"
        ff_df_tgt_s = f"{_ff_df_tgt:.1f}%" if _ff_df_tgt is not None else "\u2013"

        table_rows = ""
        for letter, smry, _ in letter_data:
            bins  = smry.get("bins", {})        if smry else {}
            rb    = smry.get("repair_bins", {}) if smry else {}
            dlcp  = smry.get("dlcp", {})        if smry else {}
            die   = smry.get("die", "\u2013")  if smry else "\u2013"
            b1    = _parse_pct(bins.get("Bin 1", "0"))
            b2    = _parse_pct(bins.get("Bin 2", "0"))
            b3    = _parse_pct(bins.get("Bin 3", "0"))
            b4    = _parse_pct(bins.get("Bin 4", "0"))
            ff    = b1 + b2
            ffdf  = b1 + b2 + b3 + b4
            ff_col    = "#66bb6a" if (_ff_tgt    is None or ff    >= _ff_tgt)    else "#ef5350"
            ffdf_col  = "#66bb6a" if (_ff_df_tgt is None or ffdf >= _ff_df_tgt) else "#ef5350"
            rv198   = rb.get("198", "\u2013")
            rv201   = rb.get("201") or rb.get("2") or "\u2013"
            rv202   = rb.get("202", "\u2013")
            rv_upm  = smry.get("upm_med", "\u2013") if smry else "\u2013"
            _d202 = dlcp  # dlcp is now a flat dict (aggregate over all DLCP IB 1-4 dies)
            if rv_upm == "\u2013":  # UPM absent → DLCP unreliable
                _d202_hp_col = "#546e7a"
                rv_hp = "\u2013"
                rv_lp = "\u2013"
            elif _d202:
                _span = "<span style='color:#546e7a;font-size:10px'>"
                _hp_ns = f"<br>{_span}({_d202['hp_n']:,})</span>" if _d202.get('hp_n') is not None else ""
                _lp_ns = f"<br>{_span}({_d202['lp_n']:,})</span>" if _d202.get('lp_n') is not None else ""
                _d202_hp_val = float(_d202['hp'].rstrip('%')) if _d202.get('hp') else 0.0
                _d202_hp_col = "#4caf50" if _d202_hp_val >= 30 else "#ef5350"
                rv_hp = f"{_d202['hp']}{_hp_ns}"
                rv_lp = f"{_d202['lp']}{_lp_ns}"
            else:
                _d202_hp_col = "#546e7a"
                rv_hp = "\u2013"
                rv_lp = "\u2013"
            prog_col = clrs.get(letter, "#80cbc4")
            table_rows += (
                f"<tr>"
                f"<td style='color:{prog_col};font-weight:bold;font-family:monospace'>0H61{letter}</td>"
                f"<td>{die}</td>"
                f"<td style='color:{ff_col};font-weight:bold'>{ff:.1f}%</td>"
                f"<td style='color:{ffdf_col};font-weight:bold'>{ffdf:.1f}%</td>"
                f"<td>{rv_upm}</td>"
                f"<td style='color:{_d202_hp_col};font-weight:bold'>{rv_hp}</td>"
                f"<td style='color:#f0a500'>{rv_lp}</td>"
                f"<td>{rv198}</td><td>{rv201}</td><td>{rv202}</td>"
                f"</tr>\n"
            )

        bindist_cols = ""
        for letter, _, tp_dir in letter_data:
            tp_path = Path(tp_dir) if tp_dir else None
            bd_html = ""
            if tp_path and tp_path.exists():
                bdfiles = sorted(tp_path.glob("*BinDistribution*.html"))
                if bdfiles:
                    try:
                        bd_content = bdfiles[0].read_text(encoding="utf-8", errors="replace")
                        bd_escaped = _html_mod.escape(bd_content, quote=True)
                        bd_html = (
                            f'<iframe srcdoc="{bd_escaped}" width="100%" height="920"'
                            f' style="border:none;background:#fff;border-radius:4px;display:block"></iframe>'
                        )
                    except Exception:
                        pass
            if not bd_html:
                bd_html = "<p style='color:#90a4ae;font-size:0.88em'>BinDistribution not available</p>"
            col = clrs.get(letter, "#80cbc4")
            bindist_cols += (
                f"<div style='flex:1;min-width:340px'>"
                f"<div style='color:{col};font-weight:bold;font-size:1em;margin-bottom:6px'>0H61{letter}</div>"
                f"{bd_html}</div>"
            )

        legend = "&ensp;".join(
            f"<span style='display:inline-flex;align-items:center;gap:5px'>"
            f"<span style='width:14px;height:14px;background:{clrs.get(l,'#90a4ae')};"
            f"border-radius:3px;display:inline-block'></span>"
            f"<span style='color:#cde;font-size:0.9em'>0H61{l}</span></span>"
            for l, _, _ in letter_data
        )
        ts_now = datetime.now().strftime("%Y-%m-%d %H:%M")
        html = (
            "<!DOCTYPE html>\n<html lang='en'>\n"
            "<head><meta charset='utf-8'>"
            "<title>NVL816-BLLC Cross-Program Comparison</title><style>\n"
            "body{font-family:Segoe UI,Arial,sans-serif;background:#1a252f;color:#e8f0f7;"
            "margin:0;padding:16px 32px 60px}\n"
            "h1{color:#4fc3f7;border-bottom:2px solid #4fc3f7;padding-bottom:8px;margin-bottom:4px}\n"
            ".ts{color:#90a4ae;font-size:0.85em;margin-top:0}\n"
            "table{border-collapse:collapse;min-width:500px;margin-bottom:6px}\n"
            "th{background:#263950;color:#4fc3f7;padding:7px 14px;text-align:center;white-space:nowrap}\n"
            "td{padding:5px 14px;border-bottom:1px solid #1e3a55;text-align:center;color:#cde}\n"
            "tr:hover td{background:#1a3050}\n"
            ".sec{color:#4fc3f7;font-size:1.05em;font-weight:bold;"
            "border-bottom:2px solid #2e4a6a;padding-bottom:6px;margin:24px 0 14px}\n"
            "</style></head>\n<body>\n"
            "<h1>&#128202;&nbsp;NVL816-BLLC Cross-Program Comparison</h1>\n"
            f"<p class='ts'>Generated: {ts_now}&nbsp;|&nbsp;Latest op per program</p>\n"
            f"<div style='margin-bottom:14px'>{legend}</div>\n"
            "<div class='sec'>Summary&ensp;"
            "<button onclick=\"_csvDl('cmp-all-tbl','NVL_Compare_Programs.csv')\" "
            "title='Download CSV' style='background:none;border:1px solid #4fc3f7;"
            "color:#4fc3f7;border-radius:4px;padding:1px 8px;cursor:pointer;"
            "font-size:0.82em;vertical-align:middle'>&#128190; CSV</button></div>\n"
            "<table id='cmp-all-tbl'><thead><tr>"
            "<th>Program</th><th>Die</th>"
            "<th>FF<br><span style='font-weight:normal;font-size:0.85em'>(1+2)</span></th>"
            "<th>FF+DF<br><span style='font-weight:normal;font-size:0.85em'>(1+2+3+4)</span></th>"
            "<th>UPM<br><span style='font-weight:normal;font-size:0.85em'>(Med %)</span></th>"
            "<th style='color:#5dade2'>DLCP<br><span style='font-weight:normal;font-size:0.85em'>(HP)</span></th>"
            "<th style='color:#f0a500'>DLCP<br><span style='font-weight:normal;font-size:0.85em'>(LP)</span></th>"
            "<th>FB198<br><span style='font-weight:normal;font-size:0.85em'>(Vmin&nbsp;Repair)</span></th>"
            "<th>FB201<br><span style='font-weight:normal;font-size:0.85em'>(Vnom&nbsp;Repair)</span></th>"
            "<th>FB202<br><span style='font-weight:normal;font-size:0.85em'>(Vmax&nbsp;Repair)</span></th>"
            f"</tr></thead><tbody>{table_rows}</tbody></table>\n"
            f"<p class='ts'>FF Target: {ff_tgt_s}&nbsp;|&nbsp;FF+DF Target: {ff_df_tgt_s}</p>\n"
            "<div class='sec'>Visual Comparison</div>\n"
            f"{chart_html}\n"
            "<div class='sec'>Bin Distribution</div>\n"
            f"<div style='display:flex;gap:18px;flex-wrap:wrap'>{bindist_cols}</div>\n"
            + _CSV_DL_SCRIPT +
            "\n</body></html>"
        )
        try:
            out_path = auto_dir / "output" / "compare" / "compare_report_ALL.html"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(html, encoding="utf-8")
            return out_path
        except Exception:
            return None

    def _repair_cells(smry: dict | None) -> str:
        if not smry:
            return "<td>\u2013</td>" * 6  # UPM, HP, LP, FB198, FB201, FB202
        rb      = smry.get("repair_bins", {})
        dlcp    = smry.get("dlcp", {})
        upm_med = smry.get("upm_med")
        upm_cell = f"<td>{upm_med}</td>" if upm_med else "<td>\u2013</td>"
        if not upm_med:  # UPM absent → DLCP unreliable, show – too
            hp_cell = "<td>\u2013</td>"
            lp_cell = "<td>\u2013</td>"
        elif dlcp:
            _span = "<span style='color:#546e7a;font-size:10px'>"
            _hp_ns = f"<br>{_span}({dlcp['hp_n']:,})</span>" if dlcp.get('hp_n') is not None else ""
            _lp_ns = f"<br>{_span}({dlcp['lp_n']:,})</span>" if dlcp.get('lp_n') is not None else ""
            _hp_val = float(dlcp['hp'].rstrip('%')) if dlcp.get('hp') else 0.0
            _hp_col = "#4caf50" if _hp_val >= 30 else "#ef5350"
            hp_cell = f"<td style='color:{_hp_col};font-weight:bold'>{dlcp['hp']}{_hp_ns}</td>"
            lp_cell = f"<td style='color:#f0a500'>{dlcp['lp']}{_lp_ns}</td>"
        else:
            hp_cell = "<td>\u2013</td>"
            lp_cell = "<td>\u2013</td>"  # noqa
        def _rv(n: int) -> str:
            return f"<td>{rb.get(str(n), chr(8211))}</td>"
        vnom = rb.get("201") or rb.get("2") or "\u2013"
        return upm_cell + hp_cell + lp_cell + _rv(198) + f"<td>{vnom}</td>" + _rv(202)

    def _cells(smry: dict | None) -> str:
        if not smry:
            return "<td>–</td>" * 4
        bins = smry.get("bins", {})
        b1 = _parse_pct(bins.get("Bin 1", "0"))
        b2 = _parse_pct(bins.get("Bin 2", "0"))
        b3 = _parse_pct(bins.get("Bin 3", "0"))
        b4 = _parse_pct(bins.get("Bin 4", "0"))
        ff    = b1 + b2
        ff_df = b1 + b2 + b3 + b4
        ff_col    = "#66bb6a" if (_ff_tgt    is None or ff    >= _ff_tgt)    else "#ef5350"
        ff_df_col = "#66bb6a" if (_ff_df_tgt is None or ff_df >= _ff_df_tgt) else "#ef5350"
        ff_tgt_s    = f"{_ff_tgt:.1f}%"    if _ff_tgt    is not None else "–"
        ff_df_tgt_s = f"{_ff_df_tgt:.1f}%" if _ff_df_tgt is not None else "–"
        return (
            f"<td style='color:{ff_col};font-weight:bold'>{ff:.1f}%</td>"
            f"<td style='color:#546e7a'>{ff_tgt_s}</td>"
            f"<td style='color:{ff_df_col};font-weight:bold'>{ff_df:.1f}%</td>"
            f"<td style='color:#546e7a'>{ff_df_tgt_s}</td>"
        )

    # Load excluded_ops so comparison history respects the same filter as email
    _excl_ops: set[str] = set()
    try:
        _ec = json.loads(Path(_EMAIL_CFG).read_text(encoding="utf-8"))
        _excl_ops = {str(o) for o in _ec.get("excluded_ops", [])}
    except Exception:
        pass

    def _title_plot_btn(cp: Path) -> str:
        if cp and cp.exists():
            uri = cp.as_uri()
            return (
                f"<button class='cmp-plot-btn' style='font-size:2em;line-height:1;padding:0 4px' "
                f"onclick=\"window.open('{uri}','_blank','popup,width=1400,height=900')\" "
                f"title='View trend chart'>&#128202;</button>"
            )
        return ""

    _sub = "<span style='font-weight:normal;font-size:0.85em'>"
    hdr = (
        "<th>Die</th>"
        f"<th>FF<br>{_sub}(1+2)</span></th>"
        f"<th>FF&nbsp;Tgt<br>{_sub}(%)</span></th>"
        f"<th>FF+DF<br>{_sub}(1+2+3+4)</span></th>"
        f"<th>FF+DF&nbsp;Tgt<br>{_sub}(%)</span></th>"
    )
    hdr_card1 = hdr + (
        f"<th>UPM<br>{_sub}(Med %)</span></th>"
        f"<th style='color:#5dade2'>DLCP<br>{_sub}(HP)</span></th>"
        f"<th style='color:#f0a500'>DLCP<br>{_sub}(LP)</span></th>"
        f"<th>FB198<br>{_sub}(Vmin&nbsp;Repair)</span></th>"
        f"<th>FB201<br>{_sub}(Vnom&nbsp;Repair)</span></th>"
        f"<th>FB202<br>{_sub}(Vmax&nbsp;Repair)</span></th>"
    )

    _MAX_ROWS = 4
    _HDR_H    = 34   # px
    _ROW_H    = 30   # px

    # ── Card 1: latest op per TP letter (supplement with history for missing letters) ────
    letter_best: dict[str, tuple] = {}   # letter -> (item, hist_date_str|None)
    for letter, entries in sorted_groups:
        fresh = [(op, item) for op, item in entries if not _is_stale(item)]
        _, best = fresh[0] if fresh else entries[0]
        letter_best[letter] = (best, None)

    # Scan history to fill in letters absent from today's run.
    # Prefer the most-recent *tagged* run per letter; fall back to latest.
    try:
        def _folder_ts(d: Path) -> str:
            m = re.search(r'\d{8}_\d{6}', d.name)
            return m.group(0) if m else ''
        _hist_dirs = sorted(
            [d for d in run_dir.parent.iterdir()
             if d.is_dir() and d.name.startswith("NVL_0H61") and d != run_dir],
            key=_folder_ts, reverse=True,
        )[:30]
        _hist_latest: dict = {}  # letter -> (item, hdate)  — most recent
        _hist_tagged: dict = {}  # letter -> (item, hdate)  — most recent *tagged*
        for _rf in _hist_dirs:
            _m_rf  = re.search(r"(\d{8})_(\d{6})", _rf.name)
            _hdate = (
                f"{_m_rf.group(1)[:4]}-{_m_rf.group(1)[4:6]}-{_m_rf.group(1)[6:]}"
                if _m_rf else _rf.name
            )
            _rf_tagged = (_rf / ".tag").exists()
            try:
                _td_list = sorted(_rf.iterdir())
            except Exception:
                continue  # skip unreadable folder
            for _td in _td_list:
                if not _td.is_dir() or _td.name.endswith("_R0"):
                    continue
                _ml = re.search(r"[0-9A-Za-z]H61([A-Za-z])", _td.name)
                if not _ml:
                    continue
                _let = _ml.group(1).upper()
                if _let in letter_best:
                    continue   # covered by current run
                _op_m = re.search(r"_(\d{5,6})$", _td.name)
                if _op_m and _op_m.group(1) in _excl_ops:
                    continue   # skip excluded op
                _hitem = ((_td.name, True, _td, f"prev: {_hdate}"), _hdate)
                if _let not in _hist_latest:
                    _hist_latest[_let] = _hitem   # newest (first hit desc)
                if _rf_tagged and _let not in _hist_tagged:
                    _hist_tagged[_let] = _hitem   # newest tagged
        # Prefer tagged over latest; latest is the fallback
        for _let, _hitem in _hist_latest.items():
            letter_best[_let] = _hist_tagged.get(_let, _hitem)
    except Exception:
        pass

    rows1 = ""
    row_count1 = 0
    letter_smry_data: list = []
    for letter, (best, hist_date) in sorted(letter_best.items(), reverse=True):
        smry      = _extract_yield_summary(Path(best[2]))
        die       = smry.get("die", "–") if smry else "–"
        # Outlook doesn't support opacity on <tr>; dim history rows by styling
        # each cell's text colour instead.
        _dim_style = " style='color:#78909c'" if hist_date else ""
        note      = f"&nbsp;<span class='ts' style='font-size:0.78em'>({hist_date})</span>" if hist_date else ""
        _idx_p    = Path(best[2]) / "index.html"
        _idx_link = (
            f"&nbsp;<a href='{_idx_p.as_uri()}' style='color:#4fc3f7;font-size:0.82em'"
            f" title='Open Dashboard'>&#128279;</a>"
            if _idx_p.exists() else ""
        )
        # For history rows use plain dimmed cells (override coloured yield cells)
        if hist_date:
            _r_cells = (
                f"<td{_dim_style}>{die}</td>"
                + re.sub(r"style='color:#[0-9a-fA-F]+;?", f"style='color:#78909c;", _cells(smry) + _repair_cells(smry))
            )
        else:
            _r_cells = f"<td>{die}</td>{_cells(smry)}{_repair_cells(smry)}"
        rows1 += (
            f"<tr>"
            f"<td class='cmp-prog'{_dim_style}>0H61{letter}{_idx_link}{note}</td>"
            f"<td class='cmp-op'{_dim_style}>{_op(best)}</td>"
            f"{_r_cells}"
            f"</tr>\n"
        )
        letter_smry_data.append((letter, smry, Path(best[2])))
        row_count1 += 1

    cross_all_path = _write_cross_compare_html(letter_smry_data, _auto_dir) if len(letter_smry_data) > 1 else None
    cross_all_btn  = _title_plot_btn(cross_all_path) if cross_all_path else ""

    scroll1 = (
        f"max-height:{_HDR_H + _MAX_ROWS * _ROW_H}px;overflow-y:auto;"
        if row_count1 > _MAX_ROWS else ""
    )
    card1 = (
        "<div class='cmp-card'>"
        f"<div class='cmp-title'>&#128202;&nbsp;Compare by Test Program &mdash; latest op per program{cross_all_btn}"
        "<button class='cmp-plot-btn' onclick=\"_csvDl('cmp-tbl-1','NVL_Compare_Programs.csv')\" "
        "title='Download table as CSV' style='font-size:1em'>&#128190;</button></div>"
        f"<div style='overflow-x:auto;{scroll1}'><table id='cmp-tbl-1' class='cmp-tbl'><thead>"
        f"<tr><th>Program</th><th>Op</th>{hdr_card1}</tr></thead>"
        f"<tbody>{rows1}</tbody></table></div>"
        "</div>"
    )

    # ── Card 2: current-letter run history ─────────────────────────────────────
    _m_curr_ltr = re.search(r'NVL_0H61([A-Za-z])', run_dir.name)
    _curr_ltr = _m_curr_ltr.group(1).upper() if _m_curr_ltr else 'C'
    rows2 = ""
    row_count2 = 0
    card2_cp = None
    try:
        output_dir  = run_dir.parent
        def _folder_ts2(d: Path) -> str:
            m = re.search(r'\d{8}_\d{6}', d.name)
            return m.group(0) if m else ''
        # Sort by timestamp descending; deduplicate so old + new style folders
        # for the same program+run don't create duplicate rows.
        # Key includes the program letter so NVL_0H61A_TS and NVL_0H61C_TS
        # from the same automation run are treated as separate entries.
        _rf_all = sorted(
            [d for d in output_dir.iterdir()
             if d.is_dir() and d.name.startswith("NVL_0H61")],
            key=_folder_ts2, reverse=True,
        )
        _seen_ts: set[str] = set()
        run_folders: list[Path] = []
        for _rfd in _rf_all:
            _m_ltr = re.search(r'NVL_0H61([A-Za-z])', _rfd.name)
            _rk = (f"{_m_ltr.group(1)}_{_folder_ts2(_rfd)}"
                   if _m_ltr else _folder_ts2(_rfd))
            if _rk not in _seen_ts:
                _seen_ts.add(_rk)
                run_folders.append(_rfd)
            if len(run_folders) >= 60:
                break
        for rf in run_folders:
            m = re.search(r"(\d{8})_(\d{6})", rf.name)
            date_str = (
                f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"
                f" ({m.group(2)[:2]}:{m.group(2)[2:4]})"
            ) if m else rf.name
            c_dirs = sorted(
                [d for d in rf.iterdir()
                 if d.is_dir() and re.search(rf"0H61{_curr_ltr}", d.name)
                 and not d.name.endswith("_R0")
                 and not any(d.name.endswith(f"_{op}") for op in _excl_ops)],
                key=lambda d: int(re.search(r"_(\d{5,6})$", d.name).group(1))
                              if re.search(r"_(\d{5,6})$", d.name) else 0,
                reverse=True,
            )
            if not c_dirs:
                continue
            c_dir  = c_dirs[0]
            m_op   = re.search(r"_(\d{5,6})$", c_dir.name)
            op_str = m_op.group(1) if m_op else "?"
            smry   = _extract_yield_summary(c_dir)
            die    = smry.get("die", "–") if smry else "–"
            cur    = (rf == run_dir)
            cls    = " class='cmp-current'" if cur else ""
            mark   = " &#9664;&nbsp;current" if cur else ""
            cp     = _auto_dir / f"compare_report_{c_dir.name}.html"
            if card2_cp is None:
                card2_cp = cp
            _idx2   = c_dir / "index.html"
            _idx2_lnk = (
                f"&nbsp;<a href='{_idx2.as_uri()}' style='color:#4fc3f7;font-size:0.82em' title='Open Dashboard'>&#128279;</a>"
                if _idx2.exists() else ""
            )
            rows2 += (
                f"<tr{cls}>"
                f"<td class='cmp-date'>{date_str}{mark}{_idx2_lnk}</td>"
                f"<td class='cmp-op'>{op_str}</td>"
                f"<td>{die}</td>{_cells(smry)}{_repair_cells(smry)}"
                f"</tr>\n"
            )
            row_count2 += 1
    except Exception:
        pass

    if not rows2:
        rows2 = f"<tr><td colspan='99' class='ts' style='padding:8px'>No 61{_curr_ltr} run history found.</td></tr>"

    scroll2 = (
        f"max-height:{_HDR_H + _MAX_ROWS * _ROW_H}px;overflow-y:auto;"
        if row_count2 > _MAX_ROWS else ""
    )
    card2 = (
        "<div class='cmp-card'>"
        f"<div class='cmp-title'>&#128337;&nbsp;61{_curr_ltr} Run History &mdash; newest first{_title_plot_btn(card2_cp)}"
        f"<button class='cmp-plot-btn' onclick=\"_csvDl('cmp-tbl-2','NVL_61{_curr_ltr}_History.csv')\" "
        "title='Download table as CSV' style='font-size:1em'>&#128190;</button></div>"
        f"<div style='overflow-x:auto;{scroll2}'><table id='cmp-tbl-2' class='cmp-tbl'><thead>"
        f"<tr><th>Run Date</th><th>Op</th>{hdr_card1}</tr></thead>"
        f"<tbody>{rows2}</tbody></table></div></div>"
    )

    return (
        "<div class='cmp-section'>"
        "<div class='cmp-section-hdr'>&#9660;&nbsp;Comparison Summary</div>"
        f"{card2}{card1}"
        "</div>"
    )


# Shared CSV-download JS — raw string so \r\n in the JS stays as literal backslash sequences
_CSV_DL_SCRIPT = r"""<script>
function _csvDl(tblId,fname){
  var t=document.getElementById(tblId);if(!t)return;
  var r=[],ths=t.querySelectorAll('thead th'),h=[];
  for(var i=0;i<ths.length;i++)h.push('"'+ths[i].innerText.replace(/[\r\n]+/g,' ').trim()+'"');
  r.push(h.join(','));
  var trs=t.querySelectorAll('tbody tr');
  for(var j=0;j<trs.length;j++){var cells=trs[j].querySelectorAll('td'),ro=[];
    for(var k=0;k<cells.length;k++)ro.push('"'+cells[k].innerText.replace(/[\r\n]+/g,' ').trim()+'"');
    r.push(ro.join(','));}
  var a=document.createElement('a');
  a.href='data:text/csv;charset=utf-8,'+encodeURIComponent(r.join('\r\n'));
  a.download=fname;a.click();
}
</script>"""

# ─────────────────────────────────────────────────────────────────────────────
# Step 3b — Build per-run report.html with embedded BinDistribution
# ─────────────────────────────────────────────────────────────────────────────

def _build_run_report(
    run_dir: Path,
    run_ts: str,
    aqua_file: str,
    tp_results: list[tuple[str, bool, Path]],  # (tp_key, ok, tp_output_dir[, gz_ts])
    out_path: Path | None = None,
) -> Path:
    """
    Write run_dir/report.html.
    - Grouped by program letter (61A / 61B / 61C …) as collapsible top-level categories.
    - Within each category, entries sorted by op number descending (newest op first).
    - BinDistribution collapsed by default; inlined via srcdoc so the file is
      self-contained when saved from email to Downloads.
    """
    import html as _html_mod
    from collections import defaultdict

    # ── group by program letter, sort by op descending within each group ──────
    # key e.g. NCXSDJXL0H61C002620_132322  →  letter='C', op='132322'
    def _parse(tp_key: str):
        m = re.search(r'61([A-Za-z]).*?_(\d{5,6})$', tp_key)
        return (m.group(1).upper(), m.group(2)) if m else ('?', '0')

    groups: dict[str, list] = defaultdict(list)
    for item in tp_results:           # (tp_key, ok, tp_dir[, gz_ts])
        letter, op = _parse(item[0])
        if letter == '?':
            continue   # skip _R0 dirs and unparseable keys
        groups[letter].append((op, item))

    # Sort groups by letter descending (61C first); within each group sort by op descending (newest first)
    sorted_groups = sorted(groups.items(), reverse=True)
    for letter, entries in sorted_groups:
        entries.sort(key=lambda x: x[0], reverse=True)

    compare_html = _build_compare_section(sorted_groups, run_dir)

    # ── build HTML ────────────────────────────────────────────────────────────
    categories_html = ""
    _first_card = False  # all BinDist dropdowns start collapsed
    for letter, entries in sorted_groups:
        prog_name  = f"0H61{letter}"
        ok_count   = sum(1 for _, item in entries if item[1])
        fail_count = len(entries) - ok_count
        cat_status = (f'<span class="ok">{ok_count} OK</span>'
                      if fail_count == 0 else
                      f'<span class="ok">{ok_count} OK</span>'
                      f'&ensp;<span class="fail">{fail_count} FAILED</span>')

        tp_cards = ""
        for op_num, item in entries:
            tp_key     = item[0]
            ok         = item[1]
            tp_dir     = item[2]
            gz_ts      = item[3] if len(item) > 3 else ""
            r0_label   = item[4] if len(item) > 4 else ""
            r0_dir     = Path(item[5]) if len(item) > 5 and item[5] else None
            is_stale   = gz_ts.startswith("prev:")
            status_cls = "ok" if ok else "fail"
            status_txt = "&#10004; OK" if ok else "&#10008; FAILED"
            if is_stale:
                ts_badge = f'<span class="gz-ts stale-badge">&#128337; {gz_ts}</span>'
            else:
                ts_badge = f'<span class="gz-ts">{gz_ts}</span>' if gz_ts else ""
            r0_badge = f'<span class="r0-badge">&#128204; {r0_label}</span>' if r0_label else ""

            # Links: plain run + R0 run (if available)
            index_path  = tp_dir / "index.html"
            pcm_path    = tp_dir / "pcm_analysis.html"
            compare_path = run_dir.parent / "compare" / f"compare_report_{tp_key}.html"
            links = ""
            if index_path.exists():
                links += f'<a href="{index_path.as_uri()}">Full Dashboard</a>'
            if pcm_path.exists():
                links += f' &nbsp;&nbsp; <a href="{pcm_path.as_uri()}">PCM Analysis</a>'

            if r0_dir and r0_dir.exists():
                r0_index = r0_dir / "index.html"
                r0_pcm   = r0_dir / "pcm_analysis.html"
                if r0_index.exists():
                    links += f'<br><a href="{r0_index.as_uri()}">Full Dashboard (+ R0)</a>'
                if r0_pcm.exists():
                    links += f' &nbsp;&nbsp; <a href="{r0_pcm.as_uri()}">PCM Analysis (+ R0)</a>'

            # BinDistribution: always use plain (non-R0) dir
            bd_search_dir = tp_dir

            # Inline BinDistribution via srcdoc — open only for latest 61C, collapsed otherwise
            bd_open = not is_stale and bool(re.search(r'0H61C', tp_key))
            bindist_block = '<p class="ts">BinDistribution not available.</p>'
            if bd_search_dir.exists():
                bdfiles = sorted(bd_search_dir.glob("*BinDistribution*.html"))
                if bdfiles:
                    try:
                        bd_content = bdfiles[0].read_text(encoding="utf-8", errors="replace")
                        bd_escaped = _html_mod.escape(bd_content, quote=True)
                        bindist_block = (
                            f'<iframe srcdoc="{bd_escaped}" width="100%" height="940"'
                            f' style="border:none;display:block;margin-top:8px;'
                            f'background:#fff;border-radius:4px"></iframe>'
                        )
                    except Exception as _e:
                        bindist_block = f'<p class="ts">BinDistribution read error: {_e}</p>'

            bd_display = "block" if bd_open else "none"
            bd_arrow   = "&#x25BC;" if bd_open else "&#x25B6;"

            tp_cards += f"""
<div class="tp-card">
  <div class="tp-card-hdr">
    <span class="tp-name">{tp_key}</span>
    {ts_badge}
    {r0_badge}
    <span class="{status_cls}">{status_txt}</span>
  </div>
  <p class="links">{links if links else '<span class="ts">no output links</span>'}</p>
  <div class="bd-wrap">
    <div class="bd-hdr" onclick="(function(h){{var b=h.nextElementSibling,a=h.querySelector('.bd-arr');if(b.style.display==='none'){{b.style.display='block';a.textContent='\u25BC';}}else{{b.style.display='none';a.textContent='\u25B6';}}}})(this)">
      <span class="bd-arr">{bd_arrow}</span>&nbsp;Bin Distribution
    </div>
    <div class="bd-body" style="display:{bd_display}">{bindist_block}</div>
  </div>
</div>"""
            _first_card = False

        categories_html += f"""
<details class="prog-group" open>
  <summary class="prog-summary">
    <span class="prog-name">{prog_name}</span>
    <span class="prog-meta">{len(entries)} op(s)&ensp;&bull;&ensp;{cat_status}</span>
  </summary>
  <div class="tp-cards">
    {tp_cards}
  </div>
</details>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>NVL816-BLLC Yield Report \u2014 {run_ts}</title>{_WATERMARK_CSS}<style>
body {{ font-family: Segoe UI, Arial, sans-serif; background:#1a252f; color:#e8f0f7;
       margin:0; padding:16px 28px 60px; }}
h1   {{ color:#4fc3f7; border-bottom:2px solid #4fc3f7; padding-bottom:8px; margin-bottom:4px; }}
.ts  {{ color:#90a4ae; font-size:0.85em; margin-top:0; }}
.ok  {{ color:#66bb6a; font-size:0.85em; font-weight:bold; }}
.fail{{ color:#ef5350; font-size:0.85em; font-weight:bold; }}
.links {{ margin:2px 0 8px; }}
.links a {{ color:#4fc3f7; text-decoration:none; margin-right:16px; font-size:0.9em; }}
.links a:hover {{ text-decoration:underline; }}

/* ── Program category block ── */
.prog-group {{ border:2px solid #2e4a6a; border-radius:8px; margin-bottom:28px;
               background:#1a2e40; }}
.prog-summary {{ display:flex; align-items:baseline; gap:14px; padding:12px 18px;
                 cursor:pointer; list-style:none; user-select:none; }}
.prog-summary::-webkit-details-marker {{ display:none; }}
.prog-summary::before {{ content:"\\25B6  "; color:#4fc3f7; font-size:0.75em; }}
details[open] > .prog-summary::before {{ content:"\\25BC  "; }}
.prog-name {{ color:#4fc3f7; font-size:1.15em; font-weight:bold; }}
.prog-meta {{ color:#90a4ae; font-size:0.85em; }}

/* ── Per-op cards inside a category ── */
.tp-cards {{ padding:0 14px 14px; display:flex; flex-direction:column; gap:14px; }}
.tp-card  {{ border:1px solid #263950; border-radius:6px; padding:12px 16px 14px;
             background:#1e2e3d; }}
.tp-card-hdr {{ display:flex; align-items:center; gap:12px; margin-bottom:6px; flex-wrap:wrap; }}
.tp-name {{ color:#80cbc4; font-size:0.88em; font-weight:bold; font-family:monospace; word-break:break-all; }}
.gz-ts   {{ color:#90a4ae; font-size:0.78em; white-space:nowrap; }}
.stale-badge {{ color:#ffa726; font-size:0.78em; white-space:nowrap; font-style:italic; }}
.r0-badge    {{ color:#ce93d8; font-size:0.78em; white-space:nowrap; font-weight:bold; }}

/* ── BinDist toggle (open by default; click header to collapse) ── */
.bd-wrap {{ margin-top:6px; }}
.bd-hdr  {{ cursor:pointer; color:#4fc3f7; font-size:0.88em; padding:4px 0;
            user-select:none; display:flex; align-items:center; gap:4px; }}
.bd-hdr:hover {{ color:#80cbc4; }}
.bd-arr  {{ font-size:0.72em; }}
.bd-body {{ margin-top:4px; }}
.bd-link {{ color:#4fc3f7; font-size:0.82em; text-decoration:none; display:block;
            margin-bottom:4px; }}
.bd-link:hover {{ text-decoration:underline; }}
/* ── BinDist toggle (collapsed by default) ── */
details > summary {{ cursor:pointer; color:#4fc3f7; font-size:0.88em;
                     padding:4px 0; list-style:none; user-select:none; }}
details > summary::-webkit-details-marker {{ display:none; }}
details > summary::before {{ content:"\\25B6  "; font-size:0.75em; }}
details[open] > summary::before {{ content:"\\25BC  "; }}
/* ── Comparison cards ── */
.cmp-section {{ margin-bottom:32px; }}
.cmp-section-hdr {{ color:#4fc3f7; font-size:1.05em; font-weight:bold;
                    padding:8px 0 10px; border-bottom:2px solid #2e4a6a; margin-bottom:14px; }}
.cmp-card {{ background:#1e2e3d; border:1px solid #263950; border-radius:8px;
             padding:14px 18px; margin-bottom:14px; }}
.cmp-title {{ color:#80cbc4; font-size:0.88em; font-weight:bold; margin-bottom:8px; display:flex; align-items:center; gap:8px; }}
.cmp-tbl {{ border-collapse:collapse; font-size:0.95em; min-width:400px; }}
.cmp-tbl th {{ background:#263950; color:#4fc3f7; padding:5px 12px; text-align:center;
               white-space:nowrap; }}
.cmp-tbl td {{ padding:4px 12px; border-bottom:1px solid #1e3a55; text-align:center; color:#cde; }}
.cmp-tbl tr:hover td {{ background:#1a3050; }}
.cmp-prog {{ color:#80cbc4; font-weight:bold; font-family:monospace; text-align:left!important; }}
.cmp-op   {{ color:#90a4ae; font-family:monospace; }}
.cmp-date {{ color:#90a4ae; text-align:left!important; white-space:nowrap; }}
tr.cmp-current td {{ background:#1d3a52!important; }}
tr.cmp-current .cmp-date {{ color:#ffa726; font-weight:bold; }}
.cmp-plot-btn {{ background:none; border:none; cursor:pointer; font-size:1.1em;
                 padding:1px 4px; border-radius:4px; line-height:1;
                 transition:background 0.15s; }}
.cmp-plot-btn:hover {{ background:#1d4060; }}
</style>
</head><body>
{_WATERMARK_HTML}
<h1>NVL816-BLLC Yield Report</h1>
<p class="ts">Run: <b>{run_ts}</b>&ensp;&bull;&ensp;AQUA: {Path(aqua_file).name}&ensp;&bull;&ensp;{len(tp_results)} program(s) ({sum(1 for t in tp_results if not (len(t)>3 and str(t[3]).startswith('prev:')))} updated, {sum(1 for t in tp_results if len(t)>3 and str(t[3]).startswith('prev:'))} from previous runs)</p>
{compare_html}
{categories_html}
{_CSV_DL_SCRIPT}
</body></html>
"""
    report_path = out_path or (run_dir / "report.html")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(html, encoding="utf-8")
    _log(f"Report: {report_path}  ({report_path.stat().st_size:,} bytes)")
    return report_path


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Update run_log.html
# ─────────────────────────────────────────────────────────────────────────────

_RUN_LOG_CSS = """
<style>
  body { font-family: Segoe UI, Arial, sans-serif; background:#1a252f; color:#e8f0f7; margin:24px; }
  h1   { color:#4fc3f7; border-bottom:2px solid #4fc3f7; padding-bottom:8px; }
  h2   { color:#80cbc4; margin-top:32px; }
  table{ border-collapse:collapse; width:100%; margin-top:8px; }
  th   { background:#263950; color:#4fc3f7; padding:8px 12px; text-align:left; }
  td   { padding:6px 12px; border-bottom:1px solid #263950; }
  tr:hover td { background:#1e3044; }
  .ok  { color:#66bb6a; font-weight:bold; }
  .fail{ color:#ef5350; font-weight:bold; }
  .ts  { color:#90a4ae; font-size:0.85em; }
  a    { color:#4fc3f7; text-decoration:none; }
  a:hover { text-decoration:underline; }
</style>
"""

_RUN_LOG_HEADER = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Yield Dashboard — Run Log</title>
{css}
</head>
<body>
<h1>Yield Dashboard — Automation Run Log</h1>
<p class="ts">Auto-generated by run_automation.py &nbsp;|&nbsp;
Updated: <span id="ts">{ts}</span></p>
<!-- RUNS -->
""".format(css=_RUN_LOG_CSS, ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

_RUN_LOG_FOOTER = "\n</body>\n</html>\n"


def _make_run_section(
    run_ts: str,
    aqua_file: str,
    results: list[tuple[str, bool, str]],   # (op_code, ok, output_dir)
    report_path: Path | None = None,
) -> str:
    rows_html = ""
    for r in results:
        op_code, ok, output_dir = r[0], r[1], r[2]
        index_html = Path(output_dir) / "index.html"
        link   = f'<a href="{index_html.as_uri()}">{op_code}</a>' if index_html.exists() else op_code
        status = '<span class="ok">&#10004; OK</span>' if ok else '<span class="fail">&#10008; FAILED</span>'
        rows_html += f"<tr><td>{link}</td><td>{status}</td><td class='ts'>{output_dir}</td></tr>\n"

    report_link = ""
    if report_path and report_path.exists():
        report_link = f' &nbsp;|&nbsp; <a href="{report_path.as_uri()}">&#128196; Report</a>'

    ops_str = ", ".join(r[0] for r in results)
    return f"""
<h2>Run: {run_ts} &mdash; op(s) updated: {ops_str}</h2>
<p class="ts">AQUA: {Path(aqua_file).name}{report_link}</p>
<table>
  <tr><th>Operation</th><th>Status</th><th>Output</th></tr>
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
        # Prepend: insert latest section right after <!-- RUNS --> so newest is on top
        if "<!-- RUNS -->" in existing:
            updated = existing.replace("<!-- RUNS -->", "<!-- RUNS -->\n" + section, 1)
        elif "</body>" in existing:
            # Fallback for older log files without the marker
            updated = existing.replace("</body>", section + "\n</body>", 1)
        else:
            updated = existing + section
    else:
        updated = _RUN_LOG_HEADER + section + _RUN_LOG_FOOTER

    run_log.write_text(updated, encoding="utf-8")
    _log(f"Run log updated: {run_log}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Send email
# ─────────────────────────────────────────────────────────────────────────────

def _email_body_html(
    run_ts: str,
    aqua_file: str,
    results: list[tuple],
    run_log: Path,
    attachments: list[str] | None = None,
) -> str:
    # Sort: 61C first (letter descending), then by op number descending within each letter
    def _email_sort_key(r):
        m = re.search(r'61([A-Za-z]).*?_(\d{5,6})$', r[0])
        letter = m.group(1).upper() if m else '?'
        op     = int(m.group(2)) if m else 0
        return (letter, op)   # both descending → negate with reverse=True
    sorted_results = sorted(results, key=_email_sort_key, reverse=True)

    rows = ""
    for r in sorted_results:
        run_tag, ok, output_dir = r[0], r[1], r[2]
        r0_dir    = r[3] if len(r) > 3 else ""
        stale_lbl = r[4] if len(r) > 4 else ""   # "prev: 2026-05-17" when not run today
        if stale_lbl:
            status = f"&#8212; {stale_lbl}"
            color  = "#90a4ae"
            row_bg = "background:#f5f5f5;"
        else:
            status = "✔ OK" if ok else "✖ FAILED"
            color  = "#66bb6a" if ok else "#ef5350"
            row_bg = ""
        index  = Path(output_dir) / "index.html"
        link   = f'<a href="{index.as_uri()}">{run_tag}</a>' if index.exists() else run_tag
        rows += (
            f"<tr style='{row_bg}'><td>{link}</td>"
            f"<td style='color:{color};font-weight:bold'>{status}</td>"
            f"<td style='color:#555'>{output_dir}</td></tr>\n"
        )
        if r0_dir:
            r0_index = Path(r0_dir) / "index.html"
            r0_link  = f'<a href="{r0_index.as_uri()}">{run_tag}_R0</a>' if r0_index.exists() else f'{run_tag}_R0'
            rows += (
                f"<tr style='background:#f0f7ff'>"
                f"<td style='padding-left:24px;color:#0071c5'>&#8627; {r0_link}</td>"
                f"<td style='color:#0071c5;font-weight:bold'>+ R0</td>"
                f"<td style='color:#0071c5'>{r0_dir}</td></tr>\n"
            )

    overall = "OK" if all(r[1] for r in results) else "FAILED"

    att_note = ""
    if attachments:
        links = "".join(
            f" &nbsp;&middot;&nbsp; <b>{Path(a).name}</b>"
            for a in attachments if Path(a).exists()
        )
        att_note = (f'<p style="background:#f0f7ff;padding:8px;border-left:4px solid #0071c5">'
                    f'<b>Attachment:</b>{links}</p>')

    return f"""
<html><body style="font-family:Segoe UI,Arial;color:#222;max-width:720px">
<h2 style="color:#0071c5;margin-bottom:4px">Yield Dashboard — {overall}</h2>
<p style="color:#555;font-size:0.9em;margin-top:0">{run_ts}</p>
{att_note}
<table border="1" cellpadding="6" cellspacing="0"
       style="border-collapse:collapse;width:100%;font-size:0.9em">
  <tr style="background:#0071c5;color:#fff">
    <th>Run</th><th>Status</th><th>Output folder</th>
  </tr>
  {rows}
</table>
<p style="color:#888;font-size:0.8em;margin-top:12px">
  AQUA: {aqua_file}<br>
  Full history: <a href="{run_log.as_uri()}">run_log.html</a> (attached)
</p>
</body></html>
"""


def _build_email_report_html(output_dir: Path, run_ts: str,
                             excluded_keys: list | None = None) -> str:
    """Build self-contained HTML with sidebar tabs.

    Tabs: Summary (latest per program) | 0H61A | 0H61B | ...
    Columns: Run Date | Op | Die | FF(1+2) | FF Tgt | FF+DF(1+2+3+4) |
             FF+DF Tgt | UPM Med% | DLCP HP | DLCP LP | FB198 | FB201 | FB202
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
        prog_key = f"{gen}{letter}"   # e.g. "61E", "62A"
        dt_str = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}"
        for tp_dir in sorted(rd.iterdir()):
            if not tp_dir.is_dir():
                continue
            if tp_dir.name in _excluded:
                continue
            tm = tp_pattern.search(tp_dir.name)
            if not tm or tm.group(1) != gen or tm.group(2).upper() != letter or tp_dir.name.endswith("_R0"):
                continue
            history[prog_key].append({
                "ts":      ts,
                "dt_str":  dt_str,
                "op":      tm.group(3),
                "tp_key":  tp_dir.name,
                "tp_dir":  tp_dir,
                "summary": _extract_yield_summary(tp_dir),
            })

    for prog_key in history:
        history[prog_key].sort(key=lambda x: x["ts"], reverse=True)
    # Sort by generation number desc, then letter desc: 62A > 61E > 61D > … > 61A
    sorted_letters = sorted(
        history.keys(),
        key=lambda k: (int(k[:-1]), k[-1]),
        reverse=True,
    )

    # ── helpers ───────────────────────────────────────────────────────────────
    def _pct_val(s):
        try:
            return float((s or "").rstrip('%'))
        except Exception:
            return 0.0

    def _sum_bins(sm, nums):
        if not sm:
            return "\u2013"
        bins = sm.get("bins", {})
        total = sum(_pct_val(bins.get(f"Bin {n}")) for n in nums)
        return f"{total:.1f}%" if total > 0 else "\u2013"

    def _yld_color(v_str):
        v = _pct_val(v_str)
        if v <= 0:
            return "#90a4ae"
        return "#66bb6a" if v >= 60 else "#ffa726" if v >= 40 else "#ef5350"

    def _get(sm, key, sub=""):
        if not sm:
            return "\u2013"
        if sub:
            return sm.get(key, {}).get(sub, "\u2013") or "\u2013"
        return sm.get(key, "\u2013") or "\u2013"

    def _idx_uri(entry):
        idx = entry["tp_dir"] / "index.html"
        return idx.as_uri() if idx.exists() else ""

    COL_HDR = (
        "<th>Material</th>"
        "<th>Run Date</th>"
        "<th>Op</th>"
        "<th>Die</th>"
        "<th>FF<br><small>(1+2)</small></th>"
        "<th>FF Tgt<br><small>(%)</small></th>"
        "<th>FF+DF<br><small>(1+2+3+4)</small></th>"
        "<th>FF+DF Tgt<br><small>(%)</small></th>"
        "<th>UPM<br><small>(Med %)</small></th>"
        "<th>DLCP<br><small>(HP)</small></th>"
        "<th>DLCP<br><small>(LP)</small></th>"
        "<th>FB198<br><small>(Vmin Repair)</small></th>"
        "<th>FB201<br><small>(Vnom Repair)</small></th>"
        "<th>FB202<br><small>(Vmax Repair)</small></th>"
    )

    def _data_row(entry, is_latest=False, prog_prefix="", material="", sub=False):
        sm    = entry["summary"]
        ff    = _sum_bins(sm, [1, 2])
        ffdf  = _sum_bins(sm, [1, 2, 3, 4])
        upm   = _get(sm, "upm_med")
        hp    = _get(sm, "dlcp", "hp")
        lp    = _get(sm, "dlcp", "lp")
        fb198 = _get(sm, "repair_bins", "198")
        fb201 = _get(sm, "repair_bins", "201")
        fb202 = _get(sm, "repair_bins", "202")
        die   = _get(sm, "die")
        link  = _idx_uri(entry)
        ff_tgt   = _get(sm, "ff_tgt")
        ffdf_tgt = _get(sm, "ffdf_tgt")
        date_cell = (f'<a href="{link}" class="tl">{entry["dt_str"]}</a>'
                     if link else entry["dt_str"])
        if is_latest and not sub:
            date_cell += ' <span class="latest-badge">latest</span>'
        if sub:
            row_cls = ' class="mat-sub-row"'
            mat_label = f'<span class="mat-sub-lbl">&#8627; {material}</span>'
        elif is_latest:
            row_cls = ' class="latest-row"'
            mat_label = material
        else:
            row_cls = ""
            mat_label = material
        mat_cell = f'<td class="c-mat mono">{mat_label}</td>'
        return (
            f'<tr{row_cls}>'
            f'{prog_prefix}'
            f'{mat_cell}'
            f'<td class="c-date">{date_cell}</td>'
            f'<td class="c-op mono">{entry["op"]}</td>'
            f'<td class="c-num">{die}</td>'
            f'<td class="c-num" style="color:{_yld_color(ff)};font-weight:bold">{ff}</td>'
            f'<td class="c-tgt">{ff_tgt}</td>'
            f'<td class="c-num" style="color:{_yld_color(ffdf)};font-weight:bold">{ffdf}</td>'
            f'<td class="c-tgt">{ffdf_tgt}</td>'
            f'<td class="c-num">{upm}</td>'
            f'<td class="c-num">{hp}</td>'
            f'<td class="c-num">{lp}</td>'
            f'<td class="c-num">{fb198}</td>'
            f'<td class="c-num">{fb201}</td>'
            f'<td class="c-num">{fb202}</td>'
            f'</tr>\n'
        )

    def _material_rows_for_entry(entry, is_latest=False, prog_prefix=""):
        """Emit one aggregate row (ALL materials combined) then one indented
        sub-row per material type (only when >1 material type is present)."""
        rows_html = _data_row(entry, is_latest=is_latest,
                              prog_prefix=prog_prefix, material="ALL")
        mat_summaries = _extract_per_material_summaries(entry["tp_dir"])
        for mat_id, mat_sm in mat_summaries:
            mat_entry = dict(entry)
            mat_entry["summary"] = mat_sm
            rows_html += _data_row(
                mat_entry, is_latest=False,
                prog_prefix=f'<td class="c-prog c-prog-sub"></td>' if prog_prefix else "",
                material=mat_id, sub=True,
            )
        return rows_html


    # ── Summary panel ─────────────────────────────────────────────────────────
    sum_rows = ""
    for letter in sorted_letters:
        if not history[letter]:
            continue
        e    = history[letter][0]
        link = _idx_uri(e)
        prog_cell = (
            f'<td class="c-prog"><a href="{link}" class="tl">'
            f'<span class="prog-pill">0H{letter}</span></a></td>'
            if link else
            f'<td class="c-prog"><span class="prog-pill">0H{letter}</span></td>'
        )
        sum_rows += _material_rows_for_entry(e, is_latest=True, prog_prefix=prog_cell)

    summary_panel = (
        f'<div id="panel-summary" class="panel active">\n'
        f'  <h2 class="panel-hdr">&#128200; Summary \u2014 Latest Run per Program'
        f'    <button class="csv-btn" onclick="downloadCSV(this)" title="Download visible rows as CSV">&#11123; CSV</button>'
        f'  </h2>\n'
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
    for letter in sorted_letters:
        entries = history[letter]
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
            _material_rows_for_entry(e, i == 0, prog_prefix=_prog_cell(e, letter))
            for i, e in enumerate(entries)
        )
        latest_ff = _sum_bins(entries[0]["summary"], [1, 2])
        try:
            lyf = float(latest_ff.rstrip('%'))
            badge_col = "#66bb6a" if lyf >= 60 else "#ffa726" if lyf >= 40 else "#ef5350"
        except Exception:
            badge_col = "#90a4ae"
        prog_panels += (
            f'<div id="panel-{letter}" class="panel">\n'
            f'  <h2 class="panel-hdr">\n'
            f'    <span class="prog-pill">0H{letter}</span>\n'
            f'    <span class="yld-badge" style="background:{badge_col}">{latest_ff} FF</span>\n'
            f'    <span class="panel-sub-inline">{len(entries)} run{"s" if len(entries)!=1 else ""}</span>\n'
            f'    <button class="csv-btn" onclick="downloadCSV(this)" title="Download visible rows as CSV">&#11123; CSV</button>\n'
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
    sb = '<li><button class="tab-btn active" data-panel="summary">&#128200;&nbsp;Summary</button></li>\n'
    for letter in sorted_letters:
        if not history[letter]:
            continue
        ff = _sum_bins(history[letter][0]["summary"], [1, 2])
        n  = len(history[letter])
        sb += (
            f'<li><button class="tab-btn" data-panel="{letter}">'
            f'<span class="nav-prog">0H{letter}</span>'
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
#sidebar li { margin: 0; }
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
.tab-btn.active .nav-meta { color: #607d8b; }
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
.data-tbl { border-collapse: collapse; width: 100%; font-size: 14px; min-width: 900px; }
.data-tbl th {
  background: #1a3a55; color: #4fc3f7;
  padding: 8px 12px; text-align: left;
  border-bottom: 2px solid #0f1923; font-size: 13px; line-height: 1.4; white-space: nowrap;
}
.data-tbl th small { color: #607d8b; font-weight: normal; display: block; font-size: 12px; }
.data-tbl td { padding: 6px 12px; border-bottom: 1px solid #1a2f45; vertical-align: middle; text-align: left; }
.data-tbl tr:hover td { background: #14253a; }
.latest-row td { background: #0f2233 !important; }
.c-date { white-space: nowrap; color: #90a4ae; }
.c-prog { }
.c-prog-sub { }
.c-mat  { white-space: nowrap; color: #ce93d8; font-size: 13px; }
.mat-sub-row td { background: #111e2a !important; font-size: 13px; color: #a5d6e8; }
.mat-sub-row .c-num { color: #80cbc4; }
.mat-sub-row .c-tgt { color: #7986cb; }
.mat-sub-row .c-date { color: #607d8b; }
.mat-sub-row .c-op   { color: #4db6ac; }
.mat-sub-row .c-mat { padding-left: 6px; }
.mat-sub-lbl { color: #80deea; font-size: 13px; font-style: italic; font-weight: 500; }
.c-op   { white-space: nowrap; color: #80cbc4; }
.c-num  { white-space: nowrap; }
.c-tgt  { color: #78909c; font-size: 13px; }
.mono   { font-family: monospace; font-size: 13px; }
.tl     { color: #4fc3f7; text-decoration: none; }
.tl:hover { text-decoration: underline; }
.sort-arrow { font-size: 11px; color: #4fc3f7; margin-left: 3px; }
/* ── Column filter dropdown ──────────────────────────────────────────────── */
.flt-btn {
  display: inline-block; margin-left: 5px; cursor: pointer;
  font-size: 10px; color: #607d8b; vertical-align: middle;
  padding: 0 3px; border-radius: 3px; line-height: 1;
  transition: color .15s;
}
.flt-btn:hover { color: #4fc3f7; }
.flt-btn.flt-active { color: #ffa726; }
.flt-drop {
  position: absolute; z-index: 9999;
  background: #1a2f45; border: 1px solid #263950;
  border-radius: 6px; padding: 8px 0 6px;
  box-shadow: 0 4px 18px rgba(0,0,0,.6);
  min-width: 220px; max-width: 320px;
}
.flt-search-row { padding: 0 10px 6px; }
.flt-text {
  width: 100%; box-sizing: border-box;
  background: #0f1923; border: 1px solid #263950;
  color: #dce9f5; border-radius: 4px; padding: 5px 8px;
  font-size: 13px; outline: none;
}
.flt-text:focus { border-color: #4fc3f7; }
.flt-cb-list {
  max-height: 180px; overflow-y: auto; padding: 0 10px;
  border-top: 1px solid #263950; border-bottom: 1px solid #263950;
  margin-bottom: 4px;
}
.flt-cb-lbl {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 2px; font-size: 13px; color: #c8d8e8;
  cursor: pointer; white-space: nowrap;
}
.flt-cb-lbl:hover { color: #fff; }
.flt-cb-lbl input[type=checkbox] { accent-color: #4fc3f7; cursor: pointer; }
.flt-footer { display: flex; gap: 6px; padding: 4px 10px 0; }
.flt-footer button {
  flex: 1; background: #263950; border: none; color: #90a4ae;
  border-radius: 4px; padding: 4px 0; font-size: 12px; cursor: pointer;
}
.flt-footer button:hover { background: #2e4a6a; color: #dce9f5; }
.flt-footer .flt-apply { background: #1b5e20; color: #a5d6a7; }
.flt-footer .flt-apply:hover { background: #2e7d32; }
.flt-footer .flt-clear { color: #ef9a9a; }
/* ── CSV download button ─────────────────────────────────────────────────── */
.csv-btn {
  margin-left: auto; background: #1a3a2c; border: 1px solid #2e6b4a;
  color: #80deea; border-radius: 5px; padding: 4px 12px;
  font-size: 13px; cursor: pointer; white-space: nowrap;
  transition: background .15s, color .15s;
}
.csv-btn:hover { background: #235c40; color: #b2ebf2; }
"""

    JS = """
(function(){
  /* ── Tab navigation ───────────────────────────────────────────────────── */
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

  /* ── Column sort + filter for .data-tbl ──────────────────────────────── */
  var _sortState = {};   /* key: tableId+colIdx -> {asc:bool} */
  var _filterState = {}; /* key: tableId+colIdx -> {text:'', checked:Set} */
  var _activeDropdown = null;

  function tableId(tbl){ return tbl.id || (tbl.id='tbl'+(Math.random()*1e9|0)); }

  /* Build a unique key per table+column */
  function fkey(tbl,ci){ return tableId(tbl)+':'+ci; }

  /* Collect unique values for a column (ignores hidden rows) */
  function colValues(tbl,ci){
    var vals=new Set();
    Array.from(tbl.querySelectorAll('tbody tr')).forEach(function(r){
      var c=r.cells[ci]; if(c) vals.add(c.innerText.trim());
    });
    return Array.from(vals).sort();
  }

  /* Apply all active filters to a table */
  function applyFilters(tbl){
    var tid=tableId(tbl);
    Array.from(tbl.querySelectorAll('tbody tr')).forEach(function(row){
      var show=true;
      Object.keys(_filterState).forEach(function(k){
        if(k.split(':')[0]!==tid) return;
        var ci=parseInt(k.split(':')[1]);
        var st=_filterState[k];
        var cell=row.cells[ci];
        var val=cell ? cell.innerText.trim() : '';
        if(st.text && val.toLowerCase().indexOf(st.text.toLowerCase())===-1){ show=false; }
        if(st.checked && st.checked.size>0 && !st.checked.has(val)){ show=false; }
      });
      row.style.display = show ? '' : 'none';
    });
  }

  /* Close any open dropdown */
  function closeDropdown(){
    if(_activeDropdown){ _activeDropdown.remove(); _activeDropdown=null; }
  }
  document.addEventListener('click', function(e){
    if(_activeDropdown && !_activeDropdown.contains(e.target) && !e.target.classList.contains('flt-btn')){
      closeDropdown();
    }
  });

  /* Build and show the filter dropdown for a th */
  function showDropdown(th, tbl, ci){
    closeDropdown();
    var k=fkey(tbl,ci);
    if(!_filterState[k]) _filterState[k]={text:'',checked:new Set()};
    var st=_filterState[k];

    var dd=document.createElement('div');
    dd.className='flt-drop';
    dd.innerHTML=
      '<div class="flt-search-row">'+
        '<input class="flt-text" type="text" placeholder="Search..." value="'+st.text+'">'+
      '</div>'+
      '<div class="flt-cb-list"></div>'+
      '<div class="flt-footer">'+
        '<button class="flt-all">All</button>'+
        '<button class="flt-none">None</button>'+
        '<button class="flt-apply">Apply</button>'+
        '<button class="flt-clear">Clear</button>'+
      '</div>';

    /* Position below th */
    var rect=th.getBoundingClientRect();
    dd.style.top=(rect.bottom+window.scrollY)+'px';
    dd.style.left=(rect.left+window.scrollX)+'px';
    document.body.appendChild(dd);
    _activeDropdown=dd;

    /* Populate checkboxes */
    var cbList=dd.querySelector('.flt-cb-list');
    var vals=colValues(tbl,ci);
    vals.forEach(function(v){
      var lbl=document.createElement('label');
      lbl.className='flt-cb-lbl';
      var chk=document.createElement('input');
      chk.type='checkbox';
      chk.value=v;
      chk.checked = st.checked.size===0 || st.checked.has(v);
      lbl.appendChild(chk);
      lbl.appendChild(document.createTextNode(' '+v));
      cbList.appendChild(lbl);
    });

    /* Text filter live preview */
    dd.querySelector('.flt-text').addEventListener('input',function(){
      var q=this.value.toLowerCase();
      cbList.querySelectorAll('label').forEach(function(l){
        l.style.display=l.textContent.toLowerCase().indexOf(q)>=0?'':'none';
      });
    });

    dd.querySelector('.flt-all').addEventListener('click',function(e){
      e.stopPropagation();
      cbList.querySelectorAll('input').forEach(function(c){c.checked=true;});
    });
    dd.querySelector('.flt-none').addEventListener('click',function(e){
      e.stopPropagation();
      cbList.querySelectorAll('input').forEach(function(c){c.checked=false;});
    });
    dd.querySelector('.flt-apply').addEventListener('click',function(e){
      e.stopPropagation();
      st.text=dd.querySelector('.flt-text').value.trim();
      st.checked=new Set();
      var all=cbList.querySelectorAll('input');
      var anyUnchecked=false;
      all.forEach(function(c){ if(!c.checked) anyUnchecked=true; });
      if(anyUnchecked) all.forEach(function(c){ if(c.checked) st.checked.add(c.value); });
      applyFilters(tbl);
      /* Update filter-active indicator */
      var active=(st.text||st.checked.size>0);
      th.querySelector('.flt-btn').classList.toggle('flt-active',active);
      closeDropdown();
    });
    dd.querySelector('.flt-clear').addEventListener('click',function(e){
      e.stopPropagation();
      st.text=''; st.checked=new Set();
      applyFilters(tbl);
      th.querySelector('.flt-btn').classList.remove('flt-active');
      closeDropdown();
    });
  }

  /* Attach sort + filter controls to each filterable column header */
  function initTable(tbl){
    /* cols 0=Program, 1=Material (0-indexed) */
    var filterCols=[0,1];
    var ths=Array.from(tbl.querySelectorAll('thead th'));
    ths.forEach(function(th,ci){
      /* Sort on th text click */
      th.style.cursor='pointer';
      th.title='Click to sort';
      th.addEventListener('click',function(e){
        if(e.target.classList.contains('flt-btn')) return;
        var tid=tableId(tbl);
        var k=tid+':sort:'+ci;
        _sortState[k]=!_sortState[k];
        var asc=!_sortState[k];
        var rows=Array.from(tbl.querySelectorAll('tbody tr'));
        rows.sort(function(a,b){
          var av=(a.cells[ci]||{}).innerText||'';
          var bv=(b.cells[ci]||{}).innerText||'';
          return asc ? av.localeCompare(bv) : bv.localeCompare(av);
        });
        var tbody=tbl.querySelector('tbody');
        rows.forEach(function(r){ tbody.appendChild(r); });
        /* Update sort arrows */
        ths.forEach(function(h){
          var arrow=h.querySelector('.sort-arrow');
          if(arrow) arrow.textContent='';
        });
        var arrow=th.querySelector('.sort-arrow');
        if(arrow) arrow.textContent=asc?' ↑':' ↓';
      });

      /* Sort-arrow span */
      var arrowSpan=document.createElement('span');
      arrowSpan.className='sort-arrow';
      th.appendChild(arrowSpan);

      /* Filter button for Program and Material only */
      if(filterCols.indexOf(ci)>=0){
        var btn=document.createElement('span');
        btn.className='flt-btn';
        btn.title='Filter';
        btn.innerHTML='&#9660;';
        btn.addEventListener('click',function(e){
          e.stopPropagation();
          showDropdown(th,tbl,ci);
        });
        th.appendChild(btn);
      }
    });
  }

  /* Init all current tables */
  document.querySelectorAll('.data-tbl').forEach(initTable);

  /* Re-init when tab switches (panels reuse same DOM so only once needed) */

  /* ── CSV download ─────────────────────────────────────────────────────── */
  window.downloadCSV = function(btn){
    var panel = btn.closest('.panel');
    var tbl   = panel && panel.querySelector('.data-tbl');
    if(!tbl) return;

    /* Headers — strip HTML, sort-arrow spans and filter buttons */
    var headers = Array.from(tbl.querySelectorAll('thead th')).map(function(th){
      return th.cloneNode(true).innerText.replace(/[\u2191\u2193\u25bc\u25be]/g,'').trim();
    });

    /* Visible rows only */
    var rows = Array.from(tbl.querySelectorAll('tbody tr')).filter(function(r){
      return r.style.display !== 'none';
    });

    var LF = String.fromCharCode(10), CR = String.fromCharCode(13);
    function escCSV(v){
      v = v.split(LF).join(' ').split(CR).join('').trim();
      if(v.indexOf(',')>=0 || v.indexOf('"')>=0)
        return '"'+v.split('"').join('""')+'"';
      return v;
    }

    var NL = CR+LF;
    var lines = [headers.map(escCSV).join(',')];
    rows.forEach(function(r){
      lines.push(Array.from(r.cells).map(function(c){
        return escCSV(c.innerText.trim());
      }).join(','));
    });

    /* Build panel name for filename */
    var panelId = panel.id || 'report';
    var ts = new Date().toISOString().replace(/[T:]/g,'-').slice(0,19);
    var filename = 'NVL_Yield_'+panelId+'_'+ts+'.csv';

    var blob = new Blob([lines.join(NL)], {type:'text/csv;charset=utf-8;'});
    var url  = URL.createObjectURL(blob);
    var a    = document.createElement('a');
    a.href     = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(function(){ document.body.removeChild(a); URL.revokeObjectURL(url); }, 500);
  };
})();
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>NVL816-BLLC Yield Report \u2014 {run_ts}</title>
<style>{CSS}</style>
</head>
<body>
<nav id="sidebar">
  <div id="sb-hdr">
    <h3>NVL816 Yield</h3>
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



def _collapse_report_html(html: str) -> str:
    """Return a copy of report HTML suitable for Outlook email body.
    - Converts <details>/<summary> to plain <div> elements (Outlook strips them)
    - Removes the BinDist section entirely (iframes not supported in Outlook)
    """
    # Strip entire bd-wrap blocks (iframe content can't render in Outlook)
    html = re.sub(r'<div class="bd-wrap">.*?</div>\s*</div>', '', html, flags=re.DOTALL)
    # Convert <details>/<summary> to plain divs — Outlook (Word engine) strips
    # these tags and may discard their entire content, causing visible truncation.
    html = re.sub(r'<details\b[^>]*>', '<div class="prog-group">', html)
    html = re.sub(r'</details>', '</div>', html)
    html = re.sub(r'<summary\b[^>]*>', '<div class="prog-summary">', html)
    html = re.sub(r'</summary>', '</div>', html)
    return html


_SMTP_SERVER  = "smtpauth.intel.com"
_SMTP_PORT    = 587
_SMTP_FROM    = "sujit.n.pant@intel.com"


def _send_via_outlook(to: str, subject: str, body_html: str,
                      attachments: list[str]) -> None:
    """Send via Outlook COM (requires Outlook running in user session)."""
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
        # Outlook sometimes raises a COM error after the item is already
        # dispatched (e.g. "The operation failed").  Treat as sent.
        _log(f"  Outlook COM: Send() raised {_send_err!r} — email likely dispatched.")
    _log("  Email sent via Outlook COM.")


def _send_via_smtp(to: str, subject: str, body_html: str,
                   attachments: list[str]) -> None:
    """Send via Intel SMTP relay — works without Outlook (scheduled tasks)."""
    import smtplib
    import time
    import os
    import socket
    from email.mime.multipart import MIMEMultipart
    from email.mime.text      import MIMEText
    from email.mime.base      import MIMEBase
    from email                import encoders

    msg = MIMEMultipart("mixed")
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
            part.add_header("Content-Disposition", "attachment",
                            filename=p.name)
            msg.attach(part)
            _log(f"  Attaching : {p.name}")

    recipients = [a.strip() for a in to.split(";")]
    msg_str = msg.as_string()

    # Get proxy from environment or use Intel DMZ proxy
    proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or "http://proxy-dmz.intel.com:912"
    
    # Retry with exponential backoff (account for network issues / proxy)
    max_retries = 3
    base_delay = 2  # seconds
    for attempt in range(1, max_retries + 1):
        try:
            _log(f"  SMTP attempt {attempt}/{max_retries} via proxy {proxy}...")
            
            # Try direct connection first
            try:
                with smtplib.SMTP(_SMTP_SERVER, _SMTP_PORT, timeout=60) as s:
                    s.starttls()
                    s.sendmail(_SMTP_FROM, recipients, msg_str)
                _log(f"  Email sent via SMTP ({_SMTP_SERVER}) — direct.")
                return
            except (smtplib.SMTPException, OSError, TimeoutError) as direct_err:
                # If direct fails, try via proxy
                _log(f"  Direct connection failed ({direct_err}), trying via proxy...")
                
                try:
                    import socks
                    # Parse proxy URL
                    if proxy.startswith("http://"):
                        proxy_addr = proxy[7:]
                    else:
                        proxy_addr = proxy
                    proxy_host, proxy_port = proxy_addr.rsplit(":", 1)
                    proxy_port = int(proxy_port)
                    
                    # Create SOCKS5 proxy socket tunnel for HTTP CONNECT
                    sock = socks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.set_proxy(socks.HTTP, proxy_host, proxy_port)
                    sock.connect((_SMTP_SERVER, _SMTP_PORT))
                    
                    with smtplib.SMTP(sock=sock, timeout=60) as s:
                        s.starttls()
                        s.sendmail(_SMTP_FROM, recipients, msg_str)
                    _log(f"  Email sent via SMTP ({_SMTP_SERVER}) — via proxy.")
                    return
                except ImportError:
                    _log("  PySocks not available, retrying direct connection...")
                    raise direct_err
        except (smtplib.SMTPException, OSError, TimeoutError) as e:
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                _log(f"  SMTP attempt {attempt} failed: {e}")
                _log(f"  Retrying in {delay}s...")
                time.sleep(delay)
            else:
                _log(f"  SMTP all {max_retries} attempts failed: {e}")
                raise


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
        for a in (attachments or []):
            _log(f"  Attach    : {a}")
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


# ─────────────────────────────────────────────────────────────────────────────
# No-new-data helper
# ─────────────────────────────────────────────────────────────────────────────

def _send_no_new_data_email(base_dir: Path, args) -> None:
    """Send a brief daily 'no new data' email so the user always hears back."""
    ecfg_path = _EMAIL_CFG
    ecfg: dict = {}
    if ecfg_path.exists():
        try:
            ecfg = json.loads(ecfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    to = (ecfg.get("email_to_report")
          or ecfg.get("email_to")
          or getattr(args, "email", _EMAIL_TO)
          or _EMAIL_TO)

    # Find the most recent report from a previous run
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
<h2 style="color:#6c3483">NVL Yield Automation — No New Data</h2>
<p>Run at <strong>{run_ts}</strong>: AQUA pull completed but no new lot/wafer data
was detected since the last run. Pipelines were not re-executed.</p>
{last_report_link}
<hr/><p style="font-size:0.85em;color:#888">Pant, Sujit N — GEMS FTE</p>
</body></html>"""

    subject = "NVL816-BLLC Yield Dashboard"
    send_email(to=to, subject=subject, body_html=body,
               dry_run=getattr(args, "dry_run", False))


# ─────────────────────────────────────────────────────────────────────────────
# Output cleanup
# ─────────────────────────────────────────────────────────────────────────────

def cleanup_old_runs(output_dir: Path, keep_runs: int, dry_run: bool = False) -> int:
    """Delete old output run folders, keeping the *keep_runs* most-recent per
    program letter (0H61A, 0H61B, 0H61C, …).

    Rules:
      - Folders are grouped by program letter (NVL_0H61C_* → letter C).
      - Within each letter, folders are sorted newest-first by name (date-encoded).
      - The *keep_runs* most-recent folders are kept.
      - Folders that contain a ``.tag`` file are **always** preserved (not counted
        against keep_runs).
      - Returns the number of folders actually deleted (0 in dry-run mode).
    """
    if keep_runs <= 0 or not output_dir.exists():
        return 0

    pattern = re.compile(r'^NVL_0H61([A-Za-z])_', re.IGNORECASE)
    letter_groups: dict[str, list[Path]] = {}
    for d in output_dir.iterdir():
        if not d.is_dir():
            continue
        m = pattern.match(d.name)
        if m:
            letter = m.group(1).upper()
            letter_groups.setdefault(letter, []).append(d)

    deleted = 0
    for letter in sorted(letter_groups):
        # Newest run first (folder names are NVL_0H61X_YYYYMMDD_HHMMSS)
        folders = sorted(letter_groups[letter], key=lambda d: d.name, reverse=True)
        kept = 0
        for d in folders:
            is_tagged = (d / ".tag").exists()
            if is_tagged:
                continue          # tagged → always preserved, don't count
            if kept < keep_runs:
                kept += 1
                continue          # within retention quota → keep
            # Beyond quota → schedule for deletion
            if dry_run:
                _log(f"  CLEANUP DRY-RUN: would delete {d.name}")
            else:
                try:
                    shutil.rmtree(d)
                    _log(f"  Cleanup: deleted old run {d.name}")
                    deleted += 1
                except Exception as e:
                    _log(f"  WARNING: cleanup could not delete {d.name}: {e}")

    return deleted


def _preview_cleanup(output_dir: Path, keep_runs: int) -> list[Path]:
    """Return the list of run folders that *would* be deleted by cleanup_old_runs.
    Tagged folders are excluded from the result (they are never deleted).
    """
    if keep_runs <= 0 or not output_dir.exists():
        return []

    pattern = re.compile(r'^NVL_0H61([A-Za-z])_', re.IGNORECASE)
    letter_groups: dict[str, list[Path]] = {}
    for d in output_dir.iterdir():
        if not d.is_dir():
            continue
        m = pattern.match(d.name)
        if m:
            letter = m.group(1).upper()
            letter_groups.setdefault(letter, []).append(d)

    to_delete: list[Path] = []
    for letter in sorted(letter_groups):
        folders = sorted(letter_groups[letter], key=lambda d: d.name, reverse=True)
        kept = 0
        for d in folders:
            if (d / ".tag").exists():
                continue
            if kept < keep_runs:
                kept += 1
                continue
            to_delete.append(d)

    return to_delete


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Auto-pull AQUA + split by program/op + run yield dashboards + email.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--aqua-exe",      default=_AQUA_EXE_AMR,
                    help="Path to AquaCmdLine.exe")
    ap.add_argument("--report-config", default=str(_AQUA_CFG),
                    help="AQUA .txt config (program + days baked in)")
    ap.add_argument("--base-dir",      default=str(_BASE_DIR),
                    help="Root output directory (samba share)")
    ap.add_argument("--days",          type=int, default=_DEFAULT_DAYS,
                    help="Look-back days")
    ap.add_argument("--local-csv",     default=None,
                    help="Skip AQUA pull; use this existing CSV/gz/zip (glob ok)")
    ap.add_argument("--keys",          default=None,
                    help="Comma-separated substrings to filter TP keys (e.g. '0H61C,119325'). Only matching keys run.")
    ap.add_argument("--force",         action="store_true",
                    help="Rerun all ops even if no new data")
    ap.add_argument("--dry-run",       action="store_true",
                    help="Show plan without executing")
    ap.add_argument("--email",         default=_EMAIL_TO)
    ap.add_argument("--keep-runs",     type=int, default=None, metavar="N",
                    help="Keep the N most-recent output run folders per program letter "
                         "after this run; older folders are deleted automatically. "
                         "0 = disabled. Reads from email_config.json (keep_runs) "
                         "when not set; default in config is 5.")
    ap.add_argument("--serve",         action="store_true",
                    help="Start local resend server (localhost:17450) and block. "
                         "Enables the 'Resend Email' button in BinDistribution.html.")
    ap.add_argument("--port",          type=int, default=17450,
                    help="Port for --serve mode (default: 17450)")
    args = ap.parse_args()

    # ── --serve mode: just start the server and block ─────────────────────────
    if args.serve:
        base_dir = Path(args.base_dir)
        _run_resend_server(base_dir, port=args.port, email_to=args.email)
        return

    base_dir = Path(args.base_dir)
    data_dir = base_dir / "data"
    run_log  = base_dir / "run_log.html"
    ts       = datetime.now().strftime('%Y%m%d_%H%M%S')

    _log("=" * 65)
    _log(f"run_automation  [{'DRY-RUN' if args.dry_run else 'LIVE'}]")
    _log(f"Base dir     : {base_dir}")
    _log(f"Pipeline     : {_PIPELINE}")
    _log("=" * 65)

    # ── 1. Get AQUA data ──────────────────────────────────────────────────────
    _local_7z_tmpdir = None   # kept alive so extracted CSV path stays valid
    if args.local_csv:
        import glob as _glob
        _matches = sorted(_glob.glob(args.local_csv), key=os.path.getmtime)
        if _matches:
            aqua_file = Path(_matches[-1])
            _log(f"Local CSV: {aqua_file}  ({len(_matches)} match(es) for {args.local_csv!r})")
        elif '*' in args.local_csv or '?' in args.local_csv:
            _log(f"ERROR: no files matched glob: {args.local_csv!r}")
            sys.exit(1)
        else:
            aqua_file = Path(args.local_csv)
            _log(f"Local CSV: {aqua_file}")
        # If the local file is a .7z archive, extract it to a temp dir first so
        # aqua_file points to the real CSV/gz for timestamp parsing and downstream use.
        if aqua_file.suffix.lower() == '.7z':
            import tempfile as _tempfile2
            import subprocess as _sp_7z
            _local_7z_tmpdir = _tempfile2.TemporaryDirectory(prefix='yield_auto_7z_')
            _7z_out = Path(_local_7z_tmpdir.name)
            _log(f"  Extracting {aqua_file.name} → {_7z_out}")
            try:
                _sp_7z.run([str(_7Z_EXE), 'e', str(aqua_file), f'-o{_7z_out}', '-y'],
                           check=True, capture_output=True)
            except Exception as _e7z:
                _log(f"  ERROR extracting {aqua_file.name}: {_e7z}")
                sys.exit(1)
            _extracted = None
            for _pat7 in ('*.csv.gz', '*.csv'):
                _hits7 = sorted(_7z_out.glob(_pat7), key=lambda p: p.stat().st_size, reverse=True)
                if _hits7:
                    _extracted = _hits7[0]
                    break
            if _extracted is None:
                _log(f"  ERROR: no CSV/gz found inside {aqua_file.name}")
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
            # Read email_config so failure alert goes to the right recipient
            _ecfg = {}
            _ecfg_path = _EMAIL_CFG
            if _ecfg_path.exists():
                try:
                    _ecfg = json.loads(_ecfg_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            _err_to = _ecfg.get("email_to_alert",
                        _ecfg.get("email_to_report",
                          _ecfg.get("email_to", args.email))) or args.email
            send_email(
                to=_err_to,
                subject="NVL816-BLLC Yield Dashboard",
                body_html="<p>AQUA pull failed. Check automation logs.</p>",
                dry_run=args.dry_run,
            )
            sys.exit(1)

    # ── 2. Split by (TestProgram, Operation) and update per-TP gzs ──────────
    _log(f"\nReading: {aqua_file}")
    new_rows, _ = _read_aqua_file(aqua_file)
    _log(f"  {len(new_rows):,} rows")

    groups = split_by_tp_oper(new_rows)
    if not groups and not args.dry_run:
        _log("No groups found — nothing to run.")
        sys.exit(0)

    # ── 2a. Distribute raw AQUA rows into per-program-letter folders ──────────
    # Write a dated per-letter raw snapshot BEFORE any processing so that each
    # program's input is independently recoverable regardless of how many programs
    # (1, 2, 3, 4 …) are present in the AQUA pull.
    #   data/programs/0H61A/raw_YYYYMMDD_HHMMSS.csv.gz
    #   data/programs/0H61B/raw_YYYYMMDD_HHMMSS.csv.gz   …etc.
    _ts_match = re.search(r'(\d{8}_\d{6})', Path(aqua_file).stem)
    _raw_ts   = _ts_match.group(1) if _ts_match else datetime.now().strftime('%Y%m%d_%H%M%S')

    # Collect rows + union-headers per program letter
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
        _raw_z7 = _letter_dir / f"raw_{_raw_ts}.7z"
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

    # Remove the combined raw file from data/ now that per-program slices are in place.
    # Only removes files that live directly in data_dir (auto-pull location).
    if not args.dry_run:
        try:
            _af = Path(aqua_file)
            if _af.exists() and _af.parent.resolve() == data_dir.resolve():
                _af.unlink()
                _log(f"  Removed combined raw file: {_af.name}")
        except Exception as _de:
            _log(f"\nWARNING: could not remove combined raw file: {_de}")

    # ── 3. Build list of TP keys to run ──────────────────────────────────────
    prog_dir = data_dir / "programs"
    # No persistent per-TP gz files; all_stored_keys only used for stale-TP detection (section 4b).
    all_stored_keys: list[str] = []
    # Always run all programs from the current AQUA pull
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

    # ── Optional key filter (--keys) ─────────────────────────────────────────
    if args.keys:
        _kf = [s.strip() for s in args.keys.split(',') if s.strip()]
        keys_to_run = [k for k in keys_to_run if any(f in k for f in _kf)]
        _log(f"  --keys filter '{args.keys}' → {len(keys_to_run)} key(s)")

    _log(f"\nTP programs to run ({len(keys_to_run)}): {keys_to_run or '(none)'}")

    if not keys_to_run:
        _log("Nothing to run — sending no-new-data email and exiting.")
        _send_no_new_data_email(base_dir, args)
        sys.exit(0)

    # ── 4. Per-program-letter run folders ────────────────────────────────────
    #  Group tp_keys by 0H61X letter so each letter gets its own run folder,
    #  its own report.html and its own run-log entry:
    #    base_dir/output/NVL_0H61{letter}_{ts}/    ← one folder per letter
    #      NCXSDJXL0H61{letter}XXXXXX_NNNNNN/      ← one subfolder per TP-op
    #      report.html
    #      input_{tp_key}.json
    #    base_dir/Dashboard_{tp_key}.html           ← top-level latest pointer
    _letter_groups: dict[str, list[str]] = {}
    for _k in sorted(keys_to_run):
        _m = re.search(r'[0-9A-Za-z]H61([A-Za-z])', _k)
        _letter_groups.setdefault(_m.group(1).upper() if _m else 'X', []).append(_k)
    _log(f"\nProgram groups: {list(_letter_groups.keys())} ({len(_letter_groups)} run folder(s))")

    all_results:       list[tuple[str, bool, str]] = []
    all_tp_outputs:    list[tuple] = []
    letter_report_paths: list[Path] = []

    env      = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    prod_cfg = _find_product_config("A")
    run_dir  = base_dir / "output" / f"NVL_0H61_{ts}"   # fallback; overwritten per-letter

    for _letter, _letter_keys in sorted(_letter_groups.items(), reverse=True):
        run_dir = base_dir / "output" / f"NVL_0H61{_letter}_{ts}"
        _log(f"\n{'='*65}")
        _log(f"=== Program 0H61{_letter}  ({len(_letter_keys)} TP(s))  →  {run_dir.name} ===")

        results:    list[tuple[str, bool, str]] = []
        tp_outputs: list[tuple] = []

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
            gz_path = _tp_letter_dir / f"tmp_{tp_key}.csv.gz"
            if tp_key in groups and not args.dry_run:
                _tp_rows, _tp_hdrs = groups[tp_key]
                _write_gz(_tp_rows, _tp_hdrs, gz_path)
            _log(f"\n{'='*55}")
            _log(f"TP: {tp_key}")

            tp_output_dir = run_dir / tp_key
            _misc_dir = base_dir / "output" / "misc"
            _misc_dir.mkdir(parents=True, exist_ok=True)
            dashboard     = str(_misc_dir / f"Dashboard_{tp_key}.html")  # top-level latest pointer

            # Detect program letter for R0 merge logic
            _m_let    = re.search(r'[0-9A-Za-z]H61([A-Za-z])', tp_key)
            _tp_letter = _m_let.group(1).upper() if _m_let else ''
            _r0_gz    = base_dir / "data" / "NVL816-R0-Data.csv.gz"
            _use_r0   = _tp_letter in ('C', 'D') and _r0_gz.exists() and "119325" in tp_key
            _r0_label = f"H61{_tp_letter} + NVL816-R0" if _use_r0 else ""
            if _use_r0:
                _log(f"  R0 merge   : {_r0_gz.name}  (label: {_r0_label})")

            cfg = {
                "DataCSV":            [str(gz_path)],
                "output_folder":      str(run_dir),   # pipeline writes to run_dir/tp_key/
                "dashboard":          dashboard,
                "identifier":         tp_key,         # subfolder name inside run_dir
                "TestProgram_folder": _TP_FOLDER,
                "run_parametric":     True,
                "keep_pcm_idw":       False,
            }
            if prod_cfg:
                cfg["product_config_json"] = prod_cfg

            json_path = run_dir / f"input_{tp_key}.json"
            if not args.dry_run:
                run_dir.mkdir(parents=True, exist_ok=True)
                json_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            _log(f"  Config → {json_path}")
            _log(f"  Output → {tp_output_dir}")

            if args.dry_run:
                _log(f"  DRY-RUN: would run pipeline.py --json {json_path}")
                ok = True
            else:
                if not gz_path.exists():
                    _log(f"  WARNING: gz not found: {gz_path} — skipping")
                    continue
                cmd = [sys.executable, str(_PIPELINE), "--json", str(json_path)]
                _log("  Running pipeline…")
                try:
                    result = subprocess.run(cmd, capture_output=False, text=True,
                                            timeout=3600, env=env, cwd=str(_PIPELINE.parent))
                    ok = result.returncode == 0
                    if not ok:
                        _log(f"  WARNING: pipeline rc={result.returncode}")
                except subprocess.TimeoutExpired:
                    _log("  ERROR: pipeline timed out")
                    ok = False

            # gz mtime → data freshness timestamp shown in report
            try:
                gz_ts = datetime.fromtimestamp(gz_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if gz_path.exists() else ""
            except Exception:
                gz_ts = ""

            # ── R0 second run (61C/61D only) ─────────────────────────────────
            tp_output_dir_r0: Path | None = None
            if _use_r0 and not args.dry_run and ok and gz_path.exists():
                tp_output_dir_r0 = run_dir / (tp_key + "_R0")
                cfg_r0 = {
                    "DataCSV":            [str(gz_path), str(_r0_gz)],
                    "output_folder":      str(run_dir),
                    "dashboard":          str(base_dir / "output" / "misc" / f"Dashboard_{tp_key}_R0.html"),
                    "identifier":         tp_key + "_R0",
                    "TestProgram_folder": _TP_FOLDER,
                    "run_parametric":     True,
                    "keep_pcm_idw":       False,
                }
                if prod_cfg:
                    cfg_r0["product_config_json"] = prod_cfg
                json_r0 = run_dir / f"input_{tp_key}_R0.json"
                json_r0.write_text(json.dumps(cfg_r0, indent=2), encoding="utf-8")
                _log(f"  R0 run → {tp_output_dir_r0}")
                cmd_r0 = [sys.executable, str(_PIPELINE), "--json", str(json_r0)]
                try:
                    res_r0 = subprocess.run(cmd_r0, capture_output=False, text=True,
                                            timeout=3600, env=env, cwd=str(_PIPELINE.parent))
                    if res_r0.returncode != 0:
                        _log(f"  WARNING: R0 pipeline rc={res_r0.returncode}")
                        tp_output_dir_r0 = None
                    else:
                        _watermark_output_dir(str(tp_output_dir_r0))
                except subprocess.TimeoutExpired:
                    _log("  ERROR: R0 pipeline timed out")
                    tp_output_dir_r0 = None

            tp_outputs.append((tp_key, ok, tp_output_dir, gz_ts, _r0_label, tp_output_dir_r0))
            results.append((tp_key, ok, str(tp_output_dir), str(tp_output_dir_r0) if tp_output_dir_r0 else ""))

            if not args.dry_run and ok:
                _watermark_output_dir(str(tp_output_dir))
                _stamp_dashboard_block(Path(dashboard), tp_key, ts[:8])
            # Delete temp gz (raw_<ts>.7z is the archival copy; no persistent per-TP files)
            if not args.dry_run:
                try:
                    if gz_path.exists() and gz_path.name.startswith("tmp_"):
                        gz_path.unlink()
                except Exception:
                    pass

        # ── 4b. Add previous-run data for TPs not updated this cycle ─────────
        if not args.dry_run:
            run_keys_set  = set(_letter_keys)
            out_root      = base_dir / "output"
            prev_run_dirs = sorted(
                (d for d in out_root.iterdir() if d.is_dir() and d != run_dir),
                reverse=True,
            ) if out_root.exists() else []

            # Stale keys for this letter only (from stored gz + previous output dirs)
            hist_keys: set[str] = {
                k for k in all_stored_keys
                if re.search(rf'0H61{_letter}', k, re.IGNORECASE)
            }
            for _prev in prev_run_dirs:
                for _sub in _prev.iterdir():
                    if _sub.is_dir() and not _sub.name.endswith("_R0"):
                        _sub_m = re.search(r'[0-9A-Za-z]H61([A-Za-z])', _sub.name)
                        if (_sub_m.group(1).upper() if _sub_m else 'X') == _letter:
                            hist_keys.add(_sub.name)

            stale_cands = sorted(hist_keys - run_keys_set)
            if stale_cands:
                for stale_key in stale_cands:
                    for prev_run_dir in prev_run_dirs:
                        prev_tp_dir = prev_run_dir / stale_key
                        if prev_tp_dir.is_dir():
                            m = re.search(r'_(\d{8})_', prev_run_dir.name)
                            if m:
                                d = m.group(1)
                                prev_label = f"prev: {d[:4]}-{d[4:6]}-{d[6:]}"
                            else:
                                prev_label = f"prev: {prev_run_dir.name}"
                            tp_outputs.append((stale_key, True, prev_tp_dir, prev_label, "", None))
                            _log(f"  Stale TP in report : {stale_key} ← {prev_run_dir.name}")
                            break

        # ── 4c. Generate per-TP compare_report (trend over daily runs) ───────
        if not args.dry_run and _COMPARE_RUNS.exists():
            _log(f"\nGenerating compare reports for 0H61{_letter}…")
            for tp_key, ok, tp_output_dir, gz_ts, r0_label, tp_output_dir_r0 in tp_outputs:
                compare_out = base_dir / "output" / "compare" / f"compare_report_{tp_key}.html"
                compare_out.parent.mkdir(parents=True, exist_ok=True)
                if gz_ts.startswith("prev:") and compare_out.exists():
                    continue   # stale TP — compare report already generated; skip
                dash_html = base_dir / "output" / "misc" / f"Dashboard_{tp_key}.html"
                # Build a temporary enriched Dashboard for compare_runs that includes ALL
                # historical output dirs (including prior op numbers with same prefix,
                # e.g. 132222 runs when the current key is 132322).  Write to a sidecar
                # file so the real pipeline.py Dashboard.html is never overwritten.
                _cmp_dash = base_dir / "output" / "misc" / f"_cmp_dash_{tp_key}.html"
                rebuilt = _rebuild_dashboard_html_for_tp(tp_key, base_dir, out_path=_cmp_dash)
                if rebuilt and rebuilt.stat().st_size > 0:
                    _n_blocks = rebuilt.read_text(encoding='utf-8').count('class="run-block"')
                    _log(f"  Compare dashboard: {rebuilt.name} ({_n_blocks} run blocks)")
                    dash_html = rebuilt
                elif not dash_html.exists():
                    _log(f"  SKIP {tp_key}: Dashboard.html not found and could not rebuild (no historical runs?)")
                    continue
                cmd_cmp = [sys.executable, str(_COMPARE_RUNS),
                           str(dash_html), "--out", str(compare_out), "--no-open"]
                _log(f"  Compare → {compare_out.name}")
                try:
                    res_cmp = subprocess.run(cmd_cmp, capture_output=True, text=True,
                                             timeout=300, env=env,
                                             cwd=str(_COMPARE_RUNS.parent))
                    if res_cmp.returncode != 0:
                        _log(f"  WARNING: compare_runs rc={res_cmp.returncode}: {res_cmp.stderr[:200]}")
                    else:
                        _log(f"  OK  ({compare_out.stat().st_size:,} bytes)")
                except subprocess.TimeoutExpired:
                    _log(f"  WARNING: compare_runs timed out for {tp_key}")
                except Exception as _ce:
                    _log(f"  WARNING: compare_runs error: {_ce}")

        # ── Per-letter report.html ────────────────────────────────────────────
        letter_report_path: Path | None = None
        if not args.dry_run and results:
            letter_report_path = _build_run_report(
                run_dir,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                str(aqua_file),
                tp_outputs,
            )
            if letter_report_path:
                letter_report_paths.append(letter_report_path)

        # ── Per-letter run log entry ──────────────────────────────────────────
        _log(f"\nUpdating run log for 0H61{_letter}…")
        update_run_log(
            results=results,
            aqua_file=str(aqua_file),
            run_log=run_log,
            dry_run=args.dry_run,
            report_path=letter_report_path,
        )

        all_results.extend(results)
        all_tp_outputs.extend(tp_outputs)

    # ── Flatten results from all letter groups for email ─────────────────────
    results    = all_results
    tp_outputs = all_tp_outputs
    report_path = letter_report_paths[-1] if letter_report_paths else None

    # ── 5. Load email_config.json (set by manage_email.py GUI) ───────────────
    email_cfg_path = _EMAIL_CFG
    email_cfg: dict = {}
    if email_cfg_path.exists():
        try:
            email_cfg = json.loads(email_cfg_path.read_text(encoding="utf-8"))
            # migrate old single-field format
            if "email_to" in email_cfg and "email_to_report" not in email_cfg:
                email_cfg["email_to_report"] = email_cfg.pop("email_to")
            _log(f"\nEmail config: {email_cfg_path}")
        except Exception as _e:
            _log(f"\nWARNING: could not read email_config.json: {_e}")

    excluded_keys: set[str] = set(email_cfg.get("excluded_keys", []))
    excluded_ops:  set[str] = set(str(o) for o in email_cfg.get("excluded_ops", []))
    email_to_report = email_cfg.get("email_to_report", args.email) or args.email
    email_to_alert  = email_cfg.get("email_to_alert",  email_to_report)
    if excluded_keys or excluded_ops:
        _log(f"  Excluded from report: keys={sorted(excluded_keys)} ops={sorted(excluded_ops)}")

    def _is_excluded(tp_key: str) -> bool:
        if tp_key in excluded_keys:
            return True
        for op in excluded_ops:
            if tp_key.endswith(f"_{op}") or f"_{op}_" in tp_key:
                return True
        return False

    # Filter tp_outputs to only keys allowed in the email report
    tp_outputs_email = [t for t in tp_outputs if not _is_excluded(t[0])]

    # ── 5b. Cleanup old run folders (before sending email) ───────────────────
    _keep_runs = args.keep_runs
    if _keep_runs is None:
        _keep_runs = int(email_cfg.get("keep_runs", 5))
    if _keep_runs > 0:
        _log(f"\nCleaning up old runs (keep last {_keep_runs} per letter) …")
        _n_deleted = cleanup_old_runs(
            base_dir / "output", _keep_runs, dry_run=args.dry_run
        )
        if not args.dry_run:
            _log(f"  Deleted {_n_deleted} old run folder(s).")

    # ── 6. Send email ─────────────────────────────────────────────────────────
    run_ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    all_ok  = all(r[1] for r in results)
    subject = "NVL816-BLLC Yield Dashboard"

    tmp_att_dir = Path(tempfile.mkdtemp(prefix="nvl_att_"))
    email_attachments: list[str] = []
    try:
        # Build ONE combined report from all allowed tp_outputs.
        # This single file is used as BOTH the email body and the attachment
        # so they are guaranteed to be identical.
        _ts_label   = datetime.now().strftime("%Y%m%d_%H%M%S")
        _att_name   = f"NVL816-BLLC Yield Report {_ts_label}.html"
        _comb_path  = tmp_att_dir / _att_name

        _email_body_rpt: str | None = None
        _email_body_collapsed: str | None = None
        try:
            _excl_keys = list(email_cfg.get("excluded_keys", []))
            _email_body_rpt = _build_email_report_html(
                base_dir / "output", run_ts,
                excluded_keys=_excl_keys,
            )
            _comb_path.write_text(_email_body_rpt, encoding="utf-8")
            email_attachments.append(str(_comb_path))
            # Also save a persistent copy to reports/
            _reports_dir = base_dir / "reports"
            _reports_dir.mkdir(parents=True, exist_ok=True)
            _report_save = _reports_dir / f"Yield_Report_{_ts_label}.html"
            _report_save.write_text(_email_body_rpt, encoding="utf-8")
            _log(f"Report saved: {_report_save}")
            # Regenerate reports/index.html immediately after saving the report
            try:
                _HERE_AUTO = Path(__file__).resolve().parent
                sys.path.insert(0, str(_HERE_AUTO))
                from generate_index import build_index as _build_index  # noqa
                _idx = _build_index(base_dir)
                _log(f"Index updated → {_idx}")
            except Exception as _idx_err:
                _log(f"WARNING: could not update index.html: {_idx_err}")
            # Collapse <details>/<summary> and strip iframes for Outlook compatibility
            _email_body_collapsed = _collapse_report_html(_email_body_rpt)
        except Exception as _be:
            _log(f"  WARNING: combined email report build failed: {_be}")

        if not email_attachments and letter_report_paths:
            # Fallback: attach the per-letter reports if combined build failed
            for _lp in letter_report_paths:
                if _lp and _lp.exists():
                    email_attachments.append(str(_lp))

        _fallback_body = _email_body_html(
            run_ts, str(aqua_file),
            [r for r in results if not _is_excluded(r[0])]
            + [
                (tp_key, True, str(tp_dir), "", gz_ts)
                for tp_key, ok, tp_dir, gz_ts, _r0lbl, _r0dir in tp_outputs_email
                if gz_ts.startswith("prev:") and tp_key not in {r[0] for r in results}
            ],
            run_log,
            attachments=email_attachments,
        )

        send_email(
            to=email_to_report,
            subject="NVL816-BLLC Yield Report",
            body_html=_email_body_collapsed or _email_body_rpt or _fallback_body,
            dry_run=args.dry_run,
            attachments=email_attachments,
        )
    finally:
        shutil.rmtree(tmp_att_dir, ignore_errors=True)

    _log("\n" + "=" * 65)
    for _lp in letter_report_paths:
        _log(f"Run folder : {_lp.parent}")
    for r in results:
        _log(f"  {'OK' if r[1] else 'FAILED'}  op={r[0]}  → {r[2]}")
    _log(f"Run log    : {run_log}")
    _log("=" * 65)

    # Per-TP gz files are kept in data/programs/0H61{letter}/ for selective deletion.

    # Compress the AQUA pull snapshot to .7z for better long-term storage
    if not args.dry_run and not args.local_csv and isinstance(aqua_file, Path):
        if aqua_file.suffix == ".gz" and aqua_file.stem.endswith(".csv"):
            _log(f"\nCompressing {aqua_file.name} → .7z …")
            z7 = _compress_aqua_to_7z(aqua_file)
            if z7:
                _log(f"  {aqua_file.name} → {z7.name}  ({z7.stat().st_size / 1024:.0f} KB)")
            else:
                _log(f"  WARNING: 7z compression failed; keeping {aqua_file.name}")

    if not all_ok:
        sys.exit(1)

    # ── Hint: start resend server if not dry-run ──────────────────────────
    if not args.dry_run:
        _log(f"\nTip: run  python run_automation.py --serve  to enable the")
        _log(f"     'Resend Email' button in BinDistribution.html.")


# ─────────────────────────────────────────────────────────────────────────────
# Resend server  (python run_automation.py --serve [--port N] [--base-dir D])
# ─────────────────────────────────────────────────────────────────────────────

def _run_resend_server(base_dir: Path, port: int = 17450, email_to: str = "") -> None:
    """Start a local HTTP server on localhost:port that handles POST /resend.
    Blocks until Ctrl+C.  Looks at base_dir/output/ to find the latest run.
    """
    import http.server
    import threading

    def _find_latest_tp_dirs(base_dir: Path) -> list[Path]:
        """Return tp_output dirs from the most-recent run for each program letter."""
        out_root = base_dir / "output"
        if not out_root.exists():
            return []
        run_dirs = sorted(
            (d for d in out_root.iterdir()
             if d.is_dir() and re.match(r'NVL_0H61[A-Z]_', d.name)),
            key=lambda d: d.name, reverse=True,
        )
        # One latest run per letter
        letter_run: dict[str, Path] = {}
        for rd in run_dirs:
            m = re.search(r'NVL_0H61([A-Z])_', rd.name)
            if m and m.group(1) not in letter_run:
                letter_run[m.group(1)] = rd
        tp_dirs: list[Path] = []
        for letter in sorted(letter_run):
            rd = letter_run[letter]
            for sub in sorted(rd.iterdir()):
                if sub.is_dir() and not sub.name.endswith("_R0"):
                    tp_dirs.append(sub)
        return tp_dirs

    def _handle_resend(body: dict) -> dict:
        lw_raw = body.get("lots_wafers", "all")
        row_filter: "set | None" = None
        if lw_raw != "all" and isinstance(lw_raw, list):
            row_filter = set(str(x) for x in lw_raw)
        tp_dirs = _find_latest_tp_dirs(base_dir)
        if not tp_dirs:
            return {"status": "error", "message": "No run output found in " + str(base_dir)}

        # Re-read summaries with filter
        summaries: list[tuple[str, dict]] = []
        for tp_dir in tp_dirs:
            smry = _extract_yield_summary(tp_dir, row_filter=row_filter)
            if smry:
                summaries.append((tp_dir.name, smry))

        if not summaries:
            return {"status": "error", "message": "No yield data found (filter too narrow?)"}

        # Build a simple email table
        def _parse_pct(s: str) -> float:
            try:
                return float(str(s).replace("%", "").strip())
            except Exception:
                return 0.0

        filter_desc = ""
        if row_filter:
            lots  = sorted(set(lw.split("|")[0] for lw in row_filter if "|" in lw))
            wafs  = sorted(set(lw.split("|")[1] for lw in row_filter if "|" in lw))
            filter_desc = f"Lots: {', '.join(lots)} &nbsp;|&nbsp; Wafers: {', '.join(wafs)}"
        else:
            filter_desc = "All wafers"

        rows_html = ""
        for tp_key, smry in sorted(summaries, reverse=True):
            bins = smry.get("bins", {})
            b1 = _parse_pct(bins.get("Bin 1", "0"))
            b2 = _parse_pct(bins.get("Bin 2", "0"))
            b3 = _parse_pct(bins.get("Bin 3", "0"))
            b4 = _parse_pct(bins.get("Bin 4", "0"))
            ff   = b1 + b2
            ffdf = b1 + b2 + b3 + b4
            rb   = smry.get("repair_bins", {})
            dlcp = smry.get("dlcp", {})
            rv_hp = f"{dlcp['hp']} ({dlcp['hp_n']:,})" if dlcp else "–"
            rv_lp = f"{dlcp['lp']} ({dlcp['lp_n']:,})" if dlcp else "–"
            rv198 = rb.get("198", "–")
            rv201 = rb.get("201") or rb.get("2") or "–"
            rv202 = rb.get("202", "–")
            rows_html += (
                f"<tr><td style='font-family:monospace;color:#0071c5'>{tp_key}</td>"
                f"<td style='text-align:center'>{smry.get('die','–')}</td>"
                f"<td style='text-align:center'>{ff:.1f}%</td>"
                f"<td style='text-align:center'>{ffdf:.1f}%</td>"
                f"<td style='text-align:center;color:#1565c0'>{rv_hp}</td>"
                f"<td style='text-align:center;color:#e65100'>{rv_lp}</td>"
                f"<td style='text-align:center'>{rv198}</td>"
                f"<td style='text-align:center'>{rv201}</td>"
                f"<td style='text-align:center'>{rv202}</td>"
                f"</tr>\n"
            )

        run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body_html = f"""<!DOCTYPE html><html><body style="font-family:Segoe UI,Arial;color:#222;max-width:860px">
<h2 style="color:#0071c5;margin-bottom:4px">Yield Dashboard — Filtered Resend</h2>
<p style="color:#555;font-size:0.9em;margin-top:0">{run_ts}</p>
<p style="background:#fff8e1;padding:8px 12px;border-left:4px solid #f9a825;font-size:0.9em">
  <b>Filter applied:</b>&nbsp;{filter_desc}</p>
<table border="1" cellpadding="6" cellspacing="0"
       style="border-collapse:collapse;width:100%;font-size:0.9em">
  <tr style="background:#0071c5;color:#fff">
    <th>TP Key</th><th>Die</th><th>FF (1+2)</th><th>FF+DF</th>
    <th style="color:#90caf9">DLCP HP</th><th style="color:#ffcc80">DLCP LP</th>
    <th>FB198</th><th>FB201</th><th>FB202</th>
  </tr>
  {rows_html}
</table>
<hr/><p style="font-size:0.8em;color:#888">Sent via resend server — base dir: {base_dir}</p>
</body></html>"""

        ecfg: dict = {}
        if _EMAIL_CFG.exists():
            try:
                ecfg = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
            except Exception:
                pass
        to = email_to or ecfg.get("email_to_report") or ecfg.get("email_to") or _EMAIL_TO
        subject = "NVL816-BLLC Yield Dashboard (filtered resend)"

        class _FakeArgs:
            dry_run = False

        try:
            send_email(to=to, subject=subject, body_html=body_html, dry_run=False)
            n_tp = len(summaries)
            return {"status": "ok",
                    "message": f"Email sent to {to}  ({n_tp} TP(s), {filter_desc})"}
        except Exception as exc:
            return {"status": "error", "message": f"Email failed: {exc}"}

    # ── HTTP server ──────────────────────────────────────────────────────────
    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # suppress default access log

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self):
            self.send_response(200)
            self._cors()
            self.end_headers()

        def do_GET(self):
            if self.path == "/status":
                resp = json.dumps({"status": "ok", "base_dir": str(base_dir)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.send_header("Content-Length", len(resp))
                self.end_headers()
                self.wfile.write(resp)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/resend":
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length))
                    result = _handle_resend(body)
                except Exception as exc:
                    result = {"status": "error", "message": str(exc)}
                resp = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.send_header("Content-Length", len(resp))
                self.end_headers()
                self.wfile.write(resp)
            else:
                self.send_response(404)
                self.end_headers()

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    print(f"\nResend server listening on http://localhost:{port}/resend")
    print(f"Base dir : {base_dir}")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

