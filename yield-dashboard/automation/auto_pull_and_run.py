"""
auto_pull_and_run.py
====================
Full automation pipeline:
  1. Pull AQUA data for NCXSDJXL0H61* lots (last 7 days) → C:\\work\\auto\\data\\
  2. Compare against previous snapshot (lot / wafer / program / date)
  3. If data changed → run yield dashboard (headless pipeline.py --html-only)
  4. Send email to sujit.n.pant@intel.com on completion

Usage:
  python auto_pull_and_run.py                   # full run
  python auto_pull_and_run.py --dry-run         # show what would run, no exec
  python auto_pull_and_run.py --force           # skip change-detection, always rerun
  python auto_pull_and_run.py --days 14         # look back 14 days instead of 7

Output layout:
  C:\\work\\auto\\
    data\\
      NCXSDJXL0H61_YYYYMMDDTHHMMSS.csv.gz     (raw AQUA pull)
      latest.csv.gz -> (symlink or copy of most recent pull)
    dashboard\\
      Dashboard.html
      index.html  (per-run htmls appended here)
    snapshot.json                              (change-detection cache)
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# Ensure Unicode log output works on Windows cp1252 consoles and when
# stdout is redirected to a file (e.g. Tee-Object).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE      = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent.parent.parent   # app.yield.nvl/

_PIPELINE_PY  = _REPO_ROOT / "code" / "dashboard" / "yield-dashboard" / "yld" / "src" / "pipeline.py"
_AQUA_EXE_GAR = r"\\PGSAPP3301.gar.corp.intel.com\Installer\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"
_AQUA_EXE_AMR = r"\\FMSAPP3301.amr.corp.intel.com\Installer\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"

# ── Defaults ───────────────────────────────────────────────────────────────────
_DEFAULT_SERVER      = "AMR"
_DEFAULT_DATA_DIR    = r"C:\work\auto\data"
_DEFAULT_DASH_DIR    = r"C:\work\auto\dashboard"
_DEFAULT_EMAIL       = "sujit.n.pant@intel.com"
_SNAPSHOT_FILE       = Path(r"C:\work\auto\snapshot.json")
_DEFAULT_REPORT      = str(_REPO_ROOT / "shared" / "setup" / "automation" / "yield-dashboard" / "NVL_Sort_Yield - AutoPull.txt")

# Change-detection: priority-ordered candidate column names (auto-selected from header)
_CHANGE_COL_CANDIDATES = [
    ("Lot",     ["SORT_LOT", "Lot", "Sort_Lot", "LOTFROMFS"]),
    ("Wafer",   ["SORT_WAFER", "Wafer", "Sort_Wafer_ID", "Wafer_ID", "WaferID"]),
    ("Program", ["Program Name", "Program_Name", "ProgramName"]),
    ("Date",    ["LOTS End Date Time", "End_Date_Time", "End_Date", "Start_Date_Time"]),
]

# TestProgram folder used by yield_pipeline to locate BinDefinitions.bdefs
_DEFAULT_TP_FOLDER = r"I:\program\1001\prod\hdmtprogs\nvl_ncx_sds"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_snapshot() -> dict:
    if _SNAPSHOT_FILE.exists():
        try:
            return json.loads(_SNAPSHOT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_snapshot(snap: dict) -> None:
    _SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SNAPSHOT_FILE.write_text(json.dumps(snap, indent=2), encoding="utf-8")


# ── Step 1: Pull AQUA ─────────────────────────────────────────────────────────

def _get_aqua_report_name(config_path: str) -> str:
    """Read the '@ Report : <name>' line from an AQUA config file."""
    try:
        for line in Path(config_path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("@ Report :"):
                return stripped.split(":", 1)[1].strip()
    except Exception:
        pass
    return "NVL_Sort_Yield"  # fallback


def pull_aqua(
    aqua_exe: str,
    aqua_server: str,
    report_config: str,
    data_dir: Path,
    dry_run: bool,
) -> Path | None:
    """
    Pull via -ReportConfig — all filter settings (program, days) live in the .txt file.
    AQUA honours the OutputFileName stem but may append a different extension
    (e.g. .csv.gz instead of .zip).  We glob for any file sharing the stem.
    Falls back to scanning %TEMP% for new CSVs if nothing found in data_dir.
    Returns path to the output file, or None on failure / no data.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    ts       = _ts()
    out_base = data_dir / f"NCXSDJXL0H61_{ts}"

    # AQUA ignores the extension we request and always writes .csv.gz;
    # request a .zip so we can detect the actual file by globbing out_base.*
    zip_file = out_base.with_suffix(".zip")

    # AQUA also writes to %TEMP% using internal report name — scan for fallback
    temp_dir    = Path(os.environ.get("TEMP", tempfile.gettempdir()))
    report_name = _get_aqua_report_name(report_config)
    temp_pat    = f"{report_name}*.CSV"

    cmd = [
        aqua_exe,
        "-AquaServer",    aqua_server,
        "-ReportConfig",  report_config,
        "-OutputFileName", str(zip_file),
    ]

    _log(f"{'DRY-RUN: ' if dry_run else ''}AQUA pull -> {out_base}.*")
    _log(f"  Config      : {report_config}")
    _log(f"  Report name : {report_name}")
    _log(f"  CMD         : {' '.join(cmd)}")

    if dry_run:
        return out_base.with_suffix(".csv.gz")

    # Snapshot %TEMP% before run (for fallback detection)
    before_temp = {p.resolve() for p in temp_dir.glob(temp_pat)}

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.stdout.strip():
            _log(f"AQUA stdout: {result.stdout.strip()[:500]}")
        if result.stderr.strip():
            _log(f"AQUA stderr: {result.stderr.strip()[:500]}")
        if result.returncode != 0:
            _log(f"ERROR: AQUA exited rc={result.returncode}")
            return None
    except subprocess.TimeoutExpired:
        _log("ERROR: AQUA timed out")
        return None
    except FileNotFoundError:
        _log(f"ERROR: AquaCmdLine.exe not found: {aqua_exe}")
        return None
    except Exception as e:
        _log(f"ERROR: {e}")
        return None

    # Primary: AQUA honours our OutputFileName stem but may change the extension
    # (e.g. we ask for .zip, it writes .csv.gz).  Glob for any file sharing the stem.
    written = [p for p in data_dir.glob(f"{out_base.name}*") if p.stat().st_size > 0]
    if written:
        out = max(written, key=lambda p: p.stat().st_mtime)
        _log(f"  Output: {out.name} ({out.stat().st_size:,} bytes)")
        return out

    # Fallback: check %TEMP% for new CSVs written during the run
    after_temp = {p.resolve() for p in temp_dir.glob(temp_pat)}
    new_csvs   = sorted(after_temp - before_temp, key=lambda p: p.stat().st_mtime)
    if new_csvs:
        plain = [p for p in new_csvs if p.suffix.lower() == ".csv"]
        src   = max(plain or new_csvs, key=lambda p: p.stat().st_mtime)
        dest  = data_dir / f"NCXSDJXL0H61_{ts}.csv"
        shutil.copy2(src, dest)
        _log(f"  Fallback: copied from %TEMP%: {src.name} -> {dest.name} ({dest.stat().st_size:,} bytes)")
        return dest

    _log(f"ERROR: AQUA produced no output (rc={result.returncode}; nothing matching '{out_base.name}*' in {data_dir})")
    return None



# ── Step 2: Change detection ──────────────────────────────────────────────────

def _read_aqua_file(path: Path) -> list[dict]:
    """
    Read an AQUA output file (.csv.gz, .csv, or .zip) and return list of row dicts.
    AQUA typically writes gzip-compressed CSV; the inner file is tab-delimited text.
    """
    import zipfile
    try:
        raw = path.read_bytes()
        # ZIP (magic PK)
        if raw[:2] == b'PK':
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                inner = z.read(z.namelist()[0]).decode("utf-8-sig", errors="replace")
        # gzip
        elif raw[:2] == b'\x1f\x8b':
            inner = gzip.decompress(raw).decode("utf-8-sig", errors="replace")
        else:
            inner = raw.decode("utf-8-sig", errors="replace")

        # auto-detect delimiter
        first_line = inner.split("\n")[0]
        delim = "\t" if "\t" in first_line else ","
        return list(csv.DictReader(io.StringIO(inner), delimiter=delim))
    except Exception as e:
        _log(f"WARNING: could not read {path}: {e}")
        return []


def _fingerprint(rows: list[dict], cols: list[str]) -> str:
    """SHA256 of the sorted unique values of the key columns."""
    values = set()
    for row in rows:
        values.add(tuple(row.get(c, "").strip() for c in cols))
    digest = hashlib.sha256(
        "\n".join(sorted(str(v) for v in values)).encode()
    ).hexdigest()
    return digest


def has_changed(aqua_file: Path, snapshot: dict) -> bool:
    """
    Return True if the data in aqua_file differs from the last snapshot.
    Auto-detects lot/wafer/program/date columns from the file header.
    """
    rows = _read_aqua_file(aqua_file)
    if not rows:
        _log("WARNING: no rows in pull — treating as no change")
        return False

    headers = set(rows[0].keys())
    # Pick the first candidate that exists in the header for each role
    cols = []
    for _role, candidates in _CHANGE_COL_CANDIDATES:
        for c in candidates:
            if c in headers:
                cols.append(c)
                break

    if not cols:
        _log(f"WARNING: no key columns found in header {sorted(headers)[:8]}")
        _log("Treating as changed to be safe.")
        return True

    _log(f"Change-detection columns: {cols}")
    fp = _fingerprint(rows, cols)
    prev_fp = snapshot.get("fingerprint")
    _log(f"Fingerprint: {fp[:16]}...  previous: {(prev_fp or 'none')[:16]}...")

    if fp != prev_fp:
        prev_lots = set(snapshot.get("lots", []))
        lot_col   = next((c for c in cols if "lot" in c.lower()), None)
        curr_lots = {r.get(lot_col, "").strip() for r in rows if lot_col and r.get(lot_col)}
        new_lots  = curr_lots - prev_lots
        if new_lots:
            _log(f"New lots detected: {sorted(new_lots)}")
        return True

    _log("No change detected — skipping dashboard rebuild.")
    return False


# ── Step 3: Run yield dashboard ───────────────────────────────────────────────

_LOADER = _PIPELINE_PY.parent / "_loader.py"

def _detect_test_program(aqua_csv: Path) -> str:
    """
    Return the most common TestProgram name from the AQUA CSV
    (reads 'Program Name_119325' or similar columns).
    """
    from collections import Counter
    rows = _read_aqua_file(aqua_csv)
    for col in ("Program Name_119325", "Program Name_132322", "Program Name"):
        vals = [r.get(col, "").strip() for r in rows if r.get(col, "").strip()]
        if vals:
            return Counter(vals).most_common(1)[0][0]
    return "NCXSDJXL0H61A"  # fallback

def run_dashboard(
    aqua_csv: Path,
    dash_dir: Path,
    dry_run: bool,
) -> bool:
    """
    Run the full yield pipeline headlessly against the extracted tab-delimited CSV.
    Pipes a minimal JSON config to yield_pipeline via stdin.
    Returns True on success.
    """
    dash_dir.mkdir(parents=True, exist_ok=True)
    dashboard_xlsx = dash_dir / "DigitalDashBoard.xlsx"

    test_program = _detect_test_program(aqua_csv) if not dry_run else "NCXSDJXL0H61A002618"
    tag      = datetime.now().strftime("%Y-%m-%d %H:%M")
    cfg_json = json.dumps({
        "outputFilename":   str(aqua_csv),
        "TestProgram":      test_program,
        "TestProgram_folder": _DEFAULT_TP_FOLDER,
        "output_folder":    str(dash_dir),
        "dashboard":        str(dashboard_xlsx),
        "identifier":       tag,
        "skip_aqua":        True,
    })

    cmd = [
        sys.executable,
        str(_LOADER),
        "yield_pipeline",
        "--input", "-",
        "--base",  str(aqua_csv.parent),
    ]

    _log(f"{'DRY-RUN: ' if dry_run else ''}Running yield pipeline")
    _log(f"  CSV         : {aqua_csv}")
    _log(f"  TestProgram : {test_program}")
    _log(f"  Out         : {dash_dir}")
    _log(f"  CMD         : {' '.join(cmd)}")

    if dry_run:
        return True

    try:
        result = subprocess.run(
            cmd,
            input=cfg_json,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if result.stdout.strip():
            _log(result.stdout.strip())
        if result.returncode != 0:
            _log(f"ERROR: yield_pipeline exited rc={result.returncode}\n{result.stderr.strip()}")
            return False
        _log(f"Pipeline output in: {dash_dir}")
        return True
    except subprocess.TimeoutExpired:
        _log("ERROR: yield_pipeline timed out")
        return False


# ── Step 4: Send email ────────────────────────────────────────────────────────

def _load_dashboard_html(dash_dir: Path) -> str | None:
    """Return the main Dashboard HTML from dash_dir, or None."""
    for name in ("Dashboard.html", "dashboard.html"):
        p = dash_dir / name
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
    htmls = [p for p in dash_dir.glob("*.html") if p.is_file()]
    if htmls:
        return max(htmls, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8", errors="replace")
    return None


def _send_via_outlook(to: str, subject: str, html_body: str) -> None:
    """Send HTML email via Outlook COM. Falls back to system python if win32com unavailable."""
    import tempfile
    try:
        import win32com.client
        ol   = win32com.client.Dispatch("Outlook.Application")
        mail = ol.CreateItem(0)
        mail.To       = to
        mail.Subject  = subject
        mail.HTMLBody = html_body
        mail.Send()
        return
    except ImportError:
        pass  # not in this env, try system python

    # Fallback: write HTML to a temp file so we avoid quoting issues in -c script
    candidates = [
        r"C:\Users\snpant\AppData\Local\Python\pythoncore-3.14-64\python.exe",
        r"C:\Python313\python.exe",
        r"C:\Python312\python.exe",
    ]
    tmp_html = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", encoding="utf-8", delete=False) as f:
            f.write(html_body)
            tmp_html = f.name
        script = (
            "import win32com.client; "
            "ol=win32com.client.Dispatch('Outlook.Application'); "
            "m=ol.CreateItem(0); "
            f"m.To={to!r}; m.Subject={subject!r}; "
            f"m.HTMLBody=open({tmp_html!r},encoding='utf-8').read(); m.Send()"
        )
        for py in candidates:
            if os.path.exists(py):
                result = subprocess.run([py, "-c", script], capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    return
    finally:
        if tmp_html and os.path.exists(tmp_html):
            os.unlink(tmp_html)
    raise RuntimeError("win32com not available in any known python installation")


def send_email(
    to: str,
    smtp_host: str,   # unused, kept for signature compat
    subject: str,
    html_body: str,
    dry_run: bool,
) -> None:
    _log(f"{'DRY-RUN: ' if dry_run else ''}Sending email to {to}")
    if dry_run:
        _log(f"  Subject: {subject}")
        return
    try:
        _send_via_outlook(to, subject, html_body)
        _log("Email sent.")
    except Exception as e:
        _log(f"WARNING: email failed: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Auto AQUA pull + yield dashboard rerun on data change.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--aqua-server",   default=_DEFAULT_SERVER, choices=["GAR", "AMR"])
    ap.add_argument("--aqua-exe",      default=None,
                    help="Path to AquaCmdLine.exe (auto-selected from --aqua-server)")
    ap.add_argument("--report-config", default=_DEFAULT_REPORT,
                    help="Path to AQUA .txt config file (program/days baked in)")
    ap.add_argument("--data-dir",      default=_DEFAULT_DATA_DIR,
                    help="Folder for downloaded AQUA ZIP files")
    ap.add_argument("--dashboard-dir", default=_DEFAULT_DASH_DIR,
                    help="Folder where pipeline output (xlsx, PNG) is written")
    ap.add_argument("--email",         default=_DEFAULT_EMAIL)
    ap.add_argument("--force",         action="store_true",
                    help="Skip change detection, always rerun dashboard")
    ap.add_argument("--dry-run",       action="store_true",
                    help="Show what would run without executing anything")
    args = ap.parse_args()

    aqua_exe  = args.aqua_exe or (_AQUA_EXE_GAR if args.aqua_server == "GAR" else _AQUA_EXE_AMR)
    data_dir  = Path(args.data_dir)
    dash_dir  = Path(args.dashboard_dir)
    snapshot  = _load_snapshot()

    _log("=" * 60)
    _log(f"auto_pull_and_run  [{'DRY-RUN' if args.dry_run else 'LIVE'}]")
    _log(f"Report config  : {args.report_config}")
    _log(f"Data dir       : {data_dir}")
    _log(f"Dashboard dir  : {dash_dir}")
    _log("=" * 60)

    status_lines: list[str] = []

    # ── 1. Pull ────────────────────────────────────────────────────────────────
    aqua_csv = pull_aqua(
        aqua_exe=aqua_exe,
        aqua_server=args.aqua_server,
        report_config=args.report_config,
        data_dir=data_dir,
        dry_run=args.dry_run,
    )
    if aqua_csv is None:
        _log("Pull failed — aborting.")
        send_email(
            to=args.email, smtp_host=None,
            subject="NVL816-BLLC Yield Dashboard — FAILED",
            html_body=(
                "<html><body style='font-family:Arial,sans-serif;padding:16px'>"
                "<p><b style='color:#c0392b'>AQUA pull failed.</b> Check server logs.</p>"
                f"<p style='color:#888;font-size:12px'>Run time: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>"
                "</body></html>"
            ),
            dry_run=args.dry_run,
        )
        sys.exit(1)

    status_lines.append(f"Pull  : {aqua_csv.name}")

    # ── 2. Change detection ────────────────────────────────────────────────────
    if args.force or args.dry_run:
        changed = True
        _log("Change detection skipped (--force or --dry-run).")
    else:
        changed = has_changed(aqua_csv, snapshot)

    if not changed:
        status_lines.append("Change: NONE — dashboard not rebuilt")
        send_email(
            to=args.email, smtp_host=None,
            subject="NVL816-BLLC Yield Dashboard — No new data",
            html_body=(
                "<html><body style='font-family:Arial,sans-serif;padding:16px'>"
                "<p>No new AQUA data detected &mdash; dashboard not rebuilt.</p>"
                f"<p style='color:#888;font-size:12px'>Pull: {aqua_csv.name}</p>"
                f"<p style='color:#888;font-size:12px'>Run time: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>"
                "</body></html>"
            ),
            dry_run=args.dry_run,
        )
        return

    status_lines.append("Change: YES — rebuilding dashboard")

    # ── 3. Run dashboard ─────────────────────────────────────────────────────────────────
    ok = run_dashboard(
        aqua_csv=aqua_csv,
        dash_dir=dash_dir,
        dry_run=args.dry_run,
    )
    status_lines.append(f"Dashboard: {'OK' if ok else 'FAILED'} → {dash_dir}")

    # ── 4. Update snapshot ─────────────────────────────────────────────────────
    if ok and not args.dry_run:
        rows    = _read_aqua_file(aqua_csv)
        headers = set(rows[0].keys()) if rows else set()
        cols    = []
        for _role, candidates in _CHANGE_COL_CANDIDATES:
            for c in candidates:
                if c in headers:
                    cols.append(c)
                    break
        fp       = _fingerprint(rows, cols) if rows and cols else ""
        lot_col  = next((c for c in cols if "lot" in c.lower()), None)
        lots     = sorted({r.get(lot_col, "").strip() for r in rows if lot_col and r.get(lot_col)})
        _save_snapshot({
            "fingerprint":  fp,
            "lots":         lots,
            "last_pull":    datetime.now(timezone.utc).isoformat(),
            "last_csv":     str(aqua_csv),
        })
        _log(f"Snapshot updated ({len(lots)} lots)")

    # ── 5. Email ───────────────────────────────────────────────────────────────
    subject = "NVL816-BLLC Yield Dashboard" if ok else "NVL816-BLLC Yield Dashboard — FAILED"
    _dash_html = _load_dashboard_html(dash_dir) if ok and not args.dry_run else None
    if _dash_html:
        html_body = _dash_html
    else:
        _status_html = "".join(f"<li>{ln}</li>" for ln in status_lines)
        _color = "#27ae60" if ok else "#c0392b"
        html_body = (
            "<html><body style='font-family:Arial,sans-serif;padding:16px'>"
            f"<p><b style='color:{_color}'>{'Dashboard updated.' if ok else 'Dashboard run FAILED.'}</b></p>"
            f"<ul style='font-size:13px'>{_status_html}</ul>"
            f"<p style='color:#888;font-size:12px'>Run time: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>"
            f"<p style='color:#888;font-size:12px'>Output folder: {dash_dir}</p>"
            "</body></html>"
        )

    send_email(
        to=args.email, smtp_host=None,
        subject=subject, html_body=html_body,
        dry_run=args.dry_run,
    )

    if not ok:
        sys.exit(1)
    _log("All done.")


if __name__ == "__main__":
    main()
