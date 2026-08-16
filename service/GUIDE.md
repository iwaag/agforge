# agforge — entrance guide

The capability card. Served raw at `GET /guide`, re-read from disk per
request: edit it and the next answer changes, no restart.

## What it does

- One still image per request, from a free-text desire. One agentic run
  drives the generator, post-processes, and hands back a time-limited
  download URL.
- One music track per request through the LAN music-generation service. The
  request agent fetches the service's own guide and returns its audio URL.
- Sung vocals with lyrics through the running ACE Studio desktop app and its
  self-describing CLI. This path uses a stock ACE Studio voice; the agent does
  not clone or upload voices.
- Video, 3D, animation and multi-image are not implemented.

## What it costs

Every run leaves its own numbers: `.local/jobs/<request_id>/result.json` is
what was delivered, and `.local/out/<request_id>.agent.jsonl` carries the
turns and the per-step cost. Those are the live figures. The ones below are
examples measured on agstudio across 2026-08-09/10; a number written here
goes stale as runs accumulate, the files do not.

- Time: ~15–130 s per request, most 35–45 s. Runs are killed at 900 s.
- Money: 0.00 USD on the `local` profile (agcode with local Ollama), and
  none is reported rather than a zero being invented.
  The same desire on the `sonnet` profile (Claude Code) measured 0.13–0.23 USD
  over 4–10 turns and 18–34 s. Recorded per run either way.
- Pixels are free (local SwarmUI). Download URLs expire (default 60 min).

## How to talk to it

- `POST /api/requests {"desire": "<what you want>"}` → `202 {"request_id"}`
- `GET /api/requests/<id>` → poll; `status` is `working` until the agent
  answers, then whatever the agent wrote.
- `POST /api/resign {"key": "<s3 object key>"}` →
  `200 {"key", "url", "expires_in_minutes"}`. Download URLs expire (60 min);
  the object does not. Every delivery carries its key on an `[S3KEY] <key>`
  last line — keep that line, and ask here for a fresh URL right before you
  download. Costs nothing: no agent run, no upload. `404` means the bucket no
  longer holds that key.
- `GET /guide` → this file raw. `GET /healthz` → liveness.

Capability and cost questions go in the same `desire` field as the work —
one entrance. The agent reads this card to answer them, so a question
costs a run like anything else.

## Talking to it in chat

agforge also answers Zulip direct messages sent to its bot account. A DM
starts one run of the same pipeline; there is no separate chat agent.

What the run receives as its desire is the **visible DM conversation**, not
just the newest line: up to 50 messages, oldest first, raw text, one line per
message, labelled with the speaker's display name. The bot's own earlier
replies are labelled `(you)`; its "on it" acks are stripped out.

```
[Developer] make me a small icon of a red bird
[Forge (you)] here's a small red bird icon: http://…
[Developer] same bird but blue
```

The last line is the message to answer; the earlier lines are there so that
"same bird but blue" means something. Not every message is a generation
request — a question is answered as a question.

The answer goes back as a DM. The run writes its usual `result.json`, and the
text of a **`reply`** field in it is what gets posted. That field is a request,
not a schema: without it the whole JSON is posted verbatim, which is correct
but reads badly in a chat window. URLs are pasted as-is, so they must be
complete and unmodified.

## Agent profile (Agent ≠ Model)

The `generator` role selects a profile from `agents.toml`; this deployment
may override that selection in `.local/agents.local.toml`. Standard profiles
are `local` (`agcode` + `ollama/qwen3.6:35b-a3b-coding-nvfp4`) and
`sonnet` (`claude_code` + `anthropic/claude-sonnet-5`). Every run
records role, profile, harness, provider, and canonical model. An unavailable
selection fails; it never falls back to another profile.
