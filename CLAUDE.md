# Notes for working on UsageTray

UsageTray is a Windows tray app (Python 3.10+, pystray, Pillow, tkinter) that shows Claude and Codex usage limits. Users run it with `pythonw` from a venv in `%LOCALAPPDATA%\UsageTray\venv`, or as a PyInstaller .exe.

## Architecture
- **Threads:** tkinter owns the main thread. Each provider has a `Worker` thread (`poller.py`) and each tray icon runs pystray on its own thread. They talk to the UI only through `App.q`.
- **Tray icons on Windows** must be created *and* run on the same thread. See `tray.py`.
- **Data model** (`model.py`): `Snapshot` has a list of `Window`s, with `used_pct`, `resets_at` (UTC) and `window_s`. `merge_windows` is the "never undercount" rule: for the same reset time the highest % wins, and a later reset time replaces the old window.
- **Providers** subclass `HybridProvider` (`providers/base.py`). Each has a **live** source (network, every 1–2 min) and a **file** source (local, checked every 5 s). The `_compose()` method merges them.
  - If the live source fails, `Snapshot.warning` is set and the icon shows an amber stripe tip.
  - If the newest data is over 10 min old, the status becomes `STALE` and the numbers go grey.
- **Claude live source:** `GET https://api.anthropic.com/api/oauth/usage` with the `anthropic-beta: oauth-2025-04-20` header and the token from `~/.claude/.credentials.json`. Only **read** the token; never refresh it, because refreshing rotates it and can sign Claude Code out. Known Windows issue: Claude Code may leave that token blank or expired.
- **Claude file source:** `claude_statusline.py`, installed as Claude Code's `statusLine`, writes `%APPDATA%\UsageTray\claude_status.json`.
- **Codex live source:** spawns `codex app-server` with JSON-RPC over stdio. The sequence is `initialize`, then the `initialized` notification, then `account/rateLimits/read`. Fields are camelCase: `usedPercent`, `windowDurationMins`, `resetsAt` in Unix seconds.
  - Use `CREATE_NO_WINDOW`, and kill the process tree because `codex` is usually an npm `.cmd` shim.
- **Codex file source:** `token_count` events in `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`.
- **Icons** (`icon.py`) are rendered at `winutil.tray_icon_size()`. `tray.ExactSizeIcon` overrides pystray's private `_assert_icon_handle` so Windows doesn't rescale them. That's why `requirements.txt` pins pystray to 0.19.5.

## Conventions
- **Platform calls:** keep Windows-only calls in `winutil.py`, each with a harmless fallback, so tests run on Linux.
- **Tests:** every behaviour change gets an offline test in `tests/`. There's no real network or real Codex in tests; use the fake server and `tests/fake_codex.py`. Run `python -m unittest discover -s tests`.
- **Settings:** new settings go in `Config` (`config.py`). Old config files are rewritten automatically to list every key, and the README settings table needs updating too.
- **`.bat` files** must keep CRLF line endings (enforced by `.gitattributes`).
- **Releases:** bump `__version__` in `usagetray/__init__.py`, add a `CHANGELOG.md` entry, then run the release workflow (Actions → release → Run workflow, or push a `vX.Y.Z` tag). It checks the tag matches `__version__`, tests, builds the .exe, runs `UsageTray.exe --smoke-test`, then publishes. If you add a dynamically imported module, add it as a `--hidden-import` and to `usagetray/smoke.py`.
