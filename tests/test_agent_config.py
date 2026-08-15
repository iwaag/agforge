"""ag.agent-config.v1 conformance and agforge argv resolution."""

import os
import re
import sys
from pathlib import Path

import pytest

from agag import agent_config
from agforge import agent_run, role_run


EXAMPLES = Path(__file__).resolve().parents[3] / "devpolicy" / "contracts" / "agent" / "examples"

BASE = '''schema = "ag.agent-config.v1"
[models."ollama/local-model"]
[models."anthropic/claude-sonnet-5"]
[profiles.local]
harness = "agcode"
model = "ollama/local-model"
[profiles.sonnet]
harness = "claude_code"
model = "anthropic/claude-sonnet-5"
[roles.generator]
profile = "local"
requires = []
'''


def files(tmp_path: Path, committed: str = BASE, overlay: str | None = None):
    main = tmp_path / "agents.toml"
    main.write_text(committed)
    local = tmp_path / "agents.local.toml"
    if overlay is not None:
        local.write_text(overlay)
    return main, local


@pytest.mark.parametrize("project", ["agforge", "agautolab", "agdevworld"])
def test_valid_shared_contract_examples(project):
    main = EXAMPLES / "valid" / project / "agents.toml"
    local = EXAMPLES / "valid" / project / "agents.local.toml"
    config, overlay = agent_config.load_config(main, local if local.exists() else None)
    for role in config.get("roles", {}):
        resolved = agent_config.resolve_role(config, overlay, role, check_available=False)
        assert resolved.role == role
        assert resolved.harness in {"agcode", "claude_code", "fake"}


@pytest.mark.parametrize("fixture", sorted((EXAMPLES / "invalid").glob("*.toml")),
                         ids=lambda path: path.stem)
def test_invalid_shared_contract_examples_report_expected_code(fixture):
    expected = re.search(r"# EXPECT: (E_[A-Z_]+)", fixture.read_text()).group(1)
    is_overlay = fixture.name.startswith("overlay-")
    committed = EXAMPLES / "valid" / "agforge" / "agents.toml" if is_overlay else fixture
    overlay = fixture if is_overlay else None
    with pytest.raises(agent_config.AgentConfigError) as caught:
        config, local = agent_config.load_config(committed, overlay)
        for role in config.get("roles", {}):
            agent_config.resolve_role(config, local, role, check_available=False)
    assert caught.value.code == expected


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (BASE.replace('schema = "ag.agent-config.v1"\n', ""), "E_SCHEMA"),
        (BASE.replace('harness = "agcode"', 'harness = "ollama"'), "E_UNKNOWN_HARNESS"),
        (BASE.replace("ollama/local-model", "local-model"), "E_BAD_MODEL_ID"),
        (BASE.replace('model = "ollama/local-model"', 'model = "ollama/absent"'), "E_UNKNOWN_MODEL"),
        (BASE.replace('harness = "agcode"', 'harness = "claude_code"', 1), "E_INCOMPATIBLE"),
        (BASE.replace('profile = "local"', 'profile = "absent"'), "E_UNKNOWN_PROFILE"),
        (BASE.replace("requires = []", 'requires = ["ui_actions"]'), "E_CAPABILITY_UNMET"),
    ],
)
def test_invalid_contract_classes(tmp_path, body, code):
    main, local = files(tmp_path, body)
    with pytest.raises(agent_config.AgentConfigError) as caught:
        config, overlay = agent_config.load_config(main, local)
        agent_config.resolve_role(config, overlay, "generator", check_available=False)
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("overlay_body", "code"),
    [
        (
            '''schema = "ag.agent-config.v1"
[profiles.sneaky]
harness = "agcode"
model = "ollama/local-model"
''',
            "E_OVERLAY_SCOPE",
        ),
        (
            '''schema = "ag.agent-config.v1"
[local.secrets]
anthropic_api_key = "fake-literal"
''',
            "E_SECRET_VALUE",
        ),
    ],
)
def test_invalid_overlay_classes(tmp_path, overlay_body, code):
    main, local = files(tmp_path, overlay=overlay_body)
    with pytest.raises(agent_config.AgentConfigError) as caught:
        agent_config.load_config(main, local)
    assert caught.value.code == code


def test_overlay_selects_profile_and_newest_command_glob(tmp_path):
    older = tmp_path / "claude-old"
    newer = tmp_path / "claude-new"
    for command in (older, newer):
        command.write_text("#!/bin/sh\n")
        command.chmod(0o755)
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    overlay_body = f'''schema = "ag.agent-config.v1"
[local.harness.claude_code]
command_glob = "{tmp_path}/claude-*"
[roles.generator]
profile = "sonnet"
'''
    main, local = files(tmp_path, overlay=overlay_body)
    config, overlay = agent_config.load_config(main, local)
    resolved = agent_config.resolve_role(config, overlay, "generator")
    assert resolved.profile == "sonnet"
    assert resolved.harness == "claude_code"
    assert resolved.command == str(newer)
    assert agent_run.build_argv(resolved)[4:6] == ["--model", "claude-sonnet-5"]


def test_agcode_takes_the_native_model_and_keeps_the_canonical_one(tmp_path):
    """agcode needs no [local.harness.agcode] block: sys.executable is the
    default command, and it is already the interpreter importing agag."""
    overlay_body = '''schema = "ag.agent-config.v1"
[local.provider.ollama]
base_url = "http://ollama.example:11434"
'''
    main, local = files(tmp_path, overlay=overlay_body)
    config, overlay = agent_config.load_config(main, local)
    resolved = agent_config.resolve_role(config, overlay, "generator")
    assert resolved.command == sys.executable
    assert resolved.model == "ollama/local-model"     # canonical, for the record
    assert agent_run.build_argv(resolved) == [
        sys.executable, "-m", "agag.agcode",
        "--model", "local-model",                     # native, for the wire
        "--base-url", "http://ollama.example:11434",
    ]


def test_local_ace_studio_path_is_injected_without_sourcing_shell(tmp_path):
    env_file = tmp_path / "ace-studio.env"
    env_file.write_text('ACE_STUDIO_CLI="/Applications/ACE Studio.app/tool"\n')
    assert role_run.tool_environment(
        env_file, tmp_path / "absent", tmp_path / "absent"
    ) == {"ACE_STUDIO_CLI": "/Applications/ACE Studio.app/tool"}


def test_local_tool_environment_ignores_unrelated_keys(tmp_path):
    env_file = tmp_path / "ace-studio.env"
    env_file.write_text('SECRET="do-not-import"\n')
    assert role_run.tool_environment(
        env_file, tmp_path / "absent", tmp_path / "absent"
    ) == {}


def test_local_tool_bin_and_scripts_are_prepended_to_path(tmp_path):
    env_file = tmp_path / "ace-studio.env"
    env_file.write_text('ACE_STUDIO_CLI="/Applications/ACE Studio.app/tool"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    environment = role_run.tool_environment(env_file, bin_dir, scripts_dir)
    assert environment["PATH"].split(os.pathsep)[:2] == [str(bin_dir), str(scripts_dir)]


def test_unavailable_selected_harness_fails_without_fallback(tmp_path):
    overlay_body = '''schema = "ag.agent-config.v1"
[local.harness.agcode]
command = "/definitely/absent/python"
'''
    main, local = files(tmp_path, overlay=overlay_body)
    config, overlay = agent_config.load_config(main, local)
    with pytest.raises(agent_config.AgentConfigError) as caught:
        agent_config.resolve_role(config, overlay, "generator")
    assert caught.value.code == "E_UNAVAILABLE"


def test_agcode_needs_no_declared_provider_endpoint(tmp_path):
    """OpenCode could not be pointed at ollama without an explicit base_url,
    so resolution refused one. agcode has its own local default, and the
    endpoint is only supplied when it is not that default — so an overlay
    without a provider block resolves rather than failing."""
    main, local = files(tmp_path, overlay='schema = "ag.agent-config.v1"\n')
    config, overlay = agent_config.load_config(main, local)
    resolved = agent_config.resolve_role(config, overlay, "generator")
    assert (resolved.harness, resolved.provider_base_url) == ("agcode", None)
    assert "--base-url" not in agent_run.build_argv(resolved)
