"""Table-based, non-destructive source animation editor."""
import copy
import math

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout)

from app.studio.compositor import animated_values


class AnimationDialog(QDialog):
    KEYS = ('t', 'x', 'y', 'width', 'height', 'opacity')

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = copy.deepcopy(source)
        self.keyframes = copy.deepcopy(source.get('animation', []))
        self.setWindowTitle('Анимация источника')
        self.resize(850, 480)
        layout = QVBoxLayout(self)
        note = QLabel('Ключевой кадр задаёт положение и вид слоя в указанный момент. Между кадрами значения меняются плавно. Координаты и размер — в процентах от холста. Пресет заменяет кадры в таблице; изменения сохранятся после «Применить».')
        note.setWordWrap(True)
        layout.addWidget(note)
        presets = QHBoxLayout()
        presets.addWidget(QLabel('Готовое движение'))
        self.preset = QComboBox()
        self.preset.addItems(['Появление', 'Слева в кадр', 'Приближение'])
        presets.addWidget(self.preset, 1)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(.1, 86400)
        self.duration.setDecimals(2)
        self.duration.setValue(1)
        self.duration.setSuffix(' с')
        presets.addWidget(self.duration)
        apply = QPushButton('Применить пресет')
        apply.clicked.connect(self.apply_preset)
        presets.addWidget(apply)
        layout.addLayout(presets)
        self.table = QTableWidget(0, len(self.KEYS))
        self.table.setStyleSheet('''
            QTableWidget { background: #111722; color: #e0e8f6; gridline-color: #354259;
                           border: 1px solid #354259; selection-background-color: #263d5c; }
            QHeaderView { background: #202938; }
            QHeaderView::section { background: #202938; color: #a6b9d4;
                                   border: 1px solid #354259; padding: 6px; }
            QTableCornerButton::section { background: #202938; border: 1px solid #354259; }
        ''')
        self.table.setHorizontalHeaderLabels(['Время, с', 'Слева, %', 'Сверху, %', 'Ширина, %', 'Высота, %', 'Видимость, %'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.table, 1)
        rows = QHBoxLayout()
        add = QPushButton('Добавить ключевой кадр')
        add.clicked.connect(self.add_keyframe)
        rows.addWidget(add)
        remove = QPushButton('Убрать выбранные')
        remove.clicked.connect(self.remove_keyframes)
        rows.addWidget(remove)
        clear = QPushButton('Без анимации')
        clear.clicked.connect(lambda: self.table.setRowCount(0))
        rows.addWidget(clear)
        rows.addStretch()
        layout.addLayout(rows)
        self.error_label = QLabel('')
        self.error_label.setObjectName('Alert')
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Применить')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for frame in source.get('animation', []):
            self._append(frame)

    def _base(self):
        return {key: float(self.source.get(key, 1 if key in {'width', 'height', 'opacity'} else 0))
                for key in self.KEYS if key != 't'}

    def _append(self, frame):
        index = self.table.rowCount()
        self.table.insertRow(index)
        time_seconds = frame.get('t', frame.get('time', 0))
        values = animated_values(self.source, time_seconds) | frame
        values['t'] = time_seconds
        for column, key in enumerate(self.KEYS):
            value = values[key] if key == 't' else values[key]*100
            item = QTableWidgetItem(f'{value:.6g}')
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(index, column, item)

    def add_keyframe(self):
        if self.table.rowCount() >= 10000:
            self.error_label.setText('Допустимо не более 10 000 ключевых кадров.')
            return
        try:
            frames = self.collect_keyframes()
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        frame = dict(frames[-1]) if frames else self._base()
        frame['t'] = min(86400, frames[-1]['t']+self.duration.value()) if frames else 0.
        self._append(frame)
        self.table.selectRow(self.table.rowCount()-1)
        self.error_label.clear()

    def remove_keyframes(self):
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        for index in sorted(rows, reverse=True):
            self.table.removeRow(index)

    def apply_preset(self):
        final = self._base()
        first = dict(final)
        name = self.preset.currentText()
        if name == 'Появление':
            first['opacity'] = 0.
        elif name == 'Слева в кадр':
            first['x'] = max(-8., -final['width'])
        elif name == 'Приближение':
            first['width'] = max(.001, final['width']*.75)
            first['height'] = max(.001, final['height']*.75)
            first['x'] = min(8., final['x']+(final['width']-first['width'])/2)
            first['y'] = min(8., final['y']+(final['height']-first['height'])/2)
        self.table.setRowCount(0)
        self._append(dict(t=0., **first))
        self._append(dict(t=self.duration.value(), **final))
        self.error_label.clear()

    def collect_keyframes(self):
        frames = []
        bounds = {'t': (0, 86400), 'x': (-8, 8), 'y': (-8, 8),
                  'width': (.001, 8), 'height': (.001, 8), 'opacity': (0, 1)}
        for row in range(self.table.rowCount()):
            frame = {}
            for column, key in enumerate(self.KEYS):
                item = self.table.item(row, column)
                try:
                    value = float(item.text().strip().replace(',', '.')) if item else float('nan')
                    value = value if key == 't' else value/100
                    low, high = bounds[key]
                    if not math.isfinite(value) or not low <= value <= high:
                        raise ValueError()
                except ValueError:
                    label = self.table.horizontalHeaderItem(column).text()
                    low, high = bounds[key]
                    if key != 't':
                        low, high = low*100, high*100
                    raise ValueError(f'Строка {row+1}, «{label}»: укажи число от {low:g} до {high:g}.') from None
                frame[key] = value
            frames.append(frame)
        return sorted(frames, key=lambda frame: frame['t'])

    def accept(self):
        try:
            frames = self.collect_keyframes()
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        self.keyframes = frames
        super().accept()
