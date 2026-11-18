"""Desktop entry point and a reproducible device-free health check."""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import sys

from app import APP_DISPLAY_NAME


def main(argv=None):
    parser = argparse.ArgumentParser(description=APP_DISPLAY_NAME)
    parser.add_argument('--project', help='Open a local .cas.json project')
    parser.add_argument('--smoke-test', action='store_true', help='Check without camera/microphone')
    parser.add_argument('--render-ui', metavar='PNG', help='Render the real UI')
    parser.add_argument('--report', metavar='JSON', help='Save health-check results')
    parser.add_argument('--check-models', action='store_true', help='Also run the bundled local models during smoke-test')
    parser.add_argument('--check-media', action='store_true', help='Also check MP4 encoding and extension subprocesses')
    parser.add_argument('--allow-model-metrics', action='store_true', help='Explicitly allow MediaPipe technical metrics for the requested model check')
    options = parser.parse_args(argv)
    if options.smoke_test or options.render_ui:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt6.QtCore import QTimer, Qt
    from PyQt6.QtGui import QFont, QFontDatabase
    from PyQt6.QtWidgets import QApplication
    from app.main_window import MainWindow
    app = QApplication(sys.argv[:1])
    app.setApplicationName('CamerAudioStudio')
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setApplicationVersion('2.0.0-alpha')
    for filename in ('segoeui.ttf', 'seguisb.ttf'):
        font = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts' / filename
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont('Segoe UI', 10))
    window = MainWindow()
    if options.project and not window.open_project(options.project):
        return 1
    window.show()
    if options.smoke_test or options.render_ui:
        def finish():
            code = 0
            try:
                assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint
                assert window.engine.latest_frame is not None
                assert window.scene_list.count() >= 1
                assert not window.audio_engine.running
                assert not (window.server and window.server.running)
                if options.render_ui:
                    image_path = Path(options.render_ui).resolve()
                    image_path.parent.mkdir(parents=True, exist_ok=True)
                    assert window.grab().save(str(image_path))
                report = dict(ok=True, version=app.applicationVersion(),
                    scenes=window.scene_list.count(), native_window=True,
                    frame_shape=list(window.engine.latest_frame.shape),
                    audio_started=window.audio_engine.running, network_started=bool(window.server and window.server.running),
                    font_families=len(QFontDatabase.families()))
                if options.check_models:
                    if not options.allow_model_metrics:
                        raise RuntimeError('MediaPipe requires explicit SDK metrics consent; use --allow-model-metrics only if you agree.')
                    import numpy as np
                    from app.resources import default_model_paths
                    from app.studio.vision import VisionProcessor
                    from app.audio.captions import CaptionService
                    paths = default_model_paths()
                    vision = VisionProcessor()
                    try:
                        vision.configure(dict(segmentation_model=paths.get('segmentation_model', ''),
                            face_model=paths.get('face_model', ''), background='color', auto_frame=True,
                            allow_mediapipe_metrics=True))
                        vision.apply(np.zeros((144, 256, 3), np.uint8))
                        if vision._segmenter is None or vision._landmarker is None:
                            raise RuntimeError(vision.status)
                    finally:
                        vision.close()
                    captions = CaptionService(paths.get('model_path', ''))
                    captions.feed(np.zeros((4410, 2), np.float32))
                    captions.close()
                    if captions.error:
                        raise RuntimeError(captions.error)
                    report['models_checked'] = ['segmentation', 'face', 'Russian captions']
                if options.check_media:
                    import tempfile
                    import time
                    import numpy as np
                    import av
                    from app.studio.recording import SessionRecorder
                    from app.studio.extensions import ExtensionHost
                    with tempfile.TemporaryDirectory(prefix='CamerAudioStudio-check-') as directory:
                        recorder = SessionRecorder(directory, 64, 48, 10)
                        session = recorder.start()
                        recorder.write_audio('Микс', np.zeros((4410, 2), np.float32))
                        recorder.write_frame(np.zeros((48, 64, 3), np.uint8))
                        recorder.write_frame(np.full((48, 64, 3), 100, np.uint8), time.monotonic() + .1)
                        recorder.stop()
                        manifest = json.loads((session / 'manifest.json').read_text(encoding='utf-8'))
                        if not manifest.get('video_has_audio'):
                            raise RuntimeError(manifest.get('mux_error') or 'MP4 audio stream was not created')
                        with av.open(str(session / manifest['video'])) as container:
                            if not container.streams.audio or not container.streams.video:
                                raise RuntimeError('Missing MP4 audio/video streams')
                        plugin = Path(directory) / 'check_effect.py'
                        plugin.write_text('def process_frame(frame):\n    return 255 - frame\n', encoding='utf-8')
                        host = ExtensionHost(plugin, timeout=10)
                        try:
                            if host.error or not np.all(host.apply(np.zeros((8, 8, 3), np.uint8)) == 255):
                                raise RuntimeError(host.error or 'Extension worker did not process a frame')
                        finally:
                            host.close()
                    report['media_checked'] = ['MP4 audio/video', 'isolated extension']
            except Exception as error:
                code = 1
                report = dict(ok=False, error=str(error))
            if options.report:
                path = Path(options.report).resolve()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            if sys.stdout is not None:
                print(json.dumps(report, ensure_ascii=False), flush=True)
            window.dirty = False
            window.close()
            app.exit(code)
        window.engine.start()
        QTimer.singleShot(1800, finish)
    return app.exec()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
