"""BGR scene composition without GUI or capture dependencies."""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont


def animated_values(source, time_seconds):
    values = {key: source.get(key, 1 if key in ('width', 'height', 'opacity') else 0)
              for key in ('x', 'y', 'width', 'height', 'opacity')}
    for key in values:
        points = sorted((float(frame.get('time', frame.get('t', 0))), float(frame[key]))
                        for frame in source.get('animation', []) if key in frame)
        if not points:
            continue
        if time_seconds <= points[0][0]:
            values[key] = points[0][1]
        elif time_seconds >= points[-1][0]:
            values[key] = points[-1][1]
        else:
            for (start, a), (end, b) in zip(points, points[1:]):
                if start <= time_seconds <= end:
                    values[key] = b if end == start else a + (b - a) * (time_seconds - start) / (end - start)
                    break
    return values


def _color(value, default='#ffffff'):
    try:
        return ImageColor.getrgb(str(value))[:3][::-1]
    except (ValueError, TypeError):
        return ImageColor.getrgb(default)[::-1]


@lru_cache(maxsize=16)
def _read_image(path, modified):
    del modified
    return cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)


@lru_cache(maxsize=32)
def _font(size, preferred):
    candidates = [preferred, 'C:/Windows/Fonts/segoeui.ttf', 'C:/Windows/Fonts/arial.ttf',
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 'DejaVuSans.ttf']
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _text(source, width, height):
    image = Image.new('RGBA', (width, height))
    if source.get('background'):
        b, g, r = _color(source['background'], '#151922')
        image.paste((r, g, b, 255), (0, 0, width, height))
    draw = ImageDraw.Draw(image)
    font = _font(max(1, min(512, int(source.get('font_size', min(height * .65, width / 12))))), str(source.get('font', '')))
    b, g, r = _color(source.get('color', '#ffffff'))
    draw.multiline_text((2, 0), str(source.get('text', '')), font=font, fill=(r, g, b, 255),
                        spacing=4, stroke_width=max(0, min(8, int(source.get('outline', 0)))),
                        stroke_fill=(0, 0, 0, 255))
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGBA2BGRA)


def _fit(frame, width, height, mode):
    source_height, source_width = frame.shape[:2]
    if source_width <= 0 or source_height <= 0:
        return None
    if frame.ndim == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGRA)
    elif frame.shape[2] == 3:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
    if mode == 'stretch':
        return cv2.resize(frame, (width, height))
    ratio = (max if mode in ('cover', 'crop') else min)(width / source_width, height / source_height)
    scaled = cv2.resize(frame, (max(1, round(source_width * ratio)), max(1, round(source_height * ratio))))
    if mode in ('cover', 'crop'):
        y, x = (scaled.shape[0] - height) // 2, (scaled.shape[1] - width) // 2
        return scaled[y:y + height, x:x + width]
    result = np.zeros((height, width, 4), np.uint8)
    y, x = (height - scaled.shape[0]) // 2, (width - scaled.shape[1]) // 2
    result[y:y + scaled.shape[0], x:x + scaled.shape[1]] = scaled
    return result


def compose(scene, frames, size=(1280, 720), time_seconds=0.0):
    width, height = map(int, size)
    if width < 1 or height < 1 or width * height > 40_000_000:
        raise ValueError('Недопустимый размер холста')
    canvas = np.empty((height, width, 3), np.uint8)
    canvas[:] = _color(scene.get('color', scene.get('background', '#10131a')), '#10131a')
    for source in scene.get('sources', []):
        if not source.get('visible', True):
            continue
        values = animated_values(source, time_seconds)
        opacity = float(np.clip(values['opacity'], 0, 1))
        if not opacity:
            continue
        layer_width, layer_height = round(float(values['width']) * width), round(float(values['height']) * height)
        if layer_width < 1 or layer_height < 1 or layer_width * layer_height > 40_000_000:
            continue
        kind = source.get('kind')
        if kind == 'color':
            layer = np.full((layer_height, layer_width, 4), (*_color(source.get('color', '#172033')), 255), np.uint8)
        elif kind == 'text':
            layer = _text(source, layer_width, layer_height)
        else:
            frame = frames.get(source.get('id'))
            if frame is None and kind == 'image' and source.get('path'):
                try:
                    path = Path(source['path'])
                    frame = _read_image(str(path), path.stat().st_mtime_ns)
                except (OSError, ValueError, cv2.error):
                    continue
            if not isinstance(frame, np.ndarray) or frame.ndim not in (2, 3) or not frame.size:
                continue
            if frame.dtype != np.uint8:
                frame = np.clip(frame, 0, 255).astype(np.uint8)
            if frame.ndim == 3 and frame.shape[2] not in (3, 4):
                continue
            left, top, right, bottom = source.get('crop', [0, 0, 0, 0])
            fh, fw = frame.shape[:2]
            frame = frame[max(0, round(top * fh)):min(fh, round((1 - bottom) * fh)),
                          max(0, round(left * fw)):min(fw, round((1 - right) * fw))]
            if not frame.size:
                continue
            layer = _fit(frame, layer_width, layer_height, source.get('fit', 'contain'))
        x, y = round(float(values['x']) * width), round(float(values['y']) * height)
        rotation = float(source.get('rotation', 0)) % 360
        if rotation:
            matrix = cv2.getRotationMatrix2D((layer_width / 2, layer_height / 2), -rotation, 1)
            cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
            rotated_width = int(layer_height * sine + layer_width * cosine)
            rotated_height = int(layer_height * cosine + layer_width * sine)
            matrix[0, 2] += (rotated_width - layer_width) / 2
            matrix[1, 2] += (rotated_height - layer_height) / 2
            layer = cv2.warpAffine(layer, matrix, (rotated_width, rotated_height))
            x -= (rotated_width - layer_width) // 2
            y -= (rotated_height - layer_height) // 2
        lh, lw = layer.shape[:2]
        x0, y0, x1, y1 = max(0, x), max(0, y), min(width, x + lw), min(height, y + lh)
        if x0 >= x1 or y0 >= y1:
            continue
        patch = layer[y0 - y:y1 - y, x0 - x:x1 - x]
        alpha = patch[:, :, 3:4].astype(np.float32) * (opacity / 255)
        canvas[y0:y1, x0:x1] = np.rint(patch[:, :, :3] * alpha + canvas[y0:y1, x0:x1] * (1 - alpha)).astype(np.uint8)
    return canvas
