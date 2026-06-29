"""_constants.py — shared module-level constants for the etest/PCM dashboard."""
import os
import sys

_SRC_DIR  = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_SRC_DIR, '..'))

_FROZEN = getattr(sys, 'frozen', False)
_PYTHON = sys.executable if not _FROZEN else None
_LOADER = os.path.join(_SRC_DIR, '_loader.py')

# ---------------------------------------------------------------------------
# Watermark injected into every generated HTML page.
# ---------------------------------------------------------------------------
_WM_HTML = (
    '<div id="_wm_div" style="position:fixed;top:8px;right:12px;font-size:10px;'
    'font-weight:600;pointer-events:none;z-index:99999;'
    'font-family:Arial,sans-serif;user-select:none;letter-spacing:0.04em;'
    'padding:2px 6px;border-radius:3px;background:transparent;color:rgba(255,255,255,0.95);">'
    'Pant, Sujit N \u2014 GEMS FTE</div>'
    '<script>(function(){'
    'var _all=document.querySelectorAll("#_wm_div");'
    'for(var _i=0;_i<_all.length-1;_i++){_all[_i].remove();}'
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
    """Strip any existing watermark then inject a single clean one before </body>."""
    import re as _re_wm
    if '</body>' not in html:
        return html
    html = _re_wm.sub(
        r'<div[^>]*id=["\']_wm_div["\'][^>]*>[\s\S]*?</div>\s*<script[^>]*>[\s\S]*?</script>',
        '', html)
    html = _re_wm.sub(r'<div[^>]*>[^<]*GEMS FTE[^<]*</div>', '', html)
    return html.replace('</body>', _WM_HTML + '\n</body>', 1)


# ---------------------------------------------------------------------------
# ZIP-aware file utilities
# Used by pcm_merge_gui.py and pcm_dashboard_frame.py to read PCM CSVs
# stored inside .zip archives in the 9-sites / full-sites directories.
#
# Zip references are encoded as:  "C:\path\to\archive.zip::member/path.csv"
# The "::" separator is safe on both Windows and POSIX paths.
# ---------------------------------------------------------------------------
import zipfile as _zipfile_mod

_ZIP_SEP = "::"


def _zip_basename(p: str) -> str:
    """Like os.path.basename but handles zip references (archive.zip::member)."""
    if _ZIP_SEP in p:
        return p.split(_ZIP_SEP, 1)[1].rsplit("/", 1)[-1]
    return os.path.basename(p)


def _zip_isfile(p: str) -> bool:
    """Like os.path.isfile but handles zip references (archive.zip::member)."""
    if _ZIP_SEP in p:
        zip_path, member = p.split(_ZIP_SEP, 1)
        if not os.path.isfile(zip_path):
            return False
        try:
            with _zipfile_mod.ZipFile(zip_path, "r") as _zf:
                return member in _zf.namelist()
        except Exception:
            return False
    return os.path.isfile(p)


def _walk_dir_and_zips(d: str):
    """Yield (fname, path) for every file under directory *d* (recursive).

    For regular files:  path = absolute filesystem path.
    For zip entries:    path = 'archive_abspath::member_path'  (a zip reference).

    Any .zip file found while walking is also opened and its members yielded,
    allowing callers to treat zip-contained CSVs as if they were plain files.
    """
    if not os.path.isdir(d):
        return
    for root, _dirs, files in os.walk(d):
        for fname in files:
            full = os.path.join(root, fname)
            if fname.lower().endswith(".zip"):
                try:
                    with _zipfile_mod.ZipFile(full, "r") as _zf:
                        for member in _zf.namelist():
                            if member.endswith("/"):
                                continue  # skip directory entries
                            mfname = member.rsplit("/", 1)[-1]
                            yield mfname, full + _ZIP_SEP + member
                except Exception:
                    pass
            else:
                yield fname, full


def _read_csv(path: str, **kwargs):
    """pandas.read_csv wrapper that transparently handles zip references.

    If *path* is a zip reference ('archive.zip::member'), the member is
    opened directly from the archive without extracting it to disk.
    All keyword arguments are forwarded to pandas.read_csv unchanged.
    """
    import pandas as _pd_csv
    if _ZIP_SEP in path:
        zip_path, member = path.split(_ZIP_SEP, 1)
        with _zipfile_mod.ZipFile(zip_path, "r") as _zf:
            with _zf.open(member) as _fh:
                return _pd_csv.read_csv(_fh, **kwargs)
    return _pd_csv.read_csv(path, **kwargs)
