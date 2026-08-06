# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""agforge request service — intent-level HTTP API over the generate pipeline.

Contract (see pj-agdev/devdocs/episodes/connect_world_and_forge/plan.md):

    POST /api/requests      { "desire": "<prompt text>" }
                            -> 202 { "request_id": "..." }
    GET  /api/requests/{id} -> { "status": "working" | "done" | "failed",
                                 "artifacts": [ { "kind": "image", "url": "..." } ],
                                 "detail": "<present on failed>" }
    GET  /healthz           -> { "ok": true }

Jobs are held in memory only and vanish on restart. Each request runs the
verified `scripts/generate.sh` in a worker thread; the final stdout line is
the presigned image URL. Everything generation-specific (model, size, S3)
stays inside generate.sh's own config layers — callers send only a desire.

Run: scripts under service/ read no CLI args; port comes from
AGFORGE_SERVICE_PORT (default 8092).
"""

import json
import os
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AGFORGE_ROOT = Path(__file__).resolve().parent.parent
GENERATE = AGFORGE_ROOT / "scripts" / "generate.sh"
PORT = int(os.environ.get("AGFORGE_SERVICE_PORT", "8092"))
STDERR_TAIL_CHARS = 800

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def run_job(request_id: str, desire: str) -> None:
    try:
        result = subprocess.run(
            [str(GENERATE), desire],
            cwd=AGFORGE_ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        with jobs_lock:
            jobs[request_id] = {"status": "failed", "detail": "generation timed out"}
        return
    except OSError as error:
        with jobs_lock:
            jobs[request_id] = {"status": "failed", "detail": f"could not run generate.sh: {error}"}
        return

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
        with jobs_lock:
            jobs[request_id] = {"status": "failed", "detail": detail[-STDERR_TAIL_CHARS:]}
        return

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    url = lines[-1] if lines else ""
    if not url.startswith("http"):
        with jobs_lock:
            jobs[request_id] = {
                "status": "failed",
                "detail": f"generate.sh succeeded but printed no URL: {url[:200]!r}",
            }
        return
    with jobs_lock:
        jobs[request_id] = {
            "status": "done",
            "artifacts": [{"kind": "image", "url": url}],
        }


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
