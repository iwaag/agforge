# Description
General music generation & editing tools

# Music tools

Singing-voice audio, by driving a running ACE Studio. It documents itself —
`acestudio-cli help` shows the docs for a command or topic and regex-searches
across them, and `--help` on any subcommand lists what it takes. Read those
rather than guessing.

It only works when ACE Studio is actually running with External Agent Access
enabled, which is often not the case. **Probe before planning around it:**

```sh
acestudio-cli status project
```

Watch the output, not the exit code: it answers `bridge not reachable …` and
still exits `0`. If it is not reachable, an audio deliverable is not something
you can produce right now — that is an `idea.md`, not a `plan.md`.