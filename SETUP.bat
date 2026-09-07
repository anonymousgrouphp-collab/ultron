@echo off
setlocal
title ULTRON - Environment Setup

cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python Launcher was not found.
    echo Install CPython 3.13 for Windows, including the Python Launcher, then run this again.
    pause
    exit /b 1
)

py -3.13 ULTRON_SETUP.py %*
set "SETUP_EXIT=%ERRORLEVEL%"
if not "%SETUP_EXIT%"=="0" (
    echo.
    echo ULTRON setup failed. Read the error above; no source files were changed.
    pause
)
exit /b %SETUP_EXIT%
