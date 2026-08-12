"""agforge's chat entrance: long-poll Zulip and react to DMs sent to the bot.

Deliberately dumb: no queue persistence, no delivery guarantees. A DM that
arrives while this is down is lost and the sender can resend.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

from .zulip import QueueExpired, ZulipClient, ZulipError, ZulipTimeout, dm_partners

RETRY_SECONDS = 5


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} {message}", file=sys.stderr, flush=True)


def is_dm_for_us(message: dict, self_id: int) -> bool:
    """A private message from somebody else. The bot's own DMs echo back."""
    return message.get("type") == "private" and message.get("sender_id") != self_id


def handle_message(client: ZulipClient, message: dict, self_id: int) -> None:
    """Default reaction: log it. Step 3 replaces this with the agent route."""
    partners = dm_partners(message, self_id)
    log(
        f"DM #{message.get('id')} from {message.get('sender_full_name')!r} "
        f"(id={message.get('sender_id')}, partners={partners}): "
        f"{str(message.get('content', ''))[:200]!r}"
    )


def serve(client: ZulipClient, handler=handle_message) -> None:
    self_id: int | None = None
    queue_id: str | None = None
    last_event_id = -1
    while True:
        try:
            if self_id is None:
                # Inside the loop on purpose: Zulip restarts, and a listener
                # that dies of that is a listener someone has to babysit.
                self_id = int(client.whoami()["user_id"])
                log(f"listening as user_id={self_id} ({client.email})")
            if queue_id is None:
                queue_id, last_event_id = client.register()
                log(f"registered event queue {queue_id} (last_event_id={last_event_id})")
            events = client.poll(queue_id, last_event_id)
        except ZulipTimeout:
            continue  # nothing happened within the poll window
        except QueueExpired as error:
            log(f"event queue expired ({error}); re-registering")
            queue_id = None
            continue
        except ZulipError as error:
            log(f"zulip call failed: {error}; retrying in {RETRY_SECONDS}s")
            queue_id = None
            time.sleep(RETRY_SECONDS)
            continue
        for event in events:
            last_event_id = max(last_event_id, int(event.get("id", last_event_id)))
            if event.get("type") != "message":
                continue
            message = event.get("message") or {}
            if not is_dm_for_us(message, self_id):
                continue
            try:
                handler(client, message, self_id)
            except Exception as error:  # one bad message must not end the loop
                log(f"handler failed on message #{message.get('id')}: {error!r}")


def main() -> None:
    handler = handle_message
    if os.environ.get("AGFORGE_ZULIP_LOG_ONLY") != "1":
        try:
            from .zulip_chat import react  # step 3 route
        except ImportError:
            react = None
        if react is not None:
            handler = react
    client = ZulipClient.from_env()
    log(f"agforge zulip listener starting (handler={handler.__name__})")
    try:
        serve(client, handler)
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
