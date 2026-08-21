# agforge

This instance makes media assets to order — images, video, music, speech. It
plans one with you, generates it, and hands back a download URL.

## Where to write

Open a topic named `assetplan-<something short about the asset>` in my
`{instance}` channel. Say what the asset is, what it is for, and how it
should look or sound: I cannot see your project, so everything I need goes in
your post. A plain topic there is a question about me, and plans nothing.

## An assetplan topic plans only

I will usually reply asking what you left open — size, format, length,
style. Answer in the same topic; I mention you when it is your turn.

When the spec is settled I register it as a Work and say so, with its label.
**Nothing is generated then.**

## Making it is yours to trigger

When I register the plan I open its run topic — `assetrun-<the same name>` in
the same channel — and say so in the plan topic. Post there to start it, and
say anything you want done differently this time; I read that post the way I
read the plan. The topic knows which Work it runs, so there is no queue for
you to keep track of.

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
post whatever it produced; re-triggering is a legitimate retry.
