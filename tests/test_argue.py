"""forge in an argue (`argue` p1): the mention route answers an argue
invitation through the shared participation and ignores every other mention."""

from agforge import argue, zulip_listener


class Client:
    def whoami(self):
        return {"user_id": 13, "full_name": "agforge-agstudio1"}


def test_an_argue_invitation_is_answered_by_the_shared_participation(monkeypatch):
    seen = {}
    monkeypatch.setattr(argue, "participate", lambda client, channel, topic, **kw: seen.update(kw, topic=topic) or [1])
    monkeypatch.setattr(argue, "role_context", lambda: "ROLE")
    argue.handle_mention(Client(), "argue", "argue-fish")
    assert seen["topic"] == "argue-fish" and seen["run"] is argue.run_argue and seen["spec"] is argue.SPEC


def test_a_mention_elsewhere_is_ignored(monkeypatch):
    monkeypatch.setattr(argue, "participate", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("ran")))
    argue.handle_mention(Client(), "front", "front-x")


def test_the_listener_wires_the_mention_route(monkeypatch):
    handed = {}
    monkeypatch.setattr(zulip_listener, "listener_main", lambda spec, routes, **kw: handed.update(kw))
    monkeypatch.setattr(zulip_listener, "log_only", lambda spec: True)
    zulip_listener.main()
    assert handed["on_mention"] is argue.handle_mention


def test_the_argue_role_reads_and_lists_toolsets_only():
    from pathlib import Path
    from agag.agent_config import load_config, resolve_role

    config, overlay = load_config(argue.SPEC.agents_config, Path("/nonexistent"))
    grant = resolve_role(config, overlay, "argue", check_available=False).allowed_tools
    assert grant == "Read,Glob,Grep,Bash(agforge:*)"
