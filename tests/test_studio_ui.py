import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import unittest
import time
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.main_window import MainWindow


class StudioUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.app.processEvents()

    def test_opens_without_starting_devices_and_uses_native_window(self):
        self.assertFalse(self.window.engine.running)
        self.assertFalse(self.window.audio_engine.running)
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertGreaterEqual(self.window.scene_list.count(), 3)
        self.assertGreaterEqual(self.window.minimumWidth(), 1000)

    def test_scenes_layers_and_project_roundtrip(self):
        scene = self.window.add_scene('Проверка')
        source = self.window.add_source('text', name='Титр', text='Привет, Лев!')
        self.assertEqual(self.window.project.active_scene['id'], scene['id'])
        self.assertEqual(self.window.project.find_source(source['id'])['text'], 'Привет, Лев!')
        self.assertEqual(self.window.source_list.count(), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'Проект.cas.json'
            self.window.save_project(path)
            self.window.add_scene('Лишняя')
            self.window.dirty = False
            self.window.open_project(path)
            self.assertEqual(self.window.project.active_scene['name'], 'Проверка')
            self.assertEqual(self.window.project.active_scene['sources'][0]['text'], 'Привет, Лев!')

    def test_preview_coordinates_respect_letterboxing(self):
        self.window.preview.resize(800, 600)
        self.window.preview.canvas_size = (1280, 720)
        point = self.window.preview.map_to_canvas(400, 300)
        self.assertAlmostEqual(point[0], 0.5, places=2)
        self.assertAlmostEqual(point[1], 0.5, places=2)
        self.assertIsNone(self.window.preview.map_to_canvas(400, 0))

    def test_automation_changes_real_scene(self):
        scene = self.window.project.scenes[-1]
        self.window.execute_command({'type': 'scene', 'target': scene['id']})
        self.assertEqual(self.window.project.active_scene['id'], scene['id'])

    def test_unsupported_video_and_crop_controls_are_disabled_for_text(self):
        self.window.add_source('text',name='Титр',text='Пример')
        self.assertFalse(self.window.effect_controls['invert'].isEnabled())
        self.assertFalse(self.window.crop[0].isEnabled())
        self.window.add_source('camera',name='Камера')
        self.assertTrue(self.window.effect_controls['invert'].isEnabled())
        self.assertTrue(self.window.crop[0].isEnabled())

    def test_source_percent_controls_store_normalized_coordinates(self):
        source=self.window.add_source('image',x=.25,width=.5)
        self.assertAlmostEqual(self.window.transforms['x'].value(),25)
        self.assertAlmostEqual(self.window.transforms['width'].value(),50)
        self.window.transforms['x'].setValue(40)
        self.assertAlmostEqual(source['x'],.4)
        self.window.transforms['opacity'].setValue(75)
        self.assertAlmostEqual(source['opacity'],.75)

    def test_guest_ids_are_visible_and_selectable_without_manual_endpoint_inspection(self):
        from types import SimpleNamespace
        import numpy as np
        self.window.server=SimpleNamespace(running=True,url='http://127.0.0.1:1/?token=x',certificate_path='',
            guest_frames=lambda:{'Лев-camera-abc':np.zeros((1,1,3),np.uint8)},stop=lambda:None)
        self.window.add_source('guest',device='')
        self.window.tools.refresh()
        self.assertIn('Лев-camera-abc',self.window.tools.guest_ids.toPlainText())
        self.window._refresh_guest_choices()
        index=self.window.guest_choice.findData('Лев-camera-abc')
        self.assertGreaterEqual(index,0)
        self.window.guest_choice.setCurrentIndex(index)
        self.assertEqual(self.window._selected_source()['device'],'Лев-camera-abc')
        self.window.server=None

    def test_remote_mute_updates_visible_mixer_and_output_state(self):
        self.window.execute_command({'type': 'mute', 'target': 'Микрофон', 'value': True})
        self.assertTrue(self.window.audio_engine.channels['Микрофон']['muted'])
        self.assertTrue(self.window.mixer.rows['Микрофон']['muted'].isChecked())

    def test_oversized_import_cannot_replace_current_project(self):
        import json
        import copy
        original = self.window.project
        data = copy.deepcopy(original.data)
        data.update(width=8192, height=8192)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'oversized.json'
            path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            self.assertFalse(self.window.open_project(path))
        self.assertIs(self.window.project, original)

    def test_recording_master_does_not_double_individual_channel(self):
        import av
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            self.window.project.data.update(width=320,height=180)
            self.window.mark_changed()
            self.window.engine.start_recording(directory)
            try:
                self.window.engine.recorder.write_frame(np.zeros((180,320,3),np.uint8))
                block=np.full((4410,2),.2,np.float32)
                self.window.audio_engine.track_ready.emit('Микрофон',block)
                self.window._audio_block(block)
                time.sleep(.18)
                path=self.window.engine.stop_recording()
                with av.open(str(path/'recording.mp4')) as recording:
                    values=[frame.to_ndarray() for frame in recording.decode(audio=0)]
                peak=max(float(np.abs(value).max()) for value in values)
                self.assertGreater(peak,.1)
                self.assertLess(peak,.3)
            finally:
                self.window.engine.stop()


if __name__ == '__main__':
    unittest.main()
