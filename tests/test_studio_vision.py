import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
import cv2
import numpy as np
from app.studio.vision import VisionProcessor


class VisionTests(unittest.TestCase):
    def test_model_runtime_requires_explicit_session_metrics_consent(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import builtins
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if name == 'mediapipe' or name.startswith('mediapipe.'):
                raise AssertionError('SDK must not start without consent')
            return original_import(name, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'model.tflite'
            model.write_bytes(b'dummy-local-model')
            processor = VisionProcessor()
            processor.configure(dict(background='blur', segmentation_model=str(model)))
            try:
                with patch('builtins.__import__', guarded_import):
                    frame = np.zeros((24, 32, 3), np.uint8)
                    np.testing.assert_array_equal(processor.apply(frame), frame)
                self.assertIn('согласие', processor.status)
                self.assertIsNone(processor._segmenter)
            finally:
                processor.close()

    def test_group_framing_uses_all_haar_faces_and_speaker_fallback_uses_largest(self):
        frame = np.zeros((360, 640, 3), np.uint8)
        frame[100:180, 20:80] = [0, 255, 0]
        frame[80:230, 500:600] = [0, 0, 255]
        faces = np.array([[20, 100, 60, 80], [500, 80, 100, 150]])
        processor = VisionProcessor()
        try:
            processor.configure({'auto_frame': True, 'framing_mode': 'group'})
            processor._haar = SimpleNamespace(detectMultiScale=lambda *args, **kwargs: faces)
            processor._infer(frame, 1)
            self.assertEqual(processor.framing_state['x'], 20)
            self.assertEqual(processor.framing_state['width'], 580)
            for _ in range(40):
                result = processor._autoframe(frame)
            np.testing.assert_array_equal(result, frame)
            processor.configure({'auto_frame': True, 'framing_mode': 'speaker'})
            processor._haar = SimpleNamespace(detectMultiScale=lambda *args, **kwargs: faces)
            processor._infer(frame, 2)
            self.assertEqual(processor.framing_state['x'], 500)
            self.assertEqual(processor.framing_state['width'], 100)
        finally:
            processor.close()

    def test_speaker_jaw_selection_has_hysteresis_and_survives_detection_reordering(self):
        processor = VisionProcessor()
        processor.configure({'auto_frame': True, 'framing_mode': 'speaker'})
        def face(x, jaw):
            return dict(x=x, y=40., width=80., height=100., roll=0., mouth_open=jaw > .35,
                        blink=False, jaw_open=jaw)
        try:
            processor._update_faces([face(20., .8), face(300., .1)], 1., has_jaw=True)
            self.assertEqual(processor.framing_state['x'], 20.)
            processor._update_faces([face(300., .9), face(20., .1)], 1.1, has_jaw=True)
            self.assertEqual(processor.framing_state['x'], 20.)
            processor._update_faces([face(20., .1), face(300., .9)], 1.5, has_jaw=True)
            self.assertEqual(processor.framing_state['x'], 20.)
            processor._update_faces([face(300., .9), face(20., .1)], 1.71, has_jaw=True)
            self.assertEqual(processor.framing_state['x'], 300.)
            # A single briefly open mouth never steals the shot back.
            processor._update_faces([face(20., .95), face(300., .1)], 1.8, has_jaw=True)
            processor._update_faces([face(20., .1), face(300., .8)], 2., has_jaw=True)
            self.assertEqual(processor.framing_state['x'], 300.)
        finally:
            processor.close()

    def test_different_nearby_challengers_do_not_share_speaker_hysteresis(self):
        processor = VisionProcessor()
        processor.configure({'auto_frame': True, 'framing_mode': 'speaker'})
        def faces(scores):
            return [dict(x=float(x), y=40., width=60., height=80., roll=0.,
                         mouth_open=score > .35, blink=False, jaw_open=score)
                    for x, score in zip((10, 170, 240), scores)]
        try:
            processor._update_faces(faces((.9, .1, .1)), 1., has_jaw=True)
            processor._update_faces(faces((.1, .9, .1)), 1.1, has_jaw=True)
            processor._update_faces(faces((.1, .1, .9)), 1.5, has_jaw=True)
            processor._update_faces(faces((.1, .1, .9)), 1.71, has_jaw=True)
            self.assertEqual(processor.framing_state['x'], 10.)
            processor._update_faces(faces((.1, .1, .9)), 2.11, has_jaw=True)
            self.assertEqual(processor.framing_state['x'], 240.)
        finally:
            processor.close()

    def test_disabled_preserves_bgr_and_bgra_even_tiny(self):
        processor = VisionProcessor()
        for channels in (3, 4):
            for shape in ((1, 1, channels), (2, 3, channels), (32, 48, channels)):
                frame = np.arange(np.prod(shape), dtype=np.uint8).reshape(shape)
                self.assertTrue(np.array_equal(processor.apply(frame), frame))
        processor.close()

    def test_missing_models_preserve_background_and_explain(self):
        processor = VisionProcessor()
        processor.configure({'background':'color','background_color':'#ff0000','segmentation_model':'missing-seg.tflite','reactive_mask':True,'face_model':'missing-face.task'})
        frame = np.full((8, 12, 3), 55, np.uint8)
        self.assertTrue(np.array_equal(processor.apply(frame), frame))
        self.assertIn('missing-seg', processor.status)
        self.assertIn('missing-face', processor.status)
        self.assertFalse(processor.face_state['detected'])
        processor.close()

    def test_no_face_autoframe_tiny_is_safe(self):
        processor = VisionProcessor()
        processor.configure({'auto_frame':True})
        for channels in (3,4):
            frame = np.zeros((1, 2, channels), np.uint8)
            self.assertEqual(processor.apply(frame).shape, frame.shape)
        processor.close()

    def test_mask_clips_to_frame_and_keeps_alpha(self):
        with tempfile.TemporaryDirectory() as directory:
            mask_path = str(Path(directory)/'mask.png')
            cv2.imwrite(mask_path, np.full((8,8,4), (0,0,255,255), np.uint8))
            processor = VisionProcessor()
            processor.configure({'mask_path':mask_path,'mask_scale':2.0})
            # Recent tracking can place masks partly outside the image.
            processor.face_state.update(detected=True,x=-2,y=-2,width=8,height=8,roll=0)
            processor._last_face_time = __import__('time').monotonic()
            processor._last_inference = __import__('time').monotonic()
            frame = np.full((10,10,4), (0,0,0,127), np.uint8)
            result = processor.apply(frame)
            self.assertEqual(result.shape, frame.shape)
            self.assertTrue(np.any(result[:,:,2]>0))
            self.assertTrue(np.all(result[:,:,3] == 127))
            processor.close()

if __name__ == '__main__':
    unittest.main()
