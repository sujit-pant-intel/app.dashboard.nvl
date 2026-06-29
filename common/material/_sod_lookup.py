import csv

with open(r'C:\scripts\app.yield.nvl\shared\material\8PF5CV-NVL816-BLLC_L0_lot_definition_l1.csv', encoding='utf-8-sig') as f:
    rows = list(csv.reader(f))

# Build lookup: TSMC_LOT prefix -> first matching row
lookup = {}
for r in rows[1:]:
    tsmc = r[4].strip()
    if tsmc and tsmc not in lookup:
        lookup[tsmc] = r

sod_lots = [
    ('Lot 1',         'K8A233', 'WW19.Sun'),
    ('Lot 2',         'K8A234', 'WW19.Tue'),
    ('Lot 5',         'K8A210', 'WW19.Thu'),
    ('Lot 6',         'K8A211', 'WW19.Thu'),
    ('Lot 10',        'K8A217', 'WW19.Thu'),
    ('Lot 13',        'K8A220', 'WW19.Sat'),
    ('Lot 16',        'K8A223', 'WW19.Fri'),
    ('Lot 17',        'K8A224', 'WW19.Fri'),
    ('Lot 7.2 MK',    'K9H922', 'WW19.Fri'),
    ('Lot 7',         'K8A212', 'WW20.Mon'),
    ('Lot 8 BB CIP',  'K8A213', 'WW20.Thu'),
    ('Lot 12',        'K8A219', 'WW20.Mon'),
    ('Lot 18',        'K8A225', 'WW20.Tue'),
    ('Lot 20',        'K8A227', 'WW20.Mon'),
    ('Lot 21',        'K8A228', 'WW20.Mon'),
    ('Lot 11 BB CIP', 'K8A218', 'WW21.Fri'),
    ('Lot 14',        'K8A221', 'WW21.Tue'),
    ('Lot 15 BB CIP', 'K8A222', 'WW21.Tue'),
    ('Lot 19 BB CIP', 'K8A226', 'WW21.Sat'),
]

print(f"{'Lot':<22} {'TSMC ID':<10} {'Intel LOT7':<10} {'AIO/BB':<6} {'Material Type/Skew':<34} {'Remark':<22} SOD")
print('-' * 130)
prev_ww = ''
for lot_label, tsmc6, sod in sod_lots:
    ww = sod.split('.')[0]
    if ww != prev_ww:
        print(f'\n  --- {ww} ---')
        prev_ww = ww
    match = next((r for t, r in lookup.items() if t.startswith(tsmc6)), None)
    if match:
        intel  = match[5].strip()
        aiobb  = match[9].strip()
        mat    = match[1].strip()
        remark = match[13].strip()
        wcount = sum(1 for r in rows[1:] if r[4].strip().startswith(tsmc6))
        tid    = tsmc6 + '.00'
        print(f"  {lot_label:<22} {tid:<10} {intel:<10} {aiobb:<6} {mat:<34} {remark:<22} {sod}  ({wcount} wfs)")
    else:
        print(f"  {lot_label:<22} {tsmc6+'.00':<10} {'NOT FOUND':<10} {'':<6} {'':<34} {'':<22} {sod}")
