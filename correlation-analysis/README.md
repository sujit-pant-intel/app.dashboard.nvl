# Sort + Etest Correlation Dashboard

**Author:** Pant, Sujit N — GEMS FTE  
**File:** `correlation-analysis.py`  
**Output:** self-contained `correlation_report.html` (or per-target files + sidebar wrapper)

---

## Purpose

Answers: *Which process or electrical parameters are associated with yield loss, in what direction, at what aggregation level, and which lots or wafers should be investigated first?*

Combines die-level sort parameters (UPM / SICC / CDYN), PCM/etest data merged from 9-site or full-site zip archives, target-bin logic (IB / FB), wafer coordinates, radial zones, and reticle metadata.

---

## Quick Start

```powershell
pip install -r requirements.txt --proxy http://proxy-us.intel.com:911
python correlation-analysis.py
```

The GUI guides you through four steps:

| Step | Action |
|------|--------|
| 1 | Load one or more Sort CSV / ZIP / GZ files |
| 2 | Discover PCM/etest sources from `shared/etest/` |
| 3 | Select parameters (UPM/SICC/CDYN + PCM tree) |
| 4 | Choose target bins (IB or FB), set ignore lists, run |

---

## Files

| File | Purpose |
|------|---------|
| `correlation-analysis.py` | Single-file app — GUI, pipeline, HTML report generator |
| `requirements.txt` | pip dependencies |
| `correlation-analysis-flowchart.html` | Interactive architecture map (open in browser) |
| `shared/etest/full-sites/` | Full-site PCM zip archives |
| `shared/etest/9-sites/` | 9-site PCM zip archives (IDW-eligible) |
| `shared/reticle/` | Reticle map CSVs (`DieX, DieY, LayoutX, LayoutY, Reticle`) |

---

## Correlation Methods

| Tab | Unit | Description |
|-----|------|-------------|
| **Lot Level Trend** | lot | Fail-rate trend + High-vs-Low parametric analysis |
| **Wafer Level Trend** | lot/wafer | Wafer-level fail trend; flags outlier wafers |
| **PCM Deviation (Wafer)** | wafer | `\|wafer_median − lot_center\|` vs wafer fail rate — strongest signal for rare bins |
| **PCM Deviation (Lot)** | lot | `\|lot_median − population_median\|` vs lot fail rate; detects U-shaped process-window effects |
| **PCM Wafer-Level** | wafer | Median PCM per wafer vs wafer fail rate |
| **PCM Lot-Level** | lot | Median PCM per lot vs lot fail rate |
| **Zone Analysis** | die | Wafer map, zone × quartile heat tables, reticle shot fail rates |
| **Pearson r** | die | Linear die-level correlation + Cohen d + fail ratio |
| **Spearman rho** | die | Rank-based die-level correlation; robust to outliers |
| **Spatial / Radial** | die | Geometry covariates (radius, reticle) — reported separately from electrical params |
| **Repeatability** | die/wafer | Die locations failing on ≥30 % of wafers → systematic vs random |
| **Co-Failure** | die | IB/FB distribution among failing dies |
| **Reticle** | shot | Fail rate per reticle shot field |
| **AI Review** | — | Packages top findings as a prompt for Copilot / LLM review (no data leaves the browser) |

---

## Key Architecture Decisions

### IDW median fix
IDW assigns a distinct interpolated PCM value to each die. Lot/wafer rollups **must** use `median`, not `first`. Using `first` would select one arbitrary die's value and corrupt all PCM-level correlation results.

### Object-dtype coercion
PCM columns can be read as object/string due to blanks or mixed values. All median-aggregation paths coerce with `pd.to_numeric(errors="coerce")` before groupby to ensure numeric retention.

### Merge-once across targets
When multiple target bins run in one batch, the merge + IDW + zone work is computed once. Only `_TARGET` is recomputed per additional target via `_apply_target()`.

### PCM-no-IDW exclusion from die-level methods
PCM columns filled via lot-median (no IDW) have identical values per die within a lot. Running Pearson/Spearman on them would inflate `n` artificially. Those columns are excluded from die-level methods and only appear in lot/wafer-level tabs.

### False-positive guard (High-vs-Low)
Near-constant parameters with vanishingly small pooled SD are excluded before Cohen-d ranking. This prevents degenerate t-statistics from floating to the top of the High-vs-Low table.

### Data-quality filters (inlined)
`_drop_constant_columns` and `_tag_structural_covariates` are defined directly in `correlation-analysis.py`. No separate `data_quality.py` is needed.

- **`_drop_constant_columns`** — drops SPA_\* prefixed columns, columns with < 3 unique values, zero-variance columns, and flat columns where fail/pass ratio ≈ 1.
- **`_tag_structural_covariates`** — routes geometry fields (radius, reticle, shot, zone, sort_x, sort_y) to the Spatial tab instead of electrical rankings.

### FDR correction
Benjamini-Hochberg q-values are applied per die-level method family (Pearson, Spearman) to control false discoveries across hundreds of parameters.

### Signal badge
Each result is labeled: **Strong** (q ≤ 0.01, |r| ≥ 0.30) · **Moderate** (q ≤ 0.05, |r| ≥ 0.15) · **Weak / directional** (q ≤ 0.10) · **Exploratory**.

---

## PCM Discovery

`find_pcm_for_lots()` scans `shared/etest/full-sites/` first, then `9-sites/`. If any full-site file matches a lot, 9-site entries for that lot are discarded. An optional `Extra etest folder` field accepts a local path.

IDW interpolation applies only when:
- The PCM file is 9-site (not full-site)
- `LayoutX / LayoutY` columns are present in the PCM file
- `SORT_X / SORT_Y` are present in the sort CSV
- A matching reticle map CSV exists in `shared/reticle/`

---

## Report Output

| File | Contents |
|------|---------|
| `output/correlation_report.html` | Single-target report (or sidebar wrapper for multi-target) |
| `output/correlation_<target>.html` | Per-target report when multiple bins are analyzed |
| `output/ideal_analysis_data.csv` | Merged analysis DataFrame for the first target |
| `pipeline_timing.log` | Per-stage wall-clock timings |
