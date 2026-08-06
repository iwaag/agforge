#!/bin/sh
# Run the agforge request service (port: AGFORGE_SERVICE_PORT, default 8092).
set -eu
cd "$(dirname "$0")/.."
exec uv run service/request_service.py
