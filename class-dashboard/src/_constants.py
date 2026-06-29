"""_constants.py — shared path constants and watermark for class-dashboard."""
from __future__ import annotations
import os
import re as _re

_SRC_DIR  = os.path.dirname(os.path.abspath(__file__))
_DASH_DIR = os.path.normpath(os.path.join(_SRC_DIR, '..'))   # class-dashboard/


def _find_repo_root(start: str) -> str:
    """Walk up from *start* until a sibling 'shared/' folder is found."""
    _override = os.environ.get('APP_YIELD_NVL_ROOT', '').strip()
    if _override and os.path.isdir(_override):
        return os.path.normpath(_override)
    current = os.path.abspath(start)
    for _ in range(12):
        if os.path.isdir(os.path.join(current, 'shared')):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return os.path.normpath(os.path.join(start, '..', '..', '..', '..'))


_REPO_ROOT    = _find_repo_root(_SRC_DIR)
_RETICLE_DIR  = os.path.join(_REPO_ROOT, 'shared', 'reticle')
_MATERIAL_DIR = os.path.join(_REPO_ROOT, 'shared', 'material')

# Default product config location (class-specific) — lives in shared/setup/class-dashboard
_DEFAULT_SETUP_DIR = os.path.join(_REPO_ROOT, 'shared', 'setup', 'config', 'class-dashboard')

# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------
_WM_HTML = (
    '<div id="_wm_div" style="position:fixed;top:8px;right:12px;font-size:10px;'
    'font-weight:600;pointer-events:none;z-index:99999;'
    'font-family:Arial,sans-serif;user-select:none;letter-spacing:0.04em;'
    'padding:2px 6px;border-radius:3px;background:transparent;color:rgba(255,255,255,0.95);">'
    'Pant, Sujit N \u2014 Subramaniam, Sangkeetha \u2014 GEMS FTE</div>'
    '<script>(function(){'
    'var _all=document.querySelectorAll("#_wm_div");'
    'for(var _i=0;_i<_all.length-1;_i++){_all[_i].remove();}'
    'if(window!==window.top){var _d=document.getElementById("_wm_div");if(_d)_d.style.display="none";return;}'
    'function _wm_color(){'
    'var d=document.getElementById("_wm_div");if(!d)return;'
    'd.style.color="rgba(255,255,255,0.95)";'
    '}'
    'if(document.readyState==="loading")'
    '{document.addEventListener("DOMContentLoaded",_wm_color);}'
    'else{_wm_color();}'
    '})();</script>'
)


def _wm_inject(html: str) -> str:
    """Strip any prior watermark and inject one fresh copy before </body>."""
    if '</body>' not in html:
        return html
    html = _re.sub(
        r'<div[^>]*id=["\']_wm_div["\'][^>]*>[\s\S]*?</div>\s*<script[^>]*>[\s\S]*?</script>',
        '', html)
    html = _re.sub(r'<div[^>]*>[^<]*GEMS FTE[^<]*</div>', '', html)
    return html.replace('</body>', _WM_HTML + '\n</body>', 1)
