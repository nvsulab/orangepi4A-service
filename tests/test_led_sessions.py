import ast
import sys
import threading
import unittest
from pathlib import Path

LED_DIR = Path(__file__).resolve().parents[1] / 'led-panel'
sys.path.insert(0, str(LED_DIR))
from led_sessions import LEDSessions
from gestures import GestureRecording


class SessionsTest(unittest.TestCase):
    def setUp(self):
        self.actions = []
        self.now = 0
        self.sessions = LEDSessions(lambda: self.actions.append('acquire'),
                                    lambda: self.actions.append('release'),
                                    clock=lambda: self.now)

    def test_view_only_never_stops_or_restarts_background(self):
        self.sessions.enter('viewer')
        self.sessions.heartbeat('viewer')
        self.sessions.leave('viewer')
        self.assertEqual(self.actions, [])

    def test_leaving_operator_resumes_bounce_while_viewer_remains(self):
        self.sessions.enter('viewer'); self.sessions.enter('operator')
        self.sessions.claim('operator'); self.sessions.leave('operator')
        self.assertEqual(self.actions, ['acquire', 'release'])
        self.assertIn('viewer', self.sessions.clients)

    def test_last_tab_returns_strip(self):
        self.sessions.enter('a'); self.sessions.enter('b')
        self.sessions.claim('a'); self.sessions.claim('b')
        self.sessions.leave('a')
        self.assertEqual(self.actions, ['acquire'])
        self.sessions.leave('b'); self.sessions.leave('b')
        self.assertEqual(self.actions, ['acquire', 'release'])

    def test_timeout_and_late_packets(self):
        self.sessions.enter('a'); self.sessions.claim('a'); self.now = 31; self.sessions.expire()
        self.assertEqual(self.actions, ['acquire', 'release'])
        self.assertFalse(self.sessions.heartbeat('a'))
        self.sessions.leave('b')
        self.assertFalse(self.sessions.enter('b'))
        self.assertEqual(self.actions, ['acquire', 'release'])

    def test_heartbeat_and_reentry(self):
        self.sessions.enter('a'); self.sessions.claim('a'); self.now = 25
        self.sessions.heartbeat('a'); self.now = 40; self.sessions.expire()
        self.assertEqual(self.actions, ['acquire'])
        self.sessions.leave('a'); self.sessions.enter('new-page'); self.sessions.claim('new-page')
        self.assertEqual(self.actions, ['acquire', 'release', 'acquire'])

    def test_failed_handoff_is_retried(self):
        self.sessions.enter('a'); self.sessions.claim('a')
        def fail(): raise RuntimeError('systemctl failed')
        self.sessions.release = fail
        with self.assertRaises(RuntimeError): self.sessions.leave('a')
        self.assertTrue(self.sessions.engaged)
        self.sessions.release = lambda: self.actions.append('release')
        self.sessions.expire()
        self.assertFalse(self.sessions.engaged)


class HardwareHandoffTest(unittest.TestCase):
    def setUp(self):
        self.actions = []
        actions = self.actions
        class SPI:
            def __init__(self, **kwargs): actions.append('open')
            def show(self, colors): actions.append(('write', colors))
            def close(self): actions.append('close')
        module = ast.parse((LED_DIR / 'app.py').read_text())
        controller = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'Controller')
        scope = {'GestureRecording': GestureRecording, 'threading': threading, 'NUM_LEDS': 72, 'WS2812SPI': SPI,
                 'stop_led_bouncing': lambda: actions.append('stop-bounce'),
                 'start_led_bouncing': lambda: actions.append('start-bounce')}
        exec(compile(ast.Module(body=[controller], type_ignores=[]), 'Controller', 'exec'), scope)
        self.ctrl = scope['Controller']()

    def test_stop_before_background_service_and_reentry(self):
        c = self.ctrl
        c.activate(); c.set_static((1, 2, 3)); c.show(['effect'], mode='static'); c.deactivate()
        self.assertEqual(self.actions[-2:], ['close', 'start-bounce'])
        before = list(self.actions)
        c.show(['stale-render'], mode='static'); c.show(['stale-camera'], camera=True)
        self.assertEqual(self.actions, before)
        self.assertIsNone(c.led)
        c.activate(); c.set_static((1, 2, 3)); c.show(['new-effect'], mode='static')
        self.assertEqual(self.actions[-3:], ['stop-bounce', 'open', ('write', ['new-effect'])])

    def test_release_waits_for_inflight_spi_write(self):
        c = self.ctrl; c.activate(); c.set_static((1, 2, 3))
        started = threading.Event(); finish = threading.Event()
        original = c.led.show
        def blocking_write(colors):
            if colors == ['inflight']:
                started.set(); self.assertTrue(finish.wait(2))
            original(colors)
        c.led.show = blocking_write
        writer = threading.Thread(target=lambda: c.show(['inflight'], mode='static'))
        writer.start(); self.assertTrue(started.wait(2))
        release = threading.Thread(target=c.deactivate); release.start()
        self.assertNotIn('start-bounce', self.actions)
        finish.set(); writer.join(2); release.join(2)
        self.assertFalse(writer.is_alive()); self.assertFalse(release.is_alive())
        self.assertEqual(self.actions[-2:], ['close', 'start-bounce'])


class BackgroundStartupTest(unittest.TestCase):
    def test_first_frame_is_moving_pixel_without_red_fill(self):
        import datetime
        runner = LED_DIR.parent / 'led-service/led_runner.py'
        module = ast.parse(runner.read_text())
        cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'LEDController')
        scope = {'datetime': datetime.datetime}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), 'LEDController', 'exec'), scope)
        controller = scope['LEDController'].__new__(scope['LEDController'])
        frames = []; fills = []; waits = []
        class Stop:
            def is_set(self): return False
            def wait(self, delay): waits.append(delay); return True
        class SPI:
            def xfer3(self, data): pass
        controller.NUM_LEDS = 72; controller._stop = Stop(); controller.spi = SPI()
        controller.set_all = lambda color: fills.append(color)
        controller.encode_ws281x = lambda colors: frames.append(colors) or []
        controller.cleanup = lambda: None
        controller.startup_then_bounce((255, 0, 0), (255, 128, 0), 2, 40)
        self.assertEqual(fills, [])
        self.assertEqual(waits, [0.04])
        self.assertEqual(frames[0], [(255, 128, 0)] + [(0, 0, 0)] * 71)


if __name__ == '__main__':
    unittest.main()
