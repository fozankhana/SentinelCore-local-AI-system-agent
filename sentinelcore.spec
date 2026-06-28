# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for SentinelCore v1.0.0
# Run:  pyinstaller --clean sentinelcore.spec
# Output: dist/SentinelCore/   (folder distribution)

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['sentinelcore.py'],
    pathex=[str(Path.cwd())],
    binaries=[],
    datas=[
        ('dashboard/templates', 'dashboard/templates'),
        ('dashboard/static',    'dashboard/static'),
        ('config.example.toml', '.'),
    ],
    hiddenimports=[
        # GPU
        'pynvml',
        # psutil platform shims
        'psutil._pswindows',
        'psutil._pslinux',
        'psutil._psposix',
        'psutil._psmacosx',
        # Flask internals
        'flask',
        'flask.templating',
        'flask.json',
        'jinja2',
        'jinja2.ext',
        'werkzeug',
        'werkzeug.serving',
        # HTTP
        'requests',
        'requests.adapters',
        'urllib3',
        # TOML
        'tomllib',
        'tomli',
        # SentinelCore modules
        'core.config',
        'core.store',
        'core.collector',
        'core.enforcer',
        'core.job_objects',
        'core.alerts',
        'core.ai_agent',
        'core.background',
        'core.browser_monitor',
        'core.gpu_router',
        'dashboard.server',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'email', 'html.parser',
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'IPython', 'jupyter',
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
    name='SentinelCore',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='installer/icon.ico',   # uncomment after adding icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SentinelCore',
)
