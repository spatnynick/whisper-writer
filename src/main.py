import os
import sys
import time
from audioplayer import AudioPlayer
from pynput.keyboard import Controller
from PyQt5.QtCore import QObject, QProcess
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QMessageBox

from key_listener import KeyListener
from result_thread import ResultThread
from ui.main_window import MainWindow
from ui.settings_window import SettingsWindow
from ui.status_window import StatusWindow
from transcription import create_local_model
from input_simulation import InputSimulator
from utils import ConfigManager


class WhisperWriterApp(QObject):
    def __init__(self):
        """
        Initialize the application, opening settings window if no configuration file is found.
        """
        super().__init__()
        self.app = QApplication(sys.argv)
        self.app.setWindowIcon(QIcon(os.path.join('assets', 'ww-logo.png')))

        ConfigManager.initialize()

        self.settings_window = SettingsWindow()
        self.settings_window.settings_closed.connect(self.on_settings_closed)
        self.settings_window.settings_saved.connect(self.restart_app)

        if ConfigManager.config_file_exists():
            self.initialize_components()
        else:
            print('No valid configuration file found. Opening settings window...')
            self.settings_window.show()

    def initialize_components(self):
        """
        Initialize the components of the application.
        """
        self.input_simulator = InputSimulator()

        self.key_listener = KeyListener()
        self.key_listener.add_callback("on_activate", self.on_activation)
        self.key_listener.add_callback("on_deactivate", self.on_deactivation)
        self.key_listener.add_callback("on_cancel_key", self.on_cancel_key)

        model_options = ConfigManager.get_config_section('model_options')
        model_path = model_options.get('local', {}).get('model_path')
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

        self.main_window = MainWindow()
        self.main_window.openSettings.connect(self.settings_window.show)
        self.main_window.startListening.connect(self.key_listener.start)
        self.main_window.closeApp.connect(self.exit_app)

        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.status_window = StatusWindow()

        self.create_tray_icon()
        self.key_listener.start()

    def create_tray_icon(self):
        """
        Create the system tray icon and its context menu.
        """
        self.tray_icon_idle = QIcon(os.path.join('assets', 'ww-logo.png'))
        self.tray_icon_recording = QIcon(os.path.join('assets', 'ww-logo-recording.png'))
        self.tray_icon_transcribing = QIcon(os.path.join('assets', 'ww-logo-transcribing.png'))

        self.tray_icon = QSystemTrayIcon(self.tray_icon_idle, self.app)
        self.tray_icon.setToolTip('WhisperWriter — Idle')

        tray_menu = QMenu()

        show_action = QAction('WhisperWriter Main Menu', self.app)
        show_action.triggered.connect(self.main_window.show)
        tray_menu.addAction(show_action)

        settings_action = QAction('Open Settings', self.app)
        settings_action.triggered.connect(self.settings_window.show)
        tray_menu.addAction(settings_action)

        tray_menu.addSeparator()

        self.copy_last_transcript_action = QAction('Copy Last Transcript', self.app)
        self.copy_last_transcript_action.setEnabled(False)
        self.copy_last_transcript_action.triggered.connect(self.copy_last_transcript)
        tray_menu.addAction(self.copy_last_transcript_action)

        tray_menu.addSeparator()

        exit_action = QAction('Exit', self.app)
        exit_action.triggered.connect(self.exit_app)
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def update_tray_icon(self, status):
        """
        Update the system tray icon to reflect the current recording/transcribing status,
        if enabled via the misc.show_tray_status_icon setting.
        """
        if not ConfigManager.get_config_value('misc', 'show_tray_status_icon'):
            return

        if status == 'recording':
            self.tray_icon.setIcon(self.tray_icon_recording)
            self.tray_icon.setToolTip('WhisperWriter — Recording...')
        elif status == 'transcribing':
            self.tray_icon.setIcon(self.tray_icon_transcribing)
            self.tray_icon.setToolTip('WhisperWriter — Transcribing...')
        elif status in ('idle', 'error', 'cancel'):
            self.tray_icon.setIcon(self.tray_icon_idle)
            self.tray_icon.setToolTip('WhisperWriter — Idle')

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
        if self.key_listener:
            self.key_listener.stop()
        if self.input_simulator:
            self.input_simulator.cleanup()

    def exit_app(self):
        """
        Exit the application.
        """
        self.cleanup()
        QApplication.quit()

    def restart_app(self):
        """Restart the application to apply the new settings."""
        self.cleanup()
        QApplication.quit()
        QProcess.startDetached(sys.executable, sys.argv)

    def on_settings_closed(self):
        """
        If settings is closed without saving on first run, initialize the components with default values.
        """
        if not os.path.exists(os.path.join('src', 'config.yaml')):
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
        if self.result_thread and self.result_thread.isRunning():
            recording_mode = ConfigManager.get_config_value('recording_options', 'recording_mode')
            if recording_mode == 'press_to_toggle':
                self.result_thread.stop_recording()
            elif recording_mode == 'continuous':
                self.stop_result_thread()
            return

        self.start_result_thread()

    def on_deactivation(self):
        """
        Called when the activation key combination is released.
        """
        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'hold_to_record':
            if self.result_thread and self.result_thread.isRunning():
                self.result_thread.stop_recording()

    def start_result_thread(self):
        """
        Start the result thread to record audio and transcribe it.
        """
        if self.result_thread and self.result_thread.isRunning():
            return

        self.result_thread = ResultThread(self.local_model)
        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.result_thread.statusSignal.connect(self.status_window.updateStatus)
            self.status_window.closeSignal.connect(self.stop_result_thread)
        self.result_thread.statusSignal.connect(self.update_tray_icon)
        self.result_thread.statusSignal.connect(self.on_status_changed)
        self.result_thread.resultSignal.connect(self.on_transcription_complete)
        self.result_thread.start()

    def stop_result_thread(self):
        """
        Stop the result thread.
        """
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

        if ConfigManager.get_config_value('misc', 'play_toggle_sounds'):
            if status == 'recording' and previous_status != 'recording':
                self.recording_start_sound.play(block=False)
            elif previous_status == 'recording' and status != 'recording':
                self.recording_stop_sound.play(block=False)

        if status == 'cancel':
            if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'continuous':
                self.start_result_thread()
            else:
                self.key_listener.start()

    def on_cancel_key(self):
        """
        Called on every ESC press; only actually cancels if a recording is currently
        in progress (not while transcribing, not while idle).
        """
        if self.result_thread and self.result_thread.isRunning() and self.current_status == 'recording':
            self.result_thread.cancel_recording()

    def on_transcription_complete(self, result):
        """
        When the transcription is complete, type the result and start listening for the activation key again.
        """
        self.last_transcript = result
        self.copy_last_transcript_action.setEnabled(bool(result))

        self.input_simulator.typewrite(result)

        if ConfigManager.get_config_value('misc', 'noise_on_completion'):
            AudioPlayer(os.path.join('assets', 'beep.wav')).play(block=True)

        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'continuous':
            self.start_result_thread()
        else:
            self.key_listener.start()

    def run(self):
        """
        Start the application.
        """
        sys.exit(self.app.exec_())


if __name__ == '__main__':
    import argparse
    from logging_setup import configure_logging

    parser = argparse.ArgumentParser(description='WhisperWriter')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging to ~/.cache/whisper-writer/debug.log')
    args = parser.parse_args()

    configure_logging(args.debug)

    app = WhisperWriterApp()
    app.run()
