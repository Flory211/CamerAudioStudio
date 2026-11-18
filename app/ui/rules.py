"""Visual event → action rules; refresh never recreates the automation engine."""
import copy
import math

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)
from PyQt6.QtWidgets import QSizePolicy


class RuleEditor(QWidget):
    EVENTS = [('По таймеру', 'timer'), ('Когда начинается речь', 'speech_start'),
              ('Когда речь заканчивается', 'speech_end'), ('При начале записи', 'record_start'),
              ('При остановке записи', 'record_stop')]
    ACTIONS = [('Переключить сцену', 'scene'), ('Изменить звук канала', 'mute'),
               ('Управлять записью', 'record'), ('Воспроизвести звук', 'sound')]

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._project = None
        self._snapshot = None
        self._targets_snapshot = None
        self._display_targets_snapshot = None
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel('Выбери событие и действие. Галочка в списке включает правило. Правила работают во время превью.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.rule_list = QListWidget()
        self.rule_list.setMinimumHeight(140)
        self.rule_list.setWordWrap(True)
        self.rule_list.currentRowChanged.connect(self._select)
        self.rule_list.itemChanged.connect(self._enabled_changed)
        layout.addWidget(self.rule_list)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.event, self.action, self.target, self.value = (QComboBox() for _ in range(4))
        for combo in (self.event, self.action, self.target, self.value):
            combo.setMinimumContentsLength(10)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for label, key in self.EVENTS:
            self.event.addItem(label, key)
        for label, key in self.ACTIONS:
            self.action.addItem(label, key)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(.1, 86400)
        self.interval.setDecimals(1)
        self.interval.setSuffix(' с')
        self.interval.setValue(10)
        self.interval.setToolTip('Повторять действие с этим интервалом, пока работает превью')
        self.target_label, self.value_label = QLabel('Цель'), QLabel('Состояние')
        form.addRow('Когда', self.event)
        form.addRow('Интервал', self.interval)
        form.addRow('Сделать', self.action)
        form.addRow(self.target_label, self.target)
        form.addRow(self.value_label, self.value)
        self.rule_enabled = QCheckBox('Правило включено')
        self.rule_enabled.setChecked(True)
        form.addRow(self.rule_enabled)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        for label, method in [('Добавить', self.add_rule), ('Сохранить изменения', self.save_rule), ('Убрать', self.remove_rule)]:
            button = QPushButton(label)
            button.clicked.connect(method)
            buttons.addWidget(button)
            if method == self.save_rule:
                self.save_button = button
            elif method == self.remove_rule:
                self.remove_button = button
        layout.addLayout(buttons)
        self.message = QLabel('')
        self.message.setWordWrap(True)
        self.message.setObjectName('Alert')
        layout.addWidget(self.message)
        layout.addStretch()
        self.event.currentIndexChanged.connect(self._fields)
        self.action.currentIndexChanged.connect(self._fields)
        self.refresh()
        self._fields()

    def _rules(self):
        return self.window.project.data.setdefault('rules', [])

    def _entries(self, action=None):
        action = action or self.action.currentData()
        project = self.window.project
        if action == 'scene':
            return [(scene['name'], scene['id']) for scene in project.scenes]
        if action == 'mute':
            channels = getattr(getattr(self.window, 'audio_engine', None), 'channels', {})
            return [(str(name), str(name)) for name in channels]
        if action == 'sound':
            return [(sound.get('name') or 'Звук без названия', sound.get('id') or sound.get('name'))
                    for sound in project.data.get('soundboard', [])
                    if isinstance(sound, dict) and (sound.get('id') or sound.get('name'))]
        return []

    def _targets(self):
        entries = self._entries()
        snapshot = (self.action.currentData(), entries)
        if snapshot == self._targets_snapshot:
            return
        self._targets_snapshot = copy.deepcopy(snapshot)
        previous = self.target.currentData()
        self.target.blockSignals(True)
        self.target.clear()
        for name, key in entries:
            self.target.addItem(name, key)
        index = self.target.findData(previous)
        self.target.setCurrentIndex(index if index >= 0 else (0 if entries else -1))
        self.target.blockSignals(False)

    def _fields(self, *args):
        self.interval.setEnabled(self.event.currentData() == 'timer')
        action = self.action.currentData()
        self.target.setVisible(action != 'record')
        self.target_label.setVisible(action != 'record')
        self.target_label.setText({'scene': 'Сцена', 'mute': 'Канал', 'sound': 'Звук'}.get(action, 'Цель'))
        self.value.setVisible(action in {'record', 'mute'})
        self.value_label.setVisible(action in {'record', 'mute'})
        previous = self.value.currentData()
        self.value.clear()
        if action == 'record':
            self.value.addItem('Начать запись', True)
            self.value.addItem('Остановить запись', False)
        else:
            self.value.addItem('Выключить звук', True)
            self.value.addItem('Вернуть звук', False)
        if isinstance(previous, bool):
            self.value.setCurrentIndex(self.value.findData(previous))
        self._targets()

    def build_rule(self):
        event, action = self.event.currentData(), self.action.currentData()
        rule = dict(enabled=self.rule_enabled.isChecked(), event=event, action=dict(type=action))
        if event == 'timer':
            rule['interval'] = self.interval.value()
        if action != 'record':
            target = self.target.currentData()
            if not isinstance(target, str) or not 1 <= len(target) <= 128 or target not in {key for _, key in self._entries()}:
                raise ValueError({'scene': 'Выбери существующую сцену.', 'mute': 'Выбери звуковой канал.',
                                  'sound': 'Добавь звук в саундборд и выбери его.'}[action])
            rule['action']['target'] = target
        if action in {'mute', 'record'}:
            rule['action']['value'] = bool(self.value.currentData())
        return rule

    def _error(self, text):
        self.message.setText(str(text))
        self.window.notify(str(text), error=True)

    def _changed(self, selected):
        self.message.clear()
        self.window.mark_changed()
        self._snapshot = None
        self.refresh()
        self.rule_list.setCurrentRow(selected)
        self._select(selected)

    def add_rule(self):
        try:
            if len(self._rules()) >= 1000:
                raise ValueError('В проекте уже 1000 правил. Убери ненужные перед добавлением.')
            rule = self.build_rule()
        except ValueError as exc:
            self._error(exc)
            return
        self._rules().append(rule)
        self._changed(len(self._rules())-1)

    def save_rule(self):
        index = self.rule_list.currentRow()
        if not 0 <= index < len(self._rules()):
            self._error('Выбери правило в списке, чтобы изменить его.')
            return
        try:
            rule = self.build_rule()
        except ValueError as exc:
            self._error(exc)
            return
        self._rules()[index] = rule
        self._changed(index)

    def remove_rule(self):
        index = self.rule_list.currentRow()
        if 0 <= index < len(self._rules()):
            del self._rules()[index]
            self._changed(min(index, len(self._rules())-1))

    def _select(self, index):
        if self._loading:
            return
        valid = 0 <= index < len(self._rules())
        self.save_button.setEnabled(valid)
        self.remove_button.setEnabled(valid)
        if not valid:
            return
        rule = self._rules()[index]
        if not isinstance(rule, dict):
            return
        action = rule.get('action') if isinstance(rule.get('action'), dict) else {}
        self.event.setCurrentIndex(max(0, self.event.findData(rule.get('event'))))
        self.action.setCurrentIndex(max(0, self.action.findData(action.get('type'))))
        try:
            interval = float(rule.get('interval', 10))
            if not math.isfinite(interval):
                raise ValueError()
        except (TypeError, ValueError):
            interval = 10.
        self.interval.setValue(interval)
        self.rule_enabled.setChecked(bool(rule.get('enabled', True)))
        self._fields()
        target_index = self.target.findData(action.get('target'))
        if action.get('type') in {'scene', 'mute', 'sound'}:
            self.target.setCurrentIndex(target_index)
        self.value.setCurrentIndex(max(0, self.value.findData(action.get('value', True))))

    def _enabled_changed(self, item):
        if self._loading:
            return
        index = self.rule_list.row(item)
        if 0 <= index < len(self._rules()) and isinstance(self._rules()[index], dict):
            enabled = item.checkState() == Qt.CheckState.Checked
            self._rules()[index]['enabled'] = enabled
            if self.rule_list.currentRow() == index:
                self.rule_enabled.setChecked(enabled)
            self._snapshot = copy.deepcopy(self._rules())
            self.window.mark_changed()

    def _summary(self, rule):
        if not isinstance(rule, dict):
            return 'Некорректное правило — убери его и создай заново'
        event = dict((key, label) for label, key in self.EVENTS).get(rule.get('event'), 'Неизвестное событие')
        if rule.get('event') == 'timer':
            event = f"Каждые {rule.get('interval', 1)} с"
        action = rule.get('action') if isinstance(rule.get('action'), dict) else {}
        kind = action.get('type')
        name = dict((key, label) for label, key in self.ACTIONS).get(kind, 'Неизвестное действие')
        if kind == 'record':
            name = 'Начать запись' if action.get('value') else 'Остановить запись'
        else:
            target = next((label for label, key in self._entries(kind) if key == action.get('target')), 'Цель недоступна')
            if kind == 'mute':
                name = 'Выключить звук' if action.get('value') else 'Вернуть звук'
            name += f': {target}'
        return event + ' → ' + name

    def refresh(self):
        switched = self._project is not self.window.project
        if switched:
            self._project = self.window.project
            self._snapshot = self._targets_snapshot = None
            self.message.clear()
        self._targets()
        rules = self._rules()
        display_targets = tuple((kind, tuple(self._entries(kind))) for kind in ('scene', 'mute', 'sound'))
        if not switched and rules == self._snapshot and display_targets == self._display_targets_snapshot:
            return
        self._display_targets_snapshot = display_targets
        summaries = [self._summary(rule) for rule in rules]
        same_summaries = summaries == [self.rule_list.item(i).text() for i in range(self.rule_list.count())]
        if switched or rules != self._snapshot or not same_summaries:
            selected = -1 if switched else self.rule_list.currentRow()
            self._loading = True
            self.rule_list.clear()
            for rule, text in zip(rules, summaries):
                item = QListWidgetItem(text)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if isinstance(rule, dict) and rule.get('enabled', True) else Qt.CheckState.Unchecked)
                self.rule_list.addItem(item)
            self.rule_list.setCurrentRow(selected)
            self._loading = False
            self._snapshot = copy.deepcopy(rules)
            self.save_button.setEnabled(selected >= 0)
            self.remove_button.setEnabled(selected >= 0)
