@echo off
setlocal
@chcp 65001 >nul
title ULTRON - Wake Word Listener

cd /d "%~dp0"

echo [NOTICE] wake_service.py is legacy (Phase P1 / Kill List #1).
echo OpenWakeWord in-process engine is the target replacement.
echo.

set "ULTRON_PY="

if defined ULTRON_PYTHON (
    if exist "%ULTRON_PYTHON%" set "ULTRON_PY=%ULTRON_PYTHON%"
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

%ULTRON_PY% -c "import pyaudio" >nul 2>nul
if errorlevel 1 (
    echo The experimental wake-word service requires PyAudio, which is not part of the supported core install.
    echo Install a compatible PyAudio wheel, then run this launcher again.
    pause
    exit /b 1
)

%ULTRON_PY% wake_service.py %*
pause
