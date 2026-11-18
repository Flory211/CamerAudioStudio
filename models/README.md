# Локальные модели

Подготовка: `.venv\Scripts\python.exe tools\download_models.py`.
Приложение не загружает модели автоматически и не отправляет речь/видео в облако.

- Selfie Segmenter (landscape): https://developers.google.com/edge/mediapipe/solutions/vision/image_segmenter
- Face Landmarker: https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker
- Vosk small Russian 0.22 (Apache 2.0): https://alphacephei.com/vosk/models

Размеры и SHA-256 загруженных файлов записываются в `sources.json`.
В EXE включается распакованная модель Vosk, без дубликата ZIP.
