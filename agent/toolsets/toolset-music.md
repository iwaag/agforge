# Description
General music generation & editing tools

# Music Tools
'agforge music generate --prompt "describe what to create"' — generates music. You can't specify any other parameters for now.
It can run for tens of seconds or several minutes, then prints the download URL on its last line when the track is ready. Run it in the foreground and wait for that line; a
backgrounded run that nobody waits for produces nothing.

`agforge music submit --prompt "…"` — queues the same job and prints its
ComfyUI `prompt_id` immediately instead of waiting. Use it when you would
otherwise spend your whole run watching a render: write the id into
`pending.json` and finish, and a later run of yours collects the outputs with
`agforge comfy fetch <prompt_id> --into result`. Your guide has the exact
shape of that file.
