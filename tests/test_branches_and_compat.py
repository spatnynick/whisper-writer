"""Branch selection in Settings > About, update edge cases and dependency-compatibility fixes."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['PYNPUT_BACKEND'] = 'dummy'
import sys
sys.path.insert(0, os.path.abspath('src'))
sys.path.insert(0, os.path.abspath('tests'))
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt5.QtCore import QProcess
from PyQt5.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

import model_discovery
import test_reliability  # shared app/config fixtures (its test class is not re-run here)
from ui.settings_window import SettingsWindow

APP = QApplication.instance() or QApplication([])


class _Fixture(unittest.TestCase):
    def setUp(self):
        test_reliability.ReliabilityTests.setUp(self)

    def app(self):
        return test_reliability.ReliabilityTests.app(self)


class AboutTabBranchTests(_Fixture):
    def settings(self, current='main', known=('main', 'feature/a')):
        with patch.object(SettingsWindow, 'git_info', lambda _self, args: {
            'rev-parse --abbrev-ref HEAD': current,
            'for-each-ref --format=%(refname:strip=3) refs/remotes/origin': '\n'.join(('HEAD',) + tuple(known)),
        }.get(' '.join(args))):
            window = SettingsWindow()
        self.addCleanup(lambda: (window.reset_settings(), window.close()))
        return window

    def branches(self, window):
        combo = window.app_branch_combo
        return [combo.itemData(index) for index in range(combo.count())]

    def test_lists_known_branches_main_first_and_selects_installed(self):
        window = self.settings(current='feature/a', known=('zeta', 'feature/a', 'main'))
        self.assertEqual(self.branches(window), ['main', 'feature/a', 'zeta'])
        self.assertEqual(window.app_branch_combo.currentData(), 'feature/a')
        self.assertIn('(installed)', window.app_branch_combo.currentText())
        self.assertFalse(window.app_branch_switch_button.isEnabled())
        window.app_branch_combo.setCurrentIndex(0)
        self.assertTrue(window.app_branch_switch_button.isEnabled())

    def test_switch_asks_first_and_emits_branch(self):
        window = self.settings()
        requested = []
        window.app_branch_switch_requested.connect(requested.append)
        window.app_branch_combo.setCurrentIndex(window.app_branch_combo.findData('feature/a'))
        with patch('ui.settings_window.QMessageBox.question', return_value=QMessageBox.No):
            window.app_branch_switch_button.click()
        self.assertEqual(requested, [])
        with patch('ui.settings_window.QMessageBox.question', return_value=QMessageBox.Yes) as question:
            window.app_branch_switch_button.click()
        self.assertEqual(requested, ['feature/a'])
        self.assertIn("'feature/a'", question.call_args.args[2])

    def test_refresh_uses_update_script_without_prompts_and_fills_list(self):
        window = self.settings()
        with patch('ui.settings_window.QProcess') as process_class:
            window.refresh_app_branches()
        process = process_class.return_value
        script, arguments = process.start.call_args.args
        self.assertEqual((os.path.basename(script), arguments), ('update.sh', ['--list-branches']))
        environment = process.setProcessEnvironment.call_args.args[0]
        self.assertEqual(environment.value('GIT_TERMINAL_PROMPT'), '0')
        self.assertFalse(window.app_branch_refresh_button.isEnabled())

        process.readAllStandardOutput.return_value = b'main\nnew-feature\n'
        window._on_app_branches_listed(0, QProcess.NormalExit)
        self.assertEqual(self.branches(window), ['main', 'new-feature'])
        self.assertEqual(window.app_branch_status.text(), '2 branches on GitHub.')
        self.assertTrue(window.app_branch_refresh_button.isEnabled())

    def test_failed_refresh_keeps_list_and_reports(self):
        window = self.settings()
        with patch('ui.settings_window.QProcess'):
            window.refresh_app_branches()
        window.app_branch_process.readAllStandardOutput.return_value = b''
        window._on_app_branches_listed(128, QProcess.NormalExit)
        self.assertEqual(self.branches(window), ['main', 'feature/a'])
        self.assertIn('Could not load branches', window.app_branch_status.text())

    def test_detached_checkout_can_select_a_branch(self):
        window = self.settings(current='HEAD')
        self.assertIn('detached', window.app_branch_status.text())
        self.assertTrue(window.app_branch_switch_button.isEnabled())


class UpdateFlowTests(_Fixture):
    def test_switch_runs_supervised_updater_with_branch(self):
        app = self.app()
        with patch('main.QProcess') as process:
            app.switch_app_branch('feature/a')
        process.return_value.start.assert_called_once_with(
            os.path.abspath('update.sh'), ['--switch', 'feature/a', '--no-restart'])
        self.assertEqual(app._update_phase, 'switching branch')

    def test_switch_is_refused_while_recording(self):
        app = self.app()
        app.result_thread = Mock()
        with patch.object(app, '_show_update_message') as show, patch('main.QProcess') as process:
            app.switch_app_branch('feature/a')
        process.assert_not_called()
        show.assert_called_once()
        app.settings_window.set_app_branch_status.assert_called_once()

    def test_successful_switch_restarts(self):
        app = self.app()
        app._update_process = Mock()
        app._update_process.readAllStandardOutput.return_value = b'SWITCHED\n'
        app._update_phase = 'switching branch'
        with patch.object(app, '_request_shutdown') as shutdown:
            app._on_update_finished(0, QProcess.NormalExit)
        shutdown.assert_called_once_with('restart')

    def test_failed_switch_shows_updater_reason(self):
        app = self.app()
        app._update_process = Mock()
        app._update_process.readAllStandardOutput.return_value = (
            b"Fetching origin/x...\nError: branch 'x' does not exist on origin (merged and deleted?).\n")
        app._update_phase = 'switching branch'
        with patch.object(app, '_show_update_message') as show:
            app._on_update_finished(11, QProcess.NormalExit)
        self.assertEqual(show.call_args.args[0], 'Branch switch failed')
        self.assertIn("branch 'x' does not exist", show.call_args.args[1])
        status = app.settings_window.set_app_branch_status.call_args
        self.assertTrue(status.kwargs.get('error'))

    def test_deleted_branch_is_explained_on_manual_check(self):
        app = self.app()
        app._update_process = Mock()
        app._update_process.readAllStandardOutput.return_value = b'BRANCH_GONE\n'
        with patch.object(app, '_show_update_message') as show, \
                patch.object(app, '_current_branch', return_value='feature/a'), \
                patch.object(app, '_start_update') as start:
            app._on_update_check_finished(11, None)
        start.assert_not_called()
        self.assertEqual(show.call_args.args[0], 'Branch no longer on GitHub')
        self.assertIn("'feature/a'", show.call_args.args[1])
        self.assertIn('Settings > About', show.call_args.args[1])

    def test_deleted_branch_is_announced_once_by_background_checks(self):
        app = self.app()
        app._branch_gone_notified = False
        for _ in range(2):
            process = Mock()
            process.readAllStandardOutput.return_value = b'BRANCH_GONE\n'
            app._background_update_check_process = process
            with patch.object(app, '_current_branch', return_value='feature/a'):
                app._on_background_update_check_finished(11, None)
        app.tray_icon.showMessage.assert_called_once()
        self.assertEqual(app.tray_icon.showMessage.call_args.args[2], QSystemTrayIcon.Warning)

    def test_unplayable_feedback_sound_does_not_raise(self):
        app = self.app()
        player = Mock()
        player.play.side_effect = RuntimeError('Error loading player')
        with self.assertLogs('main', level='WARNING') as logs:
            app._play_sound(player)
            app._play_sound(player)
        self.assertEqual(len(logs.records), 1)


class ModelDiscoveryTests(unittest.TestCase):
    """Model discovery no longer uses Qt's network stack, which cannot load OpenSSL 3."""

    @unittest.skipUnless(shutil.which('openssl'), 'openssl command not available')
    def test_https_discovery_verifies_against_system_trust_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            cert, key = Path(tmp) / 'cert.pem', Path(tmp) / 'key.pem'
            subprocess.run(
                ['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                 '-out', str(cert), '-days', '1', '-subj', '/CN=localhost',
                 '-addext', 'subjectAltName=DNS:localhost'],
                check=True, capture_output=True, timeout=60)
            seen = []

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    seen.append((self.path, self.headers.get('Authorization')))
                    body = b'{"data": [{"id": "secure-model"}]}'
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *args):
                    pass

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f'https://localhost:{server.server_port}/v1/models'
                untrusted = model_discovery.fetch(url, 'secret')
                self.assertIsNone(untrusted['status'])
                self.assertIn('CERTIFICATE_VERIFY_FAILED', untrusted['error'])
                self.assertEqual(seen, [])  # the key never left before verification

                # Trusting the certificate the way a system CA store would (OpenSSL reads
                # SSL_CERT_FILE when the process starts).
                probe = subprocess.run(
                    [sys.executable, '-c',
                     'import sys; sys.path.insert(0, "src"); import model_discovery; '
                     f'print(model_discovery.fetch("{url}", "secret")["status"])'],
                    env={**os.environ, 'SSL_CERT_FILE': str(cert)},
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(probe.stdout.strip(), '200', probe.stderr)
                self.assertEqual(seen, [('/v1/models', 'Bearer secret')])
            finally:
                server.shutdown()
                server.server_close()

    def test_http_errors_and_unreachable_servers_are_reported(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(401)
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = model_discovery.fetch(f'http://127.0.0.1:{server.server_port}/v1/models')
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual((result['status'], result['error']), (401, 'HTTP 401'))
        unreachable = model_discovery.fetch(f'http://127.0.0.1:{server.server_port}/v1/models', timeout=2)
        self.assertIsNone(unreachable['status'])
        self.assertTrue(unreachable['error'])

    def test_settings_shows_status_from_background_result(self):
        test_reliability.ReliabilityTests.setUp(self)
        window = SettingsWindow()
        self.addCleanup(lambda: (window.reset_settings(), window.close()))
        window.use_api_checkbox.setChecked(True)
        window.model_discovery_request_id += 1
        request_id = window.model_discovery_request_id
        window.model_discovery_key_withheld = True
        window._on_model_discovery_finished(request_id, {'status': 403, 'body': b'', 'error': 'HTTP 403'})
        self.assertIn('HTTP 403 (API key not sent', window.api_model_status.text())
        window.model_discovery_request_id += 1
        window._on_model_discovery_finished(
            window.model_discovery_request_id, {'status': None, 'body': b'', 'error': 'timed out'})
        self.assertEqual(window.api_model_status.text(), 'Unavailable')
        self.assertIn('timed out', window.api_model_status.toolTip())


if __name__ == '__main__':
    unittest.main()
