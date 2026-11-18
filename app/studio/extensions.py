"""Explicitly enabled local effects, isolated in a bounded spawned worker.

Process isolation limits crashes and latency. It is not an OS security sandbox:
an explicitly selected Python extension has the user's filesystem permissions.
"""
import importlib.util
import json
import multiprocessing as mp
import threading
from multiprocessing import shared_memory
from pathlib import Path

import numpy as np

MAX_FRAME_BYTES = 24 * 1024 * 1024


def _worker(path, connection, shared_name):
    memory = shared_memory.SharedMemory(name=shared_name)
    try:
        spec = importlib.util.spec_from_file_location('studio_user_extension', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        function = getattr(module, 'process_frame')
        if not callable(function):
            raise TypeError('process_frame должна быть функцией')
        connection.send_bytes(b'ready')
        while True:
            request = connection.recv_bytes(4096)
            if request == b'close':
                break
            shape = tuple(json.loads(request)['shape'])
            if len(shape) != 3 or shape[2] != 3 or any(not isinstance(n, int) or n < 1 for n in shape) or np.prod(shape) > MAX_FRAME_BYTES:
                raise ValueError('Недопустимый размер кадра')
            frame = np.ndarray(shape, np.uint8, buffer=memory.buf).copy()
            result = function(frame)
            if not isinstance(result, np.ndarray) or result.shape != shape or result.dtype != np.uint8:
                raise ValueError('Расширение должно вернуть BGR uint8 того же размера')
            np.ndarray(shape, np.uint8, buffer=memory.buf)[:] = result
            connection.send_bytes(b'ok')
    except (EOFError, BrokenPipeError):
        pass
    except BaseException as error:
        try:
            connection.send_bytes(('error:' + str(error)[:1000]).encode('utf-8', errors='replace'))
        except (OSError, EOFError):
            pass
    finally:
        memory.close()
        connection.close()


class ExtensionHost:
    def __init__(self, path, timeout=.2):
        self._lock = threading.RLock()
        self.error = ''
        self.timeout = max(.01, min(30, float(timeout)))
        self._process = None
        self._connection = None
        self._memory = None
        path = Path(path).resolve()
        if not path.is_file() or path.suffix.lower() != '.py':
            self.error = 'Выбери существующий Python-файл расширения'
            return
        try:
            context = mp.get_context('spawn')
            parent, child = context.Pipe(duplex=True)
            self._connection = parent
            self._memory = shared_memory.SharedMemory(create=True, size=MAX_FRAME_BYTES)
            self._process = context.Process(target=_worker, args=(str(path), child, self._memory.name), daemon=True)
            self._process.start()
            child.close()
            if not parent.poll(max(3, self.timeout)) or parent.recv_bytes(4096) != b'ready':
                raise RuntimeError('Расширение не запустилось за отведённое время')
        except Exception as error:
            self.error = str(error)
            self.close()

    def apply(self, frame):
        with self._lock:
            return self._apply_locked(frame)

    def _apply_locked(self, frame):
        if self.error or self._process is None:
            return frame
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8 or frame.nbytes > MAX_FRAME_BYTES:
            self.error = 'Недопустимый или слишком большой кадр расширения'
            self.close()
            return frame
        try:
            np.ndarray(frame.shape, np.uint8, buffer=self._memory.buf)[:] = frame
            self._connection.send_bytes(json.dumps({'shape': frame.shape}).encode())
            if not self._connection.poll(self.timeout):
                raise TimeoutError('Расширение превысило лимит времени')
            response = self._connection.recv_bytes(4096)
            if response != b'ok':
                raise RuntimeError(response.decode('utf-8', errors='replace'))
            return np.ndarray(frame.shape, np.uint8, buffer=self._memory.buf).copy()
        except Exception as error:
            self.error = str(error) or 'Процесс расширения завершился'
            self.close()
            return frame

    def close(self):
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        if self._process is not None:
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=.5)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=.5)
            self._process.close()
            self._process = None
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._memory is not None:
            self._memory.close()
            self._memory.unlink()
            self._memory = None
