"""generate_dashboard_html_svg.py — Responsive-SVG variant of the SICC/UPM/CDYN dashboard.

Same data, same interactivity as generate_dashboard_html.py, but adds a
ResizeObserver (+ window resize fallback) so SVG charts automatically redraw
at the correct size whenever the browser window or any panel is resized —
matching the behaviour of the vmin-dashboard.

Public API:
    generate_html_svg(data, output_path, title='') -> str
"""

import json
import os
import sys

# Ensure sicc_cdyn_upm/src is on sys.path so relative imports work when called
# from _pipeline_runner.py (which lives in a different directory).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from _dash_frame import build_page_open, build_page_close, CSS  # noqa: E402
from _dash_js_shared import SHARED_JS, RESIZE_JS                 # noqa: E402
from _tab_registry import TABS                                    # noqa: E402


def _wm_inject(html: str) -> str:
    _wm = (
        '<div id="_wm_div" style="position:fixed;top:8px;right:12px;font-size:10px;'
        'font-weight:600;pointer-events:none;z-index:99999;'
        'font-family:Arial,sans-serif;user-select:none;letter-spacing:0.04em;'
        'padding:2px 6px;border-radius:3px;background:transparent;">'
        'Pant, Sujit N \u2014 GEMS FTE</div>'
        '<script>(function(){'
        'if(window!==window.top){var _d=document.getElementById("_wm_div");if(_d)_d.style.display="none";return;}'
        'var d=document.getElementById("_wm_div");'
        'if(d)d.style.color="rgba(255,255,255,0.9)";'
        '})();</script>'
    )
    import re as _re_wm
    if '</body>' not in html:
        return html
    html = _re_wm.sub(
        r'<div[^>]*id=["\']_wm_div["\'][^>]*>[\s\S]*?</div>\s*<script[^>]*>[\s\S]*?</script>',
        '', html)
    html = _re_wm.sub(r'<div[^>]*>[^<]*GEMS FTE[^<]*</div>', '', html)
    return html.replace('</body>', _wm + '\n</body>', 1)


def _esc_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


# JS injected INSIDE the main IIFE (so it has direct access to render_sicc etc.)
# Adds window resize + ResizeObserver so charts redraw whenever the panel changes size.
_INNER_RESIZE_OBS_JS = r"""
// ── ResizeObserver: auto-redraw SVG charts on panel/window resize ────────────
// Injected by generate_dashboard_html_svg.py inside the main IIFE so that
// render_sicc / render_cdyn / _TAB_RENDERS are all in scope.
(function () {
  function _svgRerender() {
    var active = document.querySelector('.tab-panel.active');
    if (!active) return;
    var id = active.id;
    if (_TAB_RENDERS[id]) _TAB_RENDERS[id]();
  }
  var _rt = null;
  function _debounced() { clearTimeout(_rt); _rt = setTimeout(_svgRerender, 80); }
  window.addEventListener('resize', _debounced);
  if (typeof ResizeObserver !== 'undefined') {
    var obs = new ResizeObserver(_debounced);
    function _attach() {
      document.querySelectorAll('.dist-side, .side-layout, .tab-content, .chart-panel')
        .forEach(function (el) { obs.observe(el); });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _attach);
    else _attach();
  }
}());
"""


def generate_html_svg(data: dict, output_path: str, title: str = '') -> str:
    """Generate the responsive-SVG variant of the SICC/UPM/CDYN dashboard.

    Identical to generate_html() in generate_dashboard_html.py except:
    • The page title carries the suffix ' — SVG'.
    • A ResizeObserver is injected so charts redraw on window/panel resize.
    • Font colours and all other styling are unchanged from the original.
    """
    csv_name      = data.get('csv_name', 'data.csv')
    display_title = title or csv_name

    sicc_cols     = data.get('sicc_columns', [])
    upm_cols      = data.get('upm_columns', [])
    cdyn_cols     = data.get('cdyn_columns', [])
    targets       = data.get('targets', {})
    cdyn_targets  = data.get('cdyn_targets', {})
    rows          = data.get('rows', [])
    sicc_tbl_cfg  = data.get('sicc_table_config', [])
    cdyn_tbl_cfg  = data.get('cdyn_table_config', [])
    upm_dist      = data.get('upm_dist_cols', [])

    tgt_map = {k.upper(): v for k, v in targets.items()}
    for c in sicc_cols + upm_cols + upm_dist:
        if c.upper() not in tgt_map and c in targets:
            tgt_map[c.upper()] = targets[c]

    data_json     = _esc_json(rows)
    sicc_json     = _esc_json(sicc_cols)
    upm_json      = _esc_json(upm_cols)
    cdyn_json     = _esc_json(cdyn_cols)
    targets_json  = _esc_json(tgt_map)
    cdyn_tgt_json = _esc_json(cdyn_targets)
    sicc_tbl_json = _esc_json(sicc_tbl_cfg)
    cdyn_tbl_json = _esc_json(cdyn_tbl_cfg)
    upm_dist_json = _esc_json(upm_dist)
    _def_col      = (sicc_tbl_cfg[0][2] if sicc_tbl_cfg
                     else (sicc_cols + cdyn_cols + [''])[0])
    default_col   = _esc_json(_def_col)

    # ── Tab bar ──────────────────────────────────────────────────────────────
    tabs_html = ''
    for tab in TABS:
        active_cls = ' active' if tab.active else ''
        btn_id = tab.tab_id.replace('tab-', '')
        tabs_html += (
            f'  <button class="tab-btn{active_cls}" id="btn-{btn_id}"'
            f' onclick="showTab(this,\'{tab.tab_id}\')">{tab.label}</button>\n'
        )
    tabs_html += (
        '  <a href="https://intel.sharepoint.com/:x:/r/sites/ftesdsexecution/_layouts/15/Doc.aspx'
        '?sourcedoc=%7BB2A0D111-751C-4EEE-9F65-A43F2AC6D12F%7D'
        '&file=NVL816_CDIE-N2P_PreSi_summary.xlsx&action=default&mobileredirect=true"'
        ' target="_blank" rel="noopener noreferrer"'
        ' style="margin-left:16px;align-self:center;font-size:13px;color:#ecf0f1;'
        'text-decoration:underline;white-space:nowrap;opacity:0.85;"'
        '>SICC/CDYN SPEC</a>\n'
    )

    # ── Tab panels ───────────────────────────────────────────────────────────
    tabs_panels_html = ''
    for tab in TABS:
        panel = tab.html_fn()
        if tab.active:
            panel = panel.replace('class="tab-panel"', 'class="tab-panel active"', 1)
        tabs_panels_html += panel + '\n'

    # ── Per-tab JS ───────────────────────────────────────────────────────────
    tabs_js = ''
    for tab in TABS:
        tabs_js += tab.js_fn() + '\n'

    # ── Inline data declarations ──────────────────────────────────────────────
    data_js = (
        f'var ROWS={data_json};\n'
        f'var SICC_COLS={sicc_json};\n'
        f'var UPM_COLS={upm_json};\n'
        f'var CDYN_COLS={cdyn_json};\n'
        f'var TARGETS={targets_json};\n'
        f'var CDYN_TARGETS={cdyn_tgt_json};\n'
        f'var SICC_TBL_CFG={sicc_tbl_json};\n'
        f'var CDYN_TBL_CFG={cdyn_tbl_json};\n'
        f'var UPM_DIST_COLS={upm_dist_json};\n'
        f'var ALL_COLS=SICC_COLS.concat(UPM_COLS);\n'
        f'var SEL_COL={default_col};\n'
        f'var IS_CDYN=false;\n'
    )

    # ── Assemble HTML (identical structure to generate_dashboard_html.py) ────
    # _INNER_RESIZE_OBS_JS is injected inside the IIFE so it can access
    # _TAB_RENDERS, render_sicc, render_cdyn etc. directly.
    html = (
        build_page_open(display_title, tabs_html)
        + tabs_panels_html
        + build_page_close()
        + '<script>\n(function(){\n'
        + data_js
        + SHARED_JS
        + tabs_js
        + _INNER_RESIZE_OBS_JS          # ← inside IIFE: has access to render_* fns
        + 'if(document.readyState===\'loading\')document.addEventListener(\'DOMContentLoaded\',init);\nelse init();\n'
        + '})();\n'
        + RESIZE_JS
        + '\n</script></body></html>'
    )

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(_wm_inject(html))
    return html
