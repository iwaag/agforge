"""Turn a Zulip DM into one agforge agentic run and DM the answer back.

The chat entrance reuses the same pipeline as the `:8092` request service
(`agent_run.run_request`): one charter, one agent, one `result.json`, one run
record. What is new here is the shape of the desire — a speaker-labeled
transcript of the visible DM conversation instead of a bare prompt.
"""

from __future__ import annotations

import json
import threading
import uuid

from . import agent_run
from .zulip import ZulipClient, dm_partners

HISTORY_MESSAGES = 50
ACK_PREFIX = "On it — working on this now."
ACK_TEMPLATE = ACK_PREFIX + " (run `{request_id}`)"

DESIRE_TEMPLATE = """\
This request arrived as a Zulip direct message. You are talking with a person
in a chat window, so answer like a chat reply: short, plain, no preamble.

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
        if message.get("sender_id") == self_id and content_raw.startswith(ACK_PREFIX):
            continue  # our own "on it" acks are noise, not conversation
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


def run_and_reply(client: ZulipClient, partners: list[int], self_id: int, log) -> None:
    request_id = uuid.uuid4().hex
    history = client.dm_history(partners, num_before=HISTORY_MESSAGES)
    transcript = format_transcript(history, self_id)
    client.send_dm(partners, ACK_TEMPLATE.format(request_id=request_id))
    log(f"chat run {request_id}: {len(history)} messages of context, partners={partners}")
    job, meta = agent_run.run_request(compose_desire(transcript), request_id=request_id)
    meta.pop("output", "")
    log(
        f"chat run {request_id}: role={meta.get('role')} profile={meta.get('profile')} "
        f"harness={meta.get('harness')} provider={meta.get('provider')} model={meta.get('model')} "
        f"cost_usd={meta.get('cost_usd')} duration_ms={meta.get('duration_ms')} "
        f"status={job.get('status')} outcome_from={meta.get('outcome_from')}"
    )
    client.send_dm(partners, reply_text(job))


def react(client: ZulipClient, message: dict, self_id: int) -> None:
    """Listener handler: answer this DM in its own thread."""
    from .zulip_listener import log

    partners = dm_partners(message, self_id)
    if not partners:
        return

    def worker() -> None:
        try:
            run_and_reply(client, partners, self_id, log)
        except Exception as error:
            log(f"chat run failed: {error!r}")
            try:
                client.send_dm(partners, f"Something broke on my side: {error}")
            except Exception as send_error:
                log(f"could not report the failure to the chat: {send_error!r}")

    threading.Thread(target=worker, daemon=True).start()
