"""
wafer_map — lightweight SVG wafer-map renderer
===============================================
Exports a single JS string that injects ``wmRender(containerId, cfg)``
into any HTML page.  See wafermap.md for the full API reference.
"""

from ._renderer_js import WAFERMAP_JS

__all__ = ["WAFERMAP_JS"]
