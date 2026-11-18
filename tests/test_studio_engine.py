"""Synthetic engine tests: no devices, network or virtual drivers are opened."""
import copy
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import cv2
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

from app.studio.engine import RenderThread, StudioEngine
from app.studio.project import StudioProject


class SyntheticCapture:
    def __init__(self, source, fps, size, report):
        self.source = copy.deepcopy(source)
        self.fps, self.size = fps, size
        self.stopped = threading.Event()
        self.frame = np.full((24, 32, 3), source.get('device', 0), np.uint8)
        self.stop_calls = 0
        self.alive = True

    def start(self):
        pass

    def stop(self):
        self.stop_calls += 1
        self.stopped.set()
        self.alive = False

    def is_alive(self):
        return self.alive


class EngineTests(unittest.TestCase):
    def test_animation_clock_restarts_when_entering_another_scene(self):
        project = self.project()
        other = project.add_scene('Другая')
        engine = StudioEngine(project)
        calls = []
        def render(scene, frames, size=(1280, 720), time_seconds=0):
            calls.append((scene['id'], time_seconds))
            return np.zeros((size[1], size[0], 3), np.uint8)
        try:
            with patch('app.studio.engine.compose', side_effect=render):
                engine.start()
                deadline = time.monotonic() + 2
                while (not calls or calls[-1][1] < .15) and time.monotonic() < deadline:
                    time.sleep(.01)
                project.set_active(other['id'])
                engine.update_project(project)
                while not any(scene_id == other['id'] for scene_id, _ in calls) and time.monotonic() < deadline:
                    time.sleep(.01)
                first_time = next(stamp for scene_id, stamp in calls if scene_id == other['id'])
                self.assertLess(first_time, .05)
        finally:
            engine.stop()

    def test_image_effects_apply_to_static_preview_and_keep_bgra_alpha(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'картинка.png'
            image = np.full((24, 32, 4), (10, 20, 30, 128), np.uint8)
            path.write_bytes(cv2.imencode('.png', image)[1].tobytes())
            project = self.project()
            source = project.add_source('image', path=str(path), effects={'invert': 100})
            engine = StudioEngine(project)
            expected = np.rint(np.array([245, 235, 225]) * (128 / 255) + np.array([26, 19, 16]) * (127 / 255)).astype(np.uint8)
            np.testing.assert_array_equal(engine.latest_frame[0, 0], expected)
            render = RenderThread(engine)
            observed = []

            def compose_scene(scene, frames, *args):
                observed.append(frames[source['id']].copy())
                return np.zeros((24, 32, 3), np.uint8)

            with patch('app.studio.engine.compose', side_effect=compose_scene), patch.object(render, 'isInterruptionRequested', side_effect=[False, True]), patch.object(render, 'msleep'):
                render.run()
            np.testing.assert_array_equal(observed[0][0, 0], [245, 235, 225, 128])

    def test_explicit_sync_replaces_dead_capture_once_for_shared_sources(self):
        project = self.project()
        first = project.add_source('camera')
        second = project.add_source('camera')
        engine = StudioEngine(project)
        with patch('app.studio.engine.CaptureWorker', SyntheticCapture):
            engine.running = True
            engine.sync_sources()
            old = engine.captures[first['id']]
            old.alive = False
            engine.sync_sources()
            current = engine.captures[first['id']]
            self.assertIsNot(current, old)
            self.assertIs(current, engine.captures[second['id']])
            self.assertEqual(old.stop_calls, 1)
            engine.close()

    def test_adaptive_quality_scales_copied_explicit_fonts(self):
        project = self.project()
        source = project.add_source('text', text='Text', font_size=44)
        engine = StudioEngine(project)
        engine.quality = .5
        engine.caption_text = 'Caption'
        render = RenderThread(engine)
        observed = []

        def compose_scene(scene, frames, *args):
            observed.append({s['id']: s.get('font_size') for s in scene['sources']})
            return np.zeros((16, 16, 3), np.uint8)

        with patch('app.studio.engine.compose', side_effect=compose_scene), patch.object(render, 'isInterruptionRequested', side_effect=[False, True]), patch.object(render, 'msleep'):
            render.run()
        self.assertEqual(observed[0][source['id']], 22)
        self.assertEqual(observed[0]['live-captions'], 17)
        self.assertEqual(source['font_size'], 44)

    def test_explicit_missing_guest_does_not_show_another_guest(self):
        for device, expected_frames in [('absent-guest', set()), ('', {'guest-source'})]:
            project = self.project()
            source = project.add_source('guest', id='guest-source', device=device)
            engine = StudioEngine(project)
            engine.server = Mock(running=True, guest_frames=lambda: {'other-guest': np.ones((24, 32, 3), np.uint8)})
            render = RenderThread(engine)
            observed = []

            def compose_scene(scene, frames, *args):
                observed.append(set(frames))
                return np.zeros((24, 32, 3), np.uint8)

            with patch('app.studio.engine.compose', side_effect=compose_scene), patch('app.studio.engine.VideoEffects', return_value=Mock(apply=lambda frame: frame)), patch('app.studio.engine.VisionProcessor', return_value=Mock(apply=lambda frame: frame)), patch.object(render, 'isInterruptionRequested', side_effect=[False, True]), patch.object(render, 'msleep'):
                render.run()
            self.assertEqual(observed, [expected_frames], f'device={source["device"]}')

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def project(self):
        project = StudioProject()
        project.data.update(width=32, height=24, fps=20)
        project.active_scene['sources'] = []
        return project

    def test_shared_capture_is_stopped_only_after_last_reference(self):
        project = self.project()
        first = project.add_source('camera', device=0)
        second = project.add_source('camera', device=0)
        engine = StudioEngine(project)
        with patch('app.studio.engine.CaptureWorker', SyntheticCapture):
            engine.running = True
            engine.sync_sources()
            shared = engine.captures[first['id']]
            self.assertIs(shared, engine.captures[second['id']])
            project.remove_source(first['id'])
            engine.update_project(project)
            self.assertEqual(shared.stop_calls, 0)
            project.remove_source(second['id'])
            engine.update_project(project)
            self.assertEqual(shared.stop_calls, 1)
            engine.close()

    def test_each_source_has_independent_vision_and_mask_selection_is_cached(self):
        project = self.project()
        a = project.add_source('camera', device=1, mask='test')
        b = project.add_source('camera', device=2, mask='test')
        engine = StudioEngine(project)
        engine.captures = {s['id']: SyntheticCapture(s, 20, (32, 24), print) for s in (a, b)}
        instances, effects = [], []

        def new_vision():
            processor = Mock(status='test', apply=lambda frame: frame)
            instances.append(processor)
            return processor

        def new_effect():
            effect = Mock(apply=lambda frame: frame)
            effects.append(effect)
            return effect

        render = RenderThread(engine)
        with patch('app.studio.engine.VisionProcessor', side_effect=new_vision), patch('app.studio.engine.VideoEffects', side_effect=new_effect), patch.object(render, 'isInterruptionRequested', side_effect=[False, False, True]), patch.object(render, 'msleep'):
            render.run()
        self.assertEqual(len(instances), 2)
        for processor in instances:
            processor.close.assert_called_once()
        self.assertEqual(len(effects), 2)
        for effect in effects:
            self.assertEqual(effect.face_mask.set_mask.call_count, 1)
        self.assertEqual(engine.latest_frame.shape, (24, 32, 3))

    def test_render_failure_closes_recording_virtual_output_and_captures(self):
        project = self.project()
        source = project.add_source('camera')
        engine = StudioEngine(project)
        capture = SyntheticCapture(source, 20, (32, 24), print)
        engine.captures = {source['id']: capture}
        recorder = Mock(recording=True)
        output = Mock()
        engine.recorder, engine.virtual_output = recorder, output
        render = RenderThread(engine)
        with patch('app.studio.engine.compose', side_effect=RuntimeError('synthetic')), patch('app.studio.engine.VideoEffects', return_value=Mock(apply=lambda frame: frame)), patch.object(render, 'isInterruptionRequested', return_value=False):
            render.run()
        self.assertFalse(engine.running)
        recorder.stop.assert_called_once()
        output.stop.assert_called_once()
        self.assertEqual(capture.stop_calls, 1)
        self.assertIsNone(engine.recorder)
        self.assertIsNone(engine.virtual_output)

    def test_real_render_thread_starts_and_stops_without_devices(self):
        project = self.project()
        project.add_source('color', color='#123456')
        engine = StudioEngine(project)
        engine.start()
        deadline = time.monotonic() + 2
        while engine.replay.bytes_used == 0 and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertGreater(engine.replay.bytes_used, 0)
        self.assertTrue(engine.stop())
        self.assertFalse(engine.running)
        self.assertIsNone(engine.worker)
        self.assertFalse(engine.captures)

    def test_virtual_camera_stop_during_driver_startup_is_honoured(self):
        class SlowVirtual(QThread):
            started = pyqtSignal()
            error_occurred = pyqtSignal(str)

            def __init__(self, *args):
                super().__init__()
                self.ready = threading.Event()
                self._running = False

            def send_frame(self, frame):
                pass

            def run(self):
                self.ready.wait(2)
                self._running = True
                self.started.emit()
                while self._running:
                    self.msleep(5)

            def stop(self):
                self._running = False
                self.wait(30)

        engine = StudioEngine(self.project())
        with patch('app.studio.engine.VirtualCameraOutput', SlowVirtual):
            engine.start_virtual_camera()
            output = engine.virtual_output
            try:
                engine.stop_virtual_camera()
                output.ready.set()
                self.assertTrue(output.wait(1000))
                self.assertIsNone(engine.virtual_output)
            finally:
                output.ready.set()
                output._running = False
                output.wait(1000)
                engine.close()


if __name__ == '__main__':
    unittest.main()
