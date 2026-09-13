@echo off
setlocal
@chcp 65001 >nul
title ULTRON - Environment Setup

cd /d "%~dp0"

set "SETUP_PY="

py -3.14 -c "exit(0)" >nul 2>nul
if not errorlevel 1 (
    set SETUP_PY=py -3.14
) else (
    python -c "import sys; exit(0 if sys.version_info[:2]==(3,14) else 1)" >nul 2>nul
    if not errorlevel 1 (
        set SETUP_PY=python
    )
)

if "%SETUP_PY%"=="" (
    echo Python 3.14 was not found.
    echo Install CPython 3.14 for Windows, including the Python Launcher, then run this again.
    pause
    exit /b 1
)

%SETUP_PY% ULTRON_SETUP.py %*
set "SETUP_EXIT=%ERRORLEVEL%"
if not "%SETUP_EXIT%"=="0" (
    echo.
    echo ULTRON setup failed. Read the error above; no source files were changed.
    pause
)
exit /b %SETUP_EXIT%
