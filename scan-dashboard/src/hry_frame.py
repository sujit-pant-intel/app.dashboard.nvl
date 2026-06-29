"""hry_frame.py -- Tkinter GUI tab for HRY Scan Analysis.

Self-contained: defines its own path constants so dashboard.py does not
need modification when this tab changes.

Usage (from dashboard.py):
    sys.path.insert(0, os.path.join(_SCRIPT_DIR, 'src'))
    from hry_frame import HRYFrame
"""

import sys
sys.dont_write_bytecode = True
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

import json
import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

# -- Path constants -----------------------------------------------------------
_HRY_SRC_DIR    = os.path.dirname(os.path.abspath(__file__))
_HRY_ROOT_DIR   = os.path.normpath(os.path.join(_HRY_SRC_DIR, '..'))
_PIPELINE       = os.path.join(_HRY_SRC_DIR, 'pipeline.py')  # kept for reference
_COLLATERAL_DIR = os.path.join(_HRY_ROOT_DIR, 'collateral')  # scan-specific (partitions, brita)


def _find_repo_root(start: str) -> str:
    """Walk up until we find a directory that contains 'shared/', or use
    the APP_YIELD_NVL_ROOT env-var override (for network-share deployments)."""
    override = os.environ.get('APP_YIELD_NVL_ROOT', '')
    if override and os.path.isdir(os.path.join(override, 'shared')):
        return override
    d = start
    for _ in range(12):
        if os.path.isdir(os.path.join(d, 'shared')):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # fallback: 3 levels up from src/ (original behaviour)
    return os.path.normpath(os.path.join(start, '..', '..', '..'))


_REPO_ROOT             = _find_repo_root(_HRY_SRC_DIR)
_SHARED_COLLATERAL_DIR = os.path.join(_REPO_ROOT, 'shared')
_RETICLE_DIR    = os.path.join(_SHARED_COLLATERAL_DIR, 'reticle')
_MATERIAL_DIR   = os.path.join(_SHARED_COLLATERAL_DIR, 'material')

# _loader.py dispatcher — enables .pyd compiled deployment
# Prefer a _loader.py in the same src/ dir; fall back to the yield-dashboard one
_LOADER = os.path.join(_HRY_SRC_DIR, '_loader.py')
if not os.path.isfile(_LOADER):
    _LOADER = os.path.normpath(
        os.path.join(_HRY_ROOT_DIR, '..', 'yield-dashboard',
                     'yld', 'src', '_loader.py'))

# -- Palette (matches vmin / yield dashboards) --------------------------------
BG   = '#1a252f'
BG2  = '#2c3e50'
FG   = '#ecf0f1'
FG2  = '#95a5a6'
ABLU = '#3498db'
GRN  = '#27ae60'


# -- HRYFrame -----------------------------------------------------------------

class HRYFrame(tk.Frame):
    """HRY Scan Analysis tab."""

    def __init__(self, parent=None, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._proc = None
        self._dashboard_path = ''
        self._build_ui()

    def _build_ui(self):
        def _btn(parent, text, cmd, color=ABLU, acolor='#5dade2'):
            return tk.Button(parent, text=text, command=cmd,
                             bg=color, fg='white', activebackground=acolor,
                             relief='flat', cursor='hand2',
                             font=('Arial', 9), padx=8, pady=3)

        def _lf(text, color=FG2):
            return tk.LabelFrame(self, text=text, bg=BG, fg=color,
                                 font=('Arial', 8, 'bold'), padx=6, pady=4,
                                 relief='groove', bd=1)

        def _field(parent, label, var, browse_cmd):
            row = tk.Frame(parent, bg=BG)
            row.pack(fill='x', pady=2)
            tk.Label(row, text=label, width=16, anchor='w',
                     bg=BG, fg=FG, font=('Arial', 9)
                     ).pack(side='left')
            tk.Entry(row, textvariable=var, width=52,
                     bg=BG2, fg=FG, insertbackground=FG,
                     relief='flat', font=('Consolas', 9)
                     ).pack(side='left', expand=True, fill='x', padx=(4, 4))
            _btn(row, '...', browse_cmd, color='#1f618d').pack(side='left')

        tk.Label(self, text='HRY Scan Analysis',
                 bg=BG, fg=ABLU, font=('Arial', 13, 'bold')
                 ).pack(fill='x', padx=10, pady=(10, 4))

        # -- Settings row: Load / Save settings -------------------------------
        stt_row = tk.Frame(self, bg=BG)
        stt_row.pack(fill='x', padx=10, pady=(0, 6))
        _btn(stt_row, '  Load Settings  ', self._load_settings,
             color='#1f618d', acolor=ABLU).pack(side='left', padx=(0, 4))
        _btn(stt_row, '  Save Settings  ', self._save_settings,
             color='#1f618d', acolor=ABLU).pack(side='left', padx=(0, 4))

        # -- Input CSV files (multi-file) -------------------------------------
        frm_input = _lf(
            'Input CSV files  (one or more — each processed, then combined)',
            ABLU)
        frm_input.pack(fill='x', padx=10, pady=(0, 4))

        _lb_outer = tk.Frame(frm_input, bg=BG)
        _lb_outer.pack(fill='x', pady=(2, 0))
        _lb_scroll_y = tk.Scrollbar(_lb_outer, orient='vertical')
        _lb_scroll_x = tk.Scrollbar(_lb_outer, orient='horizontal')
        self._input_listbox = tk.Listbox(
            _lb_outer, height=4, selectmode='extended',
            bg=BG2, fg=FG, selectbackground='#1f618d', selectforeground='white',
            activestyle='none', font=('Consolas', 9), relief='flat',
            yscrollcommand=_lb_scroll_y.set,
            xscrollcommand=_lb_scroll_x.set)
        _lb_scroll_y.config(command=self._input_listbox.yview)
        _lb_scroll_x.config(command=self._input_listbox.xview)
        _lb_scroll_y.pack(side='right', fill='y')
        _lb_scroll_x.pack(side='bottom', fill='x')
        self._input_listbox.pack(side='left', fill='both', expand=True)

        _lb_btn_row = tk.Frame(frm_input, bg=BG)
        _lb_btn_row.pack(fill='x', pady=(4, 0))
        _btn(_lb_btn_row, '  Add CSV / GZ / ZIP / 7Z File(s)  ', self._add_input_files,
             color='#1f618d').pack(side='left', padx=(0, 4))
        _btn(_lb_btn_row, '  Remove Selected  ', self._remove_selected_files,
             color='#7b241c', acolor='#a93226').pack(side='left')
        tk.Label(_lb_btn_row,
                 text='Tip: select multiple files for a combined run.',
                 bg=BG, fg=FG2, font=('Arial', 8)).pack(side='left', padx=(8, 0))

        # -- Output folder ----------------------------------------------------
        frm_out = _lf('Output folder', ABLU)
        frm_out.pack(fill='x', padx=10, pady=(0, 4))
        self._out_var = tk.StringVar()
        _field(frm_out, 'Output folder:', self._out_var, self._browse_outdir)

        # -- Collateral & Enrichment ------------------------------------------
        # Reticle and Material CSV dirs are resolved automatically from the
        # shared/ folder next to this repo (shared/reticle/ and shared/material/).
        # No user input needed.

        # -- Options ----------------------------------------------------------
        frm_opts = _lf('Options', ABLU)
        frm_opts.pack(fill='x', padx=10, pady=(0, 4))
        self._standalone_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            frm_opts,
            text='Build standalone HTML  (single shareable file with all data embedded)',
            variable=self._standalone_var,
            bg=BG, fg=FG, selectcolor=BG2,
            activebackground=BG, activeforeground=FG,
            font=('Arial', 9),
        ).pack(anchor='w')

        # -- Run / Open Dashboard buttons -------------------------------------
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill='x', padx=10, pady=(6, 4))

        self._run_btn = _btn(btn_row, '  Run HRY Scan Analysis  ', self._run,
                             color=GRN, acolor='#2ecc71')
        self._run_btn.config(font=('Arial', 10, 'bold'), pady=5)
        self._run_btn.pack(side='left', fill='x', expand=True, padx=(0, 4))

        self._dash_btn = _btn(btn_row, '  Open HRY Scan Dashboard  ', self._open_dashboard,
                              color='#1f618d', acolor='#2980b9')
        self._dash_btn.config(font=('Arial', 10, 'bold'), pady=5, state='disabled')
        self._dash_btn.pack(side='left', fill='x', expand=True, padx=(4, 0))

        # -- Output log -------------------------------------------------------
        log_frm = _lf('Output', FG2)
        log_frm.pack(fill='both', expand=True, padx=10, pady=(0, 8))
        self._log = scrolledtext.ScrolledText(
            log_frm, height=12,
            font=('Consolas', 9), bg='#0d1b26', fg='#a8d8ea',
            relief='flat', insertbackground=FG, state='disabled')
        self._log.pack(fill='both', expand=True)

    # -- Browse helpers -------------------------------------------------------

    def _browse_file(self, var, filetypes):
        p = filedialog.askopenfilename(filetypes=filetypes)
        if p:
            var.set(p)

    def _browse_outdir(self):
        p = filedialog.askdirectory()
        if p:
            self._out_var.set(p)

    def _browse_dir_into(self, var):
        p = filedialog.askdirectory()
        if p:
            var.set(p)

    def _add_input_files(self):
        paths = filedialog.askopenfilenames(
            title='Add CSV / GZ / ZIP / 7Z files',
            filetypes=[('CSV / GZ / ZIP / 7Z', '*.csv *.CSV *.gz *.csv.gz *.zip *.ZIP *.7z *.7Z'),
                       ('All files', '*.*')])
        existing = set(self._input_listbox.get(0, tk.END))
        for p in paths:
            if p and p not in existing:
                self._input_listbox.insert(tk.END, p)
                existing.add(p)
        if paths and not self._out_var.get().strip():
            self._out_var.set(str(Path(paths[0]).parent / 'output'))

    def _remove_selected_files(self):
        for i in reversed(self._input_listbox.curselection()):
            self._input_listbox.delete(i)

    # -- Load / Save Settings -------------------------------------------------

    def _load_settings(self):
        p = filedialog.askopenfilename(
            title='Load HRY Scan Settings',
            filetypes=[('Scan settings', '*.scancfg.json *.json'),
                       ('All files', '*.*')])
        if p:
            self._load_settings_file(p)

    def _load_settings_file(self, p):
        """Load settings from a JSON/scancfg.json path into the UI fields."""
        try:
            data = json.loads(Path(p).read_text(encoding='utf-8-sig'))
            # support old 'inputs'/'input' keys and new 'input_files'
            _files = data.get('input_files', [])
            if not _files:
                _old = data.get('inputs', data.get('input', ''))
                _files = _old if isinstance(_old, list) else ([_old] if _old else [])
            self._input_listbox.delete(0, tk.END)
            for f in _files:
                if f:
                    self._input_listbox.insert(tk.END, f)
            self._out_var.set(data.get('output_dir', data.get('output', '')))
            self._standalone_var.set(bool(data.get('standalone', False)))
            dp = data.get('dashboard_path', '')
            if dp and os.path.isfile(dp):
                self._dashboard_path = dp
                self.after(0, lambda: self._dash_btn.configure(state='normal'))
            self._log_write(f'Settings loaded from: {p}\n')
        except Exception as exc:
            messagebox.showerror('Load failed', f'Could not load settings:\n{exc}')

    def auto_load(self, json_path: str):
        """Call after the main-loop starts to pre-populate fields from a JSON file."""
        self.after(0, lambda: self._load_settings_file(json_path))

    def _save_settings(self):
        out = self._out_var.get().strip()
        initial_dir = os.path.dirname(out) if out and os.path.isdir(os.path.dirname(out)) else ''
        p = filedialog.asksaveasfilename(
            title='Save HRY Scan Settings',
            initialdir=initial_dir,
            initialfile='scan_settings.scancfg.json',
            defaultextension='.json',
            filetypes=[('Scan settings', '*.scancfg.json *.json'),
                       ('All files', '*.*')])
        if not p:
            return
        try:
            _files = list(self._input_listbox.get(0, tk.END))
            data = {
                'input_files':    _files,
                'output_dir':     self._out_var.get().strip(),
                'standalone':     self._standalone_var.get(),
                'dashboard_path': self._dashboard_path,
            }
            Path(p).write_text(json.dumps(data, indent=2, ensure_ascii=False),
                               encoding='utf-8')
            self._log_write(f'Settings saved to: {p}\n')
        except Exception as exc:
            messagebox.showerror('Save failed', f'Could not save settings:\n{exc}')

    # -- Log ------------------------------------------------------------------

    def _log_write(self, msg):
        def _do():
            self._log.configure(state='normal')
            self._log.insert('end', msg)
            self._log.see('end')
            self._log.configure(state='disabled')
        self.after(0, _do)

    def _set_running(self, running):
        def _do():
            self._run_btn.configure(
                state='disabled' if running else 'normal',
                text='  Running...  ' if running else '  Run HRY Scan Analysis  ')
        self.after(0, _do)

    # -- Open Dashboard -------------------------------------------------------

    def _open_dashboard(self):
        p = self._dashboard_path
        if not p or not os.path.isfile(p):
            out = self._out_var.get().strip()
            if out:
                p = os.path.join(out, 'dashboard', 'index.html')
        if p and os.path.isfile(p):
            webbrowser.open(Path(p).as_uri())
        else:
            messagebox.showinfo('Not found',
                'Dashboard not found yet.\nRun the analysis first.')

    # -- Run ------------------------------------------------------------------

    def _run(self):
        input_files = [f.strip() for f in self._input_listbox.get(0, tk.END)
                       if f.strip()]
        out_dir = self._out_var.get().strip()

        if not input_files:
            messagebox.showwarning('Missing input',
                'Add at least one CSV file.')
            return
        for f in input_files:
            if not os.path.isfile(f):
                messagebox.showerror('Not found',
                    f'Input file not found:\n{f}')
                return
        if not out_dir:
            messagebox.showwarning('Missing output', 'Select an Output folder.')
            return

        Path(out_dir).mkdir(parents=True, exist_ok=True)
        # Clean previous output so stale files don't carry over
        dash_dir = Path(out_dir) / 'dashboard'
        if dash_dir.exists():
            shutil.rmtree(dash_dir)
        self._dashboard_path = ''
        self._log.configure(state='normal')
        self._log.delete('1.0', 'end')
        self._log.configure(state='disabled')
        self.after(0, lambda: self._dash_btn.configure(state='disabled'))
        self._set_running(True)
        threading.Thread(
            target=self._worker, args=(input_files, out_dir),
            daemon=True).start()

    def _worker(self, input_files, out_dir):
        def log(msg):
            self._log_write(msg + '\n')

        try:
            out_path = Path(out_dir)
            csv_paths = []

            for i, inp in enumerate(input_files, 1):
                inp_path = Path(inp)
                log('=' * 60)
                log(f'Input {i}/{len(input_files)}: {inp_path.name}')
                log('=' * 60)
                csv_paths.append(str(inp_path))

            log('')
            log('=' * 60)
            log('Pipeline  (reticle + material enrichment + dashboard)')
            log('=' * 60)
            for p in csv_paths:
                log(f'  Input  : {p}')
            log(f'  Output : {out_path}')

            if os.path.isfile(_LOADER):
                cmd = [sys.executable, '-B', _LOADER, 'pipeline', '--output', str(out_path)]
            else:
                cmd = [sys.executable, '-u', _PIPELINE, '--output', str(out_path)]
            for p in csv_paths:
                cmd += ['--input', p]
            if self._standalone_var.get():
                cmd.append('--standalone')

            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
            for line in self._proc.stdout:
                self._log_write(line)
                stripped = line.strip()
                if stripped.startswith('HRY_DASHBOARD:'):
                    hp = stripped[len('HRY_DASHBOARD:'):].strip()
                    if os.path.isfile(hp):
                        self._dashboard_path = hp
                        self.after(0,
                            lambda: self._dash_btn.configure(state='normal'))
            self._proc.wait()
            rc = self._proc.returncode

            if rc == 0:
                log('\nPipeline complete.')
                dash = str(out_path / 'dashboard' / 'index.html')
                if not self._dashboard_path and os.path.isfile(dash):
                    self._dashboard_path = dash
                    self.after(0,
                        lambda: self._dash_btn.configure(state='normal'))
                if self._dashboard_path:
                    log(f'  Dashboard: {self._dashboard_path}')
            else:
                log(f'\nPipeline exited with code {rc}')

        except Exception as exc:
            self._log_write(f'\nERROR: {exc}\n')
        finally:
            self._set_running(False)
