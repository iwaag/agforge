"""Serve one `assetplan-` topic: front agent, then generator agent.

The discipline — ack, generation workspace, chatlog, always post back, and
re-serve when a human spoke during the run — is `agag.topics.serve_topic`,
shared with agautolab. What is agforge's own is the two-agent shape:

    <N>/front/       chatlog.md          → front run  → its answer, posted
                     toolsets.csv                        (what it asked for)
    <N>/generator/   required_items.md   → generator run
                     tools/toolset-*.md    plan.md → this topic's record
                                           idea.md → posted verbatim
                                                   → its answer, posted

Generation directories are never deleted. Cutting a new `N` is precisely what
stops a previous generation's `required_items.md` or `plan.md` from being
re-executed; leftovers are evidence, not garbage.

Recording the plan also **opens the request's `assetrun-` topic** and anchors
it (`anchor.py`), the way autolab opens a `workrun-` topic when it plans a
task. Until p8 the requester had to invent an `assetrun-` name and hope the
queue picked the right Work; now the plan is what creates the button, and the
button knows what it is wired to.

Since `refactor` p2 there is no Work and no Plane. The plan is a post in
**this** topic, named by a `[selfnote][doc]` note, with the toolsets it was
planned with beside it; the request's identity is the message id of its own
`[selfnote][asset]` note (`record.py`). Registration used to be a write to
another system that could be down; it is now a write to the conversation the
requester is already reading.
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
from .record import (
    ASSETPLAN_TOPIC_PREFIX,
    ASSETRUN_TOPIC_PREFIX,
    RecordError,
    Request,
    ensure_request,
    open_run,
    record_plan,
)
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

__all__ = [
    "ASSETPLAN_TOPIC_PREFIX",
    "ASSETRUN_TOPIC_PREFIX",
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


def register_plan(
    client: ZulipClient, channel: str, topic: str, plan: Path, tools, self_id: int,
) -> tuple[Request, str]:
    """Record one generator `plan.md` as this request's plan. `(request, line)`.

    The request is anchored on first sight — its identity is minted by that
    note — and the plan is posted into the conversation that asked for it,
    with the toolsets this generation actually placed in `tools/` recorded
    beside it, so the run that executes it later rebuilds the same `tools/`.

    Planning again posts a new plan and a new `[doc]` note; the old post
    stays where it was, which is the history of the request.
    """
    try:
        request = ensure_request(client, channel, topic, self_id)
        document = plan.read_text(encoding="utf-8")
        request = record_plan(client, request, document, tools)
    except RecordError as error:
        # This module's own error, so the handler's one discipline covers a
        # plan that is not a document and a realm that refused the post alike.
        raise ListenerError(str(error)) from error
    listed = ", ".join(request.tools or []) or "none"
    return request, f'recorded {request.label} "{request.title}" (toolsets: {listed})'


def open_assetrun(client: ZulipClient, request: Request, self_id: int) -> str:
    """Open this request's own `assetrun-` topic. `record.open_run`, said.

    autolab opens a `workrun-` topic when it plans a task; this is the same
    move on agforge's vocabulary, and the topic is named after the request's
    anchor id so a later replacement under the same stem cannot merge into
    it.
    """
    run = open_run(client, request, self_id)
    return f"posting in {run.topic} starts it"


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

    Only called when `required_items.md` is there — the caller decides that,
    because whether a generator run follows also decides which post hands the
    requester their turn back.

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
    generator_dir = generation_dir(channel, topic, number, "generator")
    shutil.copyfile(front_dir / REQUIRED_ITEMS, generator_dir / REQUIRED_ITEMS)
    placed = place_toolsets(front_dir, generator_dir)

    answer = run_generator(generator_dir)

    sections: list[str] = []
    plan = generator_dir / PLAN_FILE
    if plan.is_file():
        # The record carries the toolsets the plan was made with, so the run
        # that executes it later gets the same `tools/`.
        request, line = register_plan(
            context.client, channel, topic, plan, placed, context.self_id)
        sections.append(line)
        # Recording the plan is what opens the request's own run topic — the
        # requester never has to know a name to trigger it, and the topic
        # itself carries which request it runs.
        sections.append(open_assetrun(context.client, request, context.self_id))
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

    if not (front_dir / REQUIRED_ITEMS).is_file():
        # The front has a question, not a spec: no generator run follows, so
        # this answer is the whole reply — and `serve_topic` is what prefixes
        # a reply with the name of whoever is being answered. Posting it
        # early instead (as every answer used to be posted) left the
        # requester unnamed, and a requester who is not named is never
        # brought back: `agent_standardize` p9 watched an exchange stop dead
        # on exactly this, with forge asking a question nobody was told about.
        return TopicResult([answer])

    # Posted on its own, before the generator run: the front's answer is the
    # conversational reply, and the generator can take minutes. Here the
    # registration that follows is the reply, and is what names the requester.
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
