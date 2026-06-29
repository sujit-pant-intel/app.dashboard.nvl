"""
aqua_autopull.py
================
Auto-pull AQUA data whenever new lots appear for a given devrevstep.

Workflow:
  1. Run AQUA SUMMARY query (-devrevsteps + -lastNDaysLoadEnd) to discover recent lots
  2. Compare against seen_lots.json cache
  3. For each new lot: invoke AquaCmdLine.exe with -lots override on the full report config
  4. Save CSV.gz output to --output-dir, update cache

Usage:
  python aqua_autopull.py                                         # defaults from config
  python aqua_autopull.py --devrevstep 8PF5CV --operation 119325
  python aqua_autopull.py --lot Q603S6T01 Q603S6T02              # pull specific lots now
  python aqua_autopull.py --dry-run                               # show what would run, no exec
  python aqua_autopull.py --days 60                               # look back 60 days

Defaults (edit CONFIG section below or pass as args):
  --aqua-server   GAR
  --report-config <repo>/shared/setup/aqua/NVL_Sort_Yield - Dashboard.txt
  --output-dir    C:\\\\work\\\\aqua_output
  --operation     119325
  --devrevstep    8PF5CV,8PF6CV
  --days          30
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent.parent.parent   # app.yield.nvl/

# ── CONFIG (override via CLI args) ─────────────────────────────────────────────
_AQUA_EXE_GAR   = r"\\PGSAPP3301.gar.corp.intel.com\Installer\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"
_AQUA_EXE_AMR   = r"\\amr.corp.intel.com\ec\proj\fm\MPD\AQUA\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"
_AQUA_EXE_GER   = r"\\HASAPP3301.ger.corp.intel.com\Installer\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"
_DEFAULT_SERVER  = "AMR"
_DEFAULT_REPORT  = str(_REPO_ROOT / "shared" / "setup" / "aqua" / "NVL_Sort_Yield - Dashboard.txt")
_DEFAULT_OUT_DIR = r"C:\work\aqua_output"
_CACHE_FILE      = _HERE / "seen_lots.json"
_DEFAULT_OP      = "119325"
_DEFAULT_DRS     = "8PF5CV,8PF6CV"
_DEFAULT_DAYS    = 30

_EXE_MAP = {"GAR": _AQUA_EXE_GAR, "AMR": _AQUA_EXE_AMR, "GER": _AQUA_EXE_GER}


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}   # {lot: {"pulled_at": ISO, "output": path}}


def _save_cache(cache: dict) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


# ── AQUA: discover lots via SUMMARY query ─────────────────────────────────────

def _discover_lots_aqua(
    aqua_exe: str,
    aqua_server: str,
    devrevsteps: list[str],
    operation: str,
    days: int,
    dry_run: bool = False,
) -> set[str]:
    """
    Run a quick AQUA FilterSet SUMMARY query to list lots for the given
    devrevsteps in the last N days.  Parses the output CSV for the 'Lot' column.
    Returns a set of lot strings.
    """
    drs_arg = ",".join(devrevsteps)
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, prefix="aqua_lot_list_") as tf:
        tmp_csv = tf.name

    cmd = [
        aqua_exe,
        "-AquaServer",       aqua_server,
        "-AnalysisType",     "SUMMARY",
        "-devrevsteps",      drs_arg,
        "-operations",       operation,
        "-lastNDaysLoadEnd", str(days),
        "-OutputFileName",   tmp_csv,
    ]

    print(f"[discover] {'DRY-RUN: ' if dry_run else ''}AQUA SUMMARY for devrevsteps={drs_arg}, op={operation}, last {days} days")
    print(f"           CMD: {' '.join(cmd)}")

    if dry_run:
        print("[discover] DRY-RUN: skipping AQUA call, returning empty set")
        try:
            Path(tmp_csv).unlink(missing_ok=True)
        except Exception:
            pass
        return set()

    lots: set[str] = set()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"[discover] AQUA SUMMARY error (rc={result.returncode}):\n{result.stderr.strip()}")
            return lots

        # Parse CSV — look for a 'Lot' column
        tmp_path = Path(tmp_csv)
        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            print("[discover] AQUA returned empty output")
            return lots

        with open(tmp_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            lot_col = next((h for h in headers if h.strip().lower() == "lot"), None)
            if not lot_col:
                print(f"[discover] No 'Lot' column found in SUMMARY output. Headers: {headers}")
                return lots
            for row in reader:
                lot = row[lot_col].strip()
                if lot:
                    lots.add(lot.upper())

        print(f"[discover] Found {len(lots)} lot(s): {sorted(lots)}")
    except subprocess.TimeoutExpired:
        print("[discover] TIMEOUT during SUMMARY query")
    except FileNotFoundError:
        print(f"[discover] ERROR: AquaCmdLine.exe not found at: {aqua_exe}")
    finally:
        try:
            Path(tmp_csv).unlink(missing_ok=True)
        except Exception:
            pass

    return lots


# ── AQUA: pull one lot ─────────────────────────────────────────────────────────

def _pull_lot(
    lot: str,
    aqua_exe: str,
    aqua_server: str,
    report_config: str,
    output_dir: str,
    dry_run: bool = False,
) -> bool:
    """
    Invoke AquaCmdLine.exe for a single lot.
    Returns True on success.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_file = str(out_dir / f"{lot}_{ts}.csv.gz")

    cmd = [
        aqua_exe,
        "-AquaServer",    aqua_server,
        "-ReportConfig",  report_config,
        "-lots",          lot,
        "-OutputFileName", out_file,
    ]

    print(f"[pull] {'DRY-RUN: ' if dry_run else ''}Pulling lot {lot} → {out_file}")
    print(f"       CMD: {' '.join(cmd)}")

    if dry_run:
        return True

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            print(f"[pull] ERROR (rc={result.returncode}):\n{result.stderr.strip()}")
            return False
        print(f"[pull] Done: {out_file}")
        if result.stdout.strip():
            print(f"       {result.stdout.strip()}")
        return True
    except subprocess.TimeoutExpired:
        print(f"[pull] TIMEOUT for lot {lot}")
        return False
    except FileNotFoundError:
        print(f"[pull] ERROR: AquaCmdLine.exe not found at: {aqua_exe}")
        return False


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Auto-pull AQUA data for new lots detected via TRACE/XEUS.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--devrevstep",   default=_DEFAULT_DRS,
                    help="Comma-separated devrevstep prefixes to watch (e.g. 8PF5CV,8PF6CV)")
    ap.add_argument("--operation",    default=_DEFAULT_OP,
                    help="SORT operation number")
    ap.add_argument("--days",         default=_DEFAULT_DAYS, type=int,
                    help="Look back N days in AQUA SUMMARY discovery")
    ap.add_argument("--lot",          nargs="+", default=None,
                    help="Pull specific lots now (skip AQUA discovery)")
    ap.add_argument("--aqua-server",  default=_DEFAULT_SERVER, choices=["GAR","AMR","GER"],
                    help="AQUA server domain")
    ap.add_argument("--aqua-exe",     default=None,
                    help="Path to AquaCmdLine.exe (auto-selected from --aqua-server if omitted)")
    ap.add_argument("--report-config",default=_DEFAULT_REPORT,
                    help="Path to exported AQUA report config .txt")
    ap.add_argument("--output-dir",   default=_DEFAULT_OUT_DIR,
                    help="Local folder for downloaded CSVs")
    ap.add_argument("--force",        action="store_true",
                    help="Re-pull lots even if already in cache")
    ap.add_argument("--dry-run",      action="store_true",
                    help="Show what would run without executing AQUA")
    ap.add_argument("--clear-cache",  action="store_true",
                    help="Clear the seen_lots cache and exit")
    args = ap.parse_args()

    # Handle clear-cache
    if args.clear_cache:
        if _CACHE_FILE.exists():
            _CACHE_FILE.unlink()
            print(f"[cache] Cleared: {_CACHE_FILE}")
        else:
            print(f"[cache] Nothing to clear ({_CACHE_FILE} does not exist)")
        return

    # Resolve exe
    aqua_exe = args.aqua_exe or _EXE_MAP[args.aqua_server]

    # Validate report config
    if not Path(args.report_config).exists():
        print(f"[config] ERROR: report config not found: {args.report_config}")
        sys.exit(1)

    # Determine lots to pull
    if args.lot:
        new_lots = set(l.upper() for l in args.lot)
        print(f"[main] Manual lot list: {sorted(new_lots)}")
    else:
        devrevsteps = [d.strip() for d in args.devrevstep.split(",") if d.strip()]
        discovered  = _discover_lots_aqua(
            aqua_exe=aqua_exe,
            aqua_server=args.aqua_server,
            devrevsteps=devrevsteps,
            operation=args.operation,
            days=args.days,
            dry_run=args.dry_run,
        )
        print(f"[main] AQUA discovered {len(discovered)} lot(s): {sorted(discovered)}")

        cache = _load_cache()
        if args.force:
            new_lots = discovered
        else:
            new_lots = discovered - set(cache.keys())

        print(f"[main] New lots (not yet pulled): {sorted(new_lots)}")

    if not new_lots:
        print("[main] Nothing to pull.")
        return

    # Pull each lot
    cache = _load_cache()
    for lot in sorted(new_lots):
        success = _pull_lot(
            lot=lot,
            aqua_exe=aqua_exe,
            aqua_server=args.aqua_server,
            report_config=args.report_config,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
        )
        if success and not args.dry_run:
            cache[lot] = {
                "pulled_at": datetime.now(timezone.utc).isoformat(),
                "output_dir": args.output_dir,
            }
            _save_cache(cache)

    print("[main] Done.")


if __name__ == "__main__":
    main()
