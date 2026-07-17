"""parametric_runner.py — Parametric dashboard pipeline entry point.

Usage (called by _pipeline_runner.py via subprocess):
    python parametric_runner.py \\
        --sort-csv   PATH_TO_SORT_CSV \\
        --outdir     OUTPUT_FOLDER    \\
        --identifier TEST_PROGRAM_ID  \\
        [--full-site]                 \\
        [--product-setup PATH]        \\
        [--spec-csv PATH]             \\
        [--yield-html PATH]           \\
        [--upm-html PATH]             \\
        [--sicc-html PATH]            \\
        [--cdyn-html PATH]

Steps:
  1. Read sort CSV  → extract unique lot IDs (SORT_LOT > lot > any col with 'lot').
  2. Find matching PCM CSVs in shared/etest/9-sites/ and full-sites/ (full-sites
     takes priority when --full-site is passed).
  3. Load & merge PCM data + material + spec.
  4. Generate pcm_analysis.html via etest-dashboard's generate_pcm_html.
  5. Generate ParametricDashboard.html via parametric_html.generate_parametric_html.
  6. Print the output paths (one per line).

Exit code 0 = success, non-zero = error.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile as _zipfile_mod
from pathlib import Path

_ZIP_SEP = "::"  # must match etest-dashboard/_constants.py


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_repo_root(start: str) -> str:
    """Walk up until we find a directory that contains 'shared/'."""
    d = start
    for _ in range(10):
        if os.path.isdir(os.path.join(d, "shared")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # fallback: 4 levels up from sort-parametric/
    return os.path.abspath(os.path.join(start, "..", "..", "..", ".."))


def _walk_for_pcm(root: str):
    """Yield (filename, path) for every -PCM.csv under root.

    For plain files: path = absolute filesystem path.
    For zip entries: path = 'archive.zip::member_path' (same convention as
    etest-dashboard/_constants.py _walk_dir_and_zips / _read_csv).
    """
    if not os.path.isdir(root):
        return
    for dirpath, _dirs, files in os.walk(root):
        for fname in sorted(files):
            full = os.path.join(dirpath, fname)
            if fname.lower().endswith(".zip"):
                try:
                    with _zipfile_mod.ZipFile(full, "r") as zf:
                        for member in zf.namelist():
                            if member.endswith("/"):
                                continue
                            mfname = member.rsplit("/", 1)[-1]
                            if mfname.endswith("-PCM.csv"):
                                yield mfname, full + _ZIP_SEP + member
                except Exception:
                    pass
            elif fname.endswith("-PCM.csv"):
                yield fname, full


def _lot_from_filename(fname: str) -> str | None:
    """Extract lot ID from PCM CSV filename.  E.g. '8PF6CV-R-Q601S0H0-PCM.csv' -> 'Q601S0H0'."""
    m = re.match(r"^.+-.+-(.+)-PCM\.csv$", fname)
    return m.group(1) if m else None


def _find_pcm_for_lots(
    lots: list,
    nine_site_dir: str,
    full_site_dir: str | None,
    prefer_full: bool = False,
) -> dict:
    """Return {lot_id: csv_path} for lots that have a matching PCM CSV.

    Matching strategy (in order):
      1. Exact 8-char lot match  (sort lot == PCM lot)
      2. 7-char prefix match     (sort lot[:7] == PCM lot[:7])
         Handles the common case where the sort CSV has a 7-char INTEL_LOT7
         (e.g. Q603S6T) while the PCM filename uses an 8-char lot (Q603S6T0).
         The returned key is always the sort lot ID so downstream joins work.

    Both 9-sites and full-sites are always searched.  When prefer_full=True
    (--full-site flag), full-sites is searched first so it takes priority over
    9-sites when both contain a match for the same lot.
    """
    dirs = []
    full_exists = full_site_dir and os.path.isdir(full_site_dir)
    if prefer_full and full_exists:
        dirs = [full_site_dir, nine_site_dir]
    else:
        dirs = [nine_site_dir]
        if full_exists:
            dirs.append(full_site_dir)

    lot_set = set(lots)
    # Build a 7-char prefix lookup for fallback matching
    lot7_map: dict = {}   # prefix7 -> sort_lot (first match wins)
    for lot in lots:
        p7 = lot[:7]
        if p7 not in lot7_map:
            lot7_map[p7] = lot

    found: dict = {}
    for d in dirs:
        for fname, fpath in _walk_for_pcm(d):
            pcm_lot = _lot_from_filename(fname)
            if not pcm_lot:
                continue
            # 1. Exact match
            if pcm_lot in lot_set and pcm_lot not in found:
                found[pcm_lot] = fpath
                continue
            # 2. 7-char prefix match
            sort_lot = lot7_map.get(pcm_lot[:7])
            if sort_lot and sort_lot not in found:
                found[sort_lot] = fpath
                print(f"[parametric] PCM lot prefix match: "
                      f"sort={sort_lot!r} -> PCM file lot={pcm_lot!r}")
    return found


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    sort_csv  = args.sort_csv
    outdir    = args.outdir
    merged_csv = getattr(args, 'merged_csv', '') or ''
    if not sort_csv and not merged_csv:
        print("[parametric] ERROR: --sort-csv is required (or supply --config with DataCSV).",
              file=sys.stderr)
        return 1
    if not outdir:
        print("[parametric] ERROR: --outdir is required (or supply --config with output_folder).",
              file=sys.stderr)
        return 1
    identifier = args.identifier or Path(sort_csv or merged_csv).stem
    use_full  = args.full_site

    # ── Resolve deploy_dir: the subfolder that owns _material_merged.csv ───
    # deploy_dir is where pcm_analysis.html is written and where the
    # _material_merged.csv will receive merged PCM etest columns.
    # Priority: --deploy-dir arg > auto-detect subdir > outdir
    deploy_dir: str = getattr(args, 'deploy_dir', '') or ''
    if not deploy_dir:
        for _sd in sorted(Path(outdir).iterdir()) if Path(outdir).is_dir() else []:
            if _sd.is_dir() and (
                list(_sd.glob('*_material_merged.csv'))
                or (_sd / 'index.html').exists()
            ):
                deploy_dir = str(_sd)
                break
    if not deploy_dir:
        deploy_dir = outdir
    try:
        os.makedirs(deploy_dir, exist_ok=True)
    except OSError as _e:
        if getattr(_e, 'winerror', None) != 183:
            raise

    print(f"[parametric] Sort CSV : {sort_csv}")
    print(f"[parametric] Output   : {outdir}")
    print(f"[parametric] Deploy   : {deploy_dir}")
    print(f"[parametric] Identifier: {identifier}")
    print(f"[parametric] PCM mode : {'full-site priority' if use_full else '9-site priority'} (both dirs searched)")

    # ── 1. Extract lot IDs ────────────────────────────────────────────────────
    # When --merged-csv is provided (yield pipeline already ran), extract lots
    # from it directly.  Otherwise fall back to the sort CSV.
    try:
        import pandas as pd
    except ImportError:
        print("[parametric] ERROR: pandas is required.  pip install pandas", file=sys.stderr)
        return 1

    _lot_src = merged_csv if (merged_csv and os.path.isfile(merged_csv)) else sort_csv
    try:
        # Peek at the header only to find the lot column name, then read
        # just that column across ALL rows (no nrows cap) so lots from
        # extra-data CSVs appended after the primary block aren't missed.
        _hdr = pd.read_csv(_lot_src, nrows=0, low_memory=False)
        _cols_lower = {c: c.strip().lower() for c in _hdr.columns}
        lot_col = (
            next((c for c, cl in _cols_lower.items() if cl == "sort_lot"), None)
            or next((c for c, cl in _cols_lower.items() if cl == "lot"), None)
            or next((c for c, cl in _cols_lower.items()
                     if "lot" in cl and "slot" not in cl), None)
        )
        if lot_col:
            _lot_series = pd.read_csv(_lot_src, usecols=[lot_col],
                                      low_memory=False)[lot_col]
        else:
            _lot_series = pd.Series(dtype=str)
    except Exception as exc:
        print(f"[parametric] ERROR reading CSV: {exc}", file=sys.stderr)
        return 1

    # Priority: 1) SORT_LOT  2) exact 'lot' (case-insensitive)  3) any col with 'lot' not 'slot'
    if not lot_col:
        print("[parametric] WARNING: No 'Lot' column found in CSV — "
              "PCM lot matching will be empty.", file=sys.stderr)
        lots: list = []
    else:
        lots = sorted(_lot_series.dropna().unique().tolist())
        lots = [str(l).strip() for l in lots if str(l).strip()]
    print(f"[parametric] Lot col  : {lot_col!r}")
    print(f"[parametric] Lots found in {'merged' if merged_csv else 'sort'} CSV: {lots}")

    # ── 2. Find PCM CSVs ────────────────────────────────────────────────────
    _here = os.path.dirname(os.path.abspath(__file__))
    repo_root = _find_repo_root(_here)
    nine_site_dir = os.path.join(repo_root, "shared", "etest", "9-sites")
    full_site_dir = os.path.join(repo_root, "shared", "etest", "full-sites")
    material_dir  = os.path.join(repo_root, "shared", "material")

    lot_csv_map = _find_pcm_for_lots(lots, nine_site_dir,
                                      full_site_dir, prefer_full=use_full)
    print(f"[parametric] PCM CSVs matched: {len(lot_csv_map)}/{len(lots)} lots")
    for lot, path in sorted(lot_csv_map.items()):
        print(f"  {lot} -> {path}")

    # ── 3. Load etest-dashboard helpers (optional — PCM skipped if absent) ──
    # etest-dashboard is an independent repo/package. Discovery order:
    #   1. ETEST_DASHBOARD_SRC env var (explicit install path)
    #   2. Standard sibling install: <scripts-root>/etest-dashboard/src
    #   3. Sibling of app.dashboard.nvl: repo_root/../etest-dashboard/src
    #   4. Legacy / co-deployed layouts
    _par_dir = os.path.dirname(os.path.abspath(__file__))
    _run_dir  = os.path.normpath(os.path.join(_par_dir, '..'))
    _run_parent = os.path.normpath(os.path.join(_run_dir, '..'))  # run/ in unified deploy
    _etest_candidates = [
        os.environ.get('ETEST_DASHBOARD_SRC', ''),                       # explicit override
        os.path.join(_run_parent, '..', 'etest-dashboard', 'src'),       # C:\scripts\etest-dashboard\src
        os.path.join(repo_root,   '..', 'etest-dashboard', 'src'),       # sibling of app.dashboard.nvl
        os.path.join(_run_dir,    'etest-dashboard', 'src'),             # co-deployed
        os.path.join(_run_parent, 'etest-dashboard', 'src'),             # co-deployed (parent)
    ]
    for _ec in _etest_candidates:
        _ec = os.path.normpath(_ec)
        if os.path.isdir(_ec) and _ec not in sys.path:
            sys.path.insert(0, _ec)
    # Also add every */src sibling of _run_dir and _run_parent (mirrors _loader.py glob scan)
    # Use append (not insert) so the explicitly-targeted etest-dashboard/src above
    # stays at the front of sys.path and wins over sibling src dirs (e.g. class-dashboard/src).
    import glob as _glob_sr
    for _scan_root in (_run_dir, _run_parent):
        for _sd in _glob_sr.glob(os.path.join(_scan_root, '*/src')):
            if os.path.isdir(_sd) and _sd not in sys.path:
                sys.path.append(_sd)

    # ── 4. Load & merge PCM data ────────────────────────────────────────────
    pcm_df = None
    if lot_csv_map:
        try:
            from pcm_dashboard_frame import _load_and_merge
            pcm_df = _load_and_merge(lot_csv_map, print)
            print(f"[parametric] PCM rows loaded: {len(pcm_df):,}")
        except Exception as exc:
            print(f"[parametric] WARNING: PCM load failed: {exc}", file=sys.stderr)

    # ── 5. Load product setup & spec ────────────────────────────────────────
    product_setup = None
    if args.product_setup and os.path.isfile(args.product_setup):
        try:
            import json
            with open(args.product_setup, encoding="utf-8") as f:
                product_setup = json.load(f)
            print(f"[parametric] Product setup: {args.product_setup}")
        except Exception as exc:
            print(f"[parametric] WARNING: product setup load failed: {exc}", file=sys.stderr)
    else:
        # try shared default (canonical path first, then legacy fallbacks)
        default_setup = next(
            (p for p in [
                os.path.join(repo_root, "shared", "setup", "config", "etest-dashboard", "pcm_product_setup.json"),
                os.path.join(repo_root, "shared", "setup", "etest-dashboard", "pcm_product_setup.json"),
                os.path.join(repo_root, "shared", "etest", "collateral", "pcm_product_setup.json"),
            ] if os.path.isfile(p)),
            None,
        )
        if default_setup:
            try:
                import json
                with open(default_setup, encoding="utf-8") as f:
                    product_setup = json.load(f)
                print(f"[parametric] Product setup (default): {default_setup}")
            except Exception:
                pass

    spec_lookup = None
    if args.spec_csv and os.path.isfile(args.spec_csv):
        try:
            from pcm_dashboard_frame import _load_spec_lookup
            spec_lookup = _load_spec_lookup(args.spec_csv)
            print(f"[parametric] Spec CSV: {args.spec_csv}")
        except Exception as exc:
            print(f"[parametric] WARNING: spec load failed: {exc}", file=sys.stderr)
    else:
        _spec_candidates = [
            os.path.join(repo_root, "shared", "spec", "wat",
                         "N2P_NVL816_WAT_PDK1.0_target.csv"),
            os.path.join(repo_root, "shared", "etest", "spec",
                         "N2P_NVL816_WAT_PDK1.0_target.csv"),
        ]
        default_spec = next((p for p in _spec_candidates if os.path.isfile(p)), None)
        if default_spec:
            try:
                from pcm_dashboard_frame import _load_spec_lookup
                spec_lookup = _load_spec_lookup(default_spec)
                print(f"[parametric] Spec CSV (default): {default_spec}")
            except Exception:
                pass

    # ── 4.6. IDW expansion: 9 sites → all reticle positions ────────────────
    # Always run IDW when PCM data is available.
    # When --merged-csv is provided (yield pipeline's reticle/material CSV),
    # the IDW-expanded PCM columns are merged INTO that file after IDW completes
    # so the final zipped resolved CSV contains sort + reticle + material + PCM.
    # pcm_idw_*.csv files are deleted after merging (intermediate only).
    if lot_csv_map and pcm_df is not None:
        try:
            from pcm_merge_gui import (       # type: ignore
                run_pipeline as _run_idw,
                _guess_reticle_map,
                _infer_devrevstep_lot,
                _normalise_sort_cols,
            )
            # Detect DevRevStep from sort CSV to guess the reticle map
            _df_hdr = pd.read_csv(sort_csv, nrows=100, low_memory=False)
            _df_hdr = _normalise_sort_cols(_df_hdr)
            _drs, _ = _infer_devrevstep_lot(_df_hdr)
            _rmap = _guess_reticle_map(_drs or "8PF5CV")
            if _rmap and os.path.isfile(_rmap):
                print(f"[parametric] Reticle map: {os.path.basename(_rmap)}")
                _idw_frames: list = []
                try:
                    os.makedirs(deploy_dir, exist_ok=True)
                except OSError as _e:
                    if getattr(_e, 'winerror', None) != 183:
                        raise
                # Pre-load sort CSV once — avoids re-reading it for every lot
                from pcm_merge_gui import _normalise_sort_cols as _nsc  # type: ignore
                print(f"[parametric] Pre-loading sort CSV for IDW cache...")
                _sort_df_cache = _nsc(pd.read_csv(sort_csv, low_memory=False))
                # Pass the pcm_filter to IDW so only selected params are computed
                _idw_pcm_filter = getattr(args, 'pcm_filter', '') or ''
                if _idw_pcm_filter:
                    print(f"[parametric] IDW pcm_filter: '{_idw_pcm_filter}'")
                for _lot, _pcm_csv in lot_csv_map.items():
                    _idw_path = os.path.join(deploy_dir, f"pcm_idw_{_lot}.csv")
                    _idf_from_run = None
                    if not os.path.isfile(_idw_path):
                        print(f"[parametric] Running IDW for lot {_lot} ...")
                        try:
                            _idf_from_run, _ = _run_idw(
                                input_csv=sort_csv,
                                etest_csv=_pcm_csv,
                                reticle_map_csv=_rmap,
                                output_csv=_idw_path,
                                log=print,
                                df_yield_cache=_sort_df_cache,
                                write_csv=False,
                                pcm_filter=_idw_pcm_filter,
                            )
                            print(f"[parametric] IDW done -> {_idw_path}")
                        except Exception as _ie:
                            print(f"[parametric] WARNING: IDW failed for {_lot}: {_ie}",
                                  file=sys.stderr)
                    if _idf_from_run is not None or os.path.isfile(_idw_path):
                        try:
                            _idf = (_idf_from_run if _idf_from_run is not None
                                    else pd.read_csv(_idw_path, low_memory=False, encoding='utf-8'))
                            # Normalise column names to what generate_pcm_html expects
                            _col_remap_idw = {
                                "SORT_LOT": "Lot", "SORT_WAFER": "Wafer",
                                "SORT_X": "LayoutX", "SORT_Y": "LayoutY",
                            }
                            # If SORT_LOT (canonical 8-char sort lot) and a plain 'Lot'
                            # column (7-char Intel lot) both exist, drop the plain 'Lot'
                            # so the SORT_LOT→Lot rename is unambiguous.
                            if "SORT_LOT" in _idf.columns and "Lot" in _idf.columns:
                                _idf = _idf.drop(columns=["Lot"])
                            _idf.rename(columns={k: v for k, v in _col_remap_idw.items()
                                                 if k in _idf.columns}, inplace=True)
                            # Deduplicate any remaining duplicate columns; keep='first'
                            # preserves the SORT_LOT-derived Lot which was just renamed.
                            _idf = _idf.loc[:, ~_idf.columns.duplicated(keep='first')]
                            # Keep only this lot's rows.  The IDW runs on the full sort CSV
                            # (all lots) so the output has ALL yield rows.  Each IDW result
                            # must contribute only the rows that belong to _lot.
                            if "Lot" not in _idf.columns:
                                raise ValueError(
                                    f"IDW output for lot {_lot!r} has no 'Lot' column")
                            _idf = _idf[_idf["Lot"] == _lot].copy()
                            if len(_idf) == 0:
                                raise ValueError(
                                    f"Lot {_lot!r} has no rows in IDW output {_idw_path!r}")
                            # Always force every row to the canonical lot key.
                            # The IDW CSV may contain rows where SORT_LOT was the
                            # 7-char INTEL_LOT7 value (the 9 PCM measurement sites)
                            # rather than the 8-char SORT_LOT from the sort CSV.
                            # Overriding ensures a single, consistent lot ID.
                            _idf["Lot"] = _lot
                            # Populate Program from "Program Name*" column if present.
                            if "Program" not in _idf.columns:
                                _prog_col = next(
                                    (c for c in _idf.columns if c.startswith("Program Name")),
                                    None,
                                )
                                if _prog_col:
                                    _idf = _idf.copy()
                                    _idf["Program"] = _idf[_prog_col]
                            # Populate Layout from DevRevStep if present.
                            if "Layout" not in _idf.columns and "DevRevStep" in _idf.columns:
                                _idf = _idf.copy()
                                _idf["Layout"] = _idf["DevRevStep"]
                            if "Wafer" not in _idf.columns and "Lot" in _idf.columns:
                                pass  # can't infer wafer without column
                            else:
                                # Normalise Wafer to 2-digit numeric string (strip lot prefix).
                                # Use float() before int() to handle float64 column values
                                # like 503.0 which int(str(w)) would reject.
                                _idf["Wafer"] = _idf["Wafer"].apply(
                                    lambda w: (str(int(float(str(w))) % 100)
                                               if pd.notna(w) else str(w))
                                )
                            # Per-wafer material join — yield-dashboard approach:
                            # LOTFROMFS[:7] == INTEL_LOT7, SORT_WAFER%100 == WaferID
                            # Mirrors run_pipeline step 10 in pcm_merge_gui.py.
                            if "Material" not in _idf.columns:
                                try:
                                    from pcm_dashboard_frame import _find_material_csv  # type: ignore
                                    _drs_val = (str(_idf["DevRevStep"].iloc[0])
                                                if "DevRevStep" in _idf.columns and len(_idf) > 0
                                                else "")
                                    _prefix6 = _drs_val[:6] if _drs_val else ""
                                    _lot7_val = _lot[:7]
                                    _mat_csv_p = _find_material_csv(_prefix6, _lot7_val)
                                    if _mat_csv_p:
                                        _df_mat = pd.read_csv(_mat_csv_p, low_memory=False)
                                        _df_mat.columns = [c.strip() for c in _df_mat.columns]
                                        _mat_col = "Material Type, Skew, BEOL Skew"
                                        if "INTEL_LOT7" in _df_mat.columns and "WaferID" in _df_mat.columns and _mat_col in _df_mat.columns:
                                            _df_mat["_m_lot7"]  = _df_mat["INTEL_LOT7"].astype(str).str.strip()
                                            _df_mat["_m_wafer"] = pd.to_numeric(_df_mat["WaferID"], errors="coerce")
                                            # Wafer in _idf is already normalised (% 100 str)
                                            _idf = _idf.copy()
                                            _idf["_m_lot7"]  = (
                                                _idf["LOTFROMFS"].astype(str).str[:7]
                                                if "LOTFROMFS" in _idf.columns
                                                else _lot7_val
                                            )
                                            _idf["_m_wafer"] = pd.to_numeric(
                                                _idf["Wafer"], errors="coerce")
                                            _mat_dedup = (
                                                _df_mat[["_m_lot7", "_m_wafer", _mat_col]]
                                                .drop_duplicates(subset=["_m_lot7", "_m_wafer"])
                                            )
                                            _idf = _idf.merge(
                                                _mat_dedup, on=["_m_lot7", "_m_wafer"], how="left"
                                            )
                                            _idf.rename(columns={_mat_col: "Material"}, inplace=True)
                                            _idf.drop(columns=["_m_lot7", "_m_wafer"],
                                                      inplace=True, errors="ignore")
                                            _n_mat = _idf["Material"].notna().sum()
                                            print(f"[parametric] Material per-wafer join: "
                                                  f"{_n_mat:,}/{len(_idf):,} rows matched "
                                                  f"({_mat_csv_p})")
                                except Exception as _matjoin_ex:
                                    print(f"[parametric] WARNING: per-wafer material join "
                                          f"failed: {_matjoin_ex}", file=sys.stderr)
                            # Write-back only needed when loaded from disk (resume path).
                            if _idf_from_run is None:
                                try:
                                    _idf.to_csv(_idw_path, index=False, encoding='utf-8')
                                except Exception as _wb_ex:
                                    print(f"[parametric] WARNING: IDW write-back failed: {_wb_ex}",
                                          file=sys.stderr)
                            _idw_frames.append(_idf)
                            print(f"[parametric] IDW loaded: {_idw_path} "
                                  f"({len(_idf):,} rows, {len(_idf.columns)} cols)")
                        except Exception as _le:
                            print(f"[parametric] WARNING: IDW load failed for {_lot}: {_le}",
                                  file=sys.stderr)
                if _idw_frames:
                    pcm_df = pd.concat(_idw_frames, ignore_index=True, sort=False)
                    print(f"[parametric] pcm_df replaced with IDW data: "
                          f"{len(pcm_df):,} rows × {len(pcm_df.columns)} cols")
                    # IDW CSV has no Material column — re-attach it from the
                    # material CSV directory using the same logic as _load_and_merge.
                    if "Material" not in pcm_df.columns:
                        try:
                            from pcm_dashboard_frame import _get_lot_material
                            _mat_map = {
                                _lot: _get_lot_material(_lot, _pcm_csv)
                                for _lot, _pcm_csv in lot_csv_map.items()
                            }
                            pcm_df["Material"] = (
                                pcm_df["Lot"].astype(str).map(_mat_map).fillna("")
                            )
                            print(f"[parametric] Material attached to IDW data: "
                                  f"{pcm_df['Material'].astype(bool).sum():,} rows matched")
                        except Exception as _me:
                            print(f"[parametric] WARNING: Material attach failed: {_me}",
                                  file=sys.stderr)
                            pcm_df["Material"] = ""
            else:
                print(f"[parametric] No reticle map found — skipping IDW, using 9-site data.")
        except ImportError:
            print("[parametric] pcm_merge_gui not importable — skipping IDW step.",
                  file=sys.stderr)
        except Exception as _idw_exc:
            print(f"[parametric] WARNING: IDW step failed: {_idw_exc}", file=sys.stderr)

    # ── Merge IDW PCM columns into resolved CSV ────────────────────────────
    # When --merged-csv is provided, enrich it in-place with the IDW-expanded
    # PCM measurement columns so the final zipped file contains sort + reticle
    # + material + PCM in one file.  Join key: SORT_LOT, SORT_X, SORT_Y, and
    # SORT_WAFER normalised to wafer number (e.g. 503 -> "3").
    # Delete pcm_idw_*.csv files afterwards — they are intermediate only.
    if merged_csv and os.path.isfile(merged_csv) and pcm_df is not None and len(pcm_df) > 0:
        try:
            _pcm_etest_pfx = ('Con_', 'Vts_', 'Isat_', 'Ioff_', 'Cap_',
                              'Res_', 'Td_', 'Cmim_', 'Vbd_', 'Sub_')
            _mm_df = pd.read_csv(merged_csv, low_memory=False)
            _pcm_add_cols = [c for c in pcm_df.columns
                             if any(c.startswith(p) for p in _pcm_etest_pfx)
                             and c not in _mm_df.columns]
            # Also carry Material if the resolved CSV is missing it
            if 'Material' not in _mm_df.columns and 'Material' in pcm_df.columns:
                _pcm_add_cols.append('Material')
            if _pcm_add_cols and 'LayoutX' in pcm_df.columns and 'SORT_X' in _mm_df.columns:
                # Add temporary normalised keys to resolved CSV
                _mm_df = _mm_df.copy()
                _mm_df['_nw'] = _mm_df['SORT_WAFER'].apply(
                    lambda w: str(int(float(str(w))) % 100) if pd.notna(w) else '')
                _mm_df['_lot'] = _mm_df['SORT_LOT'].astype(str)
                # Build slim PCM frame with the same normalised keys
                _pcm_slim = pcm_df[['Lot', 'Wafer', 'LayoutX', 'LayoutY'] + _pcm_add_cols].copy()
                _pcm_slim = _pcm_slim.rename(columns={
                    'Lot': '_lot', 'Wafer': '_nw',
                    'LayoutX': 'SORT_X', 'LayoutY': 'SORT_Y',
                })
                _pcm_slim['_lot'] = _pcm_slim['_lot'].astype(str)
                _pcm_slim['_nw']  = _pcm_slim['_nw'].astype(str)
                _pcm_slim = _pcm_slim.drop_duplicates(subset=['_lot', '_nw', 'SORT_X', 'SORT_Y'])
                _mm_df = _mm_df.merge(
                    _pcm_slim, on=['_lot', '_nw', 'SORT_X', 'SORT_Y'], how='left')
                _mm_df = _mm_df.drop(columns=['_lot', '_nw'], errors='ignore')
                _mm_df.to_csv(merged_csv, index=False)
                _n_pcm = int(_mm_df[_pcm_add_cols[0]].notna().sum()) if _pcm_add_cols else 0
                print(f"[parametric] PCM IDW merged into {Path(merged_csv).name}: "
                      f"{len(_pcm_add_cols)} cols, {_n_pcm:,}/{len(_mm_df):,} rows matched")
            elif not _pcm_add_cols:
                print("[parametric] PCM columns already in merged CSV — no merge needed")
            else:
                print("[parametric] WARNING: LayoutX/SORT_X not available — "
                      "PCM IDW merge into merged CSV skipped", file=sys.stderr)
        except Exception as _mm_exc:
            print(f"[parametric] WARNING: PCM IDW merge into merged CSV failed: {_mm_exc}",
                  file=sys.stderr)
        # Delete pcm_idw_*.csv — now embedded in the resolved/merged CSV
        import glob as _idw_glob
        for _idw_del in _idw_glob.glob(os.path.join(deploy_dir, 'pcm_idw_*.csv')):
            try:
                os.remove(_idw_del)
                print(f"[parametric] Deleted intermediate IDW file: {Path(_idw_del).name}")
            except Exception:
                pass

    # ── 4.5. Build merged (sort+PCM) DataFrame for PCM-Program ────────────
    # pcm_idw_*.csv files are deleted above (when --merged-csv was provided),
    # or cleaned up here — they are intermediate only and never final outputs.
    # Pass --keep-pcm-idw (or keep_pcm_idw=true in input.json) to retain them.
    if not getattr(args, 'keep_pcm_idw', False):
        import glob as _idw_glob2
        for _idw_del2 in _idw_glob2.glob(os.path.join(deploy_dir, 'pcm_idw_*.csv')):
            try:
                os.remove(_idw_del2)
                print(f"[parametric] Deleted intermediate IDW file: {Path(_idw_del2).name}")
            except Exception:
                pass
    merged_df: "pd.DataFrame | None" = None
    _col_remap = {"SORT_LOT": "Lot", "SORT_WAFER": "Wafer",
                  "SORT_X": "LayoutX", "SORT_Y": "LayoutY"}

    idw_frames = []
    for _lot in lots:
        _idw_path = os.path.join(deploy_dir, f"pcm_idw_{_lot}.csv")
        if os.path.isfile(_idw_path):
            try:
                _idf = pd.read_csv(_idw_path, low_memory=False)
                if "SORT_LOT" in _idf.columns and "Lot" in _idf.columns:
                    _idf = _idf.drop(columns=["Lot"])
                _idf.rename(columns={k: v for k, v in _col_remap.items()
                                     if k in _idf.columns}, inplace=True)
                _idf = _idf.loc[:, ~_idf.columns.duplicated(keep='first')]
                idw_frames.append(_idf)
                print(f"[parametric] IDW CSV loaded: {_idw_path} ({len(_idf):,} rows)")
            except Exception as _exc:
                print(f"[parametric] WARNING: IDW CSV load failed: {_exc}", file=sys.stderr)
    if idw_frames:
        merged_df = pd.concat(idw_frames, ignore_index=True)
        print(f"[parametric] IDW merged total: {len(merged_df):,} rows")

    # Wafer-level fallback: join sort CSV medians with PCM medians
    if merged_df is None and pcm_df is not None and len(pcm_df) > 0 and sort_csv:
        try:
            df_full = pd.read_csv(sort_csv, low_memory=False)
            df_full.rename(columns={k: v for k, v in _col_remap.items()
                                    if k in df_full.columns}, inplace=True)
            # Deduplicate: SORT_LOT→Lot rename may create a second 'Lot' col.
            df_full = df_full.loc[:, ~df_full.columns.duplicated(keep='last')]
            sort_num = [c for c in df_full.select_dtypes(include="number").columns
                        if re.match(r'^(UPM_|SICC)', c)]
            grp_s = [c for c in ["Lot", "Wafer"] if c in df_full.columns]
            if grp_s and sort_num:
                sort_agg = (df_full[grp_s + sort_num]
                            .groupby(grp_s, as_index=False).median())
                pcm_num = [c for c in pcm_df.select_dtypes(include="number").columns
                           if re.match(r'^(Td_|Ioff_)', c, re.I)]
                grp_p = [c for c in ["Lot", "Wafer"] if c in pcm_df.columns]
                if grp_p and pcm_num:
                    pcm_agg = (pcm_df[grp_p + pcm_num]
                               .groupby(grp_p, as_index=False).median())
                    # Coerce merge keys to str on both sides to avoid int64/str mismatch
                    for _k in grp_s:
                        if _k in sort_agg.columns:
                            sort_agg[_k] = sort_agg[_k].astype(str)
                        if _k in pcm_agg.columns:
                            pcm_agg[_k] = pcm_agg[_k].astype(str)
                    merged_df = sort_agg.merge(pcm_agg, on=grp_s, how="inner")
                    print(f"[parametric] Wafer-level merged: {len(merged_df):,} rows "
                          f"({len(sort_num)} sort cols + {len(pcm_num)} PCM cols)")
        except Exception as _exc:
            print(f"[parametric] WARNING: wafer merge failed: {_exc}", file=sys.stderr)

    # ── 5.5. Enrich pcm_df with sort SICC / UPM / CDYN (via sicc_processor) ─
    # Uses the same sicc_processor.process_csv pipeline as the yield-dashboard
    # SICC/CDYN/UPM tab so that column matching, UPM % scaling, and
    # siccTotalList sum columns are all handled consistently.
    # Requires sort_csv (has the raw SICC/UPM/CDYN test columns).
    _sort_groups: "dict | None" = None
    if pcm_df is not None and len(pcm_df) > 0 and sort_csv and os.path.isfile(sort_csv):
        try:
            # Add sicc_cdyn_upm/src to path so sicc_processor is importable.
            # sort-parametric/ and sicc_cdyn_upm/ are siblings under yield-dashboard/
            _sicc_src = os.path.normpath(
                os.path.join(os.path.dirname(__file__), '..', 'sicc_cdyn_upm', 'src'))
            if _sicc_src not in sys.path:
                sys.path.insert(0, _sicc_src)
            from sicc_processor import process_csv as _proc_sicc       # type: ignore

            _pcfg_path = getattr(args, "product_config_json", "") or ""
            if not _pcfg_path:
                import glob as _gl_sicc
                _sicc_setup_dir = os.path.join(repo_root, "shared", "setup", "config", "yield-dashboard")
                _sicc_cands = sorted(_gl_sicc.glob(os.path.join(_sicc_setup_dir, "*Product Config*.json")))
                if not _sicc_cands:
                    _sicc_setup_dir = os.path.join(repo_root, "shared", "spec", "collateral", "yield")
                    _sicc_cands = sorted(_gl_sicc.glob(os.path.join(_sicc_setup_dir, "*Product Config*.json")))
                if _sicc_cands:
                    # Match by DevRevStep from merged DataFrame
                    _sicc_dv = ''
                    try:
                        _dv_cols_s = [c for c in pcm_df.columns if c.upper().startswith('DEVREVSTEP')]
                        if _dv_cols_s:
                            _vals_s = pcm_df[_dv_cols_s[0]].dropna()
                            if len(_vals_s):
                                _sicc_dv = str(_vals_s.iloc[0]).strip().upper()
                    except Exception:
                        pass
                    if _sicc_dv:
                        _dv6_s = _sicc_dv[:6]
                        def _sicc_cfg_matches(p):
                            _tok = os.path.basename(p).split(' - ')[0].strip().upper()
                            return _sicc_dv.startswith(_tok) or _tok.startswith(_dv6_s)
                        _pcfg_path = next((p for p in _sicc_cands if _sicc_cfg_matches(p)), None) or _sicc_cands[0]
                    else:
                        _pcfg_path = _sicc_cands[0]
                    print(f"[parametric] Auto-selected product config (SICC): {_pcfg_path}")
            if _pcfg_path and os.path.isfile(_pcfg_path):
                import json as _jcfg
                _pcfg_full = _jcfg.loads(open(_pcfg_path, encoding="utf-8").read())

                # Build a testlist-only config for sicc_processor (siccList,
                # siccTotalList, cdynList, upmInfo).  The product config JSON
                # stores sicc_targets / cdyn_targets as lists-of-dicts which
                # would crash sicc_processor's legacy .items() path — pass them
                # via override_targets / override_cdyn_targets instead.
                # Only pass siccList/siccTotalList/upmInfo/cdynList — these drive
                # which columns appear in PCM analysis.  SiccTableConfig and
                # cdynTableConfig are for the SICC dashboard scatter plots only.
                _sicc_cfg = {k: _pcfg_full[k]
                             for k in ("siccList", "siccTotalList", "cdynList", "upmInfo")
                             if k in _pcfg_full}

                _override_targets: dict = {}
                for _e in _pcfg_full.get("sicc_targets", []):
                    _t = str(_e.get("test", "")).strip()
                    _v = _e.get("target_A")
                    if _t and _v is not None:
                        try: _override_targets[_t.upper()] = float(_v)
                        except (ValueError, TypeError): pass

                _override_cdyn_targets: dict = {}
                for _e in _pcfg_full.get("cdyn_targets", []):
                    _t = str(_e.get("test", "")).strip()
                    _v = _e.get("target_nF")
                    if _t and _v is not None:
                        try: _override_cdyn_targets[_t] = float(_v)
                        except (ValueError, TypeError): pass

                _sicc_data = _proc_sicc(
                    sort_csv, _sicc_cfg,
                    override_targets=_override_targets or None,
                    override_cdyn_targets=_override_cdyn_targets or None,
                )

                _sicc_cols = _sicc_data.get("sicc_columns", [])
                _upm_cols  = _sicc_data.get("upm_dist_cols", [])
                _cdyn_cols = _sicc_data.get("cdyn_columns", [])
                _rows      = _sicc_data.get("rows", [])
                _sort_die_df = _sicc_data.get("df")  # die-level df with UPM/SICC/CDYN

                def _norm_wafer(w):
                    """Map sort wafer IDs (e.g. 501→1, 503→3) to PCM wafer numbers.
                    Handles float values like 503.0 (pandas reads int cols as float
                    when any NaN is present) via float() before int()."""
                    try:
                        return str(int(float(str(w))) % 100)
                    except (ValueError, TypeError):
                        return str(w)

                # ── Die-level join: match each PCM reticle die to the sort die
                # at the same (Lot, Wafer, LayoutX, LayoutY) so every die gets
                # its own UPM/SICC/CDYN value instead of a per-wafer median.
                _gcols = _sicc_data.get("group_cols", {})
                _lot_c  = _gcols.get("lot", "")
                _wfr_c  = _gcols.get("wafer", "")
                _x_c    = _gcols.get("x", "")
                _y_c    = _gcols.get("y", "")
                _prg_c  = _gcols.get("program", "")

                _sort_cols = [c for c in _sicc_cols + _upm_cols + _cdyn_cols
                              if _sort_die_df is not None and c in _sort_die_df.columns]

                # ── Normalise pcm_df key columns so joins work regardless of dtype
                for _k in ("Lot", "Wafer"):
                    if _k in pcm_df.columns:
                        pcm_df[_k] = pcm_df[_k].astype(str)
                for _k in ("LayoutX", "LayoutY"):
                    if _k in pcm_df.columns:
                        pcm_df[_k] = pd.to_numeric(pcm_df[_k], errors="coerce")

                if _sort_die_df is not None and _sort_cols and _lot_c and _wfr_c:
                    # Build a tidy die-level frame with Lot/Wafer/X/Y + SICC/UPM/CDYN.
                    # SICC/UPM/CDYN are ENRICHMENT ONLY — pcm_df rows are never dropped;
                    # dies with no matching sort row get NaN in those columns.
                    _keep_cols = [_lot_c, _wfr_c]
                    if _x_c and _x_c in _sort_die_df.columns:
                        _keep_cols.append(_x_c)
                    if _y_c and _y_c in _sort_die_df.columns:
                        _keep_cols.append(_y_c)
                    # Do NOT include the program column from sort — pcm_df already has
                    # its own Program column and adding sort's program would create
                    # Program_x / Program_y suffixes that break _compute_rows groupby.
                    _keep_cols += _sort_cols
                    _die_df = _sort_die_df[_keep_cols].copy()

                    _col_remap = {_lot_c: "Lot", _wfr_c: "Wafer"}
                    if _x_c and _x_c in _die_df.columns:
                        _col_remap[_x_c] = "LayoutX"
                    if _y_c and _y_c in _die_df.columns:
                        _col_remap[_y_c] = "LayoutY"
                    _die_df.rename(columns=_col_remap, inplace=True)

                    _die_df["Lot"]        = _die_df["Lot"].astype(str)
                    _die_df["sort_wafer"] = _die_df["Wafer"].astype(str)   # save 3-digit sort wafer before normalisation
                    _die_df["Wafer"]      = _die_df["Wafer"].apply(_norm_wafer).astype(str)
                    for _k in ("LayoutX", "LayoutY"):
                        if _k in _die_df.columns:
                            _die_df[_k] = pd.to_numeric(_die_df[_k], errors="coerce")

                    # Only join on X/Y if they have usable values in both frames.
                    _xy_ok = (
                        "LayoutX" in _die_df.columns and "LayoutX" in pcm_df.columns
                        and "LayoutY" in _die_df.columns and "LayoutY" in pcm_df.columns
                        and _die_df["LayoutX"].notna().any()
                        and pcm_df["LayoutX"].notna().any()
                    )
                    _join_keys = ["Lot", "Wafer"]
                    if _xy_ok:
                        _join_keys += ["LayoutX", "LayoutY"]

                    # Collapse multiple rows per die position -> mean of numeric cols
                    _die_num = [c for c in _sort_cols if c in _die_df.columns
                                and pd.api.types.is_numeric_dtype(_die_df[c])]
                    _die_agg: dict = {c: "mean" for c in _die_num}
                    _die_agg["sort_wafer"] = "first"  # non-numeric: take first value per group
                    _die_df = _die_df.groupby(_join_keys, as_index=False).agg(_die_agg)

                    print(f"[parametric] SICC enrichment: {len(_die_df)} sort rows -> "
                          f"left-join onto {len(pcm_df)} pcm rows "
                          f"(keys={_join_keys}); "
                          f"sort Lot={_die_df['Lot'].iloc[0] if len(_die_df) else 'n/a'}, "
                          f"pcm Lot={pcm_df['Lot'].iloc[0] if len(pcm_df) else 'n/a'}")

                    if len(_die_df) > 0:
                        # LEFT join: pcm_df is the left frame — never loses rows.
                        # Rows without a matching sort die get NaN for SICC/UPM/CDYN.
                        _pcm_before = len(pcm_df)
                        pcm_df = pcm_df.merge(_die_df, on=_join_keys, how="left")
                        print(f"[parametric] pcm_df after SICC left-join: "
                              f"{len(pcm_df)} rows (was {_pcm_before})")

                elif _rows:
                    # Fallback: no die-level df — use per-wafer medians from _rows.
                    # Still left-join so pcm_df never shrinks.
                    _sw_rows = []
                    for _r in _rows:
                        _entry = {"Lot":        str(_r.get("lot", "")),
                                  "sort_wafer": str(_r.get("wafer", "")),  # raw sort_wafer before normalisation
                                  "Wafer":      _norm_wafer(_r.get("wafer", ""))}
                        # Do NOT add Program — pcm_df already has its own Program column
                        _entry.update(_r.get("medians", {}))
                        _entry.update(_r.get("cdyn", {}))
                        _sw_rows.append(_entry)

                    _sort_wafer = pd.DataFrame(_sw_rows)
                    _grp_s = [c for c in ("Lot", "Wafer")
                              if c in pcm_df.columns and c in _sort_wafer.columns]
                    if _grp_s:
                        _sort_wafer["Lot"]   = _sort_wafer["Lot"].astype(str)
                        _sort_wafer["Wafer"] = _sort_wafer["Wafer"].astype(str)
                        _num_cols_sw = [c for c in _sort_wafer.columns
                                        if c not in _grp_s and c != "sort_wafer"
                                        and pd.api.types.is_numeric_dtype(_sort_wafer[c])]
                        _agg_map = {c: "mean" for c in _num_cols_sw}
                        _agg_map["sort_wafer"] = "first"  # non-numeric: take first per group
                        _sort_wafer = _sort_wafer.groupby(_grp_s, as_index=False).agg(_agg_map)
                        _pcm_before = len(pcm_df)
                        # LEFT join — pcm_df rows never eliminated
                        pcm_df = pcm_df.merge(_sort_wafer, on=_grp_s, how="left")
                        print(f"[parametric] pcm_df after fallback left-join: "
                              f"{len(pcm_df)} rows (was {_pcm_before})")

                # Build sort_groups dict consumed by generate_pcm_html
                # (applies regardless of which merge path was taken)
                # Merge sicc_processor resolved targets with product-config overrides
                # so that USL lines appear on every SICC / CDYN / UPM chart.
                _all_targets = dict(_sicc_data.get("targets", {}))        # SICC + UPM (upper-cased keys)
                _all_targets.update({k.upper(): v
                                     for k, v in _sicc_data.get("cdyn_targets", {}).items()})
                # Product-config overrides take precedence (already applied inside
                # sicc_processor but we need them by friendly name for inject below)
                for _e in _pcfg_full.get("sicc_targets", []):
                    _tn = str(_e.get("test", "")).strip()
                    _tv = _e.get("target_A")
                    if _tn and _tv is not None:
                        try: _all_targets[_tn.upper()] = float(_tv)
                        except (ValueError, TypeError): pass
                for _e in _pcfg_full.get("cdyn_targets", []):
                    _tn = str(_e.get("test", "")).strip()
                    _tv = _e.get("target_nF")
                    if _tn and _tv is not None:
                        try: _all_targets[_tn.upper()] = float(_tv)
                        except (ValueError, TypeError): pass
                # UPM targets come from upmInfo 4th element (already in sicc_processor targets)

                def _col_usl(col: str) -> "float | None":
                    """Return USL for a renamed column, or None."""
                    v = _all_targets.get(col.upper())
                    return float(v) if v is not None else None

                _friendly_groups: dict = {}
                if _sicc_cols:
                    _friendly_groups["SICC"] = {
                        "cols": [c for c in _sicc_cols if c in pcm_df.columns],
                        "labels": {c: c for c in _sicc_cols},
                        "targets": {c: _col_usl(c) for c in _sicc_cols if _col_usl(c) is not None},
                    }
                if _upm_cols:
                    _friendly_groups["UPM"] = {
                        "cols": [c for c in _upm_cols if c in pcm_df.columns],
                        "labels": {c: c for c in _upm_cols},
                        "targets": {c: _col_usl(c) for c in _upm_cols if _col_usl(c) is not None},
                    }
                if _cdyn_cols:
                    _friendly_groups["CDYN"] = {
                        "cols": [c for c in _cdyn_cols if c in pcm_df.columns],
                        "labels": {c: c for c in _cdyn_cols},
                        "targets": {c: _col_usl(c) for c in _cdyn_cols if _col_usl(c) is not None},
                    }
                _sort_groups = _friendly_groups if _friendly_groups else None
                print(f"[parametric] Sort enrichment via sicc_processor: "
                      f"{len(_friendly_groups.get('UPM',{}).get('cols',[]))} UPM + "
                      f"{len(_friendly_groups.get('SICC',{}).get('cols',[]))} SICC + "
                      f"{len(_friendly_groups.get('CDYN',{}).get('cols',[]))} CDYN")
            else:
                print("[parametric] No product_config_json — sort SICC/UPM/CDYN omitted.")

        except Exception as _exc:
            print(f"[parametric] WARNING: sort enrichment (sicc_processor) failed: {_exc}",
                  file=sys.stderr)

    # ── 5b. Apply PCM column filter ──────────────────────────────────────────
    pcm_filter = getattr(args, 'pcm_filter', '') or ''
    if pcm_filter and pcm_df is not None and len(pcm_df) > 0:
        import fnmatch as _fnm
        _id_cols = {'Lot', 'Wafer', 'Sort_X', 'Sort_Y', 'X', 'Y',
                    'Program', 'Layout', 'Material', 'TestProgram',
                    'LayoutX', 'LayoutY'}
        # Preserve sort-enriched columns (UPM/SICC/CDYN) — never filter those
        _sort_cols_set = set()
        if _sort_groups:
            for _sg in _sort_groups.values():
                _sort_cols_set.update(_sg.get('cols', []))
        _pats = [p.strip() for p in pcm_filter.split(',') if p.strip()]
        _all_pcm = [c for c in pcm_df.columns
                    if c not in _id_cols and c not in _sort_cols_set
                    and pd.api.types.is_numeric_dtype(pcm_df[c])]
        _keep = [c for c in _all_pcm
                 if any(_fnm.fnmatch(c.upper(), pat.upper()) for pat in _pats)]
        _drop = [c for c in _all_pcm if c not in _keep]
        if _drop:
            pcm_df = pcm_df.drop(columns=_drop)
        print(f"[parametric] PCM filter '{pcm_filter}' -> {len(_keep)}/{len(_all_pcm)} etest params kept"
              f" (+ {len(_sort_cols_set)} sort cols preserved)")

    # ── 6. Generate pcm_analysis.html ───────────────────────────────────────
    pcm_html_path: str | None = None
    if pcm_df is not None and len(pcm_df) > 0:
        try:
            # Fill NaN in groupby key columns so _compute_rows() doesn't drop
            # rows via pandas groupby's default dropna=True behaviour.
            for _fc in ('Program', 'Layout', 'Material'):
                if _fc in pcm_df.columns:
                    pcm_df[_fc] = pcm_df[_fc].fillna('')
            from generate_pcm_html import generate_html as _gen_pcm
            pcm_out = os.path.join(deploy_dir, "pcm_analysis.html")
            # Read pcm_param_groups default selection from product config JSON
            _pcm_default_groups: list | None = None
            _pcfg_path2 = getattr(args, "product_config_json", "") or ""
            # If not explicitly passed, auto-discover from the central shared dir
            # matching by DevRevStep from the CSV (same logic as pipeline.py)
            if not _pcfg_path2:
                import glob as _gl2, csv as _csv2, gzip as _gz2
                _central_dir = os.path.join(repo_root, "shared", "setup", "config", "yield-dashboard")
                _candidates = sorted(_gl2.glob(os.path.join(_central_dir, "*Product Config*.json")))
                if not _candidates:
                    _central_dir = os.path.join(repo_root, "shared", "spec", "collateral", "yield")
                    _candidates = sorted(_gl2.glob(os.path.join(_central_dir, "*Product Config*.json")))
                if _candidates:
                    # Try to detect DevRevStep from merged DataFrame first,
                    # then fall back to reading the CSV (with ZIP support)
                    _dv_val = ""
                    try:
                        _dv_cols2 = [c for c in pcm_df.columns if c.upper().startswith('DEVREVSTEP')]
                        if _dv_cols2:
                            _vals2 = pcm_df[_dv_cols2[0]].dropna()
                            if len(_vals2):
                                _dv_val = str(_vals2.iloc[0]).strip().upper()
                    except Exception:
                        pass
                    if not _dv_val:
                        try:
                            import zipfile as _zf_dv, io as _io_dv
                            _scsv = getattr(args, "sort_csv", "") or ""
                            if _scsv and os.path.isfile(_scsv):
                                if _scsv.lower().endswith('.gz'):
                                    _fh2 = _gz2.open(_scsv, 'rt', encoding='utf-8', errors='replace')
                                elif _scsv.lower().endswith('.zip'):
                                    _zf_obj = _zf_dv.ZipFile(_scsv)
                                    _inner = next((n for n in _zf_obj.namelist() if not n.endswith('/')), None)
                                    _fh2 = _io_dv.TextIOWrapper(_zf_obj.open(_inner), encoding='utf-8', errors='replace') if _inner else None
                                else:
                                    _fh2 = open(_scsv, 'rt', encoding='utf-8', errors='replace')
                                if _fh2:
                                    with _fh2:
                                        _rdr2 = _csv2.DictReader(_fh2)
                                        for _i2, _row2 in enumerate(_rdr2):
                                            _dv_col2 = next((h for h in _row2 if h and h.lower().startswith('devrevstep')), None)
                                            if _dv_col2:
                                                _dv_val = (_row2.get(_dv_col2) or '').strip().upper()
                                            if _dv_val or _i2 >= 20:
                                                break
                        except Exception:
                            pass
                    if _dv_val:
                        _dv6 = _dv_val[:6]
                        def _cfg_matches(p):
                            _tok = os.path.basename(p).split(' - ')[0].strip().upper()
                            return _dv_val.startswith(_tok) or _tok.startswith(_dv6)
                        _matched = next((p for p in _candidates if _cfg_matches(p)), None)
                        _pcfg_path2 = _matched or _candidates[0]
                    else:
                        _pcfg_path2 = _candidates[0]
                    print(f"[parametric] Auto-selected product config: {_pcfg_path2}")
            if _pcfg_path2 and os.path.isfile(_pcfg_path2):
                try:
                    import json as _jcfg2
                    _pcfg_data2 = _jcfg2.loads(open(_pcfg_path2, encoding="utf-8").read())
                    _pg = _pcfg_data2.get("pcm_param_groups")
                    if isinstance(_pg, list) and _pg:
                        _pcm_default_groups = [str(x) for x in _pg]
                        print(f"[parametric] pcm_param_groups (default groups): {_pcm_default_groups}")
                    else:
                        print(f"[parametric] pcm_param_groups: not found in product config")
                except Exception as _e2:
                    print(f"[parametric] WARNING: could not read product config for pcm_param_groups: {_e2}")
                    _pcfg_data2 = {}
            else:
                print(f"[parametric] pcm_param_groups: no product config path — showing all groups")
                _pcfg_data2 = {}
            # ── Load pcm_panels config (4-tier priority) ──────────────────
            # 1. Product Config JSON 'pcm_panels' key (inline)
            # 2. Product Config JSON 'pcm_panels_file' key (relative path to setup JSON)
            # 3. {product-name} - sort - yield-dashboard-plot-setup.json
            # 4. default - sort - yield-dashboard-plot-setup.json
            _pcm_panels: dict | None = None
            _setup_dir = os.path.join(repo_root, "shared", "setup", "config", "yield-dashboard")
            if isinstance(_pcfg_data2.get("pcm_panels"), dict):
                _pcm_panels = _pcfg_data2["pcm_panels"]
                print(f"[parametric] PCM panels config: from product config JSON (inline)")
            if _pcm_panels is None and _pcfg_data2.get("pcm_panels_file") and _pcfg_path2:
                _panels_rel = str(_pcfg_data2["pcm_panels_file"])
                _panels_abs = os.path.join(os.path.dirname(_pcfg_path2), _panels_rel)
                if os.path.isfile(_panels_abs):
                    try:
                        import json as _jcfg_pf
                        _pcm_panels = _jcfg_pf.load(open(_panels_abs, encoding="utf-8"))
                        print(f"[parametric] PCM panels config: {_panels_abs}")
                    except Exception:
                        pass
                else:
                    print(f"[parametric] WARNING: pcm_panels_file not found: {_panels_abs}")
            if _pcm_panels is None and _pcfg_path2:
                import re as _re2
                _prod_m = _re2.match(r'^([^\s\-]+)', os.path.basename(_pcfg_path2))
                if _prod_m:
                    _prod_name = _prod_m.group(1)
                    _specific = os.path.join(_setup_dir, f"{_prod_name} - sort - yield-dashboard-plot-setup.json")
                    if os.path.isfile(_specific):
                        try:
                            import json as _jcfg3
                            _pcm_panels = _jcfg3.load(open(_specific, encoding="utf-8"))
                            print(f"[parametric] PCM panels config: {_specific}")
                        except Exception:
                            pass
            if _pcm_panels is None:
                _default_setup = os.path.join(_setup_dir, "default - sort - yield-dashboard-plot-setup.json")
                if os.path.isfile(_default_setup):
                    try:
                        import json as _jcfg4
                        _pcm_panels = _jcfg4.load(open(_default_setup, encoding="utf-8"))
                        print(f"[parametric] PCM panels config: default setup file")
                    except Exception:
                        pass
            _gen_pcm(
                df=pcm_df,
                product_setup=product_setup,
                output_path=pcm_out,
                spec_lookup=spec_lookup,
                sort_groups=_sort_groups,
                default_groups=_pcm_default_groups,
                pcm_panels=_pcm_panels,
            )
            pcm_html_path = pcm_out
            print(f"[parametric] PCM analysis HTML: {pcm_out}")
        except Exception as exc:
            print(f"[parametric] WARNING: PCM HTML generation failed: {exc}", file=sys.stderr)
    else:
        print("[parametric] No PCM data — skipping pcm_analysis.html")

    # ── Merge PCM etest columns into _material_merged.csv ──────────────────
    # When --merged-csv is the base, skip this step: the merged CSV IS the
    # working file and pcm_df has only 9-site data (no per-die PCM to join).
    # When running standalone (no --merged-csv), join PCM columns if a
    # _material_merged.csv exists in deploy_dir.
    if not (merged_csv and os.path.isfile(merged_csv)) and pcm_df is not None and len(pcm_df) > 0:
        _mm_paths = list(Path(deploy_dir).glob('*_material_merged.csv'))
        if _mm_paths:
            _mm_path = str(_mm_paths[0])
            try:
                _mm_df = pd.read_csv(_mm_path, low_memory=False)
                _pcm_etest_pfx = ('Con_', 'Vts_', 'Isat_', 'Ioff_', 'Poff_',
                                  'Pwr_', 'Cap_', 'Res_', 'Td_', 'Cmim_',
                                  'Vbd_', 'Sub_')
                _pcm_etest_cols = [
                    c for c in pcm_df.columns
                    if any(c.startswith(p) for p in _pcm_etest_pfx)
                    and c not in _mm_df.columns
                ]
                if _pcm_etest_cols:
                    # Join on VISUAL_ID (unique per die, present in both)
                    _jk = [c for c in ['VISUAL_ID']
                           if c in pcm_df.columns and c in _mm_df.columns]
                    if not _jk:
                        # Fallback: SORT_X + SORT_Y + SORT_WAFER + SORT_LOT
                        # (pcm_df has renamed cols; try originals first)
                        _jk = [c for c in
                               ['SORT_X', 'SORT_Y', 'SORT_WAFER', 'SORT_LOT']
                               if c in pcm_df.columns and c in _mm_df.columns]
                    if _jk:
                        _pcm_slim = (
                            pcm_df[_jk + _pcm_etest_cols]
                            .drop_duplicates(subset=_jk)
                        )
                        _mm_df = _mm_df.merge(_pcm_slim, on=_jk, how='left')
                        _mm_df.to_csv(_mm_path, index=False)
                        _n_hit = _mm_df[_pcm_etest_cols[0]].notna().sum()
                        print(f"[parametric] PCM etest merged into "
                              f"{Path(_mm_path).name}: "
                              f"{len(_pcm_etest_cols)} cols, "
                              f"{_n_hit:,}/{len(_mm_df):,} rows matched")
                    else:
                        print("[parametric] WARNING: no common join key "
                              "for PCM merge into _material_merged.csv",
                              file=sys.stderr)
                else:
                    print("[parametric] PCM etest cols already in "
                          "_material_merged.csv — no merge needed")
            except Exception as _mme:
                print(f"[parametric] WARNING: PCM merge into "
                      f"_material_merged.csv failed: {_mme}", file=sys.stderr)

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _ordered_like(text: str, pattern: str) -> bool:
    """Return True if all tokens in *pattern* (split on ``*``) appear in
    *text* in order (case-insensitive).  Mirrors sicc_processor._ordered_like."""
    tokens = [t for t in pattern.split('*') if t]
    if not tokens:
        return True
    pos = 0
    text_up = text.upper()
    for tok in tokens:
        idx = text_up.find(tok.upper(), pos)
        if idx < 0:
            return False
        pos = idx + len(tok)
    return True


def _sort_groups_from_config(config_path: str, columns: list) -> "dict | None":
    """Load UPM / SICC / CDYN sort groups from a product_config_json file.

    Uses ordered-token matching (same as sicc_processor._ordered_like) so that
    patterns like ``PTH_POWER*SICC_ALL*24*V2*VCCCORE0*PC*`` correctly match
    raw sort CSV column names.

    Returns a dict like::

        {
            "UPM":  {"cols": [...], "labels": {col: label, ...}},
            "SICC": {"cols": [...], "labels": {...}},
            "CDYN": {"cols": [...], "labels": {...}},
        }

    or None if the file is absent, unreadable, or contains no matching columns.

    JSON format (Product Config JSON):
        upmInfo  → [[label, pattern, target, pct], ...]   label at index 0
        siccList → [[pattern, label], ...]                 pattern at index 0
        cdynList → [[pattern, label], ...]                 pattern at index 0
    """
    if not config_path or not os.path.isfile(config_path):
        return None
    import json as _json_cfg
    try:
        cfg = _json_cfg.loads(open(config_path, encoding="utf-8").read())
    except Exception as _e:
        print(f"[parametric] WARNING: could not read product_config_json: {_e}",
              file=sys.stderr)
        return None

    groups: dict = {}
    # (group_name, json_key, pattern_index, label_index)
    _key_map = [
        ("UPM",  "upmInfo",  1, 0),   # upmInfo:  [label, pattern, target, pct]
        ("SICC", "siccList", 0, 1),   # siccList: [pattern, label]
        ("CDYN", "cdynList", 0, 1),   # cdynList: [pattern, label]
    ]
    for gname, cfg_key, pat_idx, lbl_idx in _key_map:
        cols_seen: set = set()
        ordered_cols: list = []
        labels: dict = {}
        for entry in cfg.get(cfg_key, []):
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            pat, label = str(entry[pat_idx]), str(entry[lbl_idx])
            for col in columns:
                if col not in cols_seen and _ordered_like(col, pat):
                    ordered_cols.append(col)
                    cols_seen.add(col)
                    labels[col] = label
        if ordered_cols:
            groups[gname] = {"cols": ordered_cols, "labels": labels}

    return groups if groups else None


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate ParametricDashboard.html from sort + PCM data.")
    p.add_argument("--sort-csv",            default="", help="Path to sort yield CSV")
    p.add_argument("--outdir",              default="", help="Output folder")
    p.add_argument("--identifier",          default="", help="Run identifier (TestProgram)")
    p.add_argument("--full-site",           action="store_true",
                   help="Use full-site PCM data in addition to 9-site")
    p.add_argument("--keep-pcm-idw",        action="store_true",
                   help="Keep pcm_idw_*.csv intermediate files (default: delete after use)")
    p.add_argument("--product-setup",       default="",
                   help="Path to pcm_product_setup.json (auto-detected if omitted)")
    p.add_argument("--product-config-json", default="",
                   help="Path to Product Config JSON (upmInfo/siccList/cdynList groups)")
    p.add_argument("--spec-csv",            default="",
                   help="Path to WAT spec CSV (auto-detected if omitted)")
    p.add_argument("--yield-html",          default="",
                   help="Path to Dashboard.html for cross-link")
    p.add_argument("--upm-html",            default="",
                   help="Path to UPM output HTML")
    p.add_argument("--sicc-html",           default="",
                   help="Path to SICC output HTML")
    p.add_argument("--cdyn-html",           default="",
                   help="Path to CDYN output HTML")
    p.add_argument("--deploy-dir",          default="",
                   help="Subfolder where pcm_analysis.html and _material_merged.csv live "
                        "(auto-detected from outdir if omitted)")
    p.add_argument("--merged-csv",          default="",
                   help="Path to the reticle/material-merged CSV written by the yield "
                        "pipeline.  When provided, lot IDs are extracted from this file "
                        "and the IDW expansion step is skipped (no pcm_idw_*.csv written).")
    p.add_argument("--dashboard",           default="",
                   help="Path to Dashboard.html — parametric link is injected on success")
    p.add_argument("--config",              default="",
                   help="Path to run JSON config (DataCSV, output_folder, identifier, dashboard, "
                        "product_config_json overridable by explicit flags)")
    p.add_argument("--pcm-filter",          default="",
                   help="Comma-separated wildcard patterns to filter PCM columns "
                        "(e.g. 'Con_*,Rc_*,Vts_RN*'). Empty = all columns.")
    return p


if __name__ == "__main__":
    _args = _build_parser().parse_args()
    # If --config supplied, fill in missing args from JSON
    if _args.config and os.path.isfile(_args.config):
        import json as _json_cfg
        with open(_args.config, encoding="utf-8") as _fh:
            _cfg = _json_cfg.load(_fh)
        def _cfg_get(*keys):
            for k in keys:
                if k in _cfg and str(_cfg[k]).strip():
                    return str(_cfg[k]).strip()
            return ""
        if not _args.sort_csv:
            _args.sort_csv = _cfg_get("DataCSV", "aqua_outputfile", "outputFilename")
        if not _args.outdir:
            _out = _cfg_get("output_folder")
            _id  = _cfg_get("identifier", "TestProgram")
            _args.outdir = os.path.join(_out, _id) if _out and _id else _out
        if not _args.identifier:
            _args.identifier = _cfg_get("identifier", "TestProgram")
        if not _args.dashboard:
            _args.dashboard = _cfg_get("dashboard")
        if not _args.product_config_json:
            _args.product_config_json = _cfg_get("product_config_json")
        if not _args.product_setup:
            _args.product_setup = _cfg_get("pcm_product_setup")
        if not _args.spec_csv:
            _args.spec_csv = _cfg_get("pcm_spec_csv")
    rc = run(_args)
    sys.exit(rc)
