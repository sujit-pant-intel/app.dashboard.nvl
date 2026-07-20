"""
Create-BLLC-Material-File.py
============================
Converts an NPI-provided BLLC material wafer list (0717-format) into a
script-friendly lot-definition file compatible with the BLLC dashboard pipeline.

What it does
------------
1. Renames source columns to match the dashboard schema:
     TSMC Lot6  → TSMC_LOT
     Intel Lot7 → INTEL_LOT7
     WaferNo    → WaferID
2. Adds a Stepping column (default: L0).
3. Derives the "Material Type, Skew, BEOL Skew" column from AIO/BB,
   MG4 split, Vy CD+, and Remark fields.
4. Back-fills INTEL_LOT7 from a reference *_l1.csv where the same
   TSMC_LOT already has a known Intel lot number.
5. Re-orders columns to match the *_l1.csv layout expected by scripts.

Usage
-----
Run: python Create-BLLC-Material-File.py
A GUI will open — select Input CSV, Reference CSV, and Output CSV, then
click "Run Transform".
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
from pathlib import Path

DEFAULT_INPUT     = ""
DEFAULT_REFERENCE = ""
DEFAULT_OUTPUT    = ""


def build_material_type(row):
    aio_bb = str(row.get("AIO/BB", "")).strip()
    mg4    = str(row.get("MG4 split", "")).strip()
    vy_cd  = str(row.get("Vy CD+", "")).strip()
    remark = str(row.get("Remark", "")).strip().lower()

    if aio_bb == "AIO":
        if "speed+" in remark or "low pre" in remark:
            return "NVL816-BLLC-L0 AIO, low pre S/D HT"
        parts = ["NVL816-BLLC-L0 AIO"]
        if mg4 == "MG4+":
            parts.append("MG4+")
        elif mg4 == "MG4++":
            parts.append("MG4++")
        return ",".join(parts)

    elif aio_bb == "BB CIP":
        parts = ["NVL816-BLLC-L5 AIO+BB"]
        if vy_cd == "VyCD+":
            parts.append("VyCD+")
        if mg4 == "MG4+":
            parts.append("MG4+")
        elif mg4 == "MG4++":
            parts.append("MG4++")
        return ",".join(parts)

    return ""


def run_transform(input_path: str, ref_path: str, output_path: str) -> str:
    """Core transform logic. Returns a status message."""
    df = pd.read_csv(input_path, dtype=str).fillna("")

    # --- 1. Rename columns ---
    df = df.rename(columns={
        "TSMC Lot6": "TSMC_LOT",
        "Intel Lot7": "INTEL_LOT7",
        "WaferNo": "WaferID",
    })

    # --- 2. Build TSMC_LOT -> INTEL_LOT7 lookup from reference ---
    ref_lookup: dict[str, str] = {}
    if ref_path:
        ref = pd.read_csv(ref_path, dtype=str).fillna("")
        if "TSMC_LOT" in ref.columns and "INTEL_LOT7" in ref.columns:
            for _, row in ref[["TSMC_LOT", "INTEL_LOT7"]].dropna().iterrows():
                tsmc = str(row["TSMC_LOT"]).strip()
                intel = str(row["INTEL_LOT7"]).strip()
                if tsmc and intel and tsmc not in ref_lookup:
                    ref_lookup[tsmc] = intel

    # --- 3. Fill INTEL_LOT7 from reference where available ---
    def fill_intel_lot(row):
        current = str(row.get("INTEL_LOT7", "")).strip()
        if current:
            return current
        tsmc = str(row.get("TSMC_LOT", "")).strip()
        return ref_lookup.get(tsmc, "")

    df["INTEL_LOT7"] = df.apply(fill_intel_lot, axis=1)

    # --- 4. Add Stepping ---
    df["Stepping"] = "L0"

    # --- 5. Build Material Type column ---
    df["Material Type, Skew, BEOL Skew"] = df.apply(build_material_type, axis=1)

    # --- 6. Reorder to match l1.csv column order ---
    col_order = [
        "Lot#",
        "Material Type, Skew, BEOL Skew",
        "Stepping",
        "TSMC_LOT",
        "INTEL_LOT7",
        "WaferID",
        "TSMC WaferID",
        "Intel WaferID",
        "AIO/BB",
        "MG4 split",
        "Device Skew",
        "Vy CD+",
        "Remark",
        "inline scrap",
    ]
    extra = [c for c in df.columns if c not in col_order]
    df = df[col_order + extra]

    df.to_csv(output_path, index=False)
    return f"Done — {len(df):,} rows written to:\n{output_path}"


# ─────────────────────────────────────────────
#  GUI
# ─────────────────────────────────────────────

def build_gui():
    root = tk.Tk()
    root.title("CSV Lot Definition Transformer")
    root.resizable(False, False)

    pad = {"padx": 8, "pady": 4}

    def make_file_row(parent, label_text, default, row, filetypes, save=False):
        ttk.Label(parent, text=label_text, width=14, anchor="e").grid(row=row, column=0, **pad)
        var = tk.StringVar(value=default)
        entry = ttk.Entry(parent, textvariable=var, width=70)
        entry.grid(row=row, column=1, **pad)

        def browse():
            cur = var.get()
            init_dir = str(Path(cur).parent) if cur else str(Path.home())
            if save:
                path = filedialog.asksaveasfilename(
                    defaultextension=".csv",
                    filetypes=filetypes,
                    initialfile=Path(cur).name if cur else "",
                    initialdir=init_dir,
                )
            else:
                path = filedialog.askopenfilename(
                    filetypes=filetypes,
                    initialdir=init_dir,
                )
            if path:
                var.set(path)

        ttk.Button(parent, text="Browse…", command=browse).grid(row=row, column=2, **pad)
        return var

    csv_types = [("CSV files", "*.csv"), ("All files", "*.*")]

    frame = ttk.Frame(root, padding=10)
    frame.grid()

    v_input = make_file_row(frame, "Input CSV:", DEFAULT_INPUT, 0, csv_types)
    v_ref   = make_file_row(frame, "Reference CSV:", DEFAULT_REFERENCE, 1, csv_types)
    v_out   = make_file_row(frame, "Output CSV:", DEFAULT_OUTPUT, 2, csv_types, save=True)

    status_var = tk.StringVar(value="Ready.")
    status = ttk.Label(frame, textvariable=status_var, foreground="gray", wraplength=600, justify="left")
    status.grid(row=4, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 8))

    def run():
        status_var.set("Running…")
        root.update_idletasks()
        try:
            msg = run_transform(v_input.get(), v_ref.get(), v_out.get())
            status_var.set(msg)
            status.config(foreground="green")
        except Exception as exc:
            status_var.set(f"Error: {exc}")
            status.config(foreground="red")

    btn_frame = ttk.Frame(frame)
    btn_frame.grid(row=3, column=0, columnspan=3, pady=6)
    ttk.Button(btn_frame, text="Run Transform", command=run, width=20).pack()

    root.mainloop()


def main():
    build_gui()


if __name__ == "__main__":
    main()
