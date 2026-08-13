"""agforge's chat entrance: pull `create-*` topics, long-poll DMs.

The mechanics live in `agag.zulip`, shared with the other agents' listeners.
Phase 3 split the two conversation kinds: request *topics* are served by the
pull loop (`sweep_serve` with the `create-` prefix — every unresolved
`create-*` topic in a subscribed channel whose last poster is not this bot,
found again on every startup and queue re-registration, so a post that
arrived while the listener was down is not lost), while DMs stay on the
event payload (`serve`, in a side thread — a DM narrow cannot be swept, and
a lost DM can simply be resent). Resolving a topic renames it to `✔ create-…`,
which stops matching the prefix, so a finished conversation goes quiet for
free.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from agag.zulip import ZulipClient, channel_name, dm_partners, is_dm_for_us, log, serve, sweep_serve

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
ZULIP_ENV = AGFORGE_ROOT / ".local" / "zulip.env"

# Request topics per the zulip_channel_topic workflow. A resolved topic is
# renamed "✔ create-…" and stops matching on its own.
REQUEST_TOPIC_PREFIX = "create-"

__all__ = ["ZULIP_ENV", "handle_message", "log", "main", "observe_topic"]


def handle_message(client: ZulipClient, message: dict, self_id: int) -> None:
    """Passive DM handler (`AGFORGE_ZULIP_LOG_ONLY=1`): log, answer nothing."""
    if message.get("type") == "stream":
        place = f"channel={channel_name(message)!r} topic={message.get('subject')!r}"
    else:
        place = f"partners={dm_partners(message, self_id)}"
    log(
        f"message #{message.get('id')} from {message.get('sender_full_name')!r} "
        f"(id={message.get('sender_id')}, {place}): "
        f"{str(message.get('content', ''))[:200]!r}"
    )


def observe_topic(channel: str, topic: str) -> None:
    """Passive sweep handler (`AGFORGE_ZULIP_LOG_ONLY=1`): log matches only."""
    log(f"observed sweep match {channel!r}/{topic!r}")


def main() -> None:
    client = ZulipClient.from_env(ZULIP_ENV)
    dm_client = ZulipClient.from_env(ZULIP_ENV)
    if os.environ.get("AGFORGE_ZULIP_LOG_ONLY") == "1":
        topic_handler = observe_topic
        dm_handler = handle_message
    else:
        from .zulip_chat import react, react_topic  # the agent route

        def topic_handler(channel: str, topic: str) -> None:
            react_topic(client, channel, topic)

        dm_handler = react
    threading.Thread(
        target=serve, args=(dm_client, dm_handler), kwargs={"accept": is_dm_for_us},
        daemon=True,
    ).start()
    log(
        "agforge zulip listener starting "
        f"(pull sweep prefix {REQUEST_TOPIC_PREFIX!r} + DM thread)"
    )
    try:
        sweep_serve(client, topic_handler, topic_filter=REQUEST_TOPIC_PREFIX)
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
