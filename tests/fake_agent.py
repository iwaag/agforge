"""Stub for AGFORGE_AGENT_CMD: replays canned agent output.

Mimics the runner's observable agent contract: charter on stdin, final
output on stdout. Stdlib only.

Env contract:
    FAKE_AGENT_OUTPUT      — text to print on stdout (default empty).
    FAKE_AGENT_EXIT        — exit code (default 0).
    FAKE_AGENT_SLEEP       — seconds to sleep before answering (timeout tests).
    FAKE_AGENT_CHARTER_OUT — when set, write the received charter to this file
                             (lets tests assert charter composition end to end).
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
    sleep = float(os.environ.get("FAKE_AGENT_SLEEP", "0"))
    if sleep:
        time.sleep(sleep)
    print(os.environ.get("FAKE_AGENT_OUTPUT", ""))
    sys.exit(int(os.environ.get("FAKE_AGENT_EXIT", "0")))


if __name__ == "__main__":
    main()
