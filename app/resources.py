"""Paths shared by source runs and PyInstaller resources."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def default_model_paths():
    paths = {'segmentation_model': ROOT / 'models/selfie_segmenter_landscape.tflite',
             'face_model': ROOT / 'models/face_landmarker.task',
             'model_path': ROOT / 'models/vosk-model-small-ru-0.22'}
    return {key: str(path) for key, path in paths.items() if path.exists()}
