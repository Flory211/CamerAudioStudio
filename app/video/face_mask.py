import cv2
import numpy as np
import os


class FaceMask:
    def __init__(self):
        # Haar Cascade для обнаружения лица
        self.face_cascade = None
        # Путь к файлу в папке проекта
        cascade_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'haarcascade_frontalface_default.xml')
        if os.path.exists(cascade_path):
            self.face_cascade = cv2.CascadeClassifier(cascade_path)
            print(f"Haar Cascade loaded from: {cascade_path}")
        else:
            print(f"Haar Cascade NOT found at: {cascade_path}")

        # Словарь масок: имя -> изображение
        self.masks = {}
        self.current_mask = None

        # Позиция маски (x, y) и размер
        self.mask_x = 0
        self.mask_y = 0
        self.mask_size = 100
        self.is_dragging = False
        self.drag_offset_x = 0
        self.drag_offset_y = 0

        # Ручной режим (без трекинга)
        self.manual_mode = True

        # Загружаем доступные маски
        self._load_masks()

    def _track_face(self, frame):
        """Отслеживает лицо через Haar Cascade"""
        if self.face_cascade is None:
            return

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(gray, 1.3, 5)

        if len(faces) > 0:
            x, y, w, h = faces[0]
            self.mask_x = x + w // 2
            self.mask_y = y + h // 2
            self.mask_size = int(max(w, h) * 1.2)

    def _load_masks(self):
        """Загружает все маски из папки masks/"""
        masks_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'masks')
        if not os.path.exists(masks_dir):
            return

        for filename in os.listdir(masks_dir):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                name = os.path.splitext(filename)[0]
                path = os.path.join(masks_dir, filename)
                # Загружаем с альфа-каналом
                img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
                if img is not None:
                    self.masks[name] = img

    def set_mask(self, name):
        """Устанавливает текущую маску по имени"""
        if name in self.masks:
            self.current_mask = self.masks[name]
            # Центрируем маску
            self.mask_x = 200
            self.mask_y = 150
        elif name == 'none':
            self.current_mask = None

    def get_mask_names(self):
        """Возвращает список доступных масок"""
        return ['none'] + list(self.masks.keys())

    def set_size(self, size):
        """Устанавливает размер маски"""
        self.mask_size = max(20, min(size, 500))

    def handle_click(self, x, y, scale_x=1.0, scale_y=1.0):
        """Обработка клика мыши на превью - перемещает маску"""
        if self.current_mask is None:
            return False

        # Масштабируем координаты
        self.mask_x = int(x * scale_x)
        self.mask_y = int(y * scale_y)
        self.is_dragging = True
        self.drag_offset_x = self.drag_offset_y = 0
        return True

    def handle_drag(self, x, y, scale_x=1.0, scale_y=1.0):
        """Обработка перетаскивания"""
        if self.is_dragging:
            self.mask_x = int(x * scale_x - self.drag_offset_x)
            self.mask_y = int(y * scale_y - self.drag_offset_y)

    def handle_release(self):
        """Обработка отпускания мыши"""
        self.is_dragging = False

    def draw_overlay(self, frame):
        """Рисует рамку вокруг маски для визуальной обратной связи"""
        if self.current_mask is None:
            return frame

        half = int(self.mask_size) // 2
        mask_x = int(self.mask_x)
        mask_y = int(self.mask_y)

        # Рисуем прямоугольник вокруг маски
        cv2.rectangle(frame,
                     (mask_x - half, mask_y - half),
                     (mask_x + half, mask_y + half),
                     (0, 255, 0), 2)

        return frame

    def apply(self, frame):
        """Blend a mask safely, including grayscale files and off-canvas positions."""
        if self.current_mask is None:
            return frame
        if not self.manual_mode:
            self._track_face(frame)
        size = max(1, min(4096, int(self.mask_size)))
        mask = cv2.resize(self.current_mask, (size, size))
        if mask.ndim == 2:
            mask = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGRA)
        elif mask.shape[2] == 3:
            mask = cv2.cvtColor(mask, cv2.COLOR_BGR2BGRA)
        elif mask.shape[2] != 4:
            return frame
        left, top = int(self.mask_x) - size // 2, int(self.mask_y) - size // 2
        x1, y1 = max(0, left), max(0, top)
        x2, y2 = min(frame.shape[1], left + size), min(frame.shape[0], top + size)
        if x1 >= x2 or y1 >= y2:
            return frame
        fragment = mask[y1 - top:y2 - top, x1 - left:x2 - left]
        alpha = fragment[:, :, 3:4].astype(np.float32) / 255.0
        frame[y1:y2, x1:x2] = np.rint(fragment[:, :, :3] * alpha + frame[y1:y2, x1:x2] * (1 - alpha)).astype(np.uint8)
        return frame
