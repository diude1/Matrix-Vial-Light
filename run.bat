@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "LOGFILE=%~dp0run.log"
set "PYTHON_EXE="
set "PYTHON_ARGS="

echo ============================================
echo   Matrix / Vial Keyboard Light Control
echo ============================================
echo.

REM ---- 1) Prefer the Windows "py" launcher -------------------------
REM Flask ships inside vendor\, so any Python 3.6+ works - no pip needed.
REM The probe below asks the interpreter to do exactly what main.py does:
REM put vendor\ on sys.path first, then import flask.
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; sys.path.insert(0, r'%~dp0vendor'); import flask" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=py"
        set "PYTHON_ARGS=-3"
        echo [1/3] Using launcher: py -3
        goto :launch
    )
)

REM ---- 2) Fall back to "python" on PATH ---------------------------
where python >nul 2>&1
if not errorlevel 1 (
    python -c "import sys; sys.path.insert(0, r'%~dp0vendor'); import flask" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=python"
        echo [1/3] Using interpreter: python
        goto :launch
    )
)

REM ---- 3) Common install locations --------------------------------
for %%D in (
    "%LOCALAPPDATA%\Programs\Python"
    "C:\Program Files"
    "C:\Program Files (x86)"
    "C:\"
) do (
    if exist "%%~D" (
        for /f "delims=" %%P in ('dir /b /o-n "%%~D\Python3*" 2^>nul') do (
            if exist "%%~D\%%P\python.exe" (
                if not defined PYTHON_EXE (
                    "%%~D\%%P\python.exe" -c "import sys; sys.path.insert(0, r'%~dp0vendor'); import flask" >nul 2>&1
                    if not errorlevel 1 set "PYTHON_EXE=%%~D\%%P\python.exe"
                )
            )
        )
    )
)
if defined PYTHON_EXE (
    echo [1/3] Using interpreter: %PYTHON_EXE%
    goto :launch
)

echo [X] No usable Python was found.
echo.
echo     This program needs Python 3.6 or newer. Flask itself is already
echo     bundled in the vendor folder, so there is nothing to install.
echo.
echo     Get Python 3 from https://www.python.org/downloads/
echo     Remember to tick "Add Python to PATH" during setup.
echo.
echo     A command line version works without the web interface:
echo         python mvl.py --help
echo.
pause
exit /b 2

:launch
echo [2/3] Starting web server (browser will open)...
echo       Log: %LOGFILE%
echo.
echo       Keep this window open while using the interface.
echo       Press Ctrl+C to stop.
echo.

REM Launch and capture every stream so failures are never silent.
%PYTHON_EXE% %PYTHON_ARGS% "%~dp0main.py" >"%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo ============================================
    echo [X] Startup FAILED  ^(exit code %RC%^)
    echo ============================================
    echo.
    echo --- run.log ---------------------------------
    type "%LOGFILE%"
    echo ---------------------------------------------
    echo.
    echo Full log saved to: %LOGFILE%
    echo.
    pause
) else (
    echo [3/3] Server stopped normally.
    echo       If the browser never opened, the address is in %LOGFILE%
)

endlocal
exit /b 0
