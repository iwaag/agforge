"""Serve one topic in this instance's own channel: a question about its work.

Until `agent_standardize` p10 this channel answered with one canned sentence
naming the `assetplan-` prefix. It could say how to ask for an asset and
nothing about the assets it had already made — so "what are you working on,
and how far has each got?" had no answer anywhere.

It is now an ordinary serving of `roles.front` through the shared skeleton,
in a generation workspace holding the conversation, with `agentchat` on PATH.
Everything the run knows it reads from the chat: its own channel's
`assetplan-` and `assetrun-` topics are the plans and their runs, and Zulip's
`✔ ` rename is what says a conversation is finished. Nothing is read from
Plane, and nothing is executed here — a plan is still planned in its
`assetplan-` topic and run from its `assetrun-` one.

Closing a finished topic out is done **when asked**. That is the contract,
not a shackle: the entrance answers questions and follows instructions, and
tidying on its own would be deciding somebody else's conversation is over.
"""

from __future__ import annotations

from pathlib import Path

from agag.topics import (
    TopicResult,
    chatlog_path,
    chatlog_placement,
    format_chatlog,
    generation_dir as shared_generation_dir,
    guide as shared_guide,
    next_generation,
    next_record_path,
    prompt_with_guide,
    serve_topic,
    topic_workspace as shared_topic_workspace,
)
from agag.zulip import ZulipClient, log

from .instance import instance_name
from .role_run import AGFORGE_ROOT, run_role
from .zulip_chat import ACK_PREFIX, SWEEP_ACK

TOPICS_ROOT = AGFORGE_ROOT / ".local" / "topics"
GUIDES = AGFORGE_ROOT / "agent" / "guides"
RECORDS_ROOT = AGFORGE_ROOT / ".local" / "agent"

# The entrance reads chat and writes text. It generates nothing, but a survey
# of a channel's topics is many small reads, so it gets more than the
# assetplan front's 360.
ENTRANCE_TIMEOUT_SECONDS = 600

EMPTY_REPLY = "There is nothing in this topic to answer yet."
NO_ANSWER = "(the run ended without a closing message)"

__all__ = [
    "EntranceError",
    "entrance_prompt",
    "handle_entrance",
    "serve_entrance",
]


class EntranceError(RuntimeError):
    """One entrance serving could not complete."""


def guide(*parts: str) -> str:
    return shared_guide(GUIDES, *parts)


def is_ack(content: str) -> bool:
    """Our own transport noise, which is not conversation."""
    return content.startswith(ACK_PREFIX) or content == SWEEP_ACK


def entrance_prompt(bot_name: str) -> str:
    """The chatlog placement, this instance's own name, then the guide.

    Naming the channel is not routing knowledge handed out: it is this
    agent's own name for its own entrance, which it would otherwise have to
    guess at from the chatlog.
    """
    return prompt_with_guide(
        [chatlog_placement(bot_name), f"Your own channel is {instance_name()!r}."],
        guide("entrance_front", "guide.md"),
    )


def serve_entrance(context) -> TopicResult:
    """One question at the entrance, answered by a front run over the board."""
    workspace_root = shared_topic_workspace(TOPICS_ROOT, context.channel, context.topic)
    number = next_generation(workspace_root)
    workspace = shared_generation_dir(
        TOPICS_ROOT, context.channel, context.topic, number, "front"
    )

    context.step = "chatlog placement"
    chatlog_path(workspace).write_text(
        format_chatlog(context.history, context.self_id, drop=is_ack), encoding="utf-8"
    )

    context.step = "front"
    output, _, exit_code = run_role(
        "front",
        entrance_prompt(context.bot_name),
        cwd=workspace,
        timeout=ENTRANCE_TIMEOUT_SECONDS,
        record=next_record_path(RECORDS_ROOT / "entrance_front"),
        # What the run actually looked at. Without it, an answer that
        # skipped a topic is indistinguishable from one that found nothing
        # in it — which is how `agent_standardize` p10 lost a whole project
        # on autolab's side and could not say why.
        transcript=workspace / "transcript.jsonl",
        stream=True,
        home=(context.channel, context.topic),
    )
    if exit_code != 0:
        raise EntranceError(f"front run exited {exit_code}: {output.strip()[:500]}")
    return TopicResult([output.strip() or NO_ANSWER])


def handle_entrance(client: ZulipClient, channel: str, topic: str) -> None:
    """Serve one entrance topic through the shared skeleton."""
    log(f"entrance topic {channel!r}/{topic!r}")
    serve_topic(
        client, channel, topic, serve_entrance,
        ack_text=SWEEP_ACK,
        empty_reply=EMPTY_REPLY,
    )
