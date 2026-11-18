"""Bounded audio capture/mixer worker; importing or constructing opens no device."""
import math
import queue
import threading
import time
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly
from PyQt6.QtCore import QObject, pyqtSignal

from .effects import AudioEffects


def stereo(samples, sample_rate=44100):
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim == 1:
        data = data[:, None]
    if data.ndim != 2 or data.shape[1] < 1:
        raise ValueError('Ожидается аудио (N, каналы)')
    data = np.repeat(data, 2, axis=1) if data.shape[1] == 1 else data[:, :2]
    if sample_rate != 44100 and len(data):
        divisor = math.gcd(int(sample_rate), 44100)
        data = resample_poly(data, 44100//divisor, int(sample_rate)//divisor, axis=0)
    return np.nan_to_num(data, nan=0., posinf=0., neginf=0.).astype(np.float32)


def _offer(buffer, value):
    try:
        buffer.put_nowait(value)
    except queue.Full:
        try:
            buffer.get_nowait()
        except queue.Empty:
            pass
        try:
            buffer.put_nowait(value)
        except queue.Full:
            pass


class AudioEngine(QObject):
    levels_changed = pyqtSignal(dict)
    audio_ready = pyqtSignal(object)
    track_ready = pyqtSignal(str, object)
    error_occurred = pyqtSignal(str)
    caption_text = pyqtSignal(str)
    sample_rate = 44100
    blocksize = 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self.effects = AudioEffects()
        self.running = False
        self.channels = {}
        self.levels = {}
        self._lock = threading.RLock()
        self._external = {}
        self._pending = {}
        self._sounds = {}
        self._output = queue.Queue(maxsize=8)
        self._output_pending = np.empty((0, 2), dtype=np.float32)
        self._failures = queue.Queue(maxsize=2)
        self._thread = None
        self._loop_thread = None
        self._stop = threading.Event()
        self.ducking = True
        self.duck_gain = 0.25
        self._channel('Микрофон')
        self._channel('Система')

    def _channel(self, name):
        with self._lock:
            return self.channels.setdefault(name, dict(gain=1., muted=False, solo=False, record=True, output=True))

    def set_gain(self, channel, value):
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 20:
            raise ValueError('Уровень канала должен быть от 0 до 20')
        with self._lock:
            self._channel(channel)['gain'] = value

    def set_muted(self, channel, value):
        with self._lock:
            self._channel(channel)['muted'] = bool(value)

    def set_solo(self, channel, value):
        with self._lock:
            self._channel(channel)['solo'] = bool(value)

    def set_route(self, channel, record=True, output=True):
        with self._lock:
            self._channel(channel).update(record=bool(record), output=bool(output))

    def mix_block(self, tracks):
        """Return record mix, monitor mix, and routed pre-master tracks (all stereo)."""
        size = max((len(value) for value in tracks.values()), default=self.blocksize)
        record = np.zeros((size, 2), dtype=np.float32)
        output = np.zeros_like(record)
        individual, levels = {}, {}
        with self._lock:
            settings = {name: dict(self._channel(name)) for name in tracks}
            any_solo = any(value['solo'] for value in self.channels.values())
        for name, value in tracks.items():
            data = stereo(value)
            if len(data) < size:
                data = np.pad(data, ((0, size-len(data)), (0, 0)))
            config = settings[name]
            if name == 'Микрофон':
                data = self.effects.apply(data)
            data = data*config['gain']
            if config['muted'] or (any_solo and not config['solo']):
                data = np.zeros_like(data)
            data = np.clip(data, -1, 1).astype(np.float32)
            rms = float(np.sqrt(np.mean(data*data))) if len(data) else 0
            levels[name] = max(-120., 20*math.log10(max(rms, 1e-6)))
            if config['record']:
                record += data
                individual[name] = data.copy()
            if config['output']:
                output += data
        self.levels = dict(levels)
        self.levels_changed.emit(levels)
        limit = self.effects.limiter
        return np.clip(record, -limit, limit), np.clip(output, -limit, limit), individual

    def feed_external(self, name, samples, sample_rate=44100):
        if not math.isfinite(float(sample_rate)) or sample_rate < 8000:
            raise ValueError('Неверная частота внешнего звука')
        data = stereo(samples, sample_rate)
        # A producer cannot retain arbitrarily large buffers or overrun realtime work.
        with self._lock:
            self._channel(name)
            buffer = self._external.setdefault(name, queue.Queue(maxsize=8))
        for offset in range(0, len(data), self.sample_rate):
            _offer(buffer, data[offset:offset+self.sample_rate].copy())

    def _pull(self, name, frames):
        with self._lock:
            buffer = self._external.get(name)
        pending = self._pending.pop(name, np.empty((0, 2), dtype=np.float32))
        chunks, count = [], 0
        while count < frames:
            if not len(pending):
                try:
                    pending = buffer.get_nowait() if buffer else np.empty((0, 2), dtype=np.float32)
                except queue.Empty:
                    break
                if not len(pending):
                    break
            take = min(frames-count, len(pending))
            chunks.append(pending[:take])
            count += take
            pending = pending[take:]
        if len(pending):
            self._pending[name] = pending
        chunks.append(np.zeros((frames-count, 2), dtype=np.float32))
        return np.concatenate(chunks)

    def play_sound(self, path, loop=False, gain=1.):
        import soundfile as sf
        gain = float(gain)
        if not math.isfinite(gain) or not 0 <= gain <= 20:
            raise ValueError('Уровень звука должен быть от 0 до 20')
        handle = sf.SoundFile(str(path))
        if not handle.frames:
            handle.close()
            raise ValueError('Звуковой файл пуст')
        name = f'Звук: {Path(path).stem}'
        with self._lock:
            if name in self._sounds:
                self._sounds[name]['file'].close()
            self._sounds[name] = dict(file=handle, loop=bool(loop), gain=gain)
            self._channel(name)
        return name

    def stop_sounds(self):
        with self._lock:
            for sound in self._sounds.values():
                sound['file'].close()
            self._sounds.clear()

    def _sound_blocks(self, frames, speech=False):
        tracks = {}
        with self._lock:
            for name, sound in list(self._sounds.items()):
                handle = sound['file']
                source_frames = max(1, round(frames*handle.samplerate/self.sample_rate))
                pieces, remaining = [], source_frames
                while remaining:
                    piece = handle.read(remaining, dtype='float32', always_2d=True)
                    pieces.append(piece)
                    remaining -= len(piece)
                    if not remaining:
                        break
                    if sound['loop']:
                        handle.seek(0)
                    else:
                        break
                data = stereo(np.concatenate(pieces), handle.samplerate)
                data = np.pad(data[:frames], ((0, max(0, frames-len(data))), (0, 0)))
                tracks[name] = data*sound['gain']*(self.duck_gain if self.ducking and speech else 1)
                if not sound['loop'] and handle.tell() == handle.frames:
                    handle.close()
                    del self._sounds[name]
        return tracks

    def start(self, input_device=None, output_device=None, system_audio=False):
        if self.running:
            return
        if any(worker and worker.is_alive() for worker in (self._thread, self._loop_thread)):
            self.error_occurred.emit('Предыдущий аудиопоток ещё закрывается. Повтори включение через несколько секунд.')
            return
        self._stop.clear()
        self._pending.clear()
        self._external.clear()
        self._output_pending = np.empty((0, 2), dtype=np.float32)
        for buffer in (self._output, self._failures):
            while not buffer.empty():
                try:
                    buffer.get_nowait()
                except queue.Empty:
                    break
        self.running = True
        self._thread = threading.Thread(target=self._run, args=(input_device, output_device, system_audio), daemon=True, name='StudioAudio')
        self._thread.start()

    def _output_callback(self, outdata, frames, timing, status):
        if status:
            _offer(self._failures, f'Сбой звукового вывода: {status}')
        outdata.fill(0)
        offset = 0
        while offset < frames:
            if not len(self._output_pending):
                try:
                    self._output_pending = self._output.get_nowait()
                except queue.Empty:
                    break
            take = min(frames-offset, len(self._output_pending))
            outdata[offset:offset+take] = self._output_pending[:take]
            self._output_pending = self._output_pending[take:]
            offset += take

    def _loopback(self):
        try:
            import soundcard as sc
            speaker = sc.default_speaker()
            microphone = sc.get_microphone(id=speaker.id, include_loopback=True)
            with microphone.recorder(samplerate=self.sample_rate, channels=2, blocksize=self.blocksize) as recorder:
                while not self._stop.is_set():
                    self.feed_external('Система', recorder.record(numframes=self.blocksize))
        except ImportError:
            _offer(self._failures, 'Системный звук недоступен: установи библиотеку soundcard в окружение приложения.')
        except Exception as exc:
            _offer(self._failures, f'Не удалось захватить общий системный звук: {exc}')

    def _run(self, input_device, output_device, system_audio):
        streams = []
        try:
            import sounddevice as sd
            rate = self.sample_rate
            capture = None
            if input_device != -1:
                info = sd.query_devices(input_device, 'input')
                channels = min(2, int(info['max_input_channels']))
                if channels < 1:
                    raise RuntimeError('У выбранного устройства нет входных каналов')
                try:
                    sd.check_input_settings(device=input_device, channels=channels, samplerate=rate, dtype='float32')
                except Exception:
                    rate = int(info.get('default_samplerate', self.sample_rate))
                capture = queue.Queue(maxsize=8)
                def callback(indata, frames, timing, status):
                    if status:
                        _offer(self._failures, f'Сбой захвата микрофона: {status}')
                    _offer(capture, indata.copy())
                stream = sd.InputStream(device=input_device, samplerate=rate, channels=channels,
                                        blocksize=round(self.blocksize*rate/self.sample_rate), dtype='float32', callback=callback)
                streams.append(stream)
                stream.start()
            if output_device is not None:
                stream = sd.OutputStream(device=output_device, samplerate=self.sample_rate, channels=2,
                                         blocksize=self.blocksize, dtype='float32', callback=self._output_callback)
                streams.append(stream)
                stream.start()
            if system_audio:
                self._loop_thread = threading.Thread(target=self._loopback, daemon=True, name='StudioLoopback')
                self._loop_thread.start()
            deadline = time.monotonic()
            while not self._stop.is_set():
                try:
                    raise RuntimeError(self._failures.get_nowait())
                except queue.Empty:
                    pass
                while capture is not None:
                    try:
                        self.feed_external('Микрофон', capture.get_nowait(), rate)
                    except queue.Empty:
                        break
                with self._lock:
                    names = list(self._external)
                tracks = {name: self._pull(name, self.blocksize) for name in names}
                mic = tracks.get('Микрофон')
                config = self._channel('Микрофон')
                speech = mic is not None and not config['muted'] and float(np.sqrt(np.mean(mic*mic))) > .02
                tracks.update(self._sound_blocks(self.blocksize, speech=speech))
                # Keep existing track timelines continuous during silence and pauses.
                with self._lock:
                    channel_names = list(self.channels)
                for name in channel_names:
                    tracks.setdefault(name, np.zeros((self.blocksize, 2), dtype='float32'))
                record, output, individual = self.mix_block(tracks)
                self.audio_ready.emit(record)
                for name, data in individual.items():
                    self.track_ready.emit(name, data)
                if output_device is not None:
                    _offer(self._output, output)
                deadline += self.blocksize/self.sample_rate
                self._stop.wait(max(0, deadline-time.monotonic()))
                if time.monotonic()-deadline > .25:
                    deadline = time.monotonic()
        except Exception as exc:
            self.error_occurred.emit(f'Аудиодвижок остановлен: {exc}')
        finally:
            self._stop.set()
            for stream in reversed(streams):
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            self.running = False

    def stop(self):
        self._stop.set()
        for worker in (self._thread, self._loop_thread):
            if worker and worker is not threading.current_thread():
                worker.join(timeout=3)
        self.running = False
        self.stop_sounds()
