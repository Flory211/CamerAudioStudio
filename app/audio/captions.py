"""Local Vosk captions on a bounded background queue. No model downloads."""
import json
import math
import queue
import threading
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly


class CaptionService:
    def __init__(self, model_path, sample_rate=44100):
        path = Path(model_path) if model_path else None
        if path is None or not path.is_dir():
            raise ValueError('Выбери папку распакованной модели Vosk. Модель загружается отдельно с сайта Vosk.')
        if not math.isfinite(float(sample_rate)) or sample_rate < 8000:
            raise ValueError('Неверная частота звука для субтитров')
        self.model_path = str(path)
        self.sample_rate = int(sample_rate)
        self.text = ''
        self.error = ''
        self.segments = []
        self._queue = queue.Queue(maxsize=8)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker = None
        self._received = 0
        self._closed = False

    def feed(self, samples):
        if self._closed:
            return self.text or None
        data = np.asarray(samples, dtype=np.float32)
        if data.ndim == 2:
            data = data.mean(axis=1)
        if data.ndim != 1:
            raise ValueError('Субтитры ожидают аудиоблок (N,) или (N, каналы)')
        with self._lock:
            for offset in range(0, len(data), self.sample_rate):
                block = data[offset:offset+self.sample_rate].copy()
                item = (self._received, block)
                self._received += len(block)
                try:
                    self._queue.put_nowait(item)
                except queue.Full:
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._queue.put_nowait(item)
                    except queue.Full:
                        pass
            if self._worker is None and not self._queue.empty():
                self._worker = threading.Thread(target=self._recognize, daemon=True, name='StudioCaptions')
                self._worker.start()
        return self.text or None

    def _result(self, raw, origin, end_time):
        result = json.loads(raw)
        text = result.get('text', '').strip()
        if not text:
            return
        words = result.get('result', [])
        with self._lock:
            start = origin + float(words[0]['start']) if words else (self.segments[-1]['end'] if self.segments else origin)
            end = origin + float(words[-1]['end']) if words else end_time
            self.segments.append(dict(start=max(0, start), end=max(start+.001, end), text=text))
            self.text = text

    def _recognize(self):
        try:
            from vosk import Model, KaldiRecognizer
            model = Model(self.model_path)
            recognizer = None
            expected = None
            origin, end_time = 0., 0.
            divisor = math.gcd(self.sample_rate, 16000)
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    position, block = self._queue.get(timeout=.1)
                except queue.Empty:
                    continue
                if expected is None or position != expected:
                    if recognizer is not None:
                        self._result(recognizer.FinalResult(), origin, end_time)
                    origin = position/self.sample_rate
                    recognizer = KaldiRecognizer(model, 16000)
                    recognizer.SetWords(True)
                expected = position+len(block)
                end_time = expected/self.sample_rate
                mono = np.nan_to_num(block, nan=0., posinf=0., neginf=0.)
                mono = resample_poly(mono, 16000//divisor, self.sample_rate//divisor)
                pcm = (np.clip(mono, -1, 1)*32767).astype('<i2').tobytes()
                if recognizer.AcceptWaveform(pcm):
                    self._result(recognizer.Result(), origin, end_time)
                else:
                    partial = json.loads(recognizer.PartialResult()).get('partial', '')
                    if partial:
                        self.text = partial
            if recognizer is not None:
                self._result(recognizer.FinalResult(), origin, end_time)
        except ImportError:
            self.error = 'Распознавание недоступно: установи vosk в окружение приложения.'
        except Exception as exc:
            self.error = f'Не удалось запустить Vosk. Проверь папку распакованной модели: {exc}'

    def close(self):
        self._closed = True
        self._stop.set()
        if self._worker and self._worker is not threading.current_thread():
            self._worker.join(timeout=10)
            if self._worker.is_alive():
                self.error = 'Модель Vosk ещё загружается; завершение распознавания ожидается в фоне.'

    @staticmethod
    def _timestamp(seconds):
        value = max(0, round(float(seconds)*1000))
        hours, value = divmod(value, 3600000)
        minutes, value = divmod(value, 60000)
        secs, millis = divmod(value, 1000)
        return f'{hours:02}:{minutes:02}:{secs:02},{millis:03}'

    def save_srt(self, path):
        with self._lock:
            segments = list(self.segments)
        content = ''.join(f"{index}\n{self._timestamp(segment['start'])} --> {self._timestamp(segment['end'])}\n{segment['text']}\n\n"
                          for index, segment in enumerate(segments, 1))
        Path(path).write_text(content, encoding='utf-8')
