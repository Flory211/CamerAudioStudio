import copy
import os
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from app.studio.automation import AutomationEngine
from app.studio.project import StudioProject


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def owner(self):
        class Owner(QWidget):
            def __init__(self):
                super().__init__()
                self.project = StudioProject()
                self.project.data['soundboard'] = [dict(id='sound-123', name='Звонок', path='bell.wav')]
                self.audio_engine = SimpleNamespace(channels={'Микрофон': {}, 'Гость': {}})
                self.changes = 0
                self.notifications = []
            def mark_changed(self, **kwargs): self.changes += 1
            def notify(self, text, error=False): self.notifications.append((text, error))
        return Owner()

    def test_visual_timer_builds_working_scene_rule_without_showing_id(self):
        from app.ui.rules import RuleEditor
        owner = self.owner()
        editor = RuleEditor(owner)
        editor.interval.setValue(5)
        scene = owner.project.scenes[1]
        editor.target.setCurrentIndex(editor.target.findData(scene['id']))
        editor.add_rule()
        self.assertEqual(owner.project.data['rules'], [dict(enabled=True, event='timer', interval=5., action=dict(type='scene', target=scene['id']))])
        self.assertNotIn(scene['id'], editor.rule_list.item(0).text())
        engine = AutomationEngine(owner.project.data['rules'])
        self.assertEqual(engine.poll(10), [])
        self.assertEqual(engine.poll(15), [dict(type='scene', target=scene['id'])])
        self.assertEqual(owner.changes, 1)
        for _ in range(4): editor.refresh()
        self.assertEqual(owner.changes, 1)
        self.assertEqual(engine.poll(16), [])
        owner.close()

    def test_visual_mute_edit_enabled_and_remove(self):
        from app.ui.rules import RuleEditor
        owner = self.owner()
        editor = RuleEditor(owner)
        editor.event.setCurrentIndex(editor.event.findData('speech_start'))
        editor.action.setCurrentIndex(editor.action.findData('mute'))
        editor.target.setCurrentIndex(editor.target.findData('Гость'))
        editor.value.setCurrentIndex(editor.value.findData(True))
        editor.add_rule()
        rule = owner.project.data['rules'][0]
        self.assertEqual(rule['action'], {'type': 'mute', 'target': 'Гость', 'value': True})
        editor.rule_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(rule['enabled'])
        editor.rule_list.setCurrentRow(0)
        editor.value.setCurrentIndex(editor.value.findData(False))
        editor.save_rule()
        self.assertFalse(owner.project.data['rules'][0]['action']['value'])
        editor.remove_rule()
        self.assertEqual(owner.project.data['rules'], [])
        owner.close()

    def test_rule_refresh_switches_project_without_saving_and_reports_missing_target(self):
        from app.ui.rules import RuleEditor
        owner = self.owner()
        editor = RuleEditor(owner)
        editor.action.setCurrentIndex(editor.action.findData('sound'))
        editor.add_rule()
        self.assertEqual(owner.project.data['rules'][0]['action']['target'], 'sound-123')
        owner.project = StudioProject()
        editor.refresh()
        self.assertEqual(editor.rule_list.count(), 0)
        editor.action.setCurrentIndex(editor.action.findData('sound'))
        editor.add_rule()
        self.assertEqual(owner.project.data['rules'], [])
        self.assertTrue(owner.notifications[-1][1])
        owner.close()

    def test_imported_invalid_rule_can_be_selected_and_replaced_visually(self):
        from app.ui.rules import RuleEditor
        owner = self.owner()
        owner.project.data['rules'] = [dict(event='timer', interval='bad', action=[])]
        editor = RuleEditor(owner)
        editor.rule_list.setCurrentRow(0)
        editor.save_rule()
        self.assertEqual(owner.project.data['rules'][0]['action']['type'], 'scene')
        self.assertEqual(owner.project.data['rules'][0]['interval'], 10.)
        owner.close()

    def test_animation_preset_interpolates_and_cancel_does_not_change_source(self):
        from app.ui.animation import AnimationDialog
        from app.studio.compositor import animated_values
        source = dict(x=.1, y=.2, width=.5, height=.4, opacity=.8, animation=[])
        original = copy.deepcopy(source)
        dialog = AnimationDialog(source)
        dialog.duration.setValue(2)
        dialog.preset.setCurrentText('Появление')
        dialog.apply_preset()
        frames = dialog.collect_keyframes()
        self.assertEqual(frames[0]['opacity'], 0)
        self.assertEqual(frames[-1]['opacity'], .8)
        self.assertEqual(frames[-1]['t'], 2)
        animated = {**source, 'animation': frames}
        self.assertAlmostEqual(animated_values(animated, 1)['opacity'], .4)
        dialog.reject()
        self.assertEqual(source, original)
        self.assertEqual(dialog.keyframes, [])

    def test_animation_table_percent_conversion_validation_and_accept(self):
        from app.ui.animation import AnimationDialog
        source = dict(x=0, y=0, width=1, height=1, opacity=1, animation=[])
        dialog = AnimationDialog(source)
        dialog.add_keyframe()
        dialog.table.item(0, 1).setText('25')
        dialog.table.item(0, 3).setText('50')
        dialog.table.item(0, 5).setText('75')
        self.assertEqual(dialog.collect_keyframes()[0], dict(t=0., x=.25, y=0., width=.5, height=1., opacity=.75))
        dialog.table.item(0, 0).setText('nan')
        with self.assertRaises(ValueError): dialog.collect_keyframes()
        dialog.table.item(0, 0).setText('0')
        dialog.accept()
        self.assertEqual(dialog.keyframes[0]['width'], .5)
        self.assertEqual(source['animation'], [])

    def test_animation_loading_sparse_frames_keeps_existing_motion(self):
        from app.ui.animation import AnimationDialog
        from app.studio.compositor import animated_values
        source = dict(x=0, y=0, width=1, height=1, opacity=1,
                      animation=[{'t': 0, 'opacity': 0}, {'t': 2, 'x': .5}])
        dialog = AnimationDialog(source)
        result = {**source, 'animation': dialog.collect_keyframes()}
        self.assertEqual(animated_values(result, 1), animated_values(source, 1))
        dialog.reject()


if __name__ == '__main__': unittest.main()
