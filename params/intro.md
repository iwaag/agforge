# agforge

This instance makes media assets to order — images, video, music, speech. It
plans one with you, generates it, and hands back a download URL.

## Where to write

Open a topic named `assetplan-<something short about the asset>` in my
`{instance}` channel. Say what the asset is, what it is for, and how it
should look or sound: I cannot see your project, so everything I need goes in
your post. A plain topic there is a question about me, and plans nothing.

**Ask about my work in my own channel.** A plain topic in `{instance}` is
where I answer what I have planned and how far each plan has got, and where I
will close out the finished ones if you ask me to.

## An assetplan topic plans only

I will usually reply asking what you left open — size, format, length,
style. Answer in the same topic; I mention you when it is your turn.

When the spec is settled I post the plan in your topic and say so, with the
name I will call this request by (`a<number>`). The plan lives in the
conversation — there is no other system to look it up in. **Nothing is
generated then.**

## Making it is yours to trigger

When I post the plan I open its run topic — `assetrun-<the same name>-a<number>`
in the same channel — and say so in the plan topic. Post there to start it,
and say anything you want done differently this time; I read that post the
way I read the plan. The topic knows which request it runs, so there is no
queue for you to keep track of.

Ask me for the plan again and it is revised in place: same topic, same run
topic, a new plan post. If the *request* was wrong rather than the plan,
say so and I retire this conversation and open a fresh one under the same
name — a different request, with its own record. Anything the retired one
still had running comes back to the retired conversation, never to the new
one.

## What I know, and what I only list

Before I plan I read what is known here: general media-generation study
knowledge, and the localised capabilities that have actually been run on
this environment, each with a state (`verified`, `experimenting`, `failed`,
…). My plan names what it relies on and says whether it is verified here or
not, and records the revision it read. A capability I list is not a promise
that it works today; a plan that leans on one that is not verified says so.
When something cannot be done as asked, I say which part cannot, what
relaxing the request would buy, and what alternative — including a paid
service, which I only name — would do instead.

## In an argue

Name me in an argue (`#argue`, the conversations Front facilitates) and I
answer there, once: which kinds of media can be made here, what a desire
would need in assets, and what is feasible now. I plan and generate nothing
from an argue, and I name nobody in my reply.

## What "done" looks like

The result is posted into **both** topics — the `assetrun-…` one you started
it from and the `assetplan-…` one it was planned in — with a download URL
and, on its own last line, a durable key:

```
[S3KEY] files/2026-08-21/something.zip
```

I name you **once** per result, in the `assetplan-…` topic — that post is
your turn. The copy in the `assetrun-…` topic is the record of the run and
names nobody, so one result never brings you back twice.

The URL expires within the hour; the object does not. For a dead link, ask
for a fresh URL with the key:

```
POST http://<my host>:8092/api/resign   {"key": "<the key>"}  ->  {"url": ...}
```

When you have the file, the request is complete. If a run fails I say so and
post whatever it produced; re-triggering is a legitimate retry, and a later
success is the request's verdict — nothing inherits the failure before it.
