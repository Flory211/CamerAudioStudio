# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

studio_data = [('app/web', 'app/web')]
for filename in ('selfie_segmenter_landscape.tflite', 'face_landmarker.task', 'sources.json', 'README.md'):
    if Path('models', filename).is_file():
        studio_data.append((str(Path('models', filename)), 'models'))
if Path('models/vosk-model-small-ru-0.22').is_dir():
    studio_data.append(('models/vosk-model-small-ru-0.22', 'models/vosk-model-small-ru-0.22'))
for package in ('mediapipe', 'vosk', 'soundcard'):
    studio_data += collect_data_files(package, excludes=['**/tests/**', '**/*test*'])
studio_binaries = []
for package in ('mediapipe', 'vosk', 'av', 'pylibsrtp'):
    studio_binaries += collect_dynamic_libs(package)
studio_imports = collect_submodules('av', filter=lambda name: 'test' not in name)
studio_imports += ['aiortc', 'aiohttp', 'vosk', 'soundcard.mediafoundation', 'pycaw.pycaw',
    'mediapipe.tasks.python.vision.image_segmenter', 'mediapipe.tasks.python.vision.face_landmarker',
    'mediapipe.tasks.python.core.base_options', 'PIL.ImageFont', 'psutil', 'av.audio.resampler']

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=studio_binaries,
    datas=[
        ('masks/trollface.png', 'masks'),
        ('haarcascade_frontalface_default.xml', '.'),
    ] + studio_data,
    hiddenimports=studio_imports + [
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
    excludes=['IPython', 'pytest', 'notebook', 'tkinter', 'matplotlib.tests', 'scipy.tests', 'numpy.tests'],
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
    name='CamerAudioStudio',
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
    name='CamerAudioStudio',
)
