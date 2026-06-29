"""trend_chart_frame.py — GUI front-end for trend_chart.py.

Layout
------
Left panel  (~280 px)  — all controls + log
Right panel (expands)  — live summary stats + run table; updates after load/filter
"""

import os
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).parent))
import trend_chart as tc

# ── Palette (matches dashboard theme) ────────────────────────────────────────
BG    = '#1a252f'
BG2   = '#2c3e50'
BG3   = '#243342'   # slightly lighter — right-panel card bg
FG    = '#ecf0f1'
FG2   = '#95a5a6'
BLUE  = '#2980b9'
ABLU  = '#3498db'
GRN   = '#27ae60'
AGRN  = '#2ecc71'
WARN  = '#f39c12'
RED   = '#c0392b'


def _btn(parent, text, cmd, color=BLUE, acolor=ABLU, width=None):
    kw = dict(text=text, command=cmd, bg=color, fg='white',
              activebackground=acolor, activeforeground='white',
              relief='flat', cursor='hand2', font=('Arial', 9),
              padx=8, pady=3)
    if width:
        kw['width'] = width
    return tk.Button(parent, **kw)


def _lf(parent, text, label_color=FG2):
    return tk.LabelFrame(parent, text=text, bg=BG, fg=label_color,
                         font=('Arial', 8, 'bold'), padx=6, pady=4,
                         relief='groove', bd=1)


def _sep(parent):
    """Thin horizontal divider."""
    tk.Frame(parent, bg=BG2, height=1).pack(fill='x', padx=6, pady=4)


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
class TrendChartFrame(tk.Frame):
    """Simple GUI: pick CSV + config, set interval/top-n, generate interactive HTML."""

    def __init__(self, parent=None, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._csv_var     = tk.StringVar()
        self._cfg_var     = tk.StringVar()
        self._out_var     = tk.StringVar()
        self._last_report = ''
        self._build_ui()

    def _build_ui(self):
        tk.Label(self, text='iBin Fail vs. Yield Trend',
                 bg=BG, fg=ABLU, font=('Arial', 13, 'bold')
                 ).pack(fill='x', padx=12, pady=(10, 2))
        tk.Label(self,
                 text='Generates a self-contained interactive HTML report.\n'
                      'Filter by program, bin, and interval directly in the browser.',
                 bg=BG, fg=FG2, font=('Arial', 9), justify='left'
                 ).pack(fill='x', padx=12)
        _sep(self)

        # CSV
        tk.Label(self, text='Input File (.csv / .zip / .gz)',
                 bg=BG, fg=FG, font=('Arial', 9, 'bold')
                 ).pack(fill='x', padx=12)
        fr = tk.Frame(self, bg=BG)
        fr.pack(fill='x', padx=12, pady=(2, 6))
        tk.Entry(fr, textvariable=self._csv_var,
                 bg=BG2, fg=FG, insertbackground=FG, relief='flat',
                 font=('Consolas', 9)
                 ).pack(side='left', fill='x', expand=True, padx=(0, 4))
        _btn(fr, 'Browse...', self._browse_csv, width=8).pack(side='left')

        # Product config
        tk.Label(self, text='Product Config JSON (optional)',
                 bg=BG, fg=FG, font=('Arial', 9, 'bold')
                 ).pack(fill='x', padx=12)
        fr2 = tk.Frame(self, bg=BG)
        fr2.pack(fill='x', padx=12, pady=(2, 6))
        tk.Entry(fr2, textvariable=self._cfg_var,
                 bg=BG2, fg=FG, insertbackground=FG, relief='flat',
                 font=('Consolas', 9)
                 ).pack(side='left', fill='x', expand=True, padx=(0, 4))
        _btn(fr2, 'Browse...', self._browse_cfg, color='#1f618d', width=8).pack(side='left', padx=(0, 3))
        _btn(fr2, 'X', lambda: self._cfg_var.set(''), color='#555', width=2).pack(side='left')

        _sep(self)

        # Output path
        tk.Label(self, text='Output HTML', bg=BG, fg=FG,
                 font=('Arial', 9, 'bold')).pack(fill='x', padx=12, pady=(8, 0))
        fr3 = tk.Frame(self, bg=BG)
        fr3.pack(fill='x', padx=12, pady=(2, 6))
        tk.Entry(fr3, textvariable=self._out_var,
                 bg=BG2, fg=FG, insertbackground=FG, relief='flat',
                 font=('Consolas', 9)
                 ).pack(side='left', fill='x', expand=True, padx=(0, 4))
        _btn(fr3, '...', self._browse_out, width=3).pack(side='left')

        _sep(self)

        _sep(self)

        # Buttons
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill='x', padx=12)
        self._run_btn = _btn(btn_row, 'Generate Interactive HTML',
                             self._generate, color=GRN, acolor=AGRN)
        self._run_btn.config(font=('Arial', 10, 'bold'), pady=6)
        self._run_btn.pack(side='left', fill='x', expand=True, padx=(0, 4))
        self._open_btn = _btn(btn_row, 'Open', self._open_report,
                              color='#935116', acolor='#ca6f1e')
        self._open_btn.config(state='disabled', pady=6)
        self._open_btn.pack(side='left')

        # Log
        _sep(self)
        tk.Label(self, text='Log', bg=BG, fg=FG2,
                 font=('Arial', 8, 'bold')).pack(fill='x', padx=12)
        log_frm = tk.Frame(self, bg='#0d1b26')
        log_frm.pack(fill='both', expand=True, padx=12, pady=(2, 10))
        self._log = tk.Text(log_frm, state='disabled',
                            font=('Consolas', 8), bg='#0d1b26', fg='#a8d8ea',
                            relief='flat', wrap='word')
        sb = tk.Scrollbar(log_frm, command=self._log.yview, bg=BG)
        self._log.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self._log.pack(fill='both', expand=True)

    def _browse_csv(self):
        p = filedialog.askopenfilename(
            title='Select input CSV / ZIP / GZ',
            filetypes=[('Supported files', '*.csv *.zip *.gz *.gzip'),
                       ('All files', '*.*')])
        if not p:
            return
        self._csv_var.set(p)
        self._out_var.set(str(Path(p).parent / (Path(p).stem + '_trend.html')))
        if not self._cfg_var.get().strip():
            try:
                preview = tc.load_csv(Path(p))
                drs = preview[0].get('devrevstep', '') if preview else ''
            except Exception:
                drs = ''
            auto = tc._find_auto_config(drs)
            if auto:
                self._cfg_var.set(str(auto))
                self._log_write(f'Auto-detected config: {auto.name}\n')

    def _browse_cfg(self):
        p = filedialog.askopenfilename(
            title='Select product config JSON',
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')])
        if p:
            self._cfg_var.set(p)

    def _browse_out(self):
        p = filedialog.asksaveasfilename(
            title='Save report as', defaultextension='.html',
            filetypes=[('HTML files', '*.html')])
        if p:
            self._out_var.set(p)

    def _open_report(self):
        if self._last_report and os.path.isfile(self._last_report):
            try:
                os.startfile(self._last_report)
            except Exception as e:
                messagebox.showerror('Error', str(e))

    def _log_write(self, msg: str):
        def _do():
            self._log.configure(state='normal')
            self._log.insert('end', msg)
            self._log.see('end')
            self._log.configure(state='disabled')
        try:
            self.after(0, _do)
        except Exception:
            pass

    def _generate(self):
        csv_str = self._csv_var.get().strip()
        if not csv_str or not os.path.isfile(csv_str):
            messagebox.showwarning('No CSV', 'Browse and select a CSV file first.')
            return

        csv_path = Path(csv_str)
        out_str  = self._out_var.get().strip()
        out_path = Path(out_str) if out_str else csv_path.parent / (csv_path.stem + '_trend.html')
        interval = 'revision'
        top_n    = 8
        thresh   = 0.0
        cfg_path = self._cfg_var.get().strip()

        self._run_btn.configure(state='disabled', text='Working...', bg=FG2)
        self._log_write(f'Loading {csv_path.name}...\n')

        def _worker():
            try:
                runs = tc.load_csv(csv_path, log=self._log_write)
                self._log_write(f'Loaded {len(runs)} run(s). Building charts...\n')

                cfg = None
                if cfg_path and Path(cfg_path).exists():
                    cfg = tc.load_product_config(cfg_path)
                    self._log_write(f'Config: {Path(cfg_path).name}\n')
                else:
                    drs = runs[0].get('devrevstep', '') if runs else ''
                    auto = tc._find_auto_config(drs)
                    if auto:
                        cfg = tc.load_product_config(auto)
                        self._log_write(f'Config (auto): {auto.name}\n')

                groups    = tc.group_runs(runs, interval)
                trend_fig = tc.build_trend_chart(
                    groups, top_n_fail_ibins=top_n,
                    fail_thresh_pct=thresh, interval=interval, cfg=cfg)
                pareto_fig     = tc.build_pareto_chart(runs, top_n=20, cfg=cfg)
                pareto_vert_fig, pareto_tbl = tc.build_pareto_vertical_chart(runs, top_n=20, cfg=cfg)

                tc.generate_html(csv_path, groups, runs, trend_fig, pareto_fig,
                                 out_path, interval=interval, top_n=top_n,
                                 cfg_path=cfg_path, cfg=cfg,
                                 pareto_vertical_fig=pareto_vert_fig,
                                 pareto_table_rows=pareto_tbl)
                self._last_report = str(out_path)
                self._log_write(f'Done -> {out_path}\n')
                try:
                    os.startfile(str(out_path))
                except Exception:
                    pass

                def _done():
                    self._open_btn.configure(state='normal')
                self.after(0, _done)

            except Exception as exc:
                import traceback
                self._log_write(f'ERROR: {exc}\n{traceback.format_exc()}\n')
            finally:
                def _re():
                    self._run_btn.configure(state='normal',
                                            text='Generate Interactive HTML',
                                            bg=GRN)
                self.after(0, _re)

        threading.Thread(target=_worker, daemon=True).start()


if __name__ == '__main__':
    root = tk.Tk()
    root.title('Trend Chart - Debug')
    root.configure(bg='#1a252f')
    root.geometry('820x560')
    frame = TrendChartFrame(root)
    frame.pack(fill='both', expand=True)
    root.mainloop()
