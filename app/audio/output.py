import numpy as np
import sounddevice as sd
from PyQt6.QtCore import QThread, pyqtSignal
import queue


class VirtualAudioOutput(QThread):
    error_occurred = pyqtSignal(str)
    started = pyqtSignal()

    def __init__(self, device_id=None, sample_rate=44100, channels=2, blocksize=1024):
        super().__init__()
        self.device_id = device_id
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        self._audio_queue = queue.Queue(maxsize=10)
        self._running = False
        self._stream = None

    def _find_device(self):
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if self.device_name.lower() in d["name"].lower() and d["max_output_channels"] > 0:
                return i
        return None

    def send_audio(self, data):
        try:
            self._audio_queue.put_nowait(data)
        except queue.Full:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._audio_queue.put_nowait(data)
            except queue.Full:
                pass

    def run(self):
        if self.device_id is None:
            self.error_occurred.emit(
                "No audio output device selected. "
                "Choose a device in the Output dropdown."
            )
            return

        try:
            self._stream = sd.OutputStream(
                device=self.device_id,
                samplerate=None,
                channels=self.channels,
                blocksize=self.blocksize,
                dtype="float32",
            )
            self._stream.start()
        except Exception as e:
            self.error_occurred.emit(f"Cannot open audio output: {e}")
            return

        self._running = True
        self.started.emit()

        while self._running:
            try:
                data = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                self._stream.write(data)
            except Exception as e:
                self.error_occurred.emit(f"Audio output write error: {e}")
                break

        self._stream.stop()
        self._stream.close()

    def stop(self):
        self._running = False
        self.wait(3000)
