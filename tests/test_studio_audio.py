import builtins
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.audio.effects import AudioEffects


class EffectsTests(unittest.TestCase):
    def test_pitch_preserves_duration_and_changes_tone(self):
        effects = AudioEffects()
        effects.pitch = 1.5
        tone = (0.2 * np.sin(2 * np.pi * 440 * np.arange(44100) / 44100)).astype('float32')
        output = effects.apply(tone)
        self.assertEqual(output.shape, tone.shape)
        self.assertEqual(output.dtype, np.float32)
        spectrum = abs(np.fft.rfft(output[4410:]))
        peak = np.argmax(spectrum) * 44100 / len(output[4410:])
        self.assertLess(abs(peak - 660), 35)

    def test_gate_and_gain_silence_and_limiter(self):
        effects = AudioEffects()
        effects.gate_db = -30
        self.assertTrue(np.all(effects.apply(np.full((1024, 2), 0.001)) == 0))
        effects.gain = 0
        self.assertTrue(np.all(effects.apply(np.ones((1024, 2))) == 0))
        effects.gain = 20
        output = effects.apply(np.array([[np.nan, np.inf], [1, -1]], dtype='float32'))
        self.assertTrue(np.isfinite(output).all())
        self.assertLessEqual(float(np.max(abs(output))), 0.950001)

    def test_reverb_has_tail_in_following_block(self):
        effects = AudioEffects()
        effects.reverb = 0.7
        effects.gate_db = -120
        impulse = np.zeros((1024, 2), dtype='float32')
        impulse[-1] = 0.4
        effects.apply(impulse)
        tail = np.concatenate([effects.apply(np.zeros_like(impulse)) for _ in range(10)])
        self.assertGreater(float(np.max(abs(tail))), 0.01)

    def test_streaming_eq_equals_whole_processing(self):
        whole, streamed = AudioEffects(), AudioEffects()
        for effects in (whole, streamed):
            effects.low_db = 6
            effects.high_db = -4
            effects.gate_db = -120
        tone = (0.1 * np.sin(2*np.pi*100*np.arange(8192)/44100)).astype('float32')
        expected = whole.apply(tone)
        self.assertGreater(float(np.std(expected[1024:])), float(np.std(tone[1024:])) * 1.4)
        actual = np.concatenate([streamed.apply(block) for block in np.split(tone, 8)])
        np.testing.assert_allclose(actual, expected, atol=1e-6)

    def test_configuration_and_preset_roundtrip(self):
        effects = AudioEffects()
        effects.preset('Робот')
        self.assertGreater(effects.robot, 0)
        other = AudioEffects()
        other.configure(effects.to_dict())
        self.assertEqual(other.to_dict(), effects.to_dict())
        with self.assertRaises(ValueError):
            other.configure({'pitch': float('nan')})


class MixerTests(unittest.TestCase):
    def setUp(self):
        from app.audio.engine import AudioEngine
        self.engine = AudioEngine()

    def tearDown(self):
        self.engine.stop()

    def test_mute_solo_and_independent_routes_change_samples(self):
        engine = self.engine
        blocks = {'Микрофон': np.full((128, 1), 0.1, dtype='float32'),
                  'Система': np.full((128, 2), 0.2, dtype='float32')}
        engine.set_muted('Микрофон', True)
        engine.set_route('Система', record=False, output=True)
        record, output, tracks = engine.mix_block(blocks)
        self.assertEqual(record.shape, (128, 2))
        self.assertTrue(np.all(record == 0))
        np.testing.assert_allclose(output, 0.2, atol=1e-6)
        self.assertNotIn('Система', tracks)
        engine.set_muted('Микрофон', False)
        engine.set_solo('Микрофон', True)
        engine.set_gain('Микрофон', 0.5)
        record, output, tracks = engine.mix_block(blocks)
        np.testing.assert_allclose(record, 0.05, atol=1e-6)
        np.testing.assert_allclose(output, 0.05, atol=1e-6)
        self.assertAlmostEqual(engine.levels['Микрофон'], -26.0206, places=3)

    def test_unicode_soundboard_loop_and_stop(self):
        import soundfile as sf
        with tempfile.TemporaryDirectory(prefix='звук-') as directory:
            path = Path(directory) / 'сигнал.wav'
            sf.write(path, np.full((128, 2), 0.1), 44100, subtype='FLOAT')
            self.engine.play_sound(path, loop=True)
            blocks = self.engine._sound_blocks(512, speech=False)
            self.assertEqual(next(iter(blocks.values())).shape, (512, 2))
            self.engine.stop_sounds()
            self.assertEqual(self.engine._sound_blocks(512, speech=False), {})

    def test_external_resampling_and_queue_is_bounded(self):
        for _ in range(50):
            self.engine.feed_external('Гость', np.ones(480), 48000)
        self.assertLessEqual(self.engine._external['Гость'].qsize(), 8)
        self.assertEqual(self.engine._external['Гость'].get().shape, (441, 2))

    def test_output_is_not_opened_when_no_device_selected(self):
        class Input:
            samplerate = 44100
            def __init__(self, **kwargs): pass
            def start(self): pass
            def stop(self): pass
            def close(self): pass
        import sounddevice as sd
        with patch.object(sd, 'query_devices', return_value={'max_input_channels': 1}), patch.object(sd, 'InputStream', Input), patch.object(sd, 'OutputStream', side_effect=AssertionError('No output requested')):
            self.engine.start()
            time.sleep(0.05)
            self.assertTrue(self.engine.running)
            self.engine.stop()
            self.assertFalse(self.engine.running)

    def test_device_start_failure_closes_input_and_stops_worker(self):
        opened = []
        class Input:
            def __init__(self, **kwargs): opened.append(self); self.closed = False
            def start(self): pass
            def stop(self): pass
            def close(self): self.closed = True
        import sounddevice as sd
        with patch.object(sd, 'query_devices', return_value={'max_input_channels': 1}), patch.object(sd, 'InputStream', Input), patch.object(sd, 'OutputStream', side_effect=RuntimeError('Disconnected')):
            self.engine.start(output_device=123)
            self.engine._thread.join(timeout=2)
            self.assertFalse(self.engine.running)
            self.assertTrue(opened[0].closed)

    def test_soundboard_guest_mode_does_not_open_microphone(self):
        import sounddevice as sd
        with patch.object(sd, 'InputStream', side_effect=AssertionError('Microphone disabled')):
            self.engine.start(input_device=-1)
            time.sleep(.05)
            self.assertTrue(self.engine.running)

    def test_output_callback_preserves_samples_across_different_frame_sizes(self):
        first = np.arange(8, dtype='float32').reshape(4, 2)/10
        second = np.arange(8, 16, dtype='float32').reshape(4, 2)/10
        self.engine._output.put(first)
        self.engine._output.put(second)
        blocks = []
        for _ in range(3):
            out = np.empty((3, 2), dtype='float32')
            self.engine._output_callback(out, 3, None, None)
            blocks.append(out)
        actual = np.concatenate(blocks)
        np.testing.assert_allclose(actual[:8], np.concatenate((first, second)))
        np.testing.assert_allclose(actual[8], 0)

    def test_soundboard_ducks_when_speech_is_present(self):
        import soundfile as sf
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'speech.wav'
            sf.write(path, np.full(2048, .2), 44100, subtype='FLOAT')
            self.engine.play_sound(path, loop=True)
            quiet = next(iter(self.engine._sound_blocks(512, speech=False).values()))
            ducked = next(iter(self.engine._sound_blocks(512, speech=True).values()))
            np.testing.assert_allclose(ducked, quiet*.25, atol=1e-6)
            self.engine.stop_sounds()


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_load_project_does_not_open_devices_and_restores_effects(self):
        from PyQt6.QtWidgets import QWidget
        from app.audio.engine import AudioEngine
        from app.studio.project import StudioProject
        from app.ui.audio_panel import AudioPanel, MixerPanel
        class Owner(QWidget):
            def __init__(self):
                super().__init__()
                self.project = StudioProject()
                self.project.data['audio'] = {'effects': {'low_db': 5}, 'channels': {'Микрофон': {'gain': .5, 'muted': True}}}
                self.audio_engine = AudioEngine()
                self.changes = 0
            def notify(self, *args, **kwargs): pass
            def mark_changed(self, **kwargs): self.changes += 1
        owner = Owner()
        with patch.object(owner.audio_engine, 'start', side_effect=AssertionError('Explicit start required')):
            panel, mixer = AudioPanel(owner), MixerPanel(owner)
            panel.load_project()
            mixer.load_project()
            self.assertEqual(owner.audio_engine.effects.low_db, 5)
            self.assertTrue(owner.audio_engine.channels['Микрофон']['muted'])
            self.assertEqual(owner.audio_engine.channels['Микрофон']['gain'], .5)
            panel.controls['low_db'].setValue(7)
            self.assertEqual(owner.project.data['audio']['effects']['low_db'], 7)
            self.assertGreater(owner.changes, 0)
        owner.close()

    def test_loading_another_project_resets_channel_mute_and_gain(self):
        from PyQt6.QtWidgets import QWidget
        from app.audio.engine import AudioEngine
        from app.studio.project import StudioProject
        from app.ui.audio_panel import MixerPanel
        class Owner(QWidget):
            def __init__(self):
                super().__init__()
                self.project = StudioProject()
                self.audio_engine = AudioEngine()
            def notify(self, *args, **kwargs): pass
            def mark_changed(self, **kwargs): pass
        owner = Owner()
        mixer = MixerPanel(owner)
        owner.audio_engine.set_muted('Микрофон', True)
        owner.audio_engine.set_gain('Микрофон', .1)
        owner.project = StudioProject()
        mixer.load_project()
        self.assertFalse(owner.audio_engine.channels['Микрофон']['muted'])
        self.assertEqual(owner.audio_engine.channels['Микрофон']['gain'], 1.)
        owner.close()


class CaptionTests(unittest.TestCase):
    def test_missing_model_is_actionable(self):
        from app.audio.captions import CaptionService
        with self.assertRaisesRegex(ValueError, 'модел'):
            CaptionService('Z:/несуществующая-модель-vosk')

    def test_srt_formats_recognizer_timing(self):
        from app.audio.captions import CaptionService
        import json
        class Model:
            def __init__(self, path): pass
        class Recognizer:
            def __init__(self, model, rate): pass
            def SetWords(self, value): pass
            def AcceptWaveform(self, pcm): return True
            def Result(self): return json.dumps({'text': 'привет мир', 'result': [{'start': 1.025, 'end': 2.5}]})
            def FinalResult(self): return '{}'
        import types
        with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', {'vosk': types.SimpleNamespace(Model=Model, KaldiRecognizer=Recognizer)}):
            service = CaptionService(directory)
            service.feed(np.ones((4410, 2), dtype='float32'))
            service.close()
            path = Path(directory) / 'слова.srt'
            service.save_srt(path)
            self.assertEqual(path.read_text(encoding='utf-8'), '1\n00:00:01,025 --> 00:00:02,500\nпривет мир\n\n')

    def test_missing_windows_session_backend_is_explicit(self):
        from app.audio import sessions
        original = builtins.__import__
        def missing(name, *args, **kwargs):
            if name.startswith('pycaw'):
                raise ImportError('test missing backend')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=missing):
            with self.assertRaisesRegex(RuntimeError, 'pycaw'):
                sessions.list_sessions()


if __name__ == '__main__':
    unittest.main()
