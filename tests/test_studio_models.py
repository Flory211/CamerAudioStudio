"""Exercise actual bundled models; these checks never open a device."""
from pathlib import Path
import time
import unittest
import os
import numpy as np

from app.resources import default_model_paths
from app.studio.vision import VisionProcessor


class LocalModelsTests(unittest.TestCase):
    def test_real_segmenter_and_landmarker_load_and_infer(self):
        if os.environ.get('CAMERAUDIO_ALLOW_MODEL_METRICS') != '1':
            self.skipTest('MediaPipe SDK metrics require explicit opt-in')
        paths = default_model_paths()
        if not all(key in paths for key in ('segmentation_model', 'face_model')):
            self.skipTest('Optional models are not downloaded')
        vision = VisionProcessor()
        try:
            vision.configure(dict(segmentation_model=paths['segmentation_model'],
                face_model=paths['face_model'], background='color', background_color='#2266aa',
                auto_frame=True, allow_mediapipe_metrics=True))
            frame = np.zeros((144, 256, 3), np.uint8)
            result = vision.apply(frame)
            self.assertEqual(result.shape, frame.shape)
            self.assertEqual(result.dtype, np.uint8)
            self.assertIsNotNone(vision._segmenter, vision.status)
            self.assertIsNotNone(vision._landmarker, vision.status)
            self.assertIsNotNone(vision._matte, vision.status)
        finally:
            vision.close()

    def test_real_vosk_model_accepts_silence(self):
        path = default_model_paths().get('model_path')
        if not path:
            self.skipTest('Optional Russian model is not downloaded')
        from app.audio.captions import CaptionService
        service = CaptionService(path)
        try:
            service.feed(np.zeros((44100, 2), dtype=np.float32))
            deadline = time.monotonic() + 20
            while service._worker and not service._queue.empty() and time.monotonic() < deadline:
                time.sleep(.05)
        finally:
            service.close()
        self.assertFalse(service.error, service.error)


if __name__ == '__main__':
    unittest.main()
