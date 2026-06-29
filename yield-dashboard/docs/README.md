# Yield Analysis Dashboard

A unified Tkinter GUI that brings together five yield-analysis tools in a single tabbed window.

---

## Quick Start

```powershell
# From the yield-dashboard directory
& "C:\scripts\.venv\Scripts\python.exe" dashboard.py
```

Requires the `.venv` virtual environment at `C:\scripts\.venv` (see [Dependencies](#dependencies)).

---

## Tabs

### Create
*Source: `yld/src/pipeline.py` → `PipelineFrame`*

Runs the full yield analysis pipeline for a given AQUA CSV or JSON config.

- Browse to an input CSV / JSON config file
- Set pipeline parameters (lot filter, bindef, fail-bucket table, etc.)
- Click **Run Pipeline** — progress streams to the log pane
- On success, opens the generated `index.html` report in the default browser
- Optionally launches the SICC UPM or SICC CDYN UPM sibling dashboards

After a successful run, `_last_dashboard_html` is stored on the frame and propagated
automatically to the **Compare** and **Manage** tabs when you switch to them.

---

### Compare
*Source: `yld/src/compareTP.py` → `CompareFrame`*

Compares multiple run identifiers side-by-side and generates a combined HTML report.

- Point to a `Dashboard.html` — the run list is populated automatically
- Check the runs to include, set an output path
- Click **Compare Selected** — output opens in the browser

---

### Manage
*Source: `yld/src/manage_dashboard.py` → `ManageFrame`*

Reorders or removes run blocks from an existing `Dashboard.html`.

- Load any `Dashboard.html`
- Drag rows to reorder, or select and delete
- **Save** writes the modified HTML in place (original backed up as `Dashboard.html.bak`)

---

### Portable
*Source: `yld/src/make_portable_dashboard.py` → `PortableFrame`*

Builds a fully self-contained single-file copy of a `Dashboard.html`.

| Link type | Strategy |
|-----------|----------|
| Relative / `file://` HTML | Embedded as Blob URL |
| Images / CSS | Inlined as base64 data URIs |
| `iframe src` HTML | Converted to `srcdoc` |
| `.xlsx`, `.jmp`, `.jmpprj` | Button disabled (cannot embed) |

- Browse to the source `Dashboard.html`
- Set output path (default: `Dashboard_portable.html` in the same folder)
- Click **Build Portable Copy**

---

### Wafer Map
*Source: `yld/src/generate_bin_wafer_heatmaps.py` → `WaferHeatmapFrame`*

Generates per-IBIN wafer heatmaps (PNG + HTML summary) for all interface bins whose
observed fallout exceeds the expected yield threshold.

#### Inputs

| Field | Description |
|-------|-------------|
| **Yield CSV** | Output CSV from the yield pipeline (must contain Sort_X, Sort_Y, Lot, Wafer, and the IBIN column) |
| **YieldTarget JSON/txt** | `fail_bucket_table.txt` or JSON with combined-bin expected yields |
| **Bindef CSV** *(optional)* | Parsed bindef CSV with `Expected Yield(%)` per bin |
| **Output folder** | Where PNGs and the summary HTML are written (auto-filled from CSV path) |

#### Column Name Overrides

Defaults match NVL816-BLLC op 119325. Change only if your CSV uses different names:

| Setting | Default |
|---------|---------|
| IBIN column | `INTERFACE_BIN_119325` |
| Sort X | `Sort_X` |
| Sort Y | `Sort_Y` |
| Lot | `Lot` |
| Wafer | `Wafer` |

#### Options

- **Force** — generate heatmaps for all IBINs even when no expected yield is defined
  (otherwise only bins that exceed threshold are generated)

---

## Local HTTP Opener Server

On launch, `dashboard.py` starts a lightweight `socketserver.TCPServer` on a random
localhost port. HTML reports use `onclick` handlers that call this server with a
`?path=...` query string; the server calls `os.startfile()` so `.jmpprj`/`.jmp` files
open in JMP instead of downloading.

The server is a daemon thread — it exits automatically when the GUI closes.

---

## File Layout

```
yield-dashboard/
├── dashboard.py            ← entry point (this README covers this file)
├── yld/
│   └── src/
│       ├── pipeline.py             ← Create tab logic (orchestrator)
│       ├── _pipeline_ui.py         ← UI mixins for PipelineFrame
│       ├── _pipeline_runner.py     ← run_pipeline, SICC runner
│       ├── _pipeline_html.py       ← HTML builders (pareto, master, dashboard)
│       ├── _pipeline_server.py     ← HTTP file-opener mixin
│       ├── compareTP.py            ← Compare tab
│       ├── compare_runs.py         ← compare_runs logic
│       ├── manage_dashboard.py     ← Manage tab
│       ├── make_portable_dashboard.py  ← Portable tab
│       ├── generate_bin_wafer_heatmaps.py  ← Wafer Map tab
│       ├── yield_pipeline.py       ← core yield analysis logic
│       ├── bin_distribution_html.py
│       ├── generate_plots_from_csv.py
│       ├── csv_utils.py
│       └── _loader.py              ← dispatches to compiled .pyd modules
└── README.md
```

---

## Dependencies

All packages are in the shared `C:\scripts\.venv`:

```
pandas
numpy
matplotlib
seaborn
pywin32          # win32com — Outlook COM for email
```

Install / update:

```powershell
& "C:\scripts\.venv\Scripts\pip.exe" install -r yld/requirements.txt `
    --proxy http://proxy-us.intel.com:911
```

---

## Standalone Usage

Each module still works independently:

```powershell
# Pipeline only
python yld/src/pipeline.py

# Compare only
python yld/src/compareTP.py

# Manage only
python yld/src/manage_dashboard.py

# Make portable
python yld/src/make_portable_dashboard.py Dashboard.html --out Dashboard_portable.html

# Wafer heatmaps (CLI)
python yld/src/generate_bin_wafer_heatmaps.py `
    --data yield.csv `
    --failbuckets fail_bucket_table.txt `
    --outdir wafer_heatmaps `
    --force
```

---

*Pant, Sujit N — GEMS FTE*
