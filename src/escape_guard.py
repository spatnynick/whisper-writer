"""Consume Escape on X11 while recording, without grabbing other idle shortcuts."""
import logging
import os

from PyQt5.QtCore import QObject, QSocketNotifier, QTimer
from PyQt5.QtGui import QGuiApplication

logger = logging.getLogger(__name__)


class EscapeGuard(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False
        self._display = None
        self._notifier = None
        # XWayland grabs cannot suppress keys going to native Wayland clients.
        self._supported = (QGuiApplication.platformName() == 'xcb'
                           and os.environ.get('XDG_SESSION_TYPE', 'x11') != 'wayland')

    def set_active(self, active):
        if not self._supported or active == self.active:
            return
        try:
            from Xlib import X, XK, display
            if self._display is None:
                self._display = display.Display()
                self._root = self._display.screen().root
                self._keycode = self._display.keysym_to_keycode(XK.string_to_keysym('Escape'))
                self._notifier = QSocketNotifier(self._display.fileno(), QSocketNotifier.Read, self)
                self._notifier.activated.connect(self._drain)
            if active:
                # AnyModifier fails completely if even one desktop shortcut (e.g.
                # Ctrl+Escape on KDE) conflicts. Grab combinations independently.
                errors = []
                for modifiers in range(256):
                    self._root.grab_key(self._keycode, modifiers, False,
                                        X.GrabModeAsync, X.GrabModeAsync,
                                        onerror=lambda *args: errors.append(args) or True)
                self._display.sync()
                if len(errors) == 256:
                    logger.warning('Escape is already grabbed by another X11 client; cancellation will remain unsuppressed.')
                    return
                if errors:
                    logger.info('Existing desktop shortcuts retain %d Escape modifier combinations.', len(errors))
            else:
                # UngrabKey removes only the passive grab. If Escape is held, its
                # active grab lasts through release, so that release cannot leak.
                self._root.ungrab_key(self._keycode, X.AnyModifier)
                self._display.sync()
            self.active = active
            # sync() may already have buffered events, leaving no readable socket.
            QTimer.singleShot(0, self._drain)
        except Exception:
            logger.exception('Unable to manage X11 Escape suppression')
            self.close()
            self._supported = False

    def _drain(self, *_):
        if self._display is None:
            return
        try:
            # Cancellation is delivered by the existing XRecord/evdev listener,
            # which still observes grabbed keys. Drain our connection's events so
            # its socket does not remain readable; X11 itself balances the release.
            while self._display.pending_events():
                self._display.next_event()
        except Exception:
            logger.exception('X11 Escape event handling failed')
            self.close()
            self._supported = False

    def close(self):
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        if self._display is not None:
            try:
                # Closing the connection releases all its passive and active grabs.
                self._display.close()
            except Exception:
                logger.exception('Closing X11 Escape connection failed')
            self._display = None
        self.active = False
