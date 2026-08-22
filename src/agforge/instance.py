"""This agent instance's own name and the spec the skeleton runs it by.

`agforge` is the agent; `agforge-agstudio1` is *this running instance of it*
(`<agent>-<instance label><N>`, the label being the host for now). The name
lives in `.local/instance.toml` (`instance.example.toml` shows the shape) and
`AGFORGE_INSTANCE_NAME` overrides it — both read by `agag.agent.AgentSpec`.

What is agforge's own is `SPEC`: its short name, its root, its two topic
prefixes, and the tool handover it adds to every run (`tool_environment` in
`role_run`, attached there to avoid an import cycle).
"""

from __future__ import annotations

from pathlib import Path

from agag.agent import AgentSpec

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
FALLBACK_NAME = "agforge"

# Request topics per the zulip_channel_topic workflow. A resolved topic is
# renamed "✔ assetplan-…" and stops matching on its own.
ASSETPLAN_TOPIC_PREFIX = "assetplan-"
# Execution-trigger topics: any non-bot post fires one Work execution.
ASSETRUN_TOPIC_PREFIX = "assetrun-"

SPEC = AgentSpec(
    FALLBACK_NAME, AGFORGE_ROOT,
    plan_prefix=ASSETPLAN_TOPIC_PREFIX, run_prefix=ASSETRUN_TOPIC_PREFIX,
)
INSTANCE_TOML = SPEC.instance_toml
INSTANCE_ENV_VAR = SPEC.instance_env_var

__all__ = [
    "AGFORGE_ROOT", "ASSETPLAN_TOPIC_PREFIX", "ASSETRUN_TOPIC_PREFIX",
    "FALLBACK_NAME", "INSTANCE_ENV_VAR", "INSTANCE_TOML", "SPEC", "instance_name",
]


def instance_name(path: Path | None = None) -> str:
    """This instance's name, from `AGFORGE_INSTANCE_NAME` or `instance.toml`."""
    if path is None:
        return SPEC.instance_name()
    from agag.instance import instance_name as read

    return read(path, fallback=FALLBACK_NAME, env_var=INSTANCE_ENV_VAR)
