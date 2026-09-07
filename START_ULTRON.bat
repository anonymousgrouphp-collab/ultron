@echo off
setlocal
title ULTRON - Desktop Assistant

cd /d "%~dp0"
set "ULTRON_PYTHON=.venv\Scripts\python.exe"

if not exist "%ULTRON_PYTHON%" (
    echo ULTRON is not set up yet. Creating the supported Python 3.13 environment...
    call SETUP.bat
    if errorlevel 1 exit /b %ERRORLEVEL%
)

"%ULTRON_PYTHON%" main.py
set "ULTRON_EXIT=%ERRORLEVEL%"
if not "%ULTRON_EXIT%"=="0" (
    echo.
    echo ULTRON closed with exit code %ULTRON_EXIT%.
    pause
)
exit /b %ULTRON_EXIT%
