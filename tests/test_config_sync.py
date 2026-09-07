"""Focused tests for selected-area Git synchronization."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath('src'))

from config_sync import (  # noqa: E402
    ConfigSyncManager,
    default_sync_settings,
    export_selected_areas,
    apply_selected_areas,
    SyncError,
)


class ConfigSyncTests(unittest.TestCase):
    @staticmethod
    def git(directory, *args):
        return subprocess.run(
            ['git', '-c', 'user.name=Sync Test', '-c', 'user.email=sync@example.invalid', *args],
            cwd=directory,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )

    def make_remote(self, root):
        remote = root / 'remote.git'
        seed = root / 'seed'
        self.git(root, 'init', '--bare', str(remote))
        self.git(root, 'init', '-b', 'main', str(seed))
        (seed / 'README').write_text('seed\n', encoding='utf-8')
        self.git(seed, 'add', '.')
        self.git(seed, 'commit', '-m', 'seed')
        self.git(seed, 'remote', 'add', 'origin', str(remote))
        self.git(seed, 'push', '-u', 'origin', 'main')
        return remote

    @staticmethod
    def settings(remote):
        settings = default_sync_settings()
        settings.update(
            enabled=True,
            repository_url=str(remote),
            branch='main',
            initial_sync_completed=True,
        )
        settings['areas']['prompt_context'] = True
        settings['areas']['provider'] = False
        return settings

    @staticmethod
    def config(prompt, provider_model='local-model'):
        return {
            'model_options': {
                'use_api': True,
                'common': {'initial_prompt': prompt, 'language': None, 'temperature': 0.0},
                'api': {
                    'base_url': 'https://example.invalid/v1',
                    'model': provider_model,
                    'secondary_model': None,
                    'timeout_seconds': 120,
                    'api_key': 'must-not-sync',
                },
                'local': {'model': 'base', 'model_path': '/machine-only/model'},
            },
            'recording_options': {'activation_key': 'ctrl+shift+space'},
            'post_processing': {},
            'misc': {},
        }

    def test_new_sync_defaults_are_safe_and_complete(self):
        settings = default_sync_settings()
        self.assertEqual(settings['interval_minutes'], 15)
        self.assertTrue(all(settings['areas'].values()))
        self.assertFalse(settings['initial_sync_completed'])

    def test_selected_area_projection_excludes_disabled_and_machine_values(self):
        settings = default_sync_settings()
        settings['areas']['prompt_context'] = True
        for area in settings['areas']:
            settings['areas'][area] = area == 'prompt_context'
        config = self.config('portable context')

        payload = export_selected_areas(config, settings)
        self.assertEqual(list(payload['areas']), ['prompt_context'])
        self.assertNotIn('api_key', str(payload))
        self.assertNotIn('model_path', str(payload))

        local = self.config('local context', provider_model='local-provider')
        applied = apply_selected_areas(local, payload, settings)
        self.assertEqual(applied['model_options']['common']['initial_prompt'], 'portable context')
        self.assertEqual(applied['model_options']['api']['model'], 'local-provider')

    def test_test_connection_is_read_only_and_push_pull_are_selective(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_home = root / 'config-home'
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config_home)}):
                remote = self.make_remote(root)
                settings = self.settings(remote)
                manager = ConfigSyncManager(settings)

                self.assertFalse((config_home / 'whisper-writer').exists())
                connection = manager.test_connection()
                self.assertEqual(connection['action'], 'test')
                self.assertEqual(connection['branches'], ['main'])
                self.assertEqual(connection['default_branch'], 'main')
                self.assertFalse((config_home / 'whisper-writer').exists())

                first = manager.push(self.config('first'))
                self.assertTrue(first['changed'])
                sync_file = Path(manager.repository_path) / 'whisperwriter-sync.yaml'
                self.assertTrue(sync_file.exists())
                first_contents = sync_file.read_text(encoding='utf-8')
                first_commit = self.git(Path(manager.repository_path), 'rev-parse', 'HEAD').stdout.strip()

                second = manager.push(self.config('first'))
                self.assertFalse(second['changed'])
                self.assertEqual(sync_file.read_text(encoding='utf-8'), first_contents)
                self.assertEqual(self.git(Path(manager.repository_path), 'rev-parse', 'HEAD').stdout.strip(), first_commit)

                other = root / 'other'
                self.git(root, 'clone', '-b', 'main', str(remote), str(other))
                (other / 'whisperwriter-sync.yaml').write_text(
                    'version: 1\nareas:\n  prompt_context:\n    model_options:\n      common:\n        initial_prompt: remote context\n',
                    encoding='utf-8',
                )
                self.git(other, 'add', 'whisperwriter-sync.yaml')
                self.git(other, 'commit', '-m', 'remote context')
                self.git(other, 'push', 'origin', 'main')

                pulled = manager.pull(self.config('local context', provider_model='local-provider'))
                self.assertTrue(pulled['changed'])
                self.assertEqual(
                    pulled['config']['model_options']['common']['initial_prompt'],
                    'remote context',
                )
                self.assertEqual(pulled['config']['model_options']['api']['model'], 'local-provider')

    def test_empty_remote_is_bootstrapped_on_first_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_home = root / 'config-home'
            remote = root / 'empty.git'
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config_home)}):
                self.git(root, 'init', '--bare', str(remote))
                settings = self.settings(remote)
                settings['branch'] = ''
                manager = ConfigSyncManager(settings)

                connection = manager.test_connection()
                self.assertTrue(connection['empty'])
                self.assertEqual(connection['branches'], [])
                self.assertEqual(connection['default_branch'], '')
                self.assertEqual(connection['branch'], '')

                # An empty remote has no detectable branch. The user must choose the
                # initial branch explicitly before the first bootstrap push.
                settings['branch'] = 'main'
                manager = ConfigSyncManager(settings)
                pushed = manager.push(self.config('initial context'))
                self.assertTrue(pushed['changed'])
                remote_head = self.git(
                    root, '--git-dir', str(remote), 'rev-parse', 'refs/heads/main'
                ).stdout.strip()
                self.assertEqual(pushed['remote_commit'], remote_head)
                self.assertIn('initial context', (Path(manager.repository_path) / 'whisperwriter-sync.yaml').read_text(encoding='utf-8'))

    def test_non_empty_remote_requires_first_pull_before_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_home = root / 'config-home'
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config_home)}):
                remote = self.make_remote(root)
                settings = default_sync_settings()
                settings.update(enabled=True, repository_url=str(remote), branch='main')
                manager = ConfigSyncManager(settings)
                with self.assertRaisesRegex(SyncError, 'Pull the remote settings'):
                    manager.push(self.config('must not overwrite'))

    def test_first_auto_sync_pulls_a_non_empty_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_home = root / 'config-home'
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config_home)}):
                remote = self.make_remote(root)
                other = root / 'other'
                self.git(root, 'clone', '-b', 'main', str(remote), str(other))
                (other / 'whisperwriter-sync.yaml').write_text(
                    'version: 1\nareas:\n  prompt_context:\n    model_options:\n      common:\n        initial_prompt: remote first\n',
                    encoding='utf-8',
                )
                self.git(other, 'add', 'whisperwriter-sync.yaml')
                self.git(other, 'commit', '-m', 'remote settings')
                self.git(other, 'push', 'origin', 'main')

                settings = default_sync_settings()
                settings.update(enabled=True, repository_url=str(remote), branch='main')
                manager = ConfigSyncManager(settings)
                result = manager.auto_sync(self.config('local first'))
                self.assertEqual(result['action'], 'pull')
                self.assertTrue(result['initial_sync_completed'])
                self.assertEqual(
                    result['config']['model_options']['common']['initial_prompt'],
                    'remote first',
                )


if __name__ == '__main__':
    unittest.main()
