"""run_py_dashboard.py — Standalone Python-only SICC/UPM/CDYN launcher.

Run directly::
    python run_py_dashboard.py

Or import ``run_python_pipeline`` to call from another script (e.g.
the original sicc_cdyn_upm/src/run_dashboard.py with engine toggle).
"""

import sys
sys.dont_write_bytecode = True

import os
import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

# ---------------------------------------------------------------------------
# Locate sibling src directories so imports work regardless of cwd
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

try:
    from sicc_processor import load_config, process_csv  # type: ignore
    from generate_dashboard_html_svg import generate_html_svg as generate_html  # type: ignore
except ImportError as _ie:
    # Provide a helpful message if the user runs from a different directory
    raise ImportError(
        f"Could not import sicc_processor / generate_dashboard_html_svg from {_THIS_DIR}.\n"
        f"Original error: {_ie}"
    )


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_CONFIG_JSON = _THIS_DIR.parent.parent / 'collateral' / 'sicc_cdyn_testlist.json'
_DEFAULT_CONFIG = _DEFAULT_CONFIG_JSON


# ---------------------------------------------------------------------------
# Core pipeline function (thread-safe — no tkinter calls)
# ---------------------------------------------------------------------------
def run_python_pipeline(csv_path: str,
                         config_path: str,
                         target_csv: str,
                         output_dir: str,
                         dashboard_dir: str,
                         status_cb,
                         done_cb,
                         error_cb,
                         product_config_path: str = '') -> None:
    """Run the full Python pipeline in a background thread.

    Parameters
    ----------
    csv_path            : path to the sort data CSV
    config_path         : path to testlist.jsl or testlist.json (may be empty)
    target_csv          : path to SICC target CSV (may be empty; legacy)
    output_dir          : folder to write output files
    dashboard_dir       : folder to write the main dashboard HTML
    status_cb           : callable(str) — progress messages
    done_cb             : callable(str) — called with dashboard HTML path on success
    error_cb            : callable(str) — called with error message on failure
    product_config_path : path to Product Config JSON (sicc_targets / upm_target / cdyn_targets)
    """
    try:
        status_cb('Loading configuration…')
        if config_path and Path(config_path).is_file():
            cfg = load_config(config_path)
        elif _DEFAULT_CONFIG.is_file():
            cfg = load_config(str(_DEFAULT_CONFIG))
            status_cb(f'Using default config: {_DEFAULT_CONFIG.name}')
        else:
            cfg = {}
            status_cb('No config found — will auto-detect columns.')

        # Log the first 20 column names so user can see what's in the CSV
        try:
            import pandas as _pd
            _hdr = list(_pd.read_csv(csv_path, nrows=0, dtype=object).columns)
            status_cb(f'CSV has {len(_hdr)} columns. First 20:')
            for _c in _hdr[:20]:
                status_cb(f'  {_c}')
            if len(_hdr) > 20:
                status_cb(f'  … and {len(_hdr)-20} more')
        except Exception:
            pass

        # Extract targets from product config JSON (overrides anything in testlist)
        _override_targets: dict = {}
        _override_cdyn_targets: dict = {}
        if product_config_path and Path(product_config_path).is_file():
            try:
                import json as _jspc
                _pcfg = _jspc.loads(Path(product_config_path).read_text(encoding='utf-8'))
                for _e in _pcfg.get('sicc_targets', []):
                    _t = str(_e.get('test', '')).strip()
                    _v = _e.get('target_A')
                    if _t and _v is not None:
                        try: _override_targets[_t.upper()] = float(_v)
                        except (ValueError, TypeError): pass
                # upm_target not used from Product Config (UPM targets come from upmInfo)
                for _e in _pcfg.get('cdyn_targets', []):
                    _t = str(_e.get('test', '')).strip()
                    _v = _e.get('target_nF')
                    if _t and _v is not None:
                        try: _override_cdyn_targets[_t] = float(_v)
                        except (ValueError, TypeError): pass
                if _override_targets or _override_cdyn_targets:
                    status_cb(f'Loaded {len(_override_targets)} SICC + {len(_override_cdyn_targets)} CDYN targets from product config.')
                # Merge testlist configs from product config (takes precedence)
                for _key in ('siccList', 'siccTotalList', 'cdynList', 'upmInfo',
                             'SiccTableConfig', 'cdynTableConfig'):
                    if _key in _pcfg:
                        cfg[_key] = _pcfg[_key]
                        status_cb(f'Using {_key} from product config.')
            except Exception as _ep:
                status_cb(f'WARNING: Could not read product config targets: {_ep}')

        status_cb('Processing CSV…')
        data = process_csv(csv_path, cfg, target_csv=target_csv,
                           override_targets=_override_targets or None,
                           override_cdyn_targets=_override_cdyn_targets or None)

        n_rows   = len(data.get('rows', []))
        n_sicc   = len(data.get('sicc_columns', []))
        n_upm    = len(data.get('upm_columns', []))
        n_cdyn   = len(data.get('cdyn_columns', []))
        status_cb(
            f'Found {n_rows} wafers | {n_sicc} SICC | {n_upm} UPM | {n_cdyn} CDYN columns'
        )
        if n_sicc == 0 and n_upm == 0:
            status_cb('WARNING: No SICC/UPM columns matched the testlist patterns.')
            status_cb('Check the column names above vs your testlist.jsl renameList.')
            status_cb('Auto-detecting numeric test columns as fallback…')

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        Path(dashboard_dir).mkdir(parents=True, exist_ok=True)

        status_cb('Generating interactive HTML…')
        csv_stem   = Path(csv_path).stem
        html_name  = f'{csv_stem}_sicc_analysis.html'
        html_path  = Path(dashboard_dir) / html_name
        generate_html(data, str(html_path))

        done_cb(str(html_path))

    except Exception as exc:
        error_cb(str(exc))


# ---------------------------------------------------------------------------
# tkinter GUI
# ---------------------------------------------------------------------------
class PyLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SICC/UPM/CDYN — Python Dashboard')
        self.resizable(False, False)
        self.configure(bg='#1a252f')

        tk.Label(
            self, text='SICC / UPM / CDYN  (Python Engine)',
            bg='#1a252f', fg='#3498db', font=('Arial', 13, 'bold')
        ).grid(row=0, column=0, columnspan=3, pady=(14, 8), padx=14)

        self.csv_var    = self._field(1, 'Input CSV file',         '',                  'csv')
        self.cfg_var    = self._field(2, 'Config (.json/.jsl)',      self._default_cfg(), 'cfg')
        self.out_var    = self._field(3, 'Output folder',            '',                  'dir')
        self.dash_var   = self._field(4, 'Dashboard folder',         '',                  'dir')

        # Info box
        info = tk.LabelFrame(self, text='What this does', bg='#1a252f', fg='#7f8c8d',
                             font=('Arial', 8), padx=8, pady=4)
        info.grid(row=6, column=0, columnspan=3, padx=14, pady=(4, 0), sticky='ew')
        for i, t in enumerate([
            '1. Read sort CSV  →  rename SICC/UPM columns via testlist config',
            '2. Compute sum columns (SICC CORE, ATOM, FULLCHIP) and UPM %',
            '3. Detect CDYN columns and compute medians',
            '4. Calculate per-wafer medians + distributions',
            '5. Write self-contained interactive HTML dashboard',
        ]):
            tk.Label(info, text=t, bg='#1a252f', fg='#95a5a6',
                     font=('Consolas', 8), anchor='w').grid(row=i, column=0, sticky='w')

        # Buttons
        btn_frame = tk.Frame(self, bg='#1a252f')
        btn_frame.grid(row=7, column=0, columnspan=3, pady=12)

        tk.Button(
            btn_frame, text='  Generate Dashboard  ',
            bg='#27ae60', fg='white', font=('Arial', 11, 'bold'),
            relief='flat', cursor='hand2', activebackground='#2ecc71',
            command=self._on_run
        ).pack(side='left', padx=6)

        # Status
        self._status = tk.StringVar(value='Ready')
        tk.Label(
            self, textvariable=self._status,
            bg='#1a252f', fg='#95a5a6', font=('Arial', 9)
        ).grid(row=8, column=0, columnspan=3, pady=(0, 10))

    # ── helpers ────────────────────────────────────────────────────────────

    def _default_cfg(self) -> str:
        return str(_DEFAULT_CONFIG) if _DEFAULT_CONFIG.is_file() else ''

    def _field(self, row: int, label: str, default: str, kind: str) -> tk.StringVar:
        tk.Label(
            self, text=label, bg='#1a252f', fg='#ecf0f1',
            font=('Arial', 9), width=18, anchor='e'
        ).grid(row=row, column=0, padx=(14, 4), pady=3)

        var = tk.StringVar(value=default)
        tk.Entry(
            self, textvariable=var, width=54,
            bg='#2c3e50', fg='white', insertbackground='white',
            relief='flat', font=('Consolas', 9)
        ).grid(row=row, column=1, padx=4, pady=3)

        def browse():
            if kind == 'dir':
                d = filedialog.askdirectory()
                if d:
                    var.set(d.replace('/', '\\'))
            else:
                ftypes = {
                    'csv': [('CSV files', '*.csv'), ('All', '*.*')],
                    'cfg': [('Config', '*.jsl *.json'), ('JSL', '*.jsl'), ('JSON', '*.json'), ('All', '*.*')],
                }.get(kind, [('All', '*.*')])
                f = filedialog.askopenfilename(filetypes=ftypes)
                if f:
                    var.set(f)

        tk.Button(
            self, text='...', bg='#2980b9', fg='white',
            relief='flat', cursor='hand2', width=3,
            activebackground='#3498db', command=browse
        ).grid(row=row, column=2, padx=(0, 14), pady=3)

        return var

    # ── run ────────────────────────────────────────────────────────────────

    def _on_run(self):
        csv   = self.csv_var.get().strip()
        cfg   = self.cfg_var.get().strip()
        out   = self.out_var.get().strip()
        dash  = self.dash_var.get().strip()

        if not csv or not Path(csv).is_file():
            messagebox.showerror('Error', 'Input CSV file not found.')
            return
        if not out:
            out = str(Path(csv).parent / 'sicc_upm_output')

        out  = str(Path(out).resolve())
        dash = str(Path(dash or out).resolve())

        self._status.set('Processing…')
        self.update()

        threading.Thread(
            target=run_python_pipeline,
            args=(csv, cfg, '', out, dash,
                  self._set_status, self._on_done, self._on_error),
            daemon=True
        ).start()

    def _set_status(self, msg: str):
        self.after(0, lambda: self._status.set(msg))

    def _on_done(self, html_path: str):
        self.after(0, lambda: self._status.set(f'Done → {html_path}'))
        webbrowser.open(Path(html_path).as_uri())

    def _on_error(self, msg: str):
        self.after(0, lambda: messagebox.showerror('Error', msg))


# ---------------------------------------------------------------------------
# Headless entry point — compatible with _loader.py dispatch
# ---------------------------------------------------------------------------
def _run_headless(args: list) -> None:
    """Run the Python pipeline without any GUI.

    Called when invoked via::

        python _loader.py run_py_dashboard --headless \\
            --csv-file   <data.csv>          \\
            --output-dir <folder>            \\
            [--config    <testlist.json>]    \\
            [--target-csv <targets.csv>]     \\
            [--dashboard-dir <folder>]       \\
            [--product-config <cfg.json>]

    Stdout lines emitted on completion (same protocol as run_dashboard.py)::

        SICC_DASHBOARD: <abs-path-to-html>
    """
    import argparse as _ap

    p = _ap.ArgumentParser(prog='run_py_dashboard.py --headless')
    p.add_argument('--csv-file',       required=True,  help='Input sort CSV')
    p.add_argument('--config',         default='',     help='Testlist .json/.jsl (optional)')
    p.add_argument('--target-csv',     default='',     help='SICC target CSV (optional)')
    p.add_argument('--output-dir',     required=True,  help='Output folder')
    p.add_argument('--dashboard-dir',  default='',     help='Dashboard folder (defaults to output-dir)')
    p.add_argument('--product-config', default='',     help='Product Config JSON (optional)')
    opts, _unknown = p.parse_known_args(args)

    result: dict = {}
    import threading as _thr
    done_evt = _thr.Event()

    def _done(html_path):
        result['html'] = html_path
        done_evt.set()

    def _error(msg):
        result['error'] = msg
        done_evt.set()

    t = _thr.Thread(
        target=run_python_pipeline,
        args=(opts.csv_file, opts.config, opts.target_csv,
              opts.output_dir, opts.dashboard_dir or opts.output_dir,
              lambda m: print(m, flush=True), _done, _error,
              opts.product_config),
        daemon=False,
    )
    t.start()
    done_evt.wait()
    t.join(timeout=5)

    if 'error' in result:
        sys.exit(1)

    html_path = result.get('html', '')
    if html_path:
        print(f'SICC_DASHBOARD: {html_path}', flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    if '--headless' in sys.argv:
        _run_headless([a for a in sys.argv[1:] if a != '--headless'])
        return

    app = PyLauncher()
    app.mainloop()


if __name__ == '__main__':
    main()
