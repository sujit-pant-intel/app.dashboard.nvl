from pathlib import Path

p = Path(r'\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\users\snpant\auto\yield\output\NVL_0H61_20260519_060001\report.html')
if not p.exists():
    print(f"ERROR: not found: {p}")
else:
    html = p.read_text(encoding='utf-8')
    orig = len(html)
    
    # We saw .cmp-tbl has 0.95em already in the previous check, 
    # but the user script said it was looking for 0.82em.
    # Let's perform a broad replace for 0.82em to something else if it still exists 
    # and maybe the user wants to ensure .cmp-tbl th doesn't have a font-size either.
    
    html2 = html.replace('font-size:0.82em;', '')
    
    changed = html != html2
    p.write_text(html2, encoding='utf-8')
    print(f"Patched={changed}, orig={orig} bytes, new={len(html2)} bytes")
    # verify
    check = p.read_text(encoding='utf-8')
    print(f"0.82em remaining: {'0.82em' in check}")
    print(f"0.95em present: {'0.95em' in check}")
