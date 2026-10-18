@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  QuickPrompt AI - Windows dependency installer
rem  Creates .venv and installs the runtime requirements.
rem ============================================================

echo.
echo === QuickPrompt AI installer ===

set "QP_PYTHON="
py -3 --version >nul 2>&1
if not errorlevel 1 set "QP_PYTHON=py -3"

if not defined QP_PYTHON (
    python --version >nul 2>&1
    if not errorlevel 1 set "QP_PYTHON=python"
)

if not defined QP_PYTHON (
    echo [ERROR] Python 3.10 or later was not found.
    echo Install it from https://www.python.org/downloads/ and select
    echo "Add Python to PATH", then run this file again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    %QP_PYTHON% -m venv .venv
    if errorlevel 1 goto :failed
) else (
    echo [1/3] Using existing virtual environment.
)

echo [2/3] Updating pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed

echo [3/3] Installing QuickPrompt AI dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Installation complete.
echo Start the application with: .venv\Scripts\python.exe main.py
echo.
pause
exit /b 0

:failed
echo.
echo [ERROR] Installation failed. Check the messages above and try again.
pause
exit /b 1
