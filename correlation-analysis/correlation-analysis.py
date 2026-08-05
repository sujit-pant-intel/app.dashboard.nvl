"""
correlation-analysis.py  (tkinter)
====================================
Sort + Etest Correlation GUI

Run:
    python correlation-analysis.py

Install:
    pip install -r requirements.txt --proxy http://proxy-us.intel.com:911
"""
import sys, os, re, zipfile, fnmatch, threading, traceback, json, datetime, html as _html
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import time as _stg_time
from collections import defaultdict as _defaultdict


def signal_label(r, q, n=None):
    """Classify a correlation result by FDR q-value and effect magnitude."""
    ar = abs(r) if r is not None else 0.0
    if q is None: q = 1.0
    if q <= 0.01 and ar >= 0.30: return "Strong"
    if q <= 0.05 and ar >= 0.15: return "Moderate"
    if q <= 0.10: return "Weak / directional"
    return "Exploratory"


def flag_duplicate_stats(top, keys=("r", "p", "q"), round_to=6):
    """Group parameters that share identical rounded statistics; adds dup_group_size and is_duplicate_stat."""
    buckets = _defaultdict(list)
    for item in top:
        sig = tuple(round(item[k], round_to) if item.get(k) is not None else None for k in keys)
        buckets[sig].append(item["param"])
    for item in top:
        sig = tuple(round(item[k], round_to) if item.get(k) is not None else None for k in keys)
        group = buckets[sig]
        item["dup_group_size"] = len(group)
        item["is_duplicate_stat"] = len(group) > 1
    return top


def collapse_collinear(top, keys=("r", "p", "q"), round_to=6):
    """Collapse parameters with identical rounded stats to one representative + member list."""
    buckets = _defaultdict(list)
    for item in top:
        sig = tuple(round(item[k], round_to) if item.get(k) is not None else None for k in keys)
        buckets[sig].append(item)
    collapsed = []
    for group in buckets.values():
        rep = dict(max(group, key=lambda d: abs(d.get("cohen_d") or d.get("d") or d.get("r") or 0)))
        rep["group_members"] = [g["param"] for g in group]
        rep["group_size"] = len(group)
        collapsed.append(rep)
    collapsed.sort(key=lambda d: abs(d.get("r") or 0), reverse=True)
    return collapsed


def separation_note(r, cohen_d):
    """Explain the low-r / high-Cohen-d pattern for rare-fail modes."""
    if cohen_d is None: return ""
    if abs(r) < 0.10 and abs(cohen_d) >= 0.8:
        return ("Weak linear r but strong pass/fail separation "
                "(rare-fail effect; interpret with Cohen d + fail ratio).")
    return ""


def _drop_constant_columns(
    df: pd.DataFrame,
    param_cols: list,
    target_col: str,
    min_unique: int = 3,
    min_std: float = 1e-9,
    fail_ratio_tol: float = 0.02,
    drop_prefixes: tuple = ("SPA_",),
    verbose: bool = True,
):
    kept, dropped = [], []
    y = df[target_col].to_numpy(dtype=float)
    fail_mask = y == 1
    pass_mask = ~fail_mask
    has_fail = fail_mask.any()
    has_pass = pass_mask.any()
    for c in param_cols:
        s = df[c]
        reason = None
        if any(c.startswith(p) for p in drop_prefixes):
            reason = f"fixed test setting ({'/'.join(drop_prefixes)})"
        elif s.nunique(dropna=True) < min_unique:
            reason = f"<{min_unique} unique values"
        elif np.nanstd(s.to_numpy(dtype=float)) < min_std:
            reason = "zero variance"
        elif has_fail and has_pass:
            x = s.to_numpy(dtype=float)
            with np.errstate(all="ignore"), _warnings.catch_warnings():
                _warnings.simplefilter("ignore", RuntimeWarning)
                fmean = np.nanmean(x[fail_mask])
                pmean = np.nanmean(x[pass_mask])
            if pmean != 0.0 and not np.isnan(pmean):
                ratio = fmean / pmean
                if (not np.isnan(ratio)
                        and abs(ratio - 1.0) < fail_ratio_tol
                        and np.nanstd(x) < min_std * 10):
                    reason = "fail_ratio~1.0 + flat"
        if reason:
            dropped.append({"param": c, "reason": reason})
        else:
            kept.append(c)
    return kept, dropped


def _tag_structural_covariates(
    param_cols: list,
    struct_patterns: tuple = (
        "radius", "reticle", "shot", "_zone", "sort_x", "sort_y",
    ),
):
    elec_cols, struct_cols = [], []
    for c in param_cols:
        cl = c.lower()
        if any(p in cl for p in struct_patterns):
            struct_cols.append(c)
        else:
            elec_cols.append(c)
    return elec_cols, struct_cols


# Tab ordering for report and test assertions
TAB_ORDER = [
    "summary", "lot_wafer_trend", "wafer_level_trend",
    "pcm_wdev", "pcm_dev", "pcm_wafer", "pcm_lot",
    "pearson", "spearman",
    "methods_structural",
    "zone_analysis", "parameter_detail", "how_to_read",
]




import numpy as np
import pandas as pd
import warnings as _warnings
# PerformanceWarning (DataFrame fragmentation) is cosmetic here — wide PCM frames
# are built once then read-only; silence to keep the console clean.
try:
    from pandas.errors import PerformanceWarning as _PerfWarning
    _warnings.simplefilter("ignore", _PerfWarning)
except Exception:
    pass

# --- paths ---
# sys.frozen is set by PyInstaller; __file__ points to _MEIPASS (temp dir) when frozen
def _get_start_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

_SCRIPT_DIR = _get_start_dir()

def _find_shared_dir(start: Path, max_levels: int = 8) -> Path:
    """Walk up the directory tree from *start* looking for a 'shared' folder."""
    current = start.resolve()
    for _ in range(max_levels):
        candidate = current / "shared"
        if candidate.is_dir():
            return candidate
        current = current.parent
    return start / "shared"   # fallback – may not exist, callers check is_dir()

_SHARED_DIR  = _find_shared_dir(_SCRIPT_DIR)
_REPO_ROOT   = _SHARED_DIR.parent   # kept for backward compat
# full-sites checked before 9-sites (full-site data needs no IDW interpolation)
_ETEST_FULL_ROOTS = [str(_SHARED_DIR / "etest" / "full-sites")]
_ETEST_9_ROOTS    = [str(_SHARED_DIR / "etest" / "9-sites")]
ZIP_SEP = "::"
_RETICLE_DIR = str(_SHARED_DIR / "reticle")

# --- colours ---
BG     = "#1a252f"
BG2    = "#0d1b26"
BG3    = "#2c3e50"
BORDER = "#1e3a5f"
FG     = "#ecf0f1"
FG_DIM = "#7f8c8d"
BLUE   = "#3498db"
GREEN  = "#2ecc71"
ORANGE = "#e67e22"
RED    = "#e74c3c"

FONT    = ("Segoe UI", 10)
FONT_SM = ("Segoe UI", 9)
FONT_HD = ("Segoe UI", 11, "bold")
FONT_CO = ("Consolas", 9)

# --- HTML report constants ---
_BTN_STYLE = ("background:#162840;color:#95a5a6;border:1px solid #2c4a6e;"
              "padding:2px 8px;border-radius:4px;cursor:pointer;font-size:11px")

_CORR_METHODS = {
    "pearson":            "Pearson r",
    "spearman":           "Spearman rho",
    "pcm_lot":            "PCM Lot-Level",
    "pcm_wafer":          "PCM Wafer-Level",
    "pcm_dev":            "PCM Deviation (Lot)",
    "pcm_wdev":           "PCM Deviation (Wafer)",
    "methods_structural": "Spatial / Radial",
}
_CORR_EXPLAIN = {
    "pearson": ("<b>Pearson r — Die-Level Correlation</b><br><br>""<b>Unit of analysis:</b> one row = one die.<br>""<b>How it is computed:</b><br>""1. Build two aligned vectors across all dies: the parameter value (x) and the binary target ""(y = 1 if the die hit the target bin, else 0).<br>""2. Pearson r = cov(x, y) / (sd(x)&middot;sd(y)) — the standardized linear covariation.<br>""3. p-value from a t-test on r with n&minus;2 df; 95% CI via Fisher z-transform.<br>""4. <b>Effect size</b> is also reported: <i>Fail&times; ratio</i> = mean(x in fails)/mean(x in pass), ""and <i>Cohen d</i> = (mean_fail &minus; mean_pass)/pooled_sd.<br><br>""<b>Why effect size matters here:</b> with a rare bin (&lt;1% fails), r is mathematically capped ""near zero even for a real driver — so <b>Cohen d / ratio are the better \u2018does it matter\u2019 metric</b> ""at die level. r &gt; 0 means higher parameter &rarr; more fails. Sensitive to outliers."),
    "spearman": ("<b>Spearman rho — Rank Correlation</b><br><br>""<b>Unit of analysis:</b> one row = one die.<br>""<b>How it is computed:</b><br>""1. Replace each parameter value by its <b>rank</b> among all dies (ties get average rank).<br>""2. Compute Pearson r on the <i>ranks</i> of x vs the target y. That is Spearman rho.<br>""3. p-value uses the same t-approximation as Pearson.<br><br>""<b>Why rank-based:</b> it captures any <b>monotonic</b> trend (consistently up or down) even if ""the relationship is curved, and it is far more robust to outlier probe values than Pearson. ""Preferred for skewed process data. rho &gt; 0 = higher parameter &rarr; more fails."),
    "kendall": ("<b>Kendall tau</b> counts concordant vs discordant pairs. "
                "More conservative (lower magnitude) than Spearman but more reliable "
                "for small samples and data with many tied ranks."),
    "mutual_info": ("<b>Mutual Information</b> detects <b>any dependency</b> - linear, "
                    "non-linear, or non-monotonic - using information theory. "
                    "Value 0 = independent; higher = stronger link. "
                    "No direction (positive/negative) and no p-value."),
    "point_biserial": ("<b>Point-biserial r</b> is Pearson r applied when the <b>target is binary</b> "
                       "(e.g. pass=0 / fail=1). Measures how well a continuous parameter "
                       "separates the two classes. Mathematically equivalent to Pearson r on 0/1 target."),
    "pcm_wafer": (
        "<b>PCM Wafer-Level Correlation</b><br><br>"
        "<b>Same idea as Lot-Level, but each data point is one wafer.</b><br>"
        "Groups the merged data by (lot, wafer). For each wafer: "
        "<i>median PCM value across all dies on that wafer</i> vs "
        "<i>% dies hitting the target bin on that wafer</i>. "
        "Pearson r is then computed across all wafers.<br><br>"
        "<b>When is this more powerful than Lot-Level?</b><br>"
        "If IDW interpolation was applied during PCM merge, each die received a "
        "position-interpolated PCM value — meaning wafer-to-wafer variation within a lot "
        "is preserved. Wafer-level correlation can then detect within-lot wafer effects.<br>"
        "If only lot-median was used (no IDW), every wafer in a lot has the same PCM value; "
        "wafer-level n is larger but adds no new PCM information beyond lot-level.<br><br>"
        "<b>n</b> = number of (lot, wafer) pairs with at least one tested die. "
        "r and p-value interpretation is the same as Lot-Level."
    ),
    "pcm_dev": (
        "<b>PCM Deviation Correlation</b><br><br>"
        "<b>What it detects:</b> non-monotonic (U-shaped or inverted-U) relationships between a PCM "
        "parameter and yield. Pearson r only detects <i>linear</i> trends — if both very low AND very "
        "high values of a parameter cause failures, Pearson r ≈ 0 and the parameter gets buried. "
        "This method transforms each lot’s PCM value to its <b>absolute deviation from the lot-population median</b>: "
        "<code>|x − median(x)|</code>, then correlates <i>that</i> with % target-bin hits per lot.<br><br>"
        "<b>How to read:</b><br>"
        "r &gt; 0 → lots far from the typical process point (high or low) have more bin hits. "
        "This is the classic \u2018process window\u2019 signature: extreme process causes failures.<br>"
        "r &lt; 0 → lots close to the typical process point have more failures — unusual but can "
        "happen if the target bin is a marginal pass that only triggers at nominal conditions.<br><br>"
        "<b>Note:</b> this method uses lot-level aggregation (same n as PCM Lot-Level). "
        "It is complementary to Lot-Level, not a replacement."
    ),
    "pcm_wdev": (
        "<b>PCM Deviation (Wafer) — Within-Lot Wafer Analysis</b><br><br>"
        "<b>Goal:</b> find <i>which specific wafers</i> are bad, and <i>why</i> — by comparing each "
        "wafer to <b>its own lot\u2019s center</b> (not the global population). This removes lot-to-lot "
        "offsets so a genuinely-anomalous wafer stands out against its siblings.<br><br>"
        "<b>How it is computed (two medians — read carefully):</b><br>"
        "1. <b>Wafer value</b> = <b>median PCM across the dies on that wafer</b> "
        "(one median <i>per wafer</i>; median is robust to die outliers).<br>"
        "2. <b>Lot center</b> = <b>median of the wafer-values within that lot</b> "
        "(one median <i>per lot</i> — the median of ~N wafer medians, <i>not</i> of all dies).<br>"
        "3. <b>Deviation</b> = <code>|wafer value &minus; lot center|</code> for each wafer.<br>"
        "4. Pearson r correlates that deviation vs the wafer\u2019s % target-bin hits, pooled over all wafers.<br>"
        "5. Lots with fewer than <b>4 wafers</b> are excluded (lot center too shaky).<br><br>"
        "<b>Second metric — within-wafer spread:</b> alongside the median, we also correlate each "
        "wafer\u2019s <b>die-to-die std of PCM</b> (how <i>non-uniform</i> the wafer is) vs its % fail. "
        "This is shown per row as <b>spread r</b>. Interpretation:<br>"
        "&bull; high <b>deviation r</b> &rarr; wafers whose <i>center drifted</i> from their lot fail more.<br>"
        "&bull; high <b>spread r</b> &rarr; wafers that are <i>internally noisy</i> (edge-hot / center-cold) "
        "fail more, even if centered.<br><br>"
        "<b>Requires IDW:</b> per-die PCM variation must exist. Lots filled by a single lot-median "
        "(no IDW) contribute no within-wafer variation and drop out. The <i>IDW N/total</i> line in the "
        "run log tells you how many lots qualify.<br>"
        "<b>Complements</b> PCM Deviation (Lot): Lot catches population-centering; Wafer catches "
        "outlier wafers within a lot."
    ),
    "pcm_lot": (
        "<b>PCM Lot-Level Correlation</b><br><br>"
        "<b>Why lot-level?</b><br>"
        "PCM (Process Control Monitor) measurements are taken at fixed wafer sites before die singulation. "
        "Every die in the same lot shares the same PCM values — there is no within-lot variation to exploit. "
        "Using die-level methods (Pearson, Spearman, …) on PCM data would inflate <i>n</i> artificially "
        "and produce misleadingly high confidence, because the same PCM value is repeated thousands of times "
        "for every die in that lot. "
        "The correct unit of analysis is the <b>lot</b>.<br><br>"
        "<b>How it is computed</b><br>"
        "1. For each lot, compute the <b>median</b> of the PCM parameter across all measurement sites "
        "(median is used instead of mean to suppress outlier probe sites).<br>"
        "2. For the same lot, compute the <b>% of dies hitting the target bin</b> "
        "(e.g. % of dies with FB == 802 out of all tested dies in that lot).<br>"
        "3. These two vectors — one value per lot — are correlated with <b>Pearson r</b>.<br>"
        "4. A p-value is reported so you can judge statistical significance given the small sample.<br><br>"
        "<b>Reading the result</b><br>"
        "r &gt; 0 → higher PCM value → more bin hits (parameter drives failures up).<br>"
        "r &lt; 0 → higher PCM value → fewer bin hits (parameter suppresses failures).<br>"
        "Treat any result with <i>n</i> &lt; 8 lots cautiously — Pearson r is unreliable on tiny samples.<br><br>"
        "<b>Low n</b> means fewer lots had both PCM data <i>and</i> sort data available. "
        "Missing PCM files, lot-ID mismatches, or lots run before PCM tracking started are common causes. "
        "Check the log after Discover to see which lots were matched."
    ),
    "methods_structural": (
        "<b>Spatial / Radial Analysis</b><br><br>"
        "Wafer-geometry covariates \u2014 <b>not</b> device electrical measurements.<br><br>"
        "A non-zero correlation here means failures track a spatial pattern: "
        "wafer edge, reticle shot, litho focus, CMP, or chuck/clamp effects. "
        "Investigate radial/edge process uniformity, not a device parameter shift.<br><br>"
        "p-values here are <b>uncorrected</b>. These fields are analyzed separately from "
        "electrical parameters and share no FDR q pool with Pearson/Spearman."
    ),
}

# --- backend helpers ---

def _walk_for_pcm(root: str):
    """Yield (fname, fpath) for every CSV that may contain PCM data.
    Walks zip archives and loose files alike; accepts any .csv name."""
    if not os.path.isdir(root):
        return
    for dirpath, _dirs, files in sorted(os.walk(root)):  # sorted for determinism
        _dirs[:] = sorted(_dirs)                          # visit subdirs in alpha order
        for fname in sorted(files):
            full = os.path.join(dirpath, fname)
            if fname.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(full, "r") as zf:
                        for member in sorted(zf.namelist()):  # sorted for determinism
                            if member.endswith("/"):
                                continue
                            mfname = member.rsplit("/", 1)[-1]
                            if mfname.lower().endswith(".csv"):
                                yield mfname, full + ZIP_SEP + member
                except Exception:
                    pass
            elif fname.lower().endswith(".csv"):
                yield fname, full

def _read_pcm_for_lot(zip_path: str, sort_lot: str):
    """Open zip_path, read every CSV inside, return rows where Lot[:7] == sort_lot[:7].
    Accepts a plain CSV file path as well (no zip)."""
    prefix7 = str(sort_lot)[:7]
    frames = []
    if zip_path.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in sorted(zf.namelist()):
                    if member.endswith("/") or not member.lower().endswith(".csv"):
                        continue
                    try:
                        with zf.open(member) as f:
                            df = pd.read_csv(f, low_memory=False)
                        if "Lot" in df.columns:
                            df = df[df["Lot"].astype(str).str[:7] == prefix7].copy()
                        if len(df):
                            frames.append(df)
                    except Exception:
                        pass
        except Exception:
            pass
    else:
        try:
            df = pd.read_csv(zip_path, low_memory=False)
            if "Lot" in df.columns:
                df = df[df["Lot"].astype(str).str[:7] == prefix7].copy()
            if len(df):
                frames.append(df)
        except Exception:
            pass
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _read_zip_grouped(zip_path: str):
    """Read every CSV in a zip (or a loose CSV) ONCE and return a dict
    {lot7: DataFrame} grouped by the first 7 chars of the Lot column.
    Lets callers serve many lots from a single parse instead of re-reading
    the whole archive per lot (huge win when 1 zip holds many lots)."""
    frames = []
    if zip_path.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in sorted(zf.namelist()):
                    if member.endswith("/") or not member.lower().endswith(".csv"):
                        continue
                    try:
                        with zf.open(member) as f:
                            frames.append(pd.read_csv(f, low_memory=False))
                    except Exception:
                        pass
        except Exception:
            return {}
    else:
        try:
            frames.append(pd.read_csv(zip_path, low_memory=False))
        except Exception:
            return {}
    if not frames:
        return {}
    big = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    if "Lot" not in big.columns:
        return {"": big}
    lot7 = big["Lot"].astype(str).str[:7]
    return {k: g.copy() for k, g in big.groupby(lot7)}


def find_pcm_for_lots(lots, devrevstep_prefix: str = ""):
    """Match sort lots to PCM etest files."""
    prefix_up = devrevstep_prefix[:6].upper() if devrevstep_prefix else ""
    lot_set = {str(l) for l in lots}
    lot7_map: dict = {}
    for lot in lot_set:
        lot7_map.setdefault(str(lot)[:7], []).append(lot)

    found: dict = {}
    tier_dirs = [(True, _ETEST_FULL_ROOTS), (False, _ETEST_9_ROOTS)]
    scanned_zips: set = set()

    for is_full, search_dirs in tier_dirs:
        for root in search_dirs:
            for _fname, fpath in _walk_for_pcm(root):
                zip_path = fpath.split(ZIP_SEP)[0] if ZIP_SEP in fpath else fpath
                if zip_path in scanned_zips:
                    continue
                scanned_zips.add(zip_path)
                try:
                    if zip_path.lower().endswith(".zip"):
                        with zipfile.ZipFile(zip_path, "r") as zf:
                            lot_vals = set()
                            for member in zf.namelist():
                                if member.endswith("/") or not member.lower().endswith(".csv"):
                                    continue
                                try:
                                    with zf.open(member) as f:
                                        hdr = list(pd.read_csv(f, nrows=0).columns)
                                    read_cols = [c for c in ["Lot", "Layout"] if c in hdr]
                                    with zf.open(member) as f:
                                        chunk = pd.read_csv(f, usecols=read_cols,
                                                            nrows=5, low_memory=False)
                                    if prefix_up and "Layout" in chunk.columns:
                                        lv = chunk["Layout"].dropna()
                                        if not lv.empty and str(lv.iloc[0])[:6].upper() != prefix_up:
                                            break  # wrong device — skip whole zip
                                    with zf.open(member) as f:
                                        lot_df = pd.read_csv(f, usecols=["Lot"], low_memory=False)
                                    lot_vals.update(lot_df["Lot"].dropna().unique().tolist())
                                except Exception:
                                    pass
                    else:
                        hdr = list(pd.read_csv(zip_path, nrows=0).columns)
                        read_cols = [c for c in ["Lot", "Layout"] if c in hdr]
                        chunk = pd.read_csv(zip_path, usecols=read_cols, nrows=5, low_memory=False)
                        if prefix_up and "Layout" in chunk.columns:
                            lv = chunk["Layout"].dropna()
                            if not lv.empty and str(lv.iloc[0])[:6].upper() != prefix_up:
                                continue
                        full = pd.read_csv(zip_path, usecols=["Lot"], low_memory=False)
                        lot_vals = set(full["Lot"].dropna().unique().tolist()) if "Lot" in full.columns else set()
                except Exception:
                    continue

                for lot_val in lot_vals:
                    p7 = str(lot_val)[:7]
                    for sort_lot in lot7_map.get(p7, []):
                        found.setdefault(sort_lot, []).append((zip_path, is_full, str(lot_val)))

    # Tier pruning: if a lot has any full-site file, drop non-full-site entries
    for sort_lot in found:
        entries = found[sort_lot]
        if any(is_f for _, is_f, _ in entries):
            found[sort_lot] = [(zp, isf, lid) for zp, isf, lid in entries if isf]
    return found



def _idw_interpolate(site_xy, site_vals, query_xy, power=2):
    """Inverse-Distance Weighting: site_xy (N,2), site_vals (N,P), query_xy (M,2) → (M,P)."""
    EPS = 1e-9
    diff = query_xy[:, np.newaxis, :] - site_xy[np.newaxis, :, :]
    dist = np.sqrt((diff ** 2).sum(axis=2))          # (M, N)
    exact = dist == 0
    w  = 1.0 / (dist ** power + EPS)
    nw = w / (w.sum(axis=1, keepdims=True) + EPS)
    result = nw @ site_vals
    for i in range(len(query_xy)):                    # honour exact hits
        hits = np.where(exact[i])[0]
        if len(hits):
            result[i] = site_vals[hits[0]]
    return result


def _load_reticle_map(layout: str, reticle_dir: str):
    """Find and load the reticle map CSV for a given layout string.
    Adds SORT_X/SORT_Y columns (centred DieX/DieY) if not already present."""
    if not os.path.isdir(reticle_dir):
        return None
    layout_up = layout.upper()
    for fname in os.listdir(reticle_dir):
        if layout_up in fname.upper() and fname.lower().endswith(".csv"):
            df_m = pd.read_csv(os.path.join(reticle_dir, fname))
            if "SORT_X" not in df_m.columns or "SORT_Y" not in df_m.columns:
                xm = (df_m["DieX"].max() + df_m["DieX"].min()) / 2
                ym = (df_m["DieY"].max() + df_m["DieY"].min()) / 2
                df_m["SORT_X"] = (df_m["DieX"] - xm).round().astype(int)
                df_m["SORT_Y"] = (df_m["DieY"] - ym).round().astype(int)
            return df_m
    return None


def _pearson_p(r: float, n: int) -> float | None:
    """Two-sided p-value for Pearson r using t-distribution."""
    import math
    if n <= 2 or math.isnan(r): return None
    t_stat = r * math.sqrt(n - 2) / math.sqrt(max(1e-15, 1 - r * r))
    from scipy.stats import t as t_dist
    return float(2 * t_dist.sf(abs(t_stat), df=n - 2))


def _apply_idw_pcm(lot_sort_df, df_pcm, avail_params, df_rmap, idw_power=2):
    """Apply IDW from PCM site positions (LayoutX/Y) to die positions (SORT_X/Y).
    Returns a DataFrame indexed like lot_sort_df with interpolated PCM values,
    or None if pre-conditions are not met."""
    if "SORT_X" not in lot_sort_df.columns or "SORT_Y" not in lot_sort_df.columns:
        return None
    if "LayoutX" not in df_pcm.columns or "LayoutY" not in df_pcm.columns:
        return None

    # Average PCM across wafers at each (LayoutX, LayoutY) site
    site_mean = (df_pcm.groupby(["LayoutX", "LayoutY"])[avail_params]
                 .mean().reset_index())
    if len(site_mean) < 2:
        return None
    site_xy   = site_mean[["LayoutX", "LayoutY"]].values.astype(float)
    site_vals = site_mean[avail_params].values.astype(float)

    # Build SORT_X/Y → LayoutX/Y lookup from reticle map
    s2l = (df_rmap[["SORT_X", "SORT_Y", "LayoutX", "LayoutY"]]
           .drop_duplicates(subset=["SORT_X", "SORT_Y"]))
    # Merge die coords → layout coords
    die_lxy = lot_sort_df[["SORT_X", "SORT_Y"]].merge(
        s2l, on=["SORT_X", "SORT_Y"], how="left")
    query_xy = die_lxy[["LayoutX", "LayoutY"]].values.astype(float)

    # Replace NaN query positions with centroid of known site positions
    nan_mask = np.isnan(query_xy).any(axis=1)
    if nan_mask.all():
        return None                                     # no mapping at all
    centroid = np.nanmean(query_xy, axis=0)
    query_xy[nan_mask] = centroid

    interp = _idw_interpolate(site_xy, site_vals, query_xy, power=idw_power)
    result  = pd.DataFrame(interp, columns=avail_params, index=lot_sort_df.index)
    return result


def _etest_cols_from_pcm(df_pcm):
    """Return numeric column names that appear after 'LayoutY' in a PCM dataframe.
    Falls back to all numeric columns if LayoutY is not present."""
    cols = list(df_pcm.columns)
    if "LayoutY" in cols:
        start = cols.index("LayoutY") + 1
        cols = cols[start:]
    return [c for c in cols if pd.api.types.is_numeric_dtype(df_pcm[c])]

def _detect_ib_col(columns):
    return (next((c for c in columns if c == "IB"), None) or
            next((c for c in columns if "INTERFACE_BIN" in c.upper()
                  and "TOTAL" not in c.upper()), None))

def _detect_fb_col(columns):
    return (next((c for c in columns if c == "FB"), None) or
            next((c for c in columns if "FUNCTIONAL_BIN" in c.upper()
                  and "TOTAL" not in c.upper()), None))

def _detect_devrevstep_col(columns):
    return next((c for c in columns if "DEVREVSTEP" in c.upper()), None)

def _read_csv_header(path):
    return list(pd.read_csv(path, nrows=0, low_memory=False).columns)


# --- CheckableComboBox ---

class CheckableComboBox(tk.Frame):
    """Button that opens a Toplevel with Checkbuttons."""

    def __init__(self, master, placeholder="Select...", **kw):
        super().__init__(master, bg=BG3, **kw)
        self._placeholder = placeholder
        self._vars = []   # list of (text, BooleanVar)
        self._popup = None

        self._btn = tk.Button(
            self, text=placeholder, anchor="w", relief="flat", bd=0,
            bg=BG3, fg=FG_DIM, activebackground=BG2, activeforeground=FG,
            font=FONT_SM, cursor="hand2",
            highlightbackground=BORDER, highlightthickness=1,
            command=self._toggle)
        self._btn.pack(fill="x", expand=True, ipady=3, ipadx=6)

    def populate(self, items, checked_all=True):
        self._vars = [(item, tk.BooleanVar(value=checked_all)) for item in items]
        for _, var in self._vars:
            var.trace_add("write", lambda *_: self._update_label())
        self._update_label()

    def _toggle(self):
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()
            self._popup = None
            return
        self._open_popup()

    def _open_popup(self):
        self._popup = tk.Toplevel(self)
        self._popup.overrideredirect(True)
        self._popup.configure(bg=BG3)
        self._popup.attributes("-topmost", True)
        self.update_idletasks()
        x = self._btn.winfo_rootx()
        y = self._btn.winfo_rooty() + self._btn.winfo_height()
        w = max(self._btn.winfo_width(), 240)
        frame = tk.Frame(self._popup, bg=BG3,
                         highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(frame, bg=BG3, bd=0, highlightthickness=0)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG3)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        for text, var in self._vars:
            cb = tk.Checkbutton(inner, text=text, variable=var,
                                bg=BG3, fg=FG, activebackground=BG2,
                                selectcolor=BG2, font=FONT_SM,
                                anchor="w", relief="flat")
            cb.pack(fill="x", padx=4, pady=1)
        inner.update_idletasks()
        h = min(inner.winfo_reqheight() + 4, 300)
        self._popup.geometry(f"{w}x{h}+{x}+{y}")
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfig(win_id, width=w - 18)
        self.winfo_toplevel().bind("<Button-1>", self._on_root_click, add=True)

    def _on_root_click(self, event):
        if self._popup and self._popup.winfo_exists():
            wx, wy = self._popup.winfo_rootx(), self._popup.winfo_rooty()
            ww, wh = self._popup.winfo_width(), self._popup.winfo_height()
            if not (wx <= event.x_root <= wx + ww and wy <= event.y_root <= wy + wh):
                bx, by = self._btn.winfo_rootx(), self._btn.winfo_rooty()
                bw, bh = self._btn.winfo_width(), self._btn.winfo_height()
                if not (bx <= event.x_root <= bx + bw and by <= event.y_root <= by + bh):
                    self._popup.destroy()
                    self._popup = None

    def _update_label(self):
        checked = self.checkedItems()
        total = len(self._vars)
        if not total:
            self._btn.config(text=self._placeholder, fg=FG_DIM)
        elif len(checked) == total:
            self._btn.config(text=f"All ({total})", fg=GREEN)
        elif not checked:
            self._btn.config(text="None", fg=RED)
        else:
            self._btn.config(text=f"{len(checked)} / {total}", fg=BLUE)

    def checkedItems(self):
        return [text for text, var in self._vars if var.get()]

    def setAllChecked(self, val):
        for _, var in self._vars:
            var.set(val)
        self._update_label()


# --- BinPicker ---

class BinPicker(tk.Frame):
    """Button + floating Toplevel with IB->FB hierarchy checkboxes."""

    def __init__(self, master, placeholder="Select bins...", **kw):
        super().__init__(master, bg=BG3, **kw)
        self._placeholder = placeholder
        self._popup = None
        self._data = {}   # {ib: {'var': BooleanVar, 'fbs': {fb: BooleanVar}}}

        self._btn = tk.Button(
            self, text=placeholder, anchor="w", relief="flat", bd=0,
            bg=BG3, fg=FG_DIM, activebackground=BG2, activeforeground=FG,
            font=FONT_SM, cursor="hand2",
            highlightbackground=BORDER, highlightthickness=1,
            command=self._toggle)
        self._btn.pack(fill="x", expand=True, ipady=3, ipadx=6)

    def populate(self, ib_fbs):
        self._data = {}
        for ib in sorted(ib_fbs.keys()):
            ib_var = tk.IntVar(value=1)  # 0=none, 1=all, 2=partial
            fb_dict = {}
            for fb in sorted(ib_fbs.get(ib, [])):
                fb_var = tk.BooleanVar(value=True)
                fb_var.trace_add("write", lambda *_, _ib=ib: self._on_fb_changed(_ib))
                fb_dict[fb] = fb_var
            ib_var.trace_add("write", lambda *_, _ib=ib: self._on_ib_changed(_ib))
            self._data[ib] = {"var": ib_var, "fbs": fb_dict}
        self._update_label()

    def _toggle(self):
        if self._popup and self._popup.winfo_exists():
            self._destroy_popup()
            return
        self._open_popup()

    def _open_popup(self):
        self._popup = tk.Toplevel(self)
        self._popup.overrideredirect(True)
        self._popup.configure(bg=BG3)
        self._popup.attributes("-topmost", True)
        self.update_idletasks()
        x = self._btn.winfo_rootx()
        y = self._btn.winfo_rooty() + self._btn.winfo_height()
        w = max(self._btn.winfo_width(), 260)

        frame = tk.Frame(self._popup, bg=BG3,
                         highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(frame, bg=BG3, bd=0, highlightthickness=0)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG3)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        self._ib_expanded = {}

        def _refresh_popup_size():
            inner.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(win_id, width=w - 18)
            h = min(inner.winfo_reqheight() + 4, 400)
            self._popup.geometry(f"{w}x{h}+{x}+{y}")

        for ib, idata in self._data.items():
            ib_var = idata["var"]
            fb_dict = idata["fbs"]
            self._ib_expanded[ib] = False

            ib_row = tk.Frame(inner, bg=BG3)
            ib_row.pack(fill="x", padx=4, pady=(4, 0))

            arrow_lbl = tk.Label(ib_row, text="▶", bg=BG3, fg=FG_DIM,
                                 font=("Segoe UI", 8), cursor="hand2", width=2)
            arrow_lbl.pack(side="left")

            ib_cb = tk.Checkbutton(ib_row, variable=ib_var,
                                   text=f"IB {ib}  ({len(fb_dict)} FB)",
                                   bg=BG3, fg=BLUE, activebackground=BG2,
                                   selectcolor=BG2, font=("Segoe UI", 9, "bold"),
                                   anchor="w", relief="flat", tristatevalue=2)
            ib_cb.pack(side="left", fill="x", expand=True)

            sub = tk.Frame(inner, bg=BG3)
            for fb, fb_var in fb_dict.items():
                fb_cb = tk.Checkbutton(sub, variable=fb_var,
                                       text=f"FB {fb}",
                                       bg=BG3, fg=GREEN, activebackground=BG2,
                                       selectcolor=BG2, font=FONT_CO,
                                       anchor="w", relief="flat")
                fb_cb.pack(fill="x")

            def make_toggle(ib=ib, sub=sub, arrow=arrow_lbl):
                def _toggle(event=None):
                    if self._ib_expanded[ib]:
                        sub.pack_forget()
                        arrow.config(text="▶")
                        self._ib_expanded[ib] = False
                    else:
                        sub.pack(fill="x", padx=20)
                        arrow.config(text="▼")
                        self._ib_expanded[ib] = True
                    _refresh_popup_size()
                return _toggle

            toggle_fn = make_toggle()
            arrow_lbl.bind("<Button-1>", toggle_fn)

        inner.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfig(win_id, width=w - 18)
        h = min(inner.winfo_reqheight() + 4, 400)
        self._popup.geometry(f"{w}x{h}+{x}+{y}")

        # enable mousewheel scrolling inside popup
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        self._popup._mw_unbind = lambda: canvas.unbind_all("<MouseWheel>")

        self.winfo_toplevel().bind("<Button-1>", self._on_root_click, add=True)
        # also catch clicks anywhere on screen via the popup's focus-out
        self._popup.bind("<FocusOut>", lambda e: self._close_popup_if_outside())

    def _close_popup_if_outside(self):
        if self._popup and self._popup.winfo_exists():
            # give focus a moment to settle before deciding
            self._popup.after(50, self._check_focus_gone)

    def _check_focus_gone(self):
        if self._popup and self._popup.winfo_exists():
            focused = self._popup.focus_get()
            if focused is None:
                self._destroy_popup()

    def _destroy_popup(self):
        if self._popup and self._popup.winfo_exists():
            if hasattr(self._popup, '_mw_unbind'):
                try:
                    self._popup._mw_unbind()
                except Exception:
                    pass
            self._popup.destroy()
        self._popup = None

    def _on_root_click(self, event):
        if self._popup and self._popup.winfo_exists():
            wx, wy = self._popup.winfo_rootx(), self._popup.winfo_rooty()
            ww, wh = self._popup.winfo_width(), self._popup.winfo_height()
            if not (wx <= event.x_root <= wx + ww and wy <= event.y_root <= wy + wh):
                bx, by = self._btn.winfo_rootx(), self._btn.winfo_rooty()
                bw, bh = self._btn.winfo_width(), self._btn.winfo_height()
                if not (bx <= event.x_root <= bx + bw and by <= event.y_root <= by + bh):
                    self._destroy_popup()

    def _on_ib_changed(self, ib):
        if getattr(self, "_fb_updating", False):
            return
        val = self._data[ib]["var"].get()
        if val == 2:
            # tristate click: treat as select-all
            self._data[ib]["var"].set(1)
            return
        for fb_var in self._data[ib]["fbs"].values():
            fb_var.set(bool(val))
        self._update_label()

    def _on_fb_changed(self, ib):
        fbs = self._data[ib]["fbs"]
        n_checked = sum(1 for v in fbs.values() if v.get())
        new_state = 1 if n_checked == len(fbs) else (2 if n_checked > 0 else 0)
        self._fb_updating = True
        self._data[ib]["var"].set(new_state)
        self._fb_updating = False
        self._update_label()

    def _update_label(self):
        ibs = self.checkedIBs()
        fbs = self.checkedFBs()
        if not ibs and not fbs:
            self._btn.config(text="None selected", fg=RED)
        else:
            self._btn.config(text=f"{len(ibs)} IB  >  {len(fbs)} FB checked", fg=BLUE)

    def checkedIBs(self):
        return [ib for ib, d in self._data.items() if d["var"].get() > 0]

    def checkedFBs(self):
        result = []
        for d in self._data.values():
            result.extend(fb for fb, var in d["fbs"].items() if var.get())
        return result

    def checkedFBsForIB(self, ib):
        if ib not in self._data:
            return []
        return [fb for fb, var in self._data[ib]["fbs"].items() if var.get()]

    def setAllChecked(self, val):
        for idata in self._data.values():
            idata["var"].set(1 if val else 0)
            for fb_var in idata["fbs"].values():
                fb_var.set(val)
        self._update_label()

    def get_state(self):
        """Return serialisable dict of checked IB/FB state."""
        return {
            str(ib): {
                "checked": int(d["var"].get()),
                "fbs": {str(fb): bool(v.get()) for fb, v in d["fbs"].items()}
            }
            for ib, d in self._data.items()
        }

    def set_state(self, state):
        """Restore checked state from dict produced by get_state()."""
        for ib, d in self._data.items():
            s = state.get(str(ib))
            if s is None:
                continue
            d["var"].set(int(s.get("checked", 1)))
            for fb, fb_var in d["fbs"].items():
                fb_var.set(bool(s.get("fbs", {}).get(str(fb), True)))
        self._update_label()


# --- CheckableTree ---

class CheckableTree(ttk.Treeview):
    """ttk.Treeview with simulated checkboxes via text prefixes."""
    CHECK   = "☑"
    UNCHECK = "☐"

    def __init__(self, master, on_change=None, **kw):
        kw.setdefault("show", "tree")
        kw.setdefault("selectmode", "none")
        super().__init__(master, **kw)
        self._on_change = on_change
        self.bind("<Button-1>", self._on_click)

    def add_group(self, label, cols, color_tag, expand=True):
        grp_text = f"{self.CHECK} {label}  ({len(cols)})"
        grp = self.insert("", "end", text=grp_text, open=expand,
                          tags=(color_tag, "group"))
        for col in cols:
            short = col
            if col.startswith(label + "_"):
                short = col[len(label)+1:]
            short = re.sub(r"_\d{5,6}$", "", short)
            leaf_text = f"{self.CHECK} {short}"
            self.insert(grp, "end", text=leaf_text,
                        values=(col,), tags=(color_tag, "leaf"))
        return grp

    def add_separator(self, label):
        self.insert("", "end", text=f"-- {label} --", tags=("sep",))

    def clear(self):
        self.delete(*self.get_children())

    def checked_cols(self):
        result = []
        for grp in self.get_children():
            for child in self.get_children(grp):
                if self._is_leaf(child) and self._is_checked(child):
                    vals = self.item(child, "values")
                    if vals:
                        result.append(vals[0])
        return result

    def set_all(self, val):
        mark = self.CHECK if val else self.UNCHECK
        for grp in self.get_children():
            for child in self.get_children(grp):
                if self._is_leaf(child):
                    old = self.item(child, "text")
                    self.item(child, text=mark + old[1:])
            self._refresh_group(grp)
        if self._on_change:
            self._on_change()

    def set_checked_cols(self, cols_set):
        """Check only the columns in cols_set, uncheck everything else."""
        for grp in self.get_children():
            for child in self.get_children(grp):
                if not self._is_leaf(child):
                    continue
                vals = self.item(child, "values")
                col = vals[0] if vals else None
                want = col in cols_set if col else False
                mark = self.CHECK if want else self.UNCHECK
                old = self.item(child, "text")
                self.item(child, text=mark + old[1:])
            self._refresh_group(grp)
        if self._on_change:
            self._on_change()

    def expand_all(self, val):
        for grp in self.get_children():
            self.item(grp, open=val)

    def _is_leaf(self, iid):
        return "leaf" in self.item(iid, "tags")

    def _is_checked(self, iid):
        return self.item(iid, "text").startswith(self.CHECK)

    def _on_click(self, event):
        region = self.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            return
        # Let the native disclosure arrow collapse/expand without intercepting
        if self.identify_element(event.x, event.y) == "Treeitem.indicator":
            return
        iid = self.identify_row(event.y)
        if not iid:
            return
        tags = self.item(iid, "tags")
        if "sep" in tags:
            return
        if "group" in tags:
            if not self.item(iid, "open"):
                # expand collapsed group so user can select individual leaves
                self.item(iid, open=True)
            else:
                self._toggle_group(iid)
                if self._on_change:
                    self._on_change()
            return
        elif "leaf" in tags:
            self._toggle_leaf(iid)
            self._refresh_group(self.parent(iid))
        if self._on_change:
            self._on_change()

    def _toggle_leaf(self, iid):
        old = self.item(iid, "text")
        if old.startswith(self.CHECK):
            self.item(iid, text=self.UNCHECK + old[1:])
        else:
            self.item(iid, text=self.CHECK + old[1:])

    def _toggle_group(self, grp):
        leaves = [c for c in self.get_children(grp) if self._is_leaf(c)]
        all_checked = all(self._is_checked(c) for c in leaves)
        target = self.UNCHECK if all_checked else self.CHECK
        for child in leaves:
            old = self.item(child, "text")
            self.item(child, text=target + old[1:])
        self._refresh_group(grp)

    def _refresh_group(self, grp):
        if not grp:
            return
        leaves = [c for c in self.get_children(grp) if self._is_leaf(c)]
        if not leaves:
            return
        n_checked = sum(1 for c in leaves if self._is_checked(c))
        old_text = self.item(grp, "text")
        rest = old_text[1:] if old_text and old_text[0] in (self.CHECK, self.UNCHECK, "\u25d1") else old_text
        if n_checked == len(leaves):
            self.item(grp, text=self.CHECK + rest)
        elif n_checked == 0:
            self.item(grp, text=self.UNCHECK + rest)
        else:
            self.item(grp, text="\u25d1" + rest)


# --- Main App ---

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sort + Etest Correlation")
        self.geometry("1140x1020")
        self.minsize(900, 800)
        self.configure(bg=BG)

        self._csv_paths       = []   # list of file paths
        self._csv_path        = ""   # kept for compat (first path)
        self._devrevstep_prefix = ""  # e.g. '8PF5CV'
        self._header_cols     = []
        self._pcm_map         = {}
        self._all_params      = []
        self._pcm_cols_discovered = set()
        self._pcm_idw_cols        = set()   # PCM cols that got per-die IDW in last merge
        self._idw_coverage        = {"idw_lots": 0, "median_lots": 0, "pcm_lots": 0}
        self._last_report     = None
        self._last_report_path = None
        self._ib_fbs          = {}
        self._ib_col          = ""
        self._fb_col          = ""
        self._pending_preset  = None
        self._last_dir        = self._load_last_dir()
        self._auto_discover_after_header = False
        self._out_var         = tk.StringVar()

        self._apply_styles()
        self._build_ui()

    def _apply_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".",            background=BG,  foreground=FG,   font=FONT_SM)
        style.configure("TFrame",       background=BG)
        style.configure("TLabel",       background=BG,  foreground=FG)
        style.configure("TLabelframe",  background=BG,  bordercolor=BORDER)
        style.configure("TLabelframe.Label", foreground=BLUE, background=BG,
                        font=("Segoe UI", 9, "bold"))
        style.configure("TScrollbar",   background=BG3, troughcolor=BG2, arrowcolor=FG_DIM)
        style.configure("TPanedwindow", background=BORDER)
        style.configure("Treeview",     background=BG2, foreground=FG,
                        fieldbackground=BG2, bordercolor=BORDER, rowheight=20)
        style.configure("Treeview.Heading", background=BG3, foreground=BLUE)
        style.map("Treeview", background=[("selected", "#1a5276")])
        style.configure("TProgressbar", troughcolor=BG2, background=BLUE)

    def _btn(self, parent, text, cmd, color=FG, bg=BG3):
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=color, activebackground=BG2, activeforeground=color,
                         relief="flat", bd=0, font=FONT_SM, padx=10, pady=4,
                         cursor="hand2",
                         highlightbackground=BORDER, highlightthickness=1)

    def _entry(self, parent, textvariable=None, width=None):
        kw = dict(bg=BG3, fg=FG, insertbackground=FG, relief="flat", bd=0,
                  font=FONT_SM, highlightbackground=BORDER, highlightthickness=1)
        if textvariable:
            kw["textvariable"] = textvariable
        if width:
            kw["width"] = width
        return tk.Entry(parent, **kw)

    def _lbl(self, parent, text, color=FG):
        return tk.Label(parent, text=text, bg=BG, fg=color, font=FONT_SM)

    # --- UI ---

    def _build_ui(self):
        # scrollable shell so all controls are reachable on any display height
        _shell = tk.Frame(self, bg=BG)
        _shell.pack(fill="both", expand=True)
        _vsb = ttk.Scrollbar(_shell, orient="vertical")
        _vsb.pack(side="right", fill="y")
        _canvas = tk.Canvas(_shell, bg=BG, bd=0, highlightthickness=0,
                            yscrollcommand=_vsb.set)
        _canvas.pack(side="left", fill="both", expand=True)
        _vsb.configure(command=_canvas.yview)
        outer = tk.Frame(_canvas, bg=BG, padx=10, pady=8)
        _win = _canvas.create_window((0, 0), window=outer, anchor="nw")
        def _on_outer_cfg(e):
            _canvas.configure(scrollregion=_canvas.bbox("all"))
        def _on_canvas_cfg(e):
            _canvas.itemconfig(_win, width=e.width)
        outer.bind("<Configure>", _on_outer_cfg)
        _canvas.bind("<Configure>", _on_canvas_cfg)
        _canvas.bind_all("<MouseWheel>",
                         lambda e: _canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        # Step 1
        g1 = ttk.LabelFrame(outer, text="Step 1 -- Load Sort CSV / ZIP / GZ  (one or more files)")
        g1.pack(fill="x", pady=(0, 6))
        # File listbox
        lb_frame = tk.Frame(g1, bg=BG)
        lb_frame.pack(fill="x", padx=8, pady=(4, 0))
        self._csv_listbox = tk.Listbox(lb_frame, bg=BG3, fg=FG, selectbackground="#1a5276",
                                       font=FONT_SM, height=4, selectmode=tk.EXTENDED)
        self._csv_listbox.pack(side="left", fill="x", expand=True)
        sb = tk.Scrollbar(lb_frame, orient="vertical", command=self._csv_listbox.yview)
        sb.pack(side="left", fill="y")
        self._csv_listbox.configure(yscrollcommand=sb.set)
        btn_frame = tk.Frame(g1, bg=BG)
        btn_frame.pack(fill="x", padx=8, pady=2)
        self._btn(btn_frame, "Add Files...", self._browse_csv, BLUE).pack(side="left", padx=2)
        self._btn(btn_frame, "Remove Selected", self._remove_csv_files, ORANGE).pack(side="left", padx=2)
        self._btn(btn_frame, "Clear All", self._clear_csv_files, FG_DIM).pack(side="left", padx=2)
        self._btn_load_hdr = self._btn(btn_frame, "Load Header ->", self._load_header, GREEN)
        self._btn_load_hdr.pack(side="left", padx=8)
        self._lbl_load = tk.Label(g1, text="No file loaded.", bg=BG, fg=FG_DIM, font=FONT_SM)
        self._lbl_load.pack(anchor="w", padx=8, pady=(0, 4))
        # keep _csv_var for preset compat
        self._csv_var = tk.StringVar()

        # Step 2
        g2 = ttk.LabelFrame(outer, text="Step 2 -- Etest Sources")
        g2.pack(fill="x", pady=(0, 6))
        self._chk_upm = tk.BooleanVar(value=True)
        self._chk_pcm = tk.BooleanVar(value=True)
        tk.Checkbutton(g2, text="UPM / SICC / CDYN columns from sort CSV",
                       variable=self._chk_upm, bg=BG, fg=FG,
                       selectcolor=BG3, activebackground=BG, font=FONT_SM
                       ).pack(anchor="w", padx=8, pady=2)
        tk.Checkbutton(g2, text="PCM/etest from shared/etest/  (auto-matched by lot)",
                       variable=self._chk_pcm, bg=BG, fg=FG,
                       selectcolor=BG3, activebackground=BG, font=FONT_SM
                       ).pack(anchor="w", padx=8, pady=2)
        row2 = tk.Frame(g2, bg=BG)
        row2.pack(fill="x", padx=8, pady=4)
        self._lbl(row2, "Extra etest folder:").pack(side="left")
        self._extra_var = tk.StringVar()
        self._entry(row2, textvariable=self._extra_var).pack(
            side="left", fill="x", expand=True, padx=6, ipady=3)
        self._btn(row2, "Browse...", self._browse_extra, BLUE).pack(side="left", padx=2)
        self._btn(row2, "Discover ->", self._discover, ORANGE).pack(side="left", padx=2)
        self._lbl_pcm = tk.Label(g2, text="", bg=BG, fg=FG_DIM, font=FONT_SM)
        self._lbl_pcm.pack(anchor="w", padx=8, pady=(0, 4))

        # Step 3
        g3 = ttk.LabelFrame(outer, text="Step 3 -- Select Parameters")
        g3.pack(fill="both", expand=True, pady=(0, 6))

        # shared selection counter
        sel_row = tk.Frame(g3, bg=BG)
        sel_row.pack(fill="x", padx=8, pady=(4, 0))
        self._lbl_sel = tk.Label(sel_row, text="0 / 0 selected", bg=BG, fg=BLUE, font=FONT_SM)
        self._lbl_sel.pack(side="right")

        paned = ttk.PanedWindow(g3, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=4)

        # ── UPM / SICC / CDYN pane with its own filter bar ──
        upm_frame = ttk.LabelFrame(paned, text="UPM / SICC / CDYN  (sort CSV columns)")
        paned.add(upm_frame, weight=1)
        upm_fbar = tk.Frame(upm_frame, bg=BG)
        upm_fbar.pack(fill="x", padx=4, pady=(4, 2))
        self._lbl(upm_fbar, "Filter:").pack(side="left")
        self._filter_upm_var = tk.StringVar()
        ufe = self._entry(upm_fbar, textvariable=self._filter_upm_var, width=16)
        ufe.pack(side="left", padx=4, ipady=3)
        ufe.bind("<Return>", lambda e: self._rebuild_params())
        self._btn(upm_fbar, "Apply",    self._rebuild_params,                    FG   ).pack(side="left", padx=2)
        self._btn(upm_fbar, "All",      lambda: (self._upm_tree.set_all(True),  self._upd_sel()), GREEN).pack(side="left", padx=2)
        self._btn(upm_fbar, "None",     lambda: (self._upm_tree.set_all(False), self._upd_sel()), RED  ).pack(side="left", padx=2)
        self._btn(upm_fbar, "Expand",   lambda: self._upm_tree.expand_all(True),  FG).pack(side="left", padx=2)
        self._btn(upm_fbar, "Collapse", lambda: self._upm_tree.expand_all(False), FG).pack(side="left", padx=2)
        self._upm_tree = self._make_tree(upm_frame)

        # ── PCM pane with its own filter bar ──
        pcm_frame = ttk.LabelFrame(paned, text="PCM / Etest  (shared/etest)")
        paned.add(pcm_frame, weight=2)
        pcm_fbar = tk.Frame(pcm_frame, bg=BG)
        pcm_fbar.pack(fill="x", padx=4, pady=(4, 2))
        self._lbl(pcm_fbar, "Filter:").pack(side="left")
        self._filter_pcm_var = tk.StringVar()
        pfe = self._entry(pcm_fbar, textvariable=self._filter_pcm_var, width=16)
        pfe.pack(side="left", padx=4, ipady=3)
        pfe.bind("<Return>", lambda e: self._rebuild_params())
        self._btn(pcm_fbar, "Apply",    self._rebuild_params,                    FG   ).pack(side="left", padx=2)
        self._btn(pcm_fbar, "All",      lambda: (self._pcm_tree.set_all(True),  self._upd_sel()), GREEN).pack(side="left", padx=2)
        self._btn(pcm_fbar, "None",     lambda: (self._pcm_tree.set_all(False), self._upd_sel()), RED  ).pack(side="left", padx=2)
        self._btn(pcm_fbar, "Expand",   lambda: self._pcm_tree.expand_all(True),  FG).pack(side="left", padx=2)
        self._btn(pcm_fbar, "Collapse", lambda: self._pcm_tree.expand_all(False), FG).pack(side="left", padx=2)
        self._pcm_tree = self._make_tree(pcm_frame)

        # Step 4
        g4 = ttk.LabelFrame(outer, text="Step 4 -- Analysis Options")
        g4.pack(fill="x", pady=(0, 6))
        row4a = tk.Frame(g4, bg=BG)
        row4a.pack(fill="x", padx=8, pady=4)
        self._lbl(row4a, "Analyze by:").pack(side="left")
        self._mode_var = tk.StringVar(value="FB")
        tk.Radiobutton(row4a, text="IB  (Interface Bin)", variable=self._mode_var,
                       value="IB", bg=BG, fg=FG, selectcolor=BG3,
                       activebackground=BG, font=FONT_SM).pack(side="left", padx=6)
        tk.Radiobutton(row4a, text="FB  (Functional Bin)", variable=self._mode_var,
                       value="FB", bg=BG, fg=FG, selectcolor=BG3,
                       activebackground=BG, font=FONT_SM).pack(side="left", padx=6)
        row4b = tk.Frame(g4, bg=BG)
        row4b.pack(fill="x", padx=8, pady=2)
        self._lbl(row4b, "Bins:").pack(side="left")
        self._bin_combo = BinPicker(row4b, "Select bins...")
        self._bin_combo.pack(side="left", fill="x", expand=True, padx=6)
        self._btn(row4b, "All",  lambda: self._bin_combo.setAllChecked(True),  GREEN).pack(side="left", padx=2)
        self._btn(row4b, "None", lambda: self._bin_combo.setAllChecked(False), RED  ).pack(side="left", padx=2)
        row4c = tk.Frame(g4, bg=BG)
        row4c.pack(fill="x", padx=8, pady=2)
        self._lbl(row4c, "Ignore IB:").pack(side="left")
        self._ignore_ib_var = tk.StringVar(value="98")
        self._entry(row4c, textvariable=self._ignore_ib_var, width=14).pack(side="left", padx=4, ipady=3)
        self._lbl(row4c, "Ignore FB:", FG).pack(side="left", padx=(12, 0))
        self._ignore_fb_var = tk.StringVar()
        self._entry(row4c, textvariable=self._ignore_fb_var, width=14).pack(side="left", padx=4, ipady=3)
        self._lbl(row4c, "(comma-separated)", FG_DIM).pack(side="left")
        self._chk_js = tk.BooleanVar(value=False)
        tk.Checkbutton(g4, text="Live JS correlation  (auto for <= 30k rows)",
                       variable=self._chk_js, bg=BG, fg=FG,
                       selectcolor=BG3, activebackground=BG, font=FONT_SM
                       ).pack(anchor="w", padx=8, pady=(2, 2))
        self._chk_bindep = tk.BooleanVar(value=False)
        tk.Checkbutton(g4, text="Analyze bin dependency (co-failure among selected bins)",
                       variable=self._chk_bindep, bg=BG, fg=FG,
                       selectcolor=BG3, activebackground=BG, font=FONT_SM
                       ).pack(anchor="w", padx=8, pady=(2, 6))

        # Output folder row
        out_bar = tk.Frame(outer, bg=BG)
        out_bar.pack(fill="x", pady=(2, 2))
        self._lbl(out_bar, "Output folder:").pack(side="left")
        self._entry(out_bar, textvariable=self._out_var).pack(
            side="left", fill="x", expand=True, padx=6, ipady=3)
        self._btn(out_bar, "Browse...", self._browse_output, BLUE).pack(side="left", padx=2)

        # Run bar
        run_bar = tk.Frame(outer, bg=BG)
        run_bar.pack(fill="x", pady=(2, 4))
        self._btn_run = self._btn(run_bar, "Run Correlation", self._run, "#fff", BLUE)
        self._btn_run.pack(side="left", padx=(0, 8))
        self._btn(run_bar, "Save Report",  self._save_report,  GREEN ).pack(side="left", padx=(0, 4))
        self._btn_open_dash = self._btn(run_bar, "Open Dashboard", self._open_dashboard, "#ecf0f1", "#117a65")
        self._btn_open_dash.pack(side="left", padx=(0, 16))
        self._btn_open_dash.config(state="disabled")
        self._btn(run_bar, "Save Preset",  self._save_preset,  ORANGE).pack(side="left", padx=(0, 4))
        self._btn(run_bar, "Load Preset",  self._load_preset,  ORANGE).pack(side="left")
        self._lbl_status = tk.Label(run_bar, text="Ready.", bg=BG, fg=BLUE, font=FONT_SM)
        self._lbl_status.pack(side="right")

        self._pb = ttk.Progressbar(outer, mode="indeterminate", length=800)
        self._pb.pack(fill="x", pady=(0, 4))

        # Log
        log_frame = tk.Frame(outer, bg=BG2,
                             highlightbackground=BORDER, highlightthickness=1)
        log_frame.pack(fill="x")
        self._log = tk.Text(log_frame, bg=BG2, fg="#95a5a6",
                            font=("Consolas", 9), relief="flat", bd=0,
                            height=8, state="disabled", wrap="word",
                            insertbackground=FG)
        vsb = ttk.Scrollbar(log_frame, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._log.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        self._log.tag_config("ok",   foreground=GREEN)
        self._log.tag_config("err",  foreground=RED)
        self._log.tag_config("warn", foreground=ORANGE)
        self._log.tag_config("dim",  foreground=FG_DIM)
        self._log.tag_config("hdr",  foreground=BLUE, font=("Consolas", 9, "bold"))

    def _make_tree(self, parent):
        frame = tk.Frame(parent, bg=BG2)
        frame.pack(fill="both", expand=True, padx=4, pady=4)
        vsb = ttk.Scrollbar(frame, orient="vertical")
        hsb = ttk.Scrollbar(frame, orient="horizontal")
        tree = CheckableTree(frame, on_change=self._upd_sel,
                             yscrollcommand=vsb.set,
                             xscrollcommand=hsb.set,
                             height=12)
        vsb.configure(command=tree.yview)
        hsb.configure(command=tree.xview)
        hsb.pack(side="bottom", fill="x")
        vsb.pack(side="right",  fill="y")
        tree.pack(side="left",  fill="both", expand=True)
        tree.tag_configure("blue",   foreground=BLUE)
        tree.tag_configure("green",  foreground=GREEN)
        tree.tag_configure("orange", foreground=ORANGE)
        tree.tag_configure("sep",    foreground=FG_DIM)
        return tree

    # --- file dialogs ---

    _LAST_DIR_FILE = (Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
                      / "corr-analysis" / "last_dir.txt")

    def _load_last_dir(self):
        try:
            d = self._LAST_DIR_FILE.read_text(encoding="utf-8").strip()
            if d and Path(d).is_dir():
                return d
        except Exception:
            pass
        return str(Path.home())

    def _save_last_dir(self, path):
        try:
            self._LAST_DIR_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._LAST_DIR_FILE.write_text(path, encoding="utf-8")
        except Exception:
            pass

    # --- remembered-dir dialog helpers ---

    def _askopen(self, title, filetypes, initial_dir=None):
        d = initial_dir or self._last_dir
        p = filedialog.askopenfilename(title=title, initialdir=d, filetypes=filetypes)
        if p:
            self._last_dir = str(Path(p).parent)
            self._save_last_dir(self._last_dir)
        return p

    def _askdir(self, title, initial_dir=None):
        d = initial_dir or self._last_dir
        p = filedialog.askdirectory(title=title, initialdir=d)
        if p:
            self._last_dir = str(Path(p))
            self._save_last_dir(self._last_dir)
        return p

    def _asksave(self, title, initialfile, defaultextension, filetypes, initial_dir=None):
        d = initial_dir or self._last_dir
        p = filedialog.asksaveasfilename(
            title=title, initialdir=d, initialfile=initialfile,
            defaultextension=defaultextension, filetypes=filetypes)
        if p:
            self._last_dir = str(Path(p).parent)
            self._save_last_dir(self._last_dir)
        return p

    def _browse_csv(self):
        d = self._last_dir or str(Path.home())
        paths = filedialog.askopenfilenames(
            title="Select Sort CSV / ZIP / GZ (multiple allowed)",
            initialdir=d,
            filetypes=[("CSV / ZIP / GZ", "*.csv *.zip *.gz"),
                       ("CSV files", "*.csv"),
                       ("ZIP files", "*.zip"),
                       ("GZ files",  "*.gz"),
                       ("All files", "*.*")])
        if paths:
            self._last_dir = str(Path(paths[0]).parent)
            self._save_last_dir(self._last_dir)
            for p in paths:
                if p not in self._csv_paths:
                    self._csv_paths.append(p)
                    self._csv_listbox.insert(tk.END, p)
            self._csv_var.set(self._csv_paths[0] if self._csv_paths else "")

    def _remove_csv_files(self):
        for i in reversed(self._csv_listbox.curselection()):
            self._csv_paths.pop(i)
            self._csv_listbox.delete(i)
        self._csv_var.set(self._csv_paths[0] if self._csv_paths else "")

    def _clear_csv_files(self):
        self._csv_paths.clear()
        self._csv_listbox.delete(0, tk.END)
        self._csv_var.set("")

    def _browse_extra(self):
        d = self._askdir("Extra etest folder")
        if d:
            self._extra_var.set(d)

    def _browse_output(self):
        d = self._askdir("Select output folder")
        if d:
            self._out_var.set(d)

    # --- header load ---

    def _load_header(self):
        if not self._csv_paths:
            messagebox.showerror("Error", "Add at least one file first.")
            return
        path = self._csv_paths[0]
        if not Path(path).exists():
            messagebox.showerror("Error", f"File not found: {path}")
            return
        self._csv_path = path
        self._log_line(f"Reading header: {path}")
        self._status("Loading header...")
        self._btn_load_hdr.config(state="disabled")
        self._pb.start(12)

        def _bg():
            cols   = _read_csv_header(path)
            ib_col = _detect_ib_col(cols)
            fb_col = _detect_fb_col(cols)
            upm    = [c for c in cols if "UPM"  in c.upper()]
            sicc   = [c for c in cols if "SICC" in c.upper() and c not in upm]
            cdyn   = [c for c in cols if "CDYN" in c.upper() and c not in upm and c not in sicc]
            drs_col = _detect_devrevstep_col(cols)
            drs_prefix = ""
            if drs_col:
                try:
                    sample = pd.read_csv(path, usecols=[drs_col], nrows=20, low_memory=False)
                    vals = sample[drs_col].dropna()
                    if not vals.empty:
                        drs_prefix = str(vals.iloc[0])[:6].upper()
                except Exception:
                    pass
            ib_fbs = {}
            if ib_col:
                read_cols = [ib_col] + ([fb_col] if fb_col else [])
                tmp = pd.read_csv(path, usecols=read_cols, low_memory=False)
                unique_ibs = sorted(tmp[ib_col].dropna().astype(int).unique().tolist())
                all_fbs = (sorted(tmp[fb_col].dropna().astype(int).unique().tolist())
                           if fb_col else [])
                for ib in unique_ibs[:40]:
                    ib_fbs[ib] = [fb for fb in all_fbs if fb // 100 == ib]
            return {"cols": cols, "ib_col": ib_col, "fb_col": fb_col,
                    "upm": upm, "sicc": sicc, "cdyn": cdyn,
                    "ib_fbs": ib_fbs, "drs_prefix": drs_prefix}

        def _done(res):
            self._pb.stop()
            self._btn_load_hdr.config(state="normal")
            if isinstance(res, Exception):
                self._log_line(f"ERROR: {res}", "err")
                self._lbl_load.config(text="Load failed -- see log.", fg=RED)
                self._status("Load failed.")
                return
            cols   = res["cols"]
            ib_col = res["ib_col"]
            self._devrevstep_prefix = res.get("drs_prefix", "")
            fb_col = res["fb_col"]
            upm    = res["upm"]
            sicc   = res.get("sicc", [])
            cdyn   = res.get("cdyn", [])
            self._ib_fbs      = res["ib_fbs"]
            self._header_cols = cols
            self._ib_col      = ib_col or ""
            self._fb_col      = fb_col or ""
            self._log_line(
                f"  Columns: {len(cols)} total | {len(upm)} UPM | "
                f"{len(sicc)} SICC | {len(cdyn)} CDYN | "
                f"IB={ib_col or '?'} | FB={fb_col or '?'}")
            if self._ib_fbs:
                self._bin_combo.populate(self._ib_fbs)
            self._all_params = (upm + sicc + cdyn) if self._chk_upm.get() else []
            self._pcm_cols_discovered = set()
            self._rebuild_params()
            self._apply_pending_preset(bins_only=True)
            n_ibs = len(self._ib_fbs)
            n_fbs = sum(len(v) for v in self._ib_fbs.values())
            self._lbl_load.config(
                text=(f"Header loaded  |  {len(cols)} cols  |  {n_ibs} IB  |  {n_fbs} FB  "
                      f"|  {len(upm)} UPM | {len(sicc)} SICC | {len(cdyn)} CDYN"),
                fg=GREEN)
            # Auto-set output folder default when CSV is loaded
            if not self._out_var.get() and self._csv_path:
                self._out_var.set(str(Path(self._csv_path).parent / "output"))
            self._status("Header loaded -- full data loaded only when you click Run.")
            if self._auto_discover_after_header:
                self._auto_discover_after_header = False
                self._log_line("  Auto-discovering PCM...", "dim")
                self._discover()

        threading.Thread(target=lambda: self._run_bg(_bg, _done), daemon=True).start()

    def _run_bg(self, fn, callback):
        try:
            res = fn()
        except Exception as e:
            res = e
        self.after(0, lambda: callback(res))

    # --- PCM discovery ---

    def _discover(self):
        if not self._csv_path:
            messagebox.showwarning("No CSV", "Load the CSV header first.")
            return
        self._lbl_pcm.config(text="Scanning shared/etest/...", fg=ORANGE)
        self._status("Discovering...")
        self._pb.start(12)

        def _bg():
            cols = self._header_cols
            lc = next((c for c in cols if c.lower() in ("lot", "sort_lot")), None)
            if not lc:
                return {"error": "No Lot column found."}
            # Read lot column from all files for discovery
            all_paths = self._csv_paths if self._csv_paths else [self._csv_path]
            self.after(0, lambda: self._log_line(f"  Discover: {len(all_paths)} file(s) → {[str(p) for p in all_paths]}", "dim"))
            lot_vals = set()
            for p in all_paths:
                try:
                    if p.lower().endswith(".zip"):
                        import zipfile
                        with zipfile.ZipFile(p, "r") as zf:
                            for member in zf.namelist():
                                if member.lower().endswith(".csv"):
                                    with zf.open(member) as f:
                                        tmp = pd.read_csv(f, usecols=[lc], low_memory=False)
                                        lot_vals.update(tmp[lc].dropna().unique())
                                    break
                    elif p.lower().endswith(".gz"):
                        tmp = pd.read_csv(p, usecols=[lc], compression="gzip", low_memory=False)
                        lot_vals.update(tmp[lc].dropna().unique())
                    else:
                        tmp = pd.read_csv(p, usecols=[lc], low_memory=False)
                        lot_vals.update(tmp[lc].dropna().unique())
                except Exception:
                    pass
            lots = list(lot_vals)
            pcm_map  = find_pcm_for_lots(lots, devrevstep_prefix=self._devrevstep_prefix)
            # Discover PCM column names from headers only — no full data read at this stage.
            # Collect unique zip/csv paths first; read 0 rows from each to get columns fast.
            pcm_cols = set()
            seen_zips: set = set()
            for _lot, pcm_files in pcm_map.items():
                for zip_path, _is_full, _pcm_lot_id in pcm_files:
                    if zip_path in seen_zips:
                        continue
                    seen_zips.add(zip_path)
                    try:
                        if zip_path.lower().endswith(".zip"):
                            import zipfile as _zf
                            with _zf.ZipFile(zip_path, "r") as zf:
                                for member in sorted(zf.namelist()):
                                    if member.endswith("/") or not member.lower().endswith(".csv"):
                                        continue
                                    with zf.open(member) as f:
                                        hdr_df = pd.read_csv(f, nrows=0, low_memory=False)
                                    pcm_cols.update(_etest_cols_from_pcm(hdr_df))
                                    break  # one member is enough for column names
                        else:
                            hdr_df = pd.read_csv(zip_path, nrows=0, low_memory=False)
                            pcm_cols.update(_etest_cols_from_pcm(hdr_df))
                    except Exception as ex:
                        self.after(0, lambda msg=f"  WARN col-scan {zip_path}: {ex}":
                                   self._log_line(msg, "warn"))
            return {"pcm_map": pcm_map, "pcm_cols": pcm_cols, "lots": lots}

        def _done(res):
            self._pb.stop()
            if isinstance(res, Exception):
                self._lbl_pcm.config(text=f"Error: {res}", fg=RED)
                self._status("Discovery failed.")
                return
            if "error" in res:
                self._lbl_pcm.config(text=f"Error: {res['error']}", fg=RED)
                self._status("Discovery failed.")
                return
            self._pcm_map = res["pcm_map"]
            self._pcm_cols_discovered = res["pcm_cols"]
            upm  = ([c for c in self._header_cols if "UPM"  in c.upper()] if self._chk_upm.get() else [])
            sicc = ([c for c in self._header_cols if "SICC" in c.upper() and c not in upm] if self._chk_upm.get() else [])
            cdyn = ([c for c in self._header_cols if "CDYN" in c.upper() and c not in upm and c not in sicc] if self._chk_upm.get() else [])
            sort_cols = upm + sicc + cdyn
            self._all_params = sort_cols + sorted(self._pcm_cols_discovered - set(sort_cols))
            self._rebuild_params()
            n_lots_total  = len(res["lots"])
            n_lots_matched = len(self._pcm_map)
            unique_zips = len({zp for entries in self._pcm_map.values() for zp, _, _ in entries})
            info = (f"{n_lots_matched}/{n_lots_total} lots matched  |  "
                    f"{unique_zips} etest file(s)  |  "
                    f"{len(self._pcm_cols_discovered)} PCM cols  |  "
                    f"{len(upm)} UPM | {len(sicc)} SICC | {len(cdyn)} CDYN")
            self._lbl_pcm.config(text=info, fg=GREEN if n_lots_matched else RED)
            if n_lots_matched == 0:
                self._log_line(
                    f"  No PCM files found for any of the {n_lots_total} lots in this CSV.\n"
                    f"  Add PCM zip files to shared/etest/full-sites or 9-sites,\n"
                    f"  or use the 'Extra etest folder' browse to point at a local folder.", "warn")
            else:
                for lot, pcm_files in self._pcm_map.items():
                    for zip_path, is_full, pcm_lot_id in pcm_files:
                        src = "full-site" if is_full else "9-site"
                        self._log_line(f"  {lot} [{src}] pcm={pcm_lot_id} -> {os.path.basename(zip_path)}")
                self._log_line(f"  Total: {n_lots_matched} lots matched → {unique_zips} etest file(s).")
                unmatched = [str(l) for l in res["lots"] if str(l) not in self._pcm_map]
                if unmatched:
                    self._log_line(f"  No PCM for {len(unmatched)} lot(s): {', '.join(unmatched[:10])}"
                                   + (" ..." if len(unmatched) > 10 else ""), "warn")
            self._status("Discovered.")
            self._apply_pending_preset(bins_only=False)

        threading.Thread(target=lambda: self._run_bg(_bg, _done), daemon=True).start()

    # --- parameter tree ---

    def _rebuild_params(self):
        self._upm_tree.clear()
        self._pcm_tree.clear()

        filt_upm = self._filter_upm_var.get().strip().lower()
        filt_pcm = self._filter_pcm_var.get().strip().lower()
        patterns_upm = [p.strip() for p in filt_upm.split(",") if p.strip()] if filt_upm else []
        patterns_pcm = [p.strip() for p in filt_pcm.split(",") if p.strip()] if filt_pcm else []

        def _matches_upm(col):
            return not patterns_upm or any(fnmatch.fnmatch(col.lower(), p) for p in patterns_upm)

        def _matches_pcm(col):
            return not patterns_pcm or any(fnmatch.fnmatch(col.lower(), p) for p in patterns_pcm)

        # Partition sort-CSV cols into UPM / SICC / CDYN by column name keyword
        _sort_cols = [c for c in self._all_params
                      if any(k in c.upper() for k in ("UPM", "SICC", "CDYN"))]
        pcm_cols   = [c for c in self._all_params
                      if c not in _sort_cols and _matches_pcm(c)]
        upm_cols  = [c for c in _sort_cols if "UPM"  in c.upper() and _matches_upm(c)]
        sicc_cols = [c for c in _sort_cols if "SICC" in c.upper() and _matches_upm(c)]
        cdyn_cols = [c for c in _sort_cols if "CDYN" in c.upper() and _matches_upm(c)]

        def _upm_subgroup(col):
            s = re.sub(r"^UPM_", "", col, flags=re.IGNORECASE)
            m = re.match(r"(\w+)", s)
            return m.group(1) if m else "UPM"

        def _sicc_subgroup(col):
            m = re.search(r"(VCCCORE\d+|VCCATOM\d+|VCCR\b|VCCIA\b|VCCIO\b|VCCSRAM\b|VNNAON\b|VCC1P8A\b|CORE\d+|ATOM\d+|RING|FULLCHIP)", col, re.IGNORECASE)
            return m.group(1).upper() if m else "SICC"

        def _cdyn_subgroup(col):
            m = re.search(r"(CORE\d+|ATOM\d+|OG_\d+B|PTH_OG)", col, re.IGNORECASE)
            return m.group(1).upper() if m else "CDYN"

        # UPM section
        if upm_cols:
            self._upm_tree.add_separator("UPM")
            upm_grps = {}
            for c in upm_cols:
                upm_grps.setdefault(_upm_subgroup(c), []).append(c)
            for grp, cols in sorted(upm_grps.items()):
                self._upm_tree.add_group(grp, cols, "blue", expand=True)

        # SICC section
        if sicc_cols:
            self._upm_tree.add_separator("SICC")
            sicc_grps = {}
            for c in sicc_cols:
                sicc_grps.setdefault(_sicc_subgroup(c), []).append(c)
            for grp, cols in sorted(sicc_grps.items()):
                self._upm_tree.add_group(grp, cols, "green", expand=False)

        # CDYN section
        if cdyn_cols:
            self._upm_tree.add_separator("CDYN")
            cdyn_grps = {}
            for c in cdyn_cols:
                cdyn_grps.setdefault(_cdyn_subgroup(c), []).append(c)
            for grp, cols in sorted(cdyn_grps.items()):
                self._upm_tree.add_group(grp, cols, "orange", expand=False)

        if not upm_cols and not sicc_cols and not cdyn_cols:
            self._upm_tree.insert("", "end", text="(no UPM / SICC / CDYN columns)", tags=("sep",))

        def _pcm_group(col):
            parts = col.split("_", 1)
            return parts[0] if len(parts) > 1 else col

        pcm_groups = {}
        for c in pcm_cols:
            pcm_groups.setdefault(_pcm_group(c), []).append(c)
        for grp, cols in sorted(pcm_groups.items()):
            self._pcm_tree.add_group(grp, cols, "green", expand=False)

        if not pcm_cols:
            self._pcm_tree.insert("", "end",
                                  text="(run Discover -> to load PCM data)",
                                  tags=("sep",))
        self._upd_sel()

    def _upd_sel(self):
        total = sum(1 for tree in (self._upm_tree, self._pcm_tree)
                    for grp in tree.get_children()
                    for child in tree.get_children(grp)
                    if tree._is_leaf(child))
        sel   = sum(1 for tree in (self._upm_tree, self._pcm_tree)
                    for grp in tree.get_children()
                    for child in tree.get_children(grp)
                    if tree._is_leaf(child) and tree._is_checked(child))
        self._lbl_sel.config(text=f"{sel} / {total} selected")

    def _sel_all(self, val):
        self._upm_tree.set_all(val)
        self._pcm_tree.set_all(val)
        self._upd_sel()

    def _tree_expand(self, val):
        self._upm_tree.expand_all(val)
        self._pcm_tree.expand_all(val)

    def _get_selected_params(self):
        return self._upm_tree.checked_cols() + self._pcm_tree.checked_cols()

    # --- preset save / load ---

    def _save_preset(self):
        path = self._asksave(
            title="Save GUI Preset",
            initialfile="preset.json",
            defaultextension=".json",
            filetypes=[("JSON preset", "*.json"), ("All files", "*.*")])
        if not path:
            return
        preset = {
            "csv_path":    self._csv_var.get(),
            "csv_paths":   list(self._csv_paths),
            "use_pcm":     bool(self._chk_pcm.get()),
            "extra_folder": self._extra_var.get(),
            "out_folder":  self._out_var.get(),
            "mode":        self._mode_var.get(),
            "ignore_ib":   self._ignore_ib_var.get(),
            "ignore_fb":   self._ignore_fb_var.get(),
            "js_compute":  bool(self._chk_js.get()),
            "bin_dep":     bool(self._chk_bindep.get()),
            "filter_upm":  self._filter_upm_var.get(),
            "filter_pcm":  self._filter_pcm_var.get(),
            "bins":        self._bin_combo.get_state(),
            "selected_params": self._get_selected_params(),
        }
        import json
        Path(path).write_text(json.dumps(preset, indent=2), encoding="utf-8")
        self._log_line(f"Preset saved: {path}", "ok")

    def _load_preset(self):
        path = self._askopen(
            title="Load GUI Preset",
            filetypes=[("JSON preset", "*.json"), ("All files", "*.*")])
        if not path:
            return
        import json
        try:
            preset = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("Load Preset", f"Could not read preset:\n{e}")
            return

        # Restore simple fields
        if "csv_path" in preset: self._csv_var.set(preset["csv_path"])
        if "csv_paths" in preset:
            self._csv_paths = list(preset["csv_paths"])
            self._csv_listbox.delete(0, tk.END)
            for p in self._csv_paths:
                self._csv_listbox.insert(tk.END, p)
            if self._csv_paths:
                self._csv_var.set(self._csv_paths[0])
        if "use_pcm"     in preset: self._chk_pcm.set(preset["use_pcm"])
        if "extra_folder" in preset: self._extra_var.set(preset["extra_folder"])
        if "out_folder"  in preset: self._out_var.set(preset["out_folder"])
        if "mode"        in preset: self._mode_var.set(preset["mode"])
        if "ignore_ib"   in preset: self._ignore_ib_var.set(preset["ignore_ib"])
        if "ignore_fb"   in preset: self._ignore_fb_var.set(preset["ignore_fb"])
        if "js_compute"  in preset: self._chk_js.set(preset["js_compute"])
        if "bin_dep"      in preset: self._chk_bindep.set(preset["bin_dep"])
        if "filter_upm"   in preset: self._filter_upm_var.set(preset["filter_upm"])
        if "filter_pcm"   in preset: self._filter_pcm_var.set(preset["filter_pcm"])
        if "filter"       in preset: self._filter_upm_var.set(preset["filter"])  # legacy

        # Stash for post-discover application
        self._pending_preset = preset
        self._log_line(f"Preset loaded: {path}", "ok")

        # Auto-trigger header load if CSV exists
        # Support both old single-path presets and new multi-path presets
        if not self._csv_paths:
            csv = preset.get("csv_path", "").strip()
            if csv:
                self._csv_paths = [csv]
                self._csv_listbox.delete(0, tk.END)
                self._csv_listbox.insert(tk.END, csv)
        csv = self._csv_paths[0] if self._csv_paths else ""
        if csv and Path(csv).exists():
            self._log_line("  Auto-loading header...", "dim")
            self._auto_discover_after_header = bool(preset.get("use_pcm", True))
            self._load_header()
        else:
            self._log_line("  CSV path and options restored. "
                           "Click 'Load Header' (then Discover if needed) "
                           "to restore parameter + bin selections.", "dim")

    def _apply_pending_preset(self, bins_only=False):
        """Apply bin + parameter selections from a pending preset (called after header/discover)."""
        p = self._pending_preset
        if not p:
            return
        if "bins" in p:
            self._bin_combo.set_state(p["bins"])
        if not bins_only and "selected_params" in p:
            cols = set(p["selected_params"])
            self._upm_tree.set_checked_cols(cols)
            self._pcm_tree.set_checked_cols(cols)
            n = len([c for c in p["selected_params"]
                     if c in set(self._upm_tree.checked_cols() +
                                  self._pcm_tree.checked_cols())])
            self._log_line(f"  Preset: {n}/{len(p['selected_params'])} parameters restored.", "ok")
            self._pending_preset = None   # consumed

    # --- Run ---

    def _run(self):
        if not self._csv_path:
            messagebox.showwarning("No Data", "Load the CSV header first.")
            return
        selected = self._get_selected_params()
        if not selected:
            messagebox.showwarning("No Params", "Select at least one parameter.")
            return
        target_specs = self._build_target_specs()
        if not target_specs:
            messagebox.showwarning("No Target", "Select at least one IB or FB bin.")
            return

        self._btn_run.config(state="disabled")
        self._status("Loading data...")
        self._pb.start(12)

        ignore_ib_text   = self._ignore_ib_var.get().strip()
        ignore_fb_text   = self._ignore_fb_var.get().strip()
        js_compute       = self._chk_js.get()
        pcm_map_snapshot = dict(self._pcm_map)

        def _load():
            paths = self._csv_paths if self._csv_paths else [self._csv_path]
            parts = []
            for p in paths:
                p = p.strip()
                if not p:
                    continue
                if p.lower().endswith(".zip"):
                    import zipfile
                    with zipfile.ZipFile(p, "r") as zf:
                        for member in zf.namelist():
                            if member.lower().endswith(".csv"):
                                with zf.open(member) as f:
                                    parts.append(pd.read_csv(f, low_memory=False))
                elif p.lower().endswith(".gz"):
                    parts.append(pd.read_csv(p, compression="gzip", low_memory=False))
                else:
                    parts.append(pd.read_csv(p, low_memory=False))
            if not parts:
                raise ValueError("No data loaded from selected files.")
            df = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
            return df

        def _on_loaded(df):
            if isinstance(df, Exception):
                self._pb.stop()
                self._btn_run.config(state="normal")
                self._log_line(f"ERROR loading CSV: {df}", "err")
                self._status("Error -- see log.")
                return
            self._chk_js.set(len(df) <= 30_000)
            self._status("Analysing...")

            def _analyse():
                import time as _time
                results = []
                _t_all = _time.time()
                _merge_cache = None   # merge/IDW is target-independent -> compute once
                for tspec in target_specs:
                    self.after(0, lambda t=tspec: self._log_line(
                        f"\n===== TIMING for {t} =====", "hdr"))
                    _t0 = _time.time()
                    if _merge_cache is None:
                        merged = self._merge_data(df, selected, tspec,
                                                  ignore_ib_text, ignore_fb_text,
                                                  pcm_map_snapshot)
                        _merge_cache = merged
                        _dt_merge = _time.time() - _t0
                        _merge_tag = "merge + PCM load"
                    else:
                        merged = self._apply_target(_merge_cache, tspec)
                        _dt_merge = _time.time() - _t0
                        _merge_tag = "merge (cached, target only)"
                    if merged is None or merged.empty:
                        self.after(0, lambda t=tspec:
                                   self._log_line(f"  WARN: empty merge for {t}", "warn"))
                        continue
                    self.after(0, lambda d=_dt_merge, n=len(merged), tag=_merge_tag:
                        self._log_line(f"  [time] {tag:<24}: {d:6.2f}s  ({n:,} dies)", "ok"))
                    _t0 = _time.time()
                    corr = self._compute_all_correlations(merged, selected, tspec)
                    _dt_corr = _time.time() - _t0
                    self.after(0, lambda d=_dt_corr:
                        self._log_line(f"  [time] correlations     : {d:6.2f}s", "ok"))
                    eff_selected = [c for c in merged.columns
                                    if c in set(selected) or c == "_radius"]
                    _t0 = _time.time()
                    zone_data = self._compute_zone_interaction(merged, eff_selected, corr)
                    _dt_zone = _time.time() - _t0
                    _n_zone = len((zone_data or {}).get("interactions", []))
                    self.after(0, lambda d=_dt_zone, nz=_n_zone:
                        self._log_line(f"  [time] zone interaction : {d:6.2f}s  ({nz} param tables)"
                                       + ("   <-- often the biggest stage" if d >= 5 else ""), "ok"))
                    results.append({"df": merged, "corr": corr,
                                    "target": tspec, "selected": selected,
                                    "js_compute": js_compute,
                                    "zone": zone_data})
                self.after(0, lambda d=_time.time()-_t_all:
                    self._log_line(f"  [time] TOTAL analyse    : {d:6.2f}s", "hdr"))
                return results

            def _on_done(results):
                self._pb.stop()
                self._btn_run.config(state="normal")
                if isinstance(results, Exception):
                    self._log_line(f"ERROR: {results}", "err")
                    self._status("Error -- see log.")
                    return
                if not results:
                    self._status("No results produced.")
                    return
                out_dir_str = self._out_var.get().strip()
                if out_dir_str:
                    out_dir = Path(out_dir_str)
                else:
                    out_dir = Path(self._csv_path).parent / "output"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_csv = out_dir / "ideal_analysis_data.csv"
                results[0]["df"].to_csv(out_csv, index=False)
                self._log_line(f"Saved CSV: {out_csv}")
                self._last_report = {"results": results, "out_dir": out_dir}
                import time as _time
                _t0 = _time.time()
                self._build_and_open_report(results, out_dir)
                self._log_line(f"  [time] report build     : {_time.time()-_t0:6.2f}s", "ok")
                if self._chk_bindep.get() and len(target_specs) >= 2:
                    try:
                        _mf = results[0]["df"]
                        dep = self._compute_bin_dependency_matrix(_mf, target_specs)
                        if dep is None:
                            self._log_line("Bin dependency: could not resolve IB/FB columns — skipped.", "warn")
                        else:
                            dep_path = self._write_bin_dependency_report(dep, out_dir)
                            if dep_path:
                                self._log_line(f"Bin dependency report: {dep_path}", "ok")
                                self._inject_bindep_into_sidebar(results, out_dir, dep_path)
                    except Exception as _bdex:
                        self._log_line(f"Bin dependency ERROR: {_bdex}", "err")
                        self._log_line(traceback.format_exc(), "err")
                elif self._chk_bindep.get() and len(target_specs) < 2:
                    self._log_line("Bin dependency: need >=2 bins selected (currently fewer).", "warn")
                self._status(f"Done -- {len(results)} target(s) analysed. Click 'Open Dashboard' to view.")

            threading.Thread(target=lambda: self._run_bg(_analyse, _on_done),
                             daemon=True).start()

        threading.Thread(target=lambda: self._run_bg(_load, _on_loaded),
                         daemon=True).start()

    def _compute_bin_dependency_matrix(self, df, target_specs, methods=("pearson", "pcm_lot"),
                                       min_shared=8):
        """Driver-profile similarity between selected bins. Compares {param->r} vectors,
        not die co-failure, so it is valid even for mutually-exclusive IB bins."""
        import math
        labels = []; specs = []
        for ts in target_specs:
            if ts.startswith("IB==") or ts.startswith("FB=="):
                try:
                    val = int(ts.split("==")[1].split("|")[0])
                except Exception:
                    continue
                kind = "IB" if ts.startswith("IB==") else "FB"
                labels.append(f"{kind}{val}"); specs.append(ts)
        if len(labels) < 2:
            return None
        profiles = {m: {} for m in methods}
        d_profiles = {m: {} for m in methods}
        for lab, ts in zip(labels, specs):
            try:
                merged = self._apply_target(df, ts)
            except Exception:
                merged = df
            try:
                params = self._get_selected_params()
            except Exception:
                params = [c for c in merged.columns if c != "_TARGET"]
            try:
                corr = self._compute_all_correlations(merged, params, ts)
            except Exception:
                corr = None
            for m in methods:
                prof = {}
                d_prof = {}
                for row in ((corr or {}).get(m) or []):
                    if row.get("r") is not None:
                        prof[row["param"]] = float(row["r"])
                    if row.get("d") is not None:
                        d_prof[row["param"]] = float(row["d"])
                profiles[m][lab] = prof
                d_profiles[m][lab] = d_prof
        def _sim(a, b):
            shared = [p for p in a if p in b]
            if len(shared) < min_shared:
                return None, len(shared)
            xa = [a[p] for p in shared]; xb = [b[p] for p in shared]
            n = len(shared)
            ma = sum(xa)/n; mb = sum(xb)/n
            va = sum((x-ma)**2 for x in xa); vb = sum((x-mb)**2 for x in xb)
            if va <= 0 or vb <= 0:
                return None, n
            cov = sum((xa[i]-ma)*(xb[i]-mb) for i in range(n))
            return round(cov/math.sqrt(va*vb), 3), n
        out = {"labels": labels, "methods": {}}
        for m in methods:
            P = profiles[m]; k = len(labels)
            mat = [[None]*k for _ in range(k)]
            pairs = []
            for i in range(k):
                for j in range(i+1, k):
                    s, n = _sim(P.get(labels[i], {}), P.get(labels[j], {}))
                    mat[i][j] = mat[j][i] = s
                    pairs.append({"a": labels[i], "b": labels[j], "similarity": s, "n_shared": n})
            pairs.sort(key=lambda p: (p["similarity"] if p["similarity"] is not None else -9),
                       reverse=True)
            base = labels[0]; bp = P.get(base, {})
            top_params = sorted(bp, key=lambda p: -abs(bp[p]))[:15]
            table = []
            D = d_profiles[m]
            for p in top_params:
                table.append({"param": p,
                              "r": {lab: (round(P.get(lab, {}).get(p), 3)
                                    if P.get(lab, {}).get(p) is not None else None) for lab in labels},
                              "d": {lab: (round(D.get(lab, {}).get(p), 2)
                                    if D.get(lab, {}).get(p) is not None else None) for lab in labels}})
            out["methods"][m] = {"matrix": mat, "pairs": pairs, "side_by_side": table,
                                 "base": base}
        return out

    def _write_bin_dependency_report(self, dep, out_dir):
        """Write bin_dependency.html showing driver-profile similarity matrices."""
        import html as _h, os
        if not dep: return None
        L = dep["labels"]
        def scol(v):
            if v is None: return "#22405f"
            av = abs(v)
            if av >= 0.6: return "#e74c3c"
            if av >= 0.3: return "#e67e22"
            if av >= 0.1: return "#7fb3d3"
            return "#33506f"
        def matrix_html(M):
            h = '<table style="border-collapse:collapse;font-size:12px"><thead><tr><th></th>'
            for c in L: h += f'<th style="padding:5px 9px;color:#7fb3d3">{_h.escape(c)}</th>'
            h += "</tr></thead><tbody>"
            for i, r in enumerate(L):
                h += f'<tr><th style="padding:5px 9px;color:#7fb3d3;text-align:left">{_h.escape(r)}</th>'
                for j in range(len(L)):
                    v = M[i][j]
                    if i == j:
                        h += '<td style="padding:6px 10px;background:#0d1828;color:#445">\u2014</td>'
                    else:
                        c = scol(v); txt = ("%+.2f" % v) if v is not None else "n/a"
                        h += (f'<td style="padding:6px 10px;text-align:center;background:{c}22;'
                              f'color:{c};font-weight:bold">{txt}</td>')
                h += "</tr>"
            return h + "</tbody></table>"
        def _dtier_py(d):
            if d is None: return ("negligible", "#5d7a99")
            a = abs(d)
            if a >= 0.8: return ("large", "#e74c3c")
            if a >= 0.5: return ("medium", "#e67e22")
            if a >= 0.2: return ("small", "#f1c40f")
            return ("negligible", "#5d7a99")
        def side_html(tbl):
            h = ('<table style="border-collapse:collapse;font-size:12px"><thead><tr>'
                 '<th style="padding:4px 9px;color:#7fb3d3;text-align:left">Parameter</th>')
            for lab in L: h += f'<th style="padding:4px 9px;color:#7fb3d3">{_h.escape(lab)}</th>'
            h += "</tr></thead><tbody>"
            for row in tbl:
                full = _h.escape(row["param"])
                disp = (full[:72] + '\u2026') if len(row["param"]) > 72 else full
                h += (f'<tr style="border-bottom:1px solid #1a2f45">'
                      f'<td style="padding:3px 9px;color:#cdd9e5;max-width:320px;word-break:break-all" title="{full}">{disp}</td>')
                for lab in L:
                    r_val = row["r"].get(lab)
                    d_val = row.get("d", {}).get(lab)
                    lbl, col = _dtier_py(d_val)
                    if d_val is not None:
                        cell = (f'<span style="color:{col};font-weight:bold">{lbl}</span>'
                                f'<span style="color:{col}"> d={d_val:+.2f}</span>'
                                f'<br><span style="color:#5d7a99;font-size:11px">r={r_val:+.3f}</span>'
                                if r_val is not None else
                                f'<span style="color:{col};font-weight:bold">{lbl}</span>'
                                f'<span style="color:{col}"> d={d_val:+.2f}</span>')
                    else:
                        col2 = "#2ecc71" if (r_val is not None and r_val > 0) else (
                               "#e74c3c" if r_val is not None else "#5d7a99")
                        cell = (f'{r_val:+.3f}' if r_val is not None else '&ndash;')
                        cell = f'<span style="color:{col2}">{cell}</span>'
                    h += f'<td style="padding:3px 9px;text-align:right">{cell}</td>'
                h += "</tr>"
            return (h + "</tbody></table>"
                    "<p style='color:#8aa1b8;font-size:11px;margin-top:6px'>Die-level values use "
                    "Cohen d for strength (raw r is compressed for rare bins). Same sign across "
                    "bins = shared driver direction; compare magnitudes for relative importance.</p>")
        method_names = {"pearson": "Die-level (Pearson)", "pcm_lot": "PCM Lot-Level"}
        body = ""
        for m, md in dep["methods"].items():
            top = md["pairs"][0] if md["pairs"] else None
            hi = ""
            if top and top["similarity"] is not None and top["similarity"] >= 0.5:
                hi = (f"<div style='margin:6px 0;padding:8px 12px;border:1px solid #a5641a;"
                      f"background:#2a1e0d;color:#f0d8b0;border-radius:6px'>Most similar: "
                      f"<b>{_h.escape(top['a'])} &harr; {_h.escape(top['b'])}</b> "
                      f"(similarity {top['similarity']:+.2f}, {top['n_shared']} shared params) "
                      f"&mdash; these bins share drivers, likely a common root cause.</div>")
            body += (f"<h2>{method_names.get(m, m)} &mdash; driver similarity</h2>{hi}"
                     f"{matrix_html(md['matrix'])}"
                     f"<h3 style='color:#7fb3d3'>Top drivers of {_h.escape(md['base'])} vs the others</h3>"
                     f"{side_html(md['side_by_side'])}")
        explain = (
            "<div style='background:#0d1f10;border:2px solid #27ae60;border-radius:8px;padding:14px 18px;margin:16px 0'>"
            "<h3 style='color:#2ecc71;margin:0 0 10px 0;font-size:15px'>&#128214; How to read this report</h3>"

            "<div style='background:#0a1520;border-left:4px solid #3498db;border-radius:0 6px 6px 0;"
            "padding:10px 14px;margin-bottom:12px'>"
            "<b style='color:#7fb3d3;font-size:13px'>What question does this answer?</b><br>"
            "<span style='color:#c8d6e8;font-size:12px;line-height:1.7'>"
            "IB (Interface Bin) categories are <b>mutually exclusive</b> &mdash; every die lands in "
            "exactly one IB. That means you cannot ask &ldquo;did the same dies fail both bins&rdquo; "
            "(they can&rsquo;t). Instead this analysis asks: <b>are the bins driven by the same "
            "input parameters?</b> If yes, they share a root cause even though different dies are affected."
            "</span></div>"

            "<div style='background:#0a1520;border-left:4px solid #e74c3c;border-radius:0 6px 6px 0;"
            "padding:10px 14px;margin-bottom:12px'>"
            "<b style='color:#e8a0a0;font-size:13px'>&#9632; Similarity Matrix &mdash; read this first</b><br>"
            "<span style='color:#c8d6e8;font-size:12px;line-height:1.7'>"
            "For each bin a <i>driver profile</i> is built: a vector of {parameter &rarr; correlation r} "
            "values across all shared parameters. The matrix cell (A, B) is Pearson r computed over "
            "those two r-vectors.<br><br>"
            "<b>+1.0</b> &nbsp;&#8594;&nbsp; identical drivers and directions &mdash; almost certainly the <b>same root cause</b>.<br>"
            "<b>&nbsp;0.0</b> &nbsp;&#8594;&nbsp; unrelated failure mechanisms.<br>"
            "<b>&minus;1.0</b> &nbsp;&#8594;&nbsp; opposite drivers (one bin benefits where the other fails).<br><br>"
            "<b style='color:#e74c3c'>Red cell |sim| &ge; 0.6</b> &rarr; strong shared mechanism &mdash; investigate as one problem.<br>"
            "<b style='color:#e67e22'>Orange cell 0.3&ndash;0.6</b> &rarr; moderate overlap &mdash; partial common root cause.<br>"
            "<b style='color:#5d7a99'>Dim cell &lt; 0.3</b> &rarr; weak or no overlap &mdash; likely independent failure modes."
            "</span></div>"

            "<div style='background:#0a1520;border-left:4px solid #f39c12;border-radius:0 6px 6px 0;"
            "padding:10px 14px;margin-bottom:12px'>"
            "<b style='color:#f5cba7;font-size:13px'>&#9632; Side-by-Side Table &mdash; pinpoint the shared driver</b><br>"
            "<span style='color:#c8d6e8;font-size:12px;line-height:1.7'>"
            "Shows the <b>top parameters of the reference bin</b> alongside every other bin&rsquo;s "
            "correlation and Cohen&nbsp;d for the <i>same parameter</i>.<br><br>"
            "<b>How to interpret each row:</b><br>"
            "&bull; <b>Same sign, similar magnitude</b> across all bins &rarr; shared driver confirmed. "
            "Prioritise this parameter for root-cause investigation.<br>"
            "&bull; <b>Same sign in only some bins</b> &rarr; partial driver. That subset of bins may share a cause "
            "while the others differ.<br>"
            "&bull; <b>Opposite signs</b> &rarr; the parameter pushes bins in different directions &mdash; "
            "likely different mechanisms despite surface similarity.<br>"
            "&bull; <b>Large Cohen&nbsp;d + small r</b> &rarr; rare-fail regime; d is the reliable effect-size "
            "metric here, not r.<br><br>"
            "The table is shown for both <b>die-level (Pearson)</b> and <b>PCM Lot-Level</b> profiles. "
            "Agreement across both levels greatly increases confidence."
            "</span></div>"

            "<div style='background:#0a1520;border-left:4px solid #9b59b6;border-radius:0 6px 6px 0;"
            "padding:10px 14px'>"
            "<b style='color:#d7bde2;font-size:13px'>&#9632; Worked example</b><br>"
            "<span style='color:#c8d6e8;font-size:12px;line-height:1.7'>"
            "IB3 vs IB42 similarity = <b style='color:#e74c3c'>+0.71</b>. "
            "Side-by-side shows <code>PTH_POWER_CJ816P::POWER_X</code> is <b>medium d=&minus;0.60</b> in IB3 "
            "and <b>small d=&minus;0.48</b> in IB42 &mdash; same negative direction, similar magnitude. "
            "<b>Conclusion:</b> both bins are partially driven by low power-rail margin; investigate that "
            "parameter first before treating the bins as independent problems."
            "</span></div>"

            "</div>")
        page = ("<!doctype html><html><head><meta charset='utf-8'><title>Bin Driver Similarity</title>"
                "<style>body{background:#0b1622;color:#dce8f3;font-family:Segoe UI,Arial,sans-serif;padding:18px}"
                "h1{color:#9fd2ff;font-size:20px}h2{color:#7fb3d3;font-size:15px;margin-top:20px}"
                "th{background:#0f2030}td,th{border:1px solid #1e3a5f}</style></head><body>"
                "<h1>Bin Driver Similarity</h1>"
                f"<div style='color:#8aa1b8;font-size:12px'>Selected bins: {_h.escape(', '.join(L))}</div>"
                f"{explain}{body}</body></html>")
        out_path = os.path.join(str(out_dir), "bin_dependency.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(page)
        return out_path

    def _build_target_specs(self):
        if self._mode_var.get() == "IB":
            specs = []
            for ib in self._bin_combo.checkedIBs():
                fbs = self._bin_combo.checkedFBsForIB(ib)
                fb_part = ",".join(str(f) for f in fbs) if fbs else ""
                specs.append(f"IB=={ib}|FBS={fb_part}")
            return specs
        else:
            return [f"FB=={fb}" for fb in self._bin_combo.checkedFBs()]

    # --- data merge ---

    def _merge_data(self, df, selected, target_spec,
                    ignore_ib_text="", ignore_fb_text="", pcm_map=None):
        if pcm_map is None:
            pcm_map = {}
        df = df.copy()
        ib_col = _detect_ib_col(df.columns)
        fb_col = _detect_fb_col(df.columns)
        ib_num = pd.to_numeric(df[ib_col], errors="coerce") if ib_col else None
        fb_num = pd.to_numeric(df[fb_col], errors="coerce") if fb_col else None

        if ignore_ib_text and ib_col and ib_num is not None:
            ignore_ibs = {int(t.strip()) for t in ignore_ib_text.split(",")
                          if t.strip().lstrip("-").isdigit()}
            if ignore_ibs:
                before = len(df)
                df = df[~ib_num.isin(ignore_ibs)].copy()
                ib_num = pd.to_numeric(df[ib_col], errors="coerce")
                fb_num = pd.to_numeric(df[fb_col], errors="coerce") if fb_col else None
                self.after(0, lambda msg=f"  Excluded IB {sorted(ignore_ibs)}: "
                           f"{before-len(df):,} rows removed": self._log_line(msg))

        if ignore_fb_text and fb_col:
            ignore_fbs = {int(t.strip()) for t in ignore_fb_text.split(",")
                          if t.strip().lstrip("-").isdigit()}
            if ignore_fbs:
                fb_num_cur = pd.to_numeric(df[fb_col], errors="coerce")
                before = len(df)
                df = df[~fb_num_cur.isin(ignore_fbs)].copy()
                fb_num = pd.to_numeric(df[fb_col], errors="coerce")
                ib_num = pd.to_numeric(df[ib_col], errors="coerce") if ib_col else None
                self.after(0, lambda msg=f"  Excluded FB {sorted(ignore_fbs)}: "
                           f"{before-len(df):,} rows removed": self._log_line(msg))

        if target_spec.startswith("FB=="):
            target_fb = int(target_spec.split("==")[1])
            df["_TARGET"] = (fb_num == target_fb).astype(int) if fb_num is not None else 0
        elif target_spec.startswith("IB=="):
            ib_part, *fbs_part = target_spec.split("|FBS=")
            target_ib = int(ib_part.split("==")[1])
            # IB mode: keep ALL units; _TARGET=1 for units with target IB, 0 for all others.
            # Do NOT pre-filter by FBS — that would leave only target-IB rows → target all 1s → zero variance.
            df["_TARGET"] = (ib_num == target_ib).astype(int) if ib_num is not None else 0
            n_hits = int(df["_TARGET"].sum())
            n_total = len(df)
            self.after(0, lambda msg=f"  IB=={target_ib}: {n_hits:,} hits / {n_total:,} total  "
                       f"(FBS info: {fbs_part[0] if fbs_part else 'all'})":
                       self._log_line(msg))
        else:
            df["_TARGET"] = fb_num.fillna(0) if fb_num is not None else 0

        # Auto-compute wafer zone from SORT_X / SORT_Y
        _sx = next((c for c in df.columns if c == "SORT_X"), None)
        _sy = next((c for c in df.columns if c == "SORT_Y"), None)
        if _sx and _sy:
            _xs = pd.to_numeric(df[_sx], errors="coerce")
            _ys = pd.to_numeric(df[_sy], errors="coerce")
            _xctr = (_xs.min() + _xs.max()) / 2
            _yctr = (_ys.min() + _ys.max()) / 2
            _xrad = (_xs.max() - _xs.min()) / 2 or 1
            _yrad = (_ys.max() - _ys.min()) / 2 or 1
            df["_radius"] = ((( (_xs - _xctr) / _xrad) ** 2 +
                               ((_ys - _yctr) / _yrad) ** 2) ** 0.5).round(4)
            df["_zone"] = pd.cut(df["_radius"], bins=[-0.01, 0.40, 0.70, 10.0],
                                  labels=[0, 1, 2]).astype(float)
            if "_radius" not in selected:
                selected = list(selected) + ["_radius"]

        pcm_needed = [c for c in selected if c not in df.columns]
        if pcm_needed and pcm_map:
            lc = next((c for c in df.columns if c.lower() in ("lot", "sort_lot")), None)
            # Pre-create all PCM columns at once (avoids DataFrame fragmentation)
            if pcm_needed:
                _new_cols = pd.DataFrame(np.nan, index=df.index, columns=pcm_needed)
                df = pd.concat([df, _new_cols], axis=1).copy()   # .copy() de-fragments
            _idw_ok_cols: set = set()
            _zip_cache: dict = {}     # zip_path -> {lot7: df}, parsed once per merge
            _idw_lot_count = 0        # lots filled via IDW (per-die interpolation)
            _median_lot_count = 0     # lots filled via lot/full-site median fallback
            _pcm_lot_total = 0        # lots that had any usable PCM
            for sort_lot, pcm_files in pcm_map.items():
                try:
                    # Collect and concatenate data from ALL matching zip files for this lot
                    parts = []
                    is_full_site = False
                    _p7 = str(sort_lot)[:7]
                    for zip_path, _is_full, _pcm_lot_id in pcm_files:
                        if zip_path not in _zip_cache:
                            _zip_cache[zip_path] = _read_zip_grouped(zip_path)
                        part = _zip_cache[zip_path].get(_p7, None)
                        if part is not None and not part.empty:
                            parts.append(part)
                        if _is_full:
                            is_full_site = True
                    if not parts:
                        continue
                    df_pcm = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
                    n_zips = len(pcm_files)
                    avail = [c for c in pcm_needed if c in df_pcm.columns
                             and c in set(_etest_cols_from_pcm(df_pcm))]
                    if not avail:
                        continue
                    lot_mask = (df[lc].astype(str) == str(sort_lot)) if lc else pd.Series(True, index=df.index)
                    lot_rows = df[lot_mask]
                    # --- Try IDW (only for 9-site data; full-site already has all sites covered) ---
                    idw_result = None
                    if (not is_full_site
                            and "LayoutX" in df_pcm.columns and "LayoutY" in df_pcm.columns
                            and "SORT_X" in df.columns and "SORT_Y" in df.columns
                            and "Layout" in df_pcm.columns):
                        layout = str(df_pcm["Layout"].dropna().iloc[0]) if len(df_pcm) else ""
                        df_rmap = _load_reticle_map(layout, _RETICLE_DIR) if layout else None
                        if df_rmap is not None:
                            idw_result = _apply_idw_pcm(lot_rows, df_pcm, avail, df_rmap)
                    if idw_result is not None:
                        # Vectorized: assign all IDW params in one block (identical values
                        # to the per-column loop; column order pinned by using `avail` both sides).
                        df.loc[lot_mask, avail] = idw_result[avail].values
                        _idw_ok_cols.update(avail)
                        _idw_lot_count += 1
                        _pcm_lot_total += 1
                        # (per-lot IDW line suppressed — see coverage summary below)
                    else:
                        # Full-site: use site-median per wafer (all sites present, no interpolation needed).
                        # 9-site fallback: lot median (IDW pre-conditions not met).
                        med = df_pcm[avail].median(numeric_only=True)
                        # Vectorized: broadcast the per-param medians across all lot rows at once.
                        df.loc[lot_mask, avail] = med.reindex(avail).astype(float).values
                        _median_lot_count += 1
                        _pcm_lot_total += 1
                        src_tag = "full-site median" if is_full_site else "lot median"
                        # (per-lot median line suppressed — see coverage summary below)
                except Exception as ex:
                    self.after(0, lambda msg=f"  WARN PCM {sort_lot}: {ex}": self._log_line(msg, "warn"))
            self._pcm_idw_cols = _idw_ok_cols
            self._idw_coverage = {
                "idw_lots": _idw_lot_count,
                "median_lots": _median_lot_count,
                "pcm_lots": _pcm_lot_total,
            }
            if _pcm_lot_total:
                _pct = 100.0 * _idw_lot_count / _pcm_lot_total
                self.after(0, lambda msg=f"  PCM fill coverage: IDW {_idw_lot_count}/"
                           f"{_pcm_lot_total} lots ({_pct:.0f}%), median "
                           f"{_median_lot_count}/{_pcm_lot_total}":
                           self._log_line(msg, "ok"))

        meta_cols = [c for c in df.columns if c.lower() in
                     ("lot", "sort_lot", "lot_id", "wafer", "wafer_id",
                      "program", "test_program") or
                     "wafer" in c.lower() or "program" in c.lower() or
                     "devrevstep" in c.lower()]
        keep = list(dict.fromkeys(
            c for c in ["_TARGET", "SORT_X", "SORT_Y", "_radius", "_zone"] + meta_cols + selected
            if c in df.columns))
        # Retain raw FB/IB bin columns so _TARGET can be recomputed for other
        # targets without re-running the expensive merge/IDW (merge-once).
        for _bc in (fb_col, ib_col):
            if _bc and _bc in df.columns and _bc not in keep:
                keep.append(_bc)
        return df[keep]

    def _apply_target(self, merged, target_spec):
        """Recompute ONLY the _TARGET column on an already-merged frame.
        Used for merge-once across multiple targets: the merge/IDW/zone/exclusion
        work is target-independent, so only _TARGET changes between targets.
        Mirrors the _TARGET logic in _merge_data exactly."""
        merged = merged.copy()
        ib_col = _detect_ib_col(merged.columns)
        fb_col = _detect_fb_col(merged.columns)
        ib_num = pd.to_numeric(merged[ib_col], errors="coerce") if ib_col else None
        fb_num = pd.to_numeric(merged[fb_col], errors="coerce") if fb_col else None
        if target_spec.startswith("FB=="):
            target_fb = int(target_spec.split("==")[1])
            merged["_TARGET"] = (fb_num == target_fb).astype(int) if fb_num is not None else 0
        elif target_spec.startswith("IB=="):
            ib_part, *fbs_part = target_spec.split("|FBS=")
            target_ib = int(ib_part.split("==")[1])
            merged["_TARGET"] = (ib_num == target_ib).astype(int) if ib_num is not None else 0
            n_hits = int(merged["_TARGET"].sum())
            self.after(0, lambda msg=f"  IB=={target_ib}: {n_hits:,} hits / {len(merged):,} total":
                       self._log_line(msg))
        else:
            merged["_TARGET"] = fb_num.fillna(0) if fb_num is not None else 0
        return merged

    # --- correlation ---

    def _compute_all_correlations(self, df, selected, target_spec):
        import warnings
        try:
            from scipy.stats import pearsonr, spearmanr
        except ImportError:
            self.after(0, lambda: self._log_line("WARN: scipy not installed", "warn"))
            return {}

        target    = df["_TARGET"].astype(float)
        is_binary = target.nunique() <= 2
        buckets   = {k: [] for k in ["pearson", "spearman"]}

        # Separate structural/spatial covariates from electrical, then drop invalid cols
        _avail = [c for c in list(selected) if c in df.columns]
        _elec_avail, _struct = _tag_structural_covariates(_avail)
        _elec_clean, _dropped = _drop_constant_columns(df, _elec_avail, "_TARGET", verbose=False)
        selected = _elec_clean + [c for c in selected if c not in df.columns]
        if _struct:
            self.after(0, lambda n=len(_struct), s=_struct: self._log_line(
                f"  Structural/spatial covariates ({n}): "
                + ", ".join(s[:6]) + (" ..." if len(s) > 6 else ""), "dim"))
        if _dropped:
            self.after(0, lambda n=len(_dropped), d=_dropped: self._log_line(
                f"  Data quality: {n} col(s) excluded"
                + " \u2014 " + ", ".join(r["param"] for r in d[:6])
                + (" ..." if len(d) > 6 else ""), "warn"))

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = self._compute_correlations_inner(
                df, selected, target, is_binary, buckets, target_spec)
            self._compute_pcm_lot_correlations(df, selected, result)
            self._compute_pcm_wafer_correlations(df, selected, result)
            self._compute_pcm_deviation_correlations(df, selected, result)
            self._compute_pcm_wafer_deviation_correlations(df, selected, result)
            # Annotate signal label and rare-fail separation note (skip private/meta keys)
            for _mk, _m_rows in result.items():
                if _mk.startswith("_") or not isinstance(_m_rows, list):
                    continue
                for _row in _m_rows:
                    if isinstance(_row, dict):
                        _row["signal"] = signal_label(_row.get("r"), _row.get("q"))
            for _row in result.get("pearson", []):
                _row["note"] = separation_note(_row.get("r"), _row.get("d"))
            # Section 2 — Spatial/Structural section (geometry covariates, not electrical)
            if _struct:
                from scipy.stats import pearsonr as _pearsonr
                _struct_rows = []
                _target_s = df["_TARGET"].astype(float)
                for _sc in _struct:
                    if _sc not in df.columns:
                        continue
                    _xs = pd.to_numeric(df[_sc], errors="coerce")
                    _mask = _xs.notna() & _target_s.notna()
                    _n = int(_mask.sum())
                    if _n < 3:
                        continue
                    try:
                        _r, _p = _pearsonr(_xs[_mask].values, _target_s[_mask].values)
                    except Exception:
                        continue
                    _struct_rows.append({"param": _sc, "r": round(float(_r), 6),
                                         "p": float(_p), "n": _n, "kind": "structural"})
                _struct_rows.sort(key=lambda d: abs(d.get("r") or 0), reverse=True)
                result["methods_structural"] = _struct_rows
            # Section 3 — collapse collinear duplicate-stat groups
            for _mk in list(result.keys()):
                if _mk.startswith("_") or not isinstance(result[_mk], list):
                    continue
                result[_mk] = collapse_collinear(result[_mk])
            result["_dropped_columns"] = _dropped
            result["_structural_covariates"] = _struct
            return result

    def _compute_correlations_inner(self, df, selected, target, is_binary,
                                    buckets, target_spec):
        import math
        _DIE_N_FLOOR = 1000   # die-level methods need a real population; below this is noise

        # ── Build per-column valid masks once ─────────────────────────────────
        feat_df = df[[c for c in selected if c in df.columns]].apply(
            pd.to_numeric, errors="coerce")
        t_s = target.astype(float)

        # Identify PCM-no-IDW cols (lot-median fill — skip Pearson/Spearman)
        pcm_no_idw = {c for c in self._pcm_cols_discovered if c not in self._pcm_idw_cols}

        # ── Vectorized Pearson (+ B2 effect size: fail/pass ratio & Cohen d) ────
        pearson_cols = [c for c in feat_df.columns if c not in pcm_no_idw]
        _mf = _mp = _sf = _sp = None; _nf = _np = 0
        if is_binary and pearson_cols:
            _fm = (t_s == 1).values; _pm = (t_s == 0).values
            _subF = feat_df[pearson_cols][_fm]; _subP = feat_df[pearson_cols][_pm]
            _mf = _subF.mean(); _sf = _subF.std(); _nf = int(_fm.sum())
            _mp = _subP.mean(); _sp = _subP.std(); _np = int(_pm.sum())
        if pearson_cols and "pearson" in buckets:
            rs = feat_df[pearson_cols].corrwith(t_s, method="pearson", drop=True)
            for col, r in rs.items():
                if math.isnan(r): continue
                valid = feat_df[col].notna() & t_s.notna()
                n = int(valid.sum())
                if n < _DIE_N_FLOOR: continue   # ignore near-empty columns (noise)
                p_val = _pearson_p(r, n)
                ci_lo, ci_hi = self._pearson_ci(r, n)
                d = ratio = mf = mp = None
                if is_binary and _mf is not None:
                    mf = float(_mf.get(col, float("nan"))); mp = float(_mp.get(col, float("nan")))
                    sf = float(_sf.get(col, float("nan"))); sp = float(_sp.get(col, float("nan")))
                    if not math.isnan(mf) and not math.isnan(mp):
                        denom = _nf + _np - 2
                        if denom > 0 and not math.isnan(sf) and not math.isnan(sp):
                            pooled = math.sqrt((max(_nf-1,0)*sf*sf + max(_np-1,0)*sp*sp)/denom)
                            d = round((mf - mp)/pooled, 4) if pooled > 0 else None
                        ratio = round(mf/mp, 4) if (mp not in (0, None) and not math.isnan(mp) and mp != 0) else None
                buckets["pearson"].append({"param": col, "r": round(float(r), 6),
                                           "p": p_val, "n": n,
                                           "ci_lo": ci_lo, "ci_hi": ci_hi,
                                           "d": d, "ratio": ratio,
                                           "mean_fail": None if (mf is None or math.isnan(mf)) else round(mf,4),
                                           "mean_pass": None if (mp is None or math.isnan(mp)) else round(mp,4)})

        # ── Vectorized Spearman — chunked to avoid OOM on wide frames ────────────
        if pearson_cols and "spearman" in buckets:
            _SP_CHUNK = 30   # 30 cols × 262k rows × 8 bytes ≈ 60 MB peak per chunk
            _sp_parts = [feat_df[pearson_cols[_i:_i+_SP_CHUNK]].corrwith(
                             t_s, method="spearman", drop=True)
                         for _i in range(0, len(pearson_cols), _SP_CHUNK)]
            rs_sp = pd.concat(_sp_parts) if _sp_parts else pd.Series(dtype=float)
            for col, r in rs_sp.items():
                if math.isnan(r): continue
                valid = feat_df[col].notna() & t_s.notna()
                n = int(valid.sum())
                if n < _DIE_N_FLOOR: continue   # ignore near-empty columns (noise)
                p_val = _pearson_p(r, n)  # same t-distribution approximation
                buckets["spearman"].append({"param": col, "r": round(float(r), 6),
                                            "p": p_val, "n": n})

        for key in ["pearson", "spearman", "pcm_lot", "pcm_wafer", "pcm_dev", "pcm_wdev"]:
            if key in buckets:
                buckets[key].sort(key=lambda x: abs(x["r"]), reverse=True)
        # B1: FDR q-values across each die-level family (multiplicity control)
        for key in ("pearson", "spearman"):
            if key in buckets:
                self._annotate_fdr(buckets[key])

        log_lines = [f"\n== Correlation: {target_spec} =="]
        for mk, rows in buckets.items():
            if not rows:
                continue
            label = _CORR_METHODS.get(mk, mk)
            log_lines.append(f"-- {label} (top 5) --")
            for rank, row in enumerate(rows[:5], 1):
                short = row["param"][:70]   # wider: distinct params share long prefixes
                r_val = row["r"]
                dir_  = "^" if r_val > 0 else "v"
                p_    = row.get("p")
                # p can underflow float64 to 0.0 at very large n -> show 'p<1e-308'
                if p_ is None:
                    p_str = ""
                elif p_ == 0.0:
                    p_str = "  p<1e-308"
                else:
                    p_str = f"  p={p_:.2e}"
                s = f"r={r_val:+.4f} {dir_}" + p_str
                log_lines.append(f"  {rank}. {short:<70} {s}  n={row['n']:,}")
        self.after(0, lambda lines=log_lines: [self._log_line(l) for l in lines])
        return buckets

    @staticmethod
    def _bh_fdr(pvals):
        """Benjamini-Hochberg FDR. Returns q-values aligned to input order."""
        import math
        valid = [i for i, p in enumerate(pvals)
                 if p is not None and not (isinstance(p, float) and math.isnan(p))]
        q = [None] * len(pvals)
        m = len(valid)
        if m == 0:
            return q
        order = sorted(valid, key=lambda i: pvals[i])
        prev = 1.0
        for rank, i in enumerate(reversed(order), start=1):
            k = m - rank + 1
            val = min(prev, pvals[i] * m / k)
            prev = val
            q[i] = round(min(val, 1.0), 6)
        return q

    def _annotate_fdr(self, rows):
        """Attach BH FDR q-value to each row dict using its p. In-place."""
        if not rows:
            return
        qs = self._bh_fdr([r.get("p") for r in rows])
        for r, qv in zip(rows, qs):
            r["q"] = qv

    @staticmethod
    def _pearson_ci(r, n, z=1.96):
        """95% CI for Pearson r via Fisher z-transform. Returns (lo, hi) rounded to 3dp."""
        import math
        if n <= 3:
            return None, None
        rc = max(-0.9999, min(0.9999, float(r)))
        zr = math.atanh(rc)
        se = 1.0 / math.sqrt(n - 3)
        return round(math.tanh(zr - z * se), 3), round(math.tanh(zr + z * se), 3)

    def _compute_pcm_lot_correlations(self, df, selected, buckets):
        """Compute Pearson r for PCM params at lot level (pct_target_hit per lot)."""
        import math
        try:
            from scipy.stats import pearsonr
        except ImportError:
            return
        lot_col = next((c for c in df.columns
                        if c.lower() in ("lot", "sort_lot", "lot_id")), None)
        if not lot_col or "_TARGET" not in df.columns:
            return
        pcm_cols = [c for c in selected
                    if c in df.columns and c in self._pcm_cols_discovered]
        if not pcm_cols:
            return

        # Build lot-level table: pct_target + representative PCM per lot.
        # IDW assigns per-die values, so use median (not "first") for a lot-level statistic.
        agg = {"_TARGET": "mean"}
        for c in pcm_cols:
            agg[c] = "median"
        # Coerce median-aggregated PCM cols to numeric (they can be object dtype ->
        # median would silently drop them). .copy() also de-fragments the frame.
        _mcols = [c for c in agg if agg[c] == "median"]
        _num = df[[lot_col] + list(agg.keys())].copy()
        if _mcols:
            _num[_mcols] = _num[_mcols].apply(pd.to_numeric, errors="coerce")
        lot_df = _num.groupby(lot_col).agg(agg).reset_index()
        lot_df = lot_df.dropna(subset=["_TARGET"])
        n_lots = len(lot_df)
        if n_lots < 6:
            self.after(0, lambda: self._log_line(
                f"  PCM Lot-Level: only {n_lots} lot(s) — skipping (need >= 6).", "warn"))
            return

        rows = []
        pct = lot_df["_TARGET"].values.astype(float)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for col in pcm_cols:
                vals = pd.to_numeric(lot_df[col], errors="coerce")
                valid = vals.notna()
                n = int(valid.sum())
                if n < 6:
                    continue
                f = vals[valid].values.astype(float)
                t = pct[valid.values]
                try:
                    r, p = pearsonr(f, t)
                    if not math.isnan(r):
                        ci_lo, ci_hi = self._pearson_ci(r, n)
                        rows.append({"param": col, "r": round(float(r), 6),
                                     "p": None if math.isnan(p) else float(p),
                                     "n": n,
                                     "ci_lo": ci_lo, "ci_hi": ci_hi})
                except Exception:
                    pass

        rows.sort(key=lambda x: abs(x["r"]), reverse=True)
        self._annotate_fdr(rows)
        buckets["pcm_lot"] = rows
        self.after(0, lambda msg=f"  PCM Lot-Level: {len(rows)} params across {n_lots} lots":
                   self._log_line(msg, "ok"))

    def _compute_pcm_wafer_correlations(self, df, selected, buckets):
        """Pearson r for PCM params at wafer level (pct_target_hit per wafer)."""
        import math
        try:
            from scipy.stats import pearsonr
        except ImportError:
            return
        lot_col   = next((c for c in df.columns
                          if c.lower() in ("lot", "sort_lot", "lot_id")), None)
        wafer_col = next((c for c in df.columns if "wafer" in c.lower()), None)
        if not wafer_col or "_TARGET" not in df.columns:
            return
        group_cols = [c for c in [lot_col, wafer_col] if c]
        pcm_cols = [c for c in selected
                    if c in df.columns and c in self._pcm_cols_discovered]
        if not pcm_cols:
            return

        agg = {"_TARGET": "mean"}
        for c in pcm_cols:
            agg[c] = "median"
        _mcols = [c for c in agg if agg[c] == "median"]
        _num = df[group_cols + [c for c in agg if c not in group_cols]].copy()
        if _mcols:
            _num[_mcols] = _num[_mcols].apply(pd.to_numeric, errors="coerce")
        wfr_df = _num.groupby(group_cols).agg(agg).reset_index()
        wfr_df = wfr_df.dropna(subset=["_TARGET"])
        n_wafers = len(wfr_df)
        if n_wafers < 6:
            self.after(0, lambda: self._log_line(
                f"  PCM Wafer-Level: only {n_wafers} wafer(s) — skipping (need >= 6).", "warn"))
            return

        rows = []
        pct = wfr_df["_TARGET"].values.astype(float)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for col in pcm_cols:
                vals = pd.to_numeric(wfr_df[col], errors="coerce")
                valid = vals.notna()
                n = int(valid.sum())
                if n < 6:
                    continue
                f = vals[valid].values.astype(float)
                t = pct[valid.values]
                try:
                    r, p = pearsonr(f, t)
                    if not math.isnan(r):
                        ci_lo, ci_hi = self._pearson_ci(r, n)
                        rows.append({"param": col, "r": round(float(r), 6),
                                     "p": None if math.isnan(p) else float(p),
                                     "n": n,
                                     "ci_lo": ci_lo, "ci_hi": ci_hi})
                except Exception:
                    pass

        rows.sort(key=lambda x: abs(x["r"]), reverse=True)
        self._annotate_fdr(rows)
        buckets["pcm_wafer"] = rows
        self.after(0, lambda msg=f"  PCM Wafer-Level: {len(rows)} params across {n_wafers} wafers":
                   self._log_line(msg, "ok"))

    def _compute_pcm_deviation_correlations(self, df, selected, buckets):
        """Pearson r between |x - median(x)| and target — detects U-shaped / non-monotonic PCM effects."""
        import math
        try:
            from scipy.stats import pearsonr
        except ImportError:
            return
        lot_col = next((c for c in df.columns
                        if c.lower() in ("lot", "sort_lot", "lot_id")), None)
        if not lot_col or "_TARGET" not in df.columns:
            return
        pcm_cols = [c for c in selected
                    if c in df.columns and c in self._pcm_cols_discovered]
        if not pcm_cols:
            return
        agg = {"_TARGET": "mean"}
        for c in pcm_cols:
            agg[c] = "median"   # IDW -> per-die values; median = lot representative
        # Coerce median-aggregated PCM cols to numeric (they can be object dtype ->
        # median would silently drop them). .copy() also de-fragments the frame.
        _mcols = [c for c in agg if agg[c] == "median"]
        _num = df[[lot_col] + list(agg.keys())].copy()
        if _mcols:
            _num[_mcols] = _num[_mcols].apply(pd.to_numeric, errors="coerce")
        lot_df = _num.groupby(lot_col).agg(agg).reset_index()
        lot_df = lot_df.dropna(subset=["_TARGET"])
        if len(lot_df) < 6:
            return
        rows = []
        pct = lot_df["_TARGET"].values.astype(float)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for col in pcm_cols:
                vals = pd.to_numeric(lot_df[col], errors="coerce")
                valid = vals.notna()
                n = int(valid.sum())
                if n < 6:
                    continue
                f_raw = vals[valid].values.astype(float)
                med = float(pd.Series(f_raw).median())
                f_dev = abs(f_raw - med)   # |x - median|
                t = pct[valid.values]
                try:
                    r, p = pearsonr(f_dev, t)
                    if not math.isnan(r):
                        ci_lo, ci_hi = self._pearson_ci(r, n)
                        rows.append({"param": col, "r": round(float(r), 6),
                                     "p": None if math.isnan(p) else float(p),
                                     "n": n,
                                     "ci_lo": ci_lo, "ci_hi": ci_hi})
                except Exception:
                    pass
        rows.sort(key=lambda x: abs(x["r"]), reverse=True)
        self._annotate_fdr(rows)
        buckets["pcm_dev"] = rows
        self.after(0, lambda msg=f"  PCM Deviation: {len(rows)} params ({len(lot_df)} lots)":
                   self._log_line(msg, "ok"))

    def _compute_pcm_wafer_deviation_correlations(self, df, selected, buckets):
        """Within-lot wafer deviation + within-wafer spread vs wafer %fail (Option B).
        wafer value = median PCM of its dies; lot center = median of wafer-values in that lot;
        deviation = |wafer value - lot center|. Secondary: die-to-die std per wafer (spread)."""
        import math, warnings
        try:
            from scipy.stats import pearsonr
        except ImportError:
            return
        lot_col   = next((c for c in df.columns
                          if c.lower() in ("lot", "sort_lot", "lot_id")), None)
        wafer_col = next((c for c in df.columns if "wafer" in c.lower()), None)
        if not lot_col or not wafer_col or "_TARGET" not in df.columns:
            return
        pcm_cols = [c for c in selected
                    if c in df.columns and c in self._pcm_cols_discovered]
        if not pcm_cols:
            return
        MIN_WAF = 4   # minimum wafers per lot for a stable lot center
        group_cols = [lot_col, wafer_col]
        _num = df[group_cols + ["_TARGET"] + pcm_cols].copy()
        _num[pcm_cols] = _num[pcm_cols].apply(pd.to_numeric, errors="coerce")
        agg = {"_TARGET": "mean"}
        for c in pcm_cols:
            agg[c] = ["median", "std"]
        wfr = _num.groupby(group_cols).agg(agg)
        wfr.columns = ["_TARGET" if a == "_TARGET" else f"{a}__{b}"
                       for a, b in wfr.columns]
        wfr = wfr.reset_index().dropna(subset=["_TARGET"])
        rows = []
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for col in pcm_cols:
                mcol, scol = f"{col}__median", f"{col}__std"
                if mcol not in wfr.columns:
                    continue
                sub = wfr[[lot_col, "_TARGET", mcol, scol]].copy()
                sub[mcol] = pd.to_numeric(sub[mcol], errors="coerce")
                sub = sub[sub[mcol].notna() & sub["_TARGET"].notna()]
                # keep only lots with >= MIN_WAF wafers
                cnt = sub.groupby(lot_col)[mcol].transform("count")
                sub = sub[cnt >= MIN_WAF]
                if len(sub) < 6:
                    continue
                lot_center = sub.groupby(lot_col)[mcol].transform("median")
                dev = (sub[mcol] - lot_center).abs()
                d = dev.values.astype(float)
                t = sub["_TARGET"].values.astype(float)
                n = len(d)
                if n < 6 or float(pd.Series(d).std()) == 0:
                    continue
                try:
                    r, p = pearsonr(d, t)
                except Exception:
                    continue
                if math.isnan(r):
                    continue
                ci_lo, ci_hi = self._pearson_ci(r, n)
                # secondary: within-wafer spread (die-to-die std) vs %fail
                spread_r = None
                sv = pd.to_numeric(sub[scol], errors="coerce")
                mask = sv.notna()
                if int(mask.sum()) >= 6 and float(sv[mask].std()) > 0:
                    try:
                        sr, _sp = pearsonr(sv[mask].values.astype(float),
                                           sub["_TARGET"][mask].values.astype(float))
                        if not math.isnan(sr):
                            spread_r = round(float(sr), 4)
                    except Exception:
                        pass
                rows.append({"param": col, "r": round(float(r), 6),
                             "p": None if math.isnan(p) else float(p), "n": n,
                             "ci_lo": ci_lo, "ci_hi": ci_hi, "spread_r": spread_r})
        rows.sort(key=lambda x: abs(x["r"]), reverse=True)
        self._annotate_fdr(rows)
        buckets["pcm_wdev"] = rows
        self.after(0, lambda msg=f"  PCM Deviation (Wafer): {len(rows)} params":
                   self._log_line(msg, "ok"))

    def _compute_zone_interaction(self, df, selected, corr_data=None, top_n=30):
        """Compute zone fail rates and zone×quartile cross-tabs.
        Capped to the top_n params by |Pearson r| for speed (params with r≈0
        have no meaningful zone signal)."""
        if "_zone" not in df.columns or "_TARGET" not in df.columns:
            return {}
        zone_labels = {0.0: "Center", 1.0: "Mid", 2.0: "Edge"}
        # Zone fail rates
        zfr = []
        for zv, zlbl in zone_labels.items():
            mask = df["_zone"] == zv
            n = int(mask.sum())
            if n == 0:
                continue
            fr = float(df.loc[mask, "_TARGET"].mean())
            zfr.append({"zone": zlbl, "fail_rate": round(fr, 6), "n": n})
        # Limit zone interaction to sort-CSV params (UPM/SICC/CDYN) — PCM has too many cols
        sort_kw = ("UPM", "SICC", "CDYN")
        upm_cols = [c for c in selected
                    if any(k in c.upper() for k in sort_kw) and c in df.columns]
        etest_cols = [c for c in selected
                      if not any(k in c.upper() for k in sort_kw) and c != "_radius"
                      and c in df.columns
                      and pd.api.types.is_numeric_dtype(df[c])]
        zone_cols = upm_cols + etest_cols
        # Rank by |Pearson r| and keep only the top_n (speed: skip r≈0 params).
        if corr_data and corr_data.get("pearson"):
            _rank = {row["param"]: abs(row.get("r", 0) or 0)
                     for row in corr_data["pearson"]}
            zone_cols = sorted(zone_cols, key=lambda c: _rank.get(c, 0.0), reverse=True)[:top_n]
            self.after(0, lambda n=len(zone_cols): self._log_line(
                f"  Zone interaction: top {n} params by |r| (of {len(upm_cols)+len(etest_cols)} eligible)", "dim"))
        interactions = []
        _q_labels = ["Q1 (low)", "Q2", "Q3", "Q4 (high)"]
        for col in zone_cols:
            try:
                vals = pd.to_numeric(df[col], errors="coerce")
                valid = vals.notna() & df["_zone"].notna() & df["_TARGET"].notna()
                if valid.sum() < 20:
                    continue
                # dynamic labels: qcut may produce fewer bins when values have few uniques
                try:
                    cuts, bins = pd.qcut(vals[valid], q=4, labels=False,
                                         duplicates="drop", retbins=True)
                    n_bins = len(bins) - 1
                    if n_bins < 2:
                        # lot-median fallback: derive bins from unique values, apply via pd.cut
                        uniq = np.sort(vals[valid].dropna().unique())
                        if len(uniq) < 2:
                            continue
                        q_n = min(4, len(uniq))
                        _, fallback_bins = pd.qcut(uniq, q=q_n, duplicates="drop", retbins=True)
                        n_bins = len(fallback_bins) - 1
                        if n_bins < 2:
                            continue
                        bin_labels = _q_labels[:n_bins]
                        q_series = pd.cut(vals[valid], bins=fallback_bins,
                                          labels=bin_labels, include_lowest=True)
                    else:
                        bin_labels = _q_labels[:n_bins]
                        q_series = pd.qcut(vals[valid], q=n_bins, labels=bin_labels,
                                           duplicates="drop")
                except (ValueError, TypeError):
                    continue
                df_v = df[valid].copy()
                df_v["_q"] = q_series
                cells = []
                for zv, zlbl in zone_labels.items():
                    for qlbl in bin_labels:
                        m2 = (df_v["_zone"] == zv) & (df_v["_q"] == qlbl)
                        n2 = int(m2.sum())
                        if n2 == 0:
                            continue
                        fr2 = float(df_v.loc[m2, "_TARGET"].mean())
                        cells.append({"zone": zlbl, "upm_q": qlbl,
                                      "fail_rate": round(fr2, 6), "n": n2})
                if cells:
                    interactions.append({"param": col, "cells": cells})
            except Exception:
                pass
        return {"zone_fail_rate": zfr, "interactions": interactions}

    def _compute_intrafield_fails(self, df, reticle_dir, src_csv_paths=None):
        """Intra-field fail analysis with WITHIN-SHOT pairing (removes shot/wafer noise).
        Also writes _intra_rdx/_intra_rdy/_intra_shot/_intra_loc onto df for the
        parameter cross-view."""
        import math, os, glob
        need = {"DieX","DieY","ReticleDieX","ReticleDieY"}
        if "_TARGET" not in df.columns or "SORT_X" not in df.columns or "SORT_Y" not in df.columns:
            return None
        rmap = None
        try:
            for f in sorted(glob.glob(os.path.join(reticle_dir, "*.csv"))):
                cols = set(pd.read_csv(f, nrows=0).columns)
                if need.issubset(cols):
                    use = list(need | ({"ReticleShot","Device"} & cols))
                    rmap = pd.read_csv(f, usecols=use); break
        except Exception:
            rmap = None
        if rmap is None or rmap.empty:
            return None
        for c in ("DieX","DieY","ReticleDieX","ReticleDieY"):
            rmap[c] = pd.to_numeric(rmap[c], errors="coerce")
        rmap = rmap.dropna(subset=["DieX","DieY","ReticleDieX","ReticleDieY"]).copy()
        rmap = rmap.astype({"DieX":int,"DieY":int,"ReticleDieX":int,"ReticleDieY":int})
        has_shot = "ReticleShot" in rmap.columns
        lut = {}
        for r in rmap.itertuples(index=False):
            lut[(int(r.DieX), int(r.DieY))] = (int(r.ReticleDieX), int(r.ReticleDieY),
                                               str(getattr(r, "ReticleShot", "")) if has_shot else "")
        # stable DieLoc# ordering: row-major by rdy then rdx
        locs = sorted({(v[0], v[1]) for v in lut.values()}, key=lambda p: (p[1], p[0]))
        loc_num = {p: i+1 for i, p in enumerate(locs)}
        sx = pd.to_numeric(df["SORT_X"], errors="coerce").values
        sy = pd.to_numeric(df["SORT_Y"], errors="coerce").values
        tgt = pd.to_numeric(df["_TARGET"], errors="coerce").values
        rdx=[]; rdy=[]; shot=[]; loc=[]
        for x, y in zip(sx, sy):
            p = lut.get((int(x), int(y))) if not (pd.isna(x) or pd.isna(y)) else None
            if p is None:
                rdx.append(None); rdy.append(None); shot.append(None); loc.append(None)
            else:
                rdx.append(p[0]); rdy.append(p[1]); shot.append(p[2]); loc.append(loc_num[(p[0],p[1])])
        df["_intra_rdx"]=rdx; df["_intra_rdy"]=rdy; df["_intra_shot"]=shot; df["_intra_loc"]=loc
        # ---- COVERAGE DIAGNOSTIC (logging only; does not affect results) ----
        try:
            _mapped_mask = pd.Series([l is not None for l in loc], index=df.index)
            _n_map = int(_mapped_mask.sum()); _n_tot = len(df)
            self.after(0, lambda a=_n_map, b=_n_tot:
                       self._log_line(f"  [coverage] mapped {a:,}/{b:,} dies "
                                      f"({100*a/max(b,1):.1f}%)", "ok" if a else "warn"))
            _devcol = next((c for c in df.columns
                            if c.lower() in ("device","layout","devrevstep")
                            or "device" in c.lower() or "layout" in c.lower()), None)
            if _devcol:
                _tmp = pd.DataFrame({"dev": df[_devcol].astype(str).values,
                                     "m": _mapped_mask.values})
                _g = _tmp.groupby("dev")["m"].agg(["sum","count"])
                _g = _g.sort_values("count", ascending=False).head(12)
                self.after(0, lambda: self._log_line(
                    "  [coverage] mapped by device/layout:", "dim"))
                for _dev, _row in _g.iterrows():
                    s_=int(_row["sum"]); c_=int(_row["count"]); p_=100*s_/max(c_,1)
                    self.after(0, lambda d=_dev, s=s_, c=c_, p=p_:
                               self._log_line(f"      {d:<14} {s:>8,}/{c:>8,}  ({p:5.1f}%)",
                                              "ok" if s else "warn"))
            try:
                _sxv = pd.to_numeric(df["SORT_X"], errors="coerce")
                _syv = pd.to_numeric(df["SORT_Y"], errors="coerce")
                _mx = [k[0] for k in lut.keys()]; _my = [k[1] for k in lut.keys()]
                self.after(0, lambda x0=_sxv.min(), x1=_sxv.max(), y0=_syv.min(), y1=_syv.max(),
                                  mx0=min(_mx), mx1=max(_mx), my0=min(_my), my1=max(_my):
                           self._log_line(
                               f"  [coverage] SORT_X range [{x0:.0f},{x1:.0f}] vs map DieX "
                               f"[{mx0},{mx1}] ; SORT_Y [{y0:.0f},{y1:.0f}] vs map DieY "
                               f"[{my0},{my1}]", "dim"))
                _un = df.loc[~_mapped_mask, ["SORT_X","SORT_Y"]].head(5)
                if len(_un):
                    _pairs = ", ".join(f"({int(r.SORT_X)},{int(r.SORT_Y)})"
                                       for r in _un.itertuples(index=False)
                                       if pd.notna(r.SORT_X) and pd.notna(r.SORT_Y))
                    self.after(0, lambda s=_pairs:
                               self._log_line(f"  [coverage] sample UNMAPPED (SORT_X,SORT_Y): {s}", "dim"))
                    _mk = ", ".join(f"({k[0]},{k[1]})" for k in list(lut.keys())[:5])
                    self.after(0, lambda s=_mk:
                               self._log_line(f"  [coverage] sample MAP (DieX,DieY): {s}", "dim"))
            except Exception:
                pass
        except Exception as _ex:
            self.after(0, lambda e=_ex: self._log_line(f"  [coverage] diag skipped: {e}", "warn"))
        # ---- END COVERAGE DIAGNOSTIC ----
        fld = pd.DataFrame({"rdx":rdx,"rdy":rdy,"loc":loc,"shot":shot,"t":tgt}).dropna(subset=["loc","t"])
        n_mapped = len(fld)
        self.after(0, lambda m=n_mapped, N=len(df):
                   self._log_line(f"  Intra-field: {m:,} dies with a resolved reticle-die "
                                  f"position for this target "
                                  f"({100*m/max(N,1):.0f}% of {N:,} total dies; this is the "
                                  f"analyzable set for this bin, not a coverage loss).",
                                  "ok" if m else "warn"))
        if fld.empty:
            return None
        overall = float(fld["t"].mean()); N = len(fld)
        # ---- pooled per-DieLoc stats + relative risk ----
        cells = []
        for lc, g in fld.groupby("loc"):
            n=len(g); fr=float(g["t"].mean())
            z=None
            if 0<overall<1 and n>=20:
                se=math.sqrt(overall*(1-overall)*(1.0/n+1.0/max(N-n,1)))
                z=(fr-overall)/se if se>0 else None
            rr = round(fr/overall, 3) if overall>0 else None
            sig = bool(z is not None and abs(z)>=2 and rr is not None and rr>=1.2)
            rr0 = g.iloc[0]
            cells.append({"loc":int(lc), "rdx":int(rr0["rdx"]), "rdy":int(rr0["rdy"]),
                          "n":n, "fail_rate":round(fr,6), "z":(round(z,3) if z is not None else None),
                          "rr":rr, "significant":sig})
        cells.sort(key=lambda c:c["fail_rate"], reverse=True)
        # ---- WITHIN-(wafer,shot) STRATIFIED rate-difference (removes wafer+shot noise) ----
        paired = None
        wafer_col = next((c for c in df.columns if "wafer" in c.lower()), None)
        fld2 = fld.copy()
        if wafer_col and wafer_col in df.columns:
            fld2["waf"] = pd.Series(df[wafer_col].values, index=df.index).reindex(fld2.index).values
            fld2 = fld2.dropna(subset=["waf"])
            fld2["stratum"] = fld2["waf"].astype(str) + "|" + fld2["shot"].astype(str)
            strat_label = "(wafer x shot)"
        elif fld2["shot"].notna().any():
            fld2 = fld2.dropna(subset=["shot"])
            fld2["stratum"] = fld2["shot"].astype(str)
            strat_label = "(shot only - no wafer column)"
        else:
            fld2 = None
            strat_label = ""
        if fld2 is not None and len(fld2) > 0:
            g = fld2.groupby(["stratum", "loc"])["t"].agg(["mean", "count"]).reset_index()
            srate = fld2.groupby("stratum")["t"].mean().rename("srate")
            g = g.join(srate, on="stratum")
            g["diff"] = g["mean"] - g["srate"]
            n_strata = int(fld2["stratum"].nunique())
            rows_out = []
            for lc, gl in g.groupby("loc"):
                w = gl["count"].values.astype(float)
                d = gl["diff"].values.astype(float)
                if w.sum() <= 0 or len(gl) < 2:
                    continue
                wm = float(np.average(d, weights=w))
                var = float(np.average((d - wm) ** 2, weights=w))
                se = math.sqrt(var / max(len(gl), 1)) if var > 0 else 0.0
                z = (wm / se) if se > 0 else None
                rows_out.append({"loc": int(lc),
                                 "mean_dev": round(wm, 6),
                                 "z": (round(z, 3) if z is not None else None),
                                 "n_strata": len(gl)})
            rows_out.sort(key=lambda r: (r["mean_dev"] if r["mean_dev"] is not None else -9), reverse=True)
            if rows_out:
                paired = {"method": "stratified_rate_diff", "strata": strat_label,
                          "n_strata": n_strata, "rows": rows_out}
        # ---- CONSISTENCY across views (flag real-but-modest signals) ----
        consistency = None
        try:
            views = {}
            if cells:
                views["pooled_failrate"] = max(cells, key=lambda c: c["fail_rate"])["loc"]
                views["relative_risk"]   = max(cells, key=lambda c: (c.get("rr") or 0))["loc"]
            if paired and paired.get("rows"):
                pr = [r for r in paired["rows"] if r.get("mean_dev") is not None]
                if pr:
                    views["stratified"] = max(pr, key=lambda r: r["mean_dev"])["loc"]
            if views:
                from collections import Counter as _Counter
                tally = _Counter(views.values())
                loc, cnt = tally.most_common(1)[0]
                consistency = {"views": views, "worst_loc": int(loc),
                               "agree": int(cnt), "n_views": len(views)}
        except Exception:
            consistency = None
        return {"overall":round(overall,6), "n_total":N, "n_positions":len(loc_num),
                "cells":cells, "paired":paired, "consistency":consistency}

    def _compute_intrafield_fb_matrix(self, df, target_spec, min_fb_hits=100, max_fbs=12):
        """For an IB target, run stratified (wafer×shot) DieLoc rate-diff per constituent FB."""
        import math
        if not target_spec.startswith("IB=="):
            return None
        if "_intra_loc" not in df.columns or "_intra_shot" not in df.columns:
            return None
        fb_col = _detect_fb_col(df.columns)
        if not fb_col or fb_col not in df.columns:
            return None
        wafer_col = next((c for c in df.columns if "wafer" in c.lower()), None)
        base = df[df["_intra_loc"].notna()].copy()
        if base.empty:
            return None
        fbnum = pd.to_numeric(base[fb_col], errors="coerce")
        if wafer_col and wafer_col in base.columns:
            strat = base[wafer_col].astype(str) + "|" + base["_intra_shot"].astype(str)
        else:
            strat = base["_intra_shot"].astype(str)
        base = base.assign(_fb=fbnum, _stratum=strat.values)
        locs = sorted([int(x) for x in base["_intra_loc"].dropna().unique()])
        hit = base[pd.to_numeric(base["_TARGET"], errors="coerce") == 1]
        fb_counts = hit["_fb"].value_counts()
        fbs = [int(fb) for fb, c in fb_counts.items() if c >= min_fb_hits][:max_fbs]
        if not fbs:
            return None

        def _strat_z_for(binary):
            tmp = base.assign(_b=binary.values)
            g = tmp.groupby(["_stratum", "_intra_loc"])["_b"].agg(["mean", "count"]).reset_index()
            sr = tmp.groupby("_stratum")["_b"].mean().rename("sr")
            g = g.join(sr, on="_stratum")
            g["d"] = g["mean"] - g["sr"]
            out = {}
            for lc, gl in g.groupby("_intra_loc"):
                w = gl["count"].values.astype(float)
                d = gl["d"].values.astype(float)
                if w.sum() <= 0 or len(gl) < 2:
                    continue
                wm = float(np.average(d, weights=w))
                var = float(np.average((d - wm) ** 2, weights=w))
                se = math.sqrt(var / len(gl)) if var > 0 else 0.0
                out[int(lc)] = (round(wm, 6), (round(wm / se, 3) if se > 0 else None))
            return out

        rows = []
        for fb in fbs:
            bin_fb = (base["_fb"] == fb).astype(int)
            zr = _strat_z_for(bin_fb)
            cells = []
            for lc in locs:
                md, z = zr.get(lc, (None, None))
                cells.append({"loc": lc, "mean_dev": md, "z": z})
            rows.append({"fb": fb, "n_hits": int((bin_fb == 1).sum()), "cells": cells})
        best = None
        for r in rows:
            for c in r["cells"]:
                if c["z"] is not None and c["mean_dev"] is not None and c["mean_dev"] > 0:
                    if best is None or c["z"] > best["z"]:
                        best = {"fb": r["fb"], "loc": c["loc"], "z": c["z"], "mean_dev": c["mean_dev"]}
        return {"locs": locs, "rows": rows, "best": best}

    def _compute_intrafield_params(self, df, intrafield, selected, min_n=200):
        """% deviation of DIE-LEVEL params per hot intra-field cell. PCM excluded (scribe-line rule)."""
        import math
        if not intrafield or "_intra_rdx" not in df.columns or "_intra_rdy" not in df.columns:
            return None
        pcm = set(getattr(self, "_pcm_cols_discovered", set()))
        die_cols = [c for c in selected
                    if c in df.columns and c not in pcm and c != "_radius"
                    and pd.api.types.is_numeric_dtype(pd.to_numeric(df[c], errors="coerce"))]
        if not die_cols:
            return None
        sub = df[["_intra_rdx","_intra_rdy"] + die_cols].copy()
        for c in die_cols:
            sub[c] = pd.to_numeric(sub[c], errors="coerce")
        field_mean = sub[die_cols].mean(numeric_only=True)
        field_std  = sub[die_cols].std(numeric_only=True)
        out_cells = []
        for cell in intrafield["cells"][:8]:
            cx, cy = cell["rdx"], cell["rdy"]
            g = sub[(sub["_intra_rdx"]==cx) & (sub["_intra_rdy"]==cy)]
            if len(g) < min_n:
                continue
            params = []
            for c in die_cols:
                v = pd.to_numeric(g[c], errors="coerce").dropna()
                if len(v) < min_n:
                    continue
                m = float(v.mean()); fm = float(field_mean[c]); fs = float(field_std[c])
                pct = None if (fm == 0 or math.isnan(fm)) else round(100.0*(m - fm)/abs(fm), 2)
                z = round((m - fm)/fs, 3) if (fs and not math.isnan(fs) and fs > 0) else None
                params.append({"param": c, "pct_dev": pct, "z": z, "n": len(v)})
            params.sort(key=lambda p: (abs(p["z"]) if p["z"] is not None else 0), reverse=True)
            out_cells.append({"rdx": cx, "rdy": cy, "fail_rate": cell["fail_rate"],
                              "params": params[:12]})
        return {"cells": out_cells, "n_die_params": len(die_cols)}

    # --- HTML report ---

    @staticmethod
    def _target_to_filename(target_spec):
        """Convert a target spec like 'IB==42|FBS=4200,4216' into a safe filename."""
        import re
        safe = re.sub(r'[^\w=,.-]', '_', target_spec)
        safe = re.sub(r'_+', '_', safe).strip('_')
        return f"correlation_{safe}.html"

    def _open_dashboard(self):
        import webbrowser
        if self._last_report_path and Path(self._last_report_path).exists():
            webbrowser.open(Path(self._last_report_path).as_uri())
        else:
            messagebox.showinfo("No Report", "Run correlation first to generate a report.")

    def _build_and_open_report(self, results, out_dir):
        if len(results) == 1:
            out_path = out_dir / "correlation_report.html"
            r = results[0]
            self._build_report_html(r["df"], r["corr"], r["selected"],
                                    r["target"], out_dir, str(out_path),
                                    zone_data=r.get("zone"))
            self._last_report_path = str(out_path)
            self.after(0, lambda: self._btn_open_dash.config(state="normal"))
            self._log_line(f"Report saved: {out_path}", "ok")
            return

        # Multiple targets — one file each + sidebar wrapper
        report_files = []
        for r in results:
            fname = self._target_to_filename(r["target"])
            fpath = out_dir / fname
            self._build_report_html(r["df"], r["corr"], r["selected"],
                                    r["target"], out_dir, str(fpath),
                                    zone_data=r.get("zone"))
            report_files.append((r["target"], fname))
            self._log_line(f"  Saved: {fpath}")

        wrapper_path = out_dir / "correlation_report.html"
        wrapper_path.write_text(
            self._build_sidebar_wrapper(report_files), encoding="utf-8")
        self._log_line(f"Report saved: {wrapper_path}")
        self._last_report_path = str(wrapper_path)
        self.after(0, lambda: self._btn_open_dash.config(state="normal"))

    @staticmethod
    def _build_sidebar_wrapper(report_files):
        import html as _h
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sidebar_items = "".join(
            f'<div class="sb-item{" active" if i == 0 else ""}" '
            f'onclick="show({i})" id="sb{i}">'
            f'{_h.escape(tgt)}</div>'
            for i, (tgt, _) in enumerate(report_files)
        )
        iframes = "".join(
            f'<iframe id="fr{i}" src="{_h.escape(fname)}" '
            f'style="display:{"block" if i == 0 else "none"};'
            f'width:100%;height:100%;border:none;"></iframe>'
            for i, (_, fname) in enumerate(report_files)
        )
        n = len(report_files)
        return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Correlation Analysis — {n} targets</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%;background:#0a1520;font-family:"Segoe UI",Arial,sans-serif;font-size:12px;overflow:hidden}}
#layout{{display:flex;height:100vh}}
#sidebar{{width:220px;min-width:180px;max-width:320px;background:#0d1b26;border-right:1px solid #1e3a5f;
          display:flex;flex-direction:column;resize:horizontal;overflow:auto}}
#sb-header{{padding:10px 12px;border-bottom:1px solid #1e3a5f;color:#3498db;
            font-size:11px;font-weight:bold;letter-spacing:.04em;white-space:nowrap}}
#sb-header span{{color:#5d7a99;font-weight:normal}}
#sb-list{{flex:1;overflow-y:auto;padding:4px 0}}
.sb-item{{padding:9px 14px;cursor:pointer;color:#7fb3d3;border-left:3px solid transparent;
          line-height:1.4;word-break:break-all;transition:background .15s}}
.sb-item:hover{{background:#162840;color:#ecf0f1}}
.sb-item.active{{background:#1a3550;color:#3498db;border-left-color:#3498db;font-weight:bold}}
#content{{flex:1;display:flex;flex-direction:column;overflow:hidden}}
</style>
</head><body>
<div id="layout">
  <div id="sidebar">
    <div id="sb-header">Analyses &nbsp;<span>({n})</span></div>
    <div id="sb-list">{sidebar_items}</div>
  </div>
  <div id="content">{iframes}</div>
</div>
<script>
function show(i){{
  const n={n};
  for(let j=0;j<n;j++){{
    document.getElementById('fr'+j).style.display=j===i?'block':'none';
    document.getElementById('sb'+j).classList.toggle('active',j===i);
  }}
}}
</script>
</body></html>"""

    def _inject_bindep_into_sidebar(self, results, out_dir, dep_path):
        """Rebuild correlation_report.html to include bin_dependency.html as a sidebar entry."""
        dep_fname = Path(dep_path).name
        if len(results) == 1:
            # Single-target had no sidebar — create one wrapping the correlation report + dep
            corr_fname = "correlation_report_inner.html"
            inner = out_dir / corr_fname
            outer = out_dir / "correlation_report.html"
            if outer.exists() and not inner.exists():
                outer.rename(inner)
            report_files = [(results[0]["target"], corr_fname),
                            ("Bin Driver Similarity", dep_fname)]
        else:
            report_files = [(r["target"], self._target_to_filename(r["target"]))
                            for r in results]
            report_files.append(("Bin Driver Similarity", dep_fname))
        wrapper_path = out_dir / "correlation_report.html"
        wrapper_path.write_text(
            self._build_sidebar_wrapper(report_files), encoding="utf-8")
        self._last_report_path = str(wrapper_path)

    def _save_report(self):
        if not self._last_report:
            messagebox.showinfo("No Report", "Run the analysis first.")
            return
        results = self._last_report["results"]
        out_dir = self._last_report["out_dir"]
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if len(results) == 1:
            path = self._asksave(
                title="Save Report as HTML",
                initialfile=f"correlation_report_{ts}.html",
                defaultextension=".html",
                filetypes=[("HTML files", "*.html"), ("All files", "*.*")])
            if path:
                r = results[0]
                self._build_report_html(r["df"], r["corr"], r["selected"],
                                        r["target"], out_dir, path,
                                        zone_data=r.get("zone"))
        else:
            folder = self._askdir(title="Select folder to save all reports")
            if folder:
                import pathlib
                dest = pathlib.Path(folder)
                report_files = []
                for r in results:
                    fname = self._target_to_filename(r["target"])
                    self._build_report_html(r["df"], r["corr"], r["selected"],
                                            r["target"], out_dir, str(dest / fname),
                                            zone_data=r.get("zone"))
                    report_files.append((r["target"], fname))
                (dest / "correlation_report.html").write_text(
                    self._build_sidebar_wrapper(report_files), encoding="utf-8")
                self._log_line(f"Saved {len(results)} reports + sidebar to {folder}")

    @staticmethod
    def _build_reticle_map_js(df, reticle_dir, src_csv_paths=None):
        """Build JS-format reticle map {"sx,sy": [reticle_loc, 0, shot_idx]}
        by matching DevRevStep prefix6 to the correct CSV in reticle_dir.
        Reads DevRevStep from original input CSVs if not in merged df."""
        if not os.path.isdir(reticle_dir):
            return {"_meta": {"error": f"Reticle dir not found: {reticle_dir}"}}
        available = [f for f in os.listdir(reticle_dir) if f.lower().endswith(".csv")]

        # --- 1. Try DevRevStep column in merged df ---
        drs_col = _detect_devrevstep_col(list(df.columns))
        prefix6 = ""
        if drs_col:
            vals = df[drs_col].dropna()
            if not vals.empty:
                prefix6 = str(vals.iloc[0])[:6].upper()

        # --- 2. Try DevRevStep from original input CSVs ---
        if not prefix6 and src_csv_paths:
            for src_path in src_csv_paths:
                if not src_path or not Path(src_path).exists():
                    continue
                try:
                    if src_path.lower().endswith(".zip"):
                        import zipfile
                        with zipfile.ZipFile(src_path) as zf:
                            members = [m for m in zf.namelist() if m.lower().endswith(".csv")]
                            if not members:
                                continue
                            with zf.open(members[0]) as f:
                                hdr = list(pd.read_csv(f, nrows=0).columns)
                    elif src_path.lower().endswith(".gz"):
                        hdr = list(pd.read_csv(src_path, nrows=0, compression="gzip").columns)
                    else:
                        hdr = list(pd.read_csv(src_path, nrows=0).columns)
                    drs_col2 = _detect_devrevstep_col(hdr)
                    if drs_col2:
                        if src_path.lower().endswith(".gz"):
                            sample = pd.read_csv(src_path, usecols=[drs_col2],
                                                 nrows=50, compression="gzip")
                        elif src_path.lower().endswith(".zip"):
                            import zipfile
                            with zipfile.ZipFile(src_path) as zf:
                                with zf.open(members[0]) as f:
                                    sample = pd.read_csv(f, usecols=[drs_col2], nrows=50)
                        else:
                            sample = pd.read_csv(src_path, usecols=[drs_col2], nrows=50)
                        vals2 = sample[drs_col2].dropna()
                        if not vals2.empty:
                            prefix6 = str(vals2.iloc[0])[:6].upper()
                            break
                except Exception:
                    pass

        if not prefix6:
            return {"_meta": {"error": "No DevRevStep column found in input CSVs.",
                              "available": available}}

        csv_path = None
        for fname in sorted(os.listdir(reticle_dir)):
            if prefix6 in fname.upper().replace("-", "").replace(" ", "") \
               and fname.lower().endswith(".csv"):
                csv_path = os.path.join(reticle_dir, fname)
                break
        if not csv_path:
            # also try with dashes
            for fname in sorted(os.listdir(reticle_dir)):
                if prefix6 in fname.upper() and fname.lower().endswith(".csv"):
                    csv_path = os.path.join(reticle_dir, fname)
                    break
        if not csv_path:
            return {"_meta": {"error": f"No CSV found for prefix '{prefix6}'",
                              "prefix": prefix6, "available": available}}
        try:
            rdf = pd.read_csv(csv_path,
                              usecols=["DieX", "DieY", "LayoutX", "LayoutY", "Reticle"])
            ox = round((rdf["DieX"].min() + rdf["DieX"].max()) / 2)
            oy = round((rdf["DieY"].min() + rdf["DieY"].max()) / 2)
            rdf["sx"] = (rdf["DieX"] - ox).astype(int)
            rdf["sy"] = (rdf["DieY"] - oy).astype(int)
            shot_order = sorted({(int(r.LayoutX), int(r.LayoutY))
                                  for r in rdf.itertuples()})
            shot_idx = {k: i for i, k in enumerate(shot_order)}
            return {
                f"{int(r.sx)},{int(r.sy)}": [
                    int(r.Reticle), 0,
                    shot_idx[(int(r.LayoutX), int(r.LayoutY))]
                ]
                for r in rdf.itertuples()
            }
        except Exception:
            return {}

    def _build_report_html(self, df, corr_data, selected, target_name,
                            out_dir, save_path=None, zone_data=None):
        if not corr_data:
            self._log_line("WARN: no correlation data -- report skipped.", "warn")
            return

        ts        = datetime.datetime.now()
        lot_col   = next((c for c in df.columns if c.lower() in
                          ("lot", "sort_lot", "lot_id")), None)
        wafer_col = next((c for c in df.columns if "wafer" in c.lower()), None)
        prog_col  = next((c for c in df.columns if "program" in c.lower()), None)
        lots      = sorted(str(v) for v in (df[lot_col].dropna().unique()
                                             if lot_col else []))
        programs  = sorted(str(v) for v in (df[prog_col].dropna().unique()
                                             if prog_col else []))
        meta = {
            "target":        target_name,
            "timestamp":     ts.strftime("%Y-%m-%d %H:%M:%S"),
            "n_total":       len(df),
            "n_params":      len(selected),
            "lots":          lots,
            "programs":      programs,
            "lot_col":       lot_col or "",
            "prog_col":      prog_col or "",
            "wafer_col":     wafer_col or "",
            "n_target_hits": int((df["_TARGET"] == 1).sum())
                             if "_TARGET" in df.columns else None,
            "js_compute":    self._chk_js.get(),
            "reticle_dir":   _RETICLE_DIR,
            "idw_coverage":  getattr(self, "_idw_coverage",
                                     {"idw_lots": 0, "median_lots": 0, "pcm_lots": 0}),
            "dropped_columns":       corr_data.get("_dropped_columns", []),
            "structural_covariates": corr_data.get("_structural_covariates", []),
            "n_electrical_params":   len([c for c in selected if c in df.columns]),
        }

        SCATTER_N = 3000
        if "_TARGET" in df.columns and df["_TARGET"].nunique() <= 2:
            hits   = df[df["_TARGET"] == 1]
            others = df[df["_TARGET"] == 0]
            n_hits   = min(len(hits),   int(SCATTER_N * 0.30))
            n_others = min(len(others), SCATTER_N - n_hits)
            df_s = pd.concat([hits.sample(n=n_hits,   random_state=42),
                              others.sample(n=n_others, random_state=42)]
                             ).sample(frac=1, random_state=42).copy()
        else:
            df_s = df.sample(n=min(SCATTER_N, len(df)), random_state=42).copy()
        # Include EVERY param the user can click in die-level tabs so the
        # distribution plot always has data (was capped at first 30 selected).
        # Ship die-level params the user can click: top-60 of each die method by |r|
        # (covers every Top-20 bar, finding card, and visible table row) — keeps file small.
        _die_params, _seen = [], set()
        for _key in ("pearson", "spearman"):
            for _r in (corr_data.get(_key) or [])[:60]:
                _p = _r["param"]
                if _p not in _seen and _p in df_s.columns:
                    _seen.add(_p); _die_params.append(_p)
        for _c in selected[:30]:
            if _c in df_s.columns and _c not in _seen:
                _seen.add(_c); _die_params.append(_c)
        keep_s = ["_TARGET"] + _die_params
        if lot_col and lot_col in df_s.columns:
            keep_s.append(lot_col)
        df_s = df_s[[c for c in keep_s if c in df_s.columns]]
        df_s.columns = [c.replace('"', "'") for c in df_s.columns]
        scatter_json = df_s.to_json(orient="records", double_precision=4)

        if meta["js_compute"]:
            keep_full = ["_TARGET"] + [c for c in selected if c in df.columns]
            if lot_col and lot_col in df.columns:
                keep_full.append(lot_col)
            df_full = df[[c for c in keep_full if c in df.columns]].copy()
            df_full.columns = [c.replace('"', "'") for c in df_full.columns]
            all_records_json = df_full.to_json(orient="records", double_precision=4)
        else:
            all_records_json = "null"

        corr_json    = json.dumps(corr_data)
        meta_json    = json.dumps(meta)
        labels_json  = json.dumps({k: v for k, v in _CORR_METHODS.items() if k in corr_data})
        explain_json = json.dumps({k: v for k, v in _CORR_EXPLAIN.items() if k in corr_data})

        # Build lot-level records — always include fail rate; add ALL discovered PCM params
        lot_records_json = "null"
        if lot_col and lot_col in df.columns:
            agg_d = {"_TARGET": "mean"}
            # Use all PCM-discovered cols present in df (not just significant correlations)
            pcm_lot_params = set()
            for key in ("pcm_lot", "pcm_wafer", "pcm_dev"):
                for r in (corr_data.get(key) or []):
                    if r["param"] in df.columns:
                        pcm_lot_params.add(r["param"])
            # Also include any column that was discovered as a PCM column
            for c in getattr(self, "_pcm_cols_discovered", set()):
                if c in df.columns:
                    pcm_lot_params.add(c)
            for p in sorted(pcm_lot_params):
                agg_d[p] = "median"   # drill-down + High/Low need lot-representative PCM
            # Also include user-selected etest/sort params so High vs Low analysis works
            # even when no PCM methods are selected
            for c in selected:
                if c in df.columns and c not in agg_d:
                    agg_d[c] = "median"
            # Coerce median-aggregated cols to numeric (object dtype -> median drops them)
            _mcols = [c for c in agg_d if agg_d[c] == "median"]
            _num = df[[lot_col] + list(agg_d.keys())].copy()
            if _mcols:
                _num[_mcols] = _num[_mcols].apply(pd.to_numeric, errors="coerce")
            lot_df = _num.groupby(lot_col).agg(agg_d).reset_index()
            lot_df.columns = [c.replace('"', "'") for c in lot_df.columns]
            lot_records_json = lot_df.to_json(orient="records", double_precision=6)

        # Build wafer-level records for PCM Wafer-Level scatter
        wafer_records_json = "null"
        if "pcm_wafer" in corr_data and corr_data["pcm_wafer"] and wafer_col and wafer_col in df.columns:
            pcm_params_w = [r["param"] for r in corr_data["pcm_wafer"]]
            group_cols_w = [c for c in [lot_col, wafer_col] if c and c in df.columns]
            agg_w = {"_TARGET": "mean"}
            for c in pcm_params_w:
                if c in df.columns:
                    agg_w[c] = "median"
            _mcols = [c for c in agg_w if agg_w[c] == "median"]
            _num = df[group_cols_w + [c for c in agg_w if c not in group_cols_w]].copy()
            if _mcols:
                _num[_mcols] = _num[_mcols].apply(pd.to_numeric, errors="coerce")
            wfr_df = _num.groupby(group_cols_w).agg(agg_w).reset_index()
            # create a label column  "lot/wafer"
            if lot_col and lot_col in wfr_df.columns:
                wfr_df["_LABEL"] = wfr_df[lot_col].astype(str) + "/" + wfr_df[wafer_col].astype(str)
            else:
                wfr_df["_LABEL"] = wfr_df[wafer_col].astype(str)
            wfr_df.columns = [c.replace('"', "'") for c in wfr_df.columns]
            wafer_records_json = wfr_df.to_json(orient="records", double_precision=6)

        # Build GENERAL lot.wafer trend records (all params) for the Wafer-Level tab.
        # Works even without PCM: each row = one (lot, wafer) with fail rate + median of every param.
        wafer_trend_records_json = "null"
        if wafer_col and wafer_col in df.columns:
            _grp = [c for c in [lot_col, wafer_col] if c and c in df.columns]
            if _grp:
                agg_wt = {"_TARGET": "mean"}
                _wt_params = set()
                for c in getattr(self, "_pcm_cols_discovered", set()):
                    if c in df.columns: _wt_params.add(c)
                for c in selected:
                    if c in df.columns: _wt_params.add(c)
                for p in sorted(_wt_params):
                    if p not in agg_wt: agg_wt[p] = "median"
                _mcols = [c for c in agg_wt if agg_wt[c] == "median"]
                _num = df[_grp + [c for c in agg_wt if c not in _grp]].copy()
                if _mcols:
                    _num[_mcols] = _num[_mcols].apply(pd.to_numeric, errors="coerce")
                wt_df = _num.groupby(_grp).agg(agg_wt).reset_index()
                if lot_col and lot_col in wt_df.columns:
                    wt_df["_LABEL"] = wt_df[lot_col].astype(str) + "/" + wt_df[wafer_col].astype(str)
                else:
                    wt_df["_LABEL"] = wt_df[wafer_col].astype(str)
                wt_df.columns = [c.replace(chr(34), chr(39)) for c in wt_df.columns]
                wafer_trend_records_json = wt_df.to_json(orient="records", double_precision=6)

        # --- Zone + Wafer Map JSON ---
        zone_json = json.dumps(zone_data or {})
        wafer_map_json = "null"
        if "SORT_X" in df.columns and "SORT_Y" in df.columns:
            ib_col_wm = _detect_ib_col(df.columns)
            fb_col_wm = _detect_fb_col(df.columns)
            target_mode = "IB" if target_name.startswith("IB==") else "FB"
            reticle_map_js = self._build_reticle_map_js(df, _RETICLE_DIR,
                                                          self._csv_paths or [self._csv_path])
            wm_cols = ["SORT_X", "SORT_Y", "_TARGET"]
            if "_zone" in df.columns:
                wm_cols.append("_zone")
            if ib_col_wm and ib_col_wm in df.columns:
                wm_cols.append(ib_col_wm)
            if fb_col_wm and fb_col_wm in df.columns:
                wm_cols.append(fb_col_wm)
            if wafer_col and wafer_col in df.columns:
                wm_cols.append(wafer_col)
            df_wm = df[[c for c in wm_cols if c in df.columns]].copy()
            # Rename to short JS keys
            rename = {"SORT_X": "x", "SORT_Y": "y", "_TARGET": "t",
                      "_zone": "z"}
            if ib_col_wm:
                rename[ib_col_wm] = "ib"
            if fb_col_wm:
                rename[fb_col_wm] = "fb"
            if wafer_col:
                rename[wafer_col] = "w"
            df_wm = df_wm.rename(columns=rename)
            # Compact integer cols to int to reduce JSON size
            for _c in ["x", "y", "t", "ib", "fb"]:
                if _c in df_wm.columns:
                    df_wm[_c] = pd.to_numeric(df_wm[_c], errors="coerce").round(0)
            if "z" in df_wm.columns:
                df_wm["z"] = pd.to_numeric(df_wm["z"], errors="coerce").round(0)
            dies_json = df_wm.to_json(orient="records", double_precision=0)
            xmin = int(df["SORT_X"].min())
            xmax = int(df["SORT_X"].max())
            ymin = int(df["SORT_Y"].min())
            ymax = int(df["SORT_Y"].max())
            reticle_json_str = json.dumps(reticle_map_js)
            wafer_map_json = (f'{{"dies":{dies_json},"reticle_map":{reticle_json_str},'
                              f'"xMin":{xmin},"xMax":{xmax},"yMin":{ymin},"yMax":{ymax},'
                              f'"mode":"{target_mode}","ib_col":"{ib_col_wm or ""}","fb_col":"{fb_col_wm or ""}","wafer_col":"{wafer_col or ""}"}}'
                              )

        intrafield = self._compute_intrafield_fails(df, _RETICLE_DIR,
                                                    self._csv_paths or [self._csv_path])
        intrafield_json = json.dumps(intrafield) if intrafield else "null"
        intrafield_params = self._compute_intrafield_params(df, intrafield, selected)
        intrafield_params_json = json.dumps(intrafield_params) if intrafield_params else "null"
        intrafield_fb = (self._compute_intrafield_fb_matrix(df, target_name)
                         if str(target_name).startswith("IB==") else None)
        intrafield_fb_json = json.dumps(intrafield_fb) if intrafield_fb else "null"
        # fold FB matrix best cell into the consistency tally (4th view for IB targets)
        if intrafield and intrafield.get("consistency") and intrafield_fb and intrafield_fb.get("best"):
            from collections import Counter as _Counter
            c = intrafield["consistency"]
            c["views"]["fb_best"] = int(intrafield_fb["best"]["loc"])
            _t = _Counter(c["views"].values()); _loc, _cnt = _t.most_common(1)[0]
            c["worst_loc"] = int(_loc); c["agree"] = int(_cnt); c["n_views"] = len(c["views"])
            intrafield_json = json.dumps(intrafield)   # re-serialize with updated consistency

        page = (self._report_head(target_name, ts) +
                self._report_body_open(meta) +
                f"\n<script>\n"
                f"const META={meta_json};\n"
                f"const CORR={corr_json};\n"
                f"const SCATTER_RECORDS={scatter_json};\n"
                f"const ALL_RECORDS={all_records_json};\n"
                f"const LOT_RECORDS={lot_records_json};\n"
                f"const WAFER_RECORDS={wafer_records_json};\n"
                f"const WAFER_TREND_RECORDS={wafer_trend_records_json};\n"
                f"const METHOD_LABELS={labels_json};\n"
                f"const METHOD_EXPLAIN={explain_json};\n"
                f"const LOT_COL=META.lot_col;\n"
                f"const JS_COMPUTE=META.js_compute;\n"
                f"const ZONE_DATA={zone_json};\n"
                f"const WAFER_MAP_DATA={wafer_map_json};\n"
                f"const INTRAFIELD_DATA={intrafield_json};\n"
                f"const INTRAFIELD_PARAMS={intrafield_params_json};\n"
                f"const INTRAFIELD_FB={intrafield_fb_json};\n"
                f"</script>\n" +
                self._report_script() +
                "</body></html>")

        out_path = Path(save_path) if save_path else Path(out_dir) / "correlation_report.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(page, encoding="utf-8")
        self._log_line(f"Report saved: {out_path}", "ok")

    @staticmethod
    def _report_head(target_name, ts):
        return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Correlation Report - {_html.escape(target_name)}</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1b26;color:#95a5a6;font-family:"Segoe UI",Arial,sans-serif;font-size:13px;padding:16px}}
h1{{color:#3498db;font-size:18px;margin-bottom:4px}}
h2{{color:#3498db;font-size:14px;margin:12px 0 6px}}
h3{{color:#7fb3d3;font-size:12px;margin:8px 0 4px}}
a{{color:#3498db}}
.cards{{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}}
.card{{background:#162840;border:1px solid #1e3a5f;border-radius:6px;padding:8px 14px;min-width:120px}}
.card .val{{color:#2ecc71;font-size:18px;font-weight:bold}}
.card .lbl{{font-size:11px;color:#7f8c8d;margin-top:2px}}
#filters{{background:#0f2030;border:1px solid #1e3a5f;border-radius:6px;padding:10px;margin:10px 0}}
#filters .row{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:6px}}
.lot-cb label{{cursor:pointer;padding:3px 8px;border-radius:4px;border:1px solid #2c4a6e;background:#162840;white-space:nowrap;user-select:none}}
.lot-cb input{{display:none}}
.lot-cb input:checked+label{{background:#1a5276;border-color:#3498db;color:#ecf0f1}}
.tab-bar{{display:flex;gap:4px;margin:12px 0 0;border-bottom:2px solid #1e3a5f}}
.tab-btn{{padding:7px 16px;cursor:pointer;background:#0d1b26;border:1px solid #1e3a5f;border-bottom:none;border-radius:4px 4px 0 0;color:#7f8c8d;font-size:12px}}
.tab-btn.active{{background:#162840;color:#3498db;border-color:#3498db}}
.tab-pane{{display:none;background:#162840;border:1px solid #1e3a5f;border-top:none;padding:12px;border-radius:0 0 6px 6px}}
.tab-pane.active{{display:block}}
.explain{{background:#0d1b26;border-left:3px solid #3498db;padding:8px 12px;margin-bottom:10px;font-size:12px;line-height:1.6;color:#bdc3c7}}details.help{{margin:8px 0 14px 0}}details.help>summary{{cursor:pointer;user-select:none;list-style:none;padding:7px 12px;background:#12263c;border:1px solid #22405f;border-left:4px solid #3a7bd5;border-radius:4px;color:#7fb3d3;font-weight:bold;font-size:12px}}details.help>summary:hover{{background:#16304a;color:#9fc5e8}}details.help>summary::-webkit-details-marker{{display:none}}details.help>summary::before{{content:'\25B6  ';color:#5dade2;font-size:10px}}details.help[open]>summary::before{{content:'\25BC  '}}details.help[open]>summary{{border-radius:4px 4px 0 0}}details.help>.help-body{{background:#101f30;border:1px solid #22405f;border-top:none;border-radius:0 0 4px 4px;padding:10px 14px;font-size:12px;line-height:1.7;color:#c8d6e8}}
.split{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.tbl-wrap{{overflow-x:auto;max-height:400px;overflow-y:auto}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
thead th{{position:sticky;top:0;background:#0f2030;color:#7fb3d3;padding:5px 8px;text-align:left;cursor:pointer;white-space:nowrap}}
thead th:hover{{color:#3498db}}
tbody tr{{border-bottom:1px solid #1a2f45;cursor:pointer}}
tbody tr:hover{{background:#1a3550}}
tbody tr.selected{{background:#1a5276}}
td{{padding:4px 8px;white-space:nowrap}}
.r-pos{{color:#2ecc71}}.r-neg{{color:#e74c3c}}.r-neu{{color:#95a5a6}}
.pval{{color:#7f8c8d;font-size:11px}}
.no-data{{color:#5d6d7e;padding:20px;text-align:center;font-style:italic}}
.scatter-wrap{{margin-top:10px}}
.badge{{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}}
.badge-strong{{background:#1a7f37;color:#fff}}
.badge-moderate{{background:#9a6700;color:#fff}}
.badge-weak-directional{{background:#8250df;color:#fff}}
.badge-exploratory{{background:#6e7781;color:#fff}}
.warn-banner{{background:#fff8c5;border:1px solid #d4a72c;padding:8px 12px;border-radius:6px;margin:8px 0;font-size:12px;color:#4a3800}}
.note-row td{{color:#7f8c8d;font-style:italic;font-size:11px;padding:2px 8px 6px 24px}}
</style></head><body>
<div style="background:#0a1520;border:1px solid #1e3a5f;border-radius:6px;padding:8px 14px;margin-bottom:12px;font-size:11px;color:#5d7a99">
  <b style="color:#3a6a9a">Generated by:</b>&nbsp;Python {sys.version.split()[0]} &middot; pandas &middot; scipy/sklearn &middot; Plotly 2.27
  <span style="margin-left:16px;color:#5d7a99;font-size:10px;letter-spacing:0.2px">Pant, Sujit N &mdash; GEMS FTE</span>
</div>
<style id="_wm_style">
#_wm_badge{{display:none}}
</style>
"""

    @staticmethod
    def _report_body_open(meta):
        h  = _html
        ts = h.escape(meta["timestamp"])
        tg = h.escape(meta["target"])
        cards = (
            f'<div class="card"><div class="val">{meta["n_total"]:,}</div><div class="lbl">Dies</div></div>'
            f'<div class="card"><div class="val">{meta["n_params"]}</div><div class="lbl">Parameters</div></div>'
            f'<div class="card"><div class="val">{len(meta["lots"])}</div><div class="lbl">Lots</div></div>'
            + ((lambda ic: (
                f'<div class="card"><div class="val" style="font-size:14px">'
                f'{ic["idw_lots"]}/{ic["pcm_lots"]}</div>'
                f'<div class="lbl">IDW lots '
                f'({100.0*ic["idw_lots"]/ic["pcm_lots"]:.0f}%)</div></div>'
              ) if ic.get("pcm_lots") else "")(meta.get("idw_coverage", {})))
            + f'<div class="card"><div class="val">{tg}</div><div class="lbl">Target</div></div>'
            + (f'<div class="card"><div class="val">{meta["n_target_hits"]:,}</div><div class="lbl">Target Hits (=1)</div></div>'
               if meta.get("n_target_hits") is not None else "")
            + f'<div class="card"><div class="val" style="font-size:12px">{ts}</div><div class="lbl">Generated</div></div>'
        )
        _PILL = ("padding:2px 8px;border-radius:10px;border:1px solid #2c4a6e;"
                 "background:#162840;white-space:nowrap;font-size:11px")
        _SHOW_N = 8
        lots_row = ""
        if meta["lots"] or meta["programs"]:
            lots_row = ('<div style="background:#0f2030;border:1px solid #1e3a5f;'
                        'border-radius:6px;padding:10px 14px;margin:10px 0">')
            if meta["lots"]:
                vis = meta["lots"][:_SHOW_N]
                hid = meta["lots"][_SHOW_N:]
                pills = "".join(
                    f'<span style="{_PILL};color:#7fb3d3">{h.escape(str(l))}</span>'
                    for l in vis)
                more = ""
                if hid:
                    more_pills = "".join(
                        f'<span style="{_PILL};color:#7fb3d3">{h.escape(str(l))}</span>'
                        for l in hid)
                    more = (f'<span id="lots-hidden" style="display:none;flex-wrap:wrap;gap:4px">'
                            f'{more_pills}</span>'
                            f'<button onclick="toggleExpandLots()" id="lots-xbtn" '
                            f'data-more="+{len(hid)} more" style="{_BTN_STYLE}">+{len(hid)} more</button>')
                lots_row += (f'<div style="margin-bottom:8px">'
                             f'<span style="color:#7fb3d3;font-size:11px;font-weight:bold">'
                             f'Analyzed Lots</span>'
                             f'&nbsp;<span style="color:#5d7a99;font-size:11px">({len(meta["lots"])})</span>'
                             f'<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:5px;align-items:center">'
                             f'{pills}{more}</div></div>')
            if meta["programs"]:
                vis_p = meta["programs"][:_SHOW_N]
                hid_p = meta["programs"][_SHOW_N:]
                ppills = "".join(
                    f'<span style="{_PILL};color:#95a5a6">{h.escape(str(p))}</span>'
                    for p in vis_p)
                pmore = ""
                if hid_p:
                    more_ppills = "".join(
                        f'<span style="{_PILL};color:#95a5a6">{h.escape(str(p))}</span>'
                        for p in hid_p)
                    pmore = (f'<span id="progs-hidden" style="display:none;flex-wrap:wrap;gap:4px">'
                             f'{more_ppills}</span>'
                             f'<button onclick="toggleExpandProgs()" id="progs-xbtn" '
                             f'data-more="+{len(hid_p)} more" style="{_BTN_STYLE}">+{len(hid_p)} more</button>')
                lots_row += (f'<div>'
                             f'<span style="color:#7fb3d3;font-size:11px;font-weight:bold">'
                             f'Test Programs</span>'
                             f'&nbsp;<span style="color:#5d7a99;font-size:11px">({len(meta["programs"])})</span>'
                             f'<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:5px;align-items:center">'
                             f'{ppills}{pmore}</div></div>')
            lots_row += "</div>"
        return (f'<h1>Correlation Report</h1><div class="cards">{cards}</div>{lots_row}'
                f'<div id="js-status" style="font-size:11px;color:#2ecc71;margin:4px 0 8px;min-height:14px"></div>'
                f'<h2>Summary - Strongest Parameters</h2>'
                f'<div id="summary-area"></div><div id="summary-explain"></div>'
                f'<h2>Correlation Methods</h2>'
                f'<div class="tab-bar" id="tab-bar"></div>'
                f'<div id="tab-area"></div>')

    @staticmethod
    def _report_script():
        return r"""
<script>
const fmt=(v,d=4)=>(v===null||v===undefined||isNaN(v))?'--':v.toFixed(d);
const strengthLabel=r=>{const a=Math.abs(r);if(a<0.05)return'<span style="color:#5d7a99">Negligible</span>';if(a<0.10)return'<span style="color:#7fb3d3">Very weak</span>';if(a<0.30)return'<span style="color:#f39c12">Weak ✓</span>';if(a<0.50)return'<span style="color:#2ecc71">Moderate ✓✓</span>';return'<span style="color:#3498db;font-weight:bold">Strong ✓✓✓</span>';};
const strengthLabelMI=mi=>{if(mi<0.001)return'<span style="color:#5d7a99">Negligible</span>';if(mi<0.005)return'<span style="color:#7fb3d3">Very weak</span>';if(mi<0.02)return'<span style="color:#f39c12">Weak ✓</span>';if(mi<0.05)return'<span style="color:#2ecc71">Moderate ✓✓</span>';return'<span style="color:#3498db;font-weight:bold">Strong ✓✓✓</span>';};
const strengthText=r=>{const a=Math.abs(r||0);if(a<0.05)return'Negligible';if(a<0.10)return'Very weak';if(a<0.30)return'Weak';if(a<0.50)return'Moderate';return'Strong';};
const strengthTextMI=mi=>{if(mi<0.001)return'Negligible';if(mi<0.005)return'Very weak';if(mi<0.02)return'Weak';if(mi<0.05)return'Moderate';return'Strong';};
function dTier(d){const a=Math.abs(d||0);if(a>=0.8)return{label:'large',color:'#e74c3c'};if(a>=0.5)return{label:'medium',color:'#e67e22'};if(a>=0.2)return{label:'small',color:'#f1c40f'};return{label:'negligible',color:'#5d7a99'};}
function ratioTier(x){if(x==null)return{label:'',color:'#5d7a99'};const d=Math.abs(x-1);if(d>=0.5)return{label:'strong',color:'#e74c3c'};if(d>=0.2)return{label:'notable',color:'#e67e22'};if(d>=0.05)return{label:'mild',color:'#f1c40f'};return{label:'flat',color:'#5d7a99'};}
function rTier(r){const a=Math.abs(r||0);if(a>=0.5)return{label:'strong',color:'#e74c3c'};if(a>=0.3)return{label:'moderate',color:'#e67e22'};if(a>=0.1)return{label:'weak',color:'#f1c40f'};return{label:'negligible',color:'#5d7a99'};}
function dirWord(r){return(r||0)<0?'lower \u2192 more fails':'higher \u2192 more fails';}
const fmtP=p=>p==null?'':'p='+p.toExponential(2).replace(/e([+-])0+(\d)/,'e$1$2');
const fmtN=n=>n.toLocaleString();
const absR=row=>row.r!==undefined?Math.abs(row.r):(row.mi||0);
const scoreLabel=(row,m)=>{if(m==='mutual_info')return'MI='+fmt(row.mi);const arrow=row.r>0?'up':'dn';return(row.r>0?'+':'')+fmt(row.r)+' '+arrow;};
const rClass=(row,m)=>{if(m==='mutual_info')return'r-pos';return row.r>0.001?'r-pos':row.r<-0.001?'r-neg':'r-neu';};
const dirSpan=(row,m)=>m==='mutual_info'?'':(row.r>0?'<span style="color:#2ecc71">positive</span>':'<span style="color:#e74c3c">negative</span>');
function toggleExpandLots(){const h=document.getElementById('lots-hidden'),b=document.getElementById('lots-xbtn');if(!h||!b)return;const open=h.style.display!=='none';h.style.display=open?'none':'inline-flex';h.style.flexWrap='wrap';h.style.gap='4px';b.textContent=open?(b.dataset.more||'more'):'Show less';}
function toggleExpandProgs(){const h=document.getElementById('progs-hidden'),b=document.getElementById('progs-xbtn');if(!h||!b)return;const open=h.style.display!=='none';h.style.display=open?'none':'inline-flex';h.style.flexWrap='wrap';h.style.gap='4px';b.textContent=open?(b.dataset.more||'more'):'Show less';}
let activeParam={};
function activeMethod(){const el=document.querySelector('.tab-btn.active');return el?el.dataset.method:null;}
function showTab(method){document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active',b.dataset.method===method));document.querySelectorAll('.tab-pane').forEach(p=>p.classList.toggle('active',p.id==='pane-'+method));if(!activeParam[method]){const rows=CORR[method]||[];activeParam[method]=rows.length?rows[0].param:null;}refreshScatter(method,activeParam[method]);}
function buildOneMethodTab(m){if(!CORR[m]||!CORR[m].length)return false;const bar=document.getElementById('tab-bar');const area=document.getElementById('tab-area');const btn=document.createElement('button');btn.className='tab-btn';btn.dataset.method=m;btn.textContent=METHOD_LABELS[m]||m;btn.onclick=()=>showTab(m);bar.appendChild(btn);const pane=document.createElement('div');pane.className='tab-pane';pane.id='pane-'+m;pane.innerHTML=buildPane(m);area.appendChild(pane);activeParam[m]=CORR[m][0].param;renderBarChart(m);refreshScatter(m,activeParam[m]);const first=document.querySelector(`#tbl-${m} tbody tr:not(.grp-hdr)`);if(first)first.classList.add('selected');return true;}function activateTab(id){document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active',b.dataset.method===id));document.querySelectorAll('.tab-pane').forEach(p=>p.classList.toggle('active',p.id==='pane-'+id));}
function buildTabs(){const bar=document.getElementById('tab-bar');const area=document.getElementById('tab-area');let first=true;for(const[m,label]of Object.entries(METHOD_LABELS)){if(!CORR[m]||!CORR[m].length)continue;const btn=document.createElement('button');btn.className='tab-btn'+(first?' active':'');btn.dataset.method=m;btn.textContent=label;btn.onclick=()=>showTab(m);bar.appendChild(btn);const pane=document.createElement('div');pane.className='tab-pane'+(first?' active':'');pane.id='pane-'+m;pane.innerHTML=buildPane(m);area.appendChild(pane);if(first){activeParam[m]=CORR[m].length?CORR[m][0].param:null;first=false;}}}
function paramGroup(p){const u=p.toUpperCase();if(u.startsWith('TPI_BIN::'))return'TPI_BIN';if(u.startsWith('TPI_ADTL::'))return'TPI_ADTL';if(u.startsWith('UPM_')){const s=p.slice(4);const mx=s.match(/^(\w+)/);return mx?mx[1]:'UPM';}const pt=p.split('_');return pt.length>1?pt[0]:p;}
function toggleGroup(gid){const rs=document.querySelectorAll('[data-gid="'+gid+'"]');const hdr=document.querySelector('[data-grphdr="'+gid+'"]');const tog=hdr?hdr.querySelector('.grp-toggle'):null;const hidden=rs.length&&rs[0].style.display==='none';rs.forEach(r=>r.style.display=hidden?'':'none');if(tog)tog.textContent=hidden?'▼':'▶';}
function toggleAllGroups(m,open){document.querySelectorAll('#tbl-'+m+' [data-grphdr]').forEach(hdr=>{const gid=hdr.dataset.grphdr;const rs=document.querySelectorAll('[data-gid="'+gid+'"]');rs.forEach(r=>r.style.display=open?'':'none');const tog=hdr.querySelector('.grp-toggle');if(tog)tog.textContent=open?'▼':'▶';});}
function buildPane(m){const rows=CORR[m]||[];const explain=METHOD_EXPLAIN[m]||'';const label=METHOD_LABELS[m]||m;const hasP=rows.length&&rows[0].p!==undefined;const colCount=8+(hasP?1:0)+(rows.length&&rows[0].ci_lo!=null?1:0)+(rows.length&&rows[0].spread_r!==undefined?1:0);const hasCi=rows.length&&rows[0].ci_lo!=null;const groups=new Map();rows.forEach((row,i)=>{const g=paramGroup(row.param);if(!groups.has(g))groups.set(g,[]);groups.get(g).push({row,i});});let trows='';let gIdx=0;groups.forEach((items,g)=>{const gid=m+'_g'+(gIdx++);const isOpen=items.length<=2;if(items.length>1){const bestRow=items[0].row;const bestSl=m==='mutual_info'?strengthLabelMI(bestRow.mi||0):strengthLabel(bestRow.r||0);trows+=`<tr class="grp-hdr" data-grphdr="${gid}" onclick="toggleGroup('${gid}')" style="cursor:pointer;background:#0f1d30"><td colspan="${colCount}" style="padding:4px 8px;font-weight:bold;color:#7fb3d3;user-select:none"><span class="grp-toggle" style="margin-right:6px;font-size:11px;display:inline-block;width:12px">${isOpen?'▼':'▶'}</span>${escQ(g)} <span style="color:#445566;font-weight:normal;font-size:11px">(${items.length})</span>&nbsp;&nbsp;<span style="font-size:11px;font-weight:normal">${bestSl}</span></td></tr>`;}items.forEach(({row,i})=>{const sc=scoreLabel(row,m);const pStr=hasP?`<td class="pval">${fmtP(row.p)}</td>`:'';const sl=m==='mutual_info'?strengthLabelMI(row.mi||0):((['pearson','spearman'].includes(m))?(t=>`<span style="color:${t.color};font-weight:bold">${t.label}</span>`)(dTier(row.d!=null?row.d:0)):(t=>`<span style="color:${t.color}">${t.label}</span>`)(rTier(row.r||0)));const ciStr=hasCi&&row.ci_lo!=null?`<td style="color:#556677;font-size:11px">[${row.ci_lo.toFixed(2)},${row.ci_hi.toFixed(2)}]</td>`:(hasCi?'<td></td>':'');const ds=(!isOpen&&items.length>1)?'display:none':'';trows+=`<tr onclick="selectRow(this,'${escQ(m)}','${escQ(row.param)}')" data-param="${escQ(row.param)}" data-gid="${gid}" style="${ds}"><td>${i+1}</td><td title="${escQ(row.param)}">${row.param.length>45?row.param.slice(0,44)+'…':row.param}</td><td class="${rClass(row,m)}" style="${(['pearson','spearman'].includes(m)&&row.d!=null)?'color:'+dTier(row.d).color:''}">${sc}</td><td>${fmt(absR(row))}</td><td>${sl}</td><td>${fmtN(row.n)}</td><td>${dirSpan(row,m)}</td><td>${row.signal?'<span class="badge badge-'+(row.signal||'').toLowerCase().replace(/ \/ /g,'-').replace(/ /g,'-')+'">'+(row.signal||'')+'</span>':''}</td>${pStr}${ciStr}${(row.spread_r!==undefined)?`<td style="color:${Math.abs(row.spread_r||0)>=0.2?'#1abc9c':'#5d7a99'}" title="within-wafer non-uniformity correlation">${row.spread_r==null?'&ndash;':(row.spread_r>0?'+':'')+row.spread_r.toFixed(3)}</td>`:''}</tr>${row.note?`<tr class="note-row" data-gid="${gid}" style="${ds}"><td colspan="${colCount}"><em>${escQ(row.note||'')}</em></td></tr>`:''}`;});});const pHdr=hasP?'<th>p-value</th>':'';const _dupCnt=(CORR[m]||[]).filter(r=>r.is_duplicate_stat).length;const _dupBanner=_dupCnt?'<div class="warn-banner">⚠ '+_dupCnt+' parameters share identical statistics — likely duplicated, derived, or strongly coupled. Review before selecting root-cause candidates.</div>':'';return`<div class="explain" style="padding:0;border:none;background:none">${explain?`<details class="help"><summary>How to read this method</summary><div class="help-body">${explain}</div></details>`:''}</div><div class="split"><div class="tbl-wrap">${_dupBanner}<h3>${label} (${rows.length}) - click row to scatter &nbsp;<button onclick="toggleAllGroups('${m}',true)" style="font-size:11px;padding:2px 7px;background:#1a2e45;color:#7fb3d3;border:1px solid #2a4060;border-radius:3px;cursor:pointer">▼ All</button> <button onclick="toggleAllGroups('${m}',false)" style="font-size:11px;padding:2px 7px;background:#1a2e45;color:#7fb3d3;border:1px solid #2a4060;border-radius:3px;cursor:pointer">▶ All</button></h3>${['pearson','spearman'].includes(m)?`<div style="font-size:11px;color:#8ba0b8;margin:6px 0 4px;padding:5px 10px;background:#0a1520;border-left:3px solid #e67e22">Note: for rare bins, Pearson r is compressed toward 0. Judge strength by <b>Cohen d</b> (<span style="color:#5d7a99">negligible</span> &lt;0.2 \u00b7 <span style="color:#f1c40f">small</span> 0.2&ndash;0.5 \u00b7 <span style="color:#e67e22">medium</span> 0.5&ndash;0.8 \u00b7 <span style="color:#e74c3c">large</span> &gt;0.8) and fail/pass ratio, not by r.</div>`:''}<table id="tbl-${m}"><thead><tr><th>#</th><th>Parameter</th><th>Score</th><th>|Score|</th><th>Strength</th><th>n</th><th>Direction</th><th>Signal</th>${pHdr}${hasCi?'<th title="95% CI via Fisher z-transform">95% CI</th>':''}${(rows.length&&rows[0].spread_r!==undefined)?'<th title="Within-wafer die-to-die std vs %fail — non-uniformity signal">Spread r</th>':''}</tr></thead><tbody>${trows}</tbody></table></div><div><h3>Top 20</h3><div id="bar-${m}" style="height:360px"></div></div></div><div class="scatter-wrap"><h3 id="scatter-title-${m}">Distribution</h3><div id="scatter-${m}" style="height:400px"></div></div>`;}
function escQ(s){return s.replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function selectRow(tr,m,param){document.querySelectorAll(`#tbl-${m} tr`).forEach(r=>r.classList.remove('selected'));tr.classList.add('selected');activeParam[m]=param;refreshScatter(m,param);}
function renderBarChart(m){const rows=(CORR[m]||[]).slice(0,20);if(!rows.length)return;const x=rows.map(r=>absR(r)).reverse();const y=rows.map(r=>r.param.length>35?r.param.slice(0,34)+'\u2026':r.param).reverse();const colors=rows.map(r=>r.r===undefined?'#3498db':r.r>0?'#2ecc71':'#e74c3c').reverse();const strength=rows.map(r=>m==='mutual_info'?strengthTextMI(r.mi||0):strengthText(r.r||0)).reverse();Plotly.newPlot('bar-'+m,[{type:'bar',orientation:'h',x,y,marker:{color:colors},customdata:strength,hovertemplate:'<b>%{y}</b><br>|score|=%{x:.4f}<br>Strength: %{customdata}<extra></extra>'}],{paper_bgcolor:'#162840',plot_bgcolor:'#0d1b26',font:{color:'#95a5a6',size:11},margin:{l:20,r:20,t:10,b:30},xaxis:{color:'#5d6d7e',gridcolor:'#1e3a5f'},yaxis:{color:'#95a5a6',automargin:true}},{responsive:true,displayModeBar:false});const _bg=document.getElementById('bar-'+m);const _names=rows.map(r=>r.param).reverse();if(_bg&&_bg.on){_bg.on('plotly_click',function(ev){const pt=ev.points&&ev.points[0];if(!pt)return;const idx=(pt.pointIndex!=null?pt.pointIndex:pt.pointNumber);const param=_names[idx];if(param==null)return;activeParam[m]=param;try{refreshScatter(m,param);}catch(e){}document.querySelectorAll('#tbl-'+m+' tbody tr').forEach(r=>{r.classList.remove('selected');if(r.getAttribute('data-param')===param)r.classList.add('selected');});const sc=document.getElementById('scatter-'+m);if(sc&&sc.scrollIntoView)sc.scrollIntoView({behavior:'smooth',block:'nearest'});});_bg.style.cursor='pointer';}}
function refreshScatterAggregate(param,records,elId,titleId,unitLabel){const el=document.getElementById(elId);if(!el)return;if(!param||!records){el.innerHTML='<div class="no-data">No '+unitLabel+'-level data</div>';return;}const xs=[],ys=[],texts=[];records.forEach(row=>{const x=row[param],y=row['_TARGET'];if(x==null||y==null)return;xs.push(+x);ys.push(+(y*100).toFixed(2));texts.push(String(row['_LABEL']||row['_label']||''));});if(xs.length===0){el.innerHTML='<div class="no-data">No data</div>';return;}const dotColor=unitLabel==='wafer'?'#9b59b6':'#f39c12';const dotBorder=unitLabel==='wafer'?'#7d3c98':'#d68910';const xLabel=param.length>35?param.slice(0,34)+'\u2026':param;const traces=[{type:'scatter',mode:'markers+text',x:xs,y:ys,text:texts,textposition:'top center',name:'data',marker:{color:dotColor,size:9,line:{color:dotBorder,width:1}},hovertemplate:'<b>%{text}</b><br>'+xLabel+': %{x:.6f}<br>% Hits: %{y:.2f}%<extra></extra>'}];if(xs.length>=4){const nBins=Math.max(3,Math.min(8,Math.floor(xs.length/2)));const xMin=Math.min(...xs),xMax=Math.max(...xs),bw=(xMax-xMin)/nBins||1;const bins=Array.from({length:nBins},(_,i)=>({cx:xMin+(i+0.5)*bw,ys:[]}));xs.forEach((x,i)=>{const bi=Math.min(nBins-1,Math.floor((x-xMin)/bw));bins[bi].ys.push(ys[i]);});const bxs=[],bys=[];bins.forEach(b=>{if(b.ys.length){bxs.push(b.cx);bys.push(b.ys.reduce((a,v)=>a+v,0)/b.ys.length);}});traces.push({type:'scatter',mode:'lines+markers',x:bxs,y:bys,name:'bin mean',line:{color:'#e74c3c',width:2,dash:'dot'},marker:{color:'#e74c3c',size:7},hovertemplate:'bin centre: %{x:.4f}<br>mean % Hits: %{y:.2f}%<extra>bin mean</extra>'});}Plotly.newPlot(el,traces,{paper_bgcolor:'#162840',plot_bgcolor:'#0d1b26',font:{color:'#95a5a6',size:11},margin:{l:60,r:20,t:40,b:60},xaxis:{title:xLabel,color:'#5d6d7e',gridcolor:'#1e3a5f'},yaxis:{title:'% Target Hits per '+unitLabel.charAt(0).toUpperCase()+unitLabel.slice(1),color:'#5d6d7e',gridcolor:'#1e3a5f'},legend:{bgcolor:'#0d1b26',bordercolor:'#1e3a5f',borderwidth:1,orientation:'h',y:-0.22},hovermode:'closest'},{responsive:true,displayModeBar:true,modeBarButtonsToRemove:['lasso2d','select2d']});document.getElementById(titleId).textContent=unitLabel.charAt(0).toUpperCase()+unitLabel.slice(1)+'-Level: '+param+' vs % Target Hits (n='+xs.length+' '+unitLabel+'s)';}
function refreshScatterLot(param){LOT_RECORDS&&LOT_RECORDS.forEach(r=>{if(!r['_LABEL'])r['_LABEL']=r[META.lot_col]||'';});refreshScatterAggregate(param,LOT_RECORDS,'scatter-pcm_lot','scatter-title-pcm_lot','lot');}
function refreshScatterWafer(param){refreshScatterAggregate(param,WAFER_RECORDS,'scatter-pcm_wafer','scatter-title-pcm_wafer','wafer');}
function refreshScatterDev(param){if(!LOT_RECORDS||!param){document.getElementById('scatter-pcm_dev').innerHTML='<div class="no-data">No data</div>';return;}const med=(arr=>{const s=[...arr].sort((a,b)=>a-b);return s.length%2?s[~~(s.length/2)]:(s[s.length/2-1]+s[s.length/2])/2;});const xs=[],ys=[],texts=[];LOT_RECORDS.forEach(row=>{const x=row[param],y=row['_TARGET'];if(x==null||y==null)return;xs.push(+x);ys.push(+(y*100).toFixed(2));texts.push(String(row['_LABEL']||row[META.lot_col]||''));});if(!xs.length){document.getElementById('scatter-pcm_dev').innerHTML='<div class="no-data">No data</div>';return;}const m=med(xs);const devs=xs.map(x=>Math.abs(x-m));const xLabel=(param.length>35?param.slice(0,34)+'\u2026':param);const nBins=Math.max(3,Math.min(7,Math.floor(devs.length/2)));const dMax=Math.max(...devs)||1;const bw=dMax/nBins;const bins=Array.from({length:nBins},(_,i)=>({cx:(i+0.5)*bw,ys:[]}));devs.forEach((d,i)=>{const bi=Math.min(nBins-1,Math.floor(d/bw));bins[bi].ys.push(ys[i]);});const bxs=[],bys=[];bins.forEach(b=>{if(b.ys.length){bxs.push(b.cx);bys.push(b.ys.reduce((a,v)=>a+v,0)/b.ys.length);}});const el=document.getElementById('scatter-pcm_dev');Plotly.newPlot(el,[{type:'scatter',mode:'markers+text',x:devs,y:ys,text:texts,textposition:'top center',name:'lots',marker:{color:'#1abc9c',size:9,line:{color:'#17a589',width:1}},hovertemplate:'<b>%{text}</b><br>|x−median|=%{x:.6f}<br>% Hits: %{y:.2f}%<extra></extra>'},{type:'scatter',mode:'lines+markers',x:bxs,y:bys,name:'bin mean',line:{color:'#e74c3c',width:2,dash:'dot'},marker:{color:'#e74c3c',size:7},hovertemplate:'|dev| bin: %{x:.4f}<br>mean: %{y:.2f}%<extra>bin mean</extra>'}],{paper_bgcolor:'#162840',plot_bgcolor:'#0d1b26',font:{color:'#95a5a6',size:11},margin:{l:60,r:20,t:40,b:60},xaxis:{title:'|'+xLabel+' − median|',color:'#5d6d7e',gridcolor:'#1e3a5f'},yaxis:{title:'% Target Hits per Lot',color:'#5d6d7e',gridcolor:'#1e3a5f'},legend:{bgcolor:'#0d1b26',bordercolor:'#1e3a5f',borderwidth:1,orientation:'h',y:-0.22},hovermode:'closest'},{responsive:true,displayModeBar:true,modeBarButtonsToRemove:['lasso2d','select2d']});document.getElementById('scatter-title-pcm_dev').textContent='Deviation: |'+param+' − median| vs % Hits (n='+devs.length+' lots)';}
function refreshScatterWdev(param){const el=document.getElementById('scatter-pcm_wdev');if(!el)return;if(typeof WAFER_TREND_RECORDS==='undefined'||!WAFER_TREND_RECORDS||!param){el.innerHTML='<div class="no-data">No wafer-level records (needs IDW per-wafer PCM).</div>';return;}const lotKey=META.lot_col;const byLot={};WAFER_TREND_RECORDS.forEach(r=>{const x=+r[param];if(isNaN(x))return;const lk=String(r[lotKey]||'?');if(!byLot[lk])byLot[lk]=[];byLot[lk].push(x);});const med=arr=>{const s=[...arr].sort((a,b)=>a-b);return s.length%2?s[(s.length-1)/2]:(s[s.length/2-1]+s[s.length/2])/2;};const lotMed={};Object.keys(byLot).forEach(lk=>{if(byLot[lk].length>=4)lotMed[lk]=med(byLot[lk]);});const xs=[],ys=[],txt=[];WAFER_TREND_RECORDS.forEach(r=>{const x=+r[param],y=r['_TARGET'];const lk=String(r[lotKey]||'?');if(isNaN(x)||y==null||!(lk in lotMed))return;xs.push(Math.abs(x-lotMed[lk]));ys.push(+(y*100).toFixed(3));txt.push(String(r['_LABEL']||lk));});if(!xs.length){el.innerHTML='<div class="no-data">No qualifying wafers (need lots with &ge;4 wafers and per-die PCM variation).</div>';return;}const xLabel=param.length>34?param.slice(0,33)+'\u2026':param;const nB=Math.max(3,Math.min(8,Math.floor(xs.length/2)));const dMax=Math.max(...xs)||1;const bw=dMax/nB;const bins=Array.from({length:nB},(_,i)=>({cx:(i+0.5)*bw,ys:[]}));xs.forEach((d,i)=>{const bi=Math.min(nB-1,Math.floor(d/bw));bins[bi].ys.push(ys[i]);});const bx=[],by=[];bins.forEach(b=>{if(b.ys.length){bx.push(b.cx);by.push(b.ys.reduce((a,v)=>a+v,0)/b.ys.length);}});Plotly.newPlot(el,[{type:'scatter',mode:'markers',x:xs,y:ys,text:txt,marker:{color:'#1abc9c',size:8,line:{color:'#0d1b26',width:1}},hovertemplate:'<b>%{text}</b><br>|wafer\u2212lotmed|=%{x:.5f}<br>Fail: %{y:.3f}%<extra></extra>',showlegend:false},{type:'scatter',mode:'lines+markers',x:bx,y:by,line:{color:'#e74c3c',width:2,dash:'dot'},marker:{color:'#e74c3c',size:6},hovertemplate:'bin: %{x:.4f}<br>mean: %{y:.3f}%<extra>bin mean</extra>',showlegend:false}],{paper_bgcolor:'#162840',plot_bgcolor:'#0d1b26',font:{color:'#95a5a6',size:11},margin:{l:60,r:20,t:20,b:55},xaxis:{title:'|'+xLabel+' \u2212 lot median|',color:'#5d6d7e',gridcolor:'#1e3a5f'},yaxis:{title:'% Fail per Wafer',color:'#5d6d7e',gridcolor:'#1e3a5f'},hovermode:'closest'},{responsive:true,displayModeBar:true,modeBarButtonsToRemove:['lasso2d','select2d']});const tt=document.getElementById('scatter-title-pcm_wdev');if(tt)tt.textContent='Within-lot wafer deviation: '+param+' (n='+xs.length+' wafers)';}
function refreshScatter(m,param){if(m==='pcm_lot'){refreshScatterLot(param);return;}if(m==='pcm_wafer'){refreshScatterWafer(param);return;}if(m==='pcm_dev'){refreshScatterDev(param);return;}if(m==='pcm_wdev'){refreshScatterWdev(param);return;}const el=document.getElementById('scatter-'+m);if(!el)return;if(!param){el.innerHTML='<div class="no-data">Select a row to see scatter</div>';return;}const buckets={0:[],1:[]};SCATTER_RECORDS.forEach(row=>{const x=row[param],y=row['_TARGET'];if(x===null||x===undefined||y===null||y===undefined)return;buckets[y==1?1:0].push(+x);});const n0=buckets[0].length,n1=buckets[1].length;if(n0+n1===0){Plotly.purge(el);el.innerHTML='<div class="no-data">No data</div>';return;}const mean=arr=>arr.length?arr.reduce((a,b)=>a+b,0)/arr.length:null;const std=(arr,mu)=>arr.length<2?0:Math.sqrt(arr.reduce((s,v)=>s+(v-mu)**2,0)/(arr.length-1));const m0=mean(buckets[0]),m1=mean(buckets[1]);const s0=m0!==null?std(buckets[0],m0):0,s1=m1!==null?std(buckets[1],m1):0;const allX=buckets[0].concat(buckets[1]);const xMin=Math.min(...allX),xMax=Math.max(...allX);const binSize=(xMax-xMin)/40||1;const xLabel=param.length>35?param.slice(0,34)+'\u2026':param;const traces=[];if(n0)traces.push({type:'histogram',x:buckets[0],name:`Pass/Other (0) n=${n0.toLocaleString()}`,histnorm:'percent',xbins:{start:xMin,end:xMax+binSize,size:binSize},marker:{color:'rgba(52,152,219,0.75)',line:{color:'#2980b9',width:0.5}},xaxis:'x',yaxis:'y',hovertemplate:'%{x:.4f}<br>%{y:.1f}%<extra>Pass</extra>'});if(n1)traces.push({type:'histogram',x:buckets[1],name:`Bin Hit (1) n=${n1.toLocaleString()}`,histnorm:'percent',xbins:{start:xMin,end:xMax+binSize,size:binSize},marker:{color:'rgba(231,76,60,0.80)',line:{color:'#c0392b',width:0.5}},xaxis:'x2',yaxis:'y2',hovertemplate:'%{x:.4f}<br>%{y:.1f}%<extra>Bin Hit</extra>'});const shapes=[];if(m0!==null)shapes.push({type:'line',xref:'x',yref:'paper',x0:m0,x1:m0,y0:0,y1:1,line:{color:'#5dade2',width:2,dash:'dot'}});if(m1!==null)shapes.push({type:'line',xref:'x2',yref:'paper',x0:m1,x1:m1,y0:0,y1:1,line:{color:'#e74c3c',width:2,dash:'dot'}});const annotations=[];if(m0!==null)annotations.push({xref:'x',yref:'paper',x:m0,y:0.97,showarrow:false,text:`mean=${m0.toFixed(4)}<br>sd=${s0.toFixed(4)}`,font:{size:10,color:'#5dade2'},align:'left',bgcolor:'rgba(10,21,32,0.75)',borderpad:3});if(m1!==null)annotations.push({xref:'x2',yref:'paper',x:m1,y:0.97,showarrow:false,text:`mean=${m1.toFixed(4)}<br>sd=${s1.toFixed(4)}`,font:{size:10,color:'#e74c3c'},align:'left',bgcolor:'rgba(10,21,32,0.75)',borderpad:3});if(m0!==null&&m1!==null)annotations.push({xref:'paper',yref:'paper',x:0.5,y:1.08,showarrow:false,text:`delta mean = ${(m1-m0).toFixed(4)} (${s0>0?((m1-m0)/s0).toFixed(2):'n/a'} sd of pass)`,font:{size:11,color:'#f39c12'},align:'center'});const axCommon={range:[xMin-binSize*0.5,xMax+binSize*0.5],color:'#5d6d7e',gridcolor:'#1e3a5f',zeroline:false};Plotly.newPlot(el,traces,{paper_bgcolor:'#162840',plot_bgcolor:'#0d1b26',font:{color:'#95a5a6',size:11},margin:{l:50,r:20,t:42,b:50},grid:{rows:1,columns:2,pattern:'independent',xgap:0.10},xaxis:{...axCommon,title:xLabel},xaxis2:{...axCommon,title:xLabel},yaxis:{title:'% of group',color:'#5d6d7e',gridcolor:'#1e3a5f'},yaxis2:{title:'% of group',color:'#5d6d7e',gridcolor:'#1e3a5f'},shapes,annotations,legend:{bgcolor:'#0d1b26',bordercolor:'#1e3a5f',borderwidth:1,orientation:'h',y:-0.18,x:0},hovermode:'closest'},{responsive:true,displayModeBar:true,modeBarButtonsToRemove:['lasso2d','select2d','toImage']});const sub=(m0!==null&&m1!==null)?`  mean(pass)=${m0.toFixed(4)}  mean(bin)=${m1.toFixed(4)}  delta=${(m1-m0).toFixed(4)}`:'';document.getElementById('scatter-title-'+m).textContent=`Distribution: ${param}${sub}`;}
function cardClick(m,param){/* die-level->pearson/spearman tab; lot-level->pcm_* tab */ const btn=document.querySelector('.tab-btn[data-method="'+m+'"]');if(btn){btn.click();}activeParam[m]=param;try{refreshScatter(m,param);}catch(e){}const pane=document.getElementById('pane-'+m);if(pane){pane.scrollIntoView({behavior:'smooth',block:'start'});const rows=pane.querySelectorAll('#tbl-'+m+' tbody tr');rows.forEach(r=>{r.classList.remove('selected');if(r.getAttribute('data-param')===param)r.classList.add('selected');});}}
function buildSummary(){const dieMethods=['pearson','spearman'];const lotMethods=['pcm_lot','pcm_wafer','pcm_dev'];const bestByTier=(methods)=>{const best=new Map();methods.forEach(m=>{(CORR[m]||[]).forEach(row=>{const a=absR(row);if(!best.has(row.param)||a>absR(best.get(row.param).row))best.set(row.param,{param:row.param,method:m,row});});});return [...best.values()].sort((x,y)=>absR(y.row)-absR(x.row));};const card=(it,rank)=>{const row=it.row,m=it.method;const label=METHOD_LABELS[m]||m;const isDie=['pearson','spearman'].includes(m);const dir=(row.r>0)?'higher':'lower';const sTier=isDie?dTier(row.d!=null?row.d:0):rTier(row.r||0);const strong=sTier.label;const strongColor=sTier.color;const sig=(row.q!=null)?(row.q<0.10?`<span style="color:#2ecc71">q=${row.q.toExponential(1)} \u2713</span>`:`<span style="color:#e67e22">q=${row.q.toExponential(1)} (weak after FDR)</span>`):(row.p!=null?`p=${row.p.toExponential(1)}`:'');const ci=(row.ci_lo!=null)?`95% CI [${row.ci_lo.toFixed(2)}, ${row.ci_hi.toFixed(2)}]`:'';const eff=(!isDie&&(row.ratio!=null||row.d!=null))?`<div style="margin-top:4px;color:#f1c40f;font-size:12px">`+(row.ratio!=null?`Fail-group mean <b>${row.ratio.toFixed(2)}\u00d7</b> pass`:'')+(row.d!=null?`  \u00b7  Cohen d=<b>${row.d.toFixed(2)}</b>`:'')+`</div>`:'';const scaleNote=m.startsWith('pcm')?'lot/wafer-level':'die-level';return `<div onclick="cardClick('${m}','${escQ(it.param)}')" title="Click to open plot for this parameter" style="flex:1;min-width:260px;background:#12263c;border:1px solid #22405f;border-left:4px solid ${row.r>0?'#2ecc71':'#e74c3c'};border-radius:6px;padding:10px 14px;cursor:pointer">`+`<div style="font-size:11px;color:#5d7a99">#${rank} \u00b7 ${label} \u00b7 ${scaleNote}</div>`+`<div style="font-size:14px;color:#dfe8f2;font-weight:bold;margin:2px 0" title="${escQ(it.param)}">${it.param.length>42?it.param.slice(0,41)+'\u2026':it.param}</div>`+`<div style="font-size:13px">${isDie?`<span style="color:${strongColor};font-weight:bold">${strong} effect</span>\u00b7${dirWord(row.r)}${row.d!=null?`\u00b7Cohen\u00a0d=<b>${row.d.toFixed(2)}</b>`:''}${row.ratio!=null?`\u00b7fails\u00a0${row.ratio.toFixed(2)}\u00d7\u00a0pass`:''}`:(`r\u00a0=\u00a0<b style="color:${strongColor}">${(row.r>0?'+':'')+(row.r||0).toFixed(3)}</b>\u00b7<span style="color:${strongColor}">${strong}</span>\u00b7${dirWord(row.r)}`)}</div>`+`<div style="font-size:11px;color:#8ba0b8;margin-top:3px">${isDie?`r=${(row.r>0?'+':'')+(row.r||0).toFixed(3)}<i style="color:#5d7a99">\u00a0(r compressed for rare bins)</i>\u00b7`:''}${sig} &nbsp; ${ci} &nbsp; n=${fmtN(row.n)}</div>`+eff+`</div>`;};const dieTop=bestByTier(dieMethods).slice(0,5);const lotTop=bestByTier(lotMethods).slice(0,5);let H='';if(lotTop.length){H+=`<h3 style="color:#f39c12">\ud83c\udfaf Lot/Wafer-level drivers (best for rare bins \u2014 go investigate these lots)</h3><div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">`;lotTop.forEach((it,i)=>H+=card(it,i+1));H+='</div>';}if(dieTop.length){H+=`<h3 style="color:#7fb3d3">Die-level separators (effect size matters more than r here)</h3><div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">`;dieTop.forEach((it,i)=>H+=card(it,i+1));H+='</div>';}if(!H)H='<div class="no-data">No correlation results to summarize.</div>';H+=`<div style="margin-top:14px;padding:8px 12px;background:#0a1520;border:1px solid #1e3a5f;border-radius:5px;font-size:11px;color:#8ba0b8"><b style="color:#9fc5e8">Strength scale:</b> <b>Die-level (Cohen d):</b> <span style="color:#5d7a99">negligible</span> &lt;0.2 &middot; <span style="color:#f1c40f">small</span> 0.2&ndash;0.5 &middot; <span style="color:#e67e22">medium</span> 0.5&ndash;0.8 &middot; <span style="color:#e74c3c">large</span> &gt;0.8. &nbsp;<b>PCM lot/wafer (r):</b> <span style="color:#f1c40f">weak</span> 0.1 &middot; <span style="color:#e67e22">moderate</span> 0.3 &middot; <span style="color:#e74c3c">strong</span> 0.5.</div>`;document.getElementById('summary-area').innerHTML=H;if(META.dropped_columns&&META.dropped_columns.length){const dc=META.dropped_columns;let dH=`<details style="margin:10px 0 0 0;background:#0d1828;border:1px solid #d4a72c;border-radius:6px;padding:8px 12px"><summary style="cursor:pointer;color:#f39c12;font-size:12px;font-weight:bold">\u26a0 ${dc.length} parameter(s) removed by data-quality filter before ranking (click to expand)</summary><div style="margin-top:8px;font-size:11px;color:#c8d6e8"><table style="border-collapse:collapse;width:100%"><thead><tr><th style="padding:3px 8px;color:#5d7a99;background:#0f2030;text-align:left">Parameter</th><th style="padding:3px 8px;color:#5d7a99;background:#0f2030;text-align:left">Reason</th></tr></thead><tbody>`;dc.forEach(d=>{dH+=`<tr style="border-bottom:1px solid #1a2f45"><td style="padding:3px 8px;color:#f39c12;font-family:monospace;font-size:11px">${escQ(d.param||'')}</td><td style="padding:3px 8px;color:#7f8c8d">${escQ(d.reason||'')}</td></tr>`;});dH+=`</tbody></table></div></details>`;document.getElementById('summary-area').insertAdjacentHTML('beforeend',dH);}if(META.structural_covariates&&META.structural_covariates.length){const sc=META.structural_covariates;let sH=`<details style="margin:8px 0 0 0;background:#0d1828;border:1px solid #1e3a5f;border-radius:6px;padding:8px 12px"><summary style="cursor:pointer;color:#7fb3d3;font-size:12px;font-weight:bold">&#128205; ${sc.length} Spatial / Structural covariate(s) \u2014 not ranked with electrical params</summary><div style="margin-top:8px;font-size:11px;color:#c8d6e8"><div style="margin-bottom:6px;color:#95a5a6;line-height:1.6">These columns reflect wafer position or reticle geometry (e.g. ReticleShotRadius), not device electrical behaviour. A correlation here indicates a <b>spatial pattern</b> (edge effect, litho, CMP) \u2014 investigate with the Zone Analysis tab, not as an electrical root cause.</div><table style="border-collapse:collapse;width:100%"><thead><tr><th style="padding:3px 8px;color:#5d7a99;background:#0f2030;text-align:left">Column</th></tr></thead><tbody>`;sc.forEach(c=>{sH+=`<tr style="border-bottom:1px solid #1a2f45"><td style="padding:3px 8px;color:#7fb3d3;font-family:monospace;font-size:11px">${escQ(c)}</td></tr>`;});sH+=`</tbody></table></div></details>`;document.getElementById('summary-area').insertAdjacentHTML('beforeend',sH);}// Zone analysis summary
let zoneSumHtml='';
if(ZONE_DATA&&ZONE_DATA.zone_fail_rate&&ZONE_DATA.zone_fail_rate.length){
  const zfr=ZONE_DATA.zone_fail_rate;
  const zCols={Center:'#00c8ff',Mid:'#f9ca24',Edge:'#ff3f3f'};
  zoneSumHtml+=`<div style="margin-top:14px;padding:10px 14px;background:#0d1828;border:1px solid #1e3a5f;border-radius:6px"><b style="color:#7fb3d3;font-size:12px">Zone Fail Rate</b><div style="display:flex;gap:16px;margin-top:6px;flex-wrap:wrap">`;
  zfr.forEach(z=>{const pct=(z.fail_rate*100).toFixed(2);zoneSumHtml+=`<div style="text-align:center"><div style="font-size:18px;font-weight:bold;color:${zCols[z.zone]||'#7f8c8d'}">${pct}%</div><div style="font-size:10px;color:#7f8c8d">${z.zone}<br><span style="color:#445566">(n=${z.n.toLocaleString()})</span></div></div>`;});
  zoneSumHtml+=`</div></div>`;
}
if(ZONE_DATA&&ZONE_DATA.interactions&&ZONE_DATA.interactions.length){
  // Find worst zone×quartile cell per param group
  function _pg(name){const m=name.match(/^([A-Za-z]+)/);return m?m[1].toUpperCase():'OTHER';}
  const worstByGroup={};
  ZONE_DATA.interactions.forEach(itm=>{
    const gk=_pg(itm.param);
    itm.cells.forEach(c=>{
      if(!worstByGroup[gk]||c.fail_rate>worstByGroup[gk].fail_rate)worstByGroup[gk]={...c,param:itm.param};
    });
  });
  const groups=Object.keys(worstByGroup).sort();
  if(groups.length){
    // Compute overall baseline fail rate for strength context
    const allRates=ZONE_DATA.zone_fail_rate||[];
    const baseFr=allRates.length?allRates.reduce((s,z)=>s+z.fail_rate,0)/allRates.length:0;
    const maxFr=Math.max(...groups.map(gk=>worstByGroup[gk].fail_rate),0.001);
    // Mechanism: Q1 low = speed/weak, Q4 high = leakage/hot
    function mechTag(q,zone){
      const isLow=q.includes('Q1');const isHigh=q.includes('Q4');
      const isEdge=zone==='Edge';const isCtr=zone==='Center';
      if(isLow&&isEdge)return`<span style="background:#1a3a5c;color:#5dade2;padding:1px 5px;border-radius:3px;font-size:9px">Edge+Slow</span>`;
      if(isHigh&&isEdge)return`<span style="background:#3d1a1a;color:#e74c3c;padding:1px 5px;border-radius:3px;font-size:9px">Edge+Hot</span>`;
      if(isLow&&isCtr)return`<span style="background:#1a2f1a;color:#2ecc71;padding:1px 5px;border-radius:3px;font-size:9px">Center+Slow</span>`;
      return`<span style="background:#2d2d1a;color:#f39c12;padding:1px 5px;border-radius:3px;font-size:9px">Mixed</span>`;
    }
    function strengthBar(fr){
      const ratio=baseFr>0?fr/baseFr:1;
      const pct=Math.min(Math.round(fr/maxFr*100),100);
      let color='#f39c12',label='Moderate';
      if(ratio>=3){color='#e74c3c';label='High';}
      else if(ratio>=1.5){color='#e67e22';label='Elevated';}
      else if(ratio<0.8){color='#2ecc71';label='Low';}
      return`<div style="display:flex;align-items:center;gap:4px"><div style="width:${pct}px;max-width:80px;height:6px;background:${color};border-radius:2px"></div><span style="color:${color};font-size:10px">${label}</span></div>`;
    }
    // Count mechanisms
    const edgeLow=groups.filter(gk=>worstByGroup[gk].zone==='Edge'&&worstByGroup[gk].upm_q.includes('Q1')).length;
    const edgeHigh=groups.filter(gk=>worstByGroup[gk].zone==='Edge'&&worstByGroup[gk].upm_q.includes('Q4')).length;
    const edgeOther=groups.filter(gk=>worstByGroup[gk].zone==='Edge'&&!worstByGroup[gk].upm_q.includes('Q1')&&!worstByGroup[gk].upm_q.includes('Q4')).length;
    const notEdge=groups.filter(gk=>worstByGroup[gk].zone!=='Edge').length;
    let insight=`<div style="margin-top:8px;padding:8px 10px;background:#0a1520;border-left:3px solid #3498db;border-radius:0 4px 4px 0;font-size:11px;color:#95a5a6;line-height:1.8">`;
    insight+=`<b style="color:#7fb3d3">Pattern:</b> ${groups.length} groups — `;
    if(edgeLow)insight+=`<b style="color:#5dade2">${edgeLow} Edge+Slow (Q1)</b> · `;
    if(edgeHigh)insight+=`<b style="color:#e74c3c">${edgeHigh} Edge+Hot (Q4)</b> · `;
    if(edgeOther)insight+=`<b style="color:#f39c12">${edgeOther} Edge+Mid</b> · `;
    if(notEdge)insight+=`<b style="color:#2ecc71">${notEdge} non-Edge</b>`;
    insight=insight.replace(/\s·\s$/,'');
    if(edgeLow&&edgeHigh)insight+=`<br><b style="color:#f39c12">⚡ Dual mechanism detected</b> — both slow and hot dies fail at edge. Likely two independent marginal paths.`;
    else if(edgeLow>edgeHigh)insight+=`<br>Dominant pattern: <b style="color:#5dade2">speed/drive-strength margin</b> at wafer edge (slow dies fail).`;
    else if(edgeHigh>edgeLow)insight+=`<br>Dominant pattern: <b style="color:#e74c3c">leakage/power margin</b> at wafer edge (hot dies fail).`;
    insight+=`</div>`;
    zoneSumHtml+=`<div style="margin-top:10px;padding:10px 14px;background:#0d1828;border:1px solid #1e3a5f;border-radius:6px">`;
    zoneSumHtml+=`<b style="color:#7fb3d3;font-size:12px">Worst Zone × Quartile (per group)</b>`;
    zoneSumHtml+=insight;
    zoneSumHtml+=`<table style="margin-top:8px;border-collapse:collapse;font-size:11px;width:100%"><thead><tr>`;
    ['Group','Zone','Quartile','Fail%','Strength','Mechanism','Param'].forEach(h=>{zoneSumHtml+=`<th style="padding:3px 8px;color:#5d7a99;background:#0f2030;text-align:left;white-space:nowrap">${h}</th>`;});
    zoneSumHtml+=`</tr></thead><tbody>`;
    groups.forEach(gk=>{
      const w=worstByGroup[gk];
      zoneSumHtml+=`<tr style="border-bottom:1px solid #1a2f45">`;
      zoneSumHtml+=`<td style="padding:3px 8px;color:#7fb3d3;font-weight:bold">${gk}</td>`;
      zoneSumHtml+=`<td style="padding:3px 8px;text-align:center">${w.zone}</td>`;
      zoneSumHtml+=`<td style="padding:3px 8px;text-align:center;white-space:nowrap">${w.upm_q}</td>`;
      zoneSumHtml+=`<td style="padding:3px 8px;text-align:center;color:#e74c3c;font-weight:bold">${(w.fail_rate*100).toFixed(2)}%</td>`;
      zoneSumHtml+=`<td style="padding:3px 8px">${strengthBar(w.fail_rate)}</td>`;
      zoneSumHtml+=`<td style="padding:3px 8px">${mechTag(w.upm_q,w.zone)}</td>`;
      zoneSumHtml+=`<td style="padding:3px 8px;color:#95a5a6;font-size:10px" title="${escQ(w.param)}">${w.param.length>45?w.param.slice(0,44)+'\u2026':w.param}</td>`;
      zoneSumHtml+=`</tr>`;
    });
    zoneSumHtml+=`</tbody></table></div>`;
  }
}
window._ZONE_SUMMARY_HTML=zoneSumHtml||'';const tgt=META.target||'';const isBin=tgt.startsWith('IB==')||tgt.startsWith('FB==');const binLabel=isBin?`<b>Y-axis (0/1):</b> Binary target "${escQ(tgt)}". 0=other bin, 1=targeted bin.`:`<b>Y-axis:</b> Continuous target "${escQ(tgt)}".`;document.getElementById('summary-explain').innerHTML=`<details class="help"><summary>How to read \u2014 scores, p / q-value, CI, Cohen\u2019s d, effect size</summary><div class="help-body"><b>How to read:</b><br>${binLabel}<br><b>Score:</b> Pearson/Spearman r ranges -1 to +1. Positive = higher param means more fails.<br><b>PCM Lot-Level tab:</b> PCM params correlated as lot % fails vs lot PCM median (n = number of lots).<br><table style="margin-top:6px;border-collapse:collapse;font-size:11px"><tr><th style="text-align:left;padding:2px 12px 2px 0;color:#7fb3d3;border-bottom:1px solid #2c4a6e">|r| range</th><th style="text-align:left;padding:2px 0;color:#7fb3d3;border-bottom:1px solid #2c4a6e">Practical meaning</th></tr><tr><td style="padding:2px 12px 2px 0;color:#5d7a99">&lt; 0.05</td><td style="color:#5d7a99">Negligible — noise level</td></tr><tr><td style="padding:2px 12px 2px 0;color:#7fb3d3">0.05 – 0.10</td><td style="color:#7fb3d3">Very weak — barely detectable</td></tr><tr><td style="padding:2px 12px 2px 0;color:#f39c12">0.10 – 0.30</td><td style="color:#f39c12">Weak but real signal</td></tr><tr><td style="padding:2px 12px 2px 0;color:#2ecc71">0.30 – 0.50</td><td style="color:#2ecc71">Moderate</td></tr><tr><td style="padding:2px 12px 2px 0;color:#3498db">&gt; 0.50</td><td style="color:#3498db">Strong</td></tr></table><div style="margin-top:10px;border-top:1px solid #2c4a6e;padding-top:8px"><b style="color:#7fb3d3">Statistical terms in the cards &amp; tables:</b><table style="margin-top:4px;border-collapse:collapse;font-size:11px;line-height:1.5"><tr><td style="padding:2px 12px 2px 0;color:#f1c40f;white-space:nowrap;vertical-align:top"><b>p-value</b></td><td style="color:#c8d6e8">Chance of seeing this correlation if the parameter truly had <i>no</i> effect. Smaller = less likely to be a fluke. p&lt;0.05 is the usual &ldquo;interesting&rdquo; line.</td></tr><tr><td style="padding:2px 12px 2px 0;color:#f1c40f;white-space:nowrap;vertical-align:top"><b>q-value (FDR)</b></td><td style="color:#c8d6e8"><b>p-value corrected for multiple testing.</b> We test ~200+ parameters at once, so by chance alone ~5% would look &ldquo;significant.&rdquo; The Benjamini&ndash;Hochberg <b>False Discovery Rate</b> adjusts for this. <b>Trust q&lt;0.10</b> &mdash; it means &le;10% of hits at this level are expected false alarms. A tiny p but large q = probably noise from testing so many params.</td></tr><tr><td style="padding:2px 12px 2px 0;color:#f1c40f;white-space:nowrap;vertical-align:top"><b>95% CI</b></td><td style="color:#c8d6e8">Confidence interval for r (via Fisher <i>z</i>-transform): the plausible range for the <i>true</i> correlation. If it <b>excludes 0</b>, the direction is trustworthy; a wide CI (few lots) = uncertain.</td></tr><tr><td style="padding:2px 12px 2px 0;color:#f1c40f;white-space:nowrap;vertical-align:top"><b>Cohen&rsquo;s d</b></td><td style="color:#c8d6e8"><b>Effect size &mdash; how far apart the fail vs pass groups are, in std-deviations.</b> Unlike r, it is <i>not</i> deflated by a rare bin, so it is the better &ldquo;does this matter?&rdquo; metric here. Guide: |d|&lt;0.2 negligible &middot; 0.2&ndash;0.5 small &middot; 0.5&ndash;0.8 medium &middot; &gt;0.8 large.</td></tr><tr><td style="padding:2px 12px 2px 0;color:#f1c40f;white-space:nowrap;vertical-align:top"><b>Fail&times; ratio</b></td><td style="color:#c8d6e8">Mean of the parameter in failing dies &divide; mean in passing dies. &ldquo;1.8&times;&rdquo; = the fail group averages 80% higher &mdash; a plain-language effect size.</td></tr></table></div></div></details>`;}
/* AI REVIEW TAB (Version A — prepare only, no network calls) */
function buildAiReviewTab(){
  const area=document.getElementById('tab-area');
  // Inject glow keyframe once
  if(!document.getElementById('_ai_glow_style')){
    const s=document.createElement('style');s.id='_ai_glow_style';
    s.textContent='@keyframes aiGlow{0%,100%{box-shadow:0 0 8px 2px #2ecc71,0 0 18px 4px #27ae6088}50%{box-shadow:0 0 16px 5px #2ecc71,0 0 36px 10px #27ae6088}}';
    document.head.appendChild(s);
  }
  // Inline pill placed just above the Summary heading
  const btn=document.createElement('button');
  btn.id='ai-review-fab';
  btn.dataset.method='aireview';
  btn.innerHTML='&#129302; AI Review';
  btn.style.cssText=[
    'display:inline-block','margin-bottom:10px',
    'padding:9px 22px','border-radius:24px',
    'background:linear-gradient(135deg,#1a7a46,#27ae60)',
    'color:#fff','font-size:13px','font-weight:bold',
    'border:2px solid #2ecc71','cursor:pointer',
    'animation:aiGlow 2s ease-in-out infinite',
    'letter-spacing:0.5px','white-space:nowrap'
  ].join(';');
  btn.onclick=()=>{
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p=>p.classList.remove('active'));
    const pane=document.getElementById('pane-aireview');
    if(pane)pane.classList.add('active');
    document.getElementById('tab-area').scrollIntoView({behavior:'smooth',block:'start'});
    renderAiReviewTab();
    const tb=document.querySelector('.tab-btn[data-method="aireview"]');
    if(tb)tb.classList.add('active');
  };
  // Insert before the "Summary - Strongest Parameters" h2
  const summaryH2=Array.from(document.querySelectorAll('h2')).find(h=>h.textContent.includes('Summary'));
  if(summaryH2)summaryH2.parentNode.insertBefore(btn,summaryH2);
  else document.body.insertBefore(btn,document.body.firstChild);
  // Also add a normal tab-bar entry so it integrates with showTab/activateTab
  const bar=document.getElementById('tab-bar');
  const tbBtn=document.createElement('button');
  tbBtn.className='tab-btn';tbBtn.dataset.method='aireview';
  tbBtn.style.cssText='background:#1a4a2e;color:#2ecc71;border-color:#2ecc71;font-weight:bold';
  tbBtn.textContent='🤖 AI Review';
  tbBtn.onclick=()=>{
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active',b===tbBtn));
    document.querySelectorAll('.tab-pane').forEach(p=>p.classList.toggle('active',p.id==='pane-aireview'));
    renderAiReviewTab();
  };
  bar.appendChild(tbBtn);
  const pane=document.createElement('div');
  pane.className='tab-pane'; pane.id='pane-aireview';
  pane.innerHTML='<div id="aireview-content" style="color:#7f8c8d;padding:20px;font-style:italic">Click tab to load...</div>';
  area.appendChild(pane);
}
function _aiSlimFindings(topN){
  topN = topN || 30;
  const meta = (typeof META!=='undefined')?META:{};
  const out = {meta:{target:meta.target, n_total:meta.n_total, n_lots:(meta.lots||[]).length,
                     n_target_hits:meta.n_target_hits, hit_rate_pct:(meta.n_total?(100*(meta.n_target_hits||0)/meta.n_total):null)},
               methods:{}};
  const labels = (typeof METHOD_LABELS!=='undefined')?METHOD_LABELS:{};
  Object.keys(labels).forEach(m=>{
    const rows=(typeof CORR!=='undefined' && CORR[m])?CORR[m]:[];
    if(!rows.length) return;
    out.methods[m]={label:labels[m], top:rows.slice(0,topN).map(r=>({
      param:r.param, r:r.r, p:r.p, q:r.q, n:r.n,
      ci:(r.ci_lo!=null?[r.ci_lo,r.ci_hi]:null),
      cohen_d:(r.d!=null?r.d:undefined),
      fail_ratio:(r.ratio!=null?r.ratio:undefined),
      spread_r:(r.spread_r!=null?r.spread_r:undefined)
    }))};
  });
  if(typeof INTRAFIELD_DATA!=='undefined' && INTRAFIELD_DATA){
    out.intra_reticle = {
      overall_fail_rate: INTRAFIELD_DATA.overall,
      n_positions: INTRAFIELD_DATA.n_positions,
      dieloc: (INTRAFIELD_DATA.cells||[]).map(c=>({loc:c.loc, fail_rate:c.fail_rate, rel_risk:c.rr, pooled_z:c.z})),
      stratified: (INTRAFIELD_DATA.paired&&INTRAFIELD_DATA.paired.rows)
        ? INTRAFIELD_DATA.paired.rows.map(r=>({loc:r.loc, mean_dev:r.mean_dev, z:r.z}))
        : null,
      consistency: INTRAFIELD_DATA.consistency || null,
      fb_by_dieloc: (typeof INTRAFIELD_FB!=='undefined'&&INTRAFIELD_FB&&INTRAFIELD_FB.rows)
        ? {rows: INTRAFIELD_FB.rows.map(r=>({fb:r.fb, n_hits:r.n_hits, cells:(r.cells||[]).map(c=>({loc:c.loc, z:c.z, mean_dev:c.mean_dev}))})),
           best: INTRAFIELD_FB.best||null}
        : null
    };
  }
  return out;
}
function _aiZoneSummary(){
  if(typeof ZONE_DATA==='undefined'||!ZONE_DATA||!ZONE_DATA.zone_fail_rate||!ZONE_DATA.zone_fail_rate.length)return'';
  return'Zone fail rates: '+ZONE_DATA.zone_fail_rate.map(z=>z.zone+' '+(z.fail_rate*100).toFixed(2)+'% (n='+z.n.toLocaleString()+')').join(', ')+'.';
}
function _aiBuildPrompt(findings, glossaryText, zoneText){
  const m=findings.meta;
  const rate=(m.hit_rate_pct!=null)?m.hit_rate_pct.toFixed(2)+'%':'n/a';
  const rare=(m.hit_rate_pct!=null && m.hit_rate_pct<2)?' This is a RARE-EVENT regime, so prioritize effect size (Cohen d, fail ratio) and lot/wafer-level signals over tiny die-level r.':'';
  let lines=[];
  lines.push('You are a semiconductor yield-analysis expert reviewing a Sort + Etest / PCM correlation study.');
  lines.push('');
  if(glossaryText&&glossaryText.trim()){
    lines.push('Parameter meanings (domain context):');
    lines.push(glossaryText.trim());
    lines.push('');
  }
  if(zoneText&&zoneText.trim()){
    lines.push('Spatial context:');
    lines.push(zoneText.trim());
    lines.push('');
  }
  lines.push('Context:');
  lines.push('- Target bin: '+(m.target||'?'));
  lines.push('- Dies analyzed: '+(m.n_total!=null?m.n_total.toLocaleString():'?')+'  | Lots: '+(m.n_lots||'?')+'  | Target hits: '+(m.n_target_hits!=null?m.n_target_hits.toLocaleString():'?')+' ('+rate+').'+rare);
  lines.push('');
  lines.push('Top findings per method (r=correlation, p=p-value, q=FDR-adjusted, CI=95% interval, Cohen d & fail_ratio=effect size, spread_r=within-wafer non-uniformity):');
  Object.keys(findings.methods).forEach(mk=>{
    const M=findings.methods[mk];
    lines.push('');
    lines.push('### '+M.label);
    M.top.slice(0,30).forEach((r,i)=>{
      let s='  '+(i+1)+'. '+r.param+'  r='+(r.r!=null?r.r.toFixed(4):'?');
      if(r.q!=null) s+='  q='+r.q.toExponential(1);
      else if(r.p!=null) s+='  p='+r.p.toExponential(1);
      if(r.cohen_d!=null) s+='  d='+r.cohen_d;
      if(r.fail_ratio!=null) s+='  ratio='+r.fail_ratio;
      if(r.spread_r!=null) s+='  spread_r='+r.spread_r;
      s+='  n='+(r.n!=null?r.n.toLocaleString():'?');
      lines.push(s);
    });
  });
  lines.push('');
  if(findings.intra_reticle){
    const ir=findings.intra_reticle;
    lines.push('### Intra-Reticle (die position within the reticle field)');
    lines.push('Note: PCM/etest params are scribe-line (~1 per field) and have NO intra-field resolution, so die-position signals come only from die-level data. ReticleShotRadius is wafer position, NOT die-in-field.');
    (ir.dieloc||[]).forEach(c=>{
      let strat='';
      if(ir.stratified){
        const sr=ir.stratified.find(s=>s.loc===c.loc);
        if(sr) strat=' stratified_z='+(sr.z!=null?sr.z.toFixed(2):'n/a');
      }
      lines.push('DieLoc '+c.loc+': fail '+(c.fail_rate!=null?(c.fail_rate*100).toFixed(2)+'%':'n/a')+
        ' rel_risk '+(c.rel_risk!=null?c.rel_risk.toFixed(2):'n/a')+'x'+
        ' pooled_z '+(c.pooled_z!=null?c.pooled_z.toFixed(2):'n/a')+strat);
    });
    if(ir.consistency){
      const co=ir.consistency;
      lines.push('Consistency: DieLoc '+(co.worst_loc||'?')+' worst-ranked in '+(co.agree!=null?co.agree:'?')+'/'+(co.n_views!=null?co.n_views:'?')+' views.');
    }
    if(ir.fb_by_dieloc&&ir.fb_by_dieloc.best){
      const b=ir.fb_by_dieloc.best;
      lines.push('Strongest FB x DieLoc: FB '+(b.fb||'?')+' at DieLoc '+(b.loc||'?')+
        ' (z='+(b.z!=null?b.z.toFixed(2):'n/a')+', +'+(b.mean_dev!=null?(b.mean_dev*100).toFixed(2)+'%':'n/a')+').');
    }
    lines.push('');
  }
  lines.push('Please produce the following sections:');
  lines.push('');
  lines.push('(A) A markdown table with columns: Driver | Level | Evidence (r, q, effect) | Physical hypothesis | Recommended action');
  lines.push('    Include the top credible drivers only (q<0.10 or strong effect size). Use the parameter meanings above to fill Physical hypothesis.');
  lines.push('');
  lines.push('(B) Investigate first — list the specific lots or wafers (by ID if visible in the data) that are most likely to explain the failure, and why.');
  lines.push('');
  lines.push('(C) Likely noise — explicitly call out any result where p is tiny but q is large (>0.20), meaning it is probably a false positive from testing many parameters simultaneously.');
  lines.push('');
  lines.push('(D) Collinear / duplicate inputs — note any groups of parameters that share nearly identical correlations (same r/p/q rounded), indicating they are derived from the same underlying signal and should not be double-counted.');
  lines.push('');
  lines.push('(E) Failure-mode interpretation — one paragraph explaining the most likely physical failure mechanism given all of the above, including any spatial (zone/edge) contribution.');
  lines.push('');
  lines.push('(F) Die-position (reticle/die-frame) signature — state whether there is a credible die-position signal in the intra-reticle data, and if so which DieLoc and FB show the strongest effect.');
  return lines.join('\n');
}
function _aiDownload(name, text, mime){
  const blob=new Blob([text],{type:mime||'text/plain'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a'); a.href=url; a.download=name;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  setTimeout(()=>URL.revokeObjectURL(url), 1500);
}
function _aiRebuildPrompt(){
  const gl=document.getElementById('ai-glossary');
  const ta=document.getElementById('ai-prompt');
  if(!gl||!ta)return;
  const findings=_aiSlimFindings(30);
  ta.value=_aiBuildPrompt(findings, gl.value, _aiZoneSummary());
}
function renderAiReviewTab(){
  const el=document.getElementById('aireview-content');
  if(!el) return;
  const findings=_aiSlimFindings(30);
  const storedGlossary=localStorage.getItem('aiReviewGlossary');
  const defaultGlossary=[
    'Rc_* = contact / via resistance',
    'Isat_* = transistor drive current (saturation)',
    'Ioff_* / Poff_* = leakage current / off-state power',
    'Vts_* = threshold voltage shift',
    'Con_* = continuity / shorts screen',
    'Pwr_* = active power',
    'Td_* = timing / propagation delay',
    'SPA_M* = stress / parametric aging monitor (fill in specific meaning)',
    'UPM_* = speed / Vmin sweep (ultra-parametric measurement)',
    'PTH_POWER_* = SICC / power-supply screen',
    'ReticleShotRadius = wafer position covariate (geometry, not electrical)'
  ].join('\n');
  const glossaryText=storedGlossary!=null?storedGlossary:defaultGlossary;
  const zoneText=_aiZoneSummary();
  const promptText=_aiBuildPrompt(findings, glossaryText, zoneText);
  const jsonText=JSON.stringify(findings,null,1);
  const tgt=(findings.meta.target||'report').replace(/[^\w=.-]/g,'_');
  el.innerHTML=''
   +'<div style="background:#0d1828;border:1px solid #1e3a5f;border-radius:6px;padding:12px 14px;margin-bottom:10px;font-size:12px;color:#c8d6e8;line-height:1.6">'
   +'<b style="color:#7fb3d3">AI Review - prepare an expert summary with Copilot</b><br>'
   +'This tab runs entirely in your browser - <b>no data is sent anywhere</b>. It packages the findings so you can review them with Intel Enterprise LLM chatbot, e.g Co-Pilot 365, VSCode Co-Pilot.'
   +'<ol style="margin:8px 0 0 18px;padding:0">'
   +'<li>Click <b>Download findings.json</b> and save it into your VS Code workspace.</li>'
   +'<li>Edit the parameter glossary below if needed (saved automatically).</li>'
   +'<li>Click <b>Copy prompt</b> (or Download prompt.md).</li>'
   +'<li>In Copilot Chat, type <code>#file:findings.json</code> then paste the prompt.</li>'
   +'</ol></div>'
   +'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">'
   +'<button id="ai-dl-json" style="padding:6px 12px;background:#1a5276;color:#ecf0f1;border:1px solid #2c4a6e;border-radius:5px;cursor:pointer;font-size:12px">Download findings.json</button>'
   +'<button id="ai-dl-md" style="padding:6px 12px;background:#1a5276;color:#ecf0f1;border:1px solid #2c4a6e;border-radius:5px;cursor:pointer;font-size:12px">Download prompt.md</button>'
   +'<button id="ai-copy" style="padding:6px 12px;background:#0e6655;color:#ecf0f1;border:1px solid #148f77;border-radius:5px;cursor:pointer;font-size:12px">Copy prompt</button>'
   +'<span id="ai-copied" style="align-self:center;color:#2ecc71;font-size:12px;display:none">copied</span>'
   +'</div>'
   +'<div style="color:#7fb3d3;font-size:12px;margin:6px 0 2px"><b>Parameter glossary</b> (one entry per line, edit to add domain context — saved in browser):</div>'
   +'<textarea id="ai-glossary" style="width:100%;height:140px;background:#0b1622;color:#b8d0e8;border:1px solid #22405f;border-radius:6px;padding:8px 10px;font-family:Consolas,monospace;font-size:11px;line-height:1.5;margin-bottom:8px"></textarea>'
   +'<div style="color:#7fb3d3;font-size:12px;margin:6px 0 4px"><b>Editable prompt</b> (auto-updates when you edit the glossary; tweak before copying):</div>'
   +'<textarea id="ai-prompt" style="width:100%;height:320px;background:#0b1622;color:#dbe7f3;border:1px solid #22405f;border-radius:6px;padding:10px;font-family:Consolas,monospace;font-size:12px;line-height:1.5"></textarea>';
  const gl=document.getElementById('ai-glossary');
  gl.value=glossaryText;
  gl.oninput=()=>{
    localStorage.setItem('aiReviewGlossary', gl.value);
    _aiRebuildPrompt();
  };
  document.getElementById('ai-prompt').value=promptText;
  document.getElementById('ai-dl-json').onclick=()=>_aiDownload('findings_'+tgt+'.json', jsonText, 'application/json');
  document.getElementById('ai-dl-md').onclick=()=>_aiDownload('prompt_'+tgt+'.md', document.getElementById('ai-prompt').value, 'text/markdown');
  document.getElementById('ai-copy').onclick=()=>{
    const ta=document.getElementById('ai-prompt'); ta.select();
    try{ navigator.clipboard.writeText(ta.value); }catch(e){ try{document.execCommand('copy');}catch(e2){} }
    const c=document.getElementById('ai-copied'); c.style.display='inline'; setTimeout(()=>c.style.display='none',1500);
  };
}
/* END AI REVIEW TAB */
/* SPATIAL / RADIAL TAB */
function buildSpatialTab(){
  const rows=(CORR['methods_structural']||[]);
  if(!rows.length)return;
  const P_THR=0.05, R_PRACTICAL=0.05;
  // actionable = p<0.05 AND |r|>=0.05; negligible = p<0.05 AND |r|<0.05
  // allInsig = no rows at all have p<0.05 (truly no spatial signal)
  const actionRows=rows.filter(r=>r.p!=null&&r.p<P_THR&&Math.abs(r.r||0)>=R_PRACTICAL);
  const negligibleRows=rows.filter(r=>r.p!=null&&r.p<P_THR&&Math.abs(r.r||0)<R_PRACTICAL);
  const allInsig=rows.every(r=>r.p==null||r.p>=P_THR);
  const bar=document.getElementById('tab-bar');
  const area=document.getElementById('tab-area');
  const btn=document.createElement('button');
  btn.className='tab-btn';btn.dataset.method='methods_structural';
  btn.textContent='Spatial / Radial'+(actionRows.length?` \u26a0\ufe0f ${actionRows.length}`:'');
  if(actionRows.length)btn.style.cssText='border-color:#8250df;color:#c084fc';
  btn.onclick=()=>{
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active',b===btn));
    document.querySelectorAll('.tab-pane').forEach(p=>p.classList.toggle('active',p.id==='pane-methods_structural'));
  };bar.appendChild(btn);
  let trows='';
  rows.forEach((row,i)=>{
    // Read column: purely |r| threshold, independent of p-value
    const isPractical=Math.abs(row.r||0)>=R_PRACTICAL;
    const isActionable=isPractical&&row.p!=null&&row.p<P_THR;
    const rStr=row.r!=null?row.r.toFixed(4):'—';
    const pStr=row.p!=null?(row.p<0.001?row.p.toExponential(2):row.p.toFixed(4)):'—';
    const read=isPractical
      ?'Radial/edge pattern \u2014 check wafer map (Zone Analysis)'
      :'Negligible spatial effect \u2014 no action';
    const badge=isActionable?`<span style="background:#8250df;color:#fff;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600;margin-left:6px">\u26a0 SPATIAL PATTERN</span>`:'';
    const rowStyle=isActionable?'background:#1a0d2e;':(allInsig?'color:#556677;':'');
    trows+=`<tr style="${rowStyle}"><td>${i+1}</td>`+
      `<td title="${escQ(row.param)}">${row.param}${badge}</td>`+
      `<td class="${isActionable?(row.r>0?'r-pos':'r-neg'):'r-neu'}">${rStr}</td>`+
      `<td>${pStr}</td><td>${row.n!=null?row.n.toLocaleString():'—'}</td>`+
      `<td style="font-size:11px;${allInsig?'color:#556677;':''}">${read}</td></tr>`;
  });
  const alertBox=actionRows.length?`<div style="background:#2d1150;border:2px solid #8250df;border-radius:8px;padding:10px 14px;margin-bottom:12px">
<b style="color:#c084fc;font-size:13px">\u26a0\ufe0f Spatial / Edge Pattern Detected</b><br>
<span style="font-size:12px;color:#e2d9f3">
${actionRows.map(r=>`<b>${escQ(r.param)}</b> (r=${(r.r||0).toFixed(3)}, p=${r.p!=null?(r.p<0.001?r.p.toExponential(1):r.p.toFixed(3)):'—'})`).join(' &bull; ')}<br><br>
Failures correlate with wafer geometry \u2014 <b>this is not an electrical device parameter.</b>
Review the wafer map for edge/radial clustering. Likely cause: edge uniformity, litho/focus, CMP, or chuck/clamp effect.
Do <u>not</u> include these in the electrical driver shortlist.
</span></div>
`:negligibleRows.length?`<div style="background:#1a2535;border:1px solid #3a4a5a;border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:12px;color:#7f9aaa">
Spatial effect statistically detectable but negligible (|r|&lt;${R_PRACTICAL}) \u2014 no wafer-map review needed.
${negligibleRows.map(r=>`${escQ(r.param)}: r=${(r.r||0).toFixed(4)}, p=${r.p!=null?(r.p<0.001?r.p.toExponential(1):r.p.toFixed(3)):'—'}`).join(' &bull; ')}
</div>`:'';
  const introText=allInsig
    ?'No significant spatial/structural signal in this bin.'
    :actionRows.length
      ?'Wafer-geometry covariates \u2014 <strong>not</strong> device electrical measurements. A correlation here indicates a spatial pattern (edge, litho/focus, CMP, chuck/clamp) \u2014 investigate radial/edge process uniformity, not a device parameter.'
      :'Spatial correlation statistically detectable but effect is negligible (|r|&lt;0.05 for all fields) \u2014 no wafer-map review needed.';
  const introStyle=allInsig
    ?'background:#1a2535;color:#556677;padding:8px 12px;border-radius:6px;font-size:12px;margin-bottom:10px'
    :'background:#f5f0ff;color:#3b1f6b;padding:8px 12px;border-radius:6px;font-size:12px;margin-bottom:10px';
  const sectionStyle=allInsig
    ?'border:1px solid #2a3a4a;border-radius:8px;padding:14px;margin:12px 0;opacity:0.7'
    :'border:1px solid #8250df;border-radius:8px;padding:14px;margin:12px 0';
  const pane=document.createElement('div');
  pane.className='tab-pane';pane.id='pane-methods_structural';
  pane.innerHTML=`<div style="${sectionStyle}">
<h2 style="color:${allInsig?'#556677':'#8250df'};margin-top:0;font-size:16px">Spatial / Radial Analysis</h2>
${alertBox}
<div style="${introStyle}">${introText}</div>
<table style="width:100%;border-collapse:collapse;font-size:12px">
<thead><tr style="color:#7fb3d3;border-bottom:1px solid #1e3a5f">
  <th style="text-align:left;padding:4px 8px">#</th>
  <th style="text-align:left;padding:4px 8px">Field</th>
  <th style="text-align:left;padding:4px 8px">r</th>
  <th style="text-align:left;padding:4px 8px">p (uncorrected)</th>
  <th style="text-align:left;padding:4px 8px">n</th>
  <th style="text-align:left;padding:4px 8px">Read</th>
</tr></thead>
<tbody>${trows}</tbody>
</table>
<div style="margin-top:10px;font-size:11px;color:#7f8c8d">
  p-values are uncorrected (threshold p&lt;${P_THR}). Spatial fields share no FDR q pool with electrical parameters.
  n_electrical = ${META.n_electrical_params!=null?META.n_electrical_params:'—'} &bull;
  n_spatial = ${rows.length} &bull; actionable = ${actionRows.length} &bull; negligible = ${negligibleRows.length}
</div></div>`;
  area.appendChild(pane);
  // Inject alert banner into summary area only for actionable spatial fields
  if(actionRows.length){
    const summaryEl=document.getElementById('summary-area');
    if(summaryEl){
      const banner=document.createElement('div');
      banner.style.cssText='background:#2d1150;border:2px solid #8250df;border-radius:8px;padding:10px 14px;margin:10px 0;cursor:pointer';
      banner.innerHTML=`<b style="color:#c084fc">\u26a0\ufe0f Spatial / Edge Pattern \u2014 Wafer-Map Review Required</b><br>`+
        `<span style="font-size:12px;color:#e2d9f3">`+
        actionRows.map(r=>`<b>${escQ(r.param)}</b> r=${(r.r||0).toFixed(3)}`).join(' &bull; ')+
        ` \u2014 failures track wafer geometry. <u>Not an electrical driver.</u> Click to open Spatial tab.</span>`;
      banner.onclick=()=>btn.click();
      summaryEl.prepend(banner);
    }
  }
}
/* END SPATIAL / RADIAL TAB */
function buildHowToReadTab(){
  buildRcaTab('how_to_read','How to Read',function(){
    const el=document.getElementById('how_to_read-content');if(!el)return;
    el.innerHTML=`<div style="max-width:820px;font-size:12px;line-height:1.8;color:#c8d6e8">
<details class="help" open><summary>Pearson r (Die-Level)</summary><div class="help-body">Measures linear association between a parameter value and the binary fail indicator. Because this is a rare fail mode, Pearson r is mathematically capped near zero even for a real driver. <b>Read Pearson together with Cohen d, fail ratio, confidence interval, and q-value.</b></div></details>
<details class="help"><summary>Spearman rho (Die-Level)</summary><div class="help-body">Measures rank-based monotonic association. Robust to outliers and skewed process data. Parameters appearing in both Pearson and Spearman are higher-confidence candidates.</div></details>
<details class="help"><summary>PCM Lot-Level</summary><div class="help-body">Compares lot-level PCM summary values against lot-level fail rate. With fewer than ~30 lots, treat p/q values cautiously.</div></details>
<details class="help"><summary>PCM Wafer-Level</summary><div class="help-body">Compares wafer-level PCM values against wafer-level fail rate. Often more sensitive than lot-level because within-lot wafer differences reveal excursions hidden by lot averaging.</div></details>
<details class="help"><summary>PCM Deviation &mdash; Lot</summary><div class="help-body">Measures whether a lot&rsquo;s PCM value deviates from the broader baseline and whether that deviation tracks fail rate. Detects U-shaped / non-monotonic process-window effects.</div></details>
<details class="help" open><summary>PCM Deviation &mdash; Wafer (headline signal)</summary><div class="help-body">Measures whether each wafer&rsquo;s PCM median deviates from its expected lot baseline and whether that deviation tracks wafer fail rate. In this dataset it provides the strongest signal, indicating the fail is more tied to wafer-level process deviation than to lot-average behavior.</div></details>
<div style="margin-top:14px;padding:10px 14px;background:#0d1828;border:1px solid #1e3a5f;border-radius:6px">
<b style="color:#7fb3d3">Key statistical terms</b><br><br>
<b style="color:#f1c40f">q-value (FDR)</b> &mdash; p-value corrected for multiple testing. Trust q&lt;0.10.<br>
<b style="color:#f1c40f">95% CI not crossing zero</b> &mdash; stable trend direction.<br>
<b style="color:#f1c40f">Cohen d</b> &mdash; separation between pass/fail populations; large |d| means strong separation even when r is small.<br>
<b style="color:#f1c40f">Signal badge</b> &mdash; Strong (q≤0.01, |r|≥0.30) &bull; Moderate (q≤0.05, |r|≥0.15) &bull; Weak/directional (q≤0.10) &bull; Exploratory (otherwise).
</div></div>`;
  });
}
/* END HOW TO READ TAB */
function renderIntraFieldBadge(){
  if(typeof INTRAFIELD_DATA==='undefined'||!INTRAFIELD_DATA)return;
  let msg=null,level='info';
  const C=(typeof INTRAFIELD_DATA!=='undefined'&&INTRAFIELD_DATA)?INTRAFIELD_DATA.consistency:null;
  // C.agree>=3 banner is owned by renderIntraFieldBadgeConsistency — skip here to avoid duplicates
  if(!msg&&typeof INTRAFIELD_FB!=='undefined'&&INTRAFIELD_FB&&INTRAFIELD_FB.best
     &&INTRAFIELD_FB.best.z!=null&&INTRAFIELD_FB.best.z>=3){
    const b=INTRAFIELD_FB.best;
    msg=`Intra-Reticle: within this IB, <b>FB ${b.fb}</b> fails `
       +`<b>${(b.mean_dev*100).toFixed(2)}%</b> above baseline at <b>DieLoc ${b.loc}</b> `
       +`(stratified z=${b.z}). Likely a die-frame / reticle field signature isolated to that FB.`;
    level='warn';
  }
  const P=INTRAFIELD_DATA.paired;
  if(!msg&&P&&P.rows&&P.rows.length){
    const top=P.rows[0];
    if(top.z!=null&&top.z>=3&&top.mean_dev!=null&&top.mean_dev>0){
      msg=`Intra-Reticle: <b>DieLoc ${top.loc}</b> fails `
         +`<b>${(top.mean_dev*100).toFixed(2)}%</b> above its shot/wafer baseline `
         +`(stratified z=${top.z}). Likely a reticle/litho field signature.`;
      level='warn';
    }
  }
  if(!msg){
    const hot=(INTRAFIELD_DATA.cells||[]).find(c=>c.significant);
    if(hot){msg=`Intra-Reticle: <b>DieLoc ${hot.loc}</b> fail ${(hot.fail_rate*100).toFixed(2)}% `
                +`(${hot.rr}x field, z=${hot.z}).`;level='warn';}
  }
  if(!msg)return;
  const host=document.getElementById('summary-area');
  if(!host)return;
  const div=document.createElement('div');
  div.style.cssText='margin:8px 0;padding:10px 14px;border-radius:6px;border:1px solid '
    +(level==='warn'?'#a5641a':'#22405f')+';background:'+(level==='warn'?'#2a1e0d':'#0d1828')
    +';color:#f0d8b0;font-size:13px;cursor:pointer';
  div.innerHTML='\u26A0\uFE0F '+msg+' <span style="color:#7fb3d3;text-decoration:underline">Open Intra-Reticle tab</span>';
  div.onclick=()=>{const b=document.querySelector('.tab-btn[data-method="intrafield"]');if(b)b.click();};
  host.parentNode.insertBefore(div,host);
}
/* ---- Intra-Reticle relative-risk magnitude tier ---- */
function _rrTier(rr){
  if(rr==null) return {label:'', color:'#5d7a99'};
  if(rr>=2.0) return {label:'strong',      color:'#e74c3c'};
  if(rr>=1.5) return {label:'notable',     color:'#e67e22'};
  if(rr>=1.2) return {label:'real but modest', color:'#f1c40f'};
  return               {label:'weak',       color:'#5d7a99'};
}
/* ---- Intra-Reticle consistency (JS-only; reads existing report data) ---- */
function _intraConsistency(){
  if(typeof INTRAFIELD_DATA==='undefined' || !INTRAFIELD_DATA) return null;
  const D=INTRAFIELD_DATA, views={};
  if(D.cells && D.cells.length){
    views.pooled_failrate = D.cells.reduce((a,c)=>c.fail_rate>a.fail_rate?c:a).loc;
    views.relative_risk   = D.cells.reduce((a,c)=>((c.rr||0)>(a.rr||0))?c:a).loc;
  }
  if(D.paired && D.paired.rows && D.paired.rows.length){
    const pr=D.paired.rows.filter(r=>r.mean_dev!=null);
    if(pr.length) views.stratified = pr.reduce((a,r)=>r.mean_dev>a.mean_dev?r:a).loc;
  }
  if(typeof INTRAFIELD_FB!=='undefined' && INTRAFIELD_FB && INTRAFIELD_FB.best
     && INTRAFIELD_FB.best.z!=null){
    views.fb_best = INTRAFIELD_FB.best.loc;
  }
  const vals=Object.values(views);
  if(!vals.length) return null;
  const tally={}; vals.forEach(v=>tally[v]=(tally[v]||0)+1);
  let worst=null,agree=0;
  Object.keys(tally).forEach(k=>{if(tally[k]>agree){agree=tally[k];worst=+k;}});
  return {views, worst_loc:worst, agree, n_views:vals.length};
}
function renderIntraConsistency(){
  const host=document.getElementById('intrafield-content')||document.getElementById('pane-intrafield');
  if(!host) return;
  const C=_intraConsistency(); if(!C) return;
  if(document.getElementById('intra-consistency-box')) return;
  const strong=C.agree>=3;
  const div=document.createElement('div'); div.id='intra-consistency-box';
  div.style.cssText='margin:12px 0;padding:11px 14px;border-radius:6px;border:1px solid '
    +(strong?'#a5641a':'#22405f')+';background:'+(strong?'#2a1e0d':'#0d1828')+';color:'
    +(strong?'#f0d8b0':'#c8d6e8')+';font-size:13px;line-height:1.55';
  const vlist=Object.keys(C.views).map(k=>k.replace('_',' ')+'\u2192DieLoc '+C.views[k]).join(' \u00b7 ');
  const worstCell=(INTRAFIELD_DATA.cells||[]).find(c=>c.loc===C.worst_loc);
  const wTier=_rrTier(worstCell?worstCell.rr:null);
  const magSentence=worstCell&&worstCell.rr!=null
    ?' Magnitude is <b>'+wTier.label+'</b> ('+worstCell.rr.toFixed(2)+'\u00d7 the field average) \u2014 significance confirms it is real; the relative risk indicates how large it is.'
    :'';
  if(strong){
    div.innerHTML='\u26A0\uFE0F <b>Consistency flag: DieLoc '+C.worst_loc+'</b> is the worst-ranked '
      +'position in <b>'+C.agree+'/'+C.n_views+'</b> independent views ('+vlist+'). '
      +'No single test is dramatic, but agreement across independent views is a credible '
      +'intra-field signature \u2014 consistent with a die-frame / reticle field issue at this position.'
      +magSentence;
  } else {
    div.innerHTML='Consistency: no die position is worst across a majority of views ('+vlist+')'+magSentence;
  }
  host.appendChild(div);
}
function renderIntraFieldBadgeConsistency(){
  const C=_intraConsistency(); if(!C || C.agree<3) return;
  const host=document.getElementById('summary-area'); if(!host) return;
  const _old=document.getElementById('intra-consistency-badge'); if(_old) _old.remove();
  const div=document.createElement('div'); div.id='intra-consistency-badge';
  div.style.cssText='margin:8px 0;padding:10px 14px;border-radius:6px;border:1px solid #a5641a;'
    +'background:#2a1e0d;color:#f0d8b0;font-size:13px;cursor:pointer';
  const worstCellB=(INTRAFIELD_DATA.cells||[]).find(c=>c.loc===C.worst_loc);
  const wTierB=_rrTier(worstCellB?worstCellB.rr:null);
  const magB=worstCellB&&worstCellB.rr!=null
    ?' Magnitude is <b>'+wTierB.label+'</b> ('+worstCellB.rr.toFixed(2)+'\u00d7 the field average) \u2014 significance confirms it is real; the relative risk indicates how large it is.'
    :'';
  div.innerHTML='\u26A0\uFE0F Intra-Reticle: <b>DieLoc '+C.worst_loc+'</b> is worst-ranked in <b>'
    +C.agree+'/'+C.n_views+'</b> independent views \u2014 a consistent intra-field signature '
    +'(likely a die-frame / reticle field issue), even though no single test is dramatic.'
    +magB+' <span style="color:#7fb3d3;text-decoration:underline">Open Intra-Reticle tab</span>';
  div.onclick=()=>{const b=document.querySelector('.tab-btn[data-method="intrafield"]');if(b)b.click();};
  host.parentNode.insertBefore(div, host);
}
(function init(){buildSummary();renderIntraFieldBadge();renderIntraFieldBadgeConsistency();buildRcaTab('lottrend','Lot Level Trend',renderLotTrendTab);buildRcaTab('wafertrend','Wafer Level Trend',renderWaferTrendTab);buildOneMethodTab('pcm_wdev');buildOneMethodTab('pcm_dev');buildOneMethodTab('pcm_wafer');buildOneMethodTab('pcm_lot');buildZoneTab();buildOneMethodTab('pearson');buildOneMethodTab('spearman');buildSpatialTab();buildRcaTab('repeatability','Repeatability',renderRepeatabilityTab);buildRcaTab('cofailure','Co-Failure',renderCoFailureTab);buildRcaTab('reticle','Reticle',renderReticleTab);buildIntraFieldTab();buildAiReviewTab();buildHowToReadTab();activateTab('lottrend');renderLotTrendTab();})();
const FB_PAL={804:'#29b6f6',806:'#26d9b0',807:'#a78bfa',808:'#ffb347',809:'#ff6e40',811:'#ff5252',812:'#ff1744',815:'#e8c999',816:'#ffd740',817:'#80cbc4',818:'#b0bec5',819:'#ffab76',820:'#80deea',821:'#b388ff',822:'#fff176',823:'#69f0ae',824:'#40c4ff',825:'#ea80fc',827:'#18ffff',828:'#ff9100',829:'#7986cb',830:'#9575cd',831:'#f48fb1',833:'#40e0ff',835:'#f72585',837:'#be00ff',899:'#ffe57a'};
function fbColor(fb){return FB_PAL[fb]||'#5577aa';}
const IB_COLS=['#3498db','#2ecc71','#e67e22','#9b59b6','#1abc9c','#e74c3c','#f1c40f','#34495e','#16a085','#8e44ad'];
let _wmColorMode='target';
function _wmDieColor(d){
  if(_wmColorMode==='fb'){const c=FB_PAL[d.fb];return c||'#5577aa';}
  if(_wmColorMode==='ib'){if(d.ib==null)return'#334455';return IB_COLS[d.ib%IB_COLS.length]||'#5577aa';}
  if(_wmColorMode==='zone'){const zc=['#3498db','#f39c12','#e74c3c'];return d.z!=null&&zc[d.z]?zc[d.z]:'#445566';}
  return d.t?'#e74c3c':'#0d1828';
}
function _buildWmRetShots(retMap){
  if(!retMap||!Object.keys(retMap).length)return[];
  const sb={};
  Object.entries(retMap).forEach(([k,v])=>{
    if(k==='_meta')return;  // skip debug metadata
    const p=k.split(','),sx=+p[0],sy=+p[1],si=v[2];
    if(!sb[si]){sb[si]={x0:sx,y0:sy,x1:sx,y1:sy};}
    const b=sb[si];
    if(sx<b.x0)b.x0=sx;if(sx>b.x1)b.x1=sx;
    if(sy<b.y0)b.y0=sy;if(sy>b.y1)b.y1=sy;
  });
  return Object.entries(sb).sort((a,b)=>+a[0]-(+b[0])).map(e=>{const si=+e[0],b=e[1];return[si,b.x0,b.y0,b.x1,b.y1];});
}
function _wmGeom(TW,xMin,xMax,yMin,yMax){
  const PAD=3,xCnt=xMax-xMin+1,yCnt=yMax-yMin+1;
  const xSpan=xMax-xMin,ySpan=yMax-yMin;
  const cs=(TW-PAD*2)/xCnt;
  const csy=(xSpan>0&&ySpan>0)?(cs*xSpan/ySpan):cs;
  const TH=Math.round(yCnt*csy+PAD*2);
  const xCtr=(xMin+xMax)/2,yCtr=(yMin+yMax)/2;
  const xRad=(xMax-xMin)/2||1,yRad=(yMax-yMin)/2||1;
  const eRx=+(xRad*cs+cs*0.5).toFixed(1),eRy=+(yRad*csy+csy*0.5).toFixed(1);
  const eCx=+(PAD+(xCtr-xMin)*cs+cs*0.5).toFixed(1),eCy=+(PAD+(yMax-yCtr)*csy+csy*0.5).toFixed(1);
  return{PAD,cs,csy,TH,TW,xMin,xMax,yMin,yMax,eRx,eRy,eCx,eCy};
}
function _retSvg(g,retShots){
  if(!retShots||!retShots.length)return'';
  let s='';
  retShots.forEach(sh=>{
    const si=sh[0],x0=sh[1],y0=sh[2],x1=sh[3],y1=sh[4];
    const sx=(g.PAD+(x0-g.xMin)*g.cs).toFixed(1),sy=(g.PAD+(g.yMax-y1)*g.csy).toFixed(1);
    const sw=((x1-x0+1)*g.cs).toFixed(1),sh2=((y1-y0+1)*g.csy).toFixed(1);
    const lx=(g.PAD+((x0+x1)/2-g.xMin)*g.cs).toFixed(1),ly=(g.PAD+(g.yMax-(y0+y1)/2)*g.csy).toFixed(1);
    s+=`<rect x="${sx}" y="${sy}" width="${sw}" height="${sh2}" fill="none" stroke="rgba(180,140,255,0.65)" stroke-width="1.5" stroke-dasharray="4,2"/>`;
    s+=`<text x="${lx}" y="${ly}" text-anchor="middle" dominant-baseline="middle" font-size="10" fill="rgba(210,170,255,0.9)" font-weight="bold">R${si+1}</text>`;
  });
  return s;
}
function _buildWaferSvg(dies,g,retShots,clipId,showZoneRings){
  const cd=`<defs><clipPath id="${clipId}"><ellipse cx="${g.eCx}" cy="${g.eCy}" rx="${g.eRx}" ry="${g.eRy}"/></clipPath></defs>`;
  const cb=`<ellipse cx="${g.eCx}" cy="${g.eCy}" rx="${g.eRx}" ry="${g.eRy}" fill="none" stroke="#a0bcd8" stroke-width="${g.TW>300?3:2}"/>`;
  let rects='';
  dies.forEach(d=>{
    const px=(g.PAD+(d.x-g.xMin)*g.cs).toFixed(1),py=(g.PAD+(g.yMax-d.y)*g.csy).toFixed(1);
    const dw=(g.cs*0.9).toFixed(1),dh=(g.csy*0.9).toFixed(1);
    rects+=`<rect x="${px}" y="${py}" width="${dw}" height="${dh}" fill="${_wmDieColor(d)}"/>`;
  });
  let rings='';
  if(showZoneRings){
    rings+=`<ellipse cx="${g.eCx}" cy="${g.eCy}" rx="${(g.eRx*0.40).toFixed(1)}" ry="${(g.eRy*0.40).toFixed(1)}" fill="none" stroke="rgba(52,152,219,0.6)" stroke-width="1.5" stroke-dasharray="5,3"/>`;
    rings+=`<ellipse cx="${g.eCx}" cy="${g.eCy}" rx="${(g.eRx*0.70).toFixed(1)}" ry="${(g.eRy*0.70).toFixed(1)}" fill="none" stroke="rgba(230,126,34,0.6)" stroke-width="1.5" stroke-dasharray="5,3"/>`;
  }
  return`<svg xmlns="http://www.w3.org/2000/svg" width="${g.TW}" height="${g.TH}">${cd}<g clip-path="url(#${clipId})">${rects}${_retSvg(g,retShots)}</g>${rings}${cb}</svg>`;
}
function buildZoneTab(){
  const bar=document.getElementById('tab-bar');
  const area=document.getElementById('tab-area');
  const btn=document.createElement('button');
  btn.className='tab-btn';btn.dataset.method='zone';btn.textContent='Zone Analysis';
  btn.onclick=()=>{
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active',b===btn));
    document.querySelectorAll('.tab-pane').forEach(p=>p.classList.toggle('active',p.id==='pane-zone'));
    renderZoneTab();
  };
  bar.appendChild(btn);
  const pane=document.createElement('div');
  pane.className='tab-pane';pane.id='pane-zone';
  pane.innerHTML='<div id="zone-content" style="color:#7f8c8d;padding:20px;font-style:italic">Loading...</div>';
  area.appendChild(pane);
}
let _zoneRendered=false;
function renderZoneTab(){
  if(_zoneRendered)return;
  _zoneRendered=true;
  const el=document.getElementById('zone-content');
  if(!WAFER_MAP_DATA){
    el.innerHTML='<div class="no-data">No SORT_X/SORT_Y data — wafer map unavailable.<br>Run with a sort CSV that includes die position columns.</div>';
    return;
  }
  const wmd=WAFER_MAP_DATA;
  const dies=wmd.dies||[];
  const retShots=_buildWmRetShots(wmd.reticle_map);
  const hasIb=dies.some(d=>d.ib!=null);
  const hasFb=dies.some(d=>d.fb!=null);
  const hasZ=dies.some(d=>d.z!=null);
  const defMode=wmd.mode==='IB'?'ib':'fb';
  _wmColorMode=(defMode==='ib'&&hasIb)?'ib':(hasFb?'fb':'target');
  // Color mode buttons
  const modeOpts=[['target','FailSig'],['zone','Zone'],['ib','IB'],['fb','FB']];
  let mbtns=modeOpts.map(([k,lbl])=>{
    const dis=(k==='ib'&&!hasIb)||(k==='fb'&&!hasFb)||(k==='zone'&&!hasZ);
    const act=_wmColorMode===k;
    return`<button onclick="setWmMode('${k}')" id="wmbtn-${k}" style="padding:4px 12px;margin:0 2px;border-radius:4px;cursor:${dis?'default':'pointer'};border:1px solid #2c4a6e;background:${act?'#1a5276':'#162840'};color:${act?'#ecf0f1':dis?'#445566':'#7fb3d3'};font-size:11px" ${dis?'disabled':''}>${lbl}</button>`;
  }).join('');
  // Info banner
  const ibFbNote=(!hasIb&&!hasFb)?` <span style="color:#7f8c8d;font-style:italic">IB/FB coloring unavailable — analysis is scoped to a single bin so all dies share the same IB/FB value.</span>`:'';
  // Group dies by wafer
  const waferMap={};
  dies.forEach(d=>{const wk=d.w!=null?String(d.w):'all';if(!waferMap[wk])waferMap[wk]=[];waferMap[wk].push(d);});
  const waferKeys=Object.keys(waferMap).sort();
  // Composite SVG — deduplicate by (x,y), fail wins
  const _compMap={};
  dies.forEach(d=>{const k=d.x+','+d.y;if(!_compMap[k]||d.t)_compMap[k]=d;});
  const compDies=Object.values(_compMap);
  const g=_wmGeom(540,wmd.xMin,wmd.xMax,wmd.yMin,wmd.yMax);
  let html=`<div style="margin-bottom:6px;font-size:11px;color:#7f8c8d;line-height:1.6;padding:6px 8px;background:#0d1828;border:1px solid #1e3a5f;border-radius:4px">`;
  html+=`<b style="color:#7fb3d3">Wafer Map &amp; Zone Analysis</b> — `;
  html+=`<b>FailSig</b> mode (red) highlights dies that matched the target bin. `;
  html+=`<b>Zone</b> mode colors dies by radial zone (<span style="color:#00c8ff">■ cyan=Center</span>, <span style="color:#f9ca24">■ yellow=Mid</span>, <span style="color:#ff3f3f">■ red=Edge</span>). `;
  html+=`The zone × quartile tables below show fail rates broken down by wafer zone and parameter quartile — useful for identifying whether failures are spatially or parametrically driven.`;
  html+=ibFbNote;
  html+=`</div>`;
  html+=`<div style="margin-bottom:8px;margin-top:6px">${mbtns} <button onclick="toggleFailHeatmap()" id="wmbtn-heatmap" style="padding:4px 12px;margin-left:8px;border-radius:4px;cursor:pointer;border:1px solid #6c3483;background:#4a235a;color:#ecf0f1;font-size:11px">&#128202; Heatmap</button> <button onclick="toggleWaferMap()" id="wmbtn-wm" style="padding:4px 12px;margin:0 2px;border-radius:4px;cursor:pointer;border:1px solid #2c4a6e;background:#162840;color:#7fb3d3;font-size:11px">&#9632; Wafer Map</button></div>`;
  html+=`<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start">`;
  html+=`<div><div style="font-size:11px;color:#7fb3d3;margin-bottom:4px">Composite (${dies.length.toLocaleString()} dies, ${waferKeys.length} wafer${waferKeys.length!==1?'s':''})</div>`;
  html+=`<div id="wm-heatmap-wrap" style="display:block"></div>`;
  html+=`<div id="wm-composite" style="display:none">${_buildWaferSvg(compDies,g,retShots,'wm_cp',true)}</div></div>`; 
  // Zone ring legend
  html+=`<div style="font-size:10px;color:#7f8c8d;align-self:center;line-height:2"><span style="color:#3498db">&#9900;</span> Center (r≤0.40)<br><span style="color:#f39c12">&#9900;</span> Mid (0.40–0.70)<br><span style="color:#e74c3c">&#9900;</span> Edge (r>0.70)</div>`;
  html+=`</div>`;
  // Per-wafer tiles
  if(waferKeys.length>1){
    html+=`<div style="margin-top:12px"><div style="font-size:11px;color:#7fb3d3;margin-bottom:4px">Per-Wafer Tiles (${waferKeys.length})</div><div id="wm-tiles" style="display:flex;flex-wrap:wrap;gap:6px;max-height:480px;overflow-y:auto;padding:4px;background:#0d1b26;border:1px solid #1e3a5f;border-radius:4px">`;
    const gS=_wmGeom(200,wmd.xMin,wmd.xMax,wmd.yMin,wmd.yMax);
    waferKeys.forEach((wk,ti)=>{
      const wDies=waferMap[wk];
      html+=`<div style="text-align:center"><div style="font-size:9px;color:#8ab4d4;margin-bottom:2px">${escQ(wk)}</div>${_buildWaferSvg(wDies,gS,[],'wm_t'+ti,false)}<div style="font-size:9px;color:#ff8080;margin-top:1px">${wDies.filter(d=>d.t).length} hits</div></div>`;
    });
    html+=`</div></div>`;
  }
  // Zone fail rate bar
  if(ZONE_DATA&&ZONE_DATA.zone_fail_rate&&ZONE_DATA.zone_fail_rate.length){
    html+=`<div style="margin-top:14px"><h3>Fail Rate by Zone</h3><div id="zone-bar" style="height:200px"></div></div>`;
  }
  // Zone × Param quartile heat tables — grouped by parameter prefix as sub-tabs
  if(ZONE_DATA&&ZONE_DATA.interactions&&ZONE_DATA.interactions.length){
    // Group interactions by prefix (first token before '_' or alpha prefix)
    function _paramGroup(name){
      const m=name.match(/^([A-Za-z]+)/);
      return m?m[1].toUpperCase():'OTHER';
    }
    const groupMap={};
    ZONE_DATA.interactions.forEach(itm=>{
      const g=_paramGroup(itm.param);
      if(!groupMap[g])groupMap[g]=[];
      groupMap[g].push(itm);
    });
    const groupKeys=Object.keys(groupMap).sort();
    const zoneSubId='zone-sub-tabs';
    html+=`<div style="margin-top:14px"><h3>Zone × Quartile Fail Rate</h3>`;
    html+=`<div style="font-size:11px;color:#7f8c8d;margin-bottom:8px">Each cell = % fails for dies in that zone <b>and</b> parameter quartile. Orange intensity = fail rate.</div>`;
    // Sub-tab bar — store keys in data attribute so zoneSubTab can read them after innerHTML set
    html+=`<div id="${zoneSubId}-bar" data-keys="${groupKeys.join('|')}" style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px">`;
    groupKeys.forEach((gk,gi)=>{
      html+=`<button onclick="zoneSubTab('${zoneSubId}','${gk}')" id="${zoneSubId}-btn-${gk}" style="padding:3px 10px;border-radius:4px;cursor:pointer;border:1px solid #2c4a6e;background:${gi===0?'#1a5276':'#162840'};color:${gi===0?'#ecf0f1':'#7fb3d3'};font-size:11px">${gk} (${groupMap[gk].length})</button>`;
    });
    html+=`</div>`;
    // Sub-tab panes
    groupKeys.forEach((gk,gi)=>{
      html+=`<div id="${zoneSubId}-pane-${gk}" style="display:${gi===0?'block':'none'}">`;
      groupMap[gk].forEach(itm=>{
        const maxFr=Math.max(...itm.cells.map(c=>c.fail_rate),0.001);
        const cellMap={};itm.cells.forEach(c=>{cellMap[c.zone+'|'+c.upm_q]=c;});
        const zones=['Center','Mid','Edge'];const qs=['Q1 (low)','Q2','Q3','Q4 (high)'];
        let tbl=`<div style="margin-bottom:12px"><div style="font-size:11px;font-weight:bold;color:#7fb3d3;margin-bottom:4px">${escQ(itm.param)}</div><table style="border-collapse:collapse;font-size:11px"><thead><tr><th style="padding:3px 8px;color:#5d7a99;background:#0f2030;text-align:left">Zone \\ Q</th>`;
        qs.forEach(q=>{tbl+=`<th style="padding:3px 8px;color:#5d7a99;background:#0f2030;white-space:nowrap">${escQ(q)}</th>`;});
        tbl+='</tr></thead><tbody>';
        zones.forEach(z=>{
          tbl+=`<tr><td style="padding:3px 8px;color:#7fb3d3;font-weight:bold;background:#0f1d30;white-space:nowrap">${z}</td>`;
          qs.forEach(q=>{
            const c=cellMap[z+'|'+q];
            if(!c){tbl+=`<td style="padding:3px 8px;color:#445566;text-align:center">—</td>`;return;}
            const op=(c.fail_rate/maxFr*0.85+0.05).toFixed(2);
            tbl+=`<td style="padding:3px 8px;background:rgba(231,76,60,${op});text-align:center;color:#fff" title="${z} | ${q}: ${(c.fail_rate*100).toFixed(1)}% (n=${c.n})">${(c.fail_rate*100).toFixed(1)}%</td>`;
          });
          tbl+='</tr>';
        });
        tbl+='</tbody></table></div>';
        html+=tbl;
      });
      html+=`</div>`;
    });
    html+=`</div>`;
  }
  el.innerHTML=(window._ZONE_SUMMARY_HTML||'')+html;
  // Auto-render heatmap (default view)
  _renderSvgHeatmap();
  // Plotly zone bar
  if(ZONE_DATA&&ZONE_DATA.zone_fail_rate&&ZONE_DATA.zone_fail_rate.length){
    const zfr=ZONE_DATA.zone_fail_rate;
    const zCols={'Center':'#00c8ff','Mid':'#f9ca24','Edge':'#ff3f3f'};
    Plotly.newPlot('zone-bar',[{type:'bar',x:zfr.map(z=>z.zone),y:zfr.map(z=>+(z.fail_rate*100).toFixed(2)),marker:{color:zfr.map(z=>zCols[z.zone]||'#7f8c8d')},text:zfr.map(z=>`n=${z.n.toLocaleString()}`),textposition:'outside',hovertemplate:'%{x}<br>Fail rate: %{y:.2f}%<br>n=%{text}<extra></extra>'}],{paper_bgcolor:'#162840',plot_bgcolor:'#0d1b26',font:{color:'#95a5a6',size:11},margin:{l:50,r:20,t:20,b:40},yaxis:{title:'Fail Rate %',color:'#5d6d7e',gridcolor:'#1e3a5f'},xaxis:{color:'#5d6d7e'}},{responsive:true,displayModeBar:false});
  }
}
function buildRcaTab(id,label,renderFn){
  const bar=document.getElementById('tab-bar');
  const area=document.getElementById('tab-area');
  const btn=document.createElement('button');
  btn.className='tab-btn';btn.dataset.method=id;btn.textContent=label;
  btn.onclick=()=>{
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active',b===btn));
    document.querySelectorAll('.tab-pane').forEach(p=>p.classList.toggle('active',p.id==='pane-'+id));
    renderFn();
  };
  bar.appendChild(btn);
  const pane=document.createElement('div');
  pane.className='tab-pane';pane.id='pane-'+id;
  pane.innerHTML=`<div id="${id}-content" style="color:#7f8c8d;padding:20px;font-style:italic">Click tab to load…</div>`;
  area.appendChild(pane);
}
// ── Repeatability ─────────────────────────────────────────────────────────────
let _repRendered=false;
function renderRepeatabilityTab(){
  if(_repRendered)return;_repRendered=true;
  const el=document.getElementById('repeatability-content');
  if(!WAFER_MAP_DATA){el.innerHTML='<div class="no-data">No wafer map data.</div>';return;}
  const dies=WAFER_MAP_DATA.dies||[];
  // Count how many wafers each (x,y) location has at least one fail on
  const locWafers={};  // key -> Set of wafer keys with a fail
  const locTotal={};   // key -> Set of wafer keys seen
  dies.forEach(d=>{
    const k=d.x+','+d.y;
    const wk=d.w!=null?String(d.w):'all';
    if(!locTotal[k])locTotal[k]=new Set();
    locTotal[k].add(wk);
    if(d.t){if(!locWafers[k])locWafers[k]=new Set();locWafers[k].add(wk);}
  });
  const totalWafers=new Set(dies.map(d=>d.w!=null?String(d.w):'all')).size;
  // Build sorted list of hot locations
  const hot=Object.keys(locWafers).map(k=>{
    const[x,y]=k.split(',').map(Number);
    return{x,y,failWafers:locWafers[k].size,totalWafers:locTotal[k]?locTotal[k].size:totalWafers};
  }).sort((a,b)=>b.failWafers-a.failWafers);
  const pctThresh=0.3;
  const systematic=hot.filter(h=>h.failWafers/h.totalWafers>=pctThresh);
  let html=`<div style="margin-bottom:10px;padding:8px 12px;background:#0d1828;border:1px solid #1e3a5f;border-radius:4px;font-size:11px;color:#7f8c8d">`;
  html+=`<b style="color:#7fb3d3">Wafer-to-Wafer Repeatability</b> — Die locations that fail on ≥${Math.round(pctThresh*100)}% of wafers are flagged as <b style="color:#e74c3c">systematic</b>. `;
  html+=`Random fails appear on only 1–2 wafers. Total wafers: <b style="color:#ecf0f1">${totalWafers}</b> &nbsp;|&nbsp; Systematic locations: <b style="color:#e74c3c">${systematic.length}</b></div>`;
  if(!hot.length){el.innerHTML=html+'<div class="no-data">No failures found.</div>';return;}
  html+=`<div style="overflow-x:auto"><table style="border-collapse:collapse;font-size:11px;width:100%"><thead><tr>`;
  ['#','X','Y','Fail Wafers','Total Wafers','Rate','Flag'].forEach(h=>{html+=`<th style="padding:4px 8px;color:#5d7a99;background:#0f2030;white-space:nowrap">${h}</th>`;});
  html+=`</tr></thead><tbody>`;
  hot.slice(0,60).forEach((h,i)=>{
    const rate=h.failWafers/h.totalWafers;
    const flag=rate>=pctThresh?`<span style="color:#e74c3c;font-weight:bold">Systematic</span>`:`<span style="color:#7f8c8d">Random</span>`;
    const bg=rate>=pctThresh?'rgba(231,76,60,0.08)':'';
    html+=`<tr style="border-bottom:1px solid #1a2f45;background:${bg}"><td style="padding:3px 8px;color:#5d7a99">${i+1}</td><td style="padding:3px 8px">${h.x}</td><td style="padding:3px 8px">${h.y}</td><td style="padding:3px 8px;color:#e74c3c;font-weight:bold">${h.failWafers}</td><td style="padding:3px 8px;color:#7f8c8d">${h.totalWafers}</td><td style="padding:3px 8px;color:#f39c12">${(rate*100).toFixed(1)}%</td><td style="padding:3px 8px">${flag}</td></tr>`;
  });
  html+=`</tbody></table></div>`;
  el.innerHTML=html;
}
// ── Co-Failure ─────────────────────────────────────────────────────────────────
let _coRendered=false;
function renderCoFailureTab(){
  if(_coRendered)return;_coRendered=true;
  const el=document.getElementById('cofailure-content');
  if(!WAFER_MAP_DATA){el.innerHTML='<div class="no-data">No wafer map data.</div>';return;}
  const dies=WAFER_MAP_DATA.dies||[];
  const hasIb=dies.some(d=>d.ib!=null);
  const hasFb=dies.some(d=>d.fb!=null);
  if(!hasIb&&!hasFb){
    el.innerHTML='<div class="no-data" style="padding:20px">Co-failure analysis requires IB/FB data.<br>Only available when running across multiple bins (not a single-bin filter).</div>';
    return;
  }
  // Count co-occurrence of IB or FB values among failing dies
  const failDies=dies.filter(d=>d.t);
  const col=hasIb?'ib':'fb';
  const label=hasIb?'IB':'FB';
  const counts={};
  failDies.forEach(d=>{const v=d[col];if(v!=null){counts[v]=(counts[v]||0)+1;}});
  const total=failDies.length;
  const sorted=Object.entries(counts).sort((a,b)=>b[1]-a[1]);
  let html=`<div style="margin-bottom:10px;padding:8px 12px;background:#0d1828;border:1px solid #1e3a5f;border-radius:4px;font-size:11px;color:#7f8c8d">`;
  html+=`<b style="color:#7fb3d3">Co-Failure ${label} Distribution</b> — Among <b style="color:#e74c3c">${total.toLocaleString()}</b> failing dies, breakdown by ${label} value. `;
  html+=`High overlap with another bin suggests shared failure mechanism.</div>`;
  html+=`<table style="border-collapse:collapse;font-size:11px"><thead><tr>`;
  ['#',label,'Fail Dies','% of Fails'].forEach(h=>{html+=`<th style="padding:4px 8px;color:#5d7a99;background:#0f2030">${h}</th>`;});
  html+=`</tr></thead><tbody>`;
  sorted.forEach(([v,n],i)=>{
    const pct=(n/total*100).toFixed(1);
    html+=`<tr style="border-bottom:1px solid #1a2f45"><td style="padding:3px 8px;color:#5d7a99">${i+1}</td><td style="padding:3px 8px;color:#7fb3d3;font-weight:bold">${v}</td><td style="padding:3px 8px;color:#e74c3c">${n.toLocaleString()}</td><td style="padding:3px 8px;color:#f39c12">${pct}%</td></tr>`;
  });
  html+=`</tbody></table>`;
  el.innerHTML=html;
}
// ── Reticle Fail Rate ──────────────────────────────────────────────────────────
let _retRendered=false;
function renderReticleTab(){
  if(_retRendered)return;_retRendered=true;
  const el=document.getElementById('reticle-content');
  if(!WAFER_MAP_DATA){el.innerHTML='<div class="no-data">No wafer map data.</div>';return;}
  const wmd=WAFER_MAP_DATA;
  // Try shot-based approach first
  const retShots=_buildWmRetShots(wmd.reticle_map);
  const dies=wmd.dies||[];
  if(!retShots||!retShots.length){
    // Fallback: check if dies have a reticle_loc field in raw reticle_map
    const rawMap=wmd.reticle_map||{};
    const hasRaw=Object.keys(rawMap).filter(k=>k!=='_meta').length>0;
    if(!hasRaw){
      // No reticle data at all — explain why
      const metaInfo=wmd.reticle_map&&wmd.reticle_map._meta?wmd.reticle_map._meta:{};
      let msg=`<div style="padding:16px;background:#0d1828;border:1px solid #2c4a6e;border-radius:6px;font-size:12px;color:#7f8c8d;line-height:1.8">`;
      msg+=`<b style="color:#e74c3c">No reticle map data available.</b><br><br>`;
      if(metaInfo.error)msg+=`<b style="color:#f39c12">Reason:</b> ${escQ(metaInfo.error)}<br><br>`;
      if(metaInfo.prefix)msg+=`<b style="color:#7fb3d3">Detected prefix:</b> <code style="color:#f39c12">${escQ(metaInfo.prefix)}</code><br>`;
      if(metaInfo.available&&metaInfo.available.length)msg+=`<b style="color:#7fb3d3">Available reticle CSVs:</b> ${metaInfo.available.map(f=>`<code style="color:#f39c12">${escQ(f)}</code>`).join(', ')}<br><br>`;
      msg+=`<b style="color:#7fb3d3">Fix:</b> Rename or add a CSV to <code style="color:#f39c12">${META.reticle_dir||'shared/reticle/'}</code> `;
      msg+=`whose filename contains the detected prefix, with columns <code>DieX, DieY, LayoutX, LayoutY, Reticle</code>.</div>`;
      el.innerHTML=msg;
      return;
    }
    // Has raw map — use reticle_loc value per die from the map
    const locToShot={};
    Object.entries(rawMap).forEach(([k,v])=>{locToShot[k]=v[2];});
    const shotStats={};
    dies.forEach(d=>{
      const k=d.x+','+d.y;
      const si=locToShot[k];
      if(si==null)return;
      if(!shotStats[si])shotStats[si]={total:0,fails:0};
      shotStats[si].total++;
      if(d.t)shotStats[si].fails++;
    });
    _renderReticleTable(el,shotStats);
    return;
  }
  // For each die, find which reticle shot it belongs to
  function shotForDie(d){
    for(let i=0;i<retShots.length;i++){
      const[si,x0,y0,x1,y1]=retShots[i];
      if(d.x>=x0&&d.x<=x1&&d.y>=y0&&d.y<=y1)return si;
    }
    return null;
  }
  // Shot-based: find which shot bounding box each die falls in
  const shotStats={};
  dies.forEach(d=>{
    const si=shotForDie(d);
    if(si==null)return;
    if(!shotStats[si])shotStats[si]={total:0,fails:0};
    shotStats[si].total++;
    if(d.t)shotStats[si].fails++;
  });
  _renderReticleTable(el,shotStats);
}
function _renderReticleTable(el,shotStats){
  const sorted=Object.entries(shotStats).sort((a,b)=>b[1].fails/b[1].total - a[1].fails/a[1].total);
  let html=`<div style="margin-bottom:10px;padding:8px 12px;background:#0d1828;border:1px solid #1e3a5f;border-radius:4px;font-size:11px;color:#7f8c8d">`;
  html+=`<b style="color:#7fb3d3">Reticle Field Fail Rate</b> — Fail rate per reticle shot. If one shot consistently has higher fails it may indicate a reticle defect or litho focus issue at that field position.</div>`;
  html+=`<table style="border-collapse:collapse;font-size:11px"><thead><tr>`;
  ['Shot','Total Dies','Fail Dies','Fail Rate'].forEach(h=>{html+=`<th style="padding:4px 8px;color:#5d7a99;background:#0f2030">${h}</th>`;});
  html+=`</tr></thead><tbody>`;
  const maxFr=Math.max(...sorted.map(([,s])=>s.total>0?s.fails/s.total:0),0.001);
  sorted.forEach(([si,s])=>{
    const fr=s.total>0?s.fails/s.total:0;
    const op=(fr/maxFr*0.8+0.05).toFixed(2);
    html+=`<tr style="border-bottom:1px solid #1a2f45"><td style="padding:3px 8px;color:#7fb3d3;font-weight:bold">R${+si+1}</td><td style="padding:3px 8px;color:#7f8c8d">${s.total.toLocaleString()}</td><td style="padding:3px 8px;color:#e74c3c">${s.fails.toLocaleString()}</td><td style="padding:3px 8px;background:rgba(231,76,60,${op});color:#fff">${(fr*100).toFixed(2)}%</td></tr>`;
  });
  html+=`</tbody></table>`;
  el.innerHTML=html;
}
// ── Lot Trend ─────────────────────────────────────────────────────────────────
function _trendFactory(cfg){
  // cfg: {records, tgtKeyHint, idField, prefix, granLabel, elId, scrollable, title}
  const el=document.getElementById(cfg.elId);
  if(!el)return;
  const RECS=cfg.records;
  if(!RECS||!RECS.length){el.innerHTML='<div class="no-data">No '+cfg.granLabel+'-level records available.</div>';return;}
  const tgtKey=Object.keys(RECS[0]).find(k=>k==='_TARGET'||k.toLowerCase().includes('target'));
  const lotKey=META.lot_col||Object.keys(RECS[0]).find(k=>k.toLowerCase().includes('lot'));
  // identity: wafer tab uses _LABEL (lot/wafer composite); lot tab uses lotKey
  const idKey=(cfg.idField&&RECS[0][cfg.idField]!=null)?cfg.idField:lotKey;
  if(!tgtKey||!idKey){el.innerHTML='<div class="no-data">Missing id or target columns.</div>';return;}
  const P=cfg.prefix;
  const allPts=RECS.map(r=>({id:String(r[idKey]),fr:+r[tgtKey],rec:r})).filter(r=>!isNaN(r.fr));
  const _frSorted=allPts.map(p=>p.fr).sort((a,b)=>a-b);
  const medFr=_frSorted[Math.floor(_frSorted.length/2)]||0;
  let _thr=0.005;   // default high-fail threshold = 0.5% (user-editable)
  let _sort={col:'fr',asc:false};
  let _pickedParam=null;

  function paramKeysAll(){
    const skip=new Set([lotKey,tgtKey,idKey,'_LABEL','_TARGET']);
    return Object.keys(RECS[0]).filter(k=>{
      if(skip.has(k))return false;
      return RECS.some(r=>r[k]!=null&&r[k]!==''&&!isNaN(+r[k]));
    });
  }

  function renderTrendTable(){
    const wrap=document.getElementById(P+'-tbl-wrap');if(!wrap)return;
    const pts=[...allPts].sort((a,b)=>{const s=_sort.col==='id'?a.id.localeCompare(b.id):(a.fr-b.fr);return _sort.asc?s:-s;});
    let tbl=`<table style="border-collapse:collapse;font-size:11px;width:100%"><thead><tr>`;
    tbl+=`<th onclick="${P}SortBy('id')" style="padding:4px 8px;color:#5d7a99;background:#0f2030;cursor:pointer;text-align:left">${cfg.granLabel==='wafer'?'Lot/Wafer':'Lot'} ${_sort.col==='id'?(_sort.asc?'▲':'▼'):''}</th>`;
    tbl+=`<th onclick="${P}SortBy('fr')" style="padding:4px 8px;color:#5d7a99;background:#0f2030;cursor:pointer;text-align:right">Fail % ${_sort.col==='fr'?(_sort.asc?'▲':'▼'):''}</th></tr></thead><tbody>`;
    pts.forEach(p=>{const hi=p.fr>=_thr;
      tbl+=`<tr style="border-bottom:1px solid #1a2f45${hi?';background:#2a1414':''}"><td style="padding:3px 8px;color:${hi?'#e74c3c':'#7fb3d3'}">${escQ(p.id)}</td><td style="padding:3px 8px;text-align:right;color:${hi?'#e74c3c':'#95a5a6'};font-weight:${hi?'bold':'normal'}">${(p.fr*100).toFixed(3)}%</td></tr>`;});
    tbl+=`</tbody></table>`;
    wrap.innerHTML=tbl;
  }

  function renderHiLo(){
    const wrap=document.getElementById(P+'-hilo-wrap');if(!wrap)return;
    const hiIds=new Set(allPts.filter(p=>p.fr>=_thr).map(p=>p.id));
    const loIds=new Set(allPts.filter(p=>p.fr<_thr).map(p=>p.id));
    if(!hiIds.size||!loIds.size){const side=!hiIds.size?'high':'low';wrap.innerHTML=`<div class="no-data">No ${side}-fail ${cfg.granLabel}s at current threshold (${(_thr*100).toFixed(2)}%). Adjust and Apply.</div>`;return;}
    const paramKeys=paramKeysAll();
    if(!paramKeys.length){wrap.innerHTML='<div class="no-data">No numeric parameters in records.</div>';return;}
    const hiRecs=RECS.filter(r=>hiIds.has(String(r[idKey])));
    const loRecs=RECS.filter(r=>loIds.has(String(r[idKey])));
    const mean=a=>a.reduce((s,v)=>s+v,0)/a.length;
    const sd=(a,m)=>Math.sqrt(a.reduce((s,v)=>s+(v-m)**2,0)/Math.max(a.length-1,1));
    function _erfc(x){const t=1/(1+0.3275911*Math.abs(x));const y=t*(0.254829592+t*(-0.284496736+t*(1.421413741+t*(-1.453152027+t*1.061405429))))*Math.exp(-x*x);return x>=0?y:2-y;}
    function _pFromT(tv,dof){let p=0;if(dof>30){const z=tv*(1-1/(4*dof));p=0.5*_erfc(z/Math.sqrt(2));}else{p=Math.pow(1+tv*tv/dof,-(dof+1)/2);}return Math.min(1,Math.max(0,2*p));}
    const rows=[];let _nConst=0,_nSmall=0;
    paramKeys.forEach(k=>{
      const hi=hiRecs.map(r=>+r[k]).filter(v=>!isNaN(v));
      const lo=loRecs.map(r=>+r[k]).filter(v=>!isNaN(v));
      if(hi.length<2||lo.length<2)return;
      const mh=mean(hi),ml=mean(lo);const sh=sd(hi,mh),sl=sd(lo,ml);
      const se=Math.sqrt(sh*sh/hi.length+sl*sl/lo.length);
      const t=se>0?(mh-ml)/se:0;
      const _scale=Math.max(Math.abs(mh),Math.abs(ml),1e-30);
      const _pooledSD=Math.sqrt((sh*sh*(hi.length-1)+sl*sl*(lo.length-1))/Math.max(hi.length+lo.length-2,1));
      const _relDelta=Math.abs(mh-ml)/_scale;
      const cohenD=_pooledSD>0?Math.abs(mh-ml)/_pooledSD:0;
      if((_pooledSD/_scale)<1e-6){_nConst++;return;}
      if(_relDelta<1e-6){_nConst++;return;}
      if(cohenD<0.2){_nSmall++;return;}
      const df=Math.max(1,(sh*sh/hi.length+sl*sl/lo.length)**2/((sh*sh/hi.length)**2/(hi.length-1)+(sl*sl/lo.length)**2/(lo.length-1)));
      rows.push({k,mh,ml,diff:mh-ml,t:Math.abs(t),tRaw:t,pval:_pFromT(Math.abs(t),df),cohenD});
    });
    rows.sort((a,b)=>b.cohenD-a.cohenD);
    const top=rows.slice(0,30);
    const df0=Math.round(hiRecs.length+loRecs.length-2);
    const critT2=df0>60?2.0:df0>30?2.04:df0>20?2.09:2.2;
    const nSig=top.filter(r=>r.t>=critT2).length;
    let html=`<div style="margin-bottom:8px;padding:8px 10px;background:#0a1520;border:1px solid #1e3a5f;border-radius:4px;font-size:11px;color:#7f8c8d;line-height:1.7">`;
    html+=`Comparing <b style="color:#e74c3c">${hiIds.size} high-fail ${cfg.granLabel}s</b> (≥${(_thr*100).toFixed(2)}%) vs <b style="color:#2ecc71">${loIds.size} normal</b> (~${df0} df). Ranked by <b style="color:#7fb3d3">Cohen d</b> (practical significance).<br>`;
    html+=`<b style="color:#2ecc71">${nSig} of ${top.length}</b> shown are statistically significant (|t|≥${critT2.toFixed(1)}). ${nSig===0?'<b style="color:#e74c3c">No significant parametric separation.</b>':''}`;
    if(_nConst||_nSmall){html+=`<br><span style="color:#5d7a99">Excluded <b style="color:#f39c12">${_nConst}</b> near-constant/degenerate param(s) and <b style="color:#f39c12">${_nSmall}</b> trivial-effect (Cohen d &lt; 0.2) to avoid false positives.</span>`;}
    html+=`<br><span style="color:#5dade2">Click any row to plot ${cfg.granLabel} XY (param vs % fail).</span></div>`;
    // top ~10 rows visible; remainder scrolls so the XY chart below stays in view
    html+=`<div style="max-height:330px;overflow-y:auto;border:1px solid #1e3a5f;border-radius:4px">`;
    html+=`<table style="border-collapse:collapse;font-size:11px;width:100%"><thead><tr>`;
    ['#','Parameter','High Mean','Normal Mean','Δ','Cohen d','|t|','p','Sig?','Direction'].forEach(h=>{html+=`<th style="padding:4px 8px;color:#5d7a99;background:#0f2030;white-space:nowrap">${h}</th>`;});
    html+=`</tr></thead><tbody>`;
    top.forEach((r,i)=>{
      const tColor=r.t>=critT2?'#e74c3c':r.t>=1.5?'#f39c12':'#5d7a99';
      const sig=r.t>=critT2?'<span style="color:#e74c3c;font-weight:bold">✓</span>':'<span style="color:#5d7a99">–</span>';
      const pStr=r.pval<0.001?'<0.001':r.pval<0.01?r.pval.toFixed(3):r.pval.toFixed(2);
      const dCol=r.cohenD>=0.8?'#e74c3c':r.cohenD>=0.5?'#f39c12':'#7fb3d3';
      const dir=r.tRaw>0?'<span style="color:#e74c3c">↑ Higher in high-fail</span>':'<span style="color:#2ecc71">↓ Lower in high-fail</span>';
      html+=`<tr onclick="${P}PickParam('${escQ(r.k)}')" style="border-bottom:1px solid #1a2f45;cursor:pointer" title="Click to plot XY">`;
      html+=`<td style="padding:3px 8px;color:#5d7a99">${i+1}</td>`;
      html+=`<td style="padding:3px 8px;color:#7fb3d3;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escQ(r.k)}">${r.k.length>40?r.k.slice(0,39)+'…':r.k}</td>`;
      html+=`<td style="padding:3px 8px;color:#e74c3c;font-weight:bold">${r.mh.toPrecision(4)}</td>`;
      html+=`<td style="padding:3px 8px;color:#2ecc71">${r.ml.toPrecision(4)}</td>`;
      html+=`<td style="padding:3px 8px;color:${r.diff>0?'#e74c3c':'#2ecc71'}">${r.diff>0?'+':''}${r.diff.toPrecision(3)}</td>`;
      html+=`<td style="padding:3px 8px;color:${dCol};font-weight:${r.cohenD>=0.5?'bold':'normal'}">${r.cohenD.toFixed(2)}</td>`;
      html+=`<td style="padding:3px 8px;color:${tColor}">${r.t.toFixed(2)}</td>`;
      html+=`<td style="padding:3px 8px;color:${r.pval<0.05?'#e74c3c':'#5d7a99'}">${pStr}</td>`;
      html+=`<td style="padding:3px 8px;text-align:center">${sig}</td>`;
      html+=`<td style="padding:3px 8px">${dir}</td></tr>`;
    });
    html+=`</tbody></table></div>`;   // close scroll wrapper
    html+=`<div id="${P}-xy-title" style="margin-top:12px;color:#7fb3d3;font-size:12px"></div>`;
    html+=`<div id="${P}-xy-plot" style="height:360px"></div>`;
    wrap.innerHTML=html;
    if(_pickedParam)renderXY(_pickedParam);
  }

  function renderXY(param){
    _pickedParam=param;
    const host=document.getElementById(P+'-xy-plot');const ttl=document.getElementById(P+'-xy-title');
    if(!host)return;
    const xs=[],ys=[],txt=[],col=[];
    allPts.forEach(p=>{const x=+p.rec[param],y=p.fr*100;if(isNaN(x))return;xs.push(x);ys.push(+y.toFixed(3));txt.push(p.id);col.push(p.fr>=_thr?'#e74c3c':'#3498db');});
    if(!xs.length){host.innerHTML='<div class="no-data">No numeric values for this parameter.</div>';if(ttl)ttl.textContent='';return;}
    const xLabel=param.length>38?param.slice(0,37)+'…':param;
    const traces=[{type:'scatter',mode:'markers',x:xs,y:ys,text:txt,marker:{color:col,size:9,line:{color:'#0d1b26',width:1}},hovertemplate:'<b>%{text}</b><br>'+xLabel+': %{x:.5f}<br>Fail: %{y:.3f}%<extra></extra>',showlegend:false}];
    if(xs.length>=4){const nB=Math.max(3,Math.min(8,Math.floor(xs.length/2)));const xMin=Math.min(...xs),xMax=Math.max(...xs),bw=(xMax-xMin)/nB||1;const bins=Array.from({length:nB},(_,i)=>({cx:xMin+(i+0.5)*bw,ys:[]}));xs.forEach((x,i)=>{const bi=Math.min(nB-1,Math.floor((x-xMin)/bw));bins[bi].ys.push(ys[i]);});const bx=[],by=[];bins.forEach(b=>{if(b.ys.length){bx.push(b.cx);by.push(b.ys.reduce((a,v)=>a+v,0)/b.ys.length);}});traces.push({type:'scatter',mode:'lines+markers',x:bx,y:by,name:'bin mean',line:{color:'#f39c12',width:2,dash:'dot'},marker:{color:'#f39c12',size:6},hovertemplate:'bin: %{x:.4f}<br>mean: %{y:.3f}%<extra>bin mean</extra>',showlegend:false});}
    Plotly.newPlot(host,traces,{paper_bgcolor:'#162840',plot_bgcolor:'#0d1b26',font:{color:'#95a5a6',size:11},margin:{l:60,r:20,t:10,b:55},xaxis:{title:xLabel,color:'#5d6d7e',gridcolor:'#1e3a5f'},yaxis:{title:'% Fail per '+cfg.granLabel,color:'#5d6d7e',gridcolor:'#1e3a5f'},hovermode:'closest'},{responsive:true,displayModeBar:true,modeBarButtonsToRemove:['lasso2d','select2d']});
    if(ttl)ttl.innerHTML=`XY: <b>${escQ(param)}</b> vs % fail — <span style="color:#e74c3c">red = high-fail ${cfg.granLabel}</span>, <span style="color:#3498db">blue = normal</span>. Outliers (dot away from trend) are your suspects.`;
  }

  function drawTrend(){
    const ptsChron=[...allPts].sort((a,b)=>a.id.localeCompare(b.id));
    const hiColor=ptsChron.map(p=>p.fr>=_thr?'#e74c3c':'#3498db');
    const plotId=P+'-trend-plot';
    Plotly.newPlot(plotId,[
      {type:'scatter',mode:'lines+markers',x:ptsChron.map(p=>p.id),y:ptsChron.map(p=>+(p.fr*100).toFixed(3)),marker:{color:hiColor,size:7},line:{color:'#2c4a6e',width:1.2},hovertemplate:'%{x}<br>Fail: %{y:.3f}%<extra></extra>',showlegend:false},
      {type:'scatter',mode:'lines',x:ptsChron.map(p=>p.id),y:ptsChron.map(()=>+(_thr*100).toFixed(3)),line:{color:'#e74c3c',width:1,dash:'dot'},hoverinfo:'skip',showlegend:false}
    ],{paper_bgcolor:'#162840',plot_bgcolor:'#0d1b26',font:{color:'#95a5a6',size:11},margin:{l:55,r:20,t:10,b:110},yaxis:{title:'Fail Rate %',color:'#5d6d7e',gridcolor:'#1e3a5f'},xaxis:{color:'#5d6d7e',tickangle:-60,automargin:true}},{responsive:true,displayModeBar:false});
  }

  // ── Layout ──
  let html=`<div style="margin-bottom:8px;padding:8px 12px;background:#0d1828;border:1px solid #1e3a5f;border-radius:4px;font-size:11px;color:#7f8c8d">`;
  html+=`<b style="color:#7fb3d3">${cfg.title}</b> — Set threshold to flag high-fail ${cfg.granLabel}s, then open <b>High vs Low</b> and click a parameter to see its XY plot.</div>`;
  html+=`<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;font-size:11px"><span style="color:#7f8c8d">High-fail threshold:</span>`;
  html+=`<input id="${P}-thr" type="number" step="0.01" min="0" max="100" value="${+(_thr*100).toFixed(2)}" style="width:70px;background:#0f2030;color:#ecf0f1;border:1px solid #2c4a6e;border-radius:3px;padding:2px 6px;font-size:11px"> %`;
  html+=`<button onclick="${P}Apply()" style="padding:2px 10px;background:#1a5276;color:#ecf0f1;border:1px solid #2c4a6e;border-radius:3px;cursor:pointer;font-size:11px">Apply</button></div>`;
  html+=`<div style="display:flex;gap:4px;margin-bottom:6px">`;
  html+=`<button id="${P}-btn-trend" onclick="${P}Show('trend')" style="padding:3px 12px;border-radius:4px 4px 0 0;cursor:pointer;border:1px solid #2c4a6e;border-bottom:none;background:#1a5276;color:#ecf0f1;font-size:11px">Trend &amp; Table</button>`;
  html+=`<button id="${P}-btn-hilo" onclick="${P}Show('hilo')" style="padding:3px 12px;border-radius:4px 4px 0 0;cursor:pointer;border:1px solid #2c4a6e;border-bottom:none;background:#162840;color:#7fb3d3;font-size:11px">High vs Low ${cfg.granLabel==='wafer'?'Wafer':'Lot'} Analysis</button></div>`;
  // trend pane (scrollable trend for long axes)
  const scrollStyle=cfg.scrollable?`overflow-x:auto`:'';
  const minW=cfg.scrollable?`min-width:${Math.max(700,allPts.length*16)}px`:'';
  html+=`<div id="${P}-pane-trend" style="background:#162840;border:1px solid #1e3a5f;border-radius:0 4px 4px 4px;padding:10px">`;
  html+=`<div style="${scrollStyle}"><div id="${P}-trend-plot" style="height:300px;${minW}"></div></div>`;
  html+=`<div id="${P}-tbl-wrap" style="max-height:360px;overflow-y:auto;margin-top:8px"></div></div>`;
  html+=`<div id="${P}-pane-hilo" style="display:none;background:#162840;border:1px solid #1e3a5f;border-radius:0 4px 4px 4px;padding:10px"><div id="${P}-hilo-wrap"><div class="no-data">Click Apply to run analysis.</div></div></div>`;
  el.innerHTML=html;

  window[P+'SortBy']=function(c){_sort={col:c,asc:_sort.col===c?!_sort.asc:true};renderTrendTable();};
  window[P+'Apply']=function(){const v=parseFloat(document.getElementById(P+'-thr').value);if(!isNaN(v)){_thr=v/100;renderTrendTable();drawTrend();renderHiLo();}};
  window[P+'Show']=function(tab){
    document.getElementById(P+'-pane-trend').style.display=tab==='trend'?'block':'none';
    document.getElementById(P+'-pane-hilo').style.display=tab==='hilo'?'block':'none';
    document.getElementById(P+'-btn-trend').style.background=tab==='trend'?'#1a5276':'#162840';
    document.getElementById(P+'-btn-trend').style.color=tab==='trend'?'#ecf0f1':'#7fb3d3';
    document.getElementById(P+'-btn-hilo').style.background=tab==='hilo'?'#1a5276':'#162840';
    document.getElementById(P+'-btn-hilo').style.color=tab==='hilo'?'#ecf0f1':'#7fb3d3';
    if(tab==='hilo')renderHiLo();
  };
  window[P+'PickParam']=function(param){renderXY(param);};

  renderTrendTable();
  drawTrend();
}
function renderLotTrendTab(){_trendFactory({records:LOT_RECORDS,idField:null,prefix:'lt',granLabel:'lot',elId:'lottrend-content',scrollable:false,title:'Lot-to-Lot Fail Rate Trend'});}
function buildIntraFieldTab(){buildRcaTab('intrafield','Intra-Reticle',renderIntraFieldTab);}
let _ifRendered=false;
function renderIntraFieldTab(){
  if(_ifRendered)return;_ifRendered=true;
  const el=document.getElementById('intrafield-content');
  if(!el)return;
  if(!INTRAFIELD_DATA){
    el.innerHTML='<div style="padding:16px;background:#0d1828;border:1px solid #2c4a6e;border-radius:6px;font-size:12px;color:#7f8c8d;line-height:1.8">'
      +'<b style="color:#e74c3c">Intra-Reticle analysis unavailable.</b><br><br>'
      +'Requires a reticle map CSV in <code>shared/reticle/</code> with columns '
      +'<code>DieX, DieY, ReticleDieX, ReticleDieY</code>, '
      +'and the sort CSV must include <code>SORT_X</code> / <code>SORT_Y</code> columns '
      +'whose values match the map\'s <code>DieX</code> / <code>DieY</code>.</div>';
    return;
  }
  const d=INTRAFIELD_DATA;
  const overall=(d.overall*100).toFixed(3);
  let html='<div style="margin-bottom:10px;padding:8px 12px;background:#0d1828;border:1px solid #1e3a5f;border-radius:6px;font-size:12px;color:#c8d6e8;line-height:1.7">';
  html+='<b style="color:#7fb3d3">Intra-Reticle Analysis</b> &mdash; Fail rate by die position ';
  html+='<b>within the reticle field</b>, pooled across all shots. A hot cell repeated ';
  html+='across shots suggests a reticle/mask or litho-field issue, not a random or wafer-radial defect. ';
  html+=`Overall field fail rate: <b style="color:#f39c12">${overall}%</b>. Based on <b>${d.n_total.toLocaleString()}</b> dies with a resolved reticle-die position for this target (${d.n_positions||'?'} die positions). This is the analyzable set for this bin, not a coverage loss.`;
  html+='</div>';
  // --- field grid heatmap with DieLoc# labels ---
  if(d.cells&&d.cells.length){
    const allX=d.cells.map(c=>c.rdx),allY=d.cells.map(c=>c.rdy);
    const minX=Math.min(...allX),maxX=Math.max(...allX),minY=Math.min(...allY),maxY=Math.max(...allY);
    const maxFr=Math.max(...d.cells.map(c=>c.fail_rate),0.001);
    const CW=64,CH=40,PAD=4;
    const W=PAD*2+(maxX-minX+1)*CW,H=PAD*2+(maxY-minY+1)*CH;
    const cellMap={};
    d.cells.forEach(c=>{cellMap[c.rdx+','+c.rdy]=c;});
    let svg=`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" style="display:block;border:1px solid #1e3a5f;border-radius:4px;background:#0a1520;margin-bottom:12px">`;
    for(let cy=maxY;cy>=minY;cy--){
      for(let cx=minX;cx<=maxX;cx++){
        const px=PAD+(cx-minX)*CW,py=PAD+(maxY-cy)*CH;
        const cell=cellMap[cx+','+cy];
        if(!cell){svg+=`<rect x="${px}" y="${py}" width="${CW-2}" height="${CH-2}" fill="#0f1d2e" rx="2"/>`;continue;}
        const t=cell.fail_rate/maxFr;
        const col=t<0.3?`rgba(26,58,92,${(0.3+t).toFixed(2)})`:t<0.6?`rgba(243,156,18,${(0.5+t*0.5).toFixed(2)})`:t<0.85?`rgba(231,76,60,${(0.7+t*0.3).toFixed(2)})`:'#ff1744';
        // yellow border when significant (|z|>=2 AND rr>=1.2)
        const sig=cell.significant===true;
        svg+=`<rect x="${px}" y="${py}" width="${CW-2}" height="${CH-2}" fill="${col}" rx="2" stroke="${sig?'#fff176':'none'}" stroke-width="${sig?2:0}"/>`;
        svg+=`<text x="${px+(CW-2)/2}" y="${py+(CH-2)*0.28}" text-anchor="middle" dominant-baseline="middle" font-size="9" fill="#a0c8f0">DieLoc ${cell.loc!=null?cell.loc:'?'}</text>`;
        svg+=`<text x="${px+(CW-2)/2}" y="${py+(CH-2)*0.62}" text-anchor="middle" dominant-baseline="middle" font-size="9" fill="#ecf0f1">${(cell.fail_rate*100).toFixed(1)}%</text>`;
        svg+=`<text x="${px+(CW-2)/2}" y="${py+(CH-2)*0.90}" text-anchor="middle" dominant-baseline="middle" font-size="8" fill="#7f8c8d">n=${cell.n.toLocaleString()}</text>`;
      }
    }
    for(let cx=minX;cx<=maxX;cx++){const px=PAD+(cx-minX)*CW+(CW-2)/2;svg+=`<text x="${px}" y="${H-2}" text-anchor="middle" font-size="8" fill="#5d7a99">x${cx}</text>`;}
    for(let cy=maxY;cy>=minY;cy--){const py=PAD+(maxY-cy)*CH+(CH-2)/2;svg+=`<text x="2" y="${py}" dominant-baseline="middle" font-size="8" fill="#5d7a99">y${cy}</text>`;}
    svg+='</svg>';
    html+=`<div style="margin-bottom:6px;font-size:11px;color:#7f8c8d">Field grid \u2014 each cell labelled DieLoc# (row-major). <span style="outline:2px solid #fff176;padding:1px 4px">yellow border</span> = |z|&ge;2 &amp; RelRisk&ge;1.2x.</div>`;
    html+=svg;
    // hottest cells table: DieLoc, (RdX,RdY), Fail%, RelRisk, n, z, Significant?
    html+='<h3 style="color:#7fb3d3;margin:10px 0 6px">Hottest Die-in-Field Positions (top 10)</h3>';
    html+='<table style="border-collapse:collapse;font-size:11px"><thead><tr>';
    ['#','DieLoc','(RdX,RdY)','Fail%','RelRisk','n','z','Significant?'].forEach(h=>{html+=`<th style="padding:4px 8px;color:#5d7a99;background:#0f2030">${h}</th>`;});
    html+='</tr></thead><tbody>';
    d.cells.slice(0,10).forEach((c,i)=>{
      const sig=c.significant===true;
      const zStr=c.z!=null?c.z.toFixed(2):'\u2014';
      const rrStr=c.rr!=null?c.rr.toFixed(2)+'x':'\u2014';
      const bg=sig?'rgba(231,76,60,0.10)':'';
      html+=`<tr style="border-bottom:1px solid #1a2f45;background:${bg}">`;
      html+=`<td style="padding:3px 8px;color:#5d7a99">${i+1}</td>`;
      html+=`<td style="padding:3px 8px;color:#a0c8f0;font-weight:bold">DieLoc ${c.loc!=null?c.loc:'?'}</td>`;
      html+=`<td style="padding:3px 8px;color:#7f8c8d">(${c.rdx},${c.rdy})</td>`;
      html+=`<td style="padding:3px 8px;color:#e74c3c;font-weight:bold">${(c.fail_rate*100).toFixed(2)}%</td>`;
      html+=`<td style="padding:3px 8px;color:${c.rr!=null&&c.rr>=1.2?'#f39c12':'#7f8c8d'}">${rrStr}</td>`;
      html+=`<td style="padding:3px 8px;color:#7f8c8d">${c.n.toLocaleString()}</td>`;
      html+=`<td style="padding:3px 8px;color:${Math.abs(c.z||0)>=2?'#e74c3c':Math.abs(c.z||0)>=1.5?'#f39c12':'#7f8c8d'}">${zStr}</td>`;
      const tier=_rrTier(c.rr);
      html+=`<td style="padding:3px 8px">${sig?`<span style="color:${tier.color};font-weight:bold">\u2713 ${tier.label} (${c.rr!=null?c.rr.toFixed(2):'?'}\u00d7)</span>`:'<span style="color:#5d7a99">\u2013</span>'}</td>`;
      html+='</tr>';
    });
    html+='</tbody></table>';
    html+='<div style="margin:6px 0 14px;padding:6px 10px;background:#0d1828;border-left:3px solid #22405f;font-size:11px;color:#7f8c8d">Note: z-score (and the stratified test) indicate whether a difference is <i>real</i>; the RelRisk column indicates how <i>large</i> it is. A high z with a low RelRisk (~1.2&#215;) is a statistically real but practically modest lean.</div>';
    // --- stratified paired section ---
    const P=d.paired;
    if(P&&P.rows&&P.rows.length){
      html+='<div style="margin-top:18px;padding:8px 12px;background:#0d1828;border:1px solid #2c4a6e;border-radius:6px;font-size:12px;color:#c8d6e8;line-height:1.7">';
      html+='<b style="color:#7fb3d3">Stratified paired test (within each wafer\u00d7shot instance)</b>';
      html+='<div style="margin-top:4px;color:#95a5a6;font-size:11px">Stratified paired test: within each '+(P.strata||'stratum')+' instance, each DieLoc is compared to that instance\u2019s own mean, then averaged across all instances. This removes wafer AND shot variation and uses every mapped die. A DieLoc with a large positive deviation and high z is a strong intra-field signature.</div>';
      html+=`<div style="margin-top:4px;color:#5d7a99;font-size:11px">Strata: ${P.n_strata!=null?P.n_strata.toLocaleString():'?'} (${P.strata||''})</div>`;
      html+='</div>';
      html+='<table style="border-collapse:collapse;font-size:11px;margin-top:8px"><thead><tr>';
      ['DieLoc','Mean \u0394 vs baseline','z','# strata'].forEach(h=>{html+=`<th style="padding:4px 8px;color:#5d7a99;background:#0f2030">${h}</th>`;});
      html+='</tr></thead><tbody>';
      P.rows.forEach(r=>{
        const hiZ=r.z!=null&&r.z>=3&&r.mean_dev!=null&&r.mean_dev>0;
        const bg=hiZ?'rgba(231,76,60,0.13)':'';
        const devStr=r.mean_dev!=null?(r.mean_dev>=0?'+':'')+`${(r.mean_dev*100).toFixed(3)}%`:'\u2014';
        const devCol=r.mean_dev!=null&&r.mean_dev>0?'#e74c3c':r.mean_dev!=null&&r.mean_dev<0?'#2ecc71':'#7f8c8d';
        html+=`<tr style="border-bottom:1px solid #1a2f45;background:${bg}">`;
        html+=`<td style="padding:3px 8px;color:#a0c8f0;font-weight:bold">DieLoc ${r.loc}</td>`;
        html+=`<td style="padding:3px 8px;text-align:right;color:${devCol};font-weight:${hiZ?'bold':'normal'}">${devStr}</td>`;
        html+=`<td style="padding:3px 8px;text-align:right;color:${hiZ?'#e74c3c':r.z!=null&&r.z>=2?'#f39c12':'#7f8c8d'};font-weight:${hiZ?'bold':'normal'}">${r.z!=null?r.z.toFixed(2):'\u2014'}</td>`;
        html+=`<td style="padding:3px 8px;text-align:right;color:#7f8c8d">${r.n_strata!=null?r.n_strata.toLocaleString():'\u2014'}</td>`;
        html+='</tr>';
      });
      html+='</tbody></table>';
    }
    // --- FB x DieLoc matrix (IB targets only) ---
    if(typeof INTRAFIELD_FB!=='undefined'&&INTRAFIELD_FB&&INTRAFIELD_FB.rows&&INTRAFIELD_FB.rows.length){
      const F=INTRAFIELD_FB;
      html+='<div style="margin-top:18px;padding:8px 12px;background:#0d1828;border:1px solid #2c6e2c;border-radius:6px;font-size:12px;color:#c8d6e8;line-height:1.7">';
      html+='<b style="color:#2ecc71">FB \u00d7 DieLoc breakdown (IB targets only)</b>';
      html+='<div style="margin-top:4px;color:#95a5a6;font-size:11px">Each constituent FB is tested separately with the same stratified (wafer\u00d7shot) method. A die-frame / reticle die-position defect usually drives ONE FB, so it stands out here even when the pooled IB looks flat. Cells show stratified z; <span style="color:#e74c3c;font-weight:bold">red/bold</span> = z\u22653 (positive dev), <span style="color:#f39c12">orange</span> = 1.5\u20133.</div>';
      if(F.best&&F.best.z>=3){html+=`<div style="margin-top:6px;color:#e74c3c;font-size:12px;font-weight:bold">\u2605 Strongest: FB ${F.best.fb} at DieLoc ${F.best.loc} (z=${F.best.z}, +${(F.best.mean_dev*100).toFixed(2)}%)</div>`;}
      html+='</div>';
      html+='<div style="overflow-x:auto;margin-top:8px"><table style="border-collapse:collapse;font-size:11px"><thead><tr>';
      html+='<th style="padding:4px 8px;color:#5d7a99;background:#0f2030;white-space:nowrap">FB (n hits)</th>';
      F.locs.forEach(lc=>{html+=`<th style="padding:4px 8px;color:#5d7a99;background:#0f2030;white-space:nowrap">DieLoc ${lc}</th>`;});
      html+='</tr></thead><tbody>';
      F.rows.forEach(r=>{
        html+=`<tr style="border-bottom:1px solid #1a2f45"><td style="padding:3px 8px;color:#2ecc71;white-space:nowrap;font-weight:bold">FB ${r.fb}<br><span style="color:#5d7a99;font-size:10px;font-weight:normal">(${r.n_hits.toLocaleString()})</span></td>`;
        r.cells.forEach(c=>{
          const z=c.z;const hiZ=z!=null&&z>=3&&c.mean_dev!=null&&c.mean_dev>0;
          const midZ=z!=null&&z>=1.5&&!hiZ;
          const bg=hiZ?'rgba(231,76,60,0.20)':midZ?'rgba(243,156,18,0.15)':'';
          const col=hiZ?'#e74c3c':midZ?'#f39c12':'#5d7a99';
          const fw=hiZ?'bold':'normal';
          const title=c.mean_dev!=null?`title="${(c.mean_dev*100).toFixed(3)}% dev"`:'';
          html+=`<td style="padding:3px 8px;text-align:center;background:${bg};color:${col};font-weight:${fw}" ${title}>${z!=null?z.toFixed(2):'\u2014'}</td>`;
        });
        html+='</tr>';
      });
      html+='</tbody></table></div>';
    }
  }
  // --- parameter fingerprint (Part B) ---
  if(INTRAFIELD_PARAMS&&INTRAFIELD_PARAMS.cells&&INTRAFIELD_PARAMS.cells.length){
    html+='<div style="margin-top:18px;padding:8px 12px;background:#0d1828;border:1px solid #1e3a5f;border-radius:6px;font-size:12px;color:#c8d6e8;line-height:1.7">';
    html+='<b style="color:#7fb3d3">Parameter Fingerprint</b> &mdash; Mean % deviation vs field average ';
    html+='for die-level sort parameters at the hottest locations. ';
    html+='<b>PCM / etest parameters are excluded</b> (scribe-line measurements have no intra-field resolution). ';
    html+=`${INTRAFIELD_PARAMS.n_die_params} die-level parameter(s) evaluated. `;
    html+='Interpretation: slow (negative Vmin / low Isat) hot cells &rarr; speed/CD field signature; flat parameters &rarr; likely defect or probe artefact.';
    html+='</div>';
    INTRAFIELD_PARAMS.cells.forEach(cell=>{
      html+=`<h3 style="color:#f39c12;margin:14px 0 6px">Cell (${cell.rdx}, ${cell.rdy}) &mdash; fail ${(cell.fail_rate*100).toFixed(2)}%</h3>`;
      if(!cell.params||!cell.params.length){html+='<div class="no-data">No die-level parameters met the minimum-n threshold for this cell.</div>';return;}
      html+='<div style="overflow-x:auto"><table style="border-collapse:collapse;font-size:11px"><thead><tr>';
      ['Parameter','% dev vs field','z','n'].forEach(h=>{html+=`<th style="padding:4px 8px;color:#5d7a99;background:#0f2030;white-space:nowrap">${h}</th>`;});
      html+='</tr></thead><tbody>';
      cell.params.forEach(p=>{
        const az=Math.abs(p.z||0);
        const zCol=az>=3?'#e74c3c':az>=1.5?'#f39c12':'#7f8c8d';
        const pctStr=p.pct_dev!=null?(p.pct_dev>0?'+':'')+p.pct_dev.toFixed(2)+'%':'—';
        const zStr=p.z!=null?(p.z>0?'+':'')+p.z.toFixed(3):'—';
        html+=`<tr style="border-bottom:1px solid #1a2f45">`;
        html+=`<td style="padding:3px 8px;color:#7fb3d3;font-family:monospace;font-size:11px" title="${escQ(p.param)}">${p.param.length>50?p.param.slice(0,49)+'\u2026':p.param}</td>`;
        html+=`<td style="padding:3px 8px;color:${p.pct_dev!=null&&p.pct_dev<0?'#2ecc71':'#e74c3c'};text-align:right">${pctStr}</td>`;
        html+=`<td style="padding:3px 8px;color:${zCol};text-align:right;font-weight:${az>=3?'bold':'normal'}">${zStr}</td>`;
        html+=`<td style="padding:3px 8px;color:#7f8c8d;text-align:right">${p.n.toLocaleString()}</td></tr>`;
      });
      html+='</tbody></table></div>';
    });
  } else if(INTRAFIELD_DATA){
    html+='<div style="margin-top:12px;color:#7f8c8d;font-size:12px">No die-level parameters available for the parameter fingerprint (need UPM/SICC columns selected and &ge;200 dies per hot cell).</div>';
  }
  // --- consistency verdict ---
  const C=INTRAFIELD_DATA&&INTRAFIELD_DATA.consistency;
  if(C&&C.n_views){
    if(C.agree>=3){
      const vNames=Object.keys(C.views).map(k=>({'pooled_failrate':'pooled fail rate','relative_risk':'relative risk','stratified':'stratified paired','fb_best':'FB breakdown'}[k]||k)).join(', ');
      html+=`<div style="margin-top:18px;padding:10px 14px;background:#2a1a06;border:2px solid #e67e22;border-radius:6px;font-size:12px;color:#f0d0a0;line-height:1.7">`;
      html+=`<b style="color:#f39c12;font-size:13px">&#9654; Consistency flag: DieLoc ${C.worst_loc}</b> is the worst-ranked position in `;
      html+=`<b>${C.agree}/${C.n_views}</b> independent views (${vNames}). `;
      html+=`No single test is dramatic, but agreement across independent views is a credible intra-field signature &mdash; `;
      html+=`consistent with a die-frame / reticle field issue at this position.</div>`;
    } else {
      html+=`<div style="margin-top:12px;color:#5d7a99;font-size:11px">No die position is consistently worst across views (max agreement: ${C.agree}/${C.n_views}).</div>`;
    }
  }
  el.innerHTML=html;
  renderIntraConsistency();
}
function renderWaferTrendTab(){_trendFactory({records:(typeof WAFER_TREND_RECORDS!=='undefined'?WAFER_TREND_RECORDS:null),idField:'_LABEL',prefix:'wt',granLabel:'wafer',elId:'wafertrend-content',scrollable:true,title:'Lot·Wafer Fail Rate Trend (find outlier wafers)'});}

function _lerpColor(c1,c2,t){
  const p=v=>parseInt(v,16);
  const r1=p(c1.slice(1,3)),g1=p(c1.slice(3,5)),b1=p(c1.slice(5,7));
  const r2=p(c2.slice(1,3)),g2=p(c2.slice(3,5)),b2=p(c2.slice(5,7));
  const h=v=>Math.round(v).toString(16).padStart(2,'0');
  return'#'+h(r1+(r2-r1)*t)+h(g1+(g2-g1)*t)+h(b1+(b2-b1)*t);
}
function _multiLerp(stops,t){
  t=Math.max(0,Math.min(1,t));
  for(let i=1;i<stops.length;i++){
    if(t<=stops[i][0]){const t0=stops[i-1][0],t1=stops[i][0];return _lerpColor(stops[i-1][1],stops[i][1],t1>t0?(t-t0)/(t1-t0):0);}
  }
  return stops[stops.length-1][1];
}
function _buildHeatmapSvg(dies,g,retShots,clipId,showZoneRings){
  // Aggregates all dies per (x,y) cell and colors by mode-specific continuous scale
  const failC={},totalC={},zoneAcc={},ibAcc={},fbAcc={};
  dies.forEach(d=>{
    const k=d.x+','+d.y;
    totalC[k]=(totalC[k]||0)+1;
    if(d.t)failC[k]=(failC[k]||0)+1;
    const zv=d.z==null?null:(d.z==='Center'?0:d.z==='Mid'?1:d.z==='Edge'?2:null);
    if(zv!=null){if(!zoneAcc[k])zoneAcc[k]=[];zoneAcc[k].push(zv);}
    if(d.ib!=null){if(!ibAcc[k])ibAcc[k]=[];ibAcc[k].push(+d.ib);}
    if(d.fb!=null){if(!fbAcc[k])fbAcc[k]=[];fbAcc[k].push(+d.fb);}
  });
  const avg=a=>a.reduce((s,v)=>s+v,0)/a.length;
  let ibMin=Infinity,ibMax=-Infinity,fbMin=Infinity,fbMax=-Infinity;
  Object.values(ibAcc).forEach(a=>{const v=avg(a);if(v<ibMin)ibMin=v;if(v>ibMax)ibMax=v;});
  Object.values(fbAcc).forEach(a=>{const v=avg(a);if(v<fbMin)fbMin=v;if(v>fbMax)fbMax=v;});
  const mode=_wmColorMode||'target';
  const failStops=[[0,'#1a3a5c'],[0.3,'#f39c12'],[0.6,'#e74c3c'],[1,'#ff0000']];
  // normalize fail rate to wafer max so gradient always spans full range
  const _maxCellFr=Math.max(...Object.keys(totalC).map(k=>(failC[k]||0)/totalC[k]),0.001);
  // high-contrast zone stops: Center=cyan-blue, Mid=bright yellow, Edge=hot red
  const zoneStops=[[0,'#00c8ff'],[0.5,'#f9ca24'],[1,'#ff3f3f']];
  const numStops=[[0,'#1a6ea8'],[0.5,'#f9ca24'],[1,'#ff3f3f']];
  const cd=`<defs><clipPath id="${clipId}"><ellipse cx="${g.eCx}" cy="${g.eCy}" rx="${g.eRx}" ry="${g.eRy}"/></clipPath></defs>`;
  const cb=`<ellipse cx="${g.eCx}" cy="${g.eCy}" rx="${g.eRx}" ry="${g.eRy}" fill="none" stroke="#a0bcd8" stroke-width="${g.TW>300?3:2}"/>`;
  let rects='';
  Object.keys(totalC).forEach(k=>{
    const [x,y]=k.split(',').map(Number);
    const px=(g.PAD+(x-g.xMin)*g.cs).toFixed(1),py=(g.PAD+(g.yMax-y)*g.csy).toFixed(1);
    const dw=(g.cs*0.9).toFixed(1),dh=(g.csy*0.9).toFixed(1);
    let color;
    if(mode==='zone'){const a=zoneAcc[k];color=a?_multiLerp(zoneStops,avg(a)/2):'#445566';}
    else if(mode==='ib'){const a=ibAcc[k];color=a?_multiLerp(numStops,ibMax>ibMin?(avg(a)-ibMin)/(ibMax-ibMin):0.5):'#334455';}
    else if(mode==='fb'){const a=fbAcc[k];color=a?_multiLerp(numStops,fbMax>fbMin?(avg(a)-fbMin)/(fbMax-fbMin):0.5):'#334455';}
    else{const fr=(failC[k]||0)/totalC[k];color=_multiLerp(failStops,fr/_maxCellFr);}
    rects+=`<rect x="${px}" y="${py}" width="${dw}" height="${dh}" fill="${color}"/>`;
  });
  let rings='';
  if(showZoneRings){
    rings+=`<ellipse cx="${g.eCx}" cy="${g.eCy}" rx="${(g.eRx*0.40).toFixed(1)}" ry="${(g.eRy*0.40).toFixed(1)}" fill="none" stroke="rgba(52,152,219,0.6)" stroke-width="1.5" stroke-dasharray="5,3"/>`;
    rings+=`<ellipse cx="${g.eCx}" cy="${g.eCy}" rx="${(g.eRx*0.70).toFixed(1)}" ry="${(g.eRy*0.70).toFixed(1)}" fill="none" stroke="rgba(230,126,34,0.6)" stroke-width="1.5" stroke-dasharray="5,3"/>`;
  }
  // --- colour-scale legend appended below the wafer map ---
  const LH=18, LPad=8, LW=g.TW-LPad*2, totalH=g.TH+LH+20;
  const ly=g.TH+6;
  let legend='';
  const gradId=clipId+'_lg';
  if(mode==='zone'){
    // discrete: Center / Mid / Edge swatches
    const sw=Math.floor(LW/3)-4;
    const zones=[['Center','#00c8ff'],['Mid','#f9ca24'],['Edge','#ff3f3f']];
    zones.forEach(([lbl,col],i)=>{
      const bx=LPad+i*(sw+6);
      legend+=`<rect x="${bx}" y="${ly}" width="${sw}" height="${LH-2}" fill="${col}" rx="2"/>`;
      legend+=`<text x="${bx+sw/2}" y="${ly+LH/2+1}" text-anchor="middle" dominant-baseline="middle" font-size="10" fill="#0d1b26" font-weight="bold">${lbl}</text>`;
    });
  } else {
    // continuous gradient bar
    let gStops='';
    const stops=mode==='ib'||mode==='fb'?numStops:failStops;
    stops.forEach(([t,col])=>{gStops+=`<stop offset="${(t*100).toFixed(0)}%" stop-color="${col}"/>`;});
    legend+=`<defs><linearGradient id="${gradId}" x1="0" y1="0" x2="1" y2="0">${gStops}</linearGradient></defs>`;
    legend+=`<rect x="${LPad}" y="${ly}" width="${LW}" height="${LH-2}" fill="url(#${gradId})" rx="2"/>`;
    // tick marks at 0 / 50 / 100 %
    const tickPts=mode==='target'?[[0,'0%'],[0.5,`${(_maxCellFr*50).toFixed(1)}%`],[1,`${(_maxCellFr*100).toFixed(1)}%`]]:[[0,'Low'],[0.5,'Mid'],[1,'High']];
    tickPts.forEach(([t,lbl])=>{
      const tx=(LPad+t*LW).toFixed(1);
      const anchor=t===0?'start':t===1?'end':'middle';
      legend+=`<line x1="${tx}" y1="${ly+LH-2}" x2="${tx}" y2="${ly+LH+2}" stroke="#a0bcd8" stroke-width="1"/>`;
      legend+=`<text x="${tx}" y="${ly+LH+10}" text-anchor="${anchor}" font-size="9" fill="#a0bcd8">${lbl}</text>`;
    });
    // mode label on left above bar
    const modeLabel=mode==='target'?'Fail rate (% of dies)':mode==='ib'?'Interface Bin':mode==='fb'?'Functional Bin':'Zone';
    legend+=`<text x="${LPad}" y="${ly-2}" font-size="9" fill="#7fb3d3">${modeLabel}</text>`;
  }
  return`<svg xmlns="http://www.w3.org/2000/svg" width="${g.TW}" height="${totalH}">${cd}<g clip-path="url(#${clipId})">${rects}${_retSvg(g,retShots)}</g>${rings}${cb}${legend}</svg>`;
}
function _renderSvgHeatmap(){
  const hw=document.getElementById('wm-heatmap-wrap');
  if(!hw||!WAFER_MAP_DATA)return;
  const wmd=WAFER_MAP_DATA;
  const g=_wmGeom(540,wmd.xMin,wmd.xMax,wmd.yMin,wmd.yMax);
  const retShots=_buildWmRetShots(wmd.reticle_map);
  hw.innerHTML=_buildHeatmapSvg(wmd.dies||[],g,retShots,'wm_heat',true);
}
function toggleFailHeatmap(){
  const hw=document.getElementById('wm-heatmap-wrap');
  const wm=document.getElementById('wm-composite');
  const hBtn=document.getElementById('wmbtn-heatmap');
  const wBtn=document.getElementById('wmbtn-wm');
  if(!hw)return;
  hw.style.display='block';if(wm)wm.style.display='none';
  if(hBtn){hBtn.style.background='#4a235a';hBtn.style.color='#ecf0f1';}
  if(wBtn){wBtn.style.background='#162840';wBtn.style.color='#7fb3d3';}
  _renderSvgHeatmap();
}
function toggleWaferMap(){
  const hw=document.getElementById('wm-heatmap-wrap');
  const wm=document.getElementById('wm-composite');
  const hBtn=document.getElementById('wmbtn-heatmap');
  const wBtn=document.getElementById('wmbtn-wm');
  if(!wm)return;
  hw.style.display='none';wm.style.display='block';
  if(hBtn){hBtn.style.background='#162840';hBtn.style.color='#c39bd3';}
  if(wBtn){wBtn.style.background='#1a5276';wBtn.style.color='#ecf0f1';}
}
function zoneSubTab(subId,active){
  const bar=document.getElementById(subId+'-bar');
  const keys=bar?(bar.dataset.keys||'').split('|').filter(Boolean):[];
  keys.forEach(gk=>{
    const pane=document.getElementById(subId+'-pane-'+gk);
    const btn=document.getElementById(subId+'-btn-'+gk);
    if(pane)pane.style.display=gk===active?'block':'none';
    if(btn){btn.style.background=gk===active?'#1a5276':'#162840';btn.style.color=gk===active?'#ecf0f1':'#7fb3d3';}
  });
}
function setWmMode(mode){
  _wmColorMode=mode;
  document.querySelectorAll('[id^=wmbtn-]').forEach(b=>{
    const k=b.id.replace('wmbtn-','');
    b.style.background=k===mode?'#1a5276':'#162840';
    b.style.color=k===mode?'#ecf0f1':(b.disabled?'#445566':'#7fb3d3');
  });
  // Re-render composite and tiles
  if(!WAFER_MAP_DATA)return;
  const wmd=WAFER_MAP_DATA;
  const dies=wmd.dies||[];
  const retShots=_buildWmRetShots(wmd.reticle_map);
  const g=_wmGeom(540,wmd.xMin,wmd.xMax,wmd.yMin,wmd.yMax);
  const cp=document.getElementById('wm-composite');
  if(cp){const _cm={};dies.forEach(d=>{const k=d.x+','+d.y;if(!_cm[k]||d.t)_cm[k]=d;});cp.innerHTML=_buildWaferSvg(Object.values(_cm),g,retShots,'wm_cp2',true);}
  // tiles: no reticle labels
  const tilesDiv=document.getElementById('wm-tiles');
  if(tilesDiv){
    const waferMap={};
    dies.forEach(d=>{const wk=d.w!=null?String(d.w):'all';if(!waferMap[wk])waferMap[wk]=[];waferMap[wk].push(d);});
    const waferKeys=Object.keys(waferMap).sort();
    const gS=_wmGeom(200,wmd.xMin,wmd.xMax,wmd.yMin,wmd.yMax);
    tilesDiv.innerHTML=waferKeys.map((wk,ti)=>{
      const wDies=waferMap[wk];
      return`<div style="text-align:center"><div style="font-size:9px;color:#8ab4d4;margin-bottom:2px">${escQ(wk)}</div>${_buildWaferSvg(wDies,gS,[],'wm_rt'+ti,false)}<div style="font-size:9px;color:#ff8080;margin-top:1px">${wDies.filter(d=>d.t).length} hits</div></div>`;
    }).join('');
  }
  // If heatmap view is currently visible, re-render it with the new mode
  const hw=document.getElementById('wm-heatmap-wrap');
  if(hw&&hw.style.display!=='none')_renderSvgHeatmap();
}
</script>
"""

    # --- log helpers ---

    def _log_line(self, msg, tag=""):
        def _do():
            self._log.config(state="normal")
            self._log.insert("end", msg + "\n", tag)
            self._log.see("end")
            self._log.config(state="disabled")
        self.after(0, _do)

    def _status(self, msg):
        self.after(0, lambda: self._lbl_status.config(text=msg))


# --- entry point ---

def _run_self_tests():
    """Run built-in unit tests. Call with: python correlation-analysis.py --test"""
    import traceback
    failures = []

    def _check(name, fn):
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:
            failures.append(name)
            print(f"  FAIL  {name}: {e}")
            traceback.print_exc()

    def t_signal_strong():
        assert signal_label(0.50, 0.0, 624) == "Strong"

    def t_signal_moderate():
        assert signal_label(0.20, 0.02, 656) == "Moderate"

    def t_signal_weak():
        assert signal_label(0.05, 0.08, 262851) == "Weak / directional"

    def t_signal_exploratory():
        assert signal_label(0.35, 0.23, 75) == "Exploratory"

    def t_duplicate_flagging():
        top = [
            {"param": "SPA_M11",  "r": -0.064377, "p": 2.2e-239, "q": 0},
            {"param": "SPA_M12",  "r": -0.064377, "p": 2.2e-239, "q": 0},
            {"param": "Isat_P3L", "r": -0.072673, "p": 1.2e-304, "q": 0},
        ]
        flag_duplicate_stats(top)
        dup = {i["param"]: i["is_duplicate_stat"] for i in top}
        assert dup["SPA_M11"] and dup["SPA_M12"]
        assert not dup["Isat_P3L"]

    def t_separation_note_fires():
        note = separation_note(-0.0727, -1.2474)
        assert "separation" in note.lower()

    def t_separation_note_silent():
        assert separation_note(0.40, 1.0) == ""

    def t_tab_order():
        assert TAB_ORDER.index("pcm_wdev") < TAB_ORDER.index("pearson")
        assert TAB_ORDER.index("pcm_wdev") < TAB_ORDER.index("pcm_lot")

    tests = [
        ("signal_label/strong",       t_signal_strong),
        ("signal_label/moderate",     t_signal_moderate),
        ("signal_label/weak",         t_signal_weak),
        ("signal_label/exploratory",  t_signal_exploratory),
        ("flag_duplicate_stats",      t_duplicate_flagging),
        ("separation_note/fires",     t_separation_note_fires),
        ("separation_note/silent",    t_separation_note_silent),
        ("tab_order",                 t_tab_order),
    ]
    print(f"\nRunning {len(tests)} self-tests...")
    for name, fn in tests:
        _check(name, fn)
    if failures:
        print(f"\n{len(failures)} FAILED: {', '.join(failures)}")
        raise SystemExit(1)
    print(f"\nAll {len(tests)} tests passed.")


def main():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        _run_self_tests()
    else:
        main()
