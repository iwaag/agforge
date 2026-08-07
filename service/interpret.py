# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Interpret a caller's desire into concrete generation parameters.

One LLM one-shot (`claude -p --output-format json`, model pinned) turns free
desire text into strict JSON the pipeline can act on:

    {"prompt": "<cleaned creative prompt>", "width": int|null,
     "height": int|null, "refuse": false}
or  {"refuse": true, "reason": "<one sentence>"}

null width/height means the desire stated no size, so config defaults apply
downstream. Malformed LLM output is retried once, then InterpretError is
raised (the service maps it to a failed job).

CLI (for manual checks): uv run service/interpret.py "<desire>"
prints the validated JSON on stdout and call metadata (cost, duration) on
stderr.

The `claude` binary is resolved from AGFORGE_CLAUDE_CMD (process env or
`.local/.env`), falling back to `claude` on PATH — this machine runs it from
a VSCode extension bundle, so the real path lives in `.local/.env`.

Test hook: AGFORGE_INTERPRET_CMD replaces the whole `claude` invocation with
an arbitrary command that reads the prompt on stdin and prints claude-style
result JSON on stdout.
"""

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

AGFORGE_ROOT = Path(__file__).resolve().parent.parent

INTERPRET_MODEL = "claude-sonnet-5"
INTERPRET_TIMEOUT_SECONDS = 120

# Dimension bounds and rounding the SD-family backend requires. Kept next to
# the capability list so both evolve together.
MIN_DIM = 64
MAX_DIM = 2048
DIM_MULTIPLE = 64

# The single place that states what agforge can do today. The interpreter
# grounds refusals in this; it will grow (music/video) alongside
# artifacts[].kind.
CAPABILITIES = f"""\
agforge can currently:
- generate exactly ONE still image per request (PNG or JPEG output)
- honor requested width/height between {MIN_DIM} and {MAX_DIM} pixels
agforge can NOT currently:
- generate video, animation, music, audio, or 3D assets
- generate multiple images in one request
- choose or switch the underlying model (model choice is configuration)"""

PROMPT_TEMPLATE = f"""\
You translate a caller's creative desire into generation parameters for an
image-generation pipeline.

{CAPABILITIES}

Read the desire below and answer with RAW JSON only (no markdown fences, no
commentary), in exactly one of these two shapes:

1. If agforge can honor the desire:
{{"prompt": "<creative prompt>", "width": <int or null>, "height": <int or null>, "refuse": false}}
   - "prompt": the desire rewritten as a clean creative prompt for a
     diffusion model. Remove quantitative requirements (sizes, pixel counts,
     aspect-ratio numbers) from the text — they belong in the fields, and
     keep the prompt in the desire's own language otherwise.
   - "width"/"height": the pixel dimensions the desire asks for, as
     integers. Interpret approximate or unusual phrasings into concrete
     integers. If the desire states no size at all, use null for both.
     If it implies only an aspect ratio, pick concrete dimensions within
     bounds matching that ratio.
2. If agforge can NOT honor the desire (wrong medium, impossible or
   out-of-bounds requirements):
{{"refuse": true, "reason": "<one short sentence naming what agforge cannot do>"}}

Desire:
{{desire}}"""


class InterpretError(Exception):
    """The LLM one-shot failed to produce a valid interpretation."""


def build_prompt(desire: str) -> str:
    return PROMPT_TEMPLATE.replace("{desire}", desire)


def claude_command() -> str:
    """Path to the claude binary: env var, then `.local/.env`, then PATH."""
    if os.environ.get("AGFORGE_CLAUDE_CMD"):
        return os.environ["AGFORGE_CLAUDE_CMD"]
    env_file = AGFORGE_ROOT / ".local" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("AGFORGE_CLAUDE_CMD="):
                value = line.partition("=")[2].strip()
                if value:
                    return value
    return "claude"


def run_llm(prompt: str) -> tuple[str, dict]:
    """Run the one-shot and return (result_text, meta).

    meta carries total_cost_usd / duration_ms when the CLI reports them.
    Raises InterpretError on launch failure, timeout, nonzero exit, or an
    outer-JSON parse failure.
    """
    override = os.environ.get("AGFORGE_INTERPRET_CMD")
    if override:
        argv = shlex.split(override)
    else:
        argv = [claude_command(), "-p", "--output-format", "json", "--model", INTERPRET_MODEL]
    try:
        proc = subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=INTERPRET_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise InterpretError(f"interpreter timed out after {INTERPRET_TIMEOUT_SECONDS}s")
    except OSError as error:
        raise InterpretError(f"could not run interpreter command: {error}")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "no output").strip().splitlines()
        raise InterpretError(f"interpreter exited {proc.returncode}: {tail[-1] if tail else ''}")
    try:
        outer = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise InterpretError(f"interpreter printed non-JSON: {proc.stdout[:200]!r}")
    if not isinstance(outer, dict):
        raise InterpretError(f"interpreter outer JSON is not an object: {proc.stdout[:200]!r}")
    meta = {k: outer[k] for k in ("total_cost_usd", "duration_ms", "is_error") if k in outer}
    result = outer.get("result")
    if not isinstance(result, str) or not result.strip():
        raise InterpretError("interpreter returned no result text")
    return result, meta


def parse_interpretation(text: str) -> dict:
    """Validate the model's answer into the strict interpretation dict.

    Raises InterpretError when the shape is wrong.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        raise InterpretError(f"interpretation is not valid JSON: {text[:200]!r}")
    if not isinstance(data, dict):
        raise InterpretError("interpretation is not a JSON object")

    if data.get("refuse") is True:
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise InterpretError("refusal without a reason")
        return {"refuse": True, "reason": reason.strip()}

    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise InterpretError("interpretation has no usable prompt")
    dims = {}
    for key in ("width", "height"):
        value = data.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise InterpretError(f"{key} must be an integer or null, got {value!r}")
        dims[key] = value
    return {"prompt": prompt.strip(), "width": dims["width"], "height": dims["height"], "refuse": False}


def interpret(desire: str) -> tuple[dict, dict]:
    """Interpret the desire; retry the one-shot once on a malformed answer.

    Returns (interpretation, meta). Raises InterpretError after the retry.
    """
    prompt = build_prompt(desire)
    last_error = None
    for attempt in (1, 2):
        started = time.monotonic()
        try:
            text, meta = run_llm(prompt)
            interpretation = parse_interpretation(text)
        except InterpretError as error:
            last_error = error
            continue
        meta.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
        meta["attempts"] = attempt
        return interpretation, meta
    raise InterpretError(str(last_error))


def validate_dimension(value: int | None) -> tuple[int | None, bool]:
    """Bound-check and round one dimension to the required multiple.

    Returns (validated_value, changed). Raises InterpretError when the value
    is outside sane bounds — the caller reports that as a refusal-class
    failure rather than silently clamping.
    """
    if value is None:
        return None, False
    if value < MIN_DIM or value > MAX_DIM:
        raise InterpretError(
            f"requested dimension {value} outside supported range {MIN_DIM}-{MAX_DIM}"
        )
    rounded = round(value / DIM_MULTIPLE) * DIM_MULTIPLE
    rounded = min(max(rounded, MIN_DIM), MAX_DIM)
    return rounded, rounded != value


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        sys.exit('usage: uv run service/interpret.py "<desire>"')
    try:
        interpretation, meta = interpret(sys.argv[1])
    except InterpretError as error:
        sys.exit(f"interpreter error: {error}")
    print(json.dumps(interpretation, ensure_ascii=False))
    print(f"meta: {json.dumps(meta)}", file=sys.stderr)


if __name__ == "__main__":
    main()
