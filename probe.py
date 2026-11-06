"""Quick check that both data sources work on this PC. Prints, then exits.

    .venv\\Scripts\\python probe.py

Never prints your login token.
"""

import sys

from usagetray.config import Config
from usagetray.model import fmt_ago, fmt_duration, now_utc
from usagetray.providers import ClaudeProvider, CodexProvider


def show(snap, where):
    print(f"\n=== {snap.provider.upper()} ===")
    print(f"  source : {where}")
    print(f"  status : {snap.status}" + (f"  (plan: {snap.plan})" if snap.plan else ""))
    if snap.warning:
        print(f"  WARNING: {snap.warning}")
    if snap.detail:
        print(f"  note   : {snap.detail}")
    if snap.as_of:
        print(f"  as of  : {fmt_ago(snap.as_of)}")
    now = now_utc()
    for w in snap.windows:
        reset = ""
        if w.resets_at:
            reset = ("already reset" if w.has_reset(now)
                     else f"resets in {fmt_duration((w.resets_at - now).total_seconds())}")
        bar = "#" * int(w.effective_pct(now) / 5)
        print(f"  {w.label:<18} {w.effective_pct(now):5.1f}%  [{bar:<20}]  {reset}")


def main():
    cfg = Config.load()
    claude = ClaudeProvider(cfg.claude_dir())
    codex = CodexProvider(cfg.codex_dir(), command=cfg.codex_command)

    snap = claude.fetch(force=True)
    left = claude.token_expires_in()
    token_info = ("blank" if not claude.read_credentials()[0] else
                  "unknown expiry" if left is None else
                  f"expires in {fmt_duration(left)}" if left > 0 else
                  f"EXPIRED {fmt_duration(-left)} ago")
    show(snap, f"live API: {'OK' if claude.live_ok() else 'not working'} (saved token {token_info}); "
               f"status line hook {'installed' if claude.hook_installed() else 'not installed'}")

    snap = codex.fetch(force=True)
    cmd = codex.codex_command()
    show(snap, f"live app-server: {'OK' if codex.live_ok() else 'not working'} "
               f"(command: {' '.join(cmd) if cmd else 'codex not found'}); logs: {codex.sessions_dir}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
