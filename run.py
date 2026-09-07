import os
import sys

print('Starting WhisperWriter...')
os.execv(sys.executable, [sys.executable, os.path.join('src', 'main.py')] + sys.argv[1:])
