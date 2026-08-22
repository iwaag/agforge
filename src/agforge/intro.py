"""Post this agforge instance's introduction to the shared agents board.

`uv run python -m agforge.intro` appends `params/intro.md` to `#agents`
under `intro-<instance>`; the mechanics are `agag.agent.intro_main`.
"""

from __future__ import annotations

from agag.intro import AGENTS_CHANNEL, intro_topic

from .instance import SPEC, instance_name

INTRO_PATH = SPEC.intro_path

__all__ = ["AGENTS_CHANNEL", "INTRO_PATH", "main", "topic"]


def topic() -> str:
    return intro_topic(instance_name())


def main() -> None:
    from agag.agent import intro_main

    intro_main(SPEC)


if __name__ == "__main__":
    main()
