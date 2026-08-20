"""Serve one `assetplan-` topic: front agent, then generator agent.

The discipline — ack, generation workspace, chatlog, always post back, and
re-serve when a human spoke during the run — is `agag.topics.serve_topic`,
shared with agautolab. What is agforge's own is the two-agent shape:

    <N>/front/       chatlog.md          → front run  → its answer, posted
                     toolsets.csv                        (what it asked for)
    <N>/generator/   required_items.md   → generator run
                     tools/toolset-*.md    plan.md → a Plane Work
                                           idea.md → posted verbatim
                                                   → its answer, posted

Generation directories are never deleted. Cutting a new `N` is precisely what
stops a previous generation's `required_items.md` or `plan.md` from being
re-executed; leftovers are evidence, not garbage.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agag.topics import (
    GuideError,
    TopicResult,
    chatlog_placement,
    chatlog_path,
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

from . import toolsets
from .plane import register_plan as plane_register_plan
from .role_run import AGFORGE_ROOT, run_role
from .zulip_chat import ACK_PREFIX, SWEEP_ACK

TOPICS_ROOT = AGFORGE_ROOT / ".local" / "topics"
GUIDES = AGFORGE_ROOT / "agent" / "guides"
RECORDS_ROOT = AGFORGE_ROOT / ".local" / "agent"

# One topic costs two agent runs, both on sonnet. The generator gets the
# wider budget: it generates assets, the front only reads and writes text.
FRONT_TIMEOUT_SECONDS = 360
GENERATOR_TIMEOUT_SECONDS = 900

REQUIRED_ITEMS = "required_items.md"
TOOLSETS_CSV = "toolsets.csv"
TOOLS_DIR = "tools"
PLAN_FILE = "plan.md"
IDEA_FILE = "idea.md"
# The generator's own signal that it answered with a question instead of a
# plan (`assetplan_generator/guide_plan.md`). A question posted into a topic
# nobody is watching is a conversation that stalls, so the reply gets a Zulip
# mention of whoever spoke last.
QUESTION_FLAG = "question.flag"

EMPTY_REPLY = "There is nothing in this topic to answer yet."

__all__ = [
    "QUESTION_FLAG",
    "ListenerError",
    "front_prompt",
    "generation_dir",
    "guide",
    "handle_generator",
    "handle_topic",
    "last_other_sender",
    "mention",
    "place_toolsets",
    "register_plan",
    "run_front",
    "run_generator",
    "topic_workspace",
]


def last_other_sender(messages: list[dict], self_id: int) -> str | None:
    """The full name of the most recent poster who is not this bot.

    The realm hides real email addresses, and Zulip's `@**Full Name**` syntax
    wants exactly this — so the name that already travels in the message list
    for `chatlog.md` is the whole lookup. `None` when only the bot has spoken.
    """
    for message in reversed(messages):
        if message.get("sender_id") == self_id:
            continue
        if name := str(message.get("sender_full_name") or "").strip():
            return name
    return None


def mention(name: str) -> str:
    return f"@**{name}**"


class ListenerError(RuntimeError):
    """One assetplan-topic workflow could not complete."""


def topic_workspace(channel: str, topic: str) -> Path:
    return shared_topic_workspace(TOPICS_ROOT, channel, topic)


def generation_dir(channel: str, topic: str, number: int, role: str) -> Path:
    return shared_generation_dir(TOPICS_ROOT, channel, topic, number, role)


def guide(*parts: str) -> str:
    return shared_guide(GUIDES, *parts)


def is_ack(content: str) -> bool:
    """Our own transport noise, which is not conversation."""
    return content.startswith(ACK_PREFIX) or content == SWEEP_ACK


def front_prompt(bot_name: str) -> str:
    return prompt_with_guide(
        [chatlog_placement(bot_name)], guide("assetplan_front", "guide.md")
    )


def _run(role: str, prompt: str, cwd: Path, timeout: float) -> str:
    record = next_record_path(RECORDS_ROOT / role)
    output, _, exit_code = run_role(role, prompt, cwd=cwd, timeout=timeout, record=record)
    if exit_code != 0:
        raise ListenerError(f"{role} run exited {exit_code}: {output.strip()[:500]}")
    return output.strip()


def run_front(prompt: str, cwd: Path) -> str:
    return _run("front", prompt, cwd, FRONT_TIMEOUT_SECONDS)


def run_generator(cwd: Path) -> str:
    return _run(
        "generator",
        guide("assetplan_generator", "guide_plan.md"),
        cwd,
        GENERATOR_TIMEOUT_SECONDS,
    )


def register_plan(channel: str, topic: str, plan: Path, tools=()) -> str:
    """Wrapped so the whole Plane route stays behind one name here."""
    return plane_register_plan(channel, topic, plan, tools)


def place_toolsets(front_dir: Path, generator_dir: Path) -> list[str]:
    """Build the generator's `tools/` from the front's `toolsets.csv`.

    No csv, or nothing in it that resolves, leaves `tools/` empty — which is
    a route the guide already covers: the generator asks back, writes
    `idea.md`, or declines. Nothing here decides on the front's behalf.
    """
    csv = front_dir / TOOLSETS_CSV
    requested = (
        toolsets.parse_names(csv.read_text(encoding="utf-8")) if csv.is_file() else []
    )
    return toolsets.place(requested, generator_dir / TOOLS_DIR)


def handle_generator(
    channel: str, topic: str, front_dir: Path, number: int, asking: str | None = None
) -> list[str]:
    """The `required_items.md` branch: build the generator workspace, run it.

    What the front *wrote* drives this, not what it said — its answer is
    relayed verbatim and never parsed. `question.flag` is the one exception,
    and even then nothing in the answer is read: the flag alone decides that
    `asking` — the last non-forge poster — gets mentioned above it.
    """
    required = front_dir / REQUIRED_ITEMS
    if not required.is_file():
        return []
    generator_dir = generation_dir(channel, topic, number, "generator")
    shutil.copyfile(required, generator_dir / REQUIRED_ITEMS)
    placed = place_toolsets(front_dir, generator_dir)

    answer = run_generator(generator_dir)

    sections: list[str] = []
    plan = generator_dir / PLAN_FILE
    if plan.is_file():
        # The Work carries the toolsets it was planned with, so the run that
        # executes it later gets the same `tools/`.
        sections.append(register_plan(channel, topic, plan, placed))
    idea = generator_dir / IDEA_FILE
    if idea.is_file():
        sections.append(idea.read_text(encoding="utf-8").strip())
    sections.append(answer)
    if (generator_dir / QUESTION_FLAG).is_file() and asking:
        sections.insert(0, mention(asking))
    return sections


def serve(context) -> TopicResult:
    """agforge's part of one serving: the front run, then the generator."""
    number = next_generation(topic_workspace(context.channel, context.topic))
    front_dir = generation_dir(context.channel, context.topic, number, "front")
    chatlog_path(front_dir).write_text(
        format_chatlog(context.history, context.self_id, drop=is_ack), encoding="utf-8"
    )

    context.step = "front"
    answer = run_front(front_prompt(context.bot_name), front_dir)
    # Posted on its own, before the generator run: the front's answer is the
    # conversational reply, and the generator can take minutes.
    context.post(answer)

    context.step = "generator"
    return TopicResult(
        handle_generator(
            context.channel,
            context.topic,
            front_dir,
            number,
            last_other_sender(context.history, context.self_id),
        )
    )


def handle_topic(client: ZulipClient, channel: str, topic: str) -> None:
    log(f"assetplan topic {channel!r}/{topic!r}")
    serve_topic(
        client, channel, topic, serve,
        ack_text=SWEEP_ACK,
        empty_reply=EMPTY_REPLY,
    )
