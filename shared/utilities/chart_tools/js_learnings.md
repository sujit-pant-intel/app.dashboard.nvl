# JS Learnings — Python F-String HTML/JS Templates

Lessons learned while building the CLASS analysis dashboard (`class_analysis_html.py`).

---

## 1. Python f-string `\'` renders as `'`, not `\'`

Inside a triple-double-quoted Python f-string `f"""..."""`, the escape sequence `\'` is just a single-quote character `'` — the backslash is consumed by Python.

| Python source | Rendered output |
|---|---|
| `\'` | `'` |
| `\\'` | `\'` |

**Rule:** To embed a JS-escaped single-quote `\'` in the output, write `\\'` in the Python f-string source.

### Example — JS onclick attribute with a dynamic value

```python
# WRONG: \'  renders as '  → JS sees ''  → broken string concat
rows.push('<tr onclick="_toggleLot(\''+lot+'\')">')

# CORRECT: \\' renders as \' → JS sees \' → proper escaped quote
rows.push('<tr onclick="_toggleLot(\\''+lot+'\\')">'+
```

---

## 2. Never end a Python f-string line with `+'` inside a JS string-building block

A line ending with `+'` opens a new JS single-quoted string literal. The newline that follows (from the Python source) falls **inside** that open string → **SyntaxError: Unterminated string literal** — the entire `<script>` block fails to parse and the page renders blank.

```python
# WRONG: line ends with +'  → opens a JS string that spans the line break
rows.push('<tr onclick="_toggleLot(\\''+lot+'\\')">'+' 
  '<td>...')

# CORRECT: line ends with +  → concat operator, newline is legal whitespace
rows.push('<tr onclick="_toggleLot(\\''+lot+'\\')">'+
  '<td>...')
```

**Rule:** Multi-line JS string concatenation in Python templates must end each intermediate line with `+` (the concat operator), never with `+'` (which opens a dangling string).

---

## 3. JS single-quoted string parsing rules

| Token | Effect |
|---|---|
| `'` | Opens or closes a string |
| `\'` | Escaped single-quote — adds `'` to string content, string stays open |
| `\\` | Escaped backslash — adds `\` to string content |
| literal newline | **SyntaxError** — not allowed inside single-quoted string |
| `+` at end of line | Legal whitespace in expression — string concat continues on next line |

---

## 4. Diagnosis pattern: blank page = JS SyntaxError in `<script>` block

When a dashboard shows **no data / blank** after loading:

1. Open DevTools → Console tab
2. Look for `SyntaxError` (Unterminated string, Unexpected token, etc.)
3. If the script block is large (> 1 MB), a single SyntaxError anywhere in it kills **all** function definitions — `buildWfrList`, `rerender`, everything.

Common culprits in Python f-string templates:
- `\'` used where `\\'` was needed (renders quote wrong)
- `+'` at end of a continuation line (opens unterminated string)
- `{{` / `}}` vs `{` / `}` confusion (double-brace escapes f-string substitution)

---

## 5. Debugging workflow for blank HTML dashboards

```
1. DevTools Console → any SyntaxError? → fix Python f-string escaping
2. DevTools Console → runtime errors (TypeError, undefined)? → logic bug in JS init
3. DevTools Elements → check DOM structure exists (panel divs, tbody)
4. DevTools Elements → check CSS (display:none, height:0, content-visibility:auto)
5. Add console.log at top of init functions to confirm they run
```

### Known gotcha: `content-visibility: auto`

CSS `content-visibility: auto` on chart container divs causes `clientWidth = 0` at draw time → SVG charts draw with zero size. Remove it from `.grp-card` or any element whose width the JS reads before painting.

---

## 6. `!0.0` is `true` in JS — never use bare `!field` to guard a numeric field

Python `0.0` serialises to JSON `0`. In JS, `!0 === true`, so a guard like:

```javascript
if (!info.worst) return;  // WRONG: skips entries where worst = 0.0
```

silently drops all records where the numeric field was initialised to zero but never updated (e.g. LSL-only violations where `worst` tracks USL overages only).

**Rule:** Use explicit numeric guards:

```javascript
if (!info.n_fail || info.n_fail === 0) return;  // correct: check the count
// or
if (info.worst == null || info.worst === 0 && !info.worst_lsl) return;
```

Applied to: `edc_cs['worst']` initialised to `0.0` in Python — LSL-only rails were silently skipped because `!0` is `true`.

---

## 8. `\\'''` (3 quotes after `\\`) in Python f-string → SyntaxError via stray `\`

When interpolating a JS variable inside a single-quoted string (e.g. for `onclick` attribute keys), the correct Python f-string pattern uses **2 quotes** after `\\`, not 3.

| Python source | Rendered JS | JS parse result |
|---|---|---|
| `\\''+var+` | `\''+var+` | ✅ `\'` adds `'`, `'` closes string, `+var+` is outside concat |
| `\\'''+var+` | `\'''+var+` | ❌ `\'` adds `'`, `'` closes, 3rd `'` opens NEW string, `+var+` is literal content, then next `\` is **stray backslash outside string → SyntaxError** |

**Rule:** Never write `\\'''+variable+` in Python f-string templates. Always use `\\''+variable+`.

### Example

```python
# WRONG: three quotes after \\  → renders \''' → SyntaxError
+'<button onclick="_fpYSelAll(\\'''+pid+'\\'\')">'

# CORRECT: two quotes after \\  → renders \''  → correct string split
+'<button onclick="_fpYSelAll(\\''+pid+'\\'\')">'
```

**Diagnosis:** If DevTools shows `SyntaxError: Unexpected token '...'` inside an event handler string, search the Python source for `\\'''` (two backslashes + three single-quotes) and replace with `\\''` (two backslashes + two single-quotes).

**Fix script pattern:**
```python
src = open(py_path, encoding='utf-8').read()
bad  = "\\\\" + "'''"   # two backslashes + three quotes
good = "\\\\" + "''"    # two backslashes + two quotes
fixed = src.replace(bad, good)
```

---

## 9. `\\''` before `)`, `]`, `,` in Python f-string → character falls outside string

When closing a JS single-quoted string that embeds an escaped `'`, using **2 quotes after `\\`** before a special character (`)`, `]`, `,`) prematurely closes the string, leaving the special char as a stray JS token.

| Python source | Rendered JS | JS parse result |
|---|---|---|
| `+'\\'')` | `+'\'')` | ❌ `'` opens S, `\'` adds `'`, `'` **closes S**, `)` is **outside** — stray `"` from HTML opens a DQ string → UNTERMINATED |
| `+'\\'')` (should be) `+'\\')"'` | `+'\')"'` | ✅ `'` opens S, `\'` adds `'`, `)` content, `"` content, `'` closes S — content: `')"` |
| `+'\\'']` | `+'\'']` | ❌ same issue — `]` falls outside string |
| `+'\\']+...+'` | `+'\']+...+'` | ✅ `'` opens, `\'` adds `'`, `]` and rest are content, `'` eventually closes |
| `+'\\'',` | `+'\'',` | ❌ `,` falls outside string |
| `+'\\',\\''+` | `+'\'`,`\''+` | ✅ `','` becomes string content spanning the escape + comma |

**Rule:** In CLOSING segments (after `+variable+`), use **1 quote after `\\`** so the special character becomes string content. In OPENING segments (before `+variable+`), use **2 quotes after `\\`** so the `'` closes the string and `+variable+` is the concat operator.

| Context | Python pattern | Quote count after `\\` |
|---|---|---|
| Opening (before `+var+`) | `\\''+var+` | **2** (escape + close) |
| Closing (before `)` `]` `,`) | `\\')`  `\\']`  `\\',` | **1** (escape, char is content) |

**Fix script pattern:**
```python
import re
# Replace \\'' NOT followed by + (wrong closing) with \\' (correct closing)
src_fixed = re.sub(r"\\\\''(?!\+)", r"\\\\'", src)
```

**Diagnosis:** If DevTools shows an unterminated double-quoted string, look for `)` or `]` falling outside a JS single-quoted string in the rendered HTML. In the Python source, find `\\''` before `)`, `]`, or `,` and remove one `'`.

---

## 10. `'\n'` in Python f-string → literal newline in HTML → UNTERMINATED string

Inside `f"""..."""`, the Python escape `\n` (2 chars: backslash + n) is processed as an **actual newline character** in the output. If this appears inside a JS `'...'` string literal, it becomes an unterminated string.

| Python source | Rendered JS | Result |
|---|---|---|
| `rows.join('\n')` | `rows.join('` + NEWLINE + `')` | ❌ UNTERMINATED — literal newline inside string |
| `rows.join('\\n')` | `rows.join('\n')` | ✅ `\n` is the JS newline escape — valid |

**Rule:** To produce a literal `\n` escape in JS output, write `'\\n'` in the Python f-string source (double-backslash before n).

---

## 7. Normalisation direction for USL vs LSL violations

When plotting "% from limit" for failing measurements:

| Violation | Correct formula | Result sign |
|---|---|---|
| `val > USL` | `(val / USL − 1) × 100` | **positive** |
| `val < LSL` | `(val / LSL − 1) × 100` | **negative** |

Using `(val/USL − 1)*100` for an LSL fail gives deeply negative values (e.g. −1000%) because `val << USL`. Always detect which boundary was crossed first:

```javascript
var pct;
if (pd.usl && v > pd.usl)      pct = (v / pd.usl - 1) * 100;   // +, above USL
else if (pd.lsl && v < pd.lsl) pct = (v / pd.lsl - 1) * 100;   // −, below LSL
else { var lim = pd.usl || pd.lsl; pct = (v / lim - 1) * 100; } // fallback
```

Zero on the Y-axis = exactly at the limit boundary. Positive = above USL. Negative = below LSL.

---

## 8. Separate data structures for different measurement modes — don't conflate them

When a dashboard mixes measurement modes, each mode stores data differently:

| Mode | Storage in die object | Fields available |
|---|---|---|
| K-mode (kill) | `d.pins[]` — per-pin array | `pin`, `val`, `usl`, `lsl`, `phase`, `has_lim` |
| E-mode (EDC) | `d.edc[cs]` — per-configset summary | `n_fail`, `n_total`, `worst`, `worst_lsl`, `usl`, `lsl` |

Iterating `d.pins` for EDC data gives empty results (EDC pins are not in the pins array). Iterating `d.edc` for K-mode per-pin values gives only summary worst-case, not individual measurements.

For variability plots across both modes, build the `pinValMap` in two separate loops:

```javascript
// K-mode: per-pin values
d.pins.forEach(function(p) { /* p.val, p.usl, p.lsl */ });

// EDC: per-CS worst-case (one value per die per configset)
Object.keys(d.edc || {}).forEach(function(cs) {
    var info = d.edc[cs];
    if (info.worst)     /* push info.worst    → USL fail */
    if (info.worst_lsl) /* push info.worst_lsl → LSL fail */
});
```

---

## 9. Pin label grouping strategy for pareto/variability charts

Regex stripping of pin names creates two failure modes:

**Over-aggregation** (regex matches, strips too much):
- `HV04_08_0P85_VCCR` → strip `_0P85_VCCR` → `HV04_08` → strip `_08` → `HV04`
- All HV04 variants are grouped into one row — high count, good for pareto
- But you lose voltage level and signal name information

**Under-aggregation** (regex doesn't match, full name kept):
- `VLC01_0_VCCCORE0ATOM0` has no `_0P\d+_` voltage pattern — keeps full name
- Each ATOM instance is a separate row — counts are fragmented, VLC appears to have low failures even when 343 dies fail a VLC rail

**Fix:** Apply a consistent grouping regex for ALL rails or use full names throughout:

```python
# Consistent: extract just {RAIL}{NN} prefix for grouping
import re
_plabel = re.match(r'^((?:VLC|LC|HV|HC)\d+)', pin_name)
label = _plabel.group(1) if _plabel else pin_name

# Or: use full pin name (no stripping) for identification accuracy
label = pin_name
```

When count-aggregation is the goal, group by base rail+index (`HV04`, `VLC01`).
When signal identification is the goal, keep the full name.

---

## 10. `\\'` without a following variable concat produces `''` — a silent template error

In Python f-string JS templates, `\\'` is the correct way to write an escaped single-quote
in JS output.  The pattern is always paired with a variable concat:

```python
# CORRECT: \\' opens the attribute, +pid+ injects the value, \\' closes it
"onclick=\"_sel(\\'\" + pid + \"\\')\""
#           ^opens        ^closes
```

If the variable concat is accidentally dropped, the output becomes `''` — two
adjacent empty string literals.  JS is silent about this; the onclick fires but
receives an empty string instead of the intended value.

```python
# BUG: both \\' end up adjacent → JS sees ''  (empty string, no id)
"onclick=\"_sel(\\'\\')\")"
```

**Diagnostic:** When searching for `\\'` in the Python source, every occurrence
should be followed (within a few characters) by `+` (a concat operator injecting
a dynamic value).  An isolated `\\'...\\'` with no `+var+` between them is a
dropped substitution.

**Search pattern (PowerShell):**

```powershell
Select-String -Path class_analysis_html.py -Pattern "\\\\'[^+\\\\]{0,6}\\\\'" |
  Select-Object -First 20 |
  ForEach-Object { "$($_.LineNumber): $($_.Line)" }
```

---

## 11. `''px` / `''#` in generated JS — diagnostic indicator of broken string concat

When a generated HTML file contains patterns like `''px`, `''#`, `''rem` inside
JS string concatenation, it means the Python template produced two adjacent empty
string literals where a variable value was expected:

```python
# INTENDED: 'style="left:' + str(x) + 'px"'
# BUG (f-string substitution dropped):
"'style=\"left:''px\"'"     # → JS sees  'style="left:' + '' + 'px"'
                             #              correct structure but value is empty
```

More commonly the `''` comes from a mis-escaped quote where a variable ended up
outside the string boundary:

```python
# Accidentally — quote closed too early
"'left:'+x+''" + "px'"      # → 'left:' + x + '' px'  → SyntaxError or broken
```

**Detection:** After generating the HTML, scan the `<script>` block:

```powershell
$js = (Get-Content dashboard.html -Raw)
[regex]::Matches($js, "''(?:px|#|rem|pt|em|vw|vh)") | ForEach-Object { $_.Value + " at " + $_.Index }
```

Any hit indicates a variable that was supposed to be concatenated was dropped or
the surrounding quotes were unbalanced.

---

## 12. Verifying generated HTML completeness — function presence check

Before debugging at runtime, verify that all expected JS functions actually appear
in the generated HTML.  A Python f-string SyntaxError or a conditional that
short-circuits generation can silently omit entire function bodies:

```python
funcs = [
    '_WFR_LOOKUP', '_grpKeyWith', '_cMapWith', '_olsFit', '_theilsenFit',
    'toggleGbyP', 'buildDistTab', '_buildDistCards', 'toggleGbyFP',
    'fpBuild', '_fpRenderChart', '_initDragCursorsXY', '_fpDownloadCSV'
]
html = open('dashboard.html', encoding='utf-8').read()
for f in funcs:
    print(('OK' if f in html else 'MISSING') + ': ' + f)
print('HTML size:', len(html) // 1024, 'KB')
```

**If a function shows MISSING:**
- Check whether its Python source block is inside a conditional that was false
- Check whether a SyntaxError in a preceding f-string causes early termination
- Check whether the function was renamed but the caller still uses the old name

---

## 13. DOM structure verification before JS debugging

Before investigating JS logic, confirm the HTML scaffolding is present.
If panel divs, tbody elements, or tab containers are missing from the DOM, all
JS that targets them will silently fail with `null` reference errors:

```python
html = open('dashboard.html', encoding='utf-8').read()
s1   = html.index('<script>')
body = html[html.index('<body>'):s1]

# Check structural elements
for tag in ['panel1', 'panel2', 'panel3', 'wfr-tbody', 'tab-var',
            'tab-dist', 'fp-panel', 'grp-grid']:
    print(f'{tag}: {"OK" if tag in body else "MISSING"}')

# Count generated cards
import re
cards = re.findall(r'id="card-grp-([^"]+)"', body)
print('Group cards:', cards)
svgs  = re.findall(r'id="svg-grp-([^"]+)"',  body)
print('SVG targets:', svgs)
```

A card without a matching SVG target means `wmRender` / chart init will
get `null` container and silently skip that group.

---

## 14. Isolating a specific generated line with a scratch script

When the Python template source is a complex multi-line f-string and you need
to see **exactly** what a particular line renders to in the output HTML, write
a minimal scratch script rather than tracing the template by eye:

```python
# C:\temp\parse_line236.py
html  = open(r'path\to\dashboard.html', encoding='utf-8').read()
s     = html.index('<script>')
e     = html.rindex('</script>')
js    = html[s+8:e]
lines = js.split('\n')

# Inspect lines around the function of interest
TARGET = 'fpBuild'
start  = next(i for i, l in enumerate(lines) if 'function ' + TARGET in l)
for i in range(start, min(start + 80, len(lines))):
    l = lines[i]
    # Flag known broken patterns
    flags = []
    if "''px" in l or "''#" in l or "''rem" in l: flags.append('BROKEN_CONCAT')
    if "onmouseover" in l and "''" in l:           flags.append('BROKEN_HANDLER')
    marker = '  <<< ' + ','.join(flags) if flags else ''
    print(f'{i:4d}: {l[:120]}{marker}')
```

This is faster than adding print statements to the generator and re-running it,
especially when the full HTML takes seconds to build.


---

## 10. `Plotly.react` vs `Plotly.newPlot` for interactive updates

`Plotly.newPlot` creates a fresh plot div from scratch — call once on init.  
`Plotly.react` diffs traces and layout, updates in place — call on filter/dropdown changes.

```javascript
// First render (on tab init)
Plotly.newPlot('div-id', traces, layout, config);

// Subsequent updates (on user input)
Plotly.react('div-id', traces, layout, config);  // no flicker, same axes
```

`Plotly.react` requires the **full layout object** on every call — it does not remember the previous layout. Passing `{}` resets axis titles, margins, etc.

Also: if `traces` is an empty array, `Plotly.react` will crash in some versions. Pass a dummy trace as fallback:

```javascript
Plotly.react('div-id', traces.length ? traces : [{type:'scatter',x:[],y:[]}], layout, config);
```

---

## 6. `console.log` in PowerShell → Exit Code 1

Typing JS debug statements directly into a PowerShell terminal fails silently and exits 1:

```powershell
# WRONG — PowerShell sees 'console.log' as a command, rest as args → errors
console.log('rows:', rows)    # Exit Code: 1
```

**Rule:** JS debug output (`console.log`, `debugger`, etc.) must go into the **browser DevTools Console**, not a shell terminal. Use the DevTools Sources tab to set breakpoints in generated HTML.

---

## 7. Blank-page diagnosis: Python one-liner inspection scripts

When a generated HTML dashboard renders blank, use these Python one-liners against the output `.html` file to isolate the problem before opening DevTools:

```python
import re
html = open(r'path\to\output.html', encoding='utf-8').read()

# Step 1 — check embedded var declarations exist
vars_found = re.findall(r'var ([A-Z_][A-Z_0-9]+)\s*=', html)
print('Vars:', vars_found[:20])                        # expect ROW_DATA, WFR_DATA, _COLOUR_PAL …

# Step 2 — check DOM structure (body before <script>)
body = html[html.index('<body>'):html.index('<script>')]
for tag in ['panel1', 'panel2', 'panel3', 'wfr-tbody', 'tab-var']:
    print(f'{tag}:', tag in body)                      # all must be True

# Step 3 — check all JS functions got embedded
funcs = ['_WFR_LOOKUP', '_grpKeyWith', '_cMapWith', '_olsFit', 'buildDistTab',
         '_buildDistCards', 'toggleGbyP', 'fpBuild', '_fpRenderChart', '_fpDownloadCSV']
for f in funcs:
    print(('OK' if f in html else 'MISSING') + ': ' + f)
print('HTML size:', len(html)//1024, 'KB')
```

If any function is `MISSING`, the Python f-string template that generates it has a syntax/substitution error causing the section to be skipped silently.

---

## 8. `_COLOUR_PAL` list vs dict — `.length` undefined crash

`_COLOUR_PAL` is defined in Python as a **list** and embedded into JS as a JSON array:

```python
# Python source
_COLOUR_PAL = ['#1f77b4', '#ff7f0e', …]

# Template line
var _COLOUR_PAL = {json.dumps(_COLOUR_PAL, separators=(',',':'))};
# → renders as JS: var _COLOUR_PAL = ["#1f77b4","#ff7f0e",…];
```

**If `_COLOUR_PAL` is accidentally changed to a dict** (e.g. `{group: color}` for colour-by-group), `json.dumps` produces `{…}` — a JS object. Then:

- `_COLOUR_PAL.length` → `undefined`
- `i % undefined` → `NaN`
- `_COLOUR_PAL[NaN]` → `undefined`
- Every colour call returns `undefined` → SVG elements render colourless or crash

**Rule:** Always keep `_COLOUR_PAL` as a flat list. For group→colour lookup, use a separate `_GROUP_COLOUR_MAP` dict and access via `_GROUP_COLOUR_MAP[key]`, never via index.

---

## 11. `onmouseover`/`onmouseout` inline style reset — `''` empty string escaping

To hover-highlight a row/label and reset on mouseout, JS uses `this.style.background=''`.
Inside a Python f-string continuation line that already uses `\\'` for other escapes, the empty-string reset must use `\\'\\''`:

```python
# In Python f-string source (building a JS string via concatenation)
+' onmouseover="this.style.background=\\'#e8f0fe\\'"'
+' onmouseout="this.style.background=\\'\\'">'+label+'</label>';

# Renders to HTML as:
#   onmouseover="this.style.background='#e8f0fe'"
#   onmouseout="this.style.background=''"
```

**Bug pattern to scan for:** `\\'\\''#xxx` or `\\'\\''px` in source → renders as `''#xxx` or `''px` in HTML — a broken value with an extra empty string concatenated before the actual value. This happens when a value variable is accidentally placed after a closing `\\'` instead of replacing it.

Search pattern in Python source to catch this class of bug:

```python
import re
src = open('class_analysis_html.py', encoding='utf-8').read()
# Find \\'' immediately followed by a non-closing char (i.e. value leaked outside)
for m in re.finditer(r"\\\\''(?=[^\"\\\\])", src):
    lno = src[:m.start()].count('\n') + 1
    print(f"Line {lno}: {src[m.start()-10:m.start()+30]!r}")
```

---

## 12. `pid.replace(/[^0-9]/,'')` only removes the FIRST non-digit

`String.replace` with a non-global regex removes only the **first** match.
For `pid = 'xyp12b'`:

```javascript
parseInt('xyp12b'.replace(/[^0-9]/,''))   // → parseInt('yp12b') → NaN  ← WRONG
parseInt('xyp12b'.replace(/[^0-9]/g,''))  // → parseInt('12')    → 12   ← CORRECT
```

Without the `g` flag, `xyp12b` → `yp12b` → `NaN` → `PCM_XY_PANELS[NaN]` is `undefined` → `fpBuild` returns silently, chart never renders.

**Rule:** Always use `/g` when stripping characters from a string via `replace`. Use `replace(/[^0-9]/g,'')` to extract the numeric index from a panel id.

---

## 13. Extract a dedicated debug script when one-liners get unwieldy

Python one-liners passed via `-c` cannot contain multi-line `try/except`, complex regex loops, or `\` continuations reliably across shells. When the inspection logic exceeds ~5 lines, write `C:\temp\parse_lineN.py` and run it:

```powershell
# Instead of a fragile -c "..." multi-liner:
python C:\temp\parse_line236.py
```

Template for line-inspection debug scripts:

```python
# C:\temp\parse_line236.py
import re
src = open(r'C:\scripts\...\class_analysis_html.py', encoding='utf-8').read()
lines = src.splitlines()

TARGET = 236 - 1  # 0-based
line = lines[TARGET]
print(f"Line {TARGET+1} ({len(line)} chars):")
print(repr(line))

# Find specific sub-patterns
for m in re.finditer(r"\\'\\\\''(?P<after>.{0,20})", line):
    print(f"  pos {m.start()}: after={m.group('after')!r}")
```

Exit code 0 = the script ran without exception (even if it printed nothing — add explicit `print('done')` to distinguish "no matches" from "script failed").

---

## 15. `\\'px\\'` — both quotes must be escaped when building `+'px'` inside a JS builder string

Inside a Python f-string building JS `+`-concatenated HTML, a JS string literal like `'px'` requires **both** opening and closing single quotes to be `\\'`:

```python
# Slider oninput that appends 'px' to a numeric value
+' oninput="...textContent=this.value+\\'px\\';..."'
#                                      ^^^^  ^^^^
#                 Python \\' → renders as \'  (both sides)
#                 JS output: this.value+'px'  ← correct

# WRONG variants and their breakage:
+' oninput="...textContent=this.value+\\'px\'..."'   # \\' + \' → \'px'  → opens unclosed JS string
+' oninput="...textContent=this.value+\'px\\'..."'   # \' + \\' → 'px\'  → 'px followed by a \' escape, string never closes
+' oninput="...textContent=this.value+\'px\'..."'    # \' + \'  → 'px'   → looks OK but \' rendered in HTML as just ' → attribute double-quote mismatch
```

**Rule:** Every JS `'string'` literal embedded inside a JS `+`-concat line in a Python f-string needs `\\'...\\'` — both the opening and closing quote escaped with double-backslash.

---

## 16. JS function verification checklist for `class_analysis_html.py` (updated)

When debugging a newly generated CLASS dashboard, this is the full function list to verify is present in the HTML:

```python
funcs = [
    # Core init / layout
    '_WFR_LOOKUP', '_grpKeyWith', '_cMapWith', '_olsFit', '_theilsenFit',
    'buildParamTable', 'buildDistTab', '_buildDistCards', 'drawAllCharts',
    # Toggle / interaction
    'toggleGbyP', 'selParam', 'toggleDistP', '_syncGbyBtnsP',
    # FP (XY scatter panel)
    'fpBuild', 'toggleGbyFP', '_fpRenderChart', '_fpDownloadCSV',
    '_fpToggleY', '_fpYDropToggle', '_fpYSearch', '_fpYSelAll', '_fpYClrAll',
    '_fpUpdateYBtn',
    # Drag cursors
    '_initDragCursorsXY',
]
html = open(r'path\to\output.html', encoding='utf-8').read()
for f in funcs:
    print(('OK  ' if f in html else 'MISS') + ': ' + f)
print('HTML size:', len(html) // 1024, 'KB')
```

Any `MISS` means the Python section that generates that function block has a substitution or escaping error — it was silently dropped during f-string evaluation. Find the function in `class_analysis_html.py`, check for unmatched `{{`/`}}` or invalid Python expressions inside `{...}`.

---

## 17. `-c` multi-line shell scripts fail with exit code 1 — use a file instead

PowerShell `-c "..."` multi-line Python one-liners break when:
- The script exceeds ~5 lines
- There are nested backslash sequences in the string (`\\\\''`, `\n`, etc.)
- An `except` / `try` block is needed

All these manifest as **exit code 1** with no helpful message. The fix is always the same — write to `C:\temp\debug_N.py` and run that:

```powershell
# WRONG — exits 1 silently on complex scripts
python -c "
import re
src = open(...)...
for m in re.finditer(r\"\\\\\\\\''\"...):
    ...
"

# CORRECT — write the script, run it
python C:\temp\debug_scan.py   # exit code 0 = ran clean
```

Add `print('done')` at the end of every scratch script so you can distinguish "ran clean, no matches" from "crashed partway through".
