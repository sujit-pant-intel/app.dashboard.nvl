"""
manage_dashboard.py

GUI to manage Dashboard.html entries.
- Load a Dashboard.html file
- List all identifier blocks with their timestamps
- Delete a block from Dashboard.html
- Delete the corresponding output folder (if it exists)
"""

import os
import re
import shutil
import glob
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


# ── HTML parsing helpers ────────────────────────────────────────────────────


# Sentinel pairs for all three sections (new format) plus legacy
_SECTION_PATTERNS = [
    (r'<!--\s*YIELD_START\s*-->', r'<!--\s*YIELD_END\s*-->',   'YIELD'),
    (r'<!--\s*COMPARE_START\s*-->', r'<!--\s*COMPARE_END\s*-->', 'COMPARE'),
    (r'<!--\s*VMIN_START\s*-->',  r'<!--\s*VMIN_END\s*-->',    'VMIN'),
    (r'<!--\s*RUNS_START\s*-->',  r'<!--\s*RUNS_END\s*-->',    'YIELD'),  # legacy
]


def _all_runs_html(html: str) -> str:
    """Concatenate inner content of all known sentinel sections."""
    parts = []
    for start_re, end_re, _ in _SECTION_PATTERNS:
        m = re.search(start_re + r'(.*?)' + end_re, html, re.S)
        if m:
            parts.append(m.group(1))
    return '\n'.join(parts)


def parse_blocks(html: str) -> list[dict]:
    """Return list of dicts with keys: stem, label, ts, html, folder_hint."""
    runs_html = _all_runs_html(html)
    if not runs_html.strip():
        return []

    blocks = []
    # Each block: <div class="run-block" data-stem="...">...</div>
    for bm in re.finditer(
        r'<div class="run-block"\s+data-stem="([^"]*)">(.*?)</div>\s*</div>',
        runs_html, re.S
    ):
        stem = bm.group(1)
        inner = bm.group(2)

        # label from run-header text (after the arrow span)
        lm = re.search(r'<div class="run-header"[^>]*>.*?</span>\s*([^<]+)', inner, re.S)
        label = lm.group(1).strip() if lm else stem

        # timestamp
        tm = re.search(r'<span class="ts">\s*-\s*([^<]+)</span>', inner)
        ts = tm.group(1).strip() if tm else ''

        # first href — used to guess output folder
        hm = re.search(r'href="((?!file://)[^"]+)"', inner)
        first_href = hm.group(1) if hm else ''

        blocks.append({
            'stem': stem,
            'label': label,
            'ts': ts,
            'first_href': first_href,
            'full_div': bm.group(0),
        })
    return blocks


def resolve_output_folder(dashboard_html_path: str, first_href: str) -> str | None:
    """Derive the output folder path from the first relative href in the block."""
    if not first_href:
        return None
    # href is relative to the Dashboard.html directory
    base = os.path.dirname(dashboard_html_path)
    # strip the filename part (e.g. "52A/NCXEBJX.../index.html" → "52A/NCXEBJX.../")
    folder = os.path.normpath(os.path.join(base, os.path.dirname(first_href)))
    return folder if os.path.isdir(folder) else folder  # return even if not yet present


def remove_block(html: str, stem: str) -> str:
    """Remove the run-block div with data-stem == stem from any section."""
    escaped = re.escape(stem)
    pattern = (
        r'[ \t]*<div class="run-block"\s+data-stem="' + escaped +
        r'">' + r'.*?</div>\s*</div>\s*'
    )
    return re.sub(pattern, '', html, flags=re.S)


def section_type_of_block(html: str, stem: str) -> str | None:
    """Return the section type ('YIELD', 'COMPARE', 'VMIN') a block belongs to."""
    escaped = re.escape(stem)
    block_re = re.compile(
        r'<div class="run-block"\s+data-stem="' + escaped + r'">',
        re.S)
    for start_re, end_re, sec_type in _SECTION_PATTERNS:
        m = re.search(start_re + r'(.*?)' + end_re, html, re.S)
        if m and block_re.search(m.group(1)):
            return sec_type
    return None


def resolve_block_files(dashboard_html_path: str, block: dict) -> list[str]:
    """Return list of absolute file paths referenced by href in a block's HTML."""
    base = os.path.dirname(dashboard_html_path)
    files = []
    for hm in re.finditer(r'href="((?!file://|http)[^"]+)"', block.get('full_div', '')):
        rel = hm.group(1)
        fpath = os.path.normpath(os.path.join(base, rel))
        files.append(fpath)
    return files


def _section_of_block(html: str, stem: str) -> tuple[str, str] | tuple[None, None]:
    """Return (start_sentinel_literal, end_sentinel_literal) for the section
    that contains the given block stem, searching all known sections."""
    escaped = re.escape(stem)
    block_re = re.compile(
        r'<div class="run-block"\s+data-stem="' + escaped + r'">',
        re.S)
    for start_re, end_re, _ in _SECTION_PATTERNS:
        m = re.search(start_re + r'(.*?)' + end_re, html, re.S)
        if m and block_re.search(m.group(1)):
            # Return the actual matched sentinel strings
            full = re.search(start_re + r'.*?' + end_re, html, re.S)
            if full:
                sm = re.search(start_re, html)
                em = re.search(end_re, html)
                if sm and em:
                    return html[sm.start():sm.end()], html[em.start():em.end()]
    return None, None


def reorder_blocks(html: str, ordered_blocks: list[dict]) -> str:
    """Reorder blocks within their respective sections.
    Blocks belonging to the same section are reordered together.
    New format: YIELD/COMPARE/VMIN sentinels.  Legacy: RUNS sentinels."""
    # Group the ordered_blocks by the section they belong to
    for start_re, end_re, _ in _SECTION_PATTERNS:
        m = re.search(start_re + r'(.*?)' + end_re, html, re.S)
        if not m:
            continue
        section_html = m.group(1)
        # Find which blocks from ordered_blocks live in this section
        section_blocks = []
        for b in ordered_blocks:
            escaped = re.escape(b['stem'])
            if re.search(r'<div class="run-block"\s+data-stem="' + escaped + r'">', section_html, re.S):
                section_blocks.append(b)
        if not section_blocks:
            continue
        new_section = '\n' + ''.join(b['full_div'] + '\n' for b in section_blocks)
        html = re.sub(
            start_re + r'.*?' + end_re,
            lambda mo, ns=new_section, sr=start_re, er=end_re: (
                re.search(sr, mo.group(0)).group(0) + ns +
                re.search(er, mo.group(0)).group(0)
            ),
            html, flags=re.S, count=1
        )
    return html


# ── Main GUI ────────────────────────────────────────────────────────────────


class ManageFrame(tk.Frame):
    def __init__(self, parent=None, **kw):
        super().__init__(parent, bg='#1a252f', **kw)
        self._html_path: str = ''
        self._html: str = ''
        self._blocks: list[dict] = []
        self._build_ui()

    # ── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self):
        BG  = '#1a252f'
        BG2 = '#2c3e50'
        FG  = '#ecf0f1'
        ABLU = '#3498db'

        # ── top bar ──
        top = tk.Frame(self, bg=BG)
        top.pack(fill=tk.X, padx=8, pady=6)

        self._path_var = tk.StringVar()
        tk.Label(top, text='Dashboard.html:', bg=BG, fg=FG,
                 font=('Arial', 9)).pack(side=tk.LEFT)
        tk.Entry(top, textvariable=self._path_var, width=60,
                 bg=BG2, fg='white', insertbackground='white',
                 relief='flat', font=('Consolas', 9)).pack(side=tk.LEFT, padx=(4, 4))
        tk.Button(top, text='Browse…', command=self._browse,
                  bg='#1f618d', fg='white', relief='flat',
                  font=('Arial', 9), padx=6).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(top, text='Load', command=self._load,
                  bg='#27ae60', fg='white', relief='flat',
                  font=('Arial', 9, 'bold'), padx=8).pack(side=tk.LEFT)

        # ── table ──
        cols = ('label', 'ts', 'folder')
        frm = tk.Frame(self, bg=BG)
        frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        style = ttk.Style()
        style.theme_use('default')
        style.configure('Dark.Treeview',
                        background=BG2, foreground=FG,
                        fieldbackground=BG2, rowheight=24,
                        font=('Consolas', 9))
        style.configure('Dark.Treeview.Heading',
                        background='#34495e', foreground=FG,
                        font=('Arial', 9, 'bold'), relief='flat')
        style.map('Dark.Treeview',
                  background=[('selected', '#2980b9')],
                  foreground=[('selected', 'white')])

        self._tree = ttk.Treeview(frm, columns=cols, show='headings',
                                  style='Dark.Treeview', selectmode='browse')
        self._tree.heading('label',  text='Identifier / Label')
        self._tree.heading('ts',     text='Timestamp')
        self._tree.heading('folder', text='Output Folder')
        self._tree.column('label',  width=280, anchor='w')
        self._tree.column('ts',     width=140, anchor='w')
        self._tree.column('folder', width=420, anchor='w')

        vsb = ttk.Scrollbar(frm, orient='vertical', command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # ── action buttons ──
        btn_frm = tk.Frame(self, bg=BG)
        btn_frm.pack(fill=tk.X, padx=8, pady=(0, 6))

        # Reorder buttons
        tk.Button(btn_frm, text='⬆ Top',
                  command=self._move_top,
                  bg='#1f618d', fg='white', relief='flat',
                  font=('Arial', 9), padx=8).pack(side=tk.LEFT, padx=(0, 2))
        tk.Button(btn_frm, text='▲ Up',
                  command=self._move_up,
                  bg='#1f618d', fg='white', relief='flat',
                  font=('Arial', 9), padx=8).pack(side=tk.LEFT, padx=(0, 2))
        tk.Button(btn_frm, text='▼ Down',
                  command=self._move_down,
                  bg='#1f618d', fg='white', relief='flat',
                  font=('Arial', 9), padx=8).pack(side=tk.LEFT, padx=(0, 2))
        tk.Button(btn_frm, text='⬇ Bottom',
                  command=self._move_bottom,
                  bg='#1f618d', fg='white', relief='flat',
                  font=('Arial', 9), padx=8).pack(side=tk.LEFT, padx=(0, 12))

        tk.Button(btn_frm, text='Delete from Dashboard.html',
                  command=self._delete_html_entry,
                  bg='#922b21', fg='white', relief='flat',
                  font=('Arial', 9, 'bold'), padx=10).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_frm, text='Delete Output Folder',
                  command=self._delete_folder,
                  bg='#7d3c98', fg='white', relief='flat',
                  font=('Arial', 9, 'bold'), padx=10).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_frm, text='Delete Both',
                  command=self._delete_both,
                  bg='#c0392b', fg='white', relief='flat',
                  font=('Arial', 9, 'bold'), padx=10).pack(side=tk.LEFT, padx=(0, 6))


        tk.Button(btn_frm, text='Delete Compare Files…',
                  command=self._delete_compare_files,
                  bg='#784212', fg='white', relief='flat',
                  font=('Arial', 9, 'bold'), padx=10).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_frm, text='Delete Vmin Files…',
                  command=self._delete_vmin_files,
                  bg='#117a65', fg='white', relief='flat',
                  font=('Arial', 9, 'bold'), padx=10).pack(side=tk.LEFT, padx=(0, 6))

        # ── status bar ──
        self._status_var = tk.StringVar(value='Load a Dashboard.html to begin.')
        tk.Label(self, textvariable=self._status_var, bg='#151e27', fg='#95a5a6',
                 font=('Consolas', 8), anchor='w', padx=6).pack(fill=tk.X, side=tk.BOTTOM)

    # ── actions ─────────────────────────────────────────────────────────────

    def _browse(self):
        path = filedialog.askopenfilename(
            title='Select Dashboard.html',
            filetypes=[('HTML files', '*.html'), ('All files', '*.*')]
        )
        if path:
            self._path_var.set(path)
            self._load()

    def _load(self):
        path = self._path_var.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showerror('Error', f'File not found:\n{path}')
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                self._html = f.read()
            self._html_path = path
        except Exception as e:
            messagebox.showerror('Error', f'Failed to read file:\n{e}')
            return

        self._blocks = parse_blocks(self._html)
        self._refresh_tree()
        self._status_var.set(f'Loaded {len(self._blocks)} block(s) from {path}')

    def _refresh_tree(self):
        for row in self._tree.get_children():
            self._tree.delete(row)
        for i, b in enumerate(self._blocks):
            folder = resolve_output_folder(self._html_path, b['first_href'])
            exists = '✓' if folder and os.path.isdir(folder) else '✗'
            if folder:
                # Relative path from Dashboard.html dir, strip leading 'output/'
                _html_dir = os.path.dirname(self._html_path) if self._html_path else ''
                try:
                    _rel = os.path.relpath(folder, _html_dir).replace('\\', '/') if _html_dir else ''
                except ValueError:
                    _rel = ''
                if _rel.startswith('output/'):
                    _rel = _rel[len('output/'):]
                _parts = [p for p in _rel.split('/') if p and p not in ('.', '..')]
                if len(_parts) >= 2:
                    # New 2-level structure: e.g. NVL_0H61A_20260522/NCXSDJXL0H61A002618_119325
                    _short = '/'.join(_parts[-2:])
                else:
                    # Old flat-output: use block timestamp as the "folder" prefix
                    _leaf = _parts[-1] if _parts else os.path.basename(folder.rstrip('/\\'))
                    _ts   = b.get('ts', '').strip()
                    _short = f'{_ts}/{_leaf}' if _ts else _leaf
                folder_disp = f'[{exists}] {_short}'
            else:
                folder_disp = '—'
            self._tree.insert('', tk.END, iid=str(i),
                               values=(b['label'], b['ts'], folder_disp))

    def _selected_block(self) -> dict | None:
        sel = self._tree.selection()
        if not sel:
            messagebox.showwarning('No selection', 'Select an entry first.')
            return None
        return self._blocks[int(sel[0])]

    def _delete_html_entry(self):
        block = self._selected_block()
        if not block:
            return
        if not messagebox.askyesno('Confirm',
                f'Remove this entry from Dashboard.html?\n\n{block["label"]}'):
            return
        self._html = remove_block(self._html, block['stem'])
        self._save_html()
        self._blocks = parse_blocks(self._html)
        self._refresh_tree()
        self._status_var.set(f'Removed entry: {block["label"]}')

    def _delete_folder(self):
        block = self._selected_block()
        if not block:
            return
        sec_type = section_type_of_block(self._html, block['stem'])

        # COMPARE blocks: delete only the referenced HTML file(s), not the folder
        if sec_type == 'COMPARE':
            files = resolve_block_files(self._html_path, block)
            existing = [f for f in files if os.path.isfile(f)]
            if not existing:
                messagebox.showinfo('Not found',
                    'No compare files found to delete.')
                return
            flist = '\n'.join(os.path.basename(f) for f in existing)
            if not messagebox.askyesno('Confirm',
                    f'Delete these compare file(s)?\n\n{flist}'):
                return
            errors = []
            for f in existing:
                try:
                    os.remove(f)
                except Exception as e:
                    errors.append(f'{os.path.basename(f)}: {e}')
            if errors:
                messagebox.showerror('Errors', '\n'.join(errors))
            else:
                self._status_var.set(
                    f'Deleted {len(existing)} compare file(s).')
            self._refresh_tree()
            return

        folder = resolve_output_folder(self._html_path, block['first_href'])
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo('Not found',
                f'Output folder not found or already deleted:\n{folder}')
            return
        if not messagebox.askyesno('Confirm',
                f'Permanently delete this folder and ALL its contents?\n\n{folder}'):
            return
        try:
            shutil.rmtree(folder)
            self._refresh_tree()
            self._status_var.set(f'Deleted folder: {folder}')
        except Exception as e:
            messagebox.showerror('Error', f'Failed to delete folder:\n{e}')

    def _delete_both(self):
        block = self._selected_block()
        if not block:
            return
        sec_type = section_type_of_block(self._html, block['stem'])

        # COMPARE blocks: remove HTML entry + delete referenced file(s) only
        if sec_type == 'COMPARE':
            files = resolve_block_files(self._html_path, block)
            existing = [f for f in files if os.path.isfile(f)]
            flist = '\n'.join(os.path.basename(f) for f in existing) if existing else '(no files found)'
            msg = (f'Remove entry from Dashboard.html AND delete compare file(s)?\n\n'
                   f'{block["label"]}\n\nFiles:\n{flist}')
            if not messagebox.askyesno('Confirm', msg):
                return
            self._html = remove_block(self._html, block['stem'])
            self._save_html()
            errors = []
            for f in existing:
                try:
                    os.remove(f)
                except Exception as e:
                    errors.append(f'{os.path.basename(f)}: {e}')
            # Update compare links in Dashboard.html
            try:
                import pathlib
                import compare_runs as _cr
                _cr.update_dashboard_compare_links(pathlib.Path(self._html_path))
                with open(self._html_path, 'r', encoding='utf-8') as f:
                    self._html = f.read()
            except Exception:
                pass
            self._blocks = parse_blocks(self._html)
            self._refresh_tree()
            if errors:
                messagebox.showerror('Errors', '\n'.join(errors))
            else:
                self._status_var.set(
                    f'Deleted entry + {len(existing)} compare file(s): {block["label"]}')
            return

        folder = resolve_output_folder(self._html_path, block['first_href'])
        folder_exists = folder and os.path.isdir(folder)
        msg = f'Remove entry from Dashboard.html AND delete output folder?\n\n{block["label"]}'
        if folder_exists:
            msg += f'\n\nFolder to delete:\n{folder}'
        else:
            msg += '\n\n(Output folder not found — only HTML entry will be removed.)'
        if not messagebox.askyesno('Confirm', msg):
            return

        self._html = remove_block(self._html, block['stem'])
        self._save_html()

        if folder_exists:
            try:
                shutil.rmtree(folder)
            except Exception as e:
                messagebox.showerror('Error', f'Failed to delete folder:\n{e}')

        self._blocks = parse_blocks(self._html)
        self._refresh_tree()
        self._status_var.set(f'Deleted entry + folder: {block["label"]}')

    def _delete_compare_files(self):
        if not self._html_path:
            messagebox.showwarning('No file', 'Load a Dashboard.html first.')
            return
        dash_dir = os.path.dirname(self._html_path)
        import re as _re2
        name_re = _re2.compile(r'compare', _re2.IGNORECASE)
        found = [
            p for p in sorted(glob.glob(os.path.join(dash_dir, '*.html')))
            if os.path.basename(p).lower() != 'dashboard.html'
            and name_re.search(os.path.splitext(os.path.basename(p))[0])
        ]
        if not found:
            messagebox.showinfo('Nothing found', 'No compare/comparison HTML files found.')
            return

        # ── Selection dialog ────────────────────────────────────────────────
        BG  = '#1a252f'
        BG2 = '#2c3e50'
        FG  = '#ecf0f1'

        dlg = tk.Toplevel(self)
        dlg.title('Delete Compare Files')
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text='Select files to delete:',
                 bg=BG, fg=FG, font=('Arial', 9, 'bold'),
                 padx=12, pady=8).pack(anchor='w')

        chk_frame = tk.Frame(dlg, bg=BG2, padx=8, pady=6)
        chk_frame.pack(fill='x', padx=12, pady=(0, 8))

        vars_ = []
        for p in found:
            var = tk.BooleanVar(value=True)
            tk.Checkbutton(chk_frame, text=os.path.basename(p),
                           variable=var, bg=BG2, fg=FG,
                           selectcolor=BG, activebackground=BG2,
                           activeforeground=FG, font=('Consolas', 9),
                           relief='flat').pack(anchor='w', pady=1)
            vars_.append((p, var))

        # Select all / None helpers
        sel_row = tk.Frame(dlg, bg=BG)
        sel_row.pack(fill='x', padx=12, pady=(0, 6))
        tk.Button(sel_row, text='Select All',
                  command=lambda: [v.set(True) for _, v in vars_],
                  bg='#1f618d', fg='white', relief='flat',
                  font=('Arial', 8), padx=6).pack(side='left', padx=(0, 4))
        tk.Button(sel_row, text='Select None',
                  command=lambda: [v.set(False) for _, v in vars_],
                  bg='#1f618d', fg='white', relief='flat',
                  font=('Arial', 8), padx=6).pack(side='left')

        def _do_delete():
            to_delete = [p for p, v in vars_ if v.get()]
            if not to_delete:
                messagebox.showwarning('Nothing selected', 'Select at least one file.',
                                       parent=dlg)
                return
            dlg.destroy()
            errors = []
            for p in to_delete:
                try:
                    os.remove(p)
                except Exception as e:
                    errors.append(f'{os.path.basename(p)}: {e}')
            # Check if any compare files remain; update Dashboard.html accordingly
            try:
                import re as _re
                import sys, pathlib
                _src = str(pathlib.Path(__file__).parent)
                if _src not in sys.path:
                    sys.path.insert(0, _src)
                import compare_runs as _cr
                _cr.update_dashboard_compare_links(pathlib.Path(self._html_path))
                with open(self._html_path, 'r', encoding='utf-8') as f:
                    self._html = f.read()
            except Exception as e:
                errors.append(f'Dashboard.html update: {e}')
            if errors:
                messagebox.showerror('Errors', '\n'.join(errors))
            else:
                self._status_var.set(
                    f'Deleted {len(to_delete)} compare file(s); Dashboard.html updated.')

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(fill='x', padx=12, pady=(0, 10))
        tk.Button(btn_row, text='Delete Selected',
                  command=_do_delete,
                  bg='#c0392b', fg='white', relief='flat',
                  font=('Arial', 9, 'bold'), padx=10).pack(side='left', padx=(0, 6))
        tk.Button(btn_row, text='Cancel',
                  command=dlg.destroy,
                  bg='#555', fg='white', relief='flat',
                  font=('Arial', 9), padx=10).pack(side='left')

        dlg.update_idletasks()

    # ── Delete Vmin Files dialog ─────────────────────────────────────────────

    def _delete_vmin_files(self):
        if not self._html_path:
            messagebox.showwarning('No file', 'Load a Dashboard.html first.')
            return

        # Collect only VMIN blocks (stem starts with "vmin__")
        vmin_blocks = [b for b in self._blocks if b['stem'].startswith('vmin__')]
        if not vmin_blocks:
            messagebox.showinfo('Nothing found', 'No Vmin entries found in Dashboard.html.')
            return

        BG  = '#1a252f'
        BG2 = '#2c3e50'
        FG  = '#ecf0f1'

        dlg = tk.Toplevel(self)
        dlg.title('Delete Vmin Files')
        dlg.configure(bg=BG)
        dlg.resizable(True, False)
        dlg.grab_set()

        tk.Label(dlg, text='Select Vmin runs to delete:',
                 bg=BG, fg=FG, font=('Arial', 9, 'bold'),
                 padx=12, pady=8).pack(anchor='w')

        # Column headers
        hdr = tk.Frame(dlg, bg='#34495e')
        hdr.pack(fill='x', padx=12, pady=(0, 2))
        tk.Label(hdr, text='  Delete?', bg='#34495e', fg=FG,
                 font=('Arial', 8, 'bold'), width=10, anchor='w').pack(side='left')
        tk.Label(hdr, text='Label', bg='#34495e', fg=FG,
                 font=('Arial', 8, 'bold'), width=30, anchor='w').pack(side='left')
        tk.Label(hdr, text='Output Folder', bg='#34495e', fg=FG,
                 font=('Arial', 8, 'bold'), anchor='w').pack(side='left', padx=(4, 0))

        chk_frame = tk.Frame(dlg, bg=BG2, padx=8, pady=6)
        chk_frame.pack(fill='x', padx=12, pady=(0, 4))

        entries = []  # (block, folder_path, tk.BooleanVar)
        for b in vmin_blocks:
            folder = resolve_output_folder(self._html_path, b['first_href'])
            var = tk.BooleanVar(value=True)
            row = tk.Frame(chk_frame, bg=BG2)
            row.pack(fill='x', pady=1)

            exists = folder and os.path.isdir(folder)
            folder_disp = folder if folder else '—'
            folder_color = '#a9dfbf' if exists else '#e74c3c'

            tk.Checkbutton(row, variable=var, bg=BG2, fg=FG,
                           selectcolor=BG, activebackground=BG2,
                           activeforeground=FG, relief='flat',
                           width=2).pack(side='left')
            tk.Label(row, text=b['label'], bg=BG2, fg=FG,
                     font=('Consolas', 9), width=30,
                     anchor='w').pack(side='left')
            tk.Label(row, text=folder_disp, bg=BG2, fg=folder_color,
                     font=('Consolas', 8),
                     anchor='w').pack(side='left', padx=(4, 0))

            entries.append((b, folder, var))

        # Select all / none
        sel_row = tk.Frame(dlg, bg=BG)
        sel_row.pack(fill='x', padx=12, pady=(0, 4))
        tk.Button(sel_row, text='Select All',
                  command=lambda: [v.set(True) for _, _, v in entries],
                  bg='#1f618d', fg='white', relief='flat',
                  font=('Arial', 8), padx=6).pack(side='left', padx=(0, 4))
        tk.Button(sel_row, text='Select None',
                  command=lambda: [v.set(False) for _, _, v in entries],
                  bg='#1f618d', fg='white', relief='flat',
                  font=('Arial', 8), padx=6).pack(side='left')

        # Options: what to delete
        opt_frame = tk.Frame(dlg, bg=BG, padx=12, pady=4)
        opt_frame.pack(fill='x')
        del_mode = tk.StringVar(value='both')
        for val, lbl in (('html', 'Dashboard entry only'),
                         ('folder', 'Output folder only'),
                         ('both',  'Entry + folder (recommended)')):
            tk.Radiobutton(opt_frame, text=lbl, variable=del_mode, value=val,
                           bg=BG, fg=FG, selectcolor='#2c3e50',
                           activebackground=BG, activeforeground=FG,
                           font=('Arial', 9)).pack(side='left', padx=(0, 12))

        def _do_delete():
            selected = [(b, folder) for b, folder, v in entries if v.get()]
            if not selected:
                messagebox.showwarning('Nothing selected', 'Select at least one run.',
                                       parent=dlg)
                return
            mode = del_mode.get()
            dlg.destroy()

            errors = []
            for b, folder in selected:
                # Remove HTML entry
                if mode in ('html', 'both'):
                    self._html = remove_block(self._html, b['stem'])

                # Delete output folder
                if mode in ('folder', 'both'):
                    if folder and os.path.isdir(folder):
                        try:
                            shutil.rmtree(folder)
                        except Exception as e:
                            errors.append(f'{os.path.basename(folder)}: {e}')
                    elif mode == 'folder':
                        errors.append(f'{b["label"]}: folder not found ({folder})')

            if mode in ('html', 'both'):
                self._save_html()

            self._blocks = parse_blocks(self._html)
            self._refresh_tree()

            if errors:
                messagebox.showerror('Errors', '\n'.join(errors))
            else:
                n = len(selected)
                self._status_var.set(
                    f'Deleted {n} Vmin run(s) '
                    f'({"entries + folders" if mode == "both" else mode}).')

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.pack(fill='x', padx=12, pady=(0, 10))
        tk.Button(btn_row, text='Delete Selected',
                  command=_do_delete,
                  bg='#117a65', fg='white', relief='flat',
                  font=('Arial', 9, 'bold'), padx=10).pack(side='left', padx=(0, 6))
        tk.Button(btn_row, text='Cancel',
                  command=dlg.destroy,
                  bg='#555', fg='white', relief='flat',
                  font=('Arial', 9), padx=10).pack(side='left')

        dlg.update_idletasks()
        # Centre over parent
        x = self.winfo_x() + (self.winfo_width()  - dlg.winfo_width())  // 2
        y = self.winfo_y() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f'+{x}+{y}')

    def _save_html(self):
        try:
            with open(self._html_path, 'w', encoding='utf-8') as f:
                f.write(self._html)
        except Exception as e:
            messagebox.showerror('Error', f'Failed to save Dashboard.html:\n{e}')

    # ── reorder helpers ──────────────────────────────────────────────────────

    def _selected_index(self) -> int | None:
        sel = self._tree.selection()
        if not sel:
            messagebox.showwarning('No selection', 'Select an entry first.')
            return None
        return int(sel[0])

    def _apply_reorder(self, new_idx: int):
        """Save reordered blocks, refresh tree, and re-select the moved row."""
        self._html = reorder_blocks(self._html, self._blocks)
        self._save_html()
        self._refresh_tree()
        self._tree.selection_set(str(new_idx))
        self._tree.see(str(new_idx))
        self._status_var.set(f'Reordered: {self._blocks[new_idx]["label"]}')

    def _move_up(self):
        idx = self._selected_index()
        if idx is None or idx == 0:
            return
        self._blocks[idx], self._blocks[idx - 1] = self._blocks[idx - 1], self._blocks[idx]
        self._apply_reorder(idx - 1)

    def _move_down(self):
        idx = self._selected_index()
        if idx is None or idx >= len(self._blocks) - 1:
            return
        self._blocks[idx], self._blocks[idx + 1] = self._blocks[idx + 1], self._blocks[idx]
        self._apply_reorder(idx + 1)

    def _move_top(self):
        idx = self._selected_index()
        if idx is None or idx == 0:
            return
        block = self._blocks.pop(idx)
        self._blocks.insert(0, block)
        self._apply_reorder(0)

    def _move_bottom(self):
        idx = self._selected_index()
        if idx is None or idx >= len(self._blocks) - 1:
            return
        block = self._blocks.pop(idx)
        self._blocks.append(block)
        self._apply_reorder(len(self._blocks) - 1)


# Keep standalone alias for backward compat
DashboardManager = ManageFrame


def main():
    root = tk.Tk()
    root.title('Dashboard Manager')
    root.geometry('900x560')
    frame = ManageFrame(root)
    frame.pack(fill=tk.BOTH, expand=True)
    root.mainloop()


if __name__ == '__main__':
    main()
