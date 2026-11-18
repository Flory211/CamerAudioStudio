from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import QWidget


class PreviewCanvas(QWidget):
    selected = pyqtSignal(str)
    transformed = pyqtSignal(str, float, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 220)
        self.canvas_size = (1280, 720)
        self.image = None
        self.sources = []
        self.selected_id = None
        self.guides = False
        self._drag = None
        self.setMouseTracking(True)

    def canvas_rect(self):
        width, height = self.canvas_size
        scale = min(self.width() / width, self.height() / height)
        return QRectF((self.width() - width * scale) / 2,
                      (self.height() - height * scale) / 2, width * scale, height * scale)

    def map_to_canvas(self, x, y):
        rect = self.canvas_rect()
        if not rect.contains(float(x), float(y)):
            return None
        return ((x - rect.x()) / rect.width(), (y - rect.y()) / rect.height())

    def set_frame(self, frame):
        if frame is not None:
            self.canvas_size = (frame.shape[1], frame.shape[0])
            rgb = frame[:, :, ::-1].copy()
            self.image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0],
                                QImage.Format.Format_RGB888).copy()
            self.update()

    def layer_rect(self, source):
        rect = self.canvas_rect()
        return QRectF(rect.x() + float(source.get('x', 0)) * rect.width(),
                      rect.y() + float(source.get('y', 0)) * rect.height(),
                      float(source.get('width', 1)) * rect.width(),
                      float(source.get('height', 1)) * rect.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#0b0e14'))
        rect = self.canvas_rect()
        if self.image:
            painter.drawImage(rect, self.image)
        else:
            painter.fillRect(rect, QColor('#151e30'))
        painter.setPen(QPen(QColor('#344157'), 1))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        if self.guides:
            painter.setPen(QPen(QColor(255, 255, 255, 50), 1, Qt.PenStyle.DashLine))
            for fraction in (1 / 3, 2 / 3):
                painter.drawLine(int(rect.x() + rect.width() * fraction), int(rect.y()),
                                 int(rect.x() + rect.width() * fraction), int(rect.bottom()))
                painter.drawLine(int(rect.x()), int(rect.y() + rect.height() * fraction),
                                 int(rect.right()), int(rect.y() + rect.height() * fraction))
        source = next((s for s in self.sources if s['id'] == self.selected_id), None)
        if source and source.get('visible', True):
            box = self.layer_rect(source)
            painter.setPen(QPen(QColor('#6aa9ff'), 2))
            painter.drawRect(box)
            painter.setBrush(QColor('#d5e7ff'))
            for x, y in ((box.left(), box.top()), (box.right(), box.top()),
                         (box.left(), box.bottom()), (box.right(), box.bottom())):
                painter.drawRect(QRectF(x - 3, y - 3, 6, 6))
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self.map_to_canvas(event.position().x(), event.position().y())
        if point is None:
            return
        current = next((s for s in self.sources if s['id'] == self.selected_id), None)
        if current and not current.get('locked', False):
            box = self.layer_rect(current)
            if abs(event.position().x() - box.right()) < 12 and abs(event.position().y() - box.bottom()) < 12:
                self._drag = (current.copy(), point, 'resize')
                return
        for source in reversed(self.sources):
            if source.get('visible', True) and self.layer_rect(source).contains(event.position()):
                self.selected_id = source['id']
                self.selected.emit(source['id'])
                if not source.get('locked', False):
                    self._drag = (source.copy(), point, 'move')
                self.update()
                return

    def mouseMoveEvent(self, event):
        if not self._drag:
            return
        rect = self.canvas_rect()
        point = ((event.position().x() - rect.x()) / rect.width(),
                 (event.position().y() - rect.y()) / rect.height())
        source, begin, mode = self._drag
        dx, dy = point[0] - begin[0], point[1] - begin[1]
        x, y, w, h = (float(source.get(k, default)) for k, default in
                      (('x', 0), ('y', 0), ('width', 1), ('height', 1)))
        if mode == 'move':
            x, y = max(-2, min(2, x + dx)), max(-2, min(2, y + dy))
        else:
            w, h = max(.01, min(4, w + dx)), max(.01, min(4, h + dy))
        self.transformed.emit(source['id'], x, y, w, h)

    def mouseReleaseEvent(self, event):
        self._drag = None
