"""Regression checks without microphone, network, or synthetic keyboard input."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['PYNPUT_BACKEND'] = 'dummy'
import sys
sys.path.insert(0, os.path.abspath('src'))
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from PyQt5.QtCore import QObject, QThread, Qt
from PyQt5.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QTextEdit, QWidget
from utils import ConfigManager
from result_thread import ResultThread
from main import WhisperWriterApp
from input_simulation import InputSimulator
import transcription
from ui.settings_window import SettingsWindow

APP = QApplication.instance() or QApplication([])


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        ConfigManager._instance = ConfigManager()
        cfg = ConfigManager._instance
        cfg.schema = cfg.load_config_schema()
        cfg.config = cfg.load_default_config()
        ConfigManager.set_config_value(False, 'misc', 'print_to_terminal')
        ConfigManager.set_config_value(False, 'misc', 'play_toggle_sounds')
        ConfigManager.set_config_value('press_to_toggle', 'recording_options', 'recording_mode')

    def app(self):
        app = WhisperWriterApp.__new__(WhisperWriterApp)
        QObject.__init__(app)
        app.failed_recordings = []
        app._retrying = False
        app.retry_transcript_action = Mock()
        app.update_action = Mock()
        app._shutdown_action = None
        app._continue_recording = False
        app._update_process = None
        app.local_model = None
        app.result_thread = None
        app.current_status = 'idle'
        app.key_listener = Mock()
        app.input_simulator = Mock()
        app.tray_icon = Mock()
        app.tray_icon_idle = Mock()
        app.tray_icon_recording = Mock()
        app.tray_icon_transcribing = Mock()
        app.tray_icon_error = Mock()
        app.tray_icon_updating = Mock()
        app.copy_last_transcript_action = Mock()
        app.settings_window = Mock()
        app.settings_window.isVisible.return_value = False
        return app

    def test_update_check_current_shows_information(self):
        app = self.app()
        process = Mock()
        process.readAllStandardOutput.return_value = b'NO_UPDATE\n'
        app._update_process = process
        with patch.object(app, '_show_update_message') as show:
            app._on_update_check_finished(0, None)
        show.assert_called_once_with('WhisperWriter', 'No update available. This installation is current.')
        app.tray_icon.showMessage.assert_not_called()
        app.tray_icon.setIcon.assert_called_with(app.tray_icon_idle)
        process.deleteLater.assert_called_once()
        app.update_action.setEnabled.assert_called_with(True)

    def test_update_check_available_starts_detached_updater(self):
        app = self.app()
        process = Mock()
        process.readAllStandardOutput.return_value = b'UPDATE_AVAILABLE\n'
        app._update_process = process
        with patch.object(app, '_start_update') as start:
            app._on_update_check_finished(10, None)
        start.assert_called_once()

    def test_update_check_error_shows_warning(self):
        app = self.app()
        process = Mock()
        process.readAllStandardOutput.return_value = b'fetch failed\n'
        app._update_process = process
        with patch.object(app, '_show_update_message') as show:
            app._on_update_check_finished(1, None)
        self.assertEqual(show.call_args.args[0], 'Update check failed')

    def test_update_is_blocked_while_worker_is_active(self):
        app = self.app()
        app.result_thread = Mock()
        with patch.object(app, '_show_update_message') as show:
            app.check_for_updates()
        show.assert_called_once()
        app.tray_icon.showMessage.assert_not_called()
        app.update_action.setEnabled.assert_not_called()

    def test_update_is_blocked_while_failed_audio_is_recoverable(self):
        app = self.app()
        app.failed_recordings = [('audio', 16000)]
        with patch.object(app, '_show_update_message') as show:
            app.check_for_updates()
        show.assert_called_once()
        self.assertIn('Retry', show.call_args.args[1])
        app.tray_icon.showMessage.assert_not_called()
        app.update_action.setEnabled.assert_not_called()

    def test_update_check_and_apply_change_tray_icon_without_notifications(self):
        app = self.app()
        app._set_update_indicator('checking')
        app.tray_icon.setIcon.assert_called_with(app.tray_icon_updating)
        app.tray_icon.setToolTip.assert_called_with('WhisperWriter — checking...')
        app.tray_icon.reset_mock()
        with patch('main.QProcess.startDetached', return_value=(True, 123)):
            app._start_update()
        app.tray_icon.setIcon.assert_called_with(app.tray_icon_updating)
        app.tray_icon.showMessage.assert_not_called()
        app.tray_icon.setToolTip.assert_called_with('WhisperWriter — updating...')

    def test_detached_update_failure_reenables_action(self):
        app = self.app()
        with patch('main.QProcess.startDetached', return_value=(False, None)), patch.object(app, '_show_update_message') as show:
            app._start_update()
        app.update_action.setEnabled.assert_called_with(True)
        self.assertEqual(show.call_args.args[0], 'Update failed')

    def test_early_release_is_not_overwritten(self):
        worker = ResultThread()
        worker.stop_recording()
        with patch.object(worker, '_record_audio', side_effect=lambda: self.assertFalse(worker.is_recording)):
            worker.run()

    def test_stop_does_not_wait_for_transcription(self):
        entered, release = threading.Event(), threading.Event()
        worker = ResultThread()
        with patch.object(worker, '_record_audio', return_value=np.ones(1600, dtype=np.int16)), patch('result_thread.transcribe', side_effect=lambda *a, **kw: (entered.set(), release.wait(3), 'text')[-1]):
            worker.start()
            self.assertTrue(entered.wait(2))
            before = time.monotonic()
            worker.stop()
            self.assertLess(time.monotonic() - before, 0.2)
            release.set()
            self.assertTrue(worker.wait(2000))

    def test_cancel_never_transcribes(self):
        worker = ResultThread()
        worker.cancel_recording()
        with patch.object(worker, '_record_audio', return_value=np.ones(1600)), patch('result_thread.transcribe') as transcribe:
            worker.run()
            transcribe.assert_not_called()

    def test_missing_audio_callback_can_be_stopped(self):
        worker = ResultThread()
        audio = Mock()
        audio.open.return_value.is_active.return_value = True
        with patch('result_thread.pyaudio.PyAudio', return_value=audio):
            worker.start()
            time.sleep(0.15)
            worker.stop()
            self.assertTrue(worker.wait(1000))
        audio.open.return_value.close.assert_called_once()
        audio.terminate.assert_called_once()

    def test_callback_burst_preserves_all_frames(self):
        worker = ResultThread()
        audio = Mock()
        blocks = [np.full(480, i, dtype=np.int16).tobytes() for i in range(8)]
        def open_audio(**kwargs):
            for block in blocks:
                kwargs['stream_callback'](block, 480, None, 0)
            return audio.open.return_value
        audio.open.side_effect = open_audio
        def stop_on_empty():
            worker.stop_recording()
            return True
        audio.open.return_value.is_active.side_effect = stop_on_empty
        with patch('result_thread.pyaudio.PyAudio', return_value=audio):
            result = worker._record_audio()
        self.assertEqual(result.tobytes(), b''.join(blocks))

    def test_overflow_fails_instead_of_silently_transcribing(self):
        worker = ResultThread()
        audio = Mock()
        def open_audio(**kwargs):
            for _ in range(101):
                kwargs['stream_callback'](b'\x00' * 960, 480, None, 0)
            return audio.open.return_value
        audio.open.side_effect = open_audio
        with patch('result_thread.pyaudio.PyAudio', return_value=audio), self.assertRaisesRegex(RuntimeError, 'overflow'):
            worker._record_audio()
        audio.open.return_value.close.assert_called_once()

    def test_typing_failure_restores_listener_and_keeps_text(self):
        app = self.app()
        app.input_simulator.typewrite.side_effect = RuntimeError('failure')
        with self.assertLogs('main', level='ERROR'):
            app.on_transcription_complete('recover me')
        app.key_listener.start.assert_called_once()
        self.assertEqual(app.last_transcript, 'recover me')

    def test_empty_result_keeps_previous_transcript(self):
        app = self.app()
        app.last_transcript = 'previous'
        app.on_transcription_complete('')
        self.assertEqual(app.last_transcript, 'previous')
        app.input_simulator.typewrite.assert_not_called()

    def test_continuous_restarts_only_on_finished(self):
        app = self.app()
        app._continue_recording = True
        app.result_thread = Mock()
        with patch.object(app, 'start_result_thread') as start:
            app.on_status_changed('cancel')
            start.assert_not_called()
            app.on_worker_finished()
            start.assert_called_once()
        self.assertIsNone(app.result_thread)

    def test_shutdown_waits_without_destroying_worker(self):
        app = self.app()
        app.result_thread = Mock()
        app.result_thread.isRunning.return_value = True
        with patch.object(app, '_finish_shutdown') as finish:
            app.exit_app()
            finish.assert_not_called()
            app.result_thread.stop.assert_called_once()
            app.on_worker_finished()
            finish.assert_called_once()

    def test_hotkey_signal_is_delivered_on_gui_thread(self):
        app = self.app()
        called = []
        app.activationRequested.connect(lambda: called.append(QThread.currentThread()), Qt.QueuedConnection)
        t = threading.Thread(target=app.activationRequested.emit)
        t.start(); t.join()
        self.assertEqual(called, [])
        APP.processEvents()
        self.assertEqual(called, [APP.thread()])

    def test_malformed_config_sections_do_not_replace_defaults(self):
        for contents in ('', '[]', 'misc: null', 'model_options:\n  api: []'):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'config.yaml'
                path.write_text(contents)
                ConfigManager._instance.load_user_config(path)
                self.assertIsInstance(ConfigManager.get_config_section('misc'), dict)
                self.assertIsInstance(ConfigManager.get_config_section('model_options', 'api'), dict)

    def test_settings_model_controls_and_prompt_editor(self):
        settings = SettingsWindow()
        try:
            settings.show()
            APP.processEvents()
            base_url = settings.findChild(QLineEdit, 'model_options_api_base_url_input')
            model_container = settings.findChild(QWidget, 'model_options_api_model_input')
            model_combo = settings.findChild(QComboBox, 'model_options_api_model_selector')
            prompt = settings.findChild(QTextEdit, 'model_options_common_initial_prompt_input')
            prompt_link = settings.findChild(QLabel, 'model_options_common_initial_prompt_link')
            self.assertIsNotNone(base_url)
            self.assertIsNotNone(model_container)
            self.assertIsNotNone(model_combo)
            self.assertIsNotNone(prompt)
            self.assertIsNotNone(prompt_link)
            self.assertTrue(model_combo.isEditable())
            self.assertEqual(settings.get_widget_value_typed(model_container, 'str'), 'whisper-1')
            self.assertIsNone(settings.get_widget_value_typed(prompt, 'str'))
            self.assertTrue(prompt_link.openExternalLinks())
            self.assertGreater(prompt.geometry().top(), settings.height() // 3)
            api_order = list(ConfigManager.get_schema()['model_options']['api'])
            self.assertLess(api_order.index('base_url'), api_order.index('model'))
        finally:
            settings.reset_settings()
            settings.close()

    def test_model_discovery_extracts_openai_and_local_shapes(self):
        self.assertEqual(
            SettingsWindow._extract_model_ids({'data': [{'id': 'one'}, {'id': 'two'}, {'id': 'one'}]}),
            ['one', 'two'],
        )
        self.assertEqual(
            SettingsWindow._extract_model_ids({'models': [{'name': 'local-one'}, 'local-two']}),
            ['local-one', 'local-two'],
        )
        self.assertEqual(SettingsWindow._models_url('http://localhost:1234/v1/').toString(), 'http://localhost:1234/v1/models')

    def test_settings_refreshes_models_from_configured_endpoint(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                body = b'{"data": [{"id": "remote-one"}, {"id": "remote-two"}]}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        settings = SettingsWindow()
        try:
            settings.use_api_checkbox.setChecked(True)
            settings.api_base_url_input.setText(f'http://127.0.0.1:{server.server_port}/v1')
            settings.refresh_api_models()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                APP.processEvents()
                if settings.api_model_combo.findText('remote-one') >= 0:
                    break
                time.sleep(.01)
            self.assertEqual(calls, ['/v1/models'])
            self.assertGreaterEqual(settings.api_model_combo.findText('remote-one'), 0)
            self.assertGreaterEqual(settings.api_model_combo.findText('remote-two'), 0)
            self.assertEqual(settings.api_model_status.text(), '2 models')
        finally:
            settings.reset_settings()
            settings.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_atomic_config_failure_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.yaml'
            path.write_text('old contents')
            with patch('utils.os.replace', side_effect=OSError('disk error')), self.assertRaises(OSError):
                ConfigManager.save_config(path)
            self.assertEqual(path.read_text(), 'old contents')
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)

    def test_failed_transcription_retains_audio_and_original_rate(self):
        data = np.ones(1600, dtype=np.int16)
        worker = ResultThread(audio_data=data, sample_rate=8000)
        failures = []
        worker.failedAudioSignal.connect(failures.append)
        with patch('result_thread.transcribe', side_effect=RuntimeError('network down')), patch('result_thread.traceback.print_exc'), patch.object(worker, '_record_audio') as record:
            worker.run()
        record.assert_not_called()
        self.assertEqual(len(failures), 1)
        self.assertIs(failures[0][0], data)
        self.assertEqual(failures[0][1], 8000)
        self.assertFalse(worker.transcription_succeeded)

    def test_retry_uses_saved_audio_without_microphone(self):
        app = self.app()
        data = np.ones(1600, dtype=np.int16)
        app.failed_recordings = [(data, 8000)]
        with patch.object(app, '_start_worker') as start:
            app.retry_transcription()
        worker = start.call_args.args[0]
        self.assertIs(worker.audio_data, data)
        self.assertEqual(worker.sample_rate, 8000)
        with patch.object(worker, '_record_audio') as record, patch('result_thread.transcribe', return_value='recovered') as transcribe:
            worker.run()
        record.assert_not_called()
        self.assertEqual(transcribe.call_args.kwargs['sample_rate'], 8000)
        self.assertTrue(worker.transcription_succeeded)

    def test_failed_retry_does_not_duplicate_recording(self):
        app = self.app()
        data = (np.ones(1600), 16000)
        app.failed_recordings = [data]
        app._retrying = True
        app.on_transcription_failed(data)
        self.assertEqual(len(app.failed_recordings), 1)

    def test_successful_retry_clears_failed_recording(self):
        app = self.app()
        app.failed_recordings = [(np.ones(1600), 16000)]
        app._retrying = True
        app.result_thread = Mock(transcription_succeeded=True)
        with patch.object(app, 'update_tray_icon'):
            app.on_worker_finished()
        self.assertEqual(app.failed_recordings, [])
        app.retry_transcript_action.setEnabled.assert_called_with(False)

    def test_error_icon_survives_worker_idle(self):
        app = self.app()
        app.tray_icon_error = object()
        app.failed_recordings = [(np.ones(1600), 16000)]
        app.update_tray_icon('idle')
        app.tray_icon.setIcon.assert_called_with(app.tray_icon_error)
        self.assertIn('retry available', app.tray_icon.setToolTip.call_args.args[0])

    def test_next_recording_discards_previous_failed_audio(self):
        app = self.app()
        app.failed_recordings = [(np.ones(1600), 16000)]
        with patch.object(app, '_start_worker') as start:
            app.start_result_thread()
        start.assert_called_once()
        self.assertEqual(app.failed_recordings, [])

    def test_ignored_start_does_not_discard_retry_audio(self):
        app = self.app()
        app.failed_recordings = [(np.ones(1600), 16000)]
        app.result_thread = Mock()
        with patch.object(app, '_start_worker') as start:
            app.start_result_thread()
        start.assert_not_called()
        self.assertEqual(len(app.failed_recordings), 1)

    def test_escape_in_active_settings_does_not_cancel_recording(self):
        app = self.app()
        app.settings_window = Mock()
        app.settings_window.isActiveWindow.return_value = True
        app.result_thread = Mock()
        app.current_status = 'recording'
        app.on_cancel_key()
        app.settings_window.discard_and_close.assert_called_once()
        app.result_thread.cancel_recording.assert_not_called()

    def test_dotool_rejects_command_injection(self):
        simulator = InputSimulator.__new__(InputSimulator)
        simulator.dotool_process = Mock()
        with self.assertRaises(ValueError):
            simulator._typewrite_dotool('hello\nkey ctrl+q', 0)
        simulator.dotool_process.stdin.write.assert_not_called()

    def test_local_non_16khz_is_rejected_before_recording(self):
        ConfigManager.set_config_value(48000, 'recording_options', 'sample_rate')
        with patch('result_thread.pyaudio.PyAudio') as audio, self.assertRaisesRegex(ValueError, '16000'):
            ResultThread()._record_audio()
        audio.assert_not_called()

    def test_real_sdk_http_request_with_updated_dependencies(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(self.rfile.read(int(self.headers['Content-Length'])))
                body = b'{"text": "tested"}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            ConfigManager.set_config_value(f'http://127.0.0.1:{server.server_port}/v1', 'model_options', 'api', 'base_url')
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}):
                self.assertEqual(transcription.transcribe_api(np.zeros(1600, dtype=np.int16)), 'tested')
            self.assertEqual(len(calls), 1)
            self.assertIn(b'audio.wav', calls[0])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_real_http_failure_can_retry_identical_audio(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(self.rfile.read(int(self.headers['Content-Length'])))
                body = b'{"error": {"message": "offline", "type": "server_error"}}' if len(calls) == 1 else b'{"text": "recovered"}'
                self.send_response(503 if len(calls) == 1 else 200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            ConfigManager.set_config_value(True, 'model_options', 'use_api')
            ConfigManager.set_config_value(f'http://127.0.0.1:{server.server_port}/v1', 'model_options', 'api', 'base_url')
            data = np.arange(1600, dtype=np.int16)
            first = ResultThread(audio_data=data, sample_rate=8000)
            failures, results = [], []
            first.failedAudioSignal.connect(failures.append)
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}), patch('result_thread.traceback.print_exc'):
                first.run()
                self.assertEqual(len(calls), 1)  # No implicit SDK retries.
                second = ResultThread(audio_data=failures[0][0], sample_rate=failures[0][1])
                second.resultSignal.connect(results.append)
                second.run()
            self.assertTrue(second.transcription_succeeded)
            self.assertEqual(results, ['recovered '])
            self.assertEqual(len(calls), 2)
            # Compare WAV data, ignoring randomized multipart boundaries.
            wavs = [body[body.index(b'RIFF'):].split(b'\r\n--')[0] for body in calls]
            self.assertEqual(wavs[0], wavs[1])
            import io, wave
            with wave.open(io.BytesIO(wavs[1])) as audio:
                self.assertEqual(audio.getframerate(), 8000)
                self.assertEqual(audio.readframes(1600), data.tobytes())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_api_closes_client_and_uses_configured_timeout(self):
        ConfigManager.set_config_value('http://localhost:1234/v1', 'model_options', 'api', 'base_url')
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}), patch('transcription.OpenAI') as client:
            client.return_value.__enter__.return_value.audio.transcriptions.create.return_value.text = 'ok'
            self.assertEqual(transcription.transcribe_api(np.zeros(1600, dtype=np.int16)), 'ok')
            self.assertEqual(client.call_args.kwargs['api_key'], 'not-needed')
            self.assertEqual(client.call_args.kwargs['max_retries'], 0)
            self.assertEqual(client.call_args.kwargs['timeout'], 120)
            client.return_value.__exit__.assert_called_once()

if __name__ == '__main__':
    unittest.main()
