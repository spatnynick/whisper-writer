"""Regression tests for the 2026-09-24 review fixes (glossary, API key binding, validation,
three-way synchronization, suspend handling and restart-after-save)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('PYNPUT_BACKEND', 'dummy')
sys.path.insert(0, os.path.abspath('src'))

import numpy as np  # noqa: E402
from PyQt5.QtCore import QObject, QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication, QLineEdit  # noqa: E402

import api_credentials  # noqa: E402
import glossary  # noqa: E402
import transcription  # noqa: E402
from config_sync import (  # noqa: E402
    ConfigSyncManager,
    SYNC_AREA_PATHS,
    default_sync_settings,
    export_selected_areas,
    merge_values,
)
from config_validation import sanitize_config, validate_value  # noqa: E402
from input_simulation import InputSimulator  # noqa: E402
from key_listener import KeyListener, parse_key_combination  # noqa: E402
from main import WhisperWriterApp  # noqa: E402
from ui.settings_window import SettingsWindow  # noqa: E402
from utils import ConfigManager  # noqa: E402

APP = QApplication.instance() or QApplication([])


def reset_config():
    ConfigManager._instance = ConfigManager()
    cfg = ConfigManager._instance
    cfg.schema = cfg.load_config_schema()
    cfg.config = cfg.load_default_config()
    ConfigManager.set_config_value(False, 'misc', 'print_to_terminal')
    return cfg


class GlossaryTests(unittest.TestCase):
    def test_static_map_matches_whole_words_only(self):
        self.assertEqual(glossary.apply_glossary_corrections('This is a problem.'), 'This is a problem.')
        self.assertEqual(glossary.apply_glossary_corrections('It was a pleasure.'), 'It was a pleasure.')
        self.assertEqual(glossary.apply_glossary_corrections('a baptism'), 'a baptism')
        self.assertEqual(glossary.apply_glossary_corrections('we use s a p daily'), 'we use SAP daily')
        self.assertEqual(glossary.apply_glossary_corrections('an eye dock arrived'), 'an IDoc arrived')

    def test_replacement_is_literal_text(self):
        with patch.dict(glossary._load(), {'static_map': {'foo': r'\1 bar\\'}}):
            self.assertEqual(glossary.apply_glossary_corrections('a foo b'), 'a \\1 bar\\\\ b')


class ApiKeyBindingTests(unittest.TestCase):
    def setUp(self):
        reset_config()

    def test_key_is_only_sent_to_its_bound_host_over_a_safe_transport(self):
        with patch.dict(os.environ, {api_credentials.API_KEY_HOST_ENV: ''}):
            self.assertTrue(api_credentials.may_send_key('https://api.openai.com/v1'))
            self.assertFalse(api_credentials.may_send_key('https://attacker.example/v1'))
            self.assertFalse(api_credentials.may_send_key('http://api.openai.com/v1'))
        with patch.dict(os.environ, {api_credentials.API_KEY_HOST_ENV: '192.168.1.5:8000'}):
            # Plain HTTP to another computer never carries the key.
            self.assertFalse(api_credentials.may_send_key('http://192.168.1.5:8000/v1'))
        with patch.dict(os.environ, {api_credentials.API_KEY_HOST_ENV: 'localhost:8000'}):
            self.assertTrue(api_credentials.may_send_key('http://localhost:8000/v1'))
            self.assertFalse(api_credentials.may_send_key('http://localhost:9000/v1'))

    def test_transcription_withholds_key_from_an_unbound_server(self):
        ConfigManager.set_config_value('https://attacker.example/v1', 'model_options', 'api', 'base_url')
        env = {'OPENAI_API_KEY': 'secret-key', api_credentials.API_KEY_HOST_ENV: 'api.openai.com'}
        with patch.dict(os.environ, env), patch('transcription.OpenAI') as client:
            client.return_value.__enter__.return_value.audio.transcriptions.create.return_value.text = 'ok'
            transcription.transcribe_api(np.zeros(1600, dtype=np.int16))
        self.assertEqual(client.call_args.kwargs['api_key'], api_credentials.PLACEHOLDER_KEY)

        ConfigManager.set_config_value('https://api.openai.com/v1', 'model_options', 'api', 'base_url')
        with patch.dict(os.environ, env), patch('transcription.OpenAI') as client:
            client.return_value.__enter__.return_value.audio.transcriptions.create.return_value.text = 'ok'
            transcription.transcribe_api(np.zeros(1600, dtype=np.int16))
        self.assertEqual(client.call_args.kwargs['api_key'], 'secret-key')

    def test_empty_timeout_uses_default_instead_of_no_timeout(self):
        ConfigManager.set_config_value('http://localhost:1234/v1', 'model_options', 'api', 'base_url')
        ConfigManager.set_config_value(None, 'model_options', 'api', 'timeout_seconds')
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}), patch('transcription.OpenAI') as client:
            client.return_value.__enter__.return_value.audio.transcriptions.create.return_value.text = 'ok'
            transcription.transcribe_api(np.zeros(1600, dtype=np.int16))
        self.assertEqual(client.call_args.kwargs['timeout'], 120)

    def test_migration_binds_existing_key_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = os.path.join(tmp, '.env')
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'k', api_credentials.API_KEY_HOST_ENV: ''}):
                os.environ.pop(api_credentials.API_KEY_HOST_ENV)
                self.assertEqual(
                    api_credentials.migrate_api_key_binding(env_path, 'http://localhost:8080/v1'),
                    'localhost:8080',
                )
                self.assertIsNone(api_credentials.migrate_api_key_binding(env_path, 'https://other/v1'))
                self.assertIn('WHISPER_WRITER_API_KEY_HOST', Path(env_path).read_text())


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = reset_config()
        self.schema = self.cfg.schema

    def test_values_are_checked_against_schema(self):
        self.assertIsNone(validate_value(self.schema, ('recording_options', 'activation_key'), 'ctrl+alt+k'))
        self.assertIsNotNone(validate_value(self.schema, ('recording_options', 'activation_key'), 'ctrl+shift+spcae'))
        self.assertIsNotNone(validate_value(self.schema, ('recording_options', 'activation_key'), ''))
        self.assertIsNotNone(validate_value(self.schema, ('recording_options', 'activation_key'), 123))
        self.assertIsNotNone(validate_value(self.schema, ('recording_options', 'recording_mode'), 'sometimes'))
        self.assertIsNotNone(validate_value(self.schema, ('model_options', 'api', 'timeout_seconds'), 0))
        self.assertIsNotNone(validate_value(self.schema, ('model_options', 'api', 'timeout_seconds'), None))
        self.assertIsNotNone(validate_value(self.schema, ('post_processing', 'writing_key_press_delay'), -1))
        self.assertIsNotNone(validate_value(self.schema, ('model_options', 'use_api'), 'yes'))
        self.assertIsNotNone(validate_value(self.schema, ('model_options', 'api', 'base_url'), 'file:///etc'))
        self.assertIsNone(validate_value(self.schema, ('model_options', 'api', 'base_url'), None))
        self.assertIsNone(validate_value(self.schema, ('recording_options', 'sound_device'), 2))
        self.assertIsNone(validate_value(self.schema, ('recording_options', 'sound_device'), '2'))
        self.assertIsNotNone(validate_value(self.schema, ('recording_options', 'sound_device'), 'usb mic'))

    def test_invalid_loaded_values_fall_back_to_defaults(self):
        config = self.cfg.load_default_config()
        config['recording_options']['activation_key'] = 123
        config['model_options']['api']['timeout_seconds'] = 'slow'
        config['recording_options']['recording_mode'] = 'continuous'
        config['model_options']['use_api'] = True
        config['recording_options']['sample_rate'] = 44100
        replaced = {path for path, _value, _reason in sanitize_config(self.schema, config)}
        self.assertIn(('recording_options', 'activation_key'), replaced)
        self.assertIn(('model_options', 'api', 'timeout_seconds'), replaced)
        self.assertIn(('recording_options', 'sample_rate'), replaced)
        self.assertEqual(config['recording_options']['activation_key'], 'ctrl+shift+space')
        self.assertEqual(config['model_options']['api']['timeout_seconds'], 120)
        self.assertEqual(config['recording_options']['sample_rate'], 16000)

    def test_hotkey_parser_rejects_unknown_and_empty_parts(self):
        self.assertEqual(len(parse_key_combination('ctrl+shift+space')), 3)
        for bad in ('', 'ctrl++space', 'ctrl+shift+spcae', None):
            with self.assertRaises(ValueError):
                parse_key_combination(bad)

    def test_invalid_hotkey_falls_back_to_default_chord(self):
        ConfigManager.set_config_value('ctrl+shift+spcae', 'recording_options', 'activation_key')
        listener = KeyListener.__new__(KeyListener)
        with patch('builtins.print'):
            listener.load_activation_keys()
        self.assertEqual(listener.key_chord.keys, parse_key_combination('ctrl+shift+space'))

    def test_settings_refuse_invalid_hotkey(self):
        settings = SettingsWindow()
        try:
            settings.findChild(QLineEdit, 'recording_options_activation_key_input').setText('ctrl+shift+spcae')
            with patch('ui.settings_window.QMessageBox.warning') as warning, \
                    patch.object(ConfigManager, 'save_config') as save:
                settings.save_settings()
            warning.assert_called_once()
            self.assertIn('unknown key', warning.call_args.args[2])
            save.assert_not_called()
        finally:
            settings.reset_settings()
            settings.close()

    def test_invalid_key_delay_does_not_abort_typing(self):
        simulator = InputSimulator.__new__(InputSimulator)
        simulator.input_method = 'pynput'
        simulator.keyboard = Mock()
        for delay in (None, -1):
            ConfigManager.set_config_value(delay, 'post_processing', 'writing_key_press_delay')
            simulator.keyboard.reset_mock()
            with patch('input_simulation.time.sleep'):
                simulator.typewrite('abc')
            self.assertEqual(simulator.keyboard.press.call_count, 3)


class ThreeWaySyncTests(unittest.TestCase):
    """Two computers with separate managed clones against one bare remote."""

    @staticmethod
    def git(directory, *args):
        return subprocess.run(
            ['git', '-c', 'user.name=Sync Test', '-c', 'user.email=sync@example.invalid', *args],
            cwd=directory, capture_output=True, text=True, check=True, timeout=15,
        )

    def setUp(self):
        reset_config()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.remote = self.root / 'remote.git'
        self.git(self.root, 'init', '--bare', '--initial-branch=main', str(self.remote))

    def tearDown(self):
        self._tmp.cleanup()

    def settings(self):
        settings = default_sync_settings()
        settings.update(enabled=True, repository_url=str(self.remote), branch='main',
                        initial_sync_completed=True)
        for area in settings['areas']:
            settings['areas'][area] = area in ('hotkeys', 'audio', 'provider')
        return settings

    def run_as(self, computer, operation, config):
        home = self.root / computer
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(home)}):
            manager = ConfigSyncManager(self.settings())
            result = getattr(manager, operation)(config)
            return result, Path(manager.repository_path)

    @staticmethod
    def config():
        return ConfigManager._instance.load_default_config()

    def test_edits_to_different_settings_on_two_computers_are_both_kept(self):
        a, b = self.config(), self.config()
        self.run_as('a', 'push', a)
        _result, _ = self.run_as('b', 'auto_sync', b)

        a['recording_options']['activation_key'] = 'ctrl+alt+k'
        self.run_as('a', 'auto_sync', a)
        b['recording_options']['sample_rate'] = 48000
        result, clone_b = self.run_as('b', 'auto_sync', b)

        # B keeps its own sample rate and receives A's hotkey instead of reverting it.
        updates = {tuple(path): value for path, _previous, value in result['updates']}
        self.assertEqual(updates, {('recording_options', 'activation_key'): 'ctrl+alt+k'})
        self.assertTrue(result['pushed'])
        result_a, _ = self.run_as('a', 'auto_sync', a)
        updates_a = {tuple(path): value for path, _previous, value in result_a['updates']}
        self.assertEqual(updates_a, {('recording_options', 'sample_rate'): 48000})
        synced = (clone_b / 'whisperwriter-sync.yaml').read_text()
        self.assertIn('ctrl+alt+k', synced)
        self.assertIn('48000', synced)

    def test_conflicting_edits_keep_remote_value_and_are_reported(self):
        base = {('recording_options', 'activation_key'): 'ctrl+shift+space'}
        mine = {('recording_options', 'activation_key'): 'ctrl+alt+m'}
        theirs = {('recording_options', 'activation_key'): 'ctrl+alt+t'}
        merged, conflicts = merge_values(base, mine, theirs, ConfigManager.get_schema())
        self.assertEqual(merged[('recording_options', 'activation_key')], 'ctrl+alt+t')
        self.assertEqual(conflicts, ['recording_options.activation_key'])

    def test_invalid_remote_values_are_rejected(self):
        a = self.config()
        self.run_as('a', 'push', a)
        other = self.root / 'hostile'
        self.git(self.root, 'clone', '-b', 'main', str(self.remote), str(other))
        (other / 'whisperwriter-sync.yaml').write_text(
            'version: 1\nareas:\n  hotkeys:\n    recording_options:\n      activation_key: 123\n'
            '  audio:\n    recording_options:\n      sample_rate: 32000\n',
            encoding='utf-8',
        )
        self.git(other, 'commit', '-am', 'bad values')
        self.git(other, 'push', 'origin', 'main')

        result, _ = self.run_as('a', 'auto_sync', a)
        updates = {tuple(path): value for path, _previous, value in result['updates']}
        self.assertEqual(updates, {('recording_options', 'sample_rate'): 32000})
        self.assertEqual([path for path, _reason in result['rejected']], ['recording_options.activation_key'])

    def test_unpushed_local_commit_is_rebuilt_on_a_moved_remote(self):
        a, b = self.config(), self.config()
        self.run_as('a', 'push', a)
        _result, clone_b = self.run_as('b', 'auto_sync', b)

        # B committed a change whose push failed (simulated by committing locally only).
        b['recording_options']['sample_rate'] = 48000
        sync_file = clone_b / 'whisperwriter-sync.yaml'
        sync_file.write_text(sync_file.read_text().replace('16000', '48000'))
        self.git(clone_b, 'commit', '-am', 'unpushed')
        # Meanwhile A pushed another change.
        a['recording_options']['activation_key'] = 'ctrl+alt+k'
        self.run_as('a', 'auto_sync', a)

        result, _ = self.run_as('b', 'auto_sync', b)
        updates = {tuple(path): value for path, _previous, value in result['updates']}
        self.assertEqual(updates, {('recording_options', 'activation_key'): 'ctrl+alt+k'})
        remote_file = self.git(self.root, '--git-dir', str(self.remote), 'show', 'main:whisperwriter-sync.yaml').stdout
        self.assertIn('48000', remote_file)
        self.assertIn('ctrl+alt+k', remote_file)

    def test_machine_specific_values_are_not_synchronized(self):
        synced = {path for paths in SYNC_AREA_PATHS.values() for path in paths}
        self.assertNotIn(('recording_options', 'sound_device'), synced)
        self.assertNotIn(('model_options', 'local', 'device'), synced)
        self.assertNotIn(('model_options', 'local', 'compute_type'), synced)
        config = self.config()
        config['recording_options']['sound_device'] = '7'
        self.assertNotIn('sound_device', str(export_selected_areas(config, default_sync_settings())))


class MainSyncAndSuspendTests(unittest.TestCase):
    def setUp(self):
        reset_config()

    def app(self):
        app = WhisperWriterApp.__new__(WhisperWriterApp)
        QObject.__init__(app)
        app.settings_window = Mock()
        app.settings_window.isVisible.return_value = False
        app.failed_recordings = []
        app._shutdown_action = None
        app._sync_worker = None
        app._sync_pending_action = None
        app._sync_pending_settings = None
        app._sync_restart_after = False
        app._sync_pending_restart_notice = False
        app._sync_current_restart_after = False
        app._sync_current_restart_notice = False
        app._sync_current_settings = default_sync_settings()
        app._sync_current_succeeded = False
        app._sync_confirming = False
        app._sync_timer = QTimer()
        app._hold_output_worker = None
        app._continue_recording = False
        app._model_hold_timer = QTimer()
        app._model_hold_pending = False
        app._model_hold_triggered = False
        app._model_hold_action = None
        return app

    def test_updates_apply_to_current_config_and_keep_concurrent_local_edits(self):
        app = self.app()
        # The user changed the hotkey while the worker ran (worker saw ctrl+shift+space).
        ConfigManager.set_config_value('ctrl+alt+l', 'recording_options', 'activation_key')
        result = {'updates': [
            (['recording_options', 'activation_key'], 'ctrl+shift+space', 'ctrl+alt+r'),
            (['recording_options', 'sample_rate'], 16000, 48000),
        ]}
        with patch.object(ConfigManager, 'save_config'):
            self.assertTrue(app._apply_sync_updates(result))
        self.assertEqual(ConfigManager.get_config_value('recording_options', 'activation_key'), 'ctrl+alt+l')
        self.assertEqual(ConfigManager.get_config_value('recording_options', 'sample_rate'), 48000)

    def test_declined_endpoint_change_is_not_applied_and_stops_provider_sync(self):
        app = self.app()
        result = {'updates': [(['model_options', 'api', 'base_url'], 'https://api.openai.com/v1',
                               'https://attacker.example/v1')]}
        saved = []
        with patch.object(app, '_confirm_remote_endpoint', return_value=False), \
                patch('main.load_sync_settings', return_value=default_sync_settings()), \
                patch('main.save_sync_settings', side_effect=lambda s: saved.append(s) or s), \
                patch.object(ConfigManager, 'save_config') as save:
            self.assertFalse(app._apply_sync_updates(result))
        save.assert_not_called()
        self.assertEqual(ConfigManager.get_config_value('model_options', 'api', 'base_url'),
                         'https://api.openai.com/v1')
        self.assertFalse(saved[0]['areas']['provider'])
        self.assertFalse(app._sync_current_settings['areas']['provider'])

    def test_failed_sync_after_save_still_restarts(self):
        app = self.app()
        app._sync_current_restart_after = True
        app._sync_current_succeeded = False
        with patch.object(app, '_request_shutdown') as shutdown:
            app._on_sync_worker_finished()
        shutdown.assert_called_once_with('restart')

    def test_failed_manual_pull_does_not_restart(self):
        app = self.app()
        app._sync_current_restart_after = True
        app._sync_current_restart_notice = True
        with patch.object(app, '_request_shutdown') as shutdown, \
                patch.object(app, '_show_update_message'):
            app._on_sync_worker_finished()
        shutdown.assert_not_called()

    def test_suspend_keeps_recording_for_retry_and_holds_transcription_output(self):
        app = self.app()
        app.escape_guard = None
        recording = Mock()
        recording.isRunning.return_value = True
        recording.is_recording = True
        app.result_thread = recording
        app.current_status = 'recording'
        app.on_prepare_for_sleep(True)
        recording.cancel_recording.assert_called_once()
        recording.stop.assert_not_called()

        transcribing = Mock()
        transcribing.isRunning.return_value = True
        transcribing.is_recording = False
        app.result_thread = transcribing
        app.current_status = 'transcribing'
        app.on_prepare_for_sleep(True)
        transcribing.stop.assert_not_called()
        self.assertIs(app._hold_output_worker, transcribing)

        app.tray_icon = Mock()
        app.copy_last_transcript_action = Mock()
        app.key_listener = Mock()
        app.input_simulator = Mock()
        app.on_transcription_complete('text after resume')
        app.input_simulator.typewrite.assert_not_called()
        self.assertEqual(app.last_transcript, 'text after resume')


if __name__ == '__main__':
    unittest.main()
