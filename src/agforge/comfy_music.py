"""Generate one music track via ComfyUI, upload it to MinIO, and print its URL.

The same contract as `generate.py` and `comfy_video.py`: prompt in,
time-limited download URL on the last line of stdout, local path on stderr.
The backend is the same ComfyUI instance the video path uses, running an
exported **API-format** ACE-Step 1.5 workflow.

Only the prompt is a parameter — it becomes the `tags` of the text-encode
node. Everything else (lyrics, duration, bpm, key) is whatever the exported
workflow carries; re-export the file to change it.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import requests

from . import generate
from .comfy_video import (
    MAX_SEED,
    free_memory,
    submit,
    wait_for_outputs,
)
from .generate import AGFORGE_ROOT, OUT_DIR

WORKFLOW_FILE = (
    AGFORGE_ROOT / ".local" / "resources" / "comfywf" / "music" /
    "audio_ace_step_1_5_checkpoint.json"
)

# Matched by class_type, never by node id: ids change on every re-export.
# The text-encode node carries the prompt (as `tags`) and one of the two
# seeds; the sampler carries the other. Left alone, every run returns the
# same track.
PROMPT_CLASS = "TextEncodeAceStepAudio1.5"
SAMPLER_CLASS = "KSampler"

__all__ = ["add_arguments", "generate_music", "load_workflow", "run"]


def load_workflow(prompt: str, path: Path | None = None) -> dict:
    """The exported workflow with this run's prompt and fresh seeds."""
    workflow_path = WORKFLOW_FILE if path is None else path
    if not workflow_path.is_file():
        sys.exit(
            f"missing {workflow_path} — export the ComfyUI workflow in API "
            "format and save it there"
        )
    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        sys.exit(f"{workflow_path} is not valid JSON: {error}")
    if not isinstance(workflow, dict) or not workflow:
        sys.exit(f"{workflow_path} is not an API-format workflow (id -> node dict)")

    injected = False
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") == PROMPT_CLASS:
            inputs = node.setdefault("inputs", {})
            inputs["tags"] = prompt
            inputs["seed"] = secrets.randbelow(MAX_SEED)
            injected = True
        elif node.get("class_type") == SAMPLER_CLASS:
            node.setdefault("inputs", {})["seed"] = secrets.randbelow(MAX_SEED)
    if not injected:
        sys.exit(f"{workflow_path} has no {PROMPT_CLASS} node to take the prompt")
    return workflow


def generate_music(comfyui_url: str, prompt: str) -> Path:
    """One track from one prompt, downloaded into `.local/out/`."""
    base = comfyui_url.rstrip("/")
    workflow = load_workflow(prompt)
    free_memory(base)
    print("generating; this can take tens of seconds to minutes", file=sys.stderr)
    prompt_id = submit(base, workflow)
    print(f"comfyui prompt_id: {prompt_id}", file=sys.stderr)
    reference = wait_for_outputs(base, prompt_id)[0]

    query = urlencode({
        "filename": reference["filename"],
        "subfolder": reference.get("subfolder", ""),
        "type": reference.get("type", "output"),
    })
    download = requests.get(f"{base}/view?{query}", timeout=300)
    download.raise_for_status()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    local_path = OUT_DIR / f"{date.today().isoformat()}-{prompt_id[:8]}" \
                           f"{Path(reference['filename']).suffix or '.mp3'}"
    local_path.write_bytes(download.content)
    return local_path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt", required=True, help="what the music should sound like")
    parser.add_argument(
        "--ttl", type=int, default=generate.DEFAULT_TTL_MINUTES, metavar="MINUTES",
        help=f"presigned URL lifetime in minutes "
             f"(default {generate.DEFAULT_TTL_MINUTES})",
    )


def run(args: argparse.Namespace) -> None:
    if not args.prompt.strip():
        sys.exit("prompt is empty")
    env = generate.load_env()
    comfyui_url = env.get("AGFORGE_COMFYUI_URL")
    if not comfyui_url:
        sys.exit("AGFORGE_COMFYUI_URL missing from .local/.env")
    local_path = generate_music(comfyui_url, args.prompt)
    print(f"local: {local_path}", file=sys.stderr)
    print(generate.upload_and_presign(env, local_path, args.ttl))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
