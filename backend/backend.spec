# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Analecta sidecar (FastAPI + uvicorn, onedir mode).
# Build: cd backend && mise exec -- uv run pyinstaller backend.spec --distpath ../src-tauri/binaries

from PyInstaller.utils.hooks import collect_data_files  # noqa: E402

a = Analysis(
    ['src/analecta/__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('src/analecta/migrations/*.sql', 'analecta/migrations'),
        # trafilatura reads settings.cfg at import time via Path(__file__).parent;
        # without this, configparser raises NoOptionError on min_extracted_size.
        *collect_data_files('trafilatura'),
        *collect_data_files('justext'),
    ],
    hiddenimports=[
        # Dynamic loaders not reachable by static analysis
        'trafilatura',
        'trafilatura.settings',
        'readability',
        'markdownify',
        'markdown_it',
        'youtube_transcript_api',
        'httpx',
        'sse_starlette',
        # uvicorn lazy-loads its protocol/loop implementations at startup
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Never needed in the sidecar
        'tkinter',
        'matplotlib',
        'IPython',
        # Defensive: __main__.py contains legacy PySide6/qasync code paths
        # that are unreachable at runtime but visible to static analysis.
        'PySide6',
        'qasync',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='analecta-sidecar',
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='analecta-sidecar',
)
