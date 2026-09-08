# -*- mode: python ; coding: utf-8 -*-
# Dash - PyInstaller Spec File
# Calem Young

block_cipher = None

a = Analysis(
    ['dash.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('style.qss', '.'),
        ('assets/icons', 'assets/icons'),
        ('config/settings.default.toml', 'config'),
        ('config/commands.default.toml', 'config'),
        ('build/installer/version.txt', 'build/installer'),
    ],
    hiddenimports=['win32com.client'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'tkinter',
        'PyQt5',
    ],
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
    name='Dash',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icons/icon.ico',
    version='build/installer/version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='dash',
)