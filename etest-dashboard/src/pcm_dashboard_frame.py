"""pcm_dashboard_frame.py — ETest/PCM Dashboard tab (Tkinter frame).

Provides the GUI panel that lets users:
  1. Select lot numbers from the 9-site (or full-site) directory.
  2. Pick a Product Setup JSON that defines parameter groups.
  3. Choose an output folder.
  4. Run the HTML dashboard generator.

This module is self-contained (no circular imports with pcm_merge_gui.py).
It is imported and instantiated by pcm_merge_gui._build_ui().
"""

from __future__ import annotations

import fnmatch
import json
import math
import os
import re
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from _constants import _LOADER, _FROZEN, _SRC_DIR, _walk_dir_and_zips, _read_csv, _zip_basename

# ---------------------------------------------------------------------------
# Path constants  (mirror those in pcm_merge_gui.py)
# ---------------------------------------------------------------------------

_HERE        = os.path.dirname(os.path.abspath(__file__))
def _find_repo_root(start: str) -> str:
    d = start
    for _ in range(8):
        if os.path.isdir(os.path.join(d, "shared")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.abspath(os.path.join(start, "..", "..", ".."))
_REPO_ROOT   = _find_repo_root(_HERE)
_NINE_SITE_DIR  = os.path.join(_REPO_ROOT, "shared", "etest", "9-sites")
_FULL_SITE_DIR  = os.path.join(_REPO_ROOT, "shared", "etest", "full-sites")
_MATERIAL_DIR   = os.path.join(_REPO_ROOT, "shared", "material")
_SPEC_CSV       = os.path.join(_REPO_ROOT, "shared", "spec", "wat",
                               "N2P_NVL816_WAT_PDK1.0_target.csv")
_DEFAULT_SETUP  = next(
    (p for p in [
        os.path.join(_REPO_ROOT, "shared", "setup", "config", "etest-dashboard", "pcm_product_setup.json"),
        os.path.join(_REPO_ROOT, "shared", "setup", "etest-dashboard", "pcm_product_setup.json"),
        os.path.join(_REPO_ROOT, "shared", "spec", "collateral", "etest", "pcm_product_setup.json"),
    ] if os.path.isfile(p)),
    os.path.join(_REPO_ROOT, "shared", "setup", "config", "etest-dashboard", "pcm_product_setup.json"),
)

# Only bring in this single combined column from the material CSV
_MAT_COLS = ["Material Type, Skew, BEOL Skew"]

# PCM CSV columns that are NOT parameters
_PCM_ID_COLS = {
    "Technology", "Layout", "Lot", "Wafer", "TestProgram", "TestProgramVersion",
    "Fab", "Step", "Equipment", "EquipmentType", "TestDateTime", "TestDate",
    "TimeLoaded", "WaferResultID", "Site", "LayoutX", "LayoutY", "Map", "MapID",
    "ReticleShotRadius",
}


# ---------------------------------------------------------------------------
# Data-loading helpers
# ---------------------------------------------------------------------------

def _scan_lots(use_full_site: bool) -> List[Tuple[str, str]]:
    """Return sorted list of (lot_id, csv_path) found in the PCM directories.

    Lot IDs are extracted from filenames:
        8PF6CV-R-Q601S0H0-PCM.csv  →  lot = "Q601S0H0"
    """
    results: List[Tuple[str, str]] = []
    seen_lots: set = set()

    dirs = [_NINE_SITE_DIR]
    if use_full_site and os.path.isdir(_FULL_SITE_DIR):
        dirs.append(_FULL_SITE_DIR)

    for d in dirs:
        if not os.path.isdir(d):
            continue
        for fname, full in _walk_dir_and_zips(d):
            if not fname.endswith("-PCM.csv"):
                continue
            # Pattern: PREFIX-STEP-LOT-PCM.csv
            m = re.match(r"^.+-.+-(.+)-PCM\.csv$", fname)
            if m:
                lot_id = m.group(1)
                if lot_id not in seen_lots:
                    seen_lots.add(lot_id)
                    results.append((lot_id, full))

    return sorted(results, key=lambda t: t[0])


def _find_csv_for_lot(lot_id: str, use_full_site: bool) -> Optional[str]:
    """Return the CSV path for a given lot ID, or None if not found."""
    dirs = [_NINE_SITE_DIR]
    if use_full_site and os.path.isdir(_FULL_SITE_DIR):
        dirs.append(_FULL_SITE_DIR)

    for d in dirs:
        if not os.path.isdir(d):
            continue
        for fname, full in _walk_dir_and_zips(d):
            if fname.endswith(f"-{lot_id}-PCM.csv"):
                return full
    return None


def _load_spec_lookup(path=None) -> Dict[str, Tuple]:
    """Load spec CSV → {param: (sl, sh, tgt, unit, name)}."""
    _path = path if path else _SPEC_CSV
    if not os.path.isfile(_path):
        return {}
    df = pd.read_csv(_path)
    df.columns = [c.strip() for c in df.columns]

    param_col = next((c for c in ["WAT naming", "Parameter"] if c in df.columns), None)
    name_col  = next((c for c in ["Device naming", "Naming", "Name"] if c in df.columns), None)
    sl_col    = next((c for c in ["Spec Low", "Spec_Low"]   if c in df.columns), None)
    sh_col    = next((c for c in ["Spec High", "Spec_High"] if c in df.columns), None)
    tgt_col   = "Target" if "Target" in df.columns else None
    unit_col  = "Unit"   if "Unit"   in df.columns else None

    if param_col is None:
        return {}

    def _fv(row, col):
        if col is None:
            return float("nan")
        try:
            v = row[col]
            return float(v) if pd.notna(v) else float("nan")
        except Exception:
            return float("nan")

    lookup: Dict[str, Tuple] = {}
    seen: set = set()
    for _, row in df.iterrows():
        p = str(row.get(param_col, "")).strip()
        if not p or p in seen:
            continue
        seen.add(p)
        sl   = _fv(row, sl_col)
        sh   = _fv(row, sh_col)
        tgt  = _fv(row, tgt_col)
        unit = str(row.get(unit_col, "")).strip() if unit_col else ""
        name = str(row.get(name_col, "")).strip() if name_col else ""
        # include if any of sl/sh/tgt is defined
        if not (math.isnan(sl) and math.isnan(sh) and math.isnan(tgt)):
            lookup[p] = (sl, sh, tgt, unit, name)
    return lookup


def _find_material_csv(tech_prefix: str, lot7: str) -> Optional[str]:
    """Return the material CSV that best matches tech_prefix and lot7."""
    if not os.path.isdir(_MATERIAL_DIR):
        return None
    # Prefer files whose name contains the 6-char prefix (case-insensitive)
    candidates = [
        os.path.join(_MATERIAL_DIR, f)
        for f in sorted(os.listdir(_MATERIAL_DIR))
        if f.lower().endswith(".csv") and tech_prefix.lower() in f.lower()
    ]
    # Among candidates, prefer the one that contains this lot7
    if lot7 and candidates:
        for fpath in candidates:
            try:
                col_check = pd.read_csv(fpath, nrows=1).columns.tolist()
                if "INTEL_LOT7" not in col_check:
                    continue
                lots_in_file = set(
                    pd.read_csv(fpath, usecols=["INTEL_LOT7"])
                    ["INTEL_LOT7"].dropna().astype(str).str.strip()
                )
                if lot7 in lots_in_file:
                    return fpath
            except Exception:
                pass
        return candidates[0]
    if candidates:
        return candidates[0]
    # Last resort: any lot-definition CSV
    for fname in sorted(os.listdir(_MATERIAL_DIR)):
        if fname.lower().endswith(".csv") and "lot" in fname.lower():
            return os.path.join(_MATERIAL_DIR, fname)
    return None


def _get_lot_layout(csv_path: str) -> str:
    """Return the first unique Layout value from the PCM CSV, or ''."""
    try:
        df = _read_csv(csv_path, usecols=["Layout"], nrows=200, low_memory=False)
        vals = df["Layout"].dropna().astype(str).str.strip()
        vals = vals[vals.str.len() > 0]
        if not vals.empty:
            uniq = vals.unique().tolist()
            return "/".join(uniq[:3])   # show up to 3 if multiple
    except Exception:
        pass
    return ""


def _get_lot_material(lot_id: str, csv_path: str) -> str:
    """Return the 'Material Type, Skew, BEOL Skew' value for this lot, or ''."""
    lot7 = lot_id[:7]
    fname = _zip_basename(csv_path)
    m = re.match(r"^([A-Z0-9]+)-[A-Z]-", fname, re.IGNORECASE)
    tech_prefix = m.group(1) if m else ""
    mat_csv = _find_material_csv(tech_prefix, lot7)
    if not mat_csv:
        return ""
    col = "Material Type, Skew, BEOL Skew"
    try:
        df_mat = pd.read_csv(mat_csv, low_memory=False)
        df_mat.columns = [c.strip() for c in df_mat.columns]
        lot7_col = "INTEL_LOT7" if "INTEL_LOT7" in df_mat.columns else None
        if lot7_col and col in df_mat.columns:
            rows = df_mat[df_mat[lot7_col].astype(str).str.strip() == lot7]
            if not rows.empty:
                val = rows[col].dropna().astype(str).str.strip()
                val = val[val.str.len() > 0]
                if not val.empty:
                    return val.iloc[0]
    except Exception:
        pass
    return ""


def _load_and_merge(
    lot_csv_map: Dict[str, str],
    log,
) -> pd.DataFrame:
    """Load PCM CSVs for each lot, join material info, combine into one DataFrame."""
    frames: List[pd.DataFrame] = []

    for lot_id, csv_path in lot_csv_map.items():
        log(f"[Load ] Lot {lot_id}: {_zip_basename(csv_path)}")
        try:
            df = _read_csv(csv_path, low_memory=False)
        except Exception as ex:
            log(f"[WARN ] Could not read {csv_path}: {ex}", "warn")
            continue

        log(f"        {len(df):,} rows, {len(df.columns)} columns")

        # Ensure Lot column has the correct lot_id
        if "Lot" not in df.columns:
            df["Lot"] = lot_id

        # ── Material join ──────────────────────────────────────────────────
        lot7 = lot_id[:7]
        # Infer tech prefix from filename
        fname = _zip_basename(csv_path)
        m = re.match(r"^([A-Z0-9]+)-[A-Z]-", fname, re.IGNORECASE)
        tech_prefix = m.group(1) if m else ""

        mat_csv = _find_material_csv(tech_prefix, lot7)
        if mat_csv:
            log(f"[Mat  ] {os.path.basename(mat_csv)}")
            try:
                df_mat = pd.read_csv(mat_csv, low_memory=False)
                df_mat.columns = [c.strip() for c in df_mat.columns]

                lot7_col  = "INTEL_LOT7" if "INTEL_LOT7" in df_mat.columns else None
                wid_col   = next((c for c in df_mat.columns
                                  if "WAFERID" in c.upper() or "WAFER ID" in c.upper()), None)
                mat_keep  = [c for c in _MAT_COLS if c in df_mat.columns]

                if lot7_col and mat_keep:
                    df_mat["_ml7"] = df_mat[lot7_col].astype(str).str.strip()
                    if wid_col:
                        df_mat["_mwid"] = pd.to_numeric(df_mat[wid_col], errors="coerce")
                        df = pd.concat([df, pd.DataFrame({"_ml7": lot7, "_mwid": pd.to_numeric(df["Wafer"], errors="coerce")}, index=df.index)], axis=1)
                        dedup = (
                            df_mat[["_ml7", "_mwid"] + mat_keep]
                            .drop_duplicates(subset=["_ml7", "_mwid"])
                        )
                        df = df.merge(dedup, on=["_ml7", "_mwid"], how="left").copy()
                        n_matched = df[mat_keep[0]].notna().sum()
                    else:
                        df = pd.concat([df, pd.DataFrame({"_ml7": lot7}, index=df.index)], axis=1)
                        dedup = (
                            df_mat[["_ml7"] + mat_keep]
                            .drop_duplicates(subset=["_ml7"])
                        )
                        df = df.merge(dedup, on="_ml7", how="left").copy()
                        n_matched = df[mat_keep[0]].notna().sum() if mat_keep else 0

                    df.drop(columns=["_ml7", "_mwid"], errors="ignore", inplace=True)
                    log(f"        Material joined: {n_matched:,}/{len(df):,} rows matched")

                    # Map the combined column directly to 'Material'
                    combined_col = "Material Type, Skew, BEOL Skew"
                    if combined_col in df.columns:
                        df = df.copy()
                        df["Material"] = df[combined_col].fillna("").astype(str)
                    else:
                        df["Material"] = ""
            except Exception as ex:
                log(f"[WARN ] Material join failed: {ex}", "warn")
                df["Material"] = ""
        else:
            log(f"[Mat  ] No material CSV found for {lot_id}")
            df["Material"] = ""

        frames.append(df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True, sort=False)
    log(f"[Merge] Combined: {len(combined):,} rows × {len(combined.columns)} columns")
    return combined


# ---------------------------------------------------------------------------
# GUI Frame
# ---------------------------------------------------------------------------

class PCMDashboardFrame(ttk.Frame):
    """Tkinter frame for the ETest/PCM Dashboard tab."""

    # Dark palette  (matches PCMMergeGUI)
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
        self._use_full_site = tk.BooleanVar(value=False)
        self._setup_var     = tk.StringVar(value=_DEFAULT_SETUP)
        self._out_var       = tk.StringVar(value=r"C:\temp")
        self._pcm_filter_var = tk.StringVar(value="")
        self._param_groups: List[dict] = []   # [{name, patterns}, ...] from setup JSON
        self._running       = False
        self._last_html: Optional[str] = None
        self._lot_map_all: List[Tuple[str, str, str]] = []  # (lot_id, csv_path, material)
        self._lot_filtered: List[Tuple[str, str, str]] = []
        self._lot_search_var: Optional[tk.StringVar] = None
        # Default location for saved GUI state
        self._gui_state_path = os.path.join(
            os.path.dirname(_DEFAULT_SETUP), "pcm_gui_state.json"
        )
        self._build_ui()
        self.after(200, self._refresh_lot_list)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        pad = {"padx": 10, "pady": 3}

        # Description
        ttk.Label(
            self,
            text=(
                "Generate an HTML variability dashboard for selected PCM lots.  "
                "Choose lots from the list below (Ctrl+click for multi-select), "
                "specify a Product Setup JSON that defines parameter groups, "
                "pick an output folder, then click Run."
            ),
            style="Desc.TLabel", wraplength=760, justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 2))

        ttk.Separator(self, orient="horizontal").grid(
            row=1, column=0, columnspan=3, sticky="ew", padx=8, pady=4)

        # ── Lot selector ──────────────────────────────────────────────────────
        ttk.Label(self, text="Available PCM Lots", style="Section.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(
            self,
            text="Select one or more lots (Ctrl+click / Shift+click for multiple).",
            style="Desc.TLabel",
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=12, pady=0)

        # Frame with listbox + scrollbar
        lot_frame = ttk.Frame(self, style="TFrame")
        lot_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=12, pady=4)
        lot_frame.columnconfigure(0, weight=1)

        # Search row
        srch_row = ttk.Frame(lot_frame, style="TFrame")
        srch_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 3))
        ttk.Label(srch_row, text="\u26b2 Search:", style="Desc.TLabel").pack(side="left", padx=(0, 4))
        self._lot_search_var = tk.StringVar()
        self._lot_search_var.trace_add("write", lambda *_: self._apply_lot_filter())
        srch_entry = ttk.Entry(srch_row, textvariable=self._lot_search_var, width=28)
        srch_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(srch_row, text="\u2715", style="Browse.TButton", width=2,
                   command=lambda: self._lot_search_var.set("")).pack(side="left", padx=(4, 0))

        self._lot_lb = tk.Listbox(
            lot_frame, selectmode="extended", height=10,
            bg=self.ENTRY_BG, fg=self.FG, selectbackground=self.ACCENT,
            selectforeground="#ffffff", font=("Consolas", 9),
            borderwidth=1, relief="sunken", exportselection=False,
        )
        self._lot_lb.grid(row=1, column=0, sticky="nsew")

        sb = ttk.Scrollbar(lot_frame, orient="vertical", command=self._lot_lb.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._lot_lb.configure(yscrollcommand=sb.set)
        lot_frame.rowconfigure(1, weight=1)

        # Lot buttons column
        btn_col = ttk.Frame(self, style="TFrame")
        btn_col.grid(row=4, column=2, sticky="n", padx=4, pady=4)

        ttk.Button(btn_col, text="Select All",  style="Browse.TButton",
                   command=self._select_all_lots).pack(fill="x", pady=2)
        ttk.Button(btn_col, text="Clear",       style="Browse.TButton",
                   command=self._clear_lots).pack(fill="x", pady=2)
        ttk.Button(btn_col, text="↺ Refresh",   style="Browse.TButton",
                   command=self._refresh_lot_list).pack(fill="x", pady=2)

        # Full-site toggle
        fs_frame = ttk.Frame(self, style="TFrame")
        fs_frame.grid(row=5, column=0, columnspan=3, sticky="w", padx=12, pady=3)

        ttk.Checkbutton(
            fs_frame, text="Include full-site CSVs", variable=self._use_full_site,
            command=self._refresh_lot_list,
        ).pack(side="left")
        ttk.Label(
            fs_frame,
            text="  (refreshes lot list above to also include full-site directory)",
            style="Desc.TLabel",
        ).pack(side="left")

        ttk.Separator(self, orient="horizontal").grid(
            row=6, column=0, columnspan=3, sticky="ew", padx=8, pady=4)

        # ── Product Setup JSON ────────────────────────────────────────────────
        ttk.Label(self, text="Product Setup JSON", style="Section.TLabel").grid(
            row=7, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(
            self,
            text=(
                "JSON file defining the dashboard title and parameter groups.  "
                "A sample is auto-created at  shared/setup/etest-dashboard/pcm_product_setup.json."
            ),
            style="Desc.TLabel", wraplength=680,
        ).grid(row=8, column=0, columnspan=3, sticky="w", padx=12, pady=0)

        ttk.Entry(self, textvariable=self._setup_var, width=70).grid(
            row=9, column=0, columnspan=2, sticky="ew", padx=12, pady=2)
        ttk.Button(self, text="Browse…", style="Browse.TButton",
                   command=self._browse_setup).grid(
            row=9, column=2, sticky="w", padx=4, pady=2)

        # ── Output Folder ─────────────────────────────────────────────────────
        ttk.Label(self, text="Output Folder", style="Section.TLabel").grid(
            row=10, column=0, columnspan=3, sticky="w", **pad)

        ttk.Entry(self, textvariable=self._out_var, width=70).grid(
            row=11, column=0, columnspan=2, sticky="ew", padx=12, pady=2)
        ttk.Button(self, text="Browse…", style="Browse.TButton",
                   command=self._browse_out).grid(
            row=11, column=2, sticky="w", padx=4, pady=2)

        ttk.Separator(self, orient="horizontal").grid(
            row=12, column=0, columnspan=3, sticky="ew", padx=8, pady=4)

        # ── PCM Parameter Groups ──────────────────────────────────────────
        ttk.Label(self, text="Parameter Groups", style="Section.TLabel").grid(
            row=13, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 0))
        ttk.Label(
            self,
            text="Select which parameter groups to include.  "
                 "Groups come from the Product Setup JSON.  "
                 "Use the custom wildcard to add extra patterns.",
            style="Desc.TLabel", wraplength=700, justify="left",
        ).grid(row=14, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 2))

        # Group listbox + buttons
        grp_frame = ttk.Frame(self, style="TFrame")
        grp_frame.grid(row=15, column=0, columnspan=2, sticky="nsew", padx=12, pady=2)
        grp_frame.columnconfigure(0, weight=1)
        grp_frame.rowconfigure(0, weight=1)

        self._grp_lb = tk.Listbox(
            grp_frame, selectmode="extended", height=6,
            bg=self.ENTRY_BG, fg=self.FG, selectbackground=self.ACCENT,
            selectforeground="#ffffff", font=("Consolas", 9),
            borderwidth=1, relief="sunken", exportselection=False,
        )
        self._grp_lb.grid(row=0, column=0, sticky="nsew")

        grp_sb = ttk.Scrollbar(grp_frame, orient="vertical",
                                command=self._grp_lb.yview)
        grp_sb.grid(row=0, column=1, sticky="ns")
        self._grp_lb.configure(yscrollcommand=grp_sb.set)

        # Group buttons
        grp_btn_col = ttk.Frame(self, style="TFrame")
        grp_btn_col.grid(row=15, column=2, sticky="n", padx=4, pady=2)
        ttk.Button(grp_btn_col, text="Select All", style="Browse.TButton",
                   command=lambda: self._grp_lb.select_set(0, "end")
                   ).pack(fill="x", pady=2)
        ttk.Button(grp_btn_col, text="Clear", style="Browse.TButton",
                   command=lambda: self._grp_lb.select_clear(0, "end")
                   ).pack(fill="x", pady=2)
        ttk.Button(grp_btn_col, text="↺ Reload", style="Browse.TButton",
                   command=self._load_param_groups).pack(fill="x", pady=2)

        # Custom wildcard filter
        filt_frame = ttk.Frame(self, style="TFrame")
        filt_frame.grid(row=16, column=0, columnspan=3, sticky="ew", padx=12, pady=(4, 2))
        ttk.Label(filt_frame, text="Custom filter:", style="Desc.TLabel").pack(
            side="left", padx=(0, 4))
        ttk.Entry(filt_frame, textvariable=self._pcm_filter_var, width=30).pack(
            side="left", padx=(0, 6))
        ttk.Label(
            filt_frame,
            text="Extra wildcard(s), comma-separated  e.g.  *Rs*,*Rc*   (merged with selected groups)",
            style="Desc.TLabel",
        ).pack(side="left")

        ttk.Separator(self, orient="horizontal").grid(
            row=17, column=0, columnspan=3, sticky="ew", padx=8, pady=6)

        # ── Run button ────────────────────────────────────────────────────────
        run_frame = ttk.Frame(self, style="TFrame")
        run_frame.grid(row=18, column=0, columnspan=3, sticky="w", padx=12)

        self._run_btn = tk.Button(
            run_frame, text="▶  Generate Dashboard",
            bg=self.BTN_RUN, fg=self.BTN_FG,
            font=("Segoe UI", 10, "bold"), padx=14, pady=5,
            relief="flat", cursor="hand2",
            command=self._run,
        )
        self._run_btn.pack(side="left")

        self._open_btn = tk.Button(
            run_frame, text="🌐  Open Dashboard",
            bg=self.PANEL, fg=self.FG,
            font=("Segoe UI", 10), padx=14, pady=5,
            relief="flat", cursor="hand2", state="disabled",
            command=self._open_dashboard,
        )
        self._open_btn.pack(side="left", padx=8)

        # Save / Load GUI state buttons
        tk.Button(
            run_frame, text="💾 Save Setup",
            bg="#313244", fg=self.FG,
            font=("Segoe UI", 9), padx=10, pady=5,
            relief="flat", cursor="hand2",
            command=self._save_setup,
        ).pack(side="left", padx=(16, 2))

        tk.Button(
            run_frame, text="📂 Load Setup",
            bg="#313244", fg=self.FG,
            font=("Segoe UI", 9), padx=10, pady=5,
            relief="flat", cursor="hand2",
            command=self._load_setup,
        ).pack(side="left", padx=2)

        self._status_lbl = ttk.Label(run_frame, text="", style="Desc.TLabel")
        self._status_lbl.pack(side="left", padx=12)

        # ── Log ───────────────────────────────────────────────────────────────
        ttk.Label(self, text="Log", style="Section.TLabel").grid(
            row=19, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 0))

        self._log_box = scrolledtext.ScrolledText(
            self, height=10, state="disabled",
            bg=self.PANEL, fg=self.FG, font=("Consolas", 8),
            insertbackground=self.FG, borderwidth=1, relief="sunken",
        )
        self._log_box.grid(row=20, column=0, columnspan=3, sticky="nsew",
                           padx=12, pady=(2, 10))
        self._log_box.tag_config("err",  foreground=self.ERR_FG)
        self._log_box.tag_config("ok",   foreground=self.OK_FG)
        self._log_box.tag_config("warn", foreground=self.WARN_FG)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(20, weight=1)

        # Load parameter groups from the setup JSON on startup
        self.after(300, self._load_param_groups)

    # ── Lot list helpers ──────────────────────────────────────────────────────

    def _refresh_lot_list(self):
        """Scan directories, look up layout+material for each lot, populate listbox."""
        raw = _scan_lots(self._use_full_site.get())
        self._lot_map_all = []
        for lot_id, csv_path in raw:
            layout = _get_lot_layout(csv_path)
            mat    = _get_lot_material(lot_id, csv_path)
            self._lot_map_all.append((lot_id, csv_path, layout, mat))
        self._apply_lot_filter(first_load=True)

    def _apply_lot_filter(self, first_load: bool = False):
        """Filter _lot_map_all by search text, repopulate listbox preserving selection."""
        # Remember which lots were selected before rebuild
        prev_sel: set = set()
        if not first_load and self._lot_filtered:
            prev_sel = {self._lot_filtered[i][0]
                        for i in self._lot_lb.curselection()}

        q = (self._lot_search_var.get() if self._lot_search_var else "").lower().strip()
        if q:
            self._lot_filtered = [
                t for t in self._lot_map_all
                if q in t[0].lower() or q in t[2].lower() or q in t[3].lower()
            ]
        else:
            self._lot_filtered = list(self._lot_map_all)

        self._lot_lb.delete(0, "end")
        for lot_id, _path, layout, mat in self._lot_filtered:
            parts = [f"{lot_id:<12}"]
            if layout:
                parts.append(f"{layout:<14}")
            if mat:
                parts.append(mat)
            label = "".join(parts).rstrip()
            self._lot_lb.insert("end", label)

        if first_load or not prev_sel:
            self._lot_lb.select_set(0, "end")
        else:
            for i, (lot_id, _, _, _) in enumerate(self._lot_filtered):
                if lot_id in prev_sel:
                    self._lot_lb.selection_set(i)

    def _select_all_lots(self):
        self._lot_lb.select_set(0, "end")

    def _clear_lots(self):
        self._lot_lb.select_clear(0, "end")

    # ── Parameter group helpers ───────────────────────────────────────────────

    def _load_param_groups(self):
        """Read groups from the Product Setup JSON and populate the group listbox."""
        setup_path = self._setup_var.get().strip()
        self._param_groups = []
        self._grp_lb.delete(0, "end")

        if not setup_path or not os.path.isfile(setup_path):
            # Fall back to built-in defaults
            groups = _default_product_setup().get("groups", [])
        else:
            try:
                with open(setup_path, "r", encoding="utf-8") as fh:
                    groups = json.load(fh).get("groups", [])
            except Exception:
                groups = _default_product_setup().get("groups", [])

        for g in groups:
            name = g.get("name", "?")
            pats = g.get("patterns", [])
            self._param_groups.append({"name": name, "patterns": pats})
            label = f"{name:<24s}  ({', '.join(pats)})"
            self._grp_lb.insert("end", label)

        # Select all by default
        self._grp_lb.select_set(0, "end")

    def _get_selected_groups(self) -> List[dict]:
        """Return the list of selected parameter group dicts."""
        return [
            self._param_groups[i]
            for i in self._grp_lb.curselection()
            if i < len(self._param_groups)
        ]

    def _browse_setup(self):
        init_dir = os.path.dirname(self._setup_var.get()) if self._setup_var.get() else _REPO_ROOT
        p = filedialog.askopenfilename(
            parent=self,
            title="Select Product Setup JSON",
            initialdir=init_dir,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if p:
            self._setup_var.set(p)

    def _browse_out(self):
        init_dir = self._out_var.get() or os.path.expanduser("~")
        p = filedialog.askdirectory(parent=self, title="Select Output Folder",
                                    initialdir=init_dir)
        if p:
            self._out_var.set(p)

    # ── Save / Load GUI state ─────────────────────────────────────────────────

    def _gui_state_dict(self) -> dict:
        """Capture current GUI state into a plain dict."""
        sel_lots = [
            self._lot_filtered[i][0]
            for i in self._lot_lb.curselection()
            if i < len(self._lot_filtered)
        ]
        return {
            "selected_lots":  sel_lots,
            "use_full_site":  self._use_full_site.get(),
            "setup_json":     self._setup_var.get(),
            "output_folder":  self._out_var.get(),
            "lot_search":     self._lot_search_var.get() if self._lot_search_var else "",
            "pcm_filter":    self._pcm_filter_var.get(),
            "selected_groups": [
                self._param_groups[i]["name"]
                for i in self._grp_lb.curselection()
                if i < len(self._param_groups)
            ],
        }

    def _save_setup(self):
        """Save current GUI state to a JSON file (prompts for path)."""
        init_dir = os.path.dirname(self._gui_state_path)
        os.makedirs(init_dir, exist_ok=True)
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save GUI Setup",
            initialdir=init_dir,
            initialfile=os.path.basename(self._gui_state_path),
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            state = self._gui_state_dict()
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
            self._gui_state_path = path
            self._status_lbl.configure(text=f"Saved: {os.path.basename(path)}")
            self._log(f"[Setup] GUI state saved to: {path}", "ok")
        except Exception as ex:
            messagebox.showerror("Save failed", str(ex), parent=self)

    def _load_setup(self):
        """Load GUI state from a JSON file (prompts for path)."""
        init_dir = os.path.dirname(self._gui_state_path)
        path = filedialog.askopenfilename(
            parent=self,
            title="Load GUI Setup",
            initialdir=init_dir,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
            self._gui_state_path = path
        except Exception as ex:
            messagebox.showerror("Load failed", str(ex), parent=self)
            return
        # Apply scalar fields immediately
        need_refresh = bool(state.get("use_full_site")) != self._use_full_site.get()
        if "use_full_site" in state:
            self._use_full_site.set(bool(state["use_full_site"]))
        if state.get("setup_json"):
            self._setup_var.set(state["setup_json"])
        if state.get("output_folder"):
            self._out_var.set(state["output_folder"])
        if self._lot_search_var:
            self._lot_search_var.set(state.get("lot_search", ""))
        self._pcm_filter_var.set(state.get("pcm_filter", ""))
        # Reload groups from JSON then restore selection
        self._load_param_groups()
        target_groups = set(state.get("selected_groups") or [])
        if target_groups:
            self._grp_lb.select_clear(0, "end")
            for i, g in enumerate(self._param_groups):
                if g["name"] in target_groups:
                    self._grp_lb.selection_set(i)
        # Re-scan if use_full_site changed
        if need_refresh:
            raw = _scan_lots(self._use_full_site.get())
            self._lot_map_all = []
            for lot_id, csv_path in raw:
                layout = _get_lot_layout(csv_path)
                mat    = _get_lot_material(lot_id, csv_path)
                self._lot_map_all.append((lot_id, csv_path, layout, mat))
            self._apply_lot_filter()
        # Restore lot selection
        target_lots = set(state.get("selected_lots") or [])
        if target_lots:
            self._lot_lb.select_clear(0, "end")
            for i, (lot_id, _, _, _) in enumerate(self._lot_filtered):
                if lot_id in target_lots:
                    self._lot_lb.selection_set(i)
        n_sel = len(self._lot_lb.curselection())
        self._status_lbl.configure(
            text=f"Loaded: {os.path.basename(path)}  ({n_sel} lots selected)")
        self._log(
            f"[Setup] GUI state loaded from: {path}  ({n_sel} lots selected)", "ok")

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _log(self, msg: str, tag: str = ""):
        self._log_box.configure(state="normal")
        self._log_box.insert("end", msg + "\n", tag)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _log_clear(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    # ── Run ───────────────────────────────────────────────────────────────────

    def _run(self):
        if self._running:
            return

        # Validate selections
        sel_indices = list(self._lot_lb.curselection())
        if not sel_indices:
            messagebox.showwarning("No lots selected",
                                   "Please select at least one lot from the list.",
                                   parent=self)
            return

        sel_lots = [(t[0], t[1]) for t in (self._lot_filtered[i] for i in sel_indices)]
        lot_csv_map = {lot: path for lot, path in sel_lots}

        # Product setup JSON
        setup_path = self._setup_var.get().strip()
        if not setup_path:
            setup_path = _DEFAULT_SETUP
        if not os.path.isfile(setup_path):
            messagebox.showwarning("Setup file not found",
                                   f"Product setup JSON not found:\n{setup_path}\n\n"
                                   "A sample will be created automatically.",
                                   parent=self)

        # Output folder
        out_folder = self._out_var.get().strip()
        if not out_folder:
            messagebox.showwarning("No output folder",
                                   "Please specify an output folder.", parent=self)
            return

        os.makedirs(out_folder, exist_ok=True)

        # Disable run button
        self._running = True
        self._run_btn.configure(state="disabled", text="⏳ Running…")
        self._status_lbl.configure(text="")
        self._log_clear()

        pcm_filter = self._pcm_filter_var.get().strip()
        selected_groups = self._get_selected_groups()

        threading.Thread(
            target=self._worker,
            args=(lot_csv_map, setup_path, out_folder,
                  self._use_full_site.get(), pcm_filter, selected_groups),
            daemon=True,
        ).start()

    def _worker(self, lot_csv_map: dict, setup_path: str,
                out_folder: str, use_full_site: bool,
                pcm_filter: str = "", selected_groups: list = None):
        try:
            self._do_generate(lot_csv_map, setup_path, out_folder,
                              pcm_filter, selected_groups)
        except Exception as ex:
            self.after(0, self._log, f"[ERROR] {ex}", "err")
            import traceback
            self.after(0, self._log, traceback.format_exc(), "err")
        finally:
            self.after(0, self._finish)

    def _do_generate(self, lot_csv_map: dict, setup_path: str, out_folder: str,
                     pcm_filter: str = "", selected_groups: list = None):
        from generate_pcm_html import generate_html

        log = lambda msg, tag="": self.after(0, self._log, msg, tag)

        log("=" * 60)
        log(f"PCM Dashboard — {len(lot_csv_map)} lot(s) selected")
        log("=" * 60)

        # Load/create product setup JSON
        if os.path.isfile(setup_path):
            log(f"[Setup] {setup_path}")
            with open(setup_path, "r", encoding="utf-8") as fh:
                product_setup = json.load(fh)
        else:
            log(f"[Setup] Not found — using built-in defaults")
            product_setup = _default_product_setup()
            # Save for future use
            os.makedirs(os.path.dirname(os.path.abspath(setup_path)), exist_ok=True)
            with open(setup_path, "w", encoding="utf-8") as fh:
                json.dump(product_setup, fh, indent=2)
            log(f"[Setup] Default saved to: {setup_path}")

        # Apply group selection — only pass selected groups to generate_html
        all_groups = product_setup.get("groups", [])
        if selected_groups is not None and len(selected_groups) < len(all_groups):
            sel_names = {g["name"] for g in selected_groups}
            product_setup["groups"] = [
                g for g in all_groups if g.get("name") in sel_names
            ]
            log(f"[Groups] {len(product_setup['groups'])}/{len(all_groups)} groups selected: "
                + ", ".join(sel_names))
        else:
            log(f"[Groups] All {len(all_groups)} groups selected")

        # Load and merge PCM data
        df = _load_and_merge(lot_csv_map, log)
        if df.empty:
            log("[ERROR] No data loaded — check lot selection.", "err")
            return

        # Load spec limits
        log(f"[Spec ] {_SPEC_CSV}")
        spec_lookup = _load_spec_lookup()
        log(f"[Spec ] {len(spec_lookup)} parameters with limits")

        # Apply PCM parameter filter if provided
        if pcm_filter:
            patterns = [p.strip() for p in pcm_filter.split(",") if p.strip()]
            all_pcm = [
                c for c in df.columns
                if c not in _PCM_ID_COLS and pd.api.types.is_numeric_dtype(df[c])
            ]
            keep = [
                c for c in all_pcm
                if any(fnmatch.fnmatch(c.upper(), pat.upper()) for pat in patterns)
            ]
            drop = [c for c in all_pcm if c not in keep]
            if drop:
                df = df.drop(columns=drop)
            log(f"[Filter] '{pcm_filter}' → {len(keep)}/{len(all_pcm)} params kept")
            if not keep:
                log("[ERROR] No PCM columns matched the filter — check spelling / wildcard.", "err")
                return

        # Generate HTML
        output_html = os.path.join(out_folder, "pcm_dashboard.html")
        log(f"[HTML ] Generating charts and HTML…")
        generate_html(df, product_setup, output_html, spec_lookup)
        log(f"[Done ] Saved: {output_html}", "ok")
        self._last_html = output_html
        self.after(0, lambda: self._open_btn.configure(state="normal"))

    def _finish(self):
        self._running = False
        self._run_btn.configure(state="normal", text="▶  Generate Dashboard")

    def _open_dashboard(self):
        if self._last_html and os.path.isfile(self._last_html):
            webbrowser.open(self._last_html)


# ---------------------------------------------------------------------------
# Default product setup
# ---------------------------------------------------------------------------

def _default_product_setup() -> dict:
    """Return a sensible default product setup for NVL816 PCM data."""
    return {
        "title": "NVL816 PCM / ETest Dashboard",
        "subtitle": "Auto-generated — edit shared/setup/etest-dashboard/pcm_product_setup.json to customise",
        "groups": [
            {
                "name": "Conductance",
                "patterns": ["Con_*"]
            },
            {
                "name": "Capacitance",
                "patterns": ["Cmim_*", "Cmin_*"]
            },
            {
                "name": "Vts N-FET",
                "patterns": ["Vts_RN*", "Vts_N*", "Vtl_N*"]
            },
            {
                "name": "Vts P-FET",
                "patterns": ["Vts_RP*", "Vts_P*", "Vtl_P*"]
            },
            {
                "name": "Vts GAA / Stacked",
                "patterns": ["Vts_GAA*", "Vts_GBA*", "Vts_DAA*", "Vts_DBA*",
                             "Vts_UAA*", "Vts_UBA*"]
            },
            {
                "name": "Isat N-FET",
                "patterns": ["Isat_RN*", "Isat_N*"]
            },
            {
                "name": "Isat P-FET",
                "patterns": ["Isat_RP*", "Isat_P*"]
            },
            {
                "name": "Isat GAA / Stacked",
                "patterns": ["Isat_GAA*", "Isat_GBA*", "Isat_DAA*", "Isat_DBA*",
                             "Isat_UAA*", "Isat_UBA*"]
            },
            {
                "name": "Ioff N-FET",
                "patterns": ["Ioff_RN*"]
            },
            {
                "name": "Ioff P-FET",
                "patterns": ["Ioff_RP*"]
            },
            {
                "name": "Contact Resistance",
                "patterns": ["Rc_*"]
            },
            {
                "name": "Sheet Resistance",
                "patterns": ["Rs_*", "RDL_*", "SPA_*"]
            },
            {
                "name": "Propagation Delay",
                "patterns": ["Td_*"]
            },
            {
                "name": "Power (Pwr)",
                "patterns": ["Pwr_*"]
            },
            {
                "name": "Power-Off (Poff)",
                "patterns": ["Poff_*"]
            },
            {
                "name": "Breakdown / Other",
                "patterns": ["VbdGO_*", "VBD_*", "Isb_*"]
            },
        ],
    }
