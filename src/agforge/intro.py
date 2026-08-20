"""Post this agforge instance's fixed introduction to the shared agents board."""

from __future__ import annotations

import subprocess
from datetime import date

from agag.zulip import ZulipClient

from .instance import AGFORGE_ROOT, instance_name
from .zulip_listener import ZULIP_ENV

AGENTS_CHANNEL = "agents"
INTRO_PATH = AGFORGE_ROOT / "params" / "intro.md"

__all__ = ["AGENTS_CHANNEL", "INTRO_PATH", "intro_text", "main", "revision", "topic"]


def revision() -> str:
    """The checked-out short revision, or an honest marker outside Git."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=AGFORGE_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def topic() -> str:
    return f"intro-{instance_name()}"


def intro_text(today: date | None = None, commit: str | None = None) -> str:
    """Fixed Markdown plus the per-post freshness stamp."""
    posted = today or date.today()
    current_revision = commit if commit is not None else revision()
    body = INTRO_PATH.read_text(encoding="utf-8").rstrip()
    return f"{body}\n\n---\nPosted: {posted.isoformat()}\nRevision: `{current_revision}`\n"


def main() -> None:
    """Append the current introduction to #agents for this instance."""
    client = ZulipClient.from_env(ZULIP_ENV)
    client.send_to_channel(AGENTS_CHANNEL, topic(), intro_text())


if __name__ == "__main__":
    main()
