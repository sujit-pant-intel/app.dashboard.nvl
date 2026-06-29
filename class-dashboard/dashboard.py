"""dashboard.py — CLASS Dashboard  (package/class-test analysis).

Pipeline output is intentionally minimal:
    1. Material merge
    2. Reticle merge
    3. CLASS analysis HTML (main page)

No additional dashboard/index/heatmap/bin-distribution pages are generated.

UI: sidebar navigation (left panel) + content frames (right panel).
"""
import os
import sys
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR    = os.path.join(_SCRIPT_DIR, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import glob
import json
import re
import shutil
import tempfile
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

# ---------------------------------------------------------------------------
# Palette  (matches yield-dashboard)
# ---------------------------------------------------------------------------
BG   = "#1a252f"
BG2  = "#2c3e50"
BG3  = "#0f1e2b"
FG   = "#ecf0f1"
FG2  = "#95a5a6"
GRN  = "#27ae60"
ABLU = "#3498db"

# ---------------------------------------------------------------------------
# Class-prog column candidates (auto-detection)
# ---------------------------------------------------------------------------
_CLASSPROG_COLS = [
    "CLASS_PROG", "CLASSPROG", "class_prog", "ClassProg",
    "PROG", "prog", "PROGRAM", "program",
    "TEST_NAME", "testname",
]


# ---------------------------------------------------------------------------
# CheckboxDropdown  — multi-select dropdown with checkboxes
# ---------------------------------------------------------------------------

class CheckboxDropdown(tk.Frame):
    """Button that opens a scrollable popup of checkboxes for multi-select filtering."""

    def __init__(self, parent, on_change=None, **kw):
        super().__init__(parent, bg=BG3, **kw)
        self._on_change = on_change
        self._values    = []
        self._vars      = {}
        self._popup     = None
        self._col_name  = None
        self._display_var = tk.StringVar(value="(no data)")

        # ── Combobox-style row: [read-only entry | ▼ button] ─────────────────
        _row = tk.Frame(self, bg="#1f618d", bd=1, relief="solid")
        _row.pack(fill="x")

        # Read-only display entry (looks like a combobox text area)
        self._entry = tk.Entry(
            _row, textvariable=self._display_var,
            state="readonly", readonlybackground=BG2,
            fg=FG, relief="flat", font=("Arial", 9),
            cursor="arrow",
        )
        self._entry.pack(side="left", fill="both", expand=True, padx=(2, 0), pady=2)
        self._entry.bind("<Button-1>", lambda e: self._toggle_popup())

        # ▼ arrow button on the right (clearly indicates dropdown)
        self._btn = tk.Button(
            _row, text=" ▼ ",
            command=self._toggle_popup,
            bg=ABLU, fg="white", activebackground="#1f618d", activeforeground="white",
            relief="flat", cursor="hand2",
            font=("Arial", 9, "bold"), padx=4, pady=2,
        )
        self._btn.pack(side="right", fill="y", pady=2, padx=(0, 2))

    # ── public API ──────────────────────────────────────────────────────────

    def load_from_csv(self, csv_path):
        """Scan CSV (first 100 k rows) to find class-prog column and populate values."""
        try:
            import pandas as _pd
            df = _pd.read_csv(csv_path, nrows=100_000, low_memory=False)
            col = next((c for c in _CLASSPROG_COLS if c in df.columns), None)
            if col is None:
                col_up = {c.upper(): c for c in df.columns}
                col = next((col_up[c.upper()] for c in _CLASSPROG_COLS
                            if c.upper() in col_up), None)
            if col:
                self._col_name = col
                vals = sorted(df[col].dropna().astype(str).unique().tolist())
                self.set_values(vals)
        except Exception:
            pass

    def set_values(self, values):
        """Thread-safe: schedules the actual update on the main tkinter thread."""
        def _apply():
            old_sel = {v for v, var in self._vars.items() if var.get()}
            self._values = list(values)
            self._vars = {}
            for v in self._values:
                was_on = (not old_sel) or (v in old_sel)
                self._vars[v] = tk.BooleanVar(value=was_on)
            self._update_label()
        try:
            self.after(0, _apply)
        except RuntimeError:
            pass  # widget destroyed

    def get_selected(self):
        """Return list of selected values, or None when all selected (= no filter)."""
        if not self._vars:
            return None
        selected = [v for v, var in self._vars.items() if var.get()]
        return None if len(selected) == len(self._vars) else selected

    def get_col_name(self):
        return self._col_name

    # ── label ────────────────────────────────────────────────────────────────

    def _update_label(self):
        if not self._vars:
            self._display_var.set("(no data)")
            return
        selected = [v for v, var in self._vars.items() if var.get()]
        n, total = len(selected), len(self._vars)
        if n == 0:
            self._display_var.set("(none selected)")
        elif n == total:
            self._display_var.set(f"All  ({total})")
        else:
            lbl = ", ".join(selected[:2])
            if n > 2:
                lbl += f"  +{n - 2} more"
            self._display_var.set(lbl)
        if self._on_change:
            self._on_change()

    # ── popup ────────────────────────────────────────────────────────────────

    def _toggle_popup(self):
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()
            self._popup = None
        else:
            self._show_popup()

    def _show_popup(self):
        if not self._values:
            return
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.configure(bg="#243342")
        self._popup = popup
        self.update_idletasks()
        bx = self._entry.winfo_rootx()
        by = self._entry.winfo_rooty() + self._entry.winfo_height() + 4
        popup.geometry(f"200+{bx}+{by}")
        popup.lift()
        popup.focus_set()

        # All / None buttons
        ctrl = tk.Frame(popup, bg="#243342")
        ctrl.pack(fill="x", padx=4, pady=(4, 2))
        for lbl, fn in [("All", self._sel_all), ("None", self._sel_none)]:
            tk.Button(ctrl, text=lbl, command=fn,
                      bg=BG3, fg=FG2, activebackground=BG2,
                      relief="flat", cursor="hand2",
                      font=("Arial", 8), padx=10, pady=2
                      ).pack(side="left", padx=(0, 3))

        # Scrollable checkbox list
        max_h = min(len(self._values) * 24 + 8, 300)
        canvas  = tk.Canvas(popup, bg="#243342",
                            highlightthickness=0, width=192, height=max_h)
        sb      = tk.Scrollbar(popup, orient="vertical", command=canvas.yview)
        inner   = tk.Frame(canvas, bg="#243342")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", pady=(0, 4))
        canvas.pack(side="left", fill="both", expand=True,
                    padx=(4, 0), pady=(0, 4))

        for v in self._values:
            if v not in self._vars:
                self._vars[v] = tk.BooleanVar(value=True)
            tk.Checkbutton(
                inner, text=str(v),
                variable=self._vars[v],
                command=self._update_label,
                bg="#243342", fg=FG, selectcolor=BG3,
                activebackground="#243342", activeforeground=FG,
                font=("Consolas", 9), anchor="w",
            ).pack(fill="x", padx=4, pady=1)

        popup.bind("<FocusOut>", self._on_focus_out)

    def _on_focus_out(self, event):
        try:
            if self._popup and self._popup.winfo_exists():
                focused = self._popup.focus_get()
                if focused and str(focused).startswith(str(self._popup)):
                    return
                self._popup.destroy()
                self._popup = None
        except Exception:
            pass

    def _sel_all(self):
        for var in self._vars.values():
            var.set(True)
        self._update_label()

    def _sel_none(self):
        for var in self._vars.values():
            var.set(False)
        self._update_label()


# ---------------------------------------------------------------------------
# Pipeline worker  (full logic — runs in a background thread)
# ---------------------------------------------------------------------------

def _run_pipeline(csv_path, out_dir, cfg_path, tag, bm_path, log_cb, done_cb,
                  use_tag_subdir=True, class_prog_filter=None, class_prog_col=None):
    try:
        from add_material_type      import add_material_type
        from apply_reticle_mapping  import apply_reticle_mapping
        from class_analysis_html    import generate_html as gen_class_html
        from class_normalize        import normalize
        from _constants             import _MATERIAL_DIR, _RETICLE_DIR, _DEFAULT_SETUP_DIR

        log_cb(f"Loading product config: {cfg_path}")
        cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))

        safe_tag = re.sub(r"[^\w\-.]", "_", tag) or "output"
        out_sub  = (Path(out_dir) / safe_tag) if use_tag_subdir else Path(out_dir)
        out_sub.mkdir(parents=True, exist_ok=True)
        log_cb(f"Output: {out_sub}")

        pcm_path    = str(out_sub / f"{safe_tag}_class_analysis.html")

        tmp_dir = Path(tempfile.mkdtemp(prefix="class_dash_"))
        try:
            # ── pre-step: concat multiple input files ────────────────────────
            if isinstance(csv_path, (list, tuple)):
                if len(csv_path) > 1:
                    import pandas as _pd
                    log_cb(f"Concatenating {len(csv_path)} input files ...")
                    _dfs = [_pd.read_csv(p, low_memory=False) for p in csv_path]
                    _combined = _pd.concat(_dfs, ignore_index=True)
                    _cpath = str(tmp_dir / "_combined_input.csv")
                    _combined.to_csv(_cpath, index=False)
                    log_cb(f"  Combined: {len(_combined)} rows from {len(csv_path)} files")
                    csv_path = _cpath
                else:
                    csv_path = csv_path[0]

            # Step 1 — Material merge
            log_cb("Step 1/3 — Material merge ...")
            mat_csv = add_material_type(
                csv_path       = csv_path,
                collateral_dir = _MATERIAL_DIR,
                output_dir     = str(tmp_dir),
                log_cb         = log_cb,
                hint_lot_col   = cfg.get('sort_lot_col'),
                hint_wafer_col = cfg.get('sort_wafer_col'),
            )

            # Step 2 — Reticle merge
            log_cb("Step 2/3 — Reticle merge ...")
            merged_csv = apply_reticle_mapping(
                csv_path       = mat_csv,
                collateral_dir = _RETICLE_DIR,
                output_dir     = str(tmp_dir),
                log_cb         = log_cb,
            )

            # Step 3 — CLASS analysis HTML (main page)
            log_cb("Step 3/3 — CLASS analysis HTML ...")
            df, vmin_meta = normalize(merged_csv, cfg, log_cb=log_cb)
            gen_class_html(
                df             = df,
                product_config = cfg,
                vmin_meta      = vmin_meta,
                output_path    = pcm_path,
                bm_path        = bm_path,
            )
            log_cb(f"  -> {pcm_path}")

        finally:
            shutil.rmtree(str(tmp_dir), ignore_errors=True)

        done_cb(True, pcm_path)

    except Exception as exc:
        import traceback
        log_cb(f"\nERROR: {exc}")
        log_cb(traceback.format_exc())
        done_cb(False, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_file(directory, pattern):
    hits = glob.glob(os.path.join(directory, pattern))
    return hits[0] if hits else None


def _build_product_setup(cfg, vmin_meta):
    groups = []
    upm_keys = list(cfg.get("sort_upm", {}).keys())
    if upm_keys:
        groups.append({"name": "UPM (Sort)", "patterns": upm_keys})
    ss_keys = list(cfg.get("sort_sicc", {}).keys()) + ["ss_fc"]
    if ss_keys:
        groups.append({"name": "SICC Sort", "patterns": ss_keys})
    sc_keys = list(cfg.get("class_sicc", {}).keys()) + ["sc_fc"]
    if sc_keys:
        groups.append({"name": "SICC Class", "patterns": sc_keys})
    _mod_pfx = {"core": "vc_", "atom": "va_", "ccf": "vf_"}
    for module in ("core", "atom", "ccf"):
        if vmin_meta.get(module):
            groups.append({
                "name":     f"Vmin {module.capitalize()}",
                "patterns": [f"{_mod_pfx[module]}*"],
            })
    return {
        "title":    cfg.get("title",    "CLASS Dashboard"),
        "subtitle": cfg.get("subtitle", ""),
        "groups":   groups,
    }


def _build_master_html(out_path, tag, pcm_path, heatmap_dir, bindist_path=None):
    from _constants import _wm_inject

    def _rel(p):
        if not p:
            return None
        try:
            return os.path.relpath(p, os.path.dirname(out_path))
        except ValueError:
            return p

    links = []
    if bindist_path and os.path.isfile(bindist_path):
        links.append(("Bin Distribution", _rel(bindist_path)))
    if pcm_path and os.path.isfile(pcm_path):
        links.append(("CLASS Analysis", _rel(pcm_path)))
    wm_idx = os.path.join(heatmap_dir, "wafermap.html")
    if os.path.isfile(wm_idx):
        links.append(("Wafer Heatmaps", _rel(wm_idx)))
    else:
        for hf in sorted(glob.glob(os.path.join(heatmap_dir, "*.html")))[:6]:
            links.append((Path(hf).stem, _rel(hf)))

    li = "".join(
        f'<li><a href="{h}" target="_blank">{n}</a></li>'
        for n, h in links if h
    )
    html = (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        f'<title>{tag} \u2014 CLASS Dashboard</title>'
        f'<style>*{{box-sizing:border-box;margin:0;padding:0}}'
        f'body{{font-family:Arial,sans-serif;background:#1a252f;color:#ecf0f1;padding:32px}}'
        f'h1{{font-size:20px;color:#2ecc71;margin-bottom:6px}}'
        f'.sub{{font-size:13px;color:#95a5a6;margin-bottom:24px}}'
        f'ul{{list-style:none;padding:0}}li{{margin:8px 0}}'
        f'a{{color:#3498db;font-size:15px;text-decoration:none;font-weight:bold}}'
        f'a:hover{{color:#2ecc71;text-decoration:underline}}</style></head>'
        f'<body><h1>CLASS Dashboard \u2014 {tag}</h1>'
        f'<p class="sub">Package / class-test analysis</p>'
        f'<ul>{li}</ul></body></html>'
    )
    Path(out_path).write_text(_wm_inject(html), encoding="utf-8")


# ---------------------------------------------------------------------------
# PipelineFrame  — run-pipeline panel
# ---------------------------------------------------------------------------

class PipelineFrame(tk.Frame):
    """Run-pipeline panel: inputs + log.  Embedded in the sidebar layout."""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._out_var = tk.StringVar()
        self._cfg_var = tk.StringVar()
        self._bm_var  = tk.StringVar()
        self._tag_var = tk.StringVar()
        self._running = False
        self._input_listbox = None  # created in _build
        self._auto_fill_cfg()
        self._build()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _auto_fill_cfg(self):
        from _constants import _DEFAULT_SETUP_DIR as _SETUP_DIR
        hits = glob.glob(
            os.path.join(_SETUP_DIR, "*-CLASS-ProductConfig*.json"))
        if hits:
            self._cfg_var.set(hits[0])
        bm_hit = os.path.join(_SETUP_DIR, "NVL_S28C_S28CB_W25_BM.xlsx")
        if os.path.isfile(bm_hit):
            self._bm_var.set(bm_hit)

    def _auto_fill_tag(self, csv_path):
        stem = Path(csv_path).stem
        for ext in (".csv", ".gz", ".zip"):
            stem = stem.replace(ext, "")
        self._tag_var.set(stem.split()[0][:40])

    def log(self, msg: str):
        def _u():
            self._log_box.configure(state="normal")
            self._log_box.insert("end", msg.rstrip("\n") + "\n")
            self._log_box.see("end")
        self.after(0, _u)

    # ── build UI ─────────────────────────────────────────────────────────────

    def _build(self):
        def _lf(text, color=FG2):
            return tk.LabelFrame(self, text=text, bg=BG, fg=color,
                                 font=("Arial", 8, "bold"), padx=6, pady=4,
                                 relief="groove", bd=1)

        def _row(parent, row, label, var, browse_cmd=None):
            tk.Label(parent, text=label, width=20, anchor="w",
                     bg=BG, fg=FG, font=("Arial", 9)).grid(
                row=row, column=0, sticky="w", pady=3, padx=(0, 4))
            e = tk.Entry(parent, textvariable=var,
                         bg=BG2, fg=FG, insertbackground=FG,
                         relief="flat", font=("Consolas", 9))
            e.grid(row=row, column=1, sticky="ew", pady=3, padx=(0, 4))
            parent.columnconfigure(1, weight=1)
            if browse_cmd:
                tk.Button(parent, text="...", command=browse_cmd, width=3,
                          bg="#1f618d", fg="white", activebackground=ABLU,
                          relief="flat", cursor="hand2").grid(
                    row=row, column=2, pady=3)

        def _btn(parent, text, cmd, color=GRN, acolor="#2ecc71"):
            return tk.Button(parent, text=text, command=cmd,
                             bg=color, fg="white", activebackground=acolor,
                             relief="flat", cursor="hand2",
                             font=("Arial", 10, "bold"), padx=10, pady=4)

        # Section title
        tk.Label(self, text="Run Pipeline",
                 bg=BG, fg=ABLU, font=("Arial", 13, "bold")
                 ).pack(fill="x", padx=12, pady=(10, 4))

        # ── Input files (Listbox) ─────────────────────────────────────────────
        frm_files = _lf("Input CSV / GZ / ZIP files", ABLU)
        frm_files.pack(fill="x", padx=10, pady=(0, 4))

        _lb_outer = tk.Frame(frm_files, bg=BG)
        _lb_outer.pack(fill="x", pady=(2, 0))
        _lb_scroll_y = tk.Scrollbar(_lb_outer, orient="vertical")
        _lb_scroll_x = tk.Scrollbar(_lb_outer, orient="horizontal")
        self._input_listbox = tk.Listbox(
            _lb_outer, height=4, selectmode="extended",
            bg=BG2, fg=FG, selectbackground="#1f618d", selectforeground="white",
            activestyle="none", font=("Consolas", 9), relief="flat",
            yscrollcommand=_lb_scroll_y.set,
            xscrollcommand=_lb_scroll_x.set)
        _lb_scroll_y.config(command=self._input_listbox.yview)
        _lb_scroll_x.config(command=self._input_listbox.xview)
        _lb_scroll_y.pack(side="right", fill="y")
        _lb_scroll_x.pack(side="bottom", fill="x")
        self._input_listbox.pack(side="left", fill="both", expand=True)

        _lb_btn_row = tk.Frame(frm_files, bg=BG)
        _lb_btn_row.pack(fill="x", pady=(4, 0))
        _btn(_lb_btn_row, "  Add File(s)  ", self._add_input_files,
             color="#1f618d", acolor=ABLU).pack(side="left", padx=(0, 4))
        _btn(_lb_btn_row, "  Remove Selected  ", self._remove_input_files,
             color="#7b241c", acolor="#a93226").pack(side="left")
        tk.Label(_lb_btn_row, text="Tip: Add from multiple folders freely.",
                 bg=BG, fg=FG2, font=("Arial", 8)).pack(side="left", padx=(8, 0))

        # ── Other Inputs ──────────────────────────────────────────────────────
        frm_in = _lf("Settings", ABLU)
        frm_in.pack(fill="x", padx=10, pady=(0, 4))
        _row(frm_in, 0, "Output folder",        self._out_var, self._browse_out)
        _row(frm_in, 1, "Product config JSON",  self._cfg_var, self._browse_cfg)
        _row(frm_in, 2, "Bin Matrix File",      self._bm_var, self._browse_bm)
        _row(frm_in, 3, "Run tag",              self._tag_var)

        # ── Action buttons ────────────────────────────────────────────────────
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", padx=10, pady=(2, 4))

        self._run_btn = _btn(btn_row, "Run Pipeline", self._on_run)
        self._run_btn.pack(side="left")
        _btn(btn_row, "Open Output", self._on_open,
             color=BG2, acolor="#3d5166").pack(side="left", padx=(6, 0))
        _btn(btn_row, "Clear Log", self._on_clear,
             color=BG2, acolor="#3d5166").pack(side="left", padx=(6, 0))

        # ── Log ───────────────────────────────────────────────────────────────
        log_frm = _lf("Log", FG2)
        log_frm.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self._log_box = scrolledtext.ScrolledText(
            log_frm, wrap="word",
            font=("Consolas", 9), bg="#0d1b26", fg="#a8d8ea",
            relief="flat", insertbackground=FG, state="disabled")
        self._log_box.pack(fill="both", expand=True)

    # ── browse callbacks ──────────────────────────────────────────────────────

    def _add_input_files(self):
        existing = set(self._input_listbox.get(0, tk.END))
        last = self._input_listbox.get(tk.END)
        init_dir = str(Path(last).parent) if last else None
        paths = filedialog.askopenfilenames(
            title="Add CLASS input CSV / GZ / ZIP file(s)",
            initialdir=init_dir,
            filetypes=[("CSV / GZ / ZIP files", "*.csv *.csv.gz *.gz *.zip"), ("All", "*.*")])
        for p in paths:
            if p and p not in existing:
                self._input_listbox.insert(tk.END, p)
                existing.add(p)
        if paths:
            first = self._input_listbox.get(0)
            if not self._tag_var.get():
                self._auto_fill_tag(first)
            if not self._out_var.get():
                self._out_var.set(str(Path(first).parent / "class_output"))

    def _remove_input_files(self):
        for i in reversed(self._input_listbox.curselection()):
            self._input_listbox.delete(i)

    def _browse_out(self):
        p = filedialog.askdirectory(title="Select output folder")
        if p:
            self._out_var.set(p)

    def _browse_cfg(self):
        p = filedialog.askopenfilename(
            title="Select product config JSON",
            filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if p:
            self._cfg_var.set(p)

    def _browse_bm(self):
        p = filedialog.askopenfilename(
            title="Select Bin Matrix File",
            filetypes=[("CSV/Excel", "*.csv *.xlsx *.xls"), ("All", "*.*")])
        if p:
            self._bm_var.set(p)

    # ── run / done ────────────────────────────────────────────────────────────

    def _on_run(self):
        if self._running:
            messagebox.showinfo("Running", "Pipeline is already running.")
            return
        csv_list = list(self._input_listbox.get(0, tk.END))
        out  = self._out_var.get().strip()
        cfg  = self._cfg_var.get().strip()
        bm   = self._bm_var.get().strip()
        tag  = self._tag_var.get().strip() or "CLASS"
        if not csv_list:
            messagebox.showerror("Missing input", "Add at least one input CSV.")
            return
        for p in csv_list:
            if not os.path.isfile(p):
                messagebox.showerror("Missing input", f"File not found:\n{p}")
                return
        if not out:
            messagebox.showerror("Missing output", "Select an output folder.")
            return
        if not cfg or not os.path.isfile(cfg):
            messagebox.showerror("Missing config", "Select a product config JSON.")
            return
        csv = csv_list if len(csv_list) > 1 else csv_list[0]
        os.makedirs(out, exist_ok=True)
        csv_log = "\n".join(f"  CSV: {p}" for p in csv_list)
        self.log(f"[RUN] {tag}\n{csv_log}\n  Out: {out}")
        self._running = True
        self._run_btn.configure(state="disabled", text="Running...", bg=FG2)
        threading.Thread(
            target=_run_pipeline,
            args=(csv, out, cfg, tag, bm, self.log, self._done),
            daemon=True,
        ).start()

    def _done(self, ok, dash_path):
        self._running = False
        def _u():
            self._run_btn.configure(state="normal", text="Run Pipeline", bg=GRN)
            if ok and dash_path and os.path.isfile(dash_path):
                self.log(f"\n Done -> {dash_path}")
                if messagebox.askyesno("Done",
                                       "Pipeline complete!\nOpen class analysis page in browser?"):
                    try:
                        webbrowser.open(f"file:///{Path(dash_path).as_posix()}")
                    except Exception:
                        os.startfile(dash_path)
            else:
                self.log("\n Pipeline failed. See log above.")
        self.after(0, _u)

    def _on_open(self):
        d = self._out_var.get().strip()
        if d and os.path.isdir(d):
            os.startfile(d)
        else:
            messagebox.showinfo("Not found", "Output folder does not exist yet.")

    def _on_clear(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")


# ---------------------------------------------------------------------------
# AboutFrame  — pipeline / module / product info panel
# ---------------------------------------------------------------------------

class AboutFrame(tk.Frame):
    """Static info panel: pipeline steps, module sources, product config."""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._build()

    def _build(self):
        tk.Label(self, text="About",
                 bg=BG, fg=ABLU, font=("Arial", 13, "bold")
                 ).pack(fill="x", padx=12, pady=(10, 4))

        def _lf(text, color=FG2):
            return tk.LabelFrame(self, text=text, bg=BG, fg=color,
                                 font=("Arial", 8, "bold"), padx=8, pady=6,
                                 relief="groove", bd=1)

        def _info(parent, label, value):
            row = tk.Frame(parent, bg=BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, width=24, anchor="w",
                     bg=BG, fg=FG2, font=("Arial", 9)).pack(side="left")
            tk.Label(row, text=value, anchor="w",
                     bg=BG, fg=FG, font=("Consolas", 9),
                     wraplength=400, justify="left").pack(
                side="left", fill="x", expand=True)

        # Pipeline steps
        pipe_frm = _lf("Pipeline  (6 steps)", ABLU)
        pipe_frm.pack(fill="x", padx=10, pady=(0, 6))
        for label, mod in [
            ("1  Material merge",  "add_material_type.py"),
            ("2  Reticle merge",   "apply_reticle_mapping.py"),
            ("3  Wafer heatmaps",  "generate_heatmap_from_csv.py  (no IBIN)"),
            ("4  CLASS analysis",  "generate_pcm_html.py  (via class_normalize)"),
            ("5  Master index",    "_Dashboard.html  (links all outputs)"),
        ]:
            _info(pipe_frm, label, mod)

        # Product config
        cfg_frm = _lf("Product Config", FG2)
        cfg_frm.pack(fill="x", padx=10, pady=(0, 6))
        from _constants import _DEFAULT_SETUP_DIR as _SETUP_DIR
        hits = glob.glob(
            os.path.join(_SETUP_DIR, "*-CLASS-ProductConfig*.json"))
        _info(cfg_frm, "Config JSON",
              hits[0] if hits else "Not found — browse in Pipeline tab")
        _info(cfg_frm, "DevRevStep", "NCXSDJ")
        _info(cfg_frm, "Product",    "NVL816  (CLASS / package test)")

        # Module sources
        src_frm = _lf("Module Sources", FG2)
        src_frm.pack(fill="x", padx=10, pady=(0, 6))
        for label, src in [
            ("add_material_type",    "yield-dashboard  (exact copy)"),
            ("apply_reticle_mapping","yield-dashboard  (exact copy)"),
            ("generate_heatmap...",  "yield-dashboard  (exact copy, no IBIN)"),
            ("generate_pcm_html",    "etest-dashboard  (exact copy)"),
            ("class_normalize",      "class-dashboard  (thin adapter)"),
        ]:
            _info(src_frm, label, src)

        # Status note
        note_frm = _lf("Status", FG2)
        note_frm.pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(note_frm,
                 text="Under development — excluded from deploy.",
                 bg=BG, fg="#e67e22", font=("Arial", 9),
                 wraplength=480, justify="left").pack(anchor="w")


# ---------------------------------------------------------------------------
# WaferPatternFrame  — standalone wafer heatmap runner
# ---------------------------------------------------------------------------

class WaferPatternFrame(tk.Frame):
    """Run wafer pattern heatmaps independently from the full pipeline.

    Uses generate_heatmap_from_csv.generate_heatmaps() directly.
    IBIN wafermap is disabled — class data does not carry sort-style IBINs.
    Coordinate columns (SORT_X_U1.U5 / SORT_Y_U1.U5) are auto-detected.
    """

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._out_var = tk.StringVar()
        self._running = False
        self._input_listbox = None  # created in _build
        self._build()

    def _build(self):
        def _lf(text, color=FG2):
            return tk.LabelFrame(self, text=text, bg=BG, fg=color,
                                 font=("Arial", 8, "bold"), padx=6, pady=4,
                                 relief="groove", bd=1)

        def _btn(parent, text, cmd, color=GRN, acolor="#2ecc71"):
            return tk.Button(parent, text=text, command=cmd,
                             bg=color, fg="white", activebackground=acolor,
                             relief="flat", cursor="hand2",
                             font=("Arial", 10, "bold"), padx=10, pady=4)

        # Section title
        tk.Label(self, text="Wafer Pattern Analysis",
                 bg=BG, fg=ABLU, font=("Arial", 13, "bold")
                 ).pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(self,
                 text="Generates per-lot/wafer die-position heatmaps.  "
                      "IBIN wafermap is not generated for CLASS data.",
                 bg=BG, fg=FG2, font=("Arial", 9),
                 wraplength=700, justify="left"
                 ).pack(fill="x", padx=12, pady=(0, 6))

        # ── Input files (Listbox) ─────────────────────────────────────────────
        frm_files = _lf("Input CSV / GZ / ZIP files", ABLU)
        frm_files.pack(fill="x", padx=10, pady=(0, 4))

        _lb_outer = tk.Frame(frm_files, bg=BG)
        _lb_outer.pack(fill="x", pady=(2, 0))
        _lb_scroll_y = tk.Scrollbar(_lb_outer, orient="vertical")
        _lb_scroll_x = tk.Scrollbar(_lb_outer, orient="horizontal")
        self._input_listbox = tk.Listbox(
            _lb_outer, height=4, selectmode="extended",
            bg=BG2, fg=FG, selectbackground="#1f618d", selectforeground="white",
            activestyle="none", font=("Consolas", 9), relief="flat",
            yscrollcommand=_lb_scroll_y.set,
            xscrollcommand=_lb_scroll_x.set)
        _lb_scroll_y.config(command=self._input_listbox.yview)
        _lb_scroll_x.config(command=self._input_listbox.xview)
        _lb_scroll_y.pack(side="right", fill="y")
        _lb_scroll_x.pack(side="bottom", fill="x")
        self._input_listbox.pack(side="left", fill="both", expand=True)

        _lb_btn_row = tk.Frame(frm_files, bg=BG)
        _lb_btn_row.pack(fill="x", pady=(4, 0))
        _btn(_lb_btn_row, "  Add File(s)  ", self._add_input_files,
             color="#1f618d", acolor=ABLU).pack(side="left", padx=(0, 4))
        _btn(_lb_btn_row, "  Remove Selected  ", self._remove_input_files,
             color="#7b241c", acolor="#a93226").pack(side="left")
        tk.Label(_lb_btn_row, text="Tip: Add from multiple folders freely.",
                 bg=BG, fg=FG2, font=("Arial", 8)).pack(side="left", padx=(8, 0))

        # ── Output folder ─────────────────────────────────────────────────────
        frm_in = _lf("Settings", ABLU)
        frm_in.pack(fill="x", padx=10, pady=(0, 4))
        frm_in.columnconfigure(1, weight=1)
        tk.Label(frm_in, text="Output folder", width=20, anchor="w",
                 bg=BG, fg=FG, font=("Arial", 9)).grid(row=0, column=0, sticky="w", pady=3, padx=(0,4))
        tk.Entry(frm_in, textvariable=self._out_var,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", font=("Consolas", 9)).grid(row=0, column=1, sticky="ew", pady=3, padx=(0,4))
        tk.Button(frm_in, text="...", command=self._browse_out, width=3,
                  bg="#1f618d", fg="white", activebackground=ABLU,
                  relief="flat", cursor="hand2").grid(row=0, column=2, pady=3)

        # Info note — column auto-detection
        note_frm = _lf("Column Detection", FG2)
        note_frm.pack(fill="x", padx=10, pady=(0, 4))
        tk.Label(note_frm,
                 text="X/Y coordinates, Lot, and Wafer columns are auto-detected from the CSV header.\n"
                      "For CLASS data: SORT_X_U1.U5, SORT_Y_U1.U5, SORT_LOT_U1.U5, SORT_WAFER_U1.U5",
                 bg=BG, fg=FG2, font=("Arial", 9),
                 justify="left").pack(anchor="w")

        # ── Run button ────────────────────────────────────────────────────────
        self._run_btn = _btn(self, "Generate Wafer Heatmaps", self._on_run)
        self._run_btn.pack(fill="x", padx=10, pady=(4, 4))

        # ── Log ───────────────────────────────────────────────────────────────
        log_frm = _lf("Log", FG2)
        log_frm.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self._log_box = scrolledtext.ScrolledText(
            log_frm, wrap="word",
            font=("Consolas", 9), bg="#0d1b26", fg="#a8d8ea",
            relief="flat", insertbackground=FG, state="disabled")
        self._log_box.pack(fill="both", expand=True)

    # ── browse ────────────────────────────────────────────────────────────────

    def _add_input_files(self):
        existing = set(self._input_listbox.get(0, tk.END))
        last = self._input_listbox.get(tk.END)
        init_dir = str(Path(last).parent) if last else None
        paths = filedialog.askopenfilenames(
            title="Add CLASS CSV / GZ / ZIP file(s)",
            initialdir=init_dir,
            filetypes=[("CSV / GZ / ZIP files", "*.csv *.csv.gz *.gz *.zip"), ("All", "*.*")])
        for p in paths:
            if p and p not in existing:
                self._input_listbox.insert(tk.END, p)
                existing.add(p)
        if paths and not self._out_var.get():
            self._out_var.set(str(Path(self._input_listbox.get(0)).parent / "wafer_heatmaps"))

    def _remove_input_files(self):
        for i in reversed(self._input_listbox.curselection()):
            self._input_listbox.delete(i)

    def _browse_out(self):
        p = filedialog.askdirectory(title="Select output folder")
        if p:
            self._out_var.set(p)

    # ── log helper ────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        def _u():
            self._log_box.configure(state="normal")
            self._log_box.insert("end", msg.rstrip("\n") + "\n")
            self._log_box.see("end")
        self.after(0, _u)

    # ── run ───────────────────────────────────────────────────────────────────

    def _on_run(self):
        if self._running:
            messagebox.showinfo("Running", "Already running."); return
        csv_list = list(self._input_listbox.get(0, tk.END))
        out = self._out_var.get().strip()
        if not csv_list:
            messagebox.showerror("Missing input", "Add at least one input CSV."); return
        for p in csv_list:
            if not os.path.isfile(p):
                messagebox.showerror("Missing input", f"File not found:\n{p}"); return
        if not out:
            messagebox.showerror("Missing output", "Select an output folder."); return
        os.makedirs(out, exist_ok=True)
        csv_log = "".join(f"  CSV: {p}\n" for p in csv_list)
        self._log(f"[RUN] Wafer heatmaps\n{csv_log}  Out: {out}\n")
        self._running = True
        self._run_btn.configure(state="disabled", text="Working...", bg=FG2)
        csv = csv_list if len(csv_list) > 1 else csv_list[0]
        threading.Thread(target=self._worker, args=(csv, out), daemon=True).start()

    def _worker(self, csv_path, out_dir):
        try:
            if _SRC_DIR not in sys.path:
                sys.path.insert(0, _SRC_DIR)
            from generate_heatmap_from_csv import generate_heatmaps
            # Support multiple input files — concatenate before heatmap generation
            if isinstance(csv_path, (list, tuple)):
                import pandas as _pd
                import tempfile, shutil
                _tmp = Path(tempfile.mkdtemp(prefix="wafer_dash_"))
                try:
                    self._log(f"Concatenating {len(csv_path)} input files ...")
                    _dfs = [_pd.read_csv(p, low_memory=False) for p in csv_path]
                    _combined = _pd.concat(_dfs, ignore_index=True)
                    _cpath = str(_tmp / "_combined_input.csv")
                    _combined.to_csv(_cpath, index=False)
                    self._log(f"  Combined: {len(_combined)} rows")
                    generate_heatmaps(
                        _cpath,
                        out_dir              = out_dir,
                        render_ibin_wafermap = False,
                    )
                finally:
                    shutil.rmtree(str(_tmp), ignore_errors=True)
            else:
                generate_heatmaps(
                    csv_path,
                    out_dir              = out_dir,
                    render_ibin_wafermap = False,
                )
            self._log(f"\n Done -> {out_dir}")
            self.after(0, lambda: os.startfile(out_dir))
        except Exception as exc:
            import traceback
            self._log(f"\nERROR: {exc}\n{traceback.format_exc()}")
        finally:
            self.after(0, lambda: self._run_btn.configure(
                state="normal", text="Generate Wafer Heatmaps", bg=GRN))
            self._running = False

    def populate_csv(self, csv_path):
        """Pre-fill the file list (called from pipeline after run)."""
        paths = list(csv_path) if isinstance(csv_path, (list, tuple)) else [csv_path]
        self._input_listbox.delete(0, tk.END)
        for p in paths:
            self._input_listbox.insert(tk.END, p)
        if paths and not self._out_var.get():
            self._out_var.set(str(Path(paths[0]).parent / "wafer_heatmaps"))


# ---------------------------------------------------------------------------
# Sidebar nav items
# ---------------------------------------------------------------------------

NAV_ITEMS = [
    ("Pipeline",      "pipeline"),
]


# ---------------------------------------------------------------------------
# ClassDashboardApp  — sidebar + content area
# ---------------------------------------------------------------------------

class ClassDashboardApp(tk.Tk):
    """Main window: fixed sidebar (left) + content frames (right)."""

    def __init__(self, initial_csv=None):
        super().__init__()
        self.title("CLASS Dashboard")
        self.geometry("1100x740")
        self.minsize(840, 600)
        self.configure(bg=BG)
        self._frames   = {}
        self._nav_btns = {}
        self._active   = None

        self._build_layout()
        self._build_sidebar()
        self._build_content()
        self._show("pipeline")

        if initial_csv:
            pf = self._frames["pipeline"]
            paths = list(initial_csv) if isinstance(initial_csv, (list, tuple)) else [initial_csv]
            for p in paths:
                pf._input_listbox.insert(tk.END, p)
            if paths:
                pf._auto_fill_tag(paths[0])

    # ── layout ───────────────────────────────────────────────────────────────

    def _build_layout(self):
        # Top bar — spans full width; watermark right-aligned
        _topbar = tk.Frame(self, bg=BG, height=22)
        _topbar.pack(side="top", fill="x")
        _topbar.pack_propagate(False)
        tk.Label(
            _topbar,
            text="Pant, Sujit N \u2014 Subramaniam, Sangkeetha \u2014 GEMS FTE",
            bg=BG, fg="#e67e22",
            font=("Arial", 7),
        ).pack(side="right", padx=10)

        # 1 px horizontal separator below top bar
        tk.Frame(self, bg="#0a1520", height=1).pack(side="top", fill="x")

        # Main row: sidebar + content
        _main = tk.Frame(self, bg=BG)
        _main.pack(side="top", fill="both", expand=True)

        self._sidebar = tk.Frame(_main, bg=BG3, width=170)
        self._sidebar.pack_forget()  # hidden — only Pipeline view exists

        self._content = tk.Frame(_main, bg=BG)
        self._content.pack(side="left", fill="both", expand=True)

    def _build_sidebar(self):
        # App title block
        hdr = tk.Frame(self._sidebar, bg=BG3, pady=16)
        hdr.pack(fill="x")
        tk.Label(hdr, text="CLASS", bg=BG3, fg=GRN,
                 font=("Arial", 16, "bold")).pack()
        tk.Label(hdr, text="Dashboard", bg=BG3, fg=FG2,
                 font=("Arial", 9)).pack()

        # Divider
        tk.Frame(self._sidebar, bg="#0a1520", height=1).pack(
            fill="x", padx=8, pady=(0, 6))

        # Nav buttons
        for label, key in NAV_ITEMS:
            btn = tk.Button(
                self._sidebar, text=label,
                command=lambda k=key: self._show(k),
                bg=BG3, fg=FG2,
                activebackground=BG2, activeforeground=FG,
                relief="flat", anchor="w",
                padx=18, pady=8,
                font=("Arial", 10),
                cursor="hand2", bd=0,
            )
            btn.pack(fill="x")
            self._nav_btns[key] = btn

        # Spacer fills remaining sidebar space
        tk.Frame(self._sidebar, bg=BG3).pack(fill="both", expand=True)

    def _build_content(self):
        self._frames["pipeline"] = PipelineFrame(self._content)
        for frame in self._frames.values():
            frame.place(relx=0, rely=0, relwidth=1, relheight=1)

    # ── navigation ───────────────────────────────────────────────────────────

    def _show(self, key: str):
        if self._active == key:
            return
        # Deactivate old button
        if self._active and self._active in self._nav_btns:
            self._nav_btns[self._active].configure(bg=BG3, fg=FG2)
        # Activate new button + raise frame
        self._active = key
        if key in self._nav_btns:
            self._nav_btns[key].configure(bg=BG2, fg=FG)
        if key in self._frames:
            self._frames[key].lift()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="CLASS Dashboard — GUI launcher or headless pipeline runner",
        add_help=True,
    )
    ap.add_argument("csv",          nargs="*",            default=[],    help="Input CLASS CSV / GZ / ZIP file(s) — one or more")
    ap.add_argument("--out",        metavar="DIR",        default=None,  help="Output directory (default: <csv_dir>/output)")
    ap.add_argument("--cfg",        metavar="JSON",       default=None,  help="Product config JSON (default: auto-detect from shared/setup)")
    ap.add_argument("--bm",         metavar="FILE",       default=None,  help="Bin Matrix CSV/Excel file (default: auto-detect from shared/setup)")
    ap.add_argument("--tag",        metavar="TAG",        default=None,  help="Output sub-folder / file tag (default: CSV stem)")
    ap.add_argument("--no-tag-subdir", action="store_true",             help="Write files directly to --out without creating a tag-named subfolder")
    ap.add_argument("--headless",   action="store_true",                 help="Run pipeline without GUI and exit")
    args, _ = ap.parse_known_args()

    if args.headless:
        import sys
        if not args.csv:
            ap.error("at least one csv path is required in --headless mode")

        from _constants import _DEFAULT_SETUP_DIR

        csv_paths = args.csv
        csv_path  = csv_paths if len(csv_paths) > 1 else csv_paths[0]
        out_dir   = args.out  or str(Path(csv_paths[0]).parent / "output")
        tag       = args.tag  or Path(csv_paths[0]).stem
        cfg_path  = args.cfg  or next(iter(
            glob.glob(os.path.join(_DEFAULT_SETUP_DIR, "*-CLASS-ProductConfig*.json"))
        ), None)
        bm_path   = args.bm   or os.path.join(_DEFAULT_SETUP_DIR, "NVL_S28C_S28CB_W25_BM.xlsx")
        if not os.path.isfile(bm_path):
            bm_path = None
        if not cfg_path:
            sys.exit(f"No product config found in {_DEFAULT_SETUP_DIR} — use --cfg")

        def _log(msg):    print(msg, flush=True)
        def _done(ok, p): sys.exit(0 if ok else 1)

        _run_pipeline(csv_path, out_dir, cfg_path, tag, bm_path, _log, _done, use_tag_subdir=not args.no_tag_subdir)
    else:
        ClassDashboardApp(initial_csv=args.csv or None).mainloop()
