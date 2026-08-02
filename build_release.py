"""
ULTRON Automated Production Build & Release Packaging Tool
=========================================================
1. Builds standalone PyInstaller executable (Ultron.exe)
2. Bundles Playwright Chromium browser binary for offline use
3. Compiles Inno Setup installer into Release/ULTRON_Setup.exe
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

# Force UTF-8 output formatting for Windows console compatibility
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "dist" / "Ultron"
RELEASE_DIR = BASE_DIR / "Release"

ISCC_PATHS = [
    r"C:\Users\mad98\AppData\Local\Programs\Inno Setup 6\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    shutil.which("iscc"),
]

def find_iscc() -> str | None:
    for path in ISCC_PATHS:
        if path and os.path.exists(path):
            return str(path)
    return None

def main():
    print("=" * 60)
    print(" ULTRON AI Desktop Assistant -- Production Build Pipeline")
    print("=" * 60)

    # 1. Clean previous build artifacts
    print("\n[1/5] Cleaning previous build folders...")
    for folder in ["build", "dist", "Release"]:
        p = BASE_DIR / folder
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            print(f"      Removed {folder}/")

    RELEASE_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Run PyInstaller
    print("\n[2/5] Compiling PyInstaller standalone executable...")
    spec_path = BASE_DIR / "Ultron.spec"
    cmd = [sys.executable, "-m", "PyInstaller", str(spec_path), "--noconfirm"]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        print("\nERROR: PyInstaller build failed!")
        sys.exit(res.returncode)

    if not (DIST_DIR / "Ultron.exe").exists():
        print("\nERROR: Ultron.exe missing from dist folder!")
        sys.exit(1)
    print("      PyInstaller build successful [OK]")

    # 3. Bundle Playwright Chromium for offline usage (only Chromium to keep build light)
    print("\n[3/5] Bundling Playwright Chromium browser for offline execution...")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    playwright_dir = Path(local_appdata) / "ms-playwright" if local_appdata else None
    if playwright_dir and playwright_dir.exists():
        target_pw = DIST_DIR / "ms-playwright"
        target_pw.mkdir(parents=True, exist_ok=True)
        for item in playwright_dir.iterdir():
            if item.name.startswith("chromium"):
                dest = target_pw / item.name
                if not dest.exists():
                    print(f"      Copying {item.name} to dist bundle...")
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)
        print("      Playwright Chromium bundled [OK]")
    else:
        print("      Notice: ms-playwright not found in LocalAppData, skipping pre-bundled browser.")

    # 4. Copy standalone Ultron.exe to Release directory
    print("\n[4/5] Preparing Release assets...")
    shutil.copy(DIST_DIR / "Ultron.exe", RELEASE_DIR / "Ultron.exe")
    readme_path = RELEASE_DIR / "README.txt"
    readme_path.write_text(
        "===================================================\n"
        " ULTRON AI Desktop Assistant -- Release Package\n"
        "===================================================\n\n"
        "INSTALLATION INSTRUCTIONS:\n\n"
        "1. Double-click ULTRON_Setup.exe\n"
        "2. Follow the setup wizard prompt.\n"
        "3. ULTRON will create Desktop and Start Menu shortcuts\n"
        "   and launch automatically.\n\n"
        "Zero dependencies required -- Python, PyQt6, audio drivers,\n"
        "and browser engines are 100% self-contained.\n",
        encoding="utf-8"
    )
    print("      Release files prepared [OK]")

    # 5. Compile Inno Setup Installer
    print("\n[5/5] Compiling Inno Setup installer (ULTRON_Setup.exe)...")
    iscc = find_iscc()
    if not iscc:
        print("WARNING: Inno Setup compiler (ISCC.exe) not found.")
        print("    Standalone PyInstaller files are ready in Release/ directory.")
        print("    Install Inno Setup 6 to compile Release/ULTRON_Setup.exe.")
        return

    iss_file = BASE_DIR / "installer.iss"
    res_iss = subprocess.run([iscc, str(iss_file)])
    if res_iss.returncode != 0:
        print("\nERROR: Inno Setup compilation failed!")
        sys.exit(res_iss.returncode)

    setup_exe = RELEASE_DIR / "ULTRON_Setup.exe"
    if setup_exe.exists():
        size_mb = setup_exe.stat().st_size / (1024 * 1024)
        print("\n" + "=" * 60)
        print(f" SUCCESS! Single-File Installer Created:")
        print(f" Path: {setup_exe.resolve()}")
        print(f" Size: {size_mb:.2f} MB")
        print("=" * 60)
    else:
        print("\nERROR: ULTRON_Setup.exe was not produced!")

if __name__ == "__main__":
    main()
