"""OS locks and child lifecycle for this single-host scheduler."""
import os
import signal
import subprocess
import threading
import time
from pathlib import Path


class FileLock:
    def __init__(self, path):
        self.file = open(Path(path), 'a+b')
        self.locked = False

    def acquire(self):
        try:
            if os.name == 'posix':
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b'0')
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            self.locked = True
            return True
        except (BlockingIOError, OSError):
            return False

    def close(self):
        self.file.close()
        self.locked = False


def watch_parent(parent_pid):
    """A crashed API must not leave codecs/inference running in the background."""
    def watch():
        while True:
            if os.getppid() != parent_pid:
                if os.name == 'posix':
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                os._exit(1)
            time.sleep(.25)
    threading.Thread(target=watch, daemon=True).start()


def terminate_tree(process):
    # Also terminate orphan descendants after the leader has already exited.
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
    process.wait(timeout=10)
