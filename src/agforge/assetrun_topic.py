"""Execute the request an `assetrun-` topic was opened for, when somebody posts.

autolab's `workrun-` shape, on agforge's vocabulary. Until
`agent_standardize` p8 this topic was a bare button: the chatlog was never
read, and any post fired whichever eligible Work `works.next_work` happened
to pick, which is why the introduction had to ask the requester for "one
trigger, one Work — let the delivery land before the next one". That burden
is gone. The topic is opened by the assetplan flow when it registers the
plan, and it carries two selfnotes (`anchor.py`) saying which request it runs
and which `assetplan-` conversation it belongs to. A trigger is answered by
reading the topic.

Since `refactor` p2 the request it names is a **message id**, not a Plane
issue, and the plan and the toolset selection are read out of that request's
own conversation (`record.py`). Two things follow that a name could not give:
the delivery goes home by id, so it follows a rename, a resolve or a
retirement and is *absent* when the origin was deleted; and a replacement
opened under the same stem is a different request with a different run topic,
so it can neither collect this one's job nor receive its result.

So the chatlog is real input now: whoever posts says what they want done, the
same way a `workrun-` post does, and the generator gets it beside `plan.md`.

The workspace is `.local/agentws/<run label>/generator/` — per run topic, not
per request and not per attempt, and never deleted. A re-trigger rebuilds
`plan.md`, `chatlog.md` and `tools/` from the record and the topic, and leaves
`result/`/`intermediate/` as they are; there is no dirty check on purpose (the
braindump drops autolab's create/delete dance).

**Except while a job is pending.** A run that is collecting outputs
(`watching.json` is there) keeps the `plan.md` and `tools/` the attempt was
submitted with, whatever the request says now: the job in ComfyUI was queued
against *that* plan, and re-planning meanwhile must not make the collecting
run answer a question the outputs were never for.

`tools/` is what the request's `[selfnote][tools]` note names. A request with
no such note is hand-made, or predates this phase, and gets the whole library
— `[]` is a recorded selection of none and is not the same answer.

The result goes to **both** topics: the `assetrun-` one, through
`serve_topic`'s ordinary reply, and the `assetplan-` one the request anchor
resolves to, where the requester was talking.

A run may also end **without a result and without having failed**: it queued
a ComfyUI job with `agforge video submit` and left `pending.json` naming the
`prompt_id`. Then this module does the talking the generator cannot do —
it posts `@**Comfy Notifier** watch <prompt_id>` as its reply, delivers
nothing, and records the run `pending`. The notifier's callback is itself a post
in this topic, so it triggers the next run, which finds `watching.json` and
collects the outputs. That hand-off is why the generator never needs a Zulip
voice of its own (`episodes/zulip_command`, the agforge follow-up).

**A callback can arrive twice**, and the second one has no `watching.json`
left to collect — it would read as an ordinary trigger, run the generator
against the plan, submit a *new* ComfyUI job and deliver a second time for
one request. `collected.txt` is what stops that: every collected job's id is
remembered beside the workspace, and a trigger naming one is answered and
nothing else. On disk, because a callback outlives the process that asked
for the watch; a restart recovers the pending job from `watching.json` and
the finished ones from here, and submits neither again.

**Only the delivery names the trigger** (`agent_standardize` p9). Being named
is how a requester's next turn happens at all, so naming them in both places
gives them two — p8's proof watched Front tell the developer "done" twice for
one image. Harmless there; where the requester is a supercoder it is a second
run against a live repository. The delivery is the post that counts, because
it is what the requester was waiting for. The `assetrun-` reply is the
record, and `handoff=False` keeps it from handing anybody a turn.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from agag.topics import (
    TopicResult,
    chatlog_path,
    format_chatlog,
    guide as shared_guide,
    next_record_path,
    serve_topic,
)
from agag.agent import exec_options_for
from agag.execopt import Selection
from agag.selfnote import is_selfnote
from agag.zulip import ZulipClient, live_topic_name, log, topic_write

from . import generate, toolsets
from .record import (
    REQUEST_DELIVERED,
    REQUEST_FAILED,
    RUN_DELIVERED,
    RUN_FAILED,
    RUN_PENDING,
    RecordError,
    Request,
    Run,
    read_run,
    record_result,
    request_label,
    request_of_run,
    set_request_state,
    set_run_state,
)
from .role_run import AGFORGE_ROOT, SPEC, run_role
from .zulip_chat import ACK_PREFIX, SWEEP_ACK

AGENTWS_ROOT = AGFORGE_ROOT / ".local" / "agentws"
GUIDES = AGFORGE_ROOT / "agent" / "guides"
RECORDS_ROOT = AGFORGE_ROOT / ".local" / "agent"

TOOLS_DIR = "tools"

# The generator's own verdict on its run, from `assetrun_generator/guide.md`.
# The exit code stays the first-class failure signal; this is the agent
# saying so itself when the harness saw nothing wrong.
FAILURE_FLAG = "failure.flag"

# The generator's "I queued something and stopped": `{"prompt_id", "note"}`.
# It is renamed to WATCHING_FILE the moment the watch has been asked for, so
# a job is never watched twice, and removed once its outputs are in.
PENDING_FILE = "pending.json"
WATCHING_FILE = "watching.json"

# Every ComfyUI job this run topic has already collected, one id per line.
# The notifier's callback is an ordinary post, so a second one — a retry, a
# restart that re-reads the mention, somebody quoting it — would otherwise
# start a whole new generation and deliver a second time for one job. This
# file is what makes the *second* callback a no-op, and it is on disk beside
# the workspace because the callback outlives the process that asked for it.
COLLECTED_FILE = "collected.txt"
#: How much of a prompt id the notifier's first line carries.
SHORT_ID = 8

# Real work, not a planning pass: autolab's work run uses 1200 s and the
# assetplan-flow generator 900 s; this sits at the top of that range.
ASSETRUN_TIMEOUT_SECONDS = 1200

# A topic nobody anchored. Not an error and not a guess: since p8 an
# `assetrun-` topic is opened by the plan that owns it, so one that says
# nothing about itself is somebody's hand-made name and has nothing to run.
UNANCHORED_REPLY = (
    "This topic is not the run topic of any plan of mine, so there is nothing "
    "here to execute. Open an `assetplan-…` topic to plan an asset; I open its "
    "`assetrun-…` topic myself when the plan is registered, and that is the one "
    "to post in."
)
EMPTY_REPLY = "There is nothing in this topic to answer yet."

FAILED_PREFIX = "the run reported failure; what it produced follows"

# The durable half of a delivery, on its own last line. A presigned URL dies
# after `generate.DEFAULT_TTL_MINUTES`; the object behind it does not, so
# whoever reads this later re-signs the key through `POST /api/resign` instead
# of finding an expired link. The same key is written as a `[selfnote][result]`
# note in both conversations, which is the record; this line is what a person
# reading the delivery sees.
S3_KEY_MARKER = "[S3KEY]"

__all__ = [
    "AGENTWS_ROOT",
    "EMPTY_REPLY",
    "FAILED_PREFIX",
    "FAILURE_FLAG",
    "PENDING_FILE",
    "COLLECTED_FILE",
    "S3_KEY_MARKER",
    "WATCHING_FILE",
    "UNANCHORED_REPLY",
    "ListenerError",
    "collected_ids",
    "collecting_a_job",
    "deliver_to_origin",
    "duplicate_callback",
    "trigger_mention",
    "handle_assetrun",
    "pending_watch",
    "prepare_workspace",
    "remember_collected",
    "remembered_trigger",
    "result_files",
    "start_watching",
    "watch_line",
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


def workspace_dir(run: Run | str) -> Path:
    """`.local/agentws/<run label>/generator/` — this run topic's directory.

    Keyed on the run's **anchor id**, which is minted once when the topic is
    opened and is unique by construction. It used to be the Plane issue id,
    which a replacement could not mint a fresh one of; a replacement now has
    its own run topic and therefore its own workspace, so it can neither
    collect the old attempt's job nor overwrite its outputs.
    """
    label = run if isinstance(run, str) else run.label
    return AGENTWS_ROOT / label / "generator"


def collecting_a_job(run: Run | str) -> bool:
    """Whether this run is coming back for a job it already submitted.

    `watching.json` is the whole signal, and it is read **before** the
    workspace is refreshed, because that decides whether it may be.
    """
    return (workspace_dir(run) / WATCHING_FILE).is_file()


def prepare_workspace(request: Request, run: Run | str, collecting: bool = False) -> Path:
    """Build (or refresh) this run's workspace from the recorded plan.

    `plan.md` is the request's current plan post, verbatim — the document the
    generator wrote and the requester read. `tools/` is rebuilt from the
    `[tools]` note beside it: no note at all is a request nobody recorded a
    selection for and gets the whole library, and a recorded selection of
    none gets none. Both are replaced on a re-trigger; `result/` and
    `intermediate/` are left as they are.

    **`collecting` freezes both.** A run woken by the notifier is collecting
    outputs a ComfyUI job produced from the plan as it stood when the job was
    submitted. Re-planning meanwhile changes the request, and it must not
    change the attempt: the plan and tools on disk are the attempt's own, and
    they stay.

    A leftover `failure.flag` is removed here, so a re-trigger starts clean
    and the flag found after the run is this run's own verdict. So is a
    leftover `pending.json`, for the same reason. `watching.json` is *kept*:
    it is how this run learns which job it is collecting.
    """
    workspace = workspace_dir(run)
    workspace.mkdir(parents=True, exist_ok=True)
    if not collecting:
        (workspace / "plan.md").write_text(request.plan, encoding="utf-8")
        shutil.rmtree(workspace / TOOLS_DIR, ignore_errors=True)
        toolsets.place(
            toolsets.names() if request.tools is None else request.tools,
            workspace / TOOLS_DIR,
        )
    (workspace / FAILURE_FLAG).unlink(missing_ok=True)
    (workspace / PENDING_FILE).unlink(missing_ok=True)
    (workspace / "result").mkdir(exist_ok=True)
    (workspace / "intermediate").mkdir(exist_ok=True)
    return workspace


def run_generator(workspace: Path, selection: Selection | None = None) -> str:
    """One generator run in the Work's workspace, with its record.

    `selection` is this serving's frozen execution option — the command
    posted in this topic, or the snapshot the plan wrote when it opened it.
    It reaches the collecting run too, because a callback is served like any
    other post here: the *home* conversation decides, never the notifier's.
    """
    record = next_record_path(RECORDS_ROOT / "assetrun")
    output, _, exit_code = run_role(
        "generator",
        shared_guide(GUIDES, "assetrun_generator", "guide.md"),
        cwd=workspace,
        timeout=ASSETRUN_TIMEOUT_SECONDS,
        record=record,
        selection=selection,
    )
    if exit_code != 0:
        raise ListenerError(f"generator run exited {exit_code}: {output.strip()[:500]}")
    return output.strip()


def serve(context) -> TopicResult:
    """One trigger: the request this topic names, run and delivered twice.

    Everything the run needs is read off the topic and the record it points
    at — which request (`[selfnote][assetrun]`), what its plan and toolsets
    currently are, and what the trigger just asked for (the chatlog). Nothing
    is chosen from a queue and nothing is looked up in another system.
    """
    run = read_run(context.client, context.channel, context.topic, context.self_id,
                   history=context.history)
    if run is None:
        return TopicResult([UNANCHORED_REPLY])

    context.step = "loading the request"
    request = request_of_run(context.client, run, context.self_id)
    if request is None:
        # The anchor is gone, so the request is *absent* — not "whatever now
        # wears that topic name". Saying so is the honest answer and the one
        # that keeps a replacement from inheriting somebody else's run.
        return TopicResult([
            f"the request this topic runs ({request_label(run.request_id)}) is gone; "
            "plan it again in an `assetplan-…` topic"
        ])
    if not request.plan:
        return TopicResult([
            f"{request.label} has no plan recorded yet, so there is nothing to run; "
            f"ask for one in {request.topic}"
        ])
    sections = [f'running "{request.title}"']

    context.step = "preparing the workspace"
    # Read before anything is refreshed: it is what decides whether the plan
    # and tools on disk belong to this attempt or to the request as it stands.
    collecting = collecting_a_job(run)
    workspace = workspace_dir(run)
    if not collecting and (again := duplicate_callback(context, workspace)):
        # Answered and nothing else: no generator run, so no second job is
        # submitted, and no delivery, so the requester is not told twice
        # about one result. Cheap on purpose — a duplicate callback must cost
        # nothing, because a retrying notifier can send several.
        return TopicResult([
            f"`{again}` was already collected and delivered; nothing more to do. "
            "Post what you want done differently to run this again."
        ])
    workspace = prepare_workspace(request, run, collecting=collecting)
    collecting_id = watched_id(workspace) if collecting else ""
    if collecting:
        sections.append("collecting the job this run submitted; its own plan and tools stand")
    # The conversation is input, not decoration: this is where the trigger
    # says what it wants of a plan that was written some time ago.
    chatlog_path(workspace).write_text(
        format_chatlog(context.history, context.self_id, drop=is_ack), encoding="utf-8"
    )

    # Read before the run: the guide has a collecting run delete this file.
    waiting_for = remembered_trigger(workspace)

    context.step = "generator run"
    answer = run_generator(workspace, context.selection)
    # The run exiting zero is not the whole verdict: the guide tells the
    # generator to leave `failure.flag` when it knows it failed. An empty
    # `result/` is still a legitimate pure-text outcome, not a signal.
    succeeded = not (workspace / FAILURE_FLAG).exists()
    if not succeeded:
        sections.append(f"{FAILURE_FLAG} is present: the generator reports failure")

    pending = pending_watch(workspace)
    if pending and succeeded:
        # Nothing to package and nothing to close: the render is still
        # running. The reply *is* the command — the notifier reads mentions,
        # and its callback will land here and trigger the next run.
        context.step = "handing the job to the notifier"
        prompt_id, note = pending
        start_watching(workspace, trigger_mention(context), run=run, request=request)
        sections.append(watch_line(prompt_id, note))
        context.step = "recording the pending job"
        set_run_state(context.client, run, RUN_PENDING)
        sections.append(
            f"queued as `{prompt_id}` and left with the notifier; {request.label} "
            "stays open and this run's next serving collects the outputs"
        )
        return TopicResult(sections)

    context.step = "packaging the result"
    (workspace / WATCHING_FILE).unlink(missing_ok=True)
    # Before the delivery, not after: a delivery that half-succeeded must not
    # leave the job open to being collected all over again.
    remember_collected(workspace, collecting_id)
    files = result_files(workspace)
    key = ""
    if files:
        key, url = upload_result(zip_result(workspace))
        footer = s3_key_footer(key)
        delivery = (
            f"result of \"{request.title}\" ({len(files)} file(s)), "
            f"temporary download (expires in {generate.DEFAULT_TTL_MINUTES} min): "
            f"{url}\n{footer}"
        )
        sections.append(
            f"result/ holds {len(files)} file(s); zipped and uploaded as {key}"
        )
    else:
        delivery = answer
        sections.append("result/ is empty; delivering the answer text")
    if not succeeded:
        # The requester hears the same verdict the record does; whatever the
        # run did produce still travels with it.
        delivery = f"{FAILED_PREFIX}\n\n{delivery}"

    context.step = "origin delivery"
    sections.append(deliver_to_origin(context, request, delivery, waiting_for))

    context.step = "recording the outcome"
    sections.append(record_outcome(context.client, run, request, key, succeeded))
    return TopicResult(sections)


def record_outcome(
    client: ZulipClient, run: Run, request: Request, key: str, succeeded: bool
) -> str:
    """Write this attempt's verdict and its durable result into both records.

    The key first, because it is the fact; then the state, in both
    conversations, because they answer different questions — this run
    delivered, and this request has been delivered. The newest state note
    wins, so a fresh attempt after a failure is never read through the old
    success verdict, and a failure after a success is not read through that
    one either.

    Whatever fails here is reported in the summary and never raised: the
    asset has already been delivered to the requester, and losing the run's
    report because the record could not be written would be the worse
    outcome.
    """
    parts: list[str] = []
    try:
        if key:
            record_result(client, run, request, key)
            parts.append(f"recorded {key}")
        set_run_state(client, run, RUN_DELIVERED if succeeded else RUN_FAILED)
        set_request_state(
            client, request, REQUEST_DELIVERED if succeeded else REQUEST_FAILED)
    except RecordError as error:
        log(f"recording the outcome of {run.label} failed: {error!r}")
        return f"the outcome could not be recorded ({error})"
    parts.append(
        f"{run.label} is {RUN_DELIVERED if succeeded else RUN_FAILED}, "
        f"{request.label} is {REQUEST_DELIVERED if succeeded else REQUEST_FAILED}"
    )
    return "; ".join(parts)


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
        exec_options=exec_options_for(SPEC, client),
    )


def pending_watch(workspace: Path) -> tuple[str, str] | None:
    """`(prompt_id, note)` the run asked to have watched, or None.

    The file is the generator's whole vocabulary for "I am not finished and I
    have not failed" — it cannot say so in Zulip itself, and an empty
    `result/` already means "a pure-text answer", so it could not be
    overloaded to mean this.
    """
    path = workspace / PENDING_FILE
    try:
        pending = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    prompt_id = str((pending or {}).get("prompt_id") or "").strip()
    if not prompt_id:
        return None
    return prompt_id, " ".join(str(pending.get("note") or "").split())


def start_watching(
    workspace: Path, trigger: str = "", run: Run | None = None,
    request: Request | None = None,
) -> None:
    """Consume `pending.json`, remembering who is waiting for this job.

    The watch is asked for exactly once — the rename is what makes that true
    across a listener restart — and `trigger` is carried across the gap
    because the requester is about to become unreadable. The run that
    collects the outputs is triggered by the *notifier's* callback, so the
    last voice in the topic is a bot that cannot want anything; delivering to
    it names a machine and leaves the person who asked un-served. Measured
    the first time this path ran end to end.

    `run` and `request` are written beside it as the work identity this
    pending job belongs to. Nothing reads them back — the workspace is
    already named after the run, and the record is in Zulip — but a directory
    full of render files should say whose job it is holding without a lookup.
    """
    pending = workspace / PENDING_FILE
    stamp = {"trigger": trigger} if trigger else {}
    if run is not None:
        stamp["run"] = run.label
        stamp["request"] = request_label(run.request_id)
    if request is not None:
        stamp["conversation"] = f"{request.channel}/{request.topic}"
    if stamp:
        try:
            body = json.loads(pending.read_text(encoding="utf-8"))
            body.update(stamp)
            pending.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            pass  # the rename matters; the courtesy of a name does not
    pending.replace(workspace / WATCHING_FILE)


def remembered_trigger(workspace: Path) -> str:
    """Who asked for the job this run is collecting, if it is collecting one.

    Read before the generator runs, because the guide has that run delete
    `watching.json` once the outputs are in.
    """
    try:
        watching = json.loads((workspace / WATCHING_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str((watching or {}).get("trigger") or "").strip()


def collected_ids(workspace: Path) -> list[str]:
    """Every ComfyUI job this run topic has already collected."""
    try:
        text = (workspace / COLLECTED_FILE).read_text(encoding="utf-8")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def remember_collected(workspace: Path, prompt_id: str) -> None:
    """Record that this job's outputs are in. Appended, never rewritten."""
    if not prompt_id or prompt_id in collected_ids(workspace):
        return
    with (workspace / COLLECTED_FILE).open("a", encoding="utf-8") as handle:
        handle.write(f"{prompt_id}\n")


def watched_id(workspace: Path) -> str:
    """The job this run is collecting, read before the generator deletes it."""
    try:
        watching = json.loads((workspace / WATCHING_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str((watching or {}).get("prompt_id") or "").strip()


def duplicate_callback(context, workspace: Path) -> str:
    """The already-collected job this trigger is a second callback for.

    A notifier callback is an ordinary post, and an ordinary post is what
    starts a run. Nothing stops the notifier — or a restart re-reading the
    same mention, or somebody quoting the callback — from delivering it
    twice, and the second one would arrive with no `watching.json` to
    collect: an ordinary trigger, which would run the generator against the
    plan, submit a **new** ComfyUI job, and deliver a second time for one
    request.

    So a trigger that names a job this workspace has already collected is
    answered and nothing else. Only the id is matched — the full one and the
    short form the callback's first line carries — because the wording of a
    notifier post is not this module's to depend on.
    """
    known = collected_ids(workspace)
    if not known:
        return ""
    for message in reversed(list(context.history)):
        if message.get("sender_id") == context.self_id:
            continue
        if is_selfnote(message.get("content")):
            continue
        text = str(message.get("content") or "")
        return next((one for one in known
                     if one in text or one[:SHORT_ID] in text), "")
    return ""


def watch_line(prompt_id: str, note: str) -> str:
    """The notifier command, as a live mention. Never fenced: a mention in a
    code fence is not a mention, and this one has to fire."""
    line = f"@**Comfy Notifier** watch {prompt_id}"
    return f"{line} {note}" if note else line


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


def origin_of(request: Request) -> tuple[str, str]:
    """Where the requester is talking: the request's **current** conversation.

    Not the root note. The root note carries a *name*, and a name does not
    follow a rename — which is exactly what retiring a request does to it.
    `request_of_run` resolved the anchor id a moment ago, so this is where
    that conversation is now, ✔ and retirement and all; a request whose
    anchor was deleted never becomes a `Request` at all, and `serve` says so
    before ever reaching here.
    """
    return (request.channel, request.topic)


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


def deliver_to_origin(context, request: Request, delivery: str, mention: str = "") -> str:
    """Post the delivery into the `assetplan-` topic, naming who triggered it.

    The trigger came from somewhere, and whoever made it is waiting in their
    own conversation, not in this one. Naming them is not courtesy: a
    participant of a topic is served only when a post names it, so this is
    the thing that gives them their turn back — which is exactly why it has
    to be the *trigger* and not merely the last voice in the room.

    `mention` overrides the reading when the trigger is older than this run:
    a job left with the notifier is collected by a run the *notifier* woke,
    and a bot cannot be the requester.

    Said either way — the assetrun summary must survive everything, including
    a dead origin channel. The origin `assetplan-` topic may already be
    resolved, or renamed out of the way entirely by a retirement; the anchor
    was resolved to its live conversation before this call, and `topic_write`
    posts under the name it wears now. This bot being last poster there
    cannot re-trigger the assetplan sweep.
    """
    channel, topic = origin_of(request)
    trigger = mention or trigger_mention(context)
    body = f"{trigger}\n\n{delivery}" if trigger else delivery
    try:
        # Under the name it wears **now**: a post under the bare name of a
        # topic that has been resolved opens a twin beside the conversation
        # instead of landing in it.
        topic_write(live_topic_name(context.client, channel, topic), body,
                    channel=channel, client=context.client)
    except Exception as error:  # noqa: BLE001 - reported, never fatal
        log(f"origin delivery to {channel!r}/{topic!r} failed: {error!r}")
        return (
            f"could not deliver to {channel}/{topic} ({error}); "
            f"the result stays here:\n\n{delivery}"
        )
    return f"delivered to {channel}/{topic}"
