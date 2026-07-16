"""_tab_registry.py — Tab dataclass and TABS list.

To add a new tab (e.g. vmin):
  1. Create _tab_vmin.py providing:
       TAB_ID = 'tab-vmin'
       TAB_LABEL = 'VMin'
       TAB_ACTIVE = False
       def tab_html() -> str: ...
       def tab_js() -> str: ...
  2. Import and append here:
       from _tab_vmin import build_tab as TAB_VMIN
       TABS.append(TAB_VMIN)
  3. Pass any new data keys in sicc_processor.py -> process_csv() return dict.
  4. Done — no existing code touched.
"""
from dataclasses import dataclass
from typing import Callable, List


@dataclass
class Tab:
    tab_id: str            # HTML element id, e.g. "tab-sicc"
    label: str             # Button label shown in tabs bar
    active: bool           # True = initially active tab
    html_fn: Callable[[], str]   # Returns the <div id="tab-X"> panel HTML
    js_fn: Callable[[], str]     # Returns JS functions for this tab


# --- Import tab modules and register them ----------------------------------
from _tab_sicc   import build_tab as TAB_SICC    # noqa: E402

TABS: List[Tab] = [TAB_SICC]
