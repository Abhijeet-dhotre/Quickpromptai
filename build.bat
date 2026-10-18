@echo off
rem ============================================================
rem  QuickPrompt AI - Windows build script
rem  Produces: dist\QuickPromptAI.exe  (one-file, windowed)
rem ============================================================
setlocal
cd /d "%~dp0"

echo === QuickPrompt AI build ===

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH. Install Python 3.10+ from python.org.
    exit /b 1
)

echo [1/5] Installing dependencies...
python -m pip install -r requirements-dev.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    exit /b 1
)

echo [2/5] Generating icon...
python assets\make_icon.py
if errorlevel 1 (
    echo [ERROR] Icon generation failed - is Pillow installed?
    exit /b 1
)

echo [3/5] Stopping any running QuickPrompt AI...
rem A running instance keeps dist\QuickPromptAI.exe open, which makes PyInstaller
rem fail while deleting or rewriting it and waste the whole build.
taskkill /IM QuickPromptAI.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul

echo [4/5] Running test suite...
python -m pytest tests -q
if errorlevel 1 (
    echo [ERROR] Tests failed - build aborted.
    exit /b 1
)

echo [5/5] Building executable with PyInstaller...
python -m PyInstaller quickprompt.spec --noconfirm --clean
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

echo.
echo ============================================================
echo  Build complete: dist\QuickPromptAI.exe
echo  Smoke test it with:  dist\QuickPromptAI.exe --smoke-test
echo ============================================================
exit /b 0
