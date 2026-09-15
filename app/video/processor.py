import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
import queue

from app.video.effects import VideoEffects


class VideoProcessor(QObject):
    processed_frame = pyqtSignal(np.ndarray)

    def __init__(self, effects: VideoEffects):
        super().__init__()
        self._effects = effects
        self._latest_raw = None
        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30 fps processing
        self._timer.timeout.connect(self._process_latest)

    def start(self):
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def feed(self, frame):
        self._latest_raw = frame

    def _process_latest(self):
        if self._latest_raw is None:
            return
        frame = self._latest_raw
        self._latest_raw = None
        processed = self._effects.apply(frame)
        self.processed_frame.emit(processed)
