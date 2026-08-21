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

Registering the Work also **opens its `assetrun-` topic** and anchors it
(`anchor.py`), the way autolab opens a `workrun-` topic when it plans a task.
Until p8 the requester had to invent an `assetrun-` name and hope the queue
picked the right Work; now the plan's registration is what creates the
button, and the button knows what it is wired to.
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
from agag.zulip import ZulipClient, log, topic_write

from . import toolsets
from .anchor import (
    Conversation,
    assetrun_topic_name,
    own_work,
    rootchat_note,
    work_note,
)
from .plane import Registration, register_plan as plane_register_plan
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
EMPTY_REPLY = "There is nothing in this topic to answer yet."

# The two topic names of one request. Spelled here rather than imported from
# the listener so this module is readable without it; `zulip_listener` owns
# the same two constants as the sweep's filter.
ASSETPLAN_TOPIC_PREFIX = "assetplan-"
ASSETRUN_TOPIC_PREFIX = "assetrun-"

__all__ = [
    "ListenerError",
    "front_prompt",
    "generation_dir",
    "guide",
    "handle_generator",
    "handle_topic",
    "open_assetrun",
    "place_toolsets",
    "register_plan",
    "run_front",
    "run_generator",
    "topic_workspace",
]


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


def register_plan(channel: str, topic: str, plan: Path, tools=()) -> Registration:
    """Wrapped so the whole Plane route stays behind one name here."""
    return plane_register_plan(channel, topic, plan, tools)


def open_assetrun(
    client: ZulipClient,
    channel: str,
    topic: str,
    registration: Registration,
    self_id: int,
) -> str:
    """Open this Work's own `assetrun-` topic and anchor it to two things.

    autolab opens a `workrun-` topic when it plans a task; this is the same
    move on agforge's vocabulary. The topic is opened with its two selfnotes
    first — the root note back to this `assetplan-` conversation, and the
    Work id — and then one visible line, which is all a reader ever sees of
    it. Because forge is its own last real speaker there, opening the topic
    does not fire it: a post from somebody else is what starts the run, and
    what that post *says* is read.

    Idempotent by the Work note: a second generation of the same plan finds
    its topic already anchored and only says where it is.
    """
    run_topic = assetrun_topic_name(topic, ASSETRUN_TOPIC_PREFIX, ASSETPLAN_TOPIC_PREFIX)
    history = client.topic_history(channel, run_topic, num_before=200)
    if own_work(history, self_id) is None:
        topic_write(
            run_topic,
            rootchat_note(Conversation(channel, topic)),
            channel=channel, client=client,
        )
        topic_write(
            run_topic,
            work_note(registration.project_id, registration.issue_id),
            channel=channel, client=client,
        )
        topic_write(
            run_topic,
            f'This topic runs {registration.label} "{registration.title}". '
            "Post here to start it, saying anything you want done differently; "
            f"the result is posted back here and in {topic}.",
            channel=channel, client=client,
        )
    return f"posting in {run_topic} starts it"


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


def handle_generator(context, front_dir: Path, number: int) -> list[str]:
    """The `required_items.md` branch: build the generator workspace, run it.

    What the front *wrote* drives this, not what it said — its answer is
    relayed verbatim and never parsed, with no exception left.

    Mentioning whoever is being answered used to live here, behind the
    generator's `question.flag`. Since `agent_standardize` p7 the shared
    `serve_topic` prefixes **every** reply with the last other speaker's
    name, because being named is how the next run happens at all and not a
    courtesy owed only to questions. Doing it here as well would name the
    requester twice.
    """
    channel, topic = context.channel, context.topic
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
        registration = register_plan(channel, topic, plan, placed)
        sections.append(registration.line)
        # Registering the plan is what opens the Work's own topic — the
        # requester never has to know a name to trigger it, and the topic
        # itself carries which Work it runs.
        sections.append(
            open_assetrun(context.client, channel, topic, registration, context.self_id)
        )
    idea = generator_dir / IDEA_FILE
    if idea.is_file():
        sections.append(idea.read_text(encoding="utf-8").strip())
    sections.append(answer)
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
    return TopicResult(handle_generator(context, front_dir, number))


def handle_topic(client: ZulipClient, channel: str, topic: str) -> None:
    log(f"assetplan topic {channel!r}/{topic!r}")
    serve_topic(
        client, channel, topic, serve,
        ack_text=SWEEP_ACK,
        empty_reply=EMPTY_REPLY,
    )
