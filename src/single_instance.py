import fcntl
import os
import time


class SingleInstanceLock:
    """
    Exclusive-lock guard so only one WhisperWriter process runs at a time.

    Uses flock() on a file under ~/.cache/whisper-writer/ rather than a PID file — a PID
    file needs its own staleness checks (process crashed, PID since reused by something
    else); flock is released by the kernel the instant a process exits for any reason,
    crash included, so there's nothing that can go stale.
    """

    def __init__(self, lock_path=None):
        self.lock_path = lock_path or os.path.join(
            os.path.expanduser('~/.cache/whisper-writer'), 'instance.lock'
        )
        self._fh = None

    def acquire(self, retries=15, delay=0.2):
        """
        Try to acquire the lock, retrying briefly (3s by default). The retry window exists
        for WhisperWriterApp.restart_app(): it starts the new process via
        QProcess.startDetached() before the old process has actually exited (that only
        happens once QApplication.quit() unwinds app.exec_() back in run()), so the new
        process can briefly race the old one for the lock on a settings-triggered restart.
        """
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        self._fh = open(self.lock_path, 'w')
        for attempt in range(retries):
            try:
                fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                if attempt == retries - 1:
                    return False
                time.sleep(delay)
        return False
