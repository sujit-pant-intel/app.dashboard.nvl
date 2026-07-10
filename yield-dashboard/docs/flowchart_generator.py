"""
flowchart_generator.py  —  Pipeline Tab Architecture Flowchart
==============================================================
Focus: Yield Analysis Dashboard  →  Pipeline Tab flow only.

Interactive features:
  • Click any node  → highlights that node + all direct neighbours;
                      everything else dims.
  • Click same node again, or click empty area  → reset.
  • Hover any node  → detailed tooltip.
  • Scroll to zoom, drag to pan, camera button exports PNG.

Usage:
    python flowchart_generator.py
    python flowchart_generator.py --out my_chart.html
"""
from __future__ import annotations
import argparse, json, os, sys

try:
    import plotly.graph_objects as go
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "plotly"])
    import plotly.graph_objects as go

# ─────────────────────────────────────────────────────────────────────────────
# LAYOUT
# ─────────────────────────────────────────────────────────────────────────────
NW, NH = 5.2, 1.05     # node half-width, half-height (data units)
BG     = '#0d1b24'

SX = dict(entry=0, inputs=14, pipeline=28, processing=42, outputs=56)

BANDS = [
    ('entry',      -6.5,  6.5, 'ENTRY POINT'),
    ('inputs',      6.5, 20.5, 'DATA INPUTS'),
    ('pipeline',   20.5, 34.5, 'PIPELINE STEPS'),
    ('processing', 34.5, 48.5, 'PROCESSING MODULES'),
    ('outputs',    48.5, 62.5, 'OUTPUT ARTIFACTS'),
]
N_BANDS = len(BANDS)

BAND_COLORS = {
    'entry':      ('#1a5276', '#2980b9'),
    'inputs':     ('#7d4f0f', '#e67e22'),
    'pipeline':   ('#7b241c', '#e74c3c'),
    'processing': ('#145a32', '#27ae60'),
    'outputs':    ('#7d6608', '#f1c40f'),
}

Y_MIN, Y_MAX = 0.0, 20.5

# ─────────────────────────────────────────────────────────────────────────────
# NODES   (id, stage, y, fill, border, l1, l2, hover)
# ─────────────────────────────────────────────────────────────────────────────
def _n(id_, stage, y, l1, l2, hover):
    fc, bc = BAND_COLORS[stage]
    return dict(id=id_, x=SX[stage], y=y, fill=fc, border=bc,
                l1=l1, l2=l2, hover=hover)

NODES = [
    # ── Entry (3 nodes, y = 14 / 10 / 6) ────────────────────────────────────
    _n('e_gui', 'entry', 14.5, 'dashboard.py', 'Pipeline Tab  (GUI)',
       '<b>dashboard.py  —  Pipeline Tab</b><br>'
       '<b>Framework:</b> Tkinter + ttk, dark theme<br>'
       '<b>Class:</b> PipelineFrame (pipeline.py)<br>'
       '<b>Mixins:</b> OpenerServerMixin, PipelineUIMixin,<br>'
       '&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;PipelineRunnerMixin, PipelineHtmlMixin<br><br>'
       '<b>Required inputs (green ★):</b><br>'
       '&nbsp;• Data CSVs (.csv / .gz / .zip / .7z)<br>'
       '&nbsp;• BinDefs / TestProgram folder<br>'
       '<b>Auto-populated:</b><br>'
       '&nbsp;• Dashboard.html path &nbsp;• Output folder<br>'
       '&nbsp;• AQUA server + report path<br>'
       '&nbsp;• Product config JSON  &nbsp;• Run identifier<br><br>'
       '<b>Buttons:</b> Run | Load JSON | Save JSON | Open Dashboard | ↺ Reset<br>'
       '<b>Log panel:</b> live output with [total | Δsection] timing<br>'
       '<b>HTTP opener server:</b> localhost random port (JMP launch)'),

    _n('e_cli', 'entry', 10.0, 'yield_pipeline.py', 'Headless CLI',
       '<b>yield_pipeline.py  —  Headless CLI</b><br>'
       '<b>Usage:</b> python yield_pipeline.py --input cfg.json<br>'
       '<b>Same pipeline logic as GUI; no window required</b><br><br>'
       '<b>Steps run:</b><br>'
       '&nbsp;1. AQUA fetch (if configured)<br>'
       '&nbsp;2. Parse BinDefs → CrystalBall CSV<br>'
       '&nbsp;3. DD update (append cols B / C)<br>'
       '&nbsp;4. Full analysis run<br><br>'
       '<b>Used by:</b> auto_pull_and_run.py for automation<br>'
       '<b>Output:</b> same artifacts as GUI pipeline run'),

    _n('e_auto', 'entry', 5.5, 'auto_pull_and_run.py', 'Full Automation',
       '<b>auto_pull_and_run.py  —  Full Automation</b><br>'
       '<b>Steps:</b><br>'
       '&nbsp;1. Pull AQUA data (NCXSDJXL0H61* lots)<br>'
       '&nbsp;2. Change detection vs snapshot.json<br>'
       '&nbsp;3. If changed → run yield_pipeline.py headless<br>'
       '&nbsp;4. Publish results to SharePoint<br>'
       '&nbsp;5. Send completion email<br><br>'
       '<b>Args:</b> --dry-run  --force  --days N<br>'
       '<b>Default lookback:</b> 7 days<br>'
       '<b>Triggered by:</b> Windows Task Scheduler'),

    # ── Data Inputs (5 nodes, y = 17 / 13.5 / 10 / 6.5 / 3) ────────────────
    _n('in_aqua', 'inputs', 17.0, 'AQUA Server', 'AMR  /  GAR',
       '<b>AQUA Server  —  Manufacturing Data Source</b><br>'
       '<b>AMR:</b> FMSAPP3301.amr.corp.intel.com<br>'
       '<b>GAR:</b> PGSAPP3301.gar.corp.intel.com<br>'
       '<b>Client:</b> AquaCmdLine.exe<br><br>'
       '<b>Args:</b> -aquaserver  -reportPath  -outputFilename<br>'
       '<b>Output:</b> raw lot / wafer test CSV or .csv.gz<br><br>'
       '<b>Default lots:</b> NCXSDJXL0H61* (NVL sort)<br>'
       '<b>Lookback:</b> configurable (default 7 days)<br>'
       '<b>Optional:</b> skipped if DataCSV provided directly'),

    _n('in_csv', 'inputs', 13.5, 'Data CSV', '.csv / .gz / .zip / .7z',
       '<b>Data CSV  —  Primary Input</b><br>'
       '<b>Formats:</b> .csv  .csv.gz  .zip  .7z<br>'
       '<b>Multi-CSV:</b> multiple files merged row-wise before processing<br><br>'
       '<b>Key columns (auto-detected):</b><br>'
       '&nbsp;• SORT_LOT / Lot / LOTFROMFS<br>'
       '&nbsp;• SORT_WAFER / Wafer_ID<br>'
       '&nbsp;• INTERFACE_BIN_* (pass / fail gate)<br>'
       '&nbsp;• FUNCTIONAL_BIN_* (root cause bin)<br>'
       '&nbsp;• Program Name / ProgramName<br>'
       '&nbsp;• LOTS End Date Time / Start_Date_Time<br><br>'
       '<b>Dedup:</b> by lot + wafer + program after merge'),

    _n('in_bindef', 'inputs', 10.0, 'BinDefinitions', '.bdefs  /  TP folder',
       '<b>BinDefinitions  —  Bin Spec</b><br>'
       '<b>Source:</b> TestProgram folder<br>'
       '<b>Default path:</b><br>'
       '&nbsp;I:\\program\\1001\\prod\\hdmtprogs\\nvl_ncx_sds<br><br>'
       '<b>Parsed by:</b> parse_bindef_to_crystalball.py<br>'
       '<b>Output:</b> *_crystalball.csv<br><br>'
       '<b>Maps bin numbers to:</b><br>'
       '&nbsp;• Bin names and descriptions<br>'
       '&nbsp;• Pass / fail classification<br>'
       '&nbsp;• Functional vs Interface bin type<br>'
       '<b>Used by:</b> Steps 4 and 5 of pipeline'),

    _n('in_cfg', 'inputs', 6.5, 'Product Config JSON', 'Fail Bucket Groups',
       '<b>Product Config JSON  —  Fail Bucket Definitions</b><br>'
       '<b>Locations (priority order):</b><br>'
       '&nbsp;• shared/setup/config/yield-dashboard/<br>'
       '&nbsp;• shared/spec/collateral/yield/<br><br>'
       '<b>Purpose:</b> Groups functional bins into<br>'
       'high-level failure buckets for Pareto analysis<br><br>'
       '<b>Example buckets:</b><br>'
       '&nbsp;• SCAN / DFT failures<br>'
       '&nbsp;• MBIST / memory failures<br>'
       '&nbsp;• Analog / IDDQ / Leakage failures<br><br>'
       '<b>Drives:</b> Step 5 DD Update + Pareto bucketing'),

    _n('in_json', 'inputs', 3.0, 'Input Config JSON', 'pipeline config  (.json)',
       '<b>Input Config JSON  —  Pipeline Config File</b><br>'
       '<b>Load via:</b> GUI "Load JSON" button or --input CLI arg<br>'
       '<b>Save via:</b> GUI "Save JSON" button<br><br>'
       '<b>Key fields:</b><br>'
       '&nbsp;• dashboard: path to Dashboard.html<br>'
       '&nbsp;• output_folder: results root path<br>'
       '&nbsp;• identifier: TP / run label<br>'
       '&nbsp;• DataCSV: single path or list of paths<br>'
       '&nbsp;• aquaserver: "AMR" or "GAR"<br>'
       '&nbsp;• reportPath: AQUA report identifier<br>'
       '&nbsp;• TestProgram / TestProgram_folder<br>'
       '&nbsp;• product_config_json: bucket config path<br>'
       '&nbsp;• sicc_run: true / false<br><br>'
       '<b>Env override:</b> APP_YIELD_NVL_ROOT sets repo root'),

    # ── Pipeline Steps (6 nodes, y = 17 / 14 / 11 / 8 / 5 / 2) ─────────────
    _n('p1', 'pipeline', 17.0, 'Step 1', 'AQUA Fetch  (optional)',
       '<b>Step 1:  AQUA Data Fetch  (Optional)</b><br>'
       '<b>Status:</b> Skipped if DataCSV is provided directly<br>'
       '<b>Client:</b> AquaCmdLine.exe (Windows shell exec)<br>'
       '<b>Args:</b> -aquaserver  -reportPath  -outputFilename<br><br>'
       '<b>Output:</b> .csv or .csv.gz saved to output_folder<br>'
       '<b>Non-zero return code:</b> pipeline aborts immediately<br>'
       '<b>File not found:</b> returns exit code 3<br><br>'
       '<b>Timing:</b> logged as [total | Δ] in output panel<br>'
       '<b>On success:</b> path forwarded to Step 2 merge'),

    _n('p2', 'pipeline', 14.0, 'Step 2', 'CSV Merge  (multi-file)',
       '<b>Step 2:  Multi-CSV Merge</b><br>'
       '<b>Triggered when:</b> DataCSV is a list (multiple files)<br>'
       '<b>Library:</b> pandas concat (dtype=object, low_memory=False)<br><br>'
       '<b>Process:</b><br>'
       '&nbsp;1. Read each file (.csv / .gz / .zip / .7z)<br>'
       '&nbsp;2. Concatenate row-wise<br>'
       '&nbsp;3. Deduplicate by lot + wafer + program columns<br>'
       '&nbsp;4. Write merged result to temp file<br><br>'
       '<b>First CSV = primary</b> — sets column order<br>'
       '<b>Single CSV:</b> step is effectively a no-op passthrough'),

    _n('p3', 'pipeline', 11.0, 'Step 3', 'Clean Output Folder',
       '<b>Step 3:  Clean Output Subfolder</b><br>'
       '<b>Purpose:</b> Remove stale artifacts from prior runs<br>'
       '<b>Library:</b> shutil.rmtree()<br><br>'
       '<b>Target path:</b> output_folder / safe_identifier /<br>'
       '&nbsp;(safe_id = alphanumeric + [-_.]; other chars → _)<br><br>'
       '<b>Multi-TP runs:</b> only the first TP name used for folder<br>'
       '<b>Failure:</b> logs [warn] and continues — not fatal<br>'
       '<b>After clean:</b> fresh empty subfolder ready for artifacts'),

    _n('p4', 'pipeline', 8.0, 'Step 4', 'Parse BinDefs  →  CrystalBall CSV',
       '<b>Step 4:  Parse BinDefinitions</b><br>'
       '<b>Script:</b> parse_bindef_to_crystalball.py<br>'
       '<b>Dispatch:</b> via _loader.py (resolves .pyd modules)<br><br>'
       '<b>Input:</b> .bdefs file (auto-located in TP folder)<br>'
       '<b>Output:</b> *_crystalball.csv in output subfolder<br><br>'
       '<b>Dev mode:</b> subprocess → _loader.py → script<br>'
       '<b>Frozen mode:</b> imports compiled module directly<br><br>'
       '<b>Provides:</b> bin number → name / category mapping<br>'
       '<b>Required by:</b> Step 5 DD Update + all HTML generators'),

    _n('p5', 'pipeline', 5.0, 'Step 5', 'DD Update  (append cols B / C)',
       '<b>Step 5:  Digital Dashboard Update</b><br>'
       '<b>Script:</b> get_dd_update.py via _loader.py<br><br>'
       '<b>Inputs:</b><br>'
       '&nbsp;• Data CSV (raw sort data from Steps 1–2)<br>'
       '&nbsp;• CrystalBall CSV (from Step 4)<br>'
       '&nbsp;• Dashboard.html (existing run index)<br>'
       '&nbsp;• Product Config JSON (bucket definitions)<br><br>'
       '<b>Appended columns to data CSV:</b><br>'
       '&nbsp;• Column B = bin category name<br>'
       '&nbsp;• Column C = fail bucket assignment<br><br>'
       '<b>Side output:</b> digital_dashboard.html<br>'
       '&nbsp;bin-category breakdown table with interactive JS rows'),

    _n('p6', 'pipeline', 2.0, 'Step 6', 'Main Analysis Run',
       '<b>Step 6:  Main Analysis Pipeline</b><br>'
       '<b>Orchestrator:</b> PipelineRunnerMixin (_pipeline_runner.py)<br>'
       '<b>Execution:</b> background thread (GUI stays live)<br>'
       '<b>Output queue:</b> log lines streamed to GUI panel<br>'
       '&nbsp;polled every 200 ms via tk.after()<br><br>'
       '<b>Dispatches to all processing modules:</b><br>'
       '&nbsp;• bin_distribution_html.py<br>'
       '&nbsp;• generate_bin_wafer_heatmaps.py<br>'
       '&nbsp;• _pipeline_html.py (pareto + master HTML)<br>'
       '&nbsp;• sort-parametric/ runner<br>'
       '&nbsp;• SICC UPM / SICC CDYN (if sicc_run=True)<br><br>'
       '<b>Timing:</b> [total elapsed | Δ since last section] per step<br>'
       '<b>On complete:</b> Dashboard.html updated, report opened'),

    # ── Processing Modules (5 nodes, y = 17 / 13.5 / 10 / 6.5 / 3) ─────────
    _n('m_bd', 'processing', 17.0, 'bin_distribution_html.py', 'Bin Distribution Table',
       '<b>bin_distribution_html.py  —  Bin Distribution</b><br>'
       '<b>Inputs:</b> enriched data CSV (with cols B / C) + bindef CSV<br><br>'
       '<b>Analysis:</b><br>'
       '&nbsp;• Per-lot and per-wafer bin counts<br>'
       '&nbsp;• INTERFACE_BIN_* pass / fail gate breakdown<br>'
       '&nbsp;• FUNCTIONAL_BIN_* root cause distribution<br>'
       '&nbsp;• Sorted by fail rate, color-coded by severity<br><br>'
       '<b>Output:</b> *_BinDistribution.html<br>'
       '<b>Embedded in output:</b><br>'
       '&nbsp;• Top-10 pareto chart (from _pipeline_html.py)<br>'
       '&nbsp;• Digital dashboard section (from get_dd_update)<br>'
       '&nbsp;• Links to wafermap.html per lot (fbTileClick JS)'),

    _n('m_wm', 'processing', 13.5, 'generate_bin_wafer_heatmaps.py', 'Wafer Spatial Heatmaps',
       '<b>generate_bin_wafer_heatmaps.py  —  Wafer Heatmaps</b><br>'
       '<b>Purpose:</b> Spatial die-level wafer heatmaps per IBIN<br><br>'
       '<b>Per lot, per IBIN bin:</b><br>'
       '&nbsp;• Die X / Y coordinates → 2D wafer grid<br>'
       '&nbsp;• Color = IBIN bin category<br>'
       '&nbsp;• One standalone HTML per lot<br><br>'
       '<b>Output:</b> heatmap/*_IBIN_WaferMap_LOT.html<br>'
       '&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;+ combined wafermap.html index<br><br>'
       '<b>Lot column priority:</b> SORT_LOT → lot → *lot* (not *slot*)<br>'
       '<b>JS navigation:</b> fbTileClick() → per-lot file + #wafer-W anchor<br>'
       '<b>Per-lot URL map:</b> _wm_files_dict{lot → html path}'),

    _n('m_pa', 'processing', 10.0, '_pipeline_html.py', 'Pareto Heatmap Builder',
       '<b>_pipeline_html.py  —  PipelineHtmlMixin</b><br>'
       '<b>Methods:</b> _build_pareto_html, _build_master_html,<br>'
       '&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;_update_dashboard_html<br><br>'
       '<b>Pareto filter:</b> FUNCTIONAL_BIN_* rows where<br>'
       '&nbsp;&nbsp;INTERFACE_BIN_* > 4  (fail gate was crossed)<br><br>'
       '<b>Libraries:</b> matplotlib (Agg backend), pandas, numpy<br>'
       '<b>Chart:</b> matplotlib figure → base64 PNG → embedded in HTML<br><br>'
       '<b>Generates:</b><br>'
       '&nbsp;• Top-10 fail pareto bar chart<br>'
       '&nbsp;• Pareto table injected into BinDistribution HTML<br>'
       '&nbsp;• pareto_heatmap.html (standalone)<br>'
       '&nbsp;• Master Dashboard HTML section (_build_master_html)<br>'
       '&nbsp;• Dashboard.html updated (_update_dashboard_html)'),

    _n('m_pr', 'processing', 6.5, 'sort-parametric/', 'Parametric Analysis',
       '<b>sort-parametric/  —  Parametric Analysis Module</b><br>'
       '<b>Files:</b><br>'
       '&nbsp;• parametric_runner.py  — orchestrator<br>'
       '&nbsp;• parametric_html.py   — HTML report generator<br>'
       '&nbsp;• pcmprog_html.py      — PCM program HTML<br><br>'
       '<b>Purpose:</b> Analyze continuous parametric measurements<br>'
       'from sort test program execution<br><br>'
       '<b>Features:</b><br>'
       '&nbsp;• Distribution plots per parameter group<br>'
       '&nbsp;• Pass / fail summary by parameter<br>'
       '&nbsp;• Driven by PCM Setup JSON pattern definitions<br><br>'
       '<b>Output:</b> parametric HTML files in per-run subfolder'),

    _n('m_si', 'processing', 3.0, 'SICC UPM  /  SICC CDYN', 'Silicon Characterization',
       '<b>SICC Analysis Modules</b><br>'
       '<b>Enabled when:</b> sicc_run = True in Input Config JSON<br><br>'
       '<b>SICC UPM:</b><br>'
       '&nbsp;sicc_upm/src/run_dashboard.py<br>'
       '&nbsp;Unit Power Management analysis<br><br>'
       '<b>SICC CDYN UPM:</b><br>'
       '&nbsp;sicc_cdyn_upm/src/run_dashboard.py<br>'
       '&nbsp;Dynamic Capacitance analysis<br><br>'
       '<b>Execution:</b> separate subprocess launched by Step 6<br>'
       '<b>Path:</b> resolved from _ROOT_DIR constant<br>'
       '<b>Output:</b> separate SICC HTML dashboards in run subfolder'),

    # ── Outputs (5 nodes, y = 17 / 13.5 / 10 / 6.5 / 3) ────────────────────
    _n('o_dash', 'outputs', 17.0, 'Dashboard.html', 'Master Run Index',
       '<b>Dashboard.html  —  Master Run Index</b><br>'
       '<b>Role:</b> Central hub linking all pipeline run reports<br>'
       '<b>Updated by:</b> _update_dashboard_html() after every run<br><br>'
       '<b>Each run row contains:</b><br>'
       '&nbsp;• Run identifier / test program label<br>'
       '&nbsp;• Run timestamp<br>'
       '&nbsp;• Overall yield % summary<br>'
       '&nbsp;• Hyperlink → BinDistribution.html<br>'
       '&nbsp;• Lot count / wafer count<br><br>'
       '<b>Managed by:</b> ManageFrame (reorder / delete runs)<br>'
       '<b>Read by:</b> yield_report.py, trend_chart.py for trending<br>'
       '<b>Served by:</b> local HTTP server + SharePoint upload'),

    _n('o_bf', 'outputs', 13.5, '*_BinDistribution.html', 'Detailed Bin Analysis',
       '<b>BinDistribution HTML  —  Primary Run Artifact</b><br>'
       '<b>Linked from:</b> Dashboard.html per-run row<br><br>'
       '<b>Sections:</b><br>'
       '&nbsp;• Bin distribution table (sortable columns)<br>'
       '&nbsp;• Interface bin pass / fail counts per lot<br>'
       '&nbsp;• Functional bin root cause breakdown<br>'
       '&nbsp;• Top-10 fail pareto chart (embedded base64 PNG)<br>'
       '&nbsp;• Digital dashboard section (DD HTML embed)<br>'
       '&nbsp;• Links to wafermap.html per lot (fbTileClick JS)<br>'
       '&nbsp;• Lot / wafer summary statistics table<br><br>'
       '<b>Watermark:</b> "Pant, Sujit N — GEMS FTE"<br>'
       '&nbsp;fixed-position div injected by WM_HTML constant'),

    _n('o_wm', 'outputs', 10.0, 'wafermap.html', 'Spatial Wafer Maps',
       '<b>Wafer Map HTML  —  Spatial Die Heatmaps</b><br>'
       '<b>Location:</b> heatmap/ subfolder within run folder<br>'
       '<b>Naming:</b> stem_IBIN_WaferMap_LOT.html<br><br>'
       '<b>One file per lot</b> + combined wafermap.html index<br><br>'
       '<b>Auto-detected:</b> _wm_url set to "wafermap.html" if<br>'
       '&nbsp;heatmap/*.html exists in output subfolder<br><br>'
       '<b>Cross-links:</b><br>'
       '&nbsp;• Linked from Pareto + BinDistribution pages<br>'
       '&nbsp;• fbTileClick navigates with #wafer-W anchor<br>'
       '<b>Per-lot map:</b> _wm_files_dict built from SORT_LOT column'),

    _n('o_pd', 'outputs', 6.5, 'pareto_heatmap.html', 'digital_dashboard.html',
       '<b>Pareto Heatmap  +  Digital Dashboard</b><br><br>'
       '<b>pareto_heatmap.html:</b><br>'
       '&nbsp;• Standalone top-N fail pareto report<br>'
       '&nbsp;• Ranked fail bar chart + color heat table<br>'
       '&nbsp;• matplotlib figure → base64 PNG embedded<br>'
       '&nbsp;• Filter: FUNCTIONAL_BIN_* where IBIN > 4<br><br>'
       '<b>digital_dashboard.html:</b><br>'
       '&nbsp;• Bin-category breakdown (get_dd_update output)<br>'
       '&nbsp;• Column B = bin category name<br>'
       '&nbsp;• Column C = fail bucket assignment<br>'
       '&nbsp;• Digital / Analog / Leakage / Structural split<br>'
       '&nbsp;• Interactive DD JS rows / headers<br><br>'
       '<b>Both embedded / linked from BinDistribution HTML</b>'),

    _n('o_rp', 'outputs', 3.0, 'yield-report.html', 'trend-report.html',
       '<b>Yield Report  +  Trend Report</b><br><br>'
       '<b>yield-report.html:</b><br>'
       '&nbsp;• Weekly yield pareto summary<br>'
       '&nbsp;• Groups runs by ISO calendar week<br>'
       '&nbsp;• Bin-fail pareto chart per week<br>'
       '&nbsp;• Generated by yield_report.py<br>'
       '&nbsp;• Input: Dashboard.html run history<br><br>'
       '<b>trend-report.html:</b><br>'
       '&nbsp;• Rolling trend charts over multiple weeks<br>'
       '&nbsp;• Yield % and top-bin count trends per TP<br>'
       '&nbsp;• Generated by manage_trend.py / trend_chart.py<br>'
       '&nbsp;• Configurable look-back window<br><br>'
       '<b>Both:</b> standalone HTML, embedded charts, watermarked'),
]
N_NODES = len(NODES)

# ─────────────────────────────────────────────────────────────────────────────
# EDGES  (src_id, dst_id, color, dash)
# ─────────────────────────────────────────────────────────────────────────────
EDGES = [
    # Entry → Pipeline
    ('e_gui',   'p1', '#2980b9', 'solid'),
    ('e_cli',   'p1', '#2980b9', 'solid'),
    ('e_auto',  'p1', '#2980b9', 'solid'),

    # Data Inputs → Pipeline steps
    ('in_aqua',   'p1', '#e67e22', 'dash'),
    ('in_csv',    'p2', '#e67e22', 'dash'),
    ('in_bindef', 'p4', '#e67e22', 'dash'),
    ('in_cfg',    'p5', '#e67e22', 'dash'),
    ('in_json',   'p1', '#e67e22', 'dash'),

    # Pipeline sequential
    ('p1', 'p2', '#e74c3c', 'solid'),
    ('p2', 'p3', '#e74c3c', 'solid'),
    ('p3', 'p4', '#e74c3c', 'solid'),
    ('p4', 'p5', '#e74c3c', 'solid'),
    ('p5', 'p6', '#e74c3c', 'solid'),

    # Step 6 → Processing
    ('p6', 'm_bd', '#27ae60', 'solid'),
    ('p6', 'm_wm', '#27ae60', 'solid'),
    ('p6', 'm_pa', '#27ae60', 'solid'),
    ('p6', 'm_pr', '#27ae60', 'solid'),
    ('p6', 'm_si', '#27ae60', 'solid'),

    # Processing → Outputs
    ('m_bd', 'o_bf',   '#f1c40f', 'solid'),
    ('m_wm', 'o_wm',   '#f1c40f', 'solid'),
    ('m_pa', 'o_pd',   '#f1c40f', 'solid'),
    ('m_pa', 'o_dash', '#f1c40f', 'solid'),
    ('m_pr', 'o_rp',   '#f1c40f', 'solid'),
    ('m_si', 'o_bf',   '#f1c40f', 'dash'),

    # Cross-links in outputs
    ('o_wm', 'o_bf',   '#95a5a6', 'dash'),
    ('o_pd', 'o_bf',   '#95a5a6', 'dash'),
    ('o_bf', 'o_dash', '#95a5a6', 'dash'),
]
N_EDGES = len(EDGES)

# ─────────────────────────────────────────────────────────────────────────────
# BUILD FIGURE
# ─────────────────────────────────────────────────────────────────────────────
def build_figure():
    node_map = {n['id']: (i, n) for i, n in enumerate(NODES)}
    shapes   = []
    annots   = []
    traces   = []

    # ── Swimlane bands  (shapes[0..N_BANDS-1]) ───────────────────────────────
    for i, (stage, x0, x1, lbl) in enumerate(BANDS):
        fc, bc = BAND_COLORS[stage]
        opacity = 0.10 if i % 2 == 0 else 0.05
        shapes.append(dict(
            type='rect', x0=x0, x1=x1, y0=Y_MIN - 0.3, y1=Y_MAX + 0.4,
            fillcolor=bc, opacity=opacity,
            line=dict(color=bc, width=1, dash='dot'),
            layer='below',
        ))
        annots.append(dict(
            x=(x0 + x1) / 2, y=Y_MAX + 0.25,
            text=f'<b>{lbl}</b>',
            showarrow=False,
            font=dict(size=13, color=bc, family='Arial Black, Arial'),
            xanchor='center', yanchor='bottom',
            bgcolor='rgba(0,0,0,0.65)', borderpad=4,
        ))
    assert len(shapes) == N_BANDS   # sanity

    # ── Node rectangles  (shapes[N_BANDS..N_BANDS+N_NODES-1]) ───────────────
    for n in NODES:
        cx, cy = n['x'], n['y']
        shapes.append(dict(
            type='rect',
            x0=cx - NW, x1=cx + NW,
            y0=cy - NH, y1=cy + NH,
            fillcolor=n['fill'],
            opacity=0.95,
            line=dict(color=n['border'], width=2.5),
            layer='above',
        ))
        annots.append(dict(
            x=cx, y=cy,
            text=(f"<b>{n['l1']}</b><br>"
                  f"<span style='font-size:12px;color:#bdc3c7'>{n['l2']}</span>"),
            showarrow=False,
            font=dict(size=14, color='#ecf0f1', family='Arial'),
            xanchor='center', yanchor='middle', align='center',
        ))
    assert len(shapes) == N_BANDS + N_NODES

    # ── Edge traces  (trace[0..N_EDGES-1]) ───────────────────────────────────
    DASH_MAP = {'solid': None, 'dash': 'dash'}
    for edge in EDGES:
        src_id, dst_id, color, dash = edge
        si, sn = node_map[src_id]
        di, dn = node_map[dst_id]
        sx, sy = sn['x'] + NW, sn['y']
        dx, dy = dn['x'] - NW, dn['y']
        mid = (sx + dx) / 2
        traces.append(go.Scatter(
            x=[sx, mid, mid, dx],
            y=[sy,  sy,  dy, dy],
            mode='lines',
            line=dict(color=color, width=2.0,
                      dash=DASH_MAP.get(dash), shape='spline'),
            hoverinfo='none',
            showlegend=False,
            opacity=1.0,
        ))
        # arrowhead
        annots.append(dict(
            x=dx, y=dy, ax=dx - 0.9, ay=dy,
            xref='x', yref='y', axref='x', ayref='y',
            showarrow=True,
            arrowhead=3, arrowsize=1.0, arrowwidth=2.0,
            arrowcolor=color, text='',
        ))
    assert len(traces) == N_EDGES

    # ── Invisible click-detection scatter  (trace[N_EDGES]) ──────────────────
    hover_x  = [n['x'] for n in NODES]
    hover_y  = [n['y'] for n in NODES]
    hover_txt = [n['hover'] for n in NODES]
    hover_cd  = [i for i in range(N_NODES)]   # customdata = node index

    traces.append(go.Scatter(
        x=hover_x, y=hover_y,
        mode='markers',
        marker=dict(size=38, color='rgba(0,0,0,0)', line=dict(width=0)),
        customdata=hover_cd,
        hoverinfo='text',
        hovertext=hover_txt,
        hoverlabel=dict(
            bgcolor='#1a252f', bordercolor='#3498db',
            font=dict(color='#ecf0f1', size=26, family='Consolas, monospace'),
            namelength=0,
        ),
        showlegend=False,
        name='_nodes',
    ))

    # ── Chart title ───────────────────────────────────────────────────────────
    annots.append(dict(
        x=28, y=Y_MAX + 1.9,
        text='<b>Yield Analysis Dashboard  —  Pipeline Tab Architecture</b>',
        showarrow=False,
        font=dict(size=22, color='#3498db', family='Arial Black, Arial'),
        xanchor='center', yanchor='bottom',
    ))
    annots.append(dict(
        x=28, y=Y_MAX + 1.1,
        text='Click any node to highlight its connections  •  Click again to reset  •  Hover for details',
        showarrow=False,
        font=dict(size=12, color='#7f8c8d', family='Arial'),
        xanchor='center', yanchor='bottom',
    ))

    fig = go.Figure(data=traces)
    fig.update_layout(
        paper_bgcolor=BG, plot_bgcolor=BG,
        shapes=shapes, annotations=annots,
        autosize=True,
        xaxis=dict(range=[-8, 64], showgrid=False, zeroline=False,
                   showticklabels=False, fixedrange=False),
        yaxis=dict(range=[Y_MIN - 1.2, Y_MAX + 3.0],
                   showgrid=False, zeroline=False,
                   showticklabels=False, fixedrange=False),
        margin=dict(l=5, r=5, t=5, b=5),
        dragmode='pan',
        hovermode='closest',
        hoverdistance=50,
        showlegend=False,
    )
    return fig

# ─────────────────────────────────────────────────────────────────────────────
# BUILD CHART DATA for JS (node + edge metadata)
# ─────────────────────────────────────────────────────────────────────────────
def build_chart_data():
    node_map = {n['id']: i for i, n in enumerate(NODES)}

    js_nodes = []
    for i, n in enumerate(NODES):
        js_nodes.append({
            'id':        n['id'],
            'shapeIdx':  N_BANDS + i,          # index into layout.shapes
            'origFill':  n['fill'],
            'origBorder': n['border'],
            'label':     n['l1'],
        })

    js_edges = []
    for t_idx, (src_id, dst_id, color, dash) in enumerate(EDGES):
        js_edges.append({
            'srcId':     src_id,
            'dstId':     dst_id,
            'traceIdx':  t_idx,               # index into figure.data
            'origColor': color,
        })

    return {'nodes': js_nodes, 'edges': js_edges,
            'nodeTraceIdx': N_EDGES,           # the invisible hover scatter
            'nBands': N_BANDS}

# ─────────────────────────────────────────────────────────────────────────────
# HIGHLIGHT JAVASCRIPT
# ─────────────────────────────────────────────────────────────────────────────
def build_js(chart_data_json: str) -> str:
    return f"""
<script>
(function() {{
  var DIV = 'chart';
  var DATA = {chart_data_json};

  /* adjacency map: nodeId -> Set of connected nodeIds */
  var adj = {{}};
  DATA.nodes.forEach(function(n) {{ adj[n.id] = []; }});
  DATA.edges.forEach(function(e) {{
    adj[e.srcId].push(e.dstId);
    adj[e.dstId].push(e.srcId);
  }});

  var highlighted = null;   /* currently highlighted node id, or null */

  /* ── Reset all nodes and edges to original colours ──────────────────── */
  function resetAll() {{
    highlighted = null;
    var shapeUpdates = {{}};
    DATA.nodes.forEach(function(n) {{
      shapeUpdates['shapes[' + n.shapeIdx + '].fillcolor'] = n.origFill;
      shapeUpdates['shapes[' + n.shapeIdx + '].line.color'] = n.origBorder;
      shapeUpdates['shapes[' + n.shapeIdx + '].line.width'] = 2.5;
      shapeUpdates['shapes[' + n.shapeIdx + '].opacity'] = 0.95;
    }});
    Plotly.relayout(DIV, shapeUpdates);

    var connIdx = DATA.edges.map(function(e) {{ return e.traceIdx; }});
    DATA.edges.forEach(function(e) {{
      Plotly.restyle(DIV, {{'line.color': e.origColor, opacity: 1.0}}, [e.traceIdx]);
    }});
  }}

  /* ── Highlight one node + its direct neighbours ──────────────────────── */
  function highlightNode(nodeId) {{
    highlighted = nodeId;
    var connected = {{}};
    connected[nodeId] = true;
    (adj[nodeId] || []).forEach(function(nid) {{ connected[nid] = true; }});

    var shapeUpdates = {{}};
    DATA.nodes.forEach(function(n) {{
      var si = n.shapeIdx;
      if (connected[n.id]) {{
        /* highlighted: original colour + bright white border glow */
        shapeUpdates['shapes[' + si + '].fillcolor']   = n.origFill;
        shapeUpdates['shapes[' + si + '].line.color']  = '#ffffff';
        shapeUpdates['shapes[' + si + '].line.width']  = 3.5;
        shapeUpdates['shapes[' + si + '].opacity']     = 1.0;
      }} else {{
        /* dimmed */
        shapeUpdates['shapes[' + si + '].fillcolor']   = '#0d1b24';
        shapeUpdates['shapes[' + si + '].line.color']  = '#1e2e3d';
        shapeUpdates['shapes[' + si + '].line.width']  = 1.0;
        shapeUpdates['shapes[' + si + '].opacity']     = 0.25;
      }}
    }});
    Plotly.relayout(DIV, shapeUpdates);

    /* Split edges into connected / disconnected groups, then batch restyle */
    var connEdges = [], dimEdges = [];
    DATA.edges.forEach(function(e) {{
      if (e.srcId === nodeId || e.dstId === nodeId) {{
        connEdges.push(e.traceIdx);
      }} else {{
        dimEdges.push(e.traceIdx);
      }}
    }});
    if (connEdges.length)
      Plotly.restyle(DIV, {{'line.color': '#ffffff', opacity: 1.0}}, connEdges);
    if (dimEdges.length)
      Plotly.restyle(DIV, {{'line.color': '#0d1b24', opacity: 0.05}}, dimEdges);
  }}

  /* ── Wire up Plotly events after DOM ready ───────────────────────────── */
  function wireEvents() {{
    var el = document.getElementById(DIV);
    if (!el || !el.on) {{
      setTimeout(wireEvents, 150);
      return;
    }}

    el.on('plotly_click', function(data) {{
      var pt = data.points[0];
      /* only react to clicks on the invisible node scatter */
      if (pt.data.name !== '_nodes') return;
      var nodeIdx = pt.customdata;
      var nodeId  = DATA.nodes[nodeIdx].id;
      if (highlighted === nodeId) {{
        resetAll();          /* click same node again → reset */
      }} else {{
        highlightNode(nodeId);
      }}
    }});

    el.on('plotly_doubleclick', function() {{
      resetAll();
    }});

    /* click on empty plot area → reset */
    el.addEventListener('click', function(evt) {{
      /* Plotly fires plotly_click for point clicks; a raw click that
         did NOT produce a plotly_click means the background was clicked */
    }});
  }}
  wireEvents();
}})();
</script>
"""

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    out_path = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'flowchart.html')

    fig = build_figure()
    chart_data = build_chart_data()
    chart_data_json = json.dumps(chart_data)

    inner = fig.to_html(
        include_plotlyjs='cdn',
        full_html=False,
        div_id='chart',
        config={
            'scrollZoom': True,
            'displayModeBar': True,
            'modeBarButtonsToRemove': ['select2d', 'lasso2d'],
            'displaylogo': False,
            'responsive': True,
            'toImageButtonOptions': {
                'format': 'png',
                'filename': 'yield_pipeline_flowchart',
                'height': 1200,
                'width': 3600,
                'scale': 2,
            },
        },
    )

    highlight_js = build_js(chart_data_json)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Yield Dashboard \u2014 Pipeline Architecture</title>
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    html, body {{ width:100%; height:100%; background:{BG}; overflow:hidden; }}
    #chart {{ width:100vw; height:100vh; }}
  </style>
</head>
<body>
{inner}
{highlight_js}
<script>
  /* full-window resize */
  (function(){{
    function resize(){{
      Plotly.relayout('chart',{{width:window.innerWidth,height:window.innerHeight}});
    }}
    window.addEventListener('resize', resize);
    var t=setInterval(function(){{
      if(document.getElementById('chart')&&window.Plotly){{resize();clearInterval(t);}}
    }},100);
  }})();
</script>
</body>
</html>"""

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'Flowchart saved: {out_path}')
    import webbrowser
    webbrowser.open(f'file:///{out_path.replace(os.sep, "/")}')


if __name__ == '__main__':
    main()
