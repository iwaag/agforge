"""agforge's chat entrance: long-poll Zulip and react to DMs sent to the bot.

The mechanics live in `agag.zulip`, shared with the other agents' listeners
(client, self-loop guard, queue re-registration, restart survival). What is
agforge's own is the credentials path and the handler.
"""

from __future__ import annotations

import os
from pathlib import Path

from agag.zulip import ZulipClient, dm_partners, log, serve

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
ZULIP_ENV = AGFORGE_ROOT / ".local" / "zulip.env"

__all__ = ["ZULIP_ENV", "handle_message", "log", "main"]


def handle_message(client: ZulipClient, message: dict, self_id: int) -> None:
    """Passive handler (`AGFORGE_ZULIP_LOG_ONLY=1`): log the DM, answer nothing."""
    partners = dm_partners(message, self_id)
    log(
        f"DM #{message.get('id')} from {message.get('sender_full_name')!r} "
        f"(id={message.get('sender_id')}, partners={partners}): "
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
        serve(client, handler)
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
