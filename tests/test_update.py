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

if __name__ == '__main__':
    unittest.main()
