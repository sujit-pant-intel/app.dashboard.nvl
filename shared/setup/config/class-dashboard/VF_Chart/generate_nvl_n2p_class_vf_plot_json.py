from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def _auto_find_workbook(search_dir: Path) -> Path:
    candidates = sorted(
        list(search_dir.glob("*.xlsx")) + list(search_dir.glob("*.xls")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not candidates:
        raise FileNotFoundError(
            f"No .xls or .xlsx files found in: {search_dir}"
        )

    preferred = [
        p
        for p in candidates
        if "nvl_n2p_class_vf_tracker" in p.name.lower()
    ]
    return preferred[0] if preferred else candidates[0]


def _default_output_for_workbook(workbook_path: Path) -> Path:
    return workbook_path.with_name(f"{workbook_path.stem}_plot_A_to_L_grouped.json")


def build_plot_data(workbook_path: Path) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}

    for sheet_name in ("CORE", "ATOM"):
        frame = pd.read_excel(workbook_path, sheet_name=sheet_name)
        selected = frame.iloc[:, :12]
        columns = list(selected.columns)
        series_list: list[dict[str, object]] = []

        for index in range(0, len(columns), 2):
            if index + 1 >= len(columns):
                continue

            x_column = columns[index]
            y_column = columns[index + 1]

            points = []
            for x_value, y_value in zip(selected[x_column], selected[y_column]):
                if pd.isna(x_value) or pd.isna(y_value):
                    continue
                points.append({"x": float(x_value), "y": float(y_value)})

            if not points:
                continue

            series_list.append(
                {
                    "xColumn": str(x_column),
                    "label": str(y_column),
                    "points": points,
                }
            )

        grouped[sheet_name] = series_list

    return grouped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create plotting JSON from the NVL N2P CLASS VF tracker workbook."
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=None,
        help="Input Excel workbook path. If omitted, the script auto-detects .xls/.xlsx in the current directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path. If omitted, writes <workbook_stem>_plot_A_to_L_grouped.json in the current directory.",
    )
    args = parser.parse_args()

    cwd = Path.cwd()
    workbook_path = args.workbook if args.workbook else _auto_find_workbook(cwd)
    output_path = args.output if args.output else _default_output_for_workbook(workbook_path)

    plot_data = build_plot_data(workbook_path)
    output_path.write_text(json.dumps(plot_data, indent=2), encoding="utf-8")

    total_series = sum(len(series_list) for series_list in plot_data.values())
    print(f"Workbook: {workbook_path}")
    print(f"Wrote {output_path}")
    print(f"Tabs: {', '.join(plot_data.keys())}")
    print(f"Series count: {total_series}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())