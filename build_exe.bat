@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "DIST=%~dp0dist\vial-matrix-light.exe"
set "BUILDDEPS=%~dp0.build\py36"
set "PYTHON_EXE="

echo ============================================
echo   Build single-file exe (PyInstaller)
echo ============================================
echo.

REM ---- 1) Pick a Python that can run this project -----------------
REM vendor\ is Flask 1.1.x, so stick to 3.6-3.10 (PyInstaller 4.10 range).
where py >nul 2>&1
if not errorlevel 1 (
    for %%V in (3.6 3.7 3.8 3.9 3.10) do (
        if not defined PYTHON_EXE (
            py -%%V -c "import sys; sys.path.insert(0, r'%~dp0vendor'); import flask" >nul 2>&1
            if not errorlevel 1 set "PYTHON_EXE=py -%%V"
        )
    )
)

if not defined PYTHON_EXE (
    python -c "import sys; sys.path.insert(0, r'%~dp0vendor'); import flask; sys.exit(0 if (3,6)<=sys.version_info[:2]<=(3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=python"
)

if not defined PYTHON_EXE (
    for %%D in ("%LOCALAPPDATA%\Programs\Python" "C:\Program Files" "C:\Program Files (x86)") do (
        if exist "%%~D" (
            for /f "delims=" %%P in ('dir /b /ad /o-n "%%~D\python3*" 2^>nul') do (
                if not defined PYTHON_EXE (
                    "%%~D\%%P\python.exe" -c "import sys; sys.path.insert(0, r'%~dp0vendor'); import flask" >nul 2>&1
                    if not errorlevel 1 set "PYTHON_EXE=%%~D\%%P\python.exe"
                )
            )
        )
    )
)

if not defined PYTHON_EXE (
    echo [X] No usable Python 3.6-3.10 was found.
    echo     Install one from https://www.python.org/downloads/
    echo     The interpreter must be able to load Flask from vendor\.
    pause
    exit /b 2
)
echo [1/4] Interpreter: %PYTHON_EXE%

REM ---- 2) PyInstaller lives in an isolated dir, never system-wide --
if not exist "%BUILDDEPS%\PyInstaller\__main__.py" (
    echo [2/4] Installing PyInstaller 4.10 into .build\py36 ...
    %PYTHON_EXE% -m pip install --target "%BUILDDEPS%" "pyinstaller==4.10"
    if errorlevel 1 goto :failed
) else (
    echo [2/4] PyInstaller already present in .build\py36
)

REM ---- 3) Build ---------------------------------------------------
echo [3/4] Building (onefile) ...
set "PYTHONPATH=%BUILDDEPS%"
%PYTHON_EXE% -m PyInstaller --noconfirm --clean "%~dp0vial-matrix-light.spec"
if errorlevel 1 goto :failed

if not exist "%DIST%" (
    echo [X] Build finished but %DIST% is missing.
    goto :failed
)

REM ---- 4) Verify the frozen exe really works ----------------------
echo.
echo [4/4] Verifying frozen exe (--selftest) ...
"%DIST%" --selftest
if errorlevel 1 (
    echo.
    echo [X] The exe was built but its self-test FAILED.
    echo     Run it by hand to see the full output:
    echo         "%DIST%" --selftest
    goto :failed
)

echo.
for %%F in ("%DIST%") do set "EXESIZE=%%~zF"
set /a EXEMB=%EXESIZE%/1048576
echo ============================================
echo   OK  dist\vial-matrix-light.exe  (!EXEMB! MB)
echo ============================================
echo.
echo   Double-click it: a console window opens, the browser follows.
echo   Target machines need no Python and no pip install.
echo.
endlocal
exit /b 0

:failed
echo.
echo ============================================
echo   BUILD FAILED
echo ============================================
echo   Check the messages above. Common causes:
echo     - antivirus locked build\ or dist\
echo     - a stray python.exe still running from a previous test
echo.
pause
endlocal
exit /b 1
