"""The entrance: since `agag_builder` p1 the serving is `agag.entrance`
(tested there). What is agforge's own is its guide and the wiring."""

from agag import entrance as shared

from agforge import entrance_topic
from agforge.role_run import SPEC

CHANNEL = "agforge-agstudio1"


def test_the_prompt_places_the_chatlog_names_this_instance_and_carries_the_guide(
    monkeypatch,
):
    monkeypatch.setenv(SPEC.instance_env_var, CHANNEL)
    prompt = entrance_topic.entrance_prompt("Forge")
    assert "chatlog" in prompt and "'Forge'" in prompt
    assert CHANNEL in prompt
    assert "agentchat topics" in prompt
    assert "assetplan-" in prompt  # forge's own guide, not the built-in default


def test_forge_has_its_own_guide_and_the_skeleton_prefers_it():
    own = (SPEC.guides / "entrance_front" / "guide.md").read_text(encoding="utf-8").strip()
    assert shared.entrance_guide(SPEC) == own
    assert own != shared.default_guide(SPEC).strip()


def test_the_guide_is_terse():
    """Same register as the assetplan guides: a reader, not a manual."""
    text = shared.entrance_guide(SPEC)
    assert len([line for line in text.splitlines() if line.strip()]) <= 10


def test_the_guide_names_no_other_agents_routing():
    """What it may say is its own vocabulary. Another agent's entrance is
    learned from that agent's introduction, never from here."""
    text = shared.entrance_guide(SPEC)
    for foreign in ("autolab", "workplan-", "workrun-", "pj-", "agfront", "front-"):
        assert foreign not in text


def test_handle_entrance_serves_through_the_shared_skeleton(monkeypatch):
    seen = []
    monkeypatch.setattr(
        shared, "serve_topic",
        lambda client, channel, topic, handler, **kw: seen.append((channel, topic, kw)),
    )
    entrance_topic.handle_entrance(object(), CHANNEL, "question")
    assert seen[0][:2] == (CHANNEL, "question")
    assert seen[0][2]["ack_text"] == shared.SWEEP_ACK
