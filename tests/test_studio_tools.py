import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from PyQt6.QtWidgets import QApplication, QWidget
from app.studio.project import StudioProject
from app.ui.tools import ToolsPanel, validate_rules

class ToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_rules_reject_invalid_actions_intervals_and_types(self):
        for rules in ({}, [{'event':'timer','interval':0,'action':{'type':'record','value':True}}], [{'event':'timer','interval':1,'action':{'type':'exec','path':'x'}}], [{'event':'speech_start','action':{'type':'mute','target':'mic','value':'yes'}}]):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                validate_rules(rules)
        rules=[{'event':'timer','interval':5,'action':{'type':'scene','target':'scene1'}}, {'event':'speech_end','action':{'type':'record','value':False}}]
        self.assertEqual(validate_rules(rules), rules)

    def test_panel_does_not_activate_devices_or_services(self):
        class Window(QWidget):
            def __init__(self):
                super().__init__()
                self.project = StudioProject()
                self.engine = SimpleNamespace(server=None,extensions=[],stats={'fps':0,'vision':'Видео без обработки'},auto_quality=False,quality=1)
                self.audio_engine=SimpleNamespace(sample_rate=44100)
                self.server=None
                self.caption_service=None
                self.notifications=[]
            def notify(self,text,error=False): self.notifications.append(text)
            def mark_changed(self,rebuild=False): pass
        window=Window()
        panel=ToolsPanel(window)
        panel.resize(380,650)
        panel.show()
        self.app.processEvents()
        panel.refresh()
        self.assertIsNone(window.server)
        self.assertIsNone(window.caption_service)
        self.assertEqual(window.engine.extensions, [])
        self.assertGreater(panel.tabs.count(), 4)
        panel.close()
        window.close()

    def test_async_services_do_not_attach_to_a_different_project(self):
        class Window(QWidget):
            def __init__(self):
                super().__init__()
                self.project = StudioProject()
                self.engine = SimpleNamespace(server=None, extensions=[], stats={}, auto_quality=False)
                self.audio_engine = SimpleNamespace(sample_rate=44100)
                self.server = self.caption_service = None
                self.jobs = []
            def run_job(self, work, done=None): self.jobs.append((work, done))
            def notify(self, text, error=False): pass
            def mark_changed(self, rebuild=False): pass
        class Server:
            def __init__(self, **kwargs): self.running = False; self.stopped = False
            def start(self, **kwargs): self.running = True
            def stop(self): self.running = False; self.stopped = True
        class Host:
            def __init__(self, path): self.error = ''; self.closed = False
            def close(self): self.closed = True
        window = Window()
        panel = ToolsPanel(window)
        try:
            with patch('app.ui.tools.StudioServer', Server):
                panel._toggle_server()
                work, done = window.jobs.pop()
                result = work()
                window.project = StudioProject()
                done(result)
                self.assertIsNone(window.server)
                self.assertIsNone(window.engine.server)
                self.assertTrue(result[1].stopped)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'effect.py'
                path.write_text('def process_frame(frame): return frame', encoding='utf-8')
                panel.extension_path.setText(str(path))
                with patch('app.studio.extensions.ExtensionHost', Host):
                    panel._toggle_extension()
                    work, done = window.jobs.pop()
                    result = work()
                    window.project = StudioProject()
                    done(result)
                    self.assertEqual(window.engine.extensions, [])
                    self.assertTrue(result[1].closed)
        finally:
            panel.close()
            window.close()

    def test_relay_password_is_runtime_only(self):
        class Window(QWidget):
            def __init__(self):
                super().__init__()
                self.project=StudioProject()
                self.engine=SimpleNamespace(server=None,extensions=[],stats={},auto_quality=False)
                self.audio_engine=SimpleNamespace(sample_rate=44100)
                self.server=self.caption_service=None
            def run_job(self,work,done=None):
                result=work()
                if done:done(result)
            def notify(self,text,error=False):pass
            def mark_changed(self,rebuild=False):StudioProject._validate(self.project.data)
        with tempfile.TemporaryDirectory() as directory:
            window=Window();panel=ToolsPanel(window)
            try:
                panel.tls_cache.setText(directory)
                panel.ice_urls.setPlainText('turns:relay.invalid:5349?transport=tcp')
                panel.ice_username.setText('temporary-user')
                panel.ice_password.setText('secret-never-in-project')
                panel._toggle_server()
                self.assertIsNotNone(window.server)
                self.assertEqual(window.server.ice_servers[0]['credential'],'secret-never-in-project')
                serialized=__import__('json').dumps(window.project.data)
                self.assertNotIn('secret-never-in-project',serialized)
                self.assertIn('relay.invalid',serialized)
            finally:
                if window.server:window.server.stop()
                panel.close();window.close()

if __name__ == '__main__': unittest.main()
