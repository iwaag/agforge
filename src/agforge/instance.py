"""This agent instance's own name — the one place the code reads it.

`agforge` is the agent; `agforge-agstudio1` is *this running instance of it*
(`<agent>-<instance label><N>`, the label being the host for now). The name is
what the Zulip and Plane accounts, the instance's own channel, and the
`intro-<name>` topic all agree on, so it lives in a file rather than being
spelled out at each use site.

Local-only because the label carries host information: `.local/instance.toml`
holds the real name, `instance.example.toml` shows the shape. With no local
file the plain agent name is used — wrong for an instance, but visibly wrong
rather than silently absent.

The reading itself is `agag.instance.instance_name`, shared with the other
standardized agents; what is agforge's own is the three values below.
"""

from __future__ import annotations

from pathlib import Path

from agag.instance import instance_name as _instance_name

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
INSTANCE_TOML = AGFORGE_ROOT / ".local" / "instance.toml"
FALLBACK_NAME = "agforge"
INSTANCE_ENV_VAR = "AGFORGE_INSTANCE_NAME"

__all__ = [
    "AGFORGE_ROOT", "FALLBACK_NAME", "INSTANCE_ENV_VAR", "INSTANCE_TOML", "instance_name",
]


def instance_name(path: Path | None = None) -> str:
    """This instance's name, from `AGFORGE_INSTANCE_NAME` or `instance.toml`."""
    return _instance_name(
        INSTANCE_TOML if path is None else path,
        fallback=FALLBACK_NAME,
        env_var=INSTANCE_ENV_VAR,
    )
