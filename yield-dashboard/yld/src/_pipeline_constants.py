"""_pipeline_constants.py — shared module-level constants for pipeline mixins."""
import os
import sys

_SRC_DIR  = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_SRC_DIR, '..'))

_FROZEN = getattr(sys, 'frozen', False)
_PYTHON = sys.executable if not _FROZEN else None
_LOADER = os.path.join(_SRC_DIR, '_loader.py')

SICC_UPM_SCRIPT      = os.path.normpath(os.path.join(_ROOT_DIR, '..', 'sicc_upm', 'src', 'run_dashboard.py'))
SICC_CDYN_UPM_SCRIPT = os.path.normpath(os.path.join(_ROOT_DIR, '..', 'sicc_cdyn_upm', 'src', 'run_dashboard.py'))

# Central Product Config JSON directory
# _REPO_ROOT: resolved by walking up from _SRC_DIR until a 'shared/' sibling is
# found.  This works both in the source repo and in deployed layouts (where the
# code lives under run/ and shared/ sits next to run/).
# Override completely by setting env-var APP_YIELD_NVL_ROOT to the repo/share root
# — useful when the shared drive is on a network path unrelated to the deploy folder.
def _find_repo_root(start: str) -> str:
    _override = os.environ.get('APP_YIELD_NVL_ROOT', '').strip()
    if _override and os.path.isdir(_override):
        return os.path.normpath(_override)
    current = os.path.abspath(start)
    for _ in range(12):
        if os.path.isdir(os.path.join(current, 'shared')):
            return current
        parent = os.path.dirname(current)
        if parent == current:   # filesystem root
            break
        current = parent
    # Fallback: fixed 5-level walk (original repo layout)
    return os.path.normpath(os.path.join(start, '..', '..', '..', '..', '..'))

_REPO_ROOT = _find_repo_root(_SRC_DIR)
# Prefer the new canonical location; fall back to the legacy path
_PROD_CFG_DIR = (
    os.path.join(_REPO_ROOT, 'shared', 'setup', 'config', 'yield-dashboard')
    if os.path.isdir(os.path.join(_REPO_ROOT, 'shared', 'setup', 'config', 'yield-dashboard'))
    else os.path.join(_REPO_ROOT, 'shared', 'spec', 'collateral', 'yield')
)

# PCM product setup JSON (defines parameter groups and patterns)
_PCM_SETUP_JSON = next(
    (p for p in [
        os.path.join(_REPO_ROOT, 'shared', 'setup', 'etest-dashboard', 'pcm_product_setup.json'),
        os.path.join(_REPO_ROOT, 'shared', 'spec', 'collateral', 'etest', 'pcm_product_setup.json'),
        os.path.join(_REPO_ROOT, 'shared', 'etest', 'collateral', 'pcm_product_setup.json'),
        os.path.join(_REPO_ROOT, 'shared', 'etest', 'spec', 'pcm_product_setup.json'),
    ] if os.path.isfile(p)),
    None,
)

# Fixed-position watermark injected into every generated HTML page.
WM_HTML = (
    '<div id="_wm_div" style="position:fixed;top:8px;right:12px;font-size:10px;'
    'font-weight:600;pointer-events:none;z-index:99999;'
    'font-family:Arial,sans-serif;user-select:none;letter-spacing:0.04em;'
    'padding:2px 6px;border-radius:3px;background:transparent;">'
    'Pant, Sujit N \u2014 GEMS FTE</div>'
    '<script>(function(){'
    'if(window!==window.top){var _d=document.getElementById("_wm_div");if(_d)_d.style.display="none";return;}'
    'function _wm_color(){'
    'var d=document.getElementById("_wm_div");if(!d)return;'
    'var bg=window.getComputedStyle(document.body).backgroundColor;'
    'var m=bg.match(/\\d+/g);'
    'if(m&&m.length>=3){'
    'var r=+m[0],g=+m[1],b=+m[2];'
    'var lum=0.299*r+0.587*g+0.114*b;'
    'd.style.color=lum<128?"rgba(255,255,255,0.9)":"rgba(20,20,20,0.75)";'
    '}else{d.style.color="rgba(255,255,255,0.9)";}'
    '}'
    'if(document.readyState==="loading")'
    '{document.addEventListener("DOMContentLoaded",_wm_color);}'
    'else{_wm_color();}'
    '})();</script>'
)


def _wm_inject(html: str) -> str:
    """Strip any existing watermark then inject a single clean one before </body>."""
    import re as _re_wm
    if '</body>' not in html:
        return html
    # Remove any previously injected watermark (both old bare-div and new div+script forms)
    html = _re_wm.sub(
        r'<div[^>]*id=["\']_wm_div["\'][^>]*>[\s\S]*?</div>\s*<script[^>]*>[\s\S]*?</script>',
        '', html)
    html = _re_wm.sub(r'<div[^>]*>[^<]*GEMS FTE[^<]*</div>', '', html)
    return html.replace('</body>', WM_HTML + '\n</body>', 1)
