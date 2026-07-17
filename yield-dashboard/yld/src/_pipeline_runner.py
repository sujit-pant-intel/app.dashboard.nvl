"""_pipeline_runner.py - run_pipeline, SICC runner, post-pipeline logic."""
import glob
from _pipeline_constants import _SRC_DIR, _ROOT_DIR, _FROZEN, _LOADER, SICC_UPM_SCRIPT, SICC_CDYN_UPM_SCRIPT, _PROD_CFG_DIR, _PCM_SETUP_JSON
import io
import json
import os
import subprocess
import sys

# Pass -B to all subprocess Python invocations to suppress __pycache__ creation
_PYTHON = [sys.executable, '-B']
import threading
import tkinter as tk
from tkinter import filedialog, messagebox


class PipelineRunnerMixin:
    def run_pipeline(self):
        import time as _time_mod
        _pipeline_t0 = _time_mod.time()  # t=0 is the moment Run is clicked
        _section_t = [_pipeline_t0]       # mutable cell so nested _ts() can update it
        def _ts(label=''):
            """Print elapsed total + delta since last section."""
            _now = _time_mod.time()
            _total = _now - _pipeline_t0
            _delta = _now - _section_t[0]
            _section_t[0] = _now
            _tag = f' {label}' if label else ''
            return f'[{_total:6.1f}s | d{_delta:.1f}s]{_tag}'
        self.output.configure(state=tk.NORMAL)
        self.output.delete('1.0', tk.END)
        self.output.insert(tk.END, '[  0.0s] ▶  Running pipeline…\n')
        self.output.see(tk.END)
        self.output.update_idletasks()

        # Merge JSON in-memory; apply dedicated section vars as overrides
        merged = dict(self.json_data)

        def _apply(key, val):
            if val:
                merged[key] = val

        _apply('dashboard',          self.dashboard_var.get().strip())
        _apply('output_folder',      self.output_folder_var.get().strip())

        _run_id = self.testprogram_id_var.get().strip() or merged.get('identifier', '')
        # If multiple TPs are comma/semicolon separated, use only the first for the
        # identifier — pipeline.py will set TestProgram = first TP and use the combined
        # sort CSV for all downstream steps.
        import re as _re_tp_gui
        _tp_parts = [p.strip() for p in _re_tp_gui.split(r'[,;\n\|]+', _run_id) if p.strip()]
        merged['identifier'] = _run_id  # pass full string; pipeline.py splits it

        _apply('product_config_json', self.fail_bucket_var.get().strip())
        merged['sicc_run'] = bool(self.sicc_run_var.get())

        _apply('aquaserver',         self.aqua_server_var.get().strip())
        _apply('aqua_cmd_path',      self.aqua_cmd_var.get().strip())
        _apply('reportPath',         self.report_path_var.get().strip())
        _apply('TestProgram_folder', self.tp_folder_var.get().strip())
        _apply('TestProgram',        self.testprogram_var.get().strip())

        # Read ALL CSVs from the data listbox (first = primary, rest = extras to merge)
        _all_csv_items = list(self._data_csv_lb.get(0, tk.END)) if hasattr(self, '_data_csv_lb') else []
        _aqua_out = self.aqua_out_var.get().strip()  # mirrors first item
        import tkinter.messagebox as _mb
        _valid_exts = ('.csv', '.csv.gz', '.zip', '.gz', '.7z')
        for _csv_item in _all_csv_items:
            if _csv_item and not any(_csv_item.lower().endswith(e) for e in _valid_exts):
                _mb.showerror('Invalid File Type', f'Data CSV must be a .csv, .csv.gz, .zip, or .7z file.\nGot: {_csv_item}')
                return
        if len(_all_csv_items) > 1:
            # Multiple CSVs — pipeline.py will merge them into one before processing
            merged['DataCSV'] = _all_csv_items
            merged.pop('outputFilename', None)
            merged.pop('aqua_outputfile', None)
        elif _aqua_out:
            merged['outputFilename'] = _aqua_out
            merged.pop('aqua_outputfile', None)
        elif 'aqua_outputfile' in merged:
            merged['outputFilename'] = merged.pop('aqua_outputfile')

        base_dir = os.path.dirname(self.input_path) if self.input_path else os.getcwd()

        # ── Force-clean identifier output subfolder before run ────────────
        try:
            _raw_out_pre = self.output_folder_var.get().strip() or merged.get('output_folder', '')
            _primary_id  = _tp_parts[0] if _tp_parts else (_run_id or '')
            _safe_id_pre = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in _primary_id)
            if _raw_out_pre and _safe_id_pre:
                _pre_clean = os.path.join(_raw_out_pre, _safe_id_pre)
                if os.path.isdir(_pre_clean):
                    import shutil as _shutil_pre
                    _shutil_pre.rmtree(_pre_clean)
                    self.output.insert(tk.END, f'Cleaned output folder: {_pre_clean}\n')
                    self.output.see(tk.END)
        except Exception as _ce_pre:
            self.output.insert(tk.END, f'[warn] Output folder cleanup failed: {_ce_pre}\n')
            self.output.see(tk.END)

        # ── Write merged config to a temp JSON and call pipeline.py --json ──
        # Single code path shared with CLI — no duplicate post-pipeline logic.
        # Include pcm_filter so pipeline.py passes it to parametric_runner.
        if hasattr(self, '_get_pcm_filter'):
            _pcm_filter_val = self._get_pcm_filter()
            if _pcm_filter_val:
                merged['pcm_filter'] = _pcm_filter_val
        import tempfile as _tmpjson_mod
        import pathlib as _pl_gui

        class _PathEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, _pl_gui.PurePath):
                    return str(obj)
                return super().default(obj)

        _tmp_json_path = [None]

        import queue as _queue_mod
        _out_queue = _queue_mod.Queue()

        _pipeline_script = os.path.join(_SRC_DIR, 'pipeline.py')

        def _run_in_thread():
            try:
                with _tmpjson_mod.NamedTemporaryFile(
                    mode='w', suffix='_gui_run.json', prefix='pipeline_gui_',
                    delete=False, encoding='utf-8'
                ) as _tf:
                    json.dump(merged, _tf, cls=_PathEncoder)
                    _tmp_json_path[0] = _tf.name
                import os as _os
                _pipe_env = {**_os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'}
                if getattr(self, 'debug_console_var', None) and self.debug_console_var.get():
                    _pipe_env['YLD_DEBUG'] = '1'
                proc = subprocess.Popen(
                    [*_PYTHON, _pipeline_script, '--json', _tmp_json_path[0]],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', env=_pipe_env,
                )
                for line in proc.stdout:
                    _out_queue.put(line)
                proc.wait()
                if proc.returncode != 0:
                    _out_queue.put(f'\n[pipeline exited with code {proc.returncode}]\n')
            except Exception as _ex:
                _out_queue.put(f'Failed to run pipeline: {_ex}\n')
            finally:
                _out_queue.put(None)  # sentinel
                try:
                    if _tmp_json_path[0] and os.path.isfile(_tmp_json_path[0]):
                        os.remove(_tmp_json_path[0])
                except Exception:
                    pass

        _spinner_chars = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏']
        _spinner_state = [0]
        _last_spinner_time = [0.0]
        _start_time = [_time_mod.time()]

        def _poll_output():
            import time
            while True:
                try:
                    chunk = _out_queue.get_nowait()
                except _queue_mod.Empty:
                    break
                if chunk is None:
                    self.output.see(tk.END)
                    self.output.insert(tk.END, f'\n{_ts()} ✔  Pipeline complete.\n')
                    self.output.see(tk.END)
                    return
                self.output.insert(tk.END, chunk)
                self.output.see(tk.END)

            now = time.time()
            if now - _last_spinner_time[0] >= 5.0:
                _last_spinner_time[0] = now
                sp = _spinner_chars[_spinner_state[0] % len(_spinner_chars)]
                _spinner_state[0] += 1
                elapsed = int(now - _start_time[0])
                self.output.insert(tk.END, f'{sp} {elapsed}s elapsed\n')
                self.output.see(tk.END)

            self.output.after(200, _poll_output)

        _last_spinner_time[0] = _time_mod.time()
        threading.Thread(target=_run_in_thread, daemon=True).start()
        _poll_output()
        return

    def open_dashboard_folder(self):
        dash = None
        focused = self.focus_get()
        if isinstance(focused, tk.Entry):
            try:
                sel = focused.selection_get()
            except Exception:
                sel = None
            if sel:
                dash = sel

        if not dash:
            if 'dashboard' in self.fields:
                dash = self.fields['dashboard'].get()
            else:
                dash = self.json_data.get('dashboard') or self.dashboard_var.get().strip()

        if not dash:
            messagebox.showwarning('No dashboard', 'dashboard field not found in JSON or selection')
            return

        resolved = self._resolve_dashboard_path(dash)
        if resolved and os.path.isfile(resolved):
            os.startfile(resolved)
        elif resolved:
            messagebox.showinfo('Not generated yet', f'Dashboard.html will be created at:\n{resolved}\n\nRun the pipeline first to generate it.')
        else:
            messagebox.showerror('Not found', f'Dashboard file not found: {dash}')

    def _resolve_dashboard_path(self, dash):
        if not dash:
            return None
        candidate = dash.strip().strip('\"\'')
        candidate = os.path.expandvars(os.path.expanduser(candidate))
        # If the candidate is an absolute path with an explicit filename,
        # honour it even when the file does not exist yet — the caller
        # (_update_dashboard_html) will create it.
        if os.path.isabs(candidate):
            _ext = os.path.splitext(candidate)[1].lower()
            if _ext in ('.html', '.htm') or os.path.isfile(candidate):
                return os.path.abspath(candidate)
        if self.input_path:
            base = os.path.dirname(self.input_path)
            rel = os.path.join(base, candidate)
            if os.path.isfile(rel):
                return os.path.abspath(rel)
        name = os.path.basename(candidate)
        search_dirs = []
        if self.input_path:
            search_dirs.append(os.path.dirname(self.input_path))
        repo_root = _ROOT_DIR
        search_dirs.append(repo_root)
        search_dirs.append(os.getcwd())
        for d in search_dirs:
            try:
                for fname in os.listdir(d):
                    if fname.lower() == name.lower() or name.lower() in fname.lower():
                        p = os.path.join(d, fname)
                        if os.path.isfile(p):
                            return os.path.abspath(p)
            except Exception:
                continue
        patterns = ['Dashboard.html', 'dashboard.html', 'DigitalDashBoard', 'DigitalDashboard']
        for d in search_dirs:
            try:
                for fname in os.listdir(d):
                    for pat in patterns:
                        if pat.lower() in fname.lower():
                            p = os.path.join(d, fname)
                            if os.path.isfile(p):
                                return os.path.abspath(p)
            except Exception:
                continue
        # File not found anywhere — return a resolved path so the caller can
        # create Dashboard.html at a sensible location.
        _raw_cand = dash.strip().strip('"\'')
        _raw_cand = os.path.expandvars(os.path.expanduser(_raw_cand))
        if os.path.isabs(_raw_cand):
            return os.path.abspath(_raw_cand)
        # Relative path / bare filename: anchor to JSON input dir or CWD
        _base = (os.path.dirname(self.input_path)
                 if self.input_path else os.getcwd())
        return os.path.abspath(os.path.join(_base, _raw_cand))

    def _find_bin_image(self, base_dir):
        candidates = []
        if base_dir:
            candidates.append(base_dir)
        repo_root = _ROOT_DIR
        candidates.append(repo_root)

        found = []
        for d in candidates:
            try:
                pattern = os.path.join(d, '*_BinDistribution.html')
                for p in glob.glob(pattern):
                    if os.path.isfile(p):
                        found.append(os.path.abspath(p))
            except Exception:
                continue

        if not found:
            return None

        real_files = [p for p in found if 'example' not in os.path.basename(p).lower()]
        if real_files:
            real_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            return real_files[0]

        found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return found[0]

    def open_bin_image(self):
        base_dir = os.path.dirname(self.input_path) if self.input_path else os.getcwd()
        csv_path = None
        for key in ('aqua_outputfile', 'outputFilename', 'output'):
            if key in self.fields and self.fields[key].get().strip():
                csv_path = self.fields[key].get().strip()
                break
        if not csv_path:
            for key in ('aqua_outputfile', 'outputFilename', 'output'):
                if key in self.json_data and self.json_data.get(key):
                    csv_path = self.json_data.get(key)
                    break

        resolved_csv = None
        if csv_path:
            candidate = csv_path.strip().strip('\"\'')
            candidate = os.path.expandvars(os.path.expanduser(candidate))
            if os.path.isabs(candidate) and os.path.isfile(candidate):
                resolved_csv = os.path.abspath(candidate)
            else:
                if self.input_path:
                    rel = os.path.join(os.path.dirname(self.input_path), candidate)
                    if os.path.isfile(rel):
                        resolved_csv = os.path.abspath(rel)
                if not resolved_csv and self.input_path:
                    try:
                        name = os.path.basename(candidate)
                        base_search = os.path.dirname(self.input_path)
                        for root, dirs, files in os.walk(base_search):
                            if name in files:
                                resolved_csv = os.path.abspath(os.path.join(root, name))
                                break
                    except Exception:
                        pass

        if resolved_csv:
            runner = os.path.join(_SRC_DIR, 'bin_distribution_html.py')
            _pcfg_bd = self.fail_bucket_var.get().strip()
            try:
                if _FROZEN:
                    import runpy as _runpy2
                    _saved_argv2 = sys.argv
                    sys.argv = [runner, resolved_csv]
                    if _pcfg_bd and os.path.isfile(_pcfg_bd):
                        sys.argv.append(_pcfg_bd)
                    try:
                        _runpy2.run_module('bin_distribution_html', run_name='__main__', alter_sys=True)
                    except SystemExit:
                        pass
                    finally:
                        sys.argv = _saved_argv2
                else:
                    _bd_cmd = [*_PYTHON, _LOADER, 'bin_distribution_html', resolved_csv]
                    if _pcfg_bd and os.path.isfile(_pcfg_bd):
                        _bd_cmd.append(_pcfg_bd)
                    subprocess.run(_bd_cmd, check=False)
            except Exception:
                pass
            stem = os.path.splitext(os.path.basename(resolved_csv))[0]
            # Look in the identifier subfolder first, then next to the CSV
            _run_id_bd = self.testprogram_id_var.get().strip()
            _out_bd_base = self.output_folder_var.get().strip()
            _safe_bd = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in _run_id_bd) if _run_id_bd else ''
            _id_dir = os.path.join(_out_bd_base, _safe_bd) if (_out_bd_base and _safe_bd) else None
            out_html = (
                os.path.join(_id_dir, stem + '_BinDistribution.html') if _id_dir and os.path.isfile(os.path.join(_id_dir, stem + '_BinDistribution.html'))
                else os.path.join(os.path.dirname(resolved_csv), stem + '_BinDistribution.html')
            )
            if os.path.isfile(out_html) and 'example' not in os.path.basename(out_html).lower():
                try:
                    os.startfile(out_html)
                    return
                except Exception:
                    msg = f'Generated HTML at: {out_html} (could not open automatically)'
                    print(msg)
                    self.output.insert(tk.END, msg + '\n')
                    self.output.see(tk.END)
            else:
                msg = f'No BinDistribution HTML found for CSV: expected {out_html}'
                print(msg)
                self.output.insert(tk.END, msg + '\n')
                self.output.see(tk.END)

        html = self._find_bin_image(base_dir)
        if html:
            try:
                os.startfile(html)
                return
            except Exception:
                pass

        msg = 'BinDistribution HTML not found.'
        print(msg)
        self.output.insert(tk.END, msg + '\n')
        self.output.see(tk.END)

    def _run_sicc_py_headless(self, csv_path: str, out_dir: str):
        """Always-run Python SICC/CDYN/UPM pipeline.
        Returns (html_abs_path, label, css_class) or None on failure."""
        if not csv_path or not os.path.isfile(csv_path):
            self.output.insert(tk.END, 'SICC/CDYN (Python): CSV not found, skipping.\n')
            self.output.see(tk.END)
            return None
        if not out_dir:
            self.output.insert(tk.END, 'SICC/CDYN (Python): output folder not set, skipping.\n')
            self.output.see(tk.END)
            return None
        try:
            import sys as _sys_py
            from pathlib import Path as _PP_py
            _sicc_py_src = _PP_py(_SRC_DIR).parent.parent / 'sicc_cdyn_upm' / 'src'
            if _sicc_py_src.is_dir() and str(_sicc_py_src) not in _sys_py.path:
                _sys_py.path.insert(0, str(_sicc_py_src))
            from sicc_processor import load_config as _lc_py, process_csv as _pc_py

            _cfg_path = _PP_py(_SRC_DIR).parent.parent / 'collateral' / 'sicc_cdyn_testlist.json'
            _cfg_py = _lc_py(str(_cfg_path)) if _cfg_path.is_file() else {}

            _ov_tgt: dict = {}
            _ov_cdyn: dict = {}
            _pcfg_path = self.fail_bucket_var.get().strip()
            if _pcfg_path and os.path.isfile(_pcfg_path):
                try:
                    _pcdata = json.loads(open(_pcfg_path, encoding='utf-8').read())
                    for _e in _pcdata.get('sicc_targets', []):
                        _t = str(_e.get('test', '')).strip()
                        _v = _e.get('target_A')
                        if _t and _v is not None:
                            try: _ov_tgt[_t.upper()] = float(_v)
                            except (ValueError, TypeError): pass
                    # upm_target not used from Product Config (UPM targets come from upmInfo)
                    for _e in _pcdata.get('cdyn_targets', []):
                        _t = str(_e.get('test', '')).strip()
                        _v = _e.get('target_nF')
                        if _t and _v is not None:
                            try: _ov_cdyn[_t] = float(_v)
                            except (ValueError, TypeError): pass
                    # Merge testlist configs from product config (takes precedence)
                    for _key in ('siccList', 'siccTotalList', 'cdynList', 'upmInfo',
                                 'SiccTableConfig', 'cdynTableConfig'):
                        if _key in _pcdata:
                            _cfg_py[_key] = _pcdata[_key]
                except Exception as _epc:
                    self.output.insert(tk.END, f'SICC/CDYN (Python): product config read error: {_epc}\n')
                    self.output.see(tk.END)

            _data_py = _pc_py(csv_path, _cfg_py,
                              override_targets=_ov_tgt or None,
                              override_cdyn_targets=_ov_cdyn or None)
            _n_w = len(_data_py.get('rows', []))
            _n_s = len(_data_py.get('sicc_columns', []))
            _n_c = len(_data_py.get('cdyn_columns', []))
            self.output.insert(tk.END,
                f'SICC/CDYN (Python): {_n_w} wafers | {_n_s} SICC | {_n_c} CDYN\n')
            self.output.see(tk.END)

            _PP_py(out_dir).mkdir(parents=True, exist_ok=True)
            _html_py = str(_PP_py(out_dir) / f'{_PP_py(csv_path).stem}_sicc_analysis.html')
            from generate_dashboard_html_svg import generate_html_svg as _gh_svg_py
            _gh_svg_py(_data_py, _html_py)
            self.output.insert(tk.END, f'SICC/CDYN Analysis: {_html_py}\n')
            self.output.see(tk.END)
            return (_html_py, 'SICC/CDYN Analysis', 'sicc-link')
        except Exception as _e_spy:
            self.output.insert(tk.END, f'SICC/CDYN (Python) failed: {_e_spy}\n')
            self.output.see(tk.END)
            return None

    def _run_sicc_upm_headless(self, out_dir_override=None):
        """Invoke the SICC/CDYN/UPM run_dashboard.py in headless mode.
        Uses the yield CSV as the JMP input file.
        JMP project is auto-derived as <json_stem>.jmpprj next to the input JSON.
        Output and dashboard are both written to sicc_output_dir.
        Returns a list of (abs_path, label, css_class) tuples, or [].
        """
        links = []

        # Skip if checkbox is not checked
        if not self.sicc_run_var.get():
            out_dir  = self.sicc_out_var.get().strip() or out_dir_override or ''
            # Still collect existing SICC HTML if output folder is set
            if out_dir and os.path.isdir(out_dir):
                all_html = glob.glob(os.path.join(out_dir, '*.html'))
                sicc_html = [p for p in all_html
                             if 'sicc_upm_dashboard' in os.path.basename(p).lower()
                             or 'sicc_cdyn_upm' in os.path.basename(p).lower()]
                cands = sorted(sicc_html, key=os.path.getmtime, reverse=True)
                if cands:
                    links.append((cands[0], 'SICC_CDYN_UPM', 'sicc-link'))
            return links

        # Resolve output folder first (needed for target CSV generation)
        out_dir = self.sicc_out_var.get().strip() or out_dir_override or self.output_folder_var.get().strip()
        if not out_dir:
            self.output.insert(tk.END, 'SICC/CDYN/UPM: Output folder not set, skipping.\n')
            self.output.see(tk.END)
            return links

        # Use manually-set sicc_csv_var if provided (legacy fallback)
        csv_file = self.sicc_csv_var.get().strip()

        # Derive jmp_file from the AQUA Info section (Output CSV)
        jmp_file = self.aqua_out_var.get().strip()
        if not jmp_file:
            for key in ('aqua_outputfile', 'outputFilename', 'output'):
                if key in self.json_data and self.json_data.get(key):
                    jmp_file = str(self.json_data[key]).strip()
                    break
        # Resolve relative path
        if jmp_file and not os.path.isabs(jmp_file) and self.input_path:
            jmp_file = os.path.join(os.path.dirname(self.input_path), jmp_file)
        if not jmp_file or not os.path.isfile(jmp_file):
            self.output.insert(tk.END, 'SICC/CDYN/UPM: yield CSV not found, skipping run.\n')
            self.output.see(tk.END)
            return links

        if _FROZEN:
            # Frozen exe: sicc_cdyn_upm/src/run_dashboard.py was compiled as
            # sicc_cdyn_upm_runner.pyd; call _run_headless() directly.
            _sicc_runner = None
            try:
                import sicc_cdyn_upm_runner as _sicc_runner
            except ImportError:
                self.output.insert(tk.END,
                    'SICC/CDYN/UPM: module not bundled in this build – skipping.\n')
                self.output.see(tk.END)
            if _sicc_runner is not None:
                _headless_args = [
                    '--jmp-file',      jmp_file,
                    '--output-dir',    out_dir,
                    '--dashboard-dir', out_dir,
                ]
                if csv_file and os.path.isfile(csv_file):
                    _headless_args += ['--target-csv', csv_file]
                if self._opener_port:
                    _headless_args += ['--opener-port', str(self._opener_port)]
                _pcfg_sicc = self.fail_bucket_var.get().strip()
                if _pcfg_sicc and os.path.isfile(_pcfg_sicc):
                    _headless_args += ['--product-config', _pcfg_sicc]
                self.output.insert(tk.END, '[Running] SICC/CDYN/UPM analysis (headless)…\n')
                self.output.see(tk.END)
                import io as _io
                _sicc_buf = _io.StringIO()
                _sicc_saved = sys.stdout
                sys.stdout = _sicc_buf
                try:
                    _sicc_runner._run_headless(_headless_args)
                except SystemExit:
                    pass
                except Exception as _sicc_exc:
                    self.output.insert(tk.END, f'SICC/CDYN/UPM run failed: {_sicc_exc}\n')
                finally:
                    sys.stdout = _sicc_saved
                _sicc_out = _sicc_buf.getvalue()
                if _sicc_out:
                    self.output.insert(tk.END, _sicc_out)
                    self.output.see(tk.END)
                for _sicc_line in _sicc_out.splitlines():
                    _sicc_line = _sicc_line.strip()
                    if _sicc_line.startswith('SICC_DASHBOARD: '):
                        p = _sicc_line[len('SICC_DASHBOARD: '):].strip()
                        if os.path.isfile(p):
                            links.append((p, 'SICC_CDYN_UPM', 'sicc-link'))
                        break
        else:
            # Dispatch run_dashboard via _loader (works with both .py and .pyd).
            # The file-existence check on SICC_CDYN_UPM_SCRIPT is intentionally
            # skipped — when compiled to .pyd the .py file no longer exists but
            # _loader can still find and import the module by name.
            cmd = [*_PYTHON, _LOADER, 'run_dashboard', '--headless',
                   '--jmp-file',   jmp_file,
                   '--output-dir', out_dir,
                   '--dashboard-dir', out_dir]
            if csv_file and os.path.isfile(csv_file):
                cmd += ['--target-csv', csv_file]
            if self._opener_port:
                cmd += ['--opener-port', str(self._opener_port)]
            # Pass product config JSON so CDYN targets reach JMP as ::cdyn_limits_map
            _pcfg_sicc = self.fail_bucket_var.get().strip()
            if _pcfg_sicc and os.path.isfile(_pcfg_sicc):
                cmd += ['--product-config', _pcfg_sicc]

            self.output.insert(tk.END, 'Running SICC/CDYN/UPM analysis (headless)...\n')
            self.output.see(tk.END)
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if proc.stdout:
                    self.output.insert(tk.END, proc.stdout)
                if proc.stderr:
                    self.output.insert(tk.END, proc.stderr)
                self.output.see(tk.END)
                for line in proc.stdout.splitlines():
                    line = line.strip()
                    if line.startswith('SICC_DASHBOARD: '):
                        p = line[len('SICC_DASHBOARD: '):].strip()
                        if os.path.isfile(p):
                            links.append((p, 'SICC_CDYN_UPM', 'sicc-link'))
                        break
            except subprocess.TimeoutExpired:
                self.output.insert(tk.END, 'SICC/CDYN/UPM: timed out after 5 minutes, continuing.\n')
                self.output.see(tk.END)
            except Exception as exc:
                self.output.insert(tk.END, f'SICC/CDYN/UPM run failed: {exc}\n')
                self.output.see(tk.END)

        # Fallback: scan output folder for sicc/upm-named HTML
        if not links and out_dir and os.path.isdir(out_dir):
            all_html = glob.glob(os.path.join(out_dir, '*.html'))
            sicc_html = [p for p in all_html
                         if 'sicc_upm_dashboard' in os.path.basename(p).lower()
                         or 'sicc_cdyn_upm' in os.path.basename(p).lower()]
            cands = sorted(sicc_html, key=os.path.getmtime, reverse=True)
            if cands:
                links.append((cands[0], 'SICC_CDYN_UPM', 'sicc-link'))

        if links:
            self.output.insert(tk.END, f'SICC/CDYN/UPM: {len(links)} link(s) collected.\n')
        else:
            self.output.insert(tk.END, 'SICC/CDYN/UPM: no output HTML found.\n')
        self.output.see(tk.END)
        return links

    # ── Parametric pipeline ───────────────────────────────────────────────────

    def run_parametric(self):
        """Run the yield pipeline AND the parametric (PCM) pipeline in sequence.

        The parametric pipeline is launched from _post_pipeline() once the
        standard yield pipeline finishes, so only ONE HTML ever opens
        (ParametricDashboard.html, at the very end).
        """
        import tkinter as tk
        sort_csv = getattr(self, 'aqua_out_var', None)
        sort_csv = sort_csv.get().strip() if sort_csv else ''
        if not sort_csv or not os.path.isfile(sort_csv):
            from tkinter import messagebox
            messagebox.showwarning(
                'No Data CSV',
                'Set the Data CSV field (AQUA Info → Data CSV) to the sort yield CSV before running Parametric.')
            return

        # Queue the parametric launch; _post_pipeline() will trigger it once
        # the yield pipeline finishes.
        self._pending_parametric_csv = sort_csv
        self.run_pipeline()

    def _launch_parametric_runner(self, sort_csv: str, _blocking: bool = False, merged_csv: str = None):
        """Spawn parametric_runner.py as a subprocess and stream its output to the log.
        _blocking=True: run worker inline (call from pipeline thread for serial execution).
        merged_csv: path to reticle/material-merged CSV; passed as --merged-csv so
            parametric_runner uses it as the per-die base (no IDW file written).
        """
        import threading
        import subprocess

        # Locate parametric_runner.py
        _par_runner = os.path.normpath(
            os.path.join(_SRC_DIR, '..', '..', 'sort-parametric', 'parametric_runner.py'))
        if not os.path.isfile(_par_runner):
            self._log('\n[Parametric] ERROR: sort-parametric/parametric_runner.py not found.\n')
            return

        outdir = (self.output_folder_var.get().strip()
                  or os.path.dirname(sort_csv))
        identifier = self.testprogram_id_var.get().strip() or ''
        # Use the identifier subfolder as outdir (where pcm_analysis.html will land)
        # so the post-run check finds it at os.path.join(outdir, 'pcm_analysis.html').
        if outdir and identifier:
            _safe_id_par = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in identifier)
            if _safe_id_par:
                outdir = os.path.join(outdir, _safe_id_par)
        use_full = getattr(self, 'pcm_full_site_var', None)
        use_full = use_full.get() if use_full else False
        # --product-setup must point to pcm_product_setup.json (has groups/patterns),
        # NOT the Product Config JSON.
        product_setup = _PCM_SETUP_JSON or ''
        spec_csv = getattr(self, 'pcm_spec_csv_var', None)
        spec_csv = spec_csv.get().strip() if spec_csv else ''

        # SICC/UPM/CDYN are auto-discovered by parametric_runner from outdir
        cmd = [
            sys.executable, _par_runner,
            '--sort-csv',   sort_csv,
            '--outdir',     outdir,
            '--identifier', identifier,
        ]
        # Pass the GUI input JSON as --config so parametric_runner reads
        # product_config_json (and other settings) from it automatically.
        if self.input_path and os.path.isfile(self.input_path):
            cmd += ['--config', self.input_path]
        if use_full:
            cmd.append('--full-site')
        if product_setup and os.path.isfile(product_setup):
            cmd += ['--product-setup', product_setup]
        if spec_csv and os.path.isfile(spec_csv):
            cmd += ['--spec-csv', spec_csv]
        product_config_json = self.fail_bucket_var.get().strip()
        if product_config_json and os.path.isfile(product_config_json):
            cmd += ['--product-config-json', product_config_json]
        dash_html = getattr(self, '_last_dashboard_html', None)
        if dash_html and os.path.isfile(dash_html):
            cmd += ['--yield-html', dash_html]

        # Pass --deploy-dir so pcm_analysis.html and PCM-merged CSV land in
        # the same subfolder as the index.html (not the parent outdir).
        _master_html = getattr(self, '_last_master_html', None)
        if _master_html and os.path.isfile(_master_html):
            _deploy = os.path.dirname(os.path.abspath(_master_html))
            cmd += ['--deploy-dir', _deploy]

        # Pass the reticle/material-merged CSV so parametric_runner uses it
        # directly as the per-die base (skips IDW expansion; no pcm_idw_*.csv).
        if merged_csv and os.path.isfile(merged_csv):
            cmd += ['--merged-csv', merged_csv]

        # Pass PCM parameter filter if any groups/wildcards selected
        pcm_filter = ''
        if hasattr(self, '_get_pcm_filter'):
            pcm_filter = self._get_pcm_filter()
        if pcm_filter:
            cmd += ['--pcm-filter', pcm_filter]

        self._log(f'\n[Parametric] Launching parametric runner...\n  CMD: {" ".join(cmd)}\n\n')

        def _worker():
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1)
                for line in proc.stdout:
                    self._log(line)
                proc.wait()
                if proc.returncode == 0:
                    self._log(f'\n[Parametric] Done.\n')
                    # Look in the subfolder (deploy_dir = master html dir) first,
                    # then fall back to outdir for backward compatibility.
                    _master_pre = getattr(self, '_last_master_html', None)
                    _deploy_pcm = (
                        os.path.join(os.path.dirname(os.path.abspath(_master_pre)),
                                     'pcm_analysis.html')
                        if _master_pre else None
                    )
                    if _deploy_pcm and os.path.isfile(_deploy_pcm):
                        pcm_html = _deploy_pcm
                    else:
                        pcm_html = os.path.join(outdir, 'pcm_analysis.html')
                    if os.path.isfile(pcm_html):
                        self._log(f'  -> {pcm_html}\n')
                        # Inject PCM Analysis section into the master index.html sidebar,
                        # immediately after the SICC / CDYN / UPM nav-link.
                        _master = getattr(self, '_last_master_html', None)
                        if _master and os.path.isfile(_master):
                            try:
                                import re as _re_pcm
                                _rel_pcm = os.path.relpath(
                                    pcm_html, os.path.dirname(os.path.abspath(_master))
                                ).replace(os.sep, '/')
                                _pcm_section = (
                                    f'  <div class="sec">Parametric Dashboard</div>\n'
                                    f'  <a class="nav-link" href="#" '
                                    f'onclick="load(\'{_rel_pcm}\',this);return false;">'
                                    f'&#128202; Parametric Dashboard</a>\n'
                                )
                                _content = open(_master, encoding='utf-8').read()
                                if _rel_pcm not in _content:
                                    # Prefer: insert at the reserved slot right after Wafer Map
                                    if '<!-- AFTER_WMAP_NAV -->' in _content:
                                        _content = _content.replace(
                                            '<!-- AFTER_WMAP_NAV -->',
                                            '<!-- AFTER_WMAP_NAV -->\n' + _pcm_section
                                        )
                                    elif '<!-- SIDEBAR_END -->' in _content:
                                        _content = _content.replace(
                                            '<!-- SIDEBAR_END -->',
                                            _pcm_section + '<!-- SIDEBAR_END -->'
                                        )
                                    open(_master, 'w', encoding='utf-8').write(_content)
                                    self._log(f'[Parametric] PCM Analysis added to index.html: {_master}\n')
                            except Exception as _pe:
                                self._log(f'[Parametric] WARNING: index.html patch failed: {_pe}\n')
                    else:
                        self._log('[Parametric] WARNING: pcm_analysis.html not found in output.\n')
                else:
                    self._log(f'\n[Parametric] Process exited with code {proc.returncode}\n')
            except Exception as exc:
                self._log(f'\n[Parametric] ERROR: {exc}\n')

        if _blocking:
            _worker()
        else:
            threading.Thread(target=_worker, daemon=True).start()

    def _log(self, msg: str):
        """Append msg to the output log (thread-safe)."""
        def _do():
            try:
                self.output.insert(tk.END, msg)
                self.output.see(tk.END)
            except Exception:
                pass
        self.after(0, _do)

    def open_report(self):
        """Open Dashboard.html (the combined run history dashboard)."""
        # Use last known Dashboard.html path first
        try:
            last = getattr(self, '_last_dashboard_html', None)
            if last and os.path.isfile(last):
                os.startfile(last)
                return
        except Exception:
            pass
        # If the dashboard field points directly to an existing .html file, open it
        _dash_raw = self.dashboard_var.get().strip() if hasattr(self, 'dashboard_var') else ''
        if _dash_raw:
            _dash_exp = os.path.expandvars(os.path.expanduser(_dash_raw.strip().strip('"\''))).replace('/', os.sep)
            if os.path.isfile(_dash_exp):
                os.startfile(_dash_exp)
                return
        # Search: dashboard html dir > CSV parent > JSON parent
        candidates = []
        if _dash_raw:
            _dash_exp = os.path.expandvars(os.path.expanduser(_dash_raw.strip().strip('"\''))).replace('/', os.sep)
            _dash_dir = os.path.dirname(_dash_exp)
            if _dash_dir:
                candidates.append(os.path.join(_dash_dir, 'Dashboard.html'))
        src = self.aqua_out_var.get().strip()
        if not src and self.json_data.get('aqua_outputfile'):
            src = str(self.json_data['aqua_outputfile'])
        if src:
            candidates.append(os.path.join(os.path.dirname(src), 'Dashboard.html'))
        if self.input_path:
            candidates.append(os.path.join(os.path.dirname(self.input_path), 'Dashboard.html'))
        for c in candidates:
            if os.path.isfile(c):
                try:
                    os.startfile(c)
                    return
                except Exception:
                    pass
        messagebox.showwarning('No Dashboard', 'Dashboard.html not found.\nRun the pipeline first, or verify the Dashboard html path points to the correct folder.')

    def save_json(self):
        merged = dict(self.json_data)

        def _set(key, val):
            if val:
                merged[key] = val
            else:
                merged.pop(key, None)

        # Dashboard Info
        _set('dashboard',          self.dashboard_var.get().strip())
        # output_folder is always auto-derived from dashboard path — not saved to JSON
        # Product Config JSON — only save if different from the auto-discovered default
        _prod_cfg_val = self.fail_bucket_var.get().strip()
        import glob as _gl_save
        _central_cfgs = sorted(_gl_save.glob(os.path.join(_PROD_CFG_DIR, 'Product Config*.json')))
        _default_cfg = _central_cfgs[0] if _central_cfgs else None
        def _same_path(a, b):
            if not a or not b: return False
            try: return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
            except Exception: return a == b
        if _prod_cfg_val and not _same_path(_prod_cfg_val, _default_cfg):
            merged['product_config_json'] = _prod_cfg_val
        else:
            merged.pop('product_config_json', None)
        merged.pop('plot_json', None)
        _set('analysis_info',      self.plot_json_var.get().strip())
        # SICC Info — sicc_run and sicc_csv_file are not saved to JSON
        for _old in ('sicc_run', 'sicc_csv_file', 'sicc_jmp_file', 'sicc_jmpprj_file', 'sicc_dashboard_dir'):
            merged.pop(_old, None)
        _set('sicc_output_dir',    self.sicc_out_var.get().strip())
        # AQUA Info
        _set('aquaserver',         self.aqua_server_var.get().strip())
        _set('aqua_cmd_path',      self.aqua_cmd_var.get().strip())
        _set('reportPath',         self.report_path_var.get().strip())
        _aqua_out = self.aqua_out_var.get().strip()
        # Save Data CSV under the canonical "DataCSV" key; remove legacy aliases
        for _old in ('outputFilename', 'aqua_outputfile', 'output'):
            merged.pop(_old, None)
        merged.pop('extra_csv_files', None)
        if _aqua_out:
            _all_lb = list(self._data_csv_lb.get(0, tk.END)) if hasattr(self, '_data_csv_lb') else [_aqua_out]
            merged['DataCSV'] = _all_lb if len(_all_lb) > 1 else _aqua_out
        # Bindef Info
        _set('TestProgram_folder', self.tp_folder_var.get().strip())
        _set('TestProgram',        self.testprogram_var.get().strip())
        _set('identifier',         self.testprogram_id_var.get().strip())
        # Parametric / PCM Options
        _pcm_full = getattr(self, 'pcm_full_site_var', None)
        if _pcm_full is not None:
            merged['pcm_full_site'] = bool(_pcm_full.get())
        else:
            merged.pop('pcm_full_site', None)
        _set('pcm_spec_csv',
             getattr(self, 'pcm_spec_csv_var', None) and
             self.pcm_spec_csv_var.get().strip())
        _run_par = getattr(self, 'run_parametric_var', None)
        if _run_par is not None:
            merged['run_parametric'] = bool(_run_par.get())
        else:
            merged.pop('run_parametric', None)
        # PCM filter / groups
        _pcm_filt = getattr(self, 'pcm_custom_filter_var', None)
        if _pcm_filt is not None:
            _set('pcm_custom_filter', _pcm_filt.get().strip())
        if hasattr(self, '_pcm_grp_vars') and hasattr(self, '_pcm_groups'):
            sel = [self._pcm_groups[i]['name']
                   for i, v in enumerate(self._pcm_grp_vars)
                   if v.get() and i < len(self._pcm_groups)]
            # Read the default selection from the product config JSON
            _cfg_default_sel = getattr(self, '_pcm_cfg_selected', None) or []
            _all_selected = len(sel) == len(self._pcm_groups)
            _matches_default = (
                _cfg_default_sel and
                set(sel) == set(_cfg_default_sel)
            )
            if _all_selected or _matches_default:
                merged.pop('pcm_selected_groups', None)
            else:
                merged['pcm_selected_groups'] = sel

        base_dir = os.path.dirname(self.input_path) if self.input_path else os.getcwd()
        default_name = (os.path.splitext(os.path.basename(self.input_path))[0] + '_edited.json'
                        if self.input_path else 'pipeline_run.json')
        save_path = filedialog.asksaveasfilename(
            title='Save merged JSON as', defaultextension='.json',
            initialdir=base_dir, initialfile=default_name,
            filetypes=[('JSON', '*.json')])
        if not save_path:
            return
        try:
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
        except Exception as e:
            messagebox.showerror('Save failed', str(e))
            return
        messagebox.showinfo('Saved', f'Merged JSON saved to {save_path}')

