"""
manage_email.py  —  GUI to configure email recipients and TP program filter.

Usage:
    python manage_email.py
    python manage_email.py --base-dir "\\\\server\\share\\auto"

Config is saved to:  <base-dir>/email_config.json

Schema:
    {
        "email_to_report": "a@b.com; c@d.com",   # final report recipients
        "email_to_alert":  "a@b.com",             # failure/error alert recipients
        "excluded_keys":   ["NCXSDJXL0H61A002618_119325", ...]
    }

run_automation.py reads this file automatically:
  - Report email  → email_to_report
  - Failure alert → email_to_alert
Excluded keys are still run by the pipeline but omitted from the email report.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

# ── defaults (same as run_automation.py) ──────────────────────────────────────
_HERE        = Path(__file__).resolve().parent
_REPO_ROOT   = _HERE.parent.parent.parent.parent   # app.yield.nvl/
_BASE_DIR    = Path(r"\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\yield")
_CFG_NAME    = "email_config.json"
_CFG_DIR     = _REPO_ROOT / "shared" / "setup" / "automation" / "yield-dashboard"
_EMAIL_TO    = "sujit.n.pant@intel.com"

# ── colours ───────────────────────────────────────────────────────────────────
BG          = "#1a252f"
BG2         = "#1e2e3d"
BG3         = "#263950"
FG          = "#e8f0f7"
FG_DIM      = "#90a4ae"
ACCENT      = "#4fc3f7"
GREEN       = "#66bb6a"
RED         = "#ef5350"
AMBER       = "#ffa726"
FONT_MONO   = ("Courier New", 9)
FONT_UI     = ("Segoe UI", 10)
FONT_TITLE  = ("Segoe UI", 13, "bold")
FONT_GROUP  = ("Segoe UI", 10, "bold")


def _load_config(cfg_path: Path) -> dict:
    if cfg_path.exists():
        try:
            d = json.loads(cfg_path.read_text(encoding="utf-8"))
            # migrate old single-field format
            if "email_to" in d and "email_to_report" not in d:
                d["email_to_report"] = d.pop("email_to")
            return d
        except Exception:
            pass
    return {"email_to_report": _EMAIL_TO, "email_to_alert": _EMAIL_TO, "excluded_keys": []}


def _save_config(cfg_path: Path, cfg: dict) -> None:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _discover_keys(base_dir: Path) -> list[str]:
    """Return all known TP keys from stored gzs."""
    prog_dir = base_dir / "data" / "programs"
    if not prog_dir.exists():
        return []
    keys = []
    for p in sorted(prog_dir.glob("*.csv.gz")):
        stem = p.name
        if stem.endswith(".csv.gz"):
            stem = stem[:-7]
        elif stem.endswith(".gz"):
            stem = stem[:-3]
        keys.append(stem)
    return sorted(keys)


def _group_keys(keys: list[str]) -> dict[str, list[str]]:
    """Group keys by program letter (61A, 61B, 61C …)."""
    groups: dict[str, list[str]] = {}
    for k in keys:
        m = re.search(r'61([A-Za-z])', k)
        letter = m.group(1).upper() if m else "?"
        groups.setdefault(letter, []).append(k)
    # Sort: letter descending (61C first), keys ascending within group
    return dict(sorted(groups.items(), reverse=True))


# ─────────────────────────────────────────────────────────────────────────────
# Main GUI
# ─────────────────────────────────────────────────────────────────────────────

class EmailManagerApp(tk.Tk):
    def __init__(self, base_dir: Path) -> None:
        super().__init__()
        self.base_dir   = base_dir
        self.cfg_path   = _CFG_DIR / _CFG_NAME
        self.cfg        = _load_config(self.cfg_path)
        self.excluded   = set(self.cfg.get("excluded_keys", []))

        self.title("Yield Automation — Email Manager")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(560, 420)

        self._build_ui()
        self._populate()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        pad = dict(padx=14, pady=6)

        # Title
        tk.Label(self, text="Email Report Manager", font=FONT_TITLE,
                 bg=BG, fg=ACCENT).pack(anchor="w", padx=14, pady=(14, 2))
        tk.Label(self, text=f"Config: {self.cfg_path}", font=("Segoe UI", 8),
                 bg=BG, fg=FG_DIM).pack(anchor="w", padx=14, pady=(0, 8))

        # ── Email recipients ──────────────────────────────────────────────────
        frm_email = tk.LabelFrame(self, text="  Recipients  ", font=FONT_UI,
                                  bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_email.pack(fill="x", **pad)

        def _email_row(parent, row, label, var, color, note):
            tk.Label(parent, text=label, font=FONT_UI, bg=BG, fg=color
                     ).grid(row=row, column=0, sticky="w", padx=8, pady=4)
            tk.Entry(parent, textvariable=var, font=FONT_UI,
                     bg=BG2, fg=FG, insertbackground=FG, relief="flat",
                     width=50).grid(row=row, column=1, padx=8, pady=4, sticky="ew")
            tk.Label(parent, text=note, font=("Segoe UI", 7), bg=BG, fg=FG_DIM
                     ).grid(row=row, column=2, sticky="w", padx=(0, 8))

        self.report_email_var = tk.StringVar(
            value=self.cfg.get("email_to_report", _EMAIL_TO))
        self.alert_email_var  = tk.StringVar(
            value=self.cfg.get("email_to_alert",  self.cfg.get("email_to_report", _EMAIL_TO)))

        _email_row(frm_email, 0, "Report To:",  self.report_email_var,
                   GREEN,  "Final report with BinDist (semicolons OK)")
        _email_row(frm_email, 1, "Alerts To:",  self.alert_email_var,
                   AMBER,  "AQUA errors, pipeline failures")
        frm_email.columnconfigure(1, weight=1)

        # ── Program filter ────────────────────────────────────────────────────
        frm_prog = tk.LabelFrame(self, text="  Program Filter  ", font=FONT_UI,
                                 bg=BG, fg=ACCENT, bd=1, relief="groove")
        frm_prog.pack(fill="both", expand=True, **pad)

        tk.Label(frm_prog,
                 text="Unchecked programs are excluded from the email report (pipeline still runs).",
                 font=("Segoe UI", 8), bg=BG, fg=FG_DIM
                 ).pack(anchor="w", padx=8, pady=(4, 0))

        # Toolbar: select-all / deselect-all
        tb = tk.Frame(frm_prog, bg=BG)
        tb.pack(fill="x", padx=8, pady=(2, 0))
        self._btn(tb, "✔ All", self._select_all).pack(side="left", padx=(0, 6))
        self._btn(tb, "✘ None", self._deselect_all).pack(side="left", padx=(0, 6))
        self._btn(tb, "↺ Refresh", self._populate).pack(side="left")

        # Scrollable canvas for groups
        canvas_frame = tk.Frame(frm_prog, bg=BG)
        canvas_frame.pack(fill="both", expand=True, padx=8, pady=6)

        self.canvas = tk.Canvas(canvas_frame, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(canvas_frame, orient="vertical",
                            command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.canvas, bg=BG)
        self._canvas_win = self.canvas.create_window((0, 0), window=self.inner,
                                                      anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)

        # ── Status / buttons ──────────────────────────────────────────────────
        bot = tk.Frame(self, bg=BG)
        bot.pack(fill="x", padx=14, pady=(0, 12))

        self.status_var = tk.StringVar(value="")
        tk.Label(bot, textvariable=self.status_var, font=("Segoe UI", 9),
                 bg=BG, fg=GREEN).pack(side="left")

        self._btn(bot, "Cancel", self.destroy, fg=FG_DIM).pack(side="right", padx=(6, 0))
        self._btn(bot, "Save", self._save, bg="#1b5e20", fg="#c8e6c9").pack(side="right")

        self.check_vars: dict[str, tk.BooleanVar] = {}

    def _btn(self, parent, text, cmd, bg=BG3, fg=FG, **kw):
        return tk.Button(parent, text=text, command=cmd,
                         font=FONT_UI, bg=bg, fg=fg,
                         activebackground=ACCENT, activeforeground=BG,
                         relief="flat", padx=10, pady=3, cursor="hand2", **kw)

    # ── Populate groups ───────────────────────────────────────────────────────

    def _populate(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self.check_vars.clear()

        keys   = _discover_keys(self.base_dir)
        groups = _group_keys(keys)

        if not keys:
            tk.Label(self.inner,
                     text="No TP keys found in data/programs/.\nRun automation first.",
                     font=FONT_UI, bg=BG, fg=FG_DIM
                     ).pack(padx=12, pady=20)
            return

        for letter, tp_keys in groups.items():
            prog_name = f"0H61{letter}"

            # Group header
            hdr = tk.Frame(self.inner, bg=BG3)
            hdr.pack(fill="x", pady=(8, 0))
            tk.Label(hdr, text=f"  {prog_name}", font=FONT_GROUP,
                     bg=BG3, fg=ACCENT).pack(side="left", padx=6, pady=4)
            n_excl = sum(1 for k in tp_keys if k in self.excluded)
            if n_excl:
                tk.Label(hdr, text=f"{n_excl} excluded", font=("Segoe UI", 8),
                         bg=BG3, fg=AMBER).pack(side="right", padx=8)

            # One row per TP key
            grp_frame = tk.Frame(self.inner, bg=BG2, bd=0)
            grp_frame.pack(fill="x", pady=(0, 2))

            for tp_key in tp_keys:
                included = tp_key not in self.excluded
                var = tk.BooleanVar(value=included)
                self.check_vars[tp_key] = var

                row = tk.Frame(grp_frame, bg=BG2)
                row.pack(fill="x", padx=4, pady=1)

                cb = tk.Checkbutton(
                    row, variable=var, bg=BG2, fg=FG,
                    activebackground=BG2, activeforeground=ACCENT,
                    selectcolor=BG3, relief="flat", cursor="hand2",
                    command=lambda k=tp_key, v=var: self._on_toggle(k, v),
                )
                cb.pack(side="left")

                # Extract op number for display
                m_op  = re.search(r'_(\d{5,6})$', tp_key)
                op_lbl = f"op {m_op.group(1)}" if m_op else ""

                tk.Label(row, text=tp_key, font=FONT_MONO,
                         bg=BG2, fg=FG if included else FG_DIM).pack(side="left", padx=(2, 10))
                tk.Label(row, text=op_lbl, font=("Segoe UI", 8),
                         bg=BG2, fg=FG_DIM).pack(side="left")

                state_lbl = tk.Label(row,
                                     text="included" if included else "EXCLUDED",
                                     font=("Segoe UI", 8),
                                     bg=BG2, fg=GREEN if included else RED)
                state_lbl.pack(side="right", padx=8)

                # Keep reference to update on toggle
                var._label     = state_lbl   # type: ignore[attr-defined]
                var._key_label = row.winfo_children()[1]   # type: ignore[attr-defined]

        self.inner.update_idletasks()

    def _on_toggle(self, key: str, var: tk.BooleanVar) -> None:
        included = var.get()
        if included:
            self.excluded.discard(key)
        else:
            self.excluded.add(key)
        try:
            var._label.config(     # type: ignore[attr-defined]
                text="included" if included else "EXCLUDED",
                fg=GREEN if included else RED)
            var._key_label.config(fg=FG if included else FG_DIM)  # type: ignore[attr-defined]
        except Exception:
            pass

    def _select_all(self) -> None:
        self.excluded.clear()
        for k, v in self.check_vars.items():
            v.set(True)
            self._on_toggle(k, v)

    def _deselect_all(self) -> None:
        for k, v in self.check_vars.items():
            v.set(False)
            self._on_toggle(k, v)

    # ── Scrolling ─────────────────────────────────────────────────────────────

    def _on_inner_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self._canvas_win, width=event.width)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ── Save ──────────────────────────────────────────────────────────────────

    def _save(self) -> None:
        report_to = self.report_email_var.get().strip()
        alert_to  = self.alert_email_var.get().strip()
        if not report_to:
            messagebox.showerror("Error", "Report recipient cannot be empty.")
            return
        if not alert_to:
            alert_to = report_to  # fall back to report list

        cfg = {
            "email_to_report": report_to,
            "email_to_alert":  alert_to,
            "excluded_keys":   sorted(self.excluded),
        }
        try:
            _save_config(self.cfg_path, cfg)
            self.cfg = cfg
            n = len(self.excluded)
            self.status_var.set(
                f"Saved — {n} key(s) excluded." if n else "Saved — all keys included."
            )
            self._populate()
        except Exception as e:
            messagebox.showerror("Save failed", str(e))


# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Manage email report filter config.")
    ap.add_argument("--base-dir", default=str(_BASE_DIR),
                    help="Base auto directory (for TP key discovery via data/programs/)")
    args = ap.parse_args()

    base_dir = Path(args.base_dir)
    app = EmailManagerApp(base_dir)
    app.mainloop()


if __name__ == "__main__":
    main()
