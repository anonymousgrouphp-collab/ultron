@echo off
title ULTRON — Wake Word Service
color 0A

echo ==============================================
echo   ULTRON — Starting Wake Word Listener...
echo ==============================================

cd /d "%~dp0"

echo Checking required voice packages...
python -m pip install SpeechRecognition PyAudio psutil --quiet

echo.
echo Launching Wake Word Listener ("wake up ultron")...
python wake_service.py

echo.
pause
