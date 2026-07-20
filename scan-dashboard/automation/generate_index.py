"""
generate_index.py  --  static HTML index of Scan_Report_*.html files.
Generated list is baked in at run time; no server required.
Regenerated automatically after every scheduled run, Save Report, Send Report.

Usage:
    python generate_index.py
    from generate_index import build_index; build_index()
"""
from __future__ import annotations
import datetime, os, re
from pathlib import Path

_UNC_REPORTS = r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\scan\reports"


def _fmt_size(n: int) -> str:
    if n < 1024:      return f"{n} B"
    if n < 1024**2:   return f"{n/1024:.0f} KB"
    return f"{n/1024**2:.1f} MB"


def build_index(base_dir: Path) -> Path:
    """Scan reports/ and write a static index.html. Returns the file path."""
    reports_dir = base_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Use os.listdir instead of Path.glob — glob silently returns nothing on UNC paths
    try:
        _names = [n for n in os.listdir(str(reports_dir))
                  if n.startswith("Scan_Report_") and n.endswith(".html")]
    except OSError:
        _names = []
    files = sorted(
        [reports_dir / n for n in _names],
        key=lambda f: f.name,   # YYYYMMDD_HHMMSS in name → lexicographic = chronological
        reverse=True,
    )
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = ""
    for i, f in enumerate(files):
        m = re.search(r"(\d{8})_(\d{6})", f.name)
        ts = ""
        if m:
            d, t = m.group(1), m.group(2)
            ts = f"{d[:4]}-{d[4:6]}-{d[6:]}  {t[:2]}:{t[2:4]}:{t[4:]}"
        try:
            st   = f.stat()
            sz   = _fmt_size(st.st_size)
            mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            sz    = "–"
            mtime = "–"
        unc   = "file:////" + _UNC_REPORTS.replace("\\", "/").lstrip("/") + "/" + f.name
        badge = '<span class="badge">latest</span>' if i == 0 else ""
        rows += (f'\n      <tr data-n="{f.name}">'
                 f'<td class="mono"><a href="{unc}" target="_blank">{f.name}</a> {badge}</td>'
                 f'<td class="dim">{ts}</td>'
                 f'<td class="dim mono">{sz}</td>'
                 f'<td class="dim">{mtime}</td></tr>')

    if not rows:
        rows = '<tr><td colspan="4" class="dim" style="padding:20px">No reports found.</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>NVL816-BLLC Scan Reports</title>
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
    #cnt{{color:var(--dim);font-size:12px}}
  </style>
</head>
<body>
  <h1>NVL816-BLLC Scan Reports</h1>
  <p class="sub">{len(files)} report(s) &nbsp;·&nbsp; Updated: {now_str}</p>

  <div class="card">
    <p style="color:var(--dim);font-size:13px;line-height:1.8">
      Click any link to open the report directly from the shared drive.<br>
      <strong style="color:#ffa726">Requires:</strong> Microsoft Edge &amp; Intel network / VPN.<br>
      If the link does not open, check the following:
    </p>
    <ul style="color:var(--dim);font-size:13px;line-height:2;margin:8px 0 4px 20px">
      <li><strong style="color:var(--fg)">No permission</strong> — request access to the samba share from <code style="color:var(--acc)">snpant</code> or your IT admin.</li>
      <li><strong style="color:var(--fg)">Not on network</strong> — connect to Intel VPN first.</li>
      <li><strong style="color:var(--fg)">Wrong browser</strong> — use <strong>Microsoft Edge</strong> (Chrome blocks UNC file:// links).</li>
      <li><strong style="color:var(--fg)">Link silent</strong> — paste the path below into Windows Explorer:</li>
    </ul>
    <code style="color:var(--acc);font-size:11px">{_UNC_REPORTS}</code>
  </div>

  <div class="card">
    <div class="sb">
      <input id="q" type="text" placeholder="Filter reports…" oninput="flt()">
      <span id="cnt">{len(files)} report(s)</span>
    </div>
    <table>
      <thead><tr><th>Report</th><th>Run Time</th><th>Size</th><th>Modified</th></tr></thead>
      <tbody id="tb">{rows}</tbody>
    </table>
  </div>
  <script>
    function flt(){{
      const q=document.getElementById("q").value.toLowerCase(),rows=document.querySelectorAll("#tb tr");
      let n=0;
      rows.forEach(r=>{{const s=r.dataset.n.toLowerCase().includes(q);r.style.display=s?"":"none";if(s)n++;}});
      document.getElementById("cnt").textContent=n+" report(s)";
    }}
  </script>
</body>
</html>"""

    out = reports_dir / "index.html"
    import subprocess as _sp, time as _time
    _wrote = False
    for _attempt in range(3):
        if _attempt == 1:
            # Flush stale Samba ACL cache by forcing a reconnect on the UNC share
            _unc = str(reports_dir)
            _parts = _unc.lstrip("\\").split("\\")
            if len(_parts) >= 2:
                _share = "\\\\" + _parts[0] + "\\" + _parts[1]
                _sp.run(["net", "use", _share, "/delete"], capture_output=True, timeout=5)
                _sp.run(["net", "use", _share, "/persistent:no"], capture_output=True, timeout=5)
        try:
            out.write_text(html, encoding="utf-8")
            _sp.run(["icacls", str(out), "/grant", "Everyone:(W)"],
                    capture_output=True, timeout=5)
            _wrote = True
            break
        except (PermissionError, OSError):
            if _attempt < 2:
                _time.sleep(1)
    if not _wrote:
        # index.html is owned by the scheduled task — write a fallback we do own
        out = reports_dir / "index_latest.html"
        out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", default=None)
    args = ap.parse_args()
    _BASE = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\scan")
    base = Path(args.base_dir) if args.base_dir else _BASE
    out  = build_index(base)
    print(f"Index written -> {out}  ({out.stat().st_size:,} bytes)")
