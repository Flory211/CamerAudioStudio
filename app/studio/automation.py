"""Finite-rate event rules; actions are returned for the application to execute."""
import copy
import math
import time


class AutomationEngine:
    def __init__(self, rules=None):
        self.rules = rules if rules is not None else []
        self._last = {}
        self._speaking = False
        self._events = {}

    def _action(self, rule):
        action = rule.get('action')
        if not isinstance(action, dict) or action.get('type') not in {'scene', 'mute', 'record', 'sound'}:
            return None
        return copy.deepcopy(action)

    def poll(self, now, speaking=False):
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            return []
        actions = []
        current_rules = {id(rule) for rule in self.rules[:1000]}
        self._last = {key: value for key, value in self._last.items() if key in current_rules}
        for rule in self.rules[:1000]:
            if not isinstance(rule, dict) or not rule.get('enabled', True) or rule.get('event') != 'timer':
                continue
            interval = rule.get('interval', 1)
            if not isinstance(interval, (int, float)) or not math.isfinite(interval):
                continue
            interval = max(.1, float(interval))
            last = self._last.setdefault(id(rule), now)
            if now < last:
                self._last[id(rule)] = now
            elif now - last >= interval:
                self._last[id(rule)] = now  # No catch-up burst after a paused event loop.
                action = self._action(rule)
                if action:
                    actions.append(action)
        if bool(speaking) != self._speaking:
            actions.extend(self.trigger('speech_start' if speaking else 'speech_end'))
        return actions

    def trigger(self, event):
        if isinstance(event, dict):
            event = event.get('event', event.get('type'))
        if event not in {'speech_start', 'speech_end', 'record_start', 'record_stop'}:
            return []
        if event in {'speech_start', 'speech_end'}:
            speaking = event == 'speech_start'
            if speaking == self._speaking:
                return []
            self._speaking = speaking
        now = time.monotonic()
        if now - self._events.get(event, -math.inf) < .1:
            return []
        self._events[event] = now
        return [action for rule in self.rules[:1000]
                if isinstance(rule, dict) and rule.get('enabled', True) and rule.get('event') == event
                and (action := self._action(rule))]
