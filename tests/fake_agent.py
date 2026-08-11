#!/usr/bin/env python3
"""Stub for the test-only `fake` harness: replays canned agent output.

Mimics the runner's observable agent contract: charter on stdin, final
output on stdout. Stdlib only.

Env contract:
    FAKE_AGENT_OUTPUT      — text to print on stdout (default empty).
    FAKE_AGENT_STDERR      — text to print on stderr (default nothing).
    FAKE_AGENT_EXIT        — exit code (default 0).
    FAKE_AGENT_SLEEP       — seconds to sleep before answering (timeout tests).
    FAKE_AGENT_CHARTER_OUT — when set, write the received charter to this file
                             (lets tests assert charter composition end to end).
    FAKE_AGENT_RESULT      — JSON to leave at the result path named in the
                             charter, for tests that do not know the id.
"""

import os
import sys
import time
from pathlib import Path


def main() -> None:
    charter = sys.stdin.read()
    out = os.environ.get("FAKE_AGENT_CHARTER_OUT")
    if out:
        Path(out).write_text(charter, encoding="utf-8")
    result = os.environ.get("FAKE_AGENT_RESULT")
    if result:
        for token in charter.split():
            if token.endswith("result.json"):
                path = Path(token)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(result, encoding="utf-8")
                break
    sleep = float(os.environ.get("FAKE_AGENT_SLEEP", "0"))
    if sleep:
        time.sleep(sleep)
    echo_env = os.environ.get("FAKE_AGENT_ECHO_ENV")
    print(os.environ.get(echo_env, "") if echo_env else os.environ.get("FAKE_AGENT_OUTPUT", ""))
    stderr = os.environ.get("FAKE_AGENT_STDERR")
    if stderr:
        print(stderr, file=sys.stderr)
    sys.exit(int(os.environ.get("FAKE_AGENT_EXIT", "0")))


if __name__ == "__main__":
    main()
