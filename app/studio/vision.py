"""Local, optional MediaPipe Tasks vision with a Haar tracking fallback."""
from __future__ import annotations

import math
from pathlib import Path
import sys
import time

import cv2
import numpy as np


MODEL_SETUP = {
    'segmentation': 'https://developers.google.com/edge/mediapipe/solutions/vision/image_segmenter#models',
    'face': 'https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker#models',
}


class VisionProcessor:
    MODEL_SETUP = MODEL_SETUP
    DEFAULTS = dict(background='off', background_color='#172033', background_path='',
                    segmentation_model='', face_model='', auto_frame=False, reactive_mask=False,
                    mask_path='', mask_scale=1., mask_mouth_path='', mask_blink_path='',
                    framing_mode='group', allow_mediapipe_metrics=False)

    def __init__(self):
        self.options = self.DEFAULTS.copy()
        self.status = 'Видео без обработки'
        self.face_state = dict(detected=False,x=0.,y=0.,width=0.,height=0.,roll=0.,mouth_open=False,blink=False)
        self.framing_state = dict(detected=False, x=0., y=0., width=0., height=0.)
        self._speaker_box = self._speaker_candidate = None
        self._speaker_since = 0.
        self._face_count = 0
        self._segmenter = self._landmarker = self._mp = self._haar = None
        self._loaded = False
        self._images = {}
        self._issues = []
        self._matte = None
        self._last_inference = 0.
        self._last_face_time = 0.
        self._shape = None
        self._crop = None

    def configure(self, options):
        normalized = self.DEFAULTS | {key:value for key,value in options.items() if key in self.DEFAULTS}
        if normalized['framing_mode'] not in ('group', 'speaker'):
            raise ValueError('Режим кадрирования: group или speaker')
        if normalized == self.options:
            return
        self.close()
        self.options = normalized
        self._images.clear()
        self._issues.clear()
        self._loaded = False
        self._matte = None
        self._last_inference = 0.
        self._last_face_time = 0.
        self._crop = None
        self.face_state.update(detected=False, mouth_open=False, blink=False)
        self.framing_state['detected'] = False
        self._speaker_box = self._speaker_candidate = None
        self._speaker_since = 0.
        self._face_count = 0

    def close(self):
        for name in ('_segmenter','_landmarker'):
            task = getattr(self, name, None)
            if task is not None:
                task.close()
                setattr(self, name, None)
        self._mp = None
        self._loaded = False

    def _issue(self, message):
        if message not in self._issues:
            self._issues.append(message)

    def _load(self):
        self._loaded = True
        opts = self.options
        needs_segment = opts['background'] != 'off'
        needs_face = bool(opts['face_model']) and (opts['auto_frame'] or opts['mask_path'] or opts['reactive_mask'])
        for key, needed, label in (('segmentation_model',needs_segment,'сегментации'),('face_model',opts['reactive_mask'] or needs_face,'лица')):
            if needed and (not opts[key] or not Path(opts[key]).is_file()):
                self._issue(f'Нужна локальная модель {label}: {opts[key] or "файл не выбран"}')
        valid_seg = needs_segment and bool(opts['segmentation_model']) and Path(opts['segmentation_model']).is_file()
        valid_face = needs_face and Path(opts['face_model']).is_file()
        if (valid_seg or valid_face) and not opts['allow_mediapipe_metrics']:
            self._issue('MediaPipe выключен: нужно согласие на техническую статистику в настройках ИИ')
            valid_seg = valid_face = False
        if valid_seg or valid_face:
            try:
                import mediapipe as mp
                from mediapipe.tasks.python import BaseOptions, vision
                self._mp = mp
                if valid_seg:
                    self._segmenter = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
                        base_options=BaseOptions(model_asset_path=str(opts['segmentation_model'])),
                        output_confidence_masks=True, output_category_mask=False))
                if valid_face:
                    self._landmarker = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
                        base_options=BaseOptions(model_asset_path=str(opts['face_model'])), num_faces=4,
                        running_mode=vision.RunningMode.IMAGE,
                        output_face_blendshapes=True))
            except Exception as error:
                self._issue(f'MediaPipe не смог открыть модель: {error}')
        if (opts['auto_frame'] or opts['mask_path']) and self._landmarker is None:
            # The fallback provides position only, never claims expression detection.
            roots = [Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2])), Path(__file__).resolve().parents[2]]
            cascade = next((root/'haarcascade_frontalface_default.xml' for root in roots if (root/'haarcascade_frontalface_default.xml').is_file()), None)
            if cascade is None and hasattr(cv2, 'data'):
                cascade = Path(cv2.data.haarcascades)/'haarcascade_frontalface_default.xml'
            if cascade:
                self._haar = cv2.CascadeClassifier(str(cascade))
                if self._haar.empty():
                    self._haar = None
            if self._haar is None:
                self._issue('Не найден локальный Haar-детектор для автооператора и масок')
        for key in ('background_path','mask_path','mask_mouth_path','mask_blink_path'):
            path = opts[key]
            if path:
                try:
                    # imdecode supports Windows paths containing Cyrillic.
                    pixels = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
                    if pixels is None:
                        raise ValueError('неподдерживаемое изображение')
                    if pixels.ndim == 2:
                        pixels = cv2.cvtColor(pixels, cv2.COLOR_GRAY2BGR)
                    self._images[key] = pixels
                except (OSError, ValueError, cv2.error):
                    self._issue(f'Не удалось открыть изображение: {path}')
        if opts['reactive_mask'] and self._landmarker is None:
            self._issue('Реакция на мимику недоступна без Face Landmarker .task; доступен обычный трекинг')

    def _update_face(self, values, now):
        old = self.face_state
        if old['detected']:
            for key in ('x','y','width','height','roll'):
                values[key] = old[key] * .65 + values[key] * .35
        old.update(values, detected=True)
        self._last_face_time = now

    @staticmethod
    def _box(face):
        return {key: float(face[key]) for key in ('x', 'y', 'width', 'height')}

    @staticmethod
    def _match_face(previous, faces, max_distance=1.25):
        """Match geometry rather than detector order, which can change per image."""
        if previous is None or not faces:
            return None
        px = previous['x'] + previous['width'] / 2
        py = previous['y'] + previous['height'] / 2
        def distance(face):
            scale = max(previous['width'], previous['height'], face['width'], face['height'], 1.)
            return math.hypot(face['x'] + face['width'] / 2 - px,
                              face['y'] + face['height'] / 2 - py) / scale
        nearest = min(faces, key=distance)
        return nearest if distance(nearest) <= max_distance else None

    def _speaker(self, faces, now):
        best = max(faces, key=lambda face: (face.get('jaw_open', 0.), face['width'] * face['height']))
        current = self._match_face(self._speaker_box, faces)
        if current is None:
            current = best
            self._speaker_candidate = None
        elif best is current:
            self._speaker_candidate = None
        elif self._match_face(self._speaker_candidate, [best], max_distance=.5) is None:
            self._speaker_candidate = self._box(best)
            self._speaker_since = now
        else:
            self._speaker_candidate = self._box(best)
            if now - self._speaker_since >= .6:
                current = best
                self._speaker_candidate = None
        self._speaker_box = self._box(current)
        return current

    def _update_faces(self, faces, now, has_jaw=False):
        faces = [face for face in faces[:4] if all(math.isfinite(float(face[key])) for key in ('x', 'y', 'width', 'height'))
                 and face['width'] > 0 and face['height'] > 0]
        self._face_count = len(faces)
        if not faces:
            if now - self._last_face_time > .8:
                self.face_state.update(detected=False, mouth_open=False, blink=False)
                self.framing_state['detected'] = False
                self._speaker_box = self._speaker_candidate = None
            return
        primary = self._match_face(self.face_state, faces) if self.face_state['detected'] else None
        primary = primary if primary is not None else max(faces, key=lambda face: face['width'] * face['height'])
        self._update_face(dict(primary), now)
        if self.options['framing_mode'] == 'group':
            x, y = min(face['x'] for face in faces), min(face['y'] for face in faces)
            right = max(face['x'] + face['width'] for face in faces)
            bottom = max(face['y'] + face['height'] for face in faces)
            target = dict(x=x, y=y, width=right-x, height=bottom-y)
        elif has_jaw:
            target = self._box(self._speaker(faces, now))
        else:
            target = self._box(max(faces, key=lambda face: face['width'] * face['height']))
        self.framing_state.update(target, detected=True)

    def _infer(self, bgr, now):
        height,width = bgr.shape[:2]
        factor = min(1.,640/max(height,width))
        small = cv2.resize(bgr,(max(1,round(width*factor)),max(1,round(height*factor)))) if factor < 1 else bgr
        mp_image = None
        if self._mp and (self._segmenter or self._landmarker):
            mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(cv2.cvtColor(small,cv2.COLOR_BGR2RGB)))
        if self._segmenter:
            try:
                result = self._segmenter.segment(mp_image)
                masks = result.confidence_masks
                if not masks:
                    raise ValueError('модель не вернула confidence mask')
                # Official binary selfie model returns one foreground channel. The
                # multiclass selfie model uses label 0 for background; sum the rest.
                labels = list(getattr(self._segmenter, 'labels', []))
                if len(masks) == 1:
                    matte = masks[0].numpy_view()
                else:
                    foreground = [i for i in range(len(masks)) if i >= len(labels) or str(labels[i]).lower() not in ('background','bg')]
                    if len(foreground) == len(masks):
                        foreground = list(range(1,len(masks)))
                    matte = sum(masks[i].numpy_view() for i in foreground)
                self._matte = np.clip(np.asarray(matte,dtype=np.float32),0,1).copy()
            except Exception as error:
                self._matte = None
                self._issue(f'Ошибка сегментации: {error}')
        faces = []
        has_jaw = False
        if self._landmarker:
            try:
                result = self._landmarker.detect(mp_image)
                for index, points in enumerate(result.face_landmarks[:4]):
                    if len(points) <= 263:
                        continue
                    xs = [p.x*width for p in points]; ys = [p.y*height for p in points]
                    left,right = points[33],points[263]
                    scores = {v.category_name:v.score for v in result.face_blendshapes[index]} if index < len(result.face_blendshapes or []) else {}
                    has_jaw = has_jaw or 'jawOpen' in scores
                    faces.append(dict(x=min(xs), y=min(ys), width=max(xs)-min(xs), height=max(ys)-min(ys),
                                           roll=math.degrees(math.atan2((right.y-left.y)*height,(right.x-left.x)*width)),
                                           jaw_open=float(scores.get('jawOpen',0)),
                                           mouth_open=scores.get('jawOpen',0)>.35,
                                           blink=max(scores.get('eyeBlinkLeft',0),scores.get('eyeBlinkRight',0))>.55))
            except Exception as error:
                self._issue(f'Ошибка трекинга лица: {error}')
        elif self._haar is not None and min(small.shape[:2]) >= 24:
            gray = cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
            rectangles = self._haar.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(24,24))
            for x,y,w,h in sorted(rectangles, key=lambda item: item[2]*item[3], reverse=True)[:4]:
                faces.append(dict(x=float(x/factor),y=float(y/factor),width=float(w/factor),height=float(h/factor),
                                  roll=0.,mouth_open=False,blink=False,jaw_open=0.))
        self._update_faces(faces, now, has_jaw=has_jaw)

    def _background(self, bgr):
        opts = self.options
        if self._matte is None or opts['background']=='off':
            return bgr
        h,w = bgr.shape[:2]
        mode = opts['background']
        if mode == 'blur':
            backdrop = cv2.GaussianBlur(bgr,(0,0),max(1,min(h,w)*.025))
        elif mode == 'color':
            color = str(opts['background_color']).lstrip('#')
            try:
                if len(color) != 6:
                    raise ValueError()
                rgb = tuple(int(color[i:i+2],16) for i in (0,2,4))
            except ValueError:
                rgb = (23,32,51)
                self._issue('Некорректный цвет фона; использован #172033')
            backdrop = np.full_like(bgr,tuple(reversed(rgb)))
        elif mode == 'image':
            image = self._images.get('background_path')
            if image is None:
                self._issue('Для замены фона выбери локальное изображение')
                return bgr
            backdrop = cv2.resize(image[:,:,:3],(w,h))
        else:
            return bgr
        alpha = cv2.resize(self._matte,(w,h),interpolation=cv2.INTER_LINEAR)[:,:,None]
        return np.clip(bgr.astype(np.float32)*alpha+backdrop.astype(np.float32)*(1-alpha),0,255).astype(np.uint8)

    def _mask(self, bgr):
        state = self.face_state
        if not state['detected']:
            return bgr
        key = 'mask_path'
        if self.options['reactive_mask'] and self._landmarker is not None:
            if state['blink'] and 'mask_blink_path' in self._images:
                key = 'mask_blink_path'
            elif state['mouth_open'] and 'mask_mouth_path' in self._images:
                key = 'mask_mouth_path'
        mask = self._images.get(key)
        if mask is None:
            return bgr
        try:
            scale = max(.1,min(6.,float(self.options['mask_scale'])))
        except (ValueError,TypeError):
            scale = 1.
        height,width = bgr.shape[:2]
        mw = max(1,min(width*6,round(state['width']*scale)))
        mh = max(1,min(height*6,round(mw*mask.shape[0]/mask.shape[1])))
        if mask.shape[2] == 3:
            mask = cv2.cvtColor(mask,cv2.COLOR_BGR2BGRA)
        mask = cv2.resize(mask,(mw,mh))
        # Expanded rotation canvas avoids cutting corners off the mask itself.
        angle = float(state['roll'])
        if abs(angle) > .1:
            matrix = cv2.getRotationMatrix2D((mw/2,mh/2),-angle,1)
            cos,sin = abs(matrix[0,0]),abs(matrix[0,1])
            rw,rh = max(1,round(mh*sin+mw*cos)),max(1,round(mh*cos+mw*sin))
            matrix[0,2] += rw/2-mw/2; matrix[1,2] += rh/2-mh/2
            mask = cv2.warpAffine(mask,matrix,(rw,rh),borderMode=cv2.BORDER_CONSTANT)
            mh,mw = mask.shape[:2]
        x = round(state['x']+state['width']/2-mw/2)
        y = round(state['y']+state['height']/2-mh/2)
        x1,y1,x2,y2 = max(0,x),max(0,y),min(width,x+mw),min(height,y+mh)
        if x1>=x2 or y1>=y2:
            return bgr
        fragment = mask[y1-y:y2-y,x1-x:x2-x]
        alpha = fragment[:,:,3:4].astype(np.float32)/255.
        out = bgr.copy()
        out[y1:y2,x1:x2] = (fragment[:,:,:3]*alpha+out[y1:y2,x1:x2]*(1-alpha)).astype(np.uint8)
        return out

    def _autoframe(self, bgr):
        if not self.options['auto_frame']:
            return bgr
        h,w = bgr.shape[:2]
        face = self.framing_state
        target = np.array([w/2,h/2,1.],dtype=np.float64)
        if face['detected'] and face['width']>0 and face['height']>0:
            zoom = min(2.5,max(1.,min(w/(face['width']*2.8),h/(face['height']*2.8))))
            target = np.array([face['x']+face['width']/2,face['y']+face['height']*.6,zoom])
        if self._crop is None:
            self._crop = np.array([w/2,h/2,1.],dtype=np.float64)
        self._crop += (target-self._crop)*.12
        cx,cy,zoom = self._crop
        cw,ch = max(1,min(w,round(w/zoom))),max(1,min(h,round(h/zoom)))
        x,y = max(0,min(w-cw,round(cx-cw/2))),max(0,min(h-ch,round(cy-ch/2)))
        return cv2.resize(bgr[y:y+ch,x:x+cw],(w,h))

    def apply(self, frame):
        if not isinstance(frame,np.ndarray) or frame.ndim!=3 or frame.shape[2] not in (3,4) or frame.size==0:
            return frame
        opts = self.options
        if opts['background']=='off' and not opts['auto_frame'] and not opts['mask_path'] and not opts['reactive_mask']:
            self.status = 'Видео без обработки'
            return frame
        if not self._loaded:
            self._load()
        shape = frame.shape[:2]
        if self._shape is not None and self._shape != shape:
            self._crop = None
            self._last_inference = 0.
            self.face_state['detected'] = False
            self.framing_state['detected'] = False
            self._speaker_box = self._speaker_candidate = None
        self._shape = shape
        now = time.monotonic()
        bgr = np.ascontiguousarray(frame[:,:,:3])
        if now-self._last_inference >= .1:
            self._infer(bgr,now)
            self._last_inference = now
        result = self._autoframe(self._mask(self._background(bgr)))
        self.status = '; '.join(self._issues) if self._issues else ('MediaPipe: обработка активна' if self._segmenter or self._landmarker else 'Haar: трекинг положения лица; мимика недоступна')
        if frame.shape[2]==4:
            return np.dstack((result,frame[:,:,3]))
        return result
