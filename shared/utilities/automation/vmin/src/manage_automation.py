"""
manage_automation.py  —  GUI to manage VMIN-dashboard automation.

Tabs:
  1. Email & Filter   — recipients + excluded op codes (email_config.json)
  2. Run History      — NVL_VMIN_YYYYMMDD_HHMMSS/ run folders; view/delete old runs
  3. Data Files       — data/ raw AQUA pull snapshots + programs/ per-TP gzips
  4. Schedule         — Windows Task Scheduler: create, check, run now, remove

Usage:
    python manage_automation.py
    python manage_automation.py --base-dir "\\\\server\\share\\auto\\vmin"
"""
from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

# ── defaults (same as run_automation.py) ──────────────────────────────────────
_HERE      = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent.parent.parent.parent   # app.yield.nvl/
_BASE_DIR  = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\vmin")
_CFG_NAME  = "email_config.json"
_CFG_DIR   = _REPO_ROOT / "shared" / "setup" / "automation" / "vmin-dashboard"
_EMAIL_TO  = "sujit.n.pant@intel.com"
_TASK_NAME = "NVL-BLLC VMIN Automation"

# ── colours ───────────────────────────────────────────────────────────────────
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
            # Migrate old key name
            if "email_to" in d and "email_to_report" not in d:
                d["email_to_report"] = d.pop("email_to")
            return d
        except Exception:
            pass
    return {"email_to_report": _EMAIL_TO, "email_to_alert": _EMAIL_TO,
            "excluded_ops": [], "excluded_keys": []}


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
        return datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


def _discover_keys(base_dir: Path) -> list[str]:
    """Return sorted list of TP keys from all NVL_VMIN_0H61* output run subfolders."""
    keys: set[str] = set()
    output_dir = base_dir / "output"
    if output_dir.exists():
        for run_dir in output_dir.iterdir():
            if run_dir.is_dir() and re.match(r'NVL_VMIN_0H61', run_dir.name):
                for sub in run_dir.iterdir():
                    if sub.is_dir():
                        keys.add(sub.name)
    return sorted(keys)


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class AutomationManager(tk.Frame):
    def __init__(self, master, base_dir: Path) -> None:
        super().__init__(master, bg=BG)
        self.base_dir = base_dir
        self.cfg_path = _CFG_DIR / _CFG_NAME
        self.cfg      = _load_config(self.cfg_path)
        self.excluded_ops = set(str(o) for o in self.cfg.get("excluded_ops", []))

        self._apply_styles()
        self._build_ui()

    # ── shared button helper ──────────────────────────────────────────────────

    def _btn(self, parent, text, cmd, bg=BG3, fg=FG, **kw):
        return tk.Button(parent, text=text, command=cmd,
                         font=FONT_UI, bg=bg, fg=fg,
                         activebackground=ACCENT, activeforeground=BG,
                         relief="flat", padx=10, pady=3, cursor="hand2", **kw)

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

    # ── top-level layout ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        hdr = tk.Frame(self, bg=BG3)
        hdr.pack(fill="x")
        tk.Label(hdr, text="VMIN Automation Manager", font=FONT_TITLE,
                 bg=BG3, fg=ACCENT).pack(side="left", padx=14, pady=8)
        tk.Label(hdr, text=str(self.base_dir), font=("Segoe UI", 8),
                 bg=BG3, fg=FG_DIM).pack(side="left", padx=4)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self._tab_email    = tk.Frame(nb, bg=BG)
        self._tab_history  = tk.Frame(nb, bg=BG)
        self._tab_data     = tk.Frame(nb, bg=BG)
        self._tab_schedule = tk.Frame(nb, bg=BG)

        nb.add(self._tab_email,    text="  Email & Filter  ")
        nb.add(self._tab_history,  text="  Run History  ")
        nb.add(self._tab_data,     text="  Data Files  ")
        nb.add(self._tab_schedule, text="  Schedule  ")

        self._build_email_tab()
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

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 1 — Email & Filter
    # ═════════════════════════════════════════════════════════════════════════

    def _build_email_tab(self) -> None:
        p   = self._tab_email
        pad = dict(padx=14, pady=6)

        top = tk.Frame(p, bg=BG)
        top.pack(fill="x", padx=14, pady=(10, 2))
        tk.Label(top, text=f"Config: {self.cfg_path}", font=("Segoe UI", 8),
                 bg=BG, fg=FG_DIM).pack(side="left")
        self._btn(top, "Cancel", self.winfo_toplevel().destroy, fg=FG_DIM
                  ).pack(side="right", padx=(6, 0))
        self._btn(top, "Save", self._save_email,
                  bg="#1b5e20", fg="#00ff7f").pack(side="right")

        # Recipients
        frm = tk.LabelFrame(p, text="  Recipients  ", font=FONT_UI,
                             bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm.pack(fill="x", **pad)

        self.report_email_var = tk.StringVar(value=self.cfg.get("email_to_report", _EMAIL_TO))
        self.alert_email_var  = tk.StringVar(
            value=self.cfg.get("email_to_alert",
                               self.cfg.get("email_to_report", _EMAIL_TO)))

        for row, label, var, color, note in [
            (0, "Report To:", self.report_email_var, GREEN, "Final report (semicolons OK)"),
            (1, "Alerts To:", self.alert_email_var,  AMBER, "AQUA errors / pipeline failures"),
        ]:
            tk.Label(frm, text=label, font=FONT_UI, bg=BG, fg=color
                     ).grid(row=row, column=0, sticky="w", padx=8, pady=4)
            tk.Entry(frm, textvariable=var, font=FONT_UI, bg=BG2, fg=FG,
                     insertbackground=FG, relief="flat", width=48
                     ).grid(row=row, column=1, padx=8, pady=4, sticky="ew")
            tk.Label(frm, text=note, font=("Segoe UI", 7), bg=BG, fg=FG_DIM
                     ).grid(row=row, column=2, sticky="w", padx=(0, 8))
        frm.columnconfigure(1, weight=1)

        # Excluded Op Codes
        frm_ops = tk.LabelFrame(p, text="  Excluded Op Codes  ", font=FONT_UI,
                                bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_ops.pack(fill="x", **pad)

        tk.Label(frm_ops, bg=BG, fg=FG_DIM, font=("Segoe UI", 8),
                 text="5-6 digit op codes skipped in both pipeline run and email report."
                 ).pack(anchor="w", padx=8, pady=(4, 0))

        self._ops_tags_frame = tk.Frame(frm_ops, bg=BG)
        self._ops_tags_frame.pack(fill="x", padx=8, pady=(4, 2))

        ops_add_row = tk.Frame(frm_ops, bg=BG)
        ops_add_row.pack(anchor="w", padx=8, pady=(0, 6))
        tk.Label(ops_add_row, text="Add:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).pack(side="left", padx=(0, 4))
        self._ops_entry_var = tk.StringVar()
        ops_entry = tk.Entry(ops_add_row, textvariable=self._ops_entry_var,
                             font=FONT_MONO, bg=BG2, fg=FG, insertbackground=FG,
                             relief="flat", width=10)
        ops_entry.pack(side="left", padx=(0, 4))
        ops_entry.bind("<Return>", lambda _e: self._add_op())
        self._btn(ops_add_row, "+ Add", self._add_op).pack(side="left")

        self._refresh_ops_tags()

        # Program Filter
        frm_pf = tk.LabelFrame(p, text="  Program Filter  ", font=FONT_UI,
                               bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_pf.pack(fill="both", expand=True, **pad)

        tk.Label(frm_pf, bg=BG, fg=FG_DIM, font=("Segoe UI", 8),
                 text="Uncheck to exclude a TP key from runs and reports. "
                      "Based on data/programs/ cache."
                 ).pack(anchor="w", padx=8, pady=(4, 0))

        pf_scroll_frame = tk.Frame(frm_pf, bg=BG)
        pf_scroll_frame.pack(fill="both", expand=True, padx=8, pady=4)
        pf_canvas = tk.Canvas(pf_scroll_frame, bg=BG, highlightthickness=0)
        pf_vsb = ttk.Scrollbar(pf_scroll_frame, orient="vertical",
                               command=pf_canvas.yview)
        self._pf_inner = tk.Frame(pf_canvas, bg=BG)
        self._pf_inner.bind("<Configure>",
            lambda e: pf_canvas.configure(scrollregion=pf_canvas.bbox("all")))
        pf_canvas.create_window((0, 0), window=self._pf_inner, anchor="nw")
        pf_canvas.configure(yscrollcommand=pf_vsb.set)
        pf_vsb.pack(side="right", fill="y")
        pf_canvas.pack(side="left", fill="both", expand=True)

        pf_btn_row = tk.Frame(frm_pf, bg=BG)
        pf_btn_row.pack(anchor="w", padx=8, pady=(0, 6))
        self._btn(pf_btn_row, "All On",  self._pf_all_on,  bg=BG3
                  ).pack(side="left", padx=(0, 4))
        self._btn(pf_btn_row, "All Off", self._pf_all_off, bg=BG3
                  ).pack(side="left", padx=(0, 4))
        self._btn(pf_btn_row, "Reload Keys", self._rebuild_pf, bg=BG3
                  ).pack(side="left")

        self._pf_vars: dict[str, tk.BooleanVar] = {}
        self._rebuild_pf()

        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x", padx=14, pady=(0, 12))
        self.email_status = tk.StringVar()
        tk.Label(bot, textvariable=self.email_status, font=("Segoe UI", 9),
                 bg=BG, fg=GREEN).pack(side="left")

    def _save_email(self) -> None:
        report_to = self.report_email_var.get().strip()
        alert_to  = self.alert_email_var.get().strip() or report_to
        if not report_to:
            messagebox.showerror("Error", "Report recipient cannot be empty.")
            return
        excluded_keys = [k for k, v in self._pf_vars.items() if not v.get()]
        cfg = {
            "email_to_report": report_to,
            "email_to_alert":  alert_to,
            "excluded_ops":    sorted(self.excluded_ops),
            "excluded_keys":   sorted(excluded_keys),
        }
        try:
            _save_config(self.cfg_path, cfg)
            self.cfg = cfg
            self.email_status.set("Saved.")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def _refresh_ops_tags(self) -> None:
        for w in self._ops_tags_frame.winfo_children():
            w.destroy()
        if not self.excluded_ops:
            tk.Label(self._ops_tags_frame, text="(none)", font=("Segoe UI", 8),
                     bg=BG, fg=FG_DIM).pack(side="left")
            return
        for op in sorted(self.excluded_ops):
            tag = tk.Frame(self._ops_tags_frame, bg=BG3, bd=0)
            tag.pack(side="left", padx=(0, 4), pady=2)
            tk.Label(tag, text=op, font=FONT_MONO, bg=BG3, fg=AMBER
                     ).pack(side="left", padx=(6, 2), pady=2)
            tk.Button(tag, text="✕", font=("Segoe UI", 8), bg=BG3, fg=RED,
                      activebackground=RED, activeforeground=BG,
                      relief="flat", cursor="hand2", bd=0,
                      command=lambda o=op: self._remove_op(o)
                      ).pack(side="left", padx=(0, 4), pady=2)

    def _add_op(self) -> None:
        raw = self._ops_entry_var.get().strip()
        if not raw:
            return
        if not re.fullmatch(r'\d{5,6}', raw):
            messagebox.showerror("Invalid op code",
                                 f"'{raw}' is not a valid 5-6 digit op code.")
            return
        self.excluded_ops.add(raw)
        self._ops_entry_var.set("")
        self._refresh_ops_tags()

    def _remove_op(self, op: str) -> None:
        self.excluded_ops.discard(op)
        self._refresh_ops_tags()

    def _rebuild_pf(self) -> None:
        """Rebuild Program Filter checkboxes from data/programs/ keys."""
        for w in self._pf_inner.winfo_children():
            w.destroy()
        keys = _discover_keys(self.base_dir)
        excluded = set(str(k) for k in self.cfg.get("excluded_keys", []))
        self._pf_vars = {}
        if not keys:
            tk.Label(self._pf_inner, text="(no keys found in data/programs/)",
                     font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                     ).grid(row=0, column=0, sticky="w", padx=4)
            return
        for i, key in enumerate(keys):
            var = tk.BooleanVar(value=(key not in excluded))
            self._pf_vars[key] = var
            cb = tk.Checkbutton(self._pf_inner, text=key, variable=var,
                                font=FONT_MONO, bg=BG, fg=FG,
                                activebackground=BG, activeforeground=ACCENT,
                                selectcolor=BG2, relief="flat", anchor="w")
            cb.grid(row=i // 2, column=i % 2, sticky="w", padx=(4, 20), pady=1)

    def _pf_all_on(self) -> None:
        for v in self._pf_vars.values():
            v.set(True)

    def _pf_all_off(self) -> None:
        for v in self._pf_vars.values():
            v.set(False)

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 2 — Run History
    # ═════════════════════════════════════════════════════════════════════════

    def _build_history_tab(self) -> None:
        p = self._tab_history

        tb = tk.Frame(p, bg=BG)
        tb.pack(fill="x", padx=12, pady=(10, 4))
        self._btn(tb, "↺ Refresh",         self._refresh_history).pack(side="left", padx=(0, 6))
        self._btn(tb, "✔ Select All",      self._hist_select_all).pack(side="left", padx=(0, 6))
        self._btn(tb, "✘ Clear",           self._hist_clear_sel).pack(side="left", padx=(0, 6))
        self._btn(tb, "🏷 Tag",            self._hist_tag,
                  bg="#2d3b2d", fg="#a5d6a7").pack(side="left", padx=(0, 6))
        self._btn(tb, "🌐 Open HTML",      self._hist_open_html,
                  bg="#1a3550", fg="#80d8ff").pack(side="left", padx=(0, 6))
        self._btn(tb, "⚠ Delete + Data", lambda: self._hist_delete(include_data=True),
                  bg="#6b3a00", fg="#ffd180").pack(side="right", padx=(16, 0))
        self._btn(tb, "🗑 Delete Run",     lambda: self._hist_delete(include_data=False),
                  bg="#5d1a1a", fg="#ffcdd2").pack(side="right", padx=(0, 6))
        self._btn(tb, "💾 Save Report", self._hist_save_report,
                  bg="#1a3a3c", fg="#80deea").pack(side="right", padx=(0, 6))
        self._btn(tb, "✉ Send Report",    self._hist_send_email,
                  bg="#1a3a5c", fg="#90caf9").pack(side="right", padx=(0, 6))

        cols = ("tag", "folder", "date", "tps", "size")
        self.hist_tree = ttk.Treeview(p, columns=cols, show="headings",
                                      selectmode="extended")
        self.hist_tree.heading("tag",    text="Tag",         anchor="w")
        self.hist_tree.heading("folder", text="Run Folder",  anchor="w")
        self.hist_tree.heading("date",   text="Date / Time", anchor="w")
        self.hist_tree.heading("tps",    text="TPs",         anchor="center")
        self.hist_tree.heading("size",   text="Size",        anchor="e")
        self.hist_tree.column("tag",    width=70,  stretch=False)
        self.hist_tree.column("folder", width=320, stretch=True)
        self.hist_tree.column("date",   width=150, stretch=False)
        self.hist_tree.column("tps",    width=60,  stretch=False, anchor="center")
        self.hist_tree.column("size",   width=90,  stretch=False, anchor="e")

        vsb = ttk.Scrollbar(p, orient="vertical",   command=self.hist_tree.yview)
        hsb = ttk.Scrollbar(p, orient="horizontal", command=self.hist_tree.xview)
        self.hist_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        hsb.pack(side="bottom", fill="x",    padx=12, pady=(0, 0))
        vsb.pack(side="right",  fill="y")
        self.hist_tree.pack(fill="both", expand=True, padx=(12, 0), pady=(0, 0))
        self.hist_tree.bind("<Double-1>", self._hist_tag_dblclick)

        # Right-click context menu
        self._hist_ctx = tk.Menu(self, tearoff=0, bg=BG2, fg=FG,
                                 activebackground=BG3, activeforeground=ACCENT,
                                 font=FONT_UI, bd=0)
        self._hist_ctx.add_command(label="🌐  Open vmin_dashboard.html in Browser",
                                   command=self._hist_open_html)
        self._hist_ctx.add_command(label="📋  Copy file:// link to Clipboard",
                                   command=lambda: self._hist_open_html(copy_only=True))
        self._hist_ctx.add_separator()
        self._hist_ctx.add_command(label="✉  Send Report", command=self._hist_send_email)
        self._hist_ctx.add_command(label="💾  Save Report", command=self._hist_save_report)
        self._hist_ctx.add_command(label="🏷  Tag Run",    command=self._hist_tag)
        self.hist_tree.bind("<Button-3>", self._hist_show_ctx)

        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x", padx=12, pady=(4, 8))
        self.hist_status = tk.StringVar()
        tk.Label(bot, textvariable=self.hist_status, font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM).pack(side="left")

        self._refresh_history()

    def _refresh_history(self) -> None:
        self.hist_tree.delete(*self.hist_tree.get_children())
        output_dir = self.base_dir / "output"
        if not output_dir.exists():
            self.hist_status.set(f"No output/ folder found under {self.base_dir}")
            return

        folders = sorted(
            [d for d in output_dir.iterdir()
             if d.is_dir() and d.name.startswith("NVL_VMIN_")],
            key=lambda d: d.name, reverse=True,
        )

        for d in folders:
            m = re.search(r'(\d{8})[_T](\d{6})', d.name)
            if m:
                date_str = (f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"
                            f" {m.group(2)[:2]}:{m.group(2)[2:4]}:{m.group(2)[4:]}")
            else:
                m2 = re.search(r'(\d{8})', d.name)
                date_str = (f"{m2.group(1)[:4]}-{m2.group(1)[4:6]}-{m2.group(1)[6:]}" if m2 else "?")
            try:
                subfolders = [x.name for x in d.iterdir() if x.is_dir()]
            except PermissionError:
                subfolders = []
            tp_count   = len(subfolders)
            if tp_count == 1:
                folder_disp = f"{d.name}/{subfolders[0]}"
            elif tp_count > 1:
                _names = ', '.join(subfolders[:2])
                folder_disp = f"{d.name}/{_names}{'\u2026' if tp_count > 2 else ''}"
            else:
                folder_disp = d.name
            sz       = _dir_size(d)
            tag_file = d / ".tag"
            tag      = tag_file.read_text(encoding="utf-8").strip() if tag_file.exists() else ""
            self.hist_tree.insert("", "end", iid=str(d),
                                  values=(tag, folder_disp, date_str, tp_count, _fmt_size(sz)))

        self.hist_status.set(f"{len(folders)} run folder(s)")

    def _hist_select_all(self) -> None:
        self.hist_tree.selection_set(self.hist_tree.get_children())

    def _hist_clear_sel(self) -> None:
        self.hist_tree.selection_remove(self.hist_tree.get_children())

    def _hist_show_ctx(self, event) -> None:
        iid = self.hist_tree.identify_row(event.y)
        if iid:
            if iid not in self.hist_tree.selection():
                self.hist_tree.selection_set(iid)
            self._hist_ctx.post(event.x_root, event.y_root)

    @staticmethod
    def _find_vmin_htmls(run_dir: Path) -> list[Path]:
        """Find vmin_dashboard.html files in TP subfolders of run_dir.
        Falls back to report.html at run_dir root if none found."""
        hits = sorted(run_dir.glob("*/vmin_dashboard.html"))
        if hits:
            return hits
        fallback = run_dir / "report.html"
        return [fallback] if fallback.exists() else []

    def _hist_open_html(self, copy_only: bool = False) -> None:
        import webbrowser
        sel = self.hist_tree.selection()
        if not sel:
            messagebox.showinfo("No run selected", "Select a run folder first.")
            return
        if len(sel) > 1 and not copy_only:
            opened = 0
            for iid in sel:
                for html in self._find_vmin_htmls(Path(iid)):
                    webbrowser.open(html.as_uri())
                    opened += 1
            self.hist_status.set(
                f"Opened {opened} dashboard(s) in browser." if opened
                else "No vmin_dashboard.html found in selected runs."
            )
            return
        run_dir = Path(sel[0])
        htmls   = self._find_vmin_htmls(run_dir)
        if not htmls:
            messagebox.showerror("No dashboard",
                                 f"vmin_dashboard.html not found in:\n{run_dir}")
            return
        if copy_only:
            urls = "\n".join(h.as_uri() for h in htmls)
            self.clipboard_clear()
            self.clipboard_append(urls)
            self.hist_status.set(f"Copied {len(htmls)} link(s) to clipboard")
        else:
            for html in htmls:
                webbrowser.open(html.as_uri())
            self.hist_status.set(
                f"Opened {len(htmls)} dashboard(s) in browser  ({run_dir.name})"
            )

    def _hist_tag_dblclick(self, event) -> None:
        if self.hist_tree.identify_region(event.x, event.y) == "cell":
            self._hist_tag()

    def _hist_tag(self) -> None:
        sel = self.hist_tree.selection()
        if not sel:
            messagebox.showinfo("No run selected", "Select one or more runs to tag.")
            return
        current = self.hist_tree.set(sel[0], "tag") if len(sel) == 1 else ""

        dlg = tk.Toplevel(self)
        dlg.title("Tag Run(s)")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.transient(self)

        names = ", ".join(Path(s).name for s in sel[:3])
        if len(sel) > 3:
            names += f" … (+{len(sel) - 3} more)"
        tk.Label(dlg, text=f"Tag for {len(sel)} run(s):", font=FONT_GROUP,
                 bg=BG, fg=ACCENT).pack(padx=16, pady=(14, 2))
        tk.Label(dlg, text=names, font=("Segoe UI", 8),
                 bg=BG, fg=FG_DIM, wraplength=320).pack(padx=16, pady=(0, 6))

        var   = tk.StringVar(value=current)
        entry = tk.Entry(dlg, textvariable=var, font=("Courier New", 11),
                         bg=BG2, fg=FG, insertbackground=FG, relief="flat", width=20)
        entry.pack(padx=16, pady=4)
        entry.select_range(0, "end")
        entry.focus_set()

        tk.Label(dlg, text="Leave blank to clear tag.", font=("Segoe UI", 8),
                 bg=BG, fg=FG_DIM).pack(padx=16, pady=(0, 6))

        def _apply() -> None:
            tag = var.get().strip()
            errors: list[str] = []
            for iid in sel:
                tag_file = Path(iid) / ".tag"
                try:
                    if tag:
                        tag_file.write_text(tag, encoding="utf-8")
                    else:
                        tag_file.unlink(missing_ok=True)
                    self.hist_tree.set(iid, "tag", tag)
                except Exception as e:
                    errors.append(f"{Path(iid).name}: {e}")
            dlg.destroy()
            if errors:
                messagebox.showerror("Tag errors", "\n".join(errors))
            self.hist_status.set(
                f"Tagged {len(sel)} run(s) as '{tag}'." if tag
                else f"Cleared tag on {len(sel)} run(s)."
            )

        entry.bind("<Return>", lambda _e: _apply())
        entry.bind("<Escape>", lambda _e: dlg.destroy())
        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(padx=16, pady=(2, 14))
        self._btn(btn_row, "Apply",  _apply,       bg="#1b5e20", fg="#00ff7f").pack(side="left", padx=(0, 6))
        self._btn(btn_row, "Cancel", dlg.destroy,  fg=FG_DIM).pack(side="left")

    def _hist_send_email(self) -> None:
        """Send report for the most-recent NVL_VMIN_* run — no selection required."""
        out_dir = self.base_dir / "output"
        if not out_dir.exists():
            messagebox.showinfo("No output folder",
                                f"No output/ directory found under:\n{self.base_dir}")
            return

        # Find latest NVL_VMIN_* run dir
        run_dirs = sorted(
            [d for d in out_dir.iterdir()
             if d.is_dir() and d.name.startswith("NVL_VMIN_")],
            key=lambda d: d.name, reverse=True,
        )
        if not run_dirs:
            messagebox.showinfo("No runs", "No NVL_VMIN_* run folders found in output/.")
            return

        run_dir     = run_dirs[0]
        report_html = run_dir / "report.html"
        if not report_html.exists():
            messagebox.showerror("No report", f"report.html not found in:\n{run_dir}")
            return

        to = self.cfg.get("email_to_report", _EMAIL_TO)
        if not messagebox.askyesno(
            "Send Report",
            f"Send VMIN report for latest run:\n  {run_dir.name}\n\nTo: {to}",
        ):
            return

        self.hist_status.set("Sending…")
        self.update_idletasks()

        def _send():
            try:
                import sys as _sys
                _sys.path.insert(0, str(_HERE))
                _sys.modules.pop('run_automation', None)  # force fresh load per automation
                from run_automation import send_email  # noqa

                body_html = report_html.read_text(encoding="utf-8", errors="replace")
                send_email(
                    to=to,
                    subject=f"NVL816-BLLC VMIN Report — {run_dir.name}",
                    body_html=body_html,
                    dry_run=False,
                )
                self.after(0, lambda: self.hist_status.set(
                    f"Sent to {to}  ({run_dir.name})"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Send failed", str(e)))
                self.after(0, lambda: self.hist_status.set("Send failed."))

        threading.Thread(target=_send, daemon=True).start()

    def _hist_save_report(self) -> None:
        """Copy latest VMIN report.html to reports/ folder — no email."""
        out_dir = self.base_dir / "output"
        if not out_dir.exists():
            messagebox.showinfo("No output folder",
                                f"No output/ directory found under:\n{self.base_dir}")
            return
        run_dirs = sorted(
            [d for d in out_dir.iterdir()
             if d.is_dir() and d.name.startswith("NVL_VMIN_")],
            key=lambda d: d.name, reverse=True,
        )
        if not run_dirs:
            messagebox.showinfo("No runs", "No NVL_VMIN_* run folders found in output/.")
            return
        run_dir = run_dirs[0]
        report_html = run_dir / "report.html"
        if not report_html.exists():
            messagebox.showerror("No report", f"report.html not found in:\n{run_dir}")
            return
        reports_dir = self.base_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime as _dt
        ts_file  = _dt.now().strftime("%Y%m%d_%H%M%S")
        out_path = reports_dir / f"VMIN_Report_{ts_file}.html"
        shutil.copy2(report_html, out_path)
        self.hist_status.set(f"Saved \u2192 {out_path.name}")
        import webbrowser
        webbrowser.open(out_path.as_uri())

    def _hist_delete(self, include_data: bool = False) -> None:
        """Delete selected run folder(s).  When include_data=True also removes
        data/programs/{letter}/ files.  Per-TP accumulated data is only deleted
        when ALL output runs for that letter are being deleted."""
        sel = self.hist_tree.selection()
        if not sel:
            messagebox.showinfo("Nothing selected", "Select one or more run folders to delete.")
            return

        output_dir = self.base_dir / "output"
        programs_dir = self.base_dir / "data" / "programs"
        sel_set = set(sel)

        to_delete: list[tuple[Path, list[Path]]] = []
        for iid in sel:
            run_dir = Path(iid)
            data_files: list[Path] = []
            if include_data:
                km = re.search(r'(0H61[A-Za-z])', run_dir.name, re.IGNORECASE)
                if km:
                    letter = km.group(1).upper()
                    prog_dir = programs_dir / letter

                    # Find all output runs for this letter that are NOT selected
                    remaining = [
                        d for d in output_dir.iterdir()
                        if d.is_dir()
                        and re.search(rf'0H61{letter}', d.name, re.IGNORECASE)
                        and str(d) not in sel_set
                    ] if output_dir.exists() else []

                    if prog_dir.is_dir() and not remaining:
                        # Last run for this letter — delete everything
                        data_files = sorted(
                            f for f in prog_dir.iterdir()
                            if f.is_file()
                        )
            to_delete.append((run_dir, data_files))

        lines: list[str] = []
        for run_dir, data_files in to_delete:
            lines.append(f"  Run folder : {run_dir.name}")
            if include_data:
                if data_files:
                    for df in data_files:
                        lines.append(f"  Data file  : {df.name}")
                else:
                    lines.append("  Data file  : (none — other runs still exist)")
            lines.append("")

        action = "output folder(s) + ALL data file(s)" if include_data else "output folder(s) only"
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Permanently delete {len(sel)} run(s) — {action}?\n\n"
            + "\n".join(lines).rstrip(),
        ):
            return

        errors: list[str] = []
        deleted = 0
        for run_dir, data_files in to_delete:
            try:
                shutil.rmtree(run_dir)
                self.hist_tree.delete(str(run_dir))
                deleted += 1
            except Exception as e:
                errors.append(f"{run_dir.name}: {e}")
            if include_data:
                for df in data_files:
                    if df.exists():
                        try:
                            df.unlink()
                        except Exception as e:
                            errors.append(f"{df.name}: {e}")

        if errors:
            messagebox.showerror("Delete errors", "\n".join(errors))
        suffix = " + data file(s)" if include_data else ""
        self.hist_status.set(
            f"Deleted {deleted} run(s){suffix}." +
            (f"  {len(errors)} error(s)." if errors else "")
        )
        try:
            self._refresh_data()
        except Exception:
            pass

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 3 — Data Files
    # ═════════════════════════════════════════════════════════════════════════

    def _build_data_tab(self) -> None:
        p = self._tab_data

        tb = tk.Frame(p, bg=BG)
        tb.pack(fill="x", padx=12, pady=(10, 4))
        self._btn(tb, "↺ Refresh",         self._refresh_data).pack(side="left", padx=(0, 6))
        self._btn(tb, "✔ Select All",      self._data_select_all).pack(side="left", padx=(0, 6))
        self._btn(tb, "✘ Clear",           self._data_clear_sel).pack(side="left", padx=(0, 6))
        self._btn(tb, "🗑 Delete Selected", self._data_delete,
                  bg="#7b1c1c", fg="#ffcdd2").pack(side="right")

        frm_raw = tk.LabelFrame(p, text="  Raw AQUA Pull Snapshots  (data/raw/)  ",
                                font=FONT_UI, bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_raw.pack(fill="x", expand=False, padx=12, pady=(0, 4))

        self.raw_tree = self._make_tree(frm_raw, ("filename", "size", "modified"))
        self.raw_tree.heading("filename", text="File",     anchor="w")
        self.raw_tree.heading("size",     text="Size",     anchor="e")
        self.raw_tree.heading("modified", text="Modified", anchor="w")
        self.raw_tree.column("filename", width=400, stretch=True)
        self.raw_tree.column("size",     width=90,  stretch=False, anchor="e")
        self.raw_tree.column("modified", width=140, stretch=False)

        frm_prog = tk.LabelFrame(p, text="  Per-TP Data Snapshots  (data/programs/)  ",
                                 font=FONT_UI, bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_prog.pack(fill="both", expand=True, padx=12, pady=(0, 4))

        self.prog_tree = self._make_tree(frm_prog, ("filename", "size", "modified"))
        self.prog_tree.heading("filename", text="File",     anchor="w")
        self.prog_tree.heading("size",     text="Size",     anchor="e")
        self.prog_tree.heading("modified", text="Modified", anchor="w")
        self.prog_tree.column("filename", width=400, stretch=True)
        self.prog_tree.column("size",     width=90,  stretch=False, anchor="e")
        self.prog_tree.column("modified", width=140, stretch=False)

        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x", padx=12, pady=(0, 8))
        self.data_status = tk.StringVar()
        tk.Label(bot, textvariable=self.data_status, font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM).pack(side="left")

        self._refresh_data()

    def _make_tree(self, parent: tk.Widget, cols: tuple) -> ttk.Treeview:
        frm  = tk.Frame(parent, bg=BG)
        frm.pack(fill="both", expand=True, padx=6, pady=4)
        tree = ttk.Treeview(frm, columns=cols, show="headings", selectmode="extended")
        vsb  = ttk.Scrollbar(frm, orient="vertical",   command=tree.yview)
        hsb  = ttk.Scrollbar(frm, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        vsb.pack(side="right",  fill="y")
        tree.pack(side="left",  fill="both", expand=True)
        return tree

    def _refresh_data(self) -> None:
        self.raw_tree.delete(*self.raw_tree.get_children())
        self.prog_tree.delete(*self.prog_tree.get_children())

        data_dir = self.base_dir / "data"
        prog_dir = data_dir / "programs"

        # Raw snapshots: data/raw/ (primary location)
        raw_files: list[Path] = []
        raw_dir = data_dir / "raw"
        if raw_dir.exists():
            for f in raw_dir.iterdir():
                if f.is_file():
                    raw_files.append(f)
        # Also check data/programs/{letter}/ for raw_* files (older layout)
        if prog_dir.exists():
            for sub in sorted(prog_dir.iterdir()):
                if sub.is_dir():
                    for f in sub.iterdir():
                        if f.is_file() and f.stem.startswith("raw_"):
                            raw_files.append(f)
        # Legacy: any files directly in data/
        if data_dir.exists():
            for f in data_dir.iterdir():
                if f.is_file():
                    raw_files.append(f)
        raw_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        for f in raw_files:
            display = f"{f.parent.name}/{f.name}" if f.parent != data_dir else f.name
            self.raw_tree.insert("", "end", iid=str(f),
                                 values=(display, _fmt_size(f.stat().st_size), _mtime_str(f)))

        # Per-program accumulated archives: non-raw *.7z / *.gz in data/programs/{letter}/
        prog_count = 0
        if prog_dir.exists():
            for sub in sorted(prog_dir.iterdir()):
                if sub.is_dir():
                    files = sorted(
                        [f for f in sub.iterdir()
                         if f.is_file() and not f.stem.startswith("raw_")
                         and f.suffix in (".7z", ".gz")],
                        key=lambda f: f.stat().st_mtime, reverse=True,
                    )
                    for f in files:
                        self.prog_tree.insert("", "end", iid=str(f),
                                             values=(f"{sub.name}/{f.name}",
                                                     _fmt_size(f.stat().st_size),
                                                     _mtime_str(f)))
                        prog_count += 1

        self.data_status.set(
            f"{len(raw_files)} raw snapshot(s)   |   {prog_count} program cache file(s)")

    def _data_select_all(self) -> None:
        self.raw_tree.selection_set(self.raw_tree.get_children())
        self.prog_tree.selection_set(self.prog_tree.get_children())

    def _data_clear_sel(self) -> None:
        self.raw_tree.selection_remove(self.raw_tree.get_children())
        self.prog_tree.selection_remove(self.prog_tree.get_children())

    def _data_delete(self) -> None:
        sel = list(self.raw_tree.selection()) + list(self.prog_tree.selection())
        if not sel:
            messagebox.showinfo("Nothing selected", "Select files to delete.")
            return
        names = [Path(s).name for s in sel]
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Permanently delete {len(sel)} file(s)?\n\n" + "\n".join(names),
        ):
            return
        errors = []
        for iid in sel:
            try:
                Path(iid).unlink()
                for tree in (self.raw_tree, self.prog_tree):
                    try:
                        tree.delete(iid)
                    except Exception:
                        pass
            except Exception as e:
                errors.append(f"{Path(iid).name}: {e}")
        if errors:
            messagebox.showerror("Delete errors", "\n".join(errors))
        self.data_status.set(
            f"Deleted {len(sel) - len(errors)} file(s)." +
            (f"  {len(errors)} error(s)." if errors else "")
        )

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 4 — Schedule
    # ═════════════════════════════════════════════════════════════════════════

    def _build_schedule_tab(self) -> None:
        import sys as _sys
        p   = self._tab_schedule
        pad = dict(padx=14, pady=6)

        _python = _sys.executable
        _script = str(_HERE / "run_automation.py")

        # Status card
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

        # Configuration
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
        self._sched_hour.set("07")
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

        # Action buttons
        btn_row = tk.Frame(p, bg=BG)
        btn_row.pack(fill="x", padx=14, pady=(2, 4))
        self._btn(btn_row, "✔ Create / Update", self._sched_create,
                  bg="#1b5e20", fg="#c8e6c9").pack(side="left", padx=(0, 8))
        self._btn(btn_row, "⏵ Run Now", self._sched_run_now,
                  bg="#1a3a5c", fg="#90caf9").pack(side="left", padx=(0, 8))
        self._btn(btn_row, "⟳ Rerun (Cached)", self._sched_rerun,
                  bg="#2d3a1e", fg="#b9f0a0").pack(side="left", padx=(0, 8))
        self._btn(btn_row, "🗑 Remove Task", self._sched_remove,
                  bg="#7b1c1c", fg="#ffcdd2").pack(side="left")

        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x", padx=14, pady=(0, 10))
        self._sched_status = tk.StringVar()
        tk.Label(bot, textvariable=self._sched_status,
                 font=("Segoe UI", 9), bg=BG, fg=GREEN).pack(side="left")

        self._sched_refresh()

    def _sched_refresh(self) -> None:
        import csv as _csv, io as _io
        try:
            r = subprocess.run(
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
                colour = (GREEN if status in ("Ready", "Running") else
                          AMBER  if status == "Disabled" else FG_DIM)
                self._sched_dot.config(fg=colour)
                self._sched_state.config(text=status, fg=colour)
                self._sched_next.config(text=next_run if next_run not in ("N/A", "") else "—")
                self._sched_last.config(text=last_run if last_run not in ("N/A", "") else "—")
                res_fg = GREEN if last_res in ("0", "0x0") else (
                    RED if last_res not in ("—", "", "267011") else FG)
                self._sched_result.config(text=last_res, fg=res_fg)
            else:
                self._sched_state.config(text="Unknown", fg=AMBER)
        except Exception as e:
            self._sched_state.config(text="Error", fg=RED)
            self._sched_status.set(f"Error querying task: {e}")

    def _sched_create(self) -> None:
        import sys as _sys
        hh = self._sched_hour.get().zfill(2)
        mm = self._sched_min.get().zfill(2)
        if (not hh.isdigit() or not mm.isdigit()
                or not (0 <= int(hh) <= 23)
                or not (0 <= int(mm) <= 59)):
            messagebox.showerror("Invalid time", f"Invalid time value: {hh}:{mm}")
            return
        tr  = f'"{_sys.executable}" "{_HERE / "run_automation.py"}"'
        cmd = ["schtasks", "/create",
               "/tn", _TASK_NAME,
               "/tr", tr,
               "/sc", "daily",
               "/st", f"{hh}:{mm}",
               "/f"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                self._sched_status.set(f"Task created — runs daily at {hh}:{mm}.")
            else:
                messagebox.showerror(
                    "schtasks failed",
                    r.stderr.strip() or r.stdout.strip() or "Unknown error")
            self._sched_refresh()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _sched_run_now(self) -> None:
        if not messagebox.askyesno("Run Now",
                                   f'Start "{_TASK_NAME}" immediately?\n\n'
                                   'This kicks off a full AQUA pull + VMIN run.'):
            return
        try:
            r = subprocess.run(["schtasks", "/run", "/tn", _TASK_NAME],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                self._sched_status.set("Task started (running in background).")
            else:
                msg = r.stderr.strip() or r.stdout.strip()
                messagebox.showerror("schtasks /run failed",
                                     msg or "Task not found — create it first.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _sched_remove(self) -> None:
        if not messagebox.askyesno("Remove Task",
                                   f'Delete scheduled task "{_TASK_NAME}"?'):
            return
        try:
            r = subprocess.run(["schtasks", "/delete", "/tn", _TASK_NAME, "/f"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                self._sched_status.set("Task removed.")
            else:
                msg = r.stderr.strip() or r.stdout.strip()
                if "cannot find" in msg.lower():
                    self._sched_status.set("Task was not scheduled.")
                else:
                    messagebox.showerror("schtasks /delete failed",
                                         msg or "Unknown error")
            self._sched_refresh()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _sched_rerun(self) -> None:
        """Open a Rerun dialog: optional --keys filter + --local-csv, then run
        run_automation.py --force with live log output."""
        import sys as _sys, subprocess as _sp

        dlg = tk.Toplevel(self)
        dlg.title("Rerun (Cached) — VMIN")
        dlg.configure(bg=BG)
        dlg.resizable(True, True)
        dlg.geometry("820x580")
        dlg.transient(self)

        # ── Button bar (TOP) ─────────────────────────────────────────────────────
        top_bar = tk.Frame(dlg, bg=BG)
        top_bar.pack(fill="x", padx=12, pady=(10, 6))
        status_var = tk.StringVar(value="Ready.")
        tk.Label(top_bar, textvariable=status_var, font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM).pack(side="right", padx=(8, 0))

        # ── Options row ──────────────────────────────────────────────────────
        opts = tk.Frame(dlg, bg=BG)
        opts.pack(fill="x", padx=12, pady=(0, 4))

        tk.Label(opts, text="--keys filter:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        keys_var = tk.StringVar()
        tk.Entry(opts, textvariable=keys_var, font=FONT_MONO,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", width=30
                 ).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=4)
        tk.Label(opts, text="e.g. 0H61C_119325  (blank = all)",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                 ).grid(row=0, column=2, sticky="w", pady=4)

        tk.Label(opts, text="--local-csv:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).grid(row=1, column=0, sticky="w", padx=(0, 6), pady=4)
        csv_var = tk.StringVar()
        tk.Entry(opts, textvariable=csv_var, font=FONT_MONO,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", width=30
                 ).grid(row=1, column=1, sticky="ew", padx=(0, 4), pady=4)

        def _browse():
            from tkinter import filedialog
            f = filedialog.askopenfilename(
                title="Select AQUA CSV / gz",
                filetypes=[("Data files", "*.csv *.csv.gz *.gz"), ("All", "*.*")],
                initialdir=str(self.base_dir / "data"),
            )
            if f:
                csv_var.set(f)
        self._btn(opts, "Browse", _browse, bg=BG3
                  ).grid(row=1, column=2, sticky="w", pady=4)
        tk.Label(opts, text="(blank = use cached programs/*.csv.gz)",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                 ).grid(row=1, column=3, sticky="w", padx=(6, 0), pady=4)
        opts.columnconfigure(1, weight=1)

        # ── Log area ─────────────────────────────────────────────────────────
        log_frm = tk.Frame(dlg, bg=BG)
        log_frm.pack(fill="both", expand=True, padx=12, pady=(4, 8))
        log_txt = tk.Text(log_frm, font=FONT_MONO, bg="#0d1b2a", fg="#c8e6c9",
                          insertbackground=FG, relief="flat", wrap="none",
                          state="disabled")
        log_vsb = ttk.Scrollbar(log_frm, orient="vertical",   command=log_txt.yview)
        log_hsb = ttk.Scrollbar(log_frm, orient="horizontal", command=log_txt.xview)
        log_txt.configure(yscrollcommand=log_vsb.set, xscrollcommand=log_hsb.set)
        log_txt.tag_config("err",  foreground="#ef9a9a")
        log_txt.tag_config("warn", foreground=AMBER)
        log_txt.tag_config("ok",   foreground=GREEN)
        log_hsb.pack(side="bottom", fill="x")
        log_vsb.pack(side="right",  fill="y")
        log_txt.pack(side="left",   fill="both", expand=True)

        # ── Shared state ───────────────────────────────────────────────────────
        _running = [False]
        start_btn_ref: list = [None]

        def _append(line: str) -> None:
            tag = ""
            lo = line.lower()
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
            keys_val = keys_var.get().strip()
            csv_val  = csv_var.get().strip()
            cmd = [_sys.executable, str(_HERE / "run_automation.py"), "--force"]
            if keys_val:
                cmd += ["--keys", keys_val]
            if csv_val:
                cmd += ["--local-csv", csv_val]
            _append("$ " + " ".join(cmd))
            _append("-" * 60)
            try:
                proc = _sp.Popen(
                    cmd,
                    stdout=_sp.PIPE, stderr=_sp.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    bufsize=1,
                )
                for line in proc.stdout:
                    dlg.after(0, _append, line.rstrip())
                proc.wait()
                rc = proc.returncode
                dlg.after(0, _append, "-" * 60)
                dlg.after(0, _append, f"Exit code: {rc}")
                dlg.after(0, status_var.set,
                          f"Done — exit {rc}" if rc == 0 else f"FAILED (exit {rc})")
            except Exception as exc:
                dlg.after(0, _append, f"ERROR: {exc}")
                dlg.after(0, status_var.set, "Error launching process.")
            finally:
                _running[0] = False
                dlg.after(0, lambda: start_btn_ref[0].config(
                    text="▶ Start", bg="#00c853", fg="#002200",
                    command=_start))

        def _start():
            if _running[0]:
                return
            _running[0] = True
            log_txt.config(state="normal")
            log_txt.delete("1.0", "end")
            log_txt.config(state="disabled")
            status_var.set("Running…")
            start_btn_ref[0].config(text="Running…", bg=AMBER, fg=BG,
                                    command=lambda: None)
            threading.Thread(target=_do_run, daemon=True).start()

        start_btn = self._btn(top_bar, "▶ Start", _start, bg="#00c853", fg="#002200")
        start_btn.pack(side="left", padx=(0, 8))
        start_btn_ref[0] = start_btn
        self._btn(top_bar, "✕ Close", dlg.destroy, fg=FG_DIM).pack(side="left")


# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="VMIN Automation Manager")
    ap.add_argument("--base-dir", default=str(_BASE_DIR),
                    help="Automation base directory (samba or local)")
    args = ap.parse_args()
    root = tk.Tk()
    root.title("VMIN Automation Manager")
    root.configure(bg=BG)
    root.resizable(True, True)
    root.minsize(700, 520)
    root.geometry("860x600")
    AutomationManager(root, Path(args.base_dir)).pack(fill="both", expand=True)
    root.mainloop()


if __name__ == "__main__":
    main()
