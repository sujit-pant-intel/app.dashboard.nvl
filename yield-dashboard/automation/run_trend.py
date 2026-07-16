"""
run_trend.py
============
Yield Trend Chart automation for NVL816-BLLC.

Workflow
--------
1. Pull AQUA data using NVL_Yield-Trend - AutoPull.txt  (own pull, independent
   of the yield dashboard pull — different report config / column set).
   Or skip the pull with --local-csv to supply an existing file.
2. Run  trend_chart.py <aqua_csv> --out <trend_report.html>
3. Send email with the trend HTML as an attachment

This script is meant to be run separately from run_automation.py,
e.g. daily or weekly from Task Scheduler.

Usage
-----
  python run_trend.py                                    # full AQUA pull + trend
  python run_trend.py --dry-run
  python run_trend.py --local-csv "C:\\data\\pull.csv"  # skip AQUA, use local file
  python run_trend.py --base-dir "\\\\server\\auto\\yield-trend"
  python run_trend.py --interval weekly     # daily/weekly/monthly (default: weekly)
  python run_trend.py --email user@intel.com
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
from datetime import datetime
from pathlib import Path

# ── UTF-8 output on Windows ────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Paths ───────────────────────────────────────────────────────────────────────
_HERE      = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent          # app.dashboard.nvl/
_TREND_SCRIPT = _REPO_ROOT / "yield-dashboard" / "yld" / "src" / "trend_chart.py"
_PROD_CFG_DIR = _REPO_ROOT / "shared" / "setup" / "config" / "yield-dashboard"
_EMAIL_CFG    = _REPO_ROOT / "shared" / "setup" / "automation" / "trend-dashboard" / "trend_setup_config.json"
_7Z_EXE       = Path(r"C:\Program Files\7-Zip\7z.exe")

# Trend-specific AQUA config (different from yield dashboard pull)
_AQUA_CFG  = _REPO_ROOT / "shared" / "setup" / "automation" / "trend-dashboard" / "NVL_Yield-Trend - AutoPull.txt"
_AQUA_EXE_GAR = r"\\gar.corp.intel.com\ec\proj\ba\aqua\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"
_AQUA_EXE_AMR = r"\\FMSAPP3301.amr.corp.intel.com\Installer\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"

_BASE_DIR  = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\trend")
_EMAIL_TO  = "sujit.n.pant@intel.com"


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ─────────────────────────────────────────────────────────────────────────────
# AQUA pull  (same pattern as run_automation.py)
# ─────────────────────────────────────────────────────────────────────────────

def _aqua_report_name(config_path: Path) -> str:
    try:
        for line in config_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if line.strip().startswith("@ Report :"):
                return line.strip().split(":", 1)[1].strip()
    except Exception:
        pass
    return "NVL_Yield_Trend"


def pull_aqua(aqua_exe: str, report_config: Path, data_dir: Path, dry_run: bool) -> Path | None:
    """Run AquaCmdLine.exe with the trend report config. Returns path to downloaded file."""
    data_dir.mkdir(parents=True, exist_ok=True)
    ts       = _ts()
    out_base = data_dir / f"trend_{ts}"
    out_req  = out_base.with_suffix(".zip")

    report_name = _aqua_report_name(report_config)
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
    _log(f"  Config : {report_config.name}")
    _log(f"  CMD    : {' '.join(cmd)}")

    if dry_run:
        _log("  DRY-RUN: skipping AQUA pull")
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
        dest = data_dir / f"trend_{ts}.csv"
        shutil.copy2(src, dest)
        _log(f"  Fallback from %TEMP%: {src.name} → {dest.name}")
        return dest

    _log("  ERROR: AQUA produced no output file")
    return None


def _normalise_aqua_file(raw_path: Path, tmp_dir: Path) -> Path:
    """
    If AQUA returned a zip/csv/gz, extract and return a plain .csv path.
    If it's already a .csv, return as-is.
    """
    suffix = raw_path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(raw_path) as zf:
            csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csvs:
                raise ValueError(f"No CSV inside {raw_path.name}")
            out = tmp_dir / csvs[0]
            zf.extract(csvs[0], tmp_dir)
            _log(f"  Extracted: {out.name}  ({out.stat().st_size:,} bytes)")
            return out
    if suffix == ".gz":
        # AQUA may output a gzip-compressed CSV (e.g. trend_YYYYMMDD_HHMMSS.csv.gz)
        inner_name = raw_path.stem  # drop .gz → e.g. trend_....csv
        if not inner_name.lower().endswith(".csv"):
            inner_name += ".csv"
        out = tmp_dir / inner_name
        with gzip.open(raw_path, "rb") as gz_in, open(out, "wb") as csv_out:
            csv_out.write(gz_in.read())
        _log(f"  Extracted gz: {out.name}  ({out.stat().st_size:,} bytes)")
        return out
    # plain .csv or .CSV
    return raw_path


# ─────────────────────────────────────────────────────────────────────────────
# Product config
# ─────────────────────────────────────────────────────────────────────────────

def _find_product_config() -> str:
    candidates = sorted(_PROD_CFG_DIR.glob("*.json"))
    for c in candidates:
        if "BB+AIO" in c.name and "L0" in c.name:
            return str(c)
    return str(candidates[0]) if candidates else ""


def _find_product_config_for(prefix: str) -> str:
    """Return the product config JSON path matching the given devrevstep prefix."""
    candidates = sorted(_PROD_CFG_DIR.glob("*.json"))
    key = prefix.upper()
    for c in candidates:
        if c.name.upper().startswith(key):
            return str(c)
    # Fallback: same logic as original _find_product_config
    return _find_product_config()


# ─────────────────────────────────────────────────────────────────────────────
# Split CSV by devrevstep  (mirrors manage_trend.py logic)
# ─────────────────────────────────────────────────────────────────────────────

# Maps devrevstep prefix → output filename stem
_DEVREVSTEP_SPLITS = {
    "8PF6CV": "NVL816-Yield-Trend-Report",
    "8PF5CV": "NVL816-BLLC-Yield-Trend-Report",
}


def _split_csv_by_devrevstep(src_csv: Path, out_dir: Path, ts: str) -> dict:
    """Split src_csv by devrevstep prefix. Returns {prefix: Path} for each
    product that has rows. Only prefixes in _DEVREVSTEP_SPLITS are kept."""
    import csv as _csv

    out_dir.mkdir(parents=True, exist_ok=True)
    writers: dict = {}
    handles: dict = {}
    out_paths: dict = {}
    try:
        with open(src_csv, newline="", encoding="utf-8", errors="replace") as fh:
            reader = _csv.DictReader(fh)
            fieldnames = reader.fieldnames or []
            for row in reader:
                drs = next(
                    (v for k, v in row.items()
                     if k.strip().lower().startswith("devrevstep") and v),
                    "",
                )
                prefix = drs.strip()[:6].upper()
                if prefix not in _DEVREVSTEP_SPLITS:
                    continue
                if prefix not in writers:
                    fname = f"{_DEVREVSTEP_SPLITS[prefix]}-{ts}.csv"
                    p = out_dir / fname
                    out_paths[prefix] = p
                    handles[prefix] = open(p, "w", newline="", encoding="utf-8")
                    writers[prefix] = _csv.DictWriter(handles[prefix], fieldnames=fieldnames)
                    writers[prefix].writeheader()
                writers[prefix].writerow(row)
    finally:
        for h in handles.values():
            h.close()
    return out_paths


# ─────────────────────────────────────────────────────────────────────────────
# Run trend_chart.py
# ─────────────────────────────────────────────────────────────────────────────

def run_trend_chart(csv_path: Path, out_html: Path, interval: str,
                    cfg_path: str, dry_run: bool) -> bool:
    cmd = [
        sys.executable, str(_TREND_SCRIPT),
        str(csv_path),
        "--interval", interval,
        "--out", str(out_html),
    ]
    if cfg_path:
        cmd += ["--cfg", cfg_path]

    _log(f"  CMD: {' '.join(cmd)}")
    if dry_run:
        _log("  DRY-RUN: would run trend_chart.py")
        return True

    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    result = subprocess.run(cmd, capture_output=False, text=True, timeout=600,
                            env=env, cwd=str(_TREND_SCRIPT.parent))
    if result.returncode != 0:
        _log(f"  WARNING: trend_chart.py exited with rc={result.returncode}")
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Email helpers
# ─────────────────────────────────────────────────────────────────────────────

def _send_via_outlook(to: str, subject: str, body_html: str, attachments: list[str]) -> None:
    import win32com.client as _win
    outlook = _win.Dispatch("Outlook.Application")
    mail    = outlook.CreateItem(0)
    mail.To = to
    mail.Subject = subject
    mail.HTMLBody = body_html
    for att in attachments:
        mail.Attachments.Add(att)
    mail.Send()
    _log("  Email sent via Outlook COM.")


_SMTP_SERVER = "smtpauth.intel.com"
_SMTP_PORT   = 587
_SMTP_FROM   = "sujit.n.pant@intel.com"


def _send_via_smtp(to: str, subject: str, body_html: str, attachments: list[str]) -> None:
    import smtplib
    import time
    import os
    import socket
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = _SMTP_FROM
    msg["To"]      = to
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    for att_path in attachments:
        with open(att_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=Path(att_path).name)
        part["Content-Disposition"] = f'attachment; filename="{Path(att_path).name}"'
        msg.attach(part)

    recipients = [a.strip() for a in to.split(";")]
    msg_str = msg.as_string()
    proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or "http://proxy-dmz.intel.com:912"

    max_retries = 3
    base_delay = 2
    for attempt in range(1, max_retries + 1):
        try:
            _log(f"  SMTP attempt {attempt}/{max_retries}...")
            try:
                with smtplib.SMTP(_SMTP_SERVER, _SMTP_PORT, timeout=60) as s:
                    s.starttls()
                    s.sendmail(_SMTP_FROM, recipients, msg_str)
                _log(f"  Email sent via SMTP ({_SMTP_SERVER}) — direct.")
                return
            except (smtplib.SMTPException, OSError, TimeoutError) as direct_err:
                _log(f"  Direct SMTP failed ({direct_err}), trying via proxy…")
                try:
                    import socks
                    proxy_addr = proxy[7:] if proxy.startswith("http://") else proxy
                    proxy_host, proxy_port_str = proxy_addr.rsplit(":", 1)
                    sock = socks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.set_proxy(socks.HTTP, proxy_host, int(proxy_port_str))
                    sock.connect((_SMTP_SERVER, _SMTP_PORT))
                    with smtplib.SMTP(sock=sock, timeout=60) as s:
                        s.starttls()
                        s.sendmail(_SMTP_FROM, recipients, msg_str)
                    _log(f"  Email sent via SMTP ({_SMTP_SERVER}) — via proxy.")
                    return
                except ImportError:
                    raise direct_err
        except (smtplib.SMTPException, OSError, TimeoutError) as e:
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                _log(f"  SMTP attempt {attempt} failed: {e}")
                _log(f"  Retrying in {delay}s…")
                time.sleep(delay)
            else:
                _log(f"  SMTP all {max_retries} attempts failed: {e}")
                raise


def send_email(to: str, subject: str, body_html: str,
               dry_run: bool, attachments: list[str] | None = None) -> None:
    _log(f"{'DRY-RUN: ' if dry_run else ''}Sending email → {to}")
    if dry_run:
        _log(f"  Subject: {subject}")
        for a in (attachments or []):
            _log(f"  Attach : {a}")
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
        _log(f"  ERROR sending email: {e}")


def _build_email_body(run_ts: str, reports: list, interval: str) -> str:
    """Build HTML email body with links to reports on the share (no attachments)."""
    rows = ""
    for html_path, ok in reports:
        status = "OK" if ok else "FAILED"
        color  = "#1f7a3f" if ok else "#c0392b"
        # html_path is already UNC (base_dir resolved via _resolve_unc in main)
        href = html_path.as_uri()  # file:////server/share/... for UNC, matches run_automation.py
        unc  = str(html_path)
        rows += (
            f'<tr><td style="color:{color};font-weight:bold">{status}</td>'
            f'<td style="font-family:monospace;font-size:12px">{html_path.name}</td>'
            f'<td style="font-size:12px"><a href="{href}">{unc}</a></td></tr>\n'
        )
    return f"""<!DOCTYPE html><html><body style="font-family:sans-serif">
<h2 style="color:#1a5276">NVL816 Yield Trend Reports</h2>
<p>Generated: <strong>{run_ts}</strong> &nbsp;|&nbsp; Interval: <strong>{interval}</strong></p>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<thead><tr style="background:#2c3e50;color:#fff"><th>Status</th><th>File</th><th>Path (open in Edge)</th></tr></thead>
<tbody>{rows}</tbody></table>
<p style="font-size:12px;color:#555">Open links in <strong>Microsoft Edge</strong> with VPN connected.</p>
<hr/>
<p style="font-size:0.85em;color:#888">Pant, Sujit N — GEMS FTE &nbsp;|&nbsp; auto-generated by run_trend.py</p>
</body></html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_unc(p: Path) -> Path:
    """If p is a mapped drive (e.g. Y:\\...) and _BASE_DIR shares the same
    relative suffix, swap the drive root for the _BASE_DIR UNC root so all
    downstream paths (links, index.html) are UNC from the start."""
    s = str(p)
    if not (len(s) >= 2 and s[1] == ":" and s[0].isalpha()):
        return p  # already UNC or relative
    # Try net use to resolve drive letter → UNC root
    try:
        import subprocess as _sp
        r = _sp.run(["net", "use", s[0].upper() + ":"],
                    capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            # "Remote name       \\server\share"
            if "remote name" in line.lower():
                unc_root = line.split(None, 2)[-1].strip().rstrip("\\")
                if unc_root.startswith("\\\\"):
                    return Path(unc_root + s[2:])
    except Exception:
        pass
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="NVL816-BLLC Yield Trend automation")
    ap.add_argument("--base-dir",      default=str(_BASE_DIR),
                    help="Automation base directory (default: network share)")
    ap.add_argument("--aqua-exe",      default=_AQUA_EXE_AMR,
                    help="Path to AquaCmdLine.exe")
    ap.add_argument("--report-config", default=str(_AQUA_CFG),
                    help="AQUA report config txt (default: NVL_Yield-Trend - AutoPull.txt)")
    ap.add_argument("--local-csv",     default="",
                    help="Skip AQUA pull; use this existing CSV/zip file directly")
    ap.add_argument("--interval",      default="weekly",
                    choices=["daily", "weekly", "bi-weekly", "monthly"],
                    help="Trend grouping interval (default: weekly)")
    ap.add_argument("--email",         default="",
                    help="Override recipient email address")
    ap.add_argument("--dry-run",       action="store_true",
                    help="Plan only — do not pull AQUA, run trend_chart, or send email")
    args = ap.parse_args()

    base_dir = _resolve_unc(Path(args.base_dir))
    data_dir = base_dir / "data"
    trend_dir = base_dir / "reports"
    run_ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ts_label = _ts()

    _log("=" * 65)
    _log(f"run_trend  {'[DRY-RUN]' if args.dry_run else '[LIVE]'}")
    _log(f"Base dir : {base_dir}")
    _log(f"Interval : {args.interval}")
    _log("=" * 65)

    tmp_dir = Path(tempfile.mkdtemp(prefix="nvl_trend_"))
    try:
        # ── 1. Get AQUA data ────────────────────────────────────────────────
        if args.local_csv:
            raw_path = Path(args.local_csv)
            _log(f"\nUsing local file: {raw_path}")
        else:
            _log(f"\nPulling AQUA data…")
            if not args.dry_run:
                data_dir.mkdir(parents=True, exist_ok=True)
            raw_path = pull_aqua(
                args.aqua_exe,
                Path(args.report_config),
                data_dir,
                args.dry_run,
            )
            if not raw_path and not args.dry_run:
                _log("AQUA pull failed — aborting.")
                sys.exit(1)

        # ── 2. Normalise (unzip if needed) ──────────────────────────────────
        if raw_path and not args.dry_run:
            csv_path = _normalise_aqua_file(raw_path, tmp_dir)
            _log(f"\nInput CSV : {csv_path.name}  ({csv_path.stat().st_size:,} bytes)")
        else:
            csv_path = raw_path  # dry-run: path is fictitious, that's fine

        # ── 2b. Split CSV by devrevstep ─────────────────────────────
        _log("\nSplitting CSV by devrevstep (" + ", ".join(_DEVREVSTEP_SPLITS) + ")...")
        if not args.dry_run:
            split_map = _split_csv_by_devrevstep(csv_path, data_dir, ts_label)
            if not split_map:
                _log("ERROR: No matching devrevstep rows found — aborting.")
                sys.exit(1)
            for _pfx, _sp in split_map.items():
                _log(f"  {_pfx} -> {_sp.name}  ({_sp.stat().st_size:,} bytes)")
            try:
                csv_path.unlink(missing_ok=True)
            except Exception:
                pass
        else:
            split_map = {pfx: data_dir / (stem + "-" + ts_label + ".csv")
                         for pfx, stem in _DEVREVSTEP_SPLITS.items()}

        # ── 3. Run trend_chart.py once per product ───────────────────
        if not args.dry_run:
            trend_dir.mkdir(parents=True, exist_ok=True)

        generated = []
        for _pfx, _csv_file in split_map.items():
            _out_html = trend_dir / (_csv_file.stem + ".html")
            _cfg = _find_product_config_for(_pfx)
            if _cfg:
                _log("[" + _pfx + "] Product config: " + Path(_cfg).name)
            _log("[" + _pfx + "] Running trend_chart.py -> " + _out_html.name)
            _ok = run_trend_chart(_csv_file, _out_html, args.interval, _cfg, args.dry_run)
            generated.append((_out_html, _ok))
            _log("  " + ("OK" if _ok else "FAIL") + " " + _pfx)

        # ── 3b. Compress split input CSVs ────────────────────────────
        if not args.dry_run:
            for _pfx, _csv_file in split_map.items():
                if not _csv_file.exists():
                    continue
                try:
                    if _7Z_EXE.exists():
                        subprocess.run(
                            [str(_7Z_EXE), "a", str(_csv_file.with_suffix(".7z")), str(_csv_file)],
                            capture_output=True, check=False,
                        )
                        _csv_file.unlink(missing_ok=True)
                        _log("  Compressed: " + _csv_file.stem + ".7z")
                    else:
                        import zipfile as _zf
                        zpath = _csv_file.with_suffix(".zip")
                        with _zf.ZipFile(zpath, "w", _zf.ZIP_DEFLATED) as _z:
                            _z.write(_csv_file, _csv_file.name)
                        _csv_file.unlink(missing_ok=True)
                        _log("  Compressed: " + _csv_file.stem + ".zip")
                except Exception as _cx:
                    _log("  WARNING: compression failed for " + _csv_file.name + ": " + str(_cx))

        # ── 3c. Regenerate index.html ─────────────────────────────────
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("_gi", _HERE / "generate_index.py")
            _gi   = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_gi)
            _gi.build_index(base_dir)
            _log("  Index updated -> " + str(trend_dir / "index.html"))
        except Exception as _idx_e:
            _log("  WARNING: index update failed: " + str(_idx_e))

        # ── 4. Send combined email ───────────────────────────────────
        email_cfg: dict = {}
        if _EMAIL_CFG.exists():
            try:
                email_cfg = json.loads(_EMAIL_CFG.read_text(encoding="utf-8"))
            except Exception:
                pass
        email_to = (args.email
                    or email_cfg.get("email_to_report")
                    or email_cfg.get("email_to")
                    or _EMAIL_TO)

        att_tmp     = Path(tempfile.mkdtemp(prefix="nvl_att_"))
        attachments = []
        for _out_html, _ok in generated:
            if _ok and not args.dry_run and _out_html.exists():
                _att = att_tmp / _out_html.name
                shutil.copy2(str(_out_html), str(_att))
                attachments.append(str(_att))

        n_ok    = sum(1 for _, ok in generated if ok)
        n_fail  = len(generated) - n_ok
        subject = "NVL816 Yield Trend Reports " + ts_label + " (" + str(n_ok) + " chart(s))"
        body     = _build_email_body(run_ts, generated, args.interval)

        _log("\nRecipient: " + email_to)
        # No attachments — HTML files are large (10-15 MB each); links in email body instead
        send_email(to=email_to, subject=subject, body_html=body,
                   dry_run=args.dry_run, attachments=[])

        _log("\n" + "=" * 65)
        for _out_html, _ok in generated:
            _log("  " + ("OK" if _ok else "FAIL") + " " + _out_html.name)
        if n_fail:
            _log("  " + str(n_fail) + " chart(s) failed.")
        _log("=" * 65)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
