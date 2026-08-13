"""Turn a Zulip message into one agforge agentic run and answer in place.

The chat entrance reuses the same pipeline as the `:8092` request service
(`agent_run.run_request`): one charter, one agent, one `result.json`, one run
record. What is new here is the shape of the desire — a speaker-labeled
transcript of the visible conversation (a DM narrow, or a channel topic)
instead of a bare prompt.
"""

from __future__ import annotations

import json
import threading
import uuid

from agag.zulip import ZulipClient, channel_name, dm_partners

from . import agent_run

HISTORY_MESSAGES = 50
ACK_PREFIX = "On it — working on this now."
ACK_TEMPLATE = ACK_PREFIX + " (run `{request_id}`)"

# The common sweep ack (phase 3, shared wording across agents). Posted
# synchronously on a topic match: it is what makes this bot the last poster,
# so the pull loop stops re-matching the topic while the run is in flight.
SWEEP_ACK = "Message received. Please wait for the reply."

DESIRE_TEMPLATE = """\
This request arrived in a Zulip chat. You are talking with people in a chat
window, so answer like a chat reply: short, plain, no preamble.

Recent conversation, oldest first, exactly as the participants see it on
screen. Lines marked "(you)" are your own earlier replies:

{transcript}

The last line is what to answer now; the earlier lines are context and may
carry the real subject of it ("the same bird, but blue"). If it is a request
for an asset, produce it and put the download URL in your answer — the URL is
pasted into the chat as-is, so it must be complete and unmodified. If it is a
question or small talk, just answer it; not every message needs a generation.

In the JSON you write for the caller, put the text you want sent to the chat
in a `reply` field. Everything else in that file is yours as usual.
"""


def format_transcript(messages: list[dict], self_id: int) -> str:
    lines = []
    for message in messages:
        content_raw = str(message.get("content", "")).strip()
        if message.get("sender_id") == self_id and (
            content_raw.startswith(ACK_PREFIX) or content_raw == SWEEP_ACK
        ):
            continue  # our own acks are noise, not conversation
        speaker = message.get("sender_full_name") or f"user{message.get('sender_id')}"
        if message.get("sender_id") == self_id:
            speaker = f"{speaker} (you)"
        lines.append(f"[{speaker}] {content_raw}")
    return "\n".join(lines)


def compose_desire(transcript: str) -> str:
    return DESIRE_TEMPLATE.format(transcript=transcript)


def reply_text(job: dict) -> str:
    """The agent's own answer, its shape not agreed in advance."""
    for key in ("reply", "message", "answer", "text", "detail"):
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "```json\n" + json.dumps(job, ensure_ascii=False, indent=2) + "\n```"


def conversation(client: ZulipClient, message: dict, self_id: int):
    """Where this message lives: (history_fn, send_fn, label), or None.

    A DM's conversation is its partner narrow; a channel message's is its
    topic. Both read the last `HISTORY_MESSAGES` messages and answer in place.
    """
    if message.get("type") == "stream":
        channel = channel_name(message)
        topic = str(message.get("subject", ""))
        return (
            lambda n: client.topic_history(channel, topic, num_before=n),
            lambda text: client.send_to_channel(channel, topic, text),
            f"channel={channel!r} topic={topic!r}",
        )
    partners = dm_partners(message, self_id)
    if not partners:
        return None
    return (
        lambda n: client.dm_history(partners, num_before=n),
        lambda text: client.send_dm(partners, text),
        f"partners={partners}",
    )


def run_and_reply(history_fn, send_fn, label: str, self_id: int, log, *, ack=True) -> None:
    request_id = uuid.uuid4().hex
    history = history_fn(HISTORY_MESSAGES)
    transcript = format_transcript(history, self_id)
    if ack:
        send_fn(ACK_TEMPLATE.format(request_id=request_id))
    log(f"chat run {request_id}: {len(history)} messages of context, {label}")
    job, meta = agent_run.run_request(compose_desire(transcript), request_id=request_id)
    meta.pop("output", "")
    log(
        f"chat run {request_id}: role={meta.get('role')} profile={meta.get('profile')} "
        f"harness={meta.get('harness')} provider={meta.get('provider')} model={meta.get('model')} "
        f"cost_usd={meta.get('cost_usd')} duration_ms={meta.get('duration_ms')} "
        f"status={job.get('status')} outcome_from={meta.get('outcome_from')}"
    )
    send_fn(reply_text(job))


def _spawn_run(history_fn, send_fn, label: str, self_id: int, log, *, ack: bool) -> None:
    def worker() -> None:
        try:
            run_and_reply(history_fn, send_fn, label, self_id, log, ack=ack)
        except Exception as error:
            log(f"chat run failed: {error!r}")
            try:
                send_fn(f"Something broke on my side: {error}")
            except Exception as send_error:
                log(f"could not report the failure to the chat: {send_error!r}")

    threading.Thread(target=worker, daemon=True).start()


def react(client: ZulipClient, message: dict, self_id: int) -> None:
    """DM listener handler: answer this message, in place, in its own thread."""
    from .zulip_listener import log

    place = conversation(client, message, self_id)
    if place is None:
        return
    history_fn, send_fn, label = place
    _spawn_run(history_fn, send_fn, label, self_id, log, ack=True)


def react_topic(client: ZulipClient, channel: str, topic: str) -> None:
    """Sweep handler: ack the topic synchronously, then answer it in a thread.

    The synchronous ack makes this bot the topic's last poster before the
    sweep moves on; the run's own "on it" ack is skipped since the common
    ack already said so.
    """
    from .zulip_listener import log

    self_id = int(client.whoami()["user_id"])
    send_fn = lambda text: client.send_to_channel(channel, topic, text)  # noqa: E731
    send_fn(SWEEP_ACK)
    _spawn_run(
        lambda n: client.topic_history(channel, topic, num_before=n),
        send_fn,
        f"channel={channel!r} topic={topic!r}",
        self_id,
        log,
        ack=False,
    )
