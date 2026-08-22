"""agforge's chat entrance: `assetplan-*`/`assetrun-*` topics, the DM route.

The listener is `agag.agent.listener_main`: the pull sweep over every topic
in this instance's own channel and the two prefixes elsewhere, the entrance
(`agag.entrance`) for whatever matches no prefix, a DM thread, and the
`AGFORGE_ZULIP_LOG_ONLY=1` observer switch. What is agforge's own is the
routing table below and the DM route (`zulip_chat.react`, one charter run).
"""

from __future__ import annotations

from agag.agent import listener_main, log_only, topic_filter as _topic_filter
from agag.zulip import ZulipClient, log

from .instance import ASSETPLAN_TOPIC_PREFIX, ASSETRUN_TOPIC_PREFIX, SPEC, instance_name

ZULIP_ENV = SPEC.zulip_env
SWEEP_PREFIXES = SPEC.sweep_prefixes

__all__ = ["SWEEP_PREFIXES", "ZULIP_ENV", "dispatch", "log", "main", "routes", "topic_filter"]


def topic_filter(channel: str, topic: str) -> bool:
    """Sweep every topic in this instance's channel, prefixes elsewhere."""
    return channel == instance_name() or topic.startswith(SWEEP_PREFIXES)


def routes() -> dict:
    """Prefix → handler. Anything else in the own channel is the entrance."""
    from .assetplan_topic import handle_topic
    from .assetrun_topic import handle_assetrun

    return {ASSETRUN_TOPIC_PREFIX: handle_assetrun, ASSETPLAN_TOPIC_PREFIX: handle_topic}


def dispatch(client: ZulipClient, channel: str, topic: str) -> None:
    """Route one swept topic to its handler (the skeleton's rule, callable)."""
    from .entrance_topic import handle_entrance

    for prefix, handler in routes().items():
        if topic.startswith(prefix):
            handler(client, channel, topic)
            return
    handle_entrance(client, channel, topic)


def main() -> None:
    dm_handler = None
    if not log_only(SPEC):
        from .zulip_chat import react  # the DM route: one charter run

        dm_handler = react
    listener_main(SPEC, routes(), dm_handler=dm_handler)


if __name__ == "__main__":
    main()
