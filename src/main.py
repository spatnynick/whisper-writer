import os
import sys
import logging
from datetime import datetime
from audioplayer import AudioPlayer
from PyQt5.QtCore import QObject, QProcess, QTimer, Qt, QPoint, pyqtSignal, pyqtSlot
from PyQt5.QtDBus import QDBusConnection
from PyQt5.QtGui import QIcon, QPainter, QColor
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QMessageBox, QStyle

from key_listener import KeyListener
from escape_guard import EscapeGuard
from result_thread import ResultThread
from ui.settings_window import SettingsWindow
from ui.status_window import StatusWindow
from transcription import create_local_model
from input_simulation import InputSimulator
from utils import ConfigManager
from config_sync import SyncWorker, load_sync_settings, save_sync_settings, selected_areas


logger = logging.getLogger(__name__)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
UPDATE_SCRIPT = os.path.join(PROJECT_ROOT, 'update.sh')


class WhisperWriterApp(QObject):
    activationRequested = pyqtSignal()
    deactivationRequested = pyqtSignal()
    cancelRequested = pyqtSignal()
    # A held activation selects the other configured model.  Keep this short enough that
    # model selection does not make the hotkey feel unresponsive, while leaving a clear
    # distinction between a tap and a hold.
    MODEL_SWITCH_HOLD_MS = 400

    def __init__(self):
        """
        Initialize the application, opening settings window if no configuration file is found.
        """
        super().__init__()
        self.app = QApplication(sys.argv)
        self.app.setWindowIcon(QIcon(os.path.join('assets', 'ww-logo.png')))
        # The app is tray-only now (no permanently-open main window) — without this, Qt quits
        # the whole app as soon as Settings (the only window shown by default) is closed, since
        # by default it counts as "the last window". The tray icon should control lifetime.
        self.app.setQuitOnLastWindowClosed(False)

        self.failed_recordings = []
        self._retry_status = None
        self._retrying = False
        self._shutdown_action = None
        self._continue_recording = False
        self._update_process = None
        self._update_phase = None
        self._model_slot = 0
        self.active_model_name = None
        self._model_hold_pending = False
        self._model_hold_triggered = False
        self._model_hold_action = None
        self._model_hold_timer = QTimer(self)
        self._model_hold_timer.setSingleShot(True)
        self._model_hold_timer.setInterval(self.MODEL_SWITCH_HOLD_MS)
        self._model_hold_timer.timeout.connect(self._on_model_hold)
        # Some Linux tray backends do not emit DoubleClick.  They emit two Trigger
        # activations instead, so keep a short click state machine for both forms.
        self._tray_click_pending = False
        self._tray_ignore_double_click = False
        self._sync_worker = None
        self._sync_pending_action = None
        self._sync_pending_settings = None
        self._sync_restart_after = False
        self._sync_current_restart_after = False
        self._sync_settings = None
        self._sync_needs_attention = False
        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._on_sync_timer)
        self.activationRequested.connect(self.on_activation, Qt.QueuedConnection)
        self.deactivationRequested.connect(self.on_deactivation, Qt.QueuedConnection)
        self.cancelRequested.connect(self.on_cancel_key, Qt.QueuedConnection)
        ConfigManager.initialize()
        self._sync_settings = load_sync_settings()
        self._sync_needs_attention = self._sync_settings.get('status') == 'Error'

        self.settings_window = SettingsWindow()
        self.settings_window.settings_closed.connect(self.on_settings_closed)
        self.settings_window.settings_saved.connect(self.on_settings_saved)
        self.settings_window.settings_saved_live.connect(self.on_settings_saved_live)
        self.settings_window.sync_settings_saved.connect(self.on_sync_settings_saved)
        self.settings_window.sync_test_requested.connect(self.test_sync_connection)
        self.settings_window.sync_pull_requested.connect(self.pull_sync_settings)
        self.settings_window.sync_push_requested.connect(self.push_sync_settings)
        self.settings_window.update_sync_status(self._sync_settings)
        self._configure_sync_timer()

        if ConfigManager.config_file_exists():
            self.initialize_components()
        else:
            print('No valid configuration file found. Opening settings window...')
            self.settings_window.show()

        self._setup_sleep_resume_watcher()

    def _setup_sleep_resume_watcher(self):
        """
        pynput's X11 hotkey listener can go stale across a suspend/resume cycle (the
        underlying Xlib connection doesn't reliably recover on its own), silently killing the
        activation hotkey until the app is restarted by hand. Watch logind's PrepareForSleep
        signal and restart the key listener a couple of seconds after resume (arg == False) to
        give X11 time to come back up first.
        """
        bus = QDBusConnection.systemBus()
        if not bus.isConnected():
            print('Could not connect to system D-Bus; hotkey listener will not auto-recover after suspend.')
            return
        connected = bus.connect(
            'org.freedesktop.login1',
            '/org/freedesktop/login1',
            'org.freedesktop.login1.Manager',
            'PrepareForSleep',
            self.on_prepare_for_sleep
        )
        if not connected:
            print('Could not subscribe to logind PrepareForSleep; hotkey listener will not auto-recover after suspend.')

    @pyqtSlot(bool)
    def on_prepare_for_sleep(self, going_to_sleep: bool):
        """Called by logind before suspend (True) and again after resume (False)."""
        if going_to_sleep:
            if getattr(self, 'escape_guard', None):
                self.escape_guard.close()
            if getattr(self, 'result_thread', None):
                self.stop_result_thread()
            return
        QTimer.singleShot(2000, self._restart_key_listener_after_resume)

    def _restart_key_listener_after_resume(self):
        if not getattr(self, 'key_listener', None):
            return
        print('Resumed from sleep — restarting hotkey listener.')
        self.key_listener.stop()
        self.key_listener.start()

    def initialize_components(self):
        """
        Initialize the components of the application.
        """
        if getattr(self, 'key_listener', None) is not None:
            return
        self.escape_guard = EscapeGuard(self)
        self.input_simulator = InputSimulator()

        self.key_listener = KeyListener()
        self.key_listener.add_callback("on_activate", self.activationRequested.emit)
        self.key_listener.add_callback("on_deactivate", self.deactivationRequested.emit)
        self.key_listener.add_callback("on_cancel_key", self.cancelRequested.emit)

        model_options = ConfigManager.get_config_section('model_options')
        self.local_model = create_local_model() if not model_options.get('use_api') else None

        self.result_thread = None
        self.current_status = 'idle'
        self.last_transcript = None

        # Kept alive as instance attributes rather than created fresh per play() call: a
        # non-blocking AudioPlayer's GStreamer pipeline is torn down as soon as the Python
        # object is garbage-collected, which (with no reference held) happens essentially
        # immediately after play(block=False) returns — before any audio is actually output.
        self.recording_start_sound = AudioPlayer(os.path.join('assets', 'recording-start.wav'))
        self.recording_stop_sound = AudioPlayer(os.path.join('assets', 'recording-stop.wav'))
        toggle_volume = ConfigManager.get_config_value('misc', 'toggle_sound_volume')
        self.recording_start_sound.volume = toggle_volume
        self.recording_stop_sound.volume = toggle_volume

        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.status_window = StatusWindow()
            self.status_window.closeSignal.connect(self.stop_result_thread)
            self.status_window.retryRequested.connect(self.retry_transcription)

        self.create_tray_icon()
        self.key_listener.start()

        # Only safe to skip a restart on save once these components actually exist — on a
        # first run (no config.yaml yet), settings_window.save_settings() must still take the
        # restart path so this method runs for the first time.
        self.settings_window.allow_live_reload = True

    def create_tray_icon(self):
        """
        Create the system tray icon and its context menu.
        """
        self.tray_icon_idle = QIcon(os.path.join('assets', 'ww-logo.png'))
        self.tray_icon_recording = QIcon(os.path.join('assets', 'ww-logo-recording.png'))
        self.tray_icon_transcribing = QIcon(os.path.join('assets', 'ww-logo-transcribing.png'))
        self.tray_icon_error = QIcon(os.path.join('assets', 'ww-logo-error.svg'))
        self.tray_icon_updating = QIcon(os.path.join('assets', 'ww-logo-updating.svg'))

        self.tray_icon = QSystemTrayIcon(self.tray_icon_idle, self.app)
        self.tray_icon.setToolTip('WhisperWriter — Idle')
        self.tray_icon.activated.connect(self.on_tray_activated)
        self._tray_click_timer = QTimer(self)
        self._tray_click_timer.setSingleShot(True)
        self._tray_click_timer.setInterval(450)
        self._tray_click_timer.timeout.connect(self._clear_tray_click)
        self._tray_ignore_timer = QTimer(self)
        self._tray_ignore_timer.setSingleShot(True)
        self._tray_ignore_timer.setInterval(450)
        self._tray_ignore_timer.timeout.connect(self._clear_tray_double_click_guard)

        tray_menu = QMenu()

        settings_action = QAction(self.app.style().standardIcon(QStyle.SP_FileDialogDetailedView), 'Open Settings', self.app)
        settings_action.triggered.connect(self.open_settings)
        tray_menu.addAction(settings_action)

        update_action = QAction(self.app.style().standardIcon(QStyle.SP_ArrowUp), 'Update', self.app)
        update_action.triggered.connect(self.check_for_updates)
        self.update_action = update_action
        tray_menu.addAction(update_action)

        tray_menu.addSeparator()

        self.copy_last_transcript_action = QAction(self.app.style().standardIcon(QStyle.SP_DialogSaveButton), 'Copy Last Transcript', self.app)
        self.copy_last_transcript_action.setEnabled(False)
        self.copy_last_transcript_action.triggered.connect(self.copy_last_transcript)
        tray_menu.addAction(self.copy_last_transcript_action)

        self.retry_transcript_action = QAction(self.app.style().standardIcon(QStyle.SP_BrowserReload), 'Retry Transcription', self.app)
        self.retry_transcript_action.triggered.connect(self.retry_transcription)
        tray_menu.addAction(self.retry_transcript_action)
        self._update_retry_actions()

        tray_menu.addSeparator()

        exit_action = QAction(self.app.style().standardIcon(QStyle.SP_DialogCloseButton), 'Exit', self.app)
        exit_action.triggered.connect(self.exit_app)
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
        self.update_tray_icon('idle')

    def open_settings(self):
        # Wait until the tray menu releases its popup grab before requesting focus.
        QTimer.singleShot(0, self.settings_window.show_and_activate)

    def _sync_is_busy(self):
        """Return whether recording/transcription work must keep Git paused."""
        return bool(getattr(self, 'result_thread', None)) or getattr(self, 'current_status', None) in (
            'recording', 'transcribing'
        )

    def _set_sync_status(self, status, error=None, remote_commit=None, persist=False):
        """Update synchronization status without opening a dialog or notification."""
        settings = load_sync_settings()
        settings['status'] = status
        if error is not None:
            settings['last_error'] = error
        if status in ('Up to date', 'Connection successful'):
            settings['last_error'] = ''
        if remote_commit:
            settings['last_remote_commit'] = remote_commit
        if persist:
            if status in ('Up to date', 'Connection successful'):
                settings['last_success'] = datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
            settings = save_sync_settings(settings)
        self._sync_settings = settings
        self._sync_needs_attention = status in ('Error', 'Not in sync', 'Push pending')
        if getattr(self, 'settings_window', None):
            self.settings_window.update_sync_status(settings)
        if getattr(self, 'tray_icon', None):
            self.update_tray_icon(getattr(self, 'current_status', 'idle'))

    def _configure_sync_timer(self):
        """Apply the locally configured free-form synchronization interval."""
        timer = getattr(self, '_sync_timer', None)
        if timer is None:
            return
        timer.stop()
        settings = load_sync_settings()
        self._sync_settings = settings
        minutes = settings.get('interval_minutes', 0)
        if settings.get('enabled') and minutes > 0:
            # QTimer uses a signed 32-bit millisecond interval.  The input itself remains a
            # free-form minute value; extremely long intervals are simply capped by Qt.
            timer.start(min(minutes * 60 * 1000, 2_147_000_000))

    def _on_sync_timer(self):
        settings = load_sync_settings()
        if settings.get('enabled') and settings.get('interval_minutes', 0) > 0:
            self.request_sync('auto', settings=settings)
            self._configure_sync_timer()

    def _queue_sync(self, action, settings, restart_after=False):
        """Remember one sync operation until the current worker/recording is finished."""
        if self._sync_pending_action is None or action != 'auto':
            self._sync_pending_action = action
            self._sync_pending_settings = settings
        self._sync_restart_after = self._sync_restart_after or restart_after

    def request_sync(self, action, settings=None, restart_after=False):
        """Start or queue a synchronization operation without blocking the GUI."""
        settings = settings or load_sync_settings()
        if action != 'test' and not settings.get('enabled'):
            self._set_sync_status('Disabled', persist=False)
            return
        if action != 'test' and not selected_areas(settings):
            self._set_sync_status('No areas selected', persist=False)
            return

        if getattr(self, '_sync_worker', None) is not None:
            self._queue_sync(action, settings, restart_after)
            return
        if self._sync_is_busy():
            self._queue_sync(action, settings, restart_after)
            self._set_sync_status('Paused until idle', persist=False)
            return
        if getattr(self, '_shutdown_action', None) or getattr(self, '_update_phase', None) == 'updating':
            self._queue_sync(action, settings, restart_after)
            return

        self._start_sync_worker(action, settings, restart_after)

    def _start_sync_worker(self, action, settings, restart_after=False):
        labels = {'test': 'Testing connection', 'push': 'Pushing settings',
                  'pull': 'Pulling settings', 'auto': 'Checking synchronization'}
        self._sync_current_restart_after = restart_after
        self._set_sync_status(labels.get(action, 'Synchronizing'), persist=False)
        worker = SyncWorker(
            action,
            settings,
            getattr(getattr(ConfigManager, '_instance', None), 'config', {}) or {},
            last_remote_commit=settings.get('last_remote_commit', ''),
            parent=self,
        )
        worker.completed.connect(self._on_sync_completed)
        worker.failed.connect(self._on_sync_failed)
        worker.finished.connect(self._on_sync_worker_finished)
        self._sync_worker = worker
        worker.start()

    def _on_sync_completed(self, result):
        """Handle a worker result on the GUI thread."""
        action = result.get('action') if isinstance(result, dict) else None
        remote_commit = result.get('remote_commit', '') if isinstance(result, dict) else ''
        try:
            if action == 'test':
                settings = load_sync_settings()
                self.settings_window.update_sync_branches(
                    result.get('branches', []), result.get('branch') or result.get('default_branch')
                )
                self._set_sync_status('Connection successful', remote_commit=remote_commit, persist=True)
                return

            if action == 'pull' and result.get('changed'):
                old_config = getattr(getattr(ConfigManager, '_instance', None), 'config', None)
                ConfigManager.replace_config(result['config'])
                try:
                    ConfigManager.save_config()
                except Exception:
                    if old_config is not None:
                        ConfigManager.replace_config(old_config)
                    raise
                if getattr(self, 'settings_window', None):
                    self.settings_window.update_widgets_from_config()
                    self.settings_window.baseline_values = self.settings_window.collect_current_values()
                self._sync_current_restart_after = True

            # A no-op interval check must not rewrite the local status file.  This keeps the
            # managed synchronization directory completely quiet when neither side changed;
            # successful operations that changed something (or clear a prior error) still
            # persist their status normally.
            previous_settings = load_sync_settings()
            persist_success = bool(result.get('changed')) or action == 'test' or bool(
                previous_settings.get('last_error')
            )
            self._set_sync_status('Up to date', remote_commit=remote_commit, persist=persist_success)
        except Exception as error:
            self._on_sync_failed(str(error))

    def _on_sync_failed(self, error):
        logger.warning('Synchronization failed: %s', error)
        self._set_sync_status('Error', error=error, persist=True)

    def _on_sync_worker_finished(self):
        worker = getattr(self, '_sync_worker', None)
        self._sync_worker = None
        if worker is not None:
            worker.deleteLater()

        restart_after = self._sync_current_restart_after
        self._sync_current_restart_after = False
        if restart_after:
            self._sync_pending_action = None
            self._sync_pending_settings = None
            if not self._shutdown_action:
                self._request_shutdown('restart')
            return

        if self._sync_pending_action is not None and not self._sync_is_busy():
            QTimer.singleShot(0, self._run_queued_sync)

    def _run_queued_sync(self):
        if self._sync_worker is not None or self._sync_is_busy():
            return
        action = self._sync_pending_action
        settings = getattr(self, '_sync_pending_settings', None)
        if action is None:
            return
        self._sync_pending_action = None
        self._sync_pending_settings = None
        restart_after = self._sync_restart_after
        self._sync_restart_after = False
        self.request_sync(action, settings=settings, restart_after=restart_after)

    def on_sync_settings_saved(self):
        self._sync_settings = load_sync_settings()
        self._configure_sync_timer()
        if getattr(self, 'settings_window', None):
            self.settings_window.update_sync_status(self._sync_settings)

    def on_settings_saved(self):
        """Push saved settings first when configured, then perform the normal restart."""
        settings = load_sync_settings()
        if settings.get('enabled') and settings.get('push_on_save') and selected_areas(settings):
            self.request_sync('push', settings=settings, restart_after=True)
        else:
            self.restart_app()

    def on_settings_saved_live(self):
        self.apply_live_settings()
        settings = load_sync_settings()
        if settings.get('enabled') and settings.get('push_on_save') and selected_areas(settings):
            self.request_sync('push', settings=settings)

    def test_sync_connection(self, settings):
        self.request_sync('test', settings=settings)

    def pull_sync_settings(self, settings):
        # A manual Pull is followed by the normal application restart, even when the selected
        # values already match locally. Automatic interval pulls restart only when they apply a
        # changed remote configuration.
        self.request_sync('pull', settings=settings, restart_after=True)

    def push_sync_settings(self, settings):
        self.request_sync('push', settings=settings)

    def _clear_tray_click(self):
        self._tray_click_pending = False

    def _clear_tray_double_click_guard(self):
        self._tray_ignore_double_click = False

    def _toggle_settings_from_tray(self):
        if self.settings_window.isVisible():
            self.settings_window.close()
        else:
            self.open_settings()

    def on_tray_activated(self, reason):
        """Toggle Settings for a double-click across Qt tray backends."""
        if reason == QSystemTrayIcon.Context:
            return
        if reason == QSystemTrayIcon.DoubleClick:
            # KDE/status-notifier implementations can send two Trigger signals followed
            # by DoubleClick.  The second Trigger already toggled the window.
            if getattr(self, '_tray_ignore_double_click', False):
                self._tray_ignore_double_click = False
                timer = getattr(self, '_tray_ignore_timer', None)
                if timer:
                    timer.stop()
                return
            self._tray_click_pending = False
            timer = getattr(self, '_tray_click_timer', None)
            if timer:
                timer.stop()
            self._toggle_settings_from_tray()
            return
        if reason != QSystemTrayIcon.Trigger:
            return
        if getattr(self, '_tray_click_pending', False):
            self._tray_click_pending = False
            timer = getattr(self, '_tray_click_timer', None)
            if timer:
                timer.stop()
            self._tray_ignore_double_click = True
            ignore_timer = getattr(self, '_tray_ignore_timer', None)
            if ignore_timer:
                ignore_timer.start()
            self._toggle_settings_from_tray()
            return
        self._tray_click_pending = True
        timer = getattr(self, '_tray_click_timer', None)
        if timer:
            timer.start()

    def _show_update_message(self, title, message, icon=QMessageBox.Information):
        parent = self.settings_window if self.settings_window.isVisible() else None
        if icon == QMessageBox.Warning:
            QMessageBox.warning(parent, title, message)
        else:
            QMessageBox.information(parent, title, message)

    def check_for_updates(self):
        """Check origin/<current branch> without blocking the GUI."""
        if self._update_process and self._update_process.state() != QProcess.NotRunning:
            return
        if self._shutdown_action:
            return
        if self.result_thread is not None or self.failed_recordings:
            detail = (
                'Retry the failed transcription or start a new recording before updating.'
                if self.failed_recordings and self.result_thread is None
                else 'Finish the current recording or transcription before updating.'
            )
            self._show_update_message('Update unavailable', detail, QMessageBox.Warning)
            return

        self.update_action.setEnabled(False)
        self._set_update_indicator('checking')
        process = QProcess(self)
        process.setWorkingDirectory(PROJECT_ROOT)
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.finished.connect(self._on_update_check_finished)
        process.errorOccurred.connect(self._on_update_check_error)
        self._update_process = process
        self._update_phase = 'checking'
        process.start(UPDATE_SCRIPT, ['--check-only'])

    def _clear_update_process(self):
        process = self._update_process
        self._update_process = None
        self._update_phase = None
        self.update_action.setEnabled(True)
        if process:
            process.deleteLater()
        return process

    def _set_update_indicator(self, phase):
        self.tray_icon.setIcon(self.tray_icon_updating)
        self.tray_icon.setToolTip(f'WhisperWriter — {phase}...')

    def _restore_update_indicator(self):
        self.update_tray_icon(getattr(self, 'current_status', 'idle'))

    def _on_update_check_error(self, error):
        process = self._update_process
        if process is None or process.state() != QProcess.NotRunning:
            return
        self._clear_update_process()
        self._restore_update_indicator()
        logger.warning('Update check process failed: %s', error)
        self._show_update_message(
            'Update check failed',
            'WhisperWriter could not check GitHub. Check your network connection and try again.',
            QMessageBox.Warning,
        )

    def _on_update_check_finished(self, exit_code, exit_status):
        process = self._update_process
        if process is None:
            return
        output = bytes(process.readAllStandardOutput()).decode('utf-8', errors='replace')
        self._clear_update_process()

        if exit_code == 0 and 'NO_UPDATE' in output:
            self._restore_update_indicator()
            self._show_update_message('WhisperWriter', 'No update available. This installation is current.')
            return
        if exit_code == 10 and 'UPDATE_AVAILABLE' in output:
            self._start_update()
            return

        logger.warning('Update check exited with code %s: %s', exit_code, output.strip())
        self._restore_update_indicator()
        self._show_update_message(
            'Update check failed',
            'WhisperWriter could not determine whether an update is available. Check the application log and try again.',
            QMessageBox.Warning,
        )

    def _start_update(self):
        # Recording or shutdown may have begun while the asynchronous fetch ran.
        if self._shutdown_action or self.result_thread is not None or self.failed_recordings:
            self._restore_update_indicator()
            return
        self.update_action.setEnabled(False)
        self._set_update_indicator('updating')
        process = QProcess(self)
        process.setWorkingDirectory(PROJECT_ROOT)
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.finished.connect(self._on_update_finished)
        process.errorOccurred.connect(self._on_update_error)
        self._update_process = process
        self._update_phase = 'updating'
        process.start(UPDATE_SCRIPT, ['--no-restart'])

    def _on_update_error(self, error):
        if error == QProcess.FailedToStart:
            self._on_update_finished(-1, QProcess.CrashExit)

    def _on_update_finished(self, exit_code, exit_status):
        process = self._update_process
        if process is None:
            return
        output = bytes(process.readAllStandardOutput()).decode('utf-8', errors='replace')
        self._clear_update_process()
        if exit_code == 0 and exit_status == QProcess.NormalExit:
            # The updater was launched with --no-restart, so a successful tray update
            # must restart this process to load the new checkout and dependencies.
            self._request_shutdown('restart')
            return
        logger.warning('Updater failed with code %s: %s', exit_code, output.strip())
        self._restore_update_indicator()
        self._show_update_message(
            'Update failed',
            'WhisperWriter could not complete the update. Check the application log and run ./update.sh to retry.',
            QMessageBox.Warning,
        )
        if self._shutdown_action:
            self._finish_shutdown()

    def _tray_icon_with_sync_marker(self, icon):
        """Overlay a small red sync marker on the idle icon when sync needs attention."""
        if not getattr(self, '_sync_needs_attention', False):
            return icon
        try:
            pixmap = icon.pixmap(64, 64)
            if pixmap.isNull():
                return icon
            radius = max(4, pixmap.width() // 8)
            center = QPoint(pixmap.width() - radius - 2, radius + 2)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QColor(255, 255, 255))
            painter.setBrush(QColor(211, 47, 47))
            painter.drawEllipse(center, radius, radius)
            painter.end()
            return QIcon(pixmap)
        except (AttributeError, TypeError):
            # Keeps lightweight test doubles and unusual tray backends usable.
            return icon

    def update_tray_icon(self, status):
        """
        Update the system tray icon to reflect the current recording/transcribing status,
        if enabled via the misc.show_tray_status_icon setting.
        """
        if status in ('idle', 'cancel') and self.failed_recordings and self._retry_status == 'error':
            status = 'error'
        active_model = getattr(self, 'active_model_name', None)
        model_suffix = ''
        if status in ('recording', 'transcribing', 'error') and active_model:
            model_suffix = f'\nModel: {active_model}'
        idle_icon = self._tray_icon_with_sync_marker(self.tray_icon_idle)
        if status != 'error' and not ConfigManager.get_config_value('misc', 'show_tray_status_icon'):
            self.tray_icon.setIcon(idle_icon)
            self.tray_icon.setToolTip(f'WhisperWriter{model_suffix}')
            return

        if status == 'recording':
            self.tray_icon.setIcon(self.tray_icon_recording)
            self.tray_icon.setToolTip(f'WhisperWriter — Recording...{model_suffix}')
        elif status == 'transcribing':
            self.tray_icon.setIcon(self.tray_icon_transcribing)
            self.tray_icon.setToolTip(f'WhisperWriter — Transcribing...{model_suffix}')
        elif status == 'error':
            self.tray_icon.setIcon(self.tray_icon_error)
            suffix = ' — retry available' if self.failed_recordings else ''
            self.tray_icon.setToolTip(f'WhisperWriter — Error{suffix}{model_suffix}')
        elif status in ('idle', 'cancel'):
            self.tray_icon.setIcon(idle_icon)
            sync_suffix = ' — Sync needs attention' if getattr(self, '_sync_needs_attention', False) else ''
            self.tray_icon.setToolTip(f'WhisperWriter — Idle{sync_suffix}')

    def _update_retry_actions(self):
        count = len(self.failed_recordings)
        available = bool(count) and self.result_thread is None and not self._shutdown_action
        self.retry_transcript_action.setEnabled(available)

    def on_transcription_failed(self, recording):
        # A failed retry retains the original entry rather than duplicating it.
        if not self._retrying:
            self.failed_recordings[:] = [recording]
            worker = getattr(self, 'result_thread', None)
            self._retry_status = (
                'cancelled'
                if worker and getattr(worker, 'is_cancelled', False) is True
                else 'error'
            )
        self._update_retry_actions()

    def _configured_model_pair(self):
        """Return the configured primary and optional secondary model for this backend."""
        options = ConfigManager.get_config_section('model_options')
        if options.get('use_api'):
            api_options = options.get('api', {})
            primary = api_options.get('model')
            secondary = api_options.get('secondary_model')
        else:
            local_options = options.get('local', {})
            primary = local_options.get('model')
            secondary = None
        return primary, secondary

    def _selected_model(self):
        """Return the selected configured model slot."""
        primary, secondary = self._configured_model_pair()
        if getattr(self, '_model_slot', 0) == 1 and secondary:
            return secondary
        return primary

    def _begin_model_hold(self, action='stop'):
        """Wait briefly to distinguish a tap from a long model-switch press.

        ``stop`` is used for a later activation while recording: a tap stops capture.
        ``initial`` is used for the first activation of a new recording: a tap leaves
        capture running, while a hold starts that recording on the secondary model.
        """
        timer = getattr(self, '_model_hold_timer', None)
        if timer is None:
            return
        if timer.isActive():
            return
        self._model_hold_pending = True
        self._model_hold_triggered = False
        self._model_hold_action = action
        timer.start()

    def _cancel_model_hold(self):
        timer = getattr(self, '_model_hold_timer', None)
        if timer is not None:
            timer.stop()
        self._model_hold_pending = False
        self._model_hold_triggered = False
        self._model_hold_action = None

    def _recording_worker_active(self, worker=None):
        """Handle the brief gap before the worker's queued recording status reaches Qt."""
        worker = worker or getattr(self, 'result_thread', None)
        if not worker or not worker.isRunning() or not worker.is_recording:
            return False
        if self.current_status == 'recording':
            return True
        return self.current_status == 'idle' and bool(getattr(worker, 'is_recording', False))

    def _on_model_hold(self):
        """Switch models after the activation shortcut has been held while recording."""
        if not getattr(self, '_model_hold_pending', False):
            return
        worker = getattr(self, 'result_thread', None)
        if not self._recording_worker_active(worker):
            self._cancel_model_hold()
            return
        _primary, secondary = self._configured_model_pair()
        if not secondary:
            self._cancel_model_hold()
            self._finish_short_model_press()
            return
        next_slot = 1 - self._model_slot
        self._model_slot = next_slot
        model_name = self._selected_model()
        if not worker.set_recording_model(model_name):
            self._model_slot = 1 - next_slot
            self._cancel_model_hold()
            return
        self._model_hold_triggered = True
        self._set_active_model(model_name)

    def _finish_short_model_press(self):
        """Stop capture for a short activation press and let the worker transcribe it."""
        worker = getattr(self, 'result_thread', None)
        if not worker or not worker.isRunning():
            return
        self._continue_recording = False
        worker.stop_recording()

    def _set_active_model(self, model_name):
        """Set the model snapshot shown by the tray/status indicators and worker."""
        self.active_model_name = model_name
        if getattr(self, 'result_thread', None):
            self.result_thread.model_name = model_name
        if getattr(self, 'status_window', None):
            self.status_window.set_model(model_name)
        if getattr(self, 'tray_icon', None) and getattr(self, 'current_status', None):
            self.update_tray_icon(self.current_status)

    def retry_transcription(self):
        if not self.failed_recordings or self.result_thread is not None or self._shutdown_action or getattr(self, '_update_phase', None) == 'updating':
            return
        recording = self.failed_recordings[0]
        audio_data, sample_rate = recording[:2]
        model_name = recording[2] if len(recording) > 2 else getattr(self, 'active_model_name', None)
        self._retrying = True
        self._continue_recording = False
        self._start_worker(ResultThread(
            self.local_model,
            audio_data=audio_data,
            sample_rate=sample_rate,
            model_name=model_name,
        ))

    def copy_last_transcript(self):
        """
        Copy the last transcript to the clipboard. Only happens when the user explicitly
        clicks this tray menu item — the app never touches the clipboard on its own.
        """
        if not self.last_transcript:
            self.tray_icon.showMessage(
                'WhisperWriter', 'No transcript captured yet.',
                QSystemTrayIcon.Information, 3000
            )
            return
        self.app.clipboard().setText(self.last_transcript)
        self.tray_icon.showMessage(
            'WhisperWriter', 'Last transcript copied to clipboard.',
            QSystemTrayIcon.Information, 2000
        )

    def cleanup(self):
        self._cancel_model_hold()
        if getattr(self, 'escape_guard', None):
            self.escape_guard.close()
        if getattr(self, 'key_listener', None):
            self.key_listener.stop()
        if getattr(self, 'input_simulator', None):
            self.input_simulator.cleanup()

    def exit_app(self):
        self._request_shutdown('exit')

    def restart_app(self):
        self._request_shutdown('restart')

    def _request_shutdown(self, action):
        # Keep Qt and the QThread alive until the worker has released its resources.
        if getattr(self, 'escape_guard', None):
            self.escape_guard.set_active(False)
        self._shutdown_action = action
        self._cancel_model_hold()
        self._continue_recording = False
        if getattr(self, 'key_listener', None):
            self.key_listener.stop()
        if getattr(self, '_update_phase', None) == 'updating':
            return
        thread = getattr(self, 'result_thread', None)
        if thread and thread.isRunning():
            thread.stop()
            return
        self._finish_shutdown()

    def _finish_shutdown(self):
        self.cleanup()
        if self._shutdown_action == 'restart':
            # Replace this process so the old instance cannot retain its lock while
            # a second interpreter starts. Python file descriptors close on exec.
            os.execv(sys.executable, [sys.executable] + sys.argv)
        QApplication.quit()

    def apply_live_settings(self):
        """
        Re-apply settings after a save that only touched schema fields marked
        `live_reload: true` (see settings_window.py) — no restart needed. Most such settings
        are already read fresh via ConfigManager.get_config_value() at the point of use;
        toggle_sound_volume is the exception since it's cached on the AudioPlayer objects.
        """
        toggle_volume = ConfigManager.get_config_value('misc', 'toggle_sound_volume')
        self.recording_start_sound.volume = toggle_volume
        self.recording_stop_sound.volume = toggle_volume

    def on_settings_closed(self):
        """
        If settings is closed without saving on first run, initialize the components with default values.
        """
        if not ConfigManager.config_file_exists() and not getattr(self, 'key_listener', None):
            QMessageBox.information(
                self.settings_window,
                'Using Default Values',
                'Settings closed without saving. Default values are being used.'
            )
            self.initialize_components()

    def on_activation(self):
        """
        Called when the activation key combination is pressed.
        """
        if self._shutdown_action or getattr(self, '_update_phase', None) == 'updating':
            return
        if self.result_thread and self.result_thread.isRunning():
            if self._recording_worker_active(self.result_thread):
                _primary, secondary = self._configured_model_pair()
                if secondary:
                    self._begin_model_hold('stop')
                else:
                    self._finish_short_model_press()
            return

        self._continue_recording = ConfigManager.get_config_value('recording_options', 'recording_mode') == 'continuous'
        # Every user-started recording begins on the primary model.  The first keypress can
        # still select the secondary model by being held past the short/long threshold.
        self.start_result_thread(reset_selection=True)
        _primary, secondary = self._configured_model_pair()
        if secondary and getattr(self, 'result_thread', None) is not None and not self._shutdown_action:
            self._begin_model_hold('initial')

    def on_deactivation(self):
        """
        Called when the activation key combination is released.
        """
        if getattr(self, '_model_hold_pending', False):
            was_long_press = self._model_hold_triggered
            action = self._model_hold_action
            self._cancel_model_hold()
            if action == 'initial':
                # A tap on the first activation starts a normal press-to-toggle/continuous
                # recording.  In hold-to-record mode the key release always ends capture.
                if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'hold_to_record':
                    if self.result_thread and self.result_thread.isRunning():
                        self.result_thread.stop_recording()
                return
            if not was_long_press:
                self._finish_short_model_press()
            return
        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'hold_to_record':
            if self.result_thread and self.result_thread.isRunning():
                self.result_thread.stop_recording()

    def start_result_thread(self, model_name=None, reset_selection=True):
        """
        Start the result thread to record audio and transcribe it.
        """
        if self._shutdown_action or self.result_thread is not None or getattr(self, '_update_phase', None) == 'updating':
            return

        # Starting a new recording explicitly abandons the previous failed audio.
        self.failed_recordings.clear()
        self._retry_status = None
        if reset_selection:
            self._model_slot = 0
        if model_name is None:
            model_name = self._selected_model()
        self._set_active_model(model_name)
        self._start_worker(ResultThread(self.local_model, model_name=model_name))

    def _start_worker(self, worker):
        self.result_thread = worker
        self._set_active_model(worker.model_name or self.active_model_name)
        if getattr(self, 'status_window', None):
            self.status_window.set_model(self.active_model_name)
        self._update_retry_actions()
        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.result_thread.statusSignal.connect(self.status_window.updateStatus)
        self.result_thread.statusSignal.connect(self.update_tray_icon)
        self.result_thread.statusSignal.connect(self.on_status_changed)
        self.result_thread.resultSignal.connect(self.on_transcription_complete)
        self.result_thread.failedAudioSignal.connect(self.on_transcription_failed)
        self.result_thread.finished.connect(self.on_worker_finished)
        self.result_thread.start()

    def stop_result_thread(self):
        """
        Stop the result thread.
        """
        self._continue_recording = False
        self._cancel_model_hold()
        if self.result_thread and self.result_thread.isRunning():
            self.result_thread.stop()

    def on_status_changed(self, status):
        """
        Track the current recording/transcription status (used to gate the cancel hotkey),
        play the recording start/stop toggle sounds, and re-arm listening after a cancelled
        recording (which, unlike a normal completed recording, never reaches
        on_transcription_complete since nothing was transcribed).
        """
        previous_status = self.current_status
        self.current_status = status
        if status != 'recording' and getattr(self, '_model_hold_pending', False):
            self._cancel_model_hold()
        if getattr(self, 'escape_guard', None):
            self.escape_guard.set_active(status == 'recording' and not self._shutdown_action)

        if ConfigManager.get_config_value('misc', 'play_toggle_sounds'):
            if status == 'recording' and previous_status != 'recording':
                self.recording_start_sound.play(block=False)
            elif previous_status == 'recording' and status != 'recording':
                self.recording_stop_sound.play(block=False)

        if status == 'error':
            self._continue_recording = False
            message = ('Transcription failed. Use Retry Transcription in the tray menu. Audio is retained until the next recording or exit/restart.'
                       if self.result_thread and self.result_thread.transcription_failed else 'Recording failed. See the application log; please try recording again.')
            self.tray_icon.showMessage('WhisperWriter', message, QSystemTrayIcon.Warning, 5000)

    def on_worker_finished(self):
        self._cancel_model_hold()
        thread = self.result_thread
        # A queued failedAudioSignal normally arrives before finished, but make the
        # retention guarantee independent of Qt's cross-thread delivery order.
        retained_recording = getattr(thread, 'retained_recording', None) if thread else None
        if thread and getattr(thread, 'is_cancelled', False) is True:
            self._retry_status = 'cancelled'
        elif thread and getattr(thread, 'transcription_failed', False) is True:
            self._retry_status = 'error'
        elif thread and getattr(thread, 'transcription_succeeded', False) is True:
            self._retry_status = None
        if (
            thread
            and not self._retrying
            and getattr(thread, 'is_cancelled', False) is True
            and not self.failed_recordings
            and isinstance(retained_recording, tuple)
            and len(retained_recording) >= 2
        ):
            self.failed_recordings[:] = [retained_recording]
            self._update_retry_actions()
        if self._retrying and thread and thread.transcription_succeeded:
            self.failed_recordings.pop(0)
        self._retrying = False
        self.result_thread = None
        self._update_retry_actions()
        if thread:
            thread.deleteLater()
        if self._shutdown_action:
            self._finish_shutdown()
        elif self._continue_recording:
            self.start_result_thread(
                model_name=getattr(self, 'active_model_name', None),
                reset_selection=False,
            )
        else:
            self.on_status_changed('idle')
            self.update_tray_icon('idle')
            if getattr(self, 'status_window', None):
                self.status_window.updateStatus('idle')
            if getattr(self, '_sync_pending_action', None) is not None:
                QTimer.singleShot(0, self._run_queued_sync)

    def on_cancel_key(self):
        """
        Called on every ESC press; only actually cancels if a recording is currently
        in progress (not while transcribing, not while idle).
        """
        if getattr(self, 'settings_window', None) and self.settings_window.isActiveWindow():
            self.settings_window.discard_and_close()
            return
        if self._recording_worker_active():
            # A cancelled continuous recording must not immediately start a new segment;
            # the retained audio is offered through the retry controls instead.
            self._continue_recording = False
            self._cancel_model_hold()
            self.result_thread.cancel_recording()

    def on_transcription_complete(self, result):
        """
        When the transcription is complete, type the result and start listening for the activation key again.
        """
        if self._shutdown_action or not result:
            return
        self.last_transcript = result
        self.copy_last_transcript_action.setEnabled(bool(result))

        # Stop listening while typing: pynput's global listener also observes the
        # synthetic keystrokes typewrite() injects (well-documented pynput behavior —
        # an XTest-injected event is indistinguishable from a real one to the XRecord
        # hook the listener uses). Left running, a dropped/reordered synthetic
        # press-or-release — more likely the longer and more punctuated the transcript,
        # so it can take a while to hit — can leave a stray key marked "pressed" in
        # KeyChord.pressed_keys forever, permanently failing the chord's exact-match
        # check (see fix #2 in FORK_NOTES.md) until the app is restarted, with the
        # activation hotkey silently never firing again.
        self.key_listener.stop()
        try:
            self.input_simulator.typewrite(result)
        except Exception:
            logger.exception('Unable to type transcript')
            self.tray_icon.showMessage('WhisperWriter', 'Typing failed. Use Copy Last Transcript to recover the text.', QSystemTrayIcon.Warning, 5000)
        finally:
            self.key_listener.start()

        if ConfigManager.get_config_value('misc', 'noise_on_completion'):
            AudioPlayer(os.path.join('assets', 'beep.wav')).play(block=True)

    def run(self):
        """
        Start the application.
        """
        sys.exit(self.app.exec_())


if __name__ == '__main__':
    import argparse
    from logging_setup import configure_logging
    from single_instance import SingleInstanceLock

    parser = argparse.ArgumentParser(description='WhisperWriter')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging to ~/.cache/whisper-writer/debug.log')
    args = parser.parse_args()

    configure_logging(args.debug)

    # Held for the lifetime of the process (module-level name, never reassigned) — the OS
    # releases it automatically on exit, so no explicit release/cleanup path is needed.
    instance_lock = SingleInstanceLock()
    if not instance_lock.acquire():
        print('WhisperWriter is already running — exiting.')
        notice_app = QApplication(sys.argv)
        app_icon = QIcon(os.path.join('assets', 'ww-logo.png'))
        notice_app.setWindowIcon(app_icon)
        already_running_box = QMessageBox()
        already_running_box.setWindowTitle('WhisperWriter')
        already_running_box.setWindowIcon(app_icon)
        already_running_box.setIconPixmap(app_icon.pixmap(48, 48))
        already_running_box.setText('WhisperWriter is already running.')
        already_running_box.exec_()
        sys.exit(1)

    app = WhisperWriterApp()
    app.run()
