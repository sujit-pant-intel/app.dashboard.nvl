"""
wafer_analysis_parametric.reticle
----------------------------------
Shared reticle-map loader for parametric debug dashboards.

Returns a dict mapping (sort_x, sort_y) -> (rdx, rdy, shot_idx) built from
the product's reticle CSV in shared/reticle/.

CSV format expected (NVL816 standard):
    DieX, DieY         — absolute die coordinates (1-based)
    LayoutX, LayoutY   — shot position (identifies which reticle shot the die belongs to)
    Reticle            — intra-shot die-location number (1-based, unique within one shot)

Usage:
    from wafer_analysis_parametric.reticle import load_reticle_map

    ret_map = load_reticle_map(df, reticle_dir, devrevstep_col='DEVREVSTEP')
    # ret_map: {(sort_x, sort_y): (reticle_loc, 0, shot_idx), ...}
"""

import os
import pandas as pd


def find_reticle_csv(prefix6: str, reticle_dir: str) -> str | None:
    """Return the path to the first CSV in reticle_dir whose filename contains
    prefix6 (case-insensitive), or None if not found."""
    if not os.path.isdir(reticle_dir):
        return None
    for fname in sorted(os.listdir(reticle_dir)):
        if prefix6.upper() in fname.upper() and fname.lower().endswith('.csv'):
            return os.path.join(reticle_dir, fname)
    return None


def load_reticle_map(df: pd.DataFrame, reticle_dir: str,
                     devrevstep_col: str = 'DEVREVSTEP') -> dict:
    """Detect the correct reticle CSV from the DevRevStep column (first 6 chars),
    then return {(sort_x, sort_y): (reticle_loc, 0, shot_idx)}.

    Parameters
    ----------
    df : pd.DataFrame
        Input data frame — used only to read the DevRevStep prefix.
    reticle_dir : str
        Absolute path to the folder containing reticle CSV files.
    devrevstep_col : str
        Column name that holds the DevRevStep string (default 'DEVREVSTEP').

    Returns
    -------
    dict
        {(sort_x, sort_y): (reticle_loc, 0, shot_idx)} or {} on any failure.
        - sort_x / sort_y : SORT coordinates centred at (0, 0)
        - reticle_loc     : 1-based intra-shot die-location number
        - shot_idx        : 0-based index of the reticle shot
    """
    prefix6 = ''
    if devrevstep_col in df.columns:
        vals = df[devrevstep_col].dropna()
        if not vals.empty:
            prefix6 = str(vals.iloc[0])[:6].upper()

    if not prefix6:
        print('  [reticle] DevRevStep column empty — reticle overlay disabled')
        return {}

    csv_path = find_reticle_csv(prefix6, reticle_dir)
    if not csv_path:
        print(f'  [reticle] No reticle CSV found for prefix "{prefix6}" in {reticle_dir}')
        return {}

    print(f'  [reticle] Using {os.path.basename(csv_path)} (prefix={prefix6})')
    try:
        rdf = pd.read_csv(csv_path, usecols=['DieX', 'DieY', 'LayoutX', 'LayoutY', 'Reticle'])
        ox = round((rdf['DieX'].min() + rdf['DieX'].max()) / 2)
        oy = round((rdf['DieY'].min() + rdf['DieY'].max()) / 2)
        rdf['sx'] = (rdf['DieX'] - ox).astype(int)
        rdf['sy'] = (rdf['DieY'] - oy).astype(int)
        # Build shot index: sorted unique (LayoutX, LayoutY) pairs → 0-based shot_idx
        shot_order = sorted({(int(r.LayoutX), int(r.LayoutY)) for r in rdf.itertuples()})
        shot_idx   = {k: i for i, k in enumerate(shot_order)}
        return {(int(r.sx), int(r.sy)):
                    (int(r.Reticle), 0, shot_idx[(int(r.LayoutX), int(r.LayoutY))])
                for r in rdf.itertuples()}
    except Exception as e:
        print(f'  [reticle] load failed: {e}')
        return {}
