#!/bin/sh
# One command: prompt in, time-limited download URL out (final line).
# Usage: scripts/generate.sh [--ttl MINUTES] "a prompt"
set -eu
cd "$(dirname "$0")/.."
exec uv run scripts/generate.py "$@"
