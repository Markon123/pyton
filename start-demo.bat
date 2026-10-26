@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
py -3 -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1
if not errorlevel 1 (
    py -3 scripts\launch_demo.py %*
    goto finish
)
python -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1
if not errorlevel 1 (
    python scripts\launch_demo.py %*
    goto finish
)
echo Python 3.12 or newer was not found.
echo Install Python from python.org, enable "Add Python to PATH", then run this file again.
pause
exit /b 1
:finish
if errorlevel 1 (
    echo.
    echo Startup failed. Copy the error above or open .local\launcher.log.
    pause
    exit /b 1
)
