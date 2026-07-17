"""pipeline.py — PipelineFrame orchestrator.

Assembles PipelineFrame from focused mixin modules:
  _pipeline_server.py  — HTTP file-opener server
  _pipeline_ui.py      — UI build + browse dialogs + load/save JSON
  _pipeline_runner.py  — run_pipeline, post-pipeline steps, SICC runner
  _pipeline_html.py    — _build_pareto_html, _build_master_html, _update_dashboard_html

Only PipelineFrame.__init__, PipelineGUI, and the __main__ CLI block live here.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

# ── Module-level constants (shared with mixin modules) ───────────────────────
_SRC_DIR  = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_SRC_DIR, '..'))

# When frozen by PyInstaller sys.executable IS the exe — never use it to run
# .py helper scripts or the exe will re-launch itself.  Use a real python.exe
# instead, located next to the exe (for developer installs) or skip the
# subprocess entirely and call the helper module directly.
_FROZEN = getattr(sys, 'frozen', False)
_PYTHON = sys.executable if not _FROZEN else None  # None = no external python available
_LOADER = os.path.join(_SRC_DIR, '_loader.py')    # dispatches to compiled .pyd modules
# Sibling scripts — all relative to _SRC_DIR
SICC_UPM_SCRIPT      = os.path.normpath(os.path.join(_ROOT_DIR, '..', 'sicc_upm', 'src', 'run_dashboard.py'))
SICC_CDYN_UPM_SCRIPT = os.path.normpath(os.path.join(_ROOT_DIR, '..', 'sicc_cdyn_upm', 'src', 'run_dashboard.py'))

# ── Mixin imports ─────────────────────────────────────────────────────────────
from _pipeline_server  import OpenerServerMixin
from _pipeline_ui      import PipelineUIMixin
from _pipeline_runner  import PipelineRunnerMixin
from _pipeline_html    import PipelineHtmlMixin


class PipelineFrame(
    OpenerServerMixin,
    PipelineUIMixin,
    PipelineRunnerMixin,
    PipelineHtmlMixin,
    tk.Frame,
):
    """Main yield-analysis GUI panel.  Embed in any tk.Tk window."""

    def __init__(self, parent=None, **kw):
        super().__init__(parent, bg='#1a252f', **kw)
        self.input_path = None
        self.json_data  = {}
        self.fields     = {}
        self._opener_port = self._start_opener_server()
        self.after(200, self._poll_open_queue)
        self._build_ui()


class PipelineGUI(tk.Tk):
    """Standalone wrapper — embeds PipelineFrame in a Tk root window."""

    def __init__(self):
        super().__init__()
        self.title('Yield Analysis Dashboard')
        self.geometry('1200x800')
        frame = PipelineFrame(self)
        frame.pack(fill=tk.BOTH, expand=True)
        # expose frame attrs on self for any legacy code
        self._frame = frame


if __name__ == '__main__':
    import argparse as _ap

    _p = _ap.ArgumentParser(add_help=False)
    _p.add_argument('csv',        nargs='?', default=None)
    _p.add_argument('output_dir', nargs='?', default=None)
    _p.add_argument('--json', '-j', dest='json_file', default=None,
                    help='Run full pipeline from an existing input.json file')
    _p.add_argument('--run',       action='store_true',
                    help='Run full pipeline headlessly (skip AQUA, use local CSV/GZ as input)')
    _p.add_argument('--tag',       default=None)
    _p.add_argument('--testprogram', '--tp', dest='testprogram', default=None,
                    help='TestProgram name (auto-detected from CSV if omitted)')
    _p.add_argument('--tp-folder', dest='tp_folder', default=None,
                    help='Folder containing BinDefinitions.bdefs (auto-loaded from product_config_json if omitted)')
    _p.add_argument('--product_config_json', '--yieldtarget', dest='product_config_json', default=None)
    _known, _rest = _p.parse_known_args()

    if _known.json_file:
        # ── JSON file mode: auto-fill defaults from CSV, then run pipeline ──
        import csv as _csv_mod
        import json as _json_mod
        import subprocess as _sp
        from collections import Counter as _Counter
        from pathlib import Path as _Pj

        _jpath = _Pj(_known.json_file).resolve()
        if not _jpath.exists():
            print(f'ERROR: JSON file not found: {_jpath}')
            sys.exit(1)
        _cfg = _json_mod.loads(_jpath.read_text(encoding='utf-8'))

        # Normalise DataCSV / aqua_outputfile → outputFilename
        if 'DataCSV' in _cfg and 'outputFilename' not in _cfg:
            _dcv = _cfg.pop('DataCSV')
            if isinstance(_dcv, list) and _dcv:
                _cfg['outputFilename'] = _dcv[0]
                if len(_dcv) > 1 and 'extra_csv_files' not in _cfg:
                    _cfg['extra_csv_files'] = _dcv[1:]
            else:
                _cfg['outputFilename'] = _dcv
        elif 'aqua_outputfile' in _cfg and 'outputFilename' not in _cfg:
            _cfg['outputFilename'] = _cfg.pop('aqua_outputfile')

        # Override identifier/tag if --tag passed on CLI
        if _known.tag:
            _cfg['identifier'] = _known.tag

        _src_dir  = _Pj(__file__).parent
        _csv_path = _Pj(_cfg['outputFilename']).resolve() if _cfg.get('outputFilename') else None

        # ── Multi-CSV merge: if extra_csv_files present, concat all into one temp CSV ──
        _extra_csvs = _cfg.get('extra_csv_files', [])
        _merge_tmp_path = None  # track for cleanup at end
        if _extra_csvs and _csv_path and _csv_path.exists():
            import pandas as _pd_merge
            import tempfile as _merge_tmp_mod
            print(f'[json] Multi-CSV  : merging {1 + len(_extra_csvs)} CSV files…')
            _frames = []
            for _mp in [_csv_path] + [_Pj(x) for x in _extra_csvs]:
                if _Pj(_mp).exists():
                    try:
                        _frames.append(_pd_merge.read_csv(str(_mp)))
                        print(f'[json]   + {_Pj(_mp).name} ({len(_frames[-1])} rows)')
                    except Exception as _me:
                        print(f'[json] WARNING: could not read {_mp}: {_me}')
                else:
                    print(f'[json] WARNING: extra CSV not found: {_mp}')
            if len(_frames) > 1:
                _merged_df = _pd_merge.concat(_frames, ignore_index=True).drop_duplicates()
                # ── Coalesce session-suffixed columns ─────────────────────────────
                # When CSVs from different test sessions are merged, columns like
                # SORT_LOT, DATA_BIN, DevRevStep appear both with no suffix (L0 CSV)
                # and with a _NNNNNN session suffix (R0 CSV).  Downstream code
                # (_normalise_sort_cols, getBinCol, etc.) expects a single canonical
                # column; duplicate names cause "DataFrame has no attribute unique".
                # General fix: for every suffixed column base_NNNNNN that has a
                # matching unsuffixed base column, back-fill the base from the
                # suffixed values (filling NaN for the other program's rows).
                import re as _re_cs
                _sess_re = _re_cs.compile(r'^(.+)_(\d{6})$')
                _suffix_groups: dict = {}
                for _c in _merged_df.columns:
                    if _c.startswith('TPI_BIN'):  # skip per-test TPI columns
                        continue
                    _m = _sess_re.match(_c)
                    if _m:
                        _base = _m.group(1)
                        _suffix_groups.setdefault(_base, []).append(_c)
                _n_coalesced = 0
                _new_base_cols: dict = {}
                for _base, _extras in _suffix_groups.items():
                    if _base in _merged_df.columns:
                        # Update existing column in-place (no fragmentation)
                        _merged_df[_base] = _merged_df[[_base] + _extras].bfill(axis=1).iloc[:, 0]
                    else:
                        # Collect new columns — concat all at once below
                        _new_base_cols[_base] = _merged_df[_extras].bfill(axis=1).iloc[:, 0]
                    _n_coalesced += 1
                if _new_base_cols:
                    # Single concat is O(n) vs O(n²) for repeated insert
                    _merged_df = _pd_merge.concat(
                        [_pd_merge.DataFrame(_new_base_cols, index=_merged_df.index),
                         _merged_df],
                        axis=1
                    )
                if _n_coalesced:
                    print(f'[json] Multi-CSV  : coalesced {_n_coalesced} session-suffixed column group(s)')
                _mt = _merge_tmp_mod.NamedTemporaryFile(
                    dir=str(_csv_path.parent), suffix='_merged.csv',
                    prefix='pipeline_multi_', delete=False, mode='w',
                    newline='', encoding='utf-8')
                _merged_df.to_csv(_mt, index=False)
                _mt.close()
                _merge_tmp_path = _mt.name
                _csv_path = _Pj(_merge_tmp_path)
                _cfg['outputFilename'] = _merge_tmp_path
                print(f'[json] Multi-CSV  : merged {_merged_df.shape[0]} rows → temp file')

        # ── Find repo root (shared/ sibling) ────────────────────────────
        def _find_repo(start):
            cur = _Pj(start).resolve()
            for _ in range(12):
                if (cur / 'shared').exists():
                    return cur
                p = cur.parent
                if p == cur:
                    break
                cur = p
            return (_src_dir / '..' / '..' / '..' / '..').resolve()

        _repo_root = _find_repo(_src_dir)
        _prod_cfg_dir = (
            _repo_root / 'shared' / 'setup' / 'config' / 'yield-dashboard'
            if (_repo_root / 'shared' / 'setup' / 'config' / 'yield-dashboard').exists()
            else _repo_root / 'shared' / 'spec' / 'collateral' / 'yield'
        )

        # ── Auto-detect TestProgram + DevRevStep from CSV ────────────────
        if _csv_path and _csv_path.exists():
            import gzip as _gz_mod, zipfile as _zip_mod, io as _io_mod
            def _csv_rows(path, n=500):
                try:
                    _p = str(path).lower()
                    if _p.endswith('.gz'):
                        _fh = _gz_mod.open(str(path), 'rt', encoding='utf-8', errors='replace')
                    elif _p.endswith('.zip'):
                        _zf = _zip_mod.ZipFile(str(path))
                        _inner = next((nm for nm in _zf.namelist() if not nm.endswith('/')), None)
                        if not _inner:
                            return
                        _fh = _io_mod.TextIOWrapper(_zf.open(_inner), encoding='utf-8', errors='replace')
                    elif _p.endswith('.7z'):
                        import tempfile as _7z_tmp, subprocess as _7z_sub, shutil as _7z_shu
                        _7z_td = _7z_tmp.mkdtemp(prefix='_csv_rows_7z_')
                        _7z_rows = []
                        try:
                            _7z_sub.run([r'C:\Program Files\7-Zip\7z.exe', 'e', str(path),
                                         f'-o{_7z_td}', '-y'], capture_output=True, timeout=60)
                            _7z_csv = next(iter(sorted(_Pj(_7z_td).glob('*.csv'))), None)
                            if _7z_csv:
                                with open(str(_7z_csv), 'rt', encoding='utf-8', errors='replace') as _7z_fh:
                                    _7z_rdr = _csv_mod.DictReader(_7z_fh)
                                    for _7z_i, _7z_row in enumerate(_7z_rdr):
                                        if _7z_i >= n:
                                            break
                                        _7z_rows.append(_7z_row)
                        finally:
                            _7z_shu.rmtree(_7z_td, ignore_errors=True)
                        yield from _7z_rows
                        return
                    else:
                        _fh = open(str(path), 'rt', encoding='utf-8', errors='replace')
                    with _fh:
                        rdr = _csv_mod.DictReader(_fh)
                        for i, row in enumerate(rdr):
                            if i >= n:
                                break
                            yield row
                except Exception:
                    return

            _tp_vals, _dv_vals = [], []
            for _row in _csv_rows(_csv_path):
                _prog_col = next((h for h in _row if h and 'program' in h.lower()), None)
                _dv_col   = next((h for h in _row if h and h.lower().startswith('devrevstep')), None)
                if _prog_col:
                    _v = (_row.get(_prog_col) or '').strip()
                    if _v:
                        _tp_vals.append(_v)
                if _dv_col:
                    _v = (_row.get(_dv_col) or '').strip()
                    if _v:
                        _dv_vals.append(_v)

            _tp_csv = _Counter(_tp_vals).most_common(1)[0][0] if _tp_vals else ''
            _dv_csv = _Counter(_dv_vals).most_common(1)[0][0] if _dv_vals else ''

            # Fill TestProgram if missing
            if not _cfg.get('TestProgram') and _tp_csv:
                _cfg['TestProgram'] = _tp_csv
                print(f'[json] Auto-detected TestProgram : {_tp_csv}')

            # Fill identifier = TestProgram if missing
            if not _cfg.get('identifier') and _cfg.get('TestProgram'):
                _cfg['identifier'] = _cfg['TestProgram']
                print(f'[json] Auto-set identifier       : {_cfg["identifier"]}')

            # Fallback: use identifier as TestProgram if still not set
            if not _cfg.get('TestProgram') and _cfg.get('identifier'):
                _cfg['TestProgram'] = _cfg['identifier']
                print(f'[json] Auto-set TestProgram from identifier: {_cfg["TestProgram"]}')

            # Auto-detect product_config_json from DevRevStep
            if not _cfg.get('product_config_json') and _dv_csv:
                _dv = _dv_csv.upper()
                _dv6 = _dv[:6]
                _candidates = (
                    list(_prod_cfg_dir.glob('* - SORT - *.json')) +
                    list(_prod_cfg_dir.glob('Product Config*.json'))
                )
                def _matches(p):
                    _tok = p.stem.split(' - ')[0].strip().upper()
                    return _dv.startswith(_tok) or _tok.startswith(_dv6)
                _match = next((p for p in _candidates if _matches(p)), None)
                if _match:
                    _cfg['product_config_json'] = str(_match)
                    print(f'[json] Auto-detected product_config: {_match.name}')

        # ── Pull defaults from product_config_json ───────────────────────
        _pcfg_path = _cfg.get('product_config_json', '')
        if _pcfg_path and _Pj(_pcfg_path).exists():
            try:
                _pcfg = _json_mod.loads(_Pj(_pcfg_path).read_text(encoding='utf-8'))
                if not _cfg.get('pcm_spec_csv') and _pcfg.get('pcm_spec_csv'):
                    _spec = str(_pcfg['pcm_spec_csv']).strip()
                    if not _Pj(_spec).is_absolute():
                        _spec = str((_Pj(_pcfg_path).parent / _spec).resolve())
                    _cfg['pcm_spec_csv'] = _spec
                    print(f'[json] Auto-set pcm_spec_csv     : {_Pj(_spec).name}')
                if not _cfg.get('TestProgram_folder') and _pcfg.get('testprogram_folder'):
                    _cfg['TestProgram_folder'] = str(_pcfg['testprogram_folder']).strip()
                    print(f'[json] Auto-set TestProgram_folder: {_cfg["TestProgram_folder"]}')
            except Exception:
                pass

        # ── Defaults: run_parametric, output_folder, dashboard ───────────
        _cfg.setdefault('run_parametric', True)
        if not _cfg.get('output_folder') and _csv_path:
            _cfg['output_folder'] = str(_csv_path.parent / 'output')
        if not _cfg.get('dashboard') and _cfg.get('output_folder'):
            _id0 = _cfg.get('identifier', '')
            _sid0 = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in _id0)
            _dash_base0 = _Pj(_cfg['output_folder']) / _sid0 if _sid0 else _Pj(_cfg['output_folder'])
            _cfg['dashboard'] = str(_dash_base0 / 'Dashboard.html')

        _loader = _src_dir / '_loader.py'
        _base   = _csv_path.parent if _csv_path else _jpath.parent

        print(f'[json] Config     : {_jpath}')
        print(f'[json] CSV        : {_cfg.get("outputFilename", "(not set)")}')
        print(f'[json] Output     : {_cfg.get("output_folder", "(not set)")}')
        print(f'[json] Dashboard  : {_cfg.get("dashboard", "(not set)")}')
        print(f'[json] Program    : {_cfg.get("TestProgram", "(not set)")}')
        print(f'[json] Tag        : {_cfg.get("identifier", "(not set)")}')
        print(f'[json] ProdConfig : {_cfg.get("product_config_json", "(not set)")}')
        print(f'[json] Parametric : {_cfg.get("run_parametric", False)}')

        # ── Multi-TP: comma/semicolon-separated identifiers → use first as primary ──
        # First TP drives: identifier (output folder), TestProgram (bindef), product config.
        # The sort CSV already contains data for all TPs; mixed-DevRevStep material join
        # and reticle retMaps handle the rest automatically.
        import re as _re_tp, shutil as _shutil
        def _split_ids(val):
            if isinstance(val, list):
                return [str(v).strip() for v in val if str(v).strip()]
            if isinstance(val, str):
                return [p.strip() for p in _re_tp.split(r'[,;\n\|]+', val) if p.strip()]
            return []
        _tp_ids = (_split_ids(_cfg.get('identifier', ''))
                   or _split_ids(_cfg.get('TestProgram', '')))
        if len(_tp_ids) > 1:
            _first_tp = _tp_ids[0]
            print(f'[json] Multi-TP   : {len(_tp_ids)} TPs specified; using first as primary: {_first_tp}')
            print(f'[json]             remaining TPs in CSV handled via mixed-DevRevStep pipeline')
            _cfg['identifier']  = _first_tp
            _cfg['TestProgram'] = _first_tp
            # Rebuild dashboard path from first TP's output subfolder
            _tp_safe0 = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in _first_tp)
            _tp_out0  = (str(_Pj(_cfg.get('output_folder', str(_base))) / _tp_safe0)
                         if _tp_safe0 else _cfg.get('output_folder', str(_base)))
            _cfg['dashboard'] = str(_Pj(_tp_out0) / 'Dashboard.html')

        # ── Force-clean output subfolder before run ──────────────────────
        _pre_out_folder = _cfg.get('output_folder', str(_base))
        _pre_identifier = _cfg.get('identifier', '')
        _pre_safe_id    = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in _pre_identifier)
        if _pre_out_folder and _pre_safe_id:
            _pre_clean_dir = _Pj(_pre_out_folder) / _pre_safe_id
            if _pre_clean_dir.is_dir():
                # Clear contents but keep the directory itself.
                # Deleting and recreating the directory on a Samba/UNC share causes
                # SMB client cache races (WinError 183 / WinError 3) in subprocesses
                # that try to mkdir or write files there immediately after.
                for _item in _pre_clean_dir.iterdir():
                    try:
                        if _item.is_dir():
                            _shutil.rmtree(str(_item))
                        else:
                            _item.unlink()
                    except OSError:
                        pass
                print(f'[json] Cleaned output folder: {_pre_clean_dir}')

        # ── Step 1: yield_pipeline ───────────────────────────────────────
        _cmd = [sys.executable, str(_loader), 'yield_pipeline',
                '--input', '-', '--base', str(_base)]
        _result = _sp.run(_cmd, input=_json_mod.dumps(_cfg),
                          capture_output=False, text=True, timeout=1800)
        if _result.returncode != 0:
            sys.exit(_result.returncode)

        # ── Step 1.5: wafermap (before HTML build so index links work) ──
        print('\n[json] Generating wafermap...')
        _sort_csv   = _cfg.get('outputFilename', '')
        _out_folder = _cfg.get('output_folder', str(_base))

        # ── Expand .7z TP cache → plain CSV so pandas can read it ────────────
        _7z_tmp_dir = None
        if _sort_csv and _sort_csv.lower().endswith('.7z') and _Pj(_sort_csv).is_file():
            import tempfile as _7z_tmp_mod, subprocess as _7z_sp, shutil as _7z_sh
            _7z_exe = r'C:\Program Files\7-Zip\7z.exe'
            _7z_tmp_dir = _7z_tmp_mod.mkdtemp(prefix='pipeline_7z_')
            _7z_r = _7z_sp.run(
                [_7z_exe, 'e', _sort_csv, f'-o{_7z_tmp_dir}', '-y'],
                capture_output=True, text=True)
            _extracted = sorted(_Pj(_7z_tmp_dir).glob('*.csv'))
            if _extracted and _7z_r.returncode == 0:
                _sort_csv = str(_extracted[0])
                _cfg['outputFilename'] = _sort_csv
                print(f'[json] Extracted .7z  → {_Pj(_sort_csv).name}')
            else:
                print(f'[json] WARNING: .7z extraction failed (rc={_7z_r.returncode}): {_7z_r.stderr[:200]}')
                _7z_sh.rmtree(_7z_tmp_dir, ignore_errors=True)
                _7z_tmp_dir = None
        _dash_html  = _cfg.get('dashboard', '')
        _identifier = _cfg.get('identifier', '')
        _prod_cfg   = _cfg.get('product_config_json', '')
        # Derive the actual output subfolder (mirrors _pipeline_runner logic)
        _safe_id    = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in _identifier)
        _actual_out = str(_Pj(_out_folder) / _safe_id) if _safe_id else _out_folder
        _bindef_csv = str(_Pj(_actual_out) / f'{_identifier}_bindef.csv')
        # Fallback: if identifier includes a test-id suffix (e.g. PROG_119325),
        # yield_pipeline names the bindef after just the program (PROG_bindef.csv).
        if not _Pj(_bindef_csv).exists():
            import glob as _glob_bd
            _bindef_candidates = sorted(_glob_bd.glob(str(_Pj(_actual_out) / '*_bindef.csv')))
            if _bindef_candidates:
                _bindef_csv = _bindef_candidates[0]

        # ── Step 1.45: join Material column into sort CSV ─────────────────
        # Supports mixed-DevRevStep inputs: each unique DevRevStep is matched
        # to its own material CSV and all are merged into a single lookup table.
        if _sort_csv and _Pj(_sort_csv).is_file():
            try:
                import pandas as _mpd_mat
                import glob as _matglob
                _mdf = _mpd_mat.read_csv(_sort_csv, low_memory=False)
                # Join when Material column has any missing rows (partial or fully absent)
                _mat_col_empty = (
                    'Material' not in _mdf.columns
                    or _mdf['Material'].isna().any()
                    or (_mdf['Material'].astype(str).str.strip().eq('').any())
                )
                # Detect lot/wafer columns — prefer SORT_LOT
                _sort_lot_col = next(
                    (c for c in _mdf.columns if c == 'SORT_LOT'), None)
                _sort_wafer_col = next(
                    (c for c in _mdf.columns if 'sort_wafer' in c.lower()), None) or next(
                    (c for c in _mdf.columns if 'wafer' in c.lower()), None)
                if _mat_col_empty and _sort_lot_col and _sort_wafer_col:
                    _mat_dir = str(_repo_root / 'shared' / 'material')
                    _mat_src_col = 'Material Type, Skew, BEOL Skew'
                    _all_mat_csv = sorted(_matglob.glob(os.path.join(_mat_dir, '*.csv')))

                    # Build join keys in the sort data
                    _mdf['_m_lot7'] = _mdf[_sort_lot_col].astype(str).str[:7]
                    _mdf['_m_wafer'] = _mpd_mat.to_numeric(
                        _mdf[_sort_wafer_col], errors='coerce').apply(
                        lambda w: int(w) % 100 if _mpd_mat.notna(w) else None)

                    # Read ALL material CSVs that have the required columns.
                    # No DevRevStep guessing — the INTEL_LOT7 + WaferID join keys
                    # determine which rows actually match.
                    _mat_frames = []
                    _seen_csv = set()
                    for _mcp in _all_mat_csv:
                        _df_m = _mpd_mat.read_csv(_mcp, low_memory=False)
                        _df_m.columns = [c.strip() for c in _df_m.columns]
                        if ('INTEL_LOT7' in _df_m.columns and 'WaferID' in _df_m.columns
                                and _mat_src_col in _df_m.columns):
                            # Truncate INTEL_LOT7 to 7 chars to match yield lot IDs
                            _df_m['_m_lot7'] = _df_m['INTEL_LOT7'].astype(str).str.strip().str[:7]
                            _df_m['_m_wafer'] = _mpd_mat.to_numeric(_df_m['WaferID'], errors='coerce')
                            _mat_frames.append(
                                _df_m[['_m_lot7', '_m_wafer', _mat_src_col]]
                                .drop_duplicates(subset=['_m_lot7', '_m_wafer']))
                            _seen_csv.add(_mcp)
                        else:
                            print(f'[json] Material CSV missing expected columns: '
                                  f'{os.path.basename(_mcp)}')

                    if _mat_frames:
                        _mat_lookup = (_mpd_mat.concat(_mat_frames, ignore_index=True)
                                       .drop_duplicates(subset=['_m_lot7', '_m_wafer'], keep='first'))
                        # Split into precise (lot+wafer) and wildcard (lot-only, no WaferID) rows
                        _lkp_w  = _mat_lookup[_mat_lookup['_m_wafer'].notna()]
                        _lkp_lo = (_mat_lookup[_mat_lookup['_m_wafer'].isna()]
                                   .drop_duplicates(subset=['_m_lot7'], keep='first'))
                        # Save existing material values to restore after merge
                        _orig_mat = _mdf['Material'].copy() if 'Material' in _mdf.columns else None
                        # Drop the stale Material column before merging
                        _mdf.drop(columns=['Material'], inplace=True, errors='ignore')
                        # Pass 1: precise lot+wafer merge
                        _mdf = _mdf.merge(_lkp_w, on=['_m_lot7', '_m_wafer'], how='left')
                        # Pass 2: lot-only fallback for rows still missing material
                        if not _lkp_lo.empty:
                            _still_miss = _mdf[_mat_src_col].isna()
                            if _still_miss.any():
                                _lo2 = _lkp_lo[['_m_lot7', _mat_src_col]].rename(
                                    columns={'_m_lot7': '_lo_lot7'})
                                _fb = _mdf.loc[_still_miss, ['_m_lot7']].merge(
                                    _lo2, left_on='_m_lot7', right_on='_lo_lot7', how='left'
                                ).drop(columns=['_m_lot7', '_lo_lot7'])
                                _fb.index = _mdf.index[_still_miss]
                                _mdf.loc[_still_miss, _mat_src_col] = _fb[_mat_src_col].values
                        _mdf.rename(columns={_mat_src_col: 'Material'}, inplace=True)
                        # Restore pre-existing material values where new merge found nothing
                        if _orig_mat is not None:
                            _orig_mat = _orig_mat.reset_index(drop=True)
                            _mdf['Material'] = _mdf['Material'].reset_index(drop=True).where(
                                _mdf['Material'].notna(), _orig_mat)
                        _mdf.drop(columns=['_m_lot7', '_m_wafer'], inplace=True, errors='ignore')
                        _n_mat = int(_mdf['Material'].notna().sum())
                        # If input was a ZIP, write the enriched data to a plain .csv so
                        # downstream steps (wafermap, bin_distribution) can read it normally.
                        if _sort_csv.lower().endswith('.zip'):
                            _sort_csv_write = _sort_csv[:-4] + '.csv'
                        else:
                            _sort_csv_write = _sort_csv
                        _mdf.to_csv(_sort_csv_write, index=False)
                        _sort_csv = _sort_csv_write   # update for all downstream steps
                        _csv_names = ', '.join(os.path.basename(p) for p in _seen_csv)
                        print(f'[json] Material joined into sort CSV: {_n_mat:,}/{len(_mdf):,} rows '
                              f'({_csv_names})')
                        if _sort_csv != _cfg.get('outputFilename', ''):
                            print(f'[json] Enriched CSV written to   : {_sort_csv}')
                    else:
                        _mdf.drop(columns=['_m_lot7', '_m_wafer'], inplace=True, errors='ignore')
            except Exception as _mat_ex:
                print(f'[json] WARNING: Material join failed: {_mat_ex}')
                import traceback as _tb_mat; _tb_mat.print_exc()
        _wm_cmd = [sys.executable, str(_loader), 'generate_heatmap_from_csv',
                   _sort_csv, _actual_out]
        if _prod_cfg and _Pj(_prod_cfg).is_file():
            _wm_cmd.append(_prod_cfg)
        _wm_cmd.append('--wafermap-only')
        if _Pj(_bindef_csv).exists():
            _wm_cmd += [f'--bindef={_bindef_csv}']
        _wm_result = _sp.run(_wm_cmd, capture_output=False, text=True, timeout=1800)
        if _wm_result.returncode != 0:
            print(f'[json] WARNING: wafermap exited with code {_wm_result.returncode}')

        # ── Step 1.6: generate BinDistribution HTML (yield dashboard) ──────
        print('\n[json] Generating BinDistribution HTML...')
        _bin_cmd = [sys.executable, str(_loader), 'bin_distribution_html',
                    _sort_csv, _actual_out]
        if _prod_cfg and _Pj(_prod_cfg).is_file():
            _bin_cmd.append(_prod_cfg)
        # tbl_path (bindef CSV) is auto-discovered from out_dir; pass only if explicit
        _bin_result = _sp.run(_bin_cmd, capture_output=False, text=True, timeout=1800)
        if _bin_result.returncode != 0:
            print(f'[json] WARNING: bin_distribution_html exited with code {_bin_result.returncode}')

        # ── Step 1.7: SICC/CDYN/UPM analysis → *_sicc_analysis.html ───────
        print('\n[json] Generating SICC/CDYN/UPM analysis HTML...')
        _sicc_src = (_src_dir / '..' / '..' / 'sicc_cdyn_upm' / 'src').resolve()
        _csv_stem = _Pj(_sort_csv).stem
        _sicc_html_out = str(_Pj(_actual_out) / f'{_csv_stem}_sicc_analysis.html')
        _sicc_script = (
            'import sys, json as _jmod, os as _os\n'
            'sys.path.insert(0, ' + repr(str(_sicc_src)) + ')\n'
            'from sicc_processor import load_config as _lc, process_csv as _pc\n'
            'from generate_dashboard_html_svg import generate_html_svg as _gh\n'
            '_cfg_path = ' + repr(str(_sicc_src / 'sicc_cdyn_testlist.json')) + '\n'
            '_cfg = _lc(_cfg_path) if _os.path.isfile(_cfg_path) else {}\n'
            '_prod_cfg_path = ' + repr(_prod_cfg) + '\n'
            'if _prod_cfg_path and _os.path.isfile(_prod_cfg_path):\n'
            '    try:\n'
            '        _pcdata = _jmod.loads(open(_prod_cfg_path, encoding="utf-8").read())\n'
            '        for _key in ("siccList","siccTotalList","cdynList","upmInfo","SiccTableConfig","cdynTableConfig"):\n'
            '            if _key in _pcdata: _cfg[_key] = _pcdata[_key]\n'
            '        _ov_tgt = {str(e.get("test","")).upper(): float(e["target_A"]) for e in _pcdata.get("sicc_targets",[]) if e.get("test") and e.get("target_A") is not None}\n'
            '        _ov_cdyn = {str(e.get("test","")): float(e["target_nF"]) for e in _pcdata.get("cdyn_targets",[]) if e.get("test") and e.get("target_nF") is not None}\n'
            '    except Exception as _e: print("[sicc] product config error:", _e); _ov_tgt = {}; _ov_cdyn = {}\n'
            'else:\n'
            '    _ov_tgt = {}; _ov_cdyn = {}\n'
            'try:\n'
            '    _data = _pc(' + repr(_sort_csv) + ', _cfg,'
            '        override_targets=_ov_tgt or None,'
            '        override_cdyn_targets=_ov_cdyn or None)\n'
            '    _gh(_data, ' + repr(_sicc_html_out) + ')\n'
            '    print("[sicc] Written:", ' + repr(_sicc_html_out) + ')\n'
            'except Exception as _e:\n'
            '    print("[sicc] FAILED:", _e)\n'
            '    import traceback as _tb; _tb.print_exc()\n'
        )
        _sicc_result = _sp.run([sys.executable, '-c', _sicc_script],
                               capture_output=True, text=True, timeout=1800)
        if _sicc_result.stdout:
            print(_sicc_result.stdout, end='')
        if _sicc_result.stderr:
            print('[json] SICC stderr:\n', _sicc_result.stderr, end='')
        if _sicc_result.returncode != 0:
            print(f'[json] WARNING: SICC analysis exited with code {_sicc_result.returncode}')

        # ── Step 2: parametric_runner (runs before HTML so pcm_analysis.html is ready) ──
        if _cfg.get('run_parametric'):
            _par_runner = (_src_dir / '..' / '..' / 'sort-parametric' / 'parametric_runner.py').resolve()
            if not _par_runner.exists():
                print(f'[json] WARNING: parametric_runner.py not found at {_par_runner}')
            else:
                _pcm_setup = next((
                    str(p) for p in [
                        _repo_root / 'shared' / 'setup' / 'etest-dashboard' / 'pcm_product_setup.json',
                        _repo_root / 'shared' / 'spec' / 'collateral' / 'etest' / 'pcm_product_setup.json',
                        _repo_root / 'shared' / 'etest' / 'collateral' / 'pcm_product_setup.json',
                        _repo_root / 'shared' / 'etest' / 'spec' / 'pcm_product_setup.json',
                    ] if _Pj(p).exists()
                ), None)
                _par_cmd = [
                    sys.executable, str(_par_runner),
                    '--sort-csv',   _cfg.get('outputFilename', ''),
                    '--outdir',     _actual_out,
                    '--deploy-dir', _actual_out,
                    '--identifier', _cfg.get('identifier', ''),
                    '--config',     str(_jpath),
                ]
                if _cfg.get('pcm_full_site'):
                    _par_cmd.append('--full-site')
                if _cfg.get('keep_pcm_idw'):
                    _par_cmd.append('--keep-pcm-idw')
                _merged_csv = _cfg.get('merged_csv', '')
                if _merged_csv and _Pj(_merged_csv).exists():
                    _par_cmd += ['--merged-csv', _merged_csv]
                if _pcm_setup:
                    _par_cmd += ['--product-setup', _pcm_setup]
                _spec_csv = _cfg.get('pcm_spec_csv', '')
                if _spec_csv and _Pj(_spec_csv).exists():
                    _par_cmd += ['--spec-csv', _spec_csv]
                _prod_cfg_path = _cfg.get('product_config_json', '')
                if _prod_cfg_path and _Pj(_prod_cfg_path).exists():
                    _par_cmd += ['--product-config-json', _prod_cfg_path]
                _pcm_filter = _cfg.get('pcm_filter', '')
                if _pcm_filter:
                    _par_cmd += ['--pcm-filter', _pcm_filter]

                print(f'\n[json] Launching parametric runner...')
                print(f'[json] CMD: {" ".join(_par_cmd)}\n')
                import os as _os
                _par_env = {**_os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'}
                _par_result = _sp.run(_par_cmd, capture_output=False, text=True, timeout=1800, env=_par_env)
                if _par_result.returncode != 0:
                    print(f'[json] WARNING: parametric_runner exited with code {_par_result.returncode}')

        # ── Step 3: build index.html + Dashboard.html ─────────────────
        print('\n[json] Building yield dashboard HTML...')
        print('[json]   (reading CSV, rendering charts — may take 1-2 min)')
        # Run HTML build as a clean subprocess using only _pipeline_html (no tkinter)
        _html_script = (
            'import sys, os; sys.path.insert(0, ' + repr(str(_src_dir)) + ');'
            'from _pipeline_html import PipelineHtmlMixin;'
            'from _pipeline_constants import _SRC_DIR, _ROOT_DIR, _FROZEN, _LOADER, SICC_UPM_SCRIPT, SICC_CDYN_UPM_SCRIPT\n'
            'class _Var:\n'
            '    def __init__(self,v=""): self._v=v\n'
            '    def get(self): return self._v\n'
            'class _H(PipelineHtmlMixin):\n'
            '    _opener_port=None\n'
            '    testprogram_id_var=_Var(' + repr(_identifier) + ')\n'
            '    fail_bucket_var=_Var(' + repr(_prod_cfg) + ')\n'
            '    output_folder_var=_Var(' + repr(_actual_out) + ')\n'
            '_h=_H()\n'
            'print("[json]   Building Digital Dashboard table + index.html...")\n'
            '_m=_h._build_master_html('
                + repr(_sort_csv) + ','
                + 'dashboard_path=' + repr(_dash_html or None) + ','
                + 'output_dir=' + repr(_actual_out) + ','
                + 'tag=' + repr(_identifier or None) + ','
                + 'bucket_json=' + repr(_prod_cfg or None) + ','
                + 'bindef_csv=' + repr(_bindef_csv if _Pj(_bindef_csv).exists() else None)
            + ')\n'
            'print("[json] Master report :", _m) if _m else print("[json] WARNING: master HTML build returned None")\n'
            'if _m:\n'
            '    print("[json]   Updating Dashboard.html...")\n'
            '    _h._update_dashboard_html('
                + repr(_sort_csv) + ','
                + 'master_html=_m,'
                + 'dashboard_html=' + repr(_dash_html or None) + ','
                + 'dashboard_html_dir=' + repr(str(_Pj(_dash_html).parent) if _dash_html else None) + ','
                + 'output_dir=' + repr(_actual_out)
            + ')\n'
            'print("[json]   HTML build complete.")\n'
        )
        _html_result = _sp.run(
            [sys.executable, '-c', _html_script],
            capture_output=False, text=True, timeout=1800)
        if _html_result.returncode != 0:
            print(f'[json] WARNING: HTML build exited with code {_html_result.returncode}')

        # Clean up merged temp CSV if one was created for multi-CSV run
        if _merge_tmp_path:
            try:
                os.remove(_merge_tmp_path)
            except Exception:
                pass

        # Clean up extracted .7z temp dir
        if _7z_tmp_dir:
            try:
                import shutil as _7z_cleanup_sh
                _7z_cleanup_sh.rmtree(_7z_tmp_dir, ignore_errors=True)
            except Exception:
                pass

        sys.exit(0)

    elif _known.csv and _known.run:
        app = PipelineGUI()
        app.mainloop()
