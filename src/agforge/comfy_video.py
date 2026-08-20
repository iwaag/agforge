"""Generate one short video via ComfyUI, upload it to MinIO, and print its URL.

The same contract as `generate.py`: prompt in, time-limited download URL on
the last line of stdout, local path on stderr. What differs is the backend —
a ComfyUI instance running an exported **API-format** workflow, which is a
dict of `id -> {class_type, inputs}`.

Only the prompt is a parameter. Everything else (model, resolution, the 5 s
duration fixed by a `PrimitiveFloat`) is whatever the exported workflow
carries; re-export the file to change it.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import requests

from . import generate
from .generate import AGFORGE_ROOT, OUT_DIR

WORKFLOW_FILE = (
    AGFORGE_ROOT / ".local" / "resources" / "comfywf" / "video" /
    "minimax_h3_t2v_turbo.json"
)

# Matched by class_type, never by node id: ids change on every re-export.
PROMPT_CLASS = "MiniMaxH3ImageToVideo"
NOISE_CLASS = "RandomNoise"

# ComfyUI's own seed range.
MAX_SEED = 2**63 - 1

# Generation takes minutes. This budget sits inside the assetrun
# generator's 1200 s run budget, with room for the upload after it.
POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 600

__all__ = [
    "add_arguments",
    "free_memory",
    "generate_video",
    "load_workflow",
    "output_references",
    "run",
    "submit",
    "wait_for_outputs",
]


def load_workflow(prompt: str, path: Path | None = None) -> dict:
    """The exported workflow with this run's prompt and a fresh seed.

    The file is git-ignored (it names local model files), so its absence is
    an ordinary configuration failure with an ordinary message.
    """
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
            node.setdefault("inputs", {})["prompt"] = prompt
            injected = True
        elif node.get("class_type") == NOISE_CLASS:
            # The exported file carries one fixed seed; left alone, every
            # run returns the same video.
            node.setdefault("inputs", {})["noise_seed"] = secrets.randbelow(MAX_SEED)
    if not injected:
        sys.exit(f"{workflow_path} has no {PROMPT_CLASS} node to take the prompt")
    return workflow


def free_memory(base: str) -> bool:
    """Ask an idle ComfyUI to unload whatever models it still holds.

    This workflow needs ~45 GiB of the 47 GiB device, so a previous run's
    resident models are enough to fail the next one — observed twice on
    2026-08-15, both times `MiniMaxH3ImageToVideo` asking for 500 MiB with
    72 MiB free. Freeing is skipped when anything is queued or running: this
    is a shared GPU, and unloading under someone else's job would trade our
    failure for theirs. Best effort — a server that will not answer this is
    reported by the run that follows, not here.
    """
    try:
        queue = requests.get(f"{base}/queue", timeout=15).json()
        if queue.get("queue_running") or queue.get("queue_pending"):
            return False
        requests.post(
            f"{base}/free", json={"unload_models": True, "free_memory": True}, timeout=60
        )
    except (requests.RequestException, ValueError):
        return False
    return True


def submit(base: str, workflow: dict) -> str:
    """Queue the workflow; answer its `prompt_id`."""
    response = requests.post(f"{base}/prompt", json={"prompt": workflow}, timeout=60)
    if response.status_code >= 400:
        sys.exit(f"ComfyUI rejected the workflow (HTTP {response.status_code}): "
                 f"{response.text[:500]}")
    data = response.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        sys.exit(f"ComfyUI returned no prompt_id: {data}")
    return str(prompt_id)


def output_references(history_entry: dict) -> list[dict]:
    """Every `{filename, subfolder, type}` the run produced, in node order.

    Which key a node reports under is the node's business (`SaveVideo` has
    changed it before), so anything with a `filename` counts.
    """
    references = []
    for node_output in (history_entry.get("outputs") or {}).values():
        if not isinstance(node_output, dict):
            continue
        for value in node_output.values():
            if not isinstance(value, list):
                continue
            references.extend(
                item for item in value
                if isinstance(item, dict) and item.get("filename")
            )
    return references


def wait_for_outputs(
    base: str, prompt_id: str, timeout: float = POLL_TIMEOUT_SECONDS
) -> list[dict]:
    """Poll `/history/<prompt_id>` until the run has an entry, then read it."""
    deadline = time.monotonic() + timeout
    while True:
        response = requests.get(f"{base}/history/{prompt_id}", timeout=30)
        response.raise_for_status()
        entry = (response.json() or {}).get(prompt_id)
        if entry:
            status = entry.get("status") or {}
            if str(status.get("status_str") or "").lower() == "error":
                sys.exit(f"ComfyUI run failed: {json.dumps(status)[:800]}")
            references = output_references(entry)
            if references:
                return references
            if status.get("completed"):
                sys.exit(f"ComfyUI run produced no output files: {json.dumps(entry)[:800]}")
        if time.monotonic() >= deadline:
            sys.exit(
                f"ComfyUI run {prompt_id} did not finish within {timeout:.0f}s; "
                f"check {base}/history/{prompt_id}"
            )
        time.sleep(POLL_INTERVAL_SECONDS)


def generate_video(comfyui_url: str, prompt: str) -> Path:
    """One video from one prompt, downloaded into `.local/out/`."""
    base = comfyui_url.rstrip("/")
    workflow = load_workflow(prompt)
    free_memory(base)
    print("generating; this takes several minutes", file=sys.stderr)
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
                           f"{Path(reference['filename']).suffix or '.mp4'}"
    local_path.write_bytes(download.content)
    return local_path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt", required=True, help="what the video should show")
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
    local_path = generate_video(comfyui_url, args.prompt)
    print(f"local: {local_path}", file=sys.stderr)
    print(generate.upload_and_presign(env, local_path, args.ttl))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
