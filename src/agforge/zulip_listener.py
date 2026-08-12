"""agforge's chat entrance: long-poll Zulip and react to messages for the bot.

The mechanics live in `agag.zulip`, shared with the other agents' listeners
(client, self-loop guard, queue re-registration, restart survival). What is
agforge's own is the credentials path, the handler, and the acceptance rule:
DMs as before, plus channel messages whose *topic* is a `create-*` request
(the zulip_channel_topic workflow). The rule is topic-based and
channel-agnostic on purpose: every subscribed agent sees every event, and
each one's own accept rule is what keeps irrelevant topics from starting a
run — a future shared project channel can carry agforge's topics next to
other agents' without anyone reacting to traffic that is not theirs.
Resolving a topic renames it to `✔ create-…`, which stops matching the
prefix, so a finished conversation goes quiet for free.
"""

from __future__ import annotations

import os
from pathlib import Path

from agag.zulip import (
    ZulipClient,
    channel_name,
    dm_partners,
    is_channel_message_for_us,
    is_dm_for_us,
    log,
    serve,
)

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
ZULIP_ENV = AGFORGE_ROOT / ".local" / "zulip.env"

# Request topics per the zulip_channel_topic workflow. A resolved topic is
# renamed "✔ create-…" and stops matching on its own.
REQUEST_TOPIC_PREFIX = "create-"

__all__ = ["ZULIP_ENV", "accept", "handle_message", "log", "main"]


def accept(message: dict, self_id: int) -> bool:
    """DMs, and channel messages in a live `create-*` request topic."""
    if is_dm_for_us(message, self_id):
        return True
    return is_channel_message_for_us(message, self_id) and str(
        message.get("subject", "")
    ).startswith(REQUEST_TOPIC_PREFIX)


def handle_message(client: ZulipClient, message: dict, self_id: int) -> None:
    """Passive handler (`AGFORGE_ZULIP_LOG_ONLY=1`): log the message, answer nothing."""
    if message.get("type") == "stream":
        place = f"channel={channel_name(message)!r} topic={message.get('subject')!r}"
    else:
        place = f"partners={dm_partners(message, self_id)}"
    log(
        f"message #{message.get('id')} from {message.get('sender_full_name')!r} "
        f"(id={message.get('sender_id')}, {place}): "
        f"{str(message.get('content', ''))[:200]!r}"
    )


def main() -> None:
    handler = handle_message
    if os.environ.get("AGFORGE_ZULIP_LOG_ONLY") != "1":
        try:
            from .zulip_chat import react  # the agent route
        except ImportError:
            react = None
        if react is not None:
            handler = react
    client = ZulipClient.from_env(ZULIP_ENV)
    log(f"agforge zulip listener starting (handler={handler.__name__})")
    try:
        serve(client, handler, accept=accept)
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
