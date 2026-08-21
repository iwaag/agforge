"""Resolve an agforge role and launch its configured harness.

The same shape as agautolab's `role_run`: one function that goes from a role
name to a finished run plus its `ag.agent-run.v1` record, and one table of
per-role tool grants beside it. Everything a role may touch is decided here,
not at the call site.

Since `agent_standardize` p10 a run also gets the handover that lets it read
and write the chat as this bot: `agentchat` on PATH and
`AGENTCHAT_ZULIP_ENV` naming this instance's own credentials. autolab has had
it since p6; forge needs it because its entrance answers about its own work
by reading the board. The identity travels as a path — the secret stays in
`.local/`.
"""

from __future__ import annotations

import os
import shlex
import sys
from dataclasses import replace
from pathlib import Path

from agag import selfnote
from agag.agent_config import ResolvedAgent, load_config, resolve_role
from agag.harness import run_harness, write_run_record

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
AGENTS_CONFIG = AGFORGE_ROOT / "agents.toml"
AGENTS_LOCAL_CONFIG = AGFORGE_ROOT / ".local" / "agents.local.toml"
ACE_STUDIO_ENV = AGFORGE_ROOT / ".local" / "ace-studio.env"
LOCAL_BIN = AGFORGE_ROOT / ".local" / "bin"
SCRIPTS_DIR = AGFORGE_ROOT / "scripts"
#: This instance's Zulip credentials. Whatever a run says in the chat is
#: attributable to the instance that said it.
ZULIP_ENV = AGFORGE_ROOT / ".local" / "zulip.env"
#: `agag.chat.ENV_VARIABLE`, spelled here so the run and the CLI agree.
AGENTCHAT_ENV_VARIABLE = "AGENTCHAT_ZULIP_ENV"

# The generator's grant: image/audio tooling, the shell verbs around it, and
# file access inside its workspace. `agforge` and `generate.sh` are reached
# through PATH, so the bare name is what the toolsets name and what is
# allowed here.
CLAUDE_ALLOWED_TOOLS = (
    "Bash(agforge:*)",
    "Bash(generate.sh:*)", "Bash(scripts/generate.sh:*)",
    "Bash(./scripts/generate.sh:*)",
    "Bash(sh scripts/generate.sh:*)", "Bash(uv:*)", "Bash(python3:*)",
    "Bash(pip:*)", "Bash(curl:*)", "Bash(sips:*)", "Bash(magick:*)",
    "Bash(ffmpeg:*)", "Bash(ffprobe:*)", "Bash(file:*)", "Bash(ls:*)",
    "Bash(pwd:*)", "Bash(cd:*)", "Bash(mkdir:*)", "Bash(cp:*)",
    "Bash(mv:*)", "Bash(rm:*)", "Bash(cat:*)", "Bash(head:*)",
    "Bash(tail:*)", "Bash(wc:*)", "Bash(grep:*)", "Bash(rg:*)",
    "Bash(find:*)", "Bash(sed:*)", "Bash(awk:*)", "Bash(echo:*)",
    "Bash(printf:*)", "Bash(jq:*)", "Bash(date:*)", "Bash(env:*)",
    "Bash(which:*)", "Bash(mc:*)", "Bash(git status:*)",
    "Bash(git log:*)", "Bash(git diff:*)", "Bash(git show:*)",
    "Bash(acestudio-cli:*)",
    "Read", "Write", "Edit", "Glob", "Grep", "WebFetch",
)

# A role missing from this table gets no `--allowedTools` at all from
# `build_argv`, and claude_code then sits waiting for an interactive
# permission answer until the timeout. Every new role belongs here.
ROLE_ALLOWED_TOOLS = {
    # The front reads and writes text — and runs `agforge toolsets --list`,
    # which its guide tells it to do. Without that grant it would sit on a
    # permission prompt until the timeout.
    # `agentchat` is what the entrance serving of this role reads the board
    # with — its own channel's topics, and `resolve` when it is told to close
    # one out. The assetplan serving of the same role never reaches for it;
    # its guide says nothing about the chat.
    "front": "Read,Write,Edit,Glob,Grep,Bash(agforge:*),Bash(agentchat:*)",
    "generator": " ".join(CLAUDE_ALLOWED_TOOLS),
}


def tool_environment(
    env_path: Path = ACE_STUDIO_ENV,
    bin_dir: Path | None = None,
    scripts_dir: Path | None = None,
) -> dict[str, str]:
    """The host-local tool handover: one allowlisted value, plus PATH.

    `run_harness` launches with `{**os.environ, **agent.environment}`, so this
    is where agforge's own tools become reachable by their bare names. Both
    `.local/bin` (host-installed CLIs) and `scripts/` (agforge's own
    `agforge`) are prepended, which is what lets a toolset say `agforge`
    and a run in a topic workspace resolve it.
    """
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    environment: dict[str, str] = {}
    for line in lines:
        tokens = shlex.split(line, comments=True)
        if len(tokens) == 1 and tokens[0].startswith("ACE_STUDIO_CLI="):
            value = tokens[0].split("=", 1)[1]
            if value:
                environment["ACE_STUDIO_CLI"] = value
            break
    prefix = [
        str(directory)
        for directory in (
            bin_dir if bin_dir is not None else LOCAL_BIN,
            scripts_dir if scripts_dir is not None else SCRIPTS_DIR,
        )
        if directory.is_dir()
    ]
    if prefix:
        environment["PATH"] = os.pathsep.join([*prefix, os.environ.get("PATH", "")])
    return environment


def chat_environment(
    bin_dir: Path | None = None,
    zulip_env: Path | None = None,
    home: tuple[str, str] | None = None,
    base_path: str | None = None,
) -> dict[str, str]:
    """`agentchat` reachable by name, speaking as this instance.

    The bin directory is the one holding the interpreter that runs the
    listener — in a `uv` project that is `.venv/bin`, where the `agentchat`
    console script is installed — so no deployment path is written down.

    `home` is the conversation being served. `agentchat send` writes it into
    whatever topic the run posts in as a root note, which is how an exchange
    outlives the run that started it.
    """
    directory = Path(sys.executable).parent if bin_dir is None else bin_dir
    environment = {AGENTCHAT_ENV_VARIABLE: str(zulip_env or ZULIP_ENV)}
    if home is not None:
        environment[selfnote.HOME_VARIABLE] = str(selfnote.Conversation(*home))
    if directory.is_dir():
        # Prepended to whatever `tool_environment` already built, not instead
        # of it: a run needs both `agentchat` and agforge's own tools.
        base = os.environ.get("PATH", "") if base_path is None else base_path
        environment["PATH"] = os.pathsep.join([str(directory), base])
    return environment


def resolve_agforge_role(
    role: str,
    *,
    profile_override: str | None = None,
    check_available: bool = True,
    config_path: Path | None = None,
    overlay_path: Path | None = None,
) -> ResolvedAgent:
    """Resolve one role against agforge's config pair, with its tool handover.

    The config pair is an argument, not a fixed fact: a caller that owns its
    own pair (the request service under test, pointed at the `fake` harness)
    passes it, and nothing here can silently fall back to the committed
    config and launch a real, paid harness.
    """
    config, overlay = load_config(
        config_path or AGENTS_CONFIG,
        AGENTS_LOCAL_CONFIG if overlay_path is None else overlay_path,
    )
    agent = resolve_role(
        config, overlay, role,
        profile_override=profile_override,
        check_available=check_available,
    )
    return replace(agent, environment={**agent.environment, **tool_environment()})


def run_role(
    role: str,
    prompt: str,
    *,
    cwd: Path,
    timeout: float,
    profile: str | None = None,
    transcript: Path | None = None,
    record: Path | None = None,
    home: tuple[str, str] | None = None,
    stream: bool = False,
) -> tuple[str, dict, int]:
    """Resolve `role`, run it once, and return output, record, and exit code.

    `home` is the conversation this run is serving, when there is one; it
    reaches the run as `AGENTCHAT_HOME`.
    """
    agent = resolve_agforge_role(role, profile_override=profile)
    agent = replace(agent, environment={
        **agent.environment,
        **chat_environment(home=home, base_path=agent.environment.get("PATH")),
    })
    result = run_harness(
        agent,
        prompt,
        cwd=cwd,
        timeout=timeout,
        allowed_tools=ROLE_ALLOWED_TOOLS.get(role),
        stream=stream,
        transcript_path=transcript,
    )
    run_record = {"schema": "ag.agent-run.v1", **result.meta}
    if record:
        write_run_record(record, request_id=record.stem, meta=result.meta)
    return result.output, run_record, result.exit_code
