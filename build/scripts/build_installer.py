"""
Build script for Dash
Builds executable and creates installer
"""

import shutil
import subprocess
from pathlib import Path
import sys
import re


def clean_build_folders():
    """Remove previous build artifacts"""
    print("Cleaning previous builds...")
    folders_to_clean = ["build/temp", "dist"]

    for folder in folders_to_clean:
        folder_path = Path(folder)
        if folder_path.exists():
            shutil.rmtree(folder_path)
            print(f"   Removed {folder}/")
    print()


def build_executable():
    """Build the executable with PyInstaller"""
    print("Building executable with PyInstaller...")
    try:
        # Use sys.executable so the build always uses the interpreter that has PyQt6 installed
        subprocess.run([sys.executable, "-m", "PyInstaller", "dash.spec", "--clean", "--workpath=build/temp"], check=True)
        print("Executable built successfully!\n")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to build executable: {e}\n")
        return False
    except FileNotFoundError:
        print("PyInstaller not found. Install it with: pip install pyinstaller\n")
        return False


def update_version_files(version):
    """Update version in all build files"""
    print(f"Setting version to {version}...")

    # Update dash.py
    dash_path = Path("dash.py")
    content = dash_path.read_text()
    content = re.sub(r'__version__\s*=\s*".*"', f'__version__ = "{version}"', content)
    dash_path.write_text(content)

    # Parse version
    version_parts = version.split(".")
    while len(version_parts) < 4:
        version_parts.append("0")
    version_tuple = ", ".join(version_parts[:4])
    version_str = ".".join(version_parts[:4])

    # Generate version_info.txt
    version_info = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({version_tuple}),
    prodvers=({version_tuple}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'Calem Young'),
        StringStruct(u'FileDescription', u'Dash - Quick Program Launcher'),
        StringStruct(u'FileVersion', u'{version_str}'),
        StringStruct(u'InternalName', u'Dash'),
        StringStruct(u'LegalCopyright', u'Copyright (C) 2025 Calem Young'),
        StringStruct(u'OriginalFilename', u'Dash.exe'),
        StringStruct(u'ProductName', u'Dash'),
        StringStruct(u'ProductVersion', u'{version_str}')])
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""
    Path("build/installer/version_info.txt").write_text(version_info)

    # Update installer.iss
    iss_path = Path("build/installer/installer.iss")
    content = iss_path.read_text()
    content = re.sub(r'#define MyAppVersion ".*"', f'#define MyAppVersion "{version}"', content)
    iss_path.write_text(content)
    print("Version files updated\n")


def get_version():
    """Read version from version.txt"""
    return Path("build/installer/version.txt").read_text().strip()


def build_installer():
    print("Creating installer with Inno Setup...")

    # Common Inno Setup installation paths
    inno_paths = [
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
        r"C:\Program Files\Inno Setup 5\ISCC.exe",
    ]

    inno_setup_path = None
    for path in inno_paths:
        if Path(path).exists():
            inno_setup_path = path
            break

    if not inno_setup_path:
        print("Inno Setup not found!")
        print("   Download from: https://jrsoftware.org/isinfo.php")
        print("   Or skip installer and use the exe from build/dist/Dash/\n")
        return False

    try:
        subprocess.run([inno_setup_path, "build/installer/installer.iss"], check=True)
        print("Installer created successfully!\n")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to create installer: {e}\n")
        return False


def main():
    print("=" * 60)
    print("Dash Build Script")
    print("=" * 60)
    print()

    version = get_version()  # Get version first

    # Step 1: Update version files
    update_version_files(version)

    # Step 2: Clean
    clean_build_folders()

    # Step 3: Build executable
    if not build_executable():
        print("Build failed at executable stage.")
        sys.exit(1)

    # Step 4: Build installer
    installer_success = build_installer()

    # Summary
    print("=" * 60)
    print("Build Summary")
    print("=" * 60)

    exe_path = Path("dist/Dash/Dash.exe")
    if exe_path.exists():
        print(f"Executable: {exe_path.absolute()}")

    installer_path = Path(f"dist/DashSetup-{version}.exe")  # Use version here
    if installer_success and installer_path.exists():
        print(f"Installer:  {installer_path.absolute()}")
        print()
        print(f"Ready to ship: DashSetup-{version}.exe")  # And here
    else:
        print()
        print("You can distribute the folder: dist/Dash/")
        print("   (Contains portable version)")

    print()
    print("Build complete!")


if __name__ == "__main__":
    main()


"""
To release a new version:

1. Update version in: build/installer/version.txt

2. Run: python build/scripts/build_installer.py

3. Distribute: dist/DashSetup-x.x.x.exe
"""
