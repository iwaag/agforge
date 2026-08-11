"""One trusted agentic run per agforge request."""

from __future__ import annotations

import json
import os
import shlex
import sys
import uuid
from dataclasses import replace
from pathlib import Path

from agag.agent_config import AgentConfigError, ResolvedAgent, load_config, resolve_role
from agag.harness import (
    build_argv as shared_build_argv,
    extract_event_text,
    run_harness,
    write_run_record as shared_write_run_record,
)

AGFORGE_ROOT = Path(__file__).resolve().parents[2]
CHARTER_TEMPLATE = AGFORGE_ROOT / "service" / "charter.md"
AGENTS_CONFIG = AGFORGE_ROOT / "agents.toml"
AGENTS_LOCAL_CONFIG = AGFORGE_ROOT / ".local" / "agents.local.toml"
ACE_STUDIO_ENV = AGFORGE_ROOT / ".local" / "ace-studio.env"

DEFAULT_BUDGET_SECONDS = 900
OUTPUT_TAIL_CHARS = 800

CLAUDE_ALLOWED_TOOLS = (
    "Bash(scripts/generate.sh:*)", "Bash(./scripts/generate.sh:*)",
    "Bash(sh scripts/generate.sh:*)", "Bash(uv:*)", "Bash(python3:*)",
    "Bash(pip:*)", "Bash(curl:*)", "Bash(sips:*)", "Bash(magick:*)",
    "Bash(ffmpeg:*)", "Bash(ffprobe:*)", "Bash(file:*)", "Bash(ls:*)",
    "Bash(pwd:*)", "Bash(cd:*)", "Bash(mkdir:*)", "Bash(cp:*)",
    "Bash(mv:*)", "Bash(rm:*)", "Bash(cat:*)", "Bash(head:*)",
    "Bash(tail:*)", "Bash(wc:*)", "Bash(grep:*)", "Bash(rg:*)",
    "Bash(find:*)", "Bash(sed:*)", "Bash(awk:*)", "Bash(echo:*)",
    "Bash(printf:*)", "Bash(jq:*)", "Bash(date:*)", "Bash(env:*)",
    "Bash(which:*)", "Bash(mc:*)", "Bash(git status:*)",
    "Bash(git log:*)", "Bash(git diff:*)", "Bash(git show:*)",
    'Bash("$ACE_STUDIO_CLI":*)', "Bash($ACE_STUDIO_CLI:*)",
    "Read", "Write", "Edit", "Glob", "Grep", "WebFetch",
)


class AgentRunError(Exception):
    """The agent run itself failed (infra); str() is the job detail."""

    def __init__(self, message: str, meta: dict | None = None, outcome: str = "failed"):
        self.meta = meta or {}
        self.outcome = outcome
        super().__init__(message)


def _local_tool_environment(path: Path = ACE_STUDIO_ENV) -> dict[str, str]:
    """Read the allowlisted host-local tool path without sourcing shell code."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    for line in lines:
        tokens = shlex.split(line, comments=True)
        if len(tokens) == 1 and tokens[0].startswith("ACE_STUDIO_CLI="):
            value = tokens[0].split("=", 1)[1]
            return {"ACE_STUDIO_CLI": value} if value else {}
    return {}


def resolve_generator(*, check_available: bool = True) -> ResolvedAgent:
    config, overlay = load_config(AGENTS_CONFIG, AGENTS_LOCAL_CONFIG)
    agent = resolve_role(
        config, overlay, "generator", check_available=check_available,
        project_name="agforge",
    )
    return replace(agent, environment={**agent.environment, **_local_tool_environment()})


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


def build_argv(agent: ResolvedAgent) -> list[str]:
    return shared_build_argv(agent, allowed_tools=" ".join(CLAUDE_ALLOWED_TOOLS))


def _agforge_failure(agent: ResolvedAgent, failure: str) -> str:
    """Translate shared harness facts into agforge's established wording."""
    replacements = (
        (f"{agent.harness} timed out", "agent run timed out"),
        (f"could not launch {agent.harness}", "could not launch agent"),
        (f"{agent.harness} exited", "agent exited"),
        (f"{agent.harness} reported an error", "agent reported an error"),
        (f"{agent.harness} produced no output: ", "agent produced no output; stderr tail: "),
        (f"{agent.harness} produced no output", "agent produced no output"),
    )
    for old, new in replacements:
        if failure.startswith(old):
            return new + failure[len(old):]
    return failure


def run_agent(
    charter: str,
    timeout: float,
    agent: ResolvedAgent,
    transcript_path: Path | None = None,
) -> tuple[str, dict]:
    """Call the shared non-raising seam and preserve agforge's exception API."""
    result = run_harness(
        agent,
        charter,
        cwd=AGFORGE_ROOT,
        timeout=timeout,
        allowed_tools=" ".join(CLAUDE_ALLOWED_TOOLS),
        transcript_path=transcript_path,
        output_tail_chars=OUTPUT_TAIL_CHARS,
    )
    meta = dict(result.meta)
    if meta.get("outcome") != "done":
        failure = _agforge_failure(agent, str(meta.get("failure") or result.output))
        outcome = "aborted" if meta.get("outcome") == "aborted" else "failed"
        raise AgentRunError(failure, meta, outcome)
    return result.output, meta


def write_run_record(request_id: str, meta: dict, outcome: str, failure: str | None = None) -> Path:
    return shared_write_run_record(
        transcripts_dir() / f"{request_id}.agent-run.json",
        request_id=request_id,
        meta=meta,
        outcome=outcome,
        failure=failure,
    )


def read_result(request_id: str) -> dict | None:
    try:
        parsed = json.loads(result_path(request_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def resolve_outcome(request_id: str, output: str) -> tuple[dict, str]:
    result = read_result(request_id)
    if result is not None:
        job = dict(result)
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
    request_id = request_id or uuid.uuid4().hex
    charter = compose_charter(desire, request_id, budget_seconds)
    transcript_path = transcripts_dir() / f"{request_id}.agent.jsonl"
    try:
        agent = resolve_generator()
        output, meta = run_agent(
            charter, timeout=budget_seconds, agent=agent, transcript_path=transcript_path
        )
    except (AgentConfigError, AgentRunError) as error:
        meta = error.meta if isinstance(error, AgentRunError) else {}
        if transcript_path.exists():
            meta["transcript"] = str(transcript_path)
        outcome = error.outcome if isinstance(error, AgentRunError) else "failed"
        meta["run_record"] = str(write_run_record(request_id, meta, outcome, str(error)))
        result = read_result(request_id)
        if result is not None:
            meta["outcome_from"] = "result_file"
            meta["infra_error"] = str(error)
            job = dict(result)
            job.setdefault("status", "ended")
            return job, meta
        return {"status": "failed", "detail": str(error)}, meta
    job, source = resolve_outcome(request_id, output)
    meta["output"] = output
    meta["outcome_from"] = source
    outcome = "failed" if job.get("status") == "failed" else "done"
    failure = str(job.get("detail")) if outcome == "failed" and job.get("detail") else None
    meta["run_record"] = str(write_run_record(request_id, meta, outcome, failure))
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
