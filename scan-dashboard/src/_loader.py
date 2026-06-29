"""
_loader.py — script dispatcher for compiled .pyd modules.

Usage:
    python _loader.py <module_name> [args...]

Imports the named module (which may be a .pyd binary) and runs its main()
or __main__ block, passing remaining args as sys.argv.

This lets all subprocess calls use:
    [sys.executable, _LOADER, 'some_module', arg1, arg2]
instead of:
    [sys.executable, 'some_module.py', arg1, arg2]
so the .py files can be compiled to .pyd and deleted.
"""
import sys
sys.dont_write_bytecode = True
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import importlib
import runpy
from pathlib import Path

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

# Re-assert _here at position 0 so this module's own pipeline.py wins
# over any same-named module found in sibling src/ directories.
if str(_here) in sys.path:
    sys.path.remove(str(_here))
sys.path.insert(0, str(_here))

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
        pass
