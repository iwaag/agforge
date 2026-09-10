"""This agent instance's own name and the spec the skeleton runs it by.

`agforge` is the agent; `agforge-agstudio1` is *this running instance of it*
(`<agent>-<instance label><N>`, the label being the host for now). The name
lives in `.local/instance.toml` (`instance.example.toml` shows the shape) and
`AGFORGE_INSTANCE_NAME` overrides it — both read by `agag.agent.AgentSpec`.

What is agforge's own is `SPEC`: its short name, its root, its two topic
prefixes, the tool handover it adds to every run (`tool_environment` in
`role_run`, attached there to avoid an import cycle), and — since `refactor`
p3 ex1 — its published **execution options** (`ag.exec-options.v1`).

The options are derived from `agents.toml`, so a name whose profile is gone
is never advertised. They are about *how forge thinks*, and the wording says
so: an execution option chooses the harness and model that read the request,
plan it and drive the tools. **It never chooses the media model.** Which
image, video or music model produces the asset is decided in the plan and
named by the toolset, so asking forge to run on `agy` must not quietly turn
a Wan video request into something else.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

from agag.agent import AgentSpec
from agag.execopt import Option

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
FALLBACK_NAME = "agforge"

# Request topics per the zulip_channel_topic workflow. A resolved topic is
# renamed "✔ assetplan-…" and stops matching on its own.
ASSETPLAN_TOPIC_PREFIX = "assetplan-"
# Execution-trigger topics: any non-bot post fires one Work execution.
ASSETRUN_TOPIC_PREFIX = "assetrun-"

#: What forge is willing to be asked for: `(profile name, usage pool, phrase)`.
#: The profile name is the public name — one-to-one, the contract's suggested
#: start — and `exec_options` publishes one only if `agents.toml` has it.
#: `sonnet`, `local` and `stub` stay private: `sonnet` is how forge is wired,
#: `local` is a laptop experiment, and `stub` is the test harness — a menu
#: that offers `fake` is a menu that lies.
PUBLIC_PROFILES = (
    ("agy", "antigravity", "Antigravity CLI (`agy`), Gemini 3.8 Flash"),
    ("agy-claude", "antigravity", "Antigravity CLI (`agy`), Claude Sonnet 4.6"),
)
#: Both of forge's roles, and the whole shape of one request: the front that
#: reads the conversation and writes the spec, the generator that plans it and
#: the generator that executes the plan — including the run a ComfyUI callback
#: brings back. Said in one phrase because a requester who selects an option
#: for a request means the request, not one of its four runs.
COVERS = (
    "everything I do for a request — entrance replies, asset planning, "
    "generation, and collection after a callback. Not the media model"
)
#: What running under no selection costs. Published because a threshold like
#: "until the pool is 70 % used" cannot be judged against a default that
#: declines to name a pool.
DEFAULT_OPTION_DETAIL = ("anthropic", "my configured defaults — Claude Sonnet 5 through claude_code")
#: The two roles `COVERS` is a sentence about — the front that reads a
#: conversation and the generator that plans and executes — and the roles the
#: published pool is **derived** from (`agag.execpool`).
EXEC_ROLES = ("front", "generator")


def configured_profiles(path: Path | None = None) -> frozenset[str]:
    """The profile names `agents.toml` declares.

    Read straight rather than through the validating loader: this runs at
    import time, and a schema complaint here would take the listener down
    while the only fact needed is which names exist.
    """
    try:
        data = tomllib.loads((path or (AGFORGE_ROOT / "agents.toml")).read_text(encoding="utf-8"))
        return frozenset(data.get("profiles", {}))
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return frozenset()


def exec_options(path: Path | None = None) -> tuple[Option, ...]:
    """forge's menu, from the profiles it is actually configured with.

    Publishing nothing when the config cannot be read leaves a reader at
    *unknown*, which is the honest answer for an instance that cannot say —
    and better than advertising a name that would fail at execution time.
    """
    profiles = configured_profiles(path)
    if not profiles:
        return ()
    pool, summary = DEFAULT_OPTION_DETAIL
    return (
        Option("default", pool, COVERS, summary),
        *(
            Option(name, option_pool, COVERS, option_summary)
            for name, option_pool, option_summary in PUBLIC_PROFILES
            if name in profiles
        ),
    )


SPEC = AgentSpec(
    FALLBACK_NAME, AGFORGE_ROOT,
    plan_prefix=ASSETPLAN_TOPIC_PREFIX, run_prefix=ASSETRUN_TOPIC_PREFIX,
    exec_options=exec_options(),
    exec_roles=EXEC_ROLES,
)
INSTANCE_TOML = SPEC.instance_toml
INSTANCE_ENV_VAR = SPEC.instance_env_var

__all__ = [
    "AGFORGE_ROOT", "ASSETPLAN_TOPIC_PREFIX", "ASSETRUN_TOPIC_PREFIX", "COVERS",
    "DEFAULT_OPTION_DETAIL", "FALLBACK_NAME", "INSTANCE_ENV_VAR", "INSTANCE_TOML",
    "PUBLIC_PROFILES", "SPEC", "configured_profiles", "exec_options", "instance_name",
]


def instance_name(path: Path | None = None) -> str:
    """This instance's name, from `AGFORGE_INSTANCE_NAME` or `instance.toml`."""
    if path is None:
        return SPEC.instance_name()
    from agag.instance import instance_name as read

    return read(path, fallback=FALLBACK_NAME, env_var=INSTANCE_ENV_VAR)
