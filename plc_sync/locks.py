"""Cross-platform advisory lock preventing duplicate long-running workers.

Windows uses msvcrt byte-range locks; POSIX uses fcntl.flock.  The lock file
remains on disk intentionally.  The kernel releases its lock on process exit.
"""
import os
from pathlib import Path

_WINDOWS = os.name == "nt"


def _acquire(handle):
    if _WINDOWS:
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(handle):
    if _WINDOWS:
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class SingleInstance:
    """Exclusive lock held for the worker's lifetime, released on exit/crash."""

    def __init__(self, database_path, kind):
        self.path = Path(str(database_path) + "." + kind + ".lock")
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            _acquire(self.handle)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError(
                f"Already running (or lock unavailable): {self.path.name}"
            ) from exc
        return self

    def __exit__(self, *args):
        if self.handle is not None:
            try:
                _release(self.handle)
            finally:
                self.handle.close()
                self.handle = None
