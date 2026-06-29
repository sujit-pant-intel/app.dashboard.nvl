"""
_loader.py — script dispatcher for compiled .pyd modules.

Usage:
    python _loader.py <module_name> [args...]

Imports the named module (which may be a .pyd binary) and runs its main()
or __main__ block, passing remaining args as sys.argv.

This lets all subprocess calls use:
    [sys.executable, _LOADER, 'bin_distribution_html', arg1, arg2]
instead of:
    [sys.executable, 'bin_distribution_html.py', arg1, arg2]
so the .py files can be compiled to .pyd and deleted.
"""
import sys
sys.dont_write_bytecode = True
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import importlib
import runpy
from pathlib import Path

# ── Samba/SMB WinError 183 patch ─────────────────────────────────────────────
# On Windows UNC (\\server\share) paths Python 3.14 pathlib.mkdir raises
# FileExistsError(WinError 183) even with exist_ok=True because the SMB client
# stat cache is stale after a recent rmtree.  Patch Path.mkdir at import time
# so all code in this process silently ignores ERROR_ALREADY_EXISTS.
_orig_path_mkdir = Path.mkdir
def _patched_path_mkdir(self, mode=0o777, parents=False, exist_ok=False):
    try:
        _orig_path_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)
    except OSError as _e:
        if getattr(_e, 'winerror', None) != 183:
            raise
        # WinError 183 = ERROR_ALREADY_EXISTS from stale SMB client cache.
        # Verify the directory actually exists; if not, force-create it.
        if not os.path.isdir(str(self)):
            os.makedirs(str(self), exist_ok=True)
Path.mkdir = _patched_path_mkdir
# Also patch os.makedirs for code that uses it directly.
_orig_os_makedirs = os.makedirs
def _patched_os_makedirs(name, mode=0o777, exist_ok=False):
    try:
        _orig_os_makedirs(name, mode=mode, exist_ok=exist_ok)
    except OSError as _e:
        if getattr(_e, 'winerror', None) != 183:
            raise
        if not os.path.isdir(str(name)):
            _orig_os_makedirs(name, mode=mode, exist_ok=True)
os.makedirs = _patched_os_makedirs
# ─────────────────────────────────────────────────────────────────────────────

# Ensure the directory containing this script is on sys.path
_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

# Also add sibling src/ directories (e.g. vmin/src, sicc_cdyn_upm/src)
# so modules like run_vmin can be found when dispatched via _loader
_run_root = _here.parent.parent  # up from <module>/src/ to run root
for _src_dir in _run_root.glob('*/src'):
    if _src_dir.is_dir() and str(_src_dir) not in sys.path:
        sys.path.insert(0, str(_src_dir))

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: _loader.py <module_name> [args...]', file=sys.stderr)
        sys.exit(1)

    mod_name = sys.argv[1]
    sys.argv = [mod_name] + sys.argv[2:]

    try:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, 'main'):
            mod.main()
        else:
            runpy.run_module(mod_name, run_name='__main__', alter_sys=True)
    except SystemExit:
        raise
