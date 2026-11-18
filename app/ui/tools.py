"""Explicit controls for optional local models, links, rules and extensions."""
import copy
import json
import math
from pathlib import Path
import socket
from urllib.parse import urlsplit, urlunsplit

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from app.studio.network import StudioServer
from app.studio.project import StudioProject
from app.studio.vision import MODEL_SETUP, VisionProcessor
from app.ui.rules import RuleEditor


def validate_rules(rules):
    if not isinstance(rules,list) or len(rules)>1000:
        raise ValueError('Правила должны быть JSON-списком, не более 1000 правил')
    for rule in rules:
        if not isinstance(rule,dict) or rule.get('event') not in {'timer','speech_start','speech_end','record_start','record_stop'}:
            raise ValueError('Событие: timer, speech_start, speech_end, record_start или record_stop')
        if 'enabled' in rule and not isinstance(rule['enabled'],bool):
            raise ValueError('enabled должен быть true или false')
        if rule['event']=='timer':
            interval=rule.get('interval',1)
            if isinstance(interval,bool) or not isinstance(interval,(int,float)) or not math.isfinite(interval) or not .1<=interval<=86400:
                raise ValueError('interval: число секунд от 0.1 до 86400')
        action=rule.get('action')
        if not isinstance(action,dict) or action.get('type') not in {'scene','mute','record','sound'}:
            raise ValueError('action.type: scene, mute, record или sound')
        if set(action)-{'type','target','value'}:
            raise ValueError('В action допустимы только type, target и value')
        if action['type'] in {'scene','mute','sound'} and (not isinstance(action.get('target'),str) or not 1<=len(action['target'])<=128):
            raise ValueError('action.target: ID сцены, звукового канала или звука')
        if action['type'] in {'mute','record'} and not isinstance(action.get('value'),bool):
            raise ValueError('Для mute и record укажи value: true или false')
    return copy.deepcopy(rules)


class ToolsPanel(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window=window
        self._project=None
        self._loading=False
        self._log=[]
        self.tabs=QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        layout=QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.addWidget(self.tabs)
        self._build_network()
        self._build_vision()
        self._build_captions()
        self._build_rules()
        self._build_files()
        self._build_status()
        self.refresh()

    def _page(self,title):
        content=QWidget()
        content.setObjectName('ToolsContent')
        content.setStyleSheet('QWidget#ToolsContent { background: #171b24; }')
        layout=QVBoxLayout(content)
        layout.setContentsMargins(10,12,10,12)
        layout.setSpacing(10)
        scroll=QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.tabs.addTab(scroll,title)
        return layout

    def _label(self,layout,text):
        label=QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)
        return label

    def _button(self,layout,text,callback):
        button=QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _field(self,layout,title,directory=False,filter='Все файлы (*)'):
        self._label(layout,title)
        row=QHBoxLayout()
        edit=QLineEdit()
        choose=QPushButton('…')
        choose.setMaximumWidth(36)
        row.addWidget(edit,1)
        row.addWidget(choose)
        layout.addLayout(row)
        def select():
            if directory:
                path=QFileDialog.getExistingDirectory(self,title,edit.text())
            else:
                path,_=QFileDialog.getOpenFileName(self,title,edit.text(),filter)
            if path:
                edit.setText(path)
                edit.editingFinished.emit()
        choose.clicked.connect(select)
        return edit

    def _job(self,work,on_done=None):
        def safe_work():
            try:
                return True,work()
            except Exception as error:
                return False,str(error)
        def finished(result):
            ok,value=result
            if ok:
                if on_done:
                    on_done(value)
            else:
                self.window.notify(value,error=True)
            self.refresh()
        self.window.run_job(safe_work,finished)

    def _build_network(self):
        box=self._page('Сеть')
        self._label(box,'Телефон, гости и веб-пульт')
        self._label(box,'Сервер выключен до нажатия кнопки. Ссылка разрешает просмотр и управление студией; передавай её только своим гостям.')
        self.lan=QCheckBox('Разрешить подключения из локальной сети')
        self.tls=QCheckBox('HTTPS для камеры телефона')
        self.port=QSpinBox();self.port.setRange(0,65535);self.port.setSpecialValueText('Свободный порт')
        box.addWidget(self.lan);box.addWidget(self.tls)
        self._label(box,'Порт');box.addWidget(self.port)
        self.tls_cache=self._field(box,'Папка локального HTTPS-сертификата',directory=True)
        self.tls_cache.setText(str(Path.cwd()/'.studio-cache'/'tls'))
        self._label(box,'STUN / TURN для подключения через Интернет (необязательно)')
        self.ice_urls=QPlainTextEdit()
        self.ice_urls.setPlaceholderText('Один URL в строке, например:\nturns:relay.example:5349?transport=tcp')
        self.ice_urls.setMaximumHeight(95)
        box.addWidget(self.ice_urls)
        self._label(box,'Логин TURN')
        self.ice_username=QLineEdit();box.addWidget(self.ice_username)
        self._label(box,'Пароль TURN (только до закрытия проекта)')
        self.ice_password=QLineEdit();self.ice_password.setEchoMode(QLineEdit.EchoMode.Password);box.addWidget(self.ice_password)
        self._label(box,'Пустой список — только прямое соединение, без внешних серверов. При запуске WebRTC используется выбранный STUN/TURN. Пароль не сохраняется в проект; владелец ссылки гостя получит данные подключения к relay. HTTPS-адрес студии должен быть доступен гостю отдельно.')
        self.server_button=self._button(box,'Включить сервер',self._toggle_server)
        self.remote_url=QLineEdit();self.remote_url.setReadOnly(True)
        self.guest_url=QLineEdit();self.guest_url.setReadOnly(True)
        self._label(box,'Пульт');box.addWidget(self.remote_url)
        self._button(box,'Скопировать ссылку пульта',lambda:QApplication.clipboard().setText(self.remote_url.text()))
        self._button(box,'Открыть пульт',lambda:self._open(self.remote_url.text()))
        self._label(box,'Камера гостя / телефона');box.addWidget(self.guest_url)
        self._button(box,'Скопировать ссылку гостя',lambda:QApplication.clipboard().setText(self.guest_url.text()))
        self._button(box,'Открыть страницу гостя',lambda:self._open(self.guest_url.text()))
        self.network_status=self._label(box,'Сервер выключен')
        self._label(box,'Подключённые камеры гостей · ID можно скопировать')
        self.guest_ids=QPlainTextEdit();self.guest_ids.setReadOnly(True);self.guest_ids.setMaximumHeight(100)
        box.addWidget(self.guest_ids)
        self._label(box,'В LAN на телефоне открой HTTPS-ссылку и доверь локальному сертификату на своём устройстве. Камера включается только после разрешения браузера. HTTP работает с камерой только на localhost. Для Интернета нужны доступный сервер и, при сложном NAT, отдельно настроенный TURN relay; встроенного облачного relay нет.')
        box.addStretch()

    def _open(self,url):
        if url and QUrl(url).isValid():
            QDesktopServices.openUrl(QUrl(url))

    def _toggle_server(self):
        server=self.window.server
        if server is not None and server.running:
            self.window.engine.server=None
            self.window.server=None
            self._job(server.stop,lambda _:self.window.notify('Сетевой сервер выключен'))
            return
        host='0.0.0.0' if self.lan.isChecked() else '127.0.0.1'
        port,tls=self.port.value(),self.tls.isChecked()
        cache=Path(self.tls_cache.text().strip() or str(Path.cwd()/'.studio-cache'/'tls'))
        urls=[line.strip() for line in self.ice_urls.toPlainText().splitlines() if line.strip()]
        ice_servers=[]
        if urls:
            ice_servers=[dict(urls=url,username=self.ice_username.text().strip(),credential=self.ice_password.text()) for url in urls]
        try:
            server=StudioServer(cache_dir=cache,ice_servers=ice_servers)
        except ValueError as error:
            self.window.notify(str(error),error=True)
            return
        requested_project=self.window.project
        self.server_button.setEnabled(False)
        def start():
            try:
                server.start(host=host,port=port,tls=tls)
                return server
            except Exception:
                server.stop()
                raise
        def done(server):
            if self.window.project is not requested_project:
                server.stop()
                self.window.notify('Запуск сервера отменён: текущий проект изменился')
                return
            self.window.server=server
            self.window.engine.server=server
            # Relay credentials remain runtime-only and are never exported in a project.
            self.window.project.data['network']={'lan':host=='0.0.0.0','tls':tls,'port':port,
                'ice_servers':[{key:value for key,value in item.items() if key!='credential'} for item in ice_servers]}
            self.window.mark_changed()
            self.window.notify('Сетевой сервер включён')
        def safe_start():
            try:
                return True,start()
            except Exception as error:
                return False,str(error)
        def finished(result):
            self.server_button.setEnabled(True)
            if result[0]:done(result[1])
            else:self.window.notify(result[1],error=True)
            self.refresh()
        self.window.run_job(safe_start,finished)

    def _build_vision(self):
        box=self._page('ИИ')
        self.model_metrics=QCheckBox('Разрешить техническую статистику MediaPipe')
        self.model_metrics.setToolTip('Согласие действует только в текущем проекте и не сохраняется в файле.')
        self.model_metrics.toggled.connect(lambda allowed:setattr(self.window.engine,'allow_mediapipe_metrics',allowed))
        box.addWidget(self.model_metrics)
        self._label(box,'MediaPipe обрабатывает кадры локально, но SDK отправляет техническую статистику Google. Для замены фона и мимики нужно отдельное согласие выше. Без него доступно базовое слежение за лицом. Согласие из импортированного проекта не принимается.')
        self._label(box,'Фон, маски и автооператор')
        self.background=QComboBox()
        for name,key in [('Без замены','off'),('Размытие','blur'),('Цвет','color'),('Изображение','image')]:
            self.background.addItem(name,key)
        box.addWidget(self.background)
        self.background_color=QLineEdit('#172033');self._label(box,'Цвет фона (#RRGGBB)');box.addWidget(self.background_color)
        self.vision_fields={}
        fields=[('background_path','Изображение фона','Изображения (*.png *.jpg *.jpeg *.webp *.bmp)'),
                ('segmentation_model','Модель фона Selfie Segmenter (.tflite)','MediaPipe (*.tflite)'),
                ('face_model','Модель лица Face Landmarker (.task)','MediaPipe (*.task)'),
                ('mask_path','Обычная маска PNG','Изображения (*.png *.webp *.jpg)'),
                ('mask_mouth_path','Маска при открытом рте','Изображения (*.png *.webp)'),
                ('mask_blink_path','Маска при моргании','Изображения (*.png *.webp)')]
        for key,title,filter in fields:
            self.vision_fields[key]=self._field(box,title,filter=filter)
            self.vision_fields[key].editingFinished.connect(self._save_vision)
        self.mask_scale=QDoubleSpinBox();self.mask_scale.setRange(.1,6);self.mask_scale.setSingleStep(.1);self.mask_scale.setValue(1)
        self._label(box,'Масштаб маски');box.addWidget(self.mask_scale)
        self.auto_frame=QCheckBox('Автооператор: плавно следить за лицом')
        self.reactive_mask=QCheckBox('Маска реагирует на рот и моргание')
        box.addWidget(self.auto_frame);box.addWidget(self.reactive_mask)
        self._label(box,'Режим автооператора')
        self.framing_mode=QComboBox();self.framing_mode.addItem('Общий план','group');self.framing_mode.addItem('Говорящий','speaker')
        box.addWidget(self.framing_mode)
        self.framing_mode.currentIndexChanged.connect(self._save_vision)
        self.vision_status=self._label(box,'Без моделей фон остаётся обычным. Haar отслеживает положение лица, Face Landmarker добавляет наклон и мимику.')
        self._label(box,'Модели скачиваются отдельно. Для фона выбери Selfie Segmenter или Selfie Multiclass, для лица — Face Landmarker. Студия обрабатывает кадры локально.')
        self._button(box,'Официальные модели фона',lambda:self._open(MODEL_SETUP['segmentation']))
        self._button(box,'Официальная модель лица',lambda:self._open(MODEL_SETUP['face']))
        self.background.currentIndexChanged.connect(self._save_vision)
        self.background_color.editingFinished.connect(self._save_vision)
        self.mask_scale.valueChanged.connect(self._save_vision)
        self.auto_frame.toggled.connect(self._save_vision)
        self.reactive_mask.toggled.connect(self._save_vision)
        box.addStretch()

    def _save_vision(self,*_):
        if self._loading:
            return
        color=self.background_color.text().strip()
        try:
            if len(color)!=7 or not color.startswith('#'):
                raise ValueError()
            int(color[1:],16)
        except ValueError:
            self.window.notify('Цвет фона: # и шесть шестнадцатеричных цифр, например #172033',error=True)
            return
        self.window.project.data['vision']={**{key:edit.text().strip() for key,edit in self.vision_fields.items()},
            'background':self.background.currentData(),'background_color':color,
            'mask_scale':self.mask_scale.value(),'auto_frame':self.auto_frame.isChecked(),'reactive_mask':self.reactive_mask.isChecked(),
            'framing_mode':self.framing_mode.currentData()}
        self.window.mark_changed()

    def _build_captions(self):
        box=self._page('Текст')
        self._label(box,'Локальные субтитры Vosk')
        self.caption_model=self._field(box,'Папка распакованной модели Vosk',directory=True)
        self.caption_button=self._button(box,'Включить субтитры',self._toggle_captions)
        self.caption_text=self._label(box,'Субтитры выключены')
        self._button(box,'Сохранить распознанный текст SRT',self._save_srt)
        self._label(box,'Субтитры получают звук из включённого микшера. Модель загружается локально при поступлении звука; речь не отправляется в Интернет.')
        self._button(box,'Официальные модели Vosk',lambda:self._open('https://alphacephei.com/vosk/models'))
        box.addStretch()

    def _toggle_captions(self):
        service=self.window.caption_service
        if service is not None:
            self.window.caption_service=None
            self.window.engine.caption_text=''
            self._job(service.close,lambda _:self.window.notify('Субтитры выключены'))
        else:
            from app.audio.captions import CaptionService
            try:
                service=CaptionService(self.caption_model.text().strip(),sample_rate=self.window.audio_engine.sample_rate)
            except Exception as error:
                self.window.notify(str(error),error=True)
                return
            self.window.caption_service=service
            self.window.project.data['captions']={'model_path':service.model_path}
            self.window.mark_changed()
            self.window.notify('Субтитры включены; включи звук для распознавания')
        self.refresh()

    def _save_srt(self):
        service=self.window.caption_service
        if service is None:
            self.window.notify('Сначала включи субтитры и распознай речь',error=True)
            return
        path,_=QFileDialog.getSaveFileName(self,'Сохранить субтитры','subtitles.srt','Субтитры (*.srt)')
        if path:
            self._job(lambda:service.save_srt(path),lambda _:self.window.notify(f'Субтитры сохранены: {path}'))

    def _build_rules(self):
        box=self._page('Правила')
        self._label(box,'Автоматические действия')
        self.rule_editor=RuleEditor(self.window)
        box.addWidget(self.rule_editor)
        box.addStretch()

    def _build_files(self):
        box=self._page('Файлы')
        self._label(box,'Переносимая библиотека проектов')
        self._button(box,'Экспортировать проект с ресурсами в ZIP',self._export_bundle)
        self._button(box,'Импортировать проект из ZIP',self._import_bundle)
        self._label(box,'Архив содержит проект и локальные файлы. При импорте студия проверяет пути и не запускает расширения. Камеры и модели на другом компьютере могут потребовать выбора.')
        self._label(box,'Локальное Python-расширение')
        self.extension_path=self._field(box,'Python-файл с process_frame(frame)',filter='Python (*.py)')
        self._label(box,'Файл запускается отдельным процессом с лимитом времени. Это не защитная песочница: расширение получает твои права на файлы и сеть. Включай только код, которому доверяешь.')
        self.extension_button=self._button(box,'Включить выбранное расширение',self._toggle_extension)
        self.extension_status=self._label(box,'Расширения выключены')
        box.addStretch()

    def _export_bundle(self):
        path,_=QFileDialog.getSaveFileName(self,'Экспортировать проект',self.window.project.data['name']+'.zip','Проект с ресурсами (*.zip)')
        if path:
            snapshot=StudioProject();snapshot.data=copy.deepcopy(self.window.project.data)
            self._job(lambda:snapshot.export_bundle(path),lambda _:self.window.notify(f'Архив сохранён: {path}'))

    def _import_bundle(self):
        path,_=QFileDialog.getOpenFileName(self,'Импортировать проект','','Проект с ресурсами (*.zip)')
        if not path:return
        destination=QFileDialog.getSaveFileName(self,'Новая папка для распаковки',str(Path(path).with_suffix('')),'Папка проекта (*)')[0]
        if destination:
            def done(project):
                self.window.set_project(project,str(Path(destination)/'project.json'))
                self.window.notify('Проект импортирован; устройства и расширения выключены')
            self._job(lambda:StudioProject.import_bundle(path,destination),done)

    def _toggle_extension(self):
        hosts=list(self.window.engine.extensions)
        if hosts:
            self.window.engine.extensions.clear()
            self._job(lambda:[host.close() for host in hosts],lambda _:self.window.notify('Расширения выключены'))
            return
        path=self.extension_path.text().strip()
        if not path or not Path(path).is_file() or Path(path).suffix.lower()!='.py':
            self.window.notify('Выбери существующий Python-файл расширения',error=True)
            return
        self.extension_button.setEnabled(False)
        requested_project=self.window.project
        def load():
            from app.studio.extensions import ExtensionHost
            host=ExtensionHost(path)
            return host
        def done(host):
            if self.window.project is not requested_project:
                host.close()
                self.extension_button.setEnabled(True)
                self.window.notify('Запуск расширения отменён: текущий проект изменился')
                return
            if host.error:
                self.window.notify(host.error,error=True)
                host.close()
            else:
                self.window.engine.extensions.append(host)
                self.window.notify('Расширение включено')
            self.extension_button.setEnabled(True)
        self._job(load,done)

    def _build_status(self):
        box=self._page('Статус')
        self.stats=self._label(box,'')
        self.adaptive=QCheckBox('Снижать качество обработки всех выходов при нагрузке')
        self.adaptive.toggled.connect(lambda value:setattr(self.window.engine,'auto_quality',value))
        box.addWidget(self.adaptive)
        self._label(box,'Журнал этой панели')
        self.journal=QPlainTextEdit();self.journal.setReadOnly(True);self.journal.setMinimumHeight(220)
        box.addWidget(self.journal)
        self._button(box,'Скопировать диагностику',lambda:QApplication.clipboard().setText(self.stats.text()+'\n'+self.journal.toPlainText()))
        self._label(box,'Адаптивное качество меняет детализацию всего кадра: превью, записи и виртуальной камеры. FPS и задержка зависят от разрешения, моделей, устройств и числа источников.')
        box.addStretch()

    def log(self,text):
        self._log.append(str(text))
        self._log=self._log[-300:]
        self.journal.setPlainText('\n'.join(self._log))
        self.journal.verticalScrollBar().setValue(self.journal.verticalScrollBar().maximum())

    def refresh(self):
        window=self.window
        self.model_metrics.setChecked(bool(getattr(window.engine,'allow_mediapipe_metrics',False)))
        if self._project is not window.project:
            self._project=window.project
            self._loading=True
            vision=VisionProcessor.DEFAULTS | window.project.data.get('vision',{})
            index=self.background.findData(vision['background'])
            self.background.setCurrentIndex(max(0,index))
            self.background_color.setText(vision['background_color'])
            for key,edit in self.vision_fields.items():edit.setText(str(vision[key]))
            self.mask_scale.setValue(float(vision['mask_scale']))
            self.auto_frame.setChecked(bool(vision['auto_frame']))
            self.framing_mode.setCurrentIndex(max(0,self.framing_mode.findData(vision.get('framing_mode','group'))))
            self.reactive_mask.setChecked(bool(vision['reactive_mask']))
            self.caption_model.setText(window.project.data.get('captions',{}).get('model_path',''))
            network=window.project.data.get('network',{})
            self.lan.setChecked(bool(network.get('lan',False)))
            self.tls.setChecked(bool(network.get('tls',False)))
            self.port.setValue(int(network.get('port',0)))
            ice=network.get('ice_servers',[])
            urls=[url for item in ice for url in ([item['urls']] if isinstance(item.get('urls'),str) else item.get('urls',[]))]
            self.ice_urls.setPlainText('\n'.join(urls))
            self.ice_username.setText(ice[0].get('username','') if ice else '')
            self.ice_password.clear()
            self._loading=False
        server=window.server
        running=server is not None and server.running
        self.server_button.setText('Выключить сервер' if running else 'Включить сервер')
        for control in (self.lan,self.tls,self.port,self.ice_urls,self.ice_username,self.ice_password):control.setEnabled(not running)
        url=server.url if running else ''
        if running and self.lan.isChecked():
            try:
                candidates=[item[4][0] for item in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET) if not item[4][0].startswith('127.')]
                if candidates:
                    parts=urlsplit(url)
                    url=urlunsplit((parts.scheme,f'{candidates[0]}:{parts.port}',parts.path,parts.query,''))
            except OSError:pass
        self.remote_url.setText(url)
        self.guest_url.setText(url.replace('/?','/guest?') if url else '')
        certificate=f'\nСертификат: {server.certificate_path}' if running and server.certificate_path else ''
        self.network_status.setText(('Сервер включён'+certificate) if running else 'Сервер выключен')
        ids=list(server.guest_frames()) if running else []
        self.guest_ids.setPlainText('\n'.join(ids) if ids else 'Нет подключённых камер')
        service=window.caption_service
        self.caption_button.setText('Выключить субтитры' if service else 'Включить субтитры')
        self.caption_text.setText((service.error or service.text or 'Ожидание звука из микшера…') if service else 'Субтитры выключены')
        stats=window.engine.stats
        self.vision_status.setText(stats.get('vision','') or 'Модели не загружены: включи превью для обработки')
        self.stats.setText(f"FPS: {stats.get('fps',0)}\nОбработка: {stats.get('render_ms',0)} мс\nCPU: {stats.get('cpu_percent','—')}%\nПамять: {stats.get('memory_mb','—')} МБ\nПропуски: {stats.get('dropped',0)}\nИсточники: {stats.get('sources',0)}\nКачество: {stats.get('quality',100)}%\nПовторы: {stats.get('replay_mb',0)} МБ\nВидео: {stats.get('vision','')}\nСеть: {'включена' if running else 'выключена'}")
        self.adaptive.setChecked(bool(window.engine.auto_quality))
        self.extension_button.setText('Выключить расширения' if window.engine.extensions else 'Включить выбранное расширение')
        self.extension_status.setText('\n'.join(host.error or 'Расширение работает' for host in window.engine.extensions) or 'Расширения выключены')
        self.rule_editor.refresh()
