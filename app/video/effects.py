import cv2
import numpy as np


class VideoEffects:
    def __init__(self):
        # Basic
        self.brightness = 0
        self.contrast = 0
        self.saturation = 0
        self.gamma = 1.0
        # Filters
        self.blur = 0
        self.sharpen = 0
        self.vignette = 0
        self.pixelate = 0
        # Color
        self.sepia = 0
        self.grayscale = 0
        self.invert = 0
        self.temperature = 0
        # Transform
        self.mirror_h = False
        self.mirror_v = False
        # Cache
        self._gamma_lut = None
        self._gamma_cached = -1.0
        self._vignette_mask = None
        self._vignette_size = (0, 0)

    def _get_gamma_lut(self):
        if self.gamma != self._gamma_cached:
            inv_gamma = 1.0 / self.gamma
            self._gamma_lut = np.array(
                [((i / 255.0) ** inv_gamma) * 255 for i in range(256)],
                dtype=np.uint8,
            )
            self._gamma_cached = self.gamma
        return self._gamma_lut

    def _get_vignette_mask(self, h, w):
        if self._vignette_size != (w, h) or self._vignette_mask is None:
            x = np.linspace(-1, 1, w)
            y = np.linspace(-1, 1, h)
            xx, yy = np.meshgrid(x, y)
            dist = np.sqrt(xx**2 + yy**2)
            mask = np.clip(1.0 - dist, 0, 1)
            self._vignette_mask = np.stack([mask] * 3, axis=-1).astype(np.float32)
            self._vignette_size = (w, h)
        return self._vignette_mask

    def reset(self):
        self.brightness = 0
        self.contrast = 0
        self.saturation = 0
        self.gamma = 1.0
        self.blur = 0
        self.sharpen = 0
        self.vignette = 0
        self.pixelate = 0
        self.sepia = 0
        self.grayscale = 0
        self.invert = 0
        self.temperature = 0
        self.mirror_h = False
        self.mirror_v = False

    def apply(self, frame):
        result = frame
        h, w = result.shape[:2]

        # Transform: mirror
        if self.mirror_h:
            result = cv2.flip(result, 1)
        if self.mirror_v:
            result = cv2.flip(result, 0)

        # Brightness + Contrast
        if self.brightness != 0 or self.contrast != 0:
            alpha = (self.contrast + 100) / 100.0
            beta = self.brightness * 1.28
            result = cv2.convertScaleAbs(result, alpha=alpha, beta=beta)

        # Saturation
        if self.saturation != 0:
            gray = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
            gray_3ch = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            factor = 1.0 + self.saturation / 100.0
            if factor >= 1.0:
                w_blend = min(factor - 1.0, 1.0)
                result = cv2.addWeighted(result, 1.0 + w_blend, gray_3ch, -w_blend, 0)
            else:
                result = cv2.addWeighted(result, factor, gray_3ch, 1.0 - factor, 0)

        # Gamma
        if self.gamma != 1.0:
            result = cv2.LUT(result, self._get_gamma_lut())

        # Temperature (warm/cool)
        if self.temperature != 0:
            result = result.astype(np.float32)
            temp = self.temperature / 100.0
            if temp > 0:
                result[:, :, 2] = np.clip(result[:, :, 2] + temp * 40, 0, 255)
                result[:, :, 0] = np.clip(result[:, :, 0] - temp * 20, 0, 255)
            else:
                result[:, :, 0] = np.clip(result[:, :, 0] - temp * 40, 0, 255)
                result[:, :, 2] = np.clip(result[:, :, 2] + temp * 20, 0, 255)
            result = result.astype(np.uint8)

        # Blur
        if self.blur > 0:
            ksize = self.blur * 2 + 1
            result = cv2.GaussianBlur(result, (ksize, ksize), 0)

        # Sharpen
        if self.sharpen > 0:
            amount = self.sharpen / 100.0
            blurred = cv2.GaussianBlur(result, (0, 0), 3)
            result = cv2.addWeighted(result, 1.0 + amount, blurred, -amount, 0)

        # Pixelate
        if self.pixelate > 0:
            block = max(self.pixelate // 5, 2)
            small = cv2.resize(result, (w // block, h // block), interpolation=cv2.INTER_LINEAR)
            result = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)

        # Vignette
        if self.vignette > 0:
            mask = self._get_vignette_mask(h, w)
            strength = self.vignette / 100.0
            mask = mask * strength + (1.0 - strength)
            result = (result.astype(np.float32) * mask).astype(np.uint8)

        # Sepia
        if self.sepia > 0:
            gray = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
            sepia_tint = np.zeros_like(result)
            sepia_tint[:, :, 0] = np.clip(gray * 0.6, 0, 255)
            sepia_tint[:, :, 1] = np.clip(gray * 0.7, 0, 255)
            sepia_tint[:, :, 2] = np.clip(gray * 0.9, 0, 255)
            w_blend = self.sepia / 100.0
            result = cv2.addWeighted(result, 1.0 - w_blend, sepia_tint, w_blend, 0)

        # Grayscale
        if self.grayscale > 0:
            gray = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
            gray_3ch = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            w_blend = self.grayscale / 100.0
            result = cv2.addWeighted(result, 1.0 - w_blend, gray_3ch, w_blend, 0)

        # Invert
        if self.invert > 0:
            inverted = cv2.bitwise_not(result)
            w_blend = self.invert / 100.0
            result = cv2.addWeighted(result, 1.0 - w_blend, inverted, w_blend, 0)

        return result
