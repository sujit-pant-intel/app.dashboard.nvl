"""
_loader.py — script dispatcher for compiled .pyd modules.

Usage:
    python _loader.py <module_name> [args...]

Imports the named module (which may be a .pyd binary) and runs its main()
or __main__ block, passing remaining args as sys.argv.

This lets all subprocess calls use:
    [sys.executable, _LOADER, 'generate_pcm_html', arg1, arg2]
instead of:
    [sys.executable, 'generate_pcm_html.py', arg1, arg2]
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
