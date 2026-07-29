#!/usr/bin/env python3
"""
VccCont BIN8 Dashboard — GUI Launcher
Run: python gui.py
"""
import os, sys, subprocess, threading, zipfile, tempfile, shutil, json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ---------------------------------------------------------------------------
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
GENERATE_PY   = os.path.join(SCRIPT_DIR, 'generate_dashboard.py')
DEFAULT_CSV   = (r'C:\scripts\app.yield.nvl\docs\issue_tracker\parametric'
                 r'\vcc_cont_bin8\data\61A-61B-Yield.CSV')

DEFAULT_PROG  = r'I:\program\1001\prod\hdmtprogs\nvl_ncx_sds\NCXSDJXL0H61C002620'

# colours (dark theme matching the dashboard)
BG      = '#0d1525'
BG2     = '#141c2e'
BG3     = '#1a2235'
BORDER  = '#1e3050'
FG      = '#c0ccd8'
FG_DIM  = '#556677'
ACCENT  = '#4a9fd4'
GREEN   = '#4ecdc4'
RED     = '#ff6b6b'
GOLD    = '#ffd166'
FONT    = ('Segoe UI', 10)
FONT_SM = ('Segoe UI', 9)
FONT_HD = ('Segoe UI', 12, 'bold')
FONT_MN = ('Segoe UI', 9)
# ---------------------------------------------------------------------------


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('VccCont BIN8 Dashboard Generator')
        self.resizable(True, True)
        self.minsize(620, 520)
        self.configure(bg=BG)
        self._last_html = tk.StringVar(value='')   # path to last generated HTML

        self._build_ui()
        self._refresh_open_btn()

        # pre-fill hardcoded defaults only (no auto-load of setup.json)
        if os.path.isfile(DEFAULT_CSV):
            self._csv_var.set(DEFAULT_CSV)
        if os.path.isdir(DEFAULT_PROG):
            self._prog_var.set(DEFAULT_PROG)

    # ── UI construction ─────────────────────────────────────────────────────
    def _build_ui(self):
        pad = dict(padx=18, pady=0)

        # ── header ──
        hdr = tk.Frame(self, bg=BG, pady=16)
        hdr.pack(fill='x', **pad)
        tk.Label(hdr, text='VccCont BIN8 Dashboard', font=FONT_HD,
                 bg=BG, fg=ACCENT).pack(anchor='w')
        tk.Label(hdr, text='Generate and open the BIN8 failure analysis dashboard from a yield CSV or ZIP export.',
                 font=FONT_SM, bg=BG, fg=FG_DIM, wraplength=560, justify='left').pack(anchor='w', pady=(2, 0))

        sep = tk.Frame(self, bg=BORDER, height=1)
        sep.pack(fill='x', padx=18, pady=(0, 12))

        # ── input card ──
        card = self._card(self, 'Inputs')
        card.pack(fill='x', padx=18, pady=(0, 10))

        # CSV / ZIP row
        self._csv_var = tk.StringVar()
        self._make_file_row(card, 'CSV / ZIP file', self._csv_var,
                            self._browse_csv,
                            tip='Yield CSV export (e.g. 61A-61B-Yield.CSV) or a ZIP containing it')

        # Test program — hidden, used as fallback when prog_root not set
        self._prog_var = tk.StringVar()

        # Program root folder row
        self._prog_root_var = tk.StringVar()
        self._make_file_row(card, 'Program root', self._prog_root_var,
                            self._browse_prog_root,
                            tip='Folder containing one subfolder per program (e.g. nvl_ncx_sds).\nEach program uses its own limits, flow, and force voltages.')

        # Output folder row
        self._out_var = tk.StringVar()
        self._make_file_row(card, 'Output folder', self._out_var,
                            self._browse_out,
                            tip='Dashboard HTML will be written here as vcccont-bin8-analysis.html')

        # ── Setup JSON row ──
        sep2 = tk.Frame(card, bg=BORDER, height=1)
        sep2.pack(fill='x', padx=14, pady=(4, 8))
        setup_row = tk.Frame(card, bg=BG2)
        setup_row.pack(fill='x', padx=14, pady=(0, 10))
        tk.Label(setup_row, text='Setup file', font=FONT_SM, bg=BG2, fg=FG,
                 width=14, anchor='w').pack(side='left')
        self._setup_var = tk.StringVar(value='')
        setup_entry = tk.Entry(setup_row, textvariable=self._setup_var,
                               bg=BG3, fg=FG, insertbackground=FG,
                               relief='flat', bd=0, font=FONT_SM,
                               highlightbackground=BORDER, highlightthickness=1)
        setup_entry.pack(side='left', fill='x', expand=True, ipady=5, padx=(0, 8))
        self._btn(setup_row, '📂  Load', self._on_load_setup, GOLD).pack(side='left', padx=(0, 6))
        self._btn(setup_row, '💾  Save', self._on_save_setup, GREEN).pack(side='left')
        tk.Label(card, text='Load or save all paths above as a JSON preset file.',
                 font=('Segoe UI', 8), bg=BG2, fg=FG_DIM, anchor='w'
                 ).pack(fill='x', padx=14, pady=(0, 8))

        # ── Live Mode checkbox ──
        live_row = tk.Frame(card, bg=BG2)
        live_row.pack(fill='x', padx=14, pady=(0, 10))
        self._live_mode_var = tk.BooleanVar(value=False)
        tk.Checkbutton(live_row,
                       text='⚡ Live Mode — embed raw data for interactive pin inspect',
                       variable=self._live_mode_var,
                       bg=BG2, fg=FG, selectcolor=BG3,
                       activebackground=BG2, activeforeground=FG,
                       font=FONT_SM).pack(side='left')

        # ── actions ──
        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(fill='x', padx=18, pady=(4, 0))

        self._gen_btn = self._btn(btn_frame, '⚙  Generate Dashboard',
                                  self._on_generate, ACCENT)
        self._gen_btn.pack(side='left', padx=(0, 10))

        self._open_btn = self._btn(btn_frame, '▶  Open Dashboard',
                                   self._on_open, GREEN)
        self._open_btn.pack(side='left')

        self._cancel_btn = self._btn(btn_frame, '✕  Cancel',
                                     self._on_cancel, RED)
        self._cancel_btn.pack(side='left', padx=(10, 0))
        self._cancel_btn.config(state='disabled')

        # ── progress bar ──
        pb_frame = tk.Frame(self, bg=BG)
        pb_frame.pack(fill='x', padx=18, pady=(10, 0))
        self._pb = ttk.Progressbar(pb_frame, mode='indeterminate', length=580)
        self._pb.pack(fill='x')

        # ── log area ──
        log_label = tk.Label(self, text='Log', font=FONT_SM, bg=BG, fg=FG_DIM, anchor='w')
        log_label.pack(fill='x', padx=18, pady=(10, 2))

        log_frame = tk.Frame(self, bg=BG3, bd=1, relief='flat',
                             highlightbackground=BORDER, highlightthickness=1)
        log_frame.pack(fill='both', expand=True, padx=18, pady=(0, 16))

        self._log = tk.Text(log_frame, bg=BG3, fg=FG, font=('Consolas', 9),
                            relief='flat', bd=0, state='disabled',
                            wrap='word', insertbackground=FG)
        vsb = ttk.Scrollbar(log_frame, orient='vertical', command=self._log.yview)
        self._log.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        self._log.pack(side='left', fill='both', expand=True, padx=6, pady=6)

        # tag colours
        self._log.tag_config('ok',   foreground=GREEN)
        self._log.tag_config('err',  foreground=RED)
        self._log.tag_config('warn', foreground=GOLD)
        self._log.tag_config('dim',  foreground=FG_DIM)
        self._log.tag_config('acc',  foreground=ACCENT)

        self._proc = None

    def _card(self, parent, title):
        outer = tk.Frame(parent, bg=BG2, bd=1, relief='flat',
                         highlightbackground=BORDER, highlightthickness=1)
        tk.Label(outer, text=title, font=('Segoe UI', 9, 'bold'),
                 bg=BG2, fg=ACCENT).pack(anchor='w', padx=14, pady=(10, 4))
        return outer

    def _btn(self, parent, text, cmd, color):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=BG3, fg=color, activebackground=BG2, activeforeground=color,
                      relief='flat', bd=0, font=FONT_SM, padx=14, pady=7,
                      cursor='hand2', highlightbackground=BORDER, highlightthickness=1)
        return b

    def _make_file_row(self, parent, label, var, browse_cmd, tip=''):
        row = tk.Frame(parent, bg=BG2)
        row.pack(fill='x', padx=14, pady=(0, 10))

        tk.Label(row, text=label, font=FONT_SM, bg=BG2, fg=FG,
                 width=14, anchor='w').pack(side='left')

        entry = tk.Entry(row, textvariable=var, bg=BG3, fg=FG,
                         insertbackground=FG, relief='flat', bd=0,
                         font=FONT_SM, highlightbackground=BORDER, highlightthickness=1)
        entry.pack(side='left', fill='x', expand=True, ipady=5, padx=(0, 8))

        browse = tk.Button(row, text='Browse…', command=browse_cmd,
                           bg=BG3, fg=ACCENT, activebackground=BG2, activeforeground=ACCENT,
                           relief='flat', bd=0, font=FONT_SM, padx=10, pady=4,
                           cursor='hand2', highlightbackground=BORDER, highlightthickness=1)
        browse.pack(side='left')

        if tip:
            tk.Label(parent, text=tip, font=('Segoe UI', 8), bg=BG2,
                     fg=FG_DIM, anchor='w').pack(fill='x', padx=14, pady=(0, 6))

    # ── file dialogs ────────────────────────────────────────────────────────
    def _browse_csv(self):
        p = filedialog.askopenfilename(
            title='Select yield CSV or ZIP',
            filetypes=[('CSV / ZIP', '*.csv *.zip'), ('CSV', '*.csv'), ('ZIP', '*.zip'), ('All', '*.*')])
        if p:
            self._csv_var.set(p)

    def _browse_prog_root(self):
        _cur = self._prog_root_var.get().strip()
        _init = _cur
        while _init and not os.path.isdir(_init):
            _init = os.path.dirname(_init)
        p = filedialog.askdirectory(
            title='Select program root folder (containing one subfolder per program)',
            initialdir=_init or os.path.expanduser('~'))
        if p:
            self._prog_root_var.set(p)

    def _browse_out(self):
        p = filedialog.askdirectory(title='Select output folder')
        if p:
            self._out_var.set(p)

    # ── setup JSON load / save ───────────────────────────────────────────────
    def _on_load_setup(self):
        _cur = self._setup_var.get().strip()
        path = filedialog.askopenfilename(
            title='Load setup JSON',
            initialdir=os.path.dirname(_cur) if _cur else os.path.expanduser('~'),
            initialfile=os.path.basename(_cur) if _cur else 'setup.json',
            filetypes=[('JSON', '*.json'), ('All', '*.*')])
        if not path:
            return
        try:
            with open(path, encoding='utf-8') as _f:
                data = json.load(_f)
        except Exception as e:
            messagebox.showerror('Load failed', f'Could not read setup file:\n{e}')
            return
        self._setup_var.set(path)
        if data.get('csv'):       self._csv_var.set(data['csv'])
        if data.get('prog'):      self._prog_var.set(data['prog'])
        if data.get('prog_root'): self._prog_root_var.set(data['prog_root'])
        if data.get('out'):       self._out_var.set(data['out'])
        if 'live_mode' in data:   self._live_mode_var.set(bool(data['live_mode']))
        self._log_line(f'[setup] Loaded: {path}', 'acc')

    def _on_save_setup(self):
        _cur = self._setup_var.get().strip()
        path = filedialog.asksaveasfilename(
            title='Save setup JSON',
            initialdir=os.path.dirname(_cur) if _cur else os.path.expanduser('~'),
            initialfile=os.path.basename(_cur) if _cur else 'setup.json',
            defaultextension='.json',
            filetypes=[('JSON', '*.json'), ('All', '*.*')])
        if not path:
            return
        try:
            data = {}
            if os.path.isfile(path):
                with open(path, encoding='utf-8') as _f:
                    data = json.load(_f)
        except Exception:
            data = {}
        data['csv']       = self._csv_var.get().strip()
        data['prog']      = self._prog_var.get().strip()
        data['prog_root'] = self._prog_root_var.get().strip()
        data['out']       = self._out_var.get().strip()
        data['live_mode'] = self._live_mode_var.get()
        try:
            with open(path, 'w', encoding='utf-8') as _f:
                json.dump(data, _f, indent=2)
            self._setup_var.set(path)
            self._log_line(f'[setup] Saved: {path}', 'ok')
        except Exception as e:
            messagebox.showerror('Save failed', f'Could not write setup file:\n{e}')

    # ── helpers ─────────────────────────────────────────────────────────────
    def _log_write(self, text, tag=''):
        def _do():
            self._log.config(state='normal')
            self._log.insert('end', text, tag)
            self._log.see('end')
            self._log.config(state='disabled')
        self.after(0, _do)

    def _log_line(self, text, tag=''):
        self._log_write(text + '\n', tag)

    def _refresh_open_btn(self):
        html = self._last_html.get()
        self._open_btn.config(state='normal' if (html and os.path.isfile(html)) else 'disabled')

    # ── extract CSV from ZIP ─────────────────────────────────────────────────
    def _resolve_csv(self, path):
        """If path is a ZIP, extract the first .csv inside to a temp dir and return its path."""
        if path.lower().endswith('.zip'):
            self._log_line(f'Extracting CSV from ZIP: {os.path.basename(path)}', 'dim')
            tmp = tempfile.mkdtemp(prefix='vcccont_')
            with zipfile.ZipFile(path, 'r') as z:
                csvs = [n for n in z.namelist() if n.lower().endswith('.csv')]
                if not csvs:
                    raise ValueError('No .csv file found inside the ZIP archive.')
                if len(csvs) > 1:
                    # prefer one with "yield" in name
                    pref = [c for c in csvs if 'yield' in c.lower()]
                    chosen = pref[0] if pref else csvs[0]
                else:
                    chosen = csvs[0]
                z.extract(chosen, tmp)
                self._log_line(f'  Using: {chosen}', 'dim')
                self._tmp_dir = tmp
                return os.path.join(tmp, chosen)
        return path

    # ── generate ─────────────────────────────────────────────────────────────
    def _on_generate(self):
        csv_path = self._csv_var.get().strip()
        out_dir  = self._out_var.get().strip()

        if not csv_path:
            messagebox.showerror('Missing input', 'Please select a CSV or ZIP file.')
            return
        if not os.path.isfile(csv_path):
            messagebox.showerror('File not found', f'Cannot find:\n{csv_path}')
            return
        if not out_dir:
            messagebox.showerror('Missing output', 'Please select an output folder.')
            return

        # If user put a file path instead of a folder, use its parent directory
        if out_dir.lower().endswith('.html') or os.path.isfile(out_dir):
            out_dir = os.path.dirname(out_dir)

        os.makedirs(out_dir, exist_ok=True)
        out_html = os.path.join(out_dir, 'vcccont-bin8-analysis.html')

        # clear log
        self._log.config(state='normal')
        self._log.delete('1.0', 'end')
        self._log.config(state='disabled')

        self._gen_btn.config(state='disabled')
        self._cancel_btn.config(state='normal')
        self._pb.start(12)
        self._tmp_dir = None

        def _worker():
            try:
                resolved_csv = self._resolve_csv(csv_path)
                self._log_line(f'Input:  {resolved_csv}', 'dim')
                self._log_line(f'Output: {out_html}', 'dim')
                self._log_line('─' * 60, 'dim')

                prog_dir      = self._prog_var.get().strip()
                prog_root_dir = self._prog_root_var.get().strip()
                cmd = [sys.executable, GENERATE_PY,
                       '--csv', resolved_csv,
                       '--out', out_html]
                if prog_root_dir:
                    cmd += ['--prog-root', prog_root_dir]
                elif prog_dir:
                    cmd += ['--prog', prog_dir]
                cmd += ['--no-gui']
                if self._live_mode_var.get():
                    cmd += ['--live-mode']

                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=SCRIPT_DIR,
                )

                for line in self._proc.stdout:
                    line = line.rstrip()
                    tag = ''
                    ll = line.lower()
                    if 'error' in ll or 'traceback' in ll or 'exception' in ll:
                        tag = 'err'
                    elif 'warning' in ll or 'warn' in ll:
                        tag = 'warn'
                    elif 'dashboard written' in ll:
                        tag = 'ok'
                    elif line.startswith('  [') or line.startswith('  Done'):
                        tag = 'dim'
                    self._log_line(line, tag)

                rc = self._proc.wait()

                def _done():
                    self._pb.stop()
                    self._gen_btn.config(state='normal')
                    self._cancel_btn.config(state='disabled')
                    if rc == 0:
                        # prefer index.html (multi-program run) over single-file path
                        _index = os.path.join(out_dir, 'index.html')
                        _open_target = _index if os.path.isfile(_index) else out_html
                        self._last_html.set(_open_target)
                        # auto-save removed — use Load/Save buttons manually
                        self._log_line('', '')
                        self._log_line(f'✔  Dashboard written: {out_html}', 'ok')
                        self._refresh_open_btn()
                    elif rc != 0:
                        self._log_line(f'✘  Process exited with code {rc}', 'err')
                    # clean up temp dir
                    if self._tmp_dir and os.path.isdir(self._tmp_dir):
                        shutil.rmtree(self._tmp_dir, ignore_errors=True)

                self.after(0, _done)

            except Exception as exc:
                def _err():
                    self._pb.stop()
                    self._gen_btn.config(state='normal')
                    self._cancel_btn.config(state='disabled')
                    self._log_line(f'✘  {exc}', 'err')
                self.after(0, _err)

        threading.Thread(target=_worker, daemon=True).start()

    # ── cancel ────────────────────────────────────────────────────────────────
    def _on_cancel(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._log_line('⚠  Cancelled by user.', 'warn')
        self._pb.stop()
        self._gen_btn.config(state='normal')
        self._cancel_btn.config(state='disabled')

    # ── open ─────────────────────────────────────────────────────────────────
    def _on_open(self):
        html = self._last_html.get()
        if not html or not os.path.isfile(html):
            messagebox.showinfo('Not found', 'Generate the dashboard first.')
            return
        os.startfile(html)


# ── style ttk ──────────────────────────────────────────────────────────────
def _apply_style():
    s = ttk.Style()
    s.theme_use('clam')
    s.configure('TScrollbar', background=BG3, troughcolor=BG2,
                 bordercolor=BORDER, arrowcolor=FG_DIM)
    s.configure('TProgressbar', troughcolor=BG2, background=ACCENT,
                 bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)


# ── entry point ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app = App()
    _apply_style()
    # centre window
    app.update_idletasks()
    w, h = 680, 560
    sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
    app.geometry(f'{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}')
    app.mainloop()
