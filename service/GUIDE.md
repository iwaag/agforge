# agforge — entrance guide

The capability card. Served raw at `GET /guide`, re-read from disk per
request: edit it and the next answer changes, no restart.

## What it does

- One still image per request, from a free-text desire. One agentic run
  drives the generator, post-processes, and hands back a time-limited
  download URL.
- One music track per request through the LAN music-generation service. The
  request agent fetches the service's own guide and returns its audio URL.
- Video, 3D, animation and multi-image are not implemented.

## What it costs

Every run leaves its own numbers: `.local/jobs/<request_id>/result.json` is
what was delivered, and `.local/out/<request_id>.agent.jsonl` carries the
turns and the per-step cost. Those are the live figures. The ones below are
examples measured on agstudio across 2026-08-09/10; a number written here
goes stale as runs accumulate, the files do not.

- Time: ~15–130 s per request, most 35–45 s. Runs are killed at 900 s.
- Money: 0.00 USD on the `local` profile (OpenCode with local Ollama).
  The same desire on the `sonnet` profile (Claude Code) measured 0.13–0.23 USD
  over 4–10 turns and 18–34 s. Recorded per run either way.
- Pixels are free (local SwarmUI). Download URLs expire (default 60 min).

## How to talk to it

- `POST /api/requests {"desire": "<what you want>"}` → `202 {"request_id"}`
- `GET /api/requests/<id>` → poll; `status` is `working` until the agent
  answers, then whatever the agent wrote.
- `GET /guide` → this file raw. `GET /healthz` → liveness.

Capability and cost questions go in the same `desire` field as the work —
one entrance. The agent reads this card to answer them, so a question
costs a run like anything else.

## Agent profile (Agent ≠ Model)

The `generator` role selects a profile from `agents.toml`; this deployment
may override that selection in `.local/agents.local.toml`. Standard profiles
are `local` (`opencode` + `ollama/qwen3.6:35b-a3b-coding-nvfp4`) and
`sonnet` (`claude_code` + `anthropic/claude-sonnet-5`). Every run
records role, profile, harness, provider, and canonical model. An unavailable
selection fails; it never falls back to another profile.
