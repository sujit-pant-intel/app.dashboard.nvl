"""_pipeline_ui.py - UI-building and browse-dialog mixin for PipelineFrame."""
import json
from _pipeline_constants import _SRC_DIR, _ROOT_DIR, _FROZEN, _LOADER, SICC_UPM_SCRIPT, SICC_CDYN_UPM_SCRIPT, _PROD_CFG_DIR, _PCM_SETUP_JSON
import os
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext


class PipelineUIMixin:
    def _build_ui(self):
        BG   = '#1a252f'
        BG2  = '#2c3e50'
        FG   = '#ecf0f1'
        FG2  = '#95a5a6'
        BLUE = '#2980b9'
        ABLU = '#3498db'

        # ── Title ──────────────────────────────────────────────────────────────
        tk.Label(
            self, text='Yield Analysis Dashboard',
            bg=BG, fg='#3498db', font=('Arial', 13, 'bold')
        ).pack(fill=tk.X, padx=10, pady=(6, 2))

        # ── Top button bar ─────────────────────────────────────────────────────
        frm = tk.Frame(self, bg=BG)
        frm.pack(fill=tk.X, padx=8, pady=(0, 3))

        def _btn(parent, text, cmd, color='#1f618d', acolor='#2980b9'):
            return tk.Button(parent, text=text, command=cmd,
                             bg=color, fg='white', activebackground=acolor,
                             relief='flat', cursor='hand2', font=('Arial', 9),
                             padx=8, pady=3)

        _btn(frm, 'Load JSON',        self.load_json,      '#1f618d', ABLU).pack(side=tk.LEFT, padx=(0,4))
        _btn(frm, 'Run',              self.run_pipeline,   '#27ae60', '#2ecc71').pack(side=tk.LEFT, padx=(0,4))
        _btn(frm, 'Save JSON',        self.save_json,      '#1f618d', ABLU).pack(side=tk.LEFT, padx=(0,4))
        _btn(frm, 'Open Dashboard',   self.open_report,    '#935116', '#ca6f1e').pack(side=tk.LEFT)
        _btn(frm, '↺ Reset',          self.reset_fields,   '#6e2f2f', '#922b21').pack(side=tk.RIGHT)

        # ── Two-column panel container ─────────────────────────────────────────
        _panels = tk.Frame(self, bg=BG)
        _panels.pack(fill=tk.X, padx=4, pady=0)
        _panels.columnconfigure(0, weight=1)
        _panels.columnconfigure(1, weight=1)

        _left  = tk.Frame(_panels, bg=BG)
        _left.grid(row=0, column=0, sticky='nsew', padx=(0, 2))
        _right = tk.Frame(_panels, bg=BG)
        _right.grid(row=0, column=1, sticky='nsew', padx=(2, 0))

        # ── Shared helpers ─────────────────────────────────────────────────────
        def _lf(parent, text, color='#7f8c8d'):
            f = tk.LabelFrame(parent, text=text, bg=BG, fg=color,
                              font=('Arial', 8), padx=6, pady=4)
            f.pack(fill=tk.X, padx=4, pady=(0, 3))
            return f

        def _field_row(parent, row, label, var=None, width=40, label_fg=None):
            tk.Label(parent, text=label, width=18, anchor='w',
                     bg=BG, fg=label_fg or FG, font=('Arial', 9)).grid(
                row=row, column=0, sticky='w', pady=2, padx=(0, 4))
            if var is None:
                var = tk.StringVar()
            e = tk.Entry(parent, textvariable=var, width=width,
                         bg=BG2, fg='white', insertbackground='white',
                         relief='flat', font=('Consolas', 9))
            e.grid(row=row, column=1, padx=(0, 4), pady=2, sticky='ew')
            parent.columnconfigure(1, weight=1)
            return var, e

        def _browse_b(parent, row, cmd):
            tk.Button(parent, text='...', command=cmd, width=3,
                      bg=BLUE, fg='white', activebackground=ABLU,
                      relief='flat', cursor='hand2').grid(
                row=row, column=2, padx=(0, 4), pady=2)

        # ══════════════════════════════════════════════════════════════════════
        # LEFT PANEL: Inputs, Render Options, Product Config
        # ══════════════════════════════════════════════════════════════════════

        # ── Inputs (required + auto-populated) ───────────────────────────────
        REQ_FG = '#2ecc71'   # bright green  — required fields
        DIM_FG = '#5d7a8a'   # dimmer blue-grey — auto-populated fields

        inp_frm = _lf(_left, 'Inputs', '#27ae60')
        # Create dashboard_var early so output_folder/identifier traces can reference it
        self.dashboard_var = tk.StringVar()
        # ── Data CSV listbox (primary + extras in one list) ──────────────────
        # First item  = primary Data CSV  (was '★ Data CSV' entry)
        # Items 1..n  = extra CSVs        (was 'Extra Data CSVs' text box)
        self.aqua_out_var = tk.StringVar()   # mirrors first listbox item
        _data_lbl_row = tk.Frame(inp_frm, bg=BG)
        _data_lbl_row.grid(row=0, column=0, columnspan=3, sticky='ew', pady=(4, 2))
        tk.Label(_data_lbl_row, text='★ Data CSVs:', width=18, anchor='w',
                 bg=BG, fg=REQ_FG, font=('Arial', 9, 'bold')).pack(side=tk.LEFT)
        tk.Button(_data_lbl_row, text='Add CSV / ZIP / 7Z…',
                  bg=BLUE, fg='white', activebackground=ABLU, relief='flat', cursor='hand2',
                  font=('Arial', 8), padx=6, pady=1,
                  command=self._browse_data_csvs).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(_data_lbl_row, text='Remove Selected',
                  bg='#7b241c', fg='white', activebackground='#a93226', relief='flat',
                  cursor='hand2', font=('Arial', 8), padx=6, pady=1,
                  command=self._remove_data_csvs).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(_data_lbl_row,
                 text='first item = primary CSV · additional items merged in',
                 bg=BG, fg=FG2, font=('Arial', 8)).pack(side=tk.LEFT, padx=(4, 0))

        _data_lb_outer = tk.Frame(inp_frm, bg=BG)
        _data_lb_outer.grid(row=1, column=0, columnspan=3, sticky='ew')
        _data_lb_sy = tk.Scrollbar(_data_lb_outer, orient='vertical')
        _data_lb_sx = tk.Scrollbar(_data_lb_outer, orient='horizontal')
        self._data_csv_lb = tk.Listbox(
            _data_lb_outer, height=7, selectmode='extended',
            bg=BG2, fg='white', selectbackground='#1f618d', selectforeground='white',
            activestyle='none', font=('Consolas', 9), relief='flat',
            yscrollcommand=_data_lb_sy.set, xscrollcommand=_data_lb_sx.set)
        _data_lb_sy.config(command=self._data_csv_lb.yview)
        _data_lb_sx.config(command=self._data_csv_lb.xview)
        _data_lb_sy.pack(side=tk.RIGHT, fill=tk.Y)
        _data_lb_sx.pack(side=tk.BOTTOM, fill=tk.X)
        self._data_csv_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._data_csv_lb.bind('<<ListboxSelect>>', lambda *_: None)

        # keep a dead Text widget so old code that checks _extra_csv_text doesn't crash
        self._extra_csv_text = tk.Text(self, height=0, width=0)

        # Separator + auto-populated section
        tk.Frame(inp_frm, bg='#3d5166', height=1).grid(
            row=3, column=0, columnspan=3, sticky='ew', pady=(8, 0))
        tk.Label(inp_frm, text='  ↳ auto-populated', bg=BG, fg=DIM_FG,
                 font=('Arial', 7, 'italic')).grid(
            row=4, column=0, columnspan=3, sticky='w', pady=(0, 2))

        self._id_prev_auto   = ['']  # tracks last auto-set value so manual edits are preserved
        self._out_prev_auto  = ['']  # tracks last auto-set output folder
        self._dash_prev_auto = ['']  # tracks last auto-set dashboard path
        self._prod_cfg_is_default = [True]  # True when product config was auto-selected (not from JSON)

        self.output_folder_var, _ = _field_row(inp_frm, 6, 'Output folder:', label_fg=DIM_FG)
        _browse_b(inp_frm, 6, lambda: self._browse_dir_into(self.output_folder_var))
        self.testprogram_id_var, _ = _field_row(inp_frm, 7, 'Identifier:', label_fg=DIM_FG)
        self.tp_folder_var, _      = _field_row(inp_frm, 8, 'TP folder:', label_fg=DIM_FG)
        _browse_b(inp_frm, 8, lambda: self._browse_dir_into(self.tp_folder_var))
        self.testprogram_var, _    = _field_row(inp_frm, 9, 'TestProgram:', label_fg=DIM_FG)
        # Dashboard html — auto-populated at <output_folder>/<identifier>/Dashboard.html
        _field_row(inp_frm, 5, 'Dashboard html:', var=self.dashboard_var, label_fg=DIM_FG)
        _browse_b(inp_frm, 5, lambda: self._browse_dashboard_html(self.dashboard_var))

        def _sync_id(*_):
            new_tp = self.testprogram_var.get()
            cur_id = self.testprogram_id_var.get()
            # Only overwrite Identifier if it's empty or still matches the last auto value
            if not cur_id or cur_id == self._id_prev_auto[0]:
                self.testprogram_id_var.set(new_tp)
                self._id_prev_auto[0] = new_tp
        self.testprogram_var.trace_add('write', _sync_id)
        # Auto-populate dashboard whenever output_folder or identifier changes
        self.output_folder_var.trace_add('write', lambda *_: self._auto_set_dashboard())
        self.testprogram_id_var.trace_add('write', lambda *_: self._auto_set_dashboard())

        _dash_chk_row = tk.Frame(inp_frm, bg=BG)
        _dash_chk_row.grid(row=10, column=0, columnspan=3, sticky='w', pady=(2, 0))
        self.reticle_save_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            _dash_chk_row,
            text='Save merged file  (<input>-merged.csv  [zipped])',
            variable=self.reticle_save_var,
            bg=BG, fg=FG, selectcolor=BG2,
            activebackground=BG, activeforeground=FG,
            font=('Arial', 9),
        ).pack(side=tk.LEFT)

        # ── TestProgram hidden vars now shown in GUI (auto-populated but overridable) ──
        self.bindef_out_var  = tk.StringVar()  # kept for compat

        # Render option vars (used by runner — UI built in right panel below)
        self.render_wafermap_var = tk.BooleanVar(value=True)
        self.render_heatmap_var  = tk.BooleanVar(value=False)
        self.render_pareto_var   = tk.BooleanVar(value=False)
        self.debug_console_var   = tk.BooleanVar(value=False)
        self.render_ibin_var     = self.render_pareto_var
        self.render_fbin_var     = self.render_pareto_var

        # ── Product Config (var setup; UI built in right panel below) ───────────
        yt_frm = None  # UI replaced by combined frame in right panel

        # Discover all Product Config JSONs in the central shared directory
        def _scan_prod_cfgs():
            import glob as _gl
            # Match both old-style "Product Config*.json" and new-style "<DEVREV> - SORT - *.json"
            cfgs = sorted(
                _gl.glob(os.path.join(_PROD_CFG_DIR, '* - SORT - *.json')) +
                _gl.glob(os.path.join(_PROD_CFG_DIR, 'Product Config*.json'))
            )
            return cfgs

        _central_cfgs = _scan_prod_cfgs()
        _cfg_labels   = [os.path.basename(p) for p in _central_cfgs]
        _CUSTOM_LABEL = 'Custom…'
        _NONE_LABEL   = '(none)'
        _dropdown_opts = [_NONE_LABEL] + _cfg_labels + [_CUSTOM_LABEL]

        self._prod_cfg_dd_var = tk.StringVar(value=_NONE_LABEL)
        self.pcm_spec_csv_var = tk.StringVar()
        self.fail_bucket_var  = tk.StringVar()
        self._prod_cfg_map = {os.path.basename(p): p for p in _central_cfgs}

        self.plot_json_var = tk.StringVar()

        # ── SICC/CDYN hidden vars ─────────────────────────────────────────────
        self.sicc_run_var      = tk.BooleanVar(value=False)
        self.sicc_csv_var      = tk.StringVar()
        self.sicc_out_var      = tk.StringVar()
        self.sicc_save_jmp_var = tk.BooleanVar(value=False)
        self._sicc_widgets     = []

        # ══════════════════════════════════════════════════════════════════════
        # RIGHT PANEL: Options + Product Config, Parametric / PCM Options
        # ══════════════════════════════════════════════════════════════════════

        # ── Hidden vars kept for backward compat with runner ─────────────────
        self.skip_aqua_var   = tk.BooleanVar(value=True)
        self.aqua_server_var = tk.StringVar()
        self.aqua_cmd_var    = tk.StringVar()
        self.report_path_var = tk.StringVar()

        # ── Options + Product Config (combined) ──────────────────────────────
        opt_frm = _lf(_right, 'Options & Product Config', '#e67e22')
        opt_frm.columnconfigure(1, weight=1)

        # Render checkboxes row
        _chk_style = dict(bg=BG, fg=FG, selectcolor=BG2,
                          activebackground=BG, activeforeground=FG,
                          font=('Arial', 9))
        _rnd_row = tk.Frame(opt_frm, bg=BG)
        _rnd_row.grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 4))
        tk.Checkbutton(_rnd_row, text='Generate Wafermap',
                       variable=self.render_wafermap_var, **_chk_style).pack(side=tk.LEFT)
        tk.Checkbutton(_rnd_row, text='Debug console',
                       variable=self.debug_console_var, **_chk_style).pack(side=tk.LEFT, padx=(12, 0))

        # Separator
        tk.Frame(opt_frm, bg='#3d5166', height=1).grid(
            row=1, column=0, columnspan=3, sticky='ew', pady=(0, 4))

        # Product Config dropdown
        tk.Label(opt_frm, text='Product Config:', width=16, anchor='w',
                 bg=BG, fg=FG, font=('Arial', 9)).grid(
            row=2, column=0, sticky='w', pady=2, padx=(0, 4))
        _dd = tk.OptionMenu(opt_frm, self._prod_cfg_dd_var, *_dropdown_opts)
        _dd.config(bg=BG2, fg='white', activebackground='#3d5166',
                   activeforeground='white', relief='flat',
                   font=('Consolas', 9), anchor='w', width=28,
                   highlightthickness=0)
        _dd['menu'].config(bg=BG2, fg='white', font=('Consolas', 9))
        _dd.grid(row=2, column=1, sticky='ew', pady=2, padx=(0, 4))

        def _refresh_cfgs():
            new_cfgs   = _scan_prod_cfgs()
            new_labels = [os.path.basename(p) for p in new_cfgs]
            self._prod_cfg_map = {os.path.basename(p): p for p in new_cfgs}
            menu = _dd['menu']
            menu.delete(0, 'end')
            for opt in [_NONE_LABEL] + new_labels + [_CUSTOM_LABEL]:
                menu.add_command(label=opt,
                                 command=tk._setit(self._prod_cfg_dd_var, opt))
            cur = self._prod_cfg_dd_var.get()
            if cur not in ([_NONE_LABEL] + new_labels + [_CUSTOM_LABEL]):
                self._prod_cfg_dd_var.set(_NONE_LABEL)

        tk.Button(opt_frm, text='↺', command=_refresh_cfgs, width=3,
                  bg='#1f618d', fg='white', activebackground=ABLU,
                  relief='flat', cursor='hand2').grid(
            row=2, column=2, pady=2, padx=(0, 2))

        def _on_dd_change(*_):
            sel = self._prod_cfg_dd_var.get()
            if sel == _NONE_LABEL:
                self.fail_bucket_var.set('')
            elif sel == _CUSTOM_LABEL:
                f = filedialog.askopenfilename(
                    title='Select Product Config JSON',
                    filetypes=[('JSON', '*.json'), ('All files', '*.*')])
                if f:
                    self.fail_bucket_var.set(f)
                    lbl = os.path.basename(f)
                    self._prod_cfg_map[lbl] = f
                    menu = _dd['menu']
                    menu.insert_command(menu.index('end'), label=lbl,
                                        command=tk._setit(self._prod_cfg_dd_var, lbl))
                    self._prod_cfg_dd_var.set(lbl)
                else:
                    prev = self.fail_bucket_var.get()
                    rev_lbl = os.path.basename(prev) if prev else _NONE_LABEL
                    self._prod_cfg_dd_var.set(rev_lbl if rev_lbl in self._prod_cfg_map else _NONE_LABEL)
            else:
                full = self._prod_cfg_map.get(sel, '')
                self.fail_bucket_var.set(full)
                if full:
                    self._load_product_cfg(full)

        self._prod_cfg_dd_var.trace_add('write', _on_dd_change)

        # Resolved path label
        tk.Label(opt_frm, text='Path:', width=16, anchor='w',
                 bg=BG, fg=FG2, font=('Arial', 8)).grid(
            row=3, column=0, sticky='w', pady=(0, 2), padx=(0, 4))
        tk.Label(opt_frm, textvariable=self.fail_bucket_var,
                 bg=BG, fg='#a9cce3', font=('Consolas', 8),
                 anchor='w', wraplength=260, justify='left').grid(
            row=3, column=1, columnspan=2, sticky='ew', pady=(0, 2))

        if len(_central_cfgs) == 1:
            self._prod_cfg_dd_var.set(_cfg_labels[0])

        # ── Parametric / PCM Options ───────────────────────────────────────────
        pcm_frm = _lf(_right, 'Parametric Dashboard', '#3498db')
        pcm_chk_row = tk.Frame(pcm_frm, bg=BG)
        pcm_chk_row.grid(row=0, column=0, columnspan=3, sticky='w')
        self.pcm_full_site_var = tk.BooleanVar(value=False)
        self.run_parametric_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            pcm_chk_row, text='Run Parametric',
            variable=self.run_parametric_var,
            bg=BG, fg='#3498db', selectcolor=BG2,
            activebackground=BG, activeforeground='#3498db',
            font=('Arial', 9, 'bold'),
        ).pack(side=tk.LEFT, padx=(0, 16))
        tk.Checkbutton(
            pcm_chk_row, text='Full-site PCM',
            variable=self.pcm_full_site_var,
            bg=BG, fg=FG, selectcolor=BG2,
            activebackground=BG, activeforeground=FG,
            font=('Arial', 9),
        ).pack(side=tk.LEFT)
        self.pcm_product_setup_var = self.fail_bucket_var  # same as Product Config — no separate GUI field
        # pcm_spec_csv_var pre-initialized above (before Product Config dropdown)

        # ── PCM Parameter Groups — read from pcm_product_setup.json ──────────
        def _load_pcm_groups_from_setup():
            if _PCM_SETUP_JSON:
                try:
                    with open(_PCM_SETUP_JSON, encoding='utf-8') as _f:
                        _js = json.load(_f)
                    grps = [
                        {"name": g["name"], "patterns": g.get("patterns", [])}
                        for g in _js.get("groups", [])
                        if g.get("name") and g.get("patterns")
                    ]
                    if grps:
                        return grps
                except Exception:
                    pass
            # Fallback if file not found
            return [
                {"name": "Conductance",         "patterns": ["Con_*"]},
                {"name": "Capacitance",         "patterns": ["Cmim_*", "Cmin_*"]},
                {"name": "Vts N-FET",           "patterns": ["Vts_RN*", "Vts_N*", "Vtl_N*"]},
                {"name": "Vts P-FET",           "patterns": ["Vts_RP*", "Vts_P*", "Vtl_P*"]},
                {"name": "Isat N-FET",          "patterns": ["Isat_RN*", "Isat_N*"]},
                {"name": "Isat P-FET",          "patterns": ["Isat_RP*", "Isat_P*"]},
                {"name": "Ioff N-FET",          "patterns": ["Ioff_RN*"]},
                {"name": "Ioff P-FET",          "patterns": ["Ioff_RP*"]},
                {"name": "Contact Resistance",  "patterns": ["Rc_*"]},
                {"name": "Sheet Resistance",    "patterns": ["Rs_*", "RDL_*", "SPA_*"]},
                {"name": "Propagation Delay",   "patterns": ["Td_*"]},
                {"name": "Power (Pwr)",         "patterns": ["Pwr_*"]},
                {"name": "Power (Off)",         "patterns": ["Poff_*"]},
                {"name": "Breakdown / Other",   "patterns": ["VbdGO_*", "VBD_*", "Isb_*"]},
            ]
        _PCM_DEFAULT_GROUPS = _load_pcm_groups_from_setup()
        self._pcm_groups = list(_PCM_DEFAULT_GROUPS)
        self._pcm_default_groups = _PCM_DEFAULT_GROUPS
        # Preserve any value already stored by _load_product_cfg (fired during auto-select above)
        if not hasattr(self, '_pcm_cfg_selected'):
            self._pcm_cfg_selected = None  # list of group names pre-selected by Product Config
        self._pcm_grp_vars = []  # list of BooleanVar, one per group

        # Dropdown row with label + menubutton + All/None buttons
        _grp_row = tk.Frame(pcm_frm, bg=BG)
        _grp_row.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(4, 0))
        tk.Label(_grp_row, text='Parameter Groups:', bg=BG, fg=FG2,
                 font=('Arial', 8)).pack(side=tk.LEFT, padx=(0, 4))
        self._pcm_grp_btn = tk.Menubutton(
            _grp_row, text='All groups selected',
            bg=BG2, fg=FG, activebackground='#3498db', activeforeground='white',
            relief='flat', font=('Consolas', 9), indicatoron=True,
            padx=6, pady=2, anchor='w')
        self._pcm_grp_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._pcm_grp_menu = tk.Menu(
            self._pcm_grp_btn, tearoff=False,
            bg=BG2, fg=FG, activebackground='#3498db', activeforeground='white',
            font=('Consolas', 9), selectcolor='#3498db')
        self._pcm_grp_btn.configure(menu=self._pcm_grp_menu)
        tk.Button(_grp_row, text='All', command=self._pcm_grp_select_all, width=3,
                  bg='#1f618d', fg='white', activebackground=ABLU,
                  relief='flat', cursor='hand2', font=('Arial', 8)
                  ).pack(side=tk.LEFT, padx=(4, 0))
        tk.Button(_grp_row, text='None', command=self._pcm_grp_clear, width=4,
                  bg='#1f618d', fg='white', activebackground=ABLU,
                  relief='flat', cursor='hand2', font=('Arial', 8)
                  ).pack(side=tk.LEFT, padx=(2, 0))
        tk.Button(_grp_row, text='\u21ba',
                  command=lambda: (
                      self._apply_pcm_group_selection(self._pcm_cfg_selected)
                      if self._pcm_cfg_selected is not None
                      else self._pcm_grp_select_all()),
                  width=3, bg='#1f618d', fg='white', activebackground=ABLU,
                  relief='flat', cursor='hand2', font=('Arial', 8)
                  ).pack(side=tk.LEFT, padx=(2, 0))

        # Populate dropdown with checkbox items
        for g in self._pcm_groups:
            var = tk.BooleanVar(value=True)
            var.trace_add('write', lambda *_: self._update_pcm_grp_label())
            label = f"{g['name']}  ({', '.join(g['patterns'])})"
            self._pcm_grp_menu.add_checkbutton(label=label, variable=var)
            self._pcm_grp_vars.append(var)
        # Apply any selection that _load_product_cfg stored before this section was built
        if getattr(self, '_pcm_cfg_selected', None):
            self._apply_pcm_group_selection(self._pcm_cfg_selected)
        self._update_pcm_grp_label()

        # Custom wildcard filter
        _filt_row = tk.Frame(pcm_frm, bg=BG)
        _filt_row.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(2, 4))
        tk.Label(_filt_row, text='Custom filter:', bg=BG, fg=FG2,
                 font=('Arial', 8)).pack(side=tk.LEFT, padx=(0, 4))
        self.pcm_custom_filter_var = tk.StringVar(value='')
        tk.Entry(_filt_row, textvariable=self.pcm_custom_filter_var, width=30,
                 bg=BG2, fg='white', insertbackground='white',
                 relief='flat', font=('Consolas', 9)
                 ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(_filt_row, text='(wildcards: Con_*,Rc_*)',
                 bg=BG, fg=FG2, font=('Arial', 7)).pack(side=tk.LEFT, padx=(4, 0))

        # ══════════════════════════════════════════════════════════════════════
        # FULL-WIDTH: Output log
        # ══════════════════════════════════════════════════════════════════════
        tk.Label(self, text='Pipeline Output', bg=BG, fg=FG2,
                 font=('Arial', 8)).pack(anchor='w', padx=8)
        self.output = scrolledtext.ScrolledText(
            self, bg='#1e2d3b', fg='#ecf0f1',
            insertbackground='white', font=('Consolas', 9), height=30
        )
        self.output.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.fields_container = tk.Frame(self, bg=BG)   # kept for compat
        self.fields = {}

    def _on_sicc_toggle(self):
        """Enable/disable SICC/CDYN/UPM entry widgets based on checkbox."""
        state = tk.NORMAL if self.sicc_run_var.get() else tk.DISABLED
        for w in self._sicc_widgets:
            w.config(state=state)

    def _apply_pcm_group_selection(self, names):
        """Check only the groups whose names are in `names`; uncheck the rest."""
        name_set = set(names)
        for i, v in enumerate(self._pcm_grp_vars):
            v.set(self._pcm_groups[i]['name'] in name_set)

    def _pcm_grp_select_all(self):
        for v in self._pcm_grp_vars:
            v.set(True)

    def _pcm_grp_clear(self):
        for v in self._pcm_grp_vars:
            v.set(False)

    def _update_pcm_grp_label(self):
        """Update the dropdown button text to reflect selection count."""
        n_sel = sum(1 for v in self._pcm_grp_vars if v.get())
        total = len(self._pcm_grp_vars)
        if n_sel == total:
            self._pcm_grp_btn.configure(text='All groups selected')
        elif n_sel == 0:
            self._pcm_grp_btn.configure(text='No groups selected')
        else:
            names = [self._pcm_groups[i]['name']
                     for i, v in enumerate(self._pcm_grp_vars) if v.get()]
            txt = ', '.join(names)
            if len(txt) > 60:
                txt = txt[:57] + '...'
            self._pcm_grp_btn.configure(text=f'{n_sel}/{total}: {txt}')

    def _get_pcm_filter(self) -> str:
        """Build combined wildcard filter from selected groups + custom entry.
        Returns comma-separated wildcards or empty string (= all params).
        """
        parts = []
        for i, v in enumerate(self._pcm_grp_vars):
            if v.get() and i < len(self._pcm_groups):
                parts.extend(self._pcm_groups[i]["patterns"])
        custom = self.pcm_custom_filter_var.get().strip()
        if custom:
            parts.extend([p.strip() for p in custom.split(",") if p.strip()])
        return ",".join(parts)

    def load_json(self):
        path = filedialog.askopenfilename(title='Select input JSON', filetypes=[('JSON', '*.json')])
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror('Failed to load JSON', str(e))
            return
        self.input_path = path
        if 'outputFilename' in data and 'aqua_outputfile' not in data:
            data['aqua_outputfile'] = data.pop('outputFilename')
        if 'DataCSV' in data and 'aqua_outputfile' not in data:
            _dcv = data.pop('DataCSV')
            if isinstance(_dcv, list) and _dcv:
                data['aqua_outputfile'] = _dcv[0]
                if len(_dcv) > 1 and 'extra_csv_files' not in data:
                    data['extra_csv_files'] = _dcv[1:]
            else:
                data['aqua_outputfile'] = _dcv
        self.json_data = data

        def _load(var, *keys):
            for k in keys:
                if k in data:
                    var.set(str(data[k]))
                    return
            var.set('')

        # Dashboard / output Info
        _load(self.output_folder_var,   'output_folder')
        self._out_prev_auto[0] = self.output_folder_var.get().strip()
        # dashboard is auto-derived from output_folder + identifier; restore explicit value if saved
        _load(self.dashboard_var,       'dashboard')
        self._dash_prev_auto[0] = self.dashboard_var.get().strip()
        # Product Config: load product_config_json, then pull analysis/sicc from product JSON
        # If not saved in the run JSON, auto-select the first central product config
        _yti = str(data.get('product_config_json', '') or '')
        _prod_cfg_explicit = bool(_yti)  # True when JSON explicitly saved a product_config_json
        if not _yti and self._prod_cfg_map:
            _yti = next(iter(self._prod_cfg_map.values()))
        self._prod_cfg_is_default[0] = not _prod_cfg_explicit
        if _yti:
            self.fail_bucket_var.set(_yti)
            # Sync dropdown: check if this path is in the central map
            _lbl = os.path.basename(_yti)
            if _lbl in self._prod_cfg_map:
                self._prod_cfg_dd_var.set(_lbl)
            else:
                # Custom path — add it to map and dropdown
                self._prod_cfg_map[_lbl] = _yti
                self._prod_cfg_dd_var.set(_lbl)
            self._load_product_cfg(_yti)
        # Explicit overrides from run JSON take priority over product config defaults
        _load(self.plot_json_var, 'analysis_info', 'plot_json')
        self.sicc_run_var.set(bool(data.get('sicc_run', False)))
        _load(self.sicc_out_var,     'sicc_output_dir')
        # AQUA Info
        _load(self.aqua_server_var,  'aquaserver')
        _load(self.aqua_cmd_var,     'aqua_cmd_path')
        _load(self.report_path_var,  'reportPath')
        _load(self.aqua_out_var,     'aqua_outputfile', 'outputFilename', 'DataCSV')
        # Populate data CSV listbox: primary first, then extras
        self._data_csv_lb.delete(0, tk.END)
        _primary = self.aqua_out_var.get().strip()
        if _primary:
            self._data_csv_lb.insert(tk.END, _primary)
        _extra = data.get('extra_csv_files', [])
        if isinstance(_extra, list):
            for _ep in _extra:
                _ep = str(_ep).strip()
                if _ep and _ep != _primary:
                    self._data_csv_lb.insert(tk.END, _ep)
        # Extra CSVs — keep dead text widget content blank
        self._extra_csv_text.delete('1.0', tk.END)
        # Bindef Info
        # Only overwrite tp_folder_var if the JSON explicitly has a value;
        # otherwise keep what _load_product_cfg already set from product config.
        _tp_folder_from_json = next(
            (str(data[k]) for k in ('TestProgram_folder', 'testProgram_folder') if data.get(k, '').strip()),
            None
        )
        if _tp_folder_from_json:
            self.tp_folder_var.set(_tp_folder_from_json)
        elif not self.tp_folder_var.get().strip():
            self.tp_folder_var.set('')
        _load(self.testprogram_var,  'TestProgram')
        # If TestProgram not saved in JSON, try reading it from the Data CSV
        if not data.get('TestProgram', '').strip():
            _csv_path = self.aqua_out_var.get().strip()
            if _csv_path:
                self._auto_populate_from_csv(_csv_path)
        # Use saved identifier if present; fall back to TestProgram
        # Set _id_prev_auto so the trace guard knows what was auto-set vs user-typed.
        if 'identifier' in data:
            _id_val = str(data['identifier'])
            self.testprogram_id_var.set(_id_val)
            # Mark as user-specified: set _id_prev_auto to something that won't match,
            # so the testprogram_var trace will NOT overwrite it.
            self._id_prev_auto[0] = '\x00'
        else:
            _id_val = self.testprogram_var.get()
            self.testprogram_id_var.set(_id_val)
            self._id_prev_auto[0] = _id_val
        # Parametric / PCM Options
        _pcm_full = getattr(self, 'pcm_full_site_var', None)
        if _pcm_full is not None:
            _pcm_full.set(bool(data.get('pcm_full_site', False)))
        # pcm_product_setup is now always the same as product_config_json — no separate field
        _pcm_spec = getattr(self, 'pcm_spec_csv_var', None)
        if _pcm_spec is not None:
            _pcm_spec.set(str(data.get('pcm_spec_csv', '')))
        _run_par = getattr(self, 'run_parametric_var', None)
        if _run_par is not None and 'run_parametric' in data:
            _run_par.set(bool(data.get('run_parametric', False)))
        # PCM filter / groups
        _pcm_filt = getattr(self, 'pcm_custom_filter_var', None)
        if _pcm_filt is not None:
            _pcm_filt.set(str(data.get('pcm_custom_filter', '')))
        _sel_grp_names = data.get('pcm_selected_groups', None)
        if _sel_grp_names is not None and hasattr(self, '_pcm_grp_vars'):
            for i, g in enumerate(self._pcm_groups):
                if i < len(self._pcm_grp_vars):
                    self._pcm_grp_vars[i].set(g['name'] in _sel_grp_names)
        self.fields = {}

    def _clear_fields(self):
        for w in self.fields_container.winfo_children():
            w.destroy()
        self.fields = {}

    def _populate_fields(self):
        self._clear_fields()
        BG, BG2, FG = '#1a252f', '#2c3e50', '#ecf0f1'
        # Show a subset of keys and any string values
        keys = list(self.json_data.keys())
        for k in keys:
            val = self.json_data.get(k)
            row = tk.Frame(self.fields_container, bg=BG)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=k, width=20, anchor='w',
                     bg=BG, fg=FG, font=('Consolas', 8)).pack(side=tk.LEFT)
            e = tk.Entry(row, width=38, bg=BG2, fg='white',
                         insertbackground='white', relief='flat',
                         font=('Consolas', 8))
            e.pack(side=tk.LEFT, padx=4)
            e.insert(0, str(val))
            btn = None
            if k in ('TestProgram_folder', 'testProgram_folder', 'TestProgramFolder'):
                btn = tk.Button(row, text='Browse', command=lambda en=e: self._browse_dir(en),
                                bg='#1f618d', fg='white', relief='flat', cursor='hand2')
            if k == 'dashboard':
                btn = tk.Button(row, text='Browse file', command=lambda en=e: self._browse_file(en),
                                bg='#1f618d', fg='white', relief='flat', cursor='hand2')
            if k in ('aqua_outputfile', 'outputFilename', 'output', 'DataCSV'):
                btn = tk.Button(row, text='Browse CSV', command=lambda en=e: self._browse_csv(en),
                                bg='#1f618d', fg='white', relief='flat', cursor='hand2')
            if k == 'product_config_json':
                btn = tk.Button(row, text='Browse', command=lambda en=e: self._browse_yieldtarget(en),
                                bg='#1f618d', fg='white', relief='flat', cursor='hand2')
            if k == 'plot_json':
                btn = tk.Button(row, text='Browse', command=lambda en=e: self._browse_plot_json_entry(en),
                                bg='#1f618d', fg='white', relief='flat', cursor='hand2')
            if btn:
                btn.pack(side=tk.LEFT, padx=4)
            self.fields[k] = e

    @staticmethod
    def _idir(var_or_path):
        """Return the best initialdir for a browse dialog from a StringVar or path string."""
        p = var_or_path.get() if hasattr(var_or_path, 'get') else (var_or_path or '')
        p = p.strip()
        if not p:
            return None
        d = p if os.path.isdir(p) else os.path.dirname(p)
        return d if os.path.isdir(d) else None

    def _browse_and_set(self, var, filetypes):
        f = filedialog.askopenfilename(filetypes=filetypes, initialdir=self._idir(var))
        if f:
            var.set(f)

    def _browse_dashboard_html(self, var):
        """Ask user: create new Dashboard.html (pick folder) or select existing file.
        Starts file dialog in the directory already set in `var` (if any)."""
        dlg = tk.Toplevel(self.winfo_toplevel())
        dlg.title('Dashboard HTML')
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

        tk.Label(dlg, text='Dashboard HTML', font=('Arial', 10, 'bold'),
                 padx=16, pady=8).grid(row=0, column=0, columnspan=2)
        tk.Label(dlg, text='Do you want to create a new Dashboard.html\nor select an existing one?',
                 justify='center', padx=16, pady=4).grid(row=1, column=0, columnspan=2)

        choice = [None]

        def _new():
            choice[0] = 'new'
            dlg.destroy()

        def _existing():
            choice[0] = 'existing'
            dlg.destroy()

        tk.Button(dlg, text='Create new', width=14, command=_new).grid(
            row=2, column=0, padx=8, pady=(6, 12))
        tk.Button(dlg, text='Select existing', width=14, command=_existing).grid(
            row=2, column=1, padx=8, pady=(6, 12))

        dlg.update_idletasks()
        px = self.winfo_rootx() + self.winfo_width() // 2 - dlg.winfo_width() // 2
        py = self.winfo_rooty() + self.winfo_height() // 2 - dlg.winfo_height() // 2
        dlg.geometry(f'+{px}+{py}')
        dlg.wait_window()

        _init = self._idir(var)
        if choice[0] == 'new':
            d = filedialog.askdirectory(title='Pick folder for new Dashboard.html',
                                        initialdir=_init)
            if d:
                d = d.replace('/', '\\')
                var.set(os.path.join(d, 'Dashboard.html'))
                if not self.output_folder_var.get().strip():
                    self.output_folder_var.set(d)
        elif choice[0] == 'existing':
            f = filedialog.askopenfilename(
                title='Select existing Dashboard.html',
                filetypes=[('HTML', '*.html'), ('All', '*.*')],
                initialdir=_init)
            if f:
                var.set(f)
                if not self.output_folder_var.get().strip():
                    self.output_folder_var.set(os.path.dirname(f).replace('/', '\\'))

    def _auto_set_dashboard(self):
        """Auto-set dashboard to <output_folder>/<identifier>/Dashboard.html.
        Only overwrites if empty or still matches the last auto-computed value."""
        cur = self.dashboard_var.get().strip()
        if cur and cur != self._dash_prev_auto[0]:
            return  # user-specified — don't overwrite
        out = self.output_folder_var.get().strip()
        if not out:
            return
        idf = self.testprogram_id_var.get().strip()
        _safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in idf)
        new_dash = os.path.join(out, _safe, 'Dashboard.html') if _safe else os.path.join(out, 'Dashboard.html')
        self.dashboard_var.set(new_dash)
        self._dash_prev_auto[0] = new_dash

    def _auto_set_output_folder(self):
        """Set output folder if not manually overridden.
        Derives from primary CSV parent folder.
        pipeline.py will append <identifier>/ itself."""
        cur = self.output_folder_var.get().strip()
        # Only overwrite if empty or still matches the last auto-computed value
        if cur and cur != self._out_prev_auto[0]:
            return

        new_out = ''
        # Derive from primary CSV path
        csv_items = list(self._data_csv_lb.get(0, tk.END))
        primary = csv_items[0].strip() if csv_items else ''
        if primary:
            new_out = os.path.dirname(os.path.normpath(primary))

        if new_out:
            self.output_folder_var.set(new_out)
            self._out_prev_auto[0] = new_out

    def reset_fields(self):
        """Clear all input fields back to their default (empty/startup) state."""
        import tkinter.messagebox as _mb
        if not _mb.askyesno('Reset', 'Clear all fields and start fresh?', default='no'):
            return
        # Text / path fields
        for var in (self.dashboard_var, self.output_folder_var,
                    self.testprogram_id_var, self.tp_folder_var, self.testprogram_var,
                    self.aqua_server_var, self.aqua_cmd_var, self.report_path_var,
                    self.plot_json_var, self.sicc_csv_var, self.sicc_out_var,
                    self.pcm_spec_csv_var, self.pcm_custom_filter_var, self.bindef_out_var):
            try: var.set('')
            except Exception: pass
        # Boolean fields — restore to startup defaults
        for var in (self.reticle_save_var, self.render_heatmap_var, self.render_pareto_var,
                    self.sicc_run_var, self.sicc_save_jmp_var, self.pcm_full_site_var):
            try: var.set(False)
            except Exception: pass
        try: self.render_wafermap_var.set(True)
        except Exception: pass
        try: self.run_parametric_var.set(True)
        except Exception: pass
        # Data CSV listbox
        try:
            self._data_csv_lb.delete(0, tk.END)
            self.aqua_out_var.set('')
        except Exception: pass
        # PCM group checkboxes — restore to all selected
        self._pcm_grp_select_all()
        self._pcm_cfg_selected = None
        # Reset auto-tracking state
        self._id_prev_auto[0]   = ''
        self._out_prev_auto[0]  = ''
        self._dash_prev_auto[0] = ''
        # Product Config — re-auto-select if only one central config
        try:
            if len(self._prod_cfg_map) == 1:
                lbl = next(iter(self._prod_cfg_map))
                self._prod_cfg_dd_var.set(lbl)
            else:
                self._prod_cfg_dd_var.set('(none)')
                self.fail_bucket_var.set('')
        except Exception: pass
        # Clear JSON data and input path
        self.json_data  = {}
        self.input_path = None

    def _browse_dir_into(self, var):
        d = filedialog.askdirectory(initialdir=self._idir(var))
        if d:
            var.set(d.replace('/', '\\'))

    def _browse_plot_json(self):
        f = filedialog.askopenfilename(
            title='Select Plot JSON',
            filetypes=[('JSON', '*.json'), ('All files', '*.*')],
            initialdir=self._idir(self.plot_json_var)
        )
        if f:
            self.plot_json_var.set(f)

    def _browse_plot_json_entry(self, entry_widget):
        f = filedialog.askopenfilename(
            title='Select Plot JSON',
            filetypes=[('JSON', '*.json'), ('All files', '*.*')]
        )
        if f:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, f)
            self.plot_json_var.set(f)

    def _browse_yieldtarget(self, entry_widget):
        f = filedialog.askopenfilename(
            title='Select YieldTarget Info',
            filetypes=[('JSON', '*.json'), ('Text files', '*.txt'), ('All files', '*.*')]
        )
        if f:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, f)
            self.fail_bucket_var.set(f)

    def _browse_product_cfg(self):
        f = filedialog.askopenfilename(
            title='Select Product Config JSON',
            filetypes=[('JSON', '*.json'), ('All files', '*.*')]
        )
        if f:
            self.fail_bucket_var.set(f)
            self._load_product_cfg(f)

    def _load_product_cfg(self, path):
        """Read sicc_run flag from a Product Config JSON.

        sicc_targets are read directly from the JSON by the SICC module at run
        time — no intermediate CSV is generated.
        """
        import json as _j
        try:
            data = _j.loads(open(path, encoding='utf-8').read())
        except Exception as _ex:
            msg = f'ERROR: Product Config JSON invalid ({path}):\n  {_ex}\n'
            try:
                self.output.insert(tk.END, msg)
                self.output.see(tk.END)
            except Exception:
                pass
            import tkinter.messagebox as _mb2
            _mb2.showerror('Product Config JSON Error', str(_ex))
            return
        if not isinstance(data, dict):
            return
        if 'sicc_run' in data:
            self.sicc_run_var.set(bool(data['sicc_run']))
        if 'pcm_spec_csv' in data and str(data['pcm_spec_csv']).strip():
            _spec = str(data['pcm_spec_csv']).strip()
            # Resolve relative paths against the JSON file's directory
            if not os.path.isabs(_spec):
                _spec = os.path.normpath(os.path.join(os.path.dirname(path), _spec))
            self.pcm_spec_csv_var.set(_spec)
        if 'testprogram_folder' in data and str(data['testprogram_folder']).strip():
            self.tp_folder_var.set(str(data['testprogram_folder']).strip())
        if 'pcm_param_groups' in data and isinstance(data['pcm_param_groups'], list):
            self._pcm_cfg_selected = [str(n) for n in data['pcm_param_groups']]
            if getattr(self, '_pcm_grp_vars', None):
                self._apply_pcm_group_selection(self._pcm_cfg_selected)
        else:
            self._pcm_cfg_selected = None
            if getattr(self, '_pcm_grp_vars', None):
                self._pcm_grp_select_all()

    def _browse_fail_bucket_table(self):
        f = filedialog.askopenfilename(
            title='Select YieldTarget Info',
            filetypes=[('JSON', '*.json'), ('Text files', '*.txt'), ('All files', '*.*')]
        )
        if f:
            self.fail_bucket_var.set(f)

    def _browse_csv(self, entry_widget):
        f = filedialog.askopenfilename(filetypes=[('CSV', '*.csv'), ('All files', '*.*')])
        if f:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, f)

    def _browse_data_csvs(self):
        """Open multi-select dialog; append chosen CSVs/ZIPs to the data listbox."""
        _items = list(self._data_csv_lb.get(0, tk.END))
        _first = _items[0] if _items else ''
        files = filedialog.askopenfilenames(
            title='Select Data CSV(s) / ZIP(s) / GZ(s) / 7Z(s)',
            filetypes=[('CSV / ZIP / GZ / 7Z', '*.csv *.zip *.gz *.csv.gz *.7z'), ('CSV', '*.csv'),
                       ('ZIP', '*.zip'), ('GZ', '*.gz *.csv.gz'), ('7Z', '*.7z'), ('All files', '*.*')],
            initialdir=self._idir(_first))
        if not files:
            return
        existing = set(_items)
        added = False
        for f in files:
            if f not in existing:
                self._data_csv_lb.insert(tk.END, f)
                existing.add(f)
                added = True
        if added:
            self._sync_aqua_out_var()
            primary = self._data_csv_lb.get(0)
            if primary:
                self._auto_populate_from_csv(primary)

    def _remove_data_csvs(self):
        """Remove currently selected entries from the data listbox."""
        for i in reversed(self._data_csv_lb.curselection()):
            self._data_csv_lb.delete(i)
        self._sync_aqua_out_var()

    def _sync_aqua_out_var(self):
        """Keep aqua_out_var in sync with the first item in the data listbox."""
        items = self._data_csv_lb.get(0, tk.END)
        self.aqua_out_var.set(items[0] if items else '')

    def _browse_data_csv(self):
        """Legacy single-file browse — delegates to multi-file version."""
        self._browse_data_csvs()

    def _browse_extra_csvs(self):
        """Legacy extra-CSV browse — appends to the data listbox (after position 0)."""
        self._browse_data_csvs()

    def _auto_populate_from_csv(self, csv_path):
        """Read the first value from the 'Program' column in the CSV (or first CSV
        inside a ZIP) and auto-set TestProgram (→ Identifier) and Output folder.
        Only overwrites testprogram_var if it is currently empty."""
        import csv as _csv
        import zipfile as _zf
        import io as _io
        import gzip as _gz

        def _read_program(fh):
            reader = _csv.DictReader(_io.TextIOWrapper(fh, encoding='utf-8', errors='replace'))
            hdrs = reader.fieldnames or []
            # Match any column whose name contains 'program' (case-insensitive)
            prog_col = next((h for h in hdrs if 'program' in h.lower()), None)
            if not prog_col:
                return ''
            for row in reader:
                prog = (row.get(prog_col) or '').strip()
                if prog:
                    return prog
            return ''

        def _zip_pick(zf):
            """Return first .csv entry, or first non-directory entry as fallback."""
            names = zf.namelist()
            csv_name = next((n for n in names if n.lower().endswith('.csv') and not n.endswith('/')), None)
            return csv_name or next((n for n in names if not n.endswith('/')), None)

        prog = ''
        try:
            if csv_path.lower().endswith('.zip'):
                with _zf.ZipFile(csv_path) as zf:
                    name = _zip_pick(zf)
                    if name:
                        with zf.open(name) as f:
                            prog = _read_program(f)
            elif csv_path.lower().endswith('.gz'):
                with _gz.open(csv_path, 'rb') as f:
                    prog = _read_program(f)
            else:
                with open(csv_path, 'rb') as f:
                    prog = _read_program(f)
        except Exception:
            pass

        if prog and not self.testprogram_var.get().strip():
            self.testprogram_var.set(prog)
            # testprogram_id_var is synced via trace on testprogram_var

        # Auto-select Product Config JSON by reading DevRevStep from the CSV content.
        # Looks for any column whose name starts with "DevRevStep" (e.g. DevRevStep_119325).
        # Matches the value against config labels: "<DEVREV> - SORT - *.json".
        # Only auto-select if the dropdown is currently at (none) or was auto-defaulted.
        if (self._prod_cfg_dd_var.get() == '(none)' or self._prod_cfg_is_default[0]) and self._prod_cfg_map:
            _devrev = ''
            try:
                def _read_devrev(fh):
                    reader = _csv.DictReader(_io.TextIOWrapper(fh, encoding='utf-8', errors='replace'))
                    hdrs = reader.fieldnames or []
                    _col = next((h for h in hdrs if h.lower().startswith('devrevstep')), None)
                    if not _col:
                        return ''
                    for row in reader:
                        v = (row.get(_col) or '').strip()
                        if v:
                            return v
                    return ''
                if csv_path.lower().endswith('.zip'):
                    with _zf.ZipFile(csv_path) as zf:
                        name = _zip_pick(zf)
                        if name:
                            with zf.open(name) as f:
                                _devrev = _read_devrev(f)
                elif csv_path.lower().endswith('.gz'):
                    with _gz.open(csv_path, 'rb') as f:
                        _devrev = _read_devrev(f)
                else:
                    with open(csv_path, 'rb') as f:
                        _devrev = _read_devrev(f)
            except Exception:
                pass
            if _devrev:
                _dv = _devrev.upper()
                _dv6 = _dv[:6]
                def _cfg_matches(lbl):
                    # Extract first token from label, e.g. "8PF5CVL" from "8PF5CVL - SORT - ..."
                    _tok = lbl.split(' - ')[0].strip().upper()
                    # Match if: DevRevStep starts with token, OR token starts with first 6 chars
                    return _dv.startswith(_tok) or _tok.startswith(_dv6)
                _match_lbl = next(
                    (lbl for lbl in self._prod_cfg_map if _cfg_matches(lbl)),
                    None
                )
                if _match_lbl:
                    self._prod_cfg_dd_var.set(_match_lbl)
                    self._prod_cfg_is_default[0] = False  # now explicitly matched

        # Auto-set output folder based on dashboard path + identifier
        self._auto_set_output_folder()

    def get_extra_csv_paths(self):
        """Return list of extra CSV paths (items 1..n in the data listbox)."""
        items = list(self._data_csv_lb.get(0, tk.END))
        return items[1:]  # everything after the primary CSV

    def _browse_dir(self, entry_widget):
        d = filedialog.askdirectory()
        if d:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, d)

    def _browse_file(self, entry_widget):
        f = filedialog.askopenfilename()
        if f:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, f)

