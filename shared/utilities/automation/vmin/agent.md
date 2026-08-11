# VMIN Dashboard Automation

Automates the NVL816-BLLC VMIN dashboard pipeline end-to-end.

## What it does

1. **AQUA pull** — calls `AquaCmdLine.exe` with `shared/setup/aqua/vmin/NVL_Sort_VMIN - Dashboard.txt`
2. **Split** — splits AQUA CSV by (TestProgram, Operation) → per-TP `.csv.gz` snapshots; column names are preserved exactly as returned by AQUA (no suffix stripping)
3. **Distribute** — raw AQUA rows grouped by program letter (0H61C, 0H61D) and written as dated snapshots: `data/programs/0H61C/raw_<ts>.7z`
4. **Change-detect** — per-TP caches written to per-program subfolders (`data/programs/0H61C/<tp_key>.csv.gz`); only TPs with new lot/wafer data re-run (or `--force`)
5. **Snapshot cleanup** — if a program letter has no changed TPs, its `raw_<ts>.7z` is deleted to avoid wasted disk space
6. **run_vmin** — calls `code/dashboard/vmin-dashboard/src/py/run_vmin.py --headless --json` for each changed TP, grouped by program letter
7. **Email** — sends HTML report + attachments via Outlook COM or Intel SMTP

## Files

| File | Purpose |
|------|---------|
| `src/run_automation.py` | Headless pipeline — AQUA pull, distribute, split, VMIN run, email |
| `src/manage_automation.py` | Tkinter GUI — email config, run history, data files, schedule |

## Output layout (samba share)

```
\\samba.../auto/vmin/
  data/
    NVL_VMIN_<ts>.csv.gz            (raw AQUA pull — combined)
    programs/
      0H61C/
        raw_<ts>.7z                 (dated raw snapshot; deleted if no changes)
        <tp_key>.csv.gz             (per-TP rolling cache for change-detection)
      0H61D/
        raw_<ts>.7z
        <tp_key>.csv.gz
  output/
    NVL_VMIN_0H61C_<YYYYMMDD_HHMMSS>/   (one folder per changed program letter)
      <tp_key>/
        vmin_dashboard.html         (VMIN dashboard output — linked from GUI)
        input.json                  (run_vmin.py input)
        _vmin_manifest.json         (run_vmin.py output manifest)
        _jmp_log.txt                (JMP log)
        *.jmpprj                    (JMP project)
      report.html                   (run summary for this program)
    NVL_VMIN_0H61D_<YYYYMMDD_HHMMSS>/
      ...
  run_log.html                      (cumulative history; one entry per program per run)
```

## Step 2: Distribute to per-program folders

- Raw rows split by program letter (`0H61C`, `0H61D`, …)
- Each letter's rows written as: `data/programs/0H61X/raw_<ts>.csv.gz` → compressed to `.7z`
- Combined raw file kept in `data/` (not deleted)

## Step 3: Per-TP rolling cache in letter subfolders

- `data/programs/0H61C/<tp_key>.csv.gz`
- `_prog_group(key)` extracts the letter from the TP key via `re.search(r'0H61([A-Za-z])', key)`
- Backward compat: on first run after migration, reads old flat `data/programs/<tp_key>.csv.gz` then migrates to subfolder; old flat file is deleted

## Step 4: Run per changed TP, grouped by letter

- `output/NVL_VMIN_0H61C_<ts>/`  ← one folder per changed program letter
- `output/NVL_VMIN_0H61D_<ts>/`
- Per-program `report.html` and `run_log.html` entry written separately

## Usage

```powershell
# Full run
C:\scripts\.venv\Scripts\python.exe run_automation.py

# Dry-run (no exec)
C:\scripts\.venv\Scripts\python.exe run_automation.py --dry-run

# Force re-run all TPs regardless of data change
C:\scripts\.venv\Scripts\python.exe run_automation.py --force

# Use a local CSV file instead of pulling from AQUA
C:\scripts\.venv\Scripts\python.exe run_automation.py --local-csv "C:\path\to\file.csv"

# Filter to specific TP keys (comma-separated substrings)
C:\scripts\.venv\Scripts\python.exe run_automation.py --keys "0H61C,119325"

# Launch GUI manager
C:\scripts\.venv\Scripts\python.exe manage_automation.py
```

## Configuration

- **Email config**: `shared/setup/automation/vmin-dashboard/email_config.json`
- **AQUA config**: `shared/setup/aqua/vmin/NVL_Sort_VMIN - Dashboard.txt`
- **Scheduled task**: "NVL-BLLC VMIN Automation" (manage via GUI → Schedule tab)

## GUI features (manage_automation.py)

- **Email & Filter tab** — email recipients, program filter (per-TP key checkboxes with All On/Off/Reload)
- **Run History tab** — lists `NVL_VMIN_*` output folders; columns: Tag, Folder, Date, TPs, Size
  - `🌐 Open HTML` button / right-click → opens `<tp_key>/vmin_dashboard.html` in browser (falls back to `report.html`)
  - `📋 Copy file:// link` → copies dashboard URI(s) to clipboard
  - `✉ Send Report` → sends email with report.html attached
  - `🏷 Tag Run` → labels a run folder
- **Data Files tab** — lists per-TP `.csv.gz` snapshots
- **Schedule tab**
  - `⟳ Rerun (Cached)` — opens dialog to re-run with existing snapshots; supports `--keys` filter and `--local-csv` override; streams live stdout output

## Column name preservation

`split_by_tp_oper` keeps all AQUA column names exactly as-is (including `_{op}` suffixes like `_119325`). Earlier versions stripped these suffixes, which broke downstream JSL/Python code that referenced columns by their full hardcoded names.

## Differences from yield-dashboard-automation

| Feature | Yield | VMIN |
|---------|-------|------|
| Pipeline | `pipeline.py --json` | `run_vmin.py --headless --json` |
| Output | HTML dashboards | JMP projects + `vmin_dashboard.html` |
| Programs | 0H61A/B/C | 0H61C/D |
| Run folder | `NVL_0H61<letter>_<ts>` | `NVL_VMIN_0H61<letter>_<ts>` |
| R0 merge | Yes | No |
| Bin comparison | Yes (FF/FF+DF/repair) | No |
| Open HTML target | `report.html` | `<tp_key>/vmin_dashboard.html` |
