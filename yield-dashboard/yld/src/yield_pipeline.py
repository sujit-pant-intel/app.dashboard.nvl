"""
yield_pipeline.py

Usage: python yield_pipeline.py --input input.json

This is a clearer-named orchestrator for the project's pipeline. It performs:
 1) Optional AQUA fetch of yield CSV
 2) Parse BinDefinitions into a Crystal Ball CSV
 3) Run the dashboard updater to append columns B/C

The behavior and JSON fields are the same as the previous `run_pipeline.py`.
"""

from __future__ import annotations
import json
import argparse
import subprocess
import sys
from pathlib import Path
import re
import pandas as pd

ROOT = Path(__file__).resolve().parent
BASE: Path | None = None
_LOADER = str(ROOT / '_loader.py')  # dispatches to compiled .pyd modules

# Use python.exe (not pythonw.exe) for subprocess workers so their
# stdout/stderr is not suppressed when launched from a windowless process.
def _python_exe() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == 'pythonw.exe':
        candidate = exe.parent / 'python.exe'
        if candidate.exists():
            return str(candidate)
    return str(exe)


def resolve_path(p) -> Path:
    p = Path(str(p))
    if p.is_absolute():
        return p
    base = BASE if BASE is not None else ROOT
    return (base / p).resolve()


def run_aqua(cmd_path: Path, aquaserver: str, reportPath: str, outputFilename: Path) -> int:
    cmd = [str(cmd_path), '-aquaserver', aquaserver, '-reportPath', reportPath, '-outputFilename', str(outputFilename)]
    cmd_display = (
        f"{cmd[0]} -aquaserver \"{aquaserver}\" -reportPath \"{reportPath}\" "
        f"-outputFilename \"{outputFilename}\""
    )
    print('Running Aqua command: ', cmd_display)
    try:
        r = subprocess.run(cmd, check=False)
        return r.returncode
    except FileNotFoundError:
        print(f"Aqua command not found at {cmd_path}")
        return 3


def run_parse_bindef(bindef_path: Path, out_csv: Path) -> int:
    if getattr(sys, 'frozen', False):
        # Frozen: import the bundled module directly — no subprocess
        try:
            import parse_bindef_to_crystalball as _pb
            saved_argv = sys.argv
            sys.argv = ['parse_bindef_to_crystalball.py', '--bindef', str(bindef_path), '--out', str(out_csv)]
            try:
                rc = _pb.main()
                return rc or 0
            finally:
                sys.argv = saved_argv
        except SystemExit as e:
            return e.code or 0
        except Exception as e:
            print(f'parse_bindef failed: {e}')
            return 1
    # _loader.py resolves the module by import — no need to check for .py on disk
    out_arg = str(out_csv)
    if not out_csv.is_absolute():
        cwd = BASE if BASE is not None else ROOT
    else:
        cwd = None
    cmd = [_python_exe(), _LOADER, 'parse_bindef_to_crystalball', '--bindef', str(bindef_path), '--out', out_arg]
    print('Running parse_bindef:', ' '.join(cmd), 'cwd=' + str(cwd) if cwd else '')
    r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0 and r.stderr:
        print('parse_bindef stderr:', r.stderr[-500:])
    return r.returncode


def run_get_dd(data_csv: Path, bindef_csv: Path, dashboard: Path, out_dir: Path | None = None) -> int:
    if getattr(sys, 'frozen', False):
        # Frozen: run the bundled module as __main__ — no subprocess
        import runpy
        argv = ['get_dd_update.py',
                '--data', str(data_csv),
                '--bin_defs', str(bindef_csv),
                '--dashboard', str(dashboard)]
        if out_dir:
            argv += ['--outdir', str(out_dir)]
        saved_argv = sys.argv
        sys.argv = argv
        try:
            runpy.run_module('get_dd_update', run_name='__main__', alter_sys=True)
            return 0
        except SystemExit as e:
            return e.code or 0
        except Exception as e:
            print(f'get_dd_update failed: {e}')
            return 1
        finally:
            sys.argv = saved_argv
    # _loader.py resolves the module by import — no need to check for .py on disk
    cmd = [_python_exe(), _LOADER, 'get_dd_update', '--data', str(data_csv), '--bin_defs', str(bindef_csv), '--dashboard', str(dashboard)]
    if out_dir:
        cmd += ['--outdir', str(out_dir)]
    print('Running get_dd_update:', ' '.join(cmd))
    r = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0 and r.stderr:
        print('get_dd_update stderr:', r.stderr[-800:])
    return r.returncode


def main():
    p = argparse.ArgumentParser(description='Run full yield pipeline.')
    p.add_argument('--input', '-i', required=True, help='Input JSON file with parameters. Use - to read JSON from stdin')
    p.add_argument('--base', '-b', required=False, help='Optional base folder to resolve relative paths when reading JSON from stdin')
    args = p.parse_args()
    global BASE
    # Support reading JSON from stdin when --input - is used. In that case
    # the caller can provide --base to indicate the original JSON folder
    # for resolving relative paths.
    if args.input == '-':
        try:
            conf = json.load(sys.stdin)
        except Exception as e:
            print('Failed to read JSON from stdin:', e)
            sys.exit(2)
        if args.base:
            BASE = Path(args.base).resolve()
        else:
            BASE = Path.cwd().resolve()
    else:
        conf_path = resolve_path(args.input)
        if not conf_path.exists():
            print('Input JSON not found:', conf_path)
            sys.exit(2)
        conf = json.loads(conf_path.read_text())
        BASE = conf_path.resolve().parent

    required = ['outputFilename', 'TestProgram']
    for k in required:
        if k not in conf:
            print(f'Missing key in input JSON: {k}')
            sys.exit(3)

    aquaserver = conf.get('aquaserver', '')
    reportPath = conf.get('reportPath', '')
    # Resolve key paths/values
    outputFilename = resolve_path(conf['outputFilename'])
    TestProgram = conf['TestProgram']
    _tp_folder_raw = conf.get('TestProgram_folder', r'I:\program\1001\prod\hdmtprogs\nvl_ncx_sds')
    testprogram_folder = resolve_path(_tp_folder_raw)
    # Keep raw dashboard config until we know the data output folder (optional)
    conf_dashboard = conf.get('dashboard', '')
    aqua_cmd_path = resolve_path(conf.get('aqua_cmd_path', '\\FMSAPP3301.amr.corp.intel.com\\Installer\\AquaHbase\\AquaCMDClient\\Client\\AquaCmdLine.exe'))

    print('Skipping aqua fetch, assuming output exists at', outputFilename)
    if not outputFilename.exists():
        print('Expected data file not found:', outputFilename)
        sys.exit(4)

    bindef_path_cfg = conf.get('bindef_path')
    # Prepare output directory: use output_folder/identifier subfolder if provided, else <data_folder>/output
    _out_folder_cfg = conf.get('output_folder', '').strip()
    _identifier_cfg = conf.get('identifier', '').strip()
    if _out_folder_cfg and _identifier_cfg:
        _safe_id_cfg = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in _identifier_cfg)
        data_output_dir = Path(_out_folder_cfg) / _safe_id_cfg
    elif _out_folder_cfg:
        data_output_dir = Path(_out_folder_cfg)
    else:
        data_output_dir = outputFilename.parent
    try:
        data_output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as _mkdir_err:
        # WinError 183 = ERROR_ALREADY_EXISTS: Samba/SMB client returns this when the
        # directory already exists but the client-side stat cache is stale (is_dir()
        # also returns False in this state).  The directory IS there — proceed.
        if getattr(_mkdir_err, 'winerror', None) != 183:
            raise
    # Grant explicit full-control ACL so the directory remains accessible after
    # this subprocess exits (Samba can map Windows ACLs to Unix perms that deny
    # access when re-opened via a different process/connection).
    try:
        import subprocess as _icacls_sp
        _user = os.environ.get('USERNAME', 'Everyone')
        _icacls_sp.run(
            ['icacls', str(data_output_dir), '/grant', f'{_user}:(OI)(CI)F', '/Q'],
            capture_output=True, timeout=10)
    except Exception:
        pass
    # conf_path may not be defined when JSON is read from stdin; use BASE as conf_dir in that case
    try:
        conf_dir = conf_path.resolve().parent
    except NameError:
        conf_dir = BASE if BASE is not None else Path.cwd()
    if bindef_path_cfg:
        bindef_path = resolve_path(bindef_path_cfg)
        if not bindef_path.exists():
            # Try searching for the bindef file by basename under BASE/ROOT
            try:
                name = Path(bindef_path_cfg).name
                search_root = BASE if BASE is not None else ROOT
                matches = list(search_root.rglob(name))
                if matches:
                    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    bindef_path = matches[0]
                    print(f"Resolved bindef_path by search: {bindef_path} (original: {bindef_path_cfg})")
                else:
                    print('Provided bindef_path does not exist:', bindef_path)
                    sys.exit(6)
            except Exception:
                print('Provided bindef_path does not exist:', bindef_path)
                sys.exit(6)
        if bindef_path.suffix.lower() == '.csv':
            bindef_csv = bindef_path
        else:
            # Always name the parsed bindef CSV after the TestProgram (ignore bindef_out config)
            bindef_out_cfg = TestProgram + '_bindef.csv'
            p = Path(bindef_out_cfg)
            # Always place bindef CSV into the data file's output folder (use the basename)
            bindef_csv = (data_output_dir / p.name).resolve()
            try:
                bindef_csv.parent.mkdir(parents=True, exist_ok=True)
            except OSError as _e:
                if getattr(_e, 'winerror', None) != 183:
                    raise
            skip_parse = False
            if bindef_csv.exists():
                try:
                    txt = bindef_csv.read_text(errors='ignore')
                    if re.search(r'(?m)^DB\d+', txt):
                        print('Existing bindef CSV contains DB entries; skipping parse step.')
                        skip_parse = True
                except Exception:
                    skip_parse = False
            if not skip_parse:
                rc = run_parse_bindef(bindef_path, bindef_csv)
                if rc != 0:
                    print('parse_bindef_to_crystalball failed with', rc)
                    sys.exit(rc)
    else:
        # Try multiple likely BinDefinitions locations under the provided TestProgram folder.
        # Also try the base TP name with the operation-id suffix stripped
        # (e.g. TestProgram='NCXSDJXL0H61C002620_119325' → base='NCXSDJXL0H61C002620').
        _tp_base = TestProgram.rsplit('_', 1)[0] if '_' in TestProgram else ''
        candidates = [
            (testprogram_folder / TestProgram / 'BinDefinitions.bdefs'),
            (testprogram_folder / 'BinDefinitions.bdefs'),
            (testprogram_folder / TestProgram / 'BinDefinitions.BDEFS'),
            (testprogram_folder / 'BinDefinitions.BDEFS')
        ]
        if _tp_base and _tp_base != TestProgram:
            candidates = [
                (testprogram_folder / TestProgram / 'BinDefinitions.bdefs'),
                (testprogram_folder / _tp_base / 'BinDefinitions.bdefs'),
                (testprogram_folder / 'BinDefinitions.bdefs'),
                (testprogram_folder / TestProgram / 'BinDefinitions.BDEFS'),
                (testprogram_folder / _tp_base / 'BinDefinitions.BDEFS'),
                (testprogram_folder / 'BinDefinitions.BDEFS'),
            ]
        bindef_path = None
        for c in candidates:
            if c.exists():
                bindef_path = c
                break
        if bindef_path is None:
            # Fallback: one-level glob under testprogram_folder using a name prefix wildcard.
            # Use the base TP name (without op-id suffix) as prefix to avoid matching
            # unrelated programs (e.g. NCXSDJXL0H61D when looking for NCXSDJXL0H61C).
            _prefix = (_tp_base if _tp_base else TestProgram) if len(TestProgram) >= 7 else TestProgram
            print(f'Exact candidates not found; trying glob {_prefix}*/BinDefinitions.bdefs ...')
            for _pat in [f'{_prefix}*/BinDefinitions.bdefs', f'{_prefix}*/BinDefinitions.BDEFS']:
                _hits = sorted(testprogram_folder.glob(_pat),
                               key=lambda p: p.stat().st_mtime, reverse=True)
                if _hits:
                    bindef_path = _hits[0]
                    print(f'Found bindef by prefix glob: {bindef_path}')
                    break
        if bindef_path is None:
            print('Bindef not found in any expected locations; tried:', candidates)
            sys.exit(6)
        # Always name the parsed bindef CSV after the TestProgram (ignore bindef_out config)
        bindef_out_cfg = TestProgram + '_bindef.csv'
        p = Path(bindef_out_cfg)
        # Place bindef CSV into the data output folder by default (use the basename)
        bindef_csv = (data_output_dir / p.name).resolve()
        try:
            bindef_csv.parent.mkdir(parents=True, exist_ok=True)
        except OSError as _e:
            if getattr(_e, 'winerror', None) != 183:
                raise
        skip_parse = False
        if bindef_csv.exists():
            try:
                txt = bindef_csv.read_text(errors='ignore')
                if re.search(r'(?m)^DB\\d+', txt):
                    print('Existing bindef CSV contains DB entries; skipping parse step.')
                    skip_parse = True
            except Exception:
                skip_parse = False
        if not skip_parse:
            rc = run_parse_bindef(bindef_path, bindef_csv)
            if rc != 0:
                print('parse_bindef_to_crystalball failed with', rc)
                sys.exit(rc)

    # ── Mixed-program bindef merge ────────────────────────────────────────────
    # When the merged CSV contains rows from multiple test programs (e.g. mixed
    # L0 + R0 stepping), merge all programs' bindefs so that get_dd_update
    # recognises all bin codes.
    # Priority:
    #   1. extra_bindef_paths in JSON config (user-specified .bdefs or _bindef.csv files)
    #   2. Auto-detect extra programs from the CSV and find their bindefs
    _dd_input_csv = outputFilename
    _dd_7z_tmp = None
    # If outputFilename is a .7z archive, extract it once for both the
    # mixed-program merge (pandas read_csv) and get_dd_update below.
    if str(outputFilename).lower().endswith('.7z') and outputFilename.exists():
        import tempfile as _dd_tmp_mod, subprocess as _dd_sp_mod
        _7z_exe_dd = r'C:\Program Files\7-Zip\7z.exe'
        _dd_7z_tmp = _dd_tmp_mod.mkdtemp(prefix='yield_dd_7z_')
        _dd_sp_mod.run([_7z_exe_dd, 'e', str(outputFilename), f'-o{_dd_7z_tmp}', '-y'],
                       capture_output=True, timeout=600)
        _dd_csvs = sorted(Path(_dd_7z_tmp).glob('*.csv'))
        if _dd_csvs:
            _dd_input_csv = _dd_csvs[0]
    try:
        import pandas as _pd_bp

        # ── Collect extra bindef CSV paths ──────────────────────────────────
        _extra_bindef_csvs = []

        # 1. User-specified extra bindefs
        _user_extra = conf.get('extra_bindef_paths', [])
        if isinstance(_user_extra, str):
            _user_extra = [_user_extra]
        for _ubp in _user_extra:
            _ubp_path = resolve_path(_ubp)
            if _ubp_path.suffix.lower() == '.csv' and _ubp_path.exists():
                _extra_bindef_csvs.append(_ubp_path)
                print(f'[bindef] User extra bindef (CSV): {_ubp_path}')
            elif _ubp_path.exists():
                # It's a .bdefs file — parse it
                _ubp_csv = data_output_dir / (_ubp_path.stem + '_bindef.csv')
                rc2 = run_parse_bindef(_ubp_path, _ubp_csv)
                if rc2 == 0 and _ubp_csv.exists():
                    _extra_bindef_csvs.append(_ubp_csv)
                    print(f'[bindef] User extra bindef (parsed): {_ubp_csv}')
            else:
                print(f'[bindef] User extra bindef not found, skipping: {_ubp}')

        # 2. Auto-detect extra programs when no user-specified extras
        if not _user_extra:
            _prog_col_filter = lambda c: ('Program Name' in c or 'PROGRAM' in c.upper()) and 'TPI_BIN' not in c
            _prog_df = _pd_bp.read_csv(str(_dd_input_csv), usecols=_prog_col_filter, low_memory=False)
            _prog_cols = list(_prog_df.columns)
            _extra_progs = set()
            for _pc in _prog_cols:
                for _val in _prog_df[_pc].dropna().unique():
                    _pname = str(_val).strip()
                    if _pname and _pname != TestProgram:
                        _extra_progs.add(_pname)
            for _ep in sorted(_extra_progs):
                _ep_bindef_csv = data_output_dir / f'{_ep}_bindef.csv'
                if not _ep_bindef_csv.exists():
                    _ep_bindef_path = None
                    for _ec in [testprogram_folder / _ep / 'BinDefinitions.bdefs',
                                 testprogram_folder / _ep / 'BinDefinitions.BDEFS']:
                        if _ec.exists():
                            _ep_bindef_path = _ec
                            break
                    if _ep_bindef_path is None:
                        _ep_prefix = _ep[:7] if len(_ep) >= 7 else _ep
                        for _pat in [f'{_ep_prefix}*/BinDefinitions.bdefs',
                                     f'{_ep_prefix}*/BinDefinitions.BDEFS']:
                            _hits = sorted(testprogram_folder.glob(_pat),
                                           key=lambda p: p.stat().st_mtime, reverse=True)
                            if _hits:
                                _ep_bindef_path = _hits[0]
                                break
                    if _ep_bindef_path:
                        rc2 = run_parse_bindef(_ep_bindef_path, _ep_bindef_csv)
                        if rc2 == 0 and _ep_bindef_csv.exists():
                            _extra_bindef_csvs.append(_ep_bindef_csv)
                            print(f'[bindef] Auto-detected {_ep}: parsed -> {_ep_bindef_csv}')
                        else:
                            print(f'[bindef] Auto-detected {_ep}: parse failed, skipping')
                    else:
                        print(f'[bindef] Auto-detected {_ep}: BinDefinitions.bdefs not found, skipping')
                else:
                    _extra_bindef_csvs.append(_ep_bindef_csv)
                    print(f'[bindef] Auto-detected {_ep}: using existing {_ep_bindef_csv}')

        # ── Merge all bindef CSVs into one 2-column file ────────────────────
        if _extra_bindef_csvs:
            _merged_bindef_csv = data_output_dir / f'{TestProgram}_bindef_merged.csv'
            _primary_bd = _pd_bp.read_csv(str(bindef_csv))
            _desc_col = _primary_bd.columns[1]
            _bd_frames = [_primary_bd]
            for _ebc in _extra_bindef_csvs:
                _edf = _pd_bp.read_csv(str(_ebc))
                if len(_edf.columns) >= 2 and _edf.columns[1] != _desc_col:
                    _edf = _edf.rename(columns={_edf.columns[1]: _desc_col})
                _bd_frames.append(_edf)
            _merged_bd = _pd_bp.concat(_bd_frames, ignore_index=True)
            _merged_bd = _merged_bd.drop_duplicates(subset=[_merged_bd.columns[0]], keep='first')
            _merged_bd.to_csv(str(_merged_bindef_csv), index=False)
            bindef_csv = _merged_bindef_csv
            print(f'[bindef] Merged bindef: {len(_merged_bd)} entries -> {_merged_bindef_csv}')
    except Exception as _bp_ex:
        print(f'[bindef] Mixed-program bindef merge skipped: {_bp_ex}')

    # Use the dashboard path exactly as configured by the user (empty string = skip append)
    dashboard = resolve_path(conf_dashboard) if conf_dashboard else Path('')

    rc = run_get_dd(_dd_input_csv, bindef_csv, dashboard, out_dir=data_output_dir)
    if _dd_7z_tmp:
        import shutil as _dd_sh
        _dd_sh.rmtree(_dd_7z_tmp, ignore_errors=True)
    if rc != 0:
        print('get_dd_update failed with', rc)
        sys.exit(rc)

    print('Pipeline completed successfully.')


if __name__ == '__main__':
    main()
