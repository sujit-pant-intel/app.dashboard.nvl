@echo off
set PYTHON=C:\Users\sangkeet\AppData\Local\Programs\Python\Python314\python.exe
:: Install/upgrade required packages (Intel proxy)
echo Checking dependencies...
"%PYTHON%" -m pip install --quiet --proxy http://proxy-us.intel.com:912 -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo WARNING: pip install failed - continuing anyway...
)
echo Starting CLASS Dashboard...
"%PYTHON%" "%~dp0dashboard.py" %*
if errorlevel 1 (
    echo.
    echo Dashboard exited with an error. See above for details.
    pause
)
