"""
NVL816 Dashboard Automation — Unified GUI
==========================================
Single window with three tabs: Yield / Scan / VMIN.
Each tab embeds its own AutomationManager panel.

Run:
    python manage_all.py
    python manage_all.py --yield-dir <path> --scan-dir <path> --vmin-dir <path>
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tkinter as tk
import tkinter.ttk as ttk
import types
from pathlib import Path

_HERE = Path(__file__).resolve().parent   # utilities/automation/

# ── shared colours (same as each sub-module) ─────────────────────────────────
BG     = "#1a252f"
BG2    = "#1e2e3d"
BG3    = "#263950"
FG     = "#e8f0f7"
FG_DIM = "#90a4ae"
ACCENT = "#4fc3f7"

FONT_TITLE = ("Segoe UI", 12, "bold")
FONT_TAB   = ("Segoe UI", 10, "bold")


# ── dynamic module loader ─────────────────────────────────────────────────────
def _load(ns: str, path: Path) -> types.ModuleType:
    """Load manage_automation.py from a sub-folder without it being a package."""
    spec = importlib.util.spec_from_file_location(ns, path)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[ns] = mod
    spec.loader.exec_module(mod)   # type: ignore[union-attr]
    return mod


_scan_mod  = _load("_mgr_scan",  _HERE / "scan"  / "src" / "manage_automation.py")
_yield_mod = _load("_mgr_yield", _HERE / "yield" / "src" / "manage_automation.py")
_vmin_mod  = _load("_mgr_vmin",  _HERE / "vmin"  / "src" / "manage_automation.py")
_class_mod = _load("_mgr_class", _HERE / "class" / "src" / "manage_automation.py")
_trend_mod = _load("_mgr_trend", _HERE / "trend" / "src" / "manage_automation.py")


# ── unified window ────────────────────────────────────────────────────────────
class AllAutomationManager(tk.Tk):

    def __init__(
        self,
        yield_dir: Path | None = None,
        scan_dir:  Path | None = None,
        vmin_dir:  Path | None = None,
        class_dir: Path | None = None,
        trend_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.title("NVL816 Dashboard Automation")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(860, 600)
        self.geometry("1060x740")

        self._apply_styles()

        # ── header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG3)
        hdr.pack(fill="x")
        tk.Label(hdr, text="NVL816 Dashboard Automation",
                 font=FONT_TITLE, bg=BG3, fg=ACCENT
                 ).pack(side="left", padx=14, pady=8)

        # ── top-level notebook ────────────────────────────────────────────────
        outer_nb = ttk.Notebook(self, style="Outer.TNotebook")
        outer_nb.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # Yield tab
        tab_yield = tk.Frame(outer_nb, bg=BG)
        outer_nb.add(tab_yield, text="  📊 Yield  ")
        _yield_mod.AutomationManager(
            tab_yield,
            yield_dir or _yield_mod._BASE_DIR,
        ).pack(fill="both", expand=True)

        # Scan tab
        tab_scan = tk.Frame(outer_nb, bg=BG)
        outer_nb.add(tab_scan, text="  🔬 Scan  ")
        _scan_mod.AutomationManager(
            tab_scan,
            scan_dir or _scan_mod._BASE_DIR,
        ).pack(fill="both", expand=True)

        # VMIN tab
        tab_vmin = tk.Frame(outer_nb, bg=BG)
        outer_nb.add(tab_vmin, text="  ⚡ VMIN  ")
        _vmin_mod.AutomationManager(
            tab_vmin,
            vmin_dir or _vmin_mod._BASE_DIR,
        ).pack(fill="both", expand=True)

        # CLASS tab
        tab_class = tk.Frame(outer_nb, bg=BG)
        outer_nb.add(tab_class, text="  🏭 CLASS  ")
        _class_mod.AutomationManager(
            tab_class,
            class_dir or _class_mod._BASE_DIR,
        ).pack(fill="both", expand=True)

        # Trend tab
        tab_trend = tk.Frame(outer_nb, bg=BG)
        outer_nb.add(tab_trend, text="  📈 Trend  ")
        _trend_mod.AutomationManager(
            tab_trend,
            trend_dir or _trend_mod._BASE_DIR,
        ).pack(fill="both", expand=True)

    def _apply_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        # Outer tabs (Yield / Scan / VMIN)
        style.configure("Outer.TNotebook",
                        background=BG3, borderwidth=0, tabmargins=[0, 0, 0, 0])
        style.configure("Outer.TNotebook.Tab",
                        background=BG3, foreground=FG_DIM,
                        padding=[18, 7], font=FONT_TAB)
        style.map("Outer.TNotebook.Tab",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", BG)])
        # Inner tabs (shared TNotebook used by sub-modules)
        style.configure("TNotebook",     background=BG,  borderwidth=0)
        style.configure("TNotebook.Tab", background=BG3, foreground=FG_DIM,
                        padding=[12, 5], font=("Segoe UI", 9))
        style.map("TNotebook.Tab",
                  background=[("selected", BG2)],
                  foreground=[("selected", ACCENT)])
        # Treeview
        style.configure("Treeview",         background=BG2, foreground=FG,
                        fieldbackground=BG2, rowheight=22, font=("Consolas", 9))
        style.configure("Treeview.Heading", background=BG3, foreground=ACCENT,
                        relief="flat", font=("Segoe UI", 9))
        style.map("Treeview",
                  background=[("selected", BG3)],
                  foreground=[("selected", ACCENT)])
        style.configure("TScrollbar",  background=BG3, troughcolor=BG,
                        arrowcolor=FG_DIM, borderwidth=0)
        style.configure("TSpinbox",    fieldbackground=BG2, foreground=FG,
                        background=BG3, arrowcolor=ACCENT, font=("Consolas", 9))


# ── entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="NVL816 Dashboard Automation — unified GUI")
    ap.add_argument("--yield-dir", default=None, help="Yield automation base dir")
    ap.add_argument("--scan-dir",  default=None, help="Scan  automation base dir")
    ap.add_argument("--vmin-dir",  default=None, help="VMIN  automation base dir")
    ap.add_argument("--class-dir", default=None, help="CLASS automation base dir")
    ap.add_argument("--trend-dir", default=None, help="Trend automation base dir")
    args = ap.parse_args()

    AllAutomationManager(
        yield_dir=Path(args.yield_dir) if args.yield_dir else None,
        scan_dir =Path(args.scan_dir)  if args.scan_dir  else None,
        vmin_dir =Path(args.vmin_dir)  if args.vmin_dir  else None,
        class_dir=Path(args.class_dir) if args.class_dir else None,
        trend_dir=Path(args.trend_dir) if args.trend_dir else None,
    ).mainloop()


if __name__ == "__main__":
    main()
