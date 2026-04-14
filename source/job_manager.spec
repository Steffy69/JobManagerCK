# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['job_manager.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('job_manager.ui', '.'),
        ('icon.ico', '.')
    ],
    hiddenimports=[
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'job_types',
        'job_scanner',
        'file_transfer',
        'label_printer',
        'usb_transfer',
        'transfer_history',
        'status_service',
        'drop_zone',
        'updater',
        # v2.1: pywin32 modules for printer control and ShellExecute
        'win32print',
        'win32api',
        'win32con',
        'pywintypes',
        # v2.1: new pure-Python modules — listed so PyInstaller bundles them
        # even if static analysis misses any indirect import path.
        'settings',
        'preflight',
        'printer_service',
        'print_sequencer',
        'zpl_templates',
        'printer_status_widget',
        'settings_dialog',
        'print_order_dialog',
    ],
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='JobManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)
