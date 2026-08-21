"""What an `assetrun-` topic knows about itself, written in the topic.

An `assetrun-` topic used to be a bare button: any post fired the *next*
eligible Work, whichever that was, and the requester carried the burden of
"one trigger, one Work — let the delivery land before the next one". Since
`agent_standardize` p8 the topic says what it runs. forge opens it when it
registers the plan and anchors it with two selfnotes
(`agag.selfnote`), which are hidden from every chatlog and every `read`:

    [selfnote][rootchat] <channel>/assetplan-<stem>
    [selfnote][work] <project id>/<issue id>

The first is the ordinary root note every agent writes when it speaks
somewhere on behalf of one of its own conversations — here it is forge
speaking to itself, and it is what the delivery is posted back into. The
second is agforge's own tag: the Work this topic executes, so a trigger is
answered by reading the topic rather than by guessing at a queue.
"""

from __future__ import annotations

from agag.selfnote import Conversation, note, own_rootchat, parse_note, rootchat_note

#: agforge's own selfnote tag, beside the shared `rootchat` one.
WORK_TAG = "work"

__all__ = [
    "WORK_TAG",
    "Conversation",
    "assetrun_topic_name",
    "own_rootchat",
    "own_work",
    "parse_work",
    "rootchat_note",
    "work_note",
]


def work_note(project_id: str, issue_id: str) -> str:
    """`[selfnote][work] <project id>/<issue id>` — what this topic runs."""
    return note(WORK_TAG, f"{project_id}/{issue_id}")


def parse_work(content) -> tuple[str, str] | None:
    """The `(project id, issue id)` a work note names, or None."""
    value = parse_note(content, WORK_TAG)
    if not value or "/" not in value:
        return None
    project_id, issue_id = value.split("/", 1)
    project_id, issue_id = project_id.strip(), issue_id.strip()
    return (project_id, issue_id) if project_id and issue_id else None


def own_work(messages, self_id: int) -> tuple[str, str] | None:
    """The Work this bot anchored this topic to, reading its history.

    The earliest note wins, as with the root note: a topic runs the Work it
    was opened for, and a re-registration writes no second note.
    """
    for message in messages:
        if message.get("sender_id") != self_id:
            continue
        found = parse_work(message.get("content"))
        if found is not None:
            return found
    return None


def assetrun_topic_name(assetplan_topic: str, run_prefix: str, plan_prefix: str) -> str:
    """`assetplan-<stem>` → `assetrun-<stem>`; one stem, two topics.

    A topic that does not carry the plan prefix keeps its whole name as the
    stem, so nothing here can silently produce `assetrun-` on its own.
    """
    stem = assetplan_topic[len(plan_prefix):] if assetplan_topic.startswith(plan_prefix) else assetplan_topic
    return f"{run_prefix}{stem}"
