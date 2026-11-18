"""Versioned studio projects and safely portable resource bundles."""
import copy
import json
import math
import ntpath
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

KINDS = {'camera', 'screen', 'window', 'image', 'text', 'color', 'network', 'guest'}
MAX_PROJECT_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_BYTES = 512 * 1024 * 1024


def _id():
    return uuid.uuid4().hex


def _number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'Недопустимое значение {name}')


def _path(value):
    if not isinstance(value, str) or '\x00' in value or len(value) > 32767:
        raise ValueError('Недопустимый путь')


def _string(value, name, limit=1000, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value):
        raise ValueError(f'Недопустимое поле {name}')


def _options(settings, *, strings=(), booleans=(), numbers=None, integers=None, paths=(), enums=None):
    if not isinstance(settings, dict):
        raise ValueError('Настройки должны быть объектом JSON')
    for key in strings:
        if key in settings:
            _string(settings[key], key)
    for key in booleans:
        if key in settings and not isinstance(settings[key], bool):
            raise ValueError(f'{key} должно быть true/false')
    for key, (low, high) in (numbers or {}).items():
        if key in settings:
            _number(settings[key], low, high, key)
    for key, (low, high) in (integers or {}).items():
        if key in settings:
            _number(settings[key], low, high, key)
            if not isinstance(settings[key], int):
                raise ValueError(f'{key} должно быть целым числом')
    for key in paths:
        if key in settings:
            _path(settings[key])
    for key, choices in (enums or {}).items():
        if key in settings and (not isinstance(settings[key], str) or settings[key] not in choices):
            raise ValueError(f'Недопустимый вариант {key}')


def _validate_settings(data):
    audio = data['audio']
    _options(audio, booleans=('system_audio', 'ducking', 'enabled'),
             numbers={'duck_gain': (0, 1)}, integers={'sample_rate': (8000, 192000), 'blocksize': (16, 65536)})
    for key in ('input_device', 'output_device'):
        if key in audio and audio[key] is not None:
            if isinstance(audio[key], str):
                _string(audio[key], key)
            else:
                _options({key: audio[key]}, integers={key: (-1, 100000)})
    effects = audio.get('effects', {})
    _options(effects, booleans=('telephone',), numbers={
        'pitch': (.5, 2), 'gain': (0, 20), 'gate_db': (-120, 0),
        'low_db': (-24, 24), 'mid_db': (-24, 24), 'high_db': (-24, 24),
        'limiter': (.01, 1), **{key: (0, 1) for key in ('reverb', 'noise_reduction', 'compressor', 'robot', 'distortion', 'wet')}})
    if 'chain' in effects:
        chain = effects['chain']
        allowed = {'gate', 'noise_reduction', 'equalizer', 'compressor', 'pitch', 'robot', 'distortion', 'telephone', 'reverb', 'gain'}
        if not isinstance(chain, list) or any(not isinstance(item, str) or item not in allowed for item in chain) or len(set(chain)) != len(chain):
            raise ValueError('Недопустимая цепочка аудиоэффектов')
    channels = audio.get('channels', {})
    if not isinstance(channels, dict) or len(channels) > 200:
        raise ValueError('Недопустимые аудиоканалы')
    for name, channel in channels.items():
        _string(name, 'channel.name', 200, required=True)
        _options(channel, booleans=('muted', 'solo', 'record', 'output'), numbers={'gain': (0, 20)})

    vision = data['vision']
    _options(vision, booleans=('auto_frame', 'reactive_mask', 'enabled'),
             strings=('background_color',), numbers={'mask_scale': (.1, 6)},
             paths=('background_path', 'segmentation_model', 'face_model', 'mask_path', 'mask_mouth_path', 'mask_blink_path'),
             enums={'background': {'off', 'blur', 'color', 'image'}, 'framing_mode': {'group', 'speaker'}})
    if 'background_color' in vision and not re.fullmatch(r'#[0-9a-fA-F]{6}', vision['background_color']):
        raise ValueError('Цвет фона должен быть #RRGGBB')
    _options(data['captions'], strings=('language', 'text'), booleans=('enabled',),
             paths=('model_path', 'path', 'output_path'), integers={'sample_rate': (8000, 192000)})
    _options(data['network'], strings=('host', 'token'), booleans=('lan', 'tls', 'enabled'),
             integers={'port': (0, 65535)}, paths=('cache_dir', 'certificate_path', 'key_path'))
    if 'ice_servers' in data['network']:
        from app.studio.network import StudioServer
        StudioServer.validate_ice_servers(data['network']['ice_servers'])
    if 'record_sources' in data and not isinstance(data['record_sources'], bool):
        raise ValueError('record_sources должно быть true/false')

    if len(data['soundboard']) > 500:
        raise ValueError('Слишком много звуков')
    sound_ids = set()
    for sound in data['soundboard']:
        _options(sound, strings=('name',), booleans=('loop',), numbers={'gain': (0, 20)}, paths=('path',))
        _string(sound.get('id'), 'sound.id', 128, required=True)
        _path(sound.get('path'))
        if sound['id'] in sound_ids:
            raise ValueError('Повторяющийся ID звука')
        sound_ids.add(sound['id'])

    if len(data['rules']) > 1000:
        raise ValueError('Слишком много правил')
    for rule in data['rules']:
        _options(rule, booleans=('enabled',), numbers={'interval': (.1, 86400)},
                 enums={'event': {'timer', 'speech_start', 'speech_end', 'record_start', 'record_stop'}})
        if 'event' not in rule:
            raise ValueError('Укажи событие правила')
        action = rule.get('action')
        _options(action, enums={'type': {'scene', 'mute', 'record', 'sound'}})
        if 'type' not in action:
            raise ValueError('Укажи тип действия правила')
        if action['type'] in {'scene', 'mute', 'sound'}:
            _string(action.get('target'), 'action.target', 128, required=True)
        elif 'target' in action:
            _string(action['target'], 'action.target', 128)
        if action['type'] in {'mute', 'record'} and not isinstance(action.get('value'), bool):
            raise ValueError('action.value должно быть true/false')
    if len(data['extensions']) > 100:
        raise ValueError('Слишком много расширений')
    for extension in data['extensions']:
        if isinstance(extension, str):
            _path(extension)
        else:
            _options(extension, paths=('path',), strings=('name',), booleans=('enabled',), numbers={'timeout': (.01, 30)})


def _source(kind, name=None, **kwargs):
    if kind not in KINDS:
        raise ValueError('Неизвестный тип источника')
    source = dict(id=_id(), name=name or kind, kind=kind, device=0, path='', text='',
                  color='#172033', x=0, y=0, width=1, height=1, opacity=1,
                  visible=True, locked=False, rotation=0, crop=[0, 0, 0, 0],
                  effects={}, animation=[])
    source.update(kwargs)
    return source


def _atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _resource_fields(data):
    """Yield mutable dictionaries with explicitly named local file references."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key in {'path', 'model_path', 'background_path', 'segmentation_model', 'face_model',
                       'mask_path', 'mask_mouth_path', 'mask_blink_path'} and isinstance(value, str) and value:
                yield data, key
            elif isinstance(value, (dict, list)):
                yield from _resource_fields(value)
    elif isinstance(data, list):
        for item in data:
            yield from _resource_fields(item)


class StudioProject:
    def __init__(self):
        from app.resources import default_model_paths
        models = default_model_paths()
        scenes = [dict(id=_id(), name=name, sources=[]) for name in ('Основная', 'Игра', 'Общение')]
        scenes[0]['sources'] = [
            _source('color', 'Фон', color='#111a28'),
            _source('color', 'Акцент', color='#5b8cff', x=.09, y=.3, width=.008, height=.32),
            _source('text', 'Заголовок', text='Собери свою первую сцену', x=.13, y=.32, width=.78, height=.17, color='#ffffff', font_size=44),
            _source('text', 'Подсказка', text='Добавь камеру, экран или текст.\nУправляй слоями слева.', x=.13, y=.52, width=.78, height=.16, color='#a1b0c8', font_size=24),
        ]
        self.data = dict(version=1, name='Моя студия', width=1280, height=720, fps=20,
                         active_scene_id=scenes[0]['id'], scenes=scenes, audio={}, rules=[],
                         soundboard=[], network={},
                         vision={key: models[key] for key in ('segmentation_model', 'face_model') if key in models},
                         captions={'model_path': models['model_path']} if 'model_path' in models else {}, extensions=[])

    @property
    def scenes(self):
        return self.data['scenes']

    @property
    def active_scene(self):
        return next(scene for scene in self.scenes if scene['id'] == self.data['active_scene_id'])

    def add_scene(self, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Укажи название сцены')
        scene = dict(id=_id(), name=name, sources=[])
        self.scenes.append(scene)
        return scene

    def duplicate_scene(self, scene_id):
        scene = copy.deepcopy(self._scene(scene_id))
        scene.update(id=_id(), name=scene['name'] + ' — копия')
        for source in scene['sources']:
            source['id'] = _id()
        self.scenes.append(scene)
        return scene

    def _scene(self, scene_id):
        for scene in self.scenes:
            if scene['id'] == scene_id:
                return scene
        raise ValueError('Сцена не найдена')

    def remove_scene(self, scene_id):
        scene = self._scene(scene_id)
        if len(self.scenes) == 1:
            raise ValueError('Нельзя удалить последнюю сцену')
        self.scenes.remove(scene)
        if self.data['active_scene_id'] == scene_id:
            self.data['active_scene_id'] = self.scenes[0]['id']

    def set_active(self, scene_id):
        self._scene(scene_id)
        self.data['active_scene_id'] = scene_id

    def add_source(self, kind, name=None, **kwargs):
        source = _source(kind, name, **kwargs)
        candidate = copy.deepcopy(self.data)
        next(s for s in candidate['scenes'] if s['id'] == candidate['active_scene_id'])['sources'].append(source)
        self._validate(candidate)
        self.active_scene['sources'].append(source)
        return source

    def find_source(self, source_id):
        return next((source for scene in self.scenes for source in scene['sources'] if source['id'] == source_id), None)

    def remove_source(self, source_id):
        for scene in self.scenes:
            scene['sources'][:] = [s for s in scene['sources'] if s['id'] != source_id]

    @staticmethod
    def _validate(data):
        if not isinstance(data, dict) or data.get('version') != 1:
            raise ValueError('Неподдерживаемая версия проекта')
        for name in ('width', 'height'):
            value = data.get(name)
            _number(value, 16, 8192, name)
            if not isinstance(value, int):
                raise ValueError('Размер кадра должен быть целым')
        _number(data.get('fps'), 1, 240, 'fps')
        if not isinstance(data['fps'], int) or data['width'] * data['height'] > 40_000_000:
            raise ValueError('Недопустимая частота или слишком большой холст')
        if not isinstance(data.get('name'), str) or len(data['name']) > 1000:
            raise ValueError('Недопустимое название проекта')
        scenes = data.get('scenes')
        if not isinstance(scenes, list) or not 1 <= len(scenes) <= 200:
            raise ValueError('Недопустимый список сцен')
        ids = set()
        scene_ids = set()
        for scene in scenes:
            if not isinstance(scene, dict) or not isinstance(scene.get('name'), str) or not isinstance(scene.get('sources'), list) or len(scene['sources']) > 500:
                raise ValueError('Недопустимая сцена')
            scene_ids.add(scene.get('id'))
            for item in [scene, *scene['sources']]:
                if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not 1 <= len(item['id']) <= 128 or item['id'] in ids:
                    raise ValueError('Недопустимый или повторяющийся ID')
                ids.add(item['id'])
            for source in scene['sources']:
                if source.get('kind') not in KINDS:
                    raise ValueError('Неизвестный тип источника')
                for name in ('name', 'text', 'color'):
                    if not isinstance(source.get(name), str):
                        raise ValueError('Недопустимое поле источника')
                _path(source.get('path'))
                for name in ('x', 'y'):
                    _number(source.get(name), -8, 8, name)
                for name in ('width', 'height'):
                    _number(source.get(name), .001, 8, name)
                _number(source.get('opacity'), 0, 1, 'opacity')
                _number(source.get('rotation'), -36000, 36000, 'rotation')
                if not isinstance(source.get('visible'), bool) or not isinstance(source.get('locked'), bool):
                    raise ValueError('Недопустимая видимость/блокировка')
                crop = source.get('crop')
                if not isinstance(crop, list) or len(crop) != 4:
                    raise ValueError('Недопустимая обрезка')
                for value in crop:
                    _number(value, 0, .999, 'crop')
                if crop[0] + crop[2] >= 1 or crop[1] + crop[3] >= 1:
                    raise ValueError('Обрезка удаляет весь кадр')
                if not isinstance(source.get('effects'), dict) or not isinstance(source.get('animation'), list) or len(source['animation']) > 10000:
                    raise ValueError('Недопустимые эффекты/анимация')
                _options(source, strings=('mask', 'font', 'background'), booleans=('mask_tracking',),
                         integers={'font_size': (8, 512), 'outline': (0, 8)},
                         numbers={'mask_x': (-8, 8), 'mask_y': (-8, 8), 'mask_size': (.001, 8)},
                         paths=('mask_path', 'mask_mouth_path', 'mask_blink_path'),
                         enums={'fit': {'contain', 'cover', 'crop', 'stretch'}})
                device = source.get('device')
                if source['kind'] in {'camera', 'screen'}:
                    _options({'device': device}, integers={'device': (0, 100000)})
                elif device is not None and (isinstance(device, bool) or not isinstance(device, (str, int))):
                    raise ValueError('Недопустимое устройство источника')
                _options(source['effects'], booleans=('mirror_h', 'mirror_v'), integers={'blur': (0, 30), 'pixelate': (0, 100)}, numbers={
                    'brightness': (-100, 100), 'contrast': (-100, 100), 'saturation': (-100, 100),
                    'gamma': (.1, 3), 'temperature': (-100, 100), 'blur': (0, 30),
                    **{key: (0, 100) for key in ('sharpen', 'vignette', 'pixelate', 'sepia', 'grayscale', 'invert')}})
                for keyframe in source['animation']:
                    if not isinstance(keyframe, dict):
                        raise ValueError('Недопустимый ключевой кадр')
                    _number(keyframe.get('time', keyframe.get('t')), 0, 86400, 'time')
                    for key in ('x', 'y', 'width', 'height', 'opacity'):
                        if key in keyframe:
                            low, high = (0, 1) if key == 'opacity' else ((.001, 8) if key in ('width', 'height') else (-8, 8))
                            _number(keyframe[key], low, high, key)
        if data.get('active_scene_id') not in scene_ids:
            raise ValueError('Активная сцена не найдена')
        for key in ('audio', 'network', 'vision', 'captions'):
            if not isinstance(data.get(key), dict):
                raise ValueError(f'Недопустимые настройки {key}')
        for key in ('rules', 'soundboard', 'extensions'):
            if not isinstance(data.get(key), list):
                raise ValueError(f'Недопустимые настройки {key}')
        _validate_settings(data)
        for owner, key in _resource_fields(data):
            _path(owner[key])

    def save(self, path):
        self._validate(self.data)
        _atomic_json(path, self.data)

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.stat().st_size > MAX_PROJECT_BYTES:
            raise ValueError('Слишком большой проект')
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            cls._validate(data)
        except (TypeError, KeyError, RecursionError, json.JSONDecodeError) as error:
            raise ValueError('Повреждённый проект') from error
        for owner, key in _resource_fields(data):
            if not Path(owner[key]).is_absolute() and '://' not in owner[key]:
                owner[key] = str((path.parent / owner[key]).resolve())
        project = cls()
        project.data = data
        return project

    def export_bundle(self, path):
        self._validate(self.data)
        data = copy.deepcopy(self.data)
        resources = {}
        resource_roots = {}
        total = 0
        for owner, key in _resource_fields(data):
            if '://' in owner[key]:
                continue
            resource = Path(owner[key])
            if not resource.exists():
                owner[key] = ''
                continue
            if resource.is_symlink() or resource.is_junction():
                raise ValueError('Ссылки на ресурсы не включаются в архив')
            resource = resource.resolve()
            if resource in resource_roots:
                owner[key] = resource_roots[resource]
                continue
            member = f'assets/{len(resource_roots):04d}_{resource.name}'
            resource_roots[resource] = member
            pending = [(resource, member)]
            while pending:
                item, item_member = pending.pop()
                if item.is_symlink() or item.is_junction() or not item.resolve().is_relative_to(resource if resource.is_dir() else resource.parent):
                    raise ValueError('Небезопасная ссылка в папке ресурса')
                if item.is_dir():
                    resources[item_member + '/'] = None
                    pending.extend((child, item_member + '/' + child.name) for child in sorted(item.iterdir()))
                elif item.is_file():
                    total += item.stat().st_size
                    resources[item_member] = item
                else:
                    raise ValueError('Неподдерживаемый ресурс проекта')
                if total > MAX_BUNDLE_BYTES or len(resources) >= 1999:
                    raise ValueError('Слишком большой пакет ресурсов')
            owner[key] = member
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=target.parent, suffix='.zip')
        os.close(handle)
        try:
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('project.json', json.dumps(data, ensure_ascii=False, allow_nan=False))
                for member, resource in resources.items():
                    if resource is None:
                        archive.writestr(member, b'')
                    else:
                        archive.write(resource, member, compress_type=zipfile.ZIP_STORED)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @classmethod
    def import_bundle(cls, path, destination):
        destination = Path(destination).resolve()
        if destination.exists():
            raise ValueError('Выбери новую папку для импорта проекта')
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(e.file_size for e in entries) > MAX_BUNDLE_BYTES:
                raise ValueError('Слишком большой архив')
            names = set()
            canonical_names = set()
            reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
            for entry in entries:
                name = entry.filename.replace('\\', '/')
                parts = PurePosixPath(name).parts
                if not name or name.startswith('/') or ntpath.splitdrive(name)[0] or '..' in parts or ':' in name or name in names or stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError('Небезопасный путь в архиве')
                if any(part.rstrip(' .') != part or part.split('.')[0].upper() in reserved or any(ord(char) < 32 for char in part) for part in parts):
                    raise ValueError('Небезопасное имя файла Windows в архиве')
                canonical = '/'.join(parts).casefold()
                if canonical in canonical_names:
                    raise ValueError('Повторяющийся путь в архиве')
                canonical_names.add(canonical)
                if entry.flag_bits & 1 or (entry.file_size > 1024 * 1024 and entry.file_size > max(1, entry.compress_size) * 500):
                    raise ValueError('Небезопасный размер/шифрование архива')
                names.add(name)
            if 'project.json' not in names:
                raise ValueError('В архиве нет project.json')
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(dir=destination.parent))
            try:
                for entry in entries:
                    target = staging / entry.filename.replace('\\', '/')
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(entry) as source, target.open('wb') as output:
                            shutil.copyfileobj(source, output, 1024 * 1024)
                project = cls.load(staging / 'project.json')
                for owner, key in _resource_fields(project.data):
                    if '://' in owner[key]:
                        continue
                    resource = Path(owner[key])
                    if not resource.is_relative_to(staging):
                        raise ValueError('Ресурс импортируемого проекта находится вне архива')
                    owner[key] = str(destination / resource.relative_to(staging))
                staging.rename(destination)
                return project
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
