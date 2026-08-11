"""
html_builder.py — WpaHtmlBuilder: build self-contained WPA HTML snippets.

Usage::

    from wafer_pattern.html_builder import WpaHtmlBuilder

    b = WpaHtmlBuilder()
    b.add_wafer("LOT1::01", dies, lot="LOT1", wafer="01",
                material="8PF6CV", program="NVL816")
    html = b.build(btn_label="📊 Wafer Pattern Analysis")

The returned string is a self-contained ``<div>`` containing CSS, DOM, and
``<script>`` — suitable for injection into any HTML page or as a standalone file.

Color assignment for fail IBs mirrors _pipeline_html.py exactly (MD5 hash +
44-slot dedup). Pass IBs 1–4 use the fixed green/grey palette.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ._wpa_js import WPA_CSS, WPA_FULL_JS

# ── IB colour constants ───────────────────────────────────────────────────────

_WM_PASS_CLR: dict[str, str] = {
    '1': '#00ff44',
    '2': '#7ddb8a',
    '3': '#3d3d3d',
    '4': '#b0b0b0',
}

_WM_FAIL_PAL: list[str] = [
    '#ff0000', '#ff6600', '#ff8800', '#ffcc00',
    '#0055ff', '#00aaff', '#aa00ff', '#cc00ff',
    '#ff0066', '#ff33aa', '#00bbee', '#ff3333',
    '#6699ff', '#cc0099', '#ffaa00', '#336bff',
    '#cc0000', '#cc4400', '#cc9900', '#0033cc',
    '#6600cc', '#dd4499', '#dd2288', '#0099cc',
    '#ff6666', '#ffdd55', '#5500cc', '#ff5500',
    '#990000', '#994400', '#cc7700', '#003399',
    '#660099', '#005580', '#990066', '#003d5c',
    '#660000', '#cc3300', '#e6b800', '#000099',
    '#330066', '#7700aa', '#550000', '#1a0066',
]
_WM_PAL_N = len(_WM_FAIL_PAL)


def _md5f(n_int: int, salt: str = 'color') -> float:
    """Return deterministic float in [0, 1) from integer + salt (MD5-based)."""
    h = hashlib.md5(f'{salt}:{n_int}'.encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _build_ib_colors(fail_ibs: list[str]) -> dict[str, str]:
    """
    Assign hex colours to IB numbers.

    Pass bins 1-4 use ``_WM_PASS_CLR``; all others get a palette slot
    chosen deterministically via MD5 hash with deduplication — same
    algorithm as ``_pipeline_html.py``.
    """
    color_map: dict[str, str] = dict(_WM_PASS_CLR)
    assigned: set[int] = set()
    for ib_str in sorted(fail_ibs, key=lambda s: int(s) if s.isdigit() else 0):
        try:
            n = int(ib_str)
        except ValueError:
            n = hash(ib_str) & 0xFFFF
        idx = int(_md5f(n, 'color') * _WM_PAL_N) % _WM_PAL_N
        for off in range(_WM_PAL_N):
            cand = (idx + off) % _WM_PAL_N
            if cand not in assigned:
                idx = cand
                break
        assigned.add(idx)
        color_map[ib_str] = _WM_FAIL_PAL[idx]
    return color_map


# ── WpaHtmlBuilder ────────────────────────────────────────────────────────────

class WpaHtmlBuilder:
    """
    Accumulates wafer data and emits a self-contained WPA HTML block.

    Parameters
    ----------
    fail_thr : int
        IB number at or above which a die is considered a fail (default 3).
    edge_exclude_rows : int
        Edge row/column exclusion for pattern scoring (default 1).
    """

    def __init__(self, fail_thr: int = 3, edge_exclude_rows: int = 1) -> None:
        self._wafers: dict[str, dict[str, Any]] = {}
        self._fail_thr = fail_thr
        self._edge_exclude_rows = edge_exclude_rows
        # Reticle data keyed by *pfx* (prefix string, e.g. lot prefix or "")
        self._ret_maps: dict[str, dict] = {}
        self._ret_shots: dict[str, list] = {}
        self._ret_site_totals: dict[str, dict] = {}
        self._ret_site_labels: dict[str, dict] = {}
        # Global reticle (single-reticle mode)
        self._global_ret_map: dict | None = None
        self._global_ret_shots: list | None = None
        self._global_ret_site_totals: dict | None = None
        self._global_ret_site_labels: dict | None = None

    # ── data accumulation ────────────────────────────────────────────────────

    def add_wafer(
        self,
        key: str,
        dies: list[tuple[int | None, int | None, int | None]],
        lot: str = '',
        wafer: str = '',
        material: str = '',
        program: str = '',
        pfx: str = '',
        reticle_map: dict | None = None,
        reticle_shots: list | None = None,
        reticle_site_totals: dict | None = None,
        reticle_site_labels: dict | None = None,
    ) -> 'WpaHtmlBuilder':
        """
        Add one wafer's die data.

        Parameters
        ----------
        key : str
            Unique key in ``"lot::wafer"`` or ``"lot::wafer::program"`` form.
        dies : list of (x, y, ib)
            Die coordinates and IB number.  Use ``None`` for missing values.
        lot, wafer, material, program : str
            Metadata shown in pickers and tables.
        pfx : str
            Reticle map prefix — use when different lots share different reticle
            files.  Leave blank for a single global reticle.
        reticle_map : dict, optional
            ``{"x,y": [site_x, site_y, shot_idx]}`` mapping.
        reticle_shots : list, optional
            ``[[xMin,yMin,xMax,yMax], …]`` shot bounding boxes.
        reticle_site_totals : dict, optional
            ``{"site_x,site_y": total_shots}`` denominator for reticle score.
        reticle_site_labels : dict, optional
            ``{"site_x,site_y": label}`` human-readable die-loc labels.
        """
        self._wafers[key] = {
            'lot': lot or key.split('::')[0],
            'wafer': wafer or (key.split('::')[1] if '::' in key else key),
            'material': material,
            'program': program,
            'pfx': pfx,
            'dies': [[d[0], d[1], d[2]] for d in dies],
        }
        if pfx and reticle_map:
            self._ret_maps[pfx] = reticle_map
            if reticle_shots:
                self._ret_shots[pfx] = reticle_shots
            if reticle_site_totals:
                self._ret_site_totals[pfx] = reticle_site_totals
            if reticle_site_labels:
                self._ret_site_labels[pfx] = reticle_site_labels
        elif reticle_map and not pfx:
            # Global reticle (overwrites if called multiple times — all wafers share it)
            self._global_ret_map = reticle_map
            if reticle_shots:
                self._global_ret_shots = reticle_shots
            if reticle_site_totals:
                self._global_ret_site_totals = reticle_site_totals
            if reticle_site_labels:
                self._global_ret_site_labels = reticle_site_labels
        return self

    def set_global_reticle(
        self,
        reticle_map: dict,
        reticle_shots: list | None = None,
        reticle_site_totals: dict | None = None,
        reticle_site_labels: dict | None = None,
    ) -> 'WpaHtmlBuilder':
        """Set a single shared reticle map used by all wafers."""
        self._global_ret_map = reticle_map
        self._global_ret_shots = reticle_shots
        self._global_ret_site_totals = reticle_site_totals
        self._global_ret_site_labels = reticle_site_labels
        return self

    # ── internal helpers ─────────────────────────────────────────────────────

    def _build_wm_pat(self) -> dict:
        """Build the ``WM_PAT`` JS object as a Python dict."""
        # Collect all fail IB strings (not in pass CLR) for colour assignment
        fail_ibs: list[str] = sorted(
            {
                str(d[2])
                for wdata in self._wafers.values()
                for d in wdata['dies']
                if d[2] is not None and str(d[2]) not in _WM_PASS_CLR
            },
            key=lambda s: int(s) if s.isdigit() else 0,
        )
        ib_colors = _build_ib_colors(fail_ibs)

        has_reticle = bool(self._global_ret_map or self._ret_maps)

        # Per-prefix reticle maps for JS
        ret_maps_js: dict = {}
        for pfx, rm in self._ret_maps.items():
            ret_maps_js[pfx] = {
                'retMap': rm,
                'retShots': self._ret_shots.get(pfx, []),
                'retSiteTotals': self._ret_site_totals.get(pfx, {}),
                'retSiteLabels': self._ret_site_labels.get(pfx, {}),
            }

        return {
            'wafers': self._wafers,
            'hasReticle': has_reticle,
            'retMap': self._global_ret_map or {},
            'retShots': self._global_ret_shots or [],
            'retSiteTotals': self._global_ret_site_totals or {},
            'retSiteLabels': self._global_ret_site_labels or {},
            'retMaps': ret_maps_js,
            'ibColors': ib_colors,
            'ibDesc': {},
        }

    # ── HTML assembly ────────────────────────────────────────────────────────

    def build(
        self,
        btn_label: str = '📊 Wafer Pattern Analysis',
        trigger_id: str | None = None,
        standalone: bool = False,
        watermark: str = '',
    ) -> str:
        """
        Build and return the WPA HTML block.

        Parameters
        ----------
        btn_label : str
            Label for the floating trigger button (if ``trigger_id`` is None,
            a ``<button id="wpa-open-btn">`` is emitted).
        trigger_id : str, optional
            DOM id of an *existing* element that will open the panel when
            clicked.  If given, no button is emitted.
        standalone : bool
            Wrap output in a full ``<!DOCTYPE html>`` page (useful for
            writing self-contained debug files).
        watermark : str
            Optional one-line watermark shown in the drag bar (e.g. "Author — Team").
        """
        wm_pat = self._build_wm_pat()
        has_reticle = wm_pat['hasReticle']

        wm_pat_json = json.dumps(wm_pat, separators=(',', ':'))

        # ── CSS ──
        css_block = f'<style>{WPA_CSS}</style>'

        # ── DOM fragments ──
        watermark_html = (
            f'<div style="position:absolute;top:6px;right:48px;font-size:9px;'
            f'color:rgba(255,255,255,0.75);font-family:Arial,sans-serif;'
            f'pointer-events:none;user-select:none;letter-spacing:0.03em;'
            f'z-index:1;white-space:nowrap">{watermark}</div>'
            if watermark else ''
        )

        reticle_tab_btn = (
            '<button class="wm-pat-tab" id="wm-pat-tab-reticle" '
            'onclick="wmPatTab(\'reticle\')">&#127760; Reticle</button>\n'
            if has_reticle else ''
        )
        reticle_tab_pane = (
            '<div class="wm-pat-tabpane" id="wm-pat-pane-reticle">'
            '<div id="wm-pat-reticle-body" style="padding:4px;font-size:11px;overflow:auto;flex:1">'
            '<span style="color:#aaa">Select wafers to view reticle analysis.</span>'
            '</div></div>\n'
            if has_reticle else ''
        )
        reticle_th = (
            '<th>Reticle</th><th>Top Die Loc</th>'
            if has_reticle else ''
        )

        guide_html = (
            '<div style="padding:8px;font-size:11px;overflow:auto;flex:1">'
            '<b style="color:#145a32">Pattern Guide — Typical Process Suspects</b>'
            '<table style="border-collapse:collapse;width:100%;margin-top:6px;font-size:11px">'
            '<thead><tr style="background:#145a32;color:#fff">'
            '<th style="padding:3px 8px;text-align:left">Pattern</th>'
            '<th style="padding:3px 8px;text-align:left">Description</th>'
            '<th style="padding:3px 8px;text-align:left">Typical Suspects</th>'
            '</tr></thead><tbody>'
            '<tr><td style="padding:3px 8px;font-weight:bold;color:#c0392b">CENTER</td>'
            '<td style="padding:3px 8px">High fail density at wafer center</td>'
            '<td style="padding:3px 8px;color:#555">CMP non-uniformity, etch loading, temperature gradient, deposition center-thick/thin</td></tr>'
            '<tr style="background:#f7f9fc"><td style="padding:3px 8px;font-weight:bold;color:#e67e22">EDGE</td>'
            '<td style="padding:3px 8px">High fail density near wafer edge</td>'
            '<td style="padding:3px 8px;color:#555">Edge seal quality, bevel etch, photoresist edge bead, implant shadowing, edge exclusion</td></tr>'
            '<tr><td style="padding:3px 8px;font-weight:bold;color:#8e44ad">DONUT</td>'
            '<td style="padding:3px 8px">Ring of fails mid-radius, clear center &amp; edge</td>'
            '<td style="padding:3px 8px;color:#555">Chuck temperature ring, deposition annular non-uniformity, spin coating ring</td></tr>'
            '<tr style="background:#f7f9fc"><td style="padding:3px 8px;font-weight:bold;color:#2471a3">SYSTEMATIC</td>'
            '<td style="padding:3px 8px">Quadrant or sector imbalance</td>'
            '<td style="padding:3px 8px;color:#555">Tool asymmetry, wafer orientation, gas flow non-uniformity, robotic handling</td></tr>'
            '<tr><td style="padding:3px 8px;font-weight:bold;color:#1f618d">RETICLE</td>'
            '<td style="padding:3px 8px">Die locations repeat across shots (reticle-correlated)</td>'
            '<td style="padding:3px 8px;color:#555">Reticle defect, mask particle, stepper/scanner optics, reticle cleaning damage</td></tr>'
            '<tr style="background:#f7f9fc"><td style="padding:3px 8px;font-weight:bold;color:#27ae60">RANDOM</td>'
            '<td style="padding:3px 8px">No dominant spatial pattern</td>'
            '<td style="padding:3px 8px;color:#555">Parametric drift, particle contamination (random), process window marginal, test escape</td></tr>'
            '</tbody></table>'
            '<p style="color:#888;font-size:10px;margin-top:6px">'
            '&#x26A0;&#xFE0F; Scores are heuristic. Always confirm with engineering context and multiple lots.'
            ' Edge exclusion row setting affects pattern and reticle scores.</p>'
            '</div>'
        )

        overlay_html = f"""
<div class="wm-pat-overlay" id="wm-pat-overlay">
  <div class="wm-pat-box" id="wm-pat-box" style="position:relative">
    <div class="wm-pat-drag" id="wm-pat-drag">
      {watermark_html}
      <b style="flex:1">{btn_label}</b>
      <button class="wm-pat-close" onclick="wmHidePat()">&times;</button>
    </div>
    <div id="wm-pat-prog-picker" style="display:none;background:#0b2d48;padding:2px 10px;flex-wrap:wrap;gap:2px"></div>
    <div id="wm-pat-lot-picker" style="background:#4a235a;padding:2px 10px;display:flex;align-items:center;flex-wrap:wrap;gap:2px"></div>
    <div id="wm-pat-wafer-picker" style="background:#1b2631;padding:2px 10px;display:flex;align-items:center;flex-wrap:wrap;gap:2px"></div>
    <div class="wm-pat-body2">
      <div class="wm-pat-ctrl" id="wm-pat-ctrl">
        <div class="wm-pat-binrow" id="wm-pat-retrow" style="display:none"></div>
        <div class="wm-pat-binrow" id="wm-pat-shotrow" style="display:none"></div>
      </div>
      <div class="wm-pat-binrow" id="wm-pat-binrow"></div>
      <div class="wm-pat-inner2">
        <div class="wm-pat-left">
          <div class="wm-pat-ltab-bar">
            <button class="wm-pat-ltab on" data-ltab="wafers" onclick="wmPatLTab(this.dataset.ltab)">Wafer Maps</button>
            <button class="wm-pat-ltab" data-ltab="composite" onclick="wmPatLTab(this.dataset.ltab)">Composite Map</button>
          </div>
          <div class="wm-pat-lpane on" id="wm-pat-lpane-wafers">
            <div style="display:flex;align-items:center;gap:6px;padding:2px 4px 4px;flex-shrink:0">
              <span style="font-size:10px;color:#666;white-space:nowrap">Tile size:</span>
              <input type="range" min="80" max="380" value="190" step="10" id="wm-tile-size-slider" style="width:90px;cursor:pointer" oninput="_wmSetTileW(+this.value)">
              <span id="wm-tile-size-lbl" style="font-size:10px;color:#555;min-width:34px">190px</span>
            </div>
            <div class="wm-pat-maps-wrap">
              <div class="wm-pat-maps" id="wm-pat-maps"></div>
            </div>
          </div>
          <div class="wm-pat-lpane" id="wm-pat-lpane-composite">
            <div id="wm-pat-modemap-body" style="padding:8px;overflow:auto;flex:1">
              <span style="color:#999;font-size:11px">Loading...</span>
            </div>
          </div>
        </div>
        <div class="wm-pat-vsplit" id="wm-pat-vsplit"></div>
        <div class="wm-pat-right">
          <div class="wm-pat-tabs">
            <button class="wm-pat-tab on" id="wm-pat-tab-impact" onclick="wmPatTab('impact')">Bin Impact</button>
            <button class="wm-pat-tab" id="wm-pat-tab-composite2" onclick="wmPatTab('composite2')">Composite</button>
            {reticle_tab_btn}
            <button class="wm-pat-tab" id="wm-pat-tab-guide" onclick="wmPatTab('guide')">Guide</button>
          </div>
          <div class="wm-pat-tabpane on" id="wm-pat-pane-impact">
            <div class="wm-pat-impact" id="wm-pat-impact-body">
              <span style="color:#aaa;font-size:11px">No fail die data</span>
            </div>
          </div>
          <div class="wm-pat-tabpane" id="wm-pat-pane-composite2">
            <div id="wm-pat-modemap-body2" style="padding:8px;overflow:auto;flex:1;display:flex;align-items:flex-start;justify-content:center">
              <span style="color:#999;font-size:11px">Select wafers to view composite map.</span>
            </div>
          </div>
          {reticle_tab_pane}
          <div class="wm-pat-tabpane" id="wm-pat-pane-guide">
            {guide_html}
          </div>
          <div class="wm-pat-scores-resize" id="wm-pat-scores-resize"></div>
          <div class="wm-pat-scores" id="wm-pat-scores-panel" style="height:180px">
            <div style="flex-shrink:0;border-bottom:2px solid #1a5276;background:#eaf4fb">
              <div id="wm-pat-lot-trend" style="overflow-x:auto;max-height:72px;padding:2px 4px"></div>
            </div>
            <div class="wm-pat-tbl-wrap">
              <table class="wm-t"><thead><tr>
                <th>Lot</th><th>Wafer</th><th>Material</th><th>Primary</th>
                <th>Conf.</th><th>Fail%</th><th>Driver IB</th>
                <th>Center</th><th>Edge</th><th>Donut</th><th>Systematic</th>
                {reticle_th}
                <th>Random</th>
              </tr></thead>
              <tbody id="wm-pat-tbody"></tbody></table>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>"""

        # Trigger button / open hook
        if trigger_id:
            btn_html = (
                f'<script>document.getElementById("{trigger_id}")'
                f'.addEventListener("click",wmOpenPat);</script>'
            )
        else:
            btn_html = (
                f'<button id="wm-pat-open-btn" class="wm-pat-btn" onclick="wmOpenPat()">'
                f'{btn_label}</button>'
            )

        # ── Script ──
        # WM_PAT must be defined before the JS functions so it's available at call time.
        # _wmFailThr / _wmEdgeExcRows are set AFTER WPA_FULL_JS to override the JS defaults.
        init_js = f'var WM_PAT={wm_pat_json};\n'

        post_init_js = (
            f'_wmFailThr={self._fail_thr};'
            f'_wmEdgeExcRows={self._edge_exclude_rows};'
            '(function(){'
            'function _wpaInit(){_wmPatBuildLotPicker();_wmPatRender();_wmPatInitDrag();}'
            'if(document.readyState==="loading"){'
            'window.addEventListener("DOMContentLoaded",_wpaInit);'
            '}else{_wpaInit();}'
            '})();'
        )

        script_block = (
            f'<script>\n'
            f'{init_js}'
            f'{WPA_FULL_JS}\n'
            f'{post_init_js}\n'
            f'</script>'
        )

        body = (
            css_block
            + '\n'
            + overlay_html
            + '\n'
            + script_block
            + '\n'
            + btn_html
        )

        if standalone:
            return (
                '<!DOCTYPE html><html><head>'
                '<meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>Wafer Pattern Analysis</title>'
                '</head><body style="margin:0;padding:16px;font-family:Arial,sans-serif">'
                + body
                + '</body></html>'
            )
        return body
