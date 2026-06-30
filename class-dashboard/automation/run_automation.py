"""
run_automation.py — CLASS dashboard automation for NVL816-BLLC.

Workflow
--------
1.  Pull AQUA CLASS data (or use --local-csv).
2.  Split the CSV by TestProgram name; keep only TPs matching configured
    patterns (supports fnmatch wildcards, e.g. NVLSB63A0H54A0*).
3.  For each matched TP:
      a. Write per-TP CSV to  data/programs/{tp_name}.csv.gz
      b. Run class-dashboard --headless →
           output/NVL_Class_{ts}/{tp_name}/{tp_name}_class_analysis.html
      c. Extract die count + top-3 freq summary (Core/Atom/CCF) from data.
4.  Build and send HTML email:
      - Table: TP Name (link) | # dies | Core top-3 freqs | Atom top-3 freqs
5.  (Optional) Clean up old run folders.

Output layout
-------------
  \\\\samba...\\auto\\class\\
    data\\
      programs\\
        NVLSB63A0H54A0ACX22.csv.gz   ← per-TP (latest data)
        ...
      NVL816-BLLC_Class_forReport_{ts}.csv.gz  ← raw AQUA snapshot
    output\\
      NVL_Class_{YYYYMMDD_HHMMSS}\\
        NVLSB63A0H54A0ACX22\\
          NVLSB63A0H54A0ACX22_class_analysis.html
        ...
    run_log.html

Usage
-----
  python run_automation.py                        # full AQUA pull + run
  python run_automation.py --dry-run              # plan only
  python run_automation.py --local-csv "path\\to\\*.csv.gz"
  python run_automation.py --aqua-server GAR
  python run_automation.py --keep-runs 5          # keep last 5 run folders
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
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
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE          = Path(__file__).resolve().parent
_REPO_ROOT     = _HERE.parent.parent          # app.dashboard.nvl/
_DASHBOARD_PY  = _REPO_ROOT / "class-dashboard" / "dashboard.py"
_DASHBOARD_SRC = _REPO_ROOT / "class-dashboard" / "src"
_AQUA_CFG      = (
    _REPO_ROOT / "shared" / "setup" / "automation" / "class-dashboard"
    / "NVL816-BLLC_Class_forReport.txt"
)
_PROD_CFG_DIR  = _REPO_ROOT / "shared" / "setup" / "config" / "class-dashboard"
_EMAIL_CFG     = (
    _REPO_ROOT / "shared" / "setup" / "automation" / "class-dashboard"
    / "class_setup_config.json"
)
_7Z_EXE        = Path(r"C:\Program Files\7-Zip\7z.exe")

# ── Defaults ───────────────────────────────────────────────────────────────────
_BASE_DIR    = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\class")
_EMAIL_TO    = "sujit.n.pant@intel.com"
_DEFAULT_DAYS = 7
_DEFAULT_TP_PATTERNS = [
    "NVLSB63A0H54*S*",
    "NVLS763C0H03*S*",
]

_AQUA_EXE_AMR = r"\\amr.corp.intel.com\ec\proj\fm\MPD\AQUA\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"
_AQUA_EXE_GAR = r"\\PGSAPP3301.gar.corp.intel.com\Installer\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"

_SMTP_SERVER  = "smtpauth.intel.com"
_SMTP_PORT    = 587
_SMTP_FROM    = "sujit.n.pant@intel.com"

# Module display labels
_MOD_LABEL = {
    "CORE": "Core (DCM)",
    "core": "Core (DCM)",
    "ATOM": "Atom",
    "atom": "Atom",
    "CCF":  "CCF/Ring",
    "ccf":  "CCF/Ring",
}

_N_TOP = {"CORE": 4, "ATOM": 3, "CCF": 3}


def _summary_from_json(jd: dict) -> dict:
    """Convert dashboard .summary.json into the dict format used by the email builder."""
    result: dict = {
        "_total_dies": jd.get("total_dies", 0),
        "_bin1_dies":  jd.get("bin1_dies",  0),
    }
    for mod, mdata in (jd.get("pass_table") or {}).items():
        fd_map = mdata.get("freq_data") or {}
        freqs: list[tuple] = []
        for fmhz_str in sorted(fd_map.keys(), key=lambda k: int(k), reverse=True):
            fdat   = fd_map[fmhz_str]
            groups = fdat.get("groups") or {}
            # Pick highest bucket with data: 4 > 2 > 1  (matches dashboard display)
            n = 0
            for bk in ("4", "2", "1"):
                if bk in groups and (groups[bk].get("n") or 0) > 0:
                    n = groups[bk]["n"]
                    break
            freqs.append((fdat["freq_mhz"], fdat["freq_label"], n))
        mod_up  = mod.upper()
        n_top   = _N_TOP.get(mod_up, 3)
        # Mirror extract_freq_summary: top n_top non-zero entries + at most 1 zero
        valid_f = [f for f in freqs if f[2] > 0]
        zero_f  = [f for f in freqs if f[2] == 0]
        freqs   = sorted(valid_f[:n_top] + zero_f[:1], key=lambda x: x[0], reverse=True)
        result[mod_up] = {
            "label": _MOD_LABEL.get(mod_up, mdata.get("label", mod_up)),
            "freqs": freqs,
            "n_top": n_top,
        }
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ─────────────────────────────────────────────────────────────────────────────
# Product config helpers
# ─────────────────────────────────────────────────────────────────────────────

def _find_product_cfg() -> Path | None:
    hits = sorted(_PROD_CFG_DIR.glob("*-CLASS-ProductConfig*.json"))
    return hits[0] if hits else None


def _load_product_cfg(cfg_path: Path) -> dict:
    return json.loads(cfg_path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# AQUA helpers (shared with yield/vmin pattern)
# ─────────────────────────────────────────────────────────────────────────────

def _aqua_report_name(config_path: Path) -> str:
    try:
        for line in config_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if line.strip().startswith("@ Report :"):
                return line.strip().split(":", 1)[1].strip()
    except Exception:
        pass
    return "NVL816-BLLC_Class_forReport"


def _compress_aqua_to_7z(gz_path: Path) -> Path | None:
    """Re-compress a .csv.gz to .7z. Returns new path or None."""
    if not _7Z_EXE.exists():
        return None
    if gz_path.suffix != ".gz" or not gz_path.stem.endswith(".csv"):
        return None
    csv_path = gz_path.with_suffix("")
    z7_path  = gz_path.parent / (gz_path.stem[:-4] + ".7z")
    try:
        with gzip.open(gz_path, "rb") as fi, open(csv_path, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        result = subprocess.run(
            [str(_7Z_EXE), "a", "-mx=5", "-mmt=on", str(z7_path), str(csv_path)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            _log(f"  WARNING: 7z failed: {result.stderr.strip()[:200]}")
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


def pull_aqua(
    aqua_exe: str,
    report_config: Path,
    data_dir: Path,
    dry_run: bool,
) -> Path | None:
    """Run AquaCmdLine.exe with the class config. Returns path to the download."""
    import os as _os; _os.makedirs(str(data_dir), exist_ok=True)
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

    # Primary: file written to data_dir
    written = [p for p in data_dir.glob(f"{out_base.name}*") if p.stat().st_size > 0]
    if written:
        out = max(written, key=lambda p: p.stat().st_mtime)
        _log(f"  Output: {out.name} ({out.stat().st_size:,} bytes)")
        return out

    # Fallback: new CSV in %TEMP%
    after_temp  = {p.resolve() for p in temp_dir.glob(temp_pat)}
    new_csvs    = sorted(after_temp - before_temp, key=lambda p: p.stat().st_mtime)
    if new_csvs:
        src  = max(new_csvs, key=lambda p: p.stat().st_mtime)
        dest = data_dir / f"{report_name}_{ts}.csv"
        shutil.copy2(src, dest)
        _log(f"  Fallback from %TEMP%: {src.name} → {dest.name}")
        return dest

    _log("  ERROR: AQUA produced no output")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# CSV reading (supports .csv, .csv.gz, .zip, .7z)
# ─────────────────────────────────────────────────────────────────────────────

def _read_aqua_file(path: Path) -> tuple[list[dict], str]:
    """Read AQUA output file. Returns (rows, delimiter)."""
    def _inner(raw: bytes) -> str:
        if raw[:6] == b'7z\xbc\xaf\x27\x1c':
            with tempfile.TemporaryDirectory() as _tmp:
                _tmp_p = Path(_tmp)
                subprocess.run(
                    [str(_7Z_EXE), "e", str(path), f"-o{_tmp}", "-y"],
                    check=True, capture_output=True,
                )
                for _pat in ("*.csv", "*.csv.gz", "*.zip"):
                    _hits = sorted(_tmp_p.glob(_pat))
                    if _hits:
                        return _inner(_hits[0].read_bytes())
            raise ValueError(f"No CSV found inside {path.name}")
        elif raw[:2] == b'PK':
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = z.namelist()
                pick  = next((n for n in names if n.lower().endswith('.csv')), names[0])
                return _inner(z.read(pick))
        elif raw[:2] == b'\x1f\x8b':
            return _inner(gzip.decompress(raw))
        else:
            return raw.decode("utf-8-sig", errors="replace")

    inner     = _inner(path.read_bytes())
    first_line = inner.split("\n")[0]
    delim     = "\t" if "\t" in first_line else ","
    rows      = list(csv.DictReader(io.StringIO(inner), delimiter=delim))
    return rows, delim


def _write_gz(rows: list[dict], fieldnames: list[str], path: Path) -> None:
    buf = io.StringIO()
    w   = csv.DictWriter(buf, fieldnames=fieldnames,
                         extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for row in rows:
        w.writerow({k: row.get(k, "") for k in fieldnames})
    path.write_bytes(gzip.compress(buf.getvalue().encode("utf-8"), compresslevel=6))


def _safe_filename(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', s).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Split by TestProgram, filtered by configured patterns
# ─────────────────────────────────────────────────────────────────────────────

def _tp_matches(tp_name: str, patterns: list[str]) -> bool:
    """Return True if tp_name matches any of the configured patterns (fnmatch)."""
    if not patterns:
        return True
    tp_upper = tp_name.upper()
    return any(fnmatch.fnmatchcase(tp_upper, p.upper()) for p in patterns)


def split_by_tp_patterns(
    rows: list[dict],
    patterns: list[str],
) -> dict[str, tuple[list[dict], list[str]]]:
    """Split AQUA rows by TestProgram name, keeping only TPs matching patterns.

    Handles both wide format (Program Name_{op} columns) and tall format
    (Program Name column).

    Returns:
        {tp_name: (rows, fieldnames)}
    """
    if not rows:
        return {}

    headers    = list(rows[0].keys())
    header_set = set(headers)

    # Find program name columns — could be "Program Name", "Program Name_119325", etc.
    prog_cols = [
        h for h in headers
        if re.match(r'^program\s*name', h.lower())
    ]
    if not prog_cols:
        # Try plain "Program" or "ProgramName"
        prog_cols = [
            h for h in headers
            if re.match(r'^program(name)?$', h.lower().replace(" ", ""))
        ]

    if not prog_cols:
        _log("  WARNING: No 'Program Name' column found — cannot split by TP")
        return {}

    _log(f"  TP columns: {prog_cols}")

    # Prefer the CLASS operation column (6248 / CLASSHOT) over Sort columns
    _class_cols = [c for c in prog_cols
                   if re.search(r'6248|classhot', c, re.IGNORECASE)]
    _preferred  = _class_cols if _class_cols else prog_cols
    _log(f"  TP key column: {_preferred[0]!r}")

    groups: dict[str, tuple[list[dict], list[str]]] = {}

    for row in rows:
        # Read TP name strictly from the preferred (CLASS) column only.
        # Rows where that column is empty are Sort-only rows — skip them.
        tp_name = ""
        for col in _preferred:
            val = (row.get(col) or "").strip()
            if val and val.upper() not in ("N/A", "NA", "NONE", "-", ""):
                tp_name = val
                break
        if not tp_name:
            continue

        if not _tp_matches(tp_name, patterns):
            continue

        if tp_name not in groups:
            groups[tp_name] = ([], headers)
        groups[tp_name][0].append(row)

    for tp, (rws, _) in groups.items():
        _log(f"    {tp}: {len(rws):,} rows")

    return groups


def update_tp_gz(
    tp_name: str,
    new_rows: list[dict],
    fieldnames: list[str],
    data_dir: Path,
    dry_run: bool,
) -> Path:
    """Write (overwrite) data_dir/programs/{tp_name}.csv.gz."""
    prog_dir = data_dir / "programs"
    gz_path  = prog_dir / f"{_safe_filename(tp_name)}.csv.gz"

    if not dry_run:
        import os as _os; _os.makedirs(str(prog_dir), exist_ok=True)

    _log(f"  {tp_name}: writing {len(new_rows):,} rows → {gz_path.name}")

    if not dry_run:
        _write_gz(new_rows, fieldnames, gz_path)
        _log(f"    → {gz_path.stat().st_size:,} bytes (gz)")
        # Keep as .csv.gz — dashboard.py / pandas reads gz natively.
        # Do NOT compress per-TP files to .7z (dashboard can't open .7z).
        return gz_path
    else:
        _log(f"    DRY-RUN: would write {gz_path}")
        return gz_path


# ─────────────────────────────────────────────────────────────────────────────
# Step 3a — Run class dashboard (headless) for one TP
# ─────────────────────────────────────────────────────────────────────────────

def run_class_pipeline(
    csv_paths: "Path | list[Path]",
    out_dir: Path,
    tag: str,
    dry_run: bool,
) -> Path | None:
    """Run dashboard.py --headless for one TP.  csv_paths may be a single
    Path or a list of paths (CSV / GZ / ZIP) — all are passed to the
    dashboard which concatenates them automatically.
    Returns the HTML path or None on failure.
    """
    if isinstance(csv_paths, Path):
        csv_paths = [csv_paths]
    safe_tag = re.sub(r"[^\w\-.]", "_", tag) or "output"
    tp_out   = out_dir / safe_tag
    html_out = tp_out / f"{safe_tag}_class_analysis.html"

    _log(f"  Pipeline [{tag}] → {tp_out}")

    if dry_run:
        _log(f"    DRY-RUN: would run dashboard.py --headless {' '.join(str(p) for p in csv_paths)}")
        return html_out

    if not _DASHBOARD_PY.exists():
        _log(f"  ERROR: dashboard.py not found: {_DASHBOARD_PY}")
        return None

    cfg_path = _find_product_cfg()
    if not cfg_path:
        _log(f"  ERROR: No product config found in {_PROD_CFG_DIR}")
        return None

    cmd = [
        sys.executable,
        str(_DASHBOARD_PY),
        *[str(p) for p in csv_paths],
        "--out",      str(out_dir),
        "--tag",      tag,
        "--headless",
        "--cfg",      str(cfg_path),
    ]

    _log(f"    CMD: {' '.join(cmd)}")

    # Force utf-8 stdout so unicode chars (e.g. → in apply_reticle_mapping.py)
    # don't crash the subprocess on Windows cp1252 terminals.
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1800,
            cwd=str(_DASHBOARD_PY.parent), env=env,
            encoding="utf-8",
        )
        for line in (result.stdout + result.stderr).splitlines():
            _log(f"      {line}")
        if result.returncode != 0:
            _log(f"  ERROR: dashboard.py exited with rc={result.returncode}")
            return None
    except subprocess.TimeoutExpired:
        _log("  ERROR: dashboard.py timed out (30 min)")
        return None
    except Exception as e:
        _log(f"  ERROR running dashboard.py: {e}")
        return None

    if html_out.exists():
        _log(f"    → {html_out.name} ({html_out.stat().st_size:,} bytes)")
        return html_out

    # Fallback: any _class_analysis.html in tp_out
    hits = list(tp_out.glob("*_class_analysis.html"))
    if hits:
        return hits[0]

    _log(f"  ERROR: Expected {html_out.name} not found in {tp_out}")
    return None


def _merge_data_files_for_tp(
    merge_data_dir: Path,
    tp_name: str,
) -> list[Path]:
    """Return files from data/merge-data/ whose Program Name column contains tp_name.

    Each file in merge-data/ may be a .csv, .csv.gz, or .zip.  We read the first
    chunk of each file to see which Program Name(s) appear in it, then include
    the file only if tp_name appears.  This way, a single merge-data file covering
    two programs (e.g. BS622 + DS622) is included in both their respective pipelines.

    Returns list of matching Paths (may be empty).
    """
    if not merge_data_dir.exists():
        return []

    candidates: list[Path] = []
    for p in sorted(merge_data_dir.iterdir()):
        if p.suffix.lower() in ('.csv', '.gz', '.zip') or p.name.lower().endswith('.csv.gz'):
            candidates.append(p)

    # Determine which TPs appear in each candidate file (read header + all rows,
    # but only parse the program-name column for speed).
    matched: list[Path] = []
    for path in candidates:
        try:
            file_rows, _ = _read_aqua_file(path)
        except Exception as _re:
            _log(f"  merge-data: could not read {path.name}: {_re}")
            continue
        if not file_rows:
            continue
        headers = list(file_rows[0].keys())
        prog_cols = [h for h in headers if re.match(r'^program\s*name', h.lower())]
        if not prog_cols:
            continue
        # Prefer CLASS op column
        _cls = [c for c in prog_cols if re.search(r'6248|classhot', c, re.IGNORECASE)]
        prog_col = (_cls or prog_cols)[0]
        names_in_file = {
            (r.get(prog_col) or '').strip().upper()
            for r in file_rows
            if (r.get(prog_col) or '').strip()
        }
        if tp_name.upper() in names_in_file:
            _log(f"  merge-data: {path.name} contains {tp_name} — will merge")
            matched.append(path)
        else:
            _log(f"  merge-data: {path.name} skipped (TPs: {sorted(names_in_file)[:6]})")
    return matched


# ─────────────────────────────────────────────────────────────────────────────
# Step 3b — Extract freq summary for email
# ─────────────────────────────────────────────────────────────────────────────

def extract_freq_summary(
    rows: list[dict],
    product_cfg: dict,
) -> dict:
    """Extract die count + top-3 freqs per module from AQUA rows.

    Returns:
        {
          "_total_dies": int,
          "CORE": {"label": "Core (DCM)", "top3": [(fmhz, label, n_valid), ...]},
          "ATOM": {"label": "Atom",       "top3": [...]},
          "CCF":  {"label": "CCF/Ring",   "top3": [...]},
        }
    """
    if not rows:
        return {"_total_dies": 0}

    headers      = list(rows[0].keys())
    vmin_search  = product_cfg.get("vmin_freq_search", {})

    result: dict = {"_total_dies": len(rows)}

    # Count bin-1 (pass) dies — mirror dashboard's _find_col(cols, 'INTERFACE_BIN', '6248')
    # Requires column name to contain both "INTERFACE BIN" and "6248" (case/underscore-insensitive)
    def _norm(s: str) -> str:
        return s.upper().replace("_", " ")
    _ibin_col = next(
        (h for h in headers if "INTERFACE BIN" in _norm(h) and "6248" in _norm(h)),
        None,
    )
    _bin1 = 0
    _bin1_rows: list[dict] = []
    if _ibin_col:
        for _row in rows:
            try:
                if int(float((_row.get(_ibin_col) or "").strip())) == 1:
                    _bin1 += 1
                    _bin1_rows.append(_row)
            except (ValueError, TypeError):
                pass
    else:
        # No IBIN column found — fall back to all rows so freq counts still work
        _bin1_rows = rows
    result["_bin1_dies"] = _bin1

    for module, patterns in vmin_search.items():
        if isinstance(patterns, str):
            patterns = [patterns]

        # Find all vmin columns for this module, keyed by fmhz
        vmin_cols: dict[int, list[str]] = {}
        for col in headers:
            if not any(p in col for p in patterns):
                continue
            m = re.search(r'_(\d+\.\d+)_(\d+)$', col)
            if not m:
                continue
            fmhz = int(round(float(m.group(1)) * 1000))
            vmin_cols.setdefault(fmhz, []).append(col)

        if not vmin_cols:
            continue

        # Count dies with valid vmin (> 0) at ANY instance per frequency.
        # Use only bin-1 rows so numerator and denominator are the same population,
        # keeping all percentages ≤ 100%.
        freq_data: list[tuple] = []
        for fmhz in sorted(vmin_cols.keys(), reverse=True):
            cols_at_freq = vmin_cols[fmhz]
            n_valid = 0
            for row in _bin1_rows:
                for col in cols_at_freq:
                    try:
                        val = float((row.get(col) or "").strip())
                        if val > 0:
                            n_valid += 1
                            break   # count die once per freq
                    except (ValueError, TypeError):
                        pass
            freq_data.append((fmhz, f"{fmhz / 1000:g}G", n_valid))  # include 0-count too

        valid_freqs = [f for f in freq_data if f[2] > 0]
        zero_freqs  = [f for f in freq_data if f[2] == 0]
        n_top = 4 if module == "CORE" else 3
        # Merge and re-sort by fmhz descending so 0-count appears in freq order
        top_n = sorted(
            valid_freqs[:n_top] + zero_freqs[:1],
            key=lambda x: x[0], reverse=True,
        )

        result[module] = {
            "label": _MOD_LABEL.get(module, module),
            "freqs": top_n,
            "n_top": n_top,
        }

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Build email
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_freqs(freqs: list[tuple], total_dies: int = 0, bin1_dies: int = 0) -> str:
    """Format freq list as lines: freq label + count + % of bin-1 (or total if no bin-1)."""
    if not freqs:
        return "\u2014"
    denom = bin1_dies if bin1_dies > 0 else total_dies
    lines = []
    for _fmhz, label, n_valid in freqs:
        pct = n_valid / denom * 100 if denom > 0 else 0.0
        pct_str = "0%" if n_valid == 0 else f"{pct:.1f}%"
        lines.append(
            f'<b style="color:#0071c5">{label}</b>\u00a0{n_valid:,}\u00a0'
            f'<span style="color:#555">({pct_str})</span>'
        )
    return "<br>".join(lines)


def save_run_record(
    run_dir: Path, run_ts: str, aqua_file: str, tp_results: list[dict]
) -> None:
    """Persist a compact run record JSON inside run_dir for later history loading."""
    import json
    record: dict = {"run_ts": run_ts, "aqua_file": aqua_file, "tp_results": []}
    for r in tp_results:
        summary  = r.get("summary", {})
        s_serial: dict = {
            "_total_dies": summary.get("_total_dies", 0),
            "_bin1_dies":  summary.get("_bin1_dies", 0),
        }
        for mod in ("CORE", "ATOM", "CCF"):
            if mod in summary:
                s_serial[mod] = {
                    "label": summary[mod].get("label", mod),
                    "freqs": [list(f) for f in summary[mod].get("freqs", [])],
                    "n_top": summary[mod].get("n_top", 3),
                }
        hp  = r.get("html_path")
        hfp = r.get("html_full_path")
        record["tp_results"].append({
            "tp_name":        r["tp_name"],
            "ok":             r["ok"],
            "html_path":      str(hp)  if hp  else "",
            "html_full_path": str(hfp) if hfp else "",
            "summary":        s_serial,
        })
    try:
        with open(run_dir / "run_record.json", "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
    except Exception as e:
        _log(f"  WARNING: could not save run_record.json: {e}")


def _refresh_tp_summary_from_disk(r: dict) -> None:
    """Re-read the .summary.json file that sits next to the HTML output.

    This ensures the email always uses the freshest on-disk data rather than
    whatever was snapshotted inside run_record.json at pipeline run-time
    (which may have been saved when the AQUA CSV lacked vmin columns).
    Only updates if the file exists and the HTML output file also exists.
    """
    import json as _json
    hp_str = r.get("html_path") or ""
    if not hp_str:
        return
    hp = Path(hp_str)
    if not hp.exists():
        return   # HTML output file gone / not yet created — skip
    sjp = hp.with_name(hp.stem + ".summary.json")
    if not sjp.exists():
        return
    try:
        with open(sjp, encoding="utf-8") as fh:
            r["summary"] = _summary_from_json(_json.load(fh))
    except Exception:
        pass  # leave existing summary intact on read error


def load_run_history(output_dir: Path, limit: int = 8) -> list[dict]:
    """Load run records from NVL_Class_* dirs, newest first.

    Dirs that have a run_record.json are loaded directly.  Older dirs that
    were created before save_run_record existed get a minimal synthetic record
    built from the HTML files found in their TP sub-directories.

    In both cases the summary for each TP is refreshed by re-reading the
    .summary.json file next to the HTML output — so the email always reflects
    the current on-disk state rather than a stale snapshot.
    """
    import json
    run_dirs = sorted(
        [d for d in output_dir.glob("NVL_Class_*") if d.is_dir()],
        key=lambda d: d.name, reverse=True,
    )
    records: list[dict] = []
    for d in run_dirs[:limit]:
        rj = d / "run_record.json"
        if rj.exists():
            try:
                with open(rj, encoding="utf-8") as fh:
                    rec = json.load(fh)
                # Always re-read summary.json from disk — don't trust the snapshot
                for r in rec.get("tp_results", []):
                    _refresh_tp_summary_from_disk(r)
                records.append(rec)
                continue
            except Exception:
                pass
        # ── Synthesize minimal record from HTML files in subdirs ──────────
        run_ts     = d.name.replace("NVL_Class_", "")
        tp_results = []
        for tp_dir in sorted(d.iterdir()):
            if not tp_dir.is_dir():
                continue
            html_files = list(tp_dir.glob("*_class_analysis.html"))
            hp = html_files[0] if html_files else None
            ok = hp is not None and hp.exists()
            summary: dict = {"_total_dies": 0}
            if hp and hp.exists():
                sjp = hp.with_name(hp.stem + ".summary.json")
                if sjp.exists():
                    try:
                        with open(sjp, encoding="utf-8") as fh:
                            summary = _summary_from_json(json.load(fh))
                    except Exception:
                        pass
            tp_results.append({
                "tp_name":   tp_dir.name,
                "ok":        ok,
                "html_path": str(hp) if hp else "",
                "summary":   summary,
            })
        if tp_results:
            records.append({
                "run_ts":     run_ts,
                "aqua_file": "",
                "tp_results": tp_results,
            })
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Compute bin_matrix rows directly from per-TP .csv.gz files
# ─────────────────────────────────────────────────────────────────────────────

def _compute_bm_from_programs_dir(programs_dir: Path) -> list[dict]:
    """Read all per-TP .csv.gz files in programs_dir and extract bin_matrix rows.

    Uses the product config's bin_matrix section (same logic as class_analysis_html.py).
    Returns a list of dicts: {speed_pct, mat, tp_rev, dlcp, pas_qdf, lot, wafer, ww}
    """
    import fnmatch as _fnm

    if not programs_dir.exists():
        return []

    cfg_path = _find_product_cfg()
    if not cfg_path:
        return []
    try:
        prod = _load_product_cfg(cfg_path)
    except Exception:
        return []

    bm_cfg      = prod.get("bin_matrix", {})
    dlcp_cfg    = bm_cfg.get("DLCP", {})
    prog_cfg    = bm_cfg.get("ProgramName", {})
    dlcp_map    = dlcp_cfg.get("dlcpMap", {})
    dlcp_start  = dlcp_cfg.get("dlcpExtractStart", 4)
    dlcp_len    = dlcp_cfg.get("dlcpExtractLength", 2)
    tp_start    = prog_cfg.get("tpRevStart", 7)
    tp_len      = prog_cfg.get("tpRevLength", 8)

    pas_qdf_pat = bm_cfg.get("passingQdfPattern", "VA-NA-UNIT-PASSING_QDFS_*_CLASSHOT")
    drev_pat    = dlcp_cfg.get("devRevStepPattern", "DEVREVSTEP_*_CLASSHOT")
    prog_pat    = prog_cfg.get("programNamePattern", "PROGRAM NAME_*_CLASSHOT")
    ww_pat      = bm_cfg.get("wwPattern", "*WORKWEEK*")

    upm_keys  = list(prod.get("sort_upm", {}).keys())
    upm_key   = upm_keys[0] if upm_keys else ""
    speed_tgt = float(prod.get("sort_upm_ref", {}).get(upm_key, 0) or 0)

    sort_lot_col_hint   = prod.get("sort_lot_col", "")
    sort_wafer_col_hint = prod.get("sort_wafer_col", "")

    all_rows: list[dict] = []

    for gz_path in sorted(programs_dir.glob("*.csv.gz")):
        try:
            rows, _delim = _read_aqua_file(gz_path)
        except Exception as _e:
            _log(f"  bm-compute: could not read {gz_path.name}: {_e}")
            continue
        if not rows:
            continue
        headers = list(rows[0].keys())

        def _find(pat: str) -> "str | None":
            return next((h for h in headers if _fnm.fnmatchcase(h.upper(), pat.upper())), None)

        pas_qdf_col = _find(pas_qdf_pat)
        drev_col    = _find(drev_pat)
        prog_col    = _find(prog_pat)
        ww_col      = _find(ww_pat)
        speed_col   = _find(upm_key.upper() + "*") if upm_key else None
        if not speed_col and upm_key:
            speed_col = next((h for h in headers if upm_key.lower() in h.lower()), None)

        lot_col_f   = next((c for c in [sort_lot_col_hint,   "SORT_LOT",   "Lot",   "LOT"]   if c and c in headers), None)
        wafer_col_f = next((c for c in [sort_wafer_col_hint, "SORT_WAFER", "Wafer", "WAFER"] if c and c in headers), None)
        mat_col_f   = next((c for c in ["Material Type, Skew, BEOL Skew", "Material Type", "MATERIAL_TYPE"] if c in headers), None)
        ibin_col_f  = next((h for h in headers if "INTERFACE BIN" in h.upper().replace("_", " ") and "6248" in h.upper()), None)

        for row in rows:
            if ibin_col_f:
                try:
                    if int(float((row.get(ibin_col_f) or "").strip())) != 1:
                        continue
                except (ValueError, TypeError):
                    continue

            spd_raw = row.get(speed_col, "") if speed_col else ""
            try:
                spd_val = float(str(spd_raw).strip())
                spd_pct = round(spd_val / speed_tgt * 100, 2) if speed_tgt else None
            except (ValueError, TypeError):
                spd_pct = None

            prog_str = str(row.get(prog_col, "") if prog_col else "")
            drev_str = str(row.get(drev_col, "") if drev_col else "")
            tp_rev   = prog_str[tp_start : tp_start + tp_len] if prog_str else ""
            dlcp_key = drev_str[dlcp_start : dlcp_start + dlcp_len] if drev_str else ""

            wafer_raw = str(row.get(wafer_col_f, "") if wafer_col_f else "")
            try:
                wafer_str = str(int(float(wafer_raw)))
            except (ValueError, TypeError):
                wafer_str = wafer_raw

            all_rows.append({
                "lot":       str(row.get(lot_col_f, "")   if lot_col_f   else ""),
                "wafer":     wafer_str,
                "speed_pct": spd_pct,
                "mat":       str(row.get(mat_col_f, "") if mat_col_f else "").strip() or "—",
                "tp_rev":    tp_rev,
                "dlcp":      dlcp_map.get(dlcp_key, dlcp_key),
                "pas_qdf":   str(row.get(pas_qdf_col, "") if pas_qdf_col else ""),
                "ww":        str(row.get(ww_col, "")      if ww_col      else ""),
            })

    return all_rows


# ─────────────────────────────────────────────────────────────────────────────
# Speed Flow – Bin Matrix email section
# Reads BM_ROWS + QDF_ROWS directly from the full_data .data.js sidecar file
# and renders the same pivot tables shown in the dashboard (By TP Rev + By Material)
# ─────────────────────────────────────────────────────────────────────────────

def _build_speed_flow_bm_html_from_datajs(data_js_path: Path) -> str:
    """Parse BM_ROWS and QDF_ROWS from a .data.js sidecar and build the email pivot tables.

    BM_ROWS format : [{lot, wafer, speed_pct, mat, tp_rev, dlcp, pas_qdf, ww}, ...]
      pas_qdf is a caret-separated list e.g. "L2PQ^L2PR"
    QDF_ROWS format: [{QDF, LINE_ITEM_NAME, FUNCTIONAL_BIG_CORE, ...}, ...]
    """
    import re as _re
    import json as _json

    try:
        raw = data_js_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        _log(f"  BM: could not read {data_js_path}: {e}")
        return ""

    def _extract_var(name: str) -> list:
        m = _re.search(
            rf'var\s+{name}\s*=\s*(\[.*?\]);',
            raw, _re.DOTALL,
        )
        if not m:
            return []
        try:
            return _json.loads(m.group(1))
        except Exception:
            return []

    bm_rows  = _extract_var("BM_ROWS")
    qdf_rows = _extract_var("QDF_ROWS")

    if not bm_rows or not qdf_rows:
        _log(f"  BM: BM_ROWS={len(bm_rows)} QDF_ROWS={len(qdf_rows)} — nothing to render")
        return ""

    # Normalise QDF key (strip BOM)
    qdf_list = []
    for q in qdf_rows:
        key = next((v for k, v in q.items() if "QDF" in k.upper()), "")
        qdf_list.append({**q, "_qdf": key.strip()})

    # Build set of QDF codes per die (pas_qdf is "^"-separated)
    # die_qdfs: list of (row, set_of_qdfs)
    die_qdfs = []
    for r in bm_rows:
        pq = str(r.get("pas_qdf") or "")
        qdfs = {q.strip() for q in pq.split("^") if q.strip()}
        die_qdfs.append((r, qdfs))

    total_dies = len(bm_rows)

    # ── shared styles (dark mode) ──────────────────────────────────────────────
    # Frozen columns: QDF(80px) + LINE_ITEM_NAME(140px) + BIG_CORE(60px) + SMALL_CORE(60px) + MAX_TURBO(60px) + SC_MAX_TURBO(60px)
    _FROZEN_SPEC = [
        "LINE_ITEM_NAME", "FUNCTIONAL_BIG_CORE", "FUNCTIONAL_SMALL_CORE",
        "MAX_TURBO_FREQ_RTE", "SMALL_CORE_MAX_TURBO_FREQ_RATE",
    ]
    _FROZEN_WIDTHS = {"_qdf": 80, "LINE_ITEM_NAME": 140,
                      "FUNCTIONAL_BIG_CORE": 62, "FUNCTIONAL_SMALL_CORE": 62,
                      "MAX_TURBO_FREQ_RTE": 62, "SMALL_CORE_MAX_TURBO_FREQ_RATE": 62}
    def _sticky_left(col_id):
        """Calculate cumulative left offset for a frozen column."""
        order = ["_qdf"] + _FROZEN_SPEC
        left = 0
        for c in order:
            if c == col_id:
                return left
            left += _FROZEN_WIDTHS.get(c, 80)
        return left
    def _th_frozen(col_id):
        w = _FROZEN_WIDTHS.get(col_id, 80)
        return (f"background:#0d2d45;color:#4fc3f7;padding:5px 7px;text-align:left;"
                f"white-space:nowrap;border:1px solid #1e3a52;font-size:0.77em;"
                f"position:sticky;left:{_sticky_left(col_id)}px;z-index:3;"
                f"min-width:{w}px;max-width:{w}px")
    def _td_frozen(col_id, bg="#0d1f2e"):
        w = _FROZEN_WIDTHS.get(col_id, 80)
        return (f"padding:4px 7px;border:1px solid #1e3a52;font-size:0.77em;"
                f"background:{bg};color:#c8d8e8;"
                f"position:sticky;left:{_sticky_left(col_id)}px;z-index:2;"
                f"min-width:{w}px;max-width:{w}px;overflow:hidden;text-overflow:ellipsis")

    th_fix = _th_frozen("_qdf")
    th_col = ("background:#0d2d45;color:#4fc3f7;padding:5px 7px;text-align:center;"
              "white-space:nowrap;border:1px solid #1e3a52;font-size:0.77em;min-width:70px")
    td_fix = _td_frozen("_qdf")
    td_val = ("padding:4px 7px;border:1px solid #1e3a52;font-size:0.77em;"
              "text-align:center;white-space:nowrap;background:#0f2233;color:#c8d8e8")
    td_val_z = td_val + ";color:#3a5570"
    tbl    = ("border-collapse:collapse;font-family:Segoe UI,Arial,sans-serif;"
              "font-size:0.85em")
    tbl_wrap = "overflow-x:auto;max-width:100%;position:relative"
    sec_h  = ("color:#4fc3f7;font-size:0.9em;font-weight:bold;margin:14px 0 4px 0;"
              "border-bottom:2px solid #1e3a52;padding-bottom:2px")

    # QDF spec columns to show
    spec_cols = [
        "LINE_ITEM_NAME", "FUNCTIONAL_BIG_CORE", "FUNCTIONAL_SMALL_CORE",
        "MAX_TURBO_FREQ_RTE", "SMALL_CORE_MAX_TURBO_FREQ_RATE", "RING_TFM_FREQ",
        "HUB2CPU_D2D_FREQ", "HUB2GPU_D2D_FREQ", "INTEGRATED_GRAPHICS_TURBO_FREQ",
        "THERMAL_DESIGN_POWER",
    ]
    spec_heads = "".join(
        f'<th style="{_th_frozen(sc) if sc in _FROZEN_SPEC else th_col}">{sc.replace("_", " ")}</th>'
        for sc in spec_cols
    )

    def _pivot_table(group_key_fn, col_label_fn, section_title: str) -> str:
        """Generic pivot: group dies by group_key_fn(row), count per QDF."""
        from collections import defaultdict, Counter
        import statistics

        # Collect groups: {col_key: [row, ...]}
        col_groups: dict = defaultdict(list)
        for r, _ in die_qdfs:
            k = group_key_fn(r)
            if k:
                col_groups[k].append(r)

        if not col_groups:
            return ""

        # Sort columns
        col_keys = sorted(col_groups.keys())

        # Column headers: label + med UPM% + vol
        col_header_cells = ""
        for ck in col_keys:
            rows_in_col = col_groups[ck]
            vol = len(rows_in_col)
            spds = [r.get("speed_pct") for r in rows_in_col if r.get("speed_pct") is not None]
            med_spd = f"{statistics.median(spds):.1f}%" if spds else "—"
            lbl = col_label_fn(ck)
            col_header_cells += (
                f'<th style="{th_col}">{lbl}<br>'
                f'<span style="font-weight:normal;font-size:0.85em">Med UPM%: {med_spd}<br>Vol: {vol:,}</span>'
                f'</th>'
            )

        # Build table rows — one per QDF
        data_rows_html = ""
        for qi, qr in enumerate(qdf_list):
            qdf_code = qr["_qdf"]
            if not qdf_code:
                continue
            bg = "#0d1f2e" if qi % 2 == 0 else "#0f2233"
            # spec cells
            spec_cells = "".join(
                f'<td style="{_td_frozen(sc, bg) if sc in _FROZEN_SPEC else td_val+";background:"+bg}">{qr.get(sc, "")}</td>'
                for sc in spec_cols
            )
            # value cells
            val_cells = ""
            for ck in col_keys:
                col_dies   = col_groups[ck]
                n_total    = len(col_dies)
                n_match    = sum(1 for r, qs in die_qdfs
                                 if group_key_fn(r) == ck and qdf_code in qs)
                pct        = n_match / n_total * 100 if n_total else 0.0
                cell_style = td_val_z if n_match == 0 else td_val
                val_cells += (
                    f'<td style="{cell_style};background:{bg}">'
                    f'{pct:.1f}%<br>'
                    f'<span style="color:#777;font-size:0.88em">{n_match}/{n_total}</span>'
                    f'</td>'
                )
            data_rows_html += (
                f'<tr style="background:{bg}">'
                f'<td style="{_td_frozen("_qdf", bg)}">{qdf_code}</td>'
                f'{spec_cells}'
                f'{val_cells}'
                f'</tr>\n'
            )

        return (
            f'<p style="{sec_h}">{section_title}</p>\n'
            f'<div style="{tbl_wrap}">'
            f'<table style="{tbl}">'
            f'<thead>'
            f'<tr><th style="{th_fix}">QDF</th>{spec_heads}{col_header_cells}</tr>'
            f'</thead>'
            f'<tbody>'
            f'{data_rows_html}'
            f'</tbody>'
            f'</table></div>\n'
        )

    # ── By TP Rev (WW | tp_rev) — BLLC material only (left panel) ──────────────
    die_qdfs_tp = [
        (r, qs) for r, qs in die_qdfs
        if "BLLC" in str(r.get("mat") or "").upper()
    ]
    # swap die_qdfs temporarily so _pivot_table sees the filtered set
    _die_qdfs_orig = die_qdfs
    die_qdfs = die_qdfs_tp

    def _tp_key(r):
        ww = str(r.get("ww") or "").strip()
        tp = str(r.get("tp_rev") or "").strip()
        return f"{ww} | {tp}" if (ww or tp) else ""

    by_tp_html  = _pivot_table(_tp_key, lambda k: k,
                               "By TP Rev  (BLLC)")

    die_qdfs = _die_qdfs_orig  # restore for By Material

    # ── By Material ────────────────────────────────────────────────────────────
    by_mat_html = _pivot_table(
        lambda r: str(r.get("mat") or "").strip() or None,
        lambda k: k,
        "By Material",
    )

    if not by_tp_html and not by_mat_html:
        return ""

    return (
        f'<div style="margin-top:24px">'
        f'<h3 style="color:#4fc3f7;margin-bottom:2px;font-family:Segoe UI,Arial,sans-serif">'
        f'Speed Flow \u2014 Bin Matrix</h3>\n'
        f'{by_tp_html}'
        f'{by_mat_html}'
        f'</div>\n'
    )


def _build_speed_flow_bm_html(bm_rows: list[dict]) -> str:
    """Kept for compatibility — no longer called by the email builder."""
    return ""



    th_s  = ("background:#0071c5;color:#fff;padding:6px 10px;text-align:left;"
             "white-space:nowrap;border:1px solid #005a9e;font-size:0.82em")
    th_r  = ("background:#0071c5;color:#fff;padding:6px 10px;text-align:right;"
             "white-space:nowrap;border:1px solid #005a9e;font-size:0.82em")
    td_s  = "padding:5px 8px;border:1px solid #d0d8e8;font-size:0.82em;white-space:nowrap"
    td_r  = "padding:5px 8px;border:1px solid #d0d8e8;font-size:0.82em;text-align:right;white-space:nowrap"
    tbl   = "border-collapse:collapse;font-family:Segoe UI,Arial,sans-serif;margin:0"

    # ── TP panel ──────────────────────────────────────────────────────────────
    tp_counts: Counter = Counter(r.get("tp_rev", "") or "—" for r in bm_rows)
    tp_html = (
        f'<table style="{tbl}">'
        f'<tr><th style="{th_s}">TP Rev</th><th style="{th_r}">Count</th><th style="{th_r}">%</th></tr>\n'
    )
    for idx, (tp, cnt) in enumerate(sorted(tp_counts.items(), key=lambda x: (-x[1], x[0]))):
        bg  = "#f7f9fc" if idx % 2 == 0 else "#ffffff"
        pct = cnt / total * 100
        tp_html += (
            f'<tr style="background:{bg}">'
            f'<td style="{td_s}">{tp}</td>'
            f'<td style="{td_r}">{cnt:,}</td>'
            f'<td style="{td_r}">{pct:.1f}%</td>'
            f'</tr>\n'
        )
    tp_html += '</table>'

    # ── Material panel ────────────────────────────────────────────────────────
    mat_counts: Counter = Counter(r.get("mat", "") or "—" for r in bm_rows)
    mat_html = (
        f'<table style="{tbl}">'
        f'<tr><th style="{th_s}">Material</th><th style="{th_r}">Count</th><th style="{th_r}">%</th></tr>\n'
    )
    for idx, (mat, cnt) in enumerate(sorted(mat_counts.items(), key=lambda x: (-x[1], x[0]))):
        bg  = "#f7f9fc" if idx % 2 == 0 else "#ffffff"
        pct = cnt / total * 100
        mat_html += (
            f'<tr style="background:{bg}">'
            f'<td style="{td_s}">{mat}</td>'
            f'<td style="{td_r}">{cnt:,}</td>'
            f'<td style="{td_r}">{pct:.1f}%</td>'
            f'</tr>\n'
        )
    mat_html += '</table>'

    panel_label = ("font-weight:bold;color:#4fc3f7;font-family:Segoe UI,Arial,sans-serif;"
                   "font-size:0.88em;margin-bottom:4px")
    return (
        f'<div style="margin-top:24px">'
        f'<h3 style="color:#4fc3f7;margin-bottom:2px;font-family:Segoe UI,Arial,sans-serif">'
        f'Speed Flow \u2014 Bin Matrix</h3>'
        f'<table style="border:none;border-collapse:separate;border-spacing:24px 0">'
        f'<tr><td style="vertical-align:top;padding:0">'
        f'<p style="{panel_label}">TP</p>{tp_html}'
        f'</td><td style="vertical-align:top;padding:0">'
        f'<p style="{panel_label}">Material</p>{mat_html}'
        f'</td></tr></table>'
        f'</div>\n'
    )



def build_class_email_body(
    run_records: list[dict],   # newest first; each has run_ts, aqua_file, tp_results
    run_log: Path,
    exclude_patterns: "list[str] | None" = None,
) -> str:
    """Build HTML email showing all historical runs grouped by date, newest first.

    exclude_patterns: fnmatch patterns for TPs to hide from email rows (e.g. R0 / auxiliary).
    TPs not matching are shown. Pass None / empty list to show all TPs (default behaviour).
    """
    from datetime import datetime as _dt

    def _fmt_ts(ts: str) -> str:
        try:
            return _dt.strptime(ts, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ts

    if not run_records:
        return "<html><body><p>No run data available.</p></body></html>"

    # Collect modules present across all records
    mods_seen = []
    for key in ("CORE", "ATOM", "CCF"):
        if any(
            key in r.get("summary", {})
            for rec in run_records
            for r in rec.get("tp_results", [])
        ):
            mods_seen.append(key)

    overall = "OK" if all(
        r["ok"] for r in run_records[0].get("tp_results", [])
    ) else "PARTIAL / FAILED"

    th = "background:#0d2d45;color:#4fc3f7;padding:8px 10px;text-align:left;white-space:nowrap;border:1px solid #1e3a52"
    n_top_label = {"CORE": "Top 4", "ATOM": "Top 3", "CCF": "Top 3"}
    mod_heads = "".join(
        f'<th style="{th}">{_MOD_LABEL.get(m, m)}<br>'
        f'<span style="font-weight:normal;font-size:0.78em">{n_top_label.get(m, "Top 3")} Freqs (dies, % of bin1)</span></th>'
        for m in mods_seen
    )
    n_cols     = 4 + len(mods_seen)
    header_row = (
        f'<tr>'
        f'<th style="{th}">Test Program</th>'
        f'<th style="{th}">Date / Time</th>'
        f'<th style="{th}">Total Dies<br><span style="font-weight:normal;font-size:0.78em">(bin1 pass)</span></th>'
        f'{mod_heads}'
        f'<th style="{th}">Status</th>'
        f'</tr>'
    )

    td = "padding:8px 10px;border:1px solid #1e3a52;color:#c8d8e8"
    sh = "background:#0a1e2e;color:#4fc3f7;font-weight:bold;padding:5px 10px;border:1px solid #1e3a52;font-size:0.85em"
    all_rows = ""

    # Derive the programs data dir from the run_log path (run_log is base_dir/run_log.html)
    _programs_dir   = run_log.parent / "data" / "programs"
    _merge_data_dir = run_log.parent / "data" / "merge-data"

    seen_tps: set[str] = set()   # TPs already shown from a newer run (deduplicate)

    for rec_idx, rec in enumerate(run_records):
        run_ts     = rec["run_ts"]
        tp_results = rec.get("tp_results", [])
        disp_ts    = _fmt_ts(run_ts)
        lbl        = " \u2014 current" if rec_idx == 0 else ""

        # Filter: skip TPs with no freq data and skip TPs already shown in a newer run
        rows_this_run = []
        for r in tp_results:
            tp_name = r["tp_name"]
            if tp_name in seen_tps:
                continue   # already displayed from a newer run
            # Exclude filter: hide TPs that match email_exclude_patterns (R0 / auxiliary)
            if exclude_patterns and any(
                fnmatch.fnmatchcase(tp_name.upper(), p.upper())
                for p in exclude_patterns
            ):
                continue   # auxiliary / R0 program — process but hide from email
            summary = r.get("summary", {})
            if not summary.get("_total_dies", 0) and not any(
                summary.get(mod, {}).get("freqs") for mod in mods_seen
            ):
                continue   # completely empty — no dies and no freq data at all
            rows_this_run.append(r)
            seen_tps.add(tp_name)

        if not rows_this_run:
            continue   # nothing to show for this run — omit its header too

        # Sort by gz mtime descending (most recently updated program first),
        # fall back to tp_name ascending for ties / missing files.
        def _tp_sort_key(r: dict) -> tuple:
            _sn = re.sub(r'[\\/:*?"<>|]', '_', r["tp_name"]).strip()
            _gz = _programs_dir / f"{_sn}.csv.gz"
            try:
                mtime = _gz.stat().st_mtime
            except OSError:
                mtime = 0.0
            return (-mtime, r["tp_name"])

        rows_this_run.sort(key=_tp_sort_key)

        all_rows += (
            f'<tr><td colspan="{n_cols}" style="{sh}">'
            f'&#128337; {disp_ts}{lbl}</td></tr>\n'
        )
        for i, r in enumerate(rows_this_run):
            tp_name    = r["tp_name"]
            ok         = r["ok"]
            html_path  = r.get("html_path") or ""
            summary    = r.get("summary", {})
            total_dies = summary.get("_total_dies", 0)
            bin1_dies  = summary.get("_bin1_dies", 0)
            hp = Path(html_path) if html_path else None
            if hp and hp.exists():
                tp_link = (
                    f'<a href="{hp.as_uri()}" '
                    f'style="color:#0071c5;font-weight:bold">{tp_name}</a>'
                )
            else:
                tp_link = f'<b>{tp_name}</b>'
            # Full-data dashboard link
            hfp = Path(r.get("html_full_path") or "") if r.get("html_full_path") else None
            if hfp and hfp.exists():
                tp_link += (
                    f'<br><a href="{hfp.as_uri()}" '
                    f'style="color:#888;font-size:0.82em;font-weight:normal">'
                    f'&#9654; Full data</a>'
                )
            # Raw data gz link
            _safe_tp = re.sub(r'[\\/:*?"<>|]', '_', tp_name).strip()
            _gz = _programs_dir / f"{_safe_tp}.csv.gz"
            if _gz.exists():
                tp_link += (
                    f'<br><a href="{_gz.as_uri()}" '
                    f'style="color:#5d8a3c;font-size:0.80em;font-weight:normal">'
                    f'\U0001f4c4 raw data</a>'
                )
            # Merge-data file links (shown once per first TP row only, to avoid repetition)
            if rec_idx == 0 and i == 0 and _merge_data_dir.exists():
                _mfiles = sorted(
                    p for p in _merge_data_dir.iterdir()
                    if p.suffix.lower() in ('.csv', '.zip', '.gz')
                    or p.name.lower().endswith('.csv.gz')
                )
                for _mf in _mfiles:
                    tp_link += (
                        f'<br><a href="{_mf.as_uri()}" '
                        f'style="color:#8b5e00;font-size:0.80em;font-weight:normal">'
                        f'\U0001f4e6 {_mf.name}</a>'
                    )
            status_label = "\u2714 OK" if ok else "\u2716 FAILED"
            status_color = "#66bb6a" if ok else "#ef5350"
            mod_cells = ""
            for mod in mods_seen:
                mod_info = summary.get(mod, {})
                freqs = [
                    tuple(f) if not isinstance(f, tuple) else f
                    for f in mod_info.get("freqs", [])
                ]
                mod_cells += (
                    f'<td style="{td};font-size:0.86em;vertical-align:top">'
                    f'{_fmt_freqs(freqs, total_dies, bin1_dies)}</td>'
                )
            row_bg = "#2a0f0f" if not ok else ("#0d1f2e" if i % 2 == 0 else "#0f2233")
            all_rows += (
                f'<tr style="background:{row_bg}">'
                f'<td style="{td}">{tp_link}</td>'
                f'<td style="{td};color:#7a9bb5;white-space:nowrap;font-size:0.88em">{disp_ts}</td>'
                f'<td style="{td};text-align:center">{total_dies:,}'
                + (f'<br><span style="color:#5a7a9a;font-size:0.82em">({bin1_dies:,})</span>' if bin1_dies else '')
                + '</td>'
                f'{mod_cells}'
                f'<td style="{td};color:{status_color};font-weight:bold;text-align:center">'
                f'{status_label}</td>'
                f'</tr>\n'
            )

    latest_ts = _fmt_ts(run_records[0]["run_ts"])

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body   {{ background:#0f1e2b; color:#d0dde8; font-family:Segoe UI,Arial,sans-serif;
             max-width:1200px; margin:0 auto; padding:16px; }}
  h2     {{ color:#4fc3f7; margin-bottom:4px; }}
  a      {{ color:#4fc3f7; }}
  table.main {{ border-collapse:collapse; width:100%; font-size:0.88em;
               margin-top:8px; border:1px solid #1e3a52; }}
  table.main th {{ background:#0d2d45; color:#4fc3f7; padding:8px 10px;
                  text-align:left; white-space:nowrap;
                  border:1px solid #1e3a52; }}
  table.main td {{ padding:8px 10px; border:1px solid #1e3a52; color:#c8d8e8; }}
  table.main tr:nth-child(even) td {{ background:#0d1f2e; }}
  table.main tr:nth-child(odd)  td {{ background:#0f2233; }}
  table.main tr td:first-child a {{ color:#4fc3f7; font-weight:bold; }}

</style>
</head>
<body>
  <h2>NVL816-BLLC CLASS Report \u2014 {overall}</h2>
  <p style="color:#7a9bb5;font-size:0.88em;margin-top:0">{latest_ts}</p>
  <table class="main" border="1" cellpadding="0" cellspacing="0">
    {header_row}
    {all_rows}
  </table>
  <p style="color:#3a5570;font-size:0.78em;margin-top:12px">
    Run log: <a href="{run_log.as_uri()}">{run_log.name}</a>
  </p>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Email sending (same pattern as yield)
# ─────────────────────────────────────────────────────────────────────────────

def _send_via_outlook(to: str, subject: str, body_html: str,
                      attachments: list[str]) -> None:
    import tempfile, shutil as _shutil
    import win32com.client as _w
    _ol = _w.Dispatch("Outlook.Application")
    _m  = _ol.CreateItem(0)
    _m.To       = to
    _m.Subject  = subject
    _m.HTMLBody = body_html

    # attachments are local paths (already written to temp by send_email)
    for att in attachments:
        src = Path(att)
        if src.exists():
            _m.Attachments.Add(str(src))
            _log(f"  Attaching: {src.name}")
        else:
            _log(f"  WARNING: attachment not found, skipping: {src}")
    try:
        _m.Send()
    except Exception as _send_err:
        raise RuntimeError(f"Outlook Send() raised: {_send_err}") from _send_err
    _log("  Email sent via Outlook COM.")


def _send_via_smtp(to: str, subject: str, body_html: str,
                   attachments: list[str]) -> None:
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text      import MIMEText
    from email.mime.application import MIMEApplication

    msg = MIMEMultipart("mixed")
    msg["From"]    = _SMTP_FROM
    msg["To"]      = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    for att in attachments:
        src = Path(att)
        if not src.exists():
            _log(f"  WARNING: attachment not found, skipping: {src}")
            continue
        with open(str(src), "rb") as f:
            part = MIMEApplication(f.read(), Name=src.name)
        part["Content-Disposition"] = f'attachment; filename="{src.name}"'
        msg.attach(part)
        _log(f"  Attaching: {src.name}")

    with smtplib.SMTP(_SMTP_SERVER, _SMTP_PORT, timeout=30) as s:
        s.starttls()
        s.sendmail(_SMTP_FROM, [a.strip() for a in to.split(";")],
                   msg.as_string())
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

    # Write the email body to a local temp HTML file so recipients can open
    # it in a browser for the best rendering experience.
    import tempfile as _tf
    _tmp = Path(_tf.mkdtemp(prefix="cls_report_"))
    _safe = re.sub(r'[^\w\-]', '_', subject[:40])
    _html_att = _tmp / f"{_safe}.html"
    _html_att.write_text(body_html, encoding="utf-8")
    _log(f"  Attaching report HTML: {_html_att.name}")
    atts = [str(_html_att)]

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
        _log(f"  ERROR sending email: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Run log
# ─────────────────────────────────────────────────────────────────────────────

def _append_run_log(run_log: Path, run_ts: str, tp_results: list[dict]) -> None:
    """Append a section to the cumulative HTML run log."""
    rows = ""
    for r in sorted(tp_results, key=lambda x: x["tp_name"]):
        tp   = r["tp_name"]
        ok   = r["ok"]
        html = r.get("html_path")
        c    = "#27ae60" if ok else "#c0392b"
        lbl  = "OK" if ok else "FAILED"
        link = (
            f'<a href="{Path(html).as_uri()}">{tp}</a>'
            if html and Path(html).exists() else tp
        )
        rows += (
            f"<tr><td>{link}</td>"
            f"<td style='color:{c}'>{lbl}</td></tr>\n"
        )

    block = f"""
<details>
<summary>{run_ts} — {len(tp_results)} TP(s)</summary>
<table border="1" cellpadding="4" cellspacing="0"
       style="border-collapse:collapse;margin:6px 0;font-size:0.85em">
  <tr style="background:#dde;"><th>Test Program</th><th>Status</th></tr>
  {rows}
</table>
</details>
"""
    if run_log.exists():
        content = run_log.read_text(encoding="utf-8")
        insert_at = content.find("<!-- RUNS -->")
        if insert_at >= 0:
            run_log.write_text(
                content[:insert_at + len("<!-- RUNS -->")] + block + content[insert_at + len("<!-- RUNS -->"):],
                encoding="utf-8",
            )
            return

    # Create fresh run log
    import os as _os; _os.makedirs(str(run_log.parent), exist_ok=True)
    run_log.write_text(
        f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        f'<title>CLASS Automation Run Log</title>'
        f'<style>body{{font-family:Arial,sans-serif;padding:16px;max-width:800px}}'
        f'details{{margin:4px 0;border:1px solid #ccc;border-radius:4px;padding:4px 8px}}'
        f'summary{{cursor:pointer;font-weight:bold}}'
        f'table{{border-collapse:collapse;width:100%}}'
        f'td,th{{padding:4px 8px;border:1px solid #ccc}}</style></head>'
        f'<body><h2>CLASS Automation Run Log</h2><!-- RUNS -->{block}</body></html>',
        encoding="utf-8",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup old runs
# ─────────────────────────────────────────────────────────────────────────────

def cleanup_old_runs(output_dir: Path, keep_runs: int, dry_run: bool = False) -> int:
    """Delete old NVL_Class_* run folders, keeping the *keep_runs* most-recent.

    Returns number of folders deleted.
    """
    if keep_runs <= 0 or not output_dir.exists():
        return 0

    run_folders = sorted(
        [d for d in output_dir.iterdir()
         if d.is_dir() and re.match(r'^NVL_Class_\d{8}_\d{6}$', d.name)],
        key=lambda d: d.name,
        reverse=True,
    )

    to_delete = run_folders[keep_runs:]
    deleted   = 0
    for d in to_delete:
        _log(f"  {'DRY-RUN: would delete' if dry_run else 'Deleting'}: {d.name}")
        if not dry_run:
            try:
                shutil.rmtree(str(d))
                deleted += 1
            except Exception as e:
                _log(f"    WARNING: {e}")

    return deleted


def _preview_cleanup(output_dir: Path, keep_runs: int) -> list[Path]:
    """Return list of folders that *would* be deleted by cleanup_old_runs()."""
    if keep_runs <= 0 or not output_dir.exists():
        return []
    run_folders = sorted(
        [d for d in output_dir.iterdir()
         if d.is_dir() and re.match(r'^NVL_Class_\d{8}_\d{6}$', d.name)],
        key=lambda d: d.name,
        reverse=True,
    )
    return run_folders[keep_runs:]


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="NVL816-BLLC CLASS automation — pull AQUA, run dashboard, email."
    )
    ap.add_argument("--dry-run",     action="store_true",
                    help="Plan only; no AQUA pull, no dashboard, no email.")
    ap.add_argument("--local-csv",   metavar="PATH",
                    help="Use a local CSV/gz/zip file instead of pulling AQUA.")
    ap.add_argument("--base-dir",    metavar="PATH",
                    help=f"Override output root (default: {_BASE_DIR})")
    ap.add_argument("--aqua-server", choices=["AMR", "GAR"], default="AMR",
                    help="AQUA server to use (default: AMR).")
    ap.add_argument("--days",        type=int, default=_DEFAULT_DAYS, metavar="N",
                    help=f"Look-back days for AQUA pull (default: {_DEFAULT_DAYS}).")
    ap.add_argument("--keep-runs",   type=int, default=-1, metavar="N",
                    help="Keep N most-recent output runs (0=disabled; default: from email_config.json).")
    ap.add_argument("--email",       metavar="ADDR",
                    help="Override email recipient(s).")
    args = ap.parse_args()

    base_dir   = Path(args.base_dir) if args.base_dir else _BASE_DIR
    data_dir   = base_dir / "data"
    output_dir = base_dir / "output"
    run_log    = base_dir / "run_log.html"

    run_ts     = _ts()
    run_date   = run_ts[:8]
    run_dir    = output_dir / f"NVL_Class_{run_ts}"

    _log(f"CLASS Automation — {run_ts}")
    _log(f"  Base : {base_dir}")
    _log(f"  Mode : {'DRY-RUN' if args.dry_run else 'LIVE'}")

    # ── Load email_config.json ─────────────────────────────────────────────
    email_cfg: dict = {}
    if _EMAIL_CFG.exists():
        try:
            email_cfg = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
        except Exception as _e:
            _log(f"  WARNING: could not read email_config.json: {_e}")

    if args.email:
        to = args.email
    elif (email_cfg.get("email_group_enabled")
          and email_cfg.get("email_to_group")):
        _grp = email_cfg["email_to_group"]
        to   = "; ".join(a for a in _grp if a) if isinstance(_grp, list) else str(_grp)
    else:
        to = (email_cfg.get("email_to_report")
              or email_cfg.get("email_to")
              or _EMAIL_TO)

    # ── TP patterns (from config, override, or defaults) ───────────────────
    tp_patterns: list[str] = (
        email_cfg.get("tp_patterns") or _DEFAULT_TP_PATTERNS
    )
    _log(f"  TP patterns: {tp_patterns}")

    # ── Keep-runs setting ──────────────────────────────────────────────────
    keep_runs = args.keep_runs
    if keep_runs < 0:
        keep_runs = int(email_cfg.get("keep_runs", 0))

    # ── Product config ─────────────────────────────────────────────────────
    prod_cfg_path = _find_product_cfg()
    if not prod_cfg_path:
        _log(f"  ERROR: No product config JSON found in {_PROD_CFG_DIR}")
        sys.exit(1)
    prod_cfg = _load_product_cfg(prod_cfg_path)
    _log(f"  Product config: {prod_cfg_path.name}")

    # ── Step 1: Get AQUA CSV ───────────────────────────────────────────────
    aqua_file = ""
    if args.local_csv:
        import glob as _glob
        local_files = sorted(_glob.glob(args.local_csv))
        if not local_files:
            _log(f"  ERROR: No files matching --local-csv {args.local_csv}")
            sys.exit(1)
        aqua_path = Path(local_files[-1])
        aqua_file = str(aqua_path)
        _log(f"  Local CSV: {aqua_path}")
    else:
        aqua_exe = _AQUA_EXE_AMR if args.aqua_server == "AMR" else _AQUA_EXE_GAR
        aqua_path = pull_aqua(aqua_exe, _AQUA_CFG, data_dir, args.dry_run)
        aqua_file = str(aqua_path) if aqua_path else ""
        if not aqua_path and not args.dry_run:
            _log("  ERROR: AQUA pull failed. Aborting.")
            _send_error_email(to, run_ts, "AQUA pull failed", args.dry_run)
            sys.exit(1)

    # ── Step 2: Read & split ───────────────────────────────────────────────
    _log("\nStep 2: Reading and splitting CSV by TestProgram …")
    if args.dry_run and not aqua_path.exists():
        rows, _delim = [], ","
    else:
        rows, _delim = _read_aqua_file(aqua_path)
        _log(f"  Total rows: {len(rows):,}")

    groups = split_by_tp_patterns(rows, tp_patterns)

    if not groups:
        _log("  No matching TestPrograms found. Sending no-data email.")
        _send_no_data_email(to, run_ts, tp_patterns, args.dry_run)
        sys.exit(0)

    _log(f"  Matched TPs: {sorted(groups.keys())}")

    # ── Step 3: Per-TP pipeline ────────────────────────────────────────────
    _log("\nStep 3: Running class dashboard per TP …")
    # pathlib.mkdir(parents=True) has a known bug on Windows UNC paths where it
    # raises FileExistsError for intermediate dirs that already exist.  Use
    # os.makedirs as a robust workaround.
    import os as _os
    _os.makedirs(str(run_dir), exist_ok=True)

    tp_results: list[dict] = []
    merge_data_dir = data_dir / "merge-data"

    for tp_name, (tp_rows, fieldnames) in sorted(groups.items()):
        _log(f"\n  ── {tp_name} ({len(tp_rows):,} rows) ──")

        # 3a: write per-TP CSV (split data)
        gz_path = update_tp_gz(tp_name, tp_rows, fieldnames, data_dir, args.dry_run)

        # 3a-merge: find merge-data files for this TP
        _log(f"  Checking merge-data dir: {merge_data_dir}")
        merge_files = _merge_data_files_for_tp(merge_data_dir, tp_name)
        if merge_files:
            _log(f"  {tp_name}: merging with {[f.name for f in merge_files]}")

        # 3a-merge-copy: copy merge-data source file(s) into the per-TP output folder
        if merge_files and not args.dry_run:
            safe_tp    = re.sub(r'[\\/:*?"<>|]', '_', tp_name).strip() or "output"
            tp_out_dir = run_dir / safe_tp
            _os.makedirs(str(tp_out_dir), exist_ok=True)
            for _mf in merge_files:
                _dest = tp_out_dir / _mf.name
                try:
                    shutil.copy2(str(_mf), str(_dest))
                    _log(f"  Copied merge-data source → {_dest.relative_to(run_dir)}")
                except Exception as _ce:
                    _log(f"  WARNING: could not copy {_mf.name} to output: {_ce}")

        # 3b: run dashboard — pass gz + any merge-data files as separate inputs
        html_path = run_class_pipeline([gz_path] + merge_files, run_dir, tp_name, args.dry_run)
        ok = (html_path is not None and (args.dry_run or html_path.exists()))

        # 3c: read summary from dashboard-generated JSON (avoids re-calculating)
        summary: dict = {}
        if args.dry_run:
            summary = {"_total_dies": len(tp_rows)}
        elif html_path:
            _json_p = Path(html_path).with_name(Path(html_path).stem + '.summary.json')
            if _json_p.exists():
                try:
                    with open(_json_p, encoding='utf-8') as _jf:
                        summary = _summary_from_json(json.load(_jf))
                    _log(f"  {tp_name}: summary loaded from dashboard JSON")
                except Exception as _je:
                    _log(f"  WARNING: could not read summary JSON ({_je}); falling back to extract")
            if not summary:
                try:
                    summary = extract_freq_summary(tp_rows, prod_cfg)
                except Exception as e:
                    _log(f"  WARNING: freq summary failed: {e}")
                    summary = {"_total_dies": len(tp_rows)}

        # 3d: persist / restore "last good summary" next to the gz file
        # If this run produced freq data (vmin columns present), cache it for future AQUA-only runs.
        # If this run has no freq data (AQUA pull without vmin), load the cached summary instead.
        _cache_p = data_dir / "programs" / f"{_safe_filename(tp_name)}.last_summary.json"
        _has_modules = any(k in summary for k in ("CORE", "ATOM", "CCF"))
        if _has_modules and not args.dry_run:
            try:
                import json as _json
                _cache_p.write_text(_json.dumps(summary, default=list), encoding="utf-8")
            except Exception:
                pass  # cache write failure is non-fatal
        elif not _has_modules and _cache_p.exists():
            try:
                import json as _json
                _cached = _json.loads(_cache_p.read_text(encoding="utf-8"))
                # Merge cached module data with current die counts
                _cached["_total_dies"] = summary.get("_total_dies", _cached.get("_total_dies", 0))
                _cached["_bin1_dies"]  = summary.get("_bin1_dies",  _cached.get("_bin1_dies",  0))
                summary = _cached
                _log(f"  {tp_name}: using cached freq summary (AQUA pull lacks vmin columns)")
            except Exception:
                pass  # cache read failure → keep empty summary

        tp_results.append({
            "tp_name":        tp_name,
            "ok":             ok,
            "html_path":      html_path,
            "html_full_path": None,   # filled in after full-data run below
            "summary":        summary,
        })

        _log(f"  {tp_name}: {'OK' if ok else 'FAILED'} "
             f"({summary.get('_total_dies', 0):,} dies)")

    # ── Step 3-Full: Combined full-data dashboard (all per-TP gz + merge-data) ──
    _log("\nStep 3-Full: Building combined full-data dashboard …")
    _fd_gz_files  = sorted((data_dir / "programs").glob("*.csv.gz"))
    _fd_merge     = sorted(merge_data_dir.iterdir()) if merge_data_dir.exists() else []
    _fd_merge     = [p for p in _fd_merge
                     if p.suffix.lower() in ('.csv', '.zip', '.gz')
                     or p.name.lower().endswith('.csv.gz')]
    _fd_inputs    = _fd_gz_files + _fd_merge
    html_full_path: Path | None = None
    if _fd_inputs:
        _log(f"  {len(_fd_gz_files)} program file(s) + {len(_fd_merge)} merge-data file(s)")
        html_full_path = run_class_pipeline(_fd_inputs, run_dir, "full_data", args.dry_run)
    else:
        _log("  No files found for full-data run, skipping.")
    for r in tp_results:
        r["html_full_path"] = html_full_path

    # ── Update run log ─────────────────────────────────────────────────────
    if not args.dry_run:
        try:
            _append_run_log(run_log, run_ts, tp_results)
        except Exception as _rlog_err:
            _log(f"  WARNING: could not update run_log.html: {_rlog_err}")
        save_run_record(run_dir, run_ts, aqua_file, tp_results)

    # ── Optional cleanup ───────────────────────────────────────────────────
    if keep_runs > 0:
        _log(f"\nCleanup: keeping last {keep_runs} run folders …")
        n_deleted = cleanup_old_runs(output_dir, keep_runs, args.dry_run)
        _log(f"  Deleted {n_deleted} old run folder(s).")

    # ── Step 4: Email ──────────────────────────────────────────────────────
    _log("\nStep 4: Building and sending email …")
    if not args.dry_run:
        run_records = load_run_history(output_dir)
    if args.dry_run or not run_records:
        run_records = [{"run_ts": run_ts, "aqua_file": aqua_file, "tp_results": tp_results}]
    body = build_class_email_body(
        run_records, run_log,
        exclude_patterns=email_cfg.get("email_exclude_patterns") or [],
    )

    ok_tps = [r for r in tp_results if r["ok"]]
    n_ok   = len(ok_tps)
    n_tot  = len(tp_results)
    date_fmt = f"{run_ts[:4]}-{run_ts[4:6]}-{run_ts[6:8]}"
    subject  = f"NVL816-BLLC CLASS Report — {date_fmt} ({n_ok}/{n_tot} TPs)"

    send_email(to, subject, body, args.dry_run)

    # Save a persistent copy to reports/
    if not args.dry_run:
        _reports_dir = base_dir / "reports"
        _reports_dir.mkdir(parents=True, exist_ok=True)
        _ts_label = datetime.now().strftime("%Y%m%d_%H%M%S")
        _report_save = _reports_dir / f"Class_Report_{_ts_label}.html"
        _report_save.write_text(body, encoding="utf-8")
        _log(f"Report saved: {_report_save}")

    _log(f"\nDone — {n_ok}/{n_tot} TPs OK")


def _send_error_email(to: str, run_ts: str, reason: str, dry_run: bool) -> None:
    body = (
        f"<html><body><h3 style='color:#c0392b'>CLASS Automation Error</h3>"
        f"<p><b>{reason}</b></p>"
        f"<p>Run: {run_ts}</p></body></html>"
    )
    send_email(to, f"NVL816-BLLC CLASS Automation ERROR — {run_ts}", body, dry_run)


def _send_no_data_email(
    to: str, run_ts: str, patterns: list[str], dry_run: bool
) -> None:
    pats = ", ".join(patterns) if patterns else "(no patterns configured)"
    body = (
        f"<html><body><h3 style='color:#e67e22'>CLASS Automation — No Matching TPs</h3>"
        f"<p>No test programs matching <code>{pats}</code> were found in today's AQUA pull.</p>"
        f"<p>Run: {run_ts}</p></body></html>"
    )
    send_email(to, f"NVL816-BLLC CLASS — No New Data {run_ts}", body, dry_run)


if __name__ == "__main__":
    main()
