@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================
echo   Optional: install the hidapi backend
echo ============================================
echo.
echo This program runs on Windows with ZERO dependencies (a built-in
echo ctypes HID backend is included). Installing hidapi just makes the
echo low-level layer use the official library. Functionality is identical.
echo.

where py >nul 2>&1
if not errorlevel 1 (
    py -3 -m pip install --user hidapi
) else (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -m pip install --user hidapi
    ) else (
        echo [X] Python not found on PATH.
        echo     Install Python 3 first, then run this script again.
    )
)

echo.
echo Done. You can now run run.bat
pause
