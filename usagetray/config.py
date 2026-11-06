"""Settings, stored as JSON in %APPDATA%\\UsageTray\\config.json."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

from . import APP_NAME

log = logging.getLogger(__name__)


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Config:
    # Live checks (need internet), in seconds on mains power.
    claude_live_interval_s: int = 120   # Anthropic usage endpoint
    codex_live_interval_s: int = 60     # `codex app-server` rate-limit request
    # How often to look for instant updates from Claude Code's status line and
    # Codex's session logs (cheap file checks, no network).
    file_check_interval_s: int = 5
    # All intervals are multiplied by this when the laptop is on battery.
    battery_multiplier: float = 2.0

    warn_pct: float = 80.0
    crit_pct: float = 95.0
    # Show "!" when a weekly limit resets within this many days and you've
    # used less than this much of it.
    underuse_pct: float = 50.0
    underuse_days: float = 2.0
    notify: bool = True

    show_claude: bool = True
    show_codex: bool = True

    # Override data locations (leave null to auto-detect).
    claude_config_dir: Optional[str] = None  # default: %CLAUDE_CONFIG_DIR% or ~/.claude
    codex_home: Optional[str] = None         # default: %CODEX_HOME% or ~/.codex
    # Command used to start Codex (default: "codex" found on PATH).
    codex_command: Optional[str] = None

    @classmethod
    def load(cls) -> "Config":
        path = app_dir() / "config.json"
        cfg = cls()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                known = {f.name for f in fields(cls)}
                for k, v in data.items():
                    if k in known:
                        setattr(cfg, k, v)
                # Rewrite if settings were added/renamed in a newer version,
                # so the file always lists every option.
                if set(data) != known:
                    cfg.save()
            except Exception as e:  # bad JSON shouldn't stop the app
                log.warning("Could not read %s: %s (using defaults)", path, e)
        else:
            cfg.save()  # write defaults so the user can find and edit them
        return cfg

    def save(self) -> None:
        path = app_dir() / "config.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def claude_dir(self) -> Path:
        if self.claude_config_dir:
            return Path(os.path.expandvars(self.claude_config_dir)).expanduser()
        env = os.environ.get("CLAUDE_CONFIG_DIR")
        return Path(env) if env else Path.home() / ".claude"

    def codex_dir(self) -> Path:
        if self.codex_home:
            return Path(os.path.expandvars(self.codex_home)).expanduser()
        env = os.environ.get("CODEX_HOME")
        return Path(env) if env else Path.home() / ".codex"
