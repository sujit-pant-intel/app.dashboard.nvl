"""GUI front-end for compare_runs — dark theme matching Yield Analysis Dashboard."""

import os
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox

sys.path.insert(0, str(Path(__file__).parent))
import compare_runs as cr

# ── Palette (same as dashboard.py) ──────────────────────────────────────────
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
    f = tk.LabelFrame(parent, text=text, bg=BG, fg=label_color,
                      font=('Arial', 8, 'bold'), padx=6, pady=4,
                      relief='groove', bd=1)
    return f


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class CompareFrame(tk.Frame):
    def __init__(self, parent=None, **kw):
        super().__init__(parent, bg=BG, **kw)

        self._dash_path  = tk.StringVar()
        self._out_var    = tk.StringVar()
        self._run_records = []
        self._check_vars  = []
        self._last_report_path = ''

        self._build_ui()

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        P = {'padx': 10, 'pady': 4}

        # ── Title ─────────────────────────────────────────────────────────────
        tk.Label(self, text='TestProgram Compare Tool',
                 bg=BG, fg=ABLU, font=('Arial', 13, 'bold')
                 ).pack(fill='x', padx=10, pady=(8, 2))

        # ── Step 1 ────────────────────────────────────────────────────────────
        frm1 = _lf(self, 'Step 1 — Dashboard.html', ABLU)
        frm1.pack(fill='x', **P)

        entry_row = tk.Frame(frm1, bg=BG)
        entry_row.pack(fill='x')
        tk.Entry(entry_row, textvariable=self._dash_path, width=52,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief='flat', font=('Consolas', 9)
                 ).pack(side='left', padx=(0, 4), pady=2, expand=True, fill='x')
        _btn(entry_row, 'Browse…', self._browse).pack(side='left', padx=(0, 4))
        _btn(entry_row, 'Load',    self._load,  color='#1f618d').pack(side='left')

        # ── Step 2 ────────────────────────────────────────────────────────────
        frm2 = _lf(self, 'Step 2 — Select identifiers to compare', '#9b59b6')
        frm2.pack(fill='both', expand=True, **P)

        sel_row = tk.Frame(frm2, bg=BG)
        sel_row.pack(fill='x', pady=(2, 4))
        _btn(sel_row, 'Select all',   self._sel_all,  color='#1f618d').pack(side='left', padx=(0, 4))
        _btn(sel_row, 'Deselect all', self._sel_none, color='#6d3b01').pack(side='left')
        tk.Label(sel_row, text='Use ↑↓ to set column order in report',
                 bg=BG, fg=FG2, font=('Arial', 8)).pack(side='left', padx=(12, 0))

        list_outer = tk.Frame(frm2, bg=BG2, relief='flat', bd=1)
        list_outer.pack(fill='both', expand=True)

        self._canvas = tk.Canvas(list_outer, bg=BG2, borderwidth=0,
                                 highlightthickness=0)
        vsb = tk.Scrollbar(list_outer, orient='vertical',
                           command=self._canvas.yview,
                           bg=BG2, troughcolor=BG)
        self._canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        self._canvas.pack(side='left', fill='both', expand=True)

        self._list_inner = tk.Frame(self._canvas, bg=BG2)
        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self._list_inner, anchor='nw')
        self._list_inner.bind('<Configure>', self._on_inner_configure)
        self._canvas.bind('<Configure>', self._on_canvas_configure)

        # ── Step 3 ────────────────────────────────────────────────────────────
        frm3 = _lf(self, 'Step 3 — Output', FG2)
        frm3.pack(fill='x', **P)

        out_row = tk.Frame(frm3, bg=BG)
        out_row.pack(fill='x')
        tk.Label(out_row, text='Output file:', bg=BG, fg=FG,
                 font=('Arial', 9), width=11, anchor='w').pack(side='left')
        tk.Entry(out_row, textvariable=self._out_var, width=46,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief='flat', font=('Consolas', 9)
                 ).pack(side='left', padx=(0, 4), expand=True, fill='x')
        _btn(out_row, '…', self._browse_out, width=3).pack(side='left')

        # ── Action buttons ────────────────────────────────────────────────────
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

        # ── Log ───────────────────────────────────────────────────────────────
        log_frm = _lf(self, 'Log', FG2)
        log_frm.pack(fill='both', expand=False, **P)
        self._log = tk.Text(log_frm, height=6, state='disabled',
                            font=('Consolas', 8), bg='#0d1b26', fg='#a8d8ea',
                            relief='flat', insertbackground=FG)
        self._log.pack(fill='both', expand=True)

    # ---------------------------------------------------------------- events --

    def _on_inner_configure(self, _evt=None):
        self._canvas.configure(scrollregion=self._canvas.bbox('all'))

    def _on_canvas_configure(self, evt):
        self._canvas.itemconfig(self._canvas_win, width=evt.width)

    def _browse(self):
        p = filedialog.askopenfilename(
            title='Select Dashboard.html',
            filetypes=[('HTML files', '*.html'), ('All files', '*.*')])
        if p:
            self._dash_path.set(p)
            self._load()

    def _browse_out(self):
        p = filedialog.asksaveasfilename(
            title='Save report as',
            defaultextension='.html',
            filetypes=[('HTML files', '*.html')])
        if p:
            self._out_var.set(p)

    def _sel_all(self):
        for v in self._check_vars:
            v.set(True)

    def _sel_none(self):
        for v in self._check_vars:
            v.set(False)

    # ------------------------------------------------------------------ load --

    def _load(self):
        path_str = self._dash_path.get().strip()
        if not path_str:
            return
        dash = Path(path_str)
        if not dash.exists():
            messagebox.showerror('Not found', f'File not found:\n{dash}')
            return
        try:
            records = cr.parse_dashboard(dash)
        except Exception as exc:
            messagebox.showerror('Parse error', str(exc))
            return
        if not records:
            messagebox.showwarning('No runs', 'No run blocks found in Dashboard.html')
            return

        self._run_records = records
        self._log_write(f'Loaded {len(records)} identifier(s) from {dash.name}\n')
        self._open_btn.configure(state='normal')

        for w in self._list_inner.winfo_children():
            w.destroy()
        self._check_vars = []

        for i, rec in enumerate(records):
            var = tk.BooleanVar(value=True)
            self._check_vars.append(var)
            row_bg = BG2 if i % 2 == 0 else '#253545'
            row = tk.Frame(self._list_inner, bg=row_bg)
            row.pack(fill='x')
            tk.Label(row, text=f'Col {i+1}', bg=row_bg, fg='#7fb3d3',
                     font=('Arial', 8), width=5).pack(side='left', padx=(4, 0))
            tk.Button(row, text='↑', command=lambda idx=i: self._move_up(idx),
                      bg=BG, fg=FG, relief='flat', font=('Arial', 8),
                      padx=2, pady=0, cursor='hand2').pack(side='left')
            tk.Button(row, text='↓', command=lambda idx=i: self._move_down(idx),
                      bg=BG, fg=FG, relief='flat', font=('Arial', 8),
                      padx=2, pady=0, cursor='hand2').pack(side='left', padx=(0, 4))
            tk.Checkbutton(row, variable=var, bg=row_bg, fg=FG,
                           selectcolor=BG, activebackground=row_bg,
                           activeforeground=FG, relief='flat',
                           font=('Arial', 9),
                           text=rec['name']).pack(side='left', padx=2, pady=2)
            ts = rec.get('ts', '')
            if ts:
                tk.Label(row, text=ts, bg=row_bg, fg=FG2,
                         font=('Arial', 8)).pack(side='right', padx=8)

        self._out_var.set(str(dash.parent / 'compare_report.html'))

    def _move_up(self, idx):
        if idx <= 0 or idx >= len(self._run_records):
            return
        self._run_records[idx-1], self._run_records[idx] = self._run_records[idx], self._run_records[idx-1]
        self._check_vars[idx-1], self._check_vars[idx] = (
            tk.BooleanVar(value=self._check_vars[idx].get()),
            tk.BooleanVar(value=self._check_vars[idx-1].get()))
        self._rebuild_list()

    def _move_down(self, idx):
        if idx < 0 or idx >= len(self._run_records) - 1:
            return
        self._run_records[idx], self._run_records[idx+1] = self._run_records[idx+1], self._run_records[idx]
        self._check_vars[idx], self._check_vars[idx+1] = (
            tk.BooleanVar(value=self._check_vars[idx+1].get()),
            tk.BooleanVar(value=self._check_vars[idx].get()))
        self._rebuild_list()

    def _rebuild_list(self):
        for w in self._list_inner.winfo_children():
            w.destroy()
        new_vars = []
        for i, (rec, var) in enumerate(zip(self._run_records, self._check_vars)):
            new_var = tk.BooleanVar(value=var.get())
            new_vars.append(new_var)
            row_bg = BG2 if i % 2 == 0 else '#253545'
            row = tk.Frame(self._list_inner, bg=row_bg)
            row.pack(fill='x')
            tk.Label(row, text=f'Col {i+1}', bg=row_bg, fg='#7fb3d3',
                     font=('Arial', 8), width=5).pack(side='left', padx=(4, 0))
            tk.Button(row, text='↑', command=lambda idx=i: self._move_up(idx),
                      bg=BG, fg=FG, relief='flat', font=('Arial', 8),
                      padx=2, pady=0, cursor='hand2').pack(side='left')
            tk.Button(row, text='↓', command=lambda idx=i: self._move_down(idx),
                      bg=BG, fg=FG, relief='flat', font=('Arial', 8),
                      padx=2, pady=0, cursor='hand2').pack(side='left', padx=(0, 4))
            tk.Checkbutton(row, variable=new_var, bg=row_bg, fg=FG,
                           selectcolor=BG, activebackground=row_bg,
                           activeforeground=FG, relief='flat',
                           font=('Arial', 9),
                           text=rec['name']).pack(side='left', padx=2, pady=2)
            ts = rec.get('ts', '')
            if ts:
                tk.Label(row, text=ts, bg=row_bg, fg=FG2,
                         font=('Arial', 8)).pack(side='right', padx=8)
        self._check_vars = new_vars

    # ------------------------------------------------------------- generate --

    def _generate(self):
        if not self._run_records:
            messagebox.showwarning('No data', 'Load a Dashboard.html first.')
            return
        selected = [rec for rec, var in zip(self._run_records, self._check_vars)
                    if var.get()]
        if len(selected) < 1:
            messagebox.showwarning('No selection', 'Select at least 1 identifier.')
            return

        out_path  = Path(self._out_var.get().strip() or
                         Path(self._dash_path.get()).parent / 'compare_report.html')
        dash_path = Path(self._dash_path.get())

        self._run_btn.configure(state='disabled', text='Working…', bg=FG2)
        self._log_write('Loading data…\n')

        def _worker():
            try:
                dash_dir  = dash_path.parent
                runs_data = []
                for rec in selected:
                    xlsx_p     = cr.find_xlsx(dash_dir, rec['index_href'])
                    data       = None
                    output_dir = None
                    if xlsx_p:
                        self._log_write(f'  [{rec["name"]}] {xlsx_p.name}\n')
                        data       = cr.read_xlsx(xlsx_p)
                        output_dir = xlsx_p.parent
                    else:
                        self._log_write(f'  [{rec["name"]}] no xlsx found\n')
                        import re as _re
                        href = _re.sub(r'^file:///', '', rec['index_href'] or '').replace('/', os.sep)
                        idx  = dash_dir / href if not os.path.isabs(href) else Path(href)
                        output_dir = idx.parent if idx else None

                    bin_data = None
                    upm_data = None
                    cdyn_data = None
                    if output_dir and output_dir.exists():
                        bin_p = cr.find_bin_html(output_dir)
                        if bin_p:
                            self._log_write(f'  [{rec["name"]}] {bin_p.name}\n')
                            bin_data = cr.parse_bin_html(bin_p)
                        gm_p = cr.find_group_medians(output_dir)
                        if gm_p:
                            self._log_write(f'  [{rec["name"]}] {gm_p.name}\n')
                            upm_data = cr.parse_group_medians(gm_p)
                        cdyn_p = cr.find_cdyn_medians(output_dir)
                        if cdyn_p:
                            self._log_write(f'  [{rec["name"]}] {cdyn_p.name}\n')
                            cdyn_data = cr.parse_cdyn_medians(cdyn_p)

                    # Fallback: extract UPM from raw CSV if Group_Medians not found
                    upm_detail = None
                    if not upm_data and output_dir and output_dir.exists():
                        raw_csv = cr.find_raw_csv(output_dir)
                        if raw_csv:
                            # Find config JSON early for extraction
                            _cfg_tmp = None
                            try:
                                _coll = dash_path.parent / 'collateral'
                                if _coll.exists():
                                    _cfs = sorted(_coll.glob('Product Config*.json'),
                                                  key=lambda p: p.stat().st_mtime, reverse=True)
                                    if _cfs:
                                        _cfg_tmp = str(_cfs[0])
                            except Exception:
                                pass
                            upm_data, upm_detail = cr.extract_upm_from_csv(raw_csv, config_json=_cfg_tmp)
                            if upm_data:
                                self._log_write(f'  [{rec["name"]}] UPM from {raw_csv.name}\n')

                    runs_data.append({**rec, 'data': data,
                                      'xlsx_path': xlsx_p or '',
                                      'bin_data': bin_data,
                                      'upm_data': upm_data,
                                      'upm_detail': upm_detail,
                                      'cdyn_data': cdyn_data})

                self._log_write('Generating report…\n')
                # Find Product Config JSON in collateral/ folder
                _cfg_json = None
                try:
                    _collateral = dash_path.parent / 'collateral'
                    if _collateral.exists():
                        _cfgs = sorted(_collateral.glob('Product Config*.json'),
                                       key=lambda p: p.stat().st_mtime, reverse=True)
                        if _cfgs:
                            _cfg_json = str(_cfgs[0])
                            self._log_write(f'  Config: {_cfgs[0].name}\n')
                except Exception:
                    pass
                cr.generate_report(runs_data, out_path, config_json=_cfg_json,
                                   dash_dir=dash_path.parent)
                self._log_write(f'Done → {out_path}\n')
                # Update comparison links in Dashboard.html
                try:
                    cr.update_dashboard_compare_links(dash_path, out_path)
                    self._log_write(f'Updated {dash_path.name} with compare links.\n')
                except Exception as e:
                    self._log_write(f'Warning: could not update Dashboard.html: {e}\n')
                self._last_report_path = str(out_path)
                self.after(0, lambda: self._open_btn.configure(state='normal'))
            except Exception as exc:
                self._log_write(f'ERROR: {exc}\n')
            finally:
                self.after(0, lambda: self._run_btn.configure(
                    state='normal', text='▶  Generate Report', bg=GRN))

        threading.Thread(target=_worker, daemon=True).start()

    # --------------------------------------------------------------- open --

    def _open_dashboard(self):
        p = self._dash_path.get().strip()
        if p and os.path.isfile(p):
            try:
                os.startfile(p)
            except Exception as exc:
                messagebox.showerror('Error', str(exc))
        else:
            messagebox.showwarning('Not found', 'Dashboard.html not found. Load a Dashboard.html first.')

    # ------------------------------------------------------------------- log --

    def _log_write(self, msg: str):
        def _do():
            self._log.configure(state='normal')
            self._log.insert('end', msg)
            self._log.see('end')
            self._log.configure(state='disabled')
        self.after(0, _do)


# Keep standalone entrypoint
class CompareGUI(tk.Tk):
    """Standalone wrapper — embeds CompareFrame in a Tk root window."""
    def __init__(self):
        super().__init__()
        self.title('TestProgram Compare Tool')
        self.resizable(True, True)
        self.minsize(620, 540)
        frame = CompareFrame(self)
        frame.pack(fill='both', expand=True)


# ---------------------------------------------------------------------------
if __name__ == '__main__':
    CompareGUI().mainloop()
