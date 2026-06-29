"""
run_dashboard.py
----------------
Entry-point wrapper for the compiled dashboard .pyd.
PyInstaller bundles all .pyd files and packages — end users need nothing installed.
"""

import sys
sys.dont_write_bytecode = True
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import multiprocessing
from pathlib import Path


def _resource_dir() -> Path:
    """Return the folder where bundled resources (.pyd files) live."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


_HERE = _resource_dir()
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _route_script(script_path: str, args: list) -> None:
    """When frozen and called as `exe script.py arg1 arg2 ...`, import the
    matching bundled module and call its main(), forwarding argv so that
    argparse / sys.argv-based scripts work unchanged.
    Falls back to runpy.run_module(..., run_name='__main__') for scripts
    that have no main() but use `if __name__ == '__main__':` guards."""
    import importlib
    import runpy
    name = Path(script_path).stem          # e.g. 'generate_placeholder_png'
    sys.argv = [script_path] + args        # restore argv as the script expects
    try:
        mod = importlib.import_module(name)
        if hasattr(mod, 'main'):
            mod.main()
        else:
            # No main() — execute the __main__ block via runpy
            runpy.run_module(name, run_name='__main__', alter_sys=True)
    except SystemExit:
        pass   # normal exit from argparse --help etc.


def main():
    multiprocessing.freeze_support()

    # Script-router: if a .py path is passed as the first argument, dispatch
    # to that module rather than showing the GUI.  This makes all
    #   subprocess.run([sys.executable, 'some_helper.py', ...])
    # calls inside the codebase work correctly when frozen.
    if (getattr(sys, 'frozen', False)
            and len(sys.argv) > 1
            and sys.argv[1].endswith('.py')):
        try:
            _route_script(sys.argv[1], sys.argv[2:])
        except Exception as _e:
            import traceback
            print(f'Script router error: {_e}', file=sys.__stderr__)
            traceback.print_exc(file=sys.__stderr__)
        sys.exit(0)   # ALWAYS exit — never fall through to GUI

    import dashboard
    dashboard.start_opener_server()
    dashboard.DashboardApp().mainloop()


if __name__ == '__main__':
    main()
