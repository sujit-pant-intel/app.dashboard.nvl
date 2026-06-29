# NVL N2P CLASS VF Plot JSON

Generator script for the VF overlay JSON used by the CLASS dashboard poly-fit chart.

All VF chart assets live in the `VF_Chart/` subdirectory:

```
shared/setup/class-dashboard/VF_Chart/
  generate_nvl_n2p_class_vf_plot_json.py   ← generator script
  NVL_N2P_CLASS_VF_tracker.xlsx            ← source workbook (update this)
  NVL_N2P_CLASS_VF_tracker_plot_A_to_L_grouped.json   ← generated output
```

## Inputs

- Workbook: `VF_Chart/NVL_N2P_CLASS_VF_tracker.xlsx`
- Sheets processed: `CORE`, `ATOM`
- Columns used: first 12 (`A:L`) — treated as X/Y pairs (col0=x, col1=y, col2=x, col3=y, …)

## Output

- File: `VF_Chart/<workbook_stem>_plot_A_to_L_grouped.json`
- Shape:

```json
{
  "CORE": [
    {
      "xColumn": "Frequency (GHz)",
      "label": "Series label",
      "points": [
        {"x": 1.2, "y": 0.48}
      ]
    }
  ],
  "ATOM": [
    {
      "xColumn": "Frequency (GHz).1",
      "label": "Series label",
      "points": [
        {"x": 1.2, "y": 0.46}
      ]
    }
  ]
}
```

## Run

From the `VF_Chart/` folder (script auto-detects the workbook):

```powershell
cd "C:\scripts\app.yield.nvl\shared\setup\class-dashboard\VF_Chart"
c:/scripts/.venv/Scripts/python.exe generate_nvl_n2p_class_vf_plot_json.py
```

Or with explicit paths from anywhere:

```powershell
c:/scripts/.venv/Scripts/python.exe "C:\scripts\app.yield.nvl\shared\setup\class-dashboard\VF_Chart\generate_nvl_n2p_class_vf_plot_json.py" `
  --workbook "C:\scripts\app.yield.nvl\shared\setup\class-dashboard\VF_Chart\NVL_N2P_CLASS_VF_tracker.xlsx" `
  --output   "C:\scripts\app.yield.nvl\shared\setup\class-dashboard\VF_Chart\NVL_N2P_CLASS_VF_tracker_plot_A_to_L_grouped.json"
```

## Notes

- The script finds the most-recently-modified `.xlsx` in the working directory if `--workbook` is omitted.
- NaN rows are silently skipped.
- Old flat files (`NVL_N2P_CLASS_VF_tracker_combined.csv`, `*_table.csv`, `*_plot_A_to_L.json`) have been removed; use the grouped JSON from `VF_Chart/` instead.