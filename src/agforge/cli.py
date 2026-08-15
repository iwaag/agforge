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

from . import comfy_video, generate, toolsets as toolsets_module

__all__ = ["build_parser", "main"]


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

    video = commands.add_parser("video", help="video generation")
    video_actions = video.add_subparsers(dest="action", required=True)
    video_generate = video_actions.add_parser(
        "generate", help="one 5-second video from a prompt",
        description="Generate one 5-second video via ComfyUI and print its "
                    "time-limited download URL as the last line. The prompt "
                    "is the only parameter.",
    )
    comfy_video.add_arguments(video_generate)
    video_generate.set_defaults(run=_run_video_generate, parser=video_generate)

    return parser


def _run_toolsets(args: argparse.Namespace) -> None:
    # `--list` is the only thing this subcommand does; asking for it without
    # the flag is a usage error, not an empty answer.
    if not args.list:
        args.parser.error("nothing to do: pass --list")
    for line in toolsets_module.listing():
        print(line)


def _run_image_generate(args: argparse.Namespace) -> None:
    generate.run(args)


def _run_video_generate(args: argparse.Namespace) -> None:
    comfy_video.run(args)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
