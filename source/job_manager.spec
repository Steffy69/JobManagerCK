# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for JobManagerCK.

Onefile build (the auto-updater swaps a single JobManager.exe), with the
payload trimmed hard: the stock PyQt5 hook drags in ~46 MB (uncompressed)
of OpenGL/QML/Network DLLs, translations and plugins that a plain-widgets
app never touches. Every launch self-extracts the whole archive to %TEMP%
(and Defender rescans it), so payload size is launch time.

upx is OFF: UPX-packed DLLs barely shrink the final zlib-compressed
archive, they slow extraction, and packed unsigned exes are a known
Defender false-positive magnet.
"""

import os

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
        'job_scan_worker',
        'file_transfer',
        'label_printer',
        'usb_transfer',
        'move_job',
        'transfer_common',
        'transfer_history',
        'drop_zone',
        'updater',
        'app_logging',
        # pywin32 modules for printer control and ShellExecute
        'win32print',
        'win32api',
        'win32con',
        'pywintypes',
        # pure-Python modules — listed so PyInstaller bundles them even if
        # static analysis misses any indirect import path.
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
    excludes=[
        # Pulled in by PyInstaller's own runtime hooks / stdlib graph, used
        # by nothing in this app.
        'setuptools',
        'pkg_resources',
        '_distutils_hack',
        'pydoc',
        'pydoc_data',
        'doctest',
        'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ---------------------------------------------------------------------------
# Payload trim. The app uses QtCore/QtGui/QtWidgets and renders an .ico —
# nothing below is reachable. Names are matched case-insensitively against
# each TOC entry's destination path.
# ---------------------------------------------------------------------------

_DROP_FRAGMENTS = (
    # Software OpenGL + ANGLE stack (~28 MB): only needed for Qt Quick or
    # machines forced into software rendering of GL content.
    'opengl32sw.dll',
    'd3dcompiler_47.dll',
    'libegl.dll',
    'libglesv2.dll',
    # QML/Quick/Network engines a widgets-only app never loads.
    'qt5quick',
    'qt5qml',
    'qt5websockets',
    'qt5network',
    'qt5dbus',
    'qt5svg',
    # Plugins for platforms/features not in use.
    'plugins\\platforms\\qwebgl',
    'plugins\\platforms\\qminimal',
    'plugins\\platforms\\qoffscreen',
    'plugins\\platformthemes\\',
    'plugins\\generic\\',
    'plugins\\iconengines\\',
    # Image formats: keep qico (window icon); drop the rest.
    'plugins\\imageformats\\qgif',
    'plugins\\imageformats\\qicns',
    'plugins\\imageformats\\qjpeg',
    'plugins\\imageformats\\qsvg',
    'plugins\\imageformats\\qtga',
    'plugins\\imageformats\\qtiff',
    'plugins\\imageformats\\qwbmp',
    'plugins\\imageformats\\qwebp',
    # 8+ MB of UI translations for an English-only app.
    'qt5\\translations\\',
)


def _keep(entry):
    dest = entry[0].lower().replace('/', '\\')
    return not any(fragment in dest for fragment in _DROP_FRAGMENTS)


a.binaries = TOC([entry for entry in a.binaries if _keep(entry)])
a.datas = TOC([entry for entry in a.datas if _keep(entry)])

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
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)
