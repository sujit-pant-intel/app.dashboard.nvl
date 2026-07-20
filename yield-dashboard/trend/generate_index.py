"""
generate_index.py  --  static HTML index of NVL816 Yield Trend Report HTML files.
Trend-specific: BLLC tab + NVL816 tab, trend UNC path. No shared code with yield.
Regenerated automatically after every scheduled run, Save Report, Send Report.

Usage:
    python generate_index.py --base-dir "\\\\server\\share\\auto\\trend"
    from generate_index import build_index; build_index(base_dir)
"""
from __future__ import annotations
import argparse, datetime, re
from pathlib import Path

_UNC_REPORTS = r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\trend\reports"


def _fmt_size(n: int) -> str:
    if n < 1024:      return f"{n} B"
    if n < 1024**2:   return f"{n/1024:.0f} KB"
    return f"{n/1024**2:.1f} MB"


def build_index(base_dir: Path) -> Path:
    """Scan reports/ and write a static index.html with BLLC / NVL816 tabs. Returns the file path."""
    reports_dir = base_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Resolve UNC for link hrefs
    _base_str = str(base_dir)
    if _base_str.startswith("\\\\"):
        unc_reports = str(base_dir / "reports").replace("/", "\\")
    else:
        unc_reports = _UNC_REPORTS
        try:
            import subprocess as _sp
            _r = _sp.run(["net", "use", _base_str[:2].upper()],
                         capture_output=True, text=True, timeout=5)
            for _line in _r.stdout.splitlines():
                if "remote name" in _line.lower():
                    unc_root = _line.split(None, 2)[-1].strip().rstrip("\\")
                    if unc_root.startswith("\\\\"):
                        unc_reports = unc_root + _base_str[2:].replace("/", "\\") + "\\reports"
                    break
        except Exception:
            pass

    all_files = sorted(
        (f for f in reports_dir.glob("NVL816*.html")
         if not f.name.startswith("index")),
        key=lambda f: f.name,
        reverse=True,
    )
    bllc_files  = [f for f in all_files if "BLLC" in f.name]
    other_files = [f for f in all_files if "BLLC" not in f.name]
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
                mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                sz    = "–"
                mtime = "–"
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
    tmp = reports_dir / "index.html.tmp"
    import time as _time
    for _attempt in range(3):
        try:
            tmp.write_text(html, encoding="utf-8")
            if out.exists():
                out.unlink()
            tmp.replace(out)
            break
        except (PermissionError, OSError):
            if _attempt < 2:
                _time.sleep(1)
            else:
                tmp.unlink(missing_ok=True)
    return out


if __name__ == "__main__":
    import webbrowser
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", default=None)
    args = ap.parse_args()
    _BASE = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\trend")
    base = Path(args.base_dir) if args.base_dir else _BASE
    out  = build_index(base)
    print(f"Index written -> {out}")
    webbrowser.open(out.as_uri())
