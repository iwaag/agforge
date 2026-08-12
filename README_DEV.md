# agforge — developer/agent entry point

The asset-generation workspace of pj-agdev: prompt in, generated image
out, wrapped in an intent-level HTTP API. Accumulates know-how under
"Easier Next Time" — whatever was painful this time should be one command
next time.

## Development policy: trust the agent first

Let the agent fulfill a caller's desire with its own judgment. When it
fails, its own report is the asset. Move a step to deterministic code only
after it has proven to be a recurring, mechanical part of the agent's
behavior. Since `unshackle_agent` turn1 the runner no longer parses,
verifies or classifies on the agent's behalf: the caller's answer is the
agent's to write (`devdocs/episodes/unshackle_agent/agforge/turn1/` at the
workspace root).

## Map

- `scripts/generate.sh [--ttl MIN] [--model N] [--width W --height H]
  [--steps N] [--cfgscale N] [--seed N] "<prompt>"` — SwarmUI →
  `.local/out/` → MinIO → presigned URL on the last line of stdout, local
  path on stderr.
- `uv run service/transform.py [--format png|jpeg] [--width W --height H]
  <file>` — resize/convert/re-upload; fresh URL on the last line. No flags
  = upload as-is. `service/transform.py` is a launcher for the
  `agforge.transform` package module.
- `service/serve.sh` — the request service on :8092
  (`AGFORGE_SERVICE_PORT`). Log: `.local/out/service.log`.
- `service/listen.sh` — the Zulip chat entrance: long-polls for DMs to the
  forge bot and answers each one with a run of the same pipeline. Credentials
  in `.local/zulip.env`, log `.local/out/zulip-listener.log`. Set
  `AGFORGE_ZULIP_LOG_ONLY=1` to watch without answering.
- `service/charter.md` — what the request agent is told. Re-read per
  request; wording changes need no restart. The main ENT tuning lever.
- `service/GUIDE.md` — the capability/cost card, served at `GET /guide`.
- `agents.toml` — committed `ag.agent-config.v1` models, profiles, and the
  `generator` role. `.local/agents.local.toml` supplies executable paths,
  provider endpoints, and an optional local profile selection.
- `opencode.json` — OpenCode tool grants (wide allowlist, deny by default).
  Claude Code equivalent: `CLAUDE_ALLOWED_TOOLS` in
  `src/agforge/agent_run.py`.
- `src/agforge/` — the installed application package. Files under `service/`
  and `scripts/` are compatibility launchers or runtime documents.
- `params/defaults.toml` — sample generation defaults.
- `.local/` — git-ignored: `.env`, `devenv.md`, `out/` (images,
  transcripts, service log), `jobs/` (agent-written results),
  `problems/` (agent-written reports).
- `uv run pytest -q` — deterministic shell only, no live services.

## HTTP contract

```
POST /api/requests      { "desire": "<prompt text>" } -> 202 { "request_id" }
GET  /api/requests/{id} -> "status": "working" while the run is going,
                           then the agent's own JSON, served unvalidated
GET  /guide             -> service/GUIDE.md as text/plain (also /api/guide)
GET  /healthz           -> { "ok": true }
```

The agent writes `.local/jobs/<request_id>/result.json` and every key in
it is the agent's. Without it, the service reports that the run ended
with nothing for the caller and passes the agent's last words along. The
one field the runner adds is `status` when absent, meaning the run is
over. Jobs live in memory and vanish on restart (pollers get 404 —
re-request).

agdevworld is the current caller. It reads the whole body with a model,
not a schema, so no key here is agreed in advance (unshackle_agent
turn2/turn3).

## Chat contract

The Zulip listener is the second entrance, added in the `zulip_receive`
episode. A DM to the forge bot becomes one `agent_run.run_request()` — the
same pipeline as `:8092`, called in-process, so one charter and one run
record cover both entrances. The desire it receives is the visible DM
conversation as a speaker-labelled transcript; `service/GUIDE.md` documents
that format and the `reply` field the run is asked to write. Two entrances is
a temporary state: chat is meant to become the single one.

## Generation parameters

`params/defaults.toml` → `.local/.env` → CLI flags, later wins. `model` is
required (SwarmUI 0.9.7.4 rejects requests without it) and deliberately
absent from `defaults.toml` — set `AGFORGE_SWARMUI_MODEL` or `--model`.
Valid names: `POST /API/ListModels` with a `session_id` from
`POST /API/GetNewSession` and `{"path": "", "depth": 2}` — the call
answers `missing session id` without one. Everything else falls back to
SwarmUI's server defaults.

## `.local/.env` keys

```sh
AGFORGE_SWARMUI_URL=       # SwarmUI base URL
AGFORGE_SWARMUI_MODEL=     # from /API/ListModels; no default exists
AGFORGE_S3_ENDPOINT=       # MinIO endpoint recipients can reach, not localhost
AGFORGE_S3_BUCKET=agforge  # agforge's own bucket
AGFORGE_S3_ACCESS_KEY=
AGFORGE_S3_SECRET_KEY=
AGFORGE_SWARMUI_WIDTH= HEIGHT= STEPS= CFGSCALE= SEED=   # optional
```

Agent selection is not configured through `.env`. See `agents.toml`; local
runtime facts use `.local/agents.local.toml`. Tests select the canonical
`fake` profile. Directory/service test overrides remain
`AGFORGE_JOBS_DIR`, `AGFORGE_PROBLEMS_DIR`, `AGFORGE_TRANSCRIPTS_DIR`, and
`AGFORGE_SERVICE_PORT`.

Each run keeps the raw `.agent.jsonl` transcript and a neighboring
`.agent-run.json` record with role, profile, harness, provider, canonical
model, outcome, duration, and usage/cost when reported.

Actual values for this environment: `.local/.env`, `.local/devenv.md`.

## Safety devices

Two, both guarding irreversible harm rather than mistakes: `generate.py`
refuses to write to the `nctl-outbox` bucket (another project's), and no
agent here runs with `--dangerously-skip-permissions` / `opencode run
--auto`, because these run natively on the agstudio Mac. Never commit
endpoints, hostnames, credentials, or generated images.

## Related docs

- `pj-agdev/devdocs/episodes/agforge/` — begin, agentify ex1–ex3
- `devdocs/episodes/unshackle_agent/agforge/` — the unshackling turns
- MinIO reuse context: `pj-clusterintent` devenv (`nctl.toml` `[storage]`)
