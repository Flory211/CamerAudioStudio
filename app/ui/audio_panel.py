"""Studio audio properties, independent channel routing and local soundboard."""
from pathlib import Path
import uuid

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)


def _button(text, callback, layout):
    button = QPushButton(text)
    button.clicked.connect(callback)
    layout.addWidget(button)
    return button


def _spin(low, high, step=1, suffix=''):
    widget = QDoubleSpinBox()
    widget.setRange(low, high)
    widget.setDecimals(2 if step < 1 else 0)
    widget.setSingleStep(step)
    widget.setSuffix(suffix)
    return widget


def _state(window):
    return window.project.data.setdefault('audio', {})


class AudioPanel(QWidget):
    EFFECT_LABELS = {'gate': 'Шумовой порог', 'noise_reduction': 'Шумоподавление',
        'equalizer': 'Эквалайзер', 'compressor': 'Компрессор', 'pitch': 'Высота голоса',
        'robot': 'Робот', 'distortion': 'Искажение', 'telephone': 'Телефон',
        'reverb': 'Эхо', 'gain': 'Усиление'}

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.engine = window.audio_engine
        self._loading = True
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName('AudioSurface')
        body.setStyleSheet('QWidget#AudioSurface { background: #101218; }')
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(4, 4, 4, 4)
        devices = QGroupBox('Устройства и захват')
        form = QFormLayout(devices)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.input_device = QComboBox()
        self.output_device = QComboBox()
        for combo in (self.input_device, self.output_device):
            combo.setMinimumContentsLength(12)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        form.addRow('Микрофон', self.input_device)
        form.addRow('Вывод', self.output_device)
        self.system_audio = QCheckBox('Захватывать общий системный звук')
        form.addRow(self.system_audio)
        note = QLabel('Вывод в наушники или виртуальный кабель.\nБез выбранного вывода звук доступен записи.')
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QHBoxLayout()
        self.start_button = _button('Включить звук', self._toggle, buttons)
        _button('Обновить', self._devices, buttons)
        form.addRow(buttons)
        self.status = QLabel('Аудиозахват выключен')
        self.status.setWordWrap(True)
        form.addRow(self.status)
        layout.addWidget(devices)
        group = QGroupBox('Микрофон и голос')
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.presets = QComboBox()
        self.presets.addItems(['Чистый', 'Робот', 'Телефон', 'Глубокий', 'Высокий', 'Эхо', 'Радио'])
        self.presets.activated.connect(self._preset)
        form.addRow('Пресет', self.presets)
        self.controls = {}
        definitions = [('gate_db', 'Шумовой порог', -120, 0, 1, ' дБ'),
            ('noise_reduction', 'Шумоподавление', 0, 1, .05, ''),
            ('low_db', 'Низкие частоты', -24, 24, 1, ' дБ'),
            ('mid_db', 'Средние частоты', -24, 24, 1, ' дБ'),
            ('high_db', 'Высокие частоты', -24, 24, 1, ' дБ'),
            ('compressor', 'Компрессор', 0, 1, .05, ''),
            ('limiter', 'Лимитер', .01, 1, .05, ''),
            ('pitch', 'Высота голоса', .5, 2, .05, ' ×'),
            ('robot', 'Робот', 0, 1, .05, ''),
            ('distortion', 'Искажение', 0, 1, .05, ''),
            ('reverb', 'Эхо', 0, 1, .05, ''),
            ('gain', 'Усиление', 0, 4, .1, ' ×'),
            ('wet', 'Доля обработки', 0, 1, .05, '')]
        for key, label, low, high, step, suffix in definitions:
            control = _spin(low, high, step, suffix)
            control.setValue(getattr(self.engine.effects, key))
            control.valueChanged.connect(lambda value, key=key: self._effect(key, value))
            self.controls[key] = control
            form.addRow(label, control)
        self.telephone = QCheckBox('Телефонная полоса частот')
        self.telephone.toggled.connect(lambda value: self._effect('telephone', value))
        form.addRow(self.telephone)
        layout.addWidget(group)
        chain = QGroupBox('Порядок обработки — перетащи строки')
        chain_layout = QVBoxLayout(chain)
        self.chain = QListWidget()
        self.chain.setMinimumHeight(170)
        self.chain.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.chain.model().rowsMoved.connect(self._chain_changed)
        chain_layout.addWidget(self.chain)
        layout.addWidget(chain)
        _button('Громкость Windows-приложений…', self._sessions, layout)
        limitations = QLabel('Windows-сессии управляют уровнями приложений. Системный захват — общий звук устройства, без отдельных дорожек приложений.')
        limitations.setWordWrap(True)
        layout.addWidget(limitations)
        layout.addStretch()
        self._devices()
        self.input_device.currentIndexChanged.connect(self._save)
        self.output_device.currentIndexChanged.connect(self._save)
        self.system_audio.toggled.connect(self._save)
        self._loading = False
        self.load_project()

    def _devices(self):
        previous_input = self.input_device.currentData()
        previous_output = self.output_device.currentData()
        for combo in (self.input_device, self.output_device):
            combo.blockSignals(True)
            combo.clear()
        self.input_device.addItem('Микрофон Windows по умолчанию', None)
        self.input_device.addItem('Без микрофона — звуки и гости', -1)
        self.output_device.addItem('Только запись / без вывода', None)
        try:
            import sounddevice as sd
            for index, device in enumerate(sd.query_devices()):
                if device['max_input_channels']:
                    self.input_device.addItem(device['name'], index)
                if device['max_output_channels']:
                    self.output_device.addItem(device['name'], index)
        except Exception as exc:
            self.status.setText(f'Список устройств недоступен: {exc}')
        for combo, previous in ((self.input_device, previous_input), (self.output_device, previous_output)):
            combo.setCurrentIndex(max(0, combo.findData(previous)))
            combo.blockSignals(False)

    def _effect(self, key, value):
        if not self._loading:
            self.engine.effects.configure({key: value})
            self._save()

    def _save(self, *args):
        if self._loading:
            return
        _state(self.window).update(effects=self.engine.effects.to_dict(),
            input_device=self.input_device.currentData(), output_device=self.output_device.currentData(),
            system_audio=self.system_audio.isChecked())
        self.window.mark_changed()

    def _preset(self, *args):
        try:
            self.engine.effects.preset(self.presets.currentText())
            self._sync_effects()
            self._save()
        except ValueError as exc:
            self.window.notify(str(exc), error=True)

    def _sync_effects(self):
        loading, self._loading = self._loading, True
        for key, control in self.controls.items():
            control.setValue(getattr(self.engine.effects, key))
        self.telephone.setChecked(self.engine.effects.telephone)
        self.chain.clear()
        for effect in self.engine.effects.chain:
            item = QListWidgetItem(self.EFFECT_LABELS.get(effect, effect))
            item.setData(Qt.ItemDataRole.UserRole, effect)
            self.chain.addItem(item)
        self._loading = loading

    def _chain_changed(self, *args):
        if not self._loading:
            order = [self.chain.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.chain.count())]
            self.engine.effects.configure({'chain': order})
            self._save()

    def _toggle(self):
        if self.engine.running:
            self.engine.stop()
        else:
            self._save()
            self.engine.start(self.input_device.currentData(), self.output_device.currentData(), self.system_audio.isChecked())
        self.refresh()

    def load_project(self):
        self._loading = True
        settings = _state(self.window)
        try:
            self.engine.effects.configure({**self.engine.effects.DEFAULTS, 'chain': self.engine.effects.ORDER.copy(), **settings.get('effects', {})})
        except (TypeError, ValueError) as exc:
            self.window.notify(f'Настройки звука проекта не применены: {exc}', error=True)
        self._sync_effects()
        for combo, key in ((self.input_device, 'input_device'), (self.output_device, 'output_device')):
            index = combo.findData(settings.get(key))
            combo.setCurrentIndex(max(0, index))
        self.system_audio.setChecked(bool(settings.get('system_audio', False)))
        self._loading = False
        self.refresh()

    def refresh(self):
        self.start_button.setText('Выключить звук' if self.engine.running else 'Включить звук')
        self.status.setText('Захват и микшер включены' if self.engine.running else 'Аудиозахват выключен')

    def _sessions(self):
        from app.audio import sessions
        try:
            items = sessions.list_sessions()
        except Exception as exc:
            self.window.notify(str(exc), error=True)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Громкость Windows-приложений')
        dialog.resize(520, 400)
        layout = QVBoxLayout(dialog)
        label = QLabel('Изменения сразу применяются в микшере Windows.')
        label.setWordWrap(True)
        layout.addWidget(label)
        def update(call, *values):
            try:
                call(*values)
            except Exception as exc:
                self.window.notify(str(exc), error=True)
        for item in items:
            row = QHBoxLayout()
            row.addWidget(QLabel(item['name']), 1)
            level = _spin(0, 100, 1, '%')
            level.setValue(item['volume']*100)
            level.valueChanged.connect(lambda value, key=item['id']: update(sessions.set_session_volume, key, value/100))
            row.addWidget(level)
            mute = QCheckBox('Без звука')
            mute.setChecked(item['muted'])
            mute.toggled.connect(lambda value, key=item['id']: update(sessions.set_session_mute, key, value))
            row.addWidget(mute)
            layout.addLayout(row)
        if not items:
            layout.addWidget(QLabel('Активные аудиосессии не найдены. Включи звук в приложении.'))
        layout.addStretch()
        _button('Закрыть', dialog.accept, layout)
        dialog.exec()


class MixerPanel(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.engine = window, window.audio_engine
        self._loading = False
        self.rows = {}
        self.levels = {}
        self.engine.levels_changed.connect(self._levels)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, 3)
        self.channel_widget = QWidget()
        self.channel_widget.setObjectName('AudioSurface')
        self.channel_widget.setStyleSheet('QWidget#AudioSurface { background: #101218; }')
        self.channel_layout = QVBoxLayout(self.channel_widget)
        self.channel_layout.setContentsMargins(0, 0, 4, 0)
        self.channel_layout.addStretch()
        scroll.setWidget(self.channel_widget)
        board = QGroupBox('Саундборд')
        board_layout = QVBoxLayout(board)
        self.sound_list = QListWidget()
        self.sound_list.setMinimumWidth(220)
        self.sound_list.setMaximumHeight(90)
        self.sound_list.currentRowChanged.connect(self._sound_selected)
        board_layout.addWidget(self.sound_list)
        options = QHBoxLayout()
        self.loop = QCheckBox('Повтор')
        self.loop.toggled.connect(self._sound_options)
        options.addWidget(self.loop)
        self.sound_gain = _spin(0, 4, .1, ' ×')
        self.sound_gain.setValue(1)
        self.sound_gain.valueChanged.connect(self._sound_options)
        options.addWidget(self.sound_gain)
        board_layout.addLayout(options)
        buttons = QHBoxLayout()
        _button('+ WAV', self._add_sound, buttons)
        _button('Играть', self._play_sound, buttons).setToolTip('Воспроизвести выбранный звук')
        _button('Стоп', self.engine.stop_sounds, buttons).setToolTip('Остановить все звуки')
        _button('Убрать', self._remove_sound, buttons).setToolTip('Убрать из саундборда')
        board_layout.addLayout(buttons)
        self.ducking = QCheckBox('Приглушать звуки во время речи')
        self.ducking.toggled.connect(self._duck)
        board_layout.addWidget(self.ducking)
        layout.addWidget(board, 2)
        self.load_project()

    def _channel_row(self, name):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        title = QLabel(name)
        title.setMinimumWidth(105)
        title.setMaximumWidth(180)
        row.addWidget(title)
        meter = QProgressBar()
        meter.setRange(0, 60)
        meter.setMinimumWidth(50)
        meter.setMaximumWidth(100)
        row.addWidget(meter, 1)
        gain = _spin(0, 4, .1, ' ×')
        gain.setMaximumWidth(90)
        gain.setValue(self.engine.channels[name]['gain'])
        gain.valueChanged.connect(lambda value: self._channel_change(name, 'gain', value))
        row.addWidget(gain)
        fields = {'gain': gain, 'meter': meter}
        config = self.engine.channels[name]
        for key, text in [('muted', 'Без звука'), ('solo', 'Соло'), ('record', 'Запись'), ('output', 'Вывод')]:
            control = QCheckBox(text)
            control.setToolTip({'muted': 'Отключить канал', 'solo': 'Слушать только выбранные Solo-каналы', 'record': 'Отправлять в запись', 'output': 'Отправлять на выбранное устройство'}[key])
            control.setChecked(config[key])
            control.toggled.connect(lambda value, key=key: self._channel_change(name, key, value))
            row.addWidget(control)
            fields[key] = control
        self.channel_layout.insertWidget(self.channel_layout.count()-1, widget)
        self.rows[name] = fields

    def _channel_change(self, name, key, value):
        if self._loading:
            return
        if key == 'gain':
            self.engine.set_gain(name, value)
        elif key == 'muted':
            self.engine.set_muted(name, value)
        elif key == 'solo':
            self.engine.set_solo(name, value)
        else:
            config = self.engine.channels[name]
            self.engine.set_route(name, record=value if key == 'record' else config['record'], output=value if key == 'output' else config['output'])
        _state(self.window)['channels'] = {name: dict(values) for name, values in self.engine.channels.items()}
        self.window.mark_changed()

    def _levels(self, levels):
        self.levels = levels

    def refresh(self):
        for name in list(self.engine.channels):
            if name not in self.rows:
                self._channel_row(name)
        for name, row in self.rows.items():
            config = self.engine.channels.get(name)
            if config is not None:
                for key in ('gain', 'muted', 'solo', 'record', 'output'):
                    control = row[key]
                    was_blocked = control.blockSignals(True)
                    try:
                        if key == 'gain':
                            control.setValue(config[key])
                        else:
                            control.setChecked(config[key])
                    finally:
                        control.blockSignals(was_blocked)
            value = self.levels.get(name, -120.) if self.engine.running else -120.
            row['meter'].setValue(round(max(0, min(60, value+60))))
            row['meter'].setToolTip(f'{value:.1f} дБ')

    def load_project(self):
        self._loading = True
        settings = _state(self.window)
        for name in list(self.engine.channels):
            self.engine.set_gain(name, 1.)
            self.engine.set_muted(name, False)
            self.engine.set_solo(name, False)
            self.engine.set_route(name, True, True)
        for name, values in settings.get('channels', {}).items():
            try:
                self.engine.set_gain(name, values.get('gain', 1))
                self.engine.set_muted(name, values.get('muted', False))
                self.engine.set_solo(name, values.get('solo', False))
                self.engine.set_route(name, values.get('record', True), values.get('output', True))
            except (TypeError, ValueError) as exc:
                self.window.notify(f'Настройки канала {name}: {exc}', error=True)
        self.engine.ducking = bool(settings.get('ducking', True))
        self.ducking.setChecked(self.engine.ducking)
        self.refresh()
        for name, row in self.rows.items():
            if name in self.engine.channels:
                for key in ('gain', 'muted', 'solo', 'record', 'output'):
                    control = row[key]
                    setter = control.setValue if key == 'gain' else control.setChecked
                    setter(self.engine.channels[name][key])
        self.sound_list.clear()
        for item in self.window.project.data.get('soundboard', []):
            item.setdefault('id', uuid.uuid4().hex)
            self.sound_list.addItem(item.get('name') or Path(item.get('path', '')).stem)
        self._loading = False
        if self.sound_list.count():
            self.sound_list.setCurrentRow(0)

    def _duck(self, value):
        self.engine.ducking = value
        if not self._loading:
            _state(self.window)['ducking'] = value
            self.window.mark_changed()

    def _add_sound(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Добавить звуки', '', 'WAV-файлы (*.wav)')
        for path in paths:
            self.window.project.data.setdefault('soundboard', []).append(dict(id=uuid.uuid4().hex, path=path, name=Path(path).stem, loop=False, gain=1.))
        if paths:
            self.load_project()
            self.window.mark_changed()

    def _sound_selected(self, index):
        items = self.window.project.data.get('soundboard', [])
        if 0 <= index < len(items):
            loading, self._loading = self._loading, True
            self.loop.setChecked(bool(items[index].get('loop', False)))
            self.sound_gain.setValue(float(items[index].get('gain', 1)))
            self._loading = loading

    def _sound_options(self, *args):
        items = self.window.project.data.get('soundboard', [])
        index = self.sound_list.currentRow()
        if not self._loading and 0 <= index < len(items):
            items[index].update(loop=self.loop.isChecked(), gain=self.sound_gain.value())
            self.window.mark_changed()

    def _play_sound(self):
        items = self.window.project.data.get('soundboard', [])
        index = self.sound_list.currentRow()
        if not 0 <= index < len(items):
            self.window.notify('Сначала добавь и выбери WAV-файл.')
            return
        try:
            settings = _state(self.window)
            if not self.engine.running:
                self.engine.start(settings.get('input_device'), settings.get('output_device'), settings.get('system_audio', False))
            item = items[index]
            self.engine.play_sound(item['path'], loop=self.loop.isChecked(), gain=self.sound_gain.value())
            self.refresh()
            if settings.get('output_device') is None:
                self.window.notify('Звук отправлен в микшер записи. Для прослушивания выбери устройство вывода во вкладке «Звук».')
        except Exception as exc:
            self.window.notify(f'Не удалось воспроизвести звук: {exc}', error=True)

    def _remove_sound(self):
        index = self.sound_list.currentRow()
        items = self.window.project.data.get('soundboard', [])
        if 0 <= index < len(items):
            del items[index]
            self.load_project()
            self.window.mark_changed()
