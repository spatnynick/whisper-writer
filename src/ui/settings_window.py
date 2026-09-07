import os
import json
import math
import subprocess
import sys
from dotenv import set_key
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
    QMessageBox, QShortcut, QTabWidget, QWidget, QSizePolicy, QSpacerItem, QToolButton, QStyle,
    QFileDialog, QTextEdit, QGroupBox, QScrollArea, QFrame
)
from PyQt5.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QIcon, QKeySequence, QIntValidator
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ui.base_window import BaseWindow
from utils import ConfigManager
import glossary
from config_sync import (
    SYNC_AREA_LABELS,
    detect_git_path,
    load_sync_settings,
    normalize_sync_settings,
    save_sync_settings as write_sync_settings,
)

PROMPTING_GUIDE_URL = 'https://developers.openai.com/api/docs/guides/speech-to-text'

class SettingsWindow(BaseWindow):
    settings_closed = pyqtSignal()
    settings_saved = pyqtSignal()
    # Emitted instead of settings_saved when every changed setting is flagged
    # `live_reload: true` in the schema — main.py re-applies them without restarting.
    settings_saved_live = pyqtSignal()
    sync_settings_saved = pyqtSignal()
    sync_test_requested = pyqtSignal(object)
    sync_pull_requested = pyqtSignal(object)
    sync_push_requested = pyqtSignal(object)

    def __init__(self):
        """Initialize the settings window."""
        super().__init__('Settings', 760, 700, frameless=False)
        self.main_layout.setContentsMargins(18, 18, 18, 14)
        self.main_layout.setSpacing(14)
        self.setWindowIcon(QIcon(os.path.join('assets', 'ww-logo.png')))
        self.schema = ConfigManager.get_schema()
        self.sync_settings = load_sync_settings()
        # Set to True by main.py once the app's other components exist — on a first run
        # (no config.yaml yet) a save must still take the restart path, since that's what
        # actually creates them.
        self.allow_live_reload = False
        self.model_discovery_manager = QNetworkAccessManager(self)
        self.model_discovery_reply = None
        self.model_discovery_request_id = 0
        self.model_discovery_timeout = QTimer(self)
        self.model_discovery_timeout.setSingleShot(True)
        self.model_discovery_timeout.timeout.connect(self._on_model_discovery_timeout)
        self.model_refresh_debounce = QTimer(self)
        self.model_refresh_debounce.setSingleShot(True)
        self.model_refresh_debounce.setInterval(600)
        self.model_refresh_debounce.timeout.connect(self.refresh_api_models)
        self.init_settings_ui()
        self.baseline_values = self.collect_current_values()
        self.sync_baseline_values = self.collect_sync_values()
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.escape_shortcut.setContext(Qt.WindowShortcut)
        self.escape_shortcut.activated.connect(self.discard_and_close)

    def discard_and_close(self):
        """Escape discards edits without saving or restarting the tray application."""
        self.reset_settings()
        self.close()

    def show_and_activate(self):
        """Restore and focus Settings after an explicit tray-menu request."""
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def init_settings_ui(self):
        """Initialize the settings user interface."""
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs)

        self.create_tabs()
        self.create_sync_tab()
        self.create_about_tab()
        self.create_buttons()

        # Connect the use_api checkbox state change
        self.use_api_checkbox = self.findChild(QCheckBox, 'model_options_use_api_input')
        if self.use_api_checkbox:
            self.use_api_checkbox.stateChanged.connect(self.on_api_mode_changed)
            self.on_api_mode_changed(self.use_api_checkbox.isChecked())

        self.api_base_url_input = self.findChild(QLineEdit, 'model_options_api_base_url_input')
        self.api_key_input = self.findChild(QLineEdit, 'model_options_api_api_key_input')
        if self.api_base_url_input:
            self.api_base_url_input.textChanged.connect(self._schedule_model_refresh)
            self.api_base_url_input.editingFinished.connect(self.refresh_api_models)
        if self.api_key_input:
            self.api_key_input.editingFinished.connect(self.refresh_api_models)
        if getattr(self, 'api_model_refresh_button', None):
            self.api_model_refresh_button.clicked.connect(self.refresh_api_models)

        if self.use_api_checkbox and self.use_api_checkbox.isChecked():
            QTimer.singleShot(0, self.refresh_api_models)

    def _sync_labeled_row(self, label_text, widget):
        """Create a consistent label/control row for synchronization settings."""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)
        label = QLabel(f'{label_text}:', row)
        label.setMinimumWidth(195)
        row_layout.addWidget(label)
        row_layout.addWidget(widget, 1)
        return row

    def create_sync_tab(self):
        """Create local-only Git synchronization settings and status controls."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, 'Synchronization')

        intro = QLabel(
            'Synchronize only the selected settings through a separate Git repository. '
            'API keys and other secrets are never synchronized.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        connection_group = QGroupBox('Git connection')
        connection_layout = QVBoxLayout(connection_group)
        connection_layout.setContentsMargins(12, 16, 12, 12)
        connection_layout.setSpacing(10)

        self.sync_enabled_checkbox = QCheckBox('Enable synchronization', connection_group)
        self.sync_enabled_checkbox.setObjectName('sync_enabled_input')
        connection_layout.addWidget(self.sync_enabled_checkbox)

        self.sync_repository_url_input = QLineEdit(connection_group)
        self.sync_repository_url_input.setObjectName('sync_repository_url_input')
        self.sync_repository_url_input.setPlaceholderText('https://git.example.com/user/whisperwriter-settings.git')
        connection_layout.addWidget(self._sync_labeled_row('Repository URL', self.sync_repository_url_input))

        self.sync_branch_combo = QComboBox(connection_group)
        self.sync_branch_combo.setObjectName('sync_branch_input')
        self.sync_branch_combo.setEditable(False)
        self.sync_branch_combo.setPlaceholderText('Test connection to discover branches')
        connection_layout.addWidget(self._sync_labeled_row('Branch', self.sync_branch_combo))

        git_path_container = QWidget(connection_group)
        git_path_layout = QHBoxLayout(git_path_container)
        git_path_layout.setContentsMargins(0, 0, 0, 0)
        self.sync_git_path_input = QLineEdit(git_path_container)
        self.sync_git_path_input.setObjectName('sync_git_path_input')
        self.sync_git_path_input.setPlaceholderText('Automatic detection')
        git_path_layout.addWidget(self.sync_git_path_input, 1)
        git_browse_button = QPushButton('Browse', git_path_container)
        git_browse_button.setObjectName('sync_git_path_browse')
        git_browse_button.clicked.connect(self.browse_git_path)
        git_path_layout.addWidget(git_browse_button)
        connection_layout.addWidget(self._sync_labeled_row('Git executable', git_path_container))

        auth_group = QGroupBox('Git authentication')
        auth_layout = QVBoxLayout(auth_group)
        auth_layout.setContentsMargins(12, 16, 12, 12)
        auth_layout.setSpacing(10)
        auth_help = QLabel(
            'Authentication values are stored locally and are never copied to the '
            'synchronization repository.'
        )
        auth_help.setWordWrap(True)
        auth_layout.addWidget(auth_help)

        self.sync_auth_type_combo = QComboBox(auth_group)
        self.sync_auth_type_combo.setObjectName('sync_auth_type_input')
        self.sync_auth_type_combo.addItem('Use Git credential helper / SSH agent', 'system')
        self.sync_auth_type_combo.addItem('HTTPS username and token/password', 'https')
        self.sync_auth_type_combo.addItem('SSH private key', 'ssh')
        self.sync_auth_type_combo.currentIndexChanged.connect(self._update_sync_auth_visibility)
        auth_layout.addWidget(self._sync_labeled_row('Authentication type', self.sync_auth_type_combo))

        self.sync_auth_username_input = QLineEdit(auth_group)
        self.sync_auth_username_input.setObjectName('sync_auth_username_input')
        self.sync_auth_username_row = self._sync_labeled_row('Username', self.sync_auth_username_input)
        auth_layout.addWidget(self.sync_auth_username_row)

        self.sync_auth_secret_input = QLineEdit(auth_group)
        self.sync_auth_secret_input.setObjectName('sync_auth_secret_input')
        self.sync_auth_secret_input.setEchoMode(QLineEdit.Password)
        self.sync_auth_secret_input.setPlaceholderText('Token or password')
        self.sync_auth_secret_row = self._sync_labeled_row('Token/password', self.sync_auth_secret_input)
        auth_layout.addWidget(self.sync_auth_secret_row)

        ssh_key_container = QWidget(auth_group)
        ssh_key_layout = QHBoxLayout(ssh_key_container)
        ssh_key_layout.setContentsMargins(0, 0, 0, 0)
        self.sync_ssh_key_input = QLineEdit(ssh_key_container)
        self.sync_ssh_key_input.setObjectName('sync_ssh_key_input')
        self.sync_ssh_key_input.setPlaceholderText('Optional when an SSH agent is used')
        ssh_key_layout.addWidget(self.sync_ssh_key_input, 1)
        ssh_key_browse_button = QPushButton('Browse', ssh_key_container)
        ssh_key_browse_button.setObjectName('sync_ssh_key_browse')
        ssh_key_browse_button.clicked.connect(self.browse_ssh_key)
        ssh_key_layout.addWidget(ssh_key_browse_button)
        self.sync_ssh_key_row = self._sync_labeled_row('Private key', ssh_key_container)
        auth_layout.addWidget(self.sync_ssh_key_row)

        self.sync_ssh_passphrase_input = QLineEdit(auth_group)
        self.sync_ssh_passphrase_input.setObjectName('sync_ssh_passphrase_input')
        self.sync_ssh_passphrase_input.setEchoMode(QLineEdit.Password)
        self.sync_ssh_passphrase_input.setPlaceholderText('Optional')
        self.sync_ssh_passphrase_row = self._sync_labeled_row('Key passphrase', self.sync_ssh_passphrase_input)
        auth_layout.addWidget(self.sync_ssh_passphrase_row)
        connection_layout.addWidget(auth_group)
        layout.addWidget(connection_group)

        behavior_group = QGroupBox('Automatic synchronization')
        behavior_layout = QVBoxLayout(behavior_group)
        behavior_layout.setContentsMargins(12, 16, 12, 12)
        behavior_layout.setSpacing(10)

        self.sync_push_on_save_checkbox = QCheckBox('Push selected settings after Save', behavior_group)
        self.sync_push_on_save_checkbox.setObjectName('sync_push_on_save_input')
        behavior_layout.addWidget(self.sync_push_on_save_checkbox)

        self.sync_interval_input = QLineEdit(behavior_group)
        self.sync_interval_input.setObjectName('sync_interval_minutes_input')
        self.sync_interval_input.setValidator(QIntValidator(0, 525600, self.sync_interval_input))
        self.sync_interval_input.setPlaceholderText('0 = disabled')
        behavior_layout.addWidget(self._sync_labeled_row('Sync interval (minutes)', self.sync_interval_input))
        layout.addWidget(behavior_group)

        areas_group = QGroupBox('Synchronized areas')
        areas_layout = QVBoxLayout(areas_group)
        areas_layout.setContentsMargins(12, 16, 12, 12)
        areas_layout.setSpacing(6)
        self.sync_area_checkboxes = {}
        for area, label_text in SYNC_AREA_LABELS.items():
            checkbox = QCheckBox(label_text, areas_group)
            checkbox.setObjectName(f'sync_area_{area}_input')
            self.sync_area_checkboxes[area] = checkbox
            areas_layout.addWidget(checkbox)
        areas_layout.addWidget(QLabel('Disabled areas remain local and are not overwritten by Pull.'))
        layout.addWidget(areas_group)

        actions_group = QGroupBox('Synchronization status')
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setContentsMargins(12, 16, 12, 12)
        actions_layout.setSpacing(8)

        action_row = QHBoxLayout()
        self.sync_test_button = QPushButton('Test connection and refresh branches', actions_group)
        self.sync_test_button.setObjectName('sync_test_connection')
        self.sync_test_button.clicked.connect(self.request_sync_test)
        action_row.addWidget(self.sync_test_button)
        self.sync_pull_button = QPushButton('Pull now', actions_group)
        self.sync_pull_button.setObjectName('sync_pull_now')
        self.sync_pull_button.clicked.connect(self.request_sync_pull)
        action_row.addWidget(self.sync_pull_button)
        self.sync_push_button = QPushButton('Push now', actions_group)
        self.sync_push_button.setObjectName('sync_push_now')
        self.sync_push_button.clicked.connect(self.request_sync_push)
        action_row.addWidget(self.sync_push_button)
        actions_layout.addLayout(action_row)

        self.sync_status_label = QLabel(actions_group)
        self.sync_status_label.setObjectName('sync_status')
        self.sync_status_label.setWordWrap(True)
        actions_layout.addWidget(self.sync_status_label)
        self.sync_error_label = QLabel(actions_group)
        self.sync_error_label.setObjectName('sync_error')
        self.sync_error_label.setWordWrap(True)
        self.sync_error_label.setStyleSheet('color: #c0392b;')
        actions_layout.addWidget(self.sync_error_label)
        layout.addWidget(actions_group)
        layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

        self.update_sync_widgets_from_settings()

    def browse_git_path(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select Git executable')
        if path:
            self.sync_git_path_input.setText(path)

    def browse_ssh_key(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select SSH private key')
        if path:
            self.sync_ssh_key_input.setText(path)

    def _update_sync_auth_visibility(self):
        auth_type = self.sync_auth_type_combo.currentData()
        https_visible = auth_type == 'https'
        ssh_visible = auth_type == 'ssh'
        self.sync_auth_username_row.setVisible(https_visible)
        self.sync_auth_secret_row.setVisible(https_visible)
        self.sync_ssh_key_row.setVisible(ssh_visible)
        self.sync_ssh_passphrase_row.setVisible(ssh_visible)

    def update_sync_widgets_from_settings(self):
        """Populate synchronization controls without changing their local status baseline."""
        settings = normalize_sync_settings(self.sync_settings)
        self.sync_settings = settings
        self.sync_enabled_checkbox.setChecked(settings['enabled'])
        self.sync_repository_url_input.setText(settings['repository_url'])
        self.sync_branch_combo.blockSignals(True)
        self.sync_branch_combo.clear()
        if settings['branch']:
            self.sync_branch_combo.addItem(settings['branch'], settings['branch'])
            self.sync_branch_combo.setCurrentText(settings['branch'])
        self.sync_branch_combo.blockSignals(False)

        git_path = settings['git_path'] or detect_git_path()
        self.sync_git_path_input.setText(git_path or '')
        self.sync_push_on_save_checkbox.setChecked(settings['push_on_save'])
        self.sync_interval_input.setText(str(settings['interval_minutes']) if settings['interval_minutes'] else '')
        for area, checkbox in self.sync_area_checkboxes.items():
            checkbox.setChecked(settings['areas'].get(area, False))

        auth = settings['auth']
        index = self.sync_auth_type_combo.findData(auth['type'])
        self.sync_auth_type_combo.setCurrentIndex(index if index >= 0 else 0)
        self.sync_auth_username_input.setText(auth['username'])
        self.sync_auth_secret_input.setText(auth['secret'])
        self.sync_ssh_key_input.setText(auth['ssh_key_path'])
        self.sync_ssh_passphrase_input.setText(auth['ssh_passphrase'])
        self._update_sync_auth_visibility()
        self.update_sync_status(settings)

    def update_sync_branches(self, branches, selected=None):
        """Replace the branch drop-down after a successful remote discovery."""
        selected = selected or self.sync_branch_combo.currentText().strip()
        self.sync_branch_combo.blockSignals(True)
        self.sync_branch_combo.clear()
        for branch in branches:
            self.sync_branch_combo.addItem(branch, branch)
        if selected and self.sync_branch_combo.findData(selected) >= 0:
            self.sync_branch_combo.setCurrentIndex(self.sync_branch_combo.findData(selected))
        elif branches:
            self.sync_branch_combo.setCurrentIndex(0)
        self.sync_branch_combo.blockSignals(False)

    def update_sync_status(self, settings=None):
        """Show persisted synchronization state without creating a notification."""
        settings = normalize_sync_settings(settings or self.sync_settings)
        self.sync_settings = settings
        status = settings.get('status') or 'Not configured'
        self.sync_status_label.setText(f'Status: {status}')
        last_success = settings.get('last_success')
        if last_success:
            self.sync_status_label.setText(f'Status: {status}\nLast successful sync: {last_success}')
        error = settings.get('last_error') or ''
        self.sync_error_label.setText(f'Last error: {error}' if error else '')

    def request_sync_test(self):
        """Test the values currently shown, including unsaved repository settings."""
        try:
            values = self.collect_sync_values()
        except ValueError as error:
            self.sync_error_label.setText(str(error))
            return
        self.sync_test_requested.emit(values)

    def request_sync_pull(self):
        """Request a pull using the values currently shown in the Sync tab."""
        try:
            values = self.collect_sync_values()
        except ValueError as error:
            self.sync_error_label.setText(str(error))
            return
        self.sync_pull_requested.emit(values)

    def request_sync_push(self):
        """Request a push using the values currently shown in the Sync tab."""
        try:
            values = self.collect_sync_values()
        except ValueError as error:
            self.sync_error_label.setText(str(error))
            return
        self.sync_push_requested.emit(values)

    def collect_sync_values(self):
        """Collect only editable synchronization preferences, excluding status fields."""
        interval_text = self.sync_interval_input.text().strip()
        try:
            interval = int(interval_text) if interval_text else 0
        except ValueError:
            raise ValueError('Sync interval: enter a whole number of minutes.') from None
        if interval < 0:
            raise ValueError('Sync interval: enter zero or a positive number of minutes.')
        return {
            'enabled': self.sync_enabled_checkbox.isChecked(),
            'repository_url': self.sync_repository_url_input.text().strip(),
            'branch': self.sync_branch_combo.currentText().strip(),
            'git_path': self.sync_git_path_input.text().strip(),
            'push_on_save': self.sync_push_on_save_checkbox.isChecked(),
            'interval_minutes': interval,
            'areas': {area: checkbox.isChecked() for area, checkbox in self.sync_area_checkboxes.items()},
            'auth': {
                'type': self.sync_auth_type_combo.currentData() or 'system',
                'username': self.sync_auth_username_input.text(),
                'secret': self.sync_auth_secret_input.text(),
                'ssh_key_path': self.sync_ssh_key_input.text().strip(),
                'ssh_passphrase': self.sync_ssh_passphrase_input.text(),
            },
        }

    def sync_settings_changed(self):
        try:
            return self.collect_sync_values() != self.sync_baseline_values
        except ValueError:
            return True

    def create_tabs(self):
        """Create tabs for each category in the schema."""
        titles = {'model_options': 'Transcription', 'recording_options': 'Recording',
                  'post_processing': 'Text output', 'misc': 'General'}
        for category, settings in self.schema.items():
            tab = QWidget()
            tab_layout = QVBoxLayout()
            tab.setLayout(tab_layout)
            tab_layout.setContentsMargins(12, 14, 12, 14)
            tab_layout.setSpacing(14)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(tab)
            self.tabs.addTab(scroll, titles.get(category, category.replace('_', ' ').capitalize()))

            self.create_settings_widgets(tab_layout, category, settings)
            labels = [label for label in tab.findChildren(QLabel)
                      if label.objectName().endswith('_label')]
            if labels:
                label_width = min(240, max(label.fontMetrics().horizontalAdvance(label.text()) for label in labels) + 8)
                for label in labels:
                    label.setWordWrap(True)
                    label.setFixedWidth(label_width)
            tab_layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def create_about_tab(self):
        """Create an About tab showing this fork's identity and version, read live from git
        (never hardcoded) so it stays correct if the remote or fork owner ever changes."""
        tab = QWidget()
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(10)
        tab.setLayout(layout)
        self.tabs.addTab(tab, 'About')

        title = QLabel('WhisperWriter')
        title_font = title.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        commit_hash = self.git_info(['rev-parse', '--short', 'HEAD'])
        commit_date = self.git_info(['log', '-1', '--format=%cd', '--date=format:%Y-%m-%d %H:%M'])
        ahead_count = self.git_info(['rev-list', '--count', 'upstream/main..HEAD'])
        version_text = f"commit {commit_hash}" if commit_hash else "commit: unknown"
        if commit_date:
            version_text += f" ({commit_date})"
        if ahead_count and ahead_count.isdigit():
            version_text += f" — {ahead_count} commit(s) ahead of upstream"
        version_label = QLabel(version_text)
        version_label.setWordWrap(True)
        layout.addWidget(version_label)

        origin_url = self.github_web_url(self.git_info(['remote', 'get-url', 'origin']))
        if origin_url:
            fork_label = QLabel(f'This fork: <a href="{origin_url}">{origin_url}</a>')
            fork_label.setOpenExternalLinks(True)
            fork_label.setWordWrap(True)
            fork_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
            layout.addWidget(fork_label)
        else:
            layout.addWidget(QLabel('This fork: (no "origin" git remote found)'))

        upstream_url = self.github_web_url(self.git_info(['remote', 'get-url', 'upstream']))
        if upstream_url:
            upstream_label = QLabel(f'Forked from (upstream): <a href="{upstream_url}">{upstream_url}</a>')
            upstream_label.setOpenExternalLinks(True)
            upstream_label.setWordWrap(True)
            upstream_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
            layout.addWidget(upstream_label)

        layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def git_info(self, args):
        """Run a git command against this repo's root and return its stripped stdout, or None
        if git isn't available, this isn't a git checkout, or the command fails (e.g. no
        "upstream" remote configured, or upstream/main isn't fetched locally)."""
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        try:
            result = subprocess.run(
                ['git'] + args, cwd=repo_root, capture_output=True, text=True, timeout=3
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def github_web_url(self, remote_url):
        """Normalize a git remote URL (SSH or HTTPS) into a clickable GitHub web URL."""
        if not remote_url:
            return None
        url = remote_url.strip()
        if url.endswith('.git'):
            url = url[:-4]
        if url.startswith('git@github.com:'):
            url = 'https://github.com/' + url[len('git@github.com:'):]
        elif not (url.startswith('https://github.com/') or url.startswith('http://github.com/')):
            return None
        return url

    def create_settings_widgets(self, layout, category, settings):
        """Create widgets for each setting in a category."""
        if category == 'model_options':
            self.add_setting_widget(layout, 'use_api', settings['use_api'], category)
            self.create_api_model_group(layout, settings['api'])
            self.local_model_group = self._settings_group(
                layout, 'Local model', category, settings['local'], sub_category='local')
            self._settings_group(layout, 'Transcription options', category, settings['common'],
                                 keys=('language', 'temperature'), sub_category='common')
            self._settings_group(layout, 'Prompt context (optional)', category, settings['common'],
                                 keys=('initial_prompt',), sub_category='common')
            return
        groups = {
            'recording_options': (
                ('Activation', ('activation_key', 'recording_mode', 'input_backend')),
                ('Microphone', ('sound_device', 'sample_rate')),
                ('Timing', ('silence_duration', 'min_duration'))),
            'post_processing': (
                ('Text formatting', ('remove_trailing_period', 'add_trailing_space', 'remove_capitalization')),
                ('Text insertion', ('input_method', 'writing_key_press_delay'))),
            'misc': (
                ('Status indicators', ('hide_status_window', 'status_window_position', 'show_tray_status_icon')),
                ('Sounds', ('play_toggle_sounds', 'toggle_sound_volume', 'noise_on_completion')),
                ('Diagnostics', ('print_to_terminal',))),
        }
        if category in groups:
            for title, keys in groups[category]:
                self._settings_group(layout, title, category, settings, keys=keys)
            return
        for sub_category, sub_settings in settings.items():
            if isinstance(sub_settings, dict) and 'value' in sub_settings:
                self.add_setting_widget(layout, sub_category, sub_settings, category)
            else:
                for key, meta in sub_settings.items():
                    self.add_setting_widget(layout, key, meta, category, sub_category)

    def _settings_group(self, layout, title, category, settings, keys=None, sub_category=None):
        group = QGroupBox(title)
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(12, 16, 12, 12)
        group_layout.setSpacing(10)
        for key in keys if keys is not None else settings:
            self.add_setting_widget(group_layout, key, settings[key], category, sub_category)
        layout.addWidget(group)
        return group

    def create_api_model_group(self, layout, settings):
        """Group the endpoint and model selectors into one compact model box."""
        group = QGroupBox('API model selection')
        group.setObjectName('model_options_api_models_group')
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(12, 16, 12, 12)
        group_layout.setSpacing(10)

        # Connection details precede model choices; discovery sits below both selectors.
        self.api_model_combos = []
        for key in ('base_url', 'api_key', 'model', 'secondary_model'):
            self.add_setting_widget(group_layout, key, settings[key], 'model_options', 'api')

        group_layout.addWidget(self.create_api_model_refresh_controls())
        self.add_setting_widget(group_layout, 'timeout_seconds', settings['timeout_seconds'], 'model_options', 'api')
        layout.addWidget(group)
        self.api_model_group = group

    def create_api_model_refresh_controls(self):
        """Create model discovery controls beneath both model selectors."""
        row = QWidget()
        row.setObjectName('model_options_api_model_refresh_row')
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 2, 0, 0)
        row_layout.addStretch(1)

        refresh_button = QPushButton('Refresh models', row)
        refresh_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        refresh_button.setToolTip('Load models from the configured API base URL')
        refresh_button.setObjectName('model_options_api_model_refresh')
        row_layout.addWidget(refresh_button)

        status = QLabel('', row)
        status.setObjectName('model_options_api_model_status')
        status.setMinimumWidth(90)
        status.setToolTip('Model discovery status')
        row_layout.addWidget(status)

        help_button = self.create_help_button(
            'Query the configured API base URL for available model IDs. The number of models found is shown here.'
        )
        help_button.setObjectName('model_options_api_model_refresh_help')
        row_layout.addWidget(help_button)

        self.api_model_refresh_button = refresh_button
        self.api_model_status = status
        return row

    def create_buttons(self):
        """Create reset and save buttons, side by side on one line."""
        button_row = QHBoxLayout()
        button_row.addStretch(1)

        reset_button = QPushButton('Discard changes')
        reset_button.setToolTip('Discard unsaved changes and reload the last saved settings')
        reset_button.clicked.connect(self.reset_settings)
        button_row.addWidget(reset_button)

        save_button = QPushButton('Save')
        save_button.clicked.connect(self.save_settings)
        button_row.addWidget(save_button)

        self.main_layout.addLayout(button_row)

    def add_setting_widget(self, layout, key, meta, category, sub_category=None):
        """Add a setting widget to the layout."""
        item_layout = QHBoxLayout()
        display_names = {
            ('model_options', 'api', 'model'): 'Primary model',
            ('model_options', 'api', 'secondary_model'): 'Secondary model',
            ('model_options', 'api', 'base_url'): 'Server URL',
            ('model_options', 'api', 'api_key'): 'API key',
            ('model_options', 'api', 'timeout_seconds'): 'Request timeout (s)',
            ('model_options', None, 'use_api'): 'Use API server',
            ('model_options', 'local', 'condition_on_previous_text'): 'Use previous context',
            ('model_options', 'local', 'vad_filter'): 'Filter silence',
            ('recording_options', None, 'activation_key'): 'Keyboard shortcut',
            ('recording_options', None, 'sound_device'): 'Microphone index',
            ('recording_options', None, 'sample_rate'): 'Sample rate (Hz)',
            ('recording_options', None, 'silence_duration'): 'Silence before stop (ms)',
            ('recording_options', None, 'min_duration'): 'Minimum recording (ms)',
            ('post_processing', None, 'writing_key_press_delay'): 'Delay between keys (s)',
            ('misc', None, 'toggle_sound_volume'): 'Toggle sound volume (%)',
        }
        label_text = display_names.get((category, sub_category, key), key.replace('_', ' ').capitalize())
        label = QLabel(f"{label_text}:")
        label.setMinimumWidth(195)
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        widget = self.create_widget_for_type(key, meta, category, sub_category)
        if not widget:
            return

        help_button = self.create_help_button(meta.get('description', ''))
        is_prompt = category == 'model_options' and sub_category == 'common' and key == 'initial_prompt'

        item_layout.setSpacing(10)
        if not is_prompt:
            item_layout.addWidget(label)
        if isinstance(widget, QWidget):
            if is_prompt:
                widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                item_layout.addWidget(widget, 1)
            else:
                item_layout.addWidget(widget, 1)
        else:
            item_layout.addLayout(widget)
        item_layout.addWidget(help_button)
        layout.addLayout(item_layout)

        if is_prompt:
            # Keep the guide link below the editor so the prompt can use the full row width.
            prompt_link_row = QHBoxLayout()
            prompt_link_row.addWidget(self.create_prompt_link())
            prompt_link_row.addStretch(1)
            layout.addLayout(prompt_link_row)

        # Set object names for the widget, label, and help button
        widget_name = f"{category}_{sub_category}_{key}_input" if sub_category else f"{category}_{key}_input"
        label_name = f"{category}_{sub_category}_{key}_label" if sub_category else f"{category}_{key}_label"
        help_name = f"{category}_{sub_category}_{key}_help" if sub_category else f"{category}_{key}_help"
        
        label.setObjectName(label_name)
        if is_prompt:
            label.deleteLater()
        help_button.setObjectName(help_name)
        
        if isinstance(widget, QWidget):
            widget.setObjectName(widget_name)
        else:
            # If it's a layout (for model_path), set the object name on the QLineEdit
            line_edit = widget.itemAt(0).widget()
            if isinstance(line_edit, QLineEdit):
                line_edit.setObjectName(widget_name)

    def create_widget_for_type(self, key, meta, category, sub_category):
        """Create a widget based on the meta type."""
        meta_type = meta.get('type')
        current_value = self.get_config_value(category, sub_category, key, meta)

        if category == 'model_options' and sub_category == 'api' and key in ('model', 'secondary_model'):
            return self.create_api_model_selector(current_value, primary=key == 'model')
        if category == 'model_options' and sub_category == 'common' and key == 'initial_prompt':
            return self.create_text_edit(current_value)
        if meta_type == 'bool':
            return self.create_checkbox(current_value, key)
        elif meta_type == 'str' and 'options' in meta:
            return self.create_combobox(current_value, meta['options'], readable=key in (
                'recording_mode', 'input_backend', 'input_method', 'status_window_position'))
        elif meta_type == 'str':
            return self.create_line_edit(current_value, key)
        elif meta_type in ['int', 'float']:
            return self.create_line_edit(str(current_value))
        return None

    def create_checkbox(self, value, key):
        widget = QCheckBox()
        widget.setChecked(value)
        if key == 'use_api':
            widget.setObjectName('model_options_use_api_input')
        return widget

    def create_combobox(self, value, options, readable=False):
        widget = QComboBox()
        for option in options:
            widget.addItem(option.replace('_', ' ').capitalize() if readable else option, option)
        widget.setCurrentIndex(widget.findData(value))
        return widget

    def create_api_model_selector(self, value, primary=False):
        """Create an editable API model combo."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        combo = QComboBox(container)
        combo.setEditable(True)
        combo.setMinimumContentsLength(12)
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.setObjectName(
            'model_options_api_model_selector'
            if primary else 'model_options_api_secondary_model_selector'
        )
        if not primary:
            combo.addItem('')
            combo.setPlaceholderText('Disabled — use primary model')
            combo.lineEdit().setPlaceholderText('Disabled — use primary model')
        if value:
            combo.addItem(str(value))
            combo.setCurrentText(str(value))
        layout.addWidget(combo, 1)

        self.api_model_combos = getattr(self, 'api_model_combos', [])
        self.api_model_combos.append(combo)
        if primary:
            self.api_model_combo = combo
        return container

    def create_line_edit(self, value, key=None):
        widget = QLineEdit(value)
        if key in ('language', 'sound_device'):
            widget.setPlaceholderText('Automatic' if key == 'language' else 'System default')
        if key == 'api_key':
            widget.setEchoMode(QLineEdit.Password)
            widget.setText(os.getenv('OPENAI_API_KEY') or value)
        elif key == 'model_path':
            layout = QHBoxLayout()
            layout.addWidget(widget)
            browse_button = QPushButton('Browse')
            browse_button.clicked.connect(lambda: self.browse_model_path(widget))
            layout.addWidget(browse_button)
            layout.setContentsMargins(0, 0, 0, 0)
            container = QWidget()
            container.setLayout(layout)
            return container
        return widget

    def create_text_edit(self, value):
        """Create a compact multi-line editor for longer prompt text."""
        widget = QTextEdit()
        widget.setAcceptRichText(False)
        widget.setTabChangesFocus(True)
        widget.setPlainText(str(value) if value is not None else '')
        widget.setMinimumHeight(80)
        widget.setMaximumHeight(140)
        widget.setPlaceholderText('Optional words, names, or context for the transcription model')
        return widget

    def create_help_button(self, description):
        help_button = QToolButton()
        help_button.setIcon(self.style().standardIcon(QStyle.SP_MessageBoxQuestion))
        help_button.setAutoRaise(True)
        help_button.setToolTip(description)
        help_button.setCursor(Qt.PointingHandCursor)
        help_button.setFocusPolicy(Qt.TabFocus)
        help_button.clicked.connect(lambda: self.show_description(description))
        return help_button

    def create_prompt_link(self):
        """Add a visible link to OpenAI's prompting guidance below the prompt editor."""
        link = QLabel(
            f'<a href="{PROMPTING_GUIDE_URL}">'
            'OpenAI speech-to-text prompting guide</a>'
        )
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        link.setToolTip('Open the OpenAI prompting guide in your browser')
        link.setCursor(Qt.PointingHandCursor)
        link.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        link.setObjectName('model_options_common_initial_prompt_link')
        return link

    def get_config_value(self, category, sub_category, key, meta):
        if sub_category:
            value = ConfigManager.get_config_value(category, sub_category, key)
        else:
            value = ConfigManager.get_config_value(category, key)
        if category == 'model_options' and sub_category == 'common' and key == 'initial_prompt':
            return glossary.normalize_initial_prompt(value)
        return value if value is not None else meta['value']

    def browse_model_path(self, widget):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Whisper Model File", "", "Model Files (*.bin);;All Files (*)")
        if file_path:
            widget.setText(file_path)

    def show_description(self, description):
        """Show a description dialog."""
        QMessageBox.information(self, 'Description', description)

    def on_api_mode_changed(self, use_api):
        """Toggle API/local options and refresh API models when API mode is enabled."""
        self.toggle_api_local_options(use_api)
        if getattr(self, 'api_model_group', None):
            self.api_model_group.setVisible(bool(use_api))
        if getattr(self, 'local_model_group', None):
            self.local_model_group.setVisible(not bool(use_api))
        if use_api:
            self._schedule_model_refresh()
        else:
            self._cancel_model_discovery()
            self._set_model_discovery_status('')

    def _schedule_model_refresh(self):
        """Debounce URL edits so a pasted/typed endpoint triggers one model request."""
        self._cancel_model_discovery()
        if self.use_api_checkbox and self.use_api_checkbox.isChecked():
            self.model_refresh_debounce.start()

    @staticmethod
    def _models_url(base_url):
        """Return the OpenAI-compatible models endpoint for a configured base URL."""
        value = (base_url or '').strip().rstrip('/')
        if not value:
            return QUrl()
        url = QUrl(value + '/models')
        if not url.isValid() or url.scheme() not in ('http', 'https') or not url.host():
            return QUrl()
        return url

    @staticmethod
    def _extract_model_ids(payload):
        """Extract model IDs from OpenAI-compatible and common local-server responses."""
        if isinstance(payload, dict):
            entries = payload.get('data')
            if not isinstance(entries, list):
                entries = payload.get('models')
        else:
            entries = payload
        if not isinstance(entries, list):
            return []

        model_ids = []
        for entry in entries:
            if isinstance(entry, str):
                model_id = entry.strip()
            elif isinstance(entry, dict):
                model_id = entry.get('id') or entry.get('name')
                model_id = model_id.strip() if isinstance(model_id, str) else ''
            else:
                model_id = ''
            if model_id and model_id not in model_ids:
                model_ids.append(model_id)
        return model_ids

    def _set_model_discovery_status(self, text, error=False):
        status = getattr(self, 'api_model_status', None)
        if not status:
            return
        status.setText(text)
        status.setToolTip(text or 'Model discovery status')
        if error:
            status.setStyleSheet('color: #c0392b;')
        else:
            status.setStyleSheet('')

    def _cancel_model_discovery(self):
        self.model_discovery_request_id += 1
        self.model_discovery_timeout.stop()
        reply = self.model_discovery_reply
        self.model_discovery_reply = None
        refresh_button = getattr(self, 'api_model_refresh_button', None)
        if refresh_button:
            refresh_button.setEnabled(True)
        if reply:
            reply.abort()

    def refresh_api_models(self):
        """Load models from the configured API endpoint without blocking the settings UI."""
        self.model_refresh_debounce.stop()
        if self.use_api_checkbox and not self.use_api_checkbox.isChecked():
            return

        self._cancel_model_discovery()
        url = self._models_url(self.api_base_url_input.text() if self.api_base_url_input else '')
        if not url.isValid():
            self._set_model_discovery_status('Invalid URL', error=True)
            return

        self.model_discovery_request_id += 1
        request_id = self.model_discovery_request_id
        request = QNetworkRequest(url)
        request.setHeader(QNetworkRequest.UserAgentHeader, 'WhisperWriter')
        api_key = self.api_key_input.text().strip() if self.api_key_input else ''
        if api_key:
            request.setRawHeader(b'Authorization', f'Bearer {api_key}'.encode('utf-8'))

        self.api_model_refresh_button.setEnabled(False)
        self._set_model_discovery_status('Loading...')
        reply = self.model_discovery_manager.get(request)
        self.model_discovery_reply = reply
        reply.finished.connect(lambda reply=reply, request_id=request_id: self._on_model_discovery_finished(reply, request_id))
        self.model_discovery_timeout.start(5000)

    def _on_model_discovery_timeout(self):
        reply = self.model_discovery_reply
        if not reply:
            return
        self.model_discovery_reply = None
        self.model_discovery_request_id += 1
        reply.abort()
        self.api_model_refresh_button.setEnabled(True)
        self._set_model_discovery_status('Request timed out', error=True)

    def _on_model_discovery_finished(self, reply, request_id):
        reply.deleteLater()
        if request_id != self.model_discovery_request_id:
            return
        self.model_discovery_reply = None
        self.model_discovery_timeout.stop()
        self.api_model_refresh_button.setEnabled(True)

        status_code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if reply.error() != QNetworkReply.NoError:
            self._set_model_discovery_status('Unavailable', error=True)
            return
        if status_code is not None and int(status_code) >= 400:
            self._set_model_discovery_status(f'HTTP {int(status_code)}', error=True)
            return

        try:
            payload = json.loads(bytes(reply.readAll()).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._set_model_discovery_status('Invalid response', error=True)
            return

        model_ids = self._extract_model_ids(payload)
        if not model_ids:
            self._set_model_discovery_status('No models found', error=True)
            return
        self._replace_api_model_options(model_ids)
        count = len(model_ids)
        self._set_model_discovery_status(f'{count} model' + ('' if count == 1 else 's'))

    def _replace_api_model_options(self, model_ids):
        """Replace discovered choices while preserving both last-selected values."""
        for combo_index, combo in enumerate(getattr(self, 'api_model_combos', [])):
            current = combo.currentText().strip()
            combo.blockSignals(True)
            combo.clear()
            if combo_index > 0:
                combo.addItem('')
            combo.addItems(model_ids)
            if current and current not in model_ids:
                combo.insertItem(1 if combo_index > 0 else 0, current)
            if current:
                combo.setCurrentText(current)
            elif combo_index > 0:
                combo.setCurrentIndex(0)
            elif model_ids:
                combo.setCurrentIndex(0)
            combo.blockSignals(False)

    def save_settings(self):
        """Save application and local synchronization settings."""
        try:
            self.collect_current_values()
            sync_values = self.collect_sync_values()
        except ValueError as error:
            QMessageBox.warning(self, 'Invalid setting', str(error))
            return
        changed = self.changed_settings()
        sync_changed = sync_values != self.sync_baseline_values
        if not changed and not sync_changed:
            self.close()
            return

        needs_initial_config = not ConfigManager.config_file_exists()

        live_reload_only = bool(changed) and self.allow_live_reload and all(
            meta.get('live_reload') for *_, meta in changed
        )

        if changed or needs_initial_config:
            self.iterate_settings(self.save_setting)

            # Save the API key to the per-user .env file.
            api_key = ConfigManager.get_config_value('model_options', 'api', 'api_key') or ''
            ConfigManager.ensure_config_directory()
            set_key(ConfigManager.env_path(), 'OPENAI_API_KEY', api_key)
            os.environ['OPENAI_API_KEY'] = api_key

            # Remove the API key from the config
            ConfigManager.set_config_value(None, 'model_options', 'api', 'api_key')

            ConfigManager.save_config()
            self.baseline_values = self.collect_current_values()

        if sync_changed:
            updated_sync_settings = dict(self.sync_settings)
            updated_sync_settings.update(sync_values)
            self.sync_settings = write_sync_settings(updated_sync_settings)
            self.sync_baseline_values = self.collect_sync_values()
            self.sync_settings_saved.emit()

        if not changed and not needs_initial_config and self.sync_settings.get('enabled') and self.sync_settings.get('push_on_save'):
            self.sync_push_requested.emit(self.sync_settings)

        if not changed and not needs_initial_config:
            self.close()
        elif not changed:
            QMessageBox.information(self, 'Settings Saved', 'Settings have been saved. The application will now restart.')
            self.settings_saved.emit()
            self.close()
        elif live_reload_only:
            self.settings_saved_live.emit()
            self.close()
        else:
            QMessageBox.information(self, 'Settings Saved', 'Settings have been saved. The application will now restart.')
            self.settings_saved.emit()
            self.close()

    def save_setting(self, widget, category, sub_category, key, meta):
        value = self.get_widget_value_typed(widget, meta.get('type'))
        if sub_category:
            ConfigManager.set_config_value(value, category, sub_category, key)
        else:
            ConfigManager.set_config_value(value, category, key)

    def reset_settings(self):
        """Reset the settings to the saved values."""
        self._cancel_model_discovery()
        ConfigManager.reload_config()
        self.update_widgets_from_config()
        self.baseline_values = self.collect_current_values()
        self.sync_settings = load_sync_settings()
        self.update_sync_widgets_from_settings()
        self.sync_baseline_values = self.collect_sync_values()

    def collect_current_values(self):
        """Snapshot every widget's current typed value, keyed by (category, sub_category, key)."""
        values = {}

        def collect(widget, category, sub_category, key, meta):
            try:
                values[(category, sub_category, key)] = self.get_widget_value_typed(widget, meta.get('type'))
            except ValueError:
                raise ValueError(f'{key.replace("_", " ").capitalize()}: enter a valid {meta.get("type")} value.') from None

        self.iterate_settings(collect)
        return values

    def changed_settings(self):
        """Return [(category, sub_category, key, meta)] for every widget whose value differs
        from the last-saved/loaded baseline."""
        changed = []

        def check(widget, category, sub_category, key, meta):
            try:
                current = self.get_widget_value_typed(widget, meta.get('type'))
            except ValueError:
                changed.append((category, sub_category, key, meta))
                return
            if current != self.baseline_values.get((category, sub_category, key)):
                changed.append((category, sub_category, key, meta))

        self.iterate_settings(check)
        return changed

    def update_widgets_from_config(self):
        """Update all widgets with values from the current configuration."""
        self.iterate_settings(self.update_widget_value)

    def update_widget_value(self, widget, category, sub_category, key, meta):
        """Update a single widget with the value from the configuration."""
        if sub_category:
            config_value = ConfigManager.get_config_value(category, sub_category, key)
        else:
            config_value = ConfigManager.get_config_value(category, key)

        if (category, sub_category, key) == ('model_options', 'api', 'api_key'):
            config_value = os.getenv('OPENAI_API_KEY') or config_value

        self.set_widget_value(widget, config_value, meta.get('type'))

    def set_widget_value(self, widget, value, value_type):
        """Set the value of the widget."""
        if isinstance(widget, QCheckBox):
            widget.setChecked(value)
        elif isinstance(widget, QComboBox):
            index = widget.findData(value)
            if index >= 0:
                widget.setCurrentIndex(index)
            else:
                widget.setCurrentText(str(value) if value is not None else '')
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value) if value is not None else '')
        elif isinstance(widget, QTextEdit):
            widget.setPlainText(str(value) if value is not None else '')
        elif isinstance(widget, QWidget) and widget.layout():
            input_widget = self._container_input_widget(widget)
            if isinstance(input_widget, QComboBox):
                input_widget.setCurrentText(str(value) if value is not None else '')
            elif isinstance(input_widget, QLineEdit):
                input_widget.setText(str(value) if value is not None else '')
            elif isinstance(input_widget, QTextEdit):
                input_widget.setPlainText(str(value) if value is not None else '')

    def get_widget_value_typed(self, widget, value_type):
        """Get the value of the widget with proper typing."""
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        elif isinstance(widget, QComboBox):
            return widget.currentData() if widget.currentData() is not None else widget.currentText() or None
        elif isinstance(widget, QLineEdit):
            text = widget.text()
            if value_type == 'int':
                return int(text) if text else None
            elif value_type == 'float':
                value = float(text) if text else None
                if value is not None and not math.isfinite(value):
                    raise ValueError('Expected a finite number')
                return value
            else:
                return text or None
        elif isinstance(widget, QTextEdit):
            return widget.toPlainText() or None
        elif isinstance(widget, QWidget) and widget.layout():
            input_widget = self._container_input_widget(widget)
            if isinstance(input_widget, QComboBox):
                return input_widget.currentText() or None
            if isinstance(input_widget, QLineEdit):
                return input_widget.text() or None
            if isinstance(input_widget, QTextEdit):
                return input_widget.toPlainText() or None
        return None

    @staticmethod
    def _container_input_widget(widget):
        """Return the editable control hosted by a composite setting widget."""
        if not widget.layout():
            return None
        for index in range(widget.layout().count()):
            child = widget.layout().itemAt(index).widget()
            if isinstance(child, (QComboBox, QLineEdit, QTextEdit)):
                return child
        return None

    def toggle_api_local_options(self, use_api):
        """Toggle visibility of API and local options."""
        self.iterate_settings(lambda w, c, s, k, m: self.toggle_widget_visibility(w, c, s, k, use_api))

    def toggle_widget_visibility(self, widget, category, sub_category, key, use_api):
        if sub_category in ['api', 'local']:
            widget.setVisible(use_api if sub_category == 'api' else not use_api)
            
            # Also toggle visibility of the corresponding label and help button
            label = self.findChild(QLabel, f"{category}_{sub_category}_{key}_label")
            help_button = self.findChild(QToolButton, f"{category}_{sub_category}_{key}_help")
            
            if label:
                label.setVisible(use_api if sub_category == 'api' else not use_api)
            if help_button:
                help_button.setVisible(use_api if sub_category == 'api' else not use_api)


    def iterate_settings(self, func):
        """Iterate over all settings and apply a function to each."""
        for category, settings in self.schema.items():
            for sub_category, sub_settings in settings.items():
                if isinstance(sub_settings, dict) and 'value' in sub_settings:
                    widget = self.findChild(QWidget, f"{category}_{sub_category}_input")
                    if widget:
                        func(widget, category, None, sub_category, sub_settings)
                else:
                    for key, meta in sub_settings.items():
                        widget = self.findChild(QWidget, f"{category}_{sub_category}_{key}_input")
                        if widget:
                            func(widget, category, sub_category, key, meta)

    def closeEvent(self, event):
        """Confirm before closing the settings window, but only if something was actually
        changed — nothing to lose otherwise, so don't nag."""
        if self.changed_settings() or self.sync_settings_changed():
            reply = QMessageBox.question(
                self,
                'Close without saving?',
                'Are you sure you want to close without saving?',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self.reset_settings()

        self.model_refresh_debounce.stop()
        self._cancel_model_discovery()
        self.settings_closed.emit()
        super().closeEvent(event)
