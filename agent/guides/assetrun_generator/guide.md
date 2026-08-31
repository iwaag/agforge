Do what described in "plan.md" using whatever described in files in "tools/" folder.
"chatlog.md" is the conversation that started this run. Its last message is
what the person who started it asked for now: follow it where it says
something more specific than the plan, and ignore it where it says nothing.
Put all final products inside "result/" folder.
Put all intermediate products inside "intermediate/" folder.
If failed, create empty "failure.flag" file.

A video or music generation takes minutes. Do not sit through it and do not
poll: `agforge video submit` and `agforge music submit` queue the same job
`generate` runs and print its `prompt_id` straight away. Write

    {"prompt_id": "<the id>", "note": "<a few words naming this job>"}

into "pending.json", say in your report what is pending and what should
happen to it, and finish. You will be run again when the job ends, with
"pending.json" renamed to "watching.json" and the outcome in "chatlog.md".
Then read the id from "watching.json", run `agforge comfy fetch <prompt_id>
--into result` to collect the files, delete "watching.json", and finish
normally — that run is the one that delivers.

Do not post the notifier line yourself; you have no chat tool and do not need
one. Writing "pending.json" is how you ask for the wait, and it is asked for
once — leave "watching.json" alone except to read it and, when the outputs
are in, delete it.

`agforge image generate` is not part of this: it returns in seconds and hands
you the finished image, so use it directly.
