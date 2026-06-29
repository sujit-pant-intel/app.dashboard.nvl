"""pcm_param_analysis_frame.py — Parameter Analysis tab for the ETest Dashboard.

Loads PCM lot data, applies lot / wafer / material filters, then produces:
  • Executive Summary  — one row per parameter, coloured PASS / MARGINAL / FAIL / NO SPEC
  • Per-Lot/Wafer Detail — click any parameter to see a breakdown by Lot × Wafer × Material
"""

from __future__ import annotations

import math
import os
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from _constants import _walk_dir_and_zips, _read_csv, _zip_basename

# Re-use helpers from pcm_dashboard_frame (no circular imports — different class)
from pcm_dashboard_frame import (
    _scan_lots,
    _load_and_merge,
    _load_spec_lookup,
    _get_lot_layout,
    _get_lot_material,
    _find_repo_root,
)

_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = _find_repo_root(_HERE)

# Columns that identify a die/site, not a measured parameter
_PCM_ID_COLS = {
    "Technology", "Layout", "Lot", "Wafer", "TestProgram", "TestProgramVersion",
    "Fab", "Step", "Equipment", "EquipmentType", "TestDateTime", "TestDate",
    "TimeLoaded", "WaferResultID", "Site", "LayoutX", "LayoutY", "Map", "MapID",
    "ReticleShotRadius", "Material", "_ml7", "_mwid",
}

# ── Thresholds ────────────────────────────────────────────────────────────────
_THRESH_FAIL     = 5.0   # %fail ≥ this → FAIL
_THRESH_MARGINAL = 0.0   # %fail > 0    → MARGINAL


# ─────────────────────────────────────────────────────────────────────────────
# Stats helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_v(v, pct: bool = False) -> str:
    """Format a float nicely; return '—' for NaN."""
    if isinstance(v, float) and math.isnan(v):
        return "—"
    if pct:
        return f"{v:.1f}%"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def _param_stats(series: "pd.Series", sl: float, sh: float) -> tuple:
    """
    Compute (n, median, p1, p99, pct_fail_low, pct_fail_high, pct_fail) for *series*.
    Returns (0, nan×6) if the series has no non-null values.
    """
    nan = float("nan")
    vals = series.dropna()
    n = len(vals)
    if n == 0:
        return 0, nan, nan, nan, nan, nan, nan

    arr  = vals.to_numpy(dtype=float)
    med  = float(np.median(arr))
    p1   = float(np.percentile(arr, 1))
    p99  = float(np.percentile(arr, 99))

    n_lo   = int((arr < sl).sum()) if not math.isnan(sl) else 0
    n_hi   = int((arr > sh).sum()) if not math.isnan(sh) else 0
    n_fail = n_lo + n_hi

    pct_lo   = 100.0 * n_lo   / n if (n > 0 and not math.isnan(sl)) else nan
    pct_hi   = 100.0 * n_hi   / n if (n > 0 and not math.isnan(sh)) else nan
    pct_fail = 100.0 * n_fail / n if n > 0 else nan

    return n, med, p1, p99, pct_lo, pct_hi, pct_fail


def _status_from_pct(pct_fail: float, has_spec: bool) -> str:
    if not has_spec:
        return "NO SPEC"
    if math.isnan(pct_fail):
        return "—"
    if pct_fail >= _THRESH_FAIL:
        return "FAIL"
    if pct_fail > _THRESH_MARGINAL:
        return "MARGINAL"
    return "PASS"


_STATUS_ORDER = {"FAIL": 0, "MARGINAL": 1, "PASS": 2, "NO SPEC": 3, "—": 4}
_TAG_MAP      = {"FAIL": "fail", "MARGINAL": "marginal", "PASS": "pass",
                 "NO SPEC": "nospec", "—": "nospec"}


# ─────────────────────────────────────────────────────────────────────────────
# Frame
# ─────────────────────────────────────────────────────────────────────────────

class PCMParamAnalysisFrame(ttk.Frame):
    """Tkinter frame for the Parameter Analysis tab."""

    # Dark palette (matches the rest of the app)
    BG       = "#1e1e2e"
    PANEL    = "#2a2a3e"
    ACCENT   = "#7c6af7"
    FG       = "#cdd6f4"
    ENTRY_BG = "#313244"
    BTN_RUN  = "#a6e3a1"
    BTN_FG   = "#1e1e2e"
    SECTION  = "#89b4fa"
    DESC_FG  = "#a6adc8"
    ERR_FG   = "#f38ba8"
    OK_FG    = "#a6e3a1"
    WARN_FG  = "#f9e2af"

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.configure(style="TFrame")

        self._use_full_site   = tk.BooleanVar(value=False)
        self._running         = False
        self._lot_map_all:    List[Tuple[str, str, str, str]] = []
        self._lot_filtered:   List[Tuple[str, str, str, str]] = []
        self._lot_search_var  = tk.StringVar()
        self._wafer_var       = tk.StringVar()
        self._mat_var         = tk.StringVar()
        self._df:             Optional[pd.DataFrame] = None
        self._spec_lookup:    Dict = {}
        self._summary_rows:   List[dict] = []
        self._sort_col:       str  = "pct_fail"
        self._sort_asc:       bool = False           # descending by default

        self._lot_search_var.trace_add("write", lambda *_: self._apply_lot_filter())
        self._build_ui()
        self.after(300, self._refresh_lot_list)

    # ─── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)   # results expand

        # ── Filter panel ──────────────────────────────────────────────────────
        flt = ttk.Frame(self, style="TFrame")
        flt.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        flt.columnconfigure(1, weight=1)

        # Left: lot list
        lot_block = ttk.LabelFrame(flt, text="PCM Lots", style="TFrame", padding=4)
        lot_block.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        lot_block.columnconfigure(0, weight=1)

        srch = ttk.Frame(lot_block, style="TFrame")
        srch.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        ttk.Label(srch, text="\u26b2", style="Desc.TLabel").pack(side="left", padx=(0, 3))
        ttk.Entry(srch, textvariable=self._lot_search_var, width=22).pack(
            side="left", fill="x", expand=True)
        ttk.Button(srch, text="\u2715", style="Browse.TButton", width=2,
                   command=lambda: self._lot_search_var.set("")).pack(side="left", padx=(2, 0))

        self._lot_lb = tk.Listbox(
            lot_block, selectmode="extended", height=7, width=34,
            bg=self.ENTRY_BG, fg=self.FG, selectbackground=self.ACCENT,
            selectforeground="#ffffff", font=("Consolas", 8),
            borderwidth=1, relief="sunken", exportselection=False,
        )
        self._lot_lb.grid(row=1, column=0, sticky="nsew")
        _sb = ttk.Scrollbar(lot_block, orient="vertical", command=self._lot_lb.yview)
        _sb.grid(row=1, column=1, sticky="ns")
        self._lot_lb.configure(yscrollcommand=_sb.set)
        lot_block.rowconfigure(1, weight=1)

        btn_row = ttk.Frame(lot_block, style="TFrame")
        btn_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(3, 0))
        for lbl, cmd in [("All",  lambda: self._lot_lb.select_set(0, "end")),
                         ("None", lambda: self._lot_lb.select_clear(0, "end")),
                         ("\u21ba", self._refresh_lot_list)]:
            ttk.Button(btn_row, text=lbl, style="Browse.TButton",
                       command=cmd).pack(side="left", padx=2)

        ttk.Checkbutton(
            lot_block, text="Full-site CSVs",
            variable=self._use_full_site,
            command=self._refresh_lot_list,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # Right: wafer + material filters
        right = ttk.LabelFrame(flt, text="Filters", style="TFrame", padding=6)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)

        ttk.Label(right, text="Wafer numbers:", style="Desc.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(right, textvariable=self._wafer_var, width=30).grid(
            row=0, column=1, sticky="ew", pady=3)
        ttk.Label(right,
                  text="Comma-separated, e.g. 1,2,5   (blank = all wafers)",
                  style="Desc.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))

        ttk.Label(right, text="Material:", style="Desc.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 6))
        self._mat_combo = ttk.Combobox(right, textvariable=self._mat_var,
                                       state="readonly", width=40)
        self._mat_combo.grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(right, text="Leave blank to include all materials.",
                  style="Desc.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w")

        ttk.Label(right,
                  text="\nFail thresholds:  ≥5 % fail → FAIL    >0 % → MARGINAL    0 % → PASS",
                  style="Desc.TLabel").grid(
            row=4, column=0, columnspan=2, sticky="w")

        # ── Run row ───────────────────────────────────────────────────────────
        run_row = ttk.Frame(self, style="TFrame")
        run_row.grid(row=2, column=0, sticky="ew", padx=10, pady=4)

        self._analyze_btn = tk.Button(
            run_row, text="\u25b6  Run Analysis",
            bg=self.BTN_RUN, fg=self.BTN_FG,
            font=("Segoe UI", 10, "bold"), padx=14, pady=5,
            relief="flat", cursor="hand2",
            command=self._run_analysis,
        )
        self._analyze_btn.pack(side="left")

        self._export_btn = tk.Button(
            run_row, text="\u2b07 Export Summary CSV",
            bg=self.PANEL, fg=self.FG,
            font=("Segoe UI", 9), padx=10, pady=5,
            relief="flat", cursor="hand2",
            state="disabled",
            command=self._export_summary_csv,
        )
        self._export_btn.pack(side="left", padx=8)

        self._status_lbl = ttk.Label(run_row, text="", style="Desc.TLabel")
        self._status_lbl.pack(side="left", padx=8)

        # ── Results notebook ──────────────────────────────────────────────────
        res_nb = ttk.Notebook(self)
        res_nb.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 4))

        # Tab A: Executive Summary
        exec_fr = ttk.Frame(res_nb, style="TFrame")
        res_nb.add(exec_fr, text="  Executive Summary  ")
        exec_fr.columnconfigure(0, weight=1)
        exec_fr.rowconfigure(1, weight=1)

        ttk.Label(
            exec_fr,
            text=(
                "One row per parameter — sorted by failure rate (worst first).  "
                "Click a row to see per-lot / wafer breakdown in the Detail tab."
            ),
            style="Desc.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 2))

        exec_cols = ("parameter", "unit",
                     "spec_low", "target", "spec_high",
                     "n", "median", "p1", "p99",
                     "pct_fail_lo", "pct_fail_hi", "pct_fail", "status")
        exec_hdrs = ("Parameter", "Unit",
                     "Spec Lo", "Target", "Spec Hi",
                     "N", "Median", "P1", "P99",
                     "%Fail Lo", "%Fail Hi", "%Fail", "Status")
        exec_widths = (190, 55, 75, 75, 75, 55, 85, 85, 85, 75, 75, 65, 65)

        self._exec_tree = ttk.Treeview(
            exec_fr, columns=exec_cols, show="headings",
            selectmode="browse",
        )
        for col, hdr, w in zip(exec_cols, exec_hdrs, exec_widths):
            anchor = "w" if col in ("parameter", "unit", "status") else "e"
            self._exec_tree.heading(
                col, text=hdr,
                command=lambda c=col: self._sort_exec(c))
            self._exec_tree.column(
                col, width=w, anchor=anchor,
                stretch=(col == "parameter"))

        self._exec_tree.tag_configure("pass",     background="#1e3a2f", foreground="#a6e3a1")
        self._exec_tree.tag_configure("marginal", background="#383820", foreground="#f9e2af")
        self._exec_tree.tag_configure("fail",     background="#3a1a1a", foreground="#f38ba8")
        self._exec_tree.tag_configure("nospec",   background="#2a2a3e", foreground="#a6adc8")
        self._exec_tree.bind("<<TreeviewSelect>>", self._on_exec_select)

        exec_vsb = ttk.Scrollbar(exec_fr, orient="vertical",   command=self._exec_tree.yview)
        exec_hsb = ttk.Scrollbar(exec_fr, orient="horizontal", command=self._exec_tree.xview)
        self._exec_tree.configure(yscrollcommand=exec_vsb.set, xscrollcommand=exec_hsb.set)
        self._exec_tree.grid(row=1, column=0, sticky="nsew")
        exec_vsb.grid(row=1, column=1, sticky="ns")
        exec_hsb.grid(row=2, column=0, columnspan=2, sticky="ew")

        # Tab B: Per-Lot/Wafer Detail
        det_fr = ttk.Frame(res_nb, style="TFrame")
        res_nb.add(det_fr, text="  Per-Lot / Wafer Detail  ")
        det_fr.columnconfigure(0, weight=1)
        det_fr.rowconfigure(1, weight=1)

        self._detail_lbl = ttk.Label(
            det_fr,
            text="Select a parameter in the Executive Summary tab to drill down.",
            style="Desc.TLabel",
        )
        self._detail_lbl.grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 2))

        det_cols = ("lot", "wafer", "material",
                    "n", "median", "p1", "p99",
                    "pct_fail_lo", "pct_fail_hi", "pct_fail", "status")
        det_hdrs = ("Lot", "Wafer", "Material",
                    "N", "Median", "P1", "P99",
                    "%Fail Lo", "%Fail Hi", "%Fail", "Status")
        det_widths = (110, 55, 160, 55, 90, 90, 90, 75, 75, 65, 65)

        self._det_tree = ttk.Treeview(
            det_fr, columns=det_cols, show="headings",
            selectmode="browse",
        )
        for col, hdr, w in zip(det_cols, det_hdrs, det_widths):
            anchor = "w" if col in ("lot", "material", "status") else "e"
            self._det_tree.heading(col, text=hdr)
            self._det_tree.column(col, width=w, anchor=anchor,
                                  stretch=(col == "material"))

        self._det_tree.tag_configure("pass",     background="#1e3a2f", foreground="#a6e3a1")
        self._det_tree.tag_configure("marginal", background="#383820", foreground="#f9e2af")
        self._det_tree.tag_configure("fail",     background="#3a1a1a", foreground="#f38ba8")
        self._det_tree.tag_configure("nospec",   background="#2a2a3e", foreground="#a6adc8")

        det_vsb = ttk.Scrollbar(det_fr, orient="vertical",   command=self._det_tree.yview)
        det_hsb = ttk.Scrollbar(det_fr, orient="horizontal", command=self._det_tree.xview)
        self._det_tree.configure(yscrollcommand=det_vsb.set, xscrollcommand=det_hsb.set)
        self._det_tree.grid(row=1, column=0, sticky="nsew")
        det_vsb.grid(row=1, column=1, sticky="ns")
        det_hsb.grid(row=2, column=0, columnspan=2, sticky="ew")

        # ── Log ───────────────────────────────────────────────────────────────
        ttk.Label(self, text="Log", style="Section.TLabel").grid(
            row=3, column=0, sticky="w", padx=12, pady=(4, 0))
        self._log_box = scrolledtext.ScrolledText(
            self, height=6, state="disabled",
            bg=self.PANEL, fg=self.FG, font=("Consolas", 8),
            insertbackground=self.FG, borderwidth=1, relief="sunken",
        )
        self._log_box.grid(row=4, column=0, sticky="nsew", padx=10, pady=(2, 8))
        self._log_box.tag_config("err",  foreground=self.ERR_FG)
        self._log_box.tag_config("ok",   foreground=self.OK_FG)
        self._log_box.tag_config("warn", foreground=self.WARN_FG)

        self.rowconfigure(1, weight=3)
        self.rowconfigure(4, weight=1)

    # ─── Lot list ─────────────────────────────────────────────────────────────

    def _refresh_lot_list(self):
        raw = _scan_lots(self._use_full_site.get())
        self._lot_map_all = []
        for lot_id, csv_path in raw:
            layout = _get_lot_layout(csv_path)
            mat    = _get_lot_material(lot_id, csv_path)
            self._lot_map_all.append((lot_id, csv_path, layout, mat))
        self._apply_lot_filter(first_load=True)

    def _apply_lot_filter(self, first_load: bool = False):
        prev_sel: set = set()
        if not first_load and self._lot_filtered:
            prev_sel = {self._lot_filtered[i][0]
                        for i in self._lot_lb.curselection()}

        q = self._lot_search_var.get().lower().strip()
        self._lot_filtered = [
            t for t in self._lot_map_all
            if not q or q in t[0].lower() or q in t[2].lower() or q in t[3].lower()
        ]

        self._lot_lb.delete(0, "end")
        for lot_id, _path, layout, mat in self._lot_filtered:
            parts = [f"{lot_id:<12}"]
            if layout:
                parts.append(f"{layout:<14}")
            if mat:
                parts.append(mat)
            self._lot_lb.insert("end", "".join(parts).rstrip())

        if first_load or not prev_sel:
            self._lot_lb.select_set(0, "end")
        else:
            for i, (lot_id, *_) in enumerate(self._lot_filtered):
                if lot_id in prev_sel:
                    self._lot_lb.selection_set(i)

    # ─── Analysis ─────────────────────────────────────────────────────────────

    def _run_analysis(self):
        if self._running:
            return
        sel = list(self._lot_lb.curselection())
        if not sel:
            messagebox.showwarning("No lots selected",
                                   "Select at least one lot.", parent=self)
            return

        lot_csv_map = {
            self._lot_filtered[i][0]: self._lot_filtered[i][1]
            for i in sel
        }
        wafer_filter = self._wafer_var.get().strip()
        mat_filter   = self._mat_var.get().strip()

        self._running = True
        self._analyze_btn.configure(state="disabled", text="\u23f3 Analyzing\u2026")
        self._status_lbl.configure(text="")
        self._log_clear()

        threading.Thread(
            target=self._worker,
            args=(lot_csv_map, wafer_filter, mat_filter),
            daemon=True,
        ).start()

    def _worker(self, lot_csv_map, wafer_filter, mat_filter):
        try:
            self._do_analysis(lot_csv_map, wafer_filter, mat_filter)
        except Exception as ex:
            import traceback
            self.after(0, self._log, f"[ERROR] {ex}", "err")
            self.after(0, self._log, traceback.format_exc(), "err")
        finally:
            self.after(0, self._finish)

    def _do_analysis(self, lot_csv_map: dict, wafer_filter: str, mat_filter: str):
        log = lambda msg, tag="": self.after(0, self._log, msg, tag)

        log("=" * 62)
        log(f"Parameter Analysis — {len(lot_csv_map)} lot(s)")
        log("=" * 62)

        # ── Load ──────────────────────────────────────────────────────────────
        df = _load_and_merge(lot_csv_map, log)
        if df.empty:
            log("[ERROR] No data loaded — check lot selection.", "err")
            return

        # ── Wafer filter ──────────────────────────────────────────────────────
        if wafer_filter:
            wafers = {w.strip() for w in wafer_filter.split(",") if w.strip()}
            if wafers:
                mask = df["Wafer"].astype(str).str.strip().isin(wafers)
                df   = df[mask]
                log(f"[Filt ] Wafer filter {sorted(wafers)} → {len(df):,} rows")

        # ── Material filter ───────────────────────────────────────────────────
        # Populate combo before applying filter
        if "Material" in df.columns:
            mats = sorted(
                df["Material"].dropna().astype(str).str.strip()
                .loc[lambda s: s.str.len() > 0].unique().tolist()
            )
            self.after(0, lambda m=mats: self._mat_combo.configure(values=[""] + m))

        if mat_filter and "Material" in df.columns:
            df = df[df["Material"].astype(str).str.strip() == mat_filter]
            log(f"[Filt ] Material '{mat_filter}' → {len(df):,} rows")

        if df.empty:
            log("[WARN ] No rows remaining after filters.", "warn")
            return

        # ── Identify parameter columns ─────────────────────────────────────────
        param_cols = [
            c for c in df.columns
            if c not in _PCM_ID_COLS
            and pd.api.types.is_numeric_dtype(df[c])
        ]
        log(f"[Param] {len(param_cols)} numeric parameter columns")

        # ── Spec ──────────────────────────────────────────────────────────────
        spec_lookup = _load_spec_lookup()
        log(f"[Spec ] {len(spec_lookup)} parameters with limits in spec CSV")

        # ── Per-parameter summary ─────────────────────────────────────────────
        rows: List[dict] = []
        for param in param_cols:
            entry    = spec_lookup.get(param, (float("nan"),) * 5)
            sl, sh   = entry[0], entry[1]
            tgt      = entry[2]
            unit     = entry[3] if len(entry) > 3 else ""
            has_spec = not (math.isnan(sl) and math.isnan(sh))

            n, med, p1, p99, pct_lo, pct_hi, pct_fail = _param_stats(df[param], sl, sh)
            status = _status_from_pct(pct_fail, has_spec)

            rows.append(dict(
                parameter   = param,
                unit        = unit,
                spec_low    = sl,
                target      = tgt,
                spec_high   = sh,
                n           = n,
                median      = med,
                p1          = p1,
                p99         = p99,
                pct_fail_lo = pct_lo,
                pct_fail_hi = pct_hi,
                pct_fail    = pct_fail,
                status      = status,
            ))

        # Sort: FAIL → MARGINAL → PASS → NO SPEC; within FAIL sort by %fail desc
        rows.sort(key=lambda r: (
            _STATUS_ORDER.get(r["status"], 9),
            -(r["pct_fail"] if not math.isnan(r["pct_fail"]) else 0.0),
        ))

        self._summary_rows = rows
        self._df           = df
        self._spec_lookup  = spec_lookup

        n_fail = sum(1 for r in rows if r["status"] == "FAIL")
        n_marg = sum(1 for r in rows if r["status"] == "MARGINAL")
        n_pass = sum(1 for r in rows if r["status"] == "PASS")
        n_none = sum(1 for r in rows if r["status"] == "NO SPEC")

        log(f"[Done ] {len(rows)} params analysed: "
            f"FAIL={n_fail}  MARGINAL={n_marg}  PASS={n_pass}  NO SPEC={n_none}", "ok")

        self.after(0, self._populate_exec_table)

    # ─── Executive table ──────────────────────────────────────────────────────

    def _populate_exec_table(self):
        self._exec_tree.delete(*self._exec_tree.get_children())

        for r in self._summary_rows:
            tag  = _TAG_MAP.get(r["status"], "nospec")
            pct_fail = r["pct_fail"]
            vals = (
                r["parameter"],
                r["unit"],
                _fmt_v(r["spec_low"]),
                _fmt_v(r["target"]),
                _fmt_v(r["spec_high"]),
                str(r["n"]),
                _fmt_v(r["median"]),
                _fmt_v(r["p1"]),
                _fmt_v(r["p99"]),
                _fmt_v(r["pct_fail_lo"], pct=True),
                _fmt_v(r["pct_fail_hi"], pct=True),
                _fmt_v(pct_fail, pct=True),
                r["status"],
            )
            self._exec_tree.insert("", "end", iid=r["parameter"],
                                   values=vals, tags=(tag,))

        self._export_btn.configure(state="normal")
        n_fail = sum(1 for r in self._summary_rows if r["status"] == "FAIL")
        n_marg = sum(1 for r in self._summary_rows if r["status"] == "MARGINAL")
        self._status_lbl.configure(
            text=(f"{len(self._summary_rows)} params  \u2502  "
                  f"{n_fail} FAIL  \u2502  {n_marg} MARGINAL"))

    def _sort_exec(self, col: str):
        """Sort executive table by *col*; toggle direction on repeated clicks."""
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = (col not in ("pct_fail", "pct_fail_lo", "pct_fail_hi"))

        iids = self._exec_tree.get_children("")
        data = [(self._exec_tree.set(iid, col), iid) for iid in iids]

        def _key(x):
            try:
                v = x[0].rstrip("%")
                return (0, float(v)) if v not in ("—", "") else (1, 0.0)
            except ValueError:
                return (0 if x[0] else 1, x[0])

        data.sort(key=_key, reverse=not self._sort_asc)
        for idx, (_, iid) in enumerate(data):
            self._exec_tree.move(iid, "", idx)

    def _on_exec_select(self, _event=None):
        sel = self._exec_tree.selection()
        if not sel:
            return
        self._populate_detail(sel[0])   # iid == parameter name

    # ─── Detail table ─────────────────────────────────────────────────────────

    def _populate_detail(self, param: str):
        self._det_tree.delete(*self._det_tree.get_children())
        df = self._df
        if df is None or param not in df.columns:
            return

        entry  = self._spec_lookup.get(param, (float("nan"),) * 5)
        sl, sh = entry[0], entry[1]

        self._detail_lbl.configure(
            text=(f"Parameter: {param}   \u2502   "
                  f"Spec Lo: {_fmt_v(sl)}   "
                  f"Spec Hi: {_fmt_v(sh)}   "
                  f"(click column header to sort)")
        )

        # Group by available ID columns
        group_cols = [c for c in ("Lot", "Wafer", "Material") if c in df.columns]
        if not group_cols:
            return

        has_spec = not (math.isnan(sl) and math.isnan(sh))

        for keys, grp in df.groupby(group_cols, sort=True, observed=True):
            keys = keys if isinstance(keys, tuple) else (keys,)

            n, med, p1, p99, pct_lo, pct_hi, pct_fail = _param_stats(grp[param], sl, sh)
            status = _status_from_pct(pct_fail, has_spec)
            tag    = _TAG_MAP.get(status, "nospec")

            # Pad keys to (Lot, Wafer, Material)
            key_list = list(str(k) for k in keys)
            while len(key_list) < 3:
                key_list.append("")

            row_vals = key_list + [
                str(n),
                _fmt_v(med),
                _fmt_v(p1),
                _fmt_v(p99),
                _fmt_v(pct_lo, pct=True),
                _fmt_v(pct_hi, pct=True),
                _fmt_v(pct_fail, pct=True),
                status,
            ]
            self._det_tree.insert("", "end", values=row_vals, tags=(tag,))

    # ─── Export ───────────────────────────────────────────────────────────────

    def _export_summary_csv(self):
        if not self._summary_rows:
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export Parameter Analysis CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            pd.DataFrame(self._summary_rows).to_csv(path, index=False)
            self._log(f"[Export] Saved: {path}", "ok")
        except Exception as ex:
            messagebox.showerror("Export failed", str(ex), parent=self)

    # ─── Log helpers ──────────────────────────────────────────────────────────

    def _log(self, msg: str, tag: str = ""):
        self._log_box.configure(state="normal")
        self._log_box.insert("end", msg + "\n", tag)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _log_clear(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    def _finish(self):
        self._running = False
        self._analyze_btn.configure(state="normal", text="\u25b6  Run Analysis")
