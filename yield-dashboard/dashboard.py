"""
dashboard.py  --  Unified Yield Analysis Dashboard GUI
=======================================================
Combines four tools in a single tabbed window:

  Pipeline   -- run the yield analysis pipeline       (pipeline.py / PipelineFrame)
  Compare    -- compare multiple run identifiers       (compareTP.py / CompareFrame)
  Manage     -- reorder / delete Dashboard.html runs   (manage_dashboard.py / ManageFrame)
  Portable   -- build a self-contained portable copy   (make_portable_dashboard.py)
  Wafer Map  -- generate per-IBIN wafer heatmaps       (WaferHeatmapFrame)

Each tab is fully independent; all four tools still work as standalone scripts.
"""

import os
import sys
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import http.server
import socketserver
import subprocess
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# -- ensure src/ is on the path -----------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(_SCRIPT_DIR, 'yld', 'src')
_LOADER = os.path.join(_SRC_DIR, '_loader.py')  # dispatches to compiled .pyd modules
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from pipeline            import PipelineFrame
from compareTP           import CompareFrame
from manage_dashboard    import ManageFrame
from trend_chart_frame   import TrendChartFrame
import make_portable_dashboard as _mpd

# ---------------------------------------------------------------------------
# Local HTTP opener server — lets HTML onclick open .jmpprj/.jmp via the
# Windows shell so the browser doesn't download the file instead.
# ---------------------------------------------------------------------------
_OPENER_PORT: int = 0


def start_opener_server() -> int:
    """Start a one-shot local HTTP server; return the port. Safe to call multiple times."""
    global _OPENER_PORT
    if _OPENER_PORT:
        return _OPENER_PORT

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            import urllib.parse as _up
            parsed = _up.urlparse(self.path)
            params = _up.parse_qs(parsed.query)
            path   = params.get("path", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if not path:
                self.wfile.write(b"no path"); return
            try:
                os.startfile(path)
                self.wfile.write(b"OK")
            except Exception as e:
                self.wfile.write(str(e).encode())

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    _OPENER_PORT = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return _OPENER_PORT

# -- Palette -------------------------------------------------------------------
BG   = '#1a252f'
BG2  = '#2c3e50'
FG   = '#ecf0f1'
FG2  = '#95a5a6'
ABLU = '#3498db'
GRN  = '#27ae60'


# -- Wafer Map tab ------------------------------------------------------------

class WaferHeatmapFrame(tk.Frame):
    """
    GUI wrapper for generate_bin_wafer_heatmaps.
    Generates per-IBIN wafer heatmaps for all interface bins present in the
    yield CSV that exceed their expected yield thresholds.
    """

    def __init__(self, parent=None, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._build_ui()

    def _build_ui(self):
        def _btn(parent, text, cmd, color=ABLU, acolor='#5dade2'):
            return tk.Button(parent, text=text, command=cmd,
                             bg=color, fg='white', activebackground=acolor,
                             relief='flat', cursor='hand2',
                             font=('Arial', 9), padx=8, pady=3)

        def _lf(text, color=FG2):
            f = tk.LabelFrame(self, text=text, bg=BG, fg=color,
                              font=('Arial', 8, 'bold'), padx=6, pady=4,
                              relief='groove', bd=1)
            return f

        def _row(parent, row, label, var, width=50, browse_cmd=None):
            tk.Label(parent, text=label, width=22, anchor='w',
                     bg=BG, fg=FG, font=('Arial', 9)).grid(
                row=row, column=0, sticky='w', pady=2, padx=(0, 4))
            e = tk.Entry(parent, textvariable=var, width=width,
                         bg=BG2, fg='white', insertbackground='white',
                         relief='flat', font=('Consolas', 9))
            e.grid(row=row, column=1, sticky='ew', pady=2, padx=(0, 4))
            parent.columnconfigure(1, weight=1)
            if browse_cmd:
                tk.Button(parent, text='...', command=browse_cmd, width=3,
                          bg='#1f618d', fg='white', activebackground=ABLU,
                          relief='flat', cursor='hand2').grid(
                    row=row, column=2, pady=2)

        tk.Label(self, text='Wafer Map  —  IBIN Heatmaps',
                 bg=BG, fg=ABLU, font=('Arial', 13, 'bold')
                 ).pack(fill='x', padx=10, pady=(8, 4))

        # ── Data inputs ───────────────────────────────────────────────────────
        frm_data = _lf('Data Inputs', ABLU)
        frm_data.pack(fill='x', padx=10, pady=(0, 4))

        self._csv_var     = tk.StringVar()
        self._fb_var      = tk.StringVar()
        self._bindef_var  = tk.StringVar()
        self._outdir_var  = tk.StringVar()

        _row(frm_data, 0, 'Yield CSV / gz / 7z:', self._csv_var,
             browse_cmd=lambda: self._browse_file(self._csv_var, [('Data files','*.csv *.csv.gz *.7z'),('All','*.*')]))
        _row(frm_data, 1, 'YieldTarget JSON/txt:', self._fb_var,
             browse_cmd=lambda: self._browse_file(self._fb_var,
                 [('JSON/txt','*.json;*.txt'),('All','*.*')]))
        _row(frm_data, 2, 'Bindef CSV (optional):', self._bindef_var,
             browse_cmd=lambda: self._browse_file(self._bindef_var, [('CSV','*.csv'),('All','*.*')]))
        _row(frm_data, 3, 'Output folder:',       self._outdir_var,
             browse_cmd=lambda: self._browse_dir(self._outdir_var))

        # ── Column options ─────────────────────────────────────────────────────
        frm_cols = _lf('Column Names  (edit only if CSV uses non-default names)', FG2)
        frm_cols.pack(fill='x', padx=10, pady=(0, 4))

        self._bincol_var  = tk.StringVar(value='INTERFACE_BIN_119325')
        self._sortx_var   = tk.StringVar(value='Sort_X')
        self._sorty_var   = tk.StringVar(value='Sort_Y')
        self._lotcol_var  = tk.StringVar(value='Lot')
        self._wafcol_var  = tk.StringVar(value='Wafer')

        _row(frm_cols, 0, 'IBIN column:',  self._bincol_var, width=40)
        _row(frm_cols, 1, 'Sort X column:', self._sortx_var,  width=20)
        _row(frm_cols, 2, 'Sort Y column:', self._sorty_var,  width=20)
        _row(frm_cols, 3, 'Lot column:',    self._lotcol_var, width=20)
        _row(frm_cols, 4, 'Wafer column:',  self._wafcol_var, width=20)

        # ── Options ────────────────────────────────────────────────────────────
        opt_row = tk.Frame(self, bg=BG)
        opt_row.pack(fill='x', padx=12, pady=(0, 4))
        self._force_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt_row, text='Force  (generate for ALL IBINs even when no expected yield)',
                       variable=self._force_var,
                       bg=BG, fg=FG2, selectcolor=BG2,
                       activebackground=BG, activeforeground=FG,
                       font=('Arial', 9)).pack(side='left')

        # ── Run button ─────────────────────────────────────────────────────────
        self._run_btn = _btn(self, 'Generate Wafer Heatmaps',
                             self._run, color=GRN, acolor='#2ecc71')
        self._run_btn.config(font=('Arial', 10, 'bold'), pady=5)
        self._run_btn.pack(fill='x', padx=10, pady=(2, 4))

        # ── Log ────────────────────────────────────────────────────────────────
        log_frm = _lf('Output', FG2)
        log_frm.pack(fill='both', expand=True, padx=10, pady=(0, 8))
        self._log = scrolledtext.ScrolledText(
            log_frm, height=12,
            font=('Consolas', 9), bg='#0d1b26', fg='#a8d8ea',
            relief='flat', insertbackground=FG, state='disabled')
        self._log.pack(fill='both', expand=True)

    # ── helpers ────────────────────────────────────────────────────────────────

    def _browse_file(self, var, filetypes):
        p = filedialog.askopenfilename(filetypes=filetypes)
        if p:
            var.set(p)
            # auto-fill output dir from CSV location
            if var is self._csv_var and not self._outdir_var.get():
                self._outdir_var.set(str(Path(p).parent / 'wafer_heatmaps'))

    def _browse_dir(self, var):
        d = filedialog.askdirectory()
        if d:
            var.set(d)

    def _log_write(self, msg: str):
        def _do():
            self._log.configure(state='normal')
            self._log.insert('end', msg)
            self._log.see('end')
            self._log.configure(state='disabled')
        self.after(0, _do)

    def _run(self):
        import subprocess
        csv_path = self._csv_var.get().strip()
        if not csv_path:
            messagebox.showwarning('No CSV', 'Select a Yield CSV file first.')
            return
        if not os.path.isfile(csv_path):
            messagebox.showerror('Not found', f'CSV not found:\n{csv_path}')
            return

        outdir = self._outdir_var.get().strip()
        if not outdir:
            outdir = str(Path(csv_path).parent / 'wafer_heatmaps')
            self._outdir_var.set(outdir)

        cmd = [
            sys.executable,
            _LOADER, 'generate_bin_wafer_heatmaps',
            '--data',   csv_path,
            '--outdir', outdir,
            '--bincol', self._bincol_var.get().strip() or 'INTERFACE_BIN_119325',
            '--sortx',  self._sortx_var.get().strip()  or 'Sort_X',
            '--sorty',  self._sorty_var.get().strip()  or 'Sort_Y',
            '--lotcol', self._lotcol_var.get().strip()  or 'Lot',
            '--wafercol', self._wafcol_var.get().strip() or 'Wafer',
        ]
        fb = self._fb_var.get().strip()
        if fb and os.path.isfile(fb):
            cmd += ['--failbuckets', fb]
        bindef = self._bindef_var.get().strip()
        if bindef and os.path.isfile(bindef):
            cmd += ['--bindef', bindef]
        if self._force_var.get():
            cmd.append('--force')

        self._run_btn.configure(state='disabled', text='Working...', bg=FG2)
        self._log_write(f'Running wafer heatmap generator...\n  CSV: {csv_path}\n  Out: {outdir}\n\n')

        def _worker():
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1)
                for line in proc.stdout:
                    self._log_write(line)
                proc.wait()
                if proc.returncode == 0:
                    self._log_write(f'\nDone. Heatmaps written to:\n  {outdir}\n')
                    try:
                        os.startfile(outdir)
                    except Exception:
                        pass
                else:
                    self._log_write(f'\nProcess exited with code {proc.returncode}\n')
            except Exception as exc:
                self._log_write(f'\nERROR: {exc}\n')
            finally:
                self.after(0, lambda: self._run_btn.configure(
                    state='normal', text='Generate Wafer Heatmaps', bg=GRN))

        threading.Thread(target=_worker, daemon=True).start()


# -- Portable tab --------------------------------------------------------------

class PortableFrame(tk.Frame):
    """
    GUI wrapper for make_portable_dashboard.make_portable().
    Lets the user pick a Dashboard.html, optionally set an output path,
    then build the self-contained portable file while streaming progress to a log.
    """

    def __init__(self, parent=None, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._build_ui()

    def _build_ui(self):
        def _btn(parent, text, cmd, color=ABLU, acolor='#5dade2'):
            return tk.Button(parent, text=text, command=cmd,
                             bg=color, fg='white', activebackground=acolor,
                             relief='flat', cursor='hand2',
                             font=('Arial', 9), padx=8, pady=3)

        def _lf(text, color=FG2):
            f = tk.LabelFrame(self, text=text, bg=BG, fg=color,
                              font=('Arial', 8, 'bold'), padx=6, pady=4,
                              relief='groove', bd=1)
            return f

        # Title
        tk.Label(self, text='Make Portable Dashboard',
                 bg=BG, fg=ABLU, font=('Arial', 13, 'bold')
                 ).pack(fill='x', padx=10, pady=(8, 4))

        # -- Input -------------------------------------------------------------
        frm_in = _lf('Dashboard.html to embed', ABLU)
        frm_in.pack(fill='x', padx=10, pady=(0, 4))

        row = tk.Frame(frm_in, bg=BG)
        row.pack(fill='x')
        self._in_var = tk.StringVar()
        tk.Entry(row, textvariable=self._in_var, width=56,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief='flat', font=('Consolas', 9)
                 ).pack(side='left', expand=True, fill='x', padx=(0, 4), pady=2)
        _btn(row, 'Browse...', self._browse_in).pack(side='left')

        # -- Output ------------------------------------------------------------
        frm_out = _lf('Output file  (leave blank = auto)', FG2)
        frm_out.pack(fill='x', padx=10, pady=(0, 4))

        row2 = tk.Frame(frm_out, bg=BG)
        row2.pack(fill='x')
        self._out_var = tk.StringVar()
        tk.Entry(row2, textvariable=self._out_var, width=56,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief='flat', font=('Consolas', 9)
                 ).pack(side='left', expand=True, fill='x', padx=(0, 4), pady=2)
        _btn(row2, '...', self._browse_out, color='#1f618d').pack(side='left')

        # -- Run button --------------------------------------------------------
        self._run_btn = _btn(self, 'Build Portable Copy',
                             self._run, color=GRN, acolor='#2ecc71')
        self._run_btn.config(font=('Arial', 10, 'bold'), pady=5)
        self._run_btn.pack(fill='x', padx=10, pady=(4, 4))

        # -- Log ---------------------------------------------------------------
        log_frm = _lf('Output', FG2)
        log_frm.pack(fill='both', expand=True, padx=10, pady=(0, 8))
        self._log = scrolledtext.ScrolledText(
            log_frm, height=12,
            font=('Consolas', 9), bg='#0d1b26', fg='#a8d8ea',
            relief='flat', insertbackground=FG, state='disabled')
        self._log.pack(fill='both', expand=True)

    # -- helpers ---------------------------------------------------------------

    def _browse_in(self):
        p = filedialog.askopenfilename(
            title='Select Dashboard.html',
            filetypes=[('HTML files', '*.html'), ('All files', '*.*')])
        if p:
            self._in_var.set(p)
            if not self._out_var.get():
                self._out_var.set(str(Path(p).parent / 'Dashboard_portable.html'))

    def _browse_out(self):
        p = filedialog.asksaveasfilename(
            title='Save portable file as',
            defaultextension='.html',
            filetypes=[('HTML files', '*.html')])
        if p:
            self._out_var.set(p)

    def _log_write(self, msg: str):
        def _do():
            self._log.configure(state='normal')
            self._log.insert('end', msg)
            self._log.see('end')
            self._log.configure(state='disabled')
        self.after(0, _do)

    def _run(self):
        in_path = self._in_var.get().strip()
        if not in_path:
            messagebox.showwarning('No input', 'Select a Dashboard.html file first.')
            return
        dash = Path(in_path)
        if not dash.exists():
            messagebox.showerror('Not found', f'File not found:\n{dash}')
            return
        out_str = self._out_var.get().strip()
        out_path = Path(out_str) if out_str else None

        self._run_btn.configure(state='disabled', text='Working...', bg=FG2)
        self._log_write(f'Building portable copy of {dash.name} ...\n')

        def _worker():
            import io, contextlib
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    result = _mpd.make_portable(dash, out_path)
                self._log_write(buf.getvalue())
                self._log_write(f'\nDone -> {result}\n')
            except Exception as exc:
                self._log_write(buf.getvalue())
                self._log_write(f'\nERROR: {exc}\n')
            finally:
                self.after(0, lambda: self._run_btn.configure(
                    state='normal', text='Build Portable Copy', bg=GRN))

        threading.Thread(target=_worker, daemon=True).start()


# -- Combined App --------------------------------------------------------------


# -- Combined App --------------------------------------------------------------

class DashboardApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Yield Analysis Dashboard')
        self.geometry('1280x800')
        self.configure(bg=BG)

        # Notebook style
        style = ttk.Style(self)
        style.theme_use('default')
        style.configure('App.TNotebook',
                        background=BG, borderwidth=0, tabmargins=[2, 4, 2, 0])
        style.configure('App.TNotebook.Tab',
                        background='#253545', foreground=FG2,
                        padding=[14, 5], font=('Arial', 9, 'bold'),
                        borderwidth=0)
        style.map('App.TNotebook.Tab',
                  background=[('selected', BG), ('active', BG2)],
                  foreground=[('selected', ABLU), ('active', FG)])

        # Watermark — packed before notebook so it isn't covered
        _wm = tk.Label(self, text='Pant, Sujit N — GEMS FTE',
                       bg=BG, fg=ABLU, font=('Arial', 8, 'bold'),
                       padx=6, pady=2)
        _wm.pack(side='bottom', anchor='w')

        nb = ttk.Notebook(self, style='App.TNotebook')
        nb.pack(fill='both', expand=True, padx=0, pady=0)

        self._pipeline_tab = PipelineFrame(nb)
        self._compare_tab  = CompareFrame(nb)
        self._trend_tab    = TrendChartFrame(nb)
        self._manage_tab   = ManageFrame(nb)
        self._portable_tab = PortableFrame(nb)
        self._wafer_tab    = WaferHeatmapFrame(nb)

        nb.add(self._pipeline_tab, text='   Create   ')
        nb.add(self._compare_tab,  text='  Compare   ')
        nb.add(self._trend_tab,    text='Yield Trend ')
        nb.add(self._manage_tab,   text='   Manage   ')
        nb.add(self._portable_tab, text='  Portable  ')
        nb.add(self._wafer_tab,    text=' Wafer Map  ')

        # When switching to Manage or Compare, pre-fill Dashboard.html if known

    def _on_tab_change(self, _evt=None):
        """Propagate last known Dashboard.html path to Manage and Compare tabs."""
        try:
            dash = getattr(self._pipeline_tab, '_last_dashboard_html', None)
            if not dash:
                return
            if not self._compare_tab._dash_path.get():
                self._compare_tab._dash_path.set(dash)
            if not self._manage_tab._path_var.get():
                self._manage_tab._path_var.set(dash)
        except Exception:
            pass


if __name__ == '__main__':
    start_opener_server()   # start before GUI so port is ready
    DashboardApp().mainloop()
