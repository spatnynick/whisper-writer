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
from PyQt5.QtWidgets import QApplication
from utils import ConfigManager
from result_thread import ResultThread
from main import WhisperWriterApp
from input_simulation import InputSimulator
import transcription

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
        app._shutdown_action = None
        app._continue_recording = False
        app.result_thread = None
        app.current_status = 'idle'
        app.key_listener = Mock()
        app.input_simulator = Mock()
        app.tray_icon = Mock()
        app.copy_last_transcript_action = Mock()
        return app

    def test_early_release_is_not_overwritten(self):
        worker = ResultThread()
        worker.stop_recording()
        with patch.object(worker, '_record_audio', side_effect=lambda: self.assertFalse(worker.is_recording)):
            worker.run()

    def test_stop_does_not_wait_for_transcription(self):
        entered, release = threading.Event(), threading.Event()
        worker = ResultThread()
        with patch.object(worker, '_record_audio', return_value=np.ones(1600, dtype=np.int16)), patch('result_thread.transcribe', side_effect=lambda *a: (entered.set(), release.wait(3), 'text')[-1]):
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

    def test_atomic_config_failure_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.yaml'
            path.write_text('old contents')
            with patch('utils.os.replace', side_effect=OSError('disk error')), self.assertRaises(OSError):
                ConfigManager.save_config(path)
            self.assertEqual(path.read_text(), 'old contents')
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)

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
