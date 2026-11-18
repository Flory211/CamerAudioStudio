"""Stateful, fixed-duration voice processing for the studio audio worker."""
import math
import numpy as np
from scipy import signal


class AudioEffects:
    DEFAULTS = dict(pitch=1.0, gain=1.0, reverb=0.0, gate_db=-60.0,
                    noise_reduction=0.0, compressor=0.0, limiter=0.95,
                    low_db=0.0, mid_db=0.0, high_db=0.0, robot=0.0,
                    distortion=0.0, telephone=False, wet=1.0)
    ORDER = ['gate', 'noise_reduction', 'equalizer', 'compressor', 'pitch',
             'robot', 'distortion', 'telephone', 'reverb', 'gain']
    RANGES = dict(pitch=(0.5, 2), gain=(0, 20), reverb=(0, 1), gate_db=(-120, 0),
                  noise_reduction=(0, 1), compressor=(0, 1), limiter=(0.01, 1),
                  low_db=(-24, 24), mid_db=(-24, 24), high_db=(-24, 24),
                  robot=(0, 1), distortion=(0, 1), wet=(0, 1))

    def __init__(self):
        self.__dict__.update(self.DEFAULTS)
        self.chain = self.ORDER.copy()
        self._sample_rate = 44100
        self._filters = {}
        self._reverb_state = None
        self._pitch_history = None
        self._pitch_phase = 0
        self._robot_phase = 0

    def set_sample_rate(self, sr):
        if not math.isfinite(float(sr)) or sr < 8000:
            raise ValueError('Частота звука должна быть не ниже 8000 Гц')
        if int(sr) != self._sample_rate:
            self._sample_rate = int(sr)
            self._filters.clear()
            self._reverb_state = self._pitch_history = None

    def to_dict(self):
        return {**{key: getattr(self, key) for key in self.DEFAULTS}, 'chain': list(self.chain)}

    def configure(self, settings):
        updated = {}
        for key, value in settings.items():
            if key == 'chain':
                if not isinstance(value, list) or len(set(value)) != len(value) or any(v not in self.ORDER for v in value):
                    raise ValueError('Неизвестный или повторяющийся эффект в цепочке')
                updated[key] = list(value)
            elif key == 'telephone':
                updated[key] = bool(value)
            elif key in self.RANGES:
                value = float(value)
                low, high = self.RANGES[key]
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f'Параметр {key}: допустимый диапазон {low}…{high}')
                updated[key] = value
        self.__dict__.update(updated)

    def preset(self, name):
        presets = {
            'Чистый': {}, 'clean': {}, 'Без эффектов': {},
            'Робот': dict(robot=1.0, compressor=0.5), 'robot': dict(robot=1.0, compressor=0.5),
            'Телефон': dict(telephone=True, compressor=0.5), 'telephone': dict(telephone=True),
            'Глубокий': dict(pitch=0.75, low_db=4), 'deep': dict(pitch=0.75, low_db=4),
            'Высокий': dict(pitch=1.5), 'high': dict(pitch=1.5),
            'Эхо': dict(reverb=0.5), 'echo': dict(reverb=0.5),
            'Радио': dict(telephone=True, distortion=0.15, compressor=0.6),
        }
        if name not in presets:
            raise ValueError(f'Неизвестный пресет: {name}')
        self.configure({**self.DEFAULTS, 'chain': self.ORDER.copy(), **presets[name]})
        self._filters.clear()
        self._reverb_state = self._pitch_history = None

    def _filter(self, samples, key, sos):
        cached = self._filters.get(key)
        if cached is None or cached[1].shape[2] != samples.shape[1] or not np.array_equal(cached[0], sos):
            cached = (sos, np.zeros((len(sos), 2, samples.shape[1])))
        output, state = signal.sosfilt(sos, samples, axis=0, zi=cached[1])
        self._filters[key] = (sos, state)
        return output

    def _eq(self, samples):
        rate = self._sample_rate
        low = self._filter(samples, 'eq-low', signal.butter(2, 250, fs=rate, output='sos'))
        high = self._filter(samples, 'eq-high', signal.butter(2, min(4000, rate*.4), btype='highpass', fs=rate, output='sos'))
        mid = samples - low - high
        return low * 10**(self.low_db/20) + mid * 10**(self.mid_db/20) + high * 10**(self.high_db/20)

    def _pitch(self, samples):
        # Two overlapping moving delay taps transpose without changing block length.
        size = max(128, int(self._sample_rate * .04))
        history = self._pitch_history
        if history is None or history.shape[1] != samples.shape[1]:
            history = np.zeros((size+3, samples.shape[1]))
        source = np.concatenate((history, samples))
        n = len(samples)
        phase = (self._pitch_phase + np.arange(n) * (1-self.pitch) / size) % 1
        other = (phase+.5) % 1
        indexes = np.arange(n) + len(history)
        def tap(ph):
            position = indexes - (2 + ph*size)
            floor = np.floor(position).astype(int)
            frac = (position-floor)[:, None]
            return source[floor]*(1-frac) + source[floor+1]*frac
        weight = (.5-.5*np.cos(2*np.pi*phase))[:, None]
        result = tap(phase)*weight + tap(other)*(1-weight)
        self._pitch_history = source[-(size+3):].copy()
        self._pitch_phase = (self._pitch_phase + n*(1-self.pitch)/size) % 1
        return result

    def _reverb(self, samples):
        delay = max(1, int(self._sample_rate*.073))
        if self._reverb_state is None or self._reverb_state.shape != (delay, samples.shape[1]):
            self._reverb_state = np.zeros((delay, samples.shape[1]))
        result = samples.copy()
        for start in range(0, len(samples), delay):
            block = samples[start:start+delay]
            count = len(block)
            delayed = self._reverb_state[:count].copy()
            self._reverb_state = np.concatenate((self._reverb_state[count:], block+delayed*.65))
            result[start:start+count] += delayed * self.reverb
        return result

    def apply(self, samples):
        source = np.asarray(samples, dtype=np.float32)
        if source.ndim not in (1, 2):
            raise ValueError('Аудиоблок должен иметь форму (N,) или (N, каналы)')
        if len(source) == 0:
            return source.copy()
        mono = source.ndim == 1
        dry = np.nan_to_num(source[:, None] if mono else source, nan=0., posinf=0., neginf=0.).astype(np.float64)
        result = dry.copy()
        for name in self.chain:
            if name == 'gate' and self.gate_db > -120:
                if np.sqrt(np.mean(result*result)) < 10**(self.gate_db/20):
                    result[:] = 0
            elif name == 'noise_reduction' and self.noise_reduction > 0:
                highpass = self._filter(result, 'noise-highpass', signal.butter(2, 80, 'highpass', fs=self._sample_rate, output='sos'))
                threshold = .015*self.noise_reduction
                reduction = np.maximum(0, 1-threshold/(abs(highpass)+1e-9))
                result = highpass*((1-self.noise_reduction)+self.noise_reduction*reduction)
            elif name == 'equalizer' and (self.low_db or self.mid_db or self.high_db):
                result = self._eq(result)
            elif name == 'compressor' and self.compressor > 0:
                magnitude = abs(result)
                compressed = .2*(magnitude/.2+1e-12)**(1/(1+5*self.compressor))
                result *= np.minimum(1, compressed/(magnitude+1e-12))
            elif name == 'pitch' and self.pitch != 1:
                result = self._pitch(result)
            elif name == 'robot' and self.robot:
                phase = self._robot_phase + np.arange(len(result))*2*np.pi*70/self._sample_rate
                result *= (1-self.robot)+self.robot*np.sin(phase)[:, None]
                self._robot_phase = (phase[-1]+2*np.pi*70/self._sample_rate) % (2*np.pi)
            elif name == 'distortion' and self.distortion:
                result = np.tanh(result*(1+15*self.distortion))/(1+2*self.distortion)
            elif name == 'telephone' and self.telephone:
                sos = signal.butter(3, [300, min(3400, self._sample_rate*.45)], 'bandpass', fs=self._sample_rate, output='sos')
                result = self._filter(result, 'telephone', sos)
            elif name == 'reverb' and self.reverb:
                result = self._reverb(result)
            elif name == 'gain':
                result *= self.gain
        result = dry*(1-self.wet)+result*self.wet
        result = np.clip(np.nan_to_num(result), -self.limiter, self.limiter).astype(np.float32)
        return result[:, 0] if mono else result
