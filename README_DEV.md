# agforge — developer/agent entry point

agforge is the asset-generation workspace of pj-agdev: an agent workspace for
generating images (and later music/video) that accumulates know-how, config,
docs, and scripts under the "Easier Next Time" policy. Whatever was painful
this time should be one command next time.

## What lives where

- `scripts/` — pipeline scripts. The main deliverable is a one-command
  prompt → image → S3 upload → presigned download URL pipeline.
- `.local/` — git-ignored. All endpoints, hostnames, credentials, and local
  notes live here, never in the repo.
  - `.local/devenv.md` — local-only notes: actual endpoints, quirks observed.
  - `.local/.env` — environment variables the scripts read (see below).
  - `.local/out/` — locally downloaded generated images (also git-ignored).

## Pipeline

```sh
scripts/generate.sh "a prompt"
scripts/generate.sh --ttl 240 "a prompt"   # URL lifetime in minutes (default 60)
# stderr: local:<path to the downloaded image under .local/out/>
# stdout final line: time-limited presigned download URL
```

`generate.sh` is a thin wrapper around `uv run scripts/generate.py` (needs
`uv` on PATH; dependencies are declared inline in the script). Steps under
the hood: SwarmUI HTTP API (`GetNewSession` → `GenerateText2Image`) →
download image to `.local/out/` → upload to the `agforge` bucket on MinIO →
presigned GET URL.

## `.local/.env` keys

Required:

```sh
AGFORGE_SWARMUI_URL=       # SwarmUI base URL
AGFORGE_SWARMUI_MODEL=     # model name from /API/ListModels — SwarmUI rejects calls without it
AGFORGE_S3_ENDPOINT=       # MinIO endpoint; must be the hostname recipients can reach, never localhost
AGFORGE_S3_BUCKET=agforge  # dedicated bucket; never write to nctl-outbox
AGFORGE_S3_ACCESS_KEY=
AGFORGE_S3_SECRET_KEY=
```

Optional (otherwise SwarmUI's current server defaults apply):
`AGFORGE_SWARMUI_WIDTH`, `AGFORGE_SWARMUI_HEIGHT`, `AGFORGE_SWARMUI_STEPS`,
`AGFORGE_SWARMUI_CFGSCALE`, `AGFORGE_SWARMUI_SEED`.

Actual values for this environment are in git-ignored `.local/.env` and
`.local/devenv.md` (endpoints, chosen model, quirks).

## Hard rules

- Never commit endpoints, hostnames, credentials, or generated images.
- Never write into the `nctl-outbox` bucket; agforge uses its own `agforge`
  bucket.

## Related docs

- Episode plan/reports: `pj-agdev/devdocs/episodes/agforge/begin/`
- MinIO reuse context: `pj-clusterintent` devenv (`nctl.toml` `[storage]`).
