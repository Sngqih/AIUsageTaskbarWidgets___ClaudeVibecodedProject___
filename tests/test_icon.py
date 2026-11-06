"""Icon drawing + hover text."""

import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from usagetray.icon import level_color, render_icon, tooltip  # noqa: E402
from usagetray.model import ERROR, OK, Snapshot, Window, now_utc  # noqa: E402


def snap(a=22, b=6, warning=""):
    n = now_utc()
    return Snapshot("claude", OK, [Window("five_hour", "5-hour", a, n + timedelta(hours=2, minutes=50), True),
                                   Window("seven_day", "Weekly", b, n + timedelta(days=5, hours=10))],
                    n, n, plan="pro", warning=warning)


class IconTests(unittest.TestCase):
    def test_sizes(self):
        for size in (16, 20, 24, 32, 40):
            for s in (snap(), snap(100, 100), snap(warning="x"), Snapshot("codex", ERROR)):
                img = render_icon(s, size=size)
                self.assertEqual(img.size, (size, size))
                self.assertIsNotNone(img.getbbox())

    def test_colours_go_green_to_red(self):
        g, r = level_color(10), level_color(97)
        self.assertGreater(g[1], g[0])        # green-ish
        self.assertGreater(r[0], r[1])        # red-ish
        self.assertNotEqual(level_color(10, light=True), g)

    def test_tooltip(self):
        t = tooltip(snap())
        self.assertIn("5-hour: 22% used, resets in 2h", t)
        self.assertIn("Weekly: 6% used, resets in 5d", t)
        self.assertLessEqual(len(tooltip(snap(warning="Not live: " + "x" * 200))), 127)


if __name__ == "__main__":
    unittest.main()


class PaceTests(unittest.TestCase):
    def test_even_pace_and_underuse(self):
        n = now_utc()
        wk = Window("seven_day", "Weekly", 6, n + timedelta(days=1, hours=21), False, 7 * 86400)
        self.assertAlmostEqual(wk.expected_pct(n), (1 - (45 / 168)) * 100, places=3)  # ~73%
        self.assertTrue(wk.underused(50, 2, n))
        self.assertFalse(Window("seven_day", "Weekly", 60, wk.resets_at, False, 7 * 86400).underused(50, 2, n))
        far = Window("seven_day", "Weekly", 6, n + timedelta(days=3), False, 7 * 86400)
        self.assertFalse(far.underused(50, 2, n))                     # more than 2 days left
        five = Window("five_hour", "5-hour", 1, n + timedelta(hours=1), True, 5 * 3600)
        self.assertFalse(five.underused(50, 2, n))                    # only weekly limits
        self.assertIsNone(Window("x", "x", 1, None).expected_pct(n))

    def test_bang_in_icon_and_tooltip(self):
        n = now_utc()
        s = Snapshot("codex", OK, [Window("primary", "5-hour", 0, n + timedelta(hours=4), True, 5 * 3600),
                                   Window("secondary", "Weekly", 6, n + timedelta(days=1, hours=21), False, 7 * 86400)],
                     n, n)
        self.assertIn("(!) Under 50% used with 1d 2", tooltip(s))
        with_bang = render_icon(s, size=24)
        s.windows[1].used_pct = 60
        without = render_icon(s, size=24)
        self.assertNotEqual(with_bang.tobytes(), without.tobytes())
