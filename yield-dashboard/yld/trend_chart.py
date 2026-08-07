"""trend_chart.py — shim that invokes the trend-chart main() from yield_trend.py.

yield_trend.py contains multiple merged sections each with their own
if __name__ == '__main__': block.  Running yield_trend.py directly as a script
triggers all of them in sequence (compare_runs fires first and exits 1).
Importing it as a module avoids that: __name__ != '__main__', so none of the
__main__ guards fire, and we can call just the trend-chart main().
"""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_yield_trend_mod",
    Path(__file__).resolve().parent / "yield_trend.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)   # __name__ == '_yield_trend_mod' — no __main__ blocks run

# The last main() defined in yield_trend.py belongs to the trend-chart section.
_mod.main()
