# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Do not ship these in the AppImage. Bundled copies are often older than the
# host libstdc++ that system audio libs (e.g. JACK via PortAudio) need, which
# breaks launch with missing GLIBCXX_* symbols. Use the host C++ runtime instead.
_EXCLUDED_SHARED_LIBS = {
    'libstdc++.so.6',
    'libgcc_s.so.1',
}


def _keep_binary(entry):
    name = entry[0].split('/')[-1]
    return name not in _EXCLUDED_SHARED_LIBS


added_files = [
    ('resources/favicon.png', 'resources/'),
]
added_files += collect_data_files('ahrs')

a = Analysis(
    ['restim.py'],
    pathex=['.'],
    binaries=[],
    datas=added_files,
    hiddenimports=[],
    hookspath=['pyinstaller-hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
a.binaries = [entry for entry in a.binaries if _keep_binary(entry)]

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='restim',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/favicon.png',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='restim',
)
