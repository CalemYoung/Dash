"""
Build script for Dash
Builds executable and creates installer

The version comes from build/installer/version.txt and nowhere else. This
script generates the PyInstaller version resource from it and passes it to
Inno Setup on the command line, so no committed file has to be rewritten.
"""

import shutil
import subprocess
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VERSION_FILE = PROJECT_ROOT / "build" / "installer" / "version.txt"
VERSION_INFO_FILE = PROJECT_ROOT / "build" / "installer" / "version_info.txt"
INSTALLER_SCRIPT = PROJECT_ROOT / "build" / "installer" / "installer.iss"
DIST_DIR = PROJECT_ROOT / "dist"


def clean_build_folders():
    """Remove previous build artifacts"""
    print("Cleaning previous builds...")
    for folder in ("build/temp", "dist"):
        folder_path = PROJECT_ROOT / folder
        if folder_path.exists():
            shutil.rmtree(folder_path)
            print(f"   Removed {folder}/")
    print()


def build_executable():
    """Build the executable with PyInstaller"""
    print("Building executable with PyInstaller...")
    try:
        # Use sys.executable so the build always uses the interpreter that has PyQt6 installed
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "dash.spec", "--clean", "--workpath=build/temp"],
            check=True,
            cwd=PROJECT_ROOT,
        )
        print("Executable built successfully!\n")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to build executable: {e}\n")
        return False
    except FileNotFoundError:
        print("PyInstaller not found. Install it with: pip install pyinstaller\n")
        return False


def write_version_info(version):
    """Generate the Windows version resource PyInstaller embeds in Dash.exe.

    The output is ignored by git; it is derived entirely from version.txt.
    """
    print(f"Generating version resource for {version}...")

    version_parts = version.split(".")
    while len(version_parts) < 4:
        version_parts.append("0")
    version_tuple = ", ".join(version_parts[:4])
    version_str = ".".join(version_parts[:4])

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
    VERSION_INFO_FILE.write_text(version_info, encoding="utf-8")
    print("Version resource written\n")


def get_version():
    """Read version from version.txt"""
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def find_inno_setup():
    """Locate ISCC.exe from the common Inno Setup install locations."""
    candidates = [
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
        r"C:\Program Files\Inno Setup 5\ISCC.exe",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return shutil.which("ISCC")


def build_installer(version):
    print("Creating installer with Inno Setup...")

    inno_setup_path = find_inno_setup()
    if not inno_setup_path:
        print("Inno Setup not found!")
        print("   Download from: https://jrsoftware.org/isinfo.php")
        print("   Or skip installer and use the exe from dist/dash/\n")
        return False

    try:
        subprocess.run(
            [inno_setup_path, f"/DMyAppVersion={version}", str(INSTALLER_SCRIPT)],
            check=True,
            cwd=PROJECT_ROOT,
        )
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

    version = get_version()

    # Step 1: Derive the exe version resource
    write_version_info(version)

    # Step 2: Clean
    clean_build_folders()

    # Step 3: Build executable
    if not build_executable():
        print("Build failed at executable stage.")
        sys.exit(1)

    # Step 4: Build installer
    installer_success = build_installer(version)

    # Summary
    print("=" * 60)
    print("Build Summary")
    print("=" * 60)

    exe_path = DIST_DIR / "dash" / "Dash.exe"
    if exe_path.exists():
        print(f"Executable: {exe_path}")

    installer_path = DIST_DIR / f"DashSetup-{version}.exe"
    if installer_success and installer_path.exists():
        print(f"Installer:  {installer_path}")
        print()
        print(f"Ready to ship: DashSetup-{version}.exe")
    else:
        print()
        print("You can distribute the folder: dist/dash/")
        print("   (Contains portable version)")

    print()
    print("Build complete!")


if __name__ == "__main__":
    main()


"""
To release a new version:

1. Update version in: build/installer/version.txt

2. Commit, then tag it `v<version>` and push the tag. GitHub Actions builds
   the installer and attaches it to the release.

   For a local build instead: python build/scripts/build_installer.py
   and distribute dist/DashSetup-<version>.exe
"""
