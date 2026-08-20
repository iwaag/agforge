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
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
INSTANCE_TOML = AGFORGE_ROOT / ".local" / "instance.toml"
FALLBACK_NAME = "agforge"

__all__ = ["AGFORGE_ROOT", "FALLBACK_NAME", "INSTANCE_TOML", "instance_name"]


def instance_name(path: Path | None = None) -> str:
    """This instance's name, from `AGFORGE_INSTANCE_NAME` or `instance.toml`.

    The environment variable wins so a second instance on one host can be run
    without a second checkout.
    """
    from_env = os.environ.get("AGFORGE_INSTANCE_NAME", "").strip()
    if from_env:
        return from_env
    source = INSTANCE_TOML if path is None else path
    try:
        data = tomllib.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return FALLBACK_NAME
    name = str(data.get("name", "")).strip()
    return name or FALLBACK_NAME
