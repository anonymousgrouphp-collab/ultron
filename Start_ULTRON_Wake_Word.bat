@echo off
setlocal
title ULTRON - Wake Word Listener

cd /d "%~dp0"
set "ULTRON_PYTHON=.venv\Scripts\python.exe"

if not exist "%ULTRON_PYTHON%" (
    echo ULTRON is not set up yet. Creating the supported Python 3.13 environment...
    call SETUP.bat
    if errorlevel 1 exit /b %ERRORLEVEL%
)

"%ULTRON_PYTHON%" -c "import pyaudio" >nul 2>nul
if errorlevel 1 (
    echo The experimental wake-word service requires PyAudio, which is not part of the supported core install.
    echo Install a compatible PyAudio wheel into .venv, then run this launcher again.
    pause
    exit /b 1
)

"%ULTRON_PYTHON%" wake_service.py
pause
