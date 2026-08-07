# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "boto3", "requests"]
# ///
"""agforge request service — intent-level HTTP API over the generate pipeline.

Contract (see pj-agdev/devdocs/episodes/connect_world_and_forge/plan.md):

    POST /api/requests      { "desire": "<prompt text>" }
                            -> 202 { "request_id": "..." }
    GET  /api/requests/{id} -> { "status": "working" | "done" | "failed",
                                 "artifacts": [ { "kind": "image", "url": "..." } ],
                                 "detail": "<present on failed>" }
    GET  /healthz           -> { "ok": true }

Jobs are held in memory only and vanish on restart. Each request runs a
bounded agent pipeline in a worker thread (see
pj-agdev/devdocs/episodes/agforge/agentify/plan.md):

    interpret (one LLM shot, service/interpret.py)
      -> validate dims -> generate.sh --width/--height -> verify actual pixels
      -> resize when acceptable / fail honestly

Failure detail prefixes callers can dispatch on textually:
`refused: ...` (agforge cannot honor the desire), `unsatisfied: ...`
(generation could not be made to match the desire), `interpreter error: ...`,
plus raw pipeline errors.

Run: scripts under service/ read no CLI args; port comes from
AGFORGE_SERVICE_PORT (default 8092). Test hooks: AGFORGE_GENERATE_CMD
replaces generate.sh, AGFORGE_INTERPRET_CMD replaces the LLM call.
"""

import json
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

AGFORGE_ROOT = Path(__file__).resolve().parent.parent
GENERATE = AGFORGE_ROOT / "scripts" / "generate.sh"
PORT = int(os.environ.get("AGFORGE_SERVICE_PORT", "8092"))
STDERR_TAIL_CHARS = 800
JOB_BUDGET_SECONDS = 900
ASPECT_TOLERANCE = 0.02

sys.path.insert(0, str(AGFORGE_ROOT / "service"))
sys.path.insert(0, str(AGFORGE_ROOT / "scripts"))
import generate as generate_module  # noqa: E402
import interpret  # noqa: E402

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


class PipelineError(Exception):
    """A pipeline stage failed; str() is the caller-facing detail."""


def log(message: str) -> None:
    print(message, file=sys.stderr)


def finish(request_id: str, job: dict) -> None:
    with jobs_lock:
        jobs[request_id] = job


def fail(request_id: str, detail: str) -> None:
    finish(request_id, {"status": "failed", "detail": detail[-STDERR_TAIL_CHARS:]})


def run_generate(
    prompt: str, width: int | None, height: int | None, timeout: float
) -> tuple[str, Path]:
    """Run generate.sh once; return (presigned_url, local_image_path)."""
    if timeout <= 0:
        raise PipelineError("generation timed out")
    override = os.environ.get("AGFORGE_GENERATE_CMD")
    argv = shlex.split(override) if override else [str(GENERATE)]
    if width is not None:
        argv += ["--width", str(width)]
    if height is not None:
        argv += ["--height", str(height)]
    argv.append(prompt)
    try:
        result = subprocess.run(
            argv,
            cwd=AGFORGE_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise PipelineError("generation timed out")
    except OSError as error:
        raise PipelineError(f"could not run generate.sh: {error}")

    if result.returncode != 0:
        # The last non-empty stderr line is the actual error (sys.exit message
        # or the final exception line of a traceback) — far more readable in a
        # caller's UI than a mid-traceback tail.
        stderr_lines = [line for line in (result.stderr or "").splitlines() if line.strip()]
        detail = (
            stderr_lines[-1].strip()
            if stderr_lines
            else (result.stdout or "generate.sh failed with no output").strip()
        )
        raise PipelineError(detail)

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    url = lines[-1] if lines else ""
    if not url.startswith("http"):
        raise PipelineError(f"generate.sh succeeded but printed no URL: {url[:200]!r}")
    local_path = None
    for line in (result.stderr or "").splitlines():
        if line.strip().startswith("local:"):
            local_path = Path(line.strip().partition("local:")[2].strip())
    if local_path is None or not local_path.exists():
        raise PipelineError("generate.sh printed no usable 'local: <path>' line")
    return url, local_path


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def dims_match(actual: tuple[int, int], target_w: int | None, target_h: int | None) -> bool:
    return (target_w is None or actual[0] == target_w) and (
        target_h is None or actual[1] == target_h
    )


def resize_and_upload(local_path: Path, target_w: int | None, target_h: int | None) -> str:
    """Deterministically resize to the exact requested size and re-presign.

    A single unset dimension is derived from the actual aspect ratio.
    """
    actual_w, actual_h = image_size(local_path)
    width = target_w if target_w is not None else max(1, round(actual_w * target_h / actual_h))
    height = target_h if target_h is not None else max(1, round(actual_h * target_w / actual_w))
    resized_path = local_path.with_name(f"{local_path.stem}-{width}x{height}{local_path.suffix}")
    with Image.open(local_path) as image:
        image.resize((width, height), Image.LANCZOS).save(resized_path)
    env = generate_module.load_env()
    return generate_module.upload_and_presign(
        env, resized_path, generate_module.DEFAULT_TTL_MINUTES
    )


def run_job(request_id: str, desire: str) -> None:
    deadline = time.monotonic() + JOB_BUDGET_SECONDS

    # Interpret: one LLM shot turns the desire into prompt + quantitative
    # requirements, or an honest refusal.
    try:
        interpretation, meta = interpret.interpret(desire)
    except interpret.InterpretError as error:
        return fail(request_id, f"interpreter error: {error}")
    log(
        f"job {request_id}: interpreter cost_usd={meta.get('total_cost_usd')} "
        f"duration_ms={meta.get('duration_ms')} attempts={meta.get('attempts')}"
    )
    if interpretation["refuse"]:
        return fail(request_id, f"refused: {interpretation['reason']}")

    # Validate: bounds + multiple-of-64 rounding for the SD-family backend.
    target_w, target_h = interpretation["width"], interpretation["height"]
    try:
        gen_w, w_rounded = interpret.validate_dimension(target_w)
        gen_h, h_rounded = interpret.validate_dimension(target_h)
    except interpret.InterpretError as error:
        return fail(request_id, f"refused: {error}")
    rounding_changed = w_rounded or h_rounded
    if rounding_changed:
        log(
            f"job {request_id}: rounded requested {target_w}x{target_h} "
            f"to {gen_w}x{gen_h} for generation"
        )

    # Generate + verify: actual pixels must match the desire's numbers.
    url = local_path = actual = None
    for attempt in (1, 2):
        try:
            url, local_path = run_generate(
                interpretation["prompt"], gen_w, gen_h, deadline - time.monotonic()
            )
        except PipelineError as error:
            return fail(request_id, str(error))
        if target_w is None and target_h is None:
            break
        try:
            actual = image_size(local_path)
        except OSError as error:
            return fail(request_id, f"could not read generated image: {error}")
        if dims_match(actual, target_w, target_h):
            actual = None  # verified exact — no resize needed
            break
        generated_as_asked = dims_match(actual, gen_w, gen_h)
        log(
            f"job {request_id}: attempt {attempt} produced {actual[0]}x{actual[1]}, "
            f"requested {target_w}x{target_h}"
        )
        if generated_as_asked:
            break  # generator did what it was told; retrying cannot help

    if actual is not None:
        # Still mismatched after retry/rounding: a deterministic resize is
        # acceptable when the shape is right — rounding-induced mismatch, a
        # single-dimension request, or an aspect ratio within tolerance.
        if target_w is not None and target_h is not None and not rounding_changed:
            aspect_off = abs(actual[0] / actual[1] - target_w / target_h) / (target_w / target_h)
            if aspect_off > ASPECT_TOLERANCE:
                return fail(
                    request_id,
                    f"unsatisfied: desire asked for {target_w}x{target_h} but generation "
                    f"produced {actual[0]}x{actual[1]} (aspect ratio differs)",
                )
        try:
            url = resize_and_upload(local_path, target_w, target_h)
        except Exception as error:  # boto3/PIL errors are all caller-opaque here
            return fail(request_id, f"unsatisfied: resize to requested size failed: {error}")
        log(f"job {request_id}: resized {actual[0]}x{actual[1]} to exact requested size")

    finish(
        request_id,
        {"status": "done", "artifacts": [{"kind": "image", "url": url}]},
    )


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            return self.send_json(200, {"ok": True})
        if self.path.startswith("/api/requests/"):
            request_id = self.path.removeprefix("/api/requests/").rstrip("/")
            with jobs_lock:
                job = jobs.get(request_id)
            if job is None:
                return self.send_json(404, {"error": "not_found", "detail": "unknown request_id"})
            return self.send_json(200, job)
        return self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/api/requests":
            return self.send_json(404, {"error": "not_found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return self.send_json(400, {"error": "bad_request", "detail": "body must be JSON"})
        desire = parsed.get("desire") if isinstance(parsed, dict) else None
        if not isinstance(desire, str):
            return self.send_json(
                400, {"error": "bad_request", "detail": 'body must be {"desire": "<prompt text>"}'}
            )
        request_id = uuid.uuid4().hex
        with jobs_lock:
            jobs[request_id] = {"status": "working", "artifacts": []}
        threading.Thread(target=run_job, args=(request_id, desire), daemon=True).start()
        return self.send_json(202, {"request_id": request_id})

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} {format % args}", file=sys.stderr)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"agforge request service listening on :{PORT}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
