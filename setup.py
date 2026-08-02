import subprocess
import sys
import platform
from pathlib import Path

print("Installing requirements...")
subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)

print("Installing Playwright browsers...")
subprocess.run([sys.executable, "-m", "playwright", "install"], check=True)

if platform.system() == "Windows":
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        postinstall = Path(sys.executable).parent / "Scripts" / "pywin32_postinstall.py"
        print(
            "\n⚠️  pywin32 did not install correctly — desktop shortcut creation "
            "will fall back to a slower method that may not work on this machine.\n"
            "    Try fixing it manually with:\n"
            f'    "{sys.executable}" -m pip install --force-reinstall pywin32\n'
            f'    "{sys.executable}" "{postinstall}" -install\n'
        )

# Check and create config/api_keys.json from example if it doesn't exist
config_dir = Path("config")
api_key_file = config_dir / "api_keys.json"
example_file = config_dir / "api_keys.json.example"

if not api_key_file.exists() and example_file.exists():
    import shutil
    shutil.copy(example_file, api_key_file)
    print("📋 Created config/api_keys.json from template. Please add your Gemini API key inside config/api_keys.json.")

print("\n✅ Setup complete! Run 'python main.py' or 'run.bat' to start HUNNY (ULTRON AI Engine).")

