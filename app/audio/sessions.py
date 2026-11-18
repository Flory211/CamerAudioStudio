"""Windows session volume/mute controls, distinct from whole-device loopback."""
import math
from contextlib import contextmanager


@contextmanager
def _sessions():
    try:
        from pycaw.pycaw import AudioUtilities
        import comtypes
    except ImportError as exc:
        raise RuntimeError('Windows-аудиосессии недоступны: установи pycaw и comtypes в окружение приложения.') from exc
    initialized = False
    try:
        comtypes.CoInitialize()
        initialized = True
        yield AudioUtilities.GetAllSessions()
    except OSError as exc:
        raise RuntimeError(f'Не удалось получить Windows-аудиосессии: {exc}') from exc
    finally:
        if initialized:
            comtypes.CoUninitialize()


def list_sessions():
    result = []
    with _sessions() as sessions:
        for session in sessions:
            try:
                process = session.Process
                name = session.DisplayName or (process.name() if process else 'Системные звуки')
                volume = session.SimpleAudioVolume
                result.append(dict(id=session.InstanceIdentifier, name=name,
                                   volume=float(volume.GetMasterVolume()), muted=bool(volume.GetMute())))
            except (OSError, RuntimeError):
                # Processes may exit while Windows enumerates sessions.
                continue
    return result


def set_session_volume(session_id, volume):
    volume = float(volume)
    if not math.isfinite(volume) or not 0 <= volume <= 1:
        raise ValueError('Громкость Windows-сессии должна быть от 0 до 1')
    with _sessions() as sessions:
        for session in sessions:
            if session.InstanceIdentifier == session_id:
                session.SimpleAudioVolume.SetMasterVolume(volume, None)
                return
    raise ValueError('Windows-аудиосессия уже закрыта или не найдена')


def set_session_mute(session_id, muted):
    with _sessions() as sessions:
        for session in sessions:
            if session.InstanceIdentifier == session_id:
                session.SimpleAudioVolume.SetMute(bool(muted), None)
                return
    raise ValueError('Windows-аудиосессия уже закрыта или не найдена')
