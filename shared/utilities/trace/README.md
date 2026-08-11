# TRACE Bridge — Usage Guide

The TRACE bridge is a Python wrapper around a .NET CLI tool that queries Intel's TRACE / XEUS test data systems. It provides lot-level and wafer-level ituff metadata, bin distributions, and unit-level data for NVL sort results.

## Default Usage Policy

- Always use the Python wrapper in `trace_bridge.py` for TRACE/XEUS data queries.
- Do not call the .NET bridge DLL directly for normal analysis workflows.
- Use direct CLI calls only for low-level debugging of wrapper issues.

Reason: the Python wrapper is the most reliable interface in this environment and provides consistent argument handling and JSON parsing.

---

## Location

```
C:\scripts\app.yield.nvl\utilities\testprogram\trace\
├── trace_bridge.py          ← Python API (use this)
├── test_xeus.py             ← Example / smoke test script
└── bridge\                  ← .NET CLI binary (do not call directly)
    ├── Program.cs
    └── bin\Release\net10.0-windows\win-x64\
        ├── trace-bridge.exe
        └── trace-bridge.dll
```

---

## Requirements

| Requirement | Notes |
|-------------|-------|
| Python 3.x | Standard library only (`subprocess`, `json`) |
| .NET 10 runtime | Must be at `C:\dotnet10\dotnet.exe` |
| Network | Requires access to Intel internal TRACE/XEUS services |

The Python wrapper automatically detects `C:\dotnet10\dotnet.exe` and invokes the DLL through it. No install steps needed — just import.

---

## Python API

```python
import sys
sys.path.insert(0, r'C:\scripts\app.yield.nvl\utilities\testprogram\trace')
import trace_bridge
```

---

### `xeus_get` — Get wafer ituff definitions from XEUS

```python
results = trace_bridge.xeus_get(
    lot,                      # required — e.g. "Q603S6T03"
    operation=None,           # optional — e.g. "119325"
    program=None,             # optional — e.g. "NCXSDJXL0H61B002619"
    visualid=None,            # optional — filter by ituff name
)
```

**Returns:** list of ituff definition dicts, one per wafer.

**Example:**
```python
results = trace_bridge.xeus_get("Q603S6T03", operation="119325")
for r in results:
    print(r["name"], r["totalLatestUnits"], r["yieldText"])
# W506_Q603S6T03_119325_Seq1  393  0%
# W507_Q603S6T03_119325_Seq1  393  0%
# ...
```

**Result fields:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | str | Wafer ituff name (e.g. `W506_Q603S6T03_119325_Seq1`) |
| `lot` | str | Lot ID |
| `operation` | str | Operation number |
| `programName` | str | Test program name |
| `partType` | str | Part type (e.g. `8PF5CVL`) |
| `facility` | str | Test facility (e.g. `STT`) |
| `endDate` | str | ISO datetime of last test |
| `totalLatestUnits` | int | Total units tested |
| `totalPassUnits` | int | Units that passed |
| `yield` | float | Pass yield as decimal (0.0–1.0) |
| `yieldText` | str | Human-readable yield (e.g. `"73%"`) |
| `stplDirectory` | str | UNC path to test program |
| `materialType` | str | `"Xeus"` for XEUS-sourced data |

---

### `xeus_bin_dist` — Get bin distribution summary (metadata)

```python
result = trace_bridge.xeus_bin_dist(
    lot,                      # required
    operation=None,           # optional
    program=None,             # optional
    bin_kind="interface",     # "interface" | "hard" | "functional" | "full"
)
```

**Returns:** a dict with wafer metadata for all matching sessions. Detailed per-bin counts require the XEUS session API (see Known Limitations below).

**Example:**
```python
result = trace_bridge.xeus_bin_dist("Q603S6T03", operation="119325", bin_kind="interface")
print(result["query"]["matches"])        # 6 (number of wafers)
print(result["message"])                 # "Detailed bin distribution requires session API."
for m in result["allMatches"]:
    print(m["name"], m["totalLatestUnits"])  # wafer name + unit count
```

**Result structure:**

| Field | Description |
|-------|-------------|
| `selectedItuff` | The most recent wafer ituff definition |
| `query.matches` | Total number of matching wafers found |
| `query.backend` | `"XEUS"` |
| `message` | Status message (may indicate session API unavailable) |
| `allMatches` | Full list of all matching wafer ituff dicts |

> **Note:** When XEUS session API is available, `distribution` (list of `{bin, count, percent}`) and `totalUnits` may also be present. Currently the session API returns no data — use `xeus_get` for wafer-level metadata in the meantime.

**bin_kind options:**

| bin_kind | Maps to |
|----------|---------|
| `interface` | Interface bin (default, same as `hard`) |
| `hard` | Hard bin |
| `functional` | Functional bin |
| `full` | Full/data bin (encodes test counter) |

---

### `xeus_units` — Get per-unit rows from XEUS session

```python
units = trace_bridge.xeus_units(
    lot,                      # required
    operation=None,           # optional
    program=None,             # optional — filter to specific test program
    wafer=None,               # optional — wafer number string e.g. "506"
    bin_kind="interface",     # "interface" | "hard" | "functional" | "full"
    bin_value=None,           # optional — filter to specific bin e.g. 8
    include_test=True,        # include fail_test field per unit
)
```

**Returns:** list of unit dicts. Each unit has bin and optional `fail_test`.

**Example:**
```python
units = trace_bridge.xeus_units("Q603S6T03", operation="119325", wafer="506",
                                 bin_kind="interface", bin_value=8, include_test=True)
for u in units:
    print(u.get("fail_test"), u.get("interfaceBin"))
```

> **Important:** Always use `wafer=` scoping. Lot-wide calls (no `wafer=`) hang indefinitely. Even wafer-scoped calls require a working XEUS session API — see Known Limitations.

---

### `search_ituff` — Search TRACE index (Aries/sort-indexed lots)

```python
results = trace_bridge.search_ituff(
    query,                          # lot, visual ID, or program substring
    search_type="sort",             # "sort" or "class"
    sort_source="AMR",              # "AMR" | "GER" | "GAR" (for sort)
    site="FM",                      # site for class search
    site_datasource="CLASS",        # "CLASS" | "CLASSHDMT" (for class)
)
```

> **Note:** For NVL816-BLLC STT lots, `search_ituff` currently returns empty (`[]`). Use `xeus_get` instead — XEUS is the correct backend for STT facility data.

**Sort source guide:**

| sort_source | Region |
|-------------|--------|
| `AMR` | Americas (default) |
| `GER` | Germany / Europe |
| `GAR` | Global (all regions) |

---

### `get_ituffs` — Get Aries ituff definitions (class test data)

```python
results = trace_bridge.get_ituffs(
    lot,            # required
    operation=None  # optional
)
```

> Returns Aries (class test) ituff definitions. For sort data at STT, use `xeus_get` instead.

---

### `get-by-visualid` (CLI only) — Query by visual ID

```python
# Via CLI only — not available as a Python function
# Visual ID must be in Aries format, not EFUSE format
```

> **Known limitation:** CSV files from JMAG/yield reports use `EFUSE_*` format visual IDs (e.g. `EFUSE_Q603S6T0_503_-6_7`). These are **not** Aries visual IDs and will return `[]`. Use `xeus_get` with a lot ID instead.

---

## Direct CLI Usage

If you need to call the bridge directly (e.g. from PowerShell or batch scripts):

```powershell
$dotnet = "C:\dotnet10\dotnet.exe"
$dll    = "C:\scripts\app.yield.nvl\utilities\testprogram\trace\bridge\bin\Release\net10.0-windows\win-x64\trace-bridge.dll"

# xeus-get
& $dotnet $dll xeus-get --lot Q603S6T03 --operation 119325

# xeus-bin-dist
& $dotnet $dll xeus-bin-dist --lot Q603S6T03 --operation 119325 --bin-kind interface

# search (Aries sort index)
& $dotnet $dll search Q603S6T03 --type sort --sort-source GAR

# get-ituffs (Aries class index)
& $dotnet $dll get-ituffs --lot Q603S6T03 --operation 119325

# get-by-visualid
& $dotnet $dll get-by-visualid --visualid "Q603S6T03_506_0_0"
```

All commands output JSON to stdout. Errors output a `{error, message}` JSON object to stdout with exit code 1.

---

## Complete Python Example

```python
import sys, re
sys.path.insert(0, r'C:\scripts\app.yield.nvl\utilities\testprogram\trace')
import trace_bridge

LOT = "Q603S6T03"
OP  = "119325"

# 1. Check which wafers exist (always works)
wafers = trace_bridge.xeus_get(LOT, operation=OP)
print(f"Found {len(wafers)} wafers:")
for w in wafers:
    print(f"  {w['name']}  program={w['programName']}  units={w['totalLatestUnits']}")

# 2. Get bin distribution metadata (allMatches available; distribution requires XEUS session API)
dist = trace_bridge.xeus_bin_dist(LOT, operation=OP, bin_kind="interface")
print(f"\nWafer sessions ({dist['query']['matches']} matches):")
for m in dist["allMatches"]:
    print(f"  {m['name']}  units={m['totalLatestUnits']}")

# 3. Per-unit query — wafer-scoped only (requires XEUS session API)
def wafers_from_defs(defs):
    out = []
    for d in defs:
        m = re.match(r'W(\d+)_', str(d.get('name', '')))
        if m:
            out.append(m.group(1))
    return sorted(set(out))

ws = wafers_from_defs(wafers)
for w in ws:
    try:
        units = trace_bridge.xeus_units(LOT, operation=OP, wafer=w,
                                         bin_kind="interface", bin_value=8, include_test=True)
        units = units if isinstance(units, list) else []
        print(f"  W{w}: {len(units)} bin8 units")
    except Exception as e:
        print(f"  W{w}: {e}")
```

---

## Backend Decision Guide

| Data need | Use |
|-----------|-----|
| STT sort lot wafer list + metadata | `xeus_get(lot, operation=...)` ✅ works |
| Which program ran each wafer | `xeus_get` → `programName` field ✅ works |
| STT bin distribution (per-bin counts) | `xeus_bin_dist` or `xeus_units` — requires XEUS session API ⚠️ |
| Per-unit data with fail test | `xeus_units(lot, wafer=w, ...)` — wafer-scoped only; requires XEUS session API ⚠️ |
| Aries/class test data (JF) | `get_ituffs(lot, operation=...)` — returns `[]` for STT lots |
| Search by lot substring | `search_ituff(query, sort_source="GAR")` — requires AMR access ⚠️ |
| CLASSHDMT bin dist | `bin-dist` CLI command — requires AMR access ⚠️ |

---

## Timeouts

All bridge commands use a **60-second timeout** by default (defined in `Program.cs`). `xeus_bin_dist` can be slow on large lots — if it times out, run it in a background terminal or increase the timeout by modifying `Program.cs` and rebuilding.

---

## Rebuilding the Bridge (if Program.cs changes)

```powershell
cd C:\scripts\app.yield.nvl\utilities\testprogram\trace\bridge
C:\dotnet10\dotnet.exe build -c Release
```

Output goes to `bin\Release\net10.0-windows\win-x64\`.

---

## Known Limitations (as of 2026-05-15)

| API | Status | Error |
|-----|--------|-------|
| `xeus_get` | ✅ Working | Returns wafer metadata correctly. `totalPassUnits=0` / `yield=0%` is a known XEUS data population bug — unit counts are correct. |
| `xeus_bin_dist` | ⚠️ Partial | Returns wafer list (`allMatches`) but no `distribution` data. XEUS session API not responding. |
| `xeus_units` | ❌ Hangs | Wafer-scoped calls hang indefinitely. XEUS session API down. Lot-wide calls (no `wafer=`) always hang. |
| `bin-dist` (CLASS) | ❌ Auth error | `IOException: This user can't sign in because this account is currently disabled` on `\\amr.corp.intel.com\...\CLASSHDMT\Indices\ituff_index.zip` |
| `search_ituff` | ❌ Auth error | Same service account issue on AMR SORT index. |
| `get_ituffs` | ⚠️ Empty | Returns `[]` for STT lots — correct behavior, STT data is in XEUS not Aries. |

Support ticket filed for Issues 1 (XEUS session API) and 2 (AMR service account disabled).

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `xeus_get` returns `[]` | Lot not in XEUS, or operation mismatch | Try without `operation=` filter |
| `xeus_get` shows `totalPassUnits=0`, `yield=0%` | Known XEUS data population bug | Ignore — `totalLatestUnits` is correct |
| `xeus_units` hangs indefinitely | XEUS session API down | Check Known Limitations; wait for support fix |
| `xeus_units` without `wafer=` hangs | Lot-wide queries always time out | Always pass `wafer=` when using `xeus_units` |
| `search_ituff` returns `[]` for STT lots | STT data is in XEUS, not Aries sort index | Use `xeus_get` |
| `search_ituff` raises `IOException: account disabled` | AMR service account disabled | Support ticket required |
| `bin-dist` raises `IOException: account disabled` | AMR service account disabled on CLASSHDMT index | Support ticket required |
| `get-by-visualid` returns `[]` | `EFUSE_*` format IDs are not Aries visual IDs | Use lot-level `xeus_get` |
| `RuntimeError: TRACE bridge failed` | .NET 10 not at `C:\dotnet10\dotnet.exe` | Verify path; bridge auto-detects it |
| Bridge exits with "You must install or update .NET" | System has .NET 8 but bridge requires .NET 10 | Use `C:\dotnet10\dotnet.exe` (not system dotnet) |
| CLI says "Unknown command: xeus-units" | Compiled DLL is older than source | Rebuild: `cd bridge; C:\dotnet10\dotnet.exe build -c Release` |
| Timeout after ~90s | Large lot or slow network | Run from background terminal, check VPN |
