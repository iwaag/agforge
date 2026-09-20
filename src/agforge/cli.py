"""`agforge` — the one command agforge hands to its own subagents.

Everything a role is told to run lives behind this name, reached bare
through PATH (`scripts/agforge`, prepended by `role_run.tool_environment`).
The subcommands are the vocabulary the toolset documents in
`agent/toolsets/` describe:

    agforge toolsets --list       what toolsets exist, one line each
    agforge knowledge list        every knowledge source, its revision, its index
    agforge knowledge show <source>/<path>   one file of it (or a directory)
    agforge knowledge search <terms…>        lines matching every term
    agforge knowledge path <source>[/<path>] where it is on disk, to run a script
    agforge image generate "…"    SwarmUI  → presigned URL on the last line
    agforge video generate --prompt "…"   ComfyUI → the same contract
    agforge music generate --prompt "…"   ComfyUI → the same contract
    agforge video submit   --prompt "…"   queue it, print the prompt_id, return
    agforge music submit   --prompt "…"   queue it, print the prompt_id, return
    agforge comfy fetch <prompt_id>       download a finished job's outputs

`--help` on any of them is the usage information (Tool Giving); no guide
text repeats it.
"""

from __future__ import annotations

import argparse

from . import comfy_async, comfy_music, comfy_video, generate, knowledge, toolsets as toolsets_module

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

    know = commands.add_parser(
        "knowledge", help="what is known about making media, and where",
        description="Search, read and cite the knowledge sources configured "
                    "on this host: general study knowledge (what a model or "
                    "workflow is and what tests found) and its localised form "
                    "(what runs here, how, and in what state). An index row "
                    "says its own state; unverified entries are listed too.",
    )
    know_actions = know.add_subparsers(dest="action", required=True)
    know_list = know_actions.add_parser(
        "list", help="every source, its revision and its index rows",
        description="Print every configured source with its git revision and "
                    "the rows of its INDEX.md files, whole. Read it first, "
                    "then `show` what looks relevant.",
    )
    know_list.set_defaults(run=_run_knowledge_list, parser=know_list)
    know_show = know_actions.add_parser(
        "show", help="print one file, or list a directory",
        description="Print `<source>/<relative path>` as text; a directory "
                    "lists its entries. Nothing outside a source is reachable.",
    )
    know_show.add_argument("ref", help="<source>/<relative path>, e.g. localize/hud_icons/README.md")
    know_show.set_defaults(run=_run_knowledge_show, parser=know_show)
    know_search = know_actions.add_parser(
        "search", help="lines matching every term, across all sources",
        description="Case-insensitive; every term must be on the line. "
                    "Prints `<source>/<path>:<line>: <text>`, bounded.",
    )
    know_search.add_argument("terms", nargs="+", help="words to look for")
    know_search.set_defaults(run=_run_knowledge_search, parser=know_search)
    know_path = know_actions.add_parser(
        "path", help="absolute path of a source or a file in it",
        description="Where `<source>[/<relative path>]` is on disk, for "
                    "running a localised script in place, e.g. "
                    "`uv run --with pillow python \"$(agforge knowledge path "
                    "localize/hud_icons/make_icons.py)\" --help`.",
    )
    know_path.add_argument("ref", help="<source>[/<relative path>]")
    know_path.set_defaults(run=_run_knowledge_path, parser=know_path)

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
                    "is the only parameter. The run takes several minutes and "
                    "the URL appears only when it is over; wait for it.",
    )
    comfy_video.add_arguments(video_generate)
    video_generate.set_defaults(run=_run_video_generate, parser=video_generate)
    video_submit = video_actions.add_parser(
        "submit", help="queue one video and print its ComfyUI prompt_id",
        description="Queue the same video job `generate` runs, print its "
                    "ComfyUI prompt_id, and return immediately without "
                    "waiting for the render. Hand that id to the notifier "
                    "and let your run end; `agforge comfy fetch <prompt_id>` "
                    "collects the outputs afterwards.",
    )
    comfy_async.add_submit_arguments(video_submit, "what the video should show")
    video_submit.set_defaults(run=_run_video_submit, parser=video_submit)

    music = commands.add_parser("music", help="music generation")
    music_actions = music.add_subparsers(dest="action", required=True)
    music_generate = music_actions.add_parser(
        "generate", help="one music track from a prompt",
        description="Generate one music track via ComfyUI and print its "
                    "time-limited download URL as the last line. The prompt "
                    "is the only parameter. The run takes tens of seconds to "
                    "minutes and the URL appears only when it is over; wait "
                    "for it.",
    )
    comfy_music.add_arguments(music_generate)
    music_generate.set_defaults(run=_run_music_generate, parser=music_generate)
    music_submit = music_actions.add_parser(
        "submit", help="queue one track and print its ComfyUI prompt_id",
        description="Queue the same music job `generate` runs, print its "
                    "ComfyUI prompt_id, and return immediately without "
                    "waiting for the render. Hand that id to the notifier "
                    "and let your run end; `agforge comfy fetch <prompt_id>` "
                    "collects the outputs afterwards.",
    )
    comfy_async.add_submit_arguments(music_submit, "what the music should sound like")
    music_submit.set_defaults(run=_run_music_submit, parser=music_submit)

    comfy = commands.add_parser("comfy", help="a queued ComfyUI job")
    comfy_actions = comfy.add_subparsers(dest="action", required=True)
    comfy_fetch = comfy_actions.add_parser(
        "fetch", help="download the outputs of a finished job",
        description="Download every output file of a finished ComfyUI job "
                    "into a directory, keeping ComfyUI's own filenames, and "
                    "print the local paths. It never waits: ask for a job "
                    "the notifier has already reported as finished.",
    )
    comfy_async.add_fetch_arguments(comfy_fetch)
    comfy_fetch.set_defaults(run=_run_comfy_fetch, parser=comfy_fetch)

    return parser


def _run_toolsets(args: argparse.Namespace) -> None:
    # `--list` is the only thing this subcommand does; asking for it without
    # the flag is a usage error, not an empty answer.
    if not args.list:
        args.parser.error("nothing to do: pass --list")
    for line in toolsets_module.listing():
        print(line)


def _run_knowledge_list(args: argparse.Namespace) -> None:
    print(knowledge.listing())


def _run_knowledge_show(args: argparse.Namespace) -> None:
    try:
        print(knowledge.show(args.ref))
    except knowledge.KnowledgeError as error:
        args.parser.error(str(error))


def _run_knowledge_search(args: argparse.Namespace) -> None:
    hits = knowledge.search(args.terms)
    print("\n".join(hits) if hits else "no line holds every term; try fewer or other words")


def _run_knowledge_path(args: argparse.Namespace) -> None:
    try:
        print(knowledge.path_of(args.ref))
    except knowledge.KnowledgeError as error:
        args.parser.error(str(error))


def _run_image_generate(args: argparse.Namespace) -> None:
    generate.run(args)


def _run_video_generate(args: argparse.Namespace) -> None:
    comfy_video.run(args)


def _run_music_generate(args: argparse.Namespace) -> None:
    comfy_music.run(args)


def _run_video_submit(args: argparse.Namespace) -> None:
    comfy_async.run_submit("video", args)


def _run_music_submit(args: argparse.Namespace) -> None:
    comfy_async.run_submit("music", args)


def _run_comfy_fetch(args: argparse.Namespace) -> None:
    comfy_async.run_fetch(args)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
