"""Deterministic-shell tests for the Zulip chat entrance.

Same rule as test_service.py: nothing here asserts what the agent said.
These pin the shell around the chat route — self-echo filtering, transcript
assembly, and how the agent's answer becomes a chat message.
"""

import json

from agforge import zulip, zulip_chat, zulip_listener

BOT_ID = 13
HUMAN_ID = 8


def dm(sender_id, content, recipients=(BOT_ID, HUMAN_ID), full_name="Developer"):
    return {
        "id": 1,
        "type": "private",
        "sender_id": sender_id,
        "sender_full_name": "Forge" if sender_id == BOT_ID else full_name,
        "content": content,
        "display_recipient": [{"id": i, "email": f"user{i}@example.invalid"} for i in recipients],
    }


def test_own_messages_are_not_reacted_to():
    assert zulip_listener.is_dm_for_us(dm(HUMAN_ID, "hi"), BOT_ID)
    assert not zulip_listener.is_dm_for_us(dm(BOT_ID, "hi back"), BOT_ID)


def test_stream_messages_are_ignored():
    message = dm(HUMAN_ID, "hi")
    message["type"] = "stream"
    assert not zulip_listener.is_dm_for_us(message, BOT_ID)


def test_dm_partners_excludes_the_bot():
    assert zulip.dm_partners(dm(HUMAN_ID, "hi"), BOT_ID) == [HUMAN_ID]
    group = dm(HUMAN_ID, "hi", recipients=(BOT_ID, HUMAN_ID, 9))
    assert zulip.dm_partners(group, BOT_ID) == [HUMAN_ID, 9]


def test_transcript_labels_speakers_and_drops_acks():
    transcript = zulip_chat.format_transcript(
        [
            dm(HUMAN_ID, "make me a red bird"),
            dm(BOT_ID, zulip_chat.ACK_TEMPLATE.format(request_id="abc")),
            dm(BOT_ID, "here it is: http://example.invalid/bird.png"),
            dm(HUMAN_ID, "same bird but blue"),
        ],
        BOT_ID,
    )
    assert transcript.splitlines() == [
        "[Developer] make me a red bird",
        "[Forge (you)] here it is: http://example.invalid/bird.png",
        "[Developer] same bird but blue",
    ]


def test_desire_carries_the_transcript():
    desire = zulip_chat.compose_desire("[Developer] hello")
    assert "[Developer] hello" in desire
    assert "`reply` field" in desire


def test_reply_prefers_the_agents_reply_field():
    assert zulip_chat.reply_text({"reply": "done ", "url": "u"}) == "done"
    assert zulip_chat.reply_text({"detail": "run failed"}) == "run failed"


def test_reply_falls_back_to_the_whole_answer():
    job = {"status": "ended", "asset": {"url": "http://example.invalid/x.png"}}
    text = zulip_chat.reply_text(job)
    assert json.loads(text.removeprefix("```json").removesuffix("```")) == job


def test_env_reader_ignores_comments_and_blank_lines(tmp_path):
    path = tmp_path / "zulip.env"
    path.write_text("# a comment\n\nZULIP_URL=https://example.invalid\nZULIP_EMAIL=b@c.invalid\n")
    assert zulip.read_env(path) == {
        "ZULIP_URL": "https://example.invalid",
        "ZULIP_EMAIL": "b@c.invalid",
    }
