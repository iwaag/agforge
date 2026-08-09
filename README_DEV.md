# agforge — developer/agent entry point

agforge is the asset-generation workspace of pj-agdev: an agent workspace for
generating images (and later music/video) that accumulates know-how, config,
docs, and scripts under the "Easier Next Time" policy. Whatever was painful
this time should be one command next time.

## Development policy: trust the agent first

- Start by trusting the agent: let it fulfill a caller's desire
  non-deterministically, with its own judgment. When it fails or cannot
  comply, have it leave a report **in its own words** (see Problem reports
  below) — those reports are valuable assets for improvement, not noise.
  A failure that produced an honest report is a successful experiment.
- Improve the process gradually. Move a step to deterministic
  script/code only after it has proven to be a recurring, mechanical part
  of the agent's behavior, and without sacrificing overall flexibility.
  Preemptively locking the agent into rigid extraction/validation
  pipelines to eliminate the possibility of failure is NOT recommended —
  it wastes the agent's capability and kills the learning loop.

(The request service follows this policy since agentify ex2: one trusted
agentic run per request; see
`devdocs/episodes/agforge/agentify/ex2/` in pj-agdev.)

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
GET  /api/requests/{id} -> { "status": "working" | "done" | "failed"
                                       | "answered",
                             "artifacts": [ { "kind": "image", "url": "<presigned URL>" } ],
                             "reply": "<the capability card, present on answered>",
                             "detail": "<human-readable, present on failed>" }
GET  /guide             -> service/GUIDE.md as text/plain (also /api/guide,
                           which is the path reachable through agdevworld's
                           same-origin passthrough)
GET  /healthz           -> { "ok": true }
```

### Entrance guide

`POST /api/requests` is the single entrance, so "what can you do?" and
"what does it cost?" arrive in the same `desire` field as the work
(devpolicy/policy.md, *Entrance Guide*). A desire that is such a question
is answered from `service/GUIDE.md` immediately and finishes `answered` —
no agent run, no money, no wait. The card is re-read from disk per request,
so editing it needs no restart.

Recognising the question is a cheap regex (`is_guide_question`), never a
model — asking what something costs must not itself cost a run. It is
biased to *miss* a guide question (which then simply runs as a desire)
rather than steal a real one: any generation verb vetoes the match, as does
a desire longer than 200 characters.

`kind` lets agforge later return music/video without breaking callers.
Jobs are held **in memory only and vanish on service restart** — pollers of
a restarted service get 404 and should just re-request. Generation takes
tens of seconds; poll every few seconds.

### The agent path (how a desire becomes an image)

Each request runs **one trusted agentic run** in a worker thread (whole
job budget 900 s; see `devdocs/episodes/agforge/agentify/ex2/` in
pj-agdev):

1. **Charter** (`service/charter.md`, filled by `service/agent_run.py`):
   the desire verbatim plus a concise briefing — what agforge is, how to
   call `scripts/generate.sh`, size bounds, data-shaping rules, the
   finish contract, and the budget. The charter is the main artifact the
   Easier Next Time loop tunes.
2. **One headless agent run** with a scoped tool allowlist (never
   skip-permissions). The agent drives generation itself, checks its own
   output (size, format — the generator tends to emit JPEG regardless of
   what was asked), post-processes and re-uploads when needed, and
   authors its own problem report when it cannot comply.
3. **Lenient outcome parsing**: the runner scans the agent's final
   output for `RESULT_URL: <presigned url>` / `RESULT_FAILED: <one
   line>`, tolerating surrounding prose. Neither marker → the job fails
   with the output tail as `detail`. No retry machinery, no strict-JSON
   validation.
4. **Runner-side URL verification** (since agentify ex3): before a
   `done` is delivered, the runner GETs the `RESULT_URL` once (GET, not
   HEAD — MinIO presigned GETs may 403 on HEAD). Non-200 or unreachable
   fails the job with a detail naming it as a likely URL transcription
   problem; on success the content-type/size are logged as evidence.

Failure `detail` is the agent's own one-line reason (or the runner's
infra error). Since agentify ex3 the recurring resize/convert/re-upload
mechanics are offered to the agent as a sanctioned one-line tool —
`uv run service/transform.py [--format png|jpeg] [--width W --height H]
<file>` (flag-less form = plain re-upload) — the charter tells the agent
about it, and whether to post-process at all stays the agent's decision.

Subjective quality is deliberately not judged here — callers (the coming
director) own taste; this agent only makes quantitative intent real.

### Problem reports (Easier Next Time)

When the agent cannot fulfill a desire — as opposed to an infrastructure
error killing the run — it writes a report at

```text
.local/problems/<UTC stamp>-<request_id[:8]>/problem.md
```

This is the raw inbox of the Easier Next Time loop: a human and an agent
review these reports together, decide a fix or a capability change, then
delete or archive the folder. Only the path rule is fixed — the content
is the agent explaining **in its own words** what was asked, what it
tried, and why it could not comply. Reports are local-only (git-ignored)
and never surfaced to callers beyond the normal failure `detail`. Tests
override the root with `AGFORGE_PROBLEMS_DIR`.

### Agent backends

Selected by `AGFORGE_AGENT_BACKEND` (process env or `.local/.env`,
default `ollama`):

- `ollama` (default): opencode headless (`opencode run`) over a local
  ollama model — deliberately a weaker agent, so charter wording gaps
  surface as observable behavior instead of being papered over. Binary
  and model are configuration: `AGFORGE_OPENCODE_CMD`,
  `AGFORGE_OPENCODE_MODEL` (e.g. `ollama/<model>`); the committed
  `opencode.json` holds the deny-by-default bash allowlist. Zero
  marginal cost.
- `claude`: scoped `claude -p` (model pinned `claude-sonnet-5`, explicit
  `--allowedTools`), the comparison/escalation backend,
  ~$0.1–0.5/request. Binary via `AGFORGE_CLAUDE_CMD` when not on PATH.
  When the ollama agent fails where claude succeeds, record the
  divergence — that contrast is a finding, not a defect to hide.

Observability (since agentify ex3): the ollama backend runs
`opencode run --format json`, and the raw event stream (tool calls
included) is saved per job to `.local/out/<request_id>.agent.jsonl`
(override dir: `AGFORGE_TRANSCRIPTS_DIR`) — even when the run times out
or exits nonzero. The runner extracts the agent's words from the `text`
events leniently (plain text passes through, so the claude backend and
the test stub keep working). Infra-failure details keep the harness
stderr tail. Each job logs backend, duration, cost, turns, transcript
path, and the URL-check result, plus the agent's final output.

## Tests

```sh
uv run pytest -q          # no live services needed
```

`tests/` covers the deterministic shell only: charter composition,
lenient outcome parsing, budget/timeout handling, and the HTTP contract,
through the `AGFORGE_AGENT_CMD` stub (`tests/fake_agent.py`). Agent
behavior itself is not unit-tested — it is observed live and recorded in
episode reports. Live smoke (real SwarmUI + MinIO) stays manual: POST a
desire with an explicit size, measure the downloaded artifact.

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
AGFORGE_AGENT_BACKEND=     # agent backend: ollama (default) or claude
AGFORGE_OPENCODE_CMD=      # path to the opencode binary when not on PATH (ollama backend)
AGFORGE_OPENCODE_MODEL=    # opencode model ref, e.g. ollama/<model> (ollama backend)
AGFORGE_CLAUDE_CMD=        # path to the claude binary when not on PATH (claude backend)
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
