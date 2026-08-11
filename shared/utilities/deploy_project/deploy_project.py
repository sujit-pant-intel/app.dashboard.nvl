"""
deploy.py
---------
GUI tool to compile Python source files with Cython and deploy to a share.

.py files are compiled to native .pyd binaries via Cython (free, fast ~10-30s).
Non-.py files (.jsl, .json, .txt, .csv, …) are always copied as-is.

Install:
    python -m pip install --user cython setuptools
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext
from tkinter import ttk

# ── Colours ───────────────────────────────────────────────────────────────────
BG       = '#1a252f'
BG2      = '#2c3e50'
FG       = '#ecf0f1'
BTN_BG   = '#2980b9'
BTN_FG   = '#ffffff'
ENTRY_BG = '#34495e'
GREEN    = '#27ae60'

_SKIP_EXTS   = {'.log', '.pyc', '.pyo', '.pyd', '.so', '.c', '.spec', '.md'}
_SKIP_PREFIX = ('__',)
_SKIP_NAMES  = {'obfuscate_and_deploy.py'}
# Folder names to exclude from the source glob entirely
_SKIP_DIRS   = {'.venv', 'venv', 'env', '.env', '__pycache__', '.git', 'node_modules',
                'dist', 'build', '.tox', '.mypy_cache', '.idea', '.vscode',
                # Large runtime data folders — not code/config, deployed separately or pre-existing
                '9-sites', 'full-sites', 'raw-data',
                # Not yet deploy-ready dashboards
                'vmin-dashboard', 'class-dashboard',
                # Dev-only docs
                'readme', 'readme/'}


class DeployApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Protect & Deploy')
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(660, 560)
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        pad = {'padx': 10, 'pady': 4}

        # Source folder
        tk.Label(self, text='Source folder  (scripts to protect)',
                 bg=BG, fg=FG, anchor='w').pack(fill='x', **pad)
        r = tk.Frame(self, bg=BG); r.pack(fill='x', padx=10, pady=2)
        self.src_var = tk.StringVar()
        tk.Entry(r, textvariable=self.src_var, bg=ENTRY_BG, fg=FG,
                 insertbackground=FG, relief='flat', bd=4).pack(side='left', fill='x', expand=True)
        tk.Button(r, text='Browse…', bg=BTN_BG, fg=BTN_FG, relief='flat', bd=0, padx=8,
                  command=self._browse_src).pack(side='left', padx=(6, 0))

        # Deploy folder
        tk.Label(self, text='Deploy folder  (network share or local destination)',
                 bg=BG, fg=FG, anchor='w').pack(fill='x', **pad)
        r2 = tk.Frame(self, bg=BG); r2.pack(fill='x', padx=10, pady=2)
        self.dst_var = tk.StringVar()
        tk.Entry(r2, textvariable=self.dst_var, bg=ENTRY_BG, fg=FG,
                 insertbackground=FG, relief='flat', bd=4).pack(side='left', fill='x', expand=True)
        tk.Button(r2, text='Browse…', bg=BTN_BG, fg=BTN_FG, relief='flat', bd=0, padx=8,
                  command=self._browse_dst).pack(side='left', padx=(6, 0))

        # Extra source folders (merged into run/ alongside primary source)
        tk.Label(self, text='Extra source folders  (merged into run/ — one per line; e.g. sibling etest-dashboard)',
                 bg=BG, fg='#95a5a6', anchor='w', font=('Segoe UI', 8)).pack(fill='x', padx=10, pady=(6,0))
        rx = tk.Frame(self, bg=BG); rx.pack(fill='x', padx=10, pady=2)
        self.extra_src_var = tk.StringVar()
        tk.Entry(rx, textvariable=self.extra_src_var, bg=ENTRY_BG, fg=FG,
                 insertbackground=FG, relief='flat', bd=4).pack(side='left', fill='x', expand=True)
        tk.Button(rx, text='Add…', bg=BTN_BG, fg=BTN_FG, relief='flat', bd=0, padx=8,
                  command=self._browse_extra_src).pack(side='left', padx=(6, 0))

        # Entry module
        tk.Label(self, text='Entry module  (compiled module to launch)',
                 bg=BG, fg=FG, anchor='w').pack(fill='x', **pad)
        r3 = tk.Frame(self, bg=BG); r3.pack(fill='x', padx=10, pady=2)
        self.entry_var = tk.StringVar(value='dashboard')
        self._entry_cb = ttk.Combobox(r3, textvariable=self.entry_var, state='normal')
        self._entry_cb.pack(side='left', fill='x', expand=True)
        tk.Label(r3, text='must expose main()', bg=BG, fg='#7f8c8d',
                 font=('Segoe UI', 8)).pack(side='left', padx=(8, 0))

        # Options frame
        opt = tk.LabelFrame(self, text=' Options ', bg=BG, fg=FG, relief='groove', bd=1)
        opt.pack(fill='x', padx=10, pady=8)

        self.compile_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt, text='Compile .py → .pyd with Cython  (uncheck to copy .py files as-is)',
                       variable=self.compile_var,
                       bg=BG, fg=FG, selectcolor=BG2,
                       activebackground=BG, activeforeground=FG).pack(anchor='w', padx=8, pady=2)

        self.copy_non_py_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt, text='Also copy non-.py files  (.jsl, .json, .txt, .csv, …)',
                       variable=self.copy_non_py_var,
                       bg=BG, fg=FG, selectcolor=BG2,
                       activebackground=BG, activeforeground=FG).pack(anchor='w', padx=8, pady=2)

        self.clear_dst_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt, text='Clear deploy folder before copying',
                       variable=self.clear_dst_var,
                       bg=BG, fg=FG, selectcolor=BG2,
                       activebackground=BG, activeforeground=FG).pack(anchor='w', padx=8, pady=2)

        self.recursive_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt, text='Recurse into sub-folders',
                       variable=self.recursive_var,
                       bg=BG, fg=FG, selectcolor=BG2,
                       activebackground=BG, activeforeground=FG).pack(anchor='w', padx=8, pady=2)

        # Run button
        self._run_btn = tk.Button(self, text='▶  Protect & Deploy',
                                  bg=GREEN, fg=BTN_FG, relief='flat', bd=0,
                                  font=('Segoe UI', 10, 'bold'), padx=16, pady=6,
                                  command=self._start)
        self._run_btn.pack(pady=(6, 2))

        # Log
        tk.Label(self, text='Log', bg=BG, fg=FG, anchor='w').pack(fill='x', padx=10)
        self.log = scrolledtext.ScrolledText(
            self, bg='#0d1b2a', fg='#a8d8ea',
            font=('Consolas', 9), relief='flat', bd=4, state='disabled')
        self.log.pack(fill='both', expand=True, padx=10, pady=(2, 10))
        self.log.tag_config('ok',    foreground='#2ecc71')
        self.log.tag_config('err',   foreground='#e74c3c')
        self.log.tag_config('info',  foreground='#f39c12')
        self.log.tag_config('plain', foreground='#a8d8ea')

    # ── Browse ────────────────────────────────────────────────────────────────

    def _browse_src(self):
        d = filedialog.askdirectory(title='Select source folder')
        if d:
            self.src_var.set(d)
            self._refresh_entry_choices(d)

    def _browse_extra_src(self):
        d = filedialog.askdirectory(title='Select extra source folder to merge into run/')
        if d:
            cur = self.extra_src_var.get().strip()
            self.extra_src_var.set((cur + '\n' + d).strip())

    def _refresh_entry_choices(self, folder: str):
        """Populate the entry-module combobox with .py stems from folder."""
        p = Path(folder)
        stems = sorted(f.stem for f in p.glob('*.py')
                       if not f.stem.startswith(_SKIP_PREFIX))
        self._entry_cb['values'] = stems
        if stems and self.entry_var.get() not in stems:
            self.entry_var.set(stems[0])

    def _browse_dst(self):
        d = filedialog.askdirectory(title='Select deploy / share folder')
        if d:
            self.dst_var.set(d)

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log(self, msg: str, tag: str = 'plain'):
        self.log.configure(state='normal')
        self.log.insert('end', msg + '\n', tag)
        self.log.see('end')
        self.log.configure(state='disabled')
        self.update_idletasks()

    # ── Entry point ───────────────────────────────────────────────────────────

    def _start(self):
        src = self.src_var.get().strip()
        dst = self.dst_var.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror('Error', 'Source folder not found.')
            return
        if not dst:
            messagebox.showerror('Error', 'Please choose a deploy folder.')
            return
        self._run_btn.configure(state='disabled', text='Running…')
        threading.Thread(target=self._run, args=(src, dst), daemon=True).start()

    def _run(self, src: str, dst: str):
        try:
            self._do_run(src, dst)
        except Exception as exc:
            self._log(f'Unexpected error: {exc}', 'err')
            import traceback
            self._log(traceback.format_exc(), 'err')
        finally:
            self.after(0, lambda: self._run_btn.configure(
                state='normal', text='▶  Protect & Deploy'))

    # ── Main orchestration ────────────────────────────────────────────────────

    def _do_run(self, src: str, dst: str):
        import tempfile
        src_p   = Path(src)
        dst_p   = Path(dst)
        # Extra source folders — each is merged into run/ preserving relative structure
        _extra_dirs = [
            Path(p.strip()) for p in self.extra_src_var.get().splitlines()
            if p.strip() and os.path.isdir(p.strip())
        ]
        glob_fn = src_p.rglob if self.recursive_var.get() else src_p.glob

        # Delete output folder first, before any compilation work
        if self.clear_dst_var.get() and dst_p.exists():
            self._log('Deleting deploy folder…', 'info')
            def _force_remove(func, path, exc):
                import stat
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except Exception:
                    pass
            shutil.rmtree(dst_p, onexc=_force_remove)
            self._log('  Done.', 'ok')

        # Collect .py files — skip virtual-env and build folders
        py_files = sorted(
            f for f in glob_fn('*.py')
            if not any(part in _SKIP_DIRS for part in f.relative_to(src_p).parts)
        )
        # Collect .py files from extra source folders (flat — placed in run/<extra_dir_name>/)
        for _xd in _extra_dirs:
            _xd_name = _xd.name
            for _xf in sorted(_xd.rglob('*.py') if self.recursive_var.get() else _xd.glob('*.py')):
                if not any(part in _SKIP_DIRS for part in _xf.relative_to(_xd).parts):
                    py_files.append(_xf)
        if not py_files:
            self._log('No .py files found in source folder.', 'err')
            return
        self._log(f'Found {len(py_files)} .py file(s).', 'info')

        # Compile all .py files except _loader.py and _run.py (which must stay as .py)
        # _loader.py dispatches imports; _run.py is the entry stub — both are plain launchers.
        _NO_COMPILE = {
            '_loader.py',
            '_run.py',
            'obfuscate_and_deploy.py',
            'setup.py',
            # Invoked by filename in deployed workflows; keep as real .py files.
            'parametric_runner.py',       # launched via sys.executable <path>/parametric_runner.py
            'generate_heatmap_from_csv.py',
            'bin_distribution_html.py',
            'generate_plots_from_csv.py',
            'parse_bindef_to_crystalball.py',
            'get_dd_update.py',
        }
        compile_files = [f for f in py_files if f.name not in _NO_COMPILE]
        passthru_files = [f for f in py_files if f.name in _NO_COMPILE]

        tmp_dir = Path(tempfile.mkdtemp(prefix='deploy_tmp_'))
        self._log(f'Temp dir: {tmp_dir}', 'info')

        try:
            if self.compile_var.get() and compile_files:
                self._log(f'Compiling {len(compile_files)} .py file(s) → .pyd…', 'info')
                protected = self._run_cython(compile_files, src_p, tmp_dir)
            else:
                protected = {}

            # Pass-through files go as .py
            for f in passthru_files:
                protected[f.relative_to(src_p)] = f

            if not protected:
                self._log('No files were protected successfully.', 'err')
                return

            # Prepare deploy folder — all content goes into run\ subfolder;
            # only <entry>.bat and setup.bat sit at the top level.
            dst_p.mkdir(parents=True, exist_ok=True)
            run_p = dst_p / 'run'
            run_p.mkdir(parents=True, exist_ok=True)

            # Copy protected files
            self._log('Copying protected files to deploy folder…', 'info')
            for orig_rel, protected_file in protected.items():
                # Extra-source files: place under run/<extra_dir_name>/rel_path
                _placed = False
                for _xd in _extra_dirs:
                    try:
                        _xrel = Path(protected_file).relative_to(_xd)
                        dest = run_p / _xd.name / _xrel.parent / protected_file.name
                        _placed = True
                    except ValueError:
                        try:
                            orig_abs = (src_p / orig_rel).resolve()
                            _xrel = orig_abs.relative_to(_xd.resolve())
                            dest = run_p / _xd.name / _xrel.parent / protected_file.name
                            _placed = True
                        except ValueError:
                            pass
                    if _placed:
                        break
                if not _placed:
                    dest = run_p / orig_rel.parent / protected_file.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(protected_file, dest)
                self._log(f'  run\\{dest.relative_to(run_p)}', 'ok')

            # Copy non-.py files
            if self.copy_non_py_var.get():
                self._log('Copying non-.py files…', 'info')
                _non_py_sources = [(glob_fn, src_p)]
                for _xd in _extra_dirs:
                    _xglob = _xd.rglob if self.recursive_var.get() else _xd.glob
                    _non_py_sources.append((_xglob, _xd))
                for _gfn, _base in _non_py_sources:
                    _prefix = '' if _base == src_p else _base.name + '/'
                    for f in sorted(_gfn('*')):
                        if not f.is_file():
                            continue
                        rel = f.relative_to(_base)
                        if any(part in _SKIP_DIRS for part in rel.parts):
                            continue
                        if f.suffix.lower() in _SKIP_EXTS or f.suffix.lower() == '.py':
                            continue
                        if f.name in _SKIP_NAMES:
                            continue
                        if any(f.name.startswith(p) for p in _SKIP_PREFIX):
                            continue
                        dest = run_p / (_base.name if _base != src_p else '') / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, dest)
                        self._log(f'  run\\{dest.relative_to(run_p)}')

            # Remove .py files that have a matching .pyd — all compiled modules.
            # Keep _loader.py and _run.py (entry stubs, must stay as .py).
            if self.compile_var.get():
                import re as _re
                _KEEP_PY = {'_loader.py', '_run.py'}
                self._log('Removing .py files replaced by .pyd…', 'info')
                removed = 0
                for pyd in run_p.rglob('*.pyd'):
                    stem = _re.sub(r'\..*', '', pyd.name)
                    py_sibling = pyd.parent / f'{stem}.py'
                    if py_sibling.exists() and py_sibling.name not in _KEEP_PY:
                        py_sibling.unlink()
                        self._log(f'  removed run\\{py_sibling.relative_to(run_p)}', 'info')
                        removed += 1
                if removed == 0:
                    self._log('  (none found)', 'info')

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

            self._write_launchers(src_p, dst_p, run_p)

        self._log('')
        self._log(f'Done.  Deployed to: {dst_p}', 'ok')

    def _write_launchers(self, src_p: Path, dst_p: Path, run_p: Path | None = None):
        """Write _run.py stub, <entry>.bat, <entry>.vbs and requirements.txt."""
        if run_p is None:
            run_p = dst_p / 'run'
            run_p.mkdir(parents=True, exist_ok=True)
        entry_module = self.entry_var.get().strip() or 'dashboard'
        self._log('Writing launchers…', 'info')

        # ── _run.py stub (lives in run\) ──────────────────────────────────────
        stub = run_p / '_run.py'
        stub.write_text(
            'import sys, os\n'
            'from pathlib import Path\n'
            'sys.path.insert(0, str(Path(__file__).resolve().parent))\n'
            'import multiprocessing\n'
            'multiprocessing.freeze_support()\n'
            f'import {entry_module} as _app\n'
            'if hasattr(_app, "main"):\n'
            '    _app.main()\n'
            'elif hasattr(_app, "run"):\n'
            '    _app.run()\n'
            'else:\n'
            '    # .pyd modules have no code object — runpy.run_module won\'t work.\n'
            '    # Find the tk.Tk subclass (the main App window) and launch it.\n'
            '    import inspect, tkinter as _tk\n'
            '    _app_cls = None\n'
            '    for _name, _obj in inspect.getmembers(_app, inspect.isclass):\n'
            '        try:\n'
            '            if issubclass(_obj, _tk.Tk) and _obj is not _tk.Tk:\n'
            '                _app_cls = _obj\n'
            '                break\n'
            '        except TypeError:\n'
            '            pass\n'
            '    if _app_cls is None:\n'
            '        raise RuntimeError(\n'
            f'            f"No entry point found in module \'{entry_module}\'. "\n'
            '            f"Expected main(), run(), or a tk.Tk subclass. "\n'
            '            f"Available: {{[x for x in dir(_app) if not x.startswith(\'__\')]}}"\n'
            '        )\n'
            '    _instance = _app_cls()\n'
            '    _instance.mainloop()\n',
            encoding='utf-8',
        )

        # ── <entry>.bat (top level — launches run\_run.py) ────────────────────
        bat = dst_p / f'{entry_module}.bat'
        bat.write_text(
            '@echo off\n'
            'pushd "%~dp0"\n'
            'setlocal enabledelayedexpansion\n'
            '\n'
            ':: Capture the bat folder as an absolute path before pushd remaps it\n'
            'set "BATDIR=%~dp0"\n'
            '\n'
            ':: Find pythonw.exe — check PATH first, then same folder as python.exe, then registry\n'
            'set "PYW="\n'
            'for /f "delims=" %%i in (\'where pythonw.exe 2^>nul\') do if not defined PYW set "PYW=%%i"\n'
            'if not defined PYW (\n'
            '    for /f "delims=" %%i in (\'where python.exe 2^>nul\') do (\n'
            '        if not defined PYW (\n'
            '            if exist "%%~dpi\\pythonw.exe" set "PYW=%%~dpi\\pythonw.exe"\n'
            '        )\n'
            '    )\n'
            ')\n'
            'if not defined PYW (\n'
            '    for /f "tokens=2*" %%a in (\'reg query "HKCU\\Software\\Python\\PythonCore" /s /v "ExecutablePath" 2^>nul ^| findstr /i "pythonw"\') do (\n'
            '        if not defined PYW if exist "%%b" set "PYW=%%b"\n'
            '    )\n'
            ')\n'
            '\n'
            'if not defined PYW (\n'
            '    echo ERROR: pythonw.exe not found. Please run setup.bat first.\n'
            '    pause\n'
            '    exit /b 1\n'
            ')\n'
            '\n'
           f'start "" "!PYW!" "!BATDIR!run\\_run.py"\n',
            encoding='utf-8',
        )

        # ── <entry>.vbs (no console flash, lives in run\) ────────────────────
        vbs = run_p / f'{entry_module}.vbs'
        vbs.write_text(
            'Set fso = CreateObject("Scripting.FileSystemObject")\n'
            'Set sh  = CreateObject("WScript.Shell")\n'
            'root   = fso.GetParentFolderName(WScript.ScriptFullName)\n'
            'script = root & "\\_run.py"\n'
            'sh.CurrentDirectory = root\n'
            '\n'
            '\' Find pythonw.exe: check PATH, then same folder as python.exe\n'
            'Dim pyw : pyw = ""\n'
            'On Error Resume Next\n'
            'Set ex = sh.Exec("cmd /c where pythonw.exe 2>nul")\n'
            'Do While ex.Status = 0 : WScript.Sleep 20 : Loop\n'
            'pyw = Split(Trim(ex.StdOut.ReadAll()), vbCrLf)(0)\n'
            'On Error GoTo 0\n'
            '\n'
            'If pyw = "" Or Not fso.FileExists(pyw) Then\n'
            '    On Error Resume Next\n'
            '    Set ex2 = sh.Exec("cmd /c where python.exe 2>nul")\n'
            '    Do While ex2.Status = 0 : WScript.Sleep 20 : Loop\n'
            '    Dim pydir : pydir = fso.GetParentFolderName(Split(Trim(ex2.StdOut.ReadAll()), vbCrLf)(0))\n'
            '    If fso.FileExists(pydir & "\\pythonw.exe") Then pyw = pydir & "\\pythonw.exe"\n'
            '    On Error GoTo 0\n'
            'End If\n'
            '\n'
            'If pyw = "" Then pyw = "pythonw.exe"\n'
            '\n'
            'sh.Run Chr(34) & pyw & Chr(34) & " " & Chr(34) & script & Chr(34), 0, False\n',
            encoding='utf-8',
        )

        # ── requirements.txt (lives in run\) ─────────────────────────────────
        req_src = src_p / 'requirements.txt'
        req_dst = run_p / 'requirements.txt'
        if req_src.exists():
            shutil.copy2(req_src, req_dst)
        else:
            req_dst.write_text(
                'pandas\nopenpyxl\nmatplotlib\nnumpy\ndocopt\nPillow\n',
                encoding='utf-8',
            )

        # ── setup.bat (first-time install helper — installs Python if missing) ─
        setup = dst_p / 'setup.bat'
        _pyver = f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'
        _pyexe = f'python-{_pyver}-amd64.exe'
        _pyurl = f'https://www.python.org/ftp/python/{_pyver}/{_pyexe}'
        setup.write_text(
            '@echo off\n'
            'setlocal enabledelayedexpansion\n'
            'echo === Dashboard Setup ===\n'
            'echo.\n'
            '\n'
            ':: ── Step 1: Locate python.exe ───────────────────────────────────────────\n'
            'set "PYEXE="\n'
            '\n'
            ':: Check PATH first\n'
            'for /f "delims=" %%i in (\'where python.exe 2^>nul\') do if not defined PYEXE set "PYEXE=%%i"\n'
            '\n'
            ':: Check per-user install location  (AppData\\Local\\Programs\\Python)\n'
            'if not defined PYEXE (\n'
            '    for /d %%d in ("%LOCALAPPDATA%\\Programs\\Python\\Python3*") do (\n'
            '        if not defined PYEXE (\n'
            '            if exist "%%d\\python.exe" set "PYEXE=%%d\\python.exe"\n'
            '        )\n'
            '    )\n'
            ')\n'
            '\n'
            ':: Check system-wide install  (C:\\Python3x or C:\\Program Files\\Python3x)\n'
            'if not defined PYEXE (\n'
            '    for /d %%d in ("C:\\Python3*" "C:\\Program Files\\Python3*" "C:\\Program Files (x86)\\Python3*") do (\n'
            '        if not defined PYEXE (\n'
            '            if exist "%%d\\python.exe" set "PYEXE=%%d\\python.exe"\n'
            '        )\n'
            '    )\n'
            ')\n'
            '\n'
            ':: Check registry (HKCU — per-user install)\n'
            'if not defined PYEXE (\n'
            '    for /f "tokens=2*" %%a in (\'reg query "HKCU\\Software\\Python\\PythonCore" /s /v "ExecutablePath" 2^>nul ^| findstr /i "python.exe"\') do (\n'
            '        if not defined PYEXE if exist "%%b" set "PYEXE=%%b"\n'
            '    )\n'
            ')\n'
            '\n'
            'if not defined PYEXE goto :install_python\n'
            '\n'
            ':: ── Step 2: Add Python dir and Scripts\\ to PATH for this session and persist ─\n'
            ':add_to_path\n'
            'for %%i in ("!PYEXE!") do set "PYDIR=%%~dpi"\n'
            'if "!PYDIR:~-1!"=="\\" set "PYDIR=!PYDIR:~0,-1!"\n'
            'set "SCRIPTS=!PYDIR!\\Scripts"\n'
            'echo Python found: !PYEXE!\n'
            'echo Python dir  : !PYDIR!\n'
            '\n'
            ':: Add to current session PATH\n'
            'set "PATH=!PYDIR!;!SCRIPTS!;!PATH!"\n'
            '\n'
            ':: Persist to user PATH via setx\n'
            'set "NEWPATH=!PYDIR!;!SCRIPTS!"\n'
            'for /f "usebackq tokens=2*" %%a in (`reg query "HKCU\\Environment" /v PATH 2^>nul`) do set "CURPATH=%%b"\n'
            'echo !CURPATH! | find /i "!PYDIR!" >nul 2>&1\n'
            'if %errorlevel% neq 0 (\n'
            '    setx PATH "!NEWPATH!;!CURPATH!" >nul 2>&1\n'
            '    echo Python PATH added to user environment permanently.\n'
            ') else (\n'
            '    echo Python PATH already in user environment.\n'
            ')\n'
            '\n'
            'goto :install_packages\n'
            '\n'
            ':: ── Step 3: Download and install Python if missing ─────────────────────────\n'
            ':install_python\n'
           f'echo Python not found. Downloading Python {_pyver} installer...\n'
           f'curl -L -o "%TEMP%\\{_pyexe}" "{_pyurl}"\n'
            'if %errorlevel% neq 0 (\n'
            '    echo Download failed. Please install Python manually from https://www.python.org/downloads/\n'
            '    pause\n'
            '    exit /b 1\n'
            ')\n'
            'echo Installing Python silently (for current user, no admin needed)...\n'
           f'"%TEMP%\\{_pyexe}" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0\n'
            'if %errorlevel% neq 0 (\n'
            '    echo Python installation failed. Please install manually.\n'
            '    pause\n'
            '    exit /b 1\n'
            ')\n'
           f'echo Python {_pyver} installed.\n'
            'echo Locating new install...\n'
            'for /d %%d in ("%LOCALAPPDATA%\\Programs\\Python\\Python3*") do (\n'
            '    if not defined PYEXE if exist "%%d\\python.exe" set "PYEXE=%%d\\python.exe"\n'
            ')\n'
            'if not defined PYEXE (\n'
            '    echo Could not locate Python after install. Please re-run setup.bat.\n'
            '    pause\n'
            '    exit /b 1\n'
            ')\n'
            'goto :add_to_path\n'
            '\n'
            ':: ── Step 4: Install required packages ──────────────────────────────────────\n'
            ':install_packages\n'
            'echo.\n'
            'echo Installing required packages...\n'
            '"!PYEXE!" -m pip install -r "%~dp0run\\requirements.txt" --proxy http://proxy-us.intel.com:911\n'
            'if %errorlevel% neq 0 (\n'
            '    echo Package install failed. Try running as administrator or check your internet connection.\n'
            '    pause\n'
            '    exit /b 1\n'
            ')\n'
            'echo.\n'
           f'echo Setup complete! Double-click {entry_module}.bat to launch.\n'
            'pause\n',
            encoding='utf-8',
        )

        self._log(f'  {entry_module}.bat + setup.bat (top level), run\\_run.py + run\\requirements.txt + run\\{entry_module}.vbs', 'ok')

    # ── Cython ────────────────────────────────────────────────────────────────

    def _ensure_cython(self) -> bool:
        # Check both Cython and setuptools are available
        missing = []
        for pkg, imp in [('cython', 'Cython'), ('setuptools', 'setuptools')]:
            r = subprocess.run(
                [sys.executable, '-c', f'import {imp}'],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                missing.append(pkg)
        if missing:
            self._log(f'Installing {", ".join(missing)}…', 'info')
            ret2 = subprocess.run(
                [sys.executable, '-m', 'pip', 'install',
                 '--proxy', 'http://proxy-us.intel.com:911'] + missing,
                capture_output=True, text=True,
            )
            if ret2.returncode != 0:
                self._log('pip install failed:\n' + ret2.stderr[-400:], 'err')
                return False
            self._log(f'{", ".join(missing)} installed.', 'ok')
        r = subprocess.run(
            [sys.executable, '-c', 'import Cython; print(Cython.__version__)'],
            capture_output=True, text=True,
        )
        self._log(f'Cython {r.stdout.strip()}', 'ok')
        return True

    def _run_cython(self, py_files: list[Path], src_p: Path,
                    tmp_dir: Path) -> dict[Path, Path]:
        """Compile each .py → .pyd via Cython + setuptools build_ext."""
        self._log('Checking Cython…', 'info')
        if not self._ensure_cython():
            return {}

        self._log('Compiling with Cython…', 'info')
        result: dict[Path, Path] = {}

        for py in py_files:
            rel     = py.relative_to(src_p)
            out_sub = tmp_dir / rel.parent
            out_sub.mkdir(parents=True, exist_ok=True)

            self._log(f'Compiling  {rel} …')

            # Write a minimal setup.py in out_sub, pointing at the source file
            setup_py = out_sub / '_setup_build.py'
            setup_py.write_text(
                'from setuptools import setup\n'
                'from Cython.Build import cythonize\n'
                'setup(ext_modules=cythonize(\n'
                f'    {str(py)!r},\n'
                '    language_level=3,\n'
                '    compiler_directives={"always_allow_keywords": True},\n'
                '))\n',
                encoding='utf-8',
            )

            ret = subprocess.run(
                [
                    sys.executable, str(setup_py),
                    'build_ext', '--inplace',
                    f'--build-lib={out_sub}',
                    f'--build-temp={out_sub / "_build"}',
                ],
                capture_output=True, text=True, cwd=str(out_sub),
            )

            # Clean up temp build artefacts (and .c from original source dir)
            for _f in out_sub.glob('*.c'):
                try: _f.unlink()
                except OSError: pass
            _c_in_src = py.with_suffix('.c')
            if _c_in_src.exists():
                try: _c_in_src.unlink()
                except OSError: pass
            _build = out_sub / '_build'
            if _build.exists():
                shutil.rmtree(_build, ignore_errors=True)
            try: setup_py.unlink()
            except OSError: pass

            if ret.returncode != 0:
                err_text = (ret.stderr or ret.stdout).strip()
                self._log(f'  Cython compile failed — copying .py as-is', 'info')
                self._log(f'  ({err_text[-200:]})', 'plain')
                # Fall back: copy the original .py unchanged
                fallback = out_sub / py.name
                shutil.copy2(py, fallback)
                result[rel] = fallback
                continue

            # Find the .pyd (or .so on Linux/Mac)
            pyd = None
            for ext in ('.pyd', '.so'):
                matches = list(out_sub.glob(f'{py.stem}*{ext}'))
                if matches:
                    pyd = matches[0]
                    break
            if pyd:
                result[rel] = pyd
                self._log(f'  OK → {pyd.name}', 'ok')
            else:
                self._log(f'  Could not find compiled output in {out_sub}', 'err')

        return result



if __name__ == '__main__':
    app = DeployApp()
    app.mainloop()
