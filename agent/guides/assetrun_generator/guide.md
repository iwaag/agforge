Do what described in "plan.md" using whatever described in files in "tools/" folder.
"chatlog.md" is the conversation that started this run. Its last message is
what the person who started it asked for now: follow it where it says
something more specific than the plan, and ignore it where it says nothing.
Put all final products inside "result/" folder.
Put all intermediate products inside "intermediate/" folder.
If failed, create empty "failure.flag" file.

When a ComfyUI generation takes minutes, do not wait for it. Submit it, run
`comfynotify watch <prompt_id>` (`--help` explains the tool), record in your
report what is pending and what to do with its result, then finish. The
notifier will post into this topic and call you back — two lines naming the
state and the `prompt_id`; read `GET /history/<prompt_id>` yourself for the
outputs.
