#!/bin/sh
# Run the agforge Zulip listener (credentials: .local/zulip.env).
set -eu
cd "$(dirname "$0")/.."
exec uv run service/zulip_listener.py
