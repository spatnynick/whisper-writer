import os
import json
import subprocess
import sys
from dotenv import set_key, load_dotenv
from PyQt5.QtWidgets import (
    QApplication, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
    QMessageBox, QShortcut, QTabWidget, QWidget, QSizePolicy, QSpacerItem, QToolButton, QStyle,
    QFileDialog, QTextEdit, QGroupBox
)
from PyQt5.QtCore import Qt, QCoreApplication, QProcess, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ui.base_window import BaseWindow
from utils import ConfigManager

load_dotenv()

PROMPTING_GUIDE_URL = 'https://developers.openai.com/api/docs/guides/speech-to-text'

class SettingsWindow(BaseWindow):
    settings_closed = pyqtSignal()
    settings_saved = pyqtSignal()
    # Emitted instead of settings_saved when every changed setting is flagged
    # `live_reload: true` in the schema — main.py re-applies them without restarting.
    settings_saved_live = pyqtSignal()

    def __init__(self):
        """Initialize the settings window."""
        super().__init__('Settings', 760, 700, frameless=False)
        self.setWindowIcon(QIcon(os.path.join('assets', 'ww-logo.png')))
        self.schema = ConfigManager.get_schema()
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

    def create_tabs(self):
        """Create tabs for each category in the schema."""
        for category, settings in self.schema.items():
            tab = QWidget()
            tab_layout = QVBoxLayout()
            tab.setLayout(tab_layout)
            self.tabs.addTab(tab, category.replace('_', ' ').capitalize())

            self.create_settings_widgets(tab_layout, category, settings)
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
        layout.addWidget(QLabel(version_text))

        origin_url = self.github_web_url(self.git_info(['remote', 'get-url', 'origin']))
        if origin_url:
            fork_label = QLabel(f'This fork: <a href="{origin_url}">{origin_url}</a>')
            fork_label.setOpenExternalLinks(True)
            fork_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
            layout.addWidget(fork_label)
        else:
            layout.addWidget(QLabel('This fork: (no "origin" git remote found)'))

        upstream_url = self.github_web_url(self.git_info(['remote', 'get-url', 'upstream']))
        if upstream_url:
            upstream_label = QLabel(f'Forked from (upstream): <a href="{upstream_url}">{upstream_url}</a>')
            upstream_label.setOpenExternalLinks(True)
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
        deferred_prompt = None
        for sub_category, sub_settings in settings.items():
            if isinstance(sub_settings, dict) and 'value' in sub_settings:
                self.add_setting_widget(layout, sub_category, sub_settings, category)
            elif category == 'model_options' and sub_category == 'api':
                self.create_api_model_group(layout, sub_settings)
                for key in ('timeout_seconds', 'api_key'):
                    if key in sub_settings:
                        self.add_setting_widget(layout, key, sub_settings[key], category, sub_category)
            else:
                for key, meta in sub_settings.items():
                    if category == 'model_options' and sub_category == 'common' and key == 'initial_prompt':
                        deferred_prompt = (key, meta, sub_category)
                        continue
                    self.add_setting_widget(layout, key, meta, category, sub_category)

        if deferred_prompt:
            key, meta, sub_category = deferred_prompt
            layout.addSpacing(28)
            prompt_heading = QLabel('Prompt context (optional)')
            prompt_heading.setObjectName('model_options_common_initial_prompt_heading')
            prompt_font = prompt_heading.font()
            prompt_font.setBold(True)
            prompt_heading.setFont(prompt_font)
            layout.addWidget(prompt_heading)
            self.add_setting_widget(layout, key, meta, category, sub_category)

    def create_api_model_group(self, layout, settings):
        """Group the endpoint and model selectors into one compact model box."""
        group = QGroupBox('API model selection')
        group.setObjectName('model_options_api_models_group')
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(10, 10, 10, 10)
        group_layout.setSpacing(6)

        # The schema order keeps the endpoint above the primary and secondary selectors.
        self.api_model_combos = []
        for key, meta in settings.items():
            if key in ('base_url', 'model', 'secondary_model'):
                self.add_setting_widget(group_layout, key, meta, 'model_options', 'api')

        group_layout.addWidget(self.create_api_model_refresh_controls())
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

        reset_button = QPushButton('Reset')
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
        }
        label_text = display_names.get((category, sub_category, key), key.replace('_', ' ').capitalize())
        label = QLabel(f"{label_text}:")
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        widget = self.create_widget_for_type(key, meta, category, sub_category)
        if not widget:
            return

        help_button = self.create_help_button(meta.get('description', ''))
        is_prompt = category == 'model_options' and sub_category == 'common' and key == 'initial_prompt'

        item_layout.addWidget(label)
        if isinstance(widget, QWidget):
            if is_prompt:
                label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
                widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                widget.setMinimumWidth(500)
                item_layout.addWidget(widget, 1)
            else:
                item_layout.addWidget(widget)
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
            return self.create_combobox(current_value, meta['options'])
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

    def create_combobox(self, value, options):
        widget = QComboBox()
        widget.addItems(options)
        widget.setCurrentText(value)
        return widget

    def create_api_model_selector(self, value, primary=False):
        """Create an editable API model combo."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        combo = QComboBox(container)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.setObjectName(
            'model_options_api_model_selector'
            if primary else 'model_options_api_secondary_model_selector'
        )
        if not primary:
            combo.addItem('')
            combo.setPlaceholderText('Disabled — use primary model')
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
            return ConfigManager.get_config_value(category, sub_category, key) or meta['value']
        return ConfigManager.get_config_value(category, key) or meta['value']

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
        if use_api:
            self._schedule_model_refresh()
        else:
            self._cancel_model_discovery()
            self._set_model_discovery_status('')

    def _schedule_model_refresh(self):
        """Debounce URL edits so a pasted/typed endpoint triggers one model request."""
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
        """Save the settings to the config file and .env file. If nothing actually changed,
        this is a no-op close. If every changed setting is schema-flagged `live_reload: true`
        and the app's components already exist, apply them in place instead of restarting."""
        changed = self.changed_settings()
        if not changed:
            self.close()
            return

        live_reload_only = self.allow_live_reload and all(meta.get('live_reload') for *_, meta in changed)

        self.iterate_settings(self.save_setting)

        # Save the API key to the .env file
        api_key = ConfigManager.get_config_value('model_options', 'api', 'api_key') or ''
        set_key('.env', 'OPENAI_API_KEY', api_key)
        os.environ['OPENAI_API_KEY'] = api_key

        # Remove the API key from the config
        ConfigManager.set_config_value(None, 'model_options', 'api', 'api_key')

        ConfigManager.save_config()
        self.baseline_values = self.collect_current_values()

        if live_reload_only:
            self.settings_saved_live.emit()
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
        ConfigManager.reload_config()
        self.update_widgets_from_config()
        self.baseline_values = self.collect_current_values()

    def collect_current_values(self):
        """Snapshot every widget's current typed value, keyed by (category, sub_category, key)."""
        values = {}

        def collect(widget, category, sub_category, key, meta):
            values[(category, sub_category, key)] = self.get_widget_value_typed(widget, meta.get('type'))

        self.iterate_settings(collect)
        return values

    def changed_settings(self):
        """Return [(category, sub_category, key, meta)] for every widget whose value differs
        from the last-saved/loaded baseline."""
        changed = []

        def check(widget, category, sub_category, key, meta):
            current = self.get_widget_value_typed(widget, meta.get('type'))
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

        self.set_widget_value(widget, config_value, meta.get('type'))

    def set_widget_value(self, widget, value, value_type):
        """Set the value of the widget."""
        if isinstance(widget, QCheckBox):
            widget.setChecked(value)
        elif isinstance(widget, QComboBox):
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
            return widget.currentText() or None
        elif isinstance(widget, QLineEdit):
            text = widget.text()
            if value_type == 'int':
                return int(text) if text else None
            elif value_type == 'float':
                return float(text) if text else None
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
        if self.changed_settings():
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
            ConfigManager.reload_config()  # Revert to last saved configuration
            self.update_widgets_from_config()

        self.settings_closed.emit()
        super().closeEvent(event)
