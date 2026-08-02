@echo off
title ULTRON — AI Desktop Assistant
color 0A

echo ===================================================
echo    ULTRON AI Engine — One-Click Setup and Launch
echo ===================================================
echo.

cd /d "%~dp0"

REM ── Check Python is installed ─────────────────────────────────────────
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python was not found on this PC.
    echo.
    echo Please install Python 3.10 or newer from https://python.org
    echo During install, make sure to check "Add python.exe to PATH".
    echo Then double-click this file again.
    echo.
    pause
    exit /b 1
)

echo [1/4] Checking Python dependencies (first run may take a few minutes)...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Package installation failed. Check your internet connection
    echo and that Python 3.10+ is installed, then try again.
    pause
    exit /b %ERRORLEVEL%
)
echo       Done.

echo.
echo [2/4] Checking Playwright browser engine...
python -m playwright install chromium --with-deps >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo       WARNING: Playwright browser setup had an issue.
    echo       Web-browsing features may need manual setup later.
) else (
    echo       Done.
)

echo.
echo [3/4] Checking configuration...
if not exist "config\api_keys.json" (
    if exist "config\api_keys.json.example" (
        copy "config\api_keys.json.example" "config\api_keys.json" >nul
        echo       Created config\api_keys.json from template.
    )
)
echo       Done. (If no key is set yet, ULTRON will ask for it in a popup.)

echo.
echo [4/4] Launching ULTRON...
echo ===================================================
echo.
python main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ULTRON closed with an error ^(code %ERRORLEVEL%^).
    pause
)
