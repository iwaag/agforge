Files in "tools/" folder describes what you can use to create items described in "required_items.md".

Before planning, look at what is known: Bash `agforge knowledge list` prints every knowledge source with its revision and index. A `general` source says what a model or workflow is and what tests found; a `local` source says what actually runs on this environment, how, and in what state (`planned` / `experimenting` / `verified` / `failed` / `retired` — only `verified` has been run end to end here). `agforge knowledge show <source>/<path>` reads one file, `agforge knowledge search <terms>` finds lines across sources, `agforge knowledge path <source>/<path>` gives the place to run a script from. Nothing is pre-selected for you: read what looks relevant, and you may go on to something else if your first pick does not fit.

Read the inputs in this order when they disagree: the requester's words in the chat (the project's own requirement) win over "required_items.md", which wins over local knowledge, which wins over general knowledge.

If you think you can create all required items with what you are allowed to use, make creation plan at "plan.md". In it, name the knowledge you rely on as `<source>/<path>` and say whether it is verified here or not; the run that executes the plan gets the same references. Do not copy long passages — a reference is enough, the run can read it.
If you must ask the requester a question instead of planning, write the question in your reply.
If you can't create it right now, but you have any idea on how to enable it, write "idea.md". An `idea.md` may also propose relaxing the requirement or a commercial service, with the trade-off; say which parts of the original request that would give up.

In idea.md and plan.md, the first line is a Markdown heading ("# ...") and becomes the title,
and the rest of the file becomes the description.

Otherwise just politely reply saying it's impossible.
