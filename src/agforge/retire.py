"""Retire an asset request, releasing its name for whatever is asked next.

The one route `refactor` p2 asks for. Re-planning revises a request in place
— a new plan post in the same conversation, and the same run topic — which
is the ordinary move and needs nothing here. This is the other one: **the
request itself was wrong**, and what is wanted is a fresh one, usually under
the same name the requester has been using all along.

    uv run python -m agforge.retire <channel> <topic> [--replace]

`--replace` opens the replacement under the freed name and links it back by
anchor id. Without it the name is simply released and the next
`assetplan-<stem>` topic anybody opens is an ordinary new request.

Three things stay true afterwards, and they are the whole point:

- **Late replies belong to the old request.** A ComfyUI job still running is
  collected by the old run topic, whose `[assetrun]` note names the old
  request by id, and its result is delivered to that conversation under
  whatever name it now wears. The replacement never receives it.
- **A replacement is a different request.** New anchor, new run topic, its
  own plan and its own outcome — it does not inherit the retired request's
  verdict, its results, or its pending job.
- **A deleted origin is absent.** Nothing here resolves a conversation by
  name, so a request whose anchor is gone is reported gone rather than
  silently becoming whatever took its name.

Nothing is interrupted and nothing is deleted. Forcing a running generation
to stop is out of scope; deferring the retirement until it lands is the
requester's call, and both orders leave the record readable.
"""

from __future__ import annotations

import argparse
import sys

from agag.zulip import ZulipClient

from .instance import SPEC
from .record import RecordError, open_replacement, read_request, retire_request

__all__ = ["build_parser", "main", "retire"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agforge.retire", description=__doc__.splitlines()[0])
    parser.add_argument("channel", help="the channel the request lives in")
    parser.add_argument("topic", help="the assetplan- topic to retire")
    parser.add_argument(
        "--replace", action="store_true",
        help="open the replacement under the freed name, linked by anchor id",
    )
    return parser


def retire(client: ZulipClient, channel: str, topic: str, replace: bool = False) -> list[str]:
    """Retire one request. One reported line per thing that moved."""
    self_id = int(client.whoami()["user_id"])
    request = read_request(client, channel, topic, self_id)
    if request is None:
        raise RecordError(f"{channel}/{topic} is not a request of mine")
    lines = retire_request(client, request, self_id)
    if replace:
        fresh = open_replacement(client, channel, topic, self_id, request)
        lines.append(
            f"opened {fresh.label} under {topic}, replacing {request.label}; "
            "plan it by posting there"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    client = ZulipClient.from_env(SPEC.zulip_env)
    try:
        for line in retire(client, arguments.channel, arguments.topic, arguments.replace):
            print(line)
    except RecordError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
