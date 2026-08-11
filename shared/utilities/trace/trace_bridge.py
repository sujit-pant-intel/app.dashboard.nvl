import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parent
BRIDGE_DIR = BASE_DIR / "bridge"
DOTNET_EXE = Path("C:/dotnet10/dotnet.exe") if Path("C:/dotnet10/dotnet.exe").exists() else Path("dotnet")
BRIDGE_EXE = BRIDGE_DIR / "bin" / "Release" / "net10.0-windows" / "win-x64" / "trace-bridge.exe"
BRIDGE_DLL = BRIDGE_DIR / "bin" / "Release" / "net10.0-windows" / "win-x64" / "trace-bridge.dll"


def _run_bridge(args: List[str]) -> Any:
    if BRIDGE_DLL.exists() and DOTNET_EXE.name.lower() == "dotnet.exe":
        cmd = [str(DOTNET_EXE), str(BRIDGE_DLL)] + args
    elif BRIDGE_EXE.exists():
        cmd = [str(BRIDGE_EXE)] + args
    else:
        cmd = [str(DOTNET_EXE), "run", "--no-build", "-c", "Release", "--"] + args

    env = None
    if DOTNET_EXE.name.lower() == "dotnet.exe":
        env = dict(**os.environ)
        env["DOTNET_ROOT"] = str(DOTNET_EXE.parent)
        env["PATH"] = str(DOTNET_EXE.parent) + ";" + env.get("PATH", "")

    proc = subprocess.run(
        cmd,
        cwd=str(BRIDGE_DIR),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if proc.returncode != 0:
        # Prefer JSON error output from the bridge when available.
        if stdout.startswith("{"):
            try:
                err = json.loads(stdout)
                raise RuntimeError(f"TRACE bridge failed: {err}")
            except json.JSONDecodeError:
                pass
        raise RuntimeError(f"TRACE bridge failed (code={proc.returncode}): {stderr or stdout}")

    if not stdout:
        return []

    return json.loads(stdout)


def search_ituff(
    query: str,
    search_type: str = "sort",
    site: str = "FM",
    sort_source: str = "AMR",
    site_datasource: str = "CLASS",
) -> List[Dict[str, Any]]:
    """Search TRACE index by lot/visual-id/program name.

    search_type: "sort" or "class"
    site: used for class search (e.g. FM, IDC, SC)
    site_datasource: used for class search (CLASS or CLASSHDMT)
    sort_source: used for sort search (AMR, GER, GAR)
    """
    args = ["search", query, "--type", search_type]
    if search_type.lower() == "class":
        args += ["--site", site, "--site-datasource", site_datasource]
    else:
        args += ["--sort-source", sort_source]
    return _run_bridge(args)


def get_by_program(program_name: str) -> List[Dict[str, Any]]:
    """Get ituff definitions from Aries by test program name.

    Returns a list of ItuffDefinition dicts (same shape as get_ituffs).
    Each dict includes 'rootTpDirectory', 'tplDirectory', 'stplDirectory'
    which can be used to locate .mtpl files for ADTL limit extraction.
    """
    return _run_bridge(["get-by-program", "--program", program_name])


def get_ituffs(lot: str, operation: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get Aries ituff definitions by lot and optional operation."""
    args = ["get-ituffs", "--lot", lot]
    if operation:
        args += ["--operation", operation]
    return _run_bridge(args)


def xeus_get(
    lot: str,
    operation: Optional[str] = None,
    program: Optional[str] = None,
    visualid: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get XEUS ituff definitions by lot (and optional operation/program/visualid)."""
    args = ["xeus-get", "--lot", lot]
    if operation:
        args += ["--operation", operation]
    if program:
        args += ["--program", program]
    if visualid:
        args += ["--visualid", visualid]
    return _run_bridge(args)


def xeus_bin_dist(
    lot: str,
    operation: Optional[str] = None,
    program: Optional[str] = None,
    bin_kind: str = "interface",
) -> Dict[str, Any]:
    """Get XEUS bin distribution summary by lot (and optional operation/program).
    
    bin_kind: "interface" (default), "hard", "functional", or "full"
    Returns summary with all matching wafers and selected ituff.
    """
    args = ["xeus-bin-dist", "--lot", lot, "--bin-kind", bin_kind]
    if operation:
        args += ["--operation", operation]
    if program:
        args += ["--program", program]
    return _run_bridge(args)


def xeus_units(
    lot: str,
    operation: Optional[str] = None,
    program: Optional[str] = None,
    wafer: Optional[str] = None,
    bin_kind: str = "interface",
    bin_value: Optional[int] = None,
    include_test: bool = True,
) -> Dict[str, Any]:
    """Get per-unit rows from XEUS session.

    bin_kind: "interface" (default), "hard", "functional", or "full"
    bin_value: optional filter for selected bin value (e.g., interface bin 8)
    include_test: include bin-setter test instance name when available
    """
    args = ["xeus-units", "--lot", lot, "--bin-kind", bin_kind]
    if operation:
        args += ["--operation", operation]
    if program:
        args += ["--program", program]
    if wafer:
        args += ["--wafer", wafer]
    if bin_value is not None:
        args += ["--bin", str(bin_value)]
    args += ["--include-test", "true" if include_test else "false"]
    return _run_bridge(args)


if __name__ == "__main__":
    # Example usage:
    #   python trace_bridge.py
    try:
        data = search_ituff("fab-sort", search_type="sort", sort_source="AMR")
        print(json.dumps(data[:5], indent=2))
    except Exception as exc:
        print(f"Error: {exc}")
