# Yield Trend Chart Generator

An interactive HTML report generator showing iBin fail rates over time, correlated with overall yield metrics (FF, FF+DF).

---

## Quick Start

```powershell
# Navigate to the trend chart source directory
cd "c:\scripts\app.yield.nvl\code\dashboard\yield-dashboard\yld\src"

# Generate HTML (auto-detects product config)
python trend_chart.py "C:\path\to\input.csv"

# Specify output path
python trend_chart.py "C:\path\to\input.csv" --out "C:\path\to\output.html"

# Custom product config & options
python trend_chart.py "C:\path\to\input.csv" --cfg "C:\path\to\config.json" `
  --interval weekly --topn 20 --thresh 0.5 --out "C:\path\to\output.html"
```

---

## Command-Line Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `csv` | Path | **required** | Input CSV file (yield data) |
| `--cfg` | Path | auto-detect | Product config JSON (bin names, IBIN targets, FF target) |
| `--interval` | Choice | `weekly` | Time grouping: `daily`, `weekly`, `monthly` |
| `--topn` | Int | 8 | Top N failing IBins to show |
| `--thresh` | Float | 0.0 | Min threshold (%) to include in top-N |
| `--out` | Path | `<csv>_trend.html` | Output HTML file path |

---

## Interactive Features

### Trend Chart (Top)

**Fail Rate Bars** — Stacked bars show fail % for each period, grouped by top-N IBins

- **Hover**: Shows lot name with material, wafer, program, date, and fail count + percentage
  - Format: `Lot(Material)`, `Fail: N (Pct%)`
- **Click**: Opens **Functional Bin Drilldown** table for the clicked bar
  - Shows only FBs from that bar's runs
  - Total Tested = all selected wafers from sidebar

**Yield Lines** — Blue (FF) and green dashed (FF+DF) trend lines

- **Hover**: Shows overall yield % + IB/FB breakdown (formatted, selectable)
  - **FF Line (Blue)**: Shows IB1, IB2 + their FBs
  - **FF+DF Line (Green)**: Shows IB1, IB2, IB3, IB4 + their FBs
  - Both include summary of FB126/226/326/426 if present
- **Click**: Shows sticky tooltip — formatted text for copy-paste (close with ✕ or Escape)

---

### Functional Bin Drilldown (Bottom, Trend Tab)

Displayed when you click a histogram bar. Shows detailed FB breakdown.

**Columns:**
- **Interface Bin** — iBin number
- **Lot (Wafers)** — Merged format: `Lot1(W1,W2), Lot2(W3,W4)` (sorted)
- **Functional Bin** — FB number
- **Description** — FB description from product config
- **Fail Test Module** — Which test instance set the FB
- **Total Tested** — Count of all selected wafers in sidebar (same for all FBs in this bar click)
- **Fail Count** — Units failing this FB (from clicked bar only)
- **Fail %** — (Fail Count / Total Tested) × 100

---

### Pareto Charts (Horizontal & Vertical)

**Horizontal Pareto** — Top 20 IBins by fail rate (left panel)

- **Hover**: Avg fail % + IB/FB breakdown
- **Click**: Opens FB drilldown for that IB

**Vertical Pareto** — Fail % vs cumulative % (right panel)

- Shows bars (fail %) + cumulative line
- Hover for details
- Table below lists top failing bins with fail modules

---

### Sidebar Controls (Left)

**Program Filter** — Limit to selected test programs

**Lot/Wafer Grouper** — Toggle lot- or wafer-level view

- **Lot mode**: Aggregates all wafers per lot per period
- **Wafer mode**: Shows each wafer as a separate bar
- Lots grouped by first 7 characters; material name in group header

**Legend Toggle** — Click IB legend items to hide/show bars

---

## Product Config JSON

Optional but recommended. Enables:
- IB/FB names and descriptions
- IB yield targets for reference lines
- FF/FF+DF yield targets

**Example:**

```json
{
  "product": "NVL816",
  "bin_map": {
    "1": { "cat": "Scan", "desc": "SCAN_FAIL" },
    "2": { "cat": "Memory", "desc": "Memory Timeout" },
    "41": { "cat": "Functional", "desc": "Logic Fail" }
  },
  "fb_map": {
    "101": { "bdesc": "SCAN_CHAIN_CHECK" },
    "198": { "bdesc": "SCAN_EDGE_TEST" },
    "201": { "bdesc": "MEMORY_READ" }
  },
  "yield_target": {
    "ff": 98.5,
    "ff_df": 96.0
  },
  "ff_name": "SDS FF",
  "ff_df_name": "SDS FF+DF"
}
```

---

## Data Format (Input CSV)

Requires columns:
- `lot` — Lot identifier
- `sort_lot` — Sort lot (7-char prefix used for grouping)
- `wafer` — Wafer number
- `material` — Material name
- `program` — Test program name
- `date` — Test date (YYYY-MM-DD format)
- `total_dies` — Total die count
- `bin_counts` — JSON dict of `{ib: count}`
- `fb_counts` — JSON dict of `{ib: {fb: count}}`
- `ff_yield` — Final fine (%) — decimal 0–100
- `ff_df_yield` — Final fine + defect-free (%) — decimal 0–100

---

## Output

**Single HTML file** containing:
- Fully self-contained: all data embedded as JSON
- Interactive: filter, toggle, click without server
- Portable: view offline, email, share
- Print-friendly CSS included

---

## Troubleshooting

**"ERROR: file not found"** — Check CSV path exists and is readable

**"No product config — ibin names not shown"** — Provide `--cfg` or ensure auto-detection finds it in shared materials folder

**Chart not updating after sidebar filter** — Refresh the page (F5)

**Tooltip not closing** — Press Escape or click the ✕ button

---

## Examples

### Weekly trend for a single program

```powershell
python trend_chart.py "C:\data\yield_2024.csv" `
  --cfg "C:\shared\nvl816_config.json" `
  --interval weekly --topn 10 `
  --out "C:\reports\trend_weekly.html"
```

### Daily trend with custom thresholds

```powershell
python trend_chart.py "C:\data\yield_2024.csv" `
  --interval daily --thresh 0.5 --topn 15 `
  --out "C:\reports\trend_daily.html"
```

### Auto-discover everything

```powershell
python trend_chart.py "C:\data\yield_2024.csv"
# Output: C:\data\yield_2024_trend.html
```
