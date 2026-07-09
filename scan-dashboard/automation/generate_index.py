"""
generate_index.py  --  static HTML index of Scan_Report_*.html files.
Generated list is baked in at run time; no server required.
Regenerated automatically after every scheduled run, Save Report, Send Report.

Usage:
    python generate_index.py --base-dir "\\\\server\\share\\auto\\scan"
    from generate_index import build_index; build_index(base_dir)
"""
from __future__ import annotations
import argparse, datetime, re
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

    files = sorted(
        [f for f in reports_dir.glob("Scan_Report_*.html") if f.name != "index.html"],
        key=lambda f: f.stat().st_mtime, reverse=True,
    )
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = ""
    for i, f in enumerate(files):
        m = re.search(r"(\d{8})_(\d{6})", f.name)
        ts = ""
        if m:
            d, t = m.group(1), m.group(2)
            ts = f"{d[:4]}-{d[4:6]}-{d[6:]}  {t[:2]}:{t[2:4]}:{t[4:]}"
        sz    = _fmt_size(f.stat().st_size)
        mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
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
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    import webbrowser
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", default=None)
    args = ap.parse_args()
    _BASE = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\scan")
    base = Path(args.base_dir) if args.base_dir else _BASE
    out  = build_index(base)
    print(f"Index written -> {out}")
    webbrowser.open(out.as_uri())
