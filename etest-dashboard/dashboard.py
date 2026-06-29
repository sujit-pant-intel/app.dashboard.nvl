import os, sys
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR    = os.path.join(_SCRIPT_DIR, "src")
_LOADER     = os.path.join(_SRC_DIR, "_loader.py")   # dispatches to compiled .pyd modules
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from pcm_merge_gui import PCMMergeGUI

if __name__ == "__main__":
    # Usage:
    #   python dashboard.py                          ← simple mode (input + output only)
    #   python dashboard.py -d                       ← advanced mode (all options visible)
    #   python dashboard.py C:/work/etest/input.json ← simple + pre-load config
    #   python dashboard.py -d C:/work/etest/input.json
    import json as _json

    args = sys.argv[1:]
    advanced = "-d" in args or "--debug" in args or "--advanced" in args
    args = [a for a in args if a not in ("-d", "--debug", "--advanced")]
    _json_arg = args[0] if args and args[0].endswith(".json") else ""

    app = PCMMergeGUI(advanced=advanced)
    if _json_arg and os.path.isfile(_json_arg):
        with open(_json_arg, "r", encoding="utf-8") as _fh:
            app._apply_config(_json.load(_fh))
    app.mainloop()
