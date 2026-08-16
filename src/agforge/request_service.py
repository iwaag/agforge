"""agforge intent-level HTTP API over one agentic run."""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import agent_run, generate

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("AGFORGE_SERVICE_PORT", "8092"))
JOB_BUDGET_SECONDS = 900
GUIDE_PATH = AGFORGE_ROOT / "service" / "GUIDE.md"

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def read_guide() -> str:
    try:
        return GUIDE_PATH.read_text(encoding="utf-8")
    except OSError:
        return "No capability card is installed on this agforge instance."


def log(message: str) -> None:
    print(message, file=sys.stderr)


def finish(request_id: str, job: dict) -> None:
    with jobs_lock:
        jobs[request_id] = job


def run_job(request_id: str, desire: str) -> None:
    job, meta = agent_run.run_request(
        desire, request_id=request_id, budget_seconds=JOB_BUDGET_SECONDS
    )
    output = meta.pop("output", "")
    log(
        f"job {request_id}: agent role={meta.get('role')} profile={meta.get('profile')} "
        f"harness={meta.get('harness')} provider={meta.get('provider')} model={meta.get('model')} "
        f"cost_usd={meta.get('cost_usd')} "
        f"duration_ms={meta.get('duration_ms')} num_turns={meta.get('num_turns')} "
        f"transcript={meta.get('transcript')} run_record={meta.get('run_record')} "
        f"outcome_from={meta.get('outcome_from')}"
    )
    if output.strip():
        log(f"job {request_id}: agent output:\n{output.strip()}")
    finish(request_id, job)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_text(self, status: int, text: str) -> None:
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            return self.send_json(200, {"ok": True})
        if self.path.rstrip("/") in ("/guide", "/api/guide"):
            return self.send_text(200, read_guide())
        if self.path.startswith("/api/requests/"):
            request_id = self.path.removeprefix("/api/requests/").rstrip("/")
            with jobs_lock:
                job = jobs.get(request_id)
            if job is None:
                return self.send_json(404, {"error": "not_found", "detail": "unknown request_id"})
            return self.send_json(200, job)
        return self.send_json(404, {"error": "not_found"})

    def read_json(self) -> object:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_resign(self, parsed: object) -> None:
        """Re-sign an object already in the bucket: no upload, no agent run.

        A delivery's presigned URL lives `DEFAULT_TTL_MINUTES`; the object
        behind it lives on. A consumer that kept the `[S3KEY]` footer asks
        here for a fresh URL immediately before it needs one, rather than
        hoping the link it was handed has not expired in the meantime.
        """
        key = parsed.get("key") if isinstance(parsed, dict) else None
        if not isinstance(key, str) or not key.strip():
            return self.send_json(
                400, {"error": "bad_request", "detail": 'body must be {"key": "<s3 object key>"}'}
            )
        key = key.strip()
        try:
            env = generate.load_env()
            if not generate.object_exists(env, key):
                return self.send_json(
                    404, {"error": "not_found", "detail": f"no object at {key!r}"}
                )
            url = generate.presign(env, key, generate.DEFAULT_TTL_MINUTES)
        except SystemExit as error:
            # `generate` answers a missing configuration by exiting, which is
            # right for the CLI it serves and would kill this thread here.
            return self.send_json(500, {"error": "misconfigured", "detail": str(error)})
        return self.send_json(
            200,
            {
                "key": key,
                "url": url,
                "expires_in_minutes": generate.DEFAULT_TTL_MINUTES,
            },
        )

    def do_POST(self) -> None:
        route = self.path.rstrip("/")
        if route not in ("/api/requests", "/api/resign"):
            return self.send_json(404, {"error": "not_found"})
        try:
            parsed = self.read_json()
        except (ValueError, json.JSONDecodeError):
            return self.send_json(400, {"error": "bad_request", "detail": "body must be JSON"})
        if route == "/api/resign":
            return self.do_resign(parsed)
        desire = parsed.get("desire") if isinstance(parsed, dict) else None
        if not isinstance(desire, str):
            return self.send_json(
                400, {"error": "bad_request", "detail": 'body must be {"desire": "<prompt text>"}'}
            )
        request_id = uuid.uuid4().hex
        with jobs_lock:
            jobs[request_id] = {"status": "working"}
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
