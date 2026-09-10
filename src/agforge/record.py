"""One asset request, kept in the Zulip conversations it happens in.

Until `refactor` p2 this was `plane.py` + `works.py`: the plan was an issue's
description, the toolset selection was a `[TOOLS]` footer on it, the result
was an issue comment, and "finished" was a Plane state group. The chat
carried the conversation and Plane carried the record, so understanding one
request meant reading two systems and trusting they still agreed. That is
the cost `refactor` p1 removed from autolab, and this module removes it from
forge.

**A conversation is its own record.** The plan is a post in the `assetplan-`
topic that asked for it. The execution is the `assetrun-` topic forge opens
for it. The outcome and the delivered object keys are notes in both. Nothing
about a request is anywhere but Zulip and the object store.

## Two conversations, two identities

    assetplan-<stem>            the request   → [selfnote][asset]  → a<id>
    assetrun-<stem>-a<id>       its execution → [selfnote][assetrun] → r<id>

The anchors are message ids (`anchor.py`), so the record survives every
rename, resolve and move, and a deleted anchor is *absent* rather than
whatever took its name. The run topic wears the request's id, so a
replacement can take the requester's stem back without merging into the
conversation it replaced.

## Prose for people, notes for programs

The plan is an ordinary post — that is what a requester reads, and what
`prepare_workspace` writes back out as `plan.md`. Everything a program has to
answer without guessing is a selfnote, which is hidden from every chatlog and
never counts as somebody speaking, so writing the record never buys a run.

## Retirement is one route, and it is the one that matters

`retire_request` renames the request and its run out of the sweep's
vocabulary and resolves both, releasing the stem for a fresh request. What
makes it safe is the same thing that makes identity work: a late notifier
callback lands in the **old** run topic, whose `[assetrun]` note names the
**old** request by id, so the late result is delivered to the request it
belongs to — under whatever name that conversation now wears — and the new
request never receives it. A retired request whose anchor was deleted is
absent, and the delivery says so instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from agag.document import DocumentError, compose, split
from agag.execopt import Selection, exec_note
from agag.zulip import (
    RESOLVED_TOPIC_PREFIX,
    ZulipClient,
    ZulipError,
    live_topic_name,
    topic_history_across_resolve,
)

from .anchor import (
    Conversation,
    asset_note,
    doc_note,
    own_asset,
    own_doc,
    own_replaces,
    own_run,
    own_state,
    own_tools,
    replaces_note,
    result_note,
    results_in,
    rootchat_note,
    run_note,
    state_note,
    tools_note,
)

#: How much of a conversation one lookup reads. An `assetplan-` topic grows a
#: post per planning round and an `assetrun-` topic a post per attempt; both
#: stay far below this.
HISTORY_MESSAGES = 500

#: Zulip accepts an over-long message and **truncates it silently**, appending
#: `[message truncated]` — the post succeeds and the record is quietly wrong
#: (measured in `advance_mediagen_study` p6 ex1, and the reason the ComfyUI
#: notifier's callback is two lines). A plan that would be truncated is
#: refused here instead, where the caller can say so.
POST_LIMIT = 9000

#: A request's states. The newest note wins, so a fresh attempt after a
#: failure is never read through the old success verdict.
REQUEST_PLANNED = "planned"
REQUEST_DELIVERED = "delivered"
REQUEST_FAILED = "failed"
REQUEST_RETIRED = "retired"
#: Written by the **operation room**, with the human's own credential, and
#: never by forge. It is read back here anyway (`anchor.EXTERNAL_STATES`)
#: because a request somebody has accepted is finished, and forge answering
#: "where do my plans stand" must not go on calling it merely delivered.
REQUEST_ACCEPTED = "accepted"

#: A run's states. `pending` is the one that is neither: a ComfyUI job was
#: queued and the notifier will wake the run that collects it.
RUN_PENDING = "pending"
RUN_DELIVERED = "delivered"
RUN_FAILED = "failed"

ASSETPLAN_TOPIC_PREFIX = "assetplan-"
ASSETRUN_TOPIC_PREFIX = "assetrun-"
RETIRED_TOPIC_PREFIX = "retired-"

__all__ = [
    "ASSETPLAN_TOPIC_PREFIX",
    "ASSETRUN_TOPIC_PREFIX",
    "HISTORY_MESSAGES",
    "POST_LIMIT",
    "REQUEST_ACCEPTED",
    "REQUEST_DELIVERED",
    "REQUEST_FAILED",
    "REQUEST_PLANNED",
    "REQUEST_RETIRED",
    "RUN_DELIVERED",
    "RUN_FAILED",
    "RUN_PENDING",
    "RecordError",
    "Request",
    "Run",
    "assetrun_topic_name",
    "bare_topic",
    "compose_document",
    "ensure_request",
    "open_run",
    "read_request",
    "read_run",
    "record_plan",
    "record_result",
    "request_at",
    "request_label",
    "request_of_run",
    "retire_request",
    "retired_topic_name",
    "run_label",
    "set_request_state",
    "set_run_state",
    "split_document",
    "stem_of",
]


class RecordError(RuntimeError):
    """A work record could not be read or written."""


# --- documents --------------------------------------------------------------


def split_document(text: str) -> tuple[str, str]:
    try:
        return split(text)
    except DocumentError as error:
        raise RecordError(str(error)) from error


def compose_document(title: str, body: str | None) -> str:
    return compose(title, body)


# --- names ------------------------------------------------------------------


def request_label(anchor_id: int) -> str:
    """`a5912` — the request's anchor id, worn as a name."""
    return f"a{int(anchor_id)}"


def run_label(anchor_id: int) -> str:
    """`r5913` — the run's anchor id, worn as a name."""
    return f"r{int(anchor_id)}"


def bare_topic(name: str) -> str:
    """A topic name without Zulip's resolved marker."""
    return name[len(RESOLVED_TOPIC_PREFIX):] if name.startswith(RESOLVED_TOPIC_PREFIX) else name


def stem_of(topic: str) -> str:
    """The requester's own word for this request, from its topic name.

    A topic that does not carry the plan prefix keeps its whole name as the
    stem, so nothing here can silently invent one.
    """
    bare = bare_topic(topic)
    return bare[len(ASSETPLAN_TOPIC_PREFIX):] if bare.startswith(ASSETPLAN_TOPIC_PREFIX) else bare


def assetrun_topic_name(stem: str, request_id: int) -> str:
    """`assetrun-robot-a5912` — one run topic per request, named by its id.

    The stem is the requester's word and is reusable; the id is not. A
    re-planned request keeps this name (same anchor); a replacement, opened
    under the same stem, gets its own.
    """
    return f"{ASSETRUN_TOPIC_PREFIX}{stem}-{request_label(request_id)}"


def retired_topic_name(topic: str, label: str) -> str:
    """`✔ retired-assetplan-robot-a5912` — where a retired conversation goes.

    Out of the prefix vocabulary the listener sweeps *and* resolved, so
    neither a topic filter nor a sweep reaches it — and the stem it was
    wearing is released for whatever is asked next.
    """
    name = f"{RETIRED_TOPIC_PREFIX}{bare_topic(topic)}-{label}"
    return f"{RESOLVED_TOPIC_PREFIX}{name}"


# --- what the conversations say ---------------------------------------------


@dataclass(frozen=True)
class Request:
    """One asset request: its plan, where it lives, and where it has got to."""

    anchor_id: int
    stem: str
    channel: str
    topic: str
    #: The plan as the generator wrote it, `# title` heading and all.
    plan: str = ""
    #: The toolsets it was planned with. `None` means nobody recorded a
    #: selection, which is answered with the whole library; `[]` means the
    #: selection was none.
    tools: list[str] | None = None
    state: str = REQUEST_PLANNED
    replaces: int | None = None
    #: Object keys this request has delivered, oldest first.
    results: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return request_label(self.anchor_id)

    @property
    def run_topic(self) -> str:
        return assetrun_topic_name(self.stem, self.anchor_id)

    @property
    def title(self) -> str:
        try:
            return split_document(self.plan)[0] if self.plan else ""
        except RecordError:
            return ""


@dataclass(frozen=True)
class Run:
    """One execution conversation, and the request it executes."""

    anchor_id: int
    request_id: int
    channel: str
    topic: str
    state: str = ""
    results: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return run_label(self.anchor_id)


def _history(client: ZulipClient, channel: str, topic: str) -> list[dict]:
    return topic_history_across_resolve(client, channel, topic, HISTORY_MESSAGES)


def _document_from(history: list[dict], self_id: int) -> str:
    """The visible plan this conversation currently holds.

    Named by `[selfnote][doc]`, because the newest post in a request topic is
    usually the requester talking, and a re-plan leaves the old plan post
    sitting above the new one.
    """
    pointer = own_doc(history, self_id)
    if pointer is None:
        return ""
    for message in history:
        if int(message.get("id", 0)) == pointer:
            return str(message.get("content") or "").strip()
    return ""


def read_request(
    client: ZulipClient, channel: str, topic: str, self_id: int, history=None
) -> Request | None:
    """The request this conversation is, or None when it is not one."""
    messages = list(history) if history is not None else _history(client, channel, topic)
    anchored = own_asset(messages, self_id)
    if anchored is None:
        return None
    anchor_id, stem = anchored
    return Request(
        anchor_id=anchor_id,
        stem=stem,
        channel=channel,
        topic=bare_topic(topic),
        plan=_document_from(messages, self_id),
        tools=own_tools(messages, self_id),
        state=own_state(messages, self_id) or REQUEST_PLANNED,
        replaces=own_replaces(messages, self_id),
        results=tuple(results_in(messages, self_id)),
    )


def ensure_request(
    client: ZulipClient, channel: str, topic: str, self_id: int, history=None
) -> Request:
    """This conversation's request, anchoring it on first sight.

    Writing the anchor is what mints the identity: the note's own message id
    is the request from then on, whatever the topic is later called.
    """
    found = read_request(client, channel, topic, self_id, history=history)
    if found is not None:
        return found
    stem = stem_of(topic)
    live = live_topic_name(client, channel, topic)
    anchor_id = int(client.send_to_channel(channel, live, asset_note(stem)))
    return Request(anchor_id=anchor_id, stem=stem, channel=channel, topic=bare_topic(topic))


def _conversation_of(client: ZulipClient, message_id: int) -> tuple[str, str] | None:
    """`(channel, bare topic)` an anchor is in **now**, or None if it is gone.

    Every rename is followed and a deleted anchor is absent — the two
    properties that make an id a better identity than a name.
    """
    message = client.message(int(message_id))
    if not message:
        return None
    channel = str(message.get("display_recipient") or "")
    topic = str(message.get("subject") or "")
    return (channel, bare_topic(topic)) if channel and topic else None


def request_at(client: ZulipClient, request_id: int, self_id: int) -> Request | None:
    """The request an anchor id names, wherever its conversation now is."""
    where = _conversation_of(client, request_id)
    if where is None:
        return None
    channel, topic = where
    return read_request(client, channel, topic, self_id)


def read_run(
    client: ZulipClient, channel: str, topic: str, self_id: int, history=None
) -> Run | None:
    """The run this conversation is, or None when it is not one."""
    messages = list(history) if history is not None else _history(client, channel, topic)
    anchored = own_run(messages, self_id)
    if anchored is None:
        return None
    anchor_id, request_id = anchored
    return Run(anchor_id=anchor_id, request_id=request_id, channel=channel,
               topic=bare_topic(topic), state=own_state(messages, self_id) or "",
               results=tuple(results_in(messages, self_id)))


def request_of_run(client: ZulipClient, run: Run, self_id: int) -> Request | None:
    """The request a run executes, followed by id rather than by name."""
    return request_at(client, run.request_id, self_id)


# --- writing ----------------------------------------------------------------


def _post(client: ZulipClient, channel: str, topic: str, text: str) -> int:
    live = live_topic_name(client, channel, topic)
    try:
        return int(client.send_to_channel(channel, live, text))
    except ZulipError as error:
        raise RecordError(f"could not write to {channel}/{live}: {error}") from error


def record_plan(client: ZulipClient, request: Request, plan: str, tools=()) -> Request:
    """Post one plan as this request's current document, with its toolsets.

    Three writes, in this order and for this reason: the plan is visible
    prose because that is what the requester reads; `[doc]` names it so a
    re-plan does not leave two candidates; `[tools]` is written beside it
    every time, so the pair can never be half-updated.

    Registering the same plan again is a new post and a new `[doc]` note —
    the old one stays where it was, which is the history of the request.
    """
    document = plan.strip()
    if not document:
        raise RecordError("a plan with nothing in it is not a plan")
    if len(document) > POST_LIMIT:
        raise RecordError(
            f"this plan is {len(document)} characters; Zulip truncates a post over about "
            f"{POST_LIMIT} silently, so it is refused rather than half-recorded"
        )
    split_document(document)  # a plan that is not a document is refused here
    doc_id = _post(client, request.channel, request.topic, document)
    _post(client, request.channel, request.topic, doc_note(doc_id))
    listed = [str(name).strip() for name in tools if str(name).strip()]
    _post(client, request.channel, request.topic, tools_note(listed))
    _post(client, request.channel, request.topic, state_note(REQUEST_PLANNED))
    return replace(request, plan=document, tools=listed, state=REQUEST_PLANNED)


def open_run(
    client: ZulipClient, request: Request, self_id: int,
    selection: Selection | None = None,
) -> Run:
    """Open this request's execution conversation and anchor it to two things.

    The root note back to the request (the shared convention) and the
    `[assetrun]` note naming the request by id, both written before the one
    visible line — so a reader sees a sentence and a program sees a record.
    forge is its own last real speaker there, so opening the topic does not
    fire it: a post from somebody else starts the run, and what that post
    says is read.

    Idempotent by the run note: planning again finds the topic already
    anchored and only says where it is.

    `selection` is the planning conversation's frozen execution option, and
    an explicit one is snapshotted here as `[selfnote][exec]`
    (`ag.exec-options.v1` §5, autolab's move on forge's vocabulary): the run
    inherits how the plan was asked to be made. A snapshot, not a reference —
    changing the plan topic later reaches the *next* run topic it opens, and
    this one is overridden by an ordinary command posted in it, which is
    newer and therefore wins. Idempotence means the same thing here as for
    the other notes: a re-plan finds the topic anchored and writes nothing,
    so a run already under way keeps what it started with.
    """
    topic = request.run_topic
    history = _history(client, request.channel, topic)
    found = read_run(client, request.channel, topic, self_id, history=history)
    if found is not None:
        return found
    _post(client, request.channel, topic,
          rootchat_note(Conversation(request.channel, request.topic)))
    if selection is not None and selection.explicit:
        _post(client, request.channel, topic, exec_note(
            selection.option,
            Conversation(request.channel, request.topic),
            selection.message_id,
        ))
    # The run note's own id is the run, so it is the one write whose id is kept.
    anchor_id = _post(client, request.channel, topic, run_note(request.anchor_id))
    title = request.title or request.stem
    _post(client, request.channel, topic,
          f'This topic runs {request.label} "{title}". Post here to start it, saying '
          "anything you want done differently; the result is posted back here and in "
          f"{request.topic}.")
    return Run(anchor_id=anchor_id, request_id=request.anchor_id,
               channel=request.channel, topic=topic)


def set_request_state(client: ZulipClient, request: Request, state: str) -> Request:
    _post(client, request.channel, request.topic, state_note(state))
    return replace(request, state=state)


def set_run_state(client: ZulipClient, run: Run, state: str) -> Run:
    _post(client, run.channel, run.topic, state_note(state))
    return replace(run, state=state)


def record_result(client: ZulipClient, run: Run, request: Request | None, key: str) -> str:
    """Record one delivered object key in both conversations.

    Both, because they answer different questions and a reader may only be
    looking at one: the run says *this attempt produced this object*, and the
    request says *this request has these assets*. The key is durable; the
    presigned URL in the visible delivery is not.
    """
    if not key.strip():
        return ""
    _post(client, run.channel, run.topic, result_note(key))
    if request is not None:
        _post(client, request.channel, request.topic, result_note(key))
    return key.strip()


def _retire_conversation(client: ZulipClient, channel: str, topic: str, label: str) -> str:
    """Rename a conversation out of the sweep and resolve it. Its new name.

    One rename does both, because Zulip's resolve *is* a rename. Renaming
    also **releases the display name**, which is the point: Zulip has one
    topic per name in a channel, so a fresh request under the same stem
    would otherwise merge into the conversation it was meant to replace.

    A conversation with no messages cannot be renamed — there is nothing to
    PATCH — and is already nothing to serve, so its name is returned as it is.
    """
    live = live_topic_name(client, channel, topic)
    try:
        tail = client.topic_history(channel, live, num_before=1)
    except ZulipError as error:
        raise RecordError(f"could not read {channel}/{live} to retire it: {error}") from error
    if not tail:
        return live
    retired = retired_topic_name(topic, label)
    client.rename_topic(int(tail[-1]["id"]), retired)
    return retired


def retire_request(client: ZulipClient, request: Request, self_id: int) -> list[str]:
    """Retire a request and its run, releasing the stem. One line each.

    The request is marked `retired` **before** anything is renamed, so the
    state note lands in the conversation while its name is still the one
    every reader knows. Then both conversations are renamed out of the
    prefix vocabulary and resolved.

    Nothing is deleted and nothing is interrupted. A ComfyUI job still
    running is collected by the old run topic when the notifier calls back,
    and its result is delivered to this request — by anchor id, under
    whatever name this conversation now wears. That is what "late replies
    belong to the old request" means in practice.
    """
    lines: list[str] = []
    set_request_state(client, request, REQUEST_RETIRED)
    run_topic = request.run_topic
    run = read_run(client, request.channel, run_topic, self_id)
    if run is not None:
        name = _retire_conversation(client, request.channel, run_topic, run.label)
        lines.append(f"retired {request.channel}/{run_topic} → {name}")
    name = _retire_conversation(client, request.channel, request.topic, request.label)
    lines.append(f"retired {request.channel}/{request.topic} → {name}")
    return lines


def open_replacement(
    client: ZulipClient, channel: str, topic: str, self_id: int, replaced: Request,
) -> Request:
    """Open a fresh request under a name a retired one released.

    Its own anchor, and a `[replaces]` note naming what it was opened for —
    by id, because the name now belongs to this conversation. The order is
    not a preference: the old conversation must be renamed first, or the two
    merge.
    """
    request = ensure_request(client, channel, topic, self_id)
    _post(client, channel, topic, replaces_note(replaced.anchor_id))
    return replace(request, replaces=replaced.anchor_id)
