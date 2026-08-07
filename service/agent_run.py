# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""One trusted agentic run per request (agentify ex2).

Composes a charter prompt from `service/charter.md`, runs one headless
agent over it with a scoped tool allowlist, and leniently reads the
outcome markers from the agent's output:

    RESULT_URL: <presigned url>     -> fulfilled
    RESULT_FAILED: <one line>       -> honest failure (problem.md written
                                       by the agent, in its own words)

Neither marker present -> the job fails with the tail of the agent
output as detail. A `done` outcome is delivered only after the runner
GETs the URL once (ex3 hardening): non-200 or unreachable fails the job
with a detail naming it as a likely transcription problem. No retry machinery, no strict-JSON validation — the
agent is trusted to drive generation, self-check, and problem reporting.

Backends, selected by AGFORGE_AGENT_BACKEND (process env or
`.local/.env`, default `ollama`):

- `ollama`: opencode headless (`opencode run`) against the local ollama
  model. Tool permissions come from the committed `opencode.json`
  allowlist in the agforge root (deny by default). Binary and model are
  configuration: AGFORGE_OPENCODE_CMD, AGFORGE_OPENCODE_MODEL.
- `claude`: scoped `claude -p` (model pinned below) with an explicit
  --allowedTools list. NEVER --dangerously-skip-permissions — this runs
  natively on the agstudio Mac and local policy forbids it here.

Test hook: AGFORGE_AGENT_CMD replaces the whole agent invocation with a
command that reads the charter on stdin and prints agent-style output on
stdout.

CLI (manual runs): uv run service/agent_run.py "<desire>"
prints the job dict on stdout; full agent output and meta go to stderr.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

AGFORGE_ROOT = Path(__file__).resolve().parent.parent
CHARTER_TEMPLATE = AGFORGE_ROOT / "service" / "charter.md"

CLAUDE_MODEL = "claude-sonnet-5"
DEFAULT_BUDGET_SECONDS = 900
OUTPUT_TAIL_CHARS = 800

# Scoped allowlist for the claude backend — the agent gets exactly the
# commands the charter tells it about, nothing more.
CLAUDE_ALLOWED_TOOLS = (
    "Bash(scripts/generate.sh:*)",
    "Bash(./scripts/generate.sh:*)",
    "Bash(uv run:*)",
    "Bash(sips:*)",
    "Bash(file:*)",
    "Bash(ls:*)",
    "Bash(mkdir:*)",
    "Bash(cat:*)",
    "Read",
    "Write",
)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


class AgentRunError(Exception):
    """The agent run itself failed (infra); str() is the job detail."""


def local_env(name: str) -> str | None:
    """Resolve a config value: process env first, then `.local/.env`."""
    if os.environ.get(name):
        return os.environ[name]
    env_file = AGFORGE_ROOT / ".local" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                value = line.partition("=")[2].strip()
                if value:
                    return value
    return None


def agent_backend() -> str:
    """Which agent backend runs the job: `ollama` (default) or `claude`."""
    backend = local_env("AGFORGE_AGENT_BACKEND") or "ollama"
    if backend not in ("ollama", "claude"):
        raise AgentRunError(f"unknown AGFORGE_AGENT_BACKEND: {backend!r}")
    return backend


def transcripts_dir() -> Path:
    override = os.environ.get("AGFORGE_TRANSCRIPTS_DIR")
    return Path(override) if override else AGFORGE_ROOT / ".local" / "out"


def problems_dir() -> Path:
    override = os.environ.get("AGFORGE_PROBLEMS_DIR")
    return Path(override) if override else AGFORGE_ROOT / ".local" / "problems"


def problem_path(request_id: str) -> Path:
    """The path rule (location only — content is always the agent's)."""
    stamp = time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
    return problems_dir() / f"{stamp}-{request_id[:8]}" / "problem.md"


def compose_charter(
    desire: str, request_id: str, budget_seconds: int = DEFAULT_BUDGET_SECONDS
) -> str:
    template = CHARTER_TEMPLATE.read_text(encoding="utf-8")
    return (
        template.replace("{{DESIRE}}", desire)
        .replace("{{REQUEST_ID}}", request_id)
        .replace("{{PROBLEM_PATH}}", str(problem_path(request_id)))
        .replace("{{BUDGET_SECONDS}}", str(budget_seconds))
    )


def build_argv() -> list[str]:
    override = os.environ.get("AGFORGE_AGENT_CMD")
    if override:
        return shlex.split(override)
    if agent_backend() == "claude":
        claude = local_env("AGFORGE_CLAUDE_CMD") or "claude"
        return [
            claude,
            "-p",
            "--output-format",
            "json",
            "--model",
            CLAUDE_MODEL,
            "--allowedTools",
            " ".join(CLAUDE_ALLOWED_TOOLS),
        ]
    opencode = local_env("AGFORGE_OPENCODE_CMD") or "opencode"
    # --format json = raw JSON events (one per line): tool calls become
    # reviewable in the per-job transcript instead of final-message-only.
    argv = [opencode, "run", "--format", "json"]
    model = local_env("AGFORGE_OPENCODE_MODEL")
    if model:
        argv += ["-m", model]
    return argv


def extract_event_text(raw: str) -> tuple[str, dict]:
    """Leniently read an opencode `--format json` event stream.

    One JSON object per line; the agent's words live in `text` events
    under part.text, and step_finish events carry per-step cost. Lines
    that are not such events (plain-text output from the test stub or an
    older opencode) pass through unchanged, so this degrades to identity
    on non-JSON output. Returns (text_for_marker_scanning, stats).
    """
    texts: list[str] = []
    turns = 0
    cost = 0.0
    saw_event = False
    for line in raw.splitlines():
        stripped = line.strip()
        event = None
        if stripped.startswith("{"):
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                event = None
        if not isinstance(event, dict) or "type" not in event:
            texts.append(line)
            continue
        saw_event = True
        part = event.get("part")
        part = part if isinstance(part, dict) else {}
        if event["type"] == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
        elif event["type"] == "step_finish":
            turns += 1
            if isinstance(part.get("cost"), (int, float)):
                cost += part["cost"]
    stats = {"num_turns": turns, "total_cost_usd": round(cost, 6)} if saw_event else {}
    return "\n".join(texts), stats


def run_agent(
    charter: str, timeout: float, transcript_path: Path | None = None
) -> tuple[str, dict]:
    """Run one headless agent over the charter; return (output_text, meta).

    Raises AgentRunError on launch failure, timeout, or nonzero exit —
    infra failures, not ENT problem reports. Whatever the agent process
    wrote to stdout is saved raw to transcript_path (even on timeout or
    nonzero exit), and infra-failure details keep the harness stderr
    tail so the next empty-output flake is diagnosable (ex3, after ex2
    had nothing to diagnose one with).
    """
    if timeout <= 0:
        raise AgentRunError("agent run timed out (no budget left)")
    argv = build_argv()
    backend = "override" if os.environ.get("AGFORGE_AGENT_CMD") else agent_backend()

    def save_transcript(raw: str) -> bool:
        if transcript_path and raw.strip():
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text(raw, encoding="utf-8")
            return True
        return False

    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            input=charter,
            cwd=AGFORGE_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except subprocess.TimeoutExpired as error:
        save_transcript(error.stdout if isinstance(error.stdout, str) else "")
        raise AgentRunError(f"agent run timed out after {int(timeout)}s")
    except OSError as error:
        raise AgentRunError(f"could not launch agent ({argv[0]}): {error}")
    meta: dict = {
        "backend": backend,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
    raw = ANSI_RE.sub("", proc.stdout or "")
    if save_transcript(raw):
        meta["transcript"] = str(transcript_path)
    stderr_tail = ANSI_RE.sub("", proc.stderr or "").strip()[-OUTPUT_TAIL_CHARS:]

    if backend == "claude":
        # claude -p --output-format json wraps the final message; copy the
        # cost-capture pattern from agautolab's claude_code adapter.
        try:
            outer = json.loads(proc.stdout)
        except json.JSONDecodeError:
            outer = None
        output = raw
        if isinstance(outer, dict):
            for key in ("total_cost_usd", "duration_ms", "num_turns", "is_error", "subtype"):
                if key in outer:
                    meta[key] = outer[key]
            result = outer.get("result")
            output = result if isinstance(result, str) else ""
    else:
        output, stats = extract_event_text(raw)
        meta.update(stats)

    if proc.returncode != 0:
        tail = output.strip()[-OUTPUT_TAIL_CHARS:] or "no output"
        detail = f"agent exited {proc.returncode}: {tail}"
        if stderr_tail:
            detail += f"; stderr tail: {stderr_tail}"
        raise AgentRunError(detail)
    if not output.strip():
        detail = "agent produced no output"
        if stderr_tail:
            detail += f"; stderr tail: {stderr_tail}"
        raise AgentRunError(detail)
    return output, meta


def parse_outcome(output: str) -> dict:
    """Leniently read the finish contract out of the agent's output.

    Scans every line for the last RESULT_URL: / RESULT_FAILED: marker,
    tolerating surrounding prose and markdown decoration. Returns the job
    dict for the HTTP contract.
    """
    url = failed = None
    for line in output.splitlines():
        if "RESULT_URL:" in line:
            candidate = line.rsplit("RESULT_URL:", 1)[1].strip().strip("*`> ")
            if candidate.startswith("http"):
                url, failed = candidate.split()[0], None
        elif "RESULT_FAILED:" in line:
            failed = line.rsplit("RESULT_FAILED:", 1)[1].strip().strip("*`> ")
            url = None
    if url:
        return {"status": "done", "artifacts": [{"kind": "image", "url": url}]}
    if failed is not None:
        detail = failed or "agent reported failure without a reason"
        return {"status": "failed", "detail": detail[-OUTPUT_TAIL_CHARS:]}
    tail = output.strip()[-OUTPUT_TAIL_CHARS:]
    return {
        "status": "failed",
        "detail": f"agent finished without a RESULT marker; output tail: {tail}",
    }


URL_CHECK_TIMEOUT_SECONDS = 30


def verify_result_url(url: str) -> dict:
    """GET the delivered RESULT_URL once — cheap, deterministic, no judgment.

    Ex2 saw the agent retype presigned URLs lossily; a corrupted URL was
    still delivered as `done`. One GET catches that transcription class
    without taking anything away from the agent. MinIO presigned GETs
    answer plain GET (HEAD may 403 — never use HEAD here). Returns
    {"ok": True, "status", "content_type", "size_bytes"} or
    {"ok": False, "reason"}.
    """
    try:
        with urllib.request.urlopen(url, timeout=URL_CHECK_TIMEOUT_SECONDS) as resp:
            body = resp.read()
            return {
                "ok": True,
                "status": resp.status,
                "content_type": resp.headers.get("Content-Type", ""),
                "size_bytes": len(body),
            }
    except urllib.error.HTTPError as error:
        return {"ok": False, "reason": f"HTTP {error.code}"}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        return {"ok": False, "reason": str(error) or type(error).__name__}


def run_request(
    desire: str,
    request_id: str | None = None,
    budget_seconds: int = DEFAULT_BUDGET_SECONDS,
) -> tuple[dict, dict]:
    """The whole ex2 shape: charter -> one agent run -> lenient outcome.

    Returns (job_dict, meta). Infra failures come back as failed jobs, not
    exceptions — callers only need the job dict.
    """
    request_id = request_id or uuid.uuid4().hex
    charter = compose_charter(desire, request_id, budget_seconds)
    transcript_path = transcripts_dir() / f"{request_id}.agent.jsonl"
    try:
        output, meta = run_agent(
            charter, timeout=budget_seconds, transcript_path=transcript_path
        )
    except AgentRunError as error:
        meta = {"backend": "error"}
        if transcript_path.exists():
            meta["transcript"] = str(transcript_path)
        return {"status": "failed", "detail": str(error)}, meta
    meta["output"] = output
    job = parse_outcome(output)
    if job["status"] == "done":
        check = verify_result_url(job["artifacts"][0]["url"])
        meta["url_check"] = check
        if not check["ok"]:
            job = {
                "status": "failed",
                "detail": (
                    f"RESULT_URL failed verification ({check['reason']}) — "
                    "the agent likely mistranscribed the presigned URL"
                ),
            }
    return job, meta


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        sys.exit('usage: uv run service/agent_run.py "<desire>"')
    job, meta = run_request(sys.argv[1])
    output = meta.pop("output", "")
    print("--- agent output ---", file=sys.stderr)
    print(output, file=sys.stderr)
    print(f"meta: {json.dumps(meta)}", file=sys.stderr)
    print(json.dumps(job, ensure_ascii=False))


if __name__ == "__main__":
    main()
