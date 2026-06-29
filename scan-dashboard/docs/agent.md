# Scan RAWSTR Dashboard — Agent Reference

## Overview

Processes scan test RAWSTR columns from die-sort CSV data, decodes per-IP pass/fail
bitstrings, and builds a static HTML dashboard (wafer maps, IP failure pareto, lot/wafer
table, filter tree).

## Architecture

```
scan-dashboard/
  dashboard.py          # Tkinter GUI entry point
  src/
    hry_frame.py        # HRYFrame tab — all GUI logic, settings load/save, run/open
    pipeline.py         # Headless Python pipeline (all processing + dashboard build)
  dashboard/
    index.html          # Dashboard HTML template
    data.js             # Placeholder — overwritten by pipeline with real data
  collateral/
    Partition_Info.csv
    reticle/            # Reticle CSV files matched by DERVREVSTEP[:6]
    material/           # Material CSV files matched by LOT[:7] + WAFER
```

## Running

### GUI (interactive)
```
python dashboard.py
python dashboard.py U:/path/to/input.json    # pre-populates all fields from JSON
```

### Headless pipeline
```
python src/pipeline.py --input data.csv --output ./results
python src/pipeline.py --from-json input.json              # load all settings from JSON
python src/pipeline.py --from-json input.json --standalone  # also produce single-file HTML
```

## Settings JSON format (input.json / *.scancfg.json)
Supports both old (`inputs`/`output`) and new (`input_files`/`output_dir`) key names:
```json
{
  "input_files": ["path/to/data.csv"],
  "output_dir":  "path/to/output",
  "jmp":         "C:\\Program Files\\SAS\\JMPPRO\\17\\jmp.exe",
  "reticle_dir": "",
  "material_dir": "",
  "standalone":  false
}
```

## Key implementation notes
- **Loader**: `hry_frame._load_settings_file(path)` handles both old and new JSON key formats.
  `auto_load(path)` is the public method used for CLI pre-population (called after mainloop starts).
- **pipeline.py `--from-json`**: reads the JSON, maps old/new keys, then merges with any
  explicit CLI flags (CLI wins over JSON).
- **data.js template**: placeholder only — `build_dashboard()` always overwrites it with real data.
  Never edit `dashboard/data.js` manually.
- **JMP support**: .jmp input files are auto-exported to CSV via JMP before processing.
- **Reticle**: matched by `DERVREVSTEP[:6]` to CSV filenames in `collateral/reticle/`.
- **Material**: matched by `SORT_LOT[:7]` + `WAFER` to CSV files in `collateral/material/`.

---

## Reticle / Wafer Map — Known Issues & Fixes

### 1. `ret_site_num` corrupted for edge shots (pipeline.py)

**Root cause**: Edge shots at the wafer boundary contain only a partial set of die
columns.  Computing intra-shot position as `rdx = DieX - shot_min_x` gives `rdx=0` for
every die in that shot (they all appear to be column 0), so the wrong site number is
stored in `ret_site_num["0,0"]`.

**Fix**: Use explicit `ReticleDieX` / `ReticleDieY` columns from the reticle CSV
(present since the 8PF5CV mapping file).  Only fall back to the computed offset when
those columns are absent.

```python
if 'ReticleDieX' in rt.columns and 'ReticleDieY' in rt.columns:
    rdx_col = rt['ReticleDieX'].astype(int)
    rdy_col = rt['ReticleDieY'].astype(int)
else:
    shot_min_x = rt.groupby(['LayoutX','LayoutY'])['DieX'].transform('min').round().astype(int)
    shot_min_y = rt.groupby(['LayoutX','LayoutY'])['DieY'].transform('min').round().astype(int)
    rdx_col = (die_x - shot_min_x).astype(int)
    rdy_col = (die_y - shot_min_y).astype(int)
```

**Rule**: Always prefer explicit `ReticleDieX/Y` over computed offsets.

---

### 2. Die Loc pills showing values from unrelated layout files (index.html)

**Root cause**: `D.reticle_layout` is keyed by the first-6-char prefix of
`DERVREVSTEP` and contains **all** layouts loaded at pipeline build time (currently
three CSV files: NVL816-BLLC=1–4, NVL816=1–6, NVL48=1–12).  Naïvely iterating
`Object.values(D.reticle_layout)` gives the union of all three, so the Die Loc pills
show **1–12** even for a pure NVL816 run.

**Fix** (`_rvInitFilters` in `index.html`): determine the active prefixes from
`D.die_map` and only include Reticle values for those prefixes.

```javascript
// Only pull Reticle site values from layouts present in the actual data
const _activePfx = new Set(
  (D.die_map||[]).map(d => String(d.Layout||'').substring(0,6).toUpperCase()).filter(Boolean)
);
const _layoutAll = D.reticle_layout || {};
let _retRaw = (_activePfx.size ? [..._activePfx] : Object.keys(_layoutAll))
  .flatMap(pfx => ((_layoutAll[pfx]||{}).reticle||[]).map(v=>String(v==null?'':v).trim()).filter(Boolean));
if (!_retRaw.length) _retRaw = (D.die_map||[]).map(d=>String(d.Reticle||'').trim()).filter(Boolean);
```

**Mixed-product note**: if a run genuinely contains dies from multiple layout prefixes
(e.g., NVL816 + NVL48), `_activePfx` will hold both, so pills will correctly show the
union (1–12).  Filtering by Die Loc = 5 then only passes NVL48 dies — correct.

---

### 3. Shot filter state model (`_rvShotFilter`)

Three distinct states with different rendering behaviour:

| Value | Meaning | Shot outlines drawn? | Non-selected dies |
|---|---|---|---|
| `null` | All shots selected | Yes (all) | N/A |
| `new Set()` (empty) | No shot filter ("None") | No | All dies shown |
| `new Set([0,1,...])` | Specific shots | Yes (selected only) | Dimmed to `#0d1118` |

**Conditional `retShots` in `wmRender` call**:
```javascript
retShots: (_rvShotFilter === null || _rvShotFilter.size > 0) ? retShots : []
```
Pass an empty array when `_rvShotFilter` is the empty Set so `wmRender` draws no shot
outlines at all.

---

### 4. `retShotLabels` in `wmRender`

- `retShotLabels: false` (or omitted) → no labels inside shots.
- `retShotLabels: true` → auto-number shots "1", "2", "3", … (uses `si+1`).
- Pass an explicit array `["A","B",...]` to override labels per shot.

Use `retShotLabels: true` for the **composite** wafer map so users can identify which
shot number each outlined region corresponds to.  Use `false` (or `labelScale:0.5`)
for individual per-wafer tiles where labels would be too crowded.

---

## RAWSTR Column Format

Scan test columns in the input CSV follow the pattern:

```
SCN_{MODULE}::{TESTTYPE}_{BLOCK}_HRY_{KILL}_{SUBFLOW}_{DFT}_{VRAIL}_{VCORNER}_{FREQ}_{STEPPING}_POR_HRY_RAWSTR_{JOBID}
```

Matched by regex `_COL_RE` in `pipeline.py`.  Token positions (1-based after `::`, split by `_`):

| Position | Field | Usage |
|---|---|---|
| Before `::` | MODULE (`SCN_ATOM`, `SCN_CORE`, `SCN_UNCORE`) | Joined to config `MODULE` |
| 1 | TESTTYPE (`CHAIN`, `STUCKAT`, `ATSPEED`, `DIAG`) | Used as test-type grouping |
| 2 | BLOCK (`ATOM0`–`ATOM3`, `CORE0`–`CORE3`, `UNCORE`) | Joined to config `TEST`; used as fail-pair component |
| 3 | HRY type | Not used |
| 4 | K/E (kill / edc) | Not used |
| 5 | SUBFLOW (`BEGIN`, `FINAL`, `PREHVQK`) | Dashboard filter |
| 6 | DFT flag | Not used |
| 7 | Voltage rail (`VATOM`, `VCCIA`, `VCCR`, …) | Not used |
| 8 | Voltage corner (`NOM`, `MIN`, `MAX`) | Dashboard filter |
| 9 | Frequency (`LFM`, `HFM`, …) | Dashboard filter |
| Last token | JOBID (numeric) | Stored on column record |

**Example column names:**
```
SCN_ATOM::ATSPEED_ATOM0_HRY_K_PREHVQK_N_VATOM_NOM_LFM_M0_POR_HRY_RAWSTR_119325
SCN_CORE::CHAIN_CORE2_HRY_K_BEGIN_N_VCCIA_NOM_LFM_M2_POR_HRY_RAWSTR_119325
SCN_UNCORE::STUCKAT_UNCORE_HRY_K_BEGIN_N_VCCR_NOM_LFM_X_POR_HRY_RAWSTR_119325
```

---

## Bitstring Decoding

Each cell in a RAWSTR column is either a compressed (`DEFLATE32_…`) or plain bitstring.

### DEFLATE32 decode (`pipeline._deflate32_decode`)

```
value = "DEFLATE32_<base32-encoded-zlib-data>"
```

1. Strip the `DEFLATE32_` prefix.
2. Decode each character using the custom base-32 alphabet
   `ABCDEFGHIJKLMNOPQRSTUVWXYZ234567` (each char → 5 bits).
3. Pack bits into bytes (pad to multiple of 8 with trailing zeros).
4. `zlib.decompress(raw, -8)` (raw deflate, no header) → UTF-8 string.

If the value does **not** start with `DEFLATE32_` it is already a plain bitstring —
return it unchanged.

### Bitstring semantics

```
bitstring = "1  0  1  0  0  1  1  0  8  ..."
index       0  1  2  3  4  5  6  7  8
            ↑
        reset bit
```

| Character | Meaning |
|---|---|
| `0` | FAIL |
| `1` | PASS |
| `8` | UNTESTED (TotalFailCaptureCount limit reached — remaining partitions not executed) |
| `9` | UNASSIGNED (no config `HRYIndex` points to this position) |

**Bit 0 — reset bit**: must be `'1'` for the test to be considered valid.
If `bit[0] != '1'` the status is `RESET_FAIL` for every IP in that die/column.

### `_get_status(bitstr, idx)` return values

| Condition | Return |
|---|---|
| `bitstr` empty | `MISSING` |
| `bitstr[0] != '1'` | `RESET_FAIL` |
| `idx >= len(bitstr)` | `UNASSIGNED` |
| `bitstr[idx] == '0'` | `FAIL` |
| `bitstr[idx] == '1'` | `PASS` |
| `bitstr[idx] == '8'` | `UNTESTED` |

---

## IP Failure Determination

**An IP is counted as a failure if and only if: reset bit = `1` AND `bit[INDEX] = '0'`.**

- `RESET_FAIL` (reset bit = 0) is **not** an IP-level failure — the test did not execute.
- `UNTESTED` and `UNASSIGNED` are not failures.
- Only `STATUS == 'FAIL'` records are kept in `per_ip`; all other statuses are discarded.

Pipeline code:
```python
statuses  = decoded.apply(lambda s, i=idx: _get_status(s, i))
fail_mask = statuses.isin(["FAIL"])   # RESET_FAIL excluded intentionally
```

---

## Config File (`shared/setup/scan-dashboard/*.csv`)

```
MODULE, TEST, IP, REGION, PARTITION, INDEX
```

| Column | Description |
|---|---|
| `MODULE` | Matches `SCN_{MODULE}` part of column name (e.g., `SCN_ATOM`) |
| `TEST` | Matches BLOCK token from column name (e.g., `ATOM0`, `CORE2`) |
| `IP` | IP / partition name (e.g., `pcie_tx_lane0`) |
| `REGION` | Region grouping (e.g., `IO`, `COMPUTE`) — used in fail pair and filter tree |
| `PARTITION` | Redundant with TEST/BLOCK in current data — not used for filtering |
| `INDEX` | Bit position in the decoded bitstring for this IP |

Fields not used: `si_area`, `scan_cell_count`.

Total fault = STUCKAT + ATSPEED fails (CHAIN and DIAG tracked separately).

---

## Yield / Failure Percentage Calculation

**Formula:**

$$\text{Fail \%} = \frac{\text{fail\_count}}{\text{total\_dies\_per\_wafer}} \times 100$$

**`total_dies_per_wafer`** is sourced from the reticle mapping CSV (not the row count of
the input CSV, which may be a subset):

```python
prefix   = lot[:6].upper()                        # e.g. "8PF5CV"
rt_total = len(reticle_layout[prefix]["x"])        # 393 for 8PF5CV, 619 for 8PF6CV
total_dies_per_wafer[f"{lot}|{wafer}"] = rt_total  # fallback: len(grp) if no reticle
```

Reticle files live in `shared/reticle/` and are auto-loaded by `build_reticle_layouts()`.
The prefix is the first 6 characters of the LOT number.

---

## Data Model

### `per_ip` (failure records only)

One row per failing (die × IP × test column):

```
LOT, WAFER, X, Y, VISUAL_ID, MODULE, TESTTYPE, BLOCK, SUBFLOW, VCORNER, FREQ,
PARTITION, IP, REGION, STATUS
```

### `die_map` (per-die summary)

One row per failing die, aggregated across all per_ip records:

```
LOT, WAFER, X, Y, VISUAL_ID, Layout, IB, FB,
CHAIN, STUCKAT, ATSPEED, DIAG,
fails_chain, fails_stuckat, fails_atspeed, fails_diag, fails
```

`fails_*` are Python sets of fail-pair strings (see below).  `fails` is the union of all four.

### Fail pair format

```
"BLOCK:REGION:IP"
```

Example: `"ATOM0:IO:pcie_tx_lane0"`.

- **BLOCK** disambiguates same-named IPs across different blocks (ATOM0 vs ATOM1).
- **REGION** groups IPs within a block by silicon area.
- Used as the unique key in the IP filter tree and wafer map highlighting.

---

## Dashboard — Recent Changes (May 2026)

### 5. wmRender IIFE injection order fix (pipeline.py + index.html)

**Problem**: Individual per-wafer tile SVG maps were blank (`window.wmRender` was
`undefined` when tiles rendered), introduced when the color-filter feature was added.

**Root cause**: `pipeline.py` was injecting `WAFERMAP_JS` (the wmRender IIFE) via
`html.replace("</body>", WAFERMAP_JS + "\n</body>")`, placing it *after* the main
`<script>` block. In a `file://` browser context this created a subtle race: the main
script's `setTimeout(applyFilters, 0)` could fire before the IIFE completed registration
of `window.wmRender`.

**Fix** (`pipeline.py › write_dashboard`): inject before the main script block instead:

```python
_marker = '\n<script>\n"use strict";'
if _marker in _html:
    _html = _html.replace(_marker, '\n' + WAFERMAP_JS + _marker, 1)
else:
    _html = _html.replace("</body>", WAFERMAP_JS + "\n</body>", 1)
```

`window.wmRender` is now guaranteed to be set before any dashboard code runs.

---

### 6. YIELD_TARGET loaded from collateral CSV (pipeline.py + index.html)

**Problem**: The polynomial target reference line in FailPerFault used a hardcoded
`YIELD_TARGET` array baked into the HTML template — stale and not updateable.

**Source file**: `shared/setup/scan-dashboard/yield-estimate-per-fault-count.csv`

```
Fault_Count,Target (%)
771838036,7.31
...
```

**Fix**:
- `pipeline.py › _load_yield_target()` reads the CSV using `_YIELD_TARGET_CSV`
  (`_SHARED_CFG / "yield-estimate-per-fault-count.csv"`) and returns
  `[{"fc": int, "pct": float}, ...]`. Included in the result dict as `"yield_target"`.
- `index.html`: hardcoded array replaced with
  `const YIELD_TARGET = (D.yield_target && D.yield_target.length) ? D.yield_target : [];`
- Edit the CSV to change target points; next pipeline run picks them up automatically.

---

### 7. FailPerFault — per-IP markers + legend at bottom (index.html)

**Change**: Scatter traces in the FailPerFault chart now use **one trace per IP** with a
unique marker symbol cycling through 20 Plotly shapes:

```javascript
const IP_SYMS = ['circle','square','diamond','cross','x','triangle-up','triangle-down',
  'triangle-left','triangle-right','pentagon','hexagon','star','hexagram',
  'star-triangle-up','star-square','hourglass','bowtie','circle-cross','circle-x','square-cross'];
```

Color still encodes **module** (blue=ATOM, red=CORE, green=UNCORE via `mc()`).
Legend moved to bottom (`y: -0.18, yanchor: 'top'`). Duplicate IPs (same IP in multiple
blocks when byReg=true) share a `legendgroup` and `showlegend:false` after the first
occurrence to avoid duplicate legend entries.

---

### 8. FailPerFault — target anchor points hidden by default (index.html)

The raw `YIELD_TARGET` anchor points trace (`tgtPts`) now has `visible: 'legendonly'`
so it does not clutter the scatter plot. Click "Target pts" in the legend to show them.

---

### 9. FailPerTestType — Block+Region mode revamp (index.html)

**Problem**: When "Block + Region" was checked, the 4-level hierarchical x-axis
(`module / block / region / ip`) created group-span labels at the top of the chart that
overlapped with the TestType legend at `y: 1.06`.

**Fix**: when `byReg=true`, the chart switches to a completely different trace strategy:

| Mode | Traces | X-axis | Y-axis | Legend |
|---|---|---|---|---|
| byReg=false | One per TestType (CHAIN/STUCKAT/ATSPEED/DIAG) | Module / IP | Fail Dies per TT | Top |
| byReg=true | One per Block/Region combo (MPLT palette) | Module / IP | Total fail dies | Bottom |

In byReg=true mode the per-TestType breakdown is surfaced in the **hover tooltip**:
`Block: CORE0  Region: ICORE0 … CHAIN: 12 | STUCKAT: 4`

Legend position: `y: -0.22, yanchor: 'top'` when byReg, `y: 1.06` otherwise.

---

### 10. Pipeline — improved error for wrong CSV type (pipeline.py)

When no `SCN_*::*_HRY_RAWSTR_*` columns are found, the error now lists the `::` prefixes
present in the file and explains what format is required:

```
No SCN RAWSTR columns found in the CSV.
  This CSV has N columns with M '::' prefixes: PREFIX1, PREFIX2, ...
  The scan dashboard requires columns matching:
    SCN_<MODULE>::<CHAIN|STUCKAT|ATSPEED|DIAG>_<BLOCK>_HRY_<K|E>_..._POR_HRY_RAWSTR_<ID>
  This looks like a yield/sort CSV. You need a SCAN HRY RAWSTR export from TRACE.
```

---

### 11. FailPerFault — TT filter bar (index.html)

A CHAIN / STUCKAT / ATSPEED / DIAG checkbox bar was added to the FailPerFault data
panel (matching the existing bar on FailPerTestType). Both panels now share the same
`TT_SHOW` global Set and `toggleTTShow()` function, so checking/unchecking a test type
updates both tabs simultaneously.

