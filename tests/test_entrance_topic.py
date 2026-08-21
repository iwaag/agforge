"""The entrance: a question about this instance's own work, answered by a run.

The serving discipline is `agag.topics` and is tested there. What is pinned
here is agforge's own part: the workspace it runs in, the conversation
reaching it as a file, the guide it carries, and that a failed run is an
error the topic hears about rather than silence.
"""

import pytest
from agag.topics import TopicContext

from agforge import entrance_topic

BOT_ID = 13
CHANNEL = "agforge-agstudio1"
TOPIC = "what-are-you-working-on"


def context(history=None):
    return TopicContext(
        client=None, channel=CHANNEL, topic=TOPIC,
        self_id=BOT_ID, bot_name="Forge",
        history=history if history is not None else [
            {"id": 1, "sender_id": 8, "sender_full_name": "Developer",
             "content": "list your plans and where each stands"},
        ],
    )


def wire(monkeypatch, tmp_path, answer="Two plans; one finished.", exit_code=0):
    monkeypatch.setattr(entrance_topic, "TOPICS_ROOT", tmp_path / "topics")
    monkeypatch.setattr(entrance_topic, "RECORDS_ROOT", tmp_path / "records")
    calls = {}

    def run_role(role, prompt, *, cwd, timeout, record=None, home=None,
                 transcript=None, stream=False, **kw):
        calls.update(role=role, prompt=prompt, cwd=cwd, home=home,
                     timeout=timeout, transcript=transcript, stream=stream)
        return answer, {}, exit_code

    monkeypatch.setattr(entrance_topic, "run_role", run_role)
    return calls


def test_the_front_role_answers_in_the_topics_own_workspace(monkeypatch, tmp_path):
    calls = wire(monkeypatch, tmp_path)
    result = entrance_topic.serve_entrance(context())
    assert result.sections == ["Two plans; one finished."]
    assert calls["role"] == "front"
    assert calls["cwd"] == tmp_path / "topics" / CHANNEL / TOPIC / "1" / "front"


def test_the_conversation_reaches_the_run_as_a_file(monkeypatch, tmp_path):
    calls = wire(monkeypatch, tmp_path)
    entrance_topic.serve_entrance(context())
    assert (calls["cwd"] / "chatlog.md").read_text(encoding="utf-8") == (
        "[Developer] list your plans and where each stands\n"
    )


def test_our_own_ack_is_not_conversation(monkeypatch, tmp_path):
    calls = wire(monkeypatch, tmp_path)
    entrance_topic.serve_entrance(context([
        {"id": 1, "sender_id": BOT_ID, "sender_full_name": "Forge",
         "content": entrance_topic.SWEEP_ACK},
        {"id": 2, "sender_id": 8, "sender_full_name": "Developer", "content": "well?"},
    ]))
    assert (calls["cwd"] / "chatlog.md").read_text(encoding="utf-8") == "[Developer] well?\n"


def test_what_the_run_looked_at_is_kept(monkeypatch, tmp_path):
    """An answer that skipped a topic and one that found nothing in it read
    the same. The transcript is what separates them afterwards."""
    calls = wire(monkeypatch, tmp_path)
    entrance_topic.serve_entrance(context())
    assert calls["transcript"] == calls["cwd"] / "transcript.jsonl"
    # And it must be the streamed record: without this the file holds a cost
    # report, which cannot tell a skipped topic from an empty one.
    assert calls["stream"] is True


def test_the_run_knows_which_conversation_it_is_serving(monkeypatch, tmp_path):
    """`AGENTCHAT_HOME`: anything it says elsewhere resolves back to here."""
    calls = wire(monkeypatch, tmp_path)
    entrance_topic.serve_entrance(context())
    assert calls["home"] == (CHANNEL, TOPIC)


def test_each_serving_cuts_a_new_generation(monkeypatch, tmp_path):
    calls = wire(monkeypatch, tmp_path)
    entrance_topic.serve_entrance(context())
    entrance_topic.serve_entrance(context())
    assert calls["cwd"].name == "front" and calls["cwd"].parent.name == "2"


def test_a_silent_run_still_says_something(monkeypatch, tmp_path):
    """An ack followed by silence would hide the topic from the sweep."""
    wire(monkeypatch, tmp_path, answer="   ")
    assert entrance_topic.serve_entrance(context()).sections == [entrance_topic.NO_ANSWER]


def test_a_failed_run_is_an_error_the_topic_hears_about(monkeypatch, tmp_path):
    wire(monkeypatch, tmp_path, answer="boom", exit_code=3)
    with pytest.raises(entrance_topic.EntranceError):
        entrance_topic.serve_entrance(context())


# --- the prompt and its guide ----------------------------------------------


def test_the_prompt_places_the_chatlog_names_this_instance_and_carries_the_guide(
    monkeypatch,
):
    monkeypatch.setattr(entrance_topic, "instance_name", lambda: CHANNEL)
    prompt = entrance_topic.entrance_prompt("Forge")
    assert "chatlog" in prompt and "'Forge'" in prompt
    assert CHANNEL in prompt
    assert "agentchat topics" in prompt


def test_the_guide_is_terse():
    """Same register as the assetplan guides: a reader, not a manual."""
    text = entrance_topic.guide("entrance_front", "guide.md")
    assert len([line for line in text.splitlines() if line.strip()]) <= 10


def test_the_guide_names_no_other_agents_routing():
    """What it may say is its own vocabulary. Another agent's entrance is
    learned from that agent's introduction, never from here."""
    text = entrance_topic.guide("entrance_front", "guide.md")
    for foreign in ("autolab", "workplan-", "workrun-", "pj-", "agfront", "front-"):
        assert foreign not in text
