"""Status-line hook, installer and Claude file-fallback tests (offline)."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NOW = time.time()


def run_hook(env, payload):
    return subprocess.run([sys.executable, str(ROOT / "claude_statusline.py")],
                          input=json.dumps(payload).encode(), capture_output=True, env=env, timeout=20)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.appdata = Path(tempfile.mkdtemp())
        self.env = dict(os.environ, APPDATA=str(self.appdata))
        self.status = self.appdata / "UsageTray" / "claude_status.json"

    def rl(self, five, week, r5=NOW + 3600, r7=NOW + 86400 * 3):
        return {"rate_limits": {"five_hour": {"used_percentage": five, "resets_at": r5},
                                "seven_day": {"used_percentage": week, "resets_at": r7}}}

    def test_writes_and_prints(self):
        r = run_hook(self.env, dict(self.rl(23.5, 41.2), model={"display_name": "Opus"}))
        self.assertEqual(r.returncode, 0)
        self.assertIn("5h 24%", r.stdout.decode())
        data = json.loads(self.status.read_text())
        self.assertEqual(data["rate_limits"]["five_hour"]["used_percentage"], 23.5)

    def test_merge_keeps_highest_in_same_window_and_resets_on_new_window(self):
        run_hook(self.env, self.rl(40, 20))
        run_hook(self.env, self.rl(10, 5))  # an idle session with older numbers
        d = json.loads(self.status.read_text())["rate_limits"]
        self.assertEqual(d["five_hour"]["used_percentage"], 40)
        self.assertEqual(d["seven_day"]["used_percentage"], 20)
        run_hook(self.env, self.rl(3, 21, r5=NOW + 3600 * 5))  # new 5h window
        d = json.loads(self.status.read_text())["rate_limits"]
        self.assertEqual(d["five_hour"]["used_percentage"], 3)
        self.assertEqual(d["seven_day"]["used_percentage"], 21)

    def test_iso_reset_times(self):
        iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        run_hook(self.env, {"rate_limits": {"five_hour": {"used_percentage": 42, "resets_at": iso}}})
        d = json.loads(self.status.read_text())["rate_limits"]
        self.assertAlmostEqual(d["five_hour"]["resets_at"], NOW + 7200, delta=60)

    def test_no_rate_limits_and_garbage_input(self):
        r = run_hook(self.env, {"model": {}})
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.status.exists())
        r = subprocess.run([sys.executable, str(ROOT / "claude_statusline.py")],
                           input=b"not json", capture_output=True, env=self.env, timeout=20)
        self.assertEqual(r.returncode, 0)

    def test_chains_previous_status_line(self):
        (self.appdata / "UsageTray").mkdir(parents=True, exist_ok=True)
        (self.appdata / "UsageTray" / "statusline_chain.json").write_text(
            json.dumps({"command": f'"{sys.executable}" -c "import sys;d=sys.stdin.read();print(\'MINE\', len(d)>0)"'}))
        r = run_hook(self.env, self.rl(1, 2))
        self.assertIn("MINE True", r.stdout.decode())
        self.assertTrue(self.status.exists())


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.cdir = Path(tempfile.mkdtemp())
        self.appdata = Path(tempfile.mkdtemp())
        self.env = dict(os.environ, APPDATA=str(self.appdata), CLAUDE_CONFIG_DIR=str(self.cdir))

    def inst(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "install_claude_statusline.py"), *args],
                              capture_output=True, env=self.env, timeout=20, text=True)

    def test_install_keeps_and_restores_existing(self):
        (self.cdir / "settings.json").write_text(json.dumps(
            {"model": "opus", "statusLine": {"type": "command", "command": "~/mine.sh"}}))
        r = self.inst()
        self.assertEqual(r.returncode, 0, r.stderr)
        s = json.loads((self.cdir / "settings.json").read_text())
        self.assertIn("claude_statusline.py", s["statusLine"]["command"])
        self.assertEqual(s["model"], "opus")
        self.assertTrue((self.cdir / "settings.json.usagetray-backup").exists())
        chain = json.loads((self.appdata / "UsageTray" / "statusline_chain.json").read_text())
        self.assertEqual(chain["command"], "~/mine.sh")
        self.inst()  # idempotent: must not chain to itself
        chain = json.loads((self.appdata / "UsageTray" / "statusline_chain.json").read_text())
        self.assertEqual(chain["command"], "~/mine.sh")
        self.inst("--remove")
        s = json.loads((self.cdir / "settings.json").read_text())
        self.assertEqual(s["statusLine"]["command"], "~/mine.sh")

    def test_install_fresh_and_remove(self):
        self.assertEqual(self.inst().returncode, 0)
        self.assertIn("statusLine", json.loads((self.cdir / "settings.json").read_text()))
        self.inst("--remove")
        self.assertNotIn("statusLine", json.loads((self.cdir / "settings.json").read_text()))


class FallbackTests(unittest.TestCase):
    """Matches the real Windows case: .credentials.json with blank tokens."""

    def setUp(self):
        self.appdata = Path(tempfile.mkdtemp())
        os.environ["APPDATA"] = str(self.appdata)
        self.cdir = Path(tempfile.mkdtemp())
        (self.cdir / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "", "refreshToken": "", "expiresAt": 0, "subscriptionType": "pro"}}))
        from usagetray.providers.claude import ClaudeProvider
        self.status = self.appdata / "claude_status.json"
        self.p = ClaudeProvider(self.cdir, self.status)

    def test_blank_token_no_hook(self):
        from usagetray.model import ERROR
        s = self.p.fetch()
        self.assertEqual(s.status, ERROR)
        self.assertIn("Log in to Claude Code", s.detail)

    def test_blank_token_with_status_file(self):
        from usagetray.model import OK
        self.status.write_text(json.dumps({"captured_at": NOW - 300, "rate_limits": {
            "five_hour": {"used_percentage": 42.0, "resets_at": NOW + 3600},
            "seven_day": {"used_percentage": 18.0, "resets_at": NOW + 86400}}}))
        s = self.p.fetch()
        self.assertEqual(s.status, OK)
        self.assertIn("Not live", s.warning)
        self.assertEqual(s.plan, "pro")
        self.assertEqual((s.primary().used_pct, s.weekly().used_pct), (42.0, 18.0))
        self.assertEqual(s.primary().label, "5-hour")
        self.assertAlmostEqual(s.as_of.timestamp(), NOW - 300, delta=1)
        self.assertIsNone(self.p.fetch())          # unchanged file -> no update
        self.assertIsNotNone(self.p.fetch(force=True))


if __name__ == "__main__":
    unittest.main()
