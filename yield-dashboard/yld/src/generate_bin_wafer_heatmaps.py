"""
Generate per-bin wafer heatmaps and a summary table for bins > 4

Usage:
    python src/generate_bin_wafer_heatmaps.py --data <yield_csv> --bindef <bindef_csv> --outdir <output_dir>

This script is intentionally standalone so it doesn't modify other pipeline code.
It expects the yield CSV to contain columns `Sort_X`, `Sort_Y`, `Lot`, `Wafer`, and
the bin column `INTERFACE_BIN_119325` (or a column name provided via --bincol).

Behavior:
- For each numeric bin > 4 present in the data and where the aggregated
  `Yield (%)` for that bin is greater than the expected yield (from bindef file),
  create a PNG named <csv_stem>_Bin{N}_WaferHeatmap.png in `outdir`.
- Each PNG contains a heatmap (Sort_X x Sort_Y) where cell values are fallout %
  (fail percent for that bin) and a table listing Lot, Wafer, % fail sorted desc.

The bindef CSV is expected to contain columns with bin definitions and an
`Expected Yield(%)` column for aggregations (compatible with the existing
`fail_bucket_table.txt` expectations used elsewhere).
"""
import argparse
import os
import sys
import math
import textwrap
from collections import defaultdict

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns


def parse_args():
    p = argparse.ArgumentParser(description="Generate wafer heatmaps for bins > 4")
    p.add_argument("--data", required=True, help="Yield CSV (with Sort_X/Sort_Y/Lot/Wafer and bin column)")
    p.add_argument("--bindef", required=False, help="Parsed bindef CSV containing Expected Yield(%%) values")
    p.add_argument("--outdir", required=False, default="output", help="Output folder for generated PNGs")
    p.add_argument("--force", action="store_true", help="Generate heatmaps even if expected yield is missing")
    p.add_argument("--bincol", default="INTERFACE_BIN_119325", help="Column name for bin field")
    p.add_argument("--failbuckets", required=False, help="Path to fail_bucket_table.txt with combined-bin expected yields")
    p.add_argument("--sortx", default="Sort_X", help="Column name for wafer X coordinate")
    p.add_argument("--sorty", default="Sort_Y", help="Column name for wafer Y coordinate")
    p.add_argument("--lotcol", default="Lot", help="Column name for Lot")
    p.add_argument("--wafercol", default="Wafer", help="Column name for Wafer")
    p.add_argument("--layoutx", default=None, help="Column name for reticle LayoutX coordinate")
    p.add_argument("--layouty", default=None, help="Column name for reticle LayoutY coordinate")
    p.add_argument("--reticle", default=None, help="Column name for Reticle number")
    return p.parse_args()


def load_expected_yields(bindef_path):
    # Try to read expected yields into dict {bin:int -> expected_pct:float}
    if not bindef_path:
        return {}
    try:
        df = pd.read_csv(bindef_path)
    except Exception:
        return {}
    expected = {}
    # Look for explicit columns 'Bin' and 'Expected Yield(%)' or parse header rows
    cols = [c.strip().lower() for c in df.columns]
    if "expected yield(%)" in df.columns:
        keycol = df.columns[0]
        for _, r in df.iterrows():
            try:
                b = str(r[keycol]).strip()
                exp = float(r["Expected Yield(%)"])
            except Exception:
                continue
            # bins may contain slashes; map each numeric token
            for tok in [t for t in b.replace("+", "/").split("/") if t]:
                try:
                    expected[int(tok)] = exp
                except Exception:
                    pass
    else:
        # fallback: attempt to find any numeric-like column and a third column as expected
        for col in df.columns:
            if col.lower().startswith("bin") or col.lower().startswith("fb"):
                # assume third column is expected
                try:
                    for _, r in df.iterrows():
                        b = str(r[col])
                        exp = float(r.iloc[2])
                        for tok in [t for t in b.replace("+", "/").split("/") if t]:
                            try:
                                expected[int(tok)] = exp
                            except Exception:
                                pass
                except Exception:
                    pass
                break
    return expected


def extract_numeric_bins(cell):
    # Extract integer tokens from a cell like '1/2/3' or '31/88/91'
    if pd.isna(cell):
        return []
    s = str(cell)
    tokens = []
    for part in s.replace("+", "/").split("/"):
        part = part.strip()
        if not part:
            continue
        try:
            tokens.append(int(part))
        except Exception:
            # try to pick integers inside string
            import re
            m = re.findall(r"(\d+)", part)
            for mm in m:
                tokens.append(int(mm))
    return tokens


def prepare_heatmap_matrix(df, bin_number, bincol, sortx, sorty, lotcol, wafercol, groups=None, display_bin=None):
    # For each Lot+Wafer, compute fail% for given bin (count fail rows / total rows *100)
    # We assume each row is a unit; if the yield CSV provides a percentage column, use counts
    group = df.groupby([lotcol, wafercol])
    records = []
    for (lot, wafer), sub in group:
        total = len(sub)
        # count rows where bin column contains the bin_number or any in bin_number list
        if isinstance(bin_number, (list, tuple, set)):
            matches = sub[bincol].apply(lambda c: any(b in extract_numeric_bins(c) for b in bin_number))
        else:
            matches = sub[bincol].apply(lambda c: bin_number in extract_numeric_bins(c))
        fails = matches.sum()
        fail_pct = (fails / total) * 100 if total > 0 else 0.0
        # take median/first Sort_X/Y from this wafer
        sx = sub[sortx].dropna().astype(float).median() if sortx in sub.columns else np.nan
        sy = sub[sorty].dropna().astype(float).median() if sorty in sub.columns else np.nan
        # collect unique numeric bins present on this wafer (for 'bins per wafer' column)
        bins_on_wafer = set()
        for v in sub[bincol].dropna().unique():
            for tok in extract_numeric_bins(v):
                bins_on_wafer.add(tok)
        bins_list = sorted(bins_on_wafer)
        bins_str = "/".join(str(x) for x in bins_list) if bins_list else ""
        # determine fail-bucket labels for this wafer (may match multiple groups)
        fb_str = ""
        if groups:
            # If display_bin is a single numeric bin, prefer the fail-bucket label that contains that bin
            try:
                if display_bin is not None:
                    db = str(display_bin).strip()
                    db_tokens = extract_numeric_bins(db)
                    if len(db_tokens) == 1:
                        db_int = int(db_tokens[0])
                        for g in groups:
                            if db_int in g.get("bins", []):
                                fb_str = g.get("label", "")
                                break
            except Exception:
                fb_str = ""
            # fallback: populate based on bins present on wafer
            if not fb_str:
                fb_labels = []
                for g in groups:
                    if any(b in bins_on_wafer for b in g.get("bins", [])):
                        fb_labels.append(g.get("label", ""))
                fb_str = ",".join(sorted(set([l for l in fb_labels if l])))
        # BinDisplay: use provided display_bin or the numeric bin(s) on wafer
        bin_display = display_bin if display_bin is not None else bins_str
        records.append({
            "Lot": lot,
            "Wafer": wafer,
            "Fail%": fail_pct,
            "Sort_X": sx,
            "Sort_Y": sy,
            "Bins": bins_str,
            "Fails": int(fails),
            "Total": int(total),
            "FailBucket": fb_str,
            "BinDisplay": bin_display,
        })
    recdf = pd.DataFrame(records)
    # produce matrix keyed by Sort_Y (rows) and Sort_X (cols)
    # handle missing or NaN coordinates by dropping those wafers from heatmap but keep in table
    mat = None
    if not recdf.empty and recdf["Sort_X"].notna().any() and recdf["Sort_Y"].notna().any():
        # aggregate by coordinate: sum Fails and Total across wafers at same location
        piv = recdf.dropna(subset=["Sort_X", "Sort_Y"]).copy()
        agg = piv.groupby(["Sort_Y", "Sort_X"]).agg({"Fails": "sum", "Total": "sum"}).reset_index()
        # compute combined fail % per location
        agg["Fail%"] = agg.apply(lambda r: (r["Fails"] / r["Total"]) * 100.0 if r["Total"] > 0 else 0.0, axis=1)
        # pivot to matrix
        mat = agg.pivot(index="Sort_Y", columns="Sort_X", values="Fail%").sort_index(ascending=False)
    return recdf, mat


def _overlay_reticle_grid(ax, mat, reticle_info):
    """Overlay reticle boundary lines and reticle numbers on a seaborn heatmap."""
    if reticle_info is None or mat is None:
        return

    cols = list(mat.columns)   # Sort_X values (ascending)
    rows = list(mat.index)     # Sort_Y values (descending as displayed)

    # Build lookup: (Sort_X, Sort_Y) -> (LayoutX, LayoutY, Reticle)
    lookup = {}
    for _, r in reticle_info.iterrows():
        sx = r["Sort_X"]
        sy = r["Sort_Y"]
        lx = r.get("LayoutX")
        ly = r.get("LayoutY")
        ret = r.get("Reticle") if "Reticle" in reticle_info.columns else None
        lookup[(sx, sy)] = (lx, ly, ret)

    # Draw vertical line segments where LayoutX changes between adjacent columns
    for j in range(len(cols) - 1):
        for i in range(len(rows)):
            key_left = (cols[j], rows[i])
            key_right = (cols[j + 1], rows[i])
            lx_left = lookup.get(key_left, (None,))[0]
            lx_right = lookup.get(key_right, (None,))[0]
            if lx_left is not None and lx_right is not None and lx_left != lx_right:
                ax.plot([j + 1, j + 1], [i, i + 1], color="blue", linewidth=2, alpha=0.8)

    # Draw horizontal line segments where LayoutY changes between adjacent rows
    for i in range(len(rows) - 1):
        for j in range(len(cols)):
            key_top = (cols[j], rows[i])
            key_bottom = (cols[j], rows[i + 1])
            ly_top = lookup.get(key_top, (None, None))[1]
            ly_bottom = lookup.get(key_bottom, (None, None))[1]
            if ly_top is not None and ly_bottom is not None and ly_top != ly_bottom:
                ax.plot([j, j + 1], [i + 1, i + 1], color="blue", linewidth=2, alpha=0.8)

    # Annotate each cell with its reticle number
    for i, sy in enumerate(rows):
        for j, sx in enumerate(cols):
            info = lookup.get((sx, sy))
            if info and info[2] is not None:
                try:
                    reticle_num = int(info[2])
                    ax.text(j + 0.5, i + 0.5, str(reticle_num),
                            ha="center", va="center", fontsize=5,
                            color="black", fontweight="bold", alpha=0.7)
                except (ValueError, TypeError):
                    pass


def _overlay_reticle_grid_round(ax, mat, reticle_info, die_dx, die_dy, gap, cx, cy):
    """Overlay reticle boundary lines and numbers on the round wafer map."""
    if reticle_info is None or mat is None:
        return

    cols = sorted(mat.columns.values.astype(float))
    rows = sorted(mat.index.values.astype(float))

    # Build lookup: (Sort_X, Sort_Y) -> (LayoutX, LayoutY, Reticle)
    lookup = {}
    for _, r in reticle_info.iterrows():
        sx = r["Sort_X"]
        sy = r["Sort_Y"]
        lx = r.get("LayoutX")
        ly = r.get("LayoutY")
        ret = r.get("Reticle") if "Reticle" in reticle_info.columns else None
        lookup[(sx, sy)] = (lx, ly, ret)

    # Draw vertical boundary lines where LayoutX changes between adjacent columns
    for j in range(len(cols) - 1):
        for i in range(len(rows)):
            key_left = (cols[j], rows[i])
            key_right = (cols[j + 1], rows[i])
            lx_left = lookup.get(key_left, (None,))[0]
            lx_right = lookup.get(key_right, (None,))[0]
            if lx_left is not None and lx_right is not None and lx_left != lx_right:
                bx = ((cols[j] + cols[j + 1]) / 2 - cx) * die_dx
                by = (rows[i] - cy) * die_dy
                ax.plot([bx, bx], [by - die_dy * gap / 2, by + die_dy * gap / 2],
                        color="blue", linewidth=1.5, alpha=0.8)

    # Draw horizontal boundary lines where LayoutY changes between adjacent rows
    for i in range(len(rows) - 1):
        for j in range(len(cols)):
            key_top = (cols[j], rows[i])
            key_bottom = (cols[j], rows[i + 1])
            ly_top = lookup.get(key_top, (None, None))[1]
            ly_bottom = lookup.get(key_bottom, (None, None))[1]
            if ly_top is not None and ly_bottom is not None and ly_top != ly_bottom:
                bx = (cols[j] - cx) * die_dx
                by = ((rows[i] + rows[i + 1]) / 2 - cy) * die_dy
                ax.plot([bx - die_dx * gap / 2, bx + die_dx * gap / 2], [by, by],
                        color="blue", linewidth=1.5, alpha=0.8)

    # Annotate each cell with its reticle number
    for sy_val in rows:
        for sx_val in cols:
            info = lookup.get((sx_val, sy_val))
            if info and info[2] is not None:
                try:
                    reticle_num = int(info[2])
                    ax.text((sx_val - cx) * die_dx, (sy_val - cy) * die_dy,
                            str(reticle_num),
                            ha="center", va="center", fontsize=5,
                            color="black", fontweight="bold", alpha=0.7)
                except (ValueError, TypeError):
                    pass


def render_heatmap_and_table(outpath, csv_stem, bin_number, recdf, mat, expected_val=None, display_bin=None, reticle_info=None):
    sns.set(style="whitegrid")
    # use a wider page to accommodate the table columns
    fig = plt.figure(figsize=(14, 10))
    gs = fig.add_gridspec(3, 1, height_ratios=(2, 3, 2))

    # Title
    ax_title = fig.add_subplot(gs[0, 0])
    ax_title.axis("off")
    # allow bin_number to be either numeric or a label (group)
    title_text = f"{csv_stem}  {bin_number} Wafer Fallout"
    ax_title.text(0.5, 0.5, title_text, ha="center", va="center", fontsize=14, fontweight="bold")

    # Heatmap — round wafer map using individual die rectangles
    # (follows wafer_map_simple.py logic exactly)
    ax_hm = fig.add_subplot(gs[1, 0])
    if mat is not None:
        _cols_v = mat.columns.values.astype(float)   # Sort_X values
        _rows_v = mat.index.values.astype(float)     # Sort_Y values

        # Center coordinates at (0,0) — same as wafer_map_simple.py
        cx = (_cols_v.min() + _cols_v.max()) / 2.0
        cy = (_rows_v.min() + _rows_v.max()) / 2.0
        _cols_c = _cols_v - cx   # centered X
        _rows_c = _rows_v - cy   # centered Y

        # Scale Y so wafer appears circular  (wafer_map_simple.py: die_dy = x_range / y_range)
        x_range = _cols_v.max() - _cols_v.min()
        y_range = _rows_v.max() - _rows_v.min()
        die_dx = 1.0
        die_dy = (x_range / y_range) if y_range > 0 else 1.0

        # Colormap: Reds for fail%
        vmax = max(1.0, np.nanmax(mat.values))
        cmap = plt.cm.Reds
        norm_cm = plt.Normalize(vmin=0, vmax=vmax)

        # Draw each die as a rectangle (wafer_map_simple.py style)
        gap = 0.9
        for sy_raw, sy_c in zip(_rows_v, _rows_c):
            for sx_raw, sx_c in zip(_cols_v, _cols_c):
                try:
                    val = mat.loc[sy_raw, sx_raw]
                except KeyError:
                    continue
                if np.isnan(val):
                    continue
                px = sx_c * die_dx
                py = sy_c * die_dy
                color = cmap(norm_cm(val))
                rect = mpatches.Rectangle(
                    (px - die_dx * gap / 2, py - die_dy * gap / 2),
                    die_dx * gap, die_dy * gap,
                    linewidth=0.3, edgecolor="gray", facecolor=color
                )
                ax_hm.add_patch(rect)

        # wafer_map_simple.py: NO circle outline — dies themselves form the round shape

        # Overlay reticle grid if available
        if reticle_info is not None:
            _overlay_reticle_grid_round(ax_hm, mat, reticle_info,
                                        die_dx, die_dy, gap, cx, cy)

        # Colorbar
        sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm_cm)
        sm.set_array([])
        cbar = plt.colorbar(sm, ax=ax_hm, fraction=0.046, pad=0.04)
        cbar.set_label("% fail", fontsize=12)

        # Axis limits: max absolute centered coord + 5%
        extent_x = (abs(_cols_c).max() * die_dx + die_dx * 0.5) * 1.025
        extent_y = (abs(_rows_c).max() * die_dy + die_dy * 0.5) * 1.025
        ax_hm.set_xlim(-extent_x, extent_x)
        ax_hm.set_ylim(-extent_y, extent_y)
        ax_hm.set_aspect("equal")
        ax_hm.set_xlabel("Sort_X", fontsize=12)
        ax_hm.set_ylabel("Sort_Y", fontsize=12)
        # Remap Y-axis ticks to original Sort_Y values (undo die_dy scaling + centering)
        y_ticks = [t for t in ax_hm.get_yticks() if -extent_y <= t <= extent_y]
        ax_hm.set_yticks(y_ticks)
        ax_hm.set_yticklabels([f"{v / die_dy + cy:.0f}" if die_dy != 0
                                else f"{v:.0f}" for v in y_ticks])
        # Remap X-axis ticks to original Sort_X values (undo centering)
        x_ticks = [t for t in ax_hm.get_xticks() if -extent_x <= t <= extent_x]
        ax_hm.set_xticks(x_ticks)
        ax_hm.set_xticklabels([f"{v + cx:.0f}" for v in x_ticks])
        ax_hm.set_xlim(-extent_x, extent_x)
        ax_hm.set_ylim(-extent_y, extent_y)
        ax_hm.axhline(0, color="black", linewidth=0.5, linestyle="--", alpha=0.3)
        ax_hm.axvline(0, color="black", linewidth=0.5, linestyle="--", alpha=0.3)
        ax_hm.grid(True, alpha=0.2)
        ax_hm.set_title("Wafer Fallout Heatmap (higher = worse)")
    else:
        ax_hm.text(0.5, 0.5, "No wafer coordinates available for heatmap", ha="center", va="center")
        ax_hm.axis("off")

    # Table
    ax_tab = fig.add_subplot(gs[2, 0])
    ax_tab.axis("off")
    # build table: Lot, Wafer, Bin (or group label), Yield (Fail%), Expected Yield, Fail Bucket
    tdf = recdf[["Lot", "Wafer", "Fail%", "Bins", "FailBucket", "BinDisplay"]].copy()
    tdf = tdf.sort_values(by="Fail%", ascending=False).reset_index(drop=True)
    # limit to top 50 rows to keep image readable
    tdf = tdf.head(50)
    # prepare cell text with numeric formatting for Fail% and expected value
    cell_rows = []
    expected_str = f"{expected_val:.2f}" if expected_val is not None else ""
    for _, r in tdf.iterrows():
        # prefer BinDisplay for the Bin column; fallback to provided display_bin or bin_number
        bin_col = r.get("BinDisplay") or display_bin or str(bin_number)
        cell_rows.append([
            str(r["Lot"]),
            str(r["Wafer"]),
            str(bin_col),
            f"{r['Fail%']:.2f}",
            expected_str,
            r.get("FailBucket", "")
        ])
    # compute column widths proportional to max text length to avoid clipping
    try:
        cols = list(zip(*(["Lot","Wafer","Bin","Yield","Expected","Fail Bucket"] ,) + tuple([tuple(r) for r in cell_rows])))
    except Exception:
        cols = None
    col_widths = None
    if cols:
        try:
            max_lens = [max(len(str(x)) for x in col) for col in cols]
            total_chars = sum(max_lens) or 1
            # base fractions
            col_widths = [max(0.08, ml / total_chars * 1.1) for ml in max_lens]
            # normalize to sum to ~0.95 to leave margins
            s = sum(col_widths) or 1.0
            col_widths = [w / s * 0.95 for w in col_widths]
        except Exception:
            col_widths = None

    tbl = ax_tab.table(cellText=cell_rows, colLabels=["Lot", "Wafer", "Bin", "Yield", "Expected", "Fail Bucket"], colWidths=col_widths, loc="center", cellLoc='left')
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    # scale table rows slightly for readability
    tbl.scale(1.2, 1.2)

    # Apply conditional coloring per user's request:
    # For standalone per-bin heatmaps (display_bin is a single numeric value):
    #  - If bin == 1: highlight row red when Yield < Expected
    #  - Otherwise (all other standalone bins): highlight row red when Yield > Expected
    # For group heatmaps (non-numeric display_bin), keep previous behavior (highlight when Yield > Expected)
    def _is_single_numeric(s):
        try:
            toks = extract_numeric_bins(str(s))
            return len(toks) == 1
        except Exception:
            return False

    single_bin = _is_single_numeric(display_bin)

    # header row bgcolor
    for col in range(len(cell_rows[0])):
        try:
            cell = tbl[(-1, col)]
            cell.set_facecolor("#cccccc")
        except Exception:
            pass

    for i, row in enumerate(cell_rows):
        try:
            yield_val = float(row[3]) if row[3] != "" else None
            exp_val = float(expected_val) if expected_val is not None else None
        except Exception:
            yield_val = None
            exp_val = None
        highlight = False
        if exp_val is not None and yield_val is not None:
            if single_bin:
                # single numeric bin behavior
                try:
                    bin_tok = extract_numeric_bins(str(display_bin))[0]
                    if int(bin_tok) == 1:
                        highlight = (yield_val < exp_val)
                    else:
                        highlight = (yield_val > exp_val)
                except Exception:
                    highlight = (yield_val > exp_val)
            else:
                # group/default behavior: highlight when yield > expected
                highlight = (yield_val > exp_val)
        face = "#ff9999" if highlight else "white"
        for col in range(len(row)):
            try:
                cell = tbl[(i, col)]
                cell.set_facecolor(face)
            except Exception:
                pass

    plt.tight_layout()
    fig.savefig(outpath, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main():
    args = parse_args()
    data_path = args.data
    bindef_path = args.bindef
    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)

    try:
        df = pd.read_csv(data_path, dtype=object)
    except Exception as e:
        print(f"Failed to read data CSV: {e}", file=sys.stderr)
        sys.exit(2)

    expected = load_expected_yields(bindef_path)
    csv_stem = os.path.splitext(os.path.basename(data_path))[0]

    # Auto-detect IBIN column if the specified one is missing
    if args.bincol not in df.columns:
        alt = next((c for c in df.columns if 'INTERFACE_BIN' in c.upper() and 'TOTAL' not in c.upper()), None)
        if alt:
            print(f"Column '{args.bincol}' not found in CSV; auto-detected '{alt}'")
            args.bincol = alt
        else:
            print(f"Error: column '{args.bincol}' not found in CSV.\n"
                  f"Available columns: {list(df.columns)}", file=sys.stderr)
            sys.exit(2)

    # collect all numeric bins present in dataset
    bins_present = set()
    for v in df[args.bincol].dropna().unique():
        for tok in extract_numeric_bins(v):
            bins_present.add(tok)

    print(f"Found {len(bins_present)} numeric bins in data: {sorted(bins_present)}")
    print(f"Loaded expected yields for {len(expected)} bins from bindef")

    # Resolve Lot/Wafer column names if defaults not present
    lot_candidates = [args.lotcol, "SORT_LOT", "Lot_119325", "LOT", "LOT_119325"]
    lotcol_name = next((c for c in lot_candidates if c in df.columns), None)
    if lotcol_name is None:
        lotcol_name = next((c for c in df.columns if "lot" in c.lower()), args.lotcol)

    wafer_candidates = [args.wafercol, "SORT_WAFER", "Wafer_119325", "WAFER", "WAFER_119325"]
    wafercol_name = next((c for c in wafer_candidates if c in df.columns), None)
    if wafercol_name is None:
        wafercol_name = next((c for c in df.columns if "wafer" in c.lower()), args.wafercol)

    print(f"Using Lot column: {lotcol_name}; Wafer column: {wafercol_name}")

    # Resolve Sort_X/Sort_Y column names robustly (many CSVs use uppercase or different names)
    sortx_candidates = [args.sortx, "SORT_X", "SortX", "sort_x", "X"]
    sortx_name = next((c for c in sortx_candidates if c in df.columns), None)
    if sortx_name is None:
        sortx_name = next((c for c in df.columns if "sort_x" in c.lower() or c.lower() == 'x'), args.sortx)

    sorty_candidates = [args.sorty, "SORT_Y", "SortY", "sort_y", "Y"]
    sorty_name = next((c for c in sorty_candidates if c in df.columns), None)
    if sorty_name is None:
        sorty_name = next((c for c in df.columns if "sort_y" in c.lower() or c.lower() == 'y'), args.sorty)

    print(f"Using Sort_X column: {sortx_name}; Sort_Y column: {sorty_name}")

    # Detect LayoutX / LayoutY / Reticle columns for reticle grid overlay
    layoutx_candidates = [args.layoutx] if args.layoutx else []
    layoutx_candidates += ["LayoutX", "layoutX", "LAYOUTX", "layout_x", "Layout_X", "LAYOUT_X"]
    layoutx_col = next((c for c in layoutx_candidates if c and c in df.columns), None)

    layouty_candidates = [args.layouty] if args.layouty else []
    layouty_candidates += ["LayoutY", "layoutY", "LAYOUTY", "layout_y", "Layout_Y", "LAYOUT_Y"]
    layouty_col = next((c for c in layouty_candidates if c and c in df.columns), None)

    reticle_candidates = [args.reticle] if args.reticle else []
    reticle_candidates += ["Reticle", "reticle", "RETICLE", "Reticle_Number", "ReticleNumber"]
    reticle_col = next((c for c in reticle_candidates if c and c in df.columns), None)

    reticle_info = None
    if layoutx_col and layouty_col:
        ri_cols = [sortx_name, sorty_name, layoutx_col, layouty_col]
        if reticle_col:
            ri_cols.append(reticle_col)
        reticle_info = df[ri_cols].drop_duplicates(subset=[sortx_name, sorty_name]).copy()
        rename_map = {sortx_name: "Sort_X", sorty_name: "Sort_Y",
                      layoutx_col: "LayoutX", layouty_col: "LayoutY"}
        if reticle_col:
            rename_map[reticle_col] = "Reticle"
        reticle_info = reticle_info.rename(columns=rename_map)
        print(f"Reticle overlay enabled: LayoutX={layoutx_col}, LayoutY={layouty_col}" +
              (f", Reticle={reticle_col}" if reticle_col else ""))
    else:
        print("Reticle overlay: LayoutX/LayoutY columns not found, skipping overlay")

    # parse fail-bucket groups if provided
    groups = []
    if args.failbuckets:
        try:
            with open(args.failbuckets, "r", encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln or ln.startswith("#"):
                        continue
                    # expect pipe-separated lines: | bins | label | expected |
                    if "|" in ln:
                        parts = [p.strip() for p in ln.split("|") if p.strip()]
                        if len(parts) >= 3:
                            bins_str = parts[0]
                            label = parts[1]
                            try:
                                expected_val = float(parts[2])
                            except Exception:
                                expected_val = None
                            # parse bin tokens
                            bin_tokens = []
                            for tok in bins_str.replace("+", "/").split("/"):
                                tok = tok.strip()
                                if not tok:
                                    continue
                                try:
                                    bin_tokens.append(int(tok))
                                except Exception:
                                    # maybe token contains non-digit; extract digits
                                    import re
                                    for m in re.findall(r"(\d+)", tok):
                                        try:
                                            bin_tokens.append(int(m))
                                        except Exception:
                                            pass
                            if bin_tokens:
                                groups.append({"label": label, "bins": sorted(set(bin_tokens)), "expected": expected_val, "bins_str": bins_str})
        except Exception:
            print(f"Failed to read fail-buckets file: {args.failbuckets}")

    if groups:
        print(f"Loaded {len(groups)} fail-bucket groups from {args.failbuckets}")

    # Build map of expected yields for single-bin groups from fail-buckets (bin -> expected)
    group_expected_map = {}
    for g in groups:
        try:
            if g.get("expected") is not None and isinstance(g.get("bins"), (list, tuple)) and len(g.get("bins")) == 1:
                group_expected_map[int(g["bins"][0])] = float(g.get("expected"))
        except Exception:
            continue

    # for each bin > 4, compute aggregated yield across entire dataset
    for b in sorted(bins_present):
        if b <= 4:
            continue
        # overall yield% for bin = (count where bin present) / total rows *100
        total = len(df)
        if total == 0:
            continue
        present_count = df[args.bincol].apply(lambda c: b in extract_numeric_bins(c)).sum()
        yield_pct = (present_count / total) * 100.0
        exp = expected.get(b, None)
        # fallback to single-bin expected value from fail-bucket groups
        if exp is None:
            exp = group_expected_map.get(int(b), None)
        # create image if expected exists and yield_pct > expected
        if exp is not None and yield_pct > exp:
            recdf, mat = prepare_heatmap_matrix(df, b, args.bincol, sortx_name, sorty_name, lotcol_name, wafercol_name, groups=groups, display_bin=str(b))
            outpath = os.path.join(outdir, f"{csv_stem}_Bin{b}_WaferHeatmap.png")
            render_heatmap_and_table(outpath, csv_stem, b, recdf, mat, expected_val=exp, display_bin=str(b), reticle_info=reticle_info)
            print(f"Wrote {outpath}")
        else:
            # If expected missing, optionally force-generation based on flag
            if exp is None:
                if args.force:
                    recdf, mat = prepare_heatmap_matrix(df, b, args.bincol, sortx_name, sorty_name, lotcol_name, wafercol_name, groups=groups, display_bin=str(b))
                    outpath = os.path.join(outdir, f"{csv_stem}_Bin{b}_WaferHeatmap.png")
                    render_heatmap_and_table(outpath, csv_stem, b, recdf, mat, expected_val=None, display_bin=str(b), reticle_info=reticle_info)
                    print(f"Wrote (forced) {outpath} (yield {yield_pct:.3f}%)")
                else:
                    print(f"Skipping Bin {b}: no expected yield found; overall yield {yield_pct:.3f}%")
            else:
                print(f"Skipping Bin {b}: yield {yield_pct:.3f}% <= expected {exp}")

    # Now evaluate fail-bucket groups (combined bins)
    for g in groups:
        bins_list = g["bins"]
        total = len(df)
        if total == 0:
            continue
        # Skip groups that contain any bin <= 4 per user rule
        if any(int(b) <= 4 for b in bins_list):
            print(f"Skipping group '{g.get('label')}' because it contains bin(s) <= 4: {bins_list}")
            continue
        present_count = df[args.bincol].apply(lambda c: any(b in extract_numeric_bins(c) for b in bins_list)).sum()
        yield_pct = (present_count / total) * 100.0
        exp = g.get("expected", None)
        label_safe = "_".join([t for t in g.get("label", "group").replace("/","_").replace(" ","_").replace("(","").replace(")","").split()])
        outpath = os.path.join(outdir, f"{csv_stem}_{label_safe}_GroupHeatmap.png")
        if exp is not None and yield_pct > exp:
            recdf, mat = prepare_heatmap_matrix(df, bins_list, args.bincol, sortx_name, sorty_name, lotcol_name, wafercol_name, groups=groups, display_bin=g.get('bins_str'))
            render_heatmap_and_table(outpath, csv_stem, label_safe, recdf, mat, expected_val=exp, reticle_info=reticle_info)
            print(f"Wrote group heatmap {outpath} (yield {yield_pct:.3f}% > expected {exp})")
        else:
            print(f"Skipping group '{g.get('label')}' (yield {yield_pct:.3f}% <= expected {exp})")


if __name__ == "__main__":
    main()
