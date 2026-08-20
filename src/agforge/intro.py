"""Post this agforge instance's fixed introduction to the shared agents board."""

from __future__ import annotations

from agag.intro import AGENTS_CHANNEL, intro_topic, post_intro
from agag.zulip import ZulipClient

from .instance import AGFORGE_ROOT, instance_name
from .zulip_listener import ZULIP_ENV

INTRO_PATH = AGFORGE_ROOT / "params" / "intro.md"

__all__ = ["AGENTS_CHANNEL", "INTRO_PATH", "main", "topic"]


def topic() -> str:
    return intro_topic(instance_name())


def main() -> None:
    """Append the current introduction to #agents for this instance."""
    client = ZulipClient.from_env(ZULIP_ENV)
    post_intro(client, instance=instance_name(), intro_path=INTRO_PATH, root=AGFORGE_ROOT)


if __name__ == "__main__":
    main()
