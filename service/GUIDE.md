# agforge — entrance guide

The capability card. Served raw at `GET /guide`, re-read from disk per
request: edit it and the next answer changes, no restart.

## What it does

- One still image per request, from a free-text desire. One agentic run
  drives the generator, post-processes, and hands back a time-limited
  download URL.
- Music, video, 3D, animation and multi-image are not implemented.

## What it costs

Measured on agstudio (`.local/out/service.log`):

- Time: ~20–105 s per request, most 30–40 s. Runs are killed at 900 s.
- Money: 0.00 USD on the default backend (local ollama via opencode).
  The same desire on `AGFORGE_AGENT_BACKEND=claude` measured 0.134 USD /
  4 turns / 18 s (2026-08-09). Recorded per run either way.
- Pixels are free (local SwarmUI). Download URLs expire (default 60 min).

## How to talk to it

- `POST /api/requests {"desire": "<what you want>"}` → `202 {"request_id"}`
- `GET /api/requests/<id>` → poll; `status` is `working` until the agent
  answers, then whatever the agent wrote.
- `GET /guide` → this file raw. `GET /healthz` → liveness.

Capability and cost questions go in the same `desire` field as the work —
one entrance. The agent reads this card to answer them, so a question
costs a run like anything else.

## Backend (Agent ≠ Model)

`AGFORGE_AGENT_BACKEND` = `ollama` (default) | `claude`, from the process
env then `.local/.env`. Model within a backend: `AGFORGE_OPENCODE_MODEL`.
Every run records which backend served it.
