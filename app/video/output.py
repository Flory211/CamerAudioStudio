import numpy as np
import pyvirtualcam
from PyQt6.QtCore import QThread, pyqtSignal
import queue


class VirtualCameraOutput(QThread):
    error_occurred = pyqtSignal(str)
    started = pyqtSignal()

    def __init__(self, width, height, fps, backend="obs"):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self.backend = backend
        self._frame_queue = queue.Queue(maxsize=2)
        self._running = False
        self._cam = None

    def send_frame(self, frame):
        try:
            self._frame_queue.put_nowait(frame)
        except queue.Full:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frame_queue.put_nowait(frame)
            except queue.Full:
                pass

    def run(self):
        try:
            self._cam = pyvirtualcam.Camera(
                width=self.width,
                height=self.height,
                fps=self.fps,
                backend=self.backend,
            )
        except Exception as e:
            self.error_occurred.emit(f"Failed to create virtual camera: {e}")
            return

        self._running = True
        self.started.emit()

        while self._running:
            try:
                frame = self._frame_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            rgb = frame[:, :, ::-1]  # BGR → RGB
            if rgb.shape[0] != self.height or rgb.shape[1] != self.width:
                import cv2
                rgb = cv2.resize(rgb, (self.width, self.height))

            self._cam.send(rgb)
            self._cam.sleep_until_next_frame()

        self._cam.close()

    def stop(self):
        self._running = False
        self.wait(3000)
