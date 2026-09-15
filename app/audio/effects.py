import numpy as np
from scipy import signal as sig


class AudioEffects:
    def __init__(self):
        self.pitch = 1.0
        self.gain = 1.0
        self.reverb = 0.0
        self._sample_rate = 44100
        self._reverb_ir = None
        self._reverb_cached_amount = -1.0

    def set_sample_rate(self, sr):
        self._sample_rate = sr
        self._reverb_cached_amount = -1.0

    def _build_reverb_ir(self, amount):
        duration = 0.3 + amount * 1.7  # 0.3s to 2.0s
        decay = 2.0 + amount * 6.0
        n_samples = int(self._sample_rate * duration)
        t = np.arange(n_samples) / self._sample_rate
        ir = np.random.randn(n_samples) * np.exp(-decay * t)
        for ch in range(1):
            pass
        ir = ir / (np.max(np.abs(ir)) + 1e-9)
        ir[0] = 1.0
        return ir

    def apply(self, samples):
        result = samples.copy()

        # Gain
        if self.gain != 1.0:
            result = result * self.gain

        # Pitch shift via resampling
        if self.pitch != 1.0:
            n_in = result.shape[0]
            n_out = int(n_in / self.pitch)
            if n_out < 1:
                n_out = 1
            if result.ndim == 1:
                result = sig.resample(result, n_out)
            else:
                resampled_channels = []
                for ch in range(result.shape[1]):
                    resampled_channels.append(sig.resample(result[:, ch], n_out))
                result = np.column_stack(resampled_channels)

        # Reverb via convolution with impulse response
        if self.reverb > 0.01:
            if abs(self.reverb - self._reverb_cached_amount) > 0.01 or self._reverb_ir is None:
                self._reverb_ir = self._build_reverb_ir(self.reverb)
                self._reverb_cached_amount = self.reverb

            if result.ndim == 1:
                wet = sig.fftconvolve(result, self._reverb_ir * self.reverb, mode="full")[:len(result)]
                result = result + wet
            else:
                for ch in range(result.shape[1]):
                    wet = sig.fftconvolve(result[:, ch], self._reverb_ir * self.reverb, mode="full")[:len(result)]
                    result[:, ch] = result[:, ch] + wet

        # Soft clip to prevent harsh distortion
        result = np.tanh(result)

        return result.astype(np.float32)
