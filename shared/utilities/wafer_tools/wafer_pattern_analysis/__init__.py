"""
wafer_pattern — shared WPA (Wafer Pattern Analysis) module.

Public API::

    from wafer_pattern import (
        score_wafer,           # pure-Python pattern scoring
        score_wafer_reticle,   # reticle-correlated score
        WaferPattern,          # result dataclass
        PATTERN_COLORS,        # dict[str, str] — hex colours per pattern name
        WpaHtmlBuilder,        # build self-contained WPA HTML
    )
"""

from .scorer import score_wafer, score_wafer_reticle, WaferPattern, PATTERN_COLORS
from .html_builder import WpaHtmlBuilder

__all__ = [
    'score_wafer',
    'score_wafer_reticle',
    'WaferPattern',
    'PATTERN_COLORS',
    'WpaHtmlBuilder',
]
