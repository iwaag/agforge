"""Stub for AGFORGE_INTERPRET_CMD: prints claude-style outer JSON.

Env contract:
    FAKE_LLM_RESULT  — the inner `result` text to return every call.
    FAKE_LLM_RESULTS — JSON list of result texts, one per call (requires
                       FAKE_LLM_STATE, a counter file path, to know the
                       call index; the last entry repeats).
"""

import json
import os
import sys
from pathlib import Path


def main() -> None:
    sys.stdin.read()
    calls = 0
    state = os.environ.get("FAKE_LLM_STATE")
    if state:
        state_path = Path(state)
        calls = int(state_path.read_text() or "0") if state_path.exists() else 0
        state_path.write_text(str(calls + 1))
    sequence = os.environ.get("FAKE_LLM_RESULTS")
    if sequence:
        results = json.loads(sequence)
        result = results[min(calls, len(results) - 1)]
    else:
        result = os.environ["FAKE_LLM_RESULT"]
    print(json.dumps({"result": result, "total_cost_usd": 0.01, "is_error": False}))


if __name__ == "__main__":
    main()
