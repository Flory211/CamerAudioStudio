"""Capture and rendering lifecycle, independent from the studio widgets."""
import copy
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal

from app.studio.compositor import compose, _read_image
from app.studio.recording import ReplayBuffer, SessionRecorder
from app.studio.vision import VisionProcessor
from app.video.effects import VideoEffects
from app.video.output import VirtualCameraOutput


def _local_image(source):
    try:
        path = Path(source.get('path', ''))
        return _read_image(str(path), path.stat().st_mtime_ns)
    except (OSError, ValueError, cv2.error):
        return None


def _source_effects(source, frame, effects, selected_masks):
    sid = source['id']
    fx = effects.get(sid)
    if fx is None:
        fx = effects[sid] = VideoEffects()
    fx.reset()
    for key, value in source.get('effects', {}).items():
        if key in ('brightness', 'contrast', 'saturation', 'gamma', 'temperature',
                   'blur', 'sharpen', 'vignette', 'pixelate', 'sepia', 'grayscale',
                   'invert', 'mirror_h', 'mirror_v'):
            setattr(fx, key, value)
    mask = source.get('mask') or 'none'
    if selected_masks.get(sid) != mask:
        fx.face_mask.set_mask(mask)
        selected_masks[sid] = mask
    fx.face_mask.manual_mode = not source.get('mask_tracking', True)
    if not source.get('mask_tracking', True):
        fx.face_mask.mask_x = float(source.get('mask_x', .5)) * frame.shape[1]
        fx.face_mask.mask_y = float(source.get('mask_y', .4)) * frame.shape[0]
        fx.face_mask.set_size(int(float(source.get('mask_size', .25)) * frame.shape[1]))
    if frame.ndim == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    alpha = frame[:, :, 3].copy() if frame.shape[2] == 4 else None
    processed = fx.apply(frame[:, :, :3].copy())
    if alpha is not None:
        if source.get('effects', {}).get('mirror_h'):
            alpha = cv2.flip(alpha, 1)
        if source.get('effects', {}).get('mirror_v'):
            alpha = cv2.flip(alpha, 0)
        processed = np.dstack((processed, alpha))
    return processed


class CaptureWorker(threading.Thread):
    def __init__(self, source, fps, size, report):
        super().__init__(daemon=True, name=f"capture-{source['id'][:8]}")
        self.source = copy.deepcopy(source)
        self.fps, self.size, self.report = fps, size, report
        self.stopped = threading.Event()
        self.frame = None

    def run(self):
        source, cap = self.source, None
        try:
            if source['kind'] in ('camera', 'network'):
                device = source.get('device', 0) if source['kind'] == 'camera' else source.get('path', '')
                if source['kind'] == 'camera':
                    cap = cv2.VideoCapture(int(device), cv2.CAP_DSHOW)
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
                    cap.set(cv2.CAP_PROP_FPS, self.fps)
                else:
                    cap = cv2.VideoCapture(device, cv2.CAP_FFMPEG, [
                        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000,
                        cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000])
                if not cap.isOpened():
                    raise RuntimeError('Не удалось открыть источник. Проверь устройство или адрес потока.')
                while not self.stopped.is_set():
                    start = time.monotonic()
                    ok, frame = cap.read()
                    if not ok:
                        raise RuntimeError('Источник перестал передавать изображение.')
                    self.frame = frame
                    self.stopped.wait(max(0, 1 / self.fps - (time.monotonic() - start)))
            else:
                import mss
                import pygetwindow
                with mss.mss() as capture:
                    while not self.stopped.is_set():
                        start = time.monotonic()
                        if source['kind'] == 'window':
                            windows = pygetwindow.getWindowsWithTitle(str(source.get('device', '')))
                            if not windows:
                                raise RuntimeError('Выбранное окно закрыто или недоступно.')
                            window = windows[0]
                            if window.isMinimized:
                                self.stopped.wait(0.2)
                                continue
                            region = dict(left=window.left, top=window.top,
                                          width=max(1, window.width), height=max(1, window.height))
                        else:
                            monitors = capture.monitors[1:]
                            if not monitors:
                                raise RuntimeError('Мониторы не обнаружены.')
                            region = monitors[min(max(0, int(source.get('device', 0))), len(monitors) - 1)]
                        self.frame = np.asarray(capture.grab(region))[:, :, :3].copy()
                        self.stopped.wait(max(0, 1 / self.fps - (time.monotonic() - start)))
        except Exception as exc:
            self.report(f"{source['name']}: {exc}")
        finally:
            if cap is not None:
                cap.release()

    def stop(self):
        self.stopped.set()


class RenderThread(QThread):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def run(self):
        engine = self.engine
        started = time.monotonic()
        last_stat, frames_done = started, 0
        dropped = 0
        active_scene_id, scene_started = None, started
        effects, visions, selected_masks = {}, {}, {}
        last_error = ''
        try:
            while not self.isInterruptionRequested():
                begin = time.monotonic()
                with engine.lock:
                    data = engine.snapshot
                    sources = dict(engine.captures)
                    server = engine.server
                    recorder = engine.recorder
                    virtual_output = engine.virtual_output
                    caption = engine.caption_text
                    plugins = list(engine.extensions)
                scene = copy.deepcopy(data['scene'])
                if scene['id'] != active_scene_id:
                    active_scene_id, scene_started = scene['id'], begin
                frames = {}
                if server is not None and server.running:
                    guests = server.guest_frames()
                else:
                    guests = {}
                active_ids = {s['id'] for s in scene['sources'] if s.get('visible', True)}
                for sid in list(visions):
                    if sid not in active_ids:
                        visions.pop(sid).close()
                for sid in list(effects):
                    if sid not in active_ids:
                        effects.pop(sid)
                        selected_masks.pop(sid, None)
                for source in scene['sources']:
                    if not source.get('visible', True):
                        continue
                    sid = source['id']
                    if source['kind'] == 'guest':
                        guest_id = source.get('device', '')
                        frame = guests.get(str(guest_id))
                        if frame is None and guest_id in ('', None, 0) and guests:
                            frame = next(iter(guests.values()))
                    else:
                        worker = sources.get(sid)
                        frame = worker.frame if worker is not None else None
                        if frame is None and source['kind'] == 'image':
                            frame = _local_image(source)
                    if frame is None:
                        continue
                    if recorder is not None and recorder.recording and data.get('record_sources', True) and source['kind'] in ('camera', 'screen', 'window', 'network', 'guest'):
                        recorder.write_source_frame(sid, frame, timestamp=begin)
                    frame = _source_effects(source, frame, effects, selected_masks)
                    if source['kind'] in ('camera', 'guest', 'network'):
                        vision = visions.get(sid)
                        if vision is None:
                            vision = visions[sid] = VisionProcessor()
                        vision.configure(dict(data.get('vision', {}), allow_mediapipe_metrics=engine.allow_mediapipe_metrics))
                        frame = vision.apply(frame)
                    frames[sid] = frame
                size = (data['width'], data['height'])
                quality = engine.quality
                render_size = (max(16, int(size[0] * quality)), max(16, int(size[1] * quality)))
                if caption:
                    scene['sources'].append(dict(id='live-captions', kind='text', name='Субтитры',
                        text=caption, x=.07, y=.84, width=.86, height=.12, visible=True,
                        opacity=1, color='#ffffff', font_size=34, background='#151922'))
                if quality != 1:
                    for source in scene['sources']:
                        if source['kind'] == 'text' and 'font_size' in source:
                            source['font_size'] = max(1, round(source['font_size'] * quality))
                            if 'outline' in source:
                                source['outline'] = max(0, round(source['outline'] * quality))
                frame = compose(scene, frames, render_size, begin - scene_started)
                for plugin in plugins:
                    frame = plugin.apply(frame)
                if render_size != size:
                    frame = cv2.resize(frame, size)
                engine.latest_frame = frame
                engine.replay.append(frame, timestamp=begin)
                if recorder is not None and recorder.recording:
                    recorder.write_frame(frame, timestamp=begin)
                if virtual_output is not None:
                    virtual_output.send_frame(frame)
                elapsed = time.monotonic() - begin
                frames_done += 1
                dropped += max(0, int(elapsed * data['fps']) - 1)
                if begin - last_stat >= 1:
                    engine.stats = dict(fps=round(frames_done / (begin - last_stat), 1),
                        render_ms=round(elapsed * 1000, 1),
                        dropped=dropped,
                        sources=len(sources), quality=round(quality * 100), vision='; '.join(dict.fromkeys(v.status for v in visions.values())),
                        replay_mb=round(engine.replay.bytes_used / 1048576, 1))
                    if engine.process_monitor is not None:
                        try:
                            import psutil
                            engine.stats.update(cpu_percent=round(engine.process_monitor.cpu_percent() / max(1, psutil.cpu_count() or 1), 1),
                                                memory_mb=round(engine.process_monitor.memory_info().rss / 1048576, 1))
                        except (OSError, psutil.Error):
                            pass
                    if engine.auto_quality:
                        if elapsed > 1.15 / data['fps']:
                            engine.quality = max(.4, quality - .1)
                        elif elapsed < .5 / data['fps']:
                            engine.quality = min(1, quality + .05)
                    frames_done, last_stat = 0, begin
                wait = max(0, 1 / data['fps'] - elapsed)
                if wait:
                    self.msleep(int(wait * 1000))
        except Exception as exc:
            last_error = f'Обработка видео: {exc}'
            engine.error_occurred.emit(last_error)
        finally:
            for vision in visions.values():
                vision.close()
            engine._render_finished(self, bool(last_error))


class StudioEngine(QObject):
    error_occurred = pyqtSignal(str)

    def __init__(self, project):
        super().__init__()
        self.project = project
        self.lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self.running = False
        self.captures = {}
        self.worker = None
        self.virtual_output = None
        self._retired_outputs = []
        self.recorder = None
        self.server = None
        self.caption_text = ''
        self.extensions = []
        self.latest_frame = None
        self._static_effects, self._static_masks = {}, {}
        self.process_monitor = None
        try:
            import psutil
            self.process_monitor = psutil.Process()
            self.process_monitor.cpu_percent()
        except (ImportError, OSError):
            pass
        self.replay = ReplayBuffer()
        self.stats = dict(fps=0, render_ms=0, dropped=0, sources=0, quality=100, vision='', replay_mb=0)
        self.auto_quality = False
        self.allow_mediapipe_metrics = False
        self.quality = 1.0
        self.update_project(project)

    def update_project(self, project):
        self.project = project
        with self.lock:
            self.snapshot = dict(width=int(project.data['width']), height=int(project.data['height']),
                fps=int(project.data['fps']), scene=copy.deepcopy(project.active_scene),
                vision=copy.deepcopy(project.data.get('vision', {})),
                record_sources=bool(project.data.get('record_sources', True)))
        if self.running:
            self.sync_sources()
        else:
            frames = {}
            image_ids = set()
            for source in project.active_scene['sources']:
                if source['kind'] == 'image' and source.get('visible', True):
                    frame = _local_image(source)
                    if frame is not None:
                        image_ids.add(source['id'])
                        frames[source['id']] = _source_effects(source, frame, self._static_effects, self._static_masks)
            self._static_effects = {key: value for key, value in self._static_effects.items() if key in image_ids}
            self._static_masks = {key: value for key, value in self._static_masks.items() if key in image_ids}
            self.latest_frame = compose(project.active_scene, frames,
                (project.data['width'], project.data['height']))

    def sync_sources(self):
        desired = {s['id']: s for s in self.project.active_scene['sources']
                   if s['kind'] in ('camera', 'screen', 'window', 'network') and s.get('visible', True)}
        with self.lock:
            if not self.running:
                return
            size = self.snapshot['width'], self.snapshot['height']
            fps = self.snapshot['fps']
            previous = set(self.captures.values())
            def key(source):
                return source['kind'], str(source.get('device', '')), source.get('path', '')
            existing = {key(w.source): w for w in previous if w.size == size and w.fps == fps
                        and not w.stopped.is_set() and w.is_alive()}
            captures = {}
            for sid, source in desired.items():
                worker = existing.get(key(source))
                if worker is None:
                    worker = CaptureWorker(source, fps, size, self.error_occurred.emit)
                    worker.start()
                    existing[key(source)] = worker
                captures[sid] = worker
            self.captures = captures
            for worker in previous - set(captures.values()):
                worker.stop()

    def start(self):
        with self._lifecycle_lock:
            if self.worker is not None and self.worker.isRunning():
                return
            self.running = True
            self.replay.clear()
            self.update_project(self.project)
            self.worker = RenderThread(self)
            self.worker.start()

    def stop(self):
        with self._lifecycle_lock:
            if self.worker:
                self.worker.requestInterruption()
                if not self.worker.wait(10000):
                    self.error_occurred.emit('Обработка завершает текущий кадр. Повтори остановку позже.')
                    return False
                self.worker = None
            self.running = False
            self._cleanup_outputs()
            return True

    def _render_finished(self, worker, failed):
        with self.lock:
            if self.worker is not None and self.worker is not worker:
                return
            self.running = False
        if failed:
            self._cleanup_outputs()

    def _cleanup_outputs(self):
        with self.lock:
            for worker in set(self.captures.values()):
                worker.stop()
            self.captures.clear()
        self.stop_virtual_camera()
        self.stop_recording()

    def start_virtual_camera(self, backend='obs'):
        with self._lifecycle_lock:
            with self.lock:
                if self.virtual_output is not None:
                    return
            self.start()
            output = VirtualCameraOutput(self.project.data['width'],
                self.project.data['height'], self.project.data['fps'], backend)
            output._studio_cancelled = False
            # The existing output opens its driver asynchronously. A direct startup
            # callback honours a stop requested while that driver was opening.
            output.started.connect(lambda: self._virtual_started(output), Qt.ConnectionType.DirectConnection)
            output.error_occurred.connect(lambda message: self._virtual_error(message, output))
            with self.lock:
                self.virtual_output = output
            output.start()

    def _virtual_started(self, output):
        if output._studio_cancelled:
            output._running = False

    def _virtual_error(self, message, output=None):
        if output is None or self.virtual_output is output:
            self.stop_virtual_camera()
        self.error_occurred.emit(message)

    def stop_virtual_camera(self):
        with self.lock:
            output, self.virtual_output = self.virtual_output, None
        if output:
            output._studio_cancelled = True
            output.stop()
            if output.isRunning():
                self._retired_outputs.append(output)
        self._retired_outputs = [item for item in self._retired_outputs if item.isRunning()]

    def start_recording(self, directory):
        with self._lifecycle_lock:
            with self.lock:
                if self.recorder is not None:
                    return self.recorder.start()
            self.start()
            recorder = SessionRecorder(directory, self.project.data['width'],
                self.project.data['height'], self.project.data['fps'])
            path = recorder.start()
            with self.lock:
                if self.running:
                    self.recorder = recorder
                    return path
            recorder.stop()
            raise RuntimeError('Обработка видео завершилась до запуска записи')

    def record_audio(self, name, samples):
        with self.lock:
            recorder = self.recorder
        if recorder is not None and recorder.recording:
            recorder.write_audio(name, samples)

    def stop_recording(self):
        with self.lock:
            recorder, self.recorder = self.recorder, None
        return recorder.stop() if recorder else None

    def close(self):
        if not self.stop():
            return False
        with self.lock:
            extensions, self.extensions = self.extensions, []
        for plugin in extensions:
            plugin.close()
        return True
