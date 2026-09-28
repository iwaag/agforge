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
    assert "assetplan-" in prompt  # forge's prefixes, filled into the default vocabulary


def test_forge_needs_no_guide_of_its_own_for_the_entrance():
    """agent_guide p2 step 3: forge's own entrance guide was pyagag's default
    with its prefixes filled in, word for word. The file is gone and the
    default vocabulary, under the shared fixed half, says the same."""
    assert not (SPEC.guides / "entrance_front" / "guide.md").exists()
    text = shared.entrance_guide(SPEC)
    assert text.endswith(shared.default_guide(SPEC))
    assert "`assetplan-…` is a plan, `assetrun-…` is its run" in text


def test_the_fixed_half_is_said_once():
    text = shared.entrance_guide(SPEC)
    assert text.count("posts your answer twice") == 1 and text.count("marked finished") == 1


def test_the_guide_names_no_other_agents_routing():
    """What it may say is its own vocabulary. Another agent's entrance is
    learned from that agent's introduction, never from here."""
    text = shared.entrance_guide(SPEC)
    for foreign in ("autolab", "workplan-", "workrun-", "pj-", "agfront", "front-"):
        assert foreign not in text


class Whoami:
    """The one read the shared entrance makes of the client before serving:
    the Zulip *full name* this instance is mentioned by, which is what an
    execution-option command has to be addressed to."""

    def whoami(self):
        return {"user_id": 13, "full_name": "Forge"}


def test_handle_entrance_serves_through_the_shared_skeleton(monkeypatch):
    seen = []
    monkeypatch.setattr(
        shared, "serve_topic",
        lambda client, channel, topic, handler, **kw: seen.append((channel, topic, kw)),
    )
    entrance_topic.handle_entrance(Whoami(), CHANNEL, "question")
    assert seen[0][:2] == (CHANNEL, "question")
    assert seen[0][2]["ack_text"] == shared.SWEEP_ACK


def test_the_entrance_publishes_forges_menu_and_obeys_it(monkeypatch):
    """The entrance is work like any other, so it runs under the topic's
    option — an option that covered the planning but not the question would
    be a menu that lies (`refactor` p3 ex1 step 2)."""
    seen = []
    monkeypatch.setattr(
        shared, "serve_topic",
        lambda client, channel, topic, handler, **kw: seen.append((channel, topic, kw)),
    )
    entrance_topic.handle_entrance(Whoami(), CHANNEL, "question")
    published = seen[0][2]["exec_options"]
    assert published is not None
    assert published.bot == "Forge"
    assert "agy" in published.names and "default" in published.names
