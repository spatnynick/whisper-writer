import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

# update.sh first checks that the venv interpreter still runs.
VENV_PROBE = '-c import sys; sys.exit(sys.version_info < (3, 10))'


class UpdateTests(unittest.TestCase):
    def test_supervised_update_installs_without_restarting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            (root / 'venv/bin').mkdir(parents=True)
            (root / 'bin').mkdir()
            (root / 'update.sh').write_text(Path('update.sh').read_text())
            scripts = {
                'bin/git': '#!/bin/sh\ncase "$1" in\nrev-parse) echo main;;\nstatus|fetch) exit 0;;\n*) exit 99;;\nesac\n',
                'bin/pgrep': '#!/bin/sh\ntouch restart-attempted\nexit 1\n',
                'venv/bin/python3': '#!/bin/sh\nprintf "%s\\n" "$*" >> calls\n',
            }
            for name, text in scripts.items():
                path = root / name
                path.write_text(text)
                path.chmod(0o755)
            result = subprocess.run(
                ['bash', str(root / 'update.sh'), '--no-restart'],
                env={**os.environ, 'PATH': str(root / 'bin') + ':' + os.environ['PATH']},
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'calls').read_text().splitlines(), [VENV_PROBE, '-m pip install -q -r requirements.txt', '-m pip check'])
            self.assertFalse((root / 'restart-attempted').exists())

    def test_real_git_check_rejects_dirty_ahead_and_divergent_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote, checkout = Path(tmp) / 'remote', Path(tmp) / 'checkout'
            remote.mkdir()
            def git(directory, *args):
                return subprocess.run(
                    ['git', '-c', 'user.name=Review Test', '-c', 'user.email=test@example.invalid', *args],
                    cwd=directory, capture_output=True, text=True, check=True, timeout=10,
                )
            git(remote, 'init', '-b', 'main')
            (remote / 'update.sh').write_text(Path('update.sh').read_text())
            git(remote, 'add', '.')
            git(remote, 'commit', '-m', 'initial')
            git(remote, 'clone', str(remote), str(checkout))
            def check(expected):
                result = subprocess.run(['bash', str(checkout / 'update.sh'), '--check-only'],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            check(0)
            (checkout / 'local.txt').write_text('local change')
            check(1)
            git(checkout, 'add', '.')
            git(checkout, 'commit', '-m', 'local ahead')
            check(1)
            (remote / 'remote.txt').write_text('remote change')
            git(remote, 'add', '.')
            git(remote, 'commit', '-m', 'remote diverged')
            check(1)

    def test_current_checkout_still_repairs_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            (root / 'venv/bin').mkdir(parents=True)
            (root / 'bin').mkdir()
            (root / 'update.sh').write_text(Path('update.sh').read_text())
            scripts = {
                'bin/git': '#!/bin/sh\ncase "$1" in\nrev-parse) echo main;;\nstatus|fetch) exit 0;;\n*) exit 99;;\nesac\n',
                'bin/pgrep': '#!/bin/sh\nexit 1\n',
                'venv/bin/python3': '#!/bin/sh\nprintf "%s\\n" "$*" >> calls\n',
            }
            for name, text in scripts.items():
                path = root / name
                path.write_text(text)
                path.chmod(0o755)
            result = subprocess.run(['bash', str(root / 'update.sh')], env={**os.environ, 'PATH': str(root / 'bin') + ':' + os.environ['PATH']}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Already up to date', result.stdout)
            self.assertEqual((root / 'calls').read_text().splitlines(), [VENV_PROBE, '-m pip install -q -r requirements.txt', '-m pip check'])

    def test_check_only_reports_current_without_installing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            (root / 'venv/bin').mkdir(parents=True)
            (root / 'bin').mkdir()
            (root / 'update.sh').write_text(Path('update.sh').read_text())
            scripts = {
                'bin/git': '#!/bin/sh\ncase "$1" in\nrev-parse) echo main;;\nstatus|fetch) exit 0;;\n*) exit 99;;\nesac\n',
            }
            for name, text in scripts.items():
                path = root / name
                path.write_text(text)
                path.chmod(0o755)
            result = subprocess.run(
                ['bash', str(root / 'update.sh'), '--check-only'],
                env={**os.environ, 'PATH': str(root / 'bin') + ':' + os.environ['PATH']},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('NO_UPDATE', result.stdout)

    def test_check_only_reports_new_remote_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').mkdir()
            (root / 'venv/bin').mkdir(parents=True)
            (root / 'bin').mkdir()
            (root / 'update.sh').write_text(Path('update.sh').read_text())
            git = root / 'bin/git'
            git.write_text('''#!/bin/sh
case "$1" in
rev-parse)
    if [ "$2" = "--abbrev-ref" ]; then echo main
    elif [ "$2" = "HEAD" ]; then echo local
    else echo remote
    fi
    ;;
status|fetch) exit 0 ;;
merge-base) exit 0 ;;
*) exit 99 ;;
esac
''')
            git.chmod(0o755)
            result = subprocess.run(
                ['bash', str(root / 'update.sh'), '--check-only'],
                env={**os.environ, 'PATH': str(root / 'bin') + ':' + os.environ['PATH']},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 10, result.stderr)
            self.assertIn('UPDATE_AVAILABLE', result.stdout)

class BranchSwitchTests(unittest.TestCase):
    """update.sh against real Git repositories: switching, rewritten and deleted branches."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.remote, self.checkout = self.tmp / 'remote', self.tmp / 'checkout'
        self.remote.mkdir()
        self.git(self.remote, 'init', '-b', 'main')
        (self.remote / 'update.sh').write_text(Path('update.sh').read_text())
        (self.remote / '.gitignore').write_text('venv/\nvenv.previous/\n')
        (self.remote / 'requirements.txt').write_text('pip\n')
        self.commit(self.remote, 'initial')
        self.git(self.remote, 'checkout', '-b', 'feature')
        (self.remote / 'feature.txt').write_text('feature 1')
        self.commit(self.remote, 'feature 1')
        self.git(self.remote, 'checkout', 'main')
        self.git(self.tmp, 'clone', '--single-branch', '-b', 'main', str(self.remote), str(self.checkout))
        # A venv whose interpreter works; update.sh only runs "-m pip ..." through it.
        (self.checkout / 'venv/bin').mkdir(parents=True)
        python = self.checkout / 'venv/bin/python3'
        python.write_text('#!/bin/sh\nexit 0\n')
        python.chmod(0o755)

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def git(directory, *args):
        return subprocess.run(
            ['git', '-c', 'user.name=Review Test', '-c', 'user.email=test@example.invalid', *args],
            cwd=directory, capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()

    def commit(self, directory, message):
        self.git(directory, 'add', '.')
        self.git(directory, 'commit', '-m', message)

    def update(self, *args, env=None):
        return subprocess.run(['bash', str(self.checkout / 'update.sh'), *args],
                              capture_output=True, text=True, timeout=120,
                              env={**os.environ, **(env or {})})

    def head(self, directory, ref='HEAD'):
        return self.git(directory, 'rev-parse', ref)

    def test_switch_follow_rewrite_detect_deletion_and_return_to_main(self):
        listed = self.update('--list-branches')
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(sorted(listed.stdout.split()), ['feature', 'main'])

        # Works although the single-branch clone's refspec does not cover "feature".
        switched = self.update('--switch', 'feature', '--no-restart')
        self.assertEqual(switched.returncode, 0, switched.stdout + switched.stderr)
        self.assertIn('SWITCHED', switched.stdout)
        self.assertEqual(self.git(self.checkout, 'rev-parse', '--abbrev-ref', 'HEAD'), 'feature')
        self.assertEqual(self.head(self.checkout), self.head(self.remote, 'feature'))

        # The branch is restarted from main and force-pushed: followed, nothing is lost.
        self.git(self.remote, 'checkout', 'feature')
        self.git(self.remote, 'reset', '--hard', 'main')
        (self.remote / 'feature.txt').write_text('feature 2')
        self.commit(self.remote, 'feature 2')
        self.git(self.remote, 'checkout', 'main')
        self.assertEqual(self.update('--check-only').returncode, 10)
        updated = self.update('--no-restart')
        self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)
        self.assertIn('rewritten', updated.stdout)
        self.assertEqual(self.head(self.checkout), self.head(self.remote, 'feature'))

        # Merged and deleted on GitHub: reported distinctly, then back to main.
        self.git(self.remote, 'branch', '-D', 'feature')
        gone = self.update('--check-only')
        self.assertEqual(gone.returncode, 11, gone.stdout + gone.stderr)
        self.assertIn('BRANCH_GONE', gone.stdout)
        back = self.update('--switch', 'main', '--no-restart')
        self.assertEqual(back.returncode, 0, back.stdout + back.stderr)
        self.assertEqual(self.git(self.checkout, 'rev-parse', '--abbrev-ref', 'HEAD'), 'main')
        self.assertEqual(self.update('--check-only').returncode, 0)

    def test_unpublished_local_commits_are_never_discarded(self):
        self.assertEqual(self.update('--switch', 'feature', '--no-restart').returncode, 0)
        (self.checkout / 'local.txt').write_text('only here')
        self.commit(self.checkout, 'local work')
        local_head = self.head(self.checkout)

        self.git(self.remote, 'checkout', 'feature')
        self.git(self.remote, 'reset', '--hard', 'main')
        (self.remote / 'feature.txt').write_text('rewritten')
        self.commit(self.remote, 'rewritten')
        self.git(self.remote, 'checkout', 'main')

        self.assertEqual(self.update('--check-only').returncode, 1)
        self.assertEqual(self.update('--no-restart').returncode, 1)
        self.assertEqual(self.update('--switch', 'main', '--no-restart').returncode, 0)
        refused = self.update('--switch', 'feature', '--no-restart')
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn('not on origin/feature', refused.stderr)
        self.assertEqual(self.head(self.checkout, 'refs/heads/feature'), local_head)

    def test_switch_rejects_bad_and_unknown_branches(self):
        bad = self.update('--switch', '../evil', '--no-restart')
        self.assertEqual(bad.returncode, 2)
        missing = self.update('--switch', 'does-not-exist', '--no-restart')
        self.assertEqual(missing.returncode, 11, missing.stdout + missing.stderr)
        self.assertEqual(self.git(self.checkout, 'rev-parse', '--abbrev-ref', 'HEAD'), 'main')
        self.assertEqual(self.update('--switch').returncode, 2)
        self.assertEqual(self.update('--check-only', '--switch', 'main').returncode, 2)

    def test_venv_with_missing_interpreter_is_rebuilt(self):
        python = self.checkout / 'venv/bin/python3'
        python.unlink()
        python.symlink_to('/nonexistent/python3.12')
        result = self.update('--no-restart', env={'WHISPER_WRITER_PYTHON': sys.executable})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Rebuilding venv', result.stdout)
        check = subprocess.run([str(self.checkout / 'venv/bin/python3'), '-c', 'print("ok")'],
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(check.stdout.strip(), 'ok')
        self.assertTrue((self.checkout / 'venv.previous').is_symlink() or (self.checkout / 'venv.previous').exists())
        self.assertEqual(self.git(self.checkout, 'status', '--porcelain'), '')

    def test_failed_rebuild_restores_the_previous_venv(self):
        failing = self.tmp / 'python-fail'
        failing.write_text('#!/bin/sh\ncase "$1" in -c) exit 0;; esac\nexit 1\n')
        failing.chmod(0o755)
        result = self.update('--rebuild-venv', '--no-restart', env={'WHISPER_WRITER_PYTHON': str(failing)})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('restoring the previous one', result.stderr)
        self.assertEqual((self.checkout / 'venv/bin/python3').read_text(), '#!/bin/sh\nexit 0\n')
        self.assertFalse((self.checkout / 'venv.previous').exists())


if __name__ == '__main__':
    unittest.main()
