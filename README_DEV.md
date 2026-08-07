# agforge — developer/agent entry point

agforge is the asset-generation workspace of pj-agdev: an agent workspace for
generating images (and later music/video) that accumulates know-how, config,
docs, and scripts under the "Easier Next Time" policy. Whatever was painful
this time should be one command next time.

## What lives where

- `scripts/` — pipeline scripts. The main deliverable is a one-command
  prompt → image → S3 upload → presigned download URL pipeline.
- `service/` — the request service: an intent-level HTTP API wrapping the
  same pipeline for other agents/apps (currently agdevworld).
- `.local/` — git-ignored. All endpoints, hostnames, credentials, and local
  notes live here, never in the repo.
  - `.local/devenv.md` — local-only notes: actual endpoints, quirks observed.
  - `.local/.env` — environment variables the scripts read (see below).
  - `.local/out/` — locally downloaded generated images (also git-ignored).

## Pipeline

```sh
scripts/generate.sh "a prompt"
scripts/generate.sh --ttl 240 "a prompt"   # URL lifetime in minutes (default 60)
scripts/generate.sh --width 256 --height 256 "a prompt"   # per-request override
# stderr: local:<path to the downloaded image under .local/out/>
# stdout final line: time-limited presigned download URL
```

`generate.sh` is a thin wrapper around `uv run scripts/generate.py` (needs
`uv` on PATH; dependencies are declared inline in the script). Steps under
the hood: SwarmUI HTTP API (`GetNewSession` → `GenerateText2Image`) →
download image to `.local/out/` → upload to the `agforge` bucket on MinIO →
presigned GET URL.

## Request service

A **fully prompt-only** HTTP API over the pipeline: callers send a desire
(prompt text) and poll for result artifacts. Callers know nothing about
models, sizes, or SwarmUI — an internal agent reads the desire and
assembles the concrete generation parameters itself.

```sh
service/serve.sh          # listens on :8092 (override: AGFORGE_SERVICE_PORT)
```

Contract:

```
POST /api/requests      { "desire": "<prompt text>" }
                        -> 202 { "request_id": "..." }
GET  /api/requests/{id} -> { "status": "working" | "done" | "failed",
                             "artifacts": [ { "kind": "image", "url": "<presigned URL>" } ],
                             "detail": "<human-readable, present on failed>" }
GET  /healthz           -> { "ok": true }
```

`kind` lets agforge later return music/video without breaking callers.
Jobs are held **in memory only and vanish on service restart** — pollers of
a restarted service get 404 and should just re-request. Generation takes
tens of seconds; poll every few seconds.

### The agent path (how a desire becomes an image)

Each request runs a bounded pipeline in a worker thread (whole job budget
900 s; see `devdocs/episodes/agforge/agentify/` in pj-agdev):

1. **Interpret** (`service/interpret.py`, one LLM one-shot): extracts the
   creative prompt and any
   quantitative requirements (width/height) out of the desire text, or
   refuses desires agforge cannot honor (wrong medium, absurd dimensions).
   Sizes stated in the desire now *control* generation instead of being
   passed to the diffusion model as prose. Null size → config defaults
   (`params/defaults.toml` / `.local/.env`) apply, their intended role.
2. **Validate** (code): bounds 64–2048, rounding to the nearest multiple
   of 64 (SD-family requirement); rounding is recorded.
3. **Generate** (code): `scripts/generate.sh --width/--height` — the
   unchanged, verified low-level tool, still available directly for
   humans/scripts.
4. **Verify** (code, no LLM): the actual pixels of the generated file are
   checked against the desire's numbers. Mismatch → one retry; a
   persistent mismatch with the right shape (rounding-induced,
   single-dimension, or aspect within 2 %) gets a deterministic resize to
   the exact requested size and a fresh presigned URL; otherwise the job
   fails honestly.

Failure `detail` prefixes let callers tell classes apart textually:

- `refused: ...` — agforge cannot honor the desire (says why).
- `unsatisfied: ...` — generation could not be made to match the desire.
- `interpreter error: ...` — the LLM one-shot itself failed.
- anything else — pipeline/infra error (SwarmUI, S3, ...).

Subjective quality is deliberately not judged here — callers (the coming
director) own taste; this agent only makes quantitative intent real.

The interpreter has two backends, selected by `AGFORGE_INTERPRET_BACKEND`
(process env or `.local/.env`, default `claude`):

- `claude`: one `claude -p` shot, model pinned `claude-sonnet-5`. The
  binary is resolved via `AGFORGE_CLAUDE_CMD` (process env or
  `.local/.env`) when it is not on PATH. ~$0.07/request.
- `ollama`: one `/api/generate` call (`format: json`, temperature 0)
  against an ollama server. `AGFORGE_OLLAMA_URL` and
  `AGFORGE_OLLAMA_MODEL` are required (process env or `.local/.env`);
  endpoints are configuration, never committed. Zero marginal cost; needs
  a tool-grade instruct model (see the agentify/ex1 report in pj-agdev).

The backend and per-job cost/duration are logged per job.

## Tests

```sh
uv run pytest -q          # no live services needed
```

`tests/` fakes the LLM (`AGFORGE_INTERPRET_CMD`) and generate.sh
(`AGFORGE_GENERATE_CMD`) behind their real subprocess contracts to cover
the interpreter and the whole pipeline, including retry/resize/refusal
paths. Live smoke (real SwarmUI + MinIO) stays manual: POST a desire with
an explicit size, measure the downloaded artifact.

## Generation parameters

SwarmUI generation parameters (`model`, `width`, `height`, `steps`,
`cfgscale`, `seed`) are resolved by merging three layers, later wins:

1. `params/defaults.toml` — versioned sample values for the optional
   params. Committed to the repo; rough values are fine, tune freely.
2. `.local/.env` — this environment's actual values (see keys below).
3. Per-request CLI flags (`--model`, `--width`, `--height`, `--steps`,
   `--cfgscale`, `--seed`) — highest priority, for one-off overrides.

`model` is the only required parameter — SwarmUI 0.9.7.4 rejects requests
without it ("No model input given"). It's deliberately absent from
`params/defaults.toml` (which models are installed varies per SwarmUI
instance); set it via `AGFORGE_SWARMUI_MODEL` in `.local/.env` or
`--model`. Valid names come from `POST /API/ListModels` on the running
SwarmUI instance. If `model` is unresolved from all three layers, the
script fails fast with a message pointing at all three places to set it.

All other params fall back to SwarmUI's current server defaults if unset
in every layer.

## `.local/.env` keys

Required:

```sh
AGFORGE_SWARMUI_URL=       # SwarmUI base URL
AGFORGE_S3_ENDPOINT=       # MinIO endpoint; must be the hostname recipients can reach, never localhost
AGFORGE_S3_BUCKET=agforge  # dedicated bucket; never write to nctl-outbox
AGFORGE_S3_ACCESS_KEY=
AGFORGE_S3_SECRET_KEY=
```

Effectively required (no default in `params/defaults.toml`), but can be set
via `--model` instead of `.env`:

```sh
AGFORGE_SWARMUI_MODEL=     # model name from /API/ListModels; no default exists in defaults.toml
```

Optional (see `params/defaults.toml` for sample values, otherwise
SwarmUI's current server defaults apply):
`AGFORGE_SWARMUI_WIDTH`, `AGFORGE_SWARMUI_HEIGHT`, `AGFORGE_SWARMUI_STEPS`,
`AGFORGE_SWARMUI_CFGSCALE`, `AGFORGE_SWARMUI_SEED`.

Service-only, optional:

```sh
AGFORGE_INTERPRET_BACKEND= # interpreter backend: claude (default) or ollama
AGFORGE_CLAUDE_CMD=        # path to the claude binary when not on PATH (claude backend)
AGFORGE_OLLAMA_URL=        # ollama base URL (required for the ollama backend)
AGFORGE_OLLAMA_MODEL=      # ollama model name (required for the ollama backend)
```

Actual values for this environment are in git-ignored `.local/.env` and
`.local/devenv.md` (endpoints, chosen model, quirks).

## Agent instruction: no S3 configured

If `AGFORGE_S3_*` is unset in `.local/.env` and the user's prompt names no
alternative storage, do not improvise a workaround. Instead propose
starting the `pj-clusterintent` devenv MinIO and setting it up:

1. Start MinIO from `pj-clusterintent/devenv/` (`docker compose up -d`).
2. Using the root credentials from `pj-clusterintent/devenv/.env`
   (`MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`):
   - `mc mb <alias>/agforge`
   - create a policy `agforge-rw` scoped to the `agforge` bucket only
   - create a user `agforge` with that policy attached
3. Record the endpoint/access key/secret key in `agforge/.local/.env`
   (`AGFORGE_S3_ENDPOINT`/`AGFORGE_S3_ACCESS_KEY`/`AGFORGE_S3_SECRET_KEY`).

Do not reuse the `nctl` user's key for this — its policy (`nctl-outbox-rw`)
is scoped to the `nctl-outbox` bucket only, so bucket creation and any
other-bucket access with it is rejected (`Access Denied`). That dead end
already cost time once (see problem.md #3).

## Hard rules

- Never commit endpoints, hostnames, credentials, or generated images.
- Never write into the `nctl-outbox` bucket; agforge uses its own `agforge`
  bucket.

## Related docs

- Episode plan/reports: `pj-agdev/devdocs/ent-episodes/swarmui-flow/`
- MinIO reuse context: `pj-clusterintent` devenv (`nctl.toml` `[storage]`).
