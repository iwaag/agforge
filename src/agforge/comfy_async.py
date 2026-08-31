"""Fire-and-forget ComfyUI: queue a job, hand back its `prompt_id`, fetch later.

`agforge video generate` waits for the render inside the run. That is fine
while the wait fits in one run's budget, and it stops being fine the moment
the notifier is supposed to do the waiting: the point of posting
`@**Comfy Notifier** watch <prompt_id>` is that the run *ends* and is called
back when the job does, and a synchronous generator can never produce the id
that command needs. `zulip_command` step 4 found this the hard way — an
assetrun was asked to use the notifier and correctly answered that it could
not, because nothing it could run returns a `prompt_id`.

So this module splits the existing generators along the seam `comfy_video`
already has — `submit()` on one side, the wait and the download on the other
— and adds nothing else. The ComfyUI URL stays where it has always been, in
`.local/.env`, read here; no agent run is ever handed it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlencode

import requests

from . import comfy_music, comfy_video, generate
from .comfy_video import free_memory, output_references, submit

# What `<kind> submit` means, per media. Each backend already knows how to
# turn a prompt into its own exported workflow; async adds no new workflow.
BACKENDS = {
    "video": comfy_video,
    "music": comfy_music,
}

__all__ = [
    "add_fetch_arguments",
    "add_submit_arguments",
    "comfyui_url",
    "fetch_outputs",
    "finished_outputs",
    "run_fetch",
    "run_submit",
    "submit_job",
]


def comfyui_url() -> str:
    url = generate.load_env().get("AGFORGE_COMFYUI_URL")
    if not url:
        sys.exit("AGFORGE_COMFYUI_URL missing from .local/.env")
    return str(url).rstrip("/")


def submit_job(kind: str, prompt: str) -> str:
    """Queue one job and answer its `prompt_id` without waiting for pixels."""
    backend = BACKENDS[kind]
    base = comfyui_url()
    workflow = backend.load_workflow(prompt)
    free_memory(base)
    return submit(base, workflow)


def finished_outputs(base: str, prompt_id: str) -> list[dict]:
    """This prompt's output references, or exit saying why there are none.

    Unlike `wait_for_outputs` this never polls: by the time anything calls it
    the notifier has already said the job reached a terminal state, and a
    fetch that quietly blocked for ten minutes would put the waiting back
    inside the run that just avoided it.
    """
    response = requests.get(f"{base}/history/{prompt_id}", timeout=30)
    response.raise_for_status()
    entry = (response.json() or {}).get(prompt_id)
    if not entry:
        sys.exit(
            f"ComfyUI has no history for {prompt_id}: it is still queued, or "
            "ComfyUI restarted and lost it"
        )
    status = entry.get("status") or {}
    if str(status.get("status_str") or "").lower() == "error":
        sys.exit(f"ComfyUI run failed: {json.dumps(status)[:800]}")
    references = output_references(entry)
    if not references:
        sys.exit(f"ComfyUI run {prompt_id} produced no output files")
    return references


def fetch_outputs(prompt_id: str, into: Path) -> list[Path]:
    """Download every output of a finished prompt into `into`."""
    base = comfyui_url()
    into.mkdir(parents=True, exist_ok=True)
    written = []
    for reference in finished_outputs(base, prompt_id):
        query = urlencode({
            "filename": reference["filename"],
            "subfolder": reference.get("subfolder", ""),
            "type": reference.get("type", "output"),
        })
        download = requests.get(f"{base}/view?{query}", timeout=300)
        download.raise_for_status()
        # ComfyUI's own filename is kept: a 125-frame graph names its frames
        # in order, and renaming them here would destroy that order.
        target = into / Path(str(reference["filename"])).name
        target.write_bytes(download.content)
        written.append(target)
    return written


def add_submit_arguments(parser: argparse.ArgumentParser, what: str) -> None:
    parser.add_argument("--prompt", required=True, help=what)


def add_fetch_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("prompt_id", help="the id `submit` printed")
    parser.add_argument(
        "--into", type=Path, default=Path("result"), metavar="DIR",
        help="directory to download into (default: %(default)s)",
    )


def run_submit(kind: str, args: argparse.Namespace) -> None:
    if not args.prompt.strip():
        sys.exit("prompt is empty")
    print(submit_job(kind, args.prompt))


def run_fetch(args: argparse.Namespace) -> None:
    written = fetch_outputs(args.prompt_id, args.into)
    for path in written:
        print(path)
