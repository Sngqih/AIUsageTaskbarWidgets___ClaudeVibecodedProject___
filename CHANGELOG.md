# Changelog

## 0.4.0 (2026-11-06)
- **Taskbar icons:** the numbers now show `%`.
- **Underuse warning:** a yellow `!` appears before the weekly number when that limit resets within 2 days and less than 50% is used. Both thresholds are configurable.
- **Panel:** each weekly bar has a grey "even pace" bar behind it, and the reset line shows the even-pace %.
- **Setup:** the Python environment moved to `%LOCALAPPDATA%\UsageTray\venv`, outside the project folder.
- **Fix:** a quoted `codex_command` setting now works on Windows.
- **Fix:** the Claude Code status line now always prints UTF-8. Before this, the `·` could appear garbled on Windows.
- **Repo:** added GitHub Actions for tests (Windows and Linux) and tag-triggered `.exe` releases.

## 0.3.0
- **Taskbar icons:** they show numbers instead of bars, coloured by how much is left, and are drawn at the exact tray icon size so they stay sharp.
- **Hover text:** spells out "X% used, resets in …" for each limit.

## 0.2.0
- **Live checks for both tools:** Claude through Anthropic's usage endpoint, and Codex through `codex app-server`'s `account/rateLimits/read`.
- **Instant updates:** the status line and session logs are merged in without ever undercounting.
- **Warnings:** an amber "not live" marker, and the bars go grey once the data is over 10 minutes old.

## 0.1.0
- **First version:** tray icons with bars, a details panel, Claude via its usage endpoint, Codex via session logs, battery-aware polling and Start with Windows.
