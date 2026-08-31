import sys
import os
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer, QPropertyAnimation
from PyQt5.QtGui import QPixmap, QIcon, QPainter
from PyQt5.QtWidgets import QApplication, QLabel, QHBoxLayout, QGraphicsOpacityEffect

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

        # Opacity effect + animation used to pulse the icon while recording.
        self.icon_opacity_effect = QGraphicsOpacityEffect(self.icon_label)
        self.icon_label.setGraphicsEffect(self.icon_opacity_effect)
        self.pulse_animation = QPropertyAnimation(self.icon_opacity_effect, b"opacity")
        self.pulse_animation.setDuration(900)
        self.pulse_animation.setStartValue(1.0)
        self.pulse_animation.setKeyValueAt(0.5, 0.45)
        self.pulse_animation.setEndValue(1.0)
        self.pulse_animation.setLoopCount(-1)

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
        self.pulse_animation.stop()
        self.closeSignal.emit()
        super().closeEvent(event)

    @pyqtSlot(str)
    def updateStatus(self, status):
        """
        Update the status window based on the given status.
        """
        if status == 'recording':
            self.icon_label.setPixmap(self.microphone_pixmap)
            self.status_label.setText('Recording...')
            self.pulse_animation.start()
            self.show()
        elif status == 'transcribing':
            self.pulse_animation.stop()
            self.icon_opacity_effect.setOpacity(1.0)
            self.icon_label.setPixmap(self.pencil_pixmap)
            self.status_label.setText('Transcribing...')

        if status in ('idle', 'error', 'cancel'):
            self.pulse_animation.stop()
            self.icon_opacity_effect.setOpacity(1.0)
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
