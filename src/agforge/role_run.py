"""Resolve an agforge role and launch its configured harness.

The run itself is `agag.agent.run_role` — config pair, `agentchat` handover
(`AGENTCHAT_ZULIP_ENV`, `AGENTCHAT_HOME`, PATH), the role's own grant from
`agents.toml` (`ag.agent-config.v2`), the `ag.agent-run.v1` record. What is
agforge's own is `tool_environment`: `ACE_STUDIO_CLI` from `.local/ace-studio.env`
and its own tool directories (`.local/bin`, `scripts/`) on PATH, so a toolset
can say `agforge` and a run in a topic workspace resolves it.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from agag import agent as skeleton
from agag.agent import AGENTCHAT_ENV_VARIABLE, chat_environment as _chat_environment
from agag.agent_config import ResolvedAgent, load_config

from .instance import AGFORGE_ROOT, SPEC as _BARE_SPEC

AGENTS_CONFIG = _BARE_SPEC.agents_config
AGENTS_LOCAL_CONFIG = _BARE_SPEC.agents_local_config
ACE_STUDIO_ENV = AGFORGE_ROOT / ".local" / "ace-studio.env"
LOCAL_BIN = AGFORGE_ROOT / ".local" / "bin"
SCRIPTS_DIR = AGFORGE_ROOT / "scripts"
ZULIP_ENV = _BARE_SPEC.zulip_env

__all__ = [
    "ACE_STUDIO_ENV", "AGENTCHAT_ENV_VARIABLE", "AGENTS_CONFIG", "AGENTS_LOCAL_CONFIG",
    "AGFORGE_ROOT", "SPEC", "ZULIP_ENV", "chat_environment", "load_config",
    "resolve_agforge_role", "run_role", "tool_environment",
]


def tool_environment(
    env_path: Path = ACE_STUDIO_ENV,
    bin_dir: Path | None = None,
    scripts_dir: Path | None = None,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The host-local tool handover: one allowlisted value, plus PATH."""
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
        path = (base or {}).get("PATH") or os.environ.get("PATH", "")
        environment["PATH"] = os.pathsep.join([*prefix, path])
    return environment


#: The spec the skeleton runs agforge by: the bare one plus the tool handover.
SPEC = replace(_BARE_SPEC, extra_environment=lambda env: tool_environment(base=env))


def chat_environment(
    bin_dir: Path | None = None,
    zulip_env: Path | None = None,
    home: tuple[str, str] | None = None,
    base_path: str | None = None,
) -> dict[str, str]:
    """`agentchat` reachable by name, speaking as this instance (skeleton's)."""
    spec = SPEC if zulip_env is None else replace(SPEC, root=zulip_env.parent.parent)
    return _chat_environment(spec, home=home, base_path=base_path, bin_dir=bin_dir)


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
    return skeleton.resolve_spec_role(
        SPEC, role,
        profile_override=profile_override,
        check_available=check_available,
        config_path=config_path,
        overlay_path=overlay_path,
    )


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
    """Resolve `role`, run it once, and return output, record, and exit code."""
    return skeleton.run_role(
        SPEC, role, prompt,
        cwd=cwd, timeout=timeout, profile=profile, transcript=transcript,
        record=record, home=home, stream=stream,
    )
