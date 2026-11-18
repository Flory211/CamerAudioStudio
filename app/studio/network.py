"""Opt-in authenticated local HTTP/WebRTC bridge, independent of the Qt UI."""
from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
import ipaddress
import json
from pathlib import Path
import re
import secrets
import socket
import ssl
import threading
import time

import cv2
import numpy as np


class StudioServer:
    MAX_GUESTS = 4

    def __init__(self, cache_dir=None, ice_servers=None):
        self.ice_servers = self.validate_ice_servers(ice_servers)
        self.token = secrets.token_urlsafe(32)
        self.url = ''
        self.running = False
        self.certificate_path = ''
        self.cache_dir = Path(cache_dir) if cache_dir else Path.cwd() / '.studio-cache' / 'tls'
        self._lock = threading.Lock()
        self._state = {}
        self._jpeg = b''
        self._commands = deque(maxlen=256)
        self._frames = {}
        self._audio = deque(maxlen=200)
        self._thread = self._loop = self._runner = None
        self._peers = {}
        self._tasks = set()
        self._error = None

    @staticmethod
    def validate_ice_servers(servers):
        if servers is None:
            return []
        if not isinstance(servers, list) or len(servers) > 8:
            raise ValueError('ICE: укажи список максимум из 8 серверов')
        result = []
        for server in servers:
            if not isinstance(server, dict) or set(server) - {'urls', 'username', 'credential'}:
                raise ValueError('ICE: допустимы urls, username и credential')
            urls = server.get('urls')
            entries = [urls] if isinstance(urls, str) else urls
            if not isinstance(entries, list) or not 1 <= len(entries) <= 8:
                raise ValueError('ICE: нужен URL или список из 1–8 URL')
            if any(not isinstance(url, str) or len(url) > 2048
                   or not re.fullmatch(r'(?:stun|stuns|turn|turns):[^\s\x00-\x1f]+', url)
                   for url in entries):
                raise ValueError('ICE URL должен начинаться с stun:, stuns:, turn: или turns: и не содержать пробелы')
            for key in ('username', 'credential'):
                if key in server and (not isinstance(server[key], str) or len(server[key]) > 512):
                    raise ValueError('ICE: логин и пароль должны быть строками до 512 символов')
            result.append(deepcopy(server))
        return result

    def start(self, host='127.0.0.1', port=0, tls=False):
        if self.running:
            return self.url
        # Resolve only explicit local/LAN bindings, never select a LAN interface silently.
        if not isinstance(host, str) or len(host) > 253:
            raise ValueError('Некорректный адрес сервера')
        if not 0 <= int(port) <= 65535:
            raise ValueError('Порт должен быть от 0 до 65535')
        ready = threading.Event()
        self._error = None
        self._thread = threading.Thread(target=self._serve_thread, args=(host, int(port), bool(tls), ready), daemon=True, name='StudioWebRTC')
        self._thread.start()
        if not ready.wait(15):
            raise RuntimeError('Сетевой сервер не ответил при запуске')
        if self._error:
            raise RuntimeError(f'Не удалось запустить сервер: {self._error}') from self._error
        return self.url

    def _serve_thread(self, host, port, tls, ready):
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._setup(host, port, tls))
            self.running = True
            ready.set()
            loop.run_forever()
        except Exception as error:
            self._error = error
            ready.set()
        finally:
            loop.run_until_complete(self._cleanup())
            loop.close()
            self.running = False

    def _ssl_context(self, host):
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from datetime import datetime, timedelta, timezone
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        certificate = self.cache_dir / 'studio-certificate.pem'
        private_key = self.cache_dir / 'studio-private-key.pem'
        names = [x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]
        try:
            address = ipaddress.ip_address(host)
            if not address.is_unspecified:
                names.append(x509.IPAddress(address))
        except ValueError:
            names.append(x509.DNSName(host))
        # Include the actual adapter addresses for an explicit wildcard LAN binding.
        if host in ('0.0.0.0', '::'):
            for result in socket.getaddrinfo(socket.gethostname(), None):
                try:
                    names.append(x509.IPAddress(ipaddress.ip_address(result[4][0])))
                except ValueError:
                    pass
        now = datetime.now(timezone.utc)
        self.certificate_path = str(certificate)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        if certificate.is_file() and private_key.is_file():
            try:
                existing = x509.load_pem_x509_certificate(certificate.read_bytes())
                existing_names = set(existing.extensions.get_extension_for_class(x509.SubjectAlternativeName).value)
                if (set(names).issubset(existing_names) and existing.not_valid_before_utc <= now
                        and existing.not_valid_after_utc > now + timedelta(days=1)):
                    context.load_cert_chain(certificate, private_key)
                    return context
            except (ValueError, OSError, ssl.SSLError, x509.ExtensionNotFound):
                pass
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'CamerAudioStudio local')])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=30)).add_extension(x509.SubjectAlternativeName(names), False)
                .sign(key, hashes.SHA256()))
        private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        context.load_cert_chain(certificate, private_key)
        return context

    async def _setup(self, host, port, tls):
        from aiohttp import web

        @web.middleware
        async def authenticate(request, handler):
            token = request.query.get('token', '')
            if not token or not secrets.compare_digest(token, self.token):
                raise web.HTTPForbidden(text='Неверный или отсутствующий токен')
            response = await handler(request)
            response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                     'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY'})
            return response

        app = web.Application(middlewares=[authenticate], client_max_size=64 * 1024)
        app.add_routes([web.get('/', self._page), web.get('/guest', self._page),
                        web.get('/state', self._get_state), web.get('/frame', self._get_frame),
                        web.get('/ice', self._get_ice),
                        web.post('/control', self._control), web.post('/offer', self._offer)])
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        context = self._ssl_context(host) if tls else None
        site = web.TCPSite(self._runner, host, port, ssl_context=context)
        await site.start()
        bound_port = site._server.sockets[0].getsockname()[1]
        display_host = '127.0.0.1' if host == '0.0.0.0' else ('::1' if host == '::' else host)
        if ':' in display_host:
            display_host = f'[{display_host}]'
        self.url = f'{"https" if tls else "http"}://{display_host}:{bound_port}/?token={self.token}'

    def stop(self):
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=10)
        self.running = False

    async def _cleanup(self):
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        await asyncio.gather(*(peer.close() for peer in tuple(self._peers.values())), return_exceptions=True)
        self._peers.clear()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        with self._lock:
            self._frames.clear()
            self._audio.clear()
            self._commands.clear()

    def set_state(self, state):
        # JSON roundtrip bounds external mutable references and guarantees serializability.
        encoded = json.dumps(state, ensure_ascii=False)
        if len(encoded.encode('utf8')) > 256 * 1024:
            raise ValueError('Состояние пульта слишком велико')
        with self._lock:
            self._state = json.loads(encoded)

    def set_frame(self, frame):
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return
        height, width = frame.shape[:2]
        if max(height, width) > 960:
            frame = cv2.resize(frame, (max(1, round(width * 960 / max(height,width))), max(1, round(height * 960 / max(height,width)))))
        ok, data = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok and data.size <= 2 * 1024 * 1024:
            with self._lock:
                self._jpeg = data.tobytes()

    def drain_commands(self):
        with self._lock:
            result = list(self._commands)
            self._commands.clear()
        return result

    def guest_frames(self):
        with self._lock:
            return {key: frame.copy() for key, frame in self._frames.items()}

    def drain_audio(self):
        with self._lock:
            result = list(self._audio)
            self._audio.clear()
        return result

    async def _page(self, request):
        from aiohttp import web
        name = 'guest.html' if request.path == '/guest' else 'remote.html'
        return web.Response(text=(Path(__file__).resolve().parent.parent/'web'/name).read_text(encoding='utf8'), content_type='text/html')

    async def _get_state(self, request):
        from aiohttp import web
        with self._lock:
            state = deepcopy(self._state)
        state['guests'] = list(self._peers)
        return web.json_response(state)

    async def _get_ice(self, request):
        from aiohttp import web
        return web.json_response({'iceServers': self.ice_servers})

    async def _get_frame(self, request):
        from aiohttp import web
        with self._lock:
            data = self._jpeg
        if not data:
            raise web.HTTPNoContent()
        return web.Response(body=data, content_type='image/jpeg')

    async def _json(self, request):
        from aiohttp import web
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text='Ожидался JSON')
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text='Ожидался объект JSON')
        return data

    async def _control(self, request):
        from aiohttp import web
        data = await self._json(request)
        kind = data.get('type')
        target = data.get('target', '')
        valid_target = (isinstance(target, str) and 1 <= len(target) <= 256
                        and target not in ('.', '..') and '/' not in target and '\\' not in target
                        and all(ord(char) >= 32 and ord(char) != 127 for char in target))
        if set(data) - {'type', 'target', 'value'} or kind not in ('scene','mute','record','sound'):
            raise web.HTTPBadRequest(text='Недопустимая команда')
        if kind in ('scene','mute','sound') and not valid_target:
            raise web.HTTPBadRequest(text='Некорректная цель команды')
        if kind in ('mute','record') and not isinstance(data.get('value'), bool):
            raise web.HTTPBadRequest(text='Значение должно быть true/false')
        if 'value' in data and not isinstance(data['value'], (str, bool, int, float, type(None))):
            raise web.HTTPBadRequest(text='Некорректное значение')
        with self._lock:
            if len(self._commands) >= self._commands.maxlen:
                raise web.HTTPTooManyRequests(text='Очередь команд заполнена')
            self._commands.append(data)
        return web.json_response({'ok':True})

    async def _offer(self, request):
        from aiohttp import web
        data = await self._json(request)
        name = data.get('name', 'Гость')
        sdp = data.get('sdp', '')
        if (data.get('type') != 'offer' or not isinstance(sdp, str) or not sdp.startswith('v=0')
                or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80):
            raise web.HTTPBadRequest(text='Некорректное WebRTC-предложение или имя')
        if len(self._peers) >= self.MAX_GUESTS:
            raise web.HTTPTooManyRequests(text='Не больше четырёх гостей одновременно')
        try:
            from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
        except ImportError:
            raise web.HTTPServiceUnavailable(text='Для WebRTC установи aiortc и av в окружение студии')
        # External ICE providers are used only when explicitly configured locally.
        peer = RTCPeerConnection(configuration=RTCConfiguration(
            iceServers=[RTCIceServer(**server) for server in self.ice_servers]))
        guest_id = name.strip() + '-' + secrets.token_hex(3)
        self._peers[guest_id] = peer

        @peer.on('track')
        def on_track(track):
            task = asyncio.create_task(self._receive(guest_id, track))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        @peer.on('connectionstatechange')
        async def state_changed():
            if peer.connectionState in ('failed','closed'):
                self._peers.pop(guest_id, None)
                with self._lock:
                    self._frames.pop(guest_id, None)
                await peer.close()

        try:
            await peer.setRemoteDescription(RTCSessionDescription(sdp=sdp, type='offer'))
            await peer.setLocalDescription(await peer.createAnswer())
            # Non-trickle ICE: localDescription is sent only after gathering completes.
            return web.json_response({'sdp':peer.localDescription.sdp,'type':peer.localDescription.type,'id':guest_id})
        except Exception as error:
            self._peers.pop(guest_id, None)
            await peer.close()
            raise web.HTTPBadRequest(text=f'Ошибка WebRTC: {type(error).__name__}')

    async def _receive(self, guest_id, track):
        try:
            from av.audio.resampler import AudioResampler
            resampler = AudioResampler(format='fltp', layout='stereo', rate=48000) if track.kind == 'audio' else None
            while True:
                frame = await track.recv()
                if track.kind == 'video':
                    pixels = frame.to_ndarray(format='bgr24')
                    h,w = pixels.shape[:2]
                    if max(h,w) > 1920:
                        pixels = cv2.resize(pixels, (max(1,round(w*1920/max(h,w))),max(1,round(h*1920/max(h,w)))))
                    with self._lock:
                        self._frames[guest_id] = pixels
                elif resampler:
                    for audio_frame in resampler.resample(frame):
                        samples = audio_frame.to_ndarray().T.copy()
                        with self._lock:
                            self._audio.append((guest_id, samples, 48000))
        except asyncio.CancelledError:
            raise
        except Exception:
            # Track end/disconnect is a normal lifecycle event.
            pass
        finally:
            if track.kind == 'video':
                with self._lock:
                    self._frames.pop(guest_id, None)
