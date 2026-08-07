# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""agforge request service — intent-level HTTP API over one agentic run.

Contract (see pj-agdev/devdocs/episodes/connect_world_and_forge/plan.md):

    POST /api/requests      { "desire": "<prompt text>" }
                            -> 202 { "request_id": "..." }
    GET  /api/requests/{id} -> { "status": "working" | "done" | "failed",
                                 "artifacts": [ { "kind": "image", "url": "..." } ],
                                 "detail": "<present on failed>" }
    GET  /healthz           -> { "ok": true }

Jobs are held in memory only and vanish on restart. Each request runs ONE
trusted agentic run in a worker thread (agentify ex2,
pj-agdev/devdocs/episodes/agforge/agentify/ex2/plan.md):

    compose charter (service/charter.md) -> one headless agent run with a
    scoped tool allowlist -> leniently read RESULT_URL / RESULT_FAILED

The agent drives generation itself via scripts/generate.sh, checks its
own output, and — when it cannot fulfill the desire — writes
`.local/problems/<UTC stamp>-<request_id[:8]>/problem.md` in its own
words before failing. There is no code-side interpret/verify/resize/
convert stage and no templated problem report anymore; failure `detail`
is whatever the agent (or the runner, on infra errors) said.

Run: scripts under service/ read no CLI args; port comes from
AGFORGE_SERVICE_PORT (default 8092). Backend selection and test hook
(AGFORGE_AGENT_CMD) live in service/agent_run.py.
"""

import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AGFORGE_ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("AGFORGE_SERVICE_PORT", "8092"))
JOB_BUDGET_SECONDS = 900

sys.path.insert(0, str(AGFORGE_ROOT / "service"))
import agent_run  # noqa: E402

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


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
        f"job {request_id}: agent backend={meta.get('backend')} "
        f"cost_usd={meta.get('total_cost_usd')} "
        f"duration_ms={meta.get('duration_ms')} num_turns={meta.get('num_turns')}"
    )
    # The transcript is the observable agent behavior this episode collects;
    # keep it in the service log for later reading.
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
