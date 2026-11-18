"""Native Windows studio UI. Devices and external code start only on user actions."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import time
import uuid

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from app import APP_DISPLAY_NAME
from app.audio.engine import AudioEngine
from app.studio.automation import AutomationEngine
from app.studio.engine import StudioEngine
from app.studio.project import StudioProject
from app.ui.audio_panel import AudioPanel, MixerPanel
from app.ui.preview import PreviewCanvas
from app.ui.theme import STYLE, studio_icon
from app.ui.tools import ToolsPanel
from app.ui.animation import AnimationDialog


class BackgroundJob(QThread):
    completed=pyqtSignal(object)
    failed=pyqtSignal(str)
    def __init__(self,work,parent=None):
        super().__init__(parent)
        self.work=work
    def run(self):
        try:self.completed.emit(self.work())
        except Exception as error:self.failed.emit(str(error))


class MainWindow(QMainWindow):
    SOURCE_NAMES={'camera':'Камера','screen':'Экран','window':'Окно','image':'Изображение',
                  'text':'Текст','color':'Цвет','network':'Сетевой поток','guest':'Гость / телефон'}
    EFFECTS=[('brightness','Яркость',-100,100,0),('contrast','Контраст',-100,100,0),
             ('saturation','Насыщенность',-100,100,0),('gamma','Гамма',.1,3,1),
             ('temperature','Температура',-100,100,0),('blur','Размытие',0,30,0),
             ('sharpen','Резкость',0,100,0),('vignette','Виньетка',0,100,0),
             ('pixelate','Пикселизация',0,100,0),('sepia','Сепия',0,100,0),
             ('grayscale','Чёрно-белый',0,100,0),('invert','Инверсия',0,100,0)]

    def __init__(self):
        super().__init__()
        self.project=StudioProject()
        self.engine=StudioEngine(self.project)
        self.audio_engine=AudioEngine(self)
        self.server=None
        self.caption_service=None
        self.project_path=None
        self.recording_directory=None
        self.dirty=False
        self.log_messages=[]
        self._jobs=set()
        self._loading=False
        self._selected_id=None
        self._levels={}
        self._remote_time=0.
        self._ui_time=0.
        self._undo=[]
        self._last_history=copy.deepcopy(self.project.data)
        self.automation=AutomationEngine(self.project.data['rules'])
        self._automation_signature=''
        self._closing=False
        self.setWindowTitle(f'{APP_DISPLAY_NAME} — Моя студия')
        self.setWindowIcon(studio_icon())
        self.setMinimumSize(1100,720)
        self.resize(1480,940)
        self.setStyleSheet(STYLE)
        palette=self.palette()
        palette.setColor(QPalette.ColorRole.Window,QColor('#171b24'))
        palette.setColor(QPalette.ColorRole.Base,QColor('#111722'))
        palette.setColor(QPalette.ColorRole.Text,QColor('#dce2ed'))
        palette.setColor(QPalette.ColorRole.WindowText,QColor('#dce2ed'))
        self.setPalette(palette)
        self._build_ui()
        self._build_shortcuts()
        self.engine.error_occurred.connect(lambda message:self.notify(message,error=True))
        self.audio_engine.error_occurred.connect(lambda message:self.notify(message,error=True))
        self.audio_engine.track_ready.connect(self.engine.record_audio,Qt.ConnectionType.DirectConnection)
        self.audio_engine.audio_ready.connect(self._audio_block,Qt.ConnectionType.DirectConnection)
        self.audio_engine.levels_changed.connect(self._capture_levels,Qt.ConnectionType.DirectConnection)
        self._rebuild_lists()
        self.preview.set_frame(self.engine.latest_frame)
        self.timer=QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(60)
        self.notify('Студия готова. Добавь источник и включи превью.')
        self.dirty=False
        self._update_title()

    @staticmethod
    def _button(text,callback,layout,object_name=None):
        widget=QPushButton(text)
        if object_name:widget.setObjectName(object_name)
        widget.clicked.connect(callback)
        layout.addWidget(widget)
        return widget

    @staticmethod
    def _label(text,object_name=None):
        label=QLabel(text)
        if object_name:label.setObjectName(object_name)
        return label

    def _card(self,title):
        frame=QFrame();frame.setObjectName('Card')
        layout=QVBoxLayout(frame);layout.setContentsMargins(12,12,12,12)
        layout.addWidget(self._label(title,'Section'))
        return frame,layout

    def _build_ui(self):
        root=QWidget();self.setCentralWidget(root)
        layout=QVBoxLayout(root);layout.setContentsMargins(12,10,12,8);layout.setSpacing(10)
        header=QFrame();header.setObjectName('Header')
        top=QHBoxLayout(header);top.setContentsMargins(12,10,12,10);top.setSpacing(9)
        logo=QLabel();logo.setPixmap(studio_icon().pixmap(34,34));top.addWidget(logo)
        top.addWidget(self._label(APP_DISPLAY_NAME,'Brand'))
        self.project_label=self._label('Моя студия','Muted');top.addWidget(self.project_label,1)
        self._button('Открыть',lambda:self.open_project(),top)
        self._button('Сохранить',lambda:self.save_project(),top)
        self.preview_button=self._button('Превью',self._toggle_preview,top,'Primary')
        self.virtual_button=self._button('Вирт. камера',self._toggle_virtual,top)
        self.record_button=self._button('● Запись',self._toggle_recording,top,'Record')
        self.record_button.setCheckable(True)
        layout.addWidget(header)
        self.main_split=QSplitter(Qt.Orientation.Vertical)
        horizontal=QSplitter(Qt.Orientation.Horizontal)
        left=QWidget();left_layout=QVBoxLayout(left);left_layout.setContentsMargins(0,0,0,0);left_layout.setSpacing(10)
        scenes,scene_box=self._card('СЦЕНЫ')
        self.scene_list=QListWidget();self.scene_list.setMinimumHeight(110)
        self.scene_list.currentItemChanged.connect(self._scene_selected)
        self.scene_list.itemDoubleClicked.connect(lambda _:self._rename_scene())
        scene_box.addWidget(self.scene_list,1)
        row=QHBoxLayout();self._button('+',self._new_scene_dialog,row);self._button('Копия',self._duplicate_scene,row);self._button('−',self._remove_scene,row);scene_box.addLayout(row)
        left_layout.addWidget(scenes,2)
        sources,source_box=self._card('ИСТОЧНИКИ')
        hint=self._label('Верхний слой — первым в списке','Muted');hint.setWordWrap(True);source_box.addWidget(hint)
        self.source_list=QListWidget();self.source_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.source_list.currentItemChanged.connect(self._source_selected)
        self.source_list.itemChanged.connect(self._source_visibility)
        self.source_list.model().rowsMoved.connect(self._source_reordered)
        self.source_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.source_list.customContextMenuRequested.connect(self._source_menu)
        source_box.addWidget(self.source_list,1)
        row=QHBoxLayout();self._button('+ Источник',self._add_source_menu,row);self._button('−',self._remove_source,row);source_box.addLayout(row)
        row=QHBoxLayout();self._button('↑',lambda:self._move_source(1),row);self._button('↓',lambda:self._move_source(-1),row);self._button('Копия',self._duplicate_source,row);source_box.addLayout(row)
        left_layout.addWidget(sources,3)
        left.setMinimumWidth(200);left.setMaximumWidth(320)
        horizontal.addWidget(left)
        center,center_layout=self._card('ХОЛСТ СТУДИИ')
        self.preview=PreviewCanvas();self.preview.selected.connect(self._select_source);self.preview.transformed.connect(self._transform_source)
        center_layout.addWidget(self.preview,1)
        row=QHBoxLayout();self.preview_info=self._label('1280 × 720  ·  20 FPS','Muted');row.addWidget(self.preview_info,1)
        guides=QCheckBox('Направляющие');guides.toggled.connect(self._guides);row.addWidget(guides)
        self._button('Сохранить повтор',self._save_replay,row)
        self._button('Проект…',self._project_settings,row)
        center_layout.addLayout(row)
        note=self._label('Выбери слой на холсте · перетаскивай для перемещения · правый нижний угол меняет размер','Muted');note.setWordWrap(True);center_layout.addWidget(note)
        horizontal.addWidget(center)
        self.properties=QTabWidget();self.properties.setMinimumWidth(320);self.properties.setMaximumWidth(460)
        self._build_source_properties()
        self._build_video_properties()
        self.audio_panel=AudioPanel(self);self.properties.addTab(self.audio_panel,'Звук')
        self.tools=ToolsPanel(self);self.properties.addTab(self.tools,'Инструменты')
        horizontal.addWidget(self.properties)
        horizontal.setStretchFactor(0,0);horizontal.setStretchFactor(1,1);horizontal.setStretchFactor(2,0)
        horizontal.setSizes([220,850,370]);self.main_split.addWidget(horizontal)
        bottom,bottom_layout=self._card('АУДИОМИКШЕР И САУНДБОРД')
        self.mixer=MixerPanel(self);self.mixer.setMinimumHeight(140);bottom_layout.addWidget(self.mixer)
        self.main_split.addWidget(bottom);self.main_split.setSizes([650,230]);layout.addWidget(self.main_split,1)
        self.status_label=self._label('Готово','Muted');layout.addWidget(self.status_label)
        menu=self.menuBar().addMenu('Проект')
        for title,callback in [('Новый проект',self._new_project),('Открыть…',lambda:self.open_project()),('Сохранить',lambda:self.save_project()),('Сохранить как…',lambda:self.save_project(save_as=True)),('Настройки…',self._project_settings)]:
            action=menu.addAction(title);action.triggered.connect(callback)
        edit=self.menuBar().addMenu('Правка');edit.addAction('Отменить',self._undo_change)
        view=self.menuBar().addMenu('Студия');view.addAction('Начать / остановить превью',self._toggle_preview);view.addAction('Сохранить повтор со звуком',self._save_replay)

    def _property_page(self,title):
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        content=QWidget();content.setObjectName('PropertyContent')
        content.setStyleSheet('QWidget#PropertyContent { background: #171b24; }')
        if not hasattr(self,'_property_contents'):self._property_contents=[]
        self._property_contents.append(content)
        form=QFormLayout(content);form.setContentsMargins(12,14,12,14);form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll.setWidget(content);self.properties.addTab(scroll,title)
        return form

    def _spin(self,low,high,step=.01):
        spin=QDoubleSpinBox();spin.setRange(low,high);spin.setSingleStep(step);spin.setDecimals(3 if step<1 else 0)
        return spin

    def _build_source_properties(self):
        form=self._property_page('Источник')
        self.source_title=self._label('Выбери источник','PageTitle');form.addRow(self.source_title)
        self.source_name=QLineEdit();self.source_name.editingFinished.connect(self._save_source_fields);form.addRow('Название',self.source_name)
        self.source_kind=self._label('—','Muted');form.addRow('Тип',self.source_kind)
        self.source_device=QLineEdit();self.source_device.setPlaceholderText('Индекс камеры/экрана, заголовок окна или ID гостя');self.source_device.editingFinished.connect(self._save_source_fields);form.addRow('Устройство / ID',self.source_device)
        self.guest_choice=QComboBox();self.guest_choice.addItem('Автоматически: первая камера','')
        self.guest_choice.setMinimumContentsLength(12)
        self.guest_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.guest_choice.currentIndexChanged.connect(self._guest_chosen)
        form.addRow('Камера гостя',self.guest_choice)
        self.source_path=QLineEdit();self.source_path.editingFinished.connect(self._save_source_fields)
        row=QHBoxLayout();row.addWidget(self.source_path,1);self._button('…',self._choose_source_path,row);form.addRow('Файл / URL',row)
        self.source_text=QPlainTextEdit();self.source_text.setMaximumHeight(95);self.source_text.textChanged.connect(self._save_source_fields);form.addRow('Текст',self.source_text)
        self.source_color=QLineEdit();self.source_color.editingFinished.connect(self._save_source_fields);form.addRow('Цвет #RRGGBB',self.source_color)
        self.font_size=QSpinBox();self.font_size.setRange(8,512);self.font_size.setValue(40);self.font_size.valueChanged.connect(self._save_source_fields);form.addRow('Размер шрифта',self.font_size)
        self.transforms={}
        for key,title,low,high in [('x','X',-8,8),('y','Y',-8,8),('width','Ширина',.001,8),('height','Высота',.001,8),('opacity','Непрозрачность',0,1),('rotation','Поворот, °',-360,360)]:
            spin=self._spin(low if key=='rotation' else low*100,high if key=='rotation' else high*100,1)
            spin.setSuffix(' °' if key=='rotation' else ' %')
            spin.valueChanged.connect(self._save_source_fields);self.transforms[key]=spin;form.addRow(title,spin)
        self.crop=[]
        for title in ('Обрезка слева','Обрезка сверху','Обрезка справа','Обрезка снизу'):
            spin=self._spin(0,99.9,.1);spin.setDecimals(1);spin.setSuffix(' %');spin.valueChanged.connect(self._save_source_fields);self.crop.append(spin);form.addRow(title,spin)
        crop_note=self._label('Обрезка применяется к камере, видео, экрану и изображению. Текст и цвет используют размер слоя.','Muted');crop_note.setWordWrap(True);form.addRow(crop_note)
        self.locked=QCheckBox('Закрепить положение');self.locked.toggled.connect(self._save_source_fields);form.addRow(self.locked)
        animation=QPushButton('Анимация и ключевые кадры…');animation.clicked.connect(self._animation_dialog);form.addRow(animation)
        hint=self._label('Положение и размер заданы в процентах холста: 100% = весь кадр. Для телефона включи сервер во вкладке «Инструменты → Сеть».','Muted');hint.setWordWrap(True);form.addRow(hint)

    def _build_video_properties(self):
        form=self._property_page('Видео')
        heading=self._label('Эффекты выбранного слоя','PageTitle');heading.setWordWrap(True);form.addRow(heading)
        supported=self._label('Эффекты доступны для камеры, видео, экрана и изображения. Для текста и цвета используй вкладку «Источник».','Muted');supported.setWordWrap(True);form.addRow(supported)
        self.effect_controls={}
        for key,title,low,high,default in self.EFFECTS:
            spin=self._spin(low,high,.05 if key=='gamma' else 1);spin.setValue(default)
            spin.valueChanged.connect(lambda value,key=key:self._set_effect(key,value));self.effect_controls[key]=spin;form.addRow(title,spin)
        self.mirror_h=QCheckBox('Отразить по горизонтали');self.mirror_h.toggled.connect(lambda value:self._set_effect('mirror_h',value));form.addRow(self.mirror_h)
        self.mirror_v=QCheckBox('Отразить по вертикали');self.mirror_v.toggled.connect(lambda value:self._set_effect('mirror_v',value));form.addRow(self.mirror_v)
        self.mask=QComboBox();self.mask.addItem('Без маски','')
        root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))/'masks'
        if root.exists():
            for path in sorted(root.iterdir()):
                if path.suffix.lower() in ('.png','.jpg','.jpeg'):self.mask.addItem(path.stem,path.stem)
        self.mask.currentIndexChanged.connect(self._save_mask);form.addRow('Сохранённые маски',self.mask)
        self.mask_tracking=QCheckBox('Следить за лицом');self.mask_tracking.setChecked(True);self.mask_tracking.toggled.connect(self._save_mask);form.addRow(self.mask_tracking)
        self.mask_fields={}
        for key,title,default in [('mask_x','Центр маски X',.5),('mask_y','Центр маски Y',.4),('mask_size','Размер маски',.25)]:
            spin=self._spin(0,200,1);spin.setSuffix(' %');spin.setValue(default*100);spin.valueChanged.connect(self._save_mask);self.mask_fields[key]=spin;form.addRow(title,spin)
        reset=QPushButton('Сбросить видеоэффекты');reset.clicked.connect(self._reset_effects);form.addRow(reset)
        note=self._label('Замена фона, автооператор и реактивные PNG-маски: «Инструменты → ИИ». Локальные модели выбираются там же.','Muted');note.setWordWrap(True);form.addRow(note)

    def _build_shortcuts(self):
        for sequence,callback in [('Ctrl+S',lambda:self.save_project()),('Ctrl+Shift+S',lambda:self.save_project(save_as=True)),('Ctrl+O',lambda:self.open_project()),('Ctrl+N',self._new_project),('Ctrl+Z',self._undo_change),('F9',self._toggle_recording),('F10',self._save_replay)]:
            shortcut=QShortcut(QKeySequence(sequence),self);shortcut.activated.connect(callback)
        for index in range(9):
            shortcut=QShortcut(QKeySequence(f'Alt+{index+1}'),self)
            shortcut.activated.connect(lambda index=index:self.select_scene(self.project.scenes[index]['id']) if index<len(self.project.scenes) else None)
            sound_shortcut=QShortcut(QKeySequence(f'Ctrl+Alt+{index+1}'),self)
            sound_shortcut.activated.connect(lambda index=index:self._sound_shortcut(index))

    def _sound_shortcut(self,index):
        sounds=self.project.data['soundboard']
        if index<len(sounds):
            self.mixer.sound_list.setCurrentRow(index)
            self.mixer._play_sound()

    def _selected_source(self):
        return self.project.find_source(self._selected_id) if self._selected_id else None

    def _rebuild_lists(self):
        self._loading=True
        self.scene_list.clear()
        for scene in self.project.scenes:
            item=QListWidgetItem(scene['name']);item.setData(Qt.ItemDataRole.UserRole,scene['id']);self.scene_list.addItem(item)
            if scene['id']==self.project.active_scene['id']:self.scene_list.setCurrentItem(item)
        self.source_list.clear()
        for source in reversed(self.project.active_scene['sources']):
            prefix='▣ ' if source.get('locked') else ''
            item=QListWidgetItem(prefix+source['name']);item.setData(Qt.ItemDataRole.UserRole,source['id'])
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if source.get('visible',True) else Qt.CheckState.Unchecked)
            self.source_list.addItem(item)
            if source['id']==self._selected_id:self.source_list.setCurrentItem(item)
        self.preview.sources=self.project.active_scene['sources'];self.preview.selected_id=self._selected_id;self.preview.update()
        self._loading=False
        self._load_source_fields()

    def add_scene(self,name):
        scene=self.project.add_scene(name);self.project.set_active(scene['id']);self._selected_id=None
        self.mark_changed(rebuild=True);return scene

    def add_source(self,kind,name=None,**kwargs):
        source=self.project.add_source(kind,name or self.SOURCE_NAMES.get(kind,kind),**kwargs)
        self._selected_id=source['id'];self.mark_changed(rebuild=True);return source

    def select_scene(self,scene_id):
        if scene_id==self.project.active_scene['id']:return
        self.project.set_active(scene_id);self._selected_id=None;self.mark_changed(rebuild=True)

    def _scene_selected(self,item,*_):
        if item and not self._loading:self.select_scene(item.data(Qt.ItemDataRole.UserRole))

    def _new_scene_dialog(self):
        name,ok=QInputDialog.getText(self,'Новая сцена','Название сцены:')
        if ok and name.strip():self.add_scene(name.strip())

    def _rename_scene(self):
        scene=self.project.active_scene
        name,ok=QInputDialog.getText(self,'Переименовать сцену','Название:',text=scene['name'])
        if ok and name.strip():scene['name']=name.strip();self.mark_changed(rebuild=True)

    def _duplicate_scene(self):
        scene=self.project.duplicate_scene(self.project.active_scene['id']);self.project.set_active(scene['id']);self._selected_id=None;self.mark_changed(rebuild=True)

    def _remove_scene(self):
        try:self.project.remove_scene(self.project.active_scene['id']);self._selected_id=None;self.mark_changed(rebuild=True)
        except ValueError as error:self.notify(str(error),error=True)

    def _source_selected(self,item,*_):
        if not self._loading:self._select_source(item.data(Qt.ItemDataRole.UserRole) if item else None)

    def _select_source(self,source_id):
        self._selected_id=source_id;self.preview.selected_id=source_id;self.preview.update()
        self.source_list.blockSignals(True)
        for index in range(self.source_list.count()):
            item=self.source_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole)==source_id:self.source_list.setCurrentItem(item);break
        self.source_list.blockSignals(False);self._load_source_fields()

    def _source_visibility(self,item):
        if self._loading:return
        source=self.project.find_source(item.data(Qt.ItemDataRole.UserRole))
        if source:source['visible']=item.checkState()==Qt.CheckState.Checked;self.mark_changed()

    def _source_reordered(self,*_):
        if self._loading:return
        sources=self.project.active_scene['sources'];by_id={source['id']:source for source in sources}
        sources[:]=[by_id[self.source_list.item(i).data(Qt.ItemDataRole.UserRole)] for i in reversed(range(self.source_list.count()))]
        self.mark_changed()

    def _move_source(self,offset):
        source=self._selected_source()
        if source:
            sources=self.project.active_scene['sources'];index=sources.index(source);target=max(0,min(len(sources)-1,index+offset))
            sources[index],sources[target]=sources[target],sources[index];self.mark_changed(rebuild=True)

    def _duplicate_source(self):
        source=self._selected_source()
        if source:
            new=copy.deepcopy(source);new['id']=uuid.uuid4().hex;new['name']+=' — копия'
            self.project.active_scene['sources'].append(new);self._selected_id=new['id'];self.mark_changed(rebuild=True)

    def _remove_source(self):
        if self._selected_id:self.project.remove_source(self._selected_id);self._selected_id=None;self.mark_changed(rebuild=True)

    def _source_menu(self,position):
        menu=QMenu(self);menu.addAction('Переименовать',lambda:self.source_name.setFocus());menu.addAction('Дублировать',self._duplicate_source)
        source=self._selected_source()
        if source:menu.addAction('Разблокировать' if source.get('locked') else 'Закрепить',lambda:self.locked.setChecked(not self.locked.isChecked()))
        menu.addAction('Убрать из сцены',self._remove_source);menu.exec(self.source_list.mapToGlobal(position))

    def _add_source_menu(self):
        menu=QMenu(self)
        for kind,title in self.SOURCE_NAMES.items():menu.addAction(title,lambda checked=False,kind=kind:self._source_dialog(kind))
        menu.exec(self.source_list.mapToGlobal(self.source_list.rect().bottomLeft()))

    def _source_dialog(self,kind):
        kwargs={}
        if kind=='image':
            path,_=QFileDialog.getOpenFileName(self,'Изображение','','Изображения (*.png *.jpg *.jpeg *.bmp *.webp)')
            if not path:return
            kwargs['path']=path
        elif kind in ('camera','screen'):
            index,ok=QInputDialog.getInt(self,self.SOURCE_NAMES[kind],'Индекс устройства (камера обычно 0, экран 0 — основной):',0,0,32)
            if not ok:return
            kwargs['device']=index
        elif kind=='window':
            title,ok=QInputDialog.getText(self,'Захват окна','Заголовок открытого окна (полностью или часть):')
            if not ok or not title.strip():return
            kwargs['device']=title.strip()
        elif kind=='network':
            address,ok=QInputDialog.getText(self,'Сетевой поток','URL видеопотока (RTSP / HTTP):')
            if not ok or not address.strip():return
            kwargs['path']=address.strip()
        elif kind=='guest':
            guest,ok=QInputDialog.getText(self,'Телефон / гость','ID гостя из активных подключений (пусто — первый гость):')
            if not ok:return
            kwargs['device']=guest.strip()
        elif kind=='text':kwargs.update(text='Новый текст',x=.1,y=.1,width=.8,height=.2,color='#ffffff',font_size=40)
        elif kind=='color':
            color,ok=QInputDialog.getText(self,'Цветной слой','Цвет #RRGGBB:',text='#172033')
            if not ok:return
            kwargs['color']=color
        try:self.add_source(kind,**kwargs)
        except Exception as error:self.notify(str(error),error=True)

    def _load_source_fields(self):
        source=self._selected_source();self._loading=True
        for content in self._property_contents:
            for widget in content.findChildren(QWidget):
                if not isinstance(widget,QLabel):widget.setEnabled(source is not None)
        self.source_title.setText(source['name'] if source else 'Выбери источник')
        self.source_kind.setText(self.SOURCE_NAMES.get(source['kind'],'—') if source else '—')
        self.source_name.setText(source['name'] if source else '')
        self.source_device.setText(str(source.get('device','')) if source else '')
        self.source_path.setText(source.get('path','') if source else '')
        self.source_text.setPlainText(source.get('text','') if source else '')
        self.source_color.setText(source.get('color','#ffffff') if source else '#ffffff')
        self.font_size.setValue(int(source.get('font_size',40)) if source else 40)
        for key,spin in self.transforms.items():
            value=float(source.get(key,1 if key in ('width','height','opacity') else 0)) if source else (1 if key in ('width','height','opacity') else 0)
            spin.setValue(value if key=='rotation' else value*100)
        for index,spin in enumerate(self.crop):spin.setValue(source.get('crop',[0,0,0,0])[index]*100 if source else 0)
        self.locked.setChecked(bool(source and source.get('locked',False)))
        for key,_,_,_,default in self.EFFECTS:self.effect_controls[key].setValue(source.get('effects',{}).get(key,default) if source else default)
        self.mirror_h.setChecked(bool(source and source.get('effects',{}).get('mirror_h',False)))
        self.mirror_v.setChecked(bool(source and source.get('effects',{}).get('mirror_v',False)))
        self.mask.setCurrentIndex(max(0,self.mask.findData(source.get('mask','') if source else '')))
        self.mask_tracking.setChecked(source.get('mask_tracking',True) if source else True)
        for key,spin in self.mask_fields.items():spin.setValue(100*(source.get(key,.5 if key=='mask_x' else (.4 if key=='mask_y' else .25)) if source else .5))
        video_supported=source is not None and source['kind'] not in ('text','color')
        for widget in self._property_contents[1].findChildren(QWidget):
            if not isinstance(widget,QLabel):widget.setEnabled(video_supported)
        for spin in self.crop:spin.setEnabled(video_supported)
        self.guest_choice.setEnabled(source is not None and source['kind']=='guest')
        self._refresh_guest_choices()
        self._loading=False

    def _refresh_guest_choices(self):
        source=self._selected_source()
        selected=str(source.get('device','')) if source and source['kind']=='guest' else ''
        server=self.server
        ids=list(server.guest_frames()) if server is not None and server.running else []
        self.guest_choice.blockSignals(True)
        self.guest_choice.clear();self.guest_choice.addItem('Автоматически: первая камера','')
        for guest_id in ids:self.guest_choice.addItem(guest_id,guest_id)
        if selected and selected not in ids:self.guest_choice.addItem(selected+' (не подключён)',selected)
        self.guest_choice.setCurrentIndex(max(0,self.guest_choice.findData(selected)))
        self.guest_choice.blockSignals(False)

    def _guest_chosen(self,*_):
        source=self._selected_source()
        if self._loading or source is None or source['kind']!='guest':return
        value=self.guest_choice.currentData() or ''
        self.source_device.setText(value)
        source['device']=value;self.mark_changed()

    def _save_source_fields(self,*_):
        source=self._selected_source()
        if self._loading or source is None:return
        candidate=copy.deepcopy(source)
        device=self.source_device.text().strip()
        try:
            if source['kind'] in ('camera','screen'):
                device=int(device)
                if device<0:raise ValueError('Индекс устройства должен быть положительным или нулём')
            candidate.update(name=self.source_name.text().strip() or self.SOURCE_NAMES[source['kind']],device=device,
                path=self.source_path.text().strip(),text=self.source_text.toPlainText(),color=self.source_color.text().strip(),
                font_size=self.font_size.value(),locked=self.locked.isChecked(),crop=[spin.value()/100 for spin in self.crop],
                **{key:spin.value() if key=='rotation' else spin.value()/100 for key,spin in self.transforms.items()})
            data=copy.deepcopy(self.project.data)
            for scene in data['scenes']:
                for index,item in enumerate(scene['sources']):
                    if item['id']==source['id']:scene['sources'][index]=candidate
            StudioProject._validate(data)
        except (ValueError,TypeError) as error:
            self.notify(str(error),error=True);return
        source.update(candidate);self.mark_changed()
        self.source_title.setText(source['name'])
        item=self.source_list.currentItem()
        if item:
            self.source_list.blockSignals(True);item.setText(('▣ ' if source['locked'] else '')+source['name']);self.source_list.blockSignals(False)

    def _choose_source_path(self):
        source=self._selected_source()
        if source and source['kind']=='network':
            address,ok=QInputDialog.getText(self,'Сетевой поток','URL:',text=source['path'])
            if ok:self.source_path.setText(address);self._save_source_fields()
        else:
            path,_=QFileDialog.getOpenFileName(self,'Выбрать файл источника','','Изображения (*.png *.jpg *.jpeg *.bmp *.webp);;Все файлы (*)')
            if path:self.source_path.setText(path);self._save_source_fields()

    def _transform_source(self,source_id,x,y,width,height):
        source=self.project.find_source(source_id)
        if source and not source.get('locked'):
            source.update(x=x,y=y,width=width,height=height)
            self.mark_changed();self._load_source_fields()

    def _set_effect(self,key,value):
        source=self._selected_source()
        if not self._loading and source:
            if key not in ('gamma','mirror_h','mirror_v'):
                value=int(value)
            source.setdefault('effects',{})[key]=value;self.mark_changed()

    def _save_mask(self,*_):
        source=self._selected_source()
        if not self._loading and source:
            source.update(mask=self.mask.currentData(),mask_tracking=self.mask_tracking.isChecked(),**{key:spin.value()/100 for key,spin in self.mask_fields.items()})
            self.mark_changed()

    def _reset_effects(self):
        source=self._selected_source()
        if source:source['effects']={};source['mask']='';self.mark_changed();self._load_source_fields()

    def _animation_dialog(self):
        source=self._selected_source()
        if source is None:self.notify('Сначала выбери источник');return
        dialog=AnimationDialog(source,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            source['animation']=copy.deepcopy(dialog.keyframes)
            self.mark_changed()

    def _guides(self,enabled):self.preview.guides=enabled;self.preview.update()

    def mark_changed(self,rebuild=False):
        if self._loading:return
        if self.project.data!=self._last_history:
            self._undo.append(self._last_history)
            self._undo=self._undo[-50:]
            self._last_history=copy.deepcopy(self.project.data)
        self.dirty=True
        self.engine.update_project(self.project)
        if rebuild:self._rebuild_lists()
        self.preview.sources=self.project.active_scene['sources']
        self.preview.update()
        if not self.engine.running:self.preview.set_frame(self.engine.latest_frame)
        self.preview_info.setText(f"{self.project.data['width']} × {self.project.data['height']}  ·  {self.project.data['fps']} FPS")
        self._update_title()

    def _undo_change(self):
        if not self._undo:return
        data=self._undo.pop();self.project.data=copy.deepcopy(data);self._last_history=copy.deepcopy(data)
        self._selected_id=None;self.dirty=True;self.engine.update_project(self.project);self._rebuild_lists()
        self.audio_panel.load_project();self.mixer.load_project();self.tools._project=None;self.tools.refresh();self._update_title()

    def _update_title(self):
        name=self.project.data['name'];self.project_label.setText(name+(' •' if self.dirty else ''))
        self.setWindowTitle(f'{APP_DISPLAY_NAME} — {name}'+(' *' if self.dirty else ''))

    def notify(self,text,error=False):
        text=str(text)
        line=time.strftime('%H:%M:%S')+(' · Ошибка: ' if error else ' · ')+text
        self.log_messages.append(line);self.log_messages=self.log_messages[-300:]
        if hasattr(self,'status_label'):
            self.status_label.setText(text)
            self.status_label.setStyleSheet('color:#f3b76b' if error else '')
            self.status_label.setToolTip(text)
        if hasattr(self,'tools'):self.tools.log(line)

    def run_job(self,work,on_done=None):
        job=BackgroundJob(work,self);self._jobs.add(job)
        if on_done:job.completed.connect(on_done)
        job.failed.connect(lambda text:self.notify(text,error=True))
        def finished():
            self._jobs.discard(job);job.deleteLater()
        job.finished.connect(finished);job.start();return job

    def _confirm_replace(self):
        if not self.dirty:return True
        choice=QMessageBox.question(self,'Сохранить изменения?','Проект изменён. Сохранить его перед продолжением?',QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Save)
        if choice==QMessageBox.StandardButton.Cancel:return False
        if choice==QMessageBox.StandardButton.Save:return self.save_project()
        return True

    def save_project(self,path=None,save_as=False):
        if isinstance(path,bool):path=None
        if path is None and (save_as or self.project_path is None):
            path,_=QFileDialog.getSaveFileName(self,'Сохранить проект',str(self.project_path or self.project.data['name']+'.cas.json'),'Проект студии (*.cas.json *.json)')
            if not path:return False
        path=Path(path or self.project_path)
        try:self.project.save(path)
        except Exception as error:self.notify(f'Не удалось сохранить проект: {error}',error=True);return False
        self.project_path=path;self.dirty=False;self._update_title();self.notify(f'Проект сохранён: {path.name}');return True

    def open_project(self,path=None):
        if isinstance(path,bool):path=None
        if path is None:
            path,_=QFileDialog.getOpenFileName(self,'Открыть проект','','Проект студии (*.cas.json *.json)')
            if not path:return False
        if not self._confirm_replace():return False
        try:
            project=StudioProject.load(path)
            return self.set_project(project,path,confirm=False)
        except Exception as error:self.notify(f'Не удалось открыть проект: {error}',error=True);return False

    def _new_project(self):
        if self._confirm_replace():self.set_project(StudioProject(),confirm=False)

    def set_project(self,project,path=None,confirm=True):
        try:
            StudioProject._validate(project.data)
        except (ValueError, TypeError, KeyError) as error:
            self.notify(f'Проект не применён: {error}',error=True)
            return False
        if confirm and not self._confirm_replace():return False
        if not self._stop_services():return False
        self.project=project;self.project_path=Path(path) if path else None;self._selected_id=None
        self._undo=[];self._last_history=copy.deepcopy(project.data)
        self.automation=AutomationEngine(project.data['rules']);self._automation_signature=''
        self.engine.update_project(project)
        self.audio_panel.load_project();self.mixer.load_project()
        self.tools._project=None;self.tools.refresh();self._rebuild_lists()
        self.preview.canvas_size=(project.data['width'],project.data['height']);self.preview.set_frame(self.engine.latest_frame)
        self.dirty=False;self._update_title();self.notify('Проект открыт; устройства и расширения выключены');return True

    def _stop_services(self):
        self.engine.allow_mediapipe_metrics=False
        if not self.engine.stop():
            self.notify('Обработка ещё завершает кадр; повтори действие через несколько секунд',error=True);return False
        self.audio_engine.stop()
        service,self.caption_service=self.caption_service,None
        if service:service.close()
        if self.server:self.server.stop();self.server=None
        self.engine.server=None
        for host in self.engine.extensions:host.close()
        self.engine.extensions.clear()
        return True

    def _project_settings(self):
        dialog=QDialog(self);dialog.setWindowTitle('Настройки проекта');form=QFormLayout(dialog)
        name=QLineEdit(self.project.data['name']);form.addRow('Название',name)
        width=QSpinBox();width.setRange(16,7680);width.setValue(self.project.data['width']);form.addRow('Ширина',width)
        height=QSpinBox();height.setRange(16,4320);height.setValue(self.project.data['height']);form.addRow('Высота',height)
        fps=QSpinBox();fps.setRange(1,120);fps.setValue(int(self.project.data['fps']));form.addRow('FPS',fps)
        note=QLabel('Размер и FPS меняются при выключенной записи и виртуальной камере.');note.setWordWrap(True);form.addRow(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);form.addRow(buttons)
        buttons.rejected.connect(dialog.reject)
        def save():
            if self.engine.recorder or self.engine.virtual_output:
                self.notify('Останови запись и виртуальную камеру перед изменением формата',error=True);return
            was_running=self.engine.running
            if was_running and not self.engine.stop():return
            self.project.data.update(name=name.text().strip() or 'Моя студия',width=width.value(),height=height.value(),fps=fps.value())
            self.mark_changed()
            if was_running:self.engine.start()
            dialog.accept()
        buttons.accepted.connect(save);dialog.exec()

    def _toggle_preview(self):
        if self.engine.running:
            if self.engine.recorder:
                self.notify('Сначала останови запись, чтобы выключить превью',error=True);return
            self.engine.stop()
        else:self.engine.start()
        self._update_buttons()

    def _toggle_virtual(self):
        if self.engine.virtual_output:self.engine.stop_virtual_camera()
        else:
            backend,ok=QInputDialog.getItem(self,'Виртуальная камера','Вывод:', ['OBS Virtual Camera','Unity Capture'],0,False)
            if not ok:return
            try:self.engine.start_virtual_camera('obs' if backend.startswith('OBS') else 'unitycapture')
            except Exception as error:self.notify(str(error),error=True)
        self._update_buttons()

    def _toggle_recording(self):
        recorder=self.engine.recorder
        if recorder:
            self.run_job(self.engine.stop_recording,self._recording_finished)
        else:
            directory=QFileDialog.getExistingDirectory(self,'Папка для нового сеанса записи',str(self.recording_directory or Path.home()/'Videos'))
            if not directory:self._update_buttons();return
            self.recording_directory=directory
            try:
                path=self.engine.start_recording(directory)
                self.notify(f'Запись включена: {path}. После остановки сохраняются MP4 и отдельные дорожки; результат сборки указан в манифесте сеанса.')
                for action in self.automation.trigger('record_start'):self.execute_command(action)
            except Exception as error:self.notify(str(error),error=True)
        self._update_buttons()

    def _recording_finished(self,result):
        self.notify(f'Запись сохранена: {result}')
        for action in self.automation.trigger('record_stop'):self.execute_command(action)
        self._update_buttons()

    def _save_replay(self):
        path,_=QFileDialog.getSaveFileName(self,'Сохранить повтор','replay.mp4','Видео MP4 со звуком (*.mp4);;Видео AVI без звука (*.avi)')
        if path:self.run_job(lambda:self.engine.replay.save(path,fps=self.project.data['fps']),lambda result:self.notify(f'Повтор сохранён: {result}'+(' (AVI без звука)' if Path(result).suffix.lower()=='.avi' else '')))

    def _capture_levels(self,levels):self._levels=dict(levels)

    def _audio_block(self,samples):
        self.engine.record_audio('Микс',samples)
        if self.engine.running:
            self.engine.replay.append_audio(samples)
        service=self.caption_service
        if service is not None:service.feed(samples)

    def execute_command(self,command):
        if not isinstance(command,dict):return
        kind=command.get('type');target=command.get('target')
        try:
            if kind=='scene':self.select_scene(target)
            elif kind=='mute':
                if target not in self.audio_engine.channels:raise ValueError('Звуковой канал не найден')
                if not isinstance(command.get('value'),bool):raise ValueError('Для mute нужно true/false')
                self.mixer._channel_change(target,'muted',command['value']);self.mixer.refresh()
            elif kind=='record':
                value=command.get('value')
                if not isinstance(value,bool):return
                if value and not self.engine.recorder:
                    if not self.recording_directory:self.notify('Сначала начни запись кнопкой и выбери папку; затем пульт сможет управлять ею',error=True);return
                    self.engine.start_recording(self.recording_directory)
                    for action in self.automation.trigger('record_start'):self.execute_command(action)
                elif not value and self.engine.recorder:self.run_job(self.engine.stop_recording,self._recording_finished)
            elif kind=='sound':
                sound=next((item for item in self.project.data['soundboard'] if item['id']==target),None)
                if sound is None:raise ValueError('Звук не найден')
                if not self.audio_engine.running:self.notify('Включи звук в студии перед запуском звука с пульта',error=True);return
                self.audio_engine.play_sound(sound['path'],sound.get('loop',False),sound.get('gain',1))
        except (ValueError,KeyError,TypeError) as error:self.notify(str(error),error=True)

    def _update_buttons(self):
        self.preview_button.setText('Остановить превью' if self.engine.running else 'Превью')
        self.virtual_button.setText('Выключить вирт. камеру' if self.engine.virtual_output else 'Вирт. камера')
        self.record_button.setChecked(self.engine.recorder is not None)
        self.record_button.setText('■ Стоп запись' if self.engine.recorder else '● Запись')

    def _tick(self):
        frame=self.engine.latest_frame
        if frame is not None:self.preview.set_frame(frame)
        if self.caption_service:self.engine.caption_text=self.caption_service.text
        else:self.engine.caption_text=''
        now=time.monotonic()
        rules=self.project.data['rules']
        signature=json.dumps(rules,sort_keys=True,ensure_ascii=False)
        if signature!=self._automation_signature:
            self.automation=AutomationEngine(rules);self._automation_signature=signature
        if self.engine.running:
            for command in self.automation.poll(now,speaking=self._levels.get('Микрофон',-120)>-35):self.execute_command(command)
        server=self.server
        if server is not None and server.running:
            for command in server.drain_commands():self.execute_command(command)
            for name,samples,rate in server.drain_audio():self.audio_engine.feed_external(name,samples,rate)
            if now-self._remote_time>=.2:
                server.set_state(dict(scenes=[{'id':scene['id'],'name':scene['name']} for scene in self.project.scenes],
                    active_scene_id=self.project.active_scene['id'],recording=self.engine.recorder is not None,
                    channels=[{'id':name,'name':name,'muted':value['muted']} for name,value in self.audio_engine.channels.copy().items()],
                    sounds=[{'id':sound['id'],'name':sound.get('name','Звук')} for sound in self.project.data['soundboard']]))
                server.set_frame(frame);self._remote_time=now
        if now-self._ui_time>.5:
            self._refresh_guest_choices()
            self.tools.refresh();self.audio_panel.refresh();self.mixer.refresh();self._update_buttons();self._ui_time=now

    def closeEvent(self,event):
        if self._jobs:
            self.notify('Дождись завершения фоновой операции перед закрытием',error=True);event.ignore();return
        if not self._confirm_replace():event.ignore();return
        if not self._stop_services():event.ignore();return
        self._closing=True;self.timer.stop();event.accept()
