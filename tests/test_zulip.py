"""Deterministic-shell tests for the Zulip chat entrance.

Same rule as test_service.py: nothing here asserts what the agent said.
These pin the shell around the chat route — self-echo filtering, transcript
assembly, and how the agent's answer becomes a chat message.

The client and the listener loop themselves live in `agag.zulip` and are
tested there; the two generic assertions kept below are the ones the chat
route's correctness rests on.
"""

import json

from agag import zulip

from agforge import zulip_chat

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
    assert zulip.is_dm_for_us(dm(HUMAN_ID, "hi"), BOT_ID)
    assert not zulip.is_dm_for_us(dm(BOT_ID, "hi back"), BOT_ID)


def test_stream_messages_are_ignored():
    message = dm(HUMAN_ID, "hi")
    message["type"] = "stream"
    assert not zulip.is_dm_for_us(message, BOT_ID)


def test_dm_partners_excludes_the_bot():
    assert zulip.dm_partners(dm(HUMAN_ID, "hi"), BOT_ID) == [HUMAN_ID]
    group = dm(HUMAN_ID, "hi", recipients=(BOT_ID, HUMAN_ID, 9))
    assert zulip.dm_partners(group, BOT_ID) == [HUMAN_ID, 9]


def channel_message(sender_id, content, channel="FreeForge", topic="create-20260812-x"):
    return {
        "id": 2,
        "type": "stream",
        "sender_id": sender_id,
        "sender_full_name": "Forge" if sender_id == BOT_ID else "Devworld Assistant",
        "content": content,
        "display_recipient": channel,
        "subject": topic,
    }


def test_accept_takes_dms_and_live_request_topics():
    from agforge.zulip_listener import accept

    assert accept(dm(HUMAN_ID, "hi"), BOT_ID)
    assert accept(channel_message(HUMAN_ID, "make a bird"), BOT_ID)
    # topic-based, channel-agnostic: a future project channel works unchanged
    assert accept(channel_message(HUMAN_ID, "make a bird", channel="project-x"), BOT_ID)
    assert not accept(channel_message(BOT_ID, "own echo"), BOT_ID)
    assert not accept(channel_message(HUMAN_ID, "chatter", topic="requests"), BOT_ID)
    assert not accept(
        channel_message(
            HUMAN_ID, "late reply", topic=f"{zulip.RESOLVED_TOPIC_PREFIX}create-20260812-x"
        ),
        BOT_ID,
    )


def test_conversation_answers_a_channel_message_in_its_topic():
    sent = []

    class Client:
        def topic_history(self, channel, topic, num_before):
            assert (channel, topic) == ("FreeForge", "create-20260812-x")
            return [channel_message(HUMAN_ID, "make a bird")]

        def send_to_channel(self, channel, topic, text):
            sent.append((channel, topic, text))

    place = zulip_chat.conversation(Client(), channel_message(HUMAN_ID, "make a bird"), BOT_ID)
    history_fn, send_fn, label = place
    assert [m["content"] for m in history_fn(50)] == ["make a bird"]
    send_fn("here you go")
    assert sent == [("FreeForge", "create-20260812-x", "here you go")]
    assert "create-20260812-x" in label


def test_conversation_falls_back_to_the_dm_narrow():
    class Client:
        def dm_history(self, partners, num_before):
            return []

        def send_dm(self, partners, text):
            pass

    assert zulip_chat.conversation(Client(), dm(HUMAN_ID, "hi"), BOT_ID) is not None
    lonely = dm(BOT_ID, "echo", recipients=(BOT_ID,))
    assert zulip_chat.conversation(Client(), lonely, BOT_ID) is None


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
