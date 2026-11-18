"""Bounded asynchronous video/WAV sessions and compressed replay history."""
from collections import deque
from datetime import datetime, timezone
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import queue
import re
import threading
import tempfile
import time
import uuid
import wave

import cv2
import numpy as np

from .project import _atomic_json


def _pcm16(samples):
    if np.issubdtype(samples.dtype, np.floating):
        return np.rint(np.nan_to_num(samples, nan=0, posinf=1, neginf=-1).clip(-1, 1) * 32767).astype('<i2')
    return samples.clip(-32768, 32767).astype('<i2')


def _mux_video_audio(video_path, audio_path, output_path, fps):
    """Stream-encode MP4 with chronological video/AAC packets and bounded memory."""
    import av
    output_path = Path(output_path)
    temporary = output_path.with_name(output_path.stem + '.partial.mp4')
    rate = Fraction(str(fps)).limit_denominator(1000)
    try:
        with av.open(str(video_path)) as source, wave.open(str(audio_path), 'rb') as audio, av.open(str(temporary), 'w') as output:
            video = output.add_stream('libx264', rate=rate)
            video.width = source.streams.video[0].width // 2 * 2
            video.height = source.streams.video[0].height // 2 * 2
            video.pix_fmt = 'yuv420p'
            video.options = {'preset': 'veryfast', 'crf': '20'}
            sample_rate, channels = audio.getframerate(), audio.getnchannels()
            layout = 'stereo' if channels == 2 else 'mono'
            sound = output.add_stream('aac', rate=sample_rate)
            sound.layout = layout
            sound.bit_rate = 128000
            decoder = iter(source.decode(video=0))
            frame = next(decoder, None)
            audio_data = audio.readframes(1024)
            video_index, sample_index = 0, 0
            while frame is not None or audio_data:
                if frame is not None and (not audio_data or video_index / float(rate) <= sample_index / sample_rate):
                    converted = frame.reformat(width=video.width, height=video.height, format='yuv420p')
                    converted.pts = video_index
                    converted.time_base = Fraction(rate.denominator, rate.numerator)
                    for packet in video.encode(converted):
                        output.mux(packet)
                    video_index += 1
                    frame = next(decoder, None)
                else:
                    samples = np.frombuffer(audio_data, '<i2').reshape(-1, channels).astype(np.float32) / 32768
                    if channels != 2:
                        samples = samples.mean(axis=1, keepdims=True)
                    converted = av.AudioFrame.from_ndarray(np.ascontiguousarray(samples.T), format='fltp', layout=layout)
                    converted.sample_rate = sample_rate
                    converted.pts = sample_index
                    converted.time_base = Fraction(1, sample_rate)
                    for packet in sound.encode(converted):
                        output.mux(packet)
                    sample_index += len(samples)
                    audio_data = audio.readframes(1024)
            for stream in (video, sound):
                for packet in stream.encode(None):
                    output.mux(packet)
        os.replace(temporary, output_path)
        return output_path
    finally:
        temporary.unlink(missing_ok=True)


def _master_track(directory, audio_paths, sample_rate):
    for name in ('Микс', 'mix', 'master', 'Master'):
        if name in audio_paths:
            return Path(directory) / audio_paths[name]
    if len(audio_paths) == 1:
        return Path(directory) / next(iter(audio_paths.values()))
    inputs = []
    target = Path(directory) / 'master.wav'
    try:
        inputs = [wave.open(str(Path(directory) / path), 'rb') for path in audio_paths.values()]
        with wave.open(str(target), 'wb') as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            while True:
                blocks = []
                for track in inputs:
                    block = np.frombuffer(track.readframes(4096), '<i2').reshape(-1, track.getnchannels()).astype(np.float32)
                    if block.shape[1] != 2:
                        block = np.repeat(block.mean(axis=1, keepdims=True), 2, axis=1)
                    blocks.append(block)
                count = max(len(block) for block in blocks)
                if not count:
                    break
                mixed = np.zeros((count, 2), np.float32)
                for block in blocks:
                    mixed[:len(block)] += block
                output.writeframesraw(np.clip(mixed, -32768, 32767).astype('<i2').tobytes())
        return target
    finally:
        for track in inputs:
            track.close()


def _writer(path, width, height, fps):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    codec = 'mp4v' if path.suffix.lower() == '.mp4' else 'MJPG'
    if path.suffix.lower() not in ('.mp4', '.avi'):
        path = path.with_suffix('.avi')
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), fps, (width, height))
    if not writer.isOpened():
        writer.release()
        raise RuntimeError('Не удалось открыть кодек записи видео')
    return path, writer


class SessionRecorder:
    def __init__(self, directory, width, height, fps, sample_rate=44100):
        self.directory = Path(directory)
        self.width, self.height = int(width), int(height)
        self.fps, self.sample_rate = float(fps), int(sample_rate)
        if not (16 <= self.width <= 8192 and 16 <= self.height <= 8192 and 1 <= self.fps <= 240 and 8000 <= self.sample_rate <= 192000):
            raise ValueError('Недопустимые параметры записи')
        self.recording = False
        self.error = ''
        self._session = None
        self._thread = None
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()

    def start(self):
        with self._lifecycle_lock:
            return self._start_locked()

    def _start_locked(self):
        if self.recording:
            return self._session
        self.directory.mkdir(parents=True, exist_ok=True)
        self._session = self.directory / (datetime.now().strftime('%Y-%m-%d_%H-%M-%S_') + uuid.uuid4().hex[:8])
        self._session.mkdir()
        self._video_path, self._video = _writer(self._session / 'video.avi', self.width, self.height, self.fps)
        self._queue = queue.Queue(maxsize=8)
        self._dropped = 0
        self._frames = 0
        self._discontinuities = 0
        self._origin = None
        self._previous = None
        self._audio = {}
        self._audio_paths = {}
        self._audio_frames = {}
        self._audio_silence = {}
        self._sources = {}
        self._source_paths = {}
        self._clock_origin = time.monotonic()
        self.error = ''
        self._started = datetime.now(timezone.utc).isoformat()
        self.recording = True
        self._thread = threading.Thread(target=self._run, name='studio-recorder', daemon=True)
        self._thread.start()
        return self._session

    def _enqueue(self, item):
        with self._lock:
            if not self.recording:
                return
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                self._dropped += 1

    def write_frame(self, frame, timestamp=None):
        if not self.recording:
            return
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8 or frame.nbytes > 64 * 1024 * 1024:
            raise ValueError('Ожидается кадр BGR uint8')
        timestamp = time.monotonic() if timestamp is None else float(timestamp)
        if not math.isfinite(timestamp):
            raise ValueError('Недопустимая временная метка')
        self._enqueue(('video', timestamp, frame.copy()))

    def write_audio(self, name, samples):
        if not self.recording:
            return
        samples = np.asarray(samples)
        if samples.ndim not in (1, 2) or samples.size == 0 or samples.nbytes > 4 * 1024 * 1024 or (samples.ndim == 2 and not 1 <= samples.shape[1] <= 8):
            raise ValueError('Недопустимый аудиоблок')
        duration = samples.shape[0] / self.sample_rate
        block_start = max(0., time.monotonic() - self._clock_origin - duration)
        self._enqueue(('audio', str(name)[:200], samples.copy(), block_start))

    def write_source_frame(self, name, frame, timestamp=None):
        if not self.recording:
            return
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8 or frame.nbytes > 64 * 1024 * 1024:
            raise ValueError('Ожидается исходный кадр BGR uint8')
        timestamp = time.monotonic() if timestamp is None else float(timestamp)
        if not math.isfinite(timestamp):
            raise ValueError('Недопустимая временная метка')
        self._enqueue(('source', str(name)[:200], frame.copy(), timestamp))

    def _run(self):
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                if item[0] == 'video':
                    self._write_video(item[1], item[2])
                elif item[0] == 'audio':
                    self._write_audio(item[1], item[2], item[3])
                else:
                    self._write_source(item[1], item[2], item[3])
        except Exception as error:
            self.error = f'Ошибка записи: {error}'
        finally:
            self._video.release()
            for output in self._audio.values():
                output.close()
            for track in self._sources.values():
                track['writer'].release()

    def _write_video(self, timestamp, frame):
        if self._origin is None:
            self._origin = self._clock_origin if abs(timestamp - self._clock_origin) < 10 else timestamp
        index = max(0, round((timestamp - self._origin) * self.fps))
        if index < self._frames:
            return
        if index - self._frames > self.fps * 10:
            self._discontinuities += 1
            self._origin += (index - self._frames) / self.fps
            index = self._frames
        if frame.shape[:2] != (self.height, self.width):
            frame = cv2.resize(frame, (self.width, self.height))
        while self._frames < index:
            self._video.write(self._previous if self._previous is not None else frame)
            self._frames += 1
        self._video.write(frame)
        self._frames += 1
        self._previous = frame

    def _write_source(self, name, frame, timestamp):
        if name not in self._sources:
            height, width = frame.shape[:2]
            width, height = max(2, width // 2 * 2), max(2, height // 2 * 2)
            path, writer = _writer(self._session / f'source_{len(self._sources):02d}.avi', width, height, self.fps)
            origin = self._clock_origin if abs(timestamp - self._clock_origin) < 10 else (self._origin if self._origin is not None else timestamp)
            self._sources[name] = dict(writer=writer, width=width, height=height, origin=origin, count=0, previous=None)
            self._source_paths[name] = path.name
        track = self._sources[name]
        index = max(0, round((timestamp - track['origin']) * self.fps))
        if index < track['count']:
            return
        if index - track['count'] > self.fps * 10:
            track['origin'] += (index - track['count']) / self.fps
            index = track['count']
        frame = cv2.resize(frame, (track['width'], track['height']))
        while track['count'] < index:
            blank = track['previous'] if track['previous'] is not None else np.zeros_like(frame)
            track['writer'].write(blank)
            track['count'] += 1
        track['writer'].write(frame)
        track['count'] += 1
        track['previous'] = frame

    def _write_audio(self, name, samples, block_start):
        channels = 1 if samples.ndim == 1 else samples.shape[1]
        if name not in self._audio:
            safe = re.sub(r'[^\w.-]', '_', name, flags=re.UNICODE).strip('.')[:80] or 'audio'
            filename = f'{len(self._audio):02d}_{safe}.wav'
            output = wave.open(str(self._session / filename), 'wb')
            output.setnchannels(channels)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate)
            self._audio[name] = output
            self._audio_paths[name] = filename
            self._audio_frames[name] = 0
            self._audio_silence[name] = 0
        output = self._audio[name]
        if output.getnchannels() != channels:
            raise ValueError(f'Число каналов дорожки «{name}» изменилось')
        desired_frame = round(block_start * self.sample_rate)
        missing = desired_frame - self._audio_frames[name]
        if missing > self.sample_rate * .03:
            # Write small chunks even for a channel first enabled hours into a session.
            zeros = bytes(min(missing, self.sample_rate) * channels * 2)
            remaining = missing
            while remaining:
                count = min(remaining, self.sample_rate)
                output.writeframesraw(zeros[:count * channels * 2])
                remaining -= count
            self._audio_frames[name] += missing
            self._audio_silence[name] += missing
        pcm = _pcm16(samples)
        output.writeframesraw(pcm.tobytes())
        self._audio_frames[name] += samples.shape[0]

    def stop(self):
        with self._lifecycle_lock:
            return self._stop_locked()

    def _stop_locked(self):
        with self._lock:
            if not self.recording:
                return self._session
            self.recording = False
        while self._thread.is_alive():
            try:
                self._queue.put(None, timeout=.1)
                break
            except queue.Full:
                continue
        self._thread.join()
        final_video = self._video_path
        mux_error = ''
        has_audio = False
        if self._audio_paths and self._frames:
            try:
                master = _master_track(self._session, self._audio_paths, self.sample_rate)
                final_video = _mux_video_audio(self._video_path, master, self._session / 'recording.mp4', self.fps)
                has_audio = True
            except Exception as error:
                mux_error = f'MP4 со звуком не создан: {error}. Видео AVI и WAV сохранены отдельно.'
        _atomic_json(self._session / 'manifest.json', dict(
            version=1, started=self._started, video=final_video.name, video_has_audio=has_audio,
            raw_video=self._video_path.name, video_tracks=self._source_paths, mux_error=mux_error,
            audio=self._audio_paths, width=self.width, height=self.height, fps=self.fps,
            audio_frames=self._audio_frames, audio_silence_frames=self._audio_silence,
            sample_rate=self.sample_rate, frames=self._frames, dropped_blocks=self._dropped,
            timestamp_discontinuities=self._discontinuities, error=self.error,
            note='MP4 со звуком и отдельные исходные дорожки.' if has_audio else 'Видео AVI и отдельные WAV-дорожки; звук не встроен в видео.'))
        return self._session


class ReplayBuffer:
    def __init__(self, seconds=20, max_bytes=128 * 1024 * 1024, fps=20, sample_rate=44100):
        self.seconds, self.max_bytes, self.fps = float(seconds), int(max_bytes), float(fps)
        self.sample_rate = int(sample_rate)
        if not (0 < self.seconds <= 3600 and self.max_bytes > 0 and 1 <= self.fps <= 240):
            raise ValueError('Недопустимые параметры буфера повторов')
        self._frames = deque()
        self._audio = deque()
        self._latest_time = None
        self._bytes = 0
        self._lock = threading.Lock()

    @property
    def bytes_used(self):
        with self._lock:
            return self._bytes

    def append(self, frame, timestamp=None):
        timestamp = time.monotonic() if timestamp is None else float(timestamp)
        if not math.isfinite(timestamp):
            raise ValueError('Недопустимая временная метка')
        success, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not success:
            return
        data = encoded.tobytes()
        with self._lock:
            if self._frames and timestamp < self._frames[-1][0]:
                self._frames.clear()
                self._audio.clear()
                self._bytes = 0
                self._latest_time = None
            self._frames.append((timestamp, data))
            self._bytes += len(data)
            self._latest_time = max(timestamp, self._latest_time or timestamp)
            self._prune()

    def append_audio(self, samples, timestamp=None):
        samples = np.asarray(samples)
        if samples.ndim not in (1, 2) or not samples.size or samples.nbytes > 4 * 1024 * 1024:
            raise ValueError('Недопустимый аудиоблок повтора')
        channels = 1 if samples.ndim == 1 else samples.shape[1]
        if not 1 <= channels <= 8:
            raise ValueError('Недопустимое число аудиоканалов')
        duration = samples.shape[0] / self.sample_rate
        timestamp = time.monotonic() - duration if timestamp is None else float(timestamp)
        if not math.isfinite(timestamp):
            raise ValueError('Недопустимая временная метка')
        data = _pcm16(samples).tobytes()
        with self._lock:
            self._audio.append((timestamp, data, channels))
            self._bytes += len(data)
            self._latest_time = max(timestamp + duration, self._latest_time or timestamp)
            self._prune()

    def _prune(self):
        cutoff = self._latest_time - self.seconds
        while self._frames and self._frames[0][0] < cutoff:
            self._bytes -= len(self._frames.popleft()[1])
        while self._audio:
            timestamp, data, channels = self._audio[0]
            trim = max(0, min(len(data), round((cutoff - timestamp) * self.sample_rate) * channels * 2))
            if trim == 0:
                break
            self._bytes -= trim
            self._audio.popleft()
            if trim < len(data):
                self._audio.appendleft((timestamp + trim / (self.sample_rate * channels * 2), data[trim:], channels))
                break
        while self._bytes > self.max_bytes:
            if self._frames and (not self._audio or self._frames[0][0] <= self._audio[0][0]):
                self._bytes -= len(self._frames.popleft()[1])
            elif self._audio:
                self._bytes -= len(self._audio.popleft()[1])
            else:
                break

    def clear(self):
        with self._lock:
            self._frames.clear()
            self._audio.clear()
            self._bytes = 0
            self._latest_time = None

    def save(self, path, fps=None):
        with self._lock:
            frames = list(self._frames)
            audio = list(self._audio)
        if not frames:
            raise ValueError('Буфер повторов пуст')
        fps = self.fps if fps is None else float(fps)
        if not 1 <= fps <= 240:
            raise ValueError('Недопустимая частота кадров')
        first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
        height, width = first.shape[:2]
        requested = Path(path)
        wants_audio = bool(audio) and requested.suffix.lower() == '.mp4'
        intermediate = None
        if wants_audio:
            requested.parent.mkdir(parents=True, exist_ok=True)
            handle, intermediate = tempfile.mkstemp(prefix=requested.stem + '_video_', suffix='.avi', dir=requested.parent)
            os.close(handle)
        path, writer = _writer(intermediate if intermediate is not None else requested, width, height, fps)
        try:
            cursor = 0
            for index in range(max(1, round((frames[-1][0] - frames[0][0]) * fps) + 1)):
                target_time = frames[0][0] + index / fps
                while cursor + 1 < len(frames) and frames[cursor + 1][0] <= target_time:
                    cursor += 1
                frame = cv2.imdecode(np.frombuffer(frames[cursor][1], np.uint8), cv2.IMREAD_COLOR)
                if frame.shape[:2] != (height, width):
                    frame = cv2.resize(frame, (width, height))
                writer.write(frame)
        finally:
            writer.release()
        mux_error = ''
        has_audio = False
        if wants_audio:
            try:
                with tempfile.TemporaryDirectory(prefix='studio-replay-') as folder:
                    audio_path = Path(folder) / 'audio.wav'
                    self._write_replay_audio(audio_path, audio, frames[0][0], frames[-1][0] + 1 / fps)
                    path = _mux_video_audio(path, audio_path, requested, fps)
                    has_audio = True
                    Path(intermediate).unlink(missing_ok=True)
            except Exception as error:
                mux_error = f'Звук не встроен: {error}'
        _atomic_json(path.with_suffix(path.suffix + '.json'), dict(video=path.name, video_has_audio=has_audio, fps=fps, mux_error=mux_error))
        return path

    def _write_replay_audio(self, path, blocks, start, end):
        channels = 2 if blocks[0][2] == 2 else 1
        total = max(1, round((end - start) * self.sample_rate))
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(channels)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate)
            written = 0
            silence = bytes(self.sample_rate * channels * 2)
            for timestamp, data, count in sorted(blocks):
                samples = np.frombuffer(data, '<i2').reshape(-1, count)
                if count != channels:
                    samples = samples.mean(axis=1, keepdims=True).astype('<i2')
                    if channels == 2:
                        samples = np.repeat(samples, 2, axis=1)
                offset = round((timestamp - start) * self.sample_rate)
                skip = max(0, written - offset)
                offset = max(written, offset)
                if offset >= total:
                    break
                while written < offset:
                    chunk = min(offset - written, self.sample_rate)
                    output.writeframesraw(silence[:chunk * channels * 2])
                    written += chunk
                samples = samples[skip:skip + max(0, total - written)]
                output.writeframesraw(samples.astype('<i2').tobytes())
                written += len(samples)
            while written < total:
                chunk = min(total - written, self.sample_rate)
                output.writeframesraw(silence[:chunk * channels * 2])
                written += chunk
