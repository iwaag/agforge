"""Serve one topic in this instance's own channel: a question about its work.

Since `agag_builder` p1 the serving is `agag.entrance`, shared by every agent
on the skeleton; what stays here is agforge's guide
(`agent/guides/entrance_front/guide.md`, the `assetplan-`/`assetrun-`
vocabulary), which the skeleton picks over its built-in default.
"""

from __future__ import annotations

from agag import entrance as shared
from agag.entrance import EMPTY_REPLY, ENTRANCE_TIMEOUT_SECONDS, NO_ANSWER, EntranceError
from agag.zulip import ZulipClient

from .role_run import SPEC

TOPICS_ROOT = SPEC.topics_root
GUIDES = SPEC.guides
RECORDS_ROOT = SPEC.records_root

__all__ = [
    "EMPTY_REPLY", "ENTRANCE_TIMEOUT_SECONDS", "NO_ANSWER",
    "EntranceError", "entrance_prompt", "handle_entrance", "serve_entrance",
]


def entrance_prompt(bot_name: str) -> str:
    return shared.entrance_prompt(SPEC, bot_name)


def serve_entrance(context):
    return shared.serve_entrance(SPEC, context)


def handle_entrance(client: ZulipClient, channel: str, topic: str) -> None:
    shared.handle_entrance(SPEC, client, channel, topic)
