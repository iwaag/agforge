"""Role resolution and the run record, without launching a real harness."""

import json

from agag.harness import HarnessResult

from agag import agent as skeleton

from agforge import role_run


def grants() -> dict[str, str]:
    """Each role's grant as `agents.toml` declares it (ag.agent-config.v2)."""
    config = role_run.load_config(role_run.AGENTS_CONFIG)[0]
    return {
        role: settings["allowed_tools"] if isinstance(settings["allowed_tools"], str)
        else ",".join(settings["allowed_tools"])
        for role, settings in config["roles"].items()
    }


def test_run_role_carries_the_grant_and_writes_its_run_record(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        skeleton, "run_harness",
        lambda agent, prompt, **kwargs: (
            calls.append((agent, prompt, kwargs))
            or HarnessResult("answer", 0, {"role": agent.role, "outcome": "done"})
        ),
    )
    record = tmp_path / "records" / "run-0001.json"

    output, run_record, code = role_run.run_role(
        "front", "question", cwd=tmp_path, timeout=30, record=record,
        home=("agforge-agstudio1", "hello"),
    )

    assert (output, code) == ("answer", 0)
    assert run_record["schema"] == "ag.agent-run.v1"
    written = json.loads(record.read_text())
    assert written["request_id"] == "run-0001"
    assert written["role"] == "front"
    agent, _, kwargs = calls[0]
    # The caller's cwd wins: the listener points each role at its generation
    # directory, and nothing here pins a fixed workspace.
    assert kwargs["cwd"] == tmp_path
    assert kwargs["allowed_tools"] == grants()["front"]
    # The handover: agentchat speaks as this instance, from this conversation,
    # and forge's own tools stay on PATH beside it.
    assert agent.environment["AGENTCHAT_ZULIP_ENV"] == str(role_run.ZULIP_ENV)
    assert agent.environment["AGENTCHAT_HOME"] == "agforge-agstudio1/hello"
    assert str(role_run.SCRIPTS_DIR) in agent.environment["PATH"].split(":")


def test_every_role_carries_a_tool_grant():
    """v2 makes the grant part of the role: a role without one is rejected by
    `load_config`, so this only has to say what the grants are."""
    assert set(grants()) == {"front", "generator"}
    assert "Bash(agentchat:*)" in grants()["front"]


def test_resolve_generator_obeys_the_config_paths_it_is_pointed_at(tmp_path, monkeypatch):
    """The request-service tests redirect `agent_run`'s config pair at the
    `fake` harness. A resolver that ignored it would launch the committed
    profile — a real, paid run — from inside the test suite."""
    from agforge import agent_run

    (tmp_path / "agents.toml").write_text(
        'schema = "ag.agent-config.v1"\n'
        '[models."ollama/test-model"]\n'
        "[profiles.stub]\n"
        'harness = "fake"\n'
        'model = "ollama/test-model"\n'
        "[roles.generator]\n"
        'profile = "stub"\n'
        "requires = []\n"
    )
    (tmp_path / "agents.local.toml").write_text(
        'schema = "ag.agent-config.v1"\n[local.harness.fake]\ncommand = "/bin/echo"\n'
    )
    monkeypatch.setattr(agent_run, "AGENTS_CONFIG", tmp_path / "agents.toml")
    monkeypatch.setattr(agent_run, "AGENTS_LOCAL_CONFIG", tmp_path / "agents.local.toml")

    resolved_agent = agent_run.resolve_generator()
    assert (resolved_agent.harness, resolved_agent.command) == ("fake", "/bin/echo")


def test_generator_reaches_generate_sh_through_path():
    """`generate.sh` is handed over through PATH, not by absolute path: the
    guide names it bare and the generator runs from a topic workspace."""
    environment = role_run.tool_environment()
    assert str(role_run.SCRIPTS_DIR) in environment["PATH"].split(":")
    assert (role_run.SCRIPTS_DIR / "generate.sh").is_file()
    assert "Bash(generate.sh:*)" in grants()["generator"]
