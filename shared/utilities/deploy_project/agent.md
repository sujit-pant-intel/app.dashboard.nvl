# deploy_project.py — Copilot Agent Instructions

## Purpose

`deploy_project.py` is a dark-themed **tkinter GUI tool** that:

1. Compiles `.py` source files to native `.pyd` binaries via **Cython**
2. Copies all other files (`.jsl`, `.json`, `.txt`, `.csv`, …) as-is
3. Writes launcher stubs (`_run.py`, `<entry>.bat`, `<entry>.vbs`, `setup.bat`, `requirements.txt`) into the deploy folder

Run with: `python deploy_project.py`

---

## File Layout

```
deploy/
  deploy_project.py      ← this tool
  agent.md               ← this file
```

Deploy output structure (written to the chosen deploy folder):

```
<deploy_folder>/
  <entry>.bat            ← double-click launcher (top level)
  setup.bat              ← first-time Python + package installer
  run/
    _run.py              ← entry stub: imports <entry> .pyd and calls main()
    <entry>.vbs          ← windowless launcher (no console flash)
    requirements.txt     ← pip requirements (copied or generated)
    *.pyd                ← Cython-compiled modules
    *.jsl / *.json / …   ← non-.py files copied as-is
```

---

## UI Fields

| Field | Variable | Default | Notes |
|---|---|---|---|
| Source folder | `self.src_var` | — | Folder containing `.py` source files |
| Deploy folder | `self.dst_var` | — | Destination (network share or local) |
| Entry module | `self.entry_var` | `dashboard` | `ttk.Combobox` — auto-populated from `.py` stems on Browse; editable |

### Entry module combobox

`_refresh_entry_choices(folder)` populates the combobox with all non-dunder `.py`
stems from the top level of the source folder when a source is browsed.
The user can also type any name manually.

---

## Options (checkboxes)

| Option | Variable | Default |
|---|---|---|
| Compile `.py` → `.pyd` with Cython | `self.compile_var` | `True` |
| Also copy non-`.py` files | `self.copy_non_py_var` | `True` |
| Clear deploy folder before copying | `self.clear_dst_var` | `False` |
| Recurse into sub-folders | `self.recursive_var` | `True` |

---

## Pipeline (`_do_run`)

```
_start()
  └─ _run()  [daemon thread]
       └─ _do_run(src, dst)
            ├─ glob .py files (skip _SKIP_DIRS)
            ├─ split: compile_files vs passthru_files (_NO_COMPILE)
            ├─ _run_cython(compile_files, src_p, tmp_dir)  → protected dict
            ├─ copy protected .pyd files → run\
            ├─ copy non-.py files → run\  (if copy_non_py_var)
            ├─ remove .py siblings of .pyd  (except _KEEP_PY)
            └─ _write_launchers(src_p, dst_p, run_p)
```

---

## Key Constants

| Name | Value | Purpose |
|---|---|---|
| `_SKIP_EXTS` | `.log .pyc .pyo .pyd .so .c .spec` | Extensions never copied |
| `_SKIP_PREFIX` | `('__',)` | File stems starting with these are skipped |
| `_SKIP_NAMES` | `{'obfuscate_and_deploy.py'}` | Specific filenames to exclude |
| `_SKIP_DIRS` | `.venv venv __pycache__ build dist .git …` | Top-level dirs excluded from glob |
| `_NO_COMPILE` | `{'_loader.py', '_run.py', 'obfuscate_and_deploy.py', 'setup.py'}` | Pass-through as `.py`, not compiled |
| `_KEEP_PY` | `{'_loader.py', '_run.py'}` | `.py` files NOT removed after Cython step |

---

## Cython Compilation (`_run_cython`)

- Calls `_ensure_cython()` first — checks `Cython` + `setuptools`; pip-installs if missing
- For each `.py` file: writes a temp `setup.py`, runs `python setup.py build_ext --inplace`
- Output: `<stem>.cpython-3xx-win_amd64.pyd` in `tmp_dir`
- Returns `{orig_rel: pyd_path}` dict

---

## Launcher Stubs (`_write_launchers`)

### `run\_run.py`
```python
import {entry_module} as _app
if hasattr(_app, "main"):   _app.main()
elif hasattr(_app, "run"):  _app.run()
else: runpy.run_module("{entry_module}", run_name="__main__", alter_sys=True)
```
Priority: `main()` → `run()` → `runpy.run_module()`

### `setup.bat`
- Checks for Python; downloads and silently installs if missing (`python.org/ftp`)
- Installs packages from `run\requirements.txt`
- Uses Intel proxy: `http://proxy-us.intel.com:911`

---

## Embedded Python Launcher (`_build_embedded_launcher`)

Copies the current Python runtime into `_python\` inside the deploy folder:
- `python.exe` / `pythonw.exe`
- Runtime DLLs (`python*.dll`, `vcruntime*.dll`, …)
- `DLLs\` — extension modules
- `Lib\` — stdlib + site-packages (excludes `__pycache__`)
- `tcl\` — required for tkinter

Writes `_python\python3xx._pth` to set `sys.path` and add deploy root.

---

## PyInstaller Exe Builder (`_build_exe`)

- Locates `pyinstaller.exe` via `_find_pyinstaller()` (Scripts\, AppData, PATH)
- Runs `pyinstaller --onefile --noconsole --distpath <dst> <launcher>`
- Moves output `.exe` to deploy root; removes `<entry>_run.exe` temp name

---

## Colours

| Constant | Value | Usage |
|---|---|---|
| `BG` | `#1a252f` | Window background |
| `BG2` | `#2c3e50` | Checkbox select colour |
| `FG` | `#ecf0f1` | Foreground text |
| `BTN_BG` | `#2980b9` | Browse buttons |
| `GREEN` | `#27ae60` | Run button |
| `ENTRY_BG` | `#34495e` | Text entry background |

---

## Common Pitfalls

| Problem | Cause | Fix |
|---|---|---|
| `cython` / `setuptools` not found | Not installed | Tool auto-installs on first run |
| Combobox shows no entries | Source folder typed manually (not browsed) | Click Browse… or type the module name |
| `.pyd` not found after compile | Python version mismatch between build and runtime | Use the same Python to run the tool and deploy |
| `setup.bat` proxy fails | Non-Intel network | Edit `http://proxy-us.intel.com:911` in `_write_launchers` |
| Tkinter import error on embedded Python | Missing `tcl\` folder | `_build_embedded_launcher` copies it automatically |
| `_run.py` deleted after compile | It's in `_NO_COMPILE` and `_KEEP_PY` | Both sets protect it — no action needed |
