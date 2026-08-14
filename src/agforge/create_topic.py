"""Serve one `create-` topic: front agent, then generator agent.

The same discipline as agautolab's `zulip_listener.handle_topic` — ack first,
build a workspace, run the agents in it, and always post back how far the
topic got — but agforge's own two-agent shape:

    ACK
    N = the topic's next generation number
    <N>/front/       chatlog.md          → front run  → its answer, posted
    <N>/generator/   required_items.md   → generator run
                     tools.md              plan.md → a Plane Work
                                           idea.md → posted verbatim
                                                   → its answer, posted

Generation directories are never deleted. Cutting a new `N` is precisely what
stops a previous generation's `required_items.md` or `plan.md` from being
re-executed; leftovers are evidence, not garbage.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agag.zulip import (
    ZulipClient,
    ZulipError,
    _safe_topic_component,
    log,
    topic_write,
)

from .plane import register_plan as plane_register_plan
from .role_run import AGFORGE_ROOT, run_role
from .zulip_chat import ACK_PREFIX, SWEEP_ACK

TOPICS_ROOT = AGFORGE_ROOT / ".local" / "topics"
GUIDES = AGFORGE_ROOT / "agent" / "guides"
RECORDS_ROOT = AGFORGE_ROOT / ".local" / "agent"

HISTORY_MESSAGES = 1000

# One topic now costs two agent runs, both on sonnet. The generator gets the
# wider budget: it generates assets, the front only reads and writes text.
FRONT_TIMEOUT_SECONDS = 360
GENERATOR_TIMEOUT_SECONDS = 900

REQUIRED_ITEMS = "required_items.md"
PLAN_FILE = "plan.md"
IDEA_FILE = "idea.md"

__all__ = [
    "ListenerError",
    "format_chatlog",
    "front_prompt",
    "generation_dir",
    "guide",
    "handle_topic",
    "next_generation",
    "next_record_path",
    "register_plan",
    "run_front",
    "run_generator",
    "topic_workspace",
]


class ListenerError(RuntimeError):
    """One create-topic workflow could not complete."""


def topic_workspace(channel: str, topic: str) -> Path:
    """`.local/topics/<channel>/<topic>/` — the topic's own directory."""
    return (
        TOPICS_ROOT
        / _safe_topic_component(channel, "channel")
        / _safe_topic_component(topic, "topic")
    )


def next_generation(topic_dir: Path) -> int:
    """The topic's next generation number: highest existing one, plus one.

    Numbering is read off the directory itself rather than a counter file, so
    a hand-made or hand-removed generation cannot desynchronize it.
    """
    highest = 0
    if topic_dir.is_dir():
        for child in topic_dir.iterdir():
            if child.is_dir() and child.name.isdigit():
                highest = max(highest, int(child.name))
    return highest + 1


def generation_dir(channel: str, topic: str, number: int, role: str) -> Path:
    directory = topic_workspace(channel, topic) / str(number) / role
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def format_chatlog(messages: list[dict], self_id: int) -> str:
    """`[name] body`, own lines as `name (you)`, acks dropped.

    The acks are this bot's own transport noise, not conversation; leaving
    them in would teach the front that "please wait for the reply" is
    something it once said in answer to a request.
    """
    lines = []
    for message in messages:
        content = str(message.get("content", "")).strip()
        own = message.get("sender_id") == self_id
        if own and (content.startswith(ACK_PREFIX) or content == SWEEP_ACK):
            continue
        speaker = message.get("sender_full_name") or f"user{message.get('sender_id')}"
        if own:
            speaker = f"{speaker} (you)"
        lines.append(f"[{speaker}] {content}")
    return "\n".join(lines) + ("\n" if lines else "")


def guide(*parts: str) -> str:
    """Read one guide file under `agent/guides/`.

    The instruction text belongs to the agents, not to this transport. A
    missing guide is fatal on purpose: a run started without it would be a
    prompt with no instruction in it.
    """
    path = GUIDES.joinpath(*parts)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ListenerError(f"cannot read guide {path}: {error}") from error
    if not text:
        raise ListenerError(f"guide is empty: {path}")
    return text


def front_prompt(bot_name: str) -> str:
    return (
        "The chatlog is placed in the working directory. "
        f"You are {bot_name!r} in the chatlog."
        f"\n\n{guide('create_front', 'guide.md')}"
    )


def next_record_path(directory: Path) -> Path:
    """`run-NNNN.json`, numbered the way every other agforge run record is."""
    directory.mkdir(parents=True, exist_ok=True)
    number = 1
    while (directory / f"run-{number:04d}.json").exists():
        number += 1
    return directory / f"run-{number:04d}.json"


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
        "generator", guide("create_generator", "guide_plan.md"), cwd, GENERATOR_TIMEOUT_SECONDS
    )


def register_plan(channel: str, topic: str, plan: Path) -> str:
    """Register the generator's `plan.md` as this topic's Plane Work.

    Wrapped rather than imported at the call site so the whole Plane client
    stays behind one name — step 5 moves it into `agag` and only this line
    changes.
    """
    return plane_register_plan(channel, topic, plan)


def handle_generator(channel: str, topic: str, front_dir: Path, number: int) -> list[str]:
    """The `required_items.md` branch: build the generator workspace and run it.

    What the front *wrote* drives this, not what it said — its answer is
    relayed verbatim and never parsed.
    """
    required = front_dir / REQUIRED_ITEMS
    if not required.is_file():
        return []
    generator_dir = generation_dir(channel, topic, number, "generator")
    shutil.copyfile(required, generator_dir / REQUIRED_ITEMS)
    shutil.copyfile(GUIDES / "create_generator" / "tools.md", generator_dir / "tools.md")

    answer = run_generator(generator_dir)

    sections: list[str] = []
    plan = generator_dir / PLAN_FILE
    if plan.is_file():
        sections.append(register_plan(channel, topic, plan))
    idea = generator_dir / IDEA_FILE
    if idea.is_file():
        sections.append(idea.read_text(encoding="utf-8").strip())
    sections.append(answer)
    return sections


def handle_topic(client: ZulipClient, channel: str, topic: str) -> None:
    """Serve one awaiting create topic, and always answer it.

    Every exit path after the ack posts something. An ack followed by silence
    would leave this bot as the topic's last poster, which hides the topic
    from the sweep until a human posts again.

    A human posting *during* a run is not lost either: the final reply makes
    this bot the last poster, so before leaving this re-checks for messages
    newer than the chatlog it processed and serves the topic again — as a new
    generation, with the fuller chatlog.
    """
    log(f"create topic {channel!r}/{topic!r}")
    self_user = client.whoami()
    self_id = int(self_user["user_id"])
    bot_name = str(self_user.get("full_name") or client.email)

    while True:
        topic_write(topic, SWEEP_ACK, channel=channel, client=client)

        sections: list[str] = []
        processed_up_to = 0
        completed = False
        step = "reading the topic"
        try:
            number = next_generation(topic_workspace(channel, topic))
            front_dir = generation_dir(channel, topic, number, "front")

            step = "chatlog"
            history = client.topic_history(channel, topic, num_before=HISTORY_MESSAGES)
            processed_up_to = max((int(m.get("id", 0)) for m in history), default=0)
            (front_dir / "chatlog.md").write_text(
                format_chatlog(history, self_id), encoding="utf-8"
            )
            if not any(m.get("sender_id") != self_id for m in history):
                # An empty topic is not a request, and it is not harmless
                # either: `sweep_topics` only skips a topic whose *last*
                # poster is this bot, so a topic with no messages at all
                # matches every sweep forever. Answering it in one line —
                # without an agent run — is what silences it. This is
                # reachable: resolving a topic renames it, and a sweep that
                # reads the old name inside that window finds it empty.
                log(f"nothing to answer in {channel!r}/{topic!r}: no messages")
                topic_write(
                    topic,
                    "There is nothing in this topic to answer yet.",
                    channel=channel,
                    client=client,
                )
                return

            step = "front"
            answer = run_front(front_prompt(bot_name), front_dir)
            # Posted on its own, before the generator run: the front's answer
            # is the conversational reply, and the generator can take minutes.
            topic_write(topic, answer, channel=channel, client=client)

            step = "generator"
            sections = handle_generator(channel, topic, front_dir, number)
            completed = True
        except Exception as error:  # noqa: BLE001 - the topic is the error channel
            log(f"create topic workflow failed during {step}: {error!r}")
            sections = [f"failed during {step}: {error}"]

        if sections:
            topic_write(
                topic,
                "\n\n".join(section for section in sections if section),
                channel=channel,
                client=client,
            )
        if not completed:
            return  # do not loop on a failing topic; a human post re-arms it

        try:
            tail = client.topic_history(channel, topic, num_before=HISTORY_MESSAGES)
        except ZulipError as error:
            log(f"post-run re-check failed for {channel!r}/{topic!r}: {error!r}")
            return
        if not any(
            m.get("sender_id") != self_id and int(m.get("id", 0)) > processed_up_to
            for m in tail
        ):
            return
        log(f"reprocessing {channel!r}/{topic!r}: human posts arrived during the run")
