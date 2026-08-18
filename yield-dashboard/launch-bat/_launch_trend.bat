@echo off
for /f "delims=" %%P in ('where py 2^>nul') do set "PYEXE=py" & goto :found
set "PYEXE=python"
:found
"%PYEXE%" "\\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa002\users\snpant\tools\scripts\app.dashboard.nvl\yield-dashboard\yld\run_trend.py"
