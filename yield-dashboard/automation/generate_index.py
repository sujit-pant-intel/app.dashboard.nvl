"""
generate_index.py  --  static HTML index of Yield_Report_*.html files.
Generated list is baked in at run time; no server required.
Regenerated automatically after every scheduled run, Save Report, Send Report.

Usage:
    python generate_index.py --base-dir "\\\\server\\share\\auto\\yield"
    from generate_index import build_index; build_index(base_dir)
"""
from __future__ import annotations
import argparse, datetime, re
from pathlib import Path

_UNC_REPORTS = r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\yield\reports"


def _fmt_size(n: int) -> str:
    if n < 1024:      return f"{n} B"
    if n < 1024**2:   return f"{n/1024:.0f} KB"
    return f"{n/1024**2:.1f} MB"


def build_index(base_dir: Path, unc_base: Path | None = None) -> Path:
    """Scan reports/ and write a static index.html. Returns the file path.
    base_dir  — used for actual file I/O (may be a mapped drive).
    unc_base  — used for file:// link construction (should be UNC); defaults to base_dir.
    """
    reports_dir = base_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Use unc_base for link construction so file:// URLs work for all recipients.
    link_base = unc_base if unc_base is not None else base_dir
    link_reports_dir = link_base / "reports"

    # Derive display UNC path for the footer (copy-paste into Explorer)
    _link_str = str(link_reports_dir)
    if _link_str.startswith("\\\\"):
        unc_reports = _link_str
    else:
        unc_reports = _UNC_REPORTS  # last-resort fallback

    files = sorted(
        (f for f in reports_dir.glob("*.html")
         if not f.name.startswith("index")),
        key=lambda f: f.name,
        reverse=True,
    )
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Split into two groups by product prefix
    groups = {
        "NVL816-BLLC": [f for f in files if "NVL816-BLLC" in f.name],
        "NVL816":       [f for f in files if "NVL816-BLLC" not in f.name],
    }

    def _make_rows(file_list):
        rows = ""
        for i, f in enumerate(file_list):
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
                sz = mtime = "–"
            uri   = (link_reports_dir / f.name).as_uri()
            badge = '<span class="badge">latest</span>' if i == 0 else ""
            rows += (f'\n      <tr data-n="{f.name}">'
                     f'<td class="mono"><a href="{uri}" target="_blank">{f.name}</a> {badge}</td>'
                     f'<td class="dim">{ts}</td>'
                     f'<td class="dim mono">{sz}</td>'
                     f'<td class="dim">{mtime}</td></tr>')
        return rows or '<tr><td colspan="4" class="dim" style="padding:20px">No reports found.</td></tr>'

    rows_bllc = _make_rows(groups["NVL816-BLLC"])
    rows_nvl  = _make_rows(groups["NVL816"])

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
    /* tabs */
    .tabs{{display:flex;gap:0;margin-bottom:0;border-bottom:2px solid var(--bg3)}}
    .tab-btn{{background:var(--bg3);color:var(--dim);border:none;padding:10px 22px;
              font-family:var(--font);font-size:13px;cursor:pointer;border-radius:6px 6px 0 0;
              margin-right:4px;transition:background .15s}}
    .tab-btn.active{{background:var(--bg2);color:var(--acc);font-weight:bold}}
    .tab-panel{{display:none}}.tab-panel.active{{display:block}}
  </style>
</head>
<body>
  <h1>NVL816 Yield Trend Reports</h1>
  <p class="sub">{len(files)} report(s) &nbsp;·&nbsp; Updated: {now_str}</p>

  <div class="card">
    <p style="color:var(--dim);font-size:13px;line-height:1.8">
      Click any link to open the report directly from the shared drive.<br>
      <strong style="color:#ffa726">Requires:</strong> Microsoft Edge &amp; Intel network / VPN.
    </p>
    <code style="color:var(--acc);font-size:11px">{unc_reports}</code>
  </div>

  <div class="card" style="padding-bottom:0">
    <div class="tabs">
      <button class="tab-btn active" onclick="switchTab('bllc',this)">NVL816-BLLC &nbsp;<span class="cnt">({len(groups["NVL816-BLLC"])})</span></button>
      <button class="tab-btn"        onclick="switchTab('nvl',this)" >NVL816 &nbsp;<span class="cnt">({len(groups["NVL816"])})</span></button>
    </div>

    <div id="tab-bllc" class="tab-panel active" style="padding-top:14px">
      <div class="sb">
        <input id="q-bllc" type="text" placeholder="Filter…" oninput="flt('bllc')">
        <span id="cnt-bllc" class="cnt">{len(groups["NVL816-BLLC"])} report(s)</span>
      </div>
      <table><thead><tr><th>Report</th><th>Run Time</th><th>Size</th><th>Modified</th></tr></thead>
        <tbody id="tb-bllc">{rows_bllc}</tbody></table>
    </div>

    <div id="tab-nvl" class="tab-panel" style="padding-top:14px">
      <div class="sb">
        <input id="q-nvl" type="text" placeholder="Filter…" oninput="flt('nvl')">
        <span id="cnt-nvl" class="cnt">{len(groups["NVL816"])} report(s)</span>
      </div>
      <table><thead><tr><th>Report</th><th>Run Time</th><th>Size</th><th>Modified</th></tr></thead>
        <tbody id="tb-nvl">{rows_nvl}</tbody></table>
    </div>
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
      rows.forEach(r => {{ const s = r.dataset.n.toLowerCase().includes(q); r.style.display = s ? '' : 'none'; if (s) n++; }});
      document.getElementById('cnt-' + id).textContent = n + ' report(s)';
    }}
  </script>
</body>
</html>"""

    out = reports_dir / "index.html"  # write via I/O path (mapped drive) to preserve NFS permissions
    import time as _time
    for _attempt in range(3):
        try:
            out.write_text(html, encoding="utf-8")
            break
        except (PermissionError, OSError):
            if _attempt < 2:
                _time.sleep(1)
    return out
    out  = build_index(base)
    print(f"Index written -> {out}")
    webbrowser.open(out.as_uri())
