"""
manage_automation.py  —  GUI to manage CLASS-dashboard automation.

Tabs:
  1. Settings & Programs  — email recipients + TP pattern list (wildcard supported)
  2. Run History          — NVL_Class_YYYYMMDD_HHMMSS/ run folders; view/open
  3. Data Files           — data/programs/*.csv.gz per-TP snapshots
  4. Schedule             — Windows Task Scheduler: create, check, run now, remove

Usage:
    python manage_automation.py
    python manage_automation.py --base-dir "\\\\server\\share\\auto\\class"
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess as _sp
import sys as _sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

# ── defaults ───────────────────────────────────────────────────────────────────
_HERE      = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent   # app.dashboard.nvl/
_BASE_DIR  = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\class")
_CFG_DIR   = _REPO_ROOT / "shared" / "setup" / "automation" / "class-dashboard"
_CFG_NAME  = "class_setup_config.json"
_EMAIL_TO  = "sujit.n.pant@intel.com"
_TASK_NAME = "NVL-BLLC Class Automation"

_DEFAULT_TP_PATTERNS = [
    "NVLSB63A0H54A0ACX22",
    "NVLSB63A0H54A0BS622",
    "NVLSB63A0H54A0CCX22",
]

# ── colours ────────────────────────────────────────────────────────────────────
BG         = "#1a252f"
BG2        = "#1e2e3d"
BG3        = "#263950"
FG         = "#e8f0f7"
FG_DIM     = "#90a4ae"
ACCENT     = "#4fc3f7"
GREEN      = "#66bb6a"
RED        = "#ef5350"
AMBER      = "#ffa726"
FONT_MONO  = ("Courier New", 9)
FONT_UI    = ("Segoe UI", 10)
FONT_TITLE = ("Segoe UI", 13, "bold")
FONT_GROUP = ("Segoe UI", 10, "bold")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_config(cfg_path: Path) -> dict:
    if cfg_path.exists():
        try:
            d = json.loads(cfg_path.read_text(encoding="utf-8"))
            if "email_to" in d and "email_to_report" not in d:
                d["email_to_report"] = d.pop("email_to")
            return d
        except Exception:
            pass
    return {
        "email_to_report": _EMAIL_TO,
        "email_to_alert":  _EMAIL_TO,
        "tp_patterns":     _DEFAULT_TP_PATTERNS[:],
    }


def _save_config(cfg_path: Path, cfg: dict) -> None:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


def _dir_size(p: Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except Exception:
        return 0


def _mtime_str(p: Path) -> str:
    try:
        return datetime.datetime.fromtimestamp(
            p.stat().st_mtime
        ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


# ─────────────────────────────────────────────────────────────────────────────
# Main manager widget
# ─────────────────────────────────────────────────────────────────────────────

class AutomationManager(tk.Frame):

    def __init__(self, master, base_dir: Path) -> None:
        super().__init__(master, bg=BG)
        self.base_dir = base_dir
        self.cfg_path = _CFG_DIR / _CFG_NAME
        self.cfg      = _load_config(self.cfg_path)

        self._apply_styles()
        self._build_ui()

    # ── shared helpers ─────────────────────────────────────────────────────────

    def _btn(self, parent, text, cmd, bg=BG3, fg=FG, **kw):
        return tk.Button(
            parent, text=text, command=cmd,
            font=FONT_UI, bg=bg, fg=fg,
            activebackground=ACCENT, activeforeground=BG,
            relief="flat", padx=10, pady=3, cursor="hand2", **kw
        )

    def _apply_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("TNotebook",     background=BG,  borderwidth=0)
        style.configure("TNotebook.Tab", background=BG3, foreground=FG_DIM,
                        padding=[14, 5], font=("Segoe UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", BG2)],
                  foreground=[("selected", ACCENT)])
        style.configure("Treeview",
                        background=BG2, foreground=FG,
                        fieldbackground=BG2, rowheight=24,
                        font=FONT_MONO)
        style.configure("Treeview.Heading",
                        background=BG3, foreground=ACCENT,
                        font=FONT_GROUP, relief="flat")
        style.map("Treeview",
                  background=[("selected", "#2a4a6a")],
                  foreground=[("selected", FG)])

    # ── top-level layout ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        hdr = tk.Frame(self, bg=BG3)
        hdr.pack(fill="x")
        tk.Label(hdr, text="CLASS Automation Manager", font=FONT_TITLE,
                 bg=BG3, fg=ACCENT).pack(side="left", padx=14, pady=8)
        info = tk.Frame(hdr, bg=BG3)
        info.pack(side="left", padx=4)
        tk.Label(info, text=f"base_dir: {self.base_dir}", font=("Segoe UI", 10, "bold"),
                 bg=BG3, fg="#5BB8FF").pack(anchor="w")
        tk.Label(info, text=f"config: {self.cfg_path}", font=("Segoe UI", 9),
                 bg=BG3, fg="#7ECFFF").pack(anchor="w")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self._tab_settings = tk.Frame(nb, bg=BG)
        self._tab_history  = tk.Frame(nb, bg=BG)
        self._tab_data     = tk.Frame(nb, bg=BG)
        self._tab_schedule = tk.Frame(nb, bg=BG)

        nb.add(self._tab_settings, text="  Settings & Programs  ")
        nb.add(self._tab_history,  text="  Run History  ")
        nb.add(self._tab_data,     text="  Data Files  ")
        nb.add(self._tab_schedule, text="  Schedule  ")

        self._build_settings_tab()
        self._build_history_tab()
        self._build_data_tab()
        self._build_schedule_tab()

        nb.bind("<<NotebookTabChanged>>", self._on_tab_change)

    def _on_tab_change(self, event) -> None:
        idx = event.widget.index("current")
        if idx == 1:
            self._refresh_history()
        elif idx == 2:
            self._refresh_data()
        elif idx == 3:
            self._sched_refresh()

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 1 — Settings & Programs
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_settings_tab(self) -> None:
        p   = self._tab_settings
        pad = dict(padx=14, pady=6)

        # Action bar
        top = tk.Frame(p, bg=BG)
        top.pack(fill="x", padx=14, pady=(10, 2))
        tk.Label(top, text=f"Config: {self.cfg_path}", font=("Segoe UI", 8),
                 bg=BG, fg=FG_DIM).pack(side="left")
        self._btn(top, "Save All", self._save_settings,
                  bg="#1b5e20", fg="#00ff7f").pack(side="right")

        # ── Recipients ──────────────────────────────────────────────────────
        frm_email = tk.LabelFrame(p, text="  Recipients  ", font=FONT_UI,
                                  bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_email.pack(fill="x", **pad)

        self._report_email_var = tk.StringVar(
            value=self.cfg.get("email_to_report", _EMAIL_TO))
        self._alert_email_var  = tk.StringVar(
            value=self.cfg.get("email_to_alert",
                               self.cfg.get("email_to_report", _EMAIL_TO)))

        for row, label, var, color, note in [
            (0, "Report To:", self._report_email_var, GREEN,
             "Final report recipients (semicolons OK)"),
            (1, "Alerts To:", self._alert_email_var,  AMBER,
             "AQUA errors / pipeline failures"),
        ]:
            tk.Label(frm_email, text=label, font=FONT_UI, bg=BG, fg=color
                     ).grid(row=row, column=0, sticky="w", padx=8, pady=4)
            tk.Entry(frm_email, textvariable=var, font=FONT_UI, bg=BG2, fg=FG,
                     insertbackground=FG, relief="flat", width=48
                     ).grid(row=row, column=1, padx=8, pady=4, sticky="ew")
            tk.Label(frm_email, text=note, font=("Segoe UI", 7), bg=BG, fg=FG_DIM
                     ).grid(row=row, column=2, sticky="w", padx=(0, 8))
        frm_email.columnconfigure(1, weight=1)

        # ── Group Email ──────────────────────────────────────────────────────
        tk.Frame(frm_email, bg="#2a4060", height=1
                 ).grid(row=2, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 0))

        self._group_email_enabled = tk.BooleanVar(
            value=bool(self.cfg.get("email_group_enabled", False)))
        chk_row = tk.Frame(frm_email, bg=BG)
        chk_row.grid(row=3, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 0))
        self._group_chk = tk.Checkbutton(
            chk_row,
            text="Send report to group  (overrides \"Report To\" above)",
            variable=self._group_email_enabled,
            font=FONT_UI, bg=BG, fg=FG, selectcolor=BG2,
            activebackground=BG, activeforeground=FG,
            command=self._toggle_group_email,
        )
        self._group_chk.pack(side="left")

        tk.Label(frm_email, text="Group Emails:", font=FONT_UI, bg=BG, fg=ACCENT,
                 ).grid(row=4, column=0, sticky="nw", padx=8, pady=(4, 2))
        self._group_email_text = scrolledtext.ScrolledText(
            frm_email, height=3,
            font=("Consolas", 9), bg="#0d1b26", fg="#a8d8ea",
            insertbackground=FG, relief="flat",
        )
        self._group_email_text.grid(row=4, column=1, columnspan=2, sticky="ew",
                                    padx=8, pady=(4, 2))
        tk.Label(frm_email, text="One address per line (semicolons also OK)",
                 font=("Segoe UI", 7), bg=BG, fg=FG_DIM,
                 ).grid(row=5, column=1, sticky="w", padx=8, pady=(0, 6))

        # Populate from config
        _grp_addrs = self.cfg.get("email_to_group", [])
        if isinstance(_grp_addrs, list):
            self._group_email_text.insert("1.0", "\n".join(_grp_addrs))
        elif isinstance(_grp_addrs, str):
            self._group_email_text.insert("1.0", _grp_addrs)
        self._toggle_group_email()

        # ── Keep Runs ───────────────────────────────────────────────────────
        frm_keep = tk.LabelFrame(p, text="  Run Folder Cleanup  ", font=FONT_UI,
                                 bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_keep.pack(fill="x", **pad)

        self._keep_runs_var = tk.StringVar(
            value=str(self.cfg.get("keep_runs", 0)))
        kr_row = tk.Frame(frm_keep, bg=BG)
        kr_row.pack(fill="x", padx=8, pady=6)
        tk.Label(kr_row, text="Keep N most-recent run folders (0 = disabled):",
                 font=FONT_UI, bg=BG, fg=FG).pack(side="left", padx=(0, 8))
        tk.Entry(kr_row, textvariable=self._keep_runs_var,
                 font=FONT_MONO, bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", width=6).pack(side="left")
        tk.Label(kr_row, text="  (passed to run_automation.py --keep-runs)",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM).pack(side="left", padx=(6, 0))

        # ── Test Program Patterns ───────────────────────────────────────────
        frm_tp = tk.LabelFrame(p, text="  Test Program Patterns  ", font=FONT_UI,
                               bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_tp.pack(fill="both", expand=True, **pad)

        tk.Label(
            frm_tp,
            text=(
                "One pattern per line. Wildcards supported (fnmatch style).\n"
                "Only TestPrograms matching ANY of these patterns will be processed and emailed.\n"
                "Leave blank to process ALL programs in the AQUA pull."
            ),
            font=("Segoe UI", 8), bg=BG, fg=FG_DIM, justify="left",
        ).pack(anchor="w", padx=8, pady=(4, 2))

        btn_row = tk.Frame(frm_tp, bg=BG)
        btn_row.pack(fill="x", padx=8, pady=(0, 4))
        self._btn(btn_row, "↺ Reset to defaults", self._reset_tp_patterns
                  ).pack(side="left")
        self._btn(btn_row, "✔ Validate", self._validate_tp_patterns
                  ).pack(side="left", padx=(6, 0))

        self._tp_text = scrolledtext.ScrolledText(
            frm_tp, height=8,
            font=("Consolas", 10), bg="#0d1b26", fg="#a8d8ea",
            insertbackground=FG, relief="flat",
        )
        self._tp_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Populate from config
        patterns = self.cfg.get("tp_patterns", _DEFAULT_TP_PATTERNS[:])
        self._tp_text.insert("1.0", "\n".join(patterns))

        # ── V2 Excluded Programs ───────────────────────────────────────
        frm_v2ex = tk.LabelFrame(
            p, text="  V2 Full-Data — Excluded Programs  ",
            font=FONT_UI, bg=BG, fg=ACCENT, bd=1, relief="groove"
        )
        frm_v2ex.pack(fill="both", expand=False, **pad)

        # Max-age row
        age_row = tk.Frame(frm_v2ex, bg=BG)
        age_row.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(age_row, text="Include programs written within the last",
                 font=FONT_UI, bg=BG, fg=FG).pack(side="left")
        self._v2_max_age_var = tk.StringVar(
            value=str(self.cfg.get("v2_max_age_days", 7)))
        tk.Spinbox(age_row, from_=1, to=365, width=4,
                   textvariable=self._v2_max_age_var,
                   font=FONT_MONO, bg=BG2, fg=FG,
                   buttonbackground=BG2, relief="flat"
                   ).pack(side="left", padx=(6, 4))
        tk.Label(age_row, text="day(s)  (uses file's disk timestamp)",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM).pack(side="left")

        tk.Label(
            frm_v2ex,
            text=(
                "One TP name per line (fnmatch wildcards OK).\n"
                "Programs matching these patterns are excluded even if within the age limit."
            ),
            font=("Segoe UI", 8), bg=BG, fg=FG_DIM, justify="left",
        ).pack(anchor="w", padx=8, pady=(4, 2))

        v2ex_btn_row = tk.Frame(frm_v2ex, bg=BG)
        v2ex_btn_row.pack(fill="x", padx=8, pady=(0, 4))
        self._btn(v2ex_btn_row, "↺ Clear", self._clear_v2_excluded
                  ).pack(side="left")

        self._v2_excluded_text = scrolledtext.ScrolledText(
            frm_v2ex, height=4,
            font=("Consolas", 10), bg="#0d1b26", fg="#a8d8ea",
            insertbackground=FG, relief="flat",
        )
        self._v2_excluded_text.pack(fill="both", expand=False, padx=8, pady=(0, 8))

        # Populate from config
        v2_excluded = self.cfg.get("v2_excluded_tps", [])
        self._v2_excluded_text.insert("1.0", "\n".join(v2_excluded))

        # Status bar
        self._settings_status = tk.StringVar()
        tk.Label(p, textvariable=self._settings_status, font=("Segoe UI", 9),
                 bg=BG, fg=GREEN).pack(anchor="w", padx=14, pady=(0, 6))

    def _get_v2_excluded(self) -> list[str]:
        raw = self._v2_excluded_text.get("1.0", "end")
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _clear_v2_excluded(self) -> None:
        self._v2_excluded_text.delete("1.0", "end")
        self._settings_status.set("V2 exclusion list cleared (not saved)")

    def _toggle_group_email(self) -> None:
        state = "normal" if self._group_email_enabled.get() else "disabled"
        self._group_email_text.configure(state=state)

    def _get_group_emails(self) -> list[str]:
        raw = self._group_email_text.get("1.0", "end")
        addrs = []
        for line in raw.splitlines():
            for part in line.split(";"):
                a = part.strip()
                if a:
                    addrs.append(a)
        return addrs

    def _get_tp_patterns(self) -> list[str]:
        raw = self._tp_text.get("1.0", "end")
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _reset_tp_patterns(self) -> None:
        self._tp_text.delete("1.0", "end")
        self._tp_text.insert("1.0", "\n".join(_DEFAULT_TP_PATTERNS))
        self._settings_status.set("Reset to defaults (not saved)")

    def _validate_tp_patterns(self) -> None:
        patterns = self._get_tp_patterns()
        if not patterns:
            messagebox.showinfo("TP Patterns", "No patterns — ALL programs will be processed.")
        else:
            msg = f"{len(patterns)} pattern(s):\n" + "\n".join(f"  • {p}" for p in patterns)
            messagebox.showinfo("TP Patterns", msg)

    def _save_settings(self) -> None:
        try:
            keep = int(self._keep_runs_var.get())
        except ValueError:
            messagebox.showerror("Invalid", "Keep-runs must be an integer.")
            return

        self.cfg["email_to_report"]    = self._report_email_var.get().strip()
        self.cfg["email_to_alert"]     = self._alert_email_var.get().strip()
        self.cfg["email_group_enabled"] = self._group_email_enabled.get()
        self.cfg["email_to_group"]     = self._get_group_emails()
        self.cfg["keep_runs"]          = keep
        self.cfg["tp_patterns"]     = self._get_tp_patterns()
        self.cfg["v2_excluded_tps"] = self._get_v2_excluded()
        try:
            self.cfg["v2_max_age_days"] = int(self._v2_max_age_var.get())
        except ValueError:
            messagebox.showerror("Invalid", "V2 max age must be an integer.")
            return

        _save_config(self.cfg_path, self.cfg)
        self._settings_status.set(
            f"Saved → {self.cfg_path.name}  "
            f"({len(self.cfg['tp_patterns'])} patterns, "
            f"{len(self.cfg['v2_excluded_tps'])} V2 exclusions)"
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 2 — Run History
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_history_tab(self) -> None:
        p = self._tab_history

        # Toolbar
        tb = tk.Frame(p, bg=BG3)
        tb.pack(fill="x", padx=0, pady=0)
        self._btn(tb, "↺ Refresh",     self._refresh_history).pack(side="left",  padx=6, pady=4)
        self._btn(tb, "📂 Open Folder", self._hist_open_folder).pack(side="left", padx=(0, 6), pady=4)
        self._btn(tb, "✉ Send Report",  self._hist_send_email).pack(side="left",  padx=(0, 6), pady=4)
        self._btn(tb, "� Save Report",  self._hist_save_report).pack(side="left",  padx=(0, 6), pady=4)
        self._btn(tb, "�🗑 Delete",       self._hist_delete,
                  bg=RED, fg="white").pack(side="right", padx=6, pady=4)
        self._btn(tb, "🔍 Preview Cleanup", self._hist_preview_cleanup
                  ).pack(side="right", padx=(0, 6), pady=4)

        # Treeview
        cols = ("run", "tps", "size", "when")
        self._hist_tree = ttk.Treeview(p, columns=cols, show="headings",
                                       selectmode="extended")
        for col, label, width in [
            ("run",  "Run Folder",    260),
            ("tps",  "TPs",           200),
            ("size", "Size",           80),
            ("when", "Modified",      140),
        ]:
            self._hist_tree.heading(col, text=label)
            self._hist_tree.column(col, width=width, minwidth=60)

        vsb = ttk.Scrollbar(p, orient="vertical",   command=self._hist_tree.yview)
        hsb = ttk.Scrollbar(p, orient="horizontal",  command=self._hist_tree.xview)
        self._hist_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        p.rowconfigure(1, weight=1)
        p.columnconfigure(0, weight=1)
        tb.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._hist_tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")

        # Context menu
        self._hist_ctx = tk.Menu(self._hist_tree, tearoff=0, bg=BG2, fg=FG,
                                 activebackground=ACCENT, activeforeground=BG)
        self._hist_ctx.add_command(label="📂  Open Folder",   command=self._hist_open_folder)
        self._hist_ctx.add_command(label="✉  Send Report",    command=self._hist_send_email)
        self._hist_ctx.add_command(label="💾  Save Report",    command=self._hist_save_report)
        self._hist_ctx.add_separator()
        self._hist_ctx.add_command(label="🗑  Delete",         command=self._hist_delete)
        self._hist_tree.bind("<Button-3>", self._hist_context_menu)
        self._hist_tree.bind("<Double-1>", lambda _e: self._hist_open_folder())

        self._hist_status = tk.StringVar()
        tk.Label(p, textvariable=self._hist_status, font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM).grid(row=3, column=0, sticky="w", padx=8, pady=4)

    def _hist_run_dirs(self) -> list[Path]:
        output_dir = self.base_dir / "output"
        if not output_dir.exists():
            return []
        return sorted(
            [d for d in output_dir.iterdir()
             if d.is_dir() and re.match(r'^NVL_Class_\d{8}_\d{6}$', d.name)],
            key=lambda d: d.name,
            reverse=True,
        )

    def _refresh_history(self) -> None:
        self._hist_tree.delete(*self._hist_tree.get_children())
        run_dirs = self._hist_run_dirs()
        for d in run_dirs:
            tps = [s.name for s in d.iterdir() if s.is_dir()]
            tp_str = ", ".join(sorted(tps)[:4])
            if len(tps) > 4:
                tp_str += f" (+{len(tps) - 4})"
            size = _dir_size(d)
            self._hist_tree.insert("", "end", values=(
                d.name,
                tp_str or "—",
                _fmt_size(size),
                _mtime_str(d),
            ), tags=(str(d),))
        self._hist_status.set(f"{len(run_dirs)} run folder(s)")

    def _hist_selected_dirs(self) -> list[Path]:
        dirs = []
        for iid in self._hist_tree.selection():
            tag = self._hist_tree.item(iid, "tags")
            if tag:
                dirs.append(Path(tag[0]))
        return dirs

    def _hist_context_menu(self, event) -> None:
        row = self._hist_tree.identify_row(event.y)
        if row:
            self._hist_tree.selection_set(row)
            self._hist_ctx.post(event.x_root, event.y_root)

    def _hist_open_folder(self) -> None:
        dirs = self._hist_selected_dirs()
        if not dirs:
            messagebox.showinfo("Open Folder", "Select a run first.")
            return
        for d in dirs[:3]:
            if d.exists():
                try:
                    os.startfile(str(d))
                except Exception as e:
                    messagebox.showerror("Open Folder",
                                        f"Could not open folder:\n{d}\n\n{e}")

    def _hist_delete(self) -> None:
        dirs = self._hist_selected_dirs()
        if not dirs:
            messagebox.showinfo("Delete", "Select run folder(s) first.")
            return
        names = "\n".join(d.name for d in dirs)
        if not messagebox.askyesno("Confirm Delete",
                                   f"Delete {len(dirs)} run folder(s)?\n\n{names}"):
            return
        for d in dirs:
            try:
                shutil.rmtree(str(d))
            except Exception as e:
                messagebox.showerror("Error", f"Could not delete {d.name}:\n{e}")
        self._refresh_history()

    def _hist_preview_cleanup(self) -> None:
        import sys as _sys
        _sys.path.insert(0, str(_HERE / "automation"))
        from run_automation import _preview_cleanup
        try:
            keep = int(self.cfg.get("keep_runs", 0))
        except ValueError:
            keep = 0
        if keep <= 0:
            messagebox.showinfo("Preview Cleanup",
                                "keep_runs is 0 — cleanup disabled.\nSet it in Settings tab.")
            return
        output_dir = self.base_dir / "output"
        to_delete  = _preview_cleanup(output_dir, keep)
        if not to_delete:
            messagebox.showinfo("Preview Cleanup",
                                f"Nothing to delete (keeping {keep}, fewer exist).")
        else:
            msg = (
                f"Would delete {len(to_delete)} folder(s) "
                f"(keeping {keep} most-recent):\n\n"
                + "\n".join(f"  • {d.name}" for d in to_delete)
            )
            messagebox.showinfo("Preview Cleanup", msg)

    def _hist_send_email(self) -> None:
        """Evaluate ALL output run folders and send a combined historical report."""
        run_dirs = self._hist_run_dirs()
        if not run_dirs:
            messagebox.showinfo("Send Report", "No run folders found.")
            return

        output_dir = self.base_dir / "output"
        if (self.cfg.get("email_group_enabled")
                and self.cfg.get("email_to_group")):
            _grp = self.cfg["email_to_group"]
            to   = "; ".join(a for a in _grp if a) if isinstance(_grp, list) else str(_grp)
        else:
            to = self.cfg.get("email_to_report", _EMAIL_TO)
        run_log    = self.base_dir / "run_log.html"
        latest_ts  = run_dirs[0].name.replace("NVL_Class_", "")
        date_fmt   = f"{latest_ts[:4]}-{latest_ts[4:6]}-{latest_ts[6:8]}"

        preview_msg = (
            f"Send CLASS report to: {to}\n\n"
            f"Will evaluate {len(run_dirs)} run folder(s):\n"
            + "\n".join(f"  \u2022 {d.name}" for d in run_dirs[:8])
            + (f"\n  \u2026 ({len(run_dirs) - 8} more)" if len(run_dirs) > 8 else "")
        )
        if not messagebox.askyesno("Send Report", preview_msg):
            return

        def _send():
            try:
                _sys.modules.pop("run_automation", None)
                _sys.path.insert(0, str(_HERE / "automation"))
                from run_automation import (
                    build_class_email_body, load_run_history, send_email,
                )
                # load_run_history re-reads .summary.json from each HTML output
                # on disk — no need to re-run the pipeline
                run_records = load_run_history(output_dir)
                if not run_records:
                    self.after(0, lambda: messagebox.showwarning(
                        "No Data", "No run records found in output folder."))
                    return

                n_runs = len(run_records)
                n_ok   = sum(
                    1 for r in run_records[0].get("tp_results", []) if r["ok"]
                )
                n_tot  = len(run_records[0].get("tp_results", []))
                body    = build_class_email_body(
                    run_records, run_log,
                    exclude_patterns=self.cfg.get("email_exclude_patterns") or [],
                )
                subject = (
                    f"NVL816-BLLC CLASS Report \u2014 {date_fmt} "
                    f"({n_ok}/{n_tot} TPs, {n_runs} run(s))"
                )
                send_email(to, subject, body, dry_run=False)
                # ── Also save to reports/ ──────────────────────────────────
                from datetime import datetime as _dt
                _reports_dir = self.base_dir / "reports"
                _reports_dir.mkdir(parents=True, exist_ok=True)
                _saved = _reports_dir / f"Class_Report_{_dt.now().strftime('%Y%m%d_%H%M%S')}.html"
                _saved.write_text(body, encoding="utf-8")
                self.after(0, lambda: messagebox.showinfo(
                    "Sent",
                    f"Email sent to {to}\n{n_runs} run(s) included.\n\nSaved → {_saved.name}"))
            except Exception as exc:
                import traceback
                _tb = traceback.format_exc()[:400]
                self.after(0, lambda e=exc, t=_tb: messagebox.showerror(
                    "Error", f"Send failed:\n{e}\n\n{t}"))

        threading.Thread(target=_send, daemon=True).start()

    def _hist_save_report(self) -> None:
        """Build CLASS report HTML and save to reports/ folder — no email."""
        run_dirs = self._hist_run_dirs()
        if not run_dirs:
            messagebox.showinfo("Save Report", "No run folders found.")
            return
        output_dir  = self.base_dir / "output"
        reports_dir = self.base_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        run_log = self.base_dir / "run_log.html"

        def _save():
            try:
                _sys.modules.pop("run_automation", None)
                _sys.path.insert(0, str(_HERE / "automation"))
                from run_automation import build_class_email_body, load_run_history
                run_records = load_run_history(output_dir)
                if not run_records:
                    self.after(0, lambda: messagebox.showwarning(
                        "No Data", "No run records found in output folder."))
                    return
                body = build_class_email_body(
                    run_records, run_log,
                    exclude_patterns=self.cfg.get("email_exclude_patterns") or [],
                )
                from datetime import datetime as _dt
                ts_file  = _dt.now().strftime("%Y%m%d_%H%M%S")
                out_path = reports_dir / f"Class_Report_{ts_file}.html"
                out_path.write_text(body, encoding="utf-8")
                def _done():
                    self._hist_status.set(f"Saved \u2192 {out_path.name}")
                    import webbrowser
                    webbrowser.open(out_path.as_uri())
                self.after(0, _done)
            except Exception as exc:
                import traceback
                _tb = traceback.format_exc()[:400]
                self.after(0, lambda e=exc, t=_tb: messagebox.showerror(
                    "Error", f"Save failed:\n{e}\n\n{t}"))

        threading.Thread(target=_save, daemon=True).start()

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 3 — Data Files
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_data_tab(self) -> None:
        p = self._tab_data

        tb = tk.Frame(p, bg=BG3)
        tb.pack(fill="x")
        self._btn(tb, "↺ Refresh",       self._refresh_data).pack(side="left", padx=6, pady=4)
        self._btn(tb, "📂 Open programs/", self._data_open_dir).pack(side="left", padx=(0, 6), pady=4)
        self._btn(tb, "🗑 Delete Selected", self._data_delete,
                  bg=RED, fg="white").pack(side="right", padx=6, pady=4)

        cols = ("file", "tp", "size", "when")
        self._data_tree = ttk.Treeview(p, columns=cols, show="headings",
                                       selectmode="extended")
        for col, label, width in [
            ("file", "File",          280),
            ("tp",   "Test Program",  220),
            ("size", "Size",           80),
            ("when", "Modified",      130),
        ]:
            self._data_tree.heading(col, text=label)
            self._data_tree.column(col, width=width, minwidth=60)

        vsb = ttk.Scrollbar(p, orient="vertical",  command=self._data_tree.yview)
        hsb = ttk.Scrollbar(p, orient="horizontal", command=self._data_tree.xview)
        self._data_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        p.rowconfigure(1, weight=1)
        p.columnconfigure(0, weight=1)
        tb.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._data_tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")

        self._data_status = tk.StringVar()
        tk.Label(p, textvariable=self._data_status, font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM).grid(row=3, column=0, sticky="w", padx=8, pady=4)

    def _refresh_data(self) -> None:
        self._data_tree.delete(*self._data_tree.get_children())
        prog_dir = self.base_dir / "data" / "programs"
        if not prog_dir.exists():
            self._data_status.set("data/programs/ not found")
            return

        files = sorted(
            list(prog_dir.glob("*.csv.gz")) + list(prog_dir.glob("*.7z")),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for f in files:
            # Derive TP name from filename (strip extension(s))
            stem = f.name
            for ext in (".7z", ".csv", ".gz"):
                stem = stem.replace(ext, "")
            self._data_tree.insert("", "end", values=(
                f.name,
                stem,
                _fmt_size(f.stat().st_size),
                _mtime_str(f),
            ), tags=(str(f),))

        total = sum(f.stat().st_size for f in files)
        self._data_status.set(f"{len(files)} file(s) — {_fmt_size(total)} total")

    def _data_open_dir(self) -> None:
        d = self.base_dir / "data" / "programs"
        if d.exists():
            os.startfile(str(d))
        else:
            messagebox.showinfo("Not Found", f"Directory not yet created:\n{d}")

    def _data_delete(self) -> None:
        files = []
        for iid in self._data_tree.selection():
            tag = self._data_tree.item(iid, "tags")
            if tag:
                files.append(Path(tag[0]))
        if not files:
            messagebox.showinfo("Delete", "Select file(s) first.")
            return
        if not messagebox.askyesno("Confirm Delete",
                                   f"Delete {len(files)} data file(s)?"):
            return
        for f in files:
            try:
                f.unlink()
            except Exception as e:
                messagebox.showerror("Error", f"Could not delete {f.name}:\n{e}")
        self._refresh_data()

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 4 — Schedule
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_schedule_tab(self) -> None:
        p   = self._tab_schedule
        pad = dict(padx=14, pady=6)

        _python = _sys.executable
        _script = str(_HERE / "automation" / "run_automation.py")

        # ── Task Status card ─────────────────────────────────────────────────
        frm_st = tk.LabelFrame(p, text="  Task Status  ", font=FONT_UI,
                               bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_st.pack(fill="x", **pad)

        self._sched_dot   = tk.Label(frm_st, text="●", font=("Segoe UI", 14),
                                     bg=BG, fg=FG_DIM)
        self._sched_dot.grid(row=0, column=0, padx=(10, 4), pady=6, sticky="w")
        self._sched_state = tk.Label(frm_st, text="Checking…",
                                     font=FONT_GROUP, bg=BG, fg=FG_DIM)
        self._sched_state.grid(row=0, column=1, sticky="w", pady=6)
        self._btn(frm_st, "↺ Refresh", self._sched_refresh
                  ).grid(row=0, column=5, padx=10, pady=4, sticky="e")

        for col, lbl, attr in [
            (0, "Next Run:",    "_sched_next"),
            (1, "Last Run:",    "_sched_last"),
            (2, "Last Result:", "_sched_result"),
        ]:
            tk.Label(frm_st, text=lbl, font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                     ).grid(row=1, column=col * 2,
                            sticky="w", padx=(10 if col == 0 else 4, 0), pady=(0, 6))
            lv = tk.Label(frm_st, text="—", font=FONT_MONO, bg=BG, fg=FG)
            lv.grid(row=1, column=col * 2 + 1, sticky="w", padx=(4, 14), pady=(0, 6))
            setattr(self, attr, lv)
        frm_st.columnconfigure(5, weight=1)

        # ── Configuration ────────────────────────────────────────────────────
        frm_cfg = tk.LabelFrame(p, text="  Configuration  ", font=FONT_UI,
                                bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_cfg.pack(fill="x", **pad)

        for row, lbl, val in [
            (0, "Task name:", _TASK_NAME),
            (1, "Script:",    _script),
            (2, "Python:",    _python),
        ]:
            tk.Label(frm_cfg, text=lbl, font=FONT_UI, bg=BG, fg=FG_DIM
                     ).grid(row=row, column=0, sticky="w", padx=(10, 4), pady=3)
            tk.Label(frm_cfg, text=val, font=FONT_MONO, bg=BG, fg=FG,
                     anchor="w", wraplength=520
                     ).grid(row=row, column=1, sticky="w", padx=(0, 10), pady=3)
        frm_cfg.columnconfigure(1, weight=1)

        time_row = tk.Frame(frm_cfg, bg=BG)
        time_row.grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 8))
        tk.Label(time_row, text="Daily run time:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).pack(side="left", padx=(0, 8))
        self._sched_hour = ttk.Spinbox(time_row, from_=0, to=23, width=4,
                                       format="%02.0f", font=FONT_MONO)
        self._sched_hour.set("06")
        self._sched_hour.pack(side="left")
        tk.Label(time_row, text=":", font=FONT_MONO, bg=BG, fg=FG
                 ).pack(side="left", padx=2)
        self._sched_min = ttk.Spinbox(time_row, from_=0, to=59, width=4,
                                      format="%02.0f", font=FONT_MONO)
        self._sched_min.set("00")
        self._sched_min.pack(side="left")
        tk.Label(time_row, text="(daily, runs while logged in)",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                 ).pack(side="left", padx=(8, 0))

        # ── Action buttons ───────────────────────────────────────────────────
        btn_row = tk.Frame(p, bg=BG)
        btn_row.pack(fill="x", padx=14, pady=(2, 4))
        self._btn(btn_row, "✔ Create / Update", self._sched_create,
                  bg="#1b5e20", fg="#c8e6c9").pack(side="left", padx=(0, 8))
        self._btn(btn_row, "⏵ Run Now", self._sched_run_now,
                  bg="#1a3a5c", fg="#90caf9").pack(side="left", padx=(0, 8))
        self._btn(btn_row, "⟳ Rerun (Cached)", self._sched_rerun,
                  bg="#2d3a1e", fg="#b9f0a0").pack(side="left", padx=(0, 8))
        self._btn(btn_row, "🗑 Remove Task", self._sched_delete,
                  bg="#7b1c1c", fg="#ffcdd2").pack(side="left")

        # ── Status bar ───────────────────────────────────────────────────────
        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x", padx=14, pady=(0, 10))
        self._sched_status = tk.StringVar()
        tk.Label(bot, textvariable=self._sched_status,
                 font=("Segoe UI", 9), bg=BG, fg=GREEN).pack(side="left")

        self._sched_refresh()

    def _sched_refresh(self) -> None:
        import csv as _csv, io as _io
        try:
            r = _sp.run(
                ["schtasks", "/query", "/tn", _TASK_NAME, "/fo", "csv", "/v"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                self._sched_dot.config(fg=FG_DIM)
                self._sched_state.config(text="Not scheduled", fg=FG_DIM)
                for attr in ("_sched_next", "_sched_last", "_sched_result"):
                    getattr(self, attr).config(text="—", fg=FG)
                return
            lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
            if len(lines) >= 2:
                rows = list(_csv.reader(_io.StringIO("\n".join(lines))))
                hdr  = rows[0]
                dat  = rows[1] if len(rows) > 1 else []
                def _col(name: str) -> str:
                    try:
                        return dat[hdr.index(name)] if name in hdr else "—"
                    except Exception:
                        return "—"
                status   = _col("Status")
                next_run = _col("Next Run Time")
                last_run = _col("Last Run Time")
                last_res = _col("Last Result")

                colour = GREEN if status in ("Ready", "Running") else \
                         AMBER  if status == "Disabled"           else FG_DIM
                self._sched_dot.config(fg=colour)
                self._sched_state.config(text=status, fg=colour)
                self._sched_next.config(
                    text=next_run if next_run not in ("N/A", "") else "—")
                self._sched_last.config(
                    text=last_run if last_run not in ("N/A", "") else "—")
                res_fg = GREEN if last_res in ("0", "0x0") else \
                         RED   if last_res not in ("—", "", "267011") else FG
                self._sched_result.config(text=last_res, fg=res_fg)
            else:
                self._sched_state.config(text="Unknown", fg=AMBER)
        except Exception as e:
            self._sched_state.config(text="Error", fg=RED)
            self._sched_status.set(f"Error querying task: {e}")

    def _sched_create(self) -> None:
        hh = self._sched_hour.get().zfill(2)
        mm = self._sched_min.get().zfill(2)
        if (not hh.isdigit() or not mm.isdigit()
                or not (0 <= int(hh) <= 23)
                or not (0 <= int(mm) <= 59)):
            messagebox.showerror("Invalid time", f"Invalid time value: {hh}:{mm}")
            return
        tr = f'"{_sys.executable}" "{_HERE / "automation" / "run_automation.py"}"'
        cmd = ["schtasks", "/create",
               "/tn", _TASK_NAME,
               "/tr", tr,
               "/sc", "daily",
               "/st", f"{hh}:{mm}",
               "/f"]
        try:
            r = _sp.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                self._sched_status.set(f"Task created — runs daily at {hh}:{mm}.")
            else:
                messagebox.showerror(
                    "schtasks failed",
                    r.stderr.strip() or r.stdout.strip() or "Unknown error")
            self._sched_refresh()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _sched_rerun(self) -> None:
        """Rerun dialog: optional --local-csv, then run pipeline on cached data."""
        import sys as _sys, subprocess as _sp

        # Auto-detect latest cached snapshot in data/ (not data/programs/)
        data_dir = self.base_dir / "data"
        try:
            candidates = sorted(
                list(data_dir.glob("*.csv.gz")) + list(data_dir.glob("*.7z")),
                key=lambda f: f.stat().st_mtime, reverse=True,
            ) if data_dir.exists() else []
        except Exception:
            candidates = []
        default_csv = str(candidates[0]) if candidates else ""

        dlg = tk.Toplevel(self)
        dlg.title("Rerun (Cached)")
        dlg.configure(bg=BG)
        dlg.resizable(True, True)
        dlg.geometry("820x580")
        dlg.transient(self)

        top_bar = tk.Frame(dlg, bg=BG)
        top_bar.pack(fill="x", padx=12, pady=(10, 6))
        status_var = tk.StringVar(value="Ready.")
        tk.Label(top_bar, textvariable=status_var, font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM).pack(side="right", padx=(8, 0))

        opts = tk.Frame(dlg, bg=BG)
        opts.pack(fill="x", padx=12, pady=(0, 4))

        tk.Label(opts, text="--local-csv:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        csv_var = tk.StringVar(value=default_csv)
        tk.Entry(opts, textvariable=csv_var, font=FONT_MONO,
                 bg=BG2, fg=FG, insertbackground=FG, relief="flat", width=30
                 ).grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=4)

        def _browse():
            from tkinter import filedialog
            f = filedialog.askopenfilename(
                title="Select cached AQUA CSV / gz / 7z",
                filetypes=[("Data files", "*.csv *.csv.gz *.7z"), ("All", "*.*")],
                initialdir=str(data_dir),
            )
            if f:
                csv_var.set(f)
        self._btn(opts, "Browse", _browse, bg=BG3
                  ).grid(row=0, column=2, sticky="w", pady=4)
        tk.Label(opts, text="(blank = use cached data/*.csv.gz)",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                 ).grid(row=0, column=3, sticky="w", padx=(6, 0), pady=4)
        opts.columnconfigure(1, weight=1)

        log_frm = tk.Frame(dlg, bg=BG)
        log_frm.pack(fill="both", expand=True, padx=12, pady=(4, 8))
        log_txt = tk.Text(log_frm, font=FONT_MONO, bg="#0d1b2a", fg="#c8e6c9",
                          insertbackground=FG, relief="flat", wrap="none", state="disabled")
        log_vsb = ttk.Scrollbar(log_frm, orient="vertical",   command=log_txt.yview)
        log_hsb = ttk.Scrollbar(log_frm, orient="horizontal", command=log_txt.xview)
        log_txt.configure(yscrollcommand=log_vsb.set, xscrollcommand=log_hsb.set)
        log_txt.tag_config("err",  foreground="#ef9a9a")
        log_txt.tag_config("warn", foreground=AMBER)
        log_txt.tag_config("ok",   foreground=GREEN)
        log_hsb.pack(side="bottom", fill="x")
        log_vsb.pack(side="right",  fill="y")
        log_txt.pack(side="left",   fill="both", expand=True)

        _proc: list[_sp.Popen | None] = [None]
        _running = [False]
        start_btn_ref: list = [None]

        def _append(line: str) -> None:
            lo = line.lower()
            tag = ""
            if any(w in lo for w in ("error", "traceback", "failed", "exception")):
                tag = "err"
            elif "warning" in lo:
                tag = "warn"
            elif any(w in lo for w in (" ok ", "→ ok", "sent", "email sent")):
                tag = "ok"
            log_txt.config(state="normal")
            log_txt.insert("end", line + "\n", tag)
            log_txt.see("end")
            log_txt.config(state="disabled")

        def _do_run():
            csv_val = csv_var.get().strip()
            if not csv_val:
                dlg.after(0, messagebox.showerror, "No file",
                          "Provide a --local-csv path or browse for a cached file.")
                _running[0] = False
                dlg.after(0, lambda: start_btn_ref[0].config(
                    text="▶ Start", bg="#00c853", fg="#002200", command=_start))
                return
            cmd = [_sys.executable, str(_HERE / "automation" / "run_automation.py"),
                   "--local-csv", csv_val]
            _append("$ " + " ".join(cmd))
            _append("-" * 60)
            try:
                proc = _sp.Popen(
                    cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                )
                _proc[0] = proc
                for line in proc.stdout:
                    try:
                        dlg.after(0, _append, line.rstrip())
                    except Exception:
                        break   # dialog closed while subprocess still running
                proc.wait()
                rc = proc.returncode
                try:
                    dlg.after(0, _append, "-" * 60)
                    dlg.after(0, _append, f"Exit code: {rc}")
                    dlg.after(0, status_var.set,
                              f"Done \u2014 exit {rc}" if rc == 0 else f"FAILED (exit {rc})")
                except Exception:
                    pass
            except Exception as exc:
                try:
                    dlg.after(0, _append, f"ERROR: {exc}")
                    dlg.after(0, status_var.set, "Error launching process.")
                except Exception:
                    pass
            finally:
                _running[0] = False
                try:
                    dlg.after(0, lambda: start_btn_ref[0].config(
                        text="\u25b6 Start", bg="#00c853", fg="#002200", command=_start))
                except Exception:
                    pass

        def _start():
            if _running[0]:
                return
            _running[0] = True
            log_txt.config(state="normal")
            log_txt.delete("1.0", "end")
            log_txt.config(state="disabled")
            status_var.set("Running…")
            start_btn_ref[0].config(text="Running…", bg=AMBER, fg=BG, command=lambda: None)
            threading.Thread(target=_do_run, daemon=True).start()

        start_btn = self._btn(top_bar, "▶ Start", _start, bg="#00c853", fg="#002200")
        start_btn.pack(side="left", padx=(0, 8))
        start_btn_ref[0] = start_btn
        self._btn(top_bar, "✕ Close", dlg.destroy, fg=FG_DIM).pack(side="left")

    def _sched_run_now(self) -> None:
        if not messagebox.askyesno("Run Now",
                                   f'Start "{_TASK_NAME}" immediately?\n\n'
                                   'This kicks off a full AQUA pull + pipeline run.'):
            return
        try:
            r = _sp.run(["schtasks", "/run", "/tn", _TASK_NAME],
                        capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                self._sched_status.set("Task started (running in background).")
            else:
                msg = r.stderr.strip() or r.stdout.strip()
                messagebox.showerror(
                    "schtasks /run failed",
                    msg or "Task not found — create it first.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _sched_delete(self) -> None:
        if not messagebox.askyesno("Remove Task",
                                   f'Remove scheduled task "{_TASK_NAME}"?'):
            return
        try:
            r = _sp.run(
                ["schtasks", "/delete", "/tn", _TASK_NAME, "/f"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                self._sched_status.set(f"Task '{_TASK_NAME}' removed.")
            else:
                messagebox.showerror("Error", r.stderr.strip())
        except Exception as e:
            messagebox.showerror("Error", str(e))
        self._sched_refresh()


# ─────────────────────────────────────────────────────────────────────────────
# Standalone launcher
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="CLASS Automation Manager GUI"
    )
    ap.add_argument("--base-dir", metavar="PATH", default=None,
                    help="Override base dir (default: from config)")
    args = ap.parse_args()
    cfg = _load_config(_CFG_DIR / _CFG_NAME)
    base_dir = Path(args.base_dir) if args.base_dir else Path(cfg.get("base_dir", str(_BASE_DIR)))
    root = tk.Tk()
    root.title("CLASS Automation Manager")
    root.geometry("980x700")
    root.configure(bg=BG)
    AutomationManager(root, base_dir).pack(fill="both", expand=True)
    root.mainloop()


if __name__ == "__main__":
    main()
