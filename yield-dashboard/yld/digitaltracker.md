# Digital Dashboard — `get_dd_update.py` Reference

Source file: `src/get_dd_update.py`

---

## Purpose

Reads a raw sort data CSV, classifies each die into module yield buckets (ARR/FUN/SCN × ATOM/CORE/CCF/NONCCF), counts defeature die (IB3/IB4) by module type, and writes an Excel workbook used to populate the Intel Digital Dashboard (DD).

---

## Output Excel Structure

Three columns are written per product lot:

| Column | Header | Description |
|--------|--------|-------------|
| 1 | Sub Module | Module row label (e.g. `ARR_ATOM`, `FUN_CORE`, `Bin 1`) |
| 2 | `NW {TP} Yield Loss (Fail Bins) (%)` | Fraction of total die assigned to each fail-bin module bucket |
| 3 | `NW {TP} Recovery Bins (3-4) (%)` | Fraction of total die that are IB3/IB4 and attributed to each module type |

Column 3 rows are only populated for `*_ATOM` and `*_CORE` sub-module rows (not CCF/NONCCF).

---

## Module Classification — `moduleMap`

`moduleMap` is a module-level dict mapping regex patterns (matched against the bin description string) to a category dict with `"dd"` and `"vmax"` keys.

### Category hierarchy

```
Good bins:   Bin 1 / Bin 198 (Vmin Repair) / Bin 2 (Hard Repair) / Bin 202 (Vmax Repair) / Bin 3 / Bin 4
Reset:       Reset  (IB 19, IB 35)
Fail bins:   ARR_ATOM / ARR_CCF / ARR_CORE / ARR_NONCCF
             FUN_ATOM / FUN_CCF / FUN_CORE / FUN_NONCCF
             SCN_ATOM / SCN_CORE / SCN_UNCORE
```

### `getModuleFromBinDesc(modMap, binDesc)`

1. If `re.search(r"B26\d", binDesc)` matches → returns `{"dd": "HVQK (B26)", ...}` (short-circuit for HVQK bins).
2. Iterates all `moduleMap` patterns in insertion order; returns first match.
3. Raises `LookupError` if no pattern matches.

---

## Column 2 — `getYieldByModule`

Reads `DATA_BIN` (7-digit leaf bin) for every die, divides by 10 000 to get a rounded FB value, builds a bin-description key (`"FB{n}"` for pass bins, `"DB{n}"` for fail bins ≥ 1000 × 10000), looks up the description in `binDefs`, then calls `getModuleFromBinDesc` to classify into a `dd` bucket.

Result: `moduleYield["dd"][bucket]` = count of die in that bucket.

---

## Column 3 — `updateDefeatureModCnts`

Populates `defeatureModCnts` dict: `{module_key: count}` where `module_key` ∈ `{ARR_ATOM, FUN_ATOM, SCN_ATOM, ARR_CORE, FUN_CORE, SCN_CORE}`.

### Primary path — LOGTRACKER decode

For each IB3/IB4 die, iterates all columns matching:
```python
LOGTRACKER_AM[0-3]   # ATOM array
LOGTRACKER_AP[0-3]   # ATOM (primary)
LOGTRACKER_CR[0-7]   # CORE
```
Calls `prime_error_decode(val[10:].strip("="))` to inflate the PRIME compressed string. Parses the second decoded line for a module pattern `(\w{3}_\w{4,5})::`.  
Example decoded value: `ARR_ATOM0::some_test_instance` → module key = `"ARR_ATOM"`.

**This is the only path that can produce a per-module (ARR/FUN/SCN) breakdown for column 3.**

### Fallback path — binDefs lookup

Activated when:
- LOGTRACKER primary path found nothing (`dfCnt == 0`)
- `binDefs` and `modMap` are both provided

For each unique `DATA_BIN` value among IB3/IB4 die:
```python
roundDbin = round(float(dbin) / 10000)   # e.g. 3010001 → 301
binKey = "FB" + str(roundDbin)            # e.g. "FB301"
binDesc = binDefs.get(binKey)             # e.g. "B301_PASS"
module = getModuleFromBinDesc(modMap, binDesc)
ddCat = module.get("dd", "")
```

**For NVL816-BLLC IB3/4 bins**, `binDesc` is synthesized as `"B301_PASS"`, `"B302_PASS"`, etc. These match the `moduleMap` pattern:
```python
r"B3\d\d_PASS" → {"dd": "Bin 3", ...}
r"B4\d\d_PASS" → {"dd": "Bin 4", ...}
```

The fallback **succeeds without error** but sets `dfModCnts["Bin 3"]` and `dfModCnts["Bin 4"]` — not any `*_ATOM` or `*_CORE` key. `makeOutXl` only reads `defeatureModCnts.get("ARR_ATOM", 0)` etc., so **column 3 = 0% for all rows**.

---

## Why Column 3 = 0% for NVL816-BLLC

### Root cause: sub-bins encode repair type, not test type

The `SortBinCalculatorConfig_{etemp,cold,hot}.json` in the test program (`Modules/TPI_BIN/InputFiles/`) defines `GoodDieBinAssignments`. All three temperature configs are identical for IB3/4:

| BinValue | FB  | IB | CoreConfig | AtomConfig | DefectRepair | VminRepair | VmaxRepair |
|----------|-----|----|------------|------------|--------------|------------|------------|
| 3010001  | 301 | 3  | 8 cores    | 12 atoms   | 0            | 0          | 0          |
| 3020001  | 302 | 3  | 8 cores    | 12 atoms   | 0            | 1 (Vmin)   | 0          |
| 3030001  | 303 | 3  | 8 cores    | 12 atoms   | 1 (Defect)   | 0          | 0          |
| 3040001  | 304 | 3  | 8 cores    | 12 atoms   | N/A          | N/A        | N/A        |
| 4010001  | 401 | 4  | 6 cores    | 16 atoms   | 0            | 0          | 0          |
| 4020001  | 402 | 4  | 6 cores    | 16 atoms   | 0            | 1 (Vmin)   | 0          |
| 4030001  | 403 | 4  | 6 cores    | 16 atoms   | 1 (Defect)   | 0          | 0          |
| 4040001  | 404 | 4  | 6 cores    | 16 atoms   | N/A          | N/A        | N/A        |

**IB=3** = 4 atoms defeatured (AtomConfig 16 → 12). **IB=4** = 2 cores defeatured (CoreConfig 8 → 6).

The sub-bin number encodes **which repair was applied** — not which ARR/FUN/SCN test caused the defeat. There is no information in the bin number that distinguishes an ARR-caused atom defeat from a FUN-caused atom defeat.

### LOGTRACKER columns are absent for IB3/4 die

For UltraBinner GOODBIN paths (IB=3/4), the test program assigns the bin via `SPEXSortSetBinTestMethod` without going through the normal module test failure path that populates LOGTRACKER_AP/CR. Those LOGTRACKER columns are only written when a die hits a standard module test failure. For IB3/4 die in this product, **all LOGTRACKER_AP0–3 and LOGTRACKER_CR0–7 columns are NaN**.

### Confirmed columns non-null for IB3/4 die in this lot

Only these columns carry meaningful data for IB3/4 die:
- `DATA_BIN_*` — leaf bin (e.g. 3010001)
- `DATA_TOTAL_BIN_*`, `INTERFACE_TOTAL_BIN_*`, `FUNCTIONAL_TOTAL_BIN_*`
- `BOMGROUPNAME_*` — always `"UNDEFINED"` for IB3/4

### Conclusion

> **Column 3 = 0% for NVL816-BLLC is correct behaviour, not a bug.**  
> To populate column 3 with a per-module (ARR/FUN/SCN) breakdown, LOGTRACKER_AP/CR data must be present and non-null. The test program does not encode this information in the bin number.

---

## `_buildBinDefsFromDF` — Synthesizing binDefs from Data CSV

When a separate `.bindef` / Crystal Ball CSV is not available, `binDefs` is built directly from the raw data CSV columns.

```python
for dbin in df[db_col].dropna().unique():
    round_dbin = round(float(dbin) / 10000)
    if round_dbin < 1000:
        # Pass bin: synthesize "B{fb}_PASS" style key
        bin_key = "FB" + str(round_dbin)           # e.g. "FB301"
        binDefs[bin_key] = f"B{round_dbin}_PASS"   # e.g. "B301_PASS"
    else:
        # Fail bin: use the "Bin Description_" column value (full test path)
        bin_key = "DB" + str(round(float(dbin)))
        binDefs[bin_key] = df.loc[df[db_col]==dbin, bd_col].dropna().iloc[0]
```

This synthesized description (`"B301_PASS"`) is sufficient to match good-bin `moduleMap` patterns but contains no module-type information for IB3/4.

---

## productInfo Table

Defined in `get_dd_update.py`. Used by `updateDefeatureModCnts` and `makeOutXl`.

| Product | DPW | DevRevStep | TPrgx | dfBins | numCores | numAtoms |
|---------|-----|------------|-------|--------|----------|----------|
| ARL68-N3B | 797 | 8PYJCVJ | `(E6\w)` | [3,4] | 6 | 8 |
| ARLS816 | 516 | 8PYVCVB, 8PYVCVAB | `(8[2,3]\w)` | [3,4] | 8 | 16 |
| NVL48 | 1200 | 8PY6CVT | `(8[1,2]\w)` | [3,4] | 4 | 8 |
| NVL816 | 619 | 8PF6CVP, 8PF6CVR | `(5[1,2]\w)` | [3,4] | 8 | 16 |
| NVL816-BLLC | 393 | 8PF5CVL | `(6[01]\w)` | [3,4] | 8 | 16 |

`dfBins` = list of Interface Bin values treated as defeature (recovery) bins.  
`numCores` / `numAtoms` = used to build LOGTRACKER column regex range (e.g. `LOGTRACKER_CR[0-7]`).

---

## Common Pitfalls

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| Column 3 = 0% for all module rows | LOGTRACKER absent (NVL816-BLLC and similar) | Expected — see above. Cannot be fixed without LOGTRACKER data |
| Column 3 = 0% despite IB3/4 die present | Fallback finds match but maps to `"Bin 3"`/`"Bin 4"` not `*_ATOM`/`*_CORE` | Same root cause — LOGTRACKER required for per-module attribution |
| `"Not all Defeatured die accounted for"` warning | IB3/4 die exist but LOGTRACKER null and fallback has no binDefs match | Provide binDefs via `_buildBinDefsFromDF`; if descriptions are GOODBIN-style, warning is expected |
| Bin 198 shows wrong description | Old `_bin_map_cat` regex `^FB198\d+$` matched `FB19880019` | Fixed (commit `2d100e2`): use exact `bindef_dict.get(f'FB{n_str}', '')` lookup |
| `updateDefeatureModCnts` raises `UnboundLocalError` | Called with `waferLvl=True` | Wafer-level not supported; always pass `waferLvl=False` |
