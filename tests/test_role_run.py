"""Role resolution and the run record, without launching a real harness."""

import json

from agag.agent_config import ResolvedAgent
from agag.harness import HarnessResult

from agforge import role_run


def resolved(role: str) -> ResolvedAgent:
    return ResolvedAgent(
        role=role,
        profile="stub",
        harness="fake",
        provider="ollama",
        model="ollama/test",
        model_options={},
        command="agent",
        provider_base_url=None,
    )


def wire(monkeypatch, calls, *, output="answer", exit_code=0):
    monkeypatch.setattr(
        role_run, "resolve_agforge_role", lambda role, **kwargs: resolved(role)
    )
    monkeypatch.setattr(
        role_run,
        "run_harness",
        lambda agent, prompt, **kwargs: (
            calls.append((agent, prompt, kwargs))
            or HarnessResult(output, exit_code, {"role": agent.role, "outcome": "done"})
        ),
    )


def test_run_role_writes_its_run_record(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, calls)
    record = tmp_path / "records" / "run-0001.json"

    output, run_record, code = role_run.run_role(
        "front", "question", cwd=tmp_path, timeout=30, record=record
    )

    assert (output, code) == ("answer", 0)
    assert run_record["schema"] == "ag.agent-run.v1"
    written = json.loads(record.read_text())
    assert written["schema"] == "ag.agent-run.v1"
    assert written["request_id"] == "run-0001"
    assert written["role"] == "front"
    # The caller's cwd wins: the listener points each role at its generation
    # directory, and nothing here pins a fixed workspace.
    assert calls[0][2]["cwd"] == tmp_path


def test_every_role_carries_a_tool_grant(monkeypatch, tmp_path):
    """A role missing from the table gets no `--allowedTools` at all, and
    claude_code then waits for an interactive answer until the timeout."""
    calls = []
    wire(monkeypatch, calls)
    for role in ("front", "generator"):
        role_run.run_role(role, "p", cwd=tmp_path, timeout=5)
    assert [call[2]["allowed_tools"] for call in calls] == [
        role_run.ROLE_ALLOWED_TOOLS["front"],
        role_run.ROLE_ALLOWED_TOOLS["generator"],
    ]
    assert set(role_run.ROLE_ALLOWED_TOOLS) == set(
        role_run.load_config(role_run.AGENTS_CONFIG)[0]["roles"]
    )


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
    assert "Bash(generate.sh:*)" in role_run.ROLE_ALLOWED_TOOLS["generator"]
