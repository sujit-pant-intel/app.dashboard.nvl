"""
Trend Chart Automation Manager — GUI to manage trend chart generation.

Tabs:
  1. Email & Filter  — recipient + chart parameters (trend_product_config.json)
  2. Run History     — past HTML chart generations; view/delete
  3. Data Files      — input CSV snapshots and cached outputs
  4. Schedule        — Windows Task Scheduler: create, check, run now, remove + Rerun (Cached)

Usage:
    python manage_automation.py
    python manage_automation.py --base-dir "\\\\server\\share\\auto\\trend"
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import tempfile
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

# ── defaults ──────────────────────────────────────────────────────────────────
_HERE      = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent   # app.dashboard.nvl/
_BASE_DIR  = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\trend")
_CFG_NAME       = "trend_product_config.json"
_EMAIL_CFG_NAME = "trend_setup_config.json"
_CFG_DIR        = _REPO_ROOT / "shared" / "setup" / "automation" / "trend-dashboard"
_EMAIL_TO       = "sujit.n.pant@intel.com"
_TASK_NAME = "NVL-BLLC Trend Chart"

# ── AQUA pull ─────────────────────────────────────────────────────────────────
_AQUA_CFG     = _REPO_ROOT / "shared" / "setup" / "automation" / "trend-dashboard" / "NVL_Yield-Trend - AutoPull.txt"
_AQUA_EXE_GAR = r"\\PGSAPP3301.gar.corp.intel.com\Installer\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"
#_AQUA_EXE_AMR = r"\\FMSAPP3301.amr.corp.intel.com\Installer\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"
_AQUA_EXE_AMR = r"\\amr.corp.intel.com\ec\proj\fm\MPD\AQUA\AquaHbase\AquaCMDClient\Client\AquaCmdLine.exe"




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

def _find_auto_config_json(devrevstep: str = "") -> Path | None:
    """Mirror trend_chart._find_auto_config: glob shared/setup/yield-dashboard/ for a matching JSON."""
    d = _REPO_ROOT / "shared" / "setup" / "config" / "yield-dashboard"
    if not d.exists():
        return None
    jsons = sorted(d.glob("*.json"))
    if not jsons:
        return None
    if devrevstep:
        key = devrevstep.upper()
        for p in jsons:
            if p.name.upper().startswith(key):
                return p
    # Fallback: skip 'default' files, pick first that has a 'name' field
    non_default = [p for p in jsons if not p.stem.lower().startswith("default")]
    for p in non_default:
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("name", "").strip():
                return p
        except Exception:
            pass
    return non_default[0] if non_default else jsons[0]


def _product_name_from_csv(csv_path: str) -> str:
    """Scan up to 200 rows of csv_path, find the most common devrevstep prefix,
    look up the matching product JSON, return its 'name' field."""
    try:
        import csv as _csv, collections as _col
        counts: dict[str, int] = _col.Counter()
        with open(csv_path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            reader = _csv.DictReader(fh)
            for i, row in enumerate(reader):
                drs = next((v for k, v in row.items() if k.strip().lower().startswith("devrevstep") and v), "")
                if drs:
                    counts[drs.strip()[:6].upper()] += 1
                if i >= 199:
                    break
        if counts:
            best_prefix = counts.most_common(1)[0][0]
            cfg_p = _find_auto_config_json(best_prefix)
            if cfg_p:
                return json.loads(cfg_p.read_text(encoding="utf-8")).get("name", "").strip()
    except Exception:
        pass
    return ""


def _product_name_from_html(html_path) -> str:
    """Parse the generated HTML for the chart_name embedded by trend_chart.py.
    Looks for  \"chart_name\": \"<value>\"  in the DATA JSON block."""
    try:
        import re as _re
        text = Path(html_path).read_text(encoding="utf-8", errors="replace")
        m = _re.search(r'"chart_name"\s*:\s*"([^"]+)"', text)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return ""


def _compress_to_7z(src: Path) -> Path:
    """Compress src to src.7z using py7zr; falls back to zip. Returns archive path."""
    archive = src.with_suffix(".7z")
    try:
        import py7zr
        with py7zr.SevenZipFile(archive, "w") as z:
            z.write(src, src.name)
        src.unlink()
        return archive
    except ImportError:
        pass
    # Fallback: zip
    import zipfile
    zpath = src.with_suffix(".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(src, src.name)
    src.unlink()
    return zpath


def _decompress_csv(path: Path) -> Path:
    """If path is a compressed archive, extract the first .csv inside it to a
    temp file and return that temp path.  Plain .csv returned unchanged."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return path
    tmp_dir = Path(tempfile.mkdtemp(prefix="trend_csv_"))
    if suffix == ".7z":
        try:
            import py7zr
            with py7zr.SevenZipFile(path, "r") as z:
                names = [n for n in z.getnames() if n.lower().endswith(".csv")]
                if not names:
                    raise ValueError("No .csv found inside archive.")
                z.extract(targets=[names[0]], path=tmp_dir)
                return tmp_dir / names[0]
        except ImportError:
            raise RuntimeError("py7zr is required to open .7z files: pip install py7zr")
    if suffix == ".zip":
        import zipfile
        with zipfile.ZipFile(path, "r") as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise ValueError("No .csv found inside zip.")
            z.extract(names[0], tmp_dir)
            return tmp_dir / names[0]
    if suffix in (".gz", ".bz2"):
        import gzip, bz2
        opener = gzip.open if suffix == ".gz" else bz2.open
        out = tmp_dir / path.stem          # e.g. foo.csv from foo.csv.gz
        with opener(path, "rb") as fi, open(out, "wb") as fo:
            fo.write(fi.read())
        return out
    raise ValueError(f"Unsupported archive format: {suffix}")



def _load_config(cfg_path: Path) -> dict:
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"email_to": _EMAIL_TO, "interval": "weekly", "topn": 8, "thresh": 0.0}


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
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class AutomationManager(tk.Frame):
    def __init__(self, master, base_dir: Path) -> None:
        super().__init__(master, bg=BG)
        self.base_dir    = base_dir
        self.cfg_path   = _CFG_DIR / _CFG_NAME
        self.cfg        = _load_config(self.cfg_path)
        self.ecfg_path  = _CFG_DIR / _EMAIL_CFG_NAME
        self.ecfg       = _load_config(self.ecfg_path) if self.ecfg_path.exists() else {
            "email_to": _EMAIL_TO, "smtp_server": "smtp.intel.com",
            "smtp_port": 25, "smtp_from": _EMAIL_TO,
            "subject_prefix": "NVL-BLLC Trend Chart",
        }

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
        tk.Label(hdr, text="Trend Chart Automation Manager", font=FONT_TITLE,
                 bg=BG3, fg=ACCENT).pack(side="left", padx=14, pady=8)
        info = tk.Frame(hdr, bg=BG3)
        info.pack(side="left", padx=4)
        tk.Label(info, text=f"base_dir: {self.base_dir}", font=("Segoe UI", 10, "bold"),
                 bg=BG3, fg="#5BB8FF").pack(anchor="w")
        tk.Label(info, text=f"config: {self.ecfg_path}", font=("Segoe UI", 9),
                 bg=BG3, fg="#7ECFFF").pack(anchor="w")

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
            self._refresh_task()

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 1 — Email & Filter
    # ═════════════════════════════════════════════════════════════════════════

    def _build_email_tab(self) -> None:
        p   = self._tab_email
        pad = dict(padx=14, pady=6)

        # Top action bar
        top = tk.Frame(p, bg=BG)
        top.pack(fill="x", padx=14, pady=(10, 2))
        tk.Label(top, text=f"Config: {self.ecfg_path}", font=("Segoe UI", 8),
                 bg=BG, fg=FG_DIM).pack(side="left")
        self._btn(top, "Cancel", self.winfo_toplevel().destroy, fg=FG_DIM
                  ).pack(side="right", padx=(6, 0))
        self._btn(top, "Save", self._save_email_config,
                  bg="#1b5e20", fg="#00ff7f").pack(side="right")

        # ── Recipients ────────────────────────────────────────────────────────
        frm = tk.LabelFrame(p, text="  Recipients  ", font=FONT_UI,
                            bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm.pack(fill="x", **pad)

        self.email_var       = tk.StringVar(value=self.ecfg.get("email_to",       _EMAIL_TO))
        self.alert_email_var = tk.StringVar(value=self.ecfg.get("email_to_alert",
                                            self.ecfg.get("email_to", _EMAIL_TO)))

        for row, label, var, color, note in [
            (0, "Report To:", self.email_var,       GREEN, "Trend report recipient(s) (semicolons OK)"),
            (1, "Alerts To:", self.alert_email_var, AMBER, "Pipeline failures / errors"),
        ]:
            tk.Label(frm, text=label, font=FONT_UI, bg=BG, fg=color
                     ).grid(row=row, column=0, sticky="w", padx=8, pady=4)
            tk.Entry(frm, textvariable=var, font=FONT_UI, bg=BG2, fg=FG,
                     insertbackground=FG, relief="flat", width=48
                     ).grid(row=row, column=1, padx=8, pady=4, sticky="ew")
            tk.Label(frm, text=note, font=("Segoe UI", 7), bg=BG, fg=FG_DIM
                     ).grid(row=row, column=2, sticky="w", padx=(0, 8))
        frm.columnconfigure(1, weight=1)

        # ── Chart parameters ──────────────────────────────────────────────────
        frm_params = tk.LabelFrame(p, text="  Chart Parameters  ", font=FONT_UI,
                                   bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_params.pack(fill="x", **pad)

        row = 0
        tk.Label(frm_params, text="Interval:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        self.interval_var = tk.StringVar(value=self.cfg.get("interval", "weekly"))
        for i, iv in enumerate(["daily", "weekly", "monthly"]):
            tk.Radiobutton(frm_params, text=iv, variable=self.interval_var, value=iv,
                           font=FONT_UI, bg=BG, fg=FG, selectcolor=BG2, activebackground=BG
                           ).grid(row=row, column=1 + i, sticky="w", padx=4, pady=4)

        row += 1
        tk.Label(frm_params, text="Top N IBins:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        self.topn_var = tk.StringVar(value=str(self.cfg.get("topn", 8)))
        tk.Spinbox(frm_params, from_=1, to=50, textvariable=self.topn_var,
                   font=FONT_MONO, bg=BG2, fg=FG, width=6, relief="flat"
                   ).grid(row=row, column=1, sticky="w", padx=8, pady=4)

        row += 1
        tk.Label(frm_params, text="Threshold (%):", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        self.thresh_var = tk.StringVar(value=str(self.cfg.get("thresh", 0.0)))
        tk.Spinbox(frm_params, from_=0.0, to=100.0, increment=0.1, textvariable=self.thresh_var,
                   font=FONT_MONO, bg=BG2, fg=FG, width=6, relief="flat"
                   ).grid(row=row, column=1, sticky="w", padx=8, pady=4)

        # ── Status ────────────────────────────────────────────────────────────
        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x", padx=14, pady=(0, 12))
        self.email_status = tk.StringVar()
        tk.Label(bot, textvariable=self.email_status, font=("Segoe UI", 9),
                 bg=BG, fg=GREEN).pack(side="left")

    def _save_email_config(self) -> None:
        email_to = self.email_var.get().strip()
        if not email_to:
            messagebox.showerror("Error", "Email recipient cannot be empty.")
            return
        alert_to = self.alert_email_var.get().strip() or email_to
        # Save chart parameters to trend_product_config.json
        self.cfg.update({
            "interval": self.interval_var.get(),
            "topn":     int(self.topn_var.get() or 8),
            "thresh":   float(self.thresh_var.get() or 0.0),
        })
        # Save email settings to trend_setup_config.json
        self.ecfg.update({
            "email_to":       email_to,
            "email_to_alert": alert_to,
        })
        try:
            _save_config(self.cfg_path,  self.cfg)
            _save_config(self.ecfg_path, self.ecfg)
            self.email_status.set("✓ Saved trend_product_config.json + trend_setup_config.json")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    # ═════════════════════════════════════════════════════════════════════════
    # TAB 2 — Run History
    # ═════════════════════════════════════════════════════════════════════════

    def _build_history_tab(self) -> None:
        p = self._tab_history

        tb = tk.Frame(p, bg=BG)
        tb.pack(fill="x", padx=12, pady=(10, 4))
        self._btn(tb, "↺ Refresh",       self._refresh_history).pack(side="left", padx=(0, 6))
        self._btn(tb, "✔ Select All",    self._hist_select_all).pack(side="left", padx=(0, 6))
        self._btn(tb, "✘ Clear",         self._hist_clear_sel).pack(side="left", padx=(0, 6))
        self._btn(tb, "🌐 Open HTML",    self._hist_open_html,
                  bg="#1a3550", fg="#80d8ff").pack(side="left", padx=(0, 6))
        self._btn(tb, "✉ Send Report",   self._hist_send_email,
                  bg="#1a3a1a", fg="#a5d6a7").pack(side="left", padx=(0, 6))
        self._btn(tb, "📂 Open Report",  self._hist_open_report,
                  bg="#1a3a3c", fg="#80deea").pack(side="left", padx=(0, 6))
        self._btn(tb, "🔄 Rebuild Index", self._hist_generate_index,
                  bg="#2d3b1a", fg="#c5e1a5").pack(side="left", padx=(0, 6))
        self._btn(tb, "🗑 Delete",       self._hist_delete,
                  bg="#5d1a1a", fg="#ffcdd2").pack(side="right")

        cols = ("date", "size", "status")
        self.hist_tree = ttk.Treeview(p, columns=cols, show="tree headings",
                                      selectmode="extended")
        self.hist_tree.heading("#0",     text="Report Name",  anchor="w",      command=lambda c="#0":     self._sort_tree(self.hist_tree, c))
        self.hist_tree.heading("date",   text="Date / Time",  anchor="w",      command=lambda c="date":   self._sort_tree(self.hist_tree, c))
        self.hist_tree.heading("size",   text="Size",         anchor="e",      command=lambda c="size":   self._sort_tree(self.hist_tree, c))
        self.hist_tree.heading("status", text="Status",       anchor="center", command=lambda c="status": self._sort_tree(self.hist_tree, c))
        self.hist_tree.column("#0",      width=260, stretch=True)
        self.hist_tree.column("date",    width=150, stretch=False)
        self.hist_tree.column("size",    width=90,  stretch=False, anchor="e")
        self.hist_tree.column("status",  width=80,  stretch=False, anchor="center")

        vsb = ttk.Scrollbar(p, orient="vertical",   command=self.hist_tree.yview)
        hsb = ttk.Scrollbar(p, orient="horizontal", command=self.hist_tree.xview)
        self.hist_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        hsb.pack(side="bottom", fill="x",    padx=12, pady=(0, 0))
        vsb.pack(side="right",  fill="y")
        self.hist_tree.pack(fill="both", expand=True, padx=(12, 0), pady=(0, 0))
        self.hist_tree.bind("<Double-1>", lambda _e: self._hist_open_html())

        # Right-click context menu
        self._hist_ctx = tk.Menu(self, tearoff=0, bg=BG2, fg=FG,
                                 activebackground=BG3, activeforeground=ACCENT,
                                 font=FONT_UI, bd=0)
        self._hist_ctx.add_command(label="🌐  Open in Browser",
                                   command=self._hist_open_html)
        self._hist_ctx.add_command(label="📋  Copy file:// link",
                                   command=lambda: self._hist_open_html(copy_only=True))
        self._hist_ctx.add_command(label="✉  Send via Email",
                                   command=self._hist_send_email)
        self._hist_ctx.add_command(label="�  Open Report (no email)",
                                   command=self._hist_open_report)
        self._hist_ctx.add_separator()
        self._hist_ctx.add_command(label="🗑  Delete", command=self._hist_delete)
        self.hist_tree.bind("<Button-3>", self._hist_show_ctx)

        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x", padx=12, pady=(4, 8))
        self.hist_status = tk.StringVar()
        tk.Label(bot, textvariable=self.hist_status, font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM).pack(side="left")

        self._refresh_history()

    def _refresh_history(self) -> None:
        self.hist_tree.delete(*self.hist_tree.get_children())
        reports_dir = self.base_dir / "reports"
        if not reports_dir.exists():
            self.hist_status.set(f"No reports/ folder found under {self.base_dir}")
            return
        files = sorted(reports_dir.glob("*.html"), reverse=True)[:50]
        for f in files:
            self.hist_tree.insert("", "end", iid=str(f), text=f.name,
                                  values=(_mtime_str(f), _fmt_size(f.stat().st_size), "Ready"))
        self.hist_status.set(f"{len(files)} report(s)")

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

    def _hist_open_html(self, copy_only: bool = False) -> None:
        import webbrowser
        sel = self.hist_tree.selection()
        if not sel:
            messagebox.showinfo("No report selected", "Select a report first.")
            return
        if len(sel) > 1 and not copy_only:
            opened = 0
            for iid in sel:
                p = Path(iid)
                if p.exists():
                    webbrowser.open(p.as_uri())
                    opened += 1
            self.hist_status.set(f"Opened {opened} report(s) in browser." if opened
                                 else "No HTML files found.")
            return
        html = Path(sel[0])
        if not html.exists():
            messagebox.showerror("Not found", f"File not found:\n{html}")
            return
        url = html.as_uri()
        if copy_only:
            self.clipboard_clear()
            self.clipboard_append(url)
            self.hist_status.set(f"Copied to clipboard: {url}")
        else:
            webbrowser.open(url)
            self.hist_status.set(f"Opened: {html.name}")

    def _hist_generate_index(self) -> None:
        """Scan reports/ on samba and rewrite index.html with only files that exist."""
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("_gi", _HERE / "yld" / "generate_index.py")
            _gi   = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_gi)
            _gi.build_index(self.base_dir)
            out = self.base_dir / "reports" / "index.html"
            self.hist_status.set(f"Index rebuilt \u2192 {out}")
        except Exception as e:
            messagebox.showerror("Rebuild Index failed", str(e))

    def _hist_delete(self) -> None:
        sel = self.hist_tree.selection()
        if not sel:
            messagebox.showinfo("Nothing selected", "Select report(s) to delete.")
            return
        names = [Path(s).name for s in sel]
        if not messagebox.askyesno("Confirm Delete",
                                   f"Delete {len(sel)} report file(s)?\n\n" + "\n".join(names)):
            return
        errors = []
        for iid in sel:
            try:
                Path(iid).unlink()
                self.hist_tree.delete(iid)
            except Exception as e:
                errors.append(f"{Path(iid).name}: {e}")
        if errors:
            messagebox.showerror("Delete errors", "\n".join(errors))
        self.hist_status.set(
            f"Deleted {len(sel) - len(errors)} file(s)." +
            (f"  {len(errors)} error(s)." if errors else "")
        )

    def _hist_send_email(self) -> None:
        """Send the selected HTML report via email with a full historical table."""
        sel = self.hist_tree.selection()
        if not sel:
            messagebox.showinfo("No report selected", "Select a report to send.")
            return
        if len(sel) > 1:
            messagebox.showinfo("One at a time", "Select a single report to send.")
            return

        html_path = Path(sel[0])
        if not html_path.exists():
            messagebox.showerror("Not found", f"File not found:\n{html_path}")
            return

        email_to    = self.ecfg.get("email_to", _EMAIL_TO)
        interval    = self.interval_var.get()
        date_tag    = datetime.now().strftime("%Y-%m-%d")
        # Subject auto-built from product name via devrevstep in CSV → shared JSON
        _prod_name2 = _product_name_from_csv(str(html_path))  # won't work on HTML
        if not _prod_name2:
            _cfg_p = _find_auto_config_json()
            if _cfg_p:
                try:
                    _prod_name2 = json.loads(_cfg_p.read_text(encoding="utf-8")).get("name", "").strip()
                except Exception:
                    pass
        subject     = f"{_prod_name2 or 'Yield Trend Chart'} - Yield Trend Report - {date_tag}"

        if not messagebox.askyesno("Send Email",
                                   f"Send report to:\n  {email_to}\n\n"
                                   f"Subject:\n  {subject}\n\n"
                                   f"Attachment:\n  {html_path.name}"):
            return

        self.hist_status.set("Sending email…")
        self.update_idletasks()

        def _run():
            try:
                self._send_report_email(html_path=html_path, interval=interval, ok=True)
                self.after(0, self.hist_status.set,
                           f"✓ Email sent to {email_to}: {html_path.name}")
            except Exception as e:
                self.after(0, messagebox.showerror, "Send failed", str(e))
                self.after(0, self.hist_status.set, "Email send failed.")

        threading.Thread(target=_run, daemon=True).start()

    def _hist_open_report(self) -> None:
        """Open the latest saved trend report from reports/ — or regenerate from a plain CSV."""
        reports_dir = self.base_dir / "reports"
        existing    = sorted(reports_dir.glob("*.html"), key=lambda f: f.stat().st_mtime,
                             reverse=True) if reports_dir.exists() else []
        if existing:
            # Reports already live in reports/ — open the latest one
            latest = existing[0]
            self.hist_status.set(f"Opening latest saved report: {latest.name}")
            import webbrowser
            webbrowser.open(latest.as_uri())
            return

        # No HTML yet — try to regenerate from a plain CSV in data/
        data_dir   = self.base_dir / "data"
        csv_files  = sorted(data_dir.glob("*.csv"), key=lambda f: f.stat().st_mtime,
                             reverse=True) if data_dir.exists() else []
        if not csv_files:
            messagebox.showinfo(
                "No reports",
                f"No saved reports found in:\n  {reports_dir}\n\n"
                "Use 'Run Now' or 'Rerun (Cached)' to generate one.",
            )
            return

        reports_dir.mkdir(parents=True, exist_ok=True)
        src = csv_files[0]
        self.hist_status.set(f"Generating report from {src.name}…")
        self.update_idletasks()

        def _run():
            try:
                trend_script = str(
                    _REPO_ROOT / "yield-dashboard"
                    / "yld" / "src" / "trend_chart.py"
                )
                from datetime import datetime as _dt
                ts_file  = _dt.now().strftime("%Y%m%d_%H%M%S")
                out_html = reports_dir / f"Trend_Report_{ts_file}.html"
                import subprocess as _sp
                r = _sp.run(
                    [sys.executable, trend_script, str(src), "--out", str(out_html)],
                    capture_output=True, text=True, timeout=300,
                )
                if r.returncode != 0:
                    err = r.stderr.strip()[:400] or r.stdout.strip()[:400] or f"exit {r.returncode}"
                    raise RuntimeError(err)
                # regenerate index.html
                import importlib.util as _ilu
                _spec = _ilu.spec_from_file_location("_gi", _HERE / "yld" / "generate_index.py")
                _gi = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_gi)
                _gi.build_index(self.base_dir)
                def _done():
                    self.hist_status.set(f"Saved \u2192 {out_html.name}")
                    self._refresh_history()
                    import webbrowser
                    webbrowser.open(out_html.as_uri())
                self.after(0, _done)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Save failed", str(e)))
                self.after(0, lambda: self.hist_status.set("Save failed."))

        threading.Thread(target=_run, daemon=True).start()

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

        frm_csv = tk.LabelFrame(p, text="  Input CSV Files  (data/)  ",
                                font=FONT_UI, bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_csv.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.csv_tree = self._make_tree(frm_csv, ("size", "modified"))
        self.csv_tree.heading("#0",       text="File",     anchor="w", command=lambda c="#0":      self._sort_tree(self.csv_tree, c))
        self.csv_tree.heading("size",     text="Size",     anchor="e", command=lambda c="size":     self._sort_tree(self.csv_tree, c))
        self.csv_tree.heading("modified", text="Modified", anchor="w", command=lambda c="modified": self._sort_tree(self.csv_tree, c))
        self.csv_tree.column("#0",        width=400, stretch=True)
        self.csv_tree.column("size",      width=90,  stretch=False, anchor="e")
        self.csv_tree.column("modified",  width=140, stretch=False)

        frm_rpt = tk.LabelFrame(p, text="  Output Reports  (reports/)  ",
                                font=FONT_UI, bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_rpt.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.rpt_tree = self._make_tree(frm_rpt, ("size", "modified"))
        self.rpt_tree.heading("#0",       text="File",     anchor="w", command=lambda c="#0":      self._sort_tree(self.rpt_tree, c))
        self.rpt_tree.heading("size",     text="Size",     anchor="e", command=lambda c="size":     self._sort_tree(self.rpt_tree, c))
        self.rpt_tree.heading("modified", text="Modified", anchor="w", command=lambda c="modified": self._sort_tree(self.rpt_tree, c))
        self.rpt_tree.column("#0",        width=400, stretch=True)
        self.rpt_tree.column("size",      width=90,  stretch=False, anchor="e")
        self.rpt_tree.column("modified",  width=140, stretch=False)

        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x", padx=12, pady=(0, 8))
        self.data_status = tk.StringVar()
        tk.Label(bot, textvariable=self.data_status, font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM).pack(side="left")

        self._refresh_data()

    def _make_tree(self, parent: tk.Widget, cols: tuple) -> ttk.Treeview:
        frm  = tk.Frame(parent, bg=BG)
        frm.pack(fill="both", expand=True, padx=6, pady=4)
        tree = ttk.Treeview(frm, columns=cols, show="tree headings", selectmode="extended")
        vsb  = ttk.Scrollbar(frm, orient="vertical",   command=tree.yview)
        hsb  = ttk.Scrollbar(frm, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        vsb.pack(side="right",  fill="y")
        tree.pack(side="left",  fill="both", expand=True)
        return tree

    def _sort_tree(self, tree: ttk.Treeview, col: str) -> None:
        """Click-to-sort any Treeview column. Toggles asc/desc on each click."""
        if not hasattr(self, '_tree_sort'):
            self._tree_sort: dict = {}
        key = (id(tree), col)
        reverse = not self._tree_sort.get(key, False)
        self._tree_sort[key] = reverse
        _UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        def _key(iid: str):
            v = tree.item(iid, "text") if col == "#0" else tree.set(iid, col)
            m = re.match(r'^([\d.]+)\s*(B|KB|MB|GB|TB)?$', v.strip(), re.I)
            if m:
                return float(m.group(1)) * _UNITS.get((m.group(2) or "B").upper(), 1)
            try:
                return float(v.replace(",", ""))
            except ValueError:
                return v.lower()
        items = sorted(tree.get_children(""), key=_key, reverse=reverse)
        for i, iid in enumerate(items):
            tree.move(iid, "", i)
        arrow = " ↓" if reverse else " ↑"
        for c in list(tree["columns"]):
            base = tree.heading(c)["text"].rstrip(" ↑↓")
            tree.heading(c, text=base + (arrow if c == col else ""))
        if "tree" in str(tree.cget("show")):
            base = tree.heading("#0")["text"].rstrip(" ↑↓")
            tree.heading("#0", text=base + (arrow if col == "#0" else ""))

    def _refresh_data(self) -> None:
        self.csv_tree.delete(*self.csv_tree.get_children())
        self.rpt_tree.delete(*self.rpt_tree.get_children())

        data_dir = self.base_dir / "data"
        csv_count = 0
        if data_dir.exists():
            _patterns = ("*.csv", "*.7z", "*.zip", "*.gz", "*.bz2")
            all_data = sorted(
                (f for pat in _patterns for f in data_dir.glob(pat)),
                key=lambda f: f.stat().st_mtime, reverse=True,
            )
            # de-duplicate (a file could match multiple patterns on some OSes)
            seen = set()
            for f in all_data:
                if f in seen:
                    continue
                seen.add(f)
                self.csv_tree.insert("", "end", iid=str(f), text=f.name,
                                     values=(_fmt_size(f.stat().st_size), _mtime_str(f)))
                csv_count += 1

        reports_dir = self.base_dir / "reports"
        rpt_count = 0
        if reports_dir.exists():
            files = sorted(reports_dir.glob("*.html"), key=lambda f: f.stat().st_mtime, reverse=True)
            for f in files:
                self.rpt_tree.insert("", "end", iid=str(f), text=f.name,
                                     values=(_fmt_size(f.stat().st_size), _mtime_str(f)))
                rpt_count += 1

        self.data_status.set(f"{csv_count} CSV file(s)   |   {rpt_count} report(s)")

    def _data_select_all(self) -> None:
        self.csv_tree.selection_set(self.csv_tree.get_children())
        self.rpt_tree.selection_set(self.rpt_tree.get_children())

    def _data_clear_sel(self) -> None:
        self.csv_tree.selection_remove(self.csv_tree.get_children())
        self.rpt_tree.selection_remove(self.rpt_tree.get_children())

    def _data_delete(self) -> None:
        sel = list(self.csv_tree.selection()) + list(self.rpt_tree.selection())
        if not sel:
            messagebox.showinfo("Nothing selected", "Select files to delete.")
            return
        names = [Path(s).name for s in sel]
        if not messagebox.askyesno("Confirm Delete",
                                   f"Permanently delete {len(sel)} file(s)?\n\n" + "\n".join(names)):
            return
        errors = []
        for iid in sel:
            try:
                Path(iid).unlink()
                for tree in (self.csv_tree, self.rpt_tree):
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
        p   = self._tab_schedule
        pad = dict(padx=14, pady=6)

        # ── Status card ───────────────────────────────────────────────────────
        frm_st = tk.LabelFrame(p, text="  Task Status  ", font=FONT_UI,
                               bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_st.pack(fill="x", **pad)

        self._sched_dot   = tk.Label(frm_st, text="●", font=("Segoe UI", 14),
                                     bg=BG, fg=FG_DIM)
        self._sched_dot.grid(row=0, column=0, padx=(10, 4), pady=6, sticky="w")
        self._sched_state = tk.Label(frm_st, text="Checking…",
                                     font=FONT_GROUP, bg=BG, fg=FG_DIM)
        self._sched_state.grid(row=0, column=1, sticky="w", pady=6)
        self._btn(frm_st, "↺ Refresh", self._refresh_task
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

        run_script = str(_HERE / "automation" / "run_trend.py")

        for row, lbl, val in [
            (0, "Task name:", _TASK_NAME),
            (1, "Script:",    run_script),
            (2, "Python:",    sys.executable),
        ]:
            tk.Label(frm_cfg, text=lbl, font=FONT_UI, bg=BG, fg=FG_DIM
                     ).grid(row=row, column=0, sticky="w", padx=(10, 4), pady=3)
            tk.Label(frm_cfg, text=val, font=FONT_MONO, bg=BG, fg=FG,
                     anchor="w", wraplength=520
                     ).grid(row=row, column=1, sticky="w", padx=(0, 10), pady=3)
        frm_cfg.columnconfigure(1, weight=1)

        time_row = tk.Frame(frm_cfg, bg=BG)
        time_row.grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 4))
        tk.Label(time_row, text="Run time:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).pack(side="left", padx=(0, 8))
        self._sched_hour = ttk.Spinbox(time_row, from_=0, to=23, width=4,
                                       format="%02.0f", font=FONT_MONO)
        self._sched_hour.set("03")
        self._sched_hour.pack(side="left")
        tk.Label(time_row, text=":", font=FONT_MONO, bg=BG, fg=FG
                 ).pack(side="left", padx=2)
        self._sched_min = ttk.Spinbox(time_row, from_=0, to=59, width=4,
                                      format="%02.0f", font=FONT_MONO)
        self._sched_min.set("00")
        self._sched_min.pack(side="left")
        tk.Label(time_row, text="(runs while logged in)",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                 ).pack(side="left", padx=(8, 0))

        # ── Day-of-week selector ─────────────────────────────────────────────
        days_row = tk.Frame(frm_cfg, bg=BG)
        days_row.grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))
        tk.Label(days_row, text="Run on:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).pack(side="left", padx=(0, 8))
        self._day_vars: dict[str, tk.BooleanVar] = {}
        for day in ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]:
            var = tk.BooleanVar(value=False)
            self._day_vars[day] = var
            tk.Checkbutton(
                days_row, text=day, variable=var,
                bg=BG, fg=FG, selectcolor=BG3,
                activebackground=BG, activeforeground=ACCENT,
                font=("Segoe UI", 9),
            ).pack(side="left", padx=2)
        tk.Label(days_row, text="(leave all unchecked = daily)",
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
        self._btn(btn_row, "🗑 Remove Task", self._sched_remove,
                  bg="#7b1c1c", fg="#ffcdd2").pack(side="left")

        # ── Status bar ───────────────────────────────────────────────────────
        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x", padx=14, pady=(0, 10))
        self._sched_status = tk.StringVar()
        tk.Label(bot, textvariable=self._sched_status,
                 font=("Segoe UI", 9), bg=BG, fg=GREEN).pack(side="left")

        self._refresh_task()

    def _refresh_task(self) -> None:
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
                rows = list(csv.reader(io.StringIO("\n".join(lines))))
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
                         AMBER if status == "Disabled"           else FG_DIM
                self._sched_dot.config(fg=colour)
                self._sched_state.config(text=status, fg=colour)
                self._sched_next.config(
                    text=next_run if next_run not in ("N/A", "") else "—")
                self._sched_last.config(
                    text=last_run if last_run not in ("N/A", "") else "—")
                res_fg = GREEN if last_res in ("0", "0x0") else \
                         RED   if last_res not in ("—", "", "267011") else FG
                self._sched_result.config(text=last_res, fg=res_fg)

                # Populate day checkboxes from existing task
                days_val = _col("Days")  # e.g. "MON, WED, FRI" or "Every day"
                if days_val and days_val not in ("—", "N/A", "Every day", ""):
                    active = {d.strip().upper() for d in days_val.split(",")}
                    for day, var in self._day_vars.items():
                        var.set(day in active)
                # Populate time from Next Run Time (HH:MM)
                if next_run and next_run not in ("—", "N/A", ""):
                    import re as _re
                    m = _re.search(r"(\d{1,2}):(\d{2})", next_run)
                    if m:
                        try:
                            self._sched_hour.set(f"{int(m.group(1)):02d}")
                            self._sched_min.set(f"{int(m.group(2)):02d}")
                        except Exception:
                            pass
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
        # Save current Email & Filter settings first
        self._save_email_config()
        tr = f'"{sys.executable}" "{_HERE / "automation" / "run_trend.py"}"'
        selected_days = [d for d, v in self._day_vars.items() if v.get()]
        if selected_days:
            cmd = ["schtasks", "/create",
                   "/tn", _TASK_NAME,
                   "/tr", tr,
                   "/sc", "weekly",
                   "/d", ",".join(selected_days),
                   "/st", f"{hh}:{mm}",
                   "/f"]
            desc = f"weekly on {', '.join(selected_days)} at {hh}:{mm}"
        else:
            cmd = ["schtasks", "/create",
                   "/tn", _TASK_NAME,
                   "/tr", tr,
                   "/sc", "daily",
                   "/st", f"{hh}:{mm}",
                   "/f"]
            desc = f"daily at {hh}:{mm}"
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                self._sched_status.set(f"Task created — runs {desc}.")
            else:
                messagebox.showerror("schtasks failed",
                                     r.stderr.strip() or r.stdout.strip() or "Unknown error")
            self._refresh_task()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _sched_run_now(self) -> None:
        """AQUA pull → run_trend.py, with a live log window."""
        if not messagebox.askyesno("Run Now",
                                   "Pull fresh data from AQUA and generate the trend chart?\n\n"
                                   "This may take several minutes."):
            return

        dlg = tk.Toplevel(self)
        dlg.title("Run Now — Live Log")
        dlg.configure(bg=BG)
        dlg.resizable(True, True)
        dlg.geometry("860x520")
        dlg.transient(self)

        status_var = tk.StringVar(value="Starting…")
        tk.Label(dlg, textvariable=status_var, font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM).pack(fill="x", padx=12, pady=(8, 2))

        log = tk.Text(dlg, font=FONT_MONO, bg="#0d1117", fg="#c9d1d9",
                      wrap="none", relief="flat", state="normal")
        sb_y = ttk.Scrollbar(dlg, orient="vertical",   command=log.yview)
        sb_x = ttk.Scrollbar(dlg, orient="horizontal", command=log.xview)
        log.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        sb_y.pack(side="right",  fill="y")
        sb_x.pack(side="bottom", fill="x")
        log.pack(fill="both", expand=True, padx=12, pady=(0, 4))

        def _append(msg: str) -> None:
            log.insert("end", msg)
            log.see("end")
            log.update()

        def _run() -> None:
            try:
                base_dir  = Path(self.base_dir) if hasattr(self, "base_dir") else _BASE_DIR
                data_dir  = base_dir / "data"
                data_dir.mkdir(parents=True, exist_ok=True)
                cfg       = _load_config(self.cfg_path)
                interval  = cfg.get("interval", "weekly")
                dest_csv  = data_dir / f"yield_{interval}.csv"

                # ── Step 1: AQUA pull ─────────────────────────────────────
                _append("═" * 60 + "\n")
                _append("STEP 1 — AQUA pull\n")
                _append("═" * 60 + "\n")

                aqua_exe = (_AQUA_EXE_AMR if Path(_AQUA_EXE_AMR).exists()
                            else _AQUA_EXE_GAR if Path(_AQUA_EXE_GAR).exists()
                            else "")
                if not aqua_exe:
                    _append("ERROR: AquaCmdLine.exe not found on GAR or AMR share.\n")
                    status_var.set("✖ AQUA not found")
                    return
                if not _AQUA_CFG.exists():
                    _append(f"ERROR: AutoPull config not found:\n  {_AQUA_CFG}\n")
                    status_var.set("✖ AutoPull.txt missing")
                    return

                # Parse report name from AutoPull.txt for output filename
                report_name = "NVL_Yield-Trend"
                try:
                    for line in _AQUA_CFG.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                        if line.strip().startswith("@ Report :"):
                            report_name = line.strip().split(":", 1)[1].strip()
                            break
                except Exception:
                    pass

                import time as _time
                ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
                # Sanitize report_name: replace spaces and dashes-with-spaces with underscores
                safe_name = report_name.replace(" - ", "_").replace(" ", "_")
                out_base = data_dir / f"{safe_name}_{ts}"
                _exe_lower = str(aqua_exe).lower()
                _server = "AMR" if "amr" in _exe_lower else "GAR"

                # subprocess.run with a list handles spaces automatically — no manual quoting needed
                aqua_cfg_str = str(_AQUA_CFG)
                aqua_out_str = str(out_base.with_suffix(".zip"))
                aqua_cmd = [
                    aqua_exe,
                    "-AquaServer",     _server,
                    "-ReportConfig",   aqua_cfg_str,
                    "-OutputFileName", aqua_out_str,
                ]
                # Display with quotes for readability only
                _append(f'CMD: "{aqua_exe}" -AquaServer {_server} -ReportConfig "{aqua_cfg_str}" -OutputFileName "{aqua_out_str}"\n\n')
                status_var.set("Pulling AQUA data…")
                import time as _time
                _pull_start = _time.time()

                try:
                    r_aqua = subprocess.run(
                        aqua_cmd, capture_output=True, text=True, timeout=3600
                    )
                    if r_aqua.stdout.strip():
                        _append(r_aqua.stdout.strip() + "\n")
                    if r_aqua.returncode != 0:
                        _append(f"ERROR: AQUA rc={r_aqua.returncode}\n{r_aqua.stderr.strip()[:400]}\n")
                        status_var.set(f"✖ AQUA failed (rc={r_aqua.returncode})")
                        return
                except FileNotFoundError:
                    _append(f"ERROR: AquaCmdLine.exe not found: {aqua_exe}\n")
                    status_var.set("✖ AQUA exe missing")
                    return
                except subprocess.TimeoutExpired:
                    _append("ERROR: AQUA pull timed out (60 min)\n")
                    status_var.set("✖ AQUA timeout")
                    return

                # Find the downloaded file — AQUA ignores -OutputFileName and uses
                # its own internal report name, so search for ANY new file in data_dir
                # that appeared after the pull started (use pull_start_time snapshot).
                import time as _time
                _time.sleep(1)   # brief pause so mtime is definitely after pull_start
                _pull_cutoff = _pull_start - 2  # 2 s grace margin
                written = sorted(
                    [p for p in data_dir.iterdir()
                     if p.is_file()
                     and p.suffix.lower() in (".csv", ".zip", ".7z", ".gz", ".bz2")
                     and p.stat().st_mtime >= _pull_cutoff
                     and p.stat().st_size > 0],
                    key=lambda p: p.stat().st_mtime,
                )
                if not written:
                    _append("ERROR: No output file found after AQUA pull.\n")
                    status_var.set("✖ No AQUA output")
                    return
                pulled = max(written, key=lambda p: p.stat().st_mtime)
                _append(f"Downloaded: {pulled.name}  ({pulled.stat().st_size:,} bytes)\n")

                # Decompress to dest_csv (yield_weekly.csv)
                _append(f"\nDecompressing → {dest_csv.name}…\n")
                import zipfile as _zf, gzip as _gz
                try:
                    suffix = pulled.suffix.lower()
                    if suffix == ".zip":
                        with _zf.ZipFile(pulled) as z:
                            csv_members = [m for m in z.namelist() if m.lower().endswith(".csv")]
                            if not csv_members:
                                _append("ERROR: No CSV inside zip.\n")
                                status_var.set("✖ Zip has no CSV")
                                return
                            with z.open(csv_members[0]) as src, open(dest_csv, "wb") as dst:
                                dst.write(src.read())
                    elif suffix == ".7z":
                        # Use 7-Zip executable (py7zr may not be installed)
                        _7z_exe = Path(r"C:\Program Files\7-Zip\7z.exe")
                        if not _7z_exe.exists():
                            _7z_exe = Path(r"C:\Program Files (x86)\7-Zip\7z.exe")
                        if _7z_exe.exists():
                            import tempfile as _tmp
                            _tmp_dir = Path(_tmp.mkdtemp(prefix="aqua_7z_"))
                            _r7z = subprocess.run(
                                [str(_7z_exe), "e", str(pulled), f"-o{_tmp_dir}", "-y",
                                 "*.csv", "-r"],
                                capture_output=True, text=True, timeout=120,
                            )
                            _csv_files = list(_tmp_dir.glob("*.csv"))
                            if not _csv_files:
                                # fallback: extract everything and pick first file
                                subprocess.run(
                                    [str(_7z_exe), "e", str(pulled), f"-o{_tmp_dir}", "-y"],
                                    capture_output=True, timeout=120,
                                )
                                _csv_files = sorted(_tmp_dir.iterdir(), key=lambda f: f.stat().st_size, reverse=True)
                            if not _csv_files:
                                _append("ERROR: No CSV extracted from 7z.\n")
                                status_var.set("✖ 7z has no CSV")
                                return
                            shutil.copy2(_csv_files[0], dest_csv)
                            shutil.rmtree(_tmp_dir, ignore_errors=True)
                        else:
                            _append("ERROR: 7-Zip not found (C:\\Program Files\\7-Zip\\7z.exe).\n")
                            status_var.set("✖ 7-Zip missing")
                            return
                    elif suffix in (".gz", ".bz2"):
                        import gzip as _gz2, bz2 as _bz2
                        opener = _gz2.open if suffix == ".gz" else _bz2.open
                        with opener(pulled, "rb") as src, open(dest_csv, "wb") as dst:
                            dst.write(src.read())
                    elif suffix == ".csv":
                        shutil.copy2(pulled, dest_csv)
                    else:
                        _append(f"WARNING: Unknown extension {suffix}, copying as-is.\n")
                        shutil.copy2(pulled, dest_csv)
                except Exception as ex:
                    _append(f"ERROR decompressing: {ex}\n")
                    status_var.set("✖ Decompress failed")
                    return

                # Delete the raw AQUA download
                try:
                    if pulled.suffix.lower() != ".csv":
                        pulled.unlink()
                        _append(f"Deleted: {pulled.name}\n")
                except Exception as ex:
                    _append(f"WARNING: Could not delete {pulled.name}: {ex}\n")

                _append(f"CSV ready: {dest_csv}\n\n")

                # ── Step 2: Split CSV by devrevstep ──────────────────────
                _append("═" * 60 + "\n")
                _append("STEP 2 — Split CSV by devrevstep (8PF6CV / 8PF5CV)\n")
                _append("═" * 60 + "\n")
                ts_split = datetime.now().strftime("%Y%m%d_%H%M%S")
                split_map = AutomationManager._split_csv_by_devrevstep(
                    dest_csv, data_dir, ts_split
                )
                if not split_map:
                    _append("ERROR: No matching devrevstep rows found (8PF6CV / 8PF5CV).\n")
                    status_var.set("✖ No matching devrevstep rows")
                    return
                for _pfx, _sp in split_map.items():
                    _append(f"  {_pfx} → {_sp.name}  ({_sp.stat().st_size:,} bytes)\n")
                # Remove the merged dest_csv — only split files are kept
                try:
                    dest_csv.unlink(missing_ok=True)
                except Exception:
                    pass
                _append("\n")

                # ── Step 3: Run trend_chart.py on each split CSV ──────────
                _append("═" * 60 + "\n")
                _append("STEP 3 — Generate trend charts\n")
                _append("═" * 60 + "\n")
                trend_script = str(
                    _REPO_ROOT / "yield-dashboard"
                    / "yld" / "src" / "trend_chart.py"
                )
                reports_dir = self.base_dir / "reports"
                reports_dir.mkdir(parents=True, exist_ok=True)
                generated: list = []  # list of (html_path, returncode)
                for _pfx, _csv_file in split_map.items():
                    _out_html = reports_dir / f"{_csv_file.stem}.html"
                    _cmd = [sys.executable, trend_script,
                            str(_csv_file), "--out", str(_out_html)]
                    _append(f"\n--- {_pfx} ---\n")
                    _append("$ " + " ".join(_cmd) + "\n")
                    status_var.set(f"Running trend_chart ({_pfx})…")
                    try:
                        _proc = subprocess.Popen(
                            _cmd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                        )
                        for _line in _proc.stdout:
                            _append(_line)
                        _proc.wait()
                        generated.append((_out_html, _proc.returncode))
                        if _proc.returncode == 0:
                            _append(f"✔ {_pfx} done → {_out_html.name}\n")
                        else:
                            _append(f"✖ {_pfx} exit {_proc.returncode}\n")
                    except Exception as _exc:
                        _append(f"ERROR running trend_chart for {_pfx}: {_exc}\n")
                        generated.append((_out_html, -1))

                # ── Step 4: 7z split input CSVs ───────────────────────────
                _append("\n" + "═" * 60 + "\n")
                _append("STEP 4 — Compressing split CSV files\n")
                _append("═" * 60 + "\n")
                for _pfx, _csv_file in split_map.items():
                    try:
                        _arc = _compress_to_7z(_csv_file)
                        _append(f"  {_pfx} → {_arc.name}\n")
                    except Exception as _ex:
                        _append(f"  WARNING: Could not compress {_csv_file.name}: {_ex}\n")

                # ── Step 5: Send combined email ────────────────────────────
                if generated:
                    _append("\n" + "═" * 60 + "\n")
                    _append("STEP 5 — Sending email\n")
                    _append("═" * 60 + "\n")
                    status_var.set("Sending email…")
                    try:
                        self._send_combined_report_email(
                            reports=generated, interval=interval
                        )
                        _append("✔ Email sent.\n")
                    except Exception as _e:
                        _append(f"WARNING: Email failed: {_e}\n")

                _all_ok = all(rc == 0 for _, rc in generated)
                if _all_ok:
                    _append("\n✔ All done.\n")
                    status_var.set("✔ Complete")
                else:
                    _failed = sum(1 for _, rc in generated if rc != 0)
                    _append(f"\n✖ {_failed} chart(s) failed.\n")
                    status_var.set(f"✖ {_failed} chart(s) failed")

                # regenerate index.html
                try:
                    import importlib.util as _ilu
                    _spec = _ilu.spec_from_file_location("_gi", _HERE / "yld" / "generate_index.py")
                    _gi = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_gi)
                    _gi.build_index(self.base_dir)
                    _append("Index updated → reports/index.html\n")
                except Exception as _idx_e:
                    _append(f"WARNING: index update failed: {_idx_e}\n")

                self._refresh_task()
                self._refresh_data()

            except Exception as ex:
                _append(f"\nUNEXPECTED ERROR: {ex}\n")
                status_var.set(f"✖ Error: {str(ex)[:60]}")

        threading.Thread(target=_run, daemon=True).start()

    def _sched_rerun(self) -> None:
        """Open a Rerun dialog: select input CSV (AQUA pull or cached),
        then run trend_chart.py with live log output."""

        dlg = tk.Toplevel(self)
        dlg.title("Rerun (Cached)")
        dlg.configure(bg=BG)
        dlg.resizable(True, True)
        dlg.geometry("820x580")
        dlg.transient(self)

        # ── Button bar (TOP) ─────────────────────────────────────────────────
        top_bar = tk.Frame(dlg, bg=BG)
        top_bar.pack(fill="x", padx=12, pady=(10, 6))
        status_var = tk.StringVar(value="Ready.")
        tk.Label(top_bar, textvariable=status_var, font=("Segoe UI", 9),
                 bg=BG, fg=FG_DIM).pack(side="right", padx=(8, 0))

        # ── Options row ──────────────────────────────────────────────────────
        opts = tk.Frame(dlg, bg=BG)
        opts.pack(fill="x", padx=12, pady=(0, 4))

        tk.Label(opts, text="Input CSV:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        csv_var = tk.StringVar()
        # Pre-fill with last cached CSV if available
        cached = self._load_cache()
        if cached:
            last_run = sorted(cached.keys())[-1]
            last_csv = cached[last_run].get("csv_path", "")
            if last_csv:
                csv_var.set(last_csv)
        tk.Entry(opts, textvariable=csv_var, font=FONT_MONO,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", width=40
                 ).grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=4)

        def _browse_csv():
            from tkinter import filedialog
            init_dir = str(self.base_dir / "data") if (self.base_dir / "data").exists() else None
            f = filedialog.askopenfilename(
                title="Select input CSV (AQUA pull or cached data)",
                filetypes=[
                    ("Data files", "*.csv *.7z *.zip *.gz *.bz2"),
                    ("CSV files", "*.csv"),
                    ("7-Zip archives", "*.7z"),
                    ("ZIP archives", "*.zip"),
                    ("GZip files", "*.gz *.bz2"),
                    ("All files", "*.*"),
                ],
                initialdir=init_dir,
            )
            if f:
                csv_var.set(f)
        self._btn(opts, "Browse", _browse_csv, bg=BG3
                  ).grid(row=0, column=2, sticky="w", pady=4)
        tk.Label(opts, text="(blank = use cached programs/*.csv)",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                 ).grid(row=0, column=3, sticky="w", padx=(6, 0), pady=4)

        # Output path row — auto-named with product name + timestamp
        tk.Label(opts, text="Output HTML:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).grid(row=1, column=0, sticky="w", padx=(0, 6), pady=4)
        interval = self.interval_var.get()
        _prod_cfg  = self.cfg.get("cfg", "")
        _prod_name = ""
        if _prod_cfg:
            try:
                _prod_name = json.loads(Path(_prod_cfg).read_text()).get("name", "").strip()
            except Exception:
                pass
        import re as _re
        _prod_slug = _re.sub(r'[\\/:*?"<>| ]+', "_", _prod_name).strip("_") if _prod_name else "trend"
        _ts_now    = datetime.now().strftime("%Y%m%d_%H%M")
        _out_name  = f"{_prod_slug}_{interval}_{_ts_now}.html"
        out_var = tk.StringVar(value=str(self.base_dir / "reports" / _out_name))

        # When CSV selection changes, mirror its stem as the output HTML name (timestamped)
        def _sync_out_from_csv(*_):
            csv_p = csv_var.get().strip()
            if csv_p:
                stem = Path(csv_p).stem
                _ts  = datetime.now().strftime("%Y%m%d_%H%M")
                out_var.set(str(self.base_dir / "reports" / f"{stem}_{_ts}.html"))
        csv_var.trace_add("write", _sync_out_from_csv)

        tk.Entry(opts, textvariable=out_var, font=FONT_MONO,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", width=40
                 ).grid(row=1, column=1, sticky="ew", padx=(0, 4), pady=4)

        def _browse_out():
            from tkinter import filedialog
            f = filedialog.asksaveasfilename(
                title="Save HTML report as",
                defaultextension=".html",
                filetypes=[("HTML files", "*.html"), ("All files", "*.*")],
                initialdir=str(self.base_dir / "reports"),
            )
            if f:
                out_var.set(f)
        self._btn(opts, "Browse", _browse_out, bg=BG3
                  ).grid(row=1, column=2, sticky="w", pady=4)
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

        _running = [False]
        start_btn_ref: list = [None]

        def _append(line: str) -> None:
            tag = ""
            lo = line.lower()
            if any(w in lo for w in ("error", "traceback", "failed", "exception")):
                tag = "err"
            elif "warning" in lo:
                tag = "warn"
            elif any(w in lo for w in ("written", "saved", " ok ", "→ ok")):
                tag = "ok"
            log_txt.config(state="normal")
            log_txt.insert("end", line + "\n", tag)
            log_txt.see("end")
            log_txt.config(state="disabled")

        def _do_run():
            csv_path = csv_var.get().strip()
            if not csv_path:
                dlg.after(0, _append, "ERROR: No input CSV selected.")
                dlg.after(0, status_var.set, "Error — no CSV selected.")
                _running[0] = False
                dlg.after(0, lambda: start_btn_ref[0].config(
                    text="▶ Start", bg="#00c853", fg="#002200", command=_start))
                return

            interval = self.interval_var.get()
            topn     = int(self.topn_var.get() or 8)
            thresh   = float(self.thresh_var.get() or 0.0)
            email_to = self.email_var.get().strip()
            ts_run   = datetime.now().strftime("%Y%m%d_%H%M%S")
            data_dir    = self.base_dir / "data"
            reports_dir = self.base_dir / "reports"

            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                reports_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                dlg.after(0, _append, f"ERROR: Cannot create dirs: {e}")
                _running[0] = False
                dlg.after(0, lambda: start_btn_ref[0].config(
                    text="▶ Start", bg="#00c853", fg="#002200", command=_start))
                return

            # ── Step 1: Decompress input if needed → plain CSV ────────────────
            dlg.after(0, _append, "═" * 60)
            dlg.after(0, _append, "STEP 1 — Decompressing input file")
            dlg.after(0, _append, "═" * 60)
            src_p = Path(csv_path)
            suffix = src_p.suffix.lower()
            try:
                if suffix == ".7z":
                    _7z_exe = Path(r"C:\Program Files\7-Zip\7z.exe")
                    if not _7z_exe.exists():
                        _7z_exe = Path(r"C:\Program Files (x86)\7-Zip\7z.exe")
                    if not _7z_exe.exists():
                        raise RuntimeError("7-Zip not found")
                    import tempfile as _tmp
                    _tmp_dir = Path(_tmp.mkdtemp(prefix="rerun_7z_"))
                    subprocess.run(
                        [str(_7z_exe), "e", str(src_p), f"-o{_tmp_dir}", "-y"],
                        capture_output=True, timeout=120,
                    )
                    _csvs = sorted(_tmp_dir.glob("*.csv"),
                                   key=lambda f: f.stat().st_size, reverse=True)
                    if not _csvs:
                        _csvs = sorted(_tmp_dir.iterdir(),
                                       key=lambda f: f.stat().st_size, reverse=True)
                    if not _csvs:
                        raise RuntimeError("No file extracted from .7z")
                    raw_csv_path = _csvs[0]
                    dlg.after(0, _append, f"Extracted: {raw_csv_path.name}")
                elif suffix == ".zip":
                    import zipfile as _zf, tempfile as _tmp
                    _tmp_dir = Path(_tmp.mkdtemp(prefix="rerun_zip_"))
                    with _zf.ZipFile(src_p) as z:
                        members = [m for m in z.namelist() if m.lower().endswith(".csv")]
                        if not members:
                            members = z.namelist()
                        z.extract(members[0], _tmp_dir)
                    raw_csv_path = _tmp_dir / members[0]
                    dlg.after(0, _append, f"Extracted: {raw_csv_path.name}")
                elif suffix in (".gz", ".bz2"):
                    import gzip as _gz, bz2 as _bz2, tempfile as _tmp
                    _tmp_dir = Path(_tmp.mkdtemp(prefix="rerun_gz_"))
                    raw_csv_path = _tmp_dir / src_p.stem
                    opener = _gz.open if suffix == ".gz" else _bz2.open
                    with opener(src_p, "rb") as fi, open(raw_csv_path, "wb") as fo:
                        fo.write(fi.read())
                    dlg.after(0, _append, f"Extracted: {raw_csv_path.name}")
                else:
                    raw_csv_path = src_p
                    dlg.after(0, _append, f"Using CSV directly: {src_p.name}")
            except Exception as e:
                dlg.after(0, _append, f"ERROR decompressing: {e}")
                dlg.after(0, status_var.set, "✖ Decompress failed")
                _running[0] = False
                dlg.after(0, lambda: start_btn_ref[0].config(
                    text="▶ Start", bg="#00c853", fg="#002200", command=_start))
                return

            # ── Step 2: Split by devrevstep ───────────────────────────────────
            dlg.after(0, _append, "═" * 60)
            dlg.after(0, _append, "STEP 2 — Splitting by devrevstep (8PF6CV / 8PF5CV)")
            dlg.after(0, _append, "═" * 60)
            try:
                split_map = AutomationManager._split_csv_by_devrevstep(
                    raw_csv_path, data_dir, ts_run
                )
            except Exception as e:
                dlg.after(0, _append, f"ERROR splitting CSV: {e}")
                dlg.after(0, status_var.set, "✖ Split failed")
                _running[0] = False
                dlg.after(0, lambda: start_btn_ref[0].config(
                    text="▶ Start", bg="#00c853", fg="#002200", command=_start))
                return

            if not split_map:
                dlg.after(0, _append,
                          "ERROR: No rows matched 8PF6CV or 8PF5CV — check devrevstep column.")
                dlg.after(0, status_var.set, "✖ No matching rows")
                _running[0] = False
                dlg.after(0, lambda: start_btn_ref[0].config(
                    text="▶ Start", bg="#00c853", fg="#002200", command=_start))
                return

            for pfx, sp in split_map.items():
                dlg.after(0, _append, f"  {pfx} → {sp.name}  ({sp.stat().st_size:,} bytes)")

            # ── Step 3: Run trend_chart.py on each split CSV ──────────────────
            dlg.after(0, _append, "═" * 60)
            dlg.after(0, _append, "STEP 3 — Generating trend charts")
            dlg.after(0, _append, "═" * 60)
            trend_script = str(
                _REPO_ROOT / "yield-dashboard"
                / "yld" / "src" / "trend_chart.py"
            )
            generated: list = []  # (html_path, returncode)
            for pfx, csv_file in split_map.items():
                out_html = reports_dir / f"{csv_file.stem}.html"
                cmd = [sys.executable, trend_script,
                       str(csv_file), "--out", str(out_html)]
                dlg.after(0, _append, f"\n--- {pfx} ---")
                dlg.after(0, _append, "$ " + " ".join(cmd))
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace",
                        bufsize=1,
                    )
                    for line in proc.stdout:
                        dlg.after(0, _append, line.rstrip())
                    proc.wait()
                    generated.append((out_html, proc.returncode))
                    dlg.after(0, _append,
                              f"✔ {pfx} → {out_html.name}" if proc.returncode == 0
                              else f"✖ {pfx} exit {proc.returncode}")
                except Exception as exc:
                    dlg.after(0, _append, f"ERROR: {exc}")
                    generated.append((out_html, -1))

            # ── Step 4: Compress split CSVs to 7z ────────────────────────────
            dlg.after(0, _append, "═" * 60)
            dlg.after(0, _append, "STEP 4 — Compressing split CSVs")
            dlg.after(0, _append, "═" * 60)
            for pfx, csv_file in split_map.items():
                if csv_file.exists():
                    try:
                        arc = _compress_to_7z(csv_file)
                        dlg.after(0, _append, f"  {pfx} → {arc.name}")
                    except Exception as ex:
                        dlg.after(0, _append, f"  WARNING: compress failed: {ex}")

            # ── Step 5: Send combined email ───────────────────────────────────
            dlg.after(0, _append, "═" * 60)
            dlg.after(0, _append, "STEP 5 — Sending email")
            dlg.after(0, _append, "═" * 60)
            def _send_after_run():
                try:
                    self._send_combined_report_email(
                        reports=generated, interval=interval
                    )
                    dlg.after(0, _append, "✔ Email sent.")
                    dlg.after(0, status_var.set, "Done — email sent.")
                except Exception as _e:
                    dlg.after(0, _append, f"WARNING: Email failed: {_e}")
            threading.Thread(target=_send_after_run, daemon=True).start()

            all_ok = all(rc == 0 for _, rc in generated)
            # regenerate index.html
            try:
                import importlib.util as _ilu
                _spec = _ilu.spec_from_file_location("_gi", _HERE / "yld" / "generate_index.py")
                _gi = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_gi)
                _gi.build_index(self.base_dir)
                dlg.after(0, _append, "Index updated → reports/index.html")
            except Exception as _idx_e:
                dlg.after(0, _append, f"WARNING: index update failed: {_idx_e}")
            dlg.after(0, self._refresh_history)
            dlg.after(0, self._refresh_data)
            dlg.after(0, status_var.set,
                      "✔ Complete" if all_ok
                      else f"✖ {sum(1 for _,rc in generated if rc!=0)} chart(s) failed")
            _running[0] = False
            dlg.after(0, lambda: start_btn_ref[0].config(
                text="▶ Start", bg="#00c853", fg="#002200", command=_start))

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
            self._refresh_task()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ── Shared email helper ────────────────────────────────────────────────────

    def _send_report_email(self, html_path: Path, interval: str, ok: bool = True,
                           csv_path: str = "") -> None:
        """Send email for a generated report with full historical table."""
        import smtplib
        from email import encoders as _enc
        from email.mime.base import MIMEBase
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        ecfg        = self.ecfg
        email_to    = ecfg.get("email_to",       _EMAIL_TO)
        smtp_server = ecfg.get("smtp_server",     "smtp.intel.com")
        smtp_port   = int(ecfg.get("smtp_port",   25))
        smtp_from   = ecfg.get("smtp_from",       _EMAIL_TO)
        subject_tpl = ecfg.get("subject", ecfg.get("subject_prefix", "{product} \u2014 {interval} \u2014 {date}"))

        # Product name: read from the generated HTML (authoritative — trend_chart.py wrote it)
        # Fall back to CSV scan, then JSON auto-detect
        product_name = _product_name_from_html(html_path)
        if not product_name:
            product_name = _product_name_from_csv(csv_path) if csv_path else ""
        if not product_name:
            _cfg_p = _find_auto_config_json()
            if _cfg_p:
                try:
                    product_name = json.loads(_cfg_p.read_text(encoding="utf-8")).get("name", "").strip()
                except Exception:
                    pass

        now_str  = datetime.now().strftime("%Y-%m-%d %H:%M")
        date_tag = datetime.now().strftime("%Y-%m-%d")
        subject  = f"{product_name or 'Yield Trend Chart'} - Yield Trend Report - {date_tag}"

        # Build historical table (all reports in same dir, newest first)
        reports_dir = html_path.parent
        try:
            all_reports = sorted(
                reports_dir.glob("*.html"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            all_reports = [html_path] if html_path.exists() else []

        rows = ""
        for rpt in all_reports:
            is_current   = rpt.resolve() == html_path.resolve()
            status_txt   = ("\u2714 OK" if ok else "\u2716 FAILED") if is_current else "\u2014"
            status_color = ("#66bb6a" if ok else "#ef5350") if is_current else "#90a4ae"
            try:
                mtime = datetime.fromtimestamp(rpt.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                mtime = "?"
            row_bg  = "background:#1a2d4a;" if is_current else "background:#252526;"
            bold    = "font-weight:bold;"   if is_current else ""
            badge   = (
                " &nbsp;<span style='background:#1565c0;color:#fff;"
                "font-size:0.75em;padding:1px 5px;border-radius:3px'>NEW</span>"
                if is_current else ""
            )
            rpt_prod = _product_name_from_html(rpt) or product_name
            rows += (
                f"<tr style='{row_bg}'>"
                f"<td style='color:#d4d4d4;{bold}'>{rpt_prod or '&#8212;'}</td>"
                f"<td style='color:#d4d4d4;{bold}'><a href='{rpt.as_uri()}' style='color:#4fc3f7'>{rpt_prod + ' \u2014 ' + mtime if rpt_prod else rpt.name}</a>{badge}</td>"
                f"<td style='color:{status_color};font-weight:bold'>{status_txt}</td>"
                f"<td style='color:#9e9e9e'>{mtime}</td>"
                f"</tr>\n"
            )

        topn   = self.topn_var.get()
        thresh = self.thresh_var.get()
        body   = f"""
<html>
<head>
<meta name="color-scheme" content="dark light">
<style>
  @media (prefers-color-scheme: dark) {{
    body {{ background:#1e1e1e!important; color:#d4d4d4!important; }}
    h2   {{ color:#4fc3f7!important; }}
    .meta {{ color:#9e9e9e!important; }}
    table {{ border-color:#444!important; }}
    th   {{ background:#1565c0!important; color:#fff!important; }}
    td   {{ border-color:#444!important; }}
    .footer {{ color:#757575!important; }}
    a    {{ color:#4fc3f7!important; }}
  }}
</style>
</head>
<body style="font-family:Segoe UI,Arial;background:#1e1e1e;color:#d4d4d4;max-width:760px">
<h2 style="color:#4fc3f7;margin-bottom:4px">
  {product_name + " \u2014 " if product_name else ""}Yield Trend Chart
</h2>
<p class="meta" style="color:#9e9e9e;font-size:0.9em;margin-top:0">
  {now_str} &nbsp;|&nbsp; interval={interval} &nbsp; top-N={topn} &nbsp; thresh={thresh}%
</p>
<table border="1" cellpadding="6" cellspacing="0"
       style="border-collapse:collapse;width:100%;font-size:0.9em;border-color:#444">
  <tr style="background:#1565c0;color:#fff">
    <th align="left">Product</th>
    <th align="left">Report</th>
    <th align="left">Status</th>
    <th align="left">Generated</th>
  </tr>
  {rows}
</table>
<p class="footer" style="color:#757575;font-size:0.8em;margin-top:12px">
  Open any <code>.html</code> in a browser \u2014 fully self-contained.
</p>
<p class="footer" style="color:#616161;font-size:0.75em">
  NVL BLLC Trend Automation \u2022 {now_str}
</p>
</body></html>
"""
        msg = MIMEMultipart("mixed")
        msg["From"]    = smtp_from
        msg["To"]      = email_to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html", "utf-8"))

        def _via_outlook():
            import win32com.client as _w
            _ol = _w.Dispatch("Outlook.Application")
            _m  = _ol.CreateItem(0)
            _m.To = email_to; _m.Subject = subject; _m.HTMLBody = body
            try:
                _m.Send()
            except Exception:
                pass

        try:
            _via_outlook()
            return
        except ImportError:
            pass
        except Exception:
            pass

        recipients = [a.strip() for a in email_to.split(";") if a.strip()]
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as s:
            s.sendmail(smtp_from, recipients, msg.as_string())

    # ── Split CSV helper ──────────────────────────────────────────────────────

    @staticmethod
    def _split_csv_by_devrevstep(src_csv: Path, out_dir: Path, ts: str) -> dict:
        """Split src_csv rows by devrevstep prefix into named output CSVs.

        Returns a dict mapping prefix → Path for each file that received rows.
        Only 8PF6CV and 8PF5CV rows are written; all other devrevstep values
        are silently discarded.
        """
        import csv as _csv

        SPLITS = {
            "8PF6CV": f"NVL816-Yield-Trend-Report-{ts}.csv",
            "8PF5CV": f"NVL816-BLLC-Yield-Trend-Report-{ts}.csv",
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        writers: dict = {}
        handles: dict = {}
        out_paths: dict = {}
        try:
            with open(src_csv, newline="", encoding="utf-8", errors="replace") as fh:
                reader = _csv.DictReader(fh)
                fieldnames = reader.fieldnames or []
                for row in reader:
                    drs = next(
                        (v for k, v in row.items()
                         if k.strip().lower().startswith("devrevstep") and v),
                        "",
                    )
                    prefix = drs.strip()[:6].upper()
                    if prefix not in SPLITS:
                        continue
                    if prefix not in writers:
                        p = out_dir / SPLITS[prefix]
                        out_paths[prefix] = p
                        handles[prefix] = open(p, "w", newline="", encoding="utf-8")
                        writers[prefix] = _csv.DictWriter(
                            handles[prefix], fieldnames=fieldnames
                        )
                        writers[prefix].writeheader()
                    writers[prefix].writerow(row)
        finally:
            for h in handles.values():
                h.close()
        return out_paths

    # ── Combined email helper ─────────────────────────────────────────────────

    def _send_combined_report_email(
        self, reports: list, interval: str
    ) -> None:
        """Send one email listing every generated report with its status.

        reports — list of (html_path: Path, returncode: int)
        """
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        ecfg        = self.ecfg
        email_to    = ecfg.get("email_to",    _EMAIL_TO)
        smtp_server = ecfg.get("smtp_server", "smtp.intel.com")
        smtp_port   = int(ecfg.get("smtp_port", 25))
        smtp_from   = ecfg.get("smtp_from",   _EMAIL_TO)

        now_str  = datetime.now().strftime("%Y-%m-%d %H:%M")
        date_tag = datetime.now().strftime("%Y-%m-%d")
        subject  = f"NVL816 Yield Trend Reports — {date_tag}"

        topn   = self.topn_var.get()
        thresh = self.thresh_var.get()

        rows = ""
        for html_path, rc in reports:
            ok          = rc == 0
            status_txt  = "✔ OK" if ok else "✖ FAILED"
            status_clr  = "#66bb6a" if ok else "#ef5350"
            prod_name   = _product_name_from_html(html_path)
            try:
                mtime = datetime.fromtimestamp(
                    html_path.stat().st_mtime
                ).strftime("%Y-%m-%d %H:%M")
            except Exception:
                mtime = "?"
            rows += (
                f"<tr style='background:#1a2d4a;'>"
                f"<td style='color:#d4d4d4;font-weight:bold'>{prod_name or html_path.stem}</td>"
                f"<td style='color:#d4d4d4'>"
                f"<a href='{html_path.as_uri()}' style='color:#4fc3f7'>{html_path.name}</a></td>"
                f"<td style='color:{status_clr};font-weight:bold'>{status_txt}</td>"
                f"<td style='color:#9e9e9e'>{mtime}</td>"
                f"</tr>\n"
            )

        body = f"""
<html>
<head><meta name="color-scheme" content="dark light">
<style>
  @media (prefers-color-scheme: dark) {{
    body {{ background:#1e1e1e!important; color:#d4d4d4!important; }}
    h2   {{ color:#4fc3f7!important; }}
    th   {{ background:#1565c0!important; color:#fff!important; }}
    a    {{ color:#4fc3f7!important; }}
  }}
</style></head>
<body style="font-family:Segoe UI,Arial;background:#1e1e1e;color:#d4d4d4;max-width:780px">
<h2 style="color:#4fc3f7;margin-bottom:4px">NVL816 — Yield Trend Reports</h2>
<p style="color:#9e9e9e;font-size:0.9em;margin-top:0">
  {now_str} &nbsp;|&nbsp; interval={interval} &nbsp; top-N={topn} &nbsp; thresh={thresh}%
</p>
<table border="1" cellpadding="6" cellspacing="0"
       style="border-collapse:collapse;width:100%;font-size:0.9em;border-color:#444">
  <tr style="background:#1565c0;color:#fff">
    <th align="left">Product</th>
    <th align="left">Report</th>
    <th align="left">Status</th>
    <th align="left">Generated</th>
  </tr>
  {rows}
</table>
<p style="color:#757575;font-size:0.8em;margin-top:12px">
  Open any <code>.html</code> in a browser — fully self-contained.
</p>
<p style="color:#616161;font-size:0.75em">NVL BLLC Trend Automation &bull; {now_str}</p>
</body></html>
"""
        msg = MIMEMultipart("mixed")
        msg["From"]    = smtp_from
        msg["To"]      = email_to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html", "utf-8"))

        def _via_outlook():
            import win32com.client as _w
            _ol = _w.Dispatch("Outlook.Application")
            _m  = _ol.CreateItem(0)
            _m.To = email_to; _m.Subject = subject; _m.HTMLBody = body
            try:
                _m.Send()
            except Exception:
                pass

        try:
            _via_outlook()
            return
        except ImportError:
            pass
        except Exception:
            pass

        recipients = [a.strip() for a in email_to.split(";") if a.strip()]
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as s:
            s.sendmail(smtp_from, recipients, msg.as_string())

    # ── Cache helpers ──────────────────────────────────────────────────────────

    def _get_cache_file(self) -> Path:
        """Get path to cache JSON file, with fallback to local if Samba unavailable."""
        try:
            cache_dir = self.base_dir / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir / "run_cache.json"
        except (OSError, PermissionError):
            local_cache = _HERE / ".cache"
            try:
                local_cache.mkdir(exist_ok=True)
                return local_cache / "trend_run_cache.json"
            except Exception:
                return _HERE / "trend_run_cache.json"

    def _load_cache(self) -> dict:
        try:
            f = self._get_cache_file()
            if f.exists():
                return json.loads(f.read_text())
        except Exception:
            pass
        return {}

    def _save_cache(self, data: dict) -> None:
        try:
            f = self._get_cache_file()
            f.write_text(json.dumps(data, indent=2))
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Trend Chart Automation Manager")
    ap.add_argument("--base-dir", default=None,
                    help="Trend automation base directory (overrides config)")
    args = ap.parse_args()
    cfg = _load_config(_CFG_DIR / _EMAIL_CFG_NAME)
    base_dir = Path(args.base_dir) if args.base_dir else Path(cfg.get("base_dir", str(_BASE_DIR)))
    root = tk.Tk()
    root.title("Trend Chart Automation Manager")
    root.configure(bg=BG)
    root.resizable(True, True)
    root.minsize(800, 600)
    root.geometry("960x720")
    AutomationManager(root, base_dir).pack(fill="both", expand=True)
    root.mainloop()


if __name__ == "__main__":
    main()
