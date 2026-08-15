"""`agforge` — the one command agforge hands to its own subagents.

Everything a role is told to run lives behind this name, reached bare
through PATH (`scripts/agforge`, prepended by `role_run.tool_environment`).
The subcommands are the vocabulary the toolset documents in
`agent/toolsets/` describe:

    agforge toolsets --list       what toolsets exist, one line each
    agforge image generate "…"    SwarmUI  → presigned URL on the last line
    agforge video generate --prompt "…"   ComfyUI → the same contract

`--help` on any of them is the usage information (Tool Giving); no guide
text repeats it.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import generate

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
TOOLSETS_DIR = AGFORGE_ROOT / "agent" / "toolsets"
TOOLSET_GLOB = "toolset-*.md"

DESCRIPTION_HEADING = re.compile(r"^#\s+Description\s*$", re.IGNORECASE)
ANY_HEADING = re.compile(r"^#{1,6}\s")

__all__ = ["build_parser", "describe_toolset", "list_toolsets", "main"]


def describe_toolset(text: str) -> str:
    """The body of a toolset's leading `# Description` section, on one line.

    Toolset files always open with that heading; a file that does not is
    still listed, with an empty description, rather than skipped — the front
    should see every toolset that exists.
    """
    body: list[str] = []
    collecting = False
    for line in text.splitlines():
        if DESCRIPTION_HEADING.match(line):
            collecting = True
            continue
        if collecting and ANY_HEADING.match(line):
            break
        if collecting:
            body.append(line)
    return " ".join(" ".join(body).split())


def list_toolsets(directory: Path | None = None) -> list[str]:
    """One `name, description` line per toolset file, in name order.

    This output is what the front copies into `toolsets.csv`, so it is a
    stable contract: the first comma-separated field is the toolset name
    without its extension.
    """
    root = TOOLSETS_DIR if directory is None else directory
    lines = []
    for path in sorted(root.glob(TOOLSET_GLOB)):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        lines.append(f"{path.stem}, {describe_toolset(text)}".rstrip(", "))
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agforge", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    toolsets = commands.add_parser(
        "toolsets", help="the toolsets this agforge offers",
        description="List the toolsets in agent/toolsets/.",
    )
    toolsets.add_argument(
        "--list", action="store_true",
        help="print one 'name, description' line per toolset",
    )
    toolsets.set_defaults(run=_run_toolsets, parser=toolsets)

    image = commands.add_parser("image", help="image generation")
    image_actions = image.add_subparsers(dest="action", required=True)
    image_generate = image_actions.add_parser(
        "generate", help="one image from a prompt",
        description="Generate one image via SwarmUI and print its "
                    "time-limited download URL as the last line.",
    )
    generate.add_arguments(image_generate)
    image_generate.set_defaults(run=_run_image_generate, parser=image_generate)

    return parser


def _run_toolsets(args: argparse.Namespace) -> None:
    # `--list` is the only thing this subcommand does; asking for it without
    # the flag is a usage error, not an empty answer.
    if not args.list:
        args.parser.error("nothing to do: pass --list")
    for line in list_toolsets():
        print(line)


def _run_image_generate(args: argparse.Namespace) -> None:
    generate.run(args)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
