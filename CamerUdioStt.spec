# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # App modules
        'app',
        'app.main_window',
        'app.video',
        'app.video.capture',
        'app.video.effects',
        'app.video.processor',
        'app.video.output',
        'app.audio',
        'app.audio.capture',
        'app.audio.effects',
        'app.audio.output',
        'config',
        # cv2
        'cv2',
        'cv2.cv2',
        # numpy
        'numpy',
        'numpy.fft',
        'numpy.linalg',
        'numpy.core',
        'numpy.core._multiarray_umath',
        # scipy - signal
        'scipy',
        'scipy.signal',
        'scipy.signal._signaltools',
        'scipy.signal._spectral_py',
        'scipy.signal._filter_design',
        'scipy.signal._fir_filter_design',
        'scipy.signal._lti_conversion',
        'scipy.signal._savitzky_golay',
        'scipy.signal._upfirdn',
        'scipy.signal._waveforms',
        'scipy.signal._wavelets',
        'scipy.signal._czt',
        'scipy.signal._max_len_seq',
        'scipy.signal._spline_filters',
        'scipy.signal._peak_finding',
        'scipy.signal._arraytools',
        'scipy.signal._support_alternative_backends',
        # scipy._external - array_api_compat (fixes ModuleNotFoundError)
        'scipy._external',
        'scipy._external.array_api_compat',
        'scipy._external.array_api_compat.numpy',
        'scipy._external.array_api_compat.numpy.fft',
        'scipy._external.array_api_compat.numpy.linalg',
        'scipy._external.array_api_compat.common',
        'scipy._external.array_api_compat.common._aliases',
        'scipy._external.array_api_compat.common._helpers',
        # scipy._lib
        'scipy._lib',
        'scipy._lib._array_api',
        'scipy._lib._util',
        'scipy._lib._ccallback',
        'scipy._lib._testutils',
        'scipy._lib.doccer',
        # sounddevice
        'sounddevice',
        'soundfile',
        'cffi',
        '_cffi_backend',
        # pyvirtualcam
        'pyvirtualcam',
        # mss
        'mss',
        # pygetwindow
        'pygetwindow',
        'pyrect',
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
    [],
    exclude_binaries=True,
    name='CamerUdioStt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Без консольного окна
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
    name='CamerUdioStt',
)
