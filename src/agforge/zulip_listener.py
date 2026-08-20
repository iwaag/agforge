"""agforge's chat entrance: pull `assetplan-*`/`assetrun-*` topics, poll DMs.

The mechanics live in `agag.zulip`, shared with the other agents' listeners.
Swept *topics* are routed by `dispatch`: `assetplan-` topics go through
`assetplan_topic.handle_topic` — the front/generator pair over a generation
workspace — and `assetrun-` topics through `assetrun_topic.handle_assetrun`
— one Work execution per trigger. The pull loop (`sweep_serve`) finds every
unresolved matching topic in a subscribed channel whose last poster is not
this bot, again on every startup and queue re-registration, so a post that
arrived while the listener was down is not lost. DMs stay on the event
payload (`serve`, in a side thread — a DM narrow cannot be swept, and a lost
DM can simply be resent). Resolving a topic renames it to `✔ …`, which stops
matching the prefixes, so a finished conversation goes quiet for free.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from agag.zulip import ZulipClient, channel_name, dm_partners, is_dm_for_us, log, serve, sweep_serve

from .instance import instance_name

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
ZULIP_ENV = AGFORGE_ROOT / ".local" / "zulip.env"

# Request topics per the zulip_channel_topic workflow. A resolved topic is
# renamed "✔ assetplan-…" and stops matching on its own.
ASSETPLAN_TOPIC_PREFIX = "assetplan-"
# Execution-trigger topics: any non-bot post fires one Work execution.
ASSETRUN_TOPIC_PREFIX = "assetrun-"
SWEEP_PREFIXES = (ASSETRUN_TOPIC_PREFIX, ASSETPLAN_TOPIC_PREFIX)

__all__ = [
    "ZULIP_ENV", "dispatch", "entrance_reply", "handle_message", "log", "main",
    "observe_topic", "topic_filter",
]


def topic_filter(channel: str, topic: str) -> bool:
    """Sweep every topic in this instance's channel, prefixes elsewhere."""
    return channel == instance_name() or topic.startswith(SWEEP_PREFIXES)


def entrance_reply() -> str:
    """The p1 placeholder response for questions at this instance's entrance."""
    name = instance_name()
    return (
        f"This is {name}, an asset-generation agent. "
        f"To request an asset, open an `assetplan-…` topic in `{name}`."
    )


def dispatch(client: ZulipClient, channel: str, topic: str) -> None:
    """Route one swept topic to its handler.

    The two prefixes share no stem, so neither can shadow the other and the
    order here is free. Both work in any subscribed channel: an `assetrun-`
    topic carries no project — the project comes from the chosen Work.
    """
    from .assetplan_topic import handle_topic
    from .assetrun_topic import handle_assetrun

    if topic.startswith(ASSETRUN_TOPIC_PREFIX):
        handle_assetrun(client, channel, topic)
        return
    if topic.startswith(ASSETPLAN_TOPIC_PREFIX):
        handle_topic(client, channel, topic)
        return
    client.send_to_channel(channel, topic, entrance_reply())


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
        from .zulip_chat import react  # the DM route: one charter run

        def topic_handler(channel: str, topic: str) -> None:
            dispatch(client, channel, topic)

        dm_handler = react
    threading.Thread(
        target=serve, args=(dm_client, dm_handler), kwargs={"accept": is_dm_for_us},
        daemon=True,
    ).start()
    log(
        "agforge zulip listener starting "
        f"(pull sweep: all topics in {instance_name()!r}, prefixes {SWEEP_PREFIXES} elsewhere + DM thread)"
    )
    try:
        sweep_serve(client, topic_handler, topic_filter=topic_filter)
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
