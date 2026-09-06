import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class UpdateTests(unittest.TestCase):
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
