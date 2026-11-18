import json
import unittest
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import numpy as np
from app.studio.network import StudioServer


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.server = StudioServer()
        self.url = self.server.start()
        parts = urlsplit(self.url)
        self.base = f'{parts.scheme}://{parts.netloc}'
        self.auth = '?token=' + self.server.token

    def tearDown(self):
        self.server.stop()

    def request(self, path, data=None):
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(self.base + path, data=body, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_all_endpoints_deny_absent_or_wrong_token(self):
        for path in ('/', '/guest', '/state', '/frame', '/control', '/offer', '/ice'):
            for auth in ('', '?token=incorrect'):
                with self.subTest(path=path, auth=auth):
                    code, _ = self.request(path + auth, {} if path in ('/control', '/offer') else None)
                    self.assertEqual(code, 403)
        self.assertEqual(self.server.drain_commands(), [])

    def test_state_frame_and_validated_commands(self):
        self.server.set_state({'scenes': [{'id': 's1', 'name': 'Камера'}], 'recording': False})
        self.server.set_frame(np.full((12, 16, 3), 42, np.uint8))
        code, state = self.request('/state' + self.auth)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(state)['scenes'][0]['id'], 's1')
        code, jpeg = self.request('/frame' + self.auth)
        self.assertEqual(code, 200)
        self.assertTrue(jpeg.startswith(b'\xff\xd8'))
        command = {'type': 'mute', 'target': 'Звук: bell (1).wav', 'value': True}
        code, _ = self.request('/control' + self.auth, command)
        self.assertEqual(code, 200)
        self.assertEqual(self.server.drain_commands(), [command])
        for command in ({'type':'scene','target':'s1'}, {'type':'mute','target':'mic','value':True}, {'type':'record','value':True}, {'type':'sound','target':'bell'}):
            self.assertEqual(self.request('/control' + self.auth, command)[0], 200)
        self.assertEqual(len(self.server.drain_commands()), 4)
        self.assertEqual(self.server.drain_commands(), [])
        for command in ({'type':'eval','value':'print(1)'}, {'type':'scene','target':'../secret'}, {'type':'mute','target':'mic','value':'yes'}, {'type':'sound','target':'a','path':'C:/secret'}):
            self.assertEqual(self.request('/control' + self.auth, command)[0], 400)

    def test_pages_render_and_body_limits_and_restart(self):
        for path in ('/', '/guest'):
            code, body = self.request(path + self.auth)
            self.assertEqual(code, 200)
            self.assertIn(b'<html', body.lower())
        self.assertEqual(self.request('/control' + self.auth, {'type':'sound','target':'x'*70000})[0], 413)
        self.server.stop()
        self.assertFalse(self.server.running)
        self.assertEqual(self.server.guest_frames(), {})
        self.assertEqual(self.server.drain_audio(), [])
        self.assertTrue(self.server.start().startswith('http://127.0.0.1:'))

    def test_offer_validation_happens_before_peer_allocation(self):
        for data in ({}, {'type': 'answer', 'sdp': 'bogus'}, {'type':'offer','sdp':'bogus','name':'x'*81}):
            self.assertEqual(self.request('/offer'+self.auth, data)[0], 400)

    def test_ice_default_has_no_external_servers(self):
        code, body = self.request('/ice' + self.auth)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {'iceServers': []})

    def test_ice_configuration_validated_and_exposed_only_with_token(self):
        for config in ({}, [1], [{'urls':'https://example.com'}], [{'urls':[]}],
                       [{'urls':'turn:example.com','credential':False}],
                       [{'urls':'stun:example.com'}]*9):
            with self.subTest(config=config), self.assertRaises(ValueError):
                StudioServer(ice_servers=config)
        configuration=[{'urls':['stun:relay.example:3478','turns:relay.example:5349?transport=tcp'],
                        'username':'guest','credential':'temporary-secret'}]
        server=StudioServer(ice_servers=configuration)
        try:
            url=server.start()
            configuration[0]['credential']='changed-after-construction'
            with urllib.request.urlopen(url.replace('/?', '/ice?'), timeout=5) as response:
                data=json.load(response)
            self.assertEqual(data['iceServers'][0]['credential'], 'temporary-secret')
            self.assertEqual(data['iceServers'][0]['urls'][1], 'turns:relay.example:5349?transport=tcp')
        finally:
            server.stop()

if __name__ == '__main__':
    unittest.main()

class WebRTCIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_webrtc_receives_separate_video_and_audio_tracks(self):
        import asyncio
        from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, VideoStreamTrack, AudioStreamTrack
        from aiohttp import ClientSession
        from av import VideoFrame
        class SyntheticVideo(VideoStreamTrack):
            async def recv(self):
                pts, time_base = await self.next_timestamp()
                frame = VideoFrame.from_ndarray(np.full((48,64,3), (11,22,180), np.uint8), format='bgr24')
                frame.pts = pts
                frame.time_base = time_base
                return frame
        server = StudioServer()
        url = server.start()
        peer = RTCPeerConnection(configuration=RTCConfiguration(iceServers=[]))
        peer.addTrack(SyntheticVideo())
        peer.addTrack(AudioStreamTrack())
        try:
            await peer.setLocalDescription(await peer.createOffer())
            async with ClientSession() as client:
                async with client.post(url.replace('/?', '/offer?'), json={'type':'offer','sdp':peer.localDescription.sdp,'name':'Тест'}) as response:
                    self.assertEqual(response.status, 200)
                    answer = await response.json()
            await peer.setRemoteDescription(RTCSessionDescription(sdp=answer['sdp'],type=answer['type']))
            frames, audio = {}, []
            for _ in range(100):
                frames = server.guest_frames()
                audio.extend(server.drain_audio())
                if frames and audio:
                    break
                await asyncio.sleep(.05)
            self.assertIn(answer['id'], frames)
            pixels = frames[answer['id']]
            self.assertEqual(pixels.shape, (48,64,3))
            self.assertGreater(float(pixels[:,:,2].mean()), 150)
            self.assertTrue(audio)
            self.assertEqual(audio[0][0], answer['id'])
            self.assertEqual(audio[0][1].shape[1], 2)
            self.assertEqual(audio[0][1].dtype, np.float32)
            self.assertEqual(audio[0][2], 48000)
        finally:
            await peer.close()
            server.stop()

    async def test_explicit_tls_creates_certificate_and_serves_authenticated_state(self):
        import tempfile
        import ssl
        from pathlib import Path
        from aiohttp import ClientSession, TCPConnector
        with tempfile.TemporaryDirectory() as directory:
            server = StudioServer(cache_dir=directory)
            try:
                url = server.start(tls=True)
                self.assertTrue(Path(server.certificate_path).is_file())
                context = ssl.create_default_context(cafile=server.certificate_path)
                async with ClientSession(connector=TCPConnector(ssl=context)) as client:
                    async with client.get(url.replace('/?', '/state?')) as response:
                        self.assertEqual(response.status, 200)
            finally:
                server.stop()

class CertificateReuseTests(unittest.TestCase):
    def test_tls_restart_preserves_trusted_certificate(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            server=StudioServer(cache_dir=directory)
            try:
                server.start(tls=True)
                first=Path(server.certificate_path).read_bytes()
                server.stop()
                server.start(tls=True)
                self.assertEqual(Path(server.certificate_path).read_bytes(), first)
            finally:
                server.stop()
