# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""One trusted agentic run per request.

Composes a charter prompt from `service/charter.md`, runs one headless
agent over it, and serves whatever the agent left for the caller.

The caller's answer is the agent's to write:

1. `.local/jobs/<request_id>/result.json` — served **unvalidated**. The
   agent decides every key and every value. No schema, no key whitelist,
   no coercion.
2. Absent — the runner states the fact that the run ended with nothing
   for the caller, and carries the agent's own final words along.

Nothing here judges whether the agent did well. A `status` this module
fills in describes what reached the caller; the caller's own
requirements are the caller's to enforce (unshackle_agent turn1). The
`RESULT_URL:` marker scan was deleted in turn3 — it carried 0 of 23 runs
across turns 1 and 2, so both ways of answering were one way.

Backends, selected by AGFORGE_AGENT_BACKEND (process env or
`.local/.env`, default `ollama`):

- `ollama`: opencode headless (`opencode run`) against the local ollama
  model. Tool grants come from `opencode.json` in the agforge root.
  Binary and model: AGFORGE_OPENCODE_CMD, AGFORGE_OPENCODE_MODEL.
- `claude`: scoped `claude -p` (model pinned below). NEVER
  `--dangerously-skip-permissions` — this runs natively on the agstudio
  Mac and local policy forbids it here.

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
import uuid
from pathlib import Path

AGFORGE_ROOT = Path(__file__).resolve().parent.parent
CHARTER_TEMPLATE = AGFORGE_ROOT / "service" / "charter.md"

CLAUDE_MODEL = "claude-sonnet-5"
DEFAULT_BUDGET_SECONDS = 900
OUTPUT_TAIL_CHARS = 800

# Tool grant for the claude backend. Wide on purpose (Tool Giving): the
# agent gets far more than the charter mentions, so it can reach for
# whatever a desire actually needs. Still an allowlist, not `*` — these
# runs are native on the developer's own Mac, where full-open would have
# the blast radius of --dangerously-skip-permissions.
CLAUDE_ALLOWED_TOOLS = (
    "Bash(scripts/generate.sh:*)",
    "Bash(./scripts/generate.sh:*)",
    "Bash(sh scripts/generate.sh:*)",
    "Bash(uv:*)",
    "Bash(python3:*)",
    "Bash(pip:*)",
    "Bash(curl:*)",
    "Bash(sips:*)",
    "Bash(magick:*)",
    "Bash(ffmpeg:*)",
    "Bash(ffprobe:*)",
    "Bash(file:*)",
    "Bash(ls:*)",
    "Bash(pwd:*)",
    "Bash(cd:*)",
    "Bash(mkdir:*)",
    "Bash(cp:*)",
    "Bash(mv:*)",
    "Bash(rm:*)",
    "Bash(cat:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
    "Bash(wc:*)",
    "Bash(grep:*)",
    "Bash(rg:*)",
    "Bash(find:*)",
    "Bash(sed:*)",
    "Bash(awk:*)",
    "Bash(echo:*)",
    "Bash(printf:*)",
    "Bash(jq:*)",
    "Bash(date:*)",
    "Bash(env:*)",
    "Bash(which:*)",
    "Bash(mc:*)",
    "Bash(git status:*)",
    "Bash(git log:*)",
    "Bash(git diff:*)",
    "Bash(git show:*)",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
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


def jobs_dir() -> Path:
    override = os.environ.get("AGFORGE_JOBS_DIR")
    return Path(override) if override else AGFORGE_ROOT / ".local" / "jobs"


def result_path(request_id: str) -> Path:
    """Where the caller looks. The file's content is entirely the agent's."""
    return jobs_dir() / request_id / "result.json"


def compose_charter(
    desire: str, request_id: str, budget_seconds: int = DEFAULT_BUDGET_SECONDS
) -> str:
    template = CHARTER_TEMPLATE.read_text(encoding="utf-8")
    return (
        template.replace("{{DESIRE}}", desire)
        .replace("{{REQUEST_ID}}", request_id)
        .replace("{{RESULT_PATH}}", str(result_path(request_id)))
        .replace("{{PROBLEMS_DIR}}", str(problems_dir()))
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
    on non-JSON output. Returns (agent_text, stats).
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
    facts about the process, not statements about the agent's work.
    Whatever the agent process wrote to stdout is saved raw to
    transcript_path (even on timeout or nonzero exit).
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


def read_result(request_id: str) -> dict | None:
    """Read `.local/jobs/<id>/result.json` — the agent's own answer.

    Served as written: no schema, no key whitelist, no value coercion.
    Returns None when the agent left nothing (or left something that is
    not a JSON object, which cannot be an HTTP body here).
    """
    path = result_path(request_id)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def resolve_outcome(request_id: str, output: str) -> tuple[dict, str]:
    """What the caller receives, and where it came from.

    The agent's result file, or else the plain fact that the run ended
    with nothing for the caller. The second case is a statement about
    what reached the caller — never a verdict on the run.
    """
    result = read_result(request_id)
    if result is not None:
        job = dict(result)
        # Fill only what is structurally absent: `status` says the run is
        # over, the one thing the runner knows and the agent could not.
        # Nothing else is added — 13 runs in turn2 invented six different
        # key names for the answer and never once used `artifacts`.
        job.setdefault("status", "ended")
        return job, "result_file"
    tail = output.strip()[-OUTPUT_TAIL_CHARS:]
    return (
        {
            "status": "ended",
            "detail": (
                "the run ended and left nothing for the caller "
                f"({result_path(request_id)} absent); "
                f"the agent's last words: {tail}"
            ),
        },
        "nothing",
    )


def run_request(
    desire: str,
    request_id: str | None = None,
    budget_seconds: int = DEFAULT_BUDGET_SECONDS,
) -> tuple[dict, dict]:
    """charter -> one agent run -> whatever the agent left for the caller.

    Returns (job_dict, meta). Infra failures come back as failed jobs, not
    exceptions — callers only need the job dict. Even then the result file
    is honored: an agent that answered before its process died still
    answered.
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
        result = read_result(request_id)
        if result is not None:
            meta["outcome_from"] = "result_file"
            meta["infra_error"] = str(error)
            job = dict(result)
            job.setdefault("status", "ended")
            return job, meta
        return {"status": "failed", "detail": str(error)}, meta
    meta["output"] = output
    job, source = resolve_outcome(request_id, output)
    meta["outcome_from"] = source
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
