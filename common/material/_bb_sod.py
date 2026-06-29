import csv, email, os

# ── Material CSV ──────────────────────────────────────────────────────────────
MAT = r'C:\scripts\app.yield.nvl\shared\material\8PF5CV-NVL816-BLLC_L0_lot_definition_l1.csv'
with open(MAT, encoding='utf-8-sig') as f:
    rows = list(csv.reader(f))

bb_lots = {}
for r in rows[1:]:
    tsmc = r[4].strip()
    if tsmc and r[9].strip() == 'BB CIP' and tsmc not in bb_lots:
        bb_lots[tsmc] = {
            'intel':   r[5].strip(),
            'lot_num': r[0].strip(),
            'remark':  r[13].strip(),
            'mat':     r[1].strip(),
            'mg4':     r[10].strip(),
            'skew':    r[11].strip(),
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

# Use only first table (ends at second 'Ship Out Date' header)
sod_markers = [i for i, l in enumerate(lines) if 'Ship Out Date' in l]
first_table_end = sod_markers[1] if len(sod_markers) > 1 else len(lines)
section = lines[:first_table_end]

import re as _re

def get_final_sod(tsmc6):
    """Find the LAST occurrence block of tsmc6 in first table.
    Return (ww, actual_date, lot_label)."""
    last_ww       = None
    last_date     = None
    last_lot_label = ''
    for i, l in enumerate(section):
        if tsmc6 in l:
            ctx_after  = section[i:min(len(section), i+15)]
            ctx_before = section[max(0, i-12):i]
            ww = [x.strip() for x in ctx_after if x.strip().startswith('WW')]
            # actual date: YYYY/MM/DD lines just before the WW line
            dates = [x.strip() for x in ctx_after
                     if _re.match(r'^\d{4}/\d{2}/\d{2}$', x.strip())]
            if ww:
                last_ww = ww[-1]
            if dates:
                last_date = dates[-1]
            lot_label = next((x.strip() for x in reversed(ctx_before)
                              if x.strip().startswith('Lot ')), '')
            if lot_label:
                last_lot_label = lot_label
    return last_ww or 'n/a', last_date or '', last_lot_label

# ── Print sorted by SOD ───────────────────────────────────────────────────────
results = []
for tsmc in sorted(bb_lots.keys()):
    v = bb_lots[tsmc]
    sod, actual_date, email_label = get_final_sod(tsmc)
    label = email_label or v['lot_num']
    results.append((sod, actual_date, label, tsmc, v))

print(f"{'Lot Label':<36} {'TSMC':<10} {'Intel LOT7':<10} {'Wfs':>4}  {'Date':<12} {'WW SOD':<14} {'Material / Skew'}")
print('-' * 120)
prev_ww = ''
for sod, actual_date, label, tsmc, v in sorted(results, key=lambda x: x[0]):
    ww = sod.split('.')[0]
    if ww != prev_ww:
        print(f'\n  [{ww}]')
        prev_ww = ww
    mat_info = v['mat']
    if v['mg4']:  mat_info += f"  MG4={v['mg4']}"
    if v['skew']: mat_info += f"  Skew={v['skew']}"
    print(f"  {label:<36} {tsmc:<10} {v['intel']:<10} {v['wfs']:>4}  {actual_date:<12} {sod:<14} {mat_info}")

