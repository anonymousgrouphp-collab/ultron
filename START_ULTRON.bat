@echo off
setlocal
@chcp 65001 >nul
title ULTRON - Desktop Assistant

cd /d "%~dp0"

set "ULTRON_PY="

if defined ULTRON_PYTHON (
    if exist "%ULTRON_PYTHON%" set "ULTRON_PY=%ULTRON_PYTHON%"
)

if "%ULTRON_PY%"=="" (
    py -3.14 -c "import sounddevice" >nul 2>nul
    if not errorlevel 1 set ULTRON_PY=py -3.14
)

if "%ULTRON_PY%"=="" (
    python -c "import sys, sounddevice; exit(0 if sys.version_info[:2]==(3,14) else 1)" >nul 2>nul
    if not errorlevel 1 set ULTRON_PY=python
)

if "%ULTRON_PY%"=="" (
    py -3.14 -c "exit(0)" >nul 2>nul
    if not errorlevel 1 set ULTRON_PY=py -3.14
)

if "%ULTRON_PY%"=="" (
    python -c "import sys; exit(0 if sys.version_info[:2]==(3,14) else 1)" >nul 2>nul
    if not errorlevel 1 set ULTRON_PY=python
)

if "%ULTRON_PY%"=="" (
    if exist ".venv\Scripts\python.exe" set "ULTRON_PY=.venv\Scripts\python.exe"
)

if "%ULTRON_PY%"=="" (
    echo ULTRON requires Python 3.14. Creating the supported environment...
    call SETUP.bat
    if errorlevel 1 exit /b %ERRORLEVEL%
    set "ULTRON_PY=python"
)

%ULTRON_PY% main.py %*
set "ULTRON_EXIT=%ERRORLEVEL%"
if not "%ULTRON_EXIT%"=="0" (
    echo.
    echo ULTRON closed with exit code %ULTRON_EXIT%.
    pause
)
exit /b %ULTRON_EXIT%
