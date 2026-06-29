from pathlib import Path

p = Path(r'\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\yield\output\NVL_0H61_20260519_060001\report.html')
if not p.exists():
    print(f"ERROR: not found: {p}")
else:
    html = p.read_text(encoding='utf-8')
    orig = len(html)
    # Change .cmp-tbl font-size from 0.82em to 0.95em
    html2 = html.replace('.cmp-tbl { border-collapse:collapse; font-size:0.82em;', '.cmp-tbl { border-collapse:collapse; font-size:0.95em;')
    # Remove font-size:0.82em from .cmp-tbl th (various formats)
    html2 = html2.replace('white-space:nowrap; font-size:0.82em; }', 'white-space:nowrap; }')
    changed = html != html2
    p.write_text(html2, encoding='utf-8')
    print(f"Patched={changed}, orig={orig} bytes, new={len(html2)} bytes")
    # verify
    check = p.read_text(encoding='utf-8')
    print(f"0.82em remaining: {'0.82em' in check}")
    print(f"0.95em present: {'0.95em' in check}")
