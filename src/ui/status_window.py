import sys
import os
import time
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer
from PyQt5.QtGui import QPixmap, QIcon, QPainter, QColor
from PyQt5.QtWidgets import QApplication, QLabel, QHBoxLayout

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ui.base_window import BaseWindow
from utils import ConfigManager

class StatusWindow(BaseWindow):
    statusSignal = pyqtSignal(str)
    closeSignal = pyqtSignal()

    def __init__(self):
        """
        Initialize the status window.
        """
        super().__init__('', 200, 56, show_title_bar=False)
        self.corner_radius = 28  # height // 2, for a full pill shape
        # Same colors as the tray icon's recording/transcribing glyphs (ww-logo-*.svg), so the
        # popup's border reads as the same status language rather than a new one.
        self.recording_border_color = QColor('#E53935')
        self.transcribing_border_color = QColor('#F59E0B')
        self.initStatusUI()
        self.statusSignal.connect(self.updateStatus)

    def initStatusUI(self):
        """
        Initialize the status user interface.
        """
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)

        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(0, 0, 0, 0)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(24, 24)
        microphone_path = os.path.join('assets', 'microphone.png')
        pencil_path = os.path.join('assets', 'pencil.png')
        self.microphone_pixmap = self._tinted_pixmap(microphone_path, 24, self.accent_color)
        self.pencil_pixmap = self._tinted_pixmap(pencil_path, 24, self.text_color)
        self.icon_label.setPixmap(self.microphone_pixmap)
        self.icon_label.setAlignment(Qt.AlignCenter)

        # Manual pulse: a plain QTimer regenerates a pixmap with a painter-applied opacity
        # and calls setPixmap() on icon_label every tick. Deliberately NOT a QGraphicsEffect:
        # a QGraphicsOpacityEffect on icon_label (nested inside main_widget, which itself
        # carries a QGraphicsDropShadowEffect, inside a translucent frameless top-level
        # window with a custom paintEvent) rendered a stray duplicate icon outside the
        # window bounds and intermittently failed to repaint the "real" icon after
        # start/stop/setOpacity calls. Plain QLabel.setPixmap() sidesteps that bug class.
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._onPulseTick)
        self._pulse_base_pixmap = None
        self._pulse_start_time = 0.0
        self._pulse_period = 0.9  # seconds per dim-and-back cycle

        self.status_label = QLabel('Recording...')
        status_font = self.status_label.font()
        status_font.setPointSize(10)
        self.status_label.setFont(status_font)
        self.status_label.setStyleSheet(f"color: {self.text_color.name()};")

        status_layout.addStretch(1)
        status_layout.addWidget(self.icon_label)
        status_layout.addWidget(self.status_label)
        status_layout.addStretch(1)

        self.main_layout.addLayout(status_layout)

    def _tinted_pixmap(self, path, size, color):
        """
        Load the (black-silhouette-on-transparent) icon at `path`, scale it to `size`x`size`,
        and tint its shape with `color` using CompositionMode_SourceIn so only the alpha
        mask of the source image is kept.
        """
        pixmap = QPixmap(path).scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        tinted = QPixmap(pixmap.size())
        tinted.fill(Qt.transparent)
        painter = QPainter(tinted)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), color)
        painter.end()
        return tinted

    def _startPulse(self, base_pixmap, period=0.9):
        """
        Start pulsing `base_pixmap` on icon_label between full opacity and a dimmed
        opacity, via a plain QTimer + QLabel.setPixmap() (no QGraphicsEffect).
        """
        self._pulse_base_pixmap = base_pixmap
        self._pulse_period = period
        self._pulse_start_time = time.monotonic()
        self._onPulseTick()
        self._pulse_timer.start(40)

    def _stopPulse(self):
        """
        Stop the pulse timer, if running.
        """
        self._pulse_timer.stop()

    def _onPulseTick(self):
        """
        Compute the current opacity from elapsed time and repaint icon_label with a
        freshly composited pixmap at that opacity.
        """
        elapsed = time.monotonic() - self._pulse_start_time
        phase = (elapsed % self._pulse_period) / self._pulse_period  # 0..1
        # Triangle wave: 1.0 -> 0.45 -> 1.0
        opacity = 1.0 - 0.55 * (1 - abs(2 * phase - 1))
        self.icon_label.setPixmap(self._opacityPixmap(self._pulse_base_pixmap, opacity))

    def _opacityPixmap(self, base_pixmap, opacity):
        """
        Return a new QPixmap with `base_pixmap` drawn onto a transparent pixmap at the
        given painter opacity (ordinary compositing, no QGraphicsEffect involved).
        """
        result = QPixmap(base_pixmap.size())
        result.setDevicePixelRatio(base_pixmap.devicePixelRatio())
        result.fill(Qt.transparent)
        painter = QPainter(result)
        painter.setOpacity(opacity)
        painter.drawPixmap(0, 0, base_pixmap)
        painter.end()
        return result

    def show(self):
        """
        Position the window according to the configured status_window_position and show it.
        """
        screen_geometry = QApplication.primaryScreen().availableGeometry()
        margin = 24  # gap from the screen edge, separate from BaseWindow's SHADOW_MARGIN
        w, h = self.width(), self.height()  # already includes BaseWindow's SHADOW_MARGIN padding
        position = ConfigManager.get_config_value('misc', 'status_window_position') or 'bottom_right'
        positions = {
            'bottom_right':  (screen_geometry.width() - w - margin, screen_geometry.height() - h - margin),
            'bottom_center': ((screen_geometry.width() - w) // 2, screen_geometry.height() - h - margin),
            'bottom_left':   (margin, screen_geometry.height() - h - margin),
            'top_right':     (screen_geometry.width() - w - margin, margin),
            'top_center':    ((screen_geometry.width() - w) // 2, margin),
            'top_left':      (margin, margin),
            'center':        ((screen_geometry.width() - w) // 2, (screen_geometry.height() - h) // 2),
        }
        x, y = positions.get(position, positions['bottom_right'])
        self.move(screen_geometry.x() + x, screen_geometry.y() + y)
        super().show()

    def closeEvent(self, event):
        """
        Emit the close signal when the window is closed.
        """
        self._stopPulse()
        self.closeSignal.emit()
        super().closeEvent(event)

    @pyqtSlot(str)
    def updateStatus(self, status):
        """
        Update the status window based on the given status.
        """
        if status == 'recording':
            self.status_label.setText('Recording...')
            self._startPulse(self.microphone_pixmap, period=0.9)
            self.border_color = self.recording_border_color
            self.update()
            self.show()
        elif status == 'transcribing':
            self.status_label.setText('Transcribing...')
            # Slightly slower period than recording, to read as "processing" rather
            # than the more urgent recording pulse.
            self._startPulse(self.pencil_pixmap, period=1.4)
            self.border_color = self.transcribing_border_color
            self.update()

        if status in ('idle', 'error', 'cancel'):
            self._stopPulse()
            self.close()


if __name__ == '__main__':
    ConfigManager.initialize()
    app = QApplication(sys.argv)

    status_window = StatusWindow()
    status_window.show()

    # Simulate status updates
    QTimer.singleShot(3000, lambda: status_window.statusSignal.emit('transcribing'))
    QTimer.singleShot(6000, lambda: status_window.statusSignal.emit('idle'))

    sys.exit(app.exec_())
