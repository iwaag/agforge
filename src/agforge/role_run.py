"""Resolve an agforge role and launch its configured harness.

The same shape as agautolab's `role_run`: one function that goes from a role
name to a finished run plus its `ag.agent-run.v1` record, and one table of
per-role tool grants beside it. Everything a role may touch is decided here,
not at the call site.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import replace
from pathlib import Path

from agag.agent_config import ResolvedAgent, load_config, resolve_role
from agag.harness import run_harness, write_run_record

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
AGENTS_CONFIG = AGFORGE_ROOT / "agents.toml"
AGENTS_LOCAL_CONFIG = AGFORGE_ROOT / ".local" / "agents.local.toml"
ACE_STUDIO_ENV = AGFORGE_ROOT / ".local" / "ace-studio.env"
LOCAL_BIN = AGFORGE_ROOT / ".local" / "bin"
SCRIPTS_DIR = AGFORGE_ROOT / "scripts"

# The generator's grant: image/audio tooling, the shell verbs around it, and
# file access inside its workspace. `generate.sh` is reached through PATH, so
# the bare name is what the guide names and what is allowed here.
CLAUDE_ALLOWED_TOOLS = (
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
    "front": "Read,Write,Edit,Glob,Grep",
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
    `generate.sh`) are prepended, which is what lets a guide say `generate.sh`
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
        project_name="agforge",
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
) -> tuple[str, dict, int]:
    """Resolve `role`, run it once, and return output, record, and exit code."""
    agent = resolve_agforge_role(role, profile_override=profile)
    result = run_harness(
        agent,
        prompt,
        cwd=cwd,
        timeout=timeout,
        allowed_tools=ROLE_ALLOWED_TOOLS.get(role),
        transcript_path=transcript,
    )
    run_record = {"schema": "ag.agent-run.v1", **result.meta}
    if record:
        write_run_record(record, request_id=record.stem, meta=result.meta)
    return result.output, run_record, result.exit_code
