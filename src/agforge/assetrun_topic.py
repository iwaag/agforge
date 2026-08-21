"""Execute the Work an `assetrun-` topic was opened for, when somebody posts.

autolab's `workrun-` shape, on agforge's vocabulary. Until
`agent_standardize` p8 this topic was a bare button: the chatlog was never
read, and any post fired whichever eligible Work `works.next_work` happened
to pick, which is why the introduction had to ask the requester for "one
trigger, one Work — let the delivery land before the next one". That burden
is gone. The topic is opened by the assetplan flow when it registers the
plan, and it carries two selfnotes (`anchor.py`) saying which Work it runs
and which `assetplan-` conversation it belongs to. A trigger is answered by
reading the topic.

So the chatlog is real input now: whoever posts says what they want done, the
same way a `workrun-` post does, and the generator gets it beside `plan.md`.

The workspace is `.local/agentws/<work id>/generator/` — per Work, not per
topic, and never deleted. A re-trigger rebuilds `plan.md`, `chatlog.md` and
`tools/` from the Work and the topic, and leaves `result/`/`intermediate/` as
they are; there is no dirty check on purpose (the braindump drops autolab's
create/delete dance).

`tools/` is what the Work's `[TOOLS]` description footer names — the
toolsets the create flow planned it with. A Work without that footer is
hand-made, or predates this phase, and gets the whole library.

The result goes to **both** topics: the `assetrun-` one, through
`serve_topic`'s ordinary reply, and the `assetplan-` one the root note names,
where the requester was talking.

**Only the delivery names the trigger** (`agent_standardize` p9). Being named
is how a requester's next turn happens at all, so naming them in both places
gives them two — p8's proof watched Front tell the developer "done" twice for
one image. Harmless there; where the requester is a supercoder it is a second
run against a live repository. The delivery is the post that counts, because
it is what the requester was waiting for. The `assetrun-` reply is the
record, and `handoff=False` keeps it from handing anybody a turn.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agag.plane import compose_document, description_html
from agag.topics import (
    TopicResult,
    chatlog_path,
    format_chatlog,
    guide as shared_guide,
    next_record_path,
    serve_topic,
)
from agag.selfnote import is_selfnote
from agag.zulip import ZulipClient, log, topic_write

from . import generate, toolsets
from .anchor import own_rootchat, own_work
from .plane import split_tools_footer
from .role_run import AGFORGE_ROOT, run_role
from .works import Work, report_work, work_by_id
from .zulip_chat import ACK_PREFIX, SWEEP_ACK

AGENTWS_ROOT = AGFORGE_ROOT / ".local" / "agentws"
GUIDES = AGFORGE_ROOT / "agent" / "guides"
RECORDS_ROOT = AGFORGE_ROOT / ".local" / "agent"

TOOLS_DIR = "tools"

# The generator's own verdict on its run, from `assetrun_generator/guide.md`.
# The exit code stays the first-class failure signal; this is the agent
# saying so itself when the harness saw nothing wrong.
FAILURE_FLAG = "failure.flag"

# Real work, not a planning pass: autolab's work run uses 1200 s and the
# assetplan-flow generator 900 s; this sits at the top of that range.
ASSETRUN_TIMEOUT_SECONDS = 1200

# A topic nobody anchored. Not an error and not a guess: since p8 an
# `assetrun-` topic is opened by the plan that owns it, so one that says
# nothing about itself is somebody's hand-made name and has no Work to run.
UNANCHORED_REPLY = (
    "This topic is not the run topic of any plan of mine, so there is nothing "
    "here to execute. Open an `assetplan-…` topic to plan an asset; I open its "
    "`assetrun-…` topic myself when the plan is registered, and that is the one "
    "to post in."
)
EMPTY_REPLY = "There is nothing in this topic to answer yet."

FAILED_PREFIX = "the run reported failure; what it produced follows"

# The durable half of a delivery, on its own last line — the same shape as the
# `[TOOLS]` footer `plane.py` already puts in a description. A presigned URL
# dies after `generate.DEFAULT_TTL_MINUTES`; the object behind it does not, so
# whoever reads this later re-signs the key through `POST /api/resign` instead
# of finding an expired link. Carried by both the delivery post and the Plane
# comment, because a consumer may only be looking at one of them.
S3_KEY_MARKER = "[S3KEY]"

__all__ = [
    "AGENTWS_ROOT",
    "EMPTY_REPLY",
    "FAILED_PREFIX",
    "FAILURE_FLAG",
    "S3_KEY_MARKER",
    "UNANCHORED_REPLY",
    "ListenerError",
    "deliver_to_origin",
    "trigger_mention",
    "handle_assetrun",
    "prepare_workspace",
    "result_files",
    "run_generator",
    "s3_key_footer",
    "serve",
    "upload_result",
    "workspace_dir",
    "zip_result",
]


class ListenerError(RuntimeError):
    """One assetrun-topic workflow could not complete."""


def is_ack(content: str) -> bool:
    """Our own transport noise, which is not conversation."""
    return content.startswith(ACK_PREFIX) or content == SWEEP_ACK


def workspace_dir(issue_id: str) -> Path:
    """`.local/agentws/<work id>/generator/` — the Work's own directory."""
    return AGENTWS_ROOT / issue_id / "generator"


def prepare_workspace(work: Work) -> Path:
    """Build (or refresh) the Work's workspace.

    `plan.md` is the Work itself, in the same document shape `register_plan`
    split it from, minus the `[TOOLS]` footer — that line is addressed to
    this function, not to the generator. `tools/` is rebuilt from it, both
    derived from the Work and both replaced on a re-trigger; `result/` and
    `intermediate/` are left as they are.

    A leftover `failure.flag` is removed here, so a re-trigger starts clean
    and the flag found after the run is this run's own verdict.
    """
    workspace = workspace_dir(work.issue_id)
    workspace.mkdir(parents=True, exist_ok=True)
    description, requested = split_tools_footer(work.description)
    (workspace / "plan.md").write_text(
        compose_document(work.name, description_html(description)), encoding="utf-8"
    )
    shutil.rmtree(workspace / TOOLS_DIR, ignore_errors=True)
    toolsets.place(
        toolsets.names() if requested is None else requested, workspace / TOOLS_DIR
    )
    (workspace / FAILURE_FLAG).unlink(missing_ok=True)
    (workspace / "result").mkdir(exist_ok=True)
    (workspace / "intermediate").mkdir(exist_ok=True)
    return workspace


def run_generator(workspace: Path) -> str:
    """One generator run in the Work's workspace, with its record."""
    record = next_record_path(RECORDS_ROOT / "assetrun")
    output, _, exit_code = run_role(
        "generator",
        shared_guide(GUIDES, "assetrun_generator", "guide.md"),
        cwd=workspace,
        timeout=ASSETRUN_TIMEOUT_SECONDS,
        record=record,
    )
    if exit_code != 0:
        raise ListenerError(f"generator run exited {exit_code}: {output.strip()[:500]}")
    return output.strip()


def serve(context) -> TopicResult:
    """One trigger: the Work this topic names, run and delivered twice.

    Everything the run needs is read off the topic — which Work
    (`[selfnote][work]`), where the requester is talking
    (`[selfnote][rootchat]`), and what they just asked for (the chatlog).
    Nothing is chosen from a queue.
    """
    anchored = own_work(context.history, context.self_id)
    if anchored is None:
        return TopicResult([UNANCHORED_REPLY])
    project_id, issue_id = anchored

    context.step = "loading the work"
    work = work_by_id(project_id, issue_id)
    if work is None:
        return TopicResult([
            f"the Work this topic runs ({issue_id}) is gone from Plane; "
            "plan it again in an `assetplan-…` topic"
        ])
    sections = [f'running "{work.name}"']

    context.step = "preparing the workspace"
    workspace = prepare_workspace(work)
    # The conversation is input, not decoration: this is where the trigger
    # says what it wants of a plan that was written some time ago.
    chatlog_path(workspace).write_text(
        format_chatlog(context.history, context.self_id, drop=is_ack), encoding="utf-8"
    )

    context.step = "generator run"
    answer = run_generator(workspace)
    # The run exiting zero is not the whole verdict: the guide tells the
    # generator to leave `failure.flag` when it knows it failed. An empty
    # `result/` is still a legitimate pure-text outcome, not a signal.
    succeeded = not (workspace / FAILURE_FLAG).exists()
    if not succeeded:
        sections.append(f"{FAILURE_FLAG} is present: the generator reports failure")

    context.step = "packaging the result"
    files = result_files(workspace)
    comment = answer
    if files:
        key, url = upload_result(zip_result(workspace))
        footer = s3_key_footer(key)
        delivery = (
            f"result of \"{work.name}\" ({len(files)} file(s)), "
            f"temporary download (expires in {generate.DEFAULT_TTL_MINUTES} min): "
            f"{url}\n{footer}"
        )
        # The Plane comment is the ledger consumers read months later, so it
        # carries the key too — never only the URL that outlives it by an hour.
        comment = f"{answer}\n\n{footer}" if answer else footer
        sections.append(
            f"result/ holds {len(files)} file(s); zipped and uploaded as {key}"
        )
    else:
        delivery = answer
        sections.append("result/ is empty; delivering the answer text")
    if not succeeded:
        # The requester hears the same verdict the Work does; whatever the run
        # did produce still travels with it.
        delivery = f"{FAILED_PREFIX}\n\n{delivery}"

    context.step = "origin delivery"
    sections.append(deliver_to_origin(context, work, delivery))

    context.step = "reporting to plane"
    # `success=False` leaves the Work in its unstarted state, so it stays
    # selectable and a re-trigger runs it again.
    label, commented, completed = report_work(
        work.project_id, work.issue_id, comment, succeeded
    )
    sections.append(
        f"work {label}: commented {'yes' if commented else 'no'}, "
        f"Done {'yes' if completed else 'no'}"
    )
    return TopicResult(sections)


def handle_assetrun(client: ZulipClient, channel: str, topic: str) -> None:
    """Serve one triggered assetrun topic through the shared skeleton.

    The skeleton is what it was a button instead of: an ack so the sweep
    leaves it alone while the generator works, the chatlog, always an answer,
    a reply that names whoever spoke last, and a re-check for a post that
    arrived during the run.
    """
    log(f"assetrun topic {channel!r}/{topic!r}")
    serve_topic(
        client, channel, topic, serve,
        ack_text=SWEEP_ACK,
        empty_reply=EMPTY_REPLY,
        # The delivery into the `assetplan-` topic names the trigger. This
        # post is the record of the run, and naming them here too would buy
        # them a second run for one delivery.
        handoff=False,
    )


def result_files(workspace: Path) -> list[Path]:
    """Every file under `result/`, in stable order."""
    return sorted(path for path in (workspace / "result").rglob("*") if path.is_file())


def zip_result(workspace: Path) -> Path:
    """`result/` as `result.zip` in the workspace root — outside the archived
    directory, so it can never contain itself. Overwritten on re-trigger."""
    return Path(shutil.make_archive(
        str(workspace / "result"), "zip", root_dir=workspace / "result"
    ))


def upload_result(archive: Path) -> tuple[str, str]:
    """`(bucket key, presigned download URL)` for the archive.

    `generate.load_env`/`upload_and_presign_key` answer a missing
    configuration with `sys.exit`, which is right for the CLI they serve and
    wrong here — a SystemExit would sail past the handler's error discipline.
    """
    try:
        return generate.upload_and_presign_key(
            generate.load_env(), archive, generate.DEFAULT_TTL_MINUTES
        )
    except SystemExit as error:
        raise ListenerError(f"upload failed: {error}") from error


def s3_key_footer(key: str) -> str:
    return f"{S3_KEY_MARKER} {key}"


def origin_of(context, work: Work) -> tuple[str, str] | None:
    """Where the requester is talking: the topic's root note, or the Work's key.

    The root note is what forge wrote when it opened this topic, so it is the
    answer for anything planned since p8. `work.origin()` — p1's
    `<channel>/<topic>` external id — still answers for a Work planned
    before that.
    """
    home = own_rootchat(context.history, context.self_id)
    return home.as_pair() if home is not None else work.origin()


def trigger_mention(context) -> str:
    """`@**<name>**` for whoever triggered *this* run.

    Read off `context.history` — the conversation as it stood when the run
    was served — and not by re-reading the topic afterwards, which is what
    `handoff_mention` does. A generation takes minutes, and anybody may post
    into the run topic while it lasts: `agent_standardize` p9 watched a
    supervisor ask "how is it going?" mid-run and receive the delivery
    intended for the agent that had actually triggered it, which was then
    never called back at all. The trigger is a fact about the past, so it is
    read from the past.
    """
    for message in reversed(list(context.history)):
        if message.get("sender_id") == context.self_id:
            continue
        if is_selfnote(message.get("content")):
            continue
        name = str(message.get("sender_full_name") or "").strip()
        if name:
            return f"@**{name}**"
    return ""


def deliver_to_origin(context, work: Work, delivery: str) -> str:
    """Post the delivery into the `assetplan-` topic, naming who triggered it.

    The trigger came from somewhere, and whoever made it is waiting in their
    own conversation, not in this one. Naming them is not courtesy: a
    participant of a topic is served only when a post names it, so this is
    the thing that gives them their turn back — which is exactly why it has
    to be the *trigger* and not merely the last voice in the room.

    Said either way — the assetrun summary must survive everything, including
    a dead origin channel. The origin `assetplan-` topic may already be
    resolved (`✔`); posting under the plain name still lands, and this bot
    being last poster there cannot re-trigger the assetplan sweep.
    """
    origin = origin_of(context, work)
    if origin is None:
        return f"no origin topic recorded; the result stays here:\n\n{delivery}"
    channel, topic = origin
    trigger = trigger_mention(context)
    body = f"{trigger}\n\n{delivery}" if trigger else delivery
    try:
        topic_write(topic, body, channel=channel, client=context.client)
    except Exception as error:  # noqa: BLE001 - reported, never fatal
        log(f"origin delivery to {channel!r}/{topic!r} failed: {error!r}")
        return (
            f"could not deliver to {channel}/{topic} ({error}); "
            f"the result stays here:\n\n{delivery}"
        )
    return f"delivered to {channel}/{topic}"
