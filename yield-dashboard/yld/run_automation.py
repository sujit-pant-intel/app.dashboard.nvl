"""
run_automation.py
-----------------
Task Scheduler entry point for the yield automation pipeline.

yield_automation.py is a concatenated mega-file whose first
if __name__ == "__main__" block belongs to a comparison tool
(requires a dashboard path arg) — it exits before the real
automation orchestrator main() at line ~7212 ever runs.

Importing as a module skips all __main__ guards; the last
defined main() is the automation orchestrator.

Some sections in the mega-file use modules (e.g. logging) that were
imported in the original separate scripts but weren't re-added after
concatenation. Pre-inject them so exec_module doesn't NameError on them.

Usage (same args as the orchestrator):
    python run_automation.py [--force] [--keys ...] [--local-csv ...]

All stdout/stderr (including uncaught tracebacks, which Task Scheduler
otherwise swallows silently) is tee'd to logs/run_YYYYMMDD_HHMMSS.log
next to this script so failures are diagnosable after the fact.
"""
import importlib.util
import io
import logging
import re
import sys
import traceback
from datetime import datetime as _datetime_cls
from pathlib import Path

# Reconfigure console streams to UTF-8 so Unicode log chars never crash the process
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

_HERE = Path(__file__).resolve().parent
_YA   = _HERE / "yield_automation.py"


class _Tee:
    """Mirror writes to multiple streams (console + log file)."""
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)

    def flush(self):
        for s in self._streams:
            s.flush()


def _product_label() -> str:
    if "--product-name" in sys.argv:
        idx = sys.argv.index("--product-name")
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return "default"


_logs_dir  = _HERE / "logs"
_logs_dir.mkdir(parents=True, exist_ok=True)
_safe_label = re.sub(r'[^\w]', '_', _product_label())
_log_path   = _logs_dir / f"run_{_safe_label}_{_datetime_cls.now().strftime('%Y%m%d_%H%M%S')}.log"
_log_fh     = open(_log_path, "w", encoding="utf-8")
sys.stdout  = _Tee(sys.stdout, _log_fh)
sys.stderr  = _Tee(sys.stderr, _log_fh)

spec = importlib.util.spec_from_file_location("_yield_auto", str(_YA))
mod  = importlib.util.module_from_spec(spec)
# Pre-inject modules missing from sections that relied on earlier-section imports
mod.__dict__["logging"] = logging
spec.loader.exec_module(mod)   # __name__ != "__main__" → all guards skipped

# serve_reports section (line ~8050) does `import datetime` (module), overwriting
# the `from datetime import datetime` (class) that run_automation's main() needs
mod.__dict__["datetime"] = _datetime_cls

try:
    mod.main()   # automation orchestrator (last def main() in the file)
except SystemExit:
    raise
except Exception:
    traceback.print_exc()
    sys.exit(1)
finally:
    _log_fh.close()
