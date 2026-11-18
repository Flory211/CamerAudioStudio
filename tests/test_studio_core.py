import json
import copy
import tempfile
import time
import unittest
import wave
import zipfile
from pathlib import Path

import numpy as np


class ProjectTests(unittest.TestCase):
    def test_malformed_nested_settings_are_rejected_without_changing_existing_project(self):
        from app.studio.project import StudioProject
        cases = [
            ('soundboard', [1]), ('soundboard', [{'id': 'sound', 'path': 'x.wav', 'gain': 'bad'}]),
            ('soundboard', [{'id': 'sound', 'path': [], 'name': 'x'}]),
            ('audio', {'channels': []}), ('audio', {'channels': {'mic': 1}}),
            ('audio', {'channels': {'mic': {'gain': 'bad'}}}),
            ('audio', {'effects': []}), ('audio', {'effects': {'pitch': 'bad'}}),
            ('audio', {'effects': {'chain': [{}]}}), ('audio', {'input_device': []}),
            ('audio', {'system_audio': 'true'}), ('audio', {'effects': {'telephone': 1}}),
            ('vision', {'mask_scale': 'bad'}), ('vision', {'background': 'unknown'}),
            ('vision', {'face_model': []}), ('vision', {'auto_frame': 'yes'}),
            ('captions', {'model_path': 1}), ('captions', {'sample_rate': -1}),
            ('network', {'port': 'bad'}), ('network', {'port': 65536}),
            ('network', {'lan': 1}), ('network', {'tls': []}),
            ('rules', [1]), ('rules', [{'event': 'unknown', 'action': {'type': 'record', 'value': True}}]),
            ('rules', [{'event': 'timer', 'interval': 0, 'action': {'type': 'record', 'value': True}}]),
            ('rules', [{'event': 'record_start', 'action': {'type': 'mute', 'target': 1, 'value': True}}]),
            ('rules', [{'event': 'record_start', 'action': {'type': 'record', 'value': 'true'}}]),
            ('extensions', [1]),
        ]
        project = StudioProject()
        original = copy.deepcopy(project.data)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.json'
            for key, value in cases:
                data = copy.deepcopy(original)
                data[key] = value
                path.write_text(json.dumps(data), encoding='utf-8')
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    StudioProject.load(path)
                self.assertEqual(project.data, original)

    def test_default_model_paths_are_passive_and_directory_models_are_portable(self):
        from unittest.mock import patch
        from app.studio.project import StudioProject
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = root / 'vosk-model'
            (model / 'conf').mkdir(parents=True)
            (model / 'conf' / 'model.conf').write_bytes(b'model configuration')
            with patch('app.resources.default_model_paths', return_value={'model_path': str(model), 'face_model': str(root / 'face.task')}):
                project = StudioProject()
            self.assertEqual(project.data['captions']['model_path'], str(model))
            self.assertEqual(project.data['vision']['face_model'], str(root / 'face.task'))
            self.assertNotIn('enabled', project.data['captions'])
            project.export_bundle(root / 'project.zip')
            imported = StudioProject.import_bundle(root / 'project.zip', root / 'restored')
            restored_model = Path(imported.data['captions']['model_path'])
            self.assertEqual((restored_model / 'conf' / 'model.conf').read_bytes(), b'model configuration')

    def test_scene_copy_has_independent_sources_and_fresh_ids(self):
        from app.studio.project import StudioProject
        project = StudioProject()
        self.assertEqual([s['name'] for s in project.scenes], ['Основная', 'Игра', 'Общение'])
        self.assertNotIn('camera', [s['kind'] for s in project.active_scene['sources']])
        first = project.active_scene
        copy = project.duplicate_scene(first['id'])
        copy['sources'][0]['color'] = '#ffffff'
        self.assertNotEqual(copy['id'], first['id'])
        self.assertNotEqual(copy['sources'][0]['id'], first['sources'][0]['id'])
        self.assertNotEqual(copy['sources'][0]['color'], first['sources'][0]['color'])
        project.set_active(copy['id'])
        source = project.add_source('camera', x=.2)
        self.assertIs(project.find_source(source['id']), source)
        project.remove_source(source['id'])
        self.assertIsNone(project.find_source(source['id']))
        project.remove_scene(copy['id'])
        self.assertIn(project.active_scene, project.scenes)

    def test_json_validation_and_portable_bundle(self):
        from app.studio.project import StudioProject
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            asset = root / 'изображение.bin'
            asset.write_bytes(b'asset')
            project = StudioProject()
            source = project.add_source('image', path=str(asset))
            project.save(root / 'project.json')
            restored = StudioProject.load(root / 'project.json')
            self.assertEqual(restored.find_source(source['id'])['path'], str(asset))
            project.export_bundle(root / 'project.zip')
            imported = StudioProject.import_bundle(root / 'project.zip', root / 'imported')
            imported_path = Path(imported.find_source(source['id'])['path'])
            self.assertEqual(imported_path.read_bytes(), b'asset')
            self.assertTrue(imported_path.is_relative_to((root / 'imported').resolve()))
            data = json.loads((root / 'project.json').read_text(encoding='utf-8'))
            data['width'] = -1
            (root / 'bad.json').write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):
                StudioProject.load(root / 'bad.json')

    def test_zip_rejects_unsafe_members_before_extraction(self):
        from app.studio.project import StudioProject
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for member in ('../escape', '/absolute', 'C:/drive', 'a\\..\\escape'):
                with zipfile.ZipFile(root / 'bad.zip', 'w') as archive:
                    archive.writestr(member, 'bad')
                with self.subTest(member=member), self.assertRaises(ValueError):
                    StudioProject.import_bundle(root / 'bad.zip', root / 'out')
            self.assertFalse((root / 'escape').exists())

    def test_zip_rejects_windows_device_names_and_case_collisions(self):
        from app.studio.project import StudioProject
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for index, members in enumerate((('CON.png',), ('assets/a.png', 'assets/A.png'), ('assets/file. ',))):
                with zipfile.ZipFile(root / 'bad.zip', 'w') as archive:
                    archive.writestr('project.json', json.dumps(StudioProject().data))
                    for member in members:
                        archive.writestr(member, b'data')
                with self.subTest(members=members), self.assertRaises(ValueError):
                    StudioProject.import_bundle(root / 'bad.zip', root / f'out{index}')

    def test_bundle_handles_missing_resources_and_rejects_symlinks(self):
        from app.studio.project import StudioProject
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = StudioProject()
            source = project.add_source('image', path=str(root / 'missing.png'))
            project.export_bundle(root / 'missing.zip')
            imported = StudioProject.import_bundle(root / 'missing.zip', root / 'restored')
            self.assertEqual(imported.find_source(source['id'])['path'], '')
            entry = zipfile.ZipInfo('link')
            entry.create_system = 3
            entry.external_attr = (0o120777 << 16)
            with zipfile.ZipFile(root / 'link.zip', 'w') as archive:
                archive.writestr(entry, '../secret')
            with self.assertRaises(ValueError):
                StudioProject.import_bundle(root / 'link.zip', root / 'unsafe')

    def test_rejects_duplicate_source_ids_and_nonfinite_values(self):
        from app.studio.project import StudioProject
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'project.json'
            project = StudioProject()
            project.active_scene['sources'][1]['id'] = project.active_scene['sources'][0]['id']
            with self.assertRaises(ValueError):
                project.save(path)
            project = StudioProject()
            project.active_scene['sources'][0]['x'] = float('nan')
            with self.assertRaises(ValueError):
                project.save(path)

    def test_bundle_preserves_vision_models_and_mask_assets(self):
        from app.studio.project import StudioProject
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            asset = root / 'model.onnx'
            asset.write_bytes(b'model')
            project = StudioProject()
            project.data['vision'] = {'segmentation_model': str(asset), 'face_model': str(asset)}
            source = project.add_source('camera', mask_path=str(asset), mask_mouth_path=str(asset), mask_blink_path=str(asset))
            project.export_bundle(root / 'project.zip')
            imported = StudioProject.import_bundle(root / 'project.zip', root / 'imported')
            values = [*imported.data['vision'].values(), *[imported.find_source(source['id'])[key] for key in ('mask_path', 'mask_mouth_path', 'mask_blink_path')]]
            for value in values:
                self.assertTrue(Path(value).is_relative_to((root / 'imported').resolve()))
                self.assertEqual(Path(value).read_bytes(), b'model')


class CompositorTests(unittest.TestCase):
    def test_order_alpha_clip_and_crop(self):
        from app.studio.compositor import compose
        bottom = {'id': 'a', 'kind': 'color', 'color': '#ff0000'}
        top = {'id': 'b', 'kind': 'camera', 'x': .5, 'y': 0, 'width': 1, 'height': 1}
        frame = np.full((4, 4, 4), (255, 0, 0, 128), np.uint8)
        result = compose({'sources': [bottom, top]}, {'b': frame}, (4, 4))
        np.testing.assert_array_equal(result[0, 0], [0, 0, 255])
        self.assertTrue(126 <= result[0, 3, 0] <= 129)
        self.assertEqual(result.dtype, np.uint8)
        self.assertEqual(result.shape, (4, 4, 3))

    def test_animation_interpolates_and_unicode_text_renders(self):
        from app.studio.compositor import animated_values, compose
        source = {'id': 'text', 'kind': 'text', 'text': 'Привет, Лев!',
                  'animation': [{'time': 0, 'x': 0}, {'time': 2, 'x': .5}]}
        self.assertAlmostEqual(animated_values(source, 1)['x'], .25)
        result = compose({'sources': [source]}, {}, (240, 60))
        self.assertGreater(len(np.unique(result.reshape(-1, 3), axis=0)), 2)

    def test_text_background_and_source_crop(self):
        from app.studio.compositor import compose
        source = {'id': 'caption', 'kind': 'text', 'text': '', 'background': '#ff0000'}
        result = compose({'sources': [source]}, {}, (8, 8))
        np.testing.assert_array_equal(result[7, 7], [0, 0, 255])
        frame = np.zeros((8, 8, 3), np.uint8)
        frame[:, 4:] = [0, 255, 0]
        source = {'id': 'camera', 'kind': 'camera', 'crop': [.5, 0, 0, 0], 'fit': 'stretch'}
        result = compose({'sources': [source]}, {'camera': frame}, (8, 8))
        np.testing.assert_array_equal(result[0, 0], [0, 255, 0])


class AutomationTests(unittest.TestCase):
    def test_explicit_speech_events_fire_only_on_edges(self):
        from unittest.mock import patch
        from app.studio.automation import AutomationEngine
        engine = AutomationEngine([{'event': 'speech_start', 'action': {'type': 'record', 'value': True}}])
        with patch('app.studio.automation.time.monotonic', return_value=1):
            self.assertEqual(len(engine.trigger('speech_start')), 1)
        with patch('app.studio.automation.time.monotonic', return_value=2):
            self.assertEqual(engine.trigger('speech_start'), [])
            engine.trigger('speech_end')
        with patch('app.studio.automation.time.monotonic', return_value=3):
            self.assertEqual(len(engine.trigger('speech_start')), 1)

    def test_timer_and_speech_edges_do_not_repeat(self):
        from app.studio.automation import AutomationEngine
        engine = AutomationEngine([
            {'enabled': True, 'event': 'timer', 'interval': 2, 'action': {'type': 'scene', 'target': 'game'}},
            {'enabled': True, 'event': 'speech_start', 'action': {'type': 'mute', 'value': True}},
            {'enabled': True, 'event': 'speech_end', 'action': {'type': 'mute', 'value': False}},
        ])
        self.assertEqual(engine.poll(0), [])
        self.assertEqual(engine.poll(1, True), [{'type': 'mute', 'value': True}])
        self.assertEqual(engine.poll(1.5, True), [])
        actions = engine.poll(2, False)
        self.assertEqual({a['type'] for a in actions}, {'scene', 'mute'})
        self.assertEqual(engine.poll(2), [])
        self.assertEqual(len(engine.poll(100)), 1)


class RecordingTests(unittest.TestCase):
    def test_replay_mp4_does_not_overwrite_same_named_existing_avi(self):
        from app.studio.recording import ReplayBuffer
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            previous = root / 'replay.avi'
            previous.write_bytes(b'existing user video')
            replay = ReplayBuffer()
            replay.append(np.zeros((24, 32, 3), np.uint8), timestamp=0)
            replay.append_audio(np.zeros(4410, np.float32), timestamp=0)
            replay.save(root / 'replay.mp4')
            self.assertEqual(previous.read_bytes(), b'existing user video')

    def test_combined_mp4_has_real_audio_and_raw_source_video_is_preserved(self):
        import av
        from unittest.mock import patch
        from app.studio.recording import SessionRecorder
        with tempfile.TemporaryDirectory() as folder:
            recorder = SessionRecorder(folder, 32, 24, 10, sample_rate=8000)
            with patch('app.studio.recording.time.monotonic', return_value=10):
                session = recorder.start()
                recorder.write_frame(np.zeros((24, 32, 3), np.uint8), timestamp=10)
                recorder.write_source_frame('camera-id', np.full((12, 16, 3), 120, np.uint8), timestamp=10)
                recorder.write_audio('Микс', np.sin(np.arange(3200) * .2).astype(np.float32) * .2)
                recorder.write_frame(np.full((24, 32, 3), 200, np.uint8), timestamp=10.3)
            recorder.stop()
            manifest = json.loads((session / 'manifest.json').read_text(encoding='utf-8'))
            self.assertTrue(manifest['video_has_audio'], manifest)
            self.assertTrue((session / manifest['video_tracks']['camera-id']).is_file())
            with av.open(str(session / manifest['video'])) as video:
                self.assertEqual(len(video.streams.video), 1)
                self.assertEqual(len(video.streams.audio), 1)
                self.assertGreater(sum(frame.samples for frame in video.decode(audio=0)), 0)
            with av.open(str(session / manifest['video'])) as video:
                self.assertEqual(len(list(video.decode(video=0))), 4)

    def test_replay_mp4_keeps_bounded_audio_and_muxes_it(self):
        import av
        from app.studio.recording import ReplayBuffer
        replay = ReplayBuffer(seconds=1, max_bytes=100000, fps=10)
        for index in range(4):
            replay.append(np.full((24, 32, 3), index * 50, np.uint8), timestamp=index / 10)
            replay.append_audio(np.full(4410, .1, np.float32), timestamp=index / 10)
        self.assertLessEqual(replay.bytes_used, 100000)
        with tempfile.TemporaryDirectory() as folder:
            path = replay.save(Path(folder) / 'replay.mp4')
            with av.open(str(path)) as video:
                self.assertEqual(len(video.streams.audio), 1)
                self.assertGreater(sum(frame.samples for frame in video.decode(audio=0)), 0)
            manifest = json.loads(path.with_suffix(path.suffix + '.json').read_text(encoding='utf-8'))
            self.assertTrue(manifest['video_has_audio'])
        replay.clear()
        self.assertEqual(replay.bytes_used, 0)

    def test_late_audio_track_is_padded_to_recording_timeline(self):
        from unittest.mock import patch
        from app.studio.recording import SessionRecorder
        with tempfile.TemporaryDirectory() as folder:
            recorder = SessionRecorder(folder, 32, 24, 10, sample_rate=8000)
            with patch('app.studio.recording.time.monotonic', return_value=10):
                session = recorder.start()
            with patch('app.studio.recording.time.monotonic', return_value=11):
                recorder.write_audio('guest', np.full(800, .5, np.float32))
            recorder.stop()
            manifest = json.loads((session / 'manifest.json').read_text(encoding='utf-8'))
            with wave.open(str(session / manifest['audio']['guest']), 'rb') as audio:
                samples = np.frombuffer(audio.readframes(audio.getnframes()), '<i2')
            self.assertEqual(len(samples), 8000)
            self.assertTrue(np.all(samples[:7200] == 0))
            self.assertGreater(samples[-1], 0)

    def test_replay_bounds_and_real_video_output(self):
        from app.studio.recording import ReplayBuffer
        import cv2
        replay = ReplayBuffer(seconds=1, max_bytes=10000, fps=10)
        for index in range(30):
            replay.append(np.full((24, 32, 3), index, np.uint8), index / 10)
        self.assertLessEqual(replay.bytes_used, 10000)
        with tempfile.TemporaryDirectory() as folder:
            output = replay.save(Path(folder) / 'replay.avi')
            video = cv2.VideoCapture(str(output))
            self.assertTrue(video.isOpened())
            self.assertLessEqual(video.get(cv2.CAP_PROP_FRAME_COUNT), 11)
            video.release()
        replay.clear()
        self.assertEqual(replay.bytes_used, 0)

    def test_session_finalizes_timestamp_corrected_video_and_named_audio(self):
        from app.studio.recording import SessionRecorder
        import cv2
        with tempfile.TemporaryDirectory() as folder:
            recorder = SessionRecorder(folder, 32, 24, 10, sample_rate=8000)
            session = recorder.start()
            self.assertTrue(recorder.recording)
            frame = np.zeros((24, 32, 3), np.uint8)
            recorder.write_frame(frame, 10)
            recorder.write_frame(frame + 255, 10.3)
            recorder.write_audio('Микрофон', np.ones(800, np.float32) * .25)
            self.assertEqual(recorder.stop(), session)
            self.assertFalse(recorder.recording)
            manifest = json.loads((session / 'manifest.json').read_text(encoding='utf-8'))
            video = cv2.VideoCapture(str(session / manifest['video']))
            self.assertEqual(video.get(cv2.CAP_PROP_FRAME_COUNT), 4)
            video.release()
            with wave.open(str(session / manifest['audio']['Микрофон']), 'rb') as audio:
                self.assertEqual(audio.getnframes(), 800)
                self.assertEqual(audio.getframerate(), 8000)
            self.assertTrue(manifest['video_has_audio'])


class ExtensionTests(unittest.TestCase):
    def test_close_waits_for_in_flight_apply(self):
        import threading
        from app.studio.extensions import ExtensionHost
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'effect.py'
            marker = Path(folder) / 'processing'
            path.write_text('import time\nfrom pathlib import Path\ndef process_frame(frame):\n'
                            f'    Path({str(marker)!r}).write_text("started")\n'
                            '    time.sleep(.2)\n    return 255-frame\n', encoding='utf-8')
            host = ExtensionHost(path, timeout=2)
            results = []
            worker = threading.Thread(target=lambda: results.append(host.apply(np.zeros((8, 8, 3), np.uint8))))
            worker.start()
            deadline = time.monotonic() + 2
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists(), host.error)
            host.close()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(np.all(results[0] == 255), host.error)

    def test_process_isolated_transform_and_timeout_fail_closed(self):
        from app.studio.extensions import ExtensionHost
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'effect.py'
            path.write_text('def process_frame(frame):\n    return 255 - frame\n', encoding='utf-8')
            host = ExtensionHost(path, timeout=2)
            try:
                result = host.apply(np.zeros((8, 8, 3), np.uint8))
                self.assertTrue(np.all(result == 255), host.error)
            finally:
                host.close()
            path.write_text('import time\ndef process_frame(frame):\n    time.sleep(5)\n    return frame\n', encoding='utf-8')
            host = ExtensionHost(path, timeout=.1)
            original = np.zeros((8, 8, 3), np.uint8)
            try:
                start = time.monotonic()
                np.testing.assert_array_equal(host.apply(original), original)
                self.assertLess(time.monotonic() - start, 2)
                self.assertTrue(host.error)
            finally:
                host.close()


if __name__ == '__main__':
    unittest.main()
