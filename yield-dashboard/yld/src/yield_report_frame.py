"""yield_report_frame.py — GUI front-end for yield_report.py (weekly pareto)."""

import os
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox

sys.path.insert(0, str(Path(__file__).parent))
import yield_report as yr

# ── Palette ──────────────────────────────────────────────────────────────────
BG   = '#1a252f'
BG2  = '#2c3e50'
FG   = '#ecf0f1'
FG2  = '#95a5a6'
BLUE = '#2980b9'
ABLU = '#3498db'
GRN  = '#27ae60'
AGRN = '#2ecc71'


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


# ---------------------------------------------------------------------------
# ReportFrame
# ---------------------------------------------------------------------------

class ReportFrame(tk.Frame):
    def __init__(self, parent=None, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._dash_path   = tk.StringVar()
        self._cfg_var     = tk.StringVar()
        self._out_var     = tk.StringVar()
        self._weeks_var   = tk.StringVar(value='0')
        self._interval_var = tk.StringVar(value='weekly')
        self._last_report = ''
        self._build_ui()

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        P = {'padx': 10, 'pady': 4}

        # Title
        tk.Label(self, text='Yield Pareto Report',
                 bg=BG, fg=ABLU, font=('Arial', 13, 'bold')
                 ).pack(fill='x', padx=10, pady=(8, 2))
        tk.Label(self,
                 text='Groups all runs by the chosen interval and generates a bin-fail pareto per period.',
                 bg=BG, fg=FG2, font=('Arial', 8)
                 ).pack(fill='x', padx=10, pady=(0, 4))

        # Step 1 — Input CSV / ZIP / GZ
        frm1 = _lf(self, 'Step 1 — Input CSV / ZIP / GZ', ABLU)
        frm1.pack(fill='x', **P)
        tk.Label(frm1, text='Lot, Wafer, Program Name, Interface Bin, Count, Total Dies  —  Accepts .csv, .zip, .gz',
                 bg=BG, fg=FG2, font=('Arial', 7)).pack(anchor='w', pady=(0, 2))
        entry_row = tk.Frame(frm1, bg=BG)
        entry_row.pack(fill='x')
        tk.Entry(entry_row, textvariable=self._dash_path, width=52,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief='flat', font=('Consolas', 9)
                 ).pack(side='left', padx=(0, 4), pady=2, expand=True, fill='x')
        _btn(entry_row, 'Browse…', self._browse).pack(side='left', padx=(0, 4))
        _btn(entry_row, 'Load',    self._load,  color='#1f618d').pack(side='left')

        # Run list (read-only summary)
        frm2 = _lf(self, 'Runs found in CSV', '#9b59b6')
        frm2.pack(fill='both', expand=True, **P)
        list_outer = tk.Frame(frm2, bg=BG2, relief='flat', bd=1)
        list_outer.pack(fill='both', expand=True)
        sb = tk.Scrollbar(list_outer, orient='vertical', bg=BG2, troughcolor=BG)
        self._run_listbox = tk.Listbox(
            list_outer, height=8, selectmode='extended',
            bg=BG2, fg=FG, selectbackground='#1f618d', selectforeground='white',
            activestyle='none', font=('Consolas', 9), relief='flat',
            yscrollcommand=sb.set)
        sb.config(command=self._run_listbox.yview)
        sb.pack(side='right', fill='y')
        self._run_listbox.pack(side='left', fill='both', expand=True)

        # Step 3 — Options + output
        frm3 = _lf(self, 'Step 2 — Options', FG2)
        frm3.pack(fill='x', **P)

        # Interval radio buttons
        int_row = tk.Frame(frm3, bg=BG)
        int_row.pack(fill='x', pady=(0, 4))
        tk.Label(int_row, text='Interval:', bg=BG, fg=FG,
                 font=('Arial', 9), width=22, anchor='w').pack(side='left')
        for iv in yr.INTERVALS:
            tk.Radiobutton(
                int_row, text=iv.title(), variable=self._interval_var, value=iv,
                bg=BG, fg=FG, selectcolor=BG2, activebackground=BG,
                activeforeground=FG, font=('Arial', 9), relief='flat'
            ).pack(side='left', padx=(0, 6))

        # Product config JSON (optional — for ibin names)
        cfg_row = tk.Frame(frm3, bg=BG)
        cfg_row.pack(fill='x', pady=(0, 4))
        tk.Label(cfg_row, text='Product config (optional):', bg=BG, fg=FG,
                 font=('Arial', 9), width=22, anchor='w').pack(side='left')
        tk.Entry(cfg_row, textvariable=self._cfg_var, width=38,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief='flat', font=('Consolas', 8)
                 ).pack(side='left', padx=(0, 4), expand=True, fill='x')
        _btn(cfg_row, 'Browse…', self._browse_cfg, width=8).pack(side='left', padx=(0, 4))
        _btn(cfg_row, 'Clear',   lambda: self._cfg_var.set(''),
             color='#6d3b01', width=6).pack(side='left')

        # Weeks/periods filter
        wk_row = tk.Frame(frm3, bg=BG)
        wk_row.pack(fill='x', pady=(0, 4))
        tk.Label(wk_row, text='Last N periods (0 = all):', bg=BG, fg=FG,
                 font=('Arial', 9), width=22, anchor='w').pack(side='left')
        tk.Entry(wk_row, textvariable=self._weeks_var, width=6,
                 bg=BG2, fg='white', insertbackground='white',
                 relief='flat', font=('Consolas', 9)).pack(side='left', padx=(0, 8))
        tk.Label(wk_row,
                 text='e.g. 8 = show only the 8 most recent periods',
                 bg=BG, fg=FG2, font=('Arial', 8)).pack(side='left')

        # Output file
        out_row = tk.Frame(frm3, bg=BG)
        out_row.pack(fill='x')
        tk.Label(out_row, text='Output file:', bg=BG, fg=FG,
                 font=('Arial', 9), width=12, anchor='w').pack(side='left')
        tk.Entry(out_row, textvariable=self._out_var, width=46,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief='flat', font=('Consolas', 9)
                 ).pack(side='left', padx=(0, 4), expand=True, fill='x')
        _btn(out_row, '…', self._browse_out, width=3).pack(side='left')

        # Buttons
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=(6, 2), padx=10, fill='x')
        self._run_btn = _btn(btn_row, '▶  Generate Report', self._generate,
                             color=GRN, acolor=AGRN)
        self._run_btn.config(font=('Arial', 10, 'bold'), pady=5)
        self._run_btn.pack(side='left', expand=True, fill='x', padx=(0, 4))
        self._open_btn = _btn(btn_row, '  Open Dashboard  ', self._open_dashboard,
                              color='#935116', acolor='#ca6f1e')
        self._open_btn.config(font=('Arial', 10, 'bold'), pady=5, state='disabled')
        self._open_btn.pack(side='left')

        # Log
        log_frm = _lf(self, 'Log', FG2)
        log_frm.pack(fill='both', expand=False, **P)
        self._log = tk.Text(log_frm, height=6, state='disabled',
                            font=('Consolas', 8), bg='#0d1b26', fg='#a8d8ea',
                            relief='flat', insertbackground=FG)
        self._log.pack(fill='both', expand=True)

    # ---------------------------------------------------------------- events --

    def _browse(self):
        p = filedialog.askopenfilename(
            title='Select input CSV / ZIP / GZ',
            filetypes=[
                ('Supported files', '*.csv *.zip *.gz *.gzip'),
                ('CSV files', '*.csv'),
                ('ZIP archives', '*.zip'),
                ('GZ archives', '*.gz *.gzip'),
                ('All files', '*.*'),
            ])
        if p:
            self._dash_path.set(p)
            # Auto-detect product config if not already set
            if not self._cfg_var.get().strip():
                try:
                    import trend_chart as tc
                    auto = tc._find_auto_config()
                    if auto:
                        self._cfg_var.set(str(auto))
                        self._log_write(f'Auto-detected product config: {auto.name}\n')
                except Exception:
                    pass
            self._load()

    def _browse_cfg(self):
        p = filedialog.askopenfilename(
            title='Select product config JSON',
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')])
        if p:
            self._cfg_var.set(p)

    def _browse_out(self):
        p = filedialog.asksaveasfilename(
            title='Save report as',
            defaultextension='.html',
            filetypes=[('HTML files', '*.html')])
        if p:
            self._out_var.set(p)

    def _open_dashboard(self):
        rep = self._last_report
        if rep and os.path.isfile(rep):
            try:
                os.startfile(rep)
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

    # ------------------------------------------------------------------ load --

    def _load(self):
        path_str = self._dash_path.get().strip()
        if not path_str:
            return
        csv_path = Path(path_str)
        if not csv_path.exists():
            messagebox.showerror('Not found', f'File not found:\n{csv_path}')
            return
        try:
            import trend_chart as tc
            runs = tc.load_csv(csv_path)
        except Exception as exc:
            messagebox.showerror('Parse error', str(exc))
            return
        if not runs:
            messagebox.showwarning('No runs', 'No run data found in file.')
            return

        self._run_listbox.delete(0, 'end')
        interval = self._interval_var.get()
        for r in runs:
            ts = r.get('date_str', '') or ''
            period = yr._ts_to_period(ts, interval) or '?'
            self._run_listbox.insert('end',
                f'{r.get("label", "")}   {ts}   [{period}]')

        self._out_var.set(str(csv_path.parent / (csv_path.stem + '_report.html')))
        self._open_btn.configure(state='normal')
        self._log_write(f'Loaded {len(runs)} run(s) from {csv_path.name}\n')

    # ------------------------------------------------------------- generate --

    def _generate(self):
        csv_str = self._dash_path.get().strip()
        if not csv_str:
            messagebox.showwarning('No data', 'Load a CSV / ZIP / GZ file first.')
            return
        csv_path = Path(csv_str)
        out_path = Path(self._out_var.get().strip() or
                        csv_path.parent / (csv_path.stem + '_report.html'))
        interval  = self._interval_var.get()
        try:
            weeks_back = int(self._weeks_var.get().strip() or '0')
        except ValueError:
            weeks_back = 0

        # Load product config if provided
        cfg = None
        cfg_path_str = self._cfg_var.get().strip()
        if cfg_path_str:
            try:
                import trend_chart as tc
                cfg = tc.load_product_config(cfg_path_str)
            except Exception as e:
                self._log_write(f'Warning: could not load product config: {e}\n')

        self._run_btn.configure(state='disabled', text='Working\u2026', bg=FG2)
        self._log_write('Loading run data\u2026\n')

        def _worker():
            try:
                runs_data = yr.runs_from_csv(csv_path,
                                             log=self._log_write,
                                             interval=interval)
                self._log_write('Generating report\u2026\n')
                yr.generate_report(csv_path, runs_data, out_path,
                                   weeks_back=weeks_back,
                                   interval=interval,
                                   cfg=cfg)
                self._log_write(f'Done → {out_path}\n')
                self._last_report = str(out_path)
                try:
                    os.startfile(str(out_path))
                except Exception:
                    pass
            except Exception as exc:
                import traceback
                self._log_write(f'ERROR: {exc}\n{traceback.format_exc()}\n')
            finally:
                def _re_enable():
                    self._run_btn.configure(state='normal',
                                            text='▶  Generate Report', bg=GRN)
                try:
                    self.after(0, _re_enable)
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()
