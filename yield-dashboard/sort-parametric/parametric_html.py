"""parametric_html.py — Generates ParametricDashboard.html.

The dashboard has a fixed 3-section left sidebar:
  1. PCM Analysis — tabs: Variability | Distribution | XY | Analysis
  2. Test Program — links to UPM / SICC / CDYN HTML files if present
  3. PCM-Program  — blank (reserved for future use)

Main content is an iframe pointing at the selected section's HTML.
The PCM analysis content (pcm_analysis.html) must be generated before
this file is called — see parametric_runner.py.

Public API
----------
    generate_parametric_html(
        out_folder: str | Path,
        pcm_html_path: str | Path | None,
        lots: list[str],
        identifier: str,
        upm_html: str | None  = None,
        sicc_html: str | None = None,
        cdyn_html: str | None = None,
    ) -> str   # path to ParametricDashboard.html
"""

from __future__ import annotations

import os
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
_CSS = """\
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden;font-family:Arial,sans-serif;background:#1a252f;color:#ecf0f1;font-size:13px}
/* ── Layout ── */
#shell{display:flex;height:100vh;overflow:hidden}
/* ── Sidebar ── */
#sidebar{width:220px;min-width:180px;flex-shrink:0;background:#1a252f;border-right:2px solid #2c3e50;display:flex;flex-direction:column;overflow-y:auto}
.sb-hdr{padding:10px 12px 6px;background:#0d1b26;border-bottom:1px solid #2c3e50}
.sb-hdr .sb-title{font-size:13px;font-weight:bold;color:#3498db;letter-spacing:0.04em}
.sb-hdr .sb-sub{font-size:10px;color:#7f8c8d;margin-top:2px}
.sb-section{margin-top:4px}
.sb-section-hdr{padding:6px 12px;font-size:11px;font-weight:bold;color:#7f8c8d;letter-spacing:0.08em;text-transform:uppercase;background:#141e27;border-top:1px solid #2c3e50;user-select:none;display:flex;align-items:center;justify-content:space-between}
.sb-section-hdr[onclick]:not([onclick="void(0)"]){cursor:pointer}
.sb-section-hdr[onclick]:not([onclick="void(0)"]):hover{background:#1f2e3d;color:#aeb6bf}
.sb-section-hdr[data-id].active{color:#3498db;background:#1f3a50}
.sb-section.collapsed .sb-items{display:none}
.sb-items{padding:4px 0}
.sb-item{padding:6px 12px 6px 24px;font-size:12px;color:#95a5a6;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-left:3px solid transparent;transition:background 0.1s,color 0.1s}
.sb-item:hover{background:#2c3e50;color:#ecf0f1}
.sb-item.active{background:#1f3a50;color:#3498db;border-left-color:#3498db;font-weight:bold}
.sb-item.blank{color:#4a6a8a;cursor:default;font-style:italic}
.sb-item.blank:hover{background:transparent;color:#4a6a8a}
/* ── Main ── */
#main{flex:1;display:flex;flex-direction:column;overflow:hidden}
#top-bar{background:#0d1b26;padding:6px 14px;display:flex;align-items:center;gap:10px;border-bottom:1px solid #2c3e50;flex-shrink:0}
#top-bar .tb-title{font-size:14px;font-weight:bold;color:#3498db;flex:1}
#top-bar .tb-badge{font-size:11px;color:#95a5a6;background:#1a252f;padding:2px 8px;border-radius:10px;border:1px solid #2c3e50}
#top-bar .tb-link{font-size:11px;color:#27ae60;cursor:pointer;text-decoration:underline;text-underline-offset:2px}
#top-bar .tb-link:hover{color:#2ecc71}
#content{flex:1;overflow:hidden;position:relative}
#content iframe{width:100%;height:100%;border:none;display:none;position:absolute;top:0;left:0}
#content iframe.active{display:block}
#content .blank-pane{display:none;width:100%;height:100%;align-items:center;justify-content:center;flex-direction:column;gap:10px;position:absolute;top:0;left:0}
#content .blank-pane.active{display:flex}
.blank-icon{font-size:48px;opacity:0.3}
.blank-msg{font-size:14px;color:#4a6a8a}
.blank-sub{font-size:11px;color:#34495e}
/* ── Watermark ── */
#wm{position:fixed;bottom:6px;right:10px;font-size:9px;color:#34495e;pointer-events:none}
"""

# ---------------------------------------------------------------------------
# JS
# ---------------------------------------------------------------------------
_JS = """\
var _yieldUrl=null;
/* ── Generic pane switching ───────────────────────────────────────────────────── */
function _show(id){
  document.querySelectorAll('#content iframe,#content .blank-pane').forEach(function(el){el.classList.remove('active');});
  document.querySelectorAll('[data-id]').forEach(function(el){el.classList.remove('active');});
  var pane=document.getElementById('pane-'+id);
  if(pane)pane.classList.add('active');
  var item=document.querySelector('[data-id="'+id+'"]');
  if(item)item.classList.add('active');
}
function _openYield(){
  var url=_yieldUrl;
  if(url)window.open(url,'_blank');
}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_parametric_html(
    out_folder: "str | Path",
    pcm_html_path: "str | Path | None",
    lots: list,
    identifier: str,
    upm_html: "str | None" = None,
    sicc_html: "str | None" = None,
    cdyn_html: "str | None" = None,
    yield_dashboard_html: "str | None" = None,
    pcmprog_html: "str | None" = None,
) -> str:
    """Generate ParametricDashboard.html and return its path."""
    out_folder = Path(out_folder)
    out_folder.mkdir(parents=True, exist_ok=True)

    out_path = out_folder / "ParametricDashboard.html"

    # Make paths relative (relative to out_folder for iframe src)
    def _rel(p: "str | None") -> "str | None":
        if not p:
            return None
        try:
            return Path(p).relative_to(out_folder).as_posix()
        except ValueError:
            return Path(p).as_posix().replace("\\", "/")

    pcm_rel        = _rel(pcm_html_path) if pcm_html_path else None
    upm_rel        = _rel(upm_html)  if upm_html  else None
    sicc_rel       = _rel(sicc_html) if sicc_html else None
    cdyn_rel       = _rel(cdyn_html) if cdyn_html else None
    yield_rel      = _rel(yield_dashboard_html) if yield_dashboard_html else None
    pcmprog_rel    = _rel(pcmprog_html) if pcmprog_html else None

    lots_str  = ", ".join(lots) if lots else "—"
    ident_esc = _esc(identifier)

    # ── PCM Analysis — no sub-items ───────────────────────────────────────────
    pcm_src = pcm_rel or ""

    # ── Sidebar HTML ─────────────────────────────────────────────────────────
    def _sb_item(item_id: str, label: str, blank: bool = False, indent: int = 24) -> str:
        cls = "sb-item blank" if blank else "sb-item"
        onclick = "" if blank else f' onclick="_show(\'{item_id}\')" data-id="{item_id}"'
        return (f'<div class="{cls}"{onclick} '
                f'style="padding-left:{indent}px">{_esc(label)}</div>\n')

    _pcm_hdr_style = "cursor:pointer" if pcm_src else "cursor:default;opacity:0.5"
    _pcm_hdr_click = "_show('pcm')" if pcm_src else "void(0)"

    sb_tp_items = ""
    if upm_rel:
        sb_tp_items  += _sb_item("tp_upm",  "UPM")
    if sicc_rel:
        sb_tp_items  += _sb_item("tp_sicc", "SICC")
    if cdyn_rel:
        sb_tp_items  += _sb_item("tp_cdyn", "CDYN")
    if not sb_tp_items:
        sb_tp_items = _sb_item("tp_none", "(coming soon)", blank=True)

    # PCM-Program sidebar
    if pcmprog_rel:
        sb_pcmprog_items = (_sb_item("pcmprog_upm",  "UPM vs Propagation Delay") +
                            _sb_item("pcmprog_sicc", "SICC vs Poff"))
        _pcmprog_hdr_click = "_show('pcmprog_upm')"
        _pcmprog_hdr_style = "cursor:pointer"
    else:
        sb_pcmprog_items = _sb_item("pcmprog_none", "(coming soon)", blank=True)
        _pcmprog_hdr_click = "void(0)"
        _pcmprog_hdr_style = "cursor:default;opacity:0.5"

    sidebar_html = f"""\
<div id="sidebar">
  <div class="sb-hdr">
    <div class="sb-title">&#128202; Parametric</div>
    <div class="sb-sub">{ident_esc}</div>
  </div>

  <!-- Parametric Dashboard -->
  <div class="sb-section" id="sec-pcm">
    <div class="sb-section-hdr" onclick="{_pcm_hdr_click}" data-id="pcm" style="{_pcm_hdr_style}">
      Parametric Dashboard
    </div>
  </div>

  <!-- Test Program -->
  <div class="sb-section" id="sec-tp">
    <div class="sb-section-hdr" style="cursor:default">
      Test Program
    </div>
    <div class="sb-items">
{sb_tp_items}    </div>
  </div>

  <!-- PCM-Program -->
  <div class="sb-section" id="sec-pcmprog">
    <div class="sb-section-hdr" onclick="{_pcmprog_hdr_click}" {f'data-id="pcmprog"' if pcmprog_rel else ''} style="{_pcmprog_hdr_style}">
      PCM-Program
    </div>
    <div class="sb-items">
{sb_pcmprog_items}    </div>
  </div>
</div>"""

    # ── Iframe / pane definitions ────────────────────────────────────────────
    panes_html = ""

    def _iframe(pane_id: str, src: "str | None") -> str:
        if src:
            return f'<iframe id="pane-{pane_id}" src="{src}"></iframe>\n'
        return _blank_pane(pane_id, "No data available",
                           "Run the pipeline to generate this section.")

    def _blank_pane(pane_id: str, msg: str, sub: str = "") -> str:
        return (f'<div class="blank-pane" id="pane-{pane_id}">'
                f'<div class="blank-icon">&#128202;</div>'
                f'<div class="blank-msg">{_esc(msg)}</div>'
                f'<div class="blank-sub">{_esc(sub)}</div>'
                f'</div>\n')

    # PCM pane — single iframe
    if pcm_src:
        panes_html += f'<iframe id="pane-pcm" src="{pcm_src}"></iframe>\n'
    else:
        panes_html += _blank_pane("pcm", "No PCM data available",
                                  "No matching PCM CSV found for the lots in this run.")

    # Test program tabs
    panes_html += _iframe("tp_upm",  upm_rel)
    panes_html += _iframe("tp_sicc", sicc_rel)
    panes_html += _iframe("tp_cdyn", cdyn_rel)
    panes_html += _blank_pane("tp_none", "Test Program",
                               "UPM / SICC / CDYN links coming soon.")

    # PCM-Program panes — iframe anchored to a specific tab via URL hash
    if pcmprog_rel:
        panes_html += f'<iframe id="pane-pcmprog_upm"  src="{pcmprog_rel}#upm_td"></iframe>\n'
        panes_html += f'<iframe id="pane-pcmprog_sicc" src="{pcmprog_rel}#sicc_ioff"></iframe>\n'
    else:
        panes_html += _blank_pane("pcmprog_upm",  "PCM-Program",
                                   "Run the pipeline to generate PCM-Program correlations.")
        panes_html += _blank_pane("pcmprog_sicc", "PCM-Program",
                                   "Run the pipeline to generate PCM-Program correlations.")
    panes_html += _blank_pane("pcmprog_none", "PCM-Program", "(coming soon)")

    # ── Top bar ─────────────────────────────────────────────────────────────
    yield_link = ""
    if yield_rel:
        yield_link = (f'<span class="tb-link" onclick="_openYield()">'
                      f'&#128279; Yield Dashboard</span>')
    lot_badge = f'<span class="tb-badge">Lots: {_esc(lots_str)}</span>' if lots else ""
    top_bar_html = f"""\
<div id="top-bar">
  <div class="tb-title">Parametric Dashboard</div>
  {lot_badge}
  {yield_link}
</div>"""

    # ── Initial pane ─────────────────────────────────────────────────────────
    if pcm_src:
        first_show_js = "_show('pcm');"
    elif upm_rel:
        first_show_js = "_show('tp_upm');"
    elif sicc_rel:
        first_show_js = "_show('tp_sicc');"
    elif cdyn_rel:
        first_show_js = "_show('tp_cdyn');"
    else:
        first_show_js = "_show('pcmprog_placeholder');"

    yield_url_js = f'"{yield_rel}"' if yield_rel else 'null'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Parametric Dashboard — {ident_esc}</title>
<style>
{_CSS}
</style>
</head>
<body>
<div id="shell">
{sidebar_html}
  <div id="main">
{top_bar_html}
    <div id="content">
{panes_html}    </div>
  </div>
</div>
<div id="wm">Pant, Sujit N \u2014 GEMS FTE</div>
<script>
var _yieldUrl={yield_url_js};
{_JS}
window.addEventListener('DOMContentLoaded',function(){{{first_show_js}}});
</script>
</body>
</html>
"""

    out_path.write_text(html, encoding="utf-8")
    return str(out_path)


def _esc(s: str) -> str:
    """HTML-escape a string."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))
