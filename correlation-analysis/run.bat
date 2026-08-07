@echo off
echo Installing requirements...
pip install -r requirements.txt --proxy http://proxy-us.intel.com:911 --quiet
if %errorlevel% neq 0 (
    echo ERROR: Failed to install requirements. Check your network/proxy.
    pause
    exit /b 1
)
echo Starting correlation-analysis...
python correlation-analysis.py
pause
