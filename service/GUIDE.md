# agforge — entrance guide

The capability card the request service answers "what can you do / what does
it cost" from. Plain text, re-read from disk per request: edit it and the
next answer changes, no restart. Served raw at `GET /guide`.

## What this is

The asset-generation workspace. You send one desire in words; one trusted
agentic run fulfills it — it drives the generator itself, checks its own
output, converts or resizes when the desire asked for something the
generator did not deliver, and hands back a time-limited download URL.

## What it can do today

- **One still image per request**, from a free-text description. Size and
  format are yours to ask for (e.g. "a 512x512 PNG of …"); the agent
  post-processes to match, and the service GETs the delivered URL once
  before calling the request done.

## What it cannot do today

Music, video, 3D, animation, and more than one image per request. Ask for
any of those and the request comes back `failed` with the agent's own
explanation, plus a problem report written in its own words under
`.local/problems/`.

## What it costs

Measured on agstudio over the runs in `.local/out/service.log`
(2026-08-07 → 08):

- **Time: ~20–105 seconds** per request, most around 30–40 s. The hard
  wall-clock budget is 900 s; a request that cannot finish inside it fails
  loudly rather than hanging.
- **Money: 0.00 USD on the default backend.** The agent runs on local
  ollama via opencode, which reports no price. Switched to
  `AGFORGE_AGENT_BACKEND=claude`, the same desire measured **0.134 USD** in
  4 turns and 18 seconds (2026-08-09) — recorded per run either way.
- Image generation itself runs on local SwarmUI, so pixels are free; the
  download URL is presigned and expires (default 30 minutes) — fetch it
  before then.

## How to talk to it

`POST /api/requests {"desire": "<what you want>"}` → `202 {"request_id"}`,
then poll `GET /api/requests/<id>` until `status` is `done` or `failed`.
This is the single entrance: capability and cost questions go in the same
`desire` field as the work, and are answered from this card without
spending a run. `GET /guide` serves this file raw, `GET /healthz` is the
liveness probe.

## Backend (Agent ≠ Model)

`AGFORGE_AGENT_BACKEND` = `ollama` (default) | `claude`, resolved from the
process environment first and then `.local/.env`. Model within a backend:
`AGFORGE_OPENCODE_MODEL`. Every run records which backend served it.
