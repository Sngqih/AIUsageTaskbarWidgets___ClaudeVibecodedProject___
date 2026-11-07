"""Offline tests: fake Claude endpoint, fake `codex app-server`, fake logs.

    python -m unittest discover -s tests
"""

import http.server
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APPDATA", tempfile.mkdtemp())

from usagetray.model import ERROR, OK, STALE, Window, merge_windows  # noqa: E402

NOW = datetime.now(timezone.utc)
FAKE_CODEX = f'"{sys.executable}" "{ROOT / "tests" / "fake_codex.py"}"'

CLAUDE_BODY = {
    "five_hour": {"utilization": 42.0, "resets_at": (NOW + timedelta(hours=2)).isoformat()},
    "seven_day": {"utilization": 18.0, "resets_at": (NOW + timedelta(days=3)).isoformat()},
    "seven_day_opus": None,
    "seven_day_sonnet": {"utilization": 5.0, "resets_at": (NOW + timedelta(days=4)).isoformat()},
    "seven_day_fable": {"utilization": 9.0, "resets_at": None},
    "seven_day_oauth_apps": None,
    "iguana_necktie": None,
    "extra_usage": {"is_enabled": False, "monthly_limit": None},
}


class FakeHandler(http.server.BaseHTTPRequestHandler):
    mode = "ok"
    seen_headers = {}

    def do_GET(self):  # noqa: N802
        FakeHandler.seen_headers = {k.lower(): v for k, v in self.headers.items()}
        if FakeHandler.mode == "401":
            self.send_response(401); self.end_headers(); return
        if FakeHandler.mode == "429":
            self.send_response(429); self.send_header("Retry-After", "30"); self.end_headers(); return
        data = json.dumps(CLAUDE_BODY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class MergeTests(unittest.TestCase):
    def test_never_undercounts(self):
        r = NOW + timedelta(hours=2)
        api = [Window("five_hour", "5-hour", 40, r, True), Window("seven_day_sonnet", "S", 5, r)]
        stale_file = [Window("five_hour", "5-hour", 30, r, True)]           # older reading, same window
        self.assertEqual(merge_windows(api, stale_file)[0].used_pct, 40)
        newer_file = [Window("five_hour", "5-hour", 55, r + timedelta(seconds=20), True)]
        self.assertEqual(merge_windows(api, newer_file)[0].used_pct, 55)
        new_window = [Window("five_hour", "5-hour", 2, r + timedelta(hours=5), True)]
        self.assertEqual(merge_windows(api, new_window)[0].used_pct, 2)    # reset happened
        self.assertEqual([w.key for w in merge_windows(api, stale_file)], ["five_hour", "seven_day_sonnet"])


class ClaudeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), FakeHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        os.environ["USAGETRAY_CLAUDE_ENDPOINT"] = f"http://127.0.0.1:{cls.server.server_port}/u"
        import usagetray.providers.claude as c
        importlib.reload(c)
        cls.mod = c

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def make(self, creds=True, expired=False, token="sk-ant-oat01-TEST", status=None):
        d = Path(tempfile.mkdtemp())
        if creds:
            exp = (time.time() + (-60 if expired else 3600)) * 1000
            (d / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
                "accessToken": token, "expiresAt": exp, "subscriptionType": "max"}}))
        sf = d / "claude_status.json"
        if status is not None:
            sf.write_text(json.dumps(status))
        return self.mod.ClaudeProvider(d, sf)

    def test_live_ok(self):
        FakeHandler.mode = "ok"
        p = self.make()
        s = p.fetch()
        self.assertEqual(s.status, OK)
        self.assertTrue(p.live_ok())
        self.assertEqual(s.warning, "")
        self.assertEqual(s.plan, "max")
        self.assertEqual([w.key for w in s.windows][:2], ["five_hour", "seven_day"])
        self.assertEqual((s.primary().used_pct, s.weekly().used_pct), (42.0, 18.0))
        self.assertIn("Weekly · Fable", [w.label for w in s.windows])
        self.assertEqual(FakeHandler.seen_headers.get("anthropic-beta"), "oauth-2025-04-20")
        self.assertEqual(FakeHandler.seen_headers.get("authorization"), "Bearer sk-ant-oat01-TEST")
        self.assertIsNone(p.fetch())  # nothing due, nothing changed

    def test_live_plus_newer_status_line(self):
        FakeHandler.mode = "ok"
        reset = (NOW + timedelta(hours=2)).timestamp()
        p = self.make(status={"captured_at": time.time(), "rate_limits": {
            "five_hour": {"used_percentage": 47.0, "resets_at": reset},     # newer, higher
            "seven_day": {"used_percentage": 10.0, "resets_at": (NOW + timedelta(days=3)).timestamp()}}})
        s = p.fetch()
        self.assertEqual(s.primary().used_pct, 47.0)
        self.assertEqual(s.weekly().used_pct, 18.0)   # status line lower -> API value kept
        self.assertTrue(any(w.key == "seven_day_sonnet" for w in s.windows))

    def test_blank_token(self):
        s = self.make(token="").fetch()
        self.assertEqual(s.status, ERROR)
        self.assertIn("Log in to Claude Code", s.detail)

    def test_blank_token_with_recent_status_line_warns(self):
        p = self.make(token="", status={"captured_at": time.time() - 120, "rate_limits": {
            "five_hour": {"used_percentage": 12, "resets_at": time.time() + 3000}}})
        s = p.fetch()
        self.assertEqual(s.status, OK)
        self.assertIn("Not live", s.warning)
        self.assertIn("Log in to Claude Code", s.warning)

    def test_old_status_line_without_live_is_stale(self):
        p = self.make(token="", status={"captured_at": time.time() - 3600, "rate_limits": {
            "five_hour": {"used_percentage": 12, "resets_at": time.time() + 3000}}})
        self.assertEqual(p.fetch().status, STALE)

    def test_no_creds(self):
        s = self.make(creds=False).fetch()
        self.assertEqual(s.status, ERROR)

    def test_expired(self):
        FakeHandler.mode = "401"
        s = self.make(expired=True).fetch()
        self.assertEqual(s.status, ERROR)
        self.assertIn("login expired", s.detail)
        self.assertIn("Log in to Claude Code", s.detail)

    def test_expired_token_is_not_sent(self):
        FakeHandler.mode = "ok"
        FakeHandler.seen_headers = {}
        p = self.make(expired=True)
        s = p.fetch()
        self.assertEqual(FakeHandler.seen_headers, {})       # no request made
        self.assertIn("login expired", s.detail)
        self.assertGreaterEqual(p._next_live - time.monotonic(), 25 * 60)

    def test_recovers_when_login_saved(self):
        FakeHandler.mode = "ok"
        p = self.make(expired=True)
        self.assertEqual(p.fetch().status, ERROR)
        self.assertIsNone(p.fetch())                         # waiting, nothing changed
        # Claude Code saves a fresh login -> picked up on the very next tick
        creds = p.credentials_path
        creds.write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "sk-ant-oat01-NEW", "expiresAt": (time.time() + 3600) * 1000,
            "subscriptionType": "max"}}))
        os.utime(creds, (time.time() + 5, time.time() + 5))
        s = p.fetch()
        self.assertEqual(s.status, OK)
        self.assertTrue(p.live_ok())
        self.assertEqual(s.warning, "")
        self.assertEqual(FakeHandler.seen_headers.get("authorization"), "Bearer sk-ant-oat01-NEW")

    def test_rejected_token_waits_for_new_login(self):
        FakeHandler.mode = "401"
        p = self.make()
        s = p.fetch()
        self.assertIn("didn't accept", s.detail)
        self.assertGreaterEqual(p._next_live - time.monotonic(), 25 * 60)
        FakeHandler.mode = "ok"

    def test_expired_with_status_line_tooltip(self):
        from usagetray.icon import tooltip
        p = self.make(expired=True, status={"captured_at": time.time() - 60, "rate_limits": {
            "five_hour": {"used_percentage": 12, "resets_at": time.time() + 3000}}})
        s = p.fetch()
        self.assertEqual(s.status, OK)
        self.assertTrue(s.warning.startswith("Not live: Claude Code's login expired"))
        self.assertIn("(!) Not live: Claude Code's login expired", tooltip(s))

    def test_429_backoff(self):
        FakeHandler.mode = "429"
        p = self.make()
        p.fetch()
        self.assertGreaterEqual(p._next_live - time.monotonic(), 100)  # max(interval, 30s)
        FakeHandler.mode = "ok"

    def test_offline(self):
        os.environ["USAGETRAY_CLAUDE_ENDPOINT"] = "http://127.0.0.1:9/nothing"
        importlib.reload(self.mod)
        s = self.make().fetch()
        self.assertIn("offline", s.detail)
        os.environ["USAGETRAY_CLAUDE_ENDPOINT"] = f"http://127.0.0.1:{self.server.server_port}/u"
        importlib.reload(self.mod)


def codex_event(ts, rl):
    return json.dumps({"timestamp": ts.isoformat().replace("+00:00", "Z"), "type": "event_msg",
                       "payload": {"type": "token_count", "info": None, "rate_limits": rl}})


class CodexLiveTests(unittest.TestCase):
    def setUp(self):
        from usagetray.providers import codex
        self.codex = codex
        self.home = Path(tempfile.mkdtemp())

    def run_mode(self, mode, timeout=10):
        os.environ["FAKE_CODEX_MODE"] = mode
        cmd = [sys.executable, str(ROOT / "tests" / "fake_codex.py")]
        return self.codex.app_server_rate_limits(cmd, self.home, timeout=timeout)

    def test_ok(self):
        os.environ["FAKE_CODEX_MODE"] = "ok"
        p = self.codex.CodexProvider(self.home, command=FAKE_CODEX)
        s = p.fetch()
        self.assertTrue(p.live_ok(), s.warning)
        self.assertEqual(s.status, OK)
        self.assertEqual((s.primary().used_pct, s.weekly().used_pct), (37, 64))
        self.assertEqual((s.primary().label, s.weekly().label), ("5-hour", "Weekly"))
        self.assertEqual(s.plan, "plus")
        self.assertAlmostEqual(s.primary().resets_at.timestamp(), time.time() + 4000, delta=30)

    def test_empty_first_answer_retried(self):
        res, err = self.run_mode("empty1")
        self.assertEqual(err, "")
        self.assertEqual(res["rateLimits"]["primary"]["usedPercent"], 37)

    def test_error_and_apikey_and_hang(self):
        self.assertIn("not logged in", self.run_mode("error")[1])
        self.assertIn("API key", self.run_mode("apikey")[1])
        t = time.monotonic()
        res, err = self.run_mode("hang", timeout=2)
        self.assertIsNone(res)
        self.assertIn("in time", err)
        self.assertLess(time.monotonic() - t, 10)

    def test_live_beats_older_logs(self):
        os.environ["FAKE_CODEX_MODE"] = "ok"
        d = date.today()
        day = self.home / "sessions" / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
        day.mkdir(parents=True)
        reset = int(time.time()) + 4000
        (day / "rollout-z.jsonl").write_text(codex_event(NOW - timedelta(hours=1), {
            "primary": {"used_percent": 20.0, "window_minutes": 300, "resets_at": reset}}) + "\n")
        p = self.codex.CodexProvider(self.home, command=FAKE_CODEX)
        self.assertEqual(p.fetch().primary().used_pct, 37)


class CodexLogTests(unittest.TestCase):
    """Log-file reading (used for instant updates and as the fallback)."""

    def setUp(self):
        from usagetray.providers.codex import CodexProvider
        self.home = Path(tempfile.mkdtemp())
        d = date.today()
        self.day = self.home / "sessions" / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
        self.day.mkdir(parents=True)
        self.p = CodexProvider(self.home, command="definitely-not-a-real-codex-binary")

    def write(self, name, lines):
        f = self.day / name
        f.write_text("\n".join(lines) + "\n")
        return f

    def test_new_format_change_detection_and_not_live_warning(self):
        rl = {"primary": {"used_percent": 12.5, "window_minutes": 300,
                          "resets_at": int((NOW + timedelta(hours=3)).timestamp())},
              "secondary": {"used_percent": 61.0, "window_minutes": 10080,
                            "resets_at": int((NOW + timedelta(days=2)).timestamp())},
              "plan_type": "plus"}
        filler = [json.dumps({"type": "response_item", "payload": {"text": "x" * 5000}})] * 200
        older = dict(rl, primary=dict(rl["primary"], used_percent=1.0))
        self.write("rollout-a.jsonl", [codex_event(NOW - timedelta(minutes=5), older)]
                   + filler + [codex_event(NOW, rl)] + filler)
        s = self.p.fetch()
        self.assertEqual(s.status, OK)
        self.assertIn("Not live", s.warning)
        self.assertEqual(s.plan, "plus")
        self.assertEqual(s.primary().used_pct, 12.5)
        self.assertEqual(s.weekly().used_pct, 61.0)
        self.assertIsNone(self.p.fetch())
        time.sleep(0.01)
        with open(self.day / "rollout-a.jsonl", "a") as f:
            f.write(codex_event(NOW, dict(rl, primary=dict(rl["primary"], used_percent=20))) + "\n")
        self.assertEqual(self.p.fetch().primary().used_pct, 20)

    def test_old_relative_format_reset_and_stale(self):
        ts = NOW - timedelta(hours=6)
        rl = {"primary": {"used_percent": 80.0, "window_minutes": 299, "resets_in_seconds": 3600},
              "secondary": {"used_percent": 22.0, "window_minutes": 10079, "resets_in_seconds": 351406}}
        self.write("rollout-b.jsonl", [codex_event(ts, rl)])
        s = self.p.fetch()
        self.assertEqual(s.status, STALE)               # 6h old and not live
        self.assertEqual(s.primary().effective_pct(), 0.0)
        self.assertEqual(s.weekly().effective_pct(), 22.0)

    def test_flat_legacy(self):
        rl = {"primary_used_percent": 7.0, "primary_window_minutes": 300,
              "primary_resets_in_seconds": 100, "secondary_used_percent": 3.0}
        self.write("rollout-c.jsonl", [codex_event(NOW, rl)])
        self.assertEqual([w.used_pct for w in self.p.fetch().windows], [7.0, 3.0])

    def test_partial_last_line(self):
        rl = {"primary": {"used_percent": 9.0, "window_minutes": 300}}
        f = self.write("rollout-e.jsonl", [codex_event(NOW, rl)])
        with open(f, "a") as fh:
            fh.write('{"timestamp":"x","type":"event_msg","payload":{"type":"token_count","rate_limits":{"prim')
        self.assertEqual(self.p.fetch().primary().used_pct, 9.0)

    def test_nothing_anywhere(self):
        from usagetray.providers.codex import CodexProvider
        s = CodexProvider(self.home / "nope", command="definitely-not-a-real-codex-binary").fetch()
        self.assertEqual(s.status, ERROR)


class ModelTests(unittest.TestCase):
    def test_roundtrip(self):
        from usagetray.model import Snapshot
        s = Snapshot("claude", OK, [Window("five_hour", "5-hour", 4.0, NOW, True)], NOW, NOW, "d", "pro", "w")
        s2 = Snapshot.from_json(json.loads(json.dumps(s.to_json())))
        self.assertEqual(s2.windows[0].resets_at, NOW)
        self.assertEqual((s2.plan, s2.warning), ("pro", "w"))


class CodexCommandTests(unittest.TestCase):
    def test_quoted_windows_path_is_unquoted(self):
        from usagetray.providers import codex
        old = codex.winutil.IS_WIN
        try:
            codex.winutil.IS_WIN = True
            p = codex.CodexProvider(Path("."), command='"C:\\Program Files\\nodejs\\codex.cmd"')
            self.assertEqual(p.codex_command(), ["C:\\Program Files\\nodejs\\codex.cmd"])
            codex.winutil.IS_WIN = False
            p = codex.CodexProvider(Path("."), command='"/opt/my codex/codex" --flag')
            self.assertEqual(p.codex_command(), ["/opt/my codex/codex", "--flag"])
        finally:
            codex.winutil.IS_WIN = old


if __name__ == "__main__":
    unittest.main()
