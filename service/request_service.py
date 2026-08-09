# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""agforge request service — intent-level HTTP API over one agentic run.

Contract (see pj-agdev/devdocs/episodes/connect_world_and_forge/plan.md):

    POST /api/requests      { "desire": "<prompt text>" }
                            -> 202 { "request_id": "..." }
    GET  /api/requests/{id} -> { "status": "working" | "done" | "failed"
                                           | "answered",
                                 "artifacts": [ { "kind": "image", "url": "..." } ],
                                 "reply": "<present on answered>",
                                 "detail": "<present on failed>" }
    GET  /guide             -> service/GUIDE.md as text/plain
    GET  /healthz           -> { "ok": true }

`POST /api/requests` is the single entrance, so capability and cost
questions arrive in the same `desire` field as the work
(devpolicy/policy.md, Entrance Guide). A desire that is a question about
what agforge can do or what it costs is answered from `service/GUIDE.md`
immediately and finishes `answered` — no agent run, no money, no wait. The
card is re-read from disk per request (cagent's llms.txt pattern), so
editing it needs no restart.

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
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AGFORGE_ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("AGFORGE_SERVICE_PORT", "8092"))
JOB_BUDGET_SECONDS = 900
GUIDE_PATH = AGFORGE_ROOT / "service" / "GUIDE.md"

sys.path.insert(0, str(AGFORGE_ROOT / "service"))
import agent_run  # noqa: E402

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


# --- the entrance guide ---------------------------------------------------
#
# Recognising a guide question is deliberately a cheap, deterministic
# matcher rather than a model: the whole point is that asking "what does
# this cost" must not itself cost an agent run. It is biased towards
# missing a guide question (which then simply runs as a desire and fails
# honestly) over stealing a real one — hence the length bound and the
# generation-verb veto, which wins over any question phrasing.

GUIDE_QUESTION = re.compile(
    r"what (can|do) you (do|make|generate|produce)"
    r"|what (are|is) your (capabilit|limit)"
    r"|what (is|are) (this|you)\b"
    r"|what (does|will) (it|this|that|a request|an image) cost"
    r"|how much (does|do|is|would)"
    r"|what'?s the (cost|price)"
    r"|\b(capabilities|your price|price list|pricing)\b"
    r"|何ができ|なにができ|できること|いくら(かかる|ですか)?|料金|費用|コスト",
    re.IGNORECASE,
)

# A desire that asks for something to be made is work, whatever question
# mark it carries: "can you draw me a picture of a price tag?" is a request.
# Verbs only — the asset *nouns* stay out, because "how much does an image
# cost" is a guide question and vetoing on the word "image" would eat it.
GENERATION_VERB = re.compile(
    r"\b(draw|paint|render|generate|create|design|illustrate|sketch|"
    r"make (me|a|an|us)|produce a)\b"
    r"|描い|書い|作って|生成して",
    re.IGNORECASE,
)

GUIDE_QUESTION_MAX_CHARS = 200


def is_guide_question(desire: str) -> bool:
    text = desire.strip()
    if len(text) > GUIDE_QUESTION_MAX_CHARS or GENERATION_VERB.search(text):
        return False
    return bool(GUIDE_QUESTION.search(text))


def read_guide() -> str:
    """Re-read per request; a missing card must not take the service down."""
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
        f"job {request_id}: agent backend={meta.get('backend')} "
        f"cost_usd={meta.get('total_cost_usd')} "
        f"duration_ms={meta.get('duration_ms')} num_turns={meta.get('num_turns')} "
        f"transcript={meta.get('transcript')} url_check={meta.get('url_check')}"
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
        if is_guide_question(desire):
            # `detail` carries a readable one-liner as well, so a client that
            # only understands working/done/failed shows something sensible
            # instead of an empty failure.
            with jobs_lock:
                jobs[request_id] = {
                    "status": "answered",
                    "artifacts": [],
                    "reply": read_guide(),
                    "detail": "answered from the entrance guide; no agent ran",
                }
            log(f"job {request_id}: answered from the entrance guide (no agent run)")
            return self.send_json(202, {"request_id": request_id})
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
