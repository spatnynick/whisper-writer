"""Run ONLY on an isolated X server: xvfb-run -a env QT_QPA_PLATFORM=xcb
XDG_SESSION_TYPE=x11 venv/bin/python tests/x11_checks.py.
Sends synthetic keys to its own test window; never run on the desktop display.
"""
import os
import sys
import time
import unittest
sys.path.insert(0, os.path.abspath('src'))
from PyQt5.QtWidgets import QApplication, QLineEdit
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from unittest.mock import patch
from Xlib import X, XK, display
from Xlib.ext import xtest
from escape_guard import EscapeGuard
from ui.status_window import StatusWindow
from ui.settings_window import SettingsWindow
from utils import ConfigManager

if not os.environ.get('WW_ISOLATED_X11_TEST'):
    raise SystemExit('Refusing to inject keys without WW_ISOLATED_X11_TEST=1 on isolated Xvfb.')

APP = QApplication([])
APP.setQuitOnLastWindowClosed(False)
ConfigManager._instance = ConfigManager()
ConfigManager._instance.schema = ConfigManager.load_config_schema()
ConfigManager._instance.config = ConfigManager._instance.load_default_config()


def pump():
    for _ in range(8):
        APP.processEvents()
        time.sleep(.01)


class X11Checks(unittest.TestCase):
    def setUp(self):
        self.client = display.Display()
        self.window = self.client.screen().root.create_window(
            10, 10, 300, 200, 0, X.CopyFromParent, X.InputOutput, X.CopyFromParent,
            event_mask=X.KeyPressMask | X.KeyReleaseMask)
        self.window.map()
        self.client.sync()
        pump()  # Let an optional window manager map/reparent the test window.
        self.window.set_input_focus(X.RevertToParent, X.CurrentTime)
        self.client.sync()
        self.guard = EscapeGuard()
        self.esc = self.client.keysym_to_keycode(XK.string_to_keysym('Escape'))

    def tearDown(self):
        self.guard.close()
        self.window.destroy()
        self.client.close()
        pump()

    def key(self, code, kind):
        xtest.fake_input(self.client, kind, code)
        self.client.sync()
        pump()

    def received(self):
        result = []
        while self.client.pending_events():
            event = self.client.next_event()
            if event.type in (X.KeyPress, X.KeyRelease):
                result.append((event.type, event.detail))
        return result

    def test_escape_consumed_only_during_capture_including_release(self):
        self.key(self.esc, X.KeyPress)
        self.key(self.esc, X.KeyRelease)
        self.assertEqual(self.received(), [(X.KeyPress, self.esc), (X.KeyRelease, self.esc)])
        self.guard.set_active(True)
        self.assertTrue(self.guard.active)
        self.key(self.esc, X.KeyPress)
        self.guard.set_active(False)  # Cancellation completes before release.
        self.key(self.esc, X.KeyRelease)
        self.assertEqual(self.received(), [])
        self.key(self.esc, X.KeyPress)
        self.key(self.esc, X.KeyRelease)
        self.assertEqual(len(self.received()), 2)

    def test_other_keys_pass_and_second_recording_cancels(self):
        letter = self.client.keysym_to_keycode(XK.string_to_keysym('a'))
        for _ in range(2):
            self.guard.set_active(True)
            self.key(letter, X.KeyPress)
            self.key(letter, X.KeyRelease)
            self.assertEqual(len(self.received()), 2)
            self.key(self.esc, X.KeyPress)
            self.key(self.esc, X.KeyRelease)
            self.assertEqual(self.received(), [])
            self.guard.set_active(False)

    def test_conflicting_grab_does_not_break_other_owner(self):
        self.client.screen().root.grab_key(self.esc, 0, False,
                                          X.GrabModeAsync, X.GrabModeAsync)
        self.client.sync()
        with self.assertLogs('escape_guard', 'INFO'):
            self.guard.set_active(True)
        self.assertTrue(self.guard.active)  # Other combinations remain available.
        self.guard.set_active(False)
        self.key(self.esc, X.KeyPress)
        self.key(self.esc, X.KeyRelease)
        self.assertEqual(len(self.received()), 2)  # Original owner kept its grab.
        self.client.screen().root.ungrab_key(self.esc, 0)
        self.client.sync()
        self.guard.set_active(True)
        self.key(self.esc, X.KeyPress)
        self.key(self.esc, X.KeyRelease)
        self.assertEqual(self.received(), [])

    def test_pynput_still_observes_consumed_escape_for_cancellation(self):
        from pynput import keyboard
        observed = []
        listener = keyboard.Listener(on_press=lambda key: observed.append(key))
        listener.start()
        listener.wait()
        try:
            self.guard.set_active(True)
            self.key(self.esc, X.KeyPress)
            self.key(self.esc, X.KeyRelease)
            self.assertIn(keyboard.Key.esc, observed)
            self.assertEqual(self.received(), [])
        finally:
            listener.stop()
            listener.join(2)

    def test_status_visible_without_stealing_focus_for_each_state(self):
        popup = StatusWindow()
        try:
            for state in ('recording', 'transcribing', 'recording'):
                popup.updateStatus('idle')
                popup.updateStatus(state)
                pump()
                native = self.client.create_resource_object('window', int(popup.winId()))
                self.assertEqual(native.get_attributes().map_state, X.IsViewable)
                self.assertEqual(self.client.get_input_focus().focus.id, self.window.id)
                self.assertTrue(popup.isVisible())
                popup.updateStatus('idle')
                self.assertFalse(popup.isVisible())
        finally:
            popup.close()

    def test_cancelled_status_offers_clickable_retry(self):
        popup = StatusWindow()
        requested = []
        popup.retryRequested.connect(lambda: requested.append(True))
        try:
            popup.show_retry()
            pump()
            self.assertTrue(popup.isVisible())
            self.assertTrue(popup.retry_button.isVisible())
            QTest.mouseClick(popup.retry_button, Qt.LeftButton)
            pump()
            self.assertEqual(requested, [True])
            popup.updateStatus('recording')
            self.assertFalse(popup.retry_button.isVisible())
        finally:
            popup.close()

    def test_settings_restores_and_requests_focus(self):
        settings = SettingsWindow()
        try:
            settings.showMinimized()
            pump()
            settings.show_and_activate()
            pump()
            self.assertFalse(settings.isMinimized())
            self.assertTrue(settings.isVisible())
            self.assertTrue(settings.isActiveWindow())
        finally:
            settings.close()

    def test_escape_discards_settings_without_saving_or_quitting(self):
        settings = SettingsWindow()
        closed, saved, quitting = [], [], []
        settings.settings_closed.connect(lambda: closed.append(True))
        settings.settings_saved.connect(lambda: saved.append(True))
        settings.settings_saved_live.connect(lambda: saved.append(True))
        quit_callback = lambda: quitting.append(True)
        APP.aboutToQuit.connect(quit_callback)
        try:
            settings.show_and_activate()
            pump()
            widget = settings.findChild(QLineEdit, 'recording_options_activation_key_input')
            original = widget.text()
            widget.setText('ctrl+shift+a')
            self.assertTrue(settings.changed_settings())
            widget.setFocus()
            with patch.object(ConfigManager, 'reload_config'), patch.object(ConfigManager, 'save_config') as save, patch('ui.settings_window.QMessageBox.question') as question:
                QTest.keyClick(widget, Qt.Key_Escape)
                pump()
                save.assert_not_called()
                question.assert_not_called()
            self.assertFalse(settings.isVisible())
            self.assertEqual(widget.text(), original)
            self.assertEqual(closed, [True])
            self.assertEqual(saved, [])
            self.assertEqual(quitting, [])
        finally:
            APP.aboutToQuit.disconnect(quit_callback)
            settings.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
