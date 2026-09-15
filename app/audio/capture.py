import numpy as np
import sounddevice as sd
from PyQt6.QtCore import QThread, pyqtSignal
import queue


class AudioCapture(QThread):
    audio_ready = pyqtSignal(np.ndarray)
    error_occurred = pyqtSignal(str)

    def __init__(self, device=None, sample_rate=44100, channels=2, blocksize=1024):
        super().__init__()
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        self._audio_queue = queue.Queue()
        self._running = False
        self._stream = None

    def _callback(self, indata, frames, time, status):
        if status:
            self.error_occurred.emit(f"Audio capture error: {status}")
        self._audio_queue.put(indata.copy())

    def run(self):
        try:
            self._stream = sd.InputStream(
                device=self.device,
                samplerate=None,
                channels=self.channels,
                blocksize=self.blocksize,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as e:
            self.error_occurred.emit(f"Cannot open audio device: {e}")
            return

        self._running = True
        while self._running:
            try:
                data = self._audio_queue.get(timeout=0.5)
                self.audio_ready.emit(data)
            except queue.Empty:
                continue

        self._stream.stop()
        self._stream.close()

    def stop(self):
        self._running = False
        self.wait(2000)


def list_audio_devices():
    return sd.query_devices()
