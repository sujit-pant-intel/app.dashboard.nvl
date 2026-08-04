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
"""
import importlib.util
import logging
import sys
from datetime import datetime as _datetime_cls
from pathlib import Path

_YA = Path(__file__).resolve().parent / "yield_automation.py"

spec = importlib.util.spec_from_file_location("_yield_auto", str(_YA))
mod  = importlib.util.module_from_spec(spec)
# Pre-inject modules missing from sections that relied on earlier-section imports
mod.__dict__["logging"] = logging
spec.loader.exec_module(mod)   # __name__ != "__main__" → all guards skipped

# serve_reports section (line ~8050) does `import datetime` (module), overwriting
# the `from datetime import datetime` (class) that run_automation's main() needs
mod.__dict__["datetime"] = _datetime_cls

mod.main()   # automation orchestrator (last def main() in the file)
