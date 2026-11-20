"""Cross-platform lock tests; fake the Windows locking API when on Linux."""
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from plc_sync import locks


class CrossPlatformLockTests(unittest.TestCase):
    def test_prevents_duplicate_worker_and_releases(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / 'edge.sqlite3')
            with locks.SingleInstance(db, 'collector'):
                with self.assertRaisesRegex(RuntimeError, 'Already running'):
                    with locks.SingleInstance(db, 'collector'):
                        pass
                # Collector and sync should be able to run simultaneously.
                with locks.SingleInstance(db, 'synchronizer'):
                    pass
            with locks.SingleInstance(db, 'collector'):
                pass

    def test_windows_branch_uses_msvcrt_nonblocking_lock_and_release(self):
        calls = []
        fake = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0,
                                     locking=lambda fd, mode, length: calls.append((mode, length)))
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(locks, '_WINDOWS', True), patch.dict('sys.modules', {'msvcrt': fake}):
                with locks.SingleInstance(str(Path(temp) / 'edge.sqlite3'), 'collector'):
                    pass
        self.assertEqual(calls, [(2, 1), (0, 1)])


if __name__ == '__main__':
    unittest.main()
