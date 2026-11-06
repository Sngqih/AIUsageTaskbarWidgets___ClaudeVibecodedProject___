"""The packaged-build self-check (Windows only: the tray backend needs a desktop)."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "win32", "tray backend only available on Windows")
class SmokeTests(unittest.TestCase):
    def test_smoke_test_passes(self):
        r = subprocess.run([sys.executable, str(ROOT / "main.py"), "--smoke-test"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
