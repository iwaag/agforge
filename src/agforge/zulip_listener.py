"""agforge's chat entrance: long-poll Zulip and react to messages for the bot.

The mechanics live in `agag.zulip`, shared with the other agents' listeners
(client, self-loop guard, queue re-registration, restart survival). What is
agforge's own is the credentials path, the handler, and the acceptance rule:
DMs as before, plus messages in `create-*` request channels (the
zulip_channel_topic workflow) whose topic is not yet resolved. `#FreeForge`
itself is deliberately not accepted — announcements live there.
"""

from __future__ import annotations

import os
from pathlib import Path

from agag.zulip import (
    RESOLVED_TOPIC_PREFIX,
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

# Request channels created per the zulip_channel_topic workflow.
REQUEST_CHANNEL_PREFIX = "create-"

__all__ = ["ZULIP_ENV", "accept", "handle_message", "log", "main"]


def accept(message: dict, self_id: int) -> bool:
    """DMs, and live topics in request channels. Resolved topics are done."""
    if is_dm_for_us(message, self_id):
        return True
    return (
        is_channel_message_for_us(message, self_id)
        and channel_name(message).startswith(REQUEST_CHANNEL_PREFIX)
        and not str(message.get("subject", "")).startswith(RESOLVED_TOPIC_PREFIX)
    )


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
