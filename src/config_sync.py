"""Optional Git synchronization for selected WhisperWriter settings.

The application keeps its normal runtime configuration in one YAML file.  This module
projects selected settings into a separate, Git-managed YAML file and can apply those
selected settings back without exposing machine-local values such as API keys, device
indexes, or model paths.
"""

import copy
import os
import shlex
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

import yaml
from PyQt5.QtCore import QThread, pyqtSignal

from utils import ConfigManager


SYNC_FILE_NAME = 'whisperwriter-sync.yaml'
SYNC_VERSION = 1

SYNC_AREA_LABELS = {
    'prompt_context': 'Prompt context',
    'provider': 'Provider settings',
    'hotkeys': 'Hotkeys',
    'recording': 'Recording behavior',
    'audio': 'Audio settings',
    'local_model': 'Local model',
    'text_output': 'Text output',
    'interface': 'Interface and general settings',
}

# These paths intentionally contain no API key or machine-specific model path.  The
# program can therefore synchronize a whole area without accidentally moving secrets or
# paths between Linux and Windows installations.
SYNC_AREA_PATHS = {
    'prompt_context': (
        ('model_options', 'common', 'initial_prompt'),
    ),
    'provider': (
        ('model_options', 'use_api'),
        ('model_options', 'common', 'language'),
        ('model_options', 'common', 'temperature'),
        ('model_options', 'api', 'base_url'),
        ('model_options', 'api', 'model'),
        ('model_options', 'api', 'secondary_model'),
        ('model_options', 'api', 'timeout_seconds'),
    ),
    'hotkeys': (
        ('recording_options', 'activation_key'),
        ('recording_options', 'input_backend'),
    ),
    'recording': (
        ('recording_options', 'recording_mode'),
        ('recording_options', 'silence_duration'),
        ('recording_options', 'min_duration'),
    ),
    'audio': (
        ('recording_options', 'sound_device'),
        ('recording_options', 'sample_rate'),
    ),
    'local_model': (
        ('model_options', 'local', 'model'),
        ('model_options', 'local', 'device'),
        ('model_options', 'local', 'compute_type'),
        ('model_options', 'local', 'condition_on_previous_text'),
        ('model_options', 'local', 'vad_filter'),
    ),
    'text_output': (
        ('post_processing', 'writing_key_press_delay'),
        ('post_processing', 'remove_trailing_period'),
        ('post_processing', 'add_trailing_space'),
        ('post_processing', 'remove_capitalization'),
        ('post_processing', 'input_method'),
    ),
    'interface': (
        ('misc', 'print_to_terminal'),
        ('misc', 'hide_status_window'),
        ('misc', 'noise_on_completion'),
        ('misc', 'status_window_position'),
        ('misc', 'show_tray_status_icon'),
        ('misc', 'play_toggle_sounds'),
        ('misc', 'toggle_sound_volume'),
    ),
}

_MISSING = object()


class SyncError(RuntimeError):
    """An expected synchronization failure that can be shown in Settings."""


def default_sync_settings():
    """Return local-only synchronization preferences and status."""
    return {
        'enabled': False,
        'repository_url': '',
        'branch': '',
        # Empty means automatic Git detection.  A non-empty value is an explicit override.
        'git_path': '',
        'push_on_save': True,
        'interval_minutes': 0,
        'areas': {area: area == 'prompt_context' for area in SYNC_AREA_PATHS},
        'auth': {
            # system: use Git Credential Manager, credential.helper, SSH agent, etc.
            # https: use the local username/token fields below through GIT_ASKPASS.
            # ssh: use the local private-key path and optional passphrase.
            'type': 'system',
            'username': '',
            'secret': '',
            'ssh_key_path': '',
            'ssh_passphrase': '',
        },
        'status': 'Not configured',
        'last_error': '',
        'last_success': '',
        'last_checked': '',
        'last_remote_commit': '',
    }


def normalize_sync_settings(raw):
    """Merge a possibly incomplete local sync file with safe defaults."""
    settings = default_sync_settings()
    if not isinstance(raw, dict):
        return settings

    for key in ('enabled', 'push_on_save'):
        if isinstance(raw.get(key), bool):
            settings[key] = raw[key]
    for key in ('repository_url', 'branch', 'git_path', 'status', 'last_error',
                'last_success', 'last_checked', 'last_remote_commit'):
        if isinstance(raw.get(key), str):
            settings[key] = raw[key]
    try:
        settings['interval_minutes'] = max(0, int(raw.get('interval_minutes', 0)))
    except (TypeError, ValueError):
        pass

    areas = raw.get('areas')
    if isinstance(areas, dict):
        for area in SYNC_AREA_PATHS:
            if isinstance(areas.get(area), bool):
                settings['areas'][area] = areas[area]

    auth = raw.get('auth')
    if isinstance(auth, dict):
        for key in ('type', 'username', 'secret', 'ssh_key_path', 'ssh_passphrase'):
            if isinstance(auth.get(key), str):
                settings['auth'][key] = auth[key]
    if settings['auth']['type'] not in ('system', 'https', 'ssh'):
        settings['auth']['type'] = 'system'
    return settings


def load_sync_settings(path=None):
    """Load local-only synchronization preferences."""
    path = path or ConfigManager.sync_settings_path()
    if not os.path.isfile(path):
        return default_sync_settings()
    try:
        with open(path, 'r', encoding='utf-8-sig') as file:
            return normalize_sync_settings(yaml.safe_load(file))
    except (OSError, yaml.YAMLError):
        settings = default_sync_settings()
        settings['status'] = 'Error'
        settings['last_error'] = 'The local synchronization settings file could not be read.'
        return settings


def save_sync_settings(settings, path=None):
    """Atomically save local-only synchronization preferences."""
    path = path or ConfigManager.sync_settings_path()
    path = os.path.abspath(path)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    normalized = normalize_sync_settings(settings)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                         delete=False) as file:
            temporary_path = file.name
            yaml.safe_dump(normalized, file, default_flow_style=False, sort_keys=False,
                           allow_unicode=True)
            file.flush()
            os.fsync(file.fileno())
        if os.name != 'nt':
            os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return normalized


def synchronization_repository_path():
    """Return the managed local clone path inside the platform config directory."""
    return os.path.join(ConfigManager.config_directory(), 'sync', 'repository')


def detect_git_path(configured_path=''):
    """Find Git on PATH or in common Linux/Windows installation locations."""
    configured_path = (configured_path or '').strip()
    if configured_path:
        on_path = shutil.which(configured_path)
        if on_path:
            return os.path.abspath(on_path)
        if os.path.isfile(configured_path):
            return os.path.abspath(configured_path)
        return None

    on_path = shutil.which('git')
    if on_path:
        return os.path.abspath(on_path)

    if os.name == 'nt':
        candidates = (
            os.path.join(os.getenv('ProgramFiles', r'C:\Program Files'), 'Git', 'cmd', 'git.exe'),
            os.path.join(os.getenv('ProgramFiles', r'C:\Program Files'), 'Git', 'bin', 'git.exe'),
            os.path.join(os.getenv('LOCALAPPDATA', ''), 'Programs', 'Git', 'cmd', 'git.exe'),
        )
    else:
        candidates = ('/usr/bin/git', '/usr/local/bin/git')
    return next((os.path.abspath(path) for path in candidates if os.path.isfile(path)), None)


def _get_nested(mapping, path, default=None):
    value = mapping
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def _set_nested(mapping, path, value):
    target = mapping
    for key in path[:-1]:
        child = target.get(key)
        if not isinstance(child, dict):
            child = {}
            target[key] = child
        target = child
    target[path[-1]] = copy.deepcopy(value)


def selected_areas(settings):
    areas = settings.get('areas', {}) if isinstance(settings, dict) else {}
    return [area for area in SYNC_AREA_PATHS if areas.get(area) is True]


def export_selected_areas(config, settings):
    """Project the selected values from the full runtime config into a sync payload."""
    exported = {}
    for area in selected_areas(settings):
        area_config = {}
        for path in SYNC_AREA_PATHS[area]:
            _set_nested(area_config, path, _get_nested(config, path))
        exported[area] = area_config
    return {'version': SYNC_VERSION, 'areas': exported}


def apply_selected_areas(config, payload, settings):
    """Apply only selected known paths from a sync payload to a config copy."""
    result = copy.deepcopy(config)
    if not isinstance(payload, dict) or not isinstance(payload.get('areas'), dict):
        return result
    remote_areas = payload['areas']
    for area in selected_areas(settings):
        area_config = remote_areas.get(area)
        if not isinstance(area_config, dict):
            continue
        for path in SYNC_AREA_PATHS[area]:
            value = _get_nested(area_config, path, default=_MISSING)
            if value is not _MISSING:
                # ``None`` is a valid synchronized value (for example an unset language),
                # so a distinct sentinel is needed for missing paths.
                _set_nested(result, path, value)
    return result


def _payload_with_updates(existing, updates):
    payload = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    payload['version'] = SYNC_VERSION
    if not isinstance(payload.get('areas'), dict):
        payload['areas'] = {}
    for area, values in updates.items():
        payload['areas'][area] = copy.deepcopy(values)
    return payload


def _read_sync_payload(path):
    if not os.path.isfile(path):
        return {'version': SYNC_VERSION, 'areas': {}}
    try:
        with open(path, 'r', encoding='utf-8-sig') as file:
            payload = yaml.safe_load(file)
    except (OSError, yaml.YAMLError) as error:
        raise SyncError(f'Could not read synchronization file: {error}') from error
    if payload is None:
        return {'version': SYNC_VERSION, 'areas': {}}
    if not isinstance(payload, dict) or not isinstance(payload.get('areas', {}), dict):
        raise SyncError('The synchronization file must contain an areas mapping.')
    return payload


def _write_sync_payload(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                         delete=False) as file:
            temporary_path = file.name
            yaml.safe_dump(payload, file, default_flow_style=False, sort_keys=False,
                           allow_unicode=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


class ConfigSyncManager:
    """Synchronous Git operations used from a background ``SyncWorker``."""

    def __init__(self, settings):
        self.settings = normalize_sync_settings(settings)
        self.repository_url = self.settings['repository_url'].strip()
        if not self.repository_url:
            raise SyncError('A Git repository URL has not been configured.')
        self.git_path = detect_git_path(self.settings.get('git_path'))
        if not self.git_path:
            raise SyncError('Git was not found. Install Git or configure its executable path.')
        self.repository_path = Path(synchronization_repository_path())

    @contextmanager
    def _auth_environment(self):
        """Build a subprocess environment without putting credentials in Git arguments."""
        auth = self.settings.get('auth', {})
        auth_type = auth.get('type', 'system')
        environment = os.environ.copy()
        # Synchronization runs without a terminal.  Existing credential helpers and SSH
        # agents still work, while an unconfigured helper fails cleanly instead of waiting
        # forever for an invisible password prompt.
        environment['GIT_TERMINAL_PROMPT'] = '0'
        temporary_directory = None
        try:
            if auth_type == 'https':
                if not auth.get('username') or not auth.get('secret'):
                    raise SyncError('HTTPS authentication requires a username and token/password.')
                temporary_directory = tempfile.TemporaryDirectory(prefix='whisper-writer-askpass-')
                askpass = self._write_askpass(Path(temporary_directory.name))
                environment.update({
                    'GIT_ASKPASS': str(askpass),
                    'WHISPER_WRITER_GIT_USERNAME': auth['username'],
                    'WHISPER_WRITER_GIT_SECRET': auth['secret'],
                })
            elif auth_type == 'ssh':
                key_path = os.path.expanduser((auth.get('ssh_key_path') or '').strip())
                if not key_path or not os.path.isfile(key_path):
                    raise SyncError('SSH authentication requires an existing private key path.')
                ssh_path = key_path.replace('"', '\\"')
                if os.name == 'nt':
                    ssh_command = f'ssh -i "{ssh_path}" -o IdentitiesOnly=yes'
                else:
                    ssh_command = f'ssh -i {shlex.quote(key_path)} -o IdentitiesOnly=yes'
                environment['GIT_SSH_COMMAND'] = ssh_command
                if auth.get('ssh_passphrase'):
                    temporary_directory = tempfile.TemporaryDirectory(prefix='whisper-writer-askpass-')
                    askpass = self._write_askpass(Path(temporary_directory.name))
                    environment.update({
                        'SSH_ASKPASS': str(askpass),
                        'SSH_ASKPASS_REQUIRE': 'force',
                        'WHISPER_WRITER_GIT_SECRET': auth['ssh_passphrase'],
                    })
            yield environment
        finally:
            if temporary_directory:
                temporary_directory.cleanup()

    @staticmethod
    def _write_askpass(directory):
        if os.name == 'nt':
            path = directory / 'askpass.cmd'
            # HTTPS supplies the username through Git's credential.username config.  The
            # helper therefore only needs to return the token/password; SSH also asks only
            # for the passphrase.
            path.write_text('@echo off\r\necho %WHISPER_WRITER_GIT_SECRET%\r\n', encoding='utf-8')
        else:
            path = directory / 'askpass.sh'
            path.write_text('#!/bin/sh\nprintf "%s\\n" "$WHISPER_WRITER_GIT_SECRET"\n', encoding='utf-8')
            path.chmod(0o700)
        return path

    def _run_git(self, args, cwd=None, timeout=90, check=True):
        command = [self.git_path]
        if self.settings.get('auth', {}).get('type') == 'https':
            username = self.settings.get('auth', {}).get('username', '')
            if username:
                command.extend(['-c', f'credential.username={username}'])
        command.extend(args)
        try:
            with self._auth_environment() as environment:
                result = subprocess.run(
                    command,
                    cwd=str(cwd) if cwd else None,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
        except subprocess.TimeoutExpired as error:
            raise SyncError('Git operation timed out.') from error
        except (OSError, subprocess.SubprocessError) as error:
            raise SyncError(f'Could not run Git: {error}') from error
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip()
            raise SyncError(detail or f'Git exited with code {result.returncode}.')
        return result

    def _remote_info(self):
        """Read remote branches and default branch without changing the local clone."""
        symref = self._run_git(['ls-remote', '--symref', self.repository_url, 'HEAD'], timeout=30).stdout
        heads = self._run_git(['ls-remote', '--heads', self.repository_url], timeout=30).stdout
        default_branch = None
        head_commit = None
        for line in symref.splitlines():
            if line.startswith('ref: refs/heads/') and line.endswith('\tHEAD'):
                default_branch = line.split('\t', 1)[0][len('ref: refs/heads/'):]
            elif '\tHEAD' in line:
                head_commit = line.split('\t', 1)[0].strip()
        branches = []
        for line in heads.splitlines():
            if '\trefs/heads/' not in line:
                continue
            commit, ref = line.split('\t', 1)
            branch = ref[len('refs/heads/'):]
            if branch and branch not in branches:
                branches.append(branch)
            if branch == default_branch:
                head_commit = commit.strip()
        branches.sort()
        if not default_branch:
            configured = self.settings.get('branch', '').strip()
            if configured in branches:
                default_branch = configured
            elif 'main' in branches:
                default_branch = 'main'
            elif 'master' in branches:
                default_branch = 'master'
            elif branches:
                default_branch = branches[0]
            for line in heads.splitlines():
                if line.endswith(f'\trefs/heads/{default_branch}'):
                    head_commit = line.split('\t', 1)[0].strip()
                    break
        empty = not branches and not head_commit
        if empty:
            # An empty remote has no branch for Git to report. Use the conventional branch
            # name (or an explicitly configured one) so the first push can bootstrap it.
            default_branch = self.settings.get('branch', '').strip() or 'main'
        return {
            'branches': branches,
            'default_branch': default_branch or '',
            'head_commit': head_commit or '',
            'empty': empty,
        }

    def test_connection(self):
        info = self._remote_info()
        configured = self.settings.get('branch', '').strip()
        if info['branches'] and configured and configured not in info['branches']:
            raise SyncError(f'Configured branch was not found: {configured}')
        branches = info['branches'] or [info['default_branch']]
        return {
            'git_path': self.git_path,
            'branches': branches,
            'default_branch': info['default_branch'],
            'branch': configured or info['default_branch'],
            'empty': info['empty'],
        }

    def _selected_branch(self, info):
        branch = self.settings.get('branch', '').strip() or info.get('default_branch', '')
        if not branch or (info.get('branches') and branch not in info['branches']):
            raise SyncError('Select a valid branch after testing the repository connection.')
        return branch

    def _ensure_repository(self, branch, empty_remote=False):
        path = self.repository_path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            if empty_remote:
                # ``clone --branch`` cannot initialize an empty remote. Create an unborn
                # local branch and attach the remote; the first selected-area push creates
                # the initial commit and branch on the server.
                initialized = self._run_git(
                    ['init', '--initial-branch', branch, str(path)],
                    cwd=path.parent,
                    check=False,
                )
                if initialized.returncode != 0:
                    self._run_git(['init', str(path)], cwd=path.parent)
                    self._run_git(['checkout', '-B', branch], cwd=path)
                self._run_git(['remote', 'add', 'origin', self.repository_url], cwd=path)
            else:
                self._run_git(['clone', '--branch', branch, self.repository_url, str(path)], timeout=180)
            return path

        if not (path / '.git').exists():
            raise SyncError(f'The synchronization directory is not a Git repository: {path}')

        remote = self._run_git(['remote', 'get-url', 'origin'], cwd=path, check=False)
        if remote.returncode != 0:
            self._run_git(['remote', 'add', 'origin', self.repository_url], cwd=path)
        elif remote.stdout.strip() != self.repository_url:
            self._run_git(['remote', 'set-url', 'origin', self.repository_url], cwd=path)

        current_branch = self._run_git(['branch', '--show-current'], cwd=path).stdout.strip()
        if current_branch != branch:
            status = self._run_git(['status', '--porcelain', '--untracked-files=all'], cwd=path).stdout.strip()
            if status:
                raise SyncError('The local synchronization repository has uncommitted changes.')
            self._run_git(['fetch', '--quiet', 'origin', branch], cwd=path)
            self._run_git(['checkout', '-B', branch, f'origin/{branch}'], cwd=path)
        return path

    def _prepare_repository(self, path, branch, remote_commit, operation):
        status = self._run_git(['status', '--porcelain', '--untracked-files=all'], cwd=path).stdout.strip()
        if status:
            raise SyncError('The local synchronization repository has uncommitted changes.')

        local_commit = self._local_commit(path)
        if local_commit == remote_commit:
            return local_commit

        if not remote_commit:
            if local_commit and operation == 'pull':
                raise SyncError('The local synchronization branch has unpushed changes.')
            return local_commit

        self._run_git(['fetch', '--quiet', 'origin', branch], cwd=path)
        counts = self._run_git(
            ['rev-list', '--left-right', '--count', f'HEAD...origin/{branch}'], cwd=path
        ).stdout.split()
        try:
            ahead, behind = int(counts[0]), int(counts[1])
        except (IndexError, ValueError) as error:
            raise SyncError('Could not compare the local and remote synchronization branches.') from error

        if ahead and behind:
            raise SyncError('The local and remote synchronization branches have diverged.')
        if behind:
            self._run_git(['merge', '--ff-only', f'origin/{branch}'], cwd=path)
            return remote_commit
        if ahead and operation == 'pull':
            raise SyncError('The local synchronization branch has unpushed changes.')
        return local_commit

    def _local_commit(self, path):
        """Return HEAD or an empty string for an unborn initial branch."""
        result = self._run_git(['rev-parse', 'HEAD'], cwd=path, check=False)
        return result.stdout.strip() if result.returncode == 0 else ''

    def _commit_and_push(self, path, branch, message):
        self._run_git(['add', '--', SYNC_FILE_NAME], cwd=path)
        self._run_git([
            '-c', 'user.name=WhisperWriter',
            '-c', 'user.email=whisper-writer@localhost',
            'commit', '-m', message,
        ], cwd=path)
        self._run_git(['push', 'origin', branch], cwd=path, timeout=180)

    def push(self, config):
        areas = selected_areas(self.settings)
        if not areas:
            return {'action': 'push', 'changed': False, 'remote_commit': ''}
        info = self._remote_info()
        branch = self._selected_branch(info)
        path = self._ensure_repository(branch, empty_remote=info.get('empty', False))
        local_commit_before = self._local_commit(path)
        sync_path = path / SYNC_FILE_NAME
        local_existing = _read_sync_payload(str(sync_path))
        update = export_selected_areas(config, self.settings)['areas']
        local_payload_changed = _payload_with_updates(local_existing, update) != local_existing
        self._prepare_repository(path, branch, info['head_commit'], 'push')

        existing = _read_sync_payload(str(sync_path))
        if local_payload_changed:
            candidate = _payload_with_updates(existing, update)
            _write_sync_payload(str(sync_path), candidate)
            self._commit_and_push(path, branch, 'Update WhisperWriter synchronized settings')
        else:
            # A previous push may have committed successfully but lost network access before
            # the remote accepted it.  Push an existing local commit, without rewriting files.
            local_commit = self._local_commit(path)
            if local_commit != info['head_commit']:
                self._run_git(['push', 'origin', branch], cwd=path, timeout=180)
                candidate = existing
            elif local_commit_before != info['head_commit']:
                raise SyncError('Remote synchronized settings changed; pull before pushing.')
            else:
                candidate = existing

        remote_commit = self._remote_info()['head_commit']
        return {
            'action': 'push',
            'changed': local_payload_changed,
            'remote_commit': remote_commit,
        }

    def pull(self, config):
        areas = selected_areas(self.settings)
        if not areas:
            return {'action': 'pull', 'changed': False, 'config': copy.deepcopy(config), 'remote_commit': ''}
        info = self._remote_info()
        branch = self._selected_branch(info)
        path = self._ensure_repository(branch, empty_remote=info.get('empty', False))
        self._prepare_repository(path, branch, info['head_commit'], 'pull')
        payload = _read_sync_payload(str(path / SYNC_FILE_NAME))
        updated_config = apply_selected_areas(config, payload, self.settings)
        return {
            'action': 'pull',
            'changed': updated_config != config,
            'config': updated_config,
            'remote_commit': info['head_commit'],
        }

    def auto_sync(self, config, last_remote_commit=''):
        """Push local selected changes or apply remote changes during an interval tick."""
        areas = selected_areas(self.settings)
        if not areas:
            return {'action': 'auto', 'changed': False, 'remote_commit': ''}

        info = self._remote_info()
        branch = self._selected_branch(info)
        path = self._ensure_repository(branch, empty_remote=info.get('empty', False))
        local_commit_before = self._local_commit(path)
        sync_path = path / SYNC_FILE_NAME
        local_existing = _read_sync_payload(str(sync_path))
        current_payload = export_selected_areas(config, self.settings)['areas']
        # Compare the application against the last local sync snapshot before fetching.  Once
        # the remote branch is fast-forwarded, that file may contain another computer's
        # values, which must be treated as a remote change rather than a local edit.
        local_payload_changed = _payload_with_updates(local_existing, current_payload) != local_existing
        self._prepare_repository(path, branch, info['head_commit'], 'auto')

        existing = _read_sync_payload(str(sync_path))

        # A previous commit may have succeeded locally but failed while pushing.  The
        # interval should retry that push rather than treating the local commit as a remote
        # pull, and it must not create a second empty commit.
        local_commit_after = self._local_commit(path)
        if (
            not local_payload_changed
            and local_commit_before != info['head_commit']
            and local_commit_after != info['head_commit']
        ):
            self._run_git(['push', 'origin', branch], cwd=path, timeout=180)
            return {
                'action': 'push',
                'changed': False,
                'remote_commit': self._remote_info()['head_commit'],
            }

        if local_payload_changed:
            candidate = _payload_with_updates(existing, current_payload)
            _write_sync_payload(str(sync_path), candidate)
            self._commit_and_push(path, branch, 'Update WhisperWriter synchronized settings')
            return {
                'action': 'push',
                'changed': True,
                'remote_commit': self._remote_info()['head_commit'],
            }

        # The managed clone is updated by _prepare_repository, so its pre-fetch HEAD is the
        # durable local snapshot. Relying on the status file here would repeat a no-op pull
        # forever when a remote commit changed only an area disabled on this machine.
        remote_changed = local_commit_before != info['head_commit']
        if not remote_changed:
            return {'action': 'auto', 'changed': False, 'remote_commit': info['head_commit']}

        payload = _read_sync_payload(str(sync_path))
        updated_config = apply_selected_areas(config, payload, self.settings)
        return {
            'action': 'pull',
            'changed': updated_config != config,
            'config': updated_config,
            'remote_commit': info['head_commit'],
        }


class SyncWorker(QThread):
    """Run one synchronization operation without blocking the Qt GUI thread."""

    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, action, settings, config, last_remote_commit='', parent=None):
        super().__init__(parent)
        self.action = action
        self.settings = copy.deepcopy(settings)
        self.config = copy.deepcopy(config)
        self.last_remote_commit = last_remote_commit

    def run(self):
        try:
            manager = ConfigSyncManager(self.settings)
            if self.action == 'test':
                result = manager.test_connection()
            elif self.action == 'push':
                result = manager.push(self.config)
            elif self.action == 'pull':
                result = manager.pull(self.config)
            elif self.action == 'auto':
                result = manager.auto_sync(self.config, self.last_remote_commit)
            else:
                raise SyncError(f'Unknown synchronization operation: {self.action}')
            self.completed.emit(result)
        except Exception as error:  # Keep every expected/third-party failure in the status UI.
            self.failed.emit(str(error))
