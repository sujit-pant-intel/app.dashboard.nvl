@echo off
:: Install/upgrade required packages (Intel proxy)
echo Checking dependencies...
python -m pip install --quiet --proxy http://proxy-us.intel.com:911 -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo WARNING: pip install failed - continuing anyway...
)
echo Starting CLASS Dashboard...
python "%~dp0dashboard.py" %*
if errorlevel 1 (
    echo.
    echo Dashboard exited with an error. See above for details.
    pause
)
