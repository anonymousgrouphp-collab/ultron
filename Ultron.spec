# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = [
    ('actions', 'actions'),
    ('config', 'config'),
    ('core', 'core'),
    ('dashboard', 'dashboard'),
    ('memory', 'memory'),
    ('ui.py', '.'),
    ('wake_service.py', '.'),
    ('requirements.txt', '.'),
    ('readme.md', '.'),
]

binaries = []

hiddenimports = [
    'PyQt6',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtWebEngineCore',
    'google.genai',
    'google.genai.types',
    'google.generativeai',
    'sounddevice',
    'soundfile',
    'speech_recognition',
    'pyaudio',
    'psutil',
    'requests',
    'bs4',
    'duckduckgo_search',
    'playwright',
    'pyautogui',
    'pyperclip',
    'pygetwindow',
    'cv2',
    'numpy',
    'mss',
    'send2trash',
    'youtube_transcript_api',
    'pptx',
    'fastapi',
    'uvicorn',
    'cryptography',
    'comtypes',
    'pycaw',
    'win10toast',
    'pywinauto',
    'qrcode',
    'PIL',
    'actions.file_processor',
    'actions.flight_finder',
    'actions.open_app',
    'actions.weather_report',
    'actions.send_message',
    'actions.reminder',
    'actions.computer_settings',
    'actions.screen_processor',
    'actions.youtube_video',
    'actions.desktop',
    'actions.browser_control',
    'actions.file_controller',
    'actions.code_helper',
    'actions.dev_agent',
    'actions.web_search',
    'actions.computer_control',
    'actions.game_updater',
    'actions.system_monitor',
    'actions.proactive',
    'memory.memory_manager',
    'memory.config_manager',
    'memory.cmr_manager',
    'memory.reminder_manager',
    'core.llm_client',
    'core.stt',
    'core.tts',
    'core.installer',
    'dashboard.server',
]

for pkg in ['PyQt6', 'sounddevice', 'speech_recognition', 'google.genai', 'playwright', 'uvicorn', 'fastapi']:
    try:
        tmp_datas, tmp_binaries, tmp_hidden = collect_all(pkg)
        datas.extend(tmp_datas)
        binaries.extend(tmp_binaries)
        hiddenimports.extend(tmp_hidden)
    except Exception as e:
        print(f"Hook collection warning for {pkg}: {e}")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Ultron',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='config/jarvis.ico' if os.path.exists('config/jarvis.ico') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Ultron',
)
