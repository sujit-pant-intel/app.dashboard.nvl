import os, sys
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
from pathlib import Path
import tkinter as tk
from tkinter import ttk

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR    = os.path.join(_SCRIPT_DIR, 'src')
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from hry_frame import HRYFrame

BG   = '#1a252f'
BG2  = '#2c3e50'
FG   = '#ecf0f1'
FG2  = '#95a5a6'
ABLU = '#3498db'


class HRYApp(tk.Tk):
    def __init__(self, auto_load_json: str = ''):
        super().__init__()
        self.title('HRY Scan Analysis')
        self.geometry('920x820')
        self.configure(bg=BG)
        style = ttk.Style(self)
        style.theme_use('default')
        style.configure('App.TNotebook', background=BG, borderwidth=0, tabmargins=[2, 4, 2, 0])
        style.configure('App.TNotebook.Tab', background='#253545', foreground=FG2,
                        padding=[14, 5], font=('Arial', 9, 'bold'), borderwidth=0)
        style.map('App.TNotebook.Tab',
                  background=[('selected', BG), ('active', BG2)],
                  foreground=[('selected', ABLU), ('active', FG)])
        nb = ttk.Notebook(self, style='App.TNotebook')
        nb.pack(fill='both', expand=True, padx=0, pady=0)
        self._hry_tab = HRYFrame(nb)
        nb.add(self._hry_tab, text='   HRY Scan Analysis   ')
        if auto_load_json:
            self._hry_tab.auto_load(auto_load_json)


if __name__ == '__main__':
    _json_arg = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith('.json') else ''
    HRYApp(auto_load_json=_json_arg).mainloop()
