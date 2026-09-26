Do what described in "plan.md" using whatever described in files in "tools/" folder.
"chatlog.md" is the conversation that started this run. Its last message is
what the person who started it asked for now: follow it where it says
something more specific than the plan, and ignore it where it says nothing.
"knowledge.md" says which knowledge sources and revisions the plan was made
against and whether they have moved since. `agforge knowledge show <source>/<path>`
reads any of them now, `agforge knowledge search <terms>` finds lines, and
`agforge knowledge path <source>/<path>` is where a localised script lives so
you can run it in place. If the plan's approach fails, you may look for
another one there; say in your report what you actually used.
Put all final products inside "result/" folder.
Put all intermediate products inside "intermediate/" folder.
If failed, create empty "failure.flag" file. Say in your report what failed,
what would have to change for it to work, and any alternative you can see.
Name the kind of change, because the requester routes it and you cannot:
an environment problem (a library, service or model missing or unreachable),
an implementation problem (a localised script or its README is wrong), or a
knowledge gap (nothing known covers this); the localised source's own README
says where each kind goes.

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

# Human-authored references

`agrefs` reads what the developer has published for a project to be built
from — stories, images, templates, runnable examples — by name at a pinned
revision: `<source>@<revision>[:<path>]`. `agrefs list` shows
every source the developer has published for agents, with what each is for;
`agrefs sync <source>` fetches the newest published revision and
prints the commit it is; `agrefs show <source>@<rev>[:<path>]` prints a text
file, lists a directory, or says what a binary is; `agrefs path …` is the
file itself, which your own image reader can open (`agrefs --help` has the
rest). A request that names a reference names *that* revision: work from
it, quote what you used as `<source>@<rev>:<path>` in what you write, and
never put a newer revision or a summary of your own in the place of the
original without saying so. The originals are read-only; derivatives go
into your own workspace. When a reference and the request disagree, or a
reference cannot be reached, say so rather than inventing.

Say in your report which reference (`<source>@<rev>:<path>`) went into the
result and how (init image, matched palette, prompt), so the requester can
compare the result with the original.
