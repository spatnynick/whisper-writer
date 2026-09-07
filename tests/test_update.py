import os
from pathlib import Path
import subprocess
import tempfile
import unittest


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
            self.assertEqual((root / 'calls').read_text().splitlines(), ['-m pip install -q -r requirements.txt', '-m pip check'])
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
            self.assertEqual((root / 'calls').read_text().splitlines(), ['-m pip install -q -r requirements.txt', '-m pip check'])

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

if __name__ == '__main__':
    unittest.main()
