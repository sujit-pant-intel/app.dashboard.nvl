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
import datetime as _dt
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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
# Index generator  (inlined from generate_index.py)
# ─────────────────────────────────────────────────────────────────────────────

_UNC_REPORTS = r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\trend\reports"


def _fmt_size(n: int) -> str:
    if n < 1024:      return f"{n} B"
    if n < 1024**2:   return f"{n/1024:.0f} KB"
    return f"{n/1024**2:.1f} MB"


def build_index(base_dir: Path) -> Path:
    """Scan reports/ and write a static index.html with BLLC / NVL816 tabs. Returns the file path."""
    reports_dir = base_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    _base_str = str(base_dir)
    if _base_str.startswith("\\\\"):
        unc_reports = str(base_dir / "reports").replace("/", "\\")
    else:
        unc_reports = _UNC_REPORTS
        try:
            _r = subprocess.run(["net", "use", _base_str[:2].upper()],
                                capture_output=True, text=True, timeout=5)
            for _line in _r.stdout.splitlines():
                if "remote name" in _line.lower():
                    unc_root = _line.split(None, 2)[-1].strip().rstrip("\\")
                    if unc_root.startswith("\\\\"):
                        unc_reports = unc_root + _base_str[2:].replace("/", "\\") + "\\reports"
                    break
        except Exception:
            pass

    try:
        _names = [n for n in os.listdir(str(reports_dir))
                  if n.startswith("NVL816") and n.endswith(".html") and not n.startswith("index")]
    except OSError:
        _names = []
    all_files = sorted(
        [reports_dir / n for n in _names],
        key=lambda f: f.name,
        reverse=True,
    )
    bllc_files  = [f for f in all_files if "BLLC" in f.name]
    other_files = [f for f in all_files if "BLLC" not in f.name]
    now_str = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _rows(files):
        if not files:
            return '<tr><td colspan="4" class="dim" style="padding:20px">No reports found.</td></tr>'
        out = ""
        for i, f in enumerate(files):
            m = re.search(r"(\d{8})_(\d{6})", f.name)
            ts = ""
            if m:
                d, t = m.group(1), m.group(2)
                ts = f"{d[:4]}-{d[4:6]}-{d[6:]}  {t[:2]}:{t[2:4]}:{t[4:]}"
            try:
                st    = f.stat()
                sz    = _fmt_size(st.st_size)
                mtime = _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                sz    = "\u2013"
                mtime = "\u2013"
            href  = "file:////" + unc_reports.replace("\\", "/").lstrip("/") + "/" + f.name
            badge = '<span class="badge">latest</span>' if i == 0 else ""
            out += (f'\n      <tr data-n="{f.name}">'
                    f'<td class="mono"><a href="{href}" target="_blank">{f.name}</a> {badge}</td>'
                    f'<td class="dim">{ts}</td>'
                    f'<td class="dim mono">{sz}</td>'
                    f'<td class="dim">{mtime}</td></tr>')
        return out

    bllc_rows  = _rows(bllc_files)
    other_rows = _rows(other_files)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>NVL816 Yield Trend Reports</title>
  <style>
    :root{{--bg:#1a252f;--bg2:#1e2e3d;--bg3:#263950;--fg:#e8f0f7;
          --dim:#90a4ae;--acc:#4fc3f7;--grn:#66bb6a;
          --font:"Segoe UI",sans-serif;--mono:"Courier New",monospace}}
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:var(--bg);color:var(--fg);font-family:var(--font);font-size:14px;padding:24px}}
    h1{{color:var(--acc);font-size:22px;margin-bottom:4px}}
    .sub{{color:var(--dim);font-size:12px;margin-bottom:20px}}
    .card{{background:var(--bg2);border-radius:8px;padding:16px 20px;margin-bottom:20px}}
    table{{width:100%;border-collapse:collapse}}
    thead th{{background:var(--bg3);color:var(--acc);font-size:12px;text-align:left;padding:8px 10px}}
    tbody tr{{border-bottom:1px solid var(--bg3)}}
    tbody tr:hover{{background:#22384d}}
    td{{padding:8px 10px;font-size:13px}}
    a{{color:var(--acc);text-decoration:none}}  a:hover{{text-decoration:underline}}
    .badge{{background:#1b3a1b;color:var(--grn);padding:2px 8px;border-radius:10px;
            font-size:11px;font-family:var(--mono)}}
    .dim{{color:var(--dim)}}  .mono{{font-family:var(--mono);font-size:12px}}
    .sb{{display:flex;gap:10px;margin-bottom:14px;align-items:center}}
    input{{flex:1;background:var(--bg3);border:none;color:var(--fg);
           font-family:var(--mono);font-size:13px;padding:6px 10px;border-radius:4px;outline:none}}
    .cnt{{color:var(--dim);font-size:12px}}
    .tabs{{display:flex;gap:4px;margin-bottom:0;border-bottom:2px solid var(--bg3)}}
    .tab-btn{{background:var(--bg3);color:var(--dim);border:none;padding:9px 22px;
              font-family:var(--font);font-size:13px;cursor:pointer;border-radius:6px 6px 0 0}}
    .tab-btn.active{{background:var(--bg2);color:var(--acc);font-weight:600}}
    .tab-panel{{display:none}}.tab-panel.active{{display:block}}
  </style>
</head>
<body>
  <h1>NVL816 Yield Trend Reports</h1>
  <p class="sub">{len(all_files)} report(s) &nbsp;·&nbsp; Updated: {now_str}</p>

  <div class="card">
    <p style="color:var(--dim);font-size:13px;line-height:1.8">
      Click any link to open the report directly from the shared drive.<br>
      <strong style="color:#ffa726">Requires:</strong> Microsoft Edge &amp; Intel network / VPN.<br>
      If the link does not open, paste the path below into Windows Explorer:
    </p>
    <ul style="color:var(--dim);font-size:13px;line-height:2;margin:8px 0 4px 20px">
      <li><strong style="color:var(--fg)">No permission</strong> — request access from <code style="color:var(--acc)">snpant</code> or IT admin.</li>
      <li><strong style="color:var(--fg)">Not on network</strong> — connect to Intel VPN first.</li>
      <li><strong style="color:var(--fg)">Wrong browser</strong> — use <strong>Microsoft Edge</strong>.</li>
    </ul>
    <code style="color:var(--acc);font-size:11px">{unc_reports}</code>
  </div>

  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('bllc',this)">
      NVL816-BLLC &nbsp;<span class="cnt">({len(bllc_files)})</span>
    </button>
    <button class="tab-btn" onclick="switchTab('other',this)">
      NVL816 &nbsp;<span class="cnt">({len(other_files)})</span>
    </button>
  </div>

  <!-- BLLC tab -->
  <div id="tab-bllc" class="tab-panel card active" style="border-radius:0 8px 8px 8px">
    <div class="sb">
      <input id="q-bllc" type="text" placeholder="Filter…" oninput="flt('bllc')">
      <span id="cnt-bllc" class="cnt">{len(bllc_files)} report(s)</span>
    </div>
    <table>
      <thead><tr><th>Report</th><th>Run Time</th><th>Size</th><th>Modified</th></tr></thead>
      <tbody id="tb-bllc">{bllc_rows}</tbody>
    </table>
  </div>

  <!-- NVL816 tab -->
  <div id="tab-other" class="tab-panel card" style="border-radius:0 8px 8px 8px">
    <div class="sb">
      <input id="q-other" type="text" placeholder="Filter…" oninput="flt('other')">
      <span id="cnt-other" class="cnt">{len(other_files)} report(s)</span>
    </div>
    <table>
      <thead><tr><th>Report</th><th>Run Time</th><th>Size</th><th>Modified</th></tr></thead>
      <tbody id="tb-other">{other_rows}</tbody>
    </table>
  </div>

  <script>
    function switchTab(id, btn) {{
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById('tab-' + id).classList.add('active');
      btn.classList.add('active');
    }}
    function flt(id) {{
      const q = document.getElementById('q-' + id).value.toLowerCase();
      const rows = document.querySelectorAll('#tb-' + id + ' tr');
      let n = 0;
      rows.forEach(r => {{ const s = r.dataset.n.toLowerCase().includes(q); r.style.display = s ? '' : 'none'; if(s) n++; }});
      document.getElementById('cnt-' + id).textContent = n + ' report(s)';
    }}
  </script>
</body>
</html>"""

    out = reports_dir / "index.html"
    _wrote = False
    for _attempt in range(3):
        if _attempt == 1:
            _unc = str(reports_dir)
            _parts = _unc.lstrip("\\").split("\\")
            if len(_parts) >= 2:
                _share = "\\\\" + _parts[0] + "\\" + _parts[1]
                subprocess.run(["net", "use", _share, "/delete"], capture_output=True, timeout=5)
                subprocess.run(["net", "use", _share, "/persistent:no"], capture_output=True, timeout=5)
        try:
            _tmp = out.with_suffix(".tmp")
            _tmp.write_text(html, encoding="utf-8")
            os.replace(str(_tmp), str(out))
            subprocess.run(["icacls", str(out), "/grant", "Everyone:(W)"],
                           capture_output=True, timeout=5)
            _wrote = True
            break
        except (PermissionError, OSError):
            if _attempt < 2:
                time.sleep(1)
    if not _wrote:
        out = reports_dir / "index_latest.html"
        out.write_text(html, encoding="utf-8")
    return out


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

    unc_base = _resolve_unc(Path(args.base_dir))   # always UNC — used for both I/O and link construction
    base_dir = unc_base
    data_dir = base_dir / "data"
    trend_dir = base_dir / "reports"
    unc_trend_dir = unc_base / "reports"    # UNC version for email links
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
            _out_html = trend_dir / (_csv_file.stem + ".html")         # I/O path (Y: drive)
            _unc_html = unc_trend_dir / (_csv_file.stem + ".html")     # UNC path for links
            _cfg = _find_product_config_for(_pfx)
            if _cfg:
                _log("[" + _pfx + "] Product config: " + Path(_cfg).name)
            _log("[" + _pfx + "] Running trend_chart.py -> " + _out_html.name)
            _ok = run_trend_chart(_csv_file, _out_html, args.interval, _cfg, args.dry_run)
            generated.append((_unc_html, _ok))  # use UNC path so email links are network-accessible
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
            build_index(base_dir)
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
