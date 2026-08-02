# ⚡ ULTRON AI Desktop Assistant

> **ULTRON** is a next-generation AI Desktop Assistant powered by Google Gemini, PyQt6, and hands-free voice recognition. It features real-time voice synthesis, computer control automation, hardware telemetry monitoring, browser automation, and a remote web dashboard.

---

## 🚀 Key Features

- 🤖 **Google Gemini Engine**: Ultra-fast LLM responses powered by native audio and text models.
- 🎙️ **Hands-Free Voice Recognition & TTS**: Neural speech output and real-time wake word listener ("Wake up Ultron").
- 💻 **Computer Control & Automation**: Control system volume, brightness, media playback, applications, window management, and custom shortcuts.
- 🌐 **Automated Web Browsing**: Integrated Playwright engine for automated search, page extraction, and web tasks.
- 📊 **Hardware Telemetry Monitor**: Real-time CPU, RAM, GPU, battery, network, and process status.
- 📱 **Remote Control Dashboard**: Web-based control HUD accessible locally and from smartphone browsers.
- 📦 **1-Click Installer Packaging**: Complete build pipeline using PyInstaller and Inno Setup to create standalone `ULTRON_Setup.exe`.

---

## 📁 Repository Structure

```
ULTRON_GitHub/
├── START_ULTRON.bat            # 1-Click launcher script for development
├── Start_ULTRON_Wake_Word.bat  # Background voice wake-word listener
├── build_release.py            # Automated build script (PyInstaller + Inno Setup)
├── installer.iss               # Inno Setup installer script
├── Ultron.spec                 # PyInstaller production spec configuration
├── main.py                     # Main assistant entry point & event loop
├── ui.py                       # PyQt6 WebEngine visual HUD interface
├── wake_service.py              # Always-on voice wake word background service
├── setup.py                    # Environment configuration script
├── requirements.txt            # Python dependencies
├── .gitignore                  # Git exclusion rules (keeps repo < 100 MB)
├── config/                     # Configuration files & icons
│   ├── api_keys.json.example   # Configuration template for API keys
│   └── jarvis.ico              # Application icon
├── core/                       # LLM client, TTS, STT, and engine logic
├── actions/                    # Assistant action tools (browser, system, desktop)
├── dashboard/                  # Remote web dashboard server & web assets
└── memory/                     # Local assistant memory & preference persistence
```

---

## ⚡ Quick Start for Developers

### Prerequisites
- Windows 10 / 11 (64-bit)
- Python 3.10+ installed and added to `PATH`

### 1️⃣ Clone & Setup
```bash
git clone https://github.com/your-username/ULTRON.git
cd ULTRON
pip install -r requirements.txt
python -m playwright install chromium
```

### 2️⃣ Configure Gemini API Key
1. Copy `config/api_keys.json.example` to `config/api_keys.json`.
2. Paste your Google Gemini API key into `config/api_keys.json`:
```json
{
    "gemini_api_key": "YOUR_GEMINI_API_KEY_HERE"
}
```
> Get a free API key at [Google AI Studio](https://aistudio.google.com/apikey).

### 3️⃣ Run ULTRON
Double-click `START_ULTRON.bat` or run:
```bash
python main.py
```

---

## 📦 Building the Standalone 1-Click Installer (`ULTRON_Setup.exe`)

To compile ULTRON into a zero-dependency Windows installer (`ULTRON_Setup.exe`) for end-users:

1. Install PyInstaller & Inno Setup 6:
```bash
pip install pyinstaller
winget install JRSoftware.InnoSetup
```
2. Run the automated build script:
```bash
python build_release.py
```
3. The compiled installer will be generated in `Release/ULTRON_Setup.exe`.

---

## 🎤 Hands-Free Wake Word Setup

To enable background listening:
- Double-click **`Start_ULTRON_Wake_Word.bat`**.
- Say **"Wake up ultron"** or **"Hey ultron"** to automatically bring up the assistant!

---

## 🛡️ License & Contributing

Contributions, issues, and feature requests are welcome!  
Feel free to star ⭐ the repository if you enjoy using ULTRON.
