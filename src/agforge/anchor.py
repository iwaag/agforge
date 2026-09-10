"""What a forge conversation knows about itself, written in the conversation.

Until `refactor` p2 an `assetplan-` topic's plan lived in a Plane issue and
the `assetrun-` topic carried a `[selfnote][work] <project>/<issue>` pointer
into it. Understanding one request meant reading two systems and trusting
they still agreed — the same two-system cost `refactor` p1 removed from
autolab. There is no issue to point at any more, so the notes here carry the
record instead of a pointer into one:

    [selfnote][asset] <stem>              in an assetplan- topic
    [selfnote][doc] <message id>          which post is the current plan
    [selfnote][tools] <names>             the toolsets that plan was made with
    [selfnote][assetrun] <request id>     in an assetrun- topic
    [selfnote][state] <word>              where this conversation has got to
    [selfnote][result] <object key>       an asset this request produced
    [selfnote][replaces] <message id>     the request this one was opened for

**Identity is a message id.** The `[asset]` note's own id *is* the request,
and the `[assetrun]` note's own id is the run. A message id is the one thing
in Zulip no rename touches — resolving renames a topic, a human may rename it
again, a message may be moved to another channel — and `ZulipClient.message`
answers with the conversation the anchor is in *now*, `None` for one that was
deleted. So an `assetplan-robot` topic that was retired and replaced by a new
topic of the same name is a **different request**, and a deleted anchor is
*absent* rather than whatever took its name.

That is also why the run topic is named `assetrun-<stem>-a<request id>`
rather than `assetrun-<stem>`: the stem is the requester's word and is
reusable, the id is not. A re-planned request keeps its run topic (same id);
a replacement gets its own.

`[state]` is read newest-first, so a fresh attempt after a failure is not
read through an old success verdict and vice versa. `[result]` is append-only
and names the **durable object key**, never the presigned URL that outlives
it by an hour.

The `[rootchat]` note stays exactly as it was — the shared convention saying
which conversation forge is speaking on behalf of. It answers "where do I
reply"; `[assetrun]` answers "what am I running", and the delivery follows
the id, because a name-based return path does not follow a rename.

`agag.selfnote` has the format and the reason selfnotes never buy a run.
"""

from __future__ import annotations

from agag.selfnote import Conversation, note, own_rootchat, parse_note, rootchat_note

#: agforge's own selfnote tags, beside the shared `rootchat` and `served`.
ASSET_TAG = "asset"
RUN_TAG = "assetrun"
DOC_TAG = "doc"
TOOLS_TAG = "tools"
STATE_TAG = "state"
RESULT_TAG = "result"
REPLACES_TAG = "replaces"

#: What a `[tools]` note says when the plan was made with **no** toolset at
#: all. An absent note and an empty one are different answers: absent means
#: nobody recorded a selection (a hand-made plan, or one from before this
#: phase) and is answered with the whole library; this one means the
#: selection was none.
NO_TOOLS = "-"

__all__ = [
    "ASSET_TAG",
    "DOC_TAG",
    "NO_TOOLS",
    "REPLACES_TAG",
    "RESULT_TAG",
    "RUN_TAG",
    "STATE_TAG",
    "TOOLS_TAG",
    "Conversation",
    "asset_note",
    "doc_note",
    "own_asset",
    "own_doc",
    "own_replaces",
    "own_rootchat",
    "own_run",
    "own_state",
    "own_tools",
    "parse_asset",
    "parse_doc",
    "parse_replaces",
    "parse_result",
    "parse_run",
    "parse_state",
    "parse_tools",
    "replaces_note",
    "result_note",
    "results_in",
    "rootchat_note",
    "run_note",
    "state_note",
    "tools_note",
]


# --- what a conversation is -------------------------------------------------


def asset_note(stem: str) -> str:
    """`[selfnote][asset] <stem>` — this conversation is an asset request.

    The value is the requester's own word for it, readable from the topic
    name too; it is written anyway so the note says what it is to anybody
    who finds it alone. What matters is the note's **own message id**: that
    is the request.
    """
    return note(ASSET_TAG, str(stem).strip() or "-")


def parse_asset(content) -> str | None:
    """The stem an asset note names, or None for anything else."""
    return parse_note(content, ASSET_TAG)


def run_note(request_id: int) -> str:
    """`[selfnote][assetrun] <request id>` — this conversation is a run.

    The value names the request it executes, by anchor id: that is the
    execution relationship, and it is what the delivery follows home.
    """
    return note(RUN_TAG, str(int(request_id)))


def parse_run(content) -> int | None:
    """The request id a run note names, or None for anything else."""
    return _as_id(parse_note(content, RUN_TAG))


# --- what it currently says -------------------------------------------------


def doc_note(message_id: int) -> str:
    """`[selfnote][doc] <message id>` — which post is the current plan.

    A plan is re-posted when it is planned again, and the newest post in the
    topic is usually the requester talking, so "the plan" cannot be "the
    last thing said".
    """
    return note(DOC_TAG, str(int(message_id)))


def parse_doc(content) -> int | None:
    return _as_id(parse_note(content, DOC_TAG))


def tools_note(names) -> str:
    """`[selfnote][tools] toolset-image, toolset-video` — what it was planned with.

    Written beside the plan it belongs to, every time a plan is posted, so
    the pair is never half-updated.
    """
    listed = [str(name).strip() for name in names if str(name).strip()]
    return note(TOOLS_TAG, ", ".join(listed) if listed else NO_TOOLS)


def parse_tools(content) -> list[str] | None:
    """The toolset names a tools note carries, or None for anything else.

    `[]` and `None` are different answers — see `NO_TOOLS`.
    """
    value = parse_note(content, TOOLS_TAG)
    if value is None:
        return None
    if value.strip() == NO_TOOLS:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def state_note(state: str) -> str:
    """`[selfnote][state] <word>` — where this conversation has got to."""
    return note(STATE_TAG, str(state).strip())


def parse_state(content) -> str | None:
    return parse_note(content, STATE_TAG)


def result_note(key: str) -> str:
    """`[selfnote][result] <object key>` — one asset this request produced.

    The **durable** key in the object store, never the presigned URL: the
    URL expires within the hour and the object does not, so a reader months
    later re-signs the key instead of finding a dead link.
    """
    return note(RESULT_TAG, str(key).strip())


def parse_result(content) -> str | None:
    return parse_note(content, RESULT_TAG)


def replaces_note(anchor_id: int) -> str:
    """`[selfnote][replaces] <message id>` — the request this one replaces."""
    return note(REPLACES_TAG, str(int(anchor_id)))


def parse_replaces(content) -> int | None:
    return _as_id(parse_note(content, REPLACES_TAG))


# --- reading a history ------------------------------------------------------


def _as_id(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _earliest(messages, self_id: int, parse):
    """The first note of a kind this bot wrote, with its own message id.

    Earliest, because an identity is written once: a re-registration adds no
    second anchor, and a topic runs the request it was opened for.
    """
    for message in messages:
        if message.get("sender_id") != self_id:
            continue
        found = parse(message.get("content"))
        if found is not None:
            return int(message.get("id", 0)), found
    return None


def _newest(messages, self_id: int, parse):
    """The last note of a kind this bot wrote. The newest one wins."""
    for message in reversed(list(messages)):
        if message.get("sender_id") != self_id:
            continue
        found = parse(message.get("content"))
        if found is not None:
            return found
    return None


def own_asset(messages, self_id: int) -> tuple[int, str] | None:
    """`(anchor id, stem)` of the request this conversation is."""
    return _earliest(messages, self_id, parse_asset)


def own_run(messages, self_id: int) -> tuple[int, int] | None:
    """`(anchor id, request id)` of the run this conversation is."""
    return _earliest(messages, self_id, parse_run)


def own_doc(messages, self_id: int) -> int | None:
    return _newest(messages, self_id, parse_doc)


def own_tools(messages, self_id: int) -> list[str] | None:
    return _newest(messages, self_id, parse_tools)


def own_state(messages, self_id: int) -> str | None:
    return _newest(messages, self_id, parse_state)


def own_replaces(messages, self_id: int) -> int | None:
    return _newest(messages, self_id, parse_replaces)


def results_in(messages, self_id: int) -> list[str]:
    """Every object key this conversation has recorded, oldest first.

    Append-only: a second attempt adds its own key rather than replacing the
    first one's, because both objects exist and both were once delivered.
    """
    keys: list[str] = []
    for message in messages:
        if message.get("sender_id") != self_id:
            continue
        key = parse_result(message.get("content"))
        if key and key not in keys:
            keys.append(key)
    return keys
