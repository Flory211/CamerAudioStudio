import cv2
import numpy as np
import mss
import pygetwindow as gw
from PyQt6.QtCore import QThread, pyqtSignal


class CameraCapture(QThread):
    frame_ready = pyqtSignal(np.ndarray)
    error_occurred = pyqtSignal(str)

    def __init__(self, camera_index=0, fps=30, resolution=None):
        super().__init__()
        self.camera_index = camera_index
        self.fps = fps
        self.resolution = resolution
        self._running = False
        self._cap = None

    def run(self):
        self._cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not self._cap.isOpened():
            self.error_occurred.emit(f"Cannot open camera {self.camera_index}")
            return

        if self.resolution:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
        self._cap.set(cv2.CAP_PROP_FPS, self.fps)

        self._running = True
        while self._running:
            ret, frame = self._cap.read()
            if not ret:
                self.error_occurred.emit("Failed to read frame from camera")
                break
            self.frame_ready.emit(frame)
            self.msleep(int(1000 / self.fps))

        self._cap.release()

    def stop(self):
        self._running = False
        self.wait(2000)


class ScreenCapture(QThread):
    frame_ready = pyqtSignal(np.ndarray)
    error_occurred = pyqtSignal(str)

    def __init__(self, fps=30, monitor_index=0):
        super().__init__()
        self.fps = fps
        self.monitor_index = monitor_index
        self._running = False

    def run(self):
        self._running = True
        with mss.mss() as sct:
            monitors = sct.monitors[1:]  # skip the "all monitors" combined entry
            if not monitors:
                self.error_occurred.emit("No monitors found")
                return
            idx = min(self.monitor_index, len(monitors) - 1)
            monitor = monitors[idx]

            while self._running:
                img = sct.grab(monitor)
                frame = np.array(img)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                self.frame_ready.emit(frame)
                self.msleep(int(1000 / self.fps))

    def stop(self):
        self._running = False
        self.wait(2000)


class WindowCapture(QThread):
    frame_ready = pyqtSignal(np.ndarray)
    error_occurred = pyqtSignal(str)

    def __init__(self, window_title, fps=30):
        super().__init__()
        self.window_title = window_title
        self.fps = fps
        self._running = False

    def _get_window_rect(self):
        windows = gw.getWindowsWithTitle(self.window_title)
        if not windows:
            return None
        win = windows[0]
        return {
            "left": max(win.left, 0),
            "top": max(win.top, 0),
            "width": max(win.width, 1),
            "height": max(win.height, 1),
        }

    def run(self):
        self._running = True
        with mss.mss() as sct:
            while self._running:
                region = self._get_window_rect()
                if region is None:
                    self.error_occurred.emit(f"Window '{self.window_title}' not found")
                    self.msleep(500)
                    continue

                img = sct.grab(region)
                frame = np.array(img)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                self.frame_ready.emit(frame)
                self.msleep(int(1000 / self.fps))

    def stop(self):
        self._running = False
        self.wait(2000)


def list_cameras(max_index=5):
    available = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            available.append(i)
        cap.release()
    return available


def list_window_titles():
    return [t for t in gw.getAllTitles() if t.strip()]
