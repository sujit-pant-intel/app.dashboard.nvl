"""
_lot_tracker.py
===============
Shows all BLLC lots shipping in next 2 weeks (WW19-WW21)
SOD dates from WW19 email + material details from 8PF5CV CSV.
"""
import csv, email, os, re

# ── Material CSV ──────────────────────────────────────────────────────────────
MAT = r'C:\scripts\app.yield.nvl\shared\material\8PF5CV-NVL816-BLLC_L0_lot_definition_l1.csv'
with open(MAT, encoding='utf-8-sig') as f:
    rows = list(csv.reader(f))

mat_lookup = {}
for r in rows[1:]:
    tsmc = r[4].strip()
    if tsmc and tsmc not in mat_lookup:
        mat_lookup[tsmc] = {
            'intel':   r[5].strip(),
            'lot_num': r[0].strip(),
            'aiobb':   r[9].strip(),
            'mat':     r[2].strip(),   # short: AIO / BB CIP
            'mat_full':r[1].strip(),   # full: NVL816-BLLC-L0 AIO+BB...
            'mg4':     r[10].strip(),
            'skew':    r[11].strip(),
            'remark':  r[13].strip(),
            'wfs':     sum(1 for x in rows[1:] if x[4].strip() == tsmc),
        }

# ── WW19 email ───────────────────────────────────────────────────────────────
folder = r'C:\scripts\app.yield.nvl\docs\lot-tracking\nvl816-bllc'
fname  = '[TSMC N2P] NVL CPU 816 BLLC L0 Lot tracking & WIP Progress - WW19.eml'
with open(os.path.join(folder, fname), 'rb') as f:
    msg = email.message_from_bytes(f.read())
body = ''
for part in msg.walk():
    if part.get_content_type() == 'text/plain':
        payload = part.get_payload(decode=True)
        charset = part.get_content_charset() or 'utf-8'
        body += payload.decode(charset, errors='replace')

lines = body.splitlines()

# First table: ends at second 'Ship Out Date' header
sod_markers = [i for i, l in enumerate(lines) if 'Ship Out Date' in l]
first_table_end = sod_markers[1] if len(sod_markers) > 1 else len(lines)
section = lines[:first_table_end]

# ── Parse all lots from first table ──────────────────────────────────────────
# Strategy: scan for TSMC lot patterns (K8A/K9H/K9K/K0A + 3 alphanum)
LOT_RE = re.compile(r'^(K[89][A-Z0-9]{4}|K0[A-Z][0-9]{3})\.\d{2}$')

entries = []  # list of (tsmc6, lot_label, sod_ww, sod_date)
i = 0
while i < len(section):
    l = section[i].strip()
    if LOT_RE.match(l):
        tsmc_full = l          # e.g. K8A218.00
        tsmc6     = l[:6]      # e.g. K8A218

        # Look back for lot label
        lot_label = ''
        for j in range(i-1, max(0, i-15), -1):
            cand = section[j].strip()
            if cand.startswith('Lot ') or cand.startswith('SHL') or cand.startswith('QRV'):
                lot_label = cand
                break

        # Look forward for date + WW — grab the date immediately before the first WW line
        sod_date = ''
        sod_ww   = ''
        last_date_seen = ''
        for j in range(i+1, min(len(section), i+20)):
            cand = section[j].strip()
            if re.match(r'^\d{4}/\d{2}/\d{2}$', cand):
                last_date_seen = cand
            if cand.startswith('WW') and not sod_ww:
                sod_ww   = cand
                sod_date = last_date_seen  # date just before this WW = SOD date

        entries.append((tsmc6, lot_label, sod_ww, sod_date, tsmc_full))
    i += 1

# Deduplicate: keep LAST entry per tsmc6 (= most final SOD step)
seen = {}
for tsmc6, lot_label, sod_ww, sod_date, tsmc_full in entries:
    seen[tsmc6] = (lot_label, sod_ww, sod_date, tsmc_full)

# Filter to WW19-WW21
TARGET_WW = {'WW19', 'WW20', 'WW21'}
filtered = {
    tsmc6: v for tsmc6, v in seen.items()
    if v[1].split('.')[0] in TARGET_WW
}

# Sort by WW then day
def sort_key(item):
    ww = item[1][1]  # e.g. 'WW20.Mon'
    day_order = {'Sun':0,'Mon':1,'Tue':2,'Wed':3,'Thu':4,'Fri':5,'Sat':6}
    parts = ww.split('.')
    ww_num = int(parts[0][2:]) if len(parts) > 0 and parts[0][2:].isdigit() else 99
    day_str = parts[1][:3] if len(parts) > 1 else 'ZZZ'
    return (ww_num, day_order.get(day_str, 9))

sorted_entries = sorted(filtered.items(), key=sort_key)

# ── Print ─────────────────────────────────────────────────────────────────────
print(f"\n{'Lot Label':<32} {'TSMC':<10} {'Intel LOT7':<10} {'Wfs':>4}  {'Date':<12} {'WW SOD':<14} Material Type, Skew, BEOL Skew")
print('=' * 130)
prev_ww = ''
for tsmc6, (lot_label, sod_ww, sod_date, tsmc_full) in sorted_entries:
    ww = sod_ww.split('.')[0]
    if ww != prev_ww:
        print(f'\n  ── {ww} ──────────────────────────────────────────────────────────────────────')
        prev_ww = ww

    m = mat_lookup.get(tsmc6, {})
    intel    = m.get('intel',    '???')
    wfs      = m.get('wfs',      '?')
    mat_full = m.get('mat_full', '?')
    label    = lot_label or m.get('lot_num', tsmc6)

    print(f"  {label:<32} {tsmc_full:<10} {intel:<10} {str(wfs):>4}  {sod_date:<12} {sod_ww:<14} {mat_full}")

print()
print(f"Total lots in WW19-WW21: {len(sorted_entries)}")
