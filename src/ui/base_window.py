from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QPainter, QBrush, QColor, QPainterPath, QGuiApplication, QPalette, QPen
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QMainWindow,
    QGraphicsDropShadowEffect
)


class BaseWindow(QMainWindow):
    # Space (in px) left around the visible "card" for the drop shadow to render into.
    # The top-level frameless window is (width + 2*SHADOW_MARGIN) x (height + 2*SHADOW_MARGIN);
    # the painted card and its content occupy the inset SHADOW_MARGIN..(-SHADOW_MARGIN) rect.
    SHADOW_MARGIN = 16

    def __init__(self, title, width, height, show_title_bar=True, frameless=True):
        """
        Initialize the base window.
        """
        super().__init__()
        self.frameless = frameless
        self.initUI(title, width, height, show_title_bar)
        self.setWindowPosition()
        self.is_dragging = False

    def initUI(self, title, width, height, show_title_bar=True):
        """
        Initialize the user interface.

        When self.frameless is False, this is a normal top-level window with the platform's
        native title bar/border and no custom painting or transparency — used by windows that
        should look like a standard Qt application window (e.g. Settings) rather than the
        frameless "card" look used elsewhere (main window, status popup).
        """
        self.setWindowTitle(title)

        if not self.frameless:
            self.resize(width, height)
            self.setCentralWidget(QWidget(self))
            self.main_widget = self.centralWidget()
            self.main_layout = QVBoxLayout(self.main_widget)
            return

        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        margin = self.SHADOW_MARGIN
        self.setFixedSize(width + margin * 2, height + margin * 2)

        # Corner radius for the painted "card" background; subclasses may override this
        # (e.g. to height // 2 for a pill shape) any time before the first paint.
        self.corner_radius = 16

        # Optional colored ring around the card, off by default. A subclass can set
        # self.border_color (QColor or None) and self.border_width any time, then call
        # self.update() to repaint — used by StatusWindow to make the popup's current state
        # (recording/transcribing) readable at a glance, not just via the small icon.
        self.border_color = None
        self.border_width = 3

        # Resolve theme colors from the active palette instead of hardcoding them.
        palette = self.palette()
        window_color = palette.color(QPalette.Window)
        self.card_color = QColor(window_color)
        self.card_color.setAlpha(240)
        self.text_color = palette.color(QPalette.WindowText)
        self.accent_color = palette.color(QPalette.Highlight)

        # Outer widget fills the whole (oversized) top-level window and is fully transparent;
        # it exists only to host the inset "card" widget with room for the shadow around it.
        outer_widget = QWidget(self)
        outer_layout = QVBoxLayout(outer_widget)
        outer_layout.setContentsMargins(margin, margin, margin, margin)

        self.main_widget = QWidget(outer_widget)
        self.main_layout = QVBoxLayout(self.main_widget)
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        outer_layout.addWidget(self.main_widget)

        shadow = QGraphicsDropShadowEffect(self.main_widget)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 120))
        self.main_widget.setGraphicsEffect(shadow)

        if show_title_bar:
            # Create a widget for the title bar
            title_bar = QWidget()
            title_bar_layout = QHBoxLayout(title_bar)
            title_bar_layout.setContentsMargins(0, 0, 0, 0)

            # Add the title label
            title_label = QLabel('WhisperWriter')
            title_font = title_label.font()
            title_font.setPointSize(12)
            title_font.setBold(True)
            title_label.setFont(title_font)
            title_label.setAlignment(Qt.AlignCenter)
            title_label.setStyleSheet(f"color: {self.text_color.name()};")

            # Create a widget for the close button
            close_button_widget = QWidget()
            close_button_layout = QHBoxLayout(close_button_widget)
            close_button_layout.setContentsMargins(0, 0, 0, 0)

            close_button = QPushButton('×')
            close_button.setFixedSize(25, 25)
            close_button.setCursor(Qt.PointingHandCursor)
            close_button.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    border: none;
                    color: {self.text_color.name()};
                    font-size: 16px;
                }}
                QPushButton:hover {{
                    color: #e0483d;
                }}
            """)
            close_button.clicked.connect(self.handleCloseButton)

            close_button_layout.addWidget(close_button, alignment=Qt.AlignRight)

            # Add widgets to the title bar layout
            title_bar_layout.addWidget(QWidget(), 1)  # Left spacer
            title_bar_layout.addWidget(title_label, 3)  # Title (with more width)
            title_bar_layout.addWidget(close_button_widget, 1)  # Close button

            self.main_layout.addWidget(title_bar)

        self.setCentralWidget(outer_widget)

        self.setStyleSheet(self._build_stylesheet())

    def _build_stylesheet(self) -> str:
        """
        Build a QSS stylesheet from the active palette so all BaseWindow subclasses
        (and their child widgets) pick up theme-correct colors instead of hardcoded ones.
        """
        palette = self.palette()
        button_color = palette.color(QPalette.Button)
        button_text_color = palette.color(QPalette.ButtonText)
        base_color = palette.color(QPalette.Base)
        text_color = palette.color(QPalette.Text)
        window_color = palette.color(QPalette.Window)
        highlight_color = palette.color(QPalette.Highlight)
        highlighted_text_color = palette.color(QPalette.HighlightedText)

        # A subtle midtone border between the window and window-text colors.
        border_color = QColor(
            (window_color.red() + text_color.red()) // 2,
            (window_color.green() + text_color.green()) // 2,
            (window_color.blue() + text_color.blue()) // 2,
        )
        border_color.setAlpha(90)

        # Pick the lighten/darken direction that reads correctly against the button color.
        is_dark = button_color.lightness() < 128
        hover_color = button_color.lighter(130) if is_dark else button_color.darker(108)
        pressed_color = button_color.lighter(150) if is_dark else button_color.darker(118)

        return f"""
            QWidget {{
                color: {text_color.name()};
            }}
            QPushButton {{
                background-color: {button_color.name()};
                color: {button_text_color.name()};
                border: 1px solid {border_color.name(QColor.HexArgb)};
                border-radius: 8px;
                padding: 6px 14px;
            }}
            QPushButton:hover {{
                background-color: {hover_color.name()};
            }}
            QPushButton:pressed {{
                background-color: {pressed_color.name()};
            }}
            QPushButton#primaryButton {{
                background-color: {highlight_color.name()};
                color: {highlighted_text_color.name()};
                border: none;
                font-weight: bold;
            }}
            QPushButton#primaryButton:hover {{
                background-color: {highlight_color.lighter(112).name()};
            }}
            QPushButton#primaryButton:pressed {{
                background-color: {highlight_color.darker(110).name()};
            }}
            QLineEdit, QComboBox {{
                background-color: {base_color.name()};
                color: {text_color.name()};
                border: 1px solid {border_color.name(QColor.HexArgb)};
                border-radius: 6px;
                padding: 4px 8px;
            }}
            QLineEdit:focus, QComboBox:focus {{
                border: 1px solid {highlight_color.name()};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {base_color.name()};
                color: {text_color.name()};
                selection-background-color: {highlight_color.name()};
                selection-color: {highlighted_text_color.name()};
                border: 1px solid {border_color.name(QColor.HexArgb)};
            }}
            QTabWidget::pane {{
                border: 1px solid {border_color.name(QColor.HexArgb)};
                border-radius: 8px;
                top: -1px;
            }}
            QTabBar::tab {{
                background-color: {button_color.name()};
                color: {button_text_color.name()};
                border: 1px solid {border_color.name(QColor.HexArgb)};
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                padding: 6px 14px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {highlight_color.name()};
                color: {highlighted_text_color.name()};
            }}
            QTabBar::tab:!selected:hover {{
                background-color: {hover_color.name()};
            }}
            QToolButton {{
                background-color: transparent;
                border: none;
                border-radius: 6px;
                padding: 2px;
            }}
            QToolButton:hover {{
                background-color: {border_color.name(QColor.HexArgb)};
            }}
        """

    def setWindowPosition(self):
        """
        Set the window position to the center of the screen.
        """
        center_point = QGuiApplication.primaryScreen().availableGeometry().center()
        frame_geometry = self.frameGeometry()
        frame_geometry.moveCenter(center_point)
        self.move(frame_geometry.topLeft())

    def handleCloseButton(self):
        """
        Close the window.
        """
        self.close()

    def mousePressEvent(self, event):
        """
        Allow the window to be moved by clicking and dragging anywhere on the window.
        Only applies to frameless windows, which have no native title bar to drag by.
        """
        if not self.frameless:
            super().mousePressEvent(event)
            return
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.start_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        """
        Move the window when dragging.
        """
        if not self.frameless:
            super().mouseMoveEvent(event)
            return
        if Qt.LeftButton and self.is_dragging:
            self.move(event.globalPos() - self.start_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        """
        Stop dragging the window.
        """
        if not self.frameless:
            super().mouseReleaseEvent(event)
            return
        self.is_dragging = False

    def paintEvent(self, event):
        """
        Paint a rounded "card" background (derived from the active palette), inset from the
        window's edges by SHADOW_MARGIN so the QGraphicsDropShadowEffect on main_widget has
        transparent space around the card to render its shadow into. Frameless windows only —
        a standard window (frameless=False) uses the platform's normal opaque background.
        """
        if not self.frameless:
            super().paintEvent(event)
            return
        margin = self.SHADOW_MARGIN
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()).adjusted(margin, margin, -margin, -margin),
            self.corner_radius, self.corner_radius
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(self.card_color))
        if self.border_color is not None:
            painter.setPen(QPen(self.border_color, self.border_width))
        else:
            painter.setPen(Qt.NoPen)
        painter.drawPath(path)
