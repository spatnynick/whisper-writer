import os
import sys
from dotenv import load_dotenv

print('Starting WhisperWriter...')
load_dotenv()
os.execv(sys.executable, [sys.executable, os.path.join('src', 'main.py')] + sys.argv[1:])
